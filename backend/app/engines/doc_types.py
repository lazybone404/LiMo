"""
文档引擎共享类型
"""

from dataclasses import dataclass


@dataclass
class DocResult:
    text: str
    pages: int = 1
    engine: str = ""
    is_scanned: bool = False      # 大部分页面无文字层 → 扫描件
    scanned_pages: int = 0        # 无文字层的页数
