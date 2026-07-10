"""
Prompt 构建器 — 照搬 BiliNote app/gpt/prompt.py + prompt_builder.py
"""
import json

from app.core.types import Style


# ── 整理版原文 (POLISH_PROMPT) — 照搬 BiliNote prompt.py:81-129 ──

POLISH_PROMPT = """你是一个专业的文本编辑，擅长将视频转录文本整理成干净、通顺、保留原意的原文。

语言要求：
- 输出必须使用 **中文** 撰写。
- 专有名词、技术术语、品牌名称和人名应适当保留 **英文**。

视频标题：
{video_title}

原始转录文本：
---
{segment_text}
---

你的任务：
根据上面的转录文本，进行**轻量级的文本整理**。请注意：你的任务是**整理原文**，不是总结，不是提炼，不是做笔记。请保持原文的完整信息和叙述风格。

具体要求：

1. **去除无关内容**：删除广告植入、填充词（如"嗯""啊""那个""就是说""然后呢"）、无意义的重复、问候寒暄和不相关的闲聊

2. **合并相关内容**：将同一话题下相邻的多个片段自然合并成连贯的段落，保持叙述的流畅性

3. **修正明显错别字**：根据上下文修正转录过程中产生的明显错别字或同音字错误（如"在"→"再"，"的"→"得"等），但不要改变说话人的用词习惯

4. **保持原文风格与信息**：
   - 保留说话人的表达方式、语气和叙述顺序
   - 所有事实、数据、例子、结论、观点都必须完整保留
   - **不要改写或总结**，这是最重要的一点

5. **适度分段与标题**：
   - 根据话题的自然转换进行分段
   - 为每个主题添加恰当的二级标题（`## 标题`）
   - 标题应简洁反映该段的主题

6. **去除时间戳**：转录文本中的时间戳（如 `00:00 - ` 格式）不需要保留在最终输出中

输出说明：
- 仅返回最终的 **Markdown 内容**
- **不要**将输出包裹在代码块中（例如：```markdown ```）
- 这是一份"整理版原文"，不是"笔记总结"，请保持原汁原味的内容
- 输出篇幅应接近原文长度（允许因去除无关内容而适度缩短）

请始终遵循此规则。"""


# ── 结构化笔记 (BASE_PROMPT) — 照搬 BiliNote prompt.py:1-48 ──

BASE_PROMPT = """你是一个专业的笔记助手，擅长将视频转录内容整理成清晰、有条理且信息丰富的笔记。

语言要求：
- 笔记必须使用 **中文** 撰写。
- 专有名词、技术术语、品牌名称和人名应适当保留 **英文**。

视频标题：
{video_title}

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
- **不要**将输出包裹在代码块中（例如：```markdown ```）。
- 避免将编号标题（如"1. **内容**"）写成有序列表的格式，以免解析错误。
- 如果要加粗并保留编号，应使用 `1\\. **内容**`（加反斜杠），防止被误解析为有序列表。

请始终遵循此规则。

额外重要的任务如下（每一个都必须严格完成）："""


# ── 格式选项 — 照搬 BiliNote prompt_builder.py:91-115 ──

def get_toc_format() -> str:
    return """\n9. **目录**: 自动生成一个基于 `##` 级标题的目录。目录只列标题文本，不包含任何时间戳或标记。"""

def get_link_format() -> str:
    return """\n10. **原片跳转**: 在每个 ## 章节正文第一行插入时间戳标记 `*Content-[mm:ss]`，表示该章节对应的视频时间点。不要将标记放在标题行中，不要在目录中加此标记。"""

def get_screenshot_format() -> str:
    return """\n11. **原片截图（必须执行）**: 每个 ## 章节末尾必须插入截图占位符 `*Screenshot-[mm:ss]`，时间戳从你看到的网格图左上角黄色数字中选取最相关的帧。即使某章节无明显的视觉内容，也应选取该时间段最接近的画面。"""

