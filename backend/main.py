"""
LiMo — FastAPI 入口
"""

import logging
import os as _os
import re
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

# PyInstaller 打包版：Python 原生崩溃（segfault/abort）时 dump 线程栈到 stderr
# （Tauri 重定向到 limo_backend_stderr.log）——否则静默退出无法定位。
if getattr(sys, "frozen", False):
    import faulthandler
    faulthandler.enable()

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import load_config
from app.core.concurrency import ExecutionGuard
from app.core.logging import request_id, init_request_id_logging, RequestIDFilter
from app.storage.store import Store

app = FastAPI(title="LiMo", version="0.4.8")

@app.middleware("http")
async def add_request_id(request, call_next):
    """每个 HTTP 请求注入唯一 request_id 到日志上下文 + 记录请求耗时/状态（P1.2 请求级日志）"""
    import time as _time
    import uuid
    token = request_id.set(request.headers.get("X-Request-ID", uuid.uuid4().hex[:16]))
    start = _time.perf_counter()
    try:
        response = await call_next(request)
        status = response.status_code
        duration_ms = (_time.perf_counter() - start) * 1000
    except Exception:
        # 响应异常（无 response 对象）：记录 500 后向上抛（ServerErrorMiddleware 兜底返回 500）
        _log_request(request, 500, (_time.perf_counter() - start) * 1000)
        request_id.reset(token)
        raise
    response.headers["X-Request-ID"] = request_id.get()
    _log_request(request, status, duration_ms, response_headers=response.headers)
    request_id.reset(token)
    return response


def _log_request(request, status: int, duration_ms: float, response_headers=None):
    """请求级日志：method/path/status/duration_ms。慢请求 WARN，SSE 长连接不判慢。

    - SSE 识别按响应头 content-type: text/event-stream（通用，非路径白名单——
      路径判断会漏新增流式端点）
    - 跳过高频噪音：health 探活 / static 静态资源 / assets 图片渲染
    """
    from app.core import logging as log
    path = request.url.path
    if path == "/health" or path == "/api/health" or path.startswith("/static") or path.startswith("/api/assets"):
        return
    is_sse = bool(response_headers) and "text/event-stream" in response_headers.get("content-type", "")
    kw = {"method": request.method, "path": path, "status": status, "duration_ms": round(duration_ms, 1)}
    if is_sse:
        kw["stream"] = "sse"
        log.info("HTTP", **kw)  # SSE 长连接不判慢：只记录不 WARN
    elif duration_ms >= 2000:
        kw["slow"] = True
        log.warning("HTTP", **kw)
    else:
        log.info("HTTP", **kw)

# CORS Origin 白名单 — 只允许本机与 Tauri 壳来源，阻隔任意网页跨站访问本地后端。
# - Tauri v2 生产 WebView2 实际 origin: http://tauri.localhost（Windows 实测）、https://tauri.localhost（macOS/Linux）
# - 部分平台/custom protocol 用 tauri://localhost
# - 开发 Vite devUrl: http://localhost:<任意端口>（端口通配）
_CORS_ALLOWED_ORIGIN_RE = re.compile(
    r'^(https?://(localhost|127\.0\.0\.1)(:\d+)?|https?://tauri\.localhost|tauri://localhost)$'
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=_CORS_ALLOWED_ORIGIN_RE.pattern,
    allow_credentials=True,    # Tauri WebView2 需要（仅在白名单 origin 时生效）
    allow_methods=["*"],
    allow_headers=["*"],
)

# 兜底：强制 CORS 头 — CORSMiddleware 可能因版本/协议差异漏加
# 用纯 ASGI 中间件在响应外层确保 origin 回显。
# ⚠️ 仅当 Origin 命中白名单才回显凭证；非白名单 origin 不返回任何 CORS 头，
# 浏览器据此拦截跨域响应（阻断恶意网页读取本地后端）。
from starlette.types import ASGIApp, Scope, Receive, Send
class _ForceCORSMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app
    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http":
            # 从请求中提取 origin
            req_origin = b"*"
            for k, v in scope.get("headers", []) or []:
                if k == b"origin":
                    req_origin = v
                    break
            origin_str = req_origin.decode(errors="ignore")
            allowed = bool(_CORS_ALLOWED_ORIGIN_RE.match(origin_str))
            async def _send(message):
                if message["type"] == "http.response.start" and allowed:
                    # 保留已有 header，追加/覆盖 CORS header
                    headers: list[tuple[bytes, bytes]] = []
                    has_origin = False
                    for k, v in message.get("headers", []):
                        if k == b"access-control-allow-origin":
                            has_origin = True
                            headers.append((k, req_origin))  # 覆盖为具体 origin
                        else:
                            headers.append((k, v))
                    if not has_origin:
                        headers.append((b"access-control-allow-origin", req_origin))
                    headers.append((b"access-control-allow-credentials", b"true"))
                    headers.append((b"access-control-allow-methods", b"*"))
                    headers.append((b"access-control-allow-headers", b"*"))
                    message["headers"] = headers
                await send(message)
            await self.app(scope, receive, _send)
        else:
            await self.app(scope, receive, send)

