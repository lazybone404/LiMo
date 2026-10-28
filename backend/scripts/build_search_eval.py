"""构建「检索质量门禁」的三份 fixture：语料快照 / 查询集 / 基线。

**为什么要有它**：改检索逻辑（分词、连接方式、排序、索引文本）时，凭感觉判断不了
好坏。2026-10-26 实测过一次「单测全绿 + 实验脚本 83% + 产品 41%」三者同时成立 ——
所以门禁必须跑在**同一批语料 + 同一套判据**上，而不是靠人记住上次是多少。

用法：
    python scripts/build_search_eval.py --corpus           # 重建语料快照（改语料才跑）
    python scripts/build_search_eval.py                   # 只用现有语料打分，不写任何文件
    python scripts/build_search_eval.py --update-baseline  # 打分并写基线（**更新须在 CHANGELOG 记理由**）

⚠️ **三条边界，别读成「检索好不好」的结论**：

1. **它测的是字面层**（`SearchEngine.search`），不含向量与 RRF 融合，也不含对话侧。
   `VectorSearcher.fuse` 的编排（depth / RRF k / 语义独有回补）**一个字都不会反映到
   分数上** —— 那些由各自的单测守。
2. **绝对分数不可迁移**。B 这里的「一篇文档」是 md 的一个 H2 小节（几百到三千字），
   而真实库里一篇 transcript 是整段视频转录（上万字）。BM25 的长度归一化让检索单元
   变小 ⇒ 命中显著更容易。**这个分数只在同一份语料的前后对比里有意义**，不要拿去
   跟 `eval_search.py` 的 29 题真实库分数比。
3. **用快照而非现切**。现切的失效形态是「改一句文档 → 语料变 → 基线漂移 → 门禁因
   无关改动变红」。代价是快照会随时间脱节 —— 那由 `--corpus` 显式重建承担，重建后
   **必须**同时 `--update-baseline`（基线里存了语料指纹，对不上会直接报错，见下）。

⚠️ **切片 id 必须确定性生成**：`sha256("{源文件相对路径}#{H2 标题}")[:12]`。用序号或
时间戳的话，重建一次快照就让全部基线 id 失效、门禁集体误报红。`--corpus` 重建时会
自证「重建前后 id 集合相同」（除非你确实动了源文档，那时它会把差异打出来）。
"""

import argparse
import hashlib
import json
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
_ROOT = _BACKEND.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core.search import SearchEngine                 # noqa: E402
from app.services.markdown_sections import split_by_h2   # noqa: E402
from eval_search import _score                           # noqa: E402

FIXTURES = _BACKEND / "tests" / "fixtures"
CORPUS_PATH = FIXTURES / "search_eval_corpus.json"
QUERIES_PATH = FIXTURES / "search_eval_queries.json"
BASELINE_PATH = FIXTURES / "search_eval_baseline.json"

#: 语料来源（仓库根相对路径）。选材判据是**能不能被问成一道题**，不是「改得慢」：
#: 要有成段的自然语言、且覆盖多种语义类型（架构 / 流程 / 配置 / 场景 / UI / 导出 / 检索）。
#: ⚠️ 排除 CHANGELOG（天天变）、STATUS（纯计数）、code-audit*（时点快照）、
#:    patterns.md（0 个 `##`，切不出来）。
SOURCES = [
    "docs/user-scenarios.md",
    "design/components.md",
    "design/cards.md",
    "docs/plugins.md",
    "docs/architecture.md",
    "docs/frontend-patterns.md",
    "docs/export-pipeline.md",
    "docs/prompts-inventory.md",
    "docs/search-retrieval.md",
    "docs/AGENT-ONBOARDING.md",
]

#: 切片长度窗口（**剥掉围栏代码块之后**计）。下界：太短的小节（一句话交代）问不出
#: 有区分度的题，索引里也几乎没有可匹配的正文；上界：超过它的多是整章大表/清单，
#: 一个检索单元塞进三千字以上，长度归一化的分母就失去意义。
MIN_CHARS, MAX_CHARS = 100, 3000

_FENCE_RE = re.compile(r"^```[\s\S]*?^```[ \t]*$", re.MULTILINE)


