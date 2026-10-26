"""DashScope 向量化引擎（云端内置，`text-embedding-v4`）。

**为什么它留在核心**（其余重依赖都走插件口子）：判据是「口子原则」的四标准 ——
依赖不重（复用已装的 `openai` SDK，零新依赖）、无协议污染（HTTP 调用，不是 AGPL）、
跨平台无碍（云端算）、无法律风险。这与 whisper（CUDA）/ tesseract（280M 便携版）/
docling（torch ~2GB）的差别正是「重不重」。

## 三条来自官方文档的硬数字（照抄会出事的那种）

| 参数 | 值 | 不遵守的后果 |
|---|---|---|
| 单次 `input` 条数 | **≤ 10** | HTTP 400 `batch size is invalid … should not be larger than 10` |
| 单条 token | ≤ 8192 | 服务端**静默截断**（不报错，后半段从未被索引） |
| `dimensions` 可选 | 2048 / 1536 / **1024（默认）** / 768 / 512 / 256 / 128 / 64 | — |

OpenAI 兼容模式**只支持 `float` 编码**，也不支持 `output_type` / `text_type`（那两个
要走原生 dashscope 端点；往兼容端点发 `output_type=sparse` 会拿到 **HTTP 200 + 空向量**,
比 400 更难查）。

⚠️ `BATCH_SIZE = 10` 是**服务端契约不是调优参数**：它看起来像可以调的 batch size，
但调大只会拿到 400。见 §「批处理」的实现注释。

## 为什么不复用 `LLMService`

`LLMService` 的入口是 chat/completions（流式、工具调用、思考档位…），embeddings 是
另一条 API；硬塞进去会让「一个类负责两种协议」。这里只借它的构造风格（惰性 client +
`max_retries=0`）—— `max_retries=0` 同样是有意的：重试由上层按「连续失败 3 篇停 worker」
的纪律统一决定，SDK 层偷偷重试会让失败计数永远涨不上去。
"""

import logging

from openai import AsyncOpenAI

from app.core.provider_defaults import PROVIDER_DEFAULTS
from app.engines.embedding_base import l2_normalize

logger = logging.getLogger(__name__)

#: OpenAI 兼容端点的根（与 LLM 侧的 provider 默认表**同一份**，不另写一遍字面量）
DASHSCOPE_BASE_URL = PROVIDER_DEFAULTS["dashscope"]["base_url"]

#: 单次请求最多 10 条 —— 服务端硬上限（超了 400），不是调优旋钮
BATCH_SIZE = 10

#: 单条请求超时（秒）。一次 10 条、单条 200 字的批通常 100–300ms
REQUEST_TIMEOUT = 60.0


class DashScopeEmbeddingEngine:
    """`text-embedding-v4`。接口见 `embedding_base.EmbeddingEngine`。"""

    name = "dashscope-embedding"
    model_id = "text-embedding-v4"
    dimension = 1024
    capabilities = ["embedding"]

    def __init__(self, api_key: str = ""):
        self.api_key = api_key
        self._client: AsyncOpenAI | None = None

    @property
    def client(self) -> AsyncOpenAI:
        """惰性构造（同 `LLMService.client`）。

        ⚠️ **没有 key 时不在这里抛、也在不 `__init__` 里抛**：`init_engines` 无条件注册
        这个引擎（与 OCR 同款），设置页需要在「没配 key」时也能列出它、并显示
        「未配置」。抛点放在真正要联网的那一刻，才能表达「引擎在、只是用不了」。
        """
        if self._client is None:
            if not self.api_key:
                raise RuntimeError(
                    "DashScope API Key 未配置 —— 语义检索需要它做向量化。"
                    "请在 设置 → 模型 & 引擎 → DashScope 填入 Key 后重试。"
                )
            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=DASHSCOPE_BASE_URL,
                timeout=REQUEST_TIMEOUT,
                max_retries=0,  # 重试纪律归上层（连续失败 3 篇停 worker），见模块文档
            )
            # ⚠️ 不打印 Key 的任何片段（连前 7 位也不打）：这条 logger 是标准
            # `logging` 出口，会经 `main.py` 的 BufferHandler 进内存环形缓冲 →
            # 前端日志面板 → 用户贴日志时顺带外泄。要区分「是不是同一个 Key」
            # 用不上它，要排查「Key 有没有被读到」看下面那句「未配置」的报错就够了。
            logger.info("Embedding 客户端: model=%s base_url=%s",
                        self.model_id, DASHSCOPE_BASE_URL)
        return self._client

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """编码文本 → L2 归一化向量，顺序与入参一一对应。

        ⚠️ **必须按 `index` 重排**：OpenAI 兼容端点的 `data` 数组带 `index` 字段，
        但**顺序不保证**。直接按返回顺序 append 会让「文本与向量错位」—— 检索质量
        下降、零报错、单测全绿（假数据通常恰好有序）。这条有专门的测试。
        """
        if not texts:
            return []

        out: list[list[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            batch = texts[start:start + BATCH_SIZE]
            resp = await self.client.embeddings.create(
                model=self.model_id,
                input=batch,
                dimensions=self.dimension,
                encoding_format="float",
            )
            items = sorted(resp.data, key=lambda d: d.index)
            if len(items) != len(batch):
                # 条数不符 = 契约破了。不补不猜：少一条会让后续向量与文本**整体错位**，
                # 而错位在检索时表现为「结果莫名其妙」，不像错误。
                raise RuntimeError(
                    f"向量化返回条数不符：送 {len(batch)} 条、回 {len(items)} 条"
                )
            indexes = [d.index for d in items]
            if indexes != list(range(len(batch))):
                # ⚠️ **条数对得上不等于没问题**：index 重复（`[0,0,1]`）或越界时，
                # `sorted` 之后数组长度照样等于 batch，而「哪条向量属于哪段文本」
                # 已经不可知 —— 继续下去就是本函数开头那段 docstring 要防的静默错位。
                # 这是全函数唯一能自证契约的地方：重排的正确性全押在 index 可信上。
                raise RuntimeError(
                    f"向量化返回的 index 不是 0..{len(batch) - 1}：{indexes}"
                )
            for i, item in enumerate(items):
                vec = list(item.embedding)
                if len(vec) != self.dimension:
                    # 维度不符必须当场报：写进库之后，余弦会在不同维度的向量之间
                    # 静默算错（或直接崩在 numpy 里，离这里已经几十行远）。
                    raise RuntimeError(
                        f"向量维度不符：期望 {self.dimension}，实际 {len(vec)}"
                        f"（model={self.model_id}，第 {start + i} 条）"
                    )
                out.append(l2_normalize(vec))
        return out
