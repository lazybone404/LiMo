/**
 * 静态演示站的假数据。
 *
 * ⚠️ **只在 `VITE_DEMO=1` 构建里被加载**（`main.tsx` 动态 import），正式包不含这些内容。
 * 形状必须与后端真实响应一致——否则演示站里会出现「本地正常、线上空白」的偏差，
 * 而那种偏差看起来像 bug，实际是数据造错了。字段来源：`api/core.ts` 的
 * `DocEntry` / `FolderEntry` / `MarkdownView`。
 */
import type { DocEntry, FolderEntry } from '../api/core'

export interface DemoVersion {
  version: number
  id: string
  createdAt: string
  mode: string
  chars: number
  source: string
  edited?: boolean
}

export const DEMO_FOLDERS: FolderEntry[] = [
  { id: 'fd_research', name: '技术研究', parentId: '', createdAt: '2026-09-02T09:10:00', count: 0, totalCount: 4 },
  { id: 'fd_papers', name: '论文精读', parentId: 'fd_research', createdAt: '2026-09-02T09:12:00', count: 2, totalCount: 2 },
  { id: 'fd_trends', name: '前沿动态', parentId: 'fd_research', createdAt: '2026-09-02T09:13:00', count: 1, totalCount: 1 },
  { id: 'fd_product', name: '产品与运营', parentId: '', createdAt: '2026-09-05T14:20:00', count: 3, totalCount: 3 },
  { id: 'fd_inbox', name: '待读', parentId: '', createdAt: '2026-09-20T21:05:00', count: 2, totalCount: 2 },
]

const doc = (d: Partial<DocEntry> & Pick<DocEntry, 'id' | 'title' | 'sourceType'>): DocEntry => ({
  engine: 'llm',
  contentHash: `demo${d.id.replace(/\W/g, '')}`,
  tags: [],
  createdAt: '2026-10-01T10:00:00',
  folderId: '',
  ...d,
})

export const DEMO_DOCS: DocEntry[] = [
  doc({
    id: 't_attention', title: 'Transformer 注意力机制到底在算什么', sourceType: 'video',
    engine: 'bcut-asr', duration: 1846, language: 'zh',
    tags: ['Transformer', '注意力机制', '深度学习'], createdAt: '2026-10-18T21:32:00',
    folderId: 'fd_research', sourceFileName: undefined,
  }),
  doc({
    id: 't_frontend', title: '2026 年前端工程化的五个转折点', sourceType: 'webpage',
    engine: 'trafilatura',
    tags: ['前端', '工程化', '构建工具'], createdAt: '2026-10-17T09:14:00',
    folderId: 'fd_trends',
  }),
  doc({
    id: 't_attention_paper', title: 'Attention Is All You Need（论文精读）', sourceType: 'pdf',
    engine: 'pypdfium2', language: 'en',
    tags: ['论文', 'Transformer', 'NLP'], createdAt: '2026-10-15T22:05:00',
    folderId: 'fd_papers', sourceFileName: 'attention-is-all-you-need.pdf',
  }),
  doc({
    id: 't_composite_ctx', title: '三篇论文放在一起：长上下文建模的三条路线', sourceType: 'composite',
    engine: 'llm',
    tags: ['综述', '长上下文', '论文'], createdAt: '2026-10-14T16:40:00',
    folderId: 'fd_papers',
    sourceDocIds: ['t_attention_paper', 't_trends_1', 't_trends_2'],
    sourceTitles: ['Attention Is All You Need（论文精读）', 'LongRoPE：百万级上下文的外推', 'Mamba：线性时间的序列建模'],
    sourceHashes: ['a1b2c3d4', '5e6f7a8b', '9c0d1e2f'],
  }),
  doc({
    id: 't_kb_prd', title: '产品需求文档：知识库 v2', sourceType: 'docx',
    engine: 'python-docx',
    tags: ['产品', '需求', '知识库'], createdAt: '2026-10-12T11:28:00',
    folderId: 'fd_product', sourceFileName: '知识库v2-需求文档.docx',
  }),
  doc({
    id: 't_whiteboard', title: '手写白板：Q4 路线图', sourceType: 'image',
    engine: 'qwen-vl-ocr',
    tags: ['OCR', '手写'], createdAt: '2026-10-11T18:03:00',
    folderId: 'fd_product', sourceFileName: 'whiteboard-1008.jpg',
  }),
  doc({
    id: 't_review', title: 'Q3 产品复盘会议纪要', sourceType: 'text',
    engine: 'text',
    tags: ['会议', '复盘'], createdAt: '2026-10-09T20:47:00',
    folderId: 'fd_product',
  }),
  doc({
    id: 't_ops_data', title: '2026 Q3 内容运营数据', sourceType: 'xlsx',
    engine: 'openpyxl',
    tags: ['数据', '运营'], createdAt: '2026-10-08T15:12:00',
    folderId: 'fd_inbox', sourceFileName: '2026Q3-内容运营.xlsx',
  }),
  doc({
    id: 't_card_notes', title: '《卡片笔记写作法》读书笔记', sourceType: 'epub',
    engine: 'epub-container',
    tags: ['读书', '笔记方法'], createdAt: '2026-10-05T22:31:00',
    folderId: 'fd_inbox', sourceFileName: '卡片笔记写作法.epub',
  }),
  doc({
    id: 't_rag_interview', title: '访谈：为什么我们不再用 RAG 做长期记忆', sourceType: 'video',
    engine: 'bcut-asr', duration: 3120, language: 'zh',
    tags: ['RAG', '长期记忆', '访谈'], createdAt: '2026-10-02T19:20:00',
    folderId: '',
  }),
]

