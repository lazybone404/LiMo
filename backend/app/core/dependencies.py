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
