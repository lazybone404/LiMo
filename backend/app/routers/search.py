"""检索端点 —— `GET /api/search`（字面 ∪ 语义融合）+ 索引健康 / 重建 + 向量开关。"""

import asyncio

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from app.core.config_io import save_config
from app.core.dependencies import (
    get_config,
    get_config_lock,
    get_query_service,
    get_search_engine,
    get_store,
    get_vector_searcher,
)
from app.core.errors import AppError
from app.core.origin import ensure_trusted_request
from app.core.registry import EngineRegistry
from app.core.vector_store import DEFAULT_CHUNK_SIZE
from app.generation.doc_text import search_index_text
from app.services.vector_runtime import DEFAULT_ENGINE
from app.services.vector_search import DEFAULT_DEPTH
from app.services.vector_worker import (
    DEFAULT_RELEASE,
    DEFAULT_THRESHOLD,
    DEFAULT_TRIGGER,
    RELEASE_POLICIES,
    TRIGGERS,
)

# ⚠️ **不要在这里写 `prefix="/api"`**：`main.py` 用 `include_router(..., prefix="/api")`
# 统一挂载（全仓 17 个 router 里另外 16 个都是裸 `APIRouter()`）。两处都写会得到
# `/api/api/search`——前端 `apiFetch(\`${BASE}/search\`)` 打的是 `/api/search`，
# 于是**搜索一直 404 而两侧测试全绿**（前端测试 mock 掉了 `api/search` 模块，后端
# `test_search.py` 测的是 SearchEngine 而不是 HTTP 路径；铁律 #51「能力存在 ≠ 该路径
# 在用它」的又一形态）。同类事故在 2026-07-10 已发生过一次（那次在前端侧，6 处）。
router = APIRouter(tags=["search"])

#: 单次返回条数上限。⚠️ **与前端 `useDocQuery.ts::SEARCH_LIMIT` 必须同值** ——
#: 前端要 100 条是「大列表分批渲染 60 条」的下限需求，而这里曾写 `le=50`，
#: 于是**文档库搜索框每次都是 422、一条结果都没有**。三条测试路径全都绕过了它：
#: 前端测试 mock 掉 `api/search`、`test_search.py` 测 `SearchEngine` 类而非 HTTP
#: 路径、评测脚本直调 `engine.search()`。改这个数之前先问：哪条测试真的会因此变红。
SEARCH_MAX_LIMIT = 100


@router.get("/search")
async def search_transcripts(
    q: str = Query(default="", min_length=1, description="搜索关键词"),
    limit: int = Query(default=20, ge=1, le=SEARCH_MAX_LIMIT),
    svc=Depends(get_query_service),
    searcher=Depends(get_vector_searcher),
):
    """全文搜索已导入的文档内容 —— **字面（FTS5）与语义（向量）RRF 融合**。

    响应 `{hits, semantic}`：

    - `hits`：`[{id, title, snippet}]`，按融合后的相关性排序，最多 `limit` 条。
    - `semantic`：`null` = 语义检索未开启；否则 `{error, hits}`。`error` 非空时
      表示**本次只用了字面结果**（原因逐档见 `VectorSearcher._precheck`）；
      `hits` 是语义路**本次参与融合**的篇数（按 `depth = max(limit, 50)` 取，
      不是全库命中总数），`0` 且 `error` 为空 = 索引还没有可比向量。

    ⚠️ **两路取同样深度**（`depth = max(limit, DEFAULT_DEPTH)`）：RRF 的 `1/(k+rank)`
    只有在两路基线一致时才可比。字面取 20 条、向量取 50 条会让「字面第 20 名」
    与「向量第 20 名」拿到不同的分数，排序就取决于谁取了更深的那个巧合。

    ⚠️ **语义独有的命中要回补 title**：向量能召回「一个词都没出现」的文档，
    而那些文档在字面结果里没有条目可继承。`snippet` 留空 —— FTS 的摘要取自
    检索 token 串（`core/search.py` 三列分工），语义命中没有可截取的高亮位置，
    硬造一个摘要等于凭空发明「为什么匹配」。
    """
    depth = max(limit, DEFAULT_DEPTH)
    # 字面路先跑：它自己处理空查询 / 缺引擎 / 索引坏（坏索引是 503 + 可恢复标记，
    # 语义路健康也不该把它掩盖成「能搜」——那是两条独立的库）。
    lexical = svc.search_resources(q, limit=depth).items
    if searcher is None:
        return {"hits": lexical[:limit], "semantic": None}

    ids, semantic = await searcher.fuse(
        q, [h["id"] for h in lexical], limit=limit, depth=depth)

    by_id = {h["id"]: h for h in lexical}
    hits: list[dict] = []
    for doc_id in ids:
        hit = by_id.get(doc_id) or _hit_from_store(svc, doc_id)
        if hit is not None:
            hits.append(hit)
    return {"hits": hits, "semantic": semantic}