export const DEMO_RAW: Record<string, string> = {
  t_attention: `今天想跟大家聊一个被讲烂了但很少被讲清楚的话题：注意力机制到底在算什么。

先说一个常见的误解。很多人第一次看到 attention 这个词，会以为模型在做某种「关注度分配」——就像人看一张图会先看脸。这个比喻不能算错，但它解释了为什么，没解释怎么算。

我们从最朴素的检索说起。假设你有一个查询，还有一堆键值对，你想根据查询去取最相关的值。最直接的办法是算相似度，然后按相似度加权平均。注意力就是这件事的可学习版本……

（以下为视频转写摘要，共 1,846 秒）`,
  t_frontend: `过去两年，前端工程化发生了几次不太引人注意但影响深远的转向。本文按时间顺序梳理五个节点，并说明每一次转向背后真正的驱动力。

一、构建工具从「打包」转向「按需编译」
二、类型系统从「开发期检查」转向「运行时契约」
三、组件边界从「文件」转向「数据流」
……`,
  t_attention_paper: `Attention Is All You Need
Ashish Vaswani, Noam Shazeer, Niki Parmar, Jakob Uszkoreit, Llion Jones,
Aidan N. Gomez, Łukasz Kaiser, Illia Polosukhin

Abstract
The dominant sequence transduction models are based on complex recurrent or convolutional neural networks that include an encoder and a decoder. The best performing models also connect the encoder and decoder through an attention mechanism. We propose a new simple network architecture, the Transformer, based solely on attention mechanisms, dispensing with recurrence and convolutions entirely.`,
  t_composite_ctx: `<!-- limo-meta {"v":1,"kind":"composite","readerIntent":"compare","sourceMode":"raw"} -->
## 来源文档

1. Attention Is All You Need（论文精读） — ✓ 未删节
2. LongRoPE：百万级上下文的外推 — ✓ 未删节
3. Mamba：线性时间的序列建模 — ✓ 未删节`,
  t_kb_prd: `知识库 v2 产品需求文档
版本：v0.9（评审稿）
作者：产品组

1. 背景
v1 的知识库是一个「只进不出」的仓库：导入很顺，但三个月后用户找不到东西。
2. 目标
把「找到」的中位耗时从 42 秒降到 15 秒以内。`,
  t_whiteboard: `（图片 OCR 结果）
Q4 路线图
- 10月：搜索重构 / 标签体系
- 11月：多端同步
- 12月：开放 API（待定）`,
  t_review: `Q3 产品复盘会议纪要
时间：2026-10-09 14:00-16:30
参与：产品、设计、前端、后端

一、数据回顾
DAU 环比 +18%，但周留存下降 3 个百分点。
二、问题归因
新用户在「首次导入」环节流失明显……
`,
  t_ops_data: `（表格提取结果）
月份, 新增内容, 阅读次数, 收藏率
2026-07, 1280, 48210, 12.4%
2026-08, 1512, 52990, 13.1%
2026-09, 1743, 61120, 11.8%`,
  t_card_notes: `《卡片笔记写作法》
申克·阿伦斯

第一章 关于写作，你需要知道的
写作不是从一张白纸开始的，而是从已有的笔记开始的。`,
  t_rag_interview: `主持人：你们最早是重度 RAG 用户，后来为什么放弃？

受访者：因为我们发现，用户问的绝大多数问题，答案不在文档里，而在「用户自己之前的判断」里。RAG 解决的是「找到相关文档」，但没解决「记得我上次怎么想」。`,
}