def _strip_fences(text: str) -> str:
    """剥掉围栏代码块 —— 只用于**量长度**，写进 fixture 的仍是原文。

    `SearchEngine` 建索引时 `_clean_for_search` 也会剥（同一件事的另一处实现，
    但那边是产品侧判据，这里是「这个切片值不值得当一道题」的度量）。
    """
    return _FENCE_RE.sub(" ", text)


def slice_id(rel_path: str, title: str) -> str:
    """切片的稳定 id —— 规则改一个字，全部基线都会失效，故单独一个函数。"""
    return hashlib.sha256(f"{rel_path}#{title}".encode("utf-8")).hexdigest()[:12]


def build_corpus() -> dict:
    slices, sources = [], []
    for rel in SOURCES:
        md = (_ROOT / rel).read_text(encoding="utf-8")
        _preamble, sections = split_by_h2(md)
        kept = 0
        for heading, body, _card in sections:
            title = heading.lstrip("#").strip()
            text = f"{heading}\n{body}".strip()
            n = len(_strip_fences(text).strip())
            if n < MIN_CHARS or n > MAX_CHARS:
                continue
            slices.append({"id": slice_id(rel, title), "title": title,
                           "source": rel, "body": text})
            kept += 1
        sources.append({"path": rel, "sections": len(sections), "slices": kept})

    ids = [s["id"] for s in slices]
    assert len(ids) == len(set(ids)), "切片 id 撞车（同一文件里有两个同名 H2？）"
    return {"generatedAt": datetime.now().isoformat(timespec="seconds"),
            "idRule": "sha256('{源文件相对路径}#{H2 标题}')[:12]",
            "minChars": MIN_CHARS, "maxChars": MAX_CHARS,
            "sources": sources, "slices": slices}


def corpus_hash(corpus: dict) -> str:
    """语料指纹。基线里存一份 —— 改了语料却没更新基线时，门禁要**当场报错**，
    否则它拿着旧基线给新语料打分，红绿都失去意义。"""
    h = hashlib.sha256()
    for s in corpus["slices"]:
        h.update(f"{s['id']}\0{s['body']}\0".encode("utf-8"))
    return h.hexdigest()[:16]


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def score(corpus: dict, queries: list[dict], topk: int) -> dict:
    """建临时索引 → 跑题 → 打分（打分本身走 `eval_search._score`，只有数据是自己的）。"""
    tmp = tempfile.mkdtemp(prefix="limo_searcheval_")
    try:
        engine = SearchEngine(tmp)
        bodies = {s["id"]: s["body"] for s in corpus["slices"]}
        engine.rebuild([{"id": s["id"], "title": s["title"]} for s in corpus["slices"]],
                       lambda tid: bodies[tid])
        items = [(q["kind"], q["query"], q["want"]) for q in queries]
        r = _score(lambda q: [h["id"] for h in engine.search(q, limit=50)], topk, items)
        return {"hit": r["hit"], "total": r["total"],
                "per_kind": r["per_kind"], "misses": r["misses"],
                "ranks": dict(zip([q["query"] for q in queries], r["ranks"]))}
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def _fmt(r: dict) -> str:
    detail = "  ".join(f"{k} {v[0]}/{v[1]}" for k, v in r["per_kind"].items())
    return f"{detail}  →  {r['hit']}/{r['total']} ({r['hit'] / r['total']:.0%})"


