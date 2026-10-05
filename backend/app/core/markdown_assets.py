"""Markdown 图片引用（`assets/` 相对路径）的匹配与改写 — 单一真相源（P2-18）。

`![alt](assets/xxx)` 这一形态此前散在 **4 处**：

    storage/store.py:36                   _RE_ASSET_REF     （组2 = **纯文件名**）
    generation/synthesis.py:245           _ASSET_REF_RE     （组2 = assets/xxx）
    routers/export.py:637                 _ASSET_RE         （组2 = assets/xxx）
    generation/content_preparer.py:417    asset_ref_pat     （组2 = assets/xxx）

后三者**逐字同构**，store 那份却把 `assets/` 放在捕获组**外**——「捕获组布局各异」
的后果不是报错，而是「改一处忘一处」时某一侧的文件名多一段或少一段，两侧都不崩。

⚠️ 收敛到**同一个编译对象**（而不是各自 `re.compile` 同一份字符串）还有一个实打实
的作用：`content_preparer.py:432` 与 `export.py:740` 都用 `m.re is <正则名>` 判断
「这次匹配来自哪个模式」。各自 compile 时两个对象永不相等，只有共享对象时 `is` 才成立
——所以这里导出的是**对象**，不是字符串模式。

⚠️ **本模块只放「形态 + 成对改写」**，不放具体用途（路径穿越守卫、资产复制、导出收集
各有各的语义，留在调用方）。反例：`ocr_preprocessor.py:21` 的 `_RE_IMG_REF` 长得像
本模块的 `ASSET_REF_RE`，但它**刻意更严**（限定 `img_/fig_` + 4 位数字 + 扩展名），
且全项目只有那一个使用点——搬进来会制造「看起来同源其实不同源」的错觉（模式 #61），
故留在原地并在那边注明了差异。

⚠️ **正向与反向必须同模块**（模式 #52）：`rewrite_asset_urls` 与 `unrewrite_asset_urls`
是一对，分开放时「改了正向忘了反向」不会有任何报错，只表现为磁盘上悄悄多出一批绝对
URL，直到导出 / 二次渲染才开始裂图。
"""

from __future__ import annotations

import re

ASSET_DIR = "assets"

#: ``![alt](assets/xxx)`` —— 磁盘相对路径的图片引用。
#: 组 1 = alt 文本，组 2 = **含 `assets/` 前缀**的路径（如 `assets/img_0001.png`）。
#: 路径里允许 `/`（`assets/sub/img.png`）但不允许空白与 `)`——后者是 Markdown 引用的
#: 边界，越过后会把正文一起吞掉。
ASSET_REF_RE = re.compile(r'!\[([^\]]*)\]\((assets/[^\s)]+)\)')


def asset_rel_path(arg: str) -> str:
    """`assets/img_0001.png` → `img_0001.png`。

    ⚠️ 存在的理由是**捕获组布局的统一**：本模块的 `ASSET_REF_RE` 组 2 含 `assets/`
    前缀（另三份同构副本的约定），而 `store.py` 原来那份把前缀放在组外、直接用
    `\\2` 拼 URL。统一后由本函数去掉前缀，**逐字节等价**于原实现——顺带修掉
    「`/api/assets/{tx_id}/assets/img.png`」那段重复 `assets/` 的历史 bug 形态。

    不以 `assets/` 开头时原样返回（正则保证了不会发生，留着是为了误用时**看得见**）。
    """
    prefix = ASSET_DIR + "/"
    return arg[len(prefix):] if arg.startswith(prefix) else arg


def rewrite_asset_urls(content: str, tx_id: str) -> str:
    """磁盘相对 `assets/xxx` → 绝对 API URL `/api/assets/{tx_id}/xxx`，供前端渲染。

    ⚠️ **仅用于路由层**（content API 返回前改写）。Store CRUD 方法
    （`get_markdown` / `get_transcript` 等）**不调**此函数——导出需要原始相对路径
    （从 `{doc_dir}/assets/` 读盘），续写需要原文。
    """
    if not content:
        return content
    return ASSET_REF_RE.sub(
        lambda m: f"![{m.group(1)}](/api/assets/{tx_id}/{asset_rel_path(m.group(2))})",
        content,
    )


def unrewrite_asset_urls(content: str, tx_id: str) -> str:
    """`rewrite_asset_urls` 的**反向**：绝对 API URL → `assets/xxx`。

    编辑保存前转回相对路径，避免绝对 URL 写回磁盘污染导出 / 二次渲染。

    ⚠️ 只处理**本文档自己**的 URL（`tx_id` 参与匹配）——别的文档的 `/api/assets/other/…`
    原样保留。`<img src="...">` 等非 Markdown 引用形态也不动：那是用户新增的内容，
    按用户输入存盘（与正向只认 Markdown 图片引用相对称）。
    """
    if not content:
        return content
    pat = re.compile(r"!\[([^\]]*)\]\(/api/assets/" + re.escape(tx_id) + r"/([^\s)]+)\)")
    return pat.sub(r"![\1](assets/\2)", content)
