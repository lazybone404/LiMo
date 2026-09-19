"""引擎：语义校验 · 几何自检 · 主入口 ``expand``。

依赖方向 ``geometry ← layouts ← engine`` —— 本模块在末端，没有出边。
"""

import copy
import math

from .geometry import (
    DEFAULT_HEIGHT_CAP,
    DEFAULT_WIDTH,
    MARGIN,
    TITLE_H,
    TITLE_SIZE,
    LayoutError,
    _EDGE_LABEL_MAX,
    _extent,
    _LAYOUTS,
    _MAX_NODES,
    _measure,
    _MIN_CANVAS_W,
    _MIN_SCALE,
    _overlap,
    _path_bbox,
    _path_segments,
    _ROW_GAP,
    _shift_title_space,
    _shrink_labels,
    r4,
)
from .layouts import (
    _grid,
    _layers,
    _NODES_RUNNERS,
    _norm_tree,
    _SPEC_RUNNERS,
    _tree_stats,
)

# ── 语义校验 ──────────────────────────────────────────────

def _count_nodes(spec: dict, layout: str) -> int:
    """内容节点数（shapes / labels 一律不计 —— 它们不是"图里有多少个东西"）。"""
    if layout == "layers":
        return sum(len(l.get('nodes') or []) for l in (spec.get('layers') or []))
    if layout == "grid":
        return sum(len(r) for r in (spec.get('rows') or []))
    if layout == "branch":
        return len(spec.get('nodes') or []) * 2
    if layout == "tree":
        return _tree_stats(_norm_tree(spec.get('root') or {}))[0]
    if layout == "state":
        # 默认分支数的是 spec['nodes']，state 的容器叫 states —— 不特判就会
        # 恒定返回 0，_MAX_NODES 形同虚设
        return len(spec.get('states') or [])
    if layout == "sequence":
        return len(spec.get('actors') or [])
    if layout == "quadrant":
        return sum(len(q.get('items') or []) for q in (spec.get('quadrants') or [])
                   if isinstance(q, dict))
    return len(spec.get('nodes') or [])


def _validate_id_graph(items, edges, kind: str, edge_max: int = 16) -> None:
    """flowchart / state 共用的 **字符串 id 引用**校验。

    两者都用 id 连边（不像 grid 用 ``[行,列]`` 下标）：判定会跳转/汇合，行列
    索引正是最易写错的字段，而 id 能把错误精确指到 ``edges[2].to``，
    模型据此能直接改对——报错的可操作性决定了它要不要重画一遍。
    """
    ids = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            raise LayoutError(f"{kind}[{i}] 不是对象")
        _id = it.get('id')
        if not isinstance(_id, str) or not _id:
            raise LayoutError(
                f"{kind}[{i}] 缺少字符串 id——flowchart/state 用 id 互相引用，不要用下标")
        ids.append(_id)
    if len(set(ids)) != len(ids):
        raise LayoutError("节点 id 重复："
                          + "、".join(sorted({x for x in ids if ids.count(x) > 1})))
    idset = set(ids)
    for i, e in enumerate(edges):
        if not isinstance(e, dict):
            raise LayoutError(f"edges[{i}] 不是对象")
        for key in ("from", "to"):
            v = e.get(key)
            # isinstance 前置：list/dict 未进 idset 时 `in` 会先抛 TypeError
            if not isinstance(v, str) or v not in idset:
                raise LayoutError(f"edges[{i}].{key} = {v!r} 不是任何节点的 id"
                                  f"（可用：{'、'.join(ids)}）")
    if len(edges) > edge_max:
        raise LayoutError(f"连线过多（{len(edges)} > {edge_max}），请拆分成多张图")


