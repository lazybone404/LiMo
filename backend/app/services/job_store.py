"""长操作任务的持久化层 —— `{data_dir}/jobs.db` 的读写点。

## 为什么要有这个文件

有一类操作，它的**结果不在发起它的那次交互里**：合成产出的是另一篇文档，插件依赖
安装产出的是装好的 `.venv`。它们此前的状态住在**发起它们的组件**里，于是组件一卸载
（切标签、关面板、刷新页面）进度就没了 —— 而操作本身照常推进。更糟的是按钮复位，
用户可以对同一批文档再点一次（合成 = 重复计费）。

（哪些操作算这一类，判据只有一份，写在 `job_handlers.py` 的模块文档里；这里不重复。）

这里给每个操作一个 id、一次落盘、一份可轮询的状态。前端从这个 id 出发去读，
前端组件在不在都不影响任务本身。

## 为什么是独立文件 `jobs.db`

不并进 `search.db`：那边有「损坏即删库重建」的自愈逻辑
（`SearchEngine._recover_db`），重建会连任务记录一起清掉 —— 而任务记录正是
「上次那个操作到底做完了没有」的唯一线索。

## 三条纪律

1. **连接必须真关**（`_conn` 的 docstring 说明了为什么 `with sqlite3.Connection`
   不够）。Windows 上句柄没释放时文件删不掉、异常路径会把原始错误盖成
   `WinError 32`。
2. **认领是一条带条件的 UPDATE**（`claim_next`）。读出来再写回去，在两个 worker
   的场景下会让同一个任务被执行两次。
3. **幂等靠唯一索引，不靠先查后插**。先查后插在并发下挡不住，而且撞了约束没有
   「返回已有那条」的现成语义。
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

#: 任务的合法状态。`queued` / `running` 合称「进行中」，是前端默认要的那一档。
QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"

ACTIVE_STATUSES = (QUEUED, RUNNING)
TERMINAL_STATUSES = (DONE, FAILED, CANCELLED)

#: `jobs.db` 文件名。单独一份，理由见模块文档。
DB_FILENAME = "jobs.db"

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    id               TEXT PRIMARY KEY,
    kind             TEXT NOT NULL,
    idempotency_key  TEXT,
    payload          TEXT NOT NULL DEFAULT '{}',
    status           TEXT NOT NULL,
    progress         REAL,
    message          TEXT,
    result           TEXT,
    error            TEXT,
    attempts         INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    dismissed        INTEGER NOT NULL DEFAULT 0,
    created_at       REAL NOT NULL,
    started_at       REAL,
    finished_at      REAL
);
-- ⚠️ 唯一索引**只盖进行中**的状态：幂等键要挡的是「同一件事同时触发两次」，
-- 不是「这辈子只能做一次」。盖住终态的话，一次安装失败之后用户再点安装
-- 会拿到那条旧的失败记录（按钮看起来没反应），而重试本来就是他要的动作。
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_idem
    ON jobs(idempotency_key)
    WHERE idempotency_key IS NOT NULL AND status IN ('queued', 'running');
CREATE INDEX IF NOT EXISTS idx_jobs_status
    ON jobs(status, created_at);
"""


@dataclass
class Job:
    id: str
    kind: str
    status: str
    payload: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None
    progress: float | None = None
    message: str | None = None
    result: Any = None
    error: str | None = None
    attempts: int = 0
    cancel_requested: bool = False
    dismissed: bool = False
    created_at: float = 0.0
    started_at: float | None = None
    finished_at: float | None = None

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "payload": self.payload,
            "progress": self.progress,
            "message": self.message,
            "result": self.result,
            "error": self.error,
            "attempts": self.attempts,
            "cancel_requested": self.cancel_requested,
            "dismissed": self.dismissed,
            # 秒 → 毫秒：前端一律按 `Date.now()` 的口径算「已用」。
            "created_at": self.created_at * 1000,
            "started_at": self.started_at * 1000 if self.started_at else None,
            "finished_at": self.finished_at * 1000 if self.finished_at else None,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Job":
        def _load(raw: str | None) -> Any:
            if not raw:
                return None
            try:
                return json.loads(raw)
            except (TypeError, ValueError):
                # 坏 JSON 不该让整张列表读不出来（一条脏记录毁掉全部状态，
                # 而用户看到的是「任务都不见了」）。原样返回字符串，至少可诊断。
                return raw

        return cls(
            id=row["id"],
            kind=row["kind"],
            status=row["status"],
            payload=_load(row["payload"]) or {},
            idempotency_key=row["idempotency_key"],
            progress=row["progress"],
            message=row["message"],
            result=_load(row["result"]),
            error=row["error"],
            attempts=row["attempts"],
            cancel_requested=bool(row["cancel_requested"]),
            dismissed=bool(row["dismissed"]),
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )


class JobStore:
    """`jobs.db` 的唯一读写点。**不含调度语义** —— 什么时候取下一个任务是
    `job_queue` 的事，这里只提供「原子地认领一个」这条 SQL。"""

    def __init__(self, data_dir: str | Path):
        root = Path(data_dir)
        root.mkdir(parents=True, exist_ok=True)
        self._db_path = root / DB_FILENAME
        with self._conn() as conn:
            conn.executescript(_CREATE_SQL)

    @property
    def path(self) -> Path:
        return self._db_path

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """开一个连接，**并保证关闭**。约定与 `core/search.py::_conn` 同源。"""
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            try:
                yield conn
                conn.commit()
            except BaseException:
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                raise
        finally:
            conn.close()

    # ── 写入 ──

    def create(
        self,
        kind: str,
        payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        *,
        status: str = QUEUED,
    ) -> tuple[Job, bool]:
        """建一条任务。返回 `(job, created)`；`created=False` 表示幂等键命中了
        **一条正在进行中**的记录。

        ⚠️ **撞键时返回已有那条，而不是报错**：调用方要的语义是「这件事已经在跑了
        吗」，报错会把它逼成「先 GET 再 POST」，而那个缝隙正是重复触发的入口。
        ⚠️ 命中的只可能是 `queued` / `running`（唯一索引是部分索引，见 `_CREATE_SQL`）：
        上一条已经结束时，同样的键会建出一条新任务 —— 重试是用户的权利。
        """
        job_id = uuid.uuid4().hex
        now = time.time()
        payload_json = json.dumps(payload or {}, ensure_ascii=False)
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO jobs"
                " (id, kind, idempotency_key, payload, status, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (job_id, kind, idempotency_key, payload_json, status, now),
            )
            if cur.rowcount == 0:
                row = conn.execute(
                    "SELECT * FROM jobs WHERE idempotency_key = ?"
                    " AND status IN (?, ?) ORDER BY created_at DESC LIMIT 1",
                    (idempotency_key, QUEUED, RUNNING),
                ).fetchone()
                if row is not None:
                    return Job.from_row(row), False
                # 走到这里说明 ignore 的原因不是幂等键（id 撞了，概率可忽略），
                # 重新抛一次让问题显形，而不是返回一条不存在的任务。
                raise sqlite3.IntegrityError("任务写入失败，且未命中幂等键")
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return Job.from_row(row), True

    def claim_next(self) -> Job | None:
        """原子地认领最早的一条 `queued`。没有则返回 `None`。

        ⚠️ **认领 = 一条带 `status='queued'` 条件的 UPDATE**。分成「读出来 → 写回
        running」两步时，两个同时醒来的 worker 会都认为自己拿到了同一个任务。
        """
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT id FROM jobs WHERE status = ? ORDER BY created_at LIMIT 1",
                (QUEUED,),
            ).fetchone()
            if row is None:
                return None
            job_id = row["id"]
            conn.execute(
                "UPDATE jobs SET status = ?, started_at = ?, attempts = attempts + 1"
                " WHERE id = ? AND status = ?",
                (RUNNING, time.time(), job_id, QUEUED),
            )
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return Job.from_row(row)

    def report(self, job_id: str, *, progress: float | None = None,
               message: str | None = None) -> None:
        """更新进度 / 说明。`None` 的字段保持原值。"""
        sets, args = [], []
        if progress is not None:
            sets.append("progress = ?")
            args.append(max(0.0, min(1.0, float(progress))))
        if message is not None:
            sets.append("message = ?")
            args.append(message)
        if not sets:
            return
        args.append(job_id)
        with self._conn() as conn:
            conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", args)

    def finish(self, job_id: str, status: str, *, result: Any = None,
               error: str | None = None) -> None:
        if status not in TERMINAL_STATUSES:
            raise ValueError(f"finish 只接受终态：{status}")
        with self._conn() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, result = ?, error = ?, finished_at = ?,"
                " progress = CASE WHEN ? = 'done' THEN 1.0 ELSE progress END"
                " WHERE id = ?",
                (
                    status,
                    json.dumps(result, ensure_ascii=False, default=str) if result is not None else None,
                    error,
                    time.time(),
                    status,
                    job_id,
                ),
            )

    def request_cancel(self, job_id: str) -> Job | None:
        """请求取消。`queued` 的直接落成 `cancelled`；`running` 的置旗标，
        由 worker 在下一个检查点收尾。终态的不动。"""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            job = Job.from_row(row)
            if job.status == QUEUED:
                conn.execute(
                    "UPDATE jobs SET status = ?, finished_at = ?, cancel_requested = 1"
                    " WHERE id = ? AND status = ?",
                    (CANCELLED, time.time(), job_id, QUEUED),
                )
            elif job.status == RUNNING:
                conn.execute(
                    "UPDATE jobs SET cancel_requested = 1 WHERE id = ? AND status = ?",
                    (job_id, RUNNING),
                )
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return Job.from_row(row)

    # ── 读取 ──

    def get(self, job_id: str) -> Job | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return Job.from_row(row) if row else None

    def list(self, statuses: tuple[str, ...] | None = None, limit: int = 50,
             *, include_dismissed: bool = False) -> list[Job]:
        conds: list[str] = []
        args: list[Any] = []
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            conds.append(f"status IN ({placeholders})")
            args.extend(statuses)
        # 「忽略掉的失败」不该在下次刷新页面时又冒出来 —— `dismiss` 是持久化的
        # 确认动作，不是一次性的 UI 隐藏。
        if not include_dismissed:
            conds.append("dismissed = 0")
        sql = "SELECT * FROM jobs"
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, int(limit)))
        with self._conn() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [Job.from_row(r) for r in rows]

    def dismiss(self, job_id: str) -> Job | None:
        """把一条**终态**任务从列表里收起来。进行中的不允许 dismiss ——
        那等于「看不见但还在跑」，正是这一轮要消灭的形态。"""
        with self._conn() as conn:
            conn.execute(
                "UPDATE jobs SET dismissed = 1 WHERE id = ? AND status IN (?, ?, ?)",
                (job_id, *TERMINAL_STATUSES),
            )
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return Job.from_row(row) if row else None

    # ── 启动恢复 ──

    def recover(self, retryable_kinds: set[str]) -> dict[str, int]:
        """把上次进程留下的 `running` 收尾。返回 `{requeued, failed}`。

        ⚠️ **恢复语义按 kind 分，这是最容易做错的一处**：
        - 重跑无副作用的（插件依赖安装、重索引）→ 改回 `queued`，下次接着跑；
        - 重跑要再花钱 / 会产生半程副作用的（合成、分类提交）→ 标 `failed` 并写明
          「进程中断」，不自动重跑。自动重跑一次计费 LLM 调用 = 用户为一次崩溃付两次钱。
        """
        out = {"requeued": 0, "failed": 0}
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT id, kind, attempts FROM jobs WHERE status = ?", (RUNNING,)
            ).fetchall()
            now = time.time()
            for row in rows:
                if row["kind"] in retryable_kinds:
                    conn.execute(
                        "UPDATE jobs SET status = ?, started_at = NULL,"
                        " cancel_requested = 0 WHERE id = ?",
                        (QUEUED, row["id"]),
                    )
                    out["requeued"] += 1
                else:
                    conn.execute(
                        "UPDATE jobs SET status = ?, error = ?, finished_at = ?"
                        " WHERE id = ?",
                        (FAILED, "进程中断，未自动重跑", now, row["id"]),
                    )
                    out["failed"] += 1
        return out

    def prune(self, keep_terminal: int = 200) -> int:
        """只保留最近 `keep_terminal` 条终态任务，其余删掉。返回删除条数。

        任务表不该无限长，但它也不是日志 —— 保留最近若干条是为了「昨天那个安装
        到底成了没有」还查得到。
        """
        with self._conn() as conn:
            placeholders = ",".join("?" for _ in TERMINAL_STATUSES)
            cur = conn.execute(
                "DELETE FROM jobs WHERE id IN ("
                f"  SELECT id FROM jobs WHERE status IN ({placeholders})"
                "   ORDER BY created_at DESC LIMIT -1 OFFSET ?"
                ")",
                (*TERMINAL_STATUSES, max(0, int(keep_terminal))),
            )
            return cur.rowcount or 0
