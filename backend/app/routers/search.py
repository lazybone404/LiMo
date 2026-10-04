"""全文搜索端点 — GET /api/search?q=关键词"""

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import get_query_service

# ⚠️ **不要在这里写 `prefix="/api"`**：`main.py` 用 `include_router(..., prefix="/api")`
# 统一挂载（全仓 17 个 router 里另外 16 个都是裸 `APIRouter()`）。两处都写会得到
# `/api/api/search`——前端 `apiFetch(\`${BASE}/search\`)` 打的是 `/api/search`，
# 于是**搜索一直 404 而两侧测试全绿**（前端测试 mock 掉了 `api/search` 模块，后端
# `test_search.py` 测的是 SearchEngine 而不是 HTTP 路径；铁律 #51「能力存在 ≠ 该路径
# 在用它」的又一形态）。同类事故在 2026-07-10 已发生过一次（那次在前端侧，6 处）。
router = APIRouter(tags=["search"])


@router.get("/search")
async def search_transcripts(
    q: str = Query(default="", min_length=1, description="搜索关键词"),
    limit: int = Query(default=20, ge=1, le=50),
    svc=Depends(get_query_service),
):
    """全文搜索已导入的文档内容。**返回裸 list**（`[{id,title,snippet}]`）——保持既有
    响应形状，前端 `SearchBar` 与文档库都按数组消费。

    ⚠️ 通过 `QueryService` 而不是直接 `app.state.search_engine`：搜索同样是**读路径**，
    它要跟 CLI 的 `limo search` 共用一条实现（否则就是第二份「怎么搜」的判据）。
    `truncated` 提示在 HTTP 侧被丢弃——前端的做法是「限 100 条 + 到顶时提示细化关键词」，
    它的阈值与 CLI 不同，故由各自的 adapter 决定怎么表达「没给全」。
    """
    return svc.search_resources(q, limit=limit).items
