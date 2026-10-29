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

⚠️ **CLI 不解析库的存储格式**：`index.json` 的形状、版本归档的 `{mode}/v{N}.md`
命名、`<!-- limo-meta -->` 头信封全是实现细节。在这里照抄一遍就是
第 6 个副本（现有 5 处，见 `services/query.py` 的模块 docstring），而格式演进时
**漏改的那处不报错**——它只是静默读错。

⚠️ **第一版刻意只读**：写操作（import / generate / edit / delete）的成本、并发、
幂等、误触发风险都另算，不在这层（详见 `AGENTS.md`）。
"""

import sys
from pathlib import Path


def _read_version() -> str:
    """版本号取自仓库根的 `VERSION`（`sync-version.py` 的唯一版本源）。

    ⚠️ **打包产物含 `limo_cli` 这个包，但不含 `limo` 命令**（2026-10-29 起）：`main.py`
    顶部的 `--mcp` 短路会 import `limo_cli.mcp_server`，故 spec 已把它收进
    `hiddenimports`；而 `[project.scripts]` 的 `limo` 只对 `pip install -e` 的环境生效，
    安装版用户桌面上没有那条命令。**别把「包在」读成「CLI 能用」**。

    frozen 下没有仓库根，改从 `sys._MEIPASS/VERSION` 取（spec 的 datas 把它带进包内）。
    两条都读不到时给 `unknown` 而不是抛错：版本号缺失不该让一条 `limo list` 失败。
    """
    try:
        if getattr(sys, "frozen", False):
            base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        else:
            base = Path(__file__).resolve().parents[2]
        return (base / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


__version__ = _read_version()
