"""
Evidence Manager — 证据统一格式 + 缓存 + 去重

不暴露给 LLM，作为工具原始输出和 Prompt 之间的中间层。
所有工具返回统一格式的 Evidence，Prompt 不感知底层实现。

以后换搜索源（Firecrawl / Exa / Brave 等）只改这里，不改 Prompt。
"""

import hashlib
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# ── 内存缓存（进程生命周期内有效）──

import threading as _threading

_cache: dict[str, dict[str, Any]] = {}
_cache_lock = _threading.Lock()
_url_seen: set[str] = set()
_url_seen_lock = _threading.Lock()
CACHE_TTL = 24 * 3600  # 24 小时


def _cache_key(claim_text: str) -> str:
    """基于 claim 文本生成缓存 key"""
    return hashlib.sha256(claim_text.encode()).hexdigest()[:16]


def lookup_evidence(claim_text: str) -> list[dict] | None:
    """查找缓存的证据。返回 None 表示缓存未命中或已过期。"""
    key = _cache_key(claim_text)
    with _cache_lock:
        entry = _cache.get(key)
        if entry and (time.time() - entry["cached_at"] < CACHE_TTL):
            logger.debug("Evidence cache hit: %s", claim_text[:60])
            return entry["evidence"]
    return None


def store_evidence(claim_text: str, evidence: list[dict]) -> None:
    """缓存证据"""
    key = _cache_key(claim_text)
    with _cache_lock:
        _cache[key] = {"evidence": evidence, "cached_at": time.time()}
        # 定期清理过期缓存（每 100 次写入清理一次）
        if len(_cache) % 100 == 0:
            _gc_cache()
    logger.debug("Evidence cached: %s (%d items)", claim_text[:60], len(evidence))


def _gc_cache() -> None:
    """清理过期的缓存条目"""
    now = time.time()
    expired = [k for k, v in _cache.items()
               if now - v["cached_at"] >= CACHE_TTL]
    for k in expired:
        del _cache[k]
    if expired:
        logger.debug("Evidence cache GC: removed %d expired entries", len(expired))


def is_url_fetched(url: str) -> bool:
    """检查 URL 是否已被抓取"""
    url_hash = hashlib.sha256(url.encode()).hexdigest()
    with _url_seen_lock:
        return url_hash in _url_seen


def mark_url_fetched(url: str) -> None:
    """标记 URL 已抓取"""
    url_hash = hashlib.sha256(url.encode()).hexdigest()
    with _url_seen_lock:
        _url_seen.add(url_hash)


def clear_cache() -> None:
    """清空所有缓存"""
    with _cache_lock:
        _cache.clear()
    with _url_seen_lock:
        _url_seen.clear()


# ── 统一证据格式 ──

def format_evidence(
    title: str = "",
    url: str = "",
    snippet: str = "",
    content: str = "",
    domain: str = "",
    published: str = "",
    author: str = "",
    source: str = "unknown",
    credibility: str = "unknown",
) -> dict:
    """创建统一格式的证据对象。

    Prompt 永远看到这个格式，不关心底层是 Tavily / DDG / SearXNG / Jina。
    """
    return {
        "title": title,
        "url": url,
        "snippet": snippet[:300],
        "content": content[:6000],
        "domain": domain,
        "published": published,
        "author": author,
        "source": source,
        "credibility": credibility,
        "retrieved_at": time.time(),
    }


def format_evidence_from_search(result: dict, source: str = "unknown") -> dict:
    """从搜索结果创建统一格式证据"""
    url = result.get("url", "")
    domain = ""
    import re as _re
    m = _re.search(r'https?://([^/]+)', url)
    if m:
        domain = m.group(1)

    return format_evidence(
        title=result.get("title", ""),
        url=url,
        snippet=result.get("snippet", ""),
        content=result.get("content", ""),
        domain=domain,
        source=source,
        credibility="unknown",
    )


def format_evidence_list(results: list[dict], source: str = "unknown") -> list[dict]:
    """批量格式化搜索结果"""
    return [format_evidence_from_search(r, source) for r in results]
