"""
ContentPreparer — 生成前的内容准备：VL 管线 + 图片剥离/优化 + 文本截断

把 Generator._prepare_generation 和相关模块函数抽取为独立类，
单一职责：输入 rawText → 输出干净的 (text, image_urls, video_path)。
"""

import logging
import tempfile
from pathlib import Path
from typing import Optional

from app.core.model_caps import has_capability
from app.core.token import estimate_tokens as _estimate_tokens, DEFAULT_CONTEXT as _DEFAULT_CONTEXT
# 文档直传（PDF file 块）：模块级导入——doc_understand 只依赖 app.core.logging，
# 无循环导入风险（模式 #26：函数内导入是打破循环导入的手段，不是风格偏好）
from app.services.doc_understand import build_pdf_document
from app.generation.budget import input_budget_for

logger = logging.getLogger(__name__)

# 预算常量已统一到 app.generation.budget（原先在此定义的 MAX_TOKENS=16384 /
# _PROMPT_OVERHEAD=2000 已移除）。⚠️ 不要在这里重新引入本地常量——创建侧与生成侧
# 必须共用同一份预算函数，否则小窗口模型上会二次截断、后几篇凭空消失（设计 §4.4.5）。


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


def _sanitize_image_uri(uri: str, optimize: bool) -> str | None:
    """校验 data URI 图片（PIL 可识别）；optimize 时缩放。坏图返回 None（调用方跳过）。

    2026-08-04：修复 PIL.UnidentifiedImageError 导致整个生成流崩溃——
    损坏/截断的图片 data URI 跳过而非中断（失败显式化的边界：单图损坏不影响整体生成）。
    """
    import base64
    import io as _io
    from PIL import Image
    try:
        _header, b64 = uri.split(",", 1)
        img_bytes = base64.b64decode(b64)
        with Image.open(_io.BytesIO(img_bytes)) as img:
            if not optimize:
                img.verify()  # 不优化也校验完整性
        if optimize:
            return _optimize_image_data(uri)
        return uri
    except Exception:
        logger.warning("跳过无法识别的图片（data URI 损坏），不影响生成")
        return None


