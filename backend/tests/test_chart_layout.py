"""语义化图表布局器回归测试 — chart_layout.py

对齐 2026-09-19 方案「图表生成优化 — 语义化布局 + 多图支持」：
把「画图」从生成问题变成渲染问题——LLM 只答有哪些节点/怎么排，坐标由布局器算，
几何错误从结构上不可能发生。本测试即验证这条不变式。

覆盖：
1. 4px 网格对齐（所有 x/y/w/h 离散）
2. 四种上 prompt 的 layout + branch：节点无重叠、在界内、高度 ≤640
3. `_edge_path` 只生成 M/H/V（禁斜线）且 dir 为单位轴向量
4. `hchain` 自动折行（**按 node_min_w 真实宽度**断言，不按 64px 最小值）
5. `measure` 注入生效（字体宽度与渲染同源的前提）
6. `_truncate_to_width` 截断后必然放得下
7. `validate_spec` 各类拦截（layout 白名单 / 空 nodes / edges 索引越界并定位 /
   节点上限 / 边标签超长截断而非拒绝）
8. `expand` 高度超限 → LayoutError（压缩行距后仍超）
9. `assert_layout` 能抓出重叠（它是布局器 bug 的报警器）

纯几何、零 PIL 依赖，不 mock 任何东西。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import re

from app.services import chart_layout as L

W = 640
AVAIL = W - 2 * L.MARGIN
_CMD_RE = re.compile(r"[A-Za-z]")


def _by_y(nodes):
    """按 y 分行。"""
    rows = {}
    for n in nodes:
        rows.setdefault(n['y'], []).append(n)
    return [rows[k] for k in sorted(rows)]


def _check_common(name, r):
    """所有 layout 的公共不变式。"""
    for n in r['nodes']:
        for k in ('x', 'y', 'w', 'h'):
            assert n[k] % L.UNIT == 0, f'{name}: {k}={n[k]} 未对齐 {L.UNIT}px 网格'
    for n in r['nodes']:
        assert n['x'] >= 0 and n['y'] >= 0, f'{name}: 节点越界左/上 {n}'
        assert n['x'] + n['w'] <= r['width'], f'{name}: 节点越界右 {n}'
        assert n['y'] + n['h'] <= r['height'], f'{name}: 节点越界下 {n}'
    for i, a in enumerate(r['nodes']):
        for b in r['nodes'][i + 1:]:
            overlap = (a['x'] < b['x'] + b['w'] and b['x'] < a['x'] + a['w']
                       and a['y'] < b['y'] + b['h'] and b['y'] < a['y'] + a['h'])
            assert not overlap, f"{name}: 节点重叠 {a['label']!r} / {b['label']!r}"
    # height 是**自然**高度，可能超 640 —— 实际显示高度 = height × scale
    assert r['height'] * r.get('scale', 1.0) <= 640 + 1, \
        f"{name}: 显示高度 {r['height']}×{r.get('scale', 1.0)} 超出 640"
    assert r['width'] <= 640, f"{name}: 宽度 {r['width']} 超出 640"
    for e in r['edges']:
        assert set(_CMD_RE.findall(e['d'])) <= {'M', 'H', 'V'}, \
            f"{name}: 路径含非正交命令（禁斜线）：{e['d']}"
        dx, dy = e['dir']
        assert (dx, dy) in ((1, 0), (-1, 0), (0, 1), (0, -1)), \
            f"{name}: 箭头方向不是单位轴向量 {e['dir']}"
        ex, ey = e['end']
        assert 0 <= ex <= r['width'] and 0 <= ey <= r['height'], \
            f"{name}: 箭头终点越界 {e['end']}"


def _raises(fn, frag, msg):
    """断言 fn 抛 LayoutError 且消息含 frag，返回消息文本。"""
    try:
        fn()
    except L.LayoutError as e:
        assert frag in str(e), f'{msg}: 期望报错含 {frag!r}，实际 {e}'
        return str(e)
    raise AssertionError(f'{msg}: 未抛 LayoutError')


# ── 1/2. 各 layout 公共不变式 ──

r_hchain = L.expand({"layout": "hchain", "title": "流程",
                     "nodes": [{"label": "输入"}, {"label": "清洗", "focus": True},
                               {"label": "输出"}]})
_check_common('hchain', r_hchain)
assert len(r_hchain['nodes']) == 3 and len(r_hchain['edges']) == 2
assert sum(1 for n in r_hchain['nodes'] if n['focus']) == 1
print(f"PASS: hchain — {r_hchain['width']}x{r_hchain['height']}，3 节点 2 边")

r_grid = L.expand({
    "layout": "grid", "title": "依赖",
    "rows": [[{"label": "前端"}, {"label": "缓存"}], [{"label": "接口层"}]],
    "edges": [{"from": [0, 0], "to": [1, 0], "label": "调用"},
              {"from": [0, 1], "to": [1, 0]}]})
_check_common('grid', r_grid)
assert len(r_grid['nodes']) == 3 and len(r_grid['edges']) == 2
assert len(r_grid['labels']) == 1 and r_grid['labels'][0]['text'] == '调用'
print(f"PASS: grid — 3 节点 2 边 1 边标签（无标签边不产生遮罩）")

r_tl = L.expand({"layout": "timeline",
                 "nodes": [{"label": f"事件{i}", "sub": f"{i * 5:02d}:00"} for i in range(9)]})
_check_common('timeline', r_tl)
assert len(r_tl['labels']) == 9, '每个带 sub 的节点应有一个时间标签'
assert len({n['x'] for n in r_tl['nodes']}) == 1, 'timeline 节点必须等宽左对齐（连线才是直线）'
assert len({n['w'] for n in r_tl['nodes']}) == 1, 'timeline 节点必须等宽'
print(f"PASS: timeline — 9 节点等宽竖排，{r_tl['height']}px 高（< 640）")

r_layers = L.expand({"layout": "layers", "layers": [
    {"name": "前端", "nodes": [{"label": "UI"}, {"label": "Store"}]},
    {"name": "后端", "nodes": [{"label": "Router"}, {"label": "Service"}, {"label": "Store"}]}]})
_check_common('layers', r_layers)
assert len(r_layers['nodes']) == 5 and len(r_layers['labels']) == 2
assert len(r_layers['edges']) == 0, 'layers 是包含关系，不生成边'
print(f"PASS: layers — 2 层 5 节点 2 层名标签，无边")

r_branch = L.expand({"layout": "branch", "nodes": [
    {"label": "判定A", "result": "结果A"}, {"label": "判定B", "result": "结果B"}]})
_check_common('branch', r_branch)
assert len(r_branch['nodes']) == 4
print(f"PASS: branch — 4 节点 {len(r_branch['edges'])} 边（v1 不上 prompt，仍单测防死代码）")

# ── 2b. tree — 组织 / 分类 / 目录（父子内嵌，递归宽度 + 母线连线）──
r_tree = L.expand({"layout": "tree", "title": "组织架构",
                   "root": {"label": "CEO", "children": [
                       {"label": "研发", "children": [{"label": "前端"}, {"label": "后端"}]},
                       {"label": "市场"}, {"label": "运营"}]}})
_check_common('tree', r_tree)
_lv = _by_y(r_tree['nodes'])
assert len(r_tree['nodes']) == 6, '递归应数出 6 个节点'
assert len(r_tree['edges']) == len(r_tree['nodes']) - 1, '树必为 节点数−1 条边'
assert [len(x) for x in _lv] == [1, 3, 2], f'层分布应为 1/3/2，实际 {[len(x) for x in _lv]}'
# 同父子的 x 区间按输入序左→右且不相交
_sib = sorted((n for n in r_tree['nodes'] if n['label'] in ('前端', '后端')),
              key=lambda n: n['x'])
assert [n['label'] for n in _sib] == ['前端', '后端'], '兄弟应按输入序左→右'
assert _sib[0]['x'] + _sib[0]['w'] <= _sib[1]['x'], '兄弟 x 区间不得相交'
# 根与**整个子树**同心 —— 递归居中算法最容易偏的地方（差几个像素也该立刻发现）。
# ⚠️ 取全部后代而非只取第二层：有子树的子节点（研发）自己是居中的，但它的
# 后代（前端/后端）才是它占位的真实边界，用第二层算会得出错误的「中心」。
_r0 = _lv[0][0]
_kids_all = [n for n in r_tree['nodes'] if n is not _r0]
_span = (min(n['x'] for n in _kids_all) + max(n['x'] + n['w'] for n in _kids_all)) // 2
assert abs((_r0['x'] + _r0['w'] // 2) - _span) <= 4, \
    f"根未与子块同心：根中心 {_r0['x'] + _r0['w'] // 2} / 子块中心 {_span}"
print(f"PASS: tree — {len(r_tree['nodes'])} 节点 {len(r_tree['edges'])} 边，"
      f"层分布 {[len(x) for x in _lv]}，根与子块同心")

# 超宽三级降级：压兄弟间距 → 等比收缩 → 报错请拆图
r_wide = L.expand({"layout": "tree", "root": {"label": "根", "children": [
    {"label": f"很长的子节点名称{i}"} for i in range(6)]}})
_check_common('tree-wide', r_wide)
print(f"PASS: tree 超宽自动降级（6 个长子节点 → {r_wide['width']}px 仍在画布内）")
_raises(lambda: L.expand({"layout": "tree", "root": {"label": "根", "children": [
    {"label": f"超级无敌长的子节点标签{i}"} for i in range(12)]}}),
    '树太宽', 'tree 缩到 _MIN_NODE_W 仍放不下时应报错请拆图')
print("PASS: tree 收缩触底 → 报错请拆图（不静默画出一张截字的图）")
_raises(lambda: L.expand({"layout": "tree", "root": {"label": "1", "children": [
    {"label": "2", "children": [{"label": "3", "children": [
        {"label": "4", "children": [{"label": "5"}]}]}]}]}}),
    '层级过深', 'tree 深度 > 4 应被拒')
_raises(lambda: L.expand({"layout": "tree", "root": {"label": "根", "children": [
    {"label": f"n{i}"} for i in range(30)]}}),
    '节点过多', 'tree 节点数应经 _count_nodes 递归计数后被拦')
print("PASS: tree 深度 > 4 / 节点数超上限 均被 validate_spec 拦下")

# ── 2c. flowchart — 带判定的流程（字符串 id 引用 + 拓扑分层 + 沟槽绕行）──
r_flow = L.expand({"layout": "flowchart", "title": "提交流程", "nodes": [
    {"id": "start", "label": "开始", "type": "start"},
    {"id": "submit", "label": "提交"},
    {"id": "ok", "label": "通过?", "type": "decision"},
    {"id": "fix", "label": "退回修改"},
    {"id": "done", "label": "归档", "type": "end"}],
    "edges": [{"from": "start", "to": "submit"}, {"from": "submit", "to": "ok"},
              {"from": "ok", "to": "done", "label": "是"},
              {"from": "ok", "to": "fix", "label": "否"}]})
_check_common('flowchart', r_flow)
_flv = _by_y(r_flow['nodes'])
assert [len(x) for x in _flv] == [1, 1, 1, 2], f'层分布应 1/1/1/2，实际 {[len(x) for x in _flv]}'
_shps = {n['label']: n['shape'] for n in r_flow['nodes']}
assert _shps['开始'] == 'stadium' and _shps['归档'] == 'stadium', 'start/end → stadium'
assert _shps['通过?'] == 'diamond' and _shps['提交'] == 'rect', 'decision → diamond'
# 菱形文字必须放得下**内接矩形** —— _shape_min_w 把菱形外接宽翻倍就是为了这条
for _n in r_flow['nodes']:
    assert L.text_width(_n['label']) <= L._shape_inner_w(_n), \
        f"形状内接矩形容不下 {_n['label']!r}"
# 箭头恒落在某节点**上边界**：dir=(0,1) 的必然结果，分支连线最容易在这里错位
_tops = {n['y'] for n in r_flow['nodes']}
for _e in r_flow['edges']:
    assert _e['end'][1] in _tops, f"箭头终点不在任何节点上边界：{_e['end']}"
print(f"PASS: flowchart — {len(r_flow['nodes'])} 节点 {len(r_flow['edges'])} 边，"
      f"stadium/diamond 形状分发，{len(r_flow['labels'])} 个「是/否」标签")

# 分层是**最长路径**（不是 BFS 最短）：跳层边不得把目标节点拉浅到它下一层
r_skip = L.expand({"layout": "flowchart", "nodes": [
    {"id": "a", "label": "开始", "type": "start"}, {"id": "b", "label": "校验"},
    {"id": "c", "label": "处理"}, {"id": "d", "label": "结束", "type": "end"}],
    "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "c"},
              {"from": "c", "to": "d"}, {"from": "a", "to": "d", "label": "跳过"}]})
_check_common('flowchart-skip', r_skip)
assert len(_by_y(r_skip['nodes'])) == 4, 'a→d 不应把 d 提前到 a 的下一层'
_node_r = max(n['x'] + n['w'] for n in r_skip['nodes'])
_skip = [e for e in r_skip['edges'] if L._path_bbox(e['d'])[2] > _node_r]
assert len(_skip) == 1, f'跳层边应借右侧沟槽绕行（避开中间层），实际 {len(_skip)} 条'
# assert_layout 已保证它不穿中间层节点（穿则 expand 直接抛），这里只钉住「确实绕了」
print(f"PASS: flowchart 最长路径分层 + 跳层边借沟槽绕行（{_skip[0]['d']!r}）")

_raises(lambda: L.expand({"layout": "flowchart", "nodes": [
    {"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
    "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}]}),
    'state', 'flowchart 有环应报错并把模型指向 state 布局')
_raises(lambda: L.expand({"layout": "flowchart", "nodes": [
    {"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
    "edges": [{"from": "a", "to": "zzz"}]}),
    '不是任何节点的 id', 'edges 引用不存在的 id 应精确到 edges[i].to')
_raises(lambda: L.expand({"layout": "flowchart", "nodes": [{"label": "无id"}],
                          "edges": []}),
    '缺少字符串 id', 'flowchart 节点必须带字符串 id')
_raises(lambda: L.expand({"layout": "flowchart", "nodes": [
    {"id": "a", "label": "A"}, {"id": "a", "label": "B"}], "edges": []}),
    'id 重复', '重复 id 会静默连错节点，必须拒')
# ⚠️ **「层数过深」这条闸门已在 2026-10-20 删除**（原先这里是 `nlayer > 5` 的
# 负向断言）。它和 `expand()` 的「压行距 + 等比缩放 + `_MIN_SCALE`」不同源且严格更严：
# 6 层纯矩形 round1 只要 520px（scale=1.0）就装得下，却被判「层数过深」。故这条改为
# **正向断画得出来**，负向断言改为「真的缩不动才报」——那才是唯一真相源的判据。
_r6 = L.expand({"layout": "flowchart", "nodes": [
    {"id": f"n{i}", "label": f"步骤{i}"} for i in range(6)],
    "edges": [{"from": f"n{i}", "to": f"n{i + 1}"} for i in range(5)]})
assert len(_by_y(_r6['nodes'])) == 6, '6 层链应正常分层'
assert _r6['scale'] == 1.0, f"6 层链不该触发缩放，实际 {_r6['scale']}"
_raises(lambda: L.expand({"layout": "flowchart", "nodes": [
    {"id": f"n{i}", "label": f"步骤{i}"} for i in range(18)],
    "edges": [{"from": f"n{i}", "to": f"n{i + 1}"} for i in range(17)]}),
    '拆分成多张图', '18 层链超出缩放下限（0.65）时仍应报「请拆图」')
_raises(lambda: L.expand({"layout": "flowchart", "nodes": [
    {"id": f"d{i}", "label": f"判定{i}?", "type": "decision"} for i in range(4)],
    "edges": [{"from": "d0", "to": "d1"}]}),
    '判定节点过多', 'decision > 3 应被拒（菱形太占高度）')
print("PASS: flowchart 负向用例 — 环 / 未知 id / 缺 id / 重复 id / 缩不下限 / 判定过多"
      "（+ 6 层可画：层数闸门已删）")

# ── 2d. state — 状态机（允许环与自环，回边走右侧沟槽）──
r_st = L.expand({"layout": "state", "title": "订单状态", "states": [
    {"id": "new", "label": "新建", "type": "start"},
    {"id": "paid", "label": "已支付"},
    {"id": "ship", "label": "已发货"},
    {"id": "done", "label": "已完成", "type": "end"}],
    "transitions": [{"from": "new", "to": "paid", "label": "付款"},
                    {"from": "paid", "to": "ship"}, {"from": "ship", "to": "done"},
                    {"from": "paid", "to": "paid", "label": "重试"},
                    {"from": "done", "to": "new", "label": "退款"}]})
_check_common('state', r_st)
_dots = [n for n in r_st['nodes'] if n['shape'] == 'circle']
assert len(_dots) == 2 and all(n['label'] == '' for n in _dots), \
    'start/end 各插一个**空 label** 的圆点（否则会画出空 <text>）'
# 4 状态 + 2 圆点各占一行：BFS 分层把伪状态也算进层号
assert len({n['y'] for n in r_st['nodes']}) == 6, '状态机应排成 6 行'
assert _dots[0]['y'] < _dots[1]['y'], '起点圆点在上、终点圆点在下'
print(f"PASS: state — {len(r_st['nodes'])} 节点（含 2 个起止圆点）"
      f"{len(r_st['edges'])} 条转移，分 6 行")

# 回边：从源底部下到本层走廊 → 沿右侧沟槽 → 从目标层上方走廊进入
_node_r = max(n['x'] + n['w'] for n in r_st['nodes'])
_back = [e for e in r_st['edges'] if L._path_bbox(e['d'])[2] > _node_r]
assert len(_back) == 1, f'回边应绕到节点区右侧（避开同层其余节点），实际 {len(_back)} 条'
# 回边终段必然是**竖直**进入目标顶/底（从上方或下方走廊下来），不能是斜线
assert _back[0]['dir'] in ((0, 1), (0, -1)), '回边终段应竖直进入目标'
# 自环：右缘外一个小环路，是唯一 dir=(-1,0)（向左回到起点节点）的边
_loop = [e for e in r_st['edges'] if e['dir'] == (-1, 0)]
assert len(_loop) == 1, '自环是唯一向左收尾的边'
assert _loop[0]['d'].count('H') == 2, f'自环 = 出去/折返两段水平：{_loop[0]["d"]!r}'
assert len(r_st['labels']) == 3, f'付款/重试/退款三个标签都应放下，实际 {r_st["labels"]}'
print(f"PASS: state 回边绕沟槽（{_back[0]['d']!r}）+ 自环右缘环路"
      f"（{_loop[0]['d']!r}）")

_raises(lambda: L.expand({"layout": "state", "states": [{"id": "a", "label": "A"}],
                          "transitions": [{"from": "a", "to": "zzz"}]}),
    '不是任何节点的 id', 'state 的 transitions 同样按 id 校验（共用 _validate_id_graph）')
_raises(lambda: L.expand({"layout": "state", "states": [
    {"id": f"s{i}", "label": f"状态{i}"} for i in range(3)],
    "transitions": [{"from": f"s{i}", "to": f"s{i}"} for i in range(3)]}),
    '自环过多', '自环 > 2 应被拒（每个自环都在右缘外占 28px）')
# ⚠️ **`nrow > 7` 闸门已在 2026-10-20 删除**（原先这里是「9 层超深应被拒」）。
# 8 层正是用户实际撞到的那条假失败：round1 需 760px，但 round2（行距减半）只需 564px
# ⇒ scale=1.0 完全画得下，闸门却在缩放之前就把它拒了。故改成正向断言。
_r9 = L.expand({"layout": "state", "states": [
    {"id": f"s{i}", "label": f"状态{i}"} for i in range(9)],
    "transitions": [{"from": f"s{i}", "to": f"s{i + 1}"} for i in range(8)]})
assert len(_by_y(_r9['nodes'])) == 9, '9 层状态链应正常分层'
_raises(lambda: L.expand({"layout": "state", "states": [
    {"id": f"s{i}", "label": f"状态{i}"} for i in range(18)],
    "transitions": [{"from": f"s{i}", "to": f"s{i + 1}"} for i in range(17)]}),
    '拆分成多张图', '18 层状态链超出缩放下限（0.65）时仍应报「请拆图」')
print("PASS: state 负向用例 — 未知 id / 自环过多 / 缩放下限（+ 9 层可画：层数闸门已删）")

# ── 2d'. 同节点多条回边 → 走廊分道（2026-10-26）──
# 熔断器三态：关闭 → 打开 → 半开，而「半开」又同时连回两者 —— 两条回边同从**末行**
# 节点出发，各自还有一条「进入目标层」的水平段。修复前 `corr_y`/`entry` 都只依赖
# 层号 ⇒ 这两条边的两段水平线分别完全重合（沟槽 x 只差 6px），画面上并成一条横线；
# 标签只能靠 `engine._relax_labels` 上下让位，让开就**悬空**（读者分不出哪个标签说
# 的是哪条线），而 `assert_layout` 在让位之前就因「标签遮罩重叠」整图失败 ——
# 用户 2026-10-26 报告的实证产物里 4 条正是这个（降级成「📊 图表生成失败」占位）。
_CIRCUIT = {"layout": "state", "title": "熔断器三态", "states": [
    {"id": "closed", "label": "关闭"},
    {"id": "open", "label": "打开"},
    {"id": "half", "label": "半开"}],
    "transitions": [
        {"from": "closed", "to": "open", "label": "错误率超阈值"},
        {"from": "open", "to": "half", "label": "等待时间到"},
        {"from": "half", "to": "closed", "label": "试探成功"},
        {"from": "half", "to": "open", "label": "试探失败"}]}
r_cb = L.expand(_CIRCUIT)
_check_common('state', r_cb)
assert [l['text'] for l in r_cb['labels']] == ['错误率超阈值', '等待时间到',
                                               '试探成功', '试探失败'], r_cb['labels']
_cb = [l for l in r_cb['labels'] if l['text'].startswith('试探')]
assert len(_cb) == 2, '两个分支标签都应保住（丢掉就只剩两条分不清的线）'
assert not L._overlap(_cb[0]['x'], _cb[0]['y'], _cb[0]['w'], _cb[0]['h'],
                      _cb[1]['x'], _cb[1]['y'], _cb[1]['w'], _cb[1]['h']), \
    f'两个标签仍重叠：{_cb}'
# ⚠️ 关键：**不是让位让开的**，是算子自己分了道。判据取「两条回边的水平段 y」——
# 从路径里抽，因为标签的 y 是 lane_y-8 派生的，两者同源但只有 lane 是根因。
def _h_segs(d):
    """路径里所有水平段的 y（`H` 之前的那个 y；纯竖线段的 H 与前后同 x 时被省略）。"""
    out, y = [], 0
    for tok in d.replace('M', ' M').split():
        if tok[0] == 'H':
            out.append(y)
        elif tok[0] in 'MV':
            y = int(tok[1:])
    return out


_backs = [e for e in r_cb['edges'] if _h_segs(e['d'])]
_cb_edges = sorted((_h_segs(e['d'])[0], _h_segs(e['d'])[-1]) for e in _backs
                   if len(_h_segs(e['d'])) >= 2)
assert len(_cb_edges) == 2, f'应恰有两条回边（各含两段水平线）：{_backs}'
assert _cb_edges[0][0] != _cb_edges[1][0], \
    f'两条回边仍共用同一条走廊水平线（未分道）：{_cb_edges}'
assert _cb_edges[0][1] != _cb_edges[1][1], \
    f'两条回边仍共用同一条进入段水平线（未分道）：{_cb_edges}'
# 对照实验：让位逻辑**不**参与这次成功 —— 把两条回边的标签强行摆到同一处，
# `_relax_labels` 仍然能把它们分开（说明它是兜底，不是这条用例的主角）。
_fake = [{"x": 100, "y": 200, "w": 64, "h": 16, "text": "A"},
         {"x": 100, "y": 200, "w": 64, "h": 16, "text": "B"}]
_relaxed = L._relax_labels(_fake, r_cb['nodes'], 640, W)
assert len(_relaxed) == 2 and not L._overlap(
    _relaxed[0]['x'], _relaxed[0]['y'], _relaxed[0]['w'], _relaxed[0]['h'],
    _relaxed[1]['x'], _relaxed[1]['y'], _relaxed[1]['w'], _relaxed[1]['h']), \
    f'让位兜底失效：{_relaxed}'
assert _relaxed[0]['x'] == 100 and _relaxed[0]['y'] == 200, \
    '让位只该动后一个（先到先得），不该挪已经放好的那个'
print(f"PASS: 回边分道 — 熔断器三态两条回边各自占走廊（水平段 "
      f"{_cb_edges[0]} / {_cb_edges[1]}），标签 y {_cb[0]['y']}/{_cb[1]['y']} 且兜底让位可用")

# ── 2e. sequence — 时序图（生命线走 shapes，消息顺序即时间顺序）──
r_sq = L.expand({"layout": "sequence", "title": "登录时序", "actors": [
    {"id": "u", "label": "用户"}, {"id": "s", "label": "服务端"},
    {"id": "db", "label": "数据库"}],
    "messages": [{"from": "u", "to": "s", "text": "登录请求"},
                 {"from": "s", "to": "db", "text": "查用户"},
                 {"from": "db", "to": "s", "text": "用户记录", "dashed": True},
                 {"from": "s", "to": "u", "text": "Token"}],
    "groups": [{"name": "鉴权", "from": 0, "to": 3}]})
_check_common('sequence', r_sq)
assert len(r_sq['nodes']) == 3 and len(r_sq['edges']) == 4
assert len({n['w'] for n in r_sq['nodes']}) == 1, '参与者盒必须等宽（生命线才对齐）'
assert len({n['y'] for n in r_sq['nodes']}) == 1, '参与者必须同处一行'
_cx = [n['x'] + n['w'] // 2 for n in r_sq['nodes']]
_gaps = {_cx[i + 1] - _cx[i] for i in range(len(_cx) - 1)}
assert len(_gaps) == 1, f'参与者中心应等距，实际间距 {_gaps}'
_lifelines = [sh for sh in r_sq['shapes'] if sh['kind'] == 'line']
assert len(_lifelines) == 3, '每个参与者一条生命线'
assert all(sh['x1'] == sh['x2'] for sh in _lifelines), '生命线必须竖直'
assert {sh['x1'] for sh in _lifelines} == set(_cx), '生命线必须落在参与者中心'
# 消息：数组顺序 = y 递增；终点精确落在目标参与者的生命线上（时序图的底线）
_msg_y = [e['end'][1] for e in r_sq['edges']]
assert _msg_y == sorted(_msg_y) and len(set(_msg_y)) == 4, \
    f'消息 y 必须按数组顺序严格递增（数组顺序即时间顺序）：{_msg_y}'
_mx = [n['x'] + n['w'] // 2 for n in r_sq['nodes']]
for _e in r_sq['edges']:
    assert _e['end'][0] in _mx, f'消息终点不在目标参与者中心：{_e["end"]}'
    assert _e['end'][1] > r_sq['nodes'][0]['y'] + r_sq['nodes'][0]['h'], '消息应在盒下方'
assert [e['dashed'] for e in r_sq['edges']] == [False, False, True, False], '虚线透传'
assert [lb['text'] for lb in r_sq['labels']][:4] == \
    ['1. 登录请求', '2. 查用户', '3. 用户记录', '4. Token'], '序号由布局器加'
assert any(sh['kind'] == 'rect' for sh in r_sq['shapes']), '分组框走 shapes'
print(f"PASS: sequence — 3 参与者等距 {sorted(_gaps)[0]}px，4 条消息按序下行，"
      f"{len(_lifelines)} 条生命线 + 分组框走 shapes")

_raises(lambda: L.expand({"layout": "sequence", "actors": [{"id": "u", "label": "用户"}],
                          "messages": []}),
    '至少 2 个参与者', '单参与者不成时序图')
_raises(lambda: L.expand({"layout": "sequence", "actors": [
    {"id": "u", "label": "用户"}, {"id": "s", "label": "服务端"}],
    "messages": [{"from": "u", "to": "zzz", "text": "?"}]}),
    '不是任何节点的 id', 'messages 的 to 必须匹配参与者 id')
_raises(lambda: L.expand({"layout": "sequence", "actors": [
    {"id": f"a{i}", "label": f"方{i}"} for i in range(6)],
    "messages": []}),
    '参与者过多', '参与者 > 5 应被拒（横向排不下）')
_raises(lambda: L.expand({"layout": "sequence", "actors": [
    {"id": "u", "label": "用户"}, {"id": "s", "label": "服务端"}],
    "messages": [{"from": "u", "to": "s", "text": f"m{i}"} for i in range(13)]}),
    '连线过多', '消息 > 12 应被拒')
print("PASS: sequence 负向用例 — 单参与者 / 未知 id / 参与者过多 / 消息过多")

# ── 2f. quadrant — 四象限（轴线走 shapes，全宽靠 shapes 参与 bbox）──
r_q = L.expand({"layout": "quadrant", "title": "任务优先级",
                "x": ["不紧急", "紧急"], "y": ["不重要", "重要"],
                "quadrants": [
                    {"name": "重要且紧急", "items": [{"label": "处理故障"},
                                                     {"label": "客户投诉"}]},
                    {"name": "重要不紧急", "items": [{"label": "写文档"}]},
                    {"name": "紧急不重要", "items": [{"label": "回邮件"},
                                                     {"label": "例会"}]},
                    {"name": "无关", "items": [{"label": "刷资讯"}]}]})
_check_common('quadrant', r_q)
assert len(r_q['nodes']) == 6 and not r_q['edges'], '象限图没有连线'
_qn = {n['label']: n for n in r_q['nodes']}
# 阅读顺序 TL → TR → BL → BR：模型只能按顺序填 4 个对象，位置必须与之一致，
# 否则「重要且紧急」会画到右下角（图看着仍然「对」，是最难发现的一类错）
assert _qn['处理故障']['x'] < _qn['写文档']['x'], 'TR 应在 TL 右侧'
assert _qn['处理故障']['y'] < _qn['回邮件']['y'], 'BL 应在 TL 下方'
assert _qn['刷资讯']['x'] > _qn['回邮件']['x'], 'BR 应在 BL 右侧'
assert _qn['刷资讯']['y'] > _qn['写文档']['y'], 'BR 应在 TR 下方'
_ax = [sh for sh in r_q['shapes'] if sh['kind'] == 'line']
assert len(_ax) == 2, '十字轴线两条'
_frame = [sh for sh in r_q['shapes'] if sh['kind'] == 'rect']
assert len(_frame) == 1 and _frame[0]['x'] + _frame[0]['w'] == r_q['width'] - L.MARGIN, \
    '轴线框必须撑满可用宽度（靠 shapes 参与 _extent，不是贴边 workaround）'
_name_lbl = {lb['text'] for lb in r_q['labels']}
assert {'重要且紧急', '重要不紧急', '紧急不重要', '无关'} <= _name_lbl, '四个象限名'
assert {'重要', '不重要', '不紧急', '紧急'} <= _name_lbl, '两组轴标签'
print(f"PASS: quadrant — 6 条目按 TL/TR/BL/BR 就位，轴线框撑到 {_frame[0]['x'] + _frame[0]['w']}px"
      f"（画布 {r_q['width']}px），{len(r_q['labels'])} 个标签")

_raises(lambda: L.expand({"layout": "quadrant", "quadrants": [{"name": "空"}]}),
    '至少要有一个象限带 items', '四个象限全空应被拒（空图没有信息量）')
print("PASS: quadrant 负向用例 — 全空被拒")

# ── 3. _edge_path 各分支 ──

a = {'x': 0, 'y': 100, 'w': 100, 'h': 44}
b = {'x': 200, 'y': 100, 'w': 100, 'h': 44}
d, end, direction = L._edge_path(a, b)
assert d == 'M100 122 H200' and end == (200, 122) and direction == (1, 0), (d, end, direction)
d, end, direction = L._edge_path(b, a)
assert d == 'M200 122 H100' and end == (100, 122) and direction == (-1, 0), (d, end, direction)

c = {'x': 0, 'y': 300, 'w': 100, 'h': 44}
d, end, direction = L._edge_path(a, c)
assert d == 'M50 144 V300' and end == (50, 300) and direction == (0, 1), (d, end, direction)
d, end, direction = L._edge_path(c, a)
assert d == 'M50 300 V144' and end == (50, 144) and direction == (0, -1), (d, end, direction)

# 错位的垂直边 → L 形折线（仍只含 M/V/H）
off = {'x': 300, 'y': 300, 'w': 100, 'h': 44}
d, end, direction = L._edge_path(a, off)
assert set(_CMD_RE.findall(d)) == {'M', 'V', 'H'} and direction == (0, 1), (d, direction)
print("PASS: _edge_path — 水平/垂直/L 形，全正交且给出箭头终点与方向")

# ── 4. hchain 自动折行（按真实宽度，不按 64px 最小值）──

labels9 = [f"步骤{i}" for i in range(9)]
ws9 = [L.node_min_w(s, None) for s in labels9]
assert ws9[0] > 64, f"'步骤0' 真实宽度应 > 64（实际 {ws9[0]}）—— 折行测试必须按真实宽度算"
r9 = L.expand({"layout": "hchain", "nodes": [{"label": s} for s in labels9]})
rows9 = _by_y(r9['nodes'])
assert len(rows9) > 1, f'9 个中文节点在 640 宽下应折行，实际 {len(rows9)} 排'
for row in rows9:
    total = sum(n['w'] for n in row) + L.GAP * (len(row) - 1)
    assert total <= AVAIL, f'折行后单排宽 {total} 仍超出可用 {AVAIL}'
_check_common('hchain9', r9)
print(f"PASS: hchain 自动折行 — 9 节点折成 {len(rows9)} 排，每排 ≤{AVAIL}px")

# 单排放得下时不折行
r3 = L.expand({"layout": "hchain", "nodes": [{"label": "输入"}, {"label": "输出"}]})
assert len(_by_y(r3['nodes'])) == 1, '两个短标签不该折行'
print("PASS: hchain 短链不折行")

# 三排以上也成立
r20 = L.expand({"layout": "hchain", "nodes": [{"label": f"步骤{i}"} for i in range(18)]})
_check_common('hchain18', r20)
assert len(_by_y(r20['nodes'])) >= 3, '18 个节点应折成 3 排以上'
print(f"PASS: hchain 多排折行 — 18 节点折成 {len(_by_y(r20['nodes']))} 排")

# ── 5. measure 注入生效 ──

spec2 = {"layout": "hchain", "nodes": [{"label": "输入"}, {"label": "输出"}]}
base = L.expand(spec2)
wide = L.expand(spec2, measure=lambda s, size: len(str(s)) * 40.0)
# _distribute 会按比例放大填满画布，所以不能断言「w == node_min_w」，
# 只能断言注入宽度后节点确实随之变大（这正是「字宽低估 → 文字超框」的防线）
assert L.node_min_w('输入', None) == L.r4(L.text_width('输入') + 32), '节点宽 = 文字宽 + 32px 内边距'
assert wide['nodes'][0]['w'] > base['nodes'][0]['w'], \
    f"measure 注入应加宽节点：{base['nodes'][0]['w']} → {wide['nodes'][0]['w']}"
print(f"PASS: measure 注入生效 — 节点宽 {base['nodes'][0]['w']} → {wide['nodes'][0]['w']}")

# ── 6. _truncate_to_width ──

long_s = '这是一个非常长的节点标签需要被截断'
tr = L._truncate_to_width(long_s, 60)
assert tr.endswith('…') and L.text_width(tr) <= 60, (tr, L.text_width(tr))
assert L._truncate_to_width('短', 60) == '短', '放得下时不应改动'
# 极端窄：至少留一个省略号
assert L._truncate_to_width(long_s, 4) == '…'
print(f"PASS: _truncate_to_width — {long_s!r} → {tr!r}（宽度 {L.text_width(tr):.0f} ≤ 60）")

# 超长 label 走完整链路也不越界（48 字 ≈ 656px > timeline 的 544px 可用宽）
r_long = L.expand({"layout": "timeline",
                   "nodes": [{"label": "超长标签" * 12, "sub": "00:01"},
                             {"label": "正常"}]})
_check_common('long-label', r_long)
for n in r_long['nodes']:
    assert L._measure(n['label'], 13) <= n['w'] - 32 or n['w'] < 64, \
        f"节点 {n['label']!r} 文字宽超出节点内容区 {n['w']}"
assert r_long['nodes'][0]['label'].endswith('…'), \
    f"超长标签应被截断，实际 {r_long['nodes'][0]['label']!r}"
print(f"PASS: 超长 label 经 expand 后截断且不越界 → {r_long['nodes'][0]['label']!r}")

# ── 7. validate_spec 拦截 ──

m = _raises(lambda: L.expand({"layout": "sunburst", "nodes": [{"label": "x"}]}),
            '未知布局', '未知 layout')
print(f"PASS: validate_spec 拒未知 layout — {m}")

m = _raises(lambda: L.expand({"layout": "hchain", "nodes": []}), 'nodes 为空', '空 nodes')
m = _raises(lambda: L.expand({"layout": "layers", "layers": []}), 'layers 为空', '空 layers')
m = _raises(lambda: L.expand({"layout": "grid", "rows": []}), 'rows 为空', '空 rows')
m = _raises(lambda: L.expand({"layout": "grid", "rows": [[{"label": "A"}]],
                              "edges": [{"from": [0, 0], "to": [1, 0]}]}),
            'edges[0]', 'edges 行号越界应定位到具体 edge')
m = _raises(lambda: L.expand({"layout": "grid", "rows": [[{"label": "A"}], [{"label": "B"}]],
                              "edges": [{"from": [0, 0], "to": [1, 5]}]}),
            '列号 5 越界', 'edges 列号越界')
m = _raises(lambda: L.expand({"layout": "grid", "rows": [[{"label": "A"}]],
                              "edges": [{"from": "0,0", "to": [0, 0]}]}),
            '[行号, 列号]', 'edges 坐标必须是二元数组')
m = _raises(lambda: L.expand({"layout": "hchain",
                              "nodes": [{"label": f"n{i}"} for i in range(30)]}),
            '节点过多', '节点数上限')
m = _raises(lambda: L.expand('not a dict'), '不是 JSON 对象', '非 dict 入参')
print("PASS: validate_spec — layout 白名单 / 空容器 / edges 越界定位 / 节点上限 / 非 dict")

# 隔行边：水平段会落在中间那一行上横穿节点，v1 直接拒绝
m = _raises(lambda: L.expand({"layout": "grid",
                              "rows": [[{"label": "A"}], [{"label": "B"}], [{"label": "C"}]],
                              "edges": [{"from": [0, 0], "to": [2, 0]}]}),
            '跨越了多层', '隔行边应被拒绝')
# 文案锁：绝不能写「请改为逐行连接」——那会诱导模型补一个不存在的中间节点
# 搭桥，把「图难看」升级成「图画错了（假依赖）」
assert '逐行连接' not in m and '逐行' not in m, f'报错文案诱导制造假依赖：{m}'
print(f"PASS: 隔行边被拒且文案不诱导搭桥 — {m}")

# 边标签超长 → 截断而非拒绝（标签不是骨架，截断好过丢整张图）
sp = L.validate_spec({"layout": "grid", "rows": [[{"label": "A"}], [{"label": "B"}]],
                      "edges": [{"from": [0, 0], "to": [1, 0], "label": "很长的边标签文字"}]})
assert sp['edges'][0]['label'] == "很长的边标签文字"[:L._EDGE_LABEL_MAX], sp['edges'][0]
assert len(sp['edges'][0]['label']) == L._EDGE_LABEL_MAX
print(f"PASS: 边标签超长截断为 {L._EDGE_LABEL_MAX} 字而非拒绝整图")

# ── 7c. 节点归一化：字符串简写在所有布局上必须同命运（P1-18）──

# 修复前 `_NODES_RUNNERS` 的三个算子（hchain / timeline / branch）**都没有**
# 归一化：模型写 `"nodes": ["A", "B"]` 时它们直接 `n.get('label')` →
# `AttributeError: 'str' object has no attribute 'get'`（不是 LayoutError，
# 会逃出渲染器的兜底链）；而 `_SPEC_RUNNERS` 里的算子各自调 `_norm_node` ——
# 同一份输入两种命运（模式 #54：同源是搜索题不是记忆题）。
r_s = L.expand({"layout": "hchain", "nodes": ["甲", "乙", "丙"]})
assert [n['label'] for n in r_s['nodes']] == ["甲", "乙", "丙"], r_s['nodes']
print("PASS: hchain 接受字符串节点且产物正确（判据是「不崩**且**图对」，不是只不崩）")

# 混用（字符串 + 对象）—— 归一化过的对象不能被压扁成只剩 label
r_mix = L.expand({"layout": "hchain", "nodes": ["甲", {"label": "乙", "sub": "注"}]})
assert [n['label'] for n in r_mix['nodes']] == ["甲", "乙"], r_mix['nodes']
assert r_mix['nodes'][1].get('sub') == '注', r_mix['nodes'][1]
print("PASS: hchain 字符串与对象混用，对象的 sub 未被丢失")

r_t = L.expand({"layout": "timeline", "nodes": ["起点", {"label": "终点", "sub": "12:00"}]})
assert [n['label'] for n in r_t['nodes']] == ["起点", "终点"], r_t['nodes']
print("PASS: timeline 同样接受字符串节点")

# ⚠️ branch 的字符串简写**不能**归一化了事：它是成对结构（左判定 + 右结果），
# `"判定1"` 归一化后没有 result → 右列结果框全空。图照样画得出来、几何全对、
# assert_layout 全过，只是内容没了 —— 「把崩溃换成错图」不算修好，错图没有任何
# 信号。故必须显式拒绝，并给出路（只说「缺 result」模型会重试同样的输入）。
m = _raises(lambda: L.expand({"layout": "branch", "nodes": ["判定1", "判定2"]}),
            '缺少 result', 'branch 字符串节点应显式拒绝（不是 AttributeError、更不是错图）')
assert 'hchain' in m, f'branch 报错未给出路：{m}'
print(f"PASS: branch 字符串节点被显式拒绝且给出路 — {m}")

# 对照（同一判据的另一侧）：完整输入时右列结果必须真的有字
r_b = L.expand({"layout": "branch", "nodes": [
    {"label": "判定1", "result": "结果1"},
    {"label": "判定2", "result": "结果2", "result_sub": "补充"}]})
assert [n['label'] for n in r_b['nodes']][1::2] == ["结果1", "结果2"], r_b['nodes']
assert all(n['label'] for n in r_b['nodes']), "branch 有节点标签为空"
print("PASS: branch 完整输入右列结果非空（与上一条是同一判据的两侧）")

# ── 8. 高度超限 → LayoutError ──

# timeline 行距最小，24 个节点即使行距压到下限也放不下
m = _raises(lambda: L.expand({"layout": "timeline",
                              "nodes": [{"label": f"事件{i}", "sub": "00:00"}
                                        for i in range(20)]}),
            '节点过多', '高度超限')
print(f"PASS: 高度超限（压缩行距后仍超）→ LayoutError — {m}")

# 但 9 个 timeline 节点在 640 内放得下
r_tl9 = L.expand({"layout": "timeline",
                  "nodes": [{"label": f"事件{i}", "sub": "00:00"} for i in range(9)]})
assert r_tl9['height'] <= 640
print(f"PASS: 9 个 timeline 节点正常放下（{r_tl9['height']}px，与上面的超限是同一约束的两侧）")

# ── 9. assert_layout 能抓出重叠 / 越界 ──

try:
    L.assert_layout([{'x': 0, 'y': 0, 'w': 100, 'h': 44, 'label': 'A'},
                     {'x': 50, 'y': 0, 'w': 100, 'h': 44, 'label': 'B'}], 640, 640)
    raise AssertionError('重叠节点未被抓出')
except L.LayoutError as e:
    assert '重叠' in str(e), e
try:
    L.assert_layout([{'x': 600, 'y': 0, 'w': 100, 'h': 44, 'label': 'A'}], 640, 640)
    raise AssertionError('越界节点未被抓出')
except L.LayoutError as e:
    assert '越界' in str(e), e
L.assert_layout([{'x': 0, 'y': 0, 'w': 100, 'h': 44, 'label': 'A'}], 640, 640)
print("PASS: assert_layout — 抓出重叠与越界，合法布局静默通过")

# ── 9b. assert_layout 四条新不变量的**自证** ──
# 「断言存在」≠「断言有效」：这里用故意违规的输入证明每一条真能抛错，
# 从而也证明 expand() 内部那两处调用传了全量参数、没让断言变成死代码。

# ① 边穿过非端点节点（挡路节点位于水平段中段，且不是箭头终点所在的节点）
try:
    L.assert_layout(
        [{'x': 0, 'y': 0, 'w': 100, 'h': 44, 'label': 'A'},
         {'x': 200, 'y': 120, 'w': 100, 'h': 60, 'label': '挡路'},
         {'x': 400, 'y': 200, 'w': 100, 'h': 44, 'label': 'C'}],
        640, 640,
        edges=[{'d': 'M50 44 V150 H450 V200', 'end': (450, 200), 'dir': (0, 1)}])
    raise AssertionError('边穿过非端点节点未被抓出')
except L.LayoutError as e:
    assert '穿过节点' in str(e), e

# ② 标签遮罩压住节点（遮罩不透明，压住等于把内容涂掉）
try:
    L.assert_layout([{'x': 100, 'y': 100, 'w': 100, 'h': 44, 'label': 'A'}], 640, 640,
                    labels=[{'x': 150, 'y': 110, 'w': 60, 'h': 16, 'text': '压住'}])
    raise AssertionError('标签遮罩压节点未被抓出')
except L.LayoutError as e:
    assert '压住节点' in str(e), e

# ③ 标签遮罩两两重叠（走廊分道没生效时正是这个症状）
try:
    L.assert_layout([], 640, 640,
                    labels=[{'x': 100, 'y': 100, 'w': 60, 'h': 16, 'text': '甲'},
                            {'x': 120, 'y': 105, 'w': 60, 'h': 16, 'text': '乙'}])
    raise AssertionError('标签遮罩重叠未被抓出')
except L.LayoutError as e:
    assert '遮罩重叠' in str(e), e

# ④ 连线 / 装饰元素的几何范围越界（_extent 已把它们算进画布，这是第二道闸）
try:
    L.assert_layout([], 640, 640,
                    edges=[{'d': 'M0 0 H700', 'end': (700, 0), 'dir': (1, 0)}])
    raise AssertionError('连线越界未被抓出')
except L.LayoutError as e:
    assert '连线越界' in str(e), e
try:
    L.assert_layout([], 640, 640,
                    shapes=[{'kind': 'line', 'x1': 0, 'y1': 0, 'x2': 0, 'y2': 700}])
    raise AssertionError('装饰元素越界未被抓出')
except L.LayoutError as e:
    assert '装饰元素越界' in str(e), e

# 合法输入静默通过 —— 含「贴合节点边缘出发/到达」这种不该误报的常见形态
L.assert_layout(
    [{'x': 0, 'y': 0, 'w': 100, 'h': 44, 'label': 'A'},
     {'x': 300, 'y': 200, 'w': 100, 'h': 44, 'label': 'B'}],
    640, 640,
    edges=[{'d': 'M50 44 V400 H350 V200', 'end': (350, 200), 'dir': (0, -1)}],
    labels=[{'x': 180, 'y': 392, 'w': 60, 'h': 16, 'text': '边标签'}],
    shapes=[{'kind': 'line', 'x1': 16, 'y1': 16, 'x2': 16, 'y2': 400}])
print("PASS: assert_layout 四条新不变量各自自证（边穿节点 / 遮罩压节点 / 遮罩重叠 / 越界）"
      "，贴边形态不误报")

# 自定义画布宽度（print 导出等更窄的场景）
rn = L.expand({"layout": "hchain", "nodes": [{"label": "输入"}, {"label": "输出"}]}, width=200)
assert rn['width'] == 200
_check_common('narrow', rn)
print("PASS: expand 支持自定义画布宽度（200）且不变式仍成立")

# ── 等比缩放：高度超限不再直接失败，而是按自然高度定稿 + 给小缩放系数 ──
# 竖向堆叠的 timeline 才撑得出自然高（hchain 会折行，24 节点也只有 448px）。
# 18 节点 timeline 自然高约 892 → 缩到约 0.72，仍高于可读下限 → 应成功返回
big = {"layout": "timeline",
       "nodes": [{"label": f"事件{i}", "sub": "00:00"} for i in range(1, 19)]}
rb = L.expand(big)
assert rb['height'] > L.DEFAULT_HEIGHT_CAP, \
    f"该用例应触发缩放，实际自然高 {rb['height']} 未超 {L.DEFAULT_HEIGHT_CAP}"
assert rb['scale'] < 1.0, f"应带缩放系数，实际 {rb['scale']}"
assert rb['scale'] >= L._MIN_SCALE, f"缩放系数低于下限仍返回了结果：{rb['scale']}"
assert rb['height'] * rb['scale'] <= L.DEFAULT_HEIGHT_CAP + 1, \
    "显示高度 = 自然高 × scale 必须落回画布上限内"
_check_common('scaled', rb)   # 缩放不影响几何不变式（自然坐标下校验）
print(f"PASS: 高度超限走等比缩放（自然高 {rb['height']}px → scale {rb['scale']}）")

# ── 标题占位：TITLE_H 计入自然高，全部元素整体下移 ──
rb_t = L.expand({"layout": "timeline", "title": "项目里程碑",
                 "nodes": [{"label": f"事件{i}", "sub": "00:00"} for i in range(1, 19)]})
assert rb_t['title'] == "项目里程碑" and rb_t['title_h'] == L.TITLE_H
assert rb_t['height'] == rb['height'] + L.TITLE_H, \
    f"标题应只把自然高抬高 {L.TITLE_H}px：{rb['height']} → {rb_t['height']}"
assert rb_t['scale'] < rb['scale'], '可用高度被标题吃掉后缩放系数必须变得更小'
assert min(n['y'] for n in rb_t['nodes']) == min(n['y'] for n in rb['nodes']) + L.TITLE_H, \
    '所有节点应整体下移 TITLE_H（否则会撞标题）'
assert min(lb['y'] for lb in rb_t['labels']) == min(lb['y'] for lb in rb['labels']) + L.TITLE_H, \
    '标签遮罩同样要下移'
_check_common('title', rb_t)
print(f"PASS: 标题抬高自然高 {L.TITLE_H}px 且元素整体下移"
      f"（scale {rb['scale']} → {rb_t['scale']}）")

# 窄图里标题是裁剪**下界**：画布得容得下标题，否则两端会被裁掉。
# 用 timeline 构造（hchain/grid 本就铺满画布，裁剪是空操作，测不出下界）
narrow_t = L.expand({"layout": "timeline", "title": "这是一个相当长的图表标题文字",
                     "nodes": [{"label": "A"}]})
narrow_0 = L.expand({"layout": "timeline", "nodes": [{"label": "A"}]})
assert narrow_0['width'] < narrow_t['width'], \
    f"标题比内容宽时画布应被撑宽（无标题 {narrow_0['width']} → 带标题 {narrow_t['width']}）"
print(f"PASS: 标题宽度作为裁剪下界（窄图 {narrow_0['width']}px → {narrow_t['width']}px，标题不被裁）")

# 无标题 → title_h 为 0，一切与改动前一致（零行为变化的保证）
assert rb['title_h'] == 0 and rb['title'] == ''
print("PASS: 无标题时 title_h=0，布局与标题功能引入前逐字节一致")

# 同一份图、同一画布上限：标题吃掉的 24px 正好把缩放系数推过下限 ——
# 这是 TITLE_H 真在占用高度预算的直接证据（不是「多一个节点所以拆图」）
_19 = [{"label": f"事件{i}", "sub": "00:00"} for i in range(1, 20)]
ok19 = L.expand({"layout": "timeline", "nodes": _19}, height_cap=660)
assert ok19['scale'] >= L._MIN_SCALE, f"无标题时该用例本应通过，实际 {ok19['scale']}"
m = _raises(lambda: L.expand({"layout": "timeline", "title": "标题", "nodes": _19},
                             height_cap=660),
            '请拆分成多张图', '标题占位应让同一张图越过缩放下限')
print(f"PASS: 标题确实吃掉高度预算（同图同上限：无标题 {ok19['scale']} 通过，"
      f"带标题报错拆图）")

# 缩到低于可读下限（13px 字会糊）→ 宁可报错让模型拆图
try:
    L.expand({"layout": "timeline",
              "nodes": [{"label": f"事件{i}", "sub": "00:00"} for i in range(1, 25)]})
    raise AssertionError('缩放系数低于下限时应报错拆图')
except L.LayoutError as e:
    assert '拆分成多张图' in str(e), e
print("PASS: 缩放下限生效 — 缩到低于 _MIN_SCALE 仍报错请拆图（不画糊图）")

# 未超限时零缩放，不产生任何包装（绝大多数图走这条零成本路径）
r0 = L.expand({"layout": "hchain", "nodes": [{"label": "输入"}, {"label": "输出"}]})
assert r0['scale'] == 1.0, f"未超限不该缩放，实际 {r0['scale']}"
print("PASS: 未超限时 scale 恒为 1.0")

# ── 宽度裁剪：窄图不再右半幅全空 ──
# 「先完整画出来再缩放」若只缩不放宽，等于把空白一起等比缩小。timeline / layers
# 内容只占左侧，必须连宽度也按内容裁掉。
rt = L.expand({"layout": "timeline",
               "nodes": [{"label": "开场", "sub": "00:00"},
                         {"label": "正题", "sub": "02:00"}]})
assert rt['width'] < 640, f"timeline 内容窄，应被裁剪，实际 {rt['width']}"
assert rt['width'] >= L._MIN_CANVAS_W, f"裁剪不应低于下限 {L._MIN_CANVAS_W}"
for n in rt['nodes']:
    assert n['x'] + n['w'] <= rt['width'], f"裁剪后节点出界：{n}"
_check_common('trimmed', rt)
print(f"PASS: 画布宽度按内容裁剪（timeline → {rt['width']}px）")

# 铺满的布局不受影响（内容本已到画布右缘 → 裁剪是空操作）
rfull = L.expand({"layout": "hchain", "nodes": [
    {"label": "导入内容", "sub": "URL/文件"}, {"label": "适配器提取", "sub": "正文"},
    {"label": "LLM 生成", "sub": "双产物"}, {"label": "Markdown", "sub": "落盘"}]})
assert rfull['width'] == 640, f"铺满的 hchain 不该被裁，实际 {rfull['width']}"
print("PASS: 铺满的 hchain 不受裁剪影响（仍为 640）")

# 裁剪只去空白、绝不切内容：节点与边标签都必须留在画布内
rl = L.expand({"layout": "grid",
               "rows": [[{"label": "前端"}, {"label": "缓存"}], [{"label": "数据库"}]],
               "edges": [{"from": [0, 0], "to": [1, 0], "label": "调用"}]})
for _lb in rl['labels']:
    assert _lb['x'] >= 0 and _lb['x'] + _lb['w'] <= rl['width'], f"裁剪把边标签切出界：{_lb}"
print("PASS: 边标签计入裁剪边界（不会只按节点裁而把标签切出界）")

# ── 裁剪必须把内容**拉回来**，不是把右空白换成左空白（2026-10-20）──
# 算子按 640 宽排布（内容居中），只收窄画布而不动坐标的话，窄图内容整体
# 偏移到画布右侧：右侧空白确实没了，但左侧凭空出现一大片（实测 flowchart
# 画布 412px、左侧空 232px —— 观感比不裁还差）。宽度要按**内容跨度**算，
# 再用 dx 把 minx 拉回 MARGIN；四类元素（nodes/labels/shapes/edges）必须
# 一起平移，漏掉任何一类都会让连线与节点错位。
_rst = L.expand({"layout": "state", "states": [
    {"id": "new", "label": "新建", "type": "start"},
    {"id": "paid", "label": "已支付"},
    {"id": "ship", "label": "已发货"},
    {"id": "done", "label": "已完成", "type": "end"}],
    "transitions": [{"from": "new", "to": "paid", "label": "付款"},
                    {"from": "paid", "to": "ship"},
                    {"from": "ship", "to": "done"},
                    {"from": "done", "to": "new", "label": "退货"}]})
_ext = L._extent(_rst['nodes'], _rst['labels'], _rst['shapes'], _rst['edges'])
_left, _right = _ext[0], _rst['width'] - _ext[2]
assert abs(_left - L.MARGIN) <= 1 and abs(_right - L.MARGIN) <= 1, \
    f"裁剪后左右留白应对称（左 {_left:.0f} / 右 {_right:.0f}，画布 {_rst['width']}px）"
for _e in _rst['edges']:
    _ex, _ey = _e['end']
    assert any(n['x'] <= _ex <= n['x'] + n['w'] and n['y'] <= _ey <= n['y'] + n['h']
               for n in _rst['nodes']), f"平移后连线终点与节点错位：{_e['end']}"
    _sx, _sy = L._path_segments(_e['d'])[0][:2]
    assert any(n['x'] <= _sx <= n['x'] + n['w'] and n['y'] <= _sy <= n['y'] + n['h']
               for n in _rst['nodes']), f"平移后连线起点与节点错位：{_e['d']}"
print(f"PASS: 裁剪后内容拉回 MARGIN（state 左右 {_left:.0f}/{_right:.0f}px，"
      "连线随节点同步平移）")

# ── 10. 节点形状层（shape / decor）──
# 形状只改「画成什么样」与「文字余量」，几何仍用外接矩形 —— 故这里验证的是
# 「文字放得进形状内部」，坐标正确性由各算子自己的不变量负责。

# ① rect 分支必须与形状层引入前的 node_min_w 严格同值，否则旧算子产物整体漂移
assert (L._shape_min_w("abc", "sub", None, "rect") == L.node_min_w("abc", "sub", None)
        == L._shape_min_w("abc", "sub", None))     # 第 3 项 = 缺省 shape
print("PASS: 形状层对 rect 是恒等变换（既有 4 个算子产物零漂移）")

# ② 各形状的「文字安全区」真放得下同样长度文字 —— 这是形状层唯一的存在理由
for _sh in ("rect", "stadium", "diamond", "bar"):
    _t = "测试标签"
    _node = {"x": 0, "y": 0, "w": L._shape_min_w(_t, None, None, _sh),
             "h": L.shape_h(_sh), "label": _t, "sub": None, "shape": _sh}
    _inner, _tw = L._shape_inner_w(_node), L.text_width(_t, 13)
    assert _inner >= _tw, f"{_sh}: 文字宽 {_tw:.0f} > 形状内可用宽 {_inner}（外接 {_node['w']}）"
print("PASS: rect/stadium/diamond/bar 的最小外接宽都能容下相同文字")

# ③ 菱形内接矩形只有 w/2 —— 外接宽必须翻倍，否则两腰切字
_d = L._shape_min_w("四个汉字", None, None, "diamond")
assert _d == 2 * L.node_min_w("四个汉字", None, None), _d
print(f"PASS: 菱形外接宽翻倍（{_d}px）—— 内接矩形容得下同样的文字")

# ④ 空 label 的 circle = 固定直径（state 起止圆点），不参与文字计算
assert L._shape_min_w("", None, None, "circle") == 20
print("PASS: 空 label 的 circle 用固定直径 20（state 起止圆点）")

# ⑤ _shrink_labels 按形状算可用宽度：菱形节点超长文字被截进内接矩形
_dn = {"x": 0, "y": 0, "w": 120, "h": L.shape_h("diamond"),
       "label": "非常非常长的菱形节点标签", "sub": None, "shape": "diamond"}
L._shrink_labels([_dn], None)
assert L.text_width(_dn["label"], 13) <= L._shape_inner_w(_dn), _dn["label"]
print(f"PASS: 菱形节点超长文字被截进内接矩形 → {_dn['label']!r}")

# ⑥ decor：豁免两两重叠，但**不**豁免界内校验
_n0 = {"x": 40, "y": 40, "w": 80, "h": 44, "label": "A", "sub": None}
_raises(lambda: L.assert_layout([_n0, dict(_n0, label="B")], 640, 640),
        '重叠', 'decor 引入前普通节点重叠仍须被抓')
L.assert_layout([_n0, dict(_n0, label="B", decor=True)], 640, 640)
_raises(lambda: L.assert_layout([{"x": 600, "y": 40, "w": 80, "h": 44,
                                 "label": "O", "decor": True}], 640, 640),
        '越界', 'decor 不豁免界内校验')
print("PASS: decor 豁免重叠但仍在界内校验之内")

# ⑦ _make_node 按形状 + 副文字定高（漏填就画成 44px 的菱形，文字溢出）
_mn = L._make_node({"label": "判断", "sub": "是/否", "shape": "diamond"}, 10, 10, 160, None)
assert _mn["h"] == L.shape_h("diamond", "是/否") == 68 and _mn["shape"] == "diamond", _mn
print("PASS: _make_node 按形状定高（diamond 双行 68px，rect 仍 44px）")

# ⑧ 窄节点文字不得被压成纯省略号（「空壳图」回归）
# 实测回滚点：tree 带 8 个子节点时等比收缩把每个节点压到 _MIN_NODE_W=48，
# _fit_labels 当时用写死的 avail-32 算可用宽 → 16px，连「一个中文 13px +
# 省略号 11px」都塞不下 → 9 个节点全变 '…'，出图成功但一个字都没有。
_r_shell = L.expand({"layout": "tree", "root": {
    "label": "大模型应用",
    "children": [{"label": f"材料结构化{i}"} for i in range(8)]}})
_check_common('tree-shell', _r_shell)
assert not [n for n in _r_shell['nodes'] if n['label'] == '…'], \
    f"收缩到下限也不许产出一堆省略号：{[n['label'] for n in _r_shell['nodes']]}"
assert all(n['label'] for n in _r_shell['nodes']), '每个节点至少要留下一个字'
# 收缩确实发生了（否则这条用例退化成一个普通 tree 用例，测不到窄节点路径）
assert min(n['w'] for n in _r_shell['nodes']) == L._MIN_NODE_W, \
    f"该用例须真的把节点压到下限，实际最小宽 {min(n['w'] for n in _r_shell['nodes'])}"
print(f"PASS: tree 8 子节点收缩到 {L._MIN_NODE_W}px 仍保留文字"
      f"（如 {_r_shell['nodes'][1]['label']!r}）—— 不出空壳图")

# ⑨ 两处宽度余量必须同源（_fit_labels ↔ _shape_inner_w），且空壳判据真能抛错
for _w in (40, 48, 60, 120, 300):
    _lb, _, _ = L._fit_labels("很长的节点标签文字", None, _w)
    assert _lb != '…', f"_fit_labels(avail={_w}) 把文字压成了空壳（余量算错了）"
_shell = {"x": 0, "y": 0, "w": 48, "h": 44, "label": "…", "sub": None, "shape": "rect"}
_raises(lambda: L._shrink_labels([_shell], None), '压没',
        '上游已把 label 截成空壳时，_shrink_labels 必须抛错而非放行'
        '（空壳测宽 11px 远小于可用宽，只看测量值的判据会静默失效）')
print("PASS: 空壳判据自证 —— _fit_labels 余量与 _shape_inner_w 同源，"
      "上游截空时 _shrink_labels 真能抛错")

print('Chart layout tests done')
