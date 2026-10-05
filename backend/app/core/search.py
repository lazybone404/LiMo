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
from pathlib import Path

from app.core import logging as log


class SearchEngine:
    """SQLite FTS5 全文索引。每次摄入/重转录时增量更新。"""

    def __init__(self, data_dir: str):
        self._db_path = Path(data_dir) / "search.db"
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        """创建 FTS5 虚拟表（如果不存在）"""
        with self._lock, self._conn() as conn:
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS transcripts_fts
                USING fts5(id, title, content, tokenize='unicode61')
            """)
            conn.commit()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ── 索引 ──

    def index(self, tx_id: str, title: str, content: str):
        """新增或更新一条索引。title/content 一并处理成可搜索文本（见 `_searchable_body`）。"""
        clean = _searchable_body(title, _clean_for_search(content))
        with self._lock, self._conn() as conn:
            # 先删后插 — FTS5 不支持 UPDATE，用 INSERT OR REPLACE
            conn.execute("DELETE FROM transcripts_fts WHERE id = ?", (tx_id,))
            conn.execute(
                "INSERT INTO transcripts_fts(id, title, content) VALUES (?, ?, ?)",
                (tx_id, title, clean),
            )
            conn.commit()

    def delete(self, tx_id: str):
        """删除一条索引（删除文档时调用）"""
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM transcripts_fts WHERE id = ?", (tx_id,))
            conn.commit()

    def rebuild(self, transcripts: list[dict], get_content):
        """从零重建全部索引（初始化/恢复时调用）。
        transcripts: store.list_transcripts() 返回的元数据列表
        get_content: (tx_id) -> str，返回原始文本
        """
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM transcripts_fts")
            for tx in transcripts:
                content = get_content(tx["id"])
                title = tx.get("title", "")
                # ⚠️ 判据是「标题与正文**都**空」才跳过，不是「content 为空」——
                # 按后者写，「只有标题、正文为空」的文档整条不进索引（连标题都搜不到）
                if not content and not title:
                    continue
                clean = _searchable_body(title, _clean_for_search(content))
                conn.execute(
                    "INSERT INTO transcripts_fts(id, title, content) VALUES (?, ?, ?)",
                    (tx["id"], title, clean),
                )
            conn.commit()

    # ── 搜索 ──

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """全文搜索，返回 [{id, title, snippet}, ...]，按相关性排序"""
        if not query.strip():
            return []
        # 预处理：CJK 字符间插入空格（FTS5 unicode61 不处理 CJK 分词）
        prepared = _prepare_query(query)
        # 转义 FTS5 特殊字符
        safe = _escape_fts5(prepared)
        # ⚠️ snippet() 第 2 个参数是列索引，按建表列序 (id, title, content) 数：
        # 0=id 1=title 2=content。正文命中时摘要必须取自 content（2）——取 1 会让
        # 「只在正文命中」的结果显示成标题，看起来像没匹配上。
        # （注意：不要往下面的三引号里塞 # 注释，SQL 不认，会把整条语句变成语法错误）
        with self._lock, self._conn() as conn:
            try:
                rows = conn.execute(
                    """
                    SELECT id, title,
                           snippet(transcripts_fts, 2, '<mark>', '</mark>', '…', 40) AS snippet
                    FROM transcripts_fts
                    WHERE transcripts_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (safe, limit),
                ).fetchall()
            except sqlite3.OperationalError as e:
                # 查询语法错误时回退到 LIKE。
                # ⚠️ 必须留痕：这里吞异常会把「SQL 写错」伪装成「没搜到」——
                # 实测踩过（注释误写进 SQL 字符串导致全表 CJK 查询恒返回空，
                # 无任何日志，排查方向被彻底带偏）。LIKE 也无法命中「CJK 逐字插空格」
                # 形式的索引文本，故回退本身对中文查询几乎无效。
                log.warning("FTS5 查询失败，回退 LIKE（中文查询回退后基本搜不到）",
                            q=query[:50], err=str(e)[:120])
                like = f"%{query.replace('%', '\\%').replace('_', '\\_')}%"
                rows = conn.execute(
                    """
                    SELECT id, title, substr(content, 1, 200) AS snippet
                    FROM transcripts_fts
                    WHERE title LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\'
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (like, like, limit),
                ).fetchall()
        return [{"id": r[0], "title": r[1], "snippet": r[2]} for r in rows]


# ── 工具函数 ──

def _clean_for_search(text: str) -> str:
    """清洗文本：去 Markdown 语法、CJK 逐字分割、归一化空白、截断"""
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
    # 归一化空白
    text = re.sub(r'\s+', ' ', text).strip()
    # CJK 字符间插入空格 — FTS5 unicode61 不处理 CJK 分词
    text = _insert_cjk_spaces(text)
    # 截断（FTS5 不需要全文，前 50000 字符足够）
    return text[:50000]


def _insert_cjk_spaces(text: str) -> str:
    """在 CJK 字符之间插入空格，非 CJK 部分保持原样。
    '机器学习Python' → '机 器 学 习 Python'
    """
    result: list[str] = []
    prev_cjk = False
    for ch in text:
        is_cjk = '一' <= ch <= '鿿' or '㐀' <= ch <= '䶿'
        if is_cjk:
            if prev_cjk:
                result.append(' ')
            result.append(ch)
            result.append(' ')
        else:
            result.append(ch)
        prev_cjk = is_cjk
    return re.sub(r'\s+', ' ', ''.join(result)).strip()


def _searchable_body(title: str, content: str) -> str:
    """拼出 FTS5 `content` 列要存的字符串 —— 标题（拆过字）+ 已清洗正文。

    ⚠️ **为什么标题要并进 content 列，而不是把拆字版写进 title 列**：
    FTS5 的 unicode61 不切分 CJK —— 一整段连续中文是一个 token。所以索引侧
    把 CJK 逐字拆开（`_insert_cjk_spaces`）、查询侧同样拆（`_prepare_query`），
    两侧对称才做得了子串匹配。而 `title` 列此前**只存原文、从未拆字**，于是
    「只出现在标题、正文里没有」的词永远搜不到：查询被拆成
    `使 AND 用 AND 说 AND 明`，title 列里却是整 token `使用说明` —— 四个字只中两个。
    （2026-10-06 实测：`engine.search('使用说明')` → `[]`，而该词就写在标题上。）

    ⚠️ **不能把拆字版写回 title 列**：`search()` 把读出的 title 直接返回给调用方，
    那样用户在搜索结果里会看到「✨ 使 用 说 明（示 例）」。故 title 列保持原文
    供显示，拆字版并入 content 列供匹配 —— 标题本就是搜索对象之一，
    而 content 列的定义正是「为搜索清洗过的文本」。

    ⚠️ 标题只做 CJK 拆字、**不**走 `_clean_for_search` 的去 Markdown 清洗：
    标题是纯文本，其中的 `#`/`-` 是内容而非语法（`_clean_for_search` 会把
    `[*_~`>|#-]+` 一律抹成空格）。正文仍走全套清洗。
    """
    t = _insert_cjk_spaces(title or "")
    return f"{t} {content}" if t else content


def _prepare_query(query: str) -> str:
    """预处理查询：CJK 字符间插入空格，让 FTS5 unicode61 能逐字匹配。
    '机器学习 Python' → '机 器 学 习 Python'
    """
    result: list[str] = []
    for ch in query:
        if '一' <= ch <= '鿿' or '㐀' <= ch <= '䶿':
            result.append(' ')
            result.append(ch)
            result.append(' ')
        else:
            result.append(ch)
    # 归并多余空格
    return re.sub(r'\s+', ' ', ''.join(result)).strip()


def _escape_fts5(query: str) -> str:
    """转义 FTS5 查询中的特殊字符，避免语法错误。
    保留双引号（用于精确短语匹配）和 * 通配符。
    """
    # 如果已经是引号包裹的短语，直接返回
    if query.startswith('"') and query.endswith('"'):
        return query
    # 为每个词加前缀通配符
    words = query.strip().split()
    escaped = [f'"{w}"' if any(c in w for c in '+-<>()~@') else w for w in words]
    return " AND ".join(escaped)
