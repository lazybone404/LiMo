"""静态检查：变量遮蔽（UnboundLocalError 的两类防线）

⚠️ 背景（2026-09-23，0.4.4 打包完成后的首次流式生成即炸）：

    generate_stream 顶层：  ref_texts = _resolve_reference_texts(...)
    嵌套函数 merged_stream：if ref_texts and needs_compression(...):   ← 炸在这里
                            ref_texts = await compress_references_if_needed(...)

Python 的规则是：**只要函数体内出现对某名的赋值，该名在整个函数里就是局部变量**，
与赋值语句在不在前面无关。于是嵌套函数里那句 `if ref_texts` 在任何赋值之前读它
→ UnboundLocalError。这与「关键模式」#26（函数内重复 import 造成同名局部）同根因，
只是这次跨了函数作用域。

⚠️ 第二种形态（2026-10-21，0.5.13 打包版在用户另一台机器上启动即退）：

    startup() 第 277 行：if _secret_problems: logging.getLogger(__name__)   ← 炸在这里
    startup() 第 321 行：try: import logging

    **同一个函数内**、不同分支。`import logging` 让 `logging` 成为整个 `startup`
    的局部变量，于是第 277 行在它被赋值前读取 → UnboundLocalError。
    本地永远复现不出来：第 277 行只在「配置里有明文凭据」时执行。

    这与上一条是**同一个 Python 规则**的另一种形态（赋值的来源是 import，且发生在
    同一作用域内），故收在同一个文件里：`find_shadowing` 管跨作用域，
    `find_import_shadowing` 管同作用域内的 import 遮蔽。

单测测不到它们：这不是逻辑错误，而是**运行时必炸、但看代码和跑单测都正常**的作用域
错误 —— 流式端点 / 启动分支的真实运行路径此前没有测试覆盖，而它们一走就炸。

判据必须自证有区分力（「关键模式」#38「断言存在 ≠ 断言有效」）：见下方 _OFFENDER ——
内嵌一段故意违规的源码，确认检查真能抓到；否则无法区分「检查通过」与「检查根本没跑」。
另断言**扫描范围包含后端入口 `main.py`** —— 只扫 `app/` 时上面那次事故整个看不见。
"""
import ast
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

APP_DIR = pathlib.Path(__file__).resolve().parent.parent / "app"


_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _comprehension_locals(node) -> set[str]:
    """推导式的循环变量。

    ⚠️ Python 3 里它们绑定在**推导式自己的作用域**，不会让外层函数的同名变量
    变成局部变量 —— 不排除就会误报（实测：`limo_cli/output.py::table()` 的
    `line()` 里 `[pad(cells[i], widths[i]) for i in range(...)]`，`i` 是推导式的，
    而外层 `table()` 恰好也有个 `i`）。
    """
    names: set[str] = set()
    for gen in node.generators:
        names |= {n.id for n in ast.walk(gen.target) if isinstance(n, ast.Name)}
    return names


def _iter_comprehension(node, rec, shadowed) -> None:
    """按求值顺序遍历推导式。

    iterable 在**外层作用域**求值（`for x in x` 的后者读外层 x），
    ifs / elt / key / value 在推导式作用域里求值 —— 其中与循环变量同名的读写
    属于推导式内部，忽略。
    """
    inner = shadowed | _comprehension_locals(node)
    for gen in node.generators:
        rec(gen.iter, shadowed)
    for gen in node.generators:
        for cond in gen.ifs:
            rec(cond, inner)
    if isinstance(node, ast.DictComp):
        rec(node.key, inner)
        rec(node.value, inner)
    else:
        rec(node.elt, inner)


def _own_stores(fn: ast.AST) -> set[str]:
    """fn **自身**作用域内被赋值的名字（不进入嵌套函数/类——那些是独立作用域）。"""
    out: set[str] = set()

    def rec(node, shadowed=frozenset()):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(child, _COMPREHENSIONS):
                _iter_comprehension(child, rec, shadowed)
                continue
            if (isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
                    and child.id not in shadowed):
                out.add(child.id)
            rec(child, shadowed)

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

    def rec(node, shadowed=frozenset()):
        nonlocal counter
        if isinstance(node, _COMPREHENSIONS):
            _iter_comprehension(node, rec, shadowed)
            return
        if isinstance(node, ast.Name):
            if node.id in shadowed:                 # 推导式自己的循环变量
                return
            counter += 1
            seq.append((node.id,
                        "load" if isinstance(node.ctx, ast.Load) else "store",
                        counter))
            return
        if isinstance(node, ast.AnnAssign):          # x: T = v —— 注解/值先，目标后
            if node.annotation is not None:
                rec(node.annotation, shadowed)
            if node.value is not None:
                rec(node.value, shadowed)
            rec(node.target, shadowed)
            return
        if isinstance(node, ast.AugAssign):          # x += 1 —— 先读后写
            counter += 1
            if isinstance(node.target, ast.Name):
                seq.append((node.target.id, "load", counter))
            rec(node.value, shadowed)
            counter += 1
            if isinstance(node.target, ast.Name):
                seq.append((node.target.id, "store", counter))
            return
        if isinstance(node, ast.Assign):             # 值先，目标后
            rec(node.value, shadowed)
            for t in node.targets:
                rec(t, shadowed)
            return
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue                             # 嵌套函数独立作用域，另算
            rec(child, shadowed)

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