def get_summary_format() -> str:
    return """\n🧠 Final Touch:
在笔记末尾添加一个专业的 **AI 总结** 段落（## AI 总结），简要概括整个视频的核心内容。"""


# ── 风格指令 ──
# 引导性约束，帮助 LLM 理解输出方向，但保留灵活度

_STYLE_HEADER = "## 输出风格\n"

STYLE_MAP = {
    Style.MINIMAL: _STYLE_HEADER + "\n".join([
        "**精简模式**：",
        "- 只记录核心论点和关键结论，大胆省略细节",
        "- 倾向使用短列表而非长段落，简洁优先",
        "- 每个章节只保留最重要的几个要点",
        "- 例子、背景铺垫、次要论证可以跳过",
    ]),
    Style.DETAILED: _STYLE_HEADER + "\n".join([
        "**详细模式**：",
        "- 尽可能覆盖原文的全部主要内容",
        "- 保留重要的事实、数据、例子和论证过程",
        "- 每个话题充分展开，不因篇幅删减有价值的信息",
    ]),
    Style.ACADEMIC: _STYLE_HEADER + "\n".join([
        "**学术风格**：",
        "- 使用正式的学术用语，结构清晰严谨",
        "- 适合按 背景→方法→论证→结论 的逻辑组织",
        "- 保留专业术语、引用和理论框架",
        "- 避免 emoji、感叹号等非正式表达",
    ]),
    Style.TUTORIAL: _STYLE_HEADER + "\n".join([
        "**教程笔记**：",
        "- 按操作顺序组织，步骤清晰",
        "- 关键步骤写出具体命令、配置或参数",
        "- 补充必要的上下文（为什么这样做、常见错误）",
        "- 代码、公式、关键数值必须保留",
    ]),
    Style.BUSINESS: _STYLE_HEADER + "\n".join([
        "**商业风格**：",
        "- 结论先行，再展开论据",
        "- 突出关键数据和行动建议",
        "- 语言简洁精准，区分事实与观点",
    ]),
    Style.MEETING_MINUTES: _STYLE_HEADER + "\n".join([
        "**会议纪要**：",
        "- 按议题归类组织讨论内容",
        "- 明确标注决议和待办事项",
        "- 结构建议：议题 → 讨论要点 → 决议 → 待办",
    ]),
    Style.TASK_ORIENTED: _STYLE_HEADER + "\n".join([
        "**任务导向**：",
        "- 以可执行的任务为单位组织内容",
        "- 每条任务包含：做什么 + 简要说明",
        "- 按优先级或时间线排列",
    ]),
    Style.LIFE: _STYLE_HEADER + "\n".join([
        "**生活向**：",
        "- 保持原文的个人语气和情感色彩",
        "- 突出个人感悟和生活细节",
        "- 语言温暖自然，避免冷冰冰的总结",
    ]),
    Style.XIAOHONGSHU: _STYLE_HEADER + "\n".join([
        "**小红书风格**：",
        "- 口语化、有网感，短句为主",
        "- 多用 emoji ✨🔥💯 和爆款关键词",
        "- 标题用二极管法（正面刺激/负面刺激）营造紧迫感",
        "- 参考关键词：好用到哭、宝藏、绝绝子、YYDS、建议收藏、手把手、吹爆、打工人、吐血整理、家人们",
    ]),
}

# 整理版原文风格 — 精简版的约束（polish 场景下不需要太激进）
POLISH_MINIMAL = "## 输出风格\n**精简整理**：删除冗余内容，保留核心信息和主要论证，适度缩短。"
POLISH_DETAILED = "## 输出风格\n**完整整理**：保留原文的完整信息，仅删除填充词和无关内容。"

STYLE_MAP_POLISH = {
    Style.MINIMAL: POLISH_MINIMAL,
    Style.DETAILED: POLISH_DETAILED,
}
# 其余风格 fallback 到 STYLE_MAP 对应值（在 build_polish_prompt 中处理）

