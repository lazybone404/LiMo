"""分类元数据的**真相源**：文档级 `{doc_dir}/meta.json` + 库级 `{root}/folders.json`。

## 为什么需要它们

`index.json` 的定位是**缓存**（见 `Store._load_index`），但它此前是文件夹归属、
标签、创建时间、来源信息的**唯一**存放处。于是 `_rebuild_index`（index 缺失 /
损坏 / 旧格式时自动触发）只能把它们清空：

    self._index = _empty_index()       # folders 整表归零
    "tags": [], "createdAt": "",
    "folderId": "",                    # 每篇的归属归零

「删一次 index.json，全部整理工作归零」—— 而这条路径不需要用户做错任何事，
一次断电就够。

## 分工

| 数据 | 落点 | 为什么是这里 |
|---|---|---|
| 文档级字段（`title` / `folderId` / `tags` / `createdAt` / 四个来源字段…） | `{doc_dir}/meta.json` | 是**这篇文档**的属性，跟着文档走（搬目录即搬分类） |
| `folders` 树（id / name / parentId / createdAt） | `{root}/folders.json` | 是**整个库**的属性，只有库根有资格放 |

⚠️ `transcript.md` **不能**加自证头 —— 它会进模型 Prompt，加头 = 改生成输入
（`core/transcript_meta.py` 的既定约束）。故文档侧必须是独立文件。

## 与 index.json 的关系

**真相在文件，缓存在 index** —— 与产物侧 `chars` / `edited` / `source` 同一条判据
（那三个由 `get_markdowns_by_transcript` 读文件头现算）。区别只在缓存的有无：
`folderId` / `tags` 要参与过滤与计数，每次列表都读 N 个文件太慢，故 index.json
仍保留这些字段作为**运行时缓存**，**只有重建时才回落到本模块**。

于是两份的行踪是确定的，不存在「什么时候该信哪一份」的判断：
- 正常启动 → 读 index.json（它刚被上次写盘同步过）
- `_rebuild_index` → 读本模块（index 已经不可用）

## 失败策略（两边**刻意不同**）

- `write_folders` 失败 **抛出**：它与 `_save_index` 同级同频（只有文件夹 CRUD 才写），
  失败意味着下次重建丢掉**整棵文件夹树**。静默 = 用户在某次断电之后才发现。
- `write_doc_meta` 失败 **记 warning 不抛**：它在批量操作里逐篇调用（500 篇 = 500 次），
  一篇写不进去只影响这一篇，不该让整批失败；而此时 index 已经写成功，
  用户当次体验不受影响，代价推迟到「万一以后重建」。

## 不落盘的字段

`count` / `totalCount` 是派生的（由 `list_folders` 现算），**刻意不存** ——
存了就会变成「上次拉取的快照」，而 `moveDocs` 是乐观更新，两边各读各的会在
移动瞬间互相矛盾（与 `countByFolder` 那条判据同源）。
"""

import json
import logging
from pathlib import Path
from typing import Optional

from app.core.atomic_io import atomic_write_text
from app.core.errors import AppError

logger = logging.getLogger(__name__)

# 落盘格式版本。字段只增不改；读到不认识的版本按「没有可恢复信息」处理
# （不猜、不报错 —— 这条路径是重建的一部分，必须能走完）。
SCHEMA = 1

DOC_META_NAME = "meta.json"
FOLDERS_NAME = "folders.json"

# 文档级真相包含哪些字段。
#
# 判据：**从磁盘重推不出来的那一批**。`id`（正文 sha256 重推）、`dir_name`
# （目录名本身）、`contentHash`（重算）、`ready`（运行态）都不在里面。
# `metadata` 整个 dict **不存**（可能很大），只摘出其中四个会被当顶层字段
# 消费的值（language / duration / coverUrl / sourceTitle）—— 重建后 `metadata`
# 留空，顶层字段即权威（`_transcript_index_entry` 本来就是从 metadata 摘到顶层的）。
DOC_FIELDS = (
    # 标题：重建时唯一的兜底是 `_rebuild_index` 的 `dir_name.rsplit("-", 1)[0]`，
    # 而目录名在生成时被 `_compute_dir_name` **截到 30 字**、剥掉 `<>:"/\|?*`。
    # 于是「用户起了 35 字标题」或「标题里带斜杠」→ 删一次 index.json → 标题
    # 永久变成截断/改写版（从界面上看不出是被重建改的，用户也没编辑过它）。
    # 它同时是**编辑标题**（`PATCH /transcripts/{id}`）的唯一落点。
    "title",
    "folderId", "tags", "createdAt",
    # 来源身份：不恢复它们的话，重建后一篇网页文档会变成「无来源的文本」，
    # `find_by_url` 也随之失效（同一 URL 再导入一次就会多出一篇）。
    "sourceType", "sourceUrl", "sourceFileName", "sourceFilePath",
    "engine", "engineVersion", "sourceTitle", "duration", "coverUrl", "language",
    # 合成文档的来源清单（出处头通常是第一真相，这里是副本）
    "sourceDocIds", "sourceTitles", "sourceHashes",
)

