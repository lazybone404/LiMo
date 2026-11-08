"""分层依赖守卫：`services/` 不 import FastAPI、`storage/` 不 import 上层。

⚠️ 为什么需要它：这两条约束今天**零自动检查**。破坏形态不是报错，而是「加个
`from fastapi import Request` 做类型注解，全部测试照绿」，几周后才在「只装
`storage`/`QueryService` 而不装 FastAPI」的场合暴露 —— 那时报出来的是
`ModuleNotFoundError`，而引入它的提交早就沉进历史里了。

反方向（`storage` import `routers`、`adapters` 互相 import）**刻意不守**：那种
破坏会当场循环 import 崩掉，是响亮失败，不需要守卫。

判据必须自证有区分力：文件末尾的对照样本把「违规」与「合法」两种输入喂给同一个
检查函数，证明它会红也会绿。只断言「项目当前是干净的」的话，检查函数写坏了
（比如永远返回空列表）测试也是绿的。
"""
import ast
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

APP_DIR = Path(__file__).resolve().parent.parent / 'app'

# (受约束的包前缀, 禁止的模块前缀)
RULES: list[tuple[str, tuple[str, ...]]] = [
    ('app.services', ('fastapi',)),
    ('app.storage', ('app.routers', 'app.services')),
]


def _module_name(path: Path) -> str:
    """`app/storage/store.py` → `app.storage.store`；`__init__.py` → `app.storage.__init__`。"""
    rel = path.relative_to(APP_DIR.parent).with_suffix('')
    return '.'.join(rel.parts)


def _package_of(module: str) -> str:
    """模块所在的包：`a.b.store` → `a.b`；`a.b.__init__` → `a.b`。"""
    if module.endswith('.__init__'):
        return module[: -len('.__init__')]
    return module.rsplit('.', 1)[0]


def _resolve_from(module: str, node: ast.ImportFrom) -> str:
    """把 `from .x import y` 解析成绝对模块名；`level=0` 原样返回。"""
    if node.level == 0:
        return node.module or ''
    parts = _package_of(module).split('.')
    # `level=1` 是当前包，每多一级往上退一层（`..` = 父包）
    parts = parts[: len(parts) - (node.level - 1)]
    if node.module:
        parts = parts + node.module.split('.')
    return '.'.join(parts)


def _imported(module: str, source: str) -> list[tuple[int, str]]:
    """该源文件 import 的全部模块（绝对名 + 行号）。"""
    tree = ast.parse(source, filename=module)
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend((node.lineno, a.name) for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.append((node.lineno, _resolve_from(module, node)))
    return out


def _violations(module: str, source: str) -> list[tuple[int, str]]:
    """返回 (行号, 被 import 的模块) —— 空列表表示这一份源文件合规。"""
    bad: list[tuple[int, str]] = []
    for scope, banned in RULES:
        if not (module == scope or module.startswith(scope + '.')):
            continue
        for line, target in _imported(module, source):
            if any(target == b or target.startswith(b + '.') for b in banned):
                bad.append((line, target))
    return bad


def test_项目当前没有跨层_import():
    offenders = []
    for path in sorted(APP_DIR.rglob('*.py')):
        module = _module_name(path)
        for line, target in _violations(module, path.read_text(encoding='utf-8')):
            offenders.append(f'{module}:{line} → {target}')
    assert not offenders, '跨层 import：\n' + '\n'.join(offenders)


# ── 对照实验：证明检查函数有区分力 ──

@pytest.mark.parametrize('module, source', [
    ('app.services.query', 'from fastapi import Request\n'),
    ('app.services.query', 'import fastapi.responses\n'),
    ('app.storage.store', 'from app.routers import docs\n'),
    ('app.storage.store', 'from ..services import query\n'),
    ('app.storage.store', 'from ..routers.docs import router\n'),
])
def test_违规样本会被抓到(module, source):
    assert _violations(module, source), '检查函数漏掉了这一种写法'


@pytest.mark.parametrize('module, source', [
    ('app.services.query', 'from app.storage import store\n'),
    ('app.storage.store', 'from app.core import config\n'),
    # 名字里带 fastapi 但不是那个模块
    ('app.services.query', 'from app.core.fastapi_helpers import x\n'),
    # 其他层不受这两条约束
    ('app.routers.docs', 'from fastapi import APIRouter\n'),
])
def test_合法样本不会被误伤(module, source):
    assert _violations(module, source) == []
