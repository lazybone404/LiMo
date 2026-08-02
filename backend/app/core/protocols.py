"""
适配器 + 引擎抽象协议
参照 system-design-spec.md 三 + 八.1
"""

from abc import ABC, abstractmethod
from typing import Callable

from .types import PipelineInput, AdapterOutput, ProgressEvent


# -- 适配器 --

class PipelineAdapter(ABC):
    name: str = ""

    @abstractmethod
    def accept(self, input: PipelineInput) -> bool:
        """能处理这个输入吗？"""
        ...

    @abstractmethod
    async def adapt(self, input: PipelineInput,
                    on_progress: Callable[[ProgressEvent], None] | None = None,
                    engine_config: dict | None = None,
    ) -> AdapterOutput:
        """处理 → 产出 RawTranscript"""
        ...


# -- 引擎 --

class Engine(ABC):
    name: str = ""
    supported_modes: list[str] = []  # 该引擎支持的处理模式，空列表=需运行时判断
    capabilities: list[str] = []  # 引擎能力标签：text / table / image / ocr / office / pdf / asr

    @abstractmethod
    def execute(self, *args, **kwargs):
        """子类实现具体逻辑"""
        ...

    def run(self, *args, **kwargs):
        """执行（无引擎降级链：失败直接抛出，绑定只取第一个是调用方职责）"""
        return self.execute(*args, **kwargs)
