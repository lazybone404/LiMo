# 短期待办

**这里放什么**：已经决定要做、做法清楚、近期就会动手的事。做完直接从本文件删掉。

**这里不放什么**：

- 还没定要不要做的方向、以及**等触发条件**的重构 → [roadmap.md](roadmap.md)。
  roadmap 只留大方向与「继续这么长下去会变贵」的结构债，不排期。
- 已经做完的记录 → roadmap 各域的 ✅ 行与 [CHANGELOG.md](CHANGELOG.md)。

⚠️ **同一条只待在一个地方。** 本文件与 roadmap 各写一份，等于两处各自记着「这件还没做」，
做完时漏删一处，下个接手的人就会重复做一遍。

## 排队中

### 1. 自动更新（安装版）｜优先级：高

客户端读 GitHub Release 的 `latest.json`，与 `CARGO_PKG_VERSION` 比版本号，有新的就
下载 → minisign 验签 → 安装 → 重启。**只做安装版**：LiMo 只发 NSIS 安装包
（`bundle.targets` 写的是 `all`，实际产出只有 `LiMo_x.x.x_x64-setup.exe`），
Tauri updater 插件承担全部下载 / 验签 / 替换 / 重启。

三处落点必须同批做：

1. `frontend/src-tauri/tauri.conf.json` 加 `bundle.createUpdaterArtifacts: true` 与
   `plugins.updater.pubkey` —— 两者当前都不存在，`plugins` 是空对象；
2. 发版流程加「签名 → 生成 `latest.json` → 随安装包一起上传」。现在 `scripts/` 下没有
   任何 release 脚本，`.github/workflows/` 只有 `ci.yml` 与 `deploy-demo.yml`，
   发版靠手工 `gh release create`；
3. 发版门禁加一条「`latest.json` 存在且 `signature` 非空」，与 `sync-version.py --check`
   同性质。

⚠️ **漏签是静默失效**：客户端只认为「没有新版本」，继续用旧版，全程不报错。
⚠️ **私钥丢失不可逆**：存量客户端只认已内置的公钥，换密钥后它们再也收不到自动更新。

与「打包 ≠ 发布」不冲突：自动更新是客户端行为，没发布过新版本就什么都不会发生。

### 2. 预览区上方三条栏收窄

顶栏 `h-12`、文档标签栏标签 `h-8`、预览区工具栏 `py-2.5`（分别在 `App.tsx`、
`components/preview/DocTabBar.tsx`、`components/preview/PreviewArea.tsx`），合计约 136px，
压到约 106px，图标同步缩小一档。理由是这些入口的重要性低于正文，视线应落在预览内容上。

落地时确认两件事：按钮热区不小于 24×24；窄窗口下标签栏仍能横向滚动。

### 3. 分层依赖守卫

写一条 AST 测试：遍历 `backend/app/` 下每个 `.py`，断言 `services/` 不出现 `fastapi`
import、`storage/` 不出现 `app.routers` / `app.services` import。**只做这一条**，约十行。

反方向（`storage` import `routers`、`adapters` 互相 import）破坏时 Python 会循环 import
当场崩，是响亮失败，不需要守卫。⚠️ 今天这两条约束零自动检查，破坏形态是「加个
`from fastapi import Request` 做类型注解，测试全绿」，几周后才在「import `QueryService`
而不装 FastAPI」时暴露，报出来的是 `ModuleNotFoundError`，原因隔了几十个提交。

### 4. 导出 meta 承载（四期）

md / html / docx **保留**、纯 txt **剥离**（注释在纯文本里是可见噪音）；docx 走
`core_properties`（零新依赖）；`[文档 i]` 用 meta 的 `sourceRefs` 解析回稳定 id。

### 5. 图片预处理复用

导入时已 `optimize_images`，导出 ZIP 未复用压缩后的图片，直接打包原图。

### 6. 提示词版本标记：指纹守卫 + 预览区显示

**现状**：每份产物首行的 `<!-- limo-meta -->` 里已经有 `generated.promptVersion`
（`provenance.py:80`），但两处是断的。

