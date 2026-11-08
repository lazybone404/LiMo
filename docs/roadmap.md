# 路线图

> 本文是 [CLAUDE.md](../CLAUDE.md) 里「待办事项」段的正文，2026-11-07 外移。
> 它是一张按域分组的进度表，改动频率高于规则类内容，留在 CLAUDE.md 里会让
> 每次会话的固定开销随条目增长。**规则与踩坑不在本文**，那些在 CLAUDE.md。

> ⚠️ **本文件只回答「这个方向做到哪了」与「有没有人想过要做」。** 已经决定要做、
> 做法清楚的短期任务在 [TODO.md](TODO.md)，不在这里 —— 想法按季度记，待办按周记，
> 混在一起写两边都读不出重点。

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
| ✅ | 生成闸门 | composite → 不产 polished / **格式清单照收**（允许哪些由 `AVAILABLE_OPTIONS["composite"]` 判，2026-11-07）/ 跳过 `_auto_title`；`metadataCorrupt` 推 warning 不阻断 |
| ✅ | 前端入口 | BatchBar 合成按钮（≥2 篇）+ 确认对话框（已选清单/标题/对比指引 + **逐篇 token/处理方式 + 原料单选 + 维度列表**）；DocRow「N 合 1」徽章；「合成」筛选项；tab 只注册 summary/transcript + `defaultFor`。**2026-10-29 加简单模式**：装得下时配置区默认收起，只剩标题 + 「合并」按钮；`map_reduce`/`reject` 强制展开（详见合成管道段） |
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
| ✅ | 插件系统 | EnginePlugin v1 进程外（默认）/trusted 例外 + 一键安装依赖（镜像源）+ 三方插件发布（pymupdf4llm）。**官方模板四份**（[examples/](../backend/plugins/examples/README.md)）：pymupdf4llm PDF / **faster-whisper 本地语音转写** / tesseract 本地 OCR / trusted 进程内；每份都带 `precheck` 就绪自检（**进程外 `probe_venv`、进程内 `probe`**，选错方向 = 装好了却显示不可用） |

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
| ✅ | 5 个工具 | search_docs / lookup_doc / list_docs / search_web / fetch_url。⚠️ `search_docs` 2026-10-29 起与 `GET /api/search` 走同一份融合检索（此前是标题子串过滤，正文命中搜不到） |
| ✅ | 搜索多级降级 | provider **四选一**：Tavily / **DeepSeek Search（2026-10-29）** / SearXNG 自动启动 / DuckDuckGo；选中的那个不可达（含没配 Key）统一降级 DDG。DeepSeek 另有 `fetch` 通道，失败降级 trafilatura |
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
| ✅ | 导出进度反馈（2026-10-29 接线） | 后端 `POST /export/custom/stream`（SSE 进度）+ `GET /export/download/{temp_id}` 早已就绪，缺的只是前端 —— `ExportPanel::handleCustomExport` 改为 `fetch` + `parseSSEStream`，进度条跟 `{phase, doc, total}`、`done` 拿 `download_url` 落盘。**刻意只留这一条路径**（流式端点内部调的就是 `_build_export_zip` 那一份，留同步端点当回退只会在某次改动时分叉） |
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
| ✅ | 产物自证条（2026-10-11） | 预览区正文上方一横排**图标 + 短文本**小标签：`v{n}` / 生成时间 / 字数 / 模型（lucide `History`·`Clock`·`AlignLeft`·`Cpu`）。它是 `<!-- limo-meta … -->` 自证头在**读者侧**的等价物 —— 上一轮让注释不再漏进渲染后，信息也随之消失，这一条把它以低干扰形态接回来。⚠️ **只取四项、缺就不给**（`params`/`promptVersion`/`sourceRefs` 是给 agent 读的，铺给用户是噪音）；**刻意不含「已编辑」**（同屏工具条已用 warning 色承担，相邻两行说同一件事只是噪音）。⚠️ 版本行按 **`(version, mode)` 双键**取（notes/polished 各自从 v1 起，只按 version 会让「笔记 v2」显示成整理版 v2 的时间，**无报错**）。⚠️ **流式生成中不显示**（`markdownMeta` 还是上一版，配新正文是错位读数）。⚠️ 展示判据收口 [lib/artifactMeta.ts](../frontend/src/lib/artifactMeta.ts)（`fmtTime`/`fmtChars` 从 `VersionSwitcher` 搬来，两处共用）；**不动后端**（字段早已从文件头现算派生）；**不参与导出**（视图装饰，同版本切换器） |