# ── PDF 专用 Prompt ──

PDF_POLISH_PROMPT = """你是一个专业的文档编辑，擅长将文档转录产生的 Markdown 整理成规范、准确、易读的原文。

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

2. **修正 OCR 错误**：纯图片或扫描页的 OCR 文字可能存在错别字（同音字、形近字）、乱序行、漏行等问题。请根据上下文推断修正，补全不完整的句子和段落。

3. **公式规范化**：将文档中的数学公式转为准确的 LaTeX 格式（行内公式用 `$...$`，块级公式用 `$$...$$`）。公式编号保留。

4. **保留图片引用**：转录中的 `![...](data:image/...)` 图像引用必须完整保留，不要删除或修改。

5. **保持结构**：保留原文的章节结构、标题层级和编号体系。适度调整不合理的分段，合并 OCR 导致的碎片行。

输出说明：
- 仅返回最终的 **Markdown 内容**
- **不要**将输出包裹在代码块中（例如：```markdown ```）
- 这是一份"整理版原文"，不是总结或笔记
- 输出篇幅应接近原文长度

请始终遵循此规则。"""

PDF_NOTES_PROMPT = """你是一个专业的文档阅读助手，擅长将文档转录内容整理成清晰、有条理且信息丰富的笔记。

语言要求：
- 笔记的语言应与原文保持一致（中文文档 → 中文笔记，英文文档 → 英文笔记）。
- 专有名词、技术术语保持原文。

文档标题：
{title}

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
5. **公式保留**：文档中的数学公式必须以 LaTeX 语法形式呈现（行内 `$...$`，块级 `$$...$$`）。

输出说明：
- 仅返回最终的 **Markdown 内容**
- **不要**将输出包裹在代码块中
- 避免将编号标题写成有序列表格式，如需加粗保留编号应使用 `N\\. **内容**`

请始终遵循此规则。

额外重要的任务如下（每一个都必须严格完成）："""

# ── 文档风格（通用 + 垂直场景）──

# 文档风格 — 与视频风格共享通用键 + 文档独有的垂直场景
_DOC_HEADER = "## 输出风格\n"

_DOC_MINIMAL = _DOC_HEADER + "\n".join([
    "**精简模式**：",
    "- 只提取文档最核心的论点和结论",
    "- 大胆省略例子、详细论证、背景铺垫",
    "- 用短列表代替长段落",
    "- 只为最重要的主题创建章节标题",
])

DOC_STYLE_MAP = {
    # 通用 — 与视频风格共用核心约束
    "detailed":   STYLE_MAP[Style.DETAILED],
    "minimal":    _DOC_MINIMAL,
    "academic":   STYLE_MAP[Style.ACADEMIC],
    "business":   STYLE_MAP[Style.BUSINESS],
    "tutorial":   STYLE_MAP[Style.TUTORIAL],
    "task_oriented": STYLE_MAP[Style.TASK_ORIENTED],
    # 文档独有垂直场景
    "imrad": _DOC_HEADER + "\n".join([
        "**学术精读（IMRaD）**：",
        "- 按学术论文结构组织：背景 → 方法 → 结果 → 讨论 → 结论",
        "- 每节独立完整，条理清晰",
    ]),
    "meeting": STYLE_MAP[Style.MEETING_MINUTES],
}

PDF_STYLE_MAP = DOC_STYLE_MAP

DOC_STYLE_MAP_POLISH = {
    k: (v.replace("笔记", "原文整理") if "笔记" in v else v)
    for k, v in DOC_STYLE_MAP.items()
}
PDF_STYLE_MAP_POLISH = DOC_STYLE_MAP_POLISH

# ── Excel 专用 Prompt ──

