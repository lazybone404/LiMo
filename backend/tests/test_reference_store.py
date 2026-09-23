"""验证参考文件暂存 — 全表过期清理 / 条目数闸门 / 总量闸门

⚠️ 本文件的核心是**孤儿条目**那条（用例 2）：前端一次只持有一个参考文件 id，
换一次文件就换一个 id，旧 id 从此再也不会出现在 `get_references()` 的入参里。
「按入参惰性清理」的实现在这种用法下**永远碰不到它**，条目会一直驻留到进程重启。
故用例断言的是 `_store` 的**实际条目数**——只断言「取回来的内容对」根本看不出来。
"""
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import app.core.reference_store as _rs
from app.core.errors import AppError

_DEFAULT_ENTRIES = _rs.MAX_ENTRIES
_DEFAULT_TOTAL = _rs.MAX_TOTAL_BYTES


def reset():
    _rs._store.clear()
    _rs.MAX_ENTRIES = _DEFAULT_ENTRIES
    _rs.MAX_TOTAL_BYTES = _DEFAULT_TOTAL


def expire_all():
    """把现有条目的过期时间拨到过去（模拟 TTL 到点，不真等 20 分钟）。"""
    for entry in _rs._store.values():
        entry["expires_at"] = 0


# ═══════════════════════════════════════════════════════════════
# 1. 基本存取
# ═══════════════════════════════════════════════════════════════

reset()
rid = _rs.save_reference("评论区.txt", "观众认为第 3 章讲得很清楚")
assert _rs.get_references([rid]) == [
    {"name": "评论区.txt", "content": "观众认为第 3 章讲得很清楚"}], '存取应保持 name/content 契约'
assert _rs.get_references(["不存在的id"]) == [], '未知 id 应跳过而非报错'
print('PASS: 基本存取 + 未知 id 跳过')


# ═══════════════════════════════════════════════════════════════
# 2. 孤儿条目必须被清理（本文件的核心用例）
#    a 被换掉后不再被查询 —— 旧实现把它永久留在 _store 里
# ═══════════════════════════════════════════════════════════════

reset()
rid_a = _rs.save_reference("a.txt", "A" * 10)
rid_b = _rs.save_reference("b.txt", "B" * 10)
assert len(_rs._store) == 2

expire_all()
rid_c = _rs.save_reference("c.txt", "C" * 10)
assert list(_rs._store) == [rid_c], (
    f'过期孤儿未被清理（save 路径）：{list(_rs._store)}')
assert _rs.get_references([rid_a]) == [], '过期条目不应返回内容'

# get 路径同样要全表清：b 已过期、a 不在入参里，但两者都该消失
_rs.save_reference("x.txt", "X")
rid_d = _rs.save_reference("d.txt", "D")
expire_all()
assert _rs.get_references([rid_d]) == [], '过期条目不应返回内容'
assert _rs._store == {}, f'get 应清理全表过期项，实际残留 {list(_rs._store)}'
print('PASS: 孤儿条目被清理（save 路径 + get 路径，均为全表扫）')


# ═══════════════════════════════════════════════════════════════
# 3. 条目数闸门
# ═══════════════════════════════════════════════════════════════

reset()
_rs.MAX_ENTRIES = 2
_rs.save_reference("1.txt", "x")
_rs.save_reference("2.txt", "x")
try:
    _rs.save_reference("3.txt", "x")
    raise AssertionError('超出条目数上限应抛 AppError')
except AppError as e:
    assert e.code == "REFERENCE_STORE_FULL", f'错误码应为 REFERENCE_STORE_FULL，实际 {e.code}'
    assert e.recoverable, '暂存满属于预期业务状态（前端可重试）'
assert len(_rs._store) == 2, '被拒的条目不应写入'
print('PASS: 条目数闸门 → AppError(REFERENCE_STORE_FULL) 且不写入')


# ═══════════════════════════════════════════════════════════════
# 4. 总量闸门 + 过期项先腾位置
#    （闸门是在清理之后判的 —— 否则「满但不该满」的场景会被误拒）
# ═══════════════════════════════════════════════════════════════

reset()
_rs.MAX_TOTAL_BYTES = 200
_rs.save_reference("big.txt", "字" * 60)          # UTF-8 中文 3 字节 → 180B
try:
    _rs.save_reference("big2.txt", "字" * 60)     # 180 + 180 > 200
    raise AssertionError('超出总量上限应抛 AppError')
except AppError as e:
    assert e.code == "REFERENCE_STORE_FULL", f'错误码应为 REFERENCE_STORE_FULL，实际 {e.code}'

# 同样 180B，但旧条目已过期 → 先清理再判闸门，应当放行
expire_all()
rid_new = _rs.save_reference("big3.txt", "字" * 60)
assert list(_rs._store) == [rid_new], '过期项应先腾位置，新条目才写得进'
print('PASS: 总量闸门（先清过期再判，过期项能腾出位置）')


reset()
print('\n全部通过：参考文件暂存（全表清理 + 双闸门）')
