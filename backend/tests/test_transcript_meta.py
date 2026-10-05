"""验证 transcript 出处头编解码 — 往返 / 分隔符冲突 / HTML 注释闭合 / 无头与坏头的区分

本模块是「合成文档重建索引后不丢来源」的唯一防线：index.json 丢失重建时 id 会重推、
sourceType 被写空，来源身份**只能**从 transcript.md 第一行还原。

核心不变量两条：
1. **无头 ≠ 坏头** —— 混淆会让所有历史文档被标成 metadata_corrupt（普通文档占绝大多数）
2. **只认第一行** —— 正文是不可信输入，扫全文找头等于让导入内容伪造身份
"""
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.core.transcript_meta import (
    META_VERSION, _clean_title, build_composite_raw_text, dump_meta, parse_meta, strip_meta,
)

# ═══════════════════════════════════════════════════════════════
# 1. 往返 — dump → parse 得回原 meta
# ═══════════════════════════════════════════════════════════════

META = {
    "sourceType": "composite",
    "sourceDocIds": ["tr_a", "tr_b"],
    "sourceTitles": ["论文A", "论文B"],
    "sourceHashes": ["a1b2c3d4", "e5f6g7h8"],
}
head = dump_meta(META)
assert head.startswith("<!-- limo-meta ") and head.endswith(" -->"), head
assert "\n" not in head, "出处头必须是单行——它靠「第一行」这个位置被识别"

got, corrupt = parse_meta(head + "\n\n正文内容")
assert not corrupt and got is not None
assert got["v"] == META_VERSION
assert got["sourceDocIds"] == ["tr_a", "tr_b"]
assert got["sourceTitles"] == ["论文A", "论文B"]
assert got["sourceHashes"] == ["a1b2c3d4", "e5f6g7h8"]
print("PASS: dump → parse 往返保持 sourceDocIds / sourceTitles / sourceHashes")

# dump_meta 不得就地修改入参（内部用 dict(meta) 浅拷贝 + 重建 titles 列表）
_orig = {"sourceType": "composite", "sourceTitles": ["a --> b"]}
dump_meta(_orig)
assert _orig["sourceTitles"] == ["a --> b"], "dump_meta 不得就地修改调用方的 dict"

# ═══════════════════════════════════════════════════════════════
# 2. 分隔符冲突 — JSON 方案的核心价值
# ═══════════════════════════════════════════════════════════════
#
# 对照实验：初稿的迷你格式 `sourceTitles=A|B` 用 `|` 分隔。标题本来就可能含
# `|` `,` `=`——清洗函数防的是**注入**（换行伪造标记），**不防分隔符冲突**。
# 下面这些标题喂给迷你格式必然解析错位，喂给 JSON 必须逐项相等。

NASTY = ["含,逗号", "含|竖线", "含=等号", '含"双引号', "含【方括号】"]
raw2 = build_composite_raw_text(["t1"], ["h1"], [{"title": t} for t in NASTY], "正文")
got2, corrupt2 = parse_meta(raw2)
assert not corrupt2
# 清洗会把 " → ' 、【】 → []，但 , | = 必须**原样保留**
assert got2["sourceTitles"] == ["含,逗号", "含|竖线", "含=等号", "含'双引号", "含[方括号]"], \
    f"逐项相等是 JSON 方案的意义所在：{got2['sourceTitles']}"
assert len(got2["sourceTitles"]) == len(NASTY), "项数不能因分隔符冲突而变少"
print("PASS: 标题含 , | = \" 【】 时逐项往返不丢不改（迷你分隔格式会错位）")

# ═══════════════════════════════════════════════════════════════
# 3. `-->` 必须净化 — 否则 HTML 注释提前闭合
# ═══════════════════════════════════════════════════════════════
#
# 对照实验：`json.dumps(title)` 原样塞进去 → 行内出现两个 `-->` → 导出的 HTML 里
# 注释在第一个就结束，` b"]} -->` 变成可见文本（用户看到 JSON 尾巴）。
# JSON 序列化解决不了：`-->` 不是 JSON 特殊字符。

