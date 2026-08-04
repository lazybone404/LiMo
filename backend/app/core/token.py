"""token 估算 — 公共实现（tiktoken cl100k_base，fallback 字符/2）

从 content_preparer 抽出，对话上下文预算 / 生成截断共用。
"""

import logging

logger = logging.getLogger(__name__)

# 各厂商默认上下文窗口（token）— 上下文预算兜底（优先级最低，见 model_resolver.resolve_context_window）
DEFAULT_CONTEXT = {
    "deepseek": 1_000_000,
    "openai": 128_000,
    "anthropic": 200_000,
    "dashscope": 1_000_000,
}

try:
    import tiktoken
    _TIK_ENCODER = tiktoken.get_encoding("cl100k_base")

    def estimate_tokens(text: str) -> int:
        return len(_TIK_ENCODER.encode(text))
except (ImportError, AttributeError):
    def estimate_tokens(text: str) -> int:
        return len(text) // 2