| ✅ | 产物折叠阅读三档（2026-11-08） | 预览区工具栏三档：主题（卡片网格）/ 一句话（每节一行）/ 全文（默认）。**档位优先于骨架轴**（主题档自带 `data-skeleton="card"`，不看用户选的 paper/card）。⚠️ **未展开只渲染标题 + 一句纯文本**，不把 Markdown 交给渲染器 —— 缩过的 Markdown 会散表格、废围栏、让单行图片凭空成图；展开的那一节走 `MarkerContent`（不是 `MarkdownPreview`），图片/表格/公式/十二种语义块与全文页逐字一致。⚠️ **入口判据只有「有 H2」**（`canFold`）—— 刻意**不看字数**：按字数分是提示词侧的判据（哪些注入只对长文加），与入口无关，混在一起会让能分节的短文没有按钮而界面上没有任何解释。档位持久化但**没有 H2 / 流式 / 编辑态强制回落全文**（后两条是「按钮在、行为没有」）。⚠️ **内容很少的节各有专门形态**：只有标题的节做成整行虚线分隔条（`.fold-hollow`）；正文只有一句纯文本的节不可展开、不给标识；整节是图片/表格/公式时用「含表格」这类类型说明充当那一行（`hint`）。切节复用 `scanHeadingLines`（围栏内与 `limo-block` 块内的 `##` 不算标题），`headingId` 与 TOC / 渲染侧同源。字号经根容器 `var(--read-font-size)` 接上 `Ctrl+滚轮`，网格列宽用 `em` 自动减列。**不参与导出**（同 `ArtifactMetaBar`） |

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

### 🔧 代码结构（重构，等触发条件）

> 这里只记**结构债**：不是缺功能，也不是坏掉了，而是「继续这么长下去会变贵」。
> 已量过的基线（2026-11-07）：`backend/app/` 44,705 行，`frontend/src/` 53,226 行；
> 后端最大文件 `storage/store.py` 2,350 行、`routers/export.py` 2,056 行、
> `generation/prompt.py` 1,978 行、`engines/plugin_loader.py` 1,737 行。
>
> ⚠️ 本节三条**都等触发条件**，不到条件时拆只是搬代码 —— 所以它们不进 [TODO.md](TODO.md)。

| 状态 | 功能 | 说明 |
|------|------|------|
| ❌ | `routers/export.py` 渲染层外移 | 该文件 2,056 行里 **:674-1623 这 950 行**是纯渲染：`_get_katex_assets()` 189 行 / `_build_selfcontained_html()` 186 行 / `_build_mindmap_html()` 121 行，外加 CSS 读取与 Markdown→HTML。同仓惯例是渲染住 services（`docx_export.py` 913 行、`chart_renderer.py` 1,101 行），**只有 HTML 导出是例外**。⚠️ **触发条件：第二次用到那段渲染代码**（CLI 要导出 HTML，或换掉 KaTeX 加载方式），届时抽成 `services/html_export.py`；没有第二个消费方之前拆只是搬代码 |
| ❌ | `generate_stream` 拆分 | `routers/generate.py:672-1510`，**单个函数 838 行**，同时做 SSE 推送 / 断连监听 / 超时计算 / 图表校验 / 图片归一化 / 续写 / 自动标题；同文件 `generate()` 另有 293 行（:379-672）。⚠️ **触发条件：出现第二个流式生成入口** —— 那时共用部分才谈得上抽哪里，在那之前拆只能靠猜边界 |
| ❌ | Controller→Service→Repository 分层 | generate.py 继续膨胀时 |

### 🧩 产物复用（2026-10-04 一期落地）

> 源头是三份 pi-docs 约定（密钥存储 / CLI 设计 / 产物复用），共同目标是**让产物能被 agent 复用**：不是没人能读产物，是**读的人不知道读了什么**。

