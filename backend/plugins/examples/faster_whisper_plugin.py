"""Faster-Whisper 本地语音转写插件 — 参考实现。

进程外模式：command 指向子目录独立 venv + engine.py。
`faster-whisper`（CTranslate2 推理）是本地转写里体积/速度/效果最平衡的一档，
而它的依赖（ctranslate2 / tokenizers / onnxruntime）正是「不该塞进主包」的那类
（数百 MB + 平台相关二进制 + CUDA 变体），故走插件而不是内置。

⚠️ **首次转写要下载模型**（默认 small 约 250MB，从 HuggingFace）—— 也就是说
「装好依赖」不等于「现在就能转写」，第一次会额外等几分钟。国内网络务必保留
默认的 `hf_endpoint`（默认已指向镜像），详见本目录 README.md。

照抄即用：把本文件 + 同名子目录（engine.py + requirements.txt + README.md）
拷贝到 ~/.limo/plugins/，在设置页点「一键安装依赖」，重启 LiMo 生效。
"""
from pathlib import Path

from app.core.deps import probe_venv
from app.engines.plugin_loader import EnginePlugin

# 声明文件所在目录 —— 与 command 里的相对路径同一个基准
_HERE = Path(__file__).resolve().parent


class FasterWhisperPlugin(EnginePlugin):
    name = "faster-whisper"
    category = "asr"                # 语音转写：引擎绑定走 engine_bindings.asr
    license = "MIT"
    display_name = "Faster-Whisper 本地转写"
    user_desc = "本地语音转写（离线可用、无需 API Key；首次转写需下载模型）"
    # ASR 处理的是已抽好的音频，不走 fileTypeMap（file_types 不设 ⇒ accept 恒 True）

    # 进程外模式（默认主体）：command 指向子目录独立 venv
    command = ["faster_whisper/.venv/Scripts/python.exe", "faster_whisper/engine.py"]
    # Linux/macOS: ["faster_whisper/.venv/bin/python3", "faster_whisper/engine.py"]

    # 一小时的音视频 + 首次模型下载都可能远超默认 300s
    timeout = 3600

    config_schema = [
        {"name": "model", "type": "select", "label": "模型", "default": "small",
         "options": ["tiny", "base", "small", "medium", "large-v3"],
         "hint": "越大越准也越慢越占内存；中文建议 small 起步"},
        {"name": "device", "type": "select", "label": "运行设备", "default": "cpu",
         "options": ["cpu", "cuda", "auto"],
         "hint": "cuda 需自备 NVIDIA 驱动与 cuBLAS/cuDNN，缺失时会显式报错而非静默退回 CPU"},
        {"name": "compute_type", "type": "select", "label": "计算精度", "default": "int8",
         "options": ["int8", "int8_float16", "float16", "float32"],
         "hint": "CPU 用 int8；cuda 可用 float16（显存换速度）"},
        {"name": "language", "type": "string", "label": "语言", "default": "",
         "hint": "留空自动检测；指定可减少误判（如 zh / en / ja）"},
        {"name": "vad_filter", "type": "bool", "label": "静音过滤", "default": True,
         "hint": "跳过静音段——关闭后长静音处容易产出重复的幻觉字幕"},
        {"name": "hf_endpoint", "type": "string", "label": "模型下载源", "default": "https://hf-mirror.com",
         "hint": "留空走 HuggingFace 官方；国内建议保留镜像"},
        {"name": "download_root", "type": "string", "label": "模型缓存目录", "default": "",
         "hint": "留空用 HuggingFace 默认缓存（~/.cache/huggingface）"},
    ]

    def precheck(self):
        """就绪自检：子 venv 建没建、两个真正的依赖装没装。

        ⚠️ 这里**不能**用 `core.deps.probe(...)`：那是问主进程能不能 `import`，
        而进程外插件的依赖在**子 venv** 里，主进程 `sys.path` 中根本没有它们 ——
        用 `probe` 会让每个**装好了**的插件都显示不可用。

        装的模块名：`ctranslate2` 是推理内核，`faster_whisper` 是外层 API，
        两者任一缺失都跑不起来（`faster-whisper` 装上但 ctranslate2 导入失败，
        是这一栈最常见的坏状态）。

        ⚠️ **模型没下载不算「未就绪」**：那是首次转写时才有的事，且缓存位置可由
        `download_root` 改到任何地方 —— 在这里猜它只会误报。用户会在第一次转写
        时多等几分钟，README 里已写明。
        """
        return probe_venv(
            _HERE / "faster_whisper" / ".venv",
            modules=("faster_whisper", "ctranslate2"),
            hint="在 设置 → 插件 里点「一键安装依赖」",
        )