app.add_middleware(_ForceCORSMiddleware)

# 截图静态文件在 startup 中按 config data_dir 挂载


# ── 全局异常处理 — AppError → 结构化 JSON 响应 ──

from app.core.errors import AppError
from app.core import logging as log

@app.exception_handler(AppError)
async def app_error_handler(request, exc: AppError):
    """AppError 体系 → { code, message, detail, recoverable }

    recoverable=True → INFO（预期业务状态，不是异常，如 MD_NOT_FOUND "尚未生成"）
    recoverable=False → WARNING（需关注的异常，如权限拒绝、配置错误）
    """
    if not exc.recoverable:
        log.warning("AppError", code=exc.code, message=exc.message,
                    path=request.url.path)
    from fastapi.responses import JSONResponse
    # status_code 显式指定（_explicit_status，含显式 400）时尊重；未显式时
    # recoverable → 200（避免浏览器 DevTools 显示网络错误，如 "尚未生成" 是预期
    # 业务状态，不是真的 HTTP 错误）。显式 400 必须返回 400——此前用「≠默认 400」
    # 判断，显式传 status_code=400 + recoverable=True 会被误压成 200，前端下载
    # 到损坏 zip（评审 H1）。
    http_status = exc.status_code if exc._explicit_status else (
        200 if exc.recoverable else exc.status_code)
    return JSONResponse(status_code=http_status, content=exc.to_dict())


@app.exception_handler(Exception)
async def unexpected_error_handler(request, exc: Exception):
    """未预期的异常 — 统一 500 + 日志 traceback"""
    log.exception("Unexpected server error", path=request.url.path, error=str(exc)[:200])
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=500, content={
        "code": "INTERNAL_ERROR",
        "message": "服务器内部错误，请稍后重试或联系开发者",
        "detail": str(exc)[:300],
        "recoverable": True,
    })


def _init_logging(config: dict) -> None:
    """统一日志初始化 — 控制台 + 滚动文件 + 内存环形缓冲，尊重 config.yaml 的 log_level"""
    level_name = config.get("log_level", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    # root logger — 控制所有 logger.getLogger(__name__) 的输出
    root = logging.getLogger()
    root.setLevel(level)

    # 控制台格式：HH:MM:SS LEVEL    module  message
    # PyInstaller console=False 时 stdout/stderr 可能为 None
    _out = sys.stdout or open(_os.devnull, "w")
    console = logging.StreamHandler(_out)
    console.setLevel(level)
    console.addFilter(RequestIDFilter())
    console.setFormatter(logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)-7s [rid=%(request_id)s] %(name)-30s %(message)s",
        datefmt="%H:%M:%S",
    ))
    root.addHandler(console)

    # 内存环形缓冲区 — 供前端日志面板调用 GET /api/logs
    from collections import deque
    _log_buffer: deque[dict] = deque(maxlen=1000)
    app.state.log_buffer = _log_buffer

    class BufferHandler(logging.Handler):
        def emit(self, record):
            _log_buffer.append({
                "ts": record.created,
                "level": record.levelname.lower(),
                "name": record.name,
                "msg": self.format(record),
            })

    buf_handler = BufferHandler()
    buf_handler.setLevel(logging.DEBUG)
    buf_handler.addFilter(RequestIDFilter())
    buf_handler.setFormatter(logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)-5s [rid=%(request_id)s] %(name)-25s %(message)s",
        datefmt="%H:%M:%S",
    ))
    root.addHandler(buf_handler)

    # 全量应用日志 — 滚动 5 × 2MB，路径 data_dir/app.log
    # 打包端用户无法看终端，所有日志写入文件供排查
    try:
        log_dir = Path(config.get("data_dir", "data"))
        log_dir.mkdir(parents=True, exist_ok=True)
        app_log = RotatingFileHandler(
            log_dir / "app.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8",
        )
        app_log.setLevel(logging.INFO)
        app_log.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-5s [rid=%(request_id)s] %(name)s:%(lineno)d  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        root.addHandler(app_log)
    except OSError:
        pass  # 写文件失败不阻塞启动

    # 抑制 uvicorn access 日志的重复输出（它自己已经打印了简要信息）
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    root.info("日志初始化完成 level=%s dir=%s", level_name, config.get("data_dir", "data"))