def _hit_from_store(svc, doc_id: str) -> dict | None:
    """语义路独有命中 → 列表项形状。

    `None` = 向量库里有、文档表里没有（孤儿）。**不抛错**：孤儿由 worker 的对账
    清理，此刻把它变成一次 500 会让「一个已被删除的文档」毁掉整次搜索。

    ⚠️ `snippet` 给**空串而不是省掉这个键**：前端只取非空 snippet 渲染第二行，
    两者行为相同；但省键会让 `SearchHit` 的 `snippet` 变成可选字段，在下游
    （导出、引用 chip）多出一处 `?? ''`。
    """
    try:
        r = svc.get_resource(doc_id)
    except AppError:
        return None
    return {"id": doc_id, "title": r.get("title") or "", "snippet": ""}


@router.get("/search/status")
async def search_status(request: Request, engine=Depends(get_search_engine)):
    """检索索引状态：字面（FTS5）可用性 / 篇数 / 体积 / 上次重建 + **向量侧**状态。

    ⚠️ **这个端点存在的理由不是「好看」**：索引库坏掉时 `SearchEngine.__init__`
    不再炸启动（文档照样能从磁盘读），检索能力却是没了 —— 而「没搜到」与
    「索引坏了」在界面上是**同一句话**。没有这一份状态，用户永远不知道
    自己该点一下重建。

    ⚠️ `vector` 段的字段全部来自 `app.state`，**不在这里另算一遍判据**：
    `enabled` 是「运行时真的启用了吗」（`sync_vector_runtime` 的结论），
    `reason` 是它给出的原因。照配置重新判一次就会出现「设置页说开着、状态说不开」。
    """
    out = engine.status()
    out["vector"] = _vector_status(request)
    return out


def _vector_status(request: Request) -> dict:
    vec = getattr(request.app.state, "vector_store", None)
    state = getattr(request.app.state, "vector_runtime", None)
    if vec is None:
        out = {"enabled": False, "reason": "向量索引库未初始化", "docs": {}, "chunks": 0,
               "sizeBytes": 0, "identity": None, "lastError": None}
    else:
        out = vec.status()
        runtime = state or {}
        out["enabled"] = bool(runtime.get("enabled"))
        out["reason"] = runtime.get("reason")
    # 引擎选择（2026-10-26 阶段 4）。⚠️ 两个键**在早退分支上也要给** —— 前端类型里
    # 它们是必填，缺键会让「后端没接向量库」与「字段忘了给」在界面上长得一模一样。
    out["model"] = _configured_model(request)
    out["models"] = _embedding_models()
    # 释放策略同理读**配置**而不是 worker：开关关着时也要把用户选的那档显示出来，
    # 而且读 worker 需要它正活着（关了就没有 worker 了）。
    out["release"] = _configured_release(request)
    # 触发方式与攒批阈值同理读配置（未启用时也要显示用户选的那档）。前端据此决定
    # 「阈值输入框」显不显示、「立即建立索引」要不要提示「还没到阈值」。
    out["trigger"] = _configured_trigger(request)
    out["threshold"] = _configured_threshold(request)
    return out


