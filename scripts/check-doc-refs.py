#!/usr/bin/env python3
"""文档交叉引用校验 —— 「第 N 节」必须指向本文档真实存在的章节。

## 为什么需要它（2026-10-11）

一份**主题是「文档与代码漂移」**的文档，自己的 16 处「第 N 节」引用里有 13 处
指向了不存在的章节。外部审查者只发现了其中 1 处，其余 12 处是人工核对时漏掉的。
根因很干净：

    正文里的节号是**手写**的，而章节结构在成稿过程中重排过。

于是节号有了两个真相源（标题的实际顺序 vs 正文里手写的编号），而没有任何东西
会在它们不一致时报警。这与项目模式 #54「同源漂移」是同一回事 —— 只不过这次漂
的是文档自己，而漂移的载体是**手写节号**，正是最容易以为「不用机器管」的那类。
同一批修掉的还有规模数字：它们也是手抄进文档的，整张表停在了旧的时点。

## 判据

「第 N 节」在本项目里一律指**本文档内**的章节（跨文件引用会写明文件名），
因此校验不需要解析任何链接：

    本文档的编号标题集合 ⊇ 本文档所有「第 N 节」引用里的 N

## 准入：什么样的文档适用这条判据

必须有 **h2 级整数编号标题**（`## 1. xxx`）。这不是洁癖，是必需的：CHANGELOG 用
`### 1. 修复：...` 表示「同一天做了哪几件事」，若拿它当章节编号，`docs/CHANGELOG.md`
会被整篇纳入，而它里面的「新增第 8 节」指的其实是**测试文件**里的第 8 节 ——
2026-10-11 实测会误报 2 处。只看恰好两个 `#` 就能把这一整类误报关掉。

## 已知边界（刻意不覆盖）

- **跨文件引用**：`见《密钥与凭据存储约定.md》第 5 节` 这类**不在校验范围**。
  判据只认文件内引用；这类引用在 `pi-docs/` 里有若干处，是否失效目前无人检查。
- **并列写法**：`第 4.1 / 6.3 / 6.5 节` 不会被逐项校验（正则只认「第 N 节」紧邻
  的形式）。目前正文里没有这种写法。
- **豁免通道**：把引用包进**反引号**即可跳过（行内代码视为字面文本）。描述
  「旧版本这里写的是 `第 3.1 节`」这类记录时用它 —— 勘误表就是这么写的。

## 用法

    python scripts/check-doc-refs.py            # 校验整个仓库
    python scripts/check-doc-refs.py --selftest # 只跑判据自测（合成样本，不读仓库）

## 退出码

`0` 干净 ｜ `1` 有失效引用 ｜ `2` 脚本自身出错（与 check-secrets.py 同款三态：
少了 2 这一档，「脚本崩了」与「查出问题」在 CI 上是同一个 1，无法区分）。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 编号式标题：`## 0. xxx` / `### 5.2 xxx` / `### 4.3「xxx」`
# ⚠️ 结尾前瞻是必须的 —— 没有它，CHANGELOG 的日期标题 `## 2026-10-11` 会被读成
#    编号「2026」。
HEADING_RE = re.compile(r"^#{1,6}\s+(\d+(?:\.\d+)*)(?=[.\s、）)「]|$)")
TOP_HEADING_RE = re.compile(r"^##\s+\d+(?=[.\s、）)「]|$)")
REF_RE = re.compile(r"第\s*(\d+(?:\.\d+)*)\s*节")
FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
INLINE_CODE_RE = re.compile(r"`[^`]*`")

SKIP_DIRS = {
    "node_modules", ".git", ".venv", "dist", "build", "__pycache__",
    "target", "bin", ".claude", ".codegraph",
}


def iter_docs():
    """遍历待校验的 md。跳过依赖目录、构建产物副本与技能库。"""
    for path in sorted(ROOT.rglob("*.md")):
        rel = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        yield path, rel


def strip_code(lines):
    """围栏代码块整行置空；行内代码（反引号内）剥掉。返回 [(行号, 文本)]。

    两处都算「字面文本」而非引用 —— 这给了文档一条**零成本的豁免通道**：
    要记录「旧版本这里写的是第 3.1 节」时，把它包进反引号即可。
    """
    out, in_fence = [], False
    for lineno, raw in enumerate(lines, 1):
        if FENCE_RE.match(raw):
            in_fence = not in_fence
            out.append((lineno, ""))
            continue
        out.append((lineno, "" if in_fence else INLINE_CODE_RE.sub("", raw)))
    return out


def check_text(text: str):
    """对**单篇文档的文本**求值 —— 判据的唯一实现，CLI 与自测共用。

    返回 `(applicable, ref_count, bad, heads)`：
      - `applicable=False` → 该文档不含 h2 整数编号，本判据不适用（`bad` 恒为空）
      - `bad` 是 `[(行号, 编号, 原文片段)]`
    """
    lines = strip_code(text.splitlines())

    heads: dict[str, int] = {}
    for lineno, t in lines:
        m = HEADING_RE.match(t)
        if m:
            heads.setdefault(m.group(1), lineno)

    if not any(TOP_HEADING_RE.match(t) for _, t in lines):
        return False, 0, [], heads

    ref_count, bad = 0, []
    for lineno, t in lines:
        m = REF_RE.search(t)
        if m:
            ref_count += 1
            if m.group(1) not in heads:
                bad.append((lineno, m.group(1), t.strip()))
    return True, ref_count, bad, heads


def _num_key(num: str):
    return [int(x) for x in num.split(".")]


def _fmt_numbers(heads: dict[str, int]) -> str:
    nums = sorted(heads, key=_num_key)
    if len(nums) <= 30:
        return ", ".join(nums)
    return ", ".join(nums[:30]) + f" …（共 {len(nums)} 个）"


def run_repo() -> int:
    try:
        docs = list(iter_docs())
    except OSError as e:
        print(f"[脚本错误] 遍历文档失败：{e}", file=sys.stderr)
        return 2

    checked_docs = checked_refs = 0
    failures: list[tuple[str, int, str, str, dict[str, int]]] = []
    skipped: list[str] = []

    for path, rel in docs:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            print(f"[脚本错误] 读取失败 {rel.as_posix()}：{e}", file=sys.stderr)
            return 2

        applicable, ref_count, bad, heads = check_text(text)
        if not applicable:
            # 没有编号章节的文档不适用本判据 —— 它里面的「第 N 节」指的是别处的节
            # （测试文件、别的文档），我们无从校验，也**不该**报它。
            if ref_count or REF_RE.search(text):
                skipped.append(rel.as_posix())
            continue
        if not ref_count:
            continue

        checked_docs += 1
        checked_refs += ref_count
        failures.extend((rel.as_posix(), ln, num, snip, heads) for ln, num, snip in bad)

    if failures:
        for rel, lineno, num, snippet, heads in failures:
            print(f"{rel}:{lineno}: 「第 {num} 节」不存在")
            print(f"    原文: {snippet[:110]}")
            print(f"    本文档现有编号: {_fmt_numbers(heads)}")
        print()
        print(
            f"✗ 交叉引用校验失败：{len(failures)} 处引用指向不存在的章节 "
            f"（已校验 {checked_docs} 个文档 / {checked_refs} 处引用）"
        )
        print("  修法：改成正确节号；若这处引用**刻意在记录旧文本**（如勘误表），")
        print("       把引用包进反引号即可豁免。")
        return 1

    print(f"✓ 交叉引用校验通过：{checked_docs} 个文档 / {checked_refs} 处引用")
    if skipped:
        print(
            f"  （{len(skipped)} 个文档无编号章节，不适用本判据："
            f"{', '.join(skipped)}）"
        )
    return 0


# ── 判据自测 ──────────────────────────────────────────────────────────────
# 项目的纪律是「每条防御性断言都必须能被『改坏实现 → 断言变红』证伪，否则视为
# 假绿灯」。本脚本没有 pytest 用例（它是门禁项本身，不是被测模块），故把区分力
# 的自证做进 `--selftest`：每个用例都对应一处**判据里真实的取舍**，删掉对应逻辑
# 就会红。期望值 `None` = 「本判据不适用」。
_SELFTEST_CASES: list[tuple[str, str, int | None]] = [
    ("正确引用不报", "## 1. 基线\n\n见第 1 节。\n", 0),
    ("错误引用要报", "## 1. 基线\n\n见第 2 节。\n", 1),
    ("子节引用正确不报", "## 1. 基线\n\n### 1.2 细节\n\n见第 1.2 节。\n", 0),
    ("子节引用写错要报", "## 1. 基线\n\n### 1.2 细节\n\n见第 1.3 节。\n", 1),
    ("反引号豁免", "## 1. 基线\n\n旧版写的是 `第 9 节`。\n", 0),
    ("围栏代码块不参与", "## 1. 基线\n\n```\n第 9 节\n```\n", 0),
    ("无 h2 整数编号 → 不适用", "### 1. 局部编号\n\n见第 9 节。\n", None),
    ("日期标题不误判为编号", "## 2026-10-11 — 某事\n\n见第 9 节。\n", None),
    ("CHANGELOG 形态不误报", "## 2026-10-11 — 某事\n\n### 1. 修复\n\n新增第 8 节断言\n", None),
]


def selftest() -> int:
    print(f"判据自测（{len(_SELFTEST_CASES)} 条合成样本）")
    failed = 0
    for name, text, expect in _SELFTEST_CASES:
        applicable, _refs, bad, _heads = check_text(text)
        got = len(bad) if applicable else None
        ok = got == expect
        if not ok:
            failed += 1
        shown = "不适用" if got is None else f"{got} 处失效"
        want = "不适用" if expect is None else f"{expect} 处失效"
        print(f"  {'✓' if ok else '✗'} {name}  → {shown}" + ("" if ok else f"（期望 {want}）"))
    print()
    if failed:
        print(f"✗ 自测失败：{failed}/{len(_SELFTEST_CASES)}")
        return 1
    print("✓ 自测通过")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="文档交叉引用校验（「第 N 节」是否存在）")
    parser.add_argument("--selftest", action="store_true",
                        help="只跑判据自测，不读仓库文档")
    args = parser.parse_args()
    return selftest() if args.selftest else run_repo()


if __name__ == "__main__":
    sys.exit(main())
