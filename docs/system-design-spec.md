# 系统设计说明书（原始蓝图）

> 状态：原始设计蓝图，部分技术选型已被实际实现取代  
> 最后更新：2026-06-27（设计）/ 2026-07-08（标注）  
> ⚠️ **本文档描述目标架构，实际代码实现见 [CLAUDE.md](../CLAUDE.md)。事实清单见 [STATUS.md](STATUS.md)。**
>
> **实际偏离**: 技术选型从 FunASR/MinerU/PaddleOCR → DashScope/PyMuPDF/Tesseract。引擎实测数据见 [engine-standards.md](../design/engine-standards.md)。

---

# 一、系统定位

## 1.1 一句话

> **把任何来源变成可读、可搜索的文字。要不要进一步加工，用户说了算。**

## 1.2 系统全景

```
用户输入
    │
    ├─ 文件 / 文本 → 直接导入 ──────────────┐
    │                                        │
    └─ 链接 → 获取层 → 下载落地 ────────────┘
                      │
                      ▼
              ┌─ 适配器路由 ─┐
              │ accept(输入)  │
              │ adapt(输入)   │
              └──────┬───────┘
                     ▼
            RawTranscript（核心存储，一等公民）
                     │
      ┌──────────────┼──────────────┐
      ▼              ▼              ▼
  [生成面板]    [次级工具]     [AI 对话]
  整理版原文    事实验证        半屏/全屏
  +总结笔记    提取待办        RAG+ToolCall
  后处理替换    多方对比
```

## 1.3 核心设计原则

> 复杂性不是设计进去的，而是后续插件化长出来的。

| # | 原则 | 说明 |
|---|------|------|
| P1 | 原始转录一等公民 | RawTranscript 是核心存储，Markdown 是按需视图 |
| P2 | 适配器封装差异 | 5 条管道差异封在 adapt() 内部，调度层不关心 |
| P3 | 每层只做一件事 | 获取层不关心转录，转录层不关心生成 |
| P4 | 默认统一，差分后置 | 生成面板默认一致，特有选项在"更多"里按来源展开 |
| P5 | 用户决策点前置 | 用多少引擎（转录选项）、显示什么（生成选项）——用户决定 |
| P6 | 中间态保留 | RawTranscript 是底片，Markdown 可换风格重新冲印 |

---

# 二、五条标准化管道

## 2.1 管道总表

| 管道 | 覆盖场景 | 获取 | 转录引擎 | RawTranscript 内容 |
|------|---------|------|---------|-------------------|
| 网页 | 1 文章、5 验证、6 对比 | yt-dlp / HTTP | trafilatura | 正文 Markdown |
| 视频 | 2 视频教程 | yt-dlp / Douyin | FunASR + Qwen-VL + ffmpeg | 时间轴 JSON |
| 文档 | 3 PDF/图表 | 无（直接导入） | MinerU / MarkItDown / PyMuPDF | 结构化文本 + 表格 + 公式 |
| 文本 | 4 想法、7 聊天、8 外文 | 无（直接导入） | 无（类型识别） | 原文本 |
| 图像 | 10 手写/截图 | 无（直接导入） | PaddleOCR + Qwen-VL | OCR 文字 + 画面描述 |

## 2.2 获取层：两条路径

```
用户输入
    │
    ├─ 文件 / 文本 → 直接导入 → 跳过获取层 → 进转录
    │
    └─ URL → 间接导入 → 获取层下载 → 落地 → 进转录
```

获取层双引擎：yt-dlp（国际平台）+ Douyin_TikTok_Download_API（国内短视频）。

### 获取层配置（参照 BiliNote）

```yaml
fetch:
  proxy: "http://127.0.0.1:7890"    # 全局代理，作用于 LLM + yt-dlp 下载
  cookies:                           # 平台 cookie 原始字符串（非文件路径）
    bilibili: "SESSDATA=xxx; bili_jct=yyy"
```

| 配置项 | 类型 | 作用 |
|------|------|------|
| proxy | 全局 URL | 国内访问 YouTube/GitHub 需要 |
| cookies | 按平台的原始字符串 | B站高清/字幕需要登录 cookie |

**Cookie 处理流程（参照 BiliNote `_get_cookiefile`）：**
```
用户粘贴 cookie 字符串 → 转 Netscape 格式临时文件 → yt-dlp --cookies → 用后删除
```

**为什么 cookie 存字符串不是文件路径：**
- 用户从浏览器复制的是字符串，让用户去创建文件是无谓的摩擦
- 系统内部转 Netscape 格式是几行代码的事

## 2.3 视频管道（最复杂的参考）

```
获取: yt-dlp/Douyin → video.mp4 + subtitle.vtt + meta.json
预处理: ffmpeg → audio.wav + frames/*.png
转录（并行）: ASR → transcript.json  +  VL → frame_descriptions.json
时间对齐: → aligned_transcript.json
```

降级模式：完整（ASR+VL+字幕）→ 仅音频+截图（无VL）→ 仅音频。

**字幕优先策略（参照 BiliNote）：**

```
视频 URL
  |
  +-- 1. 先拉平台字幕（B站 player API / YouTube InnerTube API）
  |      +-- 成功 -> 跳过音频下载（除非需要 VL 截图）
  |      +-- 失败/不存在 -> 2
  |
  +-- 2. 下载音频 -> faster-whisper ASR
```

