"""
文件系统 + JSON 索引存储
参照 system-design-spec.md 四.5

v2: 统一索引 — transcripts / markdowns / processes / conversations
    合并为一个 index.json，一次原子写入消除跨文件不一致。
"""

import hashlib
import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.core.types import RawTranscript, MarkdownView, ProcessResult, Style, GenerateMode
from app.core.errors import StoreError

logger = logging.getLogger(__name__)

INDEX_VERSION = 2


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
        "markdowns": {},
        "processes": {},
        "conversations": [],
    }


class Store:
    """单用户文件系统存储。v2 统一索引。"""

    def __init__(self, data_dir: str):
        self.root = Path(data_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "logs").mkdir(exist_ok=True)
        self._index: dict = _empty_index()
        self._index_path = self.root / "index.json"
        self._lock = threading.Lock()
        self._load_index()

    # ── 索引加载 / 保存 / 迁移 ──

    def _load_index(self):
        """加载统一索引。无文件或旧格式 → 自动迁移。"""
        if self._index_path.exists():
            try:
                data = json.loads(self._index_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                logger.warning("index.json 损坏，尝试从旧文件恢复...")
                data = None
            if data and isinstance(data, dict) and data.get("version", 0) >= 2:
                # v2 格式：补全缺失的子键（向前兼容）
                default = _empty_index()
                for key in default:
                    if key not in data:
                        data[key] = default[key]
                self._index = data
                return
            # 旧格式（v1 list）或损坏 → 迁移
            logger.info("检测到 v1 索引格式，自动迁移到 v2...")
            if data and isinstance(data, list):
                self._index = {"version": INDEX_VERSION, "transcripts": data}
            else:
                self._index = _empty_index()
        else:
            self._index = _empty_index()

        # 尝试从旧 4 文件加载剩余子索引
        migrated = self._migrate_from_v1()
        if migrated:
            self._save_index()
            logger.info("v1 → v2 迁移完成，共 %d transcripts, %d conversations",
                        len(self._index["transcripts"]), len(self._index["conversations"]))

    def _migrate_from_v1(self) -> bool:
        """从 v1 的 4 个独立 JSON 文件加载并合并到统一索引。返回 True 表示有数据迁移。"""
        migrated = False

        # markdown_index.json
        md_path = self.root / "markdown_index.json"
        if md_path.exists():
            try:
                md_data = json.loads(md_path.read_text(encoding="utf-8"))
                if md_data:
                    self._index["markdowns"] = md_data
                    migrated = True
            except (json.JSONDecodeError, OSError):
                logger.warning("markdown_index.json 损坏，跳过")

        # process_index.json
        pr_path = self.root / "process_index.json"
        if pr_path.exists():
            try:
                pr_data = json.loads(pr_path.read_text(encoding="utf-8"))
                if pr_data:
                    self._index["processes"] = pr_data
                    migrated = True
            except (json.JSONDecodeError, OSError):
                logger.warning("process_index.json 损坏，跳过")

        # conversations_index.json
        conv_path = self.root / "conversations_index.json"
        if conv_path.exists():
            try:
                conv_data = json.loads(conv_path.read_text(encoding="utf-8"))
                if conv_data:
                    self._index["conversations"] = conv_data
                    migrated = True
            except (json.JSONDecodeError, OSError):
                logger.warning("conversations_index.json 损坏，跳过")

        # 如果旧 index.json 是 list 格式，已在 _load_index 中处理
        return migrated

    def _save_index(self):
        """原子写入统一 index.json"""
        self._atomic_write(
            self._index_path,
            json.dumps(self._index, ensure_ascii=False, indent=2),
        )

    def _rebuild_index(self):
        """遍历所有数据目录重建索引（故障恢复）"""
        self._index = _empty_index()
        transcripts_dir = self.root / "transcripts"
        markdown_dir = self.root / "markdown"
        process_dir = self.root / "process"
        conversations_dir = self.root / "conversations"

        # 重建 transcripts
        if transcripts_dir.exists():
            for bucket in transcripts_dir.iterdir():
                if bucket.is_dir():
                    for f in bucket.iterdir():
                        if f.suffix == ".json":
                            try:
                                data = json.loads(f.read_text(encoding="utf-8"))
                                self._index["transcripts"].append(
                                    self._transcript_index_entry(data))
                            except (json.JSONDecodeError, OSError):
                                logger.warning("跳过损坏文件: %s", f.name)

        # 重建 markdowns（从磁盘文件反推 index 条目）
        if markdown_dir.exists():
            md_entries: dict[str, list[dict]] = {}
            for bucket in markdown_dir.iterdir():
                if bucket.is_dir():
                    for f in bucket.iterdir():
                        if f.suffix == ".md":
                            md_id = f.stem
                            content = f.read_text(encoding="utf-8")
                            # 尽可能从内容推断（保守估计 version=1, status="complete"）
                            tx_id = ""  # 无法从文件反推 transcript_id
                            md_entries.setdefault(tx_id, []).append({
                                "id": md_id, "transcriptId": tx_id,
                                "style": "detailed", "mode": "notes",
                                "version": 1, "createdAt": "",
                                "status": "complete",
                            })
            if md_entries:
                self._index["markdowns"] = md_entries

        # 重建 processes（从磁盘文件反推）
        if process_dir.exists():
            pr_entries: dict[str, list[dict]] = {}
            for bucket in process_dir.iterdir():
                if bucket.is_dir():
                    for f in bucket.iterdir():
                        if f.suffix == "":  # process 文件无扩展名
                            pr_id = f.name
                            pr_entries.setdefault("", []).append({
                                "id": pr_id, "transcriptId": "",
                                "mode": "unknown", "engine": "",
                                "createdAt": "",
                            })
            if pr_entries:
                self._index["processes"] = pr_entries

        # 重建 conversations
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
        return {
            "id": data["id"],
            "title": data.get("title", ""),
            "sourceType": data.get("sourceType", ""),
            "sourceUrl": data.get("sourceUrl", ""),
            "sourceFileName": data.get("sourceFileName", ""),
            "engine": data.get("engine", ""),
            "language": data.get("metadata", {}).get("language", ""),
            "duration": data.get("metadata", {}).get("duration"),
            "coverUrl": data.get("metadata", {}).get("cover_url", ""),
            "sourceTitle": data.get("metadata", {}).get("source_title", ""),
            "contentHash": data.get("contentHash", ""),
            "tags": data.get("tags", []),
            "createdAt": data.get("createdAt", ""),
        }

    @staticmethod
    def _sanitize_id(id: str) -> str:
        """清理 ID 中的路径遍历字符，仅保留安全字符"""
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

    def _path_for_id(self, id: str, kind: str = "transcripts") -> Path:
        safe_id = self._sanitize_id(id)
        prefix = safe_id[:2]
        if "/" in prefix or "\\" in prefix:
            prefix = "xx"
        d = self.root / kind / prefix
        d.mkdir(parents=True, exist_ok=True)
        ext = ".json" if kind in ("transcripts", "conversations") else ".md"
        if kind == "process":
            ext = ""  # process 文件无扩展名
        return d / f"{safe_id}{ext}"

    # ── RawTranscript CRUD ──

    def save_transcript(self, tx: RawTranscript) -> bool:
        if not tx.content_hash:
            tx.content_hash = hashlib.sha256(tx.raw_text.encode()).hexdigest()
        existing = self.find_by_hash(tx.content_hash)
        if existing:
            logger.info(f"内容重复，拒绝写入: {tx.title} == {existing.get('title')}")
            return False

        data = {
            "id": tx.id,
            "title": tx.title,
            "sourceType": tx.source_type.value if hasattr(tx.source_type, 'value') else tx.source_type,
            "sourceUrl": tx.source_url,
            "sourceFileName": tx.source_file_name,
            "sourceFilePath": tx.source_file_path,
            "engine": tx.engine,
            "engineVersion": tx.engine_version,
            "rawText": tx.raw_text,
            "contentHash": tx.content_hash,
            "metadata": _serialize_metadata(tx.metadata),
            "tags": tx.tags,
            "createdAt": tx.created_at or datetime.now().isoformat(),
        }
        path = self._path_for_id(tx.id, "transcripts")
        self._atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))
        with self._lock:
            self._index["transcripts"].append(self._transcript_index_entry(data))
            self._save_index()
        return True

    def get_transcript(self, id: str) -> Optional[dict]:
        path = self._path_for_id(id, "transcripts")
        if not path.exists():
            return None
        return self._safe_load_json(path)

    def delete_transcript(self, id: str) -> bool:
        path = self._path_for_id(id, "transcripts")
        if path.exists():
            path.unlink()
        with self._lock:
            self._index["transcripts"] = [
                e for e in self._index["transcripts"] if e["id"] != id
            ]
            # 清理关联 markdown
            mds = self._index["markdowns"].get(id, [])
            if isinstance(mds, dict):
                mds = [mds]
            for md in mds:
                md_path = self._path_for_id(md["id"], "markdown")
                if md_path.exists():
                    md_path.unlink()
            self._index["markdowns"].pop(id, None)
            # 清理关联 process
            processes = self._index["processes"].pop(id, [])
            for pr in processes:
                pr_path = self._path_for_id(pr["id"], "process")
                if pr_path.exists():
                    pr_path.unlink()
            # 清理文档图片目录（files/{tx_id}/）
            files_dir = self.root / "files" / self._sanitize_id(id)
            if files_dir.exists():
                for f in files_dir.iterdir():
                    f.unlink()
                files_dir.rmdir()
            self._save_index()
        return True

    def list_transcripts(self, filters: dict | None = None) -> list[dict]:
        with self._lock:
            dirty = False
            valid = []
            for entry in self._index["transcripts"]:
                path = self._path_for_id(entry["id"], "transcripts")
                if path.exists():
                    valid.append(entry)
                else:
                    logger.warning(f"索引条目 {entry['id']} 对应文件不存在，已移除")
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

    # ── 公开的 transcript 更新方法（替代外部直接操作 _index） ──

    # DEPRECATED: unused, kept for future use
    def update_transcript_title(self, tx_id: str, new_title: str) -> bool:
        """更新 transcript 标题（锁内 RMW）"""
        with self._lock:
            for i, entry in enumerate(self._index["transcripts"]):
                if entry["id"] == tx_id:
                    self._index["transcripts"][i]["title"] = new_title
                    self._save_index()
                    return True
        return False

    # DEPRECATED: unused, kept for future use
    def update_transcript_meta(self, tx_id: str, title: str | None = None,
                               tags: list[str] | None = None) -> bool:
        """更新 transcript 标题和/或标签（锁内 RMW）"""
        with self._lock:
            for i, entry in enumerate(self._index["transcripts"]):
                if entry["id"] == tx_id:
                    if title is not None:
                        self._index["transcripts"][i]["title"] = title
                    if tags is not None:
                        self._index["transcripts"][i]["tags"] = tags
                    self._save_index()
                    return True
        return False

    # DEPRECATED: unused, kept for future use
    def replace_transcript_entry(self, tx_id: str, new_data: dict) -> bool:
        """替换 transcript 索引条目（用于重新转录）。new_data 为完整的 index entry dict。"""
        with self._lock:
            for i, entry in enumerate(self._index["transcripts"]):
                if entry["id"] == tx_id:
                    self._index["transcripts"][i] = new_data
                    self._save_index()
                    return True
        return False

    def save_transcript_full(self, tx_data: dict) -> bool:
        """保存完整 transcript（文件 + 索引），用于已有文档的更新。
        覆盖同 id 的 transcript 文件和索引条目。"""
        tx_id = tx_data["id"]
        path = self._path_for_id(tx_id, "transcripts")
        self._atomic_write(path, json.dumps(tx_data, ensure_ascii=False, indent=2))
        with self._lock:
            new_entry = self._transcript_index_entry(tx_data)
            for i, entry in enumerate(self._index["transcripts"]):
                if entry["id"] == tx_id:
                    self._index["transcripts"][i] = new_entry
                    self._save_index()
                    return True
        return False

    # ── MarkdownView CRUD ──

    def save_markdown(self, mv: MarkdownView, extra: dict | None = None) -> bool:
        path = self._path_for_id(mv.id, "markdown")
        self._atomic_write(path, mv.content)
        with self._lock:
            entry = {
                "id": mv.id, "transcriptId": mv.transcript_id,
                "style": mv.style.value if hasattr(mv.style, 'value') else mv.style,
                "mode": mv.mode.value if hasattr(mv.mode, 'value') else mv.mode,
                "version": mv.version, "createdAt": mv.created_at,
                "status": getattr(mv, 'status', 'complete'),
                **(extra or {}),
            }
            existing = self._index["markdowns"].get(mv.transcript_id, [])
            if isinstance(existing, dict):
                existing = [existing]
            same_mode = [e for e in existing if e.get("mode") == entry["mode"]]
            top = max((e.get("version", 0) for e in same_mode), default=0)
            entry["version"] = top + 1
            existing.append(entry)
            same_mode = [e for e in existing if e.get("mode") == entry["mode"]]
            same_mode.sort(key=lambda e: e.get("version", 0), reverse=True)
            for old in same_mode[2:]:
                old_path = self._path_for_id(old["id"], "markdown")
                if old_path.exists():
                    old_path.unlink()
                existing.remove(old)
            self._index["markdowns"][mv.transcript_id] = existing
            self._save_index()
        return True

    # DEPRECATED: unused, kept for future use
    def complete_partial(self, markdown_id: str, new_content: str) -> bool:
        """将 partial 产物更新为 complete——续写完成时调用，不增版本号"""
        path = self._path_for_id(markdown_id, "markdown")
        self._atomic_write(path, new_content)
        with self._lock:
            for tx_id, entries in self._index["markdowns"].items():
                if isinstance(entries, dict):
                    continue
                for e in entries:
                    if e.get("id") == markdown_id:
                        e["status"] = "complete"
                        self._save_index()
                        return True
        return False

    def get_markdown(self, id: str) -> Optional[str]:
        path = self._path_for_id(id, "markdown")
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def get_markdowns_by_transcript(self, transcript_id: str,
                                     mode: str = "", version: int | None = None) -> list[dict]:
        """获取指定 transcript 的所有 Markdown，可按 mode/version 过滤"""
        entries = self._index["markdowns"].get(transcript_id, [])
        if isinstance(entries, dict):
            entries = [entries]
        if mode:
            entries = [e for e in entries if e.get("mode") == mode]
        if version is not None:
            entries = [e for e in entries if e.get("version") == version]
        result = []
        for meta in entries:
            content = self.get_markdown(meta["id"])
            if content:
                result.append({**meta, "content": content})
        return sorted(result, key=lambda e: e.get("version", 0), reverse=True)

    # DEPRECATED: unused, kept for future use
    def get_markdown_by_transcript(self, transcript_id: str) -> Optional[dict]:
        """获取指定 transcript 的 Markdown（优先返回笔记，兼容旧接口）"""
        mds = self.get_markdowns_by_transcript(transcript_id)
        if not mds:
            return None
        for md in mds:
            if md.get("mode") != "polished":
                return md
        return mds[0]

    # ── ProcessResult CRUD ──

    def save_process_result(self, pr: ProcessResult) -> str:
        """保存处理结果。同 transcript + 同 mode 覆盖旧结果。返回 id。"""
        path = self._path_for_id(pr.id, "process")
        self._atomic_write(path, pr.content)
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
        """获取单个处理结果的内容"""
        path = self._path_for_id(id, "process")
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

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
