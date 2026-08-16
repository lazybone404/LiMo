"""公共模型解析 — 根据 enabled_models id 解析 provider/api_key/model 配置

generate / chat / process 三处统一使用，避免代码重复。
"""

import logging

logger = logging.getLogger(__name__)

# 未知模型上下文窗口的保守默认（token）
DEFAULT_CONTEXT_WINDOW = 32 * 1024


def resolve_model(config: dict, model_id: int) -> dict | None:
    """根据 enabled_models id 解析 LLM 调用所需的完整配置。

    返回:
        {
            "provider": "openai",
            "model": {"openai": "gpt-4o"},
            "api_key": "sk-xxx",
            "base_url": {"openai": "https://api.openai.com/v1"},
            "capabilities": ["text", "image"],  # 模型能力标签
        }
        找不到模型或 provider 无 key 时返回 None。
    """
    from app.routers.model import _get_providers

    models = config.get("enabled_models", [])
    providers = {p["id"]: p for p in _get_providers(config)}

    for m in models:
        if m["id"] == model_id:
            prov = providers.get(m["provider_id"], {})
            return {
                "provider": m["provider_id"],
                "model": {m["provider_id"]: m["model_name"]},
                "api_key": prov.get("api_key", ""),
                "base_url": {m["provider_id"]: prov.get("base_url", "")},
                "capabilities": m.get("capabilities", ["text"]),
            }
    return None


def resolve_llm_config(config: dict, model_id: int | None = None,
                       scene: str = "generate") -> dict:
    """解析 LLM 配置（generate / chat / process 共用）。

    返回 llm_config dict，包含 provider, model, api_key, base_url 等。
    调用方只需用返回的 config 构造 LLMService。
    """
    llm_config = dict(config.get("llm", {}))

    # 解析模型
    if not model_id:
        model_id = config.get("scene_models", {}).get(scene)
    if model_id:
        resolved = resolve_model(config, model_id)
        if resolved:
            llm_config.update(resolved)
        # 查找用户为该模型自定义的上下文窗口
        for m in config.get("enabled_models", []):
            if m.get("id") == model_id and m.get("context_window"):
                llm_config["model_context_window"] = m["context_window"]
                break

    # 回退 API key
    if not llm_config.get("api_key"):
        from app.routers.model import _get_providers
        default_provider = llm_config.get("provider", "deepseek")
        for p in _get_providers(config):
            if p["id"] == default_provider and p.get("api_key"):
                llm_config["api_key"] = p["api_key"]
                llm_config["base_url"] = {default_provider: p.get("base_url", "")}
                break

    return llm_config


def resolve_context_window(config: dict, model_id: int | None = None,
                           provider_id: str | None = None,
                           model_name: str | None = None) -> int:
    """解析模型上下文窗口（token）— 四层优先级：

    1. 用户自定义（enabled_models.context_window，设置页可编辑）
    2. llm_models 预设 context_window（config.py 结构化字段）
    3. provider 默认表（app.core.token.DEFAULT_CONTEXT）
    4. 保守默认 32k（未知模型 + 日志提示）

    对话上下文预算（budget = context_window × 0.7）的唯一数据源。
    """
    # 1. 用户自定义
    if model_id:
        for m in config.get("enabled_models", []):
            if m.get("id") == model_id and m.get("context_window"):
                return int(m["context_window"])

    # 由 model_id 解析 provider/model_name（未显式传入时）
    if model_id and not (provider_id and model_name):
        resolved = resolve_model(config, model_id)
        if resolved:
            provider_id = resolved["provider"]
            model_name = resolved["model"].get(provider_id)

    # 2. llm_models 预设（合并 DEFAULT + 用户配置，用户字段优先——
    #    兼容已有 config.yaml 的旧 llm_models 缺 context_window 新字段）
    if provider_id and model_name:
        from app.core.config import DEFAULT_CONFIG
        for src in (config, DEFAULT_CONFIG):
            for mm in src.get("llm_models", {}).get(provider_id, []):
                if mm.get("id") == model_name and mm.get("context_window"):
                    return int(mm["context_window"])

    # 3. provider 默认表
    if provider_id:
        from app.core.token import DEFAULT_CONTEXT
        if provider_id in DEFAULT_CONTEXT:
            return DEFAULT_CONTEXT[provider_id]

    # 4. 保守默认
    logger.info("模型未配置 context_window（provider=%s model=%s），使用保守默认 %d",
                provider_id, model_name, DEFAULT_CONTEXT_WINDOW)
    return DEFAULT_CONTEXT_WINDOW
