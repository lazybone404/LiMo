"""
文件系统 + JSON 索引存储
参照 system-design-spec.md 四.5

v2: 统一索引 — transcripts / markdowns / processes / conversations
    合并为一个 index.json，一次原子写入消除跨文件不一致。
"""

import hashlib
import json
import logging
import os
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

from app.core.types import RawTranscript, MarkdownView, ProcessResult, Style, GenerateMode
from app.core.errors import AppError, StoreError
from app.core.transcript_meta import parse_meta, dump_meta

logger = logging.getLogger(__name__)

INDEX_VERSION = 3

# ── 资产 URL 改写 ──
# 注意：此函数仅用于路由层（content API 返回前改写）。
# Store CRUD 方法（get_markdown/get_transcript 等）不调此函数，
# 原因：导出需要原始相对路径（从 {doc_dir}/assets/ 读盘），续写需要原文。

# assets/ 前缀移出捕获组：\2 只取文件名，避免改写后 URL 重复 assets/ 段
# （修复：/api/assets/{tx_id}/assets/img_0000.png → /api/assets/{tx_id}/img_0000.png）
_RE_ASSET_REF = re.compile(r'!\[([^\]]*)\]\(assets/([^\s)]+)\)')


def rewrite_asset_urls(content: str, tx_id: str) -> str:
    """将磁盘相对路径 assets/xxx 改写为绝对 API URL，供前端渲染。
    应在路由层调用，不在 Store CRUD 内部调用。"""
    if not content:
        return content
    return _RE_ASSET_REF.sub(rf'![\1](/api/assets/{tx_id}/\2)', content)


def _serialize_metadata(meta) -> dict:
    """安全序列化 RawMetadata，兼容 dataclass 和 dict"""
    import dataclasses
    if dataclasses.is_dataclass(meta):
        d = dataclasses.asdict(meta)
        return {k: v for k, v in d.items() if v not in (None, [], "") or k in ("warnings",)}
    if isinstance(meta, dict):
        return meta
    return {}


def _empty_index() -> dict:
    return {
        "version": INDEX_VERSION,
        "transcripts": [],
        "folders": [],
        "markdowns": {},
        "processes": {},
        "conversations": [],
    }


