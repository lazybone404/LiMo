"""
ASR 转录结果共享数据类 — 所有 ASR 引擎统一返回类型。

从 asr.py（faster-whisper）抽离，供 bcut / dashscope 复用，
避免 whisper 移除后数据类随文件消失导致 import 断链。
"""

from dataclasses import dataclass


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass
class TranscriptResult:
    language: str
    duration: float
    segments: list[TranscriptSegment]
    full_text: str = ""
