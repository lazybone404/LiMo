"""
Prompt 构建器 — 模块化分节格式

每个 prompt 由独立的 section 字符串组成，通过 PromptComposer 组装为 XML 标签格式。
风格体系已迁移至 prompts/notes_styles.py。
"""

import json
import logging

from .prompts.notes_styles import (
    VIDEO_NOTE_STYLES,
    DOC_NOTE_STYLES,
    normalize_style,
)
from .prompts.common import PromptComposer
from app.core.types import DOC_SOURCE_TYPES

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# 语言代码 — 共享映射 + 规范化
# ═══════════════════════════════════════════════════════════════════════

_LANG_NAMES = {
    "zh-CN": "简体中文", "zh-TW": "繁體中文", "en": "English",
    "ja": "日本語", "ko": "한국어", "fr": "Français",
    "de": "Deutsch", "es": "Español",
}

_LANGUAGE_ALIAS = {
    "zh": "zh-CN", "zh_cn": "zh-CN", "zh-tw": "zh-TW",
    "en-US": "en", "en-us": "en",
}


def normalize_target_language(lang: str) -> str:
    """统一语言代码，兼容简写（如 zh → zh-CN）"""
    return _LANGUAGE_ALIAS.get(lang, lang)


def _build_output_language(
    target_language: str = "zh-CN",
    source_type: str = "text",
) -> str:
    """构建 <output_language> 节内容"""
    target_language = normalize_target_language(target_language)
    lang = _LANG_NAMES.get(target_language, target_language)
    is_excel = source_type in ("xlsx",)

    if is_excel:
        return (
            f"输出语言策略（必须严格遵守）：\n"
            f"- 分析说明、结论、建议使用 **{lang}** 撰写。\n"
            f"- 字段名、列名、变量名保留原文，不要翻译。\n"
            f"- 数值单位保持原格式，不要转换。"
        )
    return (
        f"输出语言策略（必须严格遵守）：\n"
        f"- 输出必须使用 **{lang}** 撰写。\n"
        f"- 技术术语、产品名称、API 名称、品牌、人名保留行业通用写法。\n"
        f"- 非{lang}术语首次出现时，可补充{lang}解释，不强制翻译已广泛使用的英文术语。\n"
        f"  推荐：Transformer（基于注意力机制的神经网络架构）\n"
        f"  不推荐：变换器\n"
        f"- 代码、命令、配置参数、LaTeX 公式保持原样，不翻译。\n"
        f"- 章节标题使用{lang}，首次出现可附原文。"
    )

# ═══════════════════════════════════════════════════════════════════════
# 共享 section（所有 prompt 复用）
# ═══════════════════════════════════════════════════════════════════════

_INSTRUCTION_PRIORITY = (
    "规则优先级（从高到低）：\n"
    "1. hard_constraints — 必须遵守，不可被覆盖\n"
    "2. output_language — 系统输出语言设置，不可被用户备注覆盖\n"
    "3. user_request — 用户明确要求，可覆盖 style_preference，但不能违反以上规则\n"
    "4. style_preference — 风格偏好，在不违反以上规则的前提下生效\n\n"
    "用户备注可以调整表达方式、侧重点和细节程度，但不能改变输出语言。"
)

# ═══════════════════════════════════════════════════════════════════════
# 视频/文本/音频 — POLISH（整理版原文）section 定义
# ═══════════════════════════════════════════════════════════════════════

_POLISH_ROLE = "你是一个专业的文本编辑，擅长将视频转录文本整理成干净、通顺、保留原意的原文。"

