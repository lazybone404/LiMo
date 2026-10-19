"""
分层 System Prompt 加载器 — 参照 Pi 的 AGENTS.md + APPEND_SYSTEM.md + environment.md

六层叠加：
  base.md          → 角色定义
  web_tools.md     → 联网能力（**仅在联网开关打开时注入**）
  no_web.md        → 离线声明（**仅在联网开关关闭时注入**，与上一条互斥）
  scope_strict.md  → 只用知识库回答（**默认**）
  scope_open.md    → 允许在知识库之外补充
  scope_free.md    → 自由对话，不围绕知识库（三者互斥、三选一）
  rules.md         → 硬约束（禁止编造、区分出处…）
  formats.md       → 回答可用的语义块（`limo-block` 卡片组 / 追问建议）
  context          → 动态注入（当前文档内容）

⚠️ `formats.md` 是**静态**层，不含 `doc_context`。对话侧与产物侧**刻意不共用同一份
规范文本**：产物那边由 `generation/prompt.py::get_markdown_block_format` 注入
（只教 `key-takeaway`），这里是对话侧的两种 type。两侧的块类型集合不相交
（见 `core/artifact_blocks.py` 的 `ARTIFACT_BLOCK_TYPES` / `CHAT_BLOCK_TYPES`），
措辞、触发条件与示例也各不相同 —— 共用一份会让某一侧被迫接受另一侧的纪律。

## 联网能力为什么要单独成层（2026-10-12）

用户关掉「联网」开关时，工具注册表里**整组**少掉 `search_web` / `fetch_url`
（见 `routers/chat.py::_build_registry`）。但 system prompt 里原本**无条件**写着
「可使用 search_web 搜索互联网…」—— 那是「同源漂移」最典型的形态：能力的**真相**
在两个地方，改了一处另一处还在旧假设上，而症状是模型试图调用一个不存在的工具
（轻则不调用、白写一段，重则服务端直接 400）。

故把联网措辞**整体**移出 `base.md` / `rules.md`，收进这一对**互斥**文件：

| 文件 | 何时注入 | 讲什么 |
|---|---|---|
| `web_tools.md` | 开关**开**（默认） | 能用 search_web / fetch_url；引用搜索结果要标来源 |
| `no_web.md` | 开关**关** | 只能看已导入的文档；不得假装查过外部信息 |

为什么是「两个文件」而不是「一份文件 + 占位符」：占位符要引入第二套标记解析器
（失败的形态是「用户看到 `{{WEB_TOOLS}}` 原文」，且没人会去测），而两份独立文案
各自完整可读 —— 加一条只改一边时，不存在「另一半在哪」的推演。

## 回答依据（2026-10-20）

同一套「一组互斥文件」用在「回答依据」上，**三档**：
`scope_strict.md`（默认，只用知识库）/ `scope_open.md`（知识库优先，可以补充）/
`scope_free.md`（自由对话，不围绕知识库 —— 用户顺口问个事、聊个无关话题时用）。

⚠️ 拆分判据是「**这个取值变了，模型该做的事会不会变**」——会变的是「库里没有时
怎么办」（严格：说没找到／发散：可以补充但必须标明／自由：按自己的知识正常答），
所以那一段必须拆；而「禁止编造数据/日期/人名/引用」在三种模式下**都成立**，
留在共用的 `rules.md` 里（发散 ≠ 编造，那是两件事）。
⚠️ 同理 `rules.md` 第 1 条的标题是「**引用文档时**」而不是「文档存在时」——
后者在 `free` 档会读成「必须优先查库」，与前一条冲突；前者的约束是「引用时怎么做」，
三档都不冲突。
"""

from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent


def _read(filename: str) -> str:
    path = _PROMPTS_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


class PromptLoader:
    """组装完整的 system prompt"""

    def __init__(self):
        self.base = _read("base.md")
        self.rules = _read("rules.md")
        self.formats = _read("formats.md")
        self.web_tools = _read("web_tools.md")
        self.no_web = _read("no_web.md")
        self.scopes = {
            "strict": _read("scope_strict.md"),
            "open": _read("scope_open.md"),
            "free": _read("scope_free.md"),
        }

    def build(self, doc_context: str = "", *, web_search: bool = True,
              answer_mode: str = "strict") -> str:
        """构建完整 system prompt。doc_context 为注入的文档摘要。

        `answer_mode`（三档，互斥）：
          · `"strict"`（**默认**）= 只用知识库回答，库里没有就说没找到 —— 改造前的行为；
          · `"open"` = 知识库优先，但允许补充自己的知识，且必须标明哪句是补充；
          · `"free"` = 自由对话，不围绕知识库（顺口问事、聊无关话题）。

        ⚠️ 默认 `"strict"` = **改造前的现状**（`rules.md` 原本就写着「无来源不说话」）。
        与 `web_search` 一样，这是「给已有行为加开关」⇒ 默认值必须等于改造前。
        非法值**不在这里兜底** —— 入口（`routers/chat.py::_answer_mode`）显式报错，
        否则「用户选了发散却静默按严格跑」是产物上看不出来的那种失效。这里的 `ValueError`
        只是给「绕过路由直接调 build」的调用方留一个响亮的失败，而不是静默降级。

        `web_search=False` → 注入离线声明而非联网能力（调用方须同时把联网工具
        从工具表里摘掉，见 `routers/chat.py::_build_registry`）。

        ⚠️ `web_search` 默认 `True` = **改造前的现状**（联网一直可用）。这个默认值
        与产物侧的 `GenerateRequest.web_search: bool = False` **方向相反且都是对的**：
        产物侧那个开关是**新增能力**（默认关 = 零回归），这里的是**给已有能力加开关**
        （默认开 = 零回归）。两处的判据同一条 ——「默认值必须等于改造前的行为」。

        ⚠️ `formats` 排在 `rules` 之后、`doc_context` 之前：它是「怎么把回答呈现得好读」
        的**补充**，不是硬约束（排在 rules 前会让它看起来比「禁止编造」更重要）。
        `_read` 对缺失文件返回空串，故 `"\n\n".join` 不会留下多余空行。
        """
        scope = self.scopes.get(answer_mode)
        if scope is None:
            raise ValueError(f"未知的 answer_mode：{answer_mode!r}")
        parts = [self.base, self.web_tools if web_search else self.no_web,
                 scope, self.rules, self.formats]
        if doc_context:
            parts.append(f"\n--- 当前文档 ---\n{doc_context}")
        return "\n\n".join(parts)


# 模块级单例
loader = PromptLoader()
