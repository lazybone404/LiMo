# 引擎评审记录

> **2026-07-30 更新**：引擎已从 AGPL（pymupdf4llm/pymupdf）迁移到 MIT/BSD（docling/pypdfium2），PDF 引擎已全部替换。以下 PDF 引擎章节（7/6 原始评审）保留为历史参考。

## 评判标准

2026-07-06 讨论确定：判断引擎好坏的唯一标准是**职责是否兑现**。

每个引擎通过 `self.capabilities` 声明自己能做什么：
- `text` = 提取文字，按阅读顺序排列，标题层级保留
- `table` = 表格正确转 MD，行列不混乱，不把非表格识别为表格
- `image` = 嵌入位图全量提取
- `pdf` / `office` = 对应格式的支持能力

宣称了就**必须做到**，做不到就该从 capabilities 里移除。这是及格线。

## PDF 引擎评审（⚠️ 历史记录 — 引擎已全部替换）

> 以下为 2026-07-06 原始评审。pymupdf4llm/pymupdf/pdf-ocr 已从注册表中移除（AGPL 协议风险 + 代码死沉），由 docling/docling-text/pypdfium2（全部 MIT/BSD）替代。
> 当前活跃引擎见 [STATUS.md](../docs/STATUS.md)。

引擎注册名称：~~`pymupdf4llm` `pymupdf` `pymupdf-text` `pdf-ocr`~~ → 现为 `docling` `docling-text` `pypdfium2` `markitdown`

### 测试数据

DSpark_paper.pdf（学术论文，33 页，双栏 + 矢量图 Figure 1 + 嵌入位图 ×6）

| 引擎 | capabilities | 文字 | 嵌入图 | 矢量图 | 真表格 | 假表格 | 判定 |
|------|-------------|------|--------|--------|--------|--------|------|
| pymupdf4llm | text,table,**image**,pdf | 108,579 | 6 | **26** | 11 | 0 | ✅ 图片职责全兑现 |
| pymupdf（修复前） | text,table,**image**,pdf | 104,880 | 6 | 6 | 5 | **7** | ❌ 矢量截取污染文字 |
| pymupdf（修复后） | text,table,**image**,pdf | — | 6 | 0 | — | 0 | ✅ 只做嵌入图 |
| pymupdf-text | text,table,pdf | 104,503 | 0 | 0 | 5 | 0 | ✅ 不碰图片，表格干净 |

### 决策

**pymupdf4llm → 默认**。内置版面分析，矢量图和文字分离处理，不互相污染。

**pymupdf → 保留。移除矢量图截取代码**。嵌入位图提取正常（6 张），但 `cluster_drawings` 把 Figure 1 碎片文字误识为 7 个假表格，且与 pymupdf4llm 职责重叠。修复后只保留 `page.get_images()` 嵌入图提取。

**pymupdf-text → 保留**。职责清晰：纯文字+表格。不做图片，所以不会有图片问题。速度最快。

**pdf-ocr → 待评审**。扫描件逐页 OCR，需补充测试。

**markitdown → 保留兜底**。PDF 场景不作为主力。

### 代码变更（历史）

- ~~删除 `doc.py` 中 `PyMuPDFEngine.execute()` 内的矢量图截取代码块~~ → `doc.py` 已于 2026-07-30 完全删除，拆分为 `doc_types.py` + `doc_markitdown.py` + `doc_docx.py` + `doc_pptx.py` + `doc_excel.py`
- `config.yaml`: ~~`doc:text+image = pymupdf4llm`~~ → 现为 `doc:text+image = pypdfium2`（docling 作为重试回退）

---

## DOCX 引擎评审

引擎注册名称：`markitdown` `python-docx` `python-docx-text`

### 测试数据

选拔赛作品设计文档_格式优化版.docx

| 引擎 | 字数 | H1/H2/H3 | 图片 | 表格 | 列表 | 判定 |
|------|------|---------|------|------|------|------|
| markitdown | 20,586 | 2/9/20 | 12 | 9 | 67 | ✅ |
| python-docx | 19,705 | 2/9/20 | 12 | 9 | 70 | ✅ |
| python-docx-text | 19,417 | — | 0 | 9 | — | ✅ |

### 决策

**markitdown → 默认**。效果与手写引擎持平，微软官方维护。

**python-docx / python-docx-text → 保留备选**。与 markitdown 效果持平，保留给用户更多选择。

### 代码变更

- `MarkItDownEngine.execute()` 增加 `media_dir` 处理：将内嵌 base64 图片写出为文件
- `config.yaml`: `doc:word = markitdown`

---

## PPTX 引擎评审

引擎注册名称：`markitdown` `python-pptx` `python-pptx-text`

### 测试数据

项目ppt.pptx（27 张幻灯片，图文混合）

| 引擎 | 字数 | 图片 | 判定 |
|------|------|------|------|
| markitdown | 7,205 | **33** | ✅ Slide 分隔清晰 |
| python-pptx | 4,575 | **1** | ❌ 图片提取严重不足 |
| python-pptx-text | 4,545 | 0 | ✅ 仅文字，不加图片 |

### 决策

**markitdown → 默认**。33 图 vs 1 图，差距太大。`<!-- Slide number: N -->` 分隔符合业界惯例。

