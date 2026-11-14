"""DeepSeek 平台端点的唯一住处：工具端点 / Files 端点 / Key 解析 / 错误翻译。

**为什么要有这个模块**（2026-11-14）：DeepSeek 在同一家域名下开了五个端点，
形态互不相同 —— `/v1/tools/ocr` 与 `/v1/tools/asr` 是 multipart 文件接口，
`/v1/tools/search` 与 `/v1/tools/fetch` 是 JSON 接口，`/files` 是上传即解析的
文件库。它们此前（或将要）散在 `agent/tools.py`、两个引擎、Files 服务里，
各写一份域名、各写一份 Key 回退链、各写一份错误码表 —— 换域名或加一级 Key
来源时只改一处，其余几处**静默 404 / 静默 401**，而代码读起来完全正常。

⚠️ **刻意不从 `providers[].base_url` 拼工具端点**。provider 的 base_url 是
`chat/completions` 的根，用户自建的 OpenAI 兼容代理通常只转发 chat，拿它去拼
`/v1/tools/ocr` 会 404。要过代理请配 `config.fetch.proxy`：`core/net.py` 会把它
注入成 `HTTP_PROXY`，`requests` 自动继承。这条判断最早写在 `agent/tools.py`，
现在由这一个模块承担。

⚠️ 请求一律用 `requests`（与 `engines/asr_cloud.py`、`engines/asr_bcut.py` 一致），
不引入第二套 HTTP 客户端。
"""

import logging

import requests

from app.core.errors import AppError
from app.core.provider_defaults import PROVIDER_DEFAULTS

logger = logging.getLogger(__name__)

#: 平台根地址。**不在这里写死域名** —— `PROVIDER_DEFAULTS` 才是「内置厂商地址」的
#: 真相源，复制一份就是第二真相源（该表已因同类问题被订正过两次）。
DEEPSEEK_BASE_URL: str = PROVIDER_DEFAULTS["deepseek"]["base_url"].rstrip("/")

#: 五个端点。路径本身是官方文档给的字面量，集中在这里是为了「换域名只改一处」。
TOOL_SEARCH_URL = f"{DEEPSEEK_BASE_URL}/v1/tools/search"
TOOL_FETCH_URL = f"{DEEPSEEK_BASE_URL}/v1/tools/fetch"
TOOL_OCR_URL = f"{DEEPSEEK_BASE_URL}/v1/tools/ocr"
TOOL_ASR_URL = f"{DEEPSEEK_BASE_URL}/v1/tools/asr"
FILES_URL = f"{DEEPSEEK_BASE_URL}/files"

#: 支持内置服务端工具（`deepseek_web_search` / `deepseek_read_url` / `title` /
#: `safety_check`）的模型。官方文档只把文档与聊天工具挂到 flash 上；pro 不标。
#: ⚠️ 这是**能力位之外的第二个判据**，不能合并：`enabled_models[].capabilities`
#: 是用户可编辑的标签（`text` / `image` / `document`），而「这家网关的服务端工具」
#: 是厂商侧的事实，用户改不了它。
DEEPSEEK_BUILTIN_TOOL_MODELS: frozenset[str] = frozenset({"deepseek-flash"})


def supports_deepseek_builtin(provider: str, model: str) -> bool:
    """当前对话模型能否用 DeepSeek 内置服务端工具（深度联网模式的唯一判据）。

    ⚠️ **一处判据、两个消费方**：`routers/capabilities.py` 据此下发
    `chatDeepWebAvailable`（前端决定显不显示那个按钮），`routers/chat.py` 据此决定
    走不走深度联网分支。两边各写一份表达式的失效形态是「按钮在那儿，点了却被静默
    忽略」—— 而这两个文件读的是同一份配置、同一份清单，没有理由分叉。
    """
    return (provider or "") == "deepseek" and (model or "") in DEEPSEEK_BUILTIN_TOOL_MODELS


#: 状态码 → (用户可读原因, 是否可重试)。官方错误表里出现的全部取值。
_STATUS_REASONS: dict[int, tuple[str, bool]] = {
    400: ("请求参数有误（检查文件格式与 purpose）", False),
    402: ("账户余额不足，请充值后重试", False),
    408: ("服务端处理超时，请稍后重试", True),
    413: ("文件超过体积上限", False),
    429: ("请求过于频繁，请稍后重试", True),
    500: ("服务端内部错误，请稍后重试", True),
    503: ("服务暂时不可用，请稍后重试", True),
    504: ("服务端处理超时，请稍后重试", True),
}


def deepseek_api_key(config: dict, explicit: str = "") -> str:
    """解析 DeepSeek Key —— 四级回退，顺序固定。

    1. `explicit`：调用方已经拿到的那把（引擎实例上的 `api_key`）
    2. `agent_tools.search.deepseek_api_key`：设置页「AI 对话 → 搜索」里填的
    3. `providers[id=="deepseek"].api_key`：模型厂商里填的
    4. `engine_providers[id=="deepseek"].api_key`：引擎厂商里填的

    后两级的理由相同：用户已经为其中一处配过一次，再让他为每个功能抄一遍是纯负担。
    ⚠️ **只认 `id`，不按 `base_url` 猜** —— 同一家用户可能配两条不同 base_url 的
    provider，用地址反推身份会在那天悄悄取错一把 Key。
    """
    key = (explicit or "").strip()
    if key:
        return key

    cfg = config or {}
    key = str(((cfg.get("agent_tools") or {}).get("search") or {}).get("deepseek_api_key") or "").strip()
    if key:
        return key

    for bucket in ("providers", "engine_providers"):
        for item in cfg.get(bucket) or []:
            if isinstance(item, dict) and item.get("id") == "deepseek":
                key = str(item.get("api_key") or "").strip()
                if key:
                    return key
    return ""


def check_response(resp: requests.Response, action: str) -> dict:
    """非 2xx → 翻成带原因的 `AppError`；2xx → 返回 JSON 体。

    `action` 是「在做什么」（如「OCR 识别」「上传文档」），进错误消息，
    这样同一句文案在三个功能里不会互相冒认。

    未知状态码原样带出（含上游响应体前 200 字），不硬猜原因 —— 猜错的提示
    比没有提示更糟：用户会照着一个不成立的原因去改配置。
    """
    if resp.status_code < 400:
        try:
            return resp.json()
        except ValueError as e:
            raise AppError(f"DEEPSEEK_{action}_BAD_JSON",
                           f"DeepSeek {action}返回了无法解析的内容",
                           detail=resp.text[:200], recoverable=False) from e

    reason, retryable = _STATUS_REASONS.get(
        resp.status_code, (f"服务返回 HTTP {resp.status_code}", False))
    raise AppError(
        f"DEEPSEEK_HTTP_{resp.status_code}",
        f"DeepSeek {action}失败：{reason}",
        detail=(resp.text or "")[:200],
        recoverable=retryable,
        hint="请检查 API Key、账户余额与文件大小" if resp.status_code in (401, 402, 403) else "",
    )
