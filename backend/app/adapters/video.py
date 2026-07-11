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


def _cookie_to_file(cookie_str: str, domain: str = ".bilibili.com") -> str | None:
    """Cookie 字符串 → Netscape 临时文件。参照 BiliNote _cookie_to_file。"""
    if not cookie_str:
        return None
    lines = ["# Netscape HTTP Cookie File\n"]
    for pair in cookie_str.split(";"):
        pair = pair.strip()
        if "=" in pair:
            k, _, v = pair.partition("=")
            # 跳过含非 ASCII 字符的值（浏览器截断符 `…` 等会导致 yt-dlp latin-1 错误）
            try:
                v.encode("latin-1")
            except UnicodeEncodeError:
                continue
            lines.append(f"{domain}\tTRUE\t/\tFALSE\t0\t{k}\t{v}\n")
    if len(lines) <= 1:
        return None
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
    tmp.writelines(lines)
    tmp.close()
    logger.info("Cookie 写入临时文件 (%d 条, domain=%s)", len(lines) - 1, domain)
    return tmp.name


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
                sub = fetch_subtitles(url, cookie)
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
                if not (self._engine_config or {}).get("engine_auto_fallback", True):
                    raise FatalError(f"B站字幕拉取失败（自动降级已关闭）: {e}") from e
                logger.warning("B站字幕拉取失败，回退下载: %s", e)

        if is_youtube:
            pg("fetching_subtitles", "running", "正在拉取YouTube字幕...", 10)
            try:
                from .youtube_subtitle import fetch_subtitles as fetch_yt_subtitles, get_video_info, _extract_video_id
                # 字幕 + 元数据（传入 cookie + proxy）
                yt_proxy = self._get_proxy()
                sub = fetch_yt_subtitles(url, cookie, yt_proxy)
                # 获取视频元数据（用 oEmbed API，零成本，失败不阻塞）
                vid = _extract_video_id(url)
                info = {}
                if vid:
                    info = get_video_info(vid, yt_proxy)
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
                if not (self._engine_config or {}).get("engine_auto_fallback", True):
                    raise FatalError(f"YouTube字幕拉取失败（自动降级已关闭）: {e}") from e
                logger.warning("YouTube字幕拉取失败，回退下载: %s", e)

        # ── 抖音 API 优先路径（直接获取音频 URL，无需下载视频）──
        if is_douyin:
            pg("downloading", "running", "正在提取抖音音频...", 10)
            try:
                from app.downloaders.douyin_downloader import download_audio as dy_download
                dy_result = dy_download(url, cookie)
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
                if not (self._engine_config or {}).get("engine_auto_fallback", True):
                    raise FatalError(f"抖音API提取失败（自动降级已关闭）: {e}") from e
                logger.warning("抖音API提取失败，回退 yt-dlp: %s", e)

        # ── Step 1: 下载 ──
        pg("downloading", "running", "下载音频中...", 20)
        video_info = {}
        # 确定 Cookie 域名
        if is_youtube:
            cookie_domain = ".youtube.com"
        elif is_douyin:
            cookie_domain = ".douyin.com"
        else:
            cookie_domain = ".bilibili.com"
        try:
            video_path, video_info = self._download(url, cookie, cookie_domain)
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
                audio_path = self._extract_audio(video_path)
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
    def _download(self, url: str, cookie: str = "", cookie_domain: str = ".bilibili.com") -> tuple[Path | None, dict]:
        """下载视频/音频，返回 (文件路径, 视频元数据)"""
        import yt_dlp
        tmp_dir = Path(tempfile.mkdtemp())
        ydl_opts = {
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "outtmpl": str(tmp_dir / "video.%(ext)s"),
            "noplaylist": True,
            "quiet": True, "no_warnings": True,
            "max_filesize": 200 * 1024 * 1024,
            "socket_timeout": 60,
            "extractor_retries": 1,
            "fragment_retries": 1,
            "retries": 1,
            "http_headers": _HEADERS,
        }
        # 抖音反爬严格，需要更真实的浏览器 UA（覆盖默认的 yt-dlp UA）
        if "douyin" in url:
            ydl_opts["http_headers"] = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Referer": "https://www.douyin.com/",
            }
        cf = _cookie_to_file(cookie, cookie_domain) if cookie else None
        if cf:
            ydl_opts["cookiefile"] = cf
        # 代理优先级：config.yaml > 环境变量
        proxy = self._get_proxy()
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
            files = list(tmp_dir.iterdir())
            path = files[0] if files else None
            # fallback: 用 info 中的 ext 构造
            if not path:
                ext = info.get("ext", "m4a")
                path = tmp_dir / f"video.{ext}"
            return path, info
        except Exception as e:
            msg = str(e).lower()
            if any(kw in msg for kw in ("412", "timeout", "connection", "5xx", "503", "502")):
                raise RetryableError(f"yt-dlp 下载失败: {e}") from e
            raise FatalError(f"yt-dlp 下载失败: {e}") from e
        finally:
            if cf and os.path.exists(cf):
                try:
                    os.unlink(cf)
                except OSError:
                    pass

    def _extract_audio(self, video_path: Path) -> Path:
        """用 ffmpeg 提取/转换为 MP3（比 WAV 小 ~10×，DashScope / faster-whisper 均支持）"""
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
        """根据 engine_bindings.asr 解析优先级链，返回第一个可用引擎"""
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
            elif provider_id == "faster-whisper":
                eng_name = f"faster-whisper-{model_name}" if model_name in ("small", "medium", "large-v3") else "faster-whisper-small"
            elif provider_id == "bcut":
                eng_name = "bcut-asr"
            else:
                eng_name = model_name
            engine = EngineRegistry.get("asr", eng_name)
            if engine:
                return eng_name, engine, model_name

        # 所有绑定均不可用 → 检查是否允许自动降级
        auto_fallback = cfg.get("engine_auto_fallback", True)
        if not auto_fallback:
            logger.error("ASR 绑定链为空或全部失败，自动降级已关闭")
            return "", None, ""

        # 尝试任意已注册的 ASR 引擎
        fallback_engines = EngineRegistry.list_category("asr")
        for eng_name in fallback_engines:
            engine = EngineRegistry.get("asr", eng_name)
            if engine:
                default_model = "paraformer-v2" if "dashscope" in eng_name else "small"
                logger.warning("ASR 绑定链为空或全部失败，降级使用: %s (model=%s)", eng_name, default_model)
                return eng_name, engine, default_model

        logger.error("无可用 ASR 引擎")
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

        # 带降级链的执行（同步引擎放入 asyncio.to_thread 避免阻塞事件循环）
        # 这样客户端断连检测 _watch_disconnect 可以正常运行并 set cancel_event，
        # 后台线程中的引擎在逐段检查 cancel_event 后立即停止 CPU 消耗。
        cancel_event = (self._engine_config or {}).get("cancel_event")
        tried = []
        current = engine
        current_name = eng_name
        while current is not None:
            if cancel_event and cancel_event.is_set():
                raise FatalError("操作已取消")
            logger.info("ASR: 尝试 %s (model=%s)", current_name, model_name)
            try:
                result = await asyncio.to_thread(current.run, str(audio_path), cancel_event=cancel_event)
                if result and result.segments:
                    logger.info("ASR: %s 成功 (%d段, %s)", current_name, len(result.segments), result.language)
                    return {
                        "segments": [
                            {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text}
                            for s in result.segments
                        ],
                        "language": result.language,
                        "duration": result.duration,
                        "full_text": result.full_text,
                        "engine_used": current_name,
                    }
                warnings.append(f"ASR {current_name} 返回空结果")
            except FatalError:
                raise  # 取消或其他致命错误，不降级，直接抛出
            except Exception as e:
                warnings.append(f"ASR {current_name} 失败: {e}")
                logger.warning("ASR %s 失败: %s", current_name, str(e)[:120])

            tried.append(current_name)
            # 走降级链
            fallback = getattr(current, 'fallback', None)
            if fallback:
                current_name = getattr(fallback, 'name', 'unknown')
                current = fallback
            else:
                break

        warnings.insert(0, f"所有 ASR 引擎失败 ({', '.join(tried)})")
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
                audio_path = self._extract_audio(video_path)
            except Exception as e:
                pg("extracting_audio", "error", f"音频提取失败: {e}", 50)
                raise FatalError(f"音频提取失败: {e}")

        pg("extracting_audio", "done", "音频提取完成", 50)
        pg("transcribing", "running", "语音转写中...（可能需要几分钟）", 60)

        asr_result = await self._transcribe(audio_path, warnings)

        if not asr_result.get("segments"):
            pg("transcribing", "error", "转写失败", 90)
            raise FatalError(f"语音转写失败：未获取到转写结果")

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
