"""
图表渲染器 — 把 LLM 生成的 ```chart 围栏渲染为 PNG 嵌入笔记。

协议定位（pi-docs/图表围栏内部协议设计说明.md）：
- 图表只用 ```chart 专用围栏（不属于任何通用语言，用户内容出现概率≈0，零误伤）
- 只处理 ```chart 围栏；用户自己的 svg/xml 代码块、裸内联 <svg> 一律不碰
- 围栏必然被替换：成功 → ![图表](assets/chart_N.png)；失败 → 「📊 图表生成失败」文本
- 产物（存储/展示/导出）永不含围栏，内部协议不外泄

处理链：```chart 围栏 → 提取 <svg> 根元素 → 安全过滤 → 校验链 → resvg 渲染 PNG(2x)
→ 替换为图片引用（成功）或失败提示文本（失败）。

校验链（提取出的 SVG 按不可信内容处理）：
- 检测：只认 ```chart 围栏，内容须含 <svg 根元素
- 安全过滤：<script> / on* 事件属性 / xlink:href·外部 URL 引用
- 校验：XML 良构 → viewBox + width/height 双尺寸 ≤640×640 → 有实际内容 → 渲染（失败重试 1 次）
- 多图编号：按出现顺序 chart_1 / chart_2 / …
"""

from __future__ import annotations

import logging
import os
import re
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

# 围栏：只认 chart 专用标记（协议空间与用户内容空间隔离；svg/xml 是通用语言，不碰）
_FENCE_RE = re.compile(r"```chart\s*\n(.*?)\n```", re.DOTALL)

# 画布上限（逻辑像素，方案 §2.4：≤640×640 上限，非固定值）
MAX_CANVAS = 640.0

# 单个 SVG 围栏内容长度上限（LLM 输出无显式 token cap，与超大画布叠加成 DoS 面）
MAX_SVG_BYTES = 64 * 1024

# 渲染缩放因子（固定 2x —— 屏幕 retina + A4 打印双清晰）
SCALE = 2.0

# 有实际内容判定用到的形状元素（文本/图形皆可）
_SHAPE_TAGS_RE = re.compile(
    r"<(rect|circle|ellipse|line|path|polygon|polyline|text|image)\b",
    re.IGNORECASE,
)

# <svg 根元素（含自闭合，viewBox 属性须在 <svg 标签内）
_SVG_ROOT_RE = re.compile(r"<svg\b[^>]*>.*?</svg>", re.DOTALL)

# viewBox="0 0 w h"
_VIEWBOX_RE = re.compile(r'viewBox=["\']([^"\']+)["\']')

# width/height 属性值（数值 + 可选 px 单位；百分比无法确定绝对尺寸，忽略）
_DIM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:px)?", re.IGNORECASE)


# LLM 偶发在图片引用后粘连 "svg" 字面量（如 `![图表](assets/chart_1.png)svg`）——格式垃圾。
# 仅清理图片引用同行紧随的 svg（[ \t]* 不跨换行；后跟字母不清理），避免误伤正常文本。
_IMG_SVG_GARBAGE_RE = re.compile(r"(!\[[^\]]*\]\([^)]*\))[ \t]*svg(?![A-Za-z])")


def render_charts(markdown: str, doc_dir: str = "") -> str:
    """提取生成内容中的 ```chart 围栏，渲染 PNG 嵌入笔记。

    - 无 chart 围栏或 doc_dir 为空时原样返回（不写临时目录导致引用失效）
    - 任一校验/渲染环节失败 → 替换为「📊 图表生成失败」提示文本（零残留，
      围栏/源码消失——内部协议不外泄）
    - 只处理 ```chart 专用围栏；用户自己的 svg/xml 代码块、裸内联 <svg> 一律不碰
    """
    if "```chart" not in markdown:
        return markdown
    if not doc_dir:
        return markdown

    assets_dir = os.path.join(doc_dir, "assets")
    os.makedirs(assets_dir, exist_ok=True)

    counter = 0

    def _replace(match: re.Match) -> str:
        nonlocal counter
        body = match.group(1)
        svg = _extract_svg_root(body)
        if not svg:
            # 围栏语言是 chart 但内容不含 <svg 根元素 → 不是图表代码块，不处理
            return match.group(0)
        out, counter, reason = _process_svg(svg, assets_dir, counter)
        if reason is not None:
            # 失败：替换为失败提示文本（围栏/源码消失，产物零残留）
            return _fail_note(reason)
        return out

    md = _FENCE_RE.sub(_replace, markdown)

    # LLM 在图片引用后粘连的 svg 字面量 → 清理（引用本身保持正常）
    md = _IMG_SVG_GARBAGE_RE.sub(r"\1", md)
    return md


def _process_svg(svg: str, assets_dir: str, counter: int) -> tuple[str, int, str | None]:
    """校验 + 渲染单个 SVG。返回 (输出, 新计数器, 失败原因)。

    成功：counter +1 并写 chart_{counter}.png，返回图片引用、原因 None。
    失败：返回 (None, counter, reason)——由调用方替换为失败提示文本，
      counter 不递增（编号不跳号）。
    """
    if len(svg) > MAX_SVG_BYTES:
        # 长度上限（P1-2）：不进入校验/渲染，返回失败原因由调用方替换为失败文本
        logger.info("图表 SVG 过大（%d 字符 > %d）→ 替换为失败文本", len(svg), MAX_SVG_BYTES)
        return None, counter, "SVG 过大"

    ok, reason = _validate_svg(svg)
    if not ok:
        logger.info("图表校验失败（%s）→ 替换为失败文本", reason)
        return None, counter, reason

    data = _render_png(svg)
    if data is None:
        data = _render_png(svg)  # 渲染失败重试 1 次
    if data is None:
        return None, counter, "渲染失败"

    next_counter = counter + 1
    fname = f"chart_{next_counter}.png"
    try:
        with open(os.path.join(assets_dir, fname), "wb") as f:
            f.write(data)
    except OSError as e:
        logger.warning("图表写入失败: %s", str(e)[:120])
        return None, counter, "写入失败"

    return f"![图表](assets/{fname})", next_counter, None


