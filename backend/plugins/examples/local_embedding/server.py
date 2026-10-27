"""常驻向量化服务 —— 在插件自己的 venv 里跑，**NDJSON over stdio**。

协议（与 `app/engines/plugin_loader.py::_PluginEmbeddingAdapter` 一一对应）：

```
启动   → stdout 一行  {"ready": true, "model_id": "…", "dimension": 512}
       → stdout 一行  {"ready": false, "error": "…"}   然后 exit 1
请求   ← stdin  一行  {"id": 1, "texts": ["…", "…"]}
响应   → stdout 一行  {"id": 1, "vectors": [[…], …]}
单次失败 → stdout 一行 {"id": 1, "error": "…"}          （进程继续服务）
```

⚠️ **为什么是常驻进程而不是「一次调用一个进程」**：模型加载 3–5 秒，而查询侧的
`VectorSearcher.QUERY_TIMEOUT = 3.0` —— 每次搜索都现起一个进程，等于每次搜索都超时。
文件引擎（doc/ocr/asr）可以一次性进程，是因为它们本来就只在导入时跑一次。

⚠️ **三条必须，违反哪条都表现为「功能莫名其妙」而不是报错**：

1. **每行立刻 flush**。主进程是按行阻塞等待的，留在缓冲区里的一行等于没发。
   `command` 里的 `-u` 是第二层保险（双保险不冗余：用户手动跑时不会带 `-u`）。
2. **stdout 只写协议行**，日志一律走 stderr。混进半行日志会让 JSON 解析失败，
   而主进程对解析失败的处理是**杀掉这个进程** —— 现象是每次搜索都重新加载模型。
3. **一个请求一个响应，顺序不能变**。主进程按 `id` 校验，错位即丢弃进程。
   所以这里是单线程顺序循环：**不要**为了吞吐改成线程池（并发写 stdout 会交错）。

⚠️ **换成别的模型要同时改 `model_id`**：`(model_id, dimension, chunk_size, schema_version)`
是向量库的整体身份。权重变了而 `model_id` 不变时，`ensure_identity` 会判「没变」，
于是新旧向量混在同一张表里 —— 检索质量下降、零报错。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

#: 默认模型目录。**与内置引擎 `embedding_local.DEFAULT_MODEL_DIR` 同一个位置** ——
#: 用户此前用过内置本地引擎的话，22.8MB 的模型文件已经在那儿了，装插件不用重复下载。
DEFAULT_MODEL_DIR = "~/.limo/models/bge-small-zh-v1.5-onnx"
DEFAULT_ONNX = "model_int8.onnx"
DEFAULT_MODEL_ID = "bge-small-zh-v1.5-int8"

#: 模型能力上限（不是调优旋钮）。切块大小 200 字正好落在里面；用更大的块会让
#: tokenizer **静默截断**，向量只覆盖开头一小段。
MAX_TOKENS = 512

#: 一批塞多少条给 ONNX。关掉 arena 之后内存不再随批大小涨，这个数就不必为内存低头。
BATCH = 32


def log(msg: str) -> None:
    """诊断一律 stderr —— stdout 是协议通道，一个字都不能掺。"""
    print(msg, file=sys.stderr, flush=True)


def emit(obj: dict) -> None:
    """写一行协议消息。**必须 flush**（见模块文档第 1 条）。"""
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def load_embedder(model_dir: Path, onnx_name: str):
    """加载 tokenizer + ONNX Session，返回 `embed(texts) -> list[list[float]]`。

    ⚠️ **`enable_cpu_mem_arena = False` 不能省**：ONNX Runtime 默认把推理缓冲留在
    自己的 arena 里永不归还系统，而 embedding 的中间激活比权重本身大一个量级。
    实测（Win11 / bs=32 / 22.8MB int8 权重）：arena 开 = 常驻 686MB，关 = 93MB；
    代价是建库慢 20%。**建库是一次性的，内存是常驻终身的** —— 这笔账很划算。
    """
    import numpy as np
    import onnxruntime as ort
    from tokenizers import Tokenizer

    tok_file, model_file = model_dir / "tokenizer.json", model_dir / onnx_name
    for p in (tok_file, model_file):
        if not p.exists():
            raise FileNotFoundError(f"缺模型文件：{p}")
    tokenizer = Tokenizer.from_file(str(tok_file))
    tokenizer.enable_truncation(max_length=MAX_TOKENS)

    so = ort.SessionOptions()
    so.enable_cpu_mem_arena = False
    sess = ort.InferenceSession(
        str(model_file), so, providers=["CPUExecutionProvider"]
    )

    def embed(texts: list[str]) -> list[list[float]]:
        rows = []
        for i in range(0, len(texts), BATCH):
            encs = tokenizer.encode_batch(texts[i:i + BATCH])
            n = max(1, max(len(e.ids) for e in encs))   # 批内 pad 到最长
            feed = {
                "input_ids": np.array(
                    [e.ids + [0] * (n - len(e.ids)) for e in encs], dtype=np.int64),
                "attention_mask": np.array(
                    [e.attention_mask + [0] * (n - len(e.attention_mask)) for e in encs],
                    dtype=np.int64),
                "token_type_ids": np.array(
                    [e.type_ids + [0] * (n - len(e.type_ids)) for e in encs],
                    dtype=np.int64),
            }
            cls = sess.run(None, feed)[0][:, 0]            # bge = CLS pooling
            # L2 归一化（契约 2）：归一化之后余弦退化成点积，查询侧就不必再算模长。
            rows.extend((cls / np.linalg.norm(cls, axis=1, keepdims=True)).tolist())
        return rows

    return embed


def main() -> int:
    # Windows 上 stdout/stderr 默认是 GBK，中文进 JSON 会炸。
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    # 配置由主进程经环境变量传入（设置页表单 → `plugin_config` → `LIMO_PLUGIN_CONFIG`）。
    cfg: dict = {}
    raw = os.environ.get("LIMO_PLUGIN_CONFIG")
    if raw:
        try:
            cfg = json.loads(raw) or {}
        except json.JSONDecodeError:
            log("LIMO_PLUGIN_CONFIG 不是合法 JSON，改用默认值")

    model_dir = Path(str(cfg.get("model_dir") or DEFAULT_MODEL_DIR)).expanduser()
    onnx_name = str(cfg.get("onnx_name") or DEFAULT_ONNX)
    model_id = str(cfg.get("model_id") or DEFAULT_MODEL_ID)

    try:
        embed = load_embedder(model_dir, onnx_name)
    except Exception as e:  # noqa: BLE001 —— 起不来就如实报，主进程会把它展示给用户
        emit({"ready": False, "error": f"{type(e).__name__}: {e}"})
        return 1

    # 先跑一条：既是预热（第一个真实请求不必等 warmup），也顺手拿到**真实维度**
    # —— 声明值与实际值不符时主进程会当场报错（写进库之后口径不符是静默的）。
    dim = len(embed(["就绪自检"])[0])
    emit({"ready": True, "model_id": model_id, "dimension": dim})
    log(f"ready: {model_dir} / {onnx_name} / dim={dim}")

    while True:
        line = sys.stdin.readline()
        if not line:
            return 0                    # 主进程关了管道 = 正常退出（release / 关闭应用）
        line = line.strip()
        if not line:
            continue
        rid = None
        try:
            req = json.loads(line)
            rid = req.get("id")
            texts = [str(t) for t in (req.get("texts") or [])]
            emit({"id": rid, "vectors": embed(texts) if texts else []})
        except Exception as e:  # noqa: BLE001 —— 单条失败不拖垮进程
            emit({"id": rid, "error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    sys.exit(main())
