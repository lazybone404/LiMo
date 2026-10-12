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
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

from app.core.markdown_assets import rewrite_asset_urls  # noqa: F401  (re-export，见下)
from app.core.types import RawTranscript, MarkdownView, ProcessResult, Style, GenerateMode
from app.core.errors import AppError, StoreError
from app.core.transcript_meta import parse_meta, dump_meta, parse_artifact_meta, strip_meta
from app.core.artifact_blocks import strip_blocks

logger = logging.getLogger(__name__)

INDEX_VERSION = 3

# ── 产物版本的磁盘布局 ──
#
# 每个 mode 一个目录、一个版本一个文件：`{doc_dir}/{mode}/v{N}.md`。
#
# ⚠️ **版本号必须写在文件名里**——这是硬约束。`_rebuild_index` 只能从磁盘恢复，
#    而旧布局（`{mode}.md` + `{mode}.prev.md`）只表达「最新 / 上一版」两个**位置**，
#    重建出的条目 version 恒为 1、createdAt 恒为空串、style 只能填占位：
#    **删一次 index.json，版本历史连同生成时间全丢**。
#
# ⚠️ 不用平铺 `{mode}.v{N}.md`：那会与遗留的 `{mode}.md` / `{mode}.prev.md` 挤在
#    同一命名空间，正则得同时排除 `.prev` —— 旧重建逻辑栽的正是这个坑。
#
# 目录式与既有风格一致（`{doc_dir}/process/`、`epub_chapters/`、`assets/` 都是
# 「按概念分子目录」）。详见 docs/CHANGELOG.md 2026-10-10。
VERSION_FILE_RE = re.compile(r"^v(\d+)\.md$")

# 产物 mode。⚠️ 与 `routers/markdown_edit.py::_EDITABLE_MODES` 同源（后者是本表的
# 子集）；新增 mode 时两处都要动，前端 `lib/tabRegistry.ts` 也要开 `showVersionSwitcher`。
MARKDOWN_MODES = ("notes", "polished")

# 遗留布局（一次性迁移的输入；迁移后不再产生新文件）
LEGACY_SUFFIX_LATEST = ".md"        # {mode}.md         —— 旧「最新」
LEGACY_SUFFIX_PREV = ".prev.md"     # {mode}.prev.md    —— 旧「上一版」


def markdown_version_path(doc_dir: Path, mode: str, version: int) -> Path:
    """`{doc_dir}/{mode}/v{N}.md` —— 版本文件路径的**唯一**构造点。

    ⚠️ 不要在各调用点手拼 `doc_dir / mode / f"v{version}.md"`：路径判据多份副本是
    本项目反复栽的地方（模式 #54），而这里的副本漂移症状是「读不到文件、静默返回
    空产物」——零报错。
    """
    return doc_dir / mode / f"v{version}.md"


def markdown_followup_path(doc_dir: Path, mode: str, version: int) -> Path:
    """`{doc_dir}/{mode}/v{N}.followups.json` —— 追问建议的**旁路文件**路径。

    ⚠️ 与 `v{N}.md` **并排**（不是目录、不是 index 字段）：让追问天然跟着版本走
    （v2 有 v2 的）、跟着文档删（`delete_transcript` 的 rmtree 覆盖整个 doc_dir）。
    ⚠️ `VERSION_FILE_RE` 是 `^v(\\d+)\\.md$`，所以这个 `.json` **不会**被当成一个版本
    ——它是 `v{N}.md` 的**附属**，不是独立版本。
    ⚠️ 内容不写进正文 / index / 自证头：那三处都会被导出或「删 index 重建」带出去
    （`.md`/`.html` 导出刻意保留自证头、index 只是缓存）。
    """
    return doc_dir / mode / f"v{version}.followups.json"


def list_version_files(doc_dir: Path, mode: str) -> list[tuple[int, Path]]:
    """某 mode 下已有的版本文件，按版本号升序 → `[(N, path)]`。

    **磁盘是版本序的真相源**：`save_markdown` 靠它算 `max+1`（而非读 index），
    `_scan_markdown_versions` 靠它还原重建后的版本序。
    """
    mode_dir = doc_dir / mode
    if not mode_dir.is_dir():
        return []
    found: list[tuple[int, Path]] = []
    for f in mode_dir.iterdir():
        m = VERSION_FILE_RE.match(f.name)
        if m and f.is_file():
            found.append((int(m.group(1)), f))
    return sorted(found, key=lambda t: t[0])


def markdown_dir_has_version(doc_dir: Path, mode: str) -> bool:
    """该文档该 mode 下有没有**任何**产物（新布局，或尚未迁移的遗留布局）。

    ⚠️ 供 `routers/capabilities.py::_scan_interrupted_generations` 用——那里只持有
    config、没有 store 实例，此前**内联**了 `(doc_dir / "notes.md").is_file()`。
    布局改版后那句会永远为假，症状是「已生成完的文档一直被误报成生成中断残留」
    （静默类 bug，且用户会看到一条永不消失的提示）。
    """
    if list_version_files(doc_dir, mode):
        return True
    return (doc_dir / f"{mode}{LEGACY_SUFFIX_LATEST}").is_file()

# ── 资产 URL 改写 ──
# ⚠️ 实现已下沉 `core/markdown_assets.py`（P2-18 收敛）：`assets/` 引用正则在仓库里
# 曾有 4 份副本（本文件 + synthesis + export + content_preparer），且本文件那份的
# 捕获组布局与另三份**不同**（`assets/` 放在组外）。`rewrite_asset_urls` 在顶部
# re-export，**不要在本文件重新实现**——「仅用于路由层、Store CRUD 内部不调」的
# 约定随 docstring 一起搬走了。


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


# ── 占位条目的宽限期（P1-3）──

# `_get_or_create_doc_dir` 会先建目录 + 写一条**占位索引**，`transcript.md` 直到
# `save_transcript` 才落盘 —— 中间夹着封面下载（`timeout=15`）、甚至整段视频的
# 下载 + 云端转写。这段时间里条目本来就该没有正文，**不是「文档已被删除」**。
# 本进程正在导入的（`Store._pending_ids`）一律豁免；跨进程的按年龄宽限。
PENDING_GRACE_SECONDS = 3600


