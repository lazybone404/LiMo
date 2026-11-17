"""笔记产物的**机械**质量规则 —— 产品侧检查与评测脚本共用这一份。

只管「用正则就能判死」的问题：

- 破折号：`notes_styles.py` 的平实档写明标点只用句号、逗号、顿号、问号、引号、冒号。
- 假对立：「不是 A 而是 B」只有在 A 是读者真实存在的误解时才成立。
- 加粗不成对：一行里 `**` 出现奇数次，渲染时会把后半句吃掉。
- 连续同类块：同一类 `limo-block` 连着出现且中间没有正文，视觉上是一堵色块。

⚠️ **它判断不了内容对不对**。「这个数字有没有丢」「这个条件是不是必须的」不在射程内 ——
这里全过，只说明没有机械毛病。别把「复检通过」当成「笔记正确」。
⚠️ 每条规则都带 `exceptions`：命中了不等于要改（引用原文里的破折号必须保留）。
   所以本模块只**产出候选**，判定交给模型（见 `quality_revise.py`）。

⚠️ 评测脚本 `_experiments/09-note-quality/score.py` 也 import 这同一份 —— 两处各写
   一份图案，改了产品不评测、评测不产品，两边都会算「对」。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Rule:
    """一条机械规则。"""

    key: str
    #: 一句话说明「为什么这是问题」，会原样进给模型的提示词
    why: str
    #: 命中图案
    pattern: re.Pattern[str]
    #: 命中了也不该改的情形。交给模型判断，这里只作为提示带上
    exceptions: str = ""


RULES: tuple[Rule, ...] = (
    Rule(
        key="dash",
        why="用了破折号。平实档规定标点只用句号、逗号、顿号、问号、引号、冒号。",
        pattern=re.compile(r"[^。！？\n]*——[^。！？\n]*"),
        exceptions="引用原文、代码或用户提供的原话里的破折号必须保留。",
    ),
    Rule(
        key="false-contrast",
        why="写了「不是 A 而是 B」。只有 A 是读者真实存在的误解时才成立，否则直接说 B。",
        pattern=re.compile(r"[^。！？\n]*不是[^。！？\n]{0,30}而是[^。！？\n]*"),
        exceptions="原话本身这么写、或在逐条辨析两种常见说法时，可以保留。",
    ),
    Rule(
        key="emphasis-words",
        why="用了没有事实支撑的评价词。",
        pattern=re.compile(r"[^。！？\n]*(至关重要|彻底改变|颠覆|综上所述|值得注意的是)[^。！？\n]*"),
        exceptions="后面紧跟具体事实、能撑住这个评价时可以保留。",
    ),
)

#: 加粗不成对：不是正则能表达的，按行数 `**` 的奇偶判断
UNBALANCED_BOLD = Rule(
    key="unbalanced-bold",
    why="一行里 `**` 出现了奇数次，渲染时会把后半句吃掉。",
    pattern=re.compile(r"\*\*"),
    exceptions="代码块内不算。",
)

#: 连续同类块的上限（中间没有正文才算连续）
MAX_SAME_BLOCK_RUN = 2


@dataclass
class Issue:
    """一处候选问题。`quote` 是要交给模型判断的那一整句。"""

    rule: str
    why: str
    exceptions: str
    quote: str
    line: int


@dataclass
class ScanResult:
    issues: list[Issue] = field(default_factory=list)
    #: 连续同类块超限的位置（只报告，不修：改结构不是改写句子能做的）
    block_runs: list[tuple[str, int]] = field(default_factory=list)


FENCE = re.compile(r"^[ \t]*(```|~~~)")
BLOCK_OPEN = re.compile(r'^[ \t]*<!--\s*limo-block:v1\s+type="([^"]+)"\s*-->')
BLOCK_CLOSE = re.compile(r"^[ \t]*<!--\s*/limo-block\s*-->")


def _strip_fences(lines: list[str]) -> list[tuple[int, str]]:
    """去掉代码块内的行（代码里的破折号与星号不是文风问题）。"""
    out, in_code = [], False
    for i, ln in enumerate(lines, 1):
        if FENCE.match(ln):
            in_code = not in_code
            continue
        if not in_code:
            out.append((i, ln))
    return out


def find_issues(markdown: str) -> ScanResult:
    """扫出候选问题。**只产出候选，不判定**。"""
    lines = markdown.splitlines()
    result = ScanResult()

    for i, ln in _strip_fences(lines):
        for rule in RULES:
            for m in rule.pattern.finditer(ln):
                quote = m.group(0).strip()
                if quote:
                    result.issues.append(Issue(rule.key, rule.why, rule.exceptions, quote, i))
        # 加粗：整行 `**` 为奇数
        if ln.count("**") % 2:
            result.issues.append(Issue(
                UNBALANCED_BOLD.key, UNBALANCED_BOLD.why, UNBALANCED_BOLD.exceptions,
                ln.strip(), i))

    # 连续同类块：中间出现正文才断开
    run_type, run_len, pending_body, in_block = "", 0, False, False
    for ln in lines:
        m = BLOCK_OPEN.match(ln)
        if m and not in_block:
            if not pending_body and m.group(1) == run_type:
                run_len += 1
                if run_len == MAX_SAME_BLOCK_RUN + 1:
                    result.block_runs.append((run_type, run_len))
            else:
                run_type, run_len = m.group(1), 1
            pending_body = False
            in_block = True
            continue
        if BLOCK_CLOSE.match(ln) and in_block:
            in_block = False
            continue
        if not in_block and ln.strip():
            pending_body = True

    return result


def issue_count(markdown: str) -> int:
    """给「改进有没有发生」用的标量：候选数 + 连续块超限数。"""
    r = find_issues(markdown)
    return len(r.issues) + len(r.block_runs)
