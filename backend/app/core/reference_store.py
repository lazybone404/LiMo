"""参考文件暂存 — 内存 dict + TTL，生成时按短 id 取内容。

背景：流式 generate 是 GET（query string），参考文件 ≤1MB 无法直接放 URL
（超长 URL 在代理/日志层是隐患）。改为：前端 POST 内容 → 拿短 id → 生成时只传 id。

进程内存 → 重启自动清空（契合「仅会话内不持久化」决策）；TTL 过期项**全表清理**。
"""

import secrets
import threading
import time

from app.core import logging as log
from app.core.errors import AppError

# 暂存有效期（秒）— 前端选文件到点生成通常在几分钟内
TTL = 20 * 60

# 暂存闸门（纵深防御，2026-09-23 单文件上限放宽到 1MB 时补）。
# 单文件大小已由路由校验，但**条目数原本无界**：前端换一次参考文件就换一个 id，
# 旧 id 从此不再被查询。没有这两道闸，反复换文件会持续吃内存直到进程重启。
# 正常使用（一次一份、偶尔替换）永远碰不到它们。
MAX_ENTRIES = 32
MAX_TOTAL_BYTES = 32 * 1024 * 1024

# id → {name, content, size, expires_at}
_store: dict[str, dict] = {}
_lock = threading.Lock()


def _purge_expired_locked(now: float) -> int:
    """清理**全表**过期项，返回清理条数。调用方必须已持锁。

    ⚠️ 必须全表扫，不能只清「本次被请求的 id」：前端换一次参考文件就换一个 id，
    旧 id 之后再也不会出现在 `get_references()` 的入参里 —— 那种按入参惰性清理
    的实现**永远碰不到孤儿条目**，它们会一直驻留到进程重启（1MB 时代一次漏一份，
    20 分钟 TTL 形同虚设）。
    """
    stale = [rid for rid, entry in _store.items() if entry["expires_at"] < now]
    for rid in stale:
        _store.pop(rid, None)
    return len(stale)


def _total_bytes_locked() -> int:
    """全表内容总字节数。调用方必须已持锁（size 在写入时记好，不重复编码）。"""
    return sum(entry.get("size", 0) for entry in _store.values())


def save_reference(name: str, content: str) -> str:
    """暂存一份参考文件，返回 URL 安全的短 id。

    超出条目数/总量闸门时抛 AppError（全局处理器转 400）。
    """
    size = len(content.encode("utf-8"))
    now = time.time()
    rid = secrets.token_hex(8)
    with _lock:
        purged = _purge_expired_locked(now)     # 先腾位置，再判闸门
        if len(_store) >= MAX_ENTRIES or _total_bytes_locked() + size > MAX_TOTAL_BYTES:
            log.warning("参考文件暂存已达上限",
                        entries=len(_store), total_bytes=_total_bytes_locked(),
                        incoming_bytes=size)
            raise AppError("REFERENCE_STORE_FULL",
                           "参考文件暂存区已满，请稍后重试（暂存内容 20 分钟后自动清理）",
                           recoverable=True)
        _store[rid] = {
            "name": name,
            "content": content,
            "size": size,
            "expires_at": now + TTL,
        }
    log.info("参考文件已暂存", name=name, bytes=size, rid=rid)
    if purged:
        log.info("参考文件暂存清理过期项", purged=purged)
    return rid


def get_references(ids: list[str]) -> list[dict]:
    """按序取参考文件内容（{name, content}）；不存在的 id 跳过（含已过期的）。"""
    now = time.time()
    result: list[dict] = []
    with _lock:
        purged = _purge_expired_locked(now)
        for rid in ids:
            entry = _store.get(rid)
            if not entry:
                continue
            result.append({"name": entry["name"], "content": entry["content"]})
    if purged:
        log.info("参考文件暂存清理过期项", purged=purged)
    return result
