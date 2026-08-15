"""业务服务层异常 — 独立于 app/core/errors.py 的分层，按需补充各服务专属错误"""

from app.core.errors import AppError


class EpubError(AppError):
    """电子书解读层异常（recoverable → 前端友好提示，HTTP 200）"""
    def __init__(self, code: str, message: str, detail: str = ""):
        super().__init__(code, message, detail, recoverable=True)
