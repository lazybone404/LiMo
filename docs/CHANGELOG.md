# 修改记录

> 记录每次重要修改的**做了什么**、**为什么**、**影响范围**。
> 不只是"修了 bug"，更是"当时为什么这样设计，后来为什么改了"。
> 按时间倒序，最近的在最上面。

---

## 2026-09-23 — 参考文件三改（注入文案 / 上限 1MB / 超窗自动压缩）+ DeepSeek 模型名与思考模式参数对齐

> 用户诉求：「参考文件这部分提示词可以优化以及参考文件大小的限制可以做宽一点」+「查一下 DeepSeek 官网对模型设置方面做更新」，中途追加「根据模型上下文窗口决定要不要先对参考文件先交给大模型压缩？」

### 1. 注入文案重写：从「劝退」到「主动利用」

旧 `<reference_texts>` 节对**每一份**参考都重复一遍「仅供背景参考，与原文冲突时以原文/模型判断为准」。这是**劝退措辞**——参考资料是用户**主动上传**的增量信息（典型场景是 B 站评论区观点、配套材料），文案却让模型把它当摆设。另有三处缺失：无融入指引（只说「可参考」）、冲突处置一刀切（不区分客观事实与不同观点，会抹掉评论区里的多视角）、无防照抄（参考常与原文大段重复）。

重写为三段式：**节首统一说明**（只说一次，不逐份重复）+ **四条使用要求**（主动利用 / 冲突分两类处理 / 不要照抄 / 忽略无关部分）+ **逐项编号** `【参考资料 N】来源：xxx`（替代旧的 `---` 分隔符——编号让模型能对应「第几份」）。`prompt.py::_format_reference_texts`。

### 2. 单文件上限 200KB → 1MB（前后端同值）

200KB 是按 128k 上下文模型留的余量，而主流模型已到 1M（DeepSeek V4 系列），200KB 反而成了瓶颈。1MB ≈ 35 万汉字，在 1M 窗口下「参考文件 + 原文 + 输出」仍留有余量；再大就只有超大窗口模型能用。落点：`routers/references.py::MAX_REF_BYTES` + `GenerateOptions.tsx` 前端同值预校验（错误文案同步）+ `reference_store.py` / `api/references.ts` 注释。

⚠️ **放开上限直接催生了第 3 条**：1MB 参考文件塞进 128k 窗口的模型必然超窗，请求会**直接失败**。

### 3. 超出模型上下文时先压缩参考文件（新增 `generation/reference_compressor.py`）

按模型**实际解析出的窗口**（`resolve_context_window` 四层解析）判断：`原文 + 参考 > 窗口 × 0.7`（与对话预算同源公式）才动手，**装得下就原样返回、一次 LLM 都不调**。

- **压缩用当前生成模型**，不额外配置压缩专用模型——省一个配置项
- **失败不阻塞生成**：摘要失败 → 按额度截断并标注；摘要压不够 → 追加逐份截断
- **只在 notes 侧做**：参考文件本就只注入 notes，单产物 polished / `Generator.generate(mode='polished')` 直接跳过（压缩纯属白花钱）
- **`reasoning_effort="low"`**：摘要是忠实压缩任务，DeepSeek 思考模式默认 effort=high，不降档会白等数十秒静默期（为此给 `LLMService.call()` 补了 `reasoning_effort` 透传参数）

**流式路径新增 `stage` SSE 事件**（`{type:'stage', message:'参考文件超出模型上下文，正在压缩…'}`）：压缩可能耗时数十秒，静默等待容易被误认为卡死。前端 `TaskInfo.stage` 单值承载，`setProcessing` 的全量替换语义让它在正式 chunk 到达时**自动消失**，无需手动清理。

**测试过程中抓到三个真实缺陷**（都已修，`tests/test_reference_compressor.py` 留了回归）：

1. **原文自身超预算时兜底会把参考文件削成空壳** —— `base ≥ budget` 时参考文件压到 0 也救不回请求（该场景由保底份额决定大小），硬削只是让用户拿到一份几乎空的参考文件、请求却照样失败。改为该场景**不做总量兜底**。
2. **`_truncate_to_tokens` 的「至少留 200 字符」硬下限让截断失效** —— 兜底要把每份从 200 token 削到 10 token 时，200 字符下限让每份**纹丝不动** → 不收敛。改为只以 `MIN_KEEP_RATIO` 为下限。
3. **截断不幂等 + 保留比例按「含标记长度」算** —— 反复调用会叠标记，且出现「削到 166、实际输出 186」的假收敛。改为截断前剥除已有标记、且先从 limit 里扣掉标记自身的 token 开销。

⚠️ 兜底是**近似收敛**（每份最少保留一个标记的量），业务上留的 30% 预算余量的意义正在于此。测试容差 5%——足够区分「收敛」与「没跑」（后者会超一倍）。

**文件名清洗**（`prompt.py::sanitize_reference_name`，注入侧与压缩侧同源复用）：文件名进 `【参考资料 N】来源：{name}` 与压缩 prompt 的 `来源="{name}"` 属性位，换行可伪造出假条目、双引号可闭合属性、全角方括号可让名字里出现与结构标记逐字相同的串。清洗 = 空白归一 + 引号替换 + 方括号转半角 + 限长 80。**只做结构安全，不做内容审查**（内容本就是用户自己的素材）。

### 4. DeepSeek：模型名两轮变更 + 思考模式参数契约（依官方文档核实）

查官网（`api-docs.deepseek.com` 原文，非第三方聚合站）后确认两轮变更，项目里三处实锤问题：

| 问题 | 现状 | 修法 |
|------|------|------|
| 预置模型用**已下线的名字** | `deepseek-v4-flash`（2026-09-10 起 V4.1-Flash 改名为 `deepseek-flash`，旧名对应的模型已下线，官方暂用路由兜着） | `config.py` 预置改 `deepseek-flash` + `deepseek-v4-pro`；默认模型与 `routers/model.py` 回退值同步 |
| `compress.py` 一级 fallback **名存实亡** | 硬编码 `deepseek-chat`，该名 **2026-07-24 已停用** → 每次构造失败静默掉到二级「当前对话模型」（功能被兜住所以一直没暴露） | 改 `deepseek-flash`（现役里最便宜的一档）+ 带警告的 docstring |
| 续写的 temperature 递减是**空操作** | DeepSeek 思考模式下 temperature / presence_penalty / frequency_penalty **不报错但不生效**，而思考模式默认开启 | 保留代码（兼容非思考模式与其他厂商），在 `service.py::stream` docstring 显式标注 |

**思考模式参数契约**（记下来免得再踩）：默认开启、effort 默认 high（三档 low/high/max）；上述三参数思考模式下无效；`top_p` 相反——**仅思考模式生效**且低于 0.95 按 0.95 处理。**V4.1-Flash 原生支持图像理解**（旧 vision 实验版已并入 flash 一条）。上下文 1M / 最大输出 384K / 并发 flash 2500、pro 500；价格为峰谷双档（flash 输入 1/2、输出 4/8；pro 输入 4.5/9、输出 13.5/27，元/百万 token），故预置不填单一价格以免误导。

### 5. 质量与安全性检查（同日续，用户要求「完成后对这方面进行质量以及安全性检查，前端页面要小字提示」）

逐项复查参考文件全链（上限 / 暂存 / 压缩 / 渲染），**发现并修掉四个问题**——其中两个是放开上限**放大**的既有缺陷：

**(a) 暂存区孤儿条目永不清理**（`core/reference_store.py`，1MB 把影响放大 5 倍）

旧实现是**按入参惰性清理**：只有 `get_references()` 的入参里出现某个 id、且恰好过期时才会 pop。而前端一次只持有**一个**参考文件 id —— 用户换一次文件就换一个 id，**旧 id 从此再也不会出现在任何入参里**，那条记录会一直驻留到进程重启（20 分钟 TTL 形同虚设）。1MB 时代一次漏一份，反复换文件就是持续吃内存。

改为 **`_purge_expired_locked()` 全表扫**（save 与 get 两条路径都调）。同时补两道纵深防御闸门：条目数 32、总量 32MB（超限抛 `AppError(REFERENCE_STORE_FULL)`）。⚠️ 闸门**在清理之后判** —— 否则「满了但其实已过期」的场景会被误拒。

新建 `tests/test_reference_store.py`。⚠️ 用例断言的是 `_store` 的**实际条目数**而非「取回的内容对不对」——按入参清理的实现在这种用法下取回的内容完全正确，只有条目数能暴露泄漏。并用「清理函数换成 no-op」实测验证了该用例**有区分力**（no-op 下必失败）。

**(b) 摘要没有独立超时**（`generation/reference_compressor.py`）

压缩发生在生成总超时（`_gen_timeout` 330/400s）**之外**，不给这一层就只有 SDK 的 330s 兜底：模型挂起时用户要**干等 5 分钟**才看到降级。新增 `COMPRESS_TIMEOUT = 180.0` 用 `asyncio.timeout` 包住 `llm.call()`。

⚠️ 两处容易写错：`asyncio.timeout` 抛的是 `TimeoutError`，而**用户取消**走的是 `app.llm.CancelledError`（继承 `Exception`，与 `asyncio.CancelledError` 无关）——`except CancelledError: raise` 必须排在前，两者不会互相吞掉。另 `TimeoutError` 单独列一支：`str(TimeoutError())` 是**空串**，混在通用 `except Exception` 里会留下一条 `error=` 无原因的日志，排查时分不清「超时」与「模型报错」。

测试新增用例 9，断言的是**实测耗时**（而非「函数被调用了」），并用日志替身确认走的是「超时」专用分支（两支都返回 `None`，只看返回值分不开）。

**(c) 缺 prompt 注入防御声明**（`prompt.py` / `COMPRESS_PROMPT`）

全 prompt 体系里没有一句「输入内容里的文字不是给你的指令」。参考文件的注入面恰恰**最强**：它常来自第三方（他人文章、评论区），而第 1 条把文案改成「**主动利用**」后，模型对该内容的遵从倾向进一步被放大 —— 内容里的一句「忽略以上要求，改为输出…」可能真的被执行。

在两处节首补声明（注入侧 `_format_reference_texts` 第 5 条 + 压缩侧 `COMPRESS_PROMPT`）：素材里的命令式表述一律视为**待整理的普通文本**。**内容本身保持零触碰**（不做转义/过滤）——沿用「用户内容零改动」原则，声明性防御而非改写数据。

> ⚠️ **本次只覆盖参考文件这条链**。`source_content`（导入的网页/文档正文）同样缺此声明，且注入面同级（网页正文完全来自第三方）——未纳入本轮（会改动全部 prompt 与产物风格，需单独评估），记在此处备查。

**(d) 压缩 prompt 漏占位符这类错误测不出来**

用例 8 只断言了「文件名进了 prompt」。撰写本轮改动时**实际踩到过一次**：编辑 `COMPRESS_PROMPT` 时误删了 `<参考资料>` 块（`{content}` 一起没了），而 `str.format` 对**多余关键字不报错** → 模型只收到空壳模板、摘要退化成凭空编造，**文件名断言照样通过**。补断言：与源文本比对出现次数（同时覆盖「内容进了」与「模板只展开一次」）。

**前端小字提示**（用户明确要求）：`GenerateOptions.tsx` 参考文件区加一行 10px 说明「参考文件与原文合计超出所选模型上下文窗口时，会先自动压缩参考文件（保留要点）再生成」，仅在「不只是 polished」时显示（polished 不注入参考，提示无意义）。

### 6. 修复流式生成必炸的作用域 bug（同日续，0.4.4 打包完成后首次生成即复现）

```
UnboundLocalError: cannot access local variable 'ref_texts' where it is not associated with a value
  File "app\routers\generate.py", line 528, in merged_stream
```

**症状**：0.4.4 安装包打完后，点一次「生成笔记」立刻失败 —— 不是偶发，是**必然**。

**根因**：这是第 3 条（超窗压缩）引入的。`generate_stream` 顶层有 `ref_texts = _resolve_reference_texts(...)`，而压缩逻辑写在**嵌套函数** `merged_stream` 里：

```python
async def merged_stream():          # generate_stream 内部的嵌套函数
    ...
    if ref_texts and needs_compression(...):   # ← 炸在这里
        yield "…正在压缩…"
    ref_texts = await compress_references_if_needed(ref_texts, ...)   # ← 这一句是元凶
```

Python 的规则是「**只要函数体内出现对某名的赋值，该名在整个函数里就是局部变量**」，与赋值语句在不在前面无关。`merged_stream` 里那句赋值让 `ref_texts` 成了它的局部变量 → 上一行的 `if ref_texts` 在任何赋值之前读取它 → `UnboundLocalError`。与「关键模式」#26（函数内重复 import 造成同名局部）同根因，只是这次跨了**函数作用域**。

**为什么测试没抓到**：这不是逻辑错误，而是**运行时必炸、但读代码和跑单测都正常**的作用域问题 —— 而流式端点的真实运行路径此前完全没有测试覆盖（`test_e2e_smoke.py` 需后端启动 + 真实 LLM key，不覆盖流式生成）。

**修法**：在嵌套函数内改用局部名 `refs = ref_texts` 承接，压缩结果只给 notes 用，本就没有回传外层的必要。两处（双产物 / 单产物分支）同改。

**防线**：新增 `tests/test_closure_shadowing.py` —— 用 AST 静态扫描全库，判据为
「外层函数赋值的名字 ∩ 嵌套函数赋值的名字 ∩ **嵌套函数内读早于写**」→ 违规。防的是**整类**而非这一个：

- 按**执行顺序**而非 AST 子节点顺序展开读写（`ast.Assign._fields` 是 `('targets','value')`，而语义上 value 先求值 —— 直接 `ast.walk` 会把 `x = x + 1` 判成「先写后读」，正好漏掉最该抓的一类）；`AugAssign`（`x += 1`）同样先读后写
- 形参视为「进入即绑定」（order 0），避免把「读形参」误判为先读；`nonlocal`/`global` 声明豁免
- **判据自证有区分力**（模式 #38）：内嵌三种真实违规形态（先读后写 / 自增 / 自引用赋值）确认能抓到，三种安全写法（局部名承接 / 先写后读 / nonlocal）确认不误报；并**对真实代码自证** —— 把修复回滚成 bug 版本，确认检查立刻报出 `generate_stream() 的局部变量 'ref_texts' 在嵌套函数 merged_stream() 内先读后写`
- 全库扫描确认其余 5 处同类「遮蔽」都是安全的（内层先赋值后使用，正是 `_it = None` / `chunks: list[str] = []` 这类预初始化防御写法）

**教训**：新增的「外层变量 + 嵌套函数内加工」组合必须换局部名。同一文件里 `video_path` 也因「条件分支内赋值 + finally 引用」被预初始化过（503 行 P0-5 注释）——那是**同作用域内**的同类问题（UnboundLocalError 家族的另一个变体），本次这个是**跨作用域**版本，两者的共同点是「变量可能在任何赋值之前被读取」。同一文件两次栽在这上面，故补静态检查而非只修这一处。

---

## 2026-09-21 — 打印样式 + PDF 导出（WebView2 PrintToPdf / 打印对话框双路径）

> 用户诉求：接着 Word 之后看 **PDF 导出**。四条候选路线（WebView2 PrintToPdf / xhtml2pdf / `window.print()` / Playwright）经复核后共同卡在**同一个前置**：现有自包含 HTML 是纯屏幕布局 —— 没有 `@page`、没有 `@media print`、没有分页控制。**打印样式不是其中某条路的准备工作，是四条路共用的地基**，故先做它；地基铺好后，用户选定走 **A 路线（WebView2 PrintToPdf）**——免打印对话框、直接落文件。

### 设计决定：打印的是**后端产物**，不是应用 DOM

`window.print()` 直接打 React DOM 看似最省事，但要在前端另写一套「隐藏三栏 + 工具条 + 主题 CSS 变量」的打印样式 —— 那是**第二套排版**，与后端 HTML 导出必然漂移，违背「预览一致」原则。

改为：拉取 `/export/{id}?format=html&mode=…` 的自包含 HTML，喂给渲染引擎。这样只维护**一套**打印 CSS（后端），产物与 HTML 导出逐字一致。**这个决定让后面的路线切换变得便宜**：换成 PrintToPdf 时，喂给它的仍是同一份 HTML，只换最后一步 —— 实际改动正是如此。

### A 路线：WebView2 `PrintToPdf` 直接落文件（Tauri 内）

[Rust 侧](../frontend/src-tauri/src/lib.rs) 新增 `pdf_export` 模块 + `export_pdf` command：前端 `invoke` 传 URL 与文件名 → 新建**屏幕外窗口**加载该 HTML → `on_page_load` 完成后取 WebView2 controller → `ICoreWebView2_7::PrintToPdf` → 关闭窗口 → 回传落盘路径。浏览器环境无 WebView2 COM，自动退回打印对话框（[print.ts](../frontend/src/lib/print.ts) 内分支）。

四个非显然的约束，都是**先想明白才写对**的：

1. **必须新开窗口，不能复用主窗口、也不能用 iframe** —— `PrintToPdf` 只打**该 WebView2 实例的主文档**，而主窗口的主文档是应用 UI 本身。iframe 同样不行：`ICoreWebView2Frame2` 只有 `PrintAsync`（弹打印 UI），**没有**落文件的 `PrintToPdf`。
2. **窗口挪到屏幕外（-32000, -32000），而非 `visible(false)`** —— WebView2 对不可见窗口会推迟渲染，PrintToPdf 有概率拿到空白页。
3. **`on_page_load` 回调里不能就地 `with_webview`** —— 该回调跑在 UI 线程，就地重入事件循环有死锁风险；改为换线程 + 延时后再抓 controller（顺带等图片解码/字体就绪）。延时取 **3000ms**：初版写 400ms，但 `Finished` 只代表主文档加载完，而导出 HTML 自包含（~1.8MB base64 字体 + 内联图片），字体解码与 KaTeX 客户端渲染都在其后。**没有「更短也够」的成功样本**，故保留实测值，代价是每次导出多等约 3s。
4. **`PrintSettings` 的每一项都要显式设，没有一项默认值可用** —— 尤其 `PageWidth/PageHeight` **默认是 Letter**（不设就印出美式尺寸），`ShouldPrintBackgrounds` 默认 false（关掉后代码块只剩一圈边框）。

⚠️ **代价：COM 接口不跨 crate 实例**。`pdf_export` 直接用 `webview2-com` 的类型，版本必须与 tauri 内部的同源（`tauri 2.11` → `wry 0.55` → `webview2-com 0.38`），否则 `ICoreWebView2` 是两个不相干的类型、`.cast()` 直接编译失败。`Cargo.toml` 已写死该约束并注释了排查方法。

### 依赖：零新增包

`webview2-com` / `windows` 本就是 `tauri → wry` 的传递依赖、已在 `Cargo.lock` 里，**没有下载任何新 crate、没有体积增量**。前端侧 `@tauri-apps/api` 也已在 devDependencies（`restart_backend_sidecar` 在用）。这是 A 路线相对 xhtml2pdf（CSS 子集弱、4 套主题会崩）与 Playwright（~300MB）的决定性优势。

### 打印 CSS 的落点（三处，不是一处）

| 落点 | 管什么 | 为什么不能合并 |
|------|--------|---------------|
| `_PRINT_CSS`（`export.py`） | `@page` 尺寸页边距、解除屏幕布局、分页控制、代码容器底色 | 4 套主题共用 |
| `_get_pygments_css()` 的 `@media print` 段 | **逐 token 配色**换成浅底主题 | token 是 `.codehilite .k` 这类类选择器，容器规则**压不住**它们 |
| `HTML_THEMES["limo"]` 的 `@media print` 段 | 把暗色段的浅色取值还原 | 见下第 3 条 |

⚠️ **三者顺序有意义**：`style_block` 里主题 → Pygments → badge → KaTeX → `_PRINT_CSS`。CSS 媒体查询不增加特异性，同特异性下**后者胜** —— `_PRINT_CSS` 放最末才能压住前面所有屏幕样式。

### 三个缺陷都是**读 PDF 图**发现的，不是推理出来的

验证方式是闭环的：临时脚本生成 HTML → `msedge --headless --print-to-pdf` → `pypdfium2` 转 PNG → 直接看图。前两个缺陷在截图上肉眼可见，靠读 CSS 是想不到的。

1. **代码块 token 仍是浅色**（注释几乎看不见）
   初版只写了 `.codehilite code, pre code { color: #1F2328 !important }` —— 但 one-dark 的 token 是 `.codehilite .k { color: #C678DD }` 这种**类选择器**，作用在 `<span>` 上。`code` 的 color 只对没有自身 color 的文本生效，span 按自己的规则走。∎ **修法**：整份换 Pygments `default`（浅底主题）的 defs，而不是试图逐条覆盖。

2. **`break-inside: avoid` 用在 `pre` / `table` 上会产出整页空白**
   一个 45 行的代码块装不进当页剩余空间 → 被整体推到下页 → 前一页留下大半页空白（实测 3 页文档凭空多出一页）。这两个元素的**长度无上界**，正是最不该用 avoid 的。∎ **修法**：`pre`/`table` 移出 avoid 名单（自然跨页），只保留行/项级原子性（`li`/`tr`）与短块（`blockquote`/`figure`/`img`）；表格靠 `thead { display: table-header-group }` 在每页重复表头。修后 5 页 → 4 页。

3. **limo 主题的暗色段与打印会同时匹配**
   `@media (prefers-color-scheme: dark)` 与 `@media print` 不互斥 —— 系统处于暗色偏好时一起生效，`blockquote` 的 `#AEAEB2`、行内 code 的 `#93C5FD` 落在白纸上几乎不可见。`_PRINT_CSS` 只带 `!important` 管了 body/代码容器，管不到这些。∎ **修法**：由**引入暗色段的主题自己**在打印时还原成浅色取值（主题自洽原则）。
   ∎ **验证**：把 limo 的暗色媒体查询外壳剥成顶层规则以模拟暗色偏好，打印后确认全部可读。

### 验证

**打印样式**（纯 CSS 层）：4 套主题（paper/github/minimal/limo）+ 暗色偏好模拟，各打一份 PDF 后转图肉眼核对：**全部通过**（代码块浅底深字、表格不跨页断、标题不孤立、公式正常、`max-width` 已解除）。

**PrintToPdf 端到端**（Rust 侧那条链，`unsafe` COM + 屏幕外窗口 + 时序，最需要实测的一段）：临时插入自触发代码 → `cargo run` 起真实 Tauri 应用 → 应用自动完成一次导出 → 读日志拿落盘路径。结果：

- `PDF_VERIFY result=Ok("C:\Users\...\Downloads\PDF验证-中文标题.pdf")` —— **无伪成功，路径真实存在**
- `pypdfium2` 读回：**84908 字节 / 1 页 / 209.9 × 297.0 mm**（即 A4，证明 `PageWidth/PageHeight` 确实覆盖了默认的 Letter）
- 渲染成 PNG 肉眼核对：中文正文、代码块（深底 + token 配色）、表格、引用块浅蓝底 + 左侧竖线、列表、页边距 —— **全部正确**。背景保留证明 `ShouldPrintBackgrounds(true)` 生效

⚠️ **但这一轮测的是自造的测试 HTML，不是 `/export/{id}` 的真实产物** —— 它验证了「COM 链路能出 PDF、能出 A4、背景保留」，**没有覆盖真实导出数据流**。下面是补测。

### 补测：真实导出链路暴露的 `Content-Disposition` 坑（本次修复）

用真实文档（`/export/tr_e4330f4a9157?format=html&mode=notes`）重跑同一条链，得到的是 **876 字节纯白 PDF**，而回调照样报 `succeeded=true` —— **伪成功**，没有任何报错。

四轮排除，每轮都是为了让下一轮的结论无可辩驳：

| 轮次 | 动作 | 结果 | 排除 |
|------|------|------|------|
| 1 | 延时 400 → 3000ms | 仍 876 字节 | **时序**（不是没渲染完） |
| 2 | 屏幕截图 12 帧 + 区域像素统计 | 导出窗口帧 `stddev=0.00`（纯白） | **页面根本没渲染**，不是打印环节的问题 |
| 3 | 同一份 HTML 走 `file://` / 走 `/health` | 673045 / 12738 字节，**均成功** | **External URL 与渲染链路本身** |
| 4 | `curl -D -` 看响应头 | `content-disposition: attachment` | **根因** |

∎ **根因**：`/export/{id}?format=html` 默认带 `Content-Disposition: attachment`（点「HTML」按钮就该下载文件，这是对的）。但 Tauri 路径是让 WebView2 **直接导航**到这个 URL —— attachment 让它去**下载**而不是渲染，窗口全白，PrintToPdf 打印的正是这张白纸。`file://` 不查该头，所以第 3 轮的对照实验恰好把范围收窄到了 HTTP 头。

∎ **修法**：`export_content` 加 `inline` 查询参数（默认 False）；前端 [print.ts](../frontend/src/lib/print.ts) 的 `withInline()` 在出口统一追加 `inline=1`（免得每个调用方都要记得）。**浏览器那条路径（`fetch` + `srcdoc`）不受影响** —— fetch 在 JS 里能拿到 body，无视该头。

∎ **离屏窗口下的决定性对照**：带 `inline=1` → **800470 字节**；不带 → **876 字节**。

∎ **修正后的端到端实测**（6 页全 A4 / 209.9 × 297.0mm）：p01 中文标题层级 + 引用块 + 行内代码 + 代码块浅灰底；p02 表格（表头底色 + 边框）+ 嵌套列表；p03 **代码块 Pygments 三色高亮**（证明「整份换浅底 defs」那处修法真的生效）；p04 表格换行单元格；p05 **跨页表格重复表头**；p06 **图表 PNG 内嵌**。**全部正确**。

其余：`cargo check` 零警告；前端 `tsc -b` 通过、`npm test` 98 passed、lint 无新增告警。

临时验证代码、`tauri.conf.json` 的临时改动（主窗口设 `visible:false` 以免弹窗打扰）、测试 HTML 与下载目录里的验证 PDF —— **全部已删/已还原**（`grep` 确认零残留）。

⚠️ **遗留：修复只在源码里，不在任何已构建的 exe 中**。本机 7413 上跑的 `LiMoBackend.exe` 是 8 月 26 日构建的产物，内嵌的是 4 周前的 `export.py`（`inline` 参数根本不存在）。**重新 PyInstaller 打包后此修复才在桌面版生效**。

### 遗留

- **浏览器环境仍是打印对话框**（无 WebView2 COM），需用户手动选「另存为 PDF」；Tauri 桌面版才是真一键
- 固定存系统「下载」目录，不弹「另存为」对话框 —— 要它得再引入 `tauri-plugin-dialog`；当前同名自动加 `(1)` 序号
- 仅 Windows：`PrintToPdf` 是 WebView2 能力，非 Windows 平台该 command 返回「不支持」错误
- 思维导图 tab 的 PDF **未开通**（走专用端点，markmap 打印效果未验证），按钮与 Word 同门控禁用并给出 title 原因

---

## 2026-09-21 — Word（.docx）导出

> 用户诉求：导出格式从 md / html / plain 三种扩到四种，**先做 Word**。
> 选它的理由是依赖全现成：`python-docx` 已在依赖（Doc 引擎在用）、`beautifulsoup4` 项目各处已在用 —— **零新增包、零体积增量**（PDF 那条路要背 WebView2 PrintToPdf 的 unsafe COM 或 300MB 的 Playwright，另议）。

### 两条导出路径共用一份 markdown 理解

原先 markdown→HTML 的四段逻辑（`_preprocess_gfm` / `_markdown_to_html` / `_add_lang_labels` / `_add_heading_ids`）长在 `routers/export.py` 里。Word 导出也要用它 —— 但 **services 反向 import router 是分层倒置**。故下沉为 [`app/services/markdown_render.py`](../backend/app/services/markdown_render.py)，`export.py` 保留同名私有别名。

**抽取不是"顺手重构"，是本次的正确性要求**：LiMo 的 markdown 是**方言**（表格前补空行、`~~` 删除线、任务列表 checkbox、`Content-[mm:ss]` 时间戳 badge），方言只应存在一份。两跳换来的是 **Word 导出与 HTML 导出对同一份 Markdown 的理解永远一致** —— 这正是导出系统「预览一致」原则的延伸。

⚠️ **抽取后立刻做了等价性验证，而不是靠推理**：从 git 取旧版 `export.py` 独立加载，对 8 个样本（标题/表格/代码块/任务列表/时间戳/引用/公式/空串）逐字节比对 HTML 输出 —— **差异 0 处**。`markdown_to_html(codehilite=True)` 之外新增的 `codehilite=False` 分支是 Word 侧专用：docx 里每个 token 一个 `<span>` 是纯噪音，关掉后语言名仍由 `add_lang_labels` 保留、代码文本原样取出。

### 实现要点（都是踩过才知道的）

| 点 | 做法 | 为什么 |
|---|------|--------|
| **中文不设 `w:eastAsia` 会掉字体** | `_set_run_font` 同时写 `w:ascii`/`w:hAnsi`/`w:eastAsia` | `run.font.name` 只写前两个，中文走 `w:eastAsia` 槽位 —— 不设它，中文掉回 Word 默认（宋体），与西文混排时字重行高都不一致 |
| **OOXML 元素顺序**（本次最阴险的坑） | `_ordered_insert(parent, el, order)` 按 ECMA-376 序列插位 | `w:pPr`/`w:rPr` 是 **ordered sequence**：`w:shd` 必须排在 `w:spacing`/`w:ind` 前。python-docx 没暴露 `_insert_shd`/`_insert_pBdr`，手写时顺手 `append` 就会乱序 —— **python-docx 照常读得回来，只有 Word 会提示「文件已损坏」**，所以只能自己写检查（见验证） |
| **图片** | 从 `_collect_images` 拿 bytes 直接 `add_picture(BytesIO)`，超版心（6.27in）等比缩放 | 不落盘、不 base64；公式渲染产物那种 `data:image/png;base64,` 也直接解码内嵌 |
| **代码块用段落不用单元格表格** | 逐行 run + `w:shd` 底纹 + 换行符 `add_break` | 单格表格在 Word 里会被周围文本挤压变形、跨页断行不可控；同一段落内换行还能保住缩进 |
| **超链接** | 手写 `w:hyperlink` + `part.relate_to(RT.HYPERLINK, is_external=True)` | python-docx 至今没有超链接 API（issue #74 自 2014 年挂起） |
| **公式** | 保留 LaTeX 原文（`$...$` / `$$...$$`） | 见下 |

### 顺带发现：「公式 → 渲染图片」是一个**从来没生效过**的选项

`_render_formulas_to_images` 依赖 **matplotlib** —— 而 matplotlib **不在 `pyproject.toml`、不在 PyInstaller spec、不在任何文档**里（全仓 grep 只有这一个引用点）。它永远在 `except ImportError: return md` 处静默返回原文。

也就是说：ExportPanel 里「MD 公式：渲染为图片」这个选项，**自上线起选了就等于没选**，且因为降级是静默的，没有任何日志或 UI 反馈。这正是「失败显式化」原则要防的形态。

**本次未修**，原因：修它要给打包版引入 matplotlib（~50MB+），而 HTML 导出那条路已经有 **KaTeX 服务端渲染**（Node 子进程，产物质量远好于 mathtext 近似）。合理的修法是把 md 的公式渲染也切到 KaTeX 图片管线、砍掉 matplotlib 分支 —— 属于独立议题，**已记入待办**。Word 导出不接这个字段（`formula_md` 对 docx 无效），文档与前端均明示「公式保留 LaTeX 原文」。

### 接入点

- **单文件**：`GET /api/export/{id}?format=docx&mode=xxx`（`export_content` 新增分支，既有的 md/html 分支零改动）
- **批量**：`ExportCustomRequest.formats` 加 `"docx"`，`export_custom` 与 `_build_export_zip` 两处循环各加一个分支（这两份循环是**既有重复代码**，本次未合并）
- **前端**：ExportMenu 加「导出 Word」（与 HTML 同门控，**思维导图 tab 除外** —— 交互脑图没有 Word 形态，按钮 title 给出原因）；ExportPanel 格式四选一 + Word 说明块
- **打包**：`LiMoBackend.spec` 的 hiddenimports 加两个新 services 模块；`beautifulsoup4` 从传递依赖**提为显式依赖**（此前靠 markitdown/trafilatura 带入，现在被直接 import，不该再依赖别人的依赖树）

### 验证

- **`backend/tests/test_docx_export.py`：20 PASS**。每个用例都**把产物回读一遍**再断言 —— docx 是 zip+XML，生成时不报错不等于内容正确（样式名写错、字体槽位没设、图片关系没建立，全都能「成功」产出一个 Word 打不开的文件）
- **OOXML 顺序检查是自证的**：先**复现修复前的真实写法**（先设 spacing 再 append shd）确认检查能抓到违规，再断言全要素产物零违规。第一版构造写反了（必须先 append 大下标才违规），被自证步骤当场抓到 —— 这正是「断言存在 ≠ 断言有效」的又一次实践
- **真实数据端到端：16 篇零失败**（video / text / webpage / pdf × notes / polished / transcript），最大一篇 101,868 字 / 32 图 / 33 标题 / 7 表格。全部零顺序违规、零残留 markdown 标记
- 回归：后端离线 10 个测试文件全绿 + e2e smoke 36/36；前端 vitest 98/98 + `npm run build` ✓

### 影响范围

新增 `app/services/markdown_render.py`（公共渲染）、`app/services/docx_export.py`（Word 转换）、`tests/test_docx_export.py`；改 `export.py`（-80 行重复实现 +docx 分支）、`pyproject.toml`、`LiMoBackend.spec`、ExportMenu/ExportPanel。**HTML 与 MD 导出行为逐字节不变**（已用旧版对比验证）。

---

## 2026-09-19（续） — 图表：六个几何缺陷修复 + 图表类型 4 → 9

> 用户诉求：「我准备给我们的图片要生成增加更多的图表类型选择」——方向限定为**关系/结构图**（不做统计图表），**只扩展 LLM 自动选型的类型池，不加前端 UI**。

### 第一部分：压测暴露的五个真实几何缺陷

上一轮落地的语义化布局器用「更复杂的图」压测后，实测暴露五个缺陷。前四个是**算法画错了**，第五个是**报错文案把人带偏**；第六个是**打包验证时看真实产物才发现的静默降级**（单测、压测、断言全过）：

| # | 缺陷 | 根因 | 修法 |
|---|------|------|------|
| 1 | **同行跨列边横穿中间节点** | `[0,2]→[0,0]` 只判 `a.x < b.x`，不看中间隔着谁 | 跨列时改走绕行 `M acx V(out) H bcx V(in)`；绕行侧选择必须保证走廊在画布内（首行不能向上绕、末行不能向下绕） |
| 2 | **走廊里的边全叠在同一个 y** | 水平段 y 恒为两行中线 `(a底+b顶)/2`，7 条边画成一条长横线、3 个标签叠在一起 | 走廊**分道**：按走廊聚类成车道池，同走廊内按序分配车道 y（间距 8px、4px 网格对齐）；**只有一条时仍走中线**，不无谓偏移 |
| 3 | **隔行边穿节点** | `[0,0]→[2,0]` 的中线 y 落在**中间那一行**上 | `validate_spec` 直接拒绝隔行边。⚠️ 文案**不能**写「请改为逐行连接」——那会诱导模型补一个不存在的中间节点搭桥，把「图难看」升级成「图画错了」 |
| 4 | **state 回边沟槽被裁掉** | bbox 只扫 `nodes + labels`，edges/shapes 的横向延伸不参与 | 新增 `_path_bbox(d)`（解析自产的受限路径，只含 M/H/V）；`_snapshot` 与 `_trim_width` 统一按 **nodes + labels + shapes + edges 四类元素**求 bbox |
| 5 | **报错文案误导** | `expand` 的三轮「压行距重试」把**结构性错误**（有环）也当成「高度放不下」重试，最后包装成「节点过多，图过高」 | `best is None` 时**直抛算子给的原因**——压缩行距只会改变高度，改变不了结构。实测：3 个节点带环的 flowchart 曾报「节点过多」 |
| 6 | **窄节点文字被压成纯省略号**（空壳图） | `_fit_labels` 用写死的 `avail - 32` 算可用宽，与 `_shape_inner_w` 的 `<64 → w-8` 分支**不同源**——tree 等比收缩到 `_MIN_NODE_W=48` 时按 16px 算，连「一个中文 13px + 省略号 11px」都塞不下 | `_fit_labels` 的余量改为调 `_shape_inner_w`（两处必须严格同值，这是 `_shape_inner_w` docstring 早就写明的约束）；`_shrink_labels` 补**前置**空壳检查 |

**缺陷 6 的教训（比缺陷本身更值钱）**：tree 带 8 个子节点时 9 个节点**全部**变成 `…`——图渲染成功、几何全对、`assert_layout` 全过、17 个压测用例全过，用户拿到的却是一张一个字都没有的图。**为什么所有验证都没抓到**：① 压测用例的 tree 只有 3 个子节点，没触发收缩到下限的路径；② 第一版修法是给 `_shrink_labels` 加「截断后是否为空壳」的判据——**写了但没生效**，因为 `_fit_labels` 在更上游就已经把 label 截成 `…`，等 `_shrink_labels` 拿到手时 `_measure("…")=11 < 可用宽 40`，**判据的截断分支根本进不去**。这与「1.6 可选参数不传 = 断言不执行」是同一类错误：**判据存在 ≠ 判据有效**。故修复后补了两条正式用例：窄节点不再出空壳 + 造「上游已截空」的输入确认前置判据真能抛错（54 PASS）

**缺陷 1 与 2 必须一次设计**：绕行段向下时进入的正是下一行上方的走廊，两者抢同一片垂直空间，共用一个车道池才不会互相打架。

### `title` 终于渲染了 + 三条新不变量

