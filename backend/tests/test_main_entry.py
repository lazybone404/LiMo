"""backend/main.py 入口的启动安全性守卫。

⚠️ 真实踩过的坑（0.5.12）：`faulthandler.enable()` 在模块顶部执行，而
`sys.stderr is None` 的兜底当时写在 `__main__` 段 —— **晚于它**。打包版
（`console=False`）在桌面壳未重定向 stderr 时（用户直接双击 exe、或日志文件建不出来），
`faulthandler.enable()` 拿到 `None` 直接抛 `RuntimeError: sys.stderr is None`，
后端秒退、界面停在启动失败屏 —— 而源码读起来完全正常（两段各自都对，只是顺序错了）。

两条守卫：一条钉源码顺序（便宜、直接指向根因）；一条真跑一遍打包形态下的导入
（顺序对了但换个写法绕过去的，由它兜住）。
"""

import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
MAIN_PY = BACKEND_DIR / "main.py"


def _first_code_line(lines: list[str], needle: str) -> int:
    """第一处**代码行**（排除注释）里 needle 所在的行号。"""
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            continue
        if needle in line:
            return i
    raise AssertionError(f"main.py 里找不到代码行：{needle}")


def test_stderr_归一化在_faulthandler_之前():
    lines = MAIN_PY.read_text(encoding="utf-8").splitlines()
    guard = _first_code_line(lines, "sys.stderr = open(")
    faulthandler = _first_code_line(lines, "faulthandler.enable()")
    assert guard < faulthandler, (
        "sys.stderr 的 None 兜底必须排在 faulthandler.enable() 之前 —— "
        "否则 console=False 打包版会以 RuntimeError: sys.stderr is None 启动即崩"
    )


def test_打包形态下导入_main_不崩溃():
    """模拟 PyInstaller console=False：stdout/stderr 均为 None + sys.frozen。"""
    code = (
        "import sys\n"
        "real = sys.stdout\n"
        "sys.frozen = True\n"
        "sys.stderr = None\n"
        "sys.stdout = None\n"
        "import main\n"
        "sys.stdout = real\n"
        "sys.stderr = real\n"
        "print('ok')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "ok" in proc.stdout
