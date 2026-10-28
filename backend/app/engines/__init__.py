"""
引擎注册入口
应用启动时调用 init_engines() 注册引擎
"""

import logging

from app.core.registry import EngineRegistry

logger = logging.getLogger(__name__)


def _migrate_remove_faster_whisper(config: dict) -> bool:
    """启动迁移：清理旧版 faster-whisper 引擎残留。

    faster-whisper 已从内置移除（CPU 慢 / GPU 依赖笨重，需自行安装）。
    用户旧 config.yaml 可能残留：
    - engine_providers 中的 faster-whisper 整组（含 builtin_models）
    - engine_models 中 provider_id == "faster-whisper" 的条目
    - engine_auto_fallback 残留键（配置项已删除，磁盘 false 会误伤回退逻辑）

    返回是否有变更（供调用方计入 save_config 判定，确保迁移落盘）。
    """
    changed = False

    provs = config.get("engine_providers", [])
    before = len(provs)
    provs = [p for p in provs if p.get("id") != "faster-whisper"]
    if len(provs) != before:
        config["engine_providers"] = provs
        changed = True
        logger.info("迁移: 移除 faster-whisper engine_provider（已内置移除，需插件）")

    models = config.get("engine_models", [])
    before = len(models)
    models = [m for m in models if m.get("provider_id") != "faster-whisper"]
    if len(models) != before:
        config["engine_models"] = models
        changed = True
        logger.info("迁移: 移除 %d 个 faster-whisper engine_model 条目", before - len(models))

    if "engine_auto_fallback" in config:
        config.pop("engine_auto_fallback", None)
        changed = True
        logger.info("迁移: 移除 engine_auto_fallback 残留键")

    return changed


def _migrate_remove_tesseract(config: dict) -> bool:
    """启动迁移：清理 tesseract 引擎残留（内置移除 2026-08-02，需三方插件）。

    旧 config.yaml 可能残留：
    - engine_providers 的 builtin_models 中 model_name=="tesseract"（doc-local 里曾内置）
    - engine_models 中 model_name=="tesseract" 的条目（provider pymupdf 已由
      config._sync_builtin_models 清理，此处兜底）
    - engine_bindings.ocr 指向已不存在 model 的悬空 id（如 tesseract）

    返回是否有变更（供调用方计入 save_config 判定，确保迁移落盘）。
    """
    changed = False

    # 1. engine_providers 的 builtin_models 中移除 tesseract（DEFAULT_CONFIG 删条目不自动清已持久化配置）
    for prov in config.get("engine_providers", []):
        bms = prov.get("builtin_models", [])
        before = len(bms)
        bms = [bm for bm in bms if bm.get("model_name") != "tesseract"]
        if len(bms) != before:
            prov["builtin_models"] = bms
            changed = True
            logger.info("迁移: 从 %s 移除 %d 个 tesseract builtin_model", prov.get("id"), before - len(bms))

    models = config.get("engine_models", [])
    before = len(models)
    models = [m for m in models if m.get("model_name") != "tesseract"]
    if len(models) != before:
        config["engine_models"] = models
        changed = True
        logger.info("迁移: 移除 %d 个 tesseract engine_model 条目", before - len(models))

    # 清理 ocr 绑定中悬空 id（指向已不存在的 model）
    bindings = config.setdefault("engine_bindings", {})
    ocr_list = bindings.get("ocr", [])
    if isinstance(ocr_list, int):
        ocr_list = [ocr_list]
    emap = {m["id"] for m in models}
    new_ocr = [mid for mid in ocr_list if mid in emap]
    if len(new_ocr) != len(ocr_list):
        bindings["ocr"] = new_ocr
        changed = True
        logger.info("迁移: 清理 %d 个悬空 ocr 绑定（tesseract）", len(ocr_list) - len(new_ocr))

    return changed