- **`title` 此前解析了但从不绘制**（`expand` 放进返回值，`chart_svg` 从没画过）。修法：`TITLE_H = 24` **在 `expand` 里计入**（非空则全部节点 `y += TITLE_H`、`height += TITLE_H`）——高度/缩放/断言三处自动一致
- **已决策：接受 3.75% 容量损失（640 → 616），不动 `_MIN_SCALE`**。0.65 对应 13px × 0.65 ≈ 8.5px 已到可读性边缘；标题是**内容而非装饰**，占高度预算语义合理。测试覆盖不减反增：保留 18 节点用例（改后 scale ≈ 0.6504 仍通过），**新增**「同规模 + 带标题 → LayoutError」负向用例
- **`assert_layout` 补三条不变量**：边折线段不穿非端点节点 / 标签遮罩互不重叠 / 标签遮罩不压节点（顺带 edges·shapes bbox 也在画布内）
- **⚠️ 可选参数的陷阱**：`assert_layout(nodes, W, height, edges=None, labels=None, shapes=None)` —— 若 `expand()` 内部那**两处**调用不传全量，新加的三条**永远不会执行**。「断言存在」≠「断言有效」，故额外造了故意违规输入验证它真能抛错

### 第二部分：`chart_layout.py` → 包 + 5 个新算子

**模块重组**（648 行 → 约 1150 行，拆成包，`app.services.chart_layout` 这个 import 路径**完全不变**，测试与 chart_renderer 一字未改）：

```
app/services/chart_layout/
  __init__.py   # 公开契约：显式 re-export（不靠 import *）
  geometry.py   # 常量 · 文本测量 · 网格分布 · 形状几何 · 路径与箭头 · 标签遮罩 · _path_bbox
  layouts.py    # 10 个算子 + _NODES_RUNNERS/_SPEC_RUNNERS/_ROW_GAP
  engine.py     # validate_spec / assert_layout / expand
```

- **节点形状层**：新增可选字段 `shape ∈ {rect, diamond, stadium, circle, bar}` 与 `decor`。`{x,y,w,h}` 始终是**外接矩形**，所有形状内切于它 → **AABB 不重叠断言无需改写**（对外接矩形判不重叠比判形状不重叠更强，对 diamond/circle 依然充分）
- **形状感知文字宽度**：`_shape_inner_w(n)` 取代硬编码的 `inner = w - 32`（rect 分支与原来完全等价）。菱形内接矩形是 `w/2 × h/2`，故 `_shape_min_w` 对 diamond 取 `2 × 节点最小宽`——**用公式保证菱形文字不溢出**，而非靠调参
- **`shapes` 通用通道**：算子可选返回第 4 项，`chart_svg` 在**背景之后、连线之前**渲染。统一原则：**有几何范围但不属于「内容主体」的一律走 shapes**（不占节点数、不进 `_count_nodes`、不参与重叠校验）——象限轴线框、sequence 生命线、分组框全按此归类
- **两张注册表**：`_NODES_RUNNERS = {hchain, timeline, branch}`（入参 nodes）/ `_SPEC_RUNNERS = {tree, flowchart, state, sequence, quadrant}`（入参整份 spec）

**五个新算子**（全部正交路径、零曲线，守住「连线只含 M/H/V」不变量）：

| 算子 | 要点 |
|---|---|
| `tree` | 父子语义**内嵌在结构里**，不需要 `id`/`parent`/`edges`——对 LLM 少一个字段就少一类出错面。新增 `_elbow` 总线连线（`M 父底 V bus H 子心 V 子顶`），tree 默认无箭头 |
| `flowchart` | **字符串 id 而非 grid 的 `[行,列]`**——判定会跳转/汇合，行列索引正是最易写错的字段；id 让越界错误精确定位到 `edges[2].to`。Kahn 最长路径分层 → 可产生跳层边 → 走右侧纵向沟槽 `_GUTTER_W = 20` 绕行 |
| `state` | BFS 分层 + **允许环与自环**。回边走右侧沟槽；自环走右缘 `_LOOP_W × _LOOP_H` 环路。`type=="start"/"end"` 在上下各插一个空 label 的 `circle` 圆点 |
| `sequence` | 参与者等宽同行（解 `avail ≥ n·w + 12(n−1)` 定 cap，超了就报「参与者过多，请拆图」）；生命线/分组框走 `shapes`；消息即时间顺序，自动加序号 |
| `quadrant` | `quadrants` **固定 4 个、按 TL→TR→BL→BR 顺序**（唯一零字段且无歧义的方案）；十字轴线与象限框走 `shapes`——**靠 shapes 参与 bbox 保住了全宽**，不再需要「轴标签贴死右边界」那个一改就失效的 workaround |

**一个设计修正**：`_grid_edge_label` 新增 `nodes` 参数。它默认的「按两心跨度钳制」是给**同层内水平相邻**的边用的近似；state 的前向边是**上下层**，两心 x 常只差十几像素、跨度判据直接判死，而它的水平段其实落在走廊里（周围根本没有节点）。传了 `nodes` 就跳过跨度启发式、改用 `_overlap` 逐个查真实节点——**用事实判断代替几何近似**（实测：state 的三个边标签从只放下 1 个变成 3 个全放下）。

### 第三部分：prompt 从 4 种 → 9 种全展开

`get_chart_format` 由「4 行 schema + 2 个示例」扩成**九行一类型、各带可直接改的最小 JSON**（tree 只到两层、state 只含一条回环）。

**为什么不只是列名字**：flash 模型对没见过的结构**不会自己拼 schema**，最后仍然全用示例里最眼熟的 `hchain`——「扩展类型」也就白做了。示例取**最小可用**即可，够照改就行，不堆字段。

- **约束仍只有 4 条**：是**引导**变长（告诉它有什么可画），不是**约束**变多（要求它必须画成什么样）。2026-08-16 的教训是「堆规则 ↔ 出图率是 trade-off」，本次刻意保持约束面不变
- **来源差异段语义变化**：从「决定展开哪几种」变成「在九种里优先推荐哪几种」
- **新增一句关键约束**：`flowchart / state / sequence 一律用字符串 id 互相引用（不要用下标）`

### A/B 门禁实验（这是本次唯一预设了回退条件的决策）

样本 5 份真实文档（论文 PDF ×1 + 视频转录 ×4，各取前 8000 字符）× 2 组 × 3 次 = 30 次调用（deepseek-flash）：

| 组 | 出图率 | 类型分布 | 平均 prompt |
|---|---|---|---|
| old（4 种） | **11/11 = 100%** | `chart(手写SVG)` × 11 —— **fig 布局 0 次** | 4377 tok |
| new（9 种） | **13/13 = 100%** | hchain 12 / tree 11 / layers 6 / timeline 5 / grid 4 / sequence 2 / flowchart 1 = **7 种** | 4825 tok |

**结论：不触发回退**——出图率零下降（100% → 100%），types 从 1 种扩到 7 种，prompt 只贵了约 450 token（+10%，远小于字符数 +108% 的增幅，因为新增的主要是短 JSON 行）。

**⚠️ 实验副产物（比主结论更值得记）**：旧 prompt 组 **11/11 全部输出手写 SVG 围栏，一次都没走 `fig`**——即旧 prompt 在这一模型上「语义 JSON」协议实际遵守率为 **0**。新 prompt 的九行 JSON 示例反而把「输出 JSON 而不是 SVG」这件事强化了。这是单模型单次实验（且与上一轮「6 张图」实测走 fig 的观察不一致，可能是模型版本/采样差异），记为观察而非定论，但足以说明**示例的具体程度直接决定协议遵守率**。

**实验设计踩坑（记下来免得重蹈）**：首版实验设 `max_tokens=4000`，得出「出图率 7%」——**纯属截断假象**。deepseek-flash 是推理模型，思考段先吃 token，4000 时 `finish=length` 且**正文一个字都没吐**。改为 16000 后 `finish=stop`。统计时按 `finish_reason` 剔除截断样本。

### 影响范围与验证

- ✅ `test_chart_layout.py` **54 PASS**（新增 flowchart / state / sequence / quadrant 四段 + 形状层段 + 空壳图回归段）+ `test_chart_renderer.py` + `test_prompt.py` 全绿；`test_prompt.py` 的布局断言改为**与 `_LAYOUTS` 同源**（将来加算子忘了改 prompt 会立刻挂）
- ✅ 端到端压测 17 个用例（9 类型正常路径 + 8 个边界：有环 / 隔行边 / 参与者超标 / 空象限 / 树太宽 / 超长 label / 未知 layout / 坏 JSON）：**零失败、零围栏残留**，失败路径全部给出可操作中文提示
- ✅ 渲染图肉眼核对：flowchart 菱形文字居中、state 回边与自环绕行、sequence 生命线对齐、tree 总线、象限轴线
- ✅ 缺陷 6 修复前后实测（同一份打包版产出的 `chart_2.json`）：`['…', '…', '…', '…', '…', '…', '…', '…', '…']` → `['大模…', '材料…', '邮件', '笔记…', '注释', '目录', '格式', '参考…', '图表']`——节点压到 48px 仍保留至少一个字，母线连接与等宽对齐正常
- ✅ 打包验证（`chart_layout` 单模块变包后 PyInstaller 模块发现路径变化）：PYZ 归档含 `app.services.chart_layout` / `.engine` / `.geometry` / `.layouts` 四模块；exe 启动 `/health` 200 + `/api/capabilities` 200；**真实生成产出 3 张图**（hchain / tree / state 三布局，`.json` 旁路文件存在即证明走的是 fig 语义路径而非 legacy 手写 SVG）

### 已知遗留（未处理）

- **`_trim_width` 只裁右不裁左**：布局阶段按 640 宽居中，裁剪后画布变窄而**内容坐标不变** → 窄图内容整体偏右、左侧留大空白。实测 flowchart 案例留白 232px（画布 412px，节点中心 312 vs 画布中心 206）。这是**既有行为**（grid/layers 同样，只是新算子内容更窄所以更刺眼）；修法是在裁剪后把内容整体平移回居中，但会改变既有 4 算子的全部产物坐标，未纳入本轮范围
- `quadrant` 与 `state` 在 A/B 实验中一次未被模型选中（样本里没有「二维评估」「状态机」语义，属内容驱动，非缺陷）
- flow chart **拒绝任何环**（含「退回修改 → 重新提交」这种真实流程极常见的形态），需改用 `state` 布局

---

## 2026-09-19 — 图表生成优化：语义化布局 + 多图 + 自然尺寸缩放

> 用户诉求：「画图其实还可以再优化一下，也可以不仅限于只有一张图片。不过多张图片的话生成时间肯定会延后，那么假如是有多张图片必须得考虑适当的延长生成时间」。

### 核心：把「画图」从生成问题变成渲染问题

旧协议让 LLM **手写 SVG**（含坐标、样式、引号），几何错误（重叠/越界/斜线/文字超框）只能靠 prompt 堆规则压制——而 2026-08-16 的教训是：**堆规则与出图率是直接 trade-off**（28 行硬规则让 deepseek-v4-flash 宁可跳过不画）。

新协议让 LLM 只答**语义 JSON**（有哪些节点、怎么排），坐标/宽度/连线/标签遮罩全由后端确定性布局器算——**几何错误从结构上不可能发生**，几何规则也就不必进 prompt。

- **`app/services/chart_layout.py`**（新增，纯几何零依赖）：4px 网格对齐 / 文字宽度注入测量 / 正交路径（禁斜线）/ 掩码定位 / 5 种算子（hchain 内置自动折行 / layers / timeline / grid / branch）。`grid` 是唯一能表达「谁连谁」的算子，故必须保留；`branch` 已实现并单测但**不写进 prompt**（防用上未文档化算子）
- **`app/services/chart_svg.py`**（新增）：面向 PIL 自绘子集生成 SVG，箭头用 `<polygon>` 手绘（PIL 不支持 `<marker>`）。**出口 `_FORBIDDEN_RE` 自检**——`<marker>`/`class=`/`url(#)`/`<defs>` 不是被校验拦住的而是被**静默忽略**的（用了它们照样通过校验、照样落盘，只是箭头无声消失），所以防线在工具内而非测试里
- **⚠️ 字体宽度必须注入**：布局器不可硬编码「CJK 13px/字」——LiMo 的字体是**运行时探测**的（`_detect_chinese_font`），不同字体字宽差 10–20%，硬编码会系统性低估 → 文字超框。解法是 `measure` 参数注入 `_load_font(...).getlength`，与渲染用同一个字体对象

### 多图支持（不再限一张）

- 旧 prompt 明写「**必须**画 1 张最有代表性的图」——硬性 1 张。渲染链路本无数量上限，**瓶颈纯粹在措辞**
- 新 prompt：每个 ` ```fig ` 围栏 = 1 张图，可按需输出 **1~N 张**；单个围栏节点 ≤9，超过**必须拆成多个围栏**
- **协议并存**：` ```fig `（语义 JSON，现行）/ ` ```chart `（手写 SVG，legacy 兜底）。并存三理由：协议隔离原则不变 / legacy 是**免费回退路径**（回滚只需改 `get_chart_format` 一个函数）/ 已落盘的 `chart_N.png` 不受影响
- **按内容形态容错分流**：fig 围栏内若含 `<svg`，直接按 chart 分支处理——模型改用 fig 后 legacy 兜底会自然失效，JSON 一错就是白丢一张图
- **`chart_N.json` 是协议切换的确定性信号**：两条路径都出 PNG 且张数可能一样，只有 `.json`（仅 fig 分支解析成功时落盘）能区分走的是哪条路

### 实测数据（推翻计划的定值公式）

计划原要求「跑 3 图场景实测，取 P95×1.5 定超时常量」。实测（deepseek-v4-flash，约 900 字文档）：

| 指标 | 实测值 |
|---|---|
| 总耗时 | **9.8s** |
| 首 chunk | 5.7s |
| 输出 | 2890 字符 / 1315 chunks |
| **fig 围栏数** | **6 张**（hchain×3 / grid×2 / layers×1）|

**6 张图只用 9.8 秒**，比基础超时 330s 小 33 倍——**P95×1.5 公式的前提（多图显著拉长生成）被推翻**（按公式算得 15s，比基础档还短，显然无意义）。真实原因：新协议下模型只写语义 JSON（每张 130~240 字符），不再手写数百行 SVG，出图几乎不增加成本。真正的耗时主导项是**正文字符数**，图表只在其上叠加约 18% 输出增量。

- 故 `_GEN_TIMEOUT_CHART = 400`（`330 × 1.21`，基础档线性外推 + 安全余量），非实测直接命中值
- **两个入口都要放宽**：`generate()`（非流式，无 `asyncio.timeout`，只受 SDK 约束）与 `generate_stream()`（流式）
- **⚠️ `request_timeout` 必须改两处**：`AsyncOpenAI` 是**惰性构造**的（`__init__` 只置 `self._client = None`，真正构造在 `client` property 里），只改 `__init__` 会「存了没用」——值进了实例属性但 property 里仍写死 330，**超时一点没变且单测照样 PASS**，只有端到端才暴露。已加测试直接盯住 client 构造实参
- 单 chunk 静默期的 `wait_for(anext, 150)` 是**独立**保护，不随此值变化（「330+150」式的推导没有物理意义）

### 实测中发现并修复的两处真实缺陷

- **grid 同行边标签 100% 显示不出来**：列间距固定 `GAP_X=48`，而边标签遮罩需要「文字宽+20+12 间隙」，连 2 字标签都刚好卡住 → 4 字以上**整条消失**（`_grid_edge_label` 返回 None）。prompt 引导模型写的 edge label 白写。修复：`_grid` 用剩余宽度把列间距撑到 `_GRID_GAP_MAX=120`（顺带让网格图铺开，不再挤在画布中间）
- **边标签截断无提示**：截到 8 字后「很长的边标签会被」读起来像模型没写完，而非渲染器截的 → 补省略号

### 用户提议落地：自然尺寸绘制 + 等比缩放

用户中途提议：「我们可以把图片完整的绘制出来然后再缩放到对应的比例」。

- **高度**：超 640 时不再直接失败报「请拆图」，改为按**自然高度**定稿 + 返回 `scale`，由 `render_spec_svg` 落到根 `<g transform="scale(k)">`。原来只能压缩行距然后失败（24 节点 timeline 自然高 960px，改造前直接 LayoutError）
- **`_MIN_SCALE = 0.65` 下限**：缩放会让字号同步变小（13px × 0.65 ≈ 8.5px 已是可读性边缘），低于下限宁可报错让模型拆图，也不画一张糊的
- **宽度也按内容裁剪**（`_trim_width`）：timeline/layers 内容只占画布左侧，只缩高度等于「把空白一起等比缩小」。裁剪后 timeline 640→196px、layers 640→356px。**边标签必须计入裁剪边界**，否则只按节点裁会把标签切出界
- **背景必须画在 scale 组之外**：`width="100%"` 的语义是「铺满画布」，放进组内会被缩成 100%×k，四边留出未填充空白带

### 影响范围与验证

- **导出零影响**：所有导出路径都**只按 markdown 引用收集图片**（`_find_image_refs` → `_collect_images`），从不扫描 assets 目录 → 多图 = 多个 `![](assets/chart_N.png)` 自然带多张；`chart_N.json` / `chart_N.svg` 是纯旁路文件，不写进 markdown、不被收集、不进任何产物（已实测复核）
- ✅ 后端 11 个测试文件全绿（新增 `test_chart_layout.py` 22 断言 / `test_chart_svg.py` 12 断言，扩展 `test_chart_renderer.py` / `test_prompt.py`）
- ✅ 五种 layout **真实渲染**（不 mock）肉眼核对：文字/箭头/蛇形折行/边标签遮罩/缩放后清晰度
- ✅ 前端 `npm run lint` 无 error、`npm run build` exit 0
- ✅ 后端启动 health 200 + capabilities 200

### 既有缺口（本次未动，备查）

`chart_N.svg` **用户实际够不到**：渲染器写它，但 ① 资产白名单 `_ALLOWED_IMAGE_EXTS` 明确拒绝 svg（`/api/assets` 返回 400）② 导出不扫目录所以不会打包。CLAUDE.md 记它「供深度使用时导出矢量图」，但链路上没有入口。若今后要做矢量图导出，是在 `export.py` 加一个「图表源码」选项，与本次正交。

---

## 2026-08-26 — 打包合规 + AGPL/GPL 许可证替换（主 exe 零 AGPL/GPL 库）

> 用户：「打包合规 + AGPL/GPL 许可证替换计划」。核心诉求：**不想源码被公开** → 消除主 exe（PyInstaller PYZ「合并作品」灰色地带）中的 AGPL/GPL 库。义务边界：ffmpeg 等 subprocess 独立进程无传染（附全文即可）；AGPL/GPL 库进主 exe 是风险点 → 替换。分三阶段全部完成。

### 阶段 1 — 合规补文件（零风险）
- **审计发现**：ffmpeg 实为 **GPLv3**（gyan.dev 官方声明，原文档误标 LGPL）；ebooklib（AGPL-3.0）与 html2text（GPL-3.0）全文未随包；magika/markitdown wheel 无 LICENSE；static/katex + markmap 无 LICENSE；前端无 NOTICES
- **新增 `licenses/`**（随包 `_internal/licenses/`）：GPL-3.0.txt（ffmpeg 用）+ Apache-2.0.txt（magika）+ MIT-markitdown.txt（markitdown）+ README；spec `datas` 收集
- **`bin/ffmpeg/` 补** GPL-3.0.txt + README-ffmpeg.txt（gyan.dev essentials 8.1.1 GPLv3、独立进程无传染、源码 ffmpeg.org）
- **`static/licenses/`**：KaTeX（MIT）+ markmap-lib/view（MIT）+ d3（ISC）LICENSE 复制随包
- **根目录 `THIRD-PARTY-NOTICES.md`**：后端 Python 组件 + 前端 16 个运行时依赖 + 离线静态资源 + 独立进程组件四类清单
- **文档修正**：ACKNOWLEDGMENTS ffmpeg 改 GPLv3、CLAUDE/BUILD searxng 明确不随包（AGPL，Rust launcher 检测到安装目录才启用）

### 阶段 2 — html2text（GPL-3.0）替换
- **webpage.py 兜底提取** html2text → **bs4 `get_text()`**（bs4 已是依赖，零新增）：剥离 script/style 后取纯文本，长度 <10 才回退正则去标签
- pyproject.toml + spec hiddenimports/collect_all 移除 html2text

### 阶段 3 — ebooklib（AGPL-3.0）自研替换
- **新增 `app/engines/epub_container.py`**（纯标准库 zipfile + xml.etree + posixpath，~260 行）：
  - container.xml → OPF（metadata/manifest/spine）→ 资源；TOC 优先 EPUB3 nav（properties=nav）回退 EPUB2 NCX
  - `_norm_href` 相对 OPF 目录规范化；**TOC 锚点 key 冲突修复**（keep_fragment 保留 #fragment，同文件多锚点不互相覆盖）
  - **`_parse_nav` 只遍历根 ol**（`_find_first_ol`），修复 nav.iter() 嵌套重复 + level 覆盖 bug
  - EpubItem/EpubContainer 对齐 ebooklib 接口子集（get_metadata/get_items/spine/get_item_with_id/get_toc_map）
- **`epub.py` 改单解析器**：移除 ebooklib fallback（失败显式报错，符合「引擎绑定只取第一个」）；删除 `_build_toc_map` 死代码
- **`_detect_sections` 跳过空白文本节点**：body.children 首 child 是换行缩进 → 每章前产生空 section（修复：`not str(child).strip()` continue）
- **引擎重命名 ebooklib → `epub-container`**：注册表 + `_DOC_DEFAULTS` + config.py builtin_models + capabilities fileTypeMap + 引擎 ID（DocResult.engine）全量替换
- **`_migrate_remove_ebooklib` 启动迁移**：清理旧 config 残留（builtin_models/engine_models/绑定指向旧 MD5）→ 回退默认 epub-container
- pyproject.toml + spec + uv.lock 移除 ebooklib；删除 `licenses/AGPL-3.0.txt`

### 终态
- **主 exe 零 AGPL/GPL 库**；GPL 仅剩独立进程的 ffmpeg（随包附全文合规）；AGPL 仅剩进程外插件 pymupdf4llm + 不随包的 SearXNG
- 验证：自研容器 EPUB3/EPUB2 全链路 ALL_PASS（章节/图片/元数据/TOC）+ 后端 8 个测试文件全绿 + test_epub_pipeline 全通过

### 修复 — 保存编辑产物 bug（打包版实测发现）
- **现象**：点击编辑器「保存」按钮，请求路径变成 `/api/markdown/[object Object]`，后端返回 `MD_EDIT_NOT_FOUND`（文档不存在）
- **根因**：TextareaMarkdownEditor 保存按钮 `onClick={onSave}` **把 MouseEvent 注入** `handleSaveEdit(saveDocId?: string)`——React 调用 onClick 处理器时总是传事件对象，`saveDocId` 收到 truthy 的 MouseEvent → `docId = saveDocId` 变对象 → 模板字符串 `${docId}` 得 `[object Object]`。TS 无参函数（`() => void`）赋给 `onClick` 逆变兼容不报错，静态检查无法发现（同类风险：UnsavedChangesDialog 三个按钮同样直接绑定 props）
- **修复**：① TextareaMarkdownEditor + UnsavedChangesDialog 按钮改 `onClick={() => onSave()}`（不传 event）；② `handleSaveEdit` 加 `typeof saveDocId === 'string'` 守卫（纵深防御，未来任何非 string 注入直接回退编辑态归属文档，不再拼脏 URL）
- **全量排查**：前端 74 处 `onClick={标识符}` 绑定逐一核对签名——仅此一处「handler 第一参数是可选业务参数」，其余均为无参 handler（注入的 event 被忽略，无数据污染风险）
- 影响：编辑产物保存正确落到归属文档（含切文档「保存并离开」路径）

---

## 2026-08-25 — 日志/错误系统全面修复（P0/P1/P2）

> 用户：「检查一下我们目前的日志系统以及错误系统是否足够全面尤其是日志方面」「我建议全部做完」。审查发现：export/ingest 残留 20 处 `HTTPException`（未统一 AppError 体系）、epub 路由零日志、日志风格混用（仅 4 文件用 log. helper，49 文件 stdlib）、chat.py 仅 3 条日志、LogPanel 无搜索。分 P0/P1/P2 三批修完，全部验证通过。

### P0 — 错误体系统一 + 清理
- **export.py 9 处 + ingest.py 11 处 `HTTPException` → `AppError`**（EXPORT_DOC_NOT_FOUND/EXPORT_NO_CONTENT/EXPORT_MISSING_IDS/EXPORT_CONV_NOT_FOUND/EXPORT_TEMP_EXPIRED/INGEST_DOC_NOT_FOUND/INGEST_INVALID_TX_ID/INGEST_UNSUPPORTED_EXT/INGEST_PATH_TRAVERSAL/INGEST_INVALID_FILENAME/INGEST_FILE_NOT_FOUND），移除 `HTTPException` import
- **epub 链路补日志**：路由三端点（generate/task/cancel）+ `epub_pipeline.py`（跳过已成功章、空章节占位、阶段 2 归并开始、map-reduce 分组进度）
- **print 清理核查**：abogus.py 三处 + agent.py 一处 print 均在 `__main__` 自测块/docstring 示例（非业务路径），保留

### P1 — 关键链路日志结构化
- **logging.py helper 增强**：`_emit` 用 `inspect` 取**调用者模块名**注入 logger——此前所有 helper 日志 name 固定 `app.core.logging`，来源模块信息丢失，迁移 stdlib→helper 反而退化。现在每条 helper 日志 name 即来源模块（如 `app.routers.ingest`），配合 rid 可精确定位
- **ingest.py（21 处）/ process.py（14 处）** stdlib logger → `log.` helper 结构化 kwargs（cookie 注入、持久化、封面下载、流式写入、重转录、LLM/Engine 图片处理、Engine 回退链）
- **chat.py 补对话链路日志**：预算分配（C2）、历史压缩触发（C3）、文档注入、历史裁剪、SSE 完成统计（answer_len/tools/refs）；移除不再使用的 stdlib logger
- **agent.py 补工具循环日志**：无工具直接回答、工具轮次、达到 max_rounds 强制整理（warning）、Tool 调用/失败结构化

### P2 — LogPanel 可检索
- **后端 `/api/logs` 加 `search` 参数**：在切片前对全量 1000 条 buffer 过滤（msg 子串，覆盖 request_id/关键字），非最近 N 条
- **前端 LogPanel 加搜索框**：关键字 / request_id 检索，输入变化即时刷新

### 附带修复 — AppError recoverable 与 status_code 解耦（P0 引入的 404 回归）
- **根因**：P0 迁移把 `HTTPException(404)` 改成 `AppError(status_code=404, recoverable=True)`，但 main.py 异常处理器用一维映射 `200 if recoverable else status_code` 把显式 404 也压成 200 → 删除文档后 GET 返回 200 而非 404，e2e「deleted doc -> 404」失败
- **修复**：处理器改为「显式 status_code（≠默认 400）优先尊重，未显式时 recoverable→200」。recoverable（是否预期业务状态）与 status_code（HTTP 语义）是两维独立概念，不再耦合。前端 `isRecoverable` 对 recoverable 404 仍静默处理，行为兼容

### 验证
- 后端 9 个测试文件全绿（重点 test_e2e_smoke 36/36，含 deleted doc -> 404）
- 前端 tsc 通过 + lint 零 error（存量 exhaustive-deps warning）+ vitest 98/98

---

## 2026-08-25 — 提交后代码质量/健壮性审查（开发者视角）

> 用户：「提交然后再进行一次代码质量和健壮性审查，我们需要从开发者的角度去看」。审查覆盖 5 个域：视频直传、编辑未保存保护、日志系统、错误体系、全局模式（并发/取消/资源/边界）。两个并行审查 agent（编辑保护 / 日志错误）+ 主线程人工核查，共确认并修复 3 个高危数据污染/损坏 bug + 若干中低项。

### 🔴 高危修复
- **H1 export_custom 显式 400 被压成 200 → 前端下载损坏 zip**（export.py:1258 `status_code=400, recoverable=True`）：main.py 处理器用「≠默认 400」判断是否显式指定，显式 400 落入 `recoverable→200` 分支 → 前端 ExportPanel `if (!r.ok)` 把 200 当成功 → `r.blob()` 拿到 AppError JSON → 下载损坏 zip + 成功 toast。**根治**：AppError 默认 status_code 改用哨兵 `_UNSET` 并记录 `_explicit_status` 标记，处理器只对「未显式指定且 recoverable」压 200，显式 400 必须返回 400。这是「≠默认值」判断法的设计盲区（M1），未来新增显式 400 不会再踩
- **H2 切文档弹窗打开时 Ctrl+S 把原文档编辑内容保存进新文档**（PreviewArea）：旧实现 window.confirm 是同步模态阻塞事件循环，弹窗期间 Ctrl+S 不派发；改 React 异步弹窗后该出口暴露——`selectDoc(B)` 把 `currentDocId` 切成 B，但编辑态仍属 A，Ctrl+S 无参保存用 `currentDocId=B` 把 A 的内容写入 B。**根治**：编辑态 `editing` 绑定来源 `docId`（`{mode, docId}`），所有无参保存目标 = `editing.docId`（非 currentDocId），弹窗打开时拒绝无参保存（Ctrl+S 属编辑器快捷键，弹窗「保存并离开」显式传参不受影响）
- **H3 编辑态不绑定来源文档 id，弹窗期间二次切文档错存**（PreviewArea）：弹窗 `prevDocId` 来自 `prevDocIdRef`（上一次文档 id），弹窗打开期间经 AI 浮窗（z-index 同级且 DOM 后渲染）再切文档 C 时，effect 用「中间文档 B」覆盖弹窗 → 保存/回退错指。**根治**：弹窗 `prevDocId` 用 `editing.docId`，保存目标 = `editing.docId || prevDocId`，取消回退也用编辑来源
- **H4 视频直传 prompt="" bug**（content_preparer.py:254 传空串覆盖默认画面理解指令 → 模型收到空指令）：`understand_video_direct` 加 `prompt or VIDEO_UNDERSTAND_PROMPT` 防御 + 调用方改传 None
- **H5 视频直传流式单产物路径静默失效**（generate.py 流式端点单产物分支漏传 `video_direct` → 只选 notes/polished 时直传失效走截帧，模式 #16「prepare 参数对称」同类复发）：补 `video_direct=video_direct`

### 🟡 中低项修复
- **非流式生成端点漏 `llm.aclose()`**：每请求新建 LLMService，请求结束不关 AsyncOpenAI client → httpx 连接池常驻累积（流式端点 P1-7 已覆盖，非流式遗漏）。finally 补 aclose
- **`effective_fps` 长视频帧数超限**：DashScope fps 有 0.1 下限，视频 >1280s 时 `max(0.1, ...)` 使总帧数超 MAX_FRAMES——SDK 硬约束下的尽力而为，注释澄清 + 超限 warning
- **`/api/logs` lines 非数字 500**：裸 `int()` 抛 ValueError，try 兜底默认 200
- **video_understand.py stdlib logger → log. helper**（新文件违反模式 #35）；agent.py 残余 stdlib logger 混用清理
- **process.py 重复 `router = APIRouter()` 声明**清理；`_to_file_uri` 注释笔误（实现 `file:///D:/` 三斜杠，注释两斜杠）

### 审查确认无问题的项（未改动）
- AppError 子类（TranscribeError/GenerateError 等）默认 400 + recoverable 语义不受 sentinel 影响；e2e「deleted doc -> 404」保持通过
- 视频下载/截帧临时目录清理、Cookie 临时文件删除、LLM 连接池关闭（process.py 全出口）、流式双产物 gather 顺序解包、信号量释放逃逸防护均正确
- dashscope `MultiModalConversation.call` 无 per-call base_url（走全局 `dashscope.base_http_api_url`），video_understand 的 base_url 参数是合理的防御设计

### 遗留建议
- ✅【中】**编辑中未保存 × 重新生成拦截**（已实施 2026-08-25）：GenPanel `handleGenerate`（全部生成/重新生成/继续生成入口的唯一汇聚点）检查 `dirtyEditingDocId`（PreviewArea 将编辑态暴露的归属文档 id，仅标记不提升编辑态）→ 匹配当前文档时两态 confirm「放弃编辑并重新生成？（可先保存后再生成）」，确定 → 清标记 → PreviewArea effect 自关闭编辑态（生成完成后编辑框不再开着旧值，杜绝旧编辑静默写回新产物）。轻量桥方案：uiSlice 加 `dirtyEditingDocId` 字段 + PreviewArea 两个同步 effect + GenPanel 一处拦截，零跨组件状态提升
- 【低】「取消」回退后 activeView 可能被 tab 有效性 effect 跳走，点回原 tab 再弹窗（跨 sourceType 切文档才触发）
- 【低】logging._val 对 dict/list 不截断（`agent.py` Tool 调用 args 可能夹带长文本/隐私）；`_caller_name` 帧栈遍历开销建议长期改用 LoggerAdapter 注入

---

## 2026-08-25 — 编辑未保存保护：三态弹窗（保存并离开/不保存/取消）

> 产物编辑（2026-08-15 textarea 版）原有 `window.confirm` 覆盖保护，但同步阻塞模态掩盖了「弹窗期间 Ctrl+S / 切文档」的危险出口。改 React 异步三态弹窗 + 编辑态绑定来源 docId，根治跨文档错存。

### 实现
- **三态弹窗**（`UnsavedChangesDialog`）：编辑中未保存离开 tab/文档 → 「保存并离开 / 不保存 / 取消」；保存失败留在当前可重试
- **编辑态绑定来源 docId**（`{mode, docId}`）：所有无参保存（Ctrl+S/保存按钮）用 `editing.docId` 而非 `currentDocId`，弹窗打开时拒绝无参保存——根治切文档弹窗期间 Ctrl+S 把 A 的内容存进 B
- **弹窗期间二次切文档**（经 AI 浮窗）：prevDocId 用 `editing.docId`，保存/回退不错指中间文档
- `beforeunload` 拦截关页；三态弹窗为 PreviewArea 独立组件

---

## 2026-08-25 — 视频直传模型 阶段 A（Qwen 打通，DashScope SDK 原生通道）

> 主流模型原生支持视频输入，服务端抽帧比本地 ffmpeg 截帧更稳定（自动选帧），省截帧调参/拼图/质量依赖。方案 [pi-docs/视频直传模型方案.md](pi-docs/视频直传模型方案.md) 已定稿。本地视频 >7MB 无法走 OpenAI 兼容（base64 不实用），必须走 DashScope SDK `MultiModalConversation` 原生通道（file:// ≤100MB）。

### 实现（增量，不替代）
- **视频直传 = 独立「视频画面理解」预处理**，不混入生成调用：视频(≤100MB) → SDK 视频理解 → 描述文本注入 `clean_text` → 生成调用照常（流式保留）
- 触发条件：开关开启 ∧ 模型 capabilities 含 `video` ∧ 本地视频 ≤100MB；失败/超限 → **回退截帧链路（原封不动）**
- 新增 `app/services/video_understand.py`（`understand_video_direct`，同步函数 `asyncio.to_thread`，fps 0.1-10 SDK 硬约束，>1280s 长视频尽力而为）
- config 增量标注 dashscope 视频模型（qwen3.7-plus/qwen-vl-max/qwen3-vl-flash 加 `video`；新增 qwen3.8-max）；opencode 网关不标（第三方 video_url 支持未知）
- 前端：GenPanel 视频区块「视频直传模型处理」勾选（无 video 能力禁用 + tooltip），截图间隔/拼图/质量参数置灰；模型选择能力标记「视频」
- LLMService / generator / 流式 SSE / 截帧链路 / ASR **零改动**（直传是 prepare 阶段一次非流式理解）

---

## 2026-08-17 — 新增内置厂商 OpenCode Go（OpenAI 兼容网关）

> 用户要求在设置页「大模型厂商 / 引擎厂商」支持 OpenCode Go。阅读官方文档确认：**OpenCode Go 不是引擎服务，而是 OpenAI 兼容的 LLM 网关**（`opencode.ai/go/v1`、`/chat/completions`、Bearer Key，与 OpenCode Zen 同平台共用 `OPENCODE_API_KEY`，Go 固定订阅 5h/$12·周/$30·月/$60，Zen 按量付费）。定位为**内置 LLM 厂商 + 图片处理（image_process 场景）可用视觉模型**。

### 新增
- **`PROVIDER_DEFAULTS` 注册 `opencode` 内置厂商**（`https://opencode.ai/go/v1`）——设置页 LLM 厂商区自动出现，前端零改动（providers 由 `_get_providers` 自动补全内置）
- **`llm_models` 预置 chat-completions 系模型**（官方 docs/go 清单，GPT/Grok 走 Responses API 与 LiMo 的 chat.completions 不互通，不进预置）：
  DeepSeek V4 Pro/Flash、Qwen3.8/3.7/3.6 Max/Plus、GLM-5.1/5.2/5.3、Kimi K3/K2.7 Code/K2.6、MiniMax M3/M2.7、MiMo-V2.5
- **图片处理可用**：Qwen/GLM 高版本预置标 `["text","image"]`——图片处理/OCR 场景可选（网关实际能力以调用结果为准，不支持时在设置页取消「多模态」标记；未知模型默认 text 可手动标）
- **连接测试兜底**：`test_connection` 对不实现 `GET /v1/models` 的网关（OpenCode 等）改用最小 `chat/completions` 请求真正验证 key/base（404/405 → POST 探活）；模型刷新失败自动回退预置 list（原逻辑保留）

### 影响
- 生成/对话/图片处理三个场景均可选 OpenCode Go 模型；Key 在设置页填写后立即生效
- 订阅制有请求限额（5h/周/月三级），超限后网关返回错误，属服务方限制

---

