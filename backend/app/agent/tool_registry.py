"""
工具注册中心 — 参照 Pi 的扩展工具动态注册模式

替代 chat.py 中硬编码的 _register_chat_tools()。
每个工具是独立的 ToolDef，注册后即可被 AgentRunner 发现和调用。
"""

from dataclasses import dataclass, field
from typing import Callable, Any


@dataclass
class ToolDef:
    """工具定义 — 包含 AgentRunner.register_tool 所需的所有信息"""
    name: str
    description: str
    parameters: dict
    fn: Callable[..., str]


class ToolRegistry:
    """工具注册中心。与 store/config 无关，纯工具容器。"""

    def __init__(self):
        self._tools: list[ToolDef] = []

    def register(self, tool: ToolDef) -> None:
        """注册一个工具"""
        self._tools.append(tool)

    def get_defs(self) -> list[dict]:
        """返回 OpenAI 兼容的 tool definitions 列表"""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self._tools
        ]

    def get_fn(self, name: str) -> Callable[..., str] | None:
        """根据名称查找工具函数"""
        for t in self._tools:
            if t.name == name:
                return t.fn
        return None

    def apply_to(self, agent: object) -> None:
        """将所有已注册工具应用到 AgentRunner 实例"""
        for t in self._tools:
            agent.register_tool(t.name, t.description, t.parameters, t.fn)

    @property
    def count(self) -> int:
        return len(self._tools)