字幕优先的好处：省 ASR 成本（API/GPU）、省下载带宽、速度快。

---

# 三、适配器协议

## 3.1 协议定义

```typescript
// 一个管道适配器
interface PipelineAdapter {
  // 能处理这个输入吗？
  accept(input: PipelineInput): boolean

  // 处理 → 产出原始转录
  adapt(input: PipelineInput, onProgress: ProgressCallback): Promise<AdapterOutput>
}

type PipelineInput = {
  type: "url" | "file" | "text" | "image"
  url?: string
  filePath?: string
  text?: string
  mimeType?: string
}

type AdapterOutput = {
  rawText: string
  metadata: {
    engine: string
    sourceType: "video" | "audio" | "pdf" | "docx" | "epub" | "webpage" | "chat" | "image" | "text"
    confidence?: number
    language?: string
    duration?: number
    pages?: number
    warnings?: string[]
  }
}

type ProgressCallback = (event: ProgressEvent) => void

type ProgressEvent = {
  phase: string       // "download" | "transcribe" | "ocr" | "describe" | ...
  status: string      // "running" | "done" | "error"
  percent?: number
  message?: string
}
```

## 3.2 设计要点

| 做 | 不做 |
|----|------|
| accept + adapt 两方法 | 统一内部步骤 |
| 进度统一上报 onProgress | 统一 rawText 格式 |
| 差异封在 adapt 内部 | 适配器间互相感知 |

**视频适配器 40 行，文本适配器 3 行——调度层不关心。**

## 3.3 进度上报 → SSE

适配器的 `onProgress` 回调直连 SSE（Server-Sent Events），不引入中间文件或轮询：

```
适配器内部:                      前端:
  onProgress({                    EventSource 接收:
    phase: "download",              {phase: "download", status: "running", percent: 45}
    status: "running",
    percent: 45
  })
  onProgress({phase: "download", status: "done"})
  onProgress({phase: "transcribe", status: "running", message: "转写中..."})
  ...
```

| 对比 | BiliNote（文件轮询） | 我们（SSE） |
|------|------|------|
| 机制 | 写 status.json → 前端 GET 轮询 | 一次 SSE 连接，服务端 push |
| 延迟 | 轮询间隔内不可见 | 实时 |
| 粒度 | 阶段级别 | 阶段 + 百分比 + 消息 |
| 实现 | 文件 I/O + HTTP GET | FastAPI StreamingResponse |

## 3.4 路由

```typescript
const adapters = [
  videoAdapter,     // URL + 视频平台
  webpageAdapter,   // URL + 网页
  documentAdapter,  // 文件 + 文档格式
  imageAdapter,     // 文件/粘贴 + 图片格式
  textAdapter,      // 粘贴文本（兜底）
]
// 第一个 accept 返回 true 的接管
```

### 3.5 适配器注册表（可维护性）

参照 BiliNote 转写器注册模式：字典 + 单例懒加载。

```python
# adapters/registry.py

class AdapterRegistry:
    """适配器注册表 — 新管道只需 register() 即生效"""

    _adapters: list[PipelineAdapter] = []

    @classmethod
    def register(cls, adapter: PipelineAdapter):
        """注册一个适配器。排列顺序即匹配优先级。"""
        cls._adapters.append(adapter)

    @classmethod
    def route(cls, input: PipelineInput) -> PipelineAdapter | None:
        """找到第一个能处理的适配器"""
        for adapter in cls._adapters:
            if adapter.accept(input):
                return adapter
        return None

    @classmethod
    def list_all(cls) -> list[PipelineAdapter]:
        return cls._adapters


# --- 注册（应用启动时执行一次）---

AdapterRegistry.register(VideoAdapter())
AdapterRegistry.register(WebpageAdapter())
AdapterRegistry.register(DocumentAdapter())
AdapterRegistry.register(ImageAdapter())
AdapterRegistry.register(TextAdapter())        # 兜底，放最后


# --- 使用 ---

adapter = AdapterRegistry.route(input)
if adapter:
    result = await adapter.adapt(input, on_progress)
```

**新增管道只需三步，不改任何已有代码：**

```python
# 1. 实现协议
class AudioAdapter(PipelineAdapter):
    def accept(self, input): ...
    def adapt(self, input, on_progress): ...

# 2. 注册（一行）
AdapterRegistry.register(AudioAdapter())

# 3. 重启应用
```

**以后插件化加载（v2，预留）：**

```python
# 扫描 plugins/ 目录，自动发现 PipelineAdapter 子类
for plugin_dir in Path("plugins").iterdir():
    module = importlib.import_module(f"plugins.{plugin_dir.name}")
    for obj in module.__dict__.values():
        if isinstance(obj, PipelineAdapter):
            AdapterRegistry.register(obj)
```

### 3.6 引擎注册表

适配器内部可有多引擎可选。每引擎声明 fallback，失败自动降级。