| 状态 | 功能 | 说明 |
|------|------|------|
| ✅ | 产物自证头 | `notes`/`polished`/`process` 落盘首行 `<!-- limo-meta {json} -->`，回答「从哪来 / 怎么来的 / 是什么 / 可信度边界」；`core/transcript_meta.py` 拆出 envelope 层（`_parse_envelope` 只认第一行合法注释，`parse_meta` 与 `parse_artifact_meta` 各在其上做 schema 校验）；装配走 `generation/provenance.py::artifact_meta()` + `PROMPT_VERSION`。**普通 `transcript.md` 刻意不加**（会进 prompt）；**无头 ≠ 损坏**；编辑保存带回原头；**派生视图（思维导图）剥头**（模式 #57） |
| ✅ | 共享查询层 | `services/query.py::QueryService`（零 FastAPI import，CLI 可直接 import）收口全部读路径：分页 / ID 解析 / 版本编排 / meta 装配 / 截断标记。`core/dependencies.py::get_query_service` **惰性构造**（`main.py` 先建 Store 后建 SearchEngine，提前构造 = 静默空结果）。顺带修 `/api/api/search` 双前缀（**存活四个月**，模式 #56） |
| ✅ | 密钥与凭据卫生（二期，2026-10-04） | `.limo` 内零明文：**先去重**（同一 key 写多份才是根因）→ DPAPI 外移（`ctypes` 直调 `crypt32.dll`，零新依赖）→ 只留 `secrets_ref`；四张成对表收口 `config_io.py`；启动自检 `startup_check` + `scripts/check-secrets.py`；迁移/回滚 `scripts/migrate-secrets.py`（幂等 + 无明文备份）；顺带修 **4** 缺陷（`GET /api/config` 脱敏缺口 / `merge_sensitive` 死调用 / cookie 明文临时文件 / `llm_models` 类型守卫=启动即崩）；`.bak` **同生同灭**无需特殊处理；测试 50 条含 6 条对照实验 |
| ✅ | CLI + 契约文档（三期，2026-10-04） | Python 同仓 `backend/limo_cli/`（9 文件）；七命令 `status/list/search/get/export/folders/doctor`（**不做写操作**——成本/并发/幂等/误触发风险）。三层：CLI → `QueryService` → `Store`，**CLI 不解析库格式**（否则是第 6 个副本）。两个传输层（`HttpSource` / `LocalSource`）同契约。`--json` 时 **stdout 只有 JSON、日志全走 stderr**（**不用 TTY 检测**）；退出码 `0/2/3/4/5/6`（+130 SIGINT）区分「空」与「坏」，**按 `code` 判不按 HTTP 状态**；`Page` 上 `has_more`+`next_cursor`（能继续翻）与 `truncated`+`continuation`（到头但没给完）**并排且不混用**。契约文档两处：库内 `~/.limo/AGENTS.md`（`doctor --write-agents`，默认不覆盖）+ 仓库根 `AGENTS.md`（读者是改这条链的人）。门禁 `scripts/check-cli` 16 项（独立端口 7419，含 3 条负向对照）。**顺带修 3 缺陷**（模式 #59/#60/#61） |

### 🚀 规划中

> 合并自原 pi-docs/08-待做列表 + 09-设计提案（已归档删除）。需求驱动，不预建大教堂。
>
> **2026-10-29 起按梯队排**：**一** = 成本低、见效快（半天到三天，不新增架构概念）｜**二** = 需新增插件族或新模块（三到七天，会动架构）。**梯队只表达实施顺序，不表达价值高低**；表格顺序即建议的推进顺序。