def _entry_age_seconds(entry: dict) -> float:
    """条目 `createdAt` 距今秒数；解析不了（空串 / 格式坏）→ 视为**很久以前**。

    ⚠️ 返回 `inf` 而非 0：本值的唯一用途是「够不够旧、能不能清理」，而 `createdAt`
    是本模块自己写的、格式由本模块独占 —— 读不出来只可能是条目被外力改坏。此时
    「一条永远不会有正文的占位记录永久驻留」比「把它清掉」更糟（它没有自愈途径）。
    """
    raw = entry.get("createdAt", "")
    if not raw:
        return float("inf")
    try:
        return (datetime.now() - datetime.fromisoformat(raw)).total_seconds()
    except (ValueError, TypeError):
        return float("inf")


def _unique_tx_id(seed: str, taken: set[str]) -> str:
    """从 `seed` 派生一个**未被 `taken` 占用**的文档 id（`tr_` + 12 hex）。

    ⚠️ **首次尝试与旧行为逐字相同**（`sha256(seed)[:12]`）——调用方传的是
    `transcript.md` 内容，故「不冲突」这个绝大多数情形下，重建结果与改造前
    **完全一致**（零回归）；只有真撞上时才走加盐分支。

    ⚠️ 为什么非要唯一不可：`id` 是文档的**主键** —— 前端的 React key、
    `get_transcript` / `update_title` / `delete_transcript` 的定位全用它。
    两条条目共用一个 id 时：
    - 后端：`delete_transcript` 的 `find` 只命中第一条，**第二条永远留在列表里
      且再也删不掉**（无论怎么点「删除」，它都还在）；
    - 前端：直接报 `Encountered two children with the same key` 并可能重复/省略行。
    冲突的真实来源是**磁盘上两份内容完全相同的文档目录**（同一 URL 被并发导入、
    或历史遗留的重复目录）——按内容派生 id，同内容必然同 id，故必须有盐打破它。
    """
    cand = f"tr_{hashlib.sha256(seed.encode()).hexdigest()[:12]}"
    if cand not in taken:
        return cand
    for attempt in range(16):
        cand = f"tr_{hashlib.sha256(f'{seed}\x00{attempt}'.encode()).hexdigest()[:12]}"
        if cand not in taken:
            return cand
    # 16 次仍撞：数据已严重异常（同一 seed + 16 个盐全落在同一批 id 上）。
    # 退化为随机 id（与正常导入路径同款）而不是抛错——重建/自愈中途崩掉
    # 会把「索引有点脏」升级成「应用起不来」。绝不返回重复值。
    logger.warning("文档 id 派生连续冲突（seed 前缀 %r），退化为随机 id", seed[:16])
    return f"tr_{uuid4().hex[:12]}"


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
        # 本进程内「已建目录、正文尚未落盘」的 id（P1-3）。导入窗口可能很长（视频
        # 下载 + 转写），带年龄宽限仍可能误判 → 会话内豁免是**精确**的那一半。
        self._pending_ids: set[str] = set()
        self._load_index()
        # 一次性修正：索引里**重复的文档 id**（历史重建留下的脏数据，见方法 docstring）。
        # 放在 `_load_index` **之后**——两条加载路径（正常读盘 / 故障重建）都要覆盖。
        # ⚠️ 它是**定点改字段**，不是重建：绝不重扫磁盘、绝不碰 tags/folderId/来源
        #    （第 3 轮审计 P0 的教训正是「启动迁移里整库重建，把分类全抹掉」）。
        self._dedupe_transcript_ids()
        # 一次性布局迁移：两版制（{mode}.md + {mode}.prev.md）→ 版本归档（{mode}/v{N}.md）。
        # 放在 `_load_index` **之后**：即便 index 丢了，`_load_index` 已先做过常规重建，
        # 这里搬完文件再重建一次，产物版本即可正确出现。幂等，详见方法 docstring。
        self._migrate_legacy_markdown_layout()

    # ── 索引加载 / 保存 / 迁移 ──

    def _load_index(self):
        """加载统一索引。

        三种「不可用」——**文件缺失 / 内容损坏 / 旧格式（v<3）**——统一走同一条自愈路径：
        磁盘上还扫得到文档目录就**全盘重建**，扫不到就空索引。

        ⚠️ 此前损坏分支只是 `logger.warning` + 空索引：磁盘上几十篇文档还在，库却空了，
        而**没有任何一层会告诉用户**（前端只显示「还没有文档」）。同一份文件在别的模块里
        待遇完全不同 —— `core/config.py:338` 读不出 config.yaml 时会先 `copy2` 一份 `.bak`
        再回落默认值。这里补上同样的两半：**备份**（原文是唯一还能人工抢救分类/标签/来源的
        材料，重建一落盘就覆盖）+ **重建**。
        """
        reason = ""
        if self._index_path.exists():
            try:
                data = json.loads(self._index_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("index.json 无法解析：%s", e)
                reason = "内容损坏"
            else:
                if isinstance(data, dict) and data.get("version", 0) >= 3:
                    default = _empty_index()
                    for key in default:
                        if key not in data:
                            data[key] = default[key]
                    self._index = data
                    return
                reason = (f"旧索引格式 v{data.get('version', '?')}"
                          if isinstance(data, dict) else "根节点不是对象")
        else:
            reason = "文件缺失"

        # ── 自愈 ──
        had_file = self._index_path.exists()
        backup_name = ""
        if had_file:
            # ⚠️ `with_suffix` 不收多点的后缀（`.json.bak` 会 ValueError），只能拼 name
            backup = self._index_path.with_name(self._index_path.name + ".bak")
            try:
                shutil.copy2(self._index_path, backup)
                backup_name = backup.name
            except OSError as e:
                logger.warning("索引备份失败（不阻断重建）：%s", e)

        # ⚠️ 判据与 `_rebuild_index` **同源**：不问「有没有 docs/ 目录」（文档目录就在
        #    `root/` 下，并没有中间层），而是**让它自己扫**，扫出几篇就是几篇。
        self._rebuild_index(save=False)
        n = len(self._index["transcripts"])
        # 全新用户（没文件、也没文档）保持原状：不凭空写一个空 index.json
        if n or had_file:
            self._save_index()

        if n:
            logger.warning(
                "索引不可用（%s），已按磁盘重建 %d 篇文档（原文备份为 %s）。"
                "⚠️ 重建**有损**：文档 id 由正文 sha256 重推（与旧 id 不同），"
                "文档级字段（文件夹归属 / 标签 / 来源类型与 URL / 创建时间）无法还原；"
                "产物的**版本序 / 生成时间 / 风格档**改由各自文件头的自证头还原"
                "（2026-10-10 起，此前一并丢失）。",
                reason, n, backup_name or "（备份失败）",
            )
        elif had_file:
            logger.warning("索引不可用（%s），磁盘上也没有可重建的文档目录，已初始化为空索引",
                           reason)

    def _save_index(self):
        """原子写入统一 index.json"""
        self._atomic_write(
            self._index_path,
            json.dumps(self._index, ensure_ascii=False, indent=2),
        )

    def _dedupe_transcript_ids(self) -> None:
        """把索引里**重复的文档 id** 就地改成唯一（幂等，启动时跑一次）。

        重复的**真实来源**是磁盘上两份**内容完全相同**的文档目录：`_rebuild_index`
        按 `transcript.md` 的内容派生 id，同内容必然推出同一个 id。`_rebuild_index`
        侧已改为冲突时唯一化（`_unique_tx_id`），但**改代码不会让已经落盘的脏索引
        自愈** —— 这些条目已经存在于用户的 index.json 里，每一次列表刷新都会把它们
        发给前端，于是 React 每次都报 `Encountered two children with the same key`。

        ⚠️ **只改 `id` 一个字段，不删任何东西**：
        - 另一份目录仍在磁盘上（用户可自行判断要不要删），本次不动文件系统；
        - 不重扫磁盘、不碰 `tags` / `folderId` / 来源字段（第 3 轮审计 P0 的教训：
          启动迁移里整库 `_rebuild_index` 会把标签与文件夹分类**不可逆抹掉**）；
        - `markdowns` / `processes` 的 key **保持指向原 id**：那两份目录里只可能
          一份有产物，而 dict 本来就只存得下一份 —— 让产物留在「第一条」上，与
          `get_transcript` / `delete_transcript` 取 `find` 首条的行为一致；新 id
          那条的产物为空。⚠️ 所以**不能**顺手把产物字段也搬过去，那会凭空发明
          「哪份目录生了这篇笔记」这个事实。
        """
        seen: set[str] = set()
        renamed: list[tuple[str, str]] = []
        for entry in self._index.get("transcripts", []):
            tid = entry.get("id", "")
            if tid and tid not in seen:
                seen.add(tid)
                continue
            # 空 id（被外力改坏）或重复 id → 重发一个。
            # ⚠️ 盐必须是**天然互异**的东西：`dir_name` 是同内容目录之间唯一的区别
            #    （内容相同 ⇒ 内容派生的 id 相同 ⇒ 只有目录名能打破）。
            new_id = _unique_tx_id(f"{tid}\x00{entry.get('dir_name', '')}", seen)
            seen.add(new_id)
            renamed.append((tid, new_id))
            entry["id"] = new_id

        if renamed:
            self._save_index()
            logger.warning(
                "索引中有 %d 条重复/空文档 id，已就地重发（只改 id，未动磁盘）：%s",
                len(renamed),
                "; ".join(f"{old or '(空)'} → {new}" for old, new in renamed),
            )

    def _migrate_legacy_markdown_layout(self) -> None:
        """一次性把遗留的两版制布局迁到版本归档布局（**幂等、无损**）。

        ```
        {doc_dir}/{mode}.md        （旧「最新」）  ┐
        {doc_dir}/{mode}.prev.md   （旧「上一版」）┘ → {mode}/v2.md + {mode}/v1.md
        只有 {doc_dir}/{mode}.md 没有 prev         → {mode}/v1.md
        ```

        ⚠️ **只 `os.replace` 改名**——内容与自证头一字不动。任何「顺便整理一下内容」
        都会破坏 `generatedAt` 与正文的对应关系，也让「迁移无损」失去可验证性。

        ⚠️ **幂等守卫**是「目标目录里已经有版本文件」。半途崩掉的状态（目录里已有
        `v*`，顶层却还留着 `{mode}.md`）也能自愈——把遗留文件接在最大版本号之后，
        并**记 warning**（迁移这种事不该悄悄发生）。

        ⚠️ 「只有 `{mode}.md`、没有 `{mode}.prev.md`」是**正常输入**：旧实现在
        `os.replace` 失败时静默吞掉异常，磁盘上就可能只剩最新那份。此时如实映射为
        v1——更早的那版在旧模型下**已经被物理覆盖**，无从找回，不要在这里伪造一个。

        ⚠️ **迁移失败不能阻断启动**：磁盘是脏的，但应用该照常可用（读到哪版算哪版）。

        ⚠️ **只重算受影响 mode 的产物条目，绝不整库 `_rebuild_index`**：后者会
        `self._index = _empty_index()` 后从磁盘重扫，而磁盘**无法还原**文档级字段
        （标签 / 文件夹归属 / 来源 URL / 创建时间）与 `folders` 整张表。这不是理论
        风险——升级用户（磁盘上必然留有两版制产物）首次启动就触发，随后
        `_save_index()` **立刻把清空后的索引落盘**，标签与文件夹分类不可逆丢失。
        """
        moved = 0
        resync: list[tuple[str, str, str]] = []   # (dir_name, tx_id, mode)
        # dir_name → tx_id。迁移**只动文件、不动 transcript 条目**，故映射建一次即可。
        by_dir = {e.get("dir_name", ""): e.get("id", "") for e in self._index["transcripts"]}
        for d in sorted(self.root.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            if not (d / "transcript.md").exists():
                continue      # 不是文档目录（与 `_rebuild_index` 同一条判据）
            for mode in MARKDOWN_MODES:
                try:
                    moved += self._migrate_mode_layout(d, mode)
                except OSError as e:
                    logger.warning("产物布局迁移失败（跳过该 mode，不影响使用）%s/%s：%s",
                                   d.name, mode, e)
                    continue
                tx_id = by_dir.get(d.name, "")
                if tx_id and self._markdown_entries_desynced(tx_id, d, mode):
                    resync.append((d.name, tx_id, mode))
        if resync:
            # ⚠️ 写盘失败**不阻断启动**（docstring 的承诺）：`_save_index → _atomic_write`
            # 抛的是 `StoreError`（`AppError` 子类，**不是 `OSError`**），只 catch OSError
            # 会让磁盘满 / 索引文件被占用时后端起不来。失败在这里是安全的——内存结果
            # 没落盘，下次启动 `_markdown_entries_desynced` 会再判一次（它比的是集合，
            # 天然幂等）。
            try:
                with self._lock:
                    for dir_name, tx_id, mode in resync:
                        # 只换该 mode 的条目：同文档另一个 mode 的条目原样保留
                        # （`_scan_markdown_versions` 会把它一并扫回来，但那份是磁盘视角，
                        #  可能丢掉 index 独有信息如 `status="partial"`）。
                        cur = self._index["markdowns"].get(tx_id, [])
                        if isinstance(cur, dict):
                            cur = [cur]
                        scanned = self._scan_markdown_versions(self.root / dir_name, tx_id)
                        self._index["markdowns"][tx_id] = (
                            [e for e in cur if e.get("mode") != mode]
                            + [e for e in scanned if e.get("mode") == mode]
                        )
                    self._save_index()
            except AppError as e:
                logger.warning("产物布局迁移：索引更新失败（不阻断启动，下次启动重试）：%s", e)
        if moved:
            logger.warning(
                "产物布局已迁移：%d 个文件改为「一版一文件」（{mode}/v{N}.md）。"
                "此后每个版本独立归档，不再只保留两版。", moved)

    def _markdown_entries_desynced(self, tx_id: str, doc_dir: Path, mode: str) -> bool:
        """index 里该 (文档, mode) 的版本集合，是否与磁盘上的版本文件不一致。

        判据刻意是**集合比较**而不是「本次搬动了几个文件」——于是它同时覆盖三种
        状态：正常迁移（旧条目 version 与新的文件名对不上）、迁移崩在半途（文件已
        改名、index 未及更新）、index 被外部改坏。三种都收敛到同一条修法：按磁盘
        重扫该 mode 的条目（磁盘是版本序的真相源）。

        ⚠️ 在**已迁移且一致**的库上恒为 False —— 这是「正常启动零副作用」的保证。
        """
        disk = {v for v, _ in list_version_files(doc_dir, mode)}
        entries = self._index["markdowns"].get(tx_id, [])
        if isinstance(entries, dict):
            entries = [entries]
        mem = {e.get("version") for e in entries if e.get("mode") == mode}
        return disk != mem

    @staticmethod
    def _migrate_mode_layout(doc_dir: Path, mode: str) -> int:
        """迁一个 `(doc_dir, mode)`，返回搬动的文件数（见 `_migrate_legacy_markdown_layout`）。"""
        latest = doc_dir / f"{mode}{LEGACY_SUFFIX_LATEST}"
        prev = doc_dir / f"{mode}{LEGACY_SUFFIX_PREV}"
        if not latest.exists() and not prev.exists():
            return 0

        # 目标目录已有版本 → 接在最大版本号之后（已迁移过 / 上次崩在半途）
        existing = list_version_files(doc_dir, mode)
        nxt = (existing[-1][0] if existing else 0) + 1
        mode_dir = doc_dir / mode
        moved = 0
        # ⚠️ 顺序有意义：`prev` 是**更早**的一版，必须先搬、拿更小的版本号。
        # 反过来会让「上一版」变成最新版——静默的历史错序，且看不出来。
        for legacy in (prev, latest):
            if legacy.exists():
                mode_dir.mkdir(parents=True, exist_ok=True)
                os.replace(legacy, markdown_version_path(doc_dir, mode, nxt))
                nxt += 1
                moved += 1
        return moved

    def _rebuild_index(self, save: bool = True):
        """遍历所有文档目录重建索引（故障恢复）。
        识别规则：根目录下含 transcript.md 的子目录视为文档目录。
        从目录名反推 dir_name，从文件推断 index 条目。

        `save=False` 供 `_load_index` 的「先扫一眼看有没有文档、再决定要不要落盘」用
        —— 那条路径上写盘与否取决于扫描结果，不能无条件下手。
        """
        self._index = _empty_index()
        # 本次扫描已分配的 id（⚠️ 每次重建都重置，故只需记本轮的）——见 `_unique_tx_id`
        taken_ids: set[str] = set()

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
            # ⚠️ 必须过 `_unique_tx_id`：磁盘上可能有两份**内容相同**的目录
            #    （旧的导入窗口 bug / 同 URL 并发导入的遗留），纯内容派生会让它们
            #    共用同一个 id —— 那是「删不掉的那一篇」与前端重复 key 的来源。
            raw_text = tx_md.read_text(encoding="utf-8")
            tx_id = _unique_tx_id(raw_text, taken_ids)
            taken_ids.add(tx_id)

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
                # 就绪标记（P1-3）：本函数只对**存在 transcript.md 的目录**建条目，
                # 故恒 True（与 `_transcript_index_entry` 同构，见那里的说明）。
                "ready": True,
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

            # 重建 markdown 条目 —— 读文件头还原版本序、生成时间与风格档。
            # ⚠️ 旧实现**跳过 `.prev.md` 且从不读文件内容**，于是重建出的条目
            #    version 恒为 1、createdAt 恒为空串 —— 删一次 index.json，
            #    版本历史连同时间全丢（本轮修复的核心症状）。
            md_entries = self._scan_markdown_versions(d, tx_id)
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

        if save:
            self._save_index()

    # ── 工具方法 ──

    @staticmethod
    def _markdown_entry_id(tx_id: str, mode: str, version: int) -> str:
        """版本条目的 id：由 `(tx_id, mode, version)` **确定性派生**。

        ⚠️ 用相对 key 而非绝对路径（旧布局是 `sha256(str(md_file))`）：后者会让
        同一份库在**换数据目录 / 换盘符后 id 全变**。相对 key 保证「同一篇文档的
        同一版」在任何位置都是同一个 id，删 index 重建后 id 不变。

        ⚠️ 写侧（`save_markdown`）**也用它**，不采信调用方传的 `mv.id`——同一份
        数据两套 id 是本项目模式 #54 的典型入口。
        """
        key = f"{tx_id}/{mode}/v{version}"
        return f"md_{hashlib.sha256(key.encode()).hexdigest()[:12]}"

    def _scan_markdown_versions(self, doc_dir: Path, tx_id: str) -> list[dict]:
        """扫磁盘还原该文档**全部产物版本**的 index 条目（重建的唯一构造点）。

        这是本轮修复的核心：旧重建路径**跳过 `.prev.md`、且从不读文件内容**，
        于是版本号恒为 1、`createdAt` 恒为空串、`style` 只能填占位。而答案一直在
        文件里——产物首行的自证头（`<!-- limo-meta -->`）里有 `generated.generatedAt`
        （真实生成时间）与 `generated.params.style`。本函数把它们读回来。

        ⚠️ **版本号取文件名，不取 `identity.version`**：文件名才是布局的硬约束
        （它决定读哪个文件）。两者不一致时以文件名为准。

        ⚠️ 与 `save_markdown` 的**写侧同源**：这里还原的字段就是写侧落盘时写进
        index 的那一批。两处对不上不会有任何报错（模式 #54），改一侧必须看另一侧。
        """
        entries: list[dict] = []
        for mode in MARKDOWN_MODES:
            for version, path in list_version_files(doc_dir, mode):
                try:
                    raw = path.read_text(encoding="utf-8")
                except OSError as e:
                    logger.warning("重建：跳过读不出的产物版本 %s（%s）", path, e)
                    continue
                head, _corrupt = parse_artifact_meta(raw)
                generated = (head or {}).get("generated") or {}
                params = generated.get("params") or {}
                entries.append({
                    "id": self._markdown_entry_id(tx_id, mode, version),
                    "transcriptId": tx_id,
                    # ⚠️ **这个 "detailed" 是「无法还原时的占位」，不是默认档**：
                    # 本轮之前落盘的产物多数没有自证头，读不出当时的 style，只能填
                    # 一个。改全局默认风格档时**不要**顺手改它 —— 那样会让历史产物
                    # 的标签集体变成「通俗」，比留着旧的占位更不诚实。见模式 #44。
                    "style": params.get("style") or "detailed",
                    "mode": mode,
                    "version": version,
                    "createdAt": generated.get("generatedAt", ""),
                    # ⚠️ 产物 status（complete/partial）**没有落进自证头**，磁盘上
                    # 无从还原，与旧重建路径一样填 complete。要还原它得先把它写进头。
                    "status": "complete",
                })
        return entries

    @staticmethod
    def _atomic_write(path: Path, content: str):
        """原子写入 + StoreError 保护。

        ⚠️ 临时名必须**每次唯一**（`mkstemp`），不能用固定的 `path.with_suffix(".tmp")`：
        两个写者（多进程 / 并发测试）撞上同一路径时都写同一个临时文件，内容交错，
        后一个 `replace` 把**半截内容**原子地换上去 —— 原子性只保证了「换」这一步，
        保证不了「换上去的东西是完整的」，而症状是一份解析不了的 index.json
        （即 `_load_index` 那条自愈路径，代价是全盘重建 + 分类丢失）。

        ⚠️ 别想着「顺手加个锁更稳」：`self._lock` 是**非重入** `threading.Lock`，而
        `_save_index` 恒在**已持锁**时调到本函数 —— 加锁即死锁（本函数还是
        `@staticmethod`，本来也拿不到 `self`）。并发安全由唯一临时名 + `os.replace`
        的原子性承担，不需要互斥。
        """
        tmp_path: str | None = None
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, path)
            tmp_path = None          # 已改名，finally 不该再去删它
        except OSError as e:
            # 按 errno 细分（ENOSPC / 占用 / 路径过长 / 权限 / 未知）——「一律磁盘
            # 空间不足」会让用户朝错误的方向行动，见 StoreError.write_failed。
            raise StoreError.write_failed(f"写入 {path.name}", e) from e
        finally:
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

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
            # 就绪标记（P1-3）：本函数只由 `save_transcript` 走到，此时 `transcript.md`
            # 已落盘 → 恒 True。与 `_get_or_create_doc_dir` 的占位条目（False）成对。
            # ⚠️ 读侧一律用 `entry.get("ready", True)` —— **缺键 = 就绪**：旧 index.json
            # 里没有这个键的历史条目必须按原行为处理，否则一次升级把所有老文档变成
            # 「永不清理的占位」或「可被随意清理的幽灵」，两种都糟。
            "ready": True,
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
                    # 就绪标记（P1-3）：`transcript.md` 还没写。**这是本标记唯一的
                    # False 写点**——旧实现没有它，`list_transcripts` 只看「目录里有没有
                    # transcript.md」，于是导入窗口内任何一次列表刷新都把这条判成
                    # 「文档已删除」永久删掉；随后 `save_transcript` 重建出
                    # `{title}-{hex}_1` 第二份目录，而 assets 已写在旧目录 → 该文档
                    # 图片**永久 404**、旧目录成孤儿。
                    "ready": False,
                })
                self._pending_ids.add(tx_id)
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
            # 正文已落盘 → 摘掉会话级豁免（P1-3）。此后若目录再消失，那就是真的
            # 被外力删了，按幽灵条目清理是对的。
            self._pending_ids.discard(tx.id)
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
        """列出文档条目（顺带清理幽灵条目）。

        ⚠️ 清理判据（P1-3）：**「目录里没有 transcript.md」不等于「文档已被删除」** ——
        `_get_or_create_doc_dir` 先建目录 + 写占位条目，正文直到 `save_transcript` 才落盘，
        中间夹着封面下载（15s）甚至整段视频的下载 + 转写。旧实现无条件按后者处理，于是
        导入窗口内的任何一次列表刷新都会把条目**永久删掉**，随后 `save_transcript` 重建出
        `{title}-{hex}_1` 第二份目录，而 assets 已写在旧目录 → 图片永久 404、旧目录成孤儿。

        现在分三类：就绪（返回）/ 未就绪（**保留但不返回**，见下）/ 真幽灵（删除）。
        未就绪条目要**两条豁免都不过**才清理：本进程正在导入的（`_pending_ids`）绝不清理；
        跨进程的按 `createdAt` 宽限（`PENDING_GRACE_SECONDS`）。
        """
        with self._lock:
            dirty = False
            valid = []
            pending_kept = 0
            for entry in self._index["transcripts"]:
                dir_name = entry.get("dir_name", "")
                if dir_name and (self.root / dir_name / "transcript.md").exists():
                    valid.append(entry)
                    continue

                # 未就绪的占位条目（缺键 = 就绪，兼容旧 index.json）
                if entry.get("ready", True) is False:
                    if (entry["id"] in self._pending_ids
                            or _entry_age_seconds(entry) < PENDING_GRACE_SECONDS):
                        # 保留在索引里（`save_transcript` 要能 upsert 到它，
                        # 否则就是「重建第二份目录」那条老路），但**不返回给前端**：
                        # 半成品条目（只有标题和 createdAt）出现在文档库里对用户无意义。
                        valid.append(entry)
                        pending_kept += 1
                        continue
                    logger.warning(
                        "占位条目 %s 超过 %d 秒仍无正文（导入未完成即退出？），已从索引移除",
                        entry["id"], PENDING_GRACE_SECONDS)
                    dirty = True
                    continue

                logger.warning(f"索引条目 {entry['id']} 对应目录不存在，已移除")
                dirty = True
            if dirty:
                self._index["transcripts"] = valid
                self._save_index()
            if pending_kept:
                # ⚠️ 用 debug 而非 warning：导入窗口内这是**正常态**，warning 会让每次
                # 列表刷新都刷一条（而它一天要跑很多次）。真出问题时有上面那条。
                logger.debug("列表隐藏 %d 条尚未落盘的占位条目", pending_kept)
            visible = [e for e in valid if e.get("ready", True) is not False]

        result = visible
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
    # process / 版本归档产物），把文件夹做成目录会同时破坏目录命名规则、assets 相对
    # 引用、版本归档——移动文档要重写全部 index 路径。故 folders 只活在 index.json 里。

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

    def _merge_tags(self, entry: dict, tags: list[str], mode: str) -> None:
        """把 tags 合进某条 transcript。`add` = 并入去重 / `replace` = 整体替换。

        ⚠️ **本函数是 add/replace 语义的唯一实现**：批量打标签与自动分类的写入都调它。
        两处各写一份 = 同源判据住多处（模式 #54），而它的失效方式恰好最隐蔽——
        「add 的去重在一处悄悄漂移，另一处看不出来」。
        """
        if mode == "replace":
            entry["tags"] = list(tags)
            return
        merged = list(entry.get("tags", []))
        for t in tags:
            if t not in merged:
                merged.append(t)
        entry["tags"] = merged

    def update_transcripts_tags(self, ids: list[str], tags: list[str],
                               mode: str = "add") -> int:
        """批量打标签。mode="add" 并入去重 / mode="replace" 整体替换。返回命中数。"""
        targets = set(ids)
        with self._lock:
            n = 0
            for e in self._index["transcripts"]:
                if e["id"] not in targets:
                    continue
                self._merge_tags(e, tags, mode)
                n += 1
            if n:
                self._save_index()
        return n

    def apply_classifications(self, assignments: list[dict]) -> int:
        """按**逐篇异构**的方案写入（每篇各自的文件夹 + 各自的标签）。返回命中数。

        `assignments = [{"id": str, "folder_id": str, "tags": list[str]}]`。

        ⚠️ **为什么单开一个方法而不是循环调上面两个**：分类的每篇 folder 与 tags
        通常互不相同，用现有批量端点表达等于最坏 N×2 次**全量 index.json 写盘**
        （与 `routers/ingest.py` 里「一次写盘而非 N 次」的收益同源，只是这里 N 是
        逐篇而非逐批）。一次加锁、一次写盘。

        ⚠️ 未命中（id 不存在）的条目**静默跳过**——存在性校验是调用方（路由层）的
        职责，那里才有 `failed` 明细的出口；在这里抛异常会让「一条不存在」毁掉整批。
        """
        by_id = {a["id"]: a for a in assignments}
        with self._lock:
            n = 0
            for e in self._index["transcripts"]:
                a = by_id.get(e["id"])
                if a is None:
                    continue
                e["folderId"] = a.get("folder_id") or ""
                self._merge_tags(e, a.get("tags") or [], "add")
                n += 1
            if n:
                self._save_index()
        return n

    # ── MarkdownView CRUD ──

    def save_markdown(
        self,
        mv: MarkdownView,
        file_meta: dict | None = None,
        *,
        replace_version: int | None = None,
    ) -> bool:
        """写入一份产物版本 → `{doc_dir}/{mode}/v{N}.md`。

        **三种写入语义只由 `replace_version` 一个参数区分**：

        | 语义 | 调用方 | `replace_version` | 动作 |
        |---|---|---|---|
        | **生成** | generate / epub_pipeline / sample_doc | `None` | 新建 `v{max+1}` |
        | **编辑** | `routers/markdown_edit.py` | `= 用户正在看的那一版 N` | 覆盖 `v{N}` |

        ⚠️ 旧表里还有一行「**追问**（未来）→ 覆盖 `v{N}`，body 由调用方拼好」——
        **已作废**（2026-10-11）：追问建议改走**旁路文件** `v{N}.followups.json`
        （`save_artifact_followups`），不写正文、**不经过本函数**、不产生版本。

        ⚠️ **编辑不产生版本**（用户拍板：「编辑只是用户在那个对应版本上做更改，我们
        不负责」）。覆盖时版本号不变、条目 id 不变——「这一版被改过」这个事实落在
        **文件头**的 `edit` 键里（由调用方放进 `file_meta`），而不是靠新建一条 index
        记录。旧实现把编辑也当新产物写，于是「编辑两次，AI 生成的那一版就被物理覆盖」。

        ⚠️ `file_meta` → 写进**文件首行**的自证头，随文件走、扛得住删 index 重建。
        旧签名另有一个 `index_extra`（只并进 index.json 条目），本轮**删除**：唯一
        调用点传的是 `{"edited": True}`，那种标记放 index 里删一次就丢。少一个
        「参数接反了两边都不报错」的陷阱（旧版 docstring 专门警告过）。

        ⚠️ **版本号计算与文件写入必须在同一把锁内**：版本号由磁盘现状（`max+1`）
        决定。旧实现是「文件写在锁外、index 写在锁内」——在新语义下会让两个并发的
        生成算出**同一个 N** 而互相覆盖。

        ⚠️ 版本**全量归档、不设上限**（用户选择）——磁盘是本地 md，一篇文档即使攒到
        上百版也只有几 MB。
        """
        doc_dir = self.get_doc_dir(mv.transcript_id)
        if not doc_dir:
            logger.warning("save_markdown: 文档目录不存在 tx=%s", mv.transcript_id)
            return False

        mode = mv.mode.value if hasattr(mv.mode, 'value') else mv.mode
        style = mv.style.value if hasattr(mv.style, 'value') else mv.style

        with self._lock:
            existing = list_version_files(doc_dir, mode)
            if replace_version is None:
                version = (existing[-1][0] if existing else 0) + 1
            else:
                version = replace_version
                if not markdown_version_path(doc_dir, mode, version).is_file():
                    # ⚠️ 显式失败，不静默新建：编辑请求指向一个不存在的版本，说明
                    # 前端的版本视图已过期（或被外力改坏）。悄悄新建一版等于把
                    # 「用户以为在改 v1」变成「库里凭空多了个 v1」。
                    logger.warning("save_markdown: 要覆盖的版本不存在 %s/%s/v%d",
                                   doc_dir.name, mode, version)
                    return False
            target = markdown_version_path(doc_dir, mode, version)

            # 自证头注入**真实版本号** —— 版本号现在就是文件名，写盘前已知。
            # （`provenance.py` 里「写盘那刻不知道版本号」的说明据此失效，那里已改。）
            if isinstance(file_meta, dict):
                # ⚠️ **不就地改调用方的 dict**：`identity.version` 是**本次写盘**才定下的，
                # 而 `file_meta` 是调用方的入参。就地改写会让「同一个 dict 连写两版」的
                # 调用方把第一版的版本号带进第二版（零报错，两版自证头同号）。浅拷贝足够
                # ——被写的只有 `identity` 一层，这里连它一起拷。
                file_meta = dict(file_meta)
                identity = file_meta.get("identity")
                identity = dict(identity) if isinstance(identity, dict) else {}
                identity["version"] = version
                file_meta["identity"] = identity

            content = mv.content
            if file_meta:
                # dump_meta 返回单行；空行把注释与正文隔开（也匹配 strip_meta 的回剥规则）
                content = f"{dump_meta(file_meta)}\n\n{content}"
            target.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write(target, content)

            entry_list = self._index["markdowns"].get(mv.transcript_id, [])
            if isinstance(entry_list, dict):
                entry_list = [entry_list]
            prior = next((e for e in entry_list
                          if e.get("mode") == mode and e.get("version") == version), None) or {}

            # ⚠️ 「这一版何时生成、生成得完不完整」**不因编辑而改变**——编辑只改正文。
            # createdAt 优先取自证头里的 `generatedAt`：与 `_scan_markdown_versions`
            # 的重建路径**同源**，否则「删 index 重建后时间会变」。
            created_at = mv.created_at
            if isinstance(file_meta, dict):
                created_at = ((file_meta.get("generated") or {}).get("generatedAt")
                              or prior.get("createdAt") or mv.created_at)
            status = prior.get("status") if replace_version is not None else None

            entry = {
                # ⚠️ id 由 (tx, mode, version) 确定性派生，**不采信 `mv.id`**：否则
                # 同一版在「写盘时」与「重建后」会有两个 id（模式 #54）。
                "id": self._markdown_entry_id(mv.transcript_id, mode, version),
                "transcriptId": mv.transcript_id,
                "style": style, "mode": mode, "version": version,
                "createdAt": created_at,
                "status": status or getattr(mv, 'status', 'complete'),
            }
            entry_list = [e for e in entry_list
                          if not (e.get("mode") == mode and e.get("version") == version)]
            entry_list.append(entry)
            entry_list.sort(key=lambda e: e.get("version", 0), reverse=True)
            self._index["markdowns"][mv.transcript_id] = entry_list
            self._save_index()
        return True

    def get_markdown(self, id: str) -> Optional[str]:
        """按条目 id 读**该版本**的正文（含自证头首行）。

        ⚠️ 路径直接由条目自己的 `(mode, version)` 决定。旧实现要先算同 mode 的
        `max_version`、再判断读 `{mode}.md` 还是 `{mode}.prev.md`——「一个版本读成
        哪份文件」取决于**别人**的版本号，那正是「版本 = 位置」的病根。
        """
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

        doc_dir = self.get_doc_dir(target_entry.get("transcriptId", ""))
        if not doc_dir:
            return None
        path = markdown_version_path(doc_dir, target_entry.get("mode", ""),
                                     target_entry.get("version", 0))
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

    def get_artifact_modes(self, transcript_ids: list[str]) -> dict[str, list[str]]:
        """批量取「各文档已有哪些产物」→ `{tx_id: ["notes", "polished"]}`（**不读盘**）。

        列表端点（`GET /api/transcripts`）的热路径：每篇文档带不带这个信息，决定了
        前端**要不要发产物请求**——没产物就不发，而不是盲发一个必然 404 的请求、再
        靠 `recoverable` 把它静默掉（那是把正常状态建模成 HTTP 错误的根源）。

        ⚠️ **复用 `list_markdown_entries`、不另写一份索引访问**：同一份真相读两遍写法
        是模式 #54 的温床（同源数量记错比漏改更糟）。这里只在外面补一把锁——一次
        加锁查完整个窗口，因为调用方每次刷新列表都要问全库一遍。
        """
        with self._lock:
            return {
                tx_id: sorted({e.get("mode", "") for e in self.list_markdown_entries(tx_id)
                               if e.get("mode")})
                for tx_id in transcript_ids
            }

    def get_markdowns_by_transcript(self, transcript_id: str,
                                     mode: str = "", version: int | None = None) -> list[dict]:
        """该文档的产物版本（含正文），**版本降序**（`[0]` 即最新）。

        每条 = index 条目 + `content`（含自证头）+ **四个附加字段**：
        `chars`（纯正文字数）/ `edited` / `source`（生成用的模型）/ `followUps`（追问建议）。

        ⚠️ 前三个从自证头**现算**，刻意**不进 index**：index 一删就丢，而这三个全都
        写在文件里（`generatedAt` / `generated.model` / `edit`）。两处各存一份就是双真相源
        （模式 #54），且漂移时**没有任何报错**——`chars` 会悄悄与正文对不上。

        ⚠️ `followUps` 是**第四个**，但**不是同一种东西**：它**不在自证头里**，住在
        **第三个存储位置** `v{N}.followups.json`（旁路文件，见 `markdown_followup_path`）。
        同样不进 index（理由同上）。⚠️ 别把它当「删了能重算」的派生字段——**删掉旁路
        文件就真的没有了**，它本身就是真相源。
        ⚠️ **有才给键、空不给**（与 `chars` 的「缺就不给」同款纪律）：`"followUps"` 缺
        表示「没生成过」，不是「生成了但为空」——两者在 UI 上都不显示，但把它填成 `[]`
        会让「有没有这个能力」从数据上再也分不出来。

        ⚠️ `version` 过滤是**导出「按版本导出」的落点**（`routers/export.py`）；
        显式指定版本查不到时返回空列表，由调用方决定报错，**不在这里回退到最新版**。
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

        result = []
        for meta in entries:
            path = markdown_version_path(doc_dir, meta.get("mode", ""),
                                         meta.get("version", 0))
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            head = parse_artifact_meta(text)[0] or {}
            item = {
                **meta,
                "content": text,
                # ⚠️ 标记行不算「正文字数」：它是协议元素，读者看不到它（`strip_blocks`
                # 只剥标记行、块内文字照算）。
                "chars": len(strip_blocks(strip_meta(text))),
                "edited": bool((head.get("edit") or {}).get("edited")),
                "source": (head.get("generated") or {}).get("model", ""),
            }
            # 追问旁路文件（本来就要逐版读盘，这里是搭车的一次 exists 快判）
            fu = self.read_artifact_followups(transcript_id, meta.get("mode", ""),
                                              meta.get("version", 0))
            if fu:
                item["followUps"] = fu
            result.append(item)
        return sorted(result, key=lambda e: e.get("version", 0), reverse=True)

    # ── 追问建议（旁路文件，与 v{N}.md 并排）──

    def save_artifact_followups(self, tx_id: str, mode: str, version: int,
                                questions: list[str], model: str = "") -> bool:
        """写 `{doc_dir}/{mode}/v{N}.followups.json`（与 `v{N}.md` 并排）。

        ⚠️ **空列表 / 非法版本直接拒绝**：写一个 `questions: []` 的文件等于制造「生成过
        但为空」的假象；`version <= 0` 更糟——`v0.followups.json` 是**读不回来的垃圾**
        （读取路径按 `version=N` 拼，版本号从 index/文件名来，v0 永远不是合法版本）。
        返回 `False` 让调用方把这件事**显式化**（推 `error` 字段），不静默。
        """
        if not questions or version <= 0:
            return False
        doc_dir = self.get_doc_dir(tx_id)
        if not doc_dir:
            logger.warning("save_artifact_followups: 文档目录不存在 tx=%s", tx_id)
            return False
        path = markdown_followup_path(doc_dir, mode, version)
        payload = {
            "v": 1,
            "questions": [q for q in questions if isinstance(q, str)],
            "generatedAt": datetime.now().isoformat(),
            "model": model or "",
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2))
        except OSError as e:
            logger.warning("save_artifact_followups: 写盘失败 %s (%s)", path.name, str(e)[:80])
            return False
        return True

    def read_artifact_followups(self, tx_id: str, mode: str, version: int) -> list[str]:
        """读 `{doc_dir}/{mode}/v{N}.followups.json`；**缺 / 坏一律 `[]`，永不抛**。

        ⚠️ 旁路文件损坏**不该挡阅读**（正文本体完好）——所以这里吞掉解析失败、只留
        warning。这与 `save_artifact_followups` 的「显式拒绝」是**两个方向**的语义：
        写侧失败要让人知道（承诺没兑现），读侧失败只是少一张卡（内容一字不少）。
        """
        if not mode or version <= 0:
            return []
        doc_dir = self.get_doc_dir(tx_id)
        if not doc_dir:
            return []
        path = markdown_followup_path(doc_dir, mode, version)
        # ⚠️ 快判：没勾选过追问的文档**必然**没这个文件，一次 `stat` 就返回
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.warning("read_artifact_followups: 文件损坏，忽略 %s (%s)",
                           path.name, str(e)[:80])
            return []
        qs = data.get("questions") if isinstance(data, dict) else None
        if not isinstance(qs, list):
            return []
        return [q for q in qs if isinstance(q, str) and q.strip()]

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
