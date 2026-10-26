"""本地 ONNX embedding —— 评测专用，**刻意不进产品依赖**。

`eval_search.py --vector` 与 `_experiments/02-embedding-rrf/eval_ab.py` 共用这一份
（两份实现是「同源漂移」的经典入口：改一处、另一处静默停在旧行为上）。

⚠️ **`onnxruntime` / `tokenizers` 不在 `backend/pyproject.toml` 里**，它们是评测时
手工装进 venv 的。按「口子原则」，本地 embedding 走插件口子（阶段 4），不该进核心依赖 ——
所以本模块在**导入时不做任何重依赖 import**，构造 `Embedder` 时才惰性导入、缺失时显式报错。

⚠️ **切块 / 分组排名 / RRF 三个算子不在本模块** —— 它们住在
[`app/core/vector_index.py`](../app/core/vector_index.py)（产品侧后台 worker 与查询融合也要用，
核心只依赖 numpy）。这里只保留两样**评测专属**的东西：ONNX 句柄，以及
「整个语料一次编码」这个评测形状的 `build_vectors`。

模型：`~/.limo/models/bge-small-zh-v1.5-onnx/`（`model_int8.onnx` 22.8MB + `tokenizer.json`）。
512 维、CLS pooling、L2 归一化、`max_length=512` token ≈ 400–500 中文字。

判据来自 2026-10-26 那两轮实测（见 `_experiments/02-embedding-rrf/` 与
`docs/search-retrieval.md`）：
- **固定字数切 200（`chunk_flat`）是默认**，H2 语义切没跑赢（纯向量 79%/76% vs 83%；
  RRF 层面打平，但那是字面路补上的差额）。与 H2 标题正则缺陷无关，见 `vector_index.chunk_h2`
- 文档得分 = 各块余弦**最大值**，不是平均（平均会稀释「只有一段相关」的长文档）
- RRF `k=10` 在 29 题上优于 `k=60` —— 这是**经验值不是调优结果**，落库时保留可配
"""

from __future__ import annotations

import time

import numpy as np

# 转出核心算子：老调用方写的是 `from embed_local import CHUNKERS, rrf, vector_rank ...`。
# ⚠️ 别在本模块再实现一份 —— 评测与产品必须跑同一套切块（见 vector_index 模块文档）。
from app.core.vector_index import (CHUNKERS, chunk_flat, chunk_h2,  # noqa: F401
                                   rrf, vector_rank, vector_rank_scored)

DEFAULT_MODEL_DIR = "~/.limo/models/bge-small-zh-v1.5-onnx"
DEFAULT_ONNX = "model_int8.onnx"

#: `bge-small` 的 max_length 是 512 token ≈ 400–500 中文字。**换模型必须重算**：
#: 照抄云端 v4 的 2000 字符会让后半段被 tokenizer 静默截断（实测 1000 字块只有 76%，
#: 比不做向量的 79% 还低 —— 不报错、块看起来完整，只是那半段永远搜不到）。
MAX_TOKENS = 512

__all__ = ["CHUNKERS", "Embedder", "MAX_TOKENS", "MissingDependency",
           "build_vectors", "chunk_flat", "chunk_h2", "rrf",
           "vector_rank", "vector_rank_scored"]


class MissingDependency(RuntimeError):
    """本地 embedding 的依赖没装。文案要能直接告诉用户跑什么命令。"""


class Embedder:
    """本地 ONNX 句柄。`__call__(texts) -> (n, dim) float32`，已 L2 归一化。"""

    def __init__(self, model_dir="~/.limo/models/bge-small-zh-v1.5-onnx",
                 onnx_name: str = DEFAULT_ONNX):
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:  # 失败显式化：不静默退化成纯字面
            raise MissingDependency(
                f"--vector 需要 onnxruntime 与 tokenizers：{exc}。"
                "安装：cd backend && uv pip install onnxruntime tokenizers "
                "--index-url https://pypi.tuna.tsinghua.edu.cn/simple"
            ) from exc

        from pathlib import Path

        d = Path(model_dir).expanduser()
        tok_file, model_file = d / "tokenizer.json", d / onnx_name
        for p in (tok_file, model_file):
            if not p.exists():
                raise MissingDependency(f"本地向量模型缺文件：{p}")

        self.tok = Tokenizer.from_file(str(tok_file))
        self.tok.enable_truncation(max_length=MAX_TOKENS)
        self.sess = ort.InferenceSession(str(model_file),
                                         providers=["CPUExecutionProvider"])

    def __call__(self, texts: list[str], bs: int = 32) -> np.ndarray:
        if not texts:
            return np.zeros((0, 1), dtype=np.float32)
        out = []
        for i in range(0, len(texts), bs):
            encs = self.tok.encode_batch(texts[i:i + bs])
            n = max(len(e.ids) for e in encs)   # 批内 pad 到最长（不同批之间无关）
            feed = {
                "input_ids": np.array([e.ids + [0] * (n - len(e.ids)) for e in encs],
                                      dtype=np.int64),
                "attention_mask": np.array(
                    [e.attention_mask + [0] * (n - len(e.attention_mask)) for e in encs],
                    dtype=np.int64),
                "token_type_ids": np.array(
                    [e.type_ids + [0] * (n - len(e.type_ids)) for e in encs],
                    dtype=np.int64),
            }
            cls = self.sess.run(None, feed)[0][:, 0]      # bge = CLS pooling
            out.append(cls / np.linalg.norm(cls, axis=1, keepdims=True))
        return np.vstack(out).astype(np.float32)


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
