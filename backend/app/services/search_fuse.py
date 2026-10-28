"""检索编排 —— 字面（FTS5）∪ 语义（向量）融合的**唯一一份**。

从 `routers/search.py` 就地写着的那十几行抽出（2026-10-29）。抽出的理由不是「想让
文件变小」：**对话侧也要做同一件事**（`routers/chat.py::_make_search_docs`），而在
这之前它走的是 `store.list_transcripts({"query": q})` —— 那是**标题子串过滤**，
既不碰 FTS5 也不碰向量。后果是正文里逐字存在的词只要标题没有就搜不到，而界面上
表现为「AI 说库里没有，可我用搜索框自己搜得到」，两边都不报错。
两处各写一遍编排的失效形态正是这个：**一边在融合、一边没在检索**，而它们都"能
搜到东西"，用户只能靠对比结果察觉。

⚠️ **算子一个都不在这里**：切块 / 分组排名 / RRF 在 `core/vector_index.py`；
两路取数、四元组前置校验、超时与降级文案在 `services/vector_search.py::VectorSearcher.fuse`。
本模块只回答「一次融合检索按什么顺序做哪几步、结果怎么组装」。

⚠️ **字面索引坏掉时这里不吞异常**（`QueryError` 照抛）：`/api/search` 要把它变成
503 + 可恢复标记，对话侧要把它降级成标题过滤并**标明降级**。两种处置都对，差别只能
由调用方决定 —— 在这里吞掉等于替两边选了一个，且选错的那一边完全看不出来。
"""

from __future__ import annotations

from app.core.errors import AppError
from app.services.vector_search import DEFAULT_DEPTH


async def fused_search(svc, searcher, q: str, *, limit: int,
                       depth: int | None = None) -> dict:
    """跑一次「字面 ∪（可选的）语义」检索。

    返回 `{"hits": [{id, title, snippet}], "semantic": dict | None}` —— 与
    `GET /api/search` 的响应体逐字相同，因为**它就是那个端点的实现**。

    - `searcher is None`（语义检索没开）→ `semantic: None`，纯字面。
    - 语义路失败 → `semantic["error"]` 有文案、字面结果照给（不抛、不空）。
    - 字面索引坏 / 搜索引擎不在场 → **抛** `QueryError`（见模块 docstring）。

    ⚠️ **两路取同样深度**（`depth = max(limit, DEFAULT_DEPTH)`）：RRF 的
    `1/(k+rank)` 只有在两路基线一致时才可比。字面取 20 条、向量取 50 条会让
    「字面第 20 名」与「向量第 20 名」拿到不同的分数，排序就取决于谁取了更深的巧合。
    """
    depth = max(int(limit), int(depth or DEFAULT_DEPTH))
    # 字面路先跑：它自己处理空查询 / 缺引擎 / 索引坏（坏索引是 503 + 可恢复标记，
    # 语义路健康也不该把它掩盖成「能搜」——那是两条独立的库）。
    lexical = svc.search_resources(q, limit=depth).items
    if searcher is None:
        return {"hits": lexical[:limit], "semantic": None}

    ids, semantic = await searcher.fuse(
        q, [h["id"] for h in lexical], limit=limit, depth=depth)

    by_id = {h["id"]: h for h in lexical}
    hits: list[dict] = []
    for doc_id in ids:
        hit = by_id.get(doc_id) or _hit_from_store(svc, doc_id)
        if hit is not None:
            hits.append(hit)
    return {"hits": hits, "semantic": semantic}


def _hit_from_store(svc, doc_id: str) -> dict | None:
    """语义路独有命中 → 列表项形状。

    向量能召回「一个词都没出现」的文档，而那些文档在字面结果里没有条目可继承。
    `None` = 向量库里有、文档表里没有（孤儿，由 worker 的对账清理）。**不抛错**：
    此刻把它变成一次 500 会让「一个已被删除的文档」毁掉整次搜索。

    ⚠️ `snippet` 给**空串而不是省掉这个键**：前端只取非空 snippet 渲染第二行，
    两者行为相同；但省键会让 `SearchHit` 的 `snippet` 变成可选字段，在下游
    （导出、引用 chip）多出一处 `?? ''`。语义命中也确实没有可截取的高亮位置 ——
    硬造一个摘要等于凭空发明「为什么匹配」。
    """
    try:
        r = svc.get_resource(doc_id)
    except AppError:
        return None
    return {"id": doc_id, "title": r.get("title") or "", "snippet": ""}
