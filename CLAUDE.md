# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

LiMo — 知识管理系统。输入 URL/文本/文件 → 适配器提取正文 → LLM 生成结构化 Markdown。
设计哲学：**复杂性不是设计进去的，而是后续插件化长出来的**。

## 项目术语

下面这几个词在本仓库里有固定含义，按字面直觉理解会写错代码。它们在全仓出现上千次，
但此前没有一处定义，读到的人只能从上下文反推。改到相关模块前先对照这里。

- **判据**：一个能判定的条件。写出来之后，别人照着它应能得出同一个结论。
  `result.get("status") == "done"` 是判据；「结果是否合理」不是。文档里凡是写
  「判据是 X」，X 必须落到一个表达式或一个可观察的现象上。
- **同源**：两处必须用同一份实现，不能各写一份看起来一样的。判据写两份、常量抄两套、
  编排脚本复刻被编排的脚本，都属于同源问题。它的失效形态是不报错，只静默分叉。
- **落点**：改一个功能时必须同时改到的那几处位置。漏一处通常不报错，只是行为不一致，
  所以落点要成表列出（「导入与转录分开」那段列的九处就是一例）。
- **收口**：把散在几处的实现合并成唯一入口。与「同源」的分野：同源说的是「本来该统一
  的两处」，收口说的是「统一之后留下的那个入口」。
- **真相源**：多份副本里哪一份权威，其余必须从它生成。`index.json` 是缓存不是真相源；
  分类的真相源是 `{doc_dir}/meta.json` 与 `{root}/folders.json`。
- **静默失效**：不报错、测试也绿，但行为是错的。本仓库历次审计里绝大多数 P0/P1 都是
  这一类（正文写进对方文件、占位文本流进索引、依赖穿透导致重渲染）。所以「有没有报错」
  不能当作「有没有坏」的判据。

## 设计哲学

### 口子原则（Keep core lean）

**核心系统只保留轻量、稳定、可控的能力；一切重依赖、协议风险、难维护、法律风险的能力通过插件口子接入，官方零维护。**

- **判断「内置 vs 插件」四标准**：依赖重（打爆体积/难迭代）· 有协议问题（AGPL 污染核心）· 跨平台难维护（CPU/GPU/版本）· 有法律风险（平台 ToS/合规）→ **插件化**
- **已落地实例**：whisper（CUDA 依赖）、Tesseract（280M 便携版 + tessdata）、docling（torch 2GB 无法打包）移出核心；pymupdf4llm（AGPL）进程外插件；核心只留 pypdfium2 / markitdown / dashscope 等轻量稳定引擎
- **插件口子**：`~/.limo/plugins/` + EnginePlugin v1（默认进程外 + trusted 例外），官方只维护契约 + 加载 + 文档。**三族基类**：`EnginePlugin`（doc/ocr/asr，「一个文件 → 一段文本」）、`EmbeddingPlugin`（embedding，「一组文本 → 一组向量」，进程外是**常驻子进程** —— 2026-10-27 加）与 `RerankPlugin`（rerank，「一个查询 + 一组候选 → 一组 `[下标, 分数]`」，同样常驻 —— 2026-10-29 加）。**三族的共同实质是「重依赖不进打包」**，形状差别只是它们的契约差别；加一族时要**同时**改 `ENGINE_CATEGORIES` 与 `plugin_loader` 的适配器分派（只改前者 = 插件被以 doc 契约**静默注册成功**，见「引擎系统」rerank 段）

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

`warm` 暖灰（默认）/ `neutral` 中性白 / `cream` 奶油 / `cool` 冷灰 / `rose` 玫瑰 /
`sage` 鼠尾草 / `jade` 柔绿 / `amber` 琥珀 / `pearl` 珍珠白 / `mint` 薄荷 /
`sand` 沙色 / `lilac` 丁香 / `cloud` 云白 / `blush` 腮红。

⚠️ **这里刻意不列色值**。key 与中文名住 [lib/theme-base.ts](frontend/src/lib/theme-base.ts)
的 `BASE_OPTIONS`，实际色值住 [styles/theme-layer.css](frontend/src/styles/theme-layer.css)
的 `[data-base=…]` 块。本文档曾有一张 14 行的 hex 表（第三份副本），删掉它是因为**照抄色值
的文档改不动 CSS** —— CSS 调整后没人会回来改表，读文档的人就以为值变了。

⚠️ **别拿 `theme-layer.css` 的 `data-base=` 选择器条数当基调数**——它现在是 **14** 条（2026-10-16 起 `warm` 也显式写：它必须有**一条真规则命中**，否则设置页那张暖灰基调卡只能继承 `<html>` 的 `--tk-surface`，14 张里错 1 张）。它此前是 13 条（`warm` 走 `:root` 不写选择器，2026-10-11 实测踩到，「13 还是 14」正是这么分叉的）。唯一真相源是 [lib/theme-base.ts](frontend/src/lib/theme-base.ts) 的 `BASE_OPTIONS`（14 项）。同理 **`data-accent=` 是 26 条**（26 套全部显式写，无默认项）。

## 文档索引

