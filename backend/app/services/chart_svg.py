"""chart_svg.py — 布局产物 → SVG（严格限定在 PIL 自绘渲染器可解析的子集内）。

为什么要有出口自检（2026-09-19 的关键设计）：
    ``<marker>`` / ``url(#…)`` / CSS ``class`` / ``<defs>`` **不是被校验拦住的，
    而是被静默忽略的**——``chart_renderer._sanitize_svg`` 只剥离
    ``<script>`` / ``on*`` / ``xlink:href`` / 外部 ``href`` 四类并做良构校验，
    完全不碰上述元素。后果：生成器一旦用了 ``<marker>`` 画箭头，
    ``_validate_svg`` 照样返回 True、``_render_png`` 照样成功、PNG 正常落盘，
    **箭头无声消失**——只有像素测试能抓到。

    因此防线放在本模块的出口（``_FORBIDDEN_RE``，命中即 raise），不放在测试里：
    「验收脚本自己也会错，要在工具里补可用性检查」。

固定 skin（对齐 ``refs/画图.md``）：背景 paper、主文字 ink、连线 muted、
强调 accent（≤2 个焦点）、节点白底圆角 + ink 描边。
"""

import re

from app.services.chart_layout import TITLE_SIZE

# ── 固定配色（唯一色值来源，不散落在各处）──
BG = "#f5f5f5"        # paper 背景
INK = "#2d3142"       # 主文字/节点描边
MUTED = "#4f5d75"     # 连线/副文字
ACCENT = "#eb6c36"    # 强调（focus 节点描边 + 箭头填充）
NODE_FILL = "#ffffff"

# PIL 子集外的元素：出现即生成器 bug（详见模块 docstring）
_FORBIDDEN_RE = re.compile(
    r"<marker\b|class\s*=|\burl\s*\(#|<defs\b|<image\b|<use\b|@font-face|style\s*=",
    re.IGNORECASE,
)


class RenderSpecError(RuntimeError):
    """生成器自身产出了 PIL 子集外的元素 —— 是 bug，不是用户输入问题。"""


def _esc(s) -> str:
    """XML 转义（label 里出现 & < > 会破坏良构）。"""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _arrow_polygon(end, direction, fill: str = MUTED) -> str:
    """手绘箭头。

    PIL 不支持 ``<marker>``，只能画 polygon。顶点 ``end``，底边两点沿行进方向
    回退 8px 并垂直偏移 ±4。``_draw_poly`` 对 polygon 自动闭合描边，所以三边齐全。
    """
    ex, ey = float(end[0]), float(end[1])
    dx, dy = float(direction[0]), float(direction[1])
    bx, by = ex - dx * 8, ey - dy * 8
    px, py = -dy, dx          # 垂直单位向量（dir 已是单位轴向量）
    pts = [(ex, ey), (bx + px * 4, by + py * 4), (bx - px * 4, by - py * 4)]
    body = " ".join(f"{round(a, 1)},{round(b, 1)}" for a, b in pts)
    return f'<polygon points="{body}" fill="{fill}"/>'


def _edge_svg(e) -> str:
    dash = ' stroke-dasharray="4 4"' if e.get("dashed") else ""
    return (f'<path d="{e["d"]}" fill="none" stroke="{MUTED}"'
            f' stroke-width="1.5"{dash}/>')


def _label_svg(lb: dict) -> str:
    """标签遮罩：底色矩形压住其下的连线，再写文字。

    ``anchor="end"`` 用于 timeline 的左侧时间列（右对齐贴着轴线）。
    """
    x, y, w, h = lb["x"], lb["y"], lb["w"], lb["h"]
    anchor = lb.get("anchor", "middle")
    if anchor == "end":
        tx, ta = x + w, "end"
    elif anchor == "start":
        tx, ta = x, "start"
    else:
        tx, ta = x + w // 2, "middle"
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" fill="{BG}"/>'
            f'<text x="{tx}" y="{y + h // 2}" font-size="11" text-anchor="{ta}"'
            f' dominant-baseline="middle" fill="{MUTED}">{_esc(lb["text"])}</text>')


