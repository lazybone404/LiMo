"""向量索引的纯算子：切块 / 分组排名 / RRF 融合。

**零重依赖**：只用 `numpy`（已在核心依赖里 —— 视频帧质量那一路在用）。
本地模型的 ONNX 句柄**刻意不在这里**（重依赖走插件口子，见
`scripts/embed_local.py` 与 `docs/search-retrieval.md` §6.1）。

⚠️ **只此一份**：产品侧（后台 worker 切块、查询侧融合）与评测脚本
（`scripts/eval_search.py --vector`、`_experiments/02-embedding-rrf/eval_ab.py`）
都 import 这里。抄一份的失效形态不是「多写几十行」，而是**评测与产品的切块规则
悄悄分叉** —— 那时所有 93% / 83% 的数字描述的都是另一套实现，而两边都报成功。

三个判据来自 2026-10-26 的实测（`_experiments/02-embedding-rrf/`、`03-public-datasets/`）：

- 固定字数切 200（`chunk_flat`）是默认。H2 语义切**没跑赢**：同库同轮对照下纯向量
  79% / 76%（H2 200 / 400）vs 固定切 200 的 83%；RRF 层面都能到 86%，
  但那是字面路把差额补上的 —— 看「纯向量」列才是向量质量本身。
  与早年那个「标题只取一个字」的正则缺陷**无关**（修正前后逐档完全一致，见 `chunk_h2`）
- 文档得分 = 各块余弦**最大值**，不是平均（平均会稀释「只有一段相关」的长文档）
- RRF `k=10` 在 29 题上优于 `k=60` —— 这是**经验值不是调优结果**，落库时保留可配
"""

from __future__ import annotations

import re

import numpy as np

H2 = re.compile(r"^##\s+\S.*", re.M)


# ---------------------------------------------------------------- 切块