> 下表只有 [CHANGELOG.md](docs/CHANGELOG.md) 与 [SECURITY_AUDIT.md](SECURITY_AUDIT.md) 是**时点文稿**
> （记录当时怎么想，不保证与当前代码一致）；其余均为**现状**，与代码不一致时改文档，
> 不要照着文档去迁就实现。

| 文档 | 内容 | 何时读 |
|------|------|--------|
| [README.md](README.md)（**面向用户**） | 产品介绍：能做什么 / 安装 / 首次使用 / 快捷键 | ⚠️ **改任何用户可见能力时必须同步**（它是用户的唯一入口）|
| [architecture.md](docs/architecture.md) | 六层分层架构 + 每层职责与陷阱 + 末尾「数据流与持久化」 | 理解模块边界；改持久化 |
| [pipelines.md](docs/pipelines.md) | 四条管道（生成 / 处理 / 对话 / 合成）+ 导入与转录分开 + 批量导入 + 合成管道 + 文档库与检索 + 生成闸门 + 智能分类 | 改任何一条管道；查「当初为什么这么设计」 |
| [search-retrieval.md](docs/search-retrieval.md) | 全文检索（三列分工 / 二字组 OR / 五个写点）/ 索引健康三态 / 向量检索 §6.1 / 增量机制 §4.4-4.6 / 重排 §九（默认关，四臂实验 §9.9 裁决不采纳） | 改检索、做向量化、查「为什么搜不到」、评估重排 |
| [doc-library.md](docs/doc-library.md) | 文档库：可嵌套文件夹 / 排列 ⊥ 排序 / 筛选 / 全文搜索 / 索引健康 / 语义检索 / 批量操作 / 智能分类 | 改文档库、文件夹、搜索框 |
| [chart-pipeline.md](docs/chart-pipeline.md) | 图表管道：fig 与 chart 双协议 / 十种布局算子 / 形状层 / 校验链 / PIL 自绘渲染器 | 改图表生成、布局算子、SVG 渲染 |
| [engines.md](docs/engines.md) | 五类引擎（ASR / OCR / Doc / Embedding / Rerank）+ `engine_bindings` 子场景 + 插件三族基类与分派 | 加引擎、改绑定、动插件契约 |
| [plugins.md](docs/plugins.md) | 插件标准：六个模板 + 三类基类（含常驻子进程 NDJSON 协议）+ 开发 API + `precheck` + GitHub 安装 + 安全声明 | 开发 / 安装 / 调试插件 |
| [frontend-patterns.md](docs/frontend-patterns.md) | 前端开发手册：状态管理 / 取消机制 / TaskPanel / 布局 / 主题轴 / `limo-block` / 十二种内容块 | 改前端任何东西；先读开头的「关键陷阱速查」 |
| [export-pipeline.md](docs/export-pipeline.md) | 导出架构 / 两条路径 / Tab→内容映射 / PDF 导出 / 打印 CSS / 卡片骨架跟随 / 新增格式落点清单 | 改导出、新增 tab 或格式、改打印样式 |
| [patterns.md](docs/patterns.md) | 关键模式 71 条：前端 #1-#8 / 后端 #9-#31 / 同源与成对判据 #32-#66 / CSS 宿主陷阱 #67-#71 | **写任何模块之前**；查「这个坑踩过没有」 |
| [constants-home.md](docs/constants-home.md) | 公共常量与判据的「家」：60 余处唯一真相源逐条列表 | **写新代码前先查**；表里有的 import，不复制 |
| [roadmap.md](docs/roadmap.md) | 方向与进度：按域分组的 ✅/🟡/❌ 清单 + 「规划中」的想法池 | 查「这个功能做了没有」「有没有人想过要做」。**不是规则文件** |
| [TODO.md](docs/TODO.md) | 短期待办：已决定要做、做法清楚的几条，做完即删 | 开工前看「眼下该做什么」。⚠️ 与 roadmap **不重叠**，同一条只住一处 |
| [BUILD.md](docs/BUILD.md) | PyInstaller + Tauri 完整打包流程、`hiddenimports` 清单、常见陷阱 | 打包 / 发布 |
| [desktop-shell.md](docs/desktop-shell.md) | 桌面壳启动链路 / 就绪探测 / 启动失败屏 / 进程管理 / 加速键放行 / CORS | 改启动链路、失败屏、Tauri Rust 侧 |
| [AGENTS.md](AGENTS.md)（仓库根） | CLI + MCP 两条链：三层结构 / 改 CLI 的六条硬约束 / MCP 五节 / 门禁。⚠️ 与库内 `~/.limo/AGENTS.md` 读者不同 | 改 `limo_cli` / `mcp_server.py` / `/api/cli/*` / `QueryService` |
| [对话协议.md](docs/对话协议.md) | 对话 SSE 事件协议 + 会话 CRUD API + 前端映射 | 改对话流式 / 会话管理 |
| [prompts-inventory.md](docs/prompts-inventory.md) | 全部 LLM 提示词清单 + 审视点 + 温度参数速查 + PromptComposer 标签 + Agent SSE | 审视 / 优化 prompt |
| [AGENT-ONBOARDING.md](docs/AGENT-ONBOARDING.md) | 上手教程：从零跑通 → 架构 → 注册表 → 数据流 → 打包 → 门禁 → 调试 | 新 agent 快速上手 |
| [user-scenarios.md](docs/user-scenarios.md) | 10 个用户场景及实现状态 | 理解产品需求 |
| [qianwen-models.md](docs/qianwen-models.md) | 千问平台模型速查（文本 / OCR / ASR），含价格 | 配模型 / 选引擎 |
| [cards.md](design/cards.md) | 卡片视觉语言参考（19 种）+ 令牌 + 视觉方案轴 + 按 LiMo 现状分桶。⚠️ **素材库不是实施计划** | 做卡片形态、改 `styles/cards.css` |
| [components.md](design/components.md) | Apple HIG 组件规格：布局 / 颜色 / 字体 | 改 UI 组件 / 设计系统 |
| [STATUS.md](docs/STATUS.md) | 纯事实清单（文件 / 端点 / 引擎计数 / 代码行数） | 需要统计数据 |
| [ACKNOWLEDGMENTS.md](docs/ACKNOWLEDGMENTS.md) | 开源致谢与合规声明 | 增删依赖、借鉴开源项目时 |
| [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)（仓库根） | 发行包内嵌组件的许可证清单（面向发行合规） | 打包 / 发布、增删内嵌依赖 |
| [CHANGELOG.md](docs/CHANGELOG.md) | **时点文稿**：2026-11-07 之前的存档，此后由发版流程生成 | 查「当初为什么这么定」 |
| [SECURITY_AUDIT.md](SECURITY_AUDIT.md)（仓库根） | **时点文稿**：2026-07-08 起的安全审计，逐条标 ✅ 已修 | 排查某类漏洞「是不是早就查过」 |

