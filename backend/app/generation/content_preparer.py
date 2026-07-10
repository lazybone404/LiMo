"""
ContentPreparer — 生成前的内容准备：VL 管线 + 图片剥离/优化 + 文本截断

把 Generator._prepare_generation 和相关模块函数抽取为独立类，
单一职责：输入 rawText → 输出干净的 (text, image_urls, video_path)。
"""

import logging
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MAX_TOKENS = 65536
_PROMPT_OVERHEAD = 2000

# 各厂商默认上下文窗口（token）
_DEFAULT_CONTEXT = {
    "deepseek": 1_000_000,
    "openai": 128_000,
    "anthropic": 200_000,
    "dashscope": 1_000_000,
}

try:
    import tiktoken
    _TIK_ENCODER = tiktoken.get_encoding("cl100k_base")
    def _estimate_tokens(text: str) -> int:
        return len(_TIK_ENCODER.encode(text))
except ImportError:
    def _estimate_tokens(text: str) -> int:
        return len(text) // 2


def _optimize_image_data(data_uri: str, max_dim: int = 1024, quality: int = 80) -> str:
    """PIL 缩放 data URI 图片，长边 ≤ max_dim，输出 JPEG data URI。"""
    import base64
    from PIL import Image
    import io as _io

    header, b64 = data_uri.split(",", 1)
    img_bytes = base64.b64decode(b64)
    img = Image.open(_io.BytesIO(img_bytes))

    w, h = img.size
    if max(w, h) <= max_dim:
        return data_uri

    ratio = max_dim / max(w, h)
    new_w, new_h = int(w * ratio), int(h * ratio)
    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")
    img = img.resize((new_w, new_h), Image.LANCZOS)

    buf = _io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    new_b64 = base64.b64encode(buf.getvalue()).decode()
    logger.debug("图片预处理: %dx%d → %dx%d (%.1fKB → %.1fKB)", w, h, new_w, new_h,
                 len(img_bytes) / 1024, buf.tell() / 1024)
    return f"data:image/jpeg;base64,{new_b64}"


