"""
插件管理路由 — GET/POST/DELETE/PUT /api/plugins/*

基于 plugin_loader 的 PLUGIN_REGISTRY（Phase 1 建立）。
启停「重启后生效」（不做热重载，12 号 C 类）；配置落盘 + 内存即时生效。
主程序不处理任何插件协议维度（license 仅前端展示）。
"""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends

from app.core.dependencies import get_config as get_config_dep, get_config_lock
from app.core.config_io import save_config
from app.core.errors import AppError
from app.engines.plugin_loader import (
    PLUGIN_REGISTRY,
    enumerate_plugins,
    install_plugin_deps,
    set_plugin_config,
    set_plugin_enabled,
    uninstall_plugin,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _require_plugin(name: str) -> None:
    """校验插件存在（不存在抛 AppError）"""
    if name not in PLUGIN_REGISTRY:
        raise AppError("PLUGIN_NOT_FOUND", f"插件不存在: {name}", recoverable=True)


@router.get("/plugins")
async def list_plugins():
    """插件列表（含禁用/门控拒绝的，按 category+name 排序）"""
    return {"plugins": enumerate_plugins()}


@router.post("/plugins/{name}/enable")
async def enable_plugin(name: str, cfg=Depends(get_config_dep), cfg_lock=Depends(get_config_lock)):
    """启用插件 — 写 plugin_states + 同步内存态，重启后引擎注册生效"""
    _require_plugin(name)
    with cfg_lock:
        cfg.setdefault("plugin_states", {})[name] = True
        save_config(cfg)
    set_plugin_enabled(name, True)
    return {"status": "ok", "restart_required": True}


@router.post("/plugins/{name}/disable")
async def disable_plugin(name: str, cfg=Depends(get_config_dep), cfg_lock=Depends(get_config_lock)):
    """禁用插件 — 写 plugin_states + 同步内存态，重启后引擎注销生效"""
    _require_plugin(name)
    with cfg_lock:
        cfg.setdefault("plugin_states", {})[name] = False
        save_config(cfg)
    set_plugin_enabled(name, False)
    return {"status": "ok", "restart_required": True}


@router.delete("/plugins/{name}")
async def delete_plugin(name: str, cfg=Depends(get_config_dep), cfg_lock=Depends(get_config_lock)):
    """卸载插件 — 删 ~/.limo/plugins/ 下声明文件 + 同名目录（含 venv/模型）

    路径穿越防护在 uninstall_plugin（resolve 后校验在插件目录内）。
    Windows 删除运行中 venv 可能占用失败 → 给前端可读反馈。
    """
    _require_plugin(name)
    try:
        removed = uninstall_plugin(name, cfg)
    except AppError:
        raise
    except OSError as e:
        raise AppError(
            "PLUGIN_UNINSTALL_FAILED",
            f"卸载失败：文件可能被占用，请先停止使用该插件后重试",
            detail=str(e)[:120],
            recoverable=True,
        ) from e
    # 清理 config 残留键
    with cfg_lock:
        cfg.get("plugin_states", {}).pop(name, None)
        cfg.get("plugin_configs", {}).pop(name, None)
        save_config(cfg)
    logger.info("Plugin uninstalled: %s (%s)", name, ", ".join(removed))
    return {"status": "ok", "removed": removed}


@router.post("/plugins/{name}/install-deps")
async def install_deps(name: str):
    """一键安装插件依赖 — 建 venv + pip install（国内镜像源）。

    耗时操作放线程池（模式 15）；失败显式化：返回 status=error/no-python + 原因/引导。
    打包版无系统 Python → no-python 降级引导（不静默失败）。
    """
    _require_plugin(name)
    return await asyncio.to_thread(install_plugin_deps, name)


@router.put("/plugins/{name}/config")
async def save_plugin_config(name: str, data: dict[str, Any],
                             cfg=Depends(get_config_dep), cfg_lock=Depends(get_config_lock)):
    """保存插件配置 — 写 plugin_configs 落盘 + set_plugin_config 内存即时生效。

    下一次插件执行时 stdin JSON 即含新 config（断链 2 闭合）。
    """
    _require_plugin(name)
    with cfg_lock:
        cfg.setdefault("plugin_configs", {})[name] = dict(data)
        save_config(cfg)
    set_plugin_config(name, dict(data))
    return {"status": "ok"}
