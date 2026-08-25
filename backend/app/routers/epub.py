"""电子书一键解读 — POST /api/epub/generate|task|cancel/{doc_id}

对应 app/services/epub_pipeline.py 的任务管道：
- generate：启动/续跑逐章解读（进行中重复启动 → EPUB_BUSY）
- task：查询进度/状态（无任务 → stage=null, running=false）
- cancel：软取消（置 cancel_event，保留已落盘进度，重开可继续）

epub 不挂 disconnect_watcher：打包端关窗即中断，续跑靠落盘检查点恢复。
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core import logging as log
from app.core.dependencies import get_config, get_store
from app.services import epub_pipeline

router = APIRouter()


class EpubGenerateRequest(BaseModel):
    model_id: int | None = None
    deep: bool = False


@router.post("/epub/generate/{doc_id}")
async def generate_epub(doc_id: str, data: EpubGenerateRequest,
                        store=Depends(get_store), config=Depends(get_config)):
    """启动/续跑 epub 一键解读。失败章跳过继续，产物必定落盘。"""
    log.info("epub 生成请求", doc_id=doc_id, deep=data.deep, model_id=data.model_id)
    return epub_pipeline.start_epub_task(store, config, doc_id,
                                         model_id=data.model_id, deep=data.deep)


@router.get("/epub/task/{doc_id}")
async def get_epub_task(doc_id: str, store=Depends(get_store)):
    """查询 epub 解读任务进度/状态（无任务 → stage=null, running=false）。"""
    log.debug("epub 任务查询", doc_id=doc_id)
    return epub_pipeline.get_epub_task(store, doc_id)


@router.post("/epub/cancel/{doc_id}")
async def cancel_epub(doc_id: str):
    """软取消解读任务（保留已落盘进度，可随时续跑）。无任务返回 cancelled=false。"""
    log.info("epub 取消请求", doc_id=doc_id)
    cancelled = epub_pipeline.cancel_epub_task(doc_id)
    return {"cancelled": cancelled}
