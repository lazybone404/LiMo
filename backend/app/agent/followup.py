"""追问建议 —— 回答结束后，**独立调一次** LLM 生成「读者接下来最可能想问的问题」。

**为什么是独立调用而不是让模型在主回答里顺带写**：`agent/prompts/formats.md` 早就教了
`follow-up` 块，但那是**有条件的自主行为** —— 遵循率是概率，而「用户开了开关却什么都没
看到」是**静默失败**（分不清「模型没写」还是「功能坏了」），正是本项目「失败显式化」
要消灭的形态。故开关打开时由代码**保证**有一份：模型自己写了可用的块就用它的，
没写就补调一次（业界主流实现 Dify 的 `suggested_questions_after_answer` 同款做法）。

⚠️ **两条来源互斥**：`has_usable_followup` 为真时**根本不调**本模块 —— 否则会出现
两张追问卡。判据是「有块 **且** 块里抽得出问题」，不是「有块」（见 `has_usable_followup`）。

⚠️ **不写进 `msg.content`**：追问是**附属产物**，独立走 SSE 事件 → `ChatMsg.followUps`
→ 会话消息的 `follow_ups`。于是「导出要剥离追问」这一整类问题**从根上不存在** ——
导出只读 `content`（`export.py::export_conversation`），而「被采纳的追问」是普通
user/assistant 消息、天然按正常文章排布。

⚠️ **本模块同时承载产物侧**（2026-10-11）：生成面板勾选「追问卡片」后，
`generate_artifact_followups` 在笔记落盘之后独立调一次。两侧**共用**同一份条款
（`FOLLOWUP_RULES`）与同一套解析 / 超时 / 取消语义（`_run_followup_llm`）——
分家会把「同源」重新变成「跨目录的两份」。差别只有**输入段**：
对话侧是「读者的问题 + 刚才的回答」，产物侧是「笔记主题 + 笔记内容」。
⚠️ 产物侧**没有** `has_usable_followup` 那条准入判据：产物侧刻意不新增 `limo-block`
类型（追问是**用户开关驱动**、不是模型自主判断），勾了就直接调。
"""

from __future__ import annotations

import asyncio
import logging
import re

from app.core.artifact_blocks import FENCE_RE, has_blocks, parse_blocks, strip_blocks
from app.llm import DISABLE_THINKING_EXTRA, LLMService

logger = logging.getLogger(__name__)

#: 追问生成的独立超时。
#:
#: ⚠️ **独立成层**的理由同 `reference_compressor.py::COMPRESS_TIMEOUT`（那个住在
#: `generation/reference_compressor.py`，**不在** `agent/compress.py`）：它发生在对话主
#: 流程**之后**，若共用对话的总超时，模型挂起时用户已经看到 `done` 却要干等几分钟。
#: 60s 是「用户已经可以继续操作」的上限 —— 它只是补一张卡，不是主回答。
FOLLOWUP_TIMEOUT = 60.0

#: 追问条数上限。与前端 `chat-block-content.ts::MAX_FOLLOW_UPS` 同值（**刻意不做常量
#: 同步**：后端给的上限天然就是前端的读取上限，改这里必须同时看那边）。
MAX_QUESTIONS = 6

#: 过短项的下限 —— 「还有吗」「继续」这类没有信息量的问题对读者毫无价值。
#: ⚠️ **仅服务端**：它过滤的是 LLM 原始输出，而模型自主写的块走前端解析（见下方
#: `extract_followup_items` 的纪律说明）。
MIN_QUESTION_CHARS = 4

#: 追问生成失败时给用户看的那句话。
#:
#: ⚠️ **抽成常量而不是在两处各写字面量**：这条文案是**承诺的落点**（用户开了开关，
#: 看不到卡时必须知道是「失败了」而不是「这轮没有」）。两处措辞分叉的后果是同一个
#: 功能给出两种失败说法，且两边看起来都正常（模式 #54 在**文案**上的形态）。
#: 消费者是在同一份 `followup_payload()`（对话 SSE 与「换一批」端点共用）。
FOLLOWUP_FAILED_MSG = "追问建议生成失败"

