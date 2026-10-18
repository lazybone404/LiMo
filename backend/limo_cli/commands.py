"""只读命令的实现 —— 取数（Source）→ 渲染（Out）。**命令层不碰存储、不碰 HTTP。**

每个 `cmd_*` 返回**退出码**（`exit_codes`），不抛异常给调用方 —— 分发层因此只有
一条 `try/except SourceError` 路径，而每条命令的「什么样算成功」写在它自己的返回
语句上，不需要在别处维护一张对应表。

数据形状全部来自后端契约（`schema_version`），渲染只做**排版**不做**推导**：
表格里的每一格都来自响应字段，没有一处在 CLI 里重算过（重算 = 第 N 份判据，
而它和后端那份在边界上必然分叉）。
"""

from limo_cli import exit_codes as EXIT
from limo_cli.output import clip, display_width, fmt_chars, pad, table

# 产物种类 —— ⚠️ 这里**只用于帮助文案**（未知 part 时列给用户看）。哪个 part 真的
# 存在由后端判（不存在 → `exit 3`）。故它漂移的后果是「提示里少列了一种」，不是
# 「读错数据」，不值得为它引入一次网络往返。
GENERATED_PARTS = ("notes", "polished")
PROCESS_PARTS = ("extract", "table", "digitize", "understand")
ALL_PARTS = GENERATED_PARTS + PROCESS_PARTS

# `export --part` 额外接受的特殊值：文档正文（转录）。它不是「产物」，但导出侧
# 需要它，而多开一个命令不如多认一个值。
RAW_PART = "raw"


# ── 小工具 ──

def _fmt_time(value: str) -> str:
    """ISO 时间 → `YYYY-MM-DD HH:MM`（只读展示，秒与时区对人不构成信息）。"""
    return (value or "")[:16].replace("T", " ") or "—"


def _type_label(source_type: str) -> str:
    return source_type or "(未标注)"


def _plain(snippet: str) -> str:
    """搜索摘要 → 纯文本：去掉 FTS5 注入的 `<mark>` 标签。

    ⚠️ 不做 ANSI 高亮：本 CLI 的 stdout 不许依赖「是不是终端」（见 `output.py`），
    而带转义码的文本一旦被重定向进文件就是可见噪音（`^[[7m`）。
    """
    return (snippet or "").replace("<mark>", "").replace("</mark>", "")


def _kv(label: str, value) -> str:
    return f"{pad(label, 8)}  {value}"


def _fmt_duration(seconds) -> str:
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _page_tail(page: dict, shown: int) -> str:
    """分页/截断的收尾提示。

    ⚠️ `has_more`（**能继续翻**）与 `truncated`（**到头了但没给完**）要分开说
    （`query.py::Page` 的 docstring）：把后者说成「用 --cursor 继续」等于骗调用方
    去跑一个继续不动的循环。
    """
    bits = [f"共 {shown} 条"]
    if page.get("has_more"):
        bits.append(f"还有更多 —— 用 `--cursor {page.get('next_cursor')}` 继续，"
                    f"或 `--all` 一次取全")
    if page.get("truncated"):
        hint = (page.get("continuation") or {}).get("hint", "")
        bits.append(f"结果已截断（{hint}）" if hint else "结果已截断")
    return " · ".join(bits)


# ── status ──

def cmd_status(src, out, args) -> int:
    data = src.status()
    out.data(data, render=_render_status)
    return EXIT.OK


def _render_status(d: dict) -> str:
    lines = [f"LiMo 库概览  ·  schema {d.get('schema_version', '?')}", ""]
    lines.append(_kv("文档", d.get("docCount", 0)))
    lines.append(_kv("文件夹", f"{d.get('folderCount', 0)} 个"
                             f"（未分类 {d.get('uncategorized', 0)} 篇）"))
    lines.append(_kv("标签", d.get("tagCount", 0)))
    if d.get("oldest"):
        lines.append(_kv("时间跨度", f"{_fmt_time(d['oldest'])} → {_fmt_time(d['newest'])}"))
    by = d.get("bySourceType") or {}
    if by:
        lines += ["", "按类型"]
        width = max(display_width(k) for k in by)
        lines += [f"  {pad(k, width)}  {v}" for k, v in by.items()]
    lines += ["", "用 `limo list` 列出文档，`limo search <关键词>` 搜正文。"]
    return "\n".join(lines)


# ── list ──

def cmd_list(src, out, args) -> int:
    # `--uncategorized` → `folder_id=""`（**有效筛选值**，不是「不过滤」）。
    # ⚠️ 这个参数此前只存在于 argparse 与校验里、**没有任何消费点**：`limo list
    # --uncategorized` 会静默列出全部文档，看起来像「未分类有这么多篇」。
    folder_id = "" if args.uncategorized else args.folder_id
    page = src.list_resources(
        source_type=args.source_type, tag=args.tag, folder_id=folder_id,
        query=args.query, include_descendants=args.recursive,
        limit=args.limit, all_pages=args.all_pages, cursor=args.cursor,
    )
    out.data(page, render=_render_list)
    return EXIT.OK


