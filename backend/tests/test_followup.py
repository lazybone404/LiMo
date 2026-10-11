"""追问建议（`agent/followup.py`）—— 抽取 / 判定 / 补调语义 / 失败显式化。

本模块的价值只有一句：**用户开了开关，就一定有一张追问卡**。所有用例都在钉这句话的
某个环节 —— 抽取层与前端同源、判据不比前端松、补调只在该发生时发生、任何失败都不抛
（但也不静默，`error` 字段由 chat.py 承担）。

⚠️ **五条对照实验**（第 1 / 5 / 6 / 9 条与 fixture 长度守卫）：每条都自证「判据有区分力」
—— 把实现改回错误版本，该断言立刻红。没有对照实验的测试只证明「现在没炸」。

⚠️ 抽取层的**跨语言共享 fixture** 见 `frontend/src/test/fixtures/follow-up-parse.json`
（前端 `chatFollowUp.test.tsx` 读同一份）。两份实现必然存在（JS 渲染 / Python 补调），
这份 JSON 是它们唯一的一致性证据 —— 「两边写得像」不是证据。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent import followup
from app.agent.followup import (
    FOLLOWUP_PROMPT,
    MAX_QUESTIONS,
    MIN_QUESTION_CHARS,
    build_followup_prompt,
    digest_answer,
    extract_followup_items,
    generate_followups,
    has_usable_followup,
    should_attempt,
)
from app.agent.session import SessionManager
from app.core.artifact_blocks import has_block
from app.llm.service import DISABLE_THINKING_EXTRA

#: ⚠️ 从本文件反推仓库根，**不写死绝对路径**（写死则换机器 / CI 上找不到，而找不到的
#: 表现是「这组用例消失了」）。⚠️ 读不到会直接 `FileNotFoundError`，**刻意不写
#: `pytest.skip`** —— 跳过的断言和没有的断言在报告上长得一样。
FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "frontend" / "src" / "test" / "fixtures" / "follow-up-parse.json"
)
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
CASES = FIXTURE["cases"]


def block(type_name: str, inner: str) -> str:
    """拼一个语法正确的 `limo-block` 块（多数用例都要它）。"""
    return f'<!-- limo-block:v1 type="{type_name}" -->\n{inner}\n<!-- /limo-block -->'


# ═══════════════════════════════════════════════════════════════
# LLMService 替身
# ═══════════════════════════════════════════════════════════════

class FakeLLM:
    """只暴露 `generate_followups` 用到的三样：`model` / `client.chat.completions.create` /
    `aclose`。

    `aclose` 是本替身存在的第二个理由（第一个是可控的回复）：`followup.py` 复用的是
    **对话的** llm 实例，绝不该关它 —— 但「没关」是个**没有症状**的正确行为，只有
    记数才能钉住（照抄 `compress.py` 的 `finally: await llm.aclose()` 就会关掉对话的
    连接池，而对话那边 `chat.py` 的 finally 还会再关一次，两处都看不出异常）。
    """

    def __init__(self, reply="- 第一个追问是什么\n- 第二个追问是什么",
                 raises: Exception | None = None, delay: float = 0.0,
                 supports_reasoning: bool = True):
        self.model = "fake-model"
        #: 能力位 `reasoning`（= 认 `reasoning_effort`）。默认 True 走「有档位」那条路
        #: （内置预设里带它的是 DeepSeek V4 两兄弟），需要时用参数造另一条。
        self.supports_reasoning = supports_reasoning
        self._reply = reply
        self._raises = raises
        self._delay = delay
        self.calls: list[dict] = []
        self.aclose_called = 0
        self.client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=self._create)))

    async def _create(self, **kw):
        self.calls.append(kw)
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._raises is not None:
            raise self._raises
        content = self._reply(kw) if callable(self._reply) else self._reply
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    async def aclose(self) -> None:
        self.aclose_called += 1


def prompt_sent_to(llm: FakeLLM) -> str:
    """取替身收到的那条 prompt（唯一一条 user 消息）。"""
    return llm.calls[0]["messages"][0]["content"]


# ═══════════════════════════════════════════════════════════════
# 1. prompt 的四条关键条款（含对照实验）
# ═══════════════════════════════════════════════════════════════

#: 每条都是「写出来才有价值」的**行为约束**，不是文风偏好：
#: · 20 字上限 —— 追问要能一眼扫过去（取自 Dify 的中文实测值）
#: · 不预设结论 —— 诱导性追问把模型埋的隐含前提变成用户接受的既定事实（本项目新增，
#:   现有 `formats.md` 只写了「不复述不客套」，没有反诱导）
#: · 语言跟随 —— 回答是英文而追问是中文的卡片读起来是坏的
#: · 反凑数 —— 与「数量放宽到 4～6」直接配套：放宽上限最容易换来的就是编问题，
#:   而凑出来的问题恰恰是最该避免的那类（复述性 / 客套性 / 预设结论）
KEY_CLAUSES = ("20 个字", "不预设结论", "相同的语言", "凑数")


def _missing_clauses(prompt: str) -> list[str]:
    """判据抽成函数 —— 好让对照实验能对「删掉条款的 prompt」跑**同一个判据**。"""
    return [kw for kw in KEY_CLAUSES if kw not in prompt]


def test_prompt_含四条关键条款():
    assert _missing_clauses(FOLLOWUP_PROMPT) == []


@pytest.mark.parametrize("kw", KEY_CLAUSES)
def test_对照实验_删掉任一条款判据立刻红(kw):
    """⚠️ **本条是「上面那条断言不是恒真的」的证明**：逐条删掉关键词，判据必须逐条报出它。

    没有这一条的话，上面那个 `== []` 在「判据写错了（比如 KEY_CLAUSES 里是空串）」
    时也会绿 —— 而那种绿，与「prompt 写得好」在报告上长得一样。
    """
    assert _missing_clauses(FOLLOWUP_PROMPT.replace(kw, "")) == [kw]


def test_prompt_要求每行以短横开头():
    """输出格式是**解析层的输入契约**（`LIST_ITEM_RE` 只认列表行）。

    模型改用编号或 JSON 都会让 `extract_followup_items` 抽不到东西 → 补调白花一次调用
    且用户拿不到卡（虽然会推 `error`，但那是失败，不是我们要的）。
    """
    assert "`- ` 开头" in FOLLOWUP_PROMPT


# ═══════════════════════════════════════════════════════════════
# 2. 抽取层：跑跨语言共享 fixture（与前端 chatFollowUp.test.tsx 配对）
# ═══════════════════════════════════════════════════════════════

def test_fixture_可读且用例没被删空():
    """防「用例被删空后整组测试静默变成空跑」—— 参数化到空列表时 pytest 报 0 个用例
    （exit 5），看起来像失败也可能被忽略。"""
    assert FIXTURE_PATH.is_file(), f"共享 fixture 不存在：{FIXTURE_PATH}"
    assert len(CASES) >= 8, f"用例太少（{len(CASES)}），可能被误删"


def test_fixture_项文本不触碰服务端特有的过滤层():
    """⚠️ **机器守卫，防的是「fixture 慢慢变成在测另一层」**。

    `extract_followup_items` 在共有抽取层之上还叠了 `MIN_QUESTION_CHARS` 过滤（服务端
    特有）。fixture 若用「甲」「二」这类单字占位符，后端会（正确地）把它们全滤掉，
    而期望值里写着它们 —— 本文件当场从「两侧对同一输入给同一批项」变成「后端少给了几项」，
    看起来像 bug 却不是。这条守卫让「塞单字进 fixture」立刻红在**原因**上，而不是等到
    某天后端断言莫名其妙失败。
    """
    for case in CASES:
        for item in case["items"]:
            assert len(item) >= MIN_QUESTION_CHARS, (
                f"用例「{case['name']}」的期望项 {item!r} 短于服务端过滤下限"
                f"（{MIN_QUESTION_CHARS}）—— 该用例已越出本 fixture 能钉的那一层")


def test_用例覆盖到那几类关键行为():
    """名称级守卫：关键行为一条都不能少，否则本文件会「绿着失去意义」。"""
    names = "\n".join(c["name"] for c in CASES)
    for kw in ("无序列表", "有序列表", "非列表行", "围栏", "前导空白", "CRLF", "空数组"):
        assert kw in names, f"fixture 缺少「{kw}」类用例"


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_与前端同形(case):
    """逐字段比对 —— 断言的是「前端那份实现会得到什么」，不是「后端自己觉得对」。

    ⚠️ 这条之所以能成立，靠的是 fixture 项文本一律 ≥ `MIN_QUESTION_CHARS`
    （见上面的长度守卫）：那样服务端特有的三条归一无一条被触发，两侧真的走在同一条路上。
    """
    assert extract_followup_items(case["content"]) == case["items"]


# ═══════════════════════════════════════════════════════════════
# 3. 服务端特有的三条归一（**刻意不进 fixture**）
# ═══════════════════════════════════════════════════════════════

def test_去重_服务端特有():
    """同一问题出现两次只留一个 —— 前端那个解析器不做去重（它同时服务模型自主写的块，
    在那条路上「原样呈现模型写的东西」才是对的）。"""
    got = extract_followup_items("- 这个方案的风险有哪些\n- 这个方案的风险有哪些")
    assert got == ["这个方案的风险有哪些"]


def test_过滤过短项_服务端特有():
    """「还有吗」「继续」这类没有信息量的问题对读者毫无价值。"""
    got = extract_followup_items("- 还有吗\n- 继续\n- 这个方案的风险有哪些")
    assert got == ["这个方案的风险有哪些"]
    assert len("还有吗") < MIN_QUESTION_CHARS  # 自证：它确实短于下限


def test_项文本清洗_剥标记片段并压成单行_服务端特有():
    """模型偶尔把标记片段或换行写进问题里 —— 前端不做清理（它拿到的是模型自己写的东西），
    服务端清的是 **LLM 原始输出**，两处的归一强度刻意不同。"""
    got = extract_followup_items(
        '- 关于 <!-- limo-block:v1 type="x" --> 的这个方案有什么风险')
    assert got == ["关于 的这个方案有什么风险"]
    assert "<!--" not in got[0]


def test_截断_超上限只留前_6_条():
    """8 项进 → 6 项出。⚠️ 上限两侧都有且同值，但**各自持有常量**（见 MAX_QUESTIONS 注释）。"""
    content = "\n".join(f"- 第 {i} 个追问问题是什么" for i in range(1, 9))
    got = extract_followup_items(content)
    assert len(got) == MAX_QUESTIONS
    assert got[0] == "第 1 个追问问题是什么"      # 保序（不是任意 6 个）
    assert got[-1] == "第 6 个追问问题是什么"


# ═══════════════════════════════════════════════════════════════
# 4. digest_answer —— 喂进 prompt 前对回答的截断
# ═══════════════════════════════════════════════════════════════

def test_短回答原样返回():
    assert digest_answer("一段简短的结论。") == "一段简短的结论。"


def test_长回答保头保尾且中间有省略标记():
    """⚠️ **尾部比头部重要** —— 追问接的是回答的**结尾**，为省 token 只留头部是最糟的选择。
    这条断言钉的就是「尾部的量真的保住了」，而不是「函数没崩」。"""
    answer = "头" * 900 + "中" * 5000 + "尾" * 2000
    got = digest_answer(answer, head=800, tail=1500)
    assert got.startswith("头" * 800)
    assert got.endswith("尾" * 1500)
    assert "…" in got
    assert "中" not in got                                  # 中段确实被丢掉
    assert len(got) < len(answer)                            # 确实变短了


def test_长回答里的块标记被剥掉():
    """`limo-block` 是**渲染协议**，模型看了会困惑（且凭空多占预算）。"""
    got = digest_answer(block("key-takeaway", "**核心结论**\n\n正文。"))
    assert "limo-block" not in got
    assert "正文。" in got and "**核心结论**" in got          # 内容一字不丢


# ═══════════════════════════════════════════════════════════════
# 5. has_block（核心公共函数）—— 含对照实验
# ═══════════════════════════════════════════════════════════════

def test_has_block_真块返回真():
    assert has_block(block("follow-up", "- 一个问题是什么"), "follow-up") is True


def test_has_block_只提到这个词不算用了它():
    """⚠️ **对照实验在场**：下面那个 md **确实包含** `follow-up` 这个子串，
    所以「改成子串判定」会让本条当场红 —— 这正是 `chat.py` 的 blocks 统计
    不用子串而走 `parse_blocks` 的理由（正文里讨论本协议时会出现这个词）。"""
    md = "我们用 follow-up 块来承载追问建议，这是它的协议说明。"
    assert has_block(md, "follow-up") is False
    assert "follow-up" in md                     # ← 子串判定在这里会给出 True


def test_has_block_不认识的_type_不算():
    assert has_block(block("follow-up", "- 一个问题是什么"), "card-group") is False


def test_has_block_未闭合的块不算真块():
    """开标记有、闭标记无 —— 降级为普通正文，不是块（与 `parse_blocks` 同源）。"""
    md = '<!-- limo-block:v1 type="follow-up" -->\n- 一个问题是什么'
    assert has_block(md, "follow-up") is False


def test_has_block_围栏里的标记不算():
    """代码块里的 `limo-block` 是**用户内容**，不解析（专用协议的三条不变量之一）。"""
    md = '```\n<!-- limo-block:v1 type="follow-up" -->\n- 一个问题是什么\n```'
    assert has_block(md, "follow-up") is False


# ═══════════════════════════════════════════════════════════════
# 6. has_usable_followup —— 判据必须 ≥ 前端严格度（含对照实验）
# ═══════════════════════════════════════════════════════════════

def test_空壳块不算可用_对照_has_block_认它():
    """⚠️ **这条钉的是「开了开关却一张卡都没有」**。

    模型写了个有标记、块内没有列表的空壳块时：前端 `parseFollowUpItems` 抽不出项 →
    整块退化成普通正文（不渲染卡）。若后端判据只看「有块」→ 跳过补调 →
    **用户开了开关却什么都没有**，而这是**静默**的（看不出是模型没写还是功能坏了）。

    对照实验见本用例的第二段断言：换成 `has_block` 就会认为可用。
    """
    md = block("follow-up", "")
    assert has_usable_followup(md) is False
    assert has_block(md, "follow-up") is True          # ← 判据若止步于此就是缺陷


def test_块内全是段落抽不出项也不算可用():
    """有块、有内容，但内容不是列表 —— 前端同样渲染不出 chip。"""
    md = block("follow-up", "上面回答里说的话，这里再重复一遍。")
    assert has_usable_followup(md) is False


def test_块里抽得出问题就算可用():
    assert has_usable_followup(block("follow-up", "- 这个方案的风险有哪些")) is True


def test_其它类型的块不算追问块():
    md = block("card-group", "- 这个方案的风险有哪些")
    assert has_usable_followup(md) is False


def test_没有块时不算可用_且不误判_提到词():
    assert has_usable_followup("一段普通回答，提到了 follow-up 这个词。") is False


# ═══════════════════════════════════════════════════════════════
# 7. should_attempt —— chat.py 的准入判据（五个条件）
# ═══════════════════════════════════════════════════════════════

def _attempt(answer: str = "一段普通回答。", *, follow_up: bool = True,
             saw_error: bool = False, cancelled: bool = False) -> bool:
    """默认「一切都好」的调用，用例只覆盖自己关心的那一个条件。"""
    return should_attempt(follow_up, saw_error=saw_error,
                          cancelled=cancelled, answer=answer)


def test_全部条件满足时该补调():
    assert _attempt() is True


def test_开关关闭时绝不补调():
    """零回归的**唯一保证**：关掉开关时后端一次额外调用都不发、一次都不算。"""
    assert should_attempt(False, saw_error=False, cancelled=False,
                          answer="一段普通回答。") is False


@pytest.mark.parametrize("kw,why", [
    ({"saw_error": True}, "出错的回答上追追问没有意义"),
    ({"cancelled": True}, "用户按了停止，不该再花一次调用"),
])
def test_错误或取消时补调(kw, why):
    assert _attempt(**kw) is False, why


def test_空回答不补调():
    """取消在第一个 token 之前 —— 没有内容可追问。"""
    assert _attempt(answer="   \n  ") is False


def test_已有可用块时不补调():
    """⚠️ **这条同时是「不出现两张追问卡」的证明**：模型自己写好了块时，
    `should_attempt` 返回 False，`chat.py` 那两行据此**根本不进 if** —— 补调压根不发生。

    ⚠️ 直调真实实现而非在测试里复刻那五个条件：调用点住在 `chat.py::sse_stream`
    这个嵌套 async generator 里，测试够不到，而复刻的表达式正是「改了一处、另一处还在
    旧假设上」的来源（同 `chat.py::_append_refs` 的纪律）。
    """
    assert _attempt(answer=block("follow-up", "- 这个方案的风险有哪些")) is False


def test_空壳块仍会补调():
    """与上一条成对：**空壳块不算「已有」**，所以补调照发 —— 这正是「开了一定有」的兑现处。"""
    assert _attempt(answer=block("follow-up", "")) is True


# ═══════════════════════════════════════════════════════════════
# 8. generate_followups —— 补调本体
# ═══════════════════════════════════════════════════════════════

def test_补调成功返回问题列表():
    llm = FakeLLM(reply="- 这个方案的风险有哪些\n- 它和现有做法比如何")
    out = asyncio.run(generate_followups(llm, "问什么", "答什么"))
    assert out == ["这个方案的风险有哪些", "它和现有做法比如何"]


def test_补调用的是当前对话模型且不关连接池():
    """⚠️ **`aclose` 那行是最容易照抄错的**（`compress.py` 里它是对的，因为那个模块
    **自己新建**了 llm）。关掉会把对话的连接池一起拆了，而 `chat.py` 的 finally 随后
    还会再关一次 —— 两处都不报错，症状只在长会话里偶发。"""
    llm = FakeLLM()
    asyncio.run(generate_followups(llm, "问什么", "答什么"))
    assert llm.aclose_called == 0
    assert llm.calls[0]["model"] == llm.model


def test_有档位的模型关闭思考且不传档位():
    """有「思考强度」概念的模型（能力位 `reasoning`，内置预设里是 DeepSeek V4 两兄弟）：
    追问**关掉思考**，不是降档。

    ⚠️ **判据断的是形状、不是「传了个 extra_body」**：`thinking={"type":"disabled"}` 与
    `reasoning_effort` 在服务端**互为替代**，同传会被整次请求拒绝 —— 只断「有 extra_body」
    的话，把两样一起塞进去的实现照样绿。

    ⚠️ **身份断言（`is`）而不是相等**：相等在「把字面量照抄一份过来」时同样成立，而照抄
    正是要防的（模式 #54：形状住在 `llm/service.py` 的模块级常量里，`stream()` 与这里
    共用同一份）。**对照实验**：把 `extra_body=thinking_extra(llm)` 换成字面量
    `{"thinking": {"type": "disabled"}}` → 本条红。
    """
    llm = FakeLLM()                       # supports_reasoning=True（默认）
    asyncio.run(generate_followups(llm, "问什么", "答什么"))
    extra = llm.calls[0]["extra_body"]
    assert extra == {"thinking": {"type": "disabled"}}, extra
    assert extra is DISABLE_THINKING_EXTRA, \
        "必须**引用**共用常量（照抄一份字面量会在这里红）"
    assert "reasoning_effort" not in extra, \
        "关闭与档位在服务端互为替代 —— 同传会被整次请求拒绝"


def test_无档位的模型一个字段都不塞():
    """没有「思考强度」概念的模型（qwen / gpt / claude…）：`extra_body` 是 `None`。

    ⚠️ 用户 2026-10-11 定：**有的话搞最低，没有的话走默认**。`None` 逐字等于改造前的
    行为（零回归）。**对照实验**：把 `thinking_extra` 改成无条件返回常量 → 本条红。
    """
    llm = FakeLLM(supports_reasoning=False)
    asyncio.run(generate_followups(llm, "问什么", "答什么"))
    assert llm.calls[0]["extra_body"] is None, \
        "没档位的模型不该被塞任何思考参数（没有的东西不假装有）"


def test_关闭思考的形状在代码里只定义一次():
    """源码守卫（模式 #54）：`{"type": "disabled"}` 只该住在 `llm/service.py`。

    这条路**必须**有守卫而不是只靠上面那条 `is` 断言：`followup.py` 直调 SDK、走不到
    `LLMService.stream()`，所以它「自己拼一份 extra_body」是最自然的写法。而两处形状
    分叉（比如把常量改成 `"none"` 却留下副本）时**两边都不报错** —— 只有形状对不上的
    服务端会拒，且拒绝发生在真实调用里、不是门禁里。
    """
    app_dir = Path(__file__).resolve().parents[1] / "app"
    hits = sorted(
        p.relative_to(app_dir).as_posix()
        for p in app_dir.rglob("*.py")
        if '"type": "disabled"' in p.read_text(encoding="utf-8")
    )
    assert hits == ["llm/service.py"], \
        f"关闭思考的形状只该定义在 llm/service.py；实际出现在 {hits}"


def test_喂进_prompt_的回答已剥掉块标记():
    """补调看到的是**干净正文** —— 标记是渲染协议，对模型是噪音。"""
    llm = FakeLLM()
    answer = block("follow-up", "- 已有问题是什么") + "\n\n正文结论。"
    asyncio.run(generate_followups(llm, "问什么", answer))
    sent = prompt_sent_to(llm)
    assert "limo-block" not in sent
    assert "正文结论。" in sent


def test_补调超时返回_None_且不抛(monkeypatch):
    """⚠️ 独立超时（`FOLLOWUP_TIMEOUT`）的理由：它发生在对话主流程**之后**，共用对话的
    总超时会让用户已经看到 `done` 却要干等几分钟。60s 封顶，这里 patch 成 50ms。"""
    monkeypatch.setattr(followup, "FOLLOWUP_TIMEOUT", 0.05)
    llm = FakeLLM(delay=5.0)
    assert asyncio.run(generate_followups(llm, "问什么", "答什么")) is None


def test_网络异常返回_None_且不抛():
    """吞掉一切失败 —— 「不阻塞对话」第一位，与 `compress_history` 同款。
    ⚠️ 但**失败显式化由调用方承担**（chat.py 推 `error` 字段）：追问是用户开了开关的
    承诺，而压缩只是内部优化。"""
    llm = FakeLLM(raises=RuntimeError("boom"))
    assert asyncio.run(generate_followups(llm, "问什么", "答什么")) is None


def test_模型没按列表格式输出返回_None():
    llm = FakeLLM(reply="好的，我建议你考虑以下几个方面：首先是并发问题……")
    assert asyncio.run(generate_followups(llm, "问什么", "答什么")) is None


def test_取消已置位时不发起调用():
    """⚠️ **断言的是「一次调用都没发」而不是「返回 None」** —— 用户按了停止，
    不该再花一次 token。`cancel_event` 在 `llm.client...create` 之前就被检查。"""
    llm = FakeLLM()
    ev = asyncio.Event()
    ev.set()
    assert asyncio.run(generate_followups(llm, "问什么", "答什么", cancel_event=ev)) is None
    assert llm.calls == []


def test_取消未置位时正常补调():
    """与上一条成对 —— 证明「没被取消」这条路上事件只是个旁观者。"""
    llm = FakeLLM()
    assert asyncio.run(generate_followups(llm, "问什么", "答什么",
                                          cancel_event=asyncio.Event())) is not None


# ═══════════════════════════════════════════════════════════════
# 9. 落盘往返 —— 追问建议的第四个持久化透传点（后端侧）
# ═══════════════════════════════════════════════════════════════

def test_追加助手消息带追问落盘后能读回(tmp_store):
    """⚠️ 这条钉的是 `core/types.py::ChatMessage.to_dict()` —— 它是**显式白名单**，
    漏加一行就是**在写盘那一刻丢掉字段**，而症状（刷新后卡片消失）与前端三个透传点
    漏掉时一模一样，排查时会先怀疑前端。"""
    sessions = SessionManager(tmp_store)
    conv = sessions.load_or_create("", "问题是什么")
    sessions.append_assistant_message(conv["id"], "回答内容。",
                                      follow_ups=["追问一是什么", "追问二是什么"])

    saved = tmp_store.get_conversation(conv["id"])
    assert saved["messages"][0]["follow_ups"] == ["追问一是什么", "追问二是什么"]


def test_没有追问时字段不出现_旧数据形态(tmp_store):
    """零回归的另一半：不开开关时落盘的消息**与改造前逐字相同**（没有空数组、
    没有 null）—— 前端 `follow_ups?.length` 的判据天然接住缺失，而多出的空字段会
    让旧版前端读到 `[]`（无害）但让 diff 噪音化。"""
    sessions = SessionManager(tmp_store)
    conv = sessions.load_or_create("", "问题是什么")
    sessions.append_assistant_message(conv["id"], "回答内容。")

    saved = tmp_store.get_conversation(conv["id"])
    assert "follow_ups" not in saved["messages"][0]


def test_追问补调失败时不落字段(tmp_store):
    """`generate_followups` 返回 `None` 时，消息里不该留一个空数组 —— 前端据此
    「这一轮没有补调」，而 `error` 另走 SSE 事件（瞬时的、不进库）。"""
    sessions = SessionManager(tmp_store)
    conv = sessions.load_or_create("", "问题是什么")
    sessions.append_assistant_message(conv["id"], "回答内容。", follow_ups=None)

    saved = tmp_store.get_conversation(conv["id"])
    assert "follow_ups" not in saved["messages"][0]
    assert saved["messages"][0]["content"] == "回答内容。"
