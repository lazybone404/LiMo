"""
导出端点 — Markdown → HTML / 原始 MD 下载 / 批量 zip
对标 BiliNote export.py: markdown→HTML（含表格/代码块/LaTeX）→ 浏览器下载
"""
import asyncio
import base64
import io
import json
import logging
import os
import re
import shutil
import tempfile
import uuid as _uuid
import zipfile
from datetime import datetime

# ── Pygments 语法高亮（可选依赖）──
try:
    from pygments import highlight
    from pygments.formatters import HtmlFormatter
    from pygments.lexers import get_lexer_by_name, guess_lexer
    _PYGMENTS_OK = True
except ImportError:
    _PYGMENTS_OK = False
from urllib.parse import quote
from pathlib import Path
from fastapi import APIRouter, Request, Query, HTTPException, Depends
from fastapi.responses import Response, StreamingResponse
from app.core.dependencies import get_store
from app.core.disconnect import disconnect_watcher
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()

# ── KaTeX 服务端渲染（Node.js 子进程）──

_KATEX_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "render_math.js"

def _render_formulas_katex(md: str) -> str:
    """调用 Node.js KaTeX 脚本将 $$...$$ 和 $...$ 预渲染为 HTML"""
    import subprocess, shutil
    # Node.js 或脚本不存在 → 静默回退（打包环境很常见）
    if not shutil.which("node") or not _KATEX_SCRIPT.exists():
        return md
    try:
        import os as _os
        r = subprocess.run(
            ["node", str(_KATEX_SCRIPT)],
            input=md, capture_output=True, text=True, timeout=60,
            encoding='utf-8', errors='replace',
            cwd=str(_KATEX_SCRIPT.parent),
            creationflags=subprocess.CREATE_NO_WINDOW if _os.name == 'nt' else 0,
        )
        if r.returncode == 0:
            return r.stdout
        logger.warning("KaTeX 渲染失败: %s", r.stderr[:200])
        return md
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.warning("KaTeX 不可用 (%s)，回退到原始公式", e)
        return md

# ── LaTeX 保护：在 markdown→HTML 转换前保护公式，转换后还原 ──

def _protect_latex(md: str) -> tuple[str, list[str]]:
    """将 $$...$$ 和 $...$ 公式替换为占位符，防止 markdown 转换器破坏公式。
    返回 (protected_md, placeholders) — 调用方需将 placeholders 传给 _restore_latex。"""
    placeholders: list[str] = []
    i = 0

    def _save_block(m):
        nonlocal i
        placeholders.append(m.group(0))
        ph = f'⟨⟨LATEX_BLOCK_{i}⟩⟩'
        i += 1
        return ph

    def _save_inline(m):
        nonlocal i
        placeholders.append(m.group(0))
        ph = f'⟨⟨LATEX_INLINE_{i}⟩⟩'
        i += 1
        return ph

    # 先保护 $$...$$（块级），用 DOTALL 跨行
    md = re.sub(r'\$\$\s*(.+?)\s*\$\$', _save_block, md, flags=re.DOTALL)
    # 再保护 $...$（行内），TeX 惯例：$ 前后不能为数字，前后至少一个非空白字符
    # 防止误匹配 $5 and $10 等货币金额
    md = re.sub(r'(?<!\d)\$(?=\S)(.+?)(?<=\S)\$(?!\d)', _save_inline, md)
    return md, placeholders

def _restore_latex(html: str, placeholders: list[str]) -> str:
    """将占位符还原为原始 LaTeX 公式"""
    for i, latex in enumerate(placeholders):
        html = html.replace(f'⟨⟨LATEX_BLOCK_{i}⟩⟩', latex)
        html = html.replace(f'⟨⟨LATEX_INLINE_{i}⟩⟩', latex)
    return html

# ── 导出端点 ──

@router.get("/export/{tx_id}")
async def export_content(
    tx_id: str,
    request: Request,
    store=Depends(get_store),
    format: str = Query("md", description="导出格式: md / html"),
    mode: str = Query("", description="产物模式: polished / notes / transcript"),
):
    """导出文档为 MD 或 HTML"""

    if mode == "transcript":
        tx = store.get_transcript(tx_id)
        if not tx:
            raise HTTPException(status_code=404, detail="文档不存在")
        content = tx.get("rawText", "")
        title = tx.get("title", "transcript")
    else:
        # 默认取 notes，避免 polished 排在前面导致与预览栏不一致
        filter_mode = mode if mode else "notes"
        mds = store.get_markdowns_by_transcript(tx_id, mode=filter_mode)
        # mode 已显式指定但未找到 → 不回退到其他 mode 的内容（预览一致原则）
        # 仅 mode 为空（用户未指定）时，无 notes 才回退到任意可用产物
        if not mds and not mode:
            mds = store.get_markdowns_by_transcript(tx_id, mode="")
        content = mds[0]["content"] if mds else ""
        # 回退：无 markdown 条目时尝试 process results
        # 图片文档（sourceType=image）没有 markdown 条目，处理结果存在 process_results 中
        # mode 如 extract/table/digitize/understand 对应图片处理的各种模式
        if not content and mode:
            results = store.get_process_results(tx_id)
            for r in results:
                if r.get("mode") == mode:
                    content = store.get_process_result(r["id"]) or ""
                    break
        tx = store.get_transcript(tx_id)
        title = tx.get("title", "export") if tx else "export"

    if not content:
        raise HTTPException(status_code=404, detail="无可导出内容")

    if format == "html":
        # 图片 base64 内嵌 → 自包含单文件 HTML
        refs = _find_image_refs(content)
        if refs:
            imgs, name_map = _collect_images(refs, store.root)
            if imgs:
                content = _inline_images(content, imgs, name_map, store.root)
        # 用 limo 主题匹配应用内预览样式
        export_theme = request.query_params.get("theme", "limo")
        html = _build_selfcontained_html(content, title, theme=export_theme)
        safe_name = _safe_filename(title)
        encoded_name = quote(f"{safe_name}.html", safe='')
        return Response(
            html, media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"},
        )

    # MD: 将图片路径重写为绝对 URL，外部查看时不裂图
    # 使用请求的 base_url 拼接完整路径（开发模式走 Vite proxy，打包模式直连 127.0.0.1:7413）
    base = str(request.base_url).rstrip('/')
    content = re.sub(r'\(/api/files/', f'({base}/api/files/', content)
    content = re.sub(r'\(/static/screenshots/', f'({base}/static/screenshots/', content)
    safe_name = _safe_filename(title)
    suffix = f"-{mode}" if mode else ""
    encoded_name = quote(f"{safe_name}{suffix}.md", safe='')
    return Response(
        content, media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"},
    )


def _safe_filename(title: str) -> str:
    """去除文件名不安全字符"""
    import re
    name = title or "export"
    name = re.sub(r'[\\/:*?"<>|]', '', name)
    return name[:80] if len(name) > 80 else name


# DEPRECATED: unused, kept for future use
def _export_filename(title: str, label: str, ext: str) -> str:
    """统一的导出文件名：{label}-{safe_title}.{ext}"""
    safe = _safe_filename(title)
    return f"{label}-{safe}.{ext}"


def _collect_doc_products(store, tx_id: str) -> dict[str, str]:
    """收集单个文档的所有产物 → {文件名: 内容}"""
    products = {}
    tx = store.get_transcript(tx_id)
    if not tx:
        return products
    title = _safe_filename(tx.get("title", tx_id))

    # 转录原文
    raw = tx.get("rawText", "")
    if raw:
        products[f"{title}-转录原文.md"] = raw

    # 笔记产物
    for md in store.get_markdowns_by_transcript(tx_id):
        md_mode = md.get("mode", "")
        if md_mode == "polished": mode_label = "整理版原文"
        elif md_mode == "fact_check": mode_label = "事实验证"
        else: mode_label = "笔记"
        ver = f"_v{md['version']}" if md.get("version", 1) > 1 else ""
        products[f"{title}-{mode_label}{ver}.md"] = md["content"]

    # 图片处理结果
    for pr in store.get_process_results(tx_id):
        mode_labels = {"extract": "提取文字", "table": "表格数据",
                       "digitize": "文档数字化", "understand": "理解内容"}
        mode_label = mode_labels.get(pr.get("mode"), pr.get("mode", ""))
        content = store.get_process_result(pr["id"])
        if content:
            products[f"{title}-{mode_label}.md"] = content

    return products


@router.get("/export/{tx_id}/all")
async def export_all_products(tx_id: str, request: Request, store=Depends(get_store)):
    """导出单个文档的全部产物为自包含 ZIP（含 images/ 目录）"""
    products = _collect_doc_products(store, tx_id)
    if not products:
        raise HTTPException(status_code=404, detail="无可导出内容")

    tx = store.get_transcript(tx_id)
    title = _safe_filename(tx.get("title", tx_id)) if tx else tx_id

    # 收集所有产物的图片引用 → 合并去重
    all_refs: list[tuple[str, str]] = []
    for content in products.values():
        all_refs.extend(_find_image_refs(content))
    imgs, name_map = _collect_images(all_refs, store.root)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname, content in products.items():
            if imgs:
                content = _rewrite_image_paths(content, name_map, prefix="images/")
            zf.writestr(f"{title}/{fname}", content)
        # 写入图片文件
        for img_name, data in imgs.items():
            zf.writestr(f"{title}/images/{img_name}", data)
    buf.seek(0)

    fname = f"{title}_all_products.zip"
    encoded = quote(fname.encode('utf-8'), safe='')
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"},
    )


@router.post("/export/batch")
async def export_batch(data: dict, request: Request, store=Depends(get_store)):
    """批量导出多个文档的产物为 ZIP（每文档独立子文件夹 + images/）"""
    import re as _re
    ids = data.get("ids", [])
    if not ids or not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="缺少 ids 参数或格式错误")

    for tid in ids:
        if not isinstance(tid, str) or not _re.match(r'^[a-zA-Z0-9_\-]+$', tid) or len(tid) > 64:
            return Response(f"invalid id: {tid}", status_code=400)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for tid in ids:
            products = _collect_doc_products(store, tid)
            if not products:
                continue
            tx = store.get_transcript(tid)
            folder = _safe_filename(tx.get("title", tid)) if tx else tid

            # 收集该文档所有产物图片
            all_refs: list[tuple[str, str]] = []
            for content in products.values():
                all_refs.extend(_find_image_refs(content))
            imgs, name_map = _collect_images(all_refs, store.root)

            for fname, content in products.items():
                if imgs:
                    content = _rewrite_image_paths(content, name_map, prefix="images/")
                zf.writestr(f"{folder}/{fname}", content)
            for img_name, data in imgs.items():
                zf.writestr(f"{folder}/images/{img_name}", data)
    buf.seek(0)

    date = datetime.now().strftime("%Y-%m-%d")
    fname = f"LiMo_export_{date}.zip"
    encoded = quote(fname.encode('utf-8'), safe='')
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"},
    )


# ── 对话导出 ──

@router.get("/export/{tx_id}/mindmap")
async def export_mindmap(tx_id: str, request: Request, store=Depends(get_store)):
    """导出思维导图为自包含交互式 HTML（d3.js tree layout）"""
    mds = store.get_markdowns_by_transcript(tx_id)
    # 优先取笔记（非 polished、非 fact_check），与 custom 导出 mindmap tab 一致
    content = ""
    for md in mds:
        if md.get("mode", "") not in ("polished", "fact_check"):
            content = md.get("content", "")
            break
    # 回退：没有 notes 时取任意第一个
    if not content:
        content = mds[0]["content"] if mds else ""

    if not content:
        tx = store.get_transcript(tx_id)
        content = tx.get("rawText", "") if tx else ""
    if not content:
        return Response("no content", status_code=404)

    tx = store.get_transcript(tx_id)
    title = tx.get("title", "mindmap") if tx else "mindmap"
    html = _build_mindmap_html(content, title)

    safe_name = _safe_filename(title)
    encoded_name = quote(f"{safe_name}_mindmap.html", safe='')
    return Response(
        html, media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"},
    )


@router.get("/export/conversation/{conv_id}")
def export_conversation(conv_id: str, format: str = "md", request: Request = None, store=Depends(get_store)):
    conv = store.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="对话不存在")

    title = _safe_filename(conv.get("title", conv_id))
    date = datetime.now().strftime("%Y-%m-%d")

    if format == "json":
        return Response(
            json.dumps(conv, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(f'{title}.json'.encode('utf-8'), safe='')}"},
        )

    # Markdown 格式
    lines = [f"# {conv.get('title', '对话')}", f"\n导出日期: {date}\n"]
    for m in conv.get("messages", []):
        ts = m.get("timestamp", "")[:16]
        if m["role"] == "user":
            lines.append(f"**用户** ({ts})\n\n{m['content']}\n")
        elif m["role"] == "assistant":
            lines.append(f"**AI** ({ts})\n\n{m['content']}\n")
    md = "\n".join(lines)
    return Response(md, media_type="text/markdown",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(f'{title}.md'.encode('utf-8'), safe='')}"})


@router.post("/export/conversations/batch")
async def export_conversations(data: dict, request: Request, store=Depends(get_store)):
    ids = data.get("ids", [])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for cid in ids:
            conv = store.get_conversation(cid)
            if not conv:
                continue
            title = _safe_filename(conv.get("title", cid))
            zf.writestr(f"{title}.json", json.dumps(conv, ensure_ascii=False, indent=2))
            lines = [f"# {conv.get('title', '对话')}\n"]
            for m in conv.get("messages", []):
                role = "用户" if m["role"] == "user" else "AI"
                ts = str(m.get("timestamp", ""))[:16]
                ts_str = f" ({ts})" if ts else ""
                lines.append(f"**{role}**{ts_str}\n\n{m['content']}\n")
            zf.writestr(f"{title}.md", "\n".join(lines))
    buf.seek(0)
    date = datetime.now().strftime("%Y-%m-%d")
    filename = f"LiMo_chats_{date}.zip"
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename.encode('utf-8'), safe='')}"})


# ── HTML 主题 ──

