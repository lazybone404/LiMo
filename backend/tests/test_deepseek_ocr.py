"""DeepSeek OCR 引擎（`POST /v1/tools/ocr`）。

三件事必须钉住，它们的失效形态都不报错：

1. **multipart 字段顺序**：官方要求 `purpose` 排在 `file` 之前，顺序反了返回 400。
   这条依赖 `requests` 的编码实现（先写 `data` 再写 `files`），升级库后顺序变了
   就是全量 400，而我们的代码一个字都没改。
2. **按 `index` 排序而不是数组下标**：接口不保证顺序，拼错时两页内容互换，两边都「成功」。
3. **版面标记必须整体摘掉**：只删标记会把 `<｜box｜>[[x1,y1,x2,y2]]<｜/box｜>` 里的
   坐标数字留在正文，而正文会进生成稿、导出与全文索引。
"""

import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import requests

from app.core.deepseek_api import TOOL_OCR_URL
from app.core.errors import AppError
from app.engines.ocr_deepseek import (
    MAX_OCR_BYTES,
    DeepSeekOCREngine,
    _pages_to_text,
    strip_layout_tokens,
)


class _Resp:
    def __init__(self, status_code: int = 200, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


# ── 版面标记 ──

def test_strip_layout_tokens_removes_box_coords():
    raw = "第一章<｜ref｜>标题<｜/ref｜><｜box｜>[[10,20,300,40]]<｜/box｜>正文"
    assert strip_layout_tokens(raw) == "第一章标题正文"


def test_strip_layout_tokens_multiple_boxes():
    raw = ("<｜ref｜>A<｜/ref｜><｜box｜>[[1,2,3,4]]<｜/box｜>\n"
           "<｜ref｜>B<｜/ref｜><｜box｜>[[5,6,7,8]]<｜/box｜>")
    out = strip_layout_tokens(raw)
    assert out == "A\nB"
    assert "[[" not in out and "<｜" not in out


def test_strip_layout_tokens_handles_dangling_marker():
    """截断时会出现半截标记（只有开、没有闭），兜底规则必须能删掉它。"""
    assert strip_layout_tokens("正文<｜box｜>[[1,2") == "正文[[1,2"


def test_strip_layout_tokens_empty_inputs():
    assert strip_layout_tokens("") == ""
    assert strip_layout_tokens("   \n\n  ") == ""
    assert strip_layout_tokens("<｜box｜>[[1,2,3,4]]<｜/box｜>") == ""


# ── 分页拼接 ──

def test_pages_sorted_by_index_not_array_order():
    body = {"pages": [
        {"index": 1, "text": "第二页"},
        {"index": 0, "text": "第一页"},
    ]}
    assert _pages_to_text(body) == "第一页\n\n第二页"


def test_pages_without_index_use_array_position():
    body = {"pages": [{"text": "甲"}, {"text": "乙"}]}
    assert _pages_to_text(body) == "甲\n\n乙"


def test_truncated_page_logs_warning_without_placeholder(caplog):
    """`finish_reason == length` 是部分成功：记 warning，但不往正文塞占位文本。"""
    body = {"pages": [
        {"index": 0, "text": "完整", "finish_reason": "stop"},
        {"index": 1, "text": "被截断的一段", "finish_reason": "length"},
    ]}
    with caplog.at_level(logging.WARNING, logger="app.engines.ocr_deepseek"):
        text = _pages_to_text(body)
    assert text == "完整\n\n被截断的一段"
    assert "截断" in caplog.text and "1" in caplog.text


def test_empty_pages_give_empty_text():
    assert _pages_to_text({}) == ""
    assert _pages_to_text({"pages": []}) == ""


# ── 请求形状 ──

def test_multipart_puts_purpose_before_file(tmp_path, monkeypatch):
    """官方要求 `purpose` 在 `file` 之前。这里用 requests 自己的编码器验证真实字节流。"""
    f = tmp_path / "page.png"
    f.write_bytes(b"\x89PNG" + b"0" * 64)

    captured: dict[str, bytes] = {}

    def fake_post(url, **kwargs):
        kwargs.pop("timeout", None)  # Request() 不认这个参数
        prepared = requests.Request("POST", url, **kwargs).prepare()
        captured["url"] = url
        captured["body"] = prepared.body
        return _Resp(payload={"pages": [{"index": 0, "text": "hello"}]})

    monkeypatch.setattr("app.engines.ocr_deepseek.requests.post", fake_post)

    result = DeepSeekOCREngine(api_key="sk-test").run(str(f))

    assert captured["url"] == TOOL_OCR_URL
    body = captured["body"]
    assert isinstance(body, bytes)
    assert body.index(b'name="purpose"') < body.index(b'name="file"')
    assert b"text_recognition" in body
    assert result.text == "hello"
    assert result.engine == "deepseek-ocr"


def test_response_is_joined_per_page(tmp_path, monkeypatch):
    f = tmp_path / "page.png"
    f.write_bytes(b"x" * 16)

    monkeypatch.setattr(
        "app.engines.ocr_deepseek.requests.post",
        lambda url, **kw: _Resp(payload={"pages": [
            {"index": 1, "text": "b"},
            {"index": 0, "text": "a"},
        ]}),
    )
    assert DeepSeekOCREngine(api_key="sk").run(str(f)).text == "a\n\nb"


# ── 失败显式化 ──

def test_missing_key_raises_before_any_request(tmp_path, monkeypatch):
    f = tmp_path / "page.png"
    f.write_bytes(b"x")

    def boom(*a, **kw):
        raise AssertionError("没有 Key 时不应该发起请求")

    monkeypatch.setattr("app.engines.ocr_deepseek.requests.post", boom)

    with pytest.raises(AppError) as ei:
        DeepSeekOCREngine(api_key="").run(str(f))
    assert ei.value.code == "DEEPSEEK_NO_API_KEY"


def test_oversize_file_rejected(tmp_path, monkeypatch):
    f = tmp_path / "big.pdf"
    f.write_bytes(b"x")

    monkeypatch.setattr(
        "app.engines.ocr_deepseek.requests.post",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("超限文件不应上传")),
    )
    monkeypatch.setattr("pathlib.Path.stat",
                        lambda self, **kw: os.stat_result((0, 0, 0, 0, 0, 0, MAX_OCR_BYTES + 1, 0, 0, 0)))

    with pytest.raises(AppError) as ei:
        DeepSeekOCREngine(api_key="sk").run(str(f))
    assert ei.value.code == "DEEPSEEK_OCR_TOO_LARGE"


def test_empty_result_raises(tmp_path, monkeypatch):
    f = tmp_path / "page.png"
    f.write_bytes(b"x")
    monkeypatch.setattr(
        "app.engines.ocr_deepseek.requests.post",
        lambda url, **kw: _Resp(payload={"pages": [{"index": 0, "text": ""}]}),
    )
    with pytest.raises(AppError) as ei:
        DeepSeekOCREngine(api_key="sk").run(str(f))
    assert ei.value.code == "DEEPSEEK_OCR_EMPTY"


def test_mode_is_not_forwarded_and_not_advertised():
    """接口没有「模式」这一维：`understand` 不在 supported_modes 里（列上就是做不到的入口）。"""
    eng = DeepSeekOCREngine()
    assert "understand" not in eng.supported_modes
    assert set(eng.supported_modes) == {"extract", "table", "digitize"}
