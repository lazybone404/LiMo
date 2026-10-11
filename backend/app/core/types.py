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
    # 多篇合成的对比综述产物（自身也是一篇文档，进库/可搜索/可导出/可被对话引用）
    COMPOSITE = "composite"

# 所有文档类 source_type（prompt 分派、格式选项等共用）
DOC_SOURCE_TYPES: frozenset[str] = frozenset({"pdf", "docx", "pptx", "xlsx", "epub", "document"})

class InputType(StrEnum):
    URL = "url"
    FILE = "file"
    TEXT = "text"
    IMAGE = "image"

class Style(StrEnum):
    PLAIN = "plain"
    MINIMAL = "minimal"
    DETAILED = "detailed"
    ACADEMIC = "academic"
    IMRAD = "imrad"
    LIFE = "life"
    TUTORIAL = "tutorial"
    XIAOHONGSHU = "xiaohongshu"
    BUSINESS = "business"
    # ⚠️ **枚举值必须与 `notes_styles` 的 dict key 同形**（都是 `"meeting"`）。
    # 它曾经是 `MEETING_MINUTES = "meeting_minutes"`，而风格表 key 是 `"meeting"`
    # —— 两形不一致时 `{s.value for s in Style}` 构成的白名单不含 `"meeting"`，
    # 于是前端实际发出的 `"meeting"` 被静默改写成 detailed（用户选「会议纪要」
    # 得到「详细模式」，零报错）。兼容旧值 `meeting_minutes` 由 `STYLE_ALIAS`
    # 在**字符串层**承担——但前提是白名单校验发生在归一**之后**（见
    # `routers/generate.py` 的注释）。
    MEETING = "meeting"
    TASK_ORIENTED = "task_oriented"
    # `"none"` = 前端「无」按钮：**明确不注入风格条款**，是一个真实档位而非「没选」。
    # ⚠️ 它曾长期**不在枚举里**，只在字符串层被 `_normalize_and_validate_style` 显式
    # 放行 —— 于是 `Style("none")` 在**落盘那一刻**抛 `ValueError`，**整条生成流失败**
    # （用户选「无」= 生成不出来），而那个校验函数的单测全绿：它只断言自己的返回值，
    # 从不问这个值能不能被下游消费（模式 #51「能力存在 ≠ 该路径在用它」）。
    # 加进枚举后，写入（`routers/generate.py`）与读回（`routers/markdown_edit.py`）
    # 两个方向都不再需要特判；`notes_styles` 无此 key，取文案处靠 `!= "none"` 跳过。
    NONE = "none"

class GenerateMode(StrEnum):
    POLISHED = "polished"
    NOTES = "notes"
    BOTH = "both"

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
    generation_model: str = ""
    version: int = 1
    status: str = "complete"  # "complete" | "partial" — 取消时保存 partial，续写后变 complete
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
    # assistant 的追问建议（独立第二次调用的产物，**不进 content**——见 agent/followup.py）
    follow_ups: list | None = None    # [str, ...] 追问问题文本

    def to_dict(self) -> dict:
        # ⚠️ **这是显式白名单**：不在下面的字段**在写盘那一刻被丢掉**，而症状与前端
        # 透传点漏一处完全一样（刷新/编辑后卡片消失），排查时会先怀疑前端。
        # 新增扩展字段时**必须**在这里加一行 —— 它与前端 `chatStore.ts::toBackendMsg`
        # 是一对（一个管落盘、一个管 rebuild 回写）。
        d: dict = {"id": self.id, "role": self.role, "content": self.content, "timestamp": self.timestamp}
        if self.doc_refs:
            d["doc_refs"] = self.doc_refs
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        if self.thinking:
            d["thinking"] = self.thinking
        if self.follow_ups:
            d["follow_ups"] = self.follow_ups
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
    # 历史压缩（C3）：早期消息 LLM 摘要 + 已压缩到的消息索引
    summary: str = ""
    last_compress_index: int = 0
    # clearContext（C4）：清除上下文边界（该索引前的消息既不携带也不参与压缩）
    clear_context_at: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id, "title": self.title, "model_id": self.model_id,
            "messages": self.messages, "doc_ids": self.doc_ids,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "summary": self.summary, "last_compress_index": self.last_compress_index,
            "clear_context_at": self.clear_context_at,
        }