HTML_THEMES = {
    "paper": """body { font-family: "Times New Roman", "Songti SC", serif; font-size: 12pt; line-height: 2; max-width: 700px; margin: 60px auto; padding: 0 40px; color: #111; }
h1 { font-size: 1.8em; text-align: center; margin-bottom: 0.5em; }
h2 { font-size: 1.4em; border-bottom: 1px solid #333; padding-bottom: 4px; }
h3 { font-size: 1.15em; }
del { text-decoration: line-through; }
table { border-collapse: collapse; width: 100%; margin: 16px 0; }
th, td { border: 1px solid #333; padding: 6px 10px; }
th { background: #eee; }""",
    "github": """body { font-family: -apple-system, "PingFang SC", sans-serif; max-width: 860px; margin: 40px auto; padding: 0 24px; line-height: 1.75; color: #1C1C1E; }
h1 { font-size: 2em; border-bottom: 1px solid #d0d7de; padding-bottom: .3em; }
h2 { font-size: 1.5em; border-bottom: 1px solid #d0d7de; padding-bottom: .3em; margin-top: 24px; }
h3 { font-size: 1.25em; margin-top: 24px; }
del { text-decoration: line-through; }
table { border-collapse: collapse; width: 100%; margin: 16px 0; }
th, td { border: 1px solid #d0d7de; padding: 8px 12px; }
th { background: #f6f8fa; font-weight: 600; }
blockquote { border-left: 4px solid #007AFF; padding: 0 1em; color: #636366; }
pre { background: #f6f8fa; border-radius: 8px; padding: 16px; }
code { font-family: "SF Mono", monospace; font-size: 0.9em; }
:not(pre) > code { background: #f6f8fa; padding: 2px 6px; border-radius: 4px; color: #FF2D55; }""",
    "minimal": """body { font-family: -apple-system, "PingFang SC", sans-serif; max-width: 720px; margin: 80px auto; padding: 0 24px; line-height: 1.8; color: #333; background: #fff; }
h1 { font-weight: 300; font-size: 2.2em; margin-bottom: 0.3em; color: #000; }
h2 { font-weight: 400; font-size: 1.5em; margin-top: 2em; color: #111; }
h3 { font-weight: 500; font-size: 1.1em; color: #333; }
del { text-decoration: line-through; }
table { border-collapse: collapse; width: 100%; margin: 16px 0; }
th, td { border-bottom: 1px solid #e8e8e8; padding: 10px 14px; text-align: left; }
th { font-weight: 500; color: #888; font-size: 0.85em; text-transform: uppercase; }
pre { background: #fafafa; border-radius: 4px; padding: 20px; }
code { font-family: "SF Mono", monospace; font-size: 0.9em; }
:not(pre) > code { background: #fafafa; padding: 1px 5px; border-radius: 3px; }""",
    "limo": """body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; max-width: 860px; margin: 40px auto; padding: 0 24px; line-height: 1.75; color: #1C1C1E; background: #fff; }
h1 { font-size: 2em; color: #1E3A5F; border-bottom: 1px solid #d0d7de; padding-bottom: .3em; }
h2 { font-size: 1.5em; color: #1D4ED8; border-bottom: 1px solid #d0d7de; padding-bottom: .3em; margin-top: 24px; }
h3 { font-size: 1.25em; color: #2563EB; margin-top: 24px; }
h4 { font-size: 1em; color: #3B82F6; margin-top: 24px; }
strong { color: #1E40AF; }
a { color: #2563EB; }
del { text-decoration: line-through; }
blockquote { border-left: 4px solid #3B82F6; background: #EFF6FF; margin: 0; padding: 0 1em; color: #636366; border-radius: 0 8px 8px 0; }
pre { background: #f6f8fa; border-radius: 8px; padding: 16px; overflow-x: auto; }
code { font-family: "SF Mono", "Fira Code", monospace; font-size: 0.9em; }
:not(pre) > code { background: #F0F4FF; color: #1D4ED8; padding: 2px 6px; border-radius: 4px; }
table { border-collapse: collapse; width: 100%; margin: 16px 0; }
th, td { border: 1px solid #D2D2D7; padding: 8px 12px; text-align: left; font-size: 0.95em; }
th { background: #F5F5F7; font-weight: 600; }
img { max-width: 100%; border-radius: 8px; }
hr { border-color: #D2D2D7; }
@media (prefers-color-scheme: dark) {
  body { color: #E5E5EA; background: #1C1C1E; }
  h1 { color: #93C5FD; border-bottom-color: #48484A; }
  h2 { color: #60A5FA; border-bottom-color: #48484A; }
  h3 { color: #60A5FA; }
  h4 { color: #93C5FD; }
  strong { color: #93C5FD; }
  a { color: #60A5FA; }
  blockquote { background: rgba(59,130,246,0.08); border-left-color: #60A5FA; color: #AEAEB2; }
  pre { background: #2C2C2E; }
  code { color: #E5E5EA; }
  :not(pre) > code { background: rgba(59,130,246,0.12); color: #93C5FD; }
  table th { background: #2C2C2E; }
  table td, table th { border-color: #48484A; }
  img { opacity: 0.85; }
  hr { border-color: #48484A; }
}""",
}

def _preprocess_gfm(md: str) -> str:
    """预处理 GFM 特性（Python markdown 不原生支持）"""
    # 确保 GFM 表格前有空行 — Python markdown tables 扩展要求表格前有空行，
    # 但 GFM 标准允许表格紧跟前导文字。不加此行会导致"[文字]\n|表头|\n|---|"
    # 被当作段落文本输出（react-markdown + remark-gfm 可正常渲染）
    md = re.sub(r'(^[^\n|][^\n]*)\n(\|[^\n]+\|[ \t]*\n\|[-: |]+\|)',
               r'\1\n\n\2', md, flags=re.MULTILINE)
    # 删除线 ~~text~~ → <del>text</del>
    md = re.sub(r'~~(.+?)~~', r'<del>\1</del>', md)
    # 任务列表 - [ ] → ☐, - [x] → ☑
    md = re.sub(r'^- \[ \] (.+)$', r'- ☐ \1', md, flags=re.MULTILINE)
    md = re.sub(r'^- \[x\] (.+)$', r'- ☑ \1', md, flags=re.MULTILINE)
    # 时间戳 badge — Content-[mm:ss] → 蓝色, Screenshot-[mm:ss] → 橙色
    md = re.sub(
        r'Content-\[(\d{2}:\d{2})\]',
        r'<span class="ts-badge ts-content">▶ \1</span>', md)
    md = re.sub(
        r'Screenshot-\[(\d{2}:\d{2})\]',
        r'<span class="ts-badge ts-screenshot">📷 \1</span>', md)
    return md


def _markdown_to_html(md: str) -> str:
    """统一的 Markdown → HTML 转换，含代码高亮（Pygments）+ 语言标签"""
    md = _preprocess_gfm(md)
    extensions = ["tables", "fenced_code", "sane_lists", "smarty", "md_in_html", "def_list", "abbr"]
    if _PYGMENTS_OK:
        extensions.append("codehilite")
    try:
        import markdown
        html = markdown.markdown(md, extensions=extensions)
    except ImportError:
        html = f"<pre>{md}</pre>"
    html = _add_lang_labels(html)
    html = _add_heading_ids(html)
    return html


