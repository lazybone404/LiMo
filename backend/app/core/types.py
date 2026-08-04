"""
核心数据类型
参照 system-design-spec.md 四
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Optional

# -- Enum --

class SourceType(StrEnum):
    VIDEO = "video"
    AUDIO = "audio"
    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"
    XLSX = "xlsx"
    EPUB = "epub"
    WEBPAGE = "webpage"
    CHAT = "chat"
    IMAGE = "image"
    TEXT = "text"
    DOCUMENT = "document"  # 通用文档兜底

# 所有文档类 source_type（prompt 分派、格式选项等共用）
DOC_SOURCE_TYPES: frozenset[str] = frozenset({"pdf", "docx", "pptx", "xlsx", "epub", "document"})

class InputType(StrEnum):
    URL = "url"
    FILE = "file"
    TEXT = "text"
    IMAGE = "image"

class Style(StrEnum):
    MINIMAL = "minimal"
    DETAILED = "detailed"
    ACADEMIC = "academic"
    IMRAD = "imrad"
    LIFE = "life"
    TUTORIAL = "tutorial"
    XIAOHONGSHU = "xiaohongshu"
    BUSINESS = "business"
    MEETING_MINUTES = "meeting_minutes"
    TASK_ORIENTED = "task_oriented"

class GenerateMode(StrEnum):
    POLISHED = "polished"
    NOTES = "notes"
    BOTH = "both"
    FACT_CHECK = "fact_check"

# -- Pipeline --

@dataclass
class PipelineInput:
    type: InputType
    url: Optional[str] = None
    file_path: Optional[str] = None
    text: Optional[str] = None
    mime_type: Optional[str] = None

@dataclass
class RawMetadata:
    # 公共
    language: Optional[str] = None
    confidence: Optional[float] = None
    warnings: list[str] = field(default_factory=list)
    # 音视频
    duration: Optional[float] = None
    resolution: Optional[str] = None
    subtitle_language: Optional[str] = None
    # 文档
    pages: Optional[int] = None
    has_tables: Optional[bool] = None
    has_formulas: Optional[bool] = None
    is_scanned: Optional[bool] = None
    # 网页
    domain: Optional[str] = None
    publish_date: Optional[str] = None
    # 聊天
    platform: Optional[str] = None
    participant_count: Optional[int] = None
    message_count: Optional[int] = None
    # 图像
    image_dimensions: Optional[str] = None
    image_format: Optional[str] = None
    # 来源元数据（视频平台标题、封面等）
    source_title: Optional[str] = None   # 平台上的原始标题（B站/YouTube API 返回）
    cover_url: Optional[str] = None      # 封面/缩略图 URL
    uploader: Optional[str] = None       # UP主/频道名
    # 扩展
    extra: dict[str, Any] = field(default_factory=dict)

@dataclass
class AdapterOutput:
    raw_text: str
    metadata: RawMetadata
    engine: str  # "FunASR+Qwen-VL" | "trafilatura" | ...

# -- Progress --

@dataclass
class ProgressEvent:
    phase: str       # "download" | "transcribe" | "ocr" | "describe" | "generate"
    status: str      # "running" | "done" | "error"
    percent: Optional[float] = None
    message: Optional[str] = None

# -- Storage --

@dataclass
class RawTranscript:
    id: str
    title: str
    source_type: SourceType
    engine: str
    raw_text: str
    content_hash: str
    metadata: RawMetadata = field(default_factory=RawMetadata)
    source_url: Optional[str] = None
    source_file_name: Optional[str] = None
    source_file_path: Optional[str] = None  # 本地文件在服务端的路径（引用，不复制）
    engine_version: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

@dataclass
class MarkdownView:
    id: str
    transcript_id: str
    content: str
    style: Style = Style.MINIMAL
    mode: GenerateMode = GenerateMode.POLISHED
    options: dict[str, bool] = field(default_factory=lambda: {
        "toc": False, "summary": False, "keywords": False,
        "screenshot_replace": False, "link_replace": False,
    })
    more_options: list[str] = field(default_factory=list)
    fact_check: bool = False
    generation_model: str = ""
    version: int = 1
    status: str = "complete"  # "complete" | "partial" — 取消时保存 partial，续写后变 complete
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class FactCheckClaim:
    """单条事实主张的验证结果"""
    id: str                              # "claim_01"
    text: str                            # 主张原文
    category: str                        # "statistical" | "historical" | "scientific" | "quote" | "general"
    status: str                          # "verified" | "disputed" | "unverifiable" | "inconclusive"
    confidence: float = 0.0              # 0.0 - 1.0
    sources: list[dict] = field(default_factory=list)  # [{title, url, snippet, relevance}]
    analysis: str = ""                   # LLM 推理过程
    correction: str | None = None        # 若 disputed，给出修正文本


@dataclass
class FactCheckResult:
    """完整事实验证报告"""
    id: str                              # "fc_xxxx"
    transcript_id: str                   # 关联的 RawTranscript
    source_content_id: str               # 被核查的 MarkdownView.id
    source_mode: str                     # "notes" | "polished"
    verified_count: int = 0
    disputed_count: int = 0
    unverifiable_count: int = 0
    overall_score: float = 0.0           # 0.0 - 1.0 加权可信度
    claims: list[FactCheckClaim] = field(default_factory=list)
    summary: str = ""                    # LLM 生成的总体评估段落
    content: str = ""                    # 完整 Markdown 报告（供 MarkdownView 存储）
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class ProcessResult:
    """引擎处理结果 — OCR/文档数字化等，对标 MarkdownView"""
    id: str
    transcript_id: str
    mode: str          # extract | table | digitize | understand
    engine: str        # dashscope-ocr
    content: str       # 处理结果文本
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class ChatMessage:
    """对话消息"""
    id: str            # "msg_{uuid12}"
    role: str          # user | assistant | system
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    # 扩展字段（可选，旧数据缺失时前端默认空）：
    doc_refs: list | None = None      # user @ 的文档 / assistant 引用的文档 [{docId,title}]
    tool_calls: list | None = None    # assistant 工具调用气泡 [{tool,display,summary}]
    thinking: str | None = None       # assistant 思考段

    def to_dict(self) -> dict:
        d: dict = {"id": self.id, "role": self.role, "content": self.content, "timestamp": self.timestamp}
        if self.doc_refs:
            d["doc_refs"] = self.doc_refs
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        if self.thinking:
            d["thinking"] = self.thinking
        return d


@dataclass
class Conversation:
    """AI 对话"""
    id: str            # "conv_{uuid12}"
    title: str
    model_id: int | None = None
    messages: list[dict] = field(default_factory=list)
    doc_ids: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return {
            "id": self.id, "title": self.title, "model_id": self.model_id,
            "messages": self.messages, "doc_ids": self.doc_ids,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }
