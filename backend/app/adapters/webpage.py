"""
网页适配器 — URL → 正文提取（trafilatura 主 + bs4 回退）
"""

import asyncio
import logging
import re

import trafilatura

from app.core.types import PipelineInput, AdapterOutput, RawMetadata, InputType, SourceType, ProgressEvent
from app.core.protocols import PipelineAdapter
from app.core.errors import TranscribeError

logger = logging.getLogger(__name__)


def _garbage_ratio(text: str) -> float:
    """估算文本中垃圾字符（控制字符/不可打印字符）的比例"""
    if not text:
        return 1.0
    # 控制字符 + 替换字符 U+FFFD
    garbage = sum(1 for c in text if ord(c) < 32 and c not in "\n\r\t")
    # 连续 base64 块（超过 60 个 base64 字符 = 约 45 字节的二进制数据）
    b64_blocks = len(re.findall(r"[A-Za-z0-9+/=]{60,}", text))
    return (garbage + b64_blocks * 60) / max(len(text), 1)


def _extract_with_trafilatura(html: str) -> str | None:
    """trafilatura 提取，过滤垃圾"""
    text = trafilatura.extract(
        html,
        output_format="markdown",
        include_comments=False,
        include_tables=True,
        target_language="zh",
        deduplicate=True,
    )
    if not text:
        return None
    text = _clean_extracted(text)
    if _garbage_ratio(text) > 0.3:
        logger.warning("trafilatura 提取结果垃圾比例过高(%.1f%%)，尝试回退", _garbage_ratio(text) * 100)
        return None
    return text