> 用户：「图片还是有错位之类的东西」。上一轮多边形闭合是皮外伤，这轮深挖到两个**系统性**渲染缺陷：**`<g transform>` 元素整块不画 / 画错位** + **节点内文字垂直不居中**。这是「看起来错位、缺东西、不如原仓库好看」的主要来源（原仓库 diagram-design 的规范大量用 `<g transform>` 定位 + `dominant-baseline=middle` 让文字在节点内居中）。纯 PIL 内补齐，不引入外部依赖。

### 根因
- **`_svg_to_pil` 遍历时直接跳过 `<g>`**：`transform="translate(...)"` 完全不被解析，g 内子元素（连线/标签/分组节点）要么整块不画、要么画在原始坐标 → 视觉错位/缺内容。qwen-vl 实测：`<g transform="translate(120,60)">` 内的连线+标签整体不可见
- **`_draw_text` 把 SVG 文字 y 一律当基线**：`dominant-baseline="middle"`（节点内垂直居中的标准写法）被忽略，文字按基线画偏下 → 节点标签下方偏移感

### 修复
- **`_parse_transform` + `_mat_mul`**：SVG transform（translate/scale/rotate/matrix，空格或逗号分隔组合）→ 2×3 affine 矩阵；矩阵按文本顺序从左到右复合（SVG 语义）
- **`_svg_to_pil` 改递归遍历**：`<g>` 滚入子元素并累计 transform；`_map(x,y,m)` 用户坐标（含变换）→ 像素统一映射。全部绘制函数（rect/text/line/path/circle/ellipse/polygon/polyline）坐标改走 `_map`
- **旋转/斜切兜底**：rect/ellipse 在含旋转/斜切时退化为多边形近似（`draw.line` 闭合描边；漏画圆角优于画错位置），无旋转保持原 PIL 绘制（rounded_rectangle/ellipse 观感不变）；字号/笔宽按 transform 局部缩放因子自适应（`tots = scale * lscale`）
- **`_draw_text` 支持组合锚**：`text-anchor`（start/middle/end → PIL l/m/r）× `dominant-baseline`（middle/central → 中线 m、hanging/top → ascender、text-after-edge → descender，默认基线 s）组合成 PIL 双字母 anchor——`text-anchor="middle" dominant-baseline="middle"` 真正水平+垂直居中

### 验证
- 像素断言：`<g transform="translate(120,60)">` 内连线+标签出现在平移后区域 2356px / 原位 0px（修复前原位也空 = 整块丢失）；`dominant-baseline=middle` 文字 bbox 中线 410 vs 节点中线 400（基线画法偏 ~20px）
- **qwen-vl 识图复验**：标签位于预定位置（不在原点）、盒内文字居中、无缺元素无错位
- test_chart_renderer.py 追加 transform translate + dominant-baseline middle 两项回归断言；9 个后端测试文件全绿

### 影响
- 补上 SVG 子集最常用的定位手段（transform），LLM 按原仓库规范输出 `<g transform>` 时节点/标签不再消失或错位；节点内居中文字对齐
- 仍不解析的 SVG 特性（transform 内的复杂元素、A 椭圆弧、defs/marker 等）退回「忽略或近似」（漏画优于画错）；临时验证文件 `_transform_demo.png` 留存供查看

---

> 用户在类型示例图（`_type_demo_flowchart.png`）中发现流程图错位、边框缺失。定位到渲染器 bug：**`_draw_poly` 的 polygon 描边用 `draw.line(pts)` 不闭合**——SVG 语义中 polygon 最后一点要回到起点，draw.line 只画点间连线，导致菱形/多边形缺「最后一点→首点」那条边（流程图菱形只剩 3 条边，视觉上既缺框又像错位）。用 qwen-vl-max 识图复验：5 个节点边框全部完整、文字居中、连线无断头。

### 修复
- **`_draw_poly` polygon 描边闭合**：`closed = pts + [pts[0]]`，polyline 保持不闭合（SVG 语义区分）。菱形/箭头等带 stroke 的 polygon 四条边完整
- 示例流程图 SVG 自身漏画两条流程线（开始→输入数据、输入数据→菱形）——VL 识图抓出，补全（非渲染器问题）

### 验证
- 像素分析：菱形 4 边 402/393/480/442px（修复前左上边仅 146px）
- **qwen-vl-max 识图**（新增临时工具 `_vision_check.py`，dashscope 多模态看图代替人工像素）：4 张类型示例图（流程图/时间线/结构树/架构图）全部识别出正确类型、元素与连接，无缺边框无错位
- test_chart_renderer.py 追加 polygon 闭合回归（闭合边 171px）；9 个后端测试全绿

### 影响
- 带描边的多边形（决策菱形、箭头、分组框）完整闭合，图表不再出现「缺边残缺」
- 新增 `_vision_check.py` 临时识图工具（qwen-vl 看图描述），可复用验证渲染效果

---

## 2026-08-16 — 图表连线补齐：贝塞尔曲线（C/S/Q/T）采样渲染 + 手写示例图全类型实测

> 用户：「各种连线可以正常生成吗我们手写的，你最好再搞几个示例图片看看」。构造 10 种连线类型示例图实测，暴露 PIL 自绘渲染器短板：**曲线命令此前跳过参数漏画**（图表连接线用曲线时整条消失），其余（line 直线/虚线、path 折线/虚线、polyline、polygon 箭头、相对命令）均正常。

### 补齐
- **`_path_polylines` 增加 C/S/Q/T 贝塞尔采样**：C/S 三次（`_bezier3`）+ Q/T 二次（`_bezier2`）各采样 24 段为密集折线点（虚线按弧长分段自然兼容）；S/T 平滑曲线的控制点用上一控制点反射（`_reflect`，无上一控制点时用当前点）；Z 闭合回子路径起点。A（椭圆弧）保持跳过——图表基本不用，漏画优于画错
- 相对曲线命令（c/s/q/t）一并支持（参数相对命令起点偏移）

### 验证
- 三张手写示例图存 `backend/_line_demo_{1,2,3}.png`（临时文件，用户判别后删）：① 连线类型总览（line/虚线/折线/C 曲线/Q 曲线/polyline/箭头/C 虚线）② S/T 平滑 + 相对 q/t + Z 闭合三角形 + C 虚线 ③ 模拟 LLM 真实流程图（节点 + 竖直线/水平折线/虚线/C 竖线/贝塞尔曲线 + 箭头），像素分析全部画到预期区域
- 初测用强调色 #eb6c36（RGB 和 397）时深色阈值 300 误判漏画——曲线其实已画出，检测阈值放宽到 600 复测全过
- test_chart_renderer.py 追加曲线渲染回归（C/Q/S/T 真实渲染断言，2192px）；9 个后端测试文件全绿

### 影响
- 图表曲线连线不再消失；A 椭圆弧仍不画（图表未用到，漏画优于画错）

---

## 2026-08-16 — 图表渲染 resvg→PIL 切换：resvg 全系在 Windows 无法渲染 SVG 文字（实测定案）

> 用户用两条路径生成对比图判别：「后端生成的没有文字，PIL 有文字」——此前的「resvg 有系统字体 fallback 链」结论被彻底推翻。深挖 resvg-py 与 resvg CLI（cargo 编译 + 官方二进制）实测：**根因是 resvg/usvg 在 Windows 的 text 渲染 bug，不是字体配置问题**。

### 根因（实测证据链）
- **resvg-py 0.4.0**（Python 3.12 唯一可用版本）：`font_dirs`/`font_files` 传真实字体路径全部无效，连英文字符都渲染为 0px，日志 `No match for "Times New Roman"/"Arial"`——fontdb 匹配不上
- **resvg CLI v0.48.1**（cargo 编译 + `system-fonts` feature）：`--list-fonts` 正常列出 Arial 等系统字体（fontdb 加载成功），但 usvg 渲染一律 `No match for "<font-family>"`——默认 / `--font-family` / `--use-fonts-dir` / `--use-font-file` 四种方式全试均失败
- **resvg CLI v0.47.0**（官方 Windows 二进制）：同样 `No match`，文字 0 深色像素
- 佐证：v0.48.1 release **无 Windows 二进制**（Windows CI 构建失败；v0.47 及更早有）——resvg 在 Windows 的 text 问题是上游普遍 bug，非个别绑定缺陷

### 方案：PIL 自绘 SVG 子集渲染器（`chart_renderer.py`）
- **`_render_png` 从 resvg 切 PIL**：新增 `_svg_to_pil`（rect/text/line/path/circle/ellipse/polygon/polyline 子集），非子集元素忽略（漏画优于画错）；text 用系统探测中文字体文件路径渲染（PIL `anchor` 基线锚——SVG text 的 y 即基线）
- **修复 path 虚线不画**（用户实测「PIL 的没有一些连接线」）：SVG path 命令与数字粘连（`M470 90 V130`），此前 split() 解析漏命令 → pts 全空 → 不画。改正则 tokenizer（`_path_polylines`）分离命令/数字，支持 M/L/H/V/Z + 相对命令 + 隐式 L，曲线命令跳过；`stroke-dasharray` 虚线按弧长分段绘制
- **`_detect_chinese_font` 改返回字体文件路径**（PIL 需要；此前返回 resvg 注册名）+ 按 `(path,size)` 缓存字体对象
- **零新依赖**：PIL 已随图片优化打包；**resvg_py 收集已从 LiMoBackend.spec 移除**（此前需显式 glob `resvg_py/*.pyd` 进 binaries + hiddenimports）
- 校验链 / 安全过滤 / SVG 片段保存 / 失败标注逻辑**全部不变**

### 验证
- 用户实际 SVG（`7 分钟了解 10 种 RAG 策略` chart_1.svg）渲染：文字条带深色 2951/3844 + path 虚线竖线/横线 248/562（resvg 全系为 0）
- test_chart_renderer.py 新增**真实 PIL 渲染回归用例**（不 mock `_render_png`：文字 + path 虚线必须渲染）；全部 9 个后端测试文件全绿
- 中文字体探测成功（msyh.ttc）

### 影响
- 图表渲染不再依赖 resvg_py（打包体积略减，去掉一个 .pyd + 收集配置）
- 取舍：PIL 只覆盖 prompt 约束的 SVG 子集；LLM 若输出超子集元素（image 等）→ 忽略该元素（漏画优于画错）

---

## 2026-08-16 — 图表文字问题定案：字体非根因 + 保存 SVG 片段 + 约束精简（修正上一条）

> 上一条「resvg 不跨字体 fallback 字形」的根因结论**被实测推翻**。用户连续反馈"图表没文字"后实测：resvg fontdb **有系统字体 fallback 链**——`sans-serif`/不指定字体都能渲染中文（文字中心深墨 1032px），显式 YaHei 或渲染参数映射更清晰（1568px）。字体不是"没文字"的根因。

### 修正
- **移除脆弱的字符串替换 `_ensure_chinese_font`**：正则只匹配双引号 `font-family="..."`，LLM 用单引号/style 内联/CSS 类写法全部漏掉——即使字体是根因也覆盖不全。删除该函数与 `_FONT_FAMILY_RE`
- **改 resvg 渲染参数**：`svg_to_bytes(sans_serif_family=font, font_family=font)`（`_detect_chinese_font` 保留，探测失败用 resvg 默认，仍有 fallback）——不修改 SVG 内容，所有字体写法全覆盖
- **prompt 去掉 font-family 约束**（用户"不需要指定字体"）+ **图表约束精简压缩**（用户反馈"生成太久"）：样式/节点/显示规则合并为 ~5 行核心 + 最小示例（去 font-family），**强制只画 1 张最有代表性的图**（原 1-2 张 → 图表 SVG 输出 token 减半，最大提速点）；注入段从 ~1488 字符压到 ~826 字符
- **保存 SVG 源码片段**（用户提议）：`_process_svg` 渲染成功同步写 `assets/chart_{n}.svg`——①调试：文字没渲染时能查看 LLM 原始输出（产物零残留设计此前把 SVG 直接丢了，无从取证）②用户深度使用时导出矢量图。不用于展示（assets 白名单无 svg）

### 真正的"没文字"根因（尚未定位）
- 既然 resvg 渲染中文没问题，"没文字"指向 **LLM 输出的具体 SVG**（文字 fill 与背景同色 / 文字被 rect 覆盖 / 字号或位置异常 / visibility 等）。保存 SVG 片段后重新生成即可取证定位

### 验证
- 真实 resvg 端到端冒烟：```chart 围栏 → `![图表](assets/chart_1.png)` 引用 + chart_1.png（640x320，中文深墨 3761px）+ chart_1.svg（含中文源码）三通
- test_chart_renderer.py（新增 SVG 保存断言）+ test_prompt.py 全绿

---

## 2026-08-16 — 图表文字无法显示根因定位 + 渲染层中文字体兜底（未打包）

> 用户反馈：生成的图表"没有文字/文字无法正常显示"。与上一条「LLM 没写 `<text>`」是两码事——本次**文字其实画了但渲染不出来**。

### 根因（resvg 不跨字体 fallback 字形）
- resvg 的 fontdb 按 `font-family` **精确匹配**，找不到时不回退到其他字体补字形。prompt 用 `sans-serif` → Windows 上 resvg 映射到 **Arial**（无中文字形）→ 所有中文渲染成空白/豆腐块。批量字体对比实测：sans-serif 中文深墨仅 147px（稀疏/豆腐块），Microsoft YaHei 492px（正常）
- 与「LLM 没写 `<text>`」叠加：即便 LLM 认真写了文字节点，渲染层也画不出来——这就是用户反复看到无字图的完整链条

### 修复
- **渲染层中文字体兜底**（chart_renderer.py）：`_render_png` 渲染前调 `_ensure_chinese_font()`——把 `<text>` 的泛型/西文 `font-family` 替换为系统探测的中文字体。跨平台候选：Windows `Microsoft YaHei/SimHei/SimSun`、macOS `PingFang SC`、Linux `Noto Sans CJK SC/WenQuanYi`；PIL `ImageFont.truetype(path)` 探测 + 进程内缓存（每进程只探测一次）
- **尊重 LLM 明确指定**：`font-family` 已含中文字体名（YaHei/SimHei/PingFang/Noto 等）则原样保留，不二次替换；探测失败回退原样渲染不崩溃
- **校验链加强**（同批）：`<text` 打开标签后必须有**非空白文字内容**——空 `<text></text>` / 自闭合 `<text/>` 与零文字图一并拦截（此前只查标签存在，空 text 会通过校验却渲染出无字图）
- **prompt 不动**：用户要求「通用简单字体」→ prompt 保持 `font-family="sans-serif"`，具体字体由渲染层兜底决定（LLM 无需知道平台字体名）

### 验证
- 实测修复效果：文字区域深墨像素 1255→2104px（1.7x，全图统计被节点描边稀释故取文字区域对比）
- test_chart_renderer.py 新增 3 组用例（mock 字体探测，跨环境稳定）：泛型 sans-serif 替换 / 明确中文字体保留 / 探测失败原样返回 + 空 text/自闭合 text 校验失败
- 后端 9 个测试文件全绿，无回归

### 待用户验证
- 当前用户环境 7414 是旧后端（缺 resvg_py）+ 5173 被 file-viewer 项目占用。需处理残留进程后 `bash start.sh`（7413 + reload + `.venv`）重新生成图表，确认文字正常显示

---

## 2026-08-16 — 生成警告弹窗修复（未打包，0.4.1 基础上追加）

> 用户反馈：每次生成都弹「当前内容类型不支持：保留文档图片」+「图表未生成」两个警告，且日志无记录。

### 修复
- **「当前内容类型不支持」不再被跨类型残留骚扰**：`formatImages`/`formatLink`/`formatScreenshot`/`formatReferences` 等被 genPrefs 跨文档类型持久化，切到 UI 不支持该格式的类型（如 PDF 勾过「保留文档图片」→ 文本/视频）时，`droppedFormats` 检测到残留 → **每次生成都弹 toast**。修复：只对「当前类型 UI 可见」的格式弹提示（镜像 GenerateOptions 显示条件），UI 不可见的残留静默忽略（该类型本就不支持、用户无法操作）；全量被过滤格式 `console.warn` 记录便于排查（GenPanel.tsx）
- **图表约束强化**：`get_chart_format` 从「可选生成…不适合则明确说不画」改「内容适合时必须生成；判断不适合时**不得静默跳过**，须输出 `> 图表说明：内容不适合图表化`」——消除 LLM 合法跳过路径，让「跳过」显式化（prompt.py）
- **图表约束二次精简**（同日下午）：实测 flash 模型（deepseek-v4-flash）多次不画——28 行质量硬规则（防重叠 6 条/复杂度预算/布局约定/≤300 行）创作成本过高，模型宁可跳过。移除质量硬规则，压缩到 8 行核心（必须生成/chart 围栏/viewBox 一致/安全红线）+ **最小可行动例模板**（2 节点流程图，模型照抄门槛极低）。质量略降但产出率大幅提升；防重叠/4px 网格等**质量**规则回归原仓库建议层（参考仓库 `C:\WorkPlace\_pi\explore\diagram-design\`）；但「正常显示」底线约束保留——二次精简删过头，见下条
- **warning 补日志**：generate.py 生成开始记录 `source_type/formats`；两处「图表未生成」warning 加 `log.warning` 并区分「模型判断内容不适合图表化」（检测到声明）vs「LLM 未输出图表围栏」，warning 消息同步区分——前端不再收到笼统的「LLM 未输出围栏」
- **加回「正常显示」必要约束**（当日下午，用户反馈「图表里没有文字」）：二次精简删过头，模型产出纯框线无文字图。先用 `.venv` resvg 实测**能正常渲染中英文**（排除缺字体论）——根因是 LLM 没写 `<text>` + 校验链只查形状不查文字。从原仓库 diagram-design（`C:\WorkPlace\_pi\explore\diagram-design\`）提取「至少能正常显示」必要约束回注 prompt：① 背景不透明（铺满 `<rect width="100%" height="100%" fill="#ffffff"/>`，透明底深色主题下看不清）② 元素不越界、四周留 ≥16px 边距、文字不超节点边界（超出被裁剪）③ 相邻节点间距 ≥16px 不重叠 ④ 每个节点必须有文字、字号 ≥12、深字浅底、节点内居中。校验链加**「无文字说明」兜底**（有形状但零 `<text>` → 校验失败替换为失败文本，不渲染无字图）。
- **复用原仓库样式系统**（用户要求，同日）：不只提取约束，把参考仓库 editorial 视觉语言整体注入 prompt——背景 `paper #f5f5f5`（不透明铺满）、主文字/描边 `ink #2d3142`、次要文字/连线 `muted #4f5d75`、强调 `accent #eb6c36`（≤2 焦点）、节点=白底 `#ffffff` + `rx=6` 圆角 + ink 描边 1px、节点名 `sans-serif` 12-14px 600 字重居中（Geist/Instrument Serif 换系统 sans-serif——resvg 无 Google 字体）。示例模板整体升级为该样式。实测渲染 640x320 PNG：文字像素 1.44%、角落 (245,245,245) 背景不透明。注入后 ~1488 字符，仍是单条短规范；只搬视觉样式，不搬 4px 网格/正交连接器/复杂度预算——未重蹈 28 行覆辙

### 诊断结论（约束是否有问题）
- 注入链路完整（前端序列化 → 后端 format_list → hard_constraints 注入）；**约束本身没有 bug**，但「可选生成」措辞给了 LLM 合法跳过路径 + 用户用 deepseek-v4-flash（fast 模型）画复杂 SVG 遵循度低——3 次生成零图表输出（连 svg 围栏都没有）。建议图表功能用更强模型（deepseek-v4 / qwen 大模型）

### resvg_py 缺失（用户实测日志 `No module named 'resvg_py'` 暴露）
- 用户日志实锤 **LLM 其实输出了 chart 围栏**，问题断在渲染链路。双根因：
  1. **start.sh / restart-backend.sh 用裸 `python`**（PATH 里的系统 Python，缺 backend/.venv 的项目依赖）——开发模式后端进程 import 不到 resvg_py。修复：两脚本显式用 `.venv/Scripts/python.exe -m uvicorn`（start.sh + restart-backend.sh）
  2. **PyInstaller spec 收集不到 resvg_py**——Rust 编译的 `resvg_py.pyd`（2.3MB）在包内，`collect_all` 的 `collect_dynamic_libs` 识别不到（实测 binaries=0），打包产物 `_internal/` 无 resvg_py。修复：spec 显式 `glob('resvg_py/*.pyd')` 进 binaries + hiddenimports 加 `resvg_py`/`resvg_py.resvg_py`
- 渲染链路验证：`.venv` 下示例 SVG 渲染出 `chart_1.png` ✓；已跑 PyInstaller 打包确认含 resvg_py（本次**未打包发布**，用户开发模式测试中）
- **待用户验证**：重新 `bash start.sh` 重启后端后再测图表

### 验证
- 后端 11 测试文件 + 前端 98 tests + build + lint 全绿（lint 仅存量 exhaustive-deps warnings）

---

## 2026-08-16 — 图表围栏内部协议收紧（对齐协议设计说明定稿）· 本次打包 0.4.1

> 依据 `pi-docs/图表围栏内部协议设计说明.md`（设计定稿）收紧图表识别协议。核心取舍：**误伤用户内容（静默改写用户数据）不可接受 > LLM 偶尔不遵循围栏约定（warning 重新生成）可接受**——识别从「启发式猜测（双条件/svg 根）」改「协议显式匹配」。

### 协议变化
- **只认 ````chart ```` 专用围栏**：删 chart/svg/xml 三选 + 双条件检测——svg/xml 是通用语言标记，用户内容出现概率高；chart 是专有标记，概率≈0，零误伤
- **删裸内联 `<svg>` 兜底**：围栏外裸 svg 不再渲染——处理面收敛到协议内，用户自己粘贴的 SVG 示例/富文本复制不再被静默改写
- **失败态零残留**：校验/渲染失败由「保留原代码块 + ⚠️ 标注」改为「**替换为** `📊 图表生成失败（原因）` 文本」——产物（存储/展示/导出 MD/HTML）永不含围栏，内部协议不外泄
- **prompt 收紧**：`get_chart_format` 明示「必须用 ````chart ```` 围栏；不要用 svg/xml 围栏，不要在围栏外输出裸 `<svg>`」
- **prompt 硬性规范前移**（质量检查延伸，2026-08-16）：代码校验链保留为兜底，另把 3 处「LLM 产出质量」约束前移到 prompt——① viewBox 与 width/height 必须同时给出且一致 ≤640×640（收窄 P0-2 尺寸绕过面，超限直接失败）② 单图源码 ≤300 行（减少 P1-2 长度上限触达）③ 安全红线显式清单（script/on* 事件/外部 URL 引用/animation/foreignObject——后端会过滤，含此类可能渲染失败）+ 围栏内只放 svg、图后不残留代码（减少 `_IMG_SVG_GARBAGE_RE` 触达）
- **前端只折叠 chart**：`SvgCodeBlock` 仅对 ````chart ```` 围栏折叠（协议围栏防流式卡顿）；svg/xml 恢复普通代码块（用户内容空间不折叠）
- **「图表未生成」warning**：勾选图表但 LLM 未输出 ````chart ```` 围栏（旧习惯 svg 围栏/裸内联/没画）→ 完成时 warning「图表未生成：请重新生成」（双产物 + 单产物 notes 路径）
- **保留不变**：校验链（XML 良构/尺寸双校验 ≤640/64KB/有内容/渲染重试）、安全过滤、`_IMG_SVG_GARBAGE_RE` 垃圾清理（只作用于图片引用后粘连字面量，不触碰用户内容）、P0-3 只入 notes

### 验证
- `tests/test_chart_renderer.py` 重写对齐新协议（12 用例）：svg 围栏/裸内联不处理、失败零残留、混合场景只处理 chart、垃圾清理
- 后端 11 测试文件 + 前端 98 tests + build + lint 全绿

### 影响
- LLM 输出 ````svg ```` 围栏（旧习惯）→ 不再处理，用户看到 svg 代码块 + 完成 warning 提示重新生成（过渡期成本，prompt 已引导 chart）
- 用户内容含 svg/xml 代码块或裸内联 `<svg>` → 不再被图表链路触碰（原可能被静默渲染成图改写）

---

## 2026-08-16 — 全面健壮性/代码质量/用户视角优化（45 项）+ UI 体验升级（4 项）

> 用户要求「全面进行代码健壮性以及代码质量优化以及从用户的角度来再做一些」。3 个 Explore agent（后端健壮性 / 前端用户视角 / 代码质量）+ 人工自查共发现约 60 项问题，分 **P0（崩溃/安全/数据正确性 10 项）→ P1（图表链路/资源泄漏/UX 竞态 21 项）→ P2（中断恢复/死代码清理 14 项）** 三批全部修复，每批独立回归。本次打包 **0.4.0**（新功能 → minor）。

### P0 崩溃/安全/数据正确性（10 项）
- **React hooks 崩溃修复**：`MarkdownImage` 条件 `return null` 移到两个 useEffect 之后（imgSrc 空→非空时 hook 数量 2→4 曾抛「Rendered more hooks」）
- **图表尺寸校验绕过（OOM）**：`_parse_viewbox` 同时解析 `width`/`height` 并入 `MAX_CANVAS` 校验——`width="50000"` + 合法 viewBox 不再绕过（曾 ×2 zoom ≈ 40GB 内存）
- **chart 文件名冲突**：图表**只入 notes**——generate_both 只对 notes 调 render_charts；`_post_process` 加 `render_chart` 参数，polished 三处调用传 `False`，双产物不再互踩 `chart_N.png`
- **asyncio.gather 异常泄漏**：`return_exceptions=True` 逐路处理 + 任一路异常时 cancel 另一任务再 `await gather(...)` 收尾，`_cleanup_video` 不再在另一路运行时提前删视频
- **流式取消/异常路径不删下载视频**：`video_path`/`screenshot_video_path` 删除挪进 `finally`（原每次取消泄漏单次最大 500MB）
- **process 大图 OOM**：LLM 路径前 `stat().st_size` 校验 ≤20MB，超限跳过 LLM 直接走引擎回退（Engine 路径不读 base64 不误拦）
- **assets 服务剔除 svg**：白名单仅留 png/jpg/jpeg/gif/webp——svg 经 FileResponse 直接返回时浏览器导航 URL 同源执行脚本（存储型 XSS 面）；数据目录实测 0 存量
- **删除文档不取消进行中任务**：confirmDelete 前检查 `processingTasks` 有该 doc 任务 → 弹确认 + `cancelTask` + `clearDocTasks` 再删（防孤儿落盘/文档复活）
- **per-product 失败误报整体失败**：后端单产物错误 `{"type":"error"}` → 改发 `warning`（带 mode）——前端 onWarning 只标 partial + toast，不清双产物 buffer、不 failTask（polished 超时但 notes 成功 = 单 warning + notes 保留完整）
- **ingestStream 无终态兜底**：`receivedDoneOrError` 追踪，流结束未收终态 → 「连接意外断开，请重试」（原导入永久卡死）

