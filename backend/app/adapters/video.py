"""
视频适配器 — URL → 字幕直拉优先 → 无字幕则下载+ASR
参照 BiliNote pipeline.py: pipe_subtitle → pipe_download → pipe_transcribe
"""

import asyncio
import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from app.core.types import PipelineInput, AdapterOutput, RawMetadata, InputType, ProgressEvent
from app.core.protocols import PipelineAdapter
from app.core.registry import EngineRegistry
from app.core.errors import RetryableError, FatalError, retry
from app.core.net import proxy_for
from app.engines.credentials import apply_provider_credentials

logger = logging.getLogger(__name__)

# 能交给 yt-dlp 的站点。**只列「有专用提取器」或「已知会重定向到有提取器域名的短链」**
# —— `accept()` 放行等于承诺「这个链接能转写」，放进来却下不来时用户看到的是
# 「视频下载失败」，比当成网页抓一篇空壳更接近事实，但仍然是一次失败的承诺。
#
# 2026-11-03 用 `yt_dlp.extractor.gen_extractor_classes()` 的 `ie.suitable(url)` 逐个核实
# （注意 `generic` 对任何 URL 都返回 True，判定必须排除它）：
#   bilibili.com → BiliBili ／ bilibili.tv → BiliIntl ／ live.bilibili.com → BiliLive
#   b23.tv → **无**专用提取器，但 generic 跟随 302 落到 www.bilibili.com 后由 BiliBili 接手
#   douyin.com → Douyin ／ iesdouyin.com → 分享页，同样靠重定向
#   ixigua.com → Ixigua ／ xiaohongshu.com → XiaoHongShu ／ m.weibo.cn → Weibo
#   acfun.cn → AcFunVideo ／ v.qq.com → vqq:video ／ youku.com → youku ／ iqiyi.com → iqiyi
#   douyu.com → DouyuTV ／ huya.com → huya:live ／ tiktok.com → TikTok
#   youtube.com / youtu.be / youtube-nocookie.com → youtube
#   vimeo / twitch / twitter(=x.com) / instagram / facebook / dailymotion / nicovideo
VIDEO_DOMAINS = frozenset({
    # B 站（b23.tv 是官方短链，bilibili.tv 是海外版）
    "bilibili.com", "b23.tv", "bilibili.tv",
    # YouTube
    "youtube.com", "youtu.be", "youtube-nocookie.com",
    # 抖音 / TikTok（iesdouyin.com 是分享页域名）
    "douyin.com", "iesdouyin.com", "tiktok.com",
    # 国内其他有 yt-dlp 提取器的站点
    "ixigua.com", "xiaohongshu.com", "xhslink.com", "weibo.com", "weibo.cn",
    "acfun.cn", "v.qq.com", "youku.com", "iqiyi.com", "douyu.com", "huya.com",
    # 海外
    "vimeo.com", "twitch.tv", "twitter.com", "x.com", "instagram.com",
    "facebook.com", "dailymotion.com", "nicovideo.jp",
})

# B 站字幕直拉认这两个域名。⚠️ **不含 `bilibili.tv`** —— 海外版没有这套 API，
# 它走 yt-dlp 的 BiliIntl 提取器，硬拉字幕只会白等一次超时。
BILIBILI_SUBTITLE_DOMAINS = frozenset({"bilibili.com", "b23.tv"})

YOUTUBE_DOMAINS = frozenset({"youtube.com", "youtu.be", "youtube-nocookie.com"})

DOUYIN_DOMAINS = frozenset({"douyin.com", "iesdouyin.com"})

# 同域但**不是视频**的路径 —— B 站专栏（`/read/cv…`）与动态图文（`/opus/…`）该走网页适配器。
# ⚠️ 判据必须按域名成对给：只写路径前缀的话，别的站同名前缀会被一起排除。
_NON_VIDEO_PATHS: tuple[tuple[str, str], ...] = (
    ("bilibili.com", "/read/"),
    ("bilibili.com", "/opus/"),
)


def _url_host(url: str) -> str:
    """URL → 小写主机名（无端口）。解析不出来返回空串。

    ⚠️ **判据必须是主机名，不能是 `d in url` 子串**：子串写法下
    `https://example.com/?ref=bilibili.com` 会被当成 B 站链接送去下视频 ——
    既不报错，也不会有正确结果（真正的主人在 `example.com`）。
    """
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:      # 畸形 URL（如 `http://[::1`）的 hostname 会抛
        return ""


