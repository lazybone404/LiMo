#!/usr/bin/env python3
"""产物结构统计 —— 回答「产物真的有 `##` 章节吗」。

用法：
    python scripts/check-artifact-structure.py
    python scripts/check-artifact-structure.py --json
    python scripts/check-artifact-structure.py --root D:/somewhere --root D:/other
    python scripts/check-artifact-structure.py --selftest      # 自证扫描器有区分力

**定位：可重复的验收工具，不是门禁。** prompt 改动前后各跑一次，看「按文风档分组的
H2 计数分布有没有上移」。**它不进 `scripts/check-all`** —— 它没有可以判红的标准，
只有一组要人看的分布。

四个问题（按重要性）：

  ① 按**文风档**（`generated.params.style`）分组的 H1/H2/H3 计数 → 验证「`plain`
     档产物没有 `##`」这一推断；
  ② 按**来源类型**（`source.sourceType`）分组 → 验证「视频路径无标题、网页/文档
     路径有标题」；
  ③ 相邻 H2 之间的正文字符数分布 → 判断切卡后会不会出现「一张巨卡」；
  ④ 表格/代码块/引用/图片/列表的出现率 → 为「19 种卡片里哪些值得做」提供第一份
     真实数据（此前是零数据）。

⚠️ **输出绝不回显任何标题文本、正文或文件名** —— 只给计数与分布。这份报告会被贴进
issue、传给外部讨论，产物内容属于用户隐私。与 `check-secrets.py` 的「不回显凭据值」
是同一条纪律：自检工具自己要是把内容打出来，它就成了新的泄漏通道。

⚠️ **只调读路径**（`list_transcripts` / `get_markdowns_by_transcript`），不写任何文件。
唯一的例外是 `Store.__init__` 的既有行为：`index.json` **缺失或损坏**时它会备份并
重建一次（库本来就坏着）；正常库上它是纯读的。

⚠️ H2 的判定走 `app/services/markdown_sections.py::iter_headings` —— **与导出切分器
同源**。各写一份的话症状是静默的：统计说「3 个 `##`」而导出只切出 2 张卡，两边都不
报错。
"""

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

#: 无自证头（历史产物）时的占位标签。**不是**「解析失败」—— 绝大多数历史产物就没头。
UNLABELED = "(未标注)"


# ── 从 backend 取唯一真相源 ────────────────────────────────────────

@dataclass
class Backend:
    """backend 侧被本脚本复用的东西。

    ⚠️ 一律**取用**而非复写：产物头怎么解析、正文怎么剥头、H2 怎么判，各自都有唯一
    真相源。抄一份的失效方式是「统计与导出各说各话，且都不报错」。
    """
    config_io: Any
    store_cls: Any
    strip_meta: Any
    parse_artifact_meta: Any
    iter_headings: Any
    fence_re: Any


def load_backend() -> Backend:
    sys.path.insert(0, str(ROOT / "backend"))
    from app.core import config_io
    from app.core.transcript_meta import parse_artifact_meta, strip_meta
    from app.services.markdown_sections import FENCE_RE, iter_headings
    from app.storage.store import Store
    return Backend(config_io=config_io, store_cls=Store, strip_meta=strip_meta,
                   parse_artifact_meta=parse_artifact_meta,
                   iter_headings=iter_headings, fence_re=FENCE_RE)


def resolve_data_dirs(explicit: list[str], be: Backend) -> list[Path]:
    """数据目录 —— 走 `config_io` 的路径判据，不自己拼。

    ⚠️ 刻意**不调 `load_config()`**：那个函数每次启动都会 `copy2` 一份 `.bak`，
    一个「只读」的统计脚本不该有那个副作用。这里只要 `data_dir` 一个字段。
    """
    if explicit:
        return [Path(p) for p in explicit]
    cfg_file = be.config_io.config_file_path()
    try:
        import yaml
        cfg = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
    except Exception:
        # 读不到 / 解析不了 → 用默认目录。宽 except 是**有意**的：这只是「往哪找」的
        # 兜底，为此让整个统计失败不划算；真有别的问题，下一步「目录不存在」会报出来。
        cfg = {}
    return [Path(cfg.get("data_dir") or str(cfg_file.parent))]


