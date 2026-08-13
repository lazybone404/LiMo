# LiMo 插件参考模板

> 定位：**参考实现**，照抄即用。主程序只维护「契约 + 加载机制 + 文档」。
> 插件由三方开源发布（GitHub 等），用户拷贝到 `~/.limo/plugins/` 自装。
> **主程序不做任何插件协议判断**（license 仅前端展示）。

## 三个模板

| 模板 | 形态 | 适用 |
|------|------|------|
| `pymupdf4llm_plugin.py` + `pymupdf4llm/` | 进程外（`command` + 独立 venv） | 重依赖、长任务（默认主体） |
| `tesseract_plugin.py` + `tesseract/` | 进程外（subprocess 调自带 tesseract.exe） | 本地 OCR（需自备便携版） |
| `simple_trusted_plugin.py` | 进程内（`trusted=true`） | 轻量、无重依赖自研代码 |

## 安装（进程外模板为例）

```bash
# 1. 拷贝声明文件 + 子目录到 ~/.limo/plugins/
cp -r pymupdf4llm_plugin.py pymupdf4llm ~/.limo/plugins/

# 2. 进入子目录建独立 venv + 装依赖
cd ~/.limo/plugins/pymupdf4llm
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt    # Windows
# .venv/bin/pip install -r requirements.txt      # Linux/macOS

# 3. 重启后端（插件只在启动时扫描）
```

**验证**：启动日志出现 `Plugin loaded: doc/pymupdf4llm (external)`。

## 进程外契约速查

- **stdin JSON**：`{"file": <路径>, "cancel_file": <取消标记绝对路径>, "config": <插件自身配置>, ...}`（`media_dir` 等消费方参数也会传入）
- **stdout JSON**：doc/ocr → `{"text", "pages", "is_scanned", ...}`；asr → `{"segments": [{"start","end","text"}]}`
- **v0 兼容**：argv 仍带 `--input <path>`；stdout 非 JSON 按纯文本处理
- **取消**：子进程轮询 `cancel_file` 是否出现，出现即退出；主进程宽限 3s 后强杀，`timeout` 兜底

## 发布指南（插件作者）

1. **写插件**：声明文件（`EnginePlugin` 子类）+ 引擎本体（子目录 `engine.py` 或 `process()`），参考上面模板
2. **开源发布**：推到 GitHub（README 写：安装命令 + 配置说明 + venv 步骤 + 契约版本）
3. **用户自装**：clone/拷贝到 `~/.limo/plugins/` + 建 venv + 重启
4. **契约版本**：`PLUGIN_API_VERSION`（当前 `1`），插件声明 `api_version` 对齐

## 门控速查

```
command 有值          → 进程外（默认主体）
trusted=true          → 进程内（显式信任例外，风险自担）
无 command 且未 trusted → 拒绝加载（日志 WARNING）
```
