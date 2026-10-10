"""查询层 —— HTTP 路由与 CLI 共用的**只读应用层**。

三层架构（《CLI 设计约定》）的中间层：

    CLI adapter ─┐
                 ├─→ QueryService（本模块） ─→ Store / SearchEngine（原语）
    HTTP 路由   ─┘

⚠️ **为什么 CLI 不直读 `.limo`**：文件布局（`index.json` 的形状、版本归档的
`{mode}/v{N}.md` 命名、`<!-- limo-meta -->` 头信封）是**实现细节**。
在 CLI 里照着复刻一遍 = 第 6 个副本（现有 5 处：`storage/store.py`、`routers/export.py`、
`routers/ingest.py`、`routers/generate.py`、`routers/files.py`）。任何格式演进都要
同时改 6 处，而**漏改的那处不会报错**——它只是静默读错，这正是把接口做成隔离边界
而非仅仅加速器的理由。

⚠️ **本模块零 FastAPI import**：CLI 进程要 `import` 它，而那里没有 `app.state`。
也正因如此，`content` 里的 `assets/xxx` **保持相对路径**——改写成绝对 URL 是
HTTP 层的职责（给 CLI 返回 `http://127.0.0.1:7413/...` 毫无意义）。

⚠️ **只读**：本模块不写任何文件。写路径（导入 / 生成 / 编辑 / 删除）仍走各自的
router——把它们搬进来会把「一次写入的多步编排」与「只读契约」搅在一起，而两者的
失败语义完全不同（读失败可重试，写失败可能半途）。CLI 第一阶段也刻意只读。

⚠️ **service 只调原语，不重算**（切分线）：

| service 负责 | 原语负责 |
|---|---|
| 契约成形：分页 / 游标 / ID 解析 / 版本编排 / meta 装配 / 截断标记 | 数据的真相：文件布局、版本判定、FTS 查询、index 读写的锁 |

最典型的是**版本选择**：`resolve_latest` 只调 `store.get_markdowns_by_transcript`
取 `[0]`（它已按版本降序），**绝不本地重算 max version**——本地重算等于多一份
「哪个文件是最新版」的判据，而两份判据在「写入中断」这类边界上必然分叉。
"""

import base64
import json
from dataclasses import dataclass, field

from app.core.errors import AppError
from app.core.resource_id import is_valid_resource_id
from app.core.transcript_meta import parse_artifact_meta, strip_meta

# 查询层契约版本。CLI 的 `--json` 输出带它，消费方据此判断字段集是否还认得。
# 从 "1" 起；**破坏性变更**才 +1（新增可选字段不算）。
SCHEMA_VERSION = "1"

DEFAULT_PAGE_SIZE = 50
# 单页上限。防止 `?limit=100000` 变成一次全库拉取——分页存在的意义就是让调用方
# 不得不翻页，而不是换个参数把分页绕过去。
MAX_PAGE_SIZE = 200

# `get_artifact` 单次返回的正文上限（字符）。CLI 默认用它，防大产物一次灌爆终端；
# 内部调用（导出）显式传 `max_chars=None` 要全文（见 `get_artifact`）。
DEFAULT_MAX_CHARS = 256 * 1024


