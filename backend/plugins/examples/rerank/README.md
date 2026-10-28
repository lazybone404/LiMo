# rerank — 本地重排插件（bge-reranker-v2-m3 ONNX）

常驻子进程 + NDJSON over stdio，为**对话侧检索**提供交叉编码器重排（免费、零外发）。

与 `local_embedding/` 是同一套形态、同一套协议骨架，**只有请求/响应的载荷不同**：
向量化是「一组文本 → 一组向量」，重排是「一个查询 + 一组候选 → 一组 `[下标, 分数]`」。

## 安装

```bash
# 1. 拷贝声明文件 + 本目录到 ~/.limo/plugins/
cp -r rerank_plugin.py rerank ~/.limo/plugins/

# 2. 装依赖（二选一）
#    a) 设置 → 插件 → 「一键安装依赖」（自动建 venv，走清华镜像）
#    b) 手动：
cd ~/.limo/plugins/rerank
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
#    Linux/macOS：.venv/bin/pip install -r requirements.txt -i https://...

# 3. 模型文件（见下）

# 4. 重启后端 → 设置 → 通用 → 检索索引 → 重排 → 引擎选「本地模型（bge-reranker · 插件版）」
```

## 模型文件（约 544MB，不随插件分发）

插件只读**两个文件**：`tokenizer.json` 与 `model.onnx`，默认在
`~/.limo/models/bge-reranker-v2-m3-onnx/`。

```bash
# HuggingFace 官方导出（推荐，需要 optimum[onnxruntime]）
optimum-cli export onnx --model BAAI/bge-reranker-v2-m3 --task text-classification out/
# 然后
mkdir -p ~/.limo/models/bge-reranker-v2-m3-onnx
cp out/model.onnx out/tokenizer.json ~/.limo/models/bge-reranker-v2-m3-onnx/
```

目录里应当有：

```
~/.limo/models/bge-reranker-v2-m3-onnx/
├── tokenizer.json
└── model.onnx
```

想省空间与内存可以再量化成 int8（`onnxruntime.quantization.quantize_dynamic`），
把产物命名成 `model.onnx` 或改插件设置里的「ONNX 文件名」。**量化后请同步改
`rerank_plugin.py` 的 `model_id`**（见下）。

模型放在别处也行：设置 → 插件 → 本插件卡片里的「模型目录」改成你的路径
（改完自检立刻反映，不必重启 —— 自检只查 `Path.exists()`）。

## ⚠️ 换权重必须改 `model_id`

重排**不落盘任何东西**，所以它不像向量那样有一个「身份四元组」需要重建。
`model_id` 在这里的唯一用途是**归因**：日志与实验记录里，分数漂移只有它能回答
「这轮换的是哪一份权重」，否则「换了模型」与「数据变了」长得一模一样。

换文件的做法：改 `rerank_plugin.py` 的 `model_id`（例如 `bge-reranker-v2-m3-int8`），
重启后端。

## 调试

```bash
# 直接跑服务（手动验证协议）
cd ~/.limo/plugins/rerank
echo '{"id":1,"query":"向量检索","documents":["这篇讲 RRF 融合","这篇讲图片压缩"],"top_n":2}' \
  | .venv/Scripts/python.exe -u server.py
# Linux/macOS：... | .venv/bin/python3 -u server.py

# 期望输出两行：
# {"ready": true, "model_id": "bge-reranker-v2-m3"}
# {"id": 1, "results": [[0, 8.13], [1, -3.42]]}
```

### 常见现象

- **卡在启动 / 每次检索都超时**：`command` 里的 `-u` 漏了，或 `server.py` 忘了 flush
  （主进程按行阻塞等待，缓冲区里的一行等于没发）
- **每轮都重启进程**：stdout 混进了非协议内容（日志必须走 stderr）
- **设置页说「就绪」，一检索就报缺文件**：`precheck()` 与 `server.py` 读的不是同一个
  默认目录 —— 两处 `DEFAULT_MODEL_DIR` 必须一致（改了请同时改两处）
- **`results` 第一项总是指错文档**：`server.py` 把「排序后的新位置」当成了「入参下标」
- **分数看起来合理，但排序明显比不重排还差**：多半是 `token_type_ids` 没喂
  （部分 ONNX 导出会把它算成必需输入），或模型按 pair 编码时被截断到了后半段