| 状态 | 功能 | 说明 |
|------|------|------|
| 🟡 | embedding 语义搜索 | **字面检索已重做并实测**（2026-10-26，29 题 41% → 83%）。**向量侧阶段 1–4 已全部落地**（同日：`EmbeddingEngine` 云端内置 + `vec.db` 三表 + 增量 worker + RRF 查询融合 + 设置页开关 + **本地引擎 `local-bge-small` 与引擎切换，外加 **2026-10-27 进程外插件版（打包版离线可用）****，**默认关**；见 [search-retrieval.md](search-retrieval.md) §4.8 / §6.1）；评测侧 RRF 理论上限 97%。**真实库端到端实测已完成**（`_experiments/05-real-library-product-path/`，2026-10-27）：145 篇真实库 + 36,861 块 + 44 题，走**产品 `VectorSearcher.fuse`** —— 字面 H@5 77.3% / 纯向量 84.1% / **融合 88.6%**（H@50 三路 97.7% / 93.2% / **100.0%**，单路的洞不重叠 ⇒ 融合的价值是互补不是更强）。本地引擎的数值验证同轮覆盖。**仍未做**：云端 `text-embedding-v4` 在这个库上的数值对照（要花钱，02 已有同口径算法对照）；`0.8/1.2` 偏向量加权在更大样本上的验证（**不改产品** —— 权重是在这 44 题上后验挑出来的） |
| ✅ | 本地大模型接入【一】（2026-10-29） | Ollama 一类本地端点全走 **OpenAI 兼容 `/v1`**：`PROVIDER_DEFAULTS` 加一条 + `core/token.py` 的 `DEFAULT_CONTEXT` 补窗口 + **`provider_requires_api_key()` 收口空 Key 检查**（13 处，此前各写一遍）—— **不需要伪造 API Key** 是这项的验收点。⚠️ 本地 provider 不在 models.dev 库里，`context_window` 必须由预设给值 —— 缺了它按保守 32k 裁历史，现象是「本地模型明明支持 128k 却总是忘事」，零报错 |
| ✅ | 检索质量门禁（横切，2026-10-29） | `bash scripts/check-all search`（独立层，约 1s）：81 条切片语料 + 30 题 + 基线，`SearchEngine.search` 退化就红。⚠️ **只测字面层** —— 向量 / RRF / 对话侧一个字都不反映到分数上。⚠️ **绝对分数不可迁移**（切片把 H2 当一篇文档，BM25 长度归一化让命中更容易）。⚠️ **更新基线必须在 CHANGELOG 记理由**，否则基线变橡皮图章 |
| ✅ | 检索查询改写（2026-10-29） | 前置缺陷先修：`search_docs` 此前是**标题子串过滤**，根本不走检索 —— 现与 `GET /api/search` 共用 `services/search_fuse.py::fused_search`。改写本身已实施：`services/query_rewrite.py` 一次 LLM 调用出 2–3 条检索式，形态是**「原词 + 改写一起进 RRF」**（`fused_search` 的 `extra_queries`），**不是替掉原词** —— 实验 44 题真实库：融合路 H@1 52.3% → 75.0%、H@5 88.6% → 97.7%，而「只改写」H@5 反而更低（95.5%）且丢了原词兜底。⚠️ **默认关**（`config.search.rewrite_queries`，打开 = 每轮多一次计费调用）；**只挂对话侧**（文档库搜索框是逐字联想，多一秒会毁掉输入体验）。实验否决了「规则去口语词」（负收益，二字组 OR 已被砍）与「只做关键词抽取」（H@5 打平但 H@1 低 7–9pp）。见 `_experiments/06-query-rewrite/` |
| ✅ | RSS + arXiv 导入源【一】（2026-10-29） | **RSS/Atom 走 WebpageAdapter 的内部分支**（不新建适配器 —— 同一 URL 两个 `accept()` 必然打架）→ N 条合并成一篇（上限 20）；**arXiv 是新适配器、委托 `DocumentAdapter`**（`/abs/` 与 `/pdf/` 两形态，注册在 Document 之前）。⚠️ **刻意不新增 SourceType**（会触发 `sourceTypeCoverage.test.ts` 要求同步三处，漏一处白屏）—— RSS 用 `webpage`、arXiv 用 `pdf`，既有能力自动可用。⚠️ 措辞：arXiv 的效果是「**与现有 URL 导入 PDF 行为一致**」，不是「PDF 专属能力全部可用」（源文件同样不持久化） |
| 🟡 | 播客转录导入 | 需 ASR 引擎就位后再评估 —— 2026-10-29 从「更多导入源」三合一里拆出来单独列，它和前两项**不是同一档成本** |
| ✅ | Reranker 重排【二】（2026-10-29，**默认关，实验裁决为不采纳**） | 第三族插件基类 `RerankPlugin` + 云端 `dashscope-rerank`（`gte-rerank-v2`）+ 编排 + 开关 + 本地插件模板，**全套落地但默认关**。**四臂实验结论（15 题方向性）**：`B+W` 的 H@1 比 `W` 低 **20pp**、MRR 低 **13.8pp**；`B` 也不如 `A0` —— 重排在调过权重的基线上是净损害，**保持关**（代码保留，回退成本零）。⚠️ 同轮查出并修掉一个真缺陷：库里 93% 文档 `rawText` 是字幕 JSON，被直接切窗喂 cross-encoder；修复让 B 臂 H@5 从 73.3% 拉回 86.7%，**但不足以反转结论**。⚠️ 同一实验里**加权 `0.8/1.2` 把 H@1 从 46.7% 抬到 66.7%**，是唯一干净的赢家 —— 但它是后验挑的权重，独立证据（留出集）**还没跑**，故**不算已采纳**。⚠️ **只挂对话侧**（文档库搜索框是逐字联想）。⚠️ 隐私面**与向量检索完全不同**：重排把候选正文发到云端并计费。详见 [search-retrieval.md](search-retrieval.md) §九 / §9.9 |
| ✅ | MCP 服务端【二】（2026-10-29） | 把库暴露给 Claude Code / Cursor 等外部 agent：`QueryService` 外层加 **JSON-RPC over stdio**（**不是新实现，是 `Source` 契约的第二个消费者** —— 第一个是 `limo` CLI，两个实现是 `HttpSource` / `LocalSource`；**不改 `QueryService` 一行**）。手写协议层、零新依赖（**刻意不引入 `mcp` SDK**）。**双栈**：`server/discover`（`2026-07-28`，stateless）+ legacy `initialize` 握手 —— Claude Code 逐服务器协商、失败自动回退，少一条栈一半客户端连不上。**六工具只读**：`limo_status` / `limo_list` / `limo_search` / `limo_get` / `limo_read` / `limo_folders`；`limo_read` 多一个 `part="raw"`（读原文，走 `get_resource` 而非 `get_artifact`）。⚠️ **默认直开本机库**（冷启动 0.3s，不必先开应用）、`--http` 才转去问 7413。⚠️ **`main.py` 的 `--mcp` 短路必须排在它自己的 `sys.stdout is None → devnull` 归一化之前**（windowed 打包下协议通道就是 stdout，换掉之后全写进黑洞且**零报错**）。⚠️ 与 `limo` CLI 的分工：CLI 是**人**用的终端命令，MCP 是**agent**用的工具调用，消费者不同故不合并 |
| ❌ | 跨会话记忆【二】 | 对话侧目前会话彼此隔离，模型记不住「这个人偏好简洁回答」这类长期事实。**先做「显式记忆条目」一档**（可写入的长期事实 + 对话时按相关度注入），不抄 Mem0 的语义/情节/程序三层。⚠️ 与 `search_docs` 的分工必须写死：**记忆是「关于用户的」，库是「关于内容的」** —— 混一起会让模型把用户偏好当成知识库依据引用 |
| ❌ | 知识图谱 / 反向链接 | 文档间引用关系可视化，依赖 `#标签` 语法先落地 |
| ❌ | Markdown 渲染风格预设 | 多套 CSS 变量集（行高/间距/标题比例），与主题六轴正交 |
| ❌ | remark-tag 标签插件 | `#标签` 语法在笔记中可点击（纯前端） |
| ❌ | 组件/功能挂载插件系统 | 有第三方开发者时再做 |
| ❌ | 后台任务队列 | 批量任务性能遇瓶颈时（arq/Redis） |
| ❌ | UI 大重构 | VSCode 式面板，有明确多工作流需求时 |