class ContentPreparer:
    """生成前的内容准备器。"""

    def __init__(self, store_root: str = "", llm_config: dict | None = None,
                 doc_dir: str = "", budget_profile: str = "single"):
        self.store_root = store_root
        self.doc_dir = doc_dir
        self.llm_config = llm_config or {}
        # "single" = 普通单文档（与既有行为逐值等价）
        # "multi"  = 合成文档（等式闭合，见 budget.multi_input_budget）
        self.budget_profile = budget_profile

    # ── 公开接口 ──

    # 文档直传模式下 source_content 节的正文：正文不进 prompt（只有原件进），
    # 但 prompt 里必须有东西告诉模型「材料在哪」，否则它无从下手。
    _DOC_DIRECT_PLACEHOLDER = "（待分析材料以 PDF 原件随本消息附上，请直接阅读附件内容。）"

    async def prepare(
        self,
        raw_text: str,
        vl_config: dict | None = None,
        keep_refs: bool = False,
        send_to_llm: bool = False,
        optimize_images: bool = False,
        video_direct: bool = False,
        doc_direct: bool = False,
        doc_path: str = "",
    ) -> tuple[str, list[str] | None, str | None, dict | None]:
        """准备生成上下文：VL管线 → 剥离转录图片 → 文档直传 → 截断 → 合并。

        返回 (clean_text, image_urls, video_path, document)。
        video_direct：视频直传模式（DashScope SDK 视频理解 → 描述文本注入，
        替代截帧网格图；失败/超限自动降级截帧——现有链路原封不动）。
        doc_direct + doc_path：文档直传模式（PDF 原件随生成请求发给模型，
        抽取文本**不进 prompt**）。失败/超限 → document=None 且保持文本模式。

        ⚠️ 返回值是 **4 元组**——加了 document 后刻意用位置返回而非实例属性：
        漏改调用点会立刻 ValueError，而不是静默拿到空 document（模式 #16：
        参数只传了一处导致另一处静默失效，本项目已栽过）。
        """
        import asyncio
        loop = asyncio.get_event_loop()

        # --- VL 管线：截帧 → 网格图（始终执行，结果按 keep_refs/send_to_llm 分流）---
        vl_image_urls = None
        video_path = None
        vl_text_inline = ""
        if vl_config:
            logger.info("VL config: interval=%ds grid=%dx%d",
                        vl_config.get("video_interval", 6),
                        vl_config.get("grid_cols", 2),
                        vl_config.get("grid_rows", 2))
            source_url = vl_config.get("source_url", "")
            if source_url:
                # 视频直传：模型支持 video 时 SDK 直传理解（服务端抽帧）→ 描述文本
                has_video_cap = has_capability(self.llm_config.get("capabilities", []), "video")
                if video_direct and has_video_cap:
                    desc, vp = await self._run_video_direct(vl_config, source_url)
                    if desc:
                        vl_text_inline = f"\n\n【视频画面理解（直传）】\n{desc}"
                    if vp:
                        video_path = vp
                    # 直传失败/超限 → 降级截帧（已下载视频直接截帧，不重复下载）
                    if not desc:
                        if vp:
                            vl_image_urls, _ = await loop.run_in_executor(
                                None, self._run_frames_from_path, vl_config, vp)
                        # vp None = 下载失败 → 纯文本生成（与截帧链路下载失败一致）
                else:
                    vl_image_urls, video_path = await loop.run_in_executor(
                        None, self._run_vl_pipeline, vl_config, source_url)

        # VL 截帧图的处理（对标文档图片的 keep_refs / send_to_llm / optimize 三层）：
        # - keep_refs: 截帧图嵌入为 Markdown ![](...) 引用
        # - send_to_llm: 截帧图作为 base64 发给 LLM
        # - optimize_images: 截帧图 PIL 缩放 ≤1024px
        if vl_image_urls and keep_refs:
            vl_text_inline = self._format_vl_images(vl_image_urls,
                                                     optimize=optimize_images)

        # --- 剥离转录图片（线程池执行，避免读文件+编码阻塞事件循环）---
        clean_text, doc_image_urls = await loop.run_in_executor(
            None, self._extract_and_strip_data_uris,
            raw_text, self.store_root, self.doc_dir, keep_refs, send_to_llm, optimize_images)
        if vl_text_inline:
            clean_text = clean_text + "\n\n" + vl_text_inline

        # --- 文档直传：原件随生成请求发给模型，抽取文本不进 prompt ---
        # 位置有两处讲究：
        #   ① 放在「剥离转录图片」**之后** —— 那一步会把文档内嵌图片写进 assets
        #      （导出/预览依赖），直传只改「什么进 prompt」，不改「什么落盘」；
        #   ② 放在 _truncate_text **之前** —— 直传成功后 clean_text 只剩一句占位，
        #      顺序无实质影响，但摆在这里才看得出「正文被换掉了」这件事。
        # ⚠️ 直传模式下 clean_text 被**替换**而非追加：追加等于同一份内容 token 双花
        # （PDF 视觉计费 + Markdown 文本各占一遍），正是本模式要避免的。
        document = None
        if doc_direct and doc_path:
            document = await loop.run_in_executor(None, self._run_doc_direct, doc_path)
            if document:
                clean_text = ContentPreparer._DOC_DIRECT_PLACEHOLDER

        # --- 截断过长文本 ---
        clean_text, _ = self._truncate_text(
            clean_text,
            self.llm_config.get("provider", ""),
            self.llm_config.get("context_limits"),
            self.llm_config.get("model_context_window"),
            self.budget_profile,
        )

        # --- 合并 VL 截图 + 文档图片 → 仅 send_to_llm 时发给 LLM ---
        llm_vl_urls: list[str] = []
        if vl_image_urls and send_to_llm:
            if optimize_images:
                llm_vl_urls = [self._optimize_data_uri(u) for u in vl_image_urls]
            else:
                llm_vl_urls = list(vl_image_urls)
        llm_doc_urls = doc_image_urls or []
        merged_urls = llm_vl_urls + llm_doc_urls
        has_image = has_capability(self.llm_config.get("capabilities", []), "image")
        image_urls = merged_urls if (merged_urls and has_image) else None

        # 直传模式下 prompt 里没有正文，图片没有可嵌入的位置 —— 一律不发。
        # （文件本身仍在 assets 里，导出/预览照常。）
        if document:
            image_urls = None

        return clean_text, image_urls, video_path, document

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
            from app.adapters.video import _cookie_header
            ch = _cookie_header(cookies.strip().strip("'\"").strip())
            if ch:
                ydl_opts["http_headers"]["Cookie"] = ch
                logger.info("VL: 注入 Cookie (%d 字符)", len(ch))

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.extract_info(url, download=True)
            files = list(Path(tmp_dir).iterdir())
            if not files:
                import shutil
                shutil.rmtree(tmp_dir, ignore_errors=True)
                return None
            return str(files[0])
        except Exception as e:
            logger.warning("VL 视频下载失败: %s", e)
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return None

    async def _run_video_direct(self, vl_config: dict, source_url: str) -> tuple[str | None, str | None]:
        """视频直传理解：下载视频 → DashScope SDK 视频理解 → 描述文本。

        返回 (desc, video_path)：desc=None = 直传失败（调用方降级截帧）；
        video_path=None = 下载失败（纯文本生成）。SDK 上传+处理可能数十秒，
        包 90s 超时防 prepare 卡死（评审反馈）。
        """
        import asyncio
        loop = asyncio.get_event_loop()
        cookies = vl_config.get("cookies", "")
        video_path = await loop.run_in_executor(
            None, ContentPreparer._download_video, source_url, cookies)
        if not video_path:
            logger.warning("视频直传: 视频下载失败，回退纯文本生成")
            return None, None

        # 视频直传仅 dashscope 视频模型（其他 provider 前端已禁用，后端兜底降级截帧）
        provider = self.llm_config.get("provider", "")
        if provider != "dashscope":
            logger.warning("视频直传: provider=%s 非 dashscope，降级截帧", provider)
            return None, video_path
        model = (self.llm_config.get("model") or {}).get(provider, "")
        if not model:
            logger.warning("视频直传: 无 dashscope 模型名，降级截帧")
            return None, video_path
        api_key = self.llm_config.get("api_key", "")

        from app.services.video_understand import understand_video_direct
        try:
            desc = await asyncio.wait_for(
                loop.run_in_executor(
                    None, understand_video_direct, model, video_path,
                    None, api_key, None),
                timeout=90,
            )
        except asyncio.TimeoutError:
            logger.warning("视频直传超时（>90s）→ 降级截帧")
            return None, video_path
        if not desc:
            logger.warning("视频直传失败/超限 → 降级截帧")
            return None, video_path
        return desc, video_path

    def _run_doc_direct(self, doc_path: str) -> dict | None:
        """文档直传准备：能力门 → 读原件 → 直传文档块 `{"filename","data_uri"}`。

        ⚠️ 与 _run_video_direct 的形态差异（别照抄那边的超时包装）：视频直传是
        「先理解成描述文本再注入 prompt」，故内部会发起 LLM 调用、需要自带超时；
        文档直传是「原件随生成请求一起发」，**本函数不发起任何 LLM 调用**，
        耗时只有读文件 + base64（百毫秒级）。真正的解析代价体现为生成调用的
        首包静默，由 chunk_timeout（doc_understand.DOC_DIRECT_TIMEOUT）覆盖。

        返回 None = **保持文本模式**（调用方 clean_text 原样不动），不是换链路。
        具体失败原因（不可读/为空/超限/非 PDF）见 build_pdf_document 的日志。

        ⚠️ 能力门与 `LLMService.supports_document` 是**同一判据**，两侧都走
        `core/model_caps.has_capability`（P1-15 收敛，模式 #54）——此前这里与
        `service.py:127` 各写一份同构表达式，改一处另一处静默不动。
        """
        caps = self.llm_config.get("capabilities", [])
        if not has_capability(caps, "document"):
            logger.warning("文档直传: 当前模型未标注 document 能力，改用文本模式")
            return None
        return build_pdf_document(doc_path)

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
        image_urls, _ = ContentPreparer._run_frames_from_path(vl_config, video_path)
        return image_urls, video_path

    @staticmethod
    def _run_frames_from_path(vl_config: dict, video_path: str) -> tuple[list[str] | None, str | None]:
        """对已下载视频执行截帧拼网格（直传降级复用，不重复下载）。返回 (image_urls, video_path)"""
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
            # 清理截帧临时目录（帧图 + 网格图可能数百张/数百 MB，防磁盘泄漏）
            import shutil as _sh
            for _d in (getattr(reader, 'frame_dir', None), getattr(reader, 'grid_dir', None)):
                if _d:
                    _sh.rmtree(_d, ignore_errors=True)
            return image_urls, video_path
        except Exception as e:
            logger.warning("VL: VideoReader 失败: %s，回退纯文本生成", e)
            return None, video_path

    # ── VL 截帧图内联 ──

    @staticmethod
    def _format_vl_images(vl_urls: list[str], optimize: bool = False) -> str:
        """将 VL 网格 base64 图转为 Markdown 引用嵌入文本。
        对标文档图片的 keep_refs 模式：图片不出现在 LLM 上下文中，但保留在笔记里。
        optimize=True 时 PIL 缩放长边 ≤1024px 以减小文件体积。
        """
        parts: list[str] = []
        for i, url in enumerate(vl_urls):
            if optimize and url.startswith("data:image"):
                try:
                    url = ContentPreparer._optimize_data_uri(url)
                except Exception as e:
                    logger.warning("VL 图片优化失败 (第%d张): %s", i + 1, e)
            parts.append(f"![视频截帧 {i + 1}]({url})")
        logger.info("VL: %d 张截帧图已嵌入文章 (%s)", len(parts),
                     "已优化" if optimize else "原始尺寸")
        return "\n\n".join(parts)

    @staticmethod
    def _optimize_data_uri(data_uri: str, max_edge: int = 1024) -> str:
        """PIL 缩放 base64 图片，长边 ≤ max_edge px，返回优化后的 data URI"""
        import base64
        from io import BytesIO
        from PIL import Image
        header, b64 = data_uri.split(",", 1)
        img_data = base64.b64decode(b64)
        img = Image.open(BytesIO(img_data))
        w, h = img.size
        if max(w, h) > max_edge:
            ratio = max_edge / max(w, h)
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
        buf = BytesIO()
        fmt = img.format or "JPEG"
        if fmt.upper() in ("JPEG", "JPG") and img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        img.save(buf, format=fmt, quality=85, optimize=True)
        return f"data:image/{fmt.lower()};base64,{base64.b64encode(buf.getvalue()).decode()}"

    # ── 图片剥离 ──

    @staticmethod
    def _extract_and_strip_data_uris(
        raw_text: str, store_root: str = "", doc_dir: str = "",
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
        asset_ref_pat = r'!\[([^\]]*)\]\((assets/[^\s)]+)\)'

        data_uri_re = re.compile(data_uri_pat)
        file_ref_re = re.compile(file_ref_pat)
        asset_ref_re = re.compile(asset_ref_pat)

        def _replacer(m):
            alt = m.group(1)
            if m.re is data_uri_re:
                uri = m.group(2)
                if send_to_llm:
                    cleaned = _sanitize_image_uri(uri, optimize_images)
                    if cleaned:
                        image_urls.append(cleaned)
                return f'[📷 {alt}]' if alt else '[📷 图片]'
            elif m.re is asset_ref_re and doc_dir:
                # 新格式：assets/xxx → 从 {doc_dir}/assets/ 读取
                fname = m.group(2)  # e.g. "assets/img_0001.png"
                file_path = (Path(doc_dir) / fname).resolve()
                # ⚠️ 防路径穿越：rawText 来自用户导入内容，可构造 assets/../../config.yaml
                # 读取 doc_dir 外任意文件 base64 发给 LLM / 写入导出包 — 越界则跳过不读
                try:
                    file_path.relative_to(Path(doc_dir).resolve())
                except ValueError:
                    return f'[📷 {alt}]' if alt else '[📷 图片]'
                if file_path.exists():
                    if send_to_llm:
                        import base64
                        ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else "png"
                        mime = f"image/{ext}"
                        b64 = base64.b64encode(file_path.read_bytes()).decode()
                        data_uri = f"data:{mime};base64,{b64}"
                        cleaned = _sanitize_image_uri(data_uri, optimize_images)
                        if cleaned:
                            image_urls.append(cleaned)
                    if keep_refs:
                        return f'![{alt}]({fname})'
                    else:
                        return f'[📷 {alt}]' if alt else '[📷 图片]'
            elif m.re is file_ref_re and store_root:
                # 旧格式兼容：/api/files/{tx_id}/{filename}
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
                        cleaned = _sanitize_image_uri(data_uri, optimize_images)
                        if cleaned:
                            image_urls.append(cleaned)
                    if keep_refs:
                        return f'![{alt}]({full_path})'
                    else:
                        return f'[📷 {alt}]' if alt else '[📷 图片]'
            return m.group(0)

        clean_text = data_uri_re.sub(_replacer, raw_text)
        clean_text = file_ref_re.sub(_replacer, clean_text)
        clean_text = asset_ref_re.sub(_replacer, clean_text)

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
        profile: str = "single",
    ) -> tuple[str, bool]:
        """如果文本超过可用输入预算，截断并返回 (truncated_text, was_truncated)。

        预算由 `app.generation.budget.input_budget_for(context, profile)` 唯一决定
        （本方法不再自持常量）。`profile`：
        - `"single"` 普通单文档（与既有算法逐值等价）
        - `"multi"`  合成文档（等式闭合，见 budget 模块）

        ⚠️ **创建侧与生成侧必须传同一个 `profile` 与同一个 `context`**——合成文档的
        rawText 是按 `multi` 预算恰好装满的，生成时若按 `single` 截断，用户会看到
        「内容过长，已截断」。这正是设计 §4.4.5 要消灭的二次截断。

        ⚠️ `model_context_window` 应由调用方从 `resolve_context_window(config, model_id)`
        取好后传入（见 `routers/generate.py`）。此处退回 `provider` 兜底表只是**最末一层**，
        与 `llm_models` 预设可差数倍（dashscope/qwen3.8-max：预设 131,072 / 兜底 1,000,000）。
        """
        limits = {**_DEFAULT_CONTEXT, **(context_limits or {})}
        context = model_context_window or limits.get(provider, 128_000)

        available = input_budget_for(context, profile)

        tokens = _estimate_tokens(text)
        if tokens <= available:
            return text, False

        ratio = available / tokens
        cut = max(1, int(len(text) * ratio * 0.95))
        truncated = text[:cut] + f"\n\n[内容过长，已截断：{tokens} tokens → 约 {available} tokens]"
        logger.warning("文本截断: %d tokens → ~%d tokens (context=%d, provider=%s)",
                       tokens, available, context, provider)
        return truncated, True
