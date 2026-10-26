# Faster-Whisper 本地语音转写插件（参考实现）

进程外插件：`command` 指向子目录独立 venv，`engine.py` 用 `faster-whisper`
（CTranslate2 推理）在本机把音频转成带时间轴的文字。

**这个插件的意义**：核心已移除本地转写（whisper 的 CUDA 依赖会打爆安装包），
内置两个 ASR 引擎都是云端的（bcut / dashscope）。装本插件后，视频/音频导入
可以在**完全离线、零费用、不外发**的前提下完成转写。

## 安装

```bash
# 1. 拷贝声明文件 + 子目录到 ~/.limo/plugins/
cp -r faster_whisper_plugin.py faster_whisper ~/.limo/plugins/

# 2. 装依赖（二选一）
#    a) 设置 → 插件 → 「一键安装依赖」（自动建 venv + 走清华镜像）
#    b) 手动：
cd ~/.limo/plugins/faster_whisper
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 3. 重启后端
```

装完在 **设置 → 插件** 里应看到「Faster-Whisper 本地转写」为**就绪**；
再到 **设置 → 引擎绑定 → 语音转写** 选它。

## 首次转写会下载模型

**这一步不在「一键安装依赖」里**，因为它跟着 `model` 配置项走、且缓存可以被
`download_root` 指到任何地方 —— 放在安装步骤里会让「安装成功但换模型又没下载」
显得莫名其妙。

首次用某个模型时会从 HuggingFace 拉取（默认镜像已配好）：

| 模型 | 下载体积（int8） | 适用 |
|------|------|------|
| tiny / base | ~40 / ~80 MB | 只求能听懂，中文效果一般 |
| **small（默认）** | ~250 MB | 中文日常内容，速度与效果平衡 |
| medium | ~750 MB | 有口音 / 专业术语，CPU 上明显变慢 |
| large-v3 | ~1.5 GB | 最好效果，建议配 GPU |

模型缓存在 `~/.cache/huggingface/hub/`（可用 `download_root` 改到别处，比如
放在机械盘上省 C 盘空间）。**下载只发生一次**，之后同一模型秒开。

下载失败的典型表现是 `加载模型失败 [small / cpu / int8]：...ConnectionError`：
检查网络，或把 `hf_endpoint` 换成能访问的镜像；企业代理环境请先确认
`HTTPS_PROXY` 是否已被主程序注入。

## 配置（config_schema，前端插件页表单）

| 字段 | 说明 |
|------|------|
| `model` | 模型规格，默认 `small`；见上表 |
| `device` | `cpu`（默认）/ `cuda` / `auto` |
| `compute_type` | `int8`（CPU 默认）/ `int8_float16` / `float16` / `float32` |
| `language` | 留空自动检测；知道语种时填 `zh`/`en`/`ja` 可减少误判 |
| `vad_filter` | 静音过滤，**默认开**；关掉后长静音处容易产出重复的幻觉字幕 |
| `hf_endpoint` | 模型下载源，默认 `https://hf-mirror.com`；留空走官方 |
| `download_root` | 模型缓存目录，留空用 HuggingFace 默认 |

## 常见问题

- **「就绪」但第一次转写等很久**：在下载模型（见上）。日志里能看到
  `Downloading ...` 进度。
- **`device=cuda` 报 `Could not load library cublas64_12.dll` / `cudnn` 缺失**：
  CTranslate2 不自带 CUDA 运行库，需自行安装与 CUDA 版本匹配的 cuBLAS + cuDNN。
  **它不会静默退回 CPU** —— 这是刻意的，否则你会在毫不知情的情况下等一个
  慢 10 倍的结果。
- **转写结果是空**：音频本身无语音内容；或 `vad_filter` 把整段判成静音
  （音乐/噪声）—— 试着关掉它再跑一次。
- **中文识别成繁体 / 英文**：把 `language` 显式设为 `zh`；
  仍不准时升级到 `medium`。
- **`ImportError: DLL load failed`**：venv 里的 onnxruntime 与系统架构不匹配，
  删掉 `.venv` 重新「一键安装」。

## 取消语义

`engine.py` **逐段**轮询 `cancel_file`（转写是流式生成器，解码发生在迭代时），
命中即 `sys.exit(0)` 且不输出任何结果。主进程宽限 3s 后强杀兜底。

⚠️ 刻意**不输出已解码的部分**：主进程收到半截 `segments` 会当成成功并落库，
用户以为转写完成了 —— 取消应该是取消，不是「悄悄给你前 3 分钟」。
