# CLI / MCP 这条链 —— 给在**本仓库**里干活的 agent

> 这份文档讲**怎么改 CLI 与 MCP 服务端**（两者是同一个 `Source` 契约的两个消费者）。
> 它的兄弟版是 `~/.limo/AGENTS.md`（由 `limo doctor --write-agents` 安装），那份讲
> **怎么用 CLI 读一个库** —— 读者是站在库前面的 agent，不是站在仓库里的你。两者内容刻意不重叠。
>
> ⚠️ 面向「怎么跑这个项目」的主文档仍是 [CLAUDE.md](CLAUDE.md)；这里只管 `limo` / `limo-mcp` 这条链。

## 它是什么

只读命令行：`status / list / search / get / export / folders / doctor`。
**没有写操作**（不做 import / generate / edit / delete）—— 成本、并发、幂等、
agent 误触发的风险都在写侧的账上，那份账还没算。

### 文件夹是一棵树

`folders` 返回的每项是 `{id, name, parentId, count, totalCount}`：`parentId` 为 `""`
表示根级；`count` 是**直接**挂在该夹下的篇数，`totalCount` 是**含全部子文件夹**的篇数。
两个数都保留，是因为「这个夹自己有多少」与「这棵子树多大」是两个问题（模式 #54：
消费方要知道自己拿的是哪一个）。人类可读输出按前序缩进，能直接看出层级。

`limo list --folder-id X` **默认只列直接属于 X 的文档**（与 `count` 同口径）；
要整棵子树加 `--recursive`（透传 `include_descendants=true`）。⚠️ `--recursive`
必须配合 `--folder-id`：单给时它什么都不做，而结果看起来完全正常。

### 搜索是**纯字面**的（语义融合只在 HTTP 侧）

`limo search` 走 `QueryService.search_resources` → FTS5 二字组 OR + BM25，**不接向量**。
这不是遗漏：融合要读向量矩阵（几十到几百毫秒）并编码查询串（一次网络调用），
两件都是异步的，而 `QueryService` 是同步纯读层 —— 它同时被 `LocalSource`
（`doctor --local`，没有事件循环）使用。于是形状上分成两处：

| | 谁做融合 | 形状 |
|---|---|---|
| `GET /api/search`（前端） | `routers/search.py` 的异步编排 | `{hits, semantic}` |
| `GET /api/cli/search`（CLI） | **不做** | 纯字面 hits |

⚠️ 把融合塞进 `QueryService` 会让 `LocalSource` 被迫处理「模型不可用 / 库没建过」，
而那份语义状态在 CLI 的契约里**没有位置**（加了就是给两个传输层同时加一个
`semantic` 字段，等于让 `limo --json` 的输出形状取决于用户开没开一个 UI 开关）。
真要让 CLI 也吃到语义，先改契约、再同时改两个 Source —— 而不是在命令层拼一份。

## 三层结构（改任何一处前先认清自己在哪层）

```
backend/limo_cli/          ① 命令层：参数解析、渲染、退出码
  cli.py                   入口与分发（唯一的退出码收口处）
  commands.py              七个命令的实现（+ ALL_PARTS 等共用常量）
  doctor.py                诊断（本机/后端/凭据/契约探测）
  output.py                stdout/stderr 分流 + 表格渲染
  sources.py               ② 传输层：HttpSource / LocalSource
  agents_md.py             契约文档模板
  exit_codes.py            退出码常量表
  mcp_server.py            ①' MCP 协议层：帧/方法分派/错误码/双栈握手
  mcp_tools.py             ①' MCP 能力层：六工具 schema + handler + _SourceHolder

backend/app/services/query.py   ③ 契约成形层（QueryService）
backend/app/routers/cli.py      HTTP 外壳（薄，只做参数绑定）
backend/app/storage/store.py    数据真相源
```

**纪律**：CLI **不解析库的存储格式**。它不读 `index.json`、不读 `search.db`、
不拼磁盘路径。所有取数经 `QueryService`——少了这条纪律，CLI 就是格式的第 N 个
副本，而副本的漂移是静默的。

`HttpSource`（默认，走 `/api/cli/*`）与 `LocalSource`（仅 `doctor --local`）是
**同一契约的两个实现**，不是两份逻辑：形状由 `QueryService.to_dict()` 定义，
`limo_cli/sources.py` 只负责搬运。新增方法必须两边同时加。

它们有**两个消费者**：`limo` CLI（读者是**人**）与 MCP 服务端（读者是 **agent**）。
两者都不碰 `QueryService` 一行 —— 加一个消费者不等于加一个实现。

## 怎么跑

```bash
cd backend
python -m limo_cli --help            # 未安装入口点时的等价写法
python -m limo_cli status            # 需要后端在跑（否则 exit 4）
python -m limo_cli doctor --local    # 不经过后端，直接开本机库
```

`pip install -e .` 之后可直接用 `limo`。两条入口指向同一个 `main`
（`limo_cli/__main__.py`），不存在第二份实现。

MCP 服务端（stdio，不是给人用的是给 agent 用的）：

