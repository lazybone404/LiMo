"""CLI 接口 —— 只读，返回 `QueryService` 的**原生契约形状**。

`limo` 命令行走这里（默认经 HTTP，`doctor --local` 直开库）。它与既有端点共用
同一个 `QueryService`，差别只在**响应形状**：

| | 既有端点 | 本模块 |
|---|---|---|
| 列表 | `GET /api/transcripts` → **裸 list** | `GET /api/cli/resources` → `Page` |
| 消费者 | 前端文档库（自己分批渲染、不翻页） | CLI / agent（要 `has_more` / `next_cursor` / `truncated`） |

⚠️ **为什么不在既有端点上按参数切换形状**（「传了 `cursor` 就返回对象」）：一个端点
两种形状时，调用方漏传参数不会报错 —— 它**静默拿到裸 list**，翻页循环读不到
`next_cursor` 于是正常结束。少拿数据而看起来成功，是这套契约要消灭的第一号问题。
形状固定、由 `SCHEMA_VERSION` 管版本，比形状自适应可靠。

⚠️ **底层同源**：全部经 `Depends(get_query_service)`。契约由
`tests/test_query_contract.py` 逐字段钉住（HTTP 响应 == 直接调用 `QueryService`）。

⚠️ **本模块的 prefix 只有 `/cli`**：`/api` 由 `main.py` 统一加（模式 #56 —— 双前缀的
失败模式是「两侧测试全绿 + 用户一直 404」）。
"""

from typing import Any, Optional

from fastapi import APIRouter, Depends

from app.core.dependencies import get_query_service
from app.services.query import DEFAULT_MAX_CHARS, DEFAULT_PAGE_SIZE

router = APIRouter(prefix="/cli", tags=["cli"])


@router.get("/status")
async def cli_status(svc=Depends(get_query_service)) -> dict[str, Any]:
    """库概览：文档/文件夹/标签计数 + 类型分布 + schema 版本。"""
    from app.services.query import SCHEMA_VERSION
    return {"schema_version": SCHEMA_VERSION, **svc.stats()}


@router.get("/resources")
async def cli_list_resources(
    svc=Depends(get_query_service),
    source_type: str = "",
    tag: str = "",
    folder_id: Optional[str] = None,
    q: str = "",
    include_descendants: bool = False,
    limit: int = DEFAULT_PAGE_SIZE,
    all: bool = False,
    cursor: Optional[str] = None,
) -> dict[str, Any]:
    """文档列表（分页）。

    `all=true` = 一次给全（`limit` 被忽略）。⚠️ `folder_id` 的三态语义由
    `QueryService` 的 `is not None` 保证：「不传 = 不过滤 / `""` = 只看未分类 /
    `值` = 该文件夹」，故这里**原样透传**，不能写 `folder_id or None`。

    `include_descendants=true` → 连同子文件夹里的文档一并返回（CLI 的 `--recursive`）。
    ⚠️ 默认 false 是刻意与前端对齐的：前端全量拉取后在内存里按树筛，不需要这个参数；
    它是给「不想把所有文档拉下来」的 CLI 用的。
    """
    page = svc.list_resources(
        source_type=source_type or None,
        tag=tag or None,
        folder_id=folder_id,
        query=q or None,
        include_descendants=include_descendants,
        limit=None if all else limit,
        cursor=cursor,
    )
    return page.to_dict()


@router.get("/resources/{doc_id}")
async def cli_get_resource(doc_id: str, svc=Depends(get_query_service)) -> dict[str, Any]:
    """单个文档的完整元数据（**含 rawText**）。

    ⚠️ 与 `GET /api/transcripts/{id}` 的差别只有一处：那里把 `assets/xxx` 改写成了
    绝对 API URL（HTTP 表示层的职责），这里**保持相对路径** —— CLI 拿
    `http://127.0.0.1:7413/...` 没有意义，它要的是能拼本地路径的相对引用。

    `schema_version` 平铺进顶层（`QueryService` 返回的是裸资源投影）：**本模块每条
    响应的顶层都必须有它**，客户端才有一条统一的「这份响应认得吗」判据 —— 让某个
    端点漏掉，CLI 就只能对它免检，而免检的正是那条会读错字段的路径。
    """
    from app.services.query import SCHEMA_VERSION
    return {"schema_version": SCHEMA_VERSION, **svc.get_resource(doc_id)}


@router.get("/resources/{doc_id}/artifacts")
async def cli_list_artifacts(doc_id: str, svc=Depends(get_query_service)) -> dict[str, Any]:
    """该文档**有哪些**产物（清单，不含内容）—— 让调用方不必靠 `exit 3` 盲试六个 part。

    ⚠️ 与下面那条取内容的端点**路径段数不同**，不会互相遮挡（`/artifacts` vs
    `/artifacts/{part}`）；`schema_version` 由本层补上（service 返回裸 list，与
    `list_folders` 同一约定）。
    """
    from app.services.query import SCHEMA_VERSION
    return {"schema_version": SCHEMA_VERSION, "docId": doc_id,
            "items": svc.list_artifacts(doc_id)}


@router.get("/resources/{doc_id}/artifacts/{part}")
async def cli_get_artifact(
    doc_id: str,
    part: str,
    svc=Depends(get_query_service),
    version: Optional[int] = None,
    offset: int = 0,
    max_chars: int = DEFAULT_MAX_CHARS,
    full: bool = False,
    include_raw: bool = False,
) -> dict[str, Any]:
    """取一份产物（`notes` / `polished` / `extract` / `table` / `digitize` / `understand`）。

    `full=true` = 不截断（`max_chars` 被忽略）。默认有限额：大产物一次灌满终端
    对人和 agent 都是负担，而超限时 `truncated` + `continuation` 会给出续读方式
    （**不静默截断**）。

    `version` 显式指定时查不到该版本同样 404，**不静默回退最新版** ——
    「我要第 1 版」拿到第 2 版是最坏的一种成功（见 `QueryService.get_artifact`）。

    `include_raw=true` 额外带上 `rawContent`（**含**自证头的原始字节）。默认不带：
    它是 `content` 的两倍体积，而多数消费者要的是内容不是载体。需要载体的是导出
    ——那边要的是「文件离开 `.limo` 后仍能自证」，本地重建一份头做不到这一点
    （重建出的字节与磁盘上那份**不是同一串**，读路径会给 `identity.version` 补值）。
    """
    art = svc.get_artifact(
        doc_id, part, version=version,
        offset=offset, max_chars=None if full else max_chars,
    )
    return art.to_dict(include_raw=include_raw)


@router.get("/search")
async def cli_search(
    q: str,
    svc=Depends(get_query_service),
    limit: int = DEFAULT_PAGE_SIZE,
) -> dict[str, Any]:
    """全文搜索（FTS5）。

    ⚠️ **不分页**：FTS5 的结果序是 `ORDER BY rank`（相关性），没有稳定键能做 keyset
    游标。到上限时用 `truncated` + `continuation` 表达「还有更多」，调用方要么细化
    关键词、要么调大 `limit`（`QueryService.search_resources` 有详述）。
    """
    return svc.search_resources(q, limit=limit).to_dict()


@router.get("/folders")
async def cli_folders(svc=Depends(get_query_service)) -> dict[str, Any]:
    """全部文件夹（含各自文档数）。⚠️ 裸 list，不分页（见 `list_folders`）。"""
    from app.services.query import SCHEMA_VERSION
    return {"schema_version": SCHEMA_VERSION, "items": svc.list_folders()}
