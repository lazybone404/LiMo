"""对话侧「回答依据」—— 严格 / 发散 / 自由 三档（`answer_mode`）。

用户原话：「加个全局开关来确定它是否严格遵循还是发散」，并补充「就顺便问问的
那些……似乎需要极致的发散」。于是是三档而不是布尔：

| 档 | 档名（用户看到） | 库里没有时怎么办 |
|---|---|---|
| `strict`（默认） | 只用知识库 | 说「没找到」，不许用常识补 |
| `open` | 允许发散 | 可以补充，但必须标明哪句是补充、文档优先 |
| `free` | 自由对话 | 按自己的知识正常答，不要求先查库 |

⚠️ 要防的失效（全是静默的）：

  A. 三档里有两档注入了**同一份**文案 → 用户切换了开关，产物上零变化（`strict`
     与 `open` 在前几轮可能长得一模一样，这正是「选了却看不出」的形态）。
  B. 非法值静默退回 `strict` → 用户开了发散，模型却仍按严格跑，零报错。
  C. 参数在偏离默认时**没发出去**（前端）→ 后端永远收到默认值。这条由
     `frontend/src/test/chatAnswerMode.test.tsx` 钉住，这里只钉后端入口。
  D. `rules.md` 里残留「无来源不说话」之类的严格专属措辞 → 三档同时收到它，
     等于开关只关了一半（同 `web_tools.md` / `no_web.md` 那组守卫的道理）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.agent.prompts import PromptLoader
from app.core.errors import AppError
from app.routers.chat import _answer_mode

_BACKEND = Path(__file__).resolve().parents[1]
_PROMPTS_DIR = _BACKEND / "app" / "agent" / "prompts"
_CHAT_PY = _BACKEND / "app" / "routers" / "chat.py"


# ═══════════════════════════════════════════════════════════
# 1. 三档各自注入不同的文案
# ═══════════════════════════════════════════════════════════

def test_默认档是严格_且等于改造前的行为():
    """⚠️ 默认必须是 `strict`：改造前 `rules.md` 就是「无来源不说话」。
    与 `web_search` 默认开一样 —— 「给已有行为加开关」⇒ 默认值等于改造前。"""
    loader = PromptLoader()
    assert loader.build() == loader.build(answer_mode="strict")
    assert "只依据知识库中已导入的文档" in loader.build()
    assert "允许你在知识库之外补充" not in loader.build()
    assert "自由对话" not in loader.build()


def test_发散档_允许补充且文档优先():
    sys_text = PromptLoader().build(answer_mode="open")
    assert "允许你在知识库之外补充" in sys_text
    assert "文档优先" in sys_text
    # 反向：严格档那句「不要用常识补全」不该出现
    assert "不要用你的常识" not in sys_text


def test_自由档_不要求先查库():
    sys_text = PromptLoader().build(answer_mode="free")
    assert "不需要围绕知识库" in sys_text
    assert "不要求先去查库" in sys_text
    assert "允许你在知识库之外补充" not in sys_text


def test_三档的产出两两不等_有区分力():
    """对照实验：三份 scope 文件若被写成同一段话（比如复制粘贴忘了改），
    上面那些 `in` 断言会同时成立或同时失败 —— 这一条保证「切换真的有差别」。"""
    a, b, c = (PromptLoader().build(answer_mode=m) for m in ("strict", "open", "free"))
    assert a != b and b != c and a != c


# ═══════════════════════════════════════════════════════════
# 2. 共用层不受影响
# ═══════════════════════════════════════════════════════════

def test_三档都保留硬约束与格式规范():
    """scope 是**插入**不是替换 —— 换档不能把「禁止编造」或 `limo-block` 弄丢。"""
    for m in ("strict", "open", "free"):
        sys_text = PromptLoader().build("", answer_mode=m)
        assert "禁止根据标题猜测文档内容" in sys_text
        assert "禁止编造数据、日期、人名、引用" in sys_text
        assert "limo-block" in sys_text


def test_三档都注入_doc_context():
    for m in ("strict", "open", "free"):
        assert "--- 当前文档 ---" in PromptLoader().build("正文", answer_mode=m)
        assert "--- 当前文档 ---" not in PromptLoader().build("", answer_mode=m)


def test_联网开关与回答依据正交():
    """两个开关是可叠加的（四种组合各自成立）—— 否则「并发散又断网」表达不了。"""
    for web in (True, False):
        for m in ("strict", "open", "free"):
            sys_text = PromptLoader().build("", web_search=web, answer_mode=m)
            assert ("search_web" in sys_text) == web
            assert sys_text == PromptLoader().build("", web_search=web, answer_mode=m)


def test_对照_base_与_rules_里不含任何一档的专属措辞():
    """⚠️ 源码守卫（同 `test_chat_web_search.py::test_对照_base_与_rules_里不含工具名`）：
    `base.md` / `rules.md` 是**三档都注入**的，写进那里的档位专属措辞等于
    「选了自由档，模型同时还被告知只用知识库」—— 开关只关了一半，而症状是
    「模型偶尔遵守偶尔不遵守」，没人能定位。

    ⚠️ `rules.md` 第 1 条的标题是「引用文档时」而不是「文档存在时」：后者的含义是
    「先查库」，与 `free` 档正面冲突（本轮从「文档存在时」改过来，这条守卫顺带钉住它）。
    """
    for name in ("base.md", "rules.md"):
        text = (_PROMPTS_DIR / name).read_text(encoding="utf-8")
        for phrase in ("无来源不说话", "只用知识库", "不要用你的常识",
                       "文档存在时", "先查再答"):
            assert phrase not in text, f"{name} 里出现了档位专属措辞：{phrase}"


def test_三个_scope_文件都存在且非空():
    """`_read` 对缺失文件返回空串 —— 少一个文件时那一档会**静默**少一层
    （`"\n\n".join` 遇空串只是少一段，不会报错）。"""
    for name in ("scope_strict.md", "scope_open.md", "scope_free.md"):
        assert (_PROMPTS_DIR / name).read_text(encoding="utf-8").strip(), f"{name} 是空的"


# ═══════════════════════════════════════════════════════════
# 3. 入口校验（入口严、读回宽）
# ═══════════════════════════════════════════════════════════

def test_合法值原样返回():
    for m in ("strict", "open", "free"):
        assert _answer_mode(m) == m


@pytest.mark.parametrize("bad", ["", "Strict", "OPEN", "loose", "free ", "none"])
def test_非法值显式报错_不静默退化成严格(bad):
    """⚠️ 静默退回 `strict` 是**零报错**的失效：用户以为开了发散，产物上看不出区别。

    判据同 `normalize_reader_intent` —— 请求入口对非法值报错（不是读历史时的宽容回退）。
    """
    with pytest.raises(AppError) as ei:
        _answer_mode(bad)
    assert ei.value.code == "CHAT_BAD_ANSWER_MODE"


def test_build_遇到未知档也响亮失败():
    """绕过路由直接调 `build` 的调用方（将来的 SDK / 脚本）不能拿到一份
    「悄悄按严格跑」的 prompt。"""
    with pytest.raises(ValueError):
        PromptLoader().build(answer_mode="loose")


# ═══════════════════════════════════════════════════════════
# 4. 接线守卫
# ═══════════════════════════════════════════════════════════

def _chat_body() -> str:
    return re.sub(r"^\s*#.*$", "", _CHAT_PY.read_text(encoding="utf-8"), flags=re.M)


def test_接线_流式端点声明参数并传给_build():
    """⚠️ 纯单测拦不住调用点漂移：`sse_stream` 是嵌套 async generator，
    把 `answer_mode=mode` 去掉 → 上面第 1 组**照样全绿**，而线上开关静默失效。
    同 `test_chat_web_search.py::test_接线_开关同时驱动_prompt` 的理由。"""
    body = _chat_body()
    assert 'answer_mode: str = Query("strict")' in body
    # 校验后的变量（不是裸的 `answer_mode`）—— 也就是「真的校验过」
    assert "mode = _answer_mode(answer_mode)" in body
    assert "answer_mode=mode" in body


def test_接线_非流式端点也传了():
    """`POST /chat` 没有联网开关（已知不对称），但回答依据是 `ChatRequest` 字段、
    一路传到了 `build` —— 那条承诺在它上面**成立**。"""
    body = _chat_body()
    assert 'answer_mode: str = "strict"' in body
    assert "_answer_mode(req.answer_mode)" in body


# ═══════════════════════════════════════════════════════════
# 5. 两处枚举必须键集相等（B1）
# ═══════════════════════════════════════════════════════════

def test_枚举与_prompt_层_scope_键集相等():
    """`routers/chat.py::_ANSWER_MODES` 与 `PromptLoader.scopes` 是**同一份三档**的
    两处落点：前者决定请求收不收，后者决定加载哪份 `scope_*.md`。

    只改一边的两种失效形态都不在正常路径上：模板新加了而枚举没加 → 请求 400
    （用户选不到那一档）；枚举加了而模板没加 → `scopes[mode]` 取到 `None`，
    在生成 path 上抛错。两者都要等用户真的选那一档才现形。

    ⚠️ 断言走**真实实现** `_check_scope_keys()`（它也是模块导入时的那一次体检），
    不是在这里复刻一遍集合比较 —— 复刻出来的那份绿了只能说明测试自己算对了。
    """
    from app.routers.chat import _ANSWER_MODES, _check_scope_keys

    assert set(_ANSWER_MODES) == set(PromptLoader().scopes)
    _check_scope_keys()  # 正常键集下不抛


def test_键集分叉时体检真的会炸(monkeypatch):
    """对照实验：把枚举砍掉一档 → `_check_scope_keys()` 必须抛。

    没有这条，「体检函数存在」与「体检函数有用」是两件事 —— 常量比对写错了
    （比如拿 `_ANSWER_MODES` 跟自己比）时上面那条照样绿。
    """
    from app.routers import chat as chat_router

    monkeypatch.setattr(chat_router, "_ANSWER_MODES", ("strict", "open"))
    with pytest.raises(RuntimeError):
        chat_router._check_scope_keys()
