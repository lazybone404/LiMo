"""极简 trusted 进程内插件 — 参考实现。

trusted=true 是显式信任声明（**纯技术含义**）：允许进程内 import。
适用：轻量逻辑、无重依赖的自研代码（简单格式转换、纯文本处理）。
进程内 import = 等同主程序权限，崩溃/依赖风险由插件作者自担。

⚠️ 依赖装在哪：进程内插件**没有独立 venv**，`requirements.txt` 不参与「一键安装」
（接口对进程内返回 `status="inproc"`，一个目录都不建 —— 装进 venv 也没人从那里加载）。
源码运行时用后端自身的 Python 装；**打包版主环境是 exe，装不了** ⇒ 自带依赖的插件
请改用进程外模式（`command` + `requirements.txt`）。

⚠️ 异常与返回值：`process()` 抛出的任何异常都由加载器包成 `FatalError`（带插件名，
原始 traceback 进日志）；返回值必须是 `str` 或 `dict`，返回别的类型会当场报错
（不会被当成「这份文件没内容」放行到后续链路）。

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

    # ── 就绪自检（本插件零依赖，无需重写；有依赖时照下面这行写）──
    #
    #     from app.core.deps import probe
    #     def precheck(self):
    #         return probe("markdown_it", attrs=("MarkdownIt",))
    #
    # ⚠️ **trusted（进程内）插件用 `probe`，进程外插件用 `probe_venv`**：
    #    前者的依赖就在主进程 `sys.path` 里，后者在子 venv 里（主进程 import 不到，
    #    用 `probe` 会永远报「无法导入」）。见 `app/core/deps.py` 两个函数的文档。
