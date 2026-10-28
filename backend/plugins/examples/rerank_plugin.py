"""本地重排插件（bge-reranker-v2-m3 ONNX）— 参考实现。

**形态：进程外 + 常驻子进程**（`RerankPlugin`，第三族基类）。

三类、三种形状：

| 基类 | 契约 | 例子 |
|---|---|---|
| `EnginePlugin` | 一个文件 → 一段文本 | pymupdf4llm / tesseract / faster-whisper |
| `EmbeddingPlugin` | 一组文本 → 一组向量 | bge-small |
| **`RerankPlugin`** | **一个查询 + 一组候选 → 一组「下标 + 分数」** | 本文件 |

- 为什么进程外：`onnxruntime` / `tokenizers` 是重依赖（口子原则的实例，同本地向量化）。
- 为什么**常驻**：bge-reranker 量级 500MB+，冷读几十秒，而重排发生在**一次在线
  检索里**（`RERANK_TIMEOUT = 5.0` 秒）。每轮 Popen 等于每次搜索都超时，而用户
  看到的提示是「重排超时」—— 插件根本不联网，他会去查网络。协议见
  `rerank/server.py` 的文件头。

## 照抄即用

1. 把本文件 + 同名子目录（`rerank/`：`server.py` / `requirements.txt`）拷到
   `~/.limo/plugins/`
2. 设置 → 插件里点「一键安装依赖」（走清华镜像）
3. 模型文件放 `~/.limo/models/bge-reranker-v2-m3-onnx/`（需 `tokenizer.json` +
   `model.onnx`；从 HuggingFace `BAAI/bge-reranker-v2-m3` 导出 ONNX）
4. 重启 LiMo，设置 → 通用 → 检索索引 → 重排，引擎下拉里选它

⚠️ **本轮只交付模板与契约测试，真实模型（约 544MB）的下载与精度验证不在自动化
范围里** —— 权重不进仓库、也不进安装包（那正是走插件的原因）。想验证它真的能提升
排序，用 `_experiments/07-rerank/` 的脚本换引擎跑一遍。

⚠️ **重排只作用于 AI 对话的检索**，文档库搜索框不重排（那是 250ms 防抖的逐字联想）。
"""
import os
from pathlib import Path

from app.engines.plugin_loader import RerankPlugin

#: 模型目录默认值。与 `rerank/server.py` 的 `DEFAULT_MODEL_DIR` **必须一致** ——
#: 两处不同源时，`precheck()` 查的是一个目录、子进程读的是另一个，现象是
#: 「设置页说就绪，一检索就报缺文件」。
DEFAULT_MODEL_DIR = "~/.limo/models/bge-reranker-v2-m3-onnx"
DEFAULT_ONNX = "model.onnx"


class LocalBgeRerankerPlugin(RerankPlugin):
    #: 名字里带 `local-` 前缀是给用户看的（与 `local-bge-small` 同一套命名），
    #: 不参与任何判据 —— 成本文案读 `is_local`，不靠名字猜。
    name = "local-bge-reranker"

    # 进程外 + 常驻：`-u` 关掉 Python 的 stdout 缓冲（主进程按行等响应，留在
    # 缓冲区里的一行等于没发）。server.py 自己每行也 flush，两层保险。
    #
    # ⚠️ **解释器路径按平台选**（见下面 `__init__`）：venv 的目录布局在 Windows
    # 与 POSIX 上不同（`Scripts/python.exe` vs `bin/python3`）。写死 Windows 版
    # 而 `probe_venv` 按平台判的失效形态是「自检说就绪，一 spawn 就 FileNotFoundError」。
    command = [
        "rerank/.venv/Scripts/python.exe",
        "-u",
        "rerank/server.py",
    ]

    #: 模型权重来自 `BAAI/bge-reranker-v2-m3`（**Apache-2.0**）。
    license = "Apache-2.0"
    display_name = "本地模型（bge-reranker · 免费 · 不上传）"
    user_desc = "bge-reranker-v2-m3 ONNX，交叉编码器重排，完全本地运行（插件版）"

    # ⚠️ **换权重 / 换量化档必须改 model_id**：分数漂移时只有它是线索 ——
    # 日志里两次检索都叫「重排」，凭分数分不出「这轮换了模型」还是「数据变了」。
    # 与向量那边不同，它**不进任何身份四元组**（重排不落盘），改它不需要重建。
    model_id = "bge-reranker-v2-m3"

    #: 单次能吃的候选条数。**能力声明不是调优旋钮**：编小会让 `RerankSearcher`
    #: 白丢候选（它按这个值截窗口），编大会让子进程 OOM 或超时。
    #: bge-reranker 的输入上限是 512 token/对，条数本身不设限；100 是「一轮重排
    #: 在 CPU 上还能落在 5 秒内」的实测档位。
    max_documents = 100

    is_local = True

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
         "hint": "换权重时请同时修改本插件文件里的 model_id"},
    ]

    #: 子进程的 ready 握手超时（秒）。**比 embedding 那份大**：544MB 权重的冷读
    #: 在机械盘 / 杀毒软件扫描下可以到半分钟以上。超了只影响「插件可用性判定」，
    #: 不影响已加载后的单次检索（那个由 `RERANK_TIMEOUT` 管）。
    timeout = 300

    def __init__(self):
        # venv 的目录布局按平台不同：Windows 是 `Scripts/python.exe`，POSIX 是
        # `bin/python3`。类属性留 Windows 版（本项目的发行目标），其余平台在实例上
        # 换掉 —— 不这么做的话 Linux/macOS 上会「自检说就绪、一 spawn 就
        # FileNotFoundError」（`probe_venv` 是纯文件系统检查，它不读 `command`，
        # 两边判据根本不同源）。
        if os.name != "nt":
            self.command = [
                "rerank/.venv/bin/python3",
                "-u",
                "rerank/server.py",
            ]

    def precheck(self):
        """模型文件在不在。依赖检查由 adapter 按 `requires_modules` 做完了。

        ⚠️ 会被 `GET /api/plugins` 调用，**必须轻**：只 `Path.exists()`，
        不 import onnxruntime、不加载模型（那会几十秒，设置页每次刷新都等一次）。
        """
        cfg = self.plugin_config or {}
        model_dir = Path(str(cfg.get("model_dir") or DEFAULT_MODEL_DIR)).expanduser()
        onnx_name = str(cfg.get("onnx_name") or DEFAULT_ONNX)
        missing = [str(p) for p in (model_dir / "tokenizer.json", model_dir / onnx_name)
                   if not p.exists()]
        if missing:
            return (
                "缺少模型文件：" + "、".join(missing)
                + "。把 bge-reranker-v2-m3 的 ONNX 版放进该目录（需 tokenizer.json"
                " 与 " + onnx_name + "），或在插件设置里改「模型目录」。"
            )
        return None

    def rerank(self, query, documents):
        """进程内模式才会被调用 —— 本插件走 `command`，这个方法留着只为契约完整。

        真跑起来时基类会抛 `NotImplementedError`（它是 `RerankPlugin` 的默认实现）；
        这里显式说明一句，免得读者以为漏了实现。
        """
        raise NotImplementedError(
            "本插件走进程外常驻进程（command），rerank() 由 rerank/server.py 提供"
        )