_POLISH_TASK = (
    "根据下面的转录文本，进行**轻量级的文本整理**。"
    "请注意：你的任务是**整理原文**，不是总结，不是提炼，不是做笔记。"
    "请保持原文的完整信息和叙述风格。\n\n"
    "具体要求：\n"
    "1. **去除广告和无关内容**（最重要的步骤，必须严格完成）：\n\n"
    "   ═══ 第一步：商业意图识别（先判断，再删除）═══\n"
    "   逐段问自己：这段话的目的是什么？\n"
    "   - 目的是「讲知识、摆事实、给观点」→ 保留，即使举例时提到了品牌/产品名\n"
    "   - 目的是「让读者掏钱、关注、下载、扫码、加群、购买」→ 广告，删除\n"
    "   如果拿不准，用这个测试：删除这段话后，读者会丢失有价值的信息吗？\n"
    "   会丢失 → 保留；不会丢失 → 删除。\n\n"
    "   ═══ 第二步：识别并删除以下广告类型 ═══\n\n"
    "   【硬广告 — 明显推销】\n"
    "   a) 品牌口播/带货：突然开始推销具体产品/品牌/服务，介绍功能、价格、优惠，语气转为推销腔调\n"
    "   b) 赞助声明：\"感谢XX赞助\"\"本视频由XX赞助播出\"\"感谢XX的大力支持\"\n"
    "   c) 打赏/互动诱导：\"一键三连\"\"点赞投币收藏\"\"关注转发\"\"充电\"\"弹幕扣1\"\"评论区留言\"\n"
    "   d) 课程/知识付费：\"我的XX课程\"\"加入知识星球\"\"私信领取\"\"前XX名优惠\"\"限时折扣\"\n"
    "   e) 平台导流：\"下载XX APP\"\"搜索我的ID\"\"关注我的公众号/微博/小红书\"\"加入付费社群\"\n\n"
    "   【软广告 — 隐蔽植入】\n"
    "   软广告的特点是：先铺垫知识/问题/体验 → 再引出产品 → 回到内容。\n"
    "   它不是突然插入的硬广，而是和正片内容交织在一起的。识别方法：\n\n"
    "   f) **伪装科普式植入**：\n"
    "      结构模式：提出一个问题 → 铺垫科学/理论背景 → 某个具体产品作为\"解决方案\"出现 → 详细介绍产品参数/原理/效果 → 回到内容。\n"
    "      识别信号：段落从\"怎么做呢\"\"怎么解决呢\"\"问题在于\"过渡到一个具体的、有品牌名的产品，然后花大量篇幅介绍该产品的技术参数、使用感受、功效对比。\n"
    "      处理方式：保留铺垫的科学知识部分，删除从产品名称出现到商业CTA结束的整段。\n"
    "      例子：\"我们需要补充营养\"(保留) → \"哎，这就是我最近在用的XX营养品\"(此处开始删除) → 产品介绍(删除) → \"感兴趣可以看看\"(删除) → \"好，接下来讲第二点\"(保留)。\n\n"
    "   g) **体验式植入**：\n"
    "      结构模式：\"我最近在用...\"\"我试过很多...\"\"用了几个月之后...\"→ 转入产品体验描述 → 结尾CTA。\n"
    "      识别信号：说话人从讲客观知识突然转为讲个人使用体验，且该体验围绕一个品牌产品展开。\n"
    "      区分方法：如果体验描述是为了支撑一个知识论点（如\"冥想后我的专注力提升了\"），且没有指向付费产品，保留。如果体验描述指向一个具体品牌/产品并引导购买，删除。\n\n"
    "   h) **课程对比式植入**：\n"
    "      结构模式：\"视频只能讲到这里\"\"短短X分钟讲不完\"→ 对比付费内容更系统/更深入 → 引导购买。\n"
    "      识别信号：通过贬低免费内容的完整性来衬托付费内容的价值。\n"
    "      示例：\"这个视频里我不可能讲清楚\"(保留，是事实陈述) → \"所以欢迎加入我的XX课程\"(此处开始删除) → \"我们会有直播、教程、答疑...\"(删除整段)。\n\n"
    "   i) **片头/片尾套话**：\"哈喽大家好我是XX\"\"欢迎回到我的频道\"\"记得订阅\"\"下期再见\"\"如果觉得有用请分享\"。但如果片头包含实质性内容预告，保留预告部分。\n\n"
    "   j) **填充词/口水话**：嗯、啊、呃、那个、就是说、然后呢、对吧、说白了。不改变说话风格，只删除冗余词。\n\n"
    "2. **合并相关内容**：将同一话题下相邻的多个片段自然合并成连贯的段落，保持叙述的流畅性\n"
    "3. **修正明显错别字**：根据上下文修正转录过程中产生的明显错别字或同音字错误（如\"在\"→\"再\"，\"的\"→\"得\"等），但不要改变说话人的用词习惯\n"
    "4. **保持原文风格与信息**：保留说话人的表达方式、语气和叙述顺序。所有事实、数据、例子、结论、观点都必须完整保留。**不要改写或总结**，这是最重要的一点\n"
    "5. **模糊内容不强行推理**：对于转录中听不清、语义残缺的内容（ASR 错误、环境噪声、口齿不清等），只修正可确证的错别字；**无法准确辨识的内容保留原样或标注 `[转录不清]`，不要过度推断补全或脑补原文没有的信息**——不确定的内容宁可保留，也不要编造。\n"
    "5. **适度分段与标题**：根据话题的自然转换进行分段，为每个主题添加恰当的二级标题（`## 标题`）。标题应简洁反映该段的主题\n"
    "6. **去除时间戳**：转录文本中的时间戳（如 `00:00 - ` 格式）不需要保留在最终输出中\n"
    "7. **数学公式**（如有）：使用 LaTeX 语法（行内 `$...$`，块级 `$$...$$`）。如果原文没有数学公式，忽略本条\n"
"8. **适度利用 Markdown 排版**（只为增强可读性，绝不改变内容）：原文本身就是列表/表格/引用的内容，用对应 Markdown 元素呈现（列表 `-`、表格 `| ... |`、引用 `>`）；关键术语与数据可加粗 `**...**`；段落之间用空行分隔。**禁止为了排版而重组、改写或总结原文**——排版服务于忠实呈现原文，结构化重组是「笔记」产物的职责。"
)

_POLISH_OUTPUT = (
    "仅返回最终的 **Markdown 内容**。\n"
    "- **不要**将输出包裹在代码块中（例如：```markdown ```）\n"
    "- 这是一份\"整理版原文\"，不是\"笔记总结\"，请保持原汁原味的内容\n"
    "- 输出篇幅应接近原文长度（允许因去除无关内容而适度缩短）\n"
    "输出格式：第一行必须是文档标题（用 # 开头）。"
    "如果原文没有明确标题，请根据内容概括一个简洁标题（15字以内，不要「笔记」「整理」后缀）。"
)

# ═══════════════════════════════════════════════════════════════════════
# 视频/文本/音频 — NOTES（结构化笔记）section 定义
# ═══════════════════════════════════════════════════════════════════════

_NOTES_ROLE = "你是一个专业的笔记助手，擅长将转录内容整理成清晰、有条理且信息丰富的笔记。"

_NOTES_TASK = (
    "根据下面的转录内容，生成结构化的笔记，遵循以下原则：\n"
    "1. **完整信息**：记录尽可能多的相关细节，确保内容全面。\n"
    "2. **去除广告和无关内容**：\n"
    "   - 先判断意图：内容目的是「掏钱/关注/下载/扫码/加群」→ 广告，必须省略。目的是「讲知识/事实/观点」→ 保留。\n"
    "   - 硬广告：品牌口播、赞助声明、打赏诱导（\"一键三连\"等）、课程推广、APP导流。\n"
    "   - 软广告：伪装科普式植入（铺垫知识→引出产品→介绍参数→商业CTA）、体验式植入（\"我最近在用XX\"→产品体验→引导购买）、课程对比式植入（\"视频讲不完，课程里更系统\"→推销）。\n"
    "   - 片头片尾套话、无意义填充词（嗯、啊、那个、就是说等）。\n"
    "   - 下集/续集预告与催更引流话术（\"下个视频见\"\"先别急\"\"敬请期待\"\"关注下期\"）：纯吊胃口套话必须省略；若预告附带实质信息（如下集主题），最多一句带过，不作为独立章节。\n"
    "3. **保留关键细节**：保留重要事实、示例、结论和建议。\n"
    "4. **可读布局**：必要时使用项目符号，并保持段落简短，增强可读性。\n"
    "5. 数学公式必须保留，并以 LaTeX 语法形式呈现，适合 Markdown 渲染。行内公式使用 `$...$`，块级公式使用 `$$...$$`。严禁使用 `\\(...\\)` 或 `\\[...\\]` 分隔符。\n"
    "6. **充分利用 Markdown 排版**：不要只用列表平铺内容。按内容形态选择最合适的样式——对比性/条目性信息用表格（`| ... |`），关键论断、名言、引文用引用块（`>`），原始文本（帖子、信件、条款原文）用代码块，章节或话题之间用分割线（`---`）分隔，重点术语加粗。让排版层次分明、便于快速扫读。"
)

_NOTES_OUTPUT = (
    "仅返回最终的 **Markdown 内容**。\n"
    "- **不要**将输出包裹在代码块中（例如：```markdown ```）\n"
    "- 避免将编号标题（如\"1. **内容**\"）写成有序列表的格式，以免解析错误\n"
    "- 如果要加粗并保留编号，应使用 `1\\. **内容**`（加反斜杠），防止被误解析为有序列表\n"
    "输出格式：第一行必须是笔记标题（用 # 开头）。"
    "请根据内容概括一个简洁标题（15字以内，不要「笔记」后缀）。"
)

