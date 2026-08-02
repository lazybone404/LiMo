"""Tesseract 本地 OCR 插件 — 参考实现（非官方插件）。

进程外模式：command 指向子目录独立 venv + engine.py，
engine.py 通过 subprocess 调用插件目录自带的 tesseract.exe（便携版）。

照抄即用：把本文件 + 同名子目录（engine.py + README）拷贝到 ~/.limo/plugins/，
子目录内放入 tesseract 便携版（见 README.md），建 venv，重启 LiMo 生效。
"""
from app.engines.plugin_loader import EnginePlugin


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
