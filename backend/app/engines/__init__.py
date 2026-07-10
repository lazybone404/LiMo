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

    # 必剪（Bilibili Bcut）— 免费云转写，无需 Key，中文优化
    try:
        from .asr_bcut import BcutASREngine
        bcut = BcutASREngine()
        bcut.name = "bcut-asr"
        bcut.fallback = None
        EngineRegistry.register("asr", "bcut-asr", bcut)
        # 确保 engine_providers 中有 bcut（旧 config.yaml 可能缺）
        engine_provs = config.setdefault("engine_providers", [])
        if not any(p["id"] == "bcut" for p in engine_provs):
            engine_provs.append({
                "id": "bcut", "name": "B站必剪（免费云转写）", "type": "cloud",
                "base_url": "", "api_key": "", "capabilities": ["asr"],
                "builtin_models": [{"model_name": "bcut", "category": "asr", "desc": "免费云转写 · 中文优化"}],
            })
            logger.info("Auto-added bcut to engine_providers")
        logger.info("ASR: bcut-asr registered (free cloud, no key needed)")
    except ImportError as e:
        logger.warning(f"Bcut ASR unavailable: {e}")
    except Exception as e:
        logger.warning(f"Bcut ASR init failed: {e}")

    # ASR 自动注册到 engine_models + engine_bindings，用户开箱即用
    # 对标 Tesseract OCR 的自动注册逻辑
    import hashlib
    engine_models = config.get("engine_models", [])
    existing_ids = {m["id"] for m in engine_models}
    models_changed = False

    # bcut-asr（免费云转写，默认首选）
    mid_bcut = int(hashlib.md5(b"engine:bcut:bcut").hexdigest()[:8], 16)
    if mid_bcut not in existing_ids:
        engine_models.append({
            "id": mid_bcut,
            "provider_id": "bcut",
            "model_name": "bcut",
            "category": "asr",
        })
        existing_ids.add(mid_bcut)
        models_changed = True
        logger.info("Auto-added bcut-asr to engine_models (id=%d)", mid_bcut)

    # faster-whisper-small（本地免费，兜底）
    mid_whisper_small = int(hashlib.md5(b"engine:faster-whisper:small").hexdigest()[:8], 16)
    if mid_whisper_small not in existing_ids:
        engine_models.append({
            "id": mid_whisper_small,
            "provider_id": "faster-whisper",
            "model_name": "small",
            "category": "asr",
        })
        existing_ids.add(mid_whisper_small)
        models_changed = True
        logger.info("Auto-added faster-whisper-small to engine_models (id=%d)", mid_whisper_small)

    # faster-whisper-medium（本地，更大模型）
    mid_whisper_medium = int(hashlib.md5(b"engine:faster-whisper:medium").hexdigest()[:8], 16)
    if mid_whisper_medium not in existing_ids:
        engine_models.append({
            "id": mid_whisper_medium,
            "provider_id": "faster-whisper",
            "model_name": "medium",
            "category": "asr",
        })
        existing_ids.add(mid_whisper_medium)
        models_changed = True
        logger.info("Auto-added faster-whisper-medium to engine_models (id=%d)", mid_whisper_medium)

    # DashScope ASR（云端，需 API Key）
    if dashscope_key:
        mid_dashscope = int(hashlib.md5(b"engine:dashscope:paraformer-v2").hexdigest()[:8], 16)
        if mid_dashscope not in existing_ids:
            engine_models.append({
                "id": mid_dashscope,
                "provider_id": "dashscope",
                "model_name": "paraformer-v2",
                "category": "asr",
            })
            existing_ids.add(mid_dashscope)
            models_changed = True
            logger.info("Auto-added dashscope-asr to engine_models (id=%d)", mid_dashscope)

    # 自动绑定优先级：bcut（免费云）→ faster-whisper-small（本地兜底）
    bindings = config.setdefault("engine_bindings", {})
    asr_list: list = bindings.get("asr", [])
    asr_old = list(asr_list)
    # 清理失效的绑定（ID 在 engine_models 中不存在）
    asr_list = [mid for mid in asr_list if mid in existing_ids]
    # 保证 bcut 和 faster-whisper-small 都在列表中，bcut 优先
    for mid in [mid_bcut, mid_whisper_small]:
        if mid in asr_list:
            asr_list.remove(mid)
    asr_list.insert(0, mid_whisper_small)  # 末尾兜底
    asr_list.insert(0, mid_bcut)            # 首位默认
    bindings["asr"] = asr_list
    if asr_list != asr_old:
        logger.info("Auto-bound bcut (default) + faster-whisper-small (fallback) to engine_bindings.asr")

    asr_changed = models_changed or (asr_list != asr_old)

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
        ocr_bound = mid not in ocr_list
        if ocr_bound:
            ocr_list.append(mid)
            bindings["ocr"] = ocr_list
            logger.info("Auto-bound tesseract to engine_bindings.ocr")

        # 持久化 — 仅变更时写，避免每次都写盘拖慢启动
        if asr_changed or ocr_bound or mid not in existing_ids:
            from app.core.config_io import save_config
            save_config(config)
    except ImportError as e:
        logger.info(f"Tesseract not available: {e}")
    except Exception as e:
        logger.warning(f"Tesseract init failed: {e}")
