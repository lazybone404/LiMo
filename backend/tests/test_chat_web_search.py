"""对话侧联网 —— 开关（工具表 + prompt 同源）+ 来源抽取（`sources`）。

两块互不相干的东西放同一个文件，因为它们**服务同一件事**：让「模型有没有联网、
搜到了什么」在两侧都成为**显式**的。

① **开关**：关掉时工具表整组摘掉 `search_web` / `fetch_url`，system prompt 也换成
   离线声明。⚠️ 这两件事**必须同源** —— 只摘工具不改 prompt（或反过来）就是模式 #54
   的典型形态：模型试图调用一个不存在的工具，或者明明能搜却被告知不能。

② **来源抽取**：`tool_result` 新增 `sources: [{title, url}]`。此前 `search_web` 的
   结果 URL **一条通道都没有**（`_extract_refs` 只认库内文档），用户只看得到
   「正在搜索网络: xxx」而看不到搜到了什么。URL 来自公网 ⇒ 必须有 http(s) 白名单。

⚠️ 两处都有**对照实验**（证明判据有区分力）与**接线守卫**（证明调用点真走这条路径）：
后者不可省 —— `chat_stream` 是嵌套 async generator，测试够不到内层表达式，
把 `_build_registry(store, allow_web=allow_web)` 改回 `_build_registry(store)`
上面所有用例**照样全绿**，而线上的开关静默失效。
"""
import re
from pathlib import Path
from urllib.parse import urlparse

from app.routers.chat import _build_registry, _record_tool_start, _record_tool_result
from app.llm.agent import _extract_sources, _safe_http_url
from app.agent.prompts import PromptLoader

_BACKEND = Path(__file__).resolve().parents[1]
_PROMPTS_DIR = _BACKEND / "app" / "agent" / "prompts"


def _names(reg) -> set[str]:
    return {t["function"]["name"] for t in reg.get_defs()}


# ═══════════════════════════════════════════════════════════
# 1. 开关 → 工具表
# ═══════════════════════════════════════════════════════════

def test_关掉联网_整组摘掉两个工具():
    """⚠️ 是**整组**：只摘 `search_web` 会造出「能抓网页但不能搜」这种半开状态。

    store 传 `None` 即可 —— 三个 `_make_*` 只闭包捕获，构造注册表时不调用它。
    """
    names = _names(_build_registry(None, allow_web=False))
    assert "search_web" not in names
    assert "fetch_url" not in names
    # 本地三件套不受影响（关的是「能不能联外网」，不是「能不能查库」）
    assert names == {"search_docs", "lookup_doc", "list_docs"}


def test_打开联网_五个工具全在():
    assert _names(_build_registry(None, allow_web=True)) == {
        "search_docs", "lookup_doc", "list_docs", "search_web", "fetch_url"}


def test_默认参数是打开_零回归():
    """⚠️ **默认 `True` = 改造前的现状**（联网一直可用）。

    与产物侧 `GenerateRequest.web_search: bool = False` **方向相反且都是对的**：
    那边是新增能力（默认关 = 零回归），这边是给已有能力加开关（默认开 = 零回归）。
    判据同一条 —— 「默认值必须等于改造前的行为」。
    """
    assert _names(_build_registry(None)) == _names(_build_registry(None, allow_web=True))


def test_对照_注册表查找确实有区分力():
    """`get_fn` 对没注册的名字返回 None —— 否则上面「不在集合里」的断言是空话。"""
    reg = _build_registry(None, allow_web=False)
    assert reg.get_fn("search_web") is None
    assert reg.get_fn("search_docs") is not None
    assert reg.count == 3


# ═══════════════════════════════════════════════════════════
# 2. 开关 → system prompt（与工具表同源）
# ═══════════════════════════════════════════════════════════

def test_打开联网_prompt_教它用搜索工具():
    sys = PromptLoader().build("", web_search=True)
    assert "search_web" in sys
    assert "fetch_url" in sys
    assert "没有联网能力" not in sys
    # 搜索结果的使用规则（原 `rules.md` §3）随能力一起注入
    assert "标注每条信息的来源" in sys


