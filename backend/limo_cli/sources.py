"""数据源 —— 两条路径，**同一份契约**。

| | 何时用 | 怎么走 |
|---|---|---|
| `HttpSource` | 默认 | `GET /api/cli/*`（后端已启动，进程边界） |
| `LocalSource` | `doctor --local` | 本进程直开 `Store` + `QueryService`（后端没起时诊断） |

两者暴露**同名同参同返回形状**的方法 —— 命令层因此完全不必知道数据从哪来。这不是
「两个实现长得像」，而是同一份契约的两个传输层：形状由 `QueryService` 的
`to_dict()` 定义（`schema_version` 就在里面），本模块只负责搬运。

⚠️ `LocalSource` **只给 doctor**：其他命令传 `--local` 是用法错误（`exit 2`）。理由是
`--local` 绕过后端的初始化顺序与并发控制（`ExecutionGuard`），它读到的库与「应用
正在写」的库可能是同一份——诊断偶尔这么干是合理的，日常读取不该。

⚠️ **错误必须是「带退出码的错误」而不是异常回显**：`QueryService` 抛的是 `QueryError`
（HTTP 语义，`status_code`），而 CLI 对外承诺的是**退出码**（`0/2/3/4/5/6`）。中间
这层映射就是本模块 `_CODE_EXIT` 的职责 —— 少了它，`limo get ../../etc` 会带着一个
Python traceback 退出（`exit 1`），而调用方无法区分它和「后端拒绝连接」。
"""

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from limo_cli import exit_codes as EXIT

# CLI 期望的查询契约版本。⚠️ 这个是**消费方的期望**，不是后端的真相，所以写在这里
# 而不是 import 后端常量——同仓 import 会让 `limo` 与「用户机器上正在跑的那个 exe」
# 用同一个数，于是**版本不匹配永远检测不出来**（打包版后端可能是三个月前的）。
EXPECTED_SCHEMA = "1"

DEFAULT_BASE_URL = "http://127.0.0.1:7413"
DEFAULT_TIMEOUT = 15.0


class SourceError(Exception):
    """数据源失败 —— 携带**退出码**（`exit_codes`），不是 HTTP 状态码。"""

    def __init__(self, exit_code: int, message: str, *, code: str = "",
                 detail: str = "", hint: str = "") -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.message = message
        self.code = code
        self.detail = detail
        self.hint = hint


# 后端 AppError code → CLI 退出码。**唯一**映射表（HTTP 与本地两条路径共用）。
#
# ⚠️ 判据是 code 而不是 HTTP 状态：`main.py` 的异常处理器对
# `recoverable=True` 且**未显式**传 status_code 的 AppError 返回 **HTTP 200**
# （见 `app.exception_handler(AppError)` 的 `_explicit_status` 分支）。按状态判会把
# 「资源不存在」读成一次成功响应，然后解析出一个错误对象当数据用。今天 `/api/cli/*`
# 的 QueryError 都显式传了 status，所以两种判法结果相同 —— 但那意味着**判据选错
# 也测不出来**，等哪天有人加一个 recoverable 的 404 就会静默错。
_CODE_EXIT = {
    "QUERY_INVALID_ID": EXIT.BAD_DATA,
    "QUERY_INVALID_CURSOR": EXIT.BAD_DATA,
    "QUERY_NOT_FOUND": EXIT.NOT_FOUND,
    "QUERY_ARTIFACT_NOT_FOUND": EXIT.NOT_FOUND,
    "QUERY_NO_SEARCH_ENGINE": EXIT.INTERNAL,
}

# HTTP 状态兜底（响应体没给出可识别的 code 时用）
_STATUS_EXIT = {400: EXIT.BAD_DATA, 404: EXIT.NOT_FOUND, 500: EXIT.INTERNAL}