> ⚠️ **README 与代码的一致性**：曾有两处滞后——① 「同模式保留最近两版」（实际早已改为全部保留不设上限）；② 「九种」图型（当时 prompt 只暴露七种）。两处都**读起来毫无异常**，是「文档骗人」最典型的形态。**改任何用户可见能力时回头看一眼 README**。

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
| 3 | 文档同步 | `python scripts/changelog-build.py --write` 生成 `docs/changelog/releases/v{VERSION}.md` **并改写成用户视角**（四条改写规则见 [CHANGELOG.md](docs/CHANGELOG.md) 头部）；STATUS 版本号已同步 |
| 4 | Tag + Notes + 校验 | 打 `v{VERSION}` tag；Release notes 取自第 3 条那份文件；附安装包 SHA256 校验和 |

### 发布动作

用户提出发布后 → **先展示门禁清单**（版本/验证/文档/tag 状态）→ 用户确认 → 才执行：
`git tag v{VERSION}` → `gh release create v{VERSION}`（notes 取自 `docs/changelog/releases/v{VERSION}.md`）→ 上传 NSIS/MSI 安装包 + SHA256。

## 提交纪律（2026-11-07）

一次开发会话通常同时产出几个不相干的小改动。这节的唯一目的：三个月后能回答
「这个行为是哪次提交引入的」，被中断时能从 `git log` 接着做，而不是从工作区猜。

⚠️ **2026-11-07 起 commit message 的分量变了**：不再逐次手写 CHANGELOG，提交信息成了
日常变更的**唯一记录**。写「修了什么」不够，要写清**为什么改、改成了什么、影响面多大**。
下面是这个分工的完整形态。

### 提交信息写什么

格式：`type(scope): 一句话说清改了什么`。type 取 `feat` / `fix` / `refactor` / `docs` /
`perf` / `test` / `chore` 之一 —— `scripts/changelog-build.py` 按它分组，乱写会归进「未分类」。

正文按需写三段，缺哪段取决于这个提交会不会被人回头追问：

| 段 | 写什么 | 什么时候必须写 |
|---|---|---|
| 为什么 | 原来的行为是什么、为什么不对。带上具体表现（「界面上没有任何报错」「切回来还是旧内容」），不要写「不够健壮」这类判断 | 除纯文案与版本号外，一律写 |
| 改成了什么 | 动了几处、每处做什么。多处同源时点名它们**必须一起改**的理由 | 改动跨 2 个文件以上时 |
| 影响面 | 正常路径是否逐字不变、哪些旧数据/旧调用方受影响 | 改了已有行为的默认值时必写 |

⚠️ **`fix` 提交必须注明引入问题的提交短哈希**（`introduced in a1b2c3d`）。没有它，
下一个看到这行代码的人只能猜「这里为什么这么写」，而 `git log -L` 翻出来的是一整段
无关的历史。找不到引入提交就写「引入时间不明」——**明说不知道，比留空好**。

⚠️ **破坏性变更写在 `type!:` 里**（`feat!: 移除 xx 参数`），脚本会标 ⚠️。这只影响
提示的显眼程度，真正要做的还是在正文写清「从什么变成什么」。

### 拆分判据：提交信息里有没有「顺带」

写完提交信息，如果主要描述需要写「顺带」「另外」「同时」，就该拆。反过来，拆到只剩
一行文案改动时就并进相邻提交，不为凑数量单独提交。

拆提交有一条硬前提：**每个提交都能独立编译**。它决定拆分顺序——`useDocQuery` 的参数
是必填时，「加筛选」那个提交必须排在「批量队列」之前，反过来的中间态 `tsc` 过不了。
一个文件同时承载两个主题时允许先提交一个、把另一个临时摘掉，但摘掉的部分必须在本轮
内恢复（2026-11-07 拆 `DocLibrary.tsx` 就是这么做的）。

