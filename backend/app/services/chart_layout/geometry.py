"""几何底层：画布常量 · 文本测量 · 摆放 · 正交路径 · 包围盒。

**不依赖**同包其他模块（依赖方向 ``geometry ← layouts ← engine``）。
所有算子共用的「零几何错误」基建都集中在这里：

  · 画布常量（4px 网格）
  · 文本测量/折行/截断 —— ``measure`` 由调用方注入，保证与渲染同源
  · 横向均分与自动折行
  · 正交路径与箭头（只产 M/H/V，「禁斜线」是硬不变量）
  · 路径解析 ``_path_segments`` 与元素并集包围盒 ``_extent``
"""

import math
import re

# ── 几何常量（4px 网格，对齐原实现）──
UNIT = 4          # 网格粒度：所有坐标/宽度向上取整到 4 的倍数
MARGIN = 16       # 画布四周留白
GAP = 24          # 节点默认间距
H = 44            # 节点高度
GAP_X = 48        # 同行节点间距下限（_grid 会把列间距撑到它以上，见 _GRID_GAP_MAX）

# 网格列间距上限。GAP_X=48 本身是**下限**而非定值：同行边的标签遮罩需要
# 「文字宽 + 20 padding + 12 间隙」，48px 连 2 字标签（11px×2+20=42，余量 43）都
# 刚好卡住 —— 实测 4 字标签在 48px 间距下**整条消失**（_grid_edge_label 返回 None），
# prompt 引导模型写的 edge label 白写。故 _grid 用剩余宽度把列间距撑开，
# 上限 120px 足够放下满额（8 字 / 108px）的边标签。
_GRID_GAP_MAX = 120

DEFAULT_WIDTH = 640
DEFAULT_HEIGHT_CAP = 640

# 各布局的默认行距（行距越小图越矮，超限时按比例压缩）
_ROW_GAP = {
    "hchain": 72,
    "layers": 20,
    "timeline": 16,
    "grid": 56,
    "branch": 60,
    "tree": 64,           # 要给母线（bus）留出可见的空隙
    "quadrant": 24,
    "flowchart": 48,      # 菱形容得下才不至于连线贴边
    "state": 56,          # 回边沟槽要绕行，行距太小会与相邻行打架
    "sequence": 48,       # 消息间距，超限时靠三轮压缩吸收（见 expand）
}

_LAYOUTS = ("hchain", "layers", "timeline", "grid", "branch", "tree",
            "flowchart", "state", "sequence", "quadrant")

# 等比缩放下限。高度超限时不再直接失败，而是「按自然高度完整绘制 + 整体缩小」
# （2026-09-19 用户提议：先完整画出来，再缩放到目标比例）。但缩小会让**字号同步变小**，
# 13px × 0.65 ≈ 8.5px 已是可读性边缘；再狠的缩法宁可报错让模型拆图，
# 也不该画出一张糊得看不清的图。
_MIN_SCALE = 0.65

_MAX_NODES = 24        # 单图节点硬上限（超出 = 模型没按 ≤9 拆图，直接拒）
_EDGE_LABEL_MAX = 8    # 边标签字数上限（超出截断——标签不是骨架，截断好过丢整图）
_MIN_NODE_W = 48       # 收缩下限（低于此宽度文字必折/溢出）
_MIN_CANVAS_W = 160    # 画布宽度下限（内容裁剪后不应窄于此）
_EMPTY_LABEL = "…"     # 截断到「整个字都没了」的标记——出现即判布局失败，见 _shrink_labels

# 右侧纵向沟槽宽度：flowchart 的跳层边与 state 的回边从这里绕行，避免穿过
# 中间层的节点。算子把节点区限制在 ``W - MARGIN - _GUTTER_W`` 之内，沟槽取
# 剩下那半格——不用沟槽的图会被 ``_trim_width`` 把这部分裁掉，零代价。
_GUTTER_W = 20

# 标题占位高度。标题是**内容**而非装饰，占高度预算语义合理；代价是可用高度
# 640 → 616（-3.75%），缩放下限不变——0.65 已是可读性边缘，不该为标题再放宽
# （已决策：接受这点容量损失，见 CHANGELOG）。由 expand() 统一计入，故高度、
# 缩放、断言三处自动一致。
TITLE_H = 24
TITLE_SIZE = 15        # 标题字号（比节点主文字 13 大一档，居中于 TITLE_H）