def _configured_model(request: Request) -> str:
    """当前配置的向量化引擎名。

    ⚠️ **读配置而不是读 `runtime`**：`runtime` 只回答「跑起来了吗」，没跑起来时
    设置页的下拉仍需把用户选中的那项显示出来。两者混用会得到「开着但下拉是空的」。
    """
    cfg = (getattr(request.app.state, "config", None) or {}).get("search") or {}
    return cfg.get("vector_model") or DEFAULT_ENGINE


def _configured_release(request: Request) -> str:
    """当前配置的模型释放策略（`never` / `idle` / `immediate`）。

    ⚠️ **回退判据与 `vector_runtime._release_policy` 同源**（都读
    `vector_worker.RELEASE_POLICIES` / `DEFAULT_RELEASE`），但不复用它：那个函数的
    职责是「运行时把配置变成 worker 参数」，顺手记一条 warning；这里只回答「界面上
    该选中哪一档」，不该因为读状态而打日志（设置页每次打开都会读一次）。
    """
    cfg = (getattr(request.app.state, "config", None) or {}).get("search") or {}
    raw = cfg.get("vector_release")
    return raw if raw in RELEASE_POLICIES else DEFAULT_RELEASE


def _configured_trigger(request: Request) -> str:
    """当前配置的触发方式（`auto` / `manual`）。与 `_configured_release` 同款：
    **读配置不打日志**，回退判据与 `vector_runtime._trigger` 同源（同一批常量）。
    """
    cfg = (getattr(request.app.state, "config", None) or {}).get("search") or {}
    raw = cfg.get("vector_trigger")
    return raw if raw in TRIGGERS else DEFAULT_TRIGGER


def _configured_threshold(request: Request) -> int:
    """当前配置的攒批阈值（≥ 1）。回退/夹紧规则与 `vector_runtime._threshold` 同源。"""
    cfg = (getattr(request.app.state, "config", None) or {}).get("search") or {}
    try:
        return max(1, int(cfg.get("vector_threshold")))
    except (TypeError, ValueError):
        return DEFAULT_THRESHOLD


def _embedding_models() -> list[dict]:
    """可选的向量化引擎清单（设置页下拉的数据源）。

    ⚠️ **`ready` 来自引擎自己的 `precheck()`**，不在这里另判一次「依赖装没装」——
    两处判据的失效形态是「下拉说可用、开起来报缺依赖」，而这两个界面隔着一个
    点击动作，用户不会把两句话联系起来。

    ⚠️ **顺序 = 注册顺序**（`EngineRegistry._stores` 是 dict，插入序稳定）：
    云端在前、本地在后 ⇒ 默认值 `dashscope-embedding` 落在第一位，零迁移。
    """
    out: list[dict] = []
    for name in EngineRegistry.list_category("embedding"):
        e = EngineRegistry.get("embedding", name)
        if e is None:
            continue
        check = getattr(e, "precheck", None)
        try:
            reason = check() if check is not None else None
        except Exception as exc:  # noqa: BLE001 —— 自检自己炸了也只是「不可用」
            reason = f"自检失败：{type(exc).__name__}: {exc}"
        out.append({
            "name": name,
            "label": getattr(e, "display_name", "") or name,
            "modelId": getattr(e, "model_id", ""),
            "dimension": int(getattr(e, "dimension", 0) or 0),
            # 前端据此选「免费 / 零外发」那套成本文案 —— 不靠名字前缀猜（改名就失效）
            "local": bool(getattr(e, "is_local", False)),
            "ready": reason is None,
            "reason": reason,
        })
    return out