class QueryError(AppError):
    """查询层错误。

    `code` 是**稳定契约**的一部分：CLI 的退出码映射依赖它（「空结果」与「坏 ID」
    必须能被区分开，否则脚本无法判断该重试还是该报错）。故新增 code 时不要复用
    已有名字表达不同语义。
    """

    @classmethod
    def invalid_id(cls, raw: str) -> "QueryError":
        return cls(
            "QUERY_INVALID_ID", "无效的资源 ID",
            detail=f"ID 只能是字母/数字/下划线/连字符且不超过 64 字符，实得: {raw!r}",
            status_code=400, recoverable=True,
        )

    @classmethod
    def invalid_cursor(cls, raw: str) -> "QueryError":
        return cls(
            "QUERY_INVALID_CURSOR", "分页游标无效",
            detail=f"游标应由上一次响应的 `next_cursor` 原样回传，实得: {raw!r}",
            status_code=400, recoverable=True,
        )

    @classmethod
    def not_found(cls, doc_id: str) -> "QueryError":
        return cls("QUERY_NOT_FOUND", "资源不存在",
                   detail=f"docId={doc_id}", status_code=404, recoverable=True)

    @classmethod
    def artifact_not_found(cls, doc_id: str, part: str) -> "QueryError":
        return cls("QUERY_ARTIFACT_NOT_FOUND", f"该文档没有 {part} 产物",
                   detail=f"docId={doc_id} part={part}",
                   status_code=404, recoverable=True)

    @classmethod
    def no_search_engine(cls) -> "QueryError":
        # ⚠️ **这条存在的意义就是让「静默空结果」变成「响亮的错误」**。
        # 在 `Store` 旁边提前构造 QueryService 的写法会拿到 `search_engine=None`，
        # 若此处返回空列表，用户看到的是「搜不到东西」——一个看起来完全正常的结果。
        # recoverable=False：这不是业务状态，是初始化顺序写错了。
        return cls("QUERY_NO_SEARCH_ENGINE", "查询层缺少搜索引擎实例",
                   detail="SearchEngine 尚未初始化（检查 QueryService 的构造时机）",
                   status_code=500, recoverable=False)


@dataclass
class Page:
    """一页资源。`has_more` / `next_cursor` 与 `truncated` / `continuation` 是**两套**
    「没给全」的表达，不要混用：

    - `has_more` + `next_cursor`：**可以继续翻**（列表分页），游标是稳定键
    - `truncated` + `continuation`：**到头了但没给完**（结果条数上限、正文长度上限），
      只能靠调大参数或细化条件

    把后者伪装成前者（给一个续不动的游标）等于让调用方的翻页循环静默少拿数据。
    """

    items: list[dict] = field(default_factory=list)
    has_more: bool = False
    next_cursor: str | None = None
    truncated: bool = False
    continuation: dict | None = None

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "items": self.items,
            "has_more": self.has_more,
            "next_cursor": self.next_cursor,
            "truncated": self.truncated,
            "continuation": self.continuation,
        }


@dataclass
class Artifact:
    """一份产物的读取结果。

    ⚠️ **两个正文域，别接错**：

    | 字段 | 内容 | 谁用 |
    |---|---|---|
    | `content` | **纯正文**（自证头已剥） | agent / CLI / 预览 —— 它们要的是内容 |
    | `raw_content` | 原文（**含** `<!-- limo-meta -->` 首行） | 导出 —— 产物离开 `.limo` 后靠它自证 |

    二者恒满足 `strip_meta(raw_content) == content`（有测试钉住）。只给一个的话，
    要么导出物丢掉自证、要么每个消费方自己剥一遍头（= N 份剥头实现）。

    `version` 对 process 产物恒为 `0`——**同 mode 覆盖、不版本化**是既定设计
    （OCR 是确定性的），不是"版本还没算出来"。

    `entry` 是产生这份产物的**原始 index 条目**（元数据如 `style` / `createdAt` /
    `edited`）。它不进 `to_dict()` 的默认输出——那些是 HTTP 表示层要的字段，不是
    产物内容的一部分。留着它是因为：丢掉它，把 Artifact 还原成既有 HTTP 响应形状
    就得**再查一次 index**，而两次查询之间 index 可能已变（调用方会拿到自相矛盾的
    元数据 + 内容）。
    """

    id: str
    doc_id: str
    kind: str
    version: int
    content: str
    raw_content: str
    meta: dict | None
    meta_corrupt: bool
    entry: dict = field(default_factory=dict)
    total_chars: int = 0
    offset: int = 0
    truncated: bool = False
    continuation: dict | None = None

    def to_dict(self, *, include_raw: bool = False) -> dict:
        """契约形状。默认**不含** `raw_content`——它几乎是 `content` 的两倍体积，
        而 JSON 消费者要的是内容不是载体。需要它的（导出）直接读属性。"""
        d = {
            "schema_version": SCHEMA_VERSION,
            "id": self.id,
            "docId": self.doc_id,
            "kind": self.kind,
            "version": self.version,
            "content": self.content,
            "meta": self.meta,
            "metaCorrupt": self.meta_corrupt,
            "truncated": self.truncated,
            "totalChars": self.total_chars,
            "offset": self.offset,
            "continuation": self.continuation,
        }
        if include_raw:
            d["rawContent"] = self.raw_content
        return d


