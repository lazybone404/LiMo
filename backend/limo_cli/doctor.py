"""`limo doctor` —— 一次性回答「为什么这条命令不work」。

诊断的价值不在「列出一堆检查项」，而在**让失败自己说清下一步**：每一项失败都带
`hint`（用户能做的动作），且退出码取自**第一个真失败项**——脚本据此分支。

⚠️ **doctor 只读**。它读配置、看文件在不在、打一次后端往返，**不改任何既有内容**。
两处例外要在代码里说清楚（见 `_probe_local`）：`load_config()` 会刷新
`config.yaml.bak`（内容与 config.yaml 逐字节相同），`LocalSource` 的 `Store` 会
`mkdir` 数据目录。都是「确保存在」，没有一处覆盖或删除 —— 诊断工具顺手改用户数据
是最不能接受的一类副作用。
"""

from dataclasses import dataclass
from pathlib import Path

from limo_cli import __version__
from limo_cli.output import pad
from limo_cli.sources import EXPECTED_SCHEMA, SourceError
from limo_cli import exit_codes as EXIT


@dataclass
class Check:
    """一项检查。`level` 三态：`ok` / `warn`（能跑但不理想）/ `fail`（跑不了）。"""

    name: str
    level: str = "ok"
    detail: str = ""
    hint: str = ""
    exit_code: int = EXIT.INTERNAL


def cmd_doctor(src, out, args) -> int:
    checks: list[Check] = [
        Check("CLI", detail=f"limo {__version__} · 期望契约 schema {EXPECTED_SCHEMA}"),
    ]
    checks.append(_probe_local())
    checks.append(_probe_credentials())
    checks.extend(_probe_source(src, args))

    ok = not any(c.level == "fail" for c in checks)
    payload = {
        "schema_version": EXPECTED_SCHEMA,
        "ok": ok,
        "checks": [{"name": c.name, "level": c.level, "detail": c.detail,
                    "hint": c.hint} for c in checks],
    }
    out.data(payload, render=_render)
    for c in checks:
        if c.level == "fail":
            return c.exit_code
    return EXIT.OK


# ── 各项探测 ──

def _probe_local() -> Check:
    """配置文件 + 数据目录 + 两个索引文件。**只断言存在性，不解析内容**。

    ⚠️ 刻意不数「有多少篇文档」：那要读 `index.json` 并理解它的结构，而「CLI 不
    解析库的存储格式」是这套设计的根（`limo_cli.__init__` 的 docstring）。文档数
    由 `status` 那条来（它走后端契约）——同一个数字只有一个来源。
    """
    try:
        from app.core.config import load_config
        cfg = load_config()
    except Exception as e:  # 配置读取失败是「CLI 装不起来」，不是库的问题
        return Check("配置", level="fail", detail=str(e)[:200],
                     hint="检查 ~/.limo/config.yaml 是否存在且可读",
                     exit_code=EXIT.BAD_DATA)

    data_dir = Path(cfg.get("data_dir") or (Path.home() / ".limo"))
    missing = [n for n, p in (("数据目录", data_dir),
                              ("index.json", data_dir / "index.json")) if not p.exists()]
    if missing:
        return Check("本机库", level="fail", detail=f"缺少 {'、'.join(missing)}",
                     hint=f"数据目录：{data_dir}。若从未用过 LiMo，先启动一次应用",
                     exit_code=EXIT.BAD_DATA)
    has_fts = (data_dir / "search.db").exists()
    detail = f"{data_dir}"
    if not has_fts:
        return Check("本机库", level="warn", detail=f"{detail}（全文索引未构建）",
                     hint="启动一次 LiMo 应用即可建立 search.db；此前 `limo search` 不可用")
    return Check("本机库", detail=detail)


def _probe_credentials() -> Check:
    """凭据卫生自检（二期）—— `.limo` 里不该有明文 key。

    ⚠️ 这是**卫生**不是安全边界（DPAPI 同账户无隔离），故明文只报 `warn` 而不是
    `fail`：应用照常能用，只是不该这样。
    """
    try:
        from app.core.config import load_config
        from app.core.config_io import startup_check
        problems = startup_check(load_config())
    except Exception as e:
        return Check("凭据", level="warn", detail=f"自检未跑成：{str(e)[:120]}")
    if problems:
        return Check("凭据", level="warn", detail=f"{len(problems)} 处明文凭据",
                     hint=problems[0] + "；打开设置页点一次「保存」即可迁移到加密存储")
    return Check("凭据", detail="配置内无明文凭据")


def _probe_source(src, args) -> list[Check]:
    """数据源往返 —— 后端可达性 / 契约版本 / 库概览。

    这一条同时也验证了 `schema_version` 协商：`HttpSource._check_schema` 对**每个**
    响应都跑，版本不匹配时 `status()` 自己就会抛 `CLI_SCHEMA_MISMATCH`，doctor 如实
    转述并给出退出码 —— 不然「后端是三个月前的打包版」只表现为一堆字段读出来是空的。
    """
    label = "本地直读（--local）" if getattr(args, "local", False) else "后端"
    try:
        st = src.status()
    except SourceError as e:
        return [Check(label, level="fail", detail=e.message + (f" — {e.detail}" if e.detail else ""),
                      hint=e.hint, exit_code=e.exit_code)]
    except Exception as e:
        return [Check(label, level="fail", detail=f"{type(e).__name__}: {str(e)[:200]}",
                      hint="这是 CLI 未预期的错误，请附上完整输出反馈",
                      exit_code=EXIT.INTERNAL)]

    checks = [Check(label, detail=f"契约 schema {st.get('schema_version')} · "
                                  f"{st.get('docCount', 0)} 篇文档")]
    if st.get("docCount") == 0:
        checks.append(Check("库内容", level="warn", detail="库里还没有文档",
                            hint="用 LiMo 应用导入内容（CLI 第一版只读）"))
    elif not (st.get("bySourceType") or {}):
        checks.append(Check("库内容", level="warn", detail="文档均未标注来源类型"))
    return checks


# ── 渲染 ──

_MARK = {"ok": "✓", "warn": "!", "fail": "✗"}


def _render(d: dict) -> str:
    checks = d.get("checks") or []
    width = max((len(c["name"]) for c in checks), default=4)
    lines = [f"LiMo doctor  ·  契约 schema {d.get('schema_version')}", ""]
    for c in checks:
        lines.append(f"  {_MARK.get(c['level'], '?')} {pad(c['name'], width)}  {c['detail']}")
        if c.get("hint") and c["level"] != "ok":
            lines.append(f"    {pad('', width)}  → {c['hint']}")
    lines += ["", "结论：一切正常。" if d.get("ok") else "结论：有项目未通过（见上面 ✗）。"]
    return "\n".join(lines)
