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

from urllib.parse import quote
from pathlib import Path
from fastapi import APIRouter, Request, Query, Depends
from fastapi.responses import Response, StreamingResponse
from app.core.dependencies import get_store
from app.core.disconnect import disconnect_watcher
from app.core.errors import AppError
from pydantic import BaseModel

# ── Markdown → HTML 公共渲染（2026-09-21 下沉到 services，Word 导出共用）──
# 私有别名保持零行为变化：历史调用点与内部实现一律走同一个函数。
from app.services.markdown_render import (  # noqa: E402
    PYGMENTS_OK as _PYGMENTS_OK,
    preprocess_gfm as _preprocess_gfm,
    markdown_to_html as _markdown_to_html_impl,
)
from app.services.docx_export import markdown_to_docx  # noqa: E402
from app.services.query import QueryService  # noqa: E402
from app.core.transcript_meta import strip_meta  # noqa: E402
from app.core.markdown_assets import ASSET_REF_RE  # noqa: E402
from app.core.path_guard import resolve_within  # noqa: E402

# ── Pygments 语法高亮（可选依赖）──
try:
    from pygments.formatters import HtmlFormatter
except ImportError:
    HtmlFormatter = None

logger = logging.getLogger(__name__)
router = APIRouter()

# ── KaTeX 服务端渲染（Node.js 子进程）──

_KATEX_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "render_math.js"

# ── LaTeX 分隔符归一化 ──

def _normalize_latex_delimiters(md: str) -> str:
    """将 LaTeX 分隔符归一化为 remark-math 兼容格式。

    remark-math 只识别 $ (inline) 和 $$ (display)，学术论文常用
    \\(...\\) (inline) 和 \\[...\\] (display)。此函数将后者转换为前者。
    保护 fenced code blocks 和 inline code 内的内容不被修改。
    """
    if not md or not isinstance(md, str):
        return md
    # 分割出代码块和普通文本，仅处理普通文本
    parts = re.split(r'(```[\s\S]*?```|`[^`\n]+`)', md)
    result = []
    for idx, part in enumerate(parts):
        if idx % 2 == 1:
            result.append(part)  # 代码块，保留
            continue
        # \\[...\\] → $$...$$
        part = re.sub(
            r'\\\[([\s\S]*?)\\\]',
            lambda m: _fmt_display(m.group(1)),
            part,
        )
        # \\(...\\) → $...$
        part = re.sub(
            r'\\\(([\s\S]*?)\\\)',
            lambda m: f'${m.group(1).strip()}$',
            part,
        )
        # 含 \tag 的内联公式 → 提升为 display（KaTeX 的 \tag 仅 display 支持，
        # inline 渲染会报 "\tag works only in display equations"）
        part = re.sub(
            r'\$([^$\n]+?\\tag\s*\{[^}]*\}[^$\n]*?)\$',
            lambda m: _fmt_display(m.group(1)),
            part,
        )
        result.append(part)
    return ''.join(result)


def _fmt_display(latex: str) -> str:
    """格式化块级公式，多行或长公式加换行"""
    trimmed = latex.strip()
    if '\n' in trimmed or len(trimmed) > 60:
        return f'$$\n{trimmed}\n$$'
    return f'$${trimmed}$$'


