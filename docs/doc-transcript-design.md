# Doc 转录与产物设计（原始提案）

> **状态**: 原始设计提案。实际实现已偏离，见下方标注。

## 当前状态（2026-07-08）

✅ 已实现：5 种文档类型各有独立的引擎和子场景拆分（`doc:pdf`/`doc:word`/`doc:ppt`/`doc:excel`/`doc:epub`，2026-07-31 起旧 `doc:text+image` 等 key 已统一）。
实际引擎选型见 [engine-standards.md](../design/engine-standards.md) 与 [architecture.md](architecture.md) Layer 1，非本文档中的提案。

本文档中仍有效的部分：Word/PPT 的提取策略（document-flow / slide-by-slide）、EPUB 章节处理方案。

## 原始提案（2026-07-01）

以下为原始设计提案。当时所有文档类型在导入时统一映射为 `sourceType="pdf"`，生成时用同一套 PDF prompt。Word/PPT/Excel/EPUB 的转录与产物设计尚未开始。

PDF 是唯一完整的参考基准。

### PDF 当前链路（基准）

```
导入 PDF → DocumentAdapter
  → PyMuPDF 提取：文字层 + find_tables()→MD table + extract_image()→base64 嵌入
  → 扫描件检测(无文字层>50%) → _ocr_pdf_with_images() 逐页渲染+OCR+嵌入原图
  → 存 RawTranscript (sourceType="pdf")
  → 前端 3 tab: 笔记 / 整理版原文 / 转录(MD渲染)
```

**生成 prompt 关注点**：表格修正、OCR错字修正、公式LaTeX化、保留图片引用
**风格**：IMRaD / 商务要点 / 教程手册 / 会议纪要 / 全文精要
**格式**：目录 / 翻译 / 公式集 / 参考文献 / AI总结

---

## 按文档类型分场景推演

### Word (.docx) — 详细设计 ✏️

**典型场景**：工作报告、合同/协议、论文草稿、方案书、技术文档

#### 转录引擎：python-docx

**选型理由**：MarkItDown 对 Word 图片提取是断的（mammoth 产出的 img src 指向 ZIP 内部路径），表格合并单元格丢失。python-docx 直接读 OOXML，文字/表格/图片全可控。

python-docx 能力矩阵：

| 能力 | python-docx | MarkItDown（对比） |
|------|-----------|-------------------|
| 段落文字 | ✅ 含样式（粗体/斜体/字号/颜色） | ✅ |
| 标题层级 | ✅ 通过 paragraph.style 判断 | ⚠️ 扁平化映射 |
| 表格 | ✅ 精确提取，含合并单元格信息 | ❌ 合并单元格断裂 |
| 内嵌图片 | ✅ 可提取 binary blob → base64 | ❌ 引用断裂 |
| 页眉页脚 | ⚠️ 可读但难定位归属 | ❌ |
| 列表（有序/无序） | ✅ 编号和层级 | ⚠️ 基本支持 |
| 分页/分节 | ⚠️ 可检测分页符 | ❌ |
| 文本框 | ⚠️ 单独元素，不参与段落流 | ❌ |
| 脚注/尾注 | ✅ | ❌ |
| .doc 老格式 | ❌ 只支持 .docx | ❌ |
| 新增依赖 | python-docx（~5MB） | — |

#### 转录产物结构：对标 PDF 的 PyMuPDF 输出

PDF 产物模式是 `文字层 → 表格 → 图片`，按页组织。Word 没有「页面」概念，按**文档流顺序**组织：

```
适配器元数据块（标题/作者/创建时间等，如果有）
  ↓
遍历 document.body 的块级元素（按出现顺序）：
  ├─ 标题段落 → ## / ### / ...
  ├─ 正文段落 → 保留粗体/斜体/链接
  ├─ 表格 → Markdown table（含合并单元格标注）
  ├─ 内嵌图片 → ![alt](data:image/...;base64,...)
  ├─ 列表 → - / 1. / a.
  └─ 分页符 → ---（可选，取决于是否需要页边界）
```

**关键决策**：
- Word 不分页（没有 Page N 标记），用标题层级天然分段
- 合并单元格用 `colspan`/`rowspan` 在表格上方标注（Markdown 不支持合并单元格，需要标注信息）
- 文本框放在独立段落，加 `**[文本框]**` 前缀区分
- 页眉页脚提取后放在首尾，加 `> 页眉: ...` 引用块标记