# ── 单份产物的结构画像 ─────────────────────────────────────────────

#: 三条**要求 `##` 章节**的格式指令（`get_toc_format` / `get_link_format` /
#: `get_screenshot_format`）。它们的指令进 `<hard_constraints>`，优先级高于风格档。
#: 判断「产物里的 `##` 是模型自发写的、还是这几条指令要求的」全靠这个集合。
H2_FORMATS = frozenset({"toc", "link", "screenshot"})


@dataclass
class Artifact:
    """一份产物的**结构画像** —— 刻意不含任何原文。"""
    mode: str
    style: str
    source_type: str
    has_head: bool          # 有自证头才有 params，历史产物（无头）无从判断
    h2_formats: bool        # 勾了 toc/link/screenshot 中任意一项
    levels: Counter = field(default_factory=Counter)   # 标题级别 → 个数
    section_chars: list[int] = field(default_factory=list)  # 相邻 H2 之间的正文字符数
    elements: Counter = field(default_factory=Counter)      # 块元素 → 出现次数


def detect_elements(md: str, fence_re, iter_headings) -> Counter:
    """块元素出现次数。**围栏感知** —— 代码块内部的 `>` `|` `- ` 不是引用/表格/列表。"""
    counts: Counter = Counter()
    fences = 0
    in_fence = False
    for raw in md.split("\n"):
        line = raw[:-1] if raw.endswith("\r") else raw
        if fence_re.match(line):
            fences += 1
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if "![" in line and "](" in line:
            counts["image"] += 1
        stripped = line.strip()
        if stripped.startswith(">"):
            counts["quote"] += 1
        elif stripped.startswith("|") and stripped.endswith("|") and len(stripped) > 1:
            counts["table_row"] += 1
        elif _is_list_item(line):
            counts["list"] += 1
    # 围栏成对出现，开+关算一个代码块；落单的按未闭合处理，照样算一块。
    counts["code_block"] = (fences + 1) // 2
    return counts


_UL_RE = re.compile(r"^\s*[-*+]\s+")
_OL_RE = re.compile(r"^\s*\d+[.)]\s+")


def _is_list_item(line: str) -> bool:
    """无序（`- ` / `* ` / `+ `）或有序（`1. ` / `1) `）列表项。缩进任意。"""
    return bool(_UL_RE.match(line) or _OL_RE.match(line))


def profile(mv: dict, be: Backend) -> Artifact:
    """把一条 markdown 条目变成结构画像。"""
    text = mv.get("content", "") or ""
    head = be.parse_artifact_meta(text)[0] or {}
    generated = head.get("generated") or {}
    params = generated.get("params") or {}
    source = head.get("source") or {}

    body = be.strip_meta(text)
    lines = body.split("\n")
    headings = [(lineno, level) for lineno, level, _ in be.iter_headings(body)]
    levels = Counter(level for _, level in headings)

    h2_at = [lineno for lineno, level in headings if level == 2]
    section_chars: list[int] = []
    for i, start in enumerate(h2_at):
        end = h2_at[i + 1] if i + 1 < len(h2_at) else len(lines)
        section_chars.append(len("\n".join(lines[start:end]).strip()))

    formats = params.get("formats") or []
    return Artifact(
        mode=str(mv.get("mode", "")),
        style=str(params.get("style") or UNLABELED),
        source_type=str(source.get("sourceType") or UNLABELED),
        has_head=bool(head),
        h2_formats=bool(H2_FORMATS & set(formats)),
        levels=levels,
        section_chars=section_chars,
        elements=detect_elements(body, be.fence_re, be.iter_headings),
    )


def _format_group(a: Artifact) -> str:
    """「勾选 / 未勾选 / 无记录」三分 —— 用来回答「`##` 是模型自发还是指令要求的」。

    ⚠️ 无自证头的历史产物**必须单列**，不能并进「未勾选」：它们占了本库的一大半，
    并进去会把「未勾选」这一桶稀释到看不出差异，读起来像「勾不勾都一样」。
    """
    if not a.has_head:
        return "③ 无自证头（无从判断）"
    return "① 勾了 toc/link/screenshot" if a.h2_formats else "② 未勾选"


