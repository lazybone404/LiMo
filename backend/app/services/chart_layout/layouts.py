"""布局算子：把语义 spec 排布成几何。

算子签名统一为 ``(容器, W, measure, row_gap) -> (nodes, edges, labels[, shapes])``，
第 4 项 ``shapes`` 可选（装饰性线条/框，绘制顺序见 ``chart_svg``）。

**职责边界**：本模块只管「怎么排」，不判断输入是否合法 —— ``validate_spec``
在 engine 里先跑。算子抛出的 ``LayoutError`` 都是「排不下」这类几何结论。

**两张注册表**（``expand`` 按它分派）：``_NODES_RUNNERS`` 收「扁平节点数组」
签名的算子，``_SPEC_RUNNERS`` 收需要读整份 spec 的算子（tree 的 root 是嵌套
结构、flowchart/state 的边按 id 引用、sequence 有 messages）。``grid`` /
``layers`` 的入参结构又各自不同（行 / 层容器），由 ``expand`` 直接特判。
"""

from .geometry import (
    GAP,
    GAP_X,
    H,
    MARGIN,
    UNIT,
    LayoutError,
    _GUTTER_W,
    _distribute,
    _edge_path,
    _elbow,
    _fit_labels,
    _GRID_GAP_MAX,
    _make_edge,
    _make_node,
    _measure,
    _MIN_NODE_W,
    _overlap,
    _shape_min_w,
    _truncate_to_width,
    _wrap_rows,
    node_min_w,
    r4,
    r4_down,
    shape_h,
)

# ── 各布局算子 ────────────────────────────────────────────

def _norm_node(it) -> dict:
    """节点归一化：纯字符串 = {"label": s}。"""
    return it if isinstance(it, dict) else {"label": str(it)}


def _hchain(nodes, W, measure=None, row_gap=72):
    """单向链，宽度不足时自动折成多排蛇形（吸收原 hchain2）。

    对 LLM 只有一个语义（一串顺序节点），折行是内部实现细节——少一个字段
    就少一类模型出错面。
    """
    avail = W - 2 * MARGIN
    if not nodes:
        return [], [], []
    ws, fits = [], []
    for n in nodes:
        lb2, sb2, w = _fit_labels(n.get('label', ''), n.get('sub'), avail, measure)
        fits.append((lb2, sb2, w))
        ws.append(w)
    rows = _wrap_rows(ws, GAP, avail, measure)
    multi = len(rows) > 1          # 折行后不放大，各排节点宽度才一致
    placed = []
    y = MARGIN + 24                # 原实现单排 y=40；MARGIN=16 → +24
    for ri, idxs in enumerate(rows):
        rws = [ws[i] for i in idxs]
        xs, rws = _distribute(rws, GAP, avail, center=True, grow=not multi)
        if ri % 2:                 # 奇数排镜像：末→首垂直下落才自然衔接
            xs = [W - x - w for x, w in zip(xs, rws)]
        for i, x, w in zip(idxs, xs, rws):
            placed.append((i, x, y, w))
        y += H + row_gap
    outs = []
    for i, x, yy, w in placed:
        lb, sb, _ = fits[i]
        outs.append({"x": x, "y": yy, "w": w, "h": H, "label": lb, "sub": sb,
                     "focus": bool(nodes[i].get('focus', False))})
    edges = [_make_edge(outs[i], outs[i + 1]) for i in range(len(outs) - 1)]
    return outs, edges, []


def _layers(layers, W, measure=None, row_gap=20):
    """分层图：每层一行（左侧层名 + 右侧横排节点），用于架构/模块分层总览。"""
    outs, edges, labels = [], [], []
    LBL_W = 96
    x0 = MARGIN + LBL_W
    avail = W - MARGIN - x0
    y = MARGIN + 8
    for lay in layers:
        items = [_norm_node(i) for i in (lay.get('nodes') or [])]
        labels.append({"x": MARGIN, "y": y + 14, "w": LBL_W - 12, "h": 16,
                       "text": str(lay.get('name', ''))})
        for idxs in _wrap_rows(
                [node_min_w(i.get('label', ''), i.get('sub'), measure) for i in items],
                GAP, avail, measure):
            rws = [node_min_w(items[i].get('label', ''), items[i].get('sub'), measure)
                   for i in idxs]
            # 左对齐 + 不放大：各层左边缘对齐成一条线
            xs, rws = _distribute(rws, GAP, avail, x0=x0, center=False, grow=False)
            for i, x, w in zip(idxs, xs, rws):
                it = items[i]
                lb, sb, w2 = _fit_labels(it.get('label', ''), it.get('sub'), avail, measure)
                outs.append({"x": x, "y": y, "w": w2, "h": H, "label": lb, "sub": sb,
                             "focus": bool(it.get('focus', False))})
            y += H + row_gap
        y += row_gap
    return outs, [], labels


def _timeline(nodes, W, measure=None, row_gap=16):
    """竖向时间轴：左侧时间标签列（取 sub），右侧顺序节点自上而下。

    原实现没有此布局，但它是视频笔记的核心诉求——时间戳序列用竖排最自然，
    且竖排天然规避 640 宽度上限（单列只占一行节点宽）。节点等宽以保证
    连线是干净的垂直线。
    """
    TIME_W = 64
    x0 = MARGIN + TIME_W + 16
    avail = W - MARGIN - x0
    outs, edges, labels = [], [], []
    if not nodes:
        return outs, edges, labels
    maxw = max(node_min_w(n.get('label', ''), None, measure) for n in nodes)
    maxw = r4(max(_MIN_NODE_W, min(maxw, avail)))
    y = MARGIN
    for n in nodes:
        lb, _, _ = _fit_labels(n.get('label', ''), None, maxw, measure)
        sub = n.get('sub')
        if sub:
            labels.append({"x": MARGIN, "y": y + H // 2 - 8, "w": TIME_W - 8, "h": 16,
                           "text": str(sub)[:12], "anchor": "end"})
        outs.append({"x": x0, "y": y, "w": maxw, "h": H, "label": lb, "sub": None,
                     "focus": bool(n.get('focus', False))})
        y += H + row_gap
    edges = [_make_edge(outs[i], outs[i + 1]) for i in range(len(outs) - 1)]
    return outs, edges, labels


