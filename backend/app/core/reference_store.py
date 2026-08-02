"""参考文件暂存 — 内存 dict + TTL，生成时按短 id 取内容。

背景：流式 generate 是 GET（query string），参考文件 ≤200KB 无法直接放 URL
（超长 URL 在代理/日志层是隐患）。改为：前端 POST 内容 → 拿短 id → 生成时只传 id。

进程内存 → 重启自动清空（契合「仅会话内不持久化」决策）；TTL 惰性清理。
"""

import logging
import secrets
import threading
import time

logger = logging.getLogger(__name__)

# 暂存有效期（秒）— 前端选文件到点生成通常在几分钟内
TTL = 20 * 60

# id → {name, content, expires_at}
_store: dict[str, dict] = {}
_lock = threading.Lock()


def save_reference(name: str, content: str) -> str:
    """暂存一份参考文件，返回 URL 安全的短 id。"""
    rid = secrets.token_hex(8)
    with _lock:
        _store[rid] = {
            "name": name,
            "content": content,
            "expires_at": time.time() + TTL,
        }
    logger.info("Reference staged: %s (%d bytes) -> %s", name, len(content.encode("utf-8")), rid)
    return rid


def get_references(ids: list[str]) -> list[dict]:
    """按序取参考文件内容（{name, content}），惰性清理过期项；不存在的 id 跳过。"""
    now = time.time()
    result: list[dict] = []
    with _lock:
        for rid in ids:
            entry = _store.get(rid)
            if not entry:
                continue
            if entry["expires_at"] < now:
                _store.pop(rid, None)
                logger.info("Reference expired: %s", rid)
                continue
            result.append({"name": entry["name"], "content": entry["content"]})
    return result
