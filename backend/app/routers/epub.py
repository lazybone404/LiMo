"""电子书一键解读 — POST /api/epub/generate/{doc_id} · GET /api/epub/task/{doc_id}

对应 app/services/epub_pipeline.py + 后台任务队列（`epub.interpret`）：

- generate：先 `preflight` 把不可解读的情况当场报出来，再提交任务并立即返回。
  幂等键按文档 —— 进行中再点一次拿到同一条，等价于原先的 `EPUB_BUSY`，
  但**不报错**（用户想要的就是那一条）。
- task：这本书上次解读到哪（落盘的 `epub_task.json`），外加「现在在不在跑」
  —— 后者从队列读，不在这里另判一次。

取消**不再单独开端点**：`POST /api/jobs/{id}/cancel` 是唯一入口（任务面板），
软取消语义不变 —— 置旗标、保留已落盘片段、下次从断点续跑。
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core import logging as log
from app.core.dependencies import get_job_queue, get_store
from app.routers.jobs import job_json
from app.services import epub_pipeline
from app.services.job_store import ACTIVE_STATUSES

router = APIRouter()


class EpubGenerateRequest(BaseModel):
    model_id: int | None = None
    deep: bool = False


def _active_job(queue, doc_id: str):
    """该文档进行中的解读任务（没有则 `None`）。

    判据与前端面板逐字相同：`kind == "epub.interpret"` 且 `payload.doc_id` 命中。
    """
    for job in queue.store.list(statuses=ACTIVE_STATUSES, limit=50):
        if job.kind == "epub.interpret" and job.payload.get("doc_id") == doc_id:
            return job
    return None


@router.post("/epub/generate/{doc_id}")
async def generate_epub(doc_id: str, data: EpubGenerateRequest,
                        store=Depends(get_store), queue=Depends(get_job_queue)):
    """提交/续跑 epub 一键解读。**提交即返回**，进度与结果在任务面板。"""
    tx, chapters = epub_pipeline.preflight(store, doc_id)
    job, created = queue.submit(
        "epub.interpret",
        {"doc_id": doc_id, "title": tx.get("title", ""),
         "model_id": data.model_id, "deep": data.deep},
        idempotency_key=f"epub.interpret:{doc_id}",
    )
    log.info("epub 解读已提交", doc_id=doc_id, deep=data.deep, chapters=len(chapters),
             job=job.id[:8], created=created)
    return {"job": job_json(job, queue), "created": created}


@router.get("/epub/task/{doc_id}")
async def get_epub_task(doc_id: str, store=Depends(get_store),
                        queue=Depends(get_job_queue)):
    """这本书的解读状态：逐章进度（落盘）+ 在不在跑（队列）。

    `job_id` 一并给出，前端的取消按钮据此调 `/api/jobs/{id}/cancel` —— 面板
    不必先在自己的 `jobs` 映射里找到这一条。
    """
    job = _active_job(queue, doc_id)
    log.debug("epub 任务查询", doc_id=doc_id, running=job is not None)
    return {
        **epub_pipeline.get_epub_task(store, doc_id),
        "running": job is not None,
        "job_id": job.id if job else None,
    }