def _migrate_remove_docling(config: dict) -> bool:
    """启动迁移：清理 docling 引擎残留（内置移除 2026-08-02，依赖 torch ~2GB 无法打包）。

    旧 config.yaml 可能残留：
    - engine_providers 的 builtin_models 中 model_name=="docling"
    - engine_models 中 model_name=="docling" 的条目
    - engine_bindings 指向 docling 的条目（字符串或 id）→ 置 None 让 init_engines 回退默认 pypdfium2

    返回是否有变更（供调用方计入 save_config 判定，确保迁移落盘）。
    """
    import hashlib
    changed = False

    # 1. builtin_models 移除 docling
    for prov in config.get("engine_providers", []):
        bms = prov.get("builtin_models", [])
        before = len(bms)
        bms = [bm for bm in bms if bm.get("model_name") != "docling"]
        if len(bms) != before:
            prov["builtin_models"] = bms
            changed = True
            logger.info("迁移: 从 %s 移除 %d 个 docling builtin_model", prov.get("id"), before - len(bms))

    # 2. engine_models 移除 docling
    models = config.get("engine_models", [])
    before = len(models)
    models = [m for m in models if m.get("model_name") != "docling"]
    if len(models) != before:
        config["engine_models"] = models
        changed = True
        logger.info("迁移: 移除 %d 个 docling engine_model 条目", before - len(models))

    # 3. 绑定中指向 docling 的条目 → 置 None（让 init_engines 回退默认 pypdfium2）
    docling_id = int(hashlib.md5(b"engine:doc-local:docling").hexdigest()[:8], 16)
    bindings = config.get("engine_bindings", {})
    for key in list(bindings.keys()):
        val = bindings[key]
        if val == "docling" or val == docling_id:
            bindings[key] = None
            changed = True
            logger.info("迁移: 清除 %s 的 docling 绑定（回退默认）", key)

    return changed


def _migrate_remove_ebooklib(config: dict) -> bool:
    """启动迁移：清理 ebooklib 引擎残留（2026-08-26 起自研 epub-container 替代，AGPL 移除）。

    旧 config.yaml 可能残留：
    - engine_providers 的 builtin_models 中 model_name=="ebooklib"
    - engine_models 中 model_name=="ebooklib" 的条目
    - engine_bindings 指向 ebooklib 的条目（字符串 "ebooklib" 或旧 MD5 id）→ 置 None
      让 init_engines 回退默认 epub-container

    返回是否有变更（供调用方计入 save_config 判定，确保迁移落盘）。
    """
    import hashlib
    changed = False

    # 1. builtin_models 移除 ebooklib
    for prov in config.get("engine_providers", []):
        bms = prov.get("builtin_models", [])
        before = len(bms)
        bms = [bm for bm in bms if bm.get("model_name") != "ebooklib"]
        if len(bms) != before:
            prov["builtin_models"] = bms
            changed = True
            logger.info("迁移: 从 %s 移除 %d 个 ebooklib builtin_model", prov.get("id"), before - len(bms))

    # 2. engine_models 移除 ebooklib
    models = config.get("engine_models", [])
    before = len(models)
    models = [m for m in models if m.get("model_name") != "ebooklib"]
    if len(models) != before:
        config["engine_models"] = models
        changed = True
        logger.info("迁移: 移除 %d 个 ebooklib engine_model 条目", before - len(models))

    # 3. 绑定中指向 ebooklib 的条目 → 置 None（让 init_engines 回退默认 epub-container）
    old_id = int(hashlib.md5(b"engine:doc-local:ebooklib").hexdigest()[:8], 16)
    bindings = config.get("engine_bindings", {})
    for key in list(bindings.keys()):
        val = bindings[key]
        if val == "ebooklib" or val == old_id:
            bindings[key] = None
            changed = True
            logger.info("迁移: 清除 %s 的 ebooklib 绑定（回退默认 epub-container）", key)

    return changed


