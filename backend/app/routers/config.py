"""
配置路由 — GET/POST /api/config
"""

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, model_validator
from typing import Optional, Any

from app.core.errors import AppError

from app.core.cookie_parser import auto_parse
from app.core.config_io import save_config as _save_config, mask_sensitive, merge_sensitive

logger = logging.getLogger(__name__)
router = APIRouter()


def _mask_agent_tools(agent_tools: dict) -> dict:
    """脱敏 agent_tools 中的敏感字段（Tavily API Key 等）"""
    import copy
    at = copy.deepcopy(agent_tools)
    search = at.get("search", {})
    if search.get("tavily_api_key"):
        search["tavily_api_key"] = mask_sensitive(search["tavily_api_key"])
    return at


@router.get("/config")
async def get_config(request: Request, cfg=Depends(get_config_dep), cfg_lock=Depends(get_config_lock)):
    llm_key = cfg.get("llm", {}).get("api_key", "")
    return {
        "llm": {
            "provider": cfg.get("llm", {}).get("provider"),
            "api_key": mask_sensitive(llm_key) if llm_key else "",
            "model": cfg.get("llm", {}).get("model", {}).get(
                cfg.get("llm", {}).get("provider", "deepseek"), ""
            ),
        },
        # 优先读 scene_models（新格式），回退 llm_per_scene（旧格式）
        "llm_per_scene": cfg.get("scene_models") or cfg.get("llm_per_scene", {}),
        "cloud": cfg.get("cloud", {}),
        "fetch": {
            "proxy": cfg.get("fetch", {}).get("proxy", ""),
        },
        "auth": {
            "bilibili": mask_sensitive(cfg.get("auth", {}).get("bilibili", "")),
            "youtube": mask_sensitive(cfg.get("auth", {}).get("youtube", "")),
            "douyin": mask_sensitive(cfg.get("auth", {}).get("douyin", "")),
            # 返回解析后的关键字段列表
            "bilibili_parsed": cfg.get("auth", {}).get("bilibili_parsed"),
            "youtube_parsed": cfg.get("auth", {}).get("youtube_parsed"),
            "douyin_parsed": cfg.get("auth", {}).get("douyin_parsed"),
        },
        "data_dir": cfg.get("data_dir"),
        "agent_tools": _mask_agent_tools(cfg.get("agent_tools", {})),
        "target_language": cfg.get("target_language", "zh-CN"),
    }


class ConfigUpdate(BaseModel):
    """配置更新请求——锁顶层字段，子字段保持灵活"""
    llm: dict[str, Any] | None = None
    cloud: dict[str, Any] | None = None
    fetch: dict[str, Any] | None = None
    auth: dict[str, Any] | None = None
    log_level: str | None = None
    data_dir: str | None = None
    agent_tools: dict[str, Any] | None = None
    target_language: str | None = None

    model_config = {"extra": "forbid"}  # 拒绝未预期的顶层 key


