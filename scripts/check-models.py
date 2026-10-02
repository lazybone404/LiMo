#!/usr/bin/env python3
"""核对模型预设 vs models.dev —— 找出「值得去官方页看一眼」的条目。

用法:
    python scripts/check-models.py                     # 联网拉取 models.dev
    python scripts/check-models.py --from-file X.json  # 用本地缓存（离线可用）
    python scripts/check-models.py --new-only          # A 类只列最近 6 个月的
    python scripts/check-models.py --provider dashscope
    python scripts/check-models.py --json              # 机器可读

设计定位（**先读这段再用结果**）:
    本脚本的产出是**问题，不是答案**。它只说「这条值得核对」，不说「预设错了」。
    models.dev 是社区聚合数据，实测到三类问题（2026-10-02 逐条比对 48 条预设）:
      · 口径混用   —— 1000000 vs 1048576（MB/MiB 未归一），8 条不一致里占 4 条
      · 乐观标注   —— anthropic 的 sonnet/opus 标 1M，而 Claude 的 1M 需 beta header，
                      默认仍是 200K；同家 haiku 却老实标 200K，自家数据内部就不一致
      · 覆盖率低   —— 48 条中仅 26 条有记录（54%），缺口正落在最新与最冷门的模型上
    ⇒ **一切以官方页为准**。本报告的唯一用途是决定「打开哪一页」。

    ⚠️ 能力位（尤其 capabilities 的 document）**不参与本核对**:
       models.dev 的 modalities 里 anthropic 全标着 pdf，但那说的是「模型能读 PDF」，
       我们走的是各家 OpenAI 兼容端点，认不认 file 块是另一回事（项目模式 #51）。
       该位仍只能逐家查官方文档后手标。
"""

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS_DEV_URL = "https://models.dev/api.json"

# LiMo provider id → models.dev 的 provider key。
# ⚠️ 刻意不猜：键名对不上时宁可显式写在这里，也不要写「模糊匹配」
#    （匹配错了会把另一家的窗口值安到我们头上，且看不出来）。
PROVIDER_MAP = {
    "deepseek": "deepseek",
    "dashscope": "alibaba",      # 阿里云百炼 = models.dev 的 alibaba
    "openai": "openai",
    "anthropic": "anthropic",
    "google": "google",
    "zhipu": "zai",              # 智谱 = models.dev 的 zai
    "moonshot": "moonshotai",    # 月之暗面 = models.dev 的 moonshotai（注意不是 moonshot）
}

# 聚合网关：models.dev 没有它们的上游目录，B 类核对对它们无意义。
# 它们列出的模型来自各上游，窗口值只能按上游判断。
NO_UPSTREAM = {"opencode"}


# ── 数据源 ──────────────────────────────────────────────────────────

