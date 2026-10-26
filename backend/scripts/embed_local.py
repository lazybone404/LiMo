"""本地 ONNX embedding —— **评测侧的转出口**，模型句柄已经搬进产品代码。

2026-10-26 起，`Embedder` / `MissingDependency` / 模型常量住在
[`app/engines/embedding_local.py`](../app/engines/embedding_local.py) —— 那里是
**产品引擎 `LocalEmbeddingEngine` 的家**。本模块保留为薄转出，只为两件事：

1. `eval_search.py --vector` 与 `_experiments/02-embedding-rrf/eval_ab.py` 的
   `from embed_local import Embedder` 写法不变；
2. `build_vectors` —— 「整个语料一次编码」这个**评测专属形状**（产品侧是 worker
   逐批增量，不走这里）。

⚠️ **绝不在这里再实现一份 embedder 或 chunker**。01 轮 40pp 的落差正是「实验脚本
自带一份实现」造成的：本模块的 embedder 与产品引擎的 embedder 必须是**同一个类**，
否则「评测 83%」描述的是另一套权重、另一套归一化，而两边都报成功。

⚠️ **切块 / 分组排名 / RRF 三个算子也不在本模块** —— 它们住在
[`app/core/vector_index.py`](../app/core/vector_index.py)（产品侧后台 worker 与查询融合
共用，核心只依赖 numpy）。这里转出它们只为老调用方的 `from embed_local import rrf` 不破。

模型：`~/.limo/models/bge-small-zh-v1.5-onnx/`（`model_int8.onnx` 22.8MB + `tokenizer.json`）。
512 维、CLS pooling、L2 归一化、`max_length=512` token ≈ 400–500 中文字。

判据来自 2026-10-26 那两轮实测（见 `_experiments/02-embedding-rrf/` 与
`docs/search-retrieval.md`）：
- **固定字数切 200（`chunk_flat`）是默认**，H2 语义切没跑赢（纯向量 79%/76% vs 83%；
  RRF 层面打平，但那是字面路补上的差额）。与 H2 标题正则缺陷无关，见 `vector_index.chunk_h2`
- 文档得分 = 各块余弦**最大值**，不是平均（平均会稀释「只有一段相关」的长文档）
- RRF `k=10` 在 29 题上优于 `k=60` —— 这是**经验值不是调优结果**，落库时保留可配

⚠️ **`onnxruntime` / `tokenizers` 仍不在 `backend/pyproject.toml` 里**：本模块与
`app/engines/embedding_local.py` 在**导入时都不做任何重依赖 import**，构造 `Embedder`
时才惰性导入、缺失时显式抛 `MissingDependency`（带安装命令）。口子原则的判据是
「重依赖不进核心打包」，不是「代码放在哪个目录」。
"""

from __future__ import annotations

import time

import numpy as np

# 转出核心算子：老调用方写的是 `from embed_local import CHUNKERS, rrf, vector_rank ...`。
# ⚠️ 别在本模块再实现一份 —— 评测与产品必须跑同一套切块（见 vector_index 模块文档）。
from app.core.vector_index import (CHUNKERS, chunk_flat, chunk_h2,  # noqa: F401
                                   rrf, vector_rank, vector_rank_scored)
# 转出模型句柄：产品引擎与评测脚本共用同一个类（见模块文档）。
from app.engines.embedding_local import (DEFAULT_MODEL_DIR, DEFAULT_ONNX,  # noqa: F401
                                         MAX_TOKENS, Embedder, MissingDependency)

__all__ = ["CHUNKERS", "DEFAULT_MODEL_DIR", "DEFAULT_ONNX", "Embedder", "MAX_TOKENS",
           "MissingDependency", "build_vectors", "chunk_flat", "chunk_h2", "rrf",
           "vector_rank", "vector_rank_scored"]


# ---------------------------------------------------------------- 评测形状的组装

def build_vectors(embed, items, chunker=chunk_flat) -> tuple[np.ndarray, np.ndarray, int, float]:
    """`items` = [(id, text)] → (矩阵, 每块归属的 id 数组, 块数, 编码秒数)。

    ⚠️ **这个「一次编码整个语料」的形状是评测专属**：产品侧是 worker 逐批增量，
    用 `chunk_flat` + `EmbeddingEngine.embed` 自己组装，不走这里。
    """
    chunks, owner = [], []
    for tid, text in items:
        for c in chunker(text):
            chunks.append(c)
            owner.append(tid)
    t0 = time.perf_counter()
    mat = embed(chunks)
    return mat, np.array(owner), len(chunks), time.perf_counter() - t0
