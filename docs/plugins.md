# 插件系统 — 契约 · 安装 · 发布（EnginePlugin v1）

> 加载器：[plugin_loader.py](../backend/app/engines/plugin_loader.py)，启动时 `init_plugins()` 自动扫描 `~/.limo/plugins/`。
> 参考模板：[backend/plugins/examples/](../backend/plugins/examples/README.md)（照抄即用；含 pymupdf4llm PDF / faster-whisper 本地语音转写 / tesseract 本地 OCR / trusted 进程内 四种形态）。

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

门控全类别统一（doc/ocr/asr 引擎不特殊）。进程内 import = 等同主程序权限，`trusted` 是唯一防线。

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

**一键安装依赖**：子目录含 `requirements.txt` 的进程外插件，前端插件页显示「一键安装」——自动建 venv + `pip install`（国内镜像源），失败显式反馈 + 手动命令兜底。桌面打包版无系统 Python 时降级引导（先装 Python 或手动执行）。

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

## 管理 API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/plugins` | GET | 插件列表（含禁用，按 category 分组） |
| `/api/plugins/install` | POST | 上传 zip 安装（解压到 `~/.limo/plugins/`，zip slip 防护） |
| `/api/plugins/{name}/install-deps` | POST | 一键安装依赖（子目录 `requirements.txt` → 建 venv + pip install 镜像源） |
| `/api/plugins/{name}/enable` / `disable` | POST | 写 `plugin_states`，**重启后生效** |
| `/api/plugins/{name}` | DELETE | 卸载（删 `~/.limo/plugins/` 下声明文件 + 同名目录含 venv/模型） |
| `/api/plugins/{name}/config` | PUT | 保存插件配置（`plugin_configs`，落盘 + 内存即时生效，下次执行 stdin 即含新 config） |

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

## 发布指南（插件作者）

1. **写插件**：声明文件 + 引擎本体（参考 [examples](../backend/plugins/examples/README.md)）
2. **开源发布**：推到 GitHub，README 写清楚安装命令（venv 步骤）+ 配置说明 + 契约版本（`api_version`）
3. **用户自装**：clone/拷贝到 `~/.limo/plugins/` + 建 venv + 重启
4. **契约版本**：`PLUGIN_API_VERSION`（当前 `1`）。主程序升级改契约时同步递增，插件声明 `api_version` 对齐

## 与内置引擎的交互规则

- **覆盖**：插件与内置引擎同名（category + name）时，插件覆盖内置（用户优先）
- **绑定**：`engine_bindings` 三种格式都支持插件——字符串（插件名直接查注册表）/ 整数（engine_models id）/ 列表（旧格式）
- **失败语义**：绑定只取第一个可用引擎，插件失败直接报错（引擎自动降级已移除）
- **fileTypeMap**：插件按 `file_types` 扩展名自动归入对应文件类型下拉，无需改 capabilities 代码