def load_source(path: str | None) -> tuple[dict, str]:
    """返回 (数据, 来源描述)。path 为空则联网拉取。"""
    if path:
        p = Path(path)
        return json.loads(p.read_text(encoding="utf-8")), f"本地文件 {p.name}"

    import urllib.request
    req = urllib.request.Request(MODELS_DEV_URL, headers={"User-Agent": "LiMo-check-models"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8")), MODELS_DEV_URL


def our_presets() -> dict[str, list[dict]]:
    """从 DEFAULT_CONFIG 读我们手写的 llm_models（唯一真相源，不复制一份）。"""
    sys.path.insert(0, str(ROOT / "backend"))
    from app.core.config import DEFAULT_CONFIG
    return DEFAULT_CONFIG["llm_models"]


def retired_ids() -> set[tuple[str, str]]:
    """已点名的下线模型墓碑（config.py 的 RETIRED_MODEL_IDS）。

    ⚠️ 必须消费它，否则 A 类每次都会把「我们刻意移除的旧模型」当成新模型报出来
    （实测：deepseek-v4-flash 两条），噪声会淹没真正的新模型。
    """
    sys.path.insert(0, str(ROOT / "backend"))
    from app.core.config import RETIRED_MODEL_IDS
    return set(RETIRED_MODEL_IDS)


# ── 自身完整性守卫（防「加了 provider 忘了改脚本」）───────────────────

def unmapped_providers(our: dict) -> set[str]:
    """预设里有、但 PROVIDER_MAP 未覆盖且非聚合网关的 provider。

    ⚠️ 这是「新增 provider 忘了加映射」的唯一防线。漏了不会报错——报告照样输出，
    只是**从不提及那一整个厂商**，看起来像「那家没有变更」。
    """
    return set(our) - set(PROVIDER_MAP) - NO_UPSTREAM


def orphan_graveyard(graveyard: set) -> set:
    """墓碑表里指向「本脚本核对不了」的 provider 的条目。

    A 类只遍历 PROVIDER_MAP，故指向未映射 provider 的墓碑**永远不会生效**
    （我们已有那张表，脚本不该另起一套判断之外的死配置）。
    """
    return {k for k in graveyard if k[0] not in PROVIDER_MAP}


# ── 对比（纯函数，便于单测）─────────────────────────────────────────

def source_entry(source: dict, provider: str, model_id: str) -> dict | None:
    """在数据源里查一条。provider 无对应上游或模型不存在时返回 None。"""
    key = PROVIDER_MAP.get(provider)
    if not key:
        return None
    return source.get(key, {}).get("models", {}).get(model_id)


def find_new(our: dict, source: dict, since: date | None = None,
             graveyard: set | None = None) -> list[dict]:
    """A 类：数据源有、预设没有。

    两类噪声已剔除：① 已知下线的墓碑（graveyard）；② 由 since 截断的老模型。
    ⚠️ 剩余部分仍含大量**故意不预设**的模型（我们只留主线，不铺全量），
    故按 release_date 降序、只看头部即可。

    ⚠️ 遍历的是 **`our` 的 provider 集合**而非全局 PROVIDER_MAP —— 这样 `--provider`
    过滤对 A 类同样生效（曾用全局遍历，导致 `--provider dashscope` 的报告里混进
    openai / zhipu 的新模型）。未映射的 provider 由 main() 的守卫喊出来，此处跳过。
    """
    graveyard = graveyard or set()
    out = []
    for our_prov in our:
        md_key = PROVIDER_MAP.get(our_prov)
        if not md_key:
            continue
        ours = {m["id"] for m in our.get(our_prov, [])}
        for mid, e in source.get(md_key, {}).get("models", {}).items():
            if mid in ours or (our_prov, mid) in graveyard:
                continue
            rel = e.get("release_date") or ""
            if since and rel:
                try:
                    if date.fromisoformat(rel) < since:
                        continue
                except ValueError:
                    pass
            out.append({
                "provider": our_prov,
                "id": mid,
                "name": e.get("name", ""),
                "context": (e.get("limit") or {}).get("context"),
                "output": (e.get("limit") or {}).get("output"),
                "release_date": rel,
            })
    out.sort(key=lambda x: x["release_date"], reverse=True)
    return out


def find_missing(our: dict, source: dict) -> list[dict]:
    """B 类：预设里有、数据源查无记录（含无对应上游的聚合网关，单独标记）。"""
    out = []
    for prov, models in our.items():
        for m in models:
            if prov in NO_UPSTREAM:
                continue
            if source_entry(source, prov, m["id"]) is None:
                out.append({
                    "provider": prov,
                    "id": m["id"],
                    "retired": m.get("retired", ""),
                    "context": m.get("context_window"),
                })
    return out


def find_mismatch(our: dict, source: dict) -> list[dict]:
    """C 类：双方都有但 context_window 不同。

    ⚠️ **不代表预设错**——实测 8 条不一致里 4 条只是 MB/MiB 口径差，
    1 条（qwen-turbo）是预设打开官方页核过、数据源反而可疑。
    """
    out = []
    for prov, models in our.items():
        for m in models:
            e = source_entry(source, prov, m["id"])
            if e is None:
                continue
            theirs = (e.get("limit") or {}).get("context")
            mine = m.get("context_window")
            if theirs is not None and mine is not None and theirs != mine:
                out.append({
                    "provider": prov,
                    "id": m["id"],
                    "ours": mine,
                    "theirs": theirs,
                    "ratio": round(theirs / mine, 3) if mine else None,
                })
    return out


def collect_candidates(our: dict, source: dict) -> dict[str, list[dict]]:
    """D 类：数据源提供、而预设**完全没有**的字段——新增能力的候选，非核对项。

    max_tokens：我们目前硬编码 65536（generator.py 的 MAX_TOKENS），不分模型。
    price     ：预设一律不填，故费用显示对预置模型永远不可用。
    """
    out: dict[str, list[dict]] = {"max_tokens": [], "price": []}
    for prov, models in our.items():
        for m in models:
            e = source_entry(source, prov, m["id"])
            if e is None:
                continue
            limit = e.get("limit") or {}
            cost = e.get("cost") or {}
            if limit.get("output"):
                out["max_tokens"].append(
                    {"provider": prov, "id": m["id"], "value": limit["output"]})
            if cost.get("input") or cost.get("output"):
                out["price"].append({
                    "provider": prov, "id": m["id"],
                    "in": cost.get("input"), "out": cost.get("output"),
                    "cache_read": cost.get("cache_read"),
                    "cache_write": cost.get("cache_write"),
                })
    return out


# ── 报告 ────────────────────────────────────────────────────────────

def _covered(our: dict, source: dict) -> tuple[int, int]:
    total = sum(len(v) for v in our.values())
    hit = sum(
        1 for prov, models in our.items()
        for m in models if source_entry(source, prov, m["id"]) is not None
    )
    return hit, total


def render(our: dict, source: dict, src_desc: str, new_since: date | None) -> str:
    hit, total = _covered(our, source)
    new = find_new(our, source, since=new_since, graveyard=retired_ids())
    missing = find_missing(our, source)
    mismatch = find_mismatch(our, source)
    cand = collect_candidates(our, source)

    L = []
    L.append(f"=== LiMo 模型预设核对（数据源：{src_desc}）===")
    L.append("")
    L.append(f"覆盖：{total} 条预设中 {hit} 条在数据源有记录（{hit * 100 // total}%）"
             + (f"，A 类已按 {new_since} 之后截断" if new_since else ""))
    L.append("")
    L.append("本报告只回答「哪几条值得去官方页看一眼」，不回答「谁是对的」。")
    L.append("models.dev 是社区聚合数据，已实测到口径混用与乐观标注 —— 一切以官方页为准。")
    L.append("")

    L.append(f"--- A. 可能有新模型（数据源有、预设没有） {len(new)} 条 ---")
    L.append(f"    已剔除 {len(retired_ids())} 条已知下线墓碑（config.py 的 RETIRED_MODEL_IDS）；")
    L.append("    其余多数是我们**刻意不预设**的老模型；按发布日期降序，只看头部即可。")
    for r in new[:40]:
        L.append(f"    {r['provider']:10} {r['id']:26} ctx={r['context']}  {r['release_date']}")
    if len(new) > 40:
        L.append(f"    ... 另有 {len(new) - 40} 条（--json 看全量，或 --new-only 只看近期）")
    L.append("")

    L.append(f"--- B. 可能已下架（预设有条目、数据源查无） {len(missing)} 条 ---")
    if NO_UPSTREAM:
        L.append(f"    （{', '.join(sorted(NO_UPSTREAM))} 已跳过——聚合网关无上游目录，查无属正常）")
    for r in missing:
        tag = f"  [已标 retired {r['retired']}]" if r["retired"] else ""
        L.append(f"    {r['provider']:10} {r['id']:26} ctx={r['context']}{tag}")
    L.append("")

    L.append(f"--- C. 数值不一致（双方都有但不同） {len(mismatch)} 条 ---")
    L.append("    ⚠️ 不代表预设错。历史上 8 条里 4 条只是 MB/MiB 口径差（1000000 vs 1048576）。")
    for r in mismatch:
        ratio = f"{r['ratio']}x" if r["ratio"] else "?"
        L.append(f"    {r['provider']:10} {r['id']:26} 预设={r['ours']:<9} 源={r['theirs']:<9} {ratio}")
    L.append("")

    L.append("--- D. 预设缺失的字段（数据源提供，我们未使用）---")
    L.append(f"    max_tokens   {len(cand['max_tokens'])} 条有值"
             "  ← 我们硬编码 65536，不分模型")
    L.append(f"    价格         {len(cand['price'])} 条有值"
             "  ← 预设一律不填，故预置模型永远看不到费用")
    if cand["price"]:
        L.append("    价格样本（$/百万 token，仅作参考，落地前须核对官方定价页）:")
        for r in cand["price"][:8]:
            L.append(f"      {r['provider']:10} {r['id']:26} in={r['in']} out={r['out']}"
                     f" cache_r={r['cache_read']}")
    L.append("")
    L.append("改完预设后，记得在 config.py 对应条目旁留下官方页 URL 与抓取日期。")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="核对模型预设 vs models.dev")
    ap.add_argument("--from-file", metavar="PATH",
                    help="用本地 JSON 缓存代替联网（离线可用）")
    ap.add_argument("--new-only", action="store_true",
                    help="A 类只列最近 6 个月发布的")
    ap.add_argument("--provider", help="只看某一家（LiMo 的 provider id，如 dashscope）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而非报告")
    args = ap.parse_args()

    try:
        source, desc = load_source(args.from_file)
    except Exception as e:
        print(f"拉取数据源失败：{e}", file=sys.stderr)
        print("可先用 --from-file 指定本地缓存。", file=sys.stderr)
        return 2

    our = our_presets()
    if args.provider:
        if args.provider not in our:
            print(f"未知 provider：{args.provider}（可选：{', '.join(our)}）", file=sys.stderr)
            return 2
        our = {args.provider: our[args.provider]}

    since = date.today() - timedelta(days=183) if args.new_only else None

    # 自身完整性：漏映射 = 静默少核对一整个厂商，必须在出报告前就喊出来
    for prov in sorted(unmapped_providers(our)):
        print(f"⚠️  provider「{prov}」未在 PROVIDER_MAP 中映射，本次**完全未核对**它。"
              f"新增 provider 后请补映射（无上游则加进 NO_UPSTREAM）。", file=sys.stderr)
    for prov, mid in sorted(orphan_graveyard(retired_ids())):
        print(f"⚠️  墓碑 {prov}/{mid} 指向未映射的 provider，永远不会生效。", file=sys.stderr)
    for prov in sorted(set(our) & NO_UPSTREAM):
        print(f"ℹ️  provider「{prov}」是聚合网关（其模型来自各上游），"
              f"数据源没有它的目录 —— 本脚本对它无从核对。", file=sys.stderr)

    if args.json:
        print(json.dumps({
            "source": desc,
            "coverage": dict(zip(("hit", "total"), _covered(our, source))),
            "new": find_new(our, source, since=since, graveyard=retired_ids()),
            "missing": find_missing(our, source),
            "mismatch": find_mismatch(our, source),
            "candidates": collect_candidates(our, source),
        }, ensure_ascii=False, indent=2))
    else:
        print(render(our, source, desc, since))
    return 0


if __name__ == "__main__":
    sys.exit(main())
