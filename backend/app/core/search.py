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
        """新增或更新一条索引。content 会被清洗（去 Markdown 标记、连续空白归一化）。"""
        clean = _clean_for_search(content)
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
                if content:
                    clean = _clean_for_search(content)
                    conn.execute(
                        "INSERT INTO transcripts_fts(id, title, content) VALUES (?, ?, ?)",
                        (tx["id"], tx.get("title", ""), clean),
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
        with self._lock, self._conn() as conn:
            try:
                rows = conn.execute(
                    """
                    SELECT id, title,
                           snippet(transcripts_fts, 1, '<mark>', '</mark>', '…', 40) AS snippet
                    FROM transcripts_fts
                    WHERE transcripts_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (safe, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                # 查询语法错误时回退到 LIKE
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
