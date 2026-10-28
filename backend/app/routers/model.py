"""
模型管理路由 — 复刻 BiliNote 的 Provider + Model 方案
"""

import hashlib
import logging

import requests
from fastapi import APIRouter, Request, Depends
from pydantic import BaseModel

from app.core.dependencies import get_config as get_cfg, get_config_lock
from app.core.errors import AppError

from app.core.config_io import merge_sensitive, mask_sensitive
from app.core.registry import EngineRegistry, ENGINE_CATEGORIES
from app.core.url_utils import api_root
# 内置厂商默认表已下沉 core（P1-14）——`routers/config.py` 也要用它，
# 而路由模块之间互相 import 在本项目没有先例（共享常量一贯放 core/）。
from app.core.provider_defaults import PROVIDER_DEFAULTS, provider_requires_api_key

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Pydantic models ──

class ProviderData(BaseModel):
    name: str
    api_key: str = ""
    base_url: str = ""
    type: str = "custom"


class ProviderUpdateData(BaseModel):
    name: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    enabled: int | None = None


class AddModelData(BaseModel):
    provider_id: str
    model_name: str
    context_window: int | None = None  # 用户自定义上下文窗口（token），为空则使用厂商默认
    price_in: float | None = None      # 每百万 token 输入价格（元）
    price_out: float | None = None     # 每百万 token 输出价格（元）


class SceneBindData(BaseModel):
    generate: int | None = None   # enabled_models id
    chat: int | None = None
    image_process: int | None = None

# ── Helpers ──

def _get_cfg(cfg: dict) -> dict:
    return cfg


def _save_cfg(cfg: dict):
    from app.core.config_io import save_config
    save_config(cfg)


# ⚠️ `PROVIDER_DEFAULTS` 的定义已搬到 `app/core/provider_defaults.py`（P1-14）：
# `routers/config.py::detect_models` 此前**手写了第二份**（只有 deepseek / openai 两家），
# 是「同一集合写在两处、且覆盖集不同」的典型。这里是 import 不是定义——
# **不要在本文件重新写一份表**。


def _get_providers(cfg: dict) -> list[dict]:
    """获取所有 provider，自动补全内置供应商（返回副本，不污染原始 config）"""
    providers: list = [dict(p) for p in cfg.get("providers", [])]
    existing_ids = {p["id"] for p in providers}
    global_key = cfg.get("llm", {}).get("api_key", "")
    for pid, info in PROVIDER_DEFAULTS.items():
        if pid not in existing_ids:
            providers.append({
                "id": pid, "name": info["name"],
                "api_key": "", "base_url": info["base_url"],
                "type": info["type"], "enabled": 1,
            })
    # 同步全局 api_key 到默认 provider（仅 Key 格式匹配时同步）
    global_provider = cfg.get("llm", {}).get("provider", "deepseek")
    for p in providers:
        if p["id"] == global_provider and p.get("api_key") == "":
            # 防 Key 错配：DashScope Key 不同步到 DeepSeek
            if "sk-ws-" in global_key and global_provider == "deepseek":
                logger.warning("全局 API Key 疑似 DashScope 格式（sk-ws-...），不同步到 DeepSeek。请在设置页分别配置各厂商 Key。")
                continue
            if "sk-ant" in global_key and global_provider != "anthropic":
                logger.warning("全局 API Key 疑似 Anthropic 格式（sk-ant-...），不同步到 %s。", global_provider)
                continue
            p["api_key"] = global_key
    return providers


# ── Provider ──

@router.get("/providers")
def list_providers(request: Request, config=Depends(get_cfg)):
    providers = _get_providers(_get_cfg(config))
    # 返回脱敏副本，不污染原始 config（否则 save_config 会把脱敏值写回 config.yaml）
    result = []
    for p in providers:
        p = dict(p)
        if p.get("api_key"):
            p["api_key"] = mask_sensitive(p["api_key"])
        # 派生字段（**不落盘**，只给前端）：这一家的 Key 是不是必填。
        # ⚠️ 前端**不要**自己维护一份「哪些厂商不需要 Key」的名单 —— 那是第二份表，
        #    后端加一家而前端不认，失效形态是「输入框还在要求必填 Key」。
        p["requiresApiKey"] = provider_requires_api_key(p["id"])
        result.append(p)
    return result