# ═══════════════════════════════════════════════════════════════════════
# 文档 — POLISH（整理版原文）section 定义
# ═══════════════════════════════════════════════════════════════════════

_DOC_POLISH_ROLE = "你是一个专业的文档编辑，擅长将文档转录产生的 Markdown 整理成规范、准确、易读的原文。"

_DOC_POLISH_TASK = (
    "根据下面的文档转录结果，进行**忠实于原文的整理**。请遵循以下原则：\n"
    "1. **修正表格错误**：pymupdf 识别的表格可能存在合并单元格错位、行列缺漏、内容错位等问题。请比对上下文修正表格结构，使其准确反映原文内容。输出规范的 Markdown 表格。\n"
    "2. **修正 OCR 错误**：纯图片或扫描页的 OCR 文字可能存在错别字（同音字、形近字）、乱序行、漏行等问题。只修正可确证的错误；**无法辨识的内容保留原样或标注 `[转录不清]`，不要过度推断补全或脑补**。\n"
    "3. **公式规范化**：将文档中的数学公式转为准确的 LaTeX 格式（行内公式用 `$...$`，块级公式用 `$$...$$`）。公式编号保留。\n"
    "4. **保留图片引用**：转录中的 `![...](data:image/...)` 图像引用必须完整保留，不要删除或修改。\n"
    "5. **保持结构**：保留原文的章节结构、标题层级和编号体系。适度调整不合理的分段，合并 OCR 导致的碎片行。\n"
    "6. **模糊内容不强行推理**：转录中不清晰、无法准确辨识的内容（低分辨率扫描、手写、乱码），保留可辨识部分并标注 `[转录不清]`，不要强行推断改写。\n"
"7. **适度利用 Markdown 排版**（只为增强可读性，绝不改变内容）：原文本身就是列表/表格/引用的内容，用对应 Markdown 元素呈现（列表 `-`、表格 `| ... |`、引用 `>`）；关键术语与数据可加粗 `**...**`；段落之间用空行分隔。**禁止为了排版而重组、改写或总结原文**——排版服务于忠实呈现原文，结构化重组是「笔记」产物的职责。"
)

_DOC_POLISH_OUTPUT = (
    "仅返回最终的 **Markdown 内容**。\n"
    "- **不要**将输出包裹在代码块中（例如：```markdown ```）\n"
    "- 这是一份\"整理版原文\"，不是总结或笔记\n"
    "- 输出篇幅应接近原文长度\n"
    "输出格式：第一行必须是文档标题（用 # 开头）。"
    "如果原文没有明确标题，请根据内容概括一个简洁标题（15字以内，不要「笔记」「整理」后缀）。"
)

# ═══════════════════════════════════════════════════════════════════════
# 网页 — POLISH（整理版原文）section 定义
# ═══════════════════════════════════════════════════════════════════════

_WEB_POLISH_ROLE = "你是一个专业的内容编辑，擅长将网页正文整理成干净、规范、易读的文章。"

_WEB_POLISH_TASK = (
    "根据下面的网页正文（已由提取工具转为 Markdown），进行**轻量级的排版整理**。"
    "请注意：你的任务是**整理原文**，不是总结，不是做笔记。"
    "请保持原文的完整信息和行文风格。\n\n"
    "具体要求：\n"
    "1. **清理排版残留与广告**：彻底删除以下非正文内容——\n"
    "   - 导航文字、侧边栏片段、评论区、\"分享到\"\"点赞\"等社交按钮文字\n"
    "   - 所有形式的广告和推广：横幅广告文案、弹窗内容、原生广告（文章中间插入的推广段落）、软文推广结尾（\"扫码了解更多\"\"点击领取\"\"限时优惠\"\"添加老师微信\"等）\n"
    "   - SEO 堆砌的关键词列表、重复的免责声明、版权声明模板文字\n"
    "   - \"相关阅读\"\"热门推荐\"\"广告\"\"推广\"标记后的内容块\n"
    "2. **规范化标题层级**：根据内容结构设置合理的标题层级（`#` → `##` → `###`），原文如果没有明确标题，根据内容概括\n"
    "3. **修正格式问题**：修复提取过程中产生的多余空行、断裂段落、错位的标点符号。将过长的段落适度拆分以提高可读性\n"
    "4. **保留关键元素**：保留原文中的图片引用、表格、列表、引用块（blockquote）。链接保留但无需特别处理\n"
    "5. **保持原文信息完整**：所有事实、数据、观点、结论必须完整保留。**不要改写或总结**\n"
    "6. **数学公式**（如有）：使用 LaTeX 语法（行内 `$...$`，块级 `$$...$$`）。如果没有公式，忽略本条\n"
"7. **适度利用 Markdown 排版**（只为增强可读性，绝不改变内容）：原文本身就是列表/表格/引用的内容，用对应 Markdown 元素呈现（列表 `-`、表格 `| ... |`、引用 `>`）；关键术语与数据可加粗 `**...**`；段落之间用空行分隔。**禁止为了排版而重组、改写或总结原文**——排版服务于忠实呈现原文，结构化重组是「笔记」产物的职责。"
)

_WEB_POLISH_OUTPUT = (
    "仅返回最终的 **Markdown 内容**。\n"
    "- **不要**将输出包裹在代码块中（例如：```markdown ```）\n"
    "- 这是一份\"整理版原文\"，不是总结或笔记\n"
    "- 输出篇幅应接近原文长度（允许因去噪而适度缩短）\n"
    "输出格式：第一行必须是文档标题（用 # 开头）。"
    "如果原文没有明确标题，请根据内容概括一个简洁标题（15字以内，不要「笔记」「整理」后缀）。"
)

# ═══════════════════════════════════════════════════════════════════════
# 文本 — POLISH（整理版原文）section 定义
# ═══════════════════════════════════════════════════════════════════════

_TEXT_POLISH_ROLE = "你是一个专业的文字编辑，擅长将用户提供的文本整理成格式规范、排版优美的文章。"