def init_engines(config: dict | None = None):
    """注册所有引擎"""
    config = config or {}

    # 启动迁移：清理旧版 faster-whisper / tesseract / docling / ebooklib 残留（变更计入末尾 save_config 判定）
    migrated_changed = _migrate_remove_faster_whisper(config)
    migrated_changed = _migrate_remove_tesseract(config) or migrated_changed
    migrated_changed = _migrate_remove_docling(config) or migrated_changed
    migrated_changed = _migrate_remove_ebooklib(config) or migrated_changed

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
        EngineRegistry.register("asr", "dashscope-asr", dashscope_asr)
        logger.info(f"ASR engines: dashscope-asr (key={'configured' if dashscope_key else 'NOT SET'})")
    except ImportError as e:
        logger.warning(f"DashScope ASR unavailable: {e}")
    except Exception as e:
        logger.warning(f"DashScope ASR init failed: {e}")

    # 必剪（Bilibili Bcut）— 免费云转写，无需 Key，中文优化
    try:
        from .asr_bcut import BcutASREngine
        bcut = BcutASREngine()
        bcut.name = "bcut-asr"
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

    # 自动绑定：仅在列表为空时初始化默认（bcut），不强制重排已有绑定
    # 修复：之前强制先删后插 bcut/whisper 导致每次启动 asr_changed=True → save_config 覆盖用户 key
    bindings = config.setdefault("engine_bindings", {})
    asr_list: list = bindings.get("asr", [])
    asr_old = list(asr_list)
    # 清理失效的绑定（ID 在 engine_models 中不存在，含已迁移移除的 faster-whisper id）
    asr_list = [mid for mid in asr_list if mid in existing_ids]
    # 仅在绑定列表为空时初始化默认绑定，尊重用户手动调整的顺序
    if not asr_list:
        asr_list = [mid_bcut]
        logger.info("Auto-initialized ASR bindings: bcut (default)")

    bindings["asr"] = asr_list
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

    # Doc（MIT/BSD 协议，无 AGPL 风险）
    from .doc_markitdown import MarkItDownEngine
    from .doc_docx import DocxEngine
    from .doc_pptx import PptxEngine
    from .doc_excel import ExcelEngine

    # Doc — MIT/BSD 协议引擎组
    from .docling import PyPdfium2Engine

    # 注：text_only 变体引擎（docling-text/python-docx-text/python-pptx-text）
    # 已删除 — 与完整版重复无意义。需要纯文字时由场景/用户自行选择，不拆引擎。
    # DoclingEngine 已移除（2026-08-02，依赖 torch ~2GB 无法打包，docling 打包版不可用）

    pypdfium2_eng = PyPdfium2Engine()
    pypdfium2_eng.name = "pypdfium2"
    EngineRegistry.register("doc", "pypdfium2", pypdfium2_eng)

    # PDF 整体转图片（2026-08-04）— 渲染每页为 PNG，供 OCR/多模态后续处理。
    # 绑定 doc:pdf 后替代 pypdfium2 文字提取（对纯图片/扫描 PDF 更稳）
    from .doc_pdf_image import PdfImageEngine
    pdf_image_eng = PdfImageEngine()
    pdf_image_eng.name = "pdf-image"
    EngineRegistry.register("doc", "pdf-image", pdf_image_eng)

    markit = MarkItDownEngine()
    markit.name = "markitdown"
    EngineRegistry.register("doc", "markitdown", markit)
    docx = DocxEngine()
    docx.name = "python-docx"
    EngineRegistry.register("doc", "python-docx", docx)
    pptx = PptxEngine()
    pptx.name = "python-pptx"
    EngineRegistry.register("doc", "python-pptx", pptx)
    xlsx = ExcelEngine()
    xlsx.name = "openpyxl"
    EngineRegistry.register("doc", "openpyxl", xlsx)
    # EPUB（自研解析器，纯标准库，替代 ebooklib AGPL-3.0）
    try:
        from .epub import EpubEngine
        epub_engine = EpubEngine()
        epub_engine.name = "epub-container"
        EngineRegistry.register("doc", "epub-container", epub_engine)
        logger.info("Doc engines: + epub-container (EPUB, 自研)")
    except ImportError as e:
        logger.warning(f"EPUB engine (epub-container) unavailable: {e}")
    except Exception as e:
        logger.warning(f"EPUB engine init failed: {e}")
    logger.info("Doc engines: pypdfium2 + markitdown + python-docx + python-pptx + openpyxl + epub-container")

    # Doc 引擎自动注册到 engine_models + engine_bindings，用户开箱即用
    # 对标 ASR 和 OCR 的自动注册逻辑
    import hashlib as _hl
    doc_bindings_changed = False
    doc_bindings = config.setdefault("engine_bindings", {})

    # 子场景 → 默认引擎的确定性 MD5 id
    # provider_id 必须和 build_engine_models() 的哈希键一致（engine:{provider}:{model}）
    _DOC_DEFAULTS: list[tuple[str, str, str]] = [
        ("doc:pdf", "doc-local", "pypdfium2"),      # PDF 快速转录（文字+嵌入原图）
        ("doc:word", "doc-local", "python-docx"),   # Word
        ("doc:ppt", "doc-local", "python-pptx"),    # PPT
        ("doc:excel", "doc-local", "openpyxl"),     # Excel
        ("doc:epub", "doc-local", "epub-container"),  # EPUB（自研，替代 ebooklib AGPL）
    ]
    for sub_scene, prov_id, model_name in _DOC_DEFAULTS:
        mid = int(_hl.md5(f"engine:{prov_id}:{model_name}".encode()).hexdigest()[:8], 16)
        if mid not in existing_ids:
            engine_models.append({
                "id": mid, "provider_id": prov_id,
                "model_name": model_name, "category": "doc",
            })
            existing_ids.add(mid)
            models_changed = True
            logger.info("Auto-added %s to engine_models (id=%d)", model_name, mid)
        # 自动绑定默认引擎（仅当子场景从未配置过，已配置的空列表也不覆盖）
        if sub_scene not in doc_bindings:
            doc_bindings[sub_scene] = mid
            doc_bindings_changed = True

    if doc_bindings_changed:
        logger.info("Auto-bound default doc engines: %s",
                    {k: v for k, v in doc_bindings.items() if k.startswith("doc:")})

    # 清理失效的 doc 绑定：不存在的子场景 key + id 解析不到模型的条目
    _VALID_DOC_SCENES = {s for s, _, _ in _DOC_DEFAULTS}
    emap = {m["id"]: m for m in engine_models}
    for key in list(doc_bindings.keys()):
        if not (key == "doc" or key.startswith("doc:")):
            continue
        # 扁平 doc 绑定也是可选的，但无效 id 必须修复
        if key not in _VALID_DOC_SCENES and key != "doc":
            doc_bindings.pop(key, None)
            doc_bindings_changed = True
            logger.info("清理失效 doc 绑定 key: %s", key)
            continue
        # id 解析校验：字符串直接查注册表，整数查 engine_models（兼容旧 list 格式）
        val = doc_bindings[key]
        if isinstance(val, list):
            # 空 list（DEFAULT_CONFIG 初始值 / 用户清空）视为无效，走修复分支
            val = val[0] if val else None
        if isinstance(val, str):
            ok = EngineRegistry.get("doc", val) is not None
        elif isinstance(val, int):
            bm = emap.get(val)
            ok = bm is not None and EngineRegistry.get("doc", bm.get("model_name", "")) is not None
        else:
            ok = False
        if not ok:
            # 回退到该子场景的默认引擎（扁平 doc 回退到 pypdfium2）
            fallback = "pypdfium2"
            for s, _, m in _DOC_DEFAULTS:
                if s == key:
                    fallback = m
                    break
            fallback_id = int(_hl.md5(
                f"engine:doc-local:{fallback}".encode()).hexdigest()[:8], 16)
            doc_bindings[key] = fallback_id
            doc_bindings_changed = True
            logger.info("修复失效 doc 绑定: %s → %s", key, fallback)

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

    # OCR 默认绑定（2026-08-04）：未绑定且 dashscope 有 key → 默认 qwen-vl-ocr-latest
    # （图片转文字专用模型，比 VL 大模型便宜）。避免「无可用 OCR 引擎」
    # （扫描件 OCR / 图片处理回退）。用户可在设置页改绑。
    if dashscope_ocr_key and not doc_bindings.get("ocr"):
        ocr_mid = int(_hl.md5("engine:dashscope:qwen-vl-ocr-latest".encode()).hexdigest()[:8], 16)
        if ocr_mid not in existing_ids:
            engine_models.append({
                "id": ocr_mid, "provider_id": "dashscope",
                "model_name": "qwen-vl-ocr-latest", "category": "ocr",
            })
            existing_ids.add(ocr_mid)
            models_changed = True
        doc_bindings["ocr"] = ocr_mid
        logger.info("Auto-bound OCR default: qwen-vl-ocr-latest (id=%d)", ocr_mid)

    # Tesseract 已从内置移除（2026-08-02，体积修剪 + 插件化方向）。
    # 需本地 OCR 请使用三方插件参考模板：backend/plugins/examples/tesseract_plugin.py

    # 向量化（embedding）— 语义检索用，**不作用于文件**（2026-10-26，search-retrieval.md §6.1 阶段 1）
    #
    # ⚠️ **无条件注册**（与 OCR 同款）：没配 Key 时也要列得出来，设置页才能显示「未配置」；
    #    抛点刻意放在真正要联网的那一刻（`DashScopeEmbeddingEngine.client`），
    #    见 embedding_dashscope.py 模块文档「没有 key 时不在这抛、也不在 init 里抛」。
    #
    # ⚠️ **刻意不写 engine_models / engine_bindings** —— 那是阶段 2 的事。本阶段它
    #    「注册了但没人绑定」= 等于不存在，对现有行为零变更（不触发末尾那次 save_config）。
    try:
        from .embedding_dashscope import DashScopeEmbeddingEngine
        emb_engine = DashScopeEmbeddingEngine(api_key=dashscope_ocr_key)
        EngineRegistry.register("embedding", emb_engine.name, emb_engine)
        logger.info("Embedding engines: dashscope-embedding (key=%s)",
                    "configured" if dashscope_ocr_key else "NOT SET")
    except ImportError as e:
        logger.warning(f"DashScope Embedding unavailable: {e}")
    except Exception as e:
        logger.warning(f"DashScope Embedding init failed: {e}")

    # 本地向量模型（2026-10-26，search-retrieval.md §6.1 阶段 4）—— 进程内 ONNX，
    # **免费 / 不上传 / 无需 Key**，是「离线可用」那档语义检索的全部实现。
    #
    # ⚠️ 与云端那支**同款纪律**：无条件注册（用户没装 onnxruntime 时，设置页也得能把它
    #    列出来并显示「装什么」），构造零副作用（不 import onnxruntime、不加载 22.8MB
    #    权重、不碰磁盘），抛点推迟到 `precheck()` / `warmup()` / `embed()`。
    # ⚠️ **单独一个 try**：与上面共用的话，dashscope 分支抛错会把本地引擎一起吞掉 ——
    #    而「云端不可用」与「本地不可用」是两件无关的事。
    # ⚠️ 重依赖仍不进 `pyproject.toml`、模型仍不随包分发 —— 口子原则的实质是
    #    「重依赖不进核心打包」，不是「代码必须放在 ~/.limo/plugins/ 下」。
    #    详见 embedding_local.py 模块文档的三条理由。
    try:
        from .embedding_local import LocalEmbeddingEngine
        local_emb = LocalEmbeddingEngine()
        EngineRegistry.register("embedding", local_emb.name, local_emb)
        logger.info("Embedding engines: local-bge-small (model=%s)", local_emb.model_id)
    except Exception as e:
        logger.warning(f"Local Embedding init failed: {e}")

    # 重排（rerank，2026-10-29）—— 对话侧检索的可选后置一层。
    #
    # ⚠️ 与 embedding 同款三条纪律：**无条件注册**（没配 Key 时设置页也要列得出来）、
    #    构造零副作用（抛点推迟到 `client` property / `precheck()`）、
    #    **刻意不写 engine_models / engine_bindings**（没人绑定 = 等于不存在，
    #    写进去会让末尾那次 save_config 判定翻转为真）。
    # ⚠️ 单独一个 try：与 embedding 那两支互不相关，共用时一支抛错会把另一支吞掉。
    # Key 复用 `dashscope_ocr_key`（与 embedding / OCR 同源，用户只配一处）。
    try:
        from .rerank_dashscope import DashScopeRerankEngine
        rerank_eng = DashScopeRerankEngine(api_key=dashscope_ocr_key)
        EngineRegistry.register("rerank", rerank_eng.name, rerank_eng)
        logger.info("Rerank engines: dashscope-rerank (key=%s)",
                    "configured" if dashscope_ocr_key else "NOT SET")
    except ImportError as e:
        logger.warning(f"DashScope Rerank unavailable: {e}")
    except Exception as e:
        logger.warning(f"DashScope Rerank init failed: {e}")

    # 持久化 — 只保存引擎相关字段，保护敏感信息（LLM Key 等）不被覆盖
    # migrated_changed 计入判定：faster-whisper/tesseract 迁移清理有真实变更时必须落盘
    if asr_changed or models_changed or doc_bindings_changed or migrated_changed:
        logger.info("init_engines: 引擎绑定/模型变更，持久化到 config.yaml (asr=%s models=%s doc_bindings=%s migrate=%s)",
                    asr_changed, models_changed, doc_bindings_changed, migrated_changed)
        # ⚠️ 此处曾有一段「从磁盘重读 config、用真实 Key 覆盖内存中可能的脱敏值」的防御块，
        #    2026-10-04 删除。三个理由，任一条都足以判它无效：
        #    ① `merge_sensitive(new_val: str, old_val: str) -> str` 收到的是两个 **dict**，
        #       且调用处**没有接收返回值** —— 纯函数 + 丢弃返回值 ⇒ 即使执行成功也是 no-op；
        #    ② 它读的 `_cfg_path` 是 `backend/config.yaml`（仓库内），而 `save_config` 写的是
        #       `~/.limo/config.yaml`（由 cfg["data_dir"] 推出）—— 读一个文件、写另一个；
        #    ③ 防御的**前提本就不成立**：内存 config 不会含脱敏值 —— 全仓 `mask_sensitive`
        #       调用点（config.py:28/38/50-52、model.py:133/538/742）全部只用于**构造响应**，
        #       且都先做 `dict(p)` / `deepcopy` 副本，无一写回 cfg。
        try:
            from app.core.config_io import save_config
            save_config(config)
        except OSError as e:
            logger.warning("init_engines: 无法保存配置（%s），继续运行", e)
