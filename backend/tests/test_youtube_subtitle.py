"""
YouTube 字幕直拉（youtube-transcript-api 1.x）——
API 形状契约 / 失败分型 / 分段转换 / 代理与 cookie 是否真被用上。

背景（2026-10-10 修复）：代码按 0.6.x 的 API 写，而实际装的依赖是 1.2.x
（pyproject 当时写 `>=0.6`，松到会装 1.x）。`YouTubeTranscriptApi.list(vid, cookies=...)`
每次必抛 TypeError，又被 `except Exception` 吞掉 → 字幕路径**从未成功过**，静默回退到
yt-dlp 下载（用户感受为「油管导入特别慢／像是不通」）。实测：代理与 yt-dlp 都是好的。

本文件第一组断言就是为「这种依赖漂移」立的守卫 —— 它要在**不联网**的前提下抓住
「调用方式与安装版本不同源」。
"""

import inspect

import pytest
import requests

import youtube_transcript_api as yta
from youtube_transcript_api import FetchedTranscriptSnippet, YouTubeTranscriptApi

from app.adapters import youtube_subtitle as yt

_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


# ══════════════ ① API 形状契约：钉住 1.x ══════════════

def test_api_shape_contract():
    """我们写死的调用方式必须与实际安装版本同源。

    ⚠️ 「能 import」抓不到任何东西 —— 0.6.x 也 import 得动，只是调用方式不同，
    表现就是每次调用抛 TypeError 被 except 吞掉（静默失效）。所以这里直取签名。
    """
    list_params = list(inspect.signature(YouTubeTranscriptApi.list).parameters)
    assert list_params[0] == "self", "list 应是实例方法（1.x 起）"
    assert "cookies" not in list_params, "1.x 的 list 不接受 cookies"

    fetch_params = list(inspect.signature(YouTubeTranscriptApi.fetch).parameters)
    assert fetch_params[0] == "self", "fetch 应是实例方法（1.x 起）"

    init_params = inspect.signature(YouTubeTranscriptApi.__init__).parameters
    assert "proxy_config" in init_params, "1.x 代理经 proxy_config 传"
    assert "http_client" in init_params, "1.x cookie 只能经 http_client 传"

    assert not issubclass(FetchedTranscriptSnippet, dict), "片段是 dataclass，不是 dict"
    assert {"text", "start", "duration"} <= set(FetchedTranscriptSnippet.__dataclass_fields__)


def test_contract_has_teeth():
    """对照实验：证明上面的签名断言**真有区分力** —— 旧调用方式确实会炸。

    没有这条，签名断言可能只是「签名恰好长这样」，说不清它防的是什么。
    `list()` 在参数绑定阶段就失败，**不联网**。
    """
    with pytest.raises(TypeError):
        YouTubeTranscriptApi.list("dQw4w9WgXcQ", cookies=None)


# ══════════════ ② 失败分型：谁静默、谁冒泡 ══════════════

_NO_SUBTITLE_CASES = [
    ("TranscriptsDisabled", ("v",)),
    ("NoTranscriptFound", ("v", ["en"], None)),
    ("VideoUnavailable", ("v",)),
    ("VideoUnplayable", ("v", "reason", [])),
    ("InvalidVideoId", ("v",)),
    ("AgeRestricted", ("v",)),
]
_BLOCKED_CASES = [
    ("IpBlocked", ("v",)),
    ("RequestBlocked", ("v",)),
    ("PoTokenRequired", ("v",)),
    ("YouTubeRequestFailed", ("v", requests.exceptions.HTTPError("x"))),
    ("YouTubeDataUnparsable", ("v",)),
]


@pytest.mark.parametrize("name,args", _NO_SUBTITLE_CASES)
def test_no_subtitle_errors_are_normal_degradation(name, args):
    """「视频没有字幕」= 正常降级（静默 return None，上层改走下载 + ASR）。"""
    e = getattr(yta, name)(*args)
    assert yt._is_no_subtitle(e), name
    assert not yt._is_blocked(e), name   # 两桶必须互斥，否则该冒泡的会被静默吞掉