raw3 = build_composite_raw_text(["t1"], ["h1"], [{"title": "危险 --> 标题"}], "正文")
head3 = raw3.split("\n", 1)[0]
assert head3.count("-->") == 1, f"行内只应有结尾那一个 -->（净化失败会提前闭合注释）：{head3}"
assert "危险 → 标题" in head3, "净化是替换为 → 而不是删除（保留「这里原本是箭头」）"
got3, c3 = parse_meta(raw3)
assert not c3 and got3["sourceTitles"] == ["危险 → 标题"]
print("PASS: 标题里的 --> 被替换，HTML 注释不会被提前闭合")

# 换行同理：标题里的换行若不清洗，会撑破「第一行」这个位置约定
raw4 = build_composite_raw_text(["t1"], ["h1"], [{"title": "上\n下"}], "正文")
assert "\n" not in raw4.split("\n", 1)[0], "标题里的换行必须被抹平"
assert parse_meta(raw4)[0]["sourceTitles"] == ["上 下"]
print("PASS: 标题里的换行被抹平，出处头仍是单行")

# _clean_title 必须幂等 —— dump_meta 内部会再洗一次，不幂等就会二次变形
for _t in ("危险 --> 标题", "含【括号】", 'a"b', "上\n下", "x" * 200):
    assert _clean_title(_clean_title(_t)) == _clean_title(_t), f"清洗非幂等：{_t!r}"
print("PASS: _clean_title 幂等（build 与 dump 各洗一次不会二次变形）")

# ═══════════════════════════════════════════════════════════════
# 4. 无头 vs 坏头 —— 必须分开
# ═══════════════════════════════════════════════════════════════
#
# 对照实验：把「无头」也返回 corrupt=True → **所有历史文档全被标成 metadata_corrupt**。
# 普通文档占绝大多数，这个方向错了比不实现还糟。

assert parse_meta("普通文档正文") == (None, False)
assert parse_meta("") == (None, False)
assert parse_meta(None) == (None, False)
assert parse_meta("<!-- 只是条普通注释 -->\n正文") == (None, False)
print("PASS: 无头文档返回 (None, False) —— 不会被误标为损坏")

BAD = [
    '<!-- limo-meta {坏 JSON} -->\n正文',                       # JSON 解析失败
    '<!-- limo-meta {"v":1} -->\n正文',                          # 缺 sourceType
    '<!-- limo-meta {"sourceType":"video"} -->\n正文',           # 不是 composite
    '<!-- limo-meta {"v":1,"sourceType":"composite"',            # 无结尾标记
    '<!-- limo-meta [] -->\n正文',                               # 合法 JSON 但不是 dict
    '<!-- limo-meta "字符串" -->\n正文',
]
for _bad in BAD:
    assert parse_meta(_bad) == (None, True), f"应判为损坏：{_bad!r}"
print("PASS: 有头但坏返回 (None, True) —— 六种损坏形态全部命中")

# ═══════════════════════════════════════════════════════════════
# 5. 只认第一行 —— 正文不可信
# ═══════════════════════════════════════════════════════════════
#
# 对照实验：实现改成「扫整个 rawText 找 <!-- limo-meta」→ 一段被导入的正文就能
# 伪造出处头，让这份文档冒充某几篇的来源。正文来自用户导入内容，是**不可信输入**。

_legit = dump_meta(META)
assert parse_meta("正文第一行\n" + _legit + "\n更多正文") == (None, False), \
    "正文里出现的出处头是用户数据，不能当身份识别"
assert parse_meta(" \n" + _legit) == (None, False), "头必须紧贴第一行行首"
print("PASS: 只解析第一行 —— 正文里的伪出处头不被采信")

# ═══════════════════════════════════════════════════════════════
# 6. build_composite_raw_text 组装形状
# ═══════════════════════════════════════════════════════════════

