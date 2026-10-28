"""检索质量测评 —— 29 题人工标注，按「语义 / 精确 / 混合」分类计分。

**为什么需要这个脚本**：改检索时，凭感觉判断不了好坏。2026-10-26 实测过一次
「单测全绿 + 实验脚本 83% + 产品 41%」，三者同时成立 —— 只有**在产品判据上
跑真实库**才能暴露它。任何检索改动都应该先跑这里。

用法：
    python scripts/eval_search.py                    # 默认：含笔记，前 5 判据
    python scripts/eval_search.py --mode raw         # 只索引 rawText（对照）
    python scripts/eval_search.py --topk 10          # 放宽判据
    python scripts/eval_search.py --json             # 机器可读

    python scripts/eval_search.py --vector           # 三路对照：字面 / 纯向量 / RRF
    python scripts/eval_search.py --vector --chunk h2 --rrf-k 60
    python scripts/eval_search.py --vector --json

⚠️ **只读**：直接读 `index.json` 与文档文件，**不构造 `Store`**（它的 `__init__`
会触发 legacy 迁移与索引重建 —— 测评脚本绝不能改用户的库）。索引建在临时目录。
检索判据走产品函数 `doc_text.search_index_text` 与 `core.search.SearchEngine`，
不在脚本里另拼一份 —— 那正是 2026-10-26 那 40pp 落差的来源。

⚠️ **`--vector` 需要额外依赖**：`onnxruntime` + `tokenizers`（**刻意不在
`pyproject.toml` 里** —— 本地 embedding 走插件口子，不该进核心依赖）。缺失时显式报错
并给出安装命令，不静默退化。模型默认 `~/.limo/models/bge-small-zh-v1.5-onnx/`。
不传 `--vector` 时**一行都不会碰**它们，输出与改造前逐字节相同。
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core.search import SearchEngine          # noqa: E402
from app.generation.doc_text import search_index_text  # noqa: E402

#: 人工标注：131 篇真实库里挑 29 篇，每篇一道「期望命中它」的查询。
#: ⚠️ 三类分开计分不是洁癖 —— 只看总分会被「语义全崩、关键词全对」蒙混过去，
#: 而那正是真实使用中最难受的形态（用户多数时候凭印象问，不是凭关键词）。
EVAL = [
    ("语义", "单台机器最多能塞下多少个容器实例", "tr_682bfe3d2a70"),
    ("精确", "1048", "tr_682bfe3d2a70"),
    ("语义", "让大模型一边猜一边验证来加速出字", "tr_20032c937f5a"),
    ("精确", "投机解码", "tr_20032c937f5a"),
    ("语义", "做 AI 编程工具那哥们为啥挑了这门语言", "tr_f0bffa04eba0"),
    ("混合", "Codex Rust 开源", "tr_f0bffa04eba0"),
    ("语义", "为什么跑编程助手账单那么贵", "tr_a61467858a09"),
    ("精确", "Engram", "tr_a61467858a09"),
    ("语义", "有没有工具不靠查资料库也能答好", "tr_53da38141f96"),
    ("混合", "长上下文 检索增强 取代", "tr_53da38141f96"),
    ("语义", "怎么跟模型说话它才听得懂", "tr_75e1fe9bdd8f"),
    ("精确", "Matt Pocock", "tr_75e1fe9bdd8f"),
    ("语义", "游戏里人物移动和动作怎么接起来", "tr_6e28b4feb76c"),
    ("精确", "黑板模式", "tr_6e28b4feb76c"),
    ("语义", "游戏怎么提前发现玩家不想玩了", "tr_50186fecee7d"),
    ("混合", "礼包 推送 卸载", "tr_50186fecee7d"),
    ("语义", "为什么大家现在都用病名骂人", "tr_4c1a82fc3d51"),
    ("精确", "循环解释", "tr_4c1a82fc3d51"),
    ("语义", "喝手冲的为什么看不上喝奶茶的", "tr_0c5cd4f9f5f1"),
    ("精确", "布尔迪厄", "tr_0c5cd4f9f5f1"),
    ("语义", "花钱找有经验的人问进大厂的事", "tr_d05a1d874ab6"),
    ("混合", "HRBP 猎头 大厂", "tr_d05a1d874ab6"),
    ("语义", "投简历一直没人回心里难受怎么熬", "tr_0eef5322f500"),
    ("精确", "已读不回", "tr_0eef5322f500"),
    ("语义", "画师担心被 AI 取代该怎么稳住", "tr_8c1fb01cf151"),
    ("语义", "新手画人应该先练哪些再练哪些", "tr_0ec27707fae8"),
    ("精确", "头身比例", "tr_0ec27707fae8"),
    ("语义", "现在靠拍照挣钱还干得下去吗", "tr_9fb259b3900a"),
    ("混合", "影楼 注销 婚庆", "tr_9fb259b3900a"),
]


class _DiskStore:
    """只读的 duck-typed store —— 够 `search_index_text` 用，且一个字节都不写。

    ⚠️ 不用真实 `Store`：它的 `__init__` 会迁移 legacy 布局、必要时重建索引，
    测评脚本碰用户的库是绝对不该发生的事。
    """

    def __init__(self, root: Path, meta: dict, cache: dict):
        self._root = root
        self._meta = meta      # tid -> transcript 元数据
        self._cache = cache    # tid -> (raw, notes)

    def get_transcript(self, tid):
        tx = dict(self._meta.get(tid) or {})
        tx["rawText"] = self._cache.get(tid, ("", ""))[0]
        return tx or None

    def get_markdowns_by_transcript(self, tid, mode=""):
        notes = self._cache.get(tid, ("", ""))[1]
        return [{"content": notes, "version": 1}] if notes else []


def _load(root: Path) -> tuple[dict, _DiskStore]:
    """读 index.json + 各文档的 rawText / 最新版笔记。**只读。**"""
    idx = json.loads((root / "index.json").read_text(encoding="utf-8"))
    meta, cache = {}, {}
    for t in idx["transcripts"]:
        tid, dd = t["id"], root / t["dir_name"]
        meta[tid] = t
        raw_file = dd / "transcript.md"
        raw = raw_file.read_text(encoding="utf-8") if raw_file.exists() else ""
        notes = ""
        ents = [e for e in (idx["markdowns"].get(tid) or []) if e.get("mode") == "notes"]
        if ents:
            nf = dd / "notes" / f"v{max(e['version'] for e in ents)}.md"
            if nf.exists():
                notes = nf.read_text(encoding="utf-8")
        cache[tid] = (raw, notes)
    return idx, _DiskStore(root, meta, cache)


def _text_getter(store, mode: str):
    """索引文本的取值函数 —— 三条路（字面 / 向量 / 融合）**必须用同一个**。

    两路取不同的文本就等于在比「不同的库」，而结果表上看不出异常。
    """
    if mode == "notes":
        return lambda tid: search_index_text(store, tid)
    return lambda tid: store.get_transcript(tid)["rawText"]


def _score(get_ids, topk: int, items=None) -> dict:
    """按「目标进前 topk」打分，分三类统计。`get_ids(query) -> [id...]`（按相关度降序）。

    `items` 缺省为 `EVAL`（真实库那 29 题）—— **门禁侧（`tests/test_search_eval.py`）
    把自己的题集传进来**，走的是同一个打分函数：判据只能有一份，各写一遍的失效形态
    是「门禁说没退、真实库说退了」，而两边都觉得自己对。
    """
    per_kind, misses, ranks = {}, [], []
    for kind, query, want in (items if items is not None else EVAL):
        ids = get_ids(query)
        rank = ids.index(want) + 1 if want in ids else 0
        ranks.append(rank)
        ok = 0 < rank <= topk
        per_kind.setdefault(kind, [0, 0])
        per_kind[kind][0] += ok
        per_kind[kind][1] += 1
        if not ok:
            misses.append({"query": query, "rank": rank})

    return {"per_kind": per_kind,
            "hit": sum(v[0] for v in per_kind.values()),
            "total": sum(v[1] for v in per_kind.values()),
            "misses": misses, "ranks": ranks}


def run(root: Path, mode: str, topk: int) -> dict:
    idx, store = _load(root)
    get_text = _text_getter(store, mode)
    tmp = tempfile.mkdtemp(prefix="limo_eval_")
    try:
        engine = SearchEngine(tmp)
        rows = [{"id": t["id"], "title": t.get("title", "")} for t in idx["transcripts"]]
        engine.rebuild(rows, get_text)

        r = _score(lambda q: [h["id"] for h in engine.search(q, limit=50)], topk)
        r.pop("ranks")          # 保持 `--json` 的字段集与改造前一致
        r["docs"] = len(idx["transcripts"])
        return r
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_vector(root: Path, mode: str, topk: int, model_dir: str, onnx: str,
               chunk: str, chunk_size: int, rrf_k: int, depth: int) -> dict:
    """三路对照：字面 FTS5 / 纯向量 / RRF 融合。

    ⚠️ **三路必须用同一份索引文本**（`_text_getter`）、同一批判据（`EVAL`）、
    同一个召回深度（`depth`）—— 否则表上的差值里混着变量的功劳。
    """
    from functools import partial

    from embed_local import CHUNKERS, Embedder, build_vectors, rrf, vector_rank

    idx, store = _load(root)
    get_text = _text_getter(store, mode)
    rows = [{"id": t["id"], "title": t.get("title", "")} for t in idx["transcripts"]]
    tids = [r["id"] for r in rows]

    # ① 字面路：临时索引，与 run() 完全同一条构建流程
    tmp = tempfile.mkdtemp(prefix="limo_eval_")
    try:
        engine = SearchEngine(tmp)
        engine.rebuild(rows, get_text)
        lexical = {q: [h["id"] for h in engine.search(q, limit=depth)] for _, q, _ in EVAL}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ② 向量路：全库分块 → 编码 → 逐题取文档最高分
    embed = Embedder(model_dir, onnx)
    mat, owner, nchunk, secs = build_vectors(
        embed, [(tid, get_text(tid)) for tid in tids],
        partial(CHUNKERS[chunk], size=chunk_size))
    qvec = embed([q for _, q, _ in EVAL])
    ranked = vector_rank(mat, owner, qvec, depth)
    vector = {q: ranked[i] for i, (_, q, _) in enumerate(EVAL)}

    # ③ 融合路：只用排名不用分数（BM25 与余弦量纲不可比）
    fused = {q: rrf([lexical[q], vector[q]], rrf_k) for _, q, _ in EVAL}

    out = {"docs": len(tids),
           "meta": {"model": onnx, "chunk": chunk, "chunk_size": chunk_size,
                    "chunks": nchunk, "encode_sec": round(secs, 1),
                    "rrf_k": rrf_k, "depth": depth}}
    for tag, get_ids in (("lexical", lambda q: lexical[q]),
                         ("vector", lambda q: vector[q]),
                         ("rrf", lambda q: fused[q])):
        r = _score(get_ids, topk)
        r.pop("ranks")
        out[tag] = r
    return out


def _fmt(r: dict) -> str:
    order = ("语义", "精确", "混合")
    detail = "  ".join(f"{k} {r['per_kind'][k][0]}/{r['per_kind'][k][1]}"
                       for k in order if k in r["per_kind"])
    return f"{detail}  →  {r['hit']}/{r['total']} ({r['hit'] / r['total']:.0%})"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(Path.home() / ".limo"), help="库根目录")
    ap.add_argument("--mode", choices=("notes", "raw"), default="notes",
                    help="索引文本：notes = rawText + 最新笔记（产品现状）/ raw = 只 rawText")
    ap.add_argument("--topk", type=int, default=5, help="命中判据：目标进前 N 名")
    ap.add_argument("--json", action="store_true", help="输出 JSON")

    g = ap.add_argument_group("向量（阶段 3 验收用；缺依赖时显式报错）")
    g.add_argument("--vector", action="store_true", help="加跑纯向量与 RRF 两路")
    g.add_argument("--model", default="~/.limo/models/bge-small-zh-v1.5-onnx",
                   help="本地 ONNX 模型目录")
    g.add_argument("--onnx", default="model_int8.onnx", help="模型文件名")
    g.add_argument("--chunk", choices=("flat", "h2"), default="flat",
                   help="切块策略：flat=固定字数（实测最优）/ h2=按二级标题")
    g.add_argument("--chunk-size", type=int, default=200,
                   help="块字数上限（⚠️ 受模型 max_length 约束，bge-small 约 400–500 中文字）")
    g.add_argument("--rrf-k", type=int, default=10, help="RRF 的 k（29 题上 10 优于 60）")
    g.add_argument("--depth", type=int, default=50, help="每路召回深度")
    args = ap.parse_args()

    root = Path(os.path.expanduser(args.root))
    if not (root / "index.json").exists():
        print(f"找不到库：{root / 'index.json'}", file=sys.stderr)
        return 2

    if args.vector:
        from embed_local import MissingDependency

        try:
            v = run_vector(root, args.mode, args.topk, args.model, args.onnx,
                           args.chunk, args.chunk_size, args.rrf_k, args.depth)
        except MissingDependency as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(v, ensure_ascii=False, indent=2))
            return 0
        meta = v["meta"]
        print(f"文档 {v['docs']} 篇 · 模式 {args.mode} · 判据前 {args.topk} · "
              f"{meta['chunks']} 块 / 编码 {meta['encode_sec']}s\n")
        print(f"字面 FTS5          {_fmt(v['lexical'])}")
        print(f"纯向量（{args.chunk} {args.chunk_size}）  {_fmt(v['vector'])}")
        print(f"RRF k={args.rrf_k:<12}  {_fmt(v['rrf'])}")
        for name, r in (("字面", v["lexical"]), ("向量", v["vector"]), ("融合", v["rrf"])):
            if r["misses"]:
                print(f"\n{name}失手：")
                for m in r["misses"]:
                    where = f"第 {m['rank']}" if m["rank"] else "未召回"
                    print(f"  {m['query']}（{where}）")
        return 0

    r = run(root, args.mode, args.topk)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0

    print(f"文档 {r['docs']} 篇 · 模式 {args.mode} · 判据前 {args.topk}\n")
    order = ("语义", "精确", "混合")
    detail = "  ".join(f"{k} {r['per_kind'][k][0]}/{r['per_kind'][k][1]}"
                       for k in order if k in r["per_kind"])
    print(f"{detail}  →  合计 {r['hit']}/{r['total']} ({r['hit'] / r['total']:.0%})")
    if r["misses"]:
        print("\n失手：")
        for m in r["misses"]:
            where = f"第 {m['rank']}" if m["rank"] else "未召回"
            print(f"  {m['query']}（{where}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