def _render_list(page: dict) -> str:
    items = page.get("items") or []
    if not items:
        return "没有匹配的文档。"
    rows = [[i.get("id", ""), _type_label(i.get("sourceType")),
             clip(i.get("title", ""), 44), _fmt_time(i.get("createdAt"))]
            for i in items]
    return table(rows, ["ID", "类型", "标题", "创建"]) + "\n\n" + _page_tail(page, len(items))


# ── search ──

def cmd_search(src, out, args) -> int:
    page = src.search(args.query, limit=args.limit)
    out.data(page, render=_render_search)
    return EXIT.OK


def _render_search(page: dict) -> str:
    items = page.get("items") or []
    if not items:
        # 「搜不到」是**成功**（exit 0）—— 与「ID 不存在」（exit 3）是两回事，
        # 调用方据此判断「该换关键词」还是「该报错停下」（见 `exit_codes`）
        return "没有匹配的内容。"
    rows = [[i.get("id", ""), clip(i.get("title", ""), 32),
             clip(_plain(i.get("snippet", "")), 46)] for i in items]
    return table(rows, ["ID", "标题", "命中"]) + "\n\n" + _page_tail(page, len(items))


# ── get ──

def cmd_get(src, out, args) -> int:
    if args.part:
        return _get_part(src, out, args)

    res = src.get_resource(args.doc_id)
    arts = src.list_artifacts(args.doc_id)
    payload = dict(res)
    payload["artifacts"] = arts.get("items") or []
    out.data(payload, render=_render_resource)
    return EXIT.OK


def _render_resource(d: dict) -> str:
    lines = [
        _kv("标题", d.get("title") or "(无标题)"),
        _kv("ID", d.get("id", "")),
        _kv("类型", _type_label(d.get("sourceType"))),
    ]
    if d.get("sourceUrl"):
        lines.append(_kv("来源", d["sourceUrl"]))
    if d.get("engine"):
        lines.append(_kv("引擎", d["engine"]))
    if d.get("language"):
        lines.append(_kv("语言", d["language"]))
    if _fmt_duration(d.get("duration")):
        lines.append(_kv("时长", _fmt_duration(d.get("duration"))))
    lines.append(_kv("创建", _fmt_time(d.get("createdAt"))))
    lines.append(_kv("文件夹", d.get("folderId") or "未分类"))
    if d.get("tags"):
        lines.append(_kv("标签", "、".join(d["tags"])))
    if d.get("sourceTitles"):
        lines.append(_kv("来源文档", "；".join(d["sourceTitles"])))
    raw = d.get("rawText") or ""
    lines.append(_kv("正文", f"{fmt_chars(len(raw))} 字符"
                             f"（`limo export {d.get('id','')} --part {RAW_PART}` 取全文）"))

    arts = d.get("artifacts") or []
    lines += ["", "产物"]
    if not arts:
        lines.append("  （还没有生成任何产物）")
    else:
        rows = [[a.get("kind", ""), f"v{a.get('version', 0)}",
                 _fmt_time(a.get("createdAt")),
                 a.get("style") or a.get("engine") or "",
                 "已编辑" if a.get("edited") else ""]
                for a in arts]
        lines.append(table(rows, ["种类", "版本", "创建", "风格/引擎", ""]))
        lines.append("")
        lines.append(f"  取内容：`limo get {d.get('id','')} --part notes`"
                     f"（或 `limo export ... --part notes -o notes.md`）")
    return "\n".join(lines)


def _get_part(src, out, args) -> int:
    art = src.get_artifact(args.doc_id, args.part, version=args.version,
                           offset=args.offset, max_chars=args.max_chars,
                           full=args.full)
    if args.json:
        out.data(art)
        return EXIT.OK
    # 人类模式：先给一行出处（不然一段没有上下文的正文毫无用处），再给正文
    header = _artifact_header(art)
    out.data({"header": header, "body": art.get("content", "")},
             render=lambda d: f"{d['header']}\n\n{d['body']}")
    if art.get("truncated"):
        cont = art.get("continuation") or {}
        out.note(f"⚠ 只给了 {fmt_chars(len(art.get('content', '')))} / "
                 f"{fmt_chars(art.get('totalChars', 0))} 字符 —— "
                 f"{cont.get('hint', '用 --offset 续读或 --full 取全')}")
    return EXIT.OK


