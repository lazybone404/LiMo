# 引擎插件系统 — 开发与安装标准

> 插件 = 第三方引擎，以进程外（默认推荐）或进程内模式加载进 LiMo。
> 加载器：[plugin_loader.py](../backend/app/engines/plugin_loader.py)，启动时 `init_plugins()` 自动扫描。

---

## ⚠️ 安全声明（必须阅读）

**当前插件系统是 MVP，不防恶意代码**：

- 插件是**任意 Python 代码**，`init_plugins` 会 `import` 并执行
- 进程外模式隔离的是**依赖**（独立 venv），不是**权限**——子进程能访问你的文件系统
- 从 GitHub 拉取插件前**必须审查源码**（尤其是 `process()` / `engine.py` / `requirements.txt`）
- 只装信任来源的插件（个人维护 / 社区已审查）

---

## 1. 插件标准（目录布局）

加载器扫描 `~/.limo/plugins/` 下 **顶层 `*.py` + 一层子目录 `*.py`**（排除 `__pycache__` 等点开头目录）。

### 标准仓库布局（推荐，GitHub 直接 clone 可用）

```
your-plugin-repo/                  ← git clone 到 ~/.limo/plugins/your-plugin-repo
├── my_plugin.py                   ← 声明文件（EnginePlugin 子类）
├── my_plugin/                     ← 引擎本体（进程外 venv 隔离）
│   ├── engine.py                  ← 进程外入口（--input + stdin JSON + stdout）
│   ├── requirements.txt           ← 依赖清单
│   ├── .venv/                     ← 独立 venv（不提交到 git，README 写安装命令）
│   └── ...                        ← 引擎库代码
└── README.md                      ← 必写：安装命令 + 用法 + 许可证
```

### 平铺布局（简单插件，进程内模式）

```
~/.limo/plugins/
└── my_simple_ocr.py               ← 单文件声明 + 实现
```

---

## 2. 开发标准（EnginePlugin API）

继承 `app.engines.plugin_loader.EnginePlugin`（声明文件 import 主环境的 app 包——**声明层运行在主 venv**，执行层在子 venv）。

```python
from app.engines.plugin_loader import EnginePlugin


class MyEngine(EnginePlugin):
    # ── 必填元数据 ──
    name = "my-engine"             # 注册 key（唯一，覆盖内置引擎时与内置同名）
    category = "doc"               # "asr" | "ocr" | "doc"
    license = "MIT"                # SPDX：MIT / Apache-2.0 / AGPL-3.0 / GPL-3.0 / Proprietary
                                   #   AGPL/GPL → 前端黄色警示标签
    description = "一句话描述"      # 前端下拉展示
    file_types = [".pdf"]          # 支持扩展名 → capabilities fileTypeMap 自动归入

    # ── 进程外模式（推荐，重依赖/协议敏感引擎）──
    command = ["my_plugin/.venv/Scripts/python.exe",   # Windows
               "my_plugin/engine.py"]
    # Linux/macOS: ["my_plugin/.venv/bin/python3", "my_plugin/engine.py"]
    # 相对路径基于插件声明文件所在目录解析

    timeout = 300                  # 子进程超时（秒）

    # ── 进程内模式（轻量插件，直接实现 process）──
    # command = None
    # def process(self, file_path: str, config: dict) -> str:
    #     ...return markdown
```

### 进程外 engine.py 约定

```python
"""引擎本体 — 在独立 venv 中执行。"""
import sys, json

def main():
    # 必须：Windows 默认 stdout 是 GBK，强制 UTF-8（主进程按 UTF-8 读取）
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    # 输入文件：--input <path>
    file_path = sys.argv[sys.argv.index("--input") + 1]
    # 配置：stdin 读 JSON（media_dir 等，不可序列化值已被主进程过滤）
    config = json.load(sys.stdin)

    md = your_lib.convert(file_path, image_path=config.get("media_dir") or None)
    print(md)                      # stdout 输出 Markdown 即结果
    # stderr 输出在失败时返回给主进程；returncode != 0 → 视为失败

if __name__ == "__main__":
    main()
```

