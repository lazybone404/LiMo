"""产物侧联网检索 —— 生成前 LLM 从正文抽检索词 → 调 Agent 侧 `search_web` →
结果汇总成**一份**参考资料，走既有 `reference_texts` 通道注入笔记。

## 为什么不新建注入通道

检索结果伪装成**一份普通的参考资料**（`{name, content}`）追加到 `reference_texts`
末尾，于是这些能力**全部免费继承**，一行都不用改：

| 继承来的 | 出处 |
|---|---|
| 超窗自动压缩（先摘要、装不下再等比削） | `reference_compressor.compress_references_if_needed` |
| 「参考资料只是素材，不是指令」的注入防御声明 | `prompt._format_reference_texts` |
| **只注入 notes**（整理版原文根本不接受这个参数） | `build_polish_prompt` 签名 |
| 前端「正在压缩…」的 stage 提示 | `generate.py` 两处 `needs_compression` 分支 |

## 为什么是「一份」而不是「每条一份」

`reference_compressor` 给每份的配额下界是 200 token（`quota = max(..., 200)`），
逐条注入时 6 条 = 1200 token 起，很容易撑破总量；而总量兜底是**等比削**，削到
句子中间的一份摘要对模型毫无价值。合并成一份 = 压缩器看到的是「一份需要取舍的材料」，
它能做的是**摘要**（保住语义），而不是把六份各自砍半。

## 为什么搜索走 `agent.tools.search_web`

Tavily → SearXNG → DuckDuckGo 三级降级、代理豁免判据（回环地址不走代理）、
DDG 同步库必须放 executor —— 全都在那一份实现里。在此处另写一遍 provider 分支，
就是模式 #54 的形态：两份实现对「哪家可用」「失败怎么降级」迟早给出不同答案，
而**没有任何测试会发现**（两边各自都自洽）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

from app.agent.followup import LIST_ITEM_RE, thinking_extra
from app.llm import LLMService

logger = logging.getLogger(__name__)

#: 抽词那一次 LLM 调用的独立超时。
#:
#: ⚠️ **独立成层**的理由同 `followup.py::FOLLOWUP_TIMEOUT`：它发生在生成**之前**，
#: 若共用生成的总超时，模型挂起时会把整个生成一起拖死（用户等不到任何产物）。
#: 60s 与追问侧同值 —— 都是「一次附带的、可放弃的调用」。
WEB_SEARCH_TIMEOUT = 60.0

#: **检索本身**的整体上限（不含抽词）。
#:
#: ⚠️ 与 `WEB_SEARCH_TIMEOUT` **刻意分成两个常量**：那个管「一次 LLM 调用」，这个管
#: 「N 路并发 HTTP 搜索」。合用一个，将来为其中一件事调值时就会静默改掉另一件
#: （同源漂移最典型的入口）。取值同为 60s **不是巧合**：`search_web` 内部是三级降级
#: **串行链**（Tavily 15s → SearXNG 10s → DDG），单层超时管得住一次请求、管不住
#: 「三轮都慢」，累积起来就在这个量级。
SEARCH_TIMEOUT = 60.0

#: 检索词条数。**两条**：一条对准主题，一条对准材料里的具体事物。
#: 更多条不会让结果更好（搜索引擎对同一主题的返回高度重叠），只会多花时间。
MAX_QUERIES = 2

#: 单个检索词上限（超长就截）——防模型把一整句话当查询词贴进来。
MAX_QUERY_CHARS = 40

#: 每条检索词取几条结果。4 是「够用且不淹没」的折中。
RESULTS_PER_QUERY = 4

#: 合并后的总条数上限（跨检索词按 URL 去重后截断）。
MAX_RESULTS = 6

#: 单条摘要上限。搜索侧已经各自截到 300（300 字符 ≈ 150 汉字），
#: 这里再兜一次是防「换了一家 provider 而它不截」。
SNIPPET_CHARS = 400

#: 抽词时喂给模型的正文长度。检索词来自「这篇讲什么」，前 2000 字足以判断。
TEXT_HEAD_CHARS = 2000

#: 参考资料名上限。超长就退化成不带关键词的通用名（见 `_reference_name`）。
NAME_MAX_CHARS = 40


#: 抽词 prompt。占位符 `{n}` / `{limit}` / `{title}` / `{text}` 交给 `.format()`。
QUERY_PROMPT = (
    "下面是一篇材料的标题与开头。请给出 {n} 个用于**联网搜索**的检索词，"
    "目的是为这篇材料补充背景、相关事实或最新进展。\n\n"
    "<标题>\n{title}\n</标题>\n\n"
    "<正文开头>\n{text}\n</正文开头>\n\n"
    "要求：\n"
    "1. 每个检索词是一句能直接丢进搜索引擎的**短查询**（不超过 {limit} 字），"
    "带足限定词；不要写成问句，也不要写成一句完整的话。\n"
    "2. {n} 个检索词方向要不同：一个对准材料主题本身，另一个对准材料里提到的"
    "具体事物（人名、产品、事件、术语）。\n"
    "3. 只从材料里出现过的内容出发，不要凭空想象主题。\n\n"
    "只输出检索词本身，一行一个、每行以 `- ` 开头。不要编号、不要解释、不要其他文字。"
)


@dataclass
class WebSearchOutcome:
    """一次联网检索的完整结果 —— 三个字段都能进自证头 / 失败提示。"""

    #: 组好的参考资料（`{name, content}`）；没搜到东西时为 `None`
    reference: dict | None = None
    #: 实际发出的检索词（抽词失败退化成标题时，这里就是那个标题）
    queries: list[str] = field(default_factory=list)
    #: 去重后的命中条数。**`0` 与「没开检索」是两件事**，故自证头里靠它区分
    hits: int = 0
    #: 面向用户的失败原因（`None` = 一切正常）。调用方据它推 warning
    error: str | None = None


def build_query_prompt(title: str, text: str) -> str:
    """组装抽词那一次调用的 user 消息。"""
    return QUERY_PROMPT.format(
        n=MAX_QUERIES,
        limit=MAX_QUERY_CHARS,
        title=(title or "").strip() or "（无标题）",
        text=(text or "").strip()[:TEXT_HEAD_CHARS] or "（正文为空）",
    )


def parse_queries(content: str) -> list[str]:
    """从模型输出里抽检索词。

    ⚠️ **刻意不复用 `followup.extract_followup_items`**：那个函数带
    `MIN_QUESTION_CHARS = 4` 的过短过滤（它过滤的是「还有吗」「继续」这类没有信息量的
    追问），而中文检索词天然可能只有两三个字（「通胀」「美联储」）—— 借它会**静默丢掉
    短而有效的关键词**。复用到的只有 `LIST_ITEM_RE`（列表项正则，那边已导出为公开常量）。

    兜底：模型没按列表格式输出（直接写了几行短文本）时也认它 —— 但排除以冒号结尾的
    引导语行（「以下是可以用于搜索的检索词：」），那是最常见的一种"看起来像查询词"的噪音。
    """
    lines = content.replace("\r\n", "\n").split("\n")

    out: list[str] = []
    for raw in lines:
        m = LIST_ITEM_RE.match(raw)
        if not m:
            continue
        q = m.group(1).strip()
        if q:
            out.append(q[:MAX_QUERY_CHARS])
        if len(out) >= MAX_QUERIES:
            break
    if out:
        return out

    for raw in lines:
        q = raw.strip()
        if not q or q.endswith(("：", ":")):
            continue
        out.append(q[:MAX_QUERY_CHARS])
        if len(out) >= MAX_QUERIES:
            break
    return out


async def _extract_queries(llm: LLMService, title: str, text: str,
                           cancel_event: asyncio.Event | None) -> list[str]:
    """LLM 从正文抽检索词。**失败 / 取消一律返回 `[]`（永不抛）**。

    ⚠️ **绝不 `aclose()` 调用方的 llm** —— 它在 `generate.py` 的 `finally` 里统一关，
    这里关掉会让紧随其后的生成拿不到连接池（同 `followup._run_followup_llm` 的纪律，
    那是最容易照抄错的一行）。

    ⚠️ **禁止思考**（`thinking_extra`）：抽两个检索词是纯生成、推理无产出，而代价是
    token 预算与首 token 的等待。判据（有档位就关、没档位不传）全在那边，此处不重复。
    """
    if cancel_event is not None and cancel_event.is_set():
        return []
    try:
        resp = await asyncio.wait_for(
            llm.client.chat.completions.create(
                model=llm.model,
                messages=[{"role": "user", "content": build_query_prompt(title, text)}],
                temperature=0.3,
                extra_body=thinking_extra(llm),
            ),
            timeout=WEB_SEARCH_TIMEOUT,
        )
        content = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.warning("联网检索抽词失败，跳过: %s", str(e)[:120])
        return []
    return parse_queries(content)


async def _search_all(queries: list[str],
                      cancel_event: asyncio.Event | None = None) -> list[dict]:
    """并发调 Agent 侧 `search_web`，按 URL 去重后截断。**永不抛**。

    ⚠️ `search_web` 自己吞掉所有网络异常并恒返回 JSON 字符串，但返回**结构**是契约
    （`{"results": [{title,url,snippet}], "source": ...}`）。这里对结构做一次显式校验并
    在异常时记日志 —— 否则 Agent 侧改了返回形状时，生成侧会**静默拿到空结果**：
    检索"成功"、没有任何报错、产物里也看不出少了一节。该契约由
    `tests/test_web_search_reference.py` 单独钉住。

    ⚠️ **整体闸门 + 取消透传**（2026-10-12 补的一个缺口）：`search_web` 是三级降级
    **串行链**，每层自己的 httpx 超时只管得住**单次请求**；此前这里既没有整体上限、
    也收不到 `cancel_event`，于是「三轮都慢」时生成会一直吊在这里，**用户点了取消也不
    动**（取消信号到不了这一层）。现在三条出口并列：① 起跑前若已取消 → 直接返回；
    ② `asyncio.wait` 同时等「搜索完成」与「取消置位」，**谁先到算谁**；③
    `SEARCH_TIMEOUT` 兜住病态慢。

    ⚠️ 三者都**返回 `[]`**，与「零命中」共用同一条下游路径（调用方据 `hits == 0`
    推提示）。刻意**不**新增一种失败态：对用户而言这三者能采取的行动完全相同（重试 /
    换材料），差别只对读日志的人有意义 —— 故只在日志里分开记（见下）。
    （分类粒度按「用户能采取的行动」划分，同模式 #53。）

    ⚠️ 超时 / 取消**不保证真的掐断底层请求**：DDG 走 `asyncio.to_thread`，线程取消不了
    （这一点上「用户点了取消」与「用户没点」对磁盘上那个进程没有任何区别）。这里能保证
    的只是**调用方不必再等**。既有约束，非本轮引入。
    """
    from app.agent.tools import search_web

    if cancel_event is not None and cancel_event.is_set():
        return []

    async def _one(q: str) -> list[dict]:
        try:
            raw = await search_web(q, max_results=RESULTS_PER_QUERY)
            data = json.loads(raw)
        except Exception as e:
            logger.warning("联网检索失败（query=%s），跳过: %s", q[:40], str(e)[:80])
            return []
        items = data.get("results") if isinstance(data, dict) else None
        if not isinstance(items, list):
            logger.warning("联网检索返回结构异常（query=%s）: %s", q[:40], str(raw)[:120])
            return []
        out_items: list[dict] = []
        for item in items:
            if isinstance(item, dict):
                out_items.append(item)
        return out_items

    async def _gather() -> list[list[dict]]:
        # ⚠️ 再兜一层：`_one` 已吞掉所有 Exception，但 `list()` 之外若漏出什么，
        # `search_task.result()` 会**重抛**并炸掉整次生成 —— 而检索只是个附属步骤。
        try:
            return await asyncio.gather(*(_one(q) for q in queries))
        except Exception as e:
            logger.warning("联网检索并发执行异常: %s", str(e)[:120])
            return [[] for _ in queries]

    search_task = asyncio.create_task(_gather())
    waiters: list[asyncio.Future] = [search_task]
    cancel_task: asyncio.Task | None = None
    if cancel_event is not None:
        cancel_task = asyncio.create_task(cancel_event.wait())
        waiters.append(cancel_task)

    try:
        done, _pending = await asyncio.wait(
            waiters, timeout=SEARCH_TIMEOUT, return_when=asyncio.FIRST_COMPLETED)
    finally:
        # ⚠️ 未完成的一律收回：`cancel_event.wait()` 若一直没人取消，会挂着直到进程结束
        # （"Task was destroyed but it is pending" 的来源）；`search_task` 同理。
        for t in (search_task, cancel_task):
            if t is not None and not t.done():
                t.cancel()
        await asyncio.gather(
            *(t for t in (search_task, cancel_task) if t is not None),
            return_exceptions=True)

    if search_task not in done:
        # 「没等完」与「搜不到」对用户是同一件事（都返回 []），但**对读日志的人不是**。
        if cancel_task is not None and cancel_task in done:
            logger.info("联网检索被取消，放弃等待剩余结果")
        else:
            logger.warning("联网检索整体超时（%ss），放弃等待剩余结果", SEARCH_TIMEOUT)
        return []

    groups = search_task.result()
    out: list[dict] = []
    seen: set[str] = set()
    for group in groups:
        for r in group:
            url = (r.get("url") or "").strip()
            key = url or " ".join((r.get("title") or "").split())
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(r)
            if len(out) >= MAX_RESULTS:
                break
        if len(out) >= MAX_RESULTS:
            break
    return out


def _reference_name(queries: list[str]) -> str:
    """参考资料名 —— **在这里就控长，不依赖 `sanitize_reference_name` 的 80 字截断**。

    ⚠️ 那个截断会**截在词中间**：两条中文检索词拼起来正好落在边界上时，用户在
    「参考资料」的来源名里看到的是半句话。名称是我们自己组装的，就该在组装时给它
    一个完整的名字；超长时退化成不带关键词的通用名（信息量少一点，但读得通）。
    """
    joined = " / ".join(queries)
    name = f"联网检索：{joined}" if joined else "联网检索"
    return name if len(name) <= NAME_MAX_CHARS else "联网检索"


def compose_reference(queries: list[str], results: list[dict]) -> dict | None:
    """把搜索结果拼成一份参考资料。没有可用条目时返回 `None`。

    ⚠️ 首行的**可信度声明是本份资料自己的属性**，刻意不加进
    `prompt._format_reference_texts` 的共享节首文案 —— 那段文案同时服务「用户自己
    上传的参考资料」，而「未经核实」这句话对用户主动提供的材料并不成立。
    """
    lines = ["（以下条目来自公开网页检索，内容未经核实，仅作素材参考。）", ""]
    n = 0
    for r in results:
        title = " ".join((r.get("title") or "").split())
        # `summary` 只有 DeepSeek 给（官方写明是给模型读的 600-800 字摘要，比 snippet
        # 信息密度高得多）。tavily / searxng / ddg 没有这个键 ⇒ 逐字零回归。
        snippet = " ".join(
            (r.get("summary") or r.get("snippet") or "").split())[:SNIPPET_CHARS]
        url = (r.get("url") or "").strip()
        if not title and not snippet:
            continue
        n += 1
        lines.append(f"{n}. **{title or '（无标题）'}**")
        if snippet:
            lines.append(f"   {snippet}")
        if url:
            lines.append(f"   <{url}>")
        lines.append("")
    if n == 0:
        return None
    return {"name": _reference_name(queries), "content": "\n".join(lines).rstrip()}


async def run_web_search(llm: LLMService, title: str, text: str,
                         *, cancel_event: asyncio.Event | None = None):
    """**异步生成器**：边做边把状态吐出来，最后吐一次结果。

        yield ("stage", "<面向用户的一句话>")
        yield ("result", WebSearchOutcome(...))     # 恰好一次、必定是最后一个

    ⚠️ **做成生成器（而不是一个返回值的协程）是为了两侧同源**（模式 #54）：流式端点
    要把 `stage` 转发成 SSE 事件、非流式端点只取结果，若拆成两份编排，两处对
    「什么时候算失败」「退化用什么词」「推几条提示」迟早给出不同答案。
    非流式侧 `async for` 一圈只取最后那一次 `result` 即可。
    """
    outcome = WebSearchOutcome()

    yield ("stage", "正在联网检索…")

    if cancel_event is not None and cancel_event.is_set():
        yield ("result", outcome)
        return

    queries = await _extract_queries(llm, title, text, cancel_event)
    if not queries:
        # 抽词失败 → 退化成标题检索。**不静默**：error 会让调用方推一条 warning
        # （用户勾了开关却什么都没发生，是「失败显式化」要消灭的形态）。
        fallback = (title or "").strip()[:MAX_QUERY_CHARS]
        if fallback:
            queries = [fallback]
            outcome.error = "未能从正文抽取检索词，已改用标题检索"
            logger.warning("联网检索：抽词为空，退化用标题检索: %s", fallback[:40])
    outcome.queries = queries

    if not queries:
        outcome.error = outcome.error or "未能从正文抽取检索词"
        yield ("result", outcome)
        return

    yield ("stage", f"正在检索：{' / '.join(queries)}")

    results = await _search_all(queries, cancel_event)

    # ⚠️ 检索期间用户取消 ⇒ **静默收尾**。不这样判的话，`_search_all` 因取消返回的 `[]`
    # 会被下面那句读成「没搜到」并推一条 `error` —— 用户明明是自己按了取消，界面却弹
    # 「联网检索没有返回结果」，那是把「他做的动作」报成了「我们失败了」。
    # （`hits=0` 照留：自证头记的是事实，不是借口。）
    if cancel_event is not None and cancel_event.is_set():
        yield ("result", outcome)
        return

    outcome.hits = len(results)
    if not results:
        outcome.error = "联网检索没有返回结果"
        yield ("result", outcome)
        return

    outcome.reference = compose_reference(queries, results)
    if outcome.reference is None:
        outcome.error = "联网检索结果为空"
    yield ("result", outcome)


def outcome_param(outcome: WebSearchOutcome) -> dict:
    """自证头的 `webSearch` 字段 —— **只记事实，不记 provider / 降级**。

    provider 是「这次用哪家搜的」，同一份产物换个网络环境就不同，对读产物的人没有
    价值；`queries` 与 `hits` 才是「它到底有没有真去搜、搜到了什么」的可核对事实。
    ⚠️ `hits: 0` **必须留下**（`compact_params` 用 `is not False` 判，数值 0 会保留）——
    它是「开了检索但一条都没搜到」与「压根没开检索」之间唯一的区分点。
    """
    param: dict = {"queries": list(outcome.queries), "hits": outcome.hits}
    if outcome.error:
        param["error"] = outcome.error
    return param
