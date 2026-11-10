"""
插件管理路由 — GET/POST/DELETE/PUT /api/plugins/*

基于 plugin_loader 的 PLUGIN_REGISTRY（Phase 1 建立）。
启停「重启后生效」（不做热重载，12 号 C 类）；配置落盘 + 内存即时生效。
主程序不处理任何插件协议维度（license 仅前端展示）。
"""

import asyncio
import io
import logging
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Request, UploadFile

from app.core.dependencies import get_config as get_config_dep, get_config_lock
from app.core.config_io import save_config
from app.core.errors import AppError
from app.core.origin import ensure_trusted_request
from app.engines.plugin_loader import (
    PLUGIN_REGISTRY,
    enumerate_plugins,
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
    """插件列表（含禁用/门控拒绝的，按 category+name 排序）

    ⚠️ 放线程池（模式 15）：`enumerate_plugins` 会调每个插件的 `precheck()`，
    而它是**任意第三方代码** —— 真 import 一个原生库要几百毫秒，`deps_installed`
    还要 stat 文件系统。放在事件循环上会让整个后端在那几百毫秒里不响应
    （SSE 心跳、健康检查一起停），现象是「打开插件设置页时应用卡一下」。
    """
    return {"plugins": await asyncio.to_thread(enumerate_plugins)}


@router.post("/plugins/install")
async def install_plugin_zip(request: Request, file: UploadFile = File(...),
                             cfg=Depends(get_config_dep)):
    """从 zip 插件包安装插件 — 解压到 ~/.limo/plugins/，重启后端后生效。

    安全：zip slip 防护（条目路径必须落在解压目录内）+ 大小上限 50MB。
    插件声明 .py 是用户主动选择的第三方代码（固有风险自担），但解压路径安全由本端保证。
    返回 hasRequirements 供前端提示「重启后一键安装依赖」。
    """
    ensure_trusted_request(request)
    fname = (file.filename or "").lower()
    if not fname.endswith(".zip"):
        raise AppError("PLUGIN_ZIP_REQUIRED", "请上传 .zip 插件包（从插件仓库 Release 下载）", recoverable=True)

    data = await file.read()
    if len(data) > 50 * 1024 * 1024:
        raise AppError("PLUGIN_ZIP_TOO_LARGE", "插件包超过 50MB 上限", recoverable=True)

    plugins_dir = Path(cfg.get("data_dir", "")) / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    plugins_root = plugins_dir.resolve()

    tmp = Path(tempfile.mkdtemp(prefix="plugin_install_"))
    try:
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise AppError("PLUGIN_ZIP_INVALID", "无效的 zip 插件包", recoverable=True)

        with zf:
            names = zf.namelist()
            if not names:
                raise AppError("PLUGIN_ZIP_EMPTY", "插件包为空", recoverable=True)
            # zip slip 防护：所有条目解压后必须落在 tmp 内（拒绝 ../ 与绝对路径）
            for info in zf.infolist():
                target = (tmp / info.filename).resolve()
                if not target.is_relative_to(tmp.resolve()):
                    raise AppError("PLUGIN_ZIP_PATH", "插件包包含非法路径，已拒绝解压", recoverable=True)
            zf.extractall(tmp)

        # 智能 strip 单一顶层目录（GitHub source zip 带仓库名目录）
        src = tmp
        top_names = {n.split("/")[0] for n in names if n}
        if len(top_names) == 1:
            top = next(iter(top_names))
            if (tmp / top).is_dir():
                src = tmp / top

        # 必须有 EnginePlugin 声明 .py（顶层）
        py_files = [p for p in src.glob("*.py") if p.name not in ("__init__.py", "setup.py")]
        if not py_files:
            raise AppError("PLUGIN_NO_DECL", "插件包缺少 EnginePlugin 声明 .py 文件", recoverable=True)

        # 拷贝到 plugins/（覆盖同名）；Windows 下已装插件 venv 文件占用时 rmtree 抛 OSError → 明确提示
        try:
            for item in src.iterdir():
                dest = plugins_root / item.name
                if dest.exists():
                    if dest.is_dir():
                        shutil.rmtree(dest)
                    else:
                        dest.unlink()
                shutil.move(str(item), str(dest))
        except OSError:
            raise AppError(
                "PLUGIN_IN_USE",
                "同名插件正在使用（可能依赖/venv 被占用），请重启后端后重试安装",
                recoverable=True,
            )

        has_req = any((plugins_root / p.stem / "requirements.txt").exists() for p in py_files)
        names_installed = [p.stem for p in py_files]
        logger.info("Plugin installed from zip: %s (has_req=%s)", names_installed, has_req)
        return {"status": "ok", "plugins": names_installed,
                "hasRequirements": has_req, "restart_required": True}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@router.post("/plugins/{name}/enable")
async def enable_plugin(request: Request, name: str, cfg=Depends(get_config_dep), cfg_lock=Depends(get_config_lock)):
    """启用插件 — 写 plugin_states + 同步内存态，重启后引擎注册生效"""
    ensure_trusted_request(request)
    _require_plugin(name)
    with cfg_lock:
        cfg.setdefault("plugin_states", {})[name] = True
        save_config(cfg)
    set_plugin_enabled(name, True)
    return {"status": "ok", "restart_required": True}


@router.post("/plugins/{name}/disable")
async def disable_plugin(request: Request, name: str, cfg=Depends(get_config_dep), cfg_lock=Depends(get_config_lock)):
    """禁用插件 — 写 plugin_states + 同步内存态，重启后引擎注销生效"""
    ensure_trusted_request(request)
    _require_plugin(name)
    with cfg_lock:
        cfg.setdefault("plugin_states", {})[name] = False
        save_config(cfg)
    set_plugin_enabled(name, False)
    return {"status": "ok", "restart_required": True}


@router.delete("/plugins/{name}")
async def delete_plugin(request: Request, name: str, cfg=Depends(get_config_dep), cfg_lock=Depends(get_config_lock)):
    """卸载插件 — 删 ~/.limo/plugins/ 下声明文件 + 同名目录（含 venv/模型）

    路径穿越防护在 uninstall_plugin（resolve 后校验在插件目录内）。
    Windows 删除运行中 venv 可能占用失败 → 给前端可读反馈。
    """
    ensure_trusted_request(request)
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


@router.post("/plugins/{name}/install-deps", status_code=202)
async def install_deps(request: Request, name: str):
    """一键安装插件依赖 — 建 venv + pip install（国内镜像源）。**提交后台任务，不等结果**。

    ⚠️ **2026-11-10 起不再同步等 pip 跑完**。原先的问题有两个，第二个更要紧：
    ① 用户切走页面时组件卸载，进度与结果一起丢了（而 pip 照常在跑）；
    ② 回到设置页时按钮已复位，可以对**同一个插件**再点一次 —— 两个 pip 并发写
       同一个 `.venv`，产物大概率坏掉而两次都「成功」。

    现在返回 `{job, created}`（202），进度 / 结果 / 失败都在任务面板里。幂等键
    `plugin.install:{name}` 保证同一插件同时只有一条安装任务（`created=false`
    表示命中了正在跑的那条）。
    """
    ensure_trusted_request(request)
    _require_plugin(name)
    # ⚠️ **刻意不用 `Depends(get_job_queue)`**：FastAPI 在进入函数体之前解析依赖，
    # 于是「队列没起来」会先于 `ensure_trusted_request` 炸成一个 500 —— 陌生来源
    # 拿到 500 而不是 403，守卫测试也会因此失去区分力（跨站请求本来什么都不该做）。
    # 守卫必须排在最前，所以这里在函数体里取。
    queue = request.app.state.job_queue
    from app.routers.jobs import job_json

    job, created = queue.submit(
        "plugin.install", {"name": name}, f"plugin.install:{name}",
    )
    logger.info("插件依赖安装已入队：%s (job=%s created=%s)", name, job.id[:8], created)
    return {"job": job_json(job, queue), "created": created}


@router.put("/plugins/{name}/config")
async def save_plugin_config(request: Request, name: str, data: dict[str, Any],
                             cfg=Depends(get_config_dep), cfg_lock=Depends(get_config_lock)):
    """保存插件配置 — 写 plugin_configs 落盘 + set_plugin_config 内存即时生效。

    下一次插件执行时 stdin JSON 即含新 config（断链 2 闭合）。

    ⚠️ **这份配置是明文**（`config.yaml` 的 `plugin_configs`），**不走 DPAPI 密钥库** ——
    那套成对表按扁平键名组织，装不下任意形状的嵌套 dict。插件作者应在 `config_schema`
    里只放路径 / 模型名 / 枚举这类非敏感值；需要凭据的走环境变量或系统凭据管理器。
    见 `docs/plugins.md`「插件配置怎么进子进程」。
    """
    ensure_trusted_request(request)
    _require_plugin(name)
    with cfg_lock:
        cfg.setdefault("plugin_configs", {})[name] = dict(data)
        save_config(cfg)
    set_plugin_config(name, dict(data))
    return {"status": "ok"}
