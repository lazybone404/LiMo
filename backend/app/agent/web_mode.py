"""联网的三种形态 —— 一轮对话只有一种，判据只写一次。

| 形态 | 模型手里的工具 | prompt 层 | 本地工具表 |
|---|---|---|---|
| `off` | 无 | `no_web.md` | 只注册库内三件套 |
| `local` | `search_web` / `fetch_url` | `web_tools.md` | 库内三件套 + 联网两个 |
| `server` | DeepSeek 内置服务端工具 | `deep_web.md` | **一个本地工具都不注册** |

## 为什么要有这个模块

深度联网不是「联网之外的第二个开关」，它是**联网的第三种形态**：同一件事，
执行位置从本机换到了服务端。把它建模成 `(web_search, deep_web)` 两个布尔量时，
`deep_web=1, web_search=0` 这种没有意义的组合在协议上是合法的，于是防御散成了三处：
前端 `deepWeb && webSearch`、路由 `bool(deep_web) and allow_web`、prompt 的三选一。
三处里漏掉任何一处，表现都是「按钮亮着，但这一轮没有联网」。

收成一个三态对象之后，调用方只问一次「这一轮是哪种形态」，然后照着它的三个
布尔量去做 —— 工具表、prompt 层、历史消息、SSE warning 全部由同一个对象驱动。

⚠️ **执行方式不可能合并**：本地形态是「模型调工具 → 我们执行 → 结果回填」，
服务端形态是「模型只表达意图，DeepSeek 自己执行」。协议不同，合并会造出一个
两边都不像的东西。这里统一的是**判定**，不是执行。
"""

from dataclasses import dataclass

from app.core.deepseek_api import supports_deepseek_builtin


@dataclass(frozen=True)
class WebMode:
    """一轮对话里「联网」的形态。三选一，不存在第四种，也不存在组合。"""

    key: str            # "off" / "local" / "server" —— 日志与测试用
    local_tools: bool   # 是否注册本地工具表（含库内三件套）
    net_tools: bool     # 本地工具表里是否含 search_web / fetch_url
    server_tools: bool  # 是否用服务端内置工具
    note: str = ""      # 与用户所选不一致时的说明（推 `warning` SSE 事件）

    @property
    def allow_web(self) -> bool:
        """这一轮模型有没有联网能力（两种联网形态都为真）—— 喂 prompt 的联网层。"""
        return self.key != "off"


#: 三种形态的常量。`note` 只在 `resolve_web_mode` 需要解释时才会有值。
OFF = WebMode("off", local_tools=True, net_tools=False, server_tools=False)
LOCAL = WebMode("local", local_tools=True, net_tools=True, server_tools=False)
SERVER = WebMode("server", local_tools=False, net_tools=False, server_tools=True)


def resolve_web_mode(*, allow_web: bool, want_deep: bool,
                     provider: str, model: str) -> WebMode:
    """本轮该用哪种形态。`note` 非空 = 用户要的那种没给成，要告诉用户。

    ⚠️ 顺序即语义：先判「联网关没关」，再判「要的形态能不能给」。反过来时，
    `web_search=0, deep_web=1` 会得到「模型不支持深度联网」而不是「联网已关闭」，
    而后者才是用户能操作的那一条。
    """
    if not allow_web:
        if want_deep:
            return WebMode("off", local_tools=True, net_tools=False,
                           server_tools=False,
                           note="联网已关闭，深度联网不可用，已按普通对话回答")
        return OFF
    if want_deep:
        if supports_deepseek_builtin(provider, model):
            return SERVER
        return WebMode("local", local_tools=True, net_tools=True,
                       server_tools=False,
                       note="当前对话模型不支持深度联网，已按普通联网模式回答")
    return LOCAL