def _extract_svg_root(body: str) -> str:
    """从围栏内容中提取 <svg>...</svg> 根元素（围栏外文字丢弃）。"""
    m = _SVG_ROOT_RE.search(body)
    if not m:
        return ""
    return m.group(0)


def _validate_svg(svg: str) -> tuple[bool, str]:
    """校验链：XML 良构 → 画布尺寸（viewBox + width/height 取较大）→ 有实际内容。

    resvg 实际输出尺寸由 width/height 属性决定（优先于 viewBox），只校验
    viewBox 可被 `width="50000"` 绕过导致超大画布 OOM（P0-2）——两者逐轴取较大值校验。
    """
    sanitized = _sanitize_svg(svg)
    if sanitized is None:
        return False, "XML 非良构或含危险内容"
    size = _parse_viewbox(sanitized)
    attr = _parse_attr_size(sanitized)
    if attr is not None:
        w, h = attr if size is None else (max(size[0], attr[0]), max(size[1], attr[1]))
    else:
        w, h = size if size is not None else (None, None)
    if w is not None and (w > MAX_CANVAS or h > MAX_CANVAS):
        return False, f"画布超限（{w:.0f}×{h:.0f} > {MAX_CANVAS:.0f}px）"
    if not _SHAPE_TAGS_RE.search(sanitized):
        return False, "无可见内容"
    return True, ""


def _parse_viewbox(svg: str) -> tuple[float, float] | None:
    """解析 viewBox 宽高；非 `min-x min-y w h` 四值格式返回 None（不校验尺寸）。"""
    m = _VIEWBOX_RE.search(svg)
    if not m:
        return None
    parts = m.group(1).replace(",", " ").split()
    if len(parts) != 4:
        return None
    try:
        _, _, w, h = (float(p) for p in parts)
    except ValueError:
        return None
    if w <= 0 or h <= 0:
        return None
    return w, h


def _parse_attr_size(svg: str) -> tuple[float, float] | None:
    """解析 SVG 根元素的 width/height 属性尺寸。

    仅当两者均为合法的绝对数值（含 px 后缀）时返回；百分比/缺失/非法 → None
    （此时回退 viewBox 校验）。返回的尺寸为 resvg 实际输出画布（P0-2 防御）。
    """
    m_w = re.search(r'\bwidth\s*=\s*"([^"]+)"', svg)
    m_h = re.search(r'\bheight\s*=\s*"([^"]+)"', svg)
    if not m_w or not m_h:
        return None

    def _num(v: str) -> float | None:
        v = v.strip()
        if v.endswith("%"):
            return None  # 百分比无法确定绝对尺寸
        m = _DIM_RE.match(v)
        if not m:
            return None
        val = float(m.group(1))
        return val if val > 0 else None

    w, h = _num(m_w.group(1)), _num(m_h.group(1))
    if w is None or h is None:
        return None
    return w, h


def _sanitize_svg(svg: str) -> str | None:
    """剥离危险内容后返回 SVG；非良构返回 None。

    最终产物是 PNG（resvg 渲染，不执行脚本），过滤是纵深防御 + 方案硬性要求。
    """
    # 1) 剥离 <script> 块（含自闭合/带属性的变体）
    svg = re.sub(r"<script\b[^>]*>.*?</script\s*>", "", svg, flags=re.DOTALL | re.IGNORECASE)
    svg = re.sub(r"<script\b[^>]*/>", "", svg, flags=re.IGNORECASE)
    # 2) 剥离 on* 事件属性
    svg = re.sub(r'\s+on\w+\s*=\s*"[^"]*"', "", svg, flags=re.IGNORECASE)
    svg = re.sub(r"\s+on\w+\s*=\s*'[^']*'", "", svg, flags=re.IGNORECASE)
    # 3) 剥离外部引用属性（xlink:href 全删；href 中外部/数据 URI 删，本地锚点 #xx 保留）
    svg = re.sub(r'\s+xlink:href\s*=\s*"[^"]*"', "", svg, flags=re.IGNORECASE)
    svg = re.sub(r"\s+xlink:href\s*=\s*'[^']*'", "", svg, flags=re.IGNORECASE)
    svg = re.sub(r'\s+href\s*=\s*"[^"#]*[a-zA-Z][^"]*"', "", svg)
    svg = re.sub(r"\s+href\s*=\s*'[^'#]*[a-zA-Z][^']*'", "", svg)
    # 4) XML 良构校验（解析失败 = 校验链第 1 环不过）
    try:
        ET.fromstring(svg)
    except ET.ParseError:
        return None
    return svg


def _render_png(svg: str, scale: float = SCALE) -> bytes | None:
    """resvg 渲染 SVG → PNG bytes。惰性 import（打包时须在 spec 显式收集 resvg_py）。"""
    try:
        from resvg_py import svg_to_bytes
        return svg_to_bytes(svg, zoom=scale)
    except Exception as e:  # ImportError / RuntimeError（bad svg 等）
        logger.warning("resvg 渲染失败: %s", str(e)[:120])
        return None


def _fail_note(reason: str) -> str:
    """渲染失败提示文本（替换围栏，零残留——产物不含内部协议）。"""
    return f"\n📊 图表生成失败（{reason}）\n"