@router.post("/providers")
def add_provider(data: ProviderData, request: Request, config=Depends(get_cfg), cfg_lock=Depends(get_config_lock)):
    with cfg_lock:
        cfg = _get_cfg(config)
        providers = cfg.get("providers", [])

        import re as _re
        pid = _re.sub(r'[^a-z0-9_-]', '', data.name.lower().replace(" ", "-"))[:32]
        if not pid:
            raise AppError("PROV_INVALID_NAME", "厂商名称无效，至少包含一个字母或数字", recoverable=False)
        providers.append({
            "id": pid, "name": data.name,
            "api_key": data.api_key, "base_url": data.base_url,
            "type": data.type, "enabled": 1,
        })
        cfg["providers"] = providers
        _save_cfg(cfg)
    return {"status": "ok", "id": pid}


@router.put("/providers/{provider_id}")
def update_provider(provider_id: str, data: ProviderUpdateData, request: Request, config=Depends(get_cfg), cfg_lock=Depends(get_config_lock)):
    with cfg_lock:
        cfg = _get_cfg(config)
        providers = cfg.get("providers", [])
        found = False
        for p in providers:
            if p["id"] == provider_id:
                found = True
                if data.name is not None: p["name"] = data.name
                if data.api_key is not None:
                    p["api_key"] = merge_sensitive(data.api_key, p.get("api_key", ""))
                if data.base_url is not None: p["base_url"] = data.base_url
                if data.enabled is not None: p["enabled"] = data.enabled
                break

        # 内置厂商：首次保存 Key 时自动注册到 providers 数组
        if not found and provider_id in PROVIDER_DEFAULTS:
            default = dict(PROVIDER_DEFAULTS[provider_id])
            default["id"] = provider_id
            if data.name is not None: default["name"] = data.name
            if data.api_key is not None: default["api_key"] = merge_sensitive(data.api_key, "")
            if data.base_url is not None: default["base_url"] = data.base_url
            if data.enabled is not None: default["enabled"] = data.enabled
            default["custom"] = False
            providers.append(default)
            found = True

        # 双向同步：key 也写一份到 llm.api_key（兼容旧代码）
        if data.api_key is not None and found:
            default_provider = cfg.get("llm", {}).get("provider", "")
            if provider_id == default_provider:
                old_llm_key = cfg.get("llm", {}).get("api_key", "")
                cfg.setdefault("llm", {})["api_key"] = merge_sensitive(data.api_key, old_llm_key)

        cfg["providers"] = providers
        _save_cfg(cfg)
    return {"status": "ok"}


@router.delete("/providers/{provider_id}")
def delete_provider(provider_id: str, request: Request, config=Depends(get_cfg), cfg_lock=Depends(get_config_lock)):
    """删除 LLM 厂商（仅允许自定义厂商，内置厂商拒绝删除）"""
    with cfg_lock:
        cfg = _get_cfg(config)
        providers = cfg.get("providers", [])
        target = next((p for p in providers if p["id"] == provider_id), None)
        if not target:
            raise AppError("PROVIDER_NOT_FOUND", "厂商不存在")
        if target.get("type") == "built-in":
            raise AppError("PROVIDER_BUILTIN", "不能删除内置厂商")
        cfg["providers"] = [p for p in providers if p["id"] != provider_id]
        _save_cfg(cfg)
    return {"status": "deleted"}


