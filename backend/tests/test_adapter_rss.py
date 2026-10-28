"""RSS / Atom / RDF 订阅源导入 —— 嗅探、合并形态、以及**普通网页逐字节零回归**。

这里有三个判据容易写错，各配一条对照：

1. **嗅探是两道，不是一道**。`Content-Type` 含 `xml` 就足以让 feedparser 试一次
   （有些 feed 的 `<rss` 不在前 2KB 内），前 2KB 的标记则覆盖「服务器把 type 报成
   `text/html`」的站。两道任一中即试 —— 而**真正的判据是 feedparser 出不出条目**，
   所以「嗅探命中但解析不出来」必须回退网页路径而不是报错。
2. **`_download()` 要把响应头带出来**。旧签名 `-> str` 只 return `resp.text`，
   Content-Type 在闭包里就丢了，`_looks_like_feed` 的第一道筛当场永不成立 ——
   代码看起来完整（第二道筛照样工作），失效是**静默**的。本文件那条
   `test_ContentType_单独构成判据` 就是它的守卫。
3. **普通网页零回归**。RSS 分支插在 `_download` 与 `_extract_with_trafilatura`
   之间，走不到就一个字节都不该影响输出。
"""
import asyncio
import re

import pytest

from app.adapters import webpage as W
from app.core.types import PipelineInput, InputType


# ── fixtures ──

RSS2 = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>某个技术博客</title>
    <link>https://blog.example.com/</link>
    <description>每周一篇</description>
    <item>
      <title>第一篇</title>
      <link>https://blog.example.com/p1</link>
      <description>第一篇文章的正文内容，够长。</description>
    </item>
    <item>
      <title>第二篇</title>
      <link>https://blog.example.com/p2</link>
      <description>&lt;p&gt;第二篇&lt;b&gt;带 HTML&lt;/b&gt; 的摘要文本&lt;/p&gt;</description>
    </item>
  </channel>
</rss>
"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom 订阅</title>
  <entry>
    <title>Atom 条目一</title>
    <link href="https://atom.example.com/a1"/>
    <summary>Atom 条目的摘要。</summary>
  </entry>
</feed>
"""

SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/1</loc></url>
  <url><loc>https://example.com/2</loc></url>
</urlset>
"""

PLAIN_HTML = """<html><head><title>普通网页</title></head>
<body><article><p>这是一篇普通网页的正文，长度足够通过校验。</p></article></body></html>
"""


# ═══════════════════════════════════════════════════════════
# 1. 嗅探（两道筛）
# ═══════════════════════════════════════════════════════════

def test_ContentType_单独构成判据():
    """⚠️ 这条是「`_download` 带出响应头」的守卫。

    body 里**一个 feed 标记都没有**（用注释把 `<rss` 推到 2KB 之外），
    只有 Content-Type 说它是 feed。旧签名漏掉响应头时这里必然 False。
    """
    body = "<?xml version='1.0'?><!--" + "x" * 3000 + "--><rss version='2.0'/>"
    assert "<rss" not in body[:W._FEED_SNIFF_BYTES]

    assert W._looks_like_feed("application/rss+xml; charset=utf-8", body) is True
    assert W._looks_like_feed("text/xml", body) is True
    # 对照：同样的 body，type 报成 html ⇒ 不该命中
    assert W._looks_like_feed("text/html", body) is False


def test_标记单独构成判据():
    """服务器把 feed 的 Content-Type 报成 `text/html` 是常见现象。"""
    for marker in ("<rss version='2.0'>", "<feed xmlns='...'>", "<rdf:RDF>"):
        assert W._looks_like_feed("text/html", marker + "x") is True


def test_标记只在开头_2KB_内才算():
    """正文里引用了 `<rss>` 字样的博客文章不该被当成 feed。"""
    body = "<html><body>" + "y" * 3000 + "<p>讲讲 &lt;rss&gt; 协议</p></body></html>"
    assert W._looks_like_feed("text/html", body) is False

    # ⚠️ 已知边界（刻意不加代码去堵）：真把 `<rss` 写在开头 2KB 内的文章会命中
    # 第一道筛。它由第二道筛兜住 —— `_merge_feed` 出不了条目就回退网页路径，
    # 代价是白跑一次 feedparser。而堵它需要「标记后面必须紧跟 `version=`」之类的
    # 启发式，那是给一个已有兜底的场景加规则。
    assert W._looks_like_feed("text/html", "<p><rss 是什么</p>") is True


# ═══════════════════════════════════════════════════════════
# 2. 合并形态
# ═══════════════════════════════════════════════════════════

def test_RSS2_合并成一篇():
    merged, title = W._merge_feed(RSS2)

    assert title == "某个技术博客"
    assert merged.startswith("# 某个技术博客")
    assert "## 第一篇" in merged and "## 第二篇" in merged
    assert "https://blog.example.com/p1" in merged
    assert "第一篇文章的正文内容" in merged


def test_Atom_合并成一篇():
    merged, title = W._merge_feed(ATOM)

    assert title == "Atom 订阅"
    assert "## Atom 条目一" in merged
    assert "https://atom.example.com/a1" in merged
    assert "Atom 条目的摘要" in merged