@app.on_event("startup")
async def startup():
    from datetime import datetime
    app.state.started_at = datetime.now().isoformat()

    config = load_config()
    app.state.config = config

    # PyInstaller 打包后 SSL 证书路径丢失，显式设置
    import os as _os
    try:
        import certifi
        _os.environ["SSL_CERT_FILE"] = certifi.where()
        _os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
    except Exception:
        pass

    # 注入全局代理 → dashscope SDK / openai / yt-dlp 统一走 proxy
    _proxy = (config.get("fetch", {}) or {}).get("proxy", "").strip()
    if _proxy:
        _os.environ["HTTP_PROXY"] = _proxy
        _os.environ["HTTPS_PROXY"] = _proxy
        _os.environ["http_proxy"] = _proxy
        _os.environ["https_proxy"] = _proxy

    _init_logging(config)
    init_request_id_logging()  # 注入 RequestID 过滤器

    # 存储
    app.state.store = Store(config.get("data_dir", "data"))

    # 适配器
    from app.adapters import init_adapters
    init_adapters()
    app.state.adapters_ready = True

    # 引擎
    from app.engines import init_engines
    init_engines(config)
    # 加载用户插件（在 init_engines 之后，允许插件覆盖内置引擎）
    from app.engines.plugin_loader import init_plugins
    init_plugins(config)
    app.state.engines_ready = True

    # 预置示例文档（首次启动创建一次，用户删除后不再复活；置于引擎初始化之后，
    # 避免引擎初始化失败时 marker 已写导致示例文档永不创建）
    from app.core.sample_doc import seed_sample_doc
    seed_sample_doc(app.state.store)

    # Config 写入锁 — 防止并发 RMW 损坏 config.yaml
    app.state.config_lock = threading.Lock()

    # 全局并发控制 — 限制 LLM/引擎并发数，超限自动排队
    app.state.guard = ExecutionGuard(cloud_max=5, local_max=2)

    # 全文搜索引擎 — 从已有转录重建索引（后台不阻塞启动）
    from app.core.search import SearchEngine
    _search_engine = SearchEngine(config.get("data_dir", "data"))
    app.state.search_engine = _search_engine
    try:
        _transcripts = app.state.store.list_transcripts()
        def _get_content(tx_id: str) -> str:
            tx = app.state.store.get_transcript(tx_id)
            return tx.get("rawText", "") if tx else ""
        _search_engine.rebuild(_transcripts, _get_content)
        import logging
        logging.getLogger(__name__).info("Search index ready (%d docs)", len(_transcripts))
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Search index rebuild failed: %s", e)


@app.get("/health")
@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.4.8", "started_at": app.state.started_at}


# 路由（files 在 ingest 之前——/files/{id}/original 比 /files/{id}/{name} 更具体，必须先匹配）
from app.routers import ingest, generate, config as config_router, capabilities, chat, model, process, files, export, conversations, plugins, references, epub, markdown_edit, folders, synthesis
app.include_router(folders.router, prefix="/api")
app.include_router(markdown_edit.router, prefix="/api")
app.include_router(references.router, prefix="/api")
app.include_router(epub.router, prefix="/api")
app.include_router(synthesis.router, prefix="/api")
app.include_router(plugins.router, prefix="/api")
app.include_router(files.router, prefix="/api")
app.include_router(ingest.router, prefix="/api")
app.include_router(generate.router, prefix="/api")
app.include_router(config_router.router, prefix="/api")
app.include_router(capabilities.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(model.router, prefix="/api")
app.include_router(process.router, prefix="/api")
app.include_router(export.router, prefix="/api")
app.include_router(conversations.router, prefix="/api")
from app.routers import search
app.include_router(search.router, prefix="/api")

# ── 日志查看端点 ──

from fastapi.responses import JSONResponse as _JSONResponse

@app.get("/api/logs")
async def view_logs(request: Request):
    """返回内存中的日志缓冲区（最近 N 条），供前端日志面板调用

    支持 level（error/warning/info/debug/all）与 search（msg 关键字/request_id 子串）过滤。
    search 在切片前过滤——对全量 1000 条 buffer 搜索，而非最近 N 条。
    """
    try:
        lines = int(request.query_params.get("lines", 200))
    except ValueError:
        lines = 200  # 非数字参数防御（此前裸 int() 抛 ValueError → 500）
    level = request.query_params.get("level", "all")
    search = request.query_params.get("search", "").strip().lower()
    buf = getattr(request.app.state, 'log_buffer', [])
    entries = list(buf)
    if level != "all":
        entries = [e for e in entries if e["level"] == level]
    if search:
        entries = [e for e in entries if search in e["msg"].lower()]
    entries = entries[-lines:]
    return _JSONResponse({"logs": entries, "total": len(buf), "returned": len(entries)})


if __name__ == "__main__":
    import os as _os
    # PyInstaller console=False 时 sys.stderr 为 None，uvicorn logging 会崩溃
    if sys.stderr is None:
        sys.stderr = open(_os.devnull, "w")
    if sys.stdout is None:
        sys.stdout = open(_os.devnull, "w")
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=7413)