def require_ident(value: str, label: str, *, code: str) -> str:
    """校验一个要当**路径段**用的标识符（docId / part），不合法 → `exit 5`。

    ⚠️ **必须在客户端拦，不能指望后端的 `QUERY_INVALID_ID`** —— 这是本模块最隐蔽
    的一处坑：`get ../../etc/passwd` 里的 `/` 经 URL 编码成 `%2F` 之后，会被
    Starlette **解码回 `/` 用于路由匹配**，于是请求落到「没有这条路由」→ 后端 404
    （FastAPI 默认的 `{"detail":"Not Found"}`）→ CLI 报 `exit 3`（未找到）。而同一个
    `..` 换个不含 `/` 的形态（如 `bad id!`）能正常到达 handler → 400
    `QUERY_INVALID_ID` → `exit 5`。**同一类坏输入因字符集不同得到两种退出码**，且
    404 那条会诱导调用方「换个 ID 重试」—— 正是「空」与「坏」要分开的那种场景。

    判据与后端**同源**（`app.core.resource_id.is_valid_resource_id`，零依赖模块），
    不是本地另写一份正则 —— 两份判据的漂移是静默的（这个模块放行了、后端拒了，
    或者反过来）。
    """
    from app.core.resource_id import is_valid_resource_id
    if not is_valid_resource_id(value):
        raise SourceError(
            EXIT.BAD_DATA, f"无效的{label}", code=code,
            detail=f"只能含字母/数字/下划线/连字符且 ≤64 字符，实得 {value!r}",
            hint="`limo list` 可看到合法的 ID",
        )
    return value


def _page_args(source_type, tag, folder_id, query, cursor,
               include_descendants=False) -> dict:
    """两个 Source 共用的筛选参数归一 —— 尤其 `folder_id` 的**三态**。

    ⚠️ `folder_id` 的三态是 `None`（不过滤）/ `""`（只看未分类）/ `值`（该文件夹），
    不能写 `folder_id or None`：那会把「只看未分类」这个**有效筛选**变成「不过滤」，
    而结果看起来完全正常（多给了几篇）。

    ⚠️ `include_descendants` 归一成 `True | None`（**不是** `True | False`）：
    HTTP 侧 `_get` 靠「值为 None 就不进 query string」表达「不传」，而这个参数的
    默认值就是 False —— 传不传结果一样，但少一个参数就少一次「后端默认值改了而
    CLI 还在显式传旧值」的漂移面。本地侧拿到 `None` 时 `QueryService` 的
    `if include_descendants and ...` 同样判假，两侧语义一致。

    ⚠️ **`limit` / `--all` 刻意不在这里**：两个 Source 表达「一次取全」的方式
    **不同**——本地是 `limit=None`（`QueryService` 的语义），HTTP 是 `all=true` 且
    **不传** `limit`（后端的默认值是 50，不是「全部」）。曾把两者都归一成
    `limit=None`，HTTP 路径因此静默退化成「第一页 50 条」——一个少拿了 26 篇却
    看起来完全正常的成功（第 9 项门禁抓到的就是它）。
    """
    return {
        "source_type": source_type or None,
        "tag": tag or None,
        "folder_id": folder_id,
        "query": query or None,
        "include_descendants": True if include_descendants else None,
        "cursor": cursor,
    }