_FOLDER_FIELDS = ("id", "name", "parentId", "createdAt")


# ── 文档级 ──

def doc_meta_path(doc_dir: Path) -> Path:
    return Path(doc_dir) / DOC_META_NAME


def read_doc_meta(doc_dir: Path) -> dict:
    """读一篇文档的分类元数据。缺失 / 损坏 / 版本不认识 → 返回 `{}`。

    返回空 dict 的语义是「这篇没有任何可恢复的分类信息」，调用方按空值兜底。
    **绝不在读路径上抛异常**：它是重建路径的一部分，而重建必须能走完
    —— 一篇读不出来不该让整个库卡住。
    """
    try:
        raw = doc_meta_path(doc_dir).read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return {}
    return _sanitize_doc_fields(data)


def write_doc_meta(doc_dir: Path, entry: dict) -> None:
    """把 index 条目里的文档级字段写到 `{doc_dir}/meta.json`。

    **写失败只记 warning、不抛**（理由见模块 docstring）。
    """
    payload = {"schema": SCHEMA}
    for key in DOC_FIELDS:
        payload[key] = entry.get(key)
    try:
        atomic_write_text(
            doc_meta_path(doc_dir),
            json.dumps(payload, ensure_ascii=False),
        )
    except AppError as e:
        logger.warning("写入分类元数据失败（不影响本次操作，重建时该篇会缺分类）: %s — %s",
                       doc_dir, e)


def _sanitize_doc_fields(data: dict) -> dict:
    """只保留类型正确的值 —— 手改坏的 meta.json 不该把 None 带进列表字段。"""
    out: dict = {}
    for key in DOC_FIELDS:
        val = data.get(key)
        if key in ("tags", "sourceDocIds", "sourceTitles", "sourceHashes"):
            if isinstance(val, list):
                out[key] = [v for v in val if isinstance(v, str)]
        elif key == "duration":
            if val is None or isinstance(val, (int, float, str)):
                out[key] = val
        elif isinstance(val, str):
            out[key] = val
    return out


# ── 库级 ──

def folders_path(root: Path) -> Path:
    return Path(root) / FOLDERS_NAME


def folders_exist(root: Path) -> bool:
    return folders_path(root).exists()


def read_folders(root: Path) -> Optional[list[dict]]:
    """读库级文件夹树。

    返回 **None** = 文件不存在 / 不可用（调用方才需要去看 `index.json.bak`）；
    返回 **`[]`** = 文件存在但库里确实一个夹都没有 —— 这两个必须区分开，
    否则「老库首次重建」会误以为分类是空的。
    """
    try:
        raw = folders_path(root).read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return None
    rows = data.get("folders")
    if not isinstance(rows, list):
        return None
    return _sanitize_folder_rows(rows)


def _sanitize_folder_rows(rows) -> list[dict]:
    """把落盘的行清洗成结构字段齐全的条目（缺 id 的行直接丢弃）。

    只保留结构字段：`count` / `totalCount` 是派生的，存了就会变成陈旧快照。
    """
    if not isinstance(rows, list):
        return []
    out = []
    for f in rows:
        if not isinstance(f, dict) or not f.get("id"):
            continue
        out.append({key: str(f.get(key) or "") for key in _FOLDER_FIELDS})
    return out


def write_folders(root: Path, folders: list[dict]) -> None:
    """写库级文件夹树。**失败抛出**（理由见模块 docstring）。

    ⚠️ 只写结构字段 —— `count` / `totalCount` 是派生的，落盘会变成陈旧快照。
    """
    payload = {"schema": SCHEMA, "folders": [
        {key: str(f.get(key) or "") for key in _FOLDER_FIELDS} for f in folders
    ]}
    atomic_write_text(folders_path(root), json.dumps(payload, ensure_ascii=False))