```python
# engines/registry.py
from abc import ABC, abstractmethod

class Engine(ABC):
    name: str
    fallback: "Engine" | None = None   # 降级链上的下一个

    @abstractmethod
    def execute(self, *args, **kwargs): ...

    def run(self, *args, **kwargs):
        """带降级的执行"""
        try:
            return self.execute(*args, **kwargs)
        except Exception as e:
            if self.fallback:
                logger.warning(f"{self.name} failed, -> {self.fallback.name}")
                return self.fallback.run(*args, **kwargs)
            raise

# 各类型引擎的抽象接口
class ASREngine(Engine):
    @abstractmethod
    def transcribe(self, audio: str) -> TranscriptResult: ...

class OCREngine(Engine):
    @abstractmethod
    def recognize(self, image: str) -> OCRResult: ...

class DocEngine(Engine):
    @abstractmethod
    def parse(self, file: str) -> str: ...


# --- 注册：字典 + 降级链 ---

asr_engines = {
    "faster-whisper-small":  FasterWhisperEngine("small"),
    "faster-whisper-medium": FasterWhisperEngine("medium"),
    "groq":                  GroqASREngine(),
}
asr_engines["faster-whisper-small"].fallback = None   # 无备选→降级为字幕
asr_engines["groq"].fallback = asr_engines["faster-whisper-small"]

ocr_engines = {
    "paddleocr": PaddleOCREngine(),
    "trocr":     TrOCREngine(),
}
ocr_engines["paddleocr"].fallback = ocr_engines["trocr"]


# --- 使用 ---

engine = asr_engines[user_config.get("asr_engine", "faster-whisper-small")]
result = engine.transcribe(audio_path)  # 失败自动沿链降级
```

**新增引擎两步：**

```python
# 1. 实现抽象接口
class MyASR(ASREngine):
    name = "my-asr"
    def transcribe(self, audio): ...

# 2. 注册一行
asr_engines["my-asr"] = MyASR()
```

| 对比 | BiliNote | 我们 |
|------|---------|------|
| 注册方式 | 字典 + 单例懒加载 | 同款 |
| 获取引擎 | `get_transcriber(type)` | `engines[type]` |
| 降级链 | 无，失败抛异常 | **有**，自动沿链降级 |
| 多类型 | ASR 一种 | ASR / OCR / Doc 各一套字典 |

### 3.7 能力发现端点（扩展性）

前端不硬编码管道/引擎/选项列表。后端暴露能力端点，前端动态渲染。

```
GET /api/capabilities → {
  "adapters": ["webpage","document","text","video","image"],
  "engines":  {"asr":["faster-whisper-small","faster-whisper-medium"],
               "doc":["pymupdf","markitdown"]},
  "styles":   ["minimal","detailed","academic","life"],
  "genOptions":{"video":["timestamps","visualDescriptions",...], "chat":[...]},
  "version":  "0.1.0"
}
```

**效果：**

| 操作 | 之前 | 之后 |
|------|------|------|
| 新增管道 | 后端注册 + 前端手动加 UI | 后端注册即可，前端自动出现 |
| 新增引擎 | 前后端各改一处 | 后端注册即可，设置页自动显示 |
| 新增生成选项 | `MORE_OPTIONS` + GenPanel 各加 | 后端加一行，前端自动渲染 |

**前端初始化流程：**

```
App 启动 → GET /api/capabilities
  → adapters: 动态构建导入栏支持格式列表
  → engines: 动态渲染设置页引擎选择
  → styles: 动态渲染风格按钮
  → genOptions: 根据 sourceType 动态渲染更多选项
```

---

# 四、核心存储

## 4.1 RawTranscript（一等公民）

```typescript
type RawTranscript = {
  // 核心标识
  id: string
  title: string                    // 用户可见标题（从文件/网页提取，或用户命名）

  // 来源追踪
  sourceType: SourceType           // "video" | "audio" | "pdf" | "docx" | "epub" | "webpage" | "chat" | "image" | "text"
  sourceUrl?: string               // 原始 URL（间接导入时有）
  sourceFileName?: string          // 原始文件名（直接导入时有）

  // 转录引擎
  engine: string                   // "FunASR+Qwen-VL" | "MinerU" | "trafilatura" | "Surya+Qwen-VL" | "none" | ...
  engineVersion?: string           // 引擎版本（排查用）

  // 核心内容
  rawText: string                  // 原始转录内容（引擎产出的纯文本 / JSON）
  contentHash: string              // SHA256(rawText)，去重校验

  // 元数据（按来源差分，引擎填什么是什么）
  metadata: RawMetadata

  // 用户标签
  tags: string[]                   // 用户打的标签，v1 手动

  // 时间戳
  createdAt: string                // ISO 8601
}

// 元数据 — 公共字段 + 按来源的特有字段
type RawMetadata = {
  // 公共
  language?: string                // 检测到的语言（"zh","en","ja",...）
  confidence?: number              // 平均置信度 (0-1)
  warnings?: string[]              // 降级、引擎不可用等运行时警告

  // 音视频
  duration?: number                // 秒
  resolution?: string              // "1920x1080"
  subtitleLanguage?: string        // 内置字幕语言

  // 文档
  pages?: number                   // 页数
  hasTables?: boolean              // 含表格
  hasFormulas?: boolean            // 含公式
  isScanned?: boolean              // 扫描版 vs 电子版

  // 网页
  domain?: string                  // "example.com"
  publishDate?: string             // 文章发布日期

  // 聊天
  platform?: string                // "wechat" | "slack" | ...
  participantCount?: number
  messageCount?: number

  // 图像
  imageDimensions?: string         // "3024x4032"
  imageFormat?: string             // "png" | "jpg" | ...
}
```

**rawText 格式由引擎决定，不在协议层约束：**