def _add_lang_labels(html: str) -> str:
    """在代码块前插入语言标签 <span class=\"lang-label\">，匹配预览的代码语言栏"""
    def _insert_label(m: re.Match) -> str:
        lang = m.group(1)
        label = f'<span class="lang-label">{lang}</span>\n'
        return label + m.group(0)
    # 匹配 <pre><code class="language-xxx"> 或 codehilite 包裹的 <code class="language-xxx">
    return re.sub(
        r'(?:<div class="codehilite">\s*)?<pre><code class="language-(\w+)"[^>]*>',
        _insert_label,
        html,
    )


def _add_heading_ids(html: str) -> str:
    """给 <h2>/<h3>/<h4> 追加 id 属性（算法对齐前端 headingId），
    使得 HTML 导出中的标题锚点链接可以定位"""
    def _make_id(text: str) -> str:
        # 先清除时间戳标记
        text = re.sub(r'(?:▶ |\*?Content-\[)?\d{2}:\d{2}\]?', '', text)
        text = re.sub(r'Screenshot-\[\d{2}:\d{2}\]', '', text)
        # 转小写、空白→短横、保留字母/数字/CJK
        text = text.lower().strip()
        text = re.sub(r'\s+', '-', text)
        text = re.sub(r'[^a-z0-9一-鿿\-]', '', text)
        text = re.sub(r'-+', '-', text)
        return text.strip('-')

    def _repl(m: re.Match) -> str:
        tag, inner = m.group(1), m.group(2)
        id_ = _make_id(inner)
        return f'<{tag} id="{id_}">{inner}</{tag}>'

    return re.sub(r'<(h[234])>(.+?)</\1>', _repl, html)


def _get_pygments_css() -> str:
    """生成 Pygments 代码高亮 CSS（匹配预览的 oneLight 风格）"""
    if not _PYGMENTS_OK:
        return ""
    try:
        formatter = HtmlFormatter(style="default", noclasses=False)
        # 精简：只保留 .codehilite 相关规则
        css = formatter.get_style_defs('.codehilite')
        # 追加语言标签样式
        css += """
.codehilite { background: #f6f8fa; border-radius: 8px; padding: 16px; overflow-x: auto; margin: 16px 0; }
.codehilite .lang-label { display: block; font-size: 0.75em; color: #636366; margin-bottom: 8px; font-weight: 600; }
"""
        return css
    except Exception:
        return ""


def _get_badge_css() -> str:
    """时间戳 badge CSS — 对标前端 markdown-components.tsx 的 Tailwind 样式"""
    return """
.ts-badge { display: inline-block; border-radius: 999px; padding: 1px 8px;
    font-size: 0.8em; font-weight: 500; vertical-align: middle; margin: 0 2px; }
.ts-content { background: rgba(0,122,255,0.1); color: #007aff; }
.ts-screenshot { background: rgba(255,149,0,0.1); color: #ff9500; }
"""


def _render_formulas_to_images(md: str) -> str:
    """将 Markdown 中的 $$...$$ 和 $...$ 公式渲染为 base64 PNG 嵌入"""
    import base64
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        return md  # matplotlib 不可用，保留原文

    def _latex_to_png(latex: str) -> str:
        """渲染单个 LaTeX 公式为 PNG base64 data URL。
        将常见 LaTeX 命令转换为 matplotlib mathtext 兼容格式。"""
        # LaTeX → mathtext 兼容转换
        expr = latex
        expr = re.sub(r'\\text\{([^}]*)\}', r'\\mathrm{\1}', expr)   # \text → \mathrm
        expr = re.sub(r'\\mathcal\{([^}]*)\}', r'\1', expr)           # \mathcal → 去掉（mathtext 不支持）
        expr = re.sub(r'\\mathbb\{([^}]*)\}', r'\\mathbf{\1}', expr)  # \mathbb → \mathbf
        expr = re.sub(r'\\bm\{([^}]*)\}', r'\\mathbf{\1}', expr)      # \bm → \mathbf
        expr = re.sub(r'\\math(?:scr|frak|cal)\{([^}]*)\}', r'\1', expr)
        try:
            # 根据公式长度动态计算尺寸
            w = max(len(expr) * 0.12 + 0.6, 1.5)
            h = max(0.55, min(1.2, len(expr) * 0.008 + 0.45))
            fig, ax = plt.subplots(figsize=(w, h))
            ax.axis('off')
            ax.text(0.5, 0.5, f"${expr}$", fontsize=14, ha='center', va='center',
                    transform=ax.transAxes)
            buf = io.BytesIO()
            fig.savefig(buf, format='png', dpi=100, bbox_inches='tight', pad_inches=0.05,
                        facecolor='white', edgecolor='none')
            plt.close(fig)
            buf.seek(0)
            return f"data:image/png;base64,{base64.b64encode(buf.read()).decode()}"
        except Exception:
            plt.close('all')
            return f"[公式: {latex[:60]}]"

    # 先处理 $$...$$（块级公式）
    def _replace_block(m):
        latex = m.group(1).strip()
        png = _latex_to_png(latex)
        return f'\n\n![公式]({png})\n\n'

    md = re.sub(r'\$\$\s*(.+?)\s*\$\$', _replace_block, md, flags=re.DOTALL)

    # 处理 $...$（行内公式，TeX 惯例：$ 前后不邻数字或空白）
    def _replace_inline(m):
        latex = m.group(1).strip()
        png = _latex_to_png(latex)
        return f'![公式]({png})'

    md = re.sub(r'(?<!\d)\$(?=\S)(.+?)(?<=\S)\$(?!\d)', _replace_inline, md)

    return md


# ── 图片收集/重写（导出自包含核心）──

_IMG_RE = re.compile(r'!\[([^\]]*)\]\(/api/files/([a-zA-Z0-9_\-]+)/([a-zA-Z0-9_\-.]{1,}[^\s)]*)\)')
# 截图图片（/static/screenshots/filename.jpg）
_SS_RE  = re.compile(r'!\[([^\]]*)\]\(/static/screenshots/([a-zA-Z0-9_\-.]{1,}[^\s)]*)\)')


def _find_image_refs(text: str) -> list[tuple[str, str, str | None]]:
    """扫描文本中所有图片引用 → [(tx_id, filename, source), ...]。
    source='files' 表示文档内嵌图；source='screenshots' 表示视频截图。
    按首次出现顺序排列并去重。
    """
    seen: set[str] = set()
    refs: list[tuple[str, str, str | None]] = []
    for m in _IMG_RE.finditer(text):
        full = m.group(0)
        if full not in seen:
            seen.add(full)
            refs.append((m.group(2), m.group(3), "files"))
    for m in _SS_RE.finditer(text):
        full = m.group(0)
        if full not in seen:
            seen.add(full)
            refs.append(("", m.group(2), "screenshots"))
    return refs


def _collect_images(refs: list[tuple[str, str, str | None]], store_root: str
                     ) -> tuple[dict[str, bytes], dict[str, str]]:
    """从磁盘读取所有引用图片。
    返回 (image_data, name_map):
      - image_data: {final_filename: bytes}
      - name_map: {original_filename: final_filename}（供 _rewrite_image_paths 用）
    重名冲突自动加 _N 后缀；文件缺失跳过并 log.warning。
    """
    image_data: dict[str, bytes] = {}
    name_map: dict[str, str] = {}
    seen_names: set[str] = set()

    for tx_id, filename, source in refs:
        if source == "screenshots":
            src = Path(store_root) / "screenshots" / filename
        else:
            src = Path(store_root) / "files" / tx_id / filename
        if not src.exists():
            logger.warning("导出图片缺失: %s", src)
            continue

        # 同文件已处理过 → 直接用已分配的名字
        if filename in name_map:
            continue

        # 重名处理
        final_name = filename
        parts = filename.rsplit(".", 1)
        base = parts[0]
        ext = parts[1] if len(parts) > 1 else ""
        counter = 1
        while final_name in seen_names:
            final_name = f"{base}_{counter}.{ext}" if ext else f"{base}_{counter}"
            counter += 1

        seen_names.add(final_name)
        name_map[filename] = final_name
        image_data[final_name] = src.read_bytes()

    return image_data, name_map


