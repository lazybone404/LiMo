"""
视频适配器 — URL → 字幕直拉优先 → 无字幕则下载+ASR
参照 BiliNote pipeline.py: pipe_subtitle → pipe_download → pipe_transcribe
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from app.core.types import PipelineInput, AdapterOutput, RawMetadata, InputType, ProgressEvent
from app.core.protocols import PipelineAdapter
from app.core.registry import EngineRegistry
from app.core.errors import RetryableError, FatalError, retry

logger = logging.getLogger(__name__)

VIDEO_DOMAINS = {"youtube.com", "youtu.be", "bilibili.com", "douyin.com", "tiktok.com"}

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com",
    "Origin": "https://www.bilibili.com",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _extract_bvid(url: str) -> str | None:
    if "b23.tv" in url.lower():
        try:
            import requests as req
            url = req.head(url, allow_redirects=True, timeout=10, headers={"User-Agent": _HEADERS["User-Agent"]}).url
        except Exception:
            logger.warning("Failed to follow b23.tv redirect for BVID extraction", exc_info=True)
    m = re.search(r"BV([0-9A-Za-z]+)", url)
    return f"BV{m.group(1)}" if m else None


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


class VideoAdapter(PipelineAdapter):
    name = "video"
    cookies: str = ""
    _engine_config: dict | None = None  # injected by ingest route

    def accept(self, input: PipelineInput) -> bool:
        # 本地视频文件
        if input.type == InputType.FILE:
            ext = Path(input.file_path or "").suffix.lower()
            return ext in {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv"}
        # 视频 URL
        if input.type == InputType.URL:
            url = input.url or ""
            return any(d in url for d in VIDEO_DOMAINS)
        return False

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
        is_bilibili = "bilibili.com" in url
        is_youtube = "youtube.com" in url or "youtu.be" in url
        is_douyin = "douyin.com" in url

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
                yt_proxy = self._get_proxy()
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

        # ── 抖音 API 优先路径（直接获取音频 URL，无需下载视频）──
        if is_douyin:
            pg("downloading", "running", "正在提取抖音音频...", 10)
            try:
                from app.downloaders.douyin_downloader import download_audio as dy_download
                dy_result = await asyncio.to_thread(dy_download, url, cookie)
                audio_path = Path(dy_result["file_path"])
                if audio_path.exists() and audio_path.stat().st_size > 0:
                    pg("downloading", "done", "音频提取完成", 50)
                    pg("transcribing", "running", "语音转写中...（可能需要几分钟）", 55)
                    asr_result = await self._transcribe(audio_path, warnings)
                    if asr_result.get("segments"):
                        pg("transcribing", "done",
                           f"转写完成 ({len(asr_result.get('segments', []))}段)", 85)
                        pg("done", "done", "导入完成", 100)
                        # 清理临时音频
                        try: audio_path.unlink()
                        except OSError: pass
                        return AdapterOutput(
                            raw_text=json.dumps(asr_result, ensure_ascii=False),
                            metadata=RawMetadata(
                                language=asr_result.get("language", "zh"),
                                duration=dy_result.get("duration", 0),
                                source_title=dy_result.get("title", ""),
                                cover_url=dy_result.get("cover_url", ""),
                                platform="douyin",
                                warnings=warnings,
                            ),
                            engine=asr_result.get("engine_used", "asr"),
                        )
                    pg("transcribing", "error", "转写失败", 80)
                else:
                    pg("downloading", "error", "音频文件为空", 50)
            except Exception as e:
                warnings.append(f"抖音API提取失败: {e}")
                logger.debug("抖音API提取失败，回退 yt-dlp: %s", e)

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

        # 确定平台
        platform = ""
        if "bilibili" in url:
            platform = "bilibili"
        elif "youtube" in url or "youtu.be" in url:
            platform = "youtube"
        elif "douyin" in url:
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

    def _get_proxy(self) -> str:
        """从注入的配置中读取代理设置（ingest route 注入 > 环境变量）"""
        proxy = getattr(self, 'proxy', '') or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or ""
        return proxy

    @retry(max_attempts=3, base_delay=2.0)
    def _download(self, url: str, cookie: str = "",
                  on_progress=None) -> tuple[Path | None, dict]:
        """下载视频/音频，返回 (文件路径, 视频元数据)。

        按视频大小适配网络读超时（视频越大容忍网络卡顿越久）+ 前端提示大小/预计耗时。
        """
        import yt_dlp
        tmp_dir = Path(tempfile.mkdtemp())

        # 抖音反爬严格，需要更真实的浏览器 UA（覆盖默认的 yt-dlp UA）
        headers = dict(_HEADERS)
        if "douyin" in url:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Referer": "https://www.douyin.com/",
            }
        cookie_hdr = _cookie_header(cookie)
        if cookie_hdr:
            headers["Cookie"] = cookie_hdr
        # 代理优先级：config.yaml > 环境变量
        proxy = self._get_proxy()

        # ── 预取视频大小（快速元数据请求，不下载）→ 决定超时 + 前端提示 ──
        filesize = 0
        try:
            # format 必须与正式下载一致——否则 download=False 默认返回完整视频大小（含视频轨），
            # bestaudio 实际只下载音频轨（如 391MB vs 1285MB）
            probe_opts = {"quiet": True, "no_warnings": True, "noplaylist": True,
                          "format": "bestaudio[ext=m4a]/bestaudio/best",
                          "http_headers": headers}
            if proxy:
                probe_opts["proxy"] = proxy
            with yt_dlp.YoutubeDL(probe_opts) as ydl:
                info_p = ydl.extract_info(url, download=False)
            filesize = int(info_p.get("filesize") or info_p.get("filesize_approx") or 0)
        except Exception:
            pass  # 预取失败不阻塞，走默认超时

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

    def _get_dashscope_key(self) -> str:
        """从注入的配置中读取 DashScope API Key"""
        cfg = self._engine_config or {}
        for ep in cfg.get("engine_providers", []):
            if ep.get("id") == "dashscope":
                return ep.get("api_key", "")
        return ""

    def _resolve_asr_engine(self, cfg: dict | None = None):
        """根据 engine_bindings.asr 解析第一个可用引擎（不再自动降级）"""
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
                return eng_name, engine, model_name

        # 所有绑定均不可用 → 不降级，返回空（调用方报错）
        logger.error("ASR 绑定链为空或全部失败，请在设置中更换 ASR 引擎")
        return "", None, ""

    async def _transcribe(self, audio_path: Path, warnings: list) -> dict:
        eng_name, engine, model_name = self._resolve_asr_engine()
        if not engine:
            warnings.append("ASR 引擎未注册: " + eng_name)
            return {"segments": [], "language": "unknown"}

        # 动态注入模型名（SDK async_call 据此路由 paraformer / fun-asr）
        if hasattr(engine, '_model_name'):
            engine._model_name = model_name

        # 动态注入最新 API Key（引擎 init 时 key 可能为空，设置页保存后才填）
        if eng_name == "dashscope-asr" and hasattr(engine, 'api_key') and not engine.api_key:
            dashscope_key = self._get_dashscope_key()
            if dashscope_key:
                engine.api_key = dashscope_key
                logger.info("ASR: 动态注入 DashScope API Key")

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