def validate_spec(spec) -> dict:
    """语义校验 + 归一化，返回处理后的副本。失败 raise LayoutError。

    校验只做「不校验就会画出错图」的事——几何正确性由布局器保证，不在这里重复。
    """
    if not isinstance(spec, dict):
        raise LayoutError("图表数据不是 JSON 对象")
    spec = copy.deepcopy(spec)
    layout = spec.get('layout', 'hchain')
    if layout not in _LAYOUTS:
        # 从 _LAYOUTS 拼而非手写字面量：手写的清单漏过一次 branch，且每加一种
        # 布局都要记得回来改（漏改的后果是模型永远不知道有这种图）。
        raise LayoutError(f"未知布局 {layout!r}（可用：{' / '.join(_LAYOUTS)}）")

    if layout == "tree":
        root = spec.get('root')
        if not isinstance(root, dict):
            raise LayoutError("tree 需要 root 对象（含 label 与 children）")
        if not root.get('label') and not (root.get('children') or []):
            raise LayoutError("tree 的 root 为空——至少要有 root.label")
        _cnt, _depth = _tree_stats(_norm_tree(root))
        if _depth > 4:
            raise LayoutError(
                f"树的层级过深（{_depth} 层 > 4）——竖排会挤出画布，请拆成多张图")
    elif layout == "flowchart":
        items = spec.get('nodes') or []
        if not items:
            raise LayoutError("flowchart 的 nodes 为空——至少要有一个节点")
        _validate_id_graph(items, spec.get('edges') or [], "nodes")
        _nd = sum(1 for it in items if str(it.get('type') or '') == 'decision')
        if _nd > 3:
            raise LayoutError(f"判定节点过多（{_nd} > 3），请拆分成多张图")
    elif layout == "state":
        items = spec.get('states') or []
        if not items:
            raise LayoutError("state 的 states 为空——至少要有一个状态")
        _tr = spec.get('transitions') or []
        _validate_id_graph(items, _tr, "states")
        # 自环不占层、不占走廊，但每个都要在节点右缘外占 28px；多个自环挤在一
        # 行会把那一行撑爆——超过 2 个基本是模型把「重试」拆成了好几条，合并更好
        _sl = sum(1 for e in _tr if e.get('from') == e.get('to'))
        if _sl > 2:
            raise LayoutError(f"自环过多（{_sl} > 2），请合并或拆分成多张图")
    elif layout == "sequence":
        actors = spec.get('actors') or []
        if len(actors) < 2:
            raise LayoutError("sequence 需要至少 2 个参与者——时序图是双方以上的往来")
        if len(actors) > 5:
            raise LayoutError(f"参与者过多（{len(actors)} > 5），请拆分成多张图")
        _validate_id_graph(actors, spec.get('messages') or [], "actors", edge_max=12)
        if len(spec.get('groups') or []) > 2:
            raise LayoutError("分组过多（> 2），请拆分成多张图")
    elif layout == "quadrant":
        quads = [q for q in (spec.get('quadrants') or []) if isinstance(q, dict)]
        if not any(q.get('items') for q in quads):
            raise LayoutError("quadrant 的 quadrants 至少要有一个象限带 items")
    elif layout == "layers":
        layers = spec.get('layers') or []
        if not layers:
            raise LayoutError("layers 为空——至少要有一层")
        for i, lay in enumerate(layers):
            if not isinstance(lay, dict):
                raise LayoutError(f"layers[{i}] 不是对象")
            if not (lay.get('nodes') or []):
                raise LayoutError(f"layers[{i}]（{lay.get('name', '')}）没有节点")
            lay['name'] = str(lay.get('name', ''))[:12]
    elif layout == "grid":
        rows = spec.get('rows') or []
        if not rows:
            raise LayoutError("rows 为空——至少要有一行节点")
        for r, row in enumerate(rows):
            if not isinstance(row, (list, tuple)) or not row:
                raise LayoutError(f"rows[{r}] 不是非空数组")
        for i, e in enumerate(spec.get('edges') or []):
            if not isinstance(e, dict):
                raise LayoutError(f"edges[{i}] 不是对象")
            for key in ("from", "to"):
                v = e.get(key)
                if (not isinstance(v, (list, tuple)) or len(v) != 2
                        or not all(isinstance(x, int) for x in v)):
                    raise LayoutError(
                        f"edges[{i}] 的 {key} 必须是 [行号, 列号] 两个整数"
                        f"（从 0 起），实际是 {v!r}")
                # 越界定位到具体 edge —— 这是 v1 最易写错的字段，报错必须能直接改
                if not (0 <= v[0] < len(rows)):
                    raise LayoutError(
                        f"edges[{i}] 的 {key} 行号 {v[0]} 越界（共 {len(rows)} 行）")
                if not (0 <= v[1] < len(rows[v[0]])):
                    raise LayoutError(
                        f"edges[{i}] 的 {key} 列号 {v[1]} 越界"
                        f"（rows[{v[0]}] 共 {len(rows[v[0]])} 个节点）")
            # 隔行边：水平段会落在**中间那一行**上横穿节点（_grid 只按相邻行
            # 之间的走廊分道）。v1 先拒绝——阶梯路由留作后续，_grid 的行结构
            # 是已知的，沿列间空隙逐行下降并不难，收益是能表达真实分层架构。
            # ⚠️ 文案**不能**写「请改为逐行连接」：那会诱导模型补一个不存在的
            # 中间节点搭桥，把「图难看」升级成「图画错了（假依赖）」。
            if abs(e['from'][0] - e['to'][0]) > 1:
                raise LayoutError(
                    f"edges[{i}] 跨越了多层（第{e['from'][0]}行→第{e['to'][0]}行）；"
                    "grid 只表达相邻层依赖，跨层依赖请省略该边或拆图")
            if e.get('label'):
                # 截断而非拒绝：边标签不是图的骨架，截断好过丢整张图。
                # 补省略号让读者知道被截了——否则「很长的边标签会被」这种
                # 半句话读起来像模型没写完，而不是渲染器截的。
                _t = str(e['label'])
                e['label'] = _t[:_EDGE_LABEL_MAX - 1] + '…' if len(_t) > _EDGE_LABEL_MAX else _t
    else:
        if not (spec.get('nodes') or []):
            raise LayoutError(f"{layout} 的 nodes 为空——至少要有一个节点")

    n = _count_nodes(spec, layout)
    if n > _MAX_NODES:
        raise LayoutError(f"节点过多（{n} > {_MAX_NODES}），请拆分成多张图")
    return spec


