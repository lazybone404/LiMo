"""
配置加载
"""

import copy
import logging
import os
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


DEFAULT_CONFIG: dict[str, Any] = {
    "llm": {
        "provider": "deepseek",
        "api_key": "",
        "model": {"deepseek": "deepseek-v4-flash", "openai": "gpt-4o", "anthropic": "claude-sonnet-4-6"},
        "base_url": {"deepseek": "https://api.deepseek.com"},
    },
    # 模型预设 — 不同场景绑定不同预设
    # provider/model 为空时使用 llm 全局配置
    "llm_presets": [
        {"id": "generate", "label": "生成笔记", "provider": "", "model": "", "desc": "V4 Flash — 快速便宜"},
        {"id": "chat",     "label": "AI 对话", "provider": "", "model": "", "desc": "V4 Pro — 推理更强"},
    ],
    # 可用模型列表 — 前端设置页动态渲染
    # context_window：结构化上下文窗口（token），供对话上下文预算使用（四层：用户自定义 > 预设 > provider 默认表 > 保守 32k）
    # price_in/price_out：每百万 token 价格（元）——预设不填，用户在中转/自定义模型时于设置页填写后才启用费用显示
    "llm_models": {
        "deepseek": [
            {"id": "deepseek-v4-flash", "name": "V4 Flash", "desc": "1M上下文", "context_window": 1000000},
            {"id": "deepseek-v4-pro",   "name": "V4 Pro",   "desc": "1M上下文", "context_window": 1000000},
            # V4 Flash Vision（实验性，2026-08-21 上线）：图片理解，纯文本能力与 V4 Flash 相当；
            # 已知限制：API 端会把传入图片强制缩放至 ~800×800 量级（小图放大到 ~384×384），
            # 单张图片最多计 384 token（沿用 V4 Flash 定价）。OCR/细节提取场景注意分辨率损失。
            {"id": "deepseek-v4-flash-vision-exp", "name": "V4 Flash Vision", "desc": "视觉实验 · 图片理解(强制800×800)", "capabilities": ["text", "image"], "context_window": 1000000},
        ],
        "dashscope": [
            {"id": "qwen3.8-max",         "name": "Qwen3.8 Max",         "desc": "视觉+视频 · 旗舰",              "capabilities": ["text", "image", "video"], "context_window": 131072},
            {"id": "qwen3.7-max",         "name": "Qwen3.7 Max",         "desc": "纯文本 · 旗舰推理",              "capabilities": ["text"],                 "context_window": 131072},
            {"id": "qwen3.7-plus",        "name": "Qwen3.7 Plus",        "desc": "多模态+视频 · 日常推荐",        "capabilities": ["text", "image", "video"], "context_window": 131072},
            {"id": "qwen3.7-flash",       "name": "Qwen3.7 Flash",       "desc": "纯文本 · 极速响应",              "capabilities": ["text"],                 "context_window": 131072},
            {"id": "qwen3.6-flash",       "name": "Qwen3.6 Flash",       "desc": "纯文本 · 轻量快速",              "capabilities": ["text"],                 "context_window": 131072},
            {"id": "qwen-flash",          "name": "Qwen Flash",          "desc": "纯文本 · 最便宜",               "capabilities": ["text"],                 "context_window": 131072},
            {"id": "qwen-plus",           "name": "Qwen Plus",           "desc": "纯文本 · 低成本推理",            "capabilities": ["text"],                 "context_window": 131072},
            {"id": "qwen-turbo",          "name": "Qwen Turbo",          "desc": "纯文本 · 极低成本",              "capabilities": ["text"],                 "context_window": 131072},
            {"id": "qwen-vl-ocr-latest",  "name": "Qwen VL OCR",         "desc": "视觉 · 纯OCR识别",              "capabilities": ["text", "image"],          "context_window": 131072},
            {"id": "qwen3.5-ocr",         "name": "Qwen3.5 OCR",         "desc": "视觉 · 卡证票据OCR",            "capabilities": ["text", "image"],          "context_window": 131072},
            {"id": "qwen3.5-flash",       "name": "Qwen3.5 Flash",       "desc": "视觉 · Flash多模态",            "capabilities": ["text", "image"],          "context_window": 131072},
            {"id": "qwen3-vl-flash",      "name": "Qwen3 VL Flash",      "desc": "视觉+视频 · 最快最便宜（视频 2秒-1小时）", "capabilities": ["text", "image", "video"], "context_window": 131072},
            {"id": "qwen3-coder-plus",    "name": "Qwen3 Coder Plus",    "desc": "纯文本 · 代码生成",              "capabilities": ["text"],                 "context_window": 131072},
            {"id": "qwen-vl-max",         "name": "Qwen-VL Max",          "desc": "视觉+视频 · 旗舰图片理解（视频 2秒-20分）", "capabilities": ["text", "image", "video"], "context_window": 131072},
        ],
        "openai": [
            {"id": "gpt-4o",        "name": "GPT-4o",        "desc": "文本+视觉", "context_window": 128000},
            {"id": "gpt-4o-mini",   "name": "GPT-4o Mini",   "desc": "文本",       "context_window": 128000},
        ],
        "anthropic": [
            {"id": "claude-sonnet-4-6", "name": "Sonnet 4.6", "desc": "文本+视觉", "context_window": 200000},
            {"id": "claude-opus-4-8",   "name": "Opus 4.8",   "desc": "文本+视觉", "context_window": 200000},
            {"id": "claude-haiku-4-5",  "name": "Haiku 4.5",  "desc": "文本",       "context_window": 200000},
        ],
        "google": [
            {"id": "gemini-3.5-flash", "name": "Gemini 3.5 Flash", "desc": "文本+视觉", "context_window": 1000000},
            {"id": "gemini-3.1-pro",   "name": "Gemini 3.1 Pro",   "desc": "文本+视觉", "context_window": 1000000},
            {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash", "desc": "文本+视觉", "context_window": 1000000},
        ],
        "zhipu": [
            {"id": "glm-5.2",       "name": "GLM-5.2",     "desc": "1M上下文", "context_window": 1000000},
            {"id": "glm-4.7",       "name": "GLM-4.7",     "desc": "推理+编程", "context_window": 131072},
            {"id": "glm-4v-plus",   "name": "GLM-4V-Plus", "desc": "视觉理解",   "context_window": 131072},
        ],
        "moonshot": [
            {"id": "kimi-k2.6",       "name": "Kimi K2.6",       "desc": "编程+智能体", "context_window": 131072},
            {"id": "kimi-k2-thinking", "name": "Kimi K2 Thinking", "desc": "深度推理",     "context_window": 131072},
        ],
        # OpenCode Go — OpenAI 兼容网关（opencode.ai/go/v1，chat/completions），
        # 与 OpenCode Zen 共用 OPENCODE_API_KEY（Go 固定订阅 / Zen 按量）。订阅制：5h $12 / 周 $30 / 月 $60。
        # 模型清单来自官方 docs/go 的 openai-compatible 系（GPT/Grok 走 Responses API，与 LiMo chat.completions 不互通，
        # 不进预置，若有需求在设置页手动添加）。Qwen/GLM 高版本暂标 image 供图片处理/OCR 场景可选，
        # 以网关实际能力为准，不支持时在设置页取消「多模态」标记。
        "opencode": [
            {"id": "deepseek-v4-pro",   "name": "DeepSeek V4 Pro",   "desc": "推理更强",            "context_window": 131072},
            {"id": "deepseek-v4-flash", "name": "DeepSeek V4 Flash", "desc": "快速便宜",            "context_window": 131072},
            {"id": "qwen3.8-max",       "name": "Qwen3.8 Max",       "desc": "旗舰 · 可图片",       "capabilities": ["text", "image"], "context_window": 131072},
            {"id": "qwen3.7-max",       "name": "Qwen3.7 Max",       "desc": "旗舰推理 · 可图片",   "capabilities": ["text", "image"], "context_window": 131072},
            {"id": "qwen3.7-plus",      "name": "Qwen3.7 Plus",      "desc": "日常推荐 · 可图片",   "capabilities": ["text", "image"], "context_window": 131072},
            {"id": "qwen3.6-plus",      "name": "Qwen3.6 Plus",      "desc": "低配 · 可图片",       "capabilities": ["text", "image"], "context_window": 131072},
            {"id": "glm-5.3",           "name": "GLM-5.3",           "desc": "旗舰 · 可图片",       "capabilities": ["text", "image"], "context_window": 131072},
            {"id": "glm-5.2",           "name": "GLM-5.2",           "desc": "推理 · 可图片",       "capabilities": ["text", "image"], "context_window": 131072},
            {"id": "glm-5.1",           "name": "GLM-5.1",           "desc": "轻量推理",            "context_window": 131072},
            {"id": "kimi-k3",           "name": "Kimi K3",           "desc": "深度推理",            "context_window": 131072},
            {"id": "kimi-k2.7-code",    "name": "Kimi K2.7 Code",    "desc": "编程优化",            "context_window": 131072},
            {"id": "kimi-k2.6",         "name": "Kimi K2.6",         "desc": "编程+智能体",         "context_window": 131072},
            {"id": "minimax-m3",        "name": "MiniMax M3",        "desc": "通用",               "context_window": 131072},
            {"id": "minimax-m2.7",      "name": "MiniMax M2.7",      "desc": "通用",               "context_window": 131072},
            {"id": "mimo-v2.5",         "name": "MiMo-V2.5",         "desc": "高性价比",            "context_window": 131072},
        ],
    },
    "fetch": {"proxy": ""},
    "auth": {"bilibili": "", "youtube": "", "douyin": ""},
    "cloud": {"alibaba": "", "dashscope": ""},
    # 引擎厂商（内置 + 用户自定义）
    "engine_providers": [
        {"id": "dashscope", "name": "阿里云 DashScope", "type": "cloud", "base_url": "https://dashscope.aliyuncs.com", "api_key": "", "capabilities": ["asr", "ocr"],
         "builtin_models": [
             {"model_name": "paraformer-v2", "category": "asr", "desc": "离线语音转文字 · 支持大文件"},
             {"model_name": "qwen-vl-max", "category": "ocr", "desc": "旗舰视觉 · 文字提取"},
             {"model_name": "qwen-vl-plus", "category": "ocr", "desc": "轻量视觉 · 文字提取"},
             {"model_name": "qwen-vl-ocr-latest", "category": "ocr", "desc": "纯OCR识别 · 文档/表格/公式"},
             {"model_name": "qwen3-vl-flash", "category": "ocr", "desc": "快速视觉 · 最低成本"},
             {"model_name": "qwen3.5-ocr", "category": "ocr", "desc": "视觉 · 卡证票据OCR"},
         ]},
        {"id": "bcut", "name": "B站必剪（免费云转写）", "type": "cloud", "base_url": "", "api_key": "", "capabilities": ["asr"],
         "builtin_models": [
             {"model_name": "bcut", "category": "asr", "desc": "免费云转写 · 中文优化"},
         ]},
        {"id": "doc-local", "name": "本地文档引擎组", "type": "local", "base_url": "", "api_key": "", "capabilities": ["doc"],
         "builtin_models": [
             {"model_name": "pypdfium2", "category": "doc", "desc": "PDF 极速纯文字+表格+图片（BSD-3）"},
             {"model_name": "markitdown", "category": "doc", "desc": "Office/EPUB 转 Markdown"},
             {"model_name": "python-docx", "category": "doc", "desc": "Word .docx 文字+表格+图片"},
             {"model_name": "python-pptx", "category": "doc", "desc": "PPT .pptx 文字+图片+备注"},
             {"model_name": "openpyxl", "category": "doc", "desc": "Excel .xlsx 表格数据提取"},
             {"model_name": "epub-container", "category": "doc", "desc": "EPUB 电子书 · 章节+图片+元数据（自研）"},
         ]},
    ],
    # 已启用的引擎模型（id → provider_id + model_name + category）
    "engine_models": [],
    # 引擎分配 — 绑定 engine_models 的 id（只取第一个可用，失败直接报错，不再降级）
    "engine_bindings": {
        "asr": [],
        "ocr": [],
        "doc": [],
        "doc:epub": [],
    },
    # 对话上下文管理（C2-C4）— 预算档位 / 历史上限 / 文档引用模式
    "chat_context": {
        "budget_ratio": "auto",   # auto（窗口×0.7）| 0.5 | 0.7 | 0.85（context_window 用于输入的比例）
        "history_limit": 0,        # 历史轮数上限（0 = 不限制，按预算自动裁剪）
        "doc_mode": "auto",        # auto | full | summary（文档引用模式）
    },
    # Agent 工具配置 — 网络搜索 + 网页抓取
    "agent_tools": {
        "search": {"provider": "duckduckgo", "searxng_url": "http://127.0.0.1:18080", "tavily_api_key": ""},
        "fetch":  {"provider": "trafilatura"},
    },
    # 插件系统（EnginePlugin v1）
    "plugin_states": {},    # name → bool（false=禁用，重启后生效；未配置默认 enabled）
    "plugin_configs": {},   # name → dict（插件自身配置，经进程外 stdin JSON config 传给引擎）
    "data_dir": str(Path.home() / ".limo"),
    "log_level": "INFO",
}


def _resolve_data_dir() -> str:
    """确定数据目录，优先 ~/.limo，不可写则降级到 %LOCALAPPDATA%/LiMo"""
    import os as _os

    primary = Path.home() / ".limo"
    # 尝试创建 primary，成功则使用
    try:
        primary.mkdir(parents=True, exist_ok=True)
        # 写入探针验证权限
        (primary / ".write_test").touch()
        (primary / ".write_test").unlink()
        return str(primary)
    except OSError:
        pass

    # 降级 1: %LOCALAPPDATA%/LiMo（Windows 上始终可写）
    localappdata = _os.environ.get("LOCALAPPDATA", "")
    if localappdata:
        fallback = Path(localappdata) / "LiMo"
        try:
            fallback.mkdir(parents=True, exist_ok=True)
            return str(fallback)
        except OSError:
            pass

    # 降级 2: %APPDATA%/LiMo
    appdata = _os.environ.get("APPDATA", "")
    if appdata:
        fallback = Path(appdata) / "LiMo"
        try:
            fallback.mkdir(parents=True, exist_ok=True)
            return str(fallback)
        except OSError:
            pass

    # 最终降级：临时目录
    import tempfile
    return str(Path(tempfile.gettempdir()) / "LiMo")


def _migrate_legacy_dir(old: Path, new: Path) -> None:
    """从旧数据目录迁移到新目录"""
    if old.exists() and not new.exists():
        try:
            old.rename(new)
            import logging
            logging.getLogger(__name__).info("数据目录已迁移: %s → %s", old, new)
        except OSError:
            pass  # 跨盘符等无法 rename，保留旧目录


def load_config(config_path: str | None = None) -> dict[str, Any]:
    """加载配置，优先从文件，缺省用默认值"""
    # 旧目录迁移: ~/.mybigwork → ~/.limo
    _migrate_legacy_dir(Path.home() / ".mybigwork", Path.home() / ".limo")

    # 智能选择数据目录（~/.limo 不可写时降级到 %LOCALAPPDATA%/LiMo）
    resolved_dir = _resolve_data_dir()
    DEFAULT_CONFIG["data_dir"] = resolved_dir

    # config.yaml 路径：环境变量优先，否则放在数据目录
    if config_path is None:
        config_path = (
            os.environ.get("LIMO_CONFIG") or
            os.environ.get("MYBIGWORK_CONFIG") or
            str(Path(resolved_dir) / "config.yaml")
        )

    config = copy.deepcopy(DEFAULT_CONFIG)

    path = Path(config_path)
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                user_config = yaml.safe_load(f) or {}
            _deep_merge(config, user_config)
        except (OSError, yaml.YAMLError, PermissionError) as e:
            import logging
            logging.getLogger(__name__).warning(
                "无法加载 config.yaml (%s)，使用默认配置: %s", path, e
            )

        # 自动修正旧 data_dir
        if ".mybigwork" in config.get("data_dir", ""):
            config["data_dir"] = str(Path.home() / ".limo")

        # 启动时自动备份 — 防止后续写入损坏后可恢复
        backup_path = path.with_suffix(".yaml.bak")
        try:
            import shutil
            shutil.copy2(path, backup_path)
        except Exception:
            pass  # 备份失败不影响启动

    # 向后兼容：engine_bindings 旧格式 int → [int], None → []
    bindings = config.get("engine_bindings", {})
    for cat in ("asr", "ocr", "doc"):
        val = bindings.get(cat)
        if val is None:
            bindings[cat] = []
        elif isinstance(val, int):
            bindings[cat] = [val]
        elif not isinstance(val, list):
            bindings[cat] = []

    # 增量同步：DEFAULT_CONFIG 新增的 builtin_models + provider 条目自动补入已有配置
    _sync_missing_providers(config)
    _sync_builtin_models(config)
    _sync_llm_model_caps(config)

    return config


def _sync_llm_model_caps(config: dict):
    """将 DEFAULT_CONFIG 的 llm_models 增量同步到已有配置（只补不删）。

    _deep_merge 对列表是整段替换，旧 config.yaml 的 llm_models 不包含新增
    模型（如 dashscope qwen3.8-max）与新增能力（如视频模型的 video 标注）。
    此函数按 provider 匹配：
    1. 补入 DEFAULT 有而用户没有的模型条目（新增官方模型自动出现）
    2. 把 DEFAULT 已声明的能力补入用户配置对应模型（如 text → text+image+video），
       不碰用户自定义模型与手动覆写（只补 DEFAULT 明确有的）。
    """
    default_models = DEFAULT_CONFIG.get("llm_models", {})
    user_models = config.get("llm_models", {})
    for prov_id, def_list in default_models.items():
        if not isinstance(def_list, list):
            continue
        user_list = user_models.get(prov_id)
        if not isinstance(user_list, list):
            continue
        # 1) 补入 DEFAULT 有而用户没有的模型条目（新增模型自动出现）
        user_by_id = {m.get("id") for m in user_list if m.get("id")}
        for dm in def_list:
            if dm.get("id") and dm["id"] not in user_by_id:
                user_list.append(dict(dm))  # 深拷贝，不共享 DEFAULT 引用
                logger.info("配置同步: 新增 LLM 模型 %s/%s", prov_id, dm["id"])
        # 2) 能力补全（只补 DEFAULT 明确有的，不动用户已有标记）
        def_by_id = {m.get("id"): m.get("capabilities") for m in def_list if m.get("id")}
        for um in user_list:
            mid = um.get("id")
            if not mid:
                continue
            def_caps = def_by_id.get(mid)
            if not def_caps:
                continue
            user_caps = um.get("capabilities") or []
            missing = [c for c in def_caps if c not in user_caps]
            if missing:
                um["capabilities"] = user_caps + missing
                logger.info("配置同步: 模型 %s/%s 能力补全 %s", prov_id, mid, missing)


def _sync_missing_providers(config: dict):
    """将 DEFAULT_CONFIG 中已定义但用户 config.yaml 中缺失的 engine_providers 补入。

    _deep_merge 对列表是整段替换，旧 config.yaml 不包含新增 provider（如 bcut）。
    此函数按 id 匹配，补入 DEFAULT_CONFIG 中有而用户配置中没有的条目。
    """
    default_provs = DEFAULT_CONFIG.get("engine_providers", [])
    user_provs = config.get("engine_providers", [])
    user_ids = {p["id"] for p in user_provs}
    added = False
    for dp in default_provs:
        if dp["id"] not in user_ids:
            user_provs.append(dp)
            added = True
            logger = __import__("logging").getLogger(__name__)
            logger.info("Auto-added missing engine_provider: %s", dp["id"])
    if added:
        config["engine_providers"] = user_provs


def _deep_merge(base: dict, override: dict):
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def _sync_builtin_models(config: dict):
    """将 DEFAULT_CONFIG 的 builtin_models 增量同步到已有配置（只补不删）。

    只做两件保守的事，不碰用户自定义模型：
    1. 补入 DEFAULT 中有而用户没有的 builtin 模型（新增引擎自动出现）
    2. 移除已废弃的旧 provider 整组（pymupdf — AGPL 引擎组，id 已迁移为 doc-local）
       其 builtin_models 与 engine_models 条目随 provider 一起清理。
    """
    default_provs = DEFAULT_CONFIG.get("engine_providers", [])
    user_provs = config.get("engine_providers", [])
    default_by_id = {p["id"]: p for p in default_provs}

    for up in user_provs:
        dp = default_by_id.get(up["id"])
        if not dp:
            continue
        default_builtins = dp.get("builtin_models", [])
        user_builtins = up.get("builtin_models", [])
        existing = {(bm["model_name"], bm.get("category", "")) for bm in user_builtins}
        for dbm in default_builtins:
            key = (dbm["model_name"], dbm.get("category", ""))
            if key not in existing:
                user_builtins.append(dbm)
                logger.info("配置同步: 新增引擎模型 %s/%s", up["id"], dbm["model_name"])

    # ── 旧 provider 清理 ──
    # pymupdf（AGPL 引擎组）已迁移为 doc-local；faster-whisper 已从内置移除（需自行安装插件）
    _DEPRECATED_PROVIDERS = {"pymupdf", "faster-whisper"}
    config["engine_providers"] = [
        p for p in user_provs if p.get("id") not in _DEPRECATED_PROVIDERS
    ]

    # engine_models 残留清理：provider 已不存在的条目移除（含 pymupdf 全部引擎）
    engine_models = config.get("engine_models", [])
    live_prov_ids = {p.get("id") for p in config.get("engine_providers", [])}
    kept = [m for m in engine_models if m.get("provider_id") in live_prov_ids]
    dropped = len(engine_models) - len(kept)
    if dropped:
        logger.info("配置同步: 移除 %d 个失效引擎模型（provider 已废弃）", dropped)
    config["engine_models"] = kept


import hashlib as _hashlib

def build_engine_models(config: dict) -> list[dict]:
    """从 builtin_models 动态注册引擎模型，返回完整的 engine_models 列表。

    config.yaml 中 engine_models 可能为空或不完整（builtin 模型只在 API 响应中
    动态注册，不持久化），但 adapter/process 的引擎解析器需要完整列表来将绑定 ID
    映射到 provider/model。此函数封装 capabilities/list_engine_models 同款逻辑。
    """
    models = [dict(m) for m in config.get("engine_models", [])]
    existing = {(m["provider_id"], m["model_name"], m["category"]) for m in models}
    ENGINE_CATS = {"asr", "ocr", "doc"}
    for prov in config.get("engine_providers", []):
        for bm in prov.get("builtin_models", []):
            if bm["category"] not in ENGINE_CATS:
                continue
            key = (prov["id"], bm["model_name"], bm["category"])
            if key not in existing:
                mid = int(_hashlib.md5(
                    f"engine:{prov['id']}:{bm['model_name']}".encode()
                ).hexdigest()[:8], 16)
                models.append({"id": mid, "provider_id": prov["id"],
                               "model_name": bm["model_name"], "category": bm["category"]})
                existing.add(key)
    return models