```bash
cd backend
python -m limo_cli.mcp_server        # 直开本机库，不需要后端在跑
python -m limo_cli.mcp_server --http # 改走 127.0.0.1:7413（要求 LiMo 在跑）
```

安装版是 `LiMoBackend.exe --mcp`（`main.py` 顶部短路，见下节）。

## MCP 服务端（改它之前先读这四节）

`mcp_server.py`（协议层）与 `mcp_tools.py`（能力层）刻意分家 —— 前者喂 `StringIO`
就能测，后者给个 fake `Source` 就能测；混在一起则测一个工具要先拼 JSON 再起子进程。

### 一、stdout 是协议通道，不是日志通道

stdio 模式下**任何一行意外输出**都会让客户端解析失败、连接直接断开，而本地终端里
只是多了一行字 —— **零报错**。三道防线：

1. `main()` 第一件事：`proto_out = sys.stdout` 抢下来给协议独占，紧接着
   `sys.stdout = sys.stderr` 把其余所有输出改道（`finally` 里还原）。
2. 协议写入**只经 `_write(obj)`** 一个函数（一行 JSON + `\n` + flush）。别处出现
   `sys.stdout` 一律是 bug。
3. `scripts/check-mcp` 第 7 项：故意让工具 `print('NOISE_FROM_TOOL')`，断言 stdout
   **仍然只有 1 行 JSON**、噪音落在 stderr。

⚠️ **`main.py` 的 `--mcp` 短路必须排在它自己的 `sys.stdout is None → devnull`
归一化之前。** windowed 打包下 `sys.stdout` 就是 `None`，归一化会把它换成 devnull
—— 而 MCP 的协议通道**就是** stdout，换掉之后所有响应写进黑洞，客户端只看到
「连不上」，我们这边一切正常。

### 二、`supportedVersions` 只列核实过的版本

`initialize`（legacy）的 `protocolVersion` **原样回显客户端请求值**，而
`server/discover` 的 `supportedVersions` **只列我们真实现的**。两者刻意不是一份东西：
后者写一个没实现的版本号 = 承诺一套没实现的行为，客户端协商到它之后按那套规范发请求，
我们按这套回，**双方都不报错、语义全错**。

双栈本身是必须的：Claude Code 逐服务器协商，新协议失败会自动回退 legacy `initialize`
—— 少一条栈，一半客户端连不上。

### 三、`Store` 快照问题只在直开这一路存在

Claude Code 的 MCP 子进程是常驻的，用户可能中途在 LiMo 里导入新文档。`SearchEngine`
天然新鲜（每次查询新建连接），但 `Store` 把 `index.json` 读进内存 —— 故 `LocalHolder`
每次工具调用前 `stat` 一次 `(mtime, size)`，变了就**重建整个 `LocalSource`**（实测 0.13s）。

⚠️ **不为省那几毫秒去拆 `LocalSource` 的内部结构** —— 拆了就是一个「MCP 版 LocalSource」，
第二份真相源。`--http` 模式用 `StaticHolder`（后端保证新鲜，本地零缓存）。

⚠️ **诚实记录的边界**：`Store.__init__` 不是纯只读，库处于异常状态时四条迁移自检会写盘
（索引自愈重建 / 重复 id 去重 / 分类 sidecar 补写 / 遗留布局迁移）。它们幂等、走
`atomic_write_text`，且正是 LiMo 应用自己启动时也会写的同一批东西 —— 不想让它修，
就先开一次 LiMo，库里没有需要修的东西。

### 四、工具集的形状

六个只读工具：`limo_status` / `limo_list` / `limo_search` / `limo_get` / `limo_read` /
`limo_folders`。要点：

- `limo_read` 的 `part` 合法值 = `mcp_tools.PARTS`，由 `commands.ALL_PARTS` **派生**再
  追加 `commands.RAW_PART`。手抄那六个的失效形态是「`ALL_PARTS` 加了第七种产物而 MCP
  悄悄不认」，两边都不报错。
- `part="raw"` 是**文档原文**（`transcript.md`），走 `get_resource()` 而非 `get_artifact()`
  —— 它不是产物（无版本、无自证头），切窗由 MCP 层自己做，但 `truncated` / `continuation`
  的形状与 `QueryService._build` 逐一对应。没有它，刚导入、还没生成过笔记的文档
  agent 一个字都读不到。
- `limo_get` **必须摘掉 `rawText`**（只留长度 + 300 字预览）：一篇长视频转录几十万字符，
  透传等于一次工具调用灌满上下文。
- `limo_read` 的 `max_chars` **必须显式给默认值 20000** —— 传 `None` 在 `LocalSource`
  里的语义是「用后端默认上限（256K）」，不是「不截断」。
- 搜索**只走字面**（`QueryService.search_resources`）。不接向量 / 改写 / 重排：MCP 进程
  随客户端起停，加载 22.8MB embedding 引擎或每次查询花一次云端计费，对这个场景都不划算。