def collect(data_dirs: list[Path], be: Backend) -> tuple[list[Artifact], int]:
    """遍历所有文档的**全部版本**产物。返回 (画像列表, 文档篇数)。"""
    arts: list[Artifact] = []
    docs = 0
    for d in data_dirs:
        store = be.store_cls(str(d))
        for tx in store.list_transcripts():
            docs += 1
            for mv in store.get_markdowns_by_transcript(tx["id"]):
                arts.append(profile(mv, be))
    return arts, docs


# ── 汇总 ───────────────────────────────────────────────────────────

def _no_h2_ratio(group: list[Artifact]) -> str:
    if not group:
        return "—"
    n = sum(1 for a in group if a.levels.get(2, 0) == 0)
    return f"{round(100 * n / len(group))}%"


def _h2_median(group: list[Artifact]) -> str:
    if not group:
        return "—"
    return str(int(statistics.median(a.levels.get(2, 0) for a in group)))


def _group_rows(arts: list[Artifact], key) -> list[tuple[str, list[Artifact]]]:
    buckets: dict[str, list[Artifact]] = {}
    for a in arts:
        buckets.setdefault(key(a), []).append(a)
    return sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[0]))


def _render_group(title: str, rows: list[tuple[str, list[Artifact]]]) -> str:
    out = [f"\n── {title} ──", f"{'分组':<14}{'份数':>5}{'H1 中位':>8}{'H2 中位':>8}"
                          f"{'H3 中位':>8}{'无 H2 占比':>11}"]
    for name, group in rows:
        out.append(f"{name[:13]:<14}{len(group):>5}"
                   f"{_median_of(group, 1):>8}{_median_of(group, 2):>8}"
                   f"{_median_of(group, 3):>8}{_no_h2_ratio(group):>11}")
    return "\n".join(out)


def _median_of(group: list[Artifact], level: int) -> str:
    if not group:
        return "—"
    return str(int(statistics.median(a.levels.get(level, 0) for a in group)))


def _render_sections(arts: list[Artifact]) -> str:
    chars = [c for a in arts for c in a.section_chars]
    if not chars:
        return "\n── H2 之间的正文字符数 ──\n本轮产物中**没有任何 H2**（`section_chars` 全空）"
    chars.sort()

    def pct(p: float) -> int:
        return chars[min(len(chars) - 1, int(len(chars) * p))]

    return ("\n── H2 之间的正文字符数 ──\n"
            f"样本 {len(chars)} 节 ｜ p50 = {pct(0.5):,} ｜ p90 = {pct(0.9):,} ｜ "
            f"max = {chars[-1]:,}（最长的那个节 = 一张巨卡）")


def _render_elements(arts: list[Artifact]) -> str:
    keys = ("table_row", "code_block", "quote", "image", "list")
    labels = {"table_row": "表格行", "code_block": "代码块", "quote": "引用",
              "image": "图片", "list": "列表"}
    out = ["\n── 块元素出现率（含该元素的产物占比）──"]
    n = len(arts) or 1
    out.append(" ｜ ".join(
        f"{labels[k]} {round(100 * sum(1 for a in arts if a.elements.get(k)) / n)}%"
        for k in keys))
    return "\n".join(out)


def render(arts: list[Artifact], docs: int, data_dirs: list[Path]) -> str:
    if not arts:
        return (f"产物结构统计\n数据目录：{'、'.join(str(d) for d in data_dirs)}\n\n"
                "⚠️ 没有找到任何产物 —— 数据目录为空，或路径不对。")
    lines = [
        "产物结构统计",
        f"数据目录：{'、'.join(str(d) for d in data_dirs)}",
        f"共 {len(arts)} 份产物，来自 {docs} 篇文档",
        _render_group("按文风档（generated.params.style）",
                      _group_rows(arts, lambda a: a.style)),
        _render_group("按来源类型（source.sourceType）",
                      _group_rows(arts, lambda a: a.source_type)),
        _render_group("按是否勾选「要求 ## 的格式」（toc/link/screenshot）",
                      _group_rows(arts, _format_group)),
        _render_sections(arts),
        _render_elements(arts),
    ]
    return "\n".join(lines)


