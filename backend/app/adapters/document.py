"""
文档适配器 — PDF/DOCX/PPTX/XLSX/EPUB → Markdown
支持 engine_bindings.doc 指定转录引擎
导入时自动转录（对标视频的 字幕/ASR → rawText）
"""

import asyncio
import base64
import copy
import logging
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import fitz  # PyMuPDF

from app.core.types import PipelineInput, AdapterOutput, RawMetadata, InputType, ProgressEvent
from app.core.protocols import PipelineAdapter
from app.core.registry import EngineRegistry

logger = logging.getLogger(__name__)

SUPPORTED_MIME = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/epub+zip",
}
SUPPORTED_EXT = {".pdf", ".docx", ".pptx", ".xlsx", ".epub", ".doc", ".ppt", ".xls"}


class DocumentAdapter(PipelineAdapter):
    name = "document"
    _engine_config: dict | None = None  # injected by ingest route

    def accept(self, input: PipelineInput) -> bool:
        if input.type == InputType.FILE:
            ext = Path(input.file_path or "").suffix.lower()
            return ext in SUPPORTED_EXT
        if input.type == InputType.URL:
            url_path = urlparse(input.url or "").path.lower()
            return any(url_path.endswith(ext) for ext in SUPPORTED_EXT)
        return False

    def _download_url(self, url: str, on_progress=None) -> Path:
        """下载 URL 指向的文件到临时目录"""
        from app.utils.url_security import validate_url
        try:
            validate_url(url)
        except ValueError as e:
            raise RuntimeError(f"URL 安全校验失败: {str(e)[:150]}") from e

        import requests
        tmp = Path(tempfile.mkdtemp(prefix="doc_dl_"))
        parsed = urlparse(url)
        fname = Path(parsed.path).name or "download"
        dest = tmp / fname

        if on_progress:
            on_progress(ProgressEvent(phase="download", status="running",
                                      message=f"下载文档: {fname}"))
        try:
            resp = requests.get(url, timeout=120, stream=True)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.warning("文档下载失败: %s — %s", url[:80], str(e)[:120])
            raise RuntimeError(f"文档下载失败: {str(e)[:150]}") from e
        dest.write_bytes(resp.content)
        logger.info("文档下载: %s → %s (%.1fKB)", url[:60], fname, dest.stat().st_size / 1024)
        if on_progress:
            on_progress(ProgressEvent(phase="download", status="done"))
        return dest

    async def adapt(self, input: PipelineInput,
                    on_progress=None,
                    engine_config: dict | None = None,
    ) -> AdapterOutput:
        # 使用传入的引擎配置（防并发竞态），回退到单例配置
        cfg = engine_config or self._engine_config

        path: Path

        # URL → 先下载（线程池执行，不阻塞事件循环）
        if input.type == InputType.URL and input.url:
            loop = asyncio.get_event_loop()
            path = await loop.run_in_executor(None, self._download_url, input.url, on_progress)
        else:
            path = Path(input.file_path or "")

        ext = path.suffix.lower()

        if on_progress:
            on_progress(ProgressEvent(phase="transcribe", status="running",
                                      message="转录中..."))

        # 1. 解析子场景 → 引擎优先级链
        sub_scenario = self._ext_to_sub_scenario(ext)
        engine_chain = self._resolve_doc_engine_chain(cfg, sub_scenario=sub_scenario)
        if not engine_chain:
            auto_fallback = cfg.get("engine_auto_fallback", True) if cfg else True
            if not auto_fallback:
                raise FatalError("Doc 引擎未绑定，自动降级已关闭，请在设置中绑定文档引擎")
            else:
                # 未绑定 → 按扩展名兜底
                if ext == ".pdf":
                    eng_name = "pymupdf"
                elif ext in (".docx", ".doc"):
                    eng_name = "python-docx"
                elif ext in (".pptx", ".ppt"):
                    eng_name = "markitdown"  # markitdown 图片提取远优于 python-pptx（33张 vs ~1张）
                elif ext in (".xlsx", ".xls"):
                    eng_name = "openpyxl"
                elif ext == ".epub":
                    eng_name = "ebooklib"
                else:
                    eng_name = "markitdown"
                engine = EngineRegistry.get("doc", eng_name)
                if engine:
                    engine_chain = [(eng_name, engine)]

        if not engine_chain:
            logger.warning("无可用文档引擎")
            return AdapterOutput(
                raw_text="[无可用转录引擎，请在设置中配置文档引擎]",
                metadata=RawMetadata(),
                engine="none",
            )

        # 2. 按优先级依次尝试转录引擎
        raw_text = ""
        pages = 1
        is_scanned = False
        eng_name = ""
        tried: list[str] = []

        # 转录图片输出目录（引擎写文件用）
        media_dir = cfg.get("media_dir", "") if cfg else ""

        for eng_name, engine in engine_chain:
            # 深拷贝防并发竞态
            engine = copy.copy(engine)
            tried.append(eng_name)
            logger.info("Doc 引擎尝试: %s", eng_name)
            try:
                result = await asyncio.to_thread(engine.run, str(path), media_dir=media_dir)
                if result and result.text:
                    raw_text = result.text
                    pages = getattr(result, 'pages', 1)
                    is_scanned = getattr(result, 'is_scanned', False)
                    logger.info("Doc 引擎 %s 成功: %d chars, %d pages", eng_name, len(raw_text), pages)
                    break
                logger.warning("Doc 引擎 %s 返回空结果", eng_name)
            except Exception as e:
                logger.warning("Doc 引擎 %s 失败: %s", eng_name, str(e)[:120])
                continue

        if not raw_text:
            from app.core.errors import TranscribeError
            raise TranscribeError.empty(
                detail=f"已尝试 {len(tried)} 个引擎 ({', '.join(tried)})，全部返回空结果。"
                       f"请检查引擎配置或在设置中开启引擎自动降级。"
            )

        # 3. 扫描件 PDF → OCR 叠加（嵌入页面原图 + OCR 文字）
        if is_scanned and ext == ".pdf":
            if on_progress:
                on_progress(ProgressEvent(
                    phase="transcribe", status="running",
                    message=f"检测到扫描件（{result.scanned_pages}/{pages} 页无文字层），OCR 识别中..."
                ))
            raw_text, pages = await asyncio.to_thread(self._ocr_pdf_with_images, path)

        if on_progress:
            on_progress(ProgressEvent(phase="transcribe", status="done"))

        return AdapterOutput(
            raw_text=raw_text,
            metadata=RawMetadata(pages=pages, is_scanned=is_scanned),
            engine=eng_name,
        )

    # ── Engine resolution (priority chain) ──

    @staticmethod
    def _ext_to_sub_scenario(ext: str) -> str | None:
        """文件扩展名 → 子场景 key"""
        if ext == ".pdf":
            return "doc:text+image"
        if ext in (".docx", ".doc"):
            return "doc:word"
        if ext in (".pptx", ".ppt"):
            return "doc:ppt"
        if ext in (".xlsx", ".xls"):
            return "doc:excel"
        if ext == ".epub":
            return "doc:epub"
        return None

    def _resolve_doc_engine_chain(self, cfg: dict | None = None,
                                    sub_scenario: str | None = None) -> list[tuple[str, "Engine"]]:
        """根据 engine_bindings 解析文档引擎优先级链。

        优先级：sub_scenario key → 扁平 doc key → 返回空（由扩展名兜底）
        sub_scenario 如 "doc:text+image"、"doc:office" 等。

        支持三种绑定值格式：
        - 字符串: "pymupdf4llm" → 直接用名称查找 EngineRegistry
        - 整数: 1926069186 → 通过 engine_models id→名称 查找
        - 列表: [id1, id2] → 旧格式，逐个解析
        """
        cfg = cfg or self._engine_config or {}
        bindings = cfg.get("engine_bindings", {})
        engine_models = cfg.get("engine_models", [])

        raw = bindings.get(sub_scenario) if sub_scenario else None
        if raw is None or raw == []:
            raw = bindings.get("doc")

        if not raw or raw == []:
            return []

        # 统一为列表处理
        items: list = raw if isinstance(raw, list) else [raw]

        emap = {m["id"]: m for m in engine_models}
        chain: list[tuple[str, "Engine"]] = []
        for item in items:
            model_name: str | None = None
            if isinstance(item, str):
                model_name = item  # 字符串格式：直接用名称
            elif isinstance(item, int):
                bm = emap.get(item)
                if bm:
                    model_name = bm["model_name"]
            if model_name:
                engine = EngineRegistry.get("doc", model_name)
                if engine:
                    chain.append((model_name, engine))
        return chain

    def _get_dashscope_key(self) -> str:
        cfg = self._engine_config or {}
        for ep in cfg.get("engine_providers", []):
            if ep.get("id") == "dashscope":
                return ep.get("api_key", "")
        return ""

    def _resolve_ocr_engine_chain(self) -> list[tuple[str, "Engine", str]]:
        """根据 engine_bindings.ocr 解析 OCR 引擎优先级链。
        空绑定或全部失败时，遍历已注册引擎作为降级。"""
        cfg = self._engine_config or {}
        bindings = cfg.get("engine_bindings", {})
        engine_models = cfg.get("engine_models", [])

        model_ids = bindings.get("ocr", [])
        if isinstance(model_ids, int):
            model_ids = [model_ids]

        emap = {m["id"]: m for m in engine_models}
        chain: list[tuple[str, "Engine", str]] = []
        for mid in model_ids:
            bm = emap.get(mid)
            if not bm:
                continue
            model_name = bm["model_name"]
            provider_id = bm["provider_id"]
            eng_name = model_name
            if provider_id == "dashscope":
                eng_name = "dashscope-ocr"
            engine = EngineRegistry.get("ocr", eng_name)
            if engine:
                chain.append((eng_name, engine, model_name))

        # 绑定链为空或全部失败
        if not chain:
            auto_fallback = cfg.get("engine_auto_fallback", True)
            if not auto_fallback:
                logger.error("OCR 引擎链为空，自动降级已关闭")
                return chain

            for eng_name in EngineRegistry.list_category("ocr"):
                engine = EngineRegistry.get("ocr", eng_name)
                if engine:
                    logger.warning("OCR 引擎链为空，降级使用: %s", eng_name)
                    chain.append((eng_name, engine, "default"))
                    break
        return chain

    # ── Scanned PDF OCR ──

    def _ocr_pdf_with_images(self, path: Path) -> tuple[str, int]:
        """扫描件 PDF → 逐页渲染为图片 → OCR 提取文字 → 嵌入原图 + OCR 文字

        产物：每页包含原图（150dpi）和 OCR 识别的文字，方便对照确认。
        按优先级依次尝试 OCR 引擎，失败或空结果自动降级到下一个。
        """
        doc = fitz.open(str(path))
        pages = doc.page_count
        parts: list[str] = []
        success = 0

        ocr_chain = self._resolve_ocr_engine_chain()
        dashscope_key = self._get_dashscope_key()

        for i, page in enumerate(doc):
            page_num = i + 1
            logger.info("OCR 处理第 %d/%d 页...", page_num, pages)

            # 渲染页面为图片
            pix = page.get_pixmap(dpi=150)
            img_b64 = base64.b64encode(pix.tobytes("png")).decode()

            # OCR 识别 — 按优先级链依次尝试
            tmp_path = Path(tempfile.mktemp(suffix=f"_p{page_num}.png"))
            tmp_path.write_bytes(pix.tobytes("png"))

            ocr_text = ""
            ocr_used = ""
            for eng_name, ocr_engine, ocr_model in ocr_chain:
                ocr_engine = copy.copy(ocr_engine)
                if hasattr(ocr_engine, 'api_key') and not ocr_engine.api_key:
                    if dashscope_key:
                        ocr_engine.api_key = dashscope_key
                try:
                    ocr_result = ocr_engine.run(str(tmp_path), model_name=ocr_model)
                    if ocr_result and ocr_result.text:
                        ocr_text = ocr_result.text
                        ocr_used = eng_name
                        success += 1
                        break
                    logger.warning("OCR 引擎 %s 返回空结果，尝试下一个", eng_name)
                except Exception as e:
                    logger.warning("OCR 引擎 %s 第 %d 页失败: %s", eng_name, page_num, str(e)[:120])
                    continue

            if not ocr_text:
                ocr_text = "_(所有 OCR 引擎均失败)_"

            # 嵌入原图 + OCR 文字
            parts.append(
                f"## Page {page_num}\n\n"
                f"![page {page_num}](data:image/png;base64,{img_b64})\n\n"
                f"{ocr_text}"
            )

            # 清理临时文件
            try:
                tmp_path.unlink()
            except OSError:
                pass

        doc.close()
        logger.info("扫描件 OCR 完成: %d/%d 页成功 [%s]", success, pages, ocr_used or "none")
        return "\n\n---\n\n".join(parts), pages