#: 追问条款 —— **两侧（对话 / 产物）共用的唯一一份**。
#:
#: ⚠️ **必须是一份常量、由两侧 prompt 拼接**：条款里「回答」这个词在产物侧指的是
#: 「笔记」，但第 1 条的「从读者视角出发」两侧都成立、措辞无需改；写成两份的代价是
#: 「改一条要记得改两处」（同源漂移，模式 #54）。本文件顶部那句「共用同一份条款」
#: 指的就是这里。
FOLLOWUP_RULES = """给出 4～6 个追问，全部满足：

1. 从读者视角出发 —— 读者读完上面这段后自然想知道的，不是对回答的复述。
2. 不预设结论。追问里绝不能包含回答没有给出的判断：
   ✗「这个方案的风险主要来自并发吗？」（把「主要来自并发」当成了既定前提）
   ✗「是不是应该优先选第一个方案？」（措辞已在暗示答案）
   ✓「这个方案有哪些可能的失败模式？」
   ✓「如果并发降下来，结论会变吗？」
3. 回答里已经讲过的不要再问。
4. 不要客套（「以上有帮助吗」「还需要我做什么」一律不写）。
5. 每个问题不超过 20 个字，必须是完整问句。
6. 用与回答相同的语言。
7. 几个问题应当方向不同 —— 下钻到细节 / 换个角度比较 / 什么条件下不成立 / 怎么用到别处。
   不要几个都问同一类。
8. 不要为凑数编问题。如果自然的下一步问题只有 3 个，就给 3 个 —— 复述性、客套性、
   显然已答的问题一个都不要写。数量少不是问题，废话才是。

只输出问题本身，一行一个、每行以 `- ` 开头。不要编号、不要解释、不要任何其他文字。"""

#: 对话侧 prompt —— 占位符 `{question}` / `{digest}` 交给 `.format()`。
#: ⚠️ 用 `+ FOLLOWUP_RULES` 拼接而**不是**把条款写进 f-string：条款段里没有占位符，
#: 拼接定型后再 `.format()` 只替换输入段，`{}` 转义问题随之消失。
FOLLOWUP_PROMPT = (
    "你负责在回答结束后，为读者列出「接下来最可能想问的问题」。\n\n"
    "<读者的问题>\n{question}\n</读者的问题>\n\n"
    "<刚才的回答>\n{digest}\n</刚才的回答>\n\n"
    + FOLLOWUP_RULES
)

#: 产物侧 prompt —— 输入段换成「笔记主题 + 笔记内容」。
#: ⚠️ 与上面**唯一**的差别就是这个输入段；条款部分逐字相同（共用 `FOLLOWUP_RULES`）。
ARTIFACT_FOLLOWUP_PROMPT = (
    "读者刚读完一篇笔记，请为他列出「接下来最可能想问的问题」。\n\n"
    "<笔记主题>\n{title}\n</笔记主题>\n\n"
    "<笔记内容>\n{digest}\n</笔记内容>\n\n"
    + FOLLOWUP_RULES
)

#: 列表项：`-` / `*` / `+`，或 `1.` / `1)`，后跟至少一个空白。
#: ⚠️ 与前端 `chat-block-content.ts::LIST_ITEM_RE` **逐字同构**（共享 fixture 钉住）。
LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.+?)\s*$")

#: 项文本里夹带的 HTML 注释片段（模型偶尔把标记写进问题里）。
_MARKER_FRAGMENT_RE = re.compile(r"<!--.*?-->", re.S)


def digest_answer(answer: str, head: int = 800, tail: int = 1500) -> str:
    """回答进 prompt 前的截断 —— 保头保尾、中间省略。

    ⚠️ **尾部比头部重要**：追问接的是回答的**结尾**，为省 token 只留头部是最糟的选择。
    上限按字符（中文 ≈ 1 token/字，1500+800 即约 2.3k token）而非 token 计算 ——
    这里不需要精确，只需要「不把长回答整个塞进去」。
    ⚠️ 先过 `strip_blocks`：`limo-block` 是**渲染协议**标记，模型看了会困惑（且它
    凭空多占预算）。
    """
    text = strip_blocks(answer).strip()
    if len(text) <= head + tail:
        return text
    return f"{text[:head]}\n…\n{text[-tail:]}"


