"""
LLMService — 统一的 LLM 通信层

整个后端只有这一个地方创建 AsyncOpenAI 客户端。
内置：模型解析 · 流式/非流式 · 取消 · 续写 · 多模态检测 · 重试
"""

import asyncio
import logging
from typing import AsyncGenerator

from openai import AsyncOpenAI

from app.core.model_resolver import resolve_model
from app.llm.errors import CancelledError, LLMError

logger = logging.getLogger(__name__)

MAX_TOKENS = 65536
_RETRY_COUNT = 2  # 网络错误重试次数


class LLMService:
    """统一的 LLM 通信层。构造时完成模型解析，后续只用 .stream() / .call()。

    用法:
        llm = LLMService(config, model_id=1417368505)
        async for chunk in llm.stream(prompt, cancel_event=ev):
            yield chunk
    """

    def __init__(self, config: dict, model_id: int | None = None):
        # ── 模型解析（以前三个路由各自调 resolve_model）──
        llm_cfg = dict(config.get("llm", {}))
        if model_id:
            resolved = resolve_model(config, model_id)
            if resolved:
                llm_cfg.update(resolved)

        self.provider = llm_cfg.get("provider", "deepseek")
        self.model = llm_cfg.get("model", {}).get(self.provider, "deepseek-v4-flash")
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
            kwargs = {"api_key": self.api_key, "timeout": 300.0, "max_retries": 0}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    @property
    def supports_vision(self) -> bool:
        return any("image" in (c or "") for c in self.capabilities)

    # ── 核心方法 ──

    async def stream(
        self,
        prompt: str, *,
        image_urls: list[str] | None = None,
        max_tokens: int = MAX_TOKENS,
        temperature: float = 0.7,
        max_continuations: int = 3,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncGenerator[str, None]:
        """流式 LLM 调用，每个 chunk 间检查 cancel_event。

        finish=length 时自动续写（追加"继续"→再请求）。
        """

        # ── 构建消息 ──
        if image_urls and self.supports_vision:
            content: list[dict] = [{"type": "text", "text": prompt}]
            for url in image_urls:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": url, "detail": "auto"},
                })
            messages: list[dict] = [{"role": "user", "content": content}]
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
                    stream = await self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        stream=True,
                    )
                    break
                except Exception as e:
                    if attempt < _RETRY_COUNT:
                        logger.warning("LLM 流式连接失败（%d/%d），重试...: %s",
                                      attempt + 1, _RETRY_COUNT, str(e)[:80])
                        await asyncio.sleep(1.0 * (attempt + 1))
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
                if choice.delta and choice.delta.content:
                    round_parts.append(choice.delta.content)
                    yield choice.delta.content

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
        max_tokens: int = 4096,
        temperature: float = 0.1,
        max_continuations: int = 0,
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        """非流式 LLM 调用——内部收集 stream() 的全部输出。

        process-image 用这个：max_tokens=4096, temperature=0.1, max_continuations=0。
        """
        parts: list[str] = []
        async for chunk in self.stream(
            prompt,
            image_urls=image_urls,
            max_tokens=max_tokens,
            temperature=temperature,
            max_continuations=max_continuations,
            cancel_event=cancel_event,
        ):
            parts.append(chunk)
        return "".join(parts)
