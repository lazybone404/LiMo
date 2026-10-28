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

⚠️ `extra_queries`（2026-10-29）是「原词 + 改写」的落点：同一个问题的其他说法各检索
一次，再把几条 id 序列交给 `core/vector_index.rrf` 合并。**RRF 本体仍不在这里**，
这里只多跑几次 `_one`。做法与数字见 `_experiments/06-query-rewrite/`；
只有对话侧用它（`routers/chat.py::_make_search_docs`），`/api/search` 不传。

⚠️ `reranker`（2026-10-29）是重排层：融合完的那一条再按相关性排一遍。**它不动候选**
—— 候选池在融合阶段按 `reranker.candidates` 取够，重排只改顺序，所以 H@50 类指标
必须与不重排时相同（`_experiments/07-rerank/` 的不变式）。`reranker=None` 时
（默认，`/api/search` 恒如此）走的还是原来那条路，逐字节不变。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.core import logging as log
from app.core.errors import AppError
from app.core.search import rerank_window
from app.core.vector_index import rrf
from app.services.vector_search import DEFAULT_DEPTH, RRF_K

_MARK_RE = re.compile(r"</?mark>")


async def fused_search(svc, searcher, q: str, *, limit: int,
                       depth: int | None = None,
                       extra_queries: Sequence[str] = (),
                       reranker=None) -> dict:
    """跑一次「字面 ∪（可选的）语义 →（可选的）重排」检索。

    返回 `{"hits": [{id, title, snippet}], "semantic": dict | None,
    "rerank": dict | None}` —— `/api/search` 的响应体是它的前两个键，第三个别处
    （`rerank: null`）不影响的消费方。

    - `searcher is None`（语义检索没开）→ `semantic: None`，纯字面。
    - 语义路失败 → `semantic["error"]` 有文案、字面结果照给（不抛、不空）。
    - 字面索引坏 / 搜索引擎不在场 → **抛** `QueryError`（见模块 docstring）。
    - `reranker is None`（重排没开）→ `rerank: None`，一个额外调用都不发。
    - 重排失败/超时 → `rerank["error"]` 有文案、**顺序逐字等于融合结果**。

    ⚠️ **两路取同样深度**（`depth = max(limit, DEFAULT_DEPTH)`）：RRF 的
    `1/(k+rank)` 只有在两路基线一致时才可比。字面取 20 条、向量取 50 条会让
    「字面第 20 名」与「向量第 20 名」拿到不同的分数，排序就取决于谁取了更深的巧合。

    `extra_queries`（2026-10-29）：同一问题的其他说法（来自 `services/query_rewrite`），
    **各检索一次后与原词一起进 RRF**。合并时不加权重 —— 实验里「原词 + 3 条改写」
    的推荐形态就是等权，而给原词加权是一个没测过的超参（见
    `_experiments/06-query-rewrite/`）。空序列 ⇒ 与改造前**逐字相同**的最后一步。

    ⚠️ **重排的 `query` 是原词 `q`，不是改写词**：改写词的任务是「多召回几条」，
    而重排要回答的是「用户实际问的那个问题，哪篇最相关」。同理候选文本的窗口
    也按 `q` 选（`_candidate_texts`）—— 用改写词选窗会把候选文本也换掉，那时
    重排的收益里混着「窗口选得更好」这一份，实验就不再是单变量。
    """
    depth = max(int(limit), int(depth or DEFAULT_DEPTH))
    extras = [t for t in extra_queries if t and t != q]

    #: 候选池要取多少条。开了重排就要取到 `rerank_candidates`（重排只能在**已经
    #: 召回**的候选里挑），没开就是 `limit` —— 与改造前逐字相同。
    want = int(limit)
    if reranker is not None:
        want = max(want, int(getattr(reranker, "candidates", 0) or 0))

    if not extras:
        hits, semantic = await _one(svc, searcher, q, want, depth)
    else:
        # ⚠️ **串行而非 `asyncio.gather`**：耗时大头是前面那次 LLM 调用（实测 1.06s），
        # 这 4 次检索并发到顶也省不到 1 秒；而向量路的 `_ensure_loaded` 没有锁，四条同时
        # 进冷启动会把 22.8MB 权重读四遍（同一进程内 warmup 已加载时才是纯内存快路）。
        groups: list[list[str]] = []
        pool: dict[str, dict] = {}
        semantic = None
        for term in [q, *extras]:
            term_hits, sem = await _one(svc, searcher, term, depth, depth)
            groups.append([h["id"] for h in term_hits])
            for h in term_hits:
                pool.setdefault(h["id"], h)
            if semantic is None:
                # `semantic` 自证段取**原词那次**：口径不符 / 没建过 / 库不可用都是全局
                # 状态，改写那几次只会重复同一句话；只有「query embedding 超时」可能
                # 个别失败，而那种情况报不报都在 error 与 hits 上分得清。
                semantic = sem

        # ⚠️ **这里必须切 `want` 而不是 `limit`**：切 `limit` 会把候选池压回
        # 10 条左右，重排就只能在 10 条里排 —— 那时它的实测收益里混着「候选变少
        # 了」的损失，而不是「排序变好了」。
        merged = rrf(groups, RRF_K)[:want]
        hits = [pool[i] for i in merged if i in pool]

    if reranker is None:
        return {"hits": hits[:limit], "semantic": semantic, "rerank": None}

    texts = _candidate_texts(svc, q, hits)
    order, report = await reranker.rerank(q, texts)
    return {"hits": [hits[i] for i in order][:limit],
            "semantic": semantic, "rerank": report}