**python-pptx → 降级备选**。图片能力不足，但文字+备注提取可作为兜底。

### 代码变更

- `config.yaml`: `doc:ppt = markitdown`
- **移除 `python-pptx` 的 `image` capability**（实测仅 1 张图，不及格）

---

## 导出可靠性标准

### 原则

导出产物必须**自包含**——打开即正常显示，不依赖 LiMo 服务器。

### 图片处理

三种模式：**不包含**（`/api/files/...` 原样）· **独立文件夹**（`images/` + 相对路径）· **base64 内嵌**（data URI 单文件）。

导出结构（与 Obsidian / Typora / VS Code 风格一致）：

```
导出文档名/
├── 导出文档名.md          ← 主文件
└── images/
    ├── img_0000.png
    └── img_0001.png
```

MD 内引用：`![](images/img_0000.png)`

| 导出格式 | 图片方案 | 说明 |
|---------|---------|------|
| MD（快速） | 不包含，保持 `/api/files/...` | 粘贴回 LiMo 可用 |
| MD（ZIP） | 文件夹 + 相对路径 / base64 内嵌 / 不包含 | 用户可选 |
| HTML | base64 内嵌（快速）/ 文件夹+相对路径（ZIP） | 自包含·零 CDN·4 主题 |
| 思维导图 | markmap 交互式 HTML + SVG | 与预览栏渲染一致·暗色自适应 |

### 公式处理（已完成）

- HTML：KaTeX 服务端预渲染 + 内联 CSS/JS 兜底，零 CDN ✅
- MD：保留 $...$ 原始格式
- 思维导图 HTML：markdown 清洗掉公式（markmap 不渲染数学）

### 实施状态

- ✅ 导出 MD 实现文件夹打包 + 相对路径
- ✅ 导出 HTML 图片可选内嵌 + 零 CDN
- ✅ 思维导图交互式 HTML + SVG 导出
- ✅ 图片收集/重写/内联核心函数
- ✅ 重名图片自动加 _N 后缀
- ✅ 暗色模式自适应（`@media (prefers-color-scheme: dark)`）
- ✅ 四种 HTML 主题（LiMo 预览栏 / 学术论文 / GitHub / 极简）

---

---

## XLSX 引擎评审

引擎注册名称：`openpyxl` `markitdown`

### 职责定义

XLSX 引擎只做一件事：打开文件，把表格数据完整转成 MD table。超出引擎职责的交给其他环节。

| 职责 | 归属 |
|------|------|
| 合并单元格填平对齐 | 引擎 |
| 空白行标注（不拆分） | 引擎 |
| 全量提取，不截断 | 引擎 |
| 多 Sheet 分隔 | 引擎 |
| 最大行数限制 | 生成选项 |
| 分析报告 / 数据摘要 | LLM 生成 |
| 是否拆分独立表 | 用户 / AI 判断 |

### 决策

**不主动拆分** — 空行拆分可能把一张报表切碎（内部视觉分隔），引擎不做猜测。用 HTML 注释标注空白行位置，由 AI 或用户判断。

**保留 openpyxl 为默认**。190 行代码，职责简单，不需要替换。

### 代码变更

- 添加空白行注释标注（`<!-- 以下 N 行空白 -->`）

---

## EPUB 引擎

**状态：未测试，暂不讨论。** 需补充 EPUB 测试文件并实际导入对比 `ebooklib` vs `markitdown`。

---

---

## ASR 引擎评审

引擎注册名称：`dashscope-asr` `faster-whisper-medium` `faster-whisper-small`

### 职责

音频 → 带时间戳的分段文本（JSON `{segments: [{start, end, text}]}`）。不涉及文档图片提取。

### 现有数据

| 视频 | 引擎 | 时长 | 分段 | 质量 |
|------|------|------|------|------|
| 诋毁袁隆平 | faster-whisper | 451s | 73段 | ✅ 中文准确连贯 |
| AI 复刻网页 | faster-whisper | 204s | 27段 | ✅ |

### 决策

**保持现状**。降级链 dashscope-asr → faster-whisper-medium → faster-whisper-small 设计合理：
云端优先（中文最佳），本地兜底。引擎职责单一——转写就是转写，不跨界做别的。

**未测试**：dashscope-asr 云端引擎。需配置 API Key 后对比。

---

## OCR 引擎

**不在讨论范围**。OCR 走图片处理管道（`POST /process-image`），不是导入转录层。不属于引擎评审范围。

---

## 当前默认绑定

> 2026-07-30 最新，详见 `__init__.py` 中 `_DOC_DEFAULTS`。

```
doc:text+image: pypdfium2 (回退: docling)
doc:text-only:  pypdfium2
doc:scan-ocr:   docling
doc:word:       python-docx
doc:ppt:        python-pptx
doc:excel:      openpyxl
doc:epub:       ebooklib
asr:            bcut-asr
ocr:            tesseract
```

引擎讨论覆盖了全部 5 种文档格式（PDF/DOCX/PPTX/XLSX/EPUB）。

## 待实施

- [ ] EPUB 引擎测试（需补充测试文件）
- [ ] VL 帧过滤（成本/收益不成正比 — 暂不做，见 CLAUDE.md）
