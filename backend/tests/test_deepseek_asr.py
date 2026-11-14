"""DeepSeek ASR 引擎（`POST /v1/tools/asr`）。

接口只收 16-bit PCM WAV / Ogg Opus、单段 ≤120 秒、≤8MiB，而适配器交给引擎的是
16k 单声道 MP3。所以切段是**引擎自己的责任**，本文件守的就是这一层的三条判据：

1. 段长 110 秒（离 120 秒与 8MiB 两条线都留余量）；
2. 时间偏移按每段真实时长累加（一律按 110 秒加会让时间轴越靠后偏得越多）；
3. 任何一段失败都整体失败 —— 跳过一段会产出中间缺一截的转录，界面上看不出来。
"""

import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.core.errors import AppError, FatalError
from app.engines.asr_deepseek import (
    MAX_ASR_BYTES,
    SEGMENT_SECONDS,
    DeepSeekASREngine,
)


def _chunk(tmp_path, name: str, seconds: float):
    """造一个体积与时长一致的假 WAV（44 字节头 + 16k 单声道 PCM）。"""
    p = tmp_path / name
    p.write_bytes(b"RIFF" + b"\0" * 40 + b"\0" * int(16000 * 2 * seconds))
    return p


# ── 段长 ──

def test_segment_length_leaves_margin_on_both_limits():
    assert SEGMENT_SECONDS == 110, "改这个值要同时重算时间轴与体积余量"
    assert SEGMENT_SECONDS < 120
    assert SEGMENT_SECONDS * 16000 * 2 < MAX_ASR_BYTES


# ── 时间轴 ──

def test_offsets_accumulate_per_real_duration(tmp_path, monkeypatch):
    chunks = [_chunk(tmp_path, "chunk_000.wav", 10), _chunk(tmp_path, "chunk_001.wav", 7)]
    replies = {
        "chunk_000.wav": {"text": "第一段", "duration_ms": 10_000},
        "chunk_001.wav": {"text": "第二段", "duration_ms": 7_000},
    }
    eng = DeepSeekASREngine(api_key="sk")
    monkeypatch.setattr(eng, "_post_chunk", lambda c, i, n: replies[c.name])

    res = eng._transcribe_chunks(chunks, None)

    assert [(s.start, s.end) for s in res.segments] == [(0.0, 10.0), (10.0, 17.0)]
    assert res.duration == 17.0
    assert res.full_text == "第一段\n第二段"
    assert res.language == "auto", "官方不返回语言，写 auto 比猜 zh 诚实"


def test_missing_duration_estimates_from_wav_size(tmp_path, monkeypatch):
    """最后一段通常不满 110 秒：没有 `duration_ms` 时按体积估，不按 110 硬加。"""
    chunks = [_chunk(tmp_path, "chunk_000.wav", 3)]
    eng = DeepSeekASREngine(api_key="sk")
    monkeypatch.setattr(eng, "_post_chunk", lambda c, i, n: {"text": "短"})

    res = eng._transcribe_chunks(chunks, None)
    assert res.segments[0].end == pytest.approx(3.0, abs=0.01)


def test_empty_text_still_advances_offset(tmp_path, monkeypatch):
    """静音段没有文本，但时间必须往下走，否则后续段的时间轴会挤在一起。"""
    chunks = [_chunk(tmp_path, "chunk_000.wav", 5), _chunk(tmp_path, "chunk_001.wav", 5)]
    replies = {
        "chunk_000.wav": {"text": "", "duration_ms": 5_000},
        "chunk_001.wav": {"text": "有声音", "duration_ms": 5_000},
    }
    eng = DeepSeekASREngine(api_key="sk")
    monkeypatch.setattr(eng, "_post_chunk", lambda c, i, n: replies[c.name])

    res = eng._transcribe_chunks(chunks, None)
    assert len(res.segments) == 1
    assert res.segments[0].start == 5.0


# ── 失败语义 ──

def test_one_segment_failure_fails_whole_run(tmp_path, monkeypatch):
    chunks = [_chunk(tmp_path, "chunk_000.wav", 5), _chunk(tmp_path, "chunk_001.wav", 5)]
    calls = {"n": 0}

    def fake_post(chunk, index, total):
        calls["n"] += 1
        if index == 1:
            raise AppError("DEEPSEEK_HTTP_500", "DeepSeek 语音转写失败：服务端内部错误")
        return {"text": "第一段", "duration_ms": 5_000}

    eng = DeepSeekASREngine(api_key="sk")
    monkeypatch.setattr(eng, "_post_chunk", fake_post)

    with pytest.raises(AppError):
        eng._transcribe_chunks(chunks, None)
    assert calls["n"] == 2, "第二段必须被真的尝试过，而不是被静默跳过"


