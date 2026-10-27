# local_embedding — 本地向量化插件（bge-small-zh-v1.5 ONNX int8）

常驻子进程 + NDJSON over stdio，为语义检索提供**完全本地**的向量化（免费、零外发）。

## 安装

```bash
# 1. 拷贝声明文件 + 本目录到 ~/.limo/plugins/
cp -r local_embedding_plugin.py local_embedding ~/.limo/plugins/

# 2. 装依赖（二选一）
#    a) 设置 → 插件 → 「一键安装依赖」（自动建 venv，走清华镜像）
#    b) 手动：
cd ~/.limo/plugins/local_embedding
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 3. 模型文件（见下）

# 4. 重启后端 → 设置 → 通用 → 检索索引 → 向量引擎选「本地模型（…· 插件版）」
```

## 模型文件（22.8MB，不随插件分发）

插件只读**两个文件**：`tokenizer.json` 与 `model_int8.onnx`。

默认目录 `~/.limo/models/bge-small-zh-v1.5-onnx/` —— **与内置本地引擎同一个位置**，
之前用过内置版的话文件已经在了，跳过这一步。

没有的话，从下面任一处取得 ONNX 版（int8 量化）：

- **ModelScope（国内快）**：`BAAI/bge-small-zh-v1.5` 的 ONNX 导出仓库
- **HuggingFace**：用 `optimum-cli export onnx --model BAAI/bge-small-zh-v1.5 --optimize O4 out/`
  自己导一份，把产物里的 `model.onnx` 重命名为 `model_int8.onnx`

目录里应当有：

```
~/.limo/models/bge-small-zh-v1.5-onnx/
├── tokenizer.json
└── model_int8.onnx
```

模型放在别处也行：设置 → 插件 → 本插件卡片里的「模型目录」改成你的路径（改完自检立刻反映）。

## ⚠️ 换权重必须改 `model_id`

`(model_id, dimension, chunk_size, schema_version)` 是向量库的**整体身份**。
int8 与 fp32 的向量并不相同，而 `model_id` 不变时 `ensure_identity` 会判「口径没变」，
新旧向量就混在同一张表里 —— **检索质量下降、零报错**。

换文件的做法：改声明文件 `local_embedding_plugin.py` 的 `model_id`（例如
`bge-small-zh-v1.5-fp32`），重启，然后在设置页点「重建向量索引」（重建是计费/耗时动作，
不会自动发生）。

## 调试

```bash
# 直接跑服务（手动验证协议）
cd ~/.limo/plugins/local_embedding
echo '{"id":1,"texts":["你好"]}' | .venv/Scripts/python.exe -u server.py

# 期望输出两行：
# {"ready": true, "model_id": "bge-small-zh-v1.5-int8", "dimension": 512}
# {"id": 1, "vectors": [[...512 个数...]]}
```

- **卡在启动 / 每次搜索都超时**：`command` 里的 `-u` 漏了，或 `server.py` 忘了 flush
  （主进程按行阻塞等待，缓冲区里的一行等于没发）
- **报告维度与声明不符**：声明文件里的 `dimension` 与模型实际输出不一致（bge-small 恒 512）
- **每轮都重启进程**：stdout 混进了非协议内容（日志必须走 stderr）
