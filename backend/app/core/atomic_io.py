"""原子写文本文件 —— 全项目唯一实现。

`Store._atomic_write` 与分类元数据的落盘（`storage/doc_meta.py`）共用这一份。
抽出来的理由与 `text_utils.extract_json` 同源：`mkstemp + os.replace` 的坑
（临时名必须每次唯一）只要有一份漏了，另一份的失效方式在这边完全看不出来 ——
两个写者撞上同一临时文件，后一个 `replace` 把**半截内容**原子地换上去。
原子性只保证「换」这一步，保证不了「换上去的东西是完整的」。

⚠️ 放在 `core/` 而不是 `storage/`：`storage/store.py` 与 `storage/doc_meta.py`
都要用它，住在任一方都会让另一方反向依赖。
"""

import os
import tempfile
from pathlib import Path

from app.core.errors import StoreError


def atomic_write_text(path: Path, content: str) -> None:
    """把 `content` 原子地写到 `path`（同目录 mkstemp + os.replace）。

    失败一律抛 `StoreError`（按 errno 细分原因，见 `StoreError.write_failed`）。

    ⚠️ **临时名必须每次唯一**（`mkstemp`），不能用固定的 `path.with_suffix(".tmp")`：
    两个写者（多进程 / 并发测试）撞上同一路径时都写同一个临时文件，内容交错。

    ⚠️ 别想着「顺手加个锁更稳」：调用方（如 `Store._save_index`）恒在**已持锁**
    时调到本函数，而那个锁是非重入的 `threading.Lock` —— 加锁即死锁。并发安全
    由唯一临时名 + `os.replace` 的原子性承担，不需要互斥。
    """
    tmp_path: str | None = None
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, path)
        tmp_path = None          # 已改名，finally 不该再去删它
    except OSError as e:
        raise StoreError.write_failed(f"写入 {path.name}", e) from e
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