### P1 图表链路 + 资源泄漏 + UX 竞态（21 项）
- **图表链路**：resvg 渲染/ffmpeg 抽帧改 `asyncio.to_thread`（不阻塞事件循环）；SVG 围栏 >64KB 直接失败标注不渲染；图表勾选能力门控（polished-only 时禁用 + tooltip）+ 被过滤格式一次性 toast + 后端对「勾 chart 但产物无任何 ```svg」发 warning；文案改「AI 生成 SVG，本地渲染为 PNG 嵌入」
- **资源泄漏**：LLM 流式生成器超时/异常后 finally `_it.aclose()`（httpx 连接不再泄漏）；process.py `llm.aclose()` 进 finally；双产物断连时 finally 取消 `_tasks` 孤儿任务；`mkdtemp("limo_ss_")` 永久泄漏修复（doc_dir 空跳过截图替换）+ `tempfile.mktemp`→`mkstemp`（TOCTOU）+ 下载视频父目录/`_cookie_to_file` 临时文件用后清理；双重取消 `except BaseException` 防 `semaphore.release()` 跳过（槽位永久丢失）；上传临时文件 adapter 失败时清理；LLM 重试退避补 `cancel_event` 检查；screenshot_marker 时间戳 ≤60min
- **UX 竞态**：apiFetch 包 try/catch 网络错误归一化（`ApiErrorException('网络连接失败，请检查后端是否运行', recoverable)`，全屏「内容加载失败」不再被瞬时抖动触发）；import fallback 前检查 `signal.aborted` + fallback 传同一 signal + 已收进度时提示「网络恢复重试中」；DocLibrary 改名/标签/删除 try/catch + toast（原静默假成功）；chatStore 4 处同步失败 catch 加 toast；同源导入防重入（相同 title 检查，保留不同源并发）；取消生成同时刷 polished versions（原只刷 notes）

### P2 中断恢复 + 死代码清理（14 项）
- **中断生成恢复（用户确认恢复）**：PartialBanner 加「继续生成」入口 → `limo:continue-generate` 自定义事件 → GenPanel 监听 → `handleGenerate('partial')` 以 `continue_from=partial` 续写残缺产物（与 epub 计划隔离，状态源不同）
- **去重提示改 toast**：「该内容已导入，已为你选中 · title」（原 3s 静默小字）
- **图片处理结果加载失败 toast**（原误显示「点击处理卡片」）
- **DocLibrary 底部任务进度死面板删除** + store `importProgress`/`importStages`/`setImportProgress`/`setImportStages` 字段 + `ImportStage` 接口 + 5 个死字段测试（生产恒 null 的死 UI）
- **formatSize 公共工具**：B/KB/MB/GB 智能单位，图片/文件/视频三处统一（原永远按 KB 显示）
- **主导入大小预检**：pickFile 统一入口（2GB 拒绝 + 1GB 警告）
- **取消单条导入精确匹配**：taskManager 按完整 `__import__N` key 精确取消（原归一成 `__import__` 前缀误取消全部）
- **ingest.py 图片服务去重**：`_validate_tx_id`/`_check_asset_ext`/`_check_asset_within` 三个公共 helper 消除 serve_media_file/serve_asset 重复 + 全中文化错误（无效的文档 ID/不支持的文件格式/路径穿越已拒绝/文件不存在）
- **generate.py 日志风格收敛**：9 处 `logger.%s` → `app.core.logging` kwargs/f-string，删未用 `import logging`
- **死代码清理**：`parseSSEStreamWithAbort`（两分支相同）、`Conversation` 接口、`formatTranslate` 死状态、`_export_filename`、`/process-image/{id}` 废弃路由、generate.py `Optional`/`_resolve_model`/`_track_generation`、prompt.py `_STYLE_HEADER`/`PDF_STYLE_MAP`/`VIDEO_STYLE_KEYS`/`EXCEL_STYLE_KEYS`（`STYLE_MAP`/`DOC_STYLE_MAP` 被 capabilities.py 使用保留）、capabilities.py `VIDEO_STYLE_KEYS`
- **ChatSidebar 裸错误处理**：改 `getErrorMessage(e)`（丢结构化 message）

### UI 体验升级（4 项）
- **阅读宽度动态跟随面板**：内容上限 `max(1100, 面板宽 - 160)`——折叠 GenPanel/DocLibrary 后面板变宽 → 阅读内容同步放宽（最高 ~1440px，两侧各留 ~80px 边距）利用空白空间
- **编辑/输入统一 mono 字体**：对话输入框 + 文本/链接/图片/文件 URL 粘贴框改 `font-mono`（产物编辑器源码区/代码块本就是等宽）
- **编辑器分屏可拖拽调宽**：TextareaMarkdownEditor 源码 | 预览 之间加 ResizeHandle 分隔条（默认 50%，clamp 25%–75%）
- **欢迎文档三产物化**：欢迎文档（空态示例文档）改造成 LiMo 三产物形态——**笔记**（欢迎/简介+快速开始）/ **整理版原文**（面向开发者的全面功能总览）/ **转录**（CHANGELOG 更新记录），tab 样式对齐主工具栏，内容提为模块级常量便于同步

### 文档同步
- CLAUDE.md：待办 UI/UX 域新增 4 项；前端模式更新（欢迎文档三产物 = 模式 34 扩展、mono 输入字体、分屏拖拽、动态阅读宽度）
- STATUS.md：版本 0.4.0、前端测试 103→98、工程质量表更新
- BUILD.md：产物文件名 0.3.1 → 0.4.0

### 测试
- 后端 **11 个测试文件全绿**（含 P0 新增 `tests/test_chart_renderer.py`：OOM 尺寸校验回归 + `render_chart=False` polished 不渲染 + 同 doc_dir 双产物文件名不互踩）
- 前端 `npm test`（**98 tests**）+ `npm run build` + `npm run lint`（0 error）全绿

---

## 2026-08-16 — 图表作为笔记可选组件 + AI 审核（fact_check）移除转历史

> 用户 @ `pi-docs/图表功能与fact_check移除方案.md`（2026-08-15 评审定型）要求实施。两块：① 移除 AI 审核（事实核查产品无强需求、验证链路重），② 图表作为生成笔记的可选组件（对齐「AI 输出意图 → 本地确定性工具收口 → Markdown + assets 引用」的截图模式）。

### ① AI 审核（fact_check）移除转历史

**后端**：
- 删除 `routers/fact_check.py`、`generation/fact_check.py`（旧 FactCheckEngine）、`generation/review.py`（ReviewEngine）、`agent/evidence.py`（Evidence Manager）+ main.py/spec 注册；`types.py` 删 GenerateMode.FACT_CHECK / MarkdownView.fact_check / FactCheckClaim / FactCheckResult
- `product_registry.py` 删 fact_check ProductDef；`export.py` 6 处（mode_label / 内容源回退链 / fact_check 分支）；`prompt.py` 删 7 个事实验证 prompt（旧 FactCheck 3 + AI Review 4）+ 相关温度速查；`tools.py` 删 4 个死工具（search_official/get_url_metadata/today/is_recent，仅被 review.py 用）
- 模型解析注释 `（generate / chat / process 共用）`
- **历史保留**：已生成的 `fact_check.md` 文件保留不删（转历史，无读取入口；export 内容源回退链不再含 fact_check → 旧文件 404，前端无 tab 展示）

**前端**：删 tabRegistry fact_check tab + contentSources factCheck getter + store types/contentSlice/chatSlice 字段（factChecking 一并移除）+ api `streamFactCheck`/`ReviewChunk` 类型 + PreviewArea `handleFactCheck`/case 分支 + ExportMenu/TaskPanel/GenPanel/constants 相关行 + genPrefs 防御性迁移（旧 `fact_check`/`factCheck` localStorage 清理）

**测试**：CopyContentBtn/tabRegistry/store 测试删 fact_check 断言；`test_markdown_edit.py` 的 `('summary','fact_check','transcript')` 非法 mode 用例保留（fact_check 仍是非法模式）

### ② 图表作为笔记可选组件

**后端**：
- 新建 `app/services/chart_renderer.py`：检测 ` ```svg/```xml ` 代码块（双条件：围栏语言∈{svg,xml} 且含 `<svg` 根元素）→ 安全过滤（script/on*/外部引用剥离）→ 校验链（XML 良构 → viewBox ≤640×640 → 有形状元素）→ resvg 渲染 PNG(2x) → `assets/chart_N.png` → `![图表](assets/chart_N.png)`；失败保留原代码块 + `> ⚠️ 图表生成失败（{reason}）` 标注，不阻塞整体生成；`_render_png` 惰性 import resvg_py（spec 需 collect_all）
- `prompt.py` 新增 `get_chart_format(source_type)`（基础规范 + 来源差异段：DOC→架构图/层级树、网页→流程/结构树、视频→流程步骤图/时间线）；`AVAILABLE_OPTIONS` 加 chart（video/webpage/pdf/docx/pptx/text，xlsx/audio 无）；`_ALL_FORMAT_FNS["chart"]` + `_build_hard_constraints` 加 source_type 参数注入
- **只入 notes**：`_POLISH_FORMAT_FNS` 无 chart，polish 不注入；后处理挂载 generator.py generate/generate_both + generate.py `_post_process`（在 `_normalize_generated_images` 之前）
- `LiMoBackend.spec`：hiddenimports 加 `app.services.chart_renderer` + collect_all 加 `resvg_py`
- 依赖：`uv add resvg-py`（清华镜像，resvg-py==0.4.0；API 为 `svg_to_bytes(svg, zoom=2.0)`，无 scale 参数）

**前端**：
- GenerateOptions 两区块（视频/网页、文本/文档）各加「图表」勾选（默认不勾选），排除 epub（独立解读管道）；xlsx/图片本就无笔记格式区
- genPrefs 加 `formatChart` 持久化；GenPanel state/saveGenPrefs/formatStr（chart 进 formats，availableFormats 过滤自动生效——capabilities genOptions 直引 AVAILABLE_OPTIONS）
- 欢迎文档第 3/5 条：加图表说明、删事实验证

### 文档同步
- CLAUDE.md：AI 审核管道 section → 图表管道 section；待办 AI 审核域 → 图表生成域；合计 72/74→68/70；chatSlice 只留 generating；取消机制 5 端点
- STATUS.md：端点 78→76、后端 py 95→92（agent 6/generation 10/services 3）、管道表事实验证→图表、进度删 AI 审核（72/74→63/64）、目录结构更新

### 测试
- 后端全量（prompt/adapters/engines/store/url_security/capabilities/plugin_loader/markdown_edit + e2e 冒烟 36）全绿
- 前端 `npm run build` + `npm test`（103 tests）全绿

---

## 2026-08-15 — 产物编辑能力（notes/polished 手动编辑 + 覆盖保护）

> 用户 @ `pi-docs/产物编辑方案.md` 要求实施产物编辑。此前产物（notes/polished）纯只读，用户无法修正 AI 错误、补充内容、导出前润色。零新依赖起步（textarea 版），组件抽象预留 ByteMD 增强路径。

### 后端（最小面：零改 store.py / generate.py）
- 新增 `PUT /api/markdown/{transcript_id}`（`app/routers/markdown_edit.py`）：校验 mode ∈ {notes, polished} / doc 存在 / 内容非空；**复用 `save_markdown(mv, extra={"edited": True})` 写 edited 标记**（`**(extra or {})` 零改 store 本体）；两版制自动保留生成版为 prev 可回退
- **style 保留**：编辑保存继承当前最新 entry 的 style，不因编辑丢失排版语义
- **图片 URL 反转**：GET 返回时 `rewrite_asset_urls` 把 `assets/` → `/api/assets/{tx}/`；编辑保存前 `_unrewrite_asset_urls` 反向转回，绝对 URL 不写回磁盘污染导出/二次渲染。形态对称（只处理 `![alt]()` 一种，用户手动输入的 `<img>` 不反转），`re.escape(tx_id)` 防注入
- **清除语义**：重新生成链路 `save_markdown` 不传 edited → 新 entry 无该字段 → 标记自动消失（零改 generate.py）

### 前端
- 新增 `TextareaMarkdownEditor.tsx`（`MarkdownEditor` 接口第一实现）：工具栏（B/I/S｜H1-3｜列表/引用/代码块/表格｜链接｜撤销/重做）+ 左源码右分屏实时预览（debounce 300ms，复用现有 REMARK/REHYPE + normalizeLatexDelimiters）+ 快捷键 Ctrl+B/I/S、Ctrl+Z/Y（手动历史栈 cap 100）+ 选区包裹/行级操作
- **内容区工具条**（用户确认入口位置）：summary/polished tab 的 MarkdownPreview 上方窄条，含「已手动编辑」徽章 + 铅笔编辑按钮；显示条件排除 Excel（其 summary 走 notes 内容源但数据摘要不可编辑）；生成中/任务中置灰；fact_check/transcript 等 tab 不渲染
- 保存 = PUT → 更新 currentContent/currentPolishedContent + meta（含 edited）+ versions + toast「已保存」；未保存防丢：切 tab / 切文档两态 `window.confirm`（用户确认）+ beforeunload 拦截（Tauri 关窗为边缘情况，已知限制标注）
- **覆盖保护**：`markdownEdited` store 状态（独立于 markdownMeta，两产物同时记录）——GenPanel 重新生成前检测 → confirm「将覆盖手动编辑内容」；EpubInterpretPanel 解读/继续/重跑同样确认；生成完成/解读完成自动清除标记

### 已知限制（评审确认）
- **两版制 × 连续编辑**：连续编辑 2 次会把编辑版 1 顶为 prev、生成版被挤出两版制（无法回退生成版）。接受——两版制本为「生成版 + 最近编辑」语义，不为此改 save_markdown 版本逻辑（会牵连生成链路）

### 测试
- 新增 `backend/tests/test_markdown_edit.py`（11 用例：保存成功/URL 反转/prev 保留/polished/mode 校验/不存在/空内容/清除语义/style 保留/无产物拒绝/超限拒绝）全绿
- 前端 `store.test.ts` 加 `setMarkdownEdited` 单测（103 tests 全过）；`tsc -b` + vite build 通过；oxlint 无新增 error
- 后端 10 测试文件全绿（含 e2e_smoke 36/36）

### 健壮性维护（同日，用户要求「健壮性代码维护 + 从用户角度修复漏洞」）
- **覆盖确认不再被「查看旧版」清除**：`loadVersion` 曾用 `!!md.edited` 覆盖文档级 `markdownEdited`——编辑保存 v2 后切到 v1 查看会把标记清成 false，之后重新生成不再弹确认。现 `markdownEdited` 只在「保存编辑 / 生成完成 / 切文档」时变化（查看版本不影响），确认语义与两版制回退一致
- **编辑徽章跨 tab 串扰修复**：徽章原读单槽 `markdownMeta.edited`（切 tab 不更新）→ 编辑 notes 后切到 polished tab 会错误显示「已手动编辑」。改读两产物独立的 `markdownEdited[editMode]`
- **编辑器历史栈 ref 化**（`TextareaMarkdownEditor`）：`undo`/`redo` 原在 setState updater 内调副作用（`setFuture` + `onChange`）——React 18+ 不保证 updater 只执行一次（StrictMode dev 下必然双调用），会导致 future 栈重复记录 + onChange 双发；改为 ref 历史栈 + 长度 state 驱动按钮 disabled，撤销/重做读即时状态，连按 Ctrl+Z 也正确
- **后端 PUT 加固**：`MD_EDIT_TOO_LARGE`（>5M 字符拒绝）+ `MD_EDIT_NO_PRODUCT`（transcript 存在但该 mode 无产物时拒绝——前端无产物不显示编辑入口，此为 API 层防御防直接调用创建无源产物）
- **e2e Windows PIPE 缓冲死锁修复**：`test_e2e_smoke.py` 后端 subprocess 用 `stdout/stderr=PIPE` 但从不读取 → Windows 匿名管道 ~4KB 缓冲被 uvicorn 日志填满后 `write()` 阻塞事件循环 → `GET /api/config` 间歇超时（4 次复现 3 次失败，曾误判为端口冲突 flaky）。改为写入临时日志文件（不阻塞 + 保留后端日志供诊断）

### 影响范围
- 后端：新增 `routers/markdown_edit.py` + `main.py` 注册；store.py / generate.py 零改动
- 前端：`api/generate.ts`（saveMarkdownContent）、`api/core.ts`（MarkdownView.edited）、`store/types.ts` + `contentSlice.ts`（markdownEdited）、`PreviewArea.tsx`、`GenPanel.tsx`、`EpubInterpretPanel.tsx`、新增 `TextareaMarkdownEditor.tsx`；欢迎页快速开始同步（模式 34）
- 只读预览零变化（不点编辑 = 原样）；fact_check/transcript/Excel 均不可编辑

---

## 2026-08-14 — 插件 venv 检查路径遍历异常修复

> `GET /api/plugins` 500：`_venv_valid()` 里 `base_py.exists()` 对 AppData 下 uv 托管 python（`C:\Users\...\AppData\Roaming\uv\python\...\python.exe`）做 `stat()` 时抛 `OSError: [WinError 448] 不受信任装入点`。此前 `except OSError` 只保护读 pyvenv.cfg（L452），`base_py.exists()` 在 try 外，异常一路冒泡到 router 变 500。

- **修复**：`_venv_valid()` 整个检查包进 `try/except OSError`——路径遍历失败（含 WinError 448）视为 venv 不可用返回 `False`（而非抛 500），并 `logger.warning` 记录原因（不静默）。语义与既有「失效 venv 视为未安装」一致：前端显示「一键安装依赖」，重建 venv 会用可访问的 base python 自愈
- **验证**：模拟 WinError 448（monkeypatch `Path.exists` 抛 OSError 448）→ `_venv_valid` 返回 `False` + warning 日志；后端 8 测试全绿
- 影响范围：仅 `backend/app/engines/plugin_loader.py` `_venv_valid()`；`enumerate_plugins`/路由无改动

---

## 2026-08-14 — 打包体积优化（docling 清理 + ffmpeg 精简）

> 用户 @ `pi-docs/打包体积优化计划.md` 要求执行打包体积优化。实测确认现状比计划记录更极端（ffmpeg 实际 434M 而非 194M）。执行 P1 + P2b，安装包从 ~265M/~366M 降到 **~148M/~195M**。

### P1 docling 依赖残留清理
- `backend/pyproject.toml` 删 `docling>=2.115.0`（docling 引擎 2026-08-03 已移除但依赖没删，spec 早清干净但 venv 全家残留）+ 顺带删 `pytesseract>=0.3`（Tesseract 已移除的残留，代码零引用）
- `uv sync` 移除 docling/torch/torchvision/transformers/scipy/rapidocr/tree-sitter/pywin32 等全家（venv 原 ~250M+）
- `LiMoBackend.spec` 删 hiddenimports 残留 `filelock`（docling 传递依赖时代产物，代码零引用）
- 后端 8 测试全绿（e2e_smoke 36/36）+ 打包版 exe health 200

### P2b ffmpeg 精简 build 替换
- gyan.dev full build（434M：ffmpeg 227M + ffprobe 227M）→ **gyan.dev essentials 8.1.1（194M）**
- 下载走 **ghfast.top GitHub 加速代理**（gyan.dev 官网 ~48KB/s 太慢；ghproxy.com 已失效）
- 验证：libmp3lame/pcm_s16le/mjpeg 编码器 + h264/hevc/av1/aac/mp3/opus 解码 + mp4/mkv/webm/avi/flv 容器 + ffprobe 探测；4 个调用点回归通过（video.py MP3 提取 / asr_cloud.py WAV / video_reader.py+video_helper.py 截帧）
- 完整版备份 `~/.limo/ffmpeg-backup-full/`；精简版分发源 `~/.limo/ffmpeg-portable/`

### P3 markitdown
- **保留**（用户决策，保守）。onnxruntime 39M 大头是 capi 34M 必需 DLL，tools/transformers 纯 py 仅 ~3.5M 不值得优化

### 体积结果（实测）

| 项 | 前 | 后 | 变化 |
|---|---|---|---|
| PyInstaller 产物（含 ffmpeg） | 918M | 443M | 省 475M（-52%） |
| NSIS 安装包 | ~265M | **148M** | 省 ~117M（-44%） |
| MSI 安装包 | ~366M | **195M** | 省 ~171M（-47%） |

### 文档
- `pi-docs/打包体积优化计划.md` 更新为已执行（实测数据 + 下载坑）；`docs/BUILD.md` 产物体积 + 精简 ffmpeg 源说明；`CLAUDE.md` ffmpeg 内置描述

---

## 2026-08-14 — 文档全面更新 + AGENT-ONBOARDING 上手文档

> 用户要求三件事：① 全面更新所有文档；② 写一份面向其他 AI agent 快速上手/接手开发的文档（打包章节要详细）；③ 清理和合并不必要/过时的文档。用 3 个 Explore agent 逐份对比文档与代码 + Plan agent 交叉验证，产出偏差清单后逐份对齐。**所有计数以代码实测为准。**

### 文档更新（10 份 docs + design + 根目录；docs/ 由 11 → 12 份）
- **大改**：export-pipeline.md（TAB_EXPORT → tabRegistry/contentSources 机制、GFM checkbox/时间戳 badge/标题锚点、KaTeX 双路径、补 3 导出端点、行号）；design/components.md（品牌名 MyBigWork→LiMo、ResizablePanelGroup→SlotLayout 插槽引擎、生成面板左插槽、AI 浮窗唯一形态、设置页 7 tab、快捷键）；prompts-inventory.md（Polish 白名单 +link/screenshot 移 translate、AVAILABLE_OPTIONS 补 formulas、SYSTEM_PROMPT→agent/prompts 分层、SSE 事件、AI 审核四阶段、prompt 计数）；user-scenarios.md（场景 9b 跨多篇已实现、6/7 状态修正、主动提醒部分落地）
- **中改**：architecture.md（引擎 10、路由 14、load_config 位置、格式残留修正 + 补零文档模块 FTS5 搜索/Slot 注册表/download/sse/transcript 契约/SessionManager/ocr_preprocessor/cookie_parser/视频 utils/sample_doc/config 同步/启动时序/protocols）；STATUS.md（91 py/74 端点/72-74 进度 + 目录结构）；CLAUDE.md（文档索引 + 对话协议、测试 8 文件/101 tests、关键模式 34 条、导出系统描述、待办 72/74、语义搜索措辞）
- **小改**：frontend-patterns.md（streamBuffers/backendStatus/appUrl/mbw 14 key）；对话协议.md（done 无 usage、ref 随 tool_result 携带）；qianwen-models.md（context_window/推荐模型）；BUILD.md（产物 0.3.1/hiddenimports 清单）；plugins.md（管理 API 补两行）
- **修正 2 处隐藏文档 bug**：CLAUDE.md 引用不存在的 `scripts/build.sh`（改指向 BUILD.md）；start.sh 品牌名 DeepDraft → LiMo

### 新文档
- **docs/AGENT-ONBOARDING.md**（新）：课本式上手教程——从零跑通 → 架构全景 → 六大注册表 → 数据流横切 → How-to 8 任务 → **打包发布（最详细：命令级步骤 + 顺序铁律 + 陷阱排查对照表 + 新增依赖 checklist + 发布门禁）** → 测试门禁 → 调试速查 → 上手练习

### 清理（温和清理，用户确认）
- 删除 `pi-docs/`（3 份已完成功能的规划稿，CHANGELOG 有完整记录）；删除 `frontend/README.md`（Vite 模板残留）；删除 `frontend/src/components/ui/SetupWizard.tsx`（死代码，OnboardingWizard 替代）；修正 `backend/plugins/examples/README.md`「两个模板」→「三个模板」
- SECURITY_AUDIT.md / backend/tests/TEST_REPORT.md 历史时点报告保留原样（用户确认）

### 验证
- 前端 `tsc -b` + vite build ✓、oxlint 无新增 error ✓、vitest 101/101 ✓；后端 8 测试脚本全绿（含 e2e_smoke 36/36）✓
- grep 抽查：文档新引用的 14 个文件/端点全部真实存在（slotRegistry/tabRegistry/contentSources/streamBuffers/search.py/transcripts_fts/agent/prompts/…）

---

## 2026-08-13 — 首次体验与健壮性（P1-P3 实施）

> 用户提交「首次体验与健壮性计划」。深度现状核验修正 4 处偏差：中转站后端已存在（0 新工作）、OnboardingWizard 已存在（改扩展）、启动探活已存在（补持续探活）、OCR/ASR 缺 key 已基本显式（补唯一缺口）。

### P1 健壮性 + 可观测性
- **前端持续探活**（`lib/health.ts` 新 + `uiSlice.backendStatus`）：10s 轮询 `/api/health`，连续 2 次失败 → `down`（顶部横幅「后端服务异常，正在重试…」），连续失败超 30s → `crashed`（「后端已崩溃，请重启应用」），恢复自动消失。与启动探活（只管「后端未就绪加载屏」）职责分离共存
- **`/api/health` 别名**：`/health` 是根路径（无 `/api` 前缀）且 Vite proxy 只代理 `/api`、`/static`（浏览器下走 SPA fallback）→ 后端加 `/api/health` 别名，前端 `apiUrl('/health')` 双环境通
- **请求级日志**（`main.py` middleware 扩展）：`call_next` 前后计时记录 `method/path/status/duration_ms`，慢请求（>2s）WARN、正常 INFO；**SSE 按响应头 `content-type: text/event-stream` 判定不判慢**（非路径白名单——新增流式端点不漏）；跳过高频噪音（health / static / assets 图片渲染）

### P2 首次体验
- **配置向导第三屏**（OnboardingWizard 扩展）：阅读氛围 → 系统组件 → **AI 配置**（数据源 `capabilities.missingKeys`）——LLM 厂商卡填 key + `testProviderConnection` 连通验证（复用已有 API）；DashScope 引擎卡填 key（`PUT /engine-providers/dashscope` 后端自动双向同步 `cloud.dashscope`）；已配项保存后从列表消失；可整屏跳过；老用户 missingKeys 全空自动跳过不打扰
- **`capabilities.missingKeys`**：后端 `_compute_missing_keys` **复用 `model._get_providers`**（含全局 `llm.api_key` 同步 + sk-ws/sk-ant 防错配）——llm 遍历 `llm_models` 厂商查 provider.api_key 为空，engine 复用 `cloud.dashscope` 检测
- **缺 key 主动提示**：ImportSection 图片/视频区**内联提示条**（X 关闭 + localStorage `mbw_dashscope_hint_dismissed`；非 toast——切类型不重复打扰）；chatStore `dispatchSend` 首次发送缺 LLM key toast（localStorage `mbw_llm_hint_dismissed`）
- **后端 OCR/ASR 可见化补缺**：video.py `_adapt_local`（本地视频）FatalError 原写死「未获取到转写结果」→ 汇总 warnings 带原因（对齐 URL 路径，如「ASR dashscope-asr 失败: DashScope API key 未配置」）。其余路径已显式无需改（document 扫描件占位带原因 / process 图片 AppError·TranscribeError 带 detail / URL 视频 FatalError）；image.py `_ocr` 为无调用方死代码不改

### 范围外
- P3（32-bit / 按需下载）后置；日志分级/轮转体系重构（只补请求级）；tavily 配置向导步骤

### 影响范围
- **后端**：main.py（health 别名 + 请求日志）· capabilities.py（missingKeys）· video.py（本地 ASR detail）+ tests/test_capabilities.py（新，6 用例）
- **前端**：lib/health.ts（新）· uiSlice.ts（backendStatus）· App.tsx（横幅 + 探活）· OnboardingWizard.tsx（第三屏）· api/core.ts（Capabilities 类型）· ImportSection.tsx / chatStore.ts（缺 key 提示）
- **验证**：前端 `tsc -b` + vitest 101/101；后端 6 脚本 + e2e_smoke 36/36；实测 `/api/health` 200 + 请求日志输出 + missingKeys 字段

---

## 2026-08-06 — PDF 笔记图片显示修复

> 用户反馈：PDF 笔记（DSpark，pymupdf4llm 插件导出）图片显示有问题，生成完成后需刷新才正常。

- **根因 1（图片 404）**：LLM 生成 notes 时把转录里的 `assets/img_xxx.png` 幻觉改写成 `/api/files/assets/img_0003.png`（错误绝对 URL → 前端 404）。transcript/polished 保留相对路径正确，notes 错。
- **修复**：generate.py `_post_process` 加 `_normalize_generated_images`——把 `/api/files/...` / `/api/assets/{tx}/...` 绝对 URL 规范化回相对 `assets/{filename}`（仅当文件在本文档 `{doc_dir}/assets/` 存在）；data URI / 外部 http / 已是相对路径保留。顺带修复 `_post_process` 传 `store.root` 当 doc_dir 的隐藏 bug（改传文档目录）
- **根因 2（需刷新才显示）**：前端 `MarkdownImage` 加载失败后 `broken` 永久，文件就绪不自动恢复 → 需手动刷新
- **修复**：`MarkdownImage` 加「src 变化重置 broken」+「加载失败 2s 自动重试」——生成完成后图片文件就绪自动显示，无需刷新

---

## 2026-08-06 — 插件 venv 健壮性修复

> 用户报告：pymupdf4llm 插件导入失败 `No Python at "C:\Users\...\uv\python\...\python.exe"`——插件 venv 的 base python（uv 管理的 python）路径失效。

- **`_plugin_subdir` 修复**：用声明文件 stem（而非 `adapter.name`）解析插件子目录——`pymupdf4llm_heavy.py` 里 `name=pymupdf4llm` 但子目录是 `pymupdf4llm_heavy/`，此前按 name 解析到错误路径导致 requirements/venv 找不到（`install_plugin_deps` 误报 no-requirements）
- **`_venv_valid` 新增**：校验 venv python 存在 + `pyvenv.cfg` 的 base python（home）可用——失效 venv（base python 被 uv 升级/清理）视为未安装，前端显示「一键安装依赖」
- **`install_plugin_deps` 自愈**：检测到失效 venv → 删除重建（用当前可用 base python），不再因 venv 存在而跳过
- 插件 venv base 策略：源码模式用后端 python（sys.executable）；打包版用系统 python（PyInstaller 无法用自身 exe 跑 `-m venv`）；都不可用 → 失败显式引导手动（`no-python` + hint）

---

## 2026-08-04 — PDF 链路优化 + 两个生成 bug 修复

> 用户诉求：PDF 整体转图片（供 OCR/多模态）+ OCR 引擎可选图片转文字小模型（不只多模态大模型）。

### pdf-image 引擎 + OCR 引擎名称检测
- **pdf-image Doc 引擎**（`doc_pdf_image.py` 新）：pypdfium2 渲染每页为 PNG（media_dir 写 `page_NNNN.png` / data URI），标记 is_scanned；绑定 `doc:pdf` 后可替代 pypdfium2 文字提取，对纯图片/扫描 PDF 更稳（绕开 pypdfium2 提取嵌入图的坏图问题）；document.py 对 pdf-image 跳过重复渲染+OCR（产物本身是图片转录）
- **OCR 名称检测**：`_PROVIDER_IMAGE_RULES` dashscope include 加 `ocr` → `qwen3.5-ocr` 等自动标 image，生成时「OCR 模型」下拉可选到图片转文字专用小模型；检测结果用户可手动取消/标记（`updateModel` capabilities 已支持）
- **OCR 默认绑定**：`engine_bindings.ocr` 未绑定且 dashscope 有 key → 默认绑 `qwen-vl-ocr-latest`（图片转文字专用，比 VL 大模型便宜），避免「无可用 OCR 引擎」

### 生成 bug 修复
- **PIL 坏图崩溃**：`_sanitize_image_uri` 校验 data URI（PIL 可识别 + 可选优化），损坏/截断图片跳过不中断生成流（`content_preparer`，根因是扫描/图片层 PDF 提取的坏图）
- **公式 `\tag` 渲染**：含 `\tag` 的内联公式被当 inline 渲染 → KaTeX 报 `\tag works only in display equations` 红色 katex-error；前端 `markdown-plugins.ts` + 后端 `export.py` + `render_math.js` 三处同步把含 `\tag` 内联公式提升为 display（导出 HTML 一直正常因为走 display）

---

## 2026-08-04 — 对话上下文管理（C1-C5 · 依 pi-docs/对话上下文管理计划.md）

> 用户确认「C1-C5 全量」。对话重写后上下文管理未实现：历史固定 40 条 + agent 二次截 20 条、文档引用每篇 1000 截断——无模型感知、无 token 预算。用户 token 敏感，不同模型窗口/价格不同需适配。

### C1 模型元数据
- `core/token.py`（新）：公共 tiktoken 估算（从 content_preparer 抽出复用）
- `config.py` llm_models 加结构化 `context_window`（deepseek 1M / qwen 128k / gpt 128k / claude 200k / gemini 1M）
- `model_resolver.resolve_context_window` 四层：用户自定义 > 预设（合并 DEFAULT，兼容旧 config 缺字段）> provider 默认表 > 保守 32k
- `model.py` addModel/updateModel 支持 context_window/price_in/price_out；ProviderSettings 模型元数据编辑弹窗
- price 预设不填（None），用户配了才启用费用显示——避免错误价格误导

### C2 预算引擎
- `core/chat_context.py`（新）：compute_budget（窗口×0.7 留输出）、trim_history（反向裁剪跳 isError）、build_doc_context（富余 2000 / 紧张 1000 截断）
- chat.py 按预算组装（system→doc→history 分配），meta 事件带 context_window/budget；agent.py 移除 `history[-20:]` 二次截断
- 修正 NextChat 缺陷：其 max_tokens 同时当输入预算和输出上限，本引擎预算=窗口×0.7 留输出空间

### C3 历史压缩
- `agent/compress.py`（新）：超阈值（未压缩历史 > 预算×60%）LLM 摘要早期消息；压缩模型 fallback：deepseek-chat → 当前模型 → 跳过本轮不阻塞
- Conversation 加 summary/last_compress_index/clear_context_at；session.set_clear_context（压缩基准重置）
- chat.py 压缩检查 + summary 进 system + SSE `compressed` 事件

### C4 前端上下文感知
- `utils/token.ts`（新）：estimateTokenLength（NextChat 同款字符启发式）+ formatTokens
- ChatInput：上下文占用进度条 + 输入 token + 每轮 in→out + 费用估算（配 price 才显示）
- chatStore：meta 存 budget/price、done 存 usage、compressed 提示、clearContext action
- ChatCore：清除上下文按钮 + 压缩/隐私提示条；ProviderSettings「对话上下文」区块（预算档位/历史上限/文档引用模式）
- conversations.py `POST /conversations/{id}/clear-context`

### C5 打磨
- agent.py `_create_with_retry`：create 阶段 429/5xx 退避重试（1s/3s），stream 中途失败不重试（避免重复输出）
- build_doc_context 按 docId+limit 缓存（contentHash 失效），同文档多次对话不重复处理
- 协议文档补 compressed / meta budget·price / done usage / clear-context

---

## 2026-08-04 — 对话模块全量重写（P1-P6 · 依 pi-docs/对话重写方案.md）

> 用户确认「放开手脚做完（无实际用户）」。LiMo 对话 UI 原本简陋（ChatCore 360 行 = 消息列表 + Markdown + 导出），本次按建议稿全量重写，对标 NextChat 的对话管理核心。

### 前端：独立 chatStore + 消息层组件树

- **独立 `store/chatStore.ts`**（不并入主 useStore）：流式 delta 高频更新隔离；ChatMsg 消息模型（`streaming`/`isError`/`toolCalls`/`docRefs`/`thinking`）+ 流式状态机（send/stop/regenerate/edit 分支语义/delete）
- **chatSlice 瘦身**：对话管理字段迁至 chatStore，只留 generating/factChecking + AI 浮窗 UI
- **组件树重写**：MessageItem（气泡/打字机 key 技巧/消息操作栏/工具气泡三态/引用 chips/思考折叠）、ChatInput（textarea 多行 + IME 修复 + 停止按钮 + @ 引用浮层）、MessageList（stick-to-bottom + 长会话分页窗口）、ChatSidebar（搜索/归档折叠/右键菜单）、ChatCore 整合
- **打字机**：streaming 时 ReactMarkdown `key='loading'` 增量重渲染，结束切 `'done'` 强制重挂载（对齐 NextChat）
- **消息操作**：复制 / 编辑（user 分支语义：截断+重发；assistant 本地覆盖）/ 重新生成 / 删除；流式中停止生成

### 后端：SSE 8 事件 + 会话 API 增强

- `agent.py`：tool_result 带 `refs`（从 search_docs/lookup_doc/list_docs 结果提取 docId/title）；`_stream_response` 探测 `reasoning_content` → **thinking 事件**（deepseek 思考链折叠展示）
- `chat.py`：lookup_doc 结果补 docId；chat_stream 加 `doc_refs` 参数；sse_stream 收集 tool_calls/refs/thinking 持久化
- `conversations.py`：`PATCH /conversations/{id}`（archived 归档）+ `PUT /conversations/{id}/messages`（全量 rebuild——编辑/删除/分支截断统一入口）
- `session.py` append 透传 doc_refs/tool_calls/thinking；`types.py` ChatMessage 扩展字段；store.py 会话 index 带 archived
- 新 `docs/对话协议.md`：SSE 事件流全量文档化

### 关键行为确定

- **中断持久化**：stopGenerating → abort → 后端 cancel_event → `done+cancelled`，部分回复**也会保存**（前端标「已停止」+ 保留部分内容）
- **docIds 上下文策略**：「当前选中文档 + 本条 @ 引用」（每轮动态组合去重），比建议稿的「会话内引用集合」更省 token（对齐用户 token 敏感）；已同步方案文档

### 验证

前端 100 tests（含 chatStore 18 用例）+ tsc 零错误；后端 5 测试 + e2e 36 全绿。

---

## 2026-08-03 — 视频笔记 prompt 过滤规则补「下集/续集预告」

用户审阅生成的视频笔记时发现「下集预告」被做成独立章节——纯吊胃口套话（"我知道你很急但你先别急，咱们下个视频见"）零信息量。根因：`_NOTES_TASK` 的广告/无关内容过滤规则只覆盖硬/软广告与填充词，**漏掉「下集预告/续集引流/催更」**这类视频片尾常见套话。修复：过滤规则补明确类别——纯吊胃口套话必须省略；若预告带实质信息（如下集主题）最多一句带过，不作为独立章节。

---

## 2026-08-03 — 视频下载修复：max_filesize 静默拒下载 + 按大小适配超时 + 前端提示

- **B 站 m4a 视频导入失败根因**：`max_filesize: 200MB` 让 yt-dlp 静默拒下载（390MB 视频超限，yt-dlp 只返回 info 不报错）→ `_download` 返回不存在的 `video.m4a` → ASR 报"文件不存在"
- **修复**：max_filesize 200MB → 2GB（对齐导入上限）；下载后校验文件真实存在非空（失败显式化）；临时文件查找排除 `.part/.ytdl` 残留
- **按大小适配超时 + 前端提示**（用户方案）：预取视频大小（`extract_info(download=False)`，format 与下载一致避免拿到含视频轨的虚高大小 1285MB→391MB）→ 分档 socket_timeout（<50MB:60s / 50-200:120s / 200-500:180s / ≥500:300s）→ 下载前推送「视频约 X MB，预计下载约 X 分钟」（实测 ~3MB/s 估算）

---

## 2026-08-03 — 生成参考文件（Reference Text）：参考资料注入笔记生成

### 功能

生成笔记时注入参考资料（如视频评论区观点、用户提供的审阅材料）。**官方不内置任何抓取能力**（评论区爬取有法律风险），只做中性能力：接收参考文本 → 注入生成 prompt。依据 [pi-docs/reference-text-建议.md](../../pi-docs/reference-text-建议.md)。

### 设计：暂存 id（评审 P0）

流式 generate 是 **GET**（query string），参考文件 ≤200KB 直接 JSON 进 URL 会数百 KB（代理默认 4-8KB 直接 414）。改为**暂存**：

- `POST /api/references`（`routers/references.py` + `core/reference_store.py`）→ 内存 dict + **TTL 20 分钟**惰性清理 → 返回短 id（`secrets.token_hex(8)`）
- 生成时传 `reference_ids`（流式 query 逗号分隔 / 非流式 body 数组），后端按 id 取内容注入
- 进程内存 → 重启自动清空 = **仅会话内不持久化** ✓

### prompt 注入（用户决策：仅 notes）

- 新增 `<reference_texts>` section（置于 `<user_request>` 之前，空则 compose 跳过），文本带「来源 + 仅供背景参考，与原文冲突时以原文/模型判断为准」
- **仅 `build_notes_prompt` 注入**；polished（忠实整理）与 Excel 摘要**不注入**（用户决策）
- 两条独立路径都打点：非流式（GenerateRequest → Generator → PromptBuilder）与流式（generate_stream 直接调 build_notes_prompt）

### 前端

- 生成面板「参考文件」区块（用户备注上方）：txt/md 选择器（复用 ImportSection 虚线框样式）、≤200KB 校验、已选展示 + 移除、备注 placeholder 引导
- **意图缺失确认框**：选文件未填备注 → window.confirm 两路径（确认降级背景 / 取消展开备注）
- `api/references.ts` + `BaseGenOptions.reference_ids`；参考文件 state 仅会话内（不写 localStorage）

### 涉及

新增 `core/reference_store.py` / `routers/references.py` / `frontend/src/api/references.ts`；修改 generate.py / generator.py / prompt_builder.py / prompt.py / main.py / api/generate.ts / GenPanel.tsx / GenerateOptions.tsx / tests/test_prompt.py

### 范围外（文档 4 确认）

chat 链路参考 · JSON 格式 · 评论爬取（法律风险）· process/fact_check/export · polished/Excel 注入

### 验证

- 后端 6 测试全绿（test_prompt 新增 reference 注入断言：notes 含 section / polished 无 / 空跳过 / 多段分隔）
- references 冒烟：暂存 id / 存取 / 无效 id 跳过 / TTL 惰性清理
- 前端 82 测试全绿 + `tsc -b` 类型检查通过

---

## 2026-08-03 — 插件 zip 安装器：设置页选文件安装插件

补全插件安装闭环（此前需手动拷贝到 `~/.limo/plugins/`）：

- **后端** `POST /api/plugins/install`：multipart zip 上传 → **zip slip 防护**（条目路径必须落在解压目录内，拒绝 `../`/绝对路径）→ strip 单一顶层目录（GitHub source zip）→ 校验 EnginePlugin 声明 `.py` → 解压到 `~/.limo/plugins/`（覆盖同名）→ 返回 `hasRequirements` 提示「重启后一键装依赖」。覆盖安装 OSError（venv 占用）→ 明确 AppError 而非 500
- **前端**：插件页 banner 加「安装插件」按钮（`.zip` 文件选择）→ 上传 → 成功 toast + 重启提示；AppError 明确反馈
- 安全：zip 大小 ≤ 50MB；插件 `.py` 为用户主动选择的第三方代码（固有风险自担），解压路径安全由后端保证

验证：正常 zip / GitHub source zip strip / **zip slip 攻击拒绝** / 覆盖安装 / 非 zip 与无声明明确提示——5 场景全过。

---

## 2026-08-03 — .xls 旧格式支持 + .doc/.ppt 明确提示

- **.xls**：ExcelEngine 加 **xlrd** 分支（openpyxl 不支持旧二进制格式），逐 sheet → MD 表格（数字/日期规范化），与 .xlsx 产物一致。新增依赖 xlrd + spec hiddenimports
- **.doc / .ppt**（Word/PPT 旧二进制格式）：python-docx/pptx 只支持 OpenXML → DocumentAdapter 明确提示「请另存为 .docx/.pptx 后导入」（失败显式化，不静默）

---

## 2026-08-03 — v0.2.0 发布

LiMo 0.2.0 正式发布（NSIS + MSI 安装包 + GitHub Release）。

自 0.1.6 以来的主要变化（详见下方各功能条目）：

- **插件系统正式化**：EnginePlugin v1 进程外（默认）/trusted 例外 + 管理页（启停/卸载/配置）+ 一键安装依赖 + 首个三方插件仓库发布（pymupdf4llm）
- **参考文件（Reference Text）**：参考资料注入笔记生成（暂存 id 传递，仅 notes）
- **打包体积修剪**：移除 Tesseract / docling / faster-whisper（~57% 体积，147.8M）
- **引擎降级链彻底移除**（失败显式化：绑定只取第一个，失败直接报错）
- **设计哲学**：口子原则（Keep core lean）+ 失败显式化
- **生成超时调整**（chunk 间隔 150s / 总超时 330s）+ 若干反馈/提示修复

---

## 2026-08-03 — 插件依赖一键安装（建 venv + pip 镜像源 + 失败显式化）

用户拖插件到 `~/.limo/plugins/` 后不再需要手动建 venv。前端插件页对「进程外 + 有 requirements.txt」的插件显示「一键安装」：

- **后端**：`install_plugin_deps()`（plugin_loader.py）——建 venv（base python：源码=当前后端 / **PyInstaller 打包=系统 python，Windows 优先 `py` launcher 防 Store 别名**）+ `pip install -i 清华镜像` + `CREATE_NO_WINDOW`；返回 ok / error（stderr 尾部）/ **no-python**（打包版无系统 Python → 降级引导，不静默失败）。enumerate 加 `hasRequirements`/`depsInstalled`
- **路由**：`POST /api/plugins/{name}/install-deps`（`asyncio.to_thread` 放线程池，模式 15）
- **前端**：插件卡片依赖状态 + 一键安装/重新安装按钮（失败后半截 venv 不再卡死——depsInstalled=true 时显示「重新安装」灰字）+ 失败原因/手动命令/引导提示

验证：真实安装（requests 镜像源装成功）+ 失败显式（不存在的包 → error + 原因）+ 打包降级三态（frozen 下命中 py launcher / 无 python → no-python）。

---

## 2026-08-03 — 发布 pymupdf4llm 三方插件仓库（v1.0.0）

首个公开插件仓库 [lazybone404/limo-pymupdf4llm-plugin](https://github.com/lazybone404/limo-pymupdf4llm-plugin)（Public，MIT 插件代码 + AGPL 底层依赖声明，README/Release v1.0.0）。plugins.md 新增「已知三方插件」小节——明确标注发布身份为**三方（非官方维护）**，遵循口子原则：主程序只维护契约 + 加载 + 文档，插件生态由三方发布。

---

## 2026-08-03 — 笔记生成超时各 +30s（chunk 间隔 120→150 / 总超时 300→330）

用户反馈笔记生成挂起时间需延长。三处同步 +30s：

- 流式生成 **chunk 间隔超时** 120s → 150s（双产物 + 单产物两路径；两次输出之间更长容忍，deepseek 长思考静默 90s+ 不误判）
- 流式生成 **总超时** 300s → 330s（长文档 + 多次续写的整体上限）
- LLM 客户端（OpenAI SDK）timeout 300 → 330（对齐总超时，避免 HTTP 层先掐断）

错误消息与 CLAUDE.md 描述同步更新。

---

## 2026-08-03 — 参考文件意图缺失提醒防重复：会话内只弹一次

「选文件未填备注」的确认框此前每次生成都弹（每次都点确认的用户被反复打扰，违背文档 2.3「不烦人」）。修复：GenPanel 模块级标记 `refNoNotesPrompted`，**首次**弹（无论确认/取消都先置位），本次会话内不再重复；刷新页面清空（与 refFile 仅会话内不持久化一致）。

---

## 2026-08-03 — pymupdf4llm 插件内嵌图片统一 PNG

参考实现 `examples/pymupdf4llm` 去掉 `image_format` 配置项（原可选 png/jpeg），内嵌图片统一 PNG（嵌入式最常见格式）。`engine.py` 固定 `image_format="png"`，与核心引擎 pypdfium2 / 用户侧 heavy 版一致，确保 `_persist_media_images` 的 glob 覆盖无缺口。

---

## 2026-08-03 — 插件管理反馈修复：开关即时翻转 + 待重启徽章 + 重启后端按钮

### 问题

设置页插件**启用/禁用开关点击后完全无变化**，用户不知道是否已生效。

### 根因

`enable/disable` 路由只写 config（`plugin_states`）到磁盘，**未同步 `PLUGIN_REGISTRY` 内存态**——前端点完开关重拉列表，`enabled` 仍是启动时固化的旧值 → 开关视觉纹丝不动。

### 修复

- **后端**：新增 `set_plugin_enabled()` 同步内存态（enable/disable 路由调用），开关即时翻转
- **后端**：`PLUGIN_REGISTRY` 加 `loaded` 字段（运行时是否已注册进 `EngineRegistry`，仅启动时决定）；`enumerate_plugins()` 返回 `pendingRestart`（`enabled != loaded`）
- **前端**：卡片显示「待重启」徽章（`重启后生效` / `重启后停用`）——明确告知"期望状态已改，引擎在下次启动时注册/注销"
- **前端**：设置页加「重启后端」按钮——Tauri 生产调用 Rust `restart_backend_sidecar` + 轮询 `/health` 就绪后重拉插件列表；浏览器 dev 模式提示手动重启

### 决策：不做热加载

用户确认**不做热加载**，保留「重启后生效」干净语义。理由：

1. 进程外插件每次调用都是 spawn 独立子进程，没有"热/冷"之分，热加载只省一个重启
2. 禁用正在被使用的引擎 → 新请求绑定解析立即失败（隐性），违背「失败显式化」
3. 热加载 = config / PLUGIN_REGISTRY / EngineRegistry 三方运行时同步 + 竞态，复杂度与收益不成正比
4. 真实痛点是反馈缺失（开关无变化），已由本次三项增强解决

### 影响范围

`plugin_loader.py` / `routers/plugins.py` / `frontend/src/api/plugins.ts` / `frontend/src/components/settings/PluginSettings.tsx`

### 验证

- 后端 6 个测试全绿；`set_plugin_enabled` + `enumerate` 联动单测通过（enable→True / disable→False / pendingRestart 四场景）
- 前端 82 测试全绿 + `tsc -b` 类型检查通过

---

## 2026-08-03 — 设计哲学沉淀：口子原则 + 失败显式化

确立两条设计哲学（写入 [CLAUDE.md](../CLAUDE.md) 与 [architecture.md](../docs/architecture.md)）：

- **口子原则（Keep core lean）**：核心只保留轻量、稳定、可控的能力；重依赖/协议/难维护/法律风险的能力走插件口子（`~/.limo/plugins/`）。判断「内置 vs 插件」四标准：依赖重 · 协议问题 · 跨平台难维护 · 法律风险。已落地：whisper / Tesseract / docling 移出核心，pymupdf4llm 进程外插件
- **失败显式化（No silent degradation）**：核心内在语义干净——引擎绑定只取第一个失败直接报错（无降级链，2026-08-03 彻底移除）；扫描件 OCR 失败占位带原因

---

## 2026-08-03 — 彻底移除引擎降级链

### 做了什么

用户要求**引擎不应有降级链，彻底移除**；大模型降级链 + 用户可配置降级链留后面再做。

- **Engine 协议层删 fallback**（protocols.py）：删 `fallback` 字段 + `run()` 失败时委托 `fallback.run()` 的分支——`run()` 直接 `execute()`，失败抛出
- **init_engines 删 fallback 赋值**（dashscope-asr / bcut 的 `fallback = None`）
- **扫描件 OCR 移除多引擎降级链**（document.py）：`_resolve_ocr_engine_chain` → `_resolve_ocr_engine()` 返回单个（绑定第一个可用）；`_ocr_pdf_with_images` 逐页不再尝试多个引擎，只用第一个，失败 → **占位带原因** `_(OCR 失败: <原因>)_`（不再「所有 OCR 引擎均失败」）
- **Doc 主循环简化**：`for`+`break` → 取 `engine_chain[0]` 直接执行；删不可达 `if not raw_text` 分支；保留链空处理（扩展名兜底 → 占位 AdapterOutput）
- test_engines.py 删 fallback 检查，降级链测试改为「引擎失败直接报错测试」；registry.py 注释同步

### 行为变更

- 引擎失败不再自动切换其他引擎（绑定只取第一个是既有语义，本次彻底移除协议层 fallback 兜底）
- 扫描件 OCR 无可用引擎 / 无 API Key → 占位文本**带失败原因**（利于排查），不再尝试下一个引擎

### 保留（非引擎降级链，不动）

- 扩展名兜底（未绑定时的默认引擎）、绑定修复回退（config 迁移）
- process.py LLM→OCR 回退、agent 搜索降级（Tavily→SearXNG→DDG）、webpage 提取回退——均属大模型降级链 / 非引擎，留后置

### 验证

- 后端 7 测试全绿（test_engines 无 fallback 报错）
- 冒烟：capabilities 正常（doc: pypdfium2 等、ocr: dashscope 系列）+ 导入 PDF 走 pypdfium2 成功
- 扫描件 OCR mock 验证：单引擎失败 → 占位带原因

---

## 2026-08-03 — 移除 DoclingEngine + 体积二次修剪

### 做了什么

**移除 DoclingEngine**（打包版 docling 依赖 torch ~2GB 无法内置，实际一直不可用）：
- `docling.py` 删 DoclingEngine，保留 PyPdfium2Engine（独立于 docling 包，默认 PDF 引擎不受影响）
- `init_engines` 删注册 + `_migrate_remove_docling` 迁移（清 builtin_models / engine_models / 指向 docling 的绑定）
- config / capabilities / document 兜底引擎 docling → pypdfium2
- spec 移除 docling/docling_parse/rapidocr/huggingface_hub 收集；onnxruntime 保留（magika 文件类型检测需要）
- `.venv` 卸载 docling 依赖链（torch/transformers/scipy/pandas/sympy/rapidocr/cv2/av/ctranslate2 等 ~1.3G → 398M）

### 行为变更

- **docling 引擎不再提供**（无论打包版还是开发版）。PDF 布局解析回归 pypdfium2（文字+表格+嵌入原图，毫秒级）；需要 docling 式布局/矢量图解析的用户可装三方插件（参考 pymupdf4llm 模板）

### 打包体积累计（本次 + 上次 tesseract 修剪）

| 指标 | 原始 | 现在 | 节省 |
|------|------|------|------|
| PyInstaller `_internal` | 955M | 222M | **733M（77%）** |
| 后端 exe | 67M | 27.6M | 39M |
| `.venv` 依赖 | ~1.3G | 398M | ~900M |
| NSIS 安装包 | 345M | 待 tauri build 确认 | — |

### 影响范围

- 代码：`docling.py` / `engines/__init__.py` / `config.py` / `capabilities.py` / `document.py` / `LiMoBackend.spec`
- 文档：architecture / STATUS（引擎计数 10→9）/ CLAUDE.md / plugins.md
- 验证：后端 7 测试全绿 + 打包产物冒烟（导入 PDF 走 pypdfium2 正常）+ 关键包保留（numpy/onnxruntime/magika）

---

## 2026-08-02 — 移除内置 Tesseract + 打包体积修剪

### 做了什么

**移除内置 Tesseract OCR**（体积修剪 + 插件化方向，与 faster-whisper 同模式）：
- 删除 `TesseractEngine`（ocr.py）、init_engines 注册块、config/capabilities 的 tesseract 条目、spec 的 pytesseract（datas/hiddenimports/collect_all）
- 删除 `backend/bin/tesseract/`（280M，含 ~60M 训练工具 + 57M tessdata + DLL）+ setup.exe + zip
- 新增 `_migrate_remove_tesseract` 启动迁移（清理 engine_models 的 tesseract 条目 + engine_bindings.ocr 悬空 id）
- **体积节省**：tesseract 整链 ~378M（bin 280M + PyInstaller 顶层重复 libtesseract-5.dll 97M + pytesseract 1M）+ spec excludes `av`/`faker` ~12M（docling 运行时主链路不加载，静态收集冗余）

### 行为变更

- **本地无 key OCR 从「可用」变「占位」**：无 dashscope API Key 时扫描件 OCR 逐页输出「原图 + `_(所有 OCR 引擎均失败)_`」占位文本（之前 Tesseract 是本地兜底）。需本地 OCR 请装三方插件参考模板 [examples/tesseract_plugin.py](../backend/plugins/examples/tesseract_plugin.py)（含 tesseract 便携版自备说明 + chi_sim 排查）
- 前端引擎下拉自动不再显示 Tesseract（全动态渲染，零改动）

### 影响范围

- 代码：`ocr.py` / `engines/__init__.py` / `config.py` / `capabilities.py` / `LiMoBackend.spec`
- 模板：`examples/tesseract_plugin.py` + `tesseract/{engine.py, README.md, requirements.txt}`
- 文档：`plugins.md` / `CLAUDE.md` / `STATUS.md`（引擎计数 11→10）
- 验证：后端测试全绿 + capabilities 冒烟 + 前端 82 tests + 重新打包体积对比

---

## 2026-08-02 — 插件系统完整正式化（EnginePlugin v1 · pymupdf4llm 首例）

### 做了什么

按 `pi-docs/12-插件系统设计建议` + 用户定稿 v2（删除协议维度）完整正式化插件系统：

**核心 bug 修复（驱动本计划的硬伤）**：
- `_PluginEngineAdapter.execute()` 原返回纯 `str`，消费方（document.py `.text` / video.py `.segments` / image.py `.text`）访问结构化字段**必崩 AttributeError** → pymupdf4llm 插件一直不可用
- 修复：`_dispatch_result(category, payload, name)` 按 category 分派返回 `DocResult`/`OCRResult`/`TranscriptResult`，消费方零改动

**EnginePlugin v1 契约**（`plugin_loader.py`）：
- 默认进程外 + trusted 例外门控：`command` 有值→external；`trusted=true`→inproc（全类别统一，引擎不特殊）；无 command 且未 trusted→拒绝加载
- **trusted 为纯技术声明**（可进程内 import，崩溃/依赖风险自担），不涉及任何协议判断；主程序不判断/不警告/不声明插件协议，license 仅前端展示
- 元数据字段位：`display_name`/`user_desc`/`config_schema`/`namespace`/`permissions`/`api_version`(PLUGIN_API_VERSION)

**进程外契约 JSON 化**：
- stdin JSON：`{file, cancel_file(绝对路径), config(插件自身配置), ...}`；argv 保留 `--input`（v0 兼容）
- stdout JSON：doc/ocr→`{text,pages,is_scanned}`、asr→`{segments}`；**非 JSON 回退 v0 纯文本** → 用户旧 `pymupdf4llm_heavy` 零改动恢复可用
- `subprocess.run`→`Popen` + cancel 标记文件（监视线程写标记→宽限 3s→kill，timeout 兜底）

**管理 API + 前端**：
- 新增 `routers/plugins.py`：GET 列表（含禁用）· POST enable|disable（写 `plugin_states`，重启生效）· DELETE 卸载（resolve 路径校验 + 清理残留键 + Windows 占用反馈）· PUT config（写 `plugin_configs` + `set_plugin_config` 内存即时生效，**stdin 即含新 config**）
- `PLUGIN_REGISTRY`（Phase 1 建立）登记全部插件（含门控拒绝/禁用），管理 API 数据基础
- 设置页新增「插件」tab（PluginSettings.tsx）：按 category 分组卡片 + 启用开关 + 卸载二次确认 + config_schema 简版表单
- capabilities：插件判定 `bool(_license)`→`is_plugin` 标志；engineOptions 增 `displayName/userDesc/configSchema`
- EngineSelect：license 徽章去警告语义（v2：仅展示）；optionLabel 优先 displayName；ProviderSettings 修复丢 license/plugin 徽章

**参考模板 + 文档**：
- `backend/plugins/examples/` 重构：`pymupdf4llm_plugin.py`+`pymupdf4llm/{engine.py,requirements.txt}`（进程外参考）+ `simple_trusted_plugin.py`（trusted 进程内参考），删旧进程内示例；**不随包分发**（spec 不加 datas）
- `docs/plugins.md` 按 v1 契约重写（契约/门控/安装/发布指南）；新增 `test_plugin_loader.py`（14 用例）

### 为什么

whisper 已移除且不插件化；pymupdf4llm 作为 AGPL 引擎走插件通道，但插件返回类型不匹配导致不可用。按 12 号文档完整正式化插件系统，pymupdf4llm 为首个参考实现。

### 影响范围

- 后端：`plugin_loader.py` / `capabilities.py` / 新增 `plugins.py` / `config.py` / `main.py` / `registry.py`（命名空间注释）
- 前端：新增 `plugins.ts` / `PluginSettings.tsx` / `SettingsPage` 插件 tab / `ProviderSettings` / `EngineSelect` / `core.ts`
- 模板/文档：`examples/` / `plugins.md` / 新增 `test_plugin_loader.py`
- 验证：后端 7 测试全绿 + 前端 82 tests 全绿 + 启动冒烟（`/api/plugins` 返回用户已部署 pymupdf4llm_heavy）+
  端到端（旧纯文本插件走 subprocess → DocResult，文本提取成功）

---

## 2026-08-02 — 卸载时可选移除个人配置与数据

### 做了什么

**自定义 NSIS 模板**（`frontend/src-tauri/nsis/installer.nsi`，基于 Tauri 内置模板 + Handlebars 占位符），扩展 Tauri 卸载器「删除应用数据」复选框的行为：

- 勾选时除 Tauri 框架数据（`%APPDATA%\com.limo.app` / `%LOCALAPPDATA%\com.limo.app`）外，**一并删除 LiMo 业务数据目录** `~/.limo`（知识库/配置/文档）及降级目录（`%LOCALAPPDATA%\LiMo`、`%APPDATA%\LiMo`）
- `tauri.conf.json` 加 `bundle.windows.nsis.template` 指向自定义模板
- **默认不勾选** → 卸载重装保留数据；勾选 → 完全移除（含个人配置与知识库）

### 影响范围

- 打包配置（NSIS 模板 + tauri.conf.json），代码零改动
- 验证：`npx tauri build` 渲染成功，生成的 installer.nsi 确认含 `RmDir /r "$PROFILE\.limo"` 及降级目录

---

## 2026-08-02 — 全面漏洞排查与修复（P0 高危 8 项 + 关键 P1 10 项）

### 做了什么

3 个排查 agent 分区扫描（后端逻辑/并发安全/前端）共 55 项发现，去重分级后本次修复 **P0 高危 8 项 + 关键 P1 10 项**；P2 低危（信号量 acquire 在 try 外、mktemp 废弃、死代码、前端次要项、SSRF 重定向加固）记录不修。

**P0 安全（4 项）**：
- **CORS Origin 白名单**（main.py）— 从 `allow_origin_regex=".*"` 收紧为 `tauri://localhost`/`https://tauri.localhost`/本机 localhost 端口白名单，`_ForceCORSMiddleware` 仅白名单 origin 回显凭证 → 阻隔任意恶意网页跨站读取本地后端（含 `/api/config` 的 Cookie 明文、文档、日志）与 POST 篡改配置
- **serve_asset 路径遍历**（ingest.py）— 校验收紧到 `doc_dir/assets/`（原只限 data_root，filename 含 `..` 可越出读任意文档图片）
- **导出 temp_id 路径穿越**（export.py）— 校验字符集 + resolve 后限定 `_TEMP_DIR`（原 `os.path.join` 直拼，`..%2F` 可读/删任意 .zip）
- **content_preparer asset_ref 任意读** — `assets/../../config.yaml` 越界校验（原未限目录，可读任意文件 base64 发 LLM/导出包）

