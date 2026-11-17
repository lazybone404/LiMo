"""成稿检查与定点修订的判据。

对应 `app/generation/quality_rules.py`（机械规则）与 `quality_revise.py`（有界循环）。

⚠️ 这批判据的失效形态都是**静默**的：规则写得过宽 → 每次生成都多花一次调用去改
   根本不该改的引用原文；循环写错 → 改了两轮还是原样，日志里一句都没有。
"""

from __future__ import annotations

import asyncio
import json

from app.generation.quality_rules import find_issues, issue_count
from app.generation.quality_revise import apply_replacements, revise


class _FakeLLM:
    """只做一件事：按 `原文：…` 把候选抠出来，返回一份改好的映射。"""

    def __init__(self, replacements: list[str | None]):
        self._replacements = replacements
        self.calls = 0
        self.prompts: list[str] = []

    async def call(self, prompt: str, **kwargs):
        self.calls += 1
        self.prompts.append(prompt)
        quotes = [
            line.split("原文：", 1)[1].strip()
            for line in prompt.splitlines() if "原文：" in line
        ]
        out = [
            {"i": i, "replacement": self._replacements[i]}
            for i in range(min(len(quotes), len(self._replacements)))
        ]
        return json.dumps(out, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════
# 1. 规则：命中的是候选，不是判决
# ═══════════════════════════════════════════════════════════

def test_检出破折号与假对立():
    text = "这一步很关键——它决定了后面所有事。\n这不是加热，而是改变了压力。\n"
    keys = [i.rule for i in find_issues(text).issues]
    assert "dash" in keys
    assert "false-contrast" in keys


def test_代码块里的破折号不算问题():
    # 代码里的 `——` 是注释的一部分，改了就是改代码
    text = "```\n# 这行——是注释\nprint('x')\n```\n"
    assert find_issues(text).issues == []


def test_加粗不成对按行判奇偶():
    assert find_issues("**开头没有结尾。\n").issues
    assert not find_issues("**开头** 和 **结尾**。\n").issues


def test_连续同类块_中间有正文就不算连续():
    def block(t):
        return f'<!-- limo-block:v1 type="{t}" -->\n内容\n<!-- /limo-block -->\n'

    glued = block("key-takeaway") * 3
    assert find_issues(glued).block_runs, "三块紧挨着应判超限"

    spaced = block("key-takeaway") + "中间有一段正文。\n" + block("key-takeaway") + \
        "中间有一段正文。\n" + block("key-takeaway")
    assert not find_issues(spaced).block_runs, "隔着正文的两块不是连续色块"


# ═══════════════════════════════════════════════════════════
# 2. 替换：逐字定位，找不到就跳过
# ═══════════════════════════════════════════════════════════

def test_找不到原句就跳过():
    text = "甲。乙。"
    issues = find_issues("这一句——有问题。").issues
    # 用一个不在正文里的 quote
    new, applied = apply_replacements(text, issues, [{"i": 0, "replacement": "改。"}])
    assert applied == 0 and new == text


def test_替换为相同内容不算改动():
    text = "这一句——有问题。"
    issues = find_issues(text).issues
    new, applied = apply_replacements(text, issues, [{"i": 0, "replacement": issues[0].quote}])
    assert applied == 0 and new == text


# ═══════════════════════════════════════════════════════════
# 3. 有界循环：改好了就停，改坏了就回退
# ═══════════════════════════════════════════════════════════

def _run(**kw):
    return asyncio.run(revise(**kw))


def test_一轮改干净就结束():
    text = "这一步很关键——它决定了后面所有事。\n"
    llm = _FakeLLM(["这一步很关键，它决定了后面所有事。"])
    r = _run(llm=llm, content=text)
    assert r.rounds == 1
    assert r.after == 0
    assert "——" not in r.content


def test_改坏了就回退_原文一字不动():
    text = "这一步很关键——它决定了后面所有事。\n"
    # 模型给的替换里又有破折号，候选数没有下降
    llm = _FakeLLM(["改了一句——还是破折号。"])
    r = _run(llm=llm, content=text)
    assert r.content == text, "变差时必须原样退回"
    assert r.after == r.before


def test_没有候选时一次调用都不发():
    text = "一句干净的话。\n"
    llm = _FakeLLM([])
    r = _run(llm=llm, content=text)
    assert llm.calls == 0
    assert r.rounds == 0


def test_轮数上限生效():
    # 每轮都只去掉一个破折号，且模型永远给得出「少一个」的版本
    text = "甲——一。\n乙——二。\n丙——三。\n丁——四。\n"
    llm = _FakeLLM(["甲。"])
    r = _run(llm=llm, content=text, max_rounds=2)
    assert llm.calls <= 2, "上限之外的调用不允许发生"
    assert r.rounds <= 2


def test_候选数标量是候选加块超限():
    assert issue_count("一句干净的话。") == 0
    assert issue_count("带破折号——的句子。") >= 1


def test_模型返回空串时放弃并留痕_不算改好():
    # 2026-11-17 实测：开了思考时 content 返回空串，解析成空表后「一次都没改」，
    # 日志只写「未改善，回退」—— 看起来像模型拒绝修改。这里钉住「要记成拒绝解析」。
    class _Empty:
        def __init__(self):
            self.calls = 0
        async def call(self, prompt, **kw):
            self.calls += 1
            return ""

    text = "这句话——有破折号。\n"
    llm = _Empty()
    r = _run(llm=llm, content=text)
    assert r.content == text
    assert llm.calls == 1, "空回答只该试一次，不该在循环里空转"
    assert any("不可解析" in n for n in r.notes)


def test_模型明确回空数组表示都不用改():
    class _Decline:
        async def call(self, prompt, **kw):
            return "[]"

    text = "这句话——有破折号。\n"
    r = _run(llm=_Decline(), content=text)
    assert r.content == text
    assert any("无需修改" in n for n in r.notes)
