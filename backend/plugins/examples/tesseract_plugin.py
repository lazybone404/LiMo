"""Tesseract 本地 OCR 插件 — 参考实现。

进程外模式：command 指向子目录独立 venv + engine.py，
engine.py 通过 subprocess 调用插件目录自带的 tesseract.exe（便携版）。

照抄即用：把本文件 + 同名子目录（engine.py + README）拷贝到 ~/.limo/plugins/，
子目录内放入 tesseract 便携版（见 README.md），建 venv，重启 LiMo 生效。
"""
import shutil
from pathlib import Path

from app.core.deps import probe_venv
from app.engines.plugin_loader import EnginePlugin

# 声明文件所在目录 —— 与 command 里的相对路径同一个基准
_HERE = Path(__file__).resolve().parent

# 便携版放置位置（README.md 里约定的结构）
_PORTABLE_EXE = _HERE / "tesseract" / "tesseract" / "tesseract.exe"


class TesseractPlugin(EnginePlugin):
    name = "tesseract"
    category = "ocr"
    license = "Apache-2.0"          # 仅展示用，主程序不做协议判断
    display_name = "Tesseract 本地 OCR"
    user_desc = "本地 OCR 文字提取（离线可用，无需 API Key）"
    # OCR 处理的是图片，不走 fileTypeMap（file_types 不设）

    # 进程外模式（默认主体）：command 指向子目录独立 venv
    command = ["tesseract/.venv/Scripts/python.exe", "tesseract/engine.py"]
    # Linux/macOS: ["tesseract/.venv/bin/python3", "tesseract/engine.py"]

    timeout = 300

    config_schema = [
        {"name": "lang", "type": "string", "label": "识别语言", "default": "chi_sim+eng", "hint": "tesseract 语言包，如 chi_sim+eng"},
        {"name": "psm", "type": "select", "label": "页面分割", "default": "3", "options": ["3", "6"], "hint": "3=自动分页 6=统一文本块"},
    ]

    def precheck(self):
        """就绪自检：两件事，缺一不可。

        1. **子 venv 在不在** —— `engine.py` 本身零第三方依赖，但 `command` 指向
           的是子 venv 的解释器。不检查它的话，用户会看到「插件就绪、点生成立刻
           报找不到 python.exe」。
        2. **tesseract 二进制在不在** —— 这是本插件唯一的外部依赖，也是最容易
           缺的那一半（要用户自己去下便携版）。

        ⚠️ 两者都必须是**报告**而不是「第一次执行时抛异常」：自检发生在设置页，
        用户还没付出任何东西。
        """
        reason = probe_venv(
            _HERE / "tesseract" / ".venv",
            hint="在 设置 → 插件 里点「一键安装依赖」",
        )
        if reason:
            return reason

        if _PORTABLE_EXE.is_file() or shutil.which("tesseract"):
            return None
        return (
            "缺少 tesseract 可执行文件 —— 从 "
            "https://github.com/UB-Mannheim/tesseract/releases 下载安装包，"
            "把 tesseract.exe 与 tessdata/ 放到 "
            f"{_HERE / 'tesseract' / 'tesseract'}（详见插件目录 README.md）"
        )