def _encode_cursor(entry: dict) -> str:
    """把一条资源的排序键 `(createdAt, id)` 编成**不透明**游标。

    不透明（base64）是刻意的：游标是服务端契约，调用方只管原样回传。若暴露成
    `2026-10-01T10:00:00|tr_abc` 这种明文，迟早有人自己拼一个——然后拼错（漏了
    并列时的 id 兜底，翻页就开始漏数据）。
    """
    raw = json.dumps([entry.get("createdAt", ""), entry.get("id", "")], ensure_ascii=False)
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[str, str] | None:
    """游标 → `(createdAt, id)`；任何瑕疵都返回 `None`（由调用方抛 INVALID_CURSOR）。"""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        key = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    except Exception:
        return None
    if not isinstance(key, list) or len(key) != 2 or not all(isinstance(x, str) for x in key):
        return None
    return (key[0], key[1])


def _sort_key(entry: dict) -> tuple[str, str]:
    """列表的**唯一**排序判据。游标判据必须与它同源，否则分页边界会漏项/重项。"""
    return (entry.get("createdAt", ""), entry.get("id", ""))


class QueryService:
    """只读查询契约。构造零成本（只存两个引用），每次请求新建即可。"""

    def __init__(self, store, search_engine=None) -> None:
        self._store = store
        self._search = search_engine

    # ── ID ──

    def resolve_doc_id(self, raw: str) -> str:
        """校验并原样返回资源 ID；不合法 → `QUERY_INVALID_ID`。

        ⚠️ 只校验不归一化：`..` 被**拒绝**而不是被清理成 `_` 之后继续查找。
        """
        if not is_valid_resource_id(raw):
            raise QueryError.invalid_id(raw)
        return raw

    # ── 资源 ──

    def list_resources(self, *, source_type: str | None = None, tag: str | None = None,
                       folder_id: str | None = None, query: str | None = None,
                       limit: int | None = DEFAULT_PAGE_SIZE,
                       cursor: str | None = None) -> Page:
        """列出文档（分页）。

        ⚠️ `folder_id=""` 是**有效筛选值**（只看未分类），与「不传 = 不过滤」是两种
        语义——故用 `is not None` 判定，不能靠真值。

        `limit=None` = **一次给全**（不分页）。HTTP 侧的 `GET /api/transcripts` 用它：
        那个端点今天的契约就是「返回全部」，而前端已自行做了分批渲染（60 条一个
        `IntersectionObserver` 批次）——在这里悄悄加一个 200 条上限，超出的文档会
        **静默消失**，看起来就像「用户只导入了 200 篇」。分页是新能力（给 CLI），
        不能顺手改掉既有端点的语义。
        """
        filters: dict = {}
        if source_type:
            filters["sourceType"] = source_type
        if tag:
            filters["tag"] = tag
        if query:
            filters["query"] = query
        if folder_id is not None:
            filters["folderId"] = folder_id

        entries = self._store.list_transcripts(filters or None)
        # ⚠️ **按游标判据重新排序**：`store.list_transcripts` 用的是
        # `sorted(key=createdAt, reverse=True)`，同一秒创建的文档保留 index 顺序
        # （稳定排序），而这个顺序**不确定**——index 里谁先谁后取决于写入历史。
        # 若游标按 `(createdAt, id)` 比较而列表按 `createdAt` 单键排，同秒并列的那
        # 几篇就会在翻页边界上被跳过或重复（且只在同秒多篇时才复现）。
        # 排序判据与游标判据**必须同源**。
        entries = sorted(entries, key=_sort_key, reverse=True)

        start = 0
        if cursor:
            key = _decode_cursor(cursor)
            if key is None:
                raise QueryError.invalid_cursor(cursor)
            start = next((i for i, e in enumerate(entries) if _sort_key(e) < key), len(entries))

        if limit is None:
            window = entries[start:]
            has_more = False
        else:
            size = self._clamp_limit(limit)
            window = entries[start:start + size]
            has_more = start + size < len(entries)

        # 「这篇有哪些产物」在索引里是一次内存查询（不读盘），随列表一起带出去。
        # 前端靠它决定要不要发产物请求：没产物就不发 —— 而不是盲发一个必然 404 的
        # 请求、再把它静默掉（那是把正常状态建模成 HTTP 错误的根源，见 `list_resources`
        # 上方 `modes` 的消费方 `PreviewArea.loadDocContent`）。
        # ⚠️ 放在**契约层**而非 HTTP 端点：CLI 与前端同源，两侧不会各读各的（#61）。
        modes_map = self._store.get_artifact_modes([e["id"] for e in window])
        items = []
        for e in window:
            item = self.project_resource_entry(e)
            item["modes"] = modes_map.get(e["id"], [])
            items.append(item)

        return Page(
            items=items,
            has_more=has_more,
            next_cursor=_encode_cursor(window[-1]) if (has_more and window) else None,
        )

    def search_resources(self, q: str, *, limit: int = DEFAULT_PAGE_SIZE,
                         cursor: str | None = None) -> Page:
        """全文搜索（FTS5）。

        ⚠️ **不做游标翻页**：FTS5 的结果序是 `ORDER BY rank`（相关性），没有一个
        稳定键可以做 keyset 游标——硬造一个（如用行号）在两次查询之间索引一变就错位。
        故这里用 `truncated` + `continuation` 表达「到头了」：调用方要么细化关键词，
        要么调大 `limit`。**绝不静默截断数组然后返回看起来完整的页**。

        `cursor` 参数保留在签名里以便将来接入真正的分页，当前传入会被忽略。
        """
        # ⚠️ **空查询先返回**，排在「缺引擎」判定之前：空查询根本不碰 FTS，把它的
        # 失败说成「缺少搜索引擎」等于**把参数问题伪装成配置问题**——用户敲了
        # `limo search ""`，得到的却是一句让他去查 SearchEngine 初始化的报错。
        # 放在后面判则任何非空查询仍会暴露真配置错误（这条不会掩盖它）。
        if not (q or "").strip():
            return Page(items=[])
        if self._search is None:
            raise QueryError.no_search_engine()

        size = self._clamp_limit(limit)
        # 多取一条只为**探测**还有没有更多（不返回给调用方）
        hits = self._search.search(q, size + 1)
        truncated = len(hits) > size
        items = hits[:size]
        return Page(
            items=items,
            truncated=truncated,
            continuation={
                "reason": "result_limit",
                "limit": size,
                "hint": f"结果超过 {size} 条，请细化关键词或调大 limit",
            } if truncated else None,
        )

    def list_folders(self) -> list[dict]:
        """全部文件夹（含各自文档数）。

        ⚠️ 返回**裸 list**（不是 `Page`）：文件夹是少数几个、且界面上要一次性铺满的
        维度，分页对它没有意义。契约上它与 `list_resources(...).items` 同级。
        """
        return self._store.list_folders()

    def stats(self) -> dict:
        """库概览 —— 计数与分布（CLI `status` 的数据源）。

        ⚠️ 这不是「service 只调原语、不重算」的例外：统计是**契约成形**（同一个
        数字在不同消费方口径必须一致），不是数据真相。写在 router 里会让 CLI 的
        `--local` 模式复刻一份，而两份统计口径的漂移是**静默的**——同一个库，
        HTTP 报 12 篇、本地报 11 篇，两边都"看起来对"。
        """
        entries = self._store.list_transcripts()
        by_type: dict[str, int] = {}
        tags: set[str] = set()
        uncategorized = 0
        for e in entries:
            st = e.get("sourceType") or "(未标注)"
            by_type[st] = by_type.get(st, 0) + 1
            tags.update(e.get("tags") or [])
            if not e.get("folderId"):
                uncategorized += 1
        created = sorted(e.get("createdAt") or "" for e in entries)
        return {
            "docCount": len(entries),
            # 按篇数降序 —— 概览是给人一眼看的，字典序在这里没有意义
            "bySourceType": dict(sorted(by_type.items(), key=lambda kv: -kv[1])),
            "folderCount": len(self._store.list_folders()),
            "uncategorized": uncategorized,
            "tagCount": len(tags),
            "oldest": created[0] if created else "",
            "newest": created[-1] if created else "",
        }

    def get_resource(self, doc_id: str) -> dict:
        """单个文档的完整元数据（含 rawText）。不存在 → `QUERY_NOT_FOUND`。

        ⚠️ 返回的 `rawText` **不剥自证头**：合成文档的头是「三位一体」的一部分
        （`rawText == 模型输入 == 用户所见`），剥掉它等于改内容。`assets/` 相对路径
        也保持原样——改写成绝对 URL 是 HTTP 层的活。
        """
        doc_id = self.resolve_doc_id(doc_id)
        tx = self._store.get_transcript(doc_id)
        if not tx:
            raise QueryError.not_found(doc_id)
        return self.project_resource(tx)

    # ── 产物 ──

    def list_artifacts(self, doc_id: str) -> list[dict]:
        """该文档**有哪些**产物（元数据，零内容、零文件读取）。

        存在的理由是「别让消费方盲试」：产物种类有六个（`notes` / `polished` 两个
        生成产物 + `extract` / `table` / `digitize` / `understand` 四个图片处理产物），
        而一篇文档通常只有其中一两个。没有这个清单，CLI/agent 要发现「这篇有没有
        笔记」就得挨个 `get --part`，靠 `exit 3` 反推 —— 六次往返去问一个元数据问题。

        生成产物（`storage="markdown"`）有版本概念（版本归档，全部保留），处理产物
        （`storage="process"`）恒为 `version=0`——**同 mode 覆盖不版本化**是既定设计
        （OCR 是确定性的），不是"版本还没算出来"。两类共有的键一律出现（各自的
        专有键缺省给空），免得消费方按 storage 分支取字段。
        """
        doc_id = self.resolve_doc_id(doc_id)
        if not self._store.get_transcript(doc_id):
            raise QueryError.not_found(doc_id)

        items: list[dict] = []
        for e in self._store.list_markdown_entries(doc_id):
            items.append({
                "kind": e.get("mode", ""), "version": e.get("version", 0),
                "createdAt": e.get("createdAt", ""), "style": e.get("style", ""),
                "engine": "", "edited": bool(e.get("edited")),
                "storage": "markdown",
            })
        for r in self._store.get_process_results(doc_id):
            items.append({
                "kind": r.get("mode", ""), "version": 0,
                "createdAt": r.get("createdAt", ""), "style": "",
                "engine": r.get("engine", ""), "edited": False,
                "storage": "process",
            })
        # 版本降序 + 生成产物在前：与 `get --part` 的默认（取最新版）视线一致
        items.sort(key=lambda x: (x["storage"] != "markdown", x["kind"], -x["version"]))
        return items

    def resolve_latest(self, doc_id: str, mode: str) -> Artifact | None:
        """取某文档某 mode 的**最新版**产物；没有则 `None`。

        ⚠️ 版本判定**只调 store**（`get_markdowns_by_transcript` 已按版本降序，
        故 `[0]` 就是最新）。本地重算「哪个文件版本更高」= 第二份判据，而两份判据
        在「写过 `.md` 但 index 更新中断」这类边界上必然分叉。
        """
        doc_id = self.resolve_doc_id(doc_id)
        mds = self._store.get_markdowns_by_transcript(doc_id, mode=mode)
        if not mds:
            return None
        return self.build_from_entries(doc_id, mds)

    def build_from_entries(self, doc_id: str, entries: list[dict], *,
                           offset: int = 0,
                           max_chars: int | None = DEFAULT_MAX_CHARS) -> Artifact:
        """从**已经取到手的** markdown index 条目成形一份 Artifact（取版本最高的那条）。

        单独开这个入口的理由不是省一次 I/O，而是**避免自相矛盾**：HTTP 的
        `GET /api/markdown/{id}` 同时要「版本列表」和「选中版本的内容」，而
        `get_markdowns_by_transcript` 一次就把两者都读出来了。若改成分头调用
        （列表走 store、内容走 `get_artifact`），两次读盘之间文件可能被生成任务
        替换 —— 用户会看到「版本列表里的第 2 版」配着「第 1 版的正文」。

        `entries` 必须来自 `store.get_markdowns_by_transcript`（已按版本降序，
        `[0]` 是最新版）——本方法**不做版本比较**，只取第一条。
        """
        if not entries:
            # 调用方保证非空（`resolve_latest` / `get_artifact` 都先判过）。真到这里
            # 是编程错误而非业务状态，故不去伪装成「没有这个产物」的 404。
            raise AppError("QUERY_EMPTY_ENTRIES", "内部错误：成形产物至少需要一个条目",
                           recoverable=False, status_code=500)
        return self._build(
            artifact_id=entries[0].get("id", ""), doc_id=doc_id,
            kind=entries[0].get("mode", ""), version=entries[0].get("version", 0),
            raw=entries[0].get("content", "") or "", entry=entries[0],
            offset=offset, max_chars=max_chars,
        )

    @staticmethod
    def artifact_versions(entries: list[dict]) -> list[dict]:
        """index 条目列表 → **版本列表**（前端 `versions[]`）的唯一形状落点。

        此前这个列表推导在本仓有**两份几乎相同的副本**（`routers/generate.py` 与
        `routers/markdown_edit.py`），且字段集不同——新增一个展示字段（如本轮的
        `chars`）只会被改到其中一处，另一处静默少一个字段。同源漂移，模式 #54。

        ⚠️ **`chars` / `edited` / `source` 是读时派生值**（`get_markdowns_by_transcript`
        顺带解析文件头算出），**不进 index**：进 index 就是第二份真相源，而它在
        「删除 index 重建」之后必与文件头分叉。
        ⚠️ **缺失的 `chars` 干脆不给这个键**：`0` 的语义是「真的 0 字」而不是
        「不知道」，填 `0` 是伪造数据（前端的 `undefined` 与之严格区分）。
        """
        return [
            {
                "version": e.get("version", 1),
                "id": e.get("id", ""),
                "createdAt": e.get("createdAt", ""),
                "status": e.get("status", "complete"),
                "mode": e.get("mode", ""),
                # ⚠️ 缺失时**不给这个键**（而非填 `0`）：前端的 `undefined` 语义是
                # 「后端没给」，与「真的 0 字」是两回事（`VersionSwitcher` 据此决定
                # 那一格显不显示）。填 `0` 是**伪造数据**，且与上面 docstring 自相矛盾
                # ——「不知道」被写成了「没有」。正常路径下 `chars` 恒存在（读时派生）。
                **({"chars": e["chars"]} if "chars" in e else {}),
                "edited": bool(e.get("edited")),
                "source": e.get("source", ""),
            }
            for e in entries
        ]

    def get_artifact(self, doc_id: str, part: str, version: int | None = None, *,
                     offset: int = 0, max_chars: int | None = DEFAULT_MAX_CHARS) -> Artifact:
        """取一份产物的内容 + 自证头。不存在 → `QUERY_ARTIFACT_NOT_FOUND`。

        `part` 是产物种类：`notes` / `polished`（生成产物），或 `extract` / `table` /
        `digitize` / `understand`（图片处理产物）——两类在存储上分属不同目录，但
        对外是**同一个概念**「这份文档的这个产物」，故由本方法统一收口（调用方不必
        知道它落在 `{mode}.md` 还是 `process/{mode}.md`）。

        `version=None` = 最新版；显式传版本号时查不到该版本同样报 NOT_FOUND
        （**不静默回退到最新版**——「我要第 1 版」拿到第 2 版是最坏的一种成功）。

        `max_chars=None` = 不截断（导出侧必须用它：导出的产物要完整）。有限值时
        超限**不静默截断**：`truncated=True` 且 `continuation` 给出可执行的续读方式。
        """
        doc_id = self.resolve_doc_id(doc_id)
        if not self._store.get_transcript(doc_id):
            raise QueryError.not_found(doc_id)

        mds = self._store.get_markdowns_by_transcript(doc_id, mode=part, version=version)
        if mds:
            return self.build_from_entries(doc_id, mds, offset=offset, max_chars=max_chars)

        # 生成产物没有 → 试图片处理产物（同 mode 覆盖，无版本概念）
        if version is not None:
            raise QueryError.artifact_not_found(doc_id, part)
        for r in self._store.get_process_results(doc_id):
            if r.get("mode") == part:
                raw = self._store.get_process_result(r["id"]) or ""
                return self._build(
                    artifact_id=r["id"], doc_id=doc_id, kind=part, version=0,
                    raw=raw, entry=r, offset=offset, max_chars=max_chars,
                )
        raise QueryError.artifact_not_found(doc_id, part)

    # ── 内部：契约成形 ──

    @staticmethod
    def _clamp_limit(limit: int) -> int:
        try:
            n = int(limit)
        except (TypeError, ValueError):
            n = DEFAULT_PAGE_SIZE
        return max(1, min(n, MAX_PAGE_SIZE))

    @staticmethod
    def project_resource_entry(entry: dict) -> dict:
        """index 条目 → 列表项。

        ⚠️ **直通投影**（浅拷贝，不窄化字段）是刻意的：HTTP 侧这个形状已被前端消费
        （字段名与 `GET /api/transcripts` 一致），此刻做白名单裁剪会静默削掉前端依赖
        的字段。契约上**稳定身份只有 `id`**，其余键是实现细节、可能变化——CLI 侧
        应当只依赖 `id` + 本文档列出的语义字段。
        """
        return dict(entry)

    @staticmethod
    def project_resource(tx: dict) -> dict:
        """transcript dict → 资源契约（白名单）。

        这是 `GET /api/transcripts/{id}` 与 CLI `get` 的**唯一**投影点。此前该白名单
        写在 `routers/ingest.py` 里——CLI 一旦也要它，就是第二份副本，而副本的失效
        方式是「新增字段时漏改一处」，两边静默不一致。
        """
        return {
            "id": tx["id"],
            "title": tx.get("title", ""),
            "sourceType": tx.get("sourceType", ""),
            "sourceUrl": tx.get("sourceUrl", ""),
            "engine": tx.get("engine", ""),
            "language": tx.get("metadata", {}).get("language", ""),
            "duration": tx.get("metadata", {}).get("duration"),
            "coverUrl": tx.get("metadata", {}).get("cover_url", ""),
            "contentHash": tx.get("contentHash", ""),
            "tags": tx.get("tags", []),
            "createdAt": tx.get("createdAt", ""),
            "folderId": tx.get("folderId", ""),
            # 合成来源身份（白名单序列化——store 返回了但这里不列，前端永远拿不到）
            "sourceDocIds": tx.get("sourceDocIds", []),
            "sourceTitles": tx.get("sourceTitles", []),
            "sourceHashes": tx.get("sourceHashes", []),
            "metadataCorrupt": tx.get("metadataCorrupt", False),
            "rawText": tx.get("rawText", ""),
        }

    def _build(self, *, artifact_id: str, doc_id: str, kind: str, version: int,
               raw: str, entry: dict, offset: int, max_chars: int | None) -> Artifact:
        """剥头 / 解析自证 / 切窗口——三种产物共用的**唯一**成形点。"""
        meta, corrupt = parse_artifact_meta(raw)
        content = strip_meta(raw)

        if meta is not None and not (meta.get("identity") or {}).get("version"):
            # 写盘时调用方还不知道真实版本（`store.save_markdown` 在加锁区内算），
            # 故 `identity.version` 刻意留空。读路径用 index 里的**真版本**补全——
            # 这是设计的一部分，不是"改数据"（见 `generation/provenance.py`）。
            meta.setdefault("identity", {})["version"] = version

        total = len(content)
        start = max(0, int(offset or 0))
        window = content[start:]
        truncated = max_chars is not None and len(window) > max_chars
        continuation = None
        if truncated:
            window = window[:max_chars]
            next_offset = start + len(window)
            continuation = {
                "reason": "char_limit",
                "limit": max_chars,
                "next_offset": next_offset,
                "remaining_chars": total - next_offset,
                "hint": (f"续读：offset={next_offset}"
                         f"（或调大 max_chars 一次取全，全文 {total} 字符）"),
            }

        return Artifact(
            id=artifact_id, doc_id=doc_id, kind=kind, version=version,
            content=window, raw_content=raw, meta=meta, meta_corrupt=corrupt,
            entry=dict(entry or {}),
            total_chars=total, offset=start, truncated=truncated, continuation=continuation,
        )
