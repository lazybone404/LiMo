"""FastAPI 依赖注入 — 消除 request.app.state 样板代码

用法：在路由函数签名中加入 `store = Depends(get_store)` 等，
替代函数体内的 `store = request.app.state.store`。
"""

from fastapi import Request


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