export const DEMO_NOTES: Record<string, string> = {
  t_attention: `# Transformer 注意力机制到底在算什么

<!-- limo-block:v1 type="card" -->

<!-- limo-block:v1 type="key-takeaway" -->
注意力不是「模型在关注什么」这种玄学，它是一次**可学习的加权平均**——每个词按自己算出的权重，去别的词那里取信息。
<!-- /limo-block -->

## 从一个检索问题说起

假设你手里有一个查询向量 $q$，还有一堆「键—值」对 $(k_i, v_i)$。你想根据 $q$ 取出最相关的 $v$。

最朴素的做法是算相似度，再按相似度加权平均：

$$\\text{Attention}(Q,K,V)=\\mathrm{softmax}\\left(\\frac{QK^\\top}{\\sqrt{d_k}}\\right)V$$

这个式子看起来吓人，拆开只有三步：

<!-- limo-block:v1 type="steps" -->
1. **打分**：$QK^\\top$ 算出每个查询对每个键的相关性
2. **归一**：除以 $\\sqrt{d_k}$ 防止点积随维度膨胀，再过 softmax 变成权重
3. **取值**：用权重对 $V$ 做加权平均
<!-- /limo-block -->

<!-- limo-block:v1 type="metric" -->
### 缩放因子的作用
除以 $\\sqrt{d_k}$ 后，点积的方差从 $d_k$ 回到 1，softmax 不再被少数几个大值主导。
<!-- /limo-block -->

## 多头到底在做什么

单头注意力只能表达一种「相关方式」。多头是让模型同时学习多组投影，每组关注一种关系。

| 头 | 学到的关系（论文中的观察） | 例子 |
|---|---|---|
| 头 1 | 句法依存 | 动词 → 宾语 |
| 头 2 | 指代消解 | it → 前文的某个名词 |
| 头 3 | 位置邻近 | 相邻词之间的局部关系 |
| 头 4 | 罕见但稳定 | 标点与从句边界 |

⚠️ 这些分工不是设计出来的，而是**训练后观察到的**。不同随机种子训出来的头，分工并不一致。

## 为什么它取代了 RNN

<!-- limo-block:v1 type="alert" -->
核心不是「更准」，而是**可并行**：RNN 必须按时间步串行，注意力一次就能看到全序列，训练时整段并行。
<!-- /limo-block -->

代价也很直接：序列长度 $n$ 时，注意力矩阵是 $n \\times n$。

<!-- limo-block:v1 type="timeline" -->
1. 2017：Transformer 提出，机器翻译 SOTA
2. 2018：BERT / GPT 把编码器与解码器分别推到极致
3. 2020：ViT 证明图像也能切成 token
4. 2023：长上下文成为主战场，$O(n^2)$ 成了真正的瓶颈
<!-- /limo-block -->

## 一句话总结

注意力 = 用「查询—键」算权重，用权重对「值」加权求和。多头 = 同时学多组这样的权重。所谓「模型在关注什么」，指的就是这组权重。`,

  t_frontend: `# 2026 年前端工程化的五个转折点

<!-- limo-block:v1 type="key-takeaway" -->
这篇文章的真正论点不是「工具换了」，而是**前端的复杂度从「构建期」整体迁移到了「运行期」**。
<!-- /limo-block -->

## 一、构建工具：从打包到按需编译

| 阶段 | 代表 | 解决的问题 | 新引入的问题 |
|---|---|---|---|
| 打包时代 | Webpack | 模块化、兼容性 | 冷启动慢、配置复杂 |
| 按需编译 | Vite | 冷启动 | 生产构建仍是打包 |
| 无打包 | 原生 ESM + import maps | 构建期消失 | 依赖预构建、缓存策略 |

## 二、类型系统：从开发期检查到运行时契约

类型从未离开开发期，但 2026 年的新趋势是**把类型变成运行时校验**：

\`\`\`ts
// 类型只在编译期
type User = { id: string; name: string }
// 运行时也需要知道它长什么样
const User = v.object({ id: v.string(), name: v.string() })
\`\`\`

<!-- limo-block:v1 type="recommend" -->
如果你只做一件事：把 API 边界的类型从 \`interface\` 换成运行时 schema。线上 500 错误里有一半是接口形状变了而前端类型没变。
<!-- /limo-block -->

## 三、组件边界：从文件到数据流

## 四、样式方案：从原子类到原生层叠

## 五、部署形态：从静态托管到边缘渲染

## 留给 2027 年的问题

当构建期几乎消失，前端工程师的时间会花在哪里？这篇文章的答案是：**数据边界**。`,

  t_attention_paper: `# Attention Is All You Need（精读）

<!-- limo-block:v1 type="key-takeaway" -->
这篇论文的贡献不是「提出注意力」——注意力在 2014 年就有了。它证明的是：**只靠注意力，也能做序列到序列建模**，而且更快。
<!-- /limo-block -->

## 摘要说了什么

> We propose a new simple network architecture, the Transformer, based solely on attention mechanisms, dispensing with recurrence and convolutions entirely.

两个关键词：**solely**（完全基于）与 **dispensing with**（去掉）。全文的论证都围绕这两点。

## 架构拆解

<!-- limo-block:v1 type="steps" -->
1. 输入嵌入 + 位置编码
2. 编码器 × N：多头自注意力 + 前馈网络，每层带残差与 LayerNorm
3. 解码器 × N：掩码自注意力 + 编码器-解码器交叉注意力
4. 线性层 + softmax 输出词表分布
<!-- /limo-block -->

## 为什么去掉位置编码会出事

自注意力本身是**置换不变**的：把输入顺序打乱，输出只是跟着打乱。

$$\\text{PE}_{(pos,2i)}=\\sin\\left(\\frac{pos}{10000^{2i/d}}\\right)$$

<!-- limo-block:v1 type="alert" -->
论文用的是正弦位置编码，但实验里可学习的位置嵌入效果几乎一样。这说明**位置信息的存在**比它的具体编码方式更重要。
<!-- /limo-block -->

## 实验结论

| 模型 | BLEU (EN-DE) | 训练成本 (FLOPs) |
|---|---|---|
| 先前 SOTA | 26.36 | 更高 |
| Transformer (base) | 27.3 | 3.3×10^18 |
| Transformer (big) | 28.4 | 2.3×10^19 |

## 读后判断

<!-- limo-block:v1 type="metric" -->
### 一个数字
论文标题里的 \`Is All You Need\` 是修辞。位置编码、残差、LayerNorm 缺一不可——消融实验里去掉任何一个，BLEU 都掉 1 分以上。`,

  t_composite_ctx: `# 三篇论文放在一起：长上下文建模的三条路线

## 这批材料放在一起说明了什么

三篇论文表面上都在解决「上下文不够长」，但它们对**长度**的理解并不一致：一篇认为长度受限于注意力复杂度，一篇认为受限于位置编码的外推能力，还有一篇干脆绕开了注意力本身。

## 分歧在哪

<!-- limo-block:v1 type="table" -->
| 路线 | 代表 | 把瓶颈归因于 | 代价 |
|---|---|---|---|
| 稀疏/线性注意力 | Longformer 系 | $O(n^2)$ 计算量 | 表达力下降 |
| 位置外推 | LongRoPE | 位置编码分布 | 训练分布外的稳定性 |
| 状态空间模型 | Mamba | 注意力机制本身 | 长程精确检索能力 |
<!-- /limo-block -->

## 读者问题

### 三条路线能混用吗

能，而且已经在发生：位置外推是正交于注意力结构的改进。

### 我应该选哪条

取决于你的任务里「精确回忆某个远处 token」有多重要。

## 材料作用不同

- 第一篇提供**定义**：什么是注意力
- 第二篇提供**边界**：位置编码能外推到哪
- 第三篇提供**替代方案**：不用注意力行不行

## 材料索引

| # | 文档 | 覆盖情况 |
|---|---|---|
| 1 | Attention Is All You Need（论文精读） | ✓ 未删节 |
| 2 | LongRoPE：百万级上下文的外推 | ✓ 未删节 |
| 3 | Mamba：线性时间的序列建模 | ✓ 未删节 |`,

  t_kb_prd: `# 产品需求文档：知识库 v2

<!-- limo-block:v1 type="key-takeaway" -->
v1 的核心问题不是「导入不够快」，而是**导入之后找不到**。v2 的所有设计都围绕「找回」展开。
<!-- /limo-block -->

## 背景

v1 上线 6 个月，累计导入 4.2 万篇。但数据显示：

<!-- limo-block:v1 type="metric" -->
### 42 秒
用户从「想起要查某篇文档」到「真正打开它」的中位耗时。
<!-- /limo-block -->

## 目标

| 指标 | 现状 | 目标 |
|---|---|---|
| 找到耗时（中位） | 42s | ≤15s |
| 搜索零结果率 | 23% | ≤8% |
| 标签覆盖率 | 31% | ≥70% |

## 方案概要

<!-- limo-block:v1 type="steps" -->
1. 全文搜索换成 FTS5，命中片段直接展示
2. 文件夹支持嵌套，允许用户按自己的心智模型组织
3. 智能分类：模型建议 + 用户审定，模型永不直接写库
<!-- /limo-block -->

## 不做的事

<!-- limo-block:v1 type="alert" -->
本轮**不做**跨文档语义搜索。原因是它解决的是「我不知道关键词」，而当前最大的问题是「我知道关键词但搜不到」。
<!-- /limo-block -->`,

  t_whiteboard: `# 手写白板：Q4 路线图

<!-- limo-block:v1 type="key-takeaway" -->
白板上的三个月份对应三条主线：搜索重构、多端同步、开放 API。其中开放 API 标了问号，意味着尚未承诺。
<!-- /limo-block -->

## 提取到的内容

- 10 月：搜索重构 / 标签体系
- 11 月：多端同步
- 12 月：开放 API（待定）

## 值得注意的两处

<!-- limo-block:v1 type="alert" -->
「标签体系」被画在搜索重构下面，说明它是搜索的前置依赖，而不是独立功能。
<!-- /limo-block -->`,

  t_review: `# Q3 产品复盘会议纪要

## 数据回顾

DAU 环比 +18%，但**周留存下降 3 个百分点**。

<!-- limo-block:v1 type="alert" -->
留存下降与新用户增长同时发生，通常是激活环节出了问题，而不是产品变差了。
<!-- /limo-block -->

## 问题归因

新用户在「首次导入」环节流失明显：

| 环节 | 到达率 | 流失原因（访谈） |
|---|---|---|
| 打开应用 | 100% | — |
| 配置模型 | 62% | 不知道该填什么 |
| 首次导入 | 41% | 等待时间长且无进度 |
| 首次生成 | 33% | 不知道生成什么有价值 |

## 行动项

<!-- limo-block:v1 type="steps" -->
1. 首次启动向导加入「模型配置」一步
2. 导入进度条细化到阶段
3. 空态提供三个示例文档
<!-- /limo-block -->`,

  t_ops_data: `# 2026 Q3 内容运营数据

## 核心指标

<!-- limo-block:v1 type="metric" -->
### 61,120
9 月阅读次数，环比 +15.4%，但收藏率从 13.1% 跌到 11.8%。
<!-- /limo-block -->

## 月度数据

| 月份 | 新增内容 | 阅读次数 | 收藏率 |
|---|---|---|---|
| 2026-07 | 1,280 | 48,210 | 12.4% |
| 2026-08 | 1,512 | 52,990 | 13.1% |
| 2026-09 | 1,743 | 61,120 | 11.8% |

## 结论

<!-- limo-block:v1 type="key-takeaway" -->
内容量增长带来了阅读量，但**单篇价值在稀释**——收藏率连续两个月下降，说明用户在「刷」而不是在「存」。
<!-- /limo-block -->`,

  t_card_notes: `# 《卡片笔记写作法》读书笔记

<!-- limo-block:v1 type="key-takeaway" -->
这本书讲的不是「怎么记笔记」，而是**怎么让笔记之间产生连接**。卡片的价值在连接，不在单张。
<!-- /limo-block -->

## 三种卡片

<!-- limo-block:v1 type="steps" -->
1. **闪念笔记**：随手记，48 小时内必须处理掉
2. **文献笔记**：读别人的东西时记，用自己的话
3. **永久笔记**：一张卡片一个想法，能被别的卡片引用
<!-- /limo-block -->

## 为什么大多数笔记系统会失败

<!-- limo-block:v1 type="alert" -->
因为它们只做了「收集」，没做「连接」。收藏夹里的 1000 篇文章，和没收藏是一样的。
<!-- /limo-block -->`,

  t_rag_interview: `# 访谈：为什么我们不再用 RAG 做长期记忆

## 核心观点

<!-- limo-block:v1 type="key-takeaway" -->
RAG 解决的是「找到相关文档」，而用户真正需要的是「记得我上次怎么想」。前者是检索问题，后者是记忆问题。
<!-- /limo-block -->

## 关键问答

> **主持人**：你们最早是重度 RAG 用户，后来为什么放弃？
>
> **受访者**：因为我们发现，用户问的绝大多数问题，答案不在文档里，而在用户自己之前的判断里。

## 他给出的替代方案

<!-- limo-block:v1 type="timeline" -->
1. 把「用户做过的决定」单独存成结构化记录
2. 回答时先查决策记录，再查文档
3. 文档只作为证据，不作为答案来源
<!-- /limo-block -->

## 我的判断

<!-- limo-block:v1 type="recommend" -->
这条经验对个人知识管理工具同样成立：**先存结论，再存材料**。材料是证据，结论才是可以复用的资产。
<!-- /limo-block -->`,
}

