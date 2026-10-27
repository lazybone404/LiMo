# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

LiMo — 知识管理系统。输入 URL/文本/文件 → 适配器提取正文 → LLM 生成结构化 Markdown。
设计哲学：**复杂性不是设计进去的，而是后续插件化长出来的**。

## 设计哲学

### 口子原则（Keep core lean）

**核心系统只保留轻量、稳定、可控的能力；一切重依赖、协议风险、难维护、法律风险的能力通过插件口子接入，官方零维护。**

- **判断「内置 vs 插件」四标准**：依赖重（打爆体积/难迭代）· 有协议问题（AGPL 污染核心）· 跨平台难维护（CPU/GPU/版本）· 有法律风险（平台 ToS/合规）→ **插件化**
- **已落地实例**：whisper（CUDA 依赖）、Tesseract（280M 便携版 + tessdata）、docling（torch 2GB 无法打包）移出核心；pymupdf4llm（AGPL）进程外插件；核心只留 pypdfium2 / markitdown / dashscope 等轻量稳定引擎
- **插件口子**：`~/.limo/plugins/` + EnginePlugin v1（默认进程外 + trusted 例外），官方只维护契约 + 加载 + 文档。**两族基类**：`EnginePlugin`（doc/ocr/asr，「一个文件 → 一段文本」）与 `EmbeddingPlugin`（embedding，「一组文本 → 一组向量」，进程外是**常驻子进程** —— 2026-10-27 加，见「引擎系统」embedding 段）

### 配套：失败显式化（No silent degradation）

口子原则在核心**内部**的延伸——核心不仅外延轻，内在语义也干净：
- **引擎绑定只取第一个，失败直接报错**（无引擎降级链，2026-08-03 彻底移除）
- **扫描件 OCR 失败占位带原因**（不做静默妥协）
- 不做隐式降级是原则；显式重试/备用是独立语义（大模型降级链、用户可配置降级链：后置）

## 设计语言

LiMo 采用六轴主题系统，所有视觉元素通过 CSS 变量驱动，零硬编码色值：

```
data-theme="light|dark"            ← 明暗极性
data-accent="sky|ocean|…"         ← 主辅配色（26套: 现代9+国风9+自然8，HSL动态派生）
data-base="warm|neutral|cool|…"   ← 界面基调（14种，覆盖冷暖偏色+护眼）
data-texture="plain|xuan|kraft|…" ← 预览区纹理（6种纸张质感）
data-font="system|kaiti|wenkai|…" ← 阅读字体（5种，4系统+1web）
--texture-opacity（强度滑条 10-100）← 纹理强度，反转映射到 opacity
```

### 色板架构（两层 + 一个宿主无关的数据层）

| 层 | 说明 | 例子 |
|----|------|------|
| `--tk-*` 语义令牌 | 由 `data-theme` + `data-base` 联合驱动，所有组件引用此层 | `--tk-surface`, `--tk-elevated`, `--tk-border` |
| `--md-*` Markdown 变量 | 全部从 `--tk-*` 或 accent/secondary HSL 派生，无独立硬编码 | `--md-h1`, `--md-code-bg`, `--md-blockquote-bg` |

⚠️ **六轴的「数据」住在 [styles/theme-layer.css](frontend/src/styles/theme-layer.css)**（2026-10-16 从 `index.css` 抽出）：
它是**纯声明层、零消费方选择器**（26 套 accent 原语 + 派生 + 浅色底盘 + 14 条基调 + 暗色 + `--md-*` + 字体 + 纹理数据）。
前端 `index.css` 用 `@import` 引入、**后端导出 [export.py](backend/app/routers/export.py)::`_load_theme_layer_css()` 读同一份**
内联进导出 HTML —— 抽出去的理由就是「同一份数据要给两个宿主用」，往后端抄一份就是第二真相源。
⚠️ **内部顺序即现状顺序，`[data-base]` 必须住在 `:root` 之后、`[data-theme=dark]` 之前**（三者特异性同为 (0,1,0)、
作用在同一元素，顺序即裁决）——**这条此前被写反过、整轴静默失效**，别挪；有守卫钉着（`themeTokens.test.ts`）。
⚠️ **派生型轴的陷阱见关键模式 #69**（`--tk-accent` 这类 `var()` 派生**必须在 `[data-accent]` 上自己重跑一次**，
继承传递的是**计算值**）；**暗色块一律包在 `@media screen` 里**（见 #70，纸永远是浅的）。

**关键约束：**

- **禁止硬编码色值**：任何 CSS/TSX 不得出现 `#FFFFFF`, `#F5F5F7`, `#E8E8ED` 等裸 hex。全部通过 `--tk-*` 或 Tailwind token 引用。
- **JS 里只有 label，一个色值都不留**（2026-10-16 起）：色块一律 `data-*` + `var()`（设置页四组色块 / 预设卡 / 向导预览块），
  值与实机**只有 CSS 一份** ⇒ 漂移从结构上不可能发生，也就不需要「两份值要同步」的守卫。
  `ACCENT_SWATCH` / `TEXTURE_SWATCH` / `BASE_OPTIONS` 的色值字段**已全部删除，别再建表**（此前 `TEXTURE_SWATCH` 与实际底色 **6/6 全不符**、`ACCENT_SWATCH` 的亮度散在 13 种取值而实机恒 50%/62%，零守门人、静默漂移）。
- **轴注册表 [lib/theme-axes.ts](frontend/src/lib/theme-axes.ts)**：key / attr / storageKey / options / fallback / apply 一处收口。
  加一条选择型轴 = **改 2 处**（轴模块 + 注册表一行），启动链（`THEME_AXES.forEach(a => a.apply(a.saved()))`）与预设遍历自动覆盖。
  明暗轴参与但 `apply` 带特化（还要设 `style.colorScheme` + 调 `applyOnAccent`）；**纹理强度不进注册表**（数值轴不是选择型，启动链里单独一行）。
- **github-markdown-light.css 覆盖**：`main.tsx` 导入的第三方 CSS 硬编码了 `.markdown-body { background-color: #ffffff }` 和 `#f6f8fa` 等冷灰色。`index.css` 用 `!important` + `--tk-*` 变量全部覆盖。**新增 markdown 背景样式时必须同步检查此文件。**
- **基调切换一致性**：`data-base` 切换时所有 `--tk-*` 变量同步变化，`--md-*` 通过 `var()` 引用自动跟随。
- **一键主题包**：`THEME_PRESETS` 提供 20 个预设（护眼4/经典4/粉色4/雅致4/国风4），一键设置 base+accent+texture+font 四轴。

### 界面基调（14 种）

| key | 名称 | 色调偏向 | surface |
|-----|------|---------|---------|
| `warm` | 暖灰 | 微红黄（默认） | `#F4F1EE` |
| `neutral` | 中性白 | 无偏色 | `#F5F5F5` |
| `cream` | 奶油 | 暖黄 | `#F9F6F0` |
| `cool` | 冷灰 | 蓝灰 | `#F2F3F5` |
| `rose` | 玫瑰 | 微粉红 | `#F5F1F2` |
| `sage` | 鼠尾草 | 微绿 | `#F2F4F0` |
| `jade` | 柔绿 | 微绿（护眼） | `#EEF2ED` |
| `amber` | 琥珀 | 暖黄（护眼） | `#F9F4EA` |
| `pearl` | 珍珠白 | 柔和中性 | `#F7F6F3` |
| `mint` | 薄荷 | 清爽微绿（护眼） | `#EDF4EF` |
| `sand` | 沙色 | 温暖沙色 | `#F6F2EA` |
| `lilac` | 丁香 | 柔和淡紫 | `#F3F0F6` |
| `cloud` | 云白 | 微蓝白 | `#F3F4F8` |
| `blush` | 腮红 | 极淡粉 | `#F7F1F2` |

⚠️ **别拿 `theme-layer.css` 的 `data-base=` 选择器条数当基调数**——它现在是 **14** 条（2026-10-16 起 `warm` 也显式写：它必须有**一条真规则命中**，否则设置页那张暖灰基调卡只能继承 `<html>` 的 `--tk-surface`，14 张里错 1 张）。它此前是 13 条（`warm` 走 `:root` 不写选择器，2026-10-11 实测踩到，「13 还是 14」正是这么分叉的）。唯一真相源是 [lib/theme-base.ts](frontend/src/lib/theme-base.ts) 的 `BASE_OPTIONS`（14 项）。同理 **`data-accent=` 是 26 条**（26 套全部显式写，无默认项）。

## 文档索引

| 文档 | 内容 | 何时读 |
|------|------|--------|
| [README.md](README.md)（**面向用户**） | 产品介绍：能做什么 / 安装 / 首次使用 / 快捷键 | ⚠️ **改任何用户可见能力时必须同步**（它是用户的唯一入口）|
| [architecture.md](docs/architecture.md) | 六层分层架构，每层模块职责、交互、陷阱 | 理解整体架构和模块边界 |
| [search-retrieval.md](docs/search-retrieval.md) | **全文检索**：字面检索现状（三列分工 / 二字组 OR / 五个写点）+ **索引健康三态与手动重建**（§1.1 / §1.2：坏索引必须能被看见、VACUUM 只在主动重建时做）+ **向量检索**（**阶段 1–4 已全部落地 2026-10-26**：`EmbeddingEngine` 云端内置、向量存储三表、增量 worker、RRF 查询融合与开关、**本地引擎 `local-bge-small`（内置 + **2026-10-27 进程外插件版**，打包版靠后者）+ 引擎可切换**，见 §6.1 (6)）+ **建索引门槛与规模上限 §3.2.1/3.2.2** + **增量机制 §4.4-4.6**（块级 hash / worker 五纪律 / 对账 vs 重建）+ 落地记录 §4.8 | 改检索逻辑、做向量化、查「为什么搜不到」、**定索引生命周期** |
| [frontend-patterns.md](docs/frontend-patterns.md) | 状态管理、取消机制、TaskPanel、apiUrl | 改前端状态/任务相关代码 |
| [export-pipeline.md](docs/export-pipeline.md) | 导出架构、两条路径设计、Tab→内容映射表、KaTeX/Pygments、HTML/Word 共用解析器、**PDF 导出（WebView2 PrintToPdf / 打印对话框双路径）**、打印 CSS 三处落点、**卡片骨架跟随（`skeleton=card` 参数贯通 + `data-skeleton` 主作用域 + 导出侧 `--tk-*` 令牌补齐）**、新增导出格式落点清单 | 改导出功能、新增 tab、**新增导出格式**、改打印样式/PDF 导出、改卡片导出 |
| [CHANGELOG.md](docs/CHANGELOG.md) | 修改记录：做了什么、为什么、影响范围 | 了解历史决策、避免重复踩坑 |
| [STATUS.md](docs/STATUS.md) | 纯事实清单（文件/端点/引擎计数/代码行数） | 需要统计数据 |
| [ACKNOWLEDGMENTS.md](docs/ACKNOWLEDGMENTS.md) | 开源致谢与合规声明（借鉴项目 + 第三方依赖许可证） | 新增/移除依赖、借鉴新开源项目时更新 |
| [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)（仓库根） | **发行包内嵌**第三方组件的许可证清单（与上一条**分工不同**：这条面向发行合规，逐项标「全文位置」） | 打包/发布、增删内嵌依赖（ffmpeg / KaTeX / 字体…）时 |
| [SECURITY_AUDIT.md](SECURITY_AUDIT.md)（仓库根） | **时点**安全审计报告（2026-07-08 起，逐条标 ✅ 已修）；⚠️ 不随代码更新，当前状态以本文件「安全」段为准 | 排查某类漏洞「是不是早就查过/修过」 |
| [user-scenarios.md](docs/user-scenarios.md) | 10 个用户场景及实现状态 | 理解产品需求 |
| [plugins.md](docs/plugins.md) | 插件系统标准 — **五个模板**（pymupdf4llm PDF / faster-whisper 本地转写 / tesseract 本地 OCR / **local_embedding 本地向量化** / trusted 进程内，在 [backend/plugins/examples/](backend/plugins/examples/README.md)）+ **两类基类**（`EnginePlugin`「一个文件→一段文本」/ `EmbeddingPlugin`「一组文本→一组向量」+ **常驻子进程 NDJSON 协议**）+ 开发 API + **`precheck` 就绪自检（进程外 `probe_venv` / 进程内 `probe`）** + `LIMO_PLUGIN_CONFIG` + GitHub 安装流程 + 安全声明 | 开发/安装/调试插件 |
| [prompts-inventory.md](docs/prompts-inventory.md) | 全部 LLM 提示词清单 + 审视点清单（9 已修复/8 待处理）+ 温度参数速查 + PromptComposer 标签速查 + Agent SSE 事件流 | 审视/优化 prompt、新增管道需要参考 prompt 风格 |
| [BUILD.md](docs/BUILD.md) | PyInstaller + Tauri 完整打包流程，常见陷阱 | 打包/发布 |
| [qianwen-models.md](docs/qianwen-models.md) | 千问平台模型速查（文本/OCR/ASR）含价格+调用方式 | 配模型/选引擎 |
| [components.md](design/components.md) | Apple HIG 风格组件规格，布局+颜色+字体+组件 | 改 UI 组件/设计系统 |
| [cards.md](design/cards.md) | 卡片视觉语言参考（19 种）：共享视觉语法 + `--card-*`/`--sh-*` 令牌 + 视觉方案轴（**§0.7**，默认取值的落定见其注）+ **骨架轴的触发方式 §0.9** + 按 LiMo 现状分桶（✅3 已有等价 / ⛔3 不该由模型产出 / ✅10 已落地【比较卡组 + 追问 chips（2026-10-11 对话侧）；提醒/推荐/指标 + 信息卡/摘要卡（2026-10-11 续，产物侧 3 + 对话侧 2）；工作流/时间线卡（2026-10-11 三批，`steps` + `timeline`，**性质是迁移不是新增**——从图表管道挪来）；数据表格 / 代码面板 / 媒体卡外框层（2026-10-11 四批，`table` + `code` + `image`，**给现成形态加壳、块内一字不动**）】/ 🆕3 全新【13 媒体卡**同时在两边**——只落了最外层的框】）+ 两种判据的分野（产物侧按**结构**切节 vs 对话侧按**内容形态**吐围栏）。⚠️ **是素材库不是实施计划** —— 落地哪些取决于真实产物的形态分布 | 做卡片形态、改 [styles/cards.css](frontend/src/styles/cards.css)、讨论对话侧卡片化 |
| [对话协议.md](docs/对话协议.md) | 对话 SSE 事件协议 + 会话 CRUD API + 前端映射 | 改对话流式/会话管理代码 |
| [AGENT-ONBOARDING.md](docs/AGENT-ONBOARDING.md) | 上手教程：从零跑通 → 架构 → 注册表 → 数据流 → How-to → 打包发布 → 测试门禁 → 调试速查 | 新 agent 快速上手、打包/发布 |
| [AGENTS.md](AGENTS.md)（仓库根） | CLI 这条链的开发文档：三层结构 / 改 CLI 的六条硬约束 / 门禁怎么跑。⚠️ 与库内 `~/.limo/AGENTS.md`（`limo doctor --write-agents` 生成）**读者不同、内容不重叠** | 改 `limo_cli` / `/api/cli/*` / `QueryService` |
| [code-audit.md](docs/code-audit.md) | 代码质量与缺陷扫描（第一轮）：**24 项带证据发现**（P0 可直接触发 4 / P1 静默失效·死代码 10 / P2 无机制·重复实现 10，每项含稳定 slug + 五段式）+ 「刻意为之」判据 + 已剔除的假阳性。⚠️ **是 `4174166` 时点的快照，不随代码更新** | 排查已知缺陷、修复前查有无现成记录 |
| [code-audit-2.md](docs/code-audit-2.md) | 代码质量外部审查（第二轮）：**35 项发现** + **8 条纠偏**（照报告改会引入新问题）+ **6 条描述与现状不符**。⚠️ 与第一轮一样是**时点快照**（`6c2ef28` / 0.4.16），不随代码更新 | 回溯本轮修了什么、为什么这么修 |
| [code-audit-3.md](docs/code-audit-3.md) | 代码质量自查（第三轮，**对象是刚落地的新代码**）：**10 项**（P0 启动迁移调 `_rebuild_index(save=True)` 抹掉全库标签/文件夹/来源 + P1 前端跨 mode 版本滞后致编辑覆盖用户没在看的版本 + `chars` 伪造 0 + 版本切换竞态 + 导出测试脚本式假绿 + 4 条 P2）+ **记录不修 5 条** + **对照实验 6 条** + **已知缺口 1 条**（前端 P1 无组件级行为测试）。⚠️ **本轮的新意是「写入语义从新建改为就地覆盖」放大了旧假设的危害**，故四处旧假设被重新评估。⚠️ **时点快照**（`cde70b8` 之上的工作树 / 0.5.0） | 下次自查前查清单、避免重复论证「为什么不修」 |
| [patterns.md](docs/patterns.md) | **关键模式 68 条**（从本文件拆出）：前端 `#1`-`#8` / 后端 `#9`-`#31` / **同源与成对判据 `#32`-`#66`** / CSS 宿主环境陷阱 `#67` / **文档自身的同源漂移 `#68`** | **写任何模块的代码前**；查「这个坑踩过没有」 |

> ⚠️ **README 与代码的一致性**（2026-10-11 核查并**已修**）：曾有两处滞后——① 「同模式保留最近两版」（版本归档早已重做为**全部保留不设上限**）；② 「九种」图型（当时 prompt 层面只暴露**七种** —— `hchain` / `timeline` 引导已删、改走语义块；2026-10-20 进一步收缩到**三种**，其余改走列表或新语义块）。两处都**读起来毫无异常**，是「文档骗人」最典型的形态。**改任何用户可见能力时回头看一眼 README**。

## 版本号管理

全局版本号定义在根目录 [`VERSION`](VERSION) 文件中，通过 [`scripts/sync-version.py`](scripts/sync-version.py) 同步到 7 处：

| 位置 | 说明 |
|------|------|
| `VERSION` | **单一版本源** |
| `frontend/package.json` | npm 版本 |
| `frontend/src-tauri/tauri.conf.json` | Tauri app 版本 |
| `frontend/src-tauri/Cargo.toml` | Rust crate 版本 |
| `backend/pyproject.toml` | Python 包版本 |
| `backend/main.py` | FastAPI + /health 版本 |
| `backend/app/routers/capabilities.py` | capabilities API 返回 |
| `docs/STATUS.md` | 文档版本号 |

**遵循 SemVer 语义化版本**：`MAJOR.MINOR.PATCH`

> ⚠️ **递增惯例：默认只推 patch**（`0.4.5` → `0.4.6`）。本项目的版本号实际承担「本地打包序号」的角色，**不因「本轮是新增功能」就升 minor**——只有用户明确要求推大版本时才升。要升 minor/major 先问，勿自行判断「这次改动够大」。（2026-09-25 用户明确：「版本号最好每次只推进小版本除非我要求推进大版本」；此前 `5c7c6a1` 按 SemVer 自行升到 0.5.0 随即被 `d50b175` 降回 0.4.5 再打包。）

```bash
# 查看当前版本
cat VERSION

# 自动升级（SemVer）
python scripts/sync-version.py patch     # 0.1.0 → 0.1.1（Bug 修复）
python scripts/sync-version.py minor     # 0.1.0 → 0.2.0（新功能）
python scripts/sync-version.py major     # 0.1.0 → 1.0.0（破坏性变更）

# 手动指定版本号
python scripts/sync-version.py 0.2.0

# 升级 + 打 Git tag
python scripts/sync-version.py patch --tag

# 升级 + tag + 推送
python scripts/sync-version.py minor --tag --push

# CI 检查一致性（不一致时 exit 1）
python scripts/sync-version.py --check

# 打包前强制同步（打包步骤见 docs/BUILD.md：PyInstaller → 重命名 exe → 复制 ffmpeg → Tauri）
python scripts/sync-version.py
```

> **禁止手动修改任何文件的版本号**，一律通过 `sync-version.py` 更新。

## 发行规则（2026-08-01 起）

**核心原则：打包 ≠ 发布。** 每次打包完成后，**只有用户主动提出"上传发行版/发布"才能执行上传**。打包是本地动作，上传是对外动作（GitHub Release，不可逆）。

### 发布门禁（全部通过才允许发布）

| # | 门禁 | 检查方式 |
|---|------|---------|
| 1 | 版本门禁 | `python scripts/sync-version.py --check`（7 处一致）；产物文件名匹配 VERSION |
| 2 | 验证门禁 | 后端测试全绿 + 前端测试全绿 + exe 启动 health 200 + 核心链路冒烟（导入→生成→导出） |
| 3 | 文档同步 | CHANGELOG 有对应版本记录；STATUS 版本号已同步 |
| 4 | Tag + Notes + 校验 | 打 `v{VERSION}` tag；Release notes 从 CHANGELOG 提取；附安装包 SHA256 校验和 |

### 发布动作

用户提出发布后 → **先展示门禁清单**（版本/验证/文档/tag 状态）→ 用户确认 → 才执行：
`git tag v{VERSION}` → `gh release create v{VERSION}`（notes 从 CHANGELOG）→ 上传 NSIS/MSI 安装包 + SHA256。

## 常用命令

### 门禁（提交前跑这个）

```bash
bash scripts/check-all            # 全部六项，约 40s
bash scripts/check-all frontend   # 只改前端时（tsc + lint + vitest）
bash scripts/check-all backend    # 只改后端时（pytest）
bash scripts/check-all cli        # 只改 limo_cli / QueryService 时（16 项契约）
bash scripts/check-all docs       # 只改文档时（「第 N 节」交叉引用，约 0.2s）
bash scripts/check-all quick      # 约 5s，仅 tsc + lint
```

**分层的轴是「域」不是「快慢」**：每层都是该域的**完整验证**，而域的边界就是开发者
思考的边界（"我在改前端"）。⚠️ `quick` 是唯一例外——它只看得到类型 / lint 能看见的
东西，本轮 25 项审计发现里**没有一项**是它能抓到的（全是静默失效、同源漂移、死代码，
必须跑起来才现形），别拿它的绿当「没问题」。

⚠️ `docs` 层是**文档域**（`scripts/check-doc-refs.py`，独立脚本、零依赖、约 0.2s）：
校验每份 md 里所有「第 N 节」都指向**本文件真实存在**的编号标题。判据与已知边界见该
脚本 docstring；它刻意**不并进** `quick` / `frontend`（那两层的语义是「前端代码没问
题」，而文档引用失效与前端类型毫无关系）。工具本身用 `--selftest` 自证有区分力。

⚠️ 本脚本**不重新实现任何检查**——`cli` 那层就是 `bash scripts/check-cli`。编排脚本
复刻被编排的脚本，是「同源漂移」最经典的入口（模式 #54）。失败时它会先打**关键行**
再打输出尾部（实测：pytest 失败时尾部 20 行里 18 行是 FastAPI 的 deprecation 堆栈，
只给 tail 等于给了一屏噪音）。

### 后端（Python/FastAPI）

