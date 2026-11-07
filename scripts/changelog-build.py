#!/usr/bin/env python3
"""从 commit 生成发版记录。

## 为什么有这个脚本

2026-11-07 起变更记录策略改了：开发阶段不再逐次手写 CHANGELOG，日常变更的唯一记录
是 commit message，只在发版时汇总一次。这个脚本负责「汇总」那一步。

策略与写法参考 https://github.com/kawayiYokami/P-ai 的 AGENTS.md：changelog 面向
用户，写「变了什么、对你有什么影响」，不写实现细节、不写触发条件与边界说明。

## 它的产出是草稿，不是终稿

脚本能做的只是分类与去重，**改写不了语气**。所以输出里每条前面都带一段说明：
发版前要把这些条目改写成用户视角，删掉括号里的作用域与内部实现措辞。

这一点与 `scripts/check-models.py` 同款定位 —— 脚本的产出是「问题」，不是「答案」。

## 用法

    python scripts/changelog-build.py                  # 从上个 tag 到 HEAD，只打印
    python scripts/changelog-build.py --write          # 写入 docs/changelog/releases/v{VERSION}.md
    python scripts/changelog-build.py --since v0.5.15  # 指定起点
    python scripts/changelog-build.py --version 0.5.22 # 指定版本号（默认读 VERSION）

## 退出码

    0  完成
    2  脚本自身出错（不在 git 仓库 / git 不可用 / 版本号取不到）

「没有新提交」不是错误，返回 0 并打印一句说明 —— 与运维脚本的三态约定一致。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASES_DIR = ROOT / "docs" / "changelog" / "releases"

#: 分组顺序即输出顺序。`fallback` 承接不合规的提交标题 —— 它们不该被静默丢掉，
#: 一个没写 type 的提交往往正是最需要人看一眼的那条。
GROUPS: list[tuple[str, str, tuple[str, ...]]] = [
    ("新增", "feat", ("feat",)),
    ("修复", "fix", ("fix", "hotfix")),
    ("性能", "perf", ("perf",)),
    ("重构", "refactor", ("refactor",)),
    ("文档", "docs", ("docs",)),
    ("其它", "other", ("chore", "build", "ci", "style", "test", "revert")),
]
FALLBACK = "未分类"

#: `type(scope): subject` / `type: subject` / `type!: subject`（破坏性变更）。
#: scope 允许为空、允许含 `/` 与 `-`；type 只收字母，避免把 `fix-xx: ...` 这类
#: 非规范写法错认成分组。
SUBJECT_RE = re.compile(r"^(?P<type>[a-zA-Z]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?:\s*(?P<subj>.+)$")

REC = "\x1e"
FLD = "\x1f"


def _run(args: list[str]) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout


def _last_tag() -> str | None:
    try:
        return _run(["describe", "--tags", "--abbrev=0"]).strip() or None
    except subprocess.CalledProcessError:
        # 没有 tag 的仓库：`describe` 会失败。退回第一条提交，等于「全仓库汇总」。
        try:
            return _run(["rev-list", "--max-parents=0", "HEAD"]).strip().splitlines()[0]
        except (subprocess.CalledProcessError, IndexError):
            return None


def _read_version() -> str:
    p = ROOT / "VERSION"
    if not p.exists():
        raise RuntimeError("找不到 VERSION 文件")
    return p.read_text(encoding="utf-8").strip()


def collect(since: str | None) -> list[dict[str, str]]:
    rng = f"{since}..HEAD" if since else "HEAD"
    fmt = FLD.join(["%h", "%s", "%b", "%ad"]) + REC
    out = _run(["log", rng, f"--format={fmt}", "--date=short", "--no-merges"])

    commits: list[dict[str, str]] = []
    for chunk in out.split(REC):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        parts = chunk.split(FLD)
        if len(parts) < 4:
            continue
        h, subject, body, day = parts[0], parts[1], parts[2], parts[3]

        m = SUBJECT_RE.match(subject)
        if m:
            ctype = m.group("type").lower()
            scope = (m.group("scope") or "").strip()
            bang = "!" if m.group("bang") else ""
            subj = m.group("subj").strip()
        else:
            ctype, scope, bang, subj = "", "", "", subject.strip()

        commits.append({
            "hash": h, "type": ctype, "scope": scope, "bang": bang,
            "subject": subj, "body": body.strip(), "date": day,
        })
    return commits


def group_of(ctype: str) -> str:
    for title, _key, types in GROUPS:
        if ctype in types:
            return title
    return FALLBACK


def render(commits: list[dict[str, str]], version: str, since: str | None) -> str:
    lines: list[str] = []
    lines.append(f"# v{version}")
    lines.append("")
    lines.append(f"发布日期：{date.today().isoformat()}"
                 + (f"　｜　范围：`{since}..HEAD`" if since else ""))
    lines.append("")

    if not commits:
        lines.append("这个范围内没有提交。")
        return "\n".join(lines) + "\n"

    # 分组，保持组内的时间倒序（git log 本身就是倒序）
    buckets: dict[str, list[dict[str, str]]] = {}
    for c in commits:
        buckets.setdefault(group_of(c["type"]), []).append(c)

    order = [t for t, _k, _ts in GROUPS] + [FALLBACK]
    for title in order:
        items = buckets.get(title)
        if not items:
            continue
        lines.append(f"## {title}")
        lines.append("")
        for c in items:
            scope = f"（{c['scope']}）" if c["scope"] else ""
            bang = "　⚠️ 破坏性变更" if c["bang"] else ""
            lines.append(f"- {c['subject']}{scope}{bang}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"共 {len(commits)} 条提交。原始记录：")
    lines.append("")
    lines.append("<details><summary>展开</summary>")
    lines.append("")
    for c in commits:
        raw = f"{c['type']}({c['scope']}): {c['subject']}" if c["type"] else c["subject"]
        lines.append(f"- `{c['hash']}` {c['date']} {raw}")
    lines.append("")
    lines.append("</details>")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="从 commit 生成发版记录草稿")
    ap.add_argument("--since", help="起点 ref（默认：最近一个 tag）")
    ap.add_argument("--version", help="版本号（默认读 VERSION）")
    ap.add_argument("--write", action="store_true", help="写入 docs/changelog/releases/")
    args = ap.parse_args()

    try:
        version = args.version or _read_version()
        since = args.since or _last_tag()
        commits = collect(since)
    except (RuntimeError, subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"changelog-build: {e}", file=sys.stderr)
        return 2

    text = render(commits, version, since)

    if not args.write:
        print(text)
        return 0

    RELEASES_DIR.mkdir(parents=True, exist_ok=True)
    target = RELEASES_DIR / f"v{version}.md"
    if target.exists():
        print(f"changelog-build: {target.relative_to(ROOT)} 已存在，不覆盖", file=sys.stderr)
        return 2
    target.write_text(text, encoding="utf-8")
    print(f"已写入 {target.relative_to(ROOT)}")
    print("下一步：把条目改写成用户视角（删掉作用域、内部实现措辞），再决定是否发布。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
