"""
能力发现端点 — GET /api/capabilities

前端据此动态渲染，不再硬编码管道/引擎/选项列表。
新增管道或引擎时，只需后端注册，前端自动适配。
"""

from fastapi import APIRouter, Depends
from app.core.registry import (
    AdapterRegistry, EngineRegistry,
    ENGINE_CATEGORIES, ENGINE_CATEGORY_ORDER, ENGINE_CAPABILITY_LABELS,
)
from app.core.dependencies import get_config
from app.generation.prompt import STYLE_MAP, DOC_STYLE_MAP, EXCEL_STYLE_MAP, AVAILABLE_OPTIONS
# 对比维度预设随 capabilities 下发（同「引擎前端后端驱动」原则）：前端不硬编码预设名，
# 新增一套预设只需改 synthesis.py 一处。⚠️ 单一真相源——不要在这里重写一份列表。
from app.generation.synthesis import (
    CONTENT_TYPE_LABELS,
    CONTENT_TYPES,
    DIMENSION_PRESET_LABELS,
    DIMENSION_PRESETS,
    READER_INTENTS,
)
from app.generation.product_registry import to_capability_list
from app.routers.model import _get_providers
# 产物布局判据的家（`{mode}/v{N}.md`）——不要在本文件内联 md 文件路径
from app.storage.store import MARKDOWN_MODES, markdown_dir_has_version

router = APIRouter()

# 内置厂商名映射兜底（llm_models 有 key 但 providers 无该条时用；正常由 _get_providers 补全 name）
_LLM_PROVIDER_NAMES = {
    "deepseek": "DeepSeek",
    "dashscope": "阿里云 DashScope",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Google Gemini",
    "zhipu": "智谱 GLM",
    "moonshot": "Moonshot Kimi",
}


def _dimension_presets() -> list[dict]:
    """多篇合成的对比维度预设（键顺序 == 前端下拉顺序）。

    结构是**列表**而非 dict：下拉的语义是「有序选项」，列表把这个意图写进类型
    （dict 在 Python 3.7+ 与 JS 里也保序，但那要靠读者记得）。

    ⚠️ **单一真相源**：值一律从 `synthesis.DIMENSION_PRESETS` 取，**不要在这里
    手写一份列表**——那样改了合成侧、生成侧却按旧维度抽取，属静默错配。抽成
    独立函数是为了让测试能直接断「它与 synthesis 常量同源」。

    ⚠️ 每项 `list(v)` 拷贝：`DIMENSION_PRESETS` 是模块级常量，直接外泄同一对象
    引用会让调用方的一次 append 污染全局预设。
    """
    return [
        {"key": k, "label": DIMENSION_PRESET_LABELS[k], "dimensions": list(v)}
        for k, v in DIMENSION_PRESETS.items()
    ]


def _content_types() -> list[dict]:
    """材料体裁选项（合成对话框「高级」区的多选框）。

    ⚠️ 标签取 `CONTENT_TYPE_LABELS` 而**不是** `DIMENSION_PRESET_LABELS`：后者是
    「论文综述」——那是**产物形态**，当体裁名不通（体裁描述的是材料本身）。
    两组键相同、文案不同，是刻意分开的两份。
    """
    return [{"key": k, "label": CONTENT_TYPE_LABELS[k]} for k in CONTENT_TYPES]


def _reader_intents() -> list[dict]:
    """读者意图选项（合成对话框的主控件，键顺序 == 前端选项顺序）。

    `defaultContentType` 为 `None` = **不预设体裁**：「理解主题」「比较材料」是读者
    的动作，不指示材料是什么体裁。前端据此留空高级区，而不是替用户猜一个——
    静默预填 paper 对视频转写稿毫无意义，而用户看不出为什么。
    """
    return [
        {
            "key": k,
            "label": v["label"],
            "purpose": v["purpose"],
            "defaultContentType": v["default_content_type"],
        }
        for k, v in READER_INTENTS.items()
    ]


