"""「换一批」追问端点 —— `POST /api/chat/followups`。

这是追问卡第二条来源（第一条是生成结束时的 SSE 补调）的**重跑入口**。两者渲染的是
同一张卡，故本文件盯的几乎全是**同源**问题：

① **编排共用**：端点必须调 `generate_followups`（准入 / 超时 / 抽项）与
   `followup_payload`（形状 + 失败文案），不能另写一份 —— 另写的直接后果是「什么算
   失败、失败说哪句话」出现第二处判据，两条路径产出的卡开始分叉（模式 #54）。
② **失败语义**：模型生成不出问题（超时 / 抽不出列表项）**不是请求失败** ——
   HTTP 200 + `error` 字段。这一点前端 `chatStore` 依赖（它靠 `questions.length` 判，
   不靠 try/catch）。真正的请求失败只有两条：**空 answer**（调用方错误，前端根本不会发）
   与**缺 API Key**，两者都显式抛 `AppError`。
③ **计费闸门**：入参非法时**不构造 `LLMService`** —— 那是一次计费调用的出口，
   `CLAUDE.md` 的「打开对话框零请求」是同一条纪律在另一处的形态。

⚠️ 端点**刻意无状态**（不碰 `SessionManager`，落库由前端 `rebuildConversationMessages`
承担）。原因见 `routers/chat.py::refresh_followups` 的 docstring —— 一句话是「按消息
定位的两个可用坐标（id / 下标）在各处都不成立」。于是这里的测试也不需要会话夹具。
"""
import asyncio
import inspect
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent import followup as followup_mod
from app.agent.followup import FOLLOWUP_FAILED_MSG
from app.core.errors import AppError
from app.routers import chat
from app.routers.chat import FollowUpRequest, refresh_followups

_FRONTEND_CHAT_API = Path(__file__).resolve().parents[2] / "frontend" / "src" / "api" / "chat.ts"

_ONE_QUESTION = "- 换一批得到的新问题"


# ═══════════════════════════════════════════════════════════════
# LLMService 替身 —— 只暴露端点用到的那几样
# ═══════════════════════════════════════════════════════════════

class FakeLLM:
    """`config` / `model_id` 照单收下并记下来（端点的两处解析都靠它们断言）。

    ⚠️ `aclose` 的记数不是仪式：本端点**自己新建**了 llm，故必须关（与
    `_run_followup_llm` 那条「复用调用方的实例、绝不 aclose」正好相对）。漏关的表现是
    **连接池泄漏** —— 没有异常、没有日志，只有跑久了之后句柄耗尽。
    """

    instances: list["FakeLLM"] = []

    def __init__(self, config, model_id=None, *, reply=_ONE_QUESTION,
                 raises: Exception | None = None, api_key: str = "sk-fake"):
        self.config = config
        self.model_id = model_id
        self.model = "fake-model"
        self.api_key = api_key
        #: 端点会调 `provider_requires_api_key(llm.provider)`（2026-10-29 起 keyless
        #: 厂商要豁免空 key 检查）。真 `LLMService` 一定有它 —— 替身缺了这条，
        #: 「缺 key 报错」这类用例会以 `AttributeError` 失败，看着像被测逻辑坏了。
        self.provider = "dashscope"
        #: 能力位 `reasoning`（= 认 `reasoning_effort`）。设 False 走「不传 `extra_body`」
        #: 那条 —— 本文件不验档位语义（那在 `test_followup.py`），只需要一个确定的分支。
        self.supports_reasoning = False
        self._reply = reply
        self._raises = raises
        self.calls: list[dict] = []
        self.aclose_called = 0
        self.client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=self._create)))
        FakeLLM.instances.append(self)

    async def _create(self, **kw):
        self.calls.append(kw)
        if self._raises is not None:
            raise self._raises
        content = self._reply(kw) if callable(self._reply) else self._reply
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    async def aclose(self) -> None:
        self.aclose_called += 1