def assert_layout(nodes, W, height, edges=None, labels=None, shapes=None) -> None:
    """几何自检：节点在界内、两两不重叠、边不穿节点、标签不压节点且互不重叠。

    失败 = **布局器自身的 bug**（不是用户输入问题），调用方应记日志。

    ⚠️ ``edges``/``labels``/``shapes`` 可选是给**外部/测试**调用点的兼容，
    但 ``expand()`` 内部那两处**必须传全量** —— 不传就有一条断言都不执行，
    等于写了检查却什么都不查（「断言存在」≠「断言有效」）。
    """
    for n in nodes:
        if n['x'] < 0 or n['y'] < 0 or n['x'] + n['w'] > W or n['y'] + n['h'] > height:
            raise LayoutError(f"布局器 bug：节点越界 {n.get('label')!r}")
    for i, a in enumerate(nodes):
        if a.get('decor'):
            continue                        # 装饰元素（如刻意的层叠标记）豁免重叠
        for b in nodes[i + 1:]:
            if b.get('decor'):
                continue
            if _overlap(a['x'], a['y'], a['w'], a['h'], b['x'], b['y'], b['w'], b['h']):
                raise LayoutError(
                    f"布局器 bug：节点重叠 {a.get('label')!r} / {b.get('label')!r}")

    # 边不穿过节点：段是轴对齐的，故相交判定精确（用整条边的 bbox 判会大面积误报）
    for e in (edges or []):
        end = e.get('end')
        for (x0, y0, x1, y1) in _path_segments(e.get('d')):
            for n in nodes:
                nx0, ny0 = n['x'], n['y']
                nx1, ny1 = nx0 + n['w'], ny0 + n['h']
                if y0 == y1 and x0 != x1:                       # 水平段
                    hit = (ny0 < y0 < ny1                       # 只穿内部，贴边不算
                           and min(x0, x1) < nx1 and nx0 < max(x0, x1))
                elif x0 == x1 and y0 != y1:                     # 垂直段
                    hit = (nx0 < x0 < nx1
                           and min(y0, y1) < ny1 and ny0 < max(y0, y1))
                else:
                    continue
                if not hit:
                    continue
                # 箭头终点必然落在目标节点**边界上**（不进入内部），不会命中；
                # 起点同理。真命中即说明连线横穿了路上的其他节点。
                if end and nx0 <= end[0] <= nx1 and ny0 <= end[1] <= ny1:
                    continue
                raise LayoutError(
                    f"布局器 bug：连线穿过节点 {n.get('label')!r}（路径 {e.get('d')!r}）")

    # 标签遮罩**不透明**（PIL 铺白底），压住节点就是把内容涂掉 ——
    # diagram-design `verify-geometry.py` 的同名检查；其阈值 8–14px 针对它的
    # 遮罩规格，我们固定 16px，判定须自行实现。
    for lb in (labels or []):
        for n in nodes:
            if _overlap(lb['x'], lb['y'], lb['w'], lb['h'],
                        n['x'], n['y'], n['w'], n['h']):
                raise LayoutError(
                    f"布局器 bug：标签遮罩 {lb.get('text')!r} 压住节点 "
                    f"{n.get('label')!r}")
    for i, a in enumerate(labels or []):
        for b in (labels or [])[i + 1:]:
            if _overlap(a['x'], a['y'], a['w'], a['h'],
                        b['x'], b['y'], b['w'], b['h']):
                raise LayoutError(
                    f"布局器 bug：标签遮罩重叠 {a.get('text')!r} / {b.get('text')!r}")

    # 连线与装饰元素的几何范围也必须在画布内（_extent 已把它们算进高度/宽度，
    # 这里是第二道闸——防止将来某个算子绕过 _snapshot 自行定稿）
    for e in (edges or []):
        bb = _path_bbox(e.get('d'))
        if bb and (bb[0] < 0 or bb[1] < 0 or bb[2] > W or bb[3] > height):
            raise LayoutError(f"布局器 bug：连线越界 {e.get('d')!r}")
    for sh in (shapes or []):
        if sh.get('kind') == 'line':
            xs, ys = (sh['x1'], sh['x2']), (sh['y1'], sh['y2'])
        else:
            xs = (sh['x'], sh['x'] + sh['w'])
            ys = (sh['y'], sh['y'] + sh['h'])
        if min(xs) < 0 or min(ys) < 0 or max(xs) > W or max(ys) > height:
            raise LayoutError(f"布局器 bug：装饰元素越界 {sh!r}")


