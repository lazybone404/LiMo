"""本地 ONNX embedding —— 评测专用，**刻意不进产品依赖**。

`eval_search.py --vector` 与 `_experiments/02-embedding-rrf/eval_ab.py` 共用这一份
（两份实现是「同源漂移」的经典入口：改一处、另一处静默停在旧行为上）。

⚠️ **`onnxruntime` / `tokenizers` 不在 `backend/pyproject.toml` 里**，它们是评测时
手工装进 venv 的。按「口子原则」，本地 embedding 走插件口子（阶段 4），不该进核心依赖 ——
所以本模块在**导入时不做任何重依赖 import**，构造 `Embedder` 时才惰性导入、缺失时显式报错。

模型：`~/.limo/models/bge-small-zh-v1.5-onnx/`（`model_int8.onnx` 22.8MB + `tokenizer.json`）。
512 维、CLS pooling、L2 归一化、`max_length=512` token ≈ 400–500 中文字。

判据来自 2026-10-26 那两轮实测（见 `_experiments/02-embedding-rrf/` 与
`docs/search-retrieval.md`）：
- **固定字数切 200（`chunk_flat`）是默认**，H2 语义切在各档都没跑赢（90% vs 93%）
- 文档得分 = 各块余弦**最大值**，不是平均（平均会稀释「只有一段相关」的长文档）
- RRF `k=10` 在 29 题上优于 `k=60` —— 这是**经验值不是调优结果**，落库时保留可配
"""

from __future__ import annotations

import re
import time

import numpy as np

H2 = re.compile(r"^##\s+\S", re.M)

DEFAULT_MODEL_DIR = "~/.limo/models/bge-small-zh-v1.5-onnx"
DEFAULT_ONNX = "model_int8.onnx"

#: `bge-small` 的 max_length 是 512 token ≈ 400–500 中文字。**换模型必须重算**：
#: 照抄云端 v4 的 2000 字符会让后半段被 tokenizer 静默截断（实测 1000 字块只有 76%，
#: 比不做向量的 79% 还低 —— 不报错、块看起来完整，只是那半段永远搜不到）。
MAX_TOKENS = 512


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

    ⚠️ **实测没跑赢固定切**（RRF 90% vs 93%，各档皆然），默认不用它。
    「为什么更差」目前只有推测（H2 切出的块长度极不均匀，而余弦在长度相近的文本间
    比较才稳定）——**消融实验还没做**，故保留这个函数以便将来复测。
    """
    marks = [(m.start(), m.group().strip()) for m in H2.finditer(text)]
    if not marks:
        return chunk_flat(text, size)
    segs = []
    if marks[0][0] > 0:
        segs.append(("", text[:marks[0][0]]))
    for i, (pos, title) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        segs.append((title, text[pos:end]))
    chunks = []
    for title, body in segs:
        head = f"{title}\n\n" if title else ""
        chunks.extend(head + c for c in chunk_flat(body, size))
    return chunks


CHUNKERS = {"flat": chunk_flat, "h2": chunk_h2}


# ---------------------------------------------------------------- 向量排名与融合

def build_vectors(embed, items, chunker=chunk_flat) -> tuple[np.ndarray, np.ndarray, int, float]:
    """`items` = [(id, text)] → (矩阵, 每块归属的 id 数组, 块数, 编码秒数)。"""
    chunks, owner = [], []
    for tid, text in items:
        for c in chunker(text):
            chunks.append(c)
            owner.append(tid)
    t0 = time.perf_counter()
    mat = embed(chunks)
    return mat, np.array(owner), len(chunks), time.perf_counter() - t0


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
