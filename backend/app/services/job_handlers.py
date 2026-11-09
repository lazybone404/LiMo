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
- 会（合成：一次计费 LLM 调用，重跑等于用户为一次崩溃付两次钱）
  → `False`，标失败并写明「进程中断」，由用户自己决定要不要再来一次。

⚠️ **分类提交刻意没有进来**（2026-11-10）。它起初和合成写在同一条待办里，理由是
「重跑会再花钱」——这条对合成成立，对分类提交不成立：它不调模型，是把同一批
folder/tags **写两遍**，而写两遍的结果与写一遍相同。反过来，队列是**单消费者串行**
的，一次 30 毫秒的写盘排在一场十几分钟的合成后面，用户点「应用」之后界面上会
什么都不发生。判据是「这是不是分钟级操作」，不是「它有没有副作用」。
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from app.core import logging as log
from app.core.errors import AppError
from app.services.job_queue import JobCancelled, JobContext, JobFailed, JobQueue
from app.services.job_store import Job

#: 插件依赖安装的上限。pip 在国内镜像下通常 1–3 分钟；30 分钟还没结束基本是
#: 死锁或网络悬挂，继续占着队列只会挡住后面的任务。
PLUGIN_INSTALL_TIMEOUT = 1800.0

#: 合成任务的**兜底**上限。管线自己有一个 `SYNTH_TOTAL_TIMEOUT`（900s）的硬超时，
#: 正常路径永远先由它收尾并给出「请减少篇数或换模型」这句可行动的文案；这里的
#: 值只防「超时机制本身失效」——留的余量是让管线那条先到。
SYNTH_JOB_TIMEOUT = 1200.0


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


def register_builtin_handlers(
    queue: JobQueue,
    *,
    store,
    config_getter: Callable[[], dict],
    guard,
) -> None:
    """注册内置任务类型。

    ⚠️ 依赖**由调用方注入**（`main.py` 在 startup 里传 `app.state.store` /
    `app.state.config` / `app.state.guard`），而不是 handler 自己 `import` 一个全局
    单例：队列在 startup 里建，此刻 `app.state` 已经就绪，而模块级单例会在测试里
    变成「进程里只剩最后一个 app，前一个的数据目录被默默复用」。
    `config_getter` 是**函数**不是快照 —— 任务可能在用户改完配置之后才排到。
    """

    async def _synthesis(job: Job, ctx: JobContext) -> dict[str, Any]:
        """多篇文档 → 一篇对比综述（composite 文档）。**计费**，不自动重跑。"""
        from app.generation.synthesis import (
            SYNTH_TOTAL_TIMEOUT,
            SynthesisParams,
            run_synthesis,
        )
        from app.llm import CancelledError as LLMCancelledError
        from app.llm import LLMService

        # 清洗在这里再跑一遍（与路由预检**同一个函数**）：payload 是从 `jobs.db`
        # 读回来的，它经过了 JSON 一轮，不能假定形状仍然合法。
        params = SynthesisParams.from_payload(job.payload)
        ctx.report(progress=0.02, message="准备中…")

        llm = LLMService(config_getter(), model_id=params.model_id)
        # 队列的取消是**线程旗标**（同步 handler 也要能读），管线在事件循环里
        # `await`，两者之间没有现成的桥 —— `watch_cancel` 就是那座桥。
        cancel_event = asyncio.Event()
        watcher = asyncio.create_task(ctx.watch_cancel(cancel_event))
        try:
            # 与生成 / 处理抢同一个云端槽位：合成是真实计费调用，不占槽位就会
            # 与 N 路生成并发，把「最多 5 路」的约束从内部绕过去。
            async with guard.semaphore:
                result = await run_synthesis(
                    store, config_getter(), llm, params,
                    cancel_event=cancel_event,
                    on_stage=lambda m: ctx.report(message=m),
                )
            log.info("合成完成", doc_id=result.transcript_id, mode=result.mode,
                     calls=result.llm_call_count, degraded=len(result.degraded),
                     excluded=len(result.excluded))
            # ⚠️ 只返回**纯 JSON**：`JobStore.finish` 用 `json.dumps`，塞一个
            # dataclass 进去会被 `default=str` 静默降级成一串 repr。
            return {
                "id": result.transcript_id,
                "title": result.title,
                "mode": result.mode,
                "degraded": list(result.degraded),
                "excluded": list(result.excluded),
            }
        except asyncio.CancelledError:
            # 进程停机：交给 `JobStore.recover` 按 kind 收尾，这里不写终态
            raise
        except TimeoutError:
            # ⚠️ `str(TimeoutError())` 是空串，那句文案必须自己写
            log.warning("合成整体超时", timeout=SYNTH_TOTAL_TIMEOUT,
                        docs=len(params.doc_ids))
            raise JobFailed(
                f"合成超时（超过 {SYNTH_TOTAL_TIMEOUT // 60} 分钟），"
                "请减少篇数或换用响应更快的模型",
                code="SYNTH_TIMEOUT",
            ) from None
        except LLMCancelledError:
            raise JobCancelled() from None
        except AppError as e:
            # 业务状态（篇数不足 / 源已删 / 窗口不够 / 没配 Key）：`message`
            # 就是给用户的那句话，`code` 原样带给 SSE（前端靠 `SYNTH_CANCELLED`
            # 那一档分叉，其余目前只透传）。
            log.info("合成未通过", code=e.code, message=e.message)
            raise JobFailed(e.message, detail=e.detail or None, code=e.code) from None
        finally:
            watcher.cancel()
            try:
                await llm.aclose()
            except Exception:  # noqa: BLE001 —— 关不掉连接不该覆盖真正的失败原因
                pass

    queue.register(
        "plugin.install",
        _plugin_install,
        label="安装插件依赖",
        # 重跑只是重新 pip install，没有计费也没有半程判定问题。
        retry_on_restart=True,
        timeout=PLUGIN_INSTALL_TIMEOUT,
    )
    queue.register(
        "synthesis",
        _synthesis,
        label="合成对比综述",
        # 一次计费 LLM 调用：进程中断后自动重跑 = 用户为一次崩溃付两次钱。
        retry_on_restart=False,
        timeout=SYNTH_JOB_TIMEOUT,
    )
    log.debug("内置任务类型已注册", kinds=",".join(queue.kinds))
