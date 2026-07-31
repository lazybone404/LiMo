"""
能力发现端点 — GET /api/capabilities

前端据此动态渲染，不再硬编码管道/引擎/选项列表。
新增管道或引擎时，只需后端注册，前端自动适配。
"""

from fastapi import APIRouter, Depends
from app.core.registry import AdapterRegistry, EngineRegistry
from app.core.dependencies import get_config
from app.generation.prompt import STYLE_MAP, DOC_STYLE_MAP, EXCEL_STYLE_MAP, AVAILABLE_OPTIONS, VIDEO_STYLE_KEYS
from app.generation.product_registry import to_capability_list

router = APIRouter()


@router.get("/capabilities")
async def capabilities(config=Depends(get_config)):
    has_dashscope = bool(config.get("cloud", {}).get("dashscope", ""))

    # 从 engine_models 动态构建 engineOptions（从 config 的 builtin_models 自动注册）
    engine_models = list(config.get("engine_models", []))
    existing = {(m["provider_id"], m["model_name"], m["category"]) for m in engine_models}
    seen_names = {(m["category"], m["model_name"]) for m in engine_models}  # 跨来源去重
    import hashlib
    ENGINE_CATS = {"asr", "ocr", "doc"}
    for prov in config.get("engine_providers", []):
        for bm in prov.get("builtin_models", []):
            if bm["category"] not in ENGINE_CATS:
                continue
            key = (prov["id"], bm["model_name"], bm["category"])
            name_key = (bm["category"], bm["model_name"])
            if key not in existing:
                mid = int(hashlib.md5(f"engine:{prov['id']}:{bm['model_name']}".encode()).hexdigest()[:8], 16)
                engine_models.append({"id": mid, "provider_id": prov["id"], "model_name": bm["model_name"], "category": bm["category"]})
                existing.add(key)
                seen_names.add(name_key)
    # 同样从 EngineRegistry 自动注册不在 engine_models 中的引擎
    for cat, store in EngineRegistry._stores.items():
        if cat not in ENGINE_CATS:
            continue
        for name in store:
            key = ("__registry__", name, cat)
            name_key = (cat, name)
            if key not in existing and name_key not in seen_names:
                mid = int(hashlib.md5(f"engine:reg:{cat}:{name}".encode()).hexdigest()[:8], 16)
                engine_models.append({"id": mid, "provider_id": "__registry__", "model_name": name, "category": cat})
                existing.add(key)
                seen_names.add(name_key)
    # engine_options 完全基于内存构建，不修改全局 config
    # 先从 EngineRegistry 获取每个引擎的 capabilities
    engine_caps: dict[str, dict[str, list[str]]] = {}
    for cat, store in EngineRegistry._stores.items():
        engine_caps[cat] = {}
        for name, eng in store.items():
            engine_caps[cat][name] = getattr(eng, "capabilities", [])

    # 从 builtin_models 构建 desc 查找表
    _desc_map: dict[str, dict[str, str]] = {}
    for prov in config.get("engine_providers", []):
        _desc_map[prov["id"]] = {}
        for bm in prov.get("builtin_models", []):
            _desc_map[prov["id"]][bm["model_name"]] = bm.get("desc", "")

    engine_options: dict[str, list[dict]] = {}
    for m in engine_models:
        cat = m["category"]
        if cat not in engine_options:
            engine_options[cat] = []
        caps = engine_caps.get(cat, {}).get(m["model_name"], [])
        desc = _desc_map.get(m["provider_id"], {}).get(m["model_name"], "")
        # 插件引擎的许可证信息
        _eng = EngineRegistry.get(cat, m["model_name"])
        _license = getattr(_eng, "license", None) if _eng else None
        engine_options[cat].append({
            "id": m["id"], "provider": m["provider_id"],
            "name": m["model_name"],
            "desc": desc,
            "capabilities": caps,
            "license": _license,
            "plugin": bool(_license),
        })

    # 引擎详情：名称 → { supported_modes, capabilities, ... }
    engine_details: dict[str, dict[str, dict]] = {}
    for cat, store in EngineRegistry._stores.items():
        engine_details[cat] = {}
        for name, eng in store.items():
            engine_details[cat][name] = {
                "supported_modes": getattr(eng, "supported_modes", []),
                "capabilities": getattr(eng, "capabilities", []),
            }

    # 构建 name→id 映射（前端只认数字 ID），支持 doc 及子场景 key
    name_to_id: dict[str, dict[str, int]] = {}
    for m in engine_models:
        name_to_id.setdefault(m["category"], {})[m["model_name"]] = m["id"]
        # 子场景 key（如 doc:word）也指向同一引擎的 ID
        name_to_id.setdefault(f'{m["category"]}:{m["model_name"]}', {})[m["model_name"]] = m["id"]

    # 返回标量格式（前端单选），内部存储为数组
    raw_bindings = config.get("engine_bindings", {})
    engine_bindings: dict[str, int | None] = {}
    for cat, val in raw_bindings.items():
        if isinstance(val, list) and len(val) > 0:
            v = val[0]
        else:
            v = val
        # 字符串引擎名 → 解析为数字 ID（先查子场景，再查大类）
        if isinstance(v, str):
            lookup = name_to_id.get(cat) or name_to_id.get(cat.split(':')[0], {})
            engine_bindings[cat] = lookup.get(v, None)
        elif isinstance(v, int):
            engine_bindings[cat] = v
        else:
            engine_bindings[cat] = None

    # ── 文件类型 → 可用引擎白名单（内置 + 插件动态追加）──
    # 内置引擎按名列出；插件引擎（EnginePlugin）通过 file_types 扩展名自动归入
    _DOC_SUBTYPES = {
        "pdf": ["docling", "pypdfium2"],
        "word": ["python-docx", "markitdown"],
        "ppt": ["python-pptx", "markitdown"],
        "excel": ["openpyxl", "markitdown"],
        "epub": ["ebooklib", "markitdown"],
    }
    # 扩展名 → 子类型（与 _ext_to_sub_scenario 对齐）
    _SUB_EXT = {"pdf": [".pdf"], "word": [".doc", ".docx"], "ppt": [".ppt", ".pptx"],
                "excel": [".xls", ".xlsx"], "epub": [".epub"]}
    file_type_map: dict[str, list[str]] = {}
    for st, wanted in _DOC_SUBTYPES.items():
        available = [n for n in wanted if EngineRegistry.get("doc", n)]
        # 动态追加插件引擎：EnginePlugin 暴露 file_types，匹配扩展名则归入
        for doc_name in EngineRegistry.list_category("doc"):
            if doc_name in available:
                continue
            eng = EngineRegistry.get("doc", doc_name)
            file_types = getattr(eng, "file_types", []) or []
            if file_types and any(ext in _SUB_EXT[st] for ext in file_types):
                available.append(doc_name)
        if available:
            file_type_map[st] = available

    # ── 子场景 key → 中文标签（与 fileTypeMap 子类型一一对应）──
    scenario_labels = {
        "doc:pdf": "PDF",
        "doc:word": "Word",
        "doc:ppt": "PPT",
        "doc:excel": "Excel",
        "doc:epub": "EPUB",
    }

    return {
        "adapters": [a.name for a in AdapterRegistry.all()],
        "engines": engine_details,
        "engineProviders": {
            "bcut": {
                "name": "B站必剪（免费云转写）",
                "type": "cloud",
                "keyConfigured": True,  # 免费，无需 Key
                "capabilities": ["asr"],
            },
            "dashscope": {
                "name": "阿里云 DashScope",
                "type": "cloud",
                "keyConfigured": has_dashscope,
                "capabilities": ["asr", "ocr"],
            },
            "faster-whisper": {
                "name": "faster-whisper（本地）",
                "type": "local",
                "capabilities": ["asr"],
            },
            "doc-local": {
                "name": "本地文档引擎组（Docling + PyPdfium2 + MarkItDown 等）",
                "type": "local",
                "capabilities": ["doc"],
            },
            "tesseract": {
                "name": "Tesseract（本地）",
                "type": "local",
                "capabilities": ["ocr"],
            },
        },
        "engineOptions": engine_options,
        "engineBindings": engine_bindings,
        "styles": list(STYLE_MAP.keys()),
        "docStyles": list(DOC_STYLE_MAP.keys()),
        "excelStyles": list(EXCEL_STYLE_MAP.keys()),
        "genOptions": AVAILABLE_OPTIONS,
        "llmModels": config.get("llm_models", {}),
        "products": to_capability_list(),
        "version": "0.1.3",
        # ── 新增：前端去硬编码元数据 ────────────────────────
        "fileTypeMap": file_type_map,
        "scenarioLabels": scenario_labels,
        "engineLabels": {
            "text": "文字", "table": "表格", "image": "图片",
            "ocr": "OCR", "pdf": "PDF", "office": "文档", "asr": "语音",
        },
        "styleLabels": {
            "none": "无",
            "minimal": "精简", "detailed": "全文精要", "academic": "学术", "life": "生活",
            "tutorial": "教程", "xiaohongshu": "小红书", "business": "商务",
            "meeting": "会议纪要",
            "task_oriented": "任务",
            "imrad": "IMRaD 精读",
        },
    }


@router.get("/engine-registry")
async def engine_registry():
    """返回 EngineRegistry 中所有已注册引擎的名称列表（按 category 分组）。
    前端据此动态渲染引擎选择器，不再硬编码引擎名称。
    新增引擎只需后端注册，前端自动发现。
    """
    cats = ["asr", "ocr", "doc"]
    result: dict[str, list[str]] = {}
    for cat in cats:
        result[cat] = sorted(EngineRegistry.list_category(cat))
    return result