#### 产物设计

| 维度 | 方案 |
|------|------|
| 转录视图 | 完整 MD 渲染（对标 PDF 转录 tab —— 文字+表格+图片，不截断） |
| 整理版原文 | 去 python-docx 转换噪音（多余空行/断段落）+ 修正标题层级 + 保留表格和图片 |
| 结构化笔记 | 提取关键信息、决策点、待办事项、数据结论；表格数据转文字摘要 |
| 风格 | 商务要点 / 全文精要 / 会议纪要 |
| 格式选项 | 目录、AI 总结 |

#### 与 PDF 的对比

| 维度 | PDF (PyMuPDF) | Word (python-docx) |
|------|--------------|-------------------|
| 转录产出 | 文字+表格+base64图，按 `## Page N` | 文字+表格+base64图，按文档流 |
| 页码概念 | 有（关键结构锚点） | 无（标题层级替代） |
| 表格 | find_tables()→MD table | 直接读 OOXML 表格→MD table |
| 扫描件处理 | 需要 OCR | 不涉及 |
| 公式 | 自然语言描述或图片 | OMML 公式可检测但难转换 |
| 整理版原文关注点 | 表格修正+OCR修正+公式LaTeX化 | 段落还原+标题层级修正+表格格式化 |
| 参考文献 | 有（PDF 论文场景） | 有（Word 论文场景，需检测） |
| 风格 | IMRaD/商务/教程/会议/精要 | 商务/会议/精要 |
| 格式选项 | toc/translate/formulas/references/summary | toc/summary（可选的 references） |

#### python-docx 引擎实现概要

对标 `backend/app/engines/doc.py` 中 `PyMuPDFEngine` 的结构：

```python
class DocxEngine(Engine):
    """python-docx — Word .docx 转录为 Markdown
    
    对标 PyMuPDF：文字层 + 表格 + 内嵌图片 → 富 MD
    """
    capabilities = ["text", "table", "image", "office"]
    
    def execute(self, file_path: str) -> DocResult:
        from docx import Document
        doc = Document(file_path)
        parts: list[str] = []
        
        for block in iter_blocks(doc):  # 按文档流遍历
            if is_heading(block):
                parts.append(f"{'#' * block.level} {block.text}")
            elif is_table(block):
                parts.append(table_to_markdown(block))
            elif is_image(block):
                parts.append(f"![{block.alt}](data:{block.mime};base64,{block.b64})")
            elif is_paragraph(block):
                parts.append(format_paragraph(block))  # 保留粗体/斜体
            # ... 列表、文本框、页眉页脚
        
        return DocResult(
            text="\n\n".join(parts),
            pages=len(doc.sections),  # 用节数近似
            engine="python-docx",
        )
```

引擎注册为 `doc` 类别，name=`"python-docx"`。在 `doc:office` 子场景绑定中可选择。

#### 待定问题

1. OMML 公式：检测到但不转换（和 PDF 一样交给 LLM），还是用 omath 库尝试转 LaTeX？
2. 页眉页脚的归属：放在文档首尾？还是插入到对应分节位置？
3. .doc 老格式：是否在 accept() 时拒绝，还是用 LibreOffice 做个无声转换？

---

### PPT (.pptx)

**典型场景**：演示文稿、培训课件、路演材料、会议 slides

**转录链路**：
- 引擎：MarkItDown（当前唯一）
- 产物：按幻灯片页的文本（标题 + 正文 + 备注），不保留动画/切换
- 特点：天然「页」结构，每页有标题+要点；内容呈要点式而非段落式

**产物设计方向**：

| 维度 | 方案 |
|------|------|
| 转录视图 | 按 Slide 组织的 MD（`## Slide N` + 要点列表） |
| 整理版原文 | 合并碎片化要点为连贯段落 + 保持页序 + 提取演讲者备注为上下文 |
| 结构化笔记 | 按主题重组（slides 线性但逻辑可能跳跃）+ 提取核心观点/数据/案例 |
| 风格 | 全文精要 / 商务要点 / 教程手册 |
| 格式选项 | 目录、AI 总结 |

**与 PDF 的关键差异**：
- 转录是「要点式」而非「段落式」——笔记需要从要点中重建逻辑链
- 不涉及表格修正/OCR/公式
- 演讲者备注是额外的上下文来源（需确认 MarkItDown 是否提取）

---