### 文档与代码同批，不单独攒

README 写的是同一次行为变化。代码先提交、README 后补，留下的是「代码已改、文档还写着
旧行为」的中间态——本文件「README 与代码的一致性」段里那两条历史滞后就是这么来的。

⚠️ **CHANGELOG 不在此列**（2026-11-07 起）：它是发版时统一生成的，改代码时不必动它。
但**提交信息要承担它原本承担的信息量** —— 这正是上一节存在的原因。

只有纯文档改动才单独做一次 `docs` 提交。

### 改动不过夜

做完一个主题就跑该域门禁并提交；跨主题的改动没做完就停下来商量，不在工作区攒两三天
的混合改动。这条对 agent 尤其重要：新会话的第一件事是读 `git status` 再判断每处改动
属于哪个主题，工作区越混，判断越不可靠。

### 自动检查：`.githooks/pre-commit` → `scripts/commit-gate`

每台机器装一次（钩子本体随仓库分发，`core.hooksPath` 是本地配置，不随 clone 生效）：

```bash
git config core.hooksPath .githooks
```

它按**已暂存的文件**决定跑什么，只做「让这个提交站得住」的最低成本检查：

| 暂存了 | 自动跑 | 耗时 |
|---|---|---|
| `frontend/**` | `scripts/check-all quick`（tsc -b + oxlint） | 约 7s |
| `backend/**/*.py` | `compileall` 语法检查（只编译暂存的那几个） | 毫秒级 |
| 任何 `*.md` | `scripts/check-all docs`（「第 N 节」交叉引用） | 约 0.2s |

另外打印两条提示（不拦截）：代码改动跨了前端 / 后端（确认是不是同一主题）、
提交正文是空的（读 `COMMIT_EDITMSG`，拿不到就跳过）。

⚠️ **它不跑 vitest / pytest / check-cli**。那三层是完整域门禁，一次几十秒，逐提交跑
会把提交本身变成负担；而它们能抓到的行为回归与静默失效，本来也不该等到「提交这一下」
才被发现。完整门禁的时机是：一个主题做完、打包前、改到 CLI / MCP 这类有契约测试的
地方。跳过：`git commit --no-verify` 或 `LIMO_SKIP_COMMIT_GATE=1 git commit ...`。

⚠️ **两条纪律钩子管不了**：一个提交是不是只有一个主题、改动有没有过夜。它们属于判断，
不属于检查。

## 常用命令

### 门禁（提交前跑这个）

```bash
bash scripts/check-all            # 全部六项（后端 53s / 1688 用例，前端 21s / 1481 用例，2026-11-07 实测）
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

**什么时候跑哪一层**（2026-11-07 补）。分层的意义是缩小范围，不是攒一个绿色的数字：

- 开发过程中只跑本次改动影响到的**最小相关测试**，不要每改一步就重跑同一组。
- 全量门禁在**准备提交**时跑一次。跨模块、公共类型、持久化、启动链路、构建配置这类
  高风险改动，扩大到能覆盖本次风险的范围即可。
- 不为「安心」跑无关测试。
- 反向也成立：「不跑无关测试」不等于跳过必要验证。提交前，本次改到的路径必须全绿。

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

# 运行测试：全量走 `bash scripts/check-all backend`；单跑某个文件直接
# `pytest tests/test_xxx.py`（文件名与 docstring 写明用途，共 119 个文件 / 1688 用例）
# ⚠️ 带 `pytest` 标记的四个（2026-10-06 起）必须用 pytest 调用：它们原先顶层是模块级
# 裸调用，`python tests/x.py` 能跑，但 `pytest tests/` 扫过去**一片绿而根本不产生用例**（假绿）
pytest tests/test_adapters.py        # 适配器注册 + 路由
pytest tests/test_engines.py         # 引擎注册 + 查找
pytest tests/test_plugin_loader.py   # 插件加载器
pytest tests/test_epub_pipeline.py   # EPUB 一键解读

python tests/test_e2e_smoke.py       # 端到端冒烟（36 断言，需后端已启动）
                                     # ⚠️ 已收进 main() 且被 conftest.py::collect_ignore 排除，
                                     # pytest 不会收集它（此前会被收集并静默起后端 + 污染真实配置）
```

### 前端（React/TypeScript/Vite）

