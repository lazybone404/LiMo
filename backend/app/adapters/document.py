"""
文档适配器 — PDF/DOCX/PPTX/XLSX/EPUB → Markdown
支持 engine_bindings.doc 指定转录引擎
导入时自动转录（对标视频的 字幕/ASR → rawText）
"""

import asyncio
import base64
import copy
import logging
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from app.core.errors import FatalError
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
        # ⚠️ 用 safe_get 逐跳校验重定向（裸 requests.get 默认跟随，公网 URL
        # 302 到 127.0.0.1 就绕过了首跳校验）
        from app.utils.url_security import safe_get

        import requests
        tmp = Path(tempfile.mkdtemp(prefix="doc_dl_"))
        parsed = urlparse(url)
        fname = Path(parsed.path).name or "download"
        dest = tmp / fname

        if on_progress:
            on_progress(ProgressEvent(phase="download", status="running",
                                      message=f"下载文档: {fname}"))
        try:
            resp = safe_get(url, timeout=120, stream=True)
            resp.raise_for_status()
        except ValueError as e:
            # 安全校验失败（含重定向跳到内网）不是「网络不好」，分开报
            raise RuntimeError(f"URL 安全校验失败: {str(e)[:150]}") from e
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

        # 旧二进制格式明确提示（失败显式化，不静默）：python-docx/pptx 只支持 OpenXML
        if ext == ".doc":
            raise FatalError("暂不支持旧版 .doc 格式，请用 Word/WPS 另存为 .docx 后导入")
        if ext == ".ppt":
            raise FatalError("暂不支持旧版 .ppt 格式，请用 PowerPoint 另存为 .pptx 后导入")

        if on_progress:
            on_progress(ProgressEvent(phase="transcribe", status="running",
                                      message="转录中..."))

        # 1. 解析子场景 → 引擎优先级链
        sub_scenario = self._ext_to_sub_scenario(ext)
        engine_chain = self._resolve_doc_engine_chain(cfg, sub_scenario=sub_scenario)
        if not engine_chain:
            # 未绑定 → 按扩展名兜底（默认行为，非引擎降级）
            if ext == ".pdf":
                eng_name = "pypdfium2"
            elif ext in (".docx", ".doc"):
                eng_name = "python-docx"
            elif ext in (".pptx", ".ppt"):
                eng_name = "python-pptx"  # python-pptx 图片提取稳定；markitdown 对某些 PPTX 不提取图片
            elif ext in (".xlsx", ".xls"):
                eng_name = "openpyxl"
            elif ext == ".epub":
                eng_name = "epub-container"
            else:
                eng_name = "pypdfium2"
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

        # 2. 执行绑定的第一个引擎（无引擎降级链：失败直接报错）
        # 转录图片输出目录（引擎写文件用）
        media_dir = cfg.get("media_dir", "") if cfg else ""

        eng_name, engine = engine_chain[0]
        # 深拷贝防并发竞态
        engine = copy.copy(engine)
        logger.info("Doc 引擎执行: %s", eng_name)
        try:
            result = await asyncio.to_thread(engine.run, str(path), media_dir=media_dir)
        except FatalError:
            raise
        except Exception as e:
            logger.warning("Doc 引擎 %s 失败: %s", eng_name, str(e)[:120])
            raise FatalError(f"Doc 引擎 {eng_name} 失败: {str(e)[:100]}") from e
        if not (result and result.text):
            raise FatalError(f"Doc 引擎 {eng_name} 返回空结果，请检查引擎配置或更换引擎")
        raw_text = result.text
        pages = getattr(result, 'pages', 1)
        is_scanned = getattr(result, 'is_scanned', False)
        logger.info("Doc 引擎 %s 成功: %d chars, %d pages", eng_name, len(raw_text), pages)

        # 3. 扫描件 PDF → OCR 叠加（嵌入页面原图 + OCR 文字）
        # pdf-image 引擎产物本身就是「每页图片」转录，跳过重复渲染+OCR（避免二次渲染）
        if is_scanned and ext == ".pdf" and eng_name != "pdf-image":
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
            return "doc:pdf"
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
        sub_scenario 如 "doc:pdf"、"doc:word" 等。

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

    def _resolve_ocr_engine(self) -> tuple[str, "Engine", str] | None:
        """根据 engine_bindings.ocr 解析第一个可用 OCR 引擎（无引擎降级链）。

        绑定第一个可用引擎；绑定为空或不可用 → 返回 None（调用方报错/占位）。"""
        cfg = self._engine_config or {}
        bindings = cfg.get("engine_bindings", {})
        engine_models = cfg.get("engine_models", [])

        model_ids = bindings.get("ocr", [])
        if isinstance(model_ids, int):
            model_ids = [model_ids]

        emap = {m["id"]: m for m in engine_models}
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
                return (eng_name, engine, model_name)
        return None

    # ── Scanned PDF OCR ──

    def _ocr_pdf_with_images(self, path: Path) -> tuple[str, int]:
        """扫描件 PDF → 逐页渲染为图片 → OCR 提取文字 → 嵌入原图 + OCR 文字

        产物：每页包含原图（150dpi）和 OCR 识别的文字，方便对照确认。
        只用绑定第一个 OCR 引擎（无引擎降级链）；失败/空 → 占位（原图 + 失败原因）。
        """
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(path))
        pages = len(pdf)
        parts: list[str] = []
        success = 0

        ocr_engine_info = self._resolve_ocr_engine()
        dashscope_key = self._get_dashscope_key()

        for i in range(pages):
            page_num = i + 1
            logger.info("OCR 处理第 %d/%d 页...", page_num, pages)

            # 渲染页面为图片 (150 DPI ≈ scale 2.08)
            page = pdf[i]
            bitmap = page.render(scale=2.0)
            pil_img = bitmap.to_pil()
            img_bytes = pil_img.tobytes() if pil_img.mode == 'RGB' else pil_img.convert('RGB').tobytes()
            img_b64 = base64.b64encode(img_bytes).decode()

            # OCR 识别 — 只用绑定第一个引擎（无引擎降级链）
            # P1-10：mktemp 不安全（TOCTOU 竞态）→ mkstemp（关闭 fd 防句柄泄漏）
            _fd, _tmp_name = tempfile.mkstemp(suffix=f"_p{page_num}.png")
            os.close(_fd)
            tmp_path = Path(_tmp_name)
            pil_img.save(str(tmp_path), format="PNG")

            ocr_text = ""
            ocr_fail = ""
            if ocr_engine_info:
                eng_name, ocr_engine, ocr_model = ocr_engine_info
                ocr_engine = copy.copy(ocr_engine)
                if hasattr(ocr_engine, 'api_key') and not ocr_engine.api_key:
                    if dashscope_key:
                        ocr_engine.api_key = dashscope_key
                try:
                    ocr_result = ocr_engine.run(str(tmp_path), model_name=ocr_model)
                    if ocr_result and ocr_result.text:
                        ocr_text = ocr_result.text
                        success += 1
                    else:
                        ocr_fail = "OCR 引擎返回空结果"
                except Exception as e:
                    logger.warning("OCR 引擎 %s 第 %d 页失败: %s", eng_name, page_num, str(e)[:120])
                    ocr_fail = str(e)[:80]

            if not ocr_text:
                ocr_text = f"_(OCR 失败: {ocr_fail or '无可用 OCR 引擎'})_"

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

        pdf.close()
        logger.info("扫描件 OCR 完成: %d/%d 页成功", success, pages)
        return "\n\n---\n\n".join(parts), pages