def _compute_missing_keys(config: dict) -> dict:
    """计算缺 key 项（P2.1 主动提示数据源）。

    - llm：llm_models 的厂商，对应 provider.api_key 为空（复用 model._get_providers——
      已含全局 llm.api_key 同步与 sk-ws/sk-ant 防错配；全局 key 非空会同步进 provider → 判已配）
    - engine：dashscope（cloud.dashscope 空 → OCR/ASR 云端引擎不可用）
    """
    providers_map = {p["id"]: p for p in _get_providers(config)}
    missing_llm = []
    for pid in config.get("llm_models", {}):
        p = providers_map.get(pid)
        if not p or not p.get("api_key"):
            missing_llm.append({
                "id": pid,
                "name": (p or {}).get("name") or _LLM_PROVIDER_NAMES.get(pid, pid),
            })
    missing_engine = []
    if not bool(config.get("cloud", {}).get("dashscope", "")):
        missing_engine.append("dashscope")
    return {"llm": missing_llm, "engine": missing_engine}


def _scan_interrupted_generations(config: dict) -> list[dict]:
    """扫描文档目录的 .generating 标记（生成中断残留）→ 前端提示。

    生成开始时 generate.py 写 marker，正常结束/取消/超时删除；
    应用重启/崩溃时残留 → 前端启动 Toast「上次生成未完成」。

    **判断收紧（防误报）**：仅当该文档**确实没有产物**（notes/polished 均不存在）
    才判为真正的中断；产物已生成但 marker 残留（清理失败 / 旧版遗留）是
    「假中断」——直接清理 marker，不提示。否则每次启动都会对已完成的
    文档重复弹「未完成生成」。
    """
    import json as _json
    from pathlib import Path
    data_dir = Path(config.get("data_dir", ""))
    results = []
    if not data_dir.exists():
        return results
    for doc_dir in data_dir.iterdir():
        marker = doc_dir / ".generating"
        if not marker.is_file():
            continue
        # 假中断自愈：产物已生成（生成实际完成）→ marker 是残留，清理不提示
        # ⚠️ 判据走 `store.markdown_dir_has_version`，**不要**在这里内联 md 文件路径：
        # 产物布局是 `{mode}/v{N}.md`，内联的 `(doc_dir / "notes.md").is_file()` 会在
        # 布局改版后永远为假——症状是「已生成完的文档被误报成生成中断残留」，
        # 且那条提示永远清不掉（静默类 bug）。
        has_product = any(markdown_dir_has_version(doc_dir, m) for m in MARKDOWN_MODES)
        if has_product:
            try:
                marker.unlink()
            except OSError:
                pass
            continue
        try:
            meta = _json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, _json.JSONDecodeError):
            meta = {}
        results.append({
            "tx_id": meta.get("tx_id", ""),
            "started_at": meta.get("started_at", ""),
            "dir_name": doc_dir.name,
        })
    return results