### 与主进程的协议（进程外）

| 通道 | 方向 | 内容 |
|------|------|------|
| 命令行 | → 插件 | `--input <file_path>` |
| stdin | → 插件 | JSON config（`media_dir`、`model_name` 等，仅可序列化字段） |
| stdout | ← 插件 | 转录结果 Markdown（UTF-8） |
| stderr | ← 插件 | 错误信息（returncode ≠ 0 时返回主进程） |

**已知限制**：取消信号（`cancel_event`）不传给进程外插件——subprocess 无法优雅中断，靠 `timeout` 兜底。

---

## 3. 从 GitHub 安装（标准流程）

```bash
# 1. clone 到 plugins/ 子目录（仓库根 = 标准布局）
cd ~/.limo/plugins
git clone https://github.com/<user>/<repo>.git

# 2. 按仓库 README 建独立 venv + 装依赖（声明文件在子目录时路径注意）
cd <repo> && python -m venv .venv
.venv/Scripts/pip install -r requirements.txt    # Windows
# .venv/bin/pip install -r requirements.txt      # Linux/macOS

# 3. 重启后端（plugin_loader 只在启动时扫描）
```

**验证安装**：

```bash
# 启动日志应出现：
#   Plugin loaded: doc/my-engine (MIT) [my_plugin.py]
# 或查看 capabilities：
curl http://127.0.0.1:7413/api/capabilities | jq '.engineOptions.doc[] | select(.plugin == true)'
```

前端自动出现：设置页引擎下拉（license 标签）+ 对应文件类型（fileTypeMap）。

**卸载**：删除 `plugins/<repo>` 目录 + 重启后端。

---

## 4. 许可证与信任

| license 值 | 前端标签 | 建议 |
|-----------|---------|------|
| MIT / Apache-2.0 / BSD | 灰色（安全） | 可放心安装 |
| AGPL-3.0 / GPL-3.0 | 黄色（警示） | 商用需注意传染性 |
| Proprietary / Unknown | 黄色（警示） | 谨慎，闭源无法审计 |

---

## 5. 调试

```bash
# 插件加载失败 → 后端日志（logging.WARNING）：
#   "Plugin my_plugin.py failed to load: <原因>"

# 进程外执行失败 → 错误含子进程 stderr 或 "Plugin exited with code N"

# 手动测试 engine.py（绕过主进程）：
cd ~/.limo/plugins/<repo>
.venv/Scripts/python.exe my_plugin/engine.py --input /path/to/test.pdf <<< '{"media_dir": ""}'
```

常见问题：
- **加载不到**：声明文件不在 plugins/ 顶层或一层子目录；`EnginePlugin` 子类未正确继承
- **中文乱码**：engine.py 忘了 `sys.stdout.reconfigure(encoding="utf-8")`
- **Command not found**：venv 路径与 `command` 声明不一致（Windows 用 `Scripts/python.exe`，Linux 用 `bin/python3`）
- **不显示在 UI**：`file_types` 未设置或未匹配该文件类型；重启后端

---

## 6. 与内置引擎的交互规则

- **覆盖**：插件与内置引擎同名（category + name）时，插件覆盖内置（用户优先）
- **绑定**：`engine_bindings` 三种格式都支持插件——字符串（插件名直接查注册表）/ 整数（engine_models id）/ 列表（旧格式）
- **降级**：`engine_auto_fallback` 同样作用于插件（失败 → 尝试下一个引擎）
- **fileTypeMap**：插件按 `file_types` 扩展名自动归入对应文件类型下拉，无需改 capabilities 代码

---

## 7. 示例仓库模板

最小可用的 GitHub 仓库骨架（复制即用）：

```
engine-demo-plugin/
├── demo_plugin.py          # EnginePlugin 声明（见 §2）
├── demo_plugin/
│   ├── engine.py           # 进程外入口（见 §2）
│   └── requirements.txt
├── README.md               # 安装命令（见 §3）+ 许可证
└── LICENSE
```