def build_followup_prompt(question: str, answer: str) -> str:
    """组装追问生成的单条 user 消息。"""
    return FOLLOWUP_PROMPT.format(question=question.strip(), digest=digest_answer(answer))


def build_artifact_followup_prompt(title: str, content: str) -> str:
    """组装**产物侧**追问生成的单条 user 消息。

    ⚠️ 截断复用 `digest_answer`（同机制：先 `strip_blocks` 再保头保尾）——笔记与回答
    在这一点上无差别，不为「名字叫 answer」另起一份实现。
    """
    return ARTIFACT_FOLLOWUP_PROMPT.format(
        title=(title or "").strip() or "（无标题）",
        digest=digest_answer(content),
    )


def _clean_item(text: str) -> str:
    """项文本归一 —— 剥 HTML 注释片段 + 空白压成单行。

    ⚠️ **服务端特有**（前端解析器不做）：这里的输入是 LLM 原始输出，问题里混进标记
    片段或换行都会破坏 chip 的观感；而模型自主写的块走前端解析，那条路上「原样呈现
    模型写的东西」才是对的（模型自己就是那个块的作者）。
    """
    return re.sub(r"\s+", " ", _MARKER_FRAGMENT_RE.sub("", text)).strip()


def extract_followup_items(content: str) -> list[str]:
    """从**块内容**里抽追问项。

    ⚠️ **抽取层与前端 `parseFollowUpItems` 逐字同源**（围栏感知 / 列表行 / trim）——
    由共享 fixture `follow-up-parse.json` 钉住。而下面三条**刻意不进 fixture**：

    | 归一 | 前端 `parseFollowUpItems` | 本函数 |
    |---|---|---|
    | 去重 | ✗ | ✓ **服务端特有** |
    | 过滤过短（`MIN_QUESTION_CHARS`） | ✗ | ✓ **服务端特有** |
    | 项文本清洗（剥注释 / 空白压行） | ✗ | ✓ **服务端特有** |
    | 截上限 | ✓ `MAX_FOLLOW_UPS` | ✓ `MAX_QUESTIONS`（**两侧值相同、各自持有**）|

    理由：前端那个解析器**同时服务「模型自主写的块」**，给它加清理等于为一条路改另一条
    路的既有语义（在那条路上「原样呈现模型写的东西」才是对的 —— 模型自己就是作者）。
    两条路的归一强度**刻意不同**是**设计**，不是遗漏。
    ⚠️ 故 fixture 的用例**只覆盖上表的前三列为 ✗ 的那些**（围栏 / 列表行 / trim）；
    一旦把去重或极短项的用例放进去，就等于断言「两侧给同一批项」，与本节自相矛盾。

    返回 `[]` 表示「这个块里没有问题」——调用方（`has_usable_followup`）据此认定
    **不可用**（空壳块 / 纯段落）。
    """
    items: list[str] = []
    seen: set[str] = set()
    in_fence = False
    for raw in re.sub(r"\r\n?", "\n", content).split("\n"):
        if FENCE_RE.match(raw):
            in_fence = not in_fence
            continue
        if in_fence:
            continue                       # 块内代码示例里的 `- 这不是追问`
        m = LIST_ITEM_RE.match(raw)
        if not m:
            continue
        text = _clean_item(m.group(1))
        if len(text) < MIN_QUESTION_CHARS or text in seen:
            continue
        seen.add(text)
        items.append(text)
        if len(items) >= MAX_QUESTIONS:
            break
    return items