### 📊 汇总

| 域 | 完成 | 总计 | 进度 |
|----|------|------|------|
| 📥 内容导入 | 8 | 8 | 100% |
| 📚 文档库 | 11 | 11 | 100% |
| 🔗 多篇合成 | 16 | 16 | 100% |
| 📝 转录 & 引擎 | 8 | 8 | 100% |
| ✨ LLM 生成 | 15 | 15 | 100% |
| 🔍 图片处理 | 4 | 4 | 100% |
| 💬 AI 对话 | 10 | 10 | 100% |
| 📊 图表生成 | 12 | 13 | 92% |
| 📤 导出 | 12 | 12 | 100% |
| 🎨 前端 UI/UX | 22 | 22 | 100% |
| 🛡️ 安全 & 运维 | 8 | 8 | 100% |
| 🔧 代码结构 | 0 | 3 | 0% |
| 🧩 产物复用 | 4 | 4 | 100% |
| **合计** | **130** | **134** | **97%** |

> 本表由各域 `| ✅ |` / `| ❌ |` / `| 🟡 |` 行**逐行数出来**，不手填。改完任何一个域，
> 重新数一遍再写 —— 2026-11-07 复核时发现三行数字比实际少（文档库 10 vs 11、
> LLM 生成 14 vs 15、前端 UI/UX 21 vs 22），是此前手填漏改留下的。
>
> ⚠️ 「🚀 规划中」不计入本表 —— 那一节是想法池，不是进度。