def _own_events(fn: ast.AST) -> list[tuple[str, str, int]]:
    """fn **自身**作用域内按执行顺序展开 (名字, 'load'|'import', 序号)。

    只关心两件事的相对顺序：名字被读取、名字被 import 绑定。序号仅用于**同一函数内**
    比较，故与 `_sequence` 的编号不必一致。
    ⚠️ 不进入嵌套函数/类（它们各有独立作用域，那里的 import 不影响本函数）。
    """
    events: list[tuple[str, str, int]] = []
    counter = 0

    def rec(node, shadowed=frozenset()):
        nonlocal counter
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return
        if isinstance(node, _COMPREHENSIONS):
            _iter_comprehension(node, rec, shadowed)
            return
        if isinstance(node, ast.Import):
            for a in node.names:                      # `import a.b` 绑定 a
                counter += 1
                events.append((a.asname or a.name.split(".")[0], "import", counter))
            return
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name == "*":                     # 星号导入不绑定确定名字
                    continue
                counter += 1
                events.append((a.asname or a.name, "import", counter))
            return
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id in shadowed:                   # 推导式自己的循环变量
                return
            counter += 1
            events.append((node.id, "load", counter))
            return
        for child in ast.iter_child_nodes(node):
            rec(child, shadowed)

    for stmt in fn.body:
        rec(stmt)
    return events


def find_import_shadowing(tree: ast.AST) -> list[str]:
    """返回「函数体内 import 遮蔽同名变量且更早被读取」的违规描述列表。

    判据：函数自身作用域内 import 绑定了 X ∩ 同一作用域内存在 X 的读取且更早
    ∩ X 不是形参（形参进入即绑定，不会 UnboundLocal）∩ 未被 global/nonlocal 豁免。
    """
    problems: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        events = _own_events(fn)
        if not events:
            continue
        excused = {x for s in ast.walk(fn)
                   if isinstance(s, (ast.Global, ast.Nonlocal)) for x in s.names}
        params = _param_names(fn)

        first_load: dict[str, int] = {}
        import_order: dict[str, int] = {}
        for name, kind, order in events:
            if kind == "load":
                first_load.setdefault(name, order)
            else:
                import_order.setdefault(name, order)

        for name, imp_order in import_order.items():
            load_order = first_load.get(name)
            if load_order is None or load_order >= imp_order:
                continue
            if name in params or name in excused:
                continue
            problems.append(
                f"{fn.name}() 内 `import` 绑定的 {name!r} 在更早处被读取"
                f"（读@序{load_order} < import@序{imp_order}）"
                f" —— import 让它在整个函数里成为局部变量")
    return problems


def _check(src: str) -> list[str]:
    return find_shadowing(ast.parse(src))


def _check_import(src: str) -> list[str]:
    return find_import_shadowing(ast.parse(src))


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
# 判据自证 · 同函数内 import 遮蔽（2026-10-21 事故形态）
# ═══════════════════════════════════════════════════════════════

# 违规 1：正是线上炸掉的形态 —— 分支里早读、try 里 import
_OFFENDER_IMPORT_BRANCH = '''
def startup():
    if secret_problems:
        logging.getLogger(__name__).info("plaintext secrets found")
    try:
        import logging
    except ImportError:
        pass
'''

# 违规 2：`from m import x` 同样让 x 成为局部名
_OFFENDER_FROM_IMPORT = '''
def run():
    if flag:
        os.path.join("a", "b")
    from os import path as os
'''

# 安全 1：函数内先 import 后使用（常见写法，不该报）
_CLEAN_IMPORT_THEN_USE = '''
def run():
    import json
    return json.dumps({})
'''

# 安全 2：别名 import 绑定的是别的名字
_CLEAN_IMPORT_ALIAS = '''
def run():
    print(logging.getLogger(__name__))
    import logging as _logging
    return _logging
'''