BODY = "## 综合结论\n\n这几篇的共同点是……"
raw6 = build_composite_raw_text(
    ["t1", "t2", "t3"], ["h1", "h2", "h3"],
    [
        {"title": "论文A", "status": "full"},
        {"title": "论文B", "status": "summarized"},
        {"title": "论文C", "status": "truncated"},
    ],
    BODY,
)
assert raw6.startswith("<!-- limo-meta ")
assert "## 来源文档" in raw6
assert "1. 论文A ✓ 未删节" in raw6
assert "2. 论文B ⚠ 已压缩为摘要" in raw6
assert "3. 论文C ⚠ 已截断" in raw6
assert raw6.endswith(BODY), "正文必须原样落在末尾（含缩进/换行，不得被改写）"
# 三位一体：头 + 列表 + 正文**全都在 rawText 里**，不剥离到别处
_g6, _c6 = parse_meta(raw6)
assert not _c6 and _g6["sourceDocIds"] == ["t1", "t2", "t3"]
print("PASS: 组装形状正确，降级标注持久化在产物的来源列表里（不靠 SSE 一闪）")

# status 缺省 / 未知：不应崩，也不应凭空编造标注
raw7 = build_composite_raw_text(["t1"], ["h1"], [{"title": "无状态文档"}], "正文")
assert "1. 无状态文档 ✓ 未删节" in raw7, "status 缺省视为 full（调用方没标 = 未删节）"
raw8 = build_composite_raw_text(["t1"], ["h1"], [{"title": "怪状态", "status": "???"}], "正文")
assert "1. 怪状态" in raw8, "未知 status 只影响标注，不应丢行"
print("PASS: status 缺省/未知时不崩、不编造标注")

# ═══════════════════════════════════════════════════════════════
# 7. strip_meta — 取正文
# ═══════════════════════════════════════════════════════════════

assert strip_meta(raw6).startswith("## 来源文档"), strip_meta(raw6)[:40]
assert strip_meta("普通文档正文") == "普通文档正文", "无头时必须原样返回，不能吃掉首行"
assert strip_meta("") == ""
print("PASS: strip_meta 去掉头与紧随空行；无头时原样返回")

# ═══════════════════════════════════════════════════════════════
# 8. 对比维度 / 原料模式 —— 只进 meta，不进可见正文
# ═══════════════════════════════════════════════════════════════
#
# ⚠️ 与 sourceTitles 的处理**刻意不同**：来源标题在 meta 与「## 来源文档」列表
# **两处都有**（它需要人眼可读）；维度与原料模式**只在 meta**——前者是给模型的
# 指令（混进可见正文后，用户在编辑正文时容易误删），后者是给三个月后的人看的
# 自解释字段。故下面的断言要同时钉住「meta 里有」与「正文里没有」。

DIMS = ["研究问题", "方法", "数据"]
raw_d = build_composite_raw_text(
    ["t1", "t2"], ["h1", "h2"],
    [{"title": "论文A"}, {"title": "论文B"}],
    BODY, dimensions=DIMS, source_mode="notes",
)
got_d, c_d = parse_meta(raw_d)
assert not c_d and got_d is not None
assert got_d["dimensions"] == DIMS, f"维度必须逐项往返：{got_d.get('dimensions')}"
assert got_d["sourceMode"] == "notes", got_d.get("sourceMode")
# 「只在 meta」这一半同样要断言——否则把维度也写进可见正文的实现照样通过
assert "研究问题" not in strip_meta(raw_d), \
    "维度是给模型的指令，不得混进可见正文（用户编辑正文时会误删）"
print("PASS: dimensions / sourceMode 往返逐项相等，且不出现在可见正文里")

