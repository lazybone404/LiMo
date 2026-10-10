"""
示例文档预置 — 在 DocLibrary 维护一篇「✨ 使用说明（示例）」文档。

内容源为独立 Markdown 文件（便于经常修改，改 md 即可，无需改代码）：
- sample_docs/notes.md    → 「笔记」tab（面向大众：功能 / 场景 / 理念 / QA）
- sample_docs/polished.md → 「整理版原文」tab（深度说明：引擎 / 模型 / 自定义接入）

同步规则（marker 存内容指纹 = notes+polished 联合哈希）：
- 指纹不匹配（首次 / 内容更新 / 重新安装）→ 创建或更新文档 + 写新指纹
  —— 即使之前被用户删除也会重建，示例文档兼作「更新说明」让用户知道新内容
- 指纹匹配且文档存在 → 已同步，跳过
- 指纹匹配但文档被删除 → 内容无变化，尊重删除，不复活
"""

import hashlib
import logging
import uuid
from pathlib import Path

from app.core.types import (
    GenerateMode,
    MarkdownView,
    RawTranscript,
    SourceType,
    Style,
)
from app.storage.store import Store

logger = logging.getLogger(__name__)

SAMPLE_DOC_ID = "tr_sample_000000"
SAMPLE_DOC_TITLE = "✨ 使用说明（示例）"
_MARKER = ".sample_doc_seeded"
# 固定 content_hash，与正常导入文档绝不碰撞（防止被 find_by_hash 去重拦截）
_CONTENT_HASH = hashlib.sha256(b"limo-sample-doc-v1").hexdigest()

# 内容源目录（与本文件同目录，PyInstaller datas 同步打包）
_DOCS_DIR = Path(__file__).resolve().parent / "sample_docs"
_NOTES_FILE = _DOCS_DIR / "notes.md"
_POLISHED_FILE = _DOCS_DIR / "polished.md"

# 转录原文 — 简短欢迎（「转录」tab 显示）
SAMPLE_DOC_RAW = (
    "欢迎使用 LiMo。这是预置的示例文档："
    "「笔记」页是一分钟快速了解，"
    "「整理版原文」页包含引擎、大模型选择与自定义模型接入的详细介绍。"
    "你可以随时删除这篇文档；当内容更新时，它会在下次启动重新出现。"
)


def _read_source(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("示例文档内容读取失败 %s: %s", path.name, e)
        return None


def _fingerprint(notes: str, polished: str) -> str:
    return hashlib.sha256((notes + "\x00" + polished).encode("utf-8")).hexdigest()


def _latest_md(store: Store, tx_id: str, mode: str) -> str | None:
    """读文档当前该 mode 的最新内容（判断是否需要更新）。"""
    mds = store.get_markdowns_by_transcript(tx_id) or []
    cands = [e for e in mds if e.get("mode") == mode]
    if not cands:
        return None
    top = max(cands, key=lambda e: e.get("version", 0))
    return store.get_markdown(top["id"])


def seed_sample_doc(store: Store) -> None:
    """维护示例文档。失败不阻塞启动。"""
    marker = store.root / _MARKER
    notes = _read_source(_NOTES_FILE)
    polished = _read_source(_POLISHED_FILE)
    if notes is None or polished is None:
        logger.warning("示例文档内容缺失（sample_docs/notes.md 或 polished.md），跳过预置")
        return

    fp = _fingerprint(notes, polished)
    try:
        marker_fp = marker.read_text(encoding="utf-8").strip() if marker.exists() else None
        tx_exists = store.get_transcript(SAMPLE_DOC_ID) is not None

        if marker_fp == fp:
            # 内容无变化
            if not tx_exists:
                return  # 用户删除过且内容无更新 → 尊重删除，不复活
            # 文档存在但内容同步过 → 无需操作（不重复生成，已有产物保持原样）
            return

        # 指纹不匹配 → 首次创建 / 内容更新 / 重新安装 → 创建或更新
        if not tx_exists:
            tx = RawTranscript(
                id=SAMPLE_DOC_ID,
                title=SAMPLE_DOC_TITLE,
                source_type=SourceType.TEXT,
                engine="sample",
                raw_text=SAMPLE_DOC_RAW,
                content_hash=_CONTENT_HASH,
            )
            store.save_transcript(tx)

        # 内容不一致才写入，避免无谓的版本追加
        if _latest_md(store, SAMPLE_DOC_ID, "notes") != notes:
            store.save_markdown(MarkdownView(
                id=f"md_{uuid.uuid4().hex[:12]}",
                transcript_id=SAMPLE_DOC_ID,
                content=notes,
                style=Style.DETAILED,
                mode=GenerateMode.NOTES,
            ))
        if _latest_md(store, SAMPLE_DOC_ID, "polished") != polished:
            store.save_markdown(MarkdownView(
                id=f"md_{uuid.uuid4().hex[:12]}",
                transcript_id=SAMPLE_DOC_ID,
                content=polished,
                style=Style.DETAILED,
                mode=GenerateMode.POLISHED,
            ))

        marker.write_text(fp, encoding="utf-8")
        logger.info("示例文档已%s: %s",
                    "创建" if not tx_exists else "更新", SAMPLE_DOC_TITLE)
    except Exception as e:
        logger.warning("示例文档预置失败（不影响启动）: %s", str(e)[:200])
