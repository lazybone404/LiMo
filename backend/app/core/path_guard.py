"""`{base} / {用户可控相对路径}` 的解析与越界守卫 — 单一真相源。

这条判据（resolve 后 relative_to 基准目录）此前散在至少 3 处，形态各不相同：

    generation/content_preparer.py:438   发给 LLM 的图片内联（有守卫）
    generation/synthesis.py:411          合成资产搬运（有守卫）
    routers/export.py:684,749            **导出资产收集（无守卫）**

第四处不是「写法不同」，而是**整个判据缺失**——而它恰好是唯一把内容写进对外分发产物的
那一处（导出 HTML / ZIP 会把越界读到的文件 base64 内嵌）。收成本模块的理由不是「去重」，
是**漏一处就是一条任意文件读取通道**。

⚠️ **判据不是「路径里有没有 `..`」**。合法引用本就带 `/`（`assets/sub/img.png`），而
`assets/sub/../img.png` 解析后仍落在基准目录内 —— 只按字符串判会**误伤前者、放过后者**。
唯一可靠的判据是**解析到最终形态后再比对前缀**。

⚠️ 返回值是 `Path | None` 而非抛异常：各调用方的越界处置本就不同（返回占位符 / 原样保留
引用 / 跳过该条目），把处置留在调用方，本模块只回答「这条路径能不能用」。
"""

from __future__ import annotations

from pathlib import Path


def resolve_within(base: str | Path, rel: str | Path) -> Path | None:
    """把 `rel` 拼到 `base` 下并解析；**越界或解析失败返回 `None`**。

    调用方拿到 `None` 必须**放弃该条目**（跳过 / 占位 / 原样保留），不要退回未解析的
    原始路径——退回等于守卫白加（模式 #52：成对操作漏一半与完全没做等价）。

    `rel` 为绝对路径时 `Path(base) / rel` 会丢弃 base，解析后必然越界 → `None`，这是
    期望行为。

    `Path.resolve()` 对不存在的路径同样返回（`strict=False`），故本函数**不负责**
    存在性检查，调用方仍需自行 `exists()` / `is_file()`。
    """
    base_path = Path(base)
    try:
        target = (base_path / rel).resolve()
        target.relative_to(base_path.resolve())
    except (ValueError, OSError):
        return None
    return target