def has_usable_followup(md: str) -> bool:
    """正文里是否已有**可用的** `follow-up` 块（追问补调的准入判据）。

    ⚠️ **必须是「有块 **且** 块里抽得出问题」，不是 `has_block(md, "follow-up")`**：
    模型写了个**空壳块**（有标记、块内没有列表）时，前端 `parseFollowUpItems` 抽不出项、
    整块退化成普通正文 —— 若判据只看「有块」，后端会跳过补调，于是**用户开了开关却一张
    卡都没有**（承诺静默失效）。判据比前端**更严格或等价**才安全。

    ⚠️ 这也是一条**单向包含**（后端认为可用 ⇒ 前端必能渲染出 chip），不是「两侧对同一
    输入给同一批项」的双向一致 —— 后者不成立（服务端多做了去重 / 过短 / 截断）。
    """
    if not has_blocks(md):
        return False
    return any(
        seg["kind"] == "block" and seg["type"] == "follow-up"
        and bool(extract_followup_items(seg["content"]))
        for seg in parse_blocks(md)
    )


def should_attempt(follow_up: bool, *, saw_error: bool, cancelled: bool,
                   answer: str) -> bool:
    """本轮是否该补调一次追问 —— `chat.py` 的准入判据。

    五个条件缺一不可：
    1. **用户开了开关**（否则零回归，参数根本不会传上来）；
    2. **没出错**（`error` 事件）—— 出错的回答上追追问没有意义；
    3. **没取消**（`done.cancelled`）—— 用户按了停止，不该再花一次调用；
    4. **回答非空** —— 空回答（取消在第一个 token 前）没什么可追问的；
    5. **模型没自己写出可用的块**（`has_usable_followup`）—— 否则会出现两张追问卡。
    ⚠️ 第 5 条用「可用」而不是「有块」的理由见 `has_usable_followup`。

    ⚠️ 抽成模块级函数是**为了让测试直调真实实现**：调用点在 `chat.py::sse_stream`
    这个嵌套 async generator 里，测试够不到，只能复刻表达式 —— 而复刻的表达式正是
    「改了一处、另一处还在旧假设上」的来源（同 `chat.py::_append_refs` 的纪律）。
    """
    return (follow_up and not saw_error and not cancelled
            and bool(answer.strip()) and not has_usable_followup(answer))


def thinking_extra(llm: LLMService) -> dict | None:
    """追问这一次调用该下发的思考参数 —— **有档位的压到最低，没有的不传**。

    判据是模型能力位 `reasoning`（= 认 `reasoning_effort`；内置预设里只有 DeepSeek V4
    两兄弟带它，见 `core/config.py`）。用户 2026-10-11 定的两条：**有的话搞最低，
    没有的话走默认**。

    ⚠️ 有档位时下发的是**关闭**（`DISABLE_THINKING_EXTRA`）而不是 `reasoning_effort="low"`：
    两者在服务端**互为替代**，而「最低档」的彻底形式就是关闭 —— 分类管道的实测教训是
    `low` 照样可能把整个输出预算烧在推理链上（正文 0 字符，见 `classifier.py` 的
    `CLASSIFY_BATCH_SIZE` 注释）。DeepSeek V4 系两者都认，故「关闭」对它是确定可行的。
    ⚠️ 没档位的模型（qwen / gpt / claude…）**不传**：它们本就没有「思考强度」这个概念，
    塞未知字段通常被忽略，但**没有的东西不该假装有** —— 而且 `None` 逐字等于改造前的
    行为（零回归）。

    ⚠️ **抽成模块级函数是为了让测试直调真实判据**，而不是在测试里复刻
    `DISABLE_THINKING_EXTRA if llm.supports_reasoning else None` 这个表达式
    （同 `should_attempt` 的纪律：复刻的表达式正是「改了一处、另一处还在旧假设上」的来源）。
    """
    return DISABLE_THINKING_EXTRA if llm.supports_reasoning else None


