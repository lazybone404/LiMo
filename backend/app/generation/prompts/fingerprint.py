"""提示词文本指纹 —— 手抄的 `PROMPT_VERSION` 的自动化对照物。

⚠️ 守的是什么：`PROMPT_VERSION` 由人手动 bump，而它已经漏过一次（`1ef7e23` 加了
关键词规范没 bump，于是 "23" 同时对应两种 prompt）。手抄的版本号是 prompt 文本的
第二份副本，改动与 bump 之间没有任何机器约束，漂移不报错。

指纹的做法：把 `generation/prompt.py` 与 `generation/prompts/*.py` 里**全部字符串
字面量**（跳过 docstring）排序后拼起来取 sha256 前 8 位。测试断言声明值 == 计算值；
改一句提示词而忘了 bump（或反过来，只 bump 没改文本）都会红。

⚠️ **为什么不把指纹直接当版本号**：`"24"` / `"26"` 这种说法在 CHANGELOG、讨论与
产物自证头里到处都是，且同号码对应两种 prompt 的历史已经发生（"25"/"26"）。
版本号留给人读，指纹只做守卫。

⚠️ **跳过 docstring**：注释性说明不参与 prompt 组合，改了不该要求 bump。
⚠️ **不哈希行号 / 文件名**：常量搬家（prompt.py ↔ common.py）不改提示词本身，
若把路径与顺序算进去，每次重构都会红。

用法：`from app.generation.prompts.fingerprint import prompt_fingerprint`。
"""
import ast
import hashlib
from pathlib import Path

_DIR = Path(__file__).resolve().parent


def source_files() -> list[Path]:
    """参与指纹的源文件。

    `prompts/__init__.py` **必须排除**：`PROMPT_FINGERPRINT` 就写在里面，把它算进来
    会让「声明值 == 计算值」永远不成立。`fingerprint.py` 自己同理（它的源码里有
    `"utf-8"` 这类字面量，且改实现逻辑不该改变指纹）。
    """
    files = [_DIR.parent / "prompt.py"]
    files += sorted(
        p for p in _DIR.glob("*.py")
        if p.name not in ("__init__.py", "fingerprint.py")
    )
    return files


def _literals(source: str) -> list[str]:
    """源码里的字符串字面量（跳过 docstring），排序后返回。"""
    tree = ast.parse(source)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    return sorted(
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    )


def fingerprint_of_sources(sources: list[str]) -> str:
    """给定若干份源码，算指纹。测试用它喂「改了一个字面量」的对照样本。"""
    h = hashlib.sha256()
    literals: list[str] = []
    for src in sources:
        literals.extend(_literals(src))
    for s in sorted(literals):
        h.update(s.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()[:8]


def prompt_fingerprint() -> str:
    """当前代码里提示词的实际指纹。"""
    return fingerprint_of_sources(
        [p.read_text(encoding="utf-8") for p in source_files()]
    )