| sourceType | rawText 典型格式 |
|-----------|-----------------|
| video/audio | JSON: `{timeline: [{start, end, text, frame_desc?}, ...]}` |
| pdf/docx/epub | Markdown 或 JSON: `{pages: [{blocks: [{type, text, bbox}]}]}` |
| webpage | Markdown（trafilatura 提取的正文） |
| chat | JSON: `{messages: [{time, speaker, text}, ...]}` |
| image | JSON: `{ocr: [{text, bbox}], visualDesc: "..."}` |
| text | 原文本（不过引擎） |

**这是用户存的。不是中间缓存。**

## 4.2 Markdown 视图（按需生成）

```typescript
type MarkdownView = {
  // 核心标识
  id: string
  transcriptId: string            // → RawTranscript.id

  // 生成参数（完整记录，方便重新生成时复现）
  style: "minimal" | "detailed" | "academic" | "life"
  mode: "polished" | "notes" | "both"  // 生成模式
  options: {
    toc: boolean                  // 是否包含目录
    summary: boolean              // 是否包含 ## AI 总结
    keywords: boolean             // 是否包含关键词
    moreOptions: string[]         // 勾了的更多选项
    factCheck: boolean            // 是否包含 ## 事实验证
    screenshotReplace: boolean    // 是否替换截图占位符
    linkReplace: boolean          // 是否替换视频跳转链接
  }

  // 生成结果
  content: string                 // 完整 Markdown（正文+目录+总结+关键词+事实验证）

  // 生成追踪
  generationModel: string         // "claude-sonnet-4-6" | "gpt-4o" | ...
  version: number                 // 第几次生成（同 transcriptId 递增）

  // 时间戳
  createdAt: string               // ISO 8601
}
```

**Markdown 是 RawTranscript 的衍生品。换风格 = 从 RawTranscript 重新生成，version +1。**

## 4.3 用户偏好（轻量，不是 DSL）

```typescript
type UserPreferences = {
  defaults: {
    style: string                 // "detailed"
    format: string[]              // ["toc", "summary"]
  }
  perSource: Record<string, {    // key = SourceType
    style?: string
    moreOptions?: string[]
  }>
}
```

存储为 `~/.limo/preferences.yaml`。打开生成面板时预设，用户随时可改。没有条件表达式，没有自动触发。

## 4.4 不存什么

| 不存 | 原因 |
|------|------|
| 原始文件（mp4/pdf/docx） | 体积大，转录已有完整信息 |
| 系统元信息随内容导出 | engine/confidence 可见但不出现在导出文件里 |
| Concept / Claim / Graph | v1 不做，以后插件化长出来 |

## 4.5 存储布局

v1 单用户桌面应用，不需要数据库。文件系统 + JSON 索引够用。

```
{data_dir}/
├── index.json                         # 所有 RawTranscript 的索引
├── transcripts/{id[:2]}/{id}.json     # RawTranscript.rawText + metadata
├── markdown/{id[:2]}/{id}.md          # MarkdownView.content
├── cache/{hash[:2]}/{hash}.json       # 转录缓存，按 contentHash 复用
└── preferences.yaml                   # 用户偏好
```

### 4.5.1 为什么不用 SQLite

| | BiliNote | 我们 v1 |
|------|---------|---------|
| 用户 | 多用户 Web 服务 | 单用户桌面应用 |
| 并发 | 需要事务 | 无并发写 |
| 查询 | 按平台/时间/model 多维度 | 按 tag/时间 筛选，in-memory 够快 |
| 部署 | FastAPI + SQLAlchemy | Python 单进程 |
| 方案 | SQLite | **文件系统 + JSON 索引** |

以后需要多用户/全文检索时，加一层 DAO 抽象，底层切 SQLite/Postgres，对外透明。

### 4.5.2 索引文件

```json
// index.json — 启动时读到内存，写操作时覆盖
[
  {
    "id": "tr_abc123",
    "title": "RAG架构设计要点",
    "sourceType": "video",
    "sourceUrl": "https://...",
    "engine": "FunASR+Qwen-VL",
    "language": "zh",
    "duration": 482,
    "contentHash": "a1b2c3...",
    "tags": ["RAG", "教程"],
    "createdAt": "2026-06-27T10:00:00Z"
  }
]
```

| 操作 | 方式 |
|------|------|
| 列表/搜索 | 遍历 in-memory list，按 tag/时间/sourceType 过滤 |
| 新增 | append → 写文件 |
| 删除 | 标记 `deleted: true`，不删物理文件 |

### 4.5.3 文件路径

| 数据 | 路径 | 格式 |
|------|------|------|
| RawTranscript | `transcripts/tr/tr_abc123.json` | JSON（rawText + metadata） |
| MarkdownView | `markdown/md/md_abc123.md` | 纯 Markdown |
| 转录缓存 | `cache/a1/a1b2c3....json` | JSON（按 contentHash 扁） |

前两位 hash/id 分桶，避免单目录文件过多。

### 4.5.4 缓存复用

```
导入 → 适配器输出 rawText → SHA256 → contentHash
                                          │
                           cache/{hash[:2]}/{hash}.json 存在？
                                          │
                              ┌───────────┴───────────┐
                              ▼                       ▼
                          命中 → 复用缓存           未命中 → 存储 + 写缓存
```

**按 contentHash 不是 URL/video_id：** 同一视频不同 URL 可复用；同一 URL 内容更新后自动重新转录。

