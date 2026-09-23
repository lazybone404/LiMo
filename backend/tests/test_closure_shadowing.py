"""静态检查：嵌套函数内的变量遮蔽（UnboundLocalError 的整类防线）

⚠️ 背景（2026-09-23，0.4.4 打包完成后的首次流式生成即炸）：

    generate_stream 顶层：  ref_texts = _resolve_reference_texts(...)
    嵌套函数 merged_stream：if ref_texts and needs_compression(...):   ← 炸在这里
                            ref_texts = await compress_references_if_needed(...)

Python 的规则是：**只要函数体内出现对某名的赋值，该名在整个函数里就是局部变量**，
与赋值语句在不在前面无关。于是嵌套函数里那句 `if ref_texts` 在任何赋值之前读它
→ UnboundLocalError。这与「关键模式」#26（函数内重复 import 造成同名局部）同根因，
只是这次跨了函数作用域。

单测测不到它：这不是逻辑错误，而是**运行时必炸、但看代码和跑单测都正常**的作用域
错误 —— 流式端点的真实运行路径此前没有测试覆盖，而它一走就炸。

故这里用 AST 做静态判定，防的是**整类**而非这一个：
    外层函数赋值的名字 ∩ 嵌套函数赋值的名字 ∩ 嵌套函数内「读早于写」 → 违规

判据必须自证有区分力（「关键模式」#38「断言存在 ≠ 断言有效」）：见下方 _OFFENDER ——
内嵌一段故意违规的源码，确认检查真能抓到；否则无法区分「检查通过」与「检查根本没跑」。
"""
import ast
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

APP_DIR = pathlib.Path(__file__).resolve().parent.parent / "app"