⚠️ **错误分两层**：文档不存在 / 缺产物 / 索引不可用 → `isError: true`（模型看得见原因
并能自我纠正）；未知方法 / 参数不是对象 / JSON 坏 → JSON-RPC `error`。放错层的后果是
「模型看到『出错了』但拿不到任何可行动的线索」。`CLI_NO_LOCAL_INDEX` 的 hint 在 MCP 层
**改写**（`_HINT_OVERRIDES`），不改 `sources.py` —— CLI 那份文案对 CLI 是对的。

## 门禁

```bash
bash scripts/check-cli          # 16 项，自启独立端口 7419 的后端
bash scripts/check-cli 9        # 只跑第 9 项（调试用）

bash scripts/check-mcp          # 8 项，起真子进程；**不需要后端在跑**（默认 LocalSource）
bash scripts/check-mcp 7        # 只跑第 7 项（stdout 纯净性，调试用）

bash scripts/check-all cli      # 同上（门禁总入口的 cli 层，不重复实现）
bash scripts/check-all mcp      # 同上（mcp 层）
bash scripts/check-all          # 全套八层，约 60s
```

⚠️ **`check-mcp` 与 `check-cli` 的结构差别是「不占端口、不启后端」**：它验证的场景是
「Claude Code 冷不丁拉起这个进程」，而那时 LiMo 应用本来就是关着的。它多出来的、也是
唯一值得它单独存在的变量是 **stdout 是否真的干净** —— 那是 `pytest` 层用 `StringIO`
看不见的（协议通道被污染时客户端直接断开，本地零报错）。

⚠️ **它用独立端口（7419）而不是 7413**，因为第 3 项要验证「后端不可达 → exit 4」
——借用户正在跑的 7413 会让这一项**永远绿，而它绿的原因是错的**。

⚠️ **每条判据同时查三样**：退出码、结构化标记（错误 `code` / `truncated.continuation`
/ `next_cursor`）、以及「是不是假成功」。只查退出码会漏掉「命令失败但退 0」——
那正是这套契约要消灭的形态。

**加一项门禁时**：必须同时能判**红**。一条永远绿（或永远跳过）的检查与没有它等价
——第 15 项曾长期「跳过」（列表第一篇没有产物），改法是现找一篇真有产物的文档。

## 改 CLI 时的六条硬约束

1. **退出码只在 `cli.py::main` 收口**。命令实现里既不 `sys.exit()` 也不漏异常——
   漏出去 Python 退 **1**，而 `1` 不在契约表里，调用方无法判断发生了什么。
2. **`--json` 时 stdout 只有 JSON**，诊断/错误全走 stderr。**不用 TTY 检测**
   （管道里跑是最常见的用法，检测会把脚本场景判成非交互）。错误也是 JSON。
3. **退出码判 `code` 不判 HTTP 状态**。`main.py` 对 `recoverable=True` 且未显式传
   status 的 `AppError` 返回 **HTTP 200** → 按状态判会把「找不到」读成成功，
   然后解析出一个错误对象当数据用。
4. **args 默认值用 `argparse.SUPPRESS`**（`--json` / `--base-url` / `--timeout`）。
   子命令与主 parser 写进**同一个 namespace**，`default=False` 会覆盖主 parser
   刚设好的值 → `limo --json list` 静默退化成人类可读输出。
5. **路径段标识符必须客户端预校验**（`sources.py::require_ident`）。`get ../../etc/passwd`
   的 `/` 被 URL 编码后又被 Starlette 解码回 `/` 用于路由匹配 → 落到「无此路由」
   → 404 → `exit 3`，而非 `exit 5`。同一类坏输入因字符集不同得到两种退出码。
6. **不在 CLI 里复刻后端的默认值**（如 `max_chars` 的上限）。副本会漂移：后端调大
   上限时 CLI 仍传旧值，上限被悄悄按老的执行，而两边代码看起来都对。
   ⚠️ 与 MCP 的 `DEFAULT_MAX_CHARS = 20000` **不矛盾**：那是**工具侧的产品决策**
   （工具的读者是模型，一次 20000 字符是上下文预算，不是「后端上限」），不是把后端
   数值抄过来当下限用。

## `--all` 为什么在两个 Source 里写法不同

| | 表达「一次取全」 |
|---|---|
| `LocalSource` | `limit=None`（`QueryService` 的语义） |
| `HttpSource` | `all=true` 且**不传** `limit`（后端默认 50，不是「全部」） |

曾把两者归一成 `limit=None` → HTTP 路径静默退化成「第一页 50 条」（一个少拿了
26 篇却看起来完全正常的成功）。第 9 项门禁抓到的就是它。**判据**：`Page` 的
`has_more`/`next_cursor` 与 `truncated`/`continuation` 是**两回事**，前者能继续翻、
后者到头了但没给完；把后者当分页循环会空转。

## 契约文档

`limo doctor --write-agents` 把 `agents_md.py` 的模板渲染进 `~/.limo/AGENTS.md`
（替换 `{{DATA_DIR}}`）。**默认不覆盖**：已存在且内容不同 → `exit 2` 报错，
`--force` 才覆盖且先备份为 `AGENTS.md.bak`。改动模板后跑一次验证。
