"""`python -m limo_cli` 入口 —— 与安装后的 `limo` 完全等价。

存在理由：开发机上没装入口点（`pyproject.toml` 的 `[project.scripts]` 要
`pip install -e .` 才生效），而 CLI 必须在未安装状态下也能跑（`scripts/check-cli`
用的就是这条路径）。**两条入口指向同一个 `main`**，不存在第二份实现。
"""

import sys

from limo_cli.cli import main

if __name__ == "__main__":
    sys.exit(main())
