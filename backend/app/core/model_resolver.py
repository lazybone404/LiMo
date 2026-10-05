"""公共模型解析 — 根据 enabled_models id 解析 provider/api_key/model 配置

generate / chat / process 三处统一使用，避免代码重复。
"""

import logging

logger = logging.getLogger(__name__)

# 未知模型上下文窗口的默认值（token）——第 4 层兜底，仅在**四层全部落空**时使用。
#
# ⚠️ 2026-10-02 由 32k 提高到 128k（用户判断）。理由与代价：
#   · 32k 是「宁可截断也不超窗」的过度保守值。而现代模型窗口普遍 ≥128k，
#     落到这里却按 32k 算 = 白扔 3/4 的上下文 —— 且**用户完全看不出来**，
#     只表现为「内容莫名被截断」。
#   · ⚠️ 触发面已被收窄：第 0 层 scene 回退补齐后，**必须 provider 与模型名都未知**
#     才够得着第 4 层（已知 provider 走第 3 层兜底表）——未知的是「用户自建的中转/
#     自定义模型」，这类今天基本都是 128k 起的模型。
#   · 代价：若真有 32k 窗口的模型走到这里，会**超窗失败**（API 报 context length
#     exceeded）而不是静默截断。选择接受——超窗是显式、可诊断的失败，且用户可在
#     设置页为该模型填 `context_window` 一行修好；而静默截断没有任何信号。
#     （与项目「失败显式化 / No silent degradation」一致。）
DEFAULT_CONTEXT_WINDOW = 128 * 1024


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
        # 上下文窗口：**完整四层解析**（用户自定义 > llm_models 预设 > provider 表 > 兜底）。
        #
        # ⚠️ 此前这里只查第 1 层（`enabled_models.context_window` 且非空），而第 1 层为空
        #    是常态 → 该字段大多数时候根本不出现，调用方各自再调一次
        #    `resolve_context_window`，**传的却是入参 model_id（可能为 None）**：
        #      · 本函数用的是**回退后**的 model_id（:55-56）→ LLMService 用真模型
        #      · 调用方传 None → 四层全落空 → 落到第 4 层兜底
        #    同一请求里模型是 1M 窗口、预算却按 32k 算，**差 31 倍且零报错**。
        #    收口到此处：调用方无需、也不该再自行解析（成对逻辑写两遍必然漂移）。
        llm_config["model_context_window"] = resolve_context_window(
            config, model_id, scene=scene,
        )

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
                           model_name: str | None = None,
                           scene: str = "generate") -> int:
    """解析模型上下文窗口（token）— 四层优先级：

    0. scene 回退（model_id 与 provider/model 都未给出时，取该场景绑定的模型）
    1. 用户自定义（enabled_models.context_window，设置页可编辑）
    2. llm_models 预设 context_window（config.py 结构化字段）
    3. provider 默认表（app.core.token.DEFAULT_CONTEXT）
    4. DEFAULT_CONTEXT_WINDOW（128k，未知模型 + 日志提示）

    对话上下文预算（budget = context_window × 0.7）的唯一数据源。

    ⚠️ 第 0 层不是四层之外的新来源，而是**让「解析哪个模型」这一步与
    `resolve_llm_config` 同源**——否则四层全部落空，直接返回第 4 层兜底值。
    """
    # 0. model_id 与 provider/model 都未给出 → 回退到该场景绑定的模型。
    #
    # ⚠️ 这条回退与 `resolve_llm_config`（:67-68 的 `scene_models[scene]`）**必须同源**。
    #    调用方普遍直接传 `req.model_id`，而用户用「场景默认模型」时它是 **None**：
    #      · resolve_llm_config 传 None → **回退到 scene_models** → LLMService 用真模型
    #      · 本函数此前没有这条 → 四层全落空 → **落到第 4 层兜底**
    #    后果是同一请求里**模型与预算各说各话**：deepseek-flash（1M 窗口）按兜底值算
    #    （当时是 32k ⇒ 差 31 倍；现为 128k ⇒ 差 8 倍），**且零报错**——只是文档被
    #    大量截断 / 合成被误判「窗口不足」。
    #    实测触发面：generate 非流式 :193 / 流式 :540 / synthesis 两处 全部命中。
    #
    # ⚠️ 判据是「**两个来源都为空**」而非「model_id 为空」：给了 provider/model 的调用方
    #    （reference_compressor 按**实际在用的模型**查询，可能不是场景模型）不能被覆盖。
    if not model_id and not (provider_id and model_name):
        model_id = config.get("scene_models", {}).get(scene)

    # 1. 用户自定义
    #
    # ⚠️ **两条支路缺一不可**（P1-09）：调用方分两类，此前只有第一类被覆盖 ——
    #   · 传 `model_id` 的（generate / synthesis / chat）：按下标查；
    #   · 传 `provider_id + model_name` 的（`reference_compressor` 按**实际在用的
    #     模型**查询，可能不是场景模型）：**整条第 1 层被跳过** → 用户在设置页给
    #     这个模型填的 `context_window` 对这类调用**完全不生效**，静默落到
    #     第 2/3/4 层（同一份配置下能差数倍，且零报错）。
    #   ⚠️ 判据必须是 `provider_id` **且** `model_name`，不能只按名字：`qwen-max`
    #   这类名字在各家网关都可能有，只按名字匹配会把别家的窗口安到这家的头上。
    #   ⚠️ 两个 `if` 而非 `if/elif`：按 id 没查到（老配置里缺这条）时，
    #   显式传了 provider/model 的调用方仍应有机会命中。
    if model_id:
        for m in config.get("enabled_models", []):
            if m.get("id") == model_id and m.get("context_window"):
                return int(m["context_window"])
    if provider_id and model_name:
        for m in config.get("enabled_models", []):
            if (m.get("provider_id") == provider_id
                    and m.get("model_name") == model_name
                    and m.get("context_window")):
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

    # 4. 兜底默认（连 provider 都未知——用户自建的中转/自定义模型）
    logger.info("模型未配置 context_window（provider=%s model=%s），使用默认 %d",
                provider_id, model_name, DEFAULT_CONTEXT_WINDOW)
    return DEFAULT_CONTEXT_WINDOW