```bash
cd frontend
npm run dev       # 开发服务器，端口 5173，API 代理 → 127.0.0.1:7413
npm test          # vitest（102 文件 / 1481 用例，2026-11-07 实测 21s）
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

> **正文见 [docs/BUILD.md](docs/BUILD.md)**（2026-11-07 迁入）。那里有完整的
> PyInstaller → 重命名 exe → 复制 ffmpeg → Tauri 四步流程、`hiddenimports` 清单、
> spec 中已固化的配置（certifi / OpenSSL DLL / KaTeX 字体 / searxng），以及常见陷阱。

三条最常踩的：**必须在项目根执行 Python 且必须带 `--distpath`**；
**`--noconfirm` 会删掉手动复制的 ffmpeg 与带后缀 exe**（每次都要重做）；
**新增依赖后要同步 spec 的 `hiddenimports` 与 `collect_all`**。

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

> **正文已外移到 [docs/pipelines.md](docs/pipelines.md)**（2026-11-07）。那里有：四条管道的
> 完整数据流、双产物并行、产物版本归档、合成管道（Step 0 分叉 / 预算 / 超窗分层 / 维度 /
> 原料 / 证据块）、文档库与检索、生成闸门、智能分类。**改任何一条管道先读它。**

一份 transcript → 三条下游；合成管道是 N 份 → 一份的**回流**，产物再走一遍生成管道。

```
输入 → Adapter.adapt() → RawTranscript → 存 transcript
                    ├─→ LLM Generator（generate_both → MarkdownView → 版本归档）
                    ├─→ LLM image_process（优先 → Engine 回退）
                    └─→ LLM Chat（AgentRunner → conversations/）
```

四条会**在别的模块被违反**的约束：

- **双产物并行时 `asyncio.gather` 的返回顺序 = 任务创建顺序**，解包变量名必须同序。
  曾解包反了 —— notes 与 polished 的内容互换写进对方文件，两边都「成功」。
- **版本号 `max` 必须扫磁盘取，且与写盘同处一把锁**。分开会让并发生成算出同一个 N，
  两份产物落到同一个 `v{N}.md`。
- **普通 `transcript.md` 刻意不写自证头** —— 它会进模型 Prompt，加头等于改生成输入。
  产物（notes / polished / process）才写，且编辑保存必须**带回原头**。
- **`_single_product = source_type in ("composite", "xlsx")` 不能动。** composite 只落 notes 槽：
  它的 rawText 已是定稿输入，而 `build_polish_prompt` 对它返回的是**另一份综述** ——
  开放 polished 等于同一功能两个入口。

### 文档库与图表管道

> **正文已外移到 [docs/doc-library.md](docs/doc-library.md) 与 [docs/chart-pipeline.md](docs/chart-pipeline.md)**
> （2026-11-07）。前者：可嵌套文件夹 / 排列 ⊥ 排序 / 筛选区 / 全文搜索 / 索引健康 / 语义检索 /
> 选择与批量操作 / 智能分类。后者：fig 与 chart 双协议 / 十种布局算子 / 校验链 / PIL 自绘渲染器。

三处会**在别的模块被违反**的约束：

- **文件夹是纯逻辑分类，永不映射为磁盘目录。** 每篇文档已有独立目录（含 `assets` 相对引用与
  版本归档产物），把文件夹做成目录会同时破坏三样东西。⚠️ **前端不做路径解析** ——
  `parse_path` / `path_key` 只活在 [folder_tree.py](backend/app/storage/folder_tree.py) 一处，
  建夹一律走 `POST /api/folders/ensure-path`（全有或全无）。前端自己拆 `/` 会让建夹不再是
  全有或全无，且会拆开历史里本名就含 `/` 的夹。
- **全文搜索在客户端不可能实现。** `docs` 数组**没有正文**（白名单序列化器只存元数据），
  FTS5 是唯一可行解，不是「先这么写以后优化」。
- **`get_markdown_block_format`（无条件注入）与 `get_chart_format`（勾选才注入）是成对的。**
  同篇里两者会同时出现，改一侧必须改另一侧，否则一边说「画图」一边说「别画」。
  ⚠️ `fig` 与 `chart` 的失败语义**刻意不同**：chart 内非 SVG 原样保留（可能是协议外内容），
  fig 是毫无歧义的协议标记，解析失败**必须零残留替换**为失败文本。

### 适配器注册

`app/adapters/__init__.py` → `AdapterRegistry.register()`。新增管道只需实现 `accept()` + `adapt()` 并注册即生效。注册顺序决定路由优先级：Video（含 BilibiliSubtitle / YouTubeSubtitle 字幕优先提取）→ **Arxiv** → Document → Image → Webpage → Text（兜底）。共 **6** 个适配器。

- **arXiv（2026-10-29）**：`accept()` 同步纯正则、**不发网络请求**（`^https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/`）；`adapt()` 抽 id → 构造 `https://arxiv.org/pdf/{id}` → `safe_get` → 临时文件 → **委托 `DocumentAdapter`**（零复制引擎链）。⚠️ **必须注册在 Document 之前** —— `/pdf/2401.12345v1.pdf` 这种带扩展名的形态会被 DocumentAdapter 按扩展名抢走。
- **RSS / Atom（2026-10-29）走 WebpageAdapter 的内部分支，不新建适配器**：新适配器的 `accept()` 与 Webpage 必然打架（同一 URL 两个都返 True，结果取决于注册顺序）；内部分支**免费继承** SSRF 防护 / 逐跳重定向校验 / 编码处理。判据是 `Content-Type` 含 xml **或** 前 2048 字节含 `<rss` / `<feed` / `<rdf:rdf` → 交给 `feedparser.parse()`，**真判据是出不出 `entries`**（出不来就回退普通网页提取）。N 条（上限 `RSS_MAX_ENTRIES = 20`）**合并成一篇** Markdown（`# feed 标题` + 每条的 `## 条目标题` / 链接 / 摘要）。⚠️ 为此 `_download()` 的签名从 `-> str` 改成 `-> tuple[str, dict]` —— 原先响应头在闭包里就丢了；**主路径与回退路径两处 return 都要改**。

