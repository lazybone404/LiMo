"""官方插件模板的**契约守卫** —— 让 `engine.py` 真跑一遍，而不是只断文本在场。

运行：cd backend && pytest tests/test_plugin_examples.py

## 为什么要有这一层

模板是用户照抄的起点，也是「我们承诺的输出形状」的样本。它坏的形态几乎都是
**静默**的：

- key 名写错（`segments` → `items`）→ 主进程 `_dispatch_result` 拿到空列表 →
  转写「成功」但时间轴全空；
- 取消时把已解码的部分打出去 → 主进程当成功落库 → 用户以为转完了；
- 用 `sys.exit(0)` 报失败 → `_run_external` 认为成功 → 空结果被当成「这段音频
  没人说话」。

纯静态断言（「源码里有 `json.dumps`」）对以上四条**全部看不出来**。所以这里用
一个**假的 `faster_whisper` 包**把它真跑起来：不需要网络、不需要 250MB 模型，
拿到的是真实的 stdin→stdout 往返。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "plugins" / "examples"
FW_ENGINE = EXAMPLES / "faster_whisper" / "engine.py"


def _make_fake_faster_whisper(root: Path, *, fail: str = "") -> Path:
    """造一个只够 `engine.py` 用的假包。

    `fail` 取 `"load"` / `"transcribe"` 时分别让对应阶段抛异常 —— 用来断言
    「失败是显式的（非零退出 + 人话原因）」，而不是静默产出空结果。
    """
    pkg = root / "faster_whisper"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(
        "class _Seg:\n"
        "    def __init__(self, start, end, text):\n"
        "        self.start, self.end, self.text = start, end, text\n"
        "\n"
        "class _Info:\n"
        "    language = 'zh'\n"
        "    duration = 2.0\n"
        "\n"
        "class WhisperModel:\n"
        "    def __init__(self, name, device, compute_type, download_root=None):\n"
        "        " + ("raise RuntimeError('模型下载失败')" if fail == "load" else "pass") + "\n"
        "        self._fail = " + repr(fail == "transcribe") + "\n"
        "    def transcribe(self, path, language=None, vad_filter=True, beam_size=5):\n"
        "        if self._fail:\n"
        "            raise RuntimeError('解码炸了')\n"
        "        return iter([\n"
        "            _Seg(0.0, 1.2, '  你好 '),\n"
        "            _Seg(1.2, 2.0, '世界'),\n"
        "            _Seg(2.0, 2.0, '   '),\n"      # 空白段必须被丢掉
        "        ]), _Info()\n",
        encoding="utf-8",
    )
    return root


def _run(engine: Path, payload: dict, fake_root: Path, *, argv: list[str] | None = None):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(fake_root) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, str(engine)] + (argv or []),
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=60,
    )


def test_faster_whisper_输出符合_asr_契约(tmp_path):
    """stdout 必须是 `{segments:[{start,end,text}], language, duration}`。

    ⚠️ 三个 key 名分别被 `_dispatch_result` / `TranscriptResult` / 视频时间轴消费，
    任何一个写错都**不会报错**，只会让那一列变成默认值。
    """
    fake = _make_fake_faster_whisper(tmp_path / "fake")
    proc = _run(FW_ENGINE, {"file": "a.mp3", "config": {"model": "small"}}, fake)
    assert proc.returncode == 0, proc.stderr

    data = json.loads(proc.stdout)
    assert set(data) == {"segments", "language", "duration"}
    assert data["language"] == "zh"
    assert data["duration"] == 2.0

    assert data["segments"] == [
        {"start": 0.0, "end": 1.2, "text": "你好"},    # 首尾空白被 strip
        {"start": 1.2, "end": 2.0, "text": "世界"},
    ], "空白段必须丢弃 —— 否则时间轴上会冒出空条目"


def test_faster_whisper_取消时零输出且零退出(tmp_path):
    """取消 = 什么都不给。

    ⚠️ 反向断言（`stdout == ""`）是本条的意义：把已解码的部分打出去，主进程会
    当成**成功**并落库 —— 用户以为转完了，实际只转了三分钟。
    """
    fake = _make_fake_faster_whisper(tmp_path / "fake")
    cancel = tmp_path / "cancel.flag"
    cancel.write_text("cancel", encoding="utf-8")

    proc = _run(FW_ENGINE, {"file": "a.mp3", "cancel_file": str(cancel)}, fake)
    assert proc.returncode == 0
    assert proc.stdout.strip() == "", "取消不能交出半截结果"


def test_faster_whisper_加载失败是显式错误(tmp_path):
    """device / compute_type 非法、缺 CUDA 库、模型下载失败 —— 一律非零退出 + 原因。

    ⚠️ 不能静默退回 CPU：用户会在毫不知情的情况下等一个慢十倍的结果，还以为
    GPU 在跑。
    """
    fake = _make_fake_faster_whisper(tmp_path / "fake", fail="load")
    proc = _run(FW_ENGINE, {"file": "a.mp3", "config": {"device": "cuda"}}, fake)
    assert proc.returncode == 2
    assert proc.stdout.strip() == ""
    assert "加载模型失败" in proc.stderr
    assert "cuBLAS" in proc.stderr, "cuda 失败要给出下一步动作"


def test_faster_whisper_转写失败是显式错误(tmp_path):
    fake = _make_fake_faster_whisper(tmp_path / "fake", fail="transcribe")
    proc = _run(FW_ENGINE, {"file": "a.mp3", "config": {}}, fake)
    assert proc.returncode == 2
    assert "转写失败" in proc.stderr


def test_faster_whisper_缺依赖时说人话(tmp_path):
    """子 venv 没装依赖时，engine.py 自己也要给出可照着做的提示。

    （正常情况下用户根本走不到这里 —— precheck 会先在设置页拦住。）
    """
    proc = _run(FW_ENGINE, {"file": "a.mp3", "config": {}}, tmp_path / "empty")
    assert proc.returncode == 2
    assert "无法导入 faster_whisper" in proc.stderr
    assert "一键安装依赖" in proc.stderr


@pytest.mark.parametrize(
    "decl",
    ["pymupdf4llm_plugin.py", "tesseract_plugin.py", "faster_whisper_plugin.py",
     "local_embedding_plugin.py", "rerank_plugin.py"],
)
def test_模板声明文件可被加载器解析(decl):
    """AST 静态解析（`_peek_plugin_meta`）必须能从每个模板读出 name —— 它决定
    「禁用即不 exec 插件代码」这条安全性质能不能成立。
    """
    from app.engines.plugin_loader import _peek_plugin_meta

    meta = _peek_plugin_meta(EXAMPLES / decl)
    assert meta and meta.get("name"), f"{decl} 的 name 不是字符串字面量"
