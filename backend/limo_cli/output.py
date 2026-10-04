"""输出契约 —— stdout 只放数据，其余一律 stderr。

**`--json` 模式的硬约定**（doc2 §4）：`stdout` 上**只有** JSON，任何别的字节都是
违规。故本模块把「数据」与「诊断」分开两条流：

| | 去哪 | 内容 |
|---|---|---|
| `data()` | **stdout** | 命令的结果（JSON 或人类可读） |
| `note()` / `warn()` / `error()` | **stderr** | 进度、提示、错误 |

⚠️ **不能用 TTY 检测代替 `--json`**：`limo list --json | jq` 里 stdout 是管道（非
TTY），但 `limo list --json > f.json` 里它也是文件；反过来 `limo list` 在 CI 里
stdout 同样不是 TTY —— 而那时用户要的是**表格**。显示形态是调用方显式选的，不是
从终端类型猜的。

⚠️ `--json` 下**错误也是 JSON**（进 stderr）：调用方拿到的是结构化对象而不是一句
人话，才能按 `code` 分支。人话版同时保留，只不过只有人类会看。
"""

import json
import sys
import unicodedata


class Out:
    """一次调用的输出出口。构造很便宜，命令里随手 new 一个即可。"""

    def __init__(self, json_mode: bool = False) -> None:
        self.json_mode = json_mode

    # ── 数据（stdout，唯一）──

    def data(self, payload, render=None) -> None:
        """打印命令结果。`render` 是人类可读模式的渲染函数（`--json` 下不用）。"""
        if self.json_mode:
            _write(sys.stdout, json.dumps(payload, ensure_ascii=False, indent=2))
        elif render is not None:
            text = render(payload)
            if text:
                _write(sys.stdout, text)

    # ── 诊断（stderr）──

    def note(self, message: str) -> None:
        _write(sys.stderr, message)

    def warn(self, message: str) -> None:
        _write(sys.stderr, f"警告：{message}")

    def error(self, message: str, *, code: str = "", detail: str = "",
              hint: str = "") -> None:
        """报错。`--json` 下是结构化对象 —— 调用方按 `code` 分支，而不是正则人话。"""
        if self.json_mode:
            _write(sys.stderr, json.dumps({
                "error": {"code": code or "UNKNOWN", "message": message,
                          "detail": detail, "hint": hint},
            }, ensure_ascii=False, indent=2))
            return
        lines = [f"✗ {message}"]
        if detail:
            lines.append(f"  {detail}")
        if hint:
            lines.append(f"  提示：{hint}")
        _write(sys.stderr, "\n".join(lines))


def _write(stream, text: str) -> None:
    """写一行。⚠️ **永不因输出失败而抛异常**（`limo list | head -1` 会提前关掉管道，
    此后的 write 触发 BrokenPipeError）——那时命令其实已经成功了，不该报错退出。
    """
    try:
        stream.write(text if text.endswith("\n") else text + "\n")
        stream.flush()
    except (BrokenPipeError, OSError):
        pass


# ── 人类可读渲染的小工具 ───────────────────────────────────────────

def display_width(text: str) -> int:
    """字符串的**显示**宽度：CJK 与全角字符占 2 列。

    ⚠️ 用 `len()` 对齐在这份数据上必然歪 —— 文档标题大量是中文，而
    `len("机器学习") == 4` 会按 4 列排版、实际占 8 列。表格在中文下错位不是美观
    问题：列一歪，人眼会把 A 行的值读到 B 行的列名上。
    """
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def pad(text: str, width: int, *, align: str = "left") -> str:
    """按**显示宽度**补空格到 `width`（超出则原样返回，不截断）。"""
    gap = max(0, width - display_width(text))
    return text + " " * gap if align == "left" else " " * gap + text


def table(rows: list[list[str]], headers: list[str], *, gap: str = "  ") -> str:
    """按列宽对齐的表格（最后一列不补空格，避免行尾空白）。"""
    if not rows:
        return ""
    cols = len(headers)
    widths = [display_width(h) for h in headers]
    for row in rows:
        for i in range(cols):
            widths[i] = max(widths[i], display_width(str(row[i]) if i < len(row) else ""))

    def line(cells: list[str]) -> str:
        parts = [pad(str(cells[i]), widths[i]) for i in range(cols - 1)]
        parts.append(str(cells[cols - 1]) if cols - 1 < len(cells) else "")
        return gap.join(parts).rstrip()

    out = [line(headers), gap.join("─" * w for w in widths)]
    out += [line([str(c) for c in row]) for row in rows]
    return "\n".join(out)


def clip(text: str, limit: int) -> str:
    """单行摘要截断（用于表格里的标题列）。"""
    text = " ".join((text or "").split())
    if display_width(text) <= limit:
        return text
    out, used = "", 0
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in "WF" else 1
        if used + w > limit - 1:
            break
        out += ch
        used += w
    return out + "…"


def fmt_chars(n: int) -> str:
    return f"{n:,}" if n < 1000 else f"{n / 1000:.1f}k"