```bash
cd backend
# 开发启动（端口 7413，--reload 已启用）
uv run uvicorn main:app --host 127.0.0.1 --port 7413 --reload

# 注意：--reload 只重载代码变更，不重跑 startup event。
# 修改 config.yaml / 适配器注册 / 引擎注册后需手动重启。
# ⚠️ 同理：`agent/prompts/*.md` 由**模块级单例** `PromptLoader` 在导入时读一次并缓存
# （`agent/prompts/__init__.py`），而 --reload 默认只监听 *.py —— 只改 .md、没有任何
# .py 变更时**不会重载**，对话侧会继续用旧规范而零报错（改了 prompts 却没生效的静默版本）。

# 运行测试（全量走 `bash scripts/check-all backend`；下面是单跑常用项）
# ⚠️ 标记 `pytest` 的四个（2026-10-06 起）必须是 pytest 调用：它们原先的顶层是模块级
# 裸调用，`python tests/x.py` 能跑，但 `pytest tests/` 跑过去**一片绿而根本不产生用例**
# （假绿）。现已改写为 `def test_*`，脚本式调用反而什么都不做。
python tests/test_prompt.py          # Prompt 构建验证
pytest tests/test_adapters.py        # 适配器注册 + 路由
pytest tests/test_engines.py         # 引擎注册 + 查找
python tests/test_store.py           # Store CRUD + 并发安全
python tests/test_url_security.py    # URL SSRF 防护
python tests/test_e2e_smoke.py       # 端到端冒烟测试（36 断言，需后端已启动）。⚠️ 已收进 `main()` 且被 `conftest.py::collect_ignore` 排除，pytest 不会收集它（此前会被收集并静默起后端+污染真实配置）
python tests/test_capabilities.py    # capabilities API 结构
pytest tests/test_plugin_loader.py   # 插件加载器
python tests/test_chart_layout.py    # 图表几何算子 + expand 不变量
python tests/test_chart_svg.py       # 图表 SVG 生成（PIL 子集）
python tests/test_chart_renderer.py  # 图表校验链 + PIL 自绘渲染
pytest tests/test_epub_pipeline.py   # EPUB 一键解读（逐章/归并/取消）
python tests/test_markdown_edit.py   # 产物编辑保存（PUT /api/markdown/{id}）
python tests/test_docx_export.py     # Word 导出（回读 docx 产物断言）
python tests/test_export_print.py    # 导出 HTML 打印样式（CSS 顺序 + 分页约束）
python tests/test_reference_compressor.py # 参考文件超窗压缩（预算判据/降级链/总量兜底/文件名清洗/摘要超时）
python tests/test_reference_store.py # 参考文件暂存（全表过期清理/条目数闸门/总量闸门）
python tests/test_closure_shadowing.py # 变量遮蔽静态检查（AST 扫 app/ + main.py + limo_cli/；两种形态：嵌套函数「先读后写」+ 同函数内 import 遮蔽，整类 UnboundLocalError 防线）
python tests/test_video_understand.py # 视频直传（DashScope SDK）
python tests/test_folders.py         # 文件夹 store 层（重建索引不丢分类 / 删夹不删文档 / 悬空自愈 / 批量一次写盘）
python tests/test_folders_api.py     # 文件夹 API + 批量端点（枚举校验 / failed 明细 / 全失败 400）
python tests/test_search.py          # FTS 全文搜索（snippet 列号 / rank 排序）
python tests/test_model_caps.py      # 多模态能力检测（"*" 分支 exclude 真在跑 + 对照实验）
python tests/test_effort.py          # 思考档位真相表 + 调用点覆盖 + 无硬编码字面量
python tests/test_doc_understand.py  # PDF 直传（闸门 14MiB 两侧 / 编码无损 / 能力门 / 零影响回归 / 超时链 / 调用点对称）
python tests/test_transcript_meta.py # 出处头编解码（往返 / 标题含 --> 与换行 / 无头≠损坏 / 只认第一行）
python tests/test_budget.py          # 统一预算（逐值等价 / 取小 / 窗口闸门 / single 不闭合的 xfail(strict) / cw 来源）
python tests/test_store_composite.py # 合成文档存储（删 index.json 后重建字段仍在 / save_transcript_full 守卫 / 去重豁免 / 损坏标记）
python tests/test_synthesis.py       # 合成管线（Step 0 分叉 / 预算收敛 / 资产搬运含路径穿越对照 / 降级显式化 / 篇内分块 / 软 deadline / 维度可控 / 原料可选 / 预览同源）
python tests/test_generate_gate.py   # 生成闸门（产物裁剪 / 格式清空 / metadata_corrupt 提示 / 跳过 auto-title）
python tests/test_synthesis_api.py   # 合成端点（SSE 协议 / 业务错误走事件 / 槽位归还 / 异常哨兵）
```

### 前端（React/TypeScript/Vite）

```bash
cd frontend
npm run dev       # 开发服务器，端口 5173，API 代理 → 127.0.0.1:7413
npm test          # vitest（<15s；覆盖 store/theme/api/contentSources/streamBuffers/tabRegistry/chatStore/docQuery/genParams/docLibrary/synthesis/getTypeMeta/readingPrefs/readingLayout/chatFont/chatMessageList/docTabs/docTabBar/docTabsClick/docTabsWiring/fontScaleKeys/appShortcutKeys/tagSuggest 等域）
npm run build     # 类型检查 + 生产构建
npm run lint      # oxlint
```

### 只读命令行（`limo`）

```bash
cd backend
python -m limo_cli --help            # 未装入口点时的等价写法（pip install -e . 后可直接用 limo）
python -m limo_cli status            # 需要后端在跑（否则 exit 4）
python -m limo_cli doctor --local    # 不经过后端，直接开本机库（后端没起时诊断）
python -m limo_cli doctor --write-agents   # 安装 ~/.limo/AGENTS.md（默认不覆盖）

bash scripts/check-cli               # 16 项门禁（自启 7419 端口后端，不打扰 7413）
bash scripts/check-cli 9             # 只跑第 9 项（调试用）
```

### 一键启动

```bash
bash start.sh                  # 启动后端(7413) + 前端(5173)
bash restart-backend.sh [port] # 杀端口进程后重启
```

### Tauri 桌面打包

```bash
cd frontend
npx tauri dev       # Tauri 开发模式（需先启动后端 7413）
npx tauri build     # 生产构建 → NSIS/MSI 安装包
cargo check         # 仅 Rust 类型检查（不完整编译）
```

#### PyInstaller 构建后端

```bash
cd /c/WorkPlace/Tool/LiMo/LiMo-C   # ⚠️ 必须在项目根执行（spec 的 pathex=['backend'] 相对 CWD；datas 相对 spec 目录 backend/）
# ⚠️ 必须指定 --distpath，否则输出到 dist/ 而 Tauri 打包的是 bin/ 里的旧文件
# ⚠️ 必须用 venv python -m 方式：uv run（根目录无 pyproject）/ pyinstaller.exe trampoline 都会 canonicalize 失败
backend/.venv/Scripts/python.exe -m PyInstaller --noconfirm --clean backend/LiMoBackend.spec --distpath frontend/src-tauri/bin
```

**常见陷阱**：
- ⚠️ **必须在项目根执行**：spec 的 `pathex=['backend']` 相对 CWD（在 `backend/` 目录下执行会找不到 backend 模块）；**datas 在 PyInstaller 6.x 相对 spec 所在目录（`backend/`）解析**——`static/`、`app/agent/prompts/` 等恰好在 `backend/` 下可直接用相对名，但项目根的 `licenses/` 必须写 `('../licenses', 'licenses')`（2026-08-26 实测 6.21 报 `Unable to find backend\licenses`）
- `console=False` 避免黑框闪烁
- `COLLECT` 模式（one-dir）：生成 `LiMoBackend.exe` + `_internal/` 目录
- Tauri `externalBin` 只打包 exe，`resources` 负责 `_internal/`
- Tauri v2 `externalBin` 自动加平台后缀（如 `-x86_64-pc-windows-msvc.exe`），PyInstaller 输出的 exe 需重命名匹配
- ⚠️ **PyInstaller `--noconfirm` 重建整个 `bin/LiMoBackend/` 目录**（会删除手动复制的 ffmpeg / externalBin 后缀 exe）——ffmpeg 与带后缀 exe 必须在 PyInstaller 之后、Tauri 之前重新复制。**ffmpeg 许可证文件（`GPL-3.0.txt` + `README-ffmpeg.txt`，见 `licenses/`）同样会被删，需一并重新复制**——gyan.dev 全系为 **GPLv3**（非 LGPL，官方页面声明 "All builds are ... licensed as GPLv3"，含 libx264/x265 等 GPL 库；独立进程调用无传染，但必须附全文）
- 新增 Python 依赖后需同步更新 spec 的 `hiddenimports` 和 `collect_all` 列表
- **certifi 必须加入 spec**：PyInstaller 打包后 SSL 证书路径丢失，启动时需设 `SSL_CERT_FILE` 环境变量
- **searxng 不随包（2026-08-26 起）**：SearXNG（AGPL-3.0）为可选部署——`tauri.conf.json` resources 未配置 searxng，Rust launcher（[lib.rs](frontend/src-tauri/src/lib.rs)）检测到安装目录 `searxng/` 才设 `SEARXNG_BIN_PATH` 启用。如手动部署到安装目录：必须清理 `.git`（PyInstaller/Tauri 收集 `.git` 会因权限错误 os error 5 失败）并附 AGPL-3.0 全文（从 [searxng 官方仓库](https://github.com/searxng/searxng) 获取——主包已零 AGPL 库，`licenses/` 不再附带 AGPL 全文）
- **PyInstaller 必须在项目根目录执行**：spec 中 `pathex=['backend']` 依赖 CWD（datas 相对 spec 目录 `backend/`，见上）
- **KaTeX + markmap/d3 离线资源**：PyInstaller 通过 `datas` 打包 `backend/static/`（含 katex + markmap子目录）。打包前需确保字体完整（从 `frontend/node_modules/katex/dist/fonts/` 复制 60 字体到 `backend/static/katex/fonts/`），否则导出的 HTML 公式碎掉。HTML 导出中 CSS 的 `url(fonts/...)` 会被 base64 内联，完全自包含
- **ffmpeg 内置**：将精简版 ffmpeg.exe + ffprobe.exe（gyan.dev essentials 8.1.1，194M，分发源 `~/.limo/ffmpeg-portable/`；2026-08-14 起从 434M full build 精简）复制到 `frontend/src-tauri/bin/LiMoBackend/ffmpeg/`（PyInstaller 之后、Tauri 之前），Tauri `resources` 自动打包。Rust `enhance_path()` 启动时追加到 PATH + 设 `FFMPEG_BIN_PATH` 环境变量
- **OpenSSL DLL 版本冲突**：`cryptography` 包携带的 `libcrypto-3-x64.dll`（~5MB）会覆盖 Python 原生的（~8MB），导致 `_ssl.pyd` 加载失败（`ImportError: DLL load failed`），最终使后端无法启动。spec 中需排除 cryptography 的 DLL 并显式添加 `sys.base_prefix/DLLs/` 下的原始版本。**每次新增依赖后验证**：直接运行 `LiMoBackend.exe` 确认能输出 `Uvicorn running on http://127.0.0.1:7413`
- **PyInstaller binaries 格式**：`collect_all` 返回 `(dest_name, src_path)`，`Analysis(binaries=[])` 期望 `(src_path, dest_dir)`。追加 binaries 时用 `(abs_src_path, '.')` 格式

### 密钥与凭据（运维脚本）

```bash
python scripts/check-secrets.py --check                        # 自检：数据目录有无明文
python scripts/check-secrets.py --check --root ~/.limo --root "$LOCALAPPDATA/LiMo"
python scripts/check-secrets.py --selftest                     # 检查 DPAPI 可用性

python scripts/migrate-secrets.py --check     # 只报告会改什么，**不写任何文件**（exit 1 = 需要迁移）
python scripts/migrate-secrets.py             # 执行迁移（建议先关掉 LiMo）
python scripts/migrate-secrets.py --rollback  # 还原：密钥库的值填回 YAML + 删库
```

**退出码三态**（两脚本一致）：`0` 完成/干净 ｜ `1` 需要迁移/发现明文/失败 ｜ `2` **脚本自身出错**。
少了 2 这一档，「脚本崩了」与「查出问题」在 CI 上是同一个 1，无法区分。

⚠️ **迁移前必读**：迁移工具**刻意不写明文备份**（那等于在磁盘上新增一份静默的明文副本，正是要消灭的东西）——需要额外保险请**手动**把 `config.yaml` 复制到库目录之外；回滚由 `--rollback` 承担。幂等的前提是「先把密钥库的值 inject 回来再 save」，**密钥库解不开时必须中止**（继续下去 `save_config` 会把它删掉）。

### 临时/实验脚本约定（两条路，判据是「三个月后还要不要它」）

**分流判据**：*这个脚本的产出，会不会在三个月后被用来回答「当时为什么这么选」？*

| 会 | 不会 |
|---|---|
| → `_experiments/NN-主题/`（**长期保留**） | → `backend/_*.py`（**用完即删**） |
| 有对照、有量化结果、结论影响技术选型 | 打印一下、看一眼、确认某处行为 |

#### A. 探索性调试 → `_` 前缀，用完即删

后端/前端根目录允许存在 `_` 前缀的一次性验证脚本（如 `backend/_chart_verify.py`），用于逐点排查（渲染验证、像素体检、API 探活）：
- **命名**：一律 `_` 前缀（`backend/_*.py`），git status 一眼可辨
- **用完即删**：结论得出后立即删除，不留残骸。少数用户明确要求保留的（如 `_type_demo_gen.py`）例外，但**绝不提交 git**
- **绝不进打包**：PyInstaller/`tsc` 不收集 `_` 前缀文件；`git add` 时显式排除
- **结论沉淀**：有价值的排查结论写回 CHANGELOG 或对应模块注释，脚本本身不保留

#### B. 验证性实验 → `_experiments/`，可复现、可复盘

**「跑过一次得到一串数字」不是实验，是观察。** 实验 = 有对照组 + 有量化结果 + 有「与预期的偏差」，
且**三个月后换个 agent 照着 README 能重跑出同一个结论**。每个实验目录必须回答五段：

1. **怎么测的** —— 数据从哪来、判据是什么、对照组怎么设、哪些变量被固定
2. **测出来的结构如何** —— 变量怎么组合、每个格子跑的是什么、样本量
3. **得出的结果如何** —— 表格 + 结论 + **与预期的偏差**（偏差比符合预期更有信息量）
4. **已知边界** —— 这个结论在什么条件下不成立
5. **怎么复现** —— 一条命令 + 跑之前要准备什么

约定（详见 [_experiments/README.md](_experiments/README.md)）：
- 脚本**不写死绝对路径**（用 `Path(__file__).resolve().parents[N]` 推导仓库根），整个目录挪位置也能跑
- `results/` 存**原始 stdout**，不存手工整理的摘要 —— 摘要进 README，原始输出用来核对「当时真的跑出来了」
- 改了脚本就重跑、换新日期文件，**旧的不要删**（能看出结论是否随时间漂移）
- ⚠️ **判据与数据不许复制第二份**：实验脚本应当 `import` 产品侧已有的评测脚本（`backend/scripts/eval_search.py` 的 `EVAL` / `_load`）。
  抄一份的失效形态是「产品侧改了题，实验还在跑旧题，两边都算对了」

#### 两条路共用：隔离要自证，不能靠直觉

验证脚本动的是**真实路径还是沙箱**必须由**断言**回答。2026-10-04 一期验证时，`env = dict(os.environ, LIMO_CONFIG=..., LIMO_SECRETS=...)` **只传给了 subprocess**，当前进程里 `save_config()` 用的是真实路径 → 写出了 `%LOCALAPPDATA%\LiMo\secrets.bin`。「我设了环境变量」是直觉，「当前进程的 `os.environ` 里确实有」才是事实 —— 脚本跑完先断言「未污染真实位置」（模式 #58）

### 模型预设核对（开发期脚本）

```bash
python scripts/check-models.py                     # 联网拉 models.dev
python scripts/check-models.py --from-file X.json  # 离线（用本地缓存）
python scripts/check-models.py --new-only          # A 类只看最近 6 个月
python scripts/check-models.py --provider dashscope
```

⚠️ **本脚本的产出是「问题」，不是「答案」**——它只说「这条值得去官方页看一眼」，不说「预设错了」。
models.dev 是社区聚合数据，实测三类问题：口径混用（1000000 vs 1048576，8 条不一致里占 4 条）、
乐观标注（anthropic 自家 haiku 标 200K 而 sonnet/opus 标 1M）、覆盖率仅 54%（缺口正落在最新/最冷门模型上）。
**一切以官方页为准**，脚本的唯一用途是决定「打开哪一页」。

报告四类：**A** 可能有新模型（剔除了 `RETIRED_MODEL_IDS` 的墓碑）/ **B** 可能已下架 / **C** 数值不一致 / **D** 预设缺失的字段（max_tokens、价格）。
**能力位不参与核对**——models.dev 的 `modalities` 说「模型能读 PDF」，与我们走 OpenAI 兼容端点认不认 `file` 块是两回事（模式 #51），该位仍只能逐家查官方文档后手标。

⚠️ **新增 provider 时必须同步 `PROVIDER_MAP`**（无上游目录则加进 `NO_UPSTREAM`）——漏了不会报错，只是**从不提及那一整个厂商**，看起来像「那家没有变更」。`backend/tests/test_check_models.py` 有守卫钉住（含「守卫真会红」的对照实验）。

## 架构

### 管道总览（三条下游 + 合成回流）

> 下图是「一份 transcript → 三条下游」。**合成管道**（见下）不在图里：它是 N 份 transcript → 一份新 transcript 的**回流**，其产物再走一遍生成管道。

```
输入 → Adapter.adapt() → RawTranscript → 存 transcript
                                              ↓
                    ┌─────────────────────────┼──────────────────────────┐
                    │                         │                          │
                    ▼                         ▼                          ▼
              LLM Generator          LLM (image_process)           LLM Chat
         generate_both()          优先→Engine 回退             AgentRunner
                    │                         │                          │
                    ▼                         ▼                          ▼
              MarkdownView              ProcessResult               conversations/
           save_markdown()        save_process_result()
        版本归档(全部保留)           同 mode 覆盖
```

**生成管道**（视频/网页/文本/文档 → LLM 创作）：
- 输入 `rawText`，触发 `POST /generate/{id}`
- 双产物并行（notes + polished），`source_type` 派发不同 prompt 模板
- OCR 预处理（可选）：文档图片先用廉价 OCR 模型提取文字塞回原文，再用文本 LLM 生成
- **版本归档（2026-10-10 从「两版制」重做）**：每版**独立归档、全部保留**，前端版本下拉（时间 / 字数 / 来源 / 编辑标记）。见下方「产物版本」段
- 存储：按文档聚合 `{doc_dir}/{mode}/v{N}.md`（**一版一文件**），图片用相对路径 `assets/xxx` 引用，API 返回前统一 `rewrite_asset_urls` 改写为绝对 URL
- **追问卡片（2026-10-11）**：勾选项在「笔记格式」区（与「图表」同排，**只 notes**）→ 生成完成后**独立再调一次 LLM**（与对话侧同源：`agent/followup.py` 共享 `FOLLOWUP_RULES` 与解析层）→ 落**旁路文件** + 推 `followup` 事件（推在 `done` **之后**）。⚠️ 它登记成 format 项（`AVAILABLE_OPTIONS`，不登记则勾选项不显示 + 提交时被 `.filter` **静默抹掉**）但**刻意不进 `_ALL_FORMAT_FNS`** —— prompt 层零注入，只驱动开关与旁路文件（`_build_hard_constraints` 的 `if not fn: continue` 静默跳过**正是要的**，两处都有「不是漏了」的注释）。⚠️ **那一次调用的思考档位按模型分两支**（`followup.thinking_extra`，判据是能力位 `reasoning`）：**有档位**（内置预设里只有 DeepSeek V4 两兄弟）→ **关闭思考**（形状取自 `llm/service.py` 的 `DISABLE_THINKING_EXTRA`，与 `stream()` 共用一份）；**无档位**（qwen / gpt / claude…）→ **不传**（`extra_body=None`，逐字等于改造前）。⚠️ 有档位时是**关闭**而非 `reasoning_effort="low"` —— 两者服务端互为替代，而 `low` 照样可能把输出预算烧光（分类管道实测）。⚠️ 落点与版本语义见下方「产物版本」段末条与 CHANGELOG
- **联网检索（2026-10-12）**：生成面板「**参考资料**」块内勾选（与「参考文件」并列 —— 两者**同性质**：都是「给笔记补一份参考料」；块名由「参考文件」改准）。开 → `web_search.py::run_web_search` **LLM 抽检索词**（独立 60s 超时 + 按能力位关思考，见 `thinking_extra`）→ `_search_all` 并发调 **`agent.tools.search_web`**（Tavily/SearXNG/DDG 三级降级**复用那一份**，另写 provider 分支就是模式 #54）→ 结果拼成**一份**参考资料追加进 `reference_texts`，于是超窗压缩 / 注入防御声明 / **只注 notes** 全部免费继承。⚠️ **必须是「一份」不是「每条一份」**（压缩器配额下界 200 token/份，逐条注入易撑破总量，而总量兜底是等比削 → 削到句子中间，等于白注）。⚠️ **`run_web_search` 是异步生成器**（`stage` + 恰好一次 `result`）—— 流式端点转发成 SSE、非流式只取结果，**两侧共用同一份编排**（拆两份则「什么时候算失败/退化用什么词」迟早分歧）。⚠️ **失败一律显式**：抽词失败 → 退化用标题检索；搜索失败/零命中 → 不注入 + 专属 warning kind `web_search_failed`（前端走 **info 且不标 partial** —— 检索没生效 ≠ 笔记生成失败）；**非流式不推事件只写盘**。⚠️ **检索自身另有整体闸门**（`SEARCH_TIMEOUT`，与抽词的 `WEB_SEARCH_TIMEOUT` **刻意分成两个常量**——管的不是同一件事）+ `cancel_event` 双向透传（起跑前 + `asyncio.wait(FIRST_COMPLETED)` 中途撤离）：`search_web` 是三级降级**串行链**，单层超时管不住「三轮都慢」，无闸门时用户点了取消也不动。⚠️ 超时 / 取消 / 零命中**三条都返回 `[]` 走同一条下游**（对用户可采取的行动相同，差别只在日志），但**取消 ≠ 失败**——编排层补一次判断，免得把用户自己按的取消报成「没搜到」。⚠️ 自证头 `webSearch`（`queries`/`hits`/`error`）—— **`hits: 0` 必须留**：它是「开了但没搜到」与「压根没开」唯一的区分点（`compact_params` 用 `is not False` 判，数值 0 会保留）。⚠️ `search_web` 只吞网络异常，它的**返回结构**（`{"results":[{title,url,snippet}]}`）仍是契约 → `_search_all` 显式校验 + 记日志 + 源码哨兵钉住（对面改形状而我们静默拿到空结果 = 「检索成功、产物少一节、零报错」）。**不改 `PROMPT_VERSION`**（进的是已有 `<reference_texts>` 节，不是新 prompt 规范）
- **PDF 直传（2026-09-23）**：材料怎么给模型由用户二选一——文本模式（默认，抽出 Markdown 进 prompt）/ **直传模式（PDF 原件以 `file` 块随生成请求发出，本地抽取的正文不进 prompt）**。语义是「**只发原文件**」，不是叠加（叠加 = 同一份内容 token 双花）。实现见 `services/doc_understand.py`（只编码不发起请求）+ `llm/service.py::supports_document`（**按模型标注，刻意不复用 `image` 位**——它 gate 着 AI 分析图片/OCR 模型选择/图片处理用途筛选三处入口）。⚠️ **双真相源**：直传下 rawText 仍是导出/搜索/预览的真相源但不是模型所见的真相源，扫描件上真分叉 → rawText < 100 字符时给完成期 warning。⚠️ **哪些渠道能用是逐家查官方文档得来的结论**（dashscope 只认 qwen3.8 系四个——**任何 qwen-vl / qwen3.5 视觉模型都不在名单**；openai/anthropic/google/zhipu/moonshot/opencode 各有硬理由不标）——**能力位只标查证过的，不要凭印象补标**，逐家结论与理由见 `doc_understand.py` 模块文档。⚠️ 详见「关键模式」#45/#46/#47/#48 与 CHANGELOG

**产物版本**（2026-10-10 重做：两版制 → 版本归档）：
- **磁盘**：`{doc_dir}/{mode}/v{N}.md`，一版一文件、**全部保留不设上限**。`{mode}.md` / `{mode}.prev.md` 是遗留布局，`Store.__init__` 的 `_migrate_legacy_markdown_layout()` 启动时一次性迁走（**只 `os.replace` 改名**、内容与自证头一字不动、幂等；崩在半途的「顶层仍有 `{mode}.md`」状态接在最大版本号之后 + warning）
- **头是真相，index 只是缓存**：`identity.version` 由 store **写盘前注入**（= 文件名 N）；`createdAt` / `style` / `source` 从自证头的 `generated.generatedAt` / `params.style` / `model` 还原 → **删一次 `index.json` 重建后，版本序与生成时间仍在**（此前 `_rebuild_index` 显式跳过 `.prev.md` 且从不读文件头，删一次全丢）
- **三种写入语义**：生成 = 新建 `v{max+1}`（`max` **扫磁盘**取、**与写盘同处一把 `self._lock`**——分开会让并发生成算出同一个 N）｜编辑 = `replace_version=N` **就地覆盖当前版**（版本号不变，**编辑不产生版本**，产品模型 2026-10-10 定）｜追问建议 = **不产生版本**，落旁路文件（见下条）
- **追问建议走旁路文件**（2026-10-11）：`{mode}/v{N}.followups.json` 与 `v{N}.md` **并排** —— 不进正文 / 不进 `index.json` / 不进自证头 / **不进导出**（导出只读 `.md` 正文，故这套机制在导出侧**零改动**）。读是**搭车**的（`get_markdowns_by_transcript` 本来就要逐版读盘，多一次 `exists` 快判），**非空才给 `followUps` 键**（缺 ≠ 空数组）。⚠️ 于是它是**第四类「不在 `.md` 文件里的字段」**：`chars`/`edited`/`source` 从自证头**现算**，`followUps` 从**旁路文件读** —— 那段 docstring 若笼统写「派生字段」，后人会以为删了能重算。⚠️ **与版本脱钩是取舍**：编辑正文（就地覆盖 `v{N}.md`）后追问仍基于编辑前的正文 —— 追问是「当时生成的建议」；相应地「关掉勾选重新生成」时新版无、**旧版仍有**（旁路文件按版本各存），这是**预期语义**不是 bug
- **编辑标记落文件头**（`edit` 键 + `transcript_meta.mark_edited`），不再进 index；生成路径不写它 → 「重新生成自动清除标记」无需额外逻辑
- **派生字段不进 index**：`chars` / `edited` / `source` 由 `get_markdowns_by_transcript` 读文件头现算（它本来就要逐版读盘，零额外 I/O）；版本列表形状收口 `QueryService.artifact_versions`（此前 generate / markdown_edit 各一份副本，模式 #54）
- **前端**：`VersionSwitcher` 下拉（≤1 版整块隐藏）；编辑跟随 `markdownMeta.version`；导出带 `version`（HTML/Word/PDF 三条后端路径共用 `backendParams` 一处拼；MD/复制走客户端 store 天然正确），**指定版本查不到时显式报错、不回退最新**
- ⚠️ **`markdownMeta` 只在加载文档时设一次（notes 优先）**，切 tab **不会**更新它 → 任何按 mode 消费它的地方（编辑 / 导出 / 高亮）**必须先校验 mode**，否则「切到 polished 导出却带上了 notes 的版本号」

**处理管道**（图片/Document → LLM 优先 → Engine 回退）：
- 输入 `file_path`（原始文件），触发 `POST /process/{id}`
- 4 mode：extract/table/digitize/understand
- 旧端点 `/process-image/{id}` 已废弃，保留兼容
- 同 mode 覆盖（不版本化，OCR 是确定性的）
- 调度：请求 `model_id` → `scene_models.image_process` → `scene_models.generate` → Engine
- 存储：`{doc_dir}/process/{mode}.md`

**对话管道**（Agent 工具循环，2026-08-04 前端全量重写）：
- `search_web`（Tavily → SearXNG → DDG 三级降级）+ `fetch_url`（trafilatura/Jina）
- 流式 SSE 端点 + 对话持久化（`conversations/` + CRUD）
- **上下文管理**（2026-08-04 C1-C5）：`context_window` 四层解析（用户自定义 > 预设 > provider 默认表 > 保守 32k）→ 预算 = 窗口×0.7；`core/chat_context.py` 历史按 token 反向裁剪 + 文档引用分级截断（替代固定 40 条）；`agent/compress.py` 超阈值 LLM 摘要早期消息（fallback：deepseek-flash→当前模型→跳过不阻塞）；SSE 事件：`meta` 带 budget/price、`compressed` 事件（压缩后）、`done` 不带 usage（token/费用由前端基于 meta.price 估算）；前端 token 占用进度条 + clearContext + 压缩/隐私提示
- 5 个工具：search_docs / lookup_doc / list_docs / search_web / fetch_url
- **回答依据三档**（2026-10-20）：`lib/chat-prefs.ts` 的 `answerMode`（`mbw_chatPrefs`，**默认 `strict` = 改造前行为**）——`strict` 只用知识库 / `open` 允许补充但文档优先、标明出处 / `free` 自由对话、不要求先查库。⚠️ **是枚举不是滑条**：`open`（文档优先 + 补充）与 `free`（根本不围绕文档）不是同一根轴的程度差别。⚠️ prompt 层是**三个互斥文件**（`agent/prompts/scope_{strict,open,free}.md`，`PromptLoader.scopes` 字典三选一）不是占位符，判据同联网层。⚠️ **拆分判据**：这个取值变了「模型该做的事」会不会变——「库里没有时怎么办」三档各不相同 ⇒ 拆；「禁止编造」三档都成立（**发散 ≠ 编造**）⇒ 留在 `rules.md`。⚠️ **`rules.md` 第 1 条的标题是「引用文档时」不是「文档存在时」**：后者读起来是「先查库」，与 `free` 正面冲突，源码守卫反向钉住它不得回来（`test_chat_answer_mode.py`）。⚠️ **入口严、读回宽**：路由 `_answer_mode` 对非法值显式报 `CHAT_BAD_ANSWER_MODE`（不静默退化，否则用户以为开了发散而产物上看不出区别）；`loadChatPrefs` 读磁盘旧值只退回默认档——一边是「用户刚选的值」，一边是「磁盘上可能过期的值」。⚠️ 参数**只在偏离默认时**才发（`answer_mode` 只在非 `strict` 时带上），与 `web_search` 同一条纪律。⚠️ **两处入口共享一份状态**：输入框那三个按钮与 设置 → 通用 → 「AI 对话」卡片读的是**同一个** `mbw_chatPrefs`，走 `lib/chat-prefs.ts::useChatPrefs()`（`useSyncExternalStore` + 模块内 listener，`saveChatPrefs` 末尾统一通知）。**不要退回各自 `useState(loadChatPrefs())`** —— 设置页改完之后已挂载的输入框不会重挂载，会显示旧档而发送按新档跑（静默分叉，界面完全看不出来）；**也不加 zustand slice**（判据：slice 为跨域共享 action，不为共享一个 localStorage 值）。⚠️ 快照必须返回**稳定引用**（每次 `JSON.parse` 出新对象 = 无限重渲染），故缓存**以磁盘原始串为键**（一次性缓存读不到 `localStorage.clear()` / 手改 / 另一标签页写入）。⚠️ 三档**文案**只在 `ANSWER_MODE_OPTIONS` 一份（两个入口共用），图标留在 `ChatInput`（那一处的视觉细节，不是三档的定义）。⚠️ 有源码守卫钉住：`SettingsPage.tsx` 必须含 `useChatPrefs()` 且不得出现 `useState(loadChatPrefs`，`ChatInput.tsx` 不得内联三档文案。
- **联网开关 + 公网来源**（2026-10-12）：面板上的 Globe 开关（`lib/chat-prefs.ts` 的 `webSearch`，`mbw_chatPrefs`，**默认开 = 零回归**）关掉后**整组摘掉** `search_web` / `fetch_url`。⚠️ **一个 `allow_web` 局部变量驱动两处**——工具表（`_build_registry(store, allow_web=…)`）与 system prompt 的联网层（`prompt_loader.build(…, web_search=…)`），拆成两个来源就是模式 #54（工具摘了而 prompt 还写着「你可以搜索互联网」，或反过来）。⚠️ **默认值方向与产物侧相反且都对**：产物侧 `web_search: bool = False`（**新增**能力 → 默认关 = 零回归），对话侧 `Query(1)`（给**已有**能力加开关 → 默认开 = 零回归）——判据同一条「默认值必须等于改造前的行为」；连带的参数方向也相反：`follow_up` 开着才带 `=1`，`web_search` **关着**才带 `=0`。⚠️ prompt 层是**一对互斥文件**（`agent/prompts/web_tools.md` / `no_web.md`）不是占位符（后者要第二套标记解析器，失败形态是用户看见 `{{WEB_TOOLS}}` 原文）；`base.md` / `rules.md` 里**所有**联网措辞已清空（那两份两种模式都注入，漏一句 = 开关只关了一半），有源码守卫钉住工具名只许住在那两个文件里。**公网来源**：`tool_result` 新增 `sources: [{title, url}]`（`llm/agent.py::_extract_sources`，只 `search_web` / `fetch_url` 非空）→ 工具气泡展开列表，点了开浏览器。⚠️ **与 `refs` 是两条路刻意不合并**（库内文档 `docId` 跳预览区 vs 公网 `url` 开浏览器，形状/跳转/消费方都不同）。⚠️ URL 白名单**两道**：后端 `_safe_http_url`（主防线，抽取阶段丢整条）+ 前端 `lib/chat-sources.ts::toChatSources`（纵深防御，落在两个不可信入口：SSE 流 + 磁盘恢复）。⚠️ 顺带修：落盘 `tool_calls` 此前只存 `tool`+`display`（连 `summary` 都丢 ⇒ 流式气泡有摘要、重开会话后没有），现补齐四字段并把配对抽成 `_record_tool_start` / `_record_tool_result`；`saveChatPrefs` 由整对象覆盖改为**合并式**（否则加第二个开关时「切 A 开关把 B 重置回默认」，零报错）
- 前端独立 `store/chatStore.ts`（不并入主 store）：ChatMsg 模型（streaming/toolCalls/docRefs/thinking）+ 流式状态机 + 会话 CRUD；chatSlice 只留 generating，AI 浮窗状态（aiVisible/aiPos/aiSize）在 uiSlice
- **消息级文档引用**：docIds = 当前选中文档 + 本条 @ 引用（chatStore 组合去重），不绑定会话
- **追问建议**（2026-10-11）：用户可选开关（`lib/chat-prefs.ts`，`mbw_chatPrefs`，**默认关 = 零回归**）—— 开启后**由代码保证有一份**：模型自己写了可用的 `follow-up` 块就用它的，没写则由 `agent/followup.py` **独立补调一次** LLM 并推 `followup` 事件（推在 `done` **之后**；`parseSSEStream` 读到流关闭才 break 故收得到，而 `done` 一到输入框即可用）。⚠️ 判据是「有块 **且** 块里抽得出问题」（`has_usable_followup`）**不是**「有块」—— 空壳块（有标记、块内无列表）会让补调被跳过 ⇒ **开了开关却没有卡**（静默违反承诺）。⚠️ `done` 有**四处**发出，只有「正常走完且未取消」才补调（`should_attempt`）。追问**独立存放**（`ChatMsg.followUps` → 会话 `follow_ups`），**绝不写进 `content`** ⇒ 「导出剥离」整类问题不存在；**追问的追问天然成立**（点 chip 走 `sendMessage` → 新请求照带 `follow_up=1`，刻意不设轮次上限）。⚠️ **拿到之后还能「换一批」（2026-10-12）**：`ChatFollowUpList.onRefresh` → `chatStore.refreshFollowUps` → `POST /api/chat/followups`（**无状态**，见对话侧卡片段的理由）→ 复用 `generate_followups` + `followup_payload`（**不另写编排**）；**成功替换 + 落库**、**失败保留原有那批**并记 `followUpError`。只对**后端补调的那份**开放（模型自主写在正文里的 `follow-up` 块没有可重跑的上游，判据是 `msg.followUps` 非空）

**合成管道**（2026-10-01 建立，2026-10-02 三补：超窗分层 / 维度可控 / 原料可选。N 篇已入库文档 → 一份跨文档对比综述；**下游回流进生成管道**）：

```
transcript × N ──→ POST /api/synthesis/stream ──→ 新的 composite transcript
                    （只建文档，不生成）              ↓
                                        用户核对「## 来源文档」后自行点生成
                                              ↓
                                    GET /generate/{id}/stream（现成端点）
```

- **三条生命线**：① **合成产物 = 普通文档**（`sourceType="composite"`）→ 文档库/搜索/标签/文件夹/导出/对话引用/版本管理**全部免费获得**；② **两步走**（只建文档，生成走现成端点）→ 生成域**零批量概念**；③ **单真相源**（合成文档的 rawText **就是**模型输入，生成时不许再读原文）
- **出处头嵌进正文第一行**（`<!-- limo-meta {json} -->` + `## 来源文档` 列表）：删 `index.json` 重建时 id 由 sha256 重推、`sourceType` 被写死空串，只有正文自身能扛住。编解码收口在 `core/transcript_meta.py`（写方读方必须同源）。⚠️ **头不剥离**（三位一体）；`parse_meta` **只认第一行**（防正文级伪造）；**无头 ≠ 损坏**（判据写错会让所有历史文档被标 corrupt）
- **Step 0 分叉**（`generation/synthesis.py`）：`Σ token ≤ multi 预算` → **Stuff**（N 篇原样组装，**零 LLM 调用**）/ 超出 → **Map-Reduce**（维度发现 1 次 + 逐篇**固定维度结构化抽取** N 次）。Map **不是自由摘要**——自由摘要 = 另一台模型在最终模型看到之前就决定性地筛掉了信息
- **预算同源**（`generation/budget.py`）：`single` = `int(cw×0.8)−2000−16384`（与既有逐值等价，**含已知低估**，D8 保持现状 + `xfail(strict)` 钉住）；`multi` = `min(int(cw×0.5), cw−65536−2000)`（**必须闭合**）。`cw < 102,536` 显式拒绝（`min()` 会静默取负值）。⚠️ **`cw` 由调用方从 `resolve_context_window()` 取后传入**——生成路径此前**根本没走四层解析**（`resolve_llm_config` 只查第 1 层，实际落 provider 兜底表，与预设差 7.6 倍）
- **降级落进产物**：被压缩/截断的篇在「## 来源文档」逐条标 `✓/⚠`——SSE 提示是一次性的。**状态 8 态**（`full` / `summarized` / `chunked` / `chunked_partial` / `truncated_over_window` / `truncated_timeout` / `truncated_llm_error`，原 `truncated` 是**垃圾桶**：三种失败的**该做的事完全不同**却共用一个标签）——**分类粒度按「用户能采取的行动」划分**，不按「哪行代码失败」（模式 #53）；查询走**基础状态回退**（`get(s) or get(s.split("_",1)[0])`），历史 `truncated` 与未知状态都不崩、不丢行
- **超窗分层**（「窗口不够」是**四个问题**不是一个，详见 CHANGELOG 2026-10-02）：**单篇自身超窗** → **篇内分块归并**（关键洞察：分块**不增加该篇配额**，而是让它在配额内**覆盖全文**——头部截断会让后 78% 从未进模型；取 `q = ceil(quota/K)`，⚠️ **每块必须推 `stage`**，否则篇内静默 = Σ 各块耗时，K≥6 冲破 900s 让**正常路径被判挂死**）；**病态长文拖垮整次** → **软 deadline**（到点则剩余篇硬截断 + 标 `truncated_timeout`，正常走到落库，用户拿到「M 篇完整 + K 篇截断」而非空手）；⚠️ `SYNTH_QUIET_TIMEOUT` 与 `SYNTH_TOTAL_TIMEOUT` **曾同为 900**（两条错误同刻到期、由**竞态**决定用户收到哪条）→ 改 300 并把 `max(EXTRACT_TIMEOUT, CHUNK_EXTRACT_TIMEOUT) < SYNTH_QUIET_TIMEOUT < SYNTH_TOTAL_TIMEOUT` 写成**断言**
- **预览端点** `POST /api/synthesis/preview`（**零 LLM 调用、零副作用**）：逐篇 token + 预计处理方式 + `stuff_window`。⚠️ 判据与执行**必须同源**（同一个 `_classify`）；`stuff_window` = 「让全部 N 篇**原样装下**（走 Stuff）所需的窗口」，**不是**「能做合成的最小窗口」（总量超了照样能做、只是走压缩）；banner 触发集**只含** `over_window` / `reject`（`chunked` 是设计认可的终态，若也触发就在「不用换」的路径上不断劝人换，模式 #53）
- **维度可控**（2026-10-02；**2026-10-05 角色变更**）：`DIMENSION_PRESETS` **五套**（general 通用资料 / paper 论文 / tech 技术 / product 产品 / media 观点）经 capabilities 下发（前端不硬编码）；用户给定则**跳过维度发现调用**；⚠️ **Stuff 路径也必须注入**（修复前它是**零注入**——而 Stuff 是多数情况，导致「维度发现」这个唯一的补救机制在最常见路径上根本不跑）。维度落**出处头 meta**（生成是第二次独立请求，落 index 会被重建丢失、落可见正文会与原始材料混淆）。⚠️ **`general` 必须排在字典第一位**——前端用 `dimensionPresets[0]` 预填，排后面 = 后端换了默认而前端仍静默预填 paper，且零报错（2026-10-04 由 `DEFAULT_PAPER_DIMENSIONS` → `DEFAULT_DIMENSIONS` 换锚）。⚠️ **2026-10-05 起维度不再是骨架**：它从「『## 对比总表』的行标签」降为「『## 读者问题』的**候选来源**」——矩阵的列 = 各篇文档（N 篇就是 N+1 列，选 10 篇就是 11 列），中文单元格被压到十几个字符宽，而这类产物的第一要务是**给人看**。**但逐条注入不变、也不能删**：「模型倾向保留**出现次数最多**的内容，而不是**最重要**的内容」，显式列出即显式告诉它什么重要；删了 = 这个功能彻底失效
- **产物形态（2026-10-05 重做）**：骨架由「五段固定」换成**读者问题驱动五节**——`## 这批材料放在一起说明了什么` / `## 分歧在哪`（无分歧时不写这节）/ `## 读者问题`（3–6 个 `###`，每节三步：结论 / 材料怎么说 + `[文档 i]` 锚点 / 什么条件下不成立）/ `## 材料作用不同` / `## 材料索引`（**全文唯一允许表格处**+ 覆盖情况一句话）/** `## 各篇速查`**（**工具区**，2026-10-05 补：维度作 `###`、节内每篇一行，与文章区刻意分家——见下方同日补记）。⚠️ **换骨架的根因不是文风条款不够，而是旧骨架与文风条款正面冲突**：旧版要求「分维度对比 → 共识与分歧 → 综合评价」把同一份对齐结果走三遍，而文风第 3/4/5 条同时禁止「无新增信息地重复」——两条硬指令互斥，模型只能违反一边（这是实测「后四段占 96% 篇幅」的结构性来源）。连带三处：① 事实约束第 3 条「未提及」是**表格单元格的填空规则**，表格移出正文后改写成「材料不足以判断」（语义不变：不知道就说不知道；**抽取阶段 `synthesis.py` 仍用「未提及」**——那是喂给最终模型的中间产物，不是给读者看的正文）；② 覆盖说明由「## 综合评价」末尾搬到「## 材料索引」表下（**第三次搬家**，每次都跟着骨架走）；③ 文风续写第 8/9 条（说人话 / 不设篇幅上限但按读者问题数量定）——⚠️ **第 8 条刻意不含**单篇 `plain` 档文案里的「不堆小标题和列表」，那句与要求 3–6 个 `###` 小节的新骨架正面冲突，**正是本轮要治的病换个骨架复现一次**（两侧不共用同一份文案）。⚠️ **同日验收修订**（真实重跑后）：① **锚点密度**首跑 2.5 个/段（判据 ≤1）→ 三处稀疏规则（分歧条**来源集中到行末标一次** / 读者问题**每节 ≤3 个**且只在「这句是谁说的」读不出来时才标 / 第 3 步**明说不再标来源**），文风第 8 条补排版密度（「一段只讲一件事」）；② **产物末尾写出内部术语「完整输入」——是模板自己要求的**（原文「说明输入覆盖情况（共几篇、其中几篇是完整输入）」），模型忠实照做而验收词表判它泄漏 → 改读者语言「**有没有哪篇被删节过**」，**信息保留、只换措辞**（这是「prompt 要求做的正是验收判违规的」第 4 次复现，修法一律是改 prompt 不是改验收）；③ 同术语另一落点 `transcript_meta._STATUS_LABEL["full"]`（拼进 `## 来源文档`，**那段正文就是模型输入**）→「✓ 未删节」，前端 `SYNTH_PLAN_LABEL.full` 同步（⚠️ 历史文档正文已写死旧文案，改代码不影响它们，无需迁移）。⚠️ **同日再补「## 各篇速查」工具区**（真实场景：用户转述「老师看论文，单篇做得不错，同时看多篇不方便」）：追出的三个诉求里两个（看不过来 / 比不了）要**高密度可扫的对照**，与文章区的「低密度、通俗」**要求相反** → **分区不折中**（揉一起就是把第四轮的病换个对手重演）。形态=**同一份文档，文章四节 → 材料索引 → 速查**（用户拍板），速查**按维度组织**（维度作 `###`、节内每篇一行）；⚠️ **竖排不是表格**——用户最初的「表格内容多影响显示」根因是**横向挤压**（一篇一列），竖排每行一句完整的话，「比」办到了、「表格不行」也没违反，正文唯一表格仍只有材料索引那一张。⚠️ **最大回归风险是速查区把文章区说第二遍**，故 prompt 写两条显式分工禁令（「不复述前面几节的分析」「只列每篇各自有取值的维度」——「关键差异/共同点」这类**关系性维度**逐篇取值无落点，跳过），**测试必须断禁令在场而非只断节存在**；某篇无内容写「（未涉及）」（不用表格时代的「未提及」）。⚠️ **维度由此有两个落点**（同一 `dims_lines` 喂两处）：读者问题段的**候选来源**（软）+ 速查区**小节标题**（硬）——**不违背「维度降为候选」，反而补上了它的薄弱处**：降为候选后维度在产物上没了硬落点，「维度可控」有被架空风险，速查区把它接住
- **原料可选**（2026-10-02）：`raw`（默认）/ `notes`（最新版 `[0]`），**互斥不混合**——混合会同时打破等比配额前提、产物可解释性、降级语义（**整批同源 ⇒ 三个问题一并消失**）。⚠️ 前端拿不到「这篇有没有笔记」（`DocEntry` 无产物字段），**不新增字段/端点**——预览端点顺带返回 `hasNotes`；切模式**重调预览**（本地重算 = 复刻 `plan_synthesis`）；**后端执行时必须再校验**（预览与执行之间隔着用户操作）
- **形态契约（2026-10-18）**：骨架由「一套六节」改为**按读者意图分派五套** —— `READER_INTENTS[*].shape` → `resolve_output_shape()` → `_SHAPE_SKELETONS[shape]` + `_SHAPE_TASK_BASE[shape]`。五形态：`article` 综合文章（正文主体 = 读者问题，**逐字保留已验收的那一套**）/ `claims` 观点综合（各方观点 → 这些观点如何彼此关联）/ `comparison` 比较简报（比较轴 → 差异说明了什么）/ `controversy` 争议分析（争议 N → 哪些分歧其实不成立）/ `decision` 决策与行动简报（结论 → 依据 → 行动与条件 → 什么情况下会变）。⚠️ **改前的问题不是文风不好，而是「意图只换措辞不换组织单位」**：选「找争议」与选「理解主题」的正文结构逐字相同。⚠️ **每套骨架必须带降级出口**（材料不支持该形态时直说：无实质冲突 / 不可比的原因 / 没有明确立场 / 不足以支撑行动），否则模型会**硬造结构**且产物上看不出异常 —— `test_prompt.py` 逐形态断言它在场。⚠️ **入口严、读回宽**：请求入口对非法意图报错（`normalize_reader_intent`），读历史 meta 对 `None` / 未知 key **回退 `article`**（`resolve_output_shape`，旧文档没有该字段）。⚠️ 三张表（`OUTPUT_SHAPES` / `_SHAPE_SKELETONS` / `_SHAPE_TASK_BASE`）**键集必须相等**，有模块级断言 —— 漏一张的失效形态是「骨架对了而 `<task>` 还是通用措辞」。⚠️ 共用尾（`_synthesis_tool_layer()`：材料索引 + 各篇速查）里的「读者问题」硬引用已改「**正文主体**」（其余四套没有那一节）；文风第 9 条同步中性化
- **证据块（2026-10-18）**：`<!-- limo-block:v1 type="evidence" -->` 紧跟论断，块内写方法 / 样本 / 范围 / 局限 / 反例。三类必须出现：材料间实质分歧 / 反常识判断 / 带具体数字或跨度的关键事实。**模型不写编号**（`[证据 N]` 由导出侧按围栏顺序生成 —— 编号由结构派生，事后再补引用会漂移）；**块内不得写 `##`/`###`**（线性化搬进附录后它们会变成真标题，改变导出目录结构）；只在正文主体放、不强制（没有就不写，不得凑数）。⚠️ **不进 `get_markdown_block_format()`** —— 那一份无条件注入给所有 notes，混进去等于单篇笔记也学证据块。归属由 [core/artifact_blocks.py](backend/app/core/artifact_blocks.py) 的 `SYNTHESIS_BLOCK_TYPES = {"evidence"}` 表达，与 `ARTIFACT_BLOCK_TYPES` / `CHAT_BLOCK_TYPES` **三集合不相交**（`KNOWN_BLOCK_TYPES` 取并集只为解析宽进），有 `test_合成不注入` 钉住。⚠️ **预览折叠、导出一律线性化**：前端 `EvidenceBlock` 渲染 `<details>`（summary = 「依据 · 文档 2」，**不带序号** —— `CardSections` 跨节调用会重号）；导出**四种格式含 Markdown**统一「原位 `[证据 N]` + 文末 `## 证据附录`」（用户拍板：Markdown 也降级）。⚠️ **先 linearize、后 strip**（反过来围栏已被剥掉，附录永远不生成且零报错）；`linearize_evidence` **幂等**（无围栏原样返回入参本身，不返回规范化副本 —— CRLF 会被改，逐字节零回归当场失败）；**绝不塞进 `strip_blocks`**（它被合成输入 / 对话导出 / 字数统计共用，放进去会污染非导出链路）
- **读者意图 ⊥ 材料体裁**（2026-10-04，产物形态重做的两只旋钮）：`READER_INTENTS` 六选一（2026-10-18 新增「梳理观点」`claims`，排在 `understand` 之后 ⇒ 前端默认项不变）决定 `<task>` 段的「读者目的」**与产物形态**，`CONTENT_TYPES` 多标签决定加载哪几类「体裁增量」条款（4 类 × 3 条，每类第三条是**停止条件**，多标签必须去重）。两者连同 `dimensions` / `source_mode` **全部只落出处头 meta**——生成是第二次独立请求，届时手里只有 rawText。⚠️ **错误语义刻意不同**：意图与原料模式非法**显式报错**（选错了产物的读者目的/性质就变了，而产物上看不出异常），维度与体裁**静默清洗**（多带一个不认识的值不该让整次合成失败）——判据是「**这个值错了会不会改变产物的性质**」。⚠️ 「理解主题 / 比较材料」的 `defaultContentType` 是 `None`、**刻意不预设体裁**（给「读者的动作」猜材料体裁，正是要消灭的那类错配）。⚠️ 历史字段 `user_notes`（「对比指引」）**已删除**——它是死链（签名里有、函数体零引用，链路看起来完整），位置由 `reader_intent_text` 承接
- **内容约束三块**（2026-10-04；文风 2026-10-05 由 7 条增到 9 条）：`_SYNTHESIS_FACT_CONSTRAINTS`（事实 5 条）/ `_SYNTHESIS_STYLE_CONSTRAINTS`（通用文风 9 条）/ `_SYNTHESIS_GENRE_CONSTRAINTS`（体裁增量）。合成用 `_SYNTHESIS_INSTRUCTION_PRIORITY`（三层，无 style_preference 节，**必须保留 `output_language` 行**——读者意图是用户自由文本、进 `user_request` 节，正是「不可被用户备注覆盖」保护的场景）；全局 `_INSTRUCTION_PRIORITY` 四层不动（单篇零回归）。覆盖说明**换位置而非取消**：删 `<task>` 段的强制元数据开场，改在「## 综合评价」末尾一句话说明输入覆盖情况
- **资产搬运**：源 `assets/xxx` 复制到新文档并加源序号前缀（`d1_`/`d2_`），**引用必须同步改写**（只复制不改写 = 与不搬运完全一样，却零报错）；⚠️ **每个路径都要 `relative_to` 守卫**（现存同款正则已有 5 个副本，新副本不带守卫 = 新开一条任意文件读取通道）
- **生成闸门**（`generate.py`）：composite → 不产 polished、清空 `format_list`、跳过 `_auto_title`；`metadataCorrupt` 推 warning 但不阻断
- 前端入口在**文档库 BatchBar**（选中 ≥ 2 篇）而非 GenPanel——输入的 N 篇是已入库文档，Attention 在文档库。**确认对话框消费预览端点**：逐篇 token + 处理方式（数据只有后端有——`docs` 是白名单序列化、**不含正文**）、窗口 banner、读者意图单选组（+「自定义」文本框）、原料单选组、**折叠的「高级」区**（维度预设下拉 + 维度列表 textarea 每行一个、切换预设=覆盖；体裁多选，由所选意图的 `defaultContentType` 预填、切意图即重置）——**前端不本地重算 `plan`**（那是复刻 `plan_synthesis`，成对逻辑禁止）。⚠️ `tabRegistry` **只**给 summary/transcript 加 composite，**绝不动 `DOC_TYPES`**（会被 `ALL_TEXT` 展开、白送 polished/mindmap）；`summary.defaultFor` 也要加（`forSourceType` 与 `defaultFor` 是两件事）。详见 CHANGELOG 2026-10-01 / 2026-10-02

### 文档库（组织能力 2026-09-23；前端交互重设计 2026-09-25）

- **可嵌套文件夹**（2026-10-18；`routers/folders.py` + store 层 + `storage/folder_tree.py`）：**纯逻辑分类，永不映射为磁盘目录** —— 每个文档已有独立目录（含 `assets` 相对引用与版本归档产物），做成目录会同时破坏三样东西。`index.json` 顶层 `folders`（条目带 `parentId`，`""` = 根）+ transcript 条目 `folderId`（`""` = 未分类，**UI 派生视图而非实体**）；允许重名（id 是身份）；**悬空 folderId 双向容错**；`delete_folder` 同锁区内**整棵子树一起删**（各层文档回落 `""`、**绝不删文档**，返回 `{moved, deletedFolders, deletedFolderIds}`）。⚠️ **零迁移**：读一律 `f.get("parentId","")`，**不 bump `INDEX_VERSION`**（bump 会触发 `_rebuild_index`，而它今天就会清空 folders 表）。⚠️ 五条不变量（缺/悬空/自引 → 根；环在下标最小处断开；前序保证父在子前；`descendantIds` 不含自身、`totalCount` = 直接数 + 后代求和；未分类不入树）写在 [folder_tree.py](backend/app/storage/folder_tree.py) 与 [folderTree.ts](frontend/src/components/doc-library/folderTree.ts) 两份文件头，**逐字同源**，靠共享 fixture `folder-tree.json` 钉住。⚠️ **前端不做路径解析**：`parse_path` / `path_key` 只活在后端一处，建夹一律走 `POST /api/folders/ensure-path`（全有或全无）。⚠️ 见「关键模式」#44（三处构造点 + `DOC_FIELDS` 第四处）
- **排列 ⊥ 排序**（两个独立旋钮）：**排列**（`groupBy`：平铺 / 按文件夹 / 按类型 / 按时间）管**横向归类**，**排序**管**组内纵向顺序**。刻意**没有「筛选时退化成扁平」的特殊模式**——筛了 PDF 而排列仍是按文件夹时，列表按文件夹分组、每组只显 PDF、空组自动隐藏；那种退化正是「树和筛选打架」的来源。「按文件夹」= IDE 一体化树（文件夹是可折叠分组头）。分组头三维度共用（`GroupHeader`）但**增删改只对文件夹组开放**（`group.editable` 显式标志——「把 PDF 组改叫视频」没有语义）；文件夹组**保留空组**（它是拖拽放置目标），类型/时间组隐藏空组
- **筛选区**：五个可折叠小节（文件夹范围 / 文件类型 / 标签 / 标题关键词 / 日期范围）+ **两级折叠**（整区 + 各小节），小节收起时标题行仍显示**激活摘要**（`文件类型 · PDF ×`——否则收起后不知道在筛什么）+ 区头「已激活 N 项 · 清除全部」。**文件夹刻意保留两个入口**：「文件夹范围」（**过滤**「只看这些」）与「排列：按文件夹」（**归类呈现**）——不采用「排列=folder 时隐藏该小节」，切换排列时筛选区突然多/少一节比多读一个词更困惑。**标签无 12 上限**（全量滚动 + 已选置顶）；**标题关键词纯内存零网络、不触发 FTS**，与搜索框的分工是「筛标题」vs「找内容」，二者可同时生效（AND）；**日期范围**「本周」= 本自然周（周一起），**不是**最近 7 天
- **全文搜索**：内存过滤（0ms）**立刻**渲染 → 停手 250ms 打 `GET /api/search` → 合并。⚠️ `docs` 数组**没有正文**（白名单序列化器只存元数据），故正文搜索客户端**不可能**实现 —— FTS5 是唯一可行解不是优化。
  - **索引三列各司其职**（`core/search.py`，2026-10-26 实测重做）：`title` 原文标题（索引）/ `body` 清洗后原文 **UNINDEXED**（只存不搜，**唯一用途是生成摘要** —— 中文在此保持连续）/ `toks` 检索 token 串（**唯一匹配列**）。⚠️ 摘要**不用 FTS5 `snippet()`**：它只能从被索引列取文本，而那是二字组串，摘要会变成 `机器 器学 学习` —— **token 化是检索的内部表示，不该泄漏到展示层**。
  - **索引文本 = rawText + 最新版笔记**（`doc_text.search_index_text`）。⚠️ 只索引 rawText 等于「只搜用户没写过的那部分」：实测 29 题含笔记 83% vs 不含 66%。**五个写点全部经 `update_index()`**（见「公共常量的家」表）。
  - **查询侧：二字组 + OR**（`_tokenize` / `_escape_fts5`）。⚠️ 逐字 AND 召回高但区分度为零（「的/是/不」全库都有），目标文档常被 BM25 挤到第 13、33 名；二字组 AND 则一票否决，语义型查询直接归零（15 题 7→**0**）。**区分度来自二字组，召回来自 OR，排序交给 BM25**，三者缺一不可。⚠️ `_tokenize` 的 `limit` 默认 **None**（索引侧必须收全文），只有 `search()` 传 64 —— 曾经无条件 `uniq[:64]` 导致每篇只索引开头 64 个 token，而所有单测都是短文本、全绿。
  - 结果集**单一化**：`eligible`（类型/标签/文件夹/日期/标题，**不含关键词**）∩（关键词内存命中 ∪ FTS 命中）——命中行统一走 `DocRow`（有复选框/右键/拖拽，不再是不能选的裸按钮）且**受筛选约束**；标题类命中加「**原文**」角标（措辞非「正文」：`title` 与 `toks` 两列都会命中而一次查询无法判定来自哪列），纯原文命中加 `SearchSnippet` 第二行。结果限 100 条，到顶时底部提示细化关键词（静默截断 = 用户以为「就这些」）
- **索引健康三态 + 两处提示**（2026-10-26）：`GET /api/search/status`（`available` / `docCount` / `sizeBytes` / `lastError` / `rebuiltAt`）+ `POST /api/search/reindex`（`X-LiMo-Client` 头守卫）。三态是**可用 / 索引不可用（坏库，用户可自助重建）/ 引擎不在场（开发者问题）**——后两态此前都汇成「搜不到」，与「库里真没有」逐字相同。提示分两层：设置页 `SearchIndexCard` 是**拉取**（用户主动走进来才看得见），文档库顶部 `SearchIndexBanner` 是**推送**（索引已经坏了、用户还没意识到）。⚠️ 两者共用 [lib/search-health.ts](frontend/src/lib/search-health.ts)（`useSyncExternalStore`，模块级快照）与 `reindexSearch`，判据只有一处 `status.available`。⚠️ **刻意不轮询**（对照 `lib/health.ts` 的后端探活：进程真的会掉）：索引可用性只在启动重建 / 手动重建 / 外部删 `search.db` 三个时刻变化，故用 30s TTL 去重 + 挂载取一次 + 503 时强制刷新 + 重建后写回。⚠️ **`null`（还没取到 / 取不到）一律不渲染**——「问不到」不等于「坏了」，反过来写会在后端没起的几十秒里挂一条点不动的「去重建索引」。⚠️ `useDocQuery` 的 `isIndexUnavailable` 同时喂「提示文案」与「是否刷新健康状态」两处（拆开会出现「列表说索引坏了、顶部却没动静」）。⚠️ 接线有守卫：`docLibraryLayout.test.tsx` 两条断言「横幅真的挂在文档库里」+「索引正常时零渲染」，做过摘掉组件的对照实验
- **语义检索（向量，阶段 1–4 已全部落地 2026-10-26）**：与字面检索**并列的第二路**，只在开关打开时参与融合，**默认关 = 零回归**（`config.search.vector_enabled: false`，UI 在 设置 → 通用 → 检索索引）。
  - **两个引擎可选**（2026-10-26 阶段 4）：`dashscope-embedding`（云端 1024 维，按字数计费）与 `local-bge-small`（[engines/embedding_local.py](backend/app/engines/embedding_local.py)，bge-small-zh-v1.5 ONNX int8 · 512 维 · 免费 · 零外发）。配置键 `config.search.vector_model`；`POST /api/search/vector` 的 `model` 字段切引擎，非法值 400 `VECTOR_NO_ENGINE` 且零写盘。⚠️ **切换必然「口径不符」**（1024 ↔ 512），后端给 `reason` 提示需要重建、**不自动重建**。⚠️ **刻意不是 `~/.limo/plugins/` 的 `EnginePlugin`**：那个签名是「一个文件 → 一段文本」，装不下「一组文本 → 一组向量」，且进程外每次 `Popen` + 加载 3–5 秒会撞 `QUERY_TIMEOUT = 3.0`；口子原则的实质（重依赖不进打包）由「`onnxruntime`/`tokenizers` 不在 `pyproject.toml` + 模型 22.8MB 不随包」满足。
  - **三个可选钩子**（`precheck()` / `warmup()` / `release()`，**不进 Protocol** —— `runtime_checkable` 加成员会让云端引擎不再满足，消费方一律 `getattr`）：`precheck` 的读法在 [`vector_runtime`](backend/app/services/vector_runtime.py)::`_engine_precheck`、**排在 `ensure_identity` 之前**（否则引擎起不来时先把身份写成新口径，用户装完依赖看到的是「需要重建」而实际一个向量都没编码过）；`warmup` 在 [`vector_worker`](backend/app/services/vector_worker.py)::`_warm`（**排在 `_next_ids` 确认有活之后** —— 队列空着时加载 22.8MB 纯属浪费；已加载时是纯内存快路，查询侧每次都会调）；⚠️ **预热失败不 `mark_failed`** —— `fail_count` 是花钱的闸门，模型没起来不是这篇文档的错。`release` 见下一条。
  - **模型内存释放（2026-10-26）**：`config.search.vector_release` 三档 —— `never` / **`idle`（默认，引擎侧门槛 `DEFAULT_IDLE_RELEASE = 600.0`）** / `immediate`。⚠️ **门槛住在引擎侧**，worker 不知道；`idle` 档调 `release()` 时**不传 `after_idle`**（传了就是第二份「多久算久」的判据）。⚠️ **`immediate` 只在本轮真有推进后释放一次**（`loop` 里 `stats["indexed"] or stats["failed"]` 置 `_pending_release`，`maybe_release()` 消费即复位；空转也放会把用户刚载入的模型踢掉，现象是「刚用过又要等几秒」而日志全绿），释放后 `_warmed = False`。⚠️ **释放后查询必然超时**（加载 3–5s > `QUERY_TIMEOUT = 3.0`）→ `VectorSearcher._ensure_loaded()` 在 `_precheck` 之后用**独立的 `LOAD_TIMEOUT = 30.0`** 包住 `warmup()`，载入超时/失败各有专属文案；**不是放宽 `QUERY_TIMEOUT`**（那会让「查询真卡住」也干等 30 秒）。⚠️ **入口严（非法值 400 `VECTOR_BAD_RELEASE` 零写盘）、读回宽（状态侧脏值静默回退 `idle`）**；`vector_runtime._ensure_worker` **策略变化时重启 worker**（不重启 = 配置改了行为不变）。三档 label / hint 由 **capabilities 下发**（`releasePolicies` / `_RELEASE_POLICY_META`），前端只存联合类型 `ReleasePolicy`，**仅本地引擎下渲染**（云端模型在服务端，本机不占内存）。
  - **索引触发方式（2026-10-27）**：`config.search.vector_trigger` 两档 `auto`（默认）/ `manual` + `vector_threshold`（默认 100）。`auto` = `pending` + 尚可重试的 `failed` **攒够阈值**才跑一轮；`manual` = 永不自动跑，只有「立即建立索引」（`POST /api/search/vector/sync`）才动。⚠️ **闸门住在执行层之外** —— `_should_run()` 排在 `_next_ids` / `_warm` 之前，所以「达不到阈值就不加载模型」是**结构保证**（测试断 `eng.warm_calls == 0`）；顺序反过来 = 每轮先载 22.8MB 再判断没事干，行为上看不出区别。⚠️ **重试不受闸门约束**（`failed` 且 `fail_count < MAX_FAIL` 是纠错不是增长）。⚠️ **手动触发不能 `await run_once()`** —— `attempted` 每轮私有，并发两条协程会让同一篇编码两次、计费翻倍而产物完全正常；走 `_force` 旗标 + 唤醒，执行只留在 `loop` 一条线上。⚠️ **写点叫醒 worker**（`VectorStore.set_wake_callback` → `mark_pending` 末尾回调）；`mark_pending` 可能跑在 `to_thread` 里而 `asyncio.Event.set()` 不线程安全 ⇒ `vector_runtime._start_worker` 用 `loop.call_soon_threadsafe` 包一层，且两侧都 `getattr` 兜底（唤醒是可选优化，不是 `VectorStore` 的契约）。`DEFAULT_INTERVAL = 60.0` 由此**退化为纯兜底心跳**。⚠️ **默认值 ≠ 改造前行为，是用户拍板的语义变更**：小库在 `auto` 档下不会自动建索引，补偿 = 状态区「待索引 N 篇」+ 恒在的「立即建立索引」+ `trigger`/`threshold` 回显。⚠️ **校验分野**：非法 `trigger` → 400 `VECTOR_BAD_TRIGGER` 零写盘（错的是**性质**，静默退回 `auto` 会让「选了手动档」变成谎话）；`threshold ≤ 0` → 夹紧到 1（错的是**量**）。前端阈值输入框**只在 `auto` 档渲染**（`manual` 下摆着就是谎话），失焦/回车才提交，`!== undefined` 判空（`0` 是合法入参）。
  - **存储**：`~/.limo/vec.db` 三表（[core/vector_store.py](backend/app/core/vector_store.py)：`vec_meta` 身份 / `vec_state` 状态机 / `vec_chunks` 块）。⚠️ **四元组 `(model_id, dimension, chunk_size, schema_version)` 是整体身份**，任一不符即整体不可用 —— **不自动重建**（重建是计费调用），换模型后由用户点「重建向量索引」。⚠️ `mark_pending` 三条写死语义：**清空 `content_hash`**（留旧值会让 worker 的「hash 相等跳过整篇」误命中）、**不删 `vec_chunks`**（块级增量靠旧块比对）、**归零 `fail_count`**（内容变了是新机会）。
  - **写点只打 `pending` 不编码；编码归后台 worker**（[services/vector_worker.py](backend/app/services/vector_worker.py)）：**攒批闸门**（见下条）→ 启动对账 → 分块（flat / h2，`DEFAULT_CHUNK_SIZE=200` 字）→ **块级 hash 增量** → 编码（**当前选中的引擎**：云端 `text-embedding-v4` 1024 维 / 本地 `bge-small-zh-v1.5-int8` 512 维）→ **每篇 checkpoint** → `MAX_FAIL=3` 连续失败停该篇（心跳 60s / 失败冷却 300s）。⚠️ **`BATCH_SIZE=10` 是服务端硬上限不是调优旋钮**（超了 HTTP 400）。
  - **查询融合**（[services/vector_search.py](backend/app/services/vector_search.py)）：`depth = max(limit, 50)` **两路同深度**（否则「字面第 20 名」与「向量第 20 名」拿到不同的 `1/(k+rank)`，排序取决于谁取了更深的巧合）、`RRF_K = 10`（29 题实测优于 60）。**只用排名不用分数** —— BM25 与余弦量纲不可比，归一化等于引入新超参。纯算子 `core/vector_index.py::rrf` **与评测同一份**。
  - **失败一律显式且不花钱**：`_precheck` 三档（库不可用 / 没建过 / 口径不符）**拦在编码之前**，命中即字面结果照给 + `semantic.error` 文案；`semantic.hits` **恒在**（`0` 是「开了但索引还空」，整个 `semantic: null` 才是「压根没开」——同 `webSearch.hits: 0` 的取舍）。`load_matrix` 走 `asyncio.to_thread`（同步读盘几十到几百毫秒，放事件循环里会让 worker 的 await 与 SSE 心跳一起停住）。
  - **删除侧与写入侧成对**（2026-10-26 审查 P1-01）：[routers/ingest.py](backend/app/routers/ingest.py)::`_drop_vectors(request, tx_id)` 在**单删与批量删两条路径**上各调一次 `vector_store.delete_doc`。漏了**不报错** —— 孤块继续参与融合、挤占 `depth` 名额，要等下次启动 `reconcile()` 才清（桌面应用能开好几天），而设置页那句「删除文档时对应的向量也会一并清掉」是当面承诺。**新增任何删除路径时先看这里。** ⚠️ 它吞异常只记 warning：文档已经删了、回滚不了，抛出去只是把「向量没清干净」变成用户看不懂的 500。
  - **`SearchEngine.status()` 是纯只读**（同日审查 P1-02）：读状态失败只记进返回的 `statusError`，**不碰 `_last_error`** —— `available` 的定义就是 `_last_error is None`，一次瞬时读失败写进去 = 「点开设置页看一眼」换来一个只有手动重建能解除的不可用状态，而界面上不会说发生了什么。`VectorStore.status()` 同款做法。
  - **开关的唯一维护点**：[services/vector_runtime.py](backend/app/services/vector_runtime.py)::`sync_vector_runtime` —— worker 与融合层**成对**启停（只起融合层 = 查询在用一份永不更新的索引），`get_vector_searcher` 直接读 `app.state.vector_search`、**不按配置现造**。两个端点 `POST /api/search/vector`（开关）与 `/search/vector/rebuild`（先停 worker → `reset_identity` → 再同步）。
  - **前端**：设置页 `SearchIndexCard` 的向量段（`vBusy` / `vConfirming` / `vError` 三个独立状态，`runVector` 刻意不写 `setSearchHealth` —— 共享健康状态只关心字面 `available`）；文档库底部**语义退化提示** `[data-search-semantic-note]`（muted）与 `searchError`（warning）**刻意分家**：字面索引坏了要**动手修**，语义退化多半等后台建完就好 —— 合成一条会让两件事读起来一样。
  - **已知边界**：`limo search` **不接融合**（`QueryService` 是同步纯读层，两个传输层共用；见 [AGENTS.md](AGENTS.md)）；真实库端到端实测（131 篇 ≈ 2 元 / 3 分钟）与 `eval_search.py --vector` 产品侧同轮对照**待做**，但 **2026-10-26 用户决定本轮先不做** —— 跳过的是**产品链路**（开关 → worker → 搜索），算法正确性由评测侧同源算子 + 20 条单测覆盖，理由见 CHANGELOG 同日条目。
- **⛔ 「正文匹配（N）」独立分组已取消**（2026-09-25）：它固定追加在下方、**不受筛选约束**（筛了 PDF 照样列出网页和视频）、行是裸 `<button>`、还与「排列」旋钮直接打架——是第三个维度的系统分组。三项缺陷都修，唯一值得保的性质（FTS 的 `ORDER BY rank` 相关性序）由**「相关度」排序键**承接：rank 取自 `hits` 的**数组索引**（零后端改动），非命中最末；搜索词从空变非空时自动切过去（= 原「最相关的在最前」的默认体验），用户手动改回后**本次搜索内不再回切**；无命中时该键**不出现**在选项里（所有 rank 都是 MAX，排序退化成保持原序，是个点不动的死键）
- **单击行 = 打开文档，不动选择集**（2026-09-25 定死，此前语义未定义且与 `selectMode` 自相矛盾）：复选框**列**常驻、**图标**未选中时只在 hover / 行内焦点出现（选中后常显；图标仍占布局位置，故标题不会横跳 —— `focus-within` 是给键盘用户的），Ctrl/Cmd 点击 toggle、Shift 点击区间（anchor 留组件本地——跨折叠退化为单点 toggle 可接受）、Ctrl+A 全选（**焦点在输入框内不劫持**，与 Ctrl+K 相反）、Esc 清空。**`selectMode` 已删除**——批量条由 `hasSelection = selectedIds.size > 0 || selectedFolderIds.size > 0` 派生：要"原子修改"来防的漂移，因为漂移的载体本身没了。`active`（预览目标）与 `selected`（批量目标）是两个正交维度，视觉分离且可叠加。⚠️ **Ctrl/Cmd + 按住拖动 = 划选（2026-10-18）**：方向由**起点行**决定 —— 起点已选中 → 整段**取消**，未选中 → 整段**加选**；**刻意不逐行取反**（在既有已选又有未选的行上会得到「选中/取消交错」，而手势意图是「刷一段」）。每次都从按下时的 `base` 快照重算 ⇒ 幂等（拖出去再拖回来一致）；`mousemove` 与「滚动后重算」**共用同一个 `applyTo`** —— `scroll` 不冒泡故走 `document` + `capture`、等一帧再 `elementFromPoint`（同一帧内只留一个重算，`scrollFrame` 合并）；`mousemove` 在没有布局的宿主（jsdom）用 `ev.target` 兜底，**滚动重算刻意不传 fallback**（那会锁在滚动前的行上）。⚠️ **区间的两端按「屏幕上看到的顺序」算**（`domOrder`，从 `[data-doc-list] [data-doc-id]` 实测取）：默认排列按文件夹会把列表按树前序重排、未分类排最后，**折叠组更是根本不在 DOM 里** —— 拿 `visibleIds`（数据顺序）当基准会选中另一段文档、并把看不见的文档划进批量删除名单（`Ctrl+A` 例外：全选 = 跨分批的全部匹配项，刻意不走它）。⚠️ **划选结束那一下 `click` 的抑制绑「起点行 id」**（`{id, until}` 而非纯时间窗）：浏览器在 mouseup 后必派发一次 click，只有起点行的那一下需要吞，其余行立刻可点。⚠️ **文件夹是第二个选择集**（`selectedFolderIds`，入口是分组头左侧复选框：点 toggle / Shift 区间；**不参与 Ctrl 划选** —— 分组头与文档行在同一列流里交错，混进同一条区间后「第 3 行到第 9 行」取决于中间夹了几个分组头）。批量条分开计数（「已选 3 项 · 2 个文件夹」），文件夹只做**移动**（`FolderPicker`，禁用自身与全部后代；**目标即当前父级 = no-op**：先摘掉已在该位置的夹，全是 no-op 时**不发请求**只提示，部分时文案附「N 个已在该位置」）与**删除**（二次确认；先**去后代化**只发根，本地回落范围**只认后端 `deletedFolderIds`**）；两者**部分失败时把失败项放回选择集**供直接重试；`arrangeBy !== 'folder'` 时文件夹分组头不渲染 ⇒ 自动清空残留选择（⚠️ 判据是 `arrangeBy`，**不是 `grouped === null`** —— 按类型/按时间排列时 `grouped` 同样非 null 但组不可勾选）；筛选变更时两个集合**一起清**（`noSelection()` 同源）；两个删除确认框都认 `Esc`
- **密度**：详细（默认，卡片）/ **简略**（26px 单行，复选框图标留在布局里防标题横跳，筛选区收成一行入口 + 浮层复用同一批小节组件，排列旋钮与分隔条不渲染）。**简略模式 = IDE 文件树**：**固定按文件夹分组、无视「排列方式」偏好**（那个旋钮在简略下本就不渲染，也就没有跟随它的必要），分组头是父节点、其下子行缩进一层 + 一条 1px 缩进参考线（`treeIndent`，只给**有行的组**画）。⚠️ **分组头 26px 与文件行 26px 是成对常量**，改一处必须改另一处——否则父节点比子节点矮，树形就散了（`GroupHeader.compact` / `DocRow` 紧凑分支，两处都有注释指路）。⚠️ 曾设计为「简略一律平铺不分组」（省下分组头的高度），那样省掉的是**结构**：一个文件夹 30 篇时，平铺列表除了一列标题什么信息都不给
- **大列表**：分批渲染 60 条 + `IntersectionObserver`（`root` 绑滚动容器），**全选 = 全部匹配项（跨分批）**，工具栏显示「含未显示项」。⚠️ `animationDelay: i*50ms` 在第 1000 篇要等 50 秒——是功能性错误不是动画偏好
- **批量操作**：`POST /api/transcripts/batch`（delete/tag/move）按 action **显式枚举校验**（不能静默 `affected:0`）；逐条收集 `failed` 明细；全部失败 → 400；store 侧**一次加锁一次写盘**（相对 N 次 PATCH = N 次全量写盘）
- **智能分类**（2026-10-11；同日二轮改为**两阶段**）：选中 N 篇 → 对话框**先呈现配置**（取材深度 / 文件夹策略 / 新建上限 / 标签策略）→ 用户按「开始分析」才 `POST /api/classify/preview`（恰一次 LLM 调用、**零写入**）产出「每篇 → 建议文件夹 + 建议标签」→ 逐篇审定 → `POST /api/classify/commit`（一次加锁一次写盘）。**模型永不直接写库**。⚠️ **`onStart` 是计费调用的唯一出口**——`handleOpenClassify` 只打开对话框、**一个请求都不发**（此前是「打开即分析」：用户没点任何东西就已经花了钱、界面已被填满；用户说的「初始就选了 600」只是那件事最显眼的读数）。⚠️ **两项策略各三档**：`folderMode` = 优先复用或新建（默认）/ 只复用 / 只打标签；`tagMode` = 喂全库词表（默认）/ 用户手填基础标签（**替代**词表而非叠加；全库无标签时前端默认落这档——空词表等于没喂）/ 完全交给模型。⚠️ **判据粒度分两档**：策略非法 → **显式报错 `CLASSIFY_BAD_OPTION`，且在校验处就返回**（不发起计费调用）；`notesChars` / `maxNewFolders` / `seedTags` 非法 → 静默夹紧。分野 = 「这个值错了会不会改变**产物性质**」。⚠️ **策略的两个投影必须同源**：prompt 侧（`_folder_section` / `_tag_section`）与收口侧（`_normalize_one`）收到同一次取值，故模板只留两个占位符、整段由函数生成（模式 #54）。⚠️ 四条产品决策：分类空间**优先复用**既有文件夹（必要时新建，上限 5——不加复用压力 = 每跑一次多几个夹）、取材深度**用户选**（600/200/仅标题，后端再按预算兜底缩减并**逐篇显式标注** `shortened` / `title_only`）、标签**优先复用**、**分类不做降级**（超时/JSON 非法一律 `AppError`，与 `reference_compressor` 的降级返回 `None` 刻意不同）。⚠️ **不扩 `batch` 的第 4 个 action**——它的形状是「一个动作、一个 folderId、一组统一 tags」，表达不了逐篇异构。⚠️ **新文件夹由前端在 commit 前用现成 `POST /api/folders` 建**（名字是用户可改的自由文本），commit 只做存在性校验。⚠️ 折叠左栏 = 卸载 `DocLibrary`，而分类是计费调用 → 持 `AbortController` 在 cleanup 里 abort。⚠️ **本面板内的浮层必须 Portal 到 body**：`<aside>` 带 `backdrop-blur-[20px]`，它会**创建包含块**把 `fixed` 后代锁进面板大小的一小块（用户原话「卡在那一个文档栏里面」）；`FloatingAI` 的根 `<aside>` 同款，对话侧三个弹窗一并修（**共 6 处**）。⚠️ **例外（2026-10-11 代码审查）**：`FilterPopover` 的筛选浮层与 `ChatInput` 的 @ 浮层是 `absolute` **锚在按钮上的下拉**，Portal 到 body 会失去定位基准 → 改为删遮罩 + 监听 `document` 的 `mousedown`、用 `contains` 划内外界（原注释担心的「点按钮也算外部」正由它解决）。⚠️ **同日三轮（分批 + 关思考）**：一次调用撑不住大 N（实测 90 篇时模型把 8192 输出预算**全烧在推理链上**、正文 0 字符、`finish=length`，而报错还是误导性的「JSON 不合法」）→ **每 `CLASSIFY_BATCH_SIZE=20` 篇一批**（每批独立组装 prompt，拿回各自 `docs` **合并后仍交给同一个 `normalize_suggestions`** ⇒ 新建上限的全局计数 / 同名归一 / 幻觉 id 剔除**不产生第二份实现**，模式 #54）+ **关思考而非降档**（`disable_thinking` → `extra_body={"thinking":{"type":"disabled"}}`；**实测 low 档同样把预算烧光**，降档救不了。⚠️ 与 `reasoning_effort` **服务端互斥**，代码里是 `elif`，测试有对照实验钉住）。失败语义分家：**全部批失败 → 整次显式报错**（不降级）／**部分批失败 → 结果照给但全局 warning 点名哪几批**（少它则用户会把「根本没被分析过」当成「模型的判断是归入 none」）；空输出另有 `CLASSIFY_EMPTY_OUTPUT`（与「返回了东西但不是 JSON」的 `CLASSIFY_BAD_JSON` 分开）。双层超时：每批 120s + 整次 600s（分批是**串行**的）。⚠️ **同日四轮（代码审查 7 条，详见 CHANGELOG）**：`fit_to_budget` 预算改按**单批**篇数均摊（此前按全量 N——分批后每篇被砍到约 1/批数，分批「压输出量」的收益被反向吃掉；⚠️ **副作用**：按批均摊后每篇配额下界 200 > `MIN_USEFUL_CHARS`(120)，`title_only` 分支**在默认常量下不可达**，测试改为显式抬高该常量来行使它）｜新建上限**按批分配**（`per_batch_new_limit`，此前每批都声明全量 ⇒ 全局收口拒掉约 80%；**声明按份、收口按总量**）｜空词表兜底 `seed`/`vocab` **共用同一份**（此前只修了 seed 半边，vocab 空库时「优先从上面选」+「（库里还没有标签）」自相矛盾）｜审定阶段重跑失败**仍可应用**（`canConfirm` 删 `!error`——父组件刻意留着旧 preview 给用户用）｜两处段控共用 `activeDepth`（此前三元的一支永不被行使）。⚠️ **同日五轮（实机验收 91 篇真实库，详见 CHANGELOG）**：空库那句 prompt **必须按策略分两份**——`reuse_only` 空库「只能全 none」**是对的**，`reuse_or_new` 空库**正是该新建的时候**（首次使用 / 刚丢完分类，恰是最常见的两个场景）；曾两档共用「本次请只使用 none，不要使用 reuse 或 new」，等于把用户选的 `reuse_or_new` 在最该新建的场景静默改写成 `tags_only`（实机对照同一份输入：共用 → 新建 **0**；拆开 → 新建 **23**）。⚠️ **与同日二轮的「空词表兜底」恰成反例**（那里 `seed`/`vocab` **共用一份是对的**，因为「都没词表可挑」时两档语义完全一致）——**判据是「两种取值下该做的事是否相同」，不是「两种取值是否都在同一个函数里」**；混为一谈就是模式 #54 在 prompt 层的形态（共用一份的理由看起来同样充分「都是空库嘛」，失效方式却是**用户选的那一档被悄悄换成另一档**）。⚠️ **附带观察**：`MAX_NEW_FOLDERS=5` 对主题极散的 91 篇严重不足（模型跨 5 批共提 **14 个**新名 → **60+ 篇被上限拒成 `none`**，模型分类本身是准的，问题在那个旋钮）—— **同日六轮已处置**（见下）；冷启动标签不收敛（91 篇 **223 种**，词表为空时「优先复用」无从谈起，**需第二轮才有证据**）**仍未处置**。⚠️ **同日六轮（候补 + 杂项口袋夹 + 判据定形，详见 CHANGELOG）**：① **超限被拒的篇不再丢弃模型的夹名** —— 带 `candidate` 出到对话框（多一个「新建：XX（模型原建议）」选项，采纳时**预填夹名**，等于给用户一个「手动多开一格」的手柄）；② 新增**杂项口袋夹**（`MISC_FOLDER_NAME`，**只在 `reuse_or_new` 下注入**）—— **不占 `MAX_NEW_FOLDERS` 额度**（占用则上限 5 时它会挤掉一个真实分类，**恰好救不了超限这个场景**），「尽量少放」由**收口层机械闸门**保证而非 prompt 自觉（进杂项的篇数 **> 总数 1/3** → **整夹撤下**退回候补 + 全局 warning；整数比较 `len(misc)*3 > total` 避开浮点）；库里已有「杂项」时走既有同名归一化转 `reuse`。③ 顺带修全文审计抓出的两处真缺陷 —— `preview` 回显的 `limits.maxNewFolders` 取的是**默认常量**而非用户设的值（**判据与执行不同源**，用户调成 8 而对话框仍写「最多新建 5 个」）、标签名清洗有**三份实现**而 commit 那份**漏了截长**（同一条 40 字标签走模型建议进来是 30 字、用户手打的却是 40 字，一个库里并存两种长度）→ 抽 `clean_tag_names` 为唯一内核（**条数上限仍留在各调用点**：跨策略不变量下沉、策略特有约束上浮）。⚠️ **判据至此定形**：**「一段共享文案只有在与引用它的每一个策略的完整契约都不冲突时才能共享」** —— 它同时解释了为什么空库文案**必须拆**（携带策略特有结论）、空词表兜底**必须共用**（讲的是跨策略不变量）；上一轮的「两种取值下该做的事是否相同」需额外限定取值点、不可机械检验，已弃用。⚠️ **不 bump `PROMPT_VERSION`**（不产生产物）、**不动 `MAX_NEW_FOLDERS = 5`**（改它等于重新定义「一次分类最多留几个夹」，属另一次产品决策）。⚠️ **2026-10-18 起支持嵌套路径**：`newFolderName` 仍是**一个字符串**（schema 不变），层级用 `/` 表达（`技术/前端`）——**刻意不新增 `parentId`/`parentPath` 字段**（加字段放大编造面）。解析**只活在后端** [`folder_tree.parse_path`](backend/app/storage/folder_tree.py)（整串先逐字匹配现有夹名 → 保护历史里本名就含 `/` 的夹；再按 `/` 切分），前端收集**原串**直接调 `POST /api/folders/ensure-path`（⚠️ **前端不拆 `/`**：自己拆会让建夹不再是全有或全无，且会拆开历史夹名）。⚠️ **同名歧义显式**：单段名对应多个夹 → `none` + 「存在多个同名文件夹」warning，**绝不按数组顺序取第一个**；模型省略前缀而后缀命中多个分支时同样判歧义（否则会在根级造出第三个看似正常的结构）。⚠️ **新建额度按「实际新建的文件夹行数」计**（`_plan_new_paths`；`技术/前端` 在「技术」已存在时算 1 行、两层都缺算 2 行；同一路径多篇只付一次），超限时**整条路径**进 rejected 且 `candidate` 保留完整路径；**超过所选层数截断 + warning**（文档仍被分类）——层数由用户在配置阶段的「细分程度」三档选定：1 不建子夹 / **2 允许一级（默认，2026-10-18 用户拍板）** / 3 允许两级（`CLASSIFY_MAX_DEPTH_LIMIT` 直接取 `folder_tree.MAX_PATH_SEGMENTS`，不另写字面量）；`maxDepth` 贯穿 prompt 规则插值（`_depth_rule`）→ 额度规划 → 收口截断，越界值**静默夹紧**（错的是**量**不是性质，判据同 `notesChars`；对比 `folder_mode` / `tag_mode` 错的是性质 → 显式报 `CLASSIFY_BAD_OPTION`）；⚠️ **已有的更深结构不往回退**——截断点取 `max(maxDepth, 最深已存在前缀长度)`，模型建议 `A/B/C/D/E` 而库里已有 `A/B/C/D` 时归入后者（`reuse`），而不是一律截到第 3 层（那会让「用户自己建得更深」反而让文档落进更浅的夹，且结果看起来完全正常）。于是「新建最多到用户选的那一层（上限 3 层）」是**分类建议**的上限，不是树的深度上限（手动建夹任意深）。⚠️ **不建夹时传中性值**：`reuse_only` / `tags_only` 下 `start()` 传 `maxDepth: 1`，与 `maxNewFolders: 0` 同款——否则响应回显一个用户没选过的层数。⚠️ **口袋夹按完整路径分组**（`技术/杂项` 与 `生活/杂项` 各自判 1/3，不连坐），嵌套口袋**整条路径不占额度**。⚠️ **删掉了前端本地同名预查**（`trim().toLowerCase()` 是 `path_key` 的第二份副本，它「认为相同」时会把一个本该新建的路径静默并进另一个夹）。详见 CHANGELOG 2026-10-11 / 2026-10-18
- **状态归属由「折叠即卸载」判定**（`SlotLayout` 是 `collapsed ? <PanelStrip/> : <Comp/>`，组件本地 state 收起即归零）：`selectedIds` / `listLimit` / `folderBusy` / `docsLoading` / `collapsedGroups` / `titleFilter` / `dateFrom` / `dateTo` 在 `docsSlice`（⚠️ 字段名是 **`collapsedGroups`**：存的是**收起的组**，不是「展开的文件夹」——此前三处文档写成 `expandedFolders`，是个从未存在的字段名）；`density` / `groupBy` / `sort` / `expandedSections` / `filterAreaHeight` 在 `docLibraryPrefs`（localStorage `mbw_docLibPrefs`）；`docLibSearchFocusNonce` 在 `uiSlice`（**自增 nonce 而非 boolean**——boolean 表达不了「已经是 true 时再按一次」）。**刻意不改 SlotLayout 为 CSS 隐藏**：本轮的 store 需求不止「折叠别丢」，还要跨组件共享 + 与筛选 setter 原子发生修改
- **Ctrl+K / 顶栏搜索**：`SearchBar` 已退化为**纯按钮**，点它 = 展开文档库 + 聚焦搜索框，与 Ctrl+K 共用 `openDocLibSearch`（**唯一入口**——两处各写一遍必然漂移；宽度一并复位，折叠态宽度可能是 40px，只设 `collapsed: false` 会展开出一条缝）。⚠️ 它曾是**第二套搜索实现**（限 10 条、无 AbortController、自带一份 snippet 渲染分支）——「搜文档」被做了两遍
- 前端拆 **18 文件**（编排 `DocLibrary` / `DocLibraryToolbar` / `SearchField` / `FilterArea` / `FilterSection` / `filters` / `FilterPopover` / `ListToolbar` / `DocList` / `GroupHeader` / `DocRow` / `BatchBar` / `ClassifyDialog` / hook 层 `useDocQuery` / `useDocSelection` + `docLibraryPrefs` / `docMeta` / `entryDelay`）。纯函数单独导出以便表驱动单测：`filterDocs` / `sortDocs` / `buildResultSet` / `groupDocs` / `countByFolder` / `collectTagFacets`。**文件夹计数以 `countByFolder`（本地派生）为唯一来源**——不能读后端 `folder.count`，那是上次拉取的快照，而 `moveDocs` 是乐观更新，两边各读各的会在移动瞬间互相矛盾。拖拽用原生 HTML5 DnD（零新依赖）+ ContextMenu「移动到文件夹」子菜单兜底键盘用户

**图表管道**（2026-08-16 建立，2026-09-19 改为语义化布局 + 多图 + 类型 4→9）：

**双协议并存**：```fig（语义 JSON，现行）/ ```chart（手写 SVG，legacy 兜底）
- **fig 路径**：LLM 只输出「有哪些节点、怎么排」的语义 JSON（**不写任何坐标/尺寸/SVG**）→ `app/services/chart_layout/`（拆包：`geometry.py` 几何底层 / `layouts.py` 10 个算子 / `engine.py` 校验与 expand；纯几何零依赖，算坐标/宽度/正交连线/标签遮罩，字体宽度经 `measure` 参数注入以保证与渲染同源）→ `app/services/chart_svg.py`（面向 PIL 子集生成；出口 `_FORBIDDEN_RE` 自检——`<marker>`/`class=`/`url(#)`/`<defs>` 是**被静默忽略**而非被校验拦住的）→ 现有校验链 → PIL 渲染 PNG(2x)。**几何错误从结构上不可能发生**，几何规则也就不必进 prompt（绕开 2026-08-16「堆 28 行硬规则 → fast 模型宁可跳过不画」的教训）
- **三种布局**（prompt 层面；`_LAYOUTS` 有 10 个**全部保留**）：`grid` 依赖/调用网络（唯一能表达「谁连谁」的 row×col 算子）/ `flowchart` 带**分支与汇合**的流程（字符串 id + 拓扑分层，**拒绝任何环**）/ `state` 状态机（BFS 分层，**允许环与自环**）。⚠️ **判据是「文本结构能否保真表达这个关系」**（2026-10-20 定形）：同一层不会出现第二个节点、也没有回边的，列表就能表达 ⇒ 不画图。**2026-10-20 撤掉的四种**布局与出口：`layers`/`tree` → **嵌套列表**（结构即语义，连块都不必标）· `sequence` → `sequence` 块 · `quadrant` → `quadrant` 块。更早（2026-10-11）已撤：`hchain`/`timeline` → `steps`/`timeline` 块；首尾相接的环 → `cycle` 块（2026-10-20）。**删的是引导不是能力**（算子在 `_LAYOUTS` 里仍可用，历史产物/插件/其它入口不受影响）；`get_chart_format` 开头有指路语把五种出口逐一列清。⚠️ **`get_markdown_block_format`（无条件注入）与 `get_chart_format`（勾选才注入）是成对的**——同篇里两者会同时出现，改一侧必须改另一侧，否则一边说「画图」一边说「别画」。两张注册表：`_NODES_RUNNERS`（入参 nodes）/ `_SPEC_RUNNERS`（入参整份 spec），`grid`/`layers` 在 `expand` 内特判。⚠️ **两条写死的层数闸门已删**（`flowchart` 的 `nlayer > 5`、`state` 的 `nrow > 7`）：它们在 `expand()` 的「压行距（三轮）+ 等比缩放 + `_MIN_SCALE`」**之前**就抛错，是同一件事的第二套判据且严格更严——实测 8 层纯矩形 round2 只需 564px（`scale=1.0`），却被判「层数过深」（用户实际撞到的那条假失败）。现在统一由缩放链裁决（自然上限约 13 层），真装不下才报「图过高，请拆图」
- **形状层 + `shapes` 通道**（2026-09-19）：节点可选 `shape ∈ {rect, diamond, stadium, circle, bar}`（`{x,y,w,h}` 始终是**外接矩形**，故 AABB 不重叠断言无需改写）。算子可选返回第 4 项 `shapes`（`kind: line|rect`），在**背景之后、连线之前**渲染——**有几何范围但不属于内容主体的一律走它**（不占节点数、不进 `_count_nodes`）：象限轴线框 / sequence 生命线与分组框全按此归类
- **`title` 渲染**：`TITLE_H = 24` 在 `expand` 里计入（`y += TITLE_H`、`height += TITLE_H`，高度/缩放/断言三处自动一致）。**已决策接受 3.75% 容量损失（640 → 616），不动 `_MIN_SCALE`**——0.65 对应 13px×0.65≈8.5px 已是可读性边缘，标题是内容而非装饰
- **标签截断两级 + 空壳判据**（2026-09-19）：`_fit_labels`（节点定稿时按该节点最终宽度截）→ `_shrink_labels`（布局收尾保险——`_distribute` 的收缩发生在 `_fit_labels` **之后**）。⚠️ **可用宽必须走 `_shape_inner_w` 同一个函数**：`_fit_labels` 曾写死 `avail - 32`，与后者的 `<64 → w-8` 分支**不同源**，tree 等比收缩到 `_MIN_NODE_W=48` 时按 16px 算 → 连「一个中文 13px + 省略号 11px」都塞不下，9 个节点全被截成纯 `…`（**空壳图**：渲染成功、几何全对、断言全过、压测全过，用户拿到一张一个字都没有的图）。判据：截到「整个字都不剩」直接抛 `LayoutError`；且 `_shrink_labels` 必须**前置**检查「进来时是否已是空壳」——空壳测宽 11px 远小于可用宽，只看测量值的判据**进不去截断分支，会静默失效**（第一版修法正是栽在这里）
- **chart 路径**（legacy，全链零改动）：LLM 手写 SVG → 现有 `_render_png` 全链，**免费回退路径**——回滚只需改 `get_chart_format` 一个函数
- **按内容形态容错分流**：fig 围栏内若含 `<svg` → 按 chart 分支处理（模型改用 fig 后 legacy 兜底会自然失效，JSON 一错就白丢一张图）
- **多图**：每个围栏 = 1 张图，prompt 允许 **1~N 张**（旧 prompt 硬性「必须 1 张最有代表性的图」，渲染链路本无上限，瓶颈纯粹在措辞）；单围栏节点 ≤9，超过必须拆围栏。⚠️ **文件名是内容哈希 `chart_{sha1(svg)[:8]}.png`，不是按序编号**——按序编号 `chart_N` 每次生成都从 0 起，重跑一次就把 `chart_1.png` 覆盖掉，而旧版本仍引用它（切到旧版看到「新版的图配旧版的正文」，三张图全成功、零报错）。hash 让「内容相同 → 同名，内容不同 → 不同名」同时成立，与截图路径同款做法。`counter` 只保留「失败不递增」的语义，不参与文件名（2026-08-16 已改，本文件此处长期写着旧行为）
- **自然尺寸 + 等比缩放**（2026-09-19，用户提议「先完整绘制再缩放到对应比例」）：布局按**自然尺寸**定稿；高度超 640 不再直接失败，返回 `scale` → 落到根 `<g transform="scale(k)">`；`_MIN_SCALE=0.65` 下限（再小字号就糊，宁可报错让模型拆图）。**宽度按内容裁剪 + 内容拉回 MARGIN**（`_trim_canvas`，`_trim_width` 是它的薄委托）：**四类元素 nodes+labels+shapes+edges 全部计入边界**——只按节点裁会把 state 回边沟槽切出界。⚠️ **宽度按内容跨度算，且必须平移**（2026-10-20 修）——只按右边界定宽（`maxx + MARGIN`）时，画布收窄而坐标没动，「右侧空白」被原样换成「左侧空白」（实测 flowchart：画布 412px、左侧空 232px）；现在 `_shift_space_x()` 把四类元素一起平移回 `MARGIN`，`_shift_path_x()` 重建 M/H/V 路径（与 `_shift_path_y` 对称，**边不平移就会与节点错位**）。画布被 `_MIN_CANVAS_W` / 标题宽度撑宽时内容居中。实测 state 左右各 16px / timeline 172px / 铺满的 hchain 仍 640（`dx=0` 零漂移）。⚠️ 背景 rect 必须在 scale 组**之外**，否则 `100%` 被缩成 `100%×k` 留出空白带
- **超时分档**：`_gen_timeout(formats)` → 勾选图表 `_GEN_TIMEOUT_CHART=400` / 其余 `330`。**两入口都要传**（非流式 `generate()` 无 `asyncio.timeout`，只受 SDK 约束）。⚠️ `request_timeout` 必须同时改 `LLMService.__init__` **和** `client` property——`AsyncOpenAI` 是惰性构造的，只改前者会「存了没用」（值进了实例属性但 property 里写死 330），**超时一点没变且单测照样 PASS**
- **`chart_{hash8}.json` 是协议切换的确定性信号**：两条路径都出 PNG 且张数可能一样，只有 `.json`（仅 fig 分支解析成功时落盘）能区分走的哪条路
- 实测（2026-09-19，约 900 字文档）：**6 张图 9.8s / 2890 字符**——新协议下出图几乎不增加成本，「多图会显著拉长生成」的前提不成立，故 `_GEN_TIMEOUT_CHART` 由基础档线性外推（330×1.21）而非按 P95×1.5 套用
- 生成笔记勾选「图表」（formats=chart）→ prompt 按需注入图表规范（`get_chart_format(source_type)` 基础规范 + 来源差异段）。**以下为 legacy chart 路径的历史约束**（2026-09-19 起 prompt 不再教这些——fig 路径的坐标/宽度/遮罩全在 `chart_layout`/`chart_svg`，保留备查）：prompt 硬性规范优先（viewBox+width/height 双写一致 ≤640×640、**正常显示硬规则**（2026-08-16 从原仓库 diagram-design 提取：背景不透明铺满 rect / 元素不越界留 ≥16px 边距 / 相邻节点不重叠 / 每个节点必须文字·字号 ≥12·深字浅底 / 文字节点内居中）；**样式复用原仓库**（用户要求）：背景 `paper #f5f5f5` / 主文字描边 `ink #2d3142` / 连线 `muted #4f5d75` / 强调 `accent #eb6c36`（≤2 焦点）/ 节点白底 rx=6 圆角 + ink 描边 1px / 节点名 `sans-serif` 12-14px 600 居中（Google 字体 Geist 换系统 sans-serif）、安全红线清单；2026-08-16 精简——移除防重叠 6 条/≤300 行/4px 网格等**质量**硬规则（回归原仓库建议层），改「最小可行动例模板」降低 fast 模型创作门槛，详见 CHANGELOG），代码校验链只做兜底（LLM 失控时拦截，见下）→ LLM 输出 ```chart 围栏 → `app/services/chart_renderer.py` 校验链 → PIL 渲染 PNG(2x) 存 `assets/chart_{hash8}.png` → 替换为 `![图表](assets/chart_{hash8}.png)`
- **只入 notes**：`_POLISH_FORMAT_FNS` 白名单无 chart，polish 不注入；`AVAILABLE_OPTIONS` 按 source_type 控制勾选（video/webpage/pdf/docx/pptx/text 有，xlsx/audio 无）；前端排除 epub（走独立解读管道）
- **协议收紧**（2026-08-16，依据 `pi-docs/图表围栏内部协议设计说明.md`；该目录已于 2026-10-18 清理）：只认**专用围栏**——```chart（内容须含 `<svg` 根元素）与 ```fig（语义 JSON）；svg/xml/json 围栏、裸内联 `<svg>` 一律不碰（用户内容零触碰）。取舍：误伤用户内容（静默改写用户数据）> LLM 偶尔不遵循围栏约定（warning 重新生成）。⚠️ **fig 与 chart 有意不同**：chart 内非 SVG 原样保留（可能是协议外内容），而 fig 是毫无歧义的协议标记，解析失败**必须零残留替换**为失败文本
- **校验链**：XML 良构 → viewBox ≤640×640 → 有形状元素 → **有非空文字内容（2026-08-16 起：<text 标签必须含非空白文字——空 `<text></text>` / 自闭合 `<text/>` 与零文字图一并拦截，防 LLM 只画框不写字产出无字图）** → PIL 渲染（失败重试 1 次）；任一失败**替换为** `📊 图表生成失败（{reason}）` 文本（零残留——产物永不含围栏，内部协议不外泄）。勾选图表但 LLM 未输出图表围栏 → 完成时 warning「图表未生成：请重新生成」（`_has_chart_fence()` 判定，两种围栏都算「已出图」）。**fig 路径的失败前置**：JSON 解析失败 → 「图表数据格式错误」；`LayoutError` → 「布局失败：{原因}」（节点超限/缩放下限/索引越界均带定位信息）；同样零残留、不写任何文件
- **字体实测定案 + SVG 片段保存（2026-08-16）**：resvg→PIL 切换（见下）。`_detect_chinese_font()` 探测系统第一个中文字体**文件路径**（`ImageFont.truetype` 加载成功即可用，候选 msyh.ttc/msyh.ttf/simhei/simsun/PingFang/Noto/WenQuanYi，进程内缓存探测结果 + 按 `(path,size)` 缓存字体对象），PIL 用该路径渲染中文；**prompt 不再约束 font-family**。**SVG 源码片段同步保存** `assets/chart_{hash8}.svg`（与 PNG 并列，用户提议）：不用于展示（assets 白名单无 svg），供①调试——文字没渲染时查看 LLM 原始输出②用户深度使用时导出矢量图。⚠️ **这个「导出矢量图」的用途目前用户实际够不到**：资产白名单 `_ALLOWED_IMAGE_EXTS` 拒绝 svg（`/api/assets` 返回 400），导出也只按 markdown 引用收集、不扫目录。另 **fig 路径额外落盘 `assets/chart_{hash8}.json`**（语义源，同上纯旁路）；两者都在 `_collect_images` 实测复核中确认**零外泄**
- **安全过滤**：`<script>` / `on*` 事件属性 / `xlink:href`·外部 URL 引用按不可信内容剥离（纵深防御——最终产物是 PNG 不执行脚本）
- **PIL 自绘渲染器（2026-08-16 resvg→PIL 切换）**：resvg 全系（py 绑定 0.4.0 + CLI v0.47/v0.48 官方二进制）在 **Windows 无法渲染 SVG 文字**——fontdb 加载系统字体正常（`--list-fonts` 可见 Arial），但 usvg text 解析一律 `No match for "<font-family>"` → 跳过 text（上游普遍 bug，非绑定/配置问题，实测 v0.46-v0.48 全系列 + cargo 编译 v0.48.1 + 官方 v0.47 win64 二进制皆复现；v0.48.1 无 Windows 发布即 Windows CI 失败佐证）。改 **PIL 自绘 SVG 子集渲染器**（`_svg_to_pil`）：覆盖 rect（rounded_rectangle）/text（`anchor` 基线锚——SVG y 即基线）/line/path/circle/ellipse/polygon/polyline，非子集元素（image 等）忽略（漏画优于画错）。**path 用正则 tokenizer 分离命令与数字**（`_path_polylines`，兼容粘连写法 `M470 90 V130`——此前 split() 解析漏命令导致虚线不画，用户实测反馈）；**贝塞尔曲线补齐**（2026-08-16）：C/S 三次 + Q/T 二次采样为密集折线（`_bezier3`/`_bezier2`，S/T 控制点反射 `_reflect`，Z 闭合回子路径起点），A 椭圆弧跳过——此前曲线命令跳过参数漏画，图表曲线连线消失，三张手写示例图（line/虚线/折线/曲线/箭头/S-T 平滑/相对命令）实测全渲染；**polygon 描边闭合**（2026-08-16）：SVG 语义 polygon 最后一点回起点，draw.line 不自动闭合——此前菱形/箭头描边缺最后一条边（用户实测「流程图错位和边框缺失」），`_draw_poly` 对 polygon 追加首点闭合、polyline 保持不闭合；**transform 支持**（2026-08-17）：`<g transform>` 此前整块不画/画错位（用户实测「图表错位/缺内容」头号来源——原仓库 diagram-design 规范大量用 `<g transform>` 定位 + `dominant-baseline=middle` 节点居中），`_parse_transform` 2x3 affine 解析 translate/scale/rotate/matrix 组合 + `_svg_to_pil` 递归遍历 `<g>` 累计变换至子元素 + `_map(x,y,m)` 统一映射；**text 组合锚**：text-anchor（start/middle/end）× dominant-baseline（middle→中线 m / hanging→ascender / text-after-edge→descender，默认基线 s）组合成 PIL 双字母 anchor，`dominant-baseline="middle"` 节点内文字真正垂直居中；含旋转/斜切时 rect/ellipse 退化多边形近似（漏画圆角优于画错位置），字号/笔宽按 transform 局部缩放因子自适应（`tots = scale * lscale`）；`stroke-dasharray` 虚线按弧长分段绘制。零新依赖（PIL 已随图片优化打包，spec 无需额外收集；resvg_py 收集已从 spec 移除）。校验链/安全过滤/SVG 片段保存/失败标注逻辑不变
- **后处理挂载**：generator.py `generate()`/`generate_both()` + generate.py `_post_process`（在 `_normalize_generated_images` 之前，先替换 assets 相对路径再统一 URL 规范化）

### 适配器注册

`app/adapters/__init__.py` → `AdapterRegistry.register()`。新增管道只需实现 `accept()` + `adapt()` 并注册即生效。注册顺序决定路由优先级：Video（含 BilibiliSubtitle / YouTubeSubtitle 字幕优先提取）→ Document → Image → Webpage → Text（兜底）。共 5 个适配器。

### 其他注册表

同类插件化模式还用在两处：
- **ProductRegistry**（`app/generation/product_registry.py`）— 产物注册表，定义 notes/polished 及其依赖拓扑。新增产物类型只需加一个 `ProductDef` 即可，无需改分发逻辑或前端 UI。
- **ToolRegistry**（`app/agent/tool_registry.py`）— Agent 工具注册中心，替代硬编码的 `_register_chat_tools()`。每个工具是独立 `ToolDef`，注册后自动被发现和调用。

FastAPI 依赖注入（`app/core/dependencies.py`）提供 `get_store` / `get_config` / `get_guard` / `get_config_lock` / `get_query_service` / `get_search_engine` 依赖，消除路由函数中 `request.app.state.xxx` 样板代码。**19 个路由模块中 18 个用 `Depends`**（`references.py` 两者都没用——它只有进程内暂存，不碰 store/config，本来就不需要）；仍有 `request.app.state` 残留的 **3 个**：`generate.py`（7 处）、`ingest.py`、`markdown_edit.py`（1 处，`update_index` 的 `getattr(..., None)` 兜底，那个 `None` 是有意的：检索坏了不该让编辑保存失败）。⚠️ 计数以 `ls backend/app/routers/*.py` + `grep -l "app\.state"` 的输出为准（**该 `ls` 把 `__init__.py` 也算进去**，故文件数 = 模块数 + 1；2026-10-26 实测 **20 文件 = 19 模块 + 1**，`grep -l "Depends("` 命中 18 个，`app.state` 残留 3 个）。⚠️ `search.py` 2026-10-26 已迁（新增的两个端点用 `get_search_engine`）—— 当时它那份**只出现在注释里**，而 grep 认不出注释，所以「按 grep 计数」这条纪律遇到「注释里讨论这个属性」时会假阳性：改的是措辞，不是判据。此前写的「15 个里 12 个已迁移、ingest 已迁移」也是同一类错误（模式 #54：有几处同源是搜索题）。

### 只读命令行（`limo`，2026-10-04 三期）

```
limo_cli/（命令解析 / 渲染 / 退出码）
    ↓
app/services/query.py::QueryService（契约成形）
    ↓
Store / SearchEngine（数据真相）
```

- **七命令**：`status / list / search / get / export / folders / doctor`。**不做写操作**（import/generate/edit/delete）——成本、并发、超时、幂等、误触发的风险都在写侧
- **⛔ CLI 不解析库格式**：不读 `index.json`、不读 `search.db`、不拼磁盘路径。照着复刻一遍 = 第 6 个副本（现有 5 处），而副本的失效方式是「格式演进时漏改一处，它静默读错」
- **两个传输层同一契约**：`HttpSource`（默认，`GET /api/cli/*`）与 `LocalSource`（仅 `doctor --local`，后端没起时直开本机库）。形状由 `QueryService.to_dict()` 定义，`sources.py` 只搬运。⚠️ 但**「一次取全」两边的表达不同源**（本地 `limit=None` / HTTP `all=true` 且不传 `limit`）——模式 #61
- **输出契约**：`--json` 时 **stdout 只有 JSON**、诊断与错误全走 stderr（**不用 TTY 检测**）；顶层恒有 `schema_version`（CLI 侧 `EXPECTED_SCHEMA` **刻意不 import 后端常量**——同仓 import 会让「用户机器上的旧 exe」永远检测不出来）；`Page` 上 `has_more`+`next_cursor`（**能继续翻**）与 `truncated`+`continuation`（**到头但没给完**）并排不混用
- **退出码** `0/2/3/4/5/6`（+130 SIGINT）：0 成功**含空结果**｜2 用法｜3 未找到｜4 后端不可达｜5 数据/响应异常｜6 内部错误。⚠️ **按 `code` 判不按 HTTP 状态**（`recoverable=True` 且未显式 status 的 AppError 返回 **HTTP 200**，按状态判会把「找不到」读成成功）
- **契约文档两处**：库内 `~/.limo/AGENTS.md`（`limo doctor --write-agents` 安装，**默认不覆盖**，`--force` 先备份）+ 仓库根 [`AGENTS.md`](AGENTS.md)（读者是改这条链的人）
- **门禁** [`scripts/check-cli`](scripts/check-cli) 16 项：自启**独立端口 7419**（借用户的 7413 会让「后端不可达 → exit 4」永远绿、而绿的原因是错的）；每项同时查退出码 + 结构化标记 + **是不是假成功**；含 3 条负向对照

### 引擎系统

`app/engines/__init__.py` → `EngineRegistry.register()`。四类引擎：
- **ASR**：bcut-asr（B站必剪，免费云转写）→ dashscope-asr（云端 Paraformer）。faster-whisper 已从内置移除（本地转写需自行安装；官方模板 [faster_whisper_plugin.py](backend/plugins/examples/faster_whisper_plugin.py)，进程外 + 一键装依赖）
- **OCR**：dashscope-ocr（云端，需 API Key，model_name 透传——`engine_bindings.ocr` 可绑 OCR 专用模型）。**OCR 名称检测**（2026-08-04）：`_PROVIDER_IMAGE_RULES` dashscope 匹配 `ocr` → qwen3.5-ocr 等自动标 image（生成时「OCR 模型」可选到图片转文字小模型）；检测结果用户可手动取消/标记（updateModel capabilities）。**默认绑定**：未绑定时自动绑 `qwen-vl-ocr-latest`（图片转文字专用）。**Tesseract 已从内置移除**（2026-08-02，体积修剪 + 插件化方向）——无 dashscope key 时本地 OCR 能力消失，扫描件出占位文本；需本地 OCR 请装三方插件参考模板 `backend/plugins/examples/tesseract_plugin.py`
- **Doc**（全 MIT/BSD，AGPL 引擎不内置）：
  - **pypdfium2**（BSD-3，默认）：C 引擎直读文字 + 嵌入原图，毫秒级。pdfplumber (MIT) 辅助表格提取。**docling 已移除**（2026-08-03，依赖 torch ~2GB 无法打包，打包版一直不可用）
  - **pdf-image**（2026-08-04）：整体转图片——渲染每页为 PNG 嵌入 markdown（供 OCR/多模态），绑定 `doc:pdf` 替代文字提取，对纯图片/扫描 PDF 更稳（绕开提取嵌入图的坏图问题）；document.py 跳过其重复渲染+OCR
  - **markitdown**（MIT）：Office/EPUB 转 Markdown
  - **python-docx** / **python-pptx** / **openpyxl** / **epub-container**（自研，2026-08-26 替代 ebooklib）：各格式专用引擎
- **Embedding**（2026-10-26，语义检索；**阶段 1–4 全部落地**，见「文档库」段的语义检索条目）：**两个引擎** —— `dashscope-embedding`（`text-embedding-v4`，1024 维，云端，零新依赖）+ `local-bge-small`（[embedding_local.py](backend/app/engines/embedding_local.py)，bge-small-zh-v1.5 ONNX int8，512 维，**免费 / 零外发**，`is_local=True`）。⚠️ **本地引擎有两种形态（2026-10-27 起）**：**内置** `embedding_local.py`（源码版零安装可用）+ **进程外插件版** [local_embedding_plugin.py](backend/plugins/examples/local_embedding_plugin.py)（`EmbeddingPlugin` 基类 + **常驻子进程**，打包版靠它 —— 冻结的 exe 没 pip，内置那份补不了依赖）。两者 `name` 同名（`local-bge-small`）、`model_id` / 维度 / 默认模型目录一致 ⇒ 插件覆盖内置且**向量库不必重建**。⚠️ **它不是 `EnginePlugin` 而是 `EmbeddingPlugin` 子类**（2026-10-27 单开）：`process(file_path)` 是「一个文件 → 一段文本」，装不下「一组文本 → 一组向量」；而「进程外每次 Popen 加载 3–5 秒撞 `QUERY_TIMEOUT = 3.0`」由**常驻子进程 + NDJSON over stdio** 解决（不是每次起进程）。扫描 / 门控 / 启用 / 卸载 / 一键装依赖**全部复用**。⚠️ 口子原则的实质（重依赖不进打包）仍由「`onnxruntime`/`tokenizers` 不在 `pyproject.toml`、模型 22.8MB 不随包」满足。⚠️ **三个可选钩子 `precheck()` / `warmup()` / `release()` 刻意不进本 Protocol**（`runtime_checkable` 加成员会让云端引擎不再满足），消费方用 `getattr` —— 归属见「公共常量的家」表。⚠️ **与前三类性质不同**——asr/ocr/doc 是「文件 → 文本」、受 `engine_bindings` 子场景管辖；embedding 吃**文本**吐**向量**，前端不渲染它（各处按具体 category 取，无「遍历所有类别」的消费方）。⚠️ **刻意不继承 `core.protocols.Engine`**（那个 ABC 的 `execute()` 对本类是永不被调用的空壳）→ [`engines/embedding_base.py`](backend/app/engines/embedding_base.py) 用 Protocol。⚠️ **三条契约**（`test_embedding_engine.py` 逐条钉住）：**按 `resp.data` 的 `index` 重排**（端点带 index 但**不保证有序**，按返回顺序 append = 文本与向量错位、零报错）/ **已 L2 归一化**（余弦退化成点积；归一化住协议模块，两份实现会在极小模长上分叉）/ **空输入返回空且不发起调用**。⚠️ **Key 的抛点在 `client` property 不在 `__init__`**（无条件注册，设置页要在没 Key 时也能列出）；`max_retries=0`（重试纪律归上层）。⚠️ **阶段 1 不写 `engine_models` / `engine_bindings`** —— 没人绑定 = 等于不存在，写进去会让末尾那次 `save_config` 判定翻转。详见 [search-retrieval.md](docs/search-retrieval.md) §3.1 / §六
- **插件引擎**：AGPL 引擎（pymupdf4llm）以插件形式进程外加载（`~/.limo/plugins/`，command 指向子目录 venv 隔离依赖），`plugin_loader.py` 动态注册进 EngineRegistry，capabilities 的 fileTypeMap 自动追加。**向量化插件**（`EmbeddingPlugin` 子类 → `_PluginEmbeddingAdapter`，2026-10-27）同样注册进 EngineRegistry，但 `category=embedding` 不进 fileTypeMap —— 它出现在 设置 → 通用 → 检索索引 的引擎下拉（`_embedding_models` 只读 `display_name`/`model_id`/`dimension`/`is_local`/`precheck()` ⇒ **前端零改动**）

引擎绑定通过 `engine_bindings` 动态 key 分配，doc 拆为子场景（`doc:pdf` / `doc:word` / `doc:ppt` / `doc:excel` / `doc:epub`，text 变体引擎已删除无意义），设置页可独立配置。绑定值支持字符串/数字/列表三种格式，`init_engines` 启动时清理失效 key 与无效 id。

### 模型管理

Provider → EnabledModel（能力标签）→ SceneModels（`generate` / `chat` / `image_process`）。`resolve_model()`（`app/core/model_resolver.py`）是唯一模型解析入口，generate/chat/process 统一调用。多模态能力通过 `_PROVIDER_IMAGE_RULES` 按厂商 include/exclude 规则自动检测。**内置厂商 OpenCode Go（2026-08-17）**：OpenAI 兼容网关（**`https://opencode.ai/zen/go/v1`**，chat/completions，`OPENCODE_API_KEY`，Go 订阅/ Zen 按量共用 key）——`PROVIDER_DEFAULTS` 注册 + `llm_models.opencode` 预置 chat-completions 系模型（GPT/Grok 走 Responses API 不互通不预置），Qwen/GLM 高版本标 image 供图片处理/OCR。⚠️ **两条已订正的历史结论**（2026-10-02）：① base_url 原写 `opencode.ai/go/v1` → 实测 **404**，正确端点为 `zen/go/v1`；② 「该网关不实现 `GET /v1/models`」**已证伪**——实测返回 200 且免 key（标准 OpenAI 列表格式，不含 `context_length`），故模型刷新对它是**可用**的，不需要 404 兜底那条路（该分支保留给真正不实现它的自建网关）。`test_connection` 对 404/405 仍用最小 chat 请求兜底验证。

**`provider.base_url` 归一化统一走 `app/core/url_utils.py::api_root()`**（2026-10-02）——判据是「**路径是否已含版本段**」而非「是否以 `/v1` 结尾」，因为后者对两家不成立：Google 兼容层 `.../v1beta/openai`（**必须是这个形态**，缺 `/openai` 会打到不存在的原生路径 → 整条 LLM 链路 404）与智谱 `.../api/paas/v4`。⚠️ 该判据此前在 `routers/model.py`（两处）+ `routers/config.py`（一处）**各有副本**，已收敛为唯一入口 + AST 源码守卫（见关键模式 #54）。⚠️ 新增内置 provider 时**不要**再写 `endswith("/v1")` 拼接。

### 前端布局（活动栏 + 三栏插槽 + AI 浮窗）

```
[ActivityBar] [GenPanel | DocLibrary] | [PreviewArea]
```
- **左侧活动栏**（`ui/ActivityBar.tsx`，2026-10-10）：常驻最左的竖排图标列。面板项**从 `SlotRegistry.grouped().left` 派生**（顺序即注册顺序）+ 两个**动作项**（AI 对话 / 设置，它们不是 slot，故在 store 之外硬编码）。⚠️ **顶栏的面板按钮已全部删除**——活动栏是唯一入口，刻意不留第二套（两处各写一遍必然漂移，模式 #54）
- **左侧两面板默认互斥**（`lib/layout-prefs.ts` 的 `leftPanelsExclusive`，localStorage `mbw_layout`）：同一时刻只展开一个，由活动栏图标切换；**再按一次是关闭、不露出另一个**（VS Code 语义）。改回「允许并排」在 设置 → 外观 → 侧栏布局。⚠️ **互斥靠 `uiSlice.activeLeftPanel` 这个「选择器字段」，不是「打开甲时遍历同侧把乙收起」**——后者可被任何直接改单个 slot 的调用点绕过，而这样的调用点有四处（`openDocLibSearch` / DocLibrary 右键「重新生成」/ GenPanel 与 DocLibraryToolbar 的面板自关闭），漏一处就露出两个面板。统一入口：`openPanel` / `closePanel` / `togglePanel`（`toggleGenPanel` / `setGenPanelOpen` 保留为薄委托别名）
- **互斥模式下关掉的面板「连 PanelStrip 都不渲染」**——一次只该有一个面板，34px 窄条此时没有意义；并排模式保留现状（收起 = 窄条）
- 可拖拽 resize，50–600px，<80px 自动折叠为 PanelStrip
- **预览区顶部有文档标签栏**（`preview/DocTabBar.tsx`）：VS Code 双标签模型——单击=**预览标签**（斜体、可被下一次单击替换，全组至多一个），双击=钉成**常驻**，已打开的文档直接切过去；不做溢出下拉 / 拖拽排序 / 标签组持久化（基础版，溢出走横向滚动）。⚠️ **`currentDocId` 仍是唯一真相源**（不另存 `activeTabId`），激活态由 `tab.docId === currentDocId` 派生；⚠️ **延后提交**：导航意图先进 `pendingOpenMode`/`pendingClose`，`loadDocContent` **首行**才 `commitTabChange` —— 于是「取消未保存弹窗」天然等于什么都没发生（模式 #62）；⚠️ **与本页签名易混**：`tabRegistry` + `activeView` 是一篇文档**内部**的视图切换，标签栏是**文档之间**的，两者无关
- **正文字号缩放**：`reading-prefs.ts` 的 `fontScale`（默认 1.0 = 现状 16px，归入既有「阅读版式」族）→ `--read-font-size` → `index.css` 的 `.preview-area .markdown-body { font-size }`（特异性 0,2,0 压过 vendor 硬编码的 16px，**不用 `!important`**——那是「颜色覆盖」的做法）。快捷键 `Ctrl+滚轮`（**原生** `addEventListener(..., { passive: false })`，React 的 `onWheel` 拦不住）/ `Ctrl+=`/`+`/`-`/`_`/`0`。⚠️ 三个必须：写 **px**（em 会与外层 `.preview-content` 的内联 em 相乘）、版宽换算走 `measurePxOf(measure, fontScale)` **同源**、作用域限定 `.preview-area`（模式 #64）
- **对话字号是另一份（2026-10-12）**：`lib/chat-font.ts`（key `mbw_chatFont`）→ `--chat-font-scale`（无单位倍率）→ `index.css` 的 `.chat-msg-sm|md|lg`（`calc(<基准px> × var(--chat-font-scale))`）。⚠️ **基准 px 在 `ai-chat/scale.ts`（随浮窗宽度切档）、倍率在 chat-font.ts** —— 两个维度各住一处，别把 px 搬进 JS 变量或把倍率搬进 scale。⚠️ **与阅读字号刻意不共用**（两个宿主两种排版：宽屏长文 vs 窄浮窗气泡），但**档位换算复用 `reading-prefs.nextFontScale` / `FONT_SCALE_RANGE`**（`toBe` 身份断言钉住）。⚠️ 只有滚轮一条入口，键盘 `Ctrl+±/0` **仍只调阅读字号**
- 快捷键：`Ctrl+G` 切到生成面板（⚠️ **语义随布局偏好变**：互斥下没开就打开、开着就关闭且**不露出文档库**；并排下与改造前一致的「切换」），`Ctrl+I` 开关 AI 浮窗，`Ctrl+W` 循环阅读宽度，`Ctrl+K` 展开文档库并聚焦搜索框（**全局劫持，不做 `activeElement` 守卫**——与 Ctrl+A 相反，那个必须让位给输入框内全选），`Ctrl+\` 开关沉浸阅读，字号三键见上一条。⚠️ **清单真相源是 [App.tsx::handleAppKeyDown](frontend/src/App.tsx#L43)**（全部 `case` 即全集），Rust 侧放行名单由 [appShortcutKeys.test.ts](frontend/src/test/appShortcutKeys.test.ts) 双向钉住（模式 #65）——**别照抄本行**，它是摘要不是源
- **AI 对话为浮窗（唯一形态，2026-08-04 移除插槽面板）**：`FloatingAI` 覆盖式浮窗，可拖拽（边界钳制）/resize（不超视口），位置/大小持久化 localStorage（`mbw_aiPos`/`mbw_aiSize`），内部栏（会话列表/导航）可拖拽调宽 + 拖 <80px 自动折叠。**消息区三条交互（2026-10-12，全在 `MessageList` / `ChatMarkerContent`）**：① `Ctrl+滚轮` 调**对话自己的**字号（见上「对话字号是另一份」）；② 上滚后右下角「回到最新」按钮 —— ⚠️ **显隐与 `stickToBottom` 同源**（同一个 `< 40px` 判据、同一次 `onScroll` 更新），各写一套会在阈值附近互相矛盾；③ 代码块 hover 右上角一键复制 —— ⚠️ 挂 **`pre`** 不挂 `code`（`react-markdown` v10 已无 `inline` prop，块级是 `pre > code` 而行内是裸 `code`），且**刻意不复用产物侧 `CodeBlock`**（那个带 `SyntaxHighlighter` + 标题栏，挂进来 = 全部历史消息的代码块换成另一套观感，同「不复用 `preview/MarkerContent`」那类静默回归）
- Zustand store 按域拆 7 slices（`store/themeSlice.ts` / `docsSlice` / `contentSlice` / `tasksSlice` / `chatSlice` / `uiSlice` / `toastSlice`），`store/index.ts` 只剩组合层 + 模块副作用，共享类型在 `store/types.ts`（`Store` 为各 slice 交集）。**action 零跨域引用**，新增域 = 新建 slice 文件，消费方 API 零改动。持久化为手写 localStorage（`mbw_*` 17 个 key），无 persist 中间件。`setProcessing(key, null)` 是前缀树形删除语义（删 key 本身 + `key__` 前缀子任务），`clearDocTasks`/`cancelOneTask`/`failTask` 是兄弟函数
- **Slot 插槽注册表**（`lib/slotRegistry.ts`）：`SlotDef`（位置 left/right/center + **`component` 组件本体（必填）** + 宽度上下限 / closable / visibleKey）注册即得可拖拽面板 + 活动栏图标，`SlotLayout.tsx` 是布局引擎（50–600px，<80px 折叠为 PanelStrip，`uiSlice.slots` 驱动宽度）。⚠️ **加一个面板 = 只改注册表一处**——组件本体自 2026-10-10 起也在 `SlotDef` 里（此前硬编码在 `SlotLayout` 的 `COMPONENTS` 映射，加面板要改两处）。⚠️ 注册表**持有页面组件**，故 `store/` 侧（`uiSlice`）**不得 import 它**（会成环）；开合判据走零依赖的 `lib/panelToggle.ts`。与 AI 浮窗（FloatingAI 覆盖式唯一形态）正交——插槽管内容面板，浮窗管对话
- **对话独立 chatStore**（`store/chatStore.ts`，2026-08-04）：对话状态**不并入主 store**——流式 delta 高频更新隔离（主 store 订阅者不受波及）。chatSlice 只留 generating；AI 浮窗状态（aiVisible/aiPos/aiSize）在 uiSlice。对话组件树：`ChatCore`（容器，内部栏受控拖拽调宽）→ `ChatSidebar`（会话列表/归档折叠）/ `MessageList`（分页窗口）/ `MessageItem`（气泡/打字机/操作栏/工具气泡/思考折叠）/ `ChatInput`（textarea/IME/@引用/token 进度）。消息模型 ChatMsg 在 chatStore.ts 定义，**勿与主 store types.ts 的 ChatMessage 混淆**

### Tauri 桌面壳

```
LiMo.exe (Rust) ──spawn──▶ python.exe (uvicorn :7413)
       │                              │
       ▼                              ▼
  WebView2 加载                 FastAPI 后端
  frontend/dist/                backend/
```

- **开发模式**：Vite dev server (:5173) + 手动启动后端，Tauri 加载 `devUrl`
- **生产模式**：`frontendDist` 静态文件 + `std::process::Command` 直接启动 PyInstaller 打包的 `LiMoBackend.exe`（非 Tauri sidecar()，避免 ACL 复杂度）
- **就绪探测**：Rust 端 HTTP GET `/health` 轮询（500ms 间隔，45s 超时），就绪后 emit `backend-ready` 事件（超时同理 emit `backend-startup-timeout`）。**前端以该事件为主要唤醒源，不再自己轮询 `/health`** —— 冷启动有 2.5s **静默期**（一条请求都不发，后端实测 ~2s 就绪），静默期后仅单次探测兜底「页面刷新时后端已在运行」的场景，另有 50s 硬兜底。⚠️ 前端轮询与 Rust 探测曾是**重复的**，而 WebView 里失败请求的报错 JS 抑制不掉 —— 详见「关键模式」#49
- **启动失败可见化**（2026-10-21；同日二轮补日志与排障闭环）：探测循环每 500ms 顺带 `try_wait` 子进程 —— **进程一退出就立刻报**（不再干等 45s），三个事件（`backend-startup-failed` / `backend-startup-timeout` / 命令 `get_backend_startup_failure`）共用一个 `StartupFailure` 形状：`stage`（`spawn`/`exited`/`timeout`）、`alive`、`exit_code`、`log_tail`、`log_path`/`log_dir`/`log_dir_writable`/`launcher_log_exists`/`backend_log_exists`/`exe_path`、`port_occupied`（**只在进程已退出时才探**，否则 7413 可能是它自己在监听）、`install_dir_writable`。⚠️ **判据是「有没有诊断」不是 `alive`**（2026-10-21 三轮改）：任何诊断都停在失败屏 `BackendStartupFailure`，放行改由用户点「仍然继续」—— 此前 `alive=true`（进程还在、只是没在 45s 内响应）走的是 `console.warn` + `initOnce()`，用户看到的是一屏 ERR_CONNECTION_REFUSED，而原因只进了控制台。`alive` 现在只决定失败屏上那句「进程仍在运行，可能只是启动慢」（慢 vs 死的下一步动作不同）。⚠️ **`isFatalStartupFailure` 已删**：它唯一的用途就是「什么时候自动放行」，判据存在一天就有人会把它当权威。⚠️ **日志目录是回退链**（`%LOCALAPPDATA%\LiMo\logs` → `%TEMP%\LiMo\logs` → 安装目录旁 `logs\`，取第一个**探针可写**的；全不可写时 `log_dir_writable=false` 就是「为什么没有日志」的答案）：单目录时安全软件拦截 / 磁盘满 / `LOCALAPPDATA` 缺失会让启动器与后端两段日志**同时为空**，而失败屏上一块空白读不出「日志系统自己失败了」。⚠️ `pick_log_dir(candidates)` **接收入参**：开发机上造不出「LOCALAPPDATA 只读」，只有传假候选列表才测得到「第一个写不进去就换下一个」（Rust 单测 5 条；`dir_writable` 的「不可写」用**父级是文件**构造 —— chmod 在管理员身份下会静默变成可写，测了等于没测）。⚠️ **事件 + managed state 两条路缺一不可**：进程在 WebView 加载完成前就死时 emit 会被静默丢弃，故 50s 硬兜底还会 `invoke('get_backend_startup_failure')` 主动拉一次。⚠️ `resolve_backend_exe` 是 `spawn_backend` 与诊断**共用的唯一解析**（否则诊断报的路径可能不是实际尝试拉起的那个）。
  ⚠️ **二轮的四条日志纪律**（全部是静默失效，见 CHANGELOG）：① 后端日志**追加**不截断（`open_backend_log`）—— `File::create` 会让「重试启动」把用户刚看到的崩溃证据清空；② `log_tail` 是**两段**（`build_log_tail` 拼 `[启动器]` + `[后端]`）—— DLL 缺失型故障后端一行不打印，只读后端日志恰在最需要证据时空白；启动器那段按 `SESSION_MARKER` 只取本次（`limo_startup.log` 跨会话 append）；③ `spawn_error` 必须进 summary —— 「exe 在但起不来」的真实原因（拒绝访问 os error 5）此前只落在日志文件里；④ 「找不到 exe」与「起不来」分两句文案（`exe_path.exists()` 判），处置完全不同（重装 vs 改环境）。
  ⚠️ **排障三件**：`exitCodeHint` 翻译 NTSTATUS（`-1073741515` → 缺 VC++ 运行库；**普通退出码不硬套解读**，有对照实验）；`diagnosticsText` 一键复制整块（用户不必在 `<pre>` 里跨行拖选）；安装路径警告**复用** `installWarningText` 的判据（不新建第二份文案，输入 `exe_path` + `install_dir_writable`），且**不看横幅的「已关闭」记忆** —— 那里关的是「每次开机提醒」，「解释当前失败」是另一件事。⚠️ stderr 为空的兜底文案必须点名**安装路径含中文**（中文用户名会让默认安装路径就含非 ASCII，而它正是「一行都不打印」的主力成因）
- **安装位置与运行无关**（2026-10-21 核实）：`exe_dir` 由 `current_exe()` 决定，`_internal` / `ffmpeg/` / `searxng/` 全部相对它；日志与数据走 `LOCALAPPDATA` / `~/.limo` ⇒ **换盘符、换目录都不影响，重装换目录也不丢数据**。⚠️ 真正的三类风险：装到 `C:\Program Files`（安装器是 `currentUser` 模式，**安装期**就失败，不是静默）、路径含中文/空格（启动期，失败屏与顶栏横幅都会提示）、安装不完整/被隔离（失败屏现在直接说「找不到 exe」）。`installer.nsi` 无条件插 `MUI_PAGE_DIRECTORY`，所以这个场景是真实存在的
- **进程管理**：`SidecarHandle(Mutex<Option<Child>>)` 持有进程句柄；窗口关闭 → `RunEvent::ExitRequested` → `child.kill()`
- **重启命令**：`restart_backend_sidecar` Tauri command，kill 旧进程 → spawn 新进程 → 重新探测
- **诊断**：`analyze_install_path()` 检查路径非 ASCII/空格/父目录可写性；`app_log()` 写 `limo_startup.log`；后端 stderr 重定向到 `limo_backend_stderr.log`
- **PATH 增强**：`enhance_path()` 优先追加安装目录下的 `ffmpeg/`（内置），再追加系统常见路径，确保 yt-dlp 等工具能找到 ffmpeg。同时设 `FFMPEG_BIN_PATH` 环境变量指向内置路径
- **宿主加速键放行**（2026-10-05，网页版 vs 打包版的唯一落点）：`.setup()` 里对主窗口注册 WebView2 `AcceleratorKeyPressed` 回调（`#[cfg(windows)] mod webview_keys`），**只对应用自己在用的键**设 `IsBrowserAcceleratorKeyEnabled = FALSE` —— 浏览器跳过自己的处理、事件继续传播到网页，其余（Ctrl+P 打印 / F5 刷新 / Ctrl+F 查找 / DevTools）**原样不动**。⚠️ 不设 `Handled`（那是吞掉、网页也收不到）、不动全局 `AreBrowserAcceleratorKeysEnabled`；键清单与 `App.tsx::handleAppKeyDown` 逐键对应，由 `appShortcutKeys.test.ts` 跨语言守卫（模式 #65）
- **API 适配**：`client.ts` 检测 `__TAURI_INTERNALS__` → 生产用 `http://127.0.0.1:7413/api`，开发用 `/api`（Vite proxy）
- **CORS**：`main.py` 使用 **Origin 白名单**（`http(s)://tauri.localhost` + `tauri://localhost` + 本机 localhost 任意端口）+ `_ForceCORSMiddleware` **仅白名单 origin 回显凭证**——阻隔任意恶意网页跨站读取本地后端（2026-08-02 从 `allow_origin_regex=".*"` 收紧；⚠️ 生产 WebView2 实际 origin 是 `http://tauri.localhost`，白名单必须含它）
- **配置**：`tauri.conf.json` — 1400×900 窗口 + `maximized: true` + `com.limo.app` 标识
- **依赖**：Rust 端 `tauri` (features=["devtools"]) + `tauri-plugin-shell` + `tauri-plugin-log`

## 关键设计决策

### 公共常量与判据的「家」（写新代码前先查，别复制）

**纪律：默认抽公共，复制要能说出理由。**

本轮 25 项审计里有 **8 项**（P2-18/19/20/22/23、P0-01、P1-11/12/14）根因都是「同一份东西
住在多处」——而它们**全都不是「不知道要抽公共」，是「抽出来的家没人知道在哪」**
（`markdown_assets.py` 收的那六份资产正则里，四份是逐字同构的）。所以这里有一张表：

| 要写的东西 | 家 |
|---|---|
| 模型能力位判据（image / document / reasoning / video） | [core/model_caps.py](backend/app/core/model_caps.py) |
| 资产路径正则（Markdown 图片改写 + tx 反向） | [core/markdown_assets.py](backend/app/core/markdown_assets.py) |
| 资源 id 校验（前后端同源，含长度上限） | [core/resource_id.py](backend/app/core/resource_id.py) |
| **第三方依赖可用性探测**（两个函数，按运行模式二选一） | [core/deps.py](backend/app/core/deps.py)。**进程内 → `probe(module, attrs=…)`**（真 import 一次 + 查属性）；**进程外插件 → `probe_venv(venv_dir, modules=…)`**（纯文件系统：目录 / 解释器 / `pyvenv.cfg` 的 base python / site-packages / 依赖名）。⚠️ **选错方向必错**：子 venv 的依赖不在主进程 `sys.path`，对进程外插件用 `probe` 会**永远**报「无法导入」——每个**装好了**的插件都显示不可用，用户照着提示反复重装而东西一直是好的。⚠️ **不要写 `find_spec(...) is not None`** —— 它对 namespace package 返回非 None 而里面一个属性都没有（2026-10-27 实测于安装版 `_internal/tokenizers/`：只有 `.pyd`、无 `__init__.py`），失效形态是「检查说就绪、一编码就报缺依赖」。`Embedder.__init__` 也必须经它判（`except ImportError` 抓不到 DLL 缺失的 `OSError`） |
| `provider.base_url` 归一（判据是「路径是否已含版本段」） | [core/url_utils.py](backend/app/core/url_utils.py) |
| 内置厂商默认表（名字 / base_url / 类型） | [core/provider_defaults.py](backend/app/core/provider_defaults.py) |
| 上下文窗口默认表 / token 估算 | [core/token.py](backend/app/core/token.py) |
| 代理豁免判据（本地/内网地址不走代理）+ NO_PROXY 合成 | [core/net.py](backend/app/core/net.py) |
| 产物自证头编解码 | [core/transcript_meta.py](backend/app/core/transcript_meta.py) |
| 产物元数据的**展示判据**（时间/字数格式化 + 取哪几项、什么顺序） | [lib/artifactMeta.ts](frontend/src/lib/artifactMeta.ts)（预览区自证条与 `VersionSwitcher` 共用；`fmtTime`/`fmtChars` 曾在后者里各有一份） |
| JSON 对象抽取（逐 `{` 试 `raw_decode`，容错前置说明/围栏） | [core/text_utils.py](backend/app/core/text_utils.py)（`chart_renderer` 的私有副本已改为一行委托，有身份断言钉住） |
| **全文索引的文本内容** = rawText + 最新版笔记 | [generation/doc_text.py](backend/app/generation/doc_text.py)::`search_index_text`。⚠️ 这是**唯一判据**，五个写点（摄入 / 重新转录 / 生成完成 / 编辑保存 / 启动重建）**全部**经 [core/search.py](backend/app/core/search.py)::`update_index()` 走它。各自拼一份的失效形态是「生成后搜得到、编辑后又搜不到」，两次写都报成功，用户只看到搜索「时灵时不灵」 |
| 全文索引的**写入口**（从 store 取当前状态 + 失败只留痕） | [core/search.py](backend/app/core/search.py)::`update_index(engine, store, tx_id)`。⚠️ **不接收调用方手上的文本**（那份常常还没落盘）；⚠️ `doc_text` 在函数内导入（`core/` 概念上不依赖 `generation/`） |
| 「**关闭思考**」的 `extra_body` 形状 | [llm/service.py](backend/app/llm/service.py) 的 `DISABLE_THINKING_EXTRA`。⚠️ `stream(disable_thinking=True)` 与 [agent/followup.py](backend/app/agent/followup.py) **共用同一份** —— 后者直调 `client.chat.completions.create`、**走不到 `LLMService`**，故形状必须抽出来（抄一份就是模式 #54）。`test_followup.py` 有源码守卫钉住字面量只此一处 |
| 「最新版笔记正文」（版本序判据） | [generation/doc_text.py](backend/app/generation/doc_text.py)（智能分类与合成共用；`synthesis` 的私有副本已改为委托） |
| 文件夹树（父子归化 / 前序 / 后代 / `父 › 子` 路径标签 / `totalCount` / **路径解析 `parse_path` + `path_key`**） | [storage/folder_tree.py](backend/app/storage/folder_tree.py) ↔ [doc-library/folderTree.ts](frontend/src/components/doc-library/folderTree.ts)。⚠️ **`parse_path` / `path_key` 只活在后端一处**（前端刻意不实现：两份要靠 fixture 防漂移，而它的失效形态是「历史里本名就含 `/` 的夹被拆开」这种静默错分）；其余结构函数两端同构，靠共享 fixture `folder-tree.json` 钉住 |
| **分类真相源**（文档级 `{doc_dir}/meta.json` + 库级 `{root}/folders.json`） | [storage/doc_meta.py](backend/app/storage/doc_meta.py)。`DOC_FIELDS`（17 个**从磁盘重推不出来**的字段，含 `title`——目录名是**有损**的，见该文件注释）与读写入口只此一份；`read_folders` 的 **`None`（不可用）与 `[]`（确实没有夹）刻意分开**。⚠️ **`index.json` 只是缓存**，`_rebuild_index` 回落到这里 —— 别在别处再造一份 sidecar 格式 |
| 原子写（临时名唯一 + `os.replace`） | [core/atomic_io.py](backend/app/core/atomic_io.py) 的 `atomic_write_text`。`Store._atomic_write` 是它的薄委托；`doc_meta` 直接调它 |
| 产物正文协议标记（`limo-block`）解析/剥离/渲染 | [core/artifact_blocks.py](backend/app/core/artifact_blocks.py) ↔ [lib/markdown-markers.ts](frontend/src/lib/markdown-markers.ts)（前后端各一份，共享 fixture `limo-blocks.json` 钉住） |
| 「用列表代替画图」的块族（`steps` / `timeline` / `cycle` / `sequence` / `quadrant`） | [core/artifact_blocks.py](backend/app/core/artifact_blocks.py) 的 `CHART_ALTERNATIVE_BLOCK_TYPES`。⚠️ **tuple 不是 frozenset**（prompt 文案由它 join 拼出，要稳定顺序）；prompt 文案 / `_chart_missing_warning` 判据 / 模块注释三处共用 —— 手抄一份的失效形态是「prompt 教了、完成期 warning 不认 → 用户照做反被报『图表未生成』」 |
| `limo-block` 存在性 / 按类型判据 | [core/artifact_blocks.py](backend/app/core/artifact_blocks.py) 的 `has_blocks`（有无）/ `has_block(md, type)`（**按类型**）。⚠️ **不要写 `"follow-up" in md` 子串判** —— 正文里讨论本协议时也会出现这个词 |
| 追问建议的生成层（prompt 拼装 / 列表项抽取 / 清洗 / 去重 / 过短过滤 / 超时与非阻塞） | [agent/followup.py](backend/app/agent/followup.py) 的 `extract_followup_items` / `_run_followup_llm`。**对话侧与产物侧共用同一份**（两侧都从 `FOLLOWUP_RULES` 拼 prompt、都走 `_run_followup_llm`）；后端**抽取层**与前端 `parseFollowUpItems` 逐字同源（共享 fixture `follow-up-parse.json`），而**清洗/去重/过短三条只在服务端**（前端解析器还要服务「模型自主写的块」，那条路上「原样呈现」才对）。⚠️ **对外载荷形状**（`{"type", "questions", "error"}` + 失败文案）也收在同一文件：`followup_payload` **只此一份** —— 对话 SSE 的 `followup` 事件与 `POST /api/chat/followups`（「换一批」）**共用**，拆成两个函数的直接后果是「什么算失败、失败说哪句话」出现第二处判据，而两个消费点渲染的是**同一张卡**（模式 #54） |
| 产物侧联网检索（抽检索词 / 并发搜索去重 / 组料 / 自证头字段） | [generation/web_search.py](backend/app/generation/web_search.py)。⚠️ 结果拼成**一份**参考资料走**既有** `reference_texts` 通道（不新建注入通道，超窗压缩与注入防御免费继承）；搜索实现**委托** [agent/tools.py](backend/app/agent/tools.py) 的 `search_web`（三级降级只此一份，另写 = 模式 #54） |
| 对话公网来源的形状（`ChatSource`）+ URL 白名单判据 + 域名显示 | [lib/chat-sources.ts](frontend/src/lib/chat-sources.ts)。⚠️ 与**库内**引用的形状（`DocRef` / `docId`）**刻意不合并**；后端主防线在 [llm/agent.py](backend/app/llm/agent.py) 的 `_safe_http_url`（两处同条件：scheme ∈ {http,https} + 有 host + 无空白） |
| **H2 切分 + `card` 节修饰符判据**（骨架轴的判据） | [services/markdown_sections.py](backend/app/services/markdown_sections.py) ↔ [lib/markdown-sections.ts](frontend/src/lib/markdown-sections.ts)（共享 fixture `h2-sections.json`；⚠️ **H2 × 标记的交叉**住 `limo-blocks.json::h2Cases`，`h2-sections.json` 保持「纯切分」定位） |
| 图片处理四模式标签 | [lib/imageModes.ts](frontend/src/lib/imageModes.ts) |
| 对话正文字号倍率（key `mbw_chatFont`；**独立于阅读字号**，档位换算复用 `reading-prefs`） | [lib/chat-font.ts](frontend/src/lib/chat-font.ts) |
| 对话尺寸三档的**基准字号 px**（sm/md/lg，随浮窗宽度切） | [ai-chat/scale.ts](frontend/src/components/ai-chat/scale.ts) 的 `msg`（值 = `chat-msg-*`，与 `index.css` 三条规则同源） |
| 面板默认宽 / 折叠阈值 | [lib/slotMetrics.ts](frontend/src/lib/slotMetrics.ts) |
| 面板开合判据（互斥 / 并排同源） | [lib/panelToggle.ts](frontend/src/lib/panelToggle.ts) |
| 侧栏布局偏好（互斥 or 并排） | [lib/layout-prefs.ts](frontend/src/lib/layout-prefs.ts) |
| **六轴的键与 label**（key / attr / storageKey / options / fallback / apply） | [lib/theme-axes.ts](frontend/src/lib/theme-axes.ts)。⚠️ **只放 label、不放色值** —— 值只有 CSS 一份（下一行），前端再存一个色值就是本轮删掉的那份副本 |
| **六轴的 CSS 数据**（accent 原语 + 派生 + 浅色底盘 + 14 条基调 + 暗色 + `--md-*` + 字体 + 纹理） | [styles/theme-layer.css](frontend/src/styles/theme-layer.css)。**前端 `@import` 与后端导出读同一份**（`_load_theme_layer_css`）；⚠️ 打包须在 `LiMoBackend.spec` 的 datas 里带上，否则**开发版全绿、打包版导出直接 500** |
| **纹理强度 → `--texture-opacity` 的反转映射** | [lib/texture.ts](frontend/src/lib/texture.ts) 的 `textureOpacity()`（**DOM 写入与导出参数共用一份**；`toFixed(2)` 是为了过后端 `_TEXTURE_OPACITY_RE`，它刻意不收科学计数法） |
| **向量索引纯算子**：切块（flat + h2）/ 分组排名 / `rrf` | [core/vector_index.py](backend/app/core/vector_index.py)。⚠️ **只此一份** —— 产品侧（后台 worker 切块 + 查询融合）与评测侧（`eval_search.py --vector`、`_experiments/02-embedding-rrf/eval_ab.py`）都 import 它。抄一份的失效形态不是「多几十行」，而是**评测与产品的切块规则悄悄分叉** —— 那时 93% / 83% 描述的是另一套实现，而两边都报成功（`test_vector_index.py` 有 AST 守卫钉住 `embed_local.py` 不得再 `def` 一遍） |
| **向量索引的写入编排**（启动对账 / 分块 / 块级增量 / 编码 / 每篇 checkpoint / 失败上限） | [services/vector_worker.py](backend/app/services/vector_worker.py)。唯一会**发起计费编码调用**的地方（除查询侧的一次 query embed）。⚠️ `MAX_FAIL` 与重试冷却住在这里、不散在调用点 |
| **查询融合的编排**（两路同深度 / RRF / 前置校验 / 超时 / `semantic` 形状） | [services/vector_search.py](backend/app/services/vector_search.py)::`VectorSearcher.fuse`。⚠️ `DEFAULT_DEPTH` / `RRF_K` / `QUERY_TIMEOUT` 三个数值只此一份；纯算子（切块 / rrf）在上一行，两者分工是「数值与编排 vs 算法」 |
| **语义检索开关的维护点**（worker + 融合层**成对**启停） | [services/vector_runtime.py](backend/app/services/vector_runtime.py)::`sync_vector_runtime`。⚠️ 启动（`main.py`）与设置页开关（`POST /api/search/vector`）**共用这一份** —— 各写一份的失效形态是「只起了一半」（融合层在用一份永不更新的索引，或 worker 在写一份没人读的库） |
| **向量索引的唯一落盘点**（`vec.db` 三表 / 四元组身份 / 状态机 / 块读写） | [core/vector_store.py](backend/app/core/vector_store.py)。与上一行**分工不同**：上一行是**纯算子**（不碰盘、产品与评测共用），这一行是**唯一碰 `vec.db` 的地方**（谁要读写向量都经它，别在 router / worker 里另写 sqlite）。⚠️ 三条写死的语义：`mark_pending` **清空 `content_hash`**（留旧值会让 worker 的「hash 相等跳过整篇」误命中）、**不删 `vec_chunks`**（块级 hash 增量靠旧块比对）、**归零 `fail_count`**（内容变了是新机会，而 `fail_count` 是「花钱的闸门」——向量重试每次都是计费调用）。⚠️ 四元组（`model_id`/`dimension`/`chunk_size`/`schema_version`）是**整体身份**，任一不符即整体不可用，**没有降级用这一档**，也**不自动重建**（重建要花钱） |
| **本地 embedding 引擎 + ONNX 句柄**（产品与评测**同一份**） | [engines/embedding_local.py](backend/app/engines/embedding_local.py) 的 `Embedder`（CLS pooling + 逐行归一化 + `max_length=512`）与 `LocalEmbeddingEngine`。⚠️ `scripts/embed_local.py` 已改为**从本模块转出**（`Embedder` + 评测形状的 `build_vectors`）—— 它此前是那 40pp 落差之后唯一还活着的「第二份 embedder」，评测与产品指向不同权重时「83%」描述的是另一套实现。⚠️ `onnxruntime` / `tokenizers` **刻意不在 `pyproject.toml`**，构造时惰性 import、缺失抛 `MissingDependency` 带安装命令 |
| **向量引擎的三个可选钩子**（`precheck()` / `warmup()` / `release()`） | 定义在各引擎类上（内置在 `embedding_local.py`；**插件版在 [plugin_loader.py](backend/app/engines/plugin_loader.py) 的 `_PluginEmbeddingAdapter`**，三个钩子同名同签名 ⇒ 消费方无需分支），消费方 `getattr` 取：[vector_runtime.py](backend/app/services/vector_runtime.py)::`_engine_precheck`（**排在 `ensure_identity` 之前**）/ [vector_worker.py](backend/app/services/vector_worker.py)::`_warm` 与 `maybe_release` / [vector_search.py](backend/app/services/vector_search.py)::`_ensure_loaded`。⚠️ **不进 `EmbeddingEngine` Protocol** —— `runtime_checkable` 加成员会让云端引擎不再满足；云端没有它们 = 「视为就绪 / 无需预热 / 无需释放」，不是「未知」 |
| **模型闲置释放的门槛**（600 秒）与三档取值表 | `DEFAULT_IDLE_RELEASE` 在 [embedding_local.py](backend/app/engines/embedding_local.py)（**引擎属性，worker 不知道**）；`RELEASE_POLICIES` / `DEFAULT_RELEASE` 在 [vector_worker.py](backend/app/services/vector_worker.py)，`DEFAULT_CONFIG["search"]["vector_release"]` 必须与之相等（`test_search_api.py` 有守卫） |
| **索引触发方式两档 + 攒批阈值** | `TRIGGERS` / `DEFAULT_TRIGGER` / `DEFAULT_THRESHOLD` 在 [vector_worker.py](backend/app/services/vector_worker.py)；闸门 `_should_run()` 同处（**排在 `_next_ids` / `_warm` 之前**，顺序即语义）。`DEFAULT_CONFIG["search"]["vector_trigger" / "vector_threshold"]` 必须与之相等（`test_search_api.py::test_默认配置的触发方式与常量同源`）；前端文案由 `capabilities.triggerPolicies`（`_TRIGGER_META`）下发，前端只存联合类型 `TriggerPolicy` |
| 风格档 / 引擎标签 / 域列表 / 公式集 / 体裁 / 读者意图 / 维度预设 | **capabilities 下发**（`GET /api/capabilities`），前端**不硬编码** |

⚠️ **最后一行的意思是「不要去前端建第二份表」**：凡是「后端注册了什么，前端就要列什么」的
集合，一律经 capabilities 下发 —— 前端硬编码一份 = 后端加了引擎/风格而前端静默不显示。

⚠️ 反过来，**纯前端内部**的共享常量（如 `slotMetrics`）放 `lib/`，**不要**经 capabilities
—— 那会把一个前端实现细节变成对外 API。

### 数据流与持久化
- **索引重建不再有损**（2026-10-18）：`index.json` 一直是「缓存」，但它此前是文件夹归属 / 标签 / 创建时间 / 来源的**唯一**存放处 ⇒ `_rebuild_index`（index 缺失 / 损坏 / 旧格式时自动触发，**一次断电就够**）只能清空它们。现在真相住进 sidecar：文档级 → `{doc_dir}/meta.json`、库级 → `{root}/folders.json`（`storage/doc_meta.py`），index **仍保留这些字段当运行时缓存**（`folderId` / `tags` 参与过滤计数，每次列 N 篇都读文件太慢），只有重建才回落 sidecar。⚠️ **恢复主键是 `dir_name` 不是文档 id**（重建把 id 由正文 sha256 重推、必然变）—— 这也是 sidecar 必须住在**目录**里的原因。⚠️ **`_save_index` 顺序固定：先 folders.json、后 index.json**（两者不一致时让**真相领先缓存**）；空库不凭空造文件。⚠️ **`_migrate_classification_sidecars()`**（启动时、`_load_index` 之后）把老库 index 里的分类补写成 sidecar，**只写 sidecar、一个字节都不碰 index.json**，幂等判据是「**读得出来吗**」而不是「文件在不在」（损坏的 sidecar 同样自愈）。⚠️ **刻意不从 `index.json.bak` 回落** —— 那份存的是**损坏原文**（拷它是为了给人抢救，不是给代码读），写一条「有兜底」的分支只会让人以为这条路径有救。**已知边界**：升级到本版本**之前**索引就坏了的老库，那部分分类确实回不来。六个写点（`save_transcript` / `save_transcript_full` / `set_transcripts_folder` / `update_transcripts_tags` / `apply_classifications` / `delete_folder`）都要 `_persist_doc_meta`，批量操作是「收集命中项 → 逐篇 persist → **一次** index 写盘」
- **文档 id 唯一不变量**（2026-10-12）：`id` 是主键（前端 React `key` + `get_transcript` / **`delete_transcript` 的 `find` 定位**）—— 重复 id 的真实危害是**第二条永远删不掉**，前端那句 `Encountered two children with the same key` 只是它唯一的可见征兆。两条防线：`_rebuild_index` 的 id 派生走 `_unique_tx_id(seed, taken)`（按 `transcript.md` **内容**派生，⚠️ **首次尝试与改造前逐字相同** ⇒ 绝大多数库零回归，撞了才加盐、16 次仍撞退化随机 id）；`_dedupe_transcript_ids()` 启动时定点修**已有**脏索引（幂等、**只改 `id` 一个键**、不重扫磁盘、不碰 tags/folderId/来源 —— 第 3 轮审计 P0「启动迁移整库重建抹掉分类」的教训）。⚠️ 重复的来源是磁盘上两份**内容相同**的目录，**不要**改成「输出前静默去重」（违反失败显式化，且掩盖「磁盘上真有两份」）。⚠️ 验其中一道防线时必须把另一道按住，否则对照实验全绿而结论是错的（见 `test_store_index_recovery.py`）
- **配置唯一入口**：`app/core/config_io.py` → `save_config()`。禁止业务模块直接 `yaml.dump`
- **凭据不落 `config.yaml`**（2026-10-04 二期）：值送 `core/secrets.py` 的 DPAPI 密钥库（**ctypes 直调 `crypt32.dll`，零新依赖**，落 `%LOCALAPPDATA%\LiMo\secrets.bin` —— **刻意不放 `~/.limo/`**，那是会被同步/备份/打包分享的目录），YAML 只留 `secrets_ref: dpapi:limo`。**四张成对表**（`SECRET_SCALARS`+`SECRET_LISTS` 抽↔填 / `DERIVED_AUTH_SUFFIX` 剥↔重算 / `fold_to_providers` 下沉↔跳过 / `_EMPTY_CONTAINERS` 键名↔类型）是唯一真相源，**每张的两侧对不上的症状都不抛错**（Key 消失 / 明文回盘 / 启动即崩）。⚠️ **失败显式抛 `SecretsUnavailable`，不静默退回明文**（静默退回 = 二期白做且 YAML 上看不出异常）。⚠️ **定位是卫生不是安全边界** —— 挡的是「配置文件被复制走」的被动泄漏，挡不住同账户的主动恶意进程。⚠️ **`save_config` 的隐藏前提**：`elif disk.get(SECRETS_REF_KEY)` 分支（删库+撤 ref）**要求入参是回填过的内存配置**，拿磁盘 YAML 直调 = **密钥库被无声删除**；新增调用点前先问「这个 cfg 是 load 过的吗」
- **密钥与凭据自检**：`main.py` 启动时调 `config_io.startup_check(cfg)`（查 DPAPI 可用性 + **磁盘** `config.yaml`/`.bak` 有无非空敏感字段）；⚠️ 它**必须读磁盘**——入参是 `load_config` 回填过的内存 cfg、处处非空，拿它判等于恒绿（#39「判据看的输入不是它以为的那个」）。全量内容扫描走 `scripts/check-secrets.py`（**分工不是重复**：启动路径上跑全量会让冷启动等一次目录遍历，而结论每天都一样）。`.bak` **同生同灭**（`load_config` 每次启动 `copy2` 的是磁盘文件本身，主文件干净它就干净），故照查即可，无需特殊处理
- **配置落盘形态的守卫**：`_EMPTY_CONTAINERS` 绑「键名 ↔ 类型」（`llm_models` 是 **dict** 不是 list，写错的表现是**下次启动 `AttributeError`** 而非保存报错）；测试钉住「表里的类型 == `DEFAULT_CONFIG` 的真实类型」
- **模型解析唯一入口**：`app/core/model_resolver.py` → `resolve_model()`。generate/chat/process 统一调用
- **引擎映射唯一入口**：`_resolve_doc_engine` 用 `model_name` 查找（非 `provider_id`），支持字符串/数字/列表三种绑定格式
- **读路径唯一入口**：`app/services/query.py` → `QueryService`（2026-10-04）。分页 / ID 解析 / 版本编排 / meta 装配 / 截断标记全部收口在此，**HTTP 与将来的 CLI import 同一份实现**（不是「写法相似」）。`app/services/*.py` **零 FastAPI import**（同 `services/epub_pipeline.py` 一分层），分层照抄 `routers/epub.py`（薄）→ `services/epub_pipeline.py`（厚）——router 留在 `routers/`，`services/` 不放 router。惰性构造在 `core/dependencies.py::get_query_service`（`main.py` 先建 `Store`、后建 `SearchEngine`，提前构造会拿到 `None` → **静默空结果**）
- **产物自证头**：`notes`/`polished`/`process` 落盘时首行写 `<!-- limo-meta {json} -->`（从哪来 / 怎么来的 / 是什么 / 可信度边界四条）。编解码收口 `core/transcript_meta.py`（`_parse_envelope` 只认「第一行 + HTML 注释 + JSON + dict + 有 `v`」，`parse_meta`（composite）与 `parse_artifact_meta`（产物）各在 envelope 上做自己的 schema 校验）；装配用 `generation/provenance.py::artifact_meta()` + `PROMPT_VERSION`（**当前 `"20"`**，2026-10-26；逐档记录以 [prompts/__init__.py](backend/app/generation/prompts/__init__.py) 的版本注释为准 —— 本行曾长期停在 `"5"`。2026-10-05 一天内连推四档：骨架/文风/风格表改动 → "2"，同日验收修订 → "3"，新增速查区 → "4"，真实重跑验收后收紧材料索引措辞（禁提内部标签名）→ "5"；改任何发给模型的文本都要 bump，纪律见 [prompts-inventory.md](docs/prompts-inventory.md) §13）。**普通 `transcript.md` 刻意不加**（它会进模型 Prompt，加头 = 改生成输入）；**无头 ≠ 损坏**；编辑保存必须**带回原头**；**派生视图（思维导图）必须剥头**。⚠️ **渲染层另有一条统一规则**（2026-10-11）：**正文里的 HTML 注释一律不显示**（前端 `markdown-plugins.ts::rehypeStripComments` + 后端 `docx_export.py` 剥 bs4 `Comment` 节点），但 **`.md` / `.html` 导出刻意保留**自证头（`_pick_artifact` 用 `raw_content`）—— 两者不矛盾：注释是**文本源**里的元信息，docx 是纯渲染格式没有源码可留
- **引擎绑定保存合并**：`set_engine_bindings` 合并而非替换 — 保存 OCR 绑定不会删除 ASR 绑定。`cfg["engine_bindings"] = stored`（全量替换）→ `existing.update(stored)`（增量合并）
- **LLM Key 双向同步**：`update_provider` 自动同步到 `llm.api_key`；generate router 在 `llm.api_key` 为空时从 providers 回退
- **引擎前端后端驱动**：`GET /api/capabilities` 返回 `fileTypeMap`/`scenarioLabels`/`engineLabels`/`styleLabels` 等完整元数据，前端 `constants.ts` 不再硬编码引擎名/文件类型。新增引擎只需后端注册 + `config.py` builtin_models 加一行 desc，前端自动发现

### 导入与去重
- **唯一导入入口**：GenPanel 是唯一导入入口，DocLibrary 只负责搜索/筛选/列表
- **两层去重**：URL 去重（`find_by_url`）在请求到达时拦截；内容去重（SHA256 contentHash）在提取正文后拦截。Store 级拒绝写入
- **导入区纯粹化**：ImportSection 不受 currentDoc 影响，按钮永远是「导入」

### 生成与处理
- **双产物并行**：非流式端点走 `generate_both()`（`asyncio.gather`）；流式端点（`/generate/{id}/stream`）用 `_run_product` 协程 ×2 + `asyncio.Queue` 汇合事件（chunk 交错到达 SSE 顺序安全）+ `asyncio.gather` 收拢。⚠️ **gather 返回顺序 = 任务创建顺序，解包变量名必须同序**（曾解包反了 → notes/polished 内容互换写进对方文件）。source_type 派发 prompt（视频/网页→通用，PDF/Document→文档 prompt，Excel→分析报告+数据摘要）
- **思考链（deepseek-v4）**：`reasoning_content` 逐段回调（`on_reasoning`，不混入正文）→ SSE `type=reasoning` → 前端 ProcessingPlaceholder"模型思考中"。思考链是临时展示，正式文本流式后直接替换。**思考强度**：`reasoning_effort` 经 `extra_body` 传递（qwen 忽略未知字段兼容），三档 low/high/max，**思考模式默认开启且 effort 默认 high**。polished 是忠实整理任务传 `"low"`（默认 high 思考静默 90s+）、参考文件摘要同传 `"low"`；chunk 间隔超时 150s 容忍长思考、总超时 330s（推理链 chunk 到达时 anext 正常返回不触发超时）。⚠️ **思考模式下 temperature / presence_penalty / frequency_penalty 不生效**（不报错，静默失效）——故续写时的 temperature 递减对 DeepSeek 是空操作（保留为兼容其他厂商）；`top_p` 相反，仅思考模式生效且 <0.95 按 0.95 处理（2026-09-23 依官方「思考模式」页补注）
- **图片→LLM 双轨制**：视频和文档走不同路径。视频：「AI 画面理解」一键开关 = VL 管线 + 网格图发 LLM（对标 BiliNote），网格图不嵌入笔记。文档：保留文档图片→AI 分析图片→图片预处理三级，文档图片嵌入文章有价值。`send_to_llm = bool(image_analyze)`，前端保证门控。
- **目标语言**：config.yaml `target_language` + prompt 参数化，8 语言可选
- **LLM 续写**：`finish=length` → 自动续写 3 轮，temperature 递减
- **流式 + 取消**：SSE 端点，cancel_event 真正中断 token 消耗，取消保存 partial 可续写
- **Prompt XML 分节**：`PromptComposer.section()` + `compose()` 组装 10 个标签（role/task/source_content/instruction_priority/hard_constraints/output_language/style_preference/output_format/reference_texts/user_request），空节自动跳过。`_INSTRUCTION_PRIORITY` 声明**四层**优先级：hard_constraints > output_language > user_request > style_preference（合成管道另有 `_SYNTHESIS_INSTRUCTION_PRIORITY` 三层版，无 style_preference 节）
- **参考文件（Reference Text）**：生成时注入参考资料（如评论区观点），仅 notes 注入 `<reference_texts>` section（user_request 前），polished/Excel 不注入。流式 GET 无法传大 JSON → `POST /api/references` 内存暂存（TTL 20 分钟）+ 短 id，生成传 `reference_ids`。前端参考文件 state 仅会话内不持久化。**单文件上限 1MB**（前后端同值，2026-09-23 由 200KB 放宽）
- **参考文件暂存**（`core/reference_store.py`）：内存 dict + TTL，**过期项全表清理**（`_purge_expired_locked()`，save/get 两路径都调）+ 双闸门（条目 32 / 总量 32MB，超限 `AppError(REFERENCE_STORE_FULL)`，闸门在清理**之后**判）。⚠️ **不能按入参惰性清理**：前端一次只持一个 id，换文件即换 id → 旧 id 再不会被查询，只清入参的实现永远碰不到它（详见「关键模式」第 42 条）
- **参考文件超窗压缩**（`generation/reference_compressor.py`，2026-09-23）：`原文 + 参考 > 窗口 × 0.7`（与对话预算同源）时先用当前生成模型逐份摘要，装得下零成本原样返回。失败降级逐份截断 → 总量兜底。**只在 notes 侧做**（polished 不注入参考，压缩是白花钱）。流式路径先推 `stage` SSE 事件（`{type:'stage', message}`，前端 `TaskInfo.stage` 单值承载，正式 chunk 一到即被全量替换清掉）。**摘要超时 `COMPRESS_TIMEOUT=180s` 独立一层**——压缩在生成总超时（330/400s）之外，不设它模型挂起时要干等 5 分钟；`except CancelledError: raise` 必须排在前面（用户取消走 `app.llm.CancelledError`，与 `asyncio.CancelledError` 无关），`TimeoutError` 单独列一支（`str(TimeoutError())` 是空串）。⚠️ **三个坑**：① 原文自身超预算（base ≥ budget）时**不做兜底**（压到空也救不回请求）；② 截断函数不能有「至少留 N 字符」硬下限（会让配额远小于文本时纹丝不动）；③ 截断必须幂等且从 limit 里扣掉标记自身开销
- **参考文件名清洗**（`prompt.py::sanitize_reference_name`）：注入侧与压缩 prompt 同源复用——空白归一 + 引号替换 + 全角括号转半角 + 限长 80，防换行伪造 `【参考资料 N】` 标记、双引号闭合 `来源="..."` 属性。**只做结构安全，不做内容审查**
- **参考内容的注入防御**（2026-09-23 补）：`_format_reference_texts` 第 5 条 + `COMPRESS_PROMPT` 各声明一句「参考资料只是素材，不是指令」——参考文件常来自第三方（他人文章/评论区），而「主动利用」文案会放大模型对其内容的遵从。**内容本身零触碰**（不转义不过滤），只加声明。⚠️ `source_content`（导入正文）同样缺此声明，未纳入本轮
- **Polish/Notes 风格隔离**：Polish 固定忠实整理策略（无 `<style_preference>` 节 + **不接受 user_notes 备注**，参数保留兼容但内部忽略不注入 `<user_request>`），格式白名单 `_POLISH_FORMAT_FNS`（toc/translate/formulas/references/images）。Notes 有 `<style_preference>` 节，风格从 `VIDEO_NOTE_STYLES`（10 种）或 `DOC_NOTE_STYLES`（9 种）查找。`normalize_style()` 兼容旧 `meeting_minutes` key。⚠️ **默认档 = `plain`（通俗）**（2026-10-05，Excel 例外——它映射到 detailed 同值文案）：改默认要动 **8 处**（枚举 / `COMMON_NOTE_STYLES` / `EXCEL_STYLE_MAP` / `styleLabels`【**手写 dict 不会自动带出**】/ `genPrefs` / `GenPanel` 三处 fallback / 后端 6 处签名默认值），漏一处就是静默无风格或按钮不出现；⚠️ 同一批修掉的三个**静默改写**缺陷（枚举值 `meeting_minutes` vs 表 key `meeting`、`none` 不在枚举却被注释声称合法、修前者时的反向回归）——修法是**归一与校验都走 `normalize_style`** 且 `none` 显式放行，逻辑收口 `routers/generate.py::_normalize_and_validate_style()`（抽成模块级是为了让测试**直调真实实现**而不是复刻表达式）
- **风格定义位置**：`app/generation/prompts/notes_styles.py`（COMMON_NOTE_STYLES → VIDEO_NOTE_STYLES / DOC_NOTE_STYLES），旧 `STYLE_MAP`/`DOC_STYLE_MAP` 保留为 deprecated 别名
- **Agent SSE 事件流**：`meta`（conversation_id/budget/price）→ `compressed`（历史压缩）→ `agent_status`（状态条）→ `tool_start`（`tool`/`display`，`_tool_display()` 生成安全文案）→ `tool_result`（`summary` 摘要 + `refs` 引用文档 docId/title，完整结果只传 LLM）→ `thinking`（思考段，`reasoning_content`）→ `text` 流式 token → `done`（cancelled?）。**引用不独立成事件**，随 `tool_result.refs` 携带。C 端不暴露完整工具结果到前端 UI。协议详见 docs/对话协议.md
- **对话文档注入**：`smart_truncate(rawText, 1000)` 按句子边界截断（优先级：`。` > `. ` > `\n\n` > `\n`），不再硬截 `[:1000]`

### 并发与安全
- **并发控制**：`ExecutionGuard` 双层信号量 — `cloud_semaphore(5)` 给生成/处理/云端引擎，`local_semaphore(2)` 给本地 CPU 引擎（文档解析等）。本地慢操作不抢占云端快操作的槽位。
- **并发安全**：Store `threading.Lock` 防 RMW 竞态；engine `copy.copy()` 防 api_key 串用
- **安全加固**：path traversal 防护、上传 2GB 预检、SSRF 防护（`url_security` 共享校验，5 个下载入口全部加固）

### 前端关键模式
- **GenPanel 类型门控**：5 类型选择器（网页|视频|文本|文件|图片），文件 5 子标签（PDF|Word|PPT|Excel|EPUB），每种类型只看到相关选项
- **PreviewArea 按 sourceType 分派 tab**：视频 3tab / 文档 3tab / Excel 3tab / 图片 5tab / 网页 3tab / 文本 3tab + 思维导图
- **Transcript 视图分派**：JSON+segments → 时间轴（视频）；否则 → 完整 Markdown 渲染
- **处理进度按 tab 粒度**：生成笔记只影响 summary/polished tab，图片处理只影响对应 mode tab
- **主题系统**：六轴独立 — `data-theme`(明暗) × `data-accent`(26套配色) × `data-base`(14种基调) × `data-texture`(6种纹理) × `data-font`(5种字体) × `--texture-opacity` 强度滑条。CSS 变量（`--tk-*` 全动态）+ Tailwind v4 `@theme` 引用。一键预设包（20个: 护眼/经典/粉色/雅致/国风各4）。Markdown 用 `--md-*` 变量（全部引用 `--tk-*` 或 HSL 派生，零硬编码色值）。⚠️ **数据层已抽到 [styles/theme-layer.css](frontend/src/styles/theme-layer.css)**（2026-10-16），前端与**后端导出读同一份**
- **主题模块**：`lib/theme-axes.ts`（**轴注册表**，加一条选择型轴 = 改 2 处）+ `lib/accent.ts` / `lib/texture.ts` / `lib/theme-base.ts` / `lib/theme-presets.ts` / `lib/theme.ts` — 各轴独立管理，Store 统一分发。⚠️ **这几个模块只存 key 与 label，一个色值都不存**（色块走 `data-*` + `var()`，值与实机同源），`ACCENT_SWATCH` / `TEXTURE_SWATCH` / `BASE_OPTIONS` 的色值字段已删、别再建
- **骨架轴（2026-10-10；2026-10-11 判据改由标记决定）**：`lib/card-prefs.ts`（localStorage `mbw_card`）→ `data-skeleton="card"`（**被 `card` 节修饰符标过的章节成卡**，其余保持普通正文）+ `data-card-style`（8 套视觉方案，[styles/cards.css](frontend/src/styles/cards.css)）。⚠️ **不再是「每个 H2 一张卡」** —— 用户 2026-10-11 判「章节卡片太简单粗暴，卡片应该用在需要强调的地方」，故改由模型在 `## 标题` 的**下一行**写 `<!-- limo-block:v1 type="card" -->` 决定哪几节成卡（判据 `Section.card`，前后端同源）。于是卡片在页面里是**混排**的、**没有外层容器**（`.md-cards` flex 已删——套容器会把**普通章节的每一行**变成独立 flex item），间距由 `.md-card { margin-block }` 承担。⚠️ **不是第七轴** —— 六轴描述「界面长什么样」，它描述「正文怎么组织」；放 themeSlice 只因与 texture/font 同属「影响预览观感的外观设置」，**刻意不进 `THEME_PRESETS`**（换主题包不该连带重置骨架）。⚠️ **`paper`（默认）时两个属性都 `removeAttribute`** —— 零回归靠「**选择器不匹配**」（`cards.css` 每条规则带 `[data-skeleton="card"]` 前缀）而非「取值相同」。⚠️ 切分器单一真相源 `lib/markdown-scan.ts::scanHeadingLines`（`extractHeadings` 已改为消费它，**围栏内 `## ` 不算标题**）；`lib/markdown-sections.ts::splitByH2` 切分；`CardSections.tsx` **每张卡各跑一个现有 `MarkdownPreview`**（绝不新起第二个渲染器）；接入点**只有** [PreviewArea.tsx](frontend/src/components/preview/PreviewArea.tsx) 的 notes/polished 只读态（**刻意不在 `MarkdownPreview` 内部按骨架轴分派** —— 会静默扩散到 Excel 数据摘要 / 原始转录两处）。⚠️ 设置页给 8 套方案各画了一个**迷你视觉小样**（`.card-preview`，消费与 `.md-card` **完全相同**的 `--card-*` 变量 ⇒ 无第二份真相源；圆角/内框按 0.5 缩放是缩放**同一个变量**）—— 依据是 `[data-card-style="x"]` 定义变量的那批规则**不带**骨架前缀，故小样元素自己写个属性就能取到。⚠️ **逐维度微调（2026-10-11）**：`bg` / `lift` / `border` 三项，`null` = **跟随方案（默认）—— DOM 上一个 inline 变量都不写**，故新增旋钮不改变既有用户观感；写的是 `<html>` 上的 **inline 变量**（`--card-bg-base` / `--card-lift` / `--card-border`），特异性天然压过方案块。三处硬约束：① **切方案时清空微调**（微调是「方案之上的一层覆盖」，换地基就该重新决定）；② **透明基色下抬升强制归零**（`color-mix` 的 `transparent` = `rgb(0 0 0/0)`，抬升混出的是半透明**黑**，是无效组合）且 `applyCard` **第一步无条件清空 inline**（`isLiftDisabled` 要读运行时 `--card-bg-base`，不清空会读到自证循环）；③ **受控变量清单只有 `CARD_VARS` 一份**（`applyCard` 清空、`serializeCardVars` 拼导出串都从它派生）。⚠️ 微调**只作用于章节卡片** —— `blocks.css` 的十二种语义块不吃 `--card-*`
- **语义标记 `limo-block`（2026-10-10）**：产物正文里的**协议元素** —— 模型在笔记里写 `<!-- limo-block:v1 type="key-takeaway" -->` … `<!-- /limo-block -->`（块内是普通 Markdown），渲染成**轻量强调块**（左边框 + 淡底，**刻意不是卡片** ⇒ 与骨架轴正交、无「卡中卡」）。⚠️ **十二种块，分两族**（2026-10-11 由 1 种 → 4 种 → 6 种 → 9 种 → 2026-10-20 补 `cycle`、`sequence`、`quadrant`）+ ⚠️ **`card` 是节修饰符不是块**（见本节末）：
  - **九个语义强调块**（回答「这段文字是什么性质」，块内原本就是散文）：`key-takeaway` 重点 / `alert` 提醒·异常 / `recommend` 推荐 / `metric` 指标 / `steps` 横向步骤条 / `timeline` 竖向时间线 / `cycle` 环形 / `sequence` 交互时序 / `quadrant` 二维评估（后三种 2026-10-20）；
  - **三个内容卡片**（2026-10-11 补，**性质不同族**）：`table` 对照表 / `code` 示例代码 / `image` 单独展示的图 —— 它们标的是**内容本身**（表格 / 代码 / 图片本来就与正文不同质），加标记的**全部作用**是外面多一层壳、**块内一个字节都不动**。故 CSS 里这一族**不换 `display`、不换子元素**，只做中性容器装饰（**不带语义色** —— 染色等于替它加一层它没有的意思）。⚠️ **刻意不是「把表格/代码改造成卡片形状」**：图鉴明确禁止「为了统一视觉而把代码改成普通文字卡片」，保住等宽/缩进/复制/横向滚动是前提。⚠️ **卡片外壳的选择器只能落在 `.md-block` 自己或块内通用元素（`table`/`pre`/`img`）上** —— 预览侧的 `.md-table-wrap`、代码块工具条 `.group` 在导出侧**根本不存在**（导出是裸 `<table>` / `codehilite`），碰它们就当场分叉（这层预览/导出结构差异**先于本轮就存在**）。
  - **`card` 节修饰符**（2026-10-11 补，**第三种性质**）：⚠️ **它不是块** —— 其余十一种标的是**一段内容**（包裹式、有闭合标记），`card` 标的是**一个 `##` 标题及其全部后续内容**，写法是「写在标题**下面一行**」、**没有闭合标记**。它是前端骨架轴 `card` 的**唯一触发源**（没有勾选项 ⇒ 模型不写它就等于这个轴什么都不做，不报错不警告）。⚠️ **定性成节修饰符是一个决定解决两个隐患**：① 模型的本能是把标记写在标题**之前**（其余十一种都是包裹式），当块处理会让行分类器进入块内状态、把那个 `## 标题` 整个跳过 —— **整节从文档结构里消失**；② 节内第一个真块的闭合标记会被当成 `card` 的闭合。现在 `match_open` 对它返回 `None`（`is_section_card` 是**另一个**判据，有对照实验钉住两者方向恰好相反），`parse_blocks` 里紧跟的标记行剥除顺手接住它 —— **零残留降级无需新分支**。⚠️ **`strip_break` 挂在 `card` 上而不是挂在骨架轴上**：成卡的节剥尾部 `---`（卡的下边界已承担分隔），**不成卡的节保留**（它没有卡边界，`---` 是唯一的分隔手段）；漏了后者就是「开了卡片模式、普通章节之间的分隔线却消失了」。⚠️ **它是唯一没有集合守卫盯着的 type**（刻意不进 `ARTIFACT_BLOCK_TYPES`），名字在场 + 「写在标题之下」+「没有闭合标记」三条只能由 `test_block_prompt.py::test_规范教了节级修饰符_card` 手写钉住。⚠️ **2026-10-11 实机修正：它在规范里被提到最前面**（`PROMPT_VERSION` `"12"` → `"13"`）—— 原先是列表第 10 项、示例还单独放在整段末尾，实测结果是**其余九种几乎全写对、独独 0 个 `card` 标记**（v12 只跑过 1 篇，样本小但现象干净）。推论是**形态差异**造成的：九个块级标记是同一形态的反复（只有 `type` 值不同），模型扫一眼就抓住了模式；`card` 是唯一形态不同的那一个（不包裹内容、写在标题之下、没有闭合标记），吊在长列表末尾最容易被当成「又一个 type」略过。故按**层级**重排：先讲节级的 `card`（粒度最大）、示例紧跟其说明，再讲段级九种。⚠️ **判据与类型一字未改** —— 改的是位置与结构，与同日「不堆劝退话术」的取向不冲突；
  ⚠️ **`steps` / `timeline` 两者性质是「迁移」不是「新增」**——它们替代图表管道里的 `hchain` / `timeline` 两种简单线性图（判据：Markdown 的列表结构能保真表达这种关系，就不必付画图的成本），故 `get_chart_format` 的布局引导**同步删掉这两种**（**两处必须同批改**，否则同篇里一个说「画图」一个说「别画」；算子仍留在 `_LAYOUTS` 里，删的是引导不是能力）。⚠️ 两者的 CSS 判据依赖**列表类型**（`steps` 用无序 → `<ul>`、`timeline` 用有序 → `<ol>`，各自靠 `li::after` 画箭头 / `li::before` 画序号圆点），故「`steps` 写列表、`timeline` 写有序列表」这条**必须写进 prompt**——模型不按约定写就退化成普通内容（零残留降级，但白标了一个块）。⚠️ **形态差异全部由 CSS 表达，一行业务 DOM 结构都不许变**——依据是 `blocks.css` 是导出侧 `_load_block_css()` 读的**单一真相源**：结构一旦分叉，Python 的 `render_blocks_html` 就得复刻 React 的结构（模式 #54），「预览所见 = 导出所得」退化成靠人工对齐维持。收益连锁：`MarkerContent.tsx` 与 `render_blocks_html` **零改动**。代价如实记录：`metric` **刻意不做并排**（并排要网格容器 = 结构变化），改用 `h3 + p` 相邻兄弟选择器做「`### 名称` 小标签 + 放大数字」。⚠️ **metric 数字 2026-10-17 起用 `--tk-accent`**（原 `--tk-success` —— 固定绿不随主题，而同文件注释本来写的就是「accent 留给 key-takeaway / metric 的数字」）+ `font-variant-numeric: tabular-nums`；同批宽表 `overflow-x: auto` + 首列 `sticky`、大图 `max-height: 70vh`。⚠️ **`overflow-x` 必须配 `flex-shrink: 0`**（2026-10-18 实机踩到）：`.preview-content` 是 `flex flex-col overflow-y-scroll`，overflow 非 visible 的 flex item 自动最小尺寸按 **0** 算 ⇒ 表格块被压成「很小的一行」+ 内部滚动条，内容完整、零报错。**同一 bug 还有第二形态、更严重**：关掉「内容块装饰」时归零规则把 `padding`/`border` 一并去掉 ⇒ 被压缩的块连那 26px 可视高度都没了，**块高恰好 0、整块消失**（内容仍在 `scrollHeight` 里）——所以「关装饰吞卡片」不是开关的问题，缺的就是这个属性（四组对照实测见 CHANGELOG 2026-10-18）。`artifactCardShape.test.ts` 有三条守卫：table 专项、**任何 `.md-block` 本体上的非 visible overflow 都必须配它**（通用版，防将来新增块漏配套）、关装饰规则**不得**出现 `display:none`/`visibility:hidden`/`opacity:0`/`height:0`（「只收装饰绝不吞内容」）。三者均只影响预览与导出 HTML/PDF（Word 不读 CSS）；⚠️ **`overflow-x` 管不到打印/PDF**（打印媒体没有滚动机制，长宽表右侧列仍会被裁切，见 CHANGELOG 2026-10-18「已知偏差」）。⚠️ `alert` 用 `--tk-warning` **不是** `--tk-error`（严重度由**文字**承担，颜色不该替它分级）；导出侧 `_CARD_TOKENS_CSS` 必须同步补令牌（成对常量守卫）。⚠️ **中性面板族（`metric` / `table` / `code` / `image`）的容器边框用 `--tk-border`、不是 light 档**（2026-10-11 实机修正）：`--tk-border-light` 在默认 `warm` 基调下离正文底只差 9 个灰阶、面板底色 `--tk-elevated` 离正文只差 6 阶 —— 三层叠加等于**没有容器**（用户原话「显示效果不太好」）；`--tk-border` 离正文 30 阶（3.3 倍），且与章节卡片默认方案 `gpt` 是**同一套语言**（两者本是嵌套关系：卡片里包面板）。⚠️ `timeline` 的**竖连线仍用 light 档**（细线本就该淡），故 `markerPrefs.test.ts` 的新守卫限定在两条**容器**规则内 —— 写成全局断言会把连线一起误判。解析链 `lib/markdown-scan.ts`（**唯一的行分类器**，`inFence` + `inBlock`）→ `lib/markdown-markers.ts`（`parseMarkers` / `stripMarkers`）→ `preview/MarkerContent.tsx`（每段各跑一个现有 `MarkdownPreview`）。⚠️ **接入点只有两处**（PreviewArea 的 paper 分支 + CardSections 每张卡的 body），**绝不放进 `MarkdownPreview` 内部**（会扩散到 Excel 数据摘要 / 原始转录）。⚠️ **顺序是 Markdown 层的：先切卡、再在每节内解析标记**（跨节标记在降级里自然消解）。⚠️ **开关语义是「隐藏装饰」不是「隐藏标记」，且射程是全部十二种**：`lib/marker-prefs.ts`（`mbw_markers`，默认 `decor: true`，UI 名「**内容块装饰**」——2026-10-11 由「重点块装饰」改准）关掉后**内容一字不少** —— 九个强调块只是边框与淡底消失，**三个内容卡片则退回「没包过围栏」的样子**（裸 `<table>` / 带工具条的代码块 / 靠左的图；间距刻意保留成与预览侧 `.md-table-wrap` 的 `my-4` 同值，故「关掉装饰」与「从未包过」逐字相同）。**落点是纯 CSS 层**（`[data-marker-decor="off"]` 前缀规则），**不是 JS 分支不渲染包裹层**（那会让后端导出侧再实现一遍）；DOM 落点 `data-marker-decor="off"`（**默认不写属性**⇒ 零回归靠「选择器不匹配」）。⚠️ **新增 type 时必须同步补它的 `decor=off` 归零规则**——漏了不报错，只是那一种块永远带着装饰。⚠️ 后端协议收口 `core/artifact_blocks.py`（与 `transcript_meta.py` 并列：一个管自证头、一个管正文标记），**前后端各一份实现靠共享 fixture `limo-blocks.json` 钉住**。⚠️ **prompt 无条件注入且无勾选项**（价值全在「模型自主判断重点」，先勾选就退回旧问题）—— 故「谁该有谁不该有」只能靠测试钉住
  ⚠️ **`cycle`（2026-10-20）是同一判据的延伸，但替代的**不是**一张图**：图表管道从来没有环形算子（`_LAYOUTS` 十个里没有），`flowchart` 遇环直接抛错、`state` 只适合状态迁移图 —— 模型想表达「A→B→C→A」时只能硬套 `state`，而 `state` 曾经写死 `nrow > 7` 就抛错（实测失败产物里 2 条正是这个；那条闸门已于 2026-10-20 随同批的 `flowchart` 闸门一起删除，理由见上方图表段 —— 真正的裁决者是 `expand()` 的缩放链）。`cycle` 把它变成**填空**：模型只写「有哪几个环节、顺序如何」（3–6 项**有序列表**），环形布局由 `blocks.css` 算，坐标 / 半径 / 连线完全不经模型之手。⚠️ **角度档靠 `li:first-child:nth-last-child(N)` 判项数，刻意不用 `:has()`**（本文件同时被导出 HTML 消费，为一条可优雅降级（退回普通列表）的装饰去赌目标浏览器版本不值得）；⚠️ 项数不匹配或超出 3–6 时全部节点叠在环顶 —— 不好看但**内容一字不少**（「内容永远不该因为渲染不认识而消失」优先于观感）；⚠️ **`overflow-x: auto` + `flex-shrink: 0` 成对**（与 `table` 同款，窄面板宁可横向滚动也不让节点叠成一团）；⚠️ 关装饰退回**有序列表**（与 `timeline` 同出口）；⚠️ `get_chart_format` 同步把「首尾相接的因果循环」从图表职责里分出去，`state` 的示例标题改准为「状态机（带判定的迁移，可含回环）」，避免与它抢同一批内容（**两处必须同批改**，同 `steps`/`timeline` 的分家纪律）
  ⚠️ **`sequence` / `quadrant`（2026-10-20）同样是「迁移」不是「新增」**——它们接管 `get_chart_format` 同日撤下的两种布局（图表引导由七种收缩到三种，判据见上方图表段）。两者的**形态前提必须写进 prompt**：`sequence` 用**有序列表**、每项以 `**发送方 → 接收方**：` 起头（⚠️ 纯 CSS 无法从消息文本推导参与者列位，故它只能是「消息流卡片」而不是生命线时序图 —— **方向由文字承担**，这正是它不必结构分派的原因）；`quadrant` **恰好 4 项**、顺序固定「左上 / 右上 / 左下 / 右下」（与 `chart_layout` 的 `quadrant` 算子同序），CSS 用 `grid-template-columns: repeat(2, minmax(0, 1fr))` 排 2×2 且**不画坐标轴**（象限名自己承担语义）。项数不对时退化成两列多行 / 普通列表，**内容一字不少**；两者都有 `decor=off` 归零规则。⚠️ **判据是「文本结构能否保真表达这个关系」**，不是复杂度、也不是垂直/水平 —— `layers`/`tree` 因此干脆连块都不必标（嵌套列表本身就是那个结构）
- **语义标记 · 对话侧（2026-10-11）**：同一套协议、**另一套类型**（两侧集合**不相交**，由三集合 `ARTIFACT_BLOCK_TYPES` / `CHAT_BLOCK_TYPES` / `KNOWN_BLOCK_TYPES = 并集` 拆开——「协议知道」≠「某侧实现了」，混为一谈的失效是静默的）——`card-group`（并列比较 2–4 个同级对象 → 块内 `###` 各成一张**并排卡片**）/ `follow-up`（2–4 个下一步问题 → 每项一颗 chip，点了**直接发送**）/ `info`（信息卡，「XX 是什么」）/ `digest`（摘要卡，长回答开头的整体印象）——后两种 2026-10-11 补；2026-10-12 再补 `caution`（注意事项，色用 `--tk-warning` **不是** `--tk-error`，同产物侧 `alert` 的判据）/ `how-to`（操作步骤，块内是**有序**列表，CSS 靠 `<ol>` 认）/ `quote`（引用出处）→ **共七种**，规范含**总量闸门**（这些块合计不超两块）。⚠️ **新类型名刻意避开产物侧十二种**（`caution` 而非语义相近的 `alert`、`how-to` 而非 `steps`）—— 名字撞上等于「同一套渲染器服务两种语义」，而两者的降级判据 / CSS 归宿 / 导出待遇都不同。⚠️ 对话侧**不受**「只许 CSS」那条约束（`chat-blocks.css` 明确不进导出），故可用 React 结构与 lucide 图标。⚠️ **只共享判据、不共享渲染器**：`ai-chat/ChatMarkerContent.tsx` 是**新组件**（内联 `ChatText` = 原来的裸 `ReactMarkdown`），**刻意不复用** `preview/MarkerContent`（它内部硬编码 `MarkdownPreview` → 复用等于把整个对话正文换成产物渲染路径，波及**全部历史消息**）；共享的是 `parseMarkers` 与新增的 `splitByHeading(md, level)`（`splitByH2` 改为委托，行为逐字不变）。⚠️ **流式半截标记行必须抑制**（`markdown-scan.ts::suppressTrailingPartialMarker`，**只在对话正文且 `streaming` 时调用**）：`<!-- limo-block:v1 type="card-` 这种半截行既不匹配开标记也不匹配 `MARKER_LINE_RE` → 会被当普通正文渲染出来；⚠️ **判据必须命名空间感知**（朴素的「以 `<!--` 开头且无 `-->`」会吞掉**正在输入的合法 HTML 注释**，测试有对照实验）。⚠️ **消费点**：存储/LLM 历史**保留原文**、`.md` 导出与复制**剥离**、`.json` 导出**刻意保留**（可回灌）、未闭合/未知 type **零残留降级**。⚠️ **视觉跟随卡片方案（2026-10-12）**：容器的 `border` / `border-radius` / `background` / `box-shadow` 消费 `--card-*`（与产物侧章节卡片**同一套变量、同一套 8 方案**），**尺寸级（padding / gap / margin）保持 em** —— `--card-padding` 是 `22px 26px` 的**宽栏**值，浮窗里会撑爆，而对话间距必须跟 `lib/chat-font.ts` 的字号倍率一起缩。**零 JS 改动、零中间变量层**：`--card-*` 的初值写在 `cards.css` 的**裸 `:root`**、`paper`（默认骨架）下 `applyCard()` 只 `removeAttribute` 两个 data 属性**不删变量** ⇒ 对话侧直接 `var()` 即可，默认拿初值、用户切方案自动跟随，**不需要知道当前是哪套**。⚠️ **`--card-border` 在 `apple` / `ink` 两套下是 `none`** —— 语义差异此时由**图标色**承担。⚠️ **两个交互（2026-10-12）**：① **卡片整体复制** —— `ChatCopyButton` 泛化，落点克制（`card-group` 每张卡 + `caution` / `how-to` / `quote` 外壳；`info` / `digest` **刻意不加**，内容短、复制价值低于它占的注意力，有**反向断言**守着防「顺手补齐」）；② **追问换一批** —— `POST /api/chat/followups`（**无状态**：`{question, answer, model_id}`，不碰 `SessionManager`）+ `ChatFollowUpList.onRefresh`（**缺省不渲染按钮** ⇒ 产物侧复用零回归）+ `chatStore.refreshFollowUps`（成功替换 + 落库；**失败保留原有那批** + 记 `followUpError`）。⚠️ **端点无状态的根因**：前后端消息 id **不同源**（前端 `nextMsgId()` vs 服务端 `msg_…`，会话进行中必然不同），改用**下标**则多一层「前端数组与服务端数组同步」的隐含前提 —— 而 `editMessage` / `deleteMessage` / `regenerate` 三个写动作都在改它。代价是落库由前端承担（与那三个写动作同款）。⚠️ **`.markdown-chat` 基础元素（表格 / 代码 / 引用 / 列表）2026-10-12 从 `index.css` 搬进 `chat-blocks.css`** —— 它与语义块作用在**同一批节点**上（块内就是普通 Markdown），分住两处时「给块内的表格加样式」得先想清楚谁压谁。⚠️ **代码块与表格加壳走纯 CSS、刻意不走 `limo-block`**：加壳没有任何需要模型判断的东西，标记驱动只会多出一个「模型漏标 = 静默失效」的失败模式，且纯 CSS 让**全部历史消息立刻受益**（与产物侧**标记驱动**的 `table` / `code` 差异是有意的 —— 那边要保证「预览 = 导出所得」，这边**不导出**）。⚠️ **对话样式独立**（`styles/chat-blocks.css`，`.chat-*` 不共用 `.md-block`、尺寸用 em、零硬编码色值、**不进导出**）。⚠️ prompt 是 `agent/prompts/formats.md`（system prompt 第四层，无条件注入）；**两侧规范刻意不共用一份文本**（有「不串味」断言）。⚠️ **对话侧形态判据（2026-10-17/18）**：`.chat-block` 用**四边框** `1px solid var(--block-edge)` 且**自持、不跟随 `--card-border`**（`apple`/`ink` 两套把它设为 `none`，跟随会让语义块只剩图标悬空；圆角/底色/投影仍跟随）；`.chat-cards` 列数**按张数**（1/2/3/2×2/5+ 单列，`@container (max-width:320px)` 兜底），**不再按宽度**；篇幅变体（4+ 内容块 → 长内容卡、7+ 列表项 → 整组退列等）全是纯 `:has()` 结构判据。⚠️ **长内容卡 / 退列的 `:nth-child(5)` 数的是 DOM 子节点，第一个恒为复制按钮** —— 按钮**流式期间隐藏但不卸载**（`.chat-card-copy--streaming` + `display:none`），卸载会让同一判据在两态数出不同的数、卡片在流式结束那一帧跳变且零报错；`chatCardShape.test.ts` 有**读组件源码**的守卫钉住「ChatCard 函数体内不得出现 `!streaming`」
- **⚠️ github-markdown-light.css 覆盖**：`main.tsx` 导入的第三方 CSS 硬编码 `.markdown-body { background-color: #ffffff }` 和 `#f6f8fa` 等冷灰色。`index.css` 中用 `!important` + `--tk-*` 变量覆盖。新增 markdown 背景样式时必须同步检查此文件。提交前 `grep` 确认零硬编码色值泄露

### 导出系统

**两条路径设计**（详见 [export-pipeline.md](docs/export-pipeline.md)）：
- **ExportMenu**（PreviewArea 工具栏下拉）：快捷单文件导出，内容自动跟随当前预览 tab。**复制**恒走客户端 store 直读；**MD 二选一**（设置项 `lib/export-prefs.ts::embedImages`，**默认开** = 走 `?format=md&image_md=inline` 让后端把图片 base64 内嵌、**依赖后端**；关 = 纯前端落盘、零依赖但图片是只在本应用内有效的根相对路径）；HTML/Word/ZIP 恒走后端
- **ExportPanel**（顶部 Download → 侧边面板）：批量多文档导出，用户手动选 tab/格式/主题。全部走后端

**四种格式**：MD / HTML / **Word(.docx)** / 纯文本。

**证据块线性化**（2026-10-18，合成产物专用）：`type="evidence"` 是**唯一**不走「转换/剥离」那套的类型 —— 四种格式（**含 Markdown**）统一「原位 `[证据 N]` + 文末 `## 证据附录`」。实现 [core/artifact_blocks.py](backend/app/core/artifact_blocks.py)::`linearize_evidence` ↔ [lib/markdown-markers.ts](frontend/src/lib/markdown-markers.ts)::`linearizeEvidence`（共享 fixture `evidence-linearize.json`）。⚠️ **四个后端接入点**：`GET /export/{id}`（空内容检查后立即线性化，html/docx/md 三支共用）、`_build_export_zip`（改 `content` 本身而非 `processed` —— docx 分支用原始 content）、`_collect_doc_products`（`/all` 与 `/batch`，**只处理笔记产物那一行**，转录原文/图片处理结果不动）、PDF（走 HTML 自动继承）。前端两条客户端路径（MD 下载 / **复制**）同样是 `stripMarkers(linearizeEvidence(...))`。⚠️ **先 linearize、后 strip**；⚠️ 只有 evidence 块时线性化后 `blocks_present` 变假 → 不注入 `blocks.css`（**正确行为**，别为保持为真偷偷留围栏）；⚠️ **预览与导出在此维度刻意不同**（预览 `<details>` 可折叠无编号 / 导出线性化带编号），这是用户拍板的唯一例外，矩阵见 [export-pipeline.md](docs/export-pipeline.md) §1

**导出跟随应用主题**（2026-10-16）：
- **六条轴参数**（`theme_mode` / `accent` / `base` / `font` / `texture` / `texture_opacity`）落到导出页 `<html>` 上（前五条走 `data-*`，强度走**行内 `style="--texture-opacity: …"`** —— 它是 0–1 小数，`data-*` 表达不了）。⚠️ 明暗发的是 **`resolvedTheme`**（解析后的 `light|dark`），`data-theme="system"` 是**死属性**（没有选择器认它）
- **逐轴独立、非空才写** ⇒ **不带任何参数 = 一个属性都不多、主题层不注入 = 与改造前逐字节相同**（第三方脚本与历史调用方零回归）。前端两个入口各拼一次（`ExportMenu::backendParams()` 与 `ExportPanel` 的 POST body），**批量导出经 `_build_export_zip` 也产 HTML，必须一并带**，否则「单个跟随、批量不跟随」
- **主题层注入闸门**：**有任一轴参数才**注入 `<style>{theme-layer.css}</style>`，且位置在 `HTML_THEMES` **之前**（变量先定义、后消费 —— 放后面 `var()` 全部落空，导出**比改造前更糟**：原来至少是固定的蓝，现在是无色）
- **`_EXPORT_NEUTRAL_TOKENS_CSS`（原名 `_CARD_TOKENS_CSS`）只在「未跟随」时注入**：跟随时令牌由主题层接管。⚠️ 两份**不能同时在场**（否则「卡片底色该用哪个」由 CSS 源顺序裁决）；⚠️ 也**不能删** —— `cards.css` / `blocks.css` 的注入条件与 `html_theme` 无关，删了会让 `paper`/`github`/`minimal` 的卡片底色从 `#ffffff` 变暖白。两者取值 6/6 不同，**这正是「跟随」的语义差别**
- **白名单只做字符、不持有第二张表**：五个选择型轴 `^[a-z0-9-]{1,24}$`，强度 `^(?:0|1|0?\.\d{1,4})$`；非法值**静默丢弃该项**、其余轴照常（判据同 `card_style`）。⚠️ `data-accent="{v}"` 是**原样拼接的注入面**，字符集里绝不能有 `"` `<` `>` 空格 `=`
- ⚠️ **暗色块一律包在 `@media screen` 里**（limo 主题与主题层两处 + `--md-*` 那块共三处）：`@media (prefers-color-scheme: dark)` 与 `@media print` **不互斥、会同时匹配**，裸着的话浅色标题会落到白纸上。**「暗色是屏幕概念，纸永远是浅的」**——一处改动让三个宿主（应用内 / 导出屏幕 / 导出打印）同时正确，且打印拿到的是**该配色自己的浅色档**（不是一份写死的蓝）。⚠️ 于是**没有「暗色 → 浅色」还原表了**，那是同一份主题数据的第二份副本
- ⚠️ **刻意不跟随的三处（判据不是遗漏）**：时间戳徽标 `#007aff`/`#ff9500`（**类别标识色**，跨主题保持可辨识，同 `TYPE_COLORS`）、Pygments 屏幕段与打印段（代码高亮是独立的产品决策，正交于 `--tk-*`）、**未跟随 + 卡片**这一档的明暗仍由读者系统决定（固定主题本就该固定）。三处**各有一句注释说明这是刻意分界**
- `ExportPanel` 的 `limo` 项文案已改「**跟随应用主题**」（key 不变、零迁移），3 套固定主题（`paper`/`github`/`minimal`）仍可手动选 —— 它们**不消费 `var(--tk-*)`**，选了就拿到那套固定样式（`HTML_THEMES` 共 4 项，含 `limo`）

**快捷 MD 的图片**（2026-10-11）：MD 从前只有纯前端一条路（`downloadText(stripMarkers(...))`），
正文里的 `/api/assets/{id}/x.png` 一离开应用就裂图 → 加设置项 `embedImages`（**默认开**），
开后走 `GET /export/{id}?format=md&image_md=inline`，后端 base64 内嵌。⚠️ 后端 `export_content`
的 `image_md` **默认 `link`**（= 改造前行为，零回归），且 md 分支复用 HTML/docx **同一套**
`_find_image_refs` / `_collect_images` / `_inline_images`；⚠️ **先内联、后绝对 URL 改写**（缺图靠后半兜底）；
⚠️ 走后端时前端传 `preferSuggestedName: true` —— 后端命名 `{标题}-{mode}.md` 与前端
`makeFileName` **不同源**，不给它就是「同一个按钮、改个开关文件名也变」。

**文档库 tab → 导出面板**：「原图」「OCR」的 `contentSource` 是 `'none'`（恒无内容源），
`getTypeMeta` 的 `tabs` 把它们滤掉（该字段**唯一**消费方就是导出面板）；后端
`_resolve_tab_content` 对不适用的 tab 返回**空串、不回退 rawText**（`content_tabs` 是前端
**各类型并集**拍平后发来的，回退会让每篇都多出几个「名字对不上、内容是同一段原文」的假文件）。

**Word 导出**（2026-09-21）：
- 链路 `Markdown → HTML → docx`（`app/services/docx_export.py`）。**两跳是刻意的**——markdown 解析器（含 LiMo 方言：表格前空行/删除线/任务列表/时间戳 badge）只应存在一份，故已下沉为 `app/services/markdown_render.py`，HTML 与 Word 共用。抽取时用 git 旧版对比过 8 个样本的 HTML 输出，**逐字节等价**
- `markdown_to_html(md, codehilite=False)` 是 Word 侧专用分支（docx 里逐 token 的 `<span>` 是噪音）
- 图片：`_collect_images` 拿到 bytes → `add_picture(BytesIO)` 内嵌，超版心（6.27in）等比缩放；`data:image/png;base64,` 也直接解码
- 公式：**保留 LaTeX 原文**。⚠️ `formula_md=render`（渲染图片）依赖 matplotlib，而 matplotlib **不在依赖里**——该选项自上线起从未生效（详见 CHANGELOG 2026-09-21），md/html 侧同病，待修
- 前端 ExportMenu 的 Word 按钮与 HTML **同门控**，但**思维导图 tab 除外**（交互脑图无 Word 形态）

**PDF 导出**（2026-09-21，两条路径共用一份自包含 HTML）：
- **无独立 PDF 端点**：都拉 `/export/{id}?format=html&mode=…`。**刻意不打应用 DOM**——那要维护第二套打印样式（前端 `lib/print.ts`）
  - **Tauri**：`invoke('export_pdf')` → Rust `pdf_export` 新建**屏幕外窗口**加载该 HTML → `ICoreWebView2_7::PrintToPdf` **直接落文件**（存系统「下载」目录，同名加序号），无对话框
  - **浏览器**：屏幕外 iframe → `contentWindow.print()`，用户在对话框选「另存为 PDF」
- ⚠️ **PrintToPdf 只能打「该 WebView2 实例的主文档」**——主窗口的文档是应用 UI，iframe 也只有弹对话框的 `PrintAsync`。故必须新开窗口
- ⚠️ **窗口挪到屏幕外 `(-32000,-32000)` 而非 `visible(false)`**：WebView2 对不可见窗口推迟渲染，PrintToPdf 会拿到空白页。`on_page_load` 回调在 UI 线程，**不能就地 `with_webview`**（重入事件循环）→ 换线程 + 3000ms 后再抓 controller（实测值非推导值：`Finished` 只代表主文档加载完，自包含 HTML 还有 ~1.8MB base64 字体解码与 KaTeX 客户端渲染在其后；**没有更短也够的成功样本，别调小**）
- ⚠️ **加载的 URL 必须带 `inline=1`**（`print.ts` 的 `withInline()` 统一追加）：`/export/{id}?format=html` 默认 `Content-Disposition: attachment`（点「HTML」就该下载），而 WebView2 是**直接导航**到这个 URL —— attachment 让它去「下载」而非渲染 → **窗口全白，打印出一张 876 字节白纸，回调却报 `succeeded=true`（伪成功、零报错）**。故 `export_content` 加 `inline` 参数（默认 False）切回展示语义。浏览器路径走 `fetch`+`srcdoc` 不受影响；`file://` 也不查该头（这正是把根因收窄到「HTTP 响应头」的对照实验依据）
- ⚠️ **`PrintSettings` 没有一项默认值可用**：`PageWidth/Height` 默认 **Letter**（不设就印美式尺寸）；`ShouldPrintBackgrounds` 默认 false（关掉后代码块只剩一圈边框）。四边 margin 与 `_PRINT_CSS` 的 `@page` 取**同值**，谁胜出结果都一样
- ⚠️ **COM 接口不跨 crate 实例**：`webview2-com` 版本必须与 tauri 内部的同源（`tauri 2.11` → `wry 0.55` → `webview2-com 0.38`），否则 `.cast()` 报「两个不相干的类型」。零新增包（本就是传递依赖）
- ⚠️ **打印 CSS 分三处，不可合并**：① `_PRINT_CSS`（`export.py`，`@page` + 解除屏幕布局 + 分页控制 + 代码容器底色）；② `_get_pygments_css()` 的 `@media print` 段（**逐 token 配色**换 Pygments 浅底主题）；③ `HTML_THEMES["limo"]` 的 `@media print` 段（还原自己的暗色段）。**顺序即优先级**——`_PRINT_CSS` 必须垫底（媒体查询不加特异性，同特异性后者胜）
- ⚠️ **token 配色压不住**：token 是 `.codehilite .k` 类选择器作用在 `<span>` 上，写 `.codehilite code { color }` 无效 → 必须**整份换浅底 defs**
- ⚠️ **`pre`/`table` 禁用 `break-inside: avoid`**：长度无上界，装不进当页即整体推下页 → **一整页空白**（实测 3 页文档凭空多一页）。只保留 `li`/`tr`/`blockquote`/`figure`/`img`
- ⚠️ `@media (prefers-color-scheme: dark)` 与 `@media print` **不互斥**（同时匹配），暗色浅色值须由主题自己在 print 段还原
- 门控 `canExportPdf = canExportDocx`（思维导图 tab 不开）；Tauri 下成功 toast 报完整落盘路径。**Tauri 链已端到端实测**（真实 Tauri 应用导出 + 读回断言 6 页全 209.9×297.0mm/A4 + 渲染成 PNG 逐页核对了代码块底色/Pygments 三色高亮/表格/跨页表头/图表 PNG，全部正确）；**浏览器链只测到打印 CSS 层**（`msedge --headless --print-to-pdf` 四主题 × 暗色偏好模拟），`printFromUrl` 的 iframe 交互路径未经真人点击验证

**核心约束**：
- 用户在预览栏看到什么，导出就应该得到什么。禁止导出与预览不一致的内容
- **HTML 注释一律不进渲染**（2026-10-11）：正文里任意 `<!-- … -->` 在**预览与 Word 里都不显示** —— 前端 `lib/markdown-plugins.ts::rehypeStripComments`（挂进 `REHYPE_PLUGINS` / `REHYPE_PLUGINS_PLAIN`，一处覆盖全部 7 个渲染点）+ 后端 `docx_export.py` 剥 bs4 的 `Comment` 节点（**它是 `NavigableString` 的子类，不剥会被当正文段落写进 Word**）。⚠️ 但 `.md` / `.html` 导出**刻意保留**自证头（`_pick_artifact` 用 `raw_content`）—— 分工是「注释是**文本源**里的元信息，docx 是纯渲染格式没有源码可留」
- ⚠️ **顺带**（2026-10-11）：自证头的信息在**预览侧**改由**产物自证条**（正文上方一横排带图标小标签）呈现 —— 那是**视图装饰、不参与导出**（同版本切换器 / 编辑按钮），与上一条「导出保留自证头注释」不矛盾：一边是**给人看**的形式，一边是**给 agent 读**的形式。「预览所见 = 导出所得」约束管的是**内容**。⚠️ 插件必须**跳过 `code` / `pre` 子树**（代码块里的注释是 `text` 节点、与正文注释长得一样，但那是用户要展示的代码）
- **正文里的协议元素（`limo-block`）在 HTML/Word/PDF 上是「转换」、在 md/纯文本上是「剥离」**——前者没有视觉层可谈（用户拍板「导出的文件要干净」），后者要跟随预览（`marker_decor` 开关）。⚠️ **无标记产物必须逐字节零回归**：`data-marker-decor` 不写属性、`blocks.css` 不注入、`render_blocks_html` 无标记时逐字返回渲染结果（三条都写在 `test_export_blocks.py` 里）
- 前端预览 tab → 内容映射走 `lib/tabRegistry.ts`（`TabDef` 纯数据注册：id/标签/`contentSource`/processingKinds）+ `lib/contentSources.ts`（`CONTENT_GETTERS`：`Record<ContentSource>` getter）。新增 tab = 注册一个 TabDef + 一个 getter，无需改分发逻辑（旧 `TAB_EXPORT` 表已移除）
- 后端 `GET /export/{id}` 内容源回退链：markdown（notes/polished）→ process_results（图片 extract/table 等）→ transcript（rawText）→ 404
- HTML 导出已对齐预览：Pygments 代码高亮 + 语言标签、GFM 删除线/任务列表、时间戳 badge（Content 蓝色/Screenshot 橙色）、标题锚点 id
- MD 导出自动将图片路径重写为绝对 URL（基于 `request.base_url`），外部查看不再裂图
- 图片文档没有 markdown 条目，HTML 导出依赖 `get_process_results()` 回退
- mindmap HTML 导出走独立端点 `/export/{id}/mindmap`（markmap 库需要特殊 HTML 模板）
- `apiUrl()` / `downloadFromUrl()` 内部已拼接 BASE（含 `/api`），调用方传路径时**禁止加 `/api` 前缀**，否则产生 `/api/api/...` 导致 404

**卡片骨架跟随**（2026-10-10，详见 [export-pipeline.md](docs/export-pipeline.md)「卡片骨架跟随」段）：
预览区骨架轴（`data-skeleton="card"`：**被 `card` 节修饰符标过的章节**渲染成一张卡）**导出跟随**；`GET /export/{id}` 加 `skeleton`/`card_style`/`card_vars`（**默认 `paper` + 空串 ⇒ 逐字节零回归**），前端拼装点是 `ExportMenu.tsx::backendParams()`（HTML / Word / PDF 共用）与 `ExportPanel` 的 POST body（两条传输各拼一次，变量串本身同出 `serializeCardVars`），PDF 自动跟随零后端改动。陷阱：
- ⚠️ **导出 HTML 的 `<html>` 必须同时带 `data-skeleton="card"`** —— 它是 `cards.css` 的**主作用域**，只带 `data-card-style` 则规则永不命中 ⇒ 导出**没有样式的卡片**，而结构断言全绿
- ⚠️ **`HTML_THEMES` 是自包含裸色值 CSS、零 `--tk-*`** ⇒ 卡片 CSS 的 8 个 `var(--tk-*)` 全部落空（预览里却是好的）。修法是 `export.py` 的 `_CARD_TOKENS_CSS` 补**三态**令牌（浅色 / 暗色 / `@media print` 还原），并有一条**成对常量守卫**（正则抽 `var(--tk-…)` 后逐一查定义）
- ⚠️ **`cards.css` 单一真相源在前端**，后端 `_load_card_css()` 从磁盘读并内联（开发 `frontend/src/styles/`、打包 `_MEIPASS/styles/`），**不往 `backend/static/` 放副本**、**不缓存**（会改的源文件）、**读不到显式抛 500**（不静默输出无样式卡片）
- ⚠️ **Word 侧** `_render_block` 的 elif 链必须加在**中间**（链尾 `else` 是「未知块 → 递归子元素」）；也**正因那个 `else` 兜底，漏加分支不丢内容** ⇒ 断言必须落在**结构层**（单单元格表格 / `w:tcPr/w:shd`）。docx 无圆角无投影，**刻意不接收 `card_style`**
- ⚠️ **前后端各一份 H2 切分实现**（JS `splitByH2` / Python `split_by_h2`），靠**共享 fixture** `frontend/src/test/fixtures/h2-sections.json` 钉住；**切分点必须在 `_protect_latex` 之前**（两侧输入同源）
- ⚠️ **逐维度微调（2026-10-11）走 `card_vars`**：前端发**解析好的 CSS 值**（`--card-lift:40%`），后端 `_parse_card_vars` 只做**字符白名单**（名字限 `_CARD_TUNE_VARS` 三个、值不许含 `;{}<>"'\/*`），**刻意不持有第二张「档位 → 取值」表**。该段 `<style>` 必须排在 `cards.css` **之后**（方案块与 `:root` 特异性同为 0,1,0，顺序即优先级，放前面会被静默压住）；没调过时返回空串 ⇒ 一个字节都不多

**已修复的关键问题**（2026-07-10）：
- `/api/api/` 路径重复 → 6 处修复
- data_preview tab HTML 导出 fallthrough 到 notes → 修复为 transcript
- skeleton tab 导出笔记而非骨架 → 修复为 rawText（skeleton tab 已于 2026-08-15 随骨架面板移除）
- mindmap tab HTML 导出笔记 → 修复为走 mindmap 端点
- 图片 tab HTML 导出 404 → 后端增加 process result 回退
- ExportPanel 预览显示无关文档 → 限制为仅当选中=当前文档时显示
- ExportPanel "导出全部产物"按钮仅单文档 → 改为支持多文档（POST /export/batch）

- 图片收集/重写/内联三个核心函数 → 4 端点改造
- HTML 自包含（KaTeX 预渲染·内联 CSS·零 CDN·4 主题），默认 `limo` 匹配应用内预览的 `--md-*` CSS 变量
- ZIP 三种图片模式（独立文件夹/base64 内嵌/不包含）+ 多产物标签（笔记/整理版原文/提取文字等）
- 思维导图：SVG 下载 + markmap 交互式 HTML（~980KB·暗色自适应）

### 安全
- **敏感信息脱敏**：LLM Key / Cookie / Tavily Key 在 `GET /api/config` 中统一脱敏返回（`mask_sensitive`），保存时 `merge_sensitive` 防 `***` 覆盖真实值
- **SSRF 防护**：`url_security.validate_url()` — 内网/保留 IP 段黑名单（**v4 + v6 双栈**，`::ffff:a.b.c.d` 按内嵌 v4 判）+ DNS 解析二次校验 + 协议白名单。⚠️ WebpageAdapter 曾遗漏（已修复），新增管道必须加。⚠️ **重定向必须逐跳校验**：`safe_get` / `safe_get_httpx`（`allow_redirects=False` + 每跳 `validate_url`），`requests` 默认自动跟随会让公网 URL 一跳 302 到 `127.0.0.1`
- **导出 HTML 的 CSP + nonce**（2026-10-20）：`export.py::_csp_meta` 注入 `script-src 'nonce-<每次随机>'`，我们自己的 `<script>` 全带 nonce，**正文里的 `<script>` / `on*=` 一律不执行**。⚠️ 为什么必须：导出 HTML 是「模型/用户内容原样渲染」，而 Tauri 的 PDF 路径在**同源**上下文里导航到它 —— 一段脚本就能读全库、调写接口。⚠️ **HTML 内容一个字都不改**（保「预览所见 = 导出所得」），`style-src 'unsafe-inline'` 必需
- **有副作用的 GET 的来源守卫**（2026-10-20）：`core/origin.py`，`X-LiMo-Client` 头（主判据 —— 跨站 `<img>`/`<script>` 是 no-cors、带不了自定义头）+ Origin/Referer 白名单兜底。CORS 只拦跨站**读响应**，不拦请求发出。⚠️ 白名单正则与 `main.py` 的 CORS **同一份**
- **日志值必须截断（含容器）**：`core/logging.py::_val` 字符串 80 / 容器 300。⚠️ 容器曾经不截断 ⇒ `log.info("Tool", args={...})` 把整段工具入参打进环形缓冲，`GET /api/logs` 交给前端 LogPanel，用户贴日志时顺带外泄会话内容
- **路径遍历防护**：`_safe_filename()` 取文件名末段 + `resolve().relative_to()` 双重校验，图片/文件服务均防护
- **Markdown XSS 防护**：前端 `<a>` 组件拒绝 `javascript:`/`data:` 协议，ReactMarkdown 默认不渲染原始 HTML
- **SSL 证书 (PyInstaller)**：启动时从 `certifi.where()` 设 `SSL_CERT_FILE` + `REQUESTS_CA_BUNDLE` 环境变量。缺此则 dashscope SDK / openai / yt-dlp 的 HTTPS 请求全部失败
- **代理注入**：启动时从 `config.fetch.proxy` 读到代理地址后设 `HTTP_PROXY`/`HTTPS_PROXY` 环境变量，确保所有 HTTP 库（dashscope/requests/httpx/yt-dlp）统一走代理

### 其他
- **数学公式**：全局 `remark-math` + `rehype-katex`，LaTeX `$$` 块自动排版
- **日志系统**：`contextvars.ContextVar` 注入 `request_id` → ASGI middleware，全链路追踪。`MemoryHandler`（`main.py` 中配置）缓存最近 1000 条日志供前端 LogPanel 轮询（`GET /api/logs`），重启后清空
- **客户端断连监测**：`app/core/disconnect.py` — `@asynccontextmanager` 封装，5 个 SSE 端点复用，新增端点直接 `async with disconnect_watcher(request, cancel_event):`
- **Tesseract 移除**：本地 OCR 已从内置移除（2026-08-02），`backend/bin/tesseract/` 不再随包分发。**行为变更**：无 dashscope key 时扫描件 OCR 从「本地可用」变「占位文本（原图 + _所有 OCR 引擎均失败_）」，直到用户装三方插件模板
- **EPUB 一键解读**：epub-container（自研解析器，2026-08-26 起替代 ebooklib AGPL）章节提取；生成走独立管道（`app/services/epub_pipeline.py` + `app/routers/epub.py`）——阶段 1 逐章解读落盘 → 阶段 2 归并全书总结（notes=总结 / polished=逐章），全部章处理过即触发归并（失败章跳过并在 polished 按序插失败标注），中断续跑 + 软取消（`cancel_event`），产物必定落盘。三端点 `/api/epub/generate|task|cancel/{doc_id}`，前端 GenPanel epub 专属入口三态 UI（进度条 / 继续上次解读 / 一键解读）
- **模糊帧过滤**：`frame_quality.py` — Laplacian 方差 + 熵 + 边缘密度评分，视频抽帧时自动丢弃低质量帧
- **KaTeX 离线缓存**：`_get_katex_assets()` 从 node_modules/static 加载 CSS/JS/字体，CSS 中 `url(fonts/...)` 替换为 base64 data URI（60 个字体），导出 HTML 完全自包含（零 CDN，断网可用）
- **KaTeX 服务端渲染**：`_render_formulas_katex()` 调 Node.js 脚本预渲染公式。打包环境无 Node.js → 先 `shutil.which("node")` 预检，不存则静默回退客户端渲染
- **markmap 离线化**：`markmap.ts` 不调用 `markmap-common` 的 `loadCSS`/`loadJS`（动态 CDN 加载会被 Edge WebView2 跟踪防护拦截），KaTeX CSS 在 PreviewArea 中静态 import，hljs 由 react-syntax-highlighter 内联
- **设置持久化**：`ProviderSettings.tsx` 中引擎/LLM Key 的保存不能仅靠 `onBlur`（组件卸载时不触发），需加 `useEffect` cleanup 用 ref 兜底保存
- **上下文窗口四层解析**（2026-08-04 C1）：`resolve_context_window()`（`model_resolver.py`）——用户自定义（`enabled_models.context_window`，设置页可编辑）> llm_models 预设 > provider 默认表（`core/token.py` `DEFAULT_CONTEXT`）> 保守 32k。对话预算 `budget = context_window × 0.7`（`core/chat_context.py`），历史按 token 反向裁剪 + 文档分级截断
- **路由异常标准化**：**19/19 个路由模块已全部完成 AppError 迁移**（2026-10-11 复核：全仓 `grep -l "HTTPException" backend/app/routers/*.py` 结果为空）。迁移时注意模式 #36 —— 真 404/403 必须显式传 `status_code`
- **Content-Disposition 中文文件名**：必须用 RFC 5987 `filename*=UTF-8''` 编码，否则 Starlette 会尝试 latin-1 编码 → UnicodeEncodeError
- **日志函数签名**：`app.core.logging.warning(msg, **kwargs)` 只接受 1 个位置参数 + kwargs，不能用 `%s` 占位符多参数调用。多参数用 f-string
- **空文本去重碰撞**：空字符串 SHA256 固定为 `e3b0c44...`，两个空导入会误判为重复。TextAdapter 需检查 `text.strip()` 非空
- **前端 apiUrl() 辅助函数**：`api/core.ts` 导出 `apiUrl(path)`（Tauri 下拼接 `http://127.0.0.1:7413/api`，浏览器下保持相对路径）与 `appUrl(path)`（图片/静态资源转绝对 URL，Tauri 必需）。所有 HTTP 请求必须走 `apiUrl` 或 `apiFetch`
- **processingTasks key 粒度**：`${docId}__${kind}` — 同一文档的不同操作（retranscribe/generate/process）不互相覆盖，store 提供 `clearDocTasks(docId)` 批量清除
- **设置页 overlay**：`App.tsx` 中设置页用条件渲染 overlay 而非替换整个 render tree，避免 GenPanel 卸载丢失进行中的任务状态
- **SSE 断开保护**：`streamGenerate` 追踪 `receivedDoneOrError` 标志，连接异常断开时触发 `onError('连接意外断开')` 防止 `generating` 永不复位
- **操作互斥**：GenPanel 中 `lockOtherOps` — 转录/图片处理中禁止生成，反之亦然。`Ctrl+G` 操作进行中拦截+Toast 提示
- **取消机制**：5 个端点全部覆盖 — generate/ingest/retranscribe/chat/process 均有 `cancel_event` + `_watch_disconnect`（0.5s 检测；fact_check 已随 AI 审核移除 2026-08-16）。LLM 调用逐 chunk 检查取消信号停止 token 消耗。本地同步引擎用 `asyncio.to_thread` 放到线程池避免卡死事件循环，引擎内部逐段检查 `cancel_event.is_set()` 立即停止 CPU

## 关键模式（71 条）

> **完整内容见 [docs/patterns.md](docs/patterns.md)** —— 实现中易错的关键约束，新增代码必须遵守。
>
> ⚠️ **本轮（2026-10-06）从本文件拆出**：本文件已 184KB，而该段的**起点就越过 64KB**，
> 等于**日常根本不加载** —— 偏偏它才是写代码时最该守的东西（不是风格偏好，每条都带
> 「为什么」和「怎么避开」）。拆开后按需读取，指路留在原位。
>
> **分三组 + 两条补录**：前端 `#1`-`#8`（状态/取消/任务）｜后端 `#9`-`#31`（并发/配置/日志/异步）
> ｜**同源与成对判据 `#32`-`#66`**（最容易踩：成对逻辑必须同源、判据要自证有区分力、
> 能力存在 ≠ 该路径在用、延后提交的 pending 必须有人消费……）；`#67` 是 **CSS 宿主环境
> 陷阱**（祖先的 `backdrop-filter` 会创建包含块、把 `fixed` 后代锁进面板 → **对话框** Portal 到 body；
> ⚠️ `absolute` 锚在按钮上的**下拉**不能 Portal（会失去定位基准），改写 document 监听 —— 见该条末尾的例外）。
> `#68` 把同源判据延伸到**文档自身**（手写节号是第二真相源 → 门禁 `docs` 层机器校验）。
> 末尾 `#69`-`#71` 是 **CSS 层**三条：**计算值继承**（`var()` 派生必须在每个会被设属性的元素上重跑 ——
> 「三条轴都对、只有 accent 坏」就是它）、**暗色是屏幕概念**（`prefers-color-scheme` 与 `@media print`
> 不互斥 → 包 `@media screen`，别维护还原表）、**测试解析嵌套 CSS**（`@media` 会让判据恒 -1 而不报错）。
>
> 本文件其余部分（架构、管道、导出、数据流）仍在下方，模式里的 `#NN` 交叉引用一律指
> **[docs/patterns.md](docs/patterns.md)**。

## 待办事项

> 图例：✅ 已完成　🟡 基础可用，待增强　❌ 未开始

### 📥 内容导入

| 状态 | 功能 | 说明 |
|------|------|------|
| ✅ | URL 导入 | 网页/视频/图片 URL → 适配器路由 → 正文提取 |
| ✅ | 文本粘贴 | 直接粘贴纯文本/Markdown |
| ✅ | 文件上传 | 本地文件（PDF/Word/PPT/Excel/EPUB/图片/视频），最大 2GB |
| ✅ | URL 去重 | `find_by_url` 拦截重复 URL |
| ✅ | 内容去重 | SHA256 contentHash 拦截重复内容 |
| ✅ | SSE 流式导入 | 实时进度推送 + 心跳保活 + 客户端断开不丢结果 |
| ✅ | 平台认证 | B站/YouTube Cookie 注入，自动解析 Netscape 格式 |
| ✅ | 文件拖拽上传 | ImportSection `onDragOver`/`onDrop` + `validateDropFile` 扩展名校验，拖拽区高亮反馈 |

### 📚 文档库（组织能力 2026-09-23；交互重设计 2026-09-25）

| 状态 | 功能 | 说明 |
|------|------|------|
| ✅ | **可嵌套文件夹**（2026-10-18） | `routers/folders.py` 五端点（含 `POST /folders/ensure-path`）；**纯逻辑分类不映射磁盘目录**；悬空 folderId 双向容错；**筛文件夹含全部子树**（`include_descendants`）；**删文件夹整棵子树一起删**、各层文档回落未分类、绝不删文档；`totalCount` 含子树；拖拽移动 + 右键菜单 + `FolderPicker` 树形选择；VS Code 式勾选（整列热区 + Ctrl 划选）；`limo list --recursive` |
| ✅ | 全文搜索接入 | 内存过滤（0ms）即时渲染 + 消抖 250ms 打 FTS5 合并；命中**并入主列表**（走 `DocRow`，受筛选约束）加「原文」角标 + `SearchSnippet` 摘要；相关度升格为一等排序键；限 100 条并提示截断 |
| ✅ | 排列 ⊥ 排序 | **排列**（平铺/按文件夹/按类型/按时间）与**排序**（时间/标题/类型/相关度）是两个独立旋钮；**无「筛选时退化成扁平」的特殊模式**；IDE 式一体化树 |
| ✅ | 可折叠筛选区 | 五小节（文件夹范围/文件类型/标签/标题关键词/日期范围）+ 两级折叠 + 收起显激活摘要 + 可拖分隔条；标签无 12 上限；标题关键词纯内存不触发 FTS；「本周」= 本自然周 |
| ✅ | 常态多选 | 复选框列常驻、图标悬停/选中才显；**单击 = 打开文档且不动选择集**；Ctrl/Shift 区间/Ctrl+A（输入框内不劫持）/Esc；`selectMode` 已删除，批量条由两个集合**派生**。**Ctrl 按住拖动划选**：方向由起点行决定（已选中 → 整段取消）、滚动可续选、幂等；**文件夹多选**（独立集合，点/Shift 分组头复选框）→ 批量**移动 / 删除**（删夹级联子树、文档回落未分类） |
| ✅ | 密度切换 | 详细（卡片）/ 简略（26px 单行 + 筛选浮层 + **IDE 文件树**：固定按文件夹分组、子行缩进 + 参考线、分组头与文件行同高） |
| ✅ | 大列表分批渲染 | 前 60 条 + IntersectionObserver 每次 +60（`root` 绑滚动容器）；**全选 = 全部匹配项（跨分批）**；⚠️ 顺带修 `animationDelay: i*50ms`（第 1000 篇要等 50 秒的**功能性错误**） |
| ✅ | 批量操作 | `POST /api/transcripts/batch`（删除/打标签/移动）；按 action 显式枚举校验；`failed` 明细；全失败 → 400；store 侧一次加锁一次写盘 |
| ✅ | Ctrl+K 搜索入口 | 顶栏 `SearchBar` 退化为按钮，与 Ctrl+K 共用 `openDocLibSearch`（展开面板 + 聚焦搜索框 + 宽度复位） |
| ✅ | 智能分类（2026-10-11；同日二轮改两阶段） | 选中 N 篇 → 对话框**先选配置**（取材深度 / 文件夹策略 / 新建上限 / 标签策略）→ 用户按「开始分析」才发 `POST /api/classify/preview`（一次 LLM 调用、**零写入**）建议「每篇 → 文件夹 + 标签」→ 逐篇审定/编辑 → `POST /api/classify/commit`（一次加锁一次写盘）。**模型永不直接写库**；**打开对话框零请求**（`onStart` 是唯一计费出口——此前「打开即分析」）；两项策略各三档（文件夹：复用或新建 / 只复用 / 只打标签；标签：全库词表 / 用户手填基础标签 / 完全交给模型），**非法策略显式报错且不发起调用**；分类空间**优先复用**既有文件夹（新建上限 5）；**细分程度**用户选（不建子夹 / **允许一级【默认】** / 允许两级，见文档库段末）；取材深度用户选（600/200/仅标题，无笔记篇显式标 `title_only`）；**不降级**（超时/坏 JSON 一律显式报错）；新夹由前端在 commit 前建、commit 只做存在性校验。⚠️ 对话框与文档库内所有浮层 **Portal 到 body**（`backdrop-blur` 会创建包含块把 `fixed` 锁进面板）；⚠️ **例外**：`FilterPopover` 的筛选浮层与 `ChatInput` 的 @ 浮层是 `absolute` 锚在按钮上的下拉，Portal 会失去定位基准 → 改 document 监听。**同日三轮：分批（20 篇/批，抗大 N）+ 关思考（`disable_thinking`，免推理链烧光输出预算）+ 部分批失败显式 warning。同日四轮（代码审查）：预算按单批均摊 + 新建上限按批分配 + 审定阶段失败仍可用 + 空词表兜底同源。同日五轮（91 篇实机验收）：空库文案按策略分两份——`reuse_or_new` 在空库不再被静默降级成「只打标签」（实机：新建 0 → 23）。**同日六轮：超限被拒时保留候补夹名（对话框一键采纳 + 预填）+ 杂项口袋夹（不占新建额度；进夹篇数超 1/3 整夹撤下的机械闸门）+ 修 `maxNewFolders` 回显不同源 / 标签清洗三份实现漏截长 + 判据定形**。⚠️ **2026-10-18 嵌套**：模型可建议 `技术/前端` 式路径（`parse_path` 唯一实现）；同名歧义显式化（不再按数组取第一个）；新建额度改按**文件夹行数**计；口袋夹按完整路径分组；前端改走 `ensure-path`（原串透传、不解析）；复审下拉显示路径而非裸名 |
| ✅ | 标签建议（2026-10-18） | 产物自证条**同一行**后面一排小徽章：`lib/tag-suggest.ts::suggestTags`（纯函数）从**库里已有的标签**中挑出这篇正文真提到过的几个，点一下直接加。**零 LLM / 零网络 / 零落盘** —— 让模型「生成时顺便判标签」被否掉的理由不是钱，是注意力被分走而产物上看不出异常（追问侧同款教训）。四条匹配规则各自对应一种「不做就全是噪音」：**ASCII 走词边界**（`AI` 不能命中 `MAIL`）/ **中文走子串**（`\b` 会让「前端」永远匹配不上）/ 标题命中优先 / 去冗余（`前端` 的全部出现都在 `前端框架` 里则丢短的）。⚠️ **采纳必须走 `POST /api/transcripts/batch` 的 `tag` action，不能走 `/classify/commit`** —— 后者的 `apply_classifications` 是 `e["folderId"] = a.get("folder_id") or ""` **无条件覆盖**，只带 tags 会把文档踢回未分类且界面上看不出来。⚠️ 只能复用已有标签（发现新词留给智能分类）；不参与导出、不改正文 |

### 🔗 多篇合成（2026-10-01 建立，2026-10-02 三补）

| 状态 | 功能 | 说明 |
|------|------|------|
| ✅ | 合成产物 = 普通文档 | `sourceType="composite"`，自动获得文档库/搜索/标签/文件夹/导出/对话引用/版本管理（**零链路分支**） |
| ✅ | 出处头嵌进正文 | `<!-- limo-meta {json} -->` 第一行 + `## 来源文档` 列表；`core/transcript_meta.py` 收口编解码；删 index.json 重建后字段仍在；**无头 ≠ 损坏** |
| ✅ | Step 0 分叉 | `Σ token ≤ 预算` → Stuff（**零 LLM 调用**）/ 超出 → Map-Reduce（维度发现 + 逐篇固定维度结构化抽取，**非自由摘要**） |
| ✅ | 预算同源 | `generation/budget.py`：`single` 逐值等价（含已知低估 + `xfail(strict)` 钉住）/ `multi` 闭合；`cw` 由调用方从 `resolve_context_window()` 传入（**修正生成路径此前根本没走四层解析**）；窗口 < 102,536 显式拒绝 |
| ✅ | 降级显式化 | 被压缩/截断的篇逐条标 `✓/⚠` 落进「## 来源文档」；抽取失败也显式标注，不静默跳过 |
| ✅ | 资产搬运 | 源 `assets/` 复制 + **引用同步改写** + 源序号前缀防重名；⚠️ **每个路径都过 `relative_to` 守卫**（防路径穿越） |
| ✅ | 生成闸门 | composite → 不产 polished / 清空 `format_list` / 跳过 `_auto_title`；`metadataCorrupt` 推 warning 不阻断 |
| ✅ | 前端入口 | BatchBar 合成按钮（≥2 篇）+ 确认对话框（已选清单/标题/对比指引 + **逐篇 token/处理方式 + 原料单选 + 维度列表**）；DocRow「N 合 1」徽章；「合成」筛选项；tab 只注册 summary/transcript + `defaultFor` |
| ✅ | 超窗分层（2026-10-02） | 单篇超窗 → **篇内分块归并**（配额内覆盖全文）；病态长文 → **软 deadline**（剩余篇截断而非整次作废）；降级状态 **3 态 → 8 态**（原 `truncated` 是垃圾桶）+ 基础状态回退 |
| ✅ | 预览端点（2026-10-02） | `POST /api/synthesis/preview`（零 LLM 调用/零副作用）：逐篇 token + 处理方式 + `stuff_window`；判据与执行**同源**（同一个 `_classify`）；banner 触发集只含 `over_window`/`reject` |
| ✅ | 维度可控（2026-10-02） | `DIMENSION_PRESETS` 四套经 capabilities 下发；用户给定则跳过维度发现调用；**Stuff 路径也注入**（修复前为零）；维度落出处头 meta |
| ✅ | 原料可选（2026-10-02） | `raw`（默认）/ `notes` 最新版，**互斥不混合**；预览顺带返回 `hasNotes`；后端执行时再校验；`sourceMode` 落 meta |
| ✅ | 读者意图与体裁（2026-10-04；2026-10-18 加「梳理观点」共六项） | `READER_INTENTS` 决定 `<task>` 段的读者目的**与产物形态**（`shape` → 五套骨架之一）；`CONTENT_TYPES` 多标签决定加载哪几类体裁增量条款；两者**经 meta** 抵达生成 prompt；「理解主题 / 梳理观点 / 比较材料」刻意不预设体裁 |
| ✅ | 产物形态重做（2026-10-04） | 默认套换 general 7 维；约束拆三块（事实/文风/体裁）；覆盖说明由开场换到综合评价末尾；删死参数 `user_notes`；`dump_meta` 递归清洗；入参清洗移进 `runner`（原本非法值走 HTTP 500 逃出 SSE 通道） |
| ✅ | 骨架重做（2026-10-05） | 五段固定 → **读者问题驱动五节**（根因是旧骨架与文风条款互斥，不是文风不够）；维度降为「读者问题」候选来源（**逐条注入不变、不能删**）；事实约束第 3 条改「材料不足以判断」；覆盖说明搬到材料索引表下；文风续写第 8/9 条（说人话 / 不限篇幅但按问题数量定）；⚠️ 第 8 条刻意不抄单篇 plain 档的「不堆小标题」 |
| ✅ | 各篇速查工具区（2026-10-05） | 真实场景驱动（老师看论文，同时看多篇不方便）：三个诉求里两个（看不过来 / 比不了）要**高密度可扫的对照**，与文章区的低密度要求**相反** → **同一份文档分区不折中**（文章四节 → 材料索引 → 速查）；**按维度组织**（维度作 `###`、节内每篇一行）；⚠️ **竖排清单不是表格**——避开「内容多影响显示」的横向挤压；⚠️ 两条分工禁令防「把文章区说第二遍」，测试断禁令在场而非只断节存在；维度由此**两个落点**（候选来源 + 速查小节标题），补上了「降为候选」后的硬落点缺失；⚠️ **刻意不做场景门控**（2026-10-05 决策，勿当遗漏）：场景适配由**维度分化**承担——`paper`/`tech`/`product` 维度全是逐篇取值型 → 小节全留；`general`/`media` 混着关系性维度（「关键差异」）→ 被「只列每篇各自有取值的维度」自动跳过、**缩水不消失**。加开关等于维护一份「这批材料结构可不可比」的判据，**那件事用户比我们清楚**（真需要时让他改维度更直接）；✅ **2026-10-05 两批真实重跑实测成立**（3 篇论文 → 5 小节全留 / 6 篇观点文 → **7 维缩成 5 节**，恰好是两个关系性维度被跳过），**不需要按场景区分**；残余观察点：锚点密度 0.56（论文）/ 0.94（通用，逼近 ≤1 上限） |

### 📝 转录 & 引擎

| 状态 | 功能 | 说明 |
|------|------|------|
| ✅ | ASR 引擎 | bcut (B站必剪免费云转写) + dashscope Paraformer (云端)。faster-whisper 已从内置移除（本地转写需自行安装） |
| ✅ | OCR 引擎 | dashscope VL (云端)。Tesseract 已从内置移除（需三方插件 `examples/tesseract_plugin.py`） |
| ✅ | Doc 引擎 | pypdfium2 (PDF默认,BSD-3) + pdfplumber (表格,MIT) + **pdf-image（整体转图片→OCR/多模态）** + python-docx/pptx/xlsx + openpyxl/.xls(xlrd) + markitdown + epub-container 自研 EPUB（ebooklib AGPL 已移除 2026-08-26；docling 已移除；.doc/.ppt 旧格式明确提示转 .docx/.pptx） |
| ✅ | 引擎绑定 | `engine_bindings` 按子场景配置，doc 拆为 pdf / word / ppt / excel / epub |
| ✅ | 引擎绑定只取第一个 | 绑定解析首个可用引擎，失败直接报错，不再静默降级 |
| ✅ | 视频帧提取 | ffmpeg 抽帧 + 网格拼接 + 去重（无 opencv 依赖） |
| ✅ | 模糊帧过滤 | Laplacian 方差 + 熵 + 边缘密度评分，丢弃低质量帧 |
| ✅ | 插件系统 | EnginePlugin v1 进程外（默认）/trusted 例外 + 一键安装依赖（镜像源）+ 三方插件发布（pymupdf4llm）。**官方模板四份**（[examples/](backend/plugins/examples/README.md)）：pymupdf4llm PDF / **faster-whisper 本地语音转写** / tesseract 本地 OCR / trusted 进程内；每份都带 `precheck` 就绪自检（**进程外 `probe_venv`、进程内 `probe`**，选错方向 = 装好了却显示不可用） |

### ✨ LLM 生成

| 状态 | 功能 | 说明 |
|------|------|------|
| ✅ | 双产物并行 | notes + polished，`asyncio.gather` 并发 |
| ✅ | 追问卡片（2026-10-11） | 生成前勾选（「笔记格式」区，与图表同排，**只 notes**）→ 生成完成后**独立再调一次 LLM**，笔记末尾列出 4~6 个读完自然会问的问题，点一下**自动打开 AI 浮窗并带着这篇文档发问**。⚠️ 「勾了必定有」靠独立调用而非 prompt 强制；追问落**旁路文件** `notes/v{N}.followups.json`（不进正文 / index / 自证头 / **不进导出** ⇒ 导出侧零改动）；事件推在 `done` **之后**、不吃生成预算；`AVAILABLE_OPTIONS` 登记但 **prompt 层零注入**（`_ALL_FORMAT_FNS` 刻意不含它）；⚠️ 那一次调用的**思考档位按模型分两支**（`thinking_extra`：能力位 `reasoning` 为真 → **关闭思考**；为假 → **不传**）|
| ✅ | prompt 按类型派发 | 视频/网页→通用，PDF/文档→文档 prompt，Excel→分析+数据摘要 |
| ✅ | SSE 流式输出 | 逐 token 推送 + cancel_event 真正中断 |
| ✅ | 自动续写 | `finish=length` → 3 轮续写，temperature 递减 |
| ✅ | 版本归档 | **全部版本独立归档不设上限**（`{mode}/v{N}.md`，两版制已重做）；版本下拉显示时间/字数/来源/编辑标记；切换 = 纯视图零写入；按版本导出；编辑 = 就地覆盖当前版**不产生版本**（2026-10-10） |
| ✅ | 模型解析 | `resolve_model()` 唯一入口，多模态能力自动检测 |
| ✅ | 目标语言 | config `target_language`，8 语言可选 |
| ✅ | 参考文件 | 参考资料（如评论区观点）注入笔记生成，仅 notes；暂存 id 传递（`POST /api/references`）；上限 1MB；**超出模型上下文时自动先压缩**（`stage` 事件 + 前端小字提示，2026-09-23） |
| ✅ | 联网检索（2026-10-12） | 生成面板「参考资料」块内勾选：LLM 从正文抽检索词 → **复用对话侧搜索工具**（Tavily/SearXNG/DDG 三级降级）→ 结果拼成**一份**参考资料走**既有** `reference_texts` 通道注入笔记（只注 notes，超窗压缩与注入防御免费继承）。**失败显式不降级**：抽词失败退化用标题检索、零命中则不注入，两者都推专属 `warning kind=web_search_failed`（前端 info 且**不标 partial**）；自证头 `webSearch` 留 `queries`/`hits`/`error` |
| ✅ | 视频直传 | 阶段 A Qwen 打通（2026-08-25）：DashScope SDK `MultiModalConversation` 原生通道（file:// ≤100MB 服务端抽帧），生成链路零改动（直传为 prepare 阶段一次非流式理解注入 clean_text），本地截帧链路保留为降级 |

| ✅ | 思考档位可配 | `generation/effort.py` 全系统唯一真相（此前 `"low"` 散落四处）；能力位 `reasoning` 按模型标注；**整理版恒 low 除非选 max** 且 UI 明示；静默期分档 150/180（不改则「选最高档反而更容易失败」）；AST 断言钉住无硬编码档位字面量 |
| ✅ | PDF 直传（2026-09-23） | 用户可选**二选一**（文本模式默认 / 直传模式只发原文件）；`file` 块随生成请求发送（**必须走 Chat Completions**）；闸门 14 MiB（由 data-uri 20 MiB 上限倒推）；能力位 `document` 标 dashscope 官方名单的三个（qwen3.8-max / flash / 27b），**其他渠道逐家查证后一律不标**（理由见下）；双真相源 + rawText 过短完成期 warning |
| ✅ | 默认文风通俗（2026-10-05） | 新增 `plain` 档并设为**全局默认**（Excel 例外，映射到 detailed 同值文案）；用户判断「大部分场景默认要通俗，论文类才要严谨详细」。落点 **8 处，缺一即静默无风格**；⚠️ 存量用户不受影响（localStorage 已存的 `style` 会覆盖新默认，**刻意不迁移**）；⚠️ polish 豁免元组同步加 plain（否则每次生成整理版刷一条 warning）；顺带修**三个静默改写风格档**的既有缺陷（meeting / none / 修 meeting 时的反向回归） |
| ✅ | 语义标记 `limo-block` v1（2026-10-10） | 模型在笔记里写 `<!-- limo-block:v1 type="key-takeaway" -->` … `<!-- /limo-block -->`（**最初只有这一种类型**；2026-10-11 一天内扩到**九种**、2026-10-20 补 `cycle` / `sequence` / `quadrant` 共**十二种**，分两族——**九个语义强调块** `key-takeaway` / `alert` / `recommend` / `metric` / `steps` / `timeline` / `cycle` / `sequence` / `quadrant`（`steps`/`timeline`/`cycle`/`sequence`/`quadrant` 是**从图表管道迁来的**「用列表代替画图」——`steps`/`timeline` 删的是 `hchain`/`timeline` 两种引导，2026-10-20 又把图表引导由**七种收缩到三种**（`grid`/`flowchart`/`state`），撤下的 `layers`/`tree` 改走嵌套列表、`sequence`/`quadrant` 改走这两种新块）+ **三个内容卡片** `table` / `code` / `image`（**块内是真表格/真代码/真图片，一个字节不动**，只加外壳；用户原话「同种类型的表现形式只是在原有的基础上再加上了围栏而已」）；⚠️ **`card` 性质又是第三种**（2026-10-11 加，当时是第十个；2026-10-20 的 `sequence` / `quadrant` 排在它之后）—— 其余十一种是**块**、`card` 是**节修饰符**（写在 `## 标题` 下一行、无闭合标记），它是**骨架轴 `card` 的唯一触发源**，同日同批把该轴从「每个 H2 一张卡」改成「只给标记过的章节成卡」），形态差异全在 CSS。⚠️ **内容卡片族纳入既有「内容块装饰」开关的射程**（关掉 = 还原成没包过围栏的样子），**不新造开关**，渲染为轻量强调块。**本轮搭的是「正文协议元素」这套架构，不是单做一个重点块功能**（用户原始诉求含「失败标记一类与内容无关的东西要隔离」——那批只留口子、下一批接入）。三条不变量：专用标记隔离 / **零残留降级**（剥标记行、文字一字不丢、剥完与「从未写过标记」逐字相同）/ 围栏内零触碰。**只入 notes**、**无勾选项无条件注入**、规范**不教外观**。⚠️ **刻意不加完成期 warning** —— 无标记是正常文章不是失败。顺带修 4 个既有缺陷（`_CARD_TOKENS_CSS` 注入闸门漏 `paper`+有标记 / Word 把注释泄漏成可见段落 / 合成把标记当模型输入 / `chars` 把标记算进正文字数）。⚠️ **2026-10-11 首轮实机验收结果**：九种块**确实在用**（一篇写齐了 5 种），但 **`card` 标记 0 个** → 同批把 `card` 提到规范最前（`PROMPT_VERSION` `"13"`）并提亮中性面板族的容器边框；**新一轮实机验收待用户操作**（`card` 新位置的遵循率、两种骨架下的观感、Word 读回看）。⚠️ **2026-10-20 扩张 + 图表收缩**：新增 `sequence`（消息流）/ `quadrant`（二维评估）两种块，同时 `get_chart_format` 由七种收缩到**三种**、`layouts.py` 删掉两条写死的层数闸门（实测 8 层纯矩形只需 564px 却被判「层数过深」——用户撞到的正是这条假失败）、完成期 warning 判据放宽（勾了图表但产出五种替代块之一 → 不再误报「未生成」）。判据统一为「**文本结构能否保真表达这个关系**」 |

### 🔍 图片处理

| 状态 | 功能 | 说明 |
|------|------|------|
| ✅ | 4 种模式 | extract (文字提取) / table (表格) / digitize (数字化) / understand (理解) |
| ✅ | LLM 优先 | 请求 model_id → scene_models.image_process → scene_models.generate → Engine fallback |
| ✅ | 图片优化 | `optimize_images` 开关，PIL 缩放 ≤ 1024px |
| ✅ | 同 mode 覆盖 | OCR 结果确定性，不版本化 |

### 💬 AI 对话

| 状态 | 功能 | 说明 |
|------|------|------|
| ✅ | 5 个工具 | search_docs / lookup_doc / list_docs / search_web / fetch_url |
| ✅ | 搜索多级降级 | Tavily API → SearXNG 自动启动 → DuckDuckGo |
| ✅ | SearXNG 便携版 | 自动检测 `.exe` 启动，asyncio.Lock 防并发 |
| ✅ | SSE 流式 + 持久化 | 逐 token 推送 + 自动保存对话 + 历史按 token 预算裁剪（替代固定 40 条） |
| ✅ | 对话 CRUD | 新建/重命名/删除/追加消息 + 归档（archived）+ 消息 rebuild（编辑/删除/分支截断） |
| ✅ | UI 全量重写 | 独立 chatStore 状态机 + 流式打字机 + 消息操作（复制/编辑/重新生成/删除/停止）+ 工具气泡三态 + 思考段折叠 + 长会话分页窗口 |
| ✅ | 消息级文档引用 | 输入区 @ 选文档 → docRefs chips；docIds = 当前选中 + 本条引用（动态组合）；引用 chips 点击跳转预览区 |
| ✅ | assistant 引用块 | tool_result.refs → assistant 消息引用文档 chips（点击跳转） |
| ✅ | 上下文管理 | 模型窗口感知预算（context_window 四层解析）+ 历史 token 反向裁剪 + 长对话 LLM 压缩 + 前端 token 占用进度/费用显示 + clearContext + 429 重试 |
| ✅ | 回答形态卡片（2026-10-11；2026-10-12 扩到七种 + 视觉跟随 + 两个交互） | 对话侧 `limo-block`：`card-group`（并列比较 2–4 个同级对象 → 并排卡片）/ `follow-up`（2–4 个下一步问题 → 可点 chip，点了直接发送）/ `info`（信息卡）/ `digest`（摘要卡）——后两种 2026-10-11 补；2026-10-12 再补 `caution`（注意事项）/ `how-to`（操作步骤，有序列表）/ `quote`（引用出处）→ **共七种**，规范含**总量闸门**（这些块合计不超两块）。⚠️ **视觉跟随卡片方案**：容器级属性（border / radius / bg / shadow）消费 `--card-*`（与产物侧章节卡片同一套 8 方案），**尺寸级保持 em**（`--card-padding` 是 `22px 26px` 的宽栏值，浮窗里会撑爆）；**零 JS 改动**（初值在裸 `:root`、`paper` 下不删变量）。⚠️ **两个交互**：卡片整体复制（`card-group` 每卡 + `caution`/`how-to`/`quote` 外壳；`info`/`digest` **刻意不加**）+ 追问**换一批**（`POST /api/chat/followups` **无状态**端点 + `ChatFollowUpList.onRefresh` **缺省不渲染按钮** + 失败**保留原有那批**）。⚠️ **只共享判据不共享渲染器**（新组件 `ChatMarkerContent`，**不复用**产物侧 `MarkerContent` —— 它会波及全部历史消息）；⚠️ 流式**半截标记行整行抑制**；`.md` 导出与复制**剥离**、`.json`/存储/LLM 历史**保留**；规范走 `agent/prompts/formats.md`（第四层，无条件注入）。⚠️ **追问卡片（2026-10-11）**：升为**用户可选 + 选了必定有** —— 开关（`lib/chat-prefs.ts`，**默认关 = 零回归**）开启后由 `agent/followup.py` **独立补调一次** LLM（Dify 同款）并推 `followup` 事件（推在 `done` **之后**）；判据 `has_usable_followup`（有块**且**抽得出问题，空壳块会让补调被跳过）；追问**独立字段存放**（`ChatMsg.followUps` → 会话 `follow_ups`）**不写进 `content`** ⇒ 导出天然不含候选；**追问的追问天然成立**（点 chip 走 `sendMessage`）；气泡内部末尾 + **默认展开可折叠** |

### 📊 图表生成（2026-08-16 建立，2026-09-19 语义化布局 + 多图）

> AI 审核（原事实验证）已随方案「图表功能与 fact_check 移除」下架转历史——后端 fact_check/review/evidence 删除、前端 fact_check tab 移除，历史 fact_check.md 产物保留不删（无读取入口）。旧实现记录见 CHANGELOG 2026-07-15 起条目。

| 状态 | 功能 | 说明 |
|------|------|------|
| ✅ | 笔记可选组件 | 生成笔记勾选「图表」（默认不勾选），prompt 按需注入 `get_chart_format(source_type)` 规范 |
| ✅ | 来源差异提示 | 作用已变为「**优先推荐**哪几类」（2026-10-11 起 `hchain` / `timeline` 已从引导里删去，线性流程改走语义块 `steps` / `timeline`）；DOC→tree/flowchart/layers/grid；网页→flowchart/grid/tree；视频→sequence/state/flowchart；其他→tree/flowchart/grid |
| ✅ | **语义化布局**（2026-09-19） | LLM 只输出 ` ```fig ` 语义 JSON（**不写任何坐标**）→ `chart_layout` 算几何 → `chart_svg` 生成 → PIL 渲染。几何错误从结构上不可能发生 |
| ✅ | **类型 4 → 9**（2026-09-19 续） | 新增 tree/flowchart/state/sequence/quadrant + 形状层（diamond/stadium/circle）+ `shapes` 通道（轴线/生命线/分组框）。A/B 实测出图率零下降、类型从 1 种扩到 7 种 |
| ✅ | **几何缺陷五修**（2026-09-19 续） | 同行跨列边穿节点 / 走廊边叠同一 y / 隔行边穿中间行 / edges·shapes 不参与 bbox（回边沟槽被裁）/ 报错文案误导（「节点过多」掩盖「有环」）。另补三条 `assert_layout` 不变量 |
| ✅ | **回边分道 + 标签让位**（2026-10-26） | `_state` 的 `corr_y`/`entry` 原先**只依赖层号** ⇒ 同一节点发出的多条回边水平段完全重合、沟槽 x 只差 6px，两个标签必然叠死 → `assert_layout` 硬失败 → **整张图降级成失败占位**（用户报的「偶发」4/6 条）。改为**走廊车道池统一分道**（前向边占 1 段、回边占 2 段，走廊高度按段数增长；纯前向图逐值不变）+ `engine._relax_labels` 兜底（标签仍重叠则上下让位 ±20/±40/±60，让不开就**丢弃标签**、连线仍在，同 `_grid_edge_label` 的 `None` 取舍）。顺带修 `_trim_canvas` 的 `dx` 被宽标签拉成非 4 倍数 → 整图节点脱格。⚠️ **判据是「让位只是兜底」**：主修在算子分道，有对照断言钉住，别把让位当主角 |
| ✅ | **`title` 渲染**（2026-09-19 续） | 此前解析了从不绘制；`TITLE_H=24` 在 `expand` 计入。**接受 3.75% 容量损失（640→616），不动 `_MIN_SCALE`** |
| ✅ | **多图**（2026-09-19） | 每个围栏 = 1 张图，1~N 张；单围栏 ≤9 节点超出须拆图；编号按文档顺序递增、失败不跳号。实测 6 张图 9.8s |
| ✅ | **自然尺寸 + 等比缩放**（2026-09-19） | 高度超 640 不再失败，按自然高度定稿 + `<g transform="scale(k)">`；`_MIN_SCALE=0.65` 下限；宽度按内容跨度裁剪 **并把内容平移回 MARGIN**（2026-10-20 修：此前只裁右不裁左，窄图左侧反而留白） |
| ✅ | PIL 渲染嵌入 | 围栏 → 校验链 → PIL 渲染 PNG(2x) → `assets/chart_{hash8}.png` → `![图表](...)` 引用（resvg 全系 Windows 无法渲染 SVG 文字，2026-08-16 切换）；legacy ` ```chart ` 手写 SVG 路径完整保留为回退 |
| ✅ | 校验链 + 安全过滤 | 只认 ```fig/```chart 专用围栏/XML 良构/viewBox ≤640×640/有形状元素 + script·on*·外部引用剥离；失败替换为 📊 失败文本（零残留）；无图表围栏 → 完成 warning |
| ✅ | 只入 notes | polish 白名单无 chart；xlsx/audio 无勾选；前端排除 epub |
| ❌ | 更多图表类型 | 当前 10 个算子（prompt 层面只暴露**三种** —— `grid` / `flowchart` / `state`；其余引导已撤：`hchain`/`timeline` → 语义块、`layers`/`tree` → 嵌套列表、`sequence`/`quadrant` → 语义块【2026-10-20】，`branch` 已实现未上 prompt），无 echarts/mermaid 等专业图库（插件化方向，暂不实施） |

### 📤 导出

| 状态 | 功能 | 说明 |
|------|------|------|
| ✅ | MD 下载 | 前端 Blob 直接下载；**可连图片一起带走**（设置项 `embedImages` **默认开** → 走 `GET /export/{id}?format=md&image_md=inline` base64 内嵌，**依赖后端**；关则纯前端零依赖。2026-10-11） |
| ✅ | HTML 导出 | 代码高亮(Pygments)+GFM+时间戳badge+标题锚点，4 主题，完全自包含 |
| ✅ | KaTeX 内联 | CSS/JS/字体(60个) base64 内联，零 CDN，断网可用 |
| ✅ | MD 图片路径 | 导出时自动重写为绝对 URL，外部查看不裂图 |
| ✅ | ZIP 批量 | 全部产物打包，图片独立文件夹 |
| ✅ | 思维导图 | SVG 下载 + markmap 交互式 HTML (~980KB，暗色自适应) |
| ✅ | 对话导出 | Markdown + JSON，支持批量 ZIP |
| ✅ | Word 导出 | Markdown→HTML→docx（共用解析器 `markdown_render.py`）；标题大纲级别/嵌套列表/表格/代码块/引用/超链接/☑任务列表/时间戳双色 badge/图片等比内嵌；公式保留 LaTeX 原文（2026-09-21） |
| 🟡 | 导出进度反馈 | ⚠️ **后端就绪、前端未接线**（2026-10-06 P1-12 核实，2026-10-26 复核）：`POST /export/custom/stream`（SSE 进度）+ `GET /export/download/{temp_id}` 端点完备，打包已 `await asyncio.to_thread(_build_export_zip, ...)`（不再阻塞事件循环），但前端**零调用** —— 那个 `streamExportProgress` 封装已作为死代码删除（抄回来仍是零调用，死代码只换了个地方躺），`ExportPanel` 的「详细导出」走**同步** `POST /export/custom`。如实标注，**不写成已可用**；要用起来要做的是**把 UI 接起来** |
| ❌ | 图片预处理复用 | 导入时已 optimize_images，导出 ZIP 未复用压缩后图片 |
| ✅ | 详细导出判据修复（2026-10-11） | 审查查出六条：跨类型 tab 并集不再产出「名字对不上、内容是同一段原文」的假文件（前端滤 `contentSource='none'` + 后端不回退 rawText）/「原图」「OCR」摘出导出清单（它们本就没有内容源）/ `/export/batch` 空结果 404（与 `/all`、`/custom` 统一）/ 一键导出剥 `limo-block`（与定制导出同源）/ `_inline_images` 补 `doc_dir`（成对守卫）/ 只勾 Word 时不再写无人引用的 `images/`。⚠️ **同一批发现三个导出测试文件在门禁里空跑**（模块级裸断言 → pytest 收集数 0），已加适配层 |
| ✅ | PDF 导出 | 两条路径共用一份自包含 HTML——**Tauri**：Rust `export_pdf` → 屏幕外窗口 → `ICoreWebView2_7::PrintToPdf` **免对话框直接落文件**（A4 + 背景保留 + 无页眉页脚，存系统「下载」目录、同名加序号）；**浏览器**：屏幕外 iframe → 打印对话框。打印样式三处落点（`_PRINT_CSS` + Pygments 浅底 defs + limo 暗色还原）。两条链均已实测（2026-09-21） |
| ✅ | md 公式「render」通道已移除 | ~~`formula_md=render` 依赖 matplotlib 而它不在依赖里 → 该选项自上线起从未生效（静默降级）~~ —— 2026-10-26 复核时**通道已整体移除**（函数 + 四处调用点 + 前端选项与类型，`tests/test_export_formula.py` 源码级守卫，含「删多了」的对照）。**「切到 KaTeX 图片管线」这个修法本身不成立**：`_render_formulas_katex` 靠 WebView 在客户端渲染，服务端拿不到 SVG/PNG；为「保留原文也能接受」的选项补 2GB 依赖不值得。保留 `keep` / `remove` 两条通道 |

### 🎨 前端 UI/UX

| 状态 | 功能 | 说明 |
|------|------|------|
| ✅ | 三栏布局 + AI 浮窗 | GenPanel \| DocLibrary \| PreviewArea 可拖拽 resize；AI 对话统一浮窗（位置/尺寸记忆、内部栏拖拽调宽） |
| ✅ | 左侧活动栏 + 面板互斥（2026-10-10） | `ui/ActivityBar.tsx`：常驻最左竖排图标列，面板项**从 `SlotRegistry.grouped().left` 派生** → 加一个面板自动多一个图标；顶栏面板按钮**全部删除**（唯一入口）。左侧两面板**默认互斥**（`layout-prefs.ts`，可在 设置→外观 切回并排）。互斥靠 `uiSlice.activeLeftPanel` **选择器字段**而非「打开甲时收起乙」——后者可被四处直接改单个 slot 的调用点绕过。顺带把 `SlotDef.component` 补成必填（加面板从「改两处」变「改一处」）。⚠️ 与文档多标签页同一批：都不是新功能，是**入口收敛** |
| ✅ | PanelStrip | <80px 自动折叠为图标条（⚠️ 互斥模式下被关掉的面板**连窄条都不渲染**——一次只该有一个） |
| ✅ | 快捷键 | `Ctrl+G` 切生成面板（互斥下再按关闭且不露出文档库）/ `Ctrl+I` AI 浮窗 / `Ctrl+W` 阅读宽度 / `Ctrl+K` 文档库搜索 / `Ctrl+\` 沉浸阅读 / `Ctrl+滚轮`·`Ctrl+=`·`+`·`-`·`_`·`0` 字号 |
| ✅ | 后端持续探活 | `lib/health.ts` 10s 轮询 `/api/health`，运行中断开顶部横幅（down「正在重试」/ crashed「请重启」分级，超 30s 判崩溃），恢复自动消失；与启动探活职责分离 |
| ✅ | 缺 key 主动提示 | 导入图片/视频缺 dashscope（内联提示条可 X 关）、发送对话缺 LLM key（toast 首次）前置提示，localStorage 记忆 |
| ✅ | 主题系统 | 六轴独立 — `data-theme`(明暗) × `data-accent`(26套配色) × `data-base`(14种基调) × `data-texture`(6种纹理) × `data-font`(5种字体) × 强度滑条。CSS 变量（`--tk-*` 全动态）+ Tailwind v4 `@theme` 引用。一键预设包（20个: 护眼/经典/粉色/雅致/国风各4）。Markdown 用 `--md-*` 变量（全部引用 `--tk-*` 或 HSL 派生，零硬编码色值） |
| ✅ | GenPanel 类型门控 | 5 类型 × 5 子标签，逐类型显隐选项 |
| ✅ | EPUB 一键解读 | 逐章解读 + 归并全书总结 + 中断续跑 + 三态 UI（进度条/继续上次/一键解读），GenPanel epub 专属入口 |
| ✅ | 图表生成 UI | GenerateOptions 笔记格式区「图表」勾选（视频/网页/PDF/文档/文本显示；Excel/图片/电子书排除），默认不勾选 |
| ✅ | 首次启动向导 | OnboardingWizard 三屏：阅读氛围 → 系统组件检测 → AI 配置（LLM/DashScope key，基于 missingKeys 自动跳过已配项，可整屏跳过） |
| ✅ | 阅读宽度自适应 | 内容上限 `max(1100, 面板宽−160)`，折叠侧栏后阅读区利用空白（2026-08-16） |
| ✅ | 编辑器分屏拖拽 | 产物编辑器 源码\|预览 分隔条可拖拽调宽（25-75%，对齐三栏交互，2026-08-16） |
| ✅ | 编辑/输入 mono 字体 | 对话输入框 + 文本/链接/图片/文件 URL 粘贴框统一等宽字体（2026-08-16） |
| ✅ | 欢迎文档三产物 | 空态示例文档 = 笔记（欢迎简介）/整理版原文（开发者功能）/转录（更新记录，2026-08-16） |
| ✅ | 产物编辑保护 | notes/polished 手动编辑（源码\|预览分屏）+ 未保存离开三态弹窗（保存并离开/不保存/取消）+ 编辑态绑定来源 docId（弹窗期间切文档不错存）+ 编辑中未保存 × 重新生成拦截（`dirtyEditingDocId` 轻量桥，2026-08-25） |
| ✅ | 沉浸阅读 | 去噪容器（`Ctrl+\` 开关 / `Esc` 退出 / **预览区工具栏「沉浸」按钮**（2026-10-10 补，活动栏在沉浸下也一并隐藏）/ `hideSlots` 藏侧栏，进入复用 Ctrl+G 的 `processingTasks` 守卫）+ 阅读版式三旋钮（行高 / 每行字数 / 段落区隔，**默认值 = 改造前现状**故零回归）。⚠️ **导出不跟随**（用户决策，已知偏差）。详见 CHANGELOG 2026-10-02 |
| ✅ | 文档多标签页（2026-10-05） | 预览区顶部 `DocTabBar`，**VS Code 双标签模型**（单击=预览可替换 / 双击=钉住常驻，全组至多一个预览槽）；`currentDocId` 仍是唯一真相源；**延后提交**让「取消未保存弹窗」天然等于什么都没发生（模式 #62）；右键菜单（关闭/其他/全部/固定）+ 中键关闭 + 溢出横向滚动 + 激活项滚入；**不做** Ctrl+P / 最近列表 / 拖拽排序 / 持久化。⚠️ 顺带修「简略模式双击完全没反应」（`replace_all` 只匹配到详细分支） |
| ✅ | 正文字号缩放（2026-10-05） | 补上**真实缺口**（vendor 硬写 `.markdown-body{font-size:16px}`，既有 `fontSizeScale` 对正文从未生效）；`Ctrl+滚轮` / `Ctrl+±` / `Ctrl+0`，只作用于 `.preview-area`；归入阅读版式族（`fontScale` 默认 1.0 = 零回归）；⚠️ px 非 em、版宽 `measurePxOf` 同源、滚轮走**原生** `passive:false`（模式 #63/#64）。⚠️ 键盘路径的 WebView2 宿主级加速键由 Rust 侧精准放行（`webview_keys`，模式 #65），**打包版生效与否待实测** |
| ✅ | 对话浮窗三条交互（2026-10-12） | ① **对话自己的**字号缩放（`lib/chat-font.ts` → `--chat-font-scale` → `.chat-msg-*`，**与阅读字号刻意不共用**但档位换算复用 `reading-prefs.nextFontScale`）；② **「回到最新」按钮**（上滚后右下角浮现，⚠️ 显隐与自动跟随**同源**判据 `<40px`）；③ **代码块一键复制**（`ChatPre`，hover/focus 显示，⚠️ 挂 `pre` 不挂 `code`、**不复用**产物侧 `CodeBlock`、流式中隐藏）。三条都**不进导出**；键盘 `Ctrl+±/0` 仍只调阅读字号 |
| ✅ | 产物章节卡片（2026-10-10；判据 2026-10-11 改由标记决定） | 骨架轴 `data-skeleton="card"`：**被 `card` 节修饰符标过的章节**渲染成一张卡，其余保持普通正文（**混排**，无外层容器）；8 套视觉方案（`styles/cards.css`，19 个 `--card-*` + 4 个 `--sh-*`），**默认 `gpt` = 「加重边线 + 与正文同底色」**（用户 2026-10-11 定），设置页每套带**迷你视觉小样**（`.card-preview`，与 `.md-card` 同一批变量）。⚠️ **默认 `paper` 零回归靠「选择器不匹配」而非「取值相同」**（paper 时两属性都 `removeAttribute`，cards.css 每条规则带 `[data-skeleton="card"]` 前缀）。切分器单一真相源 `markdown-scan.ts::scanHeadingLines`（围栏内 `## ` 不算标题）+ `markdown-sections.ts::splitByH2`（结果多一个 `card` 字段）；`CardSections.tsx` 每张卡各跑现有 `MarkdownPreview`；接入点只有 PreviewArea 的 notes/polished 只读态。**导出跟随**（HTML/Word/PDF，`skeleton`/`card_style`/`card_vars` 参数，PDF 自动跟随）。**逐维度微调（2026-10-11）**：底（基色）/ 抬升 / 边三根旋钮 —— 8 套方案**覆盖不到**的组合（抬升只有 0%/100% 两档、中间一整段为空）；`null` = 跟随方案（默认，零回归），切方案时清空；透明基色下抬升强制归零。⚠️ 详见 CHANGELOG 2026-10-11 |
| ✅ | 产物自证条（2026-10-11） | 预览区正文上方一横排**图标 + 短文本**小标签：`v{n}` / 生成时间 / 字数 / 模型（lucide `History`·`Clock`·`AlignLeft`·`Cpu`）。它是 `<!-- limo-meta … -->` 自证头在**读者侧**的等价物 —— 上一轮让注释不再漏进渲染后，信息也随之消失，这一条把它以低干扰形态接回来。⚠️ **只取四项、缺就不给**（`params`/`promptVersion`/`sourceRefs` 是给 agent 读的，铺给用户是噪音）；**刻意不含「已编辑」**（同屏工具条已用 warning 色承担，相邻两行说同一件事只是噪音）。⚠️ 版本行按 **`(version, mode)` 双键**取（notes/polished 各自从 v1 起，只按 version 会让「笔记 v2」显示成整理版 v2 的时间，**无报错**）。⚠️ **流式生成中不显示**（`markdownMeta` 还是上一版，配新正文是错位读数）。⚠️ 展示判据收口 [lib/artifactMeta.ts](frontend/src/lib/artifactMeta.ts)（`fmtTime`/`fmtChars` 从 `VersionSwitcher` 搬来，两处共用）；**不动后端**（字段早已从文件头现算派生）；**不参与导出**（视图装饰，同版本切换器） |

### 🛡️ 安全 & 运维

| 状态 | 功能 | 说明 |
|------|------|------|
| ✅ | SSRF 防护 | 14 个内网/保留 IP 段黑名单 + DNS 二次校验 + 协议白名单 |
| ✅ | 路径遍历防护 | `_safe_filename()` + `resolve().relative_to()` 双重校验 |
| ✅ | 敏感信息脱敏 | LLM Key / Cookie / Tavily Key 统一 `mask_sensitive` + `merge_sensitive` |
| ✅ | XSS 防护 | Markdown `<a>` 拒绝 `javascript:`/`data:` 协议 |
| ✅ | 并发控制 | `ExecutionGuard` 双层信号量（云端 5 + 本地 2），generate/process/retranscribe 已接入 |
| ✅ | 上下文窗口可配置 | `addModel()` API + ProviderSettings 展示 |
| ✅ | 日志全链路追踪 | `contextvars.ContextVar` 注入 `request_id`，前端日志面板 |
| ✅ | 旧格式错误迁移 | 19/19 个路由模块统一 AppError 体系（2026-10-11 复核无 `HTTPException` 残留） |

### 🧩 产物复用（2026-10-04 一期落地）

> 源头是三份 pi-docs 约定（密钥存储 / CLI 设计 / 产物复用），共同目标是**让产物能被 agent 复用**：不是没人能读产物，是**读的人不知道读了什么**。

| 状态 | 功能 | 说明 |
|------|------|------|
| ✅ | 产物自证头 | `notes`/`polished`/`process` 落盘首行 `<!-- limo-meta {json} -->`，回答「从哪来 / 怎么来的 / 是什么 / 可信度边界」；`core/transcript_meta.py` 拆出 envelope 层（`_parse_envelope` 只认第一行合法注释，`parse_meta` 与 `parse_artifact_meta` 各在其上做 schema 校验）；装配走 `generation/provenance.py::artifact_meta()` + `PROMPT_VERSION`。**普通 `transcript.md` 刻意不加**（会进 prompt）；**无头 ≠ 损坏**；编辑保存带回原头；**派生视图（思维导图）剥头**（模式 #57） |
| ✅ | 共享查询层 | `services/query.py::QueryService`（零 FastAPI import，CLI 可直接 import）收口全部读路径：分页 / ID 解析 / 版本编排 / meta 装配 / 截断标记。`core/dependencies.py::get_query_service` **惰性构造**（`main.py` 先建 Store 后建 SearchEngine，提前构造 = 静默空结果）。顺带修 `/api/api/search` 双前缀（**存活四个月**，模式 #56） |
| ✅ | 密钥与凭据卫生（二期，2026-10-04） | `.limo` 内零明文：**先去重**（同一 key 写多份才是根因）→ DPAPI 外移（`ctypes` 直调 `crypt32.dll`，零新依赖）→ 只留 `secrets_ref`；四张成对表收口 `config_io.py`；启动自检 `startup_check` + `scripts/check-secrets.py`；迁移/回滚 `scripts/migrate-secrets.py`（幂等 + 无明文备份）；顺带修 **4** 缺陷（`GET /api/config` 脱敏缺口 / `merge_sensitive` 死调用 / cookie 明文临时文件 / `llm_models` 类型守卫=启动即崩）；`.bak` **同生同灭**无需特殊处理；测试 50 条含 6 条对照实验 |
| ✅ | CLI + 契约文档（三期，2026-10-04） | Python 同仓 `backend/limo_cli/`（9 文件）；七命令 `status/list/search/get/export/folders/doctor`（**不做写操作**——成本/并发/幂等/误触发风险）。三层：CLI → `QueryService` → `Store`，**CLI 不解析库格式**（否则是第 6 个副本）。两个传输层（`HttpSource` / `LocalSource`）同契约。`--json` 时 **stdout 只有 JSON、日志全走 stderr**（**不用 TTY 检测**）；退出码 `0/2/3/4/5/6`（+130 SIGINT）区分「空」与「坏」，**按 `code` 判不按 HTTP 状态**；`Page` 上 `has_more`+`next_cursor`（能继续翻）与 `truncated`+`continuation`（到头但没给完）**并排且不混用**。契约文档两处：库内 `~/.limo/AGENTS.md`（`doctor --write-agents`，默认不覆盖）+ 仓库根 `AGENTS.md`（读者是改这条链的人）。门禁 `scripts/check-cli` 16 项（独立端口 7419，含 3 条负向对照）。**顺带修 3 缺陷**（模式 #59/#60/#61） |
| ❌ | 导出 meta 承载（四期） | md / html / docx **保留**、纯 txt **剥离**（注释在纯文本里是可见噪音）；docx 走 `core_properties`（零新依赖）；`[文档 i]` 用 meta 的 `sourceRefs` 解析回稳定 id |

### 🚀 规划中

> 合并自原 pi-docs/08-待做列表 + 09-设计提案（已归档删除）。需求驱动，不预建大教堂。

| 状态 | 功能 | 说明 |
|------|------|------|
| 🟡 | embedding 语义搜索 | **字面检索已重做并实测**（2026-10-26，29 题 41% → 83%）。**向量侧阶段 1–4 已全部落地**（同日：`EmbeddingEngine` 云端内置 + `vec.db` 三表 + 增量 worker + RRF 查询融合 + 设置页开关 + **本地引擎 `local-bge-small` 与引擎切换，外加 **2026-10-27 进程外插件版（打包版离线可用）****，**默认关**；见 [search-retrieval.md](docs/search-retrieval.md) §4.8 / §6.1）；评测侧 RRF 理论上限 97%。**仍未做**：真实库（131 篇）端到端实测（**2026-10-26 用户决定本轮先不做**，缺的是产品链路观测而非算法验证）、`eval_search.py --vector` 产品侧同轮对照、本地引擎的真实库**数值**验证（本轮只验契约与编排） |
| ❌ | 更多导入源 | RSS / 播客转录 / 学术论文(arXiv)，复用现有管线 |
| ❌ | 知识图谱 / 反向链接 | 文档间引用关系可视化，依赖 `#标签` 语法先落地 |
| ❌ | Markdown 渲染风格预设 | 多套 CSS 变量集（行高/间距/标题比例），与主题六轴正交 |
| ❌ | remark-tag 标签插件 | `#标签` 语法在笔记中可点击（纯前端） |
| ❌ | 组件/功能挂载插件系统 | 有第三方开发者时再做 |
| ❌ | 后台任务队列 | 批量任务性能遇瓶颈时（arq/Redis） |
| ❌ | Controller→Service→Repository 分层 | generate.py 继续膨胀时 |
| ❌ | UI 大重构 | VSCode 式面板，有明确多工作流需求时 |

### 📊 汇总

| 域 | 完成 | 总计 | 进度 |
|----|------|------|------|
| 📥 内容导入 | 8 | 8 | 100% |
| 📚 文档库 | 9 | 9 | 100% |
| 🔗 多篇合成 | 16 | 16 | 100% |
| 📝 转录 & 引擎 | 8 | 8 | 100% |
| ✨ LLM 生成 | 14 | 14 | 100% |
| 🔍 图片处理 | 4 | 4 | 100% |
| 💬 AI 对话 | 10 | 10 | 100% |
| 📊 图表生成 | 11 | 12 | 92% |
| 📤 导出 | 12 | 12 | 100% |
| 🎨 前端 UI/UX | 21 | 21 | 100% |
| 🛡️ 安全 & 运维 | 8 | 8 | 100% |
| 🧩 产物复用 | 4 | 5 | 80% |
| **合计** | **125** | **127** | **98%** |
