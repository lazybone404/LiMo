"""LiMo 命令行（`limo`）—— 只读接口层。

三层架构的**最外圈**：

    limo_cli（本包：命令解析 / 渲染 / 退出码）
        ↓ 同一个 QueryService（不是「实现相似」，是同一份）
    app/services/query.py（契约成形：分页 / ID 解析 / 版本编排 / meta 装配）
        ↓
    Store / SearchEngine（数据真相）

两条数据源路径，**同一份契约形状**：

- 默认：`GET /api/cli/*`（后端已启动，走进程边界）
- `doctor --local`：本进程直开 `Store` + `QueryService`（后端没起时诊断用）

⚠️ **CLI 不解析库的存储格式**：`index.json` 的形状、两版制的 `{mode}.md` /
`{mode}.prev.md` 命名、`<!-- limo-meta -->` 头信封全是实现细节。在这里照抄一遍就是
第 6 个副本（现有 5 处，见 `services/query.py` 的模块 docstring），而格式演进时
**漏改的那处不报错**——它只是静默读错。

⚠️ **第一版刻意只读**：写操作（import / generate / edit / delete）的成本、并发、
幂等、误触发风险都另算，不在这层（详见 `AGENTS.md`）。
"""

from pathlib import Path


def _read_version() -> str:
    """版本号取自仓库根的 `VERSION`（`sync-version.py` 的唯一版本源）。

    ⚠️ **当前打包产物不含 CLI**：PyInstaller 从 `main.py` 出发做依赖分析，不会收集
    `limo_cli`。故「桌面版用户也能敲 `limo`」这件事**尚未决策**（要么把它加进 spec，
    要么接受它只面向仓库/开发机）—— 别把现状读成设计意图。今天从仓库跑
    `python -m limo_cli` 或 `pip install -e backend/` 都可用。

    读不到 `VERSION` 时给 `unknown` 而不是抛错：版本号缺失不该让一条 `limo list` 失败。
    """
    try:
        root = Path(__file__).resolve().parents[2]
        return (root / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


__version__ = _read_version()
