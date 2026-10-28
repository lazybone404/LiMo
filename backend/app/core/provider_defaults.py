"""内置 LLM 厂商的默认元信息（名称 / base_url / 类型）。

⚠️ **这个模块是「内置厂商有哪些、地址是什么」的单一真相源**（P1-14）。

它原本住在 `routers/model.py` 里，而 `routers/config.py::detect_models` 在
「拿不到 base_url」时**手写了第二份**（只含 deepseek / openai 两家）：

    defaults = {"deepseek": "https://api.deepseek.com",
                "openai": "https://api.openai.com/v1"}

于是同一个厂商的地址有两处定义，且**两处的覆盖集不同**——`/llm/models` 端点
对 dashscope / google / zhipu / moonshot / opencode / anthropic 全部落空，
报「无法确定 API 地址」，而这**看代码看不出来**（默认表就在旁边，只是短了一截）。

⚠️ 下沉到 `core/` 而不是让 `config.py` 直接 `from app.routers.model import ...`：
路由之间互相 import 在本项目没有先例，共享常量一贯放 `core/`
（对照 `core/resource_id.py`、`core/token.py`）。路由层只做 HTTP 搬运。
"""

PROVIDER_DEFAULTS = {
    "deepseek": {"name": "DeepSeek", "base_url": "https://api.deepseek.com", "type": "built-in"},
    "dashscope": {"name": "阿里云 DashScope", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "type": "built-in"},
    "openai": {"name": "OpenAI", "base_url": "https://api.openai.com/v1", "type": "built-in"},
    "anthropic": {"name": "Anthropic", "base_url": "https://api.anthropic.com", "type": "built-in"},
    # Google Gemini — 走 **OpenAI 兼容层**，base_url 必须带 `/openai` 后缀（2026-10-02 订正）。
    # ⚠️ 此前写的是 `.../v1beta`（**原生 API** 形态），OpenAI SDK 会据此发
    #    `POST .../v1beta/chat/completions` —— 原生端点**不存在该路径** → 整条 LLM 链路必然不通。
    #    官方文档（ai.google.dev/gemini-api/docs/openai）给的兼容层 base_url 即
    #    `https://generativelanguage.googleapis.com/v1beta/openai`（**无尾斜杠**）。
    #    三条独立旁证：hex.pm ex_llm 的官方文档转录、docs.api7.ai 的 ai-gateway 配置、
    #    googleapis/python-genai#2868（官方 SDK 仓库称该路径为 "hosted OpenAI-compatibility layer"）。
    # ⚠️ 鉴权：兼容层接受标准 `Authorization: Bearer <key>`（官方 REST 示例 + Azure 文档均如此），
    #    故**不需要**为原生 API 的 `?key=` 查询参数做任何特殊分支——这正是选兼容层的原因。
    #    （`routers/model.py` 中 :307 附近的旧注释称「Google 鉴权走 ?key=」，那是**原生 API**
    #     的性质，已随该次改动订正。）
    "google": {"name": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "type": "built-in"},
    "zhipu": {"name": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4", "type": "built-in"},
    "moonshot": {"name": "Moonshot Kimi", "base_url": "https://api.moonshot.cn/v1", "type": "built-in"},
    # OpenCode Go — OpenAI 兼容网关（chat/completions，Go 固定订阅 / Zen 按量，共用 OPENCODE_API_KEY）。
    # ⚠️ base_url 实测校正（2026-10-02）：正确端点是 https://opencode.ai/zen/go/v1。
    #    此前写的是 https://opencode.ai/go/v1 —— 实测 **404**，即该 provider 的**每一个请求
    #    都打到不存在的路径**（自配 day one 起就没通过）。对照实验（同一台机器、相隔数秒）：
    #      GET https://opencode.ai/go/v1/models      → 404
    #      GET https://opencode.ai/zen/go/v1/models  → 200
    #    ⚠️ 同时订正一条**错误的历史注释**：这里曾写「网关不实现 GET /v1/models」——
    #    实测 zen/go/v1 的 /models **返回 200 且免 key**，返回 35+ 个模型 id（标准 OpenAI
    #    列表格式：id/object/created/owned_by，**不含 context_length**）。
    #    故「刷新模型」对本 provider 是**可用**的，不需要走 404 兜底那条路。
    "opencode": {"name": "OpenCode Go", "base_url": "https://opencode.ai/zen/go/v1", "type": "built-in"},
    # Ollama（本地推理，2026-10-29）—— 本地优先的知识库连模型都要上云是最刺眼的缺口。
    # ⚠️ `/v1` 不能省：Ollama 的 OpenAI 兼容层就挂在 `/v1`，缺了会打到 404。
    # ⚠️ 它**不需要 API Key**，但「不要 Key」这个性质刻意**不写进这张表**（见下）。
    "ollama": {"name": "Ollama（本地）", "base_url": "http://localhost:11434/v1", "type": "built-in"},
}


# ── 「不需要 API Key」的厂商 ──
#
# ⚠️ **刻意不写成 `PROVIDER_DEFAULTS` 里的字段**：`routers/model.py:151-152` 那句
#    `default = dict(PROVIDER_DEFAULTS[provider_id])` 会把表里的**全部键**展开进
#    `providers` 数组并落盘 —— 加个 `requires_api_key` 等于往用户的 `config.yaml`
#    里写一个纯运行时概念。而 `test_table_shape` 是子集断言（`set(info) >= {...}`），
#    加字段**不会红**，所以也没有守卫会提醒这件事。
#
# ⚠️ 判据收口在这里，**13 处调用点一律调 `provider_requires_api_key()`**，不要各写
#    `if provider == "ollama"` —— 那是模式 #54（同源判据散落）。漏改一处的失效形态是
#    「那个入口对本地模型报未配置 Key」，而界面完全正常。
KEYLESS_PROVIDERS: frozenset[str] = frozenset({"ollama"})

#: 构造 `AsyncOpenAI` 时给 keyless 厂商用的占位串。
#: ⚠️ `api_key=None` 会被 SDK 直接拒绝（`OpenAIError`）；空串能构造出来，但会被服务端
#: 当成非法凭据。占位串让「不需要真 Key」这件事在传输层也成立。
KEYLESS_API_KEY_PLACEHOLDER = "sk-no-key-required"


def provider_requires_api_key(provider_id: str) -> bool:
    """这个厂商是否需要用户填 API Key。无 Key 检查处一律调它。"""
    return provider_id not in KEYLESS_PROVIDERS


def default_base_url(provider_id: str) -> str:
    """内置厂商的默认 API 地址；非内置厂商返回空串（调用方自行提示）。

    ⚠️ 取不到时返回 `""` 而不是抛错：调用方各自有不同的「没有地址」语义
    （`detect_models` 报 `DETECT_NO_BASE_URL`，`routers/model.py` 的导入路径
    则继续往下走），把异常策略放在这里会让两边都被迫接受同一个决定。
    """
    return (PROVIDER_DEFAULTS.get(provider_id) or {}).get("base_url", "")