@router.post("/search/reindex")
async def search_reindex(
    request: Request,
    engine=Depends(get_search_engine),
    store=Depends(get_store),
):
    """全量重建**全文**索引（用户主动触发，**带 VACUUM 回收空间**）。

    ⚠️ **有副作用的 POST 必须过来源守卫**：跨站页面能发 POST（CORS 只拦「读响应」），
    而重建会重写整个 `search.db`。同 `chat` / `generate` 的做法（`core/origin.py`）。

    ⚠️ **显式失败**：重建抛错时让它冒成 HTTP 错误，不返回「部分成功」。索引只有
    「全对」与「坏的」两态，中间态（灌了一半）对用户没有可采取的行动。

    ⚠️ **只管字面索引**。向量索引的重建是另一件事（要花 embedding 的钱），走
    `POST /search/vector/rebuild` —— 两个按钮两种成本，不能藏在同一个动作后面。
    """
    ensure_trusted_request(request)
    transcripts = store.list_transcripts()

    def _get_content(tx_id: str) -> str:
        return search_index_text(store, tx_id)

    # vacuum=True：这是**用户主动**点的，代价（一次全库重写）已经在确认框里告知过。
    # 启动路径**不传**，那里等一次全库重写等于让「打开应用」变慢（见 `rebuild` 注释）。
    # ⚠️ **必须 `to_thread`**：`rebuild` 是同步的整库重写（千篇库几秒到十几秒），
    # 直接 await 一个同步函数会把事件循环整个占住 —— 期间所有请求（含 SSE 心跳）
    # 一起停住，前端表现为「点重建后整个应用卡住」。导出侧同一处已有先例。
    await asyncio.to_thread(engine.rebuild, transcripts, _get_content, vacuum=True)
    return await search_status(request, engine)


class VectorToggleBody(BaseModel):
    enabled: bool
    #: 切换到哪个向量化引擎（不传 = 保持当前配置）。与 `enabled` 独立 —— 用户可以在
    #: 关着的状态下先把引擎选好，也可以开着时直接切（切完会因口径不符停用，见 reason）。
    model: str | None = None
    #: 本地模型的内存释放策略（不传 = 保持当前配置）。三档见 `vector_worker.RELEASE_POLICIES`；
    #: 非法值**显式 400**，不静默退化 —— 用户以为关掉了释放、产物上看不出区别。
    release: str | None = None
    #: 触发方式（不传 = 保持当前配置）。`auto` / `manual` 见 `vector_worker.TRIGGERS`；
    #: 非法值**显式 400** —— 用户以为改成了手动，而 worker 照旧自动花钱建索引。
    trigger: str | None = None
    #: `auto` 档的攒批阈值（不传 = 保持当前配置）。**非法/越界静默夹紧** ——
    #: 错的是**量**不是性质（判据同 `maxNewFolders`）：`0` 会让闸门恒真、把攒批关掉。
    threshold: int | None = None

    model_config = {"extra": "forbid"}      # 拒绝未预期的 key（同 `ConfigUpdate`）


