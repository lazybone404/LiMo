"""
图表渲染器 — 把 LLM 生成的图表围栏渲染为 PNG 嵌入笔记。

协议定位（pi-docs/图表围栏内部协议设计说明.md）：
- 图表只用专用围栏（不属于任何通用语言，用户内容出现概率≈0，零误伤）
- 只处理专用围栏；用户自己的 svg/xml 代码块、裸内联 <svg> 一律不碰
- 围栏必然被替换：成功 → ![图表](assets/chart_{hash8}.png)；失败 → 「📊 图表生成失败」文本
- 产物（存储/展示/导出）永不含围栏，内部协议不外泄

两条围栏路径（2026-09-19 起并存）：
- ```fig  — **语义 JSON**（推荐）：LLM 只答「有哪些节点、怎么排」，坐标由
  chart_layout 算、chart_svg 出图。几何错误从结构上不可能发生，几何规则
  也就不必进 prompt（绕开 2026-08-16「堆规则 → fast 模型宁可跳过不画」的教训）。
- ```chart — **手写 SVG**（legacy 兜底）：原链路完全不变，模型习惯性输出
  SVG 时仍能出图；回滚只需改 prompt 的 `get_chart_format` 一个函数。

处理链：```fig/```chart 围栏 → （语义 JSON → 布局 → SVG）/（提取 <svg> 根元素）
→ 安全过滤 → 校验链 → PIL 渲染 PNG(2x) → 替换为图片引用（成功）或失败提示文本（失败）。

校验链（提取出的 SVG 按不可信内容处理）：
- 检测：只认专用围栏；chart 内容须含 <svg 根元素，fig 内容须是 JSON 对象
- 安全过滤：<script> / on* 事件属性 / xlink:href·外部 URL 引用
- 校验：XML 良构 → viewBox + width/height 双尺寸 ≤640×640 → 有实际内容 → 渲染（失败重试 1 次）
- 多图：每个围栏一张，文件名 = `chart_{内容hash8}.png`（**不是**按序编号，见 `_process_svg`）
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw, ImageFont  # PIL 已随图片优化打包，是硬依赖

from app.services import chart_layout, chart_svg  # 语义围栏链路（chart_layout 零依赖）

logger = logging.getLogger(__name__)

# 围栏：只认 chart/fig 专用标记（协议空间与用户内容空间隔离；svg/xml 是通用语言，不碰）
# ⚠️ 两个分组：group(1)=kind（chart|fig），group(2)=body。改动时所有 group 引用要同步。
_FENCE_RE = re.compile(r"```(chart|fig)\s*\n(.*?)\n```", re.DOTALL)

# 专用围栏标记（供路由层判定「选了图表但没出图」用）
CHART_FENCE_MARKS = ("```chart", "```fig")

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

# 图表必须承载文字信息——零文字图（只有框/线，LLM 偷懒不写 <text>）对读者无意义，
# 直接失败标注而非渲染出无字图（2026-08-16 用户反馈「图表里没有文字」）。
# 加强（同批）：<text 打开标签后必须有非空白文字内容——空 <text></text> / 自闭合 <text/>
# 也算「无文字」，拦截（此前只查标签存在，空 text 会通过校验却渲染出无字图）。
_TEXT_TAG_RE = re.compile(r"<text\b[^>]*>\s*[^<\s]", re.IGNORECASE)

# <svg 根元素（含自闭合，viewBox 属性须在 <svg 标签内）
_SVG_ROOT_RE = re.compile(r"<svg\b[^>]*>.*?</svg>", re.DOTALL)

# viewBox="0 0 w h"
_VIEWBOX_RE = re.compile(r'viewBox=["\']([^"\']+)["\']')

# width/height 属性值（数值 + 可选 px 单位；百分比无法确定绝对尺寸，忽略）
_DIM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:px)?", re.IGNORECASE)


# LLM 偶发在图片引用后粘连 "svg" 字面量（如 `![图表](assets/chart_59e0267f.png)svg`）——
# 格式垃圾。注意匹配是**通用**的 `![...](...)`，与图片叫什么名字无关（不依赖下面的 hash 命名）。
# 仅清理图片引用同行紧随的 svg（[ \t]* 不跨换行；后跟字母不清理），避免误伤正常文本。
_IMG_SVG_GARBAGE_RE = re.compile(r"(!\[[^\]]*\]\([^)]*\))[ \t]*svg(?![A-Za-z])")

# ── 中文字体参数（2026-08-16 resvg→PIL 切换后）──
# resvg-py 0.4.0 在 Windows 上 fontdb 加载系统字体失败，<text> 完全无法渲染
# （实测连英文字符都画不出）。图表改为 PIL 自绘解析渲染，需要系统中文字体文件
# 的路径（PIL ImageFont.truetype）。探测第一个存在的字体文件；失败用默认字体。
_FONT_CANDIDATES = [
    (r"C:\Windows\Fonts\msyh.ttc", "Microsoft YaHei"),
    (r"C:\Windows\Fonts\msyh.ttf", "Microsoft YaHei"),
    (r"C:\Windows\Fonts\simhei.ttf", "SimHei"),
    (r"C:\Windows\Fonts\simsun.ttc", "SimSun"),
    ("/System/Library/Fonts/PingFang.ttc", "PingFang SC"),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "Noto Sans CJK SC"),
    ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", "Noto Sans CJK SC"),
    ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", "WenQuanYi Micro Hei"),
]

# 探测结果缓存（进程内只探测一次，避免每次渲染重复走文件系统）
_detected_font_path: str | None = None
_font_probed = False

# PIL 字体缓存 {(path, size): ImageFont} —— 图表字号离散（12-30px 几个值），缓存命中率高
_font_cache: dict = {}


def _detect_chinese_font() -> str | None:
    """探测系统第一个可用的中文字体文件路径（PIL 渲染用，缓存）。None = 未找到。"""
    global _detected_font_path, _font_probed
    if _font_probed:
        return _detected_font_path
    _font_probed = True
    from PIL import ImageFont
    for path, name in _FONT_CANDIDATES:
        try:
            ImageFont.truetype(path, 20)  # 加载成功 = 该字体真实可用
            _detected_font_path = path
            logger.info("图表中文字体探测成功: %s (%s)", name, path)
            return path
        except Exception:
            continue
    logger.warning("未探测到系统中文字体，图表文字用 PIL 默认字体渲染")
    return None


def render_charts(markdown: str, doc_dir: str = "") -> str:
    """提取生成内容中的 ```fig / ```chart 围栏，渲染 PNG 嵌入笔记。

    - 无专用围栏或 doc_dir 为空时原样返回（不写临时目录导致引用失效）
    - 任一校验/渲染环节失败 → 替换为「📊 图表生成失败」提示文本（零残留，
      围栏/源码消失——内部协议不外泄）
    - 只处理专用围栏；用户自己的 svg/xml 代码块、裸内联 <svg> 一律不碰
    """
    if not any(mark in markdown for mark in CHART_FENCE_MARKS):
        return markdown
    if not doc_dir:
        return markdown

    assets_dir = os.path.join(doc_dir, "assets")
    os.makedirs(assets_dir, exist_ok=True)

    counter = 0

    def _replace(match: re.Match) -> str:
        nonlocal counter
        kind, body = match.group(1), match.group(2)
        # ```fig 围栏里写 <svg> 时按 chart 路径处理：模型改用 fig 后 legacy chart
        # 兜底就自然失效了，JSON 一错就是白丢一张图。按**内容形态**（而非围栏名）
        # 分流，兜底重新生效——成本一行判断。
        if kind == "fig" and "<svg" not in body:
            out, counter, reason = _process_fig(body, assets_dir, counter)
        else:
            svg = _extract_svg_root(body)
            if not svg:
                # chart 围栏但内容不含 <svg 根元素 → 不是图表代码块，不处理
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


def _font_measure(text: str, size: float) -> float:
    """用**渲染同源**字体对象测宽，供布局器使用。

    布局器的node_min_w 据此定节点宽度。不能硬编码「CJK 13px/字」——LiMo 的中文
    字体是运行时探测的（msyh/simhei/Noto…），不同字体字宽可差 10–20%，
    低估即「文字超框」，而本方案不新增几何 linter，没有兜底。
    """
    return _load_font(_detect_chinese_font(), int(size)).getlength(text)


def _parse_json_object(body: str):
    """从围栏内容里取第一个完整 JSON 对象。

    容忍模型多包一层 ```json、加前后说明文字——逐个 ``{`` 尝试 ``raw_decode``，
    比手写括号计数稳。取不到返回 None。
    """
    for i, ch in enumerate(body):
        if ch != "{":
            continue
        try:
            return json.JSONDecoder().raw_decode(body[i:])[0]
        except ValueError:
            continue
    return None


def _process_fig(body: str, assets_dir: str, counter: int) -> tuple[str, int, str | None]:
    """语义 JSON → 布局 → SVG → 复用 _process_svg 的校验/渲染/落盘。

    与 chart 分支有意不同的一点：chart 内非 SVG **原样保留**（可能是协议外内容），
    而 fig 是毫无歧义的协议标记 → 任何失败都**必须零残留替换**。
    """
    obj = _parse_json_object(body)
    if not isinstance(obj, dict):
        logger.info("图表 fig 围栏 JSON 解析失败 → 替换为失败文本")
        return None, counter, "图表数据格式错误"

    try:
        spec = chart_layout.expand(obj, width=int(MAX_CANVAS), height_cap=int(MAX_CANVAS),
                                   measure=_font_measure)
    except chart_layout.LayoutError as e:
        logger.info("图表布局失败（%s）→ 替换为失败文本", e)
        return None, counter, f"布局失败：{e}"

    try:
        svg = chart_svg.render_spec_svg(spec)
    except chart_svg.RenderSpecError as e:
        # 生成器自身的 bug（产出了 PIL 子集外的元素）——用户看不懂，只记日志
        logger.warning("图表 SVG 生成器 bug：%s", e)
        return None, counter, "图表绘制失败"

    # source_json 落盘为 chart_N.json：只在走通 fig 分支时出现，
    # 是「模型确实切到新协议」的确定性证据（只看 PNG 张数区分不了新旧路径）
    return _process_svg(svg, assets_dir, counter, source_json=obj)


def _process_svg(svg: str, assets_dir: str, counter: int,
                 source_json: dict | None = None) -> tuple[str, int, str | None]:
    """校验 + 渲染单个 SVG。返回 (输出, 新计数器, 失败原因)。

    成功：写 `chart_{内容hash8}.png`，返回图片引用、原因 None。
    失败：返回 (None, counter, reason)——由调用方替换为失败提示文本。
    ⚠️ `counter` 现在**不参与文件名**（只保留给调用方接口与「失败不递增」语义）。
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
    # ⚠️ 文件名带**内容 hash**，不是 `chart_{n}.png`：counter 每次生成都从 0 起，
    #    重跑一次就把 `chart_1.png` 覆盖掉，而 v1 的 `notes.prev.md` **仍引用**
    #    `assets/chart_1.png` —— 切到 v1 看到的是「新版的图配旧版的正文」。三张图
    #    全部渲染成功、文件都在、零报错，静默错配（模式 #52 的同族：成对的东西
    #    改了一半）。hash 让「内容相同 → 同名」（幂等、不累积孤儿）、「内容不同 →
    #    不同名」（互不覆盖）同时成立，对齐截图路径的做法
    #    （`app/utils/video_helper.py` 的 `screenshot_{i:03}_{uuid8}.jpg`）。
    fname = f"chart_{hashlib.sha1(svg.encode('utf-8')).hexdigest()[:8]}.png"
    try:
        with open(os.path.join(assets_dir, fname), "wb") as f:
            f.write(data)
        # 保存 SVG 源码片段（2026-08-16 用户提议）：不用于展示（assets 白名单无 svg），
        # 供①开发调试——文字没渲染时能查看 LLM 原始输出②用户深度使用时导出矢量图
        with open(os.path.join(assets_dir, fname.replace(".png", ".svg")),
                  "w", encoding="utf-8") as f:
            f.write(svg)
        # 语义源同步保存（仅 fig 路径）：与 .svg 一样是纯落盘旁路文件——
        # 不写进 markdown、不被服务、不进任何导出产物（导出只按 markdown 引用收集图片）。
        # 用途：① 调试布局 ② 「这次走的是新 fig 路径还是被容错接住的旧 SVG 路径」的
        # 确定性信号（两条路都出 PNG 且张数可能一样，只有 .json 能区分）
        if source_json is not None:
            with open(os.path.join(assets_dir, fname.replace(".png", ".json")),
                      "w", encoding="utf-8") as f:
                json.dump(source_json, f, ensure_ascii=False)
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
    if not _TEXT_TAG_RE.search(sanitized):
        # 有形状但零文字——对读者无意义，失败标注（prompt 已要求每个节点配文字）
        return False, "无文字说明"
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

    最终产物是 PNG（PIL 渲染，不执行脚本），过滤是纵深防御 + 方案硬性要求。
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


# ── PIL 自绘 SVG 子集渲染器（2026-08-16 resvg→PIL 切换）──
# 背景：resvg 全系（py 绑定 0.4.0 + CLI v0.47/v0.48 官方二进制）在 Windows 无法渲染
# SVG 文字——fontdb 加载系统字体正常（--list-fonts 可见 Arial），但 usvg text 解析
# 一律 "No match for '<font-family>'" → 跳过 text。上游普遍 bug，非绑定/配置问题。
# 图表 SVG 是 prompt 约束的简单子集（rect/text/line/path/circle/ellipse/polygon/polyline），
# PIL 自绘 + 系统探测中文字体可完整渲染。取舍：非子集元素（image 等）忽略——漏画优于画错。

# path d 命令 token（兼容 LLM 粘连写法 M470 90 V130：命令字母与数字分离）
_PATH_TOKEN_RE = re.compile(r"([MmLlHhVvZzCcSsQqTtAa])|(-?\d*\.?\d+)")

# 椭圆弧命令参数个数（A 不画，仅用于跳过——曲线 C/S/Q/T 已采样绘制）
_CURVE_NARGS = {"A": 7}

# 贝塞尔采样步数（C/S 三次、Q/T 二次 → 密集折线点，虚线按弧长分段精确）
_BEZIER_STEPS = 24


def _bezier3(p0, p1, p2, p3, steps: int = _BEZIER_STEPS):
    """三次贝塞尔曲线采样点（不含起点 p0）。"""
    out = []
    for i in range(1, steps + 1):
        t = i / steps
        u = 1.0 - t
        x = u ** 3 * p0[0] + 3 * u ** 2 * t * p1[0] + 3 * u * t ** 2 * p2[0] + t ** 3 * p3[0]
        y = u ** 3 * p0[1] + 3 * u ** 2 * t * p1[1] + 3 * u * t ** 2 * p2[1] + t ** 3 * p3[1]
        out.append((x, y))
    return out


def _bezier2(p0, p1, p2, steps: int = _BEZIER_STEPS):
    """二次贝塞尔曲线采样点（不含起点 p0）。"""
    out = []
    for i in range(1, steps + 1):
        t = i / steps
        u = 1.0 - t
        x = u ** 2 * p0[0] + 2 * u * t * p1[0] + t ** 2 * p2[0]
        y = u ** 2 * p0[1] + 2 * u * t * p1[1] + t ** 2 * p2[1]
        out.append((x, y))
    return out


def _reflect(cur, last_ctrl):
    """S/T 平滑曲线控制点 = 上一控制点关于当前点的反射。无上一控制点时用当前点。"""
    if last_ctrl is None:
        return cur
    return (2 * cur[0] - last_ctrl[0], 2 * cur[1] - last_ctrl[1])


def _parse_viewbox_full(svg: str) -> tuple[float, float, float, float] | None:
    """解析完整 viewBox (min-x, min-y, w, h)；非四值格式返回 None。"""
    m = _VIEWBOX_RE.search(svg)
    if not m:
        return None
    parts = m.group(1).replace(",", " ").split()
    if len(parts) != 4:
        return None
    try:
        minx, miny, w, h = (float(p) for p in parts)
    except ValueError:
        return None
    if w <= 0 or h <= 0:
        return None
    return minx, miny, w, h


# ── SVG transform 支持（2026-08-17 补齐）──
# 用户实测「图表错位/缺内容」的主因之一：<g transform="translate(...)"> 之类的元素
# 此前完全不被解析——`_svg_to_pil` 直接跳过 <g>，translate/scale/rotate 全部丢失，
# 元素画在原始坐标或整块不画。这里用 2x3 affine 矩阵表示变换，
# 2x3 表示 (a, b, c, d, e, f)，对应公式：x' = a*x + c*y + e, y' = b*x + d*y + f。

_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _mat_mul(m, n):
    """affine 矩阵复合 m∘n（先 n 后 m 应用）。m/n 为 6 元组。"""
    a1, b1, c1, d1, e1, f1 = m
    a2, b2, c2, d2, e2, f2 = n
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def _parse_transform(attr: str | None) -> tuple[float, float, float, float, float, float] | None:
    """SVG transform 属性 → 2x3 affine (a,b,c,d,e,f)。

    支持 translate / scale / rotate / matrix 的组合（空格或逗号分隔），按文本顺序
    从左到右逐个复合（SVG 语义：先声明的先作用，后声明的复合在其后）。
    无 transform / 全部解析失败 → None（恒等变换）。数值缺失按 SVG 默认（scale
    单参数 sy=sx，translate 单参数 ty=0）。
    """
    if not attr or not attr.strip():
        return None
    result = None
    for fm in re.finditer(r"([a-zA-Z]+)\s*\(([^)]*)\)", attr):
        name = fm.group(1).lower()
        args = [float(t) for t in fm.group(2).replace(",", " ").split() if t]
        if name == "translate":
            tx = args[0] if len(args) > 0 else 0.0
            ty = args[1] if len(args) > 1 else 0.0
            mat = (1.0, 0.0, 0.0, 1.0, tx, ty)
        elif name == "scale":
            sx = args[0] if len(args) > 0 else 1.0
            sy = args[1] if len(args) > 1 else sx
            mat = (sx, 0.0, 0.0, sy, 0.0, 0.0)
        elif name == "rotate":
            deg = args[0] if len(args) > 0 else 0.0
            rad = math.radians(deg)
            co, si = math.cos(rad), math.sin(rad)
            if len(args) >= 3:
                # rotate(deg, cx, cy) = translate(cx,cy) ∘ rotate ∘ translate(-cx,-cy)
                cx, cy = args[1], args[2]
                mat = _mat_mul(
                    (1.0, 0.0, 0.0, 1.0, cx, cy),
                    _mat_mul((co, si, -si, co, 0.0, 0.0), (1.0, 0.0, 0.0, 1.0, -cx, -cy)),
                )
            else:
                mat = (co, si, -si, co, 0.0, 0.0)
        elif name == "matrix" and len(args) >= 6:
            mat = tuple(float(v) for v in args[:6])
        else:
            mat = None
        if mat is None:
            continue
        result = _mat_mul(result, mat) if result is not None else mat
    return result


def _parse_color(v: str | None) -> str | None:
    """SVG 颜色值 → PIL 颜色；none/空 → None（不画该层）。#abc 简写展开。"""
    if not v:
        return None
    v = v.strip()
    if v.lower() == "none" or not v:
        return None
    if v.startswith("#") and len(v) == 4:  # #abc → #aabbcc
        v = "#" + "".join(ch * 2 for ch in v[1:])
    return v


def _parse_dasharray(v: str | None) -> list[float] | None:
    """stroke-dasharray → [dash, gap, ...]（SVG 坐标，未缩放）。None = 无虚线。"""
    if not v:
        return None
    vals = []
    for s in v.replace(",", " ").split():
        try:
            f = float(s)
        except ValueError:
            return None
        if f < 0:
            return None
        vals.append(f)
    return vals if vals else None


def _load_font(font_path: str | None, size: int) -> ImageFont.ImageFont:
    """加载字体（进程内缓存）。None → PIL 默认字体（无中文）。"""
    key = (font_path, size)
    f = _font_cache.get(key)
    if f is not None:
        return f
    if font_path:
        try:
            f = ImageFont.truetype(font_path, size)
        except Exception:
            f = ImageFont.load_default()
    else:
        f = ImageFont.load_default()
    _font_cache[key] = f
    return f


def _path_polylines(d: str) -> list[list[tuple[float, float]]]:
    """SVG path d → 若干折线（SVG 坐标，未缩放）。

    覆盖 M/L/H/V/Z 直线子集 + C/S/Q/T 贝塞尔曲线（采样为密集折线点，兼容虚线
    按弧长分段）；相对命令 + 隐式 L + 粘连写法（M470 90 V130）由正则 tokenizer
    分离。A（椭圆弧）跳过参数并断开折线——图表基本不用，漏画优于画错。
    """
    toks: list[str | float] = []
    for m in _PATH_TOKEN_RE.finditer(d):
        toks.append(m.group(1) if m.group(1) else float(m.group(2)))
    if not toks:
        return []

    polylines: list[list[tuple[float, float]]] = []
    cur = (0.0, 0.0)
    start = (0.0, 0.0)  # 当前子路径起点（Z 闭合回起点）
    last_ctrl: tuple[float, float] | None = None  # S/T 平滑曲线的反射基准
    poly: list[tuple[float, float]] | None = None
    i, n = 0, len(toks)

    def _nums(count: int) -> list[float]:
        nonlocal i
        out: list[float] = []
        while len(out) < count and i < n and isinstance(toks[i], float):
            out.append(toks[i])
            i += 1
        return out

    def _abs(v, is_rel):
        return (v[0] + cur[0], v[1] + cur[1]) if is_rel else v

    while i < n:
        t = toks[i]
        if isinstance(t, float):  # 孤立数字（冗余参数）跳过
            i += 1
            continue
        i += 1
        cmd = t
        up = cmd.upper()
        rel = cmd.islower()
        if up == "Z":
            # 闭合：回到子路径起点（画连接段，fill 场景正确闭合）
            if poly and len(poly) > 1 and \
                    (abs(poly[0][0] - cur[0]) > 1e-9 or abs(poly[0][1] - cur[1]) > 1e-9):
                poly.append(poly[0])
            cur = start
            last_ctrl = None
            poly = None
            continue
        if up == "M":
            a = _nums(2)
            if len(a) < 2:
                continue
            cur = _abs(a, rel)
            start = cur
            poly = [cur]
            polylines.append(poly)
            last_ctrl = None
            while True:  # 后续点对为隐式 L
                a = _nums(2)
                if len(a) < 2:
                    break
                cur = _abs(a, rel)
                poly.append(cur)
            continue
        if up == "L":
            if poly is None:
                poly = [cur]
                polylines.append(poly)
            last_ctrl = None
            while True:  # L 可带多组点对
                a = _nums(2)
                if len(a) < 2:
                    break
                cur = _abs(a, rel)
                poly.append(cur)
            continue
        if up == "H":
            if poly is None:
                poly = [cur]
                polylines.append(poly)
            last_ctrl = None
            a = _nums(1)
            if not a:
                continue
            cur = (cur[0] + a[0], cur[1]) if rel else (a[0], cur[1])
            poly.append(cur)
            continue
        if up == "V":
            if poly is None:
                poly = [cur]
                polylines.append(poly)
            last_ctrl = None
            a = _nums(1)
            if not a:
                continue
            cur = (cur[0], cur[1] + a[0]) if rel else (cur[0], a[0])
            poly.append(cur)
            continue
        # 贝塞尔曲线：采样为密集折线（C/S 三次，Q/T 二次），S/T 控制点反射
        if up in ("C", "S", "Q", "T"):
            if poly is None:
                poly = [cur]
                polylines.append(poly)
            if up == "C":
                a = _nums(6)
                if len(a) < 6:
                    continue
                p1 = _abs((a[0], a[1]), rel)
                p2 = _abs((a[2], a[3]), rel)
                pe = _abs((a[4], a[5]), rel)
                pts = _bezier3(cur, p1, p2, pe)
                ctrl = p2
            elif up == "S":
                a = _nums(4)
                if len(a) < 4:
                    continue
                p1 = _reflect(cur, last_ctrl)
                p2 = _abs((a[0], a[1]), rel)
                pe = _abs((a[2], a[3]), rel)
                pts = _bezier3(cur, p1, p2, pe)
                ctrl = p2
            elif up == "Q":
                a = _nums(4)
                if len(a) < 4:
                    continue
                p1 = _abs((a[0], a[1]), rel)
                pe = _abs((a[2], a[3]), rel)
                pts = _bezier2(cur, p1, pe)
                ctrl = p1
            else:  # T
                a = _nums(2)
                if len(a) < 2:
                    continue
                p1 = _reflect(cur, last_ctrl)
                pe = _abs(a, rel)
                pts = _bezier2(cur, p1, pe)
                ctrl = p1
            last_ctrl = ctrl
            poly.extend(pts)
            cur = pe
            continue
        # A（椭圆弧）等其他命令：跳过参数并断开折线（漏画优于画错）
        i += min(_CURVE_NARGS.get(up, 6), n - i)
        poly = None
        last_ctrl = None
    return polylines


def _draw_polyline(draw: ImageDraw.ImageDraw, pts, fill: str, width: int,
                   dash: list[float] | None) -> None:
    """画折线；dash = stroke-dasharray 数值列表（SVG 坐标，未缩放）。"""
    if len(pts) < 2:
        return
    if not dash:
        draw.line(pts, fill=fill, width=width)
        return
    if len(dash) % 2:
        dash = dash * 2  # 奇数项 → 重复成对（SVG 规范）
    period = sum(dash)
    if period <= 0:
        draw.line(pts, fill=fill, width=width)
        return
    segs = list(zip(pts, pts[1:]))
    seg_lens: list[tuple[float, float, tuple, tuple]] = []
    acc = 0.0
    for a, b in segs:
        L = ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5
        seg_lens.append((acc, acc + L, a, b))
        acc += L
    if acc <= 0:
        return
    total = acc

    def _pt(t: float) -> tuple[float, float]:
        for s0, s1, p0, p1 in seg_lens:
            if t <= s1:
                if s1 <= s0:
                    return p1
                f = (t - s0) / (s1 - s0)
                return (p0[0] + (p1[0] - p0[0]) * f,
                        p0[1] + (p1[1] - p0[1]) * f)
        return seg_lens[-1][3]

    t = 0.0
    di = 0
    while t < total:
        on = dash[di % len(dash)]
        if di % 2 == 0 and on > 0:
            end = min(t + on, total)
            a = _pt(t)
            b = _pt(end)
            if (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 > 1e-6:
                draw.line([a, b], fill=fill, width=width)
        off = dash[(di + 1) % len(dash)]
        t += on + (off if off > 0 else 1e-6)
        di += 2


def _draw_rect(draw: ImageDraw.ImageDraw, el, m, _map, scale: float) -> None:
    x = float(el.get("x", 0) or 0)
    y = float(el.get("y", 0) or 0)
    rw = float(el.get("width", 0) or 0)
    rh = float(el.get("height", 0) or 0)
    if rw <= 0 or rh <= 0:
        return
    rx = float(el.get("rx", 0) or 0)
    fill = _parse_color(el.get("fill"))
    stroke = _parse_color(el.get("stroke"))
    sw = float(el.get("stroke-width", 1) or 1)
    # transform 局部缩放（画布 scale 已由 _map 吸收，这里只补笔宽/圆角的局部因子）
    lscale = (abs(m[0]) + abs(m[3])) / 2
    if lscale <= 0:
        lscale = 1.0
    tots = scale * lscale
    if abs(m[1]) < 1e-9 and abs(m[2]) < 1e-9:
        # 无旋转/斜切 → 轴对齐圆角矩形（保持观感）
        (x0, y0) = _map(x, y, m)
        (x1, y1) = _map(x + rw, y + rh, m)
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        box = [x0, y0, x1, y1]
        radius = max(0, int(round(rx * tots)))
        if fill:
            draw.rounded_rectangle(box, radius=radius, fill=fill)
        if stroke:
            draw.rounded_rectangle(box, radius=radius, outline=stroke,
                                   width=max(1, int(round(sw * tots))))
    else:
        # 含旋转/斜切 → 四角多边形近似（圆角丢失，漏画圆角优于画错位置）
        pts = [_map(x, y, m), _map(x + rw, y, m),
               _map(x + rw, y + rh, m), _map(x, y + rh, m)]
        if fill:
            draw.polygon(pts, fill=fill)
        if stroke:
            draw.line(pts + [pts[0]], fill=stroke,
                      width=max(1, int(round(sw * tots))))


def _draw_text(draw: ImageDraw.ImageDraw, el, m, _map, font_path, scale: float) -> None:
    x = float(el.get("x", 0) or 0)
    y = float(el.get("y", 0) or 0)
    fs = float(el.get("font-size", 16) or 16)
    text = (el.text or "").strip()
    if not text:
        return
    # text-anchor → PIL 水平锚（start/middle/end）
    anchor_h = {"start": "l", "middle": "m", "end": "r"}.get(
        el.get("text-anchor", "start"), "l")
    # dominant-baseline → PIL 垂直锚（默认基线 s；middle/central → 中线 m，
    # text-before-edge/hanging/top → ascender；text-after-edge/bottom → descender）
    db = (el.get("dominant-baseline") or el.get("alignment-baseline") or "auto").lower()
    anchor_v = {"middle": "m", "central": "m", "text-before-edge": "a",
                "hanging": "a", "top": "a", "text-after-edge": "d",
                "bottom": "d"}.get(db, "s")
    lscale = (abs(m[0]) + abs(m[3])) / 2
    if lscale <= 0:
        lscale = 1.0
    f = _load_font(font_path, max(1, int(round(fs * scale * lscale))))
    fill = _parse_color(el.get("fill")) or "#000000"
    px, py = _map(x, y, m)
    draw.text((px, py), text, font=f, fill=fill, anchor=anchor_h + anchor_v)


def _draw_line(draw: ImageDraw.ImageDraw, el, m, _map, scale: float) -> None:
    x1 = float(el.get("x1", 0) or 0)
    y1 = float(el.get("y1", 0) or 0)
    x2 = float(el.get("x2", 0) or 0)
    y2 = float(el.get("y2", 0) or 0)
    stroke = _parse_color(el.get("stroke")) or "#000000"
    sw = float(el.get("stroke-width", 1) or 1)
    dash = _parse_dasharray(el.get("stroke-dasharray"))
    lscale = (abs(m[0]) + abs(m[3])) / 2
    if lscale <= 0:
        lscale = 1.0
    _draw_polyline(draw, [_map(x1, y1, m), _map(x2, y2, m)], stroke,
                   max(1, int(round(sw * scale * lscale))), dash)


def _draw_path(draw: ImageDraw.ImageDraw, el, m, _map, scale: float) -> None:
    d = el.get("d", "")
    if not d:
        return
    stroke = _parse_color(el.get("stroke"))
    fill = _parse_color(el.get("fill"))
    sw = float(el.get("stroke-width", 1) or 1)
    dash = _parse_dasharray(el.get("stroke-dasharray"))
    lscale = (abs(m[0]) + abs(m[3])) / 2
    if lscale <= 0:
        lscale = 1.0
    for pl in _path_polylines(d):
        pts = [_map(x, y, m) for x, y in pl]
        if len(pts) < 2:
            continue
        if fill:
            try:
                draw.polygon(pts, fill=fill)
            except Exception:
                pass
        if stroke:
            _draw_polyline(draw, pts, stroke,
                           max(1, int(round(sw * scale * lscale))), dash)


_ELLIPSE_STEPS = [2 * math.pi * i / 48 for i in range(48)]


def _draw_ellipse(draw: ImageDraw.ImageDraw, el, m, _map, scale: float) -> None:
    tag = el.tag.split("}")[-1]
    fill = _parse_color(el.get("fill"))
    stroke = _parse_color(el.get("stroke"))
    sw = float(el.get("stroke-width", 1) or 1)
    lscale = (abs(m[0]) + abs(m[3])) / 2
    if lscale <= 0:
        lscale = 1.0
    if tag == "circle":
        cx = float(el.get("cx", 0) or 0)
        cy = float(el.get("cy", 0) or 0)
        r = float(el.get("r", 0) or 0)
        rx = ry = r
    else:
        cx = float(el.get("cx", 0) or 0)
        cy = float(el.get("cy", 0) or 0)
        rx = float(el.get("rx", 0) or 0)
        ry = float(el.get("ry", 0) or 0)
    if abs(m[1]) < 1e-9 and abs(m[2]) < 1e-9:
        # 无旋转 → 轴对齐椭圆
        (px0, py0) = _map(cx - rx, cy - ry, m)
        (px1, py1) = _map(cx + rx, cy + ry, m)
        box = [px0, py0, px1, py1]
        if fill:
            draw.ellipse(box, fill=fill)
        if stroke:
            draw.ellipse(box, outline=stroke,
                         width=max(1, int(round(sw * scale * lscale))))
    else:
        # 含旋转 → 圆周采样多边形近似
        pts = [_map(cx + rx * math.cos(a), cy + ry * math.sin(a), m)
               for a in _ELLIPSE_STEPS]
        if fill:
            draw.polygon(pts, fill=fill)
        if stroke:
            draw.line(pts + [pts[0]], fill=stroke,
                      width=max(1, int(round(sw * scale * lscale))))


def _draw_poly(draw: ImageDraw.ImageDraw, el, m, _map, scale: float) -> None:
    tag = el.tag.split("}")[-1]
    vals = (el.get("points") or "").replace(",", " ").split()
    pts = []
    for i in range(0, len(vals) - 1, 2):
        try:
            pts.append(_map(float(vals[i]), float(vals[i + 1]), m))
        except ValueError:
            continue
    if len(pts) < 2:
        return
    fill = _parse_color(el.get("fill"))
    stroke = _parse_color(el.get("stroke"))
    sw = float(el.get("stroke-width", 1) or 1)
    lscale = (abs(m[0]) + abs(m[3])) / 2
    if lscale <= 0:
        lscale = 1.0
    if fill and tag == "polygon":
        draw.polygon(pts, fill=fill)
    if stroke:
        # polygon 必须闭合（SVG 语义：最后一点回起点）——draw.line 不自动闭合，
        # 此前菱形/箭头描边缺最后一条边（用户实测「流程图错位和边框缺失」）
        closed = pts + [pts[0]] if tag == "polygon" else pts
        draw.line(closed, fill=stroke, width=max(1, int(round(sw * scale * lscale))))


def _svg_to_pil(svg: str, scale: float, font_path: str | None) -> Image.Image:
    root = ET.fromstring(svg)

    vb = _parse_viewbox_full(svg)
    if vb:
        vbx, vby, vbw, vbh = vb
    else:
        vbx = vby = 0.0
        s = _parse_attr_size(svg) or (640.0, 280.0)
        vbw, vbh = s
    w = max(1, int(round(vbw * scale)))
    h = max(1, int(round(vbh * scale)))

    img = Image.new("RGB", (w, h), (245, 245, 245))
    draw = ImageDraw.Draw(img)

    def _map(x, y, m):
        """用户坐标（含 transform）→ 像素坐标。"""
        x2 = m[0] * x + m[2] * y + m[4]
        y2 = m[1] * x + m[3] * y + m[5]
        return int(round((x2 - vbx) * scale)), int(round((y2 - vby) * scale))

    # 背景 rect（width/height 百分比）铺满画布（顶层无 transform）
    for el in root:
        if el.tag.split("}")[-1] == "rect" and (
            (el.get("width") or "").strip().endswith("%")
            or (el.get("height") or "").strip().endswith("%")
        ):
            fill = _parse_color(el.get("fill"))
            if fill:
                draw.rectangle([0, 0, w, h], fill=fill)
            break

    def _walk(elements, parent_m):
        """递归遍历元素，累计 transform（<g> 会滚入子元素）。"""
        for el in elements:
            tag = el.tag.split("}")[-1]
            tm = _parse_transform(el.get("transform"))
            m = _mat_mul(tm, parent_m) if tm is not None else parent_m
            if tag == "g":
                _walk(list(el), m)
                continue
            try:
                if tag == "rect":
                    if (el.get("width") or "").strip().endswith("%") or \
                            (el.get("height") or "").strip().endswith("%"):
                        continue  # 背景 rect（百分比尺寸）已在上方铺满画布，跳过
                    _draw_rect(draw, el, m, _map, scale)
                elif tag == "text":
                    _draw_text(draw, el, m, _map, font_path, scale)
                elif tag == "line":
                    _draw_line(draw, el, m, _map, scale)
                elif tag == "path":
                    _draw_path(draw, el, m, _map, scale)
                elif tag in ("circle", "ellipse"):
                    _draw_ellipse(draw, el, m, _map, scale)
                elif tag in ("polygon", "polyline"):
                    _draw_poly(draw, el, m, _map, scale)
            except Exception as e:  # 单个元素解析/绘制失败不影响整图
                logger.warning("图表元素 <%s> 绘制失败（跳过）: %s", tag, str(e)[:80])
                continue

    _walk(list(root), _IDENTITY)
    return img


def _render_png(svg: str, scale: float = SCALE) -> bytes | None:
    """PIL 渲染 SVG → PNG bytes。

    2026-08-16 resvg→PIL 切换：resvg 全系（py 绑定 + CLI v0.47/v0.48）在 Windows
    无法渲染 SVG 文字（usvg text 解析 "No match for <font>"，上游 bug）。PIL 自绘 +
    系统探测中文字体路径可完整渲染中文。失败返回 None（调用方重试 1 次后失败标注）。
    """
    try:
        import io
        font_path = _detect_chinese_font()
        img = _svg_to_pil(svg, scale, font_path)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        logger.warning("PIL 图表渲染失败: %s", str(e)[:120])
        return None


def _fail_note(reason: str) -> str:
    """渲染失败提示文本（替换围栏，零残留——产物不含内部协议）。"""
    return f"\n📊 图表生成失败（{reason}）\n"