@pytest.mark.parametrize("name,args", _BLOCKED_CASES)
def test_blocked_errors_must_bubble(name, args):
    """「被反爬 / 网络拦下」= 用户唯一能做的动作是查代理，必须显式冒泡。"""
    e = getattr(yta, name)(*args)
    assert yt._is_blocked(e), name
    assert not yt._is_no_subtitle(e), name


# ══════════════ ③ 分段转换 ══════════════

def test_segments_from_fetched():
    """1.x 的 dataclass 序列 → 本项目 segments 结构（空格段丢弃、end = start + duration）。"""
    fetched = [
        FetchedTranscriptSnippet(text="你好世界", start=1.5, duration=2.5),
        FetchedTranscriptSnippet(text="   ", start=0.0, duration=1.0),
        FetchedTranscriptSnippet(text="hello", start=10.0, duration=0.0),
    ]
    assert yt._segments_from_fetched(fetched) == [
        {"start": 1.5, "end": 4.0, "text": "你好世界"},
        {"start": 10.0, "end": 10.0, "text": "hello"},
    ]


# ══════════════ ④ video ID 提取 ══════════════

@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42", "dQw4w9WgXcQ"),
    ("https://www.bilibili.com/video/BV1xx411c7mD", None),
])
def test_extract_video_id(url, expected):
    assert yt._extract_video_id(url) == expected


# ══════════════ ⑤ 接线行为（不联网，替身 API） ══════════════

class _FakeApi:
    """替身：记录构造入参，并在 list() 按剧本抛异常。"""

    last_init: dict = {}
    list_exc: BaseException | None = None

    def __init__(self, proxy_config=None, http_client=None):
        _FakeApi.last_init = {"proxy_config": proxy_config, "http_client": http_client}

    def list(self, video_id):
        raise _FakeApi.list_exc


@pytest.fixture
def fake_api(monkeypatch):
    monkeypatch.setattr(yt, "YouTubeTranscriptApi", _FakeApi)
    _FakeApi.list_exc = None
    _FakeApi.last_init = {}
    return _FakeApi


def test_proxy_reaches_the_api(fake_api):
    """守卫：配了代理必须**真的传进 SDK** —— 丢了就是「代理配了但没用」，
    与本次修的 bug 同类（配置存在 ≠ 该路径在用）。"""
    fake_api.list_exc = yta.TranscriptsDisabled("v")
    assert yt.fetch_subtitles(_URL, proxy="http://127.0.0.1:7890") is None
    pc = fake_api.last_init["proxy_config"]
    assert pc is not None
    assert pc.http_url == "http://127.0.0.1:7890"
    assert pc.https_url == "http://127.0.0.1:7890"


def test_no_proxy_means_no_proxy_config(fake_api):
    """对照实验：不配代理时不该硬塞一个空壳 proxy_config。"""
    fake_api.list_exc = yta.TranscriptsDisabled("v")
    assert yt.fetch_subtitles(_URL, proxy="") is None
    assert fake_api.last_init["proxy_config"] is None


def test_cookie_reaches_the_session(fake_api):
    """cookie：1.x 只能经 http_client（requests.Session）传。"""
    fake_api.list_exc = yta.TranscriptsDisabled("v")
    assert yt.fetch_subtitles(_URL, cookie_str="SID=abc; HSID=def") is None
    sess = fake_api.last_init["http_client"]
    assert sess.cookies.get("SID") == "abc"
    assert sess.cookies.get("HSID") == "def"


def test_degradation_vs_bubble(fake_api):
    """本次修复的语义核心：没字幕 → 静默 None；被拦 → 抛错（用户该去查代理）。"""
    fake_api.list_exc = yta.TranscriptsDisabled("v")
    assert yt.fetch_subtitles(_URL) is None

    fake_api.list_exc = yta.IpBlocked("v")
    with pytest.raises(RuntimeError, match="代理"):
        yt.fetch_subtitles(_URL)
