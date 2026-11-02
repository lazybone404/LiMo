"""验证适配器注册 + 路由。

⚠️ 判据纪律（P1-01，2026-10-06）：原先把结果拼成
`status = "OK" if name == expected else f"FAIL (expected=…, got=…)"` 之后**只 print**。
失败被降级成一行日志，退出码仍是 0，pytest 收集 0 项 —— 路由表被改坏也全绿。
现在改成参数化用例，每条路由一个真断言。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.core.registry import AdapterRegistry
from app.core.types import InputType, PipelineInput
from app.adapters import init_adapters


@pytest.fixture(scope="module", autouse=True)
def _adapters_ready():
    init_adapters()


def test_adapters_registered():
    """注册表非空且覆盖各承载类型（`accept()` 是纯输入判断，此处只看名字集合）。"""
    names = {cls.name for cls in AdapterRegistry.all()}
    print(f"Adapters registered: {len(names)} → {sorted(names)}")
    assert {"text", "webpage", "document", "video"} <= names, f"缺适配器，实际 {names}"


@pytest.mark.parametrize(
    "inp, expected",
    [
        (PipelineInput(type=InputType.TEXT, text="hello"), "text"),
        (PipelineInput(type=InputType.URL, url="https://example.com"), "webpage"),
        (PipelineInput(type=InputType.FILE, file_path="test.pdf"), "document"),
        (PipelineInput(type=InputType.FILE, file_path="test.docx"), "document"),
        (PipelineInput(type=InputType.FILE, file_path="test.mp4"), "video"),
    ],
    ids=["text", "url", "pdf", "docx", "mp4"],
)
def test_adapter_routing(inp, expected):
    """注册顺序决定路由优先级：Video → Document → Image → Webpage → Text（兜底）。"""
    found = AdapterRegistry.route(inp)
    name = found.name if found else None
    print(f"  {'OK' if name == expected else 'FAIL'}: {inp.type} "
          f"{inp.url or inp.file_path or (inp.text or '')[:20]} → {name}")
    assert name == expected, f"路由错误：期望 {expected}，实际 {name}"


# ══════════════ 视频 URL 的识别（2026-11-03）══════════════
#
# 判据是**主机名**不是子串，且 `VIDEO_DOMAINS` 里每个站都用 yt-dlp 的
# `ie.suitable(url)` 核实过有没有专用提取器（`generic` 对任何 URL 都返回 True，
# 核实时必须排除它 —— 否则 `b23.tv` 这种只有 `generic` 的站会假绿）。
#
# 为什么值得钉住：`accept()` 放行的语义是**承诺「这个链接能转写」**。放进来却下不来，
# 用户看到的是「视频下载失败」；不放进来，它就被当网页抓成一篇空壳。两种错都不报错。

@pytest.mark.parametrize(
    "url, expected",
    [
        # —— 本次新覆盖的形态 ——
        ("https://b23.tv/abcdefg", "video"),                    # B 站官方短链，原先漏判
        ("https://B23.TV/abcdefg", "video"),                    # 大写主机名
        ("https://b23.tv/abcdefg?share_source=copy", "video"),  # 分享参数
        ("https://m.bilibili.com/video/BV1xx411c7mD", "video"), # 移动端
        ("https://live.bilibili.com/12345", "video"),           # 直播回放
        ("https://www.bilibili.tv/en/video/12345", "video"),    # 海外版（走 BiliIntl，拉不到字幕但能下）
        ("https://v.douyin.com/abc/", "video"),                 # 抖音短链
        ("https://www.iesdouyin.com/share/video/123/", "video"),# 抖音分享页
        ("https://www.xiaohongshu.com/explore/x", "video"),
        ("https://xhslink.com/abc", "video"),
        ("https://www.ixigua.com/123", "video"),
        ("https://m.weibo.cn/status/123", "video"),
        ("https://www.acfun.cn/v/ac123", "video"),
        ("https://v.qq.com/x/page/abc.html", "video"),
        ("https://youtu.be/dQw4w9WgXcQ", "video"),
        ("https://music.youtube.com/watch?v=dQw4w9WgXcQ", "video"),
        ("https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ", "video"),
        # —— 该走网页的 ——
        ("https://www.bilibili.com/read/cv123", "webpage"),     # 专栏文章
        ("https://www.bilibili.com/opus/123", "webpage"),       # 动态图文
        ("https://example.com/?ref=bilibili.com", "webpage"),   # ⚠️ 子串误判的对照组
        ("https://notbilibili.com/x", "webpage"),               # 后缀但不是子域
    ],
    ids=[
        "b23-short", "b23-upper", "b23-share-param", "bili-mobile", "bili-live", "bili-tv",
        "douyin-short", "douyin-share", "xhs", "xhslink", "ixigua", "weibo", "acfun",
        "vqq", "youtube-short", "youtube-music", "youtube-nocookie",
        "bili-read", "bili-opus", "substring-trap", "suffix-trap",
    ],
)
def test_video_url_routing(url, expected):
    """粘贴各种平台的链接 → 落到哪个适配器。"""
    found = AdapterRegistry.route(PipelineInput(type=InputType.URL, url=url))
    name = found.name if found else None
    assert name == expected, f"{url} 应路由到 {expected}，实际 {name}"


def test_b23_短链在两层判据上一致():
    """`accept()` 放行 b23.tv 之后，`adapt()` 里的 `is_bilibili` 也必须是真。

    ⚠️ 这两处判据分开写过（accept 用子串表、adapt 用 `"bilibili.com" in url`），
    分开的失效形态是「短链能进门、进门后却按非 B 站处理」—— 只在 b23.tv 上复现，
    表现为绕开字幕直拉、白等一次 yt-dlp 下载。现在两侧共用 `_host_in`。
    """
    from app.adapters.video import BILIBILI_SUBTITLE_DOMAINS, _host_in, _is_video_url

    for url in ("https://b23.tv/abcdefg", "https://www.bilibili.com/video/BV1xx411c7mD"):
        assert _is_video_url(url), f"{url} 应被 accept 放行"
        assert _host_in(url, BILIBILI_SUBTITLE_DOMAINS), f"{url} 进门后却没被认成 B 站"

    # 海外版刻意**不在**字幕域名表里：它没有那套 API，硬拉只是白等一次超时
    assert not _host_in("https://www.bilibili.tv/en/video/1", BILIBILI_SUBTITLE_DOMAINS)
