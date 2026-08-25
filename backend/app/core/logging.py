"""
统一日志系统 — RequestID 追踪 + 辅助函数

用法:
  from app.core import logging as log
  log.info("处理开始", mode=mode, file=path.name)
  # 输出: 12:34:56.789 INFO    app.routers.process  [rid=a1b2c3d4] 处理开始 | mode=extract file=img.jpg
"""

import contextvars
import inspect
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


def _caller_name() -> str:
    """取调用 helper 的模块名（跳过本模块内部帧）。

    保证结构化日志的 name 字段仍是来源模块（如 app.routers.ingest），
    否则所有 helper 日志 name 固定为 app.core.logging，无法区分来源。
    """
    try:
        f = inspect.currentframe()
        # 帧栈: _caller_name → _emit → log.info/... → 业务调用者
        f = f.f_back.f_back.f_back if f and f.f_back else None
        if f is None:
            return "-"
        mod = f.f_globals.get("__name__", "")
        if mod == __name__ or not mod:
            return "-"
        return mod
    except Exception:
        return "-"


def _emit(level: str, msg: str, kwargs: dict):
    """按调用者模块名取 logger 发日志（子 logger 无 handler → propagate 到 root）。"""
    name = _caller_name()
    target = logging.getLogger(name) if name and name != "-" else _logger
    getattr(target, level)(_format(msg, kwargs))


def info(msg: str, **kwargs):
    """INFO 级别 + 结构化 key=value 后缀"""
    _emit("info", msg, kwargs)


def warning(msg: str, **kwargs):
    """WARNING 级别"""
    _emit("warning", msg, kwargs)


def error(msg: str, **kwargs):
    """ERROR 级别"""
    _emit("error", msg, kwargs)


def debug(msg: str, **kwargs):
    """DEBUG 级别"""
    _emit("debug", msg, kwargs)


def exception(msg: str, **kwargs):
    """EXCEPTION 级别（自动附带 traceback）"""
    _emit("exception", msg, kwargs)


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
