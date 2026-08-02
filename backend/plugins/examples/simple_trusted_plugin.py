"""极简 trusted 进程内插件 — 参考实现。

trusted=true 是显式信任声明（**纯技术含义**）：允许进程内 import。
适用：轻量逻辑、无重依赖的自研代码（简单格式转换、纯文本处理）。
进程内 import = 等同主程序权限，崩溃/依赖风险由插件作者自担。

照抄即用：拷贝本文件到 ~/.limo/plugins/，重启 LiMo 即生效。
"""
from app.engines.plugin_loader import EnginePlugin


class SimpleTextPlugin(EnginePlugin):
    name = "simple-text"
    category = "doc"
    trusted = True                       # 显式信任：允许进程内 import
    display_name = "简易文本转换"
    user_desc = "把 .txt 包装成 Markdown（纯文本、无依赖）的参考实现"
    file_types = [".txt"]

    def process(self, file_path: str, config: dict) -> str:
        with open(file_path, encoding="utf-8", errors="ignore") as f:
            text = f.read()
        return f"```text\n{text}\n```"
