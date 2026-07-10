from .webpage import WebpageAdapter
from .document import DocumentAdapter
from .text import TextAdapter
from .video import VideoAdapter
from .image import ImageAdapter
from app.core.registry import AdapterRegistry


def init_adapters():
    """应用启动时注册所有适配器，顺序决定路由优先级"""
    AdapterRegistry.register(VideoAdapter())
    AdapterRegistry.register(DocumentAdapter())   # 文档 URL (.pdf/.docx) 优先于网页
    AdapterRegistry.register(ImageAdapter())      # 图片 URL (.png/.jpg) 优先于网页
    AdapterRegistry.register(WebpageAdapter())
    AdapterRegistry.register(TextAdapter())  # 兜底