EXCEL_REPORT_PROMPT = """你是一个专业的数据分析师，擅长从结构化数据中提取洞察。

语言要求：
- 输出必须使用 **中文** 撰写。

数据内容（Markdown 表格）：
---
{data_text}
---

你的任务：
根据上面的数据表格，生成一份**数据分析报告**。请遵循以下原则：

1. **概览**: 简要描述数据来源（sheet 名称、行列数）。
2. **关键指标**: 提取最重要的数字和指标（合计、平均值、增长率等）。
3. **趋势分析**: 如有时间序列数据，分析趋势和变化。
4. **对比分析**: 比较不同类别/维度的数据。
5. **异常检测**: 标记明显异常值或值得关注的波动。
6. **图表说明**: 如果数据中包含图表标签，解释其意义。
7. **建议**: 基于数据提出 actionable 的建议。

{style_text}

输出说明：
- 仅返回最终的 **Markdown 内容**
- 使用表格、列表和标题组织内容
- **不要**将输出包裹在代码块中"""

EXCEL_SUMMARY_PROMPT = """你是一个专业的数据分析师，擅长提炼核心数据指标。

语言要求：
- 输出必须使用 **中文** 撰写。
- 简洁有力，控制字数。

数据内容（Markdown 表格）：
---
{data_text}
---

你的任务：
根据上面的数据表格，生成一份**数据摘要**。遵循以下原则：

1. 📊 **数据规模**: 几行几列、几个 sheet
2. 🔢 **核心指标**: 3-5 个最重要的数字（合计、均值、极值等）
3. 📈 **关键发现**: 2-4 句话总结最重要的洞察
4. ⚠️ **需注意**: 异常值、缺失项、数据质量问题

{style_text}

输出说明：
- 仅返回最终的 **Markdown 内容**
- 使用简洁的列表和指标卡片风格
- **不要**将输出包裹在代码块中"""

# ── PDF 专用格式 ──

def get_translate_format(target_language: str = "zh-CN") -> str:
    lang_names = {"zh-CN": "简体中文", "zh-TW": "繁體中文", "en": "English", "ja": "日本語",
                  "ko": "한국어", "fr": "Français", "de": "Deutsch", "es": "Español"}
    lang = lang_names.get(target_language, target_language)
    return f"""\n10. **翻译为{lang}（必须执行）**: 将全文内容翻译为{lang}。翻译要求：保留 LaTeX 公式原文不动；保留 Markdown 表格结构；专有名词保留原文并在首次出现时括号标注{lang}翻译；引用编号照原样保留。如果原文已经是{lang}，则只需修正翻译腔或不通顺的表达。"""

def get_formulas_format() -> str:
    return """\n11. **公式集（必须执行）**: 在笔记末尾添加 ## 公式汇总 章节，将文档中出现的所有数学公式整理为独立列表。每个公式独立编号，附带简短说明（公式用途/含义）。行内公式用 `$...$`，块级公式用 `$$...$$`。"""

def get_references_format() -> str:
    return """\n12. **参考文献表（必须执行）**: 在笔记末尾添加 ## 参考文献 章节，提取文档中引用的所有文献。
- 将全部引用放入一个 BibTeX 代码块（```bibtex ... ```）中，每个条目包含：作者、标题、期刊/会议、年份、DOI（如有）。
- 代码块后可附一个简短的阅读列表（1-3 句话概括主要引用方向即可）。
- **严禁**为每条引用单独创建子标题（###），也不要为每个条目单独分段。保持参考文献章节紧凑。"""

def get_images_format() -> str:
    return """\n13. **文档图片（必须执行）**: 文档中的图片引用 `![描述](/api/files/...)` 必须保留在笔记中。为每张有信息量的图片（图表、示意图、照片）添加简要的文字描述（1-2 句），说明图片展示的内容。纯装饰性图片可以省略描述但保留引用。"""


# ── Excel 风格 ──

EXCEL_STYLE_MAP = {
    "detailed":   "1. **详细分析**: 完整的数据分析报告，包含所有维度的深入分析。",
    "minimal":    "2. **简洁摘要**: 仅记录最重要的数据洞察和关键指标。",
    "business":   "3. **商务报告**: 提炼商业价值、趋势和行动建议，适合汇报。",
}