# ── 缺省值：不传时维度是空表、原料是 raw（**不是**默认论文维度）──
# 默认预设由 `synthesize` 决定并显式传入；本函数只负责忠实记录。
# 若在这里偷偷填默认值，caller 传空 = 「用户清空了维度」的语义就没了。
raw_def = build_composite_raw_text(["t1"], ["h1"], [{"title": "A"}], BODY)
got_def, _ = parse_meta(raw_def)
assert got_def["dimensions"] == [], got_def["dimensions"]
assert got_def["sourceMode"] == "raw", got_def["sourceMode"]
print("PASS: 不传时 dimensions=[] / sourceMode='raw'（默认值由调用方决定，本层不猜）")

# ── 历史文档：meta 里**根本没有**这两个字段 —— 读方必须能容忍 ──
# 这是最容易被漏掉的一类：新字段上线前写的产物。生成侧若直接取
# `meta["dimensions"]` 会 KeyError，把一份正常的历史合成文档变成打不开的。
_legacy_head = dump_meta({
    "sourceType": "composite", "sourceDocIds": ["t1"], "sourceTitles": ["A"],
    "sourceHashes": ["h1"],
})
_legacy, _lc = parse_meta(_legacy_head + "\n\n正文")
assert not _lc and _legacy is not None, "老格式仍是合法的 composite 头"
assert _legacy.get("dimensions") is None and _legacy.get("sourceMode") is None, \
    "老文档没有这两个字段——调用方必须用 .get() 并回退，不能下标"
print("PASS: 老格式出处头（无 dimensions/sourceMode）仍解析成功且字段为 None")

# ── 维度里的脏值不得污染出处头（与标题同一把清洗刀）──
raw_dirty = build_composite_raw_text(
    ["t1"], ["h1"], [{"title": "A"}], BODY,
    dimensions=["正常维度", "带 --> 箭头", "带\n换行"],
)
assert "\n" not in raw_dirty.split("\n", 1)[0], "维度里的换行不能撑破「第一行」约定"
got_dirty, c_dirty = parse_meta(raw_dirty)
assert not c_dirty, "维度脏值不得让整个出处头判损坏"
assert got_dirty["dimensions"][0] == "正常维度"
print("PASS: 维度里的换行/--> 被清洗，出处头仍是单行且可解析")

# ── 8.5 读者意图 / 材料体裁：新字段往返，且**只进 meta** ──
# ⚠️ 这两个字段必须落 meta 而不是请求参数：生成是**第二次独立请求**
# （用户核对完「## 来源文档」再点生成），届时手里只有 rawText。
raw_new = build_composite_raw_text(
    ["t1", "t2"], ["h1", "h2"],
    [{"title": "论文A"}, {"title": "论文B"}], BODY,
    content_type=["paper", "tech"], reader_intent="custom",
    reader_intent_text="写给产品经理看",
)
got_new, c_new = parse_meta(raw_new)
assert not c_new and got_new is not None
assert got_new["contentType"] == ["paper", "tech"], got_new.get("contentType")
assert got_new["readerIntent"] == "custom", got_new.get("readerIntent")
assert got_new["readerIntentText"] == "写给产品经理看", got_new.get("readerIntentText")
# 「只在 meta」这一半同样要断言——否则把它们也写进可见正文的实现照样通过
# （用户编辑正文时会误删；同 §8 对维度与原料模式的理由）
_visible_new = strip_meta(raw_new)
assert "写给产品经理看" not in _visible_new and '"contentType"' not in _visible_new, \
    "读者意图与体裁是给模型的指令，不得混进可见正文"
print("PASS: contentType / readerIntent* 往返逐项相等，且不出现在可见正文里")

# ── 8.5b 递归清洗：`-->` 与换行对**所有**字段生效（不再只清 sourceTitles）──
# ⚠️ `readerIntentText` 是用户自由文本、天然可能带换行与箭头；`-->` 会**提前闭合
# HTML 注释**，导出时 JSON 尾巴变成可见正文。规则按**字段**写就会一直漏（`dimensions`
# 此前就是这样漏的），按**递归遍历**写则新增字段自动受保护——本用例钉的就是这条。
raw_clean = build_composite_raw_text(
    ["t1"], ["h1"], [{"title": "A"}], BODY,
    content_type=["paper"], reader_intent="custom",
    reader_intent_text="第一行\n第二行 --> 结束",
)
_head_clean = raw_clean.split("\n", 1)[0]
assert _head_clean.count("-->") == 1, \
    f"只有结尾那一个 -->（字段里的箭头必须被替换）——实际 {_head_clean.count('-->')} 个"