class HttpSource:
    """经 `GET /api/cli/*` 读库。后端不可达 → `exit 4`。"""

    def __init__(self, base_url: str | None = None, timeout: float | None = None) -> None:
        env_base = os.environ.get("LIMO_BASE_URL", "").strip()
        self.base_url = (base_url or env_base or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout or DEFAULT_TIMEOUT

    # ── 传输 ──

    def _get(self, path: str, **params) -> dict:
        # ⚠️ 丢掉值为 None 的参数（`None` = 不传 = 不过滤）；空串要**保留**（见 `_page_args`）
        clean = {k: v for k, v in params.items() if v is not None}
        url = f"{self.base_url}/api/cli/{path}"
        if clean:
            url += "?" + urllib.parse.urlencode(clean)
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            # ⚠️ HTTPError 是 URLError 的子类，**必须排在前面** —— 否则永远走不到
            # 这里，后端返回的 404 会被说成「后端不可达」。
            raise self._from_body(e.read(), e.code)
        except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
            raise SourceError(
                EXIT.BACKEND_UNAVAILABLE, "无法连接 LiMo 后端",
                code="CLI_BACKEND_UNAVAILABLE",
                detail=f"{self.base_url} — {getattr(e, 'reason', None) or e}",
                hint="启动 LiMo 应用，或设置 LIMO_BASE_URL 指向别的端口；"
                     "诊断后端与本机库可用 `limo doctor`",
            )
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise SourceError(
                EXIT.BAD_DATA, "后端响应不是合法 JSON", code="CLI_BAD_RESPONSE",
                detail=f"path={path} error={e}",
                hint="后端版本可能与 CLI 不匹配；`limo doctor` 会比对两者",
            )
        if not isinstance(payload, dict):
            raise SourceError(
                EXIT.BAD_DATA, "后端响应结构不符合契约", code="CLI_BAD_RESPONSE",
                detail=f"path={path} 期望 JSON 对象，实得 {type(payload).__name__}",
            )
        self._check_schema(payload, path)
        return payload

    def _from_body(self, body: bytes, status: int) -> SourceError:
        """HTTP 错误 → `SourceError`。优先按响应体里的 `code` 判（见 `_CODE_EXIT`）。"""
        payload = None
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            pass
        code = payload.get("code", "") if isinstance(payload, dict) else ""
        exit_code = _CODE_EXIT.get(code) or _STATUS_EXIT.get(status) or EXIT.BAD_DATA
        message = (payload or {}).get("message") if isinstance(payload, dict) else ""
        detail = (payload or {}).get("detail", "") if isinstance(payload, dict) else ""
        return SourceError(
            exit_code, message or f"后端返回 HTTP {status}",
            code=code, detail=detail or f"HTTP {status}",
            hint="" if code in _CODE_EXIT else "后端返回了 CLI 不认识的错误码，"
                                              "可能是版本不匹配（`limo doctor`）",
        )

    def _check_schema(self, payload: dict, path: str) -> None:
        """契约版本协商：**主版本不同就停**。

        新增可选字段向后兼容（不必报错），但主版本变了意味着字段含义可能已经不同 ——
        此时静默按老字段读会得到「结构完整、值却全是错的」的结果，比直接失败糟得多。
        """
        got = payload.get("schema_version")
        if got is None:
            raise SourceError(
                EXIT.BAD_DATA, "后端响应缺少 schema_version", code="CLI_SCHEMA_MISSING",
                detail=f"path={path}",
                hint=f"CLI 期望 schema {EXPECTED_SCHEMA}；后端可能过旧，请重新打包/更新",
            )
        if str(got).split(".")[0] != EXPECTED_SCHEMA.split(".")[0]:
            raise SourceError(
                EXIT.BAD_DATA, "查询契约版本不匹配", code="CLI_SCHEMA_MISMATCH",
                detail=f"CLI 期望 {EXPECTED_SCHEMA}，后端返回 {got}",
                hint="CLI 与后端来自不同版本；用同一份代码库重跑，或更新后端",
            )

    # ── 契约方法 ──

    def status(self) -> dict:
        return self._get("status")

    def list_resources(self, *, source_type=None, tag=None, folder_id=None, query=None,
                       include_descendants=False, limit=50, all_pages=False,
                       cursor=None) -> dict:
        params = _page_args(source_type, tag, folder_id, query, cursor,
                            include_descendants)
        # ⚠️ 后端这个端点的形参叫 **`q`**（`routers/cli.py::cli_list_resources`），
        # 而 `_page_args` 产出的键是 `query` —— 它还喂着 `LocalSource` 那边
        # `QueryService.list_resources(query=…)` 的**关键字参数**，那个名字动不得。
        # 曾经原样 `**params` 传下去：FastAPI 对未知查询参数**静默忽略**，`q` 保持
        # 默认空串 ⇒ 关键词筛选凭空消失、返回全库而结构完全合法（`limo list --query`
        # 与 MCP 的 `limo_list(query=…)` 同病，两边都不报错）。
        params["q"] = params.pop("query")
        # `all_pages` → `all=true` 且**不传** limit（见 `_page_args` 的 ⚠️）
        return self._get("resources", **params,
                         limit=None if all_pages else limit,
                         all="true" if all_pages else None)

    def get_resource(self, doc_id: str) -> dict:
        require_ident(doc_id, "资源 ID", code="QUERY_INVALID_ID")
        return self._get(f"resources/{urllib.parse.quote(doc_id, safe='')}")

    def list_artifacts(self, doc_id: str) -> dict:
        require_ident(doc_id, "资源 ID", code="QUERY_INVALID_ID")
        return self._get(f"resources/{urllib.parse.quote(doc_id, safe='')}/artifacts")

    def get_artifact(self, doc_id: str, part: str, *, version=None, offset=0,
                     max_chars=None, full=False, include_raw=False) -> dict:
        """`max_chars=None` = **让后端用它自己的默认上限**（不是「不截断」）。

        ⚠️ 刻意不在 CLI 里复刻一份默认值：那是个副本，后端调大上限时 CLI 仍传旧值，
        于是**上限被悄悄按老的执行**，而两边代码看起来都对。`--full` 才是「不截断」，
        走的是独立的 `full=true`（后端把 `max_chars` 置 None）。
        """
        require_ident(doc_id, "资源 ID", code="QUERY_INVALID_ID")
        require_ident(part, "产物名", code="CLI_INVALID_PART")
        path = f"resources/{urllib.parse.quote(doc_id, safe='')}/artifacts/{urllib.parse.quote(part, safe='')}"
        return self._get(path, version=version, offset=offset, max_chars=max_chars,
                         full="true" if full else None,
                         include_raw="true" if include_raw else None)

    def search(self, q: str, *, limit=50) -> dict:
        return self._get("search", q=q, limit=limit)

    def folders(self) -> dict:
        return self._get("folders")


class LocalSource:
    """直开本机库（不经 HTTP）。仅 `doctor --local` 使用 —— 见模块 docstring。"""

    def __init__(self, data_dir: str | None = None) -> None:
        from app.core.config import load_config
        from app.storage.store import Store
        from app.services.query import DEFAULT_MAX_CHARS, QueryService

        # `app.*` 一律**函数内**导入：走 HTTP 的路径不该为了 `limo list` 拉起半个
        # backend 依赖树（`import app.core.config` 会连带 config_io / secrets / DPAPI）。
        # 这里没有 #26 的阴影风险 —— 模块顶部本来就没有同名的 app.* 导入。
        self._default_max_chars = DEFAULT_MAX_CHARS

        if data_dir:
            # ⚠️ **显式给了就不读配置**：MCP 服务端（`mcp_server.py`）在 `index.json`
            # 变化时会重建整个 `LocalSource`，而 `load_config()` 每次都会 `copy2` 一份
            # `config.yaml.bak` —— 一个只读服务端不该因为「库里有新文档」就反复写配置文件。
            # CLI 侧的调用点只有 `cli.py` 的 `LocalSource()`（无参），这条分支今天恒不命中。
            self.data_dir = Path(data_dir)
        else:
            cfg = load_config()
            self.data_dir = Path(cfg.get("data_dir") or (Path.home() / ".limo"))
        self._store = Store(str(self.data_dir))

        # ⚠️ 存在才构造。`SearchEngine.__init__` 会 `CREATE TABLE IF NOT EXISTS` 并
        # commit —— 那是一次**写盘**，而 `--local` 的全部理由就是「后端没起来时看看
        # 本机有什么」，它不该顺手改库。索引没建过就如实说没建过，把重建留给应用。
        db = self.data_dir / "search.db"
        self._search = None
        if db.exists():
            from app.core.search import SearchEngine
            eng = SearchEngine(str(self.data_dir))
            # 结构对不上（索引还没被应用升级）就当「没建过索引」——如实说没有，
            # ⚠️ **绝不在这里 DROP 重建**：那正是上面注释里「不该顺手改库」的第二种形态，
            # 而且 `--local` 手上没有 content getter，DROP 完就再也补不回来。
            if eng.is_schema_current():
                self._search = eng

        self._svc = QueryService(self._store, self._search)

    # ── 错误翻译（唯一一处把 QueryError 变成退出码的地方）──

    def _run(self, fn, *args, **kwargs):
        from app.services.query import QueryError
        try:
            return fn(*args, **kwargs)
        except QueryError as e:
            exit_code = _CODE_EXIT.get(e.code, EXIT.BAD_DATA)
            raise SourceError(exit_code, e.message, code=e.code,
                              detail=getattr(e, "detail", "")) from e

    def _need_search(self) -> None:
        if self._search is None:
            raise SourceError(
                EXIT.BACKEND_UNAVAILABLE, "本地全文索引尚未构建",
                code="CLI_NO_LOCAL_INDEX",
                detail=f"未找到 {self.data_dir / 'search.db'}",
                hint="启动一次 LiMo 应用即可建立索引（或改用不带 --local 的联网模式）",
            )

    # ── 契约方法（与 HttpSource 同名同参同形状）──

    def status(self) -> dict:
        from app.services.query import SCHEMA_VERSION
        return {"schema_version": SCHEMA_VERSION, **self._run(self._svc.stats)}

    def list_resources(self, *, source_type=None, tag=None, folder_id=None, query=None,
                       include_descendants=False, limit=50, all_pages=False,
                       cursor=None) -> dict:
        page = self._run(self._svc.list_resources,
                         **_page_args(source_type, tag, folder_id, query, cursor,
                                      include_descendants),
                         limit=None if all_pages else limit)
        return page.to_dict()

    def get_resource(self, doc_id: str) -> dict:
        from app.services.query import SCHEMA_VERSION
        require_ident(doc_id, "资源 ID", code="QUERY_INVALID_ID")
        # 与 `routers/cli.py` 同形：顶层平铺 `schema_version`（见那里的说明）
        return {"schema_version": SCHEMA_VERSION,
                **self._run(self._svc.get_resource, doc_id)}

    def list_artifacts(self, doc_id: str) -> dict:
        from app.services.query import SCHEMA_VERSION
        require_ident(doc_id, "资源 ID", code="QUERY_INVALID_ID")
        return {"schema_version": SCHEMA_VERSION, "docId": doc_id,
                "items": self._run(self._svc.list_artifacts, doc_id)}

    def get_artifact(self, doc_id: str, part: str, *, version=None, offset=0,
                     max_chars=None, full=False, include_raw=False) -> dict:
        # 本地路径也必须校验 part：`QueryService.get_artifact` 只校验 doc_id，
        # 非法 part 会一路走到「查不到」→ exit 3，与 HTTP 路径的 exit 5 分叉
        require_ident(doc_id, "资源 ID", code="QUERY_INVALID_ID")
        require_ident(part, "产物名", code="CLI_INVALID_PART")
        if full:
            limit = None                              # 不截断
        elif max_chars is None:
            limit = self._default_max_chars           # 与 HTTP 路径同语义：用后端默认
        else:
            limit = max_chars
        art = self._run(self._svc.get_artifact, doc_id, part, version=version,
                        offset=offset, max_chars=limit)
        return art.to_dict(include_raw=include_raw)

    def search(self, q: str, *, limit=50) -> dict:
        self._need_search()
        page = self._run(self._svc.search_resources, q, limit=limit)
        return page.to_dict()

    def folders(self) -> dict:
        from app.services.query import SCHEMA_VERSION
        return {"schema_version": SCHEMA_VERSION,
                "items": self._run(self._svc.list_folders)}