# 安全 3：嵌套函数里的 import 是独立作用域，不影响外层
_CLEAN_IMPORT_IN_NESTED = '''
def outer():
    print(json.dumps({}))
    def inner():
        import json
        return json
'''

# 安全 4：同名是形参（进入即绑定，import 只是覆盖值，不会 UnboundLocal）
_CLEAN_IMPORT_PARAM = '''
def run(logging):
    if logging:
        pass
    import logging
'''

# 安全 5：推导式循环变量是推导式自己的作用域（实测踩到的误报，
# 来自 limo_cli/output.py::table() —— 外层有 i，内层 line() 的推导式也有 i）
_CLEAN_COMPREHENSION_VAR = '''
def table(rows):
    for i in range(3):
        pass
    def line(cells):
        parts = [cells[i] for i in range(2)]
        return parts
'''

for label, src in [("分支内早读 + import", _OFFENDER_IMPORT_BRANCH),
                   ("from import 遮蔽", _OFFENDER_FROM_IMPORT)]:
    got = _check_import(src)
    assert len(got) == 1, (
        f'判据无区分力：故意违规的「{label}」未被抓到（返回 {got}）——'
        f'检查实际在空跑')
print('PASS: 判据自证 —— 两种 import 遮蔽形态均被捕获')

for label, src in [("先 import 后使用", _CLEAN_IMPORT_THEN_USE),
                   ("别名 import", _CLEAN_IMPORT_ALIAS),
                   ("嵌套函数内 import", _CLEAN_IMPORT_IN_NESTED),
                   ("形参同名", _CLEAN_IMPORT_PARAM)]:
    got = _check_import(src)
    assert got == [], f'误报：「{label}」被当成 import 遮蔽（{got}）'
print('PASS: 判据自证 —— 四种安全写法均不误报')

# 推导式循环变量在 Python 3 是独立作用域 —— 两个判据都不该报
assert _check(_CLEAN_COMPREHENSION_VAR) == [], \
    f'误报：推导式循环变量被当成外层赋值（{_check(_CLEAN_COMPREHENSION_VAR)}）'
assert _check_import(_CLEAN_COMPREHENSION_VAR) == [], \
    f'误报：推导式循环变量被当成 import 遮蔽（{_check_import(_CLEAN_COMPREHENSION_VAR)}）'
print('PASS: 判据自证 —— 推导式循环变量不误报')

# 这个判据只该管 import —— 层叠判据互不越界（否则「关掉一个另一个还在响」看不出来）
assert _check_import(_OFFENDER_READ_THEN_WRITE) == [], \
    'import 判据不该抓到「跨作用域先读后写」，那由 find_shadowing 负责'
assert _check(_OFFENDER_IMPORT_BRANCH) == [], \
    '嵌套遮蔽判据不该抓到「同作用域 import 遮蔽」，那由 find_import_shadowing 负责'
print('PASS: 两个判据职责不重叠')

# ═══════════════════════════════════════════════════════════════
# 真实代码库
# ═══════════════════════════════════════════════════════════════

BACKEND_DIR = APP_DIR.parent
SCAN_ROOTS = [APP_DIR, BACKEND_DIR / "limo_cli"]
files = sorted(p for d in SCAN_ROOTS for p in d.rglob("*.py"))
main_py = BACKEND_DIR / "main.py"
if main_py.exists():
    files.append(main_py)
files = sorted(set(files))
assert files, f'未找到源文件：{SCAN_ROOTS}'

# ⚠️ 扫描范围本身要有守卫：0.5.13 那次事故就发生在 main.py，而它当时**不在**扫描范围里
# （只扫 app/），于是「全绿」与「检查看不见出事的地方」是同一件事（模式 #38 的延伸）。
assert main_py in files, '扫描范围漏了后端入口 main.py —— 守卫看不见那里'

all_problems: list[tuple[str, list[str]]] = []
for path in files:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as e:
        raise AssertionError(f'{path} 语法错误：{e}')
    rel = str(path.relative_to(BACKEND_DIR))
    for p in find_shadowing(tree) + find_import_shadowing(tree):
        all_problems.append((rel, [p]))

if all_problems:
    for f, probs in all_problems:
        for p in probs:
            print(f'  ✗ {f}: {p}')

assert not all_problems, (
    f'{len(all_problems)} 个文件存在变量遮蔽 —— 运行时必然 UnboundLocalError。'
    f'修法：跨作用域改用局部名承接（local = outer_var）；'
    f'同作用域内去掉多余的函数内 import（模块顶部已有），或改名/用别名')

print(f'PASS: 全库 {len(files)} 个文件无变量遮蔽（含 main.py 与 limo_cli）')

print('\nAll closure shadowing tests PASS')