def _host_in(url: str, domains) -> bool:
    """主机名命中集合。`bilibili.com` 覆盖 `m.bilibili.com`，**不**覆盖 `notbilibili.com`。"""
    host = _url_host(url)
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in domains)


def _is_video_url(url: str) -> bool:
    """URL 是否指向一个能交给 yt-dlp 的视频页。"""
    if not _host_in(url, VIDEO_DOMAINS):
        return False
    host = _url_host(url)
    path = (urlparse(url).path or "").lower()
    for d, prefix in _NON_VIDEO_PATHS:
        if (host == d or host.endswith("." + d)) and path.startswith(prefix):
            return False
    return True

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com",
    "Origin": "https://www.bilibili.com",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _cookie_header(cookie_str: str) -> str | None:
    """Cookie 字符串 → 可直接放进 `http_headers['Cookie']` 的形式。

    ⚠️ **刻意不落盘。** 此前是 `_cookie_to_file`：把 cookie 写成系统 TEMP 里的 Netscape
    明文文件供 yt-dlp 读，用完在 finally 里删。问题在于它是一份**明文凭据落盘** ——
    正常运行会删，但崩溃/强杀就残留，且落在 `tempfile` 的公共目录里无人管辖。
    改为走 yt-dlp 的 `http_headers['Cookie']`：

    yt-dlp 在 `YoutubeDL.__init__` 里把它取出来（`YoutubeDL.py:749`）、随即 `pop` 掉，
    交给 `_load_cookies()` 装进 **cookiejar**，再由 `__extract_info` → `_apply_header_cookies(url)`
    按目标 URL 的域名 scope。`_load_cookies` 的 docstring 明说这是为堵
    GHSA-v8mc-9377-rwjj（cookie 被发给每个域）而做的。⇒ **域名绑定与 cookiefile 同一条路，
    但零落盘**，且不再需要按平台猜 domain（此前硬编码 `.bilibili.com` 之类）。

    ⚠️ 保留原有的过滤：跳过含非 ASCII 的值 —— 浏览器复制来的截断符 `…` 会让 yt-dlp
    的 latin-1 编码炸掉。⚠️ 输出必须是**纯 `k=v; k=v`**：`_load_cookies` 对带属性
    （`domain=`/`path=`）的输入会直接 `raise ValueError('Invalid syntax in Cookie Header')`。
    """
    if not cookie_str:
        return None
    pairs = []
    for pair in cookie_str.split(";"):
        pair = pair.strip()
        if "=" not in pair:
            continue
        k, _, v = pair.partition("=")
        try:
            v.encode("latin-1")
        except UnicodeEncodeError:
            continue
        pairs.append(f"{k}={v}")
    return "; ".join(pairs) if pairs else None


def _request_headers(url: str, cookie: str = "") -> dict:
    """给 yt-dlp 的请求头。**下载与探测两个消费方共用这一份**。

    抖音反爬严格，需要更真实的浏览器 UA（覆盖默认的 yt-dlp UA）。分成两份的
    失效形态是：收纳探测到的是「被拦下的页面标题」，而下载路径好得很 —— 两边
    都报成功，只是文档标题莫名其妙。
    """
    headers = dict(_HEADERS)
    if _host_in(url, DOUYIN_DOMAINS):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Referer": "https://www.douyin.com/",
        }
    cookie_hdr = _cookie_header(cookie)
    if cookie_hdr:
        headers["Cookie"] = cookie_hdr
    return headers


