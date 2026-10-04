"""产物自证头（`type == "artifact"`）—— 落盘 / 读回 / 剥离 / 两个 dict 参数不串位

守的是《产物复用与获取渠道约定》§3 的四条：产物离开 `.limo` 之后仍能自答
「从哪来 / 怎么来的 / 是什么」。

⚠️ **判据不是「meta 写进去了」**。只验一半（写入侧）会让读方静默拿到脏正文——
本模块每条都成对验：写了 → 读得回 → **剥掉之后逐字节等于原文**。

⚠️ **对照实验（每条判据配一个「看起来对、其实错」的实现，且已实测判红）**：
- 写入判据 ← 替代实现「`file_meta` 不写盘」→ 第 1 节红（实测：`读回的内容应带自证头`）
- 剥离判据 ← 替代实现「笔记当原料时不剥头」→ 第 6 节红（实测：`喂给模型的原料不该含自证头`）
- 串位判据 ← 替代实现「`index_extra` 位置填 `file_meta`」→ 第 3 节红（实测：`index 条目的 marker 应是 INDEX`）
- 派生视图判据 ← 替代实现「思维导图不剥头」→ 第 8 节红（实测：`自证头残片 '收益' 进了导出的 JS 模板字面量`）

⚠️ **一条刻意不写进来的对照**：`strip_meta` 从 `parse_meta` 换到 `_parse_envelope` 作判据，
**行为面没有区分力**——旧判据下产物头虽被归为「损坏」，但 `strip_meta` 的剥头动作照做、
结果逐字节相同。它修的是**语义**（产物头不是损坏数据），故本模块不为它伪造一条
「必红」声明：把没有区分力的判据写成对照实验，比不写更坏。
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.core.transcript_meta import dump_meta, parse_artifact_meta, strip_meta
from app.core.types import (MarkdownView, ProcessResult, RawMetadata, RawTranscript,
                            SourceType, Style, GenerateMode)
from app.generation.provenance import PROMPT_VERSION, artifact_meta, markdown_meta
from app.storage.store import Store

_TMP: list[str] = []
BODY = "# 标题\n\n正文第一段。\n\n## 小节\n\n- 一\n- 二\n"


def _new_store() -> Store:
    d = tempfile.mkdtemp()
    _TMP.append(d)
    return Store(d)


def _import_store(st: Store, tx_id: str, title: str = "示例") -> None:
    st.save_transcript(RawTranscript(
        id=tx_id, title=title, source_type=SourceType.WEBPAGE, engine="webpage",
        raw_text="x" * 40, content_hash=f"hash_{tx_id}",
        metadata=RawMetadata(), tags=[], source_url="https://example.com/a",
    ))


def _mv(mv_id: str, tx_id: str, body: str = BODY,
        mode: GenerateMode = GenerateMode.NOTES) -> MarkdownView:
    return MarkdownView(id=mv_id, transcript_id=tx_id, content=body,
                        style=Style.DETAILED, mode=mode)


def _head_line(st: Store, tx_id: str, filename: str = "notes.md") -> str:
    """读产物文件的首行（**绕过 Store**，直接看磁盘）—— 这是「文件是不是真相源」的判据。"""
    d = st.get_doc_dir(tx_id)
    assert d, f"文档目录不存在: {tx_id}"
    return (d / filename).read_text(encoding="utf-8").split("\n", 1)[0]


# ═══════════════════════════════════════════════════════════════
# 1. 落盘 → 读回 → 剥离：写方读方成对
# ═══════════════════════════════════════════════════════════════

s = _new_store()
_import_store(s, "tr_am1")
tx = s.get_transcript("tr_am1")
mv = _mv("md_am1", "tr_am1")
mv.generation_model = "qwen3.8-max"

meta = markdown_meta(mv, tx, formats=["toc", "chart"], reasoning_effort="high")
assert meta["type"] == "artifact" and meta["kind"] == "notes", meta
assert s.save_markdown(mv, file_meta=meta), "写入应成功"

got = s.get_markdowns_by_transcript("tr_am1", mode="notes")[0]
assert got["content"].startswith("<!-- limo-meta "), "读回的内容应带自证头"

parsed, corrupt = parse_artifact_meta(got["content"])
assert corrupt is False, "自己写的头不该被判损坏"
assert parsed is not None
assert parsed["kind"] == "notes", parsed
# ① 从哪来
assert parsed["source"]["docId"] == "tr_am1", parsed["source"]
assert parsed["source"]["sourceType"] == "webpage", parsed["source"]
assert parsed["source"]["sourceUrl"] == "https://example.com/a", parsed["source"]
# ② 怎么来的
assert parsed["generated"]["model"] == "qwen3.8-max", parsed["generated"]
assert parsed["generated"]["promptVersion"] == PROMPT_VERSION, parsed["generated"]
assert parsed["generated"]["params"]["formats"] == ["toc", "chart"], parsed["generated"]
assert parsed["generated"]["params"]["reasoningEffort"] == "high", parsed["generated"]
assert parsed["generated"]["generatedAt"], parsed["generated"]
# ③ 是什么 —— version 刻意留空（真实版本由 store 在加锁区内算，写盘时调用方还不知道）
assert parsed["identity"]["mode"] == "notes", parsed["identity"]
assert "version" not in parsed["identity"], "version 不该被预填（假数据比缺席更坏）"
# ④ 可信度边界：结构在，值待提示词侧配合
assert "trust" in parsed, parsed

assert strip_meta(got["content"]) == BODY, "剥离后必须**逐字节**等于原文"

print("PASS: 自证头落盘 → 读回 → 解析四条齐全（源/生成方式/身份/可信边界）")
print("PASS: 剥离后逐字节等于原文（写方读方成对，不留脏正文）")

# ═══════════════════════════════════════════════════════════════
# 2. 两版制：降级的 prev 也是**完整产物**（带它自己那一版的头）
# ═══════════════════════════════════════════════════════════════

mv2 = _mv("md_am2", "tr_am1")
mv2.generation_model = "qwen3.8-max"
s.save_markdown(mv2, file_meta=markdown_meta(mv2, tx))

prev_line = _head_line(s, "tr_am1", "notes.prev.md")
cur_line = _head_line(s, "tr_am1", "notes.md")
assert prev_line.startswith("<!-- limo-meta "), f"prev 也该带头，实得: {prev_line[:40]}"
assert cur_line.startswith("<!-- limo-meta "), f"最新版该带头，实得: {cur_line[:40]}"
# 两版各自带头，且是**不同的** meta（generatedAt 至少不同档，版本语义靠 index）
assert parse_artifact_meta(prev_line)[0] is not None
assert parse_artifact_meta(cur_line)[0] is not None

print("PASS: 降级产生的 .prev.md 同样带自证头（它是完整产物，不是残片）")

# ═══════════════════════════════════════════════════════════════
# 3. ⚠️ 对照实验：`file_meta` 与 `index_extra` 去向不同，串位不报错
#    （P3 —— 两个 dict 参数并排、名字都不带方向时最容易接错）
# ═══════════════════════════════════════════════════════════════

s3 = _new_store()
_import_store(s3, "tr_am3")
mv3 = _mv("md_am3", "tr_am3")
s3.save_markdown(mv3,
                 file_meta={"type": "artifact", "kind": "notes", "marker": "FILE"},
                 index_extra={"edited": True, "marker": "INDEX"})

entry3 = s3.get_markdowns_by_transcript("tr_am3", mode="notes")[0]
head3 = _head_line(s3, "tr_am3")

# ① file_meta 的键**只**该出现在文件首行
assert entry3.get("marker") == "INDEX", f"index 条目的 marker 应是 INDEX，实得 {entry3.get('marker')}"
assert "kind" not in entry3, f"file_meta 的键不该漏进 index 条目: {sorted(entry3)}"
assert "type" not in entry3, f"file_meta 的键不该漏进 index 条目: {sorted(entry3)}"

# ② index_extra 的键**只**该出现在 index 条目
assert entry3.get("edited") is True, "index_extra 应进 index 条目"
assert "INDEX" not in head3, f"index_extra 的键不该漏进文件首行: {head3[:80]}"
assert '"marker":"FILE"' in head3.replace(" ", ""), f"file_meta 应在文件首行: {head3[:80]}"

print("PASS: 对照实验 —— file_meta 只进文件首行、index_extra 只进 index 条目（串位必红）")

# ═══════════════════════════════════════════════════════════════
# 4. process 产物：生产者是 engine（不是生成模型的 model）
# ═══════════════════════════════════════════════════════════════

s4 = _new_store()
_import_store(s4, "tr_am4")
pr = ProcessResult(id="pr_am4", transcript_id="tr_am4", mode="extract",
                   engine="dashscope-ocr", content="识别出来的文字")
s4.save_process_result(pr, file_meta={
    "type": "artifact", "kind": "extract",
    "generated": {"engine": "dashscope-ocr", "promptVersion": PROMPT_VERSION},
})

pr_body = s4.get_process_result("pr_am4")
assert pr_body.startswith("<!-- limo-meta "), f"process 产物应带头: {pr_body[:40]}"
pr_meta, pr_corrupt = parse_artifact_meta(pr_body)
assert pr_corrupt is False and pr_meta["kind"] == "extract", pr_meta
assert strip_meta(pr_body) == "识别出来的文字", "process 产物剥离后应是纯正文"

print("PASS: 图片处理产物带头，生产者记 engine（与生成产物的 model 分区）")

# ═══════════════════════════════════════════════════════════════
# 5. 不传 meta（历史产物 / 内置示例）→ 无头，且**不判损坏**
# ═══════════════════════════════════════════════════════════════

s5 = _new_store()
_import_store(s5, "tr_am5")
mv5 = _mv("md_am5", "tr_am5")
s5.save_markdown(mv5)  # 不传 file_meta
body5 = s5.get_markdowns_by_transcript("tr_am5", mode="notes")[0]["content"]
assert body5 == BODY, "不传 meta 时应逐字节原样写入（零改动）"
m5, c5 = parse_artifact_meta(body5)
assert m5 is None and c5 is False, f"无头 ≠ 损坏，实得 ({m5}, {c5})"
assert strip_meta(body5) == body5, "无头时 strip_meta 应原样返回"

print("PASS: 无头产物不判损坏（历史产物与内置示例零影响）")

# ═══════════════════════════════════════════════════════════════
# 6. ⚠️ 「笔记当原料」入口必须剥头 —— 否则 meta 进模型 prompt 并占配额
#    （合成 source_mode="notes" 链路，§19 已上线）
# ═══════════════════════════════════════════════════════════════

from app.generation.synthesis import _latest_notes_text  # noqa: E402

s6 = _new_store()
_import_store(s6, "tr_am6")
mv6 = _mv("md_am6", "tr_am6")
mv6.generation_model = "qwen3.8-max"
s6.save_markdown(mv6, file_meta=markdown_meta(mv6, s6.get_transcript("tr_am6")))

raw_notes = s6.get_markdowns_by_transcript("tr_am6", mode="notes")[0]["content"]
assert "limo-meta" in raw_notes, "前置：库里那份确实是带头的"

notes_text = _latest_notes_text(s6, "tr_am6")
assert "limo-meta" not in notes_text, f"喂给模型的原料不该含自证头: {notes_text[:80]}"
assert notes_text == BODY.strip(), "剥头后应等于纯正文"

print("PASS: 笔记当合成原料时自证头被剥离（不进 prompt、不计入 token 配额）")

# ═══════════════════════════════════════════════════════════════
# 7. ⚠️ 编辑保存必须**带回**自证头（一次编辑不能把产物打回不可自证）
# ═══════════════════════════════════════════════════════════════

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.dependencies import get_store  # noqa: E402
from app.core.errors import AppError  # noqa: E402
from app.routers import markdown_edit  # noqa: E402

s7 = _new_store()
_import_store(s7, "tr_am7")
mv7 = _mv("md_am7", "tr_am7")
mv7.generation_model = "qwen3.8-max"
s7.save_markdown(mv7, file_meta=markdown_meta(mv7, s7.get_transcript("tr_am7")))

# 只挂 markdown_edit 路由的测试 app（不触发主 app startup，范式同 test_markdown_edit.py）
_app7 = FastAPI()
_app7.include_router(markdown_edit.router, prefix="/api")
_app7.dependency_overrides[get_store] = lambda: s7


@_app7.exception_handler(AppError)
async def _app_error_handler(request, exc: AppError):
    return JSONResponse(status_code=200 if exc.recoverable else exc.status_code,
                        content=exc.to_dict())


with TestClient(_app7) as client:
    # 前端送来的是「用户在源码编辑器里看到的正文」——可能含旧头、也可能被改过
    resp = client.put("/api/markdown/tr_am7", json={
        "mode": "notes",
        "content": "# 标题\n\n用户改过的正文。\n",
    })
    assert resp.status_code == 200, resp.text

after = s7.get_markdowns_by_transcript("tr_am7", mode="notes")[0]
head_after, corrupt_after = parse_artifact_meta(after["content"])
assert corrupt_after is False, "编辑后头不该损坏"
assert head_after is not None, "编辑保存必须带回自证头（否则产物被打回不可自证）"
assert head_after["source"]["docId"] == "tr_am7", head_after["source"]
assert head_after["generated"]["model"] == "qwen3.8-max", head_after["generated"]
assert strip_meta(after["content"]) == "# 标题\n\n用户改过的正文。\n", after["content"][:120]
assert after.get("edited") is True, "index_extra 侧不受影响"

print("PASS: 编辑保存带回自证头 + 正文生效 + edited 标记仍进 index")

# ═══════════════════════════════════════════════════════════════
# 8. 思维导图导出：派生视图**不承载**自证头（值含 `>` 时也不漏残片）
#
# ⚠️ **判据必须是「值含 `>`」的那一例**。思维导图的清洗规则里有
# `r'<[^>]+>'`（去 HTML 标签），它**按第一个 `>` 断句**——值不含 `>` 时它恰好
# 匹配到注释结尾、整行删净，于是「测含注释」会通过并给出**假绿灯**（实测确认：
# 只测这一例时，撤掉 `strip_meta` 也不红）。真实可达的触发条件是用户自定义维度
# `成本 > 收益`，或意图文本里写 `A > B`。
# ═══════════════════════════════════════════════════════════════

from app.routers.export import _build_mindmap_html  # noqa: E402

_meta8 = artifact_meta("notes", doc_id="tr_mm", mode="notes", model="m",
                       params={"dimensions": ["成本 > 收益"]})
_html8 = _build_mindmap_html(dump_meta(_meta8) + "\n\n# 标题\n", "思维导图")

# ⚠️ 判据词必须**只可能来自 meta**：`"type"` 出现在 markmap 的 JS 里（`["url","type","lang"]`）、
# `cost` 出现在 base64 字体数据里——拿它们当判据会得到**假红**（本判据第一版就跑错了）
for _tok in ('"artifact"', "promptVersion", "limo-meta", "收益"):
    assert _tok not in _html8, f"自证头残片 `{_tok}` 进了导出的 JS 模板字面量"

print("PASS: 思维导图剥掉自证头（含 `>` 的值不残片、不误判）")

for _d in _TMP:
    shutil.rmtree(_d, ignore_errors=True)

print("\n全部通过：产物自证头（落盘 / 读回 / 剥离 / 参数不串位 / 编辑保留 / 派生视图剥头）")