_TEXT_POLISH_TASK = (
    "根据下面的用户文本，进行**轻量级的格式整理**。"
    "请注意：你的任务是**整理原文**，不是总结，不是改写。"
    "请保持原文的完整信息、风格和语气。\n\n"
    "具体要求：\n"
    "1. **保留原文风格**：用户可能粘贴的是文章、笔记、对话记录或任意文本。尊重原文的行文风格和语气，不要强加新闻体、学术体等特定风格\n"
    "2. **适度格式化**：为内容添加合理的标题层级（`#` → `##`）和段落划分。如果原文已有结构，保持原结构\n"
    "3. **修正明显错误**：修正明显的错别字、标点错误和格式问题，但不改变用词习惯\n"
    "4. **保持完整**：所有内容必须完整保留，不要删减、概括或补充原文没有的信息\n"
    "5. **数学公式**：如有数学公式，使用 LaTeX 语法（行内 `$...$`，块级 `$$...$$`）\n"
"6. **适度利用 Markdown 排版**（只为增强可读性，绝不改变内容）：原文本身就是列表/表格/引用的内容，用对应 Markdown 元素呈现（列表 `-`、表格 `| ... |`、引用 `>`）；关键术语与数据可加粗 `**...**`；段落之间用空行分隔。**禁止为了排版而重组、改写或总结原文**——排版服务于忠实呈现原文，结构化重组是「笔记」产物的职责。"
)

_TEXT_POLISH_OUTPUT = (
    "仅返回最终的 **Markdown 内容**。\n"
    "- **不要**将输出包裹在代码块中（例如：```markdown ```）\n"
    "- 这是一份\"整理版原文\"，不是总结或笔记\n"
    "- 输出篇幅应接近原文长度\n"
    "输出格式：第一行必须是文档标题（用 # 开头）。"
    "如果原文没有明确标题，请根据内容概括一个简洁标题（15字以内，不要「笔记」「整理」后缀）。"
)

# ═══════════════════════════════════════════════════════════════════════
# 图片 — POLISH（整理版原文）section 定义
# ═══════════════════════════════════════════════════════════════════════

_IMAGE_POLISH_ROLE = "你是一个专业的 OCR 后处理编辑，擅长将 OCR 识别结果修正为准确、通顺的原文。"

_IMAGE_POLISH_TASK = (
    "根据下面的 OCR 识别结果，进行**忠实于原文的修正整理**。"
    "请注意：你的任务是**修正 OCR 错误并恢复原文结构**，不是总结。\n\n"
    "具体要求：\n"
    "1. **修正 OCR 错别字**：OCR 可能产生同音字、形近字、乱码字符等错误。只修正可确证的错别字；**无法辨识的乱码/残缺内容保留或标注 `[转录不清]`，不要强行推断补全或脑补**\n"
    "2. **重组段落**：OCR 输出的换行通常混乱（每个视觉行一个换行）。将属于同一段落的文字合并，恢复自然的段落结构\n"
    "3. **识别并格式化表格**：如果原文包含表格数据，尝试重建为 Markdown 表格\n"
    "4. **公式规范化**：如有数学公式，转为 LaTeX 格式（行内 `$...$`，块级 `$$...$$`）\n"
    "5. **保留所有信息**：OCR 识别的所有文字信息必须完整保留，不要删减或改写内容\n"
"6. **适度利用 Markdown 排版**（只为增强可读性，绝不改变内容）：原文本身就是列表/表格/引用的内容，用对应 Markdown 元素呈现（列表 `-`、表格 `| ... |`、引用 `>`）；关键术语与数据可加粗 `**...**`；段落之间用空行分隔。**禁止为了排版而重组、改写或总结原文**——排版服务于忠实呈现原文，结构化重组是「笔记」产物的职责。"
)

_IMAGE_POLISH_OUTPUT = (
    "仅返回最终的 **Markdown 内容**。\n"
    "- **不要**将输出包裹在代码块中（例如：```markdown ```）\n"
    "- 这是一份\"OCR 修正版原文\"，不是总结或笔记\n"
    "- 输出篇幅应接近原文长度\n"
    "输出格式：第一行必须是文档标题（用 # 开头）。"
    "如果原文没有明确标题，请根据内容概括一个简洁标题（15字以内，不要「笔记」「整理」后缀）。"
)

# ═══════════════════════════════════════════════════════════════════════
# 网页 — NOTES（结构化笔记）section 定义
# ═══════════════════════════════════════════════════════════════════════

_WEB_NOTES_ROLE = "你是一个专业的内容阅读助手，擅长将网页文章整理成清晰、有条理且信息丰富的笔记。"

_WEB_NOTES_TASK = (
    "根据下面的网页正文（已提取为 Markdown），生成结构化的笔记，遵循以下原则：\n"
    "1. **完整信息**：记录文章中的所有重要信息、论点、数据和结论。\n"
    "2. **层次结构**：使用标题层级（## → ###）组织内容，反映原文的逻辑结构。\n"
    "3. **关键细节**：保留重要事实、示例、数据引用和结论。\n"
    "4. **可读性**：适度使用项目符号，保持段落简短，增强可读性。\n"
    "5. **去除噪音**：忽略网页中的导航、侧边栏、评论区、广告等非正文内容。\n"
    "6. **数学公式**：如有公式，使用 LaTeX 语法（行内 `$...$`，块级 `$$...$$`）。\n"
    "7. **充分利用 Markdown 排版**：不要只用列表平铺内容。按内容形态选择最合适的样式——对比性/条目性信息用表格（`| ... |`），关键论断、名言、引文用引用块（`>`），原始文本（帖子、信件、条款原文）用代码块，章节或话题之间用分割线（`---`）分隔，重点术语加粗。让排版层次分明、便于快速扫读。"
)

_WEB_NOTES_OUTPUT = (
    "仅返回最终的 **Markdown 内容**。\n"
    "- **不要**将输出包裹在代码块中\n"
    "输出格式：第一行必须是笔记标题（用 # 开头）。"
    "请根据内容概括一个简洁标题（15字以内，不要「笔记」后缀）。"
)

# ═══════════════════════════════════════════════════════════════════════
# 文本 — NOTES（结构化笔记）section 定义
# ═══════════════════════════════════════════════════════════════════════

_TEXT_NOTES_ROLE = "你是一个专业的笔记助手，擅长将用户提供的文本整理成清晰、有条理且信息丰富的笔记。"

_TEXT_NOTES_TASK = (
    "根据下面的用户文本，生成结构化的笔记，遵循以下原则：\n"
    "1. **完整信息**：记录文本中的所有重要信息、论点、数据和结论。\n"
    "2. **层次结构**：使用标题层级（## → ###）组织内容，反映原文的逻辑结构。\n"
    "3. **关键细节**：保留重要事实、示例、数据和结论，不要删减关键信息。\n"
    "4. **可读性**：适度使用项目符号和列表，保持段落简短。\n"
    "5. **尊重原文风格**：用户文本可能是文章、随笔、对话等任意形式，保留其原有的表达风格和语气。\n"
    "6. **数学公式**：如有公式，使用 LaTeX 语法（行内 `$...$`，块级 `$$...$$`）。\n"
    "7. **充分利用 Markdown 排版**：不要只用列表平铺内容。按内容形态选择最合适的样式——对比性/条目性信息用表格（`| ... |`），关键论断、名言、引文用引用块（`>`），原始文本（帖子、信件、条款原文）用代码块，章节或话题之间用分割线（`---`）分隔，重点术语加粗。让排版层次分明、便于快速扫读。"
)