def _rewrite_image_paths(text: str, name_map: dict[str, str],
                         prefix: str = "images/") -> str:
    """将 /api/files/{tx_id}/{old_name} 和 /static/screenshots/{old_name} → {prefix}{new_name}。
    name_map: {出现在原始文本中的完整文件名: 最终文件名}（来自 _collect_images 的去重结果）。
    """

    def _replace(m: re.Match) -> str:
        alt = m.group(1)
        filename = m.group(3) if m.re is _IMG_RE else m.group(2)
        new_name = name_map.get(filename, filename)
        return f"![{alt}]({prefix}{new_name})"

    text = _IMG_RE.sub(_replace, text)
    text = _SS_RE.sub(_replace, text)
    return text


def _inline_images(text: str, image_data: dict[str, bytes],
                   name_map: dict[str, str], store_root: str) -> str:
    """将图片引用替换为 base64 data URI。
    name_map: {original_filename: final_filename}（来自 _collect_images）。
    image_data: {final_filename: bytes}。
    """

    # 构建 final_name → data 的快速查找
    def _replace(m: re.Match) -> str:
        alt = m.group(1)
        is_ss = m.re is _SS_RE
        filename = m.group(2) if is_ss else m.group(3)
        final_name = name_map.get(filename, filename)
        data = image_data.get(final_name)
        if not data:
            if is_ss:
                src = Path(store_root) / "screenshots" / filename
            else:
                src = Path(store_root) / "files" / m.group(2) / filename
            if src.exists():
                data = src.read_bytes()
        if data:
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
            mime_map = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png",
                        "gif": "gif", "webp": "webp", "svg": "svg+xml"}
            mime = f"image/{mime_map.get(ext, 'png')}"
            import base64
            b64 = base64.b64encode(data).decode()
            return f"![{alt}](data:{mime};base64,{b64})"
        return m.group(0)

    text = _IMG_RE.sub(_replace, text)
    text = _SS_RE.sub(_replace, text)
    return text


# ── KaTeX 自包含缓存 ──

_KATEX_CACHE: dict[str, str] | None = None
_MARKMAP_CACHE: dict[str, str] | None = None


def _get_katex_assets() -> dict[str, str]:
    """返回 {"css": ..., "js": ...} 用于内联到 HTML。
    优先读 frontend/node_modules/katex/dist/，不可用时空字典。
    CSS 中 @font-face 的 url(fonts/...) 替换为 base64 data URI，
    确保 HTML 文件完全自包含（离线可用，零 CDN 依赖）。
    首次加载后内存缓存。
    """
    global _KATEX_CACHE
    if _KATEX_CACHE is not None:
        return _KATEX_CACHE

    _KATEX_CACHE = {}
    search_dirs = [
        Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "node_modules" / "katex" / "dist",
        Path(__file__).resolve().parent.parent.parent / "static" / "katex",
    ]
    for dist in search_dirs:
        css_file = dist / "katex.min.css"
        js_file = dist / "katex.min.js"
        auto_render = dist / "contrib" / "auto-render.min.js"
        if css_file.exists() and js_file.exists():
            css = css_file.read_text(encoding="utf-8")
            # 内联字体：url(fonts/xxx.woff2) → url(data:font/woff2;base64,...)
            fonts_dir = dist / "fonts"
            if fonts_dir.exists():
                def _inline_font(m: re.Match) -> str:
                    font_file = fonts_dir / m.group(1)
                    if not font_file.exists():
                        return m.group(0)
                    ext = font_file.suffix.lower()
                    mime = {".woff2": "font/woff2", ".woff": "font/woff",
                            ".ttf": "font/truetype"}.get(ext, "application/octet-stream")
                    b64 = base64.b64encode(font_file.read_bytes()).decode()
                    return f"url(data:{mime};base64,{b64})"
                css = re.sub(r'url\(fonts/([^)]+)\)', _inline_font, css)
                logger.info("KaTeX fonts inlined from %s/fonts", dist)
            _KATEX_CACHE["css"] = css
            js = js_file.read_text(encoding="utf-8")
            if auto_render.exists():
                js += "\n" + auto_render.read_text(encoding="utf-8")
            _KATEX_CACHE["js"] = js
            logger.info("KaTeX assets loaded from %s", dist)
            break

    if not _KATEX_CACHE:
        logger.warning("KaTeX assets not found — HTML export will lack formula rendering")
    return _KATEX_CACHE


# ── 自包含 HTML 构建 ──