def _branch(nodes, W, measure=None, row_gap=60):
    """判定链：左判定 → 右结果，判定之间纵向「否」路径。

    v1 不写进 prompt（可被 hchain + focus 覆盖），但实现并单测，免得成为死代码。
    """
    rw = 140
    lx, rx = MARGIN, W - MARGIN - rw
    lw = max(_MIN_NODE_W, rx - GAP - lx)
    outs, edges = [], []
    for i, n in enumerate(nodes):
        y = MARGIN + i * row_gap
        lb, sb, _ = _fit_labels(n.get('label', ''), n.get('sub'), lw, measure)
        outs.append({"x": lx, "y": y, "w": lw, "h": H, "label": lb, "sub": sb,
                     "focus": False})
        rb, rs, _ = _fit_labels(n.get('result', ''), n.get('result_sub'), rw, measure)
        outs.append({"x": rx, "y": y, "w": rw, "h": H, "label": rb, "sub": rs,
                     "focus": bool(n.get('result_focus', False))})
    for i in range(0, len(outs), 2):
        edges.append(_make_edge(outs[i], outs[i + 1]))
        if i + 2 < len(outs):
            edges.append(_make_edge(outs[i], outs[i + 2]))
    return outs, edges, []


# ── tree：组织 / 分类 / 目录 ───────────────────────────────

def _norm_tree(it):
    """树节点归一化：纯字符串 = ``{"label": s}``；递归 ``children``。

    父子关系**内嵌在结构里**，不需要 id / parent / edges —— 对 LLM 少一个字段
    就少一类出错面（与 ``_hchain`` 把折行藏进实现同一哲学）。
    """
    if not isinstance(it, dict):
        it = {"label": str(it)}
    return {**it, 'children': [_norm_tree(c) for c in (it.get('children') or [])]}


def _tree_stats(node):
    """``(节点数, 最大深度)``（根深度 = 1）—— validate_spec 在布局前拦下过大的树。"""
    kids = node.get('children') or []
    if not kids:
        return 1, 1
    sub = [_tree_stats(c) for c in kids]
    return 1 + sum(s[0] for s in sub), 1 + max(s[1] for s in sub)


# tree 只认 rect / stadium（两者同为 44px 高）—— 层高按常量 H 累加，若放行
# diamond(64) 就会在行距被压到 8px 时与下一层重叠。判定形状在 tree 里本无语义，
# 静默归一为 rect 比报错更合适（图仍然对，只是不是菱形）。
_TREE_SHAPES = ("rect", "stadium")


def _tree_self_w(node, measure, scale: float) -> None:
    """自底向上填 ``self_w``：按形状算的自宽 × 缩放系数（就地）。"""
    sh = node.get('shape') or 'rect'
    base = _shape_min_w(node.get('label', ''), node.get('sub'), measure,
                        sh if sh in _TREE_SHAPES else 'rect')
    node['self_w'] = max(_MIN_NODE_W, r4(base * scale))
    for c in node['children']:
        _tree_self_w(c, measure, scale)


def _tree_need(node, sib: int) -> int:
    """子树占用的横向总宽 = max(自身宽, 子节点总宽)。"""
    kids = node['children']
    if not kids:
        return node['self_w']
    total = sum(_tree_need(c, sib) for c in kids) + sib * (len(kids) - 1)
    return max(node['self_w'], total)


