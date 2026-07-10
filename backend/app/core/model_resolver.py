"""公共模型解析 — 根据 enabled_models id 解析 provider/api_key/model 配置

generate / chat / process 三处统一使用，避免代码重复。
"""


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
    """解析 LLM 配置（generate / fact_check 共用）。

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