def test_关闭联网_prompt_声明离线():
    sys = PromptLoader().build("", web_search=False)
    assert "没有联网能力" in sys
    # ⚠️ 关键：**不能出现工具名** —— 那正是「同源漂移」的症状（工具表里没有，
    #    prompt 里还写着「可用」）
    assert "search_web" not in sys
    assert "fetch_url" not in sys


def test_两种模式下_rules_与_formats_都在():
    """联网层是**插入**，不是替换 —— 换掉联网不能连带把硬约束弄丢。"""
    for web in (True, False):
        sys = PromptLoader().build("", web_search=web)
        assert "禁止根据标题猜测文档内容" in sys   # rules.md
        assert "limo-block" in sys                  # formats.md


def test_两种模式下_doc_context_都注入():
    for web in (True, False):
        assert "--- 当前文档 ---" in PromptLoader().build("正文", web_search=web)
        assert "--- 当前文档 ---" not in PromptLoader().build("", web_search=web)


def test_对照_base_与_rules_里不含工具名():
    """⚠️ 源码守卫：联网措辞**只许**住在 `web_tools.md` / `no_web.md` 这一对文件里。

    写回 `base.md` 或 `rules.md` 的后果是**静默的**：那两份文件在两种模式下都注入，
    于是「关掉联网」变成一个只摘工具、不改措辞的半拉子开关 —— 模型照样以为自己能搜。
    上面那条 `test_关闭联网_prompt_声明离线` 会红，但红了之后得回来找根因；这条直接
    钉住根因所在。
    """
    for name in ("base.md", "rules.md"):
        text = (_PROMPTS_DIR / name).read_text(encoding="utf-8")
        assert "search_web" not in text, f"{name} 里出现了联网工具名（应搬去 web_tools.md）"
        assert "fetch_url" not in text, f"{name} 里出现了联网工具名（应搬去 web_tools.md）"


def test_两个联网层文件都存在():
    """`_read` 对缺失文件返回空串 —— 少一个文件时 prompt 会**静默**少一层。"""
    for name in ("web_tools.md", "no_web.md"):
        assert (_PROMPTS_DIR / name).read_text(encoding="utf-8").strip(), f"{name} 是空的"


# ═══════════════════════════════════════════════════════════
# 3. URL 白名单（主防线）
# ═══════════════════════════════════════════════════════════

def test_放行_http_与_https():
    assert _safe_http_url("http://a.com/x") == "http://a.com/x"
    assert _safe_http_url("https://a.com/x?y=1") == "https://a.com/x?y=1"
    assert _safe_http_url("  https://a.com  ") == "https://a.com"   # 两侧空白剥掉
    assert _safe_http_url("HTTPS://A.com") == "HTTPS://A.com"        # scheme 大小写不敏感


def test_拒绝危险协议与畸形串():
    """搜索结果来自公网，URL 内容不可信 —— 直接进 `href` 就是一条 XSS 通道。"""
    for bad in ("javascript:alert(1)", "data:text/html,<script>x</script>",
                "vbscript:x", "ftp://a.com/x", "//a.com/x", "/local/path",
                "a.com", "", "   ", "https://", "https://a b"):
        assert _safe_http_url(bad) == "", f"不该放行：{bad!r}"


def test_超长_URL_拒绝():
    """URL 有长度上限（前端 `isSafeSourceUrl` 没有长度判据，这里兜住）。"""
    assert _safe_http_url("https://a.com/" + "x" * 3000) == ""
    assert _safe_http_url("https://a.com/" + "x" * 100) != ""


def test_非字符串输入不崩():
    for bad in (None, 0, [], {}, object()):
        assert _safe_http_url(bad) == ""


# ═══════════════════════════════════════════════════════════
# 4. `_extract_sources` —— search_web / fetch_url 的来源
# ═══════════════════════════════════════════════════════════

