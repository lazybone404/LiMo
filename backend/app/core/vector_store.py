"""向量索引的存储层 —— `{data_dir}/vec.db`，三张表。

与 [`vector_index.py`](vector_index.py) 的分工：那边是**纯算子**（切块 / 分组排名 /
RRF，零 I/O，产品与评测共用），这边是**唯一会碰盘的向量侧模块**。

## 为什么是异库（不与 `search.db` 同库）

原设计写的是「同库不同表」，2026-10-26 改为异库。三条理由都是**现在就存在**的机制，
不是预优化：

| 理由 | 一旦触发会发生什么 |
|---|---|
| `SearchEngine._recover_db()` 删**整个文件**（`.db` / `-wal` / `-shm` 一起 unlink） | 字面索引坏一次，把「几秒 CPU 重建」偷偷升级成「**整库重花一次编码钱**」 |
| SQLite 写锁是**库级**的 | `rebuild()` 的大事务与 worker 的批量写盘互相阻塞 |
| `rebuild(vacuum=True)` 会搬全表 | 为回收 FTS 碎片，顺带搬几百 MB 的向量 BLOB |

跨库事务根本不需要 —— `vec_state.tx_id` 本来就是**软引用**（文档删了行不消失，
靠对账清孤儿，见 `docs/search-retrieval.md` §4.6）。

## 落盘位置：跟着 `data_dir` 走（2026-10-26 拍板）

⚠️ 这里回答的是设计文档 §3.2 留的悬念（原设计写「不能照抄 `search.db`，
可能放 `%LOCALAPPDATA%`」）。**决定：仍然放 `{data_dir}/vec.db`**，理由只有一条但很硬：

**向量必须与它描述的那批文档同生共死。** 固定放 `%LOCALAPPDATA%` 会让「用户配了
两个 `data_dir` 并在其间切换」变成**静默错位** —— A 库的向量被 B 库拿去检索，
余弦照样算得出一个数、结果照常返回、界面上完全看不出来。这正是下面「四元组」
要防的同一类事，而且它更隐蔽：四元组不符至少还能在开库时判定，跨库错位连判据都没有
（要修就得再存一份 `data_dir` 进 `vec_meta`，而它带来的「换个库就整库失效重算」
本身就是一笔莫名其妙的账单）。

反过来，原设计担心的「`~/.limo` 会被同步」是**不便**而不是**正确性**：最坏是
同步工具多传几百 MB，且**只对主动开启了向量检索的用户**发生。故选择正确性，
并把「`vec.db` 是派生缓存，删掉只会导致重算」写进文档供用户自行排除同步。

## 四元组是整体身份，任一不符即整体不可用

`model_id` / `dimension` / `chunk_size` / `schema_version` —— **没有「降级用」这一档**。
硬算的失效形态最坏：余弦照样算得出一个数，检索照常返回文档，只是排序毫无意义，
而界面上完全看不出来。故开库时校验（`ensure_identity`），不符即整体停用；
**不自动重建**（重建要花钱，等于「用户换了个模型，程序自作主张替他花了一笔」）。

`schema_version` 从第一版就要有：以后改切块规则或向量编码方式时，老索引会继续
按旧规则算、两边都报「成功」—— 与 `_EMPTY_CONTAINERS` 那类静默失配是同一个形态。

## 写入语义：只打标记，不同步编码

写点（`update_index` 的五个调用方）只把该篇标成 `pending`，**一次 embedding 调用都不发**：
一次网络往返会卡住「保存」按钮，用户会以为卡死了。真正的编码归后台 worker。
`pending` 时**不删 `vec_chunks`** —— 块级 hash 增量要靠旧块比对（§4.4）。

⚠️ `mark_pending` 会把 `content_hash` 置空、`fail_count` 归零：内容变了，之前的失败
记录不再适用（这是一次新的内容、新的机会）。`fail_count` 的闸门语义仍然成立 ——
worker 反复失败且不经过写点时，它照样能累到上限。
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import numpy as np

from app.core import logging as log

#: 索引口径版本。**改切块规则 / 向量编码方式时必须 +1** —— 否则老块会按旧规则
#: 参与计算、两边都报「成功」，而排序已经毫无意义。
SCHEMA_VERSION = 1

#: 默认块大小（字符）。来自 2026-10-26 的 A/B：固定切 200 跑赢 H2 语义切与 400/1000，
#: 见 `docs/search-retrieval.md` §3.2。落库只是**记录**，真正切块时由调用方传值。
DEFAULT_CHUNK_SIZE = 200

#: 文档级状态集合。不在表上加 CHECK（SQLite 改 CHECK 要重建表，而这是纯派生缓存，
#: 重建的代价是花钱），改在写入处显式校验 —— 值写错要让调用当场炸，不是静默存进去。
STATUSES = ("ok", "pending", "failed")

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS vec_meta (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    model_id       TEXT    NOT NULL,
    dimension      INTEGER NOT NULL,
    chunk_size     INTEGER NOT NULL,
    schema_version INTEGER NOT NULL,
    built_at       TEXT
);
CREATE TABLE IF NOT EXISTS vec_chunks (
    tx_id     TEXT    NOT NULL,
    chunk_idx INTEGER NOT NULL,
    text      TEXT,
    text_hash TEXT,
    vec       BLOB,
    PRIMARY KEY (tx_id, chunk_idx)
);
CREATE TABLE IF NOT EXISTS vec_state (
    tx_id        TEXT PRIMARY KEY,
    content_hash TEXT,
    chunk_count  INTEGER DEFAULT 0,
    status       TEXT    NOT NULL,
    fail_count   INTEGER DEFAULT 0,
    error        TEXT,
    indexed_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_vec_state_status ON vec_state(status);
"""


