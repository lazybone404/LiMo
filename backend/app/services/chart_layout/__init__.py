"""chart_layout — 语义化图描述 → 几何布局（坐标全部自动计算）。

**这是包的门面**：``app.services.chart_layout`` 这个 import 路径在 2026-09-19
拆包前后**完全不变**，所有既有调用方（``chart_renderer`` / ``chart_svg`` / 测试）
一字未改。子模块的依赖方向是单向的：

    geometry ← layouts ← engine

为什么要它（2026-09-19）：
    此前让 LLM 直接手写 SVG，重叠/越界/斜线/文字超框频发，只能靠 prompt 反复叮嘱；
    而 2026-08-16 实测证明这条路走不通——28 行质量硬规则让 fast 模型
    （deepseek-v4-flash）创作成本过高，宁可跳过不画，精简到 8 行才恢复产出率。
    堆规则与出图率是直接 trade-off。

    本模块把「画图」从**生成问题**变成**渲染问题**：LLM 只答「有哪些节点、怎么排」，
    坐标/宽度/连线/标签遮罩全由这里算出来 —— 几何错误从结构上不可能发生，
    几何规则也就不必进 prompt（绕开上述 trade-off）。

移植自跨项目参考实现 ``_pi/managed/doc-build/diagram.py``（纯几何、零依赖），差异：
  - **字体宽度可注入**（``measure``）：LiMo 的中文字体是运行时探测的，
    硬编码「CJK 13px/字」会系统性低估 10–20% → 文字超框。调用方传渲染同源字体
    对象的 ``getlength``，精度必然一致（见 ``geometry._measure``）。
  - **hchain 内置自动折行**：吸收原 ``hchain2`` 的 split 语义，对 LLM 少一个字段。
  - **_edge_path 额外产出终点/方向**：PIL 不支持 ``<marker>``，箭头要用
    ``<polygon>`` 手绘，方向让布局器直接给出，避免生成器反向解析路径。
  - **新增 timeline**：视频笔记的核心诉求（时间戳序列），原实现没有。
  - **去掉 extra / back_edges**：给 LLM 的 schema 不含手工坐标——那会重新引入几何错误。
  - 画布默认 640（LiMo 图表规范上限），原实现用 720。

本模块零 PIL / 零 XML 依赖，可独立单测。

``__all__`` 里带下划线的名字是**有意导出**的：测试与 ``chart_svg`` 直接使用它们
（``_edge_path`` 校验路径正交性、``_truncate_to_width`` 验证截断、``_EDGE_LABEL_MAX``
验证上限）。不用 ``import *`` —— 星号导入会把 ``math``/``re`` 这类模块也带出去，
且静态检查看不到来源。
"""

from .engine import assert_layout, expand, validate_spec
from .geometry import (
    DEFAULT_HEIGHT_CAP,
    DEFAULT_WIDTH,
    GAP,
    GAP_X,
    H,
    MARGIN,
    SHAPES,
    TITLE_H,
    TITLE_SIZE,
    UNIT,
    LayoutError,
    node_min_w,
    r4,
    shape_h,
    text_width,
)
from .layouts import (
    _branch,
    _flowchart,
    _grid,
    _hchain,
    _layers,
    _NODES_RUNNERS,
    _quadrant,
    _SPEC_RUNNERS,
    _sequence,
    _state,
    _timeline,
    _tree,
)
from .geometry import (  # noqa: E402  —— 私有工具，测试直接用
    _EDGE_LABEL_MAX,
    _GRID_GAP_MAX,
    _GUTTER_W,
    _LAYOUTS,
    _MAX_NODES,
    _MIN_CANVAS_W,
    _MIN_NODE_W,
    _MIN_SCALE,
    _ROW_GAP,
    _distribute,
    _edge_path,
    _extent,
    _fit_labels,
    _elbow,
    _make_edge,
    _make_node,
    _measure,
    _overlap,
    _path_bbox,
    _path_segments,
    _shape_inner_w,
    _shape_min_w,
    _shift_path_y,
    _shift_title_space,
    _shrink_labels,
    _truncate_to_width,
    _wrap_rows,
)
from .engine import _count_nodes, _trim_width  # noqa: E402
from .layouts import (  # noqa: E402
    _corridor_index,
    _CORRIDOR_PITCH,
    _grid_edge_label,
    _norm_node,
    _bfs_rank,
    _norm_tree,
    _topo_layers,
    _tree_stats,
)

__all__ = [
    # 门面（公开契约）
    "expand", "validate_spec", "assert_layout",
    "LayoutError", "text_width", "node_min_w", "r4",
    # 常量（调用方与测试依赖，勿改语义）
    "DEFAULT_WIDTH", "DEFAULT_HEIGHT_CAP", "TITLE_H", "TITLE_SIZE",
    "MARGIN", "GAP", "GAP_X", "H", "UNIT",
    # 节点形状
    "SHAPES", "shape_h", "_shape_min_w", "_shape_inner_w", "_make_node",
    # 算子
    "_hchain", "_timeline", "_branch", "_layers", "_grid", "_tree", "_flowchart", "_state", "_sequence", "_quadrant",
    "_NODES_RUNNERS", "_SPEC_RUNNERS",
    "_norm_node", "_norm_tree", "_tree_stats", "_topo_layers", "_bfs_rank",
    "_grid_edge_label", "_corridor_index", "_CORRIDOR_PITCH",
    # 引擎内部
    "_count_nodes", "_trim_width",
    # 几何工具（私有但被测试/渲染链直接使用）
    "_measure", "_truncate_to_width", "_distribute", "_wrap_rows",
    "_fit_labels", "_shrink_labels", "_make_edge", "_edge_path",
    "_path_segments", "_path_bbox", "_extent", "_overlap",
    "_shift_path_y", "_shift_title_space",
    "_LAYOUTS", "_ROW_GAP", "_MAX_NODES", "_EDGE_LABEL_MAX",
    "_MIN_NODE_W", "_MIN_CANVAS_W", "_MIN_SCALE", "_GRID_GAP_MAX", "_GUTTER_W",
]