# 装饰元素的颜色只认**角色名**，不接受裸 hex —— 固定 skin 必须单点维护
# （裸 hex 散落各处就等同于没有 skin）。`none` 保留给「只要描边不要填充」。
_SHAPE_PAINT = {"ink": INK, "muted": MUTED, "accent": ACCENT,
                "bg": BG, "white": NODE_FILL, "none": "none"}


def _shape_svg(sh: dict) -> str:
    """装饰性元素：轴线 / 象限框 / 生命线 / 激活条 / 分组框。

    这些元素在布局产物里走 ``shapes`` 通道 —— 有几何范围但不属于内容主体，
    故不占节点数、不参与重叠校验，但**计入包围盒**（``_extent``）。
    """
    stroke = _SHAPE_PAINT.get(sh.get("stroke") or "muted", MUTED)
    fill = _SHAPE_PAINT.get(sh.get("fill") or "none", "none")
    width = sh.get("width", 1)
    dash = ' stroke-dasharray="4 4"' if sh.get("dashed") else ""
    if sh.get("kind") == "line":
        return (f'<line x1="{sh["x1"]}" y1="{sh["y1"]}" x2="{sh["x2"]}"'
                f' y2="{sh["y2"]}" stroke="{stroke}" stroke-width="{width}"{dash}/>')
    rx = f' rx="{sh["rx"]}"' if sh.get("rx") else ""
    return (f'<rect x="{sh["x"]}" y="{sh["y"]}" width="{sh["w"]}"'
            f' height="{sh["h"]}"{rx} fill="{fill}" stroke="{stroke}"'
            f' stroke-width="{width}"{dash}/>')


def _node_shape_svg(n: dict, stroke: str, sw: int) -> str:
    """节点轮廓（形状分发）。**所有形状都内切于外接矩形** ``{x,y,w,h}`` ——
    布局器只算这个矩形，形状与几何解耦（见 chart_layout `_shape_min_w`）。

    ``stadium`` 用 ``rx=h/2`` 的圆角矩形表达，比真实半圆端更好画且视觉等价。
    """
    x, y, w, h = n["x"], n["y"], n["w"], n["h"]
    shape = n.get("shape") or "rect"
    sty = f'fill="{NODE_FILL}" stroke="{stroke}" stroke-width="{sw}"'
    if shape == "diamond":
        cx, cy = x + w // 2, y + h // 2
        return f'<polygon points="{cx},{y} {x + w},{cy} {cx},{y + h} {x},{cy}" {sty}/>'
    if shape == "stadium":
        return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}"'
                f' rx="{h // 2}" {sty}/>')
    if shape == "circle":
        r = min(w, h) // 2
        return f'<circle cx="{x + w // 2}" cy="{y + h // 2}" r="{r}" {sty}/>'
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" {sty}/>'


def _node_svg(n: dict) -> str:
    """节点卡片：白底 + 描边；focus 用 accent 加粗（≤2 个）。

    文字用 ``dominant-baseline="middle"`` 垂直居中——比手算基线对不同字体更稳
    （LiMo 的中文字体是运行时探测的）。有 sub 时主副两行各偏 8/9px。
    """
    x, y, w, h = n["x"], n["y"], n["w"], n["h"]
    cx, cy = x + w // 2, y + h // 2
    focus = bool(n.get("focus"))
    stroke, sw = (ACCENT, 2) if focus else (INK, 1)
    parts = [_node_shape_svg(n, stroke, sw)]
    label, sub = n.get("label", ""), n.get("sub")
    if not (label or sub):
        return "".join(parts)           # 起止圆点等纯标记节点：只有轮廓，无文字
    if sub:
        parts.append(f'<text x="{cx}" y="{cy - 8}" font-size="13" text-anchor="middle"'
                     f' dominant-baseline="middle" fill="{INK}">{_esc(label)}</text>')
        parts.append(f'<text x="{cx}" y="{cy + 9}" font-size="11" text-anchor="middle"'
                     f' dominant-baseline="middle" fill="{MUTED}">{_esc(sub)}</text>')
    else:
        parts.append(f'<text x="{cx}" y="{cy}" font-size="13" text-anchor="middle"'
                     f' dominant-baseline="middle" fill="{INK}">{_esc(label)}</text>')
    return "".join(parts)