def test_search_web_抽出标题与_url_三种提供方同形状():
    import json
    for provider in ("tavily", "searxng", "duckduckgo"):
        result = json.dumps({"results": [
            {"title": "标题一", "url": "https://a.com/1", "snippet": "x"},
            {"title": "标题二", "url": "https://b.com/2", "snippet": "y"},
        ], "source": provider}, ensure_ascii=False)
        assert _extract_sources("search_web", result) == [
            {"title": "标题一", "url": "https://a.com/1"},
            {"title": "标题二", "url": "https://b.com/2"},
        ]


def test_search_web_丢掉不合规_url_的整条():
    """URL 不合规就**整条丢**（不是留个没链接的标题）—— 标题单独存在无从核查。"""
    import json
    result = json.dumps({"results": [
        {"title": "恶意", "url": "javascript:alert(1)"},
        {"title": "正常", "url": "https://ok.com/x"},
        {"title": "相对路径", "url": "/rel"},
        {"title": "缺 url"},
    ]}, ensure_ascii=False)
    assert _extract_sources("search_web", result) == [{"title": "正常", "url": "https://ok.com/x"}]


def test_search_web_最多五条():
    import json
    result = json.dumps({"results": [
        {"title": f"t{i}", "url": f"https://a.com/{i}"} for i in range(9)]})
    assert len(_extract_sources("search_web", result)) == 5


def test_search_web_容忍坏输入():
    for bad in ("不是 JSON", "", "null", "[]", '{"results": "不是列表"}',
                '{"results": [1, 2, "x"]}'):
        assert _extract_sources("search_web", bad) == []


def test_search_web_标题超长截断():
    import json
    result = json.dumps({"results": [{"title": "长" * 500, "url": "https://a.com"}]})
    assert len(_extract_sources("search_web", result)[0]["title"]) == 200


def test_fetch_url_成功时用它抓的那个_url():
    """`fetch_url` 的返回值是正文（没有 URL），来源要**从入参 args 拿**。"""
    src = _extract_sources("fetch_url", "# 正文\n\n内容…", {"url": "https://a.com/post"})
    assert src == [{"title": "a.com", "url": "https://a.com/post"}]


def test_fetch_url_失败时不给来源():
    """返回 `{"error": ...}` = 正文没拿到，也就没有可展示的来源。"""
    import json
    for result in (json.dumps({"error": "抓取失败: 超时"}, ensure_ascii=False),
                   json.dumps({"error": "URL 安全校验失败"}, ensure_ascii=False)):
        assert _extract_sources("fetch_url", result, {"url": "https://a.com"}) == []


def test_fetch_url_入参_url_不合规时不给来源():
    for bad in ("javascript:alert(1)", "/rel", "", "http://"):
        assert _extract_sources("fetch_url", "正文", {"url": bad}) == []


def test_fetch_url_缺_args_不崩():
    assert _extract_sources("fetch_url", "正文") == []


def test_其他工具不给来源():
    """库内三件套的来源走 `refs`（docId/title），**不走这里**。"""
    for tool in ("search_docs", "lookup_doc", "list_docs", "unknown_tool"):
        assert _extract_sources(tool, '{"results": [{"url": "https://a.com"}]}') == []


def test_对照_sources_与_refs_确实是两条路():
    """⚠️ 判据有区分力：`search_web` **有** sources 而 **没有** refs；
    库内工具反之。两者若被合并成一个列表，前端就分不出「点了跳预览区」还是
    「点了开浏览器」。
    """
    from app.llm.agent import _extract_refs
    web = '{"results": [{"title": "t", "url": "https://a.com"}]}'
    assert _extract_sources("search_web", web) != []
    assert _extract_refs("search_web", web) == []
    docs = '{"found": 1, "results": [{"id": "tr_a", "title": "A"}]}'
    assert _extract_sources("search_docs", docs) == []
    assert _extract_refs("search_docs", docs) == [{"docId": "tr_a", "title": "A"}]


