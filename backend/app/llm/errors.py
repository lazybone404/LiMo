"""LLM 调用异常 — 统一错误类型"""


class LLMError(Exception):
    """LLM 调用失败（网络、配额、Key 无效等）"""
    def __init__(self, message: str, provider: str = "", model: str = ""):
        super().__init__(message)
        self.provider = provider
        self.model = model


class CancelledError(Exception):
    """LLM 流式调用被用户取消——用于 stream() 中 break 循环"""
    pass
