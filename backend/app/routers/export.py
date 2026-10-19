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
import sys
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
# H2 → 卡片切分。⚠️ 与前端 `lib/markdown-sections.ts::splitByH2` **同形**，
# 由共享 fixture（`frontend/src/test/fixtures/h2-sections.json`）钉住 —— 预览与
# 导出必须切出同一批卡片，否则就是「导出与预览不一致」这个核心约束的直接违反。
from app.services.markdown_sections import split_by_h2  # noqa: E402
# 产物正文的语义标记（`limo-block`）。⚠️ 与前端 `lib/markdown-markers.ts::parseMarkers`
# **同形**，由共享 fixture（`frontend/src/test/fixtures/limo-blocks.json`）钉住 ——
# 「预览里是强调块、导出里是一行注释文字」正是这条链最容易出的岔子。
from app.core.artifact_blocks import (  # noqa: E402
    has_blocks as _has_blocks,
    linearize_evidence as _linearize_evidence,
    render_blocks_html as _render_blocks_html,
    strip_blocks as _strip_blocks,
)
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
    version: int = Query(0, description="产物版本号；0 = 最新版。⚠️ 仅对 notes/polished "
                                        "生效——转录原文（transcript）只有一个版本，传了会被忽略"),
    image_md: str = Query("link", description="**仅 format=md** 时生效：`link`（默认）= 图片"
                                             "引用改写成绝对 URL（外部查看需后端在跑）；"
                                             "`inline` = 图片以 base64 内嵌进 .md，单文件自包含"
                                             "（快捷「下载 MD」在设置里开启「把图片一起带走」时走这条）"),
):
    """导出文档为 MD / HTML / Word

    `version` 支撑「**所见即所得**」：用户在预览区切到 v1 再导出，拿到的必须是 v1。
    显式版本查不到时**显式报错**（`EXPORT_VERSION_NOT_FOUND`）而不回退到最新版 ——
    「我要第 1 版」导出第 2 版是最坏的一种成功：文件已经落盘，用户不会发现拿错。
    """

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
        # `version=0` → 传 None = 不过滤（取最新，列表已按版本降序）
        mds = store.get_markdowns_by_transcript(tx_id, mode=filter_mode,
                                                version=version or None)
        # ⚠️ 显式指定版本却查不到 → **不回退**。「我要导第 1 版」导出第 2 版是最坏的
        # 一种成功（模式同 `QueryService.get_artifact`：不静默回退最新版）。
        # ⚠️ `recoverable=False` 与 `status_code=404` 两个都要显式给：前者默认 True
        # 会把响应压成 **HTTP 200**（模式 #36），前端 `!r.ok` 于是否掉失败、照样去
        # 解那个失败体。这里与同文件的 `EXPORT_NO_CONTENT` 刻意**不同**：那边
        # 「没内容」可能是「还没生成完」，重试有意义；「版本 99 不存在」重试一万次
        # 也不会出现，是可恢复性上的另一类错误。
        if version and not mds:
            raise AppError("EXPORT_VERSION_NOT_FOUND", f"版本 {version} 不存在",
                           status_code=404, recoverable=False)
        # mode 已显式指定但未找到 → 不回退到其他 mode 的内容（预览一致原则）
        # 仅 mode 为空（用户未指定）时，无 notes 才回退到任意可用产物。
        # ⚠️ 指定了 version 时**禁掉这条回退**：它会把「notes 的第 2 版」悄悄换成
        # 「polished 的第 2 版」，而用户要的是 notes。
        if not mds and not mode and not version:
            mds = store.get_markdowns_by_transcript(tx_id, mode="")
        content = mds[0]["content"] if mds else ""
        # 回退：无 markdown 条目时尝试 process results
        # 图片文档（sourceType=image）没有 markdown 条目，处理结果存在 process_results 中
        # mode 如 extract/table/digitize/understand 对应图片处理的各种模式
        # ⚠️ 同理：指定 version 时不走这条（图片处理产物**同 mode 覆盖、无版本概念**，
        # 拿一份没有版本概念的产物去满足「要第 1 版」是答非所问）。
        if not content and mode and not version:
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

    # 证据块的导出线性化（2026-10-18，用户拍板「所有格式统一，含 Markdown」）：
    # 预览里证据是可展开的 `<details>`，而任何导出格式都没有交互 —— 原位换成
    # `[证据 N]`、内容按序搬到文末 `## 证据附录`，于是「按需展开」降级成
    # 「原位占位 + 文末附录」，任何格式都读得到依据。
    # ⚠️ **必须排在 `_strip_blocks` 与 `render_blocks_html` 之前**：反过来的话围栏
    # 已被剥掉，附录永远不生成 —— 而两条链都「跑成功了」，零报错。
    # ⚠️ 无证据块时原样返回 `content`（连 CRLF 都不动）⇒ 逐字节零回归。
    # ⚠️ 只消解 evidence，其余块仍原样交给下面各自的渲染路径。
    content = _linearize_evidence(content)

    # 骨架轴 / 卡片视觉方案 —— **HTML 与 Word 两条链共用**，故读在分支之前。
    # ⚠️ 默认值与前端 `CARD_DEFAULTS` **必须同值**（`paper` / `gpt`）：任何没带参数的
    # 调用方（历史前端、第三方脚本、PDF 导出链）拿到的都必须是今天这份产物。
    # 取值合法性由 `_build_selfcontained_html` 自己兜（它只认 `"card"`，其余当 `paper`）。
    # md / plain 没有视觉层，读到也不消费 —— 刻意不在这里报错。
    skeleton = request.query_params.get("skeleton", "paper")
    card_style = request.query_params.get("card_style", "gpt")
    # 逐维度微调（2026-10-11）—— 前端传的是**解析好的 CSS 值**（`--card-lift:40%`），
    # 合法性由 `_parse_card_vars` 的字符白名单兜底，不在这里校验。默认空串 ⇒
    # 未微调的用户走今天这条逐字未改的分支。
    card_vars = request.query_params.get("card_vars", "")
    # 语义标记的装饰开关 —— ⚠️ 默认 `True`（**与前端 `MARKER_DEFAULTS` 同值**）：
    # 任何没带参数的调用方必须拿到「装饰开着」这份产物，与预览里看到的一致。
    # 只有显式的 `marker_decor=0` 才关 —— 认不认识的值一律当「开」（同骨架取值兜底）。
    marker_decor = request.query_params.get("marker_decor", "1") != "0"
    # 主题跟随（2026-10-16）—— 六项全部**默认空串**：一个都不带的调用方（历史前端、
    # 第三方脚本、PDF 导出链）拿到的是今天的固定配色，逐字节零回归。
    # ⚠️ 这里的职责只有「搬运」：合法性由 `_build_selfcontained_html` 的字符白名单兜
    # （非法值静默丢弃那一项），**刻意不在这里校验、也不维护 key 列表**（第二份真相源）。
    # 明暗传的是**解析后**的值（前端取 `resolvedTheme`）—— 导出页没有 `matchMedia`
    # 之外的判断依据，`data-theme="system"` 是一条谁都不认识的死属性。
    theme_mode = request.query_params.get("theme_mode", "")
    accent = request.query_params.get("accent", "")
    base = request.query_params.get("base", "")
    font = request.query_params.get("font", "")
    texture = request.query_params.get("texture", "")
    texture_opacity = request.query_params.get("texture_opacity", "")

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
        html = _build_selfcontained_html(content, title, theme=export_theme,
                                         skeleton=skeleton, card_style=card_style,
                                         marker_decor=marker_decor, card_vars=card_vars,
                                         theme_mode=theme_mode, accent=accent, base=base,
                                         font=font, texture=texture,
                                         texture_opacity=texture_opacity)
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
        data = markdown_to_docx(content, title, images=imgs, skeleton=skeleton)
        safe_name = _safe_filename(title)
        encoded_name = quote(f"{safe_name}.docx", safe='')
        return Response(
            data,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"},
        )

    # MD: 将图片路径重写为绝对 URL，外部查看时不裂图
    # ⚠️ **先剥语义标记**（用户拍板：导出的文件应当是干净正文）。折叠进数据层的标记是
    # 给渲染器看的协议元素，落到 `.md` 文件里就是读者可见的一行注释 —— 这里剥、数据层
    # 不剥（`query.py` 读路径返回原文，预览要靠它才能渲染出强调块）。
    content = _strip_blocks(content)
    # 图片内嵌（`image_md=inline`，2026-10-11）—— 快捷「下载 MD」在设置里开了
    # 「Markdown 导出时把图片一起带走」时走这条。动机：MD 从前只把引用改写成指向
    # 本机后端的绝对 URL，文件一离开应用（关掉后端、转发给别人）就整篇裂图。
    # ⚠️ 与 HTML / docx 两条分支**同一套**收集与内联函数（`_collect_images` /
    # `_inline_images`），故「哪些引用算什么图」「越界怎么处置」两边天然同源，不产生
    # 第二份判据（模式 #54）。⚠️ `_inline_images` 对**没找到数据的引用原样保留**，
    # 正好由下面那四条绝对 URL 改写兜底 —— 顺序不能颠倒（先内联、后改写）。
    if image_md == "inline":
        refs = _find_image_refs(content)
        if refs:
            doc_dir = str(store.get_doc_dir(tx_id) or '')
            imgs, name_map = _collect_images(refs, store.root, doc_dir=doc_dir)
            if imgs:
                content = _inline_images(content, imgs, name_map, store.root, doc_dir=doc_dir)
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
        # ⚠️ 必须剥 `limo-block` 标记 —— 与定制导出（`_build_export_zip` 的 md/plain
        # 分支）**同源**。`get_markdowns_by_transcript` 给的是磁盘原文（含标记行），
        # 不剥的话「一键导出所有产物」的 md 里留着 `<!-- limo-block:v1 … -->`，
        # 而「按设置导出」的 md 是干净的 —— 同一条「预览所见 = 导出所得」的约束
        # 在两条链上给出相反结果（2026-10-11 实跑查出）。
        # ⚠️ **先线性化、后剥标记**（顺序不能反，见 `linearize_evidence` 的 docstring）：
        # 证据块要变成 `[证据 N]` + 文末附录，其余标记照旧剥掉。
        # ⚠️ 只处理**笔记产物**这一行 —— 上面的转录原文 / 下面的图片处理结果不是产物，
        # 它们本来就不该有证据块，一并不动（少一处就少一次误伤）。
        products[f"{title}-{mode_label}{ver}.md"] = _strip_blocks(
            _linearize_evidence(md["content"])
        )

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
    total_files = 0
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
                total_files += 1
            # ⚠️ 循环变量**不叫 `data`**：本函数有个同名入参（`data: dict`），
            # 遮蔽它虽在当下无害，却是「改天有人在循环后接着用入参」的现成陷阱。
            for img_name, img_bytes in imgs.items():
                zf.writestr(f"{folder}/images/{img_name}", img_bytes)
                total_files += 1
    buf.seek(0)

    # ⚠️ 空结果必须**显式报错**，不能发一个空 ZIP —— 与 `/export/{id}/all`(404)
    # 和 `/export/custom`(400) 统一。这里是 P1-11 的**第三处**，此前只修了前两处，
    # 而 `_build_export_zip` 的注释当时就写着「已统一」，看起来像是查过了。
    # 只 `continue` 的后果：选中一批已删除/无效的文档 → 用户下载到 22 字节空 zip，
    # toast 却报「已导出」；**同一操作只选 1 篇**走 `/all` 反而报错
    # —— 「1 篇报错、2 篇静默成功」正是判据不同源最典型的形态（2026-10-11 实跑查出）。
    if total_files == 0:
        raise AppError("EXPORT_NO_CONTENT", "无可导出内容",
                       status_code=404, recoverable=True)

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
        # ⚠️ **JSON 刻意保留原文（含 `limo-block` 标记）** —— 与下面的 Markdown 分支
        # **非对称，这是有意的**：JSON 是数据记录 / 可回灌格式，剥掉标记会破坏保真
        # （再导入时卡片结构就没了）；而 Markdown 是给人读的成品，协议标记不该出现在
        # 读者的文件里。改动这一条前先想清楚「谁在消费这个格式」。
        return Response(
            json.dumps(conv, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(f'{title}.json'.encode('utf-8'), safe='')}"},
        )

    # Markdown 格式
    # ⚠️ **必须剥掉 `limo-block` 标记行**：它是**渲染协议**，不是内容。不剥的话导出的
    # .md 里会留着 `<!-- limo-block:v1 type="card-group" -->` —— **预览所见 ≠ 导出所得，
    # 且零报错**（没有人会因此收到异常，只会拿到一份脏文件）。
    # 与前端复制按钮的 `stripMarkers` 同纪律，只是前后端各一份实现（跨语言必然如此）。
    lines = [f"# {conv.get('title', '对话')}", f"\n导出日期: {date}\n"]
    for m in conv.get("messages", []):
        ts = m.get("timestamp", "")[:16]
        if m["role"] == "user":
            lines.append(f"**用户** ({ts})\n\n{_strip_blocks(m['content'])}\n")
        elif m["role"] == "assistant":
            lines.append(f"**AI** ({ts})\n\n{_strip_blocks(m['content'])}\n")
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
                # ⚠️ 同单条导出：.md 剥标记、.json 保留 —— 两处都要改，
                # 只改一处的话「导出单条干净、导出批量脏」而两边都不会报错。
                lines.append(f"**{role}**{ts_str}\n\n{_strip_blocks(m['content'])}\n")
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
    "limo": """body { font-family: var(--font-reading, -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif); max-width: 860px; margin: 40px auto; padding: 0 24px; line-height: 1.75; color: var(--tk-foreground, #1C1C1E); background-color: var(--texture-bg-color, var(--tk-surface, #fff)); background-image: var(--texture-bg-image); background-blend-mode: var(--texture-blend); }
html[data-texture]:not([data-texture="plain"]) body::before { content: ''; position: fixed; inset: 0; z-index: -1; pointer-events: none; background: var(--tk-surface, #fff); opacity: var(--texture-opacity, 0); }
h1 { font-size: 2em; color: var(--md-h1, #1E3A5F); border-bottom: 1px solid var(--tk-border, #d0d7de); padding-bottom: .3em; }
h2 { font-size: 1.5em; color: var(--md-h2, #1D4ED8); border-bottom: 1px solid var(--tk-border, #d0d7de); padding-bottom: .3em; margin-top: 24px; }
h3 { font-size: 1.25em; color: var(--md-h3, #2563EB); margin-top: 24px; }
h4 { font-size: 1em; color: var(--md-h4, #3B82F6); margin-top: 24px; }
strong { color: var(--md-strong, #1E40AF); }
a { color: var(--md-link, #2563EB); }
del { text-decoration: line-through; }
blockquote { border-left: 4px solid var(--md-blockquote-border, #3B82F6); background-color: var(--md-blockquote-bg, #EFF6FF); margin: 0; padding: 0 1em; color: var(--tk-dimmed, #636366); border-radius: 0 8px 8px 0; }
pre { background: #282C34; border-radius: 8px; padding: 16px; overflow-x: auto; color: #ABB2BF; }
code { font-family: "SF Mono", "Fira Code", monospace; font-size: 0.9em; }
:not(pre) > code { background-color: var(--md-code-bg, #F0F4FF); color: var(--md-code-color, #1D4ED8); padding: 2px 6px; border-radius: 4px; }
table { border-collapse: collapse; width: 100%; margin: 16px 0; }
th, td { border: 1px solid var(--md-table-border, #D2D2D7); padding: 8px 12px; text-align: left; font-size: 0.95em; }
th { background-color: var(--md-table-header-bg, #F5F5F7); font-weight: 600; }
img { max-width: 100%; border-radius: 8px; }
hr { border-color: var(--md-hr, #D2D2D7); }
@media screen and (prefers-color-scheme: dark) {
  html:not([data-theme]) { --tk-foreground: #E5E5EA; --tk-surface: #1C1C1E; --tk-dimmed: #AEAEB2; --tk-border: #48484A; --md-h1: #93C5FD; --md-h2: #60A5FA; --md-h3: #60A5FA; --md-h4: #93C5FD; --md-strong: #93C5FD; --md-link: #60A5FA; --md-blockquote-bg: rgba(59,130,246,0.08); --md-blockquote-border: #60A5FA; --md-code-bg: rgba(59,130,246,0.12); --md-code-color: #93C5FD; --md-table-border: #48484A; --md-table-header-bg: #2C2C2E; --md-hr: #48484A; }
  html:not([data-theme]) pre { background: #21252B; }
  html:not([data-theme]) code { color: #ABB2BF; }
  html:not([data-theme]) img { opacity: 0.85; }
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

    ⚠️ **刻意不跟随 `--tk-*`（2026-10-16 复核过，这是分界不是遗漏）**：代码高亮的
    配色主题（one-dark ↔ 打印浅底）是一个**独立的产品决策**，正交于界面主题 ——
    用户换「胭脂」配色，期望的是界面与正文变红，不是代码块里那二十种 token 色
    跟着乱掉（每套配色都要重新定一套 token 色板，代价远超收益）。
    ⚠️ 与 `themeTokens.test.ts` 里 `TYPE_COLORS` 那组「刻意保留的硬编码」同款判据。
    别把它当漏网之鱼顺手改成 `var(--tk-code-bg)`。
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
    """时间戳 badge CSS — 对标前端 markdown-components.tsx 的 Tailwind 样式

    ⚠️ **刻意不跟随 `--tk-*`（2026-10-16 复核过，这是分界不是遗漏）**：这里的
    `#007aff`(Content) / `#ff9500`(Screenshot) 是**类别标识色**，不是主题色 ——
    它的职责是让读者在一屏里分清「这句是内容 / 这句是截图」，跨主题保持可辨识
    才有意义。同 `TYPE_COLORS`（`themeTokens.test.ts` 有它的反面对照）。
    """
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
# 插入位置在 style_block 末尾 —— 同特异性下后者胜，可覆盖主题与 katex 的屏幕样式。
#
# ⚠️ 暗色**不在本文件的职责范围**（2026-10-16 改）：主题层与 limo 主题的暗色段
# 一律包在 `@media screen` 里（「**暗色是屏幕概念，纸永远是浅的**」），打印时它们
# **根本不匹配**，落下来的是该配色自己的浅色档。故这里没有、也不需要「暗色 → 浅色」
# 还原表 —— 那种表是同一份主题数据的第二份副本，且不跟随用户配色（选「胭脂」的暗色
# 打印出来会是默认蓝）。
_PRINT_CSS = """@page { size: A4; margin: 18mm 16mm; }
@media print {
  /* 解除屏幕布局（background 简写顺带清掉纹理的 background-image / blend-mode） */
  body { max-width: none !important; margin: 0 !important; padding: 0 !important;
         background: #fff !important; color: #000 !important; }
  /* 纹理强度叠加层（跟随主题时才有）—— position:fixed 层在纸上会把正文压成灰底 */
  body::before { display: none !important; }
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


#: 卡片 / 语义块 CSS 依赖的**界面令牌** —— **仅「未跟随主题」时注入**（见
#: `_build_selfcontained_html`）：跟随路径下 `--tk-*` 由 `theme-layer.css` 提供，
#: 而那一套是它的**超集**（17 个令牌 vs 这里 10 个）且带用户自己的配色。
#:
#: 「未跟随」= 固定主题（paper / github / minimal）+ 老调用方（不带任何轴参数）。
#: 那些产物**今天就是这个样子**，所以这份取值一个字都不能动 —— 它换成主题层的暖白
#: 会让固定主题的卡片底色从 `#ffffff` 变成米黄（`--tk-surface` 6/6 不同，
#: 差异是「跟随」的语义本身，不是 bug）。
#:
#: ⚠️ **成对常量守卫**：`test_export_blocks.py` 会从这两份 CSS 里正则抽出全部
#: `var(--tk-…)` 再逐一查这里有没有定义 —— 加了新令牌却忘了补，那条断言直接红。
#: 这就是本表存在的意义（比「记得同步」可靠）。
#:
#: ⚠️ 取值：中性与品牌色对齐 `HTML_THEMES["limo"]`（同一套蓝 + 同一组灰）；语义色
#: （success / warning）用应用内同一组 Apple 系统色（`index.css` 的 `--tk-success` /
#: `--tk-warning`）—— 导出页没有理由给同一个语义换一个色相。三态齐全（浅色 / 暗色 /
#: 打印）。`@media print` 段**必须在 dark 段之后**：两者会**同时匹配**（既有教训），
#: 同特异性下后者胜。
#:
#: ⚠️ 名字里的 `NEUTRAL`（2026-10-16 由 `_CARD_TOKENS_CSS` 改）指的是「**中立于主题**」：
#: 它不给任何用户配色，只保证卡片/块在固定主题下有一组能用的灰。别把它当成
#: 「主题层的中性版」而拿去替代主题层。
_EXPORT_NEUTRAL_TOKENS_CSS = """
:root {
  --tk-surface: #ffffff;   --tk-elevated: #f5f5f7;  --tk-hover: #ececf0;
  --tk-border: #d2d2d7;    --tk-border-light: #e5e5ea;
  --tk-foreground: #1c1c1e; --tk-dimmed: #636366;   --tk-accent: #2563eb;
  --tk-success: #34c759;   --tk-warning: #ff9500;
}
@media (prefers-color-scheme: dark) {
  :root {
    --tk-surface: #1c1c1e;  --tk-elevated: #2c2c2e;  --tk-hover: #353537;
    --tk-border: #48484a;   --tk-border-light: #38383a;
    --tk-foreground: #e5e5ea; --tk-dimmed: #aeaeb2;  --tk-accent: #60a5fa;
    --tk-success: #30d158;  --tk-warning: #ff9f0a;
  }
}
@media print {
  :root {
    --tk-surface: #ffffff;   --tk-elevated: #f5f5f7;  --tk-hover: #ececf0;
    --tk-border: #d2d2d7;    --tk-border-light: #e5e5ea;
    --tk-foreground: #1c1c1e; --tk-dimmed: #636366;   --tk-accent: #2563eb;
    --tk-success: #34c759;   --tk-warning: #ff9500;
  }
}
"""


#: 卡片视觉方案的**出口白名单** —— key 集合与前端 `lib/card-prefs.ts::CARD_STYLE_OPTIONS`
#: 对齐。这里的职责只有一条：不认识的值不要写进 HTML 属性（写了也不报错，只是样式
#: 永不命中）。**不在这里定义「每套长什么样」** —— 那是 `cards.css` 的事。
_CARD_STYLES = frozenset({"gpt", "apple", "compact", "outline",
                          "paper", "guofeng", "seal", "ink"})


#: 卡片「逐维度微调」的受控变量白名单 —— 与前端 `lib/card-prefs.ts::CARD_VARS` 对齐。
#: ⚠️ **这里刻意只有名字白名单，没有「档位 key → CSS 值」的表**：取值语义归前端
#: （`CARD_BG_OPTIONS` / `CARD_BORDER_OPTIONS`），导出侧只负责一件事 —— 不把任意
#: CSS 塞进我们生成的 HTML。若在这里再维护一张表，就是同一份判据的两份实现，
#: 格式演进时漏改一侧的症状是「预览里调过了、导出还是方案原样」，而导出**照样成功**。
_CARD_TUNE_VARS = frozenset({"--card-bg-base", "--card-lift", "--card-border"})

#: 变量值允许的字符集：覆盖 `var(--tk-hover)` / `color-mix(in srgb, var(--tk-accent) 8%, var(--tk-elevated))`
#: / `transparent` / `none` / `1px solid var(--tk-border)` / `40%` 这几种真实取值。
#: ⚠️ 刻意**不含** `;` `{` `}` `<` `>` `"` `'` `\` `/` `*` —— 那恰好是「从变量值里逃逸
#: 出去写新规则 / 提前闭合注释 / 折断 `<style>`」所需的全套字符，一个都不能放。
#: ⚠️ 长度上限 **200**（真实最长是 `color-mix(...)` 那条约 55 字符，余量充足）：
#: 字符集白名单不限制长度，一次请求可以带几 MB 的合法字符进 `<style>`。
_CARD_VAR_VALUE_RE = re.compile(r"^[A-Za-z0-9 ,%().#-]{1,200}$")


#: 主题跟随（2026-10-16）—— 五条**选择型轴**的属性值白名单。
#:
#: ⚠️ 与 `_parse_card_vars` **同一条纪律：只做字符白名单，刻意不持有 key 列表**。
#: 后端再维护一份「26 套配色 / 14 种基调」的副本 = 第二份真相源，前端加一套配色而
#: 这里没跟上时的症状是「导出静默不跟随那一套」，零报错、也不影响其他轴。
#: 值最终落在 `data-accent="{v}"` 上，**原样拼接就是注入面** —— 字符集里绝不能有
#: `"` `<` `>` 空格 `=`，这条正则正是那道闸。
#:
#: 非法值**静默丢弃该项**（与 `card_style` 同判据：选错配色不改变产物的**性质**，
#: 不该让整次导出失败）。长度上限 24：最长真实 key 是 `spearmint`(9) / `parchment`(9)。
_AXIS_VALUE_RE = re.compile(r"^[a-z0-9-]{1,24}$")

#: 纹理强度 `--texture-opacity`（0–1 小数）—— 唯一走**行内 style** 的轴（它不是
#: `data-*` 能表达的形式）。⚠️ 前端 `lib/texture.ts::textureOpacity` 写的是**反转映射**
#: 后的值（`(100 - 滑条值) / 100`），导出侧**原样搬运、不做第二次换算**：再反转一次
#: 等于没跟随，而且「滑条拉到底反而更明显」这种症状看起来像 CSS 写错了。
#: 也刻意不接受 `0.5%` / `calc(...)` 这类形态 —— 它是消费者（`opacity:`）要的
#: 就是个纯数字，多收一种形态只会多一份「谁来解释它」的负担。
#: ⚠️ 小数位限 **4**（前端 `toFixed(2)` 恒 2 位，余量充足）：与 `_AXIS_VALUE_RE`
#: 的长度上限同一条理由 —— 字符集白名单**不限制长度**，一次请求可以带几 MB 的
#: 合法数字进 `<style>`。
_TEXTURE_OPACITY_RE = re.compile(r"^(?:0|1|0?\.\d{1,4})$")


def _parse_card_vars(raw: str) -> str:
    """`card_vars`（`--a:1;--b:2`）→ 一段安全的 `:root{…}`；无有效项时返回空串。

    ⚠️ **逐项静默丢弃、不报错**：判据同 `card_style` —— 微调取值出错不改变产物的
    性质，不该让整次导出失败。但丢弃**只作用于那一项**，同串里其余项照常生效。
    """
    decls = []
    for item in (raw or "").split(";"):
        name, sep, value = item.partition(":")
        name, value = name.strip(), value.strip()
        if not sep or name not in _CARD_TUNE_VARS:
            continue
        if not value or not _CARD_VAR_VALUE_RE.match(value):
            continue
        decls.append(f"{name}:{value}")
    return ":root{" + ";".join(decls) + "}" if decls else ""


def _load_frontend_css(filename: str, error_code: str, error_msg: str) -> str:
    """读前端 `frontend/src/styles/{filename}`（**单一真相源**）原样内联进导出 HTML。

    `cards.css` 与 `theme-layer.css` **共用这一份读取逻辑** —— 「开发路径 / 打包路径 /
    读不到抛错」这组三连写两遍就是模式 #54 的典型入口：改了其中一份的路径规则，另一份
    照样「能跑」，只是**永远读不到**，而两条链的调用点相隔甚远，谁也不会发现。

    ⚠️ **单一真相源在前端**：前端直接 import 这些文件，后端从磁盘读**同一份文件**。
    刻意**不**往 `backend/static/` 放副本 —— 副本就是第二份拷贝，正是本项目
    「同一份判据不得有两份实现」这条纪律要消除的形态，绕到打包路径上发生也一样。

    ⚠️ **不缓存**：它们是「我们会改的源文件」，而验收步骤正是「改完 CSS 跑一次导出看
    效果」。缓存会让改动静默不生效。`_get_katex_assets` 那边可以缓存，是因为 KaTeX 是
    只读的第三方资产 —— 性质不同，不要照抄。

    ⚠️ **读不到就抛错，不静默输出无样式的产物**：静默降级的表现是「导出成功、卡片/
    主题一点样式都没有」，而「CSS 里有卡片规则」这类断言照样全绿（本项目点名的
    「断言全过、肉眼等于没有」）。`_get_katex_assets` 找不到时只 warning ——
    **这里刻意更严**：KaTeX 缺失还能读正文，这些样式缺失等于功能没实现。

    ⚠️ **打包版必须同步 `LiMoBackend.spec` 的 datas**（`('../frontend/src/styles/x', 'styles')`）
    —— 漏了的症状是**开发版全绿、打包版导出直接 500**。
    """
    candidates = [
        # 开发环境：仓库内的源文件
        Path(__file__).resolve().parents[3] / "frontend" / "src" / "styles" / filename,
    ]
    # 打包环境：spec 的 datas 把它收到 `styles/{filename}`（与 spec 那行逐字对应）
    _meipass = getattr(sys, "_MEIPASS", None)
    if _meipass:
        candidates.append(Path(_meipass) / "styles" / filename)
    for p in candidates:
        if p.is_file():
            return p.read_text(encoding="utf-8")
    raise AppError(
        error_code, error_msg,
        detail="已查找：" + "、".join(str(p) for p in candidates),
        recoverable=False, status_code=500,
    )


def _load_card_css() -> str:
    """卡片视觉方案（`cards.css`）—— 读取纪律见 `_load_frontend_css`。"""
    return _load_frontend_css("cards.css", "EXPORT_CARD_CSS_MISSING",
                              "卡片样式文件缺失，无法导出卡片骨架")


def _load_theme_layer_css() -> str:
    """主题层（`theme-layer.css`）—— **跟随应用主题时**注入，读取纪律同上。

    ⚠️ 它是导出侧的 `--tk-*` / `--md-*` / `--font-reading` / `--texture-*`
    **唯一来源**（不跟随时由 `_EXPORT_NEUTRAL_TOKENS_CSS` + `HTML_THEMES["limo"]`
    的裸值 fallback 承担）。名字里的 `theme_layer` 与前端同源。
    """
    return _load_frontend_css("theme-layer.css", "EXPORT_THEME_CSS_MISSING",
                              "主题层文件缺失，无法导出跟随主题")


def _load_block_css() -> str:
    """读前端那份 `blocks.css`（**单一真相源**）原样内联进导出 HTML。

    与 `_load_card_css` 同一纪律：单一真相源在前端（`frontend/src/styles/blocks.css`，
    `main.tsx` 直接 import 它）、不缓存（改完 CSS 跑一次导出看效果正是验收步骤）、
    读不到就**抛错**（静默降级的表现是「导出成功、强调块一点样式都没有」，
    而那些只看「div 数量对不对」的断言照样全绿）。

    ⚠️ **刻意写成第二个函数而不是给上面那个加参数**：两份文件的仓库路径、打包路径
    （spec 的 datas 是两行）、错误码、缺失后果各自独立 —— 参数化会把「哪一份缺失」
    这条信息压成同一个错误，排查时得靠猜。
    """
    candidates = [
        Path(__file__).resolve().parents[3] / "frontend" / "src" / "styles" / "blocks.css",
    ]
    _meipass = getattr(sys, "_MEIPASS", None)
    if _meipass:
        candidates.append(Path(_meipass) / "styles" / "blocks.css")
    for p in candidates:
        if p.is_file():
            return p.read_text(encoding="utf-8")
    raise AppError(
        "EXPORT_BLOCK_CSS_MISSING",
        "内容块样式文件缺失，无法导出含标记块的产物",
        detail="已查找：" + "、".join(str(p) for p in candidates),
        recoverable=False, status_code=500,
    )


def _render_md_with_blocks(md: str) -> str:
    """Markdown → HTML，其中 `limo-block` 段包成 `<div class="md-block" …>`。

    ⚠️ **分段与包裹的逻辑不在这里** —— 收口在 `artifact_blocks.render_blocks_html`
    （Word 导出共用同一份消费循环，只差一个渲染回调）。本函数只提供**本侧的渲染器**
    （`_protect_latex` → `_markdown_to_html` → `_restore_latex`，KaTeX 保护链）。
    """
    def _render(fragment: str) -> str:
        _p, _ph = _protect_latex(fragment)
        return _restore_latex(_markdown_to_html(_p), _ph)

    return _render_blocks_html(md, _render)


def _build_selfcontained_html(md: str, title: str, theme: str = "limo",
                              skeleton: str = "paper", card_style: str = "gpt",
                              marker_decor: bool = True, card_vars: str = "",
                              theme_mode: str = "", accent: str = "",
                              base: str = "", font: str = "", texture: str = "",
                              texture_opacity: str = "") -> str:
    """构建完全自包含的 HTML 文档（图片已在上游处理完毕）。

    公式策略：
    - Node.js 可用 → 服务端 KaTeX 预渲染（公式→HTML），内联 CSS
    - Node.js 不可用 → 保留 $...$ + 内联 CSS + JS 客户端渲染
    无任何外部 CDN 依赖。

    `skeleton="card"` 时按 H2 切成 `.md-card` —— 与前端预览**同一套切分判据**
    （`split_by_h2` ↔ `splitByH2`，共享 fixture 钉住），这正是「用户在预览栏看到
    什么，导出就应该得到什么」这条核心约束在本轮的落点。⚠️ **默认 `paper` 时
    逐字走今天的分支**，一个字符都不动。

    主题跟随（2026-10-16）：末六个参数是应用主题的**五条选择型轴 + 纹理强度**，
    由前端从 store 读后传入（明暗取 `resolvedTheme` —— `theme` 可能是 `'system'`）。
    ⚠️ **各轴独立**：非空**且合法**才写属性 ⇒ **一个参数都不传时逐字节等于改造前**
    （第三方脚本、历史调用方的调用点零改动）。传了任何一条轴属性即视为「跟随」：
    此时注入 `theme-layer.css`（与前端**同一份文件**），`HTML_THEMES["limo"]` 里
    那些 `var(--tk-*, <今天裸值>)` 就开始取用户的值 —— 写成 **fallback 形式**是关键：
    未跟随时取到的就是今天那批硬编码色值，**零回归不靠分支靠 `var()` 兜底**。
    """
    import html as html_mod

    theme_css = HTML_THEMES.get(theme, HTML_THEMES["github"])
    safe_title = html_mod.escape(title or "导出")
    # ⚠️ 在**任何重新赋值之前**判定（card 分支不再改 `md`，但这条约束值得写下来）：
    # 「产物里有没有语义标记」决定是否注入 blocks.css 与装饰属性。
    blocks_present = _has_blocks(md)

    if skeleton == "card":
        # ⚠️ 切分在**原始 md** 上做（不是公式保护之后的）—— 前端 `splitByH2` 看到的
        # 就是这个字符串，两侧输入同源才谈得上「切出同一批卡片」。公式保护逐段做即可
        # （占位符 `⟨⟨LATEX_BLOCK_N⟩⟩` 恒为单行，且 `_restore_latex` 紧跟其后）。
        preamble, sections = split_by_h2(md)
        blocks: list[str] = []
        if preamble.strip():
            blocks.append(_render_md_with_blocks(preamble))
        for heading, section_body, card in sections:
            # ⚠️ 标记解析发生在**切节之后**（每节各自解析）—— 顺序反过来的话，
            # 一个跨节的块会把两节的结构搅在一起，而跨节的块本来就是异常。
            inner = _render_md_with_blocks(f"{heading}\n{section_body}")
            # ⚠️ **只有被模型标记过的节**包 `<section class="md-card">`；其余章节原样
            # 穿过（渲染结果与 `paper` 下逐字相同）。用户 2026-10-11 判定「每个 H2 一张卡
            # 太简单粗暴，卡片应该用在需要强调的地方」—— 判据在前端与后端**同源**的
            # `split_by_h2` 里（共享 fixture 钉住），这里只消费 `card` 这一位。
            # ⚠️ **不再有 `.md-cards` 外层容器**：卡片与普通章节是混排的兄弟节点，
            # 套一层 flex 会把普通章节的每一行内容变成一个独立 flex item。
            blocks.append(f'<section class="md-card">{inner}</section>' if card else inner)
        body = "\n".join(blocks)
    else:
        # 公式处理：优先服务端预渲染（⚠️ **无标记时本节与改造前逐字相同** ——
        # `_render_md_with_blocks` 在 `_has_blocks` 为假时原样走这条链）
        body = _render_md_with_blocks(md)

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

    # ── 主题跟随：五条轴各自独立，非空且合法才写 ──
    # ⚠️ 白名单只做**字符**校验、**刻意不持有 key 列表**（那是 CSS 那份真相源的第二份
    # 副本）—— 与 `_parse_card_vars` 同一条纪律。非法值**静默丢弃该项**：某个轴值不对
    # **不改变产物的性质**（判据同骨架的 `card_style` / 合成管道的维度清洗），
    # 不该让整次导出失败。
    _axis_pairs = (("data-theme", theme_mode), ("data-accent", accent),
                   ("data-base", base), ("data-font", font), ("data-texture", texture))
    axis_attrs = "".join(f' {a}="{v}"' for a, v in _axis_pairs if _AXIS_VALUE_RE.match(v))
    followed = bool(axis_attrs)
    # 纹理强度是**唯一走行内 style 的轴** —— 它不是枚举值而是 0–1 的小数。
    # ⚠️ 前端写的是**反转映射**后的值（`lib/texture.ts::applyIntensity`：强度 100 → 0）。
    # 导出侧**原样搬运、不做第二次换算**：这里是搬运工不是判据的第二个家。
    # ⚠️ 只在「跟随」时才写 —— 没有轴属性时它是个恒无效果的属性（选择器不匹配）。
    style_attr = (f' style="--texture-opacity: {texture_opacity}"'
                  if followed and _TEXTURE_OPACITY_RE.match(texture_opacity) else "")

    style_block = ""
    # 主题层（`theme-layer.css`，与前端同一份文件）—— ⚠️ **必须排在 `theme_css` 之前**：
    # 它只声明变量，`HTML_THEMES["limo"]` 消费 `var(--tk-*)`，变量先定义后消费。
    # ⚠️ 不恒注入 —— 恒注入会给每个导出多一段 `<style>`，破坏「无参数产物逐字节零回归」。
    if followed:
        style_block += f"<style>{_load_theme_layer_css()}</style>\n"
    style_block += f"<style>{theme_css}</style>"
    # ⚠️ `_EXPORT_NEUTRAL_TOKENS_CSS` 是「**未跟随**时导出页缺失的那套 `--tk-*` 定义」——
    # 固定主题（`paper`/`github`/`minimal` 的 `HTML_THEMES`）**一个 `--tk-*` 都不提供**，
    # 而 `var()` 无 fallback 时整条声明作废。它现在有**两个消费者**：`cards.css`（骨架）
    # 与 `blocks.css`（标记）。漏注入的表现是「那一块完全没有样式」，不报错、不抛异常，
    # 且**应用内预览里是好的**（那边令牌由主题系统提供）—— 所以判据必须是「凡引用了
    # `--tk-*` 的 CSS 都要与它同时在场」，而不是「skeleton=card 时注入」。
    # ⚠️ **跟随时它必须让位**：主题层才是令牌的真相源（17 个 `--tk-*` 是这里的超集）。
    # 两份同时注入 = 让「跟随时卡片底色该是 #ffffff 还是暖白」由规则先后裁决，
    # 而那正是本轮要消灭的形态。取值差别（`--tk-surface`: `#ffffff` vs 暖白）**正是
    # 「跟随」的语义**，不是 bug。
    # ⚠️ 顺序在两者之间无所谓（CSS 变量运行时解析）。
    # ⚠️ 「无标记 + paper + 未跟随」不注入 —— 保证与改造前**逐字节相同**。
    if not followed and (skeleton == "card" or blocks_present):
        style_block += f"\n<style>{_EXPORT_NEUTRAL_TOKENS_CSS}</style>"
    if skeleton == "card":
        style_block += f"\n<style>{_load_card_css()}</style>"
        # 逐维度微调（2026-10-11）—— ⚠️ **必须排在 `cards.css` 之后**：方案块
        # `[data-card-style="x"]` 与本段 `:root` 的**特异性相同**（都是 0,1,0），
        # 同特异性下**后者胜**（顺序即优先级）。放到前面会被方案原样压住，
        # 症状是「预览里调过的项，导出还是方案取值」，且导出成功、不报错。
        # 用户没调过时 `_parse_card_vars` 返回空串 —— 一个字节都不多，零回归。
        _card_vars_css = _parse_card_vars(card_vars)
        if _card_vars_css:
            style_block += f"\n<style>{_card_vars_css}</style>"
    # 强调块样式 —— ⚠️ 闸门是「**产物里真有 limo-block**」而不是骨架：标记与骨架正交
    # （`paper` 下强调块也在正文流里），但**没有标记时一个字节都不该多**：绝大多数历史
    # 产物没有标记，它们的导出 HTML 必须与改造前**逐字节相同**（无条件注入会让每个导出
    # 文件都多一段 `<style>`，视觉无变化，可那条零回归断言就再也写不出来了）。
    if blocks_present:
        style_block += f"\n<style>{_load_block_css()}</style>"
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

    # ⚠️⚠️ **卡片规则的主作用域是 `data-skeleton="card"`**，`data-card-style` 只是
    # 第二根轴（选哪套皮肤）。只写后者的话，`cards.css` 里所有
    # `[data-skeleton="card"] …` 规则在导出页上**永远不命中** ⇒ 导出的是没有样式的
    # 卡片，而「section 数量与 H2 一致」「CSS 里有卡片规则」这类断言**全绿**。
    # 这正是本项目点名的「断言全过、肉眼等于没有」，故测试里有一条专门断言它存在。
    html_attrs = ""
    if skeleton == "card":
        # 不认识的值静默回退 `gpt`：视觉方案出错**不改变产物的性质**（判据同合成管道
        # 的维度清洗），不该让整次导出失败。
        html_attrs = (' data-skeleton="card" data-card-style="'
                      + (card_style if card_style in _CARD_STYLES else "gpt") + '"')
    # 装饰开关 —— 与前端 `applyMarkers` 同款：**关掉才写属性**，默认（开）不写。
    # ⚠️ 再加一道 `blocks_present` 闸门：没有块时写它是个恒无效果的属性，还会破坏
    # 「无标记产物的导出逐字节不变」。（有块但用户没关 → 不写，与前端一致。）
    if blocks_present and not marker_decor:
        html_attrs += ' data-marker-decor="off"'
    # 主题跟随的五条轴属性 + 纹理强度行内变量（未跟随时两者都是空串，见上）。
    html_attrs += axis_attrs + style_attr

    return f"""<!DOCTYPE html>
<html lang="zh-CN"{html_attrs}>
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
    # ⚠️ **显式剥语义标记，不依赖下面那条通用的 `r'<[^>]+>'`**：那条是「去 HTML 标签」
    # 的顺带效果，把它当成「标记不会进脑图」的依据等于让协议隔离挂在一个与它无关的正则上
    # —— 那条规则哪天为别的目的被调整，`<!-- limo-block:… -->` 就会**静默变成脑图节点**。
    md = _strip_blocks(md)
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
    # 骨架轴 / 卡片视觉方案（2026-10-10）。⚠️ 默认 `paper` = 改造前的产物形态，
    # 不带这两项的调用方零影响。**只对 html 格式生效**（md / plain 没有视觉层）。
    skeleton: str = "paper"        # paper | card
    card_style: str = "gpt"        # 仅 skeleton=card 时有意义
    # 逐维度微调（2026-10-11）：`--a:1;--b:2` 形态，前端已解析成 CSS 值。
    # 空串 = 没调过（零回归），非法项由 `_parse_card_vars` 逐项丢弃。
    card_vars: str = ""
    # 语义标记装饰开关（2026-10-10）。与预览区工具栏那个是**同一个开关**（前端把偏好
    # 一路带到导出请求里），语义 = 「导出应与预览一致」；md / plain 恒剥离标记行，
    # 与它无关（那里没有视觉层可谈）。
    marker_decor: bool = True
    # 主题跟随（2026-10-16）—— 与单文档端点 `GET /export/{id}` 的六个 query 参数
    # **一一对应**（明暗 / 配色 / 基调 / 字体 / 纹理 / 强度）。默认全空串 = 不跟随，
    # 与今天的固定配色逐字节相同。
    # ⚠️ **必须两处齐全**：批量与单个走的是两条端点，只改一处的话「单个导出跟随、
    # 批量导出不跟随」，而两条都成功、都不报错 —— 用户只会觉得批量导出的颜色「怪」。
    theme_mode: str = ""           # light | dark（**解析后**的值，不是 system）
    accent: str = ""
    base: str = ""
    font: str = ""
    texture: str = ""
    texture_opacity: str = ""      # 0–1 小数，前端已做反转映射
    filename: str = "export"


@router.post("/export/custom")
def export_custom(data: ExportCustomRequest, request: Request, store=Depends(get_store)):
    """定制导出（多文档 × 多 tab × 多格式）→ ZIP 下载。

    ⚠️ 本函数**只是薄包装** —— 打包逻辑全部收在 `_build_export_zip` 一份里（P1-11）。
    它此前与 `export_custom_stream` 各有一份逐字相似的实现（内联版 vs 抽取版），
    分叉后「空结果」一处报 400、一处照发空 zip：同一件事写两遍，代价总是先在
    其中一份上出现，而两份看起来都在「正常工作」。

    ⚠️ 保持**同步 `def`**（不要改成 `async def`）：FastAPI 会把同步端点丢进线程池，
    而打包里含 `subprocess.run(["node", ...])` 这类最长 60s 的阻塞调用 —— 放事件
    循环上会冻结整个后端进程（P1-12）。
    """
    buf, _total = _build_export_zip(data, store)
    zipname = f"{data.filename}.zip"
    encoded = quote(zipname.encode('utf-8'), safe='')
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"})


# ═══════════════════════════════════════════════════════════════
# 导出进度 SSE 端点 + 临时文件下载
# ═══════════════════════════════════════════════════════════════

_TEMP_DIR = os.path.join(tempfile.gettempdir(), "limo_exports")
_TEMP_TTL = 600  # 10分钟


def _build_export_zip(
    data: ExportCustomRequest,
    store,
    push=None,
    cancel_event=None,
) -> tuple[io.BytesIO, int]:
    """构建定制导出 ZIP —— **定制导出的唯一实现**（P1-11）。返回 (buffer, total_files)。

    从前这里是 `async def` 但函数体**零 await**（P1-12）：`export_custom_stream` 用
    `await _build_export_zip(...)` 调它，于是整个打包（含 KaTeX 服务端渲染的
    `subprocess.run(["node", ...])`，最长 60s）**跑在事件循环线程上** —— 该端点一被
    调用，同进程所有请求（含 `/health`）全部挂起。改成同步 `def` 后：

      · `export_custom`（同步端点）调它 → FastAPI 自动把同步端点丢进线程池；
      · `export_custom_stream` → `await asyncio.to_thread(_build_export_zip, ...)`。

    ⚠️ `push` 是**线程安全的回调**，不是 `asyncio.Queue`：本函数现在跑在 worker
    线程里，而 `asyncio.Queue.put_nowait` **不能跨线程调**（它会直接操作 loop 的
    Future）。调用方须自己包一层 `loop.call_soon_threadsafe`。
    ⚠️ `cancel_event.is_set()` 跨线程**读**是安全的（只读一个 bool），故原样保留。

    空结果 → `EXPORT_NO_CONTENT`(400)（与另一个入口的旧行为统一，见 P1-11）。
    """
    buf = io.BytesIO()
    total_files = 0

    def _check_cancel():
        # ⚠️ 抛 `asyncio.CancelledError` 而非自定义异常：调用方（`run_build`）
        # 本来就 `except asyncio.CancelledError` 推 cancelled 事件，语义一致。
        # 它在这里意味着「用户取消了」，与「任务被 loop 取消」殊途同归。
        if cancel_event and cancel_event.is_set():
            raise asyncio.CancelledError()

    def _push(phase: str, doc: int, total: int, message: str = ""):
        if push:
            push({
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
                # 证据块线性化（2026-10-18）—— ⚠️ 改的是 `content` **本身**、不是
                # `processed`：下面 docx 分支用的是**原始 content**（图片内嵌要原始
                # 引用），只改 processed 会让 Word 拿到含围栏的原文，而 md / html /
                # plain 三条都对 —— 一处漏改、三种格式正常、一种静默错。
                # ⚠️ 必须在 `for fmt` 之前：processed 初值就是 content。
                content = _linearize_evidence(content)
                for fmt in data.formats:
                    processed = content
                    ext = fmt

                    if data.image_md == "inline" and imgs:
                        # ⚠️ `doc_dir` **必须传**（与收集侧成对，模式 #52）：少了它，
                        # `_inline_images` 里「收集时没拿到、这里就地读盘」的兜底分支
                        # 永不生效。当前 `imgs` 已全量收集故不暴露 —— 但那是**巧合**
                        # 而非保证：收集侧一旦发生裁剪，内联侧就会静默保留
                        # `assets/x.png` 引用，而 docstring 仍宣称「成对」
                        # （2026-10-11 代码审查查出）。
                        processed = _inline_images(processed, imgs, name_map, store.root,
                                                   doc_dir=doc_dir)
                    elif data.image_md == "separate" and imgs:
                        processed = _rewrite_image_paths(processed, name_map, prefix="images/")

                    if fmt == "md":
                        if data.formula_md == "remove":
                            processed = _remove_formulas(processed)
                        # 与单文档 md 导出同源：标记只在前端显示，导出文件干净
                        processed = _strip_blocks(processed)
                    elif fmt == "html":
                        processed = _build_selfcontained_html(
                            processed, tx.get("title", doc_title), data.html_theme,
                            skeleton=data.skeleton, card_style=data.card_style,
                            marker_decor=data.marker_decor, card_vars=data.card_vars,
                            theme_mode=data.theme_mode, accent=data.accent,
                            base=data.base, font=data.font, texture=data.texture,
                            texture_opacity=data.texture_opacity)
                        ext = "html"
                    elif fmt == "plain":
                        processed = _remove_formulas(processed) if data.formula_plain == "remove" else _unicode_formulas(processed)
                        # 纯文本里 HTML 注释是**可见噪音**（`<!-- limo-block:v1 … -->`），
                        # 与 md 同理剥离 —— 剥的是标记行，块内文字一个字不少。
                        processed = _strip_blocks(processed)
                        ext = "txt"
                    elif fmt == "docx":
                        # Word：用**原始 content**（非 processed）——image_md=inline 已把图片
                        # 换成 base64 data URI，那条路是给自包含 HTML 用的；docx 只要原始
                        # `![alt](assets/x.png)` + 收集好的 imgs 字典即可内嵌。
                        # 公式恒保留 LaTeX 原文（md/html 侧的 render 路径已移除，见 CHANGELOG）。
                        processed = markdown_to_docx(content, tx.get("title", doc_title),
                                                      images=imgs, skeleton=data.skeleton)
                        ext = "docx"

                    safe_tab = tab.replace("/", "-")
                    zf.writestr(f"{doc_title}/{doc_title}_{safe_tab}.{ext}", processed)
                    total_files += 1

            # 图片文件（`separate` 的契约 = 正文引用 `images/x`、图片另放一个目录）
            # ⚠️ docx **不在这个契约里**：它用原始 content（引用仍是 `assets/x`）+ imgs
            # 字典内嵌图片，压根不读 `images/`。只勾 Word + 独立图片时若照写一份
            # `images/`，那个目录在整个 zip 里**无人引用** —— 纯冗余（2026-10-11 审查查出）。
            if data.image_md == "separate" and imgs and any(f != "docx" for f in data.formats):
                _push("images", idx + 1, total_docs, f"正在写入图片...")
                for img_name, data_bytes in imgs.items():
                    zf.writestr(f"{doc_title}/images/{img_name}", data_bytes)

    buf.seek(0)
    # ⚠️ 空结果必须**显式报错**，不能发一个空 ZIP（P1-11）：另一条实现此前
    # `return buf, 0` → 照发 done → 用户下载到一个 22 字节的 zip 并以为导出成功。
    # ⚠️ `status_code=400` 必须显式给：本错误 `recoverable=True`，不给状态码会按
    # AppError 默认返回 **HTTP 200**（模式 #36），前端 `!r.ok` 于是否掉失败、
    # 照样去解那个空 zip —— 静默失败比报错更坏。
    if total_files == 0:
        raise AppError("EXPORT_NO_CONTENT", "无可导出内容",
                       status_code=400, recoverable=True)
    # done 事件只在成功时推（空结果上面已经抛了，推了也没人读）
    _push("done", total_docs, total_docs, f"导出完成，共 {total_files} 个文件")
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
    """解析单个 tab 的内容 —— **定制导出唯一实现**（`_build_export_zip` 的唯一调用点）。

    返回空串 = 「该 tab 对此文档不适用」，调用方据此不产文件（见 `_build_export_zip`
    的 `if content and content.strip()`）。⚠️ 这层语义是 2026-10-11 收紧的：
    `content_tabs` 由前端**按类型分组后拍平成并集**，对某一篇文档必然夹带它没有的
    tab —— 从前那些 tab 会被兜底成 rawText，产出名字对不上的假产物。

    ⚠️ `report` / `skeleton` 是**后端认、前端从不发**的分支（`TabRegistry` 里没有这两个
    key：`report` 从未注册、`skeleton` 已随骨架面板移除）。保留是为了不改变既有形状，
    但它们当前**取不到**。
    """
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
        # 落到这里的是图片处理类 tab（extract / table / digitize / understand）。
        # ⚠️ **查不到对应处理结果就返回空串（→ 调用方不产文件），绝不回退 rawText**：
        # 前端发出的是**各类型的并集**（ExportPanel 界面上按类型分组勾选，发请求时
        # 拍平成一个列表），后端却把这份并集套到每一篇上 —— 一旦回退 rawText，
        # 一份网页文档就会凭空多出 `_提取文字.md`/`_原图.md`/`_OCR.md`，三份内容
        # 全是同一段原文。那是**看起来成功、实则是假产物**，比少一个文件坏得多
        # （2026-10-11 实跑查出：2 篇文档导出 9 个文件，其中 6 个是重复原文）。
        for r in store.get_process_results(tx.get("id", "")):
            if r.get("mode") == tab:
                content = store.get_process_result(r["id"]) or ""
                break
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
                # ⚠️ 必须 `to_thread`（P1-12）：`_build_export_zip` 是**同步**函数，
                # 直接 `await` 它等于让整个打包（含最长 60s 的 KaTeX node 子进程）
                # 独占事件循环线程 —— 该端点一被调用，同进程所有请求（含 /health）
                # 全部挂起。它从前被标成 `async def` 却零 await，正是这个陷阱的
                # 「看不出来」形态：await 一个不 await 任何东西的协程，读起来完全
                # 正常。
                # ⚠️ push 必须跨线程回到 loop：worker 线程里直接
                # `queue.put_nowait` 会用到 `call_soon` 的线程检查（默认只在 debug
                # 模式生效）→ 生产环境静默竞态、debug 下 RuntimeError。
                _loop = asyncio.get_running_loop()

                def _push(ev):
                    _loop.call_soon_threadsafe(queue.put_nowait, ev)

                buf, total_files = await asyncio.to_thread(
                    _build_export_zip, data, store, _push, cancel_event)
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
            except AppError as e:
                # 业务失败走 SSE error 事件（照 `routers/ingest.py` 的做法）：要单独
                # 列这一支，否则落到下面的 `except Exception` → `str(e)` 未必等于
                # `message`，用户拿到一句不明所以的话，而「无可导出内容」这种文案
                # 本身是可行动的（去勾选内容）。
                queue.put_nowait({"type": "error", "message": e.message, "code": e.code})
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