def _build_selfcontained_html(md: str, title: str, theme: str = "limo") -> str:
    """构建完全自包含的 HTML 文档（图片已在上游处理完毕）。

    公式策略：
    - Node.js 可用 → 服务端 KaTeX 预渲染（公式→HTML），内联 CSS
    - Node.js 不可用 → 保留 $...$ + 内联 CSS + JS 客户端渲染
    无任何外部 CDN 依赖。
    """
    import html as html_mod

    theme_css = HTML_THEMES.get(theme, HTML_THEMES["github"])
    safe_title = html_mod.escape(title or "导出")

    # 公式处理：优先服务端预渲染
    md, ph = _protect_latex(md)
    body = _markdown_to_html(md)
    body = _restore_latex(body, ph)

    # KaTeX 渲染
    katex = _get_katex_assets()
    katex_css = katex.get("css", "")
    katex_js = katex.get("js", "")

    body = _render_formulas_katex(body)

    # 公式：优先本地 KaTeX，不可用时 CDN 回退
    if katex_js:
        footer_script = f"<script>{katex_js}</script><script>renderMathInElement(document.body,{{delimiters:[{{left:'$$',right:'$$',display:true}},{{left:'$',right:'$',display:false}}]}})</script>"
    else:
        footer_script = """<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
<script src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js"></script>
<script>renderMathInElement(document.body,{delimiters:[{left:'$$',right:'$$',display:true},{left:'$',right:'$',display:false}]})</script>"""

    style_block = f"<style>{theme_css}</style>"
    pygments_css = _get_pygments_css()
    if pygments_css:
        style_block += f"\n<style>{pygments_css}</style>"
    badge_css = _get_badge_css()
    if badge_css:
        style_block += f"\n<style>{badge_css}</style>"
    if katex_css:
        style_block += f"\n<style>{katex_css}</style>"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{safe_title}</title>
{style_block}
</head>
<body>
<h1>{safe_title}</h1>
{body}
{footer_script}
</body>
</html>"""


# ── 思维导图导出 ──

def _get_markmap_assets() -> dict[str, str]:
    """返回 {"d3": ..., "markmap_view": ..., "markmap_lib": ...} 用于思维导图 HTML。
    双路径搜索：frontend/node_modules（开发）→ backend/static/markmap（打包）。
    对标 _get_katex_assets() 的双路径设计。
    """
    global _MARKMAP_CACHE
    if _MARKMAP_CACHE is not None:
        return _MARKMAP_CACHE

    _MARKMAP_CACHE = {}

    # 双路径搜索：开发环境 node_modules → 打包环境 static/markmap
    search_roots = [
        Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "node_modules",
        Path(__file__).resolve().parent.parent.parent / "static" / "markmap",
    ]

    for root in search_roots:
        if not root.exists():
            continue
        # d3.js（markmap 的渲染引擎依赖）
        d3 = root / "d3" / "dist" / "d3.min.js" if root.name == "node_modules" else root / "d3.min.js"
        if d3.exists():
            _MARKMAP_CACHE["d3"] = d3.read_text(encoding="utf-8")
            logger.info("markmap: d3.js loaded (%dKB) from %s",
                        len(_MARKMAP_CACHE["d3"]) // 1024, root)

        # markmap-view browser bundle (UMD, depends on d3)
        mv = root / "markmap-view" / "dist" / "browser" / "index.js" if root.name == "node_modules" else root / "markmap-view.js"
        if mv.exists():
            _MARKMAP_CACHE["markmap_view"] = mv.read_text(encoding="utf-8")

        # markmap-lib browser IIFE (adds Transformer to window.markmap)
        ml = root / "markmap-lib" / "dist" / "browser" / "index.iife.js" if root.name == "node_modules" else root / "markmap-lib.js"
        if ml.exists():
            _MARKMAP_CACHE["markmap_lib"] = ml.read_text(encoding="utf-8")
            logger.info("markmap: lib loaded (%dKB) from %s",
                        len(_MARKMAP_CACHE["markmap_lib"]) // 1024, root)

        # 有 d3 就说明这个路径有效，跳出
        if _MARKMAP_CACHE.get("d3"):
            break

    if not _MARKMAP_CACHE.get("d3"):
        logger.warning("d3.js not found — mindmap export will lack rendering")
    return _MARKMAP_CACHE


def _build_mindmap_html(md: str, title: str) -> str:
    """构建自包含交互式思维导图 HTML。
    使用 markmap（markmap-lib + markmap-view + d3），与预览栏渲染效果一致。
    Markdown 内容嵌入 HTML，任何浏览器打开即显示。
    """
    import html as html_mod

    safe_title = html_mod.escape(title or "思维导图")
    assets = _get_markmap_assets()
    d3_js = assets.get("d3", "")
    mv_js = assets.get("markmap_view", "")
    ml_js = assets.get("markmap_lib", "")

    # 清洗 Markdown（对标 MindmapView.tsx cleanMindmapContent）
    clean_md = md or ""
    for pattern in [
        (r'```[\s\S]*?```', ''),             # 代码块
        (r'!\[[^\]]*\]\([^)]*\)', ''),       # 图片
        (r'^@\w+\{[\s\S]*?\n\s*\}', ''),     # BibTeX
        (r'\[([^\]]*)\]\(#[^)]*\)', r'\1'),  # 锚点链接 → 纯文本（file:// 安全）
        (r'<[^>]+>', ''),                      # HTML 标签
    ]:
        clean_md = re.sub(pattern[0], pattern[1], clean_md, flags=re.MULTILINE)

    # 转义反引号和反斜杠以便安全嵌入 JS 模板字面量
    clean_md_escaped = clean_md.replace("\\", "\\\\").replace("`", "\\`")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{safe_title}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
    overflow: hidden;
    background: #ffffff;
    color: #1C1C1E;
  }}
  #mindmap {{ width: 100vw; height: 100vh; display: block; min-height: 200px; }}
  /* ── markmap LiMo light overrides ── */
  .markmap {{
    --markmap-font: 300 15px/22px -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
    --markmap-text-color: #1C1C1E;
    --markmap-a-color: #2563EB;
    --markmap-a-hover-color: #1D4ED8;
    --markmap-code-bg: #F0F4FF;
    --markmap-code-color: #1D4ED8;
    --markmap-circle-open-bg: #ffffff;
  }}
  .markmap-node > circle {{ stroke: #3B82F6; stroke-width: 2px; fill: #EFF6FF; }}
  .markmap-link {{ stroke: #D2D2D7; stroke-width: 1.5px; }}
  /* ── Dark mode ── */
  .markmap-dark body {{ background: #1C1C1E; color: #E5E5EA; }}
  .markmap-dark .markmap {{
    --markmap-text-color: #E5E5EA;
    --markmap-code-bg: rgba(59,130,246,0.12);
    --markmap-code-color: #93C5FD;
    --markmap-circle-open-bg: #2C2C2E;
  }}
  .markmap-dark .markmap-node > circle {{ stroke: #60A5FA; fill: rgba(59,130,246,0.15); }}
  .markmap-dark .markmap-link {{ stroke: #48484A; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #1C1C1E; color: #E5E5EA; }}
    .markmap {{
      --markmap-text-color: #E5E5EA;
      --markmap-code-bg: rgba(59,130,246,0.12);
      --markmap-code-color: #93C5FD;
      --markmap-circle-open-bg: #2C2C2E;
    }}
    .markmap-node > circle {{ stroke: #60A5FA; fill: rgba(59,130,246,0.15); }}
    .markmap-link {{ stroke: #48484A; }}
  }}
</style>
</head>
<body>
<svg id="mindmap"></svg>
{f'<script>{d3_js}</script>' if d3_js else '<script src="https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js"></script>'}
{f'<script>{mv_js}</script>' if mv_js else '<script src="https://cdn.jsdelivr.net/npm/markmap-view@0.17.0/dist/browser/index.js"></script>'}
{f'<script>{ml_js}</script>' if ml_js else '<script src="https://cdn.jsdelivr.net/npm/markmap-lib@0.17.0/dist/browser/index.iife.js"></script>'}
<script>
(function() {{
  var mm = window.markmap;
  if (!mm || !mm.Transformer || !mm.Markmap) {{
    document.querySelector('#mindmap').outerHTML = '<p style=\"padding:40px;color:#999;\">markmap JS 加载失败，请联网后刷新页面重试</p>';
    return;
  }}

  var md = `{clean_md_escaped}`;
  var transformer = new mm.Transformer();
  var {{ root }} = transformer.transform(md);
  var svg = document.querySelector('#mindmap');
  var m = mm.Markmap.create(svg, {{ duration: 0 }}, root);
  m.fit();
}})();
</script>
</body>
</html>"""


# ── 自定义导出端点 ──

class ExportCustomRequest(BaseModel):
    doc_ids: list[str] = []
    formats: list[str] = ["md"]          # md, html, plain
    content_tabs: list[str] = ["summary"]  # summary, polished, transcript, ...
    formula_md: str = "keep"             # keep, render, remove
    image_md: str = "separate"           # separate, inline, none
    formula_plain: str = "remove"         # remove, unicode
    html_theme: str = "limo"       # limo 匹配应用内预览
    filename: str = "export"