# ═══════════════════════════════════════════════════════════
# 5. 落盘白名单（`tool_calls`）
#
# ⚠️ 此前只存 `tool` + `display`（连 `summary` 都丢），于是「流式完成后落盘的气泡
#    没有摘要」，而重新打开会话从 `toBackendMsg` 拿回来的却带着摘要 ⇒ 同一个东西
#    两种状态。本轮补上 `summary` 与 `sources`。
# ═══════════════════════════════════════════════════════════

def test_start_落盘条目含四个字段_不含_args():
    tcs: list[dict] = []
    _record_tool_start(tcs, {"tool": "search_web", "display": "正在搜索网络: x",
                             "args": {"query": "x"}})
    assert tcs == [{"tool": "search_web", "display": "正在搜索网络: x",
                    "summary": "", "sources": []}]
    assert "args" not in tcs[0], "args 含用户输入，刻意不落盘"


def test_result_回填到对应的那条():
    tcs: list[dict] = []
    _record_tool_start(tcs, {"tool": "search_web", "display": "搜索中"})
    _record_tool_result(tcs, {"tool": "search_web", "summary": "找到 3 条相关结果",
                              "sources": [{"title": "t", "url": "https://a.com"}]})
    assert tcs[0]["summary"] == "找到 3 条相关结果"
    assert tcs[0]["sources"] == [{"title": "t", "url": "https://a.com"}]


def test_同名工具一轮内两次_各归各位():
    """⚠️ 对照实验：把配对改成「总是填最后一条」→ 第一条永远空着、第二条被写两次。"""
    tcs: list[dict] = []
    _record_tool_start(tcs, {"tool": "search_web", "display": "搜 A"})
    _record_tool_start(tcs, {"tool": "search_web", "display": "搜 B"})
    _record_tool_result(tcs, {"tool": "search_web", "summary": "找到 1 条"})
    _record_tool_result(tcs, {"tool": "search_web", "summary": "找到 5 条"})
    assert [t["summary"] for t in tcs] == ["找到 1 条", "找到 5 条"]


def test_多工具一轮内各归各位():
    tcs: list[dict] = []
    for tool, disp in (("search_docs", "查库"), ("search_web", "联网"), ("lookup_doc", "读文档")):
        _record_tool_start(tcs, {"tool": tool, "display": disp})
    _record_tool_result(tcs, {"tool": "search_web", "summary": "找到 2 条相关结果", "sources": []})
    assert [t["summary"] for t in tcs] == ["", "找到 2 条相关结果", ""]


def test_有_start_没_result_不崩():
    """取消 / 报错时 result 可能永远不来 —— 条目留着、摘要空着（不是异常）。"""
    tcs: list[dict] = []
    _record_tool_start(tcs, {"tool": "search_web", "display": "搜索中"})
    assert len(tcs) == 1 and tcs[0]["summary"] == ""


def test_无对应_start_的_result_不追加也不崩():
    tcs: list[dict] = []
    _record_tool_result(tcs, {"tool": "search_web", "summary": "找到 1 条", "sources": []})
    assert tcs == []


def test_重复_result_不覆盖已填的那条():
    """第二次 result 找不到「未填过」的同名条目 → 原地不动（不覆盖、不追加）。"""
    tcs: list[dict] = []
    _record_tool_start(tcs, {"tool": "search_web", "display": "搜索中"})
    _record_tool_result(tcs, {"tool": "search_web", "summary": "第一次", "sources": []})
    _record_tool_result(tcs, {"tool": "search_web", "summary": "第二次", "sources": []})
    assert len(tcs) == 1 and tcs[0]["summary"] == "第一次"


def test_对照_旧写法造的条目确实缺字段():
    """修复前落盘只存 `tool` + `display` —— 那两个键当时**就是**不存在。

    这是「上面那几条断言有区分力」的证据：它们断言的是两个新键在不在，
    而不是「这两个键存在但可能是空串」。
    """
    old: dict = {"tool": "search_web", "display": "搜索中"}   # 修复前的落盘形状

    tcs: list[dict] = []
    _record_tool_start(tcs, {"tool": "search_web", "display": "搜索中"})
    new = tcs[0]

    assert set(new) - set(old) == {"summary", "sources"}
    # 且旧形状在 `_record_tool_result` 的配对判据下面**认不出来**（`tc["summary"]` KeyError）
    # —— 说明「靠 `.get()` 兜底」不是这段代码的契约，条目必须由 `_record_tool_start` 产出。
    try:
        _record_tool_result([old], {"tool": "search_web", "summary": "新结果", "sources": []})
        raise AssertionError("旧形状不该被配对成功")
    except KeyError:
        pass


