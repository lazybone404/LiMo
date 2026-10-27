# LiMo 插件参考模板

> 定位：**参考实现**，照抄即用。主程序只维护「契约 + 加载机制 + 文档」。
> 插件由三方开源发布（GitHub 等），用户拷贝到 `~/.limo/plugins/` 自装。
> **主程序不做任何插件协议判断**（license 仅前端展示）。

## 五个模板

| 模板 | 形态 | 适用 |
|------|------|------|
| `pymupdf4llm_plugin.py` + `pymupdf4llm/` | 进程外（`command` + 独立 venv） | 重依赖、长任务（默认主体） |
| `faster_whisper_plugin.py` + `faster_whisper/` | 进程外（`command` + 独立 venv） | **本地语音转写**（核心只留云端 ASR） |
| `tesseract_plugin.py` + `tesseract/` | 进程外（subprocess 调自带 tesseract.exe） | 本地 OCR（需自备便携版） |
| `local_embedding_plugin.py` + `local_embedding/` | **进程外 + 常驻子进程**（`EmbeddingPlugin`） | **本地向量化**（语义检索，免 API、零外发） |
| `simple_trusted_plugin.py` | 进程内（`trusted=true`） | 轻量、无重依赖自研代码 |

⚠️ **进程内模板没有「一键安装依赖」**：进程内插件不从独立 venv 加载，`requirements.txt`
不参与安装（接口返回 `status="inproc"`，零写入）。它的依赖必须装进后端自身环境 ——
源码运行时可以，**打包版装不了**（主环境是 exe）⇒ 自带依赖请用进程外模板。

> `category` 决定结果分派：`doc` / `ocr` → `{"text", ...}`；`asr` → `{"segments": [...]}`。
> 走 `asr` 的插件在 设置 → 引擎绑定 → 语音转写 里被选中。
>
> ⚠️ **`embedding` 是另一族**：它的基类是 `EmbeddingPlugin`（`EnginePlugin` 的子类），
> 入口是 `embed(texts) -> list[list[float]]` 而不是 `process(file_path)` ——
> 「一个文件 → 一段文本」装不下「一组文本 → 一组向量」。它出现在
> 设置 → 通用 → 检索索引 → 向量引擎 的下拉里。

## 安装（进程外模板为例）

```bash
# 1. 拷贝声明文件 + 子目录到 ~/.limo/plugins/
cp -r pymupdf4llm_plugin.py pymupdf4llm ~/.limo/plugins/

# 2. 装依赖（二选一）
#    a) 设置 → 插件 → 「一键安装依赖」（自动建 venv，走清华镜像）
#    b) 手动：
cd ~/.limo/plugins/pymupdf4llm
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 3. 重启后端（插件只在启动时扫描）
```

⚠️ **子目录名以 `command` 里的相对路径为准**（`"pymupdf4llm/.venv/..."` → 找
`pymupdf4llm/requirements.txt`）。让子目录名与声明文件 stem 保持一致只是习惯，
不要依赖它 —— 「一键安装依赖」按 `command` 解析（与真正执行时同源）。

**验证**：启动日志出现 `Plugin loaded: doc/pymupdf4llm (external)`。

## 进程外契约速查

- **stdin JSON**：`{"file": <路径>, "cancel_file": <取消标记绝对路径>, "config": <插件自身配置>, ...}`（`media_dir` 等消费方参数也会传入）
- **stdout JSON**：doc/ocr → `{"text", "pages", "is_scanned", ...}`；asr → `{"segments": [{"start","end","text"}]}`
- **v0 兼容**：argv 仍带 `--input <path>`；stdout 非 JSON 按纯文本处理
- **取消**：子进程轮询 `cancel_file` 是否出现，出现即退出；主进程宽限 3s 后强杀，`timeout` 兜底
- **就绪自检**：重写 `precheck()` 返回 `None`（就绪）或一句给用户看的原因。
  ⚠️ **进程外插件用 `core.deps.probe_venv`，进程内（trusted）插件用 `core.deps.probe`**
  —— 前者的依赖在子 venv 里，主进程 `sys.path` 没有它们，用 `probe` 会让**装好的**插件
  永远显示不可用。两个函数的文档里写了完整理由。

## 向量化插件契约速查（`EmbeddingPlugin`）

**进程外模式是常驻子进程**，不是「一次调用一个进程」：模型加载 3–5 秒，而查询侧
`QUERY_TIMEOUT = 3.0` —— 每次搜索都现起一个进程，等于每次搜索都超时。

| 时机 | 方向 | 一行 JSON |
|------|------|-----------|
| 启动 | ← 插件 | `{"ready": true, "model_id": "...", "dimension": 512}` |
| 启动失败 | ← 插件 | `{"ready": false, "error": "..."}`，然后 exit 1 |
| 请求 | → 插件 | `{"id": 1, "texts": ["...", "..."]}` |
| 响应 | ← 插件 | `{"id": 1, "vectors": [[...], ...]}` |
| 单次失败 | ← 插件 | `{"id": 1, "error": "..."}`（**进程保留**，下一批继续用） |

- **`command` 必须带 `-u`**（Python 无缓冲）+ 每行 `flush()` —— 两层保险，缺了就是
  「主进程永远等不到响应」
- **stdout 只写协议行**，日志一律 stderr（混进非 JSON 行 = 适配器丢弃进程）
- **`id` 必须原样回传**：对不上的响应对应「管道错位」，适配器杀掉进程重来
- **声明 `dimension` / `model_id`**：与子进程报告的不符时**启动即失败**（写进库之后
  口径不符是静默的，表现为检索质量下降而不是报错）
- **契约三条与云端引擎相同**：顺序一一对应 / 已 L2 归一化 / 空输入返回空
- 插件配置（`config_schema` 表单值）经环境变量 **`LIMO_PLUGIN_CONFIG`**（JSON）传入，
  在**启动时**读一次 —— 改了配置需要重启后端（或等 `release` 后重起子进程）才生效
- `precheck()` 只需查**插件私有的**外部条件（模型文件、外部二进制）；
  子 venv 的依赖由声明里的 `requires_modules` 交给适配器检查（用 `probe_venv`，
  **不要**在插件里再写一遍 `probe`）

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