EXCEL_STYLE_KEYS = list(EXCEL_STYLE_MAP.keys())


def build_excel_report_prompt(data_text: str, style: str = "detailed",
                               user_notes: str = "") -> str:
    """构建 Excel 分析报告 prompt —— 用户备注作为分析指引放开头"""
    style_text = "" if style == 'none' else EXCEL_STYLE_MAP.get(style, "")
    prompt = EXCEL_REPORT_PROMPT.format(data_text=data_text, style_text=style_text)

    if user_notes.strip():
        prompt = (
            f"## 分析要求（用户指定，优先遵循）\n{user_notes.strip()}\n\n---\n\n"
            + prompt
        )
    return prompt


def build_excel_summary_prompt(data_text: str, style: str = "detailed",
                                user_notes: str = "") -> str:
    """构建 Excel 数据摘要 prompt —— 用户备注作为分析指引放开头"""
    style_text = "" if style == 'none' else EXCEL_STYLE_MAP.get(style, "")
    prompt = EXCEL_SUMMARY_PROMPT.format(data_text=data_text, style_text=style_text)

    if user_notes.strip():
        prompt = (
            f"## 分析要求（用户指定，优先遵循）\n{user_notes.strip()}\n\n---\n\n"
            + prompt
        )
    return prompt


# ── 可用选项（供 capabilities 端点 + 前端渲染）──

AVAILABLE_OPTIONS = {
    "video": ["toc", "link", "screenshot", "summary"],
    "audio": ["toc", "summary"],
    "webpage": ["toc", "summary"],
    "pdf": ["toc", "translate", "formulas", "references", "summary", "images"],
    "docx": ["toc", "summary", "images"],
    "pptx": ["toc", "summary", "images"],
    "xlsx": [],
    "text": ["toc", "summary"],
    "epub": ["toc", "summary", "images"],
}

# ── 格式函数映射（所有 format 名 → builder 的统一入口）──

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

# PDF 风格键列表
PDF_STYLE_KEYS = list(PDF_STYLE_MAP.keys())

# 视频风格键列表
VIDEO_STYLE_KEYS = list(STYLE_MAP.keys())

# Excel 风格键列表
_EXCEL_STYLE_KEYS = list(EXCEL_STYLE_MAP.keys())

# ── 构建函数 ──


def _format_segments(raw_text: str) -> str:
    """将 raw_text 格式化为 BiliNote 风格的时间戳文本（MM:SS - 内容）"""
    try:
        data = json.loads(raw_text)
        segments = data.get("segments", [])
        if segments:
            lines = []
            for s in segments:
                start = s.get("start", 0)
                m, sec = divmod(int(start), 60)
                lines.append(f"{m:02d}:{sec:02d} - {s.get('text', '')}")
            return "\n".join(lines)
    except (json.JSONDecodeError, TypeError):
        pass
    return raw_text


def build_polish_prompt(raw_text: str, title: str = "", style: str = "detailed",
                        source_type: str = "text",
                        formats: list[str] | None = None,
                        user_notes: str = "",
                        target_language: str = "zh-CN") -> str:
    """构建整理版原文 prompt"""
    from app.core.types import DOC_SOURCE_TYPES
    is_doc = source_type in DOC_SOURCE_TYPES

    if is_doc:
        prompt = PDF_POLISH_PROMPT.format(
            document_text=raw_text,
        )
    else:
        # 视频/网页/文本：去口语 + 合并段落
        prompt = POLISH_PROMPT.format(
            video_title=title or "未命名",
            segment_text=_format_segments(raw_text),
        )

    # 格式选项 — 翻译/公式/参考文献/图片对整理版同样适用
    polish_format_fns = {
        "translate": get_translate_format,
        "formulas": get_formulas_format,
        "references": get_references_format,
        "toc": get_toc_format,
        "images": get_images_format,
    }
    if formats:
        for f in formats:
            if f in polish_format_fns:
                prompt += polish_format_fns[f]()

    # 风格指令 — polish 场景用轻量约束
    if style and style != 'none':
        if is_doc:
            style_text = DOC_STYLE_MAP_POLISH.get(style, DOC_STYLE_MAP.get(style, ""))
        else:
            style_text = STYLE_MAP_POLISH.get(style, STYLE_MAP.get(style, ""))
        if style_text:
            prompt += f"\n{style_text}"

    if user_notes.strip():
        prompt += f"\n\n## 用户备注\n以下是用户提供的额外上下文和备注，请在整理原文时参考这些信息，适当融入相关内容：\n{user_notes.strip()}"
    prompt += "\n\n输出格式：第一行必须是文档标题（用 #  开头）。如果原文没有明确标题，请根据内容概括一个简洁标题（15字以内，不要「笔记」「整理」后缀）。"
    return prompt


