"""
统一日志系统 — RequestID 追踪 + 辅助函数

用法:
  from app.core import logging as log
  log.info("处理开始", mode=mode, file=path.name)
  # 输出: 12:34:56.789 INFO    app.routers.process  [rid=a1b2c3d4] 处理开始 | mode=extract file=img.jpg
"""

import contextvars
import json
import logging
import uuid

# 请求级上下文：每个 HTTP 请求一个独立的 request_id
request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    'request_id', default='-'
)

_logger = logging.getLogger(__name__)


class RequestIDFilter(logging.Filter):
    """注入 [rid=xxxxxxxx] 到每条日志记录"""
    def filter(self, record):
        record.request_id = request_id.get()[:8] if request_id.get() != '-' else '-'
        return True


def init_request_id_logging():
    """在 startup 中调用，注入 RequestIDFilter + 修改日志格式包含 rid"""
    try:
        root = logging.getLogger()
        for h in root.handlers:
            # 给所有 handler 加 rid filter
            if not any(isinstance(f, RequestIDFilter) for f in h.filters):
                h.addFilter(RequestIDFilter())
    except Exception:
        _logger.warning("Failed to add RequestIDFilter to handler", exc_info=True)


def info(msg: str, **kwargs):
    """INFO 级别 + 结构化 key=value 后缀"""
    _logger.info(_format(msg, kwargs))


def warning(msg: str, **kwargs):
    """WARNING 级别"""
    _logger.warning(_format(msg, kwargs))


def error(msg: str, **kwargs):
    """ERROR 级别"""
    _logger.error(_format(msg, kwargs))


def debug(msg: str, **kwargs):
    """DEBUG 级别"""
    _logger.debug(_format(msg, kwargs))


def exception(msg: str, **kwargs):
    """EXCEPTION 级别（自动附带 traceback）"""
    _logger.exception(_format(msg, kwargs))


def _format(msg: str, kwargs: dict) -> str:
    if not kwargs:
        return msg
    pairs = " ".join(f"{k}={_val(v)}" for k, v in kwargs.items())
    return f"{msg} | {pairs}"


def _val(v) -> str:
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, str) and len(v) <= 80:
        return v
    if isinstance(v, str):
        return v[:77] + "..."
    return json.dumps(v, default=str, ensure_ascii=False)