### 4.5.5 去重

两层，都在入库前：

```
URL 去重:  index.json 中查 sourceUrl
            → 存在 → 提示"已导入过《xxx》"，用户选跳过/强制重新导入

内容去重:  index.json 中查 contentHash
            → 存在 → 提示"内容与已有《yyy》完全相同"，不创建重复条目
```

---

# 五、消费层

## 5.1 生成面板（默认入口）

```
RawTranscript → 点击 → 生成面板
                         │
                         ├─ 整理版原文（默认 ☑）:
                         │   去噪音+修正错误+合并段落+小标题
                         │   保留完整信息，不是压缩
                         │
                         ├─ 总结笔记（☐ 勾选）:
                         │   结构化 Markdown + 目录 + AI总结 + 关键词
                         │
                         ├─ 风格（单选）:
                         │   ◉ 精简  ○ 详细  ○ 学术  ○ 生活
                         │
                         ├─ [更多选项] → 按来源差分
                         │
                         └─ [生成] → 两次 LLM 并行调用
                         │          ├─ POLISH_PROMPT → 整理版原文
                         │          └─ BASE_PROMPT   → 总结笔记
                         └─ 拼接为一个 Markdown（整理版在前，总结笔记在后）
```

**参照 BiliNote NoteResult：**

```
BiliNote:  transcript ─┬─ POLISH_PROMPT → polished_transcript（整理版原文）
                        └─ BASE_PROMPT   → markdown（总结笔记+目录+总结+关键词）

我们:      RawTranscript ─┬─ POLISH_PROMPT → 整理版原文
                          └─ BASE_PROMPT   → 总结笔记（目录+总结+关键词）
```

| 产物 | 做什么 | 什么时候用 |
|------|--------|----------|
| 整理版原文 | 去噪音、修正错别字、合并段落、保留完整叙事 | 用户想读内容本身 |
| 总结笔记 | 结构化+目录+AI总结+关键词 | 快速浏览、思维导图、搜索定位 |

两者从同一个 RawTranscript 出发，独立 prompt，并行调用。总结笔记可以单独勾选目录/总结/关键词。

**事实验证——不能合并。** Agent 多轮搜索，独立第三步：
```
Step 1+2: LLM 并行 → 整理版原文 + 总结笔记 → 拼接
Step 3:   ☑ 事实验证 → Agent 多轮搜索 → 结果拼接在 Markdown 末尾
```

事实验证的 Agent 模式（参照 BiliNote Chat Tool Calling）：

```
LLM 带着 search(query) + fetch(url) 两个工具，最多 3 轮:
  Round 1: search → 读来源 → 不够再过一轮
  Round 2: search 换关键词 → 交叉验证
  → 产出 ## 事实验证 表格（声明 | 可信度 | 来源）
```

验证结果嵌入 Markdown，不是独立对象。

### 5.1.1 生成层内部：选项 → Prompt → LLM