class Store:
    """单用户文件系统存储。v2 统一索引。"""

    def __init__(self, data_dir: str):
        self.root = Path(data_dir)
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            (self.root / "logs").mkdir(exist_ok=True)
        except OSError as e:
            raise AppError(
                "STORE_CREATE_DIR_FAILED",
                f"无法创建数据目录（权限不足或磁盘满）",
                detail=str(self.root),
                recoverable=False,
            ) from e
        self._index: dict = _empty_index()
        self._index_path = self.root / "index.json"
        self._lock = threading.Lock()
        self._load_index()

    # ── 索引加载 / 保存 / 迁移 ──

    def _load_index(self):
        """加载统一索引。v2 及以下 → 自动初始化为空（新架构不兼容）。"""
        if self._index_path.exists():
            try:
                data = json.loads(self._index_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                logger.warning("index.json 损坏，初始化为空索引")
                data = None
            if data and isinstance(data, dict) and data.get("version", 0) >= 3:
                default = _empty_index()
                for key in default:
                    if key not in data:
                        data[key] = default[key]
                self._index = data
                return
            logger.info("检测到旧索引格式 (v%s)，初始化 v3 空索引",
                        data.get("version", 0) if data else "?")
        self._index = _empty_index()

    def _save_index(self):
        """原子写入统一 index.json"""
        self._atomic_write(
            self._index_path,
            json.dumps(self._index, ensure_ascii=False, indent=2),
        )

    def _rebuild_index(self):
        """遍历所有文档目录重建索引（故障恢复）。
        识别规则：根目录下含 transcript.md 的子目录视为文档目录。
        从目录名反推 dir_name，从文件推断 index 条目。
        """
        import re as _re
        self._index = _empty_index()

        for d in sorted(self.root.iterdir()):
            if not d.is_dir():
                continue
            if d.name.startswith(".") or d.name in ("logs", "tmp", "files",
                                                      "conversations", "cache"):
                continue
            tx_md = d / "transcript.md"
            if not tx_md.exists():
                continue

            # 这是文档目录
            dir_name = d.name
            tx_id = ""

            # 从 transcript.md 内容推断 tx_id（无可靠方法，生成新 ID）
            raw_text = tx_md.read_text(encoding="utf-8")
            tx_id = f"tr_{hashlib.sha256(raw_text.encode()).hexdigest()[:12]}"

            # 构建 transcript index 条目
            tx_entry = {
                "id": tx_id, "title": dir_name.rsplit("-", 1)[0],
                "sourceType": "", "sourceUrl": "", "sourceFileName": "",
                "sourceFilePath": "", "engine": "", "engineVersion": "",
                "language": "", "duration": None, "coverUrl": "",
                "sourceTitle": "", "contentHash": hashlib.sha256(raw_text.encode()).hexdigest(),
                "metadata": {}, "tags": [], "createdAt": "",
                # 文件夹归属只存在于 index.json（文件夹是纯逻辑分类，不落磁盘），
                # 故重建必然丢失分类——这里写空串是为保持条目 schema 完整，
                # 让下游 e.get("folderId") 永远拿到字符串而非 KeyError 之外的意外。
                "folderId": "",
                # 合成来源身份（模式 #44 的第三处构造点，必须与 _transcript_index_entry 同构）
                "sourceDocIds": [], "sourceTitles": [], "sourceHashes": [],
                "metadataCorrupt": False,
                "dir_name": dir_name,
            }
            # ── 出处头是重建后**唯一**能还原合成身份的来源 ──
            # 重建时 id 由内容 sha256 重推、sourceType 被写死为空、非内容派生字段全丢，
            # 只有 transcript.md 第一行还留着「我是哪几篇合成的」。解析失败不猜，标损坏。
            _head, _corrupt = parse_meta(raw_text)
            if _head:
                tx_entry["sourceType"] = _head.get("sourceType", "")
                tx_entry["sourceDocIds"] = list(_head.get("sourceDocIds") or [])
                tx_entry["sourceTitles"] = list(_head.get("sourceTitles") or [])
                tx_entry["sourceHashes"] = list(_head.get("sourceHashes") or [])
            elif _corrupt:
                tx_entry["metadataCorrupt"] = True
            # ⚠️ 已知遗留：重建用 sha256(raw_text)[:12] 重推 id，**源文档重建后 id 也会变**
            #    → sourceDocIds 会悬空。故过期检测只能用 sourceHashes 比对当前 contentHash，
            #    不按 id 反查（§4.2.2「本项目根本没有稳定 id」）。
            self._index["transcripts"].append(tx_entry)

            # 重建 markdown 条目
            md_entries = []
            for md_file in sorted(d.glob("*.md")):
                if md_file.name == "transcript.md":
                    continue
                mode = md_file.stem
                if mode.endswith(".prev"):
                    mode = mode[:-5]  # notes.prev → notes
                # 只处理主文件，.prev 由 get_markdowns_by_transcript 检测
                if md_file.suffix == ".md" and not md_file.stem.endswith(".prev"):
                    md_id = f"md_{hashlib.sha256(str(md_file).encode()).hexdigest()[:12]}"
                    md_entries.append({
                        "id": md_id, "transcriptId": tx_id,
                        # ⚠️ **这个 "detailed" 是「无法还原时的占位」，不是默认档**：
                        # 磁盘上 md 文件只有正文，index.json 一删就没人记得当时的
                        # style，只能填一个。改全局默认风格档时**不要**顺手改它
                        # —— 那样会让历史产物的标签集体变成「通俗」，比留着旧的
                        # 占位更不诚实。见模式 #44（重建路径手工构造字面量）。
                        "style": "detailed", "mode": mode,
                        "version": 1, "createdAt": "",
                        "status": "complete",
                    })
            if md_entries:
                self._index["markdowns"][tx_id] = md_entries

            # 重建 process 条目
            proc_dir = d / "process"
            if proc_dir.exists():
                pr_entries = []
                for pr_file in sorted(proc_dir.glob("*.md")):
                    pr_id = f"pr_{hashlib.sha256(str(pr_file).encode()).hexdigest()[:12]}"
                    pr_entries.append({
                        "id": pr_id, "transcriptId": tx_id,
                        "mode": pr_file.stem, "engine": "",
                        "createdAt": "",
                    })
                if pr_entries:
                    self._index["processes"][tx_id] = pr_entries

        # 重建 conversations（不变）
        conversations_dir = self.root / "conversations"
        if conversations_dir.exists():
            for bucket in conversations_dir.iterdir():
                if bucket.is_dir():
                    for f in bucket.iterdir():
                        if f.suffix == ".json":
                            try:
                                data = json.loads(f.read_text(encoding="utf-8"))
                                self._index["conversations"].append({
                                    "id": data["id"],
                                    "title": data.get("title", ""),
                                    "model_id": data.get("model_id"),
                                    "created_at": data.get("created_at", ""),
                                    "updated_at": data.get("updated_at", ""),
                                })
                            except (json.JSONDecodeError, OSError):
                                logger.warning("跳过损坏对话文件: %s", f.name)

        self._save_index()

    # ── 工具方法 ──

    @staticmethod
    def _atomic_write(path: Path, content: str):
        """原子写入 + StoreError 保护"""
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(path)
        except OSError as e:
            raise StoreError.disk_full(detail=f"写入 {path.name} 失败: {e}") from e

    @staticmethod
    def _transcript_index_entry(data: dict) -> dict:
        meta = data.get("metadata", {})
        if not isinstance(meta, dict):
            meta = {}
        return {
            "id": data["id"],
            "title": data.get("title", ""),
            "sourceType": data.get("sourceType", ""),
            "sourceUrl": data.get("sourceUrl", ""),
            "sourceFileName": data.get("sourceFileName", ""),
            "sourceFilePath": data.get("sourceFilePath", ""),
            "engine": data.get("engine", ""),
            "engineVersion": data.get("engineVersion", ""),
            "language": meta.get("language", ""),
            "duration": meta.get("duration"),
            "coverUrl": meta.get("cover_url", ""),
            "sourceTitle": meta.get("source_title", ""),
            "contentHash": data.get("contentHash", ""),
            "metadata": meta,
            "tags": data.get("tags", []),
            "createdAt": data.get("createdAt", ""),
            # 文件夹归属。"" = 未分类（用空串而非 null，与上方 data.get(k, "") 惯例一致）。
            # ⚠️ 这个字段缺席时 save_transcript_full 会整体替换 index 条目——即「改标题
            # 顺手抹掉分类」。故 get_transcript() 必须一并返回它（见下）。
            "folderId": data.get("folderId", ""),
            # ── 合成文档（composite）的来源身份 ──
            # ⚠️ 这四个字段必须**三处同构**：本函数 / `_rebuild_index` 的内联字面量 /
            # `_get_or_create_doc_dir` 的占位条目（模式 #44）。只改前两处的实现一切正常，
            # **直到有人删掉 index.json**——重建后分类与来源全部归零。
            # 落库时由 `save_transcript` 从 rawText 的出处头解析后传入（正文是单一真相源）。
            "sourceDocIds": data.get("sourceDocIds", []),
            "sourceTitles": data.get("sourceTitles", []),
            "sourceHashes": data.get("sourceHashes", []),
            "metadataCorrupt": data.get("metadataCorrupt", False),
            "dir_name": data.get("dir_name", "") if "dir_name" in data else "",
        }

    @staticmethod
    def _sanitize_id(id: str) -> str:
        """清理 ID 中的路径遍历字符 —— **归一化器，不是校验器**（职责不同，不可合并）。

        ⚠️ 它**永不失败**：任何输入都会被变成某个可用的名字（兜底 `"unknown"`），
        因为它的调用场景是「构造磁盘路径」，此时抛异常等于让写入整个失败。这与
        `core.resource_id.is_valid_resource_id`（**校验器**，能返回 False，供读路径
        拒绝非法 ID）和 `export._safe_filename`（文件名清洗器）是三件事。

        硬合成一个的后果是双向的：要么写路径开始拒绝（用户改标题就报错），要么读路径
        继续放行 `..`（安全缺陷）。故只把**读路径**统一到校验器上。
        """
        sanitized = id.replace("\\", "/").replace("\0", "")
        sanitized = sanitized.rsplit("/", 1)[-1]
        sanitized = sanitized.rsplit("\\", 1)[-1]
        if sanitized.startswith("."):
            sanitized = "_" + sanitized[1:]
        return sanitized or "unknown"

    @staticmethod
    def _safe_load_json(path: Path, default=None):
        """安全加载 JSON，损坏时返回 default 并记录 warning"""
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("JSON 文件损坏: %s — %s", path.name, str(e)[:120])
            return default

    # ── 文档目录管理（per-document directory）──

    @staticmethod
    def _compute_dir_name(title: str, tx_id: str) -> str:
        """从标题和 ID 计算目录名: {sanitized_title}-{6位hex后缀}"""
        # 清洗 Windows 非法字符 + 控制字符（\n \r \t \x00 等）——标题含换行时
        # mkdir 抛 WinError 123（"文件名...语法不正确"），管道崩溃、SSE done 永不发
        sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', title)
        sanitized = sanitized.strip('. ')
        if len(sanitized) > 30:
            sanitized = sanitized[:30].rstrip('. ')
        if not sanitized:
            sanitized = "Untitled"
        suffix = tx_id[-6:]  # 末 6 位 hex（16M 空间）
        return f"{sanitized}-{suffix}"

    def get_doc_dir(self, tx_id: str) -> Optional[Path]:
        """从 index 查找文档目录。返回 None 表示不存在。

        ⚠️ **公开名**：`routers/` / `services/` 侧需要按 doc_id 定位文档目录（导出、
        附件服务、合成资产搬运）。它们此前**各自内联复制**了这段「遍历 index 找
        dir_name → 拼 root」的逻辑——同一判据多份副本正是项目铁律 #39/#54 反复栽的
        地方。新增调用方一律用这个公开名，不要再内联。

        ⚠️ 与 `_sanitize_id` / `files._check_tx_id` **职责不同，不可合并**：本函数只
        **查**（找不到返回 None），不校验也不归一化（见 `_sanitize_id` 的 docstring）。
        """
        for entry in self._index["transcripts"]:
            if entry["id"] == tx_id:
                dir_name = entry.get("dir_name", "")
                if dir_name:
                    d = self.root / dir_name
                    if d.exists():
                        return d
        return None

    # 旧私有名的兼容别名：历史外部调用方（routers/export.py 等）仍按私有名调用，
    # 改名不能一刀切。新代码请用 `get_doc_dir`。
    _get_doc_dir = get_doc_dir

    def _get_or_create_doc_dir(self, tx_id: str, title: str) -> Path:
        """获取已有或创建新文档目录。冲突时循环重试（追加序号）。"""
        existing = self.get_doc_dir(tx_id)
        if existing:
            return existing
        # 清洗 Windows 非法字符 + 控制字符（\n \r \t \x00 等）——标题含换行时
        # mkdir 抛 WinError 123（"文件名...语法不正确"），管道崩溃、SSE done 永不发
        sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', title)
        sanitized = sanitized.strip('. ')[:30].rstrip('. ') or "Untitled"
        base = f"{sanitized}-{tx_id[-6:]}"
        dir_name = base
        d = self.root / dir_name
        for i in range(5):
            try:
                d.mkdir(parents=True)
                break
            except FileExistsError:
                dir_name = f"{base}_{i + 1}"
                d = self.root / dir_name
        else:
            dir_name = f"{sanitized}-{tx_id[-12:]}"
            d = self.root / dir_name
            d.mkdir(parents=True, exist_ok=True)
        (d / "assets").mkdir(exist_ok=True)
        (d / "process").mkdir(exist_ok=True)
        with self._lock:
            for entry in self._index["transcripts"]:
                if entry["id"] == tx_id:
                    entry["dir_name"] = dir_name
                    self._save_index()
                    break
            else:
                # 目录已创建但 entry 尚未写入（ingest 流程先建目录、后 save_transcript）：
                # 立即占位记录 dir_name，防止后续 _get_or_create_doc_dir 重复创建空壳目录。
                # 其余字段由 save_transcript 的 upsert 补全。
                self._index["transcripts"].append({
                    "id": tx_id, "dir_name": dir_name,
                    "createdAt": datetime.now().isoformat(),
                    # 来源身份默认值（模式 #44 的第三处手工构造点）：占位条目在
                    # ingest 期间可能被 list_transcripts 读到，缺键会让前端拿到 undefined。
                    # 真实值由随后的 save_transcript upsert 覆盖。
                    "sourceDocIds": [], "sourceTitles": [], "sourceHashes": [],
                    "metadataCorrupt": False,
                })
                self._save_index()
        return d

    def _rename_doc_dir(self, tx_id: str, new_title: str) -> Optional[Path]:
        """标题变更时重命名文档目录。返回新 Path 或 None。"""
        old_dir = self.get_doc_dir(tx_id)
        if not old_dir:
            return None
        new_dir_name = self._compute_dir_name(new_title, tx_id)
        new_dir = self.root / new_dir_name
        if old_dir == new_dir:
            return old_dir
        if new_dir.exists():
            return old_dir  # 目标已存在，保持旧名
        try:
            os.replace(old_dir, new_dir)
        except OSError:
            return old_dir  # 重命名失败，保持旧名
        with self._lock:
            for entry in self._index["transcripts"]:
                if entry["id"] == tx_id:
                    entry["dir_name"] = new_dir_name
                    self._save_index()
                    break
        return new_dir

    # ── RawTranscript CRUD ──

    def save_transcript(self, tx: RawTranscript, skip_dedup: bool = False) -> bool:
        """写入新文档（正文 + 索引条目）。返回 False 表示被内容去重拦下。

        `skip_dedup=True`：**合成文档专用**。三条理由——
        ① 去重是为「重复导入同一文件」设计的，合并 N 篇是用户的显式动作；
        ② 同一批源重复合并会得到**逐字相同**的 rawText（含同一份出处头），
           去重会静默复用旧文档——既违背「新建文档」的可见语义，又抛出陈旧快照；
        ③ 出处头保证合成文档与任何普通文档不可能碰撞。
        """
        if not tx.content_hash:
            tx.content_hash = hashlib.sha256(tx.raw_text.encode()).hexdigest()
        if not skip_dedup:
            existing = self.find_by_hash(tx.content_hash)
            if existing:
                logger.info(f"内容重复，拒绝写入: {tx.title} == {existing.get('title')}")
                return False

        doc_dir = self._get_or_create_doc_dir(tx.id, tx.title)
        # 写入 transcript.md（纯 rawText）
        self._atomic_write(doc_dir / "transcript.md", tx.raw_text)

        # 出处头（合成文档）：从 **rawText 本身**解析，而不是另开一个入参——
        # 「rawText == 模型输入 == 用户所见」是本设计的前提，身份多一个来源就多一处漂移。
        # 无头的普通文档走 (None, False) 分支，字段取默认值，零行为变化。
        head, head_corrupt = parse_meta(tx.raw_text)
        head = head or {}

        data = {
            "id": tx.id,
            "title": tx.title,
            # header 优先：合成端点即使漏传 source_type，也不会让文档丢掉 composite 身份
            "sourceType": head.get("sourceType") or (
                tx.source_type.value if hasattr(tx.source_type, 'value') else tx.source_type),
            "sourceUrl": tx.source_url,
            "sourceFileName": tx.source_file_name,
            "sourceFilePath": tx.source_file_path,
            "engine": tx.engine,
            "engineVersion": tx.engine_version,
            "contentHash": tx.content_hash,
            "metadata": _serialize_metadata(tx.metadata),
            "tags": tx.tags,
            "createdAt": tx.created_at or datetime.now().isoformat(),
            "sourceDocIds": list(head.get("sourceDocIds") or []),
            "sourceTitles": list(head.get("sourceTitles") or []),
            "sourceHashes": list(head.get("sourceHashes") or []),
            "metadataCorrupt": head_corrupt,
        }
        with self._lock:
            entry = self._transcript_index_entry(data)
            entry["dir_name"] = doc_dir.name
            # upsert：占位 entry（_get_or_create_doc_dir 创建）已存在时原地补全，避免重复 append
            for i, e in enumerate(self._index["transcripts"]):
                if e["id"] == tx.id:
                    self._index["transcripts"][i] = entry
                    break
            else:
                self._index["transcripts"].append(entry)
            self._save_index()
        return True

    def get_transcript(self, id: str) -> Optional[dict]:
        """从 index 读元数据 + 从 transcript.md 读 rawText。"""
        entry = next((e for e in self._index["transcripts"] if e["id"] == id), None)
        if not entry:
            return None
        # 目录解析**不复刻**一份在这里：`get_doc_dir` 是唯一判据（它内部已处理
        # dir_name 缺失与目录不存在两种缺失形态）。此前这里内联了同一段查找，
        # 两处对「dir_name 为空」的处理迟早会漂移。
        doc_dir = self.get_doc_dir(id)
        if not doc_dir:
            return None
        tx_path = doc_dir / "transcript.md"
        if not tx_path.exists():
            return None
        raw_text = tx_path.read_text(encoding="utf-8")

        # ── 出处头**优先于 index**（设计 §4.2「transcript.md 是单一真相源」）──
        # 索引重建后 id 会被重推、sourceType 被写死为空、非内容派生字段全丢；
        # 只有 rawText 的第一行能还原合成文档的身份。故以 header 为准，index 兜底。
        # ⚠️ 损坏时**不猜**（不按标题反查来源）——一份带着错误来源清单的文档
        # 比一份标着 metadata_corrupt 的文档坏得多（项目「失败显式化」原则）。
        head, head_corrupt = parse_meta(raw_text)
        head = head or {}

        # 从 index entry 重建完整 dict（保持调用方兼容）
        meta = entry.get("metadata", {})
        return {
            "id": entry["id"],
            "title": entry.get("title", ""),
            "sourceType": head.get("sourceType") or entry.get("sourceType", ""),
            "sourceUrl": entry.get("sourceUrl", ""),
            "sourceFileName": entry.get("sourceFileName", ""),
            "sourceFilePath": entry.get("sourceFilePath", ""),
            "engine": entry.get("engine", ""),
            "engineVersion": entry.get("engineVersion", ""),
            "rawText": raw_text,
            "contentHash": entry.get("contentHash", ""),
            "metadata": meta if isinstance(meta, dict) else {},
            "tags": entry.get("tags", []),
            "createdAt": entry.get("createdAt", ""),
            # ⚠️ 必须返回：PATCH /transcripts 读的就是这个 dict，改完标题/标签后整体
            # save_transcript_full 回去；少了它，改标题就等于把文档移出文件夹。
            "folderId": entry.get("folderId", ""),
            # 同理必须返回（同一个整体替换的坑），合成来源身份四条一并返回
            "sourceDocIds": list(head.get("sourceDocIds") or entry.get("sourceDocIds") or []),
            "sourceTitles": list(head.get("sourceTitles") or entry.get("sourceTitles") or []),
            "sourceHashes": list(head.get("sourceHashes") or entry.get("sourceHashes") or []),
            "metadataCorrupt": head_corrupt or bool(entry.get("metadataCorrupt", False)),
        }

    def delete_transcript(self, id: str) -> bool:
        """删除文档目录 + index 清理。"""
        doc_dir = self.get_doc_dir(id)
        if doc_dir and doc_dir.exists():
            try:
                shutil.rmtree(doc_dir)
            except OSError as e:
                logger.warning("删除文档目录失败: %s — %s", doc_dir, e)
        with self._lock:
            self._index["transcripts"] = [
                e for e in self._index["transcripts"] if e["id"] != id
            ]
            self._index["markdowns"].pop(id, None)
            self._index["processes"].pop(id, None)
            self._save_index()
        return True

    def list_transcripts(self, filters: dict | None = None) -> list[dict]:
        with self._lock:
            dirty = False
            valid = []
            for entry in self._index["transcripts"]:
                dir_name = entry.get("dir_name", "")
                if dir_name and (self.root / dir_name / "transcript.md").exists():
                    valid.append(entry)
                else:
                    logger.warning(f"索引条目 {entry['id']} 对应目录不存在，已移除")
                    dirty = True
            if dirty:
                self._index["transcripts"] = valid
                self._save_index()

        result = self._index["transcripts"]
        if filters:
            if "sourceType" in filters:
                result = [e for e in result if e.get("sourceType") == filters["sourceType"]]
            if "tag" in filters:
                result = [e for e in result if filters["tag"] in e.get("tags", [])]
            if "query" in filters:
                q = filters["query"].lower()
                result = [e for e in result if q in e.get("title", "").lower()]
            if "folderId" in filters:
                # 用 `in` 判定而非 `is not None`：空串是**有效筛选值**（只看未分类），
                # 与「不传该键 = 不过滤」必须区分开。路由层据此只在显式传参时塞进来。
                #
                # ⚠️ 悬空 folderId（文件夹已不存在，如手工改坏 index.json）必须**归化为未分类**。
                # 直接拿原值比较的话，该文档既不属于任何文件夹、也不出现在「未分类」里——
                # 等于从**所有筛选视图**中消失（只能在「全部」里找到），用户会以为文档丢了。
                known = {f["id"] for f in self._index.get("folders", [])}
                result = [
                    e for e in result
                    if (e.get("folderId", "") if e.get("folderId", "") in known else "")
                    == filters["folderId"]
                ]
        return sorted(result, key=lambda e: e.get("createdAt", ""), reverse=True)

    def find_by_url(self, url: str) -> Optional[dict]:
        if not url:
            return None
        for e in self._index["transcripts"]:
            if e.get("sourceUrl") == url:
                return e
        return None

    def find_by_hash(self, hash_val: str) -> Optional[dict]:
        for e in self._index["transcripts"]:
            if e.get("contentHash") == hash_val:
                return e
        return None

    # ── Transcript 完整保存（更新）──

    def save_transcript_full(self, tx_data: dict) -> bool:
        """保存完整 transcript — 覆盖 transcript.md + 更新 index。
        用于已有文档的更新（retranscribe, title/tag PATCH, _auto_title）。
        """
        tx_id = tx_data["id"]
        doc_dir = self.get_doc_dir(tx_id)
        if not doc_dir:
            return False

        # 标题变更 → 重命名目录
        new_title = tx_data.get("title", "")
        if new_title:
            new_dir_name = self._compute_dir_name(new_title, tx_id)
            if new_dir_name != doc_dir.name:
                doc_dir = self._rename_doc_dir(tx_id, new_title) or doc_dir

        # 写入 transcript.md
        raw_text = tx_data.get("rawText", "")
        if raw_text:
            self._atomic_write(doc_dir / "transcript.md", raw_text)

        with self._lock:
            new_entry = self._transcript_index_entry(tx_data)
            new_entry["dir_name"] = doc_dir.name
            for i, entry in enumerate(self._index["transcripts"]):
                if entry["id"] == tx_id:
                    # 这里是**整体替换** index 条目，故任何「新 entry 里没有、旧 entry 里
                    # 有」的字段都会被静默抹掉。folderId 不属于「改标题/标签」的语义，
                    # 调用方没显式传就保持原值（分类只由文件夹 API 改动）。
                    #
                    # ⚠️ 合成来源身份同理，且这里**是唯一的静默数据损坏入口**：
                    # PATCH /transcripts、retranscribe、_auto_title 三个调用方都不会带
                    # 这四个字段——漏守卫 = 改一次标题就把来源清单抹成 []，而**零报错**。
                    if "folderId" not in tx_data:
                        new_entry["folderId"] = entry.get("folderId", "")
                    for _f in ("sourceDocIds", "sourceTitles", "sourceHashes"):
                        if _f not in tx_data:
                            new_entry[_f] = entry.get(_f, [])
                    if "metadataCorrupt" not in tx_data:
                        new_entry["metadataCorrupt"] = entry.get("metadataCorrupt", False)
                    self._index["transcripts"][i] = new_entry
                    self._save_index()
                    return True
        return False

    # ── 文件夹（单层逻辑分类）──
    #
    # 设计约束：文件夹是**纯逻辑分类**，永不映射为磁盘目录。
    # 每个文档已有独立目录 {sanitized_title}-{hex6}/（含 transcript.md / assets /
    # process / 两版制产物），把文件夹做成目录会同时破坏目录命名规则、assets 相对
    # 引用、两版制——移动文档要重写全部 index 路径。故 folders 只活在 index.json 里。

    def list_folders(self) -> list[dict]:
        """列出全部文件夹，附带各自文档数。count 现算（文件夹数远少于文档数）。"""
        with self._lock:
            folders = list(self._index.get("folders", []))
            known = {f["id"] for f in folders}
            counts: dict[str, int] = {}
            for e in self._index["transcripts"]:
                fid = e.get("folderId", "")
                # 悬空 folderId 计入不了任何 count——这里的 `in known` 只是剪枝。
                # 真正保证「悬空 = 未分类」的是 list_transcripts 里的归化（见那里）。
                if fid in known:
                    counts[fid] = counts.get(fid, 0) + 1
        return [{**f, "count": counts.get(f["id"], 0)} for f in folders]

    def folder_exists(self, folder_id: str) -> bool:
        return any(f["id"] == folder_id for f in self._index.get("folders", []))

    def create_folder(self, name: str) -> dict:
        """新建文件夹。允许重名——id 是身份，名字只是给用户看的标签。"""
        name = (name or "").strip()
        if not name:
            raise AppError("FOLDER_NAME_EMPTY", "文件夹名不能为空",
                           recoverable=True, status_code=400)
        entry = {
            "id": f"fd_{uuid4().hex[:12]}",
            "name": name,
            "createdAt": datetime.now().isoformat(),
        }
        with self._lock:
            self._index.setdefault("folders", []).append(entry)
            self._save_index()
        return entry

    def rename_folder(self, folder_id: str, name: str) -> Optional[dict]:
        """重命名。返回更新后的条目；文件夹不存在返回 None（由路由层转 404）。"""
        name = (name or "").strip()
        if not name:
            raise AppError("FOLDER_NAME_EMPTY", "文件夹名不能为空",
                           recoverable=True, status_code=400)
        with self._lock:
            for f in self._index.get("folders", []):
                if f["id"] == folder_id:
                    f["name"] = name
                    self._save_index()
                    return dict(f)
        return None

    def delete_folder(self, folder_id: str) -> Optional[int]:
        """删除文件夹，其下文档回落未分类。返回迁走的文档数；不存在返回 None。

        ⚠️ 绝不删文档——文件夹只是分类标签。搬移与删行在同一锁区内完成，
        中途崩溃也不会留下悬空引用（读路径另有兜底，见 list_transcripts）。
        """
        with self._lock:
            folders = self._index.get("folders", [])
            idx = next((i for i, f in enumerate(folders) if f["id"] == folder_id), None)
            if idx is None:
                return None
            moved = 0
            for e in self._index["transcripts"]:
                if e.get("folderId") == folder_id:
                    e["folderId"] = ""
                    moved += 1
            del folders[idx]
            self._save_index()
        return moved

    # ── 批量操作（一次加锁、一次写盘）──
    #
    # 相对「N 次 PATCH /transcripts/{id}」= N 次全量 index.json 写盘，
    # 这里是本组端点的核心收益（500 个文档 = 1 次写盘而非 500 次）。

    def set_transcripts_folder(self, ids: list[str], folder_id: str) -> int:
        """批量移动文档到指定文件夹（""=移出到未分类）。返回命中数。"""
        targets = set(ids)
        with self._lock:
            n = 0
            for e in self._index["transcripts"]:
                if e["id"] in targets:
                    e["folderId"] = folder_id
                    n += 1
            if n:
                self._save_index()
        return n

    def update_transcripts_tags(self, ids: list[str], tags: list[str],
                               mode: str = "add") -> int:
        """批量打标签。mode="add" 并入去重 / mode="replace" 整体替换。返回命中数。"""
        targets = set(ids)
        with self._lock:
            n = 0
            for e in self._index["transcripts"]:
                if e["id"] not in targets:
                    continue
                if mode == "replace":
                    e["tags"] = list(tags)
                else:
                    merged = list(e.get("tags", []))
                    for t in tags:
                        if t not in merged:
                            merged.append(t)
                    e["tags"] = merged
                n += 1
            if n:
                self._save_index()
        return n

    # ── MarkdownView CRUD ──

    def save_markdown(
        self,
        mv: MarkdownView,
        file_meta: dict | None = None,
        index_extra: dict | None = None,
    ) -> bool:
        """两版制：{mode}.md → 降级为 {mode}.prev.md → 写新版。

        ⚠️ **两个 dict 参数去向完全不同，接错了两边都不报错**（最容易犯也最静默）：

        - `file_meta` → 写进**文件首行**的 `<!-- limo-meta ... -->` 自证头。
          它随文件走，是产物离开 `.limo` 之后**唯一**还能自证的载体。
        - `index_extra` → 并进 **index.json 的条目**（如 `{"edited": True}`）。
          它只在本地库里有效，删掉 `index.json` 重建即丢。

        参数名刻意各带方向词（此前只有一个裸 `extra`，语义是后者）——「meta 进了
        index / extra 进了文件头」是最容易犯且**不产生任何报错**的错。
        """
        doc_dir = self.get_doc_dir(mv.transcript_id)
        if not doc_dir:
            logger.warning("save_markdown: 文档目录不存在 tx=%s", mv.transcript_id)
            return False

        mode = mv.mode.value if hasattr(mv.mode, 'value') else mv.mode
        latest = doc_dir / f"{mode}.md"

        # 两版制：最新版降级为 .prev.md
        # ⚠️ `.prev.md` 是**完整产物**（带它自己那一版的自证头），降级不需要剥头
        if latest.exists():
            prev = doc_dir / f"{mode}.prev.md"
            try:
                os.replace(latest, prev)
            except OSError:
                pass  # 降级失败不阻塞，直接覆盖写

        content = mv.content
        if file_meta:
            # dump_meta 返回单行；空行把注释与正文隔开（也匹配 strip_meta 的回剥规则）
            content = f"{dump_meta(file_meta)}\n\n{content}"
        self._atomic_write(latest, content)

        with self._lock:
            entry = {
                "id": mv.id, "transcriptId": mv.transcript_id,
                "style": mv.style.value if hasattr(mv.style, 'value') else mv.style,
                "mode": mode,
                "version": mv.version, "createdAt": mv.created_at,
                "status": getattr(mv, 'status', 'complete'),
                **(index_extra or {}),
            }
            existing = self._index["markdowns"].get(mv.transcript_id, [])
            if isinstance(existing, dict):
                existing = [existing]
            same_mode = [e for e in existing if e.get("mode") == mode]
            top = max((e.get("version", 0) for e in same_mode), default=0)
            entry["version"] = top + 1
            existing.append(entry)
            # 保留最近 2 版（清理超出两版的旧 index 条目）
            same_mode = [e for e in existing if e.get("mode") == mode]
            same_mode.sort(key=lambda e: e.get("version", 0), reverse=True)
            for old in same_mode[2:]:
                existing.remove(old)
            self._index["markdowns"][mv.transcript_id] = existing
            self._save_index()
        return True

    def get_markdown(self, id: str) -> Optional[str]:
        """按 id 查找 markdown 条目 -> 判断是否最新版 -> 读 .md 或 .prev.md。"""
        target_entry = None
        for entries in self._index["markdowns"].values():
            for entry in entries if isinstance(entries, list) else [entries]:
                if entry.get("id") == id:
                    target_entry = entry
                    break
            if target_entry:
                break
        if not target_entry:
            return None

        tx_id = target_entry.get("transcriptId", "")
        mode = target_entry.get("mode", "")
        doc_dir = self.get_doc_dir(tx_id)
        if not doc_dir:
            return None

        all_for_tx = self._index["markdowns"].get(tx_id, [])
        same_mode = [e for e in all_for_tx if e.get("mode") == mode]
        max_version = max((e.get("version", 0) for e in same_mode), default=0)
        if target_entry.get("version", 0) < max_version:
            path = doc_dir / f"{mode}.prev.md"
        else:
            path = doc_dir / f"{mode}.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    def list_markdown_entries(self, transcript_id: str) -> list[dict]:
        """该文档全部 Markdown 产物的 **index 条目**（版本降序，**不读盘**）。

        ⚠️ 与 `get_markdowns_by_transcript` 的差别只有「要不要内容」：后者为每个条目
        拼磁盘路径并 `read_text` —— 内容才是它的目的。而「这篇有哪些产物」是个
        元数据问题，让列清单也去读一遍全文，等于每问一次「有什么」就把整库读一遍。
        """
        entries = self._index["markdowns"].get(transcript_id, [])
        if isinstance(entries, dict):
            entries = [entries]
        return sorted((dict(e) for e in entries),
                      key=lambda e: e.get("version", 0), reverse=True)

    def get_markdowns_by_transcript(self, transcript_id: str,
                                     mode: str = "", version: int | None = None) -> list[dict]:
        """获取指定 transcript 的 Markdown，支持 mode/version 过滤。
        按版本判断：最新版读 {mode}.md，上一版读 {mode}.prev.md。
        """
        entries = self._index["markdowns"].get(transcript_id, [])
        if isinstance(entries, dict):
            entries = [entries]
        if mode:
            entries = [e for e in entries if e.get("mode") == mode]
        if version is not None:
            entries = [e for e in entries if e.get("version") == version]

        doc_dir = self.get_doc_dir(transcript_id)
        if not doc_dir:
            return []

        all_for_tx = self._index["markdowns"].get(transcript_id, [])
        mode_max = {}
        for e in all_for_tx:
            m = e.get("mode", "")
            v = e.get("version", 0)
            if v > mode_max.get(m, 0):
                mode_max[m] = v

        result = []
        for meta in entries:
            md_mode = meta.get("mode", "")
            is_latest = meta.get("version", 0) >= mode_max.get(md_mode, 0)
            suffix = "" if is_latest else ".prev"
            path = doc_dir / f"{md_mode}{suffix}.md"
            if path.exists():
                text = path.read_text(encoding="utf-8")
                result.append({**meta, "content": text})
        return sorted(result, key=lambda e: e.get("version", 0), reverse=True)

    # ── ProcessResult CRUD ──

    def save_process_result(self, pr: ProcessResult, file_meta: dict | None = None) -> str:
        """保存处理结果到 {doc_dir}/process/{mode}.md，同 mode 覆盖。

        `file_meta` 与 `save_markdown` 同义：写进**文件首行**的自证头。这里没有
        `index_extra` 对应物——process 的 index 条目字段固定，不接受额外键。
        """
        doc_dir = self.get_doc_dir(pr.transcript_id)
        if not doc_dir:
            logger.warning("save_process_result: 文档目录不存在 tx=%s", pr.transcript_id)
            return pr.id
        proc_dir = doc_dir / "process"
        proc_dir.mkdir(exist_ok=True)
        path = proc_dir / f"{pr.mode}.md"
        content = pr.content
        if file_meta:
            content = f"{dump_meta(file_meta)}\n\n{content}"
        self._atomic_write(path, content)
        with self._lock:
            entry = {
                "id": pr.id, "transcriptId": pr.transcript_id,
                "mode": pr.mode, "engine": pr.engine,
                "createdAt": pr.created_at,
            }
            existing = self._index["processes"].get(pr.transcript_id, [])
            existing = [e for e in existing if e.get("mode") != pr.mode]
            existing.append(entry)
            existing.sort(key=lambda e: e.get("createdAt", ""))
            self._index["processes"][pr.transcript_id] = existing
            self._save_index()
        return pr.id

    def get_process_results(self, transcript_id: str) -> list[dict]:
        """获取某个 transcript 的所有处理结果（元数据列表，不含内容）"""
        return self._index["processes"].get(transcript_id, [])

    def get_process_result(self, id: str) -> Optional[str]:
        """获取单个处理结果的内容 — 从 index 查 mode → 读磁盘。"""
        for entries in self._index["processes"].values():
            for entry in entries if isinstance(entries, list) else [entries]:
                if entry.get("id") == id:
                    tx_id = entry.get("transcriptId", "")
                    mode = entry.get("mode", "")
                    doc_dir = self.get_doc_dir(tx_id)
                    if not doc_dir:
                        return None
                    path = doc_dir / "process" / f"{mode}.md"
                    if path.exists():
                        return path.read_text(encoding="utf-8")
        return None

    # ── EPUB 解读（一键全解读 — 独立于 save_markdown 本体的片段/任务读写）──

    def _epub_chapters_dir(self, tx_id: str) -> Optional[Path]:
        """EPUB 章节解读片段目录（文档不存在返回 None）"""
        doc_dir = self.get_doc_dir(tx_id)
        if not doc_dir:
            return None
        return doc_dir / "epub_chapters"

    def save_epub_chapter(self, tx_id: str, chapter_index: int,
                          title: str, content: str) -> bool:
        """保存单章解读片段到 {doc_dir}/epub_chapters/{index}.md（含标题头）。

        每章成功即落盘，是逐章解读的唯一写点；阶段 2 归并和最终 polished
        组装都通过 list_epub_chapters 从这里读回（不做额外中间文件）。
        """
        d = self._epub_chapters_dir(tx_id)
        if not d:
            logger.warning("save_epub_chapter: 文档目录不存在 tx=%s", tx_id)
            return False
        d.mkdir(parents=True, exist_ok=True)
        body = content.strip()
        if not body:
            return False
        # LLM 输出未自带**一级**标题头时补一章标题。判断用 "# "（# + 空格）而非
        # startswith("#")——LLM 输出常以 "## 本章概述"（二级标题）开头，会被误判为已带标题
        if title and not body.startswith("# "):
            body = f"# {title}\n\n{body}"
        self._atomic_write(d / f"{chapter_index}.md", body)
        return True

    def list_epub_chapters(self, tx_id: str) -> list[str]:
        """按 index 升序读回全部解读片段（阶段 2 归并输入 + 最终 polished 组装）。"""
        d = self._epub_chapters_dir(tx_id)
        if not d or not d.exists():
            return []
        items = []
        for p in d.glob("*.md"):
            try:
                items.append((int(p.stem), p))
            except ValueError:
                continue
        items.sort(key=lambda x: x[0])
        return [p.read_text(encoding="utf-8") for _, p in items]

    def clear_epub_chapters(self, tx_id: str) -> bool:
        """清空解读片段目录（整轮重跑 / 全新开始时调用）。"""
        d = self._epub_chapters_dir(tx_id)
        if not d or not d.exists():
            return False
        try:
            shutil.rmtree(d)
            return True
        except OSError as e:
            logger.warning("清空 epub_chapters 失败: %s — %s", d, e)
            return False

    def save_epub_task(self, tx_id: str, state: dict) -> bool:
        """保存 epub 任务状态到 {doc_dir}/epub_task.json。"""
        doc_dir = self.get_doc_dir(tx_id)
        if not doc_dir:
            return False
        self._atomic_write(doc_dir / "epub_task.json",
                           json.dumps(state, ensure_ascii=False, indent=2))
        return True

    def load_epub_task(self, tx_id: str) -> Optional[dict]:
        """读取 epub 任务状态（不存在返回 None，不误报损坏；损坏时 _safe_load_json 兜底）。"""
        doc_dir = self.get_doc_dir(tx_id)
        if not doc_dir:
            return None
        path = doc_dir / "epub_task.json"
        if not path.exists():
            return None
        return self._safe_load_json(path)

    def clear_epub_task(self, tx_id: str) -> bool:
        """删除 epub 任务状态文件。"""
        doc_dir = self.get_doc_dir(tx_id)
        if not doc_dir:
            return False
        p = doc_dir / "epub_task.json"
        try:
            if p.exists():
                p.unlink()
            return True
        except OSError:
            return False

    # ── 缓存（按 contentHash） ──

    def cache_put(self, content_hash: str, data: dict):
        prefix = content_hash[:2]
        d = self.root / "cache" / prefix
        d.mkdir(parents=True, exist_ok=True)
        self._atomic_write(
            d / f"{content_hash}.json",
            json.dumps(data, ensure_ascii=False, indent=2),
        )

    def cache_get(self, content_hash: str) -> Optional[dict]:
        path = self.root / "cache" / content_hash[:2] / f"{content_hash}.json"
        if not path.exists():
            return None
        return self._safe_load_json(path)

    # ── 对话 (Conversation) CRUD ──

    def _conv_path(self, conv_id: str) -> Path:
        safe = self._sanitize_id(conv_id)
        d = self.root / "conversations" / safe[:2]
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{safe}.json"

    def save_conversation(self, conv) -> str:
        """保存/更新对话。返回 id。"""
        data = conv.to_dict() if hasattr(conv, 'to_dict') else conv
        path = self._conv_path(data["id"])
        self._atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))
        with self._lock:
            existing = [e for e in self._index["conversations"]
                       if e["id"] != data["id"]]
            existing.append({
                "id": data["id"], "title": data["title"],
                "model_id": data.get("model_id"),
                "created_at": data["created_at"], "updated_at": data["updated_at"],
                "archived": bool(data.get("archived", False)),
            })
            existing.sort(key=lambda e: e.get("updated_at", ""), reverse=True)
            self._index["conversations"] = existing
            self._save_index()
        return data["id"]

    def get_conversation(self, conv_id: str) -> dict | None:
        path = self._conv_path(conv_id)
        return self._safe_load_json(path)

    def list_conversations(self) -> list[dict]:
        return sorted(self._index["conversations"],
                     key=lambda e: e.get("updated_at", ""), reverse=True)

    def delete_conversation(self, conv_id: str) -> bool:
        path = self._conv_path(conv_id)
        if path.exists():
            path.unlink()
        with self._lock:
            self._index["conversations"] = [
                e for e in self._index["conversations"] if e["id"] != conv_id
            ]
            self._save_index()
        return True

    def rename_conversation(self, conv_id: str, new_title: str) -> bool:
        data = self.get_conversation(conv_id)
        if not data:
            return False
        data["title"] = new_title
        data["updated_at"] = datetime.now().isoformat()
        self.save_conversation(data)
        return True

    # ── 统计 ──

    @property
    def count(self) -> int:
        return len(self._index["transcripts"])