### Excel (.xlsx)

**典型场景**：数据表、财务报表、价格清单、分析结果

**转录链路**：
- 引擎：MarkItDown（当前唯一）
- 产物：每个 sheet → 一张 MD table
- 特点：纯数据，无叙述文字；大表格可能非常长；数据透视表/图表/公式全部丢失

**产物设计方向**：

| 维度 | 方案 |
|------|------|
| 转录视图 | 按 sheet 组织的 MD table |
| 整理版原文 | 表格规范化 + 数据完整性标注 |
| 结构化笔记 | 数据分析摘要：关键指标、趋势、异常值、数据关系描述 |
| 风格 | 数据摘要 / 详细（完整数据保留） |
| 格式选项 | AI 总结 |

**与 PDF 的关键差异**：
- 「笔记」本质是数据分析结果，不是文字笔记
- 需要特殊的 prompt 让 LLM 理解表格数据并生成文字描述
- 「整理版原文」可能是规范化后的表格
- 大表格可能超出 LLM 上下文窗口

---

### EPUB (.epub)

**典型场景**：电子书、技术手册、长篇文档

**转录链路**：
- 引擎：MarkItDown（当前唯一）
- 产物：提取 HTML→MD，保留标题层级和章节结构
- 特点：内容量大，天然有章节结构；内容量常超 LLM 上下文窗口

**产物设计方向**：

| 维度 | 方案 |
|------|------|
| 转录视图 | 完整 MD 渲染（保留章节 + 图片），可能很长 |
| 整理版原文 | 保留章节结构 + 去格式噪音 + 保留图片引用 |
| 结构化笔记 | 按章节的阅读笔记：每章核心内容、关键概念、引用 |
| 风格 | 全文精要 / 教程手册 / 精简（章节摘要） |
| 格式选项 | 目录（天然有）、AI 总结 |

**与 PDF 的关键差异**：
- 内容量可能远超 LLM 上下文窗口——需要分章处理（chunking）
- 天然有目录结构，不需要 LLM 生成
- 「笔记」更像读书笔记而非会议纪要

---

## 五类文档差异总览

| 维度 | PDF | Word | PPT | Excel | EPUB |
|------|-----|------|-----|-------|------|
| 转录引擎 | PyMuPDF | **python-docx** | MarkItDown | MarkItDown | MarkItDown |
| 转录产物形态 | 文字+表格+图片 MD | 文字+表格+图片 MD | 按页要点 MD | 按sheet表格 MD | 按章节 MD |
| 转录主要问题 | OCR错字/表格错位 | 断段/标题层级不准 | 要点碎片化 | 纯数据无叙述 | 内容量巨大 |
| 内容组织 | 按 Page N | 按文档流（标题层级） | 按 Slide N | 按 sheet | 按章节 |
| 笔记风格 | IMRaD/商务/教程/会议 | 商务/会议/精要 | 商务/教程/精要 | 数据摘要/详细 | 精要/教程/精简 |
| 整理版原文重点 | 表格修正+OCR修正+公式 | 段落还原+标题修正+表格格式化 | 要点合并+备注 | 表格规范化 | 章节保留+去噪音 |
| 特殊格式选项 | 公式集/参考文献 | toc/summary | — | — | 天然TOC |
| 上下文超限风险 | 低 | 低 | 低 | 中（大表格） | 高 |

---

## 已决/待定问题

| # | 问题 | 状态 |
|---|------|------|
| 1 | Word 转录引擎 | ✅ python-docx（对标 PyMuPDF 结构） |
| 2 | Word OMML 公式：检测到不转换 vs 用 omath 转 LaTeX？ | ⏳ 待定 |
| 3 | Word 页眉页脚：放首尾 vs 插入对应分节位置？ | ⏳ 待定 |
| 4 | Word .doc 老格式：拒绝 vs LibreOffice 无声转换？ | ⏳ 待定 |
| 5 | PPT 的演讲者备注：MarkItDown 是否提取？ | ⏳ 待探索 |
| 6 | Excel 的大表格处理：超上下文窗口时如何截断/摘要？ | ⏳ 待探索 |
| 7 | EPUB 的分章处理：导入时自动拆分 vs 生成时动态分块？ | ⏳ 待探索 |
| 8 | PPT/Excel/EPUB 是否需要各自专用引擎（像 Word 用 python-docx）？ | ⏳ 待各自详细设计时讨论 |