_TEXT_NOTES_OUTPUT = (
    "仅返回最终的 **Markdown 内容**。\n"
    "- **不要**将输出包裹在代码块中\n"
    "输出格式：第一行必须是笔记标题（用 # 开头）。"
    "请根据内容概括一个简洁标题（15字以内，不要「笔记」后缀）。"
)

# ═══════════════════════════════════════════════════════════════════════
# 图片 — NOTES（结构化笔记）section 定义
# ═══════════════════════════════════════════════════════════════════════

_IMAGE_NOTES_ROLE = "你是一个专业的内容提取助手，擅长从 OCR 识别结果中提取关键信息并整理成结构化的笔记。"

_IMAGE_NOTES_TASK = (
    "根据下面的 OCR 识别结果，生成结构化的笔记，遵循以下原则：\n"
    "1. **提取关键信息**：从 OCR 文字中提取所有有价值的信息、数据、论点。\n"
    "2. **修正 OCR 错误**：OCR 可能产生错别字、乱码，根据上下文推断修正。\n"
    "3. **层次结构**：使用标题层级（## → ###）组织内容。\n"
    "4. **保留表格数据**：如原文含表格，尽量重建为 Markdown 表格。\n"
    "5. **数学公式**：如有公式，使用 LaTeX 语法（行内 `$...$`，块级 `$$...$$`）。\n"
    "6. **充分利用 Markdown 排版**：不要只用列表平铺内容。按内容形态选择最合适的样式——对比性/条目性信息用表格（`| ... |`），关键论断、名言、引文用引用块（`>`），原始文本（帖子、信件、条款原文）用代码块，章节或话题之间用分割线（`---`）分隔，重点术语加粗。让排版层次分明、便于快速扫读。"
)

_IMAGE_NOTES_OUTPUT = (
    "仅返回最终的 **Markdown 内容**。\n"
    "- **不要**将输出包裹在代码块中\n"
    "输出格式：第一行必须是笔记标题（用 # 开头）。"
    "请根据内容概括一个简洁标题（15字以内，不要「笔记」后缀）。"
)

# ═══════════════════════════════════════════════════════════════════════
# 文档 — NOTES（结构化笔记）section 定义
# ═══════════════════════════════════════════════════════════════════════

_DOC_NOTES_ROLE = "你是一个专业的文档阅读助手，擅长将文档转录内容整理成清晰、有条理且信息丰富的笔记。"

_DOC_NOTES_TASK = (
    "根据下面的文档内容，生成结构化的笔记，遵循以下原则：\n"
    "1. **完整信息**：记录文档中的所有重要信息、论点、数据和结论。\n"
    "2. **层次结构**：使用标题层级（## → ###）组织内容，反映原文的逻辑结构。\n"
    "3. **关键细节**：保留重要事实、示例、数据表格和结论。表格数据应整理为 Markdown 表格。\n"
    "4. **可读性**：适度使用项目符号，保持段落简短，增强可读性。\n"
    "5. **公式保留**：文档中的数学公式必须以 LaTeX 语法形式呈现（行内 `$...$`，块级 `$$...$$`）。\n"
    "6. **充分利用 Markdown 排版**：不要只用列表平铺内容。按内容形态选择最合适的样式——对比性/条目性信息用表格（`| ... |`），关键论断、名言、引文用引用块（`>`），原始文本（帖子、信件、条款原文）用代码块，章节或话题之间用分割线（`---`）分隔，重点术语加粗。让排版层次分明、便于快速扫读。"
)

_DOC_NOTES_OUTPUT = (
    "仅返回最终的 **Markdown 内容**。\n"
    "- **不要**将输出包裹在代码块中\n"
    "- 避免将编号标题写成有序列表格式，如需加粗保留编号应使用 `N\\. **内容**`\n"
    "输出格式：第一行必须是笔记标题（用 # 开头）。"
    "请根据内容概括一个简洁标题（15字以内，不要「笔记」后缀）。"
)

# ═══════════════════════════════════════════════════════════════════════
# Excel — 数据摘要 section 定义
# ═══════════════════════════════════════════════════════════════════════

_EXCEL_ROLE = "你是一个专业的数据分析师，擅长提炼核心数据指标。"

_EXCEL_TASK = (
    "基于下面的数据摘要和样品，生成一份**数据摘要**。遵循以下原则：\n"
    "1. 📊 **数据规模**: 几行几列、几个 sheet、各字段数据类型\n"
    "2. 🔢 **核心指标**: 基于样品数据，提取 3-5 个值得关注的数值特征\n"
    "3. 📈 **关键发现**: 2-4 句话总结数据中透露的关键信息\n"
    "4. ⚠️ **需注意**: 缺失值、格式不一致等数据质量问题"
)

_EXCEL_HARD_CONSTRAINTS = (
    "⚠️ 严禁事项：\n"
    "- **严禁**重复输出完整数据 — 摘要不是数据副本\n"
    "- **严禁**逐行罗列每条记录"
)

_EXCEL_OUTPUT = (
    "仅返回最终的 **Markdown 内容**。\n"
    "- 使用简洁的列表和指标卡片风格\n"
    "- **不要**将输出包裹在代码块中"
)

# ═══════════════════════════════════════════════════════════════════════
# 格式选项（生成 hard_constraints 节内容）
# ═══════════════════════════════════════════════════════════════════════

def get_toc_format() -> str:
    return "**目录**: 自动生成一个基于 `##` 级标题的目录。目录只列标题文本，不包含任何时间戳或标记。"

def get_link_format() -> str:
    return "**原片跳转**: 在每个 ## 章节正文第一行插入时间戳标记 `*Content-[mm:ss]`，表示该章节对应的视频时间点。不要将标记放在标题行中，不要在目录中加此标记。"

def get_screenshot_format(has_visual: bool = False) -> str:
    if has_visual:
        return "**原片截图（必须执行）**: 每个 ## 章节末尾必须插入截图占位符 `*Screenshot-[mm:ss]`，时间戳从你看到的网格图左上角黄色数字中选取最相关的帧。即使某章节无明显的视觉内容，也应选取该时间段最接近的画面。"
    return "**原片截图（必须执行）**: 每个 ## 章节末尾必须插入截图占位符 `*Screenshot-[mm:ss]`，时间戳从转写文本中该章节对应的讲话时间中选取。选择最能代表该章节内容的时刻。"

