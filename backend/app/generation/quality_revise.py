"""成稿检查与**定点**修订 —— 有界循环，最多两轮。

流程（每一轮）::

    quality_rules 扫候选（本地、毫秒级）
        ↓ 没有候选 → 结束
    一次模型调用：逐条判断「真的违规吗」，是则给改写后的整句
        ↓
    按原句**逐字定位**替换（找不到就跳过并记日志）
        ↓
    重新扫：候选数没下降就**放弃这一轮**，保留上一版

⚠️ **模型只做判断与改写，不做决定**：要不要检查、改哪几处、还改不改第二轮，
   全由程序决定。这是它和 agent 循环的区别。
⚠️ **引用原文必须保留**。规则命中不等于要改（引文里的破折号是原文的一部分），
   所以每处都连同 `exceptions` 交给模型判断。
⚠️ **不接受变差**。改完候选数没有下降时回退，宁可留着问题也不留下一个更差的版本；
   回退这件事写日志，不静默。
⚠️ **只动命中的句子**，不让模型重写全文：全文重写会顺手改掉数字与条件，
   而我们没有第二条判据能发现。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from app.generation.quality_rules import Issue, find_issues, issue_count

logger = logging.getLogger(__name__)

#: 轮数上限。两轮是用户拍板的上限（2026-11-17）：一轮覆盖绝大多数，
#: 第二轮只用来收掉第一轮没改干净的残留，再多就是拿成本和不确定性换边际收益。
MAX_ROUNDS = 2

#: 单轮最多送多少处给模型。候选过多说明问题不是「几个措辞」，而是整篇写法要重来，
#: 那种情况不该靠逐句改写解决。
MAX_ISSUES_PER_ROUND = 12

_PROMPT = """下面是一篇笔记里检出的 {n} 处疑似问题。逐条判断：这一处**真的**违规吗？

判断之前先看这段说明，它约束的是整篇的语气与标点：
{why_hint}

候选问题：
{items}

对每一处给出结论：
- 确实违规 → `replacement` 写**改写后的完整句子**，保留原意、全部数字、单位、条件与引用；
- 是引用原文 / 代码 / 规则误报 → `replacement` 写 null。

只输出 JSON 数组，不要任何解释文字：
[{{"i": 0, "replacement": "改后的句子"}}, {{"i": 1, "replacement": null}}]"""


@dataclass
class ReviseResult:
    content: str
    rounds: int
    before: int
    after: int
    #: 每一轮的简述，进日志与调用方提示
    notes: list[str]


def _build_prompt(issues: list[Issue]) -> str:
    items = []
    whys = {}
    for i, it in enumerate(issues[:MAX_ISSUES_PER_ROUND]):
        whys.setdefault(it.rule, it.why)
        items.append(
            f'{i}. 原文：{it.quote}\n'
            f'   问题：{it.why}\n'
            f'   不必改的情形：{it.exceptions}'
        )
    why_hint = "\n".join(f"- {w}" for w in whys.values())
    return _PROMPT.format(n=len(items), why_hint=why_hint, items="\n".join(items))


def _parse(raw: str) -> list[dict]:
    """从模型的回答里抠出 JSON 数组。允许它包一层 ```json。"""
    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return []
    return [d for d in data if isinstance(d, dict)]


def apply_replacements(content: str, issues: list[Issue], answers: list[dict]) -> tuple[str, int]:
    """按原句逐字替换。**找不到原句就跳过** —— 不猜位置，不改相邻内容。

    返回 (新正文, 实际替换数)。
    """
    applied = 0
    for ans in answers:
        replacement = ans.get("replacement")
        if not isinstance(replacement, str) or not replacement.strip():
            continue
        try:
            idx = int(ans.get("i", -1))
        except (TypeError, ValueError):
            continue
        if not 0 <= idx < len(issues):
            continue
        quote = issues[idx].quote
        if not quote or quote not in content:
            logger.info("质量修订：跳过一处（原句已不在正文里）| rule=%s", issues[idx].rule)
            continue
        if replacement.strip() == quote.strip():
            continue
        content = content.replace(quote, replacement.strip(), 1)
        applied += 1
    return content, applied


async def revise(llm, content: str, *, max_rounds: int = MAX_ROUNDS,
                 cancel_event=None) -> ReviseResult:
    """有界循环：扫 → 判断并改写 → 局部复检。最多 `max_rounds` 轮。

    `llm` 是 `LLMService`（要有 `.call`）。调用方负责异常兜底：本函数不吞异常，
    生成失败该按生成的失败路径走，不能因为「修订失败」把整篇笔记丢掉。
    """
    before = issue_count(content)
    notes: list[str] = []
    rounds = 0

    while rounds < max_rounds:
        scan = find_issues(content)
        if not scan.issues:
            break

        rounds += 1
        # ⚠️ **必须关思考**：开着思考时 deepseek-flash 会把整段回答放进 reasoning、
        # `content` 返回空串。2026-11-17 实测：那一轮「候选 4 → 4」看起来像模型拒绝修改，
        # 实际是拿到空串后解析成空表，一次都没改，而日志里只有「未改善，回退」。
        # 同一个坑 `_experiments/07-rerank` 已经记过（不关思考会返回空串）。
        raw = await llm.call(
            _build_prompt(scan.issues), max_tokens=2048, temperature=0.1,
            cancel_event=cancel_event, disable_thinking=True,
        )
        answers = _parse(raw)
        if not raw or "[" not in raw:
            # 空回答与「逐条都判为不用改」是两件事：前者是故障（多半是没关思考，
            # 内容全进了 reasoning），后者是正常结论。分不清就会把故障记成
            # 「模型认为没问题」，而这正是修订静默失效的样子。
            logger.warning("质量修订：模型没有返回可解析的结果（%d 字），本轮放弃", len(raw or ""))
            notes.append(f"第 {rounds} 轮放弃：回答不可解析")
            break
        if not answers:
            # 明确回了空数组：模型逐条看过，认为都不用改
            notes.append(f"第 {rounds} 轮：模型判断无需修改")
            break
        new_content, applied = apply_replacements(content, scan.issues, answers)
        after = issue_count(new_content)

        if after >= issue_count(content):
            # 没变好（或更差）：回退，保留上一版。这条不是保守，是防「越改越糟」
            notes.append(f"第 {rounds} 轮放弃：候选 {issue_count(content)} → {after}")
            logger.info("质量修订：第 %d 轮未改善，回退 | before=%d after=%d",
                        rounds, issue_count(content), after)
            break

        content = new_content
        notes.append(f"第 {rounds} 轮：改 {applied} 处，候选 → {after}")
        logger.info("质量修订：第 %d 轮完成 | 改 %d 处 | 候选 → %d", rounds, applied, after)

    after = issue_count(content)
    if before or rounds:
        logger.info("质量修订结束 | 候选 %d → %d | 轮数 %d", before, after, rounds)
    return ReviseResult(content=content, rounds=rounds, before=before, after=after, notes=notes)
