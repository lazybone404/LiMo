"""DashScope 重排引擎（云端内置，`gte-rerank-v2`）。

**为什么它留在核心**（同 `embedding_dashscope` 的四标准）：依赖不重（`dashscope`
已在 `pyproject.toml`，零新依赖）、无协议污染（HTTP，不是 AGPL）、跨平台无碍
（云端算）、无法律风险。

## 实测过的硬数字（2026-10-29 用真实 Key 逐档探测）

| 项 | 值 | 不遵守的后果 |
|---|---|---|
| `documents` 条数 | **≤ 500** | 400 `batch size of documents is invalid, it should not be empty or larger than 500.` |
| 失败响应 | `output` 恒为 `None` | 先读 `status_code`；直接 `.output.results` 会得到 `'NoneType' object has no attribute 'results'`，而不是服务端那句可读的中文/英文原因 |
| `document` 字段 | 默认 `None` | 不传 `return_documents` 时它不是字符串，别按字符串读 |
| `relevance_score` | 0–1 的小数，**越大越相关** | 方向搞反 = 结果列表整体倒序，且看起来完全正常 |
| `usage.total_tokens` | 线性于 `query + Σdocuments`（500 条短文实测 15.6k） | 计费可见性的唯一来源，别丢 |

⚠️ **`max_documents = 500` 是实测出来的服务端契约**，不是猜的保守值：
探测脚本发 500 条返 200、501 与 800 条返 400。调大不会有收益，只会拿到 400。

## 为什么用 `AioTextReRank` 而不是 `TextReRank`

同步版的 `BaseApi.call` 走 `requests`。在事件循环里调它 = **整个后端进程（含
SSE 心跳、worker 的 await、其它并发请求）一起阻塞**，而这里等的是几百毫秒到
几秒的网络往返。异步版走 `aiohttp`，且 `trust_env=True` ⇒ `HTTP_PROXY` /
`HTTPS_PROXY` 与本项目其它出站请求同一套代理规则。

## 超时不在这里管

SDK 默认 HTTP 超时 300 秒（批处理级）。在线检索不能等 30 秒，但**这一层不设
超时** —— 由编排层 `services/rerank_search.py` 的 `RERANK_TIMEOUT` 用
`asyncio.wait_for` 统一管。理由与 `embedding_dashscope` 不自己重试一致：
「多久算超时」是编排决策，引擎只负责一次调用。`wait_for` 取消时 aiohttp 请求
会被一并取消，不会留下泄漏的连接。
"""

import logging

from dashscope import AioTextReRank

logger = logging.getLogger(__name__)

#: 单次请求最多 500 段文档 —— **实测的服务端硬上限**（500 条 200 / 501 条 400），
#: 不是调优旋钮。见模块文档的实测表。
MAX_DOCUMENTS = 500


class DashScopeRerankEngine:
    """`gte-rerank-v2`。接口见 `rerank_base.RerankEngine`。"""

    name = "dashscope-rerank"
    model_id = "gte-rerank-v2"
    max_documents = MAX_DOCUMENTS
    #: 设置页下拉的展示名（`routers/search.py::_rerank_models` 读它）。
    #: 缺了它下拉里会显示裸的 `dashscope-rerank`，用户看不出「这一次要不要花钱」。
    display_name = "云端（DashScope 重排 · 按 token 计费）"
    is_local = False

    def __init__(self, api_key: str = ""):
        self._api_key = api_key

    @property
    def api_key(self) -> str:
        """惰性校验（同 `embedding_dashscope.client`）。

        ⚠️ **抛点在这里，不在 `__init__`**：`init_engines` 无条件注册本引擎，
        设置页需要在「没配 Key」时也能列出它、并把 `ready=False` 显示成
        「未配置」而不是让整个引擎列表加载失败。抛在真正联网的那一刻，才能
        表达「引擎在、只是用不了」。

        ⚠️ **不打印 Key 的任何片段**（连前 7 位也不打）：这条 logger 经
        `main.py` 的 BufferHandler 进内存环形缓冲 → 前端日志面板 → 用户贴日志
        时顺带外泄。
        """
        if not self._api_key:
            raise RuntimeError(
                "DashScope API Key 未配置 —— 重排需要它调用云端模型。"
                "请在 设置 → 模型 & 引擎 → DashScope 填入 Key 后重试。"
            )
        return self._api_key

    async def rerank(self, query: str,
                     documents: list[str]) -> list[tuple[int, float]]:
        """候选文本 → `[(原始下标, 分数)]`，分数越大越相关。

        ⚠️ **契约 2：空输入直接返回 `[]`，一次调用都不发** —— 服务端对空
        `documents` 会返 400（实测错误串里写着 "should not be empty"），
        而调用方在「候选不足」时正会传空列表。
        """
        if not documents:
            return []

        resp = await AioTextReRank.call(
            model=self.model_id,
            query=query,
            documents=list(documents),
            api_key=self.api_key,
            # 刻意**不传** `return_documents`（我们手上就有原文，再要一遍只是
            # 白占带宽）、**不传** `top_n`（默认返回全部，截断由调用方按
            # `max_documents` 决定）、**不传** `instruct`（未经实测的服务端
            # 超参不引入 —— 它会改变排序口径，而我们的实验基线不认识它）。
        )

        # ⚠️ **必须先判 status_code**：非 200 时 SDK 把 `output` 置为 None
        # （不是抛 AttributeError，也不是省掉这个键），直接读
        # `resp.output.results` 会得到一个 `'NoneType' object has no attribute
        # 'results'` —— 用户查不到「Key 过期 / 配额不足 / 参数越界」中的哪一种。
        if resp.status_code != 200:
            raise RuntimeError(
                f"重排请求失败（HTTP {resp.status_code} {resp.code or ''}: "
                f"{str(resp.message or '')[:160]}）"
            )

        results = getattr(resp.output, "results", None) or []
        tokens = getattr(getattr(resp, "usage", None), "total_tokens", None)
        # 计费可见性：这一行是排查「重排为什么花钱」时唯一的线索来源。
        # 不记文档内容、不记 query（它可能含用户隐私），只记规模与 token。
        logger.info("重排完成: model=%s docs=%d tokens=%s",
                    self.model_id, len(documents), tokens)

        out: list[tuple[int, float]] = []
        seen: set[int] = set()
        for item in results:
            idx = int(item.index)
            if idx < 0 or idx >= len(documents):
                # 下标越界 = 服务端把候选算错了。硬报：继续下去会把这篇的分数
                # 贴到别的文档头上，而结果列表看起来完全正常（契约 1）。
                raise RuntimeError(
                    f"重排返回的下标越界：{idx}（候选 {len(documents)} 条）")
            if idx in seen:
                # 同一段候选出现两次在重排里没有语义（每篇各一个分）。两个分
                # 里哪个算数无从判断 —— 取任一个都是猜，故直接报错（契约 1）。
                raise RuntimeError(f"重排返回的下标重复：{idx}")
            seen.add(idx)
            out.append((idx, float(item.relevance_score)))
        return out
