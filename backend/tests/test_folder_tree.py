"""文件夹树纯函数 — 与前端 folderTree.ts 的镜像 fixture 对跑。

运行：uv run python tests/test_folder_tree.py   （或 pytest tests/test_folder_tree.py）

覆盖：五条不变量（缺字段/悬空/自引用归根、环断在数组下标最小者、兄弟顺序、后代不含自身、
      未分类不入树）+ `parse_path` 的四种 kind + `path_key` 的结构中立。

⚠️ fixture 不是「随便造几棵树」：`parsePaths` 里「整串优先命中现有夹名」那组**依赖运行时
   folders**（历史数据里本来就叫「前端/后端」的夹不能被拆开）。只写「输入串 → 期望切分」
   根本钉不住这条保护 —— 所以每条都带 `folders`。
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.storage import folder_tree as ft
from app.storage.folder_tree import (
    MAX_SEGMENT_LEN,
    descendant_ids,
    ancestor_ids,
    make_folder_entry,
    normalize_parents,
    parse_path,
    path_key,
    paths,
    preorder,
    total_counts,
)

#: ⚠️ 共享 fixture 只有**一份**，住在前端（与 `limo-blocks.json` / `h2-sections.json`
#: 同款做法）：后端直接读前端那份，于是「两份内容要同步」这个问题根本不存在。
#: 路径从本文件反推仓库根，不写死绝对路径（写死的话换机器 / CI 上就找不到，
#: 而找不到的表现是「这组用例消失了」）。
_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "frontend" / "src" / "test" / "fixtures" / "folder-tree.json"
)


def _load() -> dict:
    with open(_FIXTURE, encoding='utf-8') as f:
        return json.load(f)


# ── 树结构（镜像 fixture）──

def test_trees_match_fixture():
    data = _load()
    assert data["trees"], "fixture 里一棵树都没有 = 这个测试恒绿"
    for case in data["trees"]:
        folders, expect = case["folders"], case["expect"]
        label = case["name"]

        assert preorder(folders) == expect["preorder"], f"[{label}] 前序不对"
        assert paths(folders) == expect["paths"], f"[{label}] 路径标签不对"
        assert total_counts(folders, case["directCounts"]) == expect["totalCounts"], \
            f"[{label}] totalCount 不对"

        for fid, want in expect["descendants"].items():
            got = sorted(descendant_ids(folders, fid))
            assert got == sorted(want), f"[{label}] {fid} 的后代不对：{got} != {want}"
        for fid, want in expect["ancestors"].items():
            assert ancestor_ids(folders, fid) == want, f"[{label}] {fid} 的祖先不对"


def test_preorder_is_topological():
    """不变量 3：父一定排在子之前 —— DocList 的扁平数组靠这条一次扫描就够。

    这条不靠 fixture 的具体数值，而是对所有树做结构断言：任何非根节点的父级，
    在 preorder 里都必须出现在它前面。
    """
    for case in _load()["trees"]:
        folders = case["folders"]
        parent = normalize_parents(folders)
        seen: set[str] = set()
        for fid in preorder(folders):
            p = parent[fid]
            if p:
                assert p in seen, f"[{case['name']}] {fid} 排在父级 {p} 之前"
            seen.add(fid)


def test_descendants_excludes_self():
    """不变量 4 的前半条。含自身会让「子树共 N 篇」自增 1，而且没人会注意到。"""
    folders = [
        {"id": "fd_a", "name": "甲", "parentId": ""},
        {"id": "fd_b", "name": "乙", "parentId": "fd_a"},
    ]
    assert "fd_a" not in descendant_ids(folders, "fd_a")
    assert descendant_ids(folders, "fd_a") == {"fd_b"}


def test_cycle_breakpoint_follows_array_order():
    """不变量 2：断点取**数组下标最小者**，不是遍历起点。

    对照实验：同一组节点换个数组顺序，断点必须跟着换 —— 否则「确定性」是靠
    「碰巧每次都从同一起点遍历」实现的，而那不是不变量。
    """
    a = {"id": "fd_a", "name": "甲", "parentId": "fd_b"}
    b = {"id": "fd_b", "name": "乙", "parentId": "fd_a"}

    # 顺序 1：a 在下标 0 → a 是根
    p1 = normalize_parents([a, b])
    assert p1["fd_a"] == "" and p1["fd_b"] == "fd_a"

    # 顺序 2：b 在下标 0 → b 是根
    p2 = normalize_parents([b, a])
    assert p2["fd_b"] == "" and p2["fd_a"] == "fd_b"


def test_dangling_parent_is_root():
    """不变量 1 的单点检验（fixture 里已覆盖，这里保留最小复现便于定位）。"""
    folders = [{"id": "fd_a", "name": "甲", "parentId": "fd_nope"}]
    assert normalize_parents(folders) == {"fd_a": ""}
    assert paths(folders) == {"fd_a": "甲"}


# ── 条目构造 ──

def test_make_folder_entry_shape():
    e = make_folder_entry("  技术  ")
    assert e["name"] == "技术", "名字必须 strip"
    assert e["parentId"] == "", "缺省必须显式写空串（而不是省略键）"
    assert e["id"].startswith("fd_")
    assert set(e) == {"id", "name", "parentId", "createdAt"}

    child = make_folder_entry("前端", "fd_a")
    assert child["parentId"] == "fd_a"


# ── path_key ──

def test_path_key_is_structure_neutral():
    """`path_key` 只做 strip + 空白折叠 + casefold。

    ⚠️ 这条盯的是「**没做**什么」：`sanitize_reference_name` 会把 `【】` 换成 `[]`、
    把 `"` 换成 `'`、截断 80 字。拿它当匹配 key 会让这类老文件夹永远匹配不上，
    而那正是历史数据保护要保住的对象。
    """
    assert path_key("  【重要】  读书  ") == "【重要】 读书"
    assert path_key("引号\"测试") == "引号\"测试", "引号不该被替换"
    long_name = "长" * 200
    assert path_key(long_name) == long_name, "不该截断"


# ── parse_path（镜像 fixture）──

def test_parse_path_matches_fixture():
    data = _load()
    assert data["parsePaths"], "fixture 里一条路径用例都没有 = 这个测试恒绿"
    for case in data["parsePaths"]:
        got = parse_path(case["input"], case.get("folders") or [])
        want = case["expect"]
        label = case["name"]

        assert got["kind"] == want["kind"], f"[{label}] kind={got['kind']} 期望 {want['kind']}"
        if "reason" in want:
            assert got.get("reason") == want["reason"], f"[{label}] reason 不对"
        if "segments" in want:
            assert got["segments"] == want["segments"], f"[{label}] segments 不对"
        if "folderId" in want:
            assert got.get("folderId") == want["folderId"], f"[{label}] folderId 不对"
        if "candidates" in want:
            assert got.get("candidates") == want["candidates"], f"[{label}] candidates 不对"


def test_parse_path_rejects_before_looking_at_folders():
    """空串必须 reject，哪怕库里真有个空名文件夹 —— 空名根本建不出来（store 拒空）。"""
    got = parse_path("", [{"id": "fd_x", "name": "", "parentId": ""}])
    assert got["kind"] == "reject" and got["reason"] == "empty"


def test_parse_path_too_long_is_not_truncated():
    """截断是**调用方**的决定（分类侧截断 + warning，ensure-path 拒绝）。

    `parse_path` 自己截断的话，ensure-path 永远看不到「超了」，用户手打一条 5 段路径
    会被静默建成 3 段 —— 一个没有报错的降级。
    """
    got = parse_path("a/b/c/d/e")
    assert got["kind"] == "path"
    assert len(got["segments"]) == 5
    assert len(got["segments"]) > ft.MAX_PATH_SEGMENTS


def test_max_segment_len_boundary():
    ok = "a" * MAX_SEGMENT_LEN
    too_long = "a" * (MAX_SEGMENT_LEN + 1)
    assert parse_path(ok)["kind"] == "path"
    assert parse_path(too_long)["kind"] == "reject"
    assert parse_path(too_long)["reason"] == "segment_too_long"


# ── 脚本式入口 ──

def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {fn.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
