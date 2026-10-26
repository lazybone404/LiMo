"""
全文搜索引擎 — SQLite FTS5 虚拟表。
存储在 {data_dir}/search.db，与现有文件系统存储互补。

用法：
  engine = SearchEngine(data_dir)
  engine.index("tr_xxx", title, raw_text)   # 摄入时自动建索引
  results = engine.search("关键词", limit=20)  # 返回 [(id, title, snippet), ...]
"""

import sqlite3
import re
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from app.core import logging as log


#: 建表语句。**列定义与 `is_schema_current()` 必须同源** —— 那里靠 `sqlite_master`
#: 里的 DDL 原文判断新旧，改了列名却没改判据，失效形态是「旧库被当成新库，
#: 首次 INSERT 才报 no column named ...」（启动即崩，但不在这条判据上）。
_CREATE_SQL = """
    CREATE VIRTUAL TABLE IF NOT EXISTS transcripts_fts
    USING fts5(id UNINDEXED, title, body UNINDEXED, toks, tokenize='unicode61')
"""


class SearchEngine:
    """SQLite FTS5 全文索引。每次摄入/重转录时增量更新。

    ⚠️ **三列各司其职，不能合并**（2026-10-26 重做）：
    - `title`  原文标题。索引，供英文/数字精确命中（BM25 有独立列权重）
    - `body`   清洗后的原文，**UNINDEXED** —— 只存不搜，唯一用途是**生成摘要**。
               中文在此保持连续（`机器学习`），因为它是给人看的；分词只在 `toks` 里做。
    - `toks`   检索用的 token 串（CJK 二字组 + 英文词 + 数字串），索引。
               **所有匹配都发生在这一列**。

    ⚠️ **为什么摘要不再来自 FTS5 的 `snippet()`**：那个函数只能从**被索引的列**
    取文本，而被索引列是二字组串 —— 摘要会变成 `机器 器学 学习`（改造前更早的
    形态是 `机 器 学 习`，同样不可读）。token 化是检索的内部表示，不该泄漏到展示层。
    """

    def __init__(self, data_dir: str):
        self._db_path = Path(data_dir) / "search.db"
        self._lock = threading.Lock()
        #: 上一次失败的原因（`None` = 当前可用）。**构造失败不抛** ——
        #: 一个坏掉的索引文件不该让整个应用起不来：所有文档仍能从磁盘读到，
        #: 只有检索这一项能力没了。调用方靠 `available` / `status()` 判断，
        #: 再由 `QueryService` 把它变成**显式错误**而不是「没搜到」。
        self._last_error: str | None = None
        #: 上次成功重建的时间（本地时间 ISO 串），供设置页显示「上次重建于…」。
        self._rebuilt_at: str | None = None
        try:
            self._init_db()
        except Exception as e:  # noqa: BLE001 —— 任何异常都降级，不炸启动
            self._last_error = f"索引库打不开：{e}"
            log.warning("全文索引库打不开，检索将不可用（可在设置页重建索引）",
                        err=str(e)[:200])

    def _init_db(self):
        """创建 FTS5 虚拟表（如果不存在）。

        ⚠️ **这里刻意不做结构迁移** —— 迁移要 `DROP TABLE`，那是数据丢失。
        旧库由调用方显式调 `migrate_schema()`（只有应用启动路径会调，它紧接着
        就会 `rebuild()`）。见 `migrate_schema` 的注释。
        """
        with self._lock, self._conn() as conn:
            conn.execute(_CREATE_SQL)
            conn.commit()

    @contextmanager
    def _conn(self):
        """开一个连接，**并保证关闭**。

        ⚠️ **必须真关**：`with sqlite3.Connection` 只做 commit / rollback，**不关闭
        文件句柄**（此前正是这么写的）。Windows 上句柄没释放时 `_recover_db()` 删不掉
        损坏的 `search.db`（`WinError 32 另一个程序正在使用此文件`）⇒ 「坏库自愈」
        这条路根本走不通，而它的表现只是「重建后依然不可用」—— 没有任何线索指向
        「文件被自己占着」。2026-10-26 由 `test_rebuild_recovers_from_corrupt_db` 实测抓到。

        顺带的好处：关闭连接会触发 WAL checkpoint，`-wal` 不会无限长大。
        """
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            try:
                yield conn
                conn.commit()
            except BaseException:
                # 连接本身已坏（文件被换掉 / 被占用）时 rollback 也会抛，
                # 不能让它盖住原始异常 —— 那会把「为什么失败」换成一个无关的错。
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                raise
        finally:
            conn.close()

    # ── 健康状态 ──

    @property
    def available(self) -> bool:
        """索引当前是否可用于检索。"""
        return self._last_error is None

    @property
    def last_error(self) -> str | None:
        """最近一次失败原因（`None` = 一切正常）。"""
        return self._last_error

    def note_error(self, message: str) -> None:
        """由调用方（启动流程）记录失败，使状态可见。

        ⚠️ 这条路径替代了此前 `main.py` 里「只打一条 warning 然后继续」的做法：
        那时用户看到的是**搜索永远没结果**，没有诊断、也没有修复入口。
        """
        self._last_error = message
        log.warning("全文索引不可用", err=message[:200])

    def status(self) -> dict:
        """给设置页 / 诊断用的只读快照（不抛错）。

        ⚠️ **只读是认真的**：这里遇到的失败只写进返回的 dict（`statusError`），
        **不碰 `self._last_error`**。`available` 的定义就是 `_last_error is None`
        —— 让它被一次瞬时读失败毒化，等于「打开设置页看一眼，全文检索就被永久判死」，
        而解除它只有手动重建一条路（用户完全不知道发生了什么）。
        `VectorStore.status()` 同款做法，两处判据一致。
        """
        size = 0
        try:
            size = self._db_path.stat().st_size
        except OSError:
            pass
        doc_count = 0
        status_error: str | None = None
        if self.available:
            try:
                with self._lock, self._conn() as conn:
                    doc_count = conn.execute(
                        "SELECT count(*) FROM transcripts_fts"
                    ).fetchone()[0]
            except Exception as e:  # noqa: BLE001 —— 状态查询自己失败也只是状态里少个数
                doc_count = 0
                status_error = f"读索引失败：{e}"
        return {
            "available": self.available,
            "docCount": doc_count,
            "sizeBytes": size,
            "lastError": self._last_error,
            "statusError": status_error,
            "rebuiltAt": self._rebuilt_at,
        }

    def _recover_db(self) -> bool:
        """索引库不可用时删掉重建。

        ⚠️ **只在 `rebuild()` 里调** —— 那是「推倒重来」的语义，用户已经在
        重建入口上按了确认。不要在构造 / 读路径上调：删文件是不可逆动作。
        """
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(self._db_path) + suffix)
            try:
                if p.exists():
                    p.unlink()
            except OSError as e:
                self._last_error = f"无法删除损坏的索引文件：{e}"
                return False
        try:
            self._init_db()
            self._last_error = None
            log.warning("索引库已重建（旧的损坏文件已删除）")
            return True
        except Exception as e:  # noqa: BLE001
            self._last_error = f"重建索引库失败：{e}"
            return False

    # ── 结构 ──

    def is_schema_current(self) -> bool:
        """现有索引表是否已是当前结构（`id/title/body/toks`）。

        给**只读**调用方用（`limo doctor --local`）：结构对不上时它应当直接
        放弃检索，而不是为了对齐结构去动用户的库 —— `--local` 的全部理由
        就是「后端没起来时看看本机有什么」，它不该顺手改库。
        """
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='transcripts_fts'"
            ).fetchone()
        if not row:
            return True  # 没有表 → `_init_db` 已按新结构建好
        ddl = (row[0] or "").lower()
        return "toks" in ddl and "unindexed" in ddl

    def migrate_schema(self) -> bool:
        """结构过旧则整表丢弃重建，返回是否做了迁移。

        ⚠️ **只由应用启动路径调用**（`main.py`，紧接着就 `rebuild()`）。
        丢弃即数据丢失，所以刻意不放进 `__init__`：`limo_cli --local` 也会构造
        本类，而它必须保持只读（见 `is_schema_current`）。
        """
        if self.is_schema_current():
            return False
        with self._lock, self._conn() as conn:
            conn.execute("DROP TABLE transcripts_fts")
            conn.execute(_CREATE_SQL)
            conn.commit()
        log.warning("全文索引结构已升级，旧索引已丢弃，将由启动流程重建")
        return True

    # ── 索引 ──

    def index(self, tx_id: str, title: str, content: str):
        """新增或更新一条索引。正文清洗进 `body`，分词结果进 `toks`。"""
        if not self.available:
            # 索引不可用时**不写**：往坏库里灌数据既救不回检索，又让状态更难查。
            # 重建会从 store 读全部文档补全，所以跳过是安全的。
            return
        body = _clean_for_search(content)
        toks = " ".join(_tokenize(f"{title or ''}\n{body}"))
        with self._lock, self._conn() as conn:
            # 先删后插 — FTS5 不支持 UPDATE，用 INSERT OR REPLACE
            conn.execute("DELETE FROM transcripts_fts WHERE id = ?", (tx_id,))
            conn.execute(
                "INSERT INTO transcripts_fts(id, title, body, toks) VALUES (?, ?, ?, ?)",
                (tx_id, title, body, toks),
            )
            conn.commit()

    def delete(self, tx_id: str):
        """删除一条索引（删除文档时调用）"""
        if not self.available:
            return
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM transcripts_fts WHERE id = ?", (tx_id,))
            conn.commit()

    def rebuild(self, transcripts: list[dict], get_content, *, vacuum: bool = False):
        """从零重建全部索引（初始化/恢复时调用）。
        transcripts: store.list_transcripts() 返回的元数据列表
        get_content: (tx_id) -> str，返回原始文本
        vacuum: 重建后回收空间（`VACUUM`）。

        ⚠️ **`vacuum=True` 只在用户主动点「重建索引」时传**：`search.db` 的
        `auto_vacuum=0`，删掉的正文页会留在文件里（实测一个 137 行 / 14.7MB 的库
        有 23% 是空闲页）。启动时每次都 VACUUM 会让「打开应用」等一次全库重写，
        而用户主动重建时这笔开销已经被告知过。
        """
        if not self.available and not self._recover_db():
            # 恢复失败 → 让调用方拿到错误。**不能静默继续**：往一个坏库里灌数据
            # 会掩盖「索引不可用」这个事实，用户仍然搜不到任何东西。
            raise RuntimeError(self._last_error or "索引库不可用")
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM transcripts_fts")
            for tx in transcripts:
                content = get_content(tx["id"])
                title = tx.get("title", "")
                # ⚠️ 判据是「标题与正文**都**空」才跳过，不是「content 为空」——
                # 按后者写，「只有标题、正文为空」的文档整条不进索引（连标题都搜不到）
                if not content and not title:
                    continue
                body = _clean_for_search(content)
                toks = " ".join(_tokenize(f"{title}\n{body}"))
                conn.execute(
                    "INSERT INTO transcripts_fts(id, title, body, toks) VALUES (?, ?, ?, ?)",
                    (tx["id"], title, body, toks),
                )
            conn.commit()
            if vacuum:
                # ⚠️ 必须在上面的 commit 之后 —— 事务里执行 VACUUM 会报
                # `cannot VACUUM from within a transaction`。
                conn.execute("VACUUM")
        self._last_error = None
        self._rebuilt_at = datetime.now().isoformat(timespec="seconds")
        log.info("全文索引重建完成", docs=len(transcripts), vacuum=vacuum)

    # ── 搜索 ──

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """全文搜索，返回 [{id, title, snippet}, ...]，按相关性排序"""
        if not self.available:
            # 防御性短路（`QueryService` 会先抛显式错误，正常走不到这里）：
            # 直接调本类的调用方拿到空结果，而不是一个 sqlite 异常。
            log.warning("全文索引不可用，搜索返回空", q=query[:50])
            return []
        if not query.strip():
            return []
        # 查询侧封顶 64：用户可能直接把一整段粘贴进来当查询，OR 串会膨胀到
        # FTS5 处理不了的长度。索引侧**必须**不封顶（见 `_tokenize` 的 ⚠️）。
        match = _escape_fts5(_tokenize(query, limit=64))
        if not match:
            # 查询里没有任何可检索 token（纯符号 / 纯空白）—— 显式返回空，
            # 不能让空 MATCH 串流进 SQL（FTS5 会报 syntax error）。
            return []
        with self._lock, self._conn() as conn:
            try:
                rows = conn.execute(
                    """
                    SELECT id, title, body
                    FROM transcripts_fts
                    WHERE transcripts_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (match, limit),
                ).fetchall()
            except sqlite3.OperationalError as e:
                # 查询语法错误时回退到 LIKE。⚠️ 必须留痕：这里吞异常会把
                # 「SQL 写错」伪装成「没搜到」—— 实测踩过（注释误写进 SQL 字符串
                # 导致全表 CJK 查询恒返回空，无任何日志，排查方向被彻底带偏）。
                # 注：`body` 列存的是**原文**（分词只在 toks 做），故 LIKE 回退
                # 对中文查询是有效的 —— 改造前 body 是逐字插空格的形态，回退几乎无效。
                log.warning("FTS5 查询失败，回退 LIKE", q=query[:50], err=str(e)[:120])
                like = f"%{query.replace('%', '\\%').replace('_', '\\_')}%"
                rows = conn.execute(
                    """
                    SELECT id, title, body
                    FROM transcripts_fts
                    WHERE title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\'
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (like, like, limit),
                ).fetchall()
        return [
            {"id": r[0], "title": r[1], "snippet": _make_snippet(r[2] or "", query)}
            for r in rows
        ]