@router.post("/search/vector")
async def search_vector_toggle(
    body: VectorToggleBody,
    request: Request,
    config=Depends(get_config),
    config_lock=Depends(get_config_lock),
):
    """开 / 关语义检索 —— 落盘配置 + **就地启停后台 worker 与融合层**。

    ⚠️ 只改配置不启停 worker 是一个静默失效：用户打开开关、界面显示已开启，
    而索引永远没人建（要等重启才生效）。反向同理：关掉后 worker 还在跑，
    **继续花钱**且状态显示「已关闭」。

    ⚠️ **开关打开本身不花钱**，它只是允许 worker 开始编码；已索引过的篇目
    （块 hash 全中）一次调用都不发。首次全库编码的成本由设置页的开关旁说明，
    不在这个端点的返回值里编故事。

    ⚠️ **`save_config` 必须拿到 `app.state.config` 这个内存对象**（`get_config`
    返回的正是它）：拿磁盘 YAML 直调会让密钥库那条隐藏前提失效（见 CLAUDE.md
    「配置唯一入口」）。这里先改内存、再落盘，与 `config.py::update_config` 同款。
    """
    ensure_trusted_request(request)
    from app.services.vector_runtime import sync_vector_runtime

    # 校验放在锁外：引擎名不合法就**一个字节都不写**（同 `_normalize_and_validate_style`
    # 的取向 —— 错的是「性质」而非「量」时显式报错，不静默夹紧）。
    if body.model is not None and EngineRegistry.get("embedding", body.model) is None:
        raise AppError("VECTOR_NO_ENGINE", f"未找到向量化引擎：{body.model}",
                       status_code=400)
    if body.release is not None and body.release not in RELEASE_POLICIES:
        # 与 `model` 同一条纪律：错的是**性质**（用户以为关掉了释放），不是量。
        raise AppError("VECTOR_BAD_RELEASE",
                       f"未知的释放策略：{body.release}"
                       f"（可选 {' / '.join(RELEASE_POLICIES)}）",
                       status_code=400)
    if body.trigger is not None and body.trigger not in TRIGGERS:
        # 同 `release`：改了触发方式而没生效，界面上完全看不出来（只是「怎么还没建」）。
        raise AppError("VECTOR_BAD_TRIGGER",
                       f"未知的触发方式：{body.trigger}"
                       f"（可选 {' / '.join(TRIGGERS)}）",
                       status_code=400)

    with config_lock:
        s = config.setdefault("search", {})
        s["vector_enabled"] = bool(body.enabled)
        if body.model is not None:
            s["vector_model"] = body.model
        if body.release is not None:
            s["vector_release"] = body.release
        if body.trigger is not None:
            s["vector_trigger"] = body.trigger
        if body.threshold is not None:
            # 静默夹紧（≥ 1）：`0` / 负数会让闸门恒真，等于把「攒批」悄悄关掉 ——
            # 而界面上仍然显示用户填的那个数，看不出它没有生效。
            s["vector_threshold"] = max(1, int(body.threshold))
        save_config(config)

    runtime = await sync_vector_runtime(request.app)
    return {"enabled": bool(runtime.get("enabled")), "reason": runtime.get("reason"),
            "status": _vector_status(request)}


@router.post("/search/vector/sync")
async def search_vector_sync(request: Request):
    """**立即建立索引** —— 叫后台 worker 跳过闸门跑一轮增量。

    - 与 `/rebuild` 的区别：那个清空口径与全部块、重花一遍全库的钱；这个**只补
      还没建的那部分**（已建过的篇目块 hash 全中，一次编码调用都不发）。
    - 与「等自动触发」的区别：`auto` 档要攒够 `vector_threshold` 篇才动手，
      `manual` 档压根不会自动动手 —— 这个按钮是**两条路上都唯一能立刻推进**的出口。
      `auto` 档下它是兜底（小库永远攒不到 100 篇），少了它小库用户会把「没到阈值」
      读成「这功能坏了」。

    ⚠️ **它不直接跑一轮，只是置旗标 + 叫醒循环**：直接 `await run_once()` 会与
    `loop` 里正在跑的那一轮并发，两轮的 `attempted` 集合互不可见 ⇒ 同一篇文档被
    编码两次，计费翻倍而产物完全正常（日志里也看不出异常）。
    ⚠️ **worker 没在跑时显式 400**，不临时造一个：worker 的生命周期归
    `sync_vector_runtime` 管，这里另起一个就是第二份「什么时候该跑」的判据。
    """
    ensure_trusted_request(request)
    worker = getattr(request.app.state, "vector_worker", None)
    if worker is None:
        raise AppError("VECTOR_DISABLED", "语义检索未启用（或索引库不可用）",
                       status_code=400)
    worker.request_sync()
    return {"requested": True, "status": _vector_status(request)}