def chunk_hash(text: str) -> str:
    """块级指纹 —— 增量判定的**唯一依据**（§4.4）。

    取 sha256 前 32 个 hex（128 bit）：35k 块下生日碰撞概率约 2^-33，可忽略；
    完整 64 字符会让 2 万块的 `vec_chunks` 多出 0.6 MB 纯文本。
    ⚠️ 算法只此一处 —— worker 与对账各自实现一份时，「内容没变却重编」或
    「内容变了却不更新」两种失效都不会报错。
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def pack_vec(vec) -> bytes:
    """float32 小端打包。已 L2 归一化（归一化在引擎协议里，见 `embedding_base`）。"""
    return np.asarray(vec, dtype=np.float32).tobytes()


def unpack_vec(blob: bytes) -> np.ndarray:
    """`pack_vec` 的逆。返回的是 `frombuffer` 的**只读视图**，要改请先 `.copy()`。"""
    return np.frombuffer(blob, dtype=np.float32)


class VectorStore:
    """`vec.db` 的读写。**构造失败不抛** —— 坏掉的向量库不该让应用起不来。

    与 `SearchEngine` 同款取舍：所有文档仍能从磁盘读到、字面检索照常，
    只有语义检索这一项能力没了。调用方靠 `available` 判断，再由状态区显式展示。
    """

    def __init__(self, data_dir: str):
        self._db_path = Path(data_dir) / "vec.db"
        self._lock = threading.Lock()
        self._last_error: str | None = None
        #: 「有新东西排队了」的通知，由 `vector_runtime` 在起 worker 时注册
        #: （见 `set_wake_callback`）。它是**唯一**能让 worker 立刻开工的东西 ——
        #: 没有它，`mark_pending` 之后只能等兜底轮询（改造前就是 60 秒）。
        self._wake_cb = None
        try:
            self._init_db()
        except Exception as e:  # noqa: BLE001 —— 任何异常都降级，不炸启动
            self._last_error = f"向量索引库打不开：{e}"
            log.warning("向量索引库打不开，语义检索将不可用",
                        err=str(e)[:200])

    # ── 基础设施 ──

    def _init_db(self):
        with self._lock, self._conn() as conn:
            conn.executescript(_CREATE_SQL)

    @property
    def db_path(self) -> Path:
        return self._db_path

    # ── 唤醒通知 ──

    def set_wake_callback(self, fn) -> None:
        """注册「有新东西排队了」的通知（传 `None` 摘掉）。

        ⚠️ **回调可能在别的线程里被调用**：`mark_pending` 的唯一生产调用点是
        `core/search.py::update_index`，而它常跑在 `asyncio.to_thread` 里
        （摄入/生成完成两条路径）。实现方必须自己处理线程安全 ——
        `vector_runtime._start_worker` 用 `loop.call_soon_threadsafe` 包了一层。

        ⚠️ **它只是「叫醒」，不是「必须被听见」**：回调异常一律在 `_notify_wake`
        吞掉。叫不醒 worker 的代价是等一次兜底轮询，而让异常冒出去会把
        「生产标记」这一步整个弄失败 —— 那才是真的丢索引。
        """
        self._wake_cb = fn

    def _notify_wake(self) -> None:
        """叫醒 worker。**必须在锁外调用**（见 `mark_pending` 末尾）。"""
        cb = self._wake_cb
        if cb is None:
            return
        try:
            cb()
        except Exception as e:  # noqa: BLE001 —— 见 `set_wake_callback` 的注释
            log.warning("向量索引唤醒失败（等兜底轮询）", err=f"{type(e).__name__}: {e}"[:200])

    @contextmanager
    def _conn(self):
        """同 `SearchEngine._conn`：**必须真关**（`with sqlite3.Connection` 只做
        commit/rollback，**不释放文件句柄**；Windows 上句柄没释放时删不掉 `vec.db`）。"""
        conn = sqlite3.connect(str(self._db_path))
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

    @property
    def available(self) -> bool:
        return self._last_error is None

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def note_error(self, message: str) -> None:
        """由调用方记录失败，使状态可见（同 `SearchEngine.note_error`）。"""
        self._last_error = message
        log.warning("向量索引不可用", err=message[:200])

    # ── 身份（四元组） ──

    def read_identity(self) -> dict | None:
        """已落库的四元组；`None` = 从未建过向量索引。"""
        if not self.available:
            return None
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT model_id, dimension, chunk_size, schema_version, built_at"
                " FROM vec_meta WHERE id = 1"
            ).fetchone()
        if not row:
            return None
        return {
            "modelId": row[0], "dimension": row[1], "chunkSize": row[2],
            "schemaVersion": row[3], "builtAt": row[4],
        }

    def ensure_identity(self, model_id: str, dimension: int,
                        chunk_size: int = DEFAULT_CHUNK_SIZE) -> bool:
        """校验四元组；**首次**调用时把它落库（这就是「初始化」）。返回能否使用。

        - 未建过 → 写入并返回 `True`
        - 完全一致 → `True`（不写盘）
        - 任一不符 → `False`，**不写、不删、不重建** —— 向量侧整体停用，
          等用户确认后由调用方显式清空。

        ⚠️ **`built_at` 只在首次写入时落，不随后续增量刷新**：它的语义是
        「这批向量的口径何时确定」，不是「最后一次编码时间」（后者在 `vec_state.indexed_at`）。
        """
        old = self.read_identity()
        if old is None:
            with self._lock, self._conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO vec_meta"
                    "(id, model_id, dimension, chunk_size, schema_version, built_at)"
                    " VALUES (1, ?, ?, ?, ?, ?)",
                    (model_id, int(dimension), int(chunk_size), SCHEMA_VERSION,
                     datetime.now().isoformat(timespec="seconds")),
                )
            log.info("向量索引口径已初始化",
                     model=model_id, dim=int(dimension), chunk=int(chunk_size))
            return True
        same = self._identity_matches(old, model_id, dimension, chunk_size)
        if not same:
            log.warning("向量索引口径不符，向量侧停用（需用户确认后重建）",
                        stored=old, expected=self._identity_tuple(
                            model_id, dimension, chunk_size))
        return same

    @staticmethod
    def _identity_tuple(model_id: str, dimension: int, chunk_size: int) -> dict:
        return {"modelId": model_id, "dimension": int(dimension),
                "chunkSize": int(chunk_size), "schemaVersion": SCHEMA_VERSION}

    @classmethod
    def _identity_matches(cls, old: dict, model_id: str, dimension: int,
                          chunk_size: int) -> bool:
        """四元组比对 —— **唯一一份判据**（`ensure_identity` 与 `identity_ok` 共用）。

        ⚠️ 抄第二份的失效形态：查询侧与写侧的「什么算同一个口径」悄悄分叉，
        于是「worker 认为要重建」而「查询照用旧向量」—— 产物上表现成
        「勾了语义检索、结果却是上一次模型的语义」，零报错。
        """
        cur = cls._identity_tuple(model_id, dimension, chunk_size)
        return all(old[k] == v for k, v in cur.items())

    def identity_ok(self, model_id: str, dimension: int,
                    chunk_size: int = DEFAULT_CHUNK_SIZE) -> bool:
        """**只读**版四元组比对。查询侧必须用这个，不要用 `ensure_identity`。

        ⚠️ 后者在「从未建过索引」时会**写库**（它的首次调用就是初始化），
        而查询路径不该有写副作用 —— 一次搜索顺手把 `vec_meta` 定死成当前模型，
        会让紧接着启动的 worker 以为「口径早就是它了」。
        """
        old = self.read_identity()
        return bool(old) and self._identity_matches(old, model_id, dimension, chunk_size)

    def reset_identity(self, model_id: str, dimension: int,
                       chunk_size: int = DEFAULT_CHUNK_SIZE) -> None:
        """**推倒重来**：清空全部块与状态，写入新四元组。

        ⚠️ 只在用户**显式确认重建**时调 —— 这是花钱且不可逆的动作。
        `ensure_identity` 发现不符时**刻意不自动调它**（§4.2 的 2026-10-26 修订）。
        """
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM vec_chunks")
            conn.execute("DELETE FROM vec_state")
            conn.execute("DELETE FROM vec_meta")
            conn.execute(
                "INSERT INTO vec_meta"
                "(id, model_id, dimension, chunk_size, schema_version, built_at)"
                " VALUES (1, ?, ?, ?, ?, ?)",
                (model_id, int(dimension), int(chunk_size), SCHEMA_VERSION,
                 datetime.now().isoformat(timespec="seconds")),
            )
        log.warning("向量索引已整体重建（旧块与状态已清空）",
                    model=model_id, dim=int(dimension), chunk=int(chunk_size))

    # ── 文档级状态 ──

    def mark_pending(self, tx_id: str) -> None:
        """标记该篇需要（重新）编码。**不发任何 embedding 调用。**

        ⚠️ **不删 `vec_chunks`**：块级 hash 增量要靠旧块比对，删了就只能整篇重编码。
        ⚠️ `content_hash` 置空 = 「这篇的哈希未知」→ worker 不走整篇跳过、正常分块比对。
        留旧值会让「hash 相等就跳过整篇」在内容已变时错误命中，且**零报错**。
        """
        if not self.available:
            return
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO vec_state"
                "(tx_id, content_hash, chunk_count, status, fail_count, error, indexed_at)"
                " VALUES (?, NULL, 0, 'pending', 0, NULL, NULL)"
                " ON CONFLICT(tx_id) DO UPDATE SET"
                "   content_hash = NULL, status = 'pending',"
                "   fail_count = 0, error = NULL",
                (tx_id,),
            )
        # ⚠️ **在锁外**：回调最终会 `call_soon_threadsafe(Event.set)`，而线程池里的
        # 事件循环可能正在等这把锁 —— 拿着锁去排队一个回调不会死锁，但会把
        # 「标记完成」与「worker 醒来」这件事的顺序绑在一起，没有必要。
        self._notify_wake()

    def mark_ok(self, tx_id: str, chunk_count: int, content_hash: str | None = None) -> None:
        """该篇编码完成。`content_hash` 是快速路径用的整篇指纹（§4.4）。"""
        if not self.available:
            return
        with self._lock, self._conn() as conn:
            self._upsert_state(conn, tx_id, status="ok", content_hash=content_hash,
                               chunk_count=int(chunk_count), fail_count=0,
                               error=None, indexed_at=datetime.now().isoformat(timespec="seconds"))

    def mark_failed(self, tx_id: str, error: str) -> None:
        """该篇失败：`fail_count` 真的 +1（**这是「花钱的闸门」**，不是统计字段）。

        只在内存里累加的话重启即归零 ⇒ 无限重试 ⇒ 无限烧钱。故必须落盘。
        """
        if not self.available:
            return
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO vec_state(tx_id, status, fail_count, error)"
                " VALUES (?, 'failed', 1, ?)"
                " ON CONFLICT(tx_id) DO UPDATE SET"
                "   status = 'failed', fail_count = vec_state.fail_count + 1,"
                "   error = excluded.error",
                (tx_id, (error or "")[:400]),
            )

    @staticmethod
    def _upsert_state(conn, tx_id, *, status, content_hash, chunk_count,
                      fail_count, error, indexed_at):
        if status not in STATUSES:
            raise ValueError(f"未知的向量状态：{status!r}")
        conn.execute(
            "INSERT INTO vec_state"
            "(tx_id, content_hash, chunk_count, status, fail_count, error, indexed_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(tx_id) DO UPDATE SET"
            "   content_hash = excluded.content_hash,"
            "   chunk_count  = excluded.chunk_count,"
            "   status       = excluded.status,"
            "   fail_count   = excluded.fail_count,"
            "   error        = excluded.error,"
            "   indexed_at   = excluded.indexed_at",
            (tx_id, content_hash, int(chunk_count), status,
             int(fail_count), error, indexed_at),
        )

    def get_state(self, tx_id: str) -> dict | None:
        if not self.available:
            return None
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT tx_id, content_hash, chunk_count, status, fail_count,"
                " error, indexed_at FROM vec_state WHERE tx_id = ?", (tx_id,)
            ).fetchone()
        return None if not row else {
            "txId": row[0], "contentHash": row[1], "chunkCount": row[2],
            "status": row[3], "failCount": row[4], "error": row[5],
            "indexedAt": row[6],
        }

    def take_pending(self, limit: int = 20) -> list[str]:
        """取一批待编码的文档 id。**只读不写状态** —— `pending` 要保持到
        `mark_ok` / `mark_failed` 为止，否则 worker 崩在中途就丢了这条。
        """
        if not self.available:
            return []
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT tx_id FROM vec_state WHERE status = 'pending'"
                " ORDER BY tx_id LIMIT ?", (int(limit),)
            ).fetchall()
        return [r[0] for r in rows]

    def retry_ids(self, max_fail: int = 3, limit: int = 20) -> list[str]:
        """失败但未到上限的文档（§4.6 对账的第 ③ 条）。到上限的**不再返回**。"""
        if not self.available:
            return []
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT tx_id FROM vec_state WHERE status = 'failed'"
                " AND fail_count < ? ORDER BY tx_id LIMIT ?",
                (int(max_fail), int(limit)),
            ).fetchall()
        return [r[0] for r in rows]

    def actionable_counts(self, max_fail: int = 3) -> tuple[int, int]:
        """返回 `(待编码, 待重试)` 篇数 —— 给 worker 的阈值闸门用（`_should_run`）。

        ⚠️ **不能拿 `take_pending(limit)` 的长度代替**：那个有 `limit`（默认 20），
        攒到 500 篇时它也只会说 20 —— 阈值判据会永远卡在 20 附近，用户把阈值
        设成 100 就再也触发不了，而界面上「待索引 500」看起来一切正常。
        ⚠️ 也不复用 `status()`：它为了给设置页看多读了身份与文件体积，而这里
        **每轮空转都要调一次**（60 秒一回），不该付那份开销。
        """
        if not self.available:
            return (0, 0)
        try:
            with self._lock, self._conn() as conn:
                pending = conn.execute(
                    "SELECT count(*) FROM vec_state WHERE status = 'pending'"
                ).fetchone()[0]
                retry = conn.execute(
                    "SELECT count(*) FROM vec_state WHERE status = 'failed'"
                    " AND fail_count < ?", (int(max_fail),)
                ).fetchone()[0]
            return (int(pending), int(retry))
        except Exception as e:  # noqa: BLE001 —— 计数失败按「这轮没活」处理
            # ⚠️ **不写 `_last_error`**：同 `status()` 的纪律 —— `available` 的定义是
            # 「`_last_error is None`」，一次瞬时读失败会让语义检索进入只有手动重建
            # 才能解除的「不可用」态，而界面上不会说发生了什么。
            log.warning("向量索引计数失败（这轮跳过）", err=f"{type(e).__name__}: {e}"[:200])
            return (0, 0)

    # ── 块 ──

    def get_chunks(self, tx_id: str) -> list[dict]:
        """按 `chunk_idx` 升序返回该篇的块。`vec` 是**原始 bytes**，用 `unpack_vec` 解。"""
        if not self.available:
            return []
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT chunk_idx, text, text_hash, vec FROM vec_chunks"
                " WHERE tx_id = ? ORDER BY chunk_idx", (tx_id,)
            ).fetchall()
        return [{"chunkIdx": r[0], "text": r[1], "textHash": r[2], "vec": r[3]}
                for r in rows]

    def replace_chunks(self, tx_id: str, chunks: list[dict]) -> None:
        """整体替换该篇的块（一次事务，原子）。

        增量语义由 worker 承担（逐块比 `text_hash`、只对变了的块调编码、复用的块
        带上旧 `vec` 原样传来）；本方法只负责「删干净 + 写进去」这一件事 ——
        把「比 hash」也放进来会让「谁该被编码」有两个判据。

        `chunks` 每项：`{"idx", "text", "hash", "vec"}`（`vec` 可以是 bytes 或 ndarray）。
        """
        if not self.available:
            return
        rows = []
        for c in chunks:
            vec = c["vec"]
            blob = vec if isinstance(vec, (bytes, bytearray)) else pack_vec(vec)
            rows.append((tx_id, int(c["idx"]), c.get("text"),
                         c.get("hash") or chunk_hash(c.get("text") or ""), blob))
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM vec_chunks WHERE tx_id = ?", (tx_id,))
            conn.executemany(
                "INSERT INTO vec_chunks(tx_id, chunk_idx, text, text_hash, vec)"
                " VALUES (?, ?, ?, ?, ?)", rows)

    def delete_doc(self, tx_id: str) -> None:
        """删掉该篇的全部块与状态行（文档被删除 / 对账清孤儿时用）。"""
        if not self.available:
            return
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM vec_chunks WHERE tx_id = ?", (tx_id,))
            conn.execute("DELETE FROM vec_state WHERE tx_id = ?", (tx_id,))

    def known_ids(self) -> set[str]:
        """`vec_state` 里出现过的文档 id（对账用：与文档表求差找孤儿）。"""
        if not self.available:
            return set()
        with self._lock, self._conn() as conn:
            return {r[0] for r in conn.execute("SELECT tx_id FROM vec_state")}

    # ── 查询侧：全库向量矩阵 ──

    def load_matrix(self) -> tuple[np.ndarray, np.ndarray] | None:
        """全库块向量 → `(mat[n, dim], owner[n])`；空库 / 库不可用 → `None`。

        这是产品侧**唯一的「把向量读成矩阵」入口**。切块分组与排名不在这里
        （`core/vector_index.py` 的 `vector_rank_scored` 是唯一家，产品与评测共用）。

        ⚠️ **`order by rowid` 不是为了正确性**（`np.maximum.at` 聚合与行序无关），
        而是为了让同一份库两次加载得到逐字节相同的矩阵 —— 调试数值、比对 A/B
        时，一个「结果对但顺序抖动」的矩阵会让差异看起来像是逻辑变了。

        ⚠️ **维度不符的行跳过、不整库失败**：`vec_meta.dimension` 与某个 blob 的
        长度对不上，只可能是人工改库或旧版本残留（四元组每次启动都校验）。让这一行
        把整个语义检索打挂，比少一块向量的代价大得多 —— 而且那一条 warning 是
        唯一能让人发现「库被手工动过」的线索。

        **刻意不做缓存**：查询是一次 250ms 消抖节拍一发的低频调用，而缓存键
        （`count` + `max(rowid)`）**并不可靠** —— SQLite 普通表的 rowid 会复用，
        `replace_chunks` 先 DELETE 再 INSERT 恰好能让「删一篇、加一篇」前后两个键
        完全重合，于是缓存静默返回上一次的向量。没有失效键正确的缓存，比没有缓存糟。
        """
        if not self.available:
            return None
        ident = self.read_identity()
        if not ident:
            return None
        dim = int(ident["dimension"])
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT tx_id, vec FROM vec_chunks WHERE vec IS NOT NULL ORDER BY rowid"
            ).fetchall()
        if not rows:
            return None

        mat = np.empty((len(rows), dim), dtype=np.float32)
        owner: list[str] = []
        bad = 0
        for tx_id, blob in rows:
            v = np.frombuffer(blob, dtype=np.float32)
            if v.size != dim:
                bad += 1
                continue
            mat[len(owner)] = v
            owner.append(tx_id)
        if bad:
            log.warning("向量索引存在维度不符的块（已跳过）", bad=bad, dim=dim)
        if not owner:
            return None
        # `mat[:n]` 是视图：父数组就是我们要的，不额外拷贝 26MB+
        return mat[: len(owner)], np.array(owner)

    # ── 状态快照 ──

    def status(self) -> dict:
        """给设置页 / 诊断用的只读快照（**不抛错**，取不到的项给 0）。"""
        out = {
            "available": self.available,
            "lastError": self._last_error,
            "identity": None,
            "docs": {"total": 0, "ok": 0, "pending": 0, "failed": 0},
            "chunks": 0,
            "sizeBytes": 0,
        }
        try:
            out["sizeBytes"] = self._db_path.stat().st_size
        except OSError:
            pass
        if not self.available:
            return out
        try:
            out["identity"] = self.read_identity()
            with self._lock, self._conn() as conn:
                for st, n in conn.execute(
                    "SELECT status, count(*) FROM vec_state GROUP BY status"
                ):
                    if st in out["docs"]:
                        out["docs"][st] = n
                out["docs"]["total"] = sum(out["docs"][k] for k in STATUSES)
                out["chunks"] = conn.execute("SELECT count(*) FROM vec_chunks").fetchone()[0]
        except Exception as e:  # noqa: BLE001 —— 状态查询自己失败也只是少个数
            out["lastError"] = f"读向量索引失败：{e}"
        return out
