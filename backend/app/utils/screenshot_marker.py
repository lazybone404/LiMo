"""
截图标记解析 — 从 Markdown 中提取 *Screenshot-[mm:ss] 标记
移植自 BiliNote backend/app/utils/screenshot_marker.py
"""
import re
from typing import List, Tuple


# 匹配 LLM 可能输出的各种变体：
# *Screenshot-[02:35]  标准格式
# *Screenshot-[2:35]   个位数分钟
# *Screenshot-02:35    无括号
# *Screenshot-[02:35]* 被 markdown 斜体包裹残留
SCREENSHOT_PATTERN = re.compile(
    r'\*{0,2}'                   # 前导 * 或 **（markdown 强调可能残留）
    r'Screenshot-'
    r'(?:'
    r'\[(\d{1,2}):(\d{1,2})\]'   # 带括号 [mm:ss] 或 [m:ss]
    r'|'
    r'(\d{1,2}):(\d{1,2})'       # 不带括号 mm:ss 或 m:ss
    r')'
    r'\*{0,2}'                   # 尾部可能残留的 * 或 **
)


def extract_screenshot_timestamps(markdown: str) -> List[Tuple[str, int]]:
    """提取所有截图标记，返回 [(完整匹配文本（用于替换）, 总秒数), ...]"""
    results: List[Tuple[str, int]] = []
    for match in SCREENSHOT_PATTERN.finditer(markdown):
        # 解析时间戳：带括号版 (group 1,2) 或不带括号版 (group 3,4)
        mm = match.group(1) or match.group(3)
        ss = match.group(2) or match.group(4)
        total_seconds = int(mm) * 60 + int(ss)
        # 返回完整匹配文本，确保替换时不留残留字符
        results.append((match.group(0), total_seconds))
    return results