### 其他注册表

同类插件化模式还用在两处：
- **ProductRegistry**（`app/generation/product_registry.py`）— 产物注册表，定义 notes/polished 及其依赖拓扑。新增产物类型只需加一个 `ProductDef` 即可，无需改分发逻辑或前端 UI。
- **ToolRegistry**（`app/agent/tool_registry.py`）— Agent 工具注册中心，替代硬编码的 `_register_chat_tools()`。每个工具是独立 `ToolDef`，注册后自动被发现和调用。

FastAPI 依赖注入（`app/core/dependencies.py`）提供 `get_store` / `get_config` / `get_guard` / `get_config_lock` / `get_query_service` / `get_search_engine` 依赖，消除路由函数中 `request.app.state.xxx` 样板代码。**19 个路由模块中 18 个用 `Depends`**（`references.py` 两者都没用——它只有进程内暂存，不碰 store/config，本来就不需要）；仍有 `request.app.state` 残留的 **3 个**：`generate.py`（7 处）、`ingest.py`、`markdown_edit.py`（1 处，`update_index` 的 `getattr(..., None)` 兜底，那个 `None` 是有意的：检索坏了不该让编辑保存失败）。⚠️ 计数以 `ls backend/app/routers/*.py` + `grep -l "app\.state"` 的输出为准（**该 `ls` 把 `__init__.py` 也算进去**，故文件数 = 模块数 + 1；2026-10-26 实测 **20 文件 = 19 模块 + 1**，`grep -l "Depends("` 命中 18 个，`app.state` 残留 3 个）。⚠️ `search.py` 2026-10-26 已迁（新增的两个端点用 `get_search_engine`）—— 当时它那份**只出现在注释里**，而 grep 认不出注释，所以「按 grep 计数」这条纪律遇到「注释里讨论这个属性」时会假阳性：改的是措辞，不是判据。此前写的「15 个里 12 个已迁移、ingest 已迁移」也是同一类错误（模式 #54：有几处同源是搜索题）。

### 只读命令行（`limo`）与 MCP 服务端

> **正文见仓库根 [AGENTS.md](AGENTS.md)**（250 行，读者是改这条链的人）。
> 那里有：三层结构 / 七个命令与只读纪律 / `--json` 输出契约与退出码 / 改 CLI 的六条硬约束 /
> `--all` 为什么两个 Source 写法不同 / **MCP 五节**（stdout 是协议通道 / `supportedVersions`
> 只列核实过的版本 / `Store` 快照问题只在直开那一路 / 工具集的形状 / 两条已知效率边界）/
> 门禁怎么跑。⚠️ 与库内 `~/.limo/AGENTS.md`（`limo doctor --write-agents` 生成）
> **读者不同、内容不重叠**，别把两者混。

留在本文件的只有一条，因为它在**任何模块**都可能被违反：

- **CLI 与 MCP 都不解析库格式**（不读 `index.json`、不读 `search.db`、不拼磁盘路径）。
  取数一律经 `QueryService`。少这条纪律，它们就是格式的第 N 个副本，而副本的漂移是
  静默的 —— 格式演进时漏改一处，它读错而不报错。同理，给它们加能力**不等于**加一个
  实现：`Source` 契约的两个实现（`HttpSource` / `LocalSource`）已有两个消费者。

### 引擎系统

> **正文见 [docs/engines.md](docs/engines.md)**（2026-11-07 外移）。那里有：ASR/OCR/Doc/
> Embedding/Rerank 五类引擎各自的实现与取舍、`engine_bindings` 子场景拆分、插件三族基类
> 与注册分派。**加引擎、改绑定、动插件契约先读它。**

留在本文件里的只有一条，因为它在**任何模块**都可能被违反：

- **加一个插件族 = 同时改 `ENGINE_CATEGORIES` 与 `plugin_loader.py` 的 `_RESIDENT_ADAPTERS`。**
  只改前者时插件会被以 **doc 契约静默注册成功** —— 形状合法、零报错，而每次调用都在要求
  一个它永远收不到的文件路径。

### 模型管理

Provider → EnabledModel（能力标签）→ SceneModels（`generate` / `chat` / `image_process`）。`resolve_model()`（`app/core/model_resolver.py`）是唯一模型解析入口，generate/chat/process 统一调用。多模态能力通过 `_PROVIDER_IMAGE_RULES` 按厂商 include/exclude 规则自动检测。**内置厂商 OpenCode Go（2026-08-17）**：OpenAI 兼容网关（**`https://opencode.ai/zen/go/v1`**，chat/completions，`OPENCODE_API_KEY`，Go 订阅/ Zen 按量共用 key）——`PROVIDER_DEFAULTS` 注册 + `llm_models.opencode` 预置 chat-completions 系模型（GPT/Grok 走 Responses API 不互通不预置），Qwen/GLM 高版本标 image 供图片处理/OCR。⚠️ **两条已订正的历史结论**（2026-10-02）：① base_url 原写 `opencode.ai/go/v1` → 实测 **404**，正确端点为 `zen/go/v1`；② 「该网关不实现 `GET /v1/models`」**已证伪**——实测返回 200 且免 key（标准 OpenAI 列表格式，不含 `context_length`），故模型刷新对它是**可用**的，不需要 404 兜底那条路（该分支保留给真正不实现它的自建网关）。`test_connection` 对 404/405 仍用最小 chat 请求兜底验证。