@router.get("/capabilities")
async def capabilities(config=Depends(get_config)):
    has_dashscope = bool(config.get("cloud", {}).get("dashscope", ""))

    # 从 engine_models 动态构建 engineOptions（从 config 的 builtin_models 自动注册）
    engine_models = list(config.get("engine_models", []))
    existing = {(m["provider_id"], m["model_name"], m["category"]) for m in engine_models}
    seen_names = {(m["category"], m["model_name"]) for m in engine_models}  # 跨来源去重
    import hashlib
    # 扩展点：新增插件类别改 `core/registry.ENGINE_CATEGORIES`（加载机制本身类别无关，
    # 分派骨架按 category 查表）
    for prov in config.get("engine_providers", []):
        for bm in prov.get("builtin_models", []):
            if bm["category"] not in ENGINE_CATEGORIES:
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
        if cat not in ENGINE_CATEGORIES:
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
        # 插件引擎元数据（is_plugin 判定，不用 bool(_license) — 内置引擎也可能带 license 误标）
        _eng = EngineRegistry.get(cat, m["model_name"])
        _license = getattr(_eng, "license", None) if _eng else None
        _is_plugin = bool(getattr(_eng, "is_plugin", False)) if _eng else False
        engine_options[cat].append({
            "id": m["id"], "provider": m["provider_id"],
            "name": m["model_name"],
            "desc": desc,
            "capabilities": caps,
            "license": _license,
            "plugin": _is_plugin,
            "displayName": (getattr(_eng, "display_name", "") or m["model_name"]) if _eng else m["model_name"],
            "userDesc": getattr(_eng, "user_desc", "") if _eng else "",
            "configSchema": getattr(_eng, "config_schema", []) if _is_plugin else [],
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
        "pdf": ["pypdfium2"],
        "word": ["python-docx", "markitdown"],
        "ppt": ["python-pptx", "markitdown"],
        "excel": ["openpyxl", "markitdown"],
        "epub": ["epub-container", "markitdown"],
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
            "doc-local": {
                "name": "本地文档引擎组（PyPdfium2 + MarkItDown + python-docx 等）",
                "type": "local",
                "capabilities": ["doc"],
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
        "version": "0.5.0",
        # ── 新增：前端去硬编码元数据 ────────────────────────
        "fileTypeMap": file_type_map,
        "scenarioLabels": scenario_labels,
        # 引擎能力位 → 中文标签（取自 core/registry，前端 EngineSelect 消费）
        "engineLabels": ENGINE_CAPABILITY_LABELS,
        # ⚠️ 这个 dict 是**手写**的，不会随三张 MAP（styles/docStyles/excelStyles）
        # 自动更新——新增风格档时**必须同时改这里**，否则按钮标签静默退化成
        # 英文 key（`GenPanel` 用 `styleLabels[s] || s`）。pi-docs 方案曾误以为
        # 「三张 MAP 会自动带出」，实际带出的只是 key 列表，标签得手写。
        "styleLabels": {
            "none": "无",
            # `plain` = 2026-10-05 起的全局默认档（Excel 除外，见 EXCEL_STYLE_MAP）
            "plain": "通俗",
            "minimal": "精简", "detailed": "全文精要", "academic": "学术", "life": "生活",
            "tutorial": "教程", "xiaohongshu": "小红书", "business": "商务",
            "meeting": "会议纪要",
            "task_oriented": "任务",
            "imrad": "IMRaD 精读",
        },
        # 多篇合成的对比维度预设（前端对话框「高级」区下拉 + 维度 textarea 预填）
        # ⚠️ 第一项即默认选中项（前端取 [0]）——顺序由 synthesis 侧保证。
        "dimensionPresets": _dimension_presets(),
        # 材料体裁（高级区多选框；`defaultContentType` 建议值由 readerIntents 给）
        "contentTypes": _content_types(),
        # 读者意图（对话框主控件）——决定 task 段的「读者目的」
        "readerIntents": _reader_intents(),
        # ── 未完成生成标记（应用重启/崩溃时残留，前端启动提示）──
        "interruptedGenerations": _scan_interrupted_generations(config),
        # ── 缺 key 项（P2.1 主动提示：配置向导第三屏 + 操作前 toast）──
        "missingKeys": _compute_missing_keys(config),
    }


@router.get("/engine-registry")
async def engine_registry():
    """返回 EngineRegistry 中所有已注册引擎的名称列表（按 category 分组）。
    前端据此动态渲染引擎选择器，不再硬编码引擎名称。
    新增引擎只需后端注册，前端自动发现。
    """
    # 顺序取自共享常量的 **ORDER 视图**（不是 set——遍历顺序是界面顺序）
    result: dict[str, list[str]] = {}
    for cat in ENGINE_CATEGORY_ORDER:
        result[cat] = sorted(EngineRegistry.list_category(cat))
    return result