def _artifact_header(art: dict) -> str:
    """产物正文上方的一行出处。四期由导出 meta 承载，这里先给最小可用版。"""
    kind = art.get("kind", "")
    version = art.get("version", 0)
    ver = f"v{version}" if kind in GENERATED_PARTS else ""
    corrupt = "  ⚠ 自证头损坏" if art.get("metaCorrupt") else ""
    return f"── {art.get('docId','')} · {kind} {ver} · {fmt_chars(art.get('totalChars', 0))} 字符{corrupt} ──"


# ── export ──

def cmd_export(src, out, args) -> int:
    """把内容原样取出（stdout 或文件）—— **不做任何排版**。

    这是与 `get --part` 的分工：`get` 是「看一眼」（带出处行与截断提示），
    `export` 是「取出去」（`> notes.md` 之后必须是一份能直接用的文件）。

    ⚠️ 默认取**剥掉自证头**的正文：`--with-meta` 才是「连载体一起要」。默认带上
    注释，`markdown > 文件` 的用户会得到一份首行是机器注释的文档，而注释在多数
    Markdown 渲染器里虽不显示、在纯文本编辑器里却是首屏第一行。
    """
    doc_id = args.doc_id
    if args.part == RAW_PART:
        res = src.get_resource(doc_id)
        content = res.get("rawText") or ""
        if args.json:
            out.data({"schema_version": res.get("schema_version"),
                      "docId": doc_id, "kind": RAW_PART,
                      "content": content, "totalChars": len(content)})
            return EXIT.OK
        return _emit(out, content, args)

    art = src.get_artifact(doc_id, args.part, version=args.version, full=args.full,
                           # 要载体就取**磁盘上那一串**，不在本地按 meta 重建 ——
                           # 重建出的字节与文件里的不是同一份（读路径会给
                           # `identity.version` 补值），"自证"要求的是原件
                           include_raw=bool(args.with_meta))
    if args.json or args.format == "json":
        out.data(art)
        return EXIT.OK
    content = art.get("content", "")
    if args.with_meta:
        content = art.get("rawContent") or content
    return _emit(out, content, args)


def _emit(out, content: str, args) -> int:
    path = getattr(args, "output", "") or ""
    if path:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        out.note(f"已写入 {path}（{fmt_chars(len(content))} 字符）")
        return EXIT.OK
    out.data(content, render=lambda c: c)
    return EXIT.OK


# ── folders ──

def cmd_folders(src, out, args) -> int:
    data = src.folders()
    out.data(data, render=_render_folders)
    return EXIT.OK


def _render_folders(d: dict) -> str:
    items = d.get("items") or []
    if not items:
        return "还没有文件夹。"
    # 层级缩进 + 前序：平铺输出里「技术」「前端」「React」三行看不出谁是谁的子夹，
    # 而用户看 `limo folders` 的第一诉求就是「现在长什么样」。
    #
    # ⚠️ 树的推导**复用 `app.storage.folder_tree`**，不在 CLI 里另写一遍排序与断环
    #   （环数据下自写的 DFS 会死循环）。`limo_cli` 与 `app` 同仓分发，这条 import
    #   失败只会发生在「只装了 CLI」的场景——那时回落成平铺输出，而不是崩掉整条命令。
    # ⚠️ 先按 id 去重：`index.json` 被手改坏时可能出现重复 id，而下面的
    # `index_of` 是 dict —— 重复 id 会让前一条被静默丢出渲染（它的行消失，
    # 而文件夹总数又没变，用户以为 CLI 少读了一个）。只读命令不报错，去重展示。
    _seen: set[str] = set()
    _uniq = []
    for f in items:
        fid = f.get("id", "")
        if fid and fid in _seen:
            continue
        _seen.add(fid)
        _uniq.append(f)
    items = _uniq

    depth_of: dict[str, int] = {}
    order = list(range(len(items)))
    try:
        from app.storage.folder_tree import preorder, normalize_parents
        parent = normalize_parents(items)
        ids = [f["id"] for f in items]
        # ⚠️ id → 下标用 map：`ids.index(i)` 在列表里是 O(n)，外面再套一层
        # 就是 O(n²)（几百个夹时 `limo folders` 明显变慢，且随库增长线性恶化）。
        index_of = {fid: i for i, fid in enumerate(ids)}
        order = [index_of[i] for i in preorder(items)]
        for i in ids:
            n, cur = 0, parent.get(i, "")
            while cur and n < len(ids):          # 断环已在上一步做完，这里只是防御
                n += 1
                cur = parent.get(cur, "")
            depth_of[i] = n
    except Exception:                            # noqa: BLE001 —— 渲染降级，不吞业务错误
        pass

    rows = []
    for idx in order:
        f = items[idx]
        name = "  " * depth_of.get(f.get("id", ""), 0) + f.get("name", "")
        rows.append([f.get("id", ""), name, str(f.get("count", 0)),
                     str(f.get("totalCount", f.get("count", 0)))])
    return table(rows, ["ID", "名称", "文档", "含子文件夹"])
