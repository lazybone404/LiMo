# LiMo 提示词清单

> 完整记录项目中所有 LLM 提示词，供审视和优化。
> 生成日期：2026-07-15
> 最后更新：2026-07-15（P0-3 Agent 工具调用可见性 + 文档智能截断）

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
- [11. 审视点清单](#11-审视点清单)

---

## 1. 生成管道 — 整理版原文 (Polish)

### 1.1 视频/网页/文本/音频 Polish

**文件**：[backend/app/generation/prompt.py:36-63](backend/app/generation/prompt.py#L36-L63)（section 定义）
**构建函数**：`build_polish_prompt()` → [backend/app/generation/prompt.py:664-721](backend/app/generation/prompt.py#L664-L721)
**触发场景**：sourceType ∈ {video, webpage, text, audio} + mode=polished
**调参**：temperature=0.7, max_tokens=65536, max_continuations=3

**架构要点**：
- 通过 `PromptComposer.compose()` 组装为 XML 分节格式
- **无 `<style_preference>` 节** — Polish 固定忠实整理策略，不接受风格参数
- 格式白名单（`_POLISH_FORMAT_FNS`）：toc / link / screenshot / formulas / references / images（仅禁止 summary）
- `style` 参数保留但废弃（非 "detailed"/"none" 时 logger.warning 提示）
- **无 `<user_request>` 节** — Polish 不接受用户备注（`_ = user_notes` 参数保留仅作 API 兼容，内部忽略）

**实际发送的 XML 结构**：

```xml
<role>
你是一个专业的文本编辑，擅长将视频转录文本整理成干净、通顺、保留原意的原文。
语言要求：
- 输出必须使用 **中文** 撰写。
- 专有名词、技术术语、品牌名称和人名应适当保留 **英文**。
</role>

<task>
根据下面的转录文本，进行**轻量级的文本整理**。请注意：你的任务是**整理原文**，不是总结，不是提炼，不是做笔记。请保持原文的完整信息和叙述风格。

具体要求：
1. **去除无关内容**：删除广告植入、填充词（如"嗯""啊""那个""就是说""然后呢"）、无意义的重复、问候寒暄和不相关的闲聊
2. **合并相关内容**：将同一话题下相邻的多个片段自然合并成连贯的段落，保持叙述的流畅性
3. **修正明显错别字**：根据上下文修正转录过程中产生的明显错别字或同音字错误（如"在"→"再"，"的"→"得"等），但不要改变说话人的用词习惯
4. **保持原文风格与信息**：保留说话人的表达方式、语气和叙述顺序。所有事实、数据、例子、结论、观点都必须完整保留。**不要改写或总结**，这是最重要的一点
5. **适度分段与标题**：根据话题的自然转换进行分段，为每个主题添加恰当的二级标题（`## 标题`）。标题应简洁反映该段的主题
6. **去除时间戳**：转录文本中的时间戳（如 `00:00 - ` 格式）不需要保留在最终输出中
</task>

<source_content>
视频标题：{title}
转录文本：
{segment_text}
</source_content>

<instruction_priority>
规则优先级（从高到低）：
1. hard_constraints — 必须遵守，不可被覆盖
2. output_language — 系统输出语言设置，不可被用户备注覆盖
3. user_request — 用户明确要求，可覆盖 style_preference，但不能违反以上规则
4. style_preference — 风格偏好，在不违反以上规则的前提下生效

用户备注可以调整表达方式、侧重点和细节程度，但不能改变输出语言。
</instruction_priority>

<output_language>
输出语言策略（必须严格遵守）：
- 输出必须使用 **简体中文** 撰写。
- 技术术语、产品名称、API 名称、品牌、人名保留行业通用写法。
- 非简体中文术语首次出现时，可补充简体中文解释，不强制翻译已广泛使用的英文术语。
  推荐：Transformer（基于注意力机制的神经网络架构）
  不推荐：变换器
- 代码、命令、配置参数、LaTeX 公式保持原样，不翻译。
- 章节标题使用简体中文，首次出现可附原文。
</output_language>

<hard_constraints>
<!-- 仅包含 Polish 允许的格式选项：toc/formulas/references/images -->
</hard_constraints>

<output_format>
仅返回最终的 **Markdown 内容**。
- **不要**将输出包裹在代码块中（例如：```markdown ```）
- 这是一份"整理版原文"，不是"笔记总结"，请保持原汁原味的内容
- 输出篇幅应接近原文长度（允许因去除无关内容而适度缩短）
输出格式：第一行必须是文档标题（用 # 开头）。如果原文没有明确标题，请根据内容概括一个简洁标题（15字以内，不要「笔记」「整理」后缀）。
</output_format>

<user_request>
{user_notes}
</user_request>
```

### 1.2 PDF/文档 Polish

**文件**：[backend/app/generation/prompt.py:98-121](backend/app/generation/prompt.py#L98-L121)（section 定义）
**触发场景**：sourceType ∈ {pdf, docx, pptx, epub} + mode=polished
**与视频版的差异**：角色定位"文档编辑"，任务包含 5 条文档特有约束

**文档 Polish 独有硬约束**（全部在 `<task>` 节中）：

| # | 约束 | 说明 |
|---|------|------|
| 1 | 修正表格错误 | 引擎合并单元格错位、行列缺漏 |
| 2 | 修正 OCR 错误 | 错别字、乱序行、漏行 |
| 3 | 公式规范化 | LaTeX 格式 + 编号保留 |
| 4 | **保留图片引用** | `![...](data:image/...)` 必须完整保留 |
| 5 | 保持结构 | 章节结构、标题层级、编号体系 |

### 1.3 Polish 风格策略

**Polish 没有风格选择**。`build_polish_prompt()` 不调用 `normalize_style()`，不查找风格字典，`PromptComposer.compose()` 中不含 `<style_preference>` 节。

历史 `style` 参数保留仅用于 API 向后兼容，传 "detailed"/"none" 以外的值会触发 `logger.warning`。

---

## 2. 生成管道 — 结构化笔记 (Notes)

### 2.1 视频/网页/文本/音频 Notes

**文件**：[backend/app/generation/prompt.py:69-92](backend/app/generation/prompt.py#L69-L92)（section 定义）
**构建函数**：`build_notes_prompt()` → [backend/app/generation/prompt.py:744-820](backend/app/generation/prompt.py#L744-L820)
**触发场景**：sourceType ∈ {video, webpage, text, audio} + mode=notes
**调参**：temperature=0.7, max_tokens=65536, max_continuations=3

**架构要点**：
- 通过 `PromptComposer.compose()` 组装
- **有 `<style_preference>` 节** — 通过 `normalize_style()` 查找 `VIDEO_NOTE_STYLES`
- 格式选项从 `AVAILABLE_OPTIONS` 动态过滤，使用 `_ALL_FORMAT_FNS`（全部 7 种格式可用）
- **参考文件**：仅 Notes 注入 `<reference_texts>` 节（user_request 前）；polished/Excel 不注入
- `_INSTRUCTION_PRIORITY` 声明了三层优先级

```xml
<role>
你是一个专业的笔记助手，擅长将转录内容整理成清晰、有条理且信息丰富的笔记。
语言要求：
- 笔记必须使用 **中文** 撰写。
- 专有名词、技术术语、品牌名称和人名应适当保留 **英文**。
</role>

<task>
根据下面的转录内容，生成结构化的笔记，遵循以下原则：
1. **完整信息**：记录尽可能多的相关细节，确保内容全面。
2. **去除无关内容**：省略广告、填充词、问候语和不相关的言论。
3. **保留关键细节**：保留重要事实、示例、结论和建议。
4. **可读布局**：必要时使用项目符号，并保持段落简短，增强可读性。
5. 数学公式必须保留，并以 LaTeX 语法形式呈现，适合 Markdown 渲染。
</task>

<source_content>
标题：{title}
转录内容：
{segment_text}
</source_content>

<instruction_priority>
规则优先级（从高到低）：
1. hard_constraints — 必须遵守，不可被覆盖
2. user_request — 用户明确要求，可覆盖 style_preference，但不能违反 hard_constraints
3. style_preference — 风格偏好，在不违反以上规则的前提下生效
</instruction_priority>

<hard_constraints>
<!-- 从 AVAILABLE_OPTIONS 按 source_type 过滤的格式选项 -->
</hard_constraints>

<style_preference>
<!-- 从 VIDEO_NOTE_STYLES 查找的风格指令，仅当 style != "none" 时出现 -->
</style_preference>

<output_format>
仅返回最终的 **Markdown 内容**。
- **不要**将输出包裹在代码块中
- 避免将编号标题（如"1. **内容**"）写成有序列表的格式
输出格式：第一行必须是笔记标题（用 # 开头）。请根据内容概括一个简洁标题（15字以内，不要「笔记」后缀）。
</output_format>

<user_request>
{user_notes}
</user_request>
```

### 2.2 PDF/文档 Notes

**文件**：[backend/app/generation/prompt.py:127-149](backend/app/generation/prompt.py#L127-L149)（section 定义）
**触发场景**：sourceType ∈ {pdf, docx, pptx, epub} + mode=notes
**与视频版的差异**：
- 角色定位"文档阅读助手"
- 语言策略：跟随原文语言（而非强制中文）
- 风格查找 `DOC_NOTE_STYLES`（含 imrad）

---

## 3. 生成管道 — Excel 数据摘要

### 3.1 `build_excel_summary_prompt()`

**文件**：[backend/app/generation/prompt.py:155-176](backend/app/generation/prompt.py#L155-L176)（section 定义）
**构建函数**：[backend/app/generation/prompt.py:299-324](backend/app/generation/prompt.py#L299-L324)
**触发场景**：sourceType=xlsx（任意 mode）
**特殊处理**：原始 Markdown 表格被 `_build_data_summary()` 预处理为结构化摘要（sheet 结构 + 首尾各 3 行样品），大幅减少 token 消耗

**XML 结构**：`<role>` → `<task>` → `<source_content>` → `<instruction_priority>` → `<hard_constraints>` → `<style_preference>`（可选）→ `<output_format>` → `<user_request>`

**审视点**：
- `{data_section}` 由 `_build_data_summary()` 预处理——只给首尾各 3 行，中间的数据特征可能被忽略
- 当前只有 detailed/minimal/business 三种 Excel 风格
- Excel 双产物（polished+notes）当前强制返回相同内容（因为 LLM 无法做真正的数值分析）

---

## 4. 生成管道 — 格式选项

**文件**：[backend/app/generation/prompt.py:562-620](backend/app/generation/prompt.py#L562-L620)

格式选项通过 `_build_hard_constraints()` 函数统一构建，放入 `<hard_constraints>` 节。

### 4.1 格式选项总览

| 选项 | 函数 | 适用类型 | Polish | Notes |
|------|------|---------|--------|-------|
| `toc` | `get_toc_format()` | 所有（除 xlsx） | ✅ | ✅ |
| `link` | `get_link_format()` | video | ✅ | ✅ |
| `screenshot` | `get_screenshot_format(has_visual)` | video | ✅ | ✅ |
| `summary` | `get_summary_format()` | 所有（除 xlsx） | ❌ | ✅ |
| `formulas` | `get_formulas_format()` | audio/webpage/pdf/docx/pptx/text/epub | ✅ | ✅ |
| `references` | `get_references_format()` | pdf | ✅ | ✅ |
| `images` | `get_images_format()` | pdf/docx/pptx/epub | ✅ | ✅ |

**Polish 白名单规则**：toc/link/screenshot/formulas/references/images 是"编辑/结构增强"行为，允许；仅 summary 是"创作/笔记"行为，禁止。白名单由 `_POLISH_FORMAT_FNS` 控制。

> `translate` 已从格式体系移除（2026-08-14）：不在 `_ALL_FORMAT_FNS` 也不在 `AVAILABLE_OPTIONS`，前端不再出现翻译选项。

### 4.2 各类型可用选项

```python
AVAILABLE_OPTIONS = {
    "video":    ["toc", "link", "screenshot", "summary"],
    "audio":    ["toc", "summary", "formulas"],
    "webpage":  ["toc", "summary", "formulas"],
    "pdf":      ["toc", "formulas", "references", "summary", "images"],
    "docx":     ["toc", "summary", "images", "formulas"],
    "pptx":     ["toc", "summary", "images", "formulas"],
    "xlsx":     [],
    "text":     ["toc", "summary", "formulas"],
    "epub":     ["toc", "summary", "images", "formulas"],
}
```

### 4.3 格式函数映射（两套）

```python
# Notes 使用（全部 7 种格式）
_ALL_FORMAT_FNS = {
    "toc": get_toc_format, "link": get_link_format,
    "screenshot": get_screenshot_format, "summary": get_summary_format,
    "formulas": get_formulas_format, "references": get_references_format,
    "images": get_images_format,
}

# Polish 使用（白名单 6 种：仅排除 summary）
_POLISH_FORMAT_FNS = {
    "toc": get_toc_format,
    "link": get_link_format, "screenshot": get_screenshot_format,
    "formulas": get_formulas_format, "references": get_references_format,
    "images": get_images_format,
}
```

---

## 5. 生成管道 — 风格指令

**文件**：[backend/app/generation/prompts/notes_styles.py](backend/app/generation/prompts/notes_styles.py)

### 5.1 风格体系架构

```
COMMON_NOTE_STYLES (5 种: detailed/business/academic/tutorial/task_oriented)
    ├── VIDEO_NOTE_STYLES  = COMMON + { minimal, meeting, life, xiaohongshu } = 9 种
    └── DOC_NOTE_STYLES    = COMMON + { minimal, meeting, imrad }              = 8 种
```

通过 Python dict 解构 `{**COMMON_NOTE_STYLES, ...}` 展开，不复制内容。

### 5.2 共享基础风格 (COMMON_NOTE_STYLES)

| 风格 | 键 | 摘要 |
|------|-----|------|
| 全文精要 | `detailed` | 尽可能覆盖原文全部主要内容，每个话题充分展开 |
| 商业风格 | `business` | 结论先行，突出关键数据和行动建议 |
| 学术风格 | `academic` | 正式学术用语，背景→方法→论证→结论 |
| 教程笔记 | `tutorial` | 按操作顺序组织，关键步骤写出具体命令/配置/参数 |
| 任务导向 | `task_oriented` | 以可执行的任务为单位组织，按优先级排列 |

### 5.3 视频独有风格 (VIDEO_NOTE_STYLES)

| 风格 | 键 | 说明 |
|------|-----|------|
| 精简模式 | `minimal` | 只记录核心论点和关键结论，大胆省略细节 |
| 会议纪要 | `meeting` | 按议题归类，明确标注决议和待办事项 |
| 生活向 | `life` | 保持个人语气和情感色彩 |
| 小红书风格 | `xiaohongshu` | 口语化、有网感、短句、emoji 和爆款关键词 |

### 5.4 文档独有风格 (DOC_NOTE_STYLES)

| 风格 | 键 | 说明 |
|------|-----|------|
| 精简模式 | `minimal` | **文档版精简**（不同于视频版） |
| 会议纪要 | `meeting` | 同视频版 |
| IMRaD 精读 | `imrad` | 按学术论文结构：背景→方法→结果→讨论→结论 |

### 5.5 Style key 兼容层

```python
STYLE_ALIAS = {"meeting_minutes": "meeting"}

def normalize_style(style: str) -> str:
    return STYLE_ALIAS.get(style, style)
```

旧 `meeting_minutes` key 自动映射到 `meeting`，前后端均实现 `normalize_style()`。

### 5.6 Polish 风格：无

Polish 没有风格体系。`build_polish_prompt()` 不查找任何风格字典，输出中无 `<style_preference>` 节。这是 P0-1 的核心设计变更。

### 5.7 已废弃的别名

```python
STYLE_MAP = VIDEO_NOTE_STYLES          # deprecated
DOC_STYLE_MAP = DOC_NOTE_STYLES        # deprecated
PDF_STYLE_MAP = DOC_NOTE_STYLES        # deprecated
```

保留这些别名以兼容 `prompt_builder.py` 和 `capabilities.py` 的旧引用。

---

## 6. 生成管道 — Prompt 构建流程

**文件**：
- [backend/app/generation/prompts/common.py](backend/app/generation/prompts/common.py) — `PromptComposer` 工具类
- [backend/app/generation/prompts/notes_styles.py](backend/app/generation/prompts/notes_styles.py) — 风格定义
- [backend/app/generation/prompt.py:664-820](backend/app/generation/prompt.py#L664-L820) — `build_polish_prompt()` / `build_notes_prompt()`
- [backend/app/generation/prompt_builder.py](backend/app/generation/prompt_builder.py) — `PromptBuilder` 调度层

### 6.1 PromptComposer

```python
class PromptComposer:
    @staticmethod
    def section(name: str, text: str) -> str:
        # 构建一个 XML 分节，text 为空时返回空字符串

    @staticmethod
    def compose(sections: list[tuple[str, str]]) -> str:
        # 按顺序组装分节，跳过空节，节之间用双换行分隔
```

**分节顺序**：
```
<role> → <task> → <source_content> → <instruction_priority> → <hard_constraints> → <style_preference> → <output_format> → <user_request>
```

### 6.2 构建流程

```
PromptBuilder.build(source_type, mode, clean_text, title, style, formats, user_notes)
  │
  ├─ source_type == "xlsx" → build_excel_summary_prompt()
  │     └─ PromptComposer.compose(sections)
  │
  ├─ mode == "polished" → build_polish_prompt()
  │     ├─ is_doc? → _DOC_POLISH_ROLE/TASK/OUTPUT
  │     └─ else    → _POLISH_ROLE/TASK/OUTPUT
  │     ├─ hard_constraints: _build_hard_constraints(formats, _POLISH_FORMAT_FNS)
  │     ├─ NO style_preference 节
  │     └─ PromptComposer.compose(sections)
  │
  └─ mode == "notes" → build_notes_prompt()
        ├─ is_doc? → _DOC_NOTES_ROLE/TASK/OUTPUT + DOC_NOTE_STYLES
        └─ else    → _NOTES_ROLE/TASK/OUTPUT + VIDEO_NOTE_STYLES
        ├─ style: normalize_style() → 查找风格字典 → style_preference 节
        ├─ hard_constraints: _build_hard_constraints(formats, _ALL_FORMAT_FNS 按 source_type 过滤)
        └─ PromptComposer.compose(sections)
```

### 6.3 `_build_hard_constraints()` 公共函数

```python
def _build_hard_constraints(
    formats: list[str] | None,
    format_fns: dict,               # Notes 传 _ALL_FORMAT_FNS 子集，Polish 传 _POLISH_FORMAT_FNS
    target_language: str = "zh-CN",
    video_understanding: bool = False,
) -> str:
```

一个函数同时服务 Notes 和 Polish，通过传入不同的 `format_fns` 控制白名单。

### 6.4 预处理函数（不变）

| 函数 | 作用 |
|------|------|
| `_format_segments(raw_text)` | JSON segments → `MM:SS - 文本` 格式行 |
| `_parse_markdown_table(data_text)` | Markdown 表格 → 结构化 dict（sheet/字段/样品行） |
| `_build_data_summary(data_text)` | Markdown 表格 → 首尾各 3 行 + 字段列表的文本摘要 |

---

## 7. 图片处理 — LLM / OCR Prompt

（本节内容与 P0-1 无关，保持不变）

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
        └─ DashScopeOCREngine（Tesseract 已移除，需三方插件）
```

**审视点**（未修复）：
- 两处重复的 prompt 定义应该提取到共享模块
- 所有四种模式统一 temperature=0.1——understand 模式可能需要更高温度
- "忽略编辑器工具栏"规则对自然场景照片不适用

---

## 8. AI 审核 / 事实验证 — Prompt（两条管道）

### 8.0 两条管道并存

| | 旧 FactCheckEngine | 新 ReviewEngine（AI 审核，2026-07-15 起） |
|--|-------------------|----------------------------------------|
| 调用方 | [fact_check.py](../backend/app/generation/fact_check.py) | [review.py](../backend/app/generation/review.py) |
| Prompt 数 | 3（在 prompt.py 定义） | 4（在 prompt.py 定义） |
| 状态 | **保留不动**（遗留） | 端点走 `/fact-check/{id}` + `/stream` |

### 8.1 旧 FactCheckEngine — 三阶段（保留）

**文件**：[backend/app/generation/prompt.py](backend/app/generation/prompt.py)

3 个 prompt 使用传统格式化字符串，未迁移到 XML 分节格式：

- **Phase 1 断言提取** `CLAIM_EXTRACTION_PROMPT` — temperature=0.3, max_tokens=2048, max_continuations=0
- **Phase 2 交叉验证** `VERIFICATION_PROMPT` — temperature=0.3, max_tokens=1024, max_continuations=0
- **Phase 3 报告生成** `FACT_CHECK_REPORT_PROMPT` — temperature=0.5, max_tokens=4096, max_continuations=0

### 8.2 新 ReviewEngine — AI 审核四阶段

**文件**：[backend/app/generation/review.py](backend/app/generation/review.py)（imports 自 prompt.py 4 个常量）

| # | Prompt | 阶段 | 调参 |
|---|--------|------|------|
| 1 | `STATIC_REVIEW_PROMPT` | Static Review（提取 Claim + 分类 + 内部一致性 + 风险评分） | 0.3 / 4096 |
| 2 | `VERIFICATION_PLANNER_PROMPT` | Verification Planning（LLM 批量决策验证策略） | 0.3 / 2048 |
| 3 | `EVIDENCE_REVIEWER_PROMPT` | Evidence Collection + Review（按需搜索 + 多维度评估） | 0.3 / 2048 |
| 4 | `AI_REVIEW_REPORT_PROMPT` | AI Review Report（红黄绿报告 + 可信度 + Action） | 0.5 / 8192 |

验证源 fallback：notes → polished → transcript。验证方式 `verify_mode`：LLM（默认）/ Web（联网搜索）。

---

## 9. AI 对话 — Agent Prompt

### 9.1 分层 System Prompt（`app/agent/prompts/`）

**文件**：`app/agent/prompts/base.md`（角色定义）+ `rules.md`（硬约束）+ `__init__.py`（`PromptLoader`）

不再用 chat.py 内联 `SYSTEM_PROMPT` 字符串——三层叠加（参照 AGENTS.md 模式）：

```python
loader = PromptLoader()          # 模块级单例
system = loader.build(doc_context)  # base + rules + 动态注入的当前文档摘要
```

- `base.md` → 角色定义
- `rules.md` → 硬约束（禁止编造、先查再答…）
- `doc_context` → 运行时注入的文档内容（`smart_truncate(rawText, 1000)` 按句子边界截断）

### 9.2 运行时扩充

对话上下文通过 `search_docs` / `lookup_doc` / `list_docs` 三个工具访问知识库；`search_web`（Tavily → SearXNG → DDG 降级）+ `fetch_url`（trafilatura/Jina）联网。

### 9.3 Agent SSE 事件流

```
meta        → budget/price 等上下文元信息（对话预算 = 窗口×0.7）
agent_status→ "正在分析问题..."              ← 工具判定前
tool_start  → tool + display（安全文案）
tool_result → summary + refs（引用文档 docId/title，完整结果只传 LLM）
  （可能多轮）
thinking    → reasoning_content（思考段，流式）
text        → 流式 token
done        → 结束（不带 usage；token/费用统计依赖 meta.price 前端估算）
compressed  → 长对话 LLM 压缩通知（agent.py 发）
```

**C 端隐私**：`tool_result` 仅推送摘要（计数/长度），完整结果只传给 LLM 不出现在前端 UI。`tool_start` 的 `display` 由后端 `_tool_display()` 生成安全文案。无独立 `ref` 事件——引用随 `tool_result.refs` 携带。

### 9.4 工具描述

| 工具 | 描述 |
|------|------|
| `search_docs` | 跨所有文档搜索相关内容 |
| `lookup_doc` | 查看指定文档的完整内容 |
| `list_docs` | 列出当前知识库中的可用文档 |
| `search_web` | 搜索互联网（Tavily → SearXNG → DuckDuckGo 三级降级） |
| `fetch_url` | 抓取指定网页的正文内容 |

---

## 10. LLM 续写机制

**文件**：[backend/app/llm/service.py:134-198](backend/app/llm/service.py#L134-L198)

（不变）

---

## 11. 审视点清单

### 11.1 已修复（本次 P0-1）

| # | 审视点 | 修复方式 |
|---|--------|---------|
| ✅ 1 | Polish 风格指令污染（7/9 种风格含"做笔记"术语） | Polish 移除 `<style_preference>` 节，固定忠实整理策略 |
| ✅ 2 | 风格键命名不一致 `meeting_minutes` vs `meeting` | 统一为 `meeting`，`normalize_style()` 兼容旧 key |
| ✅ 3 | 视频 prompt 角色写死"视频转录" | 非文档 Notes 使用泛化的角色定义 |
| ✅ 4 | 文档 notes 缺少 user_notes 注入 | `build_notes_prompt()` 统一为所有 sourceType 注入 `<user_request>` |
| ✅ 5 | Prompt 扁平拼接，LLM 分辨不出约束优先级 | 改为 XML 分节 + `<instruction_priority>` 声明三层优先级 |
| ✅ 6 | Agent prompt 过简，容易幻觉 | 上次已修复（5 条防幻觉原则） |
| ✅ 7 | 语言策略不一致（4 个 role 硬编码矛盾语言策略） | P0-2 统一为 `<output_language>` 节，随 target_language 动态生成 |
| ✅ 8 | Agent 工具调用不可见性 | P0-3 新增 `agent_status` 事件 + `tool_start` display + `tool_result` summary |
| ✅ 9 | 对话文档注入截断 | P0-3 `rawText[:1000]` → `smart_truncate(rawText, 1000)` 按句子边界截断 |

### 11.2 未修复（P1 或更低优先级）

| # | 审视点 | 严重度 | 说明 |
|---|--------|--------|------|
| 1 | **图片处理 prompt 重复** | 中 | `process.py` 和 `ocr.py` 两处重复定义 |
| 2 | **Excel 数据预处理信息损失** | 中 | `_build_data_summary()` 只给首尾各 3 行 |
| 3 | **旧 FactCheckEngine prompt 截断** | 中 | 旧 3 阶段 Phase 1 截断 8000 chars、Phase 3 截断 2000 chars（新 ReviewEngine 无此问题） |
| 4 | **旧 FactCheckEngine 未迁移 XML 分节** | 低 | 旧 3 个 prompt 仍用传统格式化字符串（新 ReviewEngine 四阶段 prompt 已模块化） |
| 5 | **续写指令单一** | 低 | 只有一个字"继续" |
| 6 | **小红书风格适用场景** | 低 | "标题用二极管法"更适合标题而非正文 |
| 7 | **Process 管道的 temperature** | 低 | 所有 4 种模式统一 0.1 |
| 8 | **旧 FactCheckEngine 关键词提取** | 低 | 纯正则启发式 |

### 11.3 已确认 OK 的设计

| # | 设计点 | 说明 |
|---|--------|------|
| 1 | Excel 双产物返回相同内容 | LLM 无法做真正的数值分析，合理 |
| 2 | screenshot 格式的 has_visual 双模式 | VL 开启时从网格图选帧，关闭时从转写时间推断 |
| 3 | 续写时 temperature 递减 | 越往后越确定，防止随机游走 |
| 4 | 事实验证 source fallback | notes → polished → transcript |
| 5 | process 调度 LLM 优先 → Engine 回退 | 符合"LLM 能力更强但更贵"的设计理念 |
| 6 | Engine 绑定空列表 vs null | `[]`=清除绑定，`null`=不修改 |
| 7 | Polish 格式白名单 | toc/link/screenshot/formulas/references/images 是编辑行为，仅 summary 是创作行为（translate 已移除） |
| 8 | `_POLISH_FORMAT_FNS` vs `_ALL_FORMAT_FNS` | 两套映射表，白名单由传入的 `format_fns` 参数控制 |

---

## 附录 A: Prompt 文件索引

| 文件 | 包含的 Prompt / 模块 |
|------|---------------------|
| [backend/app/generation/prompt.py](backend/app/generation/prompt.py) | 全部 section 定义 + `build_polish_prompt()` / `build_notes_prompt()` / `build_excel_summary_prompt()` + 旧 FactCheck 3 prompt + AI Review 4 prompt |
| [backend/app/generation/prompts/__init__.py](backend/app/generation/prompts/__init__.py) | 包入口，导出 COMMON_NOTE_STYLES / VIDEO_NOTE_STYLES / DOC_NOTE_STYLES / normalize_style |
| [backend/app/generation/prompts/common.py](backend/app/generation/prompts/common.py) | `PromptComposer` — 分节组装工具类 |
| [backend/app/generation/prompts/notes_styles.py](backend/app/generation/prompts/notes_styles.py) | 风格定义：COMMON_NOTE_STYLES + VIDEO_NOTE_STYLES + DOC_NOTE_STYLES + STYLE_ALIAS + normalize_style |
| [backend/app/generation/prompt_builder.py](backend/app/generation/prompt_builder.py) | `PromptBuilder` 调度层（source_type + mode → 正确 prompt 函数） |
| [backend/app/generation/fact_check.py](backend/app/generation/fact_check.py) | 引用 prompt.py 中的事实验证 prompt（旧 FactCheckEngine，保留） |
| [backend/app/generation/review.py](backend/app/generation/review.py) | 引用 prompt.py 中的 AI Review 4 prompt（新 ReviewEngine，四阶段） |
| [backend/app/generation/generator.py](backend/app/generation/generator.py) | LLM 调用编排，无独立 prompt |
| [backend/app/routers/process.py](backend/app/routers/process.py) | IMAGE_PROMPTS — 4 种图片处理模式（与 ocr.py 重复） |
| [backend/app/engines/ocr.py](backend/app/engines/ocr.py) | DashScopeOCREngine — 4 种图片处理模式（与 process.py 重复） |
| [backend/app/agent/prompts/base.md](backend/app/agent/prompts/base.md) | Agent 角色定义层（分层 System Prompt 第一层） |
| [backend/app/agent/prompts/rules.md](backend/app/agent/prompts/rules.md) | Agent 硬约束层（禁止编造、先查再答…第二层） |
| [backend/app/agent/prompts/__init__.py](backend/app/agent/prompts/__init__.py) | `PromptLoader` — 组装 base + rules + 动态 doc_context（chat.py 的旧内联 SYSTEM_PROMPT 已迁移至此） |
| [backend/app/agent/tools.py](backend/app/agent/tools.py) | 5 个工具 ToolDef + 工具描述（search_docs / lookup_doc / list_docs / search_web / fetch_url） |
| [backend/app/llm/agent.py](backend/app/llm/agent.py) | AgentRunner — SSE 事件流（agent_status / tool_start / tool_result / thinking / text / done） |
| [backend/app/llm/service.py](backend/app/llm/service.py) | LLMService — 续写机制 |
| [backend/app/core/text_utils.py](backend/app/core/text_utils.py) | `smart_truncate()` — 按句子边界截断 |

## 附录 B: 温度参数速查

| 管道 | 模式 | temperature | max_tokens | max_continuations |
|------|------|-------------|------------|-------------------|
| 生成 | notes / polished | 0.7 | 65536 | 3 |
| 生成 | Excel summary | 0.7 | 65536 | 3 |
| 处理 | extract / table / digitize / understand | 0.1 | 4096 | 0 |
| 事实验证（旧 FactCheckEngine） | Phase 1 提取 | 0.3 | 2048 | 0 |
| 事实验证（旧 FactCheckEngine） | Phase 2 验证 | 0.3 | 1024 | 0 |
| 事实验证（旧 FactCheckEngine） | Phase 3 报告 | 0.5 | 4096 | 0 |
| AI 审核 | Static Review | 0.3 | 4096 | 0 |
| AI 审核 | Verification Planner | 0.3 | 2048 | 0 |
| AI 审核 | Evidence Reviewer | 0.3 | 2048 | 0 |
| AI 审核 | Report | 0.5 | 8192 | 0 |
| 对话 | 工具调用 / 最终回答 | 0.7 | — | — |

## 附录 C: PromptComposer 标签速查

| 标签 | 语义 | 出现位置 | 说明 |
|------|------|---------|------|
| `<role>` | 纯角色定义（不含语言要求） | 所有 prompt | 视频/文档 Polish/Notes 各有独立 role 定义（各 1 行） |
| `<task>` | 任务目标 + 核心原则 | 所有 prompt | 文档 Polish 独有 5 条约束在此节中 |
| `<source_content>` | 待处理内容 | 所有 prompt | 原 `{segment_text}` 等内容放入此节 |
| `<instruction_priority>` | 规则优先级声明 | 所有 prompt | 四层：hard_constraints > output_language > user_request > style_preference |
| `<output_language>` | 输出语言策略（动态） | 所有 prompt | **NEW P0-2**：术语策略 + 代码/公式保留 + Excel 字段名不翻译 |
| `<hard_constraints>` | 硬约束（格式选项） | 生成/Excel | Polish 用 `_POLISH_FORMAT_FNS` 白名单，Notes 用 `_ALL_FORMAT_FNS` |
| `<style_preference>` | 软偏好（风格指令） | Notes/Excel | **Polish 无此节** |
| `<output_format>` | 输出格式要求 | 所有 prompt | 离末尾近，权重高 |
| `<reference_texts>` | 参考资料（来源 + 内容，仅供背景） | Notes | **仅 notes 注入**（user_request 前），polished/Excel 无；空则整节跳过 |
| `<user_request>` | 用户备注 | 生成/Excel（Notes） | 最后，权重最高但不覆盖 hard_constraints 和 output_language；**Polish 无此节**（忠实整理不接受备注） |
