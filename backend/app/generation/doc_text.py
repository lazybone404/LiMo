"""下游消费者要的文档正文 — 从库里取出、剥掉全部协议层的纯文本。

**为什么单列一个模块**：`latest_notes_text` 里的「最新版」判据不可复制（见其
docstring），而它已有两个消费方（合成管道 / 自动分类），将来任何「读产物正文喂
模型」的地方还会是第三个。放在任一侧当私有函数 = 另一侧要么 import 一个私有名、
要么复刻一遍判据（模式 #54：同源判据住多处，失效方式是「格式演进时漏改一处」）。

两个消费者家族各有一个入口：
- `latest_notes_text`     → **模型**（合成 / 分类）：只要笔记正文
- `search_index_text`     → **全文索引**：rawText + 笔记，因为它回答的是
                            「这篇文档里有没有提到 X」，而不是「模型该读什么」

⚠️ 本模块只做「取 + 剥」，**不做截断**——截到多少字是消费方的决定
（合成按配额、分类按用户选的取材深度），塞进来会让两个消费方共用一个不该共用的数。
"""

from app.core import logging as log
from app.core.artifact_blocks import strip_blocks
from app.core.transcript_meta import strip_meta


def latest_notes_text(store, tx_id: str) -> str:
    """取该文档 notes 的**最新版正文**；没有笔记返回空串。

    ⚠️ 「最新版」与 `store.get_markdown` 是同一套判据：`get_markdowns_by_transcript`
    返回**按 version 降序**的列表，`[0]` 即最新版（即 `{mode}/v{max}.md`）。
    自己判版本号会与它漂移——版本判据只应存在一份。

    ⚠️ **必须剥掉自证头**：`{mode}/v{N}.md` 的首行是 `<!-- limo-meta ... -->`，而这份
    文本会作为【文档 i】的正文**进模型 prompt**。不剥的后果有三条：① 与「不给
    普通 transcript 加头」的边界自相矛盾（那条的理由正是「会进 prompt」）；②
    消费方按 `estimate_tokens(raw_text)` 算配额时，头会被算进去；③ 材料里多一类
    可控文本（历史版本的真实 JSON，非伪造也够呛）。

    ⚠️ **语义标记同剥**：与自证头同理，三条理由逐条成立——标记行进 prompt 是噪音、
    会被 `estimate_tokens` 算进配额、且是我们专属的协议元素。
    ⚠️ 剥的**只是标记行**，块内文字照旧进 prompt（`strip_blocks` 的契约）。
    """
    try:
        entries = store.get_markdowns_by_transcript(tx_id, mode="notes")
    except Exception as e:
        log.warning("读取笔记失败", doc=tx_id, error=str(e)[:200])
        return ""
    if not entries:
        return ""
    return strip_blocks(strip_meta(entries[0].get("content") or "")).strip()


def search_index_text(store, tx_id: str) -> str:
    """一篇文档进入全文索引的全部文本 = rawText + **最新版笔记正文**。

    ⚠️ **笔记必须进索引**（2026-10-26 实测，131 篇真实库 / 29 题 / 前 5 名判据）：
    只索引 rawText 命中 **66%**，加上笔记 **83%** —— 17pp，是字面检索这条线上
    最大的一笔单项收益。原因是「用户搜的词」与「原文的词」本来就不是一套：
    视频转录是逐字稿（原话说的是「这个模型一边猜一边验证」），而记下的那个词
    是笔记里才有的（「投机解码」）。只索引 rawText 等于**只搜用户没写过的那部分**。

    ⚠️ 笔记侧必须走 `latest_notes_text`（剥自证头 + 语义标记），不能直接 read：
    那些是协议元素，让 `limo-block` / `limo-meta` 变成可搜噪音毫无意义。

    ⚠️ **这是「索引文本」的唯一判据**。摄入 / 生成完成 / 编辑保存 / 启动重建
    四个写点都走它 —— 各自拼一份的失效形态是「生成后搜得到、编辑后又搜不到」，
    而两次索引都看起来成功了（模式 #54）。
    """
    tx = store.get_transcript(tx_id)
    raw = (tx or {}).get("rawText", "") or ""
    notes = latest_notes_text(store, tx_id)
    if not notes:
        return raw
    return f"{raw}\n\n{notes}" if raw.strip() else notes