def _title_svg(spec: dict) -> str:
    """顶部居中标题；无标题返回空串。

    用**自然坐标**（scale 组内），故标题跟着整体等比缩放——这与布局一致：
    ``expand()`` 已把 ``TITLE_H`` 计入总高，标题就该和内容一起缩。
    y 取 ``title_h`` 的中线配 ``dominant-baseline="middle"``，不手算基线。
    """
    text, th = spec.get("title"), int(spec.get("title_h") or 0)
    if not text or th <= 0:
        return ""
    return (f'<text x="{int(spec["width"]) // 2}" y="{th // 2}"'
            f' font-size="{TITLE_SIZE}" text-anchor="middle"'
            f' dominant-baseline="middle" fill="{INK}">{_esc(text)}</text>')


def render_spec_svg(spec: dict) -> str:
    """布局产物 → SVG 字符串。

    ``spec`` 来自 ``chart_layout.expand()``（含 width/height/nodes/edges/labels）。
    根元素 ``width``/``height`` 与 ``viewBox`` 双写一致——``_parse_attr_size``
    用 ``re.search`` 取**第一个** ``width="..."``，根标签在前才取到画布尺寸。

    绘制顺序：背景 → 连线 → 边标签遮罩 → 箭头 → 节点（后绘的压住先绘的，
    所以节点永远盖住连线和遮罩）。
    """
    W, H = int(spec["width"]), int(spec["height"])
    # 缩放系数：布局按**自然尺寸**定稿，这里整体缩到目标画布
    # （「先完整画出来，再缩放到对应比例」）。k=1 时不产生任何额外包装 ——
    # 绝大多数图走的就是这条零成本路径。
    k = float(spec.get("scale") or 1.0)
    if k <= 0:
        k = 1.0
    SW, SH = max(1, round(W * k)), max(1, round(H * k))

    # ⚠️ 背景必须画在 scale 组**之外**：`width="100%"` 的语义是「铺满画布」，
    # 放进组内会被 k 缩放成 100%×k，四边留出未填充的空白带。
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SW} {SH}"'
        f' width="{SW}" height="{SH}">',
        f'<rect width="100%" height="100%" fill="{BG}"/>',
    ]
    wrapped = abs(k - 1.0) > 1e-6
    if wrapped:
        parts.append(f'<g transform="scale({k:.6f})">')
    parts.append(_title_svg(spec))
    # 装饰层在连线**之前**：轴线/分组框该被连线和节点压住，不能反过来
    parts += [_shape_svg(sh) for sh in (spec.get("shapes") or [])]
    edges = spec.get("edges") or []
    parts += [_edge_svg(e) for e in edges]
    parts += [_label_svg(lb) for lb in (spec.get("labels") or [])]
    parts += [_arrow_polygon(e["end"], e["dir"], MUTED)
              for e in edges if e.get("arrow") and e.get("end")]
    parts += [_node_svg(n) for n in (spec.get("nodes") or [])]
    if wrapped:
        parts.append("</g>")
    parts.append("</svg>")

    svg = "".join(parts)
    m = _FORBIDDEN_RE.search(svg)
    if m:
        # 生成器自身的 bug —— 这些元素会被 PIL 渲染器静默忽略（漏画），必须响亮失败
        raise RenderSpecError(f"SVG 含 PIL 子集外元素：{m.group(0)!r}")
    return svg