def _extract_fallback(html: str) -> str:
    """回退方案：bs4 纯文本提取（兜底，宁可少不可乱）

    原用 html2text（GPL-3.0），2026-08-26 改为 bs4（项目已有依赖），
    消除主 exe 中的 GPL 依赖。
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style']):
        tag.decompose()
    text = soup.get_text(separator='\n')
    text = _clean_extracted(text)
    if text and len(text) >= 10:
        return text

    # 终极回退：正则去标签
    text = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    text = _clean_extracted(text)
    return text.strip()


def _clean_extracted(text: str) -> str:
    """网页正文后处理：移除 CSS/JS/base64 残留、控制字符、纯符号行。

    trafilatura 对某些页面（尤其是含大量内联样式的 SPA 页面）
    会混入 base64 编码的字体/图片数据和控制字符，导致预览乱码。
    """
    import unicodedata

    lines: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue

        # 1. 跳过纯 base64/CSS 行（>60% 非 ASCII 且不含中文/日文）
        non_ascii = sum(1 for c in stripped if ord(c) > 127)
        cjk = sum(1 for c in stripped if "一" <= c <= "鿿" or "぀" <= c <= "ヿ")
        if non_ascii > len(stripped) * 0.6 and cjk == 0:
            continue

        # 2. 跳过 CSS/JS 残留行
        if re.match(r"^[{}@;:#.()\[\]\-]", stripped) and len(stripped) < 200:
            continue

        # 3. 移除行内 base64 data URI（保留前后的正常文本）
        cleaned = re.sub(r"data:[^;\"')\s]*;base64,[A-Za-z0-9+/=]{100,}", "[图片]", stripped)

        # 4. 移除控制字符
        cleaned = "".join(c for c in cleaned if ord(c) >= 32 or c in "\n\t")

        # 5. 规范化 Unicode
        cleaned = unicodedata.normalize("NFKC", cleaned)

        if cleaned.strip():
            lines.append(cleaned)

    # 合并连续空行
    result = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return result.strip()


# ── RSS / Atom / RDF 订阅源 ──
#
# ⚠️ **走 WebpageAdapter 的内部分支，刻意不新建适配器**：`accept()` 接受任何 http
# URL，新适配器与它**必然打架**（同一 URL 两个 `accept` 都返回 True，谁赢取决于
# 注册顺序）。走内部分支还能免费继承 SSRF 防护 / 逐跳重定向校验 / 编码处理。

#: 合并进正文的条目上限。订阅源动辄上百条，全量合并会撑爆生成预算，
#: 而用户真正要读的是最近这一批。
RSS_MAX_ENTRIES = 20

#: 每条内容截断长度。与网页正文的 4000 字同档但更短 —— 20 条 × 2000 已是 4 万字，
#: 再长就该用户主动挑几篇导入了。
RSS_ENTRY_CHARS = 2000

#: feed 根元素的标记（**小写**比较）。真 feed 的根元素就在开头，只看前 2KB ——
#: 全文扫描会让「正文里引用了 `<rss>` 字样的博客文章」误入。
_FEED_MARKERS = ("<rss", "<feed", "<rdf:rdf")
_FEED_SNIFF_BYTES = 2048


def _looks_like_feed(content_type: str, body: str) -> bool:
    """嗅探响应体是不是订阅源。

    ⚠️ 这只是**第一道筛**，判据是「值不值得拿 feedparser 试一次」，不是「它是不是
    feed」：`application/xml` 的 sitemap、`application/xhtml+xml` 的页面都会通过。
    真正的判据是紧接着 `feedparser.parse()` 出不出 `entries` —— 出不来就回退普通
    网页路径（带一条 `info` 日志）。
    """
    if "xml" in (content_type or "").lower():
        return True
    head = body[:_FEED_SNIFF_BYTES].lower()
    return any(m in head for m in _FEED_MARKERS)


def _entry_text(entry) -> str:
    """取单条目的正文 —— `content` 优先，其次 `summary` / `description`。"""
    raw = ""
    for c in getattr(entry, "content", None) or []:
        val = c.get("value") if isinstance(c, dict) else getattr(c, "value", "")
        if val:
            raw = str(val)
            break
    if not raw:
        raw = str(getattr(entry, "summary", "") or getattr(entry, "description", "") or "")
    if not raw:
        return ""
    # 摘要常是 HTML 片段（带 <p>/<img>）：剥标签后走**与网页同一套**清洗，
    # 而不是另写一份 —— base64 内联图与 NFKC 归一那两条规则也正需要。
    text = _extract_fallback(raw) if "<" in raw else raw
    return _clean_extracted(text)[:RSS_ENTRY_CHARS].strip()


def _merge_feed(body: str) -> tuple[str, str]:
    """把 feed 合并成一篇 Markdown，返回 `(正文, feed 标题)`；解析不出条目返回 `("", "")`。

    形态（用户 2026-10-29 选定「合并成一篇」，而不是一篇一个文档）：

        # {feed 标题}

        ## {条目标题}
        {链接}
        {摘要或正文}
    """
    import feedparser

    parsed = feedparser.parse(body)
    entries = parsed.get("entries") or []
    if not entries:
        return "", ""
    feed = parsed.get("feed") or {}
    title = " ".join(str(feed.get("title") or "").split())[:120] or "订阅聚合"

    blocks: list[str] = [f"# {title}"]
    for e in entries[:RSS_MAX_ENTRIES]:
        entry_title = " ".join(str(e.get("title") or "").split()) or "(无标题)"
        block = [f"## {entry_title}"]
        link = str(e.get("link") or "").strip()
        if link:
            block.append(link)
        text = _entry_text(e)
        if text:
            block.append(text)
        blocks.append("\n\n".join(block))
    return "\n\n".join(blocks), title


def _guess_language(text: str) -> str:
    return "zh" if any("一" <= c <= "鿿" for c in text[:500]) else "en"


class WebpageAdapter(PipelineAdapter):
    name = "webpage"

    def accept(self, input: PipelineInput) -> bool:
        return input.type == InputType.URL and bool(
            re.match(r"^https?://", input.url or "")
        )

    async def adapt(self, input: PipelineInput,
                    on_progress=None,
                    engine_config: dict | None = None,
    ) -> AdapterOutput:
        url = input.url or ""

        if on_progress:
            on_progress(ProgressEvent(phase="download", status="running", message="下载网页..."))

        # SSRF 防护（⚠️ 用 safe_get 而不是裸 requests.get：后者默认跟随重定向，
        # 公网 URL 302 到 127.0.0.1 就绕过了首跳校验）
        from app.utils.url_security import safe_get

        # 下载（to_thread 释放事件循环，否则 requests 最长 30s 阻塞整个后端）
        # 使用 requests 而非 trafilatura.fetch_url，后者编码处理不稳定
        def _download() -> tuple[str, dict]:  # 同步函数（asyncio.to_thread 期望同步；async def 会返回未 await 的 coroutine）
            """返回 `(正文, 响应头)`。

            ⚠️ **响应头必须一起带出来**：feed 嗅探要看 `Content-Type`，而旧签名
            `-> str` 让它在闭包里就丢了 —— 那时「这个 URL 是不是订阅源」这个问题
            在下一行已经无从回答。
            """
            try:
                resp = safe_get(
                    url,
                    timeout=30,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "zh-CN,zh;q=0.9",
                        "Accept-Encoding": "gzip, deflate",
                    },
                )
                resp.raise_for_status()
                # 优先使用响应头声明的编码，回退到 UTF-8
                resp.encoding = resp.apparent_encoding or "utf-8"
                return resp.text, {"content_type": resp.headers.get("Content-Type", "")}
            except ValueError:
                # ⚠️ 安全校验失败**不许回退**：`trafilatura.fetch_url` 会自己发请求
                # 并跟随重定向，正是刚刚被 safe_get 拦下的那条路。直接抛出去。
                raise
            except Exception as e:
                # 回退：换成不带伪装 UA 的普通请求（原来的回退走
                # `trafilatura.fetch_url`，而它**自己发请求并跟随重定向** ——
                # 攻击者返回一个 500 就能把请求推到那条未校验的路径上）。
                logger.warning("requests 下载失败 (%s)，回退为普通请求", str(e)[:80])
                resp = safe_get(url, timeout=30)
                resp.raise_for_status()
                resp.encoding = resp.apparent_encoding or "utf-8"
                return resp.text, {"content_type": resp.headers.get("Content-Type", "")}
        downloaded, resp_headers = await asyncio.to_thread(_download)

        if not downloaded:
            raise TranscribeError.empty(detail=f"无法下载: {url}")

        if on_progress:
            on_progress(ProgressEvent(phase="extract", status="running", message="提取正文..."))

        # RSS / Atom / RDF —— 订阅源不是网页，trafilatura 会把它拆成标题碎条
        # （导航栏 + 一串条目标题），产物不可用且**不报错**。
        if _looks_like_feed(resp_headers.get("content_type", ""), downloaded):
            merged, feed_title = _merge_feed(downloaded)
            if merged:
                logger.info("RSS 订阅源合并: %s（%d 字）", url, len(merged))
                if on_progress:
                    on_progress(ProgressEvent(phase="extract", status="done"))
                return AdapterOutput(
                    raw_text=merged,
                    metadata=RawMetadata(
                        language=_guess_language(merged),
                        source_title=(feed_title or "")[:120],
                    ),
                    engine="feedparser",
                )
            # 嗅探命中但解析不出条目（sitemap / xhtml 页面 / 损坏的 feed）→
            # 按普通网页处理，而不是报错。判据是「拿不拿得到条目」，不是 Content-Type。
            logger.info("feed 嗅探命中但无条目，按普通网页处理: %s", url)

        # 提取：trafilatura 主 → bs4 回退 → 正则去标签兜底
        extracted = _extract_with_trafilatura(downloaded)
        if not extracted:
            logger.info("trafilatura 失败，尝试 bs4 回退")
            extracted = _extract_fallback(downloaded)
        if not extracted or len(extracted) < 10:
            raise TranscribeError.empty(detail=f"正文提取为空: {url}")

        if on_progress:
            on_progress(ProgressEvent(phase="extract", status="done"))

        # 元数据
        meta = trafilatura.extract_metadata(downloaded)

        # 提取网页标题（<title> 标签内容）
        source_title = ""
        if meta and getattr(meta, "title", ""):
            source_title = str(meta.title).strip()[:120]

        return AdapterOutput(
            raw_text=extracted,
            metadata=RawMetadata(
                language=_guess_language(extracted),
                source_title=source_title,
            ),
            engine="trafilatura",
        )