**`provider.base_url` 归一化统一走 `app/core/url_utils.py::api_root()`**（2026-10-02）——判据是「**路径是否已含版本段**」而非「是否以 `/v1` 结尾」，因为后者对两家不成立：Google 兼容层 `.../v1beta/openai`（**必须是这个形态**，缺 `/openai` 会打到不存在的原生路径 → 整条 LLM 链路 404）与智谱 `.../api/paas/v4`。⚠️ 该判据此前在 `routers/model.py`（两处）+ `routers/config.py`（一处）**各有副本**，已收敛为唯一入口 + AST 源码守卫（见关键模式 #54）。⚠️ 新增内置 provider 时**不要**再写 `endswith("/v1")` 拼接。
### 前端布局与关键模式

> **正文见 [docs/frontend-patterns.md](docs/frontend-patterns.md)**（437 行）。§0-§8 是状态管理与
> 任务生命周期，§9 是布局（活动栏 / 三栏插槽 / 文档标签栏 / AI 浮窗），§10 是主题轴、
> 语义标记 `limo-block` 与十二种内容块。**改前端先读它。**

五条会**在别的模块被违反**的约束：

- **折叠阅读的档位优先于骨架轴**（`topic` 自带 `data-skeleton="card"`，不看用户选的 paper/card），
  且**未展开时不把 Markdown 交给渲染器** —— 缩过的 Markdown 会散表格、废围栏、让单行图片凭空成图。
  展开的那一节走 `MarkerContent`。档位是持久化的，短文 / 流式 / 编辑态**一律回落 `full`**。
- **左侧面板互斥靠 `uiSlice.activeLeftPanel` 这个选择器字段**，不是「打开甲时遍历同侧把乙收起」
  —— 后者可被任何直接改单个 slot 的调用点绕过，而这样的调用点有四处在 CLAUDE.md 视野之外。
  统一入口 `openPanel` / `closePanel` / `togglePanel`。
- **语义标记的接入点只有两处**（PreviewArea 的 paper 分支 + `CardSections` 每张卡的 body），
  **绝不放进 `MarkdownPreview` 内部** —— 那会静默扩散到 Excel 数据摘要与原始转录。
- **六轴的 CSS 数据只有一份**（[styles/theme-layer.css](frontend/src/styles/theme-layer.css)），
  前端 `@import` 与**后端导出读同一份**。JS 侧只存 key 与 label，一个色值都不存。
- **`limo-block` 协议前后端各一份实现**（`core/artifact_blocks.py` ↔ `lib/markdown-markers.ts`），
  靠共享 fixture `limo-blocks.json` 钉住。新增 type 时必须同步补它的 `decor=off` 归零规则 ——
  漏了不报错，只是那一种块永远带着装饰。

### Tauri 桌面壳

> **正文见 [docs/desktop-shell.md](docs/desktop-shell.md)**（2026-11-07 外移）。那里有启动链路、
> 就绪探测、启动失败可见化（三个事件 / `StartupFailure` 形状 / 日志回退链）、进程管理、
> 宿主加速键放行、CORS 白名单。

留在本文件的是三条，因为它们在**别的模块**也会被违反：

- **前端不自轮询 `/health`**：以 Rust 的 `backend-ready` 事件为主要唤醒源，静默期 2.5s +
  单次探测 + 50s 硬兜底。两条路并存时，WebView 里失败请求的报错 JS 抑制不掉（模式 #49）。
- **启动失败一律停在失败屏**，放行由用户点「仍然继续」。判据是「有没有诊断」，不是 `alive`。
- **CORS 只回显白名单 origin**，生产 WebView2 的实际 origin 是 `http://tauri.localhost`，
  白名单必须含它。

## 关键设计决策

### 公共常量与判据的「家」（写新代码前先查，别复制）

**纪律：默认抽公共，复制要能说出理由。**

⚠️ **写新代码前，先打开 [docs/constants-home.md](docs/constants-home.md) 查一遍。** 那张表把
60 余处「同一份东西真正住在哪」逐条列出（模型能力位 / 资产路径正则 / provider 归一 / 检索
融合编排 / 向量索引算子 / 插件三族契约 / 主题六轴……）。**表里有的，一律 import，不复制。**

⚠️ 两条贯穿全仓的特例：**「后端注册了什么，前端就列什么」的集合一律经 capabilities 下发**
（前端硬编码一份 = 后端加了引擎而前端静默不显示）；**纯前端内部**的共享常量放 `lib/`，
不要经 capabilities（那是把一个实现细节变成对外 API）。

### 日志等级与用法

后端日志按等级过滤和排序，等级用错等于没有等级。2026-11-07 实测分布：`log.warning` 110 处、
`log.info` 73 处、`log.debug` 4 处、`log.error` **1 处**。warning 比 info 还多，说明它已经
退化成了「不是致命错误的统统算 warning」——读日志时没法按等级把真该看的挑出来。

