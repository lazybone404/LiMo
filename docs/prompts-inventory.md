# LiMo 提示词清单

> 完整记录项目中所有 LLM 提示词，供审视和优化。
> 生成日期：2026-07-15
> 最后更新：2026-07-15

---

## 目录

- [1. 生成管道 — 整理版原文 (Polish)](#1-生成管道--整理版原文-polish)
- [2. 生成管道 — 结构化笔记 (Notes)](#2-生成管道--结构化笔记-notes)
- [3. 生成管道 — Excel 数据摘要](#3-生成管道--excel-数据摘要)
- [4. 生成管道 — 格式选项](#4-生成管道--格式选项)
- [5. 生成管道 — 风格指令](#5-生成管道--风格指令)
- [6. 生成管道 — Prompt 构建流程](#6-生成管道--prompt-构建流程)
- [7. 图片处理 — LLM / OCR Prompt](#7-图片处理--llm--ocr-prompt)
- [8. 事实验证 — 三阶段 Prompt](#8-事实验证--三阶段-prompt)
- [9. AI 对话 — Agent Prompt](#9-ai-对话--agent-prompt)
- [10. LLM 续写机制](#10-llm-续写机制)
- [11. 重复代码与审视点](#11-重复代码与审视点)

---

## 1. 生成管道 — 整理版原文 (Polish)

### 1.1 视频/网页/文本/音频 — `POLISH_PROMPT`

**文件**：[backend/app/generation/prompt.py:11-54](backend/app/generation/prompt.py#L11-L54)
**触发场景**：sourceType ∈ {video, webpage, text, audio} + mode=polished
**调参**：temperature=0.7, max_tokens=65536, max_continuations=3

```
你是一个专业的文本编辑，擅长将视频转录文本整理成干净、通顺、保留原意的原文。

语言要求：
- 输出必须使用 **中文** 撰写。
- 专有名词、技术术语、品牌名称和人名应适当保留 **英文**。

视频标题：{video_title}

原始转录文本：
---
{segment_text}
---

你的任务：
根据上面的转录文本，进行**轻量级的文本整理**。请注意：你的任务是**整理原文**，不是总结，不是提炼，不是做笔记。请保持原文的完整信息和叙述风格。

具体要求：
1. **去除无关内容**：删除广告植入、填充词、无意义的重复、问候寒暄和不相关的闲聊
2. **合并相关内容**：将同一话题下相邻的多个片段自然合并成连贯的段落，保持叙述的流畅性
3. **修正明显错别字**：根据上下文修正转录过程中产生的明显错别字或同音字错误
4. **保持原文风格与信息**：保留说话人的表达方式、语气和叙述顺序。所有事实、数据、例子、结论、观点都必须完整保留。**不要改写或总结**
5. **适度分段与标题**：根据话题的自然转换进行分段，为每个主题添加恰当的二级标题（`## 标题`）
6. **去除时间戳**：转录文本中的时间戳不需要保留在最终输出中

输出说明：
- 仅返回最终的 **Markdown 内容**
- **不要**将输出包裹在代码块中（例如：```markdown ```）
- 这是一份"整理版原文"，不是"笔记总结"，请保持原汁原味的内容
- 输出篇幅应接近原文长度（允许因去除无关内容而适度缩短）

输出格式：第一行必须是文档标题（用 # 开头）。如果原文没有明确标题，请根据内容概括一个简洁标题（15字以内，不要「笔记」「整理」后缀）。
```

### 1.2 PDF/文档 — `PDF_POLISH_PROMPT`

**文件**：[backend/app/generation/prompt.py:190-220](backend/app/generation/prompt.py#L190-L220)
**触发场景**：sourceType ∈ {pdf, docx, pptx, epub} + mode=polished
**与视频版的差异**：定位是"忠实于原文的整理"，额外处理表格修正、OCR 纠错、公式规范化、图片引用保留

```
你是一个专业的文档编辑，擅长将文档转录产生的 Markdown 整理成规范、准确、易读的原文。

语言要求：
- 输出必须使用 **中文** 撰写。
- 专有名词、技术术语、品牌名称保持原文。

原始转录文本（来自 PDF 导入）：
---
{document_text}
---

你的任务：
根据上面的 PDF 转录结果，进行**忠实于原文的整理**。请遵循以下原则：

1. **修正表格错误**：pymupdf 识别的表格可能存在合并单元格错位、行列缺漏、内容错位等问题。请比对上下文修正表格结构，使其准确反映原文内容。输出规范的 Markdown 表格。
2. **修正 OCR 错误**：纯图片或扫描页的 OCR 文字可能存在错别字、乱序行、漏行等问题。请根据上下文推断修正，补全不完整的句子和段落。
3. **公式规范化**：将文档中的数学公式转为准确的 LaTeX 格式。公式编号保留。
4. **保留图片引用**：转录中的 `![...](data:image/...)` 图像引用必须完整保留，不要删除或修改。
5. **保持结构**：保留原文的章节结构、标题层级和编号体系。适度调整不合理的分段，合并 OCR 导致的碎片行。

输出说明：
- 仅返回最终的 **Markdown 内容**
- **不要**将输出包裹在代码块中
- 这是一份"整理版原文"，不是总结或笔记
- 输出篇幅应接近原文长度
```

### 1.3 Polish 风格指令

**文件**：[backend/app/generation/prompt.py:178-186](backend/app/generation/prompt.py#L178-L186)

Polish 场景只有两种风格（其余 fallback 到 STYLE_MAP）：

```
精简整理（minimal）：删除冗余内容，保留核心信息和主要论证，适度缩短。
完整整理（detailed）：保留原文的完整信息，仅删除填充词和无关内容。
```

**审视点**：其余 7 种风格（academic/tutorial/business/meeting_minutes/task_oriented/life/xiaohongshu）在 polish 场景下直接使用 STYLE_MAP 的笔记版风格指令，这些指令中包含"做笔记"等术语，对整理版原文不太适配。是否需要为 polish 场景单独写一套风格指令？

---

## 2. 生成管道 — 结构化笔记 (Notes)

### 2.1 视频/网页/文本/音频 — `BASE_PROMPT`

**文件**：[backend/app/generation/prompt.py:59-91](backend/app/generation/prompt.py#L59-L91)
**触发场景**：sourceType ∈ {video, webpage, text, audio} + mode=notes
**结构**：前缀（角色+任务）→ 后缀（格式选项+风格+用户备注）

```
你是一个专业的笔记助手，擅长将视频转录内容整理成清晰、有条理且信息丰富的笔记。

语言要求：
- 笔记必须使用 **中文** 撰写。
- 专有名词、技术术语、品牌名称和人名应适当保留 **英文**。

视频标题：{video_title}
视频转录（格式：开始时间 - 内容）：
---
{segment_text}
---

你的任务：
根据上面的分段转录内容，生成结构化的笔记，遵循以下原则：

1. **完整信息**：记录尽可能多的相关细节，确保内容全面。
2. **去除无关内容**：省略广告、填充词、问候语和不相关的言论。
3. **保留关键细节**：保留重要事实、示例、结论和建议。
4. **可读布局**：必要时使用项目符号，并保持段落简短，增强可读性。
5. 视频中提及的数学公式必须保留，并以 LaTeX 语法形式呈现，适合 Markdown 渲染。

输出说明：
- 仅返回最终的 **Markdown 内容**。
- **不要**将输出包裹在代码块中。
- 避免将编号标题（如"1. **内容**"）写成有序列表的格式，以免解析错误。
- 如果要加粗并保留编号，应使用 `1\\. **内容**`（加反斜杠），防止被误解析为有序列表。

额外重要的任务如下（每一个都必须严格完成）：
[格式选项在此拼接]

[风格指令在此拼接]

[用户备注在此拼接]

输出格式：第一行必须是笔记标题（用 # 开头）。请根据内容概括一个简洁标题（15字以内，不要「笔记」后缀）。
```

**审视点**：
- 角色定义"擅长将**视频**转录内容整理"，但实际也用于网页、文本、音频。对非视频场景，角色定位是否应泛化？
- "视频标题：{video_title}"——非视频场景下显示的是文档标题，命名一致性

### 2.2 PDF/文档 — `PDF_NOTES_PROMPT`

**文件**：[backend/app/generation/prompt.py:222-252](backend/app/generation/prompt.py#L222-L252)
**触发场景**：sourceType ∈ {pdf, docx, pptx, epub} + mode=notes
**与视频版的差异**：角色定位"文档阅读助手"，强调文档特有的结构（标题层级、表格数据、公式）

```
你是一个专业的文档阅读助手，擅长将文档转录内容整理成清晰、有条理且信息丰富的笔记。

语言要求：
- 笔记的语言应与原文保持一致（中文文档 → 中文笔记，英文文档 → 英文笔记）。
- 专有名词、技术术语保持原文。

文档标题：{title}
文档转录内容：
---
{document_text}
---

你的任务：
根据上面的文档内容，生成结构化的笔记，遵循以下原则：

1. **完整信息**：记录文档中的所有重要信息、论点、数据和结论。
2. **层次结构**：使用标题层级（## → ###）组织内容，反映原文的逻辑结构。
3. **关键细节**：保留重要事实、示例、数据表格和结论。表格数据应整理为 Markdown 表格。
4. **可读性**：适度使用项目符号，保持段落简短，增强可读性。
5. **公式保留**：文档中的数学公式必须以 LaTeX 语法形式呈现。

输出说明：
- 仅返回最终的 **Markdown 内容**
- **不要**将输出包裹在代码块中
- 避免将编号标题写成有序列表格式，如需加粗保留编号应使用 `N\\. **内容**`

额外重要的任务如下（每一个都必须严格完成）：
[格式选项在此拼接]
```

**审视点**：
- 文档 notes 的语言要求是"与原文保持一致"，但视频 notes 强制"中文撰写"。这是否合理？（英文视频是否需要英文笔记？）
- 文档 notes 没有"用户备注"注入（与视频 notes 不同），是否遗漏？

---

## 3. 生成管道 — Excel 数据摘要

### 3.1 `EXCEL_SUMMARY_PROMPT`

**文件**：[backend/app/generation/prompt.py:397-422](backend/app/generation/prompt.py#L397-L422)
**触发场景**：sourceType=xlsx（任意 mode）
**特殊处理**：原始 Markdown 表格被 `_build_data_summary()` 预处理为结构化摘要（sheet 结构 + 首尾各 3 行样品），大幅减少 token 消耗

```
你是一个专业的数据分析师，擅长提炼核心数据指标。

语言要求：
- 输出必须使用 **中文** 撰写。
- 简洁有力，控制字数。

{data_section}  ← 结构化摘要替代原始表格

你的任务：
基于以上数据摘要和样品，生成一份**数据摘要**。遵循以下原则：

1. 📊 **数据规模**: 几行几列、几个 sheet、各字段数据类型
2. 🔢 **核心指标**: 基于样品数据，提取 3-5 个值得关注的数值特征
3. 📈 **关键发现**: 2-4 句话总结数据中透露的关键信息
4. ⚠️ **需注意**: 缺失值、格式不一致等数据质量问题

{style_text}

⚠️ 严禁事项：
- **严禁**重复输出完整数据 — 摘要不是数据副本
- **严禁**逐行罗列每条记录

输出说明：
- 仅返回最终的 **Markdown 内容**
- 使用简洁的列表和指标卡片风格
- **不要**将输出包裹在代码块中
```

**审视点**：
- `{data_section}` 由 `_build_data_summary()` 预处理——这个预处理逻辑对 LLM 是否有信息丢失？只给首尾各 3 行，中间的数据特征可能被忽略
- 当前只有 detailed/minimal/business 三种 Excel 风格，是否需要支持更多？
- Excel 双产物（polished+notes）当前强制返回相同内容（因为 LLM 无法做真正的数值分析）。这个设计合理，但前端用户看到两个相同的产物可能会困惑

---

## 4. 生成管道 — 格式选项

**文件**：[backend/app/generation/prompt.py:96-442](backend/app/generation/prompt.py#L96-L442)

格式选项是拼接在 BASE_PROMPT / PDF_NOTES_PROMPT 之后的自然语言指令。通过 `AVAILABLE_OPTIONS` 表控制每种 sourceType 可用的选项。

### 4.1 格式选项总览

| 选项 | 函数 | 适用类型 | 内容摘要 |
|------|------|---------|---------|
| `toc` | `get_toc_format()` | 所有 | 自动生成基于 `##` 级标题的目录 |
| `link` | `get_link_format()` | video/audio | 每个 `##` 章节正文第一行插入 `*Content-[mm:ss]` 时间戳标记 |
| `screenshot` | `get_screenshot_format(has_visual)` | video | 每个 `##` 章节末尾插入 `*Screenshot-[mm:ss]` 占位符 |
| `summary` | `get_summary_format()` | 所有 | 笔记末尾添加 `## AI 总结` 段落 |
| `translate` | `get_translate_format(lang)` | pdf | 将全文内容翻译为目标语言 |
| `formulas` | `get_formulas_format()` | pdf | 笔记末尾添加 `## 公式汇总` 章节，提取所有数学公式 |
| `references` | `get_references_format()` | pdf | 笔记末尾添加 `## 参考文献` 章节，提取所有引用文献 |
| `images` | `get_images_format()` | pdf/docx/pptx/epub | 保留文档图片引用，为每张图片添加文字描述 |

### 4.2 各类型可用选项

```python
AVAILABLE_OPTIONS = {
    "video":    ["toc", "link", "screenshot", "summary"],
    "audio":    ["toc", "summary"],
    "webpage":  ["toc", "summary"],
    "pdf":      ["toc", "translate", "formulas", "references", "summary", "images"],
    "docx":     ["toc", "summary", "images"],
    "pptx":     ["toc", "summary", "images"],
    "xlsx":     [],
    "text":     ["toc", "summary"],
    "epub":     ["toc", "summary", "images"],
}
```

### 4.3 format → 函数映射

```python
_ALL_FORMAT_FNS = {
    "toc": get_toc_format,
    "link": get_link_format,
    "screenshot": get_screenshot_format,
    "summary": get_summary_format,
    "translate": get_translate_format,
    "formulas": get_formulas_format,
    "references": get_references_format,
    "images": get_images_format,
}
```

**审视点**：
- `screenshot` 格式有两种模式：`has_visual=True`（有网格图，从左上角黄色数字选帧）vs `has_visual=False`（从转写时间推选帧）。实际调用时 `has_visual=video_understanding`——这里 `video_understanding` 是一个布尔值，语义是"是否开启了 AI 画面理解"。但即使不开启 AI 画面理解，只要用户勾选了 screenshot 格式，也应该有截图功能。当前设计：不开 VL → LLM 从转写文本时间推断截图位置（不精确），开启 VL → LLM 看网格图选帧（更精确）。这个设计是合理的，但 prompt 中的 "左上角黄色数字" 是 VideoReader 的视觉提示，如果未来改格式会失效。
- `translate` 的 prompt 中 `{lang}` 变量用 `format()` 注入——如果语言名中包含 `{}` 特殊字符会报错（但当前 8 语言都不包含）
- `references` prompt 要求 BibTeX 格式，但文档转录可能不包含完整的文献元数据，LLM 是否能准确提取？

---

## 5. 生成管道 — 风格指令

**文件**：[backend/app/generation/prompt.py:117-285](backend/app/generation/prompt.py#L117-L285)

### 5.1 视频/网页/文本/音频 — `STYLE_MAP`（9 种）

| 风格 | 键 | 摘要 |
|------|-----|------|
| 精简模式 | `minimal` | 只记录核心论点和关键结论，大胆省略细节，短列表优先 |
| 详细模式 | `detailed` | 尽可能覆盖原文全部主要内容，每个话题充分展开 |
| 学术风格 | `academic` | 正式学术用语，按 背景→方法→论证→结论 组织 |
| 教程笔记 | `tutorial` | 按操作顺序组织，关键步骤写出具体命令/配置/参数 |
| 商业风格 | `business` | 结论先行，突出关键数据和行动建议 |
| 会议纪要 | `meeting_minutes` | 按议题归类，明确标注决议和待办事项 |
| 任务导向 | `task_oriented` | 以可执行的任务为单位组织内容，按优先级排列 |
| 生活向 | `life` | 保持个人语气和情感色彩，突出个人感悟和生活细节 |
| 小红书风格 | `xiaohongshu` | 口语化、有网感、短句、大量 emoji 和爆款关键词 |

### 5.2 文档 (PDF/Word/PPT/EPUB) — `DOC_STYLE_MAP`（8 种）

| 风格 | 键 | 来源 |
|------|-----|------|
| 详细模式 | `detailed` | 复用 STYLE_MAP |
| 精简模式 | `minimal` | 文档独有定义 |
| 学术风格 | `academic` | 复用 STYLE_MAP |
| 商业风格 | `business` | 复用 STYLE_MAP |
| 教程笔记 | `tutorial` | 复用 STYLE_MAP |
| 任务导向 | `task_oriented` | 复用 STYLE_MAP |
| 学术精读 | `imrad` | 文档独有：按论文结构 背景→方法→结果→讨论→结论 |
| 会议纪要 | `meeting` | 复用 STYLE_MAP |

### 5.3 风格指令注入位置

在 prompt 中，风格指令在格式选项**之后**、用户备注**之前**拼接。前端通过 `capabilities` 端点获取可用的风格列表。

**审视点**：
- **小红书风格** (`xiaohongshu`) 在当前生成管道中被注册为视频风格，但没有对应的格式选项。所有视频源都可选它，但它的 prompt 包含"标题用二极管法"——这种语气适用于视频标题，但在笔记生成语境中可能产生奇怪输出
- **会议纪要风格** 在文档场景下 `key=meeting`，在视频场景下 `key=meeting_minutes`——命名不一致，可能导致前端困惑
- **文档精简模式** 与 **视频精简模式** 是两套不同的定义，内容差异较大，需确认是否符合预期
- **文档 IMRaD 风格** 仅文档支持，但未在能力端点中与其他风格区分

---

## 6. 生成管道 — Prompt 构建流程

**文件**：
- [backend/app/generation/prompt_builder.py](backend/app/generation/prompt_builder.py) — `PromptBuilder` 类
- [backend/app/generation/prompt.py:530-631](backend/app/generation/prompt.py#L530-L631) — `build_polish_prompt()` / `build_notes_prompt()`

### 6.1 构建流程

```
PromptBuilder.build(source_type, mode, clean_text, title, style, formats, user_notes)
  │
  ├─ source_type == "xlsx" → build_excel_summary_prompt()
  │     └─ 原始表格 → _build_data_summary() 预处理 → EXCEL_SUMMARY_PROMPT.format()
  │
  ├─ mode == "polished" → build_polish_prompt()
  │     ├─ is_doc? → PDF_POLISH_PROMPT.format(document_text=...)
  │     └─ else    → POLISH_PROMPT.format(video_title=..., segment_text=_format_segments(...))
  │     ├─ format 拼接（六种: translate/formulas/references/toc/images/screenshot/link）
  │     ├─ style 指令拼接（DOC_STYLE_MAP_POLISH 或 STYLE_MAP_POLISH）
  │     └─ user_notes 拼接
  │
  └─ mode == "notes" → build_notes_prompt()
        ├─ is_doc? → PDF_NOTES_PROMPT.format(title=..., document_text=...)
        └─ else    → BASE_PROMPT.format(video_title=..., segment_text=_format_segments(...))
        ├─ style 指令拼接（DOC_STYLE_MAP 或 STYLE_MAP）
        ├─ format 拼接（从 AVAILABLE_OPTIONS 按 source_type 过滤）
        └─ user_notes 拼接
```

### 6.2 预处理函数

| 函数 | 作用 |
|------|------|
| `_format_segments(raw_text)` | JSON segments → `MM:SS - 文本` 格式行 |
| `_parse_markdown_table(data_text)` | Markdown 表格 → 结构化 dict（sheet/字段/样品行） |
| `_build_data_summary(data_text)` | Markdown 表格 → 首尾各 3 行 + 字段列表的文本摘要 |

**审视点**：
- `_format_segments` 仅在非文档类型时调用，为视频/音频的 JSON 格式转写时间轴
- 网页/文本没有 segments JSON，直接传原始文本，prompt 中仍显示"视频转录（格式：开始时间 - 内容）"——对非视频内容这是误导性的上下文

---

## 7. 图片处理 — LLM / OCR Prompt

### 7.1 位置与重复

图片处理的 prompt 在**两个位置重复定义**：

1. [backend/app/routers/process.py:33-73](backend/app/routers/process.py#L33-L73) — `IMAGE_PROMPTS` dict
2. [backend/app/engines/ocr.py:70-113](backend/app/engines/ocr.py#L70-L113) — `DashScopeOCREngine.execute()` 内联

两处内容**完全相同**，但独立维护。

### 7.2 四种模式 Prompt

**extract（文字提取）**：
```
请提取并输出这张图片中的全部文字内容。
规则：
1. 保留原始段落结构和换行
2. 如果图片中有表格，用 Markdown 表格格式输出
3. 忽略编辑器工具栏、菜单栏、状态栏、窗口边框等软件界面元素
4. 忽略与正文内容无关的界面文字（如应用名、按钮标签、提示信息）
5. 只输出文字，不要添加任何解释、说明或对话
6. 如果图片中没有文字，回复「无文字内容」
```

**table（表格提取）**：
```
请提取并输出这张图片中的表格数据。
规则：
1. 只提取数据表格本身——忽略编辑器工具栏、菜单栏、状态栏、窗口边框等界面元素
2. 忽略与表格数据无关的任何文字（如应用程序名称、按钮标签、提示信息）
3. 用 Markdown 表格格式输出所有表格
4. 表格前后可以用简短文字说明表格标题（仅当标题属于数据内容时）
5. 如果表格中有合并单元格，尽量还原为多行
6. 如果图片中没有数据表格，回复「未检测到表格数据」
7. 只输出结果，不要添加任何解释或对话
```

**digitize（文档数字化）**：
```
请将这张扫描件/文档图片转换为可编辑的 Markdown 文本。
规则：
1. 完整提取所有文字内容，不要遗漏
2. 保留原始文档结构：标题层级（用 #/##/###）、列表、缩进、分隔线
3. 表格用 Markdown 表格格式输出
4. 扫描噪声导致的文字歧义请根据上下文纠正，标注不确定处为 [?]
5. 保留页码、页眉、页脚信息（如有）
6. 只输出结果，不要添加任何解释或对话
```

**understand（内容理解）**：
```
请描述并解读这张图片的内容。
规则：
1. 先概括画面整体内容（1-2句）
2. 然后按要点列出关键信息和细节
3. 如果图片中有文字，提取并融入描述中
4. 如果图片中有数据/图表，解读其含义
5. 用 Markdown 格式输出，不要添加对话
```

### 7.3 调度逻辑

**文件**：[backend/app/routers/process.py:125-175](backend/app/routers/process.py#L125-L175)

```
POST /process/{id}
  │
  ├─ 1. 解析 model_id: 请求参数 → scene_models.image_process → scene_models.generate
  │
  ├─ 2. LLM 路径（有 model_id + api_key + supports_vision）
  │     └─ LLMService.call(prompt, image_urls=[data_url], temperature=0.1)
  │
  └─ 3. Engine 回退（LLM 不可用或失败）
        └─ DashScopeOCREngine → Tesseract 优先级链
```

**审视点**：
- 两处重复的 prompt 定义应该提取到共享模块（如 `app/generation/prompt.py`）
- `extract` 和 `default` 模式的 prompt 中"忽略编辑器工具栏"这个规则是为软件截图准备的，但对自然场景照片可能不适用
- 所有四种模式的 prompt 都要求"只输出结果，不要添加任何解释或对话"——这个约束对 VL 模型是否足够强？实测中是否有 LLM 无视此约束的情况？
- process 管道的 `temperature=0.1`，生成管道的 `temperature=0.7`——这个差异是否合理？（OCR 类任务需要低温度，但 understand 模式可能需要更高温度才能做出有洞察的理解）

---

## 8. 事实验证 — 三阶段 Prompt

**文件**：[backend/app/generation/prompt.py:636-779](backend/app/generation/prompt.py#L636-L779)
**调用方**：[backend/app/generation/fact_check.py](backend/app/generation/fact_check.py)

### 8.1 Phase 1: 断言提取 — `CLAIM_EXTRACTION_PROMPT`

**调参**：temperature=0.3, max_tokens=2048, max_continuations=0

```
你是一个事实核查助手。你的任务是从文本中提取**可通过公开网络搜索验证**的事实断言。

## 能力边界
我们只能通过搜索引擎检索公开网页信息。以下类型的断言**超出我们的验证能力，应跳过**：
- 学术论文中的实验数据、研究结论（通常在付费墙后）
- 企业内部数据（营收、用户数等未公开披露的数字）
- 小众领域的专业知识或冷门事实
- 个人经历、主观评价、推测性陈述

## 优先选择（符合能力边界的可验证断言）
- 知名新闻报道中的事件（时间、地点、涉事方）
- 政府/国际组织公开发布的统计数据
- 知名人物在公开场合的可查证言论
- 产品发布、公司公告等公开商业信息
- 广为人知的历史事件日期和细节

## 输出格式
以 JSON 数组返回，最多 5 条。每条包含：
- id: 编号（"claim_01" ~ "claim_05"）
- text: 断言原文（直接从原文引用）
- category: "event" | "statistic" | "claim" | "date_figure"

如果原文中没有符合上述标准的、可通过网络搜索验证的断言，返回空数组 []。

只返回 JSON 数组，不要任何额外文本。
{content_placeholder}
```

### 8.2 Phase 2: 交叉验证 — `VERIFICATION_PROMPT`

**调参**：temperature=0.3, max_tokens=1024, max_continuations=0

```
你是一个事实核查裁判。请根据提供的网络搜索结果，判断一条事实主张的可靠性。

## 你的核查方法（按序执行）

**第 1 步：意图拆解**
这条主张到底在说什么？
- 事实陈述（客观发生的事件）
- 数据声称（数字、百分比、金额）
- 引用/声明（某人说过的话）
- 观点/推测（主观判断，无法验证 → 直接判 unverifiable）
- 操作步骤/方法描述（通常无法通过网络搜索验证）

**第 2 步：关键词提取**
从主张中提取 3-5 个核心实体，用于判断搜索结果的相关性。
如果搜索结果中出现了这些核心实体，说明相关；否则不相关。

**第 3 步：权威性排序**
对找到的来源逐条评估可信度：

| 权威等级 | 示例 |
|----------|------|
| 高 | 知名新闻机构、政府网站、学术机构官网、维基百科 |
| 中 | 行业媒体、公司官网公告、知名科技博客 |
| 低 | 个人博客、论坛帖子、内容农场、自媒体、社交平台 |
| 不可用 | 404 页面、与主张无关的内容、纯广告页 |

**判定时优先采信高权威来源。低权威来源即使匹配也不足以支撑 verified。**

## 验证方法说明
我们使用搜索引擎检索公开网页，并抓取了部分页面的正文。以下因素会影响验证质量：
- 搜索结果可能不完整、不相关或偏向特定立场
- 抓取到的页面内容可能只是片段，缺少完整上下文
- 付费墙后的内容无法访问
- 我们没有能力进行实地调查或访问数据库

## 待验证主张
{claim_text}

## 网络搜索结果
{sources_text}

## 判断标准
请根据以上三步方法，综合评估搜索结果的质量和一致性：

- verified: **多个独立、高权威来源**明确支持该主张。
- disputed: 有来源明确修正或否定该主张，或不同高权威来源给出了显著不同的数字/事实。
- unverifiable: 搜索结果不相关、来源权威性过低、或完全找不到相关信息。**这是最常见的判定，不必勉强给出 verified/disputed。**
- inconclusive: 有部分相关来源但不足以确认或否定，信息模糊或互相矛盾。

## 输出
返回 JSON：
- status: "verified" | "disputed" | "unverifiable" | "inconclusive"
- confidence: 0.0-1.0 的置信度（综合来源权威性和一致性）
- intent: 主张的意图类型（"fact" | "data" | "quote" | "opinion" | "procedure"）
- keywords: 你提取的核心关键词列表
- analysis: 2-4 句话的推理过程，包括对最佳来源的权威性评述（中文）
- correction: 仅 status="disputed" 时给出修正文本，其余为 null

只返回 JSON，不要任何额外文本。
```

### 8.3 Phase 3: 报告生成 — `FACT_CHECK_REPORT_PROMPT`

**调参**：temperature=0.5, max_tokens=4096, max_continuations=0

```
你是一个专业的事实核查报告撰写人。请根据核查结果撰写一份格式规范、内容诚实的报告。

## 原始内容（被核查的文本）
{original_content}

## 核查结果（JSON）
{claims_json}

## 格式要求
严格按照以下 Markdown 模板输出：

# 事实核查报告
> 验证方法：通过搜索引擎检索公开网页，交叉比对多个来源后综合判定。
> 局限性：仅检索公开网页，未访问付费或学术数据库内容；来源质量参差不齐，结论仅供参考。

## 核查总览
| 指标 | 数值 |
|------|------|
| 核查断言数 | N 条 |
| 基本可信 | N 条 |
| 存在争议 | N 条 |
| 无法验证 | N 条 |
| 综合可信度 | X.X / 10 |

**概要**：（一句话概述）

## 逐条核查
### N. 断言原文
> （引述被核查的断言原文，用 blockquote 展示）
**判定**：✅ 基本可信 | ⚠️ 有争议 | ❓ 无法验证 | 🔍 存疑（置信度 XX%）
**依据**：（2-3 句话）
**参考来源**：
- [来源网站名](https://完整URL) — 一句话说明该来源提供了什么信息

## 说明
- 本报告基于公开网页搜索生成，验证范围和深度有限。
- 「无法验证」不等于内容错误，仅表示在公开网页中未找到支持或否定的证据。
- 标记为「有争议」或「存疑」的内容建议进一步核实。

只返回最终的 Markdown 内容，不要包裹在代码块中。
```

**审视点**：
- Phase 1 输入截断为 8000 chars（`content[:8000]`）——长文档可能有重要事实在截断部分
- Phase 2 抓取来源截断为 8000 chars/来源——这个长度对于复杂网页可能不够
- Phase 2 的关键词提取使用纯正则启发式（`_extract_keywords`），不如直接让 LLM 提取关键词——但 LLM 提取会增加一次 API 调用
- Phase 3 输入截断为 2000 chars——用户可能想看到完整原文的核查报告
- 三个阶段分别用 temperature 0.3 / 0.3 / 0.5——报告阶段温度最高，是为了让报告更有可读性，但也引入了不一致的风险

---

## 9. AI 对话 — Agent Prompt

### 9.1 `SYSTEM_PROMPT`

**文件**：[backend/app/routers/chat.py:33-39](backend/app/routers/chat.py#L33-L39)
**调参**：temperature=0.7, max_rounds=3

```
你是知识库问答助手。你可以查询已导入的文档来回答问题。
如果本地文档不足以回答，可使用 search_web 搜索互联网获取最新信息，必要时用 fetch_url 抓取网页全文精读。

回答要求：
- 基于文档或网络搜索结果回答，标注来源
- 如果所有来源都不足以回答，诚实说明
- 用中文回答，简洁准确
```

### 9.2 运行时扩充

在 chat 路由中，system prompt 会被动态扩充：

```python
context_parts = []
for did in doc_ids:
    tx = store.get_transcript(did)
    if tx:
        context_parts.append(f"[{tx['title']}]\n{tx['rawText'][:1000]}")
context = "\n\n".join(context_parts) if context_parts else "（未选中文档，可用 list_docs 查看可用文档）"

system = SYSTEM_PROMPT + f"\n\n--- 当前文档 ---\n{context}"
```

### 9.3 工具描述

| 工具 | 描述 | 参数 |
|------|------|------|
| `search_docs` | 跨所有文档搜索相关内容 | `query` (string, required) |
| `lookup_doc` | 查看指定文档的完整内容 | `id` (string, required), `query` (string, optional) |
| `list_docs` | 列出当前知识库中的可用文档 | 无 |
| `search_web` | 搜索互联网获取最新信息，返回 JSON 结果列表。当本地文档不足以回答时使用 | `query` (string, required) |
| `fetch_url` | 抓取指定网页的正文内容，返回 Markdown。用于精读搜索结果中的网页 | `url` (string, required) |

### 9.4 Agent 循环

**文件**：[backend/app/llm/agent.py](backend/app/llm/agent.py)

```
max_rounds=3  # 最多 3 轮工具调用

每轮：
  - 无工具 → 直接流式输出 final answer
  - 有工具 → 非流式调用获取 tool_calls → 执行工具 → 追加 tool_result 到 messages

达到 max_rounds 后：
  → 追加 "请基于以上对话和工具调用结果给出最终回答（使用中文）"
  → 流式输出最终回答
```

**审视点**：
- System prompt 非常简短（仅 4 句），没有给出"如何处理文档引用"的指导。LLM 有时会编造文档内容
- 对话上下文注入的是 `rawText[:1000]`，可能截断在句子中间
- `max_rounds=3` 意味着最多 3 次工具调用，但每次工具调用需要完整往返——复杂问题可能不够
- 工具调用的回合用非流式（`stream=False`），意味着用户要等到工具执行完才看到第一个 token。对于搜索慢的场景，用户体感延迟较大
- `search_web` 有三级降级（Tavily → SearXNG → DuckDuckGo），但工具描述中没有体现——LLM 不知道搜索能力有限时会 fallback
- 对话历史截断为最近 40 条（`history[-20:]` × 2 角色）

---

## 10. LLM 续写机制

**文件**：[backend/app/llm/service.py:134-198](backend/app/llm/service.py#L134-L198)

当 `finish_reason == "length"` 时自动续写：

```
第 1 轮：发送原始 prompt → LLM 响应
  如果 finish_reason == "length"：
    第 2 轮：messages += [{"role": "assistant", "content": "第1轮输出"},
                          {"role": "user", "content": "继续"}]
    第 3 轮：同样...
    最多 max_continuations 轮（当前设为 3）

每轮 temperature *= 0.8（下限 0.3）
```

**审视点**：
- 续写指令只有一个字"继续"——对某些模型这可能不够，导致续写质量下降
- 续写轮数固定为 3，长文档可能仍不够
- 续写时 temperature 递减——这是合理的设计，越往后越确定
- 续写只对 generate 管道生效（max_continuations=3），处理管道和事实验证不续写（max_continuations=0）

---

## 11. 重复代码与审视点

### 11.1 重复定义

| 问题 | 位置 | 建议 |
|------|------|------|
| 图片处理 4 种模式 prompt | `process.py:33-73` 和 `ocr.py:70-113` | 提取到 `prompt.py` 的 `IMAGE_PROCESS_PROMPTS` dict |
| process.py 中 `ImagePrompts` dict 未用 | `process.py` 定义了 `IMAGE_PROMPTS` 变量，但 `ocr.py` 中内联了同样的内容 | 统一引用 |
| 视频/文档 prompt 构建逻辑 | `build_polish_prompt` 和 `build_notes_prompt` 中有大量重复的判断逻辑 | 可提取公共上下文构建函数 |

### 11.2 全局审视清单

| # | 审视点 | 严重度 | 说明 |
|---|--------|--------|------|
| 1 | **Polish 风格指令不完整** | 中 | 7/9 种风格在 polish 场景下使用笔记版风格指令，包含"做笔记"等不适配术语 |
| 2 | **文档 notes 缺少 user_notes 注入** | 高 | `build_notes_prompt` 中文档分支没有 user_notes 拼接——待确认是否遗漏 |
| 3 | **视频 prompt 角色定位** | 低 | BASE_PROMPT 写死"视频转录内容"，但实际用于所有非文档类型 |
| 4 | **图片处理 prompt 重复** | 中 | 两处重复定义，修改时容易不同步 |
| 5 | **Excel 数据预处理信息损失** | 中 | `_build_data_summary()` 只给首尾各 3 行，中间数据特征丢失 |
| 6 | **事实验证 prompt 截断** | 中 | Phase 1 截断 8000 chars、Phase 3 截断 2000 chars |
| 7 | **对话 system prompt 过简** | 中 | 缺少文档引用规范、幻觉防护指导 |
| 8 | **续写指令单一** | 低 | 只有一个字"继续"，未给上下文提示 |
| 9 | **语言策略不一致** | 中 | 视频 notes 强制中文，文档 notes 跟随原文语言。英文视频是否需要英文笔记？ |
| 10 | **风格键命名不一致** | 低 | `meeting_minutes` (视频) vs `meeting` (文档) |
| 11 | **小红书风格适用场景** | 低 | 作为视频笔记风格可用，但 prompt 中"标题用二极管法"更适合标题而非正文 |
| 12 | **Process 管道的 temperature** | 低 | 所有 4 种模式统一 0.1——understand 模式可能需要更高温度 |
| 13 | **Agent 工具调用不可见性** | 中 | 工具调用回合非流式，用户等待时无反馈 |
| 14 | **对话文档注入截断** | 低 | `rawText[:1000]` 硬截断，不尊重句子边界 |
| 15 | **事实验证关键词提取** | 低 | 纯正则启发式，可能遗漏关键实体；可考虑让 LLM 提取 |

### 11.3 已确认 OK 的设计

| # | 设计点 | 说明 |
|---|--------|------|
| 1 | Excel 双产物返回相同内容 | LLM 无法做真正的数值分析，合理 |
| 2 | screenshot 格式的 has_visual 双模式 | VL 开启时从网格图选帧，关闭时从转写时间推断——合理 |
| 3 | 续写时 temperature 递减 | 越往后越确定，防止随机游走——合理 |
| 4 | 事实验证 source fallback | notes → polished → transcript——合理 |
| 5 | process 调度 LLM 优先 → Engine 回退 | 符合"LLM 能力更强但更贵"的设计理念 |
| 6 | Engine 绑定空列表 vs null | `[]`=清除绑定，`null`=不修改——语义清晰 |

---

## 附录 A: Prompt 文件索引

| 文件 | 包含的 Prompt |
|------|--------------|
| [backend/app/generation/prompt.py](backend/app/generation/prompt.py) | POLISH_PROMPT, BASE_PROMPT, PDF_POLISH_PROMPT, PDF_NOTES_PROMPT, EXCEL_SUMMARY_PROMPT, 全格式选项函数, 全风格指令, CLAIM_EXTRACTION_PROMPT, VERIFICATION_PROMPT, FACT_CHECK_REPORT_PROMPT |
| [backend/app/generation/prompt_builder.py](backend/app/generation/prompt_builder.py) | PromptBuilder 类 — 组合上述 prompt 的编排器 |
| [backend/app/generation/fact_check.py](backend/app/generation/fact_check.py) | 引用 prompt.py 中的三个事实验证 prompt |
| [backend/app/generation/generator.py](backend/app/generation/generator.py) | LLM 调用编排，无独立 prompt |
| [backend/app/routers/process.py](backend/app/routers/process.py) | IMAGE_PROMPTS — 4 种图片处理模式（与 ocr.py 重复） |
| [backend/app/engines/ocr.py](backend/app/engines/ocr.py) | DashScopeOCREngine — 4 种图片处理模式（与 process.py 重复） |
| [backend/app/routers/chat.py](backend/app/routers/chat.py) | SYSTEM_PROMPT + 5 个工具描述 |
| [backend/app/llm/agent.py](backend/app/llm/agent.py) | AgentRunner — 无独立 prompt，只有 max_rounds 后的最终提示 |
| [backend/app/llm/service.py](backend/app/llm/service.py) | LLMService — 续写机制（"继续"），无独立 prompt |

## 附录 B: 温度参数速查

| 管道 | 模式 | temperature | max_tokens | max_continuations |
|------|------|-------------|------------|-------------------|
| 生成 | notes / polished | 0.7 | 65536 | 3 |
| 生成 | Excel summary | 0.7 | 65536 | 3 |
| 处理 | extract / table / digitize / understand | 0.1 | 4096 | 0 |
| 事实验证 | Phase 1 提取 | 0.3 | 2048 | 0 |
| 事实验证 | Phase 2 验证 | 0.3 | 1024 | 0 |
| 事实验证 | Phase 3 报告 | 0.5 | 4096 | 0 |
| 对话 | 工具调用回合 | 0.7 | — | — |
| 对话 | 最终回答 | 0.7 | — | — |
