"""资源 ID 的**校验**判据 —— 只回答「这个名字能不能当资源 ID」，不做任何转换。

⚠️ **和另外两个同族函数职责不同，不可合并**（三个都长得像「处理 ID 字符串」，
但做的是三件事）：

| 函数 | 是什么 | 能否失败 |
|---|---|---|
| `is_valid_resource_id()`（本模块） | **校验器** —— 读路径 / 文件服务要不要接受这个名字 | 能（返回 `False`） |
| `store._sanitize_id()` | **归一化器** —— 把任意串变成可安全拼进磁盘路径的名字 | 永不失败（兜底 `"unknown"`） |
| `export._safe_filename()` | **文件名清洗器** —— 面向 `Content-Disposition` | 永不失败 |

硬合成一个的后果是**双向的**：要么写路径开始拒绝（行为变更——用户改标题就被
报错），要么读路径继续放行 `..`（安全缺陷）。故本判据覆盖**所有「拿外来 ID 当
身份」的入口**：`QueryService.resolve_doc_id`（读路径）、`files._check_tx_id`、
`ingest._validate_tx_id`（文件服务的 tx_id 段，P1-11 起）。`store._sanitize_id`
保留原职责（见其 docstring）——它是归一化器，**不该**被当成校验器用。

⚠️ 读路径只**校验**、不**归一化**：`..` 应当被**拒绝**，而不是被清理成 `_` 之后
继续查找——后者会让「请求了一个不存在的名字」与「请求了一个别名」在日志与
错误码里长得一模一样，排查时无从下手（失败显式化原则）。
"""

import re

# 与 `files._check_tx_id` 原本逐字一致（字符集 + 长度上限）——抽出来是为了让读路径
# （`QueryService.resolve_doc_id`）与文件服务用**同一条**判据，而不是各写一份。
# ⚠️ 当时 `ingest._validate_tx_id` 是**第三份**（只有字符集、无长度上限），直到
# 2026-10-06 才归并过来（P1-11）——「抽了公共函数」不等于「所有副本都改完了」。
_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")
MAX_ID_LEN = 64


def is_valid_resource_id(value: str | None) -> bool:
    """这个字符串能不能当资源 ID。

    判据刻意**只认 ASCII 字母数字、下划线、连字符**：ID 一律由系统生成
    （`tr_<hex>` / `md_<hex>` / `pr_<hex>`），从不含中文、空格或路径分隔符。
    放开字符集等于把「ID 可以是任意串」写进契约，而下游总有人会拿 ID 拼文件路径
    ——那时每多一个字符就多一分路径构造的风险，且风险不在本函数里，看不出因果。
    """
    if not value or not isinstance(value, str):
        return False
    return len(value) <= MAX_ID_LEN and bool(_ID_RE.match(value))
