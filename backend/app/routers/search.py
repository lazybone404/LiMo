"""全文搜索端点 — GET /api/search?q=关键词"""

from fastapi import APIRouter, Request, Depends, Query

from app.core.dependencies import get_store
from app.core.search import SearchEngine

router = APIRouter(prefix="/api", tags=["search"])


def _get_engine(request: Request) -> SearchEngine:
    return request.app.state.search_engine


@router.get("/search")
async def search_transcripts(
    q: str = Query(default="", min_length=1, description="搜索关键词"),
    limit: int = Query(default=20, ge=1, le=50),
    engine: SearchEngine = Depends(_get_engine),
):
    """全文搜索已导入的文档内容。返回匹配的文档 ID、标题、摘要。"""
    return engine.search(q, limit)
