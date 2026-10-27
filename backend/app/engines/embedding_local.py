"""本地 ONNX 向量化引擎（`bge-small-zh-v1.5`，进程内 + 惰性重依赖）。

## 为什么是进程内引擎，不是 `~/.limo/plugins/` 的 EnginePlugin

三条硬理由，**都不是「图省事」**：

1. **签名形状不对**：`EnginePlugin.execute(file_path, **kwargs)` 是「一个文件 → 一段文本」
   —— doc / ocr / asr 三类的共同形状。embedding 是「一组文本 → 一组向量」，
   连「文件」这个概念都没有。
2. **查询侧扛不住进程启动**：进程外每次 `subprocess.Popen` + 模型加载 3–5 秒，而
   `VectorSearcher.QUERY_TIMEOUT = 3.0` —— 第一次搜索必然超时。要做到可用就得写
   「常驻子进程 + IPC」，那是独立的一块工程（见 `docs/search-retrieval.md` §6.1 (4)）。
3. **口子原则的实质已经满足**：判据是「重依赖不进核心打包」，不是「文件放在哪个目录」。
   `onnxruntime` / `tokenizers` **不在 `pyproject.toml`**（本模块导入时零重依赖，
   构造 `Embedder` 时才惰性 import，缺失显式抛 `MissingDependency` 带安装命令）；
   模型文件在 `~/.limo/models/`，**不随包分发**。

## 与云端引擎的差异（只有两处，都不影响协议契约）

| 项 | `dashscope-embedding` | `local-bge-small` |
|---|---|---|
| 维度 | 1024 | **512** |
| 单条输入上限 | 8192 token | **512 token ≈ 400–500 中文字** |

⚠️ 第二行是**模型能力的硬约束，不是调优旋钮**：`DEFAULT_CHUNK_SIZE = 200` 字正好落在
本地模型的能力内，两个引擎共用同一个切块规则 ⇒ 四元组的 `chunkSize` 恒为 200，
换引擎只体现为 `modelId` + `dimension` 变化。照抄云端方案的 2000 字符块会把本地模型
撑爆 —— tokenizer **静默截断**，实测 1000 字块纯向量只有 76%，比不做向量的 79% 还低。

## 三条协议契约（见 `engines/embedding_base.py`）

顺序一一对应 / 已 L2 归一化 / 空输入返回空且不发起调用 —— 前两条由 `Embedder` 本身
保证（CLS pooling + 逐行归一化），本类只补**显式的形状校验**：条数与维度不符一律抛错，
绝不「补一条」或「猜一下」。

## ⚠️ 必须 `asyncio.to_thread`

ONNX 推理是同步 CPU 计算：首次加载模型 3–5 秒，全库 3.5 万块约 3 分钟。直接在事件循环里
跑会让后端在这段时间内**没有任何响应**（SSE 心跳、健康检查、前端请求一起停住），
而现象是「打开语义检索后应用卡死」—— 一个看起来像死锁、实际是阻塞的失败形态。
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path

from app.core.deps import probe

#: 模型目录与文件名。**与 `scripts/embed_local.py` 同一份**（那边转出本模块的常量），
#: 评测脚本与产品引擎必须指向同一个模型，否则「评测 83%」描述的是另一套权重。
DEFAULT_MODEL_DIR = "~/.limo/models/bge-small-zh-v1.5-onnx"
DEFAULT_ONNX = "model_int8.onnx"

#: `bge-small` 的 max_length 是 512 token ≈ 400–500 中文字。**换模型必须重算**。
MAX_TOKENS = 512

#: 输出维度（`bge-small-zh-v1.5` 的 hidden size）。四元组身份的一部分 ——
#: 与云端 v4 的 1024 不同 ⇒ 换引擎必然「口径不符」，需要显式重建。
DIMENSION = 512

#: 落库的模型标识（四元组身份）。⚠️ **带量化档**：int8 与 fp32 的向量不完全相同，
#: 若两者共用一个 id，换 onnx 文件时 `ensure_identity` 会判「没变」而实际向量已换
#: —— 检索质量轻微下降、零报错。换量化档必须同时改这个名字。
MODEL_ID = "bge-small-zh-v1.5-int8"

#: 依赖清单。判据是 `core.deps.probe()`（**真 import 一次**），不是 `find_spec` ——
#: 后者对 namespace package 返回非 None 却在里面找不到任何东西，于是安装版里
#: 「检查说就绪、一编码就报缺依赖」（2026-10-27 实测于 `_internal/tokenizers/`：
#: 只有 7.5MB 的 `tokenizers.pyd`、没有 `__init__.py`）。
_REQUIRED_MODULES = ("onnxruntime", "tokenizers")

#: 每个模块**真正会被用到**的属性（`Embedder.__init__` 里 import 的就是这两个名字）。
#: 只问「模块在不在」正是上面那条 bug 的形状，所以探测必须问到属性这一层。
_REQUIRED_ATTRS: dict[str, tuple[str, ...]] = {
    "onnxruntime": ("InferenceSession", "SessionOptions"),
    "tokenizers": ("Tokenizer",),
}

#: 「空闲多久算真的用不着了」的默认门槛（秒）。**策略本身由用户选**
#: （`search.vector_release`，见 `vector_worker.RELEASE_POLICIES`），这个数只是
#: `idle` 档的取值。10 分钟的依据：本库的使用节奏是「低频、天级」
#: （`vector_worker.DEFAULT_INTERVAL` 的注释），而「刚建完索引就翻回去搜一下」
#: 是真实存在的动作 —— 门槛太短会让那次搜索白白等一次模型加载。
DEFAULT_IDLE_RELEASE = 600.0


class MissingDependency(RuntimeError):
    """本地 embedding 的依赖没装。补法文案统一由 `_fix_hint` 给。"""


def _fix_hint(missing: list[str], model_dir: Path, onnx_name: str,
              lacks: list[str]) -> str:
    """「怎么补齐」的唯一一份文案 —— `precheck` 与 `Embedder.__init__` **共用**。

    ⚠️ 冻结环境（打包版）**没有 pip**：`LiMoBackend.exe` 跑在冻结的 Python 里，
    用户机器上通常也没有第二个 Python。给一条跑不了的命令比不给更糟 —— 他会以为
    是自己操作错了、反复试。那一档直说做不到。两处各写一份的失效形态是
    「设置页说这里装不了、真跑起来又让你 pip install」。
    """
    if getattr(sys, "frozen", False):
        # 「这些东西」而不是「这些依赖」：这一段也可能只为缺模型文件而出现。
        # ⚠️ 冻结环境给**两条**出路而不是一条（2026-10-27）：「改用云端」是花钱/外发，
        # 而装一次 `local_embedding` 插件之后**本地能力一样有**（它带自己的 venv）。
        # 只说云端会让用户以为离线选项在安装版上不存在。
        return ("安装版无法在应用内补齐这些东西 —— 可改用云端向量化引擎，"
                "或在 设置 → 插件 里安装本地向量化插件（离线可用）。")
    steps = []
    if missing:
        # 镜像源是全局约定（国内直连 pypi 常常几十秒超时）。
        steps.append("pip install " + " ".join(missing)
                     + " -i https://pypi.tuna.tsinghua.edu.cn/simple")
    if lacks:
        steps.append(f"把 tokenizer.json 与 {onnx_name} 放进 {model_dir}")
    # 装完必须重启：探测走的是**当前进程**的 `sys.path` / `sys.modules`，新装的包
    # 不重启就看不见 —— 那时界面会继续显示「缺依赖」，像是没装上。
    return "补齐后重启应用生效：" + "；".join(steps) + "。"


class Embedder:
    """本地 ONNX 句柄。`__call__(texts) -> (n, dim) float32`，已 L2 归一化。

    ⚠️ 这份实现是**产品与评测共用的唯一一份**（`scripts/embed_local.py` 从本模块转出）。
    「实验脚本自己重写一份 embedder」正是 40pp 落差的根因，换个位置重演同样致命。
    """

    def __init__(self, model_dir: str = DEFAULT_MODEL_DIR,
                 onnx_name: str = DEFAULT_ONNX):
        # 与 `precheck` **同一个判据**（`deps.probe`）。原先这里是 `except ImportError`
        # —— 而 DLL 缺失抛的是 `OSError`、命名空间包干脆不抛，两种都漏网到别处报错。
        # probe 通过之后下面这两行必然成功（它刚刚 import 过，`sys.modules` 命中）。
        reason = (probe("onnxruntime", attrs=_REQUIRED_ATTRS["onnxruntime"])
                  or probe("tokenizers", attrs=_REQUIRED_ATTRS["tokenizers"]))
        if reason:  # 失败显式化：不静默退化成纯字面
            raise MissingDependency(
                f"本地向量模型需要 onnxruntime 与 tokenizers。{reason}"
                + _fix_hint(list(_REQUIRED_MODULES), Path(model_dir).expanduser(),
                            onnx_name, [])
            )
        import onnxruntime as ort
        from tokenizers import Tokenizer

        d = Path(model_dir).expanduser()
        tok_file, model_file = d / "tokenizer.json", d / onnx_name
        for p in (tok_file, model_file):
            if not p.exists():
                raise MissingDependency(f"本地向量模型缺文件：{p}")

        self.tok = Tokenizer.from_file(str(tok_file))
        self.tok.enable_truncation(max_length=MAX_TOKENS)

        # ⚠️ **必须关掉 CPU memory arena**。ONNX Runtime 默认把每次推理申请的缓冲留在
        # 自己的 arena 里复用、**永不归还系统**，而 LLM-embedding 的中间激活比权重本身
        # 大一个量级（22.8MB 的 int8 权重，bs=32 × 202 token 一批的注意力矩阵就是几百
        # MB）。实测（Win11 / 本机，工作集）：
        #
        #   arena=on  bs=32 → 建库跑完后常驻 686MB   全库 3.8 分钟
        #   arena=on  bs=8  →              常驻 240MB   全库 3.4 分钟
        #   arena=off bs=32 →              常驻  93MB   全库 4.6 分钟
        #   arena=off bs=8  →              常驻  92MB   全库 4.7 分钟
        #
        # 取舍方向明确：**建库是一次性的，内存是常驻终身的**（worker 跑完不会退出进程，
        # 用户开着应用就一直占着）。多花 20% 的一次性建库时间换 600MB 常驻是划算的。
        # 也正因如此 `bs` 不必再为内存调小 —— 关掉 arena 后 bs=4 / 32 的驻留差不到 1MB。
        so = ort.SessionOptions()
        so.enable_cpu_mem_arena = False
        self.sess = ort.InferenceSession(str(model_file), so,
                                         providers=["CPUExecutionProvider"])

    def __call__(self, texts: list[str], bs: int = 32) -> "np.ndarray":
        import numpy as np

        if not texts:
            return np.zeros((0, 1), dtype=np.float32)
        out = []
        for i in range(0, len(texts), bs):
            encs = self.tok.encode_batch(texts[i:i + bs])
            n = max(len(e.ids) for e in encs)   # 批内 pad 到最长（不同批之间无关）
            feed = {
                "input_ids": np.array([e.ids + [0] * (n - len(e.ids)) for e in encs],
                                      dtype=np.int64),
                "attention_mask": np.array(
                    [e.attention_mask + [0] * (n - len(e.attention_mask)) for e in encs],
                    dtype=np.int64),
                "token_type_ids": np.array(
                    [e.type_ids + [0] * (n - len(e.type_ids)) for e in encs],
                    dtype=np.int64),
            }
            cls = self.sess.run(None, feed)[0][:, 0]      # bge = CLS pooling
            # 与 `embedding_base.l2_normalize` 数学等价（`v / ‖v‖`），这里用 numpy
            # 是为了向量化：一条条走 Python 循环在 3.5 万块上会慢一个数量级。
            out.append(cls / np.linalg.norm(cls, axis=1, keepdims=True))
        return np.vstack(out).astype(np.float32)


class LocalEmbeddingEngine:
    """`bge-small-zh-v1.5` ONNX int8。接口见 `embedding_base.EmbeddingEngine`。"""

    name = "local-bge-small"
    model_id = MODEL_ID
    dimension = DIMENSION
    capabilities = ["embedding"]

    #: 展示名（设置页下拉）。与 `EnginePlugin.display_name` 同义。
    display_name = "本地模型（bge-small-zh · 免费 · 不上传）"

    #: 前端据此选「免费 / 零外发」那套成本文案，而不是靠名字前缀猜。
    is_local = True

    def __init__(self, model_dir: str = DEFAULT_MODEL_DIR,
                 onnx_name: str = DEFAULT_ONNX, embedder_factory=None):
        """**构造零副作用**：不 import 重依赖、不加载模型、不碰磁盘。

        ⚠️ 与 `DashScopeEmbeddingEngine` 同款理由 —— `init_engines` 无条件注册所有引擎
        （设置页要在「没装依赖」时也能列出它并显示原因），抛点必须推迟到真正要用的那一刻。

        `embedder_factory` 是给测试用的注入点：真跑一次要 22.8MB 模型 + 3–5 秒加载，
        而单测要验的是**契约**（顺序 / 归一化 / 空输入 / 不阻塞事件循环），不是 ONNX。
        """
        self.model_dir = model_dir
        self.onnx_name = onnx_name
        self._factory = embedder_factory or Embedder
        self._inst = None
        #: 最后一次**真正用到**模型的时间（`time.monotonic()`）。`release()` 的
        #: 空闲判据读它 —— 用「worker 自己记的空闲轮数」会在「用户刚搜过、worker
        #: 恰好也在空转」时把刚加载的模型判成闲置。
        self._last_used = 0.0
        # 模型加载要 3–5 秒，而 worker 预热与「开启后立刻查询」可能同时到达 ——
        # 没有这把锁会并行构造两个 InferenceSession（内存翻倍，且后一个把前一个
        # 的句柄丢掉 ⇒ 每个仍持有它的一方都以为自己用的是唯一的那个）。
        self._lock = threading.Lock()

    # ── 惰性加载 ──

    @property
    def _embedder(self):
        if self._inst is None:
            with self._lock:
                if self._inst is None:
                    self._inst = self._factory(self.model_dir, self.onnx_name)
        self._last_used = time.monotonic()
        return self._inst

    async def warmup(self) -> None:
        """把模型加载进内存（3–5 秒）。**worker 开跑前调一次**（见 `vector_worker`）。

        ⚠️ 不预热的后果不是「慢」而是「第一次查询必定退回纯字面」：加载要 3–5 秒，
        而 `QUERY_TIMEOUT = 3.0`。用户看到的提示是「语义检索超时」，读起来像网络问题，
        而他刚开的本地模型**根本不联网**。

        ⚠️ **已加载时零成本返回**（但刷新 `_last_used`）：调用方是每次查询
        （`VectorSearcher` 冷启动兜底），不加这个快路的话每次搜索都白占一次
        线程池调度。
        """
        if self._inst is not None:
            self._last_used = time.monotonic()
            return
        await asyncio.to_thread(lambda: self._embedder)

    # ── 释放（可选钩子，`vector_worker` 用 getattr 消费） ──

    def release(self, *, after_idle: float = DEFAULT_IDLE_RELEASE) -> bool:
        """空闲够久就放掉已加载的模型，归还那 ~90MB 常驻。返回「这次真的释放了吗」。

        ⚠️ **不进 `EmbeddingEngine` Protocol**（同 `precheck` / `warmup`）：
        那个协议是 `runtime_checkable`，加成员会让云端引擎不再满足它 —— 而云端
        引擎的 client 本就是几个字符串，压根没有「释放」这回事。消费方一律 `getattr`。

        ⚠️ **判据必须读「最后一次真正使用」而不是调用方的轮数**：worker 空转时
        并不知道用户刚搜过一次，只看自己的循环会把刚加载的模型判成闲置。

        ⚠️ **`after_idle=0` 表示「不看空闲、直接就放」**（`immediate` 档）；调用方
        （`vector_worker.maybe_release`）负责把它限定在「本轮真的建过东西之后」，
        否则每一轮空转都会把用户刚加载的模型踢掉 ⇒ 每次搜索都冷启动。

        ⚠️ **释放之后第一次 `embed()` 要重新加载 3–5 秒**，这是这个开关的全部代价。
        `VectorSearcher` 因此在编码前先 `warmup()` 并给它独立超时 —— 否则那次查询
        会撞上 `QUERY_TIMEOUT = 3.0` 并退化成纯字面（用户看到的是「超时」）。
        """
        if self._inst is None:
            return False                       # 无锁快判：大多轮次都是这个分支
        if after_idle > 0 and (time.monotonic() - self._last_used) < after_idle:
            return False
        with self._lock:
            if self._inst is None:             # 另一个调用方抢先放了
                return False
            self._inst = None
        return True

    # ── 就绪自检（可选钩子，`vector_runtime` 用 getattr 消费） ──

    def precheck(self) -> str | None:
        """返回**给用户看的**没就绪原因（`None` = 可以跑）。

        与 `VectorSearcher._precheck` 分层：那个管「索引库 / 口径对不对」，
        这个管「这台机器上引擎能不能起来」。两件事用户该做的动作不同
        （装依赖 vs 点重建），合并成一句话会让人无从下手。

        ⚠️ **必须把「使用条件」说全，不能只说缺哪个包**：本地引擎**刻意不随应用
        分发**（模型 22.8MB 与 `onnxruntime` 的 CPU 原生库都不进安装包），所以它对
        谁都不是「装上就能用」，而是「先自己备好运行环境」。只写「缺少 onnxruntime」
        会让人以为是我们漏打了、下次更新会带上，于是一直等。

        ⚠️ **冻结环境（打包版）不能给 `pip install`**：`LiMoBackend.exe` 跑在冻结的
        Python 里，**没有 pip**，用户机器上通常也没有第二个 Python。给一条跑不了的
        命令比不给更糟 —— 他会以为是自己操作错了，反复试。那一档直说做不到
        （失败显式化：宁可说「这里没有」，也别给一条走不通的路）。
        """
        d = Path(self.model_dir).expanduser()
        # 判据是 `deps.probe()`（**真 import 一次 + 查属性**），不是 `find_spec`：
        # 后者对 namespace package 说「找得到」，而里面一个属性都没有 —— 那条路
        # 上自检报就绪、用户切过去、编码那一刻才炸（2026-10-27 实测于安装版）。
        fails = [(m, r) for m in _REQUIRED_MODULES
                 if (r := probe(m, attrs=_REQUIRED_ATTRS[m])) is not None]
        missing = [m for m, _ in fails]
        lacks = [n for n in ("tokenizer.json", self.onnx_name) if not (d / n).exists()]
        if not missing and not lacks:
            return None

        # 前半句只说「缺哪一类」，具体文件名与路径交给后半句 —— 两边都列一遍
        # 就是同一件事在同一个 <p> 里说两遍（实测过长，读起来像复读）。
        need = []
        if missing:
            need.append("运行库 " + " 与 ".join(missing))
        if lacks:
            need.append("模型文件")

        # 具体原因（DLL 缺失 / 安装残缺）单独补一句：只列模块名会让人以为
        # 「这个包没装」，于是去装一个**已经装着**的包，装完现象一模一样。
        detail = ("原因：" + "；".join(r for _, r in fails) + "。") if fails else ""

        return ("本地向量模型不随应用分发，需自行准备：" + "、".join(need) + "。"
                + detail + _fix_hint(missing, d, self.onnx_name, lacks))

    # ── 协议入口 ──

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """编码文本 → L2 归一化向量，顺序与入参一一对应。

        契约 3（空输入返回空、且**不发起任何调用**）在这里就短路：连模型都不加载 ——
        「查一个空批」不该把 22.8MB 权重拖进内存。
        """
        if not texts:
            return []
        return await asyncio.to_thread(self._embed_sync, list(texts))

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        # 不 import numpy：`mat` 的 shape 与 tolist() 都是数组自带的方法，
        # 这里只读不造。
        mat = self._embedder(texts)
        n, dim = int(mat.shape[0]), int(mat.shape[1])
        if n != len(texts):
            # 条数不符 = 契约破了。不补不猜：少一条会让后续向量与文本**整体错位**，
            # 而错位在检索时表现为「结果莫名其妙」，不像错误。
            raise RuntimeError(f"本地向量条数不符：送 {len(texts)} 条、回 {n} 条")
        if dim != self.dimension:
            # 维度不符必须当场报：写进库之后余弦会在不同维度之间静默算错。
            raise RuntimeError(
                f"本地向量维度不符：期望 {self.dimension}，实际 {dim}"
                f"（model={self.model_id}）")
        return mat.tolist()
