"""
LLMService — 统一的 LLM 通信层

整个后端只有这一个地方创建 AsyncOpenAI 客户端。
内置：模型解析 · 流式/非流式 · 取消 · 续写 · 多模态检测 · 重试
"""

import asyncio
import logging
from typing import AsyncGenerator, Awaitable, Callable

from openai import AsyncOpenAI

from app.core.model_resolver import resolve_model
from app.llm.errors import CancelledError, LLMError

logger = logging.getLogger(__name__)

MAX_TOKENS = 65536
_RETRY_COUNT = 2  # 网络错误重试次数

# 思考链回调签名：async (text: str) -> None
AwaitableCallable = Callable[[str], Awaitable[None]]


class LLMService:
    """统一的 LLM 通信层。构造时完成模型解析，后续只用 .stream() / .call()。

    用法:
        llm = LLMService(config, model_id=1417368505)
        async for chunk in llm.stream(prompt, cancel_event=ev):
            yield chunk
    """

    # 单次 LLM 请求的默认超时（秒）。调用方可按场景覆盖——例如勾选图表生成时
    # 输出更多（多张语义 JSON），需要更长的窗口，见 routers/generate.py 的 _gen_timeout。
    DEFAULT_REQUEST_TIMEOUT = 330.0

    def __init__(self, config: dict, model_id: int | None = None,
                 request_timeout: float = DEFAULT_REQUEST_TIMEOUT):
        # ── 模型解析（以前三个路由各自调 resolve_model）──
        llm_cfg = dict(config.get("llm", {}))
        if model_id:
            resolved = resolve_model(config, model_id)
            if resolved:
                llm_cfg.update(resolved)

        self.provider = llm_cfg.get("provider", "deepseek")
        self.model = llm_cfg.get("model", {}).get(self.provider, "deepseek-flash")
        self.api_key = llm_cfg.get("api_key", "")
        self.base_url = llm_cfg.get("base_url", {}).get(self.provider)
        self.capabilities = llm_cfg.get("capabilities", ["text"])

        # 回退 API key（从 providers 列表查找）
        if not self.api_key:
            from app.routers.model import _get_providers
            for p in _get_providers(config):
                if p["id"] == self.provider and p.get("api_key"):
                    self.api_key = p["api_key"]
                    self.base_url = p.get("base_url", "")
                    break

        # ── Key 格式检测：防止 DashScope Key 错配到 DeepSeek 等 ──
        self._warn_key_mismatch()

        # ⚠️ 这个值真正生效的地方是下面的 client property —— AsyncOpenAI 是**惰性构造**的，
        # 只改 __init__ 会「存了不用」（值进了实例属性但 property 里写死 330.0），
        # 超时一点没变且单测照样 PASS，只有端到端才暴露。两处必须同步。
        self._request_timeout = float(request_timeout)
        self._client: AsyncOpenAI | None = None

    def _warn_key_mismatch(self):
        """检测 API Key 格式与 provider 是否匹配，防止配置错乱"""
        if not self.api_key:
            return
        pk_prefix = self.api_key[:3] + "***" if len(self.api_key) > 3 else "***"
        url = (self.base_url or "").lower()

        mismatches = []
        if "dashscope" in url and not (self.api_key.startswith("sk-ws-") or self.api_key.startswith("sk-ht-")):
            mismatches.append(f"DashScope Key 通常以 sk-ws- 或 sk-ht- 开头，当前前缀 {pk_prefix}")
        if ("deepseek" in url or "api.deepseek" in url) and self.api_key.startswith("sk-ws-"):
            mismatches.append(f"疑似 DashScope Key（前缀 {pk_prefix}）被用于 DeepSeek，将导致 401")
        # ⚠️ 已知文案偏差（2026-10-02 记录，**不改逻辑**）：google 的 base_url 自本次起是
        #    OpenAI 兼容层 `.../v1beta/openai`，含 "openai" → 这条也会命中它，于是
        #    「用 DashScope Key 配 Google」会报「被用于 **OpenAI**」。
        #    触发条件是**真的配错了**（warning 的实质正确），只是厂商名说错；改判据为
        #    `api.openai.com` 会连带漏掉用户自配的含 "openai" 的中转地址，得不偿失。
        if ("openai" in url or "api.openai" in url) and self.api_key.startswith("sk-ws-"):
            mismatches.append(f"疑似 DashScope Key（前缀 {pk_prefix}）被用于 OpenAI，格式不匹配")
        if ("anthropic" in url or "api.anthropic" in url) and not self.api_key.startswith("sk-ant-"):
            mismatches.append(f"Anthropic Key 通常以 sk-ant- 开头，当前前缀 {pk_prefix}")

        for msg in mismatches:
            logger.warning("Key 格式异常: %s (provider=%s)", msg, self.provider)

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            if not self.api_key:
                raise LLMError(
                    f"缺少 API Key: provider={self.provider}",
                    provider=self.provider, model=self.model,
                )
            kwargs = {"api_key": self.api_key, "timeout": self._request_timeout,
                      "max_retries": 0}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = AsyncOpenAI(**kwargs)
            logger.info("LLM 客户端: provider=%s model=%s base_url=%s key_prefix=%s",
                       self.provider, self.model, self.base_url or "default",
                       self.api_key[:7] + "***")
        return self._client

    @property
    def supports_vision(self) -> bool:
        return any("image" in (c or "") for c in self.capabilities)

    @property
    def supports_document(self) -> bool:
        """能否在消息里带 `file` 块（PDF 直传）。

        **按模型能力标注，不按 provider 判断**——同 supports_reasoning。
        ⚠️ 刻意不复用 `image` 位：它 gate 着「AI 分析图片」「OCR 模型选择」
        「图片处理用途筛选」三处入口，拿它代指 file 块会错误放开这些入口。
        """
        return any("document" in (c or "") for c in self.capabilities)

    @property
    def supports_reasoning(self) -> bool:
        """是否认 reasoning_effort 参数。**按模型能力标注，不按 provider 判断**——
        provider 级硬编码会在新增支持该参数的渠道时前后端各改一处，
        与「能力位由模型标注」的既有原则相悖。"""
        return any("reasoning" in (c or "") for c in self.capabilities)

    async def aclose(self) -> None:
        """关闭底层 AsyncOpenAI/httpx 连接池 — 每请求新建的 LLMService 用完调用，防常驻累积"""
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass
            self._client = None

    # ── 核心方法 ──

    async def stream(
        self,
        prompt: str, *,
        image_urls: list[str] | None = None,
        document: dict | None = None,
        max_tokens: int = MAX_TOKENS,
        temperature: float = 0.7,
        max_continuations: int = 3,
        cancel_event: asyncio.Event | None = None,
        on_reasoning: AwaitableCallable | None = None,
        reasoning_effort: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """流式 LLM 调用，每个 chunk 间检查 cancel_event。

        finish=length 时自动续写（追加"继续"→再请求）。

        on_reasoning：思考模型（deepseek-v4 等）的 reasoning_content 回调——
        推理链不混入正文，单独逐段回调，供前端展示"模型思考中"。

        reasoning_effort：思考强度（'low'/'high'/'max'，deepseek 专用）。
        仅对支持该参数的模型生效（qwen 等忽略 extra_body 未知字段）。
        文档：thinking 默认开启且 effort 默认 high——简单任务传 'low'
        可大幅缩短思考静默期（polished 整理是忠实转录任务，无需深度思考）。

        ⚠️ temperature 的效力取决于模型（2026-09-23 依官方「思考模式」页补注）：
        DeepSeek 思考模式下 temperature / presence_penalty / frequency_penalty
        **不报错但不生效**，而思考模式是默认开启的——故下方续写时的 temperature
        递减对 DeepSeek 实际是空操作（保留是为兼容非思考模式与其他厂商模型）。
        top_p 则相反：仅思考模式生效，且低于 0.95 的取值按 0.95 处理。

        document：文档直传块（`{"filename", "data_uri"}`，见 services/doc_understand.py）。
        有它时**原件随消息发给模型**，prompt 里只有一句引用说明——这是「只发原文件」
        语义的落点，与 image_urls（图片走 image 能力）互不依赖。
        ⚠️ 直传模式下模型解析原件期间**一个 chunk 都不会到**（首包实测按 ~10s/MB 增长），
        调用方的 chunk 静默超时必须相应放宽，见 generate.py 的 chunk_timeout_for。
        """

        # ── 构建消息 ──
        # 三路互斥。**文档直传排在图片之前**：直传模式下 prompt 里没有正文，
        # 图片无处可嵌（prepare 已保证此时 image_urls=None），谁优先都不会错，
        # 但把「原件」放在前面语义更直白。
        if document:
            messages: list[dict] = [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "file",
                    "file": {
                        "filename": document.get("filename", "document.pdf"),
                        "file_data": document.get("data_uri", ""),
                    },
                },
            ]}]
            logger.info("LLM 文档直传流式: provider=%s model=%s filename=%s data_uri_chars=%dK",
                        self.provider, self.model, document.get("filename", ""),
                        len(document.get("data_uri", "")) // 1000)
        elif image_urls and self.supports_vision:
            content: list[dict] = [{"type": "text", "text": prompt}]
            for url in image_urls:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": url, "detail": "auto"},
                })
            messages = [{"role": "user", "content": content}]
            total_img_chars = sum(len(u) for u in image_urls)
            logger.info("LLM 多模态流式: provider=%s model=%s images=%d img_chars=%dK",
                        self.provider, self.model, len(image_urls), total_img_chars // 1000)
        else:
            messages = [{"role": "user", "content": prompt}]
            logger.info("LLM 流式: provider=%s model=%s tokens=%d",
                        self.provider, self.model, max_tokens)

        # ── 多轮续写循环 ──
        for round_idx in range(max_continuations + 1):
            # 取消检查
            if cancel_event and cancel_event.is_set():
                logger.info("LLM 流式已被取消（第 %d 轮前）", round_idx)
                raise CancelledError()

            round_parts: list[str] = []
            finish_reason = None

            for attempt in range(_RETRY_COUNT + 1):
                try:
                    _extra: dict = {}
                    if reasoning_effort:
                        # deepseek-v4 思考强度（extra_body 兼容 OpenAI SDK 未声明参数）
                        _extra["reasoning_effort"] = reasoning_effort
                    stream = await self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        stream=True,
                        extra_body=_extra or None,
                    )
                    break
                except Exception as e:
                    if attempt < _RETRY_COUNT:
                        # P1-14：重试退避期间检查取消——用户取消时不再多等 1~3s 退避
                        if cancel_event and cancel_event.is_set():
                            raise CancelledError()
                        logger.warning("LLM 流式连接失败（%d/%d），重试...: %s",
                                      attempt + 1, _RETRY_COUNT, str(e)[:80])
                        await asyncio.sleep(1.0 * (attempt + 1))
                        if cancel_event and cancel_event.is_set():
                            raise CancelledError()
                    else:
                        raise LLMError(
                            f"LLM 调用失败: {str(e)[:200]}",
                            provider=self.provider, model=self.model,
                        ) from e

            first_chunk = True
            async for chunk in stream:
                if first_chunk:
                    logger.info("LLM 首个 chunk 到达: provider=%s model=%s",
                               self.provider, self.model)
                    first_chunk = False
                # 每个 chunk 间检查取消
                if cancel_event and cancel_event.is_set():
                    logger.info("LLM 流式已被取消（第 %d 轮中，已生成 %d chars）",
                               round_idx, sum(len(p) for p in round_parts))
                    raise CancelledError()

                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                # 思考链（deepseek-v4 reasoning_content）：不混入正文，单独回调供前端展示。
                # 注意：思考 chunk 无 content——若忽略它，anext 仍返回（连接活着），
                # 外层 chunk 间隔超时不会误判（仅"完全无任何 chunk"才超时）
                if delta and getattr(delta, "reasoning_content", None):
                    if on_reasoning is not None:
                        await on_reasoning(delta.reasoning_content)
                    continue
                if delta and delta.content:
                    round_parts.append(delta.content)
                    yield delta.content

                if choice.finish_reason:
                    finish_reason = choice.finish_reason

            total_chars = sum(len(p) for p in round_parts)
            logger.info("LLM 流式响应: %d chars, model=%s, finish=%s, round=%d",
                       total_chars, self.model, finish_reason, round_idx)

            # 续写判断
            if finish_reason != "length" or round_idx >= max_continuations:
                break

            logger.info("LLM 续写第 %d 轮（本轮 %d chars）...", round_idx + 1, total_chars)
            messages.append({"role": "assistant", "content": "".join(round_parts)})
            messages.append({"role": "user", "content": "继续"})
            temperature = max(temperature * 0.8, 0.3)

    async def call(
        self,
        prompt: str, *,
        image_urls: list[str] | None = None,
        document: dict | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.1,
        max_continuations: int = 0,
        cancel_event: asyncio.Event | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        """非流式 LLM 调用——内部收集 stream() 的全部输出。

        process-image 用这个：max_tokens=4096, temperature=0.1, max_continuations=0。
        reasoning_effort：思考强度透传（参考文件摘要等忠实压缩类任务传 'low'，
        避开 DeepSeek 思考模式默认 effort=high 的数十秒静默期）。
        """
        parts: list[str] = []
        async for chunk in self.stream(
            prompt,
            image_urls=image_urls,
            document=document,
            max_tokens=max_tokens,
            temperature=temperature,
            max_continuations=max_continuations,
            cancel_event=cancel_event,
            reasoning_effort=reasoning_effort,
        ):
            parts.append(chunk)
        return "".join(parts)