# ═══════════════════════════════════════════════════════════
# 6. 接线守卫
# ═══════════════════════════════════════════════════════════

def _chat_py() -> str:
    """`chat.py` 源码，剥掉注释（注释里提到某个写法不算真在用）。"""
    text = (_BACKEND / "app" / "routers" / "chat.py").read_text(encoding="utf-8")
    return re.sub(r"^\s*#.*$", "", text, flags=re.M)


def test_接线_开关驱动工具表():
    """⚠️ 纯单测拦不住调用点漂移：`sse_stream` 是嵌套 async generator，
    把 `allow_web=allow_web` 去掉 -> 工具表恒为全量，上面第 1 组**照样全绿**。
    """
    body = _chat_py()
    # ⚠️ 2026-10-29 起这一行多了 `svc=svc, searcher=searcher`（`search_docs` 要接真检索），
    # 故断言从「带右括号的完整调用」改成「同一行还接着别的参数」—— 两者都能证明
    # `allow_web` 真的传下去了，而后一条顺便证明检索依赖也没丢（去掉它们会退回这个形态）。
    assert "_build_registry(store, allow_web=allow_web," in body
    # 判据必须来自请求参数，不能写死
    assert "allow_web = bool(web_search)" in body
    # ⚠️ 不断言「`_build_registry(store)` 不存在」：非流式 `POST /chat` 那处**刻意**
    # 用默认（`allow_web=True` = 零回归，前端根本不用那个端点）。上面那条 `allow_web=`
    # 的在场断言才是「流式那处没漏传」的证据。


def test_接线_开关同时驱动_prompt():
    """⚠️ 这两行**必须在同一个函数里**且用同一个变量 —— 拆成两个来源就是同源漂移
    （工具摘了、prompt 还写着能用）。同一条 `test_接线_开关驱动工具表` 的理由。
    """
    body = _chat_py()
    # ⚠️ 2026-10-20 起这一行多了 `answer_mode=mode`（同一个 `build` 调用点）。
    # 断言贴着**完整调用**写，是因为它的目的是「流式那处没漏传」——
    # 只断言 `web_search=allow_web` 在场的话，将来有人重写这一行仍会漏掉别的参数。
    assert "prompt_loader.build(system_text, web_search=allow_web, answer_mode=mode)" in body


def test_接线_落盘走两个模块级函数():
    body = _chat_py()
    assert "_record_tool_start(tool_calls, event)" in body
    assert "_record_tool_result(tool_calls, event)" in body
    # ⚠️ 全仓只剩**一处** `tool_calls.append(` —— 就在 `_record_tool_start` 函数体里。
    # 新增第二处（比如在 `sse_stream` 里直接 append）= 绕过白名单，退回旧写法。
    assert body.count("tool_calls.append(") == 1


def test_接线_result_仍携带_refs_与_sources():
    """`tool_result` 分支要同时消费两条来源通路：`refs`（库内）与 `sources`（公网）。

    `sources` 由 `_record_tool_result` 内部取；`refs` 由 `_append_refs` 取 ——
    后者若被顺手删掉，引用 chips 会静默消失（既有测试 `test_chat_refs.py` 也钉着它）。
    """
    body = _chat_py()
    assert '_append_refs(refs, seen_ref_ids, event.get("refs"))' in body


def test_接线_两端默认值都是联网开():
    """前端不带参数 / 后端无该参数时都必须是「联网可用」—— 两处默认值同源。"""
    body = _chat_py()
    assert "web_search: int = Query(1)" in body
    # 且必须真的被读进 allow_web（不是声明了没用）
    assert "bool(web_search)" in body
