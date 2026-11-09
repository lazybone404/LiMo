"""内置任务类型的实现与注册。

一个 kind = 一条 `register(...)`。加新类型只在这张表里加一行 + 写一个
`(job, ctx) -> result` 的函数，不在别处改分派。

## 哪些东西该迁到这里

判据是**三件事同时成立**：跑得久（分钟级）· 有结果要告诉用户 · 组件卸载不该丢。
只有其中一件的不算 —— 比如重新索引（已有 `VectorWorker` 与状态端点）、
导入 / 转录 / 生成（已在 `processingTasks`，那条链的生命周期本来就跟着文档）。

## 恢复语义（`retry_on_restart`）怎么定

问一句：**重跑一遍会不会再花钱，或者留下半程副作用？**

- 不会（插件依赖安装：重跑只是重新 `pip install`）→ `True`，重启后接着跑。
- 会（合成、分类提交：一次计费 LLM 调用；重跑可能把已提交的结果再提交一遍）
  → `False`，标失败并写明「进程中断」，由用户自己决定要不要再来一次。
  自动重跑等于用户为一次崩溃付两次钱。
"""

from __future__ import annotations

from typing import Any

from app.core import logging as log
from app.services.job_queue import JobContext, JobFailed, JobQueue
from app.services.job_store import Job

#: 插件依赖安装的上限。pip 在国内镜像下通常 1–3 分钟；30 分钟还没结束基本是
#: 死锁或网络悬挂，继续占着队列只会挡住后面的任务。
PLUGIN_INSTALL_TIMEOUT = 1800.0


def _plugin_install(job: Job, ctx: JobContext) -> dict[str, Any]:
    """跑 `pip install -r requirements.txt`（插件自己的 `.venv`）。"""
    name = str(job.payload.get("name") or "").strip()
    if not name:
        raise JobFailed("缺少插件名")

    ctx.report(progress=0.05, message="正在准备安装环境…")
    # 延迟 import：`plugin_loader` 连带拉起引擎注册表，放在模块顶部会让任何
    # import `job_handlers` 的调用方（含测试）都付这笔初始化开销。
    from app.engines.plugin_loader import install_plugin_deps

    ctx.check_cancel()
    result = install_plugin_deps(name)
    status = result.get("status")

    if status in ("ok", "inproc", "no-requirements"):
        # `inproc` / `no-requirements` 是「本来就没有可装的东西」，对用户就是成功。
        ctx.report(progress=1.0, message="依赖安装完成")
        return {"status": status, "python": result.get("python")}

    if status == "no-python":
        raise JobFailed(
            result.get("hint") or "未找到可用的 Python 解释器",
            detail=result.get("error"),
        )

    raise JobFailed(
        result.get("error") or "依赖安装失败",
        detail=result.get("hint"),
    )


def register_builtin_handlers(queue: JobQueue) -> None:
    queue.register(
        "plugin.install",
        _plugin_install,
        label="安装插件依赖",
        # 重跑只是重新 pip install，没有计费也没有半程判定问题。
        retry_on_restart=True,
        timeout=PLUGIN_INSTALL_TIMEOUT,
    )
    log.debug("内置任务类型已注册", kinds=",".join(queue.kinds))
