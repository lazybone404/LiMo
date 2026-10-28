"""FastAPI 依赖注入 — 消除 request.app.state 样板代码

用法：在路由函数签名中加入 `store = Depends(get_store)` 等，
替代函数体内的 `store = request.app.state.store`。
"""

from fastapi import Request

from app.services.query import QueryService


def get_store(request: Request):
    """注入 Store 实例"""
    return request.app.state.store


def get_config(request: Request):
    """注入 config dict（注意：返回的是可变引用，修改会影响全局状态）"""
    return request.app.state.config


def get_guard(request: Request):
    """注入 ExecutionGuard 信号量"""
    return request.app.state.guard


def get_config_lock(request: Request):
    """注入 config 写入锁"""
    return request.app.state.config_lock


def get_search_engine(request: Request):
    """注入 `SearchEngine`（索引健康状态 / 重建这两个端点用）。

    ⚠️ 与 `get_query_service` 的 `getattr` 兜底**刻意不同**：这里直接取属性。
    健康状态端点的语义就是「把搜索引擎的真实状态报出来」，取不到实例时报
    `AttributeError`（500）比返回一个假的 `{"available": true}` 诚实得多 ——
    后者会让「索引坏到连实例都没建」这一档显示成一切正常。
    """
    return request.app.state.search_engine


def get_vector_searcher(request: Request):
    """注入语义融合层；未启用时 `None`（`/search` 据此返回 `semantic: null`）。

    ⚠️ **直接读 `app.state.vector_search`，不按配置现造一个**：融合层与后台 worker
    必须**成对存在** —— 只有融合层而没有 worker 的话，查询在用一份永远不再更新的
    索引，产物上表现为「刚编辑过的文档搜不到（语义路）」，而字面路是对的，于是
    看起来像「语义检索时灵时不灵」。这对状态由 `vector_runtime.sync_vector_runtime`
    唯一维护（启动一次、每次开关一次）。
    """
    return getattr(request.app.state, "vector_search", None)


def get_rerank_searcher(request: Request):
    """注入重排编排层；未启用时 `None`（`fused_search` 据此返回 `rerank: null`）。

    ⚠️ 与 `get_vector_searcher` 同款：**直接读 `app.state`，不按配置现造一个** ——
    两者都是「开关 → 运行时对象」的产物，由 `rerank_runtime.sync_rerank_runtime`
    唯一维护。现造一份的失效形态是「配置关着、这里却给了个能跑的对象」，于是
    用户明明关了重排，每次对话检索仍在把候选正文发出去并计费。

    ⚠️ **只有对话侧拿它**。`/api/search` 刻意不注入（那是 250ms 防抖的逐字联想，
    见 `routers/search.py::search_rerank_toggle` 的文档）—— 那边的 `fused_search`
    调用不传 `reranker`，走默认 `None` 分支，与改造前逐字节相同。
    """
    return getattr(request.app.state, "rerank_search", None)


def get_query_service(request: Request) -> QueryService:
    """注入查询层服务 —— HTTP 与 CLI 共用的只读契约（见 `app/services/query.py`）。

    ⚠️ **这里在请求时构造，而不是在 startup 里挂到 `app.state` 上**。理由是初始化
    顺序的硬约束，不是性能优化：`main.py` 先建 `Store`（配置加载后立刻建），
    `SearchEngine` 建在其后（它依赖 data_dir，还要从零重建 FTS 索引）。若在 `Store`
    旁边顺手构造一个长驻的 `QueryService` —— 那是更自然的写法 —— 它会**永久**持有
    `search_engine=None`，于是 `search_resources` 变成**静默空结果**：搜索不报错，
    只是永远搜不到东西，而界面上看起来就和「没有匹配」一模一样。

    构造本身只存两个引用（零副作用、零 I/O），所以「每次请求新建」既最简单又
    天然正确：此刻 startup 早已跑完，两个原语必然就绪。

    `getattr` 兜底而非直接取属性：测试里自建的小 app 不必挂 `search_engine`，
    而**真正需要搜索引擎的那条路径**（`search_resources`）会对 `None` 显式抛错
    （`QUERY_NO_SEARCH_ENGINE`），不会被这里的兜底掩盖。
    """
    return QueryService(
        request.app.state.store,
        getattr(request.app.state, "search_engine", None),
    )