export const DEMO_POLISHED: Record<string, string> = {
  t_attention: `# Transformer 注意力机制到底在算什么（整理版原文）

今天想跟大家聊一个被讲烂了但很少被讲清楚的话题：注意力机制到底在算什么。

先说一个常见的误解。很多人第一次看到 attention 这个词，会以为模型在做某种「关注度分配」——就像人看一张图会先看脸。这个比喻不能算错，但它解释了为什么，没解释怎么算。

我们从最朴素的检索说起。假设你有一个查询，还有一个键值对的集合，你想根据查询去取最相关的值。最直接的办法是算相似度，然后按相似度做加权平均。注意力就是这件事的可学习版本：查询、键、值都来自同一段输入，通过三组可学习的投影矩阵得到。

把它写成一个式子，就是缩放点积注意力。分子是查询与键的点积，分母是维度的平方根。这个平方根不是随手加的：点积的方差会随维度线性增长，不缩放的话 softmax 会被少数几个大值主导，梯度就消失了。

多头注意力是把这个过程并行做多次。每个头有自己的投影矩阵，最后把结果拼接再投影一次。直觉上，单头只能表达一种「相关方式」，多头让模型同时学习多组关系。但要注意，头的分工是训练之后观察到的现象，不是设计出来的。

最后说为什么它取代了循环神经网络。核心不是更准，而是可以并行：循环网络必须按时间步串行，注意力一次就能看到整个序列，训练时整段并行。代价是注意力矩阵随序列长度平方增长，这也是后来长上下文成为主战场的原因。`,

  t_attention_paper: `# Attention Is All You Need（整理版原文）

The dominant sequence transduction models are based on complex recurrent or convolutional neural networks that include an encoder and a decoder. The best performing models also connect the encoder and decoder through an attention mechanism.

We propose a new simple network architecture, the Transformer, based solely on attention mechanisms, dispensing with recurrence and convolutions entirely. Experiments on two machine translation tasks show these models to be superior in quality while being more parallelizable and requiring significantly less time to train.

（以下为论文正文的整理版，保留原文章节顺序与术语，仅调整了排版。）`,
}

/** 每位文档的版本列表（演示站只展示一版，编辑入口会提示演示模式） */
export function demoVersions(docId: string, mode: 'notes' | 'polished', createdAt: string, chars: number): DemoVersion[] {
  return [{ version: 1, id: `${docId}_${mode}_v1`, createdAt, mode, chars, source: 'deepseek-v4' }]
}
