"""ASR 引擎 — DeepSeek 语音转写（`POST /v1/tools/asr`）。

接口本身很窄：一次收一段音频，返回整段文本。官方限制是**单请求 ≤120 秒、
≤8MiB、且只认 16-bit PCM WAV 或 Ogg Opus**。适配器交给这里的是 16k 单声道 MP3
（`adapters/video.py::_extract_audio`），格式和时长都不满足 —— 所以切段与转码
放在引擎内部，对外仍然是「一个音频文件进、一份 TranscriptResult 出」。

⚠️ 110 秒不是 120 秒：16k 单声道 WAV 每秒 32KB，110 秒约 3.5MB，离 8MiB 与
120 秒两条线都留了余量。按 120 硬切，ffmpeg 的段边界与接口计时谁先撞线说不准。

⚠️ 失败**不跳段**。跳过某一段会产出一份中间缺了一截的转录，而界面上完全看不出来
（`segments` 有内容、时间轴仍然连续）。宁可整次失败并带原因。
"""

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import requests

from app.core.deepseek_api import TOOL_ASR_URL, check_response
from app.core.errors import AppError, FatalError
from app.core.protocols import Engine
from app.engines.transcript import TranscriptResult, TranscriptSegment

logger = logging.getLogger(__name__)

#: 官方上限
MAX_ASR_BYTES = 8 * 1024 * 1024

#: 实际切段长度（见模块 docstring 里为什么不是 120）
SEGMENT_SECONDS = 110

#: 单个分段的上传 + 处理窗口：官方要求 10 分钟内完成
ASR_TIMEOUT = (30, 300)

#: ffmpeg 转码整段素材的上限
FFMPEG_TIMEOUT = 300

#: 16k 单声道 PCM 每秒字节数，用于响应缺少 duration_ms 时估算段长
_PCM_BYTES_PER_SECOND = 16000 * 2


class DeepSeekASREngine(Engine):
    """DeepSeek 语音转写 — 自动转码为 16-bit PCM WAV 并按 110 秒切段。"""

    name = "deepseek-asr"
    capabilities = ["asr"]

    def __init__(self, api_key: str = "", base_url: str = ""):
        #: 调用时由 `engines/credentials.py::apply_provider_credentials` 注入
        self.api_key = api_key
        self.base_url = base_url

    def execute(self, audio_path: str, cancel_event=None, **kwargs) -> TranscriptResult:
        path = Path(audio_path)
        if not path.exists():
            raise AppError("DEEPSEEK_ASR_FILE_MISSING", f"音频文件不存在: {path.name}",
                           recoverable=False)
        if not self.api_key:
            raise AppError("DEEPSEEK_NO_API_KEY",
                           "DeepSeek API Key 未配置，请在设置 → 引擎厂商 中填写",
                           recoverable=False,
                           hint="设置 → 引擎厂商 → DeepSeek")

        workdir = Path(tempfile.mkdtemp(prefix="limo_ds_asr_"))
        try:
            chunks = self._split_wav(path, workdir)
            logger.info("DeepSeek ASR: %s → %d 段 (每段 %ds)",
                        path.name, len(chunks), SEGMENT_SECONDS)
            return self._transcribe_chunks(chunks, cancel_event)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    # ── 切段 ──

    def _split_wav(self, source: Path, workdir: Path) -> list[Path]:
        """用 ffmpeg 一次性转码 + 切段，输出 16k 单声道 16-bit PCM WAV。

        单条命令做两件事（转码与切段）比「先转成整段 WAV 再切」少写一次中间文件，
        也避免长素材在磁盘上多留一份未压缩副本。
        """
        pattern = workdir / "chunk_%03d.wav"
        cmd = [
            "ffmpeg", "-y", "-i", str(source),
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
            "-f", "segment", "-segment_time", str(SEGMENT_SECONDS),
            str(pattern),
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, timeout=FFMPEG_TIMEOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except FileNotFoundError as e:
            raise AppError("DEEPSEEK_ASR_NO_FFMPEG",
                           "音频转码失败：找不到 ffmpeg", detail=str(e),
                           recoverable=False) from e
        except subprocess.TimeoutExpired as e:
            raise AppError("DEEPSEEK_ASR_FFMPEG_TIMEOUT",
                           f"音频转码超时（{FFMPEG_TIMEOUT} 秒）", detail=str(e)) from e

        if proc.returncode != 0:
            raise AppError(
                "DEEPSEEK_ASR_FFMPEG_FAILED", "音频转码失败",
                detail=(proc.stderr or b"").decode("utf-8", "replace")[-300:],
                recoverable=False)

        chunks = sorted(workdir.glob("chunk_*.wav"))
        if not chunks:
            raise AppError("DEEPSEEK_ASR_EMPTY_AUDIO",
                           "音频转码后没有得到任何分段，文件可能不含音轨",
                           recoverable=False)
        return chunks

    # ── 逐段转写 ──

    def _transcribe_chunks(self, chunks: list[Path], cancel_event) -> TranscriptResult:
        segments: list[TranscriptSegment] = []
        offset = 0.0

        for i, chunk in enumerate(chunks):
            if cancel_event is not None and cancel_event.is_set():
                raise FatalError("操作已取消")

            size = chunk.stat().st_size
            if size > MAX_ASR_BYTES:
                raise AppError(
                    "DEEPSEEK_ASR_SEGMENT_TOO_LARGE",
                    f"第 {i + 1} 段 {size / 1024 / 1024:.1f}MB 超过单请求上限"
                    f"（{MAX_ASR_BYTES // 1024 // 1024}MB）",
                    recoverable=False)

            body = self._post_chunk(chunk, i, len(chunks))
            text = (body.get("text") or "").strip()
            seconds = self._chunk_seconds(body, size)

            if text:
                segments.append(TranscriptSegment(
                    start=round(offset, 2),
                    end=round(offset + seconds, 2),
                    text=text,
                ))
            offset += seconds

        full_text = "\n".join(s.text for s in segments)
        logger.info("DeepSeek ASR 完成: %d 段 / 共 %.1f 秒", len(segments), offset)
        return TranscriptResult(
            # 官方不提供语言参数、响应里也不返回语言。写 "auto" 比猜一个 "zh" 诚实。
            language="auto",
            duration=round(offset, 2),
            segments=segments,
            full_text=full_text,
        )

    def _post_chunk(self, chunk: Path, index: int, total: int) -> dict:
        logger.info("DeepSeek ASR 上传第 %d/%d 段...", index + 1, total)
        with chunk.open("rb") as fh:
            resp = requests.post(
                TOOL_ASR_URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": (chunk.name, fh)},
                timeout=ASR_TIMEOUT,
            )
        return check_response(resp, "语音转写")

    @staticmethod
    def _chunk_seconds(body: dict, size: int) -> float:
        """该段时长 —— 优先用响应里的 `duration_ms`，缺了就按 WAV 体积估算。

        估算而不是「一律取 110 秒」：最后一段通常不满 110 秒，按 110 累加会让
        时间轴整体后移，越靠后偏得越多。
        """
        ms = body.get("duration_ms")
        if isinstance(ms, (int, float)) and ms > 0:
            return ms / 1000.0
        return max(size - 44, 0) / _PCM_BYTES_PER_SECOND
