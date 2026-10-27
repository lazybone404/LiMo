"""本地向量化插件（bge-small-zh-v1.5 ONNX int8）— 参考实现。

**形态：进程外 + 常驻子进程**（`EmbeddingPlugin`，不是 `EnginePlugin`）。

- 为什么进程外：`onnxruntime` / `tokenizers` 是重依赖，打进安装包会让体积翻几倍，
  且要跟着上游版本走。放进插件自己的 venv，主程序零负担（口子原则的实例）。
- 为什么**常驻**：模型加载 3–5 秒，而查询侧 `QUERY_TIMEOUT = 3.0` —— 一次性进程
  等于每次搜索都超时。协议见 `local_embedding/server.py` 的文件头。

照抄即用：把本文件 + 同名子目录（server.py + requirements.txt）拷贝到
`~/.limo/plugins/`，在设置 → 插件里点「一键安装依赖」，再把模型文件放到
`~/.limo/models/bge-small-zh-v1.5-onnx/`，重启 LiMo。

⚠️ **这个插件的 name 与内置引擎相同（`local-bge-small`）**，按「用户优先」规则
覆盖内置。于是打包版（内置那份缺 `tokenizers` 而不可用）装上插件后**真能离线跑**；
源码版不装插件则继续用内置那份，两者行为一致。
"""
from pathlib import Path

from app.engines.plugin_loader import EmbeddingPlugin

_HERE = Path(__file__).resolve().parent

#: 模型目录默认值 —— **与内置引擎同一个位置**，用户此前用过内置本地引擎的话
#: 文件已经在了，装插件不必重复下载 22.8MB。
DEFAULT_MODEL_DIR = "~/.limo/models/bge-small-zh-v1.5-onnx"
DEFAULT_ONNX = "model_int8.onnx"


class LocalBgeSmallPlugin(EmbeddingPlugin):
    name = "local-bge-small"

    # 进程外 + 常驻：`-u` 关掉 Python 的 stdout 缓冲（主进程按行等响应，
    # 留在缓冲区里的一行等于没发）。server.py 自己每行也 flush，两层保险。
    command = [
        "local_embedding/.venv/Scripts/python.exe",
        "-u",
        "local_embedding/server.py",
    ]
    # Linux/macOS: ["local_embedding/.venv/bin/python3", "-u", "local_embedding/server.py"]

    license = "MIT"
    display_name = "本地模型（bge-small-zh · 免费 · 不上传）"
    user_desc = "bge-small-zh-v1.5 ONNX int8，512 维，完全本地运行（插件版，打包版可用）"

    # ── 向量契约（四元组身份的一部分）──
    #
    # ⚠️ **换权重必须改 model_id**：`(model_id, dimension, chunk_size, schema_version)`
    # 是向量库的整体身份，`model_id` 没变时 `ensure_identity` 判「口径没变」，
    # 新老向量就混在同一张表里 —— 检索质量下降、零报错。int8 / fp32 的向量不同，
    # 所以量化档要写进名字里。
    #
    # ⚠️ `model_id` **刻意不做成配置项**：配置改了而 adapter 手上的值不变（它在
    # 启动时定下），两边不一致会让「报告值与声明值不符」直接判插件不可用。
    model_id = "bge-small-zh-v1.5-int8"
    dimension = 512

    # 前端据此显示「免费 / 零外发」那套成本文案（不靠名字猜）
    is_local = True

    # ── 自检材料 ──
    #
    # 进程外插件的依赖在**子 venv** 里，adapter 用 `probe_venv` 判（不是 `probe`）。
    # 这里只声明「要哪些包」，不用自己写 probe —— 写错方向会让装好的插件永远显示
    # 不可用（见 `app/core/deps.py` 两个函数的文档）。
    requires_modules = ("onnxruntime", "tokenizers")

    # 模型文件路径来自用户配置，不能写死 → 放在下面的 precheck() 里判。
    requires_files = ()

    config_schema = [
        {"name": "model_dir", "type": "str", "label": "模型目录",
         "default": DEFAULT_MODEL_DIR,
         "hint": "含 tokenizer.json 与 ONNX 文件的目录"},
        {"name": "onnx_name", "type": "str", "label": "ONNX 文件名",
         "default": DEFAULT_ONNX,
         "hint": "换权重时请同时修改声明文件里的 model_id"},
    ]

    timeout = 300

    def precheck(self):
        """模型文件在不在。依赖检查由 adapter 按 `requires_modules` 做完了。

        ⚠️ 会被 `GET /api/plugins` 调用，**必须轻**：只 `Path.exists()`，
        不 import onnxruntime、不加载模型（那会 3–5 秒，设置页每次刷新都等一次）。
        """
        cfg = self.plugin_config or {}
        model_dir = Path(str(cfg.get("model_dir") or DEFAULT_MODEL_DIR)).expanduser()
        onnx_name = str(cfg.get("onnx_name") or DEFAULT_ONNX)
        missing = [str(p) for p in (model_dir / "tokenizer.json", model_dir / onnx_name)
                   if not p.exists()]
        if missing:
            return (
                "缺少模型文件：" + "、".join(missing)
                + "。把 bge-small-zh-v1.5 的 ONNX 版放进该目录（需 tokenizer.json"
                " 与 " + onnx_name + "），或在插件设置里改「模型目录」。"
            )
        return None

    def embed(self, texts):
        """进程内模式才会被调用 —— 本插件走 `command`，这个方法留着只为契约完整。

        真跑起来时基类会抛 `NotImplementedError`（它是 `EmbeddingPlugin` 的默认实现）；
        这里显式说明一句，免得读者以为漏了实现。
        """
        raise NotImplementedError(
            "本插件走进程外常驻进程（command），embed() 由 server.py 提供"
        )