class VideoAdapter(PipelineAdapter):
    name = "video"
    cookies: str = ""
    _engine_config: dict | None = None  # injected by ingest route

    def accept(self, input: PipelineInput) -> bool:
        # 本地视频文件
        if input.type == InputType.FILE:
            ext = Path(input.file_path or "").suffix.lower()
            return ext in {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv"}
        # 视频 URL —— 按主机名判，不按子串（见 `_url_host` 的说明）
        if input.type == InputType.URL:
            return _is_video_url(input.url or "")
        return False

    def probe(self, input: PipelineInput) -> dict | None:
        """收纳模式下的元数据探测（「导入与转录分开」，2026-11-07）—— **不下载任何东西**。

        ⚠️ **这是可选钩子，不进 `PipelineAdapter` 协议**，写法照 `precheck` /
        `warmup` / `release` 的既有惯例（消费方 `getattr`）。其余五个 adapter 没有它
        也能收纳，只是标题走 `_build_title(inp)` 兜底 —— 「有没有 `probe`」**不是**
        能否只收纳的条件，档位表（`routers/ingest.py::_TIER_MEDIA`）才是。

        返回的键用 **`RawMetadata` 的字段名**（`cover_url`，不是 yt-dlp 的 `thumbnail`）：
        消费方 `_build_pending_output` 直接摊进 dataclass。

        ⚠️ 文件输入直接 `None`：本地视频没有平台元数据，用文件路径喂 yt-dlp 只会
        白花一次调用；标题走文件名兜底本来就更准。
        """
        if input.type != InputType.URL or not input.url:
            return None
        url = input.url
        info = self._probe_info(url, _request_headers(url, getattr(self, "cookies", "")),
                                self._get_proxy(url))
        if not info:
            return None
        return {
            "title": info.get("title") or "",
            # `duration` 可能是 None（直播 / 损坏的 info）：调用方 `or 0` 兜底，
            # 传 None 进 index 会变成 `null` 而前端 `DocEntry` 声明的是 `number`。
            "duration": info.get("duration") or 0,
            "cover_url": info.get("thumbnail") or "",
        }

    async def adapt(self, input: PipelineInput, on_progress=None,
                    engine_config: dict | None = None) -> AdapterOutput:
        # 使用传入的引擎配置（防并发竞态）
        if engine_config is not None:
            self._engine_config = dict(engine_config)

        # 本地视频文件 → 直接提取音频 + ASR，跳过字幕/下载
        if input.type == InputType.FILE:
            return await self._adapt_local(input, on_progress)

        url = input.url or ""
        warnings = []
        cookie = getattr(self, 'cookies', '')
        pg = lambda phase, status, message="", percent=None: (
            on_progress and on_progress(ProgressEvent(phase=phase, status=status, message=message, percent=percent))
        )

        # ── Step 0: 字幕优先（B站 / YouTube）──
        # 判据与 `accept()` 同源（同一个 `_host_in`）——分开写会得到「短链能进门、
        # 但进门后不认它是 B 站」这种只在 b23.tv 上复现的静默分叉。
        is_bilibili = _host_in(url, BILIBILI_SUBTITLE_DOMAINS)
        is_youtube = _host_in(url, YOUTUBE_DOMAINS)

        if is_bilibili:
            pg("fetching_subtitles", "running", "正在拉取B站字幕...", 10)
            try:
                from .bilibili_subtitle import fetch_subtitles
                sub = await asyncio.to_thread(fetch_subtitles, url, cookie)
                if sub and sub.get("segments"):
                    pg("fetching_subtitles", "done", f"字幕获取成功 ({len(sub['segments'])}段)", 30)
                    raw_text = json.dumps({
                        "segments": sub["segments"],
                        "language": sub.get("language", "zh"),
                        "full_text": sub.get("full_text", ""),
                        "source": "bilibili_subtitle",
                    }, ensure_ascii=False)
                    return AdapterOutput(
                        raw_text=raw_text,
                        metadata=RawMetadata(
                            language=sub.get("language", "zh"),
                            duration=sub["segments"][-1].get("end", 0) if sub["segments"] else 0,
                            subtitle_language=sub.get("language"),
                            source_title=sub.get("title", ""),
                            cover_url=sub.get("cover_url", ""),
                            uploader=sub.get("uploader", ""),
                            platform="bilibili",
                        ),
                        engine="bilibili_api",
                    )
            except Exception as e:
                warnings.append(f"B站字幕拉取失败: {e}")
                logger.debug("B站字幕拉取失败，回退下载: %s", e)

        if is_youtube:
            pg("fetching_subtitles", "running", "正在拉取YouTube字幕...", 10)
            try:
                from .youtube_subtitle import fetch_subtitles as fetch_yt_subtitles, get_video_info, _extract_video_id
                # 字幕 + 元数据（传入 cookie + proxy；to_thread 释放事件循环）
                yt_proxy = self._get_proxy(url)
                sub = await asyncio.to_thread(fetch_yt_subtitles, url, cookie, yt_proxy)
                # 获取视频元数据（用 oEmbed API，零成本，失败不阻塞）
                vid = _extract_video_id(url)
                info = {}
                if vid:
                    info = await asyncio.to_thread(get_video_info, vid, yt_proxy)
                if sub and sub.get("segments"):
                    pg("fetching_subtitles", "done", f"字幕获取成功 ({len(sub['segments'])}段)", 30)
                    raw_text = json.dumps({
                        "segments": sub["segments"],
                        "language": sub.get("language", "en"),
                        "full_text": sub.get("full_text", ""),
                        "source": "youtube_transcript_api",
                    }, ensure_ascii=False)
                    return AdapterOutput(
                        raw_text=raw_text,
                        metadata=RawMetadata(
                            language=sub.get("language", "en"),
                            duration=sub["segments"][-1].get("end", 0) if sub["segments"] else 0,
                            subtitle_language=sub.get("language"),
                            source_title=info.get("title", ""),
                            cover_url=info.get("thumbnail", ""),
                            uploader=info.get("uploader", ""),
                            platform="youtube",
                        ),
                        engine="youtube_transcript_api",
                    )
            except Exception as e:
                warnings.append(f"YouTube字幕拉取失败: {e}")
                logger.debug("YouTube字幕拉取失败，回退下载: %s", e)

        # ── Step 1: 下载 ──
        pg("downloading", "running", "下载音频中...", 20)
        video_info = {}
        try:
            video_path, video_info = await asyncio.to_thread(
                self._download, url, cookie, on_progress)
        except FatalError as e:
            logger.error("下载致命错误: %s", e)
            raise FatalError(f"视频下载失败: {str(e)[:100]}")
        except RetryableError as e:
            logger.error("下载失败（重试耗尽）: %s", e)
            raise FatalError(f"视频下载失败（重试耗尽）: {str(e)[:100]}")

        if not video_path:
            raise FatalError("视频下载失败：未获取到文件")

        pg("downloading", "done", "音频下载完成", 35)

        # ── Step 2: 提取音频（MP3 直接跳过）──
        if video_path.suffix.lower() in (".mp3", ".m4a", ".aac", ".opus"):
            audio_path = video_path
            logger.info("音频已是 %s 格式，跳过 ffmpeg 提取", video_path.suffix)
        else:
            pg("extracting_audio", "running", "提取音频...", 40)
            try:
                audio_path = await asyncio.to_thread(self._extract_audio, video_path)
                pg("extracting_audio", "done", "音频提取完成", 50)
            except Exception as e:
                raise FatalError(f"音频提取失败: {e}")

        # ── Step 3: ASR ──
        pg("transcribing", "running", "语音转写中...（可能需要几分钟）", 55)
        asr_result = await self._transcribe(audio_path, warnings)

        if not asr_result.get("segments"):
            # 汇总 ASR 失败原因
            fail_reasons = [w for w in warnings if "失败" in w or "不可用" in w]
            detail = "; ".join(fail_reasons) if fail_reasons else "所有语音识别引擎均失败"
            raise FatalError(f"语音转写失败: {detail}")

        pg("transcribing", "done", f"转写完成 ({len(asr_result.get('segments', []))}段)", 85)
        pg("done", "done", "导入完成", 100)

        # 清理
        for p in [video_path, audio_path]:
            if p and p.exists():
                try: p.unlink()
                except OSError: pass
        # 清理下载临时目录（_download 用 mkdtemp 创建，视频已消费 → 删空目录防残留）
        if video_path:
            try:
                _vdir = video_path.parent
                if _vdir.exists() and not list(_vdir.iterdir()):
                    _vdir.rmdir()
            except OSError:
                pass

        # 确定平台 —— 与 accept / 字幕分支共用同一批域名判据
        platform = ""
        if is_bilibili:
            platform = "bilibili"
        elif is_youtube:
            platform = "youtube"
        elif _host_in(url, DOUYIN_DOMAINS):
            platform = "douyin"

        return AdapterOutput(
            raw_text=json.dumps(asr_result, ensure_ascii=False),
            metadata=RawMetadata(
                warnings=warnings,
                source_title=video_info.get("title", ""),
                cover_url=video_info.get("thumbnail", ""),
                uploader=video_info.get("uploader", ""),
                platform=platform,
            ),
            engine=asr_result.get("engine_used", "asr"),
        )

    def _get_proxy(self, url: str = "") -> str:
        """从注入的配置中读取代理设置（ingest route 注入 > 环境变量），并按直连域名判据豁免。

        ⚠️ **调用必须把 url 传进来**：yt-dlp 的显式 `proxy=` **优先于 `NO_PROXY`**
        （源码 `clean_proxies` 原话 *"takes preference over everything, including
        NO_PROXY"*），环境变量那层豁免对它完全无效。只改 `net.py` 的现象是
        「SDK 走了直连、视频下载仍走代理」—— 用户看到的是 B 站导入照样失败。

        url 为空时 `proxy_for` 原样返回 proxy（判据全部落空），与改造前逐值等价。
        """
        proxy = getattr(self, 'proxy', '') or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or ""
        return proxy_for(url, proxy) or ""

    def _probe_info(self, url: str, headers: dict, proxy: str) -> dict | None:
        """`yt_dlp` 的快速元数据请求（`download=False`），失败返回 `None`。

        ⚠️ **两个消费方共用这一份**：`_download` 只读 `filesize`（决定网络读超时），
        `probe` 读标题 / 时长 / 封面。各写一份 `extract_info` 的失效形态是参数分叉 ——
        `format` 必须与正式下载一致，否则 `download=False` 默认返回**完整视频**大小
        （含视频轨），而 `bestaudio` 实际只下载音频轨（实测 391MB vs 1285MB）。
        分叉之后超时档位选错、前端那句「视频约 N MB」也说错，两边都不报错。
        """
        import yt_dlp
        probe_opts = {"quiet": True, "no_warnings": True, "noplaylist": True,
                      "format": "bestaudio[ext=m4a]/bestaudio/best",
                      "http_headers": headers}
        if proxy:
            probe_opts["proxy"] = proxy
        try:
            with yt_dlp.YoutubeDL(probe_opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception:
            return None
        return info if isinstance(info, dict) else None

    @retry(max_attempts=3, base_delay=2.0)
    def _download(self, url: str, cookie: str = "",
                  on_progress=None) -> tuple[Path | None, dict]:
        """下载视频/音频，返回 (文件路径, 视频元数据)。

        按视频大小适配网络读超时（视频越大容忍网络卡顿越久）+ 前端提示大小/预计耗时。
        """
        import yt_dlp
        tmp_dir = Path(tempfile.mkdtemp())

        # 请求头与 `probe()` 共用一份（抖音 UA / Cookie 的判据别写第二遍）
        headers = _request_headers(url, cookie)
        # 代理优先级：config.yaml > 环境变量；直连域名在这里豁免（yt-dlp 不吃 NO_PROXY）
        proxy = self._get_proxy(url)

        # ── 预取视频大小（快速元数据请求，不下载）→ 决定超时 + 前端提示 ──
        # 预取失败不阻塞，走默认超时
        info_p = self._probe_info(url, headers, proxy) or {}
        filesize = int(info_p.get("filesize") or info_p.get("filesize_approx") or 0)

        # 按大小分档网络读超时（越大容忍越久；下载持续有数据，此超时只在网络卡死时触发）
        size_mb = filesize / (1024 * 1024)
        if size_mb >= 500:
            socket_timeout = 300
        elif size_mb >= 200:
            socket_timeout = 180
        elif size_mb >= 50:
            socket_timeout = 120
        else:
            socket_timeout = 60

        # 前端提示：视频大小 + 预计耗时（参考实测下载 ~3MB/s）
        if on_progress and filesize > 0:
            est_min = max(1, round(size_mb / 3 / 60))
            on_progress(ProgressEvent(
                phase="downloading", status="running",
                message=f"视频约 {size_mb:.0f}MB，预计下载约 {est_min} 分钟", percent=15))

        ydl_opts = {
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "outtmpl": str(tmp_dir / "video.%(ext)s"),
            "noplaylist": True,
            "quiet": True, "no_warnings": True,
            # 对齐「导入最大 2GB」：200MB 会静默拒绝常见 B 站/YouTube 视频（390MB+），yt-dlp 超限只返回 info 不报错
            "max_filesize": 2 * 1024 * 1024 * 1024,
            "socket_timeout": socket_timeout,
            "extractor_retries": 1,
            "fragment_retries": 1,
            "retries": 1,
            "http_headers": headers,
        }
        if proxy:
            ydl_opts["proxy"] = proxy
            logger.info("下载代理: %s", proxy)

        info = {}
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                logger.info("yt-dlp 开始下载: %s", url[:60])
                info = ydl.extract_info(url, download=True)
                logger.info("yt-dlp 下载完成: %s", info.get("title", url[:40]))
            # 在临时目录中查找实际下载的文件（yt-dlp 可能改变扩展名）
            # 排除 .part/.ytdl 等下载临时文件；完成时这些后缀会被清理
            files = [p for p in tmp_dir.iterdir()
                     if p.is_file() and p.suffix not in (".part", ".ytdl")]
            path = files[0] if files else None
            # fallback: 用 info 中的 ext 构造
            if not path:
                ext = info.get("ext", "m4a")
                path = tmp_dir / f"video.{ext}"
            # 下载结果校验：文件必须真实存在且非空（失败显式化，不把不存在的路径抛给 ASR）
            if not path.exists() or path.stat().st_size == 0:
                raise FatalError(
                    "视频下载失败：文件未生成（可能被大小限制拦截或网络中断），请重试")
            return path, info
        except Exception as e:
            msg = str(e).lower()
            if any(kw in msg for kw in ("412", "timeout", "connection", "5xx", "503", "502")):
                raise RetryableError(f"yt-dlp 下载失败: {e}") from e
            raise FatalError(f"yt-dlp 下载失败: {e}") from e

    def _extract_audio(self, video_path: Path) -> Path:
        """用 ffmpeg 提取/转换为 MP3（比 WAV 小 ~10×，云端 ASR 均支持）"""
        audio_path = video_path.with_suffix(".mp3")
        subprocess.run([
            "ffmpeg", "-y", "-i", str(video_path),
            "-vn", "-acodec", "libmp3lame", "-b:a", "64k",
            "-ar", "16000", "-ac", "1",
            str(audio_path),
        ], check=True, capture_output=True, timeout=300,
           creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        return audio_path

    def _resolve_asr_engine(self, cfg: dict | None = None):
        """根据 engine_bindings.asr 解析第一个可用引擎（不再自动降级）。

        返回 `(注册名, 引擎, model_name, provider_id)`；provider_id 供
        `apply_provider_credentials` 取凭据。"""
        cfg = cfg or self._engine_config or {}
        bindings = cfg.get("engine_bindings", {})
        engine_models = cfg.get("engine_models", [])

        model_ids = bindings.get("asr", [])
        # 向后兼容：旧格式 int → [int]
        if isinstance(model_ids, int):
            model_ids = [model_ids]

        # 取有效绑定
        emap = {m["id"]: m for m in engine_models}
        for mid in model_ids:
            bm = emap.get(mid)
            if not bm:
                continue
            provider_id = bm["provider_id"]
            model_name = bm["model_name"]

            if provider_id == "dashscope":
                eng_name = "dashscope-asr"
            elif provider_id == "bcut":
                eng_name = "bcut-asr"
            else:
                eng_name = model_name
            engine = EngineRegistry.get("asr", eng_name)
            if engine:
                return eng_name, engine, model_name, provider_id

        # 所有绑定均不可用 → 不降级，返回空（调用方报错）
        logger.error("ASR 绑定链为空或全部失败，请在设置中更换 ASR 引擎")
        return "", None, "", ""

    async def _transcribe(self, audio_path: Path, warnings: list) -> dict:
        eng_name, engine, model_name, provider_id = self._resolve_asr_engine()
        if not engine:
            warnings.append("ASR 引擎未注册: " + eng_name)
            return {"segments": [], "language": "unknown"}

        # 凭据 + 模型名都注入到**副本**上（`apply_provider_credentials` 返回 copy）。
        # ⚠️ 此前是直接写注册表里的单例，且带 `not engine.api_key` 前置判断 ——
        #    第一次注入之后用户换 Key 不再生效，要重启才认，期间每次转写 401。
        engine = apply_provider_credentials(engine, self._engine_config or {}, provider_id)
        if hasattr(engine, '_model_name'):
            engine._model_name = model_name

        # 单引擎执行（同步引擎放入 asyncio.to_thread 避免阻塞事件循环）
        # 这样客户端断连检测 _watch_disconnect 可以正常运行并 set cancel_event，
        # 后台线程中的引擎在逐段检查 cancel_event 后立即停止 CPU 消耗。
        # 不再有多引擎尝试/降级链：绑定只取第一个，失败直接报错。
        cancel_event = (self._engine_config or {}).get("cancel_event")
        if cancel_event and cancel_event.is_set():
            raise FatalError("操作已取消")
        logger.info("ASR: 尝试 %s (model=%s)", eng_name, model_name)
        try:
            result = await asyncio.to_thread(engine.run, str(audio_path), cancel_event=cancel_event)
            if result and result.segments:
                logger.info("ASR: %s 成功 (%d段, %s)", eng_name, len(result.segments), result.language)
                return {
                    "segments": [
                        {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text}
                        for s in result.segments
                    ],
                    "language": result.language,
                    "duration": result.duration,
                    "full_text": result.full_text,
                    "engine_used": eng_name,
                }
            warnings.append(f"ASR {eng_name} 返回空结果")
        except FatalError:
            raise  # 取消或其他致命错误，直接抛出
        except Exception as e:
            warnings.append(f"ASR {eng_name} 失败: {e}")
            logger.warning("ASR %s 失败: %s", eng_name, str(e)[:120])

        warnings.insert(0, f"ASR 引擎失败: {eng_name}")
        return {"segments": [], "language": "unknown"}

    async def _adapt_local(self, input: PipelineInput, on_progress=None) -> AdapterOutput:
        """本地视频文件 → 提取音频 → ASR"""
        warnings = []
        pg = lambda phase, status, message="", percent=None: (
            on_progress and on_progress(ProgressEvent(phase=phase, status=status, message=message, percent=percent))
        )

        video_path = Path(input.file_path or "")
        pg("extracting_audio", "running", "提取音频...", 30)

        # 已是音频格式 → 直接 ASR
        ext = video_path.suffix.lower()
        if ext in (".mp3", ".m4a", ".aac", ".opus", ".wav"):
            audio_path = video_path
            logger.info("本地音频: %s，跳过 ffmpeg", video_path.name)
        else:
            try:
                audio_path = await asyncio.to_thread(self._extract_audio, video_path)
            except Exception as e:
                pg("extracting_audio", "error", f"音频提取失败: {e}", 50)
                raise FatalError(f"音频提取失败: {e}")

        pg("extracting_audio", "done", "音频提取完成", 50)
        pg("transcribing", "running", "语音转写中...（可能需要几分钟）", 60)

        asr_result = await self._transcribe(audio_path, warnings)

        if not asr_result.get("segments"):
            pg("transcribing", "error", "转写失败", 90)
            # 汇总 ASR 失败原因（对齐 URL 路径）：warnings 里带「失败/不可用」的原因
            # （如「ASR dashscope-asr 失败: DashScope API key 未配置」），让用户看到具体原因
            fail_reasons = [w for w in warnings if "失败" in w or "不可用" in w]
            detail = "; ".join(fail_reasons) if fail_reasons else "所有语音识别引擎均失败"
            raise FatalError(f"语音转写失败: {detail}")

        pg("transcribing", "done", f"转写完成 ({len(asr_result.get('segments', []))}段)", 95)
        pg("done", "done", "导入完成", 100)

        # 清理临时音频（原始视频文件不删）
        if audio_path != video_path and audio_path.exists():
            try:
                audio_path.unlink()
            except OSError:
                pass

        return AdapterOutput(
            raw_text=json.dumps(asr_result, ensure_ascii=False),
            metadata=RawMetadata(
                language=asr_result.get("language", "unknown"),
                duration=asr_result.get("duration"),
                warnings=warnings,
            ),
            engine=asr_result.get("engine_used", "asr"),
        )