def build_notes_prompt(raw_text: str, title: str = "", style: str = "detailed",
                       source_type: str = "text",
                       formats: list[str] | None = None,
                       user_notes: str = "",
                       target_language: str = "zh-CN") -> str:
    """构建结构化笔记 prompt"""
    from app.core.types import DOC_SOURCE_TYPES
    is_doc = source_type in DOC_SOURCE_TYPES

    if is_doc:
        prompt = PDF_NOTES_PROMPT.format(
            title=title or "未命名",
            document_text=raw_text,
        )
    else:
        prompt = BASE_PROMPT.format(
            video_title=title or "未命名",
            segment_text=_format_segments(raw_text),
        )

    # 风格指令 — 全局约束，放在格式之前以提升 LLM 注意力权重
    if style and style != 'none':
        if is_doc:
            style_text = DOC_STYLE_MAP.get(style, "")
        else:
            style_text = STYLE_MAP.get(style, "")
        if style_text:
            prompt += f"\n{style_text}"

    # 附加格式选项 — 从 AVAILABLE_OPTIONS 查该类型支持哪些 format，统一映射
    available = AVAILABLE_OPTIONS.get(source_type, AVAILABLE_OPTIONS.get("text", []))
    format_fns = {f: _ALL_FORMAT_FNS[f] for f in available if f in _ALL_FORMAT_FNS}
    if formats:
        for f in formats:
            if f in format_fns:
                prompt += format_fns[f](target_language) if f == "translate" else format_fns[f]()

    if user_notes.strip():
        prompt += f"\n\n## 用户备注\n以下是用户提供的额外上下文和备注，请在生成笔记时参考这些信息，适当融入相关内容：\n{user_notes.strip()}"

    prompt += "\n\n输出格式：第一行必须是笔记标题（用 #  开头）。请根据内容概括一个简洁标题（15字以内，不要「笔记」后缀）。"
    return prompt


# ── 事实验证 Prompt ──

CLAIM_EXTRACTION_PROMPT = """你是一个事实核查助手。你的任务是从文本中提取**可通过公开网络搜索验证**的事实断言。

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
- `id`: 编号（"claim_01" ~ "claim_05"）
- `text`: 断言原文（直接从原文引用）
- `category`: "event"（事件）| "statistic"（统计）| "claim"（声明）| "date_figure"（日期/数字）

如果原文中没有符合上述标准的、可通过网络搜索验证的断言，返回空数组 `[]`。

只返回 JSON 数组，不要任何额外文本。
{content_placeholder}"""