def _report(r: dict, queries: list[dict], baseline: dict | None) -> None:
    """打印分数 + **每题的排名与相对基线的位移** —— 门禁失败时要能直接看出「哪题掉了」。

    ⚠️ **没命中一律打出来**，不只是「相对基线掉了的」：首次生成基线时没有对照表，
    若也静默跳过，人看着 77% 这个数字根本不知道差在哪三题、更没法判断该改题还是改检索。
    """
    print(f"题 {r['total']} 道 · 判据前 {baseline['topk'] if baseline else 5}\n")
    print(f"合计 {_fmt(r)}\n")
    if r["misses"]:
        print(f"没进前 {baseline['topk'] if baseline else 5} 的题：")
        for m in r["misses"]:
            where = f"第 {m['rank']} 名" if m["rank"] else "未召回"
            print(f"  · {m['query']}（{where}）")
        print()
    if baseline is None:
        return
    print("题目                                        基线 → 现在")
    for q in queries:
        qq = q["query"]
        was, now = baseline["ranks"].get(qq, 0), r["ranks"].get(qq, 0)
        if was == now:
            continue
        arrow = "↓" if now == 0 or (was and now > was) else "↑"
        was_s = f"第{was}" if was else "未召回"
        now_s = f"第{now}" if now else "未召回"
        print(f"  {arrow} {qq[:34]:<34} {was_s} → {now_s}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", action="store_true", help="重建语料快照（改 SOURCES 或源文档后跑）")
    ap.add_argument("--update-baseline", action="store_true",
                    help="跑分并写基线（⚠️ 更新必须在 docs/CHANGELOG.md 记一句理由）")
    ap.add_argument("--topk", type=int, default=5, help="命中判据：目标进前 N 名（基线里记着，改它要重写基线）")
    args = ap.parse_args()

    if args.corpus:
        FIXTURES.mkdir(parents=True, exist_ok=True)
        old = _load_json(CORPUS_PATH)["slices"] if CORPUS_PATH.exists() else []
        corpus = build_corpus()
        new_ids = {s["id"] for s in corpus["slices"]}
        old_ids = {s["id"] for s in old}
        CORPUS_PATH.write_text(json.dumps(corpus, ensure_ascii=False, indent=1) + "\n",
                               encoding="utf-8")
        print(f"语料已写出：{len(corpus['slices'])} 条切片 → {CORPUS_PATH.relative_to(_ROOT)}")
        for s in corpus["sources"]:
            print(f"  {s['slices']:>3} / {s['sections']:>3} 节  {s['path']}")
        if old:
            # ⚠️ 自证 id 稳定：除非确实动了源文档，否则 id 集合必须一模一样 ——
            #    变了说明 id 规则被改了（那是「重建一次全部基线失效」的入口）。
            if new_ids == old_ids:
                print("✓ id 集合与上一版完全相同（规则未漂移，旧基线仍然对得上）")
            else:
                print(f"⚠️ id 集合变了：+{len(new_ids - old_ids)} / -{len(old_ids - new_ids)}"
                      f"（源文档改动所致 —— 必须重跑 --update-baseline）")
        print("\n下一步：python scripts/build_search_eval.py --update-baseline")
        return 0

    if not CORPUS_PATH.exists() or not QUERIES_PATH.exists():
        print(f"缺 fixture：{CORPUS_PATH} / {QUERIES_PATH}\n先跑 --corpus 并补齐查询集。",
              file=sys.stderr)
        return 2

    corpus = _load_json(CORPUS_PATH)
    queries = _load_json(QUERIES_PATH)["queries"]
    ids = {s["id"] for s in corpus["slices"]}
    bad = [q for q in queries if q["want"] not in ids]
    if bad:
        print(f"查询集里有 {len(bad)} 条指向不存在的切片：{bad[:3]}", file=sys.stderr)
        return 2

    baseline = _load_json(BASELINE_PATH) if BASELINE_PATH.exists() else None
    if baseline and baseline.get("corpusHash") != corpus_hash(corpus):
        print("⚠️ 语料指纹与基线不符（改了语料却没更新基线）—— 现在的分数没有可比对象。",
              file=sys.stderr)

    r = score(corpus, queries, args.topk)
    _report(r, queries, baseline)

    if args.update_baseline:
        if baseline and baseline.get("topk") != args.topk:
            print(f"⚠️ topk 从 {baseline.get('topk')} 改成 {args.topk} —— 基线不可比，"
                  f"这次更新等于换了把尺子。", file=sys.stderr)
        out = {"generatedAt": datetime.now().isoformat(timespec="seconds"),
               "topk": args.topk,
               "corpusHash": corpus_hash(corpus),
               "slices": len(corpus["slices"]),
               "overall": {"hit": r["hit"], "total": r["total"]},
               "perKind": {k: v for k, v in r["per_kind"].items()},
               "ranks": r["ranks"]}
        BASELINE_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n",
                                 encoding="utf-8")
        print(f"\n基线已写出 → {BASELINE_PATH.relative_to(_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
