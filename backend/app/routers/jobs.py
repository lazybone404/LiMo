"""长操作任务队列的 HTTP 面 — POST /api/jobs / GET /api/jobs / cancel / dismiss。

## 这一层不做什么

- **不实现任务**：每种 kind 的实现在 `services/job_handlers.py`，这里只按 `kind`
  字符串转交。加一个任务类型不该动本文件。
- **不拼中文名**：`label` 取自队列注册表（`JobQueue.kinds`），随每条任务下发。
  前端再抄一份「plugin.install → 安装插件依赖」就是第二真相源 —— 后端加一种任务，
  前端会静默显示成裸 kind 字符串。

## 状态过滤

`GET /api/jobs?status=active` 是前端的默认取法（`queued` + `running`）。
默认（不带参数）取「进行中 + 未忽略的失败」—— 失败要留在界面上等人处理，
但已经在界面上点过 × 的不再回来。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.core.dependencies import get_job_queue
from app.core.errors import AppError
from app.services.job_store import ACTIVE_STATUSES, FAILED, Job, TERMINAL_STATUSES

router = APIRouter()


class CreateJobRequest(BaseModel):
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    #: 幂等键可选。给了就保证「同一件事不会同时跑两遍」；上一条结束后键即释放。
    idempotency_key: str | None = None


def job_json(job: Job, queue) -> dict[str, Any]:
    """任务的对外形状。**别的路由提交任务时也走这里**（`epub.py` 的 generate
    直接返回刚建的那条）—— 各拼一份的话，`JobInfo` 加字段时总有一处漏掉，
    而前端拿到的是「少一个字段」而不是错误。"""
    out = job.to_dict()
    # 未注册的 kind（降级安装、改了名字）回落成原字符串，而不是让整条记录读不出来。
    out["label"] = queue.kinds.get(job.kind, job.kind)
    return out


def _parse_statuses(raw: str | None) -> tuple[str, ...]:
    """过滤参数 → 状态元组。省略时取「进行中 + 失败」—— 见模块文档。"""
    if not raw:
        return (*ACTIVE_STATUSES, FAILED)
    parts = [s.strip() for s in raw.split(",") if s.strip()]
    if "active" in parts:
        # `active` 与具体状态混写时以 active 为准：调用方想要的都是「现在在跑的」。
        return ACTIVE_STATUSES
    unknown = [p for p in parts if p not in (*ACTIVE_STATUSES, *TERMINAL_STATUSES)]
    if unknown:
        raise AppError("JOB_BAD_STATUS", f"未知的任务状态：{','.join(unknown)}")
    return tuple(parts)


@router.post("/jobs", status_code=202)
async def create_job(body: CreateJobRequest, queue=Depends(get_job_queue)):
    """提交一个任务。`created=false` 表示幂等键命中了正在跑的同名任务。"""
    try:
        job, created = queue.submit(body.kind, body.payload, body.idempotency_key)
    except ValueError as e:
        # 未注册的 kind 是请求本身的问题（不是服务端故障），报 400 并带上原话。
        raise AppError("JOB_UNKNOWN_KIND", str(e)) from e
    return {"job": job_json(job, queue), "created": created}


@router.get("/jobs")
async def list_jobs(
    status: str | None = Query(None, description="active / queued,running / 省略=进行中+未忽略的失败"),
    limit: int = Query(50, ge=1, le=200),
    queue=Depends(get_job_queue),
):
    statuses = _parse_statuses(status)
    jobs = queue.store.list(statuses, limit=limit)
    return {"jobs": [job_json(j, queue) for j in jobs]}


# ⚠️ 必须排在 `/jobs/{job_id}` **之前**：FastAPI 按声明顺序匹配，反过来写时
#    `/jobs/kinds` 会被当成 job_id="kinds" 走进下一条路由。
@router.get("/jobs/kinds")
async def list_kinds(queue=Depends(get_job_queue)):
    """已注册的任务类型与中文名。前端据此渲染，不在前端抄一份。"""
    return {"kinds": queue.kinds}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, queue=Depends(get_job_queue)):
    job = queue.store.get(job_id)
    if job is None:
        raise AppError("JOB_NOT_FOUND", "任务不存在", status_code=404)
    return {"job": job_json(job, queue)}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, queue=Depends(get_job_queue)):
    """请求取消。`queued` 的立即落成 `cancelled`；`running` 的置旗标，
    当前步骤结束后收尾（同步 handler 杀不掉，见 `job_queue` 模块文档）。"""
    job = queue.request_cancel(job_id)
    if job is None:
        raise AppError("JOB_NOT_FOUND", "任务不存在", status_code=404)
    return {"job": job_json(job, queue)}


@router.post("/jobs/{job_id}/dismiss")
async def dismiss_job(job_id: str, queue=Depends(get_job_queue)):
    """收起一条已结束的任务（失败重试过、或用户只是不想再看见它）。"""
    job = queue.store.dismiss(job_id)
    if job is None:
        raise AppError("JOB_NOT_FOUND", "任务不存在", status_code=404)
    return {"job": job_json(job, queue)}