def chunk_flat(text: str, size: int = 200) -> list[str]:
    """固定字数切：按 `\\n\\n` 段落累积，**不切断段落**（切一半的句子向量是噪音）。"""
    paras = [p for p in text.split("\n\n") if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        if cur and len(cur) + len(p) + 2 > size:
            chunks.append(cur)
            cur = p
        else:
            cur = (cur + "\n\n" + p) if cur else p
    if cur:
        chunks.append(cur)
    # ⚠️ 单个段落自身超长时必须**连续切**，不能 `c[:size]` 截断。
    # 截断的失效形态是**静默的**：这篇文档的向量只基于前 size 字，后半部分
    # 从未被索引 —— 而产物看上去完全正常（块数 = 语料数，像是「每篇一块」的
    # 合法结果）。2026-10-26 实测：T2Retrieval 的 corpus p50 是 469 字且正文
    # 不带空行分段，于是 118,605 篇**全部**被截到前 200 字，`Hit@5 91.1%`
    # 那是「只看开头 43%」的读数。判据：`块数 == 语料数` 且文档长度 p90 > size
    # ⇒ 切块根本没发生，必须查这里。
    out: list[str] = []
    for c in chunks:
        if len(c) <= size:
            out.append(c)
        else:
            out.extend(c[i:i + size] for i in range(0, len(c), size))
    return out


def chunk_h2(text: str, size: int = 400) -> list[str]:
    """H2 语义切 + 每块前置所属标题。

    ⚠️ **2026-10-26 修过两个缺陷，此前所有 H2 读数都作废**：
    ① `H2` 正则曾是 `^##\\s+\\S` —— `\\S` 只匹配**一个**字符，`m.group()` 拿到的是
       `"## 标"`，所谓「前置标题」几乎不携带语义；
    ② `body` 是从标题位置切的、本身**包含**标题行，只修 ① 会让标题在块里印**两遍**
       （前置一次 + 正文里又一次）。故切完要剥掉首行。

    修好后语义是干净的：块 = `## 标题` + 该节正文，与原文段落逐字一致。
    """
    marks = [(m.start(), m.group().strip()) for m in H2.finditer(text)]
    if not marks:
        return chunk_flat(text, size)
    segs = []
    if marks[0][0] > 0:
        segs.append(("", text[:marks[0][0]]))
    for i, (pos, title) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        body = text[pos:end]
        # 标题行不算正文，否则 `head + body` 会把同一行标题印两遍
        if "\n" in body:
            body = body.split("\n", 1)[1].lstrip("\n")
        else:
            body = ""            # 整节只有标题行
        segs.append((title, body))
    chunks = []
    for title, body in segs:
        parts = chunk_flat(body, size)
        if not parts:
            # 整节只有标题（或全空白）：标题本身就是一个块，别把它丢了
            if title:
                chunks.append(title)
            continue
        head = f"{title}\n\n" if title else ""
        chunks.extend(head + c for c in parts)
    return chunks


CHUNKERS = {"flat": chunk_flat, "h2": chunk_h2}


# ---------------------------------------------------------------- 向量排名与融合

def vector_rank_scored(mat: np.ndarray, owner: np.ndarray, query_vecs: np.ndarray,
                       depth: int = 50) -> list[list[tuple[str, float]]]:
    """同 `vector_rank`，但每项带余弦分数：`[[(id, score), ...], ...]`。

    **分数融合（score fusion）需要它** —— RRF 只用名次、天然跨量纲可比，
    而「把两路分数加权相加」必须先拿到原始分数再归一化。
    ⚠️ `vector_rank` 已改为委托本函数取 id，**排名与分数只此一处算**。
    """
    uniq, inv = np.unique(owner, return_inverse=True)   # uniq 已排序 ⇒ 结果确定
    if not len(uniq):
        return [[] for _ in query_vecs]
    k = min(depth, len(uniq))
    out = []
    for qv in query_vecs:
        best = np.full(len(uniq), -np.inf, dtype=np.float32)
        np.maximum.at(best, inv, mat @ qv)
        top = np.argpartition(-best, k - 1)[:k]          # 前 k，未排序
        order = top[np.argsort(-best[top])]
        out.append([(str(uniq[i]), float(best[i])) for i in order])
    return out


def vector_rank(mat: np.ndarray, owner: np.ndarray, query_vecs: np.ndarray,
                depth: int = 50) -> list[list[str]]:
    """文档得分 = 该文档所有块的余弦**最大值**，按分降序取前 `depth` 个文档 id。

    ⚠️ **分组最大值必须走整型索引，不能对每个 id 做一次 `owner == tid` 布尔掩码**：
    那样是 `O(文档数 × 块数)`，LiMo 的 137 篇（≈6500 块）看不出问题，
    而 10 万条 corpus 的公开数据集上它要跑几小时 —— 同一个函数，规模差 1000 倍就从
    「稍慢」变成「跑不完」。这里 `np.unique(return_inverse=True)` 一次建好
    块→文档的整型索引，逐题 `np.maximum.at` 做分段最大值（O(块数)）。

    实现委托 `vector_rank_scored`（丢分数），**逐字节等价于改造前**：
    `uniq` 已排序、`np.argpartition` 的稳定取法未改，id 顺序完全一致。
    """
    return [[tid for tid, _ in lst]
            for lst in vector_rank_scored(mat, owner, query_vecs, depth)]


def rrf(lists: list[list[str]], k: int = 10,
        weights: list[float] | None = None) -> list[str]:
    """Reciprocal Rank Fusion：`score = Σ w_i /(k + rank)`。

    **只用排名不用分数** —— BM25 与余弦量纲不可比，归一化会引入新超参。
    ⚠️ 只重排「两路都召回了的东西」：一路召不回的正确文档它救不了（那是 rerank 的地盘）。

    `weights` 缺省全 `1.0`，**与加参数之前逐字节同结果**（等权是原行为）。
    加权只在「两路精度差得远」时才有意义：等权隐含「两路排名同等可信」，
    而 Ecom 上字面 Hit@5 只有 49.1%、向量 64.2%（实测，见 `_experiments/03-public-datasets/`），
    给低精度信号一半话语权的直接后果是 nDCG/MRR 都低于纯向量。
    """
    ws = weights if weights is not None else [1.0] * len(lists)
    if len(ws) != len(lists):
        raise ValueError(f"weights 长度 {len(ws)} 与 lists 长度 {len(lists)} 不一致")
    agg: dict[str, float] = {}
    for w, lst in zip(ws, lists):
        for r, tid in enumerate(lst, 1):
            agg[tid] = agg.get(tid, 0.0) + w / (k + r)
    return sorted(agg, key=agg.get, reverse=True)