def _tree(spec, W, measure=None, row_gap=64):
    """组织树 / 分类树 / 目录：父在上子在下，同父的多个子共用一条水平母线。

    宽度自底向上算（子树宽 = max(自身宽, 子宽之和)），超宽时**三级降级**：
    压兄弟间距 → 等比收缩所有节点 → 报错请拆图。等比而非只缩某一层，是
    为了保持各层相对比例（单层收缩会让父子宽度倒挂，看起来像画错了）。
    """
    avail = W - 2 * MARGIN
    root = _norm_tree(spec.get('root') or {})
    if not root.get('label') and not root['children']:
        raise LayoutError("tree 的 root 为空——至少要有 root.label")

    sib = GAP
    _tree_self_w(root, measure, 1.0)
    if _tree_need(root, sib) > avail:        # 降级 ①：压兄弟间距
        sib = max(8, GAP // 2)
    if _tree_need(root, sib) > avail:
        # 降级 ②：等比收缩。**二分**而非一次算 ``avail/need`` —— 节点宽有
        # ``_MIN_NODE_W`` 下限（触底的节点不再跟着缩），一次算出的 k 会让总宽
        # 与预估不符；而 ``need`` 对 k 单调不减（k↑ → 各节点宽↑ → 总宽↑），
        # 二分必收敛。12 次足以把 k 定位到 0.03% 以内。
        lo, hi = 0.0, 1.0
        for _ in range(12):
            mid = (lo + hi) / 2
            _tree_self_w(root, measure, mid)
            if _tree_need(root, sib) <= avail:
                hi = mid
            else:
                lo = mid
        _tree_self_w(root, measure, hi)     # hi 保持 1.0 表示二分没找到可行解
        if _tree_need(root, sib) > avail:   # 降级 ③：连触底宽度都放不下 → 放弃
            raise LayoutError("树太宽，请拆分成多张图（再缩文字就看不清了）")

    outs, edges = [], []

    def _place(node, left, depth):
        sw = _tree_need(node, sib)
        y = MARGIN + depth * (H + row_gap)
        x = left + r4_down((sw - node['self_w']) / 2)
        sh = node.get('shape') or 'rect'
        n = _make_node(node, x, y, node['self_w'], measure,
                       shape=sh if sh in _TREE_SHAPES else 'rect')
        outs.append(n)
        kids = node['children']
        if not kids:
            return n
        child_y = MARGIN + (depth + 1) * (H + row_gap)
        total = sum(_tree_need(c, sib) for c in kids) + sib * (len(kids) - 1)
        bus = r4_down((y + n['h'] + child_y) / 2)   # 母线取父底与子顶的中线
        cx = left + r4_down((sw - total) / 2)
        for c in kids:
            edges.append(_elbow(n, _place(c, cx, depth + 1), bus))
            cx += _tree_need(c, sib) + sib
        return n

    _place(root, MARGIN + r4_down((avail - _tree_need(root, sib)) / 2), 0)
    return outs, edges, []


# ── flowchart：带判定的流程 ────────────────────────────────

_FLOW_SHAPES = {"start": "stadium", "end": "stadium",
                "decision": "diamond", "process": "rect"}


def _topo_layers(ids, pairs):
    """Kahn 拓扑分层 → ``(layer_of, leftover)``。

    **最长路径分层**：``layer[v] = 1 + max(layer[u])``（u 遍历 v 的前驱）。它保证
    每条边都指向更深的层，但 ``layer[v] - layer[u]`` 可能 > 1（跳层边）——那正是
    需要沟槽路由的情形。

    ``leftover`` 返回成环的节点而非一个布尔：报错要能点名是谁成环，否则用户
    只看到「有环」却不知道从哪改。
    """
    adj = {i: [] for i in ids}
    indeg = {i: 0 for i in ids}
    for u, v in pairs:
        if u in adj and v in indeg:
            adj[u].append(v)
            indeg[v] += 1
    layer = {i: 0 for i in ids}
    queue = [i for i in ids if indeg[i] == 0]
    while queue:
        u = queue.pop(0)
        for v in adj[u]:
            if layer[u] + 1 > layer[v]:
                layer[v] = layer[u] + 1
            indeg[v] -= 1
            if indeg[v] == 0:
                queue.append(v)
    return layer, [i for i in ids if indeg[i] > 0]


def _flowchart(spec, W, measure=None, row_gap=48):
    """带判定的流程：拓扑分层 → 层内按前驱重心排序 → 走廊分道 → 沟槽绕行。

    **跳层边**（如 start 直达 end，或两条长短不一的路径汇合）走右侧纵向沟槽：
    先降到起点层下方的走廊，水平移到沟槽，沿沟槽下降，再从目标层**上方**的走廊
    进入目标节点。中间层的节点因此永远不被穿过。

    这比「拒绝跳层边」好：短路径汇合在真实流程里很常见，拒绝会逼模型补一个
    不存在的中间节点搭桥，把「图不好看」升级成「图画错了（假依赖）」。
    """
    items = [_norm_node(i) for i in (spec.get('nodes') or [])]
    ids = [str(i.get('id')) for i in items]
    idx = {_id: k for k, _id in enumerate(ids)}
    raw = [e for e in (spec.get('edges') or []) if isinstance(e, dict)]
    pairs = [(str(e['from']), str(e['to'])) for e in raw]

    layer, cyc = _topo_layers(ids, pairs)
    if cyc:
        raise LayoutError(
            f"流程存在环（{'、'.join(str(c) for c in cyc[:3])}）——"
            "有回环/自环的结构请改用 state 布局")
    nlayer = max(layer.values()) + 1
    # ⚠️ 这里**不再**有「层数过深（> N）」的闸门（2026-10-20 删）：高度是否装得下
    # 由 `engine.expand()` 的「压行距（三轮）+ 等比缩放 + `_MIN_SCALE`」统一裁决，
    # 那是唯一真相源。写死一个层数阈值的直接后果是**与缩放链不同源**且严格更严 ——
    # 实测 8 层纯矩形只需 564px（scale=1.0），却被判「层数过深」。

    def _shp(it):
        return _FLOW_SHAPES.get(str(it.get('type') or 'process'), "rect")

    avail = W - 2 * MARGIN - _GUTTER_W          # 节点区，右侧给沟槽留位
    ws = [_shape_min_w(it.get('label', ''), it.get('sub'), measure, _shp(it))
          for it in items]
    row_h = [0] * nlayer
    members: list[list[int]] = [[] for _ in range(nlayer)]
    for k, _id in enumerate(ids):
        r = layer[_id]
        members[r].append(k)
        row_h[r] = max(row_h[r], shape_h(_shp(items[k]), items[k].get('sub')))

    # 每条边的水平段落在**起点层下方**那条走廊 —— 走廊高度据此撑开
    corr: dict[int, int] = {}
    for u, v in pairs:
        if layer[v] > layer[u]:
            corr[layer[u]] = corr.get(layer[u], 0) + 1
    row_y: list[int] = []
    y = MARGIN
    for r in range(nlayer):
        row_y.append(y)
        y += row_h[r] + (max(row_gap, corr.get(r, 0) * _CORRIDOR_PITCH)
                         if r + 1 < nlayer else 0)

    # 层内 x：逐层「按前驱重心排序 → 均分」。重心只依赖上一层（已定稿），
    # 所以一遍扫描即可，且连线交叉最少。无前驱的节点排在层尾保持输入序。
    preds: dict[str, list[str]] = {i: [] for i in ids}
    for u, v in pairs:
        preds[v].append(u)
    x_of = [0] * len(items)
    for r, mem in enumerate(members):
        if r > 0:
            def _bary(k):
                ps = preds[ids[k]]
                if not ps:
                    return (1, 0.0, k)
                return (0, sum(x_of[idx[p]] + ws[idx[p]] / 2 for p in ps) / len(ps), k)
            mem.sort(key=_bary)
        xs, rws = _distribute([ws[k] for k in mem], GAP, avail,
                              center=True, grow=False)
        for k, x, w2 in zip(mem, xs, rws):
            x_of[k], ws[k] = x, w2

    def _lane(k: int, j: int, n: int) -> int:
        top = row_y[k] + row_h[k]
        bot = row_y[k + 1] if k + 1 < nlayer else top + row_gap
        return r4_down(top + (bot - top) * (j + 1) / (n + 1))

    buckets: dict[int, list[int]] = {}
    for i, (u, v) in enumerate(pairs):
        if layer[v] > layer[u]:
            buckets.setdefault(layer[u], []).append(i)
    lane: dict[int, int] = {}
    for k, idxs in buckets.items():
        for j, i in enumerate(idxs):
            lane[i] = _lane(k, j, len(idxs))

    nodes = [_make_node(items[k], x_of[k], row_y[layer[ids[k]]], ws[k], measure,
                        shape=_shp(items[k]))
             for k in range(len(items))]

    gx = W - MARGIN - _GUTTER_W // 2            # 沟槽中线
    edges, labels = [], []
    for i, e in enumerate(raw):
        u, v = pairs[i]
        a, b = nodes[idx[u]], nodes[idx[v]]
        ly = lane[i]
        acx, bxc = a['x'] + a['w'] // 2, b['x'] + b['w'] // 2
        if layer[v] == layer[u] + 1:            # 相邻层：走廊里直接拐过去
            d = f"M{acx} {a['y'] + a['h']} V{ly} H{bxc} V{b['y']}"
            mid_x = (acx + bxc) // 2
        else:                                    # 跳层：借沟槽绕行
            entry = r4_down((row_y[layer[v] - 1] + row_h[layer[v] - 1]
                             + row_y[layer[v]]) / 2)
            d = (f"M{acx} {a['y'] + a['h']} V{ly} H{gx} V{entry}"
                 f" H{bxc} V{b['y']}")
            mid_x = (acx + gx) // 2
        edges.append({"d": d, "end": (bxc, b['y']), "dir": (0, 1),
                      "dashed": bool(e.get('dashed')), "arrow": True})
        if e.get('label'):
            lb = _grid_edge_label(a, b, str(e['label']), W, measure,
                                  lane_y=ly, mid_x=mid_x)
            if lb:
                labels.append(lb)
    return nodes, edges, labels


# ── state：状态机（允许环与自环）────────────────────────────

_STATE_SHAPES = {"start": "stadium", "end": "stadium",
                 "state": "rect", "decision": "diamond"}
_LOOP_W = 28            # 自环在节点右缘之外占的宽度（不占位就会压到右邻节点）
_LOOP_H = 20            # 自环环路的高度


def _bfs_rank(ids, pairs):
    """有向图的 **BFS 分层**（首次到达距离）。

    与 flowchart 的 ``_topo_layers`` 相反：状态机允许环，拓扑排序根本排不了。
    BFS 有个恰好合用的性质——每条边必满足 ``rank[v] <= rank[u] + 1``，于是
    **前向边必然是相邻层**（没有跳层边），走廊分道与沟槽路由的分工因此是干净的：
    向下的边走走廊，向上/自环走沟槽。

    无入度 0 的源（整个图都是环）时取首个节点当种子，否则那些节点会全部落空。
    """
    adj = {i: [] for i in ids}
    indeg = {i: 0 for i in ids}
    for u, v in pairs:
        if u in adj and v in adj and u != v:
            adj[u].append(v)
            indeg[v] += 1
    seeds = [i for i in ids if indeg[i] == 0] or ids[:1]
    rank = {s: 0 for s in seeds}
    queue = list(seeds)
    while queue:
        u = queue.pop(0)
        for v in adj[u]:
            if v not in rank:
                rank[v] = rank[u] + 1
                queue.append(v)
    for i in ids:
        rank.setdefault(i, 0)
    return rank


def _state(spec, W, measure=None, row_gap=56):
    """状态机：BFS 分层 → 层内重心排序 → 前向边走走廊 / 回边与自环绕右侧沟槽。

    ``type == "start"/"end"`` 的状态会各插一个 ``shape="circle"`` 的空 label 节点
    （UML 初态/终态的实心圆点），它们各占一行、不参与内容计数。

    **允许环**（这正是它与 flowchart 的分工）：回边不走「拒绝」，而是从源节点
    底部下到本层走廊 → 沿右侧沟槽上/下行 → 从目标层**上方**的走廊进入目标。
    路径的每一段都落在节点之外：走廊里没有节点，沟槽在节点区右侧之外，
    进入目标的竖线在目标正上方——所以回边永远不会穿过中间层的节点。
    目标在最顶层时改从**下方**进入（最顶层上面没有走廊），箭头方向随之取反。
    """
    raw_states = [_norm_node(i) for i in (spec.get('states') or [])]
    items, dots = [], []
    for it in raw_states:
        _id = str(it.get('id'))
        _t = str(it.get('type') or 'state')
        if _t == 'start':
            items.append({"id": _id + "__pre", "label": "", "shape": "circle"})
            dots.append((_id + "__pre", _id))
        items.append({**it, "id": _id, "label": it.get('label', ''),
                      "shape": _STATE_SHAPES.get(_t, "rect")})
        if _t == 'end':
            items.append({"id": _id + "__post", "label": "", "shape": "circle"})
            dots.append((_id, _id + "__post"))

    raw = [e for e in (spec.get('transitions') or []) if isinstance(e, dict)]
    # 虚拟圆点边与真实转移混在一张表里 —— 后面按下标取 label/dashed 才不会错位
    all_edges = [{"u": u, "v": v, "label": None, "dashed": False} for u, v in dots]
    all_edges += [{"u": str(e['from']), "v": str(e['to']),
                   "label": e.get('label'), "dashed": bool(e.get('dashed'))}
                  for e in raw]

    ids = [it['id'] for it in items]
    idx = {_id: k for k, _id in enumerate(ids)}
    pairs = [(e['u'], e['v']) for e in all_edges]
    rank = _bfs_rank(ids, pairs)
    nrow = max(rank.values()) + 1
    # ⚠️ 同样**不再**有「层数过深（> 7）」的闸门（2026-10-20 删）：高度由
    # `engine.expand()` 的压行距 + 等比缩放 + `_MIN_SCALE` 统一裁决。这条阈值与
    # 缩放链不同源且严格更严 —— 实测 8 层纯矩形只需 564px（scale=1.0）就装得下，
    # 却被判「层数过深」。自然上限约 13 层，真装不下时缩放链会报「图过高，请拆图」。

    self_loops = {u for u, v in pairs if u == v}
    # 节点区再往左收一档：自环占右缘外 28px，回边占更外侧的沟槽
    avail = W - 2 * MARGIN - _LOOP_W - _GUTTER_W

    row_h = [0] * nrow
    members: list[list[int]] = [[] for _ in range(nrow)]
    for k, _id in enumerate(ids):
        r = rank[_id]
        members[r].append(k)
        row_h[r] = max(row_h[r], shape_h(items[k]['shape'], items[k].get('sub')))

    # 只有前向边（必然相邻层，见 _bfs_rank）走行间走廊，回边/自环不进走廊
    corr: dict[int, int] = {}
    for u, v in pairs:
        if u != v and rank[v] == rank[u] + 1:
            corr[rank[u]] = corr.get(rank[u], 0) + 1
    row_y: list[int] = []
    y = MARGIN
    for r in range(nrow):
        row_y.append(y)
        y += row_h[r] + (max(row_gap, corr.get(r, 0) * _CORRIDOR_PITCH)
                         if r + 1 < nrow else 0)

    ws = [_shape_min_w(items[k].get('label', ''), items[k].get('sub'), measure,
                       items[k]['shape']) for k in range(len(items))]
    preds: dict[str, list[str]] = {}
    for u, v in pairs:
        if u != v:
            preds.setdefault(v, []).append(u)
    x_of = [0] * len(items)
    for r, mem in enumerate(members):
        if r > 0:
            def _bary(k):
                ps = preds.get(ids[k], [])
                if not ps:
                    return (1, 0.0, k)
                return (0, sum(x_of[idx[p]] + ws[idx[p]] / 2 for p in ps) / len(ps), k)
            mem.sort(key=_bary)
        # 自环节点按 w + _LOOP_W 占位 —— 环路画在节点右缘之外，不预留就是压邻节点
        pad = {k: (_LOOP_W if ids[k] in self_loops else 0) for k in mem}
        xs, pws = _distribute([ws[k] + pad[k] for k in mem], GAP, avail,
                              center=True, grow=False)
        for k, x, pw in zip(mem, xs, pws):
            x_of[k], ws[k] = x, pw - pad[k]

    nodes = [_make_node(items[k], x_of[k], row_y[rank[ids[k]]], ws[k], measure,
                        shape=items[k]['shape'])
             for k in range(len(items))]

    def _below(r):
        """第 r 行**下方**走廊的中线 y（末行则凭空取半格，让回边有地方拐）。"""
        if r + 1 < nrow:
            return r4_down((row_y[r] + row_h[r] + row_y[r + 1]) / 2)
        return r4_down(row_y[r] + row_h[r] + row_gap / 2)

    # 每条走廊里的前向边再分车道 —— 与 grid 同理：不分道时同走廊的边水平段
    # 全落在同一个 y 上，画面上并成一条长横线、标签叠成一团
    buckets: dict[int, list[int]] = {}
    for i, e in enumerate(all_edges):
        if e['u'] != e['v'] and rank[e['v']] == rank[e['u']] + 1:
            buckets.setdefault(rank[e['u']], []).append(i)
    lane: dict[int, int] = {}
    for r, idxs in buckets.items():
        top, bot = row_y[r] + row_h[r], row_y[r + 1]
        for j, i in enumerate(idxs):
            lane[i] = r4_down(top + (bot - top) * (j + 1) / (len(idxs) + 1))

    edges, labels = [], []
    back_n = 0
    for i, e in enumerate(all_edges):
        u, v = e['u'], e['v']
        a, b = nodes[idx[u]], nodes[idx[v]]
        acx, bxc = a['x'] + a['w'] // 2, b['x'] + b['w'] // 2
        lane_y, mid_x = None, None
        if u == v:                                   # 自环：右缘外一个小环路
            rx = a['x'] + a['w']
            cy = r4_down((a['y'] + a['h'] / 2))
            d = f"M{rx} {cy} H{rx + _LOOP_W} V{cy - _LOOP_H} H{rx}"
            end, dr = (rx, cy - _LOOP_H), (-1, 0)
        elif rank[v] == rank[u] + 1:                 # 前向：走廊里直接拐过去
            ly = lane[i]
            d = f"M{acx} {a['y'] + a['h']} V{ly} H{bxc} V{b['y']}"
            end, dr = (bxc, b['y']), (0, 1)
            lane_y, mid_x = ly, (acx + bxc) // 2
        else:                                        # 回边：绕右侧沟槽
            back_n += 1
            gx = W - MARGIN - _GUTTER_W + 4 + (back_n - 1) * 6
            corr_y = _below(rank[u])
            if rank[v] > 0:                          # 从目标层**上方**进入
                entry = r4_down((row_y[rank[v] - 1] + row_h[rank[v] - 1]
                                 + row_y[rank[v]]) / 2)
                end, dr = (bxc, b['y']), (0, 1)
            else:                                    # 目标在最顶层：上面没走廊，改从下方进
                entry = _below(rank[v])
                end, dr = (bxc, b['y'] + b['h']), (0, -1)
            d = (f"M{acx} {a['y'] + a['h']} V{corr_y} H{gx} V{entry}"
                 f" H{bxc} V{end[1]}")
            lane_y, mid_x = corr_y, (acx + gx) // 2
        edges.append({"d": d, "end": end, "dir": dr,
                      "dashed": bool(e.get('dashed')), "arrow": True})
        if e.get('label'):
            txt = str(e['label'])
            lb = _grid_edge_label(a, b, txt, W, measure, lane_y=lane_y, mid_x=mid_x,
                                  nodes=nodes)
            if lb is None and u == v:
                # 自环的「重试」类标签：环路在节点右缘外，_grid_edge_label 的
                # 同行分支按两心跨度钳制（自环跨度为 0）必然丢弃 —— 手放，
                # 且逐个查一遍不压任何节点
                _w = int(_measure(txt, 11, measure)) + 20
                _x, _y = a['x'] + a['w'] + _LOOP_W + 4, max(0, r4_down(a['y'] + a['h'] / 2) - 8)
                if _x + _w <= W - MARGIN and not any(
                        _overlap(_x, _y, _w, 16, n['x'], n['y'], n['w'], n['h'])
                        for n in nodes):
                    lb = {"x": _x, "y": _y, "w": _w, "h": 16, "text": txt}
            if lb:
                labels.append(lb)
    return nodes, edges, labels


# ── sequence：时序图（消息顺序即时间顺序）──────────────────

_SEQ_MIN_PITCH = 24     # 相邻消息的最小间距（标签遮罩 16px + 8 间隙）


def _sequence(spec, W, measure=None, row_gap=48):
    """时序图：参与者顶部一行 + 生命线 + 水平消息（按数组顺序自上而下）。

    ``messages`` 的**数组顺序就是时间顺序**——不给模型 ``y`` 字段，与 tree 把
    父子关系内嵌进结构同一策略：少一个字段就少一类出错面。

    生命线与分组框走 ``shapes``（``_extent`` 已把它们计入高度，见 1.4）——
    它们是装饰性的线/框，不该占节点数、也不该参与节点重叠校验。

    高度生长由现成的行距压缩机制吸收：``pitch = max(24, row_gap)``，12 条消息
    第一轮（48px）约 688px、第二轮（24px）落到约 400px，多数情况走不到等比缩放。
    """
    actors = [_norm_node(a) for a in (spec.get('actors') or [])]
    msgs = [m for m in (spec.get('messages') or []) if isinstance(m, dict)]
    ids = [str(a.get('id')) for a in actors]
    idx = {_id: k for k, _id in enumerate(ids)}
    n = len(actors)

    avail = W - 2 * MARGIN
    # 盒宽上限：解 ``step >= w + 12``（step 才是中心距，不是 avail/(n-1)——中心
    # 区间被两端各半个盒宽挤掉了）。写成 ``avail ≥ n·w + 12(n-1)`` 更直观：
    # n 个盒 + n-1 个 12px 间隙，横向总预算就是 avail。
    cap = int((avail - 12 * (n - 1)) / n)
    if cap < _MIN_NODE_W:
        raise LayoutError(f"参与者过多（{n} 个）横向排不下，请拆分成多张图")
    # 参与者盒**等宽**（时序图惯例，也让生命线上下对齐成一条）：取自然宽的最大值
    # 再统一压到 cap。宽窄不一时最宽的那个会顶到邻盒上。
    nat = max(_shape_min_w(a.get('label', ''), a.get('sub'), measure) for a in actors)
    w = min(nat, cap)
    half = w / 2
    span = avail - w                        # 中心区间 = [MARGIN+w/2, W-MARGIN-w/2]
    step = span / (n - 1) if n > 1 else 0
    cxs = [r4_down(MARGIN + half + i * step) for i in range(n)]
    ws = [w] * n

    y0 = MARGIN
    nodes = [_make_node(actors[k], r4_down(cxs[k] - half), y0, ws[k], measure)
             for k in range(n)]

    pitch = max(_SEQ_MIN_PITCH, row_gap)
    top = y0 + H + pitch                    # 第一条消息的 y
    msg_y = [top + k * pitch for k in range(len(msgs))]
    bottom = (msg_y[-1] + pitch) if msg_y else (top + pitch)

    shapes = []
    for k in range(n):                      # 生命线：从盒底到图底
        cx = nodes[k]['x'] + nodes[k]['w'] // 2
        shapes.append({"kind": "line", "x1": cx, "y1": nodes[k]['y'] + nodes[k]['h'],
                       "x2": cx, "y2": bottom, "stroke": "muted", "dashed": True})

    edges, labels = [], []
    for k, m in enumerate(msgs):
        a, b = nodes[idx[str(m['from'])]], nodes[idx[str(m['to'])]]
        acx, bcx = a['x'] + a['w'] // 2, b['x'] + b['w'] // 2
        y = msg_y[k]
        edges.append({"d": f"M{acx} {y} H{bcx}", "end": (bcx, y),
                      "dir": (1, 0) if bcx >= acx else (-1, 0),
                      "dashed": bool(m.get('dashed')), "arrow": True})
        # 序号由布局器加：模型写「消息文本」，编号是版面事实，不该让它去数
        txt = f"{k + 1}. {m.get('text') or ''}".strip()
        if txt and txt != f"{k + 1}.":
            _w = int(_measure(txt, 11, measure)) + 20
            if _w <= avail:
                _x = max(MARGIN, min((acx + bcx) // 2 - _w // 2, W - MARGIN - _w))
                labels.append({"x": _x, "y": max(0, y - 20), "w": _w, "h": 16,
                               "text": txt})

    if msgs:
        for g in (spec.get('groups') or [])[:2]:
            if not isinstance(g, dict):
                continue
            try:
                m0, m1 = int(g.get('from', 0)), int(g.get('to', 0))
            except (TypeError, ValueError):
                continue
            m0, m1 = max(0, min(m0, len(msgs) - 1)), max(0, min(m1, len(msgs) - 1))
            if m0 > m1:
                m0, m1 = m1, m0
            gy0, gy1 = msg_y[m0] - 24, msg_y[m1] + 24
            shapes.append({"kind": "rect", "x": MARGIN, "y": gy0,
                           "w": W - 2 * MARGIN, "h": gy1 - gy0,
                           "stroke": "muted", "dashed": True})
            _t = str(g.get('name', ''))[:8]
            if _t:
                _w = int(_measure(_t, 11, measure)) + 20
                labels.append({"x": MARGIN, "y": max(0, gy0 - 16), "w": _w, "h": 16,
                               "text": _t})
    return nodes, edges, labels, shapes


# ── quadrant：四象限矩阵 ──────────────────────────────────

_AXIS_LBL_W = 40        # 左侧 y 轴标签列
_X_LBL_H = 24           # 底部 x 轴标签行
_QUAD_NAME_H = 24       # 象限名行（items 从它下方开始，天然不重叠）
_QUAD_PAD = 8


def _quadrant(spec, W, measure=None, row_gap=24):
    """四象限矩阵（优先级 / 定位 / 重要性评估）。

    ``quadrants`` **固定 4 个，按 TL → TR → BL → BR 阅读顺序** —— 这是唯一
    「零字段且无歧义」的方案；要模型写 ``{"tl":…,"br":…}`` 等于多塞四个 key
    让它去记方向，而方向记反了图看着还是对的（最坏的一类错误）。

    十字轴与外框走 ``shapes``：它们本就**超出 items 的包围盒**（横轴一直伸到
    右边界），这正是 1.4 让 shapes 参与 bbox 的用武之地 —— 不再需要「右端轴
    标签硬贴 ``W-MARGIN``」那种一改就失效的 workaround。

    明确不做象限底色：渲染器不支持半透明，实色会盖住条目。
    """
    quads = [q if isinstance(q, dict) else {} for q in (spec.get('quadrants') or [])]
    quads = (quads + [{}] * 4)[:4]
    xl = [str(t)[:6] for t in (spec.get('x') or [])][:2]
    yl = [str(t)[:6] for t in (spec.get('y') or [])][:2]

    avail = W - 2 * MARGIN
    plot_x = MARGIN + _AXIS_LBL_W
    plot_w = avail - _AXIS_LBL_W
    half_w = plot_w // 2
    cell_w = half_w - 2 * _QUAD_PAD
    if cell_w < _MIN_NODE_W:
        raise LayoutError("画布过窄，四象限排不下（请加宽画布或改用其它布局）")

    rows = max(1, max(len(q.get('items') or []) for q in quads))
    cell_h = _QUAD_NAME_H + rows * (H + row_gap) - row_gap
    plot_y = MARGIN
    plot_h = 2 * cell_h

    nodes, labels = [], []
    for qi, q in enumerate(quads):
        col, row = qi % 2, qi // 2          # 0=TL 1=TR 2=BL 3=BR
        qx = plot_x + col * half_w + _QUAD_PAD
        qy = plot_y + row * cell_h
        name = str(q.get('name', ''))[:10]
        if name:
            _w = min(int(_measure(name, 11, measure)) + 12, cell_w)
            labels.append({"x": qx, "y": qy + 4, "w": _w, "h": 16,
                           "text": _truncate_to_width(name, _w - 12, measure, 11)})
        for k, it in enumerate(q.get('items') or []):
            it = _norm_node(it)
            w = min(_shape_min_w(it.get('label', ''), it.get('sub'), measure), cell_w)
            nodes.append(_make_node(it, qx, qy + _QUAD_NAME_H + k * (H + row_gap),
                                    w, measure))

    if yl:                                  # 左列自上而下 = 高、低（y 轴正方向朝上）
        for k, t in enumerate([yl[-1], yl[0]]):
            labels.append({"x": MARGIN, "y": plot_y + k * cell_h + cell_h // 2 - 8,
                           "w": _AXIS_LBL_W - 8, "h": 16, "text": t, "anchor": "end"})
    if xl:                                  # 底行自左而右 = 低、高
        for k, t in enumerate(xl):
            labels.append({"x": plot_x + k * half_w, "y": plot_y + plot_h + 4,
                           "w": half_w - 8, "h": 16, "text": t})

    shapes = [
        {"kind": "rect", "x": plot_x, "y": plot_y, "w": plot_w, "h": plot_h,
         "stroke": "ink", "fill": "none"},
        {"kind": "line", "x1": plot_x + half_w, "y1": plot_y,
         "x2": plot_x + half_w, "y2": plot_y + plot_h, "stroke": "muted"},
        {"kind": "line", "x1": plot_x, "y1": plot_y + cell_h,
         "x2": plot_x + plot_w, "y2": plot_y + cell_h, "stroke": "muted"},
    ]
    return nodes, [], labels, shapes


def _grid_edge_label(a, b, text, W, measure=None, lane_y=None, mid_x=None,
                     nodes=None):
    """边标签遮罩位置。

    遮罩宽度受**双重钳制**：① 同行边不挤压两侧节点（保留 12px 间隙）
    ② 不越出画布。钳不住则返回 None —— **省略标签文字、只留连线**，
    好过画出一块压住节点的色块。

    ``lane_y``/``mid_x``：边被分到某条走廊车道后，标签必须跟着它的水平段走
    ——否则标签与连线错位（实测 7 条边同一 y 时，三个标签叠成一团）。

    ``nodes``：改判据。默认（None）按「两心跨度」钳制——那是给**同层内水平
    相邻**的边用的近似：跨度小说明两侧竖线夹得紧，标签塞进去会被劈成两半。
    但 state 的前向边是**上下层**，两心 x 常常只差十几像素、跨度判据直接
    判死，而它的水平段落在走廊里（周围根本没有节点）。传了 ``nodes`` 就
    跳过跨度启发式，直接逐个查遮罩是否压到真实节点——**用事实判断代替
    几何近似**，落在走廊里的标签因此能保住（实测「付款」「退款」两条）。
    """
    avail = W - 2 * MARGIN
    w = int(_measure(text, 11, measure)) + 20
    if lane_y is not None:
        # 走廊车道的标签居中于水平段——但水平段两端各连着一条竖直段，标签宽过
        # 跨度就会被竖线从中间劈开（实测「路由」：两节点中心仅差 24px 而标签宽
        # 42px，两条竖线都落在遮罩内）。与同行分支同一策略：容不下就截断，
        # 截不下则丢弃。
        span = abs((a['x'] + a['w'] // 2) - (b['x'] + b['w'] // 2))
        if nodes is None and w > span - 8:
            room = span - 8
            if room < 40:
                return None
            text = _truncate_to_width(text, room - 20, measure, 11)
            w = int(_measure(text, 11, measure)) + 20
        x = mid_x if mid_x is not None else W // 2
        y = lane_y - 8
    elif a['y'] == b['y']:
        lo, hi = ((a['x'] + a['w'], b['x']) if a['x'] < b['x']
                  else (b['x'] + b['w'], a['x']))
        span = hi - lo
        if w > span - 12:                       # 挤到节点了
            room = span - 12
            if room < 48:
                return None
            text = _truncate_to_width(text, room - 20, measure, 11)
            w = int(_measure(text, 11, measure)) + 20
        x = (lo + hi) // 2
        y = a['y'] + a['h'] // 2 - 8
    else:
        top, bot = (a, b) if a['y'] < b['y'] else (b, a)
        y = (top['y'] + top['h'] + bot['y']) // 2 - 8
        x = (a['x'] + a['w'] // 2 + b['x'] + b['w'] // 2) // 2
    if w > avail:
        return None
    x0 = max(MARGIN, min(x - w // 2, W - MARGIN - w))
    y0 = max(0, y)
    if nodes is not None and any(
            _overlap(x0, y0, w, 16, n['x'], n['y'], n['w'], n['h']) for n in nodes):
        return None
    return {"x": x0, "y": y0, "w": w, "h": 16, "text": text}


_CORRIDOR_PITCH = 24    # 走廊内每条边占的高度（车道间距 + 标签遮罩 16px）


def _corridor_index(norm, r0, c0, r1, c1):
    """边需要占用的走廊索引；None = 不需要走廊（同行相邻列，直连即可）。

    走廊 k = 行 k 与行 k+1 之间的水平间隙。跨行边占用两行之间的走廊；同行边若
    **跨列**（中间隔着节点）必须绕行，也占一条——末行下方无空间，改绕它上方那条。

    只依赖行列号、不依赖坐标，所以能在排坐标**之前**统计每个走廊的边数，据此
    撑开走廊高度（见 ``_grid`` 的 ``corridor_count``）。
    """
    if r0 != r1:
        return min(r0, r1)
    if abs(c0 - c1) <= 1:
        return None                       # 相邻列直连，不占走廊
    # 同行跨列：末行向上绕（下方无空间），其余向下绕；单行图只能向下
    return r0 - 1 if (r0 > 0 and r0 == len(norm) - 1) else r0


def _grid(rows, edges_in, W, measure=None, row_gap=56, align=False):
    """通用网格布局：按行排布节点，边用 [行, 列] 索引指定，路径自动计算。

    ``align=True`` 按列对齐：各列取所有行里的最大宽度，居中后上下列心重合
    （否则每行独立居中，连线会拐）。v1 不暴露给 LLM，保留供手工 fig 使用。

    **走廊分道**：相邻两行之间是一条「走廊」，跨行边与同行绕行边**共用它**。
    若每条边都固定走走廊中线，短跨度边会被长跨度边完全盖住——实测 7 条
    row1→row2 边的水平段 y 全是 188，三个标签叠成一团。故按走廊聚成一池均匀
    分道，并按边数撑开走廊高度（否则车道与标签没有立足空间，标签只能丢弃）。
    """
    outs, edges, labels = [], [], []
    avail = W - 2 * MARGIN
    index = {}
    row_y: list[int] = []                 # 每行顶边 y（供走廊边界计算）
    norm = [[_norm_node(i) for i in row] for row in (rows or [])]
    if not norm:
        return outs, edges, labels
    colw = []
    if align:
        ncol = max(len(r) for r in norm)
        colw = [0] * ncol
        for row in norm:
            for c, it in enumerate(row):
                colw[c] = max(colw[c], node_min_w(it.get('label', ''), it.get('sub'), measure))
        colw = _shrink(colw, GAP_X, avail)

    # 走廊需求前置统计：只依赖行列号，故能在排坐标前算出，据此撑开行距
    corridor_count: dict[int, int] = {}
    for e in (edges_in or []):
        try:
            (r0, c0), (r1, c1) = e['from'], e['to']
        except (TypeError, ValueError):
            continue
        k = _corridor_index(norm, r0, c0, r1, c1)
        if k is not None:
            corridor_count[k] = corridor_count.get(k, 0) + 1

    y = MARGIN
    for r, items in enumerate(norm):
        row_y.append(y)
        ws = (list(colw[:len(items)]) if align
              else _shrink([node_min_w(i.get('label', ''), i.get('sub'), measure)
                            for i in items], GAP_X, avail))
        # 列间距吸收剩余宽度（_shrink 只保证「放得下」，不会主动撑开）：
        # 固定 48px 时同行边的标签遮罩一律塞不下 → 标签静默消失（见 _GRID_GAP_MAX）。
        # 顺带让网格图铺开，而不是一堆窄节点挤在画布中间。
        # 向下取整到 4px 网格且 ≤ slack/(n-1)，保证 total 仍 ≤ avail 不溢出。
        gap = GAP_X
        if len(ws) > 1:
            slack = avail - sum(ws)
            gap = min(_GRID_GAP_MAX, max(GAP_X, (slack // (len(ws) - 1)) // UNIT * UNIT))
        total = sum(ws) + gap * (len(ws) - 1)
        cur = MARGIN + r4(max(0, (avail - total) / 2))
        for c, (it, w) in enumerate(zip(items, ws)):
            lb, sb, w2 = _fit_labels(it.get('label', ''), it.get('sub'), avail, measure)
            w = min(w, w2)          # _fit_labels 在极端长文字时会更宽，取小的
            n = {"x": cur, "y": y, "w": w, "h": H,
                 "label": lb, "sub": sb, "focus": bool(it.get('focus', False))}
            outs.append(n)
            index[(r, c)] = n
            cur += w + gap
        # 本行与下一行之间的走廊要容下 corridor_count[r] 条边（各一条车道 + 标签）
        y += H + max(row_gap, corridor_count.get(r, 0) * _CORRIDOR_PITCH)

    def _lane_y(k: int, j: int, n: int) -> int:
        """走廊 k 内第 j 条车道（共 n 条）的 y —— 均匀分布，两端自动留余量。"""
        top = row_y[k] + H
        bot = row_y[k + 1] if k + 1 < len(row_y) else top + row_gap
        return r4(top + (bot - top) * (j + 1) / (n + 1))

    # 收集合法边 → 按走廊分池 → 分配车道 → 生成路径
    pending = []
    for e in (edges_in or []):
        a, b = index.get(tuple(e['from'])), index.get(tuple(e['to']))
        if not a or not b:
            continue
        pending.append((a, b, e, _corridor_index(norm, e['from'][0], e['from'][1],
                                                e['to'][0], e['to'][1])))
    buckets: dict[int, list[int]] = {}
    for i, (_, _, _, k) in enumerate(pending):
        if k is not None:
            buckets.setdefault(k, []).append(i)
    lane: dict[int, int] = {}
    for k, idxs in buckets.items():
        for j, i in enumerate(idxs):
            lane[i] = _lane_y(k, j, len(idxs))

    for i, (a, b, e, k) in enumerate(pending):
        acx, bcx = a['x'] + a['w'] // 2, b['x'] + b['w'] // 2
        if k is None:                       # 同行相邻列：直连
            edges.append(_make_edge(a, b, dashed=e.get('dashed', False)))
            lb = (_grid_edge_label(a, b, str(e['label']), W, measure)
                  if e.get('label') else None)
        else:
            # 绕行走廊：末段必为轴对齐，箭头方向零解析成本（与 _edge_path 同约）
            ly = lane[i]
            if a['y'] == b['y']:            # 同行跨列：绕行，否则横穿中间节点
                if ly > a['y']:
                    d = f"M{acx} {a['y'] + a['h']} V{ly} H{bcx} V{b['y'] + b['h']}"
                    end, direction = (bcx, b['y'] + b['h']), (0, -1)
                else:
                    d = f"M{acx} {a['y']} V{ly} H{bcx} V{b['y']}"
                    end, direction = (bcx, b['y']), (0, 1)
            elif a['y'] < b['y']:           # 向下跨行
                d = f"M{acx} {a['y'] + a['h']} V{ly} H{bcx} V{b['y']}"
                end, direction = (bcx, b['y']), (0, 1)
            else:                            # 向上跨行
                d = f"M{acx} {a['y']} V{ly} H{bcx} V{b['y'] + b['h']}"
                end, direction = (bcx, b['y'] + b['h']), (0, -1)
            edges.append({"d": d, "end": end, "dir": direction,
                          "dashed": bool(e.get('dashed')), "arrow": True})
            lb = (_grid_edge_label(a, b, str(e['label']), W, measure,
                                   lane_y=ly, mid_x=(acx + bcx) // 2)
                  if e.get('label') else None)
        if lb:
            labels.append(lb)
    return outs, edges, labels


def _shrink(ws, gap, avail):
    """逐个削最宽的节点直到一行放得下。"""
    guard = 0
    while sum(ws) + gap * (len(ws) - 1) > avail and max(ws) > _MIN_NODE_W and guard < 500:
        ws[ws.index(max(ws))] -= UNIT
        guard += 1
    return ws


# 算子的两套签名，按入参结构分流（`grid` / `layers` 的容器也不同，在 expand 里特判）：
#   _NODES_RUNNERS: (nodes, W, measure, row_gap)  —— 入参就是扁平的节点数组
#   _SPEC_RUNNERS:  (spec,  W, measure, row_gap)  —— 入参是整份 spec
# 后者用于父子关系内嵌在结构里（tree）或需要多条命名数组协同（flowchart 的
# nodes+edges、sequence 的 actors+messages）的布局：拆成独立字段比塞进 nodes 更明确。
_NODES_RUNNERS = {
    "hchain": _hchain,
    "timeline": _timeline,
    "branch": _branch,
}

_SPEC_RUNNERS = {
    "tree": _tree,
    "flowchart": _flowchart,
    "state": _state,
    "sequence": _sequence,
    "quadrant": _quadrant,
}

