# 插件系统 — 契约 · 安装 · 发布（EnginePlugin v1）

> 加载器：[plugin_loader.py](../backend/app/engines/plugin_loader.py)，启动时 `init_plugins()` 自动扫描 `~/.limo/plugins/`。
> 参考模板：[backend/plugins/examples/](../backend/plugins/examples/README.md)（照抄即用；含 pymupdf4llm PDF / faster-whisper 本地语音转写 / tesseract 本地 OCR / local_embedding 本地向量化 / rerank 本地重排 / trusted 进程内 六种形态）。
>
> **三类基类**：`EnginePlugin`（doc / ocr / asr，「一个文件 → 一段文本」）、
> `EmbeddingPlugin`（embedding，「一组文本 → 一组向量」）与
> `RerankPlugin`（rerank，「一个查询 + 一组候选 → 一组 `[下标, 分数]`」）。
> 同一条扫描 / 门控 / 启用 / 卸载链路，契约不同 —— 后两者见
> 「[向量化插件](#向量化插件embeddingplugin-v1)」与「[重排插件](#重排插件rerankplugin-v1)」。

## 设计初衷

**核心动机：依赖重但效果好的东西，做进核心系统维护难，不如做成插件。**

- **依赖重**：whisper 的 CUDA 运行时、tesseract 便携版、模型文件——打进安装包体积爆炸，还要跟着上游版本迭代维护
- **协议问题**：AGPL 等协议的工具不能内置，内置就污染核心（license 仅展示，主程序不判断、不管理、不提示）
- **维护难**：平台差异、用户硬件差异（CPU/GPU）、版本升级——单人维护背不起，插件由用户按需自装，零维护
- **法律风险**：爬虫/网页抓取类能力（如视频平台数据获取）存在平台 ToS、数据合规等法律风险——**核心系统不做过分的事**（不内置过度抓取能力），用户需要可自行通过插件实现，责任自担

本项目由单人维护，无组织/官方概念。插件系统 = **口子**：核心系统保持轻、稳、可控，留一个接口给有想法、有技术的贡献者——好东西通过插件口子进来。

## 定位

**插件系统 = 口子**：主程序只维护「契约 + 加载机制 + 文档」。插件不内置、不建市场、不做热重载/签名/失败恢复。

- 插件一律由**三方开源发布**（GitHub 等），用户 clone/拷贝到 `~/.limo/plugins/` 自装
- **主程序不判断、不管理、不提示任何插件协议**（license 字段仅前端展示，无警告语义）
- 删除目录即完全卸载

## 已知三方插件

> 以下插件由**三方身份**发布（即使作者同为项目维护者，发布即脱离官方维护边界），**非 LiMo 官方维护**。主程序零维护，质量/合规由插件作者负责。记录仅为便于发现，用户按需自装。

| 插件 | 仓库 | 能力 | 形态 |
|------|------|------|------|
| pymupdf4llm | [lazybone404/limo-pymupdf4llm-plugin](https://github.com/lazybone404/limo-pymupdf4llm-plugin) | PDF 文字+表格+嵌入图+矢量图完整解析（最强 PDF 转录） | 进程外（AGPL 依赖隔离） |

## 核心边界：默认进程外 + trusted 例外

| 模式 | 条件 | 含义 |
|------|------|------|
| **进程外**（默认主体） | `command` 有值 | subprocess 调用独立 venv — 依赖隔离、崩溃隔离、长任务 |
| **进程内**（显式例外） | `trusted=true` | 允许进程内 import — **纯技术声明**（可进程内 import，崩溃/依赖风险插件作者自担），**不涉及任何协议判断** |
| **拒绝** | 无 `command` 且未 `trusted` | 加载时 WARNING 日志，不注册 |

门控全类别统一（doc/ocr/asr/embedding 引擎不特殊）。进程内 import = 等同主程序权限，`trusted` 是唯一防线。

⚠️ **防线不止 `trusted` 一层**：`/api/plugins/*` 的**六个写端点**（install / enable / disable /
delete / install-deps / config）都过 `ensure_trusted_request` 来源守卫（2026-10-27 补，此前一个都没过）。
不加的话，任意恶意页面用**跨站简单请求**（无 body 的 POST、`multipart/form-data` 上传 —— 都不触发
preflight）就能推着客户端「装插件 → 重启后任意代码执行」，而 CORS 只拦读响应、拦不住请求发出。
`GET /plugins` 只读，刻意不加。

**进程内的两条硬边界**（2026-10-27 明确）：

- **依赖必须在主环境**：进程内插件没有独立 venv，`requirements.txt` 不参与「一键安装」——
  `install-deps` 对进程内插件直接返回 `status="inproc"`，**一个目录都不建**（否则会造出一个
  插件根本不会加载的 `.venv`：界面显示已装、插件仍然 import 失败、两边都不报错）。
  源码运行时用后端自身的 Python 装；打包版主环境是 exe，装不了 → **自带依赖的插件请用进程外**。
- **执行异常由加载器兜住**：`process()` 抛出的任何异常都被包成
  `FatalError("插件 <name> 执行失败：<类型>: <原因>")`，原始 traceback 进日志；
  返回值不是 `str` / `dict` 时同样是显式错误（旧行为会把它包成 `{"text": None}`，
  上层读起来像「这份文件没内容」而不是「插件坏了」）。

## 安装（用户）

```bash
# 1. 从插件发布仓库 clone，或拷贝到 ~/.limo/plugins/（顶层 *.py + 一层子目录都扫描）
cd ~/.limo/plugins
git clone https://github.com/<user>/<repo>.git          # 或直接拷贝

# 2. 按插件 README 建独立 venv + 装依赖（进程外插件）
cd <repo> && python -m venv .venv
.venv/Scripts/pip install -r requirements.txt    # Windows
# .venv/bin/pip install -r requirements.txt      # Linux/macOS

# 3. 重启后端（插件只在启动时扫描）
```

**验证**：启动日志出现 `Plugin loaded: doc/<name> (external)`；或 `curl http://127.0.0.1:7413/api/plugins`。

**zip 安装**（推荐）：插件页点「安装插件」→ 选插件 zip 包（声明 `.py` + 子目录，或 GitHub Release 下载的 source zip）→ 自动解压到 `~/.limo/plugins/` → 提示重启生效。**zip slip 防护**：非法路径条目（`../`/绝对路径）拒绝解压。

**一键安装依赖**：子目录含 `requirements.txt` 的**进程外**插件，前端插件页显示「一键安装」——自动建 venv + `pip install`（国内镜像源），失败显式反馈 + 手动命令兜底。桌面打包版无系统 Python 时降级引导（先装 Python 或手动执行）。

⚠️ **进程内（`trusted=true`）插件不适用**：它不从独立 venv 加载，依赖必须装在后端自身环境。接口对它会返回 `status="inproc"` 并说明原因，**一个目录都不建**（见上文「进程内的两条硬边界」）。

**卸载**：删除 `~/.limo/plugins/` 下对应文件/目录 + 重启；或前端插件页「卸载」。

## 开发标准（EnginePlugin v1）

继承 `app.engines.plugin_loader.EnginePlugin`（声明文件 import 主环境的 app 包——声明层运行在主 venv，执行层在子 venv）：

```python
from app.engines.plugin_loader import EnginePlugin

class MyEngine(EnginePlugin):
    # ── 运行模式（二选一）──
    command = ["my_engine/.venv/Scripts/python.exe", "my_engine/engine.py"]  # 进程外（推荐）
    # Linux/macOS: ["my_engine/.venv/bin/python3", "my_engine/engine.py"]
    # trusted = True   # 进程内例外（轻量无重依赖时）；二者皆无 → 拒绝加载

    # ── 元数据 ──
    name = "my-engine"              # 注册 key（唯一，覆盖同名内置）
    category = "doc"                # 开放字符串（v1 生效：doc/ocr/asr）
    license = "MIT"                 # SPDX（仅展示）
    display_name = "我的引擎"        # 用户向展示名，缺省回退 name
    user_desc = "一句话能力描述"     # 前端卡片展示
    description = ""
    file_types = [".pdf"]           # 支持扩展名 → capabilities fileTypeMap 自动归入

    # ── 可选 ──
    config_schema = [               # 配置声明 → 前端设置页渲染表单 → stdin JSON config 传给引擎
        {"name": "dpi", "type": "int", "label": "图片分辨率", "default": 150, "hint": "DPI"},
        {"name": "write_images", "type": "bool", "label": "嵌入图片", "default": True},
        {"name": "format", "type": "select", "label": "格式", "default": "png", "options": ["png", "jpeg"]},
    ]
    timeout = 600                   # 子进程超时（秒，默认 300）

    # ── 就绪自检（可选，强烈建议）──
    def precheck(self):
        """返回**给用户看的**不可用原因；`None` = 可以跑。

        装得上 ≠ 用得了：缺外部二进制（tesseract / ffmpeg）、缺模型文件、
        缺 API Key 都是这一档。不写它，用户要「启用 → 选它 → 点生成」走完三步
        才发现跑不了，代价是一次失败任务。

        ⚠️ 必须**报告**而不是「第一次执行时抛异常」—— 自检发生在设置页，
        用户还没付出任何东西。

        ⚠️ 会被 `GET /api/plugins` 调用，必须轻：不要在这里加载模型 / 起子进程。
        依赖探测用 `app.core.deps` 里现成的两个函数，它们返回的形状与本方法一致
        （`None` = 就绪）—— **按运行模式二选一，选错会把装好的插件报成不可用**：

          - **进程外（command 有值）** → `probe_venv(venv_dir, modules=("import名",))`
            依赖装在**子 venv** 里，主进程 `sys.path` 没有它们。用 `probe` 会**永远**
            报「无法导入」—— 用户照着提示反复重装，而东西一直是好的。
          - **进程内（trusted=true）** → `probe(module, attrs=("真正会调用的属性",))`
            依赖就在主进程里；`attrs` 别省，只问「模块在不在」会被 namespace
            package 骗过（安装版 `_internals/x/` 里有 `.pyd` 没 `__init__.py`）。
        """
        # 进程外（最常见）：
        return probe_venv(
            Path(__file__).resolve().parent / "my_engine" / ".venv",
            modules=("pymupdf4llm",),
            hint="在 设置 → 插件 里点「一键安装依赖」",
        )
```

`precheck` 的结果经 `GET /api/plugins` 的 `ready`（`true`/`false`/`null`）+
`precheckReason` 下发到设置页卡片。⚠️ `null` 是「**无法判断**」（插件被禁用、代码没加载，
自检无从调用），与 `false`（自检说不可用）刻意分开 —— 前端只渲染 `false`。

⚠️ **`probe_venv` 只做文件系统检查**（不起子进程 —— 那会让设置页等一次 Python 冷启动）。
它是**近似**：目录在 ≠ 一定能 import（DLL 缺失时目录也在）。真正的失败仍由首次执行
显式报错兜底，区别只是把「没建 venv / 没装依赖 / base python 已失效」这几种最常见的
缺失从「点了生成才知道」提前到设置页。

### 进程外 engine.py 约定

```python
"""引擎本体 — 在独立 venv 中执行。"""
import json, os, sys

def main():
    # 必须：Windows 默认 stdout 是 GBK，强制 UTF-8
    sys.stdout.reconfigure(encoding="utf-8"); sys.stderr.reconfigure(encoding="utf-8")

    config = json.load(sys.stdin)
    file_path = config.get("file") or sys.argv[sys.argv.index("--input") + 1]

    # 取消检查（可选）：cancel_file 出现 → 退出；主进程宽限 3s 后强杀
    if config.get("cancel_file") and os.path.exists(config["cancel_file"]):
        sys.exit(0)

    cfg = config.get("config", {}) or {}     # 插件自身配置（config_schema 表单保存值）
    md = your_lib.convert(file_path, dpi=int(cfg.get("dpi", 150)),
                          image_path=config.get("media_dir") or None)

    # 返回 JSON（doc/ocr）；asr 用 {"segments": [{"start","end","text"}]}
    print(json.dumps({"text": md, "pages": 10, "is_scanned": False}))

if __name__ == "__main__":
    main()
```

### 与主进程的协议（进程外）

| 通道 | 方向 | 内容 |
|------|------|------|
| 命令行 | → 插件 | `--input <file_path>`（v0 兼容） |
| stdin | → 插件 | JSON：`{"file", "cancel_file", "config", "media_dir", ...}`（`media_dir` 等消费方参数） |
| stdout | ← 插件 | JSON 结果（doc/ocr→`text/pages/is_scanned`、asr→`segments`）；**非 JSON 按 v0 纯文本兼容** |
| stderr | ← 插件 | 错误信息（returncode ≠ 0 时返回主进程） |

**取消语义**：进程外子进程轮询 `cancel_file` 标记文件，出现即退出；主进程监视线程在 cancel_event 置位后写标记 → 宽限 3s → `proc.kill()`。对单次阻塞调用（如 `to_markdown`）只能间隙检查，`timeout` 永远兜底。

**v0 兼容**：旧插件（纯文本 stdout、仅读 `--input`）零改动可用；升级到 JSON 契约可获得 `pages`/`is_scanned`/`segments` 元数据。

## 向量化插件（`EmbeddingPlugin` v1）

**同样是 `EnginePlugin` 的子类**（扫描 / 门控 / 启用 / 卸载 / 一键装依赖全部复用），
只是入口从 `process(file_path)` 换成 `embed(texts) -> list[list[float]]`：
「一个文件 → 一段文本」装不下「一组文本 → 一组向量」。参考实现
[local_embedding_plugin.py](../backend/plugins/examples/local_embedding_plugin.py)。

**注册在 `embedding` 类别**，出现在 设置 → 通用 → 检索索引 → 向量引擎 的下拉里
（`routers/search.py::_embedding_models` 只读 `display_name` / `model_id` / `dimension` /
`is_local` / `precheck()`，**前端零改动**）。

```python
from app.engines.plugin_loader import EmbeddingPlugin

class MyEmbedding(EmbeddingPlugin):
    name = "my-embedding"
    command = ["my_embedding/.venv/Scripts/python.exe", "-u", "my_embedding/server.py"]
    license = "MIT"
    display_name = "本地模型（…）"

    # ── 向量契约：四元组身份的两项 ──
    model_id = "my-model-int8"    # 换权重必须改它，否则新旧向量混表（见下）
    dimension = 512               # 不声明 → 自检直接拦下
    is_local = True               # 前端据此显示「免费 / 零外发」

    # ── 自检材料（依赖由 adapter 用 probe_venv 查，不必自己写）──
    requires_modules = ("onnxruntime", "tokenizers")
    requires_files = ()           # 固定路径的必需文件；用户可配的路径写进 precheck()

    config_schema = [
        {"name": "model_dir", "type": "str", "label": "模型目录", "default": "~/…"},
    ]

    def precheck(self):
        cfg = self.plugin_config or {}         # 已回填用户保存的配置
        ...                                     # 只查文件在不在，不加载模型
        return None
```

### 常驻子进程协议（进程外模式）

**进程外 embedding 是「常驻」而不是「一次调用一个进程」**：模型加载 3–5 秒，而查询侧
`VectorSearcher` 的 `QUERY_TIMEOUT = 3.0` —— 每次搜索都现起进程等于每次搜索都超时。
适配器 `_PluginEmbeddingAdapter` 维护一个长驻子进程，用 NDJSON over stdio 一问一答：

| 时机 | 方向 | 一行 JSON |
|------|------|-----------|
| 启动 | ← 插件 | `{"ready": true, "model_id": "...", "dimension": 512}` |
| 启动失败 | ← 插件 | `{"ready": false, "error": "..."}`，然后 exit 1 |
| 请求 | → 插件 | `{"id": 1, "texts": ["...", "..."]}` |
| 响应 | ← 插件 | `{"id": 1, "vectors": [[...], ...]}` |
| 单次失败 | ← 插件 | `{"id": 1, "error": "..."}`（**进程保留**，下一批继续用） |

四条硬要求。前三条缺一条是「主进程永远等不到响应」，第四条缺了则**什么都不报**：

1. **`command` 必须带 `-u`**（Python 无缓冲）**且**每行 `flush()` —— 两层保险
   （`-u` 关了缓冲，`flush` 保证协议行立刻出去）
2. **stdout 只写协议行**：日志一律 stderr（适配器有后台 drain 线程，Windows 管道写满
   约 64KB 不会阻塞）；混进非 JSON 行 = 适配器判定协议损坏、丢弃进程
3. **`id` 必须原样回传**：对不上的响应对应「管道错位」，适配器杀掉进程重来
   （留着只会让每一轮都读到上一条的结果）
4. **三个流都 `reconfigure(encoding="utf-8")`**（含 **stdin**）：
   ```python
   sys.stdin.reconfigure(encoding="utf-8")
   sys.stdout.reconfigure(encoding="utf-8")
   sys.stderr.reconfigure(encoding="utf-8")
   ```
   Windows 上 Python 未开 UTF-8 模式时 stdio 是 **GBK**。只配 stdout/stderr 是
   最常见也最坏的写法：**进来的** UTF-8 字节被 GBK 解码、`errors='surrogateescape'`
   把无法解码的字节留成代理字符 —— `json.loads` 照样成功，于是「编码出来的是乱码
   文本的向量」：检索质量下降、日志一片绿、一个异常都没有。
   主进程已设 `PYTHONUTF8=1`，这一条是纵深防御（插件不该依赖启动方式）。
   **文件引擎同样适用**（`json.load(sys.stdin)` 那一侧）。

**失败语义**（与文件引擎「一个进程一次调用」不同，这里要区分「进程坏了」与「这批文本编码失败」）：

| 情形 | 处置 |
|------|------|
| 响应 `error` 字段 | 抛错，**进程保留** |
| 条数 / 维度不符 | 抛错，进程保留（不补、不猜、不截断） |
| 非 JSON 行 / `id` 错位 / 超时 | 抛错，**杀掉进程**（下次调用自动重起） |
| 子进程自己退出（OOM / 被外部杀掉） | 下次调用时检测到并自动重起 |
| 声明的 `dimension` / `model_id` 与 ready 报告不符 | **启动即失败**（不静默接受） |

维度与模型必须对得上，因为四元组 `(model_id, dimension, chunk_size, schema_version)` 是向量库的
**整体身份** —— 口径不符写进库之后是**静默的**（表现为检索质量下降，而不是报错）。

### 插件配置怎么进子进程

`config_schema` 的保存值经**环境变量 `LIMO_PLUGIN_CONFIG`**（JSON）传给子进程，在**启动时读一次**
—— 改了配置要等下次起进程（用户重启后端，或等模型闲置 `release` 后重起）才生效。

⚠️ **`config_schema` 里不要放密钥**。插件配置**明文**存在 `~/.limo/config.yaml` 的
`plugin_configs` 段（主程序的 LLM / DashScope Key 走 DPAPI 密钥库，插件配置不在那条
链上 —— 它是任意形状的嵌套 dict，进不了那套按扁平键名组织的成对表）。
需要凭据的插件请让用户通过**环境变量**或系统凭据管理器提供，配置项只放路径、模型名、
行为开关这类非敏感值。

声明层的 `precheck()` 能直接读 `self.plugin_config`：适配器在设置配置时会把它同步给插件实例
（`_PluginEngineAdapter.plugin_config` 是 property + setter）。两个理由：自检发生在设置页，
那时配置已经回填过；而 adapter 手上的值与插件手上的值若各是各的，`precheck` 判的就是另一份配置。

### 生命周期钩子（不用实现）

`warmup()` / `release(after_idle)` 由适配器提供：`warmup` 提前起子进程（后台 worker 有空活时调用），
`release` 按闲置时长杀进程释放内存（`vector_release = immediate` 时每轮结束后调一次）。
**进程外插件什么都不用写** —— 它们不在 `EmbeddingEngine` Protocol 里，消费方用 `getattr` 取，
插件没有就是「视为无需预热 / 无需释放」。

**进程内（`trusted`）插件**如果要自己管理权重驻留，可以实现同名同签名的两个钩子：

```python
def warmup(self): ...                    # 提前加载，避免第一次 embed 白等
def release(self, *, after_idle: float = 600.0) -> bool:
    """释放模型；返回「这次真的释放了吗」。"""
```

适配器对进程内与进程外**转发方式一致**（`warmup` / `release` 都 `getattr` 后调用），
只是进程外的默认实现是「起 / 杀常驻子进程」，进程内的默认是「没有这个钩子」。
⚠️ 两处**必须成对**：只实现 `warmup` 而不实现 `release`，选了「常驻内存」的用户就永远
等不到释放 —— 而外部模式那份工作正常，插件作者换个形态发布才发现。

⚠️ **模型放哪、叫什么由插件自己定**（`precheck()` 里判文件），**不要**把路径写死在适配器里：
内置引擎与插件版默认指向同一个目录（`~/.limo/models/bge-small-zh-v1.5-onnx/`），
用户此前用过内置版的话装插件不必重新下载 22.8MB。

### 与内置引擎同名

模板里的 `name = "local-bge-small"` **刻意与内置引擎同名**，按「用户优先」规则覆盖内置。
于是打包版（内置那份缺 `tokenizers` 而不可用）装上插件后**真能离线跑**；
源码版不装插件则继续用内置那份，两者指向同一个 `model_id` / 维度，向量库不必重建。

⚠️ **换权重必须改 `model_id`**（例如 int8 → fp32 写成 `…-fp32`），否则 `ensure_identity`
判「口径没变」，新老向量混在同一张表里 —— 检索质量下降、零报错。换完在设置页点
「重建向量索引」（重建是计费/耗时动作，**不会自动发生**）。

## 重排插件（`RerankPlugin` v1）

**第三类基类**，同样是 `EnginePlugin` 的子类（扫描 / 门控 / 启用 / 卸载 / 一键装依赖
全部复用），入口从 `process(file_path)` 换成
`rerank(query, documents) -> list[tuple[int, float]]`。参考实现
[rerank_plugin.py](../backend/plugins/examples/rerank_plugin.py) +
[rerank/](../backend/plugins/examples/rerank/)（含 `server.py` / `requirements.txt` / README）。

```python
from app.engines.plugin_loader import RerankPlugin

class MyReranker(RerankPlugin):
    name = "my-reranker"
    command = ["rerank/.venv/Scripts/python.exe", "-u", "rerank/server.py"]
    model_id = "bge-reranker-v2-m3"    # 归因用
    max_documents = 100                 # 能力声明，不是调优旋钮
    is_local = True
    requires_modules = ("onnxruntime", "tokenizers")
    requires_files = ()
    config_schema = [...]               # 同 EmbeddingPlugin
    timeout = 300

    def rerank(self, query, documents):
        raise NotImplementedError       # 走 command（进程外）
```

**注册在 `rerank` 类别**，出现在 设置 → 通用 → 检索索引 → **重排引擎** 的下拉里
（`routers/search.py::_rerank_models` 只读 `display_name` / `model_id` / `is_local` /
`precheck()` ⇒ **前端零改动**）。

### 协议（常驻子进程，NDJSON over stdio）

与向量化那一族同一套骨架，**只有载荷不同**：

| 时机 | 方向 | 一行 JSON |
|------|------|-----------|
| 启动 | ← 插件 | `{"ready": true, "model_id": "bge-reranker-v2-m3"}` |
| 启动失败 | ← 插件 | `{"ready": false, "error": "..."}`，然后 exit 1 |
| 请求 | → 插件 | `{"id": 1, "query": "...", "documents": ["...", "..."], "top_n": 20}` |
| 响应 | ← 插件 | `{"id": 1, "results": [[0, 0.83], [3, 0.71]]}` |
| 单次失败 | ← 插件 | `{"id": 1, "error": "..."}`（**进程保留**） |

**为什么必须常驻**：bge-reranker 量级 500MB+，冷读几十秒，而重排发生在**一次在线
检索里**（`RERANK_TIMEOUT = 5.0` 秒）。每轮现起进程 = 每次搜索都超时，而提示会写成
「重排超时」，用户去查网络 —— 插件根本不联网。

三条与向量化完全相同的纪律（`-u` + 每行 flush / stdout 只写协议行 / `id` 原样回传）
之外，**这一族独有两条**：

- ⚠️ **`results` 的第一个数是「入参下标」，不是结果里的新位置**。搞反的结果是整张
  排名表错位，而每一行看起来都合法（分数是真的、下标是合法整数）。适配器对**越界
  与重复下标直接抛错**（`_PluginRerankAdapter._check_results`）—— 抛出去之后整次
  重排降级为「沿用融合顺序」，那是可接受的；不抛则是一次看不出来的错序。
- **允许只返回前 `top_n` 条**（契约 3）：调用方把缺失下标按原序补到尾部。
  **不要补假分数凑满** —— 那会让尾部候选莫名排到前面。

重排**不落盘任何东西**，所以没有向量那种「身份四元组」需要重建；`model_id` 的
唯一用途是**归因**（分数漂移只有它能回答「这轮换的是哪一份权重」）。

⚠️ `onnxruntime` / `tokenizers` **不进 `pyproject.toml`**（插件自带 venv）。
官方只交付模板与契约测试，真实权重（约 544MB）由用户按需下载 —— 见模板 README。

## 管理 API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/plugins` | GET | 插件列表（含禁用，按 category 分组） |
| `/api/plugins/install` | POST | 上传 zip 安装（解压到 `~/.limo/plugins/`，zip slip 防护） |
| `/api/plugins/{name}/install-deps` | POST | 一键安装依赖（**仅进程外**：子目录 `requirements.txt` → 建 venv + pip install 镜像源；进程内返回 `status="inproc"` 且零写入） |
| `/api/plugins/{name}/enable` / `disable` | POST | 写 `plugin_states`，**重启后生效** |
| `/api/plugins/{name}` | DELETE | 卸载（删 `~/.limo/plugins/` 下声明文件 + 同名目录含 venv/模型） |
| `/api/plugins/{name}/config` | PUT | 保存插件配置（`plugin_configs`，落盘 + 内存即时生效，下次执行 stdin 即含新 config；**embedding 插件走环境变量 `LIMO_PLUGIN_CONFIG`**，在子进程启动时读取） |

前端：设置页「插件」tab —— 按 category 分组卡片、启用开关、卸载二次确认、config_schema 表单。

## 调试

```bash
# 加载失败 → 后端日志 WARNING："Plugin xxx failed to load: <原因>"
# 门控拒绝 → "Plugin xxx 无 command 且未声明 trusted=true，拒绝加载"

# 手动测试 engine.py（绕过主进程）
cd ~/.limo/plugins/<repo>
.venv/Scripts/python.exe engine.py --input /path/to/test.pdf <<< '{"media_dir": ""}'
```

常见问题：
- **加载不到**：声明文件不在 `~/.limo/plugins/` 顶层或一层子目录；`EnginePlugin` 子类未正确继承
- **中文乱码**：engine.py 忘了 `sys.stdout.reconfigure(encoding="utf-8")`
- **Command not found**：venv 路径与 `command` 声明不一致（Windows `Scripts/python.exe`，Linux `bin/python3`）
- **不显示在 UI**：`file_types` 未设置或未匹配该文件类型；重启后端
- **被拒绝加载**：无 `command` 且未声明 `trusted=true`——进程外配置 command，或轻量插件声明 trusted
- **向量插件卡在启动 / 每次搜索都超时**：`command` 里的 `-u` 漏了，或 server 忘了 `flush()`
  （主进程按行阻塞等待，留在缓冲区里的一行等于没发）；现象是「启动超时」，真因在那条没人收到的行里
- **向量插件每轮都重起进程**：stdout 混进了非协议内容（日志必须写 stderr）；或响应 `id` 没原样回传

## 发布指南（插件作者）

1. **写插件**：声明文件 + 引擎本体（参考 [examples](../backend/plugins/examples/README.md)）
2. **开源发布**：推到 GitHub，README 写清楚安装命令（venv 步骤）+ 配置说明 + 契约版本（`api_version`）
3. **用户自装**：clone/拷贝到 `~/.limo/plugins/` + 建 venv + 重启
4. **契约版本**：`PLUGIN_API_VERSION`（当前 `1`）。主程序升级改契约时同步递增，插件声明 `api_version` 对齐

## 与内置引擎的交互规则

- **覆盖**：插件与内置引擎同名（category + name）时，插件覆盖内置（用户优先）
- **绑定**：`engine_bindings` 三种格式都支持插件——字符串（插件名直接查注册表）/ 整数（engine_models id）/ 列表（旧格式）
- **失败语义**：绑定只取第一个可用引擎，插件失败直接报错（引擎自动降级已移除）
- **fileTypeMap**：插件按 `file_types` 扩展名自动归入对应文件类型下拉，无需改 capabilities 代码（`embedding` 类别不吃文件类型，走设置页的向量引擎下拉）