生成面板的勾选通过 prompt 片段拼装实现一次 LLM 调用全出。详情见 [管道设计§十.3](pipeline-design.md#十3-生成层内部选项--prompt--llm)。

**核心机制：**

```
面板勾选 → GPTSource → 两个独立 Prompt → 两次 LLM 并行调用

整理版原文 = POLISH_PROMPT → 去噪音+修正+合并+小标题+保留完整信息
总结笔记   = BASE_PROMPT   → 结构化+目录+AI总结+关键词（可选）
两者并行，从同一个 RawTranscript 出发，结果拼接为一个 Markdown
```

**三个关键机制：**

| 机制 | 做什么 | 参照 BiliNote |
|------|--------|--------------|
| Prompt 片段拼装 | format[] + style + moreOptions → 完整 system prompt | `generate_base_prompt()` + `prompt_builder.py` |
| Chunk + Merge | 超长文本按语义边界分段，逐段 LLM，末端合并 | `RequestChunker` + `merge_prompt()` |
| Checkpoint | 每段写入临时文件，中断后从断点续跑 | `checkpoint_key` 机制 |

**与适配器层的边界：** 适配器层产出 RawTranscript（用什么引擎转录），生成层产出 Markdown（怎么展示）。两层独立——转录决定数据，生成决定显示。

### 5.1.2 后期处理：截图替换 + 链接替换

> 参照 BiliNote `_post_process_markdown` + `_insert_screenshots` + `replace_content_markers`

LLM 生成时使用占位符标记，生成后由后处理步骤替换为真实资源。

**占位符协议：**

```
LLM 产出:                       后处理替换为:
*Screenshot-01:23              → ![](screenshot_url)
  关键画面出现时标记                    ffmpeg 截取对应时间帧

Content-03:45                  → [▶ 03:45](video_url?t=225)
  引用具体操作时标记                    按平台生成可点击跳转链接
```

**流程：**
```
LLM 生成（含占位符）→ 扫描提取 → ffmpeg截图/链接拼接 → 替换 → 最终 Markdown
```

**设计要点：**
- LLM 只管占位符，不关心资源如何生成
- 后处理失败不影响正文（截图生成失败就保留占位符）
- 链接模板按平台差分（B站/YouTube/抖音格式不同）
- 视频专属——非视频来源不触发此步骤

## 5.2 更多选项（按来源差分，默认折叠）

| 来源 | 特有选项 |
|------|---------|
| 视频 | 保留时间戳链接 / 保留画面描述 / 一字不差字幕 |
| 聊天记录 | 保留说话人 / 保留时间戳 |
| PDF/文档 | 表格独立导出 / 保留公式编号 |
| 外文 | 附原文对照 |
| 网页/想法/手写 | 无 |

## 5.3 转录选项（导入时决定，和生成两层独立）

| 来源 | 转录选项 |
|------|---------|
| 视频 | 完整（ASR+VL）/ 仅音频+截图 / 仅音频 |
| PDF | 快速（电子版直读）/ 完整（OCR+VLM兜底） |
| 图像 | 文字+画面 / 仅文字 / 仅画面 |

**转录层决定 RawTranscript 有什么数据。生成层决定 Markdown 显示什么。** 转录层没跑 VL → 生成层"保留画面描述"灰掉。

## 5.4 次级工具（独立调用，不在生成面板）

| 工具 | 触发 | 输入 | 备注 |
|------|------|------|------|
| 提取待办 | 聊天记录自动提示 | RawTranscript | 独立 LLM 调用 |
| 多方对比 | 勾选多篇 | 多篇 RawTranscript | 独立 LLM 调用，并排展示 |
| AI 对话 | 独立面板（半屏/全屏） | 选中文档 + 问题 | RAG + Tool Calling |

事实验证已提升为生成面板选项（见 5.1）。

## 5.5 AI 对话（参照 BiliNote Chat Service）

### 三种模式

```
单篇:  打开一篇 → 提问，逐轮深入（BiliNote 同款）
多篇:  勾选多篇 → 跨文档提问，自动标注来源
动态:  对话中 @文档标题 或 #标签 → 随时引入新篇到当前讨论
```

**对话中途加文档：**

```
用户: "RAG的检索延迟是多少"
  AI: [查已选文档] 找到A提到200ms

用户: "@B 也说一下"          ← 动态引入文档B
  AI: [检索B] B里说是150ms，和A的200ms有差异

用户: "#教程 有没有最佳实践"   ← 按标签批量引入
  AI: [检索所有"教程"标签的文档] ...
```

### 实现

参照 BiliNote 的 `chat_service.py`，但泛化为多文档：

```
初始:  用户勾选文档 → 向量检索所有已选文档 → 拼 context
       system prompt: "你是知识库问答助手。以下是你可检索的文档列表..."

Tools: lookup_document(id, query)   — 深入查某篇原文/笔记
       search_documents(query)      — 跨所有文档检索
       list_documents()             — 列出当前会话可用的文档
       
循环:  RAG 初始检索 → LLM + Tools → 最多3轮 → 返回答案
```

### 面板布局

```
┌─ 知识库对话 ──────────────────────────────────────┐
│                                                     │
│  已选: [A: RAG设计] [B: 向量检索] [+添加]           │
│        ← 可随时增删，对话即时生效                     │
│                                                     │
│  ┌─ 对话区 ───────────────────────────────────────┐ │
│  │  用户: 这两篇的检索方案有什么不同？              │ │
│  │  AI:   A采用BM25+向量混合检索，B只用了纯向量...  │ │
│  │        来源: [A §3.2] [B §1.1]                  │ │
│  │                                                 │ │
│  │  用户: @C 里面有提到倒排索引吗                    │ │
│  │  AI:   [检索C] C中§4.1讨论了倒排索引...          │ │
│  └─────────────────────────────────────────────────┘ │
│  [提问]                                    [半屏◱]  │
└─────────────────────────────────────────────────────┘
```

| 机制 | 单篇 | 多篇 | 动态 |
|------|:--:|:--:|:--:|
| 初始上下文 | 当前文档 | 全部勾选文档 | 已有+新引入 |
| 检索范围 | 一个 transcriptId | 多个 transcriptId | 动态扩展 |
| Tool: lookup | 直接查 | 指定文档ID查 | 同左 |
| Tool: search | 单个文档内搜索 | 跨文档检索 | 当前范围 |
| 动态引入 | — | — | @文档名 / #标签 |

## 5.6 展示

- Markdown 预览（ReactMarkdown 渲染）
- 思维导图视图（markmap，Markdown 标题树 → SVG，零额外成本）
- RawTranscript 原始转录可展开
- 导出：MD 下载 / PDF / HTML / SVG / PNG / XMind

## 5.7 用户偏好（不是 DSL）

记住用户的习惯。打开生成面板时预设，不是自动化规则。

```yaml
# ~/.limo/preferences.yaml
defaults:
  style: detailed
  format: [toc, summary]

perSource:
  video:
    style: detailed
    moreOptions: [timestamps]
  pdf:
    style: academic
    moreOptions: [formulaNumbers]
  chat:
    moreOptions: [speakers, timestamps]
  foreign:
    moreOptions: [bilingual]
```

| 原则 | 说明 |
|------|------|
| 不叫 DSL | 没有条件表达式、没有规则引擎。就是偏好 JSON |
| 不自动触发 | 偏好在面板里预设，用户仍要点"生成" |
| 随时可改 | 面板里改了就是新的偏好 |
| v1 一个文件 | 单用户本地 JSON，不需要 UI 管理界面 |

---

## 5.8 Agent 层（非侵入式）

Agent 层不碰核心链路，只做一件事：**读存储 → 循环 → 返回答**。

```
核心链路（Agent 完全不知道）:
  输入 → 获取 → 适配器 → RawTranscript → 存储

消费者（Agent 是其中之一）:
  RawTranscript ─┬─ 生成面板
                 ├─ AI 对话      ← Agent 循环
                 ├─ 事实验证     ← Agent 循环
                 └─ 多方对比     ← Agent 循环
```

### 循环

```
用户提问 → RAG 粗筛 → LLM 判断
                        ├─ 够回答 → 返回答案
                        └─ 不够   → 调工具 → 结果注入 → loop
                                   最多 3 轮
```

### 工具

> 同一个循环，不同消费方配不同 prompt + tools

| 工具 | 做什么 | 谁用 |
|------|--------|------|
| `lookup_doc(id, query)` | 在指定文档中搜原文 | AI对话 / 多方对比 |
| `search_docs(query)` | 跨已选文档检索 | AI对话 / 多方对比 |
| `list_docs()` | 列出会话可用文档 | AI对话 |
| `search_web(query)` | 外部搜索 | 事实验证 |
| `fetch_url(url)` | 抓取网页全文 | 事实验证 |

### 产物

| 消费方 | 产物格式 |
|------|------|
| AI 对话 | 自然语言 + `[来源: 标题 §章节]` |
| 事实验证 | 表格 `| 声明 | 可信度 | 来源URL |` |
| 多方对比 | 并排对比 + `⚡ 冲突` 标注，不代替用户判断 |

### 设计原则

| 原则 | 说明 |
|------|------|
| 非侵入 | 核心链路、适配器、存储层不知道 Agent 存在 |
| 只读 | Agent 不改写 RawTranscript 或 Markdown |
| 扁平 | 不区分"单篇/多篇/动态"模式——只是检索范围不同 |
| 通用 | 同一个循环，换 prompt + tools 即换场景 |

---

# 六、明确不在 v1

| 不做 | 原因 |
|------|------|
| Concept / Claim / Graph | 以后插件化长出来 |
| 自动知识图谱 | 不自动关联跨文档 |
| 自动删除"低质量" | 用户判断价值 |
| 主动提醒/推送 | 用户触发系统 |
| 跨文档自动搜索范围 | 场景 9b 靠用户手动选 |
| 多人协作 | v1 单用户 |
| 微信自动抓取 | 用户粘贴 |
| 视频号 | 端内加密流 |

---

# 七、技术选型要点

| 层 | 方案 |
|------|------|
| 获取-国际 | yt-dlp |
| 获取-国内短视频 | Douyin_TikTok_Download_API |
| 网页正文 | trafilatura（Python）或 readability（Node） |
| 视频 ASR | FunASR（中文）/ faster-whisper（多语言） |
| 视频 VL | Qwen-VL（本地）/ GPT-4o（云端） |
| PDF 电子版 | PyMuPDF 直读 |
| PDF 扫描版 | MinerU / PaddleOCR + VLM 兜底 |
| 办公文档 | MarkItDown / MinerU |
| 图像 OCR | PaddleOCR v5（中文首选）/ TrOCR（手写备选） |
| LLM | DeepSeek / Claude / GPT |

### 7.1 打包策略：安装时差分

用户按需勾选能力，不下一堆用不着的。

```
安装时:
  ☑ 核心（必装）            ~80MB     Python运行时 + openai + httpx + yaml
  ☐ 网页处理                +5MB      trafilatura
  ☐ 文档处理 (PDF/Office)   +30MB     PyMuPDF + MarkItDown
  ☐ 图像 OCR               +250MB    PaddleOCR + paddlepaddle
  ☐ 视频 ASR                ── 模型首次运行时下载 ──
       faster-whisper small  +464MB    英文为主
       faster-whisper medium +1.5GB    中英均衡
       FunASR                +1.2GB    中文最强
  ☐ ffmpeg                  +50MB     视频处理必需
```

| 配置 | 安装体积 | 场景覆盖 |
|------|---------|---------|
| 核心 | 80MB | 纯文本粘贴 |
| +网页 | 85MB | +场景1/5/6/8 |
| +文档+网页 | 115MB | +场景3 |
| +OCR+文档+网页 | 365MB | +场景10 |
| +ASR+ffmpeg+全部 | ~830MB | +场景2（首次跑下载模型+464MB） |
| 全装 | ~2GB | 全部场景 |

**原则：**
- 安装器控制在 100-300MB（不含模型）
- 大模型（ASR 占 80%）首次使用时自动下载，不打包
- 用户只装自己需要的管道

---

# 八、健壮性设计

## 8.1 错误分层

每层独立的异常类，不透传底层错误到上层。

```
┌─ AppError（基类）──────────────────────────────────┐
│  code: str        # 机器可读，如 "FETCH_COOKIE_EXPIRED"
│  message: str     # 用户可读，中文
│  detail: str      # 调试用，原始异常信息
│  recoverable: bool # 用户能否自行恢复
└────────────────────────────────────────────────────┘
         │
    ┌────┴──────────┬──────────────┬──────────────┐
    ▼               ▼              ▼              ▼
FetchError     TranscribeError  GenerateError   StoreError
  获取层          转录层          生成层          存储层
```

**各层异常：**

| 层 | 典型异常 | recoverable | 用户看到 |
|------|---------|:--:|------|
| 获取 | `FETCH_NETWORK` | ✅ | "无法访问 <url>，检查链接或网络" |
| 获取 | `FETCH_COOKIE_EXPIRED` | ✅ | "B站登录已过期，请重新粘贴 Cookie" |
| 获取 | `FETCH_UNSUPPORTED` | ❌ | "xxx 格式暂不支持" |
| 转录 | `TRANS_ASR_OOM` | ✅ | "本地 ASR 内存不足，已降级为纯字幕" |
| 转录 | `TRANS_VL_QUOTA` | ✅ | "视觉识别 API 额度用尽，已跳过画面描述" |
| 转录 | `TRANS_OCR_LOW_CONFIDENCE` | ❌ | "文字识别置信度低，结果仅供参考" |
| 转录 | `TRANS_EMPTY` | ❌ | "正文提取为空，可查看原始文件" |
| 生成 | `GEN_API_TIMEOUT` | ✅ | "AI 服务响应超时，已重试 3 次，建议稍后重试" |
| 生成 | `GEN_TOKEN_OVERFLOW` | ❌ | （自动触发 Chunk+Merge，不报错） |
| 存储 | `STORE_DISK_FULL` | ✅ | "磁盘空间不足，请清理后重试" |
| 存储 | `STORE_INDEX_CORRUPT` | ❌ | （自动遍历目录重建索引） |

## 8.2 降级链

每个引擎声明备选，失败自动降级，不阻断管道。

```
视频 ASR:  platform_subtitle → faster-whisper → (降级) 跳过ASR，warnings+=asr_unavailable
视频 VL:   Qwen-VL API      → (降级) 跳过VL，warnings+=vl_unavailable
PDF:       PyMuPDF 直读      → MarkItDown → (降级) 保存原始文件引用
OCR:       PaddleOCR         → TrOCR      → (降级) 低置信度标记
LLM:       primary api_key   → fallback key → (降级) 返回原始文本
```

**降级信息记录在 RawTranscript.metadata.warnings：**

```json
{
  "warnings": [
    "VL 不可用，画面描述已跳过",
    "ASR 降级为纯字幕模式"
  ]
}
```

前端根据 warnings 决定哪些生成选项灰掉（如 VL 不可用时"保留画面描述"灰掉）。

## 8.3 日志

### 级别

| 级别 | 内容 | 示例 |
|------|------|------|
| ERROR | 用户可感知的失败 | `FETCH_NETWORK: youtube.com 连接超时` |
| WARNING | 降级、重试 | `ASR 降级为字幕模式 (reason: OOM)` |
| INFO | 正常流程节点 | `[task_abc] download done (28s, 62.7MB)` |
| DEBUG | 内部细节 | `Netscape cookie 临时文件: 15 entries` |

### 格式

```
{timestamp} [{level}] {layer}.{module} [{task_id}] {message} {context_json}

示例:
2026-06-27 10:41:31 [INFO] fetch.ytdlp [task_abc] download done {"duration_s": 28, "size_mb": 62.7}
2026-06-27 10:42:10 [WARNING] trans.asr [task_abc] degraded to subtitle {"reason": "model not found"}
2026-06-27 10:43:05 [ERROR] gen.llm [task_abc] API timeout after 3 retries {"model": "deepseek-v4", "attempts": 3}
```

### 存储

```
{data_dir}/logs/
├── app.log             # 当前日志，自动 rotate（10MB × 5）
├── app.log.1
├── ...
└── tasks/              # 每个任务一份完整日志
    └── {task_id}.log   # 从 start 到 success/fail 的完整记录
```

**为什么每任务独立日志：** 用户反馈问题时，直接发 `{task_id}.log` 就行。

## 8.4 调试产物保留

出问题时，保留中间产物供排查：

```
{data_dir}/debug/{task_id}/
├── raw.html            # 下载的原始页面（网页管道）
├── extracted.md        # 正文提取结果
├── transcript.json     # ASR 原始输出
├── frames/             # 抽帧图片
├── prompt.txt           # 发给 LLM 的完整 prompt
└── response.txt         # LLM 原始返回
```

正常完成的任务不保留 debug 产物，失败时自动保留。

## 8.5 原则

| 原则 | 说明 |
|------|------|
| 用户不看到堆栈 | 所有异常转为中文 message |
| 每层独立恢复 | 下载失败不影响已入库的 RawTranscript |
| 数据不丢 | 任何环节崩溃，已完成的部分保留 |
| 可追溯 | task_id 贯穿全链路，日志可回溯每一步 |

---

# 九、设计方法论

详见 [design-methodology.md](design-methodology.md)（14 条判断方法）。

核心几条：
- 从场景反推架构，不先画架构往场景套
- 先区分再决定存不存在
- 砍到找不到下一个可合并的
- 复杂度后置——v1 跑起来后插件长出来
- 中间态保留——RawTranscript 是底片，Markdown 可重新冲印
- 每层只做一件事，不知道其他层在做什么

---

# 十、相关文档

| 文档 | 内容 |
|------|------|
| [用户场景](user-scenarios.md) | 10 场景 + 设计洞察 |
| [技术选型](tech-options.md) | 各层引擎候选 |
| [管道设计](pipeline-design.md) | 5 条管道展开 + 适配器协议 + 生成面板 |
| [复盘方法论](design-methodology.md) | 14 条判断方法 |
| [TODO](TODO.md) | 进度追踪 |