# ── 写入口 ──

def update_index(engine: "SearchEngine | None", store, tx_id: str,
                 vec=None) -> None:
    """把一篇文档的**当前**状态写进全文索引（并给向量侧打 `pending`）。失败只留痕，不抛。

    ⚠️ **五个写点共用这一个入口**（摄入 / 重新转录 / 生成完成 / 编辑保存 /
    启动重建）：内容是什么只有一处判据（`doc_text.search_index_text`）。
    各自拼一份的失效形态是「生成后搜得到、编辑后又搜不到」，而两次写都报成功
    —— 索引里悄悄留着上一版笔记，用户只会觉得搜索「时灵时不灵」。

    ⚠️ **从 store 取，不接收调用方手上的那份文本**：调用方那份常常是刚算出来
    还没落盘的（重新转录、合成），而索引该反映的是库里的现状。

    ⚠️ `doc_text` 在函数内导入：`app/core/` 概念上不依赖 `app/generation/`，而
    「索引文本 = rawText + 最新笔记」这条判据必须和「剥自证头 / 剥语义标记」
    住在一起（那两样都是 core 内协议，各自的剥法只该有一份）。

    `vec` = `VectorStore` 或 `None`（**默认 None = 改造前的行为**）。给了就只给该篇
    打一个 `pending` 标记 —— **一次 embedding 调用都不发**（那次往返会卡住「保存」
    按钮，用户会以为卡死了），真正的编码归后台 worker。字面索引与向量标记**各自
    独立 try**：向量侧坏掉不该让字面索引白写，反过来也一样。
    """
    if store is not None and engine is not None:
        try:
            from app.generation.doc_text import search_index_text
            tx = store.get_transcript(tx_id) or {}
            engine.index(tx_id, tx.get("title", ""), search_index_text(store, tx_id))
        except Exception as e:
            log.warning("全文索引写入失败，该文档可能搜不到",
                        tx_id=tx_id, err=str(e)[:120])
    if vec is not None:
        try:
            vec.mark_pending(tx_id)
        except Exception as e:
            log.warning("向量索引标记失败，该文档可能不出现在语义检索里",
                        tx_id=tx_id, err=str(e)[:120])


# ── 工具函数 ──

def _is_cjk(ch: str) -> bool:
    """CJK 统一表意文字（含扩展 A）。"""
    o = ord(ch)
    return 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF


#: 英文词 / 数字串。数字单列一支，是因为 `1048` 这种纯数字查询必须能匹配到自己
#: （unicode61 把 `1,048` 切成 `1` + `048`，故 `_clean_for_search` 会先去掉千分位）。
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.]*|\d+(?:\.\d+)?")


def _clean_for_search(text: str) -> str:
    """清洗正文：去 Markdown 语法、归一化空白。**不分词、不截断。**

    ⚠️ 分词在 `_tokenize` 里做，且只作用于 `toks` 列 —— `body` 是给人看的
    （摘要从它取），中文必须保持连续。

    ⚠️ **这里曾有一行 `return text[:50000]`**（2026-10-26 删除）。它让超过
    5 万字符的文档后半段**从未进过索引**：实测 131 篇里 24 篇（18%）中招，
    那 24 篇的笔记 100% 被切掉 —— 一篇 37 万字符的合成文档只有前 13% 可搜。
    索引体积要到万篇量级才需要重新评估这个决定。
    """
    if not text:
        return ""
    # 去代码块
    text = re.sub(r'```[\s\S]*?```', ' ', text)
    # 去图片
    text = re.sub(r'!\[.*?\]\(.*?\)', ' ', text)
    # 去链接（保留文字）
    text = re.sub(r'\[([^\]]*)\]\(.*?\)', r'\1', text)
    # 去标题标记
    text = re.sub(r'^#{1,6}\s+', ' ', text, flags=re.MULTILINE)
    # 去 HTML 标签
    text = re.sub(r'<[^>]+>', ' ', text)
    # 去多余符号，保留中英文和数字
    text = re.sub(r'[*_~`>|#\-]+', ' ', text)
    # 数字千分位：`1,048` 会被 unicode61 切成 `1` / `048`，用户搜 `1048` 命不中
    text = re.sub(r'(?<=\d),(?=\d)', '', text)
    # 归一化空白
    return re.sub(r'\s+', ' ', text).strip()