def test_条目里的HTML被剥成纯文本():
    """摘要常是 HTML 片段 —— 剥标签走**与网页同一套**清洗而不是另写一份。"""
    merged, _ = W._merge_feed(RSS2)
    assert "<p>" not in merged and "<b>" not in merged
    assert "带 HTML" in merged


def test_解析不出条目返回空():
    """sitemap / 损坏的 feed —— 让调用方回退网页路径，而不是在这里抛。"""
    assert W._merge_feed(SITEMAP) == ("", "")
    assert W._merge_feed("不是 XML") == ("", "")
    assert W._merge_feed("") == ("", "")


def test_条目上限():
    """订阅源动辄上百条，全量合并会撑爆生成预算。"""
    items = "".join(
        f"<item><title>第{i}篇</title><link>https://e.com/{i}</link>"
        f"<description>内容{i}</description></item>"
        for i in range(25))
    feed = f"<?xml version='1.0'?><rss version='2.0'><channel><title>T</title>{items}</channel></rss>"

    merged, _ = W._merge_feed(feed)

    assert len(re.findall(r"^## ", merged, re.M)) == W.RSS_MAX_ENTRIES == 20
    assert "第19篇" in merged
    assert "第20篇" not in merged


def test_单条正文截断():
    feed = ("<?xml version='1.0'?><rss version='2.0'><channel><title>T</title><item>"
            "<title>长文</title><description>" + "字" * 5000 +
            "</description></item></channel></rss>")

    merged, _ = W._merge_feed(feed)

    assert merged.count("字") == W.RSS_ENTRY_CHARS == 2000


def test_无标题条目与无链接条目都不炸():
    feed = ("<?xml version='1.0'?><rss version='2.0'><channel><title>T</title><item>"
            "<description>只有正文</description></item></channel></rss>")

    merged, _ = W._merge_feed(feed)

    assert "(无标题)" in merged
    assert "只有正文" in merged


# ═══════════════════════════════════════════════════════════
# 3. WebpageAdapter.adapt() 三条路径
# ═══════════════════════════════════════════════════════════

class _Resp:
    def __init__(self, text: str, content_type: str):
        self.text = text
        self.apparent_encoding = "utf-8"
        self.encoding = "utf-8"
        self.headers = {"Content-Type": content_type}

    def raise_for_status(self):
        pass


def _mock_get(monkeypatch, text: str, content_type: str) -> list[dict]:
    calls: list[dict] = []

    def _fake(url, **kw):
        calls.append({"url": url, "kwargs": kw})
        return _Resp(text, content_type)

    monkeypatch.setattr("app.utils.url_security.safe_get", _fake)
    return calls


def _mock_trafilatura(monkeypatch, extracted: str = "普通网页的正文，长度足够。") -> None:
    monkeypatch.setattr(W, "_extract_with_trafilatura", lambda html: extracted)

    class _Meta:
        title = "普通网页"

    monkeypatch.setattr(W.trafilatura, "extract_metadata", lambda html: _Meta())


def test_feed_URL_走合并分支(monkeypatch):
    _mock_get(monkeypatch, RSS2, "application/rss+xml")
    # 若真的走到网页路径，这个 mock 会把输出换成别的东西 —— 于是「走错了」
    # 在断言上立刻可见
    _mock_trafilatura(monkeypatch, "不该出现的网页正文")

    out = asyncio.run(W.WebpageAdapter().adapt(
        PipelineInput(type=InputType.URL, url="https://blog.example.com/feed")))

    assert out.engine == "feedparser"
    assert out.metadata.source_title == "某个技术博客"
    assert out.raw_text.startswith("# 某个技术博客")
    assert "不该出现的网页正文" not in out.raw_text


def test_嗅探命中但无条目_回退网页路径(monkeypatch):
    """sitemap 的 Content-Type 也是 xml —— 判据是「拿不拿得到条目」。"""
    _mock_get(monkeypatch, SITEMAP, "application/xml")
    _mock_trafilatura(monkeypatch, "sitemap 页面的正文")

    out = asyncio.run(W.WebpageAdapter().adapt(
        PipelineInput(type=InputType.URL, url="https://example.com/sitemap.xml")))

    assert out.engine == "trafilatura"
    assert out.raw_text == "sitemap 页面的正文"


def test_普通网页逐字节零回归(monkeypatch):
    """⚠️ 对照：RSS 分支对它一个字节都不该产生影响。"""
    _mock_get(monkeypatch, PLAIN_HTML, "text/html; charset=utf-8")
    _mock_trafilatura(monkeypatch, "普通网页的正文，长度足够。")

    out = asyncio.run(W.WebpageAdapter().adapt(
        PipelineInput(type=InputType.URL, url="https://example.com/post")))

    assert out.engine == "trafilatura"
    assert out.raw_text == "普通网页的正文，长度足够。"
    assert out.metadata.source_title == "普通网页"
    assert out.metadata.language == "zh"


def test_中文正文判zh_英文判en():
    assert W._guess_language("这是一段中文") == "zh"
    assert W._guess_language("this is english") == "en"
    assert W._guess_language("") == "en"
