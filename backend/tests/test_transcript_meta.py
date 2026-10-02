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
assert "1. 论文A ✓ 完整输入" in raw6
assert "2. 论文B ⚠ 已压缩为摘要" in raw6
assert "3. 论文C ⚠ 已截断" in raw6
assert raw6.endswith(BODY), "正文必须原样落在末尾（含缩进/换行，不得被改写）"
# 三位一体：头 + 列表 + 正文**全都在 rawText 里**，不剥离到别处
_g6, _c6 = parse_meta(raw6)
assert not _c6 and _g6["sourceDocIds"] == ["t1", "t2", "t3"]
print("PASS: 组装形状正确，降级标注持久化在产物的来源列表里（不靠 SSE 一闪）")

# status 缺省 / 未知：不应崩，也不应凭空编造标注
raw7 = build_composite_raw_text(["t1"], ["h1"], [{"title": "无状态文档"}], "正文")
assert "1. 无状态文档 ✓ 完整输入" in raw7, "status 缺省视为 full（调用方没标 = 完整输入）"
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

print("\n全部通过：transcript_meta 编解码")