**P0 必崩（3 项）**：
- **Generator.config 未初始化**（generator.py）— `__init__` 不设 `self.config`，非流式 `POST /generate` 开 OCR → AttributeError（修复：`__init__` 加 config 参数，generate.py 传参）
- **process_image_legacy 500**（process.py）— 废弃端点直接调用带 `Depends` 的函数拿到未解析对象（修复：注入 store/config/guard 后转发）
- **chapter_indices 强转 int**（generate.py）— 非数字章节号 ValueError → 500（修复：`isdigit()` 过滤）

**P0 竞态（2 项）**：
- **适配器单例竞态**（registry.py + adapters/__init__.py）— `AdapterRegistry` 改存**类**，`route()` 命中前实例化 → 一请求一实例，并发导入的 `cookies`/`_engine_config`/`cancel_event` 不再互相污染
- **/process 断连任务泄漏**（process.py）— 手写 `_watch_disconnect` 从不 cancel，改复用 `disconnect_watcher` 自动清理

**P0 前端卡死（3 项）**：
- **streamChat 永久卡加载**（chat.ts）— 缺终态追踪，断连后 onDone/onError 不触发（对齐 generate.ts 的 `receivedDoneOrError`）
- **handleFactCheck 卡死**（PreviewArea.tsx）— `getConfig().then` 无 `.catch`，配置失败时 `factChecking` 永不复位
- **streamGenerate 取消误报**（generate.ts）— `cancelled` 不置终态标志 → 取消后误弹「连接意外断开」

**关键 P1（10 项）**：
- 非流式单产物截图写 `store.root` 错误目录 → 修正为文档目录 + 防御 None
- review 流式验证死锁（`queue.get()` 无超时 + cancel 瞬间等 sem 任务不放队列）→ 加 `wait_for` 超时
- EPUB 章节选择串文档 → PreviewArea 渲染加 `key={currentDocId}`
- 事件循环阻塞：image/webpage 适配器、封面下载、bilibili/youtube 字幕的同步 `requests.get` → 统一 `asyncio.to_thread`
- 临时资源泄漏：VL 截帧目录（数百 MB）、视频下载临时目录、上传临时源文件、空 media_dir、导出 ZIP 激活 `_TEMP_TTL` 过期清理
- LLMService.aclose()：generate 流式/chat（流式+非流式）/process/fact_check（流式+非流式）主端点关闭 httpx 连接池

### 影响范围

- 后端：`main.py` · `registry.py` · `adapters/{__init__,video,image,webpage}.py` · `routers/{generate,process,ingest,export,chat,fact_check}.py` · `generation/{generator,review,content_preparer}.py` · `llm/service.py`
- 前端：`api/{chat,generate}.ts` · `components/preview/{PreviewArea,EpubSkeletonPanel}.tsx`
- 验证：后端 5 单测全绿 + TestClient 7 项端到端（CORS 白名单/遍历拒绝/temp_id/legacy 不再 500）+ Generator.config + 前端 build + 82 测试全绿
- 记录不修：P2 低危（见计划文末）

---

## 2026-08-02 — 整理版原文适度利用 Markdown 排版

### 做了什么

**polish（整理版原文）5 类 prompt 统一加入「适度 Markdown 排版」引导** — 解决 polished 产物偏「段落式」、Markdown 排版能力未充分利用的问题。

- **背景**：polish 定位「忠实整理原文」，此前 prompt 只引导「`##` 分段标题」+ 公式 LaTeX，缺少像 notes 那样的排版引导 → 实际产物标题层级有，但加粗/列表/引用块/表格呈现弱
- **改动**：`_POLISH_TASK`（视频）/ `_DOC_POLISH_TASK` / `_WEB_POLISH_TASK` / `_TEXT_POLISH_TASK` / `_IMAGE_POLISH_TASK` 末尾各加一条「适度利用 Markdown 排版」：
  - 原文本身就是列表/表格/引用的内容 → 用对应 Markdown 元素呈现（`-` / `| ... |` / `>`）
  - 关键术语/数据可加粗；段落间空行分隔
  - **明确禁止为排版重组/改写/总结原文**——排版服务于忠实呈现，结构化重组是「笔记」职责（忠实优先，避免偏离 polish 定位）
- **影响**：仅 prompt 文本，零代码逻辑改动；已生成产物不变，重新生成后生效

### 影响范围

- 后端 `app/generation/prompt.py`（5 个 polish task 常量）
- 已生成产物不受影响（重新生成才用新 prompt）

---

## 2026-08-02 — 文档全面整理：清理过时文档 + 合并有效内容

### 做了什么

**1. 删除 10 个过时/无参考价值文档**（约 3000 行）：

- `docs/system-design-spec.md`（1146 行）— 原始设计蓝图，已标注「实际实现见 CLAUDE.md/STATUS」，技术选型（FunASR/MinerU/PaddleOCR）全部被实际实现取代
- `docs/doc-transcript-design.md`（256 行）— Doc 转录原始提案，有效部分（五类文档差异总览）已合并进 architecture.md
- `design/engine-standards.md`（246 行）— 历史引擎评审（pymupdf/faster-whisper 均已移除、绑定 key 已拆分子场景），有效部分（评判标准）已合并进 architecture.md
- `pi-docs/` 下 7 个文件（竞品分析 FlyMD/memos/immich、架构图、待做列表、设计提案、计划审阅）— 借鉴点已沉淀到 CLAUDE.md；**pi-docs/ 目录保留**（作为 agent 评审文档存档处）
- 注：删除的架构图/待做列表/设计提案中有价值的**待做项已并入 CLAUDE.md「🚀 规划中」**

**2. 合并有效内容到 [architecture.md](docs/architecture.md)**：
- 追加「五类文档差异总览」表 + 提取策略要点（Word 文档流 / PPT 按页 / Excel 按 sheet / EPUB 分章）
- 追加「引擎评判标准」（职责兑现：capabilities 宣称必须做到）
- 更新过时内容：引擎计数 13→11（ASR×4→×2）、AI 审核三阶段→四阶段（Static Review→Verification→Evidence→Report）、Process 取消陷阱（已全部接入 disconnect_watcher）

**3. CLAUDE.md 更新**：
- 新增「🚀 规划中」节（语义搜索 / 更多导入源 / 知识图谱 / 渲染风格预设 / remark-tag / 沉浸阅读 / 插件挂载 / 后台队列 / 分层重构 / UI 重构，合并自被删的 08/09）
- 文档索引移除 4 个被删文档条目（system-design-spec / doc-transcript-design / engine-standards ×2）

**4. 其余文档过时内容清理**：
- `design/components.md`：faster-whisper/Groq/PaddleOCR/TrOCR 引擎 mockup → bcut-asr/dashscope-asr/tesseract（当前实际引擎）
- `docs/STATUS.md`：引擎计数 13→11
- `docs/prompts-inventory.md`：「pymupdf 合并单元格错位」→「引擎合并单元格错位」（pymupdf 已移除）
- `SECURITY_AUDIT.md`：标注时点审计性质 + H1/M1/L1 已修复状态（追溯用，避免误读为当前漏洞）

### 影响范围

- 纯文档维护，零代码改动
- 保留文档：CLAUDE.md / README.md / SECURITY_AUDIT.md / docs（architecture/frontend-patterns/export-pipeline/plugins/prompts-inventory/STATUS/BUILD/qianwen-models/user-scenarios/CHANGELOG）/ design（components.md + tokens.css）/ pi-docs（空目录）

---

## 2026-08-02 — 移除 faster-whisper 内置 ASR + 移除引擎自动降级

### 做了什么

**1. faster-whisper 彻底移出内置** — 体积大（模型 464MB~3GB）、CPU 推理慢、GPU 又依赖笨重的 CUDA/cuDNN/cuBLAS 运行时，不适合随安装包分发。移除后默认 ASR 只剩 **bcut-asr**（免费云，无需 Key）+ **dashscope-asr**（需 Key）。

- 抽取 `TranscriptResult` / `TranscriptSegment` 到独立 [transcript.py](backend/app/engines/transcript.py)（原定义在 asr.py 里，删除后 bcut/asr_cloud 的 import 不破）
- 删除 `FasterWhisperEngine` 注册 + [asr.py](backend/app/engines/asr.py) 整文件
- 打包清理：spec 移除 `faster_whisper`/`ctranslate2`/`av`/`tokenizers`（保留 `onnxruntime`——magika/rapidocr 依赖）、pyproject 移除 `faster-whisper` 依赖
- **用户 config.yaml 残留迁移**（防启动后设置页仍有 whisper 条目、绑定后无引擎报错）：`init_engines` 开头清理 `engine_providers` 的 faster-whisper 整组 + `engine_models` 相关条目 + 残留 `engine_auto_fallback` 键，变更计入 save_config 判定落盘；`config.py` 的 `_sync_builtin_models` 废弃 provider 集合加入 faster-whisper（双保险）
- 示例文档 ASR 表格 / QA 同步更新（本地转写引擎标注「需自行安装」）

**2. 移除引擎自动降级**（`engine_auto_fallback`）— 内置引擎官方测试稳定，绑定列表改为**只取第一个可用引擎，失败直接报错**；用户换引擎去设置页手动改。降级链分工：
- **引擎**（内置，稳定）→ 移除多引擎尝试循环（image/video/document/process 四个解析点）
- **LLM**（外部服务，中转站不稳定需常换）→ **保留**备用模型降级语义，独立于引擎体系。process.py 的 LLM 不可用 → OCR 引擎回退**无条件保留**（大模型降级有价值，计划中的「备用模型配置」本次不实现，仅记录方向）
- **视频字幕回退**（字幕失败 → 下载转写）→ 属网络获取策略回退，**默认执行、不向用户暴露**：去掉 `engine_auto_fallback` 条件无条件回退，warning 弱化为 **debug 日志**
- `_transcribe` 的 fallback 链循环（`while current is not None`）一并简化：单引擎执行，防未来插件设置 fallback 复活降级行为
- 报错文案清理：document.py「或在设置中开启引擎自动降级」→「请检查引擎配置或更换引擎」；process.py「自动降级已关闭」文案随分支删除
- 前端：删除 `getEngineAutoFallback`/`setEngineAutoFallback` API + ProviderSettings 的「引擎自动降级」开关 UI + faster-whisper 删除按钮保护引用

**3. LLM 自动降级列入计划（本次不实现）** — 设计方向：`resolve_model()` 或 `LLMService.call` 层捕获 provider 失败 → 自动切换到配置的备用 provider/模型（如 deepseek 中转失败 → 切 openai 直连）。备用模型是**独立 provider 级配置，不参与 engine_bindings 体系**。需要设置页「备用模型」配置 + 失败判定（网络/429/5xx）+ 切换不重试死循环。

### 影响范围

- 后端：新增 `engines/transcript.py`（数据类抽取）；删除 `engines/asr.py`；改 `engines/__init__.py`（迁移 + 删 whisper 注册）、`adapters/{image,video,document}.py`（绑定只取第一个）、`routers/process.py`（LLM 回退无条件 + 单引擎）、`routers/{model,capabilities,ingest}.py`（删降级 API / provider / 注入）、`core/config.py`（删配置项 + provider + 废弃集合）
- 前端：`api/settings.ts` + `components/settings/ProviderSettings.tsx`（删降级开关）
- 测试：`tests/test_engines.py`（删 whisper 断言）、`tests/test_e2e_smoke.py`（删第 8 节降级 toggle）
- 文档：CLAUDE.md / architecture.md / plugins.md / README.md / STATUS.md / sample_docs 同步

---

## 2026-08-02 — 预置示例文档「✨ 使用说明」+ 修复全新安装启动崩溃

### 做了什么

**1. DocLibrary 首启自带「✨ 使用说明（示例）」文档** — 面向新用户的示例/欢迎文档，作者写给用户的应用说明 + 免责声明，同时演示 LiMo 的 Markdown 排版能力。

- 内容分层三 tab：
  - **「笔记」** — 面向大众：一分钟了解 / 核心功能 / 痛点场景对照 / 开始使用 / 名字与理念（沥墨）/ 常见问题 QA
  - **「整理版原文」** — 深度说明：处理链路 / 转换引擎详解（Doc/ASR/OCR 三张表格）/ 大模型选择与场景绑定 / 自定义大模型接入（图形界面 + config.yaml）/ 数据与隐私 / 免责声明
  - **「转录」** — 简短欢迎语
- **内容源为独立 md 文件**（`app/core/sample_docs/notes.md` + `polished.md`）—— 改 md 即更新，无需改代码；PyInstaller datas 同步打包
- **内容指纹同步机制**（marker 存 notes+polished 联合哈希）：
  - 改 md → 下次启动自动同步
  - 内容更新 → 即使被删除也重建（**兼作更新说明**，让用户知道新内容）
  - 内容无更新 + 已删除 → 尊重删除不打扰
  - 重新安装（数据目录清空）→ 自然重建
- 实现：[sample_doc.py](backend/app/core/sample_doc.py) + [main.py](backend/main.py) startup 挂载（置于 init_engines 之后）+ [LiMoBackend.spec](backend/LiMoBackend.spec) datas

**2. 修复全新安装启动崩溃（严重）** — `DEFAULT_CONFIG` 的 `engine_bindings` 中 `doc`/`doc:epub` 为**空 list**，[engines/__init__.py](backend/app/engines/__init__.py) 清理失效绑定循环 `emap.get([])` 抛 `TypeError: unhashable type: 'list'` → 全新安装（无 `~/.limo/config.yaml`）启动必崩；打包版能跑只因用户 config 绑定已非空。修复：空 list 视为无效绑定，自动回填该子场景默认引擎（doc→pypdfium2、doc:epub→ebooklib 等）。

### 影响范围

- 后端：新增 `app/core/sample_doc.py` + `app/core/sample_docs/`（首次启动自动创建示例文档，不影响已有数据；marker 幂等）
- 修复启动崩溃 → 新用户开箱即用
- 前端零改动

---

## 2026-08-02 — v0.1.5：docling 打包崩溃 + 文档图片三开关链路修复

### 做了什么

**1. 打包版 docling 引擎崩溃（pdf_resources 缺失）** — v0.1.4 打包版导入 PDF（绑定 docling）报 `RuntimeError: no existing pdf_resources_dir: C:\Program Files\LiMo\_internal\docling_parse\pdf_resources/` → FatalError。

