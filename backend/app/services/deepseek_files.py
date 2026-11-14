"""DeepSeek Files API — 上传 PDF 原件，拿 `file_id` 在生成请求里引用。

**解决的是体积问题，不是增强理解**。`doc_understand.py` 走 data-uri 的那条路
有 14MiB 硬闸门（base64 膨胀 4/3，撞服务端 20MiB 上限），超限只能退回文本模式。
Files API 先把文件上传换一个 `file_id`，消息里只带这个 id，于是闸门变成官方的
100MiB，且不再有 base64 那一份内存开销。

⚠️ **`purpose` 用 `rich_layout_recognition`**：文档直传的意义就是让模型看到原件，
纯 `text_recognition` 会丢掉图表与版式，与「只发原文件」这个承诺不符。代价是
图表按图片 token 计费 —— 这条写进用户可见文档，不只在注释里。

⚠️ **上传即解析**，响应要等解析完才回来（官方口径：文件 ≤100MiB）。所以上传
超时给 600 秒，与 `DOC_DIRECT_TIMEOUT`（首包 330 秒）分的不是同一段时间：
上传发生在生成准备阶段，不占生成流的 chunk 超时。

⚠️ **multipart 字段顺序 `purpose` 在前、`file` 在后**（官方明确要求，反了返回 400）。
`requests` 编码时先写 `data` 再写 `files`，所以这两个参数的分工不能调换；
`tests/test_deepseek_files.py` 用 requests 自己的编码器钉住真实字节流顺序。

⚠️ **缓存只是本地映射**，不是对方的真相源。缓存里过期或对不上的条目只从本地删，
**不调用 DELETE** —— 对方存储（25GiB / 10000 文件）不归我们管理，误删会让别的
客户端正在用的 `file_id` 失效。同理，缓存丢了最多重新上传一次，不会出错。
"""

import hashlib
import json
import logging
import threading
import time
from pathlib import Path

import requests

from app.core.atomic_io import atomic_write_text
from app.core.deepseek_api import FILES_URL, check_response

logger = logging.getLogger(__name__)

#: 官方单文件上限（上传路径比 data-uri 那条宽得多）
MAX_UPLOAD_BYTES = 100 * 1024 * 1024

#: 本地缓存的存活期。超过即重新上传 —— 对方不会通知「这个 file_id 没了」，
#: 只能靠本地 TTL 收敛这类不确定状态。
CACHE_TTL_SECONDS = 30 * 24 * 3600

#: 上传 + 解析同一个响应，给足窗口
UPLOAD_TIMEOUT = (30, 600)

#: 文档直传用的 purpose（见模块文档：要图表与版式，不只是文字）
DOC_PURPOSE = "rich_layout_recognition"

CACHE_FILENAME = "deepseek-files.json"

#: 保护缓存的读改写。同一份 PDF 并发生成时只上传一次（否则 N 路同时上传同一文件）。
_CACHE_LOCK = threading.Lock()


# ── 缓存 ──

def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _cache_file(cache_dir: str) -> Path:
    return Path(cache_dir) / CACHE_FILENAME


def _load_cache(cache_dir: str) -> dict:
    path = _cache_file(cache_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_cache(cache_dir: str, data: dict) -> None:
    """原子写入并顺手清掉过期条目（缓存只增不减会让文件越写越大）。"""
    cutoff = time.time() - CACHE_TTL_SECONDS
    pruned = {
        key: val for key, val in data.items()
        if isinstance(val, dict) and float(val.get("created_at") or 0) >= cutoff
    }
    path = _cache_file(cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        atomic_write_text(path, json.dumps(pruned, ensure_ascii=False, indent=2))
    except Exception as e:  # noqa: BLE001 —— 缓存写失败不该让生成本身失败
        logger.warning("DeepSeek Files 缓存写入失败: %s", str(e)[:120])


def cached_file_id(file_path: str, cache_dir: str) -> str:
    """命中缓存返回 `file_id`，否则空串。`cache_dir` 为空时直接返回空串（不缓存）。"""
    if not cache_dir:
        return ""
    try:
        digest = _file_sha256(Path(file_path))
    except OSError:
        return ""
    with _CACHE_LOCK:
        entry = _load_cache(cache_dir).get(digest)
    if not isinstance(entry, dict):
        return ""
    if time.time() - float(entry.get("created_at") or 0) > CACHE_TTL_SECONDS:
        return ""
    return str(entry.get("file_id") or "")


def forget_file_id(file_path: str, cache_dir: str) -> None:
    """把某个文件的缓存条目删掉（下次重新上传）。

    留给「服务端说这个 file_id 找不到」的调用方用 —— 本轮没有消费方
    （生成链路拿不到这种信号），但缓存与失效动作放在一处，别让调用方自己
    去改 JSON。
    """
    if not cache_dir:
        return
    try:
        digest = _file_sha256(Path(file_path))
    except OSError:
        return
    with _CACHE_LOCK:
        data = _load_cache(cache_dir)
        if digest in data:
            data.pop(digest, None)
            _save_cache(cache_dir, data)


# ── 上传 ──

def upload_file(file_path: str, api_key: str, purpose: str = DOC_PURPOSE) -> dict:
    """上传 → 返回响应体（含 `id`）。失败抛 `AppError`（由 `check_response` 翻译）。"""
    path = Path(file_path)
    with path.open("rb") as fh:
        resp = requests.post(
            FILES_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            # ⚠️ 顺序：requests 先写 data 再写 files，正好满足官方「purpose 在前」
            data={"purpose": purpose},
            files={"file": (path.name, fh)},
            timeout=UPLOAD_TIMEOUT,
        )
    return check_response(resp, "上传文档")


def ensure_file_id(file_path: str, api_key: str, cache_dir: str = "",
                   purpose: str = DOC_PURPOSE) -> str:
    """取该文件的 `file_id` —— 缓存命中则复用，否则上传。

    返回空串 = 上传成功但响应里没有 `id`（结构不符，记 warning，由调用方回退）。
    其余失败抛 `AppError`，调用方负责转成「保持文本模式」。
    """
    cached = cached_file_id(file_path, cache_dir)
    if cached:
        logger.info("DeepSeek Files 缓存命中: %s", Path(file_path).name)
        return cached

    path = Path(file_path)
    body = upload_file(str(path), api_key, purpose=purpose)
    file_id = str(body.get("id") or "")
    if not file_id:
        logger.warning("DeepSeek Files 上传响应缺少 id: %s", str(body)[:200])
        return ""

    logger.info("DeepSeek Files 上传完成: %s → %s (purpose=%s)",
                path.name, file_id, purpose)

    if cache_dir:
        digest = ""
        try:
            digest = _file_sha256(path)
        except OSError as e:
            logger.warning("DeepSeek Files 计算指纹失败，本次不缓存: %s", str(e)[:80])
        if digest:
            with _CACHE_LOCK:
                data = _load_cache(cache_dir)
                data[digest] = {
                    "file_id": file_id,
                    "filename": path.name,
                    "purpose": purpose,
                    "size": path.stat().st_size,
                    "created_at": time.time(),
                }
                _save_cache(cache_dir, data)
    return file_id