VERIFICATION_PROMPT = """你是一个事实核查裁判。请根据提供的网络搜索结果，判断一条事实主张的可靠性。

## 你的核查方法（按序执行）

**第 1 步：意图拆解**
这条主张到底在说什么？
- 事实陈述（客观发生的事件）
- 数据声称（数字、百分比、金额）
- 引用/声明（某人说过的话）
- 观点/推测（主观判断，无法验证 → 直接判 unverifiable）
- 操作步骤/方法描述（通常无法通过网络搜索验证）

**第 2 步：关键词提取**
从主张中提取 3-5 个核心实体，用于判断搜索结果的相关性：
- 人名、地名、机构名
- 专业术语、产品名、事件名
- 时间点、数字范围
如果搜索结果中出现了这些核心实体，说明相关；否则不相关。

**第 3 步：权威性排序**
对找到的来源逐条评估可信度：

| 权威等级 | 示例 |
|----------|------|
| 高 | 知名新闻机构（路透社/新华社/BBC）、政府网站（.gov）、学术机构官网（.edu）、维基百科 |
| 中 | 行业媒体、公司官网公告、知名科技博客 |
| 低 | 个人博客、论坛帖子、内容农场、自媒体、社交平台 |
| 不可用 | 404 页面、与主张无关的内容、纯广告页 |

**判定时优先采信高权威来源。低权威来源即使匹配也不足以支撑 verified。**

## 验证方法说明
我们使用搜索引擎检索公开网页，并抓取了部分页面的正文。以下因素会影响验证质量：
- 搜索结果可能不完整、不相关或偏向特定立场
- 抓取到的页面内容可能只是片段，缺少完整上下文
- 付费墙后的内容（学术论文、专业报告）无法访问
- 我们没有能力进行实地调查或访问数据库

## 待验证主张
{claim_text}

## 网络搜索结果
{sources_text}

## 判断标准
请根据以上三步方法，综合评估搜索结果的质量和一致性：

- `verified`: **多个独立、高权威来源**明确支持该主张。
- `disputed`: 有来源明确修正或否定该主张，或不同高权威来源给出了显著不同的数字/事实。
- `unverifiable`: 搜索结果不相关、来源权威性过低、或完全找不到相关信息。**这是最常见的判定，不必勉强给出 verified/disputed。**
- `inconclusive`: 有部分相关来源但不足以确认或否定，信息模糊或互相矛盾。

## 输出
返回 JSON：
- `status`: "verified" | "disputed" | "unverifiable" | "inconclusive"
- `confidence`: 0.0-1.0 的置信度（综合来源权威性和一致性）
- `intent`: 主张的意图类型（"fact" | "data" | "quote" | "opinion" | "procedure"）
- `keywords`: 你提取的核心关键词列表
- `analysis`: 2-4 句话的推理过程，包括对最佳来源的权威性评述（中文）
- `correction`: 仅 status="disputed" 时给出修正文本，其余为 null

只返回 JSON，不要任何额外文本。"""

FACT_CHECK_REPORT_PROMPT = """你是一个专业的事实核查报告撰写人。请根据核查结果撰写一份格式规范、内容诚实的报告。

## 原始内容（被核查的文本）
{original_content}

## 核查结果（JSON）
{claims_json}

## 格式要求

严格按照以下 Markdown 模板输出。报告应与笔记/整理版原文保持一致的排版风格：清晰的标题层级、简洁的列表、规范的链接格式。

---

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

**概要**：（一句话概述：本次核查发现 X 条断言可信，Y 条存在争议或无法验证。总体可信度 XX/10。）

## 逐条核查

### N. 断言原文

> （引述被核查的断言原文，用 blockquote 展示）

**判定**：✅ 基本可信 | ⚠️ 有争议 | ❓ 无法验证 | 🔍 存疑（置信度 XX%）

**依据**：（2-3 句话，说明找到了什么证据、或者为什么找不到、来源质量如何）

**参考来源**：
- [来源网站名](https://完整URL) — 一句话说明该来源提供了什么信息
- （如无可用来源，写「未找到相关公开来源」）

（对每条断言重复以上格式）

## 说明

- 本报告基于公开网页搜索生成，验证范围和深度有限。
- 「无法验证」不等于内容错误，仅表示在公开网页中未找到支持或否定的证据。
- 标记为「有争议」或「存疑」的内容建议进一步核实。

只返回最终的 Markdown 内容，不要包裹在代码块中。"""