def _trim_width(nodes, labels, W: int, shapes=None, edges=None, min_w: int = 0) -> int:
    """按内容右边界裁剪画布宽度（保留 MARGIN），消除窄图右侧的大片空白。

    固定 640 画布下，timeline / layers 这类内容只占左侧的布局会右半幅全空——
    「先完整画出来再缩放」应当连**宽度**也按内容裁，否则缩放只是等比缩小了空白。
    hchain / grid 本就铺满可用宽度，这里近似空操作。

    **四类元素全部计入**（``_extent``）：只按节点裁会把贴在右侧的边标签切出界，
    更会把超出节点右缘的连线与 shapes 切掉——state 的回边沟槽、象限的轴线框
    都在节点之外。下限 ``_MIN_CANVAS_W`` 防止单节点图被裁得过窄。

    ``min_w``：裁剪**下界**。标题居中绘制、不构成右边界，但画布窄过它就会把
    标题两端裁掉——故它只抬下界，不参与上界。
    """
    ext = _extent(nodes, labels, shapes, edges)
    floor = max(_MIN_CANVAS_W, min_w)
    if ext is None:
        return max(floor, W)
    return max(floor, min(W, int(math.ceil(ext[2])) + MARGIN))


# ── 主入口 ────────────────────────────────────────────────