@pytest.fixture(autouse=True)
def install_llm(monkeypatch):
    """把端点评到的 `LLMService` 换成替身，返回一个**改应答方式**的安装口。

    默认装上正常返回一条列表项的替身（多数用例只关心形状）；需要另一条分支的用例
    再调一次 `install_llm(reply=…)` / `install_llm(raises=…)` 覆盖 —— 比每个用例自己
    拼 `pytest.MonkeyPatch.context()` 短，也不会漏掉 `instances` 的重置。
    """
    FakeLLM.instances = []

    def install(**kw):
        monkeypatch.setattr(
            chat, "LLMService", lambda cfg, model_id=None: FakeLLM(cfg, model_id, **kw))

    install()
    return install


#: 缺省配置 —— 只为 `scene_models.chat` 的**回落**能验出来，值刻意不等于任何 model_id
_CFG = {"scene_models": {"chat": 7}}


def _call(**kw) -> dict:
    """按 HTTP 层的调用形态打一次端点（body 字段名 == `FollowUpRequest` 的字段名）。"""
    kw.setdefault("answer", "回答了点什么")
    kw.setdefault("question", "问了什么")
    req = FollowUpRequest(**kw)
    return asyncio.run(refresh_followups(req, config=_CFG))


# ═══════════════════════════════════════════════════════════════
# 1. 成功路径
# ═══════════════════════════════════════════════════════════════

def test_成功_返回新一批_且形状与_sse_事件逐字相同():
    out = _call()
    assert out == {"type": "followup", "questions": ["换一批得到的新问题"], "error": None}


def test_成功_prompt_里带着上游的问答():
    """⚠️ 断言的是**它真的用了入参**，不是「prompt 非空」：把 `req.question` 写死成
    空串，卡照样生成得出来（只是与该轮无关），而所有形状断言都绿。"""
    _call(question="地球为什么是圆的", answer="因为引力使它趋于流体静力平衡")
    prompt = FakeLLM.instances[-1].calls[0]["messages"][0]["content"]
    assert "地球为什么是圆的" in prompt
    assert "因为引力使它趋于流体静力平衡" in prompt


def test_用掉的那个实例被关掉_且只关这一次():
    _call()
    assert FakeLLM.instances[-1].aclose_called == 1


def test_提问为空也能跑_只是少一段输入():
    """用户消息可能为空（`chatStore` 向前找不到 user 时传空串）。端点**不因此报错** ——
    空提问的追问仍然有价值（针对回答本身），而报错会把「回答在、提问没了」这种
    可恢复的情况变成死路。准入判据只落在 `answer` 上。"""
    out = _call(question="")
    assert out["questions"] == ["换一批得到的新问题"]


# ═══════════════════════════════════════════════════════════════
# 2. 失败语义 —— 生成不出问题**不是**请求失败
# ═══════════════════════════════════════════════════════════════

def test_抽不出列表项_返回_200_加_error_不抛(install_llm):
    """模型答了一堆散文（没有 `- ` 列表项）→ `_run_followup_llm` 返回 `None`。

    ⚠️ 端点必须把它当**成功响应**发出去：前端拿 `questions: []` + `error` 显示一行
    小字并**保留原有那批**。若这里改成抛异常，前端 `catch` 分支会走 Toast，而
    用户手上那份能用的追问**已经被换掉了** —— 净损失且提示词不搭。
    """
    install_llm(reply="建议你从并发、缓存、幂等三个角度重新看这个问题。")
    out = _call()
    assert out["questions"] == []
    assert out["error"] == FOLLOWUP_FAILED_MSG


def test_模型抛错_同样走_error_不把异常漏出去(install_llm):
    install_llm(raises=RuntimeError("503"))
    out = _call()
    assert out["questions"] == []
    assert out["error"] == FOLLOWUP_FAILED_MSG


def test_对照_error_只在真失败时出现():
    """上一条的对照实验：把回复换成合法列表项，同一断言方向必须**反过来**。

    没有它的话，「`error` 恒等于 `FOLLOWUP_FAILED_MSG`」这种实现也能让上面两条全绿。
    """
    assert _call()["error"] is None


# ═══════════════════════════════════════════════════════════════
# 3. 请求级失败 —— 显式抛，且不发起计费调用
# ═══════════════════════════════════════════════════════════════

