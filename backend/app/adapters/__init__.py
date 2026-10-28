from .webpage import WebpageAdapter
from .document import DocumentAdapter
from .text import TextAdapter
from .video import VideoAdapter
from .image import ImageAdapter
from .arxiv import ArxivAdapter
from app.core.registry import AdapterRegistry


def init_adapters():
    """应用启动时注册所有适配器（类，route 时实例化 → 一请求一实例防并发污染），顺序决定路由优先级"""
    AdapterRegistry.register(VideoAdapter)
    # ⚠️ arXiv 必须排在 DocumentAdapter **之前**：`https://arxiv.org/pdf/2401.12345v1.pdf`
    # 这种带 `.pdf` 的形态会被 DocumentAdapter 的 `accept()` 抢走（它按扩展名匹配），
    # 而 `/abs/` 与无扩展名的 `/pdf/` 只有本适配器认。
    AdapterRegistry.register(ArxivAdapter)
    AdapterRegistry.register(DocumentAdapter)   # 文档 URL (.pdf/.docx) 优先于网页
    AdapterRegistry.register(ImageAdapter)      # 图片 URL (.png/.jpg) 优先于网页
    AdapterRegistry.register(WebpageAdapter)
    AdapterRegistry.register(TextAdapter)  # 兜底
