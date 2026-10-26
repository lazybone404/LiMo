"""PyMuPDF4LLM 插件 — 参考实现。

进程外模式：command 指向子目录的独立 venv（依赖隔离）。
照抄即用：把本文件 + 同名子目录（engine.py + requirements.txt）拷贝到 ~/.limo/plugins/，
进入子目录建 venv 装依赖（见 README.md），重启 LiMo 即生效。
"""
from pathlib import Path

from app.core.deps import probe_venv
from app.engines.plugin_loader import EnginePlugin

# 声明文件所在目录 —— 与 command 里的相对路径同一个基准
# （`_run_external` 解析 command 时用的也是它）
_HERE = Path(__file__).resolve().parent


class PyMuPDF4LLMPlugin(EnginePlugin):
    name = "pymupdf4llm"
    category = "doc"
    license = "AGPL-3.0"          # 仅展示用（前端 license 徽章），主程序不做协议判断
    display_name = "PyMuPDF4LLM PDF 解析"
    user_desc = "PDF 文字+表格+嵌入图+矢量图解析（效果最好的 PDF 转录参考实现）"
    file_types = [".pdf"]

    # 进程外模式（默认主体）：command 指向子目录独立 venv
    command = ["pymupdf4llm/.venv/Scripts/python.exe", "pymupdf4llm/engine.py"]
    # Linux/macOS: ["pymupdf4llm/.venv/bin/python3", "pymupdf4llm/engine.py"]

    timeout = 600

    # config_schema 简版：前端设置页渲染表单 → 保存后经 stdin JSON config 传给引擎
    # 内嵌图片统一 PNG（嵌入式最常见格式），不提供格式选择
    config_schema = [
        {"name": "dpi",          "type": "int",    "label": "图片分辨率", "default": 150, "hint": "嵌入图片 DPI（72-300）"},
        {"name": "write_images", "type": "bool",   "label": "嵌入图片",   "default": True},
    ]

    def precheck(self):
        """就绪自检：子 venv 建没建、依赖装没装。

        ⚠️ 这里**不能**用 `core.deps.probe("pymupdf4llm")`：那是问主进程能不能
        `import`，而进程外插件的依赖在**子 venv** 里，主进程的 `sys.path` 中根本
        没有它们 —— 用 `probe` 会让每个**装好了**的插件都显示不可用。
        详见 `app/core/deps.py::probe_venv` 的文档。
        """
        return probe_venv(
            _HERE / "pymupdf4llm" / ".venv",
            modules=("pymupdf4llm",),
            hint="在 设置 → 插件 里点「一键安装依赖」",
        )
