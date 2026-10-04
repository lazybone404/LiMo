"""契约文档 `AGENTS.md` —— 库内那份的模板与安装。

**为什么要有这份文件**：agent 打开一个库目录时，第一件想问的事是「这里有什么、
我能做什么」。没有这份文档，它只能靠试错（读 `.limo` 内部格式 → 那是实现细节，
也是本 CLI 存在的理由）。

⚠️ **库内那份为主**（`~/.limo/AGENTS.md`）：agent 打开的是**库**不是**仓库**，
所以内容不依赖仓库路径（`{{DATA_DIR}}` 在渲染时填成实际数据目录）。

⚠️ **usage 放最前**：这份文档的读者是「已经站在库前面、准备干活」的 agent，
它需要的顺序是「能做什么 → 返回什么 → 怎么省事 → 哪条花钱 → 最后才是贡献与开发」。
把「项目介绍/开发指南」写在前面，等于让每个读者先翻过三屏与他无关的东西。
"""

# ⚠️ 占位符用 `{{NAME}}` 而非 `str.format`：文档里必然出现 `{}`（JSON 示例、
# 命令语法），`format()` 会把它们当成待填字段，于是模板一改就炸在最不相干的地方。
AGENTS_MD = """# LiMo 知识库 —— 给 agent 的使用说明

> 你正站在一个 LiMo 库上（数据目录：`{{DATA_DIR}}`）。
> 下面这些命令能读到库里的**全部内容**：文档清单、正文、生成产物、全文搜索。
> **只读**：当前版本没有导入 / 生成 / 编辑 / 删除命令。

## 命令

```bash
limo status                       # 库里有什么：计数 + 类型分布
limo list --limit 20              # 最近的文档
limo list --type video --tag AI   # 按来源类型 / 标签筛
limo search 关键词                 # 全文搜索（FTS，命中带原文摘要）
limo get <docId>                  # 一篇的元数据 + **有哪些产物**
limo get <docId> --part notes     # 取它的笔记正文
limo export <docId> --part notes -o notes.md   # 取出来落成文件
limo folders                      # 文件夹清单
limo doctor                       # 出问题时先跑这个
```

开发环境未安装入口点时，等价写法：`python -m limo_cli <命令>`。

## 输出契约

- **`--json`：stdout 上只有 JSON**，诊断与错误一律进 stderr。可直接 `| jq`。
  错误也是 JSON（在 stderr）：`{"error": {"code", "message", "detail", "hint"}}`。
- 每个响应顶层都有 **`schema_version`**；主版本与你期望的不一致时 CLI 会**直接失败**
  而不是按老字段读（读出来会是「结构完整、值全错」）。
- **退出码**（这是给脚本用的主要判据 —— 「空」与「坏」刻意分开）：

  | 码 | 含义 | 你该做什么 |
  |---|---|---|
  | 0 | 成功（**含空结果**） | 搜到 0 条也是成功，换个关键词即可 |
  | 2 | 用法错误 | 参数写错了，改命令而不是重试 |
  | 3 | 未找到 | ID / 产物不存在，别重试同一个 |
  | 4 | 后端不可达 | 需要先启动 LiMo 应用（或 `--local` 走本地库） |
  | 5 | 数据或响应异常 | 坏 ID、坏游标、版本不匹配 —— 重试无用 |
  | 6 | 内部错误 | 未预期异常，stderr 里有 traceback |

## 怎么用最少的工作拿到最多数据

1. **先 `limo status`** —— 一次调用知道库里有多少文档、都是什么类型，再决定要不要翻。
2. **产物要先问再取**：`limo get <docId>` 返回的 `artifacts` 数组列出这篇**实际有什么**
   （`notes` / `polished` / `extract` / `table` / `digitize` / `understand`）。
   不要挨个试 `--part` —— 那要用 `exit 3` 反推一个元数据问题。
3. **默认都有限额，且不静默截断**：
   - 列表分页给 `has_more` + `next_cursor`（**能继续翻**），原样回传 `--cursor` 即可；
     或 `--all` 一次取全。
   - 产物正文默认截断，给 `truncated` + `continuation.next_offset`（**到头了但没给完**，
     用 `--offset` 续读或 `--full` 取全）。
   两者是**两回事**：把后者当成分页去循环，会空转到天荒地老。
4. **正文的两种取法**：`limo get <id> --json` 里的 `rawText` 是导入时的原文（含
   Markdown、可能很长）；`--part notes` 是模型加工过的笔记（通常更适合直接读）。
5. **`rawText` 是导入原文的真相源，但不总是模型看到的真相源**：PDF 直传模式下
   模型读的是原件，而本地解析出的 `rawText` 可能近乎为空（扫描件）。看产物就够了。

## 产物自带出处头

`notes` / `polished` / 图片处理产物的正文首行是：

```
<!-- limo-meta {"v":1,"kind":"artifact","source":{...},"generated":{...}} -->
```

它回答「这份东西从哪来、怎么来的、是什么版本」。**`limo` 默认给你的 `content`
已经剥掉它**；要连载体一起拿用 `--with-meta`（导出成文件时有用）。**无头不等于
损坏**（历史产物本来就没有），此时 `metaCorrupt` 为 `false` 而不是报错。

## 花钱的操作

**当前没有。** 上面全部命令都是本地或本机 HTTP 读取，零 token。

将来若加入 `generate` 系列（调用大模型生成新产物），那会**烧 token**并可能排队，
届时会在本节显式标注，并给免费替代（先 `limo get --part notes` 看看有没有现成的）。

## 本机库与后端的关系

- 默认经 `http://127.0.0.1:7413/api/cli/*` 读（应用正在跑的那个库）。
  可用 `LIMO_BASE_URL` 指向别的端口。
- `limo doctor --local` **不经过后端**，由 CLI 进程直接打开本机库 —— 后端没起来时
  用它诊断。它**只读**，不会改库。
- 本机路径与配置可用 `limo doctor` 查看。
"""


def render_agents_md(data_dir) -> str:
    return AGENTS_MD.replace("{{DATA_DIR}}", str(data_dir))


# 写入位置的判断规则：`AGENTS.md` 是给「读这个目录的 agent」的，所以它属于
# **数据目录**（用户库）而不是仓库。仓库根另有一份，由开发者维护。
AGENTS_FILENAME = "AGENTS.md"
