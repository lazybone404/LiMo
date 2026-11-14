"""DeepSeek Files API 直传（`services/deepseek_files.py` + `doc_understand` 的分叉）。

判据按「失效形态会不会被别的东西掩盖」来选：

1. **同一个文件的第二次直传不得再上传**（缓存命中）—— 否则每轮生成都付一次
   百秒级上传，而功能看起来完全正常。
2. **multipart 里 `purpose` 必须在 `file` 之前**（官方要求，反了 400）。
3. **`file_id` 消息块是扁平形状**，不是 OpenAI 的嵌套形状。
4. **上传失败一律回到文本模式**（返回 None + warning），不是抛到生成链路里。
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import requests

import app.services.deepseek_files as ds_files
from app.core.deepseek_api import FILES_URL
from app.core.errors import AppError
from app.llm.service import build_document_block
from app.services.doc_understand import build_pdf_document


class _Resp:
    def __init__(self, status_code: int = 200, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


@pytest.fixture()
def pdf(tmp_path):
    p = tmp_path / "论文.pdf"
    p.write_bytes(b"%PDF-1.4\n" + b"x" * 1024 + b"\n%%EOF")
    return str(p)


# ── 缓存 ──

def test_second_call_reuses_file_id(pdf, tmp_path, monkeypatch):
    cache_dir = str(tmp_path / "cache")
    calls = {"n": 0}

    def fake_upload(path, api_key, purpose=ds_files.DOC_PURPOSE):
        calls["n"] += 1
        return {"id": "file-abc"}

    monkeypatch.setattr(ds_files, "upload_file", fake_upload)

    first = ds_files.ensure_file_id(pdf, "sk", cache_dir)
    second = ds_files.ensure_file_id(pdf, "sk", cache_dir)

    assert first == second == "file-abc"
    assert calls["n"] == 1, "第二次必须命中缓存，不能重新上传"


def test_different_content_uploads_again(pdf, tmp_path, monkeypatch):
    """缓存键是内容指纹 —— 文件名相同但内容变了必须重传，否则模型看到旧文件。"""
    cache_dir = str(tmp_path / "cache")
    calls = {"n": 0}

    def fake_upload(path, api_key, purpose=ds_files.DOC_PURPOSE):
        calls["n"] += 1
        return {"id": f"file-{calls['n']}"}

    monkeypatch.setattr(ds_files, "upload_file", fake_upload)

    assert ds_files.ensure_file_id(pdf, "sk", cache_dir) == "file-1"
    with open(pdf, "ab") as fh:
        fh.write(b"more")
    assert ds_files.ensure_file_id(pdf, "sk", cache_dir) == "file-2"
    assert calls["n"] == 2


def test_expired_cache_entry_is_a_miss(pdf, tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    digest = ds_files._file_sha256(__import__("pathlib").Path(pdf))
    (cache_dir / ds_files.CACHE_FILENAME).write_text(json.dumps({
        digest: {
            "file_id": "file-old",
            "created_at": time.time() - ds_files.CACHE_TTL_SECONDS - 10,
        }
    }), encoding="utf-8")

    monkeypatch.setattr(ds_files, "upload_file",
                        lambda *a, **kw: {"id": "file-new"})

    assert ds_files.ensure_file_id(pdf, "sk", str(cache_dir)) == "file-new"


def test_no_cache_dir_always_uploads(pdf, monkeypatch):
    calls = {"n": 0}

    def fake_upload(path, api_key, purpose=ds_files.DOC_PURPOSE):
        calls["n"] += 1
        return {"id": f"file-{calls['n']}"}

    monkeypatch.setattr(ds_files, "upload_file", fake_upload)
    ds_files.ensure_file_id(pdf, "sk", "")
    ds_files.ensure_file_id(pdf, "sk", "")
    assert calls["n"] == 2


def test_forget_file_id_drops_the_entry(pdf, tmp_path, monkeypatch):
    cache_dir = str(tmp_path / "cache")
    monkeypatch.setattr(ds_files, "upload_file", lambda *a, **kw: {"id": "file-a"})
    ds_files.ensure_file_id(pdf, "sk", cache_dir)

    ds_files.forget_file_id(pdf, cache_dir)

    assert ds_files.cached_file_id(pdf, cache_dir) == ""


# ── multipart 形状 ──

def test_upload_puts_purpose_before_file(pdf, monkeypatch):
    captured: dict = {}

    def fake_post(url, **kwargs):
        kwargs.pop("timeout", None)
        prepared = requests.Request("POST", url, **kwargs).prepare()
        captured["url"] = url
        captured["body"] = prepared.body
        return _Resp(payload={"id": "file-x"})

    monkeypatch.setattr("app.services.deepseek_files.requests.post", fake_post)

    body = ds_files.upload_file(pdf, "sk")
    assert body["id"] == "file-x"
    assert captured["url"] == FILES_URL
    raw = captured["body"]
    assert raw.index(b'name="purpose"') < raw.index(b'name="file"')
    assert b"rich_layout_recognition" in raw


# ── doc_understand 的分叉 ──

def test_deepseek_provider_returns_file_id_block(pdf, tmp_path, monkeypatch):
    monkeypatch.setattr(ds_files, "upload_file", lambda *a, **kw: {"id": "file-1"})
    monkeypatch.setattr(ds_files, "ensure_file_id",
                        lambda *a, **kw: "file-1")

    doc = build_pdf_document(pdf, provider="deepseek", api_key="sk",
                             cache_dir=str(tmp_path / "cache"))
    assert doc == {"filename": "论文.pdf", "file_id": "file-1"}
    assert "data_uri" not in doc, "Files 路径不应再把整份文件编码进消息"


def test_deepseek_upload_failure_falls_back_to_text(pdf, tmp_path, monkeypatch):
    def boom(*a, **kw):
        raise AppError("DEEPSEEK_HTTP_429", "DeepSeek 上传文档失败：请求过于频繁")

    monkeypatch.setattr(ds_files, "ensure_file_id", boom)

    assert build_pdf_document(pdf, provider="deepseek", api_key="sk",
                              cache_dir=str(tmp_path / "cache")) is None


def test_deepseek_without_key_never_uploads(pdf, tmp_path, monkeypatch):
    monkeypatch.setattr(ds_files, "ensure_file_id",
                        lambda *a, **kw: pytest.fail("没有 Key 时不该上传"))
    assert build_pdf_document(pdf, provider="deepseek", api_key="") is None


def test_deepseek_capacity_is_100mib_not_14mib(pdf, tmp_path, monkeypatch):
    """DeepSeek 路径的闸门是官方 100MiB，不受 data-uri 那条 14MiB 约束。"""
    assert ds_files.MAX_UPLOAD_BYTES == 100 * 1024 * 1024
    # 20MiB：data-uri 路径会拒，Files 路径应放行
    big = tmp_path / "big.pdf"
    big.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr("os.path.getsize", lambda p: 20 * 1024 * 1024)
    monkeypatch.setattr(ds_files, "ensure_file_id", lambda *a, **kw: "file-big")

    doc = build_pdf_document(str(big), provider="deepseek", api_key="sk")
    assert doc and doc.get("file_id") == "file-big"


def test_deepseek_over_100mib_falls_back(pdf, monkeypatch):
    monkeypatch.setattr("os.path.getsize", lambda p: ds_files.MAX_UPLOAD_BYTES + 1)
    monkeypatch.setattr(ds_files, "ensure_file_id",
                        lambda *a, **kw: pytest.fail("超限文件不应上传"))
    assert build_pdf_document(pdf, provider="deepseek", api_key="sk") is None


def test_other_providers_keep_data_uri(pdf):
    doc = build_pdf_document(pdf, provider="dashscope", api_key="sk")
    assert doc and doc["data_uri"].startswith("data:application/pdf;base64,")


# ── 消息块形状 ──

def test_file_id_block_is_flat():
    block = build_document_block({"filename": "a.pdf", "file_id": "file-9"})
    assert block == {"type": "file", "file_id": "file-9"}
    assert "file" not in block, "DeepSeek 用扁平形状，不是 OpenAI 的嵌套形状"


def test_data_uri_block_keeps_nested_shape():
    block = build_document_block({"filename": "a.pdf", "data_uri": "data:application/pdf;base64,AA=="})
    assert block == {
        "type": "file",
        "file": {"filename": "a.pdf", "file_data": "data:application/pdf;base64,AA=="},
    }
