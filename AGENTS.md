# CLI 这条链 —— 给在**本仓库**里干活的 agent

> 这份文档讲**怎么改 CLI**。它的兄弟版是 `~/.limo/AGENTS.md`（由 `limo doctor
> --write-agents` 安装），那份讲**怎么用 CLI 读一个库** —— 读者是站在库前面的 agent，
> 不是站在仓库里的你。两者内容刻意不重叠。
>
> ⚠️ 面向「怎么跑这个项目」的主文档仍是 [CLAUDE.md](CLAUDE.md)；这里只管 `limo` 这条链。

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

## 三层结构（改任何一处前先认清自己在哪层）

```
backend/limo_cli/          ① 命令层：参数解析、渲染、退出码
  cli.py                   入口与分发（唯一的退出码收口处）
  commands.py              七个命令的实现
  doctor.py                诊断（本机/后端/凭据/契约探测）
  output.py                stdout/stderr 分流 + 表格渲染
  sources.py               ② 传输层：HttpSource / LocalSource
  agents_md.py             契约文档模板
  exit_codes.py            退出码常量表

backend/app/services/query.py   ③ 契约成形层（QueryService）
backend/app/routers/cli.py      HTTP 外壳（薄，只做参数绑定）
backend/app/storage/store.py    数据真相源
```

**纪律**：CLI **不解析库的存储格式**。它不读 `index.json`、不读 `search.db`、
不拼磁盘路径。所有取数经 `QueryService`——少了这条纪律，CLI 就是格式的第 N 个
副本，而副本的漂移是静默的。

`HttpSource`（默认，走 `/api/cli/*`）与 `LocalSource`（仅 `doctor --local`）是
**同一契约的两个传输层**，不是两个实现：形状由 `QueryService.to_dict()` 定义，
`limo_cli/sources.py` 只负责搬运。新增方法必须两边同时加。

## 怎么跑

```bash
cd backend
python -m limo_cli --help            # 未安装入口点时的等价写法
python -m limo_cli status            # 需要后端在跑（否则 exit 4）
python -m limo_cli doctor --local    # 不经过后端，直接开本机库
```

`pip install -e .` 之后可直接用 `limo`。两条入口指向同一个 `main`
（`limo_cli/__main__.py`），不存在第二份实现。

## 门禁

```bash
bash scripts/check-cli          # 16 项，自启独立端口 7419 的后端
bash scripts/check-cli 9        # 只跑第 9 项（调试用）

bash scripts/check-all cli      # 同上（门禁总入口的 cli 层，不重复实现）
bash scripts/check-all          # 全套六项（前端 + 后端 + 本项 + 文档引用），约 60s
```

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