# ── 自证 ───────────────────────────────────────────────────────────

def selftest(be: Backend) -> int:
    """自证统计器**有区分力**：造「有 3 个 H2」与「0 个 H2」两份，断言各自数对。

    ⚠️ 两段都**故意**在代码块里塞一句 `## 假标题`。少了这个用例，「0 个 H2」与
    「扫描器根本没跑起来 / 把围栏内的当标题了」无法区分（铁律 #38）。
    """
    with_h2 = "# 大标题\n\n引言\n\n## 甲\n\n正文甲\n\n## 乙\n\n正文乙\n\n## 丙\n\n正文丙\n"
    no_h2 = ("一整段话，没有任何二级标题。\n\n```\n## 这是代码块里的假标题\n"
             "> 这也不是引用\n- 这也不是列表\n```\n")

    checks = [
        ("有 3 个 H2 的样本数出 3", profile({"content": with_h2}, be).levels.get(2, 0) == 3),
        ("有 3 个 H2 的样本数出 1 个 H1", profile({"content": with_h2}, be).levels.get(1, 0) == 1),
        ("有 3 个 H2 的样本切出 3 段", len(profile({"content": with_h2}, be).section_chars) == 3),
        ("无 H2 的样本数出 0", profile({"content": no_h2}, be).levels.get(2, 0) == 0),
        ("围栏内的假标题**不**计数", profile({"content": no_h2}, be).levels.get(2, 0) == 0),
        ("围栏内的假引用**不**计数", profile({"content": no_h2}, be).elements.get("quote", 0) == 0),
        ("围栏内的假列表**不**计数", profile({"content": no_h2}, be).elements.get("list", 0) == 0),
        ("围栏本身被算成 1 个代码块", profile({"content": no_h2}, be).elements.get("code_block", 0) == 1),
    ]
    for label, cond in checks:
        print(f"{'PASS' if cond else 'FAIL'}: {label}")
    if all(c for _, c in checks):
        print("\n统计器自证通过：数得准，且不被围栏内的假标题骗到")
        return 0
    print("\n统计器自证**失败** —— 上面的计数不可信")
    return 1


# ── 主流程 ─────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="LiMo 产物结构统计（只读）")
    ap.add_argument("--root", action="append", default=[],
                    help="数据目录，可重复。省略则从 config 的 data_dir 取")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而非报告")
    ap.add_argument("--selftest", action="store_true", help="自证统计器有区分力后退出")
    args = ap.parse_args()

    be = load_backend()
    if args.selftest:
        return selftest(be)

    data_dirs = resolve_data_dirs(args.root, be)
    missing = [d for d in data_dirs if not d.is_dir()]
    if missing:
        print(f"⚠️ 数据目录不存在：{'、'.join(str(d) for d in missing)}", file=sys.stderr)
        return 1

    arts, docs = collect(data_dirs, be)

    if args.json:
        print(json.dumps({
            "data_dirs": [str(d) for d in data_dirs],
            "documents": docs,
            "artifacts": len(arts),
            "by_style": {k: len(g) for k, g in _group_rows(arts, lambda a: a.style)},
            "by_source_type": {k: len(g) for k, g in _group_rows(arts, lambda a: a.source_type)},
            "h2_counts": [a.levels.get(2, 0) for a in arts],
            "section_chars": sorted(c for a in arts for c in a.section_chars),
            "elements": {
                k: sum(1 for a in arts if a.elements.get(k))
                for k in ("table_row", "code_block", "quote", "image", "list")
            },
        }, ensure_ascii=False, indent=2))
    else:
        print(render(arts, docs, data_dirs))

    return 0 if arts else 1


if __name__ == "__main__":
    # 退出码三态，**必须能区分「跑完没东西」与「脚本崩了」**：
    #   0 = 统计完成（有产物） ｜ 1 = 没找到任何产物 ｜ 2 = 脚本自身出错
    # 少了 2 这一档，「指错了目录」与「脚本坏了」都是 1。
    import traceback
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        print("\n统计**未能完成**（既不是「有产物」也不是「没找到」）—— 退出码 2",
              file=sys.stderr)
        sys.exit(2)
