"""token 估算 — 公共实现（tiktoken cl100k_base，fallback 字符/2）

从 content_preparer 抽出，对话上下文预算 / 生成截断共用。
"""

import logging

logger = logging.getLogger(__name__)

# 各厂商默认上下文窗口（token）— 上下文预算兜底（优先级最低，见 model_resolver.resolve_context_window）
# 取值原则：这是第四层兜底，只在「用户未自定义 + 无 llm_models 预设」时生效，
# 故一律取保守值——宁可低估（截断早一点）也不要高估（请求超窗直接失败）。
# zhipu 刻意不拉平 glm-5.2 的 1M：预设层已能给 1M，兜底层取保守值才安全。
DEFAULT_CONTEXT = {
    "deepseek": 1_000_000,
    "openai": 128_000,
    "anthropic": 200_000,
    "dashscope": 1_000_000,
    "google": 1_000_000,
    "moonshot": 131_072,
    "opencode": 131_072,
    "zhipu": 131_072,
    # Ollama（本地）—— 本地模型的窗口**差异极大**（qwen3 系 32k–128k，llama 系 8k）。
    # 兜底层取 32k：宁可低估（截断早）也不要高估（本地服务直接 400 超窗）。
    # 用户可以让「刷新模型」拉到真实 `num_ctx`，或在设置页手填 context_window 覆盖这层。
    "ollama": 32_768,
}

try:
    import tiktoken
    _TIK_ENCODER = tiktoken.get_encoding("cl100k_base")

    def estimate_tokens(text: str) -> int:
        return len(_TIK_ENCODER.encode(text))
except (ImportError, AttributeError):
    def estimate_tokens(text: str) -> int:
        return len(text) // 2