| 等级 | 什么时候用 |
|---|---|
| `error` | 操作失败、非预期异常。正常路径上出现 error，说明要么这里真坏了，要么这条该降级 |
| `warning` | 降级、兜底、跳过、重试。**不是**「任何不理想的情况」 |
| `info` | 正常流程的节点、状态切换、用户可见的行为变化 |
| `debug` | 排查细节、变量值、状态快照。高频循环一律走这里，不要每条都打 info |

三条配套约定：**状态词统一用「开始 / 完成 / 跳过 / 失败」**，不要 `status=success` 这类混写；
**异常日志必须带异常信息**（`str(e)` 或 traceback，只写「失败」等于没记）；**高频循环只打聚合**
（成功 N 条 / 失败 M 条），明细留 debug。

### 数据流与持久化

> **正文已并入 [docs/architecture.md](docs/architecture.md) 末尾的「数据流与持久化」一节**
> （2026-11-07）。那里有：索引重建的无损化与 sidecar 真相源、文档 id 唯一不变量、回收站的
> 四条约束、配置与凭据的唯一入口、读路径收口。

三条会**在别的模块被违反**的约束：

- **`index.json` 是缓存，不是真相。** 分类真相住 `{doc_dir}/meta.json` 与 `{root}/folders.json`；
  重建时的恢复主键是 `dir_name` 而不是文档 id（id 由正文重推、必然变）。
- **回收站恢复绝不能走 `_rebuild_index`** —— 必须按 `trash-meta.json` 原样写回 index。
- **凭据唯一入口是 `save_config()`**，且它的 `elif disk.get(SECRETS_REF_KEY)` 分支要求入参是
  **回填过的内存配置**；拿磁盘 YAML 直调会让密钥库被无声删除。

### 导入与去重
- **唯一导入入口**：GenPanel 是唯一导入入口，DocLibrary 只负责搜索/筛选/列表
- **两层去重**：URL 去重（`find_by_url`）在请求到达时拦截；内容去重（SHA256 contentHash）在提取正文后拦截。Store 级拒绝写入
- **导入区纯粹化**：ImportSection 不受 currentDoc 影响，按钮永远是「导入」

### 导入、转录与批量导入

> **正文见 [docs/pipelines.md](docs/pipelines.md)** 的「导入与转录分开」「批量导入」两节
> （2026-11-07 外移）。那里有：两档默认值（媒体只收纳 / 文档一键）、`transcribeState` 的
> 九处落点、`probe` 钩子、SSE 与非流式共用的 `_run_adapter` / `_index_if_persisted`、
> 批量导入的串行队列与取消三出口。

三条会**在别的模块被违反**的约束：

- **`ready` / `transcribeState` / `_pending_ids` 三个同名但正交，绝不能合并。** 合并之后
  要么 pending 文档被幽灵清理扫掉，要么占位条目漏进列表。
- **`status` 不能改成 `"stored"`**：索引闸门判据是 `status == "done"`，改了会让这篇不进全文
  索引，而两条路径都报成功。
- **分叉点必须在 `adapt()` 之前且只有一份实现**（`_run_adapter`）。两个调用点逐字同构，
  只改一处 = 用户走另一条路行为完全不同，而两条路都不报错。


### 生成与处理

> **正文见 [docs/pipelines.md](docs/pipelines.md)** 的「生成与处理」一节（2026-11-07 外移）。
> 那里有双产物并行、思考链、图片双轨、目标语言、LLM 续写、参考文件（暂存 / 超窗压缩 /
> 名字清洗 / 注入防御）、风格隔离、Agent SSE 事件流。

四条会**在别的模块被违反**的约束：

- **`asyncio.gather` 的返回顺序 = 任务创建顺序**，解包变量名必须同序。曾解包反了 ——
  notes 与 polished 的内容互换写进对方文件，两边都「成功」。
- **版本号 `max` 必须扫磁盘取，且与写盘同处一把锁。** 分开会让并发生成算出同一个 N。
- **参考文件只在 notes 侧注入**，polished 不注入（压缩是白花钱）。
- **思考模式下 temperature / presence_penalty / frequency_penalty 不生效**，不报错，静默失效。

### 并发与安全
- **并发控制**：`ExecutionGuard` 双层信号量 — `cloud_semaphore(5)` 给生成/处理/云端引擎，`local_semaphore(2)` 给本地 CPU 引擎（文档解析等）。本地慢操作不抢占云端快操作的槽位。
- **并发安全**：Store `threading.Lock` 防 RMW 竞态；engine `copy.copy()` 防 api_key 串用
- **安全加固**：path traversal 防护、上传 2GB 预检、SSRF 防护（`url_security` 共享校验，5 个下载入口全部加固）

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

> **正文已外移到 [docs/roadmap.md](docs/roadmap.md)**（2026-11-07）。
>
> 分割判据只有一条：**这张表是进度快照，不是规则**。它按域列出「做了什么、还差什么」，
> 条目随开发推进持续增加，而它对「这段代码该怎么写」没有约束力 —— 那条约束在
> 「关键设计决策」与 [docs/patterns.md](docs/patterns.md) 里。
> 留在本文件里，等于让每次会话的固定开销跟着功能清单一起长。
>
> 查「这个功能做了没有」→ 读 roadmap；查「这个坑踩过没有」→ 读 patterns。