async def _one(svc, searcher, term: str, take: int,
               depth: int) -> tuple[list[dict], dict | None]:
    """单个检索式走一遍「字面 → （可选）语义融合」。

    `take` 是返回条数、`depth` 是两路各取多少条。两者分开是为了合并场景：
    每一路都要拿到 `depth` 条才能让 RRF 真的在 50 名以内排序，
    而最终结果只要 `limit` 条（见 `fused_search` 的调用点）。

    字面路先跑：它自己处理空查询 / 缺引擎 / 索引坏（坏索引是 503 + 可恢复标记，
    语义路健康也不该把它掩盖成「能搜」——那是两条独立的库）。
    """
    lexical = svc.search_resources(term, limit=depth).items
    if searcher is None:
        return lexical[:take], None

    ids, semantic = await searcher.fuse(
        term, [h["id"] for h in lexical], limit=take, depth=depth)

    by_id = {h["id"]: h for h in lexical}
    hits: list[dict] = []
    for doc_id in ids:
        hit = by_id.get(doc_id) or _hit_from_store(svc, doc_id)
        if hit is not None:
            hits.append(hit)
    return hits, semantic


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


def _candidate_texts(svc, q: str, hits: list[dict]) -> list[str]:
    """给每条候选取一段**与查询最相关**的正文窗口，作为送进重排的文本。

    ## 兜底链（每一档都是「上一档拿不到」，不是「上一档不好」）

    1. `svc.bodies(ids)` —— 索引里的清洗后正文，**唯一不读盘的来源**。它取的是
       `body` 列（原文，不是二字组串），见 `SearchEngine.bodies` 的注释。
    2. `svc.get_resource(id).rawText` —— 索引里没有这篇时（刚导入、索引坏了）读库。
       这里多一次读盘，但重排本来就要一次网络往返，读盘不在关键路径上。
    3. `hit["snippet"]` —— 字面命中的高亮摘要，剥掉 `<mark>`。它只有 90 字，
       而且**中文查询下它可能退化成正文开头**（见 `rerank_window` 的说明），
       所以它是兜底不是主路。
    4. `hit["title"]` —— 标题也算一段有意义的文本（很多文档的标题就是它的主题）。
    5. `id` —— 最后的兜底只为了让**送进引擎的列表里不存在空串**。云端对空
       `documents` 项的行为未测（可能 400，也可能给个无意义的分），两种都比
       本地替换成一篇「同名文档」更难查。

    ## 每一条的窗口都按**原词 `q`** 选

    不是按改写词（见 `fused_search` 的说明）：换一个词选窗，重排的收益里就混进了
    「窗口选得更好」这一份，实验不再是单变量。
    """
    ids = [h["id"] for h in hits]
    bodies: dict[str, str] = {}
    get_bodies = getattr(svc, "bodies", None)
    if get_bodies is not None:
        try:
            bodies = get_bodies(ids) or {}
        except Exception as e:  # noqa: BLE001 —— 取候选正文失败只让兜底链降一档
            # 不抛：兜底链的下一档（读库）本来就覆盖了「索引里没有」这个情形，
            # 而 `bodies()` 自己已经把 sqlite 错误吞成 `{}`。这里是最后一道。
            log.warning("取候选正文失败，改走读库兜底", err=f"{type(e).__name__}: {e}"[:160])

    out: list[str] = []
    for h in hits:
        doc_id = h["id"]
        body = bodies.get(doc_id) or ""
        if not body:
            try:
                body = svc.get_resource(doc_id).get("rawText") or ""
            except AppError:
                body = ""
            except Exception:  # noqa: BLE001 —— 与上一条同义：拿不到就走下一档
                body = ""
        text = rerank_window(body, q) if body else ""
        if not text:
            text = _MARK_RE.sub("", h.get("snippet") or "").strip()
        if not text:
            text = (h.get("title") or "").strip()
        if not text:
            text = doc_id
        out.append(text)
    return out
