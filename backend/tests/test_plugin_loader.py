"""插件加载器单测 — 门控 / 分派 / stdout 回退（纯函数，无 IO）"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.engines.plugin_loader import (
    _parse_stdout,
    _dispatch_result,
    _resolve_exec_mode,
    EnginePlugin,
    PLUGIN_API_VERSION,
)


class _DocPlugin(EnginePlugin):
    name = "test-doc"
    category = "doc"
    command = ["python", "engine.py"]


class _OCRPlugin(EnginePlugin):
    name = "test-ocr"
    category = "ocr"
    command = ["python", "engine.py"]


class _ASRPlugin(EnginePlugin):
    name = "test-asr"
    category = "asr"
    command = ["python", "engine.py"]


class _TrustedPlugin(EnginePlugin):
    name = "test-trusted"
    category = "doc"
    trusted = True


class _NoModePlugin(EnginePlugin):
    name = "test-nomode"
    category = "doc"


# ── 门控 ──

def test_resolve_exec_mode_external():
    assert _resolve_exec_mode(_DocPlugin()) == "external"


def test_resolve_exec_mode_trusted():
    assert _resolve_exec_mode(_TrustedPlugin()) == "inproc"


def test_resolve_exec_mode_rejected():
    assert _resolve_exec_mode(_NoModePlugin()) is None


# ── stdout 解析（JSON 优先 + v0 回退）──

def test_parse_stdout_json():
    assert _parse_stdout('{"text": "hi", "pages": 3}') == {"text": "hi", "pages": 3}


def test_parse_stdout_v0_plain():
    p = _parse_stdout("plain markdown output")
    assert p["text"] == "plain markdown output"
    assert p["_v0"] is True


def test_parse_stdout_v0_broken_json():
    p = _parse_stdout('{"text": broken')
    assert p["text"] == '{"text": broken'
    assert p["_v0"] is True


def test_parse_stdout_whitespace():
    p = _parse_stdout("  \n some md \n")
    assert p["text"] == "some md"


# ── 分派 ──

def test_dispatch_doc():
    from app.engines.doc_types import DocResult
    r = _dispatch_result("doc", {"text": "md", "pages": 5}, "p")
    assert isinstance(r, DocResult)
    assert r.text == "md" and r.pages == 5
    assert r.is_scanned is False


def test_dispatch_doc_defaults():
    from app.engines.doc_types import DocResult
    r = _dispatch_result("doc", {"text": "md"}, "p")
    assert isinstance(r, DocResult)
    assert r.pages == 1
    assert r.is_scanned is False and r.scanned_pages == 0


def test_dispatch_ocr():
    from app.engines.ocr import OCRResult
    r = _dispatch_result("ocr", {"text": "t", "confidence": 0.9}, "p")
    assert isinstance(r, OCRResult)
    assert r.text == "t" and r.confidence == 0.9


def test_dispatch_asr_segments():
    from app.engines.transcript import TranscriptResult
    r = _dispatch_result("asr", {
        "segments": [{"start": 0, "end": 1.5, "text": "a"}],
        "language": "zh",
    }, "p")
    assert isinstance(r, TranscriptResult)
    assert r.segments[0].text == "a"
    assert r.segments[0].end == 1.5
    assert r.language == "zh"
    assert r.full_text == "a"


def test_dispatch_asr_v0_plain():
    from app.engines.transcript import TranscriptResult
    r = _dispatch_result("asr", {"text": "single line"}, "p")
    assert isinstance(r, TranscriptResult)
    assert len(r.segments) == 1
    assert r.segments[0].text == "single line"


def test_dispatch_unknown_category_doc_contract():
    from app.engines.doc_types import DocResult
    r = _dispatch_result("future-category", {"text": "x"}, "p")
    assert isinstance(r, DocResult)


# ── 契约版本 ──

def test_api_version():
    assert PLUGIN_API_VERSION == 1


if __name__ == "__main__":
    import traceback
    failed = 0
    for fn_name, fn in sorted(globals().items()):
        if fn_name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {fn_name}")
            except Exception as e:
                failed += 1
                print(f"FAIL {fn_name}: {e}")
                traceback.print_exc()
    print(f"\n{len([k for k in globals() if k.startswith('test_')]) - failed}/{len([k for k in globals() if k.startswith('test_')])} passed")
    sys.exit(1 if failed else 0)
