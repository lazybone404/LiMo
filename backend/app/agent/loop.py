"""
Agent 循环 — 非侵入式 Tool Calling
服务 AI 对话 + 事实验证 + 多方对比
参照 system-design-spec.md 五.8

⚠️ DEPRECATED: 已由 app.llm.AgentRunner 替代。
  保留此文件仅用于参考和向后兼容，不推荐在新代码中使用。
"""

import json
import logging
from typing import Any, Callable

from openai import OpenAI

logger = logging.getLogger(__name__)

ToolFn = Callable[..., str]
ToolDef = dict[str, Any]


class Agent:
    """通用 Agent 循环：LLM + Tools，最多 N 轮"""

    def __init__(self, llm_config: dict, max_rounds: int = 3):
        provider = llm_config.get("provider", "deepseek")
        api_key = llm_config.get("api_key", "")
        model = llm_config.get("model", {}).get(provider, "deepseek-v4-flash")
        base_url = llm_config.get("base_url", {}).get(provider)

        client_kwargs = {"api_key": api_key, "timeout": 300.0}
        if base_url:
            client_kwargs["base_url"] = base_url

        self.client = OpenAI(**client_kwargs)
        self.model = model
        self.max_rounds = max_rounds
        self._tools: list[ToolDef] = []
        self._tool_fns: dict[str, ToolFn] = {}

    def register_tool(self, name: str, description: str, parameters: dict, fn: ToolFn):
        self._tools.append({
            "type": "function",
            "function": {"name": name, "description": description, "parameters": parameters},
        })
        self._tool_fns[name] = fn

    async def run(self, system_prompt: str, user_message: str,
                  history: list[dict] | None = None,
    ) -> str:
        """主循环：RAG 粗筛 → LLM + Tools → 返回答案"""
        messages = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history[-20:])
        messages.append({"role": "user", "content": user_message})

        for round_i in range(self.max_rounds):
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=self._tools if self._tools else None,
                temperature=0.7,
            )
            msg = response.choices[0].message

            if not msg.tool_calls:
                return msg.content or ""

            messages.append(msg)
            for tc in msg.tool_calls:
                fn_name = tc.function.name
                try:
                    fn_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    fn_args = {}

                logger.info(f"Tool [{round_i+1}/{self.max_rounds}]: {fn_name}({fn_args})")
                fn = self._tool_fns.get(fn_name)
                result = fn(**fn_args) if fn else json.dumps({"error": f"unknown tool: {fn_name}"})
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

        # 最后一轮不带工具
        response = self.client.chat.completions.create(
            model=self.model, messages=messages, temperature=0.7,
        )
        return response.choices[0].message.content or ""