def _tokenize(text: str, limit: int | None = None) -> list[str]:
    """把文本切成检索 token 表：CJK 相邻二字组 + 英文词 + 数字串（去重，可选限量）。

    ⚠️ **`limit` 默认 None = 不限，这很重要**：本函数同时服务**索引侧**（`index` /
    `rebuild`，必须收全文）与**查询侧**（`search`，只在查询特别长时才需要封顶）。
    曾经这里无条件 `return uniq[:64]`，而两个调用点都没传参 —— 于是**每篇文档只
    索引了开头 64 个 token**（一篇 85k 字符的文档 `toks` 串只有 190 字符），中间与
    结尾的专有名词全部搜不到。症状极具误导性：单测全绿（用例都是短文本，64 够用）、
    标题命中正常、语义题还能靠开头蒙对几道，而精确题命中率只有 1/9。
    这是**同一个函数服务两个语义不同的调用点**的经典失效形态。

    ⚠️ **二字组（bigram）而不是单字**（2026-10-26 实测，131 篇真实库 / 29 题）：
    单字没有区分度（「的」「是」「不」在任何文档里都出现），召回其实很高，
    但 BM25 排序被噪音挤崩 —— 目标文档常常落在第 13、33 名。二字组自带区分度，
    配合 OR 交给 BM25 排序：**55% → 83%**。

    ⚠️ **OR 不是 AND**：二字组 + AND 等于一票否决，语义型查询直接归零
    （实测 15 道语义题从 7/15 掉到 **0/15**，总分 55% → 38%）。
    「放宽连接方式」的收益在**保留召回**，精度由二字组提供，两者缺一不可。

    ⚠️ 单个 CJK 字符（游离的「的」、整个查询只有一个汉字）**保留为单 token**，
    否则这类查询会得到空 token 表 → `search()` 直接返回空，且看起来像「库里没有」。
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if not _is_cjk(text[i]):
            i += 1
            continue
        j = i
        while j < n and _is_cjk(text[j]):
            j += 1
        run = text[i:j]
        if len(run) == 1:
            out.append(run)
        else:
            out.extend(run[k:k + 2] for k in range(len(run) - 1))
        i = j
    out.extend(_TOKEN_RE.findall(text))

    # 去重保序：同一 token 出现两次对 MATCH 无意义，而长查询（用户直接粘贴
    # 一整段当查询）会生成巨大的 OR 串。
    seen: set[str] = set()
    uniq = [t for t in out if not (t in seen or seen.add(t))]
    return uniq if limit is None else uniq[:limit]


def _escape_fts5(tokens: list[str]) -> str:
    """把 token 表拼成 FTS5 查询串：**OR** 连接，每个都加引号。

    ⚠️ 引号是必须的：token 里可能含 `-` `.` 等 FTS5 语法字符，不加引号会被
    解析成列过滤或短语而报错 / 误匹配。
    """
    return " OR ".join(f'"{t}"' for t in tokens if '"' not in t)


def _make_snippet(body: str, query: str, width: int = 90) -> str:
    """从正文截一段含关键词的摘要，命中的词用 `<mark>` 包住。

    ⚠️ **自己生成，不用 FTS5 的 `snippet()`**：那个函数只能从被索引的列取文本，
    而被索引列 `toks` 是二字组串 —— 摘要会变成 `机器 器学 学习`。
    `body` 列刻意 `UNINDEXED` 就是为了让这里取到人类可读的原文。

    ⚠️ 找不到完整词时退化为「取正文开头」而不是返回空 —— 二字组 OR 允许
    **部分 token 命中**，此时摘要仍应给出上下文，否则结果列表里那一行是空白。
    """
    body = body or ""
    if not body:
        return ""
    terms = [t for t in re.split(r"\s+", (query or "").strip()) if t]
    best_pos, low = -1, body.lower()
    for t in sorted(terms, key=len, reverse=True):
        p = low.find(t.lower())
        if p >= 0 and (best_pos < 0 or p < best_pos):
            best_pos = p
    if best_pos < 0:
        head = body[: width * 2]
        return head + ("…" if len(body) > width * 2 else "")

    start = max(0, best_pos - width // 2)
    end = min(len(body), best_pos + width + width // 2)
    frag = body[start:end]
    # 长词先替换：先短后长会把「机器学」拆成「<mark>机器</mark>学」，丢掉更精确的高亮
    for t in sorted(terms, key=len, reverse=True):
        frag = re.sub(re.escape(t), lambda m: f"<mark>{m.group(0)}</mark>",
                      frag, flags=re.IGNORECASE)
    return ("…" if start > 0 else "") + frag + ("…" if end < len(body) else "")