class LayoutError(ValueError):
    """语义不合法或布局不可行。调用方应替换为失败提示文本（零残留）。"""


# ── 文本测量 ──────────────────────────────────────────────

def _measure(s: str, size: float = 13, measure=None) -> float:
    """文本宽度。

    ``measure`` 由调用方注入（签名 ``(text, size) -> float``）——LiMo 侧传
    ``_load_font(_detect_chinese_font(), size).getlength``，与渲染同源，
    宽度必然一致。未注入时退回内置估算（CJK 13px/字、其余 7.2px/字 @13px），
    仅用于无 PIL 的纯几何单测。
    """
    s = s or ""
    if measure is not None:
        try:
            return float(measure(s, size))
        except Exception:
            pass
    scale = float(size) / 13.0
    return sum(13.0 if ord(c) > 0x2E80 else 7.2 for c in s) * scale


def text_width(s: str, size: float = 13, measure=None) -> float:
    """公开的宽度估算入口（size 为像素字号）。"""
    return _measure(s, size, measure)


def r4(x: float) -> int:
    """向上对齐到 4px 网格（原实现约定，保证坐标离散、描边不出半像素）。"""
    return int(math.ceil(x / UNIT) * UNIT)


def r4_down(x: float) -> int:
    """**向下**对齐到 4px 网格 —— 居中/居中类偏移必须用它。

    尺寸用 ``r4``（宁可多给 1~3px 也不能少了放不下），但**偏移**相反：向上取整
    会把整块内容往右推几个像素，在「刚好放得下」的窄图上直接把最后一个节点
    顶出可用宽度（实测 tree 的兄弟居中：42 → 44 就够越界）。
    """
    return int(x // UNIT) * UNIT


def node_min_w(label: str, sub: str | None, measure=None) -> int:
    """节点最小宽度 = max(主文字, 副文字) + 32px 内边距，4px 对齐。

    这是「文字不超框」的唯一保证——宽度由文字决定，而非反过来。
    """
    inner = max(_measure(label, 13, measure), _measure(sub, 11, measure) if sub else 0)
    return r4(inner + 32)


# ── 节点形状 ──────────────────────────────────────────────
# 形状只影响「画成什么样」和「文字需要多少余量」，**不影响布局算法**：节点始终
# 用外接矩形 ``{x,y,w,h}`` 表达。故「外接矩形两两不重叠」的断言对 diamond/circle
# 依然充分（外接矩形不重叠 ⊃ 形状不重叠），AABB 判定一行都不用改。

_SHAPE_H = {
    "rect": H,          # 44 —— 默认
    "stadium": H,       # 44 —— 两端半圆，文字安全区被吃掉 h（见 _shape_inner_w）
    "diamond": 64,      # 菱形内接矩形只有 w/2 × h/2，高不翻倍就放不下两行
    "circle": 20,       # state 的起止圆点（label 为空，纯定位标记）
    "bar": H,           # 高度由算子显式给（sequence 激活条按跨度算）
}

SHAPES = ("rect", "diamond", "stadium", "circle", "bar")


def shape_h(shape: str, sub: str | None = None) -> int:
    """节点外接矩形高度。diamond 带副文字时再高 4px（两行才排得开）。"""
    if shape == "diamond":
        return 68 if sub else _SHAPE_H["diamond"]
    return _SHAPE_H.get(shape, H)


def _shape_min_w(label, sub, measure=None, shape="rect") -> int:
    """按形状算节点**外接矩形**的最小宽度 —— 保证文字落在形状内部。

    各形状的文字安全区相对外接矩形的占比不同，余量也就不同：

      · rect / bar   文字区 = w − 32（左右各 16 内边距）
      · stadium      两端各一个 r=h/2 的半圆，内接矩形宽 w − h
      · diamond      内接矩形宽 w/2 —— **必须翻倍**，否则菱形两腰切字
      · circle       内接正方形边长 w/√2 ≈ 0.71w，取 0.66 留余量；空 label 的
                     起止圆点是固定直径，不参与文字计算

    rect 分支与旧的 ``node_min_w`` 逐字节等价 —— 形状层引入前行为不变。
    """
    base = node_min_w(label, sub, measure)
    if shape == "diamond":
        return 2 * base
    if shape == "stadium":
        return base + H
    if shape == "circle":
        return _SHAPE_H["circle"] if not (label or sub) else r4(base / 0.66)
    return base


def _shape_inner_w(n) -> int:
    """节点内**文字可用宽度**（形状感知）—— ``_shrink_labels`` 的最后一道保险。

    与 ``_shape_min_w`` 是同一套余量的两个方向：那边是「文字 → 需要多宽」，
    这边是「已有这么宽 → 文字能占多少」。两者对 rect 必须严格同值，否则要么
    文字溢出、要么白白截断。
    """
    w, h = n['w'], n.get('h') or H
    shape = n.get('shape') or 'rect'
    if shape == "diamond":
        return max(8, w // 2 - 8)
    if shape == "stadium":
        return max(8, w - h)
    if shape == "circle":
        return max(8, int(w * 0.66))
    return w - 32 if w >= 64 else max(8, w - 8)


def _truncate_to_width(s: str, max_w: float, measure=None, size: float = 13) -> str:
    """按字符宽度累加截断并补 ``…``。

    仅在宽度被 clamp 时调用——正常情况下 ``node_min_w`` 已保证放得下。
    配合 prompt 的「label ≤8 字」是双保险。
    """
    s = s or ""
    if _measure(s, size, measure) <= max_w:
        return s
    ell = "…"
    ell_w = _measure(ell, size, measure)
    out = ""
    for ch in s:
        if _measure(out + ch, size, measure) + ell_w > max_w:
            break
        out += ch
    return (out + ell) if out else ell


def _fit_labels(label: str, sub: str | None, avail: float, measure=None,
                shape: str = "rect"):
    """把节点文字压进 avail 宽度。返回 (label, sub, w)。

    ``shape`` 决定宽度余量（见 ``_shape_min_w``）；缺省 ``rect`` 与形状层
    引入前逐字节等价，故既有 4 个算子无需改动。
    """
    w = _shape_min_w(label, sub, measure, shape)
    if w <= avail:
        return label, sub, w
    # ⚠️ 余量必须与 _shape_inner_w **同源**：那边是「已有这么宽 → 文字能占多少」，
    # 这边是「idem，且还没定稿」。曾在此写死 ``avail - 32``（那是 node_min_w 时代
    # 的配对余量），与 _shape_inner_w 的 `<64 → w-8` 分支脱节——窄节点（tree 等比
    # 收缩到 _MIN_NODE_W=48）算出 16px 可用宽，连一个中文 13px + 省略号 11px 都塞
    # 不下，于是 label 在**这里**就被截成纯 ``…``；等 _shrink_labels 拿到手时
    # ``_measure("…")=11 < 40``，它的空壳判据反倒进不去分支（2026-09-19 实测：
    # tree 8 子节点出图全是省略号，判据「写了但没生效」）。
    inner = _shape_inner_w({"w": avail, "shape": shape})
    return (_truncate_to_width(label, inner, measure, 13),
            _truncate_to_width(sub, inner, measure, 11) if sub else sub,
            r4(max(_MIN_NODE_W, avail)))


def _make_node(it, x, y, w, measure=None, shape=None) -> dict:
    """构造一个节点 dict —— 统一 label/sub/focus/shape/h 五处填充。

    形状层引入前，这五行在 5 个算子里各写一遍；新增算子越多越容易漏
    （漏 ``h = shape_h(...)`` 就画成 44px 的菱形，文字溢出）。
    """
    sh = shape or it.get('shape') or 'rect'
    lb, sb, _ = _fit_labels(it.get('label', ''), it.get('sub'), w, measure, sh)
    return {"x": x, "y": y, "w": w, "h": shape_h(sh, sb), "label": lb, "sub": sb,
            "focus": bool(it.get('focus', False)), "shape": sh}


def _shrink_labels(nodes, measure=None) -> None:
    """布局定稿后按**最终**节点宽度再截断一次文字（就地修改）。

    ``_distribute`` 的收缩是在 ``_fit_labels`` 之后发生的，极端长标签下
    节点会比文字需要的更窄。这一步是「文字不超框」这条不变式的最后保险——
    没有它，超长标签会溢出到相邻节点上（而这正是本项目要消灭的几何错误）。
    可用宽度**按形状算**：菱形的内接矩形只有 w/2，用 rect 的 w−32 会放行
    一圈溢出到两腰之外的文字。

    ⚠️ 截到**整个字都不剩**（只剩 ``…``）属于「布局已经失败」，直接抛错而非
    静默产出。实测（2026-09-19）：tree 带 8 个子节点时等比收缩把每个节点压到
    ``_MIN_NODE_W``，9 个节点**全部**变成 ``…``——图渲染成功、几何全对、断言
    全过，用户拿到的却是一张一个字都没有的图。判据刻意保守：**只有文字整个
    消失才判失败**，还能显示至少一个字的截断照旧放行（那是可接受的降级，
    也是这一步本来的用途）。
    """
    for n in nodes:
        inner = _shape_inner_w(n)
        raw = n['label']
        # 纵深防御：**上游**（_fit_labels / 算子）就把文字压没的情况。
        # 这里只看「进来时是不是空壳」——因为空壳的测量宽度（11px）远小于
        # inner，下面的截断分支根本进不去，判据会静默失效。实测踩过：
        # _fit_labels 用 16px 可用宽把 label 截成 ``…``，本函数的判据全程没触发。
        if raw == _EMPTY_LABEL:
            raise LayoutError(
                f"节点标签在布局早期就被压没了（可用宽 {inner:.0f}px）"
                "——请减少同层节点数或拆分成多张图")
        if _measure(raw, 13, measure) > inner:
            n['label'] = _truncate_to_width(raw, inner, measure, 13)
            if n['label'] == _EMPTY_LABEL:
                raise LayoutError(
                    f"节点「{raw[:8]}」被压到放不下任何文字（可用宽 {inner:.0f}px）"
                    "——图会变成一堆省略号，请减少同层节点数或拆分成多张图")
        if n.get('sub') and _measure(n['sub'], 11, measure) > inner:
            n['sub'] = _truncate_to_width(n['sub'], inner, measure, 11)


# ── 横向摆放 ──────────────────────────────────────────────

def _distribute(widths, gap, total, x0=None, center=True, grow=True):
    """在总宽 total 内摆放节点：够窄就居中留白，不够就逐个削最宽的。

    ``grow=False`` 时不放大（layers 左对齐、hchain 折行后需要各排宽度一致）。
    """
    need = sum(widths) + gap * (len(widths) - 1)
    if grow and need <= total:
        slack = total - need
        extra = [min(w * 0.6, slack / len(widths)) for w in widths]
        widths = [r4(w + e) for w, e in zip(widths, extra)]
        need = sum(widths) + gap * (len(widths) - 1)
    # 仍溢出就削最宽的（r4 向上取整会留残差，必须收敛到真的放得下）
    guard = 0
    while sum(widths) + gap * (len(widths) - 1) > total and max(widths) > _MIN_NODE_W and guard < 500:
        widths[widths.index(max(widths))] -= UNIT
        guard += 1
    need = sum(widths) + gap * (len(widths) - 1)
    base = MARGIN if x0 is None else x0
    # 居中偏移也取 4px 网格 —— 否则 hchain 折行镜像（W - x - w）后的坐标会脱格
    off = r4(max(0, (total - need) / 2)) if center else 0
    xs, cur = [], base + off
    for w in widths:
        xs.append(cur)
        cur += w + gap
    return xs, widths


def _wrap_rows(ws, gap, avail, measure=None):
    """贪心折行 → 每行的节点索引列表。

    640 宽下单排容量很有限（7 个 64px 节点就 592px，8 个放不下；而实际宽度由
    文字决定，中文标签常 100px+），所以折行是常态而非例外。
    """
    if not ws:
        return []
    cap = max(_MIN_NODE_W, avail)
    wsc = [min(w, cap) for w in ws]
    rows, cur, cur_w = [], [], 0
    for i, w in enumerate(wsc):
        add = w if not cur else gap + w
        if cur and cur_w + add > avail:
            rows.append(cur)
            cur, cur_w = [i], w
        else:
            cur.append(i)
            cur_w += add
    if cur:
        rows.append(cur)
    return rows


# ── 连线路径 ──────────────────────────────────────────────

def _edge_path(a, b):
    """两个节点之间的正交路径：同行水平、同列垂直、其余 L 形（保证禁斜线）。

    返回 ``(d, end, direction)``：``d`` 为 path 数据；``end`` 为终点坐标；
    ``direction`` 为末段方向单位向量（箭头朝向）。只生成 M/H/V —— 末段必为
    轴对齐，箭头方向零解析成本。
    """
    acx, acy = a['x'] + a['w'] // 2, a['y'] + a['h'] // 2
    bcx = b['x'] + b['w'] // 2
    if a['y'] == b['y']:
        if a['x'] < b['x']:
            return f"M{a['x'] + a['w']} {acy} H{b['x']}", (b['x'], acy), (1, 0)
        return f"M{a['x']} {acy} H{b['x'] + b['w']}", (b['x'] + b['w'], acy), (-1, 0)
    if a['y'] < b['y']:
        if abs(acx - bcx) < 4:
            return f"M{acx} {a['y'] + a['h']} V{b['y']}", (acx, b['y']), (0, 1)
        ym = (a['y'] + a['h'] + b['y']) // 2
        return (f"M{acx} {a['y'] + a['h']} V{ym} H{bcx} V{b['y']}",
                (bcx, b['y']), (0, 1))
    if abs(acx - bcx) < 4:
        return f"M{acx} {a['y']} V{b['y'] + b['h']}", (acx, b['y'] + b['h']), (0, -1)
    ym = (b['y'] + b['h'] + a['y']) // 2
    return (f"M{acx} {a['y']} V{ym} H{bcx} V{b['y'] + b['h']}",
            (bcx, b['y'] + b['h']), (0, -1))


def _make_edge(a, b, dashed=False, arrow=True):
    """构造一条边（路径 + 箭头终点/方向）。"""
    d, end, direction = _edge_path(a, b)
    return {"d": d, "end": end, "dir": direction,
            "dashed": bool(dashed), "arrow": bool(arrow)}


def _elbow(a, b, bus_y, arrow=False):
    """父→子的直角总线连线：``父底 → 母线 → 子心纵线 → 子顶``。

    tree 用它：同一父的多个子共用一条水平母线，视觉上就是一棵树。只产
    M/V/H —— 「禁斜线」不变量与 ``_edge_path`` 同约。末段恒向下，故箭头
    方向固定 ``(0, 1)``，无需解析路径。

    父子中心 x 相同时省掉母线（两点直连的竖线），避免产出 ``H`` 的零长度段。
    """
    axc = a['x'] + a['w'] // 2
    bxc = b['x'] + b['w'] // 2
    if axc == bxc:
        d = f"M{axc} {a['y'] + a['h']} V{b['y']}"
    else:
        d = f"M{axc} {a['y'] + a['h']} V{bus_y} H{bxc} V{b['y']}"
    return {"d": d, "end": (bxc, b['y']), "dir": (0, 1),
            "dashed": False, "arrow": bool(arrow)}


# ── 元素包围盒 ────────────────────────────────────────────

_PATH_TOK_RE = re.compile(r"[MHVmhv]|-?\d+(?:\.\d+)?")


def _path_segments(d: str):
    """正交路径 → 轴对齐线段列表 ``[(x0, y0, x1, y1), …]``；无法解析返回 []。

    只处理 M/H/V —— 布局器自己生成的路径全部落在这个子集内（``_edge_path`` /
    肘形 bus / 回边沟槽 / 自环），且每条命令都显式带字母，不存在 SVG 的隐式重复
    命令。集中在这里解析，胜过让每个算子各自记录自己产出的极值（12 个算子漏一个，
    图就会画到画布外）。

    为什么必须有它：**边的几何范围不一定被节点 bbox 包住**。同行绕行段、state
    的右侧回边沟槽都在节点右侧之外；12px 自环只是碰巧落在 ``MARGIN=16`` 内，
    属于巧合安全而非构造安全。**「边不穿节点」的断言也依赖它**——只有拆成段，
    轴对齐的相交判定才是精确的（用整条边的 bbox 判会大面积误报）。
    """
    tokens = _PATH_TOK_RE.findall(d or "")
    if not tokens:
        return []
    x = y = 0.0
    segs: list[tuple[float, float, float, float]] = []
    cmd = None
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.isalpha():                 # 命令字母：切换当前段类型
            cmd = t.upper()
            i += 1
            continue
        nx, ny = x, y
        if cmd == "M" and i + 1 < len(tokens):
            # M 只**移动当前点**、不画线。若当成「移动 + 记段」，每条路径都会
            # 多出一条从 (0,0) 到起点的假线段——`_extent` 只取 max 值时看不出来
            # （假段不抬高 maxx/maxy），但重建路径时会把它写进去：曾产出
            # `M0 0 H230 …` 被 assert_layout 抓出「连线穿过节点」。
            x, y = float(tokens[i]), float(tokens[i + 1])
            i += 2
            continue
        if cmd == "H":
            nx = float(tokens[i])
            i += 1
        elif cmd == "V":
            ny = float(tokens[i])
            i += 1
        else:
            i += 1
            continue
        if (nx, ny) != (x, y):
            segs.append((x, y, nx, ny))
        x, y = nx, ny
    return segs


def _path_bbox(d: str):
    """正交路径的包围盒 → ``(minx, miny, maxx, maxy)``；无法解析返回 None。"""
    segs = _path_segments(d)
    if not segs:
        return None
    xs = [s[0] for s in segs] + [s[2] for s in segs]
    ys = [s[1] for s in segs] + [s[3] for s in segs]
    return (min(xs), min(ys), max(xs), max(ys))


def _overlap(ax, ay, aw, ah, bx, by, bw, bh) -> bool:
    """两个 AABB 是否相交（半开区间——恰好贴边不算重叠）。"""
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def _num(v: float) -> str:
    """数字转路径字面量：整数不带小数点（``38`` 而非 ``38.0``，路径更好读）。"""
    iv = int(v)
    return str(iv) if iv == v else f"{v:g}"


def _shift_path_y(d: str, dy: int) -> str:
    """路径整体下移 dy，重建为只含 M/H/V 的正交路径。

    不能直接改字面量 —— M/H/V 的参数个数与含义各不相同（``M`` 吃两个数、``H``
    只吃 x），正则替换必然改错。用 ``_path_segments`` 拆段后重建：几何等价，
    且**保持「全正交」不变量**（测试断言 ``set(命令) <= {M,H,V}`` 依赖它）。
    """
    segs = _path_segments(d)
    if not segs:
        return d
    parts = [f"M{_num(segs[0][0])} {_num(segs[0][1] + dy)}"]
    for (x0, y0, x1, y1) in segs:
        if x1 != x0:
            parts.append(f"H{_num(x1)}")
        elif y1 != y0:
            parts.append(f"V{_num(y1 + dy)}")
    return " ".join(parts)


def _shift_title_space(nodes, edges, labels, shapes, dy) -> None:
    """所有元素整体下移 dy，让出顶部标题空间（title 非空时由 expand 调用）。

    **统一后处理而非给算子加 ``top`` 参数**：新增算子最容易漏的就是「记得从
    MARGIN+TITLE_H 起排」，漏了就直接撞标题；后处理一处生效，算子零改动。
    """
    if not dy:
        return
    for n in nodes:
        n['y'] += dy
    for lb in labels:
        lb['y'] += dy
    for sh in shapes:
        if sh.get('kind') == 'line':
            sh['y1'] += dy
            sh['y2'] += dy
        else:
            sh['y'] += dy
    for e in edges:
        e['d'] = _shift_path_y(e['d'], dy)
        if e.get('end'):
            e['end'] = (e['end'][0], e['end'][1] + dy)


def _extent(nodes, labels=None, shapes=None, edges=None):
    """四类元素的并集包围盒 → ``(minx, miny, maxx, maxy)``；全空返回 None。

    **凡会画出像素的东西都必须计入**：nodes（内容）、labels（遮罩）、
    shapes（轴线/生命线/分组框）、edges（连线）。少算一类，高度或裁剪就会算错
    ——sequence 的生命线撑出画布高度、state 的回边沟槽撑出画布宽度，靠的都是它。
    """
    xs: list[float] = []
    ys: list[float] = []
    for n in nodes or []:
        xs += [n['x'], n['x'] + n['w']]
        ys += [n['y'], n['y'] + n['h']]
    for lb in labels or []:
        xs += [lb['x'], lb['x'] + lb['w']]
        ys += [lb['y'], lb['y'] + lb['h']]
    for sh in shapes or []:
        if sh.get('kind') == 'line':
            xs += [sh['x1'], sh['x2']]
            ys += [sh['y1'], sh['y2']]
        else:
            xs += [sh['x'], sh['x'] + sh['w']]
            ys += [sh['y'], sh['y'] + sh['h']]
    for e in edges or []:
        bb = _path_bbox(e.get('d'))
        if bb:
            xs += [bb[0], bb[2]]
            ys += [bb[1], bb[3]]
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))

