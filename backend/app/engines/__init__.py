"""
引擎注册入口
应用启动时调用 init_engines() 注册并设置降级链
"""

import logging

from app.core.registry import EngineRegistry

logger = logging.getLogger(__name__)


def init_engines(config: dict | None = None):
    """注册所有引擎 + 降级链"""
    config = config or {}

    # ASR — 云端优先，本地兜底
    from .asr import FasterWhisperEngine

    small = FasterWhisperEngine(model_size="small")
    small.name = "faster-whisper-small"

    medium = FasterWhisperEngine(model_size="medium")
    medium.name = "faster-whisper-medium"

    small.fallback = None

    EngineRegistry.register("asr", "faster-whisper-small", small)
    EngineRegistry.register("asr", "faster-whisper-medium", medium)

    # 千问 DashScope ASR（云端，中文最佳）
    # Key 来源：cloud.dashscope → engine_providers[dashscope].api_key → cloud.alibaba
    dashscope_key = (
        config.get("cloud", {}).get("dashscope", "") or
        config.get("cloud", {}).get("alibaba", "")
    )
    if not dashscope_key:
        for ep in config.get("engine_providers", []):
            if ep.get("id") == "dashscope":
                dashscope_key = ep.get("api_key", "")
                break
    try:
        from .asr_cloud import DashScopeASREngine
        dashscope_asr = DashScopeASREngine(api_key=dashscope_key)
        dashscope_asr.name = "dashscope-asr"
        dashscope_asr.fallback = None  # 云端失败直接报错，不降级本地
        EngineRegistry.register("asr", "dashscope-asr", dashscope_asr)
        logger.info(f"ASR engines: dashscope-asr + faster-whisper-medium + small (key={'configured' if dashscope_key else 'NOT SET'})")
    except ImportError as e:
        logger.warning(f"DashScope ASR unavailable: {e}")
    except Exception as e:
        logger.warning(f"DashScope ASR init failed: {e}")

    # OCR key（Doc 引擎 PDFOCREngine 也需要）
    dashscope_ocr_key = (
        config.get("cloud", {}).get("dashscope", "") or
        config.get("cloud", {}).get("alibaba", "")
    )
    if not dashscope_ocr_key:
        for ep in config.get("engine_providers", []):
            if ep.get("id") == "dashscope":
                dashscope_ocr_key = ep.get("api_key", "")
                break

    # Doc
    from .doc import PyMuPDFEngine, PyMuPDF4LLMEngine, MarkItDownEngine, PDFOCREngine, DocxEngine, PptxEngine, ExcelEngine
    pymupdf = PyMuPDFEngine()                    # 文字+图片
    pymupdf.name = "pymupdf"
    pymupdf_text = PyMuPDFEngine(text_only=True)  # 仅文字
    pymupdf_text.name = "pymupdf-text"
    pymupdf4llm = PyMuPDF4LLMEngine()             # pymupdf4llm：文字+图片+矢量图
    pymupdf4llm.name = "pymupdf4llm"
    markit = MarkItDownEngine()
    markit.name = "markitdown"
    pdf_ocr = PDFOCREngine(api_key=dashscope_ocr_key)
    pdf_ocr.name = "pdf-ocr"
    docx = DocxEngine()                          # 文字+图片（默认）
    docx.name = "python-docx"
    docx_text = DocxEngine(text_only=True)        # 仅文字
    docx_text.name = "python-docx-text"
    EngineRegistry.register("doc", "pymupdf", pymupdf)
    EngineRegistry.register("doc", "pymupdf-text", pymupdf_text)
    EngineRegistry.register("doc", "pymupdf4llm", pymupdf4llm)
    EngineRegistry.register("doc", "markitdown", markit)
    EngineRegistry.register("doc", "pdf-ocr", pdf_ocr)
    EngineRegistry.register("doc", "python-docx", docx)
    EngineRegistry.register("doc", "python-docx-text", docx_text)
    pptx = PptxEngine()                          # 文字+图片+备注（默认）
    pptx.name = "python-pptx"
    pptx_text = PptxEngine(text_only=True)        # 仅文字+备注
    pptx_text.name = "python-pptx-text"
    EngineRegistry.register("doc", "python-pptx", pptx)
    EngineRegistry.register("doc", "python-pptx-text", pptx_text)
    xlsx = ExcelEngine()
    xlsx.name = "openpyxl"
    EngineRegistry.register("doc", "openpyxl", xlsx)
    # EPUB
    try:
        from .epub import EpubEngine
        epub_engine = EpubEngine()
        epub_engine.name = "ebooklib"
        EngineRegistry.register("doc", "ebooklib", epub_engine)
        logger.info("Doc engines: + ebooklib (EPUB)")
    except ImportError as e:
        logger.warning(f"EPUB engine (ebooklib) unavailable: {e}")
    except Exception as e:
        logger.warning(f"EPUB engine init failed: {e}")
    logger.info("Doc engines: pymupdf + pymupdf-text + python-docx + python-docx-text + python-pptx + python-pptx-text + openpyxl + markitdown + pdf-ocr (ocr key={'configured' if dashscope_ocr_key else 'NOT SET'})")

    # OCR — 云端 VL 提取文字，本地 Tesseract 轻量兜底
    try:
        from .ocr import DashScopeOCREngine
        dashscope_ocr = DashScopeOCREngine(api_key=dashscope_ocr_key)
        dashscope_ocr.name = "dashscope-ocr"
        EngineRegistry.register("ocr", "dashscope-ocr", dashscope_ocr)
        logger.info(f"OCR engines: dashscope-ocr (key={'configured' if dashscope_ocr_key else 'NOT SET'})")
    except ImportError as e:
        logger.warning(f"DashScope OCR unavailable: {e}")
    except Exception as e:
        logger.warning(f"DashScope OCR init failed: {e}")

    # Tesseract — 本地 OCR，无云降级（失败应明确报错而非偷偷走云端）
    # 自动注册到 engine_models + engine_bindings.ocr，用户无需手动设置
    try:
        from .ocr import TesseractEngine
        tesseract = TesseractEngine()
        tesseract.name = "tesseract"
        EngineRegistry.register("ocr", "tesseract", tesseract)
        logger.info("OCR engines: tesseract (local)")

        # 自动写入 engine_models（如果尚未添加）
        import hashlib
        mid = int(hashlib.md5(b"engine:pymupdf:tesseract").hexdigest()[:8], 16)
        engine_models = config.get("engine_models", [])
        existing_ids = {m["id"] for m in engine_models}
        if mid not in existing_ids:
            engine_models.append({
                "id": mid,
                "provider_id": "pymupdf",
                "model_name": "tesseract",
                "category": "ocr",
            })
            logger.info("Auto-added tesseract to engine_models (id=%d)", mid)

        # 自动绑定到 OCR 兜底
        bindings = config.setdefault("engine_bindings", {})
        ocr_list: list = bindings.get("ocr", [])
        if mid not in ocr_list:
            ocr_list.append(mid)
            bindings["ocr"] = ocr_list
            logger.info("Auto-bound tesseract to engine_bindings.ocr")

        # 持久化（一次启动只写一次）
        from app.core.config_io import save_config
        save_config(config)
    except ImportError as e:
        logger.info(f"Tesseract not available: {e}")
    except Exception as e:
        logger.warning(f"Tesseract init failed: {e}")