@router.post("/config")
async def update_config(data: ConfigUpdate, request: Request):
    """保存配置，自动解析 Cookie"""
    with cfg_lock:

        # LLM 配置
        if data.llm:
            llm_data = data.llm
            # 脱敏保护：前端发回 *** 时保留旧 key
            if llm_data.get("api_key"):
                old_key = cfg.get("llm", {}).get("api_key", "")
                llm_data["api_key"] = merge_sensitive(llm_data["api_key"], old_key)
            # 如果前端发来的 model 是字符串，存到对应 provider 下
            if isinstance(llm_data.get("model"), str) and llm_data.get("model"):
                provider = llm_data.get("provider") or cfg.get("llm", {}).get("provider", "deepseek")
                existing_models = cfg.get("llm", {}).get("model", {})
                existing_models[provider] = llm_data["model"]
                llm_data["model"] = existing_models
            elif not llm_data.get("model"):
                llm_data.pop("model", None)
            cfg["llm"] = {**cfg.get("llm", {}), **llm_data}

        # Cloud 配置
        if data.cloud:
            cloud_data = data.cloud
            if cloud_data.get("dashscope"):
                old_key = cfg.get("cloud", {}).get("dashscope", "")
                cloud_data["dashscope"] = merge_sensitive(cloud_data["dashscope"], old_key)
            cfg["cloud"] = {**cfg.get("cloud", {}), **cloud_data}

        # Fetch 配置
        if data.fetch:
            cfg["fetch"] = {**cfg.get("fetch", {}), **data.fetch}

        # Auth — Cookie（脱敏保护 + 自动解析）
        if data.auth:
            auth = cfg.get("auth", {})
            raw_bili = data.auth.get("bilibili", "")
            raw_youtube = data.auth.get("youtube", "")
            raw_douyin = data.auth.get("douyin", "")

            if raw_bili and raw_bili.strip():
                raw_bili = merge_sensitive(raw_bili, auth.get("bilibili", ""))
                parsed = auto_parse(raw_bili)
                auth["bilibili"] = raw_bili
                auth["bilibili_parsed"] = {
                    "platform": parsed["platform"],
                    "cookies": parsed["cookies"],
                    "netscape": parsed["netscape"],
                }

            if raw_youtube and raw_youtube.strip():
                raw_youtube = merge_sensitive(raw_youtube, auth.get("youtube", ""))
                parsed_yt = auto_parse(raw_youtube)
                auth["youtube"] = raw_youtube
                auth["youtube_parsed"] = {
                    "platform": parsed_yt["platform"],
                    "cookies": parsed_yt["cookies"],
                    "netscape": parsed_yt["netscape"],
                }

            if raw_douyin and raw_douyin.strip():
                raw_douyin = merge_sensitive(raw_douyin, auth.get("douyin", ""))
                parsed_dy = auto_parse(raw_douyin)
                auth["douyin"] = raw_douyin
                auth["douyin_parsed"] = {
                    "platform": parsed_dy["platform"],
                    "cookies": parsed_dy["cookies"],
                    "netscape": parsed_dy["netscape"],
                }

            cfg["auth"] = auth

        # 目标语言
        if data.target_language is not None:
            cfg["target_language"] = data.target_language

        # Agent 工具配置 — Tavily API Key 脱敏保护
        if data.agent_tools:
            merged_at = {**cfg.get("agent_tools", {}), **data.agent_tools}
            new_search = data.agent_tools.get("search", {})
            old_search = cfg.get("agent_tools", {}).get("search", {})
            if new_search.get("tavily_api_key"):
                merged_at["search"]["tavily_api_key"] = merge_sensitive(
                    new_search["tavily_api_key"], old_search.get("tavily_api_key", ""))
            cfg["agent_tools"] = merged_at

        # 日志级别 — 立即生效
        if data.log_level is not None:
            import logging
            level = getattr(logging, data.log_level.upper(), logging.INFO)
            logging.getLogger().setLevel(level)
            logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
            logger.info("日志级别已更新为 %s", data.log_level.upper())

        # 持久化到 config.yaml
        _save_config(cfg)

    return {"status": "ok"}


@router.post("/llm/models")
async def detect_models(data: dict, request: Request):
    """从厂商 API 动态检测可用模型列表"""
    provider = data.get("provider", "")
    api_key = data.get("api_key", "")
    base_url = data.get("base_url", "")

    if not api_key:
        raise AppError("DETECT_NO_API_KEY", "请先填写 API Key")

    # 构建 base_url
    if not base_url:
            base_url = config.get("llm", {}).get("base_url", {}).get(provider, "")

    if not base_url:
        # 默认地址
        defaults = {"deepseek": "https://api.deepseek.com", "openai": "https://api.openai.com/v1"}
        base_url = defaults.get(provider, "")

    if not base_url:
        raise AppError("DETECT_NO_BASE_URL", "无法确定 API 地址")

    # URL 安全校验：只允许 http/https，必须有有效 hostname
    from urllib.parse import urlparse
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https"):
        raise AppError("DETECT_BAD_SCHEME", "API 地址必须以 http:// 或 https:// 开头")
    if not parsed.hostname:
        raise AppError("DETECT_BAD_URL", "API 地址格式无效（缺少主机名）")
    if "@" in parsed.hostname:
        raise AppError("DETECT_BAD_URL", "API 地址格式无效")

    # 确保 URL 以 /v1 结尾
    url = base_url.rstrip("/")
    if not url.endswith("/v1"):
        url += "/v1"
    url += "/models"

    try:
        import requests as req
        resp = req.get(url, headers={"Authorization": f"Bearer {api_key}"}, timeout=15)
        if resp.status_code != 200:
            raise AppError("DETECT_REQUEST_FAILED", f"请求失败 ({resp.status_code}): {resp.text[:200]}")

        data = resp.json()
        items = data.get("data", [])

        # 过滤：只保留 chat/completion 相关模型，排除 embedding/moderation/audio 等
        chat_keywords = ["chat", "gpt", "claude", "deepseek", "v4", "r1", "v3", "sonnet", "opus", "haiku", "4o", "4-turbo", "4.1", "reasoner"]
        models = []
        for m in items:
            mid = m.get("id", "")
            # 跳过明显非 chat 的模型
            if any(skip in mid for skip in ["embedding", "moderation", "tts", "whisper", "dall-e", "babbage", "davinci", "omni"]):
                continue
            models.append({"id": mid, "name": mid, "desc": m.get("owned_by", "")})

        # 去重并排序
        seen = set()
        unique = []
        for m in models:
            if m["id"] not in seen:
                seen.add(m["id"])
                unique.append(m)

        logger.info(f"Detected {len(unique)} models from {provider}")
        return {"models": unique[:50]}  # 最多50个
    except Exception as e:
        raise AppError("DETECT_FAILED", f"检测失败: {str(e)[:200]}")