assert "\n" not in _head_clean, "自由文本里的换行不得撑破「只认第一行」的单行契约"
got_clean, c_clean = parse_meta(raw_clean)
assert not c_clean, "清洗之后必须仍可解析（清洗不得反过来撑坏出处头）"
assert "→" in got_clean["readerIntentText"], \
    "换成 → 而不是直接删——保住「这里原本是个箭头」的信息"
print("PASS: readerIntentText 里的 --> 与换行被递归清洗（单行契约不破）")

# ── 8.5c 历史文档：meta 里根本没有新字段 → None，且**不得判损坏** ──
# 与 §8 的 `_legacy` 同一份样本（新字段上线前写下的产物形态）。
assert _legacy.get("contentType") is None and _legacy.get("readerIntent") is None, \
    "老文档没有新字段——读方必须 .get()，不能下标"
assert not _lc, "无头≠损坏的同一个道理：老格式也不是损坏"
print("PASS: 老格式出处头（无 contentType/readerIntent）仍解析成功且字段为 None")

# ═══════════════════════════════════════════════════════════════
# 9. 降级状态八态可辨 + 基础状态回退
# ═══════════════════════════════════════════════════════════════
#
# ⚠️ `truncated` 曾是**垃圾桶**：超窗 / 超时 / 模型抽风全标它。而用户能采取的
# 行动完全不同（换大窗口模型 / 重试 / 检查 Key）——共用一个标签等于「知道出事了
# 但不知道该做什么」（设计 §17.3）。

from app.core.transcript_meta import status_label  # noqa: E402

_SPECIFIC = ["truncated_over_window", "truncated_timeout", "truncated_llm_error"]
_labels = [status_label(s) for s in _SPECIFIC]
assert all(_labels), f"三个原因必须都有标注：{_labels}"
assert len(set(_labels)) == 3, f"三种原因的文案必须互不相同（否则等于没细分）：{_labels}"
assert all(l != status_label("truncated") for l in _labels), \
    "细分状态不得与垃圾桶状态的文案相同"
# 正常态也要各自可辨，且都不含「已截断」
assert status_label("full") and status_label("summarized") and status_label("chunked")
assert len({status_label(s) for s in ("full", "summarized", "chunked")}) == 3
print(f"PASS: 八态文案互异（{_labels}）")

# ── 基础状态回退：未知细分不崩、不丢行 ──
# 对照实验：实现改成 `_STATUS_LABEL[status]` → KeyError，**整个来源列表渲染失败**；
# 改成返回空串而不回退 → 用户看不到降级标注（比粗粒度标注更坏）。
assert status_label("truncated_some_future_reason") == status_label("truncated"), \
    "未知的 truncated_* 必须回退到基础状态 truncated（历史产物存的正是它）"
assert status_label("") == "" and status_label(None) == "", "空/None 不该崩"
assert status_label("完全不认识的状态") == "", "查不到就是空串——不崩、不编造"
print("PASS: 基础状态回退（truncated_* → truncated；未知 → 空串，不崩不编造）")

# 八态全表覆盖：每个 key 都必须有非空文案（防止某天加了状态忘了写 label）
from app.core.transcript_meta import _STATUS_LABEL  # noqa: E402
assert all(v for v in _STATUS_LABEL.values()), "存在没有文案的状态 key"
for _k in _STATUS_LABEL:
    assert status_label(_k) == _STATUS_LABEL[_k], f"{_k} 的查询路径与表不一致"
print(f"PASS: {len(_STATUS_LABEL)} 个状态 key 全部有文案且查询路径一致")

print("\n全部通过：transcript_meta 编解码")