def _own_stores(fn: ast.AST) -> set[str]:
    """fn **自身**作用域内被赋值的名字（不进入嵌套函数/类——那些是独立作用域）。"""
    out: set[str] = set()

    def rec(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                out.add(child.id)
            rec(child)

    rec(fn)
    return out


def _sequence(body) -> list[tuple[str, str, int]]:
    """按**执行顺序**展开 (名字, 'load'|'store', 序号)。

    ⚠️ 必须按执行顺序而非 AST 子节点顺序：`ast.Assign._fields` 是
    ('targets', 'value')，可语义上 value 先求值。直接 ast.walk 会把 `x = x + 1`
    判成「先写后读」→ 正好漏掉最该抓的那类。
    """
    seq: list[tuple[str, str, int]] = []
    counter = 0

    def rec(node):
        nonlocal counter
        if isinstance(node, ast.Name):
            counter += 1
            seq.append((node.id,
                        "load" if isinstance(node.ctx, ast.Load) else "store",
                        counter))
            return
        if isinstance(node, ast.AnnAssign):          # x: T = v —— 注解/值先，目标后
            if node.annotation is not None:
                rec(node.annotation)
            if node.value is not None:
                rec(node.value)
            rec(node.target)
            return
        if isinstance(node, ast.AugAssign):          # x += 1 —— 先读后写
            counter += 1
            if isinstance(node.target, ast.Name):
                seq.append((node.target.id, "load", counter))
            rec(node.value)
            counter += 1
            if isinstance(node.target, ast.Name):
                seq.append((node.target.id, "store", counter))
            return
        if isinstance(node, ast.Assign):             # 值先，目标后
            rec(node.value)
            for t in node.targets:
                rec(t)
            return
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue                             # 嵌套函数独立作用域，另算
            rec(child)

    for stmt in body:
        rec(stmt)
    return seq


def _param_names(fn: ast.AST) -> set[str]:
    a = fn.args
    names = {x.arg for x in getattr(a, "posonlyargs", [])}
    names |= {x.arg for x in a.args}
    names |= {x.arg for x in a.kwonlyargs}
    if a.vararg:
        names.add(a.vararg.arg)
    if a.kwarg:
        names.add(a.kwarg.arg)
    return names


def find_shadowing(tree: ast.AST) -> list[str]:
    """返回违规描述列表（空 = 无遮蔽）。"""
    problems: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        nested = [n for n in ast.walk(fn)
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n is not fn]
        if not nested:
            continue
        outer = _own_stores(fn)
        for nf in nested:
            # nonlocal/global 是刻意声明，不算遮蔽
            excused = {x for s in ast.walk(nf) if isinstance(s, ast.Nonlocal) for x in s.names}
            excused |= {x for s in ast.walk(nf) if isinstance(s, ast.Global) for x in s.names}

            first_load: dict[str, int] = {}
            # 形参进入函数时即已绑定 → 视作 order 0 的写，避免把「读形参」误判成先读
            first_store: dict[str, int] = {p: 0 for p in _param_names(nf)}
            for name, kind, order in _sequence(nf.body):
                if kind == "load":
                    first_load.setdefault(name, order)
                else:
                    first_store.setdefault(name, order)

            for name, load_order in first_load.items():
                store_order = first_store.get(name)
                if (store_order is not None and load_order < store_order
                        and name in outer and name not in excused):
                    problems.append(
                        f"{fn.name}() 的局部变量 {name!r} 在嵌套函数 {nf.name}() 内"
                        f"先读后写（读@序{load_order} < 写@序{store_order}）")
    return problems


def _check(src: str) -> list[str]:
    return find_shadowing(ast.parse(src))


# ═══════════════════════════════════════════════════════════════
# 判据自证（必须先证明它有区分力，模式 #38）
# ═══════════════════════════════════════════════════════════════

# 违规 1：正是本次线上炸掉的形态 —— 内层先读后写
_OFFENDER_READ_THEN_WRITE = '''
async def endpoint():
    refs = []
    async def merged():
        if refs:
            pass
        refs = await compress(refs)
'''

# 违规 2：自增运算符（读写同一行，AST 子节点顺序会骗过朴素实现）
_OFFENDER_AUGASSIGN = '''
def outer():
    n = 0
    def inner():
        print(n)
        n += 1
'''

# 违规 3：`x = x + 1`（值先于目标求值，同样会炸）
_OFFENDER_SELF_REF = '''
def outer():
    n = 0
    def inner():
        print(n)
        n = n + 1
'''

# 安全 1：用局部名承接（本次的修法）
_CLEAN_LOCAL_ALIAS = '''
async def endpoint():
    refs = []
    async def merged():
        local = refs
        if local:
            pass
        local = await compress(local)
'''

# 安全 2：内层先赋值后使用（遮蔽但不炸）—— 库里 5 处都是这种
_CLEAN_WRITE_THEN_READ = '''
def outer():
    chunks = []
    async def worker():
        chunks = []
        chunks.append("x")
'''

# 安全 3：nonlocal 声明（刻意回写外层）
_CLEAN_NONLOCAL = '''
def outer():
    total = 0
    def inner():
        nonlocal total
        total += 1
'''

for label, src in [("先读后写", _OFFENDER_READ_THEN_WRITE),
                   ("自增运算符", _OFFENDER_AUGASSIGN),
                   ("自引用赋值", _OFFENDER_SELF_REF)]:
    got = _check(src)
    assert len(got) == 1, (
        f'判据无区分力：故意违规的「{label}」未被抓到（返回 {got}）——'
        f'检查实际在空跑')
print('PASS: 判据自证 —— 三种真实违规形态均被捕获')

for label, src in [("局部名承接", _CLEAN_LOCAL_ALIAS),
                   ("先写后读", _CLEAN_WRITE_THEN_READ),
                   ("nonlocal", _CLEAN_NONLOCAL)]:
    got = _check(src)
    assert got == [], f'误报：「{label}」被当成遮蔽（{got}）'
print('PASS: 判据自证 —— 三种安全写法均不误报')

# 形参不该被算作「外层变量」（形参进入即绑定）
assert _check('''
def outer():
    items = []
    def inner(items):
        print(items)
''') == [], '形参遮蔽外层同名变量是安全的，不该报'
print('PASS: 形参不被误判')

# ═══════════════════════════════════════════════════════════════
# 真实代码库
# ═══════════════════════════════════════════════════════════════

files = sorted(APP_DIR.rglob("*.py"))
assert files, f'未找到源文件：{APP_DIR}'

all_problems: list[tuple[str, list[str]]] = []
for path in files:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as e:
        raise AssertionError(f'{path} 语法错误：{e}')
    found = find_shadowing(tree)
    if found:
        all_problems.append((str(path.relative_to(APP_DIR.parent)), found))

if all_problems:
    for f, probs in all_problems:
        for p in probs:
            print(f'  ✗ {f}: {p}')

assert not all_problems, (
    f'{len(all_problems)} 个文件存在嵌套函数变量遮蔽 —— 运行时必然 UnboundLocalError。'
    f'修法：在嵌套函数内改用局部名承接（如 local = outer_var），不要写回外层变量')

print(f'PASS: 全库 {len(files)} 个文件无嵌套函数变量遮蔽')

print('\nAll closure shadowing tests PASS')