1. **它是手抄的**。`PROMPT_VERSION` 靠人记得改，已经漏过一次 —— `1ef7e23` 加了关键词
   规范没 bump，「23」同时对应两种 prompt。手抄的版本号是 prompt 文本的第二份副本，
   漂移不报错。
2. **界面上一处都不显示**。`artifactMeta.ts` 的 `buildMetaChips` 把 `promptVersion`
   与 `params` / `sourceRefs` 一起排除了，版本下拉列的是「时间 · 字数 · 模型」。
   于是「这版是哪个提示词生成的」在界面上无从判断。

**已定的两个选择**（2026-11-08）：版本号**保留手工 bump**（「v24」这种说法要留着），
另存一份 prompt 文本指纹，用测试断言两者对得上；显示位置**只做预览区**，文档库列表
不做（它按文档走，而 prompt 版本按产物版本走，一篇有 v1/v2/v3 时那一列该显示哪个
需要另定，不在这一步）。

**落点**：

| # | 位置 | 改动 |
|---|---|---|
| 1 | 新增 `generation/prompts/fingerprint.py` | AST 扫 `generation/prompt.py` 与 `generation/prompts/*.py` 的字符串字面量（跳过 docstring），sha256 取前 8 位 |
| 2 | `prompts/__init__.py` | 加 `PROMPT_FINGERPRINT`，与 `PROMPT_VERSION` 并排 |
| 3 | 新增 `tests/test_prompt_fingerprint.py` | 声明值 == 计算值；外加对照实验（改一个常量 → 计算值必须变），证明守卫有区分力 |
| 4 | `store._scan_markdown_versions`（重建路径） | 从自证头读 `generated.promptVersion` 进条目 |
| 5 | `store.save_markdown` 的 index 写入（写路径） | 同一字段 —— **4 与 5 是同源的一对**，只改一处则重建后全空，且不报错 |
| 6 | `QueryService.artifact_versions` | 白名单加 `promptVersion` |
| 7 | `api/core.ts` 的 `MarkdownVersion` | 加 `promptVersion?: string` |
| 8 | `VersionSwitcher.tsx` 每行 | `时间 · 字数 · 模型` 后加 `prompt 24` |
| 9 | `artifactMeta.ts::buildMetaChips` | 加一个 chip（`MetaChipKey` 加 `prompt`；`CHIP_ICONS` 是穷尽映射，漏配图标会当场类型错） |

⚠️ **第 9 条要顺手改掉那个文件里的旧注释**。`artifactMeta.ts:70-71` 现在写着
「`params` / `promptVersion` / `sourceRefs` 是给 agent 读的，铺给用户只是噪音，刻意
不进这张表」—— 不改，下一个人会照着注释把它删回去。

⚠️ **历史产物读不出 `promptVersion`**（早于自证头那一批，以及 `_scan_markdown_versions`
里已注明「产物 status 没有落进自证头」的同类情况）：缺就**不显示这个 chip**，不补默认值。
补 `0` 或空串等于伪造「它来自第 0 版提示词」。

### 7. 产物折叠阅读：三档

**现状**：设计在 [frontend/_fold-proto.html](../frontend/_fold-proto.html) 里跑通了
（一次性原型，不进构建、不提交，定稿后删）。三档：主题（卡片网格）/ 一句话（每节
一句）/ 全文。实测尼采那篇的 v5 笔记压缩到 5.3% / 17.1% / 100%。

生成侧的两条路都试过并撤回，**不要再加回去**：五条结构规范（加粗 43→1、引用块
16→24）与「每节首段」（引导句比例 24%→28%）。理由记在
[prompts-inventory.md](prompts-inventory.md) 的 12.4。

**已定的两个选择**（2026-11-08）：

1. **扫描 + 复用现有渲染器**。不移植原型那 400 行解析器 —— 报表、代码块、KaTeX、
   `limo-block` 语义块全由 `MarkdownPreview` 渲染，第二个渲染器的分叉不报错。
   代价是**放弃字符级 FLIP**，档位切换只能是淡入淡出。