def test_空_answer_抛_AppError_且不构造_llm():
    """⚠️ 两条断言同等重要：**抛**（不能静默返回空批次）与**没构造 LLM**
    （构造 = 一次计费调用的出口，这里什么都不该发生）。"""
    with pytest.raises(AppError) as ei:
        _call(answer="   \n  ")
    assert ei.value.code == "CHAT_FOLLOWUP_NO_ANSWER"
    assert ei.value.recoverable is False
    assert FakeLLM.instances == []


def test_缺_api_key_抛_AppError_且不发起请求(install_llm):
    install_llm(api_key="")
    with pytest.raises(AppError) as ei:
        _call()
    assert ei.value.code == "CHAT_NO_API_KEY"
    # 没 key 就不该打出去（打出去只会拿到 401，且日志里多一条误导性的失败）
    assert FakeLLM.instances[-1].calls == []


# ═══════════════════════════════════════════════════════════════
# 4. 模型解析 —— 显式指定 > 场景默认
# ═══════════════════════════════════════════════════════════════

def test_不传_model_id_回落对话场景():
    _call()
    assert FakeLLM.instances[-1].model_id == 7


def test_传了_model_id_就用它():
    _call(model_id=3)
    assert FakeLLM.instances[-1].model_id == 3


# ═══════════════════════════════════════════════════════════════
# 5. 同源守卫 —— 形状与编排只有一份
# ═══════════════════════════════════════════════════════════════

def test_端点复用共用的编排_不另写一份():
    src = inspect.getsource(refresh_followups)
    assert "generate_followups(" in src
    assert "followup_payload(" in src


def test_端点不自己拼形状():
    """`followup_payload` 被绕开时，返回体往往是这样手写的（多一个空格、少一个 `type`）
    —— 前端按 `questions` / `error` 读，手写那版通常在**失败文案**上先分叉。"""
    src = inspect.getsource(refresh_followups)
    assert not re.search(r"""["']questions["']\s*:""", src)
    assert not re.search(r"""["']error["']\s*:""", src)


def test_sse_侧也用同一个载荷函数():
    """两端点产出的必须是**同一张卡** —— 这条钉住「另一侧也没另写一份」。"""
    src = inspect.getsource(chat.chat_stream)
    assert "followup_payload(" in src
    assert not re.search(r"""["']questions["']\s*:""", src)


def test_前端发的字段名与后端入参逐字对齐():
    """跨语言同源（模式 #54）。后端 `FollowUpRequest` 改字段名而前端没跟上 → 4xx 或
    `answer` 变空串，两种症状都是**「换一批」按钮点了没用**，而两边单测各自全绿。"""
    src = _FRONTEND_CHAT_API.read_text(encoding="utf-8")
    i = src.index("export async function refreshFollowUps(")
    body = src[i:src.index("\n}", i)]
    assert "/chat/followups" in body
    for field in ("question", "answer", "model_id"):
        assert field in body, field


def test_失败文案只有一处定义():
    """端点与 SSE 侧共用 `FOLLOWUP_FAILED_MSG`（经由 `followup_payload`）。

    ⚠️ 判据要**同时**看名字与值：只搜名字的话，把常量删了、在 `followup_payload` 里
    硬写同一句话照样满足；只搜值的话，两处各写一份字面量也照样满足。故取
    「载荷函数**引用常量**」+「全库只有一处字面量赋值」两条合起来。
    """
    src = inspect.getsource(followup_mod.followup_payload)
    assert "FOLLOWUP_FAILED_MSG" in src, "载荷函数必须引用常量"
    assert followup_mod.FOLLOWUP_FAILED_MSG not in src, "载荷函数不许硬写字面量"

    followup_src = Path(followup_mod.__file__).read_text(encoding="utf-8")
    assert len(re.findall(r"""FOLLOWUP_FAILED_MSG\s*=\s*["']""", followup_src)) == 1
    assert not re.search(r"""FOLLOWUP_FAILED_MSG\s*=\s*["']""",
                         Path(chat.__file__).read_text(encoding="utf-8"))