@router.post("/search/vector/retry-failed")
async def search_vector_retry_failed(request: Request):
    """**重试失败项** —— 把失败的篇目全部重新排队，并立刻跑一轮。

    这是「失败篇没有自助出口」的补丁：`_next_ids` 只取 `pending` 与
    `retry_ids(max_fail)`，故 `fail_count >= MAX_FAIL` 的篇在**任何档位、点任何
    按钮**下都不会再被尝试一次（`force` 跳过的是闸门，不是取件范围）。用户原先
    能做的只有删掉重导。

    ⚠️ **不是「忽略上限」**：重排只是把 `fail_count` 归零，仍是逐篇调用、再次失败
    仍会回到上限 —— 与「放宽 `force` 成无限重试」的区别是**这一次由用户点击背书**，
    一次误点不会变成一次自动计费扫描。

    ⚠️ **必须接着 `request_sync()`**：重排完它们只是 `pending`；`manual` 档下 worker
    没有这个旗标不会醒，`auto` 档下也不该等攒够阈值（失败的篇数多半远小于 100）。
    """
    ensure_trusted_request(request)
    worker = getattr(request.app.state, "vector_worker", None)
    if worker is None:
        raise AppError("VECTOR_DISABLED", "语义检索未启用（或索引库不可用）",
                       status_code=400)
    vec = getattr(request.app.state, "vector_store", None)
    if vec is None:
        raise AppError("VECTOR_UNAVAILABLE", "向量索引库未初始化", status_code=503)
    requeued = vec.requeue_failed()
    if requeued:
        worker.request_sync()
    return {"requeued": requeued, "status": _vector_status(request)}


@router.post("/search/vector/rebuild")
async def search_vector_rebuild(request: Request):
    """**重建**向量索引 —— 清空口径与全部块，重新排队（**要花一遍全库编码的钱**）。

    与 `POST /search/reindex` 刻意分成两个端点：那个是字面的、免费的、毫秒级；
    这个是**计费的全库 embedding**。合成一个端点等于把「刷新」和「付费重算」
    藏在同一个按钮后面。

    ⚠️ 三步的顺序都是有理由的，且**错了都不报错**：

    1. **先停 worker**：它在跑的话，`reset_identity` 之后 `vec_state` 是空的，而
       对账（补 pending）只在 `loop()` 的最外层跑一次 —— 不重启就没有下一轮队列，
       索引永远建不起来，而状态显示「已启用、0 篇待索引」。
    2. 再 `reset_identity`：反过来会让 `ensure_identity` 拿旧身份比对失败，
       于是刚点完重建却仍然「口径不符」。
    3. 最后 `sync_vector_runtime`：它拉起的新 worker 第一件事就是 `reconcile()`，
       把「文档表有、`vec_state` 无」的全部补成 pending —— 这正是重建后要的那批。
       手写一遍 `mark_pending` 循环就是第二份「谁该被索引」的判据（模式 #54）。
    """
    ensure_trusted_request(request)
    # ⚠️ 只 import 本函数用到的两个：`DEFAULT_ENGINE` 与 `EngineRegistry` 已在模块级
    # 导入，在函数体内再导一次会让 `test_closure_shadowing.py` 判「同函数内 import 遮蔽」
    # （那类形态在别的函数里正是 UnboundLocalError 的来源，守卫一律不接受）。
    from app.services.vector_runtime import stop_vector_runtime, sync_vector_runtime

    vec = getattr(request.app.state, "vector_store", None)
    if vec is None:
        raise AppError("VECTOR_UNAVAILABLE", "向量索引库未初始化", status_code=503)

    cfg = (getattr(request.app.state, "config", None) or {}).get("search") or {}
    name = cfg.get("vector_model") or DEFAULT_ENGINE
    engine = EngineRegistry.get("embedding", name)
    if engine is None:
        raise AppError("VECTOR_NO_ENGINE", f"未找到向量化引擎：{name}", status_code=503)

    await stop_vector_runtime(request.app)
    vec.reset_identity(engine.model_id, engine.dimension, DEFAULT_CHUNK_SIZE)
    runtime = await sync_vector_runtime(request.app)
    return {"enabled": bool(runtime.get("enabled")), "reason": runtime.get("reason"),
            "status": _vector_status(request)}