async def _run_followup_llm(llm: LLMService, prompt: str,
                            *, cancel_event: asyncio.Event | None = None) -> list[str] | None:
    """**两侧共用的那一次调用**：发 prompt → 抽项；任何失败都返回 `None`（**永不抛**）。

    ⚠️ **复用调用方的 `llm` 实例、绝不 `aclose()`** —— `compress.py` 里那个
    `finally: await llm.aclose()` 是因为它**自己新建**了一个 llm；这里关了会把对话的
    连接池（或生成的连接池，见 `generate.py` 的 `finally`）一起关掉。最容易照抄错的一行。

    ⚠️ **吞掉一切失败**（超时 / 网络 / 抽不出）——与 `compress_history` 同款，
    「不阻塞主流程」是第一位的。⚠️ 但「**失败显式化**」由调用方承担：`chat.py` 与
    `generate.py` 推 `followup` 事件时带 `error` 字段（追问是用户**开了开关的承诺**）。

    ⚠️ **抽成一份**：两侧的超时 / 取消准入 / 不 aclose / 失败吞掉四处语义必须完全一致，
    各写一遍就是「改了一处、另一处还在旧假设上」（模式 #54）。

    ⚠️ **压低思考**（`thinking_extra`）：追问是「把读过的内容列成几个问题」——纯生成、
    **推理无产出**，而代价是双份的：token 预算与**首 token 的等待**（用户刚看到 `done`，
    这张卡越晚到越像卡住）。档位怎么选、为什么是「关闭」而不是 `"low"`、为什么没档位的
    模型不传 —— 全在 `thinking_extra` 的 docstring 里（**唯一一份**，别在这里再解释一遍）。
    ⚠️ 形状**引共用常量、不在这里另写一份**（模式 #54）—— 本函数直调 SDK、走不到
    `LLMService.stream()`，那里 `disable_thinking=True` 的机制够不着它。⚠️ 关闭与
    `reasoning_effort` 在服务端**互为替代**，故这里**绝不能**再传档位。
    """
    if cancel_event is not None and cancel_event.is_set():
        return None
    try:
        resp = await asyncio.wait_for(
            llm.client.chat.completions.create(
                model=llm.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                extra_body=thinking_extra(llm),
            ),
            timeout=FOLLOWUP_TIMEOUT,
        )
        content = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.warning("追问建议生成失败，跳过: %s", str(e)[:120])
        return None
    items = extract_followup_items(content)
    if not items:
        logger.warning("追问建议为空（模型未按列表格式输出），跳过")
        return None
    return items


async def generate_followups(llm: LLMService, question: str, answer: str,
                             *, cancel_event: asyncio.Event | None = None) -> list[str] | None:
    """**对话侧**补调一次 LLM 产出追问（薄封装，语义全在 `_run_followup_llm`）。"""
    return await _run_followup_llm(
        llm, build_followup_prompt(question, answer), cancel_event=cancel_event)


async def generate_artifact_followups(llm: LLMService, title: str, content: str,
                                      *, cancel_event: asyncio.Event | None = None) -> list[str] | None:
    """**产物侧**补调一次 LLM 产出追问（笔记落盘后由 `generate.py` 调用）。

    ⚠️ 与对话侧**没有**准入判据上的对应物：产物侧刻意不新增 `limo-block` 类型
    （追问是用户开关驱动、不是模型自主判断），故不存在「模型自己写了可用的块」这条
    需要互斥的路径 —— 要不要调由调用方的 `_want_followup`（勾选项）决定。
    """
    return await _run_followup_llm(
        llm, build_artifact_followup_prompt(title, content), cancel_event=cancel_event)


def followup_payload(questions: list[str] | None) -> dict[str, object]:
    """一批追问的**对外载荷** —— SSE 的 `followup` 事件与「换一批」端点**共用这一份**。

    ⚠️ 形状里 `type` 恒在：SSE 侧它是事件判别字段（前端 `parseSSEStream` 按它分派），
    HTTP 侧是冗余但无害的多余键。**不为此拆成两个函数** —— 拆开的直接后果是
    「什么算失败、失败给哪句话」出现第二处判据，而两个消费点渲染的是**同一张卡**
    （模式 #54）。

    ⚠️ `error` 的判据是 `not questions`（`None` 与空列表同为失败）：`_run_followup_llm`
    永不返回空列表，但真返回了也必须报失败 —— 「开关开了却没有卡」正是要显式化的现象。
    """
    return {
        "type": "followup",
        "questions": questions or [],
        "error": None if questions else FOLLOWUP_FAILED_MSG,
    }