@router.post("/providers/{provider_id}/test")
def test_connection(provider_id: str, request: Request, config=Depends(get_cfg), cfg_lock=Depends(get_config_lock)):
    """测试连通性 — 尝试 GET /v1/models 验证 key 和 base_url"""
    cfg = _get_cfg(config)
    providers = {p["id"]: p for p in _get_providers(cfg)}
    prov = providers.get(provider_id)
    if not prov:
        return {"ok": False, "error": "Provider not found"}

    base_url = api_root(prov.get("base_url"))
    api_key = prov.get("api_key", "")
    if provider_requires_api_key(provider_id) and not api_key:
        return {"ok": False, "error": "未配置 API Key"}

    # ⚠️ keyless 厂商（本地推理）**不发 Authorization 头**：送一个占位 Bearer 给 Ollama
    # 无害但会让日志/抓包看起来像「配了个假 Key」。判据与上面同一处。
    headers = {"Authorization": f"Bearer {api_key}"} if provider_requires_api_key(provider_id) else {}

    try:
        resp = requests.get(
            f"{base_url}/models",
            headers=headers,
            timeout=15,
        )
        if resp.status_code in (200, 401):
            # 401 = key 有效但无权限列出模型，也算连通
            return {"ok": True, "status": resp.status_code}
        if resp.status_code in (404, 405):
            # 兜底：部分**用户自配的中转 / 自建网关**不实现 GET /v1/models（404/405）
            # → 改用最小对话请求真正验证 key 与 base_url 是否可达。
            # ⚠️ 这里曾把 opencode 列为该分支的例证——**已证伪**（2026-10-02）：它其实**实现**
            #    了 /v1/models（zen/go/v1 下实测 200 且免 key）。当初观察到的 404 是因为
            #    base_url 本身写错了（opencode.ai/go/v1 不存在），不是「网关不提供该端点」。
            #    保留本分支是给真正不实现该端点的网关用的——**不要再拿 opencode 当例子**，
            #    那会把一个 base_url 配置错误误诊为「网关特性」而放过去。
            try:
                probe_model = (cfg.get("llm_models", {}).get(provider_id, [{"id": "gpt-4o-mini"}])[0] or {}).get("id", "gpt-4o-mini")
                r2 = requests.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json={"model": probe_model, "max_tokens": 1,
                          "messages": [{"role": "user", "content": "ping"}]},
                    timeout=15,
                )
                if r2.status_code in (200, 401, 400):
                    # 400 = model 名非法但 key/路由可达（网关已鉴权并通过路由），视为连通
                    return {"ok": True, "status": f"models-{resp.status_code} chat-{r2.status_code}"}
                return {"ok": False, "error": f"chat HTTP {r2.status_code}: {r2.text[:200]}"}
            except requests.Timeout:
                return {"ok": False, "error": "连接超时（15s）"}
            except requests.ConnectionError:
                return {"ok": False, "error": f"无法连接 {base_url}"}
            except Exception as e:
                return {"ok": False, "error": f"chat 验证失败: {str(e)[:200]}"}
        return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except requests.Timeout:
        return {"ok": False, "error": "连接超时（15s）"}
    except requests.ConnectionError:
        return {"ok": False, "error": f"无法连接 {base_url}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# ── Model ──

@router.get("/models")
def list_enabled_models(request: Request, config=Depends(get_cfg)):
    """已启用的模型列表"""
    cfg = _get_cfg(config)
    return cfg.get("enabled_models", [])


@router.get("/models/refresh/{provider_id}")
def refresh_models(provider_id: str, request: Request, config=Depends(get_cfg), cfg_lock=Depends(get_config_lock)):
    """从供应商 API 拉取可用模型列表（LLM + 引擎通用）"""
    cfg = _get_cfg(config)
    # 先尝试 LLM providers，再尝试 engine providers
    providers = {p["id"]: p for p in _get_providers(cfg)}
    prov = providers.get(provider_id)
    is_llm = prov is not None
    if not prov:
        eng_provs = {p["id"]: p for p in cfg.get("engine_providers", [])}
        prov = eng_provs.get(provider_id)
    if not prov:
        raise AppError("PROVIDER_NOT_FOUND", "Provider not found")

    # Engine provider → 从 builtin_models 返回已知模型列表
    if not is_llm:
        for p in cfg.get("engine_providers", []):
            if p["id"] == provider_id and p.get("builtin_models"):
                return {"models": [{"id": bm["model_name"], "owned_by": provider_id, "desc": bm.get("desc", "")} for bm in p["builtin_models"]]}
        return {"models": []}

    # LLM provider → 调用 /models API 拉取
    # ⚠️ 与 `test_connection` **共用** `api_root()`（曾是两处逐字重复的同款启发式）。
    base_url = api_root(prov.get("base_url"))

    try:
        resp = requests.get(
            f"{base_url}/models",
            headers={"Authorization": f"Bearer {prov.get('api_key', '')}"},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("data", [])
            skip_words = ["embedding", "moderation", "dall-e", "babbage", "davinci", "omni"]
            # 阿里云 DashScope 平台托管了第三方模型（DeepSeek/Kimi/GLM 等），
            # 这些第三方模型有自己的官方渠道，混在阿里云模型列表里会让用户困惑
            dashscope_third_party = [
                "deepseek-", "deepseek/", "kimi-", "glm-", "minimax-",
                "vanchin/", "chatglm", "baichuan", "llama",
                "mistral", "mixtral", "yi-",
            ]
            models = []
            for m in items:
                mid = m.get("id", "")
                if any(w in mid.lower() for w in skip_words):
                    continue
                # DashScope：过滤托管第三方模型，仅保留千问系
                if provider_id == "dashscope":
                    owned_by = m.get("owned_by", "").lower()
                    if owned_by and owned_by not in ("dashscope", "qwen", "alibaba", ""):
                        continue
                    if any(mid.lower().startswith(p) for p in dashscope_third_party):
                        continue
                models.append({"id": mid, "owned_by": m.get("owned_by", "")})
            if models:
                return {"models": models[:50]}
    except Exception as e:
        logger.warning("模型刷新失败 (%s): %s", provider_id, str(e)[:120])

    # API 拉取失败 → 回退到 llm_models 预置列表
    preset = cfg.get("llm_models", {}).get(provider_id, [])
    if preset:
        return {"models": preset}
    return {"models": []}


# 各厂商多模态模型命名约定
# 格式：{"include": [...], "exclude": [...]} 或 ["pattern", ...]（纯 include）
# include 中的 "*" = 全匹配；exclude 用于排除已知不支持的子系列
_PROVIDER_IMAGE_RULES: dict[str, dict] = {
    "openai":     {"include": ["gpt-4o", "gpt-4.1", "o1", "o3", "o4"]},
    "anthropic":  {"include": ["claude"], "exclude": ["haiku"]},  # Claude 全系除 Haiku
    # 仅标记名称中明确含视觉标识的模型（qwen-vl / qwen-omni / qvq 等）
    # qwen3.x 系列命名规则模糊（同系列有纯文本也有多模态），不自动猜测，靠预设声明
    "dashscope":  {"include": ["qwen-vl", "qwen-omni", "qvq", "qwen3-vl", "ocr"],
                   "exclude": ["coder"]},  # ocr：OCR 专用模型（qwen3.5-ocr 等）本质是视觉识别，标 image 供 OCR 选择
    "google":     {"include": ["*"]},
    # deepseek：V4.1 起 flash 原生支持图像理解（旧 vl/vision 命名已下线，
    # 靠名字匹配是彻底 no-op）。改为「默认全标 + 排除已知纯文本子系列」。
    "deepseek":   {"include": ["*"], "exclude": ["pro", "reasoner", "coder", "embed"]},
    "zhipu":      {"include": ["glm-4v", "vl", "vision"]},
    "moonshot":   {"include": []},
}

# 视频直传能力 — 精确模型列表（评审必改：不能用 "qwen-vl" 前缀匹配——
# 会误标 qwen-vl-ocr-latest，它是 OCR 引擎不是视频模型）。dashscope 视频
# 模型见方案校准表；预设声明优先，此处兜底自动标注。
_PROVIDER_VIDEO_RULES: dict[str, list[str]] = {
    "dashscope": ["qwen3.8-max", "qwen3.7-plus", "qwen3-vl-flash",
                  "qwen-vl-max", "qwen3.5-omni-plus"],
}


def _detect_capabilities(provider_id: str, model_name: str) -> list[str]:
    """根据厂商约定检测模型能力标签（多模态/纯文本/视频直传）

    已知厂商按规则表自动检测；自定义/未知 provider 默认只标 text，
    用户可在 Settings 页面手动切换「多模态」标记。
    """
    caps = ["text"]
    name = model_name.lower()
    prov = provider_id.lower()

    rules = _PROVIDER_IMAGE_RULES.get(prov)
    if rules:
        include = rules.get("include", rules) if isinstance(rules, list) else rules.get("include", [])
        exclude = rules.get("exclude", []) if isinstance(rules, dict) else []
        # ⚠️ include 与 exclude 必须同时判，不能写成 if "*" in include / elif 前缀匹配 的互斥分支——
        # 那样 "*" 分支会跳过 exclude，排除表变成死代码（google 恰无 exclude 所以一直没暴露）。
        include_hit = ("*" in include) or any(p in name for p in include)
        if include_hit and not any(e in name for e in exclude):
            caps.append("image")
    # 视频直传能力：精确模型名匹配（不使用前缀——避免误标 qwen-vl-ocr-latest 等）
    video_list = _PROVIDER_VIDEO_RULES.get(prov)
    if video_list and model_name in video_list:
        caps.append("video")
    # 其他 provider 不猜测 — 用户自行标记

    return caps


def _lookup_preset_capabilities(cfg: dict, provider_id: str, model_name: str) -> list[str] | None:
    """从 llm_models 预设查找已知模型的能力标签。
    预设中声明的能力是确定可靠的，优先于自动检测规则。
    返回 None 表示预设中无此模型，调用方应回退到 _detect_capabilities()。
    """
    preset = cfg.get("llm_models", {}).get(provider_id, [])
    for m in preset:
        if m["id"] == model_name:
            return m.get("capabilities")
    return None


@router.post("/models")
def add_model(data: AddModelData, request: Request, config=Depends(get_cfg), cfg_lock=Depends(get_config_lock)):
    """启用一个模型"""
    with cfg_lock:
        cfg = _get_cfg(config)
        models: list = cfg.get("enabled_models", [])

        # 去重
        for m in models:
            if m["provider_id"] == data.provider_id and m["model_name"] == data.model_name:
                return {"status": "ok", "id": m["id"]}

        new_id = int(hashlib.md5(f"{data.provider_id}:{data.model_name}".encode()).hexdigest()[:8], 16)
        caps = _lookup_preset_capabilities(cfg, data.provider_id, data.model_name) \
               or _detect_capabilities(data.provider_id, data.model_name)
        entry = {
            "id": new_id, "provider_id": data.provider_id,
            "model_name": data.model_name, "capabilities": caps,
        }
        if data.context_window is not None:
            entry["context_window"] = data.context_window
        if data.price_in is not None:
            entry["price_in"] = data.price_in
        if data.price_out is not None:
            entry["price_out"] = data.price_out
        models.append(entry)
        cfg["enabled_models"] = models
        _save_cfg(cfg)
    return {"status": "ok", "id": new_id, "capabilities": caps}


class UpdateModelData(BaseModel):
    capabilities: list[str] | None = None   # 手动覆写能力标签
    context_window: int | None = None       # 上下文窗口（token），对话预算数据源
    price_in: float | None = None           # 每百万 token 输入价格（元）
    price_out: float | None = None          # 每百万 token 输出价格（元）


@router.put("/models/{model_id}")
def update_model(model_id: int, data: UpdateModelData, request: Request, config=Depends(get_cfg), cfg_lock=Depends(get_config_lock)):
    """更新已启用模型（能力标签 / 上下文窗口 / 价格——中转/自定义模型修正）"""
    with cfg_lock:
        cfg = _get_cfg(config)
        models = cfg.get("enabled_models", [])
        for m in models:
            if m["id"] == model_id:
                if data.capabilities is not None:
                    m["capabilities"] = data.capabilities
                if data.context_window is not None:
                    m["context_window"] = data.context_window
                if data.price_in is not None:
                    m["price_in"] = data.price_in
                if data.price_out is not None:
                    m["price_out"] = data.price_out
                _save_cfg(cfg)
                return {
                    "status": "ok", "id": model_id,
                    "capabilities": m.get("capabilities", ["text"]),
                    "context_window": m.get("context_window"),
                    "price_in": m.get("price_in"),
                    "price_out": m.get("price_out"),
                }
    raise AppError("MODEL_NOT_FOUND", "模型不存在")


@router.delete("/models/{model_id}")
def delete_model(model_id: int, request: Request, config=Depends(get_cfg), cfg_lock=Depends(get_config_lock)):
    """删除一个已启用模型"""
    with cfg_lock:
        cfg = _get_cfg(config)
        models = cfg.get("enabled_models", [])
        cfg["enabled_models"] = [m for m in models if m["id"] != model_id]
        _save_cfg(cfg)
    return {"status": "ok"}


# ── Scene bindings ──

@router.get("/scene-models")
def get_scene_models(request: Request, config=Depends(get_cfg)):
    cfg = _get_cfg(config)
    return cfg.get("scene_models", {"generate": None, "chat": None, "image_process": None})


@router.post("/scene-models")
def set_scene_models(data: SceneBindData, request: Request, config=Depends(get_cfg), cfg_lock=Depends(get_config_lock)):
    with cfg_lock:
        cfg = _get_cfg(config)
        cfg["scene_models"] = {"generate": data.generate, "chat": data.chat, "image_process": data.image_process}
        _save_cfg(cfg)
    return {"status": "ok"}


# ── Resolve model for use ──

# ── Engine Provider ──

class EngineProviderData(BaseModel):
    name: str
    api_key: str = ""
    base_url: str = ""
    type: str = "custom"


class AddEngineModelData(BaseModel):
    provider_id: str
    model_name: str
    category: str  # asr / ocr / doc


@router.get("/engine-providers")
def list_engine_providers(request: Request, config=Depends(get_cfg)):
    """内置 + 用户自定义引擎厂商（返回副本，不污染原始 config）"""
    cfg = _get_cfg(config)
    providers = [dict(p) for p in cfg.get("engine_providers", [])]
    # 同步 DashScope Key
    ds_key = cfg.get("cloud", {}).get("dashscope", "")
    for p in providers:
        if p["id"] == "dashscope" and ds_key:
            p["api_key"] = ds_key
    # 脱敏返回
    for p in providers:
        p["api_key"] = mask_sensitive(p.get("api_key", ""))
    return providers


@router.post("/engine-providers")
def add_engine_provider(data: EngineProviderData, request: Request, config=Depends(get_cfg), cfg_lock=Depends(get_config_lock)):
    with cfg_lock:
        cfg = _get_cfg(config)
        providers = cfg.get("engine_providers", [])
        import re as _re
        pid = _re.sub(r'[^a-z0-9_-]', '', data.name.lower().replace(" ", "-"))[:32]
        if not pid:
            raise AppError("ENG_PROV_INVALID_NAME", "引擎厂商名称无效，至少包含一个字母或数字", recoverable=False)
        providers.append({
            "id": pid, "name": data.name, "type": data.type,
            "api_key": data.api_key, "base_url": data.base_url,
            "capabilities": [],
        })
        cfg["engine_providers"] = providers
        _save_cfg(cfg)
    return {"status": "ok", "id": pid}


class UpdateEngineProviderBody(BaseModel):
    api_key: str | None = None
    base_url: str | None = None
    name: str | None = None

@router.put("/engine-providers/{provider_id}")
def update_engine_provider(provider_id: str, data: UpdateEngineProviderBody, request: Request, config=Depends(get_cfg), cfg_lock=Depends(get_config_lock)):
    with cfg_lock:
        cfg = _get_cfg(config)
        updated = False
        for p in cfg.get("engine_providers", []):
            if p["id"] == provider_id:
                if data.api_key is not None:
                    p["api_key"] = merge_sensitive(data.api_key, p.get("api_key", ""))
                    updated = True
                    # DashScope key 双向同步 → cloud.dashscope
                    if provider_id == "dashscope":
                        old_ds_key = cfg.get("cloud", {}).get("dashscope", "")
                        cfg.setdefault("cloud", {})["dashscope"] = merge_sensitive(data.api_key, old_ds_key)
                if data.base_url is not None:
                    p["base_url"] = data.base_url
                if data.name is not None:
                    p["name"] = data.name
                break
        if updated:
            _save_cfg(cfg)
    return {"status": "ok"}


@router.delete("/engine-providers/{provider_id}")
def delete_engine_provider(provider_id: str, request: Request, config=Depends(get_cfg), cfg_lock=Depends(get_config_lock)):
    with cfg_lock:
        cfg = _get_cfg(config)
        # 只允许删自定义的
        builtins = {"bcut", "dashscope", "pymupdf"}
        if provider_id in builtins:
            raise AppError("PROVIDER_BUILTIN", "不能删除内置引擎厂商")
        cfg["engine_providers"] = [p for p in cfg.get("engine_providers", []) if p["id"] != provider_id]
        cfg["engine_models"] = [m for m in cfg.get("engine_models", []) if m["provider_id"] != provider_id]
        _save_cfg(cfg)
    return {"status": "ok"}


# ── Engine Model ──

@router.get("/engine-models")
def list_engine_models(request: Request, config=Depends(get_cfg)):
    """列出所有引擎模型（builtin 自动注册 + 用户自定义），不修改全局 config"""
    cfg = _get_cfg(config)
    models = [dict(m) for m in cfg.get("engine_models", [])]  # 深拷贝每一项，不污染全局
    # 从 engine_providers 的 builtin_models 自动注册（只注册引擎类模型）
    existing = {(m["provider_id"], m["model_name"], m["category"]) for m in models}
    for prov in cfg.get("engine_providers", []):
        for bm in prov.get("builtin_models", []):
            if bm["category"] not in ENGINE_CATEGORIES:
                continue  # 跳过 LLM 模型
            key = (prov["id"], bm["model_name"], bm["category"])
            if key not in existing:
                mid = int(hashlib.md5(f"engine:{prov['id']}:{bm['model_name']}".encode()).hexdigest()[:8], 16)
                models.append({"id": mid, "provider_id": prov["id"], "model_name": bm["model_name"], "category": bm["category"]})
    # 注入 capabilities（来自 EngineRegistry）+ desc（来自 builtin_models）
    providers = {p["id"]: p for p in cfg.get("engine_providers", [])}
    for m in models:
        cat = m["category"]
        name = m["model_name"]
        # capabilities from EngineRegistry
        engine = EngineRegistry.get(cat, name)
        m["capabilities"] = getattr(engine, "capabilities", []) if engine else []
        # desc from builtin_models
        prov = providers.get(m["provider_id"])
        if prov:
            for bm in prov.get("builtin_models", []):
                if bm["model_name"] == name:
                    m["desc"] = bm.get("desc", "")
                    break
        if "desc" not in m:
            m["desc"] = ""
    return models


@router.post("/engine-models")
def add_engine_model(data: AddEngineModelData, request: Request, config=Depends(get_cfg), cfg_lock=Depends(get_config_lock)):
    with cfg_lock:
        cfg = _get_cfg(config)
        models = cfg.get("engine_models", [])
        mid = int(hashlib.md5(f"engine:{data.provider_id}:{data.model_name}".encode()).hexdigest()[:8], 16)
        # 去重
        for m in models:
            if m["id"] == mid:
                return {"status": "ok", "id": mid}
        models.append({"id": mid, "provider_id": data.provider_id, "model_name": data.model_name, "category": data.category})
        cfg["engine_models"] = models
        _save_cfg(cfg)
    return {"status": "ok", "id": mid}


@router.delete("/engine-models/{model_id}")
def delete_engine_model(model_id: int, request: Request, config=Depends(get_cfg), cfg_lock=Depends(get_config_lock)):
    with cfg_lock:
        cfg = _get_cfg(config)
        cfg["engine_models"] = [m for m in cfg.get("engine_models", []) if m["id"] != model_id]
        _save_cfg(cfg)
    return {"status": "ok"}


# ── Engine bindings ──
# key: "asr" | "ocr" | "doc:pdf" | "doc:word" | ...

class EngineBindData(BaseModel):
    bindings: dict[str, int | str | list | None] = {}

@router.get("/engine-bindings")
def get_engine_bindings(request: Request, config=Depends(get_cfg)):
    cfg = _get_cfg(config)
    raw = cfg.get("engine_bindings", {})

    # 构建 name→id 映射（字符串引擎名转数字 ID，前端只认数字）
    import hashlib
    name_to_id: dict[str, dict[str, int]] = {}
    for m in cfg.get("engine_models", []):
        name_to_id.setdefault(m["category"], {})[m["model_name"]] = m["id"]
    # 补充 EngineRegistry 中的引擎（已在模块顶部导入）
    for cat, store in EngineRegistry._stores.items():
        for name in store:
            if cat not in name_to_id or name not in name_to_id[cat]:
                mid = int(hashlib.md5(f"engine:reg:{cat}:{name}".encode()).hexdigest()[:8], 16)
                name_to_id.setdefault(cat, {})[name] = mid

    result: dict[str, int | None] = {}
    for key, val in raw.items():
        if isinstance(val, list) and len(val) > 0:
            v = val[0]
        else:
            v = val
        if isinstance(v, str):
            lookup = name_to_id.get(key) or name_to_id.get(key.split(':')[0], {})
            result[key] = lookup.get(v, None)
        elif isinstance(v, int):
            result[key] = v
        else:
            result[key] = None
    return result

@router.post("/engine-bindings")
def set_engine_bindings(data: EngineBindData, request: Request, config=Depends(get_cfg), cfg_lock=Depends(get_config_lock)):
    with cfg_lock:
        cfg = _get_cfg(config)
        def _wrap(v: int | str) -> list:
            return [v]
        # 合并而非替换：保留其他类别的绑定
        # null 表示"不修改此 key"而非"清空"——前端可能在 capabilities
        # 返回 null 时连带发送，不应覆盖有效绑定。显式清空请发 []。
        existing = cfg.get("engine_bindings", {})
        for key, val in data.bindings.items():
            if val is None:
                continue
            # 空列表 = 清除绑定（区别于 null = 不修改）
            if isinstance(val, list) and len(val) == 0:
                existing.pop(key, None)
            else:
                existing[key] = _wrap(val)
        cfg["engine_bindings"] = existing
        _save_cfg(cfg)
    return {"status": "ok"}


@router.get("/resolve-model/{model_id}")
def resolve_model(model_id: int, request: Request, config=Depends(get_cfg)):
    """根据 enabled_models id 解析出 provider + api_key + model_name"""
    cfg = _get_cfg(config)
    models = cfg.get("enabled_models", [])
    providers = {p["id"]: p for p in _get_providers(cfg)}

    for m in models:
        if m["id"] == model_id:
            prov = providers.get(m["provider_id"], {})
            api_key = prov.get("api_key", "")
            return {
                "provider": m["provider_id"],
                "model_name": m["model_name"],
                "api_key": mask_sensitive(api_key) if api_key else "",
                "base_url": prov.get("base_url", ""),
            }

    # 回退到全局配置
    llm = cfg.get("llm", {})
    provider = llm.get("provider", "deepseek")
    return {
        "provider": provider,
        "model_name": llm.get("model", {}).get(provider, "deepseek-flash"),
        "api_key": llm.get("api_key", ""),
        "base_url": llm.get("base_url", {}).get(provider, ""),
    }
