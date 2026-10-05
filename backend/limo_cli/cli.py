"""`limo` 入口 —— 参数解析、用法校验、分发、退出码收口。

**分发层只做三件事**：把参数变成 `args`、把 `args` 变成 `Source`、把异常变成退出码。
其余（取数、渲染）在 `commands.py` / `doctor.py`。

⚠️ **退出码收口在这里**（唯一一处），所以命令实现里既不该 `sys.exit()` 也不该
把异常漏出去 —— 漏出去的结果是 Python 打印 traceback 并退 **1**，而 `1` 不在本
CLI 的契约表里，调用方无从判断发生了什么（见 `exit_codes.py`）。
"""

import argparse
import sys
import traceback
from pathlib import Path

from limo_cli import __version__
from limo_cli import commands, doctor
from limo_cli import exit_codes as EXIT
from limo_cli.output import Out
from limo_cli.sources import DEFAULT_BASE_URL, HttpSource, LocalSource, SourceError

_PART_HELP = "/".join(commands.ALL_PARTS)


def build_parser() -> argparse.ArgumentParser:
    # ⚠️ 子命令继承 `common`（`--json` 用 `SUPPRESS` 默认值），主 parser 自己再加
    # 一个 `--json`。这不是重复：argparse 把子命令的结果写进**同一个 namespace**，
    # 子命令若带 `default=False`，会**覆盖**主 parser 刚设好的 `--json` ——
    # `limo --json list` 于是静默退化成人类可读输出（而 stdout 上就多出了非 JSON）。
    # `SUPPRESS` 的语义是「没给这个参数就什么都不写」，主 parser 的值因此得以保留。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="stdout 只输出 JSON（诊断信息仍走 stderr）")
    # ⚠️ `--base-url` / `--timeout` 也必须在**子命令上**可用：`limo status --base-url X`
    # 是人的自然写法（选项跟着它修饰的命令），只挂在主 parser 上会得到
    # `error: unrecognized arguments` —— 一个把「位置放错了」说成「参数不存在」的
    # 报错，用户会以为这个选项根本不支持。两处都注册（同 SUPPRESS 理由，见上）。
    common.add_argument("--base-url", dest="base_url", default=argparse.SUPPRESS,
                        help="后端地址（覆盖 $LIMO_BASE_URL）")
    common.add_argument("--timeout", type=float, default=argparse.SUPPRESS,
                        help="HTTP 超时秒数")

    p = argparse.ArgumentParser(
        prog="limo",
        description="LiMo 知识库只读命令行 —— 读文档、正文、生成产物与全文搜索。",
        epilog=(
            "退出码：0 成功（含空结果）｜2 用法错误｜3 未找到｜4 后端不可达｜"
            "5 数据或响应异常｜6 内部错误\n"
            "首次使用先跑 `limo doctor`。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--json", action="store_true", help="stdout 只输出 JSON")
    p.add_argument("--version", action="version", version=f"limo {__version__}")
    p.add_argument("--base-url", dest="base_url", default=None,
                   help=f"后端地址（默认 {DEFAULT_BASE_URL}，或 $LIMO_BASE_URL）")
    p.add_argument("--timeout", type=float, default=None, help="HTTP 超时秒数（默认 15）")

    sub = p.add_subparsers(dest="command", metavar="<命令>")

    sp = sub.add_parser("status", parents=[common], help="库概览（计数 / 类型分布）")
    sp.set_defaults(func=commands.cmd_status)

    sp = sub.add_parser("list", parents=[common], help="列出文档（分页）")
    sp.add_argument("--type", dest="source_type", default="",
                    help="按来源类型筛（video / webpage / pdf / audio / composite …）")
    sp.add_argument("--tag", default="", help="按标签筛")
    sp.add_argument("--folder-id", dest="folder_id", default=None,
                    help="按文件夹筛（ID 见 `limo folders`）")
    sp.add_argument("--uncategorized", action="store_true", help="只看未分类")
    sp.add_argument("--query", "-q", default="", help="按标题关键词筛（不搜正文）")
    sp.add_argument("--limit", type=int, default=50, help="本页条数（默认 50）")
    sp.add_argument("--all", dest="all_pages", action="store_true", help="一次取全（忽略 --limit）")
    sp.add_argument("--cursor", default=None, help="上一次响应的 next_cursor")
    sp.set_defaults(func=commands.cmd_list)

    sp = sub.add_parser("search", parents=[common], help="全文搜索（FTS5）")
    sp.add_argument("query", metavar="关键词")
    sp.add_argument("--limit", type=int, default=50, help="最多返回条数（默认 50）")
    sp.set_defaults(func=commands.cmd_search)

    sp = sub.add_parser("get", parents=[common], help="取一篇文档的元数据，或某一产物正文")
    sp.add_argument("doc_id", metavar="ID")
    sp.add_argument("--part", default="", help=f"取该产物正文（{_PART_HELP}）")
    sp.add_argument("--version", type=int, default=None, help="产物版本（默认最新）")
    sp.add_argument("--offset", type=int, default=0, help="从第 N 个字符开始（续读）")
    sp.add_argument("--max-chars", dest="max_chars", type=int, default=None,
                    help="本次最多返回的字符数（默认由后端定）")
    sp.add_argument("--full", action="store_true", help="不截断")
    sp.set_defaults(func=commands.cmd_get)

    sp = sub.add_parser("export", parents=[common],
                        help="把内容原样取出（stdout 或文件）")
    sp.add_argument("doc_id", metavar="ID")
    sp.add_argument("--part", required=True, help=f"{_PART_HELP} / {commands.RAW_PART}")
    sp.add_argument("--version", type=int, default=None)
    sp.add_argument("--format", choices=["md", "json"], default="md",
                    help="md（原样内容，默认）/ json（含 meta 的完整对象）")
    sp.add_argument("-o", "--output", default="", help="写入文件（默认打印到 stdout）")
    sp.add_argument("--full", action="store_true", help="不截断")
    sp.add_argument("--with-meta", dest="with_meta", action="store_true",
                    help="连自证头（<!-- limo-meta -->）一起取")
    sp.set_defaults(func=commands.cmd_export)

    sp = sub.add_parser("folders", parents=[common], help="文件夹清单")
    sp.set_defaults(func=commands.cmd_folders)

    sp = sub.add_parser("doctor", parents=[common], help="环境与连通性诊断")
    sp.add_argument("--local", action="store_true",
                    help="不经过后端，直接打开本机库（仅本命令支持）")
    sp.add_argument("--write-agents", dest="write_agents", action="store_true",
                    help="把 AGENTS.md 安装到数据目录（供 agent 读取）")
    sp.add_argument("--force", action="store_true", help="覆盖已存在的 AGENTS.md（先备份）")
    sp.set_defaults(func=doctor.cmd_doctor)

    return p


def _validate(args) -> str:
    """用法校验 —— 返回错误文案（空串 = 通过）。

    ⚠️ 这里只拦「命令写错了」，**不拦业务结果**：`limo search <不存在的词>` 返回 0 条
    是成功（exit 0），只有 `limo search ""` 被拦下 —— 空关键词的结果（0 条）会被读成
    「库里没有相关内容」，那是**一个错误的结论**而不是一个空结果。判据是
    「这个输入的结果会不会被误读成事实」，不是「这个输入看起来正不正常」。
    """
    if args.command == "list":
        if args.uncategorized and args.folder_id is not None:
            return "--uncategorized 与 --folder-id 只能用一个"
        if args.limit < 1:
            return "--limit 必须 ≥ 1（要一次取全用 --all）"
        if args.all_pages and args.cursor:
            return "--all 与 --cursor 互相矛盾（前者要一次取全，后者从游标继续）"
    if args.command == "search":
        if not (args.query or "").strip():
            return "搜索关键词不能为空"
        if args.limit < 1:
            return "--limit 必须 ≥ 1"
    if args.command == "get":
        if args.offset < 0:
            return "--offset 不能为负"
        if args.max_chars is not None and args.max_chars < 1:
            return "--max-chars 必须 ≥ 1（要全文用 --full）"
    if args.command == "export":
        if args.part not in commands.ALL_PARTS and args.part != commands.RAW_PART:
            return (f"未知产物 {args.part!r}（可选："
                    f"{', '.join(commands.ALL_PARTS + (commands.RAW_PART,))}）")
        if args.format == "json" and args.output and args.with_meta:
            return "--with-meta 只对 --format md 有意义"
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT.USAGE

    out = Out(bool(getattr(args, "json", False)))

    problem = _validate(args)
    if problem:
        out.error(problem, code="CLI_USAGE",
                  hint=f"limo {args.command} --help")
        return EXIT.USAGE

    if args.command == "doctor" and args.write_agents:
        # ⚠️ 单独包一层，**不复用下面那个 try**：那个 except 的文案是「无法打开本机
        # 库」，而这一支根本不碰数据源——文件系统写失败（权限/占用/磁盘满）被套上
        # 那句，用户会去查库路径，而问题在目标目录。未捕获时 traceback 裸奔 + 退出码
        # 1（= USAGE，语义还是错的）。
        try:
            return _write_agents(args, out)
        except Exception as e:
            traceback.print_exc(file=sys.stderr)
            out.error("安装 AGENTS.md 失败", code="CLI_INTERNAL",
                      detail=f"{type(e).__name__}: {e}",
                      hint="请确认数据目录可写；也可手动把 AGENTS.md 放到该目录")
            return EXIT.INTERNAL

    try:
        # ⚠️ `--local` 只有 doctor 认（其他命令的 parser 里根本没这个参数，argparse
        # 会以 exit 2 拒掉）——这条分支只为 doctor 存在。
        src = LocalSource() if getattr(args, "local", False) else HttpSource(
            base_url=args.base_url, timeout=args.timeout)
    except Exception as e:
        out.error("无法打开本机库", code="CLI_LOCAL_OPEN_FAILED",
                  detail=f"{type(e).__name__}: {e}",
                  hint="`limo doctor` 可看到数据目录与配置的实际状态")
        return EXIT.INTERNAL

    try:
        return args.func(src, out, args)
    except SourceError as e:
        out.error(e.message, code=e.code, detail=e.detail, hint=e.hint)
        return e.exit_code
    except KeyboardInterrupt:
        out.note("已中断")
        return EXIT.INTERRUPTED
    except Exception as e:
        # 未预期异常：traceback 进 stderr（不是 stdout），退出码 6
        traceback.print_exc(file=sys.stderr)
        out.error("内部错误", code="CLI_INTERNAL",
                  detail=f"{type(e).__name__}: {e}",
                  hint="这是 CLI 的缺陷，请附上上面的 traceback 反馈")
        return EXIT.INTERNAL


def _write_agents(args, out) -> int:
    """把 `AGENTS.md` 安装到数据目录 —— 库内那份（agent 打开库先读到）。

    ⚠️ **默认不覆盖**：这个文件可能被人改过（补了本库特有的说明），而「静默盖掉
    用户改过的文件」是不可逆的。已存在且内容不同 → 报错；`--force` 才覆盖，且先
    备份成 `AGENTS.md.bak`。
    """
    from limo_cli.agents_md import AGENTS_FILENAME, render_agents_md
    try:
        from app.core.config import load_config
        data_dir = Path(load_config().get("data_dir") or (Path.home() / ".limo"))
    except Exception as e:
        out.error("读不到配置，无法确定数据目录", code="CLI_CONFIG_UNREADABLE",
                  detail=str(e)[:200])
        return EXIT.BAD_DATA

    path = data_dir / AGENTS_FILENAME
    content = render_agents_md(data_dir)
    if path.exists():
        old = path.read_text(encoding="utf-8")
        if old == content:
            out.note(f"{path} 已是最新，未改动")
            return EXIT.OK
        if not args.force:
            out.error(f"{path} 已存在且内容不同", code="CLI_AGENTS_EXISTS",
                      hint="确认要覆盖就加 --force（会先备份为 AGENTS.md.bak）")
            return EXIT.USAGE
        backup = path.parent / (path.name + ".bak")
        backup.write_text(old, encoding="utf-8")
        out.warn(f"原文件已备份为 {backup}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as e:
        out.error("写入失败", code="CLI_AGENTS_WRITE_FAILED", detail=str(e))
        return EXIT.INTERNAL
    out.note(f"已写入 {path}")
    return EXIT.OK