def _render_formulas_katex(md: str) -> str:
    """调用 Node.js KaTeX 脚本将公式预渲染为 HTML"""
    md = _normalize_latex_delimiters(md)
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
    """将公式替换为占位符，防止 markdown 转换器破坏公式。
    返回 (protected_md, placeholders) — 调用方需将 placeholders 传给 _restore_latex。"""
    md = _normalize_latex_delimiters(md)
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
    format: str = Query("md", description="导出格式: md / html / docx"),
    mode: str = Query("", description="产物模式: polished / notes / transcript"),
    inline: bool = Query(False, description="内联展示而非下载 —— PDF 导出窗口需渲染此页，见下"),
):
    """导出文档为 MD / HTML / Word"""

    if mode == "transcript":
        tx = store.get_transcript(tx_id)
        if not tx:
            raise AppError("EXPORT_DOC_NOT_FOUND", "文档不存在",
                            status_code=404, recoverable=True)
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
        raise AppError("EXPORT_NO_CONTENT", "无可导出内容",
                        status_code=404, recoverable=True)

    if format == "html":
        # 图片 base64 内嵌 → 自包含单文件 HTML
        refs = _find_image_refs(content)
        if refs:
            doc_dir = str(store.get_doc_dir(tx_id) or '')
            imgs, name_map = _collect_images(refs, store.root, doc_dir=doc_dir)
            if imgs:
                content = _inline_images(content, imgs, name_map, store.root, doc_dir=doc_dir)
        # 用 limo 主题匹配应用内预览样式
        export_theme = request.query_params.get("theme", "limo")
        html = _build_selfcontained_html(content, title, theme=export_theme)
        safe_name = _safe_filename(title)
        encoded_name = quote(f"{safe_name}.html", safe='')
        # ⚠️ disposition 必须是 attachment —— 点击「HTML」就该下载文件。但 PDF 导出
        # （Tauri）是让 WebView2 **直接导航**到这个 URL 再 PrintToPdf，而 attachment
        # 会让它去「下载」而不是渲染 → 打印出一张空白页（实测 876 字节 / 全白）。
        # 故 PDF 路径带 inline=1 换回展示语义；浏览器那条打印路径走 fetch+srcdoc，
        # 不受此头影响，带不带都对。
        disposition = "inline" if inline else "attachment"
        headers = {"Content-Disposition": f"{disposition}; filename*=UTF-8''{encoded_name}"}
        return Response(html, media_type="text/html; charset=utf-8", headers=headers)

    if format == "docx":
        # Word：图片以原始字节内嵌进 docx（走后端收集，不落盘、不 base64）
        imgs: dict[str, bytes] = {}
        refs = _find_image_refs(content)
        if refs:
            doc_dir = str(store.get_doc_dir(tx_id) or '')
            imgs, _name_map = _collect_images(refs, store.root, doc_dir=doc_dir)
        data = markdown_to_docx(content, title, images=imgs)
        safe_name = _safe_filename(title)
        encoded_name = quote(f"{safe_name}.docx", safe='')
        return Response(
            data,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"},
        )

    # MD: 将图片路径重写为绝对 URL，外部查看时不裂图
    base = str(request.base_url).rstrip('/')
    content = re.sub(r'\(assets/', f'({base}/api/assets/{tx_id}/', content)
    content = re.sub(r'\(/api/files/', f'({base}/api/files/', content)
    content = re.sub(r'\(/static/screenshots/', f'({base}/static/screenshots/', content)
    content = re.sub(r'\(/api/assets/', f'({base}/api/assets/', content)
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
        raise AppError("EXPORT_NO_CONTENT", "无可导出内容",
                        status_code=404, recoverable=True)

    tx = store.get_transcript(tx_id)
    title = _safe_filename(tx.get("title", tx_id)) if tx else tx_id

    # 收集所有产物的图片引用 → 合并去重
    all_refs: list[tuple[str, str]] = []
    for content in products.values():
        all_refs.extend(_find_image_refs(content))
    doc_dir = str(store.get_doc_dir(tx_id) or '')
    imgs, name_map = _collect_images(all_refs, store.root, doc_dir=doc_dir)

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
        raise AppError("EXPORT_MISSING_IDS", "缺少 ids 参数或格式错误",
                        status_code=400, recoverable=False)

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
            doc_dir = str(store.get_doc_dir(tid) or '')
            imgs, name_map = _collect_images(all_refs, store.root, doc_dir=doc_dir)

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
    # 优先取笔记（非 polished），与 custom 导出 mindmap tab 一致
    content = ""
    for md in mds:
        if md.get("mode", "") not in ("polished",):
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
        raise AppError("EXPORT_CONV_NOT_FOUND", "对话不存在",
                        status_code=404, recoverable=True)

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
pre { background: #282C34; border-radius: 8px; padding: 16px; color: #ABB2BF; }
code { font-family: "SF Mono", monospace; font-size: 0.9em; }
:not(pre) > code { background: #F0F4FF; padding: 2px 6px; border-radius: 4px; color: #1D4ED8; }""",
    "minimal": """body { font-family: -apple-system, "PingFang SC", sans-serif; max-width: 720px; margin: 80px auto; padding: 0 24px; line-height: 1.8; color: #333; background: #fff; }
h1 { font-weight: 300; font-size: 2.2em; margin-bottom: 0.3em; color: #000; }
h2 { font-weight: 400; font-size: 1.5em; margin-top: 2em; color: #111; }
h3 { font-weight: 500; font-size: 1.1em; color: #333; }
del { text-decoration: line-through; }
table { border-collapse: collapse; width: 100%; margin: 16px 0; }
th, td { border-bottom: 1px solid #e8e8e8; padding: 10px 14px; text-align: left; }
th { font-weight: 500; color: #888; font-size: 0.85em; text-transform: uppercase; }
pre { background: #282C34; border-radius: 4px; padding: 20px; color: #ABB2BF; }
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
pre { background: #282C34; border-radius: 8px; padding: 16px; overflow-x: auto; color: #ABB2BF; }
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
  pre { background: #21252B; color: #ABB2BF; }
  code { color: #ABB2BF; }
  :not(pre) > code { background: rgba(59,130,246,0.12); color: #93C5FD; }
  table th { background: #2C2C2E; }
  table td, table th { border-color: #48484A; }
  img { opacity: 0.85; }
  hr { border-color: #48484A; }
}
@media print {
  /* 打印必须消解上面的暗色段 —— 系统处于暗色偏好时两者会**同时匹配**，
     而这些浅色文字（blockquote #AEAEB2、行内 code #93C5FD）落在白纸上几乎看不见。
     `_PRINT_CSS` 的 !important 只管 body/代码容器这几条通用规则，管不到这里，
     故由引入暗色段的主题**自己**在打印时还原成浅色段取值。 */
  body { color: #1C1C1E; background: #fff; }
  h1 { color: #1E3A5F; }
  h2 { color: #1D4ED8; }
  h3 { color: #2563EB; }
  h4 { color: #3B82F6; }
  strong { color: #1E40AF; }
  a { color: #2563EB; }
  blockquote { background: #EFF6FF; border-left-color: #3B82F6; color: #636366; }
  pre { background: #fff; color: #1F2328; }
  code { color: inherit; }
  :not(pre) > code { background: #F0F4FF; color: #1D4ED8; }
  table th { background: #F5F5F7; }
  table td, table th { border-color: #D2D2D7; }
  img { opacity: 1; }
  hr { border-color: #D2D2D7; }
}""",
}

def _markdown_to_html(md: str) -> str:
    """HTML 导出：保留 Pygments 逐 token 上色（实现已下沉到 services/markdown_render.py，
    Word 导出走同一解析器的 codehilite=False 分支 —— 两条导出路径共用一份 markdown 理解）"""
    return _markdown_to_html_impl(md, codehilite=True)


def _get_pygments_css() -> str:
    """生成 Pygments 代码高亮 CSS。

    屏幕用 one-dark（深底浅字）；**打印另配一套浅底配色**——浏览器打印默认不打印
    背景色，深底一消失，one-dark 的浅色 token（注释 #7F848E 尤其）就落在白纸上，
    对比度低到几乎看不见。容器背景改白还不够：token 是 `.codehilite .k` 这类
    类选择器，改 `pre`/`code` 的 color 压不住它们，必须是**整份浅底主题的 defs**。

    两套规则同特异性 → `@media print` 块在后，打印时后者胜出，屏幕样式零影响。
    """
    if not _PYGMENTS_OK:
        return ""
    try:
        formatter = HtmlFormatter(style="one-dark", noclasses=False)
        css = formatter.get_style_defs('.codehilite')
        # 追加代码块容器样式（暗色）和语言标签
        css += """
.codehilite { background: #282C34; border-radius: 8px; padding: 16px; overflow-x: auto; margin: 16px 0; }
.codehilite .lang-label { display: block; font-size: 0.75em; color: #ABB2BF; margin-bottom: 8px; font-weight: 600; }
.codehilite pre { background: #282C34; }
"""
        # 打印：换成浅底主题（Pygments `default`），并把容器底改白
        light = HtmlFormatter(style="default", noclasses=False).get_style_defs('.codehilite')
        css += f"""
@media print {{
{light}
.codehilite, .codehilite pre {{ background: #fff !important; }}
.codehilite .lang-label {{ color: #57606A !important; }}
}}
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


# ── 图片收集/重写（导出自包含核心）──

# 新格式：assets/xxx（磁盘相对路径）—— 正则来自 `core/markdown_assets`（P2-18 收敛）。
# ⚠️ 必须是**同一个编译对象**：下方 `_collect_image_data` 用 `m.re is ASSET_REF_RE`
# 判断「这次匹配来自哪个模式」，各自 compile 时两个对象永不相等、判断恒假。
# 旧格式兼容
_IMG_RE = re.compile(r'!\[([^\]]*)\]\(/api/files/([a-zA-Z0-9_\-]+)/([a-zA-Z0-9_\-.]{1,}[^\s)]*)\)')
_SS_RE  = re.compile(r'!\[([^\]]*)\]\(/static/screenshots/([a-zA-Z0-9_\-.]{1,}[^\s)]*)\)')
# ⚠️ 与 `markdown_assets.unrewrite_asset_urls` 形态相似但**不同源**：那条要匹配
# **指定 tx_id** 的引用（编辑保存只反转本文档），这条要**提取** tx_id（导出可能跨文档）
_API_ASSET_RE = re.compile(r'!\[([^\]]*)\]\(/api/assets/([a-zA-Z0-9_\-]+)/([a-zA-Z0-9_\-.]{1,}[^\s)]*)\)')


def _find_image_refs(text: str) -> list[tuple[str, str, str]]:
    seen: set[str] = set()
    refs: list[tuple[str, str, str]] = []
    for m in ASSET_REF_RE.finditer(text):
        full = m.group(0)
        if full not in seen:
            seen.add(full)
            filename = m.group(2).split("/", 1)[1] if "/" in m.group(2) else m.group(2)
            refs.append((filename, m.group(2), "assets"))
    for m in _IMG_RE.finditer(text):
        full = m.group(0)
        if full not in seen:
            seen.add(full)
            refs.append((m.group(3), m.group(2), "files"))
    for m in _SS_RE.finditer(text):
        full = m.group(0)
        if full not in seen:
            seen.add(full)
            refs.append((m.group(2), "", "screenshots"))
    for m in _API_ASSET_RE.finditer(text):
        full = m.group(0)
        if full not in seen:
            seen.add(full)
            refs.append((m.group(3), m.group(2), "api_assets"))
    return refs


def _collect_images(refs: list[tuple[str, str, str]], store_root: str,
                    doc_dir: str = ""
                     ) -> tuple[dict[str, bytes], dict[str, str]]:
    image_data: dict[str, bytes] = {}
    name_map: dict[str, str] = {}
    seen_names: set[str] = set()

    for filename, arg2, source in refs:
        # ⚠️ 四个分支**全都**要过守卫：四份正则的字符集都允许 `..`——`ASSET_REF_RE`
        # 那份最明显，而 `_IMG_RE` / `_SS_RE` / `_API_ASSET_RE` 的末段是 `[^\s)]*`，
        # 同样能吃下 `../../config.yaml`。越界即**跳过该条目**（不读、不写）。
        if source == "assets" and doc_dir:
            src = resolve_within(doc_dir, arg2)
        elif source == "screenshots":
            src = resolve_within(store_root, f"screenshots/{filename}")
        elif source == "api_assets" and doc_dir:
            src = resolve_within(doc_dir, f"assets/{filename}")
        else:
            src = resolve_within(store_root, f"files/{arg2}/{filename}")
        if src is None:
            logger.warning("导出：图片引用越界，已跳过 source=%s ref=%s", source, filename)
            continue
        if not src.exists():
            logger.warning("导出图片缺失: %s", src)
            continue
        if filename in name_map:
            continue
        # ⚠️ 条目名只取 basename：`filename` 可以合法地含子路径与 `..`
        # （`assets/sub/../img.png` 解析后仍在 doc_dir 内，守卫会放行），原样写进
        # `{doc}/images/` 就是 zip-slip。导出物的图片本就是扁平目录，取 basename 不丢信息。
        final_name = Path(filename).name or "image"
        parts = final_name.rsplit(".", 1)
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
    def _replace_asset(m: re.Match) -> str:
        alt = m.group(1)
        asset_path = m.group(2)
        filename = asset_path.split("/", 1)[1] if "/" in asset_path else asset_path
        return f"![{alt}]({prefix}{name_map.get(filename, filename)})"
    def _replace_img(m: re.Match) -> str:
        return f"![{m.group(1)}]({prefix}{name_map.get(m.group(3), m.group(3))})"
    def _replace_ss(m: re.Match) -> str:
        return f"![{m.group(1)}]({prefix}{name_map.get(m.group(2), m.group(2))})"
    def _replace_api(m: re.Match) -> str:
        return f"![{m.group(1)}]({prefix}{name_map.get(m.group(3), m.group(3))})"
    text = ASSET_REF_RE.sub(_replace_asset, text)
    text = _IMG_RE.sub(_replace_img, text)
    text = _SS_RE.sub(_replace_ss, text)
    text = _API_ASSET_RE.sub(_replace_api, text)
    return text


def _inline_images(text: str, image_data: dict[str, bytes],
                   name_map: dict[str, str], store_root: str,
                   doc_dir: str = "") -> str:
    def _data_uri(data: bytes, filename: str) -> str:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
        mime_map = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png",
                    "gif": "gif", "webp": "webp", "svg": "svg+xml"}
        import base64
        b64 = base64.b64encode(data).decode()
        return f"data:image/{mime_map.get(ext, 'png')};base64,{b64}"
    def _read(src: Path | None) -> bytes | None:
        # ⚠️ 与 `_collect_images` **同一守卫**（成对：收集侧跳过越界，内联侧也不能读——
        # 少做一半等于完全没做，模式 #52）。传入 None 表示守卫已判越界。
        if src is None or not src.exists():
            return None
        return src.read_bytes()
    def _replace(m: re.Match) -> str:
        alt = m.group(1)
        if m.re is ASSET_REF_RE:
            filename = m.group(2).split("/", 1)[1]
            fn = name_map.get(filename, filename)
            data = image_data.get(fn)
            if not data and doc_dir:
                data = _read(resolve_within(doc_dir, m.group(2)))
        elif m.re is _SS_RE:
            filename = m.group(2)
            fn = name_map.get(filename, filename)
            data = image_data.get(fn)
            if not data:
                data = _read(resolve_within(store_root, f"screenshots/{filename}"))
        elif m.re is _API_ASSET_RE:
            filename = m.group(3)
            fn = name_map.get(filename, filename)
            data = image_data.get(fn)
            if not data and doc_dir:
                data = _read(resolve_within(doc_dir, f"assets/{filename}"))
        else:
            filename = m.group(3)
            fn = name_map.get(filename, filename)
            data = image_data.get(fn)
            if not data:
                data = _read(resolve_within(store_root, f"files/{m.group(2)}/{filename}"))
        if data:
            return f"![{alt}]({_data_uri(data, filename)})"
        return m.group(0)
    text = ASSET_REF_RE.sub(_replace, text)
    text = _IMG_RE.sub(_replace, text)
    text = _SS_RE.sub(_replace, text)
    text = _API_ASSET_RE.sub(_replace, text)
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

# 打印样式（4 套主题共用）——导出的 HTML 用浏览器「打印 / 另存为 PDF」时生效。
# 现有主题清一色是「屏幕布局」，直接打印有三处不适配：
#   1. body 的 max-width + margin:auto 在纸上变成一圈古怪的窄边距；
#   2. 长表格/代码块/图片会从中间被切断；
#   3. ⚠️ 代码块是「浅字 + 深底」(one-dark #ABB2BF on #282C34)，而**浏览器打印默认
#      不打印背景色** —— 不印背景就成浅灰字白纸，几乎不可读。
# 故打印媒体下强制改「浅底 + 深字」。
#
# ⚠️ 代码块的**逐 token 配色**不在这里管 —— token 是 `.codehilite .k` 这类类选择器，
# 靠 `pre`/`code` 的 color 压不住，必须在 _get_pygments_css() 里换一整套浅底主题。
# 本文件负责的是容器（背景/边框）与版式。
#
# 插入位置在 style_block 末尾 —— 同特异性下后者胜，可覆盖主题与 katex 的屏幕样式
# （本文件规则带 !important，也能压过 limo 主题的 prefers-color-scheme: dark 段）。
_PRINT_CSS = """@page { size: A4; margin: 18mm 16mm; }
@media print {
  /* 解除屏幕布局；!important 同时覆盖 limo 主题的暗色媒体查询 */
  body { max-width: none !important; margin: 0 !important; padding: 0 !important;
         background: #fff !important; color: #000 !important; }
  /* 代码容器改浅底深字（token 配色见 _get_pygments_css 的 @media print 段） */
  .codehilite, .codehilite pre, pre { background: #fff !important;
    border: 1px solid #D0D7DE !important; color: #1F2328 !important; }
  .codehilite code, pre code { color: #1F2328 !important; }
  /* 分页控制：标题不与后续正文分离 */
  h1, h2, h3, h4, h5, h6 { break-after: avoid; page-break-after: avoid; }
  /* ⚠️ pre / table **不能**加 break-inside:avoid —— 它们的长度无上界，一个 45 行的
     代码块或长表格一旦装不进当前页剩余空间，就会被整体推到下页，留下一整页空白
     （实测：3 页文档中间多出一页纯空白）。让它们自然跨页，只保证行/项级不断裂。 */
  blockquote, figure, img, li, tr { break-inside: avoid; page-break-inside: avoid; }
  thead { display: table-header-group; }  /* 长表格跨页时表头在每页重复 */
  tfoot { display: table-footer-group; }
  img { max-width: 100% !important; }
  a { color: inherit !important; text-decoration: underline; }
}"""


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
    # 打印样式放最末 —— 同特异性下后者胜
    style_block += f"\n<style>{_PRINT_CSS}</style>"

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

    ⚠️ **必须在这里 `strip_meta`**（思维导图**不承载**产物自证头）：
    下游清洗规则里有 `r'<[^>]+>'`（去 HTML 标签），它**按第一个 `>` 断句**——
    meta 的 JSON 里只要有一个字符串值含 `>`（最真实的一例是用户自定义维度
    `成本 > 收益`，或意图文本里写 `A > B`），注释就被从中间截断，剩下的
    ` 收益"]},"model":"m"}...` 会作为**可见文本**混进脑图节点（实测确认）。

    ⚠️ **不能在调用点剥**：本函数的输入有两个来源（`store.get_markdown` 取产物、
    以及 `rawText` 兜底），而合成文档的 `rawText` **必须**带头（三位一体）——
    在调用点剥要么漏掉一路，要么把 rawText 的语义改坏。剥头与「不承载自证」
    是同一件事的两面，故收口在这里。
    """
    import html as html_mod

    safe_title = html_mod.escape(title or "思维导图")
    md = strip_meta(md or "")
    md = _normalize_latex_delimiters(md)
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
    formats: list[str] = ["md"]          # md, html, plain, docx
    content_tabs: list[str] = ["summary"]  # summary, polished, transcript, ...
    formula_md: str = "keep"             # keep, remove（docx 恒保留 LaTeX 原文，见下）
    image_md: str = "separate"           # separate, inline, none
    formula_plain: str = "remove"         # remove, unicode
    html_theme: str = "limo"       # limo 匹配应用内预览
    filename: str = "export"


@router.post("/export/custom")
def export_custom(data: ExportCustomRequest, request: Request, store=Depends(get_store)):
    buf = io.BytesIO()
    total_files = 0

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
                elif tab == "mindmap":
                    # 取笔记内容作为思维导图源
                    mindmap_src = ""
                    for md in mds:
                        if md.get("mode", "") not in ("polished",):
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
                    doc_dir = str(store.get_doc_dir(doc_id) or '')
                    imgs, name_map = _collect_images(all_refs, store.root, doc_dir=doc_dir)

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
                        if data.formula_md == "remove":
                            processed = _remove_formulas(processed)
                    elif fmt == "html":
                        processed = _build_selfcontained_html(processed, tx.get("title", doc_title), data.html_theme)
                        ext = "html"
                    elif fmt == "plain":
                        processed = _remove_formulas(processed) if data.formula_plain == "remove" else _unicode_formulas(processed)
                        ext = "txt"
                    elif fmt == "docx":
                        # Word：用**原始 content**（非 processed）——image_md=inline 已把图片
                        # 换成 base64 data URI，那条路是给自包含 HTML 用的；docx 只要原始
                        # `![alt](assets/x.png)` + 收集好的 imgs 字典即可内嵌。
                        # 公式恒保留 LaTeX 原文（md/html 侧的 render 路径已移除，见 CHANGELOG）。
                        processed = markdown_to_docx(content, tx.get("title", doc_title), images=imgs)
                        ext = "docx"

                    safe_tab = tab.replace("/", "-")
                    zf.writestr(f"{doc_title}/{doc_title}_{safe_tab}.{ext}", processed)
                    total_files += 1

            # 写入图片文件（separate 模式）
            if data.image_md == "separate" and imgs:
                for img_name, data_bytes in imgs.items():
                    zf.writestr(f"{doc_title}/images/{img_name}", data_bytes)

    buf.seek(0)
    if total_files == 0:
        raise AppError("EXPORT_NO_CONTENT", "无可导出内容",
                        status_code=400, recoverable=True)

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
                    doc_dir = str(store.get_doc_dir(doc_id) or '')
                    imgs, name_map = _collect_images(all_refs, store.root, doc_dir=doc_dir)

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
                        if data.formula_md == "remove":
                            processed = _remove_formulas(processed)
                    elif fmt == "html":
                        processed = _build_selfcontained_html(processed, tx.get("title", doc_title), data.html_theme)
                        ext = "html"
                    elif fmt == "plain":
                        processed = _remove_formulas(processed) if data.formula_plain == "remove" else _unicode_formulas(processed)
                        ext = "txt"
                    elif fmt == "docx":
                        # Word：用**原始 content**（非 processed）——image_md=inline 已把图片
                        # 换成 base64 data URI，那条路是给自包含 HTML 用的；docx 只要原始
                        # `![alt](assets/x.png)` + 收集好的 imgs 字典即可内嵌。
                        # 公式恒保留 LaTeX 原文（md/html 侧的 render 路径已移除，见 CHANGELOG）。
                        processed = markdown_to_docx(content, tx.get("title", doc_title), images=imgs)
                        ext = "docx"

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


def _pick_artifact(store, tx: dict, mds: list[dict], mode: str) -> str:
    """取某 mode 的产物原文（**含自证头**）。

    走 `QueryService.build_from_entries` 而不是 `store.get_markdown(id)`：两者都读盘，
    但前者复用**产物内容怎么取**的那条判据（剥头 / 自证装配 / 版本选择），导出侧才不会
    与 CLI、预览各自长出一套。就地构造 `QueryService` 是因为本模块不走 `Depends` 风格
    （全程只有 `store` 一个原语）——构造只存一个引用，零 I/O。

    `max_chars=None` 是**必须显式写出来**的：导出要完整产物，而 `get_artifact` 系默认
    带截断上限（那是给终端/agent 的防爆护栏）。此处漏写会静默导出半篇文章。

    用 `raw_content`（不剥自证头）：产物离开 `.limo` 之后靠那一行自己说明来历
    （《产物复用与获取渠道约定》§3）——导出正是它最该生效的场景。
    """
    entries = [md for md in mds if md.get("mode", "") == mode]
    if not entries:
        return ""
    return QueryService(store).build_from_entries(
        tx.get("id", ""), entries, max_chars=None).raw_content


def _resolve_tab_content(store, tx: dict, mds: list[dict], tab: str) -> str:
    """解析单个 tab 的内容。与 export_custom 内联逻辑一致。"""
    content = ""
    if tab == "summary":
        content = _pick_artifact(store, tx, mds, "notes")
    elif tab == "polished":
        content = _pick_artifact(store, tx, mds, "polished")
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
        content = _pick_artifact(store, tx, mds, "polished")
    elif tab == "mindmap":
        mindmap_src = ""
        for md in mds:
            if md.get("mode", "") not in ("polished",):
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
                # 清理过期临时 ZIP（_TEMP_TTL 此前从未使用 → 未下载/取消的 ZIP 永久残留）
                try:
                    _now_ts = datetime.now().timestamp()
                    for _old in Path(_TEMP_DIR).glob("*.zip"):
                        try:
                            if _now_ts - _old.stat().st_mtime > _TEMP_TTL:
                                _old.unlink()
                        except OSError:
                            pass
                except OSError:
                    pass
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
    # ⚠️ temp_id 直接拼路径：先校验字符集，再 resolve 后限定在 _TEMP_DIR 内，
    # 否则构造 ..%2F 可路径穿越读取/删除 _TEMP_DIR 外任意 .zip
    if not re.match(r'^[a-zA-Z0-9_-]+$', temp_id):
        raise AppError("EXPORT_TEMP_EXPIRED", "文件不存在或已过期，请重新导出",
                        status_code=404, recoverable=True)
    temp_path = os.path.join(_TEMP_DIR, f"{temp_id}.zip")
    try:
        Path(temp_path).resolve().relative_to(Path(_TEMP_DIR).resolve())
    except ValueError:
        raise AppError("EXPORT_TEMP_EXPIRED", "文件不存在或已过期，请重新导出",
                        status_code=404, recoverable=True)
    if not os.path.exists(temp_path):
        raise AppError("EXPORT_TEMP_EXPIRED", "文件不存在或已过期，请重新导出",
                        status_code=404, recoverable=True)

    def cleanup_after():
        try: os.remove(temp_path)
        except OSError: pass

    return StreamingResponse(
        open(temp_path, "rb"),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=export-{temp_id[:8]}.zip"},
        background=cleanup_after,
    )