def get_summary_format() -> str:
    return "在笔记末尾添加一个专业的 **AI 总结** 段落（## AI 总结），简要概括整个视频的核心内容。"


def get_chart_format(source_type: str) -> str:
    """图表格式指令：语义 JSON 规范 + 来源差异段。仅 notes 注入（chart 不在 _POLISH_FORMAT_FNS）。

    协议（2026-09-19 起）：LLM 只写「有哪些节点、怎么排」的语义 JSON，坐标/宽度/
    连线/标签位置全部由后端 chart_layout 算出来 —— 几何错误从结构上不可能发生，
    所以这里**不需要**任何坐标/样式/SVG 教学。这条减负方向正是 2026-08-16 的教训
    要求的：往 prompt 堆 28 行质量硬规则后，fast 模型（deepseek-v4-flash）宁可跳过
    不画，精简到 8 行才恢复产出率——**堆规则与出图率是直接 trade-off**。

    围栏：```fig（语义 JSON，本函数引导）/ ```chart（手写 SVG，legacy 兜底保留）。

    **九种全展开**（2026-09-19，用户明确接受 prompt 变长的代价）：9 种结构各给
    一行「适用场景 + 可直接改的最小 JSON」。只列名字不够——flash 模型对没见过的
    结构不会自己拼 schema，最后仍然全用示例里最眼熟的 hchain，「扩展类型」也就
    没达成。示例一律取**最小可用**（tree 只到两层、state 只含一条回环），
    够模型照改即可，不堆字段。

    ⚠️ 2026-08-16 的教训是「堆规则 ↔ 出图率是 trade-off」，故本题唯一的取舍
    是长度而非规则数：9 行示例是**引导**（告诉它有什么可画），不是**约束**
    （要求它必须画成什么样）。约束仍只有 4 条。
    """
    base = (
        "**图表**：有流程/层级/时间线/依赖关系时，用 ```fig 围栏输出语义 JSON"
        "（后端自动算坐标画图，**你不需要写任何坐标、尺寸或 SVG**）。"
        "每个围栏 = 1 张图，可按内容需要输出 1~N 张。\n"
        "- 九种结构，选最贴合的一种：\n"
        '  · 流程/步骤 → {"layout":"hchain","nodes":[{"label":"输入"},{"label":"清洗","focus":true}]}\n'
        '  · 层级/包含 → {"layout":"layers","layers":[{"name":"前端","nodes":[{"label":"UI"}]}]}\n'
        '  · 时间线/阶段 → {"layout":"timeline","nodes":[{"label":"开场","sub":"00:30"}]}\n'
        '  · 依赖/调用 → {"layout":"grid","rows":[["前端"],["接口层"]],'
        '"edges":[{"from":[0,0],"to":[1,0],"label":"调用"}]}\n'
        '  · 分类/组织/目录 → {"layout":"tree","root":{"label":"产品",'
        '"children":[{"label":"设计"},{"label":"研发"}]}}\n'
        '  · 带判定的流程 → {"layout":"flowchart","nodes":['
        '{"id":"a","label":"提交","type":"start"},{"id":"b","label":"通过?","type":"decision"}],'
        '"edges":[{"from":"a","to":"b"}]}（type: start/process/decision/end）\n'
        '  · 状态与回环 → {"layout":"state","states":['
        '{"id":"a","label":"新建","type":"start"},{"id":"b","label":"完成","type":"end"}],'
        '"transitions":[{"from":"a","to":"b"},{"from":"b","to":"a","label":"重来"}]}\n'
        '  · 交互时序 → {"layout":"sequence","actors":['
        '{"id":"u","label":"用户"},{"id":"s","label":"服务端"}],"messages":['
        '{"from":"u","to":"s","text":"请求"},{"from":"s","to":"u","text":"响应","dashed":true}]}'
        '（参与者 ≤5）\n'
        '  · 二维评估/优先级 → {"layout":"quadrant","x":["低","高"],"y":["低","高"],'
        '"quadrants":[{"name":"重要紧急","items":[{"label":"修故障"}]},{},{},{}]}'
        '（4 个象限按左上/右上/左下/右下顺序）\n'
        "- 约束：label ≤8 字；单个围栏的节点 ≤9 个，超过就拆成多个围栏；focus 最多 2 个；"
        "grid 的 [行号,列号] 从 0 起且必须指向 rows 里真实存在的节点；"
        "flowchart / state / sequence 一律用**字符串 id** 互相引用（不要用下标）。\n"
        "- 示例（照改文字）：\n"
        "```fig\n"
        '{"layout":"hchain","nodes":[{"label":"输入"},{"label":"清洗","focus":true},{"label":"输出"}]}\n'
        "```\n"
        "```fig\n"
        '{"layout":"tree","root":{"label":"产品","children":[{"label":"设计"},{"label":"研发"}]}}\n'
        "```\n"
        "- 无合适结构时输出 `> 图表说明：内容不适合图表化`。禁止输出坐标、像素或 SVG。"
    )
    # 来源差异段：视频是时间序列（先做什么/发生了什么）；文档是空间结构（谁包含谁/谁连接谁）。
    # 9 种全展开后它从「决定展开哪几种」变成「在九种里优先推荐哪几种」。
    if source_type in DOC_SOURCE_TYPES:
        diff = "tree（章节层级）· flowchart（处理流程）· layers · grid"
    elif source_type in ("webpage", "link"):
        diff = "hchain（操作步骤）· flowchart · grid"
    elif source_type == "video":
        diff = "timeline（时间点分阶段）· sequence（对话往来）· hchain"
    else:
        diff = "hchain · tree · grid"
    return f"{base}\n- 来源引导（优先这几类，其余按内容自选）：{diff}"


# ── 风格指令 ──
# ⚠️ 风格体系已迁移至 prompts/notes_styles.py

# DEPRECATED: 使用 VIDEO_NOTE_STYLES 代替（capabilities.py 仍消费）
STYLE_MAP = VIDEO_NOTE_STYLES

# DEPRECATED: 使用 DOC_NOTE_STYLES 代替（capabilities.py 仍消费）
DOC_STYLE_MAP = DOC_NOTE_STYLES

# ── Excel 数据摘要辅助函数 ──