2. **入口在预览区工具栏**，与版本切换器同一排（`PreviewArea.tsx:1055` 旁边），
   只在 notes / polished 的**只读态**出现，编辑中隐藏（与版本切换器同一条纪律）。

**落点**：

| # | 位置 | 改动 |
|---|---|---|
| 1 | 新增 `lib/fold-scan.ts` | 纯函数、零 React：切节、抽句、取加粗词。照 `lib/artifactMeta.ts` 的形态，node 可直测 |
| 2 | 新增 `lib/fold-render.ts` | 一句话档：扫好的节 → 一份缩过的 markdown 字符串，交给 `MarkdownPreview` |
| 3 | 新增 `components/preview/FoldCards.tsx` | 主题档的卡片网格（直接画 DOM，不过 Markdown） |
| 4 | `PreviewArea.tsx` | 工具栏三段控件；档位进本机偏好（照 `lib/reading-prefs.ts`），刷新后不回到全文 |
| 5 | `lib/reading-prefs.ts` | 加一个轴，与既有的宽度 / 字号 / 沉浸同住 |

**三处会静默坏的地方**：

- ⚠️ **不要把 `<!-- limo-meta -->` / `<!-- limo-block -->` 一起剥掉**。原型第 210 行
  是 `s.replace(/<!--[\s\S]*?-->/g, '')`，在独立页面上无所谓；搬进 app 会把十二种
  语义块全吃掉，而折叠视图照样渲染、零报错。扫描器必须复用 `lib/markdown-markers.ts`
  对 `limo-block` 的处理。
- ⚠️ **切节与抽句只有一份实现**。主题档卡片里那一条、一句话档显示的那一句、字数统计，
  三处都要走 `fold-scan.ts` 的同一个 `leadOf`。各写一份时显示的不是同一句，不报错。
- ⚠️ **卡片层级按标题编号的段数，不按 `#` 数**（原型第 763-776 行）。模型常把「3.2」
  与「3.2.1」写成同一级 `####`，只认 `#` 数会让子节与父节并排。

**长文判据**（2026-11-08 定，生成侧门控用）：

- **生成前**（决定要不要注入格式规范）：转录正文去标点后 ≥ 3000 字符。**已落地**
  （`PROMPT_VERSION` `"25"`），且**只管关键词加粗** —— 语义块 `limo-block` 仍无条件
  注入（它服务于正常阅读，与折叠无关）。判据是
  `len(re.sub(r"[^\w]", "", body)) >= 3000`，`body` 取自
  [text_utils.py](../backend/app/core/text_utils.py) 的 `split_segments_json` ——
  视频的 `raw_text` 是 ASR JSON，直接 `len(raw_text)` 会把键名与括号算进字数。
  全库实测切掉 27%（169 篇有正文的转录里 46 篇切到短文档）。
- **生成后**（决定折叠入口要不要出现）：用产物自身的字数，**不可搬上面那个 3000**。
  产物侧的「长文」是去标点 ≥ 3800，而转录→产物的压比是 **0.51**（不是 0.70）——
  去标点后 Markdown 的 `**` `#` `-` `|` 全被剔掉，产物掉得比转录多，两个口径不能互换。

⚠️ **门控之后 `promptVersion` 不再唯一标识输入**：同一个 `"25"` 对应长文与短文两种
prompt，自证头里分不出来。要区分就在 `generated.params` 里加 `longText`
（`provenance.markdown_meta` 的 `extra_params` 能透传），但那个值的来源不在落盘点的
作用域里 —— 要么给 `MarkdownView` 加字段，要么在 `generate.py` 的五处 notes 落盘点
各自算一遍。本轮没做。

⚠️ **第一条只挡得住最短的四分之一**。39 篇 pv≥19 的产物里，「给了标记但产物不到
3800」的有 21 篇 —— 转录三五千字、产物一千五的那批靠转录判不出来，只能由产物侧那条兜住。
网页 / 文档类转录在库里没有样本，判据只在视频与音频上校准过。

**没定的**：沉浸态是否也给入口（工具栏在沉浸下整条不渲染）；折叠是否参与导出
（建议不参与，与 `ArtifactMetaBar` 同一条纪律 —— 导出物里只有正文）。