@router.post("/export/custom")
def export_custom(data: ExportCustomRequest, request: Request, store=Depends(get_store)):
    buf = io.BytesIO()
    total_files = 0

    # 工具函数
    def _remove_formulas(md: str) -> str:
        """移除 LaTeX 公式：块级（可跨行）→ [公式]，行内 → [公式]"""
        # 先处理块级 $$...$$（DOTALL 跨行）
        md = re.sub(r'\$\$[\s\S]*?\$\$', '[公式]', md)
        # 再处理行内 $...$
        md = re.sub(r'\$[^$\n]+?\$', '[公式]', md)
        return md

    def _unicode_formulas(md: str) -> str:
        """LaTeX 公式 → Unicode 近似：块级公式保留内容去 $，行内公式去 $"""
        # $$...$$ → 保留内容，去 $$
        md = re.sub(r'\$\$\s*(.+?)\s*\$\$', r'\1', md, flags=re.DOTALL)
        # $...$ → 去 $（TeX 惯例：$ 前后不邻数字或空白）
        md = re.sub(r'(?<!\d)\$(?=\S)(.+?)(?<=\S)\$(?!\d)', r'\1', md)
        return md

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for doc_id in data.doc_ids:
            tx = store.get_transcript(doc_id)
            if not tx:
                continue
            doc_title = _safe_filename(tx.get("title", doc_id))
            mds = store.get_markdowns_by_transcript(doc_id)

            # 预先收集该文档所有 tab 的内容（用于收集图片引用）
            tab_contents: dict[str, str] = {}
            for tab in data.content_tabs:
                content = ""
                if tab == "summary":
                    for md in mds:
                        if md.get("mode", "") == "notes":
                            content = store.get_markdown(md["id"]) or ""
                            break
                elif tab == "polished":
                    for md in mds:
                        if md.get("mode", "") == "polished":
                            content = store.get_markdown(md["id"]) or ""
                            break
                elif tab == "transcript":
                    content = tx.get("rawText", "")
                    try:
                        parsed = json.loads(content)
                        if isinstance(parsed, dict) and "full_text" in parsed:
                            content = parsed["full_text"]
                        elif isinstance(parsed, dict) and "segments" in parsed:
                            content = "\n\n".join(
                                f"{s.get('start', 0):.0f}s: {s.get('text', '')}"
                                for s in parsed["segments"]
                            )
                    except (json.JSONDecodeError, TypeError):
                        pass
                elif tab == "data_preview":
                    # 原始数据：直接取 transcript rawText（与预览栏 data_preview 一致）
                    content = tx.get("rawText", "")
                elif tab == "report":
                    for md in mds:
                        if md.get("mode", "") == "polished":
                            content = store.get_markdown(md["id"]) or ""
                            break
                elif tab == "fact_check":
                    for md in mds:
                        if md.get("mode", "") == "fact_check":
                            content = store.get_markdown(md["id"]) or ""
                            break
                elif tab == "mindmap":
                    # 取笔记内容作为思维导图源
                    mindmap_src = ""
                    for md in mds:
                        if md.get("mode", "") not in ("polished", "fact_check"):
                            mindmap_src = store.get_markdown(md["id"]) or ""
                            if mindmap_src: break
                    if not mindmap_src:
                        mindmap_src = tx.get("rawText", "")
                    content = _build_mindmap_html(mindmap_src, tx.get("title", doc_title) or doc_title)
                    # mindmap 是完整 HTML，跳过后续 markdown→HTML 转换
                    tab_contents[tab] = content
                    continue
                elif tab == "skeleton":
                    content = tx.get("rawText", "")
                else:
                    results = store.get_process_results(doc_id)
                    for r in results:
                        if r.get("mode") == tab:
                            content = store.get_process_result(r["id"]) or ""
                            break
                    if not content:
                        content = tx.get("rawText", "")
                        try:
                            parsed = json.loads(content)
                            if isinstance(parsed, dict) and "full_text" in parsed:
                                content = parsed["full_text"]
                        except (json.JSONDecodeError, KeyError, TypeError):
                            pass
                if content and content.strip():
                    tab_contents[tab] = content

            # 图片处理：收集该文档所有 tab 的图片引用
            imgs: dict[str, bytes] = {}
            name_map: dict[str, str] = {}
            if data.image_md != "none":
                all_refs: list[tuple[str, str]] = []
                for content in tab_contents.values():
                    all_refs.extend(_find_image_refs(content))
                if all_refs:
                    imgs, name_map = _collect_images(all_refs, store.root)

            # 写入各 tab 内容
            for tab, content in tab_contents.items():
                if tab == "mindmap":
                    # mindmap 已是自包含 HTML，直接写入，不经过 markdown→HTML 转换
                    safe_tab = tab.replace("/", "-")
                    zf.writestr(f"{doc_title}/{doc_title}_{safe_tab}.html", content)
                    total_files += 1
                    continue
                for fmt in data.formats:
                    processed = content
                    ext = fmt

                    # 图片处理
                    if data.image_md == "inline" and imgs:
                        processed = _inline_images(processed, imgs, name_map, store.root)
                    elif data.image_md == "separate" and imgs:
                        processed = _rewrite_image_paths(processed, name_map, prefix="images/")

                    # 公式处理
                    if fmt == "md":
                        if data.formula_md == "render":
                            processed = _render_formulas_to_images(processed)
                        elif data.formula_md == "remove":
                            processed = _remove_formulas(processed)
                    elif fmt == "html":
                        if data.formula_md == "render":
                            processed = _render_formulas_to_images(processed)
                        processed = _build_selfcontained_html(processed, tx.get("title", doc_title), data.html_theme)
                        ext = "html"
                    elif fmt == "plain":
                        processed = _remove_formulas(processed) if data.formula_plain == "remove" else _unicode_formulas(processed)
                        ext = "txt"

                    safe_tab = tab.replace("/", "-")
                    zf.writestr(f"{doc_title}/{doc_title}_{safe_tab}.{ext}", processed)
                    total_files += 1

            # 写入图片文件（separate 模式）
            if data.image_md == "separate" and imgs:
                for img_name, data_bytes in imgs.items():
                    zf.writestr(f"{doc_title}/images/{img_name}", data_bytes)

    buf.seek(0)
    if total_files == 0:
        raise HTTPException(status_code=400, detail="无可导出内容")

    zipname = f"{data.filename}.zip"
    encoded = quote(zipname.encode('utf-8'), safe='')
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"})


# ═══════════════════════════════════════════════════════════════
# 导出进度 SSE 端点 + 临时文件下载
# ═══════════════════════════════════════════════════════════════

_TEMP_DIR = os.path.join(tempfile.gettempdir(), "limo_exports")
_TEMP_TTL = 600  # 10分钟