class ContentPreparer:
    """生成前的内容准备器。"""

    def __init__(self, store_root: str = "", llm_config: dict | None = None):
        self.store_root = store_root
        self.llm_config = llm_config or {}

    # ── 公开接口 ──

    async def prepare(
        self,
        raw_text: str,
        vl_config: dict | None = None,
        keep_refs: bool = False,
        send_to_llm: bool = False,
        optimize_images: bool = False,
    ) -> tuple[str, list[str] | None, str | None]:
        """准备生成上下文：VL管线 → 剥离转录图片 → 截断 → 合并。

        返回 (clean_text, image_urls, video_path)。
        """
        import asyncio
        loop = asyncio.get_event_loop()

        # --- VL 管线 ---
        vl_image_urls = None
        video_path = None
        if vl_config:
            logger.info("VL config: interval=%ds grid=%dx%d",
                        vl_config.get("video_interval", 6),
                        vl_config.get("grid_cols", 2),
                        vl_config.get("grid_rows", 2))
            source_url = vl_config.get("source_url", "")
            if source_url:
                vl_image_urls, video_path = await loop.run_in_executor(
                    None, self._run_vl_pipeline, vl_config, source_url)

        # --- 剥离转录图片（线程池执行，避免读文件+编码阻塞事件循环）---
        clean_text, doc_image_urls = await loop.run_in_executor(
            None, self._extract_and_strip_data_uris,
            raw_text, self.store_root, keep_refs, send_to_llm, optimize_images)

        # --- 截断过长文本 ---
        clean_text, _ = self._truncate_text(
            clean_text,
            self.llm_config.get("provider", ""),
            self.llm_config.get("context_limits"),
            self.llm_config.get("model_context_window"),
        )

        # --- 合并 VL 截图 + 文档图片 ---
        merged_urls = (vl_image_urls or []) + (doc_image_urls or [])
        has_image = any("image" in (c or "") for c in self.llm_config.get("capabilities", []))
        image_urls = merged_urls if (merged_urls and has_image) else None

        return clean_text, image_urls, video_path

    # ── VL 管线 ──

    @staticmethod
    def _download_video(url: str, cookies: str = "") -> str | None:
        """使用 yt-dlp 下载视频文件，返回本地路径"""
        import yt_dlp
        tmp_dir = tempfile.mkdtemp(prefix="vl_video_")
        ydl_opts = {
            "format": "bestvideo[ext=mp4]+bestaudio/best",
            "outtmpl": str(Path(tmp_dir) / "video.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "max_filesize": 500 * 1024 * 1024,
            "socket_timeout": 120,
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.bilibili.com",
                "Origin": "https://www.bilibili.com",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        }
        if cookies:
            try:
                from app.adapters.video import _cookie_to_file
                cf = _cookie_to_file(cookies.strip().strip("'\"").strip())
                if cf:
                    ydl_opts["cookiefile"] = cf
                    logger.info("VL: 注入 Cookie (%s)", cf)
            except Exception as e:
                logger.warning("VL Cookie 注入失败: %s", e)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.extract_info(url, download=True)
            files = list(Path(tmp_dir).iterdir())
            return str(files[0]) if files else None
        except Exception as e:
            logger.warning("VL 视频下载失败: %s", e)
            return None

    @staticmethod
    def _run_vl_pipeline(vl_config: dict, source_url: str) -> tuple[list[str] | None, str | None]:
        """执行 VL 管线：下载视频 → 抽帧拼网格 → base64 编码。返回 (image_urls, video_path)"""
        logger.info("VL 管线启动: url=%s interval=%ds grid=%dx%d",
                    source_url[:60],
                    vl_config.get("video_interval", 6),
                    vl_config.get("grid_cols", 2),
                    vl_config.get("grid_rows", 2))

        cookies = vl_config.get("cookies", "")
        video_path = ContentPreparer._download_video(source_url, cookies)
        if not video_path:
            logger.warning("VL: 视频下载失败，回退纯文本生成")
            return None, None

        try:
            from app.utils.video_reader import VideoReader
            reader = VideoReader(
                video_path=video_path,
                grid_size=(vl_config.get("grid_cols", 2), vl_config.get("grid_rows", 2)),
                frame_interval=vl_config.get("video_interval", 6),
                dedupe_enabled=vl_config.get("dedupe_enabled", True),
                unit_width=vl_config.get("unit_width", 960),
                unit_height=vl_config.get("unit_height", 540),
                save_quality=vl_config.get("save_quality", 90),
                quality_threshold=vl_config.get("quality_threshold", 0.0),
            )
            image_urls = reader.run()
            logger.info("VL: 生成 %d 张网格图", len(image_urls))
            return image_urls, video_path
        except Exception as e:
            logger.warning("VL: VideoReader 失败: %s，回退纯文本生成", e)
            return None, video_path

    # ── 图片剥离 ──

    @staticmethod
    def _extract_and_strip_data_uris(
        raw_text: str, store_root: str = "",
        keep_refs: bool = False,
        send_to_llm: bool = False,
        optimize_images: bool = False,
    ) -> tuple[str, list[str] | None]:
        """从 rawText 中剥离图片引用。两个独立开关：keep_refs / send_to_llm。
        返回 (clean_text, image_urls)。"""
        import re

        image_urls: list[str] = []
        data_uri_pat = r'!\[([^\]]*)\]\((data:image/[^;]+;base64,[^)]+)\)'
        file_ref_pat = r'!\[([^\]]*)\]\((/api/files/([a-zA-Z0-9_\-]+)/([a-zA-Z0-9_\-.]+))\)'

        data_uri_re = re.compile(data_uri_pat)
        file_ref_re = re.compile(file_ref_pat)

        def _replacer(m):
            alt = m.group(1)
            if m.re is data_uri_re:
                uri = m.group(2)
                if send_to_llm:
                    image_urls.append(uri)
                return f'![{alt}]'
            elif store_root:
                full_path = m.group(2)
                tx_id = m.group(3)
                fname = m.group(4)
                file_path = Path(store_root) / "files" / tx_id / fname
                if file_path.exists():
                    if send_to_llm:
                        import base64
                        ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else "png"
                        mime = f"image/{ext}"
                        b64 = base64.b64encode(file_path.read_bytes()).decode()
                        data_uri = f"data:{mime};base64,{b64}"
                        if optimize_images:
                            data_uri = _optimize_image_data(data_uri)
                        image_urls.append(data_uri)
                    if keep_refs:
                        return f'![{alt}]({full_path})'
                    else:
                        return f'![{alt}]'
            return m.group(0)

        clean_text = data_uri_re.sub(_replacer, raw_text)
        clean_text = file_ref_re.sub(_replacer, clean_text)

        stripped = len(image_urls)
        if stripped:
            base64_chars = sum(len(u) for u in image_urls)
            logger.info("剥离 %d 个图片（%.1fK chars）%s",
                        stripped, base64_chars / 1000,
                        "，文件引用已保留" if keep_refs else "")
        return clean_text, image_urls if image_urls else None

    # ── 文本截断 ──

    @staticmethod
    def _truncate_text(
        text: str, provider: str,
        context_limits: dict | None = None,
        model_context_window: int | None = None,
    ) -> tuple[str, bool]:
        """如果文本超过模型上下文窗口 80%，截断并返回 (truncated_text, was_truncated)。"""
        limits = {**_DEFAULT_CONTEXT, **(context_limits or {})}
        context = model_context_window or limits.get(provider, 128_000)

        available = int(context * 0.8) - _PROMPT_OVERHEAD - MAX_TOKENS
        if available <= 0:
            available = context - MAX_TOKENS - _PROMPT_OVERHEAD

        tokens = _estimate_tokens(text)
        if tokens <= available:
            return text, False

        ratio = available / tokens
        cut = max(1, int(len(text) * ratio * 0.95))
        truncated = text[:cut] + f"\n\n[内容过长，已截断：{tokens} tokens → 约 {available} tokens]"
        logger.warning("文本截断: %d tokens → ~%d tokens (context=%d, provider=%s)",
                       tokens, available, context, provider)
        return truncated, True
