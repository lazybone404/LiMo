"""参考文件暂存路由 — POST /api/references（内存 + TTL，不持久化）。

主程序只做中性能力：接收参考文本 → 暂存 → 返回短 id（生成时传 id）。
不内置任何抓取/爬取能力（法律风险，用户自行实现）。
"""

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.errors import AppError
from app.core.reference_store import save_reference

logger = logging.getLogger(__name__)
router = APIRouter()

# 单文件上限（前端同值校验；1MB ≈ 35 万汉字 ≈ 35 万 token 参考量）
# 2026-09-23 由 200KB 放宽到 1MB：当初的 200KB 是按 128k 上下文模型留的余量，
# 而现在主流模型已到 1M（DeepSeek V4 系列），200KB 反而成了瓶颈。
# 1MB 的选用理由：1M 窗口下「参考文件 + 原文 + 输出」仍留有余量；
# 再大就只有超大窗口模型能用，128k 级模型会直接爆上下文。
MAX_REF_BYTES = 1024 * 1024


class ReferenceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    content: str = ""


@router.post("/references")
async def create_reference(body: ReferenceCreate):
    """暂存参考文件，返回短 id（20 分钟 TTL，重启清空）。"""
    if not body.content.strip():
        raise AppError("REFERENCE_EMPTY", "参考文件内容为空", recoverable=True)
    if len(body.content.encode("utf-8")) > MAX_REF_BYTES:
        raise AppError("REFERENCE_TOO_LARGE", "参考文件超过 1MB，请精简后重试", recoverable=True)
    rid = save_reference(body.name, body.content)
    return {"id": rid}