def expand(spec: dict, width: int = DEFAULT_WIDTH, height_cap: int = DEFAULT_HEIGHT_CAP,
           measure=None) -> dict:
    """语义 spec → 几何布局。

    高度超限时先压缩行距重试（图表本就该紧凑），三轮仍超则按**自然高度**定稿
    并给出等比缩放系数 ``scale`` —— 由 ``chart_svg.render_spec_svg`` 落到
    ``<g transform="scale(k)">``，即「先完整画出来，再缩到目标比例」。
    只有连缩到 ``_MIN_SCALE`` 都还超高（字号已到可读性边缘）才抛 LayoutError。

    返回的 ``height`` 是**自然高度**（**已含标题占位**），可能大于 ``height_cap``；
    实际显示高度 = ``height × scale`` ≤ ``height_cap``。

    ``title`` 非空时所有元素整体下移 ``TITLE_H``，故可用高度是
    ``height_cap - TITLE_H``（640 → 616）—— 这是有意的容量取舍，见 TITLE_H。
    """
    spec = validate_spec(spec)
    layout = spec.get('layout', 'hchain')
    title = str(spec.get('title', ''))[:16]
    title_h = TITLE_H if title else 0
    # 标题居中不参与右边界，但画布窄过它就会把标题裁掉两端 → 只抬裁剪下界
    title_w = int(_measure(title, TITLE_SIZE, measure)) + 2 * MARGIN if title else 0
    W = max(160, int(width or DEFAULT_WIDTH))
    base_gap = _ROW_GAP.get(layout, 56)
    last: LayoutError | None = None
    best = None   # 三轮里最矮的一次成功布局（factor 递减 → 后一次更矮）

    def _snapshot(nodes, edges, labels, shapes=None):
        # 高度按**四类元素的并集**算：只扫 nodes 会漏掉超出节点下缘的连线与
        # shapes（sequence 的生命线正是靠它撑出真实画布高）。
        ext = _extent(nodes, labels, shapes, edges)
        h = (ext[3] if ext else 0) + MARGIN
        if title_h:
            _shift_title_space(nodes, edges, labels, shapes or [], title_h)
            h += title_h
        return (nodes, edges, labels, shapes or [], h)

    for factor in (1.0, 0.5, 0.25):
        gap = max(8, r4(base_gap * factor))   # 行距同样取 4px 网格，y 才不脱格
        try:
            if layout == "grid":
                out = _grid(spec.get('rows') or [], spec.get('edges') or [],
                            W, measure, gap,
                            align=(spec.get('align') == 'columns'))
            elif layout == "layers":
                out = _layers(spec.get('layers') or [], W, measure, gap)
            elif layout in _SPEC_RUNNERS:      # 入参是整份 spec，见 layouts 的注册表注释
                out = _SPEC_RUNNERS[layout](spec, W, measure, gap)
            else:
                out = _NODES_RUNNERS[layout](spec.get('nodes') or [], W, measure, gap)
            # 算子可选返回第 4 项 shapes（装饰性线条/框，见 2.2）——旧算子返回
            # 3 元组，此处统一补齐，行为零变化。
            nodes, edges, labels = out[0], out[1], out[2]
            shapes = out[3] if len(out) > 3 else []
        except LayoutError as e:
            last = e
            continue
        nodes, edges, labels, shapes, height = _snapshot(nodes, edges, labels, shapes)
        best = (nodes, edges, labels, shapes, height)
        if height <= height_cap:
            W2 = _trim_width(nodes, labels, W, shapes=shapes, edges=edges, min_w=title_w)
            # ⚠️ 必须传全量：不传则后三条不变量一条都不执行（见 assert_layout 文档）
            assert_layout(nodes, W2, height, edges, labels, shapes)
            _shrink_labels(nodes, measure)   # 定稿后按最终宽度兜底截断文字
            return {"width": W2, "height": height, "scale": 1.0,
                    "title": title, "title_h": title_h,
                    "nodes": nodes, "edges": edges, "labels": labels, "shapes": shapes}
        last = LayoutError(f"布局高度 {height}px 超出 {height_cap}px")

    if best is None:
        # 三轮都没产出布局 = **结构性问题**（有环 / 树太宽 / 排不下），不是高度
        # 问题：压缩行距只会改变高度，改变不了结构。此时必须**直抛算子给出的
        # 原因**——套一层「节点过多，图过高」会把真实原因埋进括号里，用户照着
        # 这个提示去拆图，问题一点没解决（2026-09-19 压测实测：3 个节点带环的
        # flowchart 报的是「节点过多」）。
        raise last if last is not None else LayoutError("布局失败：内容无法排布")

    nodes, edges, labels, shapes, height = best
    scale = height_cap / height
    if scale < _MIN_SCALE:
        raise LayoutError(
            f"节点过多，图过高（自然高 {height}px，需缩到 {scale:.0%} 才能放下，"
            f"低于 {_MIN_SCALE:.0%} 下限会看不清），请拆分成多张图")
    W2 = _trim_width(nodes, labels, W, shapes=shapes, edges=edges, min_w=title_w)
    # 在**自然坐标**下校验（几何正确性与缩放无关）
    assert_layout(nodes, W2, height, edges, labels, shapes)
    _shrink_labels(nodes, measure)
    return {"width": W2, "height": height, "scale": round(scale, 4),
            "title": title, "title_h": title_h,
            "nodes": nodes, "edges": edges, "labels": labels, "shapes": shapes}
