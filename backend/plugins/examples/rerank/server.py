"""常驻重排服务 —— 在插件自己的 venv 里跑，**NDJSON over stdio**。

协议（与 `app/engines/plugin_loader.py::_PluginRerankAdapter` 一一对应）：

```
启动   → stdout 一行  {"ready": true, "model_id": "…"}
       → stdout 一行  {"ready": false, "error": "…"}   然后 exit 1
请求   ← stdin  一行  {"id": 1, "query": "…", "documents": ["…", "…"], "top_n": 20}
响应   → stdout 一行  {"id": 1, "results": [[0, 0.83], [3, 0.71]]}
单次失败 → stdout 一行 {"id": 1, "error": "…"}          （进程继续服务）
```

⚠️ **为什么是常驻进程**：bge-reranker 量级 500MB+，冷读几十秒；而重排发生在
**一次在线检索里**，`RERANK_TIMEOUT = 5.0` 秒。每轮现起一个进程 = 每次检索都超时，
用户看到的提示是「重排超时」—— 而插件根本不联网，他会去查网络。

⚠️ **`results` 的第一个数是「入参下标」不是「结果里的新位置」**：搞反的结果是整张
排名表错位，而每一行看起来都合法（分数是真的、下标是合法整数）。

⚠️ **三条必须**（与 `local_embedding/server.py` 同源，违反哪条都表现为「功能莫名
其妙」而不是报错）：

1. **每行立刻 flush**。主进程按行阻塞等待，留在缓冲区里的一行等于没发。
2. **stdout 只写协议行**，日志一律 stderr。混进半行日志会让 JSON 解析失败，
   而主进程对解析失败的处理是**杀掉这个进程** —— 现象是每次检索都重新加载模型。
3. **一个请求一个响应，顺序不能变**。主进程按 `id` 校验，错位即丢弃进程。

⚠️ **允许只返回前 `top_n` 条**：调用方会把缺失的下标按原序补到尾部。
**不要用「补一个假分数」凑满** —— 那会让尾部候选莫名排到前面，而分数看起来是真的。

⚠️ **换权重必须改 `model_id`**：实验记录与日志里靠它区分「这次用的是哪一份」，
分数漂移只有它是线索。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

#: 默认模型目录。bge-reranker-v2-m3 的 ONNX 版放在这里。
DEFAULT_MODEL_DIR = "~/.limo/models/bge-reranker-v2-m3-onnx"
DEFAULT_ONNX = "model.onnx"
DEFAULT_MODEL_ID = "bge-reranker-v2-m3"

#: **成对编码**的最大长度。cross-encoder 把 query 与 document 拼成一条送进模型，
#: 512 是 bge-reranker 的默认上限；超了 tokenizer 会**静默截断** —— 候选文本太长时
#: 被截掉的恰是后半段，而调用方（`core/search.py::rerank_window`）已经按 480 字
#: 选好了窗口，正常不会撞上。
MAX_TOKENS = 512

#: 一批塞多少对给 ONNX。重排一轮 20–50 对，一批就够；留余量给配置调大候选窗口的场合。
BATCH = 16


def log(msg: str) -> None:
    """诊断一律 stderr —— stdout 是协议通道，一个字都不能掺。"""
    print(msg, file=sys.stderr, flush=True)


def emit(obj: dict) -> None:
    """写一行协议消息。**必须 flush**（见模块文档第 1 条）。"""
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def load_reranker(model_dir: Path, onnx_name: str):
    """加载 tokenizer + ONNX Session，返回 `rerank(query, docs) -> list[(idx, score)]`。

    ⚠️ **`enable_cpu_mem_arena = False` 不能省**：ONNX Runtime 默认把推理缓冲留在
    自己的 arena 里永不归还系统。重排模型比 embedding 大一个量级，常驻内存的差别
    是几百 MB —— 与 `local_embedding/server.py` 同一笔账。
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
    sess = ort.InferenceSession(str(model_file), so, providers=["CPUExecutionProvider"])

    def rerank(query: str, documents: list[str]) -> list[list[float]]:
        pairs = [(query, d) for d in documents]
        out: list[list[float]] = []
        for i in range(0, len(pairs), BATCH):
            chunk = pairs[i:i + BATCH]
            encs = tokenizer.encode_batch(chunk)
            n = max(1, max(len(e.ids) for e in encs))
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
            logits = sess.run(None, feed)[0]
            # 每个模型输出的形状不同（[B,1] / [B,2] / [B]）；
            # 统一取第 0 列或标量，只为**排序**用 —— 分数之间的大小关系才是有意义的。
            out.extend([float(row[0]) if hasattr(row, "__len__") else float(row)
                        for row in logits.reshape(len(chunk), -1)])
        return out

    return rerank


def main() -> int:
    # Windows 上 stdio 默认是 GBK：中文进 JSON 会炸，**进来的** UTF-8 字节则会被
    # 静默解成代理字符 —— 后者更坏，`json.loads` 照样成功，编码出来的是乱码的分数，
    # 排序质量下降而零报错。三个流一个都不能少。
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

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
        rerank = load_reranker(model_dir, onnx_name)
    except Exception as e:  # noqa: BLE001 —— 起不来就如实报，主进程会把它展示给用户
        emit({"ready": False, "error": f"{type(e).__name__}: {e}"})
        return 1

    # 先跑一对：既是预热（第一个真实请求不必等 warmup），也顺手证明推理链真的能跑。
    # ⚠️ 这一步也在 try 里：推理失败（ONNX 与 tokenizer 不配套、内存不足）时必须走
    # `ready:false` 那条协议，否则进程带着 traceback 退出，主进程只能报
    # 「启动失败（进程无输出）」—— 真正的原因一个字都不在错误消息里。
    try:
        rerank("就绪自检", ["预热"])
    except Exception as e:  # noqa: BLE001
        emit({"ready": False, "error": f"预推理失败 {type(e).__name__}: {e}"})
        return 1
    emit({"ready": True, "model_id": model_id})
    log(f"ready: {model_dir} / {onnx_name}")

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
            query = str(req.get("query") or "")
            documents = [str(d) for d in (req.get("documents") or [])]
            top_n = req.get("top_n")
            if not documents:
                # 空输入返回空且**不做推理**（契约 3）—— 也为一个空批把模型跑一遍不值得
                emit({"id": rid, "results": []})
                continue
            scores = rerank(query, documents)
            # 下标按分数**降序**排（同分保留输入序 —— `sorted` 是稳定的）
            ranked = sorted(range(len(scores)), key=lambda i: -scores[i])
            if isinstance(top_n, int) and top_n > 0:
                ranked = ranked[:top_n]
            emit({"id": rid, "results": [[i, scores[i]] for i in ranked]})
        except Exception as e:  # noqa: BLE001 —— 单条失败不拖垮进程
            emit({"id": rid, "error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    sys.exit(main())