def test_cancel_between_segments(tmp_path, monkeypatch):
    """取消在段与段之间生效 —— 已经发出的请求等它回来，但不再发下一段。"""
    chunks = [_chunk(tmp_path, "chunk_000.wav", 5), _chunk(tmp_path, "chunk_001.wav", 5)]
    cancel = threading.Event()
    calls = {"n": 0}

    def fake_post(chunk, index, total):
        calls["n"] += 1
        cancel.set()
        return {"text": "第一段", "duration_ms": 5_000}

    eng = DeepSeekASREngine(api_key="sk")
    monkeypatch.setattr(eng, "_post_chunk", fake_post)

    with pytest.raises(FatalError):
        eng._transcribe_chunks(chunks, cancel)
    assert calls["n"] == 1


def test_oversize_segment_rejected(tmp_path, monkeypatch):
    big = tmp_path / "chunk_000.wav"
    big.write_bytes(b"\0" * (MAX_ASR_BYTES + 1))

    eng = DeepSeekASREngine(api_key="sk")
    monkeypatch.setattr(eng, "_post_chunk",
                        lambda *a, **kw: pytest.fail("超限分段不应上传"))

    with pytest.raises(AppError) as ei:
        eng._transcribe_chunks([big], None)
    assert ei.value.code == "DEEPSEEK_ASR_SEGMENT_TOO_LARGE"


def test_ffmpeg_failure_is_explicit(tmp_path, monkeypatch):
    """转码失败必须带 stderr —— 只说「转码失败」等于让用户无法自查。"""
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")

    class _Proc:
        returncode = 1
        stderr = "Invalid data found when processing input".encode()
        stdout = b""

    monkeypatch.setattr("app.engines.asr_deepseek.subprocess.run", lambda *a, **kw: _Proc())

    eng = DeepSeekASREngine(api_key="sk")
    with pytest.raises(AppError) as ei:
        eng._split_wav(src, tmp_path)
    assert ei.value.code == "DEEPSEEK_ASR_FFMPEG_FAILED"
    assert "Invalid data" in ei.value.detail


def test_missing_ffmpeg_is_explicit(tmp_path, monkeypatch):
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")

    def boom(*a, **kw):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr("app.engines.asr_deepseek.subprocess.run", boom)

    eng = DeepSeekASREngine(api_key="sk")
    with pytest.raises(AppError) as ei:
        eng._split_wav(src, tmp_path)
    assert ei.value.code == "DEEPSEEK_ASR_NO_FFMPEG"


def test_split_uses_single_ffmpeg_pass_with_segment(tmp_path, monkeypatch):
    """转码与切段是同一条命令：先转成整段 WAV 再切会在磁盘上多留一份未压缩副本。"""
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    seen: dict[str, list[str]] = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        # 模拟 ffmpeg 产出两段
        (tmp_path / "chunk_000.wav").write_bytes(b"0" * 44)
        (tmp_path / "chunk_001.wav").write_bytes(b"0" * 44)

        class _Proc:
            returncode = 0
            stderr = b""
            stdout = b""
        return _Proc()

    monkeypatch.setattr("app.engines.asr_deepseek.subprocess.run", fake_run)

    chunks = DeepSeekASREngine(api_key="sk")._split_wav(src, tmp_path)

    cmd = seen["cmd"]
    assert "-f" in cmd and "segment" in cmd
    assert cmd[cmd.index("-segment_time") + 1] == str(SEGMENT_SECONDS)
    assert cmd[cmd.index("-ar") + 1] == "16000"
    assert cmd[cmd.index("-ac") + 1] == "1"
    assert cmd[cmd.index("-c:a") + 1] == "pcm_s16le"
    assert [c.name for c in chunks] == ["chunk_000.wav", "chunk_001.wav"]


# ── 入口检查 ──

def test_missing_key_raises_before_transcoding(tmp_path, monkeypatch):
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    monkeypatch.setattr("app.engines.asr_deepseek.subprocess.run",
                        lambda *a, **kw: pytest.fail("没有 Key 时不该启动 ffmpeg"))

    with pytest.raises(AppError) as ei:
        DeepSeekASREngine(api_key="").execute(str(src))
    assert ei.value.code == "DEEPSEEK_NO_API_KEY"


def test_missing_file_raises(tmp_path):
    with pytest.raises(AppError) as ei:
        DeepSeekASREngine(api_key="sk").execute(str(tmp_path / "nope.mp3"))
    assert ei.value.code == "DEEPSEEK_ASR_FILE_MISSING"
