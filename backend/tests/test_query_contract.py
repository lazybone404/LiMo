"""HTTP 契约 == `QueryService` 输出 —— 证明读路径**只有一份**成形逻辑。

存在的理由：查询层做出来就是为了让 HTTP 与 CLI 走同一条路（《CLI 设计约定》§9）。
但「路由调了 service」这种话**代码看起来永远成立**——真正会出事的是「路由调了
service，**又自己加了点东西**」。本模块逐字段比对两侧输出，把那种「多余的加工」
钉死在第 1 行报错，而不是留给用户在生产里发现。

⚠️ **反向的、同样重要的断言**：HTTP 层**该加**的东西不能被搬进 service。目前只有
一处——`assets/xxx` → 绝对 API URL 的改写（CLI 拿绝对 URL 毫无意义）。故第 3 节
成对断言：**HTTP 版是绝对 URL、service 版仍是相对路径**。少了后半条，将来有人
「顺手」把改写挪进 service 时没有任何东西会红。

⚠️ **不进入 `TestClient` 上下文**（= 不跑 startup event），手动注入临时 Store ——
与 `test_folders_api.py` 同一范式，避免碰真实数据目录。

⚠️ **对照实验（每条判据配一个「看起来对、其实错」的实现，均已实测判红）**：

| 替代实现 | 判红处 | 实测失败信息 |
|---|---|---|
| 路由自行截断（`return page.items[:2]`） | 第 1 节 | HTTP 列表与 service 投影必须逐字段相等 |
| `folderId` 真值判断（`folder_id=folderId or None`） | 第 2 节 | 筛选 `{'folderId': ''}` 两侧不一致（HTTP 多出 `tr_c0`） |
| `assets` 改写挪进 `QueryService.project_resource` | 第 3 节 | service 必须保持**相对路径** |
| `GET /api/search` 忽略入参 `limit`、写死默认值 | 第 4 节 | 搜索响应两侧不一致（HTTP 多返回第 3 条） |
| cli 的 `content` 不剥自证头（返回磁盘原串） | 第 8.5 节 | 产物响应必须逐字段等于 `Artifact.to_dict()` |
| `_build` 忽略 `offset`（续读永远回第一页） | 第 8.5 节 | 按 `continuation` 续读拼起来必须还原全文 |
| 某个 cli 端点漏掉顶层 `schema_version` | 第 8.7 节 | 漏掉的那个端点，CLI 只能对它免检 |
| `modes` 加在路由层而非 service（只有前端拿得到） | 第 9 节 | 补过产物位后 HTTP ≠ service |
| 无产物给 `None` 而不是 `[]` | 第 9 节 | 前端靠 `undefined`（未知）与 `[]`（确定没有）分流 |

⚠️ **第 8.5 节的续读判据第一版曾给出假绿灯**（实测确认：撤掉修复也不红）：正文用的是
`"行" * 300` —— 周期性的内容让「忽略 offset、每次都返回第一页」的实现拼出恰好相等的
字符串。判据本身写对了（比拼接结果），是**测试数据**没有区分力。**造这类「续读/分页
拼接」的用例时，内容必须每段可区分**（现用编号 `000001…`），否则断言退化成只比长度。

⚠️ 第 4 节顺带抓到一个**存活了四个月的既有缺陷**：`search.py` 自带
`prefix="/api"`，而 `main.py` 又 `include_router(..., prefix="/api")` → 实际路径是
`/api/api/search`，而前端打的是 `/api/search`。**两侧测试都绿**（前端测试 mock 掉了
`api/search` 模块；后端 `test_search.py` 测的是 `SearchEngine` 而不是 HTTP 路径；
e2e 冒烟从没覆盖过搜索）——本模块是全仓第一个能发现它的东西。修法是删掉
`search.py` 的 prefix（全仓另外 16 个 router 都是裸 `APIRouter()`）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from conftest import cleanup_stores, import_doc, make_store  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.core.search import SearchEngine  # noqa: E402
from app.core.transcript_meta import strip_meta  # noqa: E402
from app.core.types import GenerateMode, MarkdownView, Style  # noqa: E402
from app.generation.provenance import artifact_meta  # noqa: E402
from app.services.query import SCHEMA_VERSION, QueryService  # noqa: E402
from main import app  # noqa: E402

S = make_store()
C = TestClient(app)
_ENG: SearchEngine | None = None


def _wire() -> QueryService:
    """把 `app.state` 指到临时 Store，返回与该 HTTP 应用**同源**的 service。

    两边拿的是同一个 `Store` 与同一个 `SearchEngine` 实例，故差异只可能来自
    「路由层多做了什么」——这正是本模块要测的唯一变量。
    """
    global _ENG
    if _ENG is None:
        _ENG = SearchEngine(str(S.root))
    app.state.store = S
    app.state.search_engine = _ENG
    return QueryService(S, _ENG)


# ═══════════════════════════════════════════════════════════════
# 1. 列表：HTTP 响应逐字段等于 service 投影（含顺序）
# ═══════════════════════════════════════════════════════════════

svc = _wire()
for _i in range(5):
    import_doc(S, f"tr_c{_i}", created_at=f"2026-10-0{_i + 1}T00:00:00")

http_list = C.get("/api/transcripts").json()
svc_list = svc.list_resources(limit=None).items
assert http_list == svc_list, (
    "HTTP 列表与 service 投影必须逐字段相等（含顺序）——差异意味着路由层多加工了一步\n"
    f"HTTP: {[e['id'] for e in http_list]}\nsvc : {[e['id'] for e in svc_list]}"
)
assert len(http_list) == 5

print("PASS: GET /api/transcripts == QueryService.list_resources(limit=None)")

# ═══════════════════════════════════════════════════════════════
# 2. 筛选参数：透传语义（空串 ≠ 缺席）在两侧必须一致
# ═══════════════════════════════════════════════════════════════

fid = S.create_folder("契约")["id"]
S.set_transcripts_folder(["tr_c0"], fid)

for _params in ({}, {"folderId": fid}, {"folderId": ""}, {"sourceType": "webpage"},
                {"sourceType": "pdf"}, {"q": "tr_c1"}, {"tag": "不存在"}):
    http_ids = [e["id"] for e in C.get("/api/transcripts", params=_params).json()]
    svc_ids = [e["id"] for e in svc.list_resources(
        folder_id=_params.get("folderId") if "folderId" in _params else None,
        source_type=_params.get("sourceType"), query=_params.get("q"),
        tag=_params.get("tag"), limit=None,
    ).items]
    assert http_ids == svc_ids, f"筛选 {_params} 两侧不一致: HTTP={http_ids} svc={svc_ids}"

assert [e["id"] for e in C.get("/api/transcripts", params={"folderId": ""}).json()] == \
    ["tr_c4", "tr_c3", "tr_c2", "tr_c1"], "空串必须退化成「只看未分类」，不是「不过滤」"

print("PASS: 筛选参数透传（含 folderId 空串 = 未分类）两侧一致")

# ═══════════════════════════════════════════════════════════════
# 3. 单资源：除 HTTP 层的 assets 改写外逐字段相等（成对断言）
# ═══════════════════════════════════════════════════════════════

import_doc(S, "tr_asset", raw_text="# 标题\n\n![图](assets/x.png)\n")

_http_res = C.get("/api/transcripts/tr_asset").json()
_svc_res = svc.get_resource("tr_asset")

assert _svc_res["rawText"] == "# 标题\n\n![图](assets/x.png)\n", \
    "service 必须保持**相对路径**——绝对 URL 是 HTTP 的表示法，CLI 用它没有意义"
assert _http_res["rawText"] == "# 标题\n\n![图](/api/assets/tr_asset/x.png)\n", \
    "HTTP 层必须改写 assets 相对路径（前端直接渲染这个字段）"
_diff = {
    k: (v, _svc_res.get(k, "<缺失>"))
    for k, v in _http_res.items() if k != "rawText" and _svc_res.get(k, "\0") != v
}
_diff.update({f"<service 独有> {k}": v for k, v in _svc_res.items() if k not in _http_res})
assert not _diff, f"除 rawText 外不得有任何字段差异: {_diff}"

print("PASS: GET /api/transcripts/{id} 与 service 同源（相对路径 vs 绝对 URL 成对）")

# ═══════════════════════════════════════════════════════════════
# 4. 搜索：入参 limit 必须透传，响应逐字段等于 service
# ═══════════════════════════════════════════════════════════════

_ENG.index("tr_c0", "文档 tr_c0", "契约搜索 关键词甲")
_ENG.index("tr_c1", "文档 tr_c1", "契约搜索 关键词乙")
_ENG.index("tr_c2", "文档 tr_c2", "契约搜索 关键词丙")

_http_hits = C.get("/api/search", params={"q": "契约搜索", "limit": 2}).json()
_svc_hits = svc.search_resources("契约搜索", limit=2).items
assert _http_hits == _svc_hits, f"搜索响应两侧不一致: HTTP={_http_hits} svc={_svc_hits}"
assert len(_http_hits) == 2, "limit 必须透传到 FTS（写死默认值的实现会多返回）"

# 空结果与「引擎缺失」是两种不同的东西——前者 200 + 空数组
assert C.get("/api/search", params={"q": "绝无此词xyz"}).json() == []

print("PASS: GET /api/search 与 service 同源、limit 透传")

# ═══════════════════════════════════════════════════════════════
# 5. 文件夹：裸 list、含 count
# ═══════════════════════════════════════════════════════════════

assert C.get("/api/folders").json() == svc.list_folders()
assert C.get("/api/folders").json()[0]["count"] == 1

print("PASS: GET /api/folders == QueryService.list_folders()")

# ═══════════════════════════════════════════════════════════════
# 6. 产物：HTTP 的 content **含**自证头（源码编辑器要看得见），与 service 恒等
# ═══════════════════════════════════════════════════════════════

S.save_markdown(
    MarkdownView(id="md_c", transcript_id="tr_c0", content="# 笔记正文\n",
                 style=Style.DETAILED, mode=GenerateMode.NOTES),
    file_meta=artifact_meta("notes", doc_id="tr_c0", mode="notes", model="test-model"),
)

_r = C.get("/api/markdown/tr_c0", params={"mode": "notes"})
assert _r.status_code == 200, _r.text
_body = _r.json()
assert _body["content"].startswith("<!-- limo-meta "), \
    "源码编辑器需要看到自证头（预览区不渲染 HTML 注释，故对读者无影响）"

_art = svc.get_artifact("tr_c0", "notes", max_chars=None)
assert _body["content"] == _art.raw_content, "HTTP 的 content 必须 == Artifact.raw_content"
assert _body["versions"] and _body["versions"][0]["version"] == 1, _body["versions"]
assert _body["id"] == _art.id and _body["mode"] == "notes"

print("PASS: GET /api/markdown 与 service 同源（含自证头）、版本列表形状不变")

# ═══════════════════════════════════════════════════════════════
# 7. 错误语义：真 404 必须是 404（不能被 recoverable 压成 200）
# ═══════════════════════════════════════════════════════════════

_r404 = C.get("/api/transcripts/tr_nope")
assert _r404.status_code == 404, _r404.status_code
assert _r404.json()["code"] == "QUERY_NOT_FOUND", _r404.json()

_r404b = C.get("/api/markdown/tr_nope", params={"mode": "notes"})
assert _r404b.status_code == 404, _r404b.status_code
# ⚠️ 这个端点刻意用既有的 `MD_NOT_FOUND`（「尚未生成」）而不是 `QUERY_NOT_FOUND`：
# 它经 `build_from_entries` 直接消费**已读出的** index 条目（正是为了避免「版本列表
# 与正文来自两次读盘」），故**不额外查一次文档是否存在**。对不存在的文档报「尚未生成」
# 在措辞上不够精确，但两者的**用户行动完全相同**（去生成），不值得为区分它多读一次盘。
assert _r404b.json()["code"] == "MD_NOT_FOUND", _r404b.json()

print("PASS: 读路径不存在 → 404 + QUERY_NOT_FOUND（非 200 伪成功）")

# ═══════════════════════════════════════════════════════════════
# 8. /api/cli/*：同一 service 的**第二种表示**（形状固定，不按参数切换）
# ═══════════════════════════════════════════════════════════════
# ⚠️ 本节的判据不是「路由调了 service」——那句话在代码里看起来永远成立。会出事的是
# 「调了 service，**又自己加了点东西**」。下面每一组都是**成对**的：既有点给裸 list
# / cli 点给 Page（前端要一口气铺满，CLI 要 next_cursor）；既有点的 content **含**自证头
# （源码编辑器要看得见）/ cli 的 content **剥掉**头（消费者要的是内容）。少了任何一半，
# 将来有人「顺手统一两边形状」时，没有任何东西会红。
#
# ⚠️ 形状**不按参数切换**（不是「传了 cursor 就返回对象」）：那种设计下漏传参数不会报错，
# 调用方静默拿到裸 list、翻页循环读不到 next_cursor 于是正常结束——少拿数据而看起来成功。

# ── 8.1 列表：裸 list ↔ Page 对象（差异只在表示层，数据同源）──
_cli_page = C.get("/api/cli/resources", params={"all": "true"}).json()
# ⚠️ 两侧必须**同一时刻**取：第 1 节的 `http_list` 是导入 5 篇时取的，而第 3 节之后
# 又多了一篇。拿它比等于在比「不同时间的库」，红得毫无意义（假失败）。
_http_bare = C.get("/api/transcripts").json()
assert isinstance(_http_bare, list) and isinstance(_cli_page, dict), \
    "既有点给裸 list、cli 点给 Page —— 形状必须固定，不能按参数切换"
assert _cli_page == svc.list_resources(limit=None).to_dict(), \
    "cli 列表必须逐字段等于 service 的 to_dict()（多加工一步就在这里红）"
assert _cli_page["items"] == _http_bare, "两个端点的条目必须同源（同一 service 投影）"

# ── 8.2 分页：limit 透传、逐页无重叠、并集 == 全集 ──
_p1 = C.get("/api/cli/resources", params={"limit": 2}).json()
assert len(_p1["items"]) == 2, "limit 必须透传（写死默认值的实现会多返回）"
assert _p1["has_more"] is True and _p1["next_cursor"], \
    "还有数据时必须**同时**给 has_more 与 next_cursor（只给其一，翻页循环就少拿）"
_seen = [i["id"] for i in _p1["items"]]
_cur = _p1["next_cursor"]
_rounds = 0
while _cur:
    _pn = C.get("/api/cli/resources", params={"limit": 2, "cursor": _cur}).json()
    _new = [i["id"] for i in _pn["items"]]
    assert _new and not (set(_seen) & set(_new)), \
        f"第 {_rounds + 2} 页为空或与前面重叠 —— 游标判据与排序判据不同源"
    _seen += _new
    _cur = _pn["next_cursor"]
    _rounds += 1
    assert _rounds < 20, "翻页不收敛（游标没前进）"
assert _seen == [e["id"] for e in svc.list_resources(limit=None).items], \
    "逐页的并集（含顺序）必须 == 全集 —— 少拿数据的实现在这里红"

# ── 8.3 单资源：schema_version 平铺 + rawText 保持相对路径（与第 3 节成对）──
_cli_res = C.get("/api/cli/resources/tr_asset").json()
assert _cli_res["schema_version"] == SCHEMA_VERSION, "cli 响应顶层必须平铺 schema_version"
assert _cli_res["rawText"] == "# 标题\n\n![图](assets/x.png)\n", \
    "cli 点必须保持相对路径（绝对 URL 是 HTTP 表示层的职责，见第 3 节）"
assert {k: v for k, v in _cli_res.items() if k != "schema_version"} == _svc_res, \
    "除顶层 schema_version 外必须与 service 输出逐字段相等"

# ── 8.4 产物清单：让调用方不必盲试六个 part ──
_cli_arts = C.get("/api/cli/resources/tr_c0/artifacts").json()
assert _cli_arts == {"schema_version": SCHEMA_VERSION, "docId": "tr_c0",
                     "items": svc.list_artifacts("tr_c0")}, "产物清单响应形状固定"
assert [a["kind"] for a in _cli_arts["items"]] == ["notes"], _cli_arts
assert _cli_arts["items"][0]["storage"] == "markdown", "生成产物与处理产物用 storage 区分"
assert "content" not in _cli_arts["items"][0], "清单是元数据，零内容零读盘"

# ── 8.5 产物正文：剥头（与第 6 节的**含头**成对）+ 截断显式化 ──
_cli_art = C.get("/api/cli/resources/tr_c0/artifacts/notes").json()
assert _cli_art == svc.get_artifact("tr_c0", "notes").to_dict(), \
    "cli 产物响应必须逐字段等于 Artifact.to_dict()"
assert _cli_art["content"] == strip_meta(_body["content"]) == "# 笔记正文\n", \
    "cli 的 content 必须**剥掉**自证头，而第 6 节的既有点**含**头（源码编辑器要看得见）"
assert "rawContent" not in _cli_art, "默认不带载体（它几乎是 content 的两倍体积）"
_cli_raw = C.get("/api/cli/resources/tr_c0/artifacts/notes",
                 params={"include_raw": "true"}).json()
assert _cli_raw["rawContent"] == _body["content"], "include_raw 给的是磁盘原串"
assert strip_meta(_cli_raw["rawContent"]) == _cli_raw["content"], \
    "两个正文域必须恒满足 strip_meta(raw) == content"

# ⚠️ 内容必须**每段可区分**（`"行" * 300` 曾在这里给出假绿灯）：周期性的正文会让
# 「忽略 offset、每次都返回第一页」的实现拼出恰好相等的字符串——续读判据于是失效，
# 而那正是本节唯一会红的判据。用编号：第 N 个 100 字符段的内容各不相同。
LONG = "".join(f"{i:03d}" for i in range(100))
S.save_markdown(
    MarkdownView(id="md_long", transcript_id="tr_c4", content=LONG,
                 style=Style.DETAILED, mode=GenerateMode.NOTES),
    file_meta=artifact_meta("notes", doc_id="tr_c4", mode="notes", model="test-model"),
)
_path = "/api/cli/resources/tr_c4/artifacts/notes"
_t1 = C.get(_path, params={"max_chars": 100}).json()
assert _t1["truncated"] is True and _t1["continuation"] is not None, \
    "超限必须显式标 truncated **并**给出继续获取方式（只标不给出 = 用户拿不到剩下的）"
assert _t1["totalChars"] == 300, "totalChars 是剥头后的正文长度（不是磁盘文件长度）"
assert _t1["continuation"]["next_offset"] == 100 and _t1["continuation"]["remaining_chars"] == 200
# ⚠️ 续读方式**必须真能用**——只断言「给了 continuation」等于没验证（它可能是个死值）
_parts = [_t1["content"]]
while _parts[-1] and len("".join(_parts)) < 300:
    _off = 100 * len(_parts)
    _parts.append(C.get(_path, params={"max_chars": 100, "offset": _off}).json()["content"])
assert "".join(_parts) == LONG, "按 continuation 续读拼起来必须还原全文"
assert C.get(_path, params={"full": "true"}).json()["content"] == LONG, \
    "full=true 必须不截断（且仍是剥头后的正文）"

# ── 8.6 搜索 / 文件夹：与既有端点同源、形状按各自约定 ──
_cli_hits = C.get("/api/cli/search", params={"q": "契约搜索", "limit": 2}).json()
assert _cli_hits == svc.search_resources("契约搜索", limit=2).to_dict()
assert _cli_hits["items"] == _http_hits, "搜索两侧条目同源（第 4 节的成对）"
assert C.get("/api/cli/folders").json() == \
    {"schema_version": SCHEMA_VERSION, "items": svc.list_folders()}

# ── 8.7 schema_version 守卫：**每一条** cli 响应顶层都得有 ──
# 这条单独存在是因为「漏掉一个端点」不会让上面任何一节变红：CLI 对它免检，而免检的
# 正是那条会读错字段的路径。
for _p, _params in [("/api/cli/status", {}), ("/api/cli/resources", {}),
                    ("/api/cli/resources/tr_c0", {}),
                    ("/api/cli/resources/tr_c0/artifacts", {}),
                    ("/api/cli/resources/tr_c0/artifacts/notes", {}),
                    ("/api/cli/search", {"q": "契约搜索"}),
                    ("/api/cli/folders", {})]:
    _resp = C.get(_p, params=_params)
    assert _resp.status_code == 200, (_p, _resp.status_code, _resp.text)
    assert _resp.json().get("schema_version") == SCHEMA_VERSION, \
        f"{_p} 顶层缺 schema_version —— 漏掉的那个端点，CLI 只能对它免检"

print("PASS: /api/cli/* 与 service 同源（Page 形状 / 剥头 / 截断续读 / schema 守卫）")

# ═══════════════════════════════════════════════════════════════
# 9. 列表携带产物位（`modes`）：让前端**先判断再请求**
# ═══════════════════════════════════════════════════════════════

# 背景：在这之前，前端打开任何文档都会盲发 `GET /api/markdown/{id}?mode=notes|polished`
# ——没生成过就是两条 404。前端**已经**把它们静默掉了（`recoverable` 不进 console.error、
# 不弹 toast），但浏览器网络层照打红字（WebView 里 JS 抑制不掉，模式 #49），更要紧的是
# **协议层把「尚未生成」这个正常状态建模成了 HTTP 错误**。产物位本来就在 `index.json` 里
# （`_index["markdowns"][tx_id]`），随列表带出去即可，前端据此零请求。
#
# ⚠️ 前端判据：`modes === undefined` → **未知**（旧后端 / mock 数据）→ 照旧发请求；
# `modes === []` → **确定没有** → 不发。故**空数组与缺键必须能区分**，`None` 更不行
# （`undefined` 与 `null` 在前端都是 falsy，`!modes` 会把两者混为一谈）。

S.save_markdown(  # tr_c0 已有 notes（第 168 行），这里补 polished，顺带钉住排序
    MarkdownView(id="md_c2", transcript_id="tr_c0", content="# 整理版正文\n",
                 style=Style.DETAILED, mode=GenerateMode.POLISHED),
    file_meta=artifact_meta("polished", doc_id="tr_c0", mode="polished", model="test-model"),
)

_rows = {e["id"]: e for e in C.get("/api/transcripts").json()}
assert _rows, "列表为空则本节全部判据退化成恒真 —— 先确认用例有输入"
assert all("modes" in e for e in _rows.values()), \
    f"每一条都必须带 modes 键，缺的是 {[k for k, e in _rows.items() if 'modes' not in e]}"
assert all(isinstance(e["modes"], list) for e in _rows.values()), \
    "modes 必须是数组（缺省给 []，不给 None）"

# ⚠️ **成对判据**：只断言「键存在」会在 `modes` 恒为 `[]` 的实现下给出假绿灯（模式 #52）
# ——而那个实现在前端就是「什么都不请求」，整个修复变成静默失效。
assert _rows["tr_c0"]["modes"] == ["notes", "polished"], _rows["tr_c0"]["modes"]
assert _rows["tr_c4"]["modes"] == ["notes"], _rows["tr_c4"]["modes"]
assert _rows["tr_c2"]["modes"] == [], \
    "无产物必须是**空数组** —— 前端靠 `undefined`（未知）与 `[]`（确定没有）分流"

# 与产物清单**同源**：断「两处的对应关系」而非各自的存在性（模式 #52——只复制不改写
# 与完全没做一模一样，两个各自正确的列表也可能互不相干）
for _id, _e in _rows.items():
    _md_kinds = sorted(a["kind"] for a in svc.list_artifacts(_id) if a["storage"] == "markdown")
    assert _e["modes"] == _md_kinds, f"{_id} 的 modes 与产物清单不同源：{_e['modes']} != {_md_kinds}"

# 补过产物位之后 HTTP 与 service 仍逐字段相等（第 1 节的判据在**有产物**的窗口上重跑）
assert C.get("/api/transcripts").json() == svc.list_resources(limit=None).items, \
    "modes 若加在路由层而不是 service 层，这里必红（CLI 会拿不到它，模式 #61）"

print("PASS: 列表携带产物位（有产物非空 / 无产物空数组 / 与产物清单同源）")

# ═══════════════════════════════════════════════════════════════
# 10. `Style.NONE`（前端「无」）能一路走到产物元数据
# ═══════════════════════════════════════════════════════════════

# 2026-10-05 的真实故障：`_normalize_and_validate_style` 白名单里显式放行了 `"none"`
# （而它当时**不在 `Style` 枚举里**），prompt 也正确地跳过了风格节，最后倒在落盘的
# `Style(style)` 上 —— `ValueError: 'none' is not a valid Style`，**整条生成流失败**
# （用户选「无」= 生成不出任何东西，且日志只有一行 ValueError）。
# 修法是把 `none` 收进枚举（`Style.NONE`），让**校验的合法集 == 下游能构造的集**。
# 本节钉住「构造」之后的**整条往返**：写盘 → 索引 → 读回仍是 `none`。
S.save_markdown(
    MarkdownView(id="md_none", transcript_id="tr_c3", content="# 无风格产物\n",
                 style=Style.NONE, mode=GenerateMode.NOTES),
    file_meta=artifact_meta("notes", doc_id="tr_c3", mode="notes", model="test-model"),
)
_got = C.get("/api/markdown/tr_c3").json()
assert _got["style"] == "none", \
    f"style='none' 必须**原样**存读（不是被改写成 plain/detailed）：{_got.get('style')!r}"
# 读回的字符串要能**再次构造**——编辑保存路径（`routers/markdown_edit.py`）就是这么干的
assert Style(_got["style"]) is Style.NONE, "读回的 style 必须能再次落到枚举上"
assert C.get("/api/transcripts").json() == svc.list_resources(limit=None).items, \
    "加了这个产物之后两侧仍必须逐字段相等"
print("PASS: Style.NONE 落盘往返（写 none → 索引 → 读回 none / 两侧仍同源）")

cleanup_stores()

print("\n全部通过：HTTP 契约 == QueryService 输出"
      "（列表 / 筛选 / 单资源 / 搜索 / 文件夹 / 产物 / 404 / cli 七端点 / 产物位 / none 往返）")