def _parse_markdown_table(data_text: str) -> dict:
    """从 Markdown 表格文本中提取结构化摘要"""
    import re
    sheets: list[dict] = []
    current_sheet = {"name": "Sheet1"}
    sheets = [current_sheet]
    current_rows: list[list[str]] = []
    header: list[str] = []

    for line in data_text.split("\n"):
        m = re.match(r"^##\s+Sheet:\s*(.+)", line)
        if m:
            if header:
                current_sheet["rows"] = len(current_rows)
                current_sheet["cols"] = len(header)
                current_sheet["columns"] = header[:20]
                current_sheet["sample_rows"] = current_rows[:3] + (
                    current_rows[-3:] if len(current_rows) > 6 else [])
            current_sheet = {"name": m.group(1).strip()}
            sheets.append(current_sheet)
            current_rows = []
            header = []
            continue
        if not line.startswith("|") and not line.startswith("##"):
            if header:
                current_sheet["rows"] = len(current_rows)
                current_sheet["cols"] = len(header)
                current_sheet["columns"] = header[:20]
                current_sheet["sample_rows"] = current_rows[:3] + (
                    current_rows[-3:] if len(current_rows) > 6 else [])
            continue
        if re.match(r"^\|[\s\-:|]+\|$", line):
            if current_rows and not header:
                header = current_rows.pop()
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.split("|")[1:-1]]
            current_rows.append(cells)

    if current_sheet and header:
        current_sheet["rows"] = len(current_rows)
        current_sheet["cols"] = len(header)
        current_sheet["columns"] = header[:20]
        current_sheet["sample_rows"] = current_rows[:3] + (
            current_rows[-3:] if len(current_rows) > 6 else [])

    return {"total_sheets": len(sheets), "sheets": sheets}


def _build_data_summary(data_text: str) -> str:
    """将 Markdown 表格替换为结构化摘要"""
    info = _parse_markdown_table(data_text)
    parts: list[str] = []
    for sh in info["sheets"]:
        cols_str = ", ".join(sh.get("columns", [])[:20])
        parts.append(
            f"## {sh['name']}\n"
            f"共 {sh['rows']} 行 × {sh['cols']} 列\n"
            f"字段: {cols_str}\n"
        )
        if sh.get("sample_rows"):
            parts.append("数据样品（首尾各 3 行）：")
            cols = sh.get("columns", [])
            parts.append("| " + " | ".join(cols[:8]) + " |")
            parts.append("| " + " | ".join(["---"] * min(len(cols), 8)) + " |")
            for row in sh["sample_rows"]:
                parts.append("| " + " | ".join(row[:8]) + " |")
        parts.append("")
    return "\n".join(parts)


# ── Excel 风格 ──

EXCEL_STYLE_MAP = {
    "detailed": "**详细分析**: 完整的数据分析报告，包含所有维度的深入分析。",
    "minimal": "**简洁摘要**: 仅记录最重要的数据洞察和关键指标。",
    "business": "**商务报告**: 提炼商业价值、趋势和行动建议，适合汇报。",
}

def build_excel_summary_prompt(data_text: str, style: str = "detailed",
                                user_notes: str = "",
                                target_language: str = "zh-CN") -> str:
    """构建 Excel 数据摘要 prompt（模块化格式）"""
    style = normalize_style(style)
    style_text = EXCEL_STYLE_MAP.get(style, "") if style and style != "none" else ""

    try:
        data_summary = _build_data_summary(data_text)
    except Exception:
        data_summary = f"数据内容（Markdown 表格）：\n---\n{data_text}\n---"

    source_content = (
        f"数据摘要和样品：\n\n{data_summary}"
    )

    sections = [
        ("role", _EXCEL_ROLE),
        ("task", _EXCEL_TASK),
        ("source_content", source_content),
        ("instruction_priority", _INSTRUCTION_PRIORITY),
        ("output_language", _build_output_language(target_language, "xlsx")),
        ("hard_constraints", _EXCEL_HARD_CONSTRAINTS),
        ("style_preference", style_text),
        ("output_format", _EXCEL_OUTPUT),
        ("user_request", user_notes.strip()),
    ]
    return PromptComposer.compose(sections)


# ── 文档专用格式选项 ──

def get_formulas_format() -> str:
    return (
        "**公式集（必须执行）**: 在笔记末尾添加 ## 公式汇总 章节，"
        "将文档中出现的所有数学公式整理为独立列表。每个公式独立编号，附带简短说明。"
        "行内公式用 `$...$`，块级公式用 `$$...$$`。"
    )

def get_references_format() -> str:
    return (
        "**参考文献表（必须执行）**: 在笔记末尾添加 ## 参考文献 章节，提取文档中引用的所有文献。\n"
        "- 将全部引用放入一个 BibTeX 代码块（```bibtex ... ```）中，每个条目包含：作者、标题、期刊/会议、年份、DOI（如有）。\n"
        "- 代码块后可附一个简短的阅读列表（1-3 句话概括主要引用方向即可）。\n"
        "- **严禁**为每条引用单独创建子标题（###），也不要为每个条目单独分段。保持参考文献章节紧凑。"
    )

def get_images_format() -> str:
    return (
        "**文档图片（必须执行）**: 文档中的图片引用 `![描述](/api/files/...)` 必须保留在笔记中。"
        "为每张有信息量的图片（图表、示意图、照片）添加简要的文字描述（1-2 句），说明图片展示的内容。"
        "纯装饰性图片可以省略描述但保留引用。"
    )


# ── 可用选项（供 capabilities 端点 + 前端渲染）──

AVAILABLE_OPTIONS = {
    "video": ["toc", "link", "screenshot", "summary", "chart"],
    "audio": ["toc", "summary", "formulas"],
    "webpage": ["toc", "summary", "formulas", "chart"],
    "pdf": ["toc", "formulas", "references", "summary", "images", "chart"],
    "docx": ["toc", "summary", "images", "formulas", "chart"],
    "pptx": ["toc", "summary", "images", "formulas", "chart"],
    "xlsx": [],
    "text": ["toc", "summary", "formulas", "chart"],
}

# ── 格式函数映射 ──

_ALL_FORMAT_FNS = {
    "toc": get_toc_format,
    "link": get_link_format,
    "screenshot": get_screenshot_format,
    "summary": get_summary_format,
    "formulas": get_formulas_format,
    "references": get_references_format,
    "images": get_images_format,
    "chart": get_chart_format,
}

# ── Polish 允许的格式选项（仅结构增强和编辑行为）──

_POLISH_FORMAT_FNS = {
    "toc": get_toc_format,
    "link": get_link_format,
    "screenshot": get_screenshot_format,
    "formulas": get_formulas_format,
    "references": get_references_format,
    "images": get_images_format,
}

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