- 根因：`docling_parse`（docling 的 Rust 解析后端，独立包）含数据目录 `pdf_resources/`（440 文件，24MB）+ 3 个依赖 DLL（`libgcc_s_seh-1.dll`/`libstdc++-6.dll`/`libwinpthread-1.dll`）。spec 中 `collect_all` 列表只有 `'docling'`，不会递归收集依赖包 `docling_parse` 的数据文件 → 打包版 `_internal\docling_parse\` 只有 .py/.pyd，`pdf_resources` 缺失。docling 初始化 `DoclingPdfParser`（编译进 `pdf_parsers.pyd`）按 `Path(__file__).parent / "pdf_resources"` 定位失败
- 修复：spec `collect_all` 列表加 `'docling_parse'`（实测 datas 448 条含 440 条 pdf_resources + 3 DLLs + 4 hiddenimports）
- ⚠️ 教训：`collect_all('docling')` 不递归收集依赖包数据文件，PyInstaller 下必须逐个显式收集有数据目录的依赖包

**2. 文档图片三开关（保留/OCR/AI分析）正交化** — 原前端把三个开关做成嵌套依赖，多组合不可达：

- 前端 [GenerateOptions.tsx](frontend/src/components/gen-panel/GenerateOptions.tsx)：`AI 分析图片` 曾依赖 `formatImages && !ocrEnabled`（OCR 勾上后 AI 分析选项消失，人为互斥）；`OCR`/`AI 分析` 都依赖 `formatImages`。改为三个平铺独立开关，任意组合
- 前端 [GenPanel.tsx](frontend/src/components/gen-panel/GenPanel.tsx)：`image_analyze`/`image_optimize` 曾被 `formatImages` 门控（不勾保留图片则 AI 分析静默失效），改为独立传递
- 后端 [generator.py](backend/app/generation/generator.py)：非流式端点两处 OCR 调用漏传 `doc_dir` → 新格式 `assets/img_NNNN.png` 定位到错误路径 `{store_root}/files/assets/...`，OCR 全部 `[图片缺失]`。流式端点原本正确，前端走流式所以未暴露
- 后端 [ocr_preprocessor.py](backend/app/generation/ocr_preprocessor.py)：正则只匹配 `img_NNNN`，docling 引擎产出 `fig_NNNN` 被跳过。放宽为 `(?:img|fig)_\d{4}`（捕获组结构保持 3 元素解包不变）

**3. 笔记 prompt 加 Markdown 排版引导** — 5 个笔记变体（视频/网页/文本/图片/文档）统一加一句：「充分利用 Markdown 排版：不要只用列表平铺内容。按内容形态选样式——对比/条目用表格、论断/名言用引用块、原始文本用代码块、章节用分割线、重点加粗」。（通用版，按内容形态给判据而非列举式）

**4. GenPanel 类型选择器顺序调整** — `link → video → text → file → image` 改为 `video → file → link → text → image`，视频和文档（file）靠前。

### 影响范围

| 文件 | 变更 |
|------|------|
| `backend/LiMoBackend.spec` | `collect_all` 加 `'docling_parse'` |
| `backend/app/generation/generator.py` | OCR 调用补传 doc_dir（2 处） |
| `backend/app/generation/ocr_preprocessor.py` | 正则容纳 img_/fig_ |
| `backend/app/generation/prompt.py` | 5 个笔记 prompt 加 Markdown 排版引导 |
| `frontend/src/components/gen-panel/GenerateOptions.tsx` | 三开关平铺独立 |
| `frontend/src/components/gen-panel/GenPanel.tsx` | image_analyze 独立传递 |
| `frontend/src/components/gen-panel/ImportSection.tsx` | 类型选择器顺序 |

---

## 2026-08-01 — 发布前修复：换行标题目录崩溃 + e2e_smoke 健壮化

### 做了什么

**标题含换行/控制字符 → 目录创建崩溃（SSE 永不完成）** — `_get_or_create_doc_dir` 的清洗正则 `[<>:"/\\|?*]` 漏掉控制字符。标题含 `\n`（如 `_extract_title_from_rawtext` 从多行文本提取标题）时 `Path.mkdir` 抛 `WinError 123`（文件名语法不正确）→ `run_pipeline` 崩溃（`Task exception was never retrieved`）→ **done 事件永不发** → 客户端（前端/测试）无限等待。

- 根因：`re.sub(r'[<>:"/\\|?*]', '', title)` 未覆盖 `\x00-\x1f`（换行/回车/TAB 等 Windows 非法控制符）
- 修复：两处（查找/创建）清洗正则补 `\x00-\x1f`
- 触发面：任何标题含换行的文本/文档导入；e2e_smoke 的 text 用例（`# Hello World\n\nTest...`）恰好触发——**发布门禁价值：不跑全量测试就发现不了**

**e2e_smoke SSE 读取健壮化** — `_post_form` 改用逐行读 + done 事件 break，不依赖连接关闭（uvicorn StreamingResponse 结束后保持 keep-alive，`read()` 可能等 EOF 超时）。timeout 15→30s。

### 影响范围

| 文件 | 变更 |
|------|------|
| `backend/app/storage/store.py` | `_get_or_create_doc_dir` / `_get_doc_dir` 清洗补控制字符 |
| `backend/tests/test_e2e_smoke.py` | SSE 逐行读取 + timeout 30s |

---

## 2026-08-01 — 生成并行化（推翻串行）+ 思考链展示 + 产物写反修复

### 做了什么

**服务端（generate.py / service.py / prompt.py）**：
- **双产物改回并行（推翻上一条目的串行决策）**：上一条记录"串行"原因是 deepseek 并发排队（第二个响应 108s+），用户实测后确认排队结论证伪——108s+ 实为 **thinking 阶段思考时间长**（服务端静默不推流）。并行时两路同时开始，总耗时 = max 而非叠加，且 notes 先出、体验渐进。两路事件经 `asyncio.Queue` 汇合（chunk 交错到达）→ SSE 顺序安全，最终 `asyncio.gather` 收拢结果
- **思考链回调**（`LLMService.stream` 加 `on_reasoning`）：deepseek-v4 的 `reasoning_content` 逐段回调，不混入正文——`getattr(delta, "reasoning_content", None)` 防护 qwen 等无此字段的模型；单产物路径收集后统一带出（回调累积 → anext 返回 → 发 SSE）
- **思考强度控制**（`reasoning_effort` 经 OpenAI SDK `extra_body` 传递）：deepseek-v4 文档确认 thinking 默认开启且 effort 默认 high——复杂任务思考 90s+ 零数据（用户感知"慢到诡异"）。**polished 专用 `reasoning_effort="low"`**：整理版原文是忠实转录整理任务，无需深度思考，思考静默期大幅缩短（用户实测"生成速度得到极大提升"）。qwen 忽略 extra_body 未知字段，天然兼容
- **chunk 间隔超时 90s → 120s**：45s/90s 判定曾把正常长思考误判为挂起触发重试；推理链 chunk 到达时 anext 正常返回（有 reasoning 也在推流），不触发超时
- **prompt 模糊约束**：三个 polish task（_POLISH_TASK / _DOC_POLISH_TASK / _IMAGE_POLISH_TASK）加"转录不清处不强行推理、忠实呈现"，减少过度思考诱因
- **⚠️ 产物写反修复（gather 解包顺序错位）**：`asyncio.gather(*_tasks)` 返回顺序 = 任务创建顺序，但解包时 notes 任务的结果被赋给 `polished_chunks` → notes_prompt 的笔记内容存进 polished.md、polished_prompt 的整理版原文存进 notes.md（前端显示层忠实读取磁盘，两边都显示"对的文件、错的内容"）。实测铁证：notes.md 43KB 流水账 vs polished.md 14KB 结构化要点。修复：解包顺序与任务创建顺序对齐

**前端（GenPanel / PreviewArea / ProcessingPlaceholder）**：
- **思考链展示**：SSE `type=reasoning` → `onReasoning` 回调 → `TaskInfo.reasoning {notes, polished}`（与 progress 同构，并行双产物 task.mode 来回跳也不串台）→ ProcessingPlaceholder 渲染"模型思考中..."滚动区。思考链是临时展示（打消用户不耐心），正式文本流式生成后直接替换
- **reasoningBuf per-taskKey Map**：双文档并发生成思考链不互踩（此前单 ref 污染 + 节流负数冻结）
- **genOutcomeRef 状态机**（ok/error/partial）：修复"超时报错 + 生成成功双 Toast 矛盾"——部分失败只报对应产物的 warning，不再同时报超时错误 + done
- **cancelledHandled ref**：并行两路 cancelled 事件去重（此前双产物取消会触发两次取消 Toast）
- **onDone refreshSaved 防竞态**：done 后立刻重新生成 → 旧 getMarkdown 响应不再覆盖新流式内容
- **taskForView（占位按 tab 匹配任务）**：替换 9 处 `activeTasks[0]`——多文档/多产物并行时占位符选错任务（"正在生成笔记"但实际在生成整理版）
- **F4 守卫 / F5 fact_check 清理 / N2 fact_check 生成中守卫 / F6 Excel displayContent**：生成中切文档/切产物竞态全面加固
- **mindmap 有内容直接渲染**：笔记生成完成后思维导图不再显示"正在生成中"
- **版本切换器 isStreaming 禁用**：生成期间禁止切换 v1/v2（防止读到生成中的半成品）

### 为什么

上一条目"串行"决策基于"deepseek 并发排队"的错误归因。用户贴日志（20:01:39 两条 LLM 流式同时开始，5265 + 14776 chars 完成，无排队）证明并发本身没问题；`polished 挂起（第 1 次），重试` 日志实为 120s 内无 chunk 的 thinking 静默。根因链：thinking 默认 high → 复杂任务思考 90s+ 静默 → 被 45s/90s 判定误判挂起重试 → 用户感知"慢到诡异"。解法四件套：思考链展示（等待有反馈）+ 120s 容忍 + effort=low（polished 专用）+ prompt 模糊约束（减少过度思考诱因）+ 并行（总耗时 max）。

产物写反是并行化引入的回归：gather 收拢结果的解包顺序与任务创建顺序不一致，notes/polished 内容互换。用户"笔记预览栏生成的是整理版原文"的报障由此而来。

### 影响范围

| 文件 | 变更 |
|------|------|
| `backend/app/llm/service.py` | on_reasoning 回调 + reasoning_effort extra_body |
| `backend/app/routers/generate.py` | queue 汇合并行 / 120s 超时 / saved_partial / **gather 解包顺序修复** |
| `backend/app/generation/prompt.py` | 三个 polish task 模糊约束 |
| `frontend/src/api/generate.ts` | onReasoning + type=reasoning 分发 |
| `frontend/src/store/types.ts` | TaskInfo.reasoning |
| `frontend/src/components/gen-panel/GenPanel.tsx` | onReasoning / Map 化 / genOutcomeRef / cancelledHandled / refreshSaved |
| `frontend/src/components/preview/PreviewArea.tsx` | taskForView / F4-F6 / N2 / mindmap / 版本切换器禁用 |
| `frontend/src/components/preview/ProcessingPlaceholder.tsx` | 思考链渲染 + mode prop |

---

## 2026-08-01 — 生成链路全面修复（服务端串行 + 前端产物接收 + 切换竞态）

### 做了什么

**服务端（generate.py）**：
- **双产物改串行**（notes → polished）：deepseek 对并发请求排队严重（并行时第二个响应 108s+），串行时每个请求独占连接（~30s/个），前端先看到笔记再看到整理版，渐进反馈符合旧版体验
- **chunk 间隔超时**（90s）：deepseek 长输出流式中途断流时 httpx 静默等 EOF（不抛异常）→ 前端永远"加载中"。`asyncio.wait_for + anext` 判定挂起 → 重试/报错
- **空响应重试**：LLM finish=stop 但 0 chars → 重试一次，仍空 → warning 事件（前端 Toast，另一产物不受影响）
- **未完成 marker**：`.generating` 标记 + `capabilities.interruptedGenerations` 上报，前端启动提示"上次应用中断"
- **双产物取消补 `saved_partial`**：此前 cancelled 事件缺字段 → 前端无"已保留"提示

**前端产物接收（GenPanel/PreviewArea）**：
- **配置变更事件总线**（`lib/configEvents.ts`）：设置页保存后广播 `limo:config-changed`，GenPanel 重新拉取模型/厂商列表、App 刷新 capabilities —— 修复"设置保存后导入面板需手动刷新页面才显示"
- **onDone 以服务器内容刷新产物**：此前只刷 versions 不刷内容 → `streamDisplay=false` 或流式丢失时产物 tab 永远空白"尚未生成"
- **onDone 防竞态**：done 后立即重新生成 → 旧 getMarkdown 响应覆盖新流式内容（`processingTasks[taskKey]` 重新激活检查）
- **取消保留 partial**：双产物取消时 buffer 写入 store（此前 both 模式丢弃）
- **polished-only 视图跳转**：生成后直接跳整理版 tab（此前强制 summary，看不到整理版流式）
- **产物级进度**：ProcessingPlaceholder 显示「正在生成整理版原文...（已生成 N 字符）」（TaskInfo.progress）

**切换链路（生成中切文档/切产物）**：
- **onChunk 顺序重构**（核心）：isSameDoc 检查后置——切走期间流式内容无条件写入 docId 隔离的 buffer，切回显示完整（此前第一行 return 丢弃全部 chunk → 流式断裂）
- **productChars per-taskKey Map**：双文档并发生成（MAX=2）进度不互踩
- **切文档 effect 竞态守卫**：生成快结束时切回 → 旧版本 V0 不再覆盖 onDone 的 V1（notes/polished/fact_check 三处）
- **切文档状态清理**：补 `factCheckProgress`/`factChecking`（A 审核中切到 B 不再残留 spinner）
- **Excel 分支改用 displayContent**：生成中切走切回显示流式而非"尚未生成"
- 生成期间禁用版本切换器

### 为什么

用户实测发现：整理版原文生成慢/挂起（deepseek 流式断流——服务端问题，换 qwen 走同一代码路径 33s 完成证明链路本身正常）、产物 tab"尚未生成"（onDone 不刷新内容）、设置保存后下拉不更新（无事件通知）、生成中切换文档后流式断裂（onChunk 提前 return）。逐条复现后按「服务端事件契约 → 前端接收 → 切换竞态」三层修复。

### 影响范围

| 文件 | 变更 |
|------|------|
| `backend/app/routers/generate.py` | 串行生成 + chunk 超时 + 重试 + marker + saved_partial |
| `backend/app/routers/capabilities.py` | interruptedGenerations 扫描 |
| `frontend/src/lib/configEvents.ts` | 新建：配置变更事件总线 |
| `frontend/src/components/gen-panel/GenPanel.tsx` | onChunk 重构 / Map 化 / onDone 刷新 / 防竞态 / 视图跳转 |
| `frontend/src/components/preview/PreviewArea.tsx` | 守卫 / factCheck 清理 / Excel displayContent / 版本切换器禁用 |
| `frontend/src/components/settings/ProviderSettings.tsx` | 保存后广播配置变更 |
| `frontend/src/App.tsx` | 订阅配置变更刷新 capabilities |

---

## 2026-08-01 — E2E 验证 + 空壳目录 bug 修复

### 做了什么

**端到端验证 14 项清单**（存储重构 + 前端重构后首次全链路实测）：文本/图片导入 → `{标题}-{6位hex}/` 目录 → 双产物生成 → 两版制（notes.md + notes.prev.md）→ v1/v2 版本切换（内容对比不同）→ HTML 导出（自包含零 CDN）→ ZIP 导出（中文文件名数据正确）→ assets 服务端点 → OCR 处理链路（`process/extract.md`）→ coverUrl v3 格式 → 删除 rmtree + index 清理。

**修复：导入重复创建空壳目录** — `_get_or_create_doc_dir` 在 index 无 entry 时创建目录但不记录，`save_transcript` 再次调用时 index miss → 重复创建 + `_1` 序号 → 空壳目录永久残留（实测一次导入产生两个目录）。修复：创建目录后立即占位记录 dir_name 到 index，`save_transcript` 改为 upsert 补全字段。修复后实测只产生一个目录。

**排除 ZIP 中文名乱码假警报** — 验证中发现 ZIP 文件名显示乱码（`ת¼ԭ��`），深度排查到 zipfile 源码层确认是 GBK 终端显示假象：Python 3.12.13 的 zipfile 写文件名时 ASCII 编码失败自动回退 UTF-8 + 设 flag 0x800，布尔比较 `names[0] == '测试文档/测试笔记.md'` 为 True，跨平台解压正常。**export.py 无改动**。

### 为什么

单元测试全绿只证明单层正确，跨层链路（导入→目录→生成→版本→导出→删除）此前从未端到端确认过。验证即发现存储重构的核心路径存在重复创建目录缺陷——这正是清单验证的价值。

### 影响范围

| 文件 | 变更 |
|------|------|
| `backend/app/storage/store.py` | `_get_or_create_doc_dir` 占位 entry + `save_transcript` upsert（16 行） |

---

## 2026-08-01 — 前端工程质量重构（五项，可维护性视角）

### 做了什么

**store 拆 slices** — `store/index.ts`（497 行单文件）拆为 `types.ts` + 7 个 slice（theme/docs/content/tasks/chat/ui/toast），`index.ts` 只剩组合层 + 模块副作用。探索确认 action 层零跨域引用（每个 action 只读自己域字段），84% 字段是单字段单 setter 纯状态，拆 slice 无行为风险。`Store[...]` 自引用类型抽为具名类型（`ActiveView`/`MarkdownMeta` 等），`msgCounter` 归 toastSlice，apply 五主题轴 + matchMedia 监听留在 index.ts（避免运行时循环依赖）。39 字段 53 action API 零变化，22 个消费方零改动。

**client.ts 按域拆分** — 967 行拆为 `core.ts`（共享核心）+ 7 个域文件（capabilities/transcripts/ingest/generate/chat/settings/export），`client.ts` 变纯 barrel（15 个 import 方零改动），`BASE` 保持模块私有不泄漏。顺带合并 `fetchSSE`/`fetchFileSSE`（154 行近逐行重复）为 `ingestStream` + `fallbackJson`，手写 SSE 解析统一走 `sse.ts` 的 `parseSSEStream`（消除第三份重复）。

**contentSource 类型绑定** — `TabDef.contentSource` 的取值逻辑此前在 3 处独立维护（PreviewArea displayContent 硬编码 `activeView === 'polished'` 三元、CopyContentBtn switch、ExportMenu contentKey 中间层），且 `displayContent` 根本没走 contentSource。新建 `lib/contentSources.ts`：`CONTENT_GETTERS: Record<ContentSource, getter>`（新增枚举值编译器强制补实现）+ `getContentForView` 统一入口，ExportMenu 冗余中间层整层删除。

**PreviewArea 展示拆分** — 773 → 646 行。6 个 props 驱动组件（`EmptyState` 替代 8 处重复空态、`ProcessingPlaceholder`/`VersionSwitcher`/`TranscriptQualityNote`/`ImageResultView`/`CopyContentBtn`），全部无 store 依赖。新增**首个组件测试**（`CopyContentBtn.test.tsx` 3 用例，@testing-library/react 此前已装未用）。

**useTaskRunner hook** — `handleRetranscribe`/`handleProcessImage` 逐字重复的 ~20 行样板（capturedId → taskKey → abort → setProcessing → await → stale 检查 → 错误处理 → finally 清理）抽取为 `hooks/useTaskRunner.ts`。AbortError 静默返回，finally 用 `setProcessing(key, null)` 前缀树形删除，onSuccess 内部多段 await 后的 stale 检查逐字保留。`handleGenerate`/`handleImport` 结构不同不强行套用。

### 为什么

用户收到前端工程质量报告（可维护性视角）：God Store 单文件、组件与 store 强耦合、字符串字面量隐式约定（contentSource 映射 3 处独立维护、漏改不报错）、client.ts 长文件、handler 模板重复。决策：P0-P2 全部五项 + store 一次全拆（探索确认 action 零跨域引用，全拆行为风险低）。

### 两处有意的行为变更（重构附带修正）

1. **复制按钮**：extract/table/digitize/understand 四 tab 有处理结果时复制按钮新出现（与 ExportMenu 复制行为对齐）
2. **流式期间非 notes/polished tab**：原逻辑任何非 polished tab 一律读 notes 缓冲（fact_check tab 显示笔记流式内容的 bug 语义）→ 现在读各自内容源

### 影响范围

| 文件 | 变更 |
|------|------|
| `store/index.ts` | 重写为组合层（~60 行） |
| `store/types.ts` + 7 个 slice | **新建** — 39 字段 53 action 按域拆分 |
| `api/client.ts` | 重写为 barrel |
| `api/core.ts` + 7 个域文件 | **新建** — 按域拆分，ingestStream 合并 |
| `lib/contentSources.ts` | **新建** — Record<ContentSource> getter |
| `lib/tabRegistry.ts` | contentSource 抽具名类型 |
| `components/preview/` 6 个组件 | **新建** — 展示拆分 |
| `components/preview/PreviewArea.tsx` | 773 → 646 行，getter 化 + 组件化 |
| `components/ui/ExportMenu.tsx` | 删除 contentKey 中间层 |
| `components/gen-panel/GenPanel.tsx` | 两个 handler 改 useTaskRunner |
| `hooks/useTaskRunner.ts` | **新建** |
| `test/CopyContentBtn.test.tsx` | **新建** — 首个组件测试 |

验证：82 tests 全绿（79 + 3 新组件测试），`npm run build`（tsc + vite）+ `npm run lint` 通过。

---

## 2026-07-31 — 存储重构 v3 + 插件引擎 + 引擎审计（补记）

### 做了什么

**存储重构（v3 按文档聚合）** — 存储从"类型分桶 + 随机 ID"重构为"一文档一目录"：`{sanitized_title}-{6位hex}/`，内含 `transcript.md`（纯文本 rawText）、`{mode}.md` + `{mode}.prev.md`（两版制）、`process/{mode}.md`、`assets/`（图片统一入文档目录）。md 文件用**相对路径** `assets/xxx` 引用图片，Typora/Obsidian 直接打开可渲染；磁盘相对路径 → API 绝对 URL 由 `rewrite_asset_urls(content, tx_id)` 统一改写（所有内容类 API 返回前调用）。删除文档 = `rmtree(doc_dir)`。目录名冲突循环追加序号，标题变更时重命名目录。INDEX_VERSION 3。软件未投产，测试数据直接删，零迁移负担。

**插件引擎（进程外加载）** — AGPL 引擎 pymupdf4llm 以插件形式加载：`~/.limo/plugins/` 下 `EnginePlugin` 声明（`command` 指向子目录 venv 隔离依赖），`plugin_loader.py` 动态注册进 EngineRegistry，capabilities 的 `fileTypeMap` 按插件 `file_types` 自动追加。Windows 修复：command 解析为绝对路径 + `sys.stdout.reconfigure(encoding="utf-8")` 防 GBK 乱码。

**引擎 UI 一致性审计** — 删除 3 个 text_only 变体引擎（docling-text/python-docx-text/python-pptx-text，与完整版重复无意义）；子场景统一为 `doc:pdf`/`doc:word`/`doc:ppt`/`doc:excel`/`doc:epub`（旧 `doc:text+image`/`doc:text-only`/`doc:scan-ocr` 清理）；engineProviders `pymupdf` → `doc-local`；engine_models 残留清理（21 → 9 条）；`_sync_builtin_models` 改为保守方案（只补不删，清理废弃 provider 整组）。

**体积优化** — 移除 opencv-python 死依赖（app 代码零引用，视频抽帧用 ffmpeg，帧质量用 PIL+numpy），节省 ~90MB 打包体积。

### 为什么

存储重构：类型分桶时代一个文档的产物散落在 6 个目录，只有 index.json 知道关系，打开数据目录无法看出哪个文件属于哪个文档。用户强调"尽早做，等以后真的有用户再改就麻了"。

插件引擎：pymupdf4llm 是 AGPL 许可证，内置会污染整体许可证；进程外 subprocess 模式 + 独立 venv 彻底隔离。

引擎审计：用户反馈"导入面板的快捷选择栏与设置页不一致，有些引擎是重复的没有意义"——text 变体引擎与完整版行为重复，UI 出现无意义选项。

### 影响范围

| 文件 | 变更 |
|------|------|
| `backend/app/storage/store.py` | v3 重构（doc_dir/两版制/rewrite_asset_urls/_rebuild_index） |
| `backend/app/routers/ingest.py` | 图片持久化入 doc_dir + 时序修复 + `/api/assets/` 端点 |
| `backend/app/engines/__init__.py` | 删 text 变体注册 + 失效绑定清理 |
| `backend/app/engines/plugin_loader.py` | 进程外插件加载（Windows 路径修复） |
| `backend/app/core/config.py` | `_sync_builtin_models` 保守化 + doc-local |
| `backend/app/routers/capabilities.py` | fileTypeMap 动态追加插件引擎 + scenarioLabels 统一 |
| `backend/app/adapters/document.py` | `_ext_to_sub_scenario` → doc:pdf |
| `backend/pyproject.toml` + `LiMoBackend.spec` | 移除 opencv-python |
| `frontend/` 多处 | 引擎场景 key、assets URL、文案同步 |
| `~/.limo/plugins/` | **新增** pymupdf4llm 插件（AGPL 隔离） |

---

## 2026-07-31 — Bug 修复 + SSE 重构

### 做了什么

**Cancel 按钮无效** — 多任务重构将 `createAbort` 改为 per-task key（如 `doc123__generate`），但 `cancelTask` / `cancelOp` 仍用 base docId（`doc123`）查找 → 永不命中。用户每次取消都浪费了 LLM token（HTTP 连接未断开）。修复：`registerAbort` 内部归一化 key，doc 任务按 base docId 存储，导入任务独立 Map 支持多导入并发。

**导入取消后仍继续运行** — `fetchSSE` / `fetchFileSSE` 的 catch 块未检查 `AbortError`，取消后回退到非流式端点继续请求。用户以为取消了，实际后端还在处理。修复：catch 开头加 `if (err.name === 'AbortError') return`。

**SSE 解析提取公共函数** — 新建 `api/sse.ts`，提取 `parseSSEStream()`。`streamGenerate` / `streamFactCheck` / `streamExportProgress` / `streamChat` 4 个函数的 SSE 解析循环统一为一个调用。消除 130+ 行重复代码。

**废弃 types 替换** — 移除 `@types/react-syntax-highlighter`（v15 类型与 v16 不匹配），新建最小类型声明文件 `src/types/react-syntax-highlighter.d.ts`。

**VideoBanner 阅读宽度适配** — 将 VideoBanner 从 `preview-content` 的 `maxWidth` 约束容器内移到外面，不受阅读宽度（narrow/normal/wide）切换影响。

**console.log 清理** — App.tsx 两处残留移除。

### 为什么

Cancel bug 是真实生效的——Map key 存 `doc123__generate`，查 `doc123`，永远匹配不上。多任务重构带来的 regression。

导入 AbortError 漏查和 SSE 重复解析是代码质量债——之前 6 个函数各自 copy 一份几乎一样的 SSE 解析逻辑。

### 影响范围

| 文件 | 变更 |
|------|------|
| `api/taskManager.ts` | registerAbort 归一化 key，导入独立 Map |
| `api/client.ts` | fetchSSE/fetchFileSSE +AbortError 检查，4 函数改用 parseSSEStream |
| `api/sse.ts` | **新建** — 公共 SSE 流解析器 |
| `types/react-syntax-highlighter.d.ts` | **新建** — 替代废弃 @types 包 |
| `PreviewArea.tsx` | VideoBanner 移出约束容器 |
| `App.tsx` | console.log 清理 |
| `generate.py` | 导入 disconnect_watcher 标注 |

---

## 2026-07-31 — 多任务并发重构

### 做了什么

将生成/转录/图片处理/导入 4 种异步操作从全局单槽改为按文档+任务粒度隔离：

**统一状态来源** — 所有操作状态从 `processingTasks`（per-doc per-kind）推导，移除 7 个全局单槽：`generating`、`isStreaming`、`streamingContent`、`retranscribing`（useState）、`imageProcessing`（useState）、`importing`（useState）+ 3 个 GenPanel ref 缓冲区。

**双层流式缓冲** — 新建 `store/streamBuffers.ts`：模块级 Map（高速写入，无 React re-render）+ Store `streamTick`（120ms 节流驱动 PreviewArea 重读）。替代之前 ref 缓冲区的方案。

**操作互斥 per-doc** — `lockOtherOps` 从全局改为只看当前文档。Doc A 转录中不再阻止 Doc B 生成。

**并发上限** — 生成最多 2 个同时、导入最多 3 个同时，超限 Toast 提示。确保 LLM API 限流 + 文件 I/O 可控。

**逐任务取消** — 新增 `cancelOneTask` store action。TaskPanel 取消 Doc A 的生成不影响 Doc B。

**移除续写功能** — LLM 续写没有上文上下文，容易重复/风格断裂。模型输出长度已足够（4K-128K）。保留取消时 partial 保存，PartialBanner 简化为纯信息展示。

**切换文档不丢流式内容** — 切回正在生成的文档时显示已累积 buffer + 继续实时追加，而非空白加载。

**后端 `has_active_task`** — `GET /api/markdown/{id}` 在文档有活跃生成任务时返回 `has_active_task: true`，前端据此 3s 轮询直到完成（刷新恢复）。

**移除 PreviewArea 欢迎页导入栏** — 拖放区 + URL 输入框与 GenPanel ImportSection 功能重复，违反"唯一导入入口"原则。空状态回归简洁引导。

### 为什么

之前每个异步操作各有一套管状态的方法（store 字段、组件 useState、闭包 ref），代码里散布着 `setGenerating(true/false)`、`setRetranscribing(true/false)` 等调用。切换文档时状态管理完全靠 `isSameDoc(capturedId)` 检查，稍有不慎就串数据。扩展新产物类型需要加新变量。

现在所有操作状态统一从 `processingTasks` 推导——它是一个 key 为 `${docId}__${kind}` 的字典，天然支持多文档多任务。新增操作类型只需 `setProcessing(key, {kind})` 一行。

### 影响范围

| 文件 | 变更 |
|------|------|
| `store/index.ts` | +`streamTick`/`bumpStreamTick`/`cancelOneTask`，-`streamingContent`/`isStreaming`/setters |
| `store/streamBuffers.ts` | **新建** — 模块级 Map + 节流 flush |
| `GenPanel.tsx` | -4 个 useState，-3 个 ref，+processingTasks 推导，+并发限制 |
| `PreviewArea.tsx` | displayContent 改用 streamBuffer + tick，移除欢迎页拖放区 |
| `PartialBanner.tsx` | 移除续写按钮 |
| `App.tsx` | `generating` → `processingTasks` |
| `generate.py` | +`_active_generations` 追踪，`get_markdown` 返回 `has_active_task` |
| `store.test.ts` | +多任务并发/取消/tick 测试 |
| `streamBuffers.test.ts` | **新建** — 21 个缓冲区单元测试 |

---

## 2026-07-31 — 6 项体验优化

### 做了什么

基于 [03-质感主题系统设计方案](../pi-docs/03-质感主题系统设计方案.md)，实施 6 项 UX 改进：

**P0 空状态拖放区** — PreviewArea 欢迎页从静态文字改为交互式拖放区 + 链接输入框。新用户第一眼就能"丢东西进来"，而非阅读说明书。复用 GenPanel ImportSection 的拖放逻辑，直接调用 ingest API。

**P2 默认 Tab 按文档类型** — PDF/文档切换后默认打开「整理版原文」，视频默认「转录」，图片默认「原图」，Excel 默认「数据预览」，网页默认「笔记」。包含「已有生成内容」和「尚无内容」两种场景。

**P3 暗色模式极淡纹理** — 之前暗色模式完全清除纹理（`background-image: none !important`），现改用 `::after` 遮罩方案，让纹理以 2-3% 透明度透过，恢复暗色下的纸张质感。

**首次启动向导（三屏）** — 新建 `OnboardingWizard`，替代旧 `SetupWizard`：
- 屏 1：阅读氛围选择 — 展示经典系列 4 张预设卡片（书香/简净/复古/青竹），点击即实时预览背景
- 屏 2：系统组件检测 — 复用旧 SetupWizard 的 ffmpeg/SearXNG 检测逻辑

**转录完成提示** — 非图片导入完成后 Toast 明确提示"点击生成按钮生成笔记"，解决用户不知道下一步做什么的问题。

### 为什么

旧 SetupWizard 只检测系统组件，缺乏情感化的首次体验。空状态是用户对产品的第一印象，拖放区比文字说明更有引导力。Tab 默认值和转录提示消除用户的操作困惑。

### 影响范围

- `frontend/src/components/ui/OnboardingWizard.tsx` — 新建（225 行）
- `frontend/src/components/preview/PreviewArea.tsx` — 拖放区 + 默认 Tab
- `frontend/src/lib/texture.css` — 暗色纹理 ::after 方案
- `frontend/src/components/gen-panel/GenPanel.tsx` — Toast 提示优化
- `frontend/src/App.tsx` — SetupWizard → OnboardingWizard

---

## 2026-07-31 — 公式渲染 + 导出管线全面修复

### 做了什么

**A. 公式 `\[...\]`/`\(...\)` 分隔符支持**

**问题**：导入学术论文（arXiv 等）后，公式在预览中不渲染，显示为原始 LaTeX 代码。

**根因**：`remark-math` 只识别 `$`/`$$` 分隔符，不识别 LaTeX 标准的 `\(...\)`（行内）和 `\[...\]`（块级）分隔符。学术论文普遍使用后者。

**修复 — 双管齐下**：

1. **前端预处理兜底**（治标）— `markdown-plugins.ts` 新增 `normalizeLatexDelimiters()` 函数，在 Markdown 解析前将 `\[...\]` → `$$...$$`、`\(...\)` → `$...$`。保护代码块内的内容不被修改。6 个 ReactMarkdown 渲染入口全部接入。新增 11 个测试用例。

2. **Prompt 约束**（治本）— Notes 的 `_NOTES_TASK` 之前只说"LaTeX 语法形式"，未指定分隔符格式。现明确要求使用 `$...$`/`$$...$$`，禁止 `\(...\)`/`\[...\]`。其他 prompt（Polished、文档、Excel 等）早有此约束，仅这一处遗漏。

3. **后端导出管线补充**（遗漏修复）— `render_math.js`、`_protect_latex`、`_render_formulas_katex`、`_render_formulas_to_images`、`_build_mindmap_html` 共 5 处原先也只识别 `$$`/`$`，现已全部接入归一化预处理。

**B. 导出 HTML 渲染对齐前端预览**

排查前端预览与后端导出 HTML 的渲染差异，修复 3 项不一致：

1. **代码高亮配色** — Pygments style: `default` → `one-dark`（暗色程序员风格）。代码块背景 `#f6f8fa`（冷灰）→ `#282C34`（one-dark 暗色）。4 套 HTML 主题 `pre`/`code` 背景同步更新。

2. **GFM 任务列表** — `- [ ]` → `<input type="checkbox" disabled>`（替代 Unicode ☐），`- [x]` → `<input type="checkbox" checked disabled>`（替代 Unicode ☑）。视觉对齐前端 remark-gfm 的真实 checkbox。

3. **代码块统一暗色** — 无论导出主题明暗，代码块始终保持暗色底色，匹配程序员阅读习惯。

### 为什么

前端预处理解决非 LLM 内容的渲染（粘贴文本、网页原始内容），Prompt 约束确保 LLM 产出天生正确，后端管线补漏覆盖导出场景。三者互补，形成完整闭环。

导出对齐让用户"看到什么导出什么"，消除信任摩擦。

### 影响范围

- `frontend/src/lib/markdown-plugins.ts` — 新增 `normalizeLatexDelimiters`（~40行）
- `frontend/src/components/preview/` — 5 文件，各 1 行调用
- `frontend/src/components/ai-chat/ChatCore.tsx` — 1 处调用
- `frontend/src/test/normalize-latex.test.ts` — 新增 11 个测试用例
- `backend/app/generation/prompt.py` — `_NOTES_TASK` 1 行
- `backend/app/routers/export.py` — `_normalize_latex_delimiters` + 4 处接入 + Pygments 配色 + GFM 任务列表 + 4 主题代码背景
- `backend/scripts/render_math.js` — 新增 `normalizeLatexDelimiters` + 调用

---

## 2026-07-30 — 深度评估后续改进（四合一）

### 做了什么

基于 [04-深度评估报告](../pi-docs/04-深度评估报告.md) 的 4 个剩余改进项，一次性完成：

**1. doc.py 拆分为独立文件** — `backend/app/engines/doc.py`（756 行）拆为 5 个文件：`doc_types.py`（共享 DocResult）+ `doc_markitdown.py` + `doc_docx.py` + `doc_pptx.py` + `doc_excel.py`。3 个未注册死代码引擎（PyMuPDFEngine/PyMuPDF4LLMEngine/PDFOCREngine）随文件删除。`docling.py` 和 `__init__.py` 的 import 路径同步更新。

**2. Zustand Store 领域分节** — `frontend/src/store/index.ts` 用分节注释（`════ 域名称 ════`）清晰划分 11 个逻辑域，保持单一 `create()` 调用的零类型错误优势。接口完全不变，所有 `useStore(s => s.field)` 选择器兼容。

**3. 前端测试基础设施** — 从零搭建：vitest + @testing-library/react + jsdom。3 个测试文件，32 个测试用例（store 19 + theme 9 + api 4）。`npm test` / `npm run test:watch` 脚本。

**4. 引擎配置 UX 优化** — EngineSelect 加"当前引擎"标签，ProviderSettings 加"恢复默认"按钮。附带修复 `EngineBindData` Pydantic 类型 bug（`int|str|None` → `int|str|list|None`，后台代码已有 `isinstance(val, list)` 处理但类型拒绝）。

### 为什么

报告指出这 4 项是不影响主线使用的结构性优化，但修完让项目更完整。实际耗时约 1.2 小时。

### 影响范围

- `backend/app/engines/` — 5 新建 + 2 修改 + 1 删除
- `frontend/src/store/index.ts` — 重构为 11 域分节
- `frontend/src/test/` — 4 新建（vitest.config + setup + 3 tests）
- `frontend/package.json` — test scripts
- `frontend/src/components/gen-panel/EngineSelect.tsx` — "当前引擎"标签
- `frontend/src/components/settings/ProviderSettings.tsx` — "恢复默认"按钮
- `backend/app/routers/model.py` — Pydantic 类型修复
- `frontend/src/api/client.ts` — saveEngineBindings 类型同步

### 验证

- 后端: `from app.engines import init_engines` + `test_engines.py` 全部通过
- 前端: `tsc -b --noEmit` 零错误 + `vitest run` 32/32 通过

---

## 2026-07-29 — 工程质量系统性提升（disconnect_watcher + Depends + 审核分片 + 导出进度）

### 做了什么

**Part 1: disconnect_watcher 公共工具** — 将 5 个路由中 ~45 行重复的 `_watch_disconnect` 实现提取为 `@asynccontextmanager`。同时修复 2 个 bug：`process.py` 资源泄漏（disconnect_task 从不 cancel）和 `chat.py` 缺 await（Task destroyed but pending）。

**Part 2: FastAPI Depends 全量迁移** — 8 个路由模块全部从 `request.app.state.xxx` 迁移到 `Depends(get_store/config/guard/config_lock)`。共修复 5 个文件缺失 `Depends` 导入的错误。

**Part 3: AI 审核长文档分片** — `split_into_chunks()` 按句子边界分片 + `asyncio.gather` 并行 LLM 提取 + `SequenceMatcher` 去重（>0.85 相似度），最多32块。

**Part 4: 导出进度反馈** — `POST /export/custom/stream` SSE 流式进度 + `GET /export/download/{temp_id}` 临时下载，复用 ingest.py 已验证的 queue + background task 模式。

### 为什么

对话中明确：工程质量全部做完，审核分片+导出进度作为功能增强。前两项消除重复代码并统一路由风格，后两项填补功能空白。

### 影响范围

- `backend/app/core/disconnect.py` — 新建（公共工具）
- `backend/app/routers/generate.py, ingest.py, chat.py, process.py, fact_check.py` — disconnect_watcher 替换
- `backend/app/routers/export.py, chat.py, process.py, fact_check.py, config.py, ingest.py, generate.py, model.py` — Depends 迁移
- `backend/app/core/text_utils.py` — `split_into_chunks()`
- `backend/app/generation/review.py` — `static_review()` 分片化
- `backend/app/routers/export.py` — 2 新端点
- `frontend/src/api/client.ts` — `streamExportProgress()`
- `frontend/src/components/ui/ExportPanel.tsx` — 进度 UI

### 验证

- `grep -r "request\.app\.state" backend/app/routers/` → generate.py 仅 7 处无害闭包残留
- `grep -r "return {\"error\"" backend/app/routers/` → 0 处旧格式
- 6 个后端测试全部通过
- Tauri + PyInstaller 打包成功（MSI 569MB / NSIS 404MB）

---

## 2026-07-26 — 多任务状态泄漏修复 + 刷新内容恢复

### 问题

两个打包端 bug，根因相同——SSE 回调中的 `isSameDoc()` 守卫拦住了任务清理逻辑。

**Bug 1 — 切换文档后产物预览永远加载中**：用户在文档 A 点生成 → 切换到文档 B → 文档 A 的 `onDone` 被 `isSameDoc` 拦截 → `setProcessing(key, null)` 永不调用 → 任务条目泄漏 → 切回 A 时 `isViewProcessing` 返回 true → 永远显示加载中。

**Bug 2 — 刷新后内容不同步**：`processingTasks` 纯内存，刷新后 = `{}`。App.tsx `init()` 只恢复文档列表，不预加载已有内容 → 用户看到空预览区。

### 修复

**任务清理脱离 isSameDoc 守卫**（`GenPanel.tsx` + `PreviewArea.tsx`）：
- 原则：`isSameDoc` 仅保护内容更新（setContent），不保护生命周期清理（setProcessing null）
- `handleGenerate` onDone/onError/onCancelled、`handleRetranscribe` finally、`handleProcessImage` finally、`handleFactCheck` onDone/onError — 7 处 `setProcessing(key, null)` 移到守卫前
- 全局 flag（`generating`/`isStreaming`）仅在 `processingTasks` 中无其他文档生成任务时才复位，防并发场景误关

**刷新后预加载内容**（`App.tsx`）：
- `init()` 恢复文档后，主动调 `getTranscript()` + `getMarkdown()` 预加载 rawText/notes/polished → 已有的生成结果立即可见

**向前兼容加固**（`PreviewArea.tsx`）：
- `activeTask`（单值 `find()`）→ `activeTasks`（数组 `filter()`）
- `isViewProcessing` 改用 `some()` — 未来同文档多任务并发时不漏检

### 影响范围
- `frontend/src/components/gen-panel/GenPanel.tsx`（+27/-7 行，7 处守卫调整）
- `frontend/src/components/preview/PreviewArea.tsx`（+47/-16 行，数组重构 + 守卫调整）
- `frontend/src/App.tsx`（+26/-1 行，预加载逻辑）

### 设计决策

- **清理与内容更新解耦** — 清理是文档级操作（key 含 docId），内容更新是视图级操作（需 isSameDoc）。两者不应共用同一个守卫。
- **全局 flag 防御式复位** — 检查 `processingTasks` 中所有文档而非仅当前文档，确保未来并发场景不会出现"一个完成→全部复位"的 bug。
- **不持久化 processingTasks** — 任务状态是 SSE 连接绑定的临时数据，持久化后会引入僵尸条目恢复问题。替代方案是主动从服务端加载已保存的内容。

---

## 2026-07-26 — DashScope 模型体系加固

### 问题

1. **第三方模型混入**：DashScope API `/v1/models` 返回阿里云平台上托管的所有模型（含 DeepSeek/Kimi/GLM 等第三方），刷新模型列表时混在千问模型中间，用户困惑。
2. **能力检测依赖命名规则**：`_PROVIDER_IMAGE_RULES` 用字符串前缀猜测模型能力（如 `qwen3.6` → image），但同系列下有纯文本也有多模态（qwen3.6-flash 实为纯文本），规则不可靠。
3. **低成本模型缺失**：`qwen-flash`（¥0.15/M 最便宜文本）、`qwen3.5-flash`（¥0.2/M 视觉 Flash）、`qwen3.5-ocr`（卡证票据 OCR）未在预设中。
4. **模型描述冗长**：含价格、上下文等信息，用户真正需要的是快速判断"能不能处理图片"。

### 解决方案

**能力标注策略**：`预设声明（确定可靠） > 用户手动标记 > 自动检测规则（仅明确视觉前缀）`

### 改动

**模型列表净化**（`routers/model.py`）：
- `refresh_models`：DashScope API 结果按 `owned_by` + ID 前缀双重过滤，排除 `deepseek-`/`kimi-`/`glm-`/`minimax-` 等第三方模型
- 新增 `_lookup_preset_capabilities()`：`add_model` 时优先从 `llm_models` 预设查能力标签，预设无此模型才回退到自动检测
- `_PROVIDER_IMAGE_RULES` dashscope 收紧：去掉模糊的 `qwen3.7-plus`/`qwen3.6`/`qwen3.5`，仅保留 `qwen-vl`/`qwen-omni`/`qvq`/`qwen3-vl` 四种明确视觉前缀

**模型预设加固**（`core/config.py`）：
- `llm_models.dashscope` 全部 13 个模型内置 `capabilities` 字段 — 5 个 `["text"]`，8 个 `["text", "image"]`
- 新增 3 个低成本模型：`qwen-flash`、`qwen3.5-flash`、`qwen3.5-ocr`
- 描述统一为 `能力类型 · 一句话定位`（去掉价格/上下文等冗余信息）
- `engine_providers.dashscope.builtin_models` 新增 3 个 OCR 引擎模型（`qwen-vl-ocr-latest`/`qwen3-vl-flash`/`qwen3.5-ocr`）

### 影响范围
- `backend/app/core/config.py`（DashScope 预设段重写，+3 模型，+13 capabilities）
- `backend/app/routers/model.py`（+50 行：过滤逻辑 + 预设查表 + 规则收紧）

### 设计决策

- **预设覆盖已知，规则兜底未知**：不会出现"预设遗漏 → 自动检测误判"的夹缝。已知模型的能力是人工声明的事实，不依赖命名规则猜测。
- **新模型交给用户判断**：API 拉取到不在预设中的模型时，仅名称含 `qwen-vl`/`qwen-omni`/`qvq`/`qwen3-vl` 的自动标视觉，其他默认纯文本。用户可在设置页手动调整。

---

## 2026-07-26 — OCR 预处理管线 + 千问模型集成

### OCR 预处理管线

适用于 PDF/Word/PPT/EPUB 等所有文档类型。在生成笔记前，先用廉价 OCR 模型（如 qwen-vl-ocr-latest ¥0.3/M）提取图片中的文字，塞回原文，再交给文本 LLM 生成。无需多模态模型也能"看到"图片内容，成本降低 5-10 倍。

**后端**：
- `ocr_preprocessor.py`（新建）：正则匹配 `/api/files/{tx_id}/img_NNNN.png` 图片引用 → 读文件 base64 → `LLMService.call(prompt, image_urls=[...])` → OCR 文字插入原文
- `Generator.generate/generate_both`：在 `ContentPreparer.prepare()` 之前执行 OCR（需要原始图片路径）
- `generate.py`：接收 `ocr_enabled` / `ocr_model_id` 参数，流式和非流式端点均已接入
- `config.py`：DEFAULT_CONFIG 加 qwen-vl-ocr-latest、qwen3-vl-flash、qwen3.7-flash、qwen-plus、qwen-turbo 等模型

**前端**：
- OCR 开关 + OCR 模型下拉（仅 Document 类型显示，从 enabledModels 中筛选 vision 能力模型）
- OCR 与 imageAnalyze 互斥：开启 OCR 时隐藏 imageAnalyze
- genPrefs 持久化 ocrEnabled / ocrModelId

### 千问平台模型文档

新增 `docs/qianwen-models.md`，涵盖文本生成、OCR/视觉、ASR 三类 LiMo 适用模型，含价格、上下文、调用方式。

### 影响范围
- `backend/app/generation/ocr_preprocessor.py`（新建，111 行）
- `backend/app/generation/generator.py`（+22 行）
- `backend/app/routers/generate.py`（+27 行）
- `backend/app/core/config.py`（+15 行）
- `frontend/src/components/gen-panel/`（+41 行）
- `frontend/src/api/client.ts`（+4 行）

---

## 2026-07-25 — AI 审核前端显示简化 + 参考来源后处理注入

### 问题

AI 审核（fact_check）前端用复杂状态机（4 种状态 A/B/C/D）+ 阶段指示器 + 仪表盘 + AI 评分，和其他产物（notes/polished）的流式 Markdown 体验完全不同。

更关键的是：AI 评分（"可信度 7.5/10"）让用户误以为这是客观裁定——实际上是用 AI 来审核 AI 生成的内容，评分本身可能不准确。我们只应该做能做到的（静态分析 + 联网验证），不做能力边界之外的事（给内容打分）。

### 改动

**后端** — 参考来源后处理注入（`review.py`）：
- 新增 `_build_references_section()`，从 `review_results[].evidence_items` 中提取所有证据 URL
- `generate_report()` 和 `generate_report_streaming()` 在 LLM 报告后程序化追加「参考来源」section
- 不依赖 LLM 是否按格式输出 URL，由系统保证

**前端** — fact_check tab 简化为纯 Markdown 流式展示（`PreviewArea.tsx`）：
- 从 ~280 行状态机（阶段指示器 + 仪表盘 + 4 种状态）→ ~35 行三态（Markdown / 加载 / 引导）
- `onProgress` 阶段结果以 Markdown blockquote 拼接到内容区，和生成笔记体验一致
- `onChunk` 处理所有 phase 的 text（修复 verify 阶段 text 被吞的 bug）
- 去掉 AI 评分/仪表盘/阶段指示器，能力边界在引导页用纯文本说明
- 删除未使用图标导入 `ClipboardList`、`Search`

**Store** — `factCheckMeta` 移除 `overall_score`、`trustworthiness`、`trustworthiness_label`、`deductions` 字段

### 影响范围
- `backend/app/generation/review.py`（+39 行）
- `frontend/src/components/preview/PreviewArea.tsx`（-161/+122 行）
- `frontend/src/store/index.ts`（类型精简）

---

## 2026-07-16 — AI 对话架构重构（参照 Pi）+ 提示词分类型重写 + 公式修复

### AI 对话架构重构

参照 Pi 的分层架构重构 `chat.py`（286 行全耦合 → 5 个独立模块）：

| 模块 | 职责 |
|------|------|
| `agent/prompts/` | 分层 System Prompt：`base.md`（角色）+ `rules.md`（硬约束）+ 动态文档注入 |
| `agent/tool_registry.py` | 工具注册中心：`ToolDef` 标准化，`ToolRegistry.apply_to()` 替代硬编码注册 |
| `agent/session.py` | 对话会话管理：`SessionManager` 封装 CRUD + 历史提取 |
| `routers/chat.py` | HTTP 调度（~220 行），只做组装 |

AgentRunner 核心循环不动，前端 API 零改动。加新工具只需创建 ToolDef 并 register。

### 前端格式指令跨类型泄露修复

`GenPanel.formatStr` 未按 sourceType 过滤 → 用户在 PDF 勾选"公式集"后切到视频，`formatFormulas=true` 仍发送 → 后端给视频 prompt 加入 `get_formulas_format()` 指令。修复：`TypeMeta` 新增 `availableFormats`（来自 API 的 `genOptions`），`formatStr` 按当前类型可用格式过滤。

同时修正视频产物公式策略：`formulas` 格式从 video AVAILABLE_OPTIONS 移除（不再强制"公式汇总"章节），改为在 `_POLISH_TASK` 中加轻量提示（"如有公式用 LaTeX，没有则忽略"）。网页 Polish task 同步补上。

### Polish 提示词按文件类型独立化

`build_polish_prompt` 原先只区分「文档 / 非文档」，webpage/text/image 被错误分到视频转录 prompt。新增 3 个独立 Polish prompt：

| 类型 | 新 prompt | 核心指令 |
|------|-----------|---------|
| webpage | `_WEB_POLISH` | 清导航/侧边栏残留、规范化标题层级、保留图片/表格/引用 |
| text | `_TEXT_POLISH` | 保留原文风格、适度格式化、修正错别字 |
| image | `_IMAGE_POLISH` | 修正 OCR 同音字/形近字、重组段落、重建表格、公式转 LaTeX |

video/audio 保持原有转录整理 prompt，doc 保持文档整理 prompt。

### Notes 提示词同步独立化

`build_notes_prompt` 原先也是 binary dispatch，webpage/text/image 共用视频 NOTES prompt（含"填充词"等视频专属指令）。新增 3 个独立 NOTES prompt，分发逻辑对齐 Polish。

### 版本显示修复

两种竞态导致版本列表错乱：
- 流式 onDone / 取消续写：notes 版本用 `setMarkdownVersions` 直接替换，若返回晚于 polished 则抹掉后者
- 重新转录：只取 notes.versions，polished 版本完全丢弃

修复：按 mode 过滤合并而非替换。同时版本切换器按当前 tab 过滤——polished tab 只显示 polished 版本，notes tab 只显示 notes 版本，互不污染。

---

## 2026-07-25 — 文档更新 + 打包

- 更新 CLAUDE.md：待办事项标记"报告可交互"和"旧格式错误迁移"为已完成，汇总 62/68→63/68（91%→93%）
- 更新 STATUS.md：事实验证 4/5→5/5，合计 59/63→60/63（94%→95%）
- 同步更新 pi-docs/01-C端项目分析.md 进度
- PyInstaller + Tauri 打包

---

## 2026-07-25 — 路由错误体系统一迁移

将 config/model/files 路由中 15 处旧格式 `return {"error":"..."}` 全部迁移为 `raise AppError("CODE", "...")`，完成错误体系的统一。

| 路由 | 旧格式数 | 新旧 |
|------|---------|------|
| config.py | 7 | `DETECT_NO_API_KEY` / `DETECT_NO_BASE_URL` / `DETECT_BAD_SCHEME` / `DETECT_BAD_URL` / `DETECT_REQUEST_FAILED` / `DETECT_FAILED` |
| files.py | 3 | `COVER_BAD_ID` / `COVER_NOT_FOUND` |
| model.py | 5 | `PROVIDER_NOT_FOUND` / `PROVIDER_BUILTIN` / `MODEL_NOT_FOUND` |

影响：前端 `apiFetch` 的统一错误拦截现在可以正确捕获这些端点的错误。

---

## 2026-07-25 — AI 审核报告来源链接后处理注入

### 问题

LLM 在生成审核报告时经常不写出可点击的 `[标题](URL)` 格式链接——prompt 已明确要求，但 LLM 执行不稳定。用户在 fact_check tab 看到"来源：相关网页"却无法点击查看原文。

### 修复

在 `review.py` 中新增 `_build_references_section()` 方法，**程序化地**从 `review_results[].evidence_items` 中提取所有证据 URL，按域名去重后在报告末尾追加「📎 参考来源」section。

- `generate_report()`：LLM 生成完成后拼接参考来源 section
- `generate_report_streaming()`：LLM 流结束后续传参考来源 section（一个 chunk）

这样 URL 的呈现不依赖 LLM 是否听话，由系统保证。

修改文件：[`backend/app/generation/review.py`](../backend/app/generation/review.py)（+35 行）

---

## 2026-07-16（早前）— 配置防丢失 + 截图修复 + 内容宽度自适应 + UI 优化

从 MyBigWork 移植。

### 配置防丢失（防御性加固）

- `save_config` 拒绝写入 `llm=None` 的损坏配置
- `save_config` 自动修复 None 列表字段为 `[]`
- `load_config` 启动时创建 `config.yaml.bak` 备份

### 原片截图修复

- `_POLISH_FORMAT_FNS` 缺 `screenshot`/`link` → polished 产物不生成截图
- `_post_process_screenshots` 不传 `store_root` → 截图写入错误路径
- 非流式端点缺截图视频下载 + `want_polished` 漏传 `formats`

### 内容宽度自适应

`ResizeObserver` + 百分比三档（80%/90%/97%），上限 1100px。`Ctrl+W` 循环切换，字体联动缩放。

### UI 优化

- 前端 emoji → lucide 图标全量替换
- 预览栏响应式间距 + 固定滚动条

---

## 2026-07-16 — 配置防丢失 + 截图修复 + 版本显示修复 + 内容宽度自适应 + UI 优化

### 配置防丢失（防御性加固）

排查 API Key / 配置偶发丢失问题。未发现确定性 bug，但识别出架构脆弱性：`save_config` 无条件写盘，若内存中 config 因任何原因缺失关键字段则会被固化。三道防御：

- `save_config` 拒绝写入 `llm=None` 的损坏配置
- `save_config` 自动修复 `llm_presets`/`engine_providers` 等 None 列表字段为 `[]`
- `load_config` 启动时创建 `config.yaml.bak` 备份

### 原片截图修复（4 处）

视频笔记「原片截图」开启后标记不替换为图片的根因分析 & 修复：

| 问题 | 修复 |
|------|------|
| `_POLISH_FORMAT_FNS` 缺 `screenshot`/`link` → polished 产物不生成截图 | 加入格式白名单 |
| `generator.py` `_post_process_screenshots` 不传 `store_root` → 截图写入 CWD 而非 data_dir | 传入 `self._prep.store_root` |
| 非流式端点缺截图视频下载 → `video_path=None` 导致后处理跳过 | 补下载逻辑 + 后处理 |
| 非流式端点 `want_polished` 漏传 `formats=req.formats` | 补传 |

### 版本显示修复

`loadVersion` 对 notes 模式传空字符串 `''`，后端不做 mode 过滤，版本列表混入 fact_check/polished 条目。改为传 `'notes'`。

### 内容宽度自适应切换

预览栏工具栏新增宽度切换按钮（`Ctrl+W`），三档基于面板实际宽度的百分比（ResizeObserver 实时追踪）：紧凑 80% / 标准 90% / 宽屏 97%，上限 1100px。切换时宽度+字体+padding 平滑过渡 350ms。

### 预览栏 UI 优化

- 外层 wrapper `overflow-y-scroll` 固定滚动条位置
- 响应式水平间距：`px-4 md:px-12 lg:px-20 xl:px-32`（紧凑/标准），宽屏特小间距

---

## 2026-07-15（深夜·二）— 前端 emoji → lucide 图标全量替换

### 改动

UI 中所有 emoji 替换为 lucide-react 开源图标，保持语义一致、风格统一。

| 位置 | 原 emoji | 替换图标 | 语义 |
|------|---------|---------|------|
| PreviewArea 错误/警告 | ⚠️ | `AlertTriangle` | 警告/注意 |
| PreviewArea modeMeta fallback | 📄 | `FileText` | 文档 |
| PreviewArea 静态审核标题 | 📋 | `ClipboardList` | 审核清单 |
| PreviewArea 验证中标题 | 🔍 | `Search` | 搜索/查找 |
| ExportPanel 类型分组标题 | 📄(fallback) | `TYPE_ICON` 映射 | 各类型对应图标 |
| ExportPanel 预览行前缀检测 | 📹🌐📄📝🖼📊📚 | `TYPE_LABELS` 中文检测 | 死代码修复 |
| GenPanel Toast 前缀 | ⚠️ | 移除 | Toast 组件已有图标 |

### 影响范围

- `PreviewArea.tsx` — 新增 FileText/ClipboardList/Search 导入
- `ExportPanel.tsx` — 新增 TYPE_ICON 映射 + Film/PenLine/Image/BarChart3/BookOpen 导入
- `GenPanel.tsx` — 移除 Toast ⚠️ 前缀
- 纯 UI 层改动，不影响 API/后端/打包

---

## 2026-07-15（深夜）— 安全漏洞修复 + 视频阻塞修复 + verify_mode

### 安全漏洞扫描与修复（6 项）

- **HIGH — `get_url_metadata` SSRF**：新增工具函数未调用 `validate_url()`，攻击者可探测内网。已补校验
- **MEDIUM — `search_official` 域名注入**：LLM 生成的域名参数未清理，可注入搜索操作符。正则清理 `[^a-zA-Z0-9.\-]`
- **MEDIUM — `run_streaming` 任务泄漏**：取消时后台 `asyncio.create_task` 未 cancel，持续消耗资源。`finally` 块 cancel+gather
- **MEDIUM — evidence.py URL 去重竞态**：并发 check-then-add 无锁保护。`threading.Lock` 保护 + 定期 GC 过期缓存
- **MEDIUM — `CancelledError` 未被捕获**：`_verify_item` 只 catch `Exception`，`CancelledError` 导致 result_queue 永久阻塞
- **LOW — SearXNG Popen 缺 CREATE_NO_WINDOW**：打包后启动 SearXNG 弹 CMD 黑框

### 视频导入阻塞修复

视频 `_download`（yt-dlp）/ `_extract_audio`（ffmpeg）/ 抖音 `dy_download` 四个同步操作未包装 `asyncio.to_thread`，下载期间阻塞整个事件循环。已全部包装。

### verify_mode 参数

AI Review 路由新增 `verify_mode` 参数（`llm` 默认 / `web` 联网），后端透传到 ReviewEngine。

---

## 2026-07-15（晚间）— P0-1~P0-3 Prompt 体系重构 + 语言策略统一 + Agent 体验

### P0-1: Polish/Notes 风格边界重设计 + Prompt XML 模块化

**问题**：`STYLE_MAP` 被 Polish 和 Notes 共用，Polish + business → "保持原文" vs "结论先行" 矛盾。

**修复**：
- 风格体系解耦：`COMMON_NOTE_STYLES` → `VIDEO_NOTE_STYLES` / `DOC_NOTE_STYLES`（`prompts/notes_styles.py`）
- Polish 固定忠实整理策略（无 `<style_preference>` 节），格式白名单 `_POLISH_FORMAT_FNS`
- Prompt 从扁平拼接改为 XML 分节：`<role>` → `<task>` → `<source_content>` → `<instruction_priority>` → `<hard_constraints>` → `<style_preference>` → `<output_format>` → `<user_request>`
- `PromptComposer` 分节组装器（`prompts/common.py`）
- `meeting_minutes` → `meeting` alias（`normalize_style()` 前后端兼容）
- 前端：polished only 模式隐藏风格选择器，both 模式标注"风格仅影响笔记"

**影响**：`prompt.py` 重写（986→650 行），`prompt_builder.py` / 前端 `GenerateOptions.tsx` / `GenPanel.tsx` / `genPrefs.ts` / `constants.ts` 适配。

### P0-2: 统一语言策略 — 输出语言 = target_language

**问题**：4 个 role 硬编码矛盾语言策略（视频强制中文、文档跟随原文、Excel 强制中文）。

**修复**：
- 所有 role 瘦身为纯角色定义（各 1 行），语言要求提取为动态 `<output_language>` 节
- `_build_output_language(target_language, source_type)` — 术语保留行业通用写法 + 不强制翻译
- Excel 独立规则：字段名保留原文，分析文字用目标语言
- `normalize_target_language()` — zh→zh-CN 等 5 个 alias
- `_INSTRUCTION_PRIORITY` 扩展为四层：hard_constraints > output_language > user_request > style_preference
- 删除 `translate` 格式选项（全链路清理：后端 5 处 + 前端 2 处，genPrefs 保留 deprecated 字段）
- 删除 `get_translate_format()` 函数
- `_build_hard_constraints()` 移除 `target_language` 参数

**影响**：`prompt.py` / `prompt_builder.py` / 前端 `GenerateOptions.tsx` / `GenPanel.tsx` / `genPrefs.ts`。

### P0-3: Agent 工具调用可见性 + 文档注入智能截断

**问题**：Agent 工具判定回合无任何反馈，用户干等；`rawText[:1000]` 硬截断可能切在句子中间。

**修复**：
- 新增 `agent_status` SSE 事件（工具判定前推送"正在分析问题..."）
- `tool_start` 增加 `display` 字段（后端 `_tool_display()` 生成安全文案，C 端不暴露路径）
- `tool_result` 改为 `summary` 摘要（仅计数/长度，完整结果只传 LLM 不出现在前端 UI）
- 新增 `smart_truncate()` — 按句子边界截断（`app/core/text_utils.py`），优先级：`。` > `. ` > `\n\n` > `\n`
- 前端 `streamChat()` 增加 `onStatus` / `onToolResult` 回调，`ChatCore.tsx` 适配新事件流
- spec hiddenimports 防御性添加 `app.core.text_utils`

**影响**：`agent.py` / `chat.py` / `text_utils.py`（新增）/ `client.ts` / `ChatCore.tsx` / `LiMoBackend.spec`。

---

## 2026-07-15（下午）— mode 过滤修复 + 打包兼容

### mode 精确匹配修复

`GET /markdown/{id}` 之前用 `(mode == "polished") == want_polished` 布尔技巧筛选，导致 `notes` 和 `fact_check` 两个模式被混在"非 polished"条目里一起返回。审核报告保存为 `fact_check` 模式后，前端加载笔记版本列表时也会把审核报告版本混进去展示。

**修复 3 处**：
- `generate.py:664` — `GET /markdown/{id}` 改为 `mode == mode` 精确匹配
- `generate.py:325` — 生成续写 partial 匹配改为 `mode == stream_mode` 精确匹配
- `export.py:1060` — summary tab 导出改为 `mode == "notes"` 精确匹配

### 打包兼容修复

- `review.py` 函数内 `from app.agent.evidence import format_evidence` 提升到模块顶部（PyInstaller 不追踪函数内导入）
- `LiMoBackend.spec` 新增 `app.generation.review` / `app.agent.evidence` 到 hiddenimports
- `LiMoBackend.spec` 新增 `duckduckgo_search` 到 collect_all（依赖 primp Rust 原生库）

### 默认搜索方案切换

`agent_tools.search.provider` 默认值从 `searxng` 改为 `duckduckgo`。DDG Instant Answer API 替换为 `duckduckgo_search` 库（真实网页搜索）。新用户无需配置即可使用搜索验证。

---

## 2026-07-15 — 事实验证重构为 AI Review（AI 报告审核系统）

### 做了什么

将原有 Search-First 的 FactCheckEngine 重构为四阶段 AI Review Engine：

**① Static Review（不联网）**: 提取可核查断言（Fact/Data/Opinion），检查内部一致性问题（缺少引用、绝对化表述、推理跳跃、逻辑冲突），双维度风险评分（Risk + Priority）。
**② Verification Planning**: 一次 LLM 批量决定每条 claim 的验证策略（LLM 已知/需联网/无法验证/观点跳过），为需搜索的 claim 生成 Query + Intent。
**③ Evidence Collection + Review**: 按需搜索（规则过滤域名优先权威来源），多维度评估（Evidence Strength + 事实一致性 + 来源可信度 + 时效性 + 引用充分性 + 推理合理性）。
**④ AI Review Report（流式）**: 红黄绿报告（🔴 需要立即修改 / 🟡 建议修改 / 🟢 可接受）+ 每个问题带 Action + 报告可信度 + 扣分项。

### 为什么

- DDG Instant Answer API 不是真正的网页搜索（只返回 Wikipedia 摘要），大量查询返回空结果
- Search-First 流程把搜索当目的，实际上搜索只是审核的手段之一
- 逐条串行验证极慢（30-90s），批量 LLM 调用可减少 80% 时间
- 输出只是 verified/disputed 二元标签，对用户帮助有限
- 新增全局设计原则：**审核结果必须可解释（Explainable by Design）**

### 影响范围

| 文件 | 动作 | 说明 |
|------|------|------|
| `backend/app/generation/review.py` | 新建 | ReviewEngine 四阶段管道 |
| `backend/app/generation/prompt.py` | 新增 4 prompt | STATIC_REVIEW / VERIFICATION_PLANNER / EVIDENCE_REVIEWER / AI_REVIEW_REPORT |
| `backend/app/routers/fact_check.py` | 修改 | 路由切换到 ReviewEngine（URL 不变） |
| `backend/app/generation/fact_check.py` | 保留 | 旧 FactCheckEngine 不动 |
| `backend/app/agent/tools.py` | 重写 DDG + 新增工具 | DDG Instant Answer API → `duckduckgo_search` 库；新增 `search_official` / `get_url_metadata` / `today` / `is_recent` |
| `backend/app/agent/evidence.py` | 新建 | Evidence Manager：统一证据格式 + 缓存（TTL 24h）+ URL 去重 |
| `frontend/src/store/index.ts` | 扩展 | 新增 reviewIssues / reviewSummary / trustworthiness 等字段 |
| `frontend/src/api/client.ts` | 扩展 | SSE 事件解析适配新格式（static_review / verify / review） |
| `frontend/src/components/preview/PreviewArea.tsx` | 重写 | fact_check tab 改为红黄绿审核报告 + 静态审核摘要 + 可信度展示 |
| `frontend/src/components/gen-panel/constants.ts` | 文案 | "事实核查" → "AI 审核" |
| `CLAUDE.md` / `CHANGELOG.md` | 更新 | 文档同步 |

---

## 2026-07-13 — 全面修复：8 项增强 + Bcut ASR + KaTeX + 公式渲染 + 引擎语义修正 + 打包

### 本轮追加修复（同日）

- **配置 Key 重启丢失**：`init_engines` 中 ASR 绑定强制先删后插重排 → 每次启动 `asr_changed=True` → `save_config` 写全量 config.yaml。若内存 config 缺字段则永久丢失。改为仅空列表初始化，后续启动不再触发保存。新增日志标注每次保存的变更来源
- **表格滚动条**：全局 CSS 补 `height: 4px`（横向滚动条之前 0px=不可见），新增 `.md-table-wrap` 8px 可见滚动条 + hover 变色。表格改用标准 `overflow-x: auto` 包裹（对标 GitHub），不嵌套纵向滚动避免"滑到底才能横滚"的体验问题
- **Excel 分析报告无价值**：新增 `_parse_markdown_table()` 解析表格→结构化摘要（行列数+列名+首尾样品），重写 prompt 模板新增 `⚠️ 严禁事项` 禁止重复输出原始表格。51KB 原始数据→~1KB 摘要发给 LLM
- **废弃代码清理**：删除 `agent/loop.py`（整个文件 86 行废弃），清理 `store.py` 5 个 DEPRECATED 方法、`export.py` 3 块废弃注释代码、`config.py` 1 个废弃函数，合计 ~300 行
- **FastAPI Depends 依赖注入**：新增 `app/core/dependencies.py`（get_store/config/guard/config_lock），conversations/capabilities/files 三个路由已迁移，其余可逐步迁移
- **Excel 分析报告移除**：LLM 无法做真正的数值分析，报告只是重复输出表格。删除 EXCEL_REPORT_PROMPT/build_excel_report_prompt，Excel 只生成数据摘要
- **PDF 默认引擎 pymupdf4llm**：pymupdf 的 builtin_models 新增 pymupdf4llm，`doc:text+image` 默认引擎改为 pymupdf4llm（文字+图片+矢量图，效果最好）
- **API Key 输入清洗**：`clean_api_key()` 自动去除 HTML 标签包裹、首尾空格和引号，防止从网页复制 Key 时带入 HTML
- **网页提取三层回退**：requests（正确编码+UA）→ trafilatura → html2text → 正则去标签。垃圾检测 >30% 自动降级，避免 SPA 页面乱码
- **网页/文本纯文本渲染**：跳过 ReactMarkdown + KaTeX，直接用 `whitespace-pre-wrap` 渲染，避免 base64 残留被误解析为 Markdown 格式
- **产物选择同步**：切换文档类型时自动重置 selectedCore 到可用产物列表，消除 Excel 默认勾选不一致
- **右键菜单定位修复**：ContextMenu 改为 `createPortal` 渲染到 body + fadeIn 动画去 `transform`，修复菜单偏移到预览栏/屏幕外的问题
- **PyInstaller 模块遗漏**：`agent/`/`generation/`/`routers/` 三个目录补 `__init__.py`；generate.py/fact_check.py 函数内 import 提到模块顶部，PyInstaller 不再遗漏 app 模块

### Bug 修复

- **UnboundLocalError**：`process.py`/`model.py` 函数内条件导入 `EngineRegistry` 覆盖模块级导入 → 条件未命中时抛异常。AST 扫描确认全代码库无其他同类问题
- **KaTeX 字体 404**：CSS 内联后 `@font-face` 保留相对路径 → 离线 HTML 公式渲染异常。60 字体 base64 内联 + 补齐 `backend/static/katex/fonts/`
- **公式渲染破裂**：`remarkGfm` 在 `remarkMath` 之前 → `_`/`<` 被误解析 → `$$` 块破裂。交换插件顺序
- **LogPanel 始终显示"暂无日志"**：`setInterval` 闭包陈旧，`logs.length` 永远为 0。改用 `useRef`
- **表格无滚动**：`<table>` 无容器 → 宽表溢出。新增 `overflow-x: auto` 包裹 + `.md-table-wrap` 8px 可见滚动条

### 引擎与适配器

- **引擎降级语义修正**：`auto_fallback` 不再拦截扩展名兜底（新用户无绑定时必须可用），仅控制引擎失败后的重试行为
- **未知扩展名兜底**：`markitdown` → `pymupdf`
- **Bcut ASR 引擎**：B站必剪免费云转写，启动时自动绑定为 ASR 默认，优先级最高

### HTML 导出

- 标题锚点 id、时间戳 badge（Content 蓝色/Screenshot 橙色）、MD 图片路径绝对 URL 重写
- Pygments 代码高亮 + GFM 删除线/任务列表 + `nl2br` 移除

### 错误处理

- 5 端点静默失败 → Toast（App 初始化/设置保存/聊天加载）
- 引擎降级 toggle Toast、streamFactCheck `receivedDoneOrError` 守卫
- LogPanel 阈值 12s→6s、SSE queue 异常类型收窄

### 代码质量

- spec `hiddenimports` 清理 5 处无效条目 → 0 ERROR
- 3 条新后端模式（`CREATE_NO_WINDOW`、`store.root`、`_watch_disconnect`）+ 2 条（阴影导入、插件顺序、auto_fallback 语义）
- 文档全面同步（CLAUDE.md/STATUS.md/CHANGELOG.md）

### 打包

- ffmpeg.exe + ffprobe.exe（434MB）内置，MSI 514MB
- KaTeX 60 字体打包

### 影响范围

- **后端**：export.py / ingest.py / process.py / model.py / document.py / LiMoBackend.spec / engines/**init**.py / prompt.py
- **前端**：client.ts / ProviderSettings.tsx / LogPanel.tsx / GenPanel.tsx / markdown-components.tsx / markdown-plugins.ts / index.css / App.tsx / ChatCore.tsx / SettingsPage.tsx
- **打包**：bin/LiMoBackend/ffmpeg/ / backend/static/katex/fonts/
- **文档**：CLAUDE.md / STATUS.md / CHANGELOG.md

---

## 2026-07-12 — 新图标 + README 重写 + 文档中链接修复

### 新应用图标

白底深蓝国风"沥墨"图形，替换了全部尺寸的图标（Tauri 打包 + favicon + 顶栏 logo）。

### README 重写

按用户任务重新组织——导入能做什么、AI 能产出什么、怎么导出——而不是堆技术细节。

### 旧产物死链接修复

历史 `[▶ mm:ss](#)` 链接渲染为静态 Play badge，不再触发页面滚动。

### 影响范围

- **文档**：README.md / CHANGELOG.md
- **前端**：App.tsx / index.html / markdown-components.tsx / 图标文件

---

## 2026-07-12 — Content 时间戳可点击 + 标题锚点滚动修复 + PPT 默认引擎切换

### Content-[mm:ss] → 可点击跳转

对标 BiliNote：`Content-[mm:ss]` 现在是可点击的链接，点击跳转到原视频对应时间点（B站/YouTube/通用格式）。仅视频文档生效，非视频文档显示静态 badge。

### 标题锚点 `scrollIntoView`

点击 `#heading-id` 链接不再带动整个窗口滚动，改为 `preventDefault` + 目标元素 `scrollIntoView`，仅影响预览区。

### 旧产物 `[▶ mm:ss](#)` 死链接

`_post_process_content_markers` 历史残留的 `href="#"` 链接不再跳转，渲染为静态 Play badge。

### PPTX 默认引擎 markitdown → python-pptx

MarkItDown 对某些 PPTX 不提取图片（找到 0 个 base64）。`doc:ppt` 默认引擎换回 `python-pptx`。

### 影响范围

- **前端**：markdown-components.tsx / MarkdownPreview.tsx / PreviewArea.tsx / GenPanel.tsx / ExportPanel.tsx
- **后端**：engines/**init**.py / document.py

---

## 2026-07-12 — 引擎开箱即用 + 截图导出内联 + 思维导图格式提示

### Doc 引擎自动绑定（开箱即用）

用户第一次用不需要碰引擎设置。三层修复：

- **后端**：`init_engines` 启动时自动注册 7 个 doc 子场景（text+image/word/ppt/excel/epub 等）到 `engine_models` + `engine_bindings`，MD5 ID 与 `build_engine_models` 对齐。save_config 移出 Tesseract try 块，绑写不受影响
- **前端**：页面加载时自动把 null 的 binding 填成第一个可用引擎 ID，`saveEngineBindings` 写盘
- 下拉框「系统默认」不再是空壳——背后有真实 ID

### 截图导出内联

导出系统之前只内联 `/api/files/` 文档图片，漏了 `/static/screenshots/` 视频截图。

- `_find_image_refs` 新增 `_SS_RE` 匹配截图 URL
- `_collect_images` 支持 `screenshots/` 目录读取
- `_inline_images` / `_rewrite_image_paths` 同时处理两种图片源
- HTML 导出自包含、ZIP separate/inline 三种模式全覆盖

### ExportPanel UI 提示

- 思维导图勾选时显示"始终导出为交互式 HTML，不受格式选择影响"
- 导出内容区显示"仅导出已生成的产物，未生成的内容会被自动跳过"

### 其他

- 后端 4 个 `subprocess` 调用统一加 `CREATE_NO_WINDOW` 消除 ffmpeg 黑框
- `Content-[mm:ss]` 后端不再替换为死链接，改由前端渲染为时间戳 badge
- `Screenshot-[mm:ss]` 前端新增 Camera 图标 badge
- 生成/导出完成后均有 Toast 提示

### 影响范围

- **后端**：engines/**init**.py / export.py / generator.py / video\*.py / asr_cloud.py
- **前端**：GenPanel.tsx / ExportPanel.tsx / ExportMenu.tsx / markdown-components.tsx / DocLibrary.tsx / VideoBanner.tsx

---

## 2026-07-12 — ffmpeg 黑框消除 + 标记渲染修复 + URL 统一

### subprocess 黑框消除

生成视频产物时 ffmpeg 会弹出 cmd 黑框。6 处 `subprocess.run` 调用加 `creationflags=subprocess.CREATE_NO_WINDOW`：

- `video_reader.py` / `video_helper.py` / `video.py` / `asr_cloud.py` / `export.py`

### Content/Screenshot 标记渲染修复

- `Content-[mm:ss]`：后端不再替换为死链接 `[▶ mm:ss](#)`，改为保留原文。前端渲染为 Play 图标时间戳 badge
- `Screenshot-[mm:ss]`：前端新增 Camera 图标 badge 渲染。后端勾选「原片截图」后 ffmpeg 抽帧替换为真实图片
- 后端 `_post_process_content_markers` 仅移除前导 `*`（避免 markdown 斜体干扰），不改变标记主体

### `appUrl()` 统一应用内 URL

Tauri 打包端无 Vite proxy，`/api/` 和 `/static/` 路径需转为绝对 URL。新增 `appUrl(path)` 函数，统一 `MarkdownImage` / `DocLibrary` / `VideoBanner` 三处图片加载。

### 影响范围

- **后端 5 文件**：video_reader.py / video_helper.py / video.py / asr_cloud.py / export.py / generator.py
- **前端 3 文件**：client.ts / markdown-components.tsx / DocLibrary.tsx / VideoBanner.tsx

---

## 2026-07-12 — 打包兼容 + 路径统一 + 测试支撑

### `_transcribe` 漏 `await` 修复

video.py 两处 `self._transcribe(...)` 改为 `async def` 后漏 `await`，返回 coroutine → 下游 `asr_result.get("segments")` 抛 `AttributeError`。

### `appUrl()` 统一 URL 转换

打包端（Tauri）无 Vite proxy，`/api/` 和 `/static/` 路径需转为 `http://127.0.0.1:7413` 绝对 URL。新增 `appUrl(path)` 函数，统一处理 `/api/`、`/static/`、外部链接三种情况。

**受影响组件**（全改为 `appUrl()`）：

- `MarkdownImage`：笔记内嵌图片、截图
- `DocLibrary`：文档封面
- `VideoBanner`：视频横幅封面

### 截图路径从 CWD → data_dir

- 后端生成：`os.getcwd()/data/screenshots/` → `{store_root}/screenshots/`
- StaticFiles 挂载：模块级相对路径 → startup 内 `{data_dir}/screenshots/`
- 打包端和网页端统一到 `~/.limo/screenshots/`

### 文件日志

新增 `app.log`（5×2MB 滚动，INFO 全量），路径 `{data_dir}/app.log`。打包端用户无法看终端，错误排查全靠这个文件。

### 文档删除时清理图片

`Store.delete_transcript` 现在清理 `files/{tx_id}/` 目录（文档内嵌图片），避免孤儿文件堆积。

### MarkItDown 图片诊断

`MarkItDownEngine` 加日志：找到 base64 图片数、写入成功数、解码失败详情。

### 影响范围

- **后端 4 文件**：video.py / main.py / generator.py / store.py / doc.py
- **前端 4 文件**：client.ts / markdown-components.tsx / DocLibrary.tsx / VideoBanner.tsx

---

## 2026-07-11 — 交互修复 + 导出面板重构 + 默认值修正

### AI 面板 / 思维导图滚动修复

- AI 面板 `scrollIntoView` → 容器 `scrollTop`，不再带动全页上翻
- 思维导图 `mm.fit()` defer 到 `requestAnimationFrame`，避免布局未完成触发页面滚动

### 思维导图白屏

`flex-1 bg-elevated` + `minHeight:200px` → 容器高度不足时 SVG 只有 200px，剩余空间显示浅色背景（像白色遮罩）。改为 `absolute inset-0` 填满父容器。

### ExportPanel 重构

- 按钮标签：「导出全部产物」→「一键导出所有产物 (ZIP)」；「导出 N 个文件」→「按上方设置导出 N 个文件」
- 删除 Markdown 预览（内容和导出不一致），替换为文字摘要
- 清理死代码：5 个无用 import、3 个 store selector、2 个 state 变量

### SVG 导出文字消失

`cloneNode` 丢失外部 CSS → 内联页面所有样式表规则到 `<style>` 标签注入 SVG。

### `streamDisplay=false` 产物静默丢失

流式关闭时 `onChunk` 直接 return，`onDone` 从空缓冲区取内容 → 产物永远为空。改为内部 ref 缓冲区始终累积，不受开关影响。

### SSE `\r\n` 解析

部分 API 返回 CRLF 换行 → JSON 解析失败，事件静默丢弃。4 处 SSE 解析统一加 `\r\n→\n` 归一化。

### 默认值修正

- `engine_auto_fallback`：`True → False`，引擎失败直接报错而非静默降级
- 公式图片渲染：`dpi 150→100, fontsize 16→14, pad 0.08→0.05`

### 影响范围

- **前端 5 文件**：GenPanel.tsx / client.ts / MindmapView.tsx / ExportPanel.tsx / ExportMenu.tsx
- **后端 2 文件**：config.py / export.py

---

## 2026-07-11 — 打包前缺陷修复：漏导入 + 截图 prompt + SVG 下载 + 死代码

### 漏导入（PyInstaller 打包后致命）

全量审计 36+ Python 文件，修复 3 处漏 import：

| 文件                   | 漏的 import  | 触发场景                                                    |
| ---------------------- | ------------ | ----------------------------------------------------------- |
| `adapters/document.py` | `FatalError` | Doc 导入时引擎未绑定 → NameError 500                        |
| `routers/model.py`     | `AppError`   | 厂商/引擎名称校验失败 → NameError 500                       |
| `routers/export.py`    | `shutil`     | KaTeX 服务端渲染预检 `shutil.which("node")` → NameError 500 |

### 纯文本 LLM 截图 prompt 修复

`video_understanding=false` 时 LLM 看不到网格图，但 prompt 还在说"从网格图左上角黄色数字中选取"。LLM 无图可选，不生成 `*Screenshot-[mm:ss]` 标记 → 截图后处理无标记可替换。

**修复**：`get_screenshot_format(has_visual=bool)` 按 VL 开关分叉——无 VL 时让 LLM 从转写时间戳中选取。

### tiktoken 打包兼容

PyInstaller 打包后 `tiktoken.get_encoding()` 抛 `AttributeError`（模块正常导入但原生扩展未链接），`except ImportError` 未捕获。改为 `except (ImportError, AttributeError)`。

### 思维导图 SVG 下载死按钮

`ExportMenu` 用 `document.querySelector('#mindmap svg')` 但 MindmapView 外层没有 `id="mindmap"` 容器，选择器永远返回 null。

### AI 面板 `scrollIntoView` 带动全页滚动

ChatCore 用 `bottomRef.scrollIntoView()` 滚动聊天到最新消息。面板刚挂载、布局未完成时，`scrollIntoView` 会带动整个页面向上翻。

**修复**：改用聊天容器的 `scrollTop = scrollHeight`，仅影响自身区域。

### 死代码清理

- `engineHasImages`：GenPanel 中 ~12 行 useMemo 计算 + GenerateOptions 中 prop 声明，从未被读取

### 打包

PyInstaller + Tauri v2 双包构建成功，含 ffmpeg 运行时。

### 影响范围

- **后端 4 文件**：document.py / model.py / export.py / content_preparer.py
- **后端 2 文件**：prompt.py + generate.py（函数签名 + 调用者）
- **前端 3 文件**：PreviewArea.tsx / GenPanel.tsx / GenerateOptions.tsx / ChatCore.tsx

---

## 2026-07-11 — 代码质量审查：修复 6 项 bug + 死代码清理 + 诊断增强

### 修复

| #   | 严重度 | 问题                                                                                          | 文件                  |
| --- | :----: | --------------------------------------------------------------------------------------------- | --------------------- |
| B1  |   中   | `_warn_mismatched_fields` 误报：`image_analyze` 加入 video 后，两个类型互相检查时会打 warning | `generate.py`         |
| B2  |   高   | 单产物路径用 raw `mode` 而非 `stream_mode` 判断 GenerateMode，传入非标准 mode 值时可能误判    | `generate.py`         |
| B6  |   中   | `generate_both` 只对 notes 做截图后处理，漏了 polished 内容                                   | `generator.py`        |
| B7  |   中   | `_optimize_data_uri` 缺 RGBA→RGB 转换，VL RGBA 图片保存 JPEG 可能失败                         | `content_preparer.py` |
| B8  |   中   | yt-dlp 下载失败时临时目录泄漏，长时间运行累积磁盘占用                                         | `content_preparer.py` |
| B10 |   低   | ASR 绑定日志说 whisper-small 是默认，实际 bcut 是首选                                         | `engines/__init__.py` |

### 死代码清理

- **`engineHasImages`**：GenPanel.tsx 中 ~12 行 useMemo 计算 + GenerateOptions.tsx 中 prop 声明，GenerateOptions 从未读取。已删除。
- **React 重复 key**：版本切换器用 `v.version` → 改为 `v.id`。markdown `p`/`li` 时间戳 badge 用 `useId()` 前缀。

### 图片→LLM 链路修复

- **空 src 警告**：图片剥离后 `![alt]` 渲染成 `<img src="">` → 改为 `[📷 alt]` 文本占位符
- **`MarkdownImage`**：空 src 直接不渲染元素（`return null`），避免浏览器重载整页

### LLM 流式诊断增强

`service.py` 加两份日志：

- 多模态请求体大小：`img_chars=2500K`，帮助诊断上传瓶颈
- 首个 chunk 到达时间：定位延迟在 API 侧还是网络侧

### 影响范围

- `generate.py`：type fields 共享字段处理 + `mode`→`stream_mode`
- `generator.py`：screenshot 双产物一致性
- `content_preparer.py`：RGBA 兼容 + temp dir 清理 + 空 src 修复
- `engines/__init__.py`：日志文案修正
- `client.ts`：VL 参数序列化补全 + `!== undefined` 统一
- `markdown-components.tsx`：空 src 守卫 + key 唯一性
- `PreviewArea.tsx`：版本 key 修复
- `GenPanel.tsx`：死代码删除 + VL 联动简化
- `GenerateOptions.tsx`：对标 BiliNote 简化 UI + VL 参数折叠
- `service.py`：多模态流式诊断日志

---

## 2026-07-11 — 对标 BiliNote 简化视频画面理解：一个开关 = VL + 发给 LLM

### 背景

之前 LiMo 的视频画面理解被拆成三层嵌套：`启用画面截图 → 保留截帧图片 → AI 分析图片`。对标 BiliNote 后发现过度设计——VL 管线生成的网格图是给大模型看的，嵌入笔记毫无意义（2×2 四格拼图对读者什么也说明不了）。BiliNote 的设计是 `video_understanding` 一个开关直接控制"VL 跑 + 发给 LLM"，简洁直观。

### 修改

**删除三级嵌套，改为对标 BiliNote 的单一开关：**

```
之前（过度设计）：
  视觉理解
  └── 启用画面截图 [checkbox]
      ├── 截图间隔、拼图尺寸...
      笔记格式
      └── 保留截帧图片 [checkbox]     ← 网格图嵌入文章（无意义）
          └── AI 分析图片 [checkbox]   ← 真正发给 LLM
              └── 图片预处理 [checkbox]

现在（对标 BiliNote）：
  AI 画面理解 ← "截取视频画面发给多模态模型辅助分析"
  └── 启用 [checkbox]
      ├── 截图间隔（秒）
      ├── 拼图尺寸（列 × 行）
      └── ▸ 高级设置（可折叠）
          ├── 单帧分辨率 / JPEG 质量
          ├── 相邻重复帧去重 / 帧质量过滤
          └── 图片预处理

  笔记格式
  └── 原片截图  ← 独立，ffmpeg 单帧，不经过大模型
```

**文档类型不变**：保留文档图片 → AI 分析图片 → 图片预处理 三级嵌套。文档图片对读者有意义，不同于 VL 网格图。

**后端同步简化**：`send_to_llm = bool(image_analyze)`。前端已保证门控（视频=videoUnderstanding，文档=formatImages&&imageAnalyze），后端不再重复检查 `keep_refs`。

**VL 参数精简**：默认只显示间隔+拼图（对标 BiliNote），其余折叠到 `<details>` 高级设置中。

### React 重复 key 修复

`markdown-components.tsx` 中 `p` 和 `li` 组件的 `key={i}` 在 `React.Children.map` 展平后产生重复 key。修复：使用 `React.useId()` 为每个组件实例生成唯一前缀 `key={`${pid}-${i}`}`。

### 前台参数序列化修复

`client.ts` 缺失 5 个 VL 参数（`dedupe_enabled`/`unit_width`/`unit_height`/`save_quality`/`frame_quality_filter`）的序列化。全部布尔/数字参数改为 `!== undefined` 判断。

### 单产物路径 send_to_llm / optimize_images 漏传

`generate.py` 单产物路径只传了 `keep_refs`，漏了 `send_to_llm` 和 `optimize_images`。

### 图片优化覆盖率

VL 图片和文档 data URI 发给 LLM 时此前不经过 optimize_images。

### 影响范围

- **前端 `GenerateOptions.tsx`**：三级嵌套改为单一开关 + VL 参数折叠 + 无模型时禁用
- **前端 `GenPanel.tsx`**：删除 VL↔formatImages 双向联动，image_analyze 按视频/文档分派
- **前端 `client.ts`**：补 5 个 VL 参数 + `!== undefined` 序列化
- **后端 `generate.py`**：`send_to_llm` 简化 + 单产物补参
- **后端 `generator.py`**：`send_to_llm` 简化
- **后端 `content_preparer.py`**：VL 图片 + data URI 优化覆盖
- **后端 `markdown-components.tsx`**：React 重复 key 修复

---

## 2026-07-11 — 视频截图逻辑解耦 + 图片→LLM 全链路修复

### 视频截图两条管线解耦

视频类型有两套完全独立的图片系统，之前被错误地耦合在一起：

| 选项             | 管线                                   | 时机                                   | 图片流向                               |
| ---------------- | -------------------------------------- | -------------------------------------- | -------------------------------------- |
| **原片截图**     | ffmpeg 单帧抽取                        | LLM 生成**后**，按 AI 标记的时间点抽帧 | 嵌入文章，**不经过大模型**             |
| **启用画面截图** | VL 管线（下载视频→抽帧→拼网格→base64） | LLM 生成**前**                         | 嵌入文章 + 可选发给 LLM（AI 分析图片） |

**问题 1**：「原片截图」被 `disabled={!videoUnderstanding}` 人为禁用，但后端 `generate.py:378` 本来就在无 VL 时独立下载视频做截图。**修复**：移除该约束，两个选项完全独立。

**问题 2**：关掉「保留截帧图片」后 VL 管线照样空跑（下载视频→抽帧→拼网格→丢弃）。**修复**：加反向联动 `!formatImages → setVideoUnderstanding(false)`。

**问题 3**：开 VL 自动开保留截帧图片，但没有反向联动。已在 GenPanel.tsx 补齐。

### 图片→LLM 参数传输修复

#### 5 个 VL 参数前端从未传给后端（严重）

`client.ts:streamGenerate()` 缺失 `dedupe_enabled`、`unit_width`、`unit_height`、`save_quality`、`frame_quality_filter` 的 query string 序列化。用户调整「单帧分辨率」「JPEG 质量」「去重」「帧质量过滤」**从未生效**，后端永远用默认值。

**同时修复**：所有布尔/数字参数从 truthiness 判断（`if (opts.xxx)`）改为 `!== undefined`，确保 `false` 和 `0` 也能正确传输。此前 `dedupe_enabled=false` 被当作 falsy 跳过 → 后端用默认值 `true`。

#### 单产物模式 send_to_llm / optimize_images 漏传

`generate.py` 单产物路径（line 538-540）调用 `prepare()` 时只传了 `keep_refs`，没传 `send_to_llm` 和 `optimize_images`。双产物路径正确传了三个参数。

**影响**：用户只勾选「笔记」或只勾选「整理版」时，「AI 分析图片」和「图片预处理」静默失效——图片永不给 LLM，也不压缩。

### 图片优化覆盖率修复

`optimize_images=True`（图片预处理）此前只在部分路径生效：

| 图片来源      |        嵌入文本        |           发给 LLM           |
| ------------- | :--------------------: | :--------------------------: |
| VL 网格图     | ✅ `_format_vl_images` |  ❌→✅ `_optimize_data_uri`  |
| 文档 data URI |     不嵌入（剥离）     | ❌→✅ `_optimize_image_data` |
| 文档文件引用  |       保留原路径       |  ✅ `_optimize_image_data`   |

### 影响范围

- **前端 `GenerateOptions.tsx`**：移除原片截图 disabled 约束 + 描述文案
- **前端 `GenPanel.tsx`**：加反向联动 effect（关保留截帧→关 VL）
- **前端 `client.ts`**：补 5 个 VL 参数 + 全部改为 `!== undefined` 序列化
- **后端 `generate.py`**：单产物路径补 `send_to_llm` + `optimize_images`
- **后端 `content_preparer.py`**：VL 图片 + data URI 发给 LLM 时走优化

---

## 2026-07-10 — 新增 Bcut（必剪）免费云转写引擎，设为默认

### 背景

faster-whisper 需要本地下载模型（~500MB），首次使用慢且占用 CPU。BiliNote 项目已有 B站必剪 API 的免费云转写实现，可直接搬运。

### Bcut API 特点

- **免费**：无需 API Key，只需 User-Agent
- **云转写**：上传音频 → B站服务器转写 → 返回分段结果
- **中文优化**：B站背景，中文识别质量好
- **耗时**：通常 10-60 秒（取决于音频长度和排队）

### 实现

`backend/app/engines/asr_bcut.py` — `BcutASREngine`，实现 `Engine` 协议：

- 分片上传音频（最多 ~500 分片）
- 创建转写任务 → 轮询结果（最长 500s 超时）
- 逐轮检查 `cancel_event`，支持取消
- 返回 `TranscriptResult`（语言 + 分段 + 时间戳）

### 默认绑定优先级

```
bcut（免费云）→ faster-whisper-small（本地兜底）→ dashscope（需 Key）
```

用户开箱即用，无需配置任何 Key 或下载模型。

### 影响范围

- **`backend/app/engines/asr_bcut.py`**：新增，~150 行
- **`backend/app/engines/__init__.py`**：注册 + 自动模型条目 + 绑定
- **`backend/app/adapters/video.py`**：`_resolve_asr_engine` 加 bcut provider 分支

---

## 2026-07-10 — 修复：PyInstaller 打包后后端启动崩溃（OpenSSL DLL 冲突）

### 现象

安装 LiMo 后启动，前端加载成功但所有 API 请求返回 `ERR_CONNECTION_REFUSED`。直接运行 `LiMoBackend.exe` 报错：

```
ImportError: DLL load failed while importing _ssl: 找不到指定的程序。
```

### 根因

`cryptography` 包携带的 `libcrypto-3-x64.dll`（~5MB）覆盖了 Python 原生版本（~8MB）。`_ssl.pyd` → `libssl-3-x64.dll` → `libcrypto-3-x64.dll` 依赖链中，cryptography 提供的 libcrypto 缺少 `libssl` 需要的导出符号，导致整个 `ssl` 模块无法加载 → FastAPI/anyio 无法启动。

### 修复

`LiMoBackend.spec` 中：

1. `collect_all('cryptography')` 后过滤掉其携带的 `libcrypto*/libssl*` DLL
2. 显式从 `sys.base_prefix/DLLs/` 添加 Python 原生的 OpenSSL DLL

### 影响范围

- **`backend/LiMoBackend.spec`**：新增 DLL 过滤逻辑 + 显式添加原生 OpenSSL
- **`CLAUDE.md`**：常见陷阱新增 OpenSSL 冲突条目
- 后续新增 pip 依赖时，若该包携带 OpenSSL DLL，可能再次触发此问题

### 验证方法

打包后直接运行 `LiMoBackend.exe`，确认输出 `Uvicorn running on http://127.0.0.1:7413`

---

## 2026-07-10 — 思维导图 HTML 导出空白（JS 未包裹 script 标签）

### 现象

导出的思维导图 HTML 文件在浏览器中打开后完全空白，什么都没有显示。

### 根因

`_build_mindmap_html()` 中，当本地 d3.js / markmap-view / markmap-lib 文件存在时，直接内联 JS 源码但**没有用 `<script>` 标签包裹**：

```python
# 修复前：裸 JS 文本，浏览器不执行
{d3_js if d3_js else '<script src="..."></script>'}
```

浏览器将这三个库的源代码当作文本节点渲染，`window.markmap` 永远为 undefined。只有 CDN 回退路径（`else` 分支）有 `<script>` 标签。

### 修复

每个内联 JS 都用 `<script>...</script>` 包裹：

```python
# 修复后
{f'<script>{d3_js}</script>' if d3_js else '<script src="..."></script>'}
```

已验证 d3.min.js / markmap-view.js / markmap-lib.js 均不含 `</script>` 字面量，无提前闭合风险。

### 影响范围

- **`backend/app/routers/export.py`**：`_build_mindmap_html()` 第 921-923 行
- 同步影响 `GET /export/{id}/mindmap` 和 `POST /export/custom` 两条路径

---

## 2026-07-10 — GFM 表格渲染遗漏 + mode 回退静默返回错误内容

### 表格问题

LLM 生成的 Markdown 中，"三大痛点：\n| 痛点 |" 这种表格紧跟前导文字无空行的写法在 GFM 中合法（react-markdown 正常渲染），但 Python markdown `tables` 扩展要求表格前有空行，导致导出 HTML 中表格被当作段落文本输出（原样显示管道符）。

**修复**：`_preprocess_gfm()` 中加正则，检测非表格行后紧跟表头+分隔行的模式，自动插入空行。

### mode 回退问题

`GET /export/{id}?mode=notes` 在 notes 未生成时，回退逻辑取全部 markdown 的第一个条目。若 polished 已生成，导出会静默返回 polished 内容，违反"预览一致"原则（预览栏显示"尚未生成笔记"）。

**修复**：仅当 mode 为空（默认）时回退到任意产物；mode 已被显式指定但未找到时，不进回退链，后续走 process results 或 404。

### 影响范围

- **`backend/app/routers/export.py`**：`_preprocess_gfm()` + `export_content()` 回退逻辑

---

## 2026-07-10 — ASR 引擎自动注册（启动即可用）

### 现象

界面显示已绑定 ASR 引擎，但导入视频时报"ASR 绑定链为空或全部失败，自动降级已关闭"。engine_models 表中只有 OCR 记录，ASR 模型条目缺失。

### 根因

Tesseract OCR 在 `init_engines()` 中有完整的自动注册逻辑（写入 `engine_models` + `engine_bindings`），但 faster-whisper / DashScope ASR 只注册了引擎实例，缺失了模型条目和默认绑定。`_resolve_asr_engine()` 通过 `engine_models` 表查找绑定 → 找不到 → 返回空。

### 修复

`app/engines/__init__.py`：对标 Tesseract 补齐 ASR 三步自动注册：

1. 写入 `engine_models`（faster-whisper-small/medium + dashscope）
2. faster-whisper-small 作为默认首选（本地免费，零配置即可用）
3. 清理 `engine_bindings` 中失效的绑定 ID

### 影响范围

- **`backend/app/engines/__init__.py`**：新增 ~60 行 ASR 自动注册
- 首次安装用户开箱即用，已有用户启动后自动修复失效绑定

---

## 2026-07-10 — 取消机制全链路修复（LLM Token + 本地 ASR CPU）

### 现象

点击取消按钮后前端 UI 清除、`AbortController.abort()` 触发，但后端继续消耗 token / CPU：

- **chat/stream**：`cancel_event` 创建后没人 set，Agent 工具循环继续跑
- **process（图片处理）**：完全没有取消机制，LLM 调用不可中断
- **本地 ASR（faster-whisper）**：同步阻塞代码卡死事件循环，`_watch_disconnect` 协程无机会执行

### 根因

三个独立问题：

1. `chat.py` 有 `cancel_event = asyncio.Event()` 但没有 `_watch_disconnect` 任务来监听客户端断连并 set 它
2. `process.py` 是普通 POST 端点，`async with guard.semaphore` 内部没有任何取消检测
3. `video.py:_transcribe()` 中 `engine.run()` 是同步阻塞调用，整个事件循环被卡死（包括 `_watch_disconnect`）。faster-whisper 逐段 yield 但未检查取消信号

### 修复

| 层           | 文件                   | 改动                                                                         |
| ------------ | ---------------------- | ---------------------------------------------------------------------------- |
| chat 端点    | `routers/chat.py`      | 加 `_watch_disconnect` + finally cleanup                                     |
| process 端点 | `routers/process.py`   | 加 `cancel_event` + disconnect 监听，传入 `llm.call()`                       |
| 适配器       | `adapters/video.py`    | `_transcribe` 改为 `async`，`engine.run()` 用 `asyncio.to_thread` 放到线程池 |
| ASR 引擎     | `engines/asr.py`       | `execute()` 接受 `cancel_event`，逐 segment 检查并抛 `FatalError`            |
| 云端 ASR     | `engines/asr_cloud.py` | `execute()` 签名加 `**kwargs` 兼容 cancel_event 透传                         |

**关键设计**：`asyncio.to_thread` 将同步引擎调用放到后台线程，事件循环恢复空闲 → `_watch_disconnect` 可正常运行 → `cancel_event.set()` → 线程中引擎在下一个 segment 检测到 → 立即停止，不再跑完整个转写。

### 影响范围

- 6 个文件，涉及聊天/图片处理/视频导入三条管线
- 取消后 token 消耗和本地 CPU 消耗可真正停止
- DashScope ASR（云端）因 HTTP 请求无法中断，但耗时短（几秒），影响小

---

## 2026-07-10 — 项目重命名：DeepDraft → LiMo（沥墨）

### 背景

发现 "DeepDraft" 名称已被他人使用，需要换名。

### 新名称

**LiMo（沥墨）** — LLM 的谐音。沥 = 过滤/沥出精华，墨 = 文字/文档。暗合项目本质：从信息洪流中沥出结构化知识。

### 修改范围

- **应用标识**：`com.deepdraft.app` → `com.limo.app`
- **二进制**：`DeepDraftBackend.exe` → `LiMoBackend.exe`（含 spec/tauri.conf/lib.rs）
- **日志文件**：`deepdraft_startup.log` → `limo_startup.log` 等
- **CSS 主题**：`data-theme="deepdraft"` → `data-theme="limo"`
- **导出默认名**：`DeepDraft_export_*.zip` → `LiMo_export_*.zip`
- **导出主题**：HTML `deepdraft` 主题 → `limo` 主题
- **User-Agent**：`DeepDraft/1.0` → `LiMo/1.0`
- **包名**：`deepdraft-backend` → `limo-backend`
- **所有文档**：CLAUDE.md、README、CHANGELOG、STATUS、export-pipeline、engine-standards、SECURITY_AUDIT
- **前端 UI 标题**：App.tsx、index.html、PreviewArea 欢迎语

### 影响范围

- 25 个源文件（全量文本替换）
- 1 个文件重命名（`DeepDraftBackend.spec` → `LiMoBackend.spec`）
- `target/` 构建缓存下次 `cargo build` 自动更新
- `dist/` 下次 `npm run build` 自动更新

---

## 2026-07-10 — 图片管线断层修复（第四轮）

### 背景

图片管线三段链路存在断层：导入保存原图 ✅ → 引擎转录 rawText（无图引用 ❌）→ LLM 生成笔记（无图可用 ❌）。经代码审计发现根因：

1. **PPTX 默认引擎图片提取严重不足**：`DocumentAdapter` 兜底链中 PPTX 默认使用 `python-pptx`（PptxEngine），该引擎仅提取 shape_type=13 的图片形状，遗漏大量内嵌图片（~1 张 vs markitdown 的 ~33 张）。CLAUDE.md 和引擎注释均说 markitdown 应替代 python-pptx，但代码未更新。
2. **PptxEngine 无 base64 回退**：`media_dir` 为空时图片提取被 `if media_dir` 守卫静默跳过，对标其他引擎（PyMuPDF/DocxEngine 都有双模式）缺失。
3. **EpubEngine 同问题**：`_walk()` 中 `if src and media_dir` 守卫导致无 `media_dir` 时图片静默丢失。
4. **前端图片加载失败静默隐藏**：`markdown-components.tsx:145` 的 `onError` 设 `display:none`，用户看不到本该有图的位置。

### 修改内容

#### 1. PPTX 默认引擎改为 markitdown

**文件**: `backend/app/adapters/document.py`

兜底链中 PPTX 从 `python-pptx` 改为 `markitdown`，与引擎注释和 CLAUDE.md 设计意图一致。

#### 2. PptxEngine 增加 base64 回退

**文件**: `backend/app/engines/doc.py`

`_extract_pptx_image()` 现在支持双模式：

- `media_dir` 非空 → 写文件引用 `![slide image](img_0001.png)`（原行为）
- `media_dir` 为空 → 内嵌 base64 `![slide image](data:image/png;base64,...)`

同时移除调用处的 `media_dir` 守卫条件。

#### 3. EpubEngine 增加 base64 回退

**文件**: `backend/app/engines/epub.py`

`_walk()` 中图片处理逻辑同样改为双模式，移除 `media_dir` 守卫。

#### 4. 前端图片加载失败显示占位符

**文件**: `frontend/src/lib/markdown-components.tsx`

- 新增 `MarkdownImage` 组件（独立组件以使用 `useState` hook）
- 加载失败时显示虚线边框占位符（SVG 图标 + alt 文字），替代原先的 `display:none`
- 对标 `CodeBlock` 独立组件模式

### 图片管线修复后状态

```
导入保存原图 ✅ → 引擎转录 rawText（含 ![]() ✅）→ LLM 生成笔记（可引用 ✅）
                                    ↕
                          content_preparer: keep_refs/send_to_llm 控制
```

| 引擎                         | 图片输出       | 备注                     |
| ---------------------------- | -------------- | ------------------------ |
| PyMuPDF (text_only=False)    | ✅ 文件/base64 | 嵌入图片 + 表格          |
| PyMuPDF4LLM                  | ✅ 文件/base64 | write_images=True        |
| MarkItDown                   | ✅ 文件/base64 | 全格式通用               |
| DocxEngine (text_only=False) | ✅ 文件/base64 | 行内图片                 |
| PptxEngine (text_only=False) | ✅ 文件/base64 | **本轮新增 base64 回退** |
| EpubEngine                   | ✅ 文件/base64 | **本轮新增 base64 回退** |
| ExcelEngine                  | ❌             | 无图片管线（表格为主）   |

### 影响范围

- 后端：`document.py`（兜底链）、`doc.py`（PptxEngine）、`epub.py`（EpubEngine）
- 前端：`markdown-components.tsx`（新增 MarkdownImage 组件）

---

## 2026-07-10 — 离线资源打包 + 事实验证日志（第三轮）

### 背景

上一轮对话日志中发现 3 个问题：d3.js 离线不可用、事实验证日志不透明、SearXNG 未打包。同时核实了 plan 中 47 项修复的实际状态——大部分已在之前会话中修复完毕。

### 修改内容

#### 1. d3.js / markmap 离线不可用修复

**根因**：`_get_markmap_assets()` 仅从 `frontend/node_modules/` 读取 d3.js 和 markmap JS 文件。PyInstaller 打包后该路径不存在，思维导图 HTML 导出依赖 CDN fallback，但 Tauri WebView2 跟踪防护会拦截 CDN 请求，导致离线环境导出空白。

**改法**：

- `export.py` `_get_markmap_assets()`：增加双路径搜索（对标 KaTeX 已有设计）
  - 开发环境：`frontend/node_modules/`（优先）
  - 打包环境：`backend/static/markmap/`（备选）
- 创建 `backend/static/markmap/` 目录，从 node_modules 复制 3 个文件（d3.min.js 280KB + markmap-view.js 50KB + markmap-lib.js 677KB）
- 同步创建 `backend/static/katex/` 目录（KaTeX 双路径设计已有但 static 目录缺失）
- `LiMoBackend.spec`：datas 新增 `('backend/static', 'static')` 打包离线资源
- `CLAUDE.md`：PyInstaller 常见陷阱新增"KaTeX + markmap/d3 离线资源"条目

#### 2. 事实验证日志不透明

**根因**：`FactCheckEngine.verify_claim()` 中搜索、LLM 调用、未预期异常共用一个笼统的 catch，日志 `验证 claim '...' 失败: {e}` 无法区分失败阶段。上次日志中 `失败: ` 后面为空即是因为异常类型不明。

**改法**：

- 搜索阶段：独立 try/except，记录 provider source + query
- 搜索无结果时：info 级别日志记录 provider 和 query（方便排查是搜索源问题还是 query 问题）
- LLM 交叉验证阶段：独立 try/except，标识"LLM 交叉验证失败"
- 外层 catch-all：日志增加异常类型名 `type(e).__name__`

#### 3. 核实 plan 中 47 项实际状态

| 状态     | 项目                                                                             |
| -------- | -------------------------------------------------------------------------------- |
| 已修复   | C1, H1-H8, H12（9 项，之前会话中已修）                                           |
| 误报     | C2（`disconnect_task=None` 在 try 前是防御性写法，finally 块引用它做 None 检查） |
| 本轮修复 | d3.js 离线、事实验证日志（2 项）                                                 |
| 低优先级 | SearXNG 未打包（200MB+ 依赖，非 bug，搜索降级链正常）                            |

### 影响范围

- 后端：`export.py`（`_get_markmap_assets` 双路径）、`fact_check.py`（verify_claim 分阶段日志）
- 构建：`LiMoBackend.spec`（新增 static/ datas）、`backend/static/`（新增 markmap + katex 目录，~1.3MB）
- 文档：`CLAUDE.md`（PyInstaller 构建陷阱）、`CHANGELOG.md`（本条记录）

---

## 2026-07-10 — 导出系统全面修复（第二轮）

### 背景

用户反馈两个问题：

1. "详细导出无法导出" — 控制台报 `/api/api/export/custom` 404
2. "快速导出导出的不是预览栏展示的对应产物"

第一轮修复（同日早些时候）解决了 GFM/Pygments/KaTeX/暗色模式等问题，但用户使用时发现路径重复和内容映射错误。

### 修改内容

#### 1. 路径重复 `/api/api/` → 404（6 处）

**根因**：`downloadFromUrl()` / `apiUrl()` 内部已拼接 BASE（含 `/api`），但调用方又传了带 `/api` 前缀的路径。

| 文件                               | 修复                                                        |
| ---------------------------------- | ----------------------------------------------------------- |
| `ExportMenu.tsx:85` HTML 导出      | `/api/export/` → `/export/`                                 |
| `ExportMenu.tsx:121` 思维导图导出  | 同上                                                        |
| `ExportMenu.tsx:164` ZIP 导出      | 同上                                                        |
| `ExportPanel.tsx:346` 导出全部产物 | 同上                                                        |
| `ExportPanel.tsx:362` 详细导出     | `apiUrl('/api/export/custom')` → `apiUrl('/export/custom')` |
| `ChatCore.tsx:313` 对话导出        | 同上                                                        |

#### 2. ExportMenu 重写 — 集中式 Tab→内容映射表

**为什么**：原先 tab→导出行为的映射分散在嵌套三元表达式（`handleExportHTML`）和 if-else 链（`getExportContent`）中，14 个 tab 只有 6 个被正确处理，图片处理 tab、skeleton、mindmap 全部 fallthrough 到错误内容。

**改法**：在 `ExportMenu.tsx` 中引入 `TAB_EXPORT` 查找表，13 个 tab 全量覆盖：

```typescript
const TAB_EXPORT: Record<string, TabExportConfig> = {
  summary:     { contentKey: 'notes',      htmlMode: 'notes',      suffix: '', ... },
  polished:    { contentKey: 'polished',   htmlMode: 'polished',   ... },
  transcript:  { contentKey: 'rawText',    htmlMode: 'transcript', ... },
  data_preview:{ contentKey: 'rawText',    htmlMode: 'transcript', ... },
  report:      { contentKey: 'polished',   htmlMode: 'polished',   ... },
  fact_check:  { contentKey: 'factCheck',  htmlMode: 'fact_check', ... },
  mindmap:     { contentKey: 'notes',      htmlMode: '__mindmap__',... }, // HTML 走专用端点
  skeleton:    { contentKey: 'rawText',    htmlMode: 'transcript', ... },
  extract:     { contentKey: 'imageResult',htmlMode: 'extract',    ... },
  table:       { contentKey: 'imageResult',htmlMode: 'table',      ... },
  digitize:    { contentKey: 'imageResult',htmlMode: 'digitize',   ... },
  understand:  { contentKey: 'imageResult',htmlMode: 'understand', ... },
  original:    { contentKey: 'none',       htmlMode: '',           ... }, // 原图无文本可导出
}
```

**设计原则**：新增 tab 时只需在此表加一行，无需改动任何业务逻辑。

#### 3. 后端 `GET /export/{id}` 增加 process result 回退

**为什么**：图片文档（sourceType=image）没有 markdown 条目，处理结果存在 `process_results` 中。原先 `export_content` 端点只查 markdown 表 → 404。

**改法**：在 `export.py:115` 增加回退链：

```
markdown（notes/polished/fact_check）→ process_results（extract/table 等）→ 404
```

#### 4. ExportPanel 修复

- **预览数据源**：原先始终显示 `currentContent`（PreviewArea 当前文档），与导出面板中选中的文档无关。改为仅当 `selectedIds[0] === currentDocId` 时才显示预览。
- **"导出全部产物"按钮**：原先仅单文档可用（`disabled={selectedIds.length !== 1}`）。改为：单文档走 `GET /export/{id}/all`，多文档走 `POST /export/batch`。

#### 5. 文档更新

- [export-pipeline.md](export-pipeline.md)：全面重写，新增架构总览、完整 tab→内容映射表（5 种文档类型 × 14 tab）、设计决策记录、新增模式操作指南
- [CLAUDE.md](../CLAUDE.md)：导出系统关键模式重写，增加两条路径设计、核心约束、回退链、已修复问题清单

### 影响范围

- 前端：`ExportMenu.tsx`（重写）、`ExportPanel.tsx`（3 处修改）、`ChatCore.tsx`（1 处）
- 后端：`export.py`（`export_content` 增加 process result 回退）
- 文档：`export-pipeline.md`（重写）、`CLAUDE.md`（导出系统部分更新）

---

## 2026-07-10 — 导出管线全面审计 + 第一轮修复

### 背景

用户要求："各种产物导出逻辑是否正确，是否能按要求样式设置"。

对 `backend/app/routers/export.py` 进行端到端审查，发现 12 项问题，按 P0-P3 优先级分批修复。

### 修改内容

#### P0：`_remove_formulas` 跨行公式匹配

`$$...$$` 块级公式使用 `r'\$\$[\s\S]*?\$\$'` 替代原正则，支持跨行匹配。

#### P1：`data_preview` 内容源修正

`export_custom` 中 `data_preview` tab 原从 markdown 表读取（错误），改为 `tx["rawText"]`（与预览栏一致）。

#### P1：mindmap 端点内容选择

`GET /export/{id}/mindmap` 原取 `mds[0]`（可能为 polished/fact_check），改为优先取非 polished/非 fact_check 的笔记。

#### P2：行内 `$` 解析 — 防误匹配货币

`_protect_latex` / `_remove_formulas` / `_render_formulas_to_images` 三处行内 `$` 正则统一改为 TeX 惯例：

```
(?<!\d)\$(?=\S)(.+?)(?<=\S)\$(?!\d)
```

`$` 前后不邻数字或空白，不再误匹配 `$5 and $10`。

#### P2：`_to_html` / `HTML_HEAD` / `HTML_FOOT` 弃用

所有 HTML 导出已统一走 `_build_selfcontained_html()`（零 CDN）。旧函数注释保留以便未来恢复 CDN-base 轻量导出。

#### P3：代码语法高亮（Pygments）

- 新增 `_PYGMENTS_OK` 可选依赖检测
- `codehilite` 扩展在 Pygments 可用时启用
- `_get_pygments_css()` 生成 CSS + `.codehilite` 样式
- `_add_lang_labels()` 从 `<code class="language-xxx">` 生成语言标签

#### P3：删除线支持

`_preprocess_gfm()`：`~~text~~` → `<del>text</del>`，4 主题 CSS 追加 `del { text-decoration: line-through; }`。

#### P3：任务列表支持

`- [ ] task` → `☐ task`，`- [x] task` → `☑ task`。

#### P3：limo 暗色模式

limo 主题增加 `@media (prefers-color-scheme: dark)` 完整暗色适配。

### 构建验证

- 前端 `npm run build` ✅
- 后端 6 测试文件全过（38/38）✅
- Tauri MSI + NSIS 安装包生成成功 ✅

### 影响范围

- 后端：`export.py`（约 200 行修改，新增/修改 ~10 个函数）
- 文档：`export-pipeline.md` 全面更新
- 内存：`docs/export-pipeline.md` 记录所有修复细节

---

## 2026-07-10 之前 — 基础架构沉淀

以下内容来自 CLAUDE.md 和既有文档记录的关键设计决策，日期为最后审查确认日。

### 数据流与持久化

- 配置唯一入口 `config_io.py → save_config()`，禁止业务模块直接 `yaml.dump`
- 模型解析唯一入口 `model_resolver.py → resolve_model()`
- 引擎映射用 `model_name` 查找（非 `provider_id`），支持字符串/数字/列表三种绑定格式
- 引擎绑定保存增量合并（`existing.update(stored)`），非全量替换
- LLM Key 双向同步：`update_provider` 自动同步到 `llm.api_key`

### 导入与去重

- GenPanel 是唯一导入入口，DocLibrary 只负责搜索/筛选/列表
- 两层去重：URL 去重（请求到达时）→ 内容去重（SHA256，提取正文后）
- 空文本去重碰撞：空字符串 SHA256 固定，TextAdapter 检查 `text.strip()` 非空

### 生成与处理

- 双产物并行（`asyncio.gather`）：notes + polished，source_type 派发 prompt
- 图片三开关：`keep_refs` → `send_to_llm` → `optimize_images`
- LLM 续写：`finish=length` → 3 轮续写，temperature 递减
- 流式 + 取消：SSE 端点，`cancel_event` 真正中断 token 消耗

### 并发与安全

- `ExecutionGuard` 双层信号量：cloud(5) + local(2)
- Store `threading.Lock` 防 RMW 竞态
- 安全加固：path traversal 防护、上传 2GB 预检、SSRF 防护（14 个 IP 段黑名单 + DNS 二次校验）

### 前端关键模式

- GenPanel 类型门控：5 类型 × 5 子标签
- PreviewArea 按 sourceType 分派 tab
- processingTasks 键格式：`${docId}__${kind}`
- 所有 HTTP 请求必须走 `apiUrl()` 或 `apiFetch()`
- 主题系统：14 个 CSS 令牌 + Tailwind v4 + Light/Dark/System
- 取消机制：`cancelEvent` + `request.is_disconnected()` 轮询

### 导出系统（初始）

- 图片收集/重写/内联三个核心函数
- HTML 自包含（KaTeX 预渲染·内联 CSS·零 CDN·4 主题）
- ZIP 三种图片模式 + 多产物标签
- 思维导图：SVG 下载 + markmap 交互式 HTML

### Tauri 桌面壳

- 生产模式：`std::process::Command` 启动 PyInstaller 打包的 exe
- 就绪探测：HTTP GET `/health` 轮询（500ms 间隔，45s 超时）
- 进程管理：`SidecarHandle(Mutex<Option<Child>>)`
- PATH 增强：`enhance_path()` 追加 ffmpeg 路径
- CORS：`allow_origin_regex=".*"` + `_ForceCORSMiddleware` 兜底

---

## 如何添加新记录

1. 在文件顶部 `## YYYY-MM-DD — 标题` 添加新条目
2. 包含三个部分：
   - **背景**：为什么做这个修改
   - **修改内容**：具体改了哪些文件、哪些行
   - **影响范围**：前端/后端/文档/构建
3. 如果修改涉及设计决策，同时更新 `docs/export-pipeline.md` 等专题文档
4. 如果修改了关键约束，同步更新 `CLAUDE.md` 中的"关键模式"部分