async def _build_export_zip(
    data: ExportCustomRequest,
    store,
    queue: asyncio.Queue | None = None,
    cancel_event: asyncio.Event | None = None,
) -> tuple[io.BytesIO, int]:
    """构建导出 ZIP，可选通过 queue 推送进度事件。
    返回 (buffer, total_files)。"""
    buf = io.BytesIO()
    total_files = 0

    def _check_cancel():
        if cancel_event and cancel_event.is_set():
            raise asyncio.CancelledError()

    def _push(phase: str, doc: int, total: int, message: str = ""):
        if queue:
            queue.put_nowait({
                "type": "progress", "phase": phase,
                "doc": doc, "total": total, "message": message,
            })

    doc_ids = data.doc_ids
    total_docs = len(doc_ids)

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for idx, doc_id in enumerate(doc_ids):
            _check_cancel()
            _push("collecting", idx + 1, total_docs, f"正在处理文档 {idx + 1}/{total_docs}...")

            tx = store.get_transcript(doc_id)
            if not tx:
                continue
            doc_title = _safe_filename(tx.get("title", doc_id))
            mds = store.get_markdowns_by_transcript(doc_id)

            # 收集 tab 内容
            tab_contents: dict[str, str] = {}
            for tab in data.content_tabs:
                content = _resolve_tab_content(store, tx, mds, tab)
                if content and content.strip():
                    tab_contents[tab] = content

            # 图片
            _check_cancel()
            imgs: dict[str, bytes] = {}
            name_map: dict[str, str] = {}
            if data.image_md != "none":
                all_refs: list[tuple[str, str]] = []
                for content in tab_contents.values():
                    all_refs.extend(_find_image_refs(content))
                if all_refs:
                    _push("images", idx + 1, total_docs, f"正在收集图片 ({len(all_refs)} 引用)...")
                    imgs, name_map = _collect_images(all_refs, store.root)

            # 写入
            _push("zipping", idx + 1, total_docs, f"正在打包...")
            for tab, content in tab_contents.items():
                if tab == "mindmap":
                    safe_tab = tab.replace("/", "-")
                    zf.writestr(f"{doc_title}/{doc_title}_{safe_tab}.html", content)
                    total_files += 1
                    continue
                for fmt in data.formats:
                    processed = content
                    ext = fmt

                    if data.image_md == "inline" and imgs:
                        processed = _inline_images(processed, imgs, name_map, store.root)
                    elif data.image_md == "separate" and imgs:
                        processed = _rewrite_image_paths(processed, name_map, prefix="images/")

                    if fmt == "md":
                        if data.formula_md == "render":
                            processed = _render_formulas_to_images(processed)
                        elif data.formula_md == "remove":
                            processed = _remove_formulas(processed)
                    elif fmt == "html":
                        if data.formula_md == "render":
                            processed = _render_formulas_to_images(processed)
                        processed = _build_selfcontained_html(processed, tx.get("title", doc_title), data.html_theme)
                        ext = "html"
                    elif fmt == "plain":
                        processed = _remove_formulas(processed) if data.formula_plain == "remove" else _unicode_formulas(processed)
                        ext = "txt"

                    safe_tab = tab.replace("/", "-")
                    zf.writestr(f"{doc_title}/{doc_title}_{safe_tab}.{ext}", processed)
                    total_files += 1

            # 图片文件
            if data.image_md == "separate" and imgs:
                _push("images", idx + 1, total_docs, f"正在写入图片...")
                for img_name, data_bytes in imgs.items():
                    zf.writestr(f"{doc_title}/images/{img_name}", data_bytes)

        _push("done", total_docs, total_docs, f"导出完成，共 {total_files} 个文件")

    buf.seek(0)
    return buf, total_files


def _resolve_tab_content(store, tx: dict, mds: list[dict], tab: str) -> str:
    """解析单个 tab 的内容。与 export_custom 内联逻辑一致。"""
    content = ""
    if tab == "summary":
        for md in mds:
            if md.get("mode", "") == "notes":
                content = store.get_markdown(md["id"]) or ""
                break
    elif tab == "polished":
        for md in mds:
            if md.get("mode", "") == "polished":
                content = store.get_markdown(md["id"]) or ""
                break
    elif tab == "transcript":
        content = tx.get("rawText", "")
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict) and "full_text" in parsed:
                content = parsed["full_text"]
            elif isinstance(parsed, dict) and "segments" in parsed:
                content = "\n\n".join(
                    f"{s.get('start', 0):.0f}s: {s.get('text', '')}"
                    for s in parsed["segments"]
                )
        except (json.JSONDecodeError, TypeError):
            pass
    elif tab == "data_preview":
        content = tx.get("rawText", "")
    elif tab == "report":
        for md in mds:
            if md.get("mode", "") == "polished":
                content = store.get_markdown(md["id"]) or ""
                break
    elif tab == "fact_check":
        for md in mds:
            if md.get("mode", "") == "fact_check":
                content = store.get_markdown(md["id"]) or ""
                break
    elif tab == "mindmap":
        mindmap_src = ""
        for md in mds:
            if md.get("mode", "") not in ("polished", "fact_check"):
                mindmap_src = store.get_markdown(md["id"]) or ""
                if mindmap_src: break
        if not mindmap_src:
            mindmap_src = tx.get("rawText", "")
        content = _build_mindmap_html(mindmap_src, tx.get("title", _safe_filename(tx.get("title", "Untitled"))) or "Untitled")
        return content  # mindmap is self-contained, skip further processing
    elif tab == "skeleton":
        content = tx.get("rawText", "")
    else:
        results = store.get_process_results(tx.get("id", ""))
        for r in results:
            if r.get("mode") == tab:
                content = store.get_process_result(r["id"]) or ""
                break
        if not content:
            content = tx.get("rawText", "")
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict) and "full_text" in parsed:
                    content = parsed["full_text"]
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
    return content


@router.post("/export/custom/stream")
async def export_custom_stream(
    data: ExportCustomRequest,
    request: Request,
    store=Depends(get_store),
):
    """SSE 流式导出进度 — 后台构建 ZIP，前端实时看进度，完成后下载"""
    queue: asyncio.Queue = asyncio.Queue()
    cancel_event = asyncio.Event()

    async def event_generator():
        async def run_build():
            try:
                buf, total_files = await _build_export_zip(data, store, queue, cancel_event)
                if cancel_event.is_set():
                    return
                # 保存到临时文件
                os.makedirs(_TEMP_DIR, exist_ok=True)
                temp_id = _uuid.uuid4().hex[:16]
                temp_path = os.path.join(_TEMP_DIR, f"{temp_id}.zip")
                with open(temp_path, "wb") as f:
                    f.write(buf.getvalue())
                queue.put_nowait({
                    "type": "done",
                    "download_url": f"/api/export/download/{temp_id}",
                    "total_files": total_files,
                })
            except asyncio.CancelledError:
                queue.put_nowait({"type": "cancelled"})
            except Exception as e:
                logger.exception("Export stream build failed")
                queue.put_nowait({"type": "error", "message": str(e)[:200]})

        async with disconnect_watcher(request, cancel_event):
            build_task = asyncio.create_task(run_build())
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=30)
                    except asyncio.TimeoutError:
                        yield ": heartbeat\n\n"
                        continue
                    if event.get("type") in ("done", "cancelled", "error"):
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                        break
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            finally:
                if not build_task.done():
                    build_task.cancel()
                    try: await build_task
                    except: pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/export/download/{temp_id}")
async def export_download(temp_id: str):
    """下载预构建的临时 ZIP 文件"""
    temp_path = os.path.join(_TEMP_DIR, f"{temp_id}.zip")
    if not os.path.exists(temp_path):
        raise HTTPException(status_code=404, detail="文件不存在或已过期，请重新导出")

    def cleanup_after():
        try: os.remove(temp_path)
        except OSError: pass

    return StreamingResponse(
        open(temp_path, "rb"),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=export-{temp_id[:8]}.zip"},
        background=cleanup_after,
    )