def _build_hard_constraints(
    formats: list[str] | None,
    format_fns: dict,
    video_understanding: bool = False,
    source_type: str = "text",
) -> str:
    """构建 hard_constraints 节文本：仅包含该 prompt 类型允许的格式选项"""
    if not formats:
        return ""
    parts = []
    for f in formats:
        fn = format_fns.get(f)
        if not fn:
            continue
        if f == "screenshot":
            parts.append(fn(has_visual=video_understanding))
        elif f == "chart":
            parts.append(fn(source_type))
        else:
            parts.append(fn())
    if not parts:
        return ""
    return "\n".join(parts)


def build_polish_prompt(
    raw_text: str, title: str = "", style: str = "detailed",
    source_type: str = "text",
    formats: list[str] | None = None,
    user_notes: str = "",
    target_language: str = "zh-CN",
    video_understanding: bool = False,
) -> str:
    """构建整理版原文 prompt（模块化格式，无 style 节）"""
    is_doc = source_type in DOC_SOURCE_TYPES

    # ── Polish 不再注入 style 指令 —— 固定忠实整理策略 ──
    if style and style not in ("detailed", "none"):
        logger.warning(
            "Polish style 参数已废弃，当前固定为忠实整理策略，忽略: %s", style)

    # ── Polish 不接受用户备注 —— 忠实整理任务，不受意图/指令干扰 ──
    # user_notes 参数保留仅作 API 兼容（调用方统一传），内部忽略不注入 <user_request> 节。
    _ = user_notes

    if source_type == "webpage":
        role, task, output = _WEB_POLISH_ROLE, _WEB_POLISH_TASK, _WEB_POLISH_OUTPUT
        source = raw_text
        source_label = f"网页标题：{title or '未命名'}\n网页正文"
    elif source_type == "image":
        role, task, output = _IMAGE_POLISH_ROLE, _IMAGE_POLISH_TASK, _IMAGE_POLISH_OUTPUT
        source = raw_text
        source_label = "图片 OCR 识别结果"
    elif source_type == "text":
        role, task, output = _TEXT_POLISH_ROLE, _TEXT_POLISH_TASK, _TEXT_POLISH_OUTPUT
        source = raw_text
        source_label = "用户提供的文本内容"
    elif is_doc:
        role, task, output = _DOC_POLISH_ROLE, _DOC_POLISH_TASK, _DOC_POLISH_OUTPUT
        source = raw_text
        source_label = "文档转录内容"
    else:
        # video, audio 及兜底 — 转录类内容
        role, task, output = _POLISH_ROLE, _POLISH_TASK, _POLISH_OUTPUT
        source = _format_segments(raw_text)
        source_label = f"视频标题：{title or '未命名'}\n转录文本"

    source_content = f"{source_label}：\n{source}"

    constraints = _build_hard_constraints(
        formats, _POLISH_FORMAT_FNS, video_understanding, source_type)

    sections = [
        ("role", role),
        ("task", task),
        ("source_content", source_content),
        ("instruction_priority", _INSTRUCTION_PRIORITY),
        ("output_language", _build_output_language(target_language, source_type)),
        ("hard_constraints", constraints),
        ("output_format", output),
        # 无 user_request —— Polish 不接受用户备注（忠实整理）
    ]
    return PromptComposer.compose(sections)


def _format_reference_texts(reference_texts: list[dict] | None) -> str:
    """参考文件列表 → reference section 文本。逐项标来源，多段用分隔符；空返回 ""。

    注入语义：参考资料仅作背景（来源 + 内容），与原文冲突时以原文/模型判断为准。
    """
    if not reference_texts:
        return ""
    parts = []
    for ref in reference_texts:
        name = ref.get("name", "未命名") if isinstance(ref, dict) else "未命名"
        content = ref.get("content", "") if isinstance(ref, dict) else ""
        if not content.strip():
            continue
        parts.append(
            f"用户提供的参考资料（来源：{name}；仅供背景参考，"
            f"与原文冲突时以原文/模型判断为准）：\n{content}"
        )
    return "\n\n---\n\n".join(parts)


def build_notes_prompt(
    raw_text: str, title: str = "", style: str = "detailed",
    source_type: str = "text",
    formats: list[str] | None = None,
    user_notes: str = "",
    target_language: str = "zh-CN",
    video_understanding: bool = False,
    reference_texts: list[dict] | None = None,
) -> str:
    """构建结构化笔记 prompt（模块化格式，含 style 节）"""
    is_doc = source_type in DOC_SOURCE_TYPES

    if source_type == "webpage":
        role, task, output = _WEB_NOTES_ROLE, _WEB_NOTES_TASK, _WEB_NOTES_OUTPUT
        source = raw_text
        source_label = f"网页标题：{title or '未命名'}\n网页正文"
    elif source_type == "image":
        role, task, output = _IMAGE_NOTES_ROLE, _IMAGE_NOTES_TASK, _IMAGE_NOTES_OUTPUT
        source = raw_text
        source_label = "图片 OCR 识别结果"
    elif source_type == "text":
        role, task, output = _TEXT_NOTES_ROLE, _TEXT_NOTES_TASK, _TEXT_NOTES_OUTPUT
        source = raw_text
        source_label = "用户提供的文本内容"
    elif is_doc:
        role, task, output = _DOC_NOTES_ROLE, _DOC_NOTES_TASK, _DOC_NOTES_OUTPUT
        source = raw_text
        source_label = f"文档标题：{title or '未命名'}\n文档转录内容"
    else:
        # video, audio 及兜底 — 转录类内容
        role, task, output = _NOTES_ROLE, _NOTES_TASK, _NOTES_OUTPUT
        source = _format_segments(raw_text)
        source_label = f"标题：{title or '未命名'}\n转录内容"

    source_content = f"{source_label}：\n{source}"

    # 风格指令
    style_text = ""
    if style and style != 'none':
        style = normalize_style(style)
        lookup = DOC_NOTE_STYLES if is_doc else VIDEO_NOTE_STYLES
        style_text = lookup.get(style, "")

    # 格式选项
    available = AVAILABLE_OPTIONS.get(source_type, AVAILABLE_OPTIONS.get("text", []))
    format_fns = {f: _ALL_FORMAT_FNS[f] for f in available if f in _ALL_FORMAT_FNS}
    constraints = _build_hard_constraints(
        formats, format_fns, video_understanding, source_type)

    sections = [
        ("role", role),
        ("task", task),
        ("source_content", source_content),
        ("instruction_priority", _INSTRUCTION_PRIORITY),
        ("output_language", _build_output_language(target_language, source_type)),
        ("hard_constraints", constraints),
        ("style_preference", style_text),
        ("output_format", output),
        ("reference_texts", _format_reference_texts(reference_texts)),
        ("user_request", user_notes.strip()),
    ]
    return PromptComposer.compose(sections)

