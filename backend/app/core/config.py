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
    "llm_models": {
        "deepseek": [
            {"id": "deepseek-v4-flash", "name": "V4 Flash", "desc": "1M上下文"},
            {"id": "deepseek-v4-pro",   "name": "V4 Pro",   "desc": "1M上下文"},
        ],
        "dashscope": [
            {"id": "qwen3.7-max",       "name": "Qwen3.7 Max",       "desc": "1M上下文·旗舰"},
            {"id": "qwen3.7-plus",      "name": "Qwen3.7 Plus",      "desc": "1M上下文·平衡"},
            {"id": "qwen3.6-flash",     "name": "Qwen3.6 Flash",     "desc": "1M上下文·轻量低成本"},
            {"id": "qwen3-coder-plus",  "name": "Qwen3 Coder Plus",  "desc": "1M上下文·代码专用"},
            {"id": "qwen-vl-max",       "name": "Qwen-VL Max",        "desc": "视觉理解"},
        ],
        "openai": [
            {"id": "gpt-4o",        "name": "GPT-4o",        "desc": "文本+视觉"},
            {"id": "gpt-4o-mini",   "name": "GPT-4o Mini",   "desc": "文本"},
        ],
        "anthropic": [
            {"id": "claude-sonnet-4-6", "name": "Sonnet 4.6", "desc": "文本+视觉"},
            {"id": "claude-opus-4-8",   "name": "Opus 4.8",   "desc": "文本+视觉"},
            {"id": "claude-haiku-4-5",  "name": "Haiku 4.5",  "desc": "文本"},
        ],
        "google": [
            {"id": "gemini-3.5-flash", "name": "Gemini 3.5 Flash", "desc": "文本+视觉"},
            {"id": "gemini-3.1-pro",   "name": "Gemini 3.1 Pro",   "desc": "文本+视觉"},
            {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash", "desc": "文本+视觉"},
        ],
        "zhipu": [
            {"id": "glm-5.2", "name": "GLM-5.2", "desc": "1M上下文"},
            {"id": "glm-4.7", "name": "GLM-4.7", "desc": "推理+编程"},
            {"id": "glm-4v-plus", "name": "GLM-4V-Plus", "desc": "视觉理解"},
        ],
        "moonshot": [
            {"id": "kimi-k2.6", "name": "Kimi K2.6", "desc": "编程+智能体"},
            {"id": "kimi-k2-thinking", "name": "Kimi K2 Thinking", "desc": "深度推理"},
        ],
    },
    "fetch": {"proxy": ""},
    "auth": {"bilibili": "", "youtube": "", "douyin": ""},
    "cloud": {"alibaba": "", "dashscope": ""},
    # 引擎厂商（内置 + 用户自定义）
    "engine_providers": [
        {"id": "dashscope", "name": "阿里云 DashScope", "type": "cloud", "base_url": "https://dashscope.aliyuncs.com", "api_key": "", "capabilities": ["asr", "ocr"],
         "builtin_models": [
             {"model_name": "paraformer-v2", "category": "asr", "desc": "离线语音转文字（支持大文件）"},
             {"model_name": "qwen-vl-max", "category": "ocr", "desc": "VL 模型 · 文字提取"},
             {"model_name": "qwen-vl-plus", "category": "ocr", "desc": "VL 模型 · 轻量"},
         ]},
        {"id": "bcut", "name": "B站必剪（免费云转写）", "type": "cloud", "base_url": "", "api_key": "", "capabilities": ["asr"],
         "builtin_models": [
             {"model_name": "bcut", "category": "asr", "desc": "免费云转写 · 中文优化"},
         ]},
        {"id": "faster-whisper", "name": "faster-whisper", "type": "local", "base_url": "", "api_key": "", "capabilities": ["asr"],
         "builtin_models": [
             {"model_name": "small", "category": "asr", "desc": "464MB · CPU"},
             {"model_name": "medium", "category": "asr", "desc": "1.5GB · CPU"},
             {"model_name": "large-v3", "category": "asr", "desc": "3GB · GPU"},
         ]},
        {"id": "pymupdf", "name": "PyMuPDF", "type": "local", "base_url": "", "api_key": "", "capabilities": ["doc"],
         "builtin_models": [
             {"model_name": "pymupdf4llm", "category": "doc", "desc": "PDF 文字+图片+矢量图（推荐）"},
             {"model_name": "pymupdf", "category": "doc", "desc": "PDF 文字层直读"},
             {"model_name": "pymupdf-text", "category": "doc", "desc": "PDF 仅文字（无图片）"},
             {"model_name": "markitdown", "category": "doc", "desc": "Office/EPUB 转 Markdown"},
             {"model_name": "python-docx", "category": "doc", "desc": "Word .docx 文字+表格+图片"},
             {"model_name": "python-docx-text", "category": "doc", "desc": "Word .docx 仅文字（无图片）"},
             {"model_name": "python-pptx", "category": "doc", "desc": "PPT .pptx 文字+图片+备注"},
             {"model_name": "python-pptx-text", "category": "doc", "desc": "PPT .pptx 仅文字+备注"},
             {"model_name": "openpyxl", "category": "doc", "desc": "Excel .xlsx 表格数据提取"},
             {"model_name": "ebooklib", "category": "doc", "desc": "EPUB 电子书 · 章节+图片+元数据"},
             {"model_name": "tesseract", "category": "ocr", "desc": "本地 Tesseract OCR · 文字提取"},
         ]},
    ],
    # 已启用的引擎模型（id → provider_id + model_name + category）
    "engine_models": [],
    # 引擎分配 — 绑定 engine_models 的 id（优先级列表，依次尝试直到成功）
    "engine_bindings": {
        "asr": [],
        "ocr": [],
        "doc": [],
        "doc:epub": [],
    },
    # LLM 不可用时是否自动降级为本地引擎（用户可在设置中关闭）
    "engine_auto_fallback": False,
    # Agent 工具配置 — 网络搜索 + 网页抓取
    "agent_tools": {
        "search": {"provider": "duckduckgo", "searxng_url": "http://127.0.0.1:18080", "tavily_api_key": ""},
        "fetch":  {"provider": "trafilatura"},
        "verify_mode": "llm",  # AI 审核默认验证方式: llm（模型知识）| web（联网搜索）
    },
    "data_dir": str(Path.home() / ".limo"),
    "log_level": "INFO",
}


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
    if config_path is None:
        # 环境变量 LIMO_CONFIG 可覆盖；向后兼容 MYBIGWORK_CONFIG
        config_path = os.environ.get(
            "LIMO_CONFIG",
            os.environ.get("MYBIGWORK_CONFIG", ""),
        ) or str(Path.home() / ".limo" / "config.yaml")

    # 旧目录迁移: ~/.mybigwork → ~/.limo
    _migrate_legacy_dir(Path.home() / ".mybigwork", Path.home() / ".limo")

    # 旧环境变量迁移: MYBIGWORK_CONFIG → LIMO_CONFIG（已在上面兼容读取）
    # 旧 data_dir 迁移: 如果用户之前的 config 中写的是 .mybigwork，自动改为 .limo
    if (Path.home() / ".limo").exists():
        DEFAULT_CONFIG["data_dir"] = str(Path.home() / ".limo")

    config = copy.deepcopy(DEFAULT_CONFIG)

    path = Path(config_path)
    if path.exists():
        with open(path, encoding="utf-8") as f:
            user_config = yaml.safe_load(f) or {}
        _deep_merge(config, user_config)

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

    return config


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
    """将 DEFAULT_CONFIG 中新增的 builtin_models 增量同步到已有配置。

    _deep_merge 对列表是整段替换，用户已有 config.yaml 的 engine_providers
    会完全覆盖 DEFAULT_CONFIG，导致新增的引擎模型（如 python-docx）丢失。
    此函数按 provider_id 匹配，补入 DEFAULT_CONFIG 中有而用户配置中没有的条目。
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
