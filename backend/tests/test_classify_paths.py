"""自动分类 · 嵌套路径（2026-10-18）—— 分类结果要能长出层级。

与 `test_classify.py` 分开的理由：那一份已经 1265 行、讲的是「一条扁平流水线」，
这一组讲的是「路径怎么解析、额度怎么算、歧义怎么办」，判据自成一块。

⚠️ 本组守的核心是**判据同源**：`resolve_new_target` 既是额度规划（`_plan_new_paths`）
的入口，也是收口侧（`_normalize_one`）的入口。两处各解析一次的话，「这条路径算几行新建」
与「它到底是 reuse 还是 new」迟早分歧 —— 而分歧的形态是**静默的**（额度算错但分类结果
看起来完全正常）。故凡是标「对照」的断言，都对应一处可执行的改法，改坏了必红。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.generation.classifier import (  # noqa: E402
    MAX_NEW_FOLDERS,
    build_classify_prompt,
    normalize_suggestions,
)


def _docs(*ids: str) -> list[dict]:
    """`collect_doc_inputs` 的输出形状（归一化的输入契约）。"""
    return [{"id": i, "title": i, "source": "notes", "opening": "",
             "chars": 0, "warnings": []} for i in ids]


# 技术 ⊃ 前端；生活。`count` 是直接数、`totalCount` 含子树 —— 两者有意不同，
# 「清单该显示哪一个」正是下面第一条要钉的。
_TREE = [
    {"id": "fd_tech", "name": "技术", "count": 2, "totalCount": 40},
    {"id": "fd_fe", "name": "前端", "parentId": "fd_tech", "count": 38, "totalCount": 38},
    {"id": "fd_life", "name": "生活", "count": 5, "totalCount": 5},
]


# ─────────────────────────────────────────────────────────────
# prompt 侧
# ─────────────────────────────────────────────────────────────

def test_prompt_shows_paths_and_subtree_counts():
    """清单展示**路径**与**子树总量**。

    裸名在嵌套之后无法区分（「前端」可能活在多个父级下，prompt 里读起来完全一样）；
    规模用 `count`（直接数）会把「只有 2 篇直接文档、下挂 40 篇」的三层夹误判成冷门。

    对照（已验证有区分力）：把 `totalCount` 换回 `count` → 「含子文件夹共 40 篇」消失；
    把路径换回裸名 → 「技术 › 前端」消失。
    """
    p = build_classify_prompt(_docs("tx_a"), _TREE, [], MAX_NEW_FOLDERS)
    assert "技术 › 前端" in p
    assert "含子文件夹共 40 篇" in p
    assert "含子文件夹共 2 篇" not in p
    # 规则段必须教模型用 `/` 分层且限深（否则它只会继续往根上加平铺夹）
    assert "最多 3 层" in p


def test_prompt_schema_does_not_add_parent_fields():
    """输出 schema **不新增** parent 字段：路径就是一个字符串。

    加结构化字段只会放大编造面（模型要同时给出父子关系与两处一致的名字），
    而字符串路径已经足够表达层级。
    """
    p = build_classify_prompt(_docs("tx_a"), _TREE, [], MAX_NEW_FOLDERS)
    assert "parentId" not in p
    assert "parentPath" not in p


# ─────────────────────────────────────────────────────────────
# 收口侧 —— 复用 vs 新建 vs 歧义
# ─────────────────────────────────────────────────────────────

def test_exact_path_resolves_to_reuse():
    """整条路径已存在 → 复用（精确匹配）。"""
    raw = {"docs": [{"id": "tx_a", "folderAction": "new", "newFolderName": "技术/前端"}]}
    out = normalize_suggestions(raw, _docs("tx_a"), _TREE, MAX_NEW_FOLDERS)
    s = out["suggestions"][0]
    assert s["folder"] == {"action": "reuse", "id": "fd_fe", "name": "前端"}, s["folder"]
    assert any("同名" in w for w in s["warnings"])


def test_single_segment_unique_name_reuses_that_folder():
    """单段名在整个库里唯一 → 复用（保留改造前「同名归并」的意图）。"""
    raw = {"docs": [{"id": "tx_a", "folderAction": "new", "newFolderName": " 前端 "}]}
    out = normalize_suggestions(raw, _docs("tx_a"), _TREE, MAX_NEW_FOLDERS)
    assert out["suggestions"][0]["folder"] == {
        "action": "reuse", "id": "fd_fe", "name": "前端"}


def test_ambiguous_same_name_is_none_with_explicit_warning():
    """同名夹有多个 → **none + 明示歧义**，绝不按数组顺序取第一个。

    对照（已验证有区分力）：把 `parse_path` 的 `len(hits) > 1` 分支去掉、改回「取第一个」
    → 本条返回 reuse(fd_b)，`action == "none"` 断言红。
    """
    folders = [
        {"id": "fd_a", "name": "甲", "count": 0},
        {"id": "fd_b", "name": "其他", "parentId": "fd_a", "count": 0},
        {"id": "fd_c", "name": "其他", "count": 0},
    ]
    raw = {"docs": [{"id": "tx_a", "folderAction": "new", "newFolderName": "其他"}]}
    out = normalize_suggestions(raw, _docs("tx_a"), folders, MAX_NEW_FOLDERS)
    f = out["suggestions"][0]["folder"]
    assert f["action"] == "none", f
    # 歧义不是「超限被拒」，不该带候补（那会让对话框预填一个注定失败的路径）
    assert "candidate" not in f, f
    assert any("存在多个同名文件夹" in w for w in out["suggestions"][0]["warnings"])


def test_suffix_ambiguity_does_not_create_a_third_lookalike_branch():
    """模型省略了前缀（只写 `技术/前端`），而库里有两条同后缀路径 → 不许在根级再造一条。

    库里已有 `A/技术/前端` 与 `B/技术/前端`，这时按根级新建会造出第三个**长得完全正常**
    的结构 —— 用户从产物上看不出这是一次猜错，只会觉得「多了个怪夹」。故判歧义。

    ⚠️ 本用例的两个分支都**不是**根级，否则根级直接精确命中、轮不到后缀判据。
    """
    folders = [
        {"id": "fd_a", "name": "甲", "count": 0},
        {"id": "fd_a_tech", "name": "技术", "parentId": "fd_a", "count": 0},
        {"id": "fd_a_fe", "name": "前端", "parentId": "fd_a_tech", "count": 0},
        {"id": "fd_b", "name": "乙", "count": 0},
        {"id": "fd_b_tech", "name": "技术", "parentId": "fd_b", "count": 0},
        {"id": "fd_b_fe", "name": "前端", "parentId": "fd_b_tech", "count": 0},
    ]
    raw = {"docs": [{"id": "tx_a", "folderAction": "new", "newFolderName": "技术/前端"}]}
    out = normalize_suggestions(raw, _docs("tx_a"), folders, MAX_NEW_FOLDERS)
    f = out["suggestions"][0]["folder"]
    assert f["action"] == "none", f
    assert any("存在多个同名文件夹" in w for w in out["suggestions"][0]["warnings"])


def test_unique_suffix_match_stays_new_but_says_so():
    """省略前缀、后缀在库里唯一 → **仍然新建**，但把「其实指向已有的那个」说出来。

    为什么不自动复用：模型没写全前缀，归入哪一支是我们猜的，猜错的代价是一个
    **长得完全正常的错归属**。为什么不静默新建：用户看到的只是一条再普通不过的
    「新建文件夹」，无从知道模型指着一个已存在的夹。

    对照（已验证有区分力）：删掉 `resolve_new_target` 里那段 `warnings.append` →
    下面两条 warning 断言红，而 `action == "new"` 仍绿（说明它们不是被 action 顺带满足的）。
    """
    folders = [
        {"id": "fd_a", "name": "甲", "count": 0},
        {"id": "fd_a_tech", "name": "技术", "parentId": "fd_a", "count": 0},
        {"id": "fd_a_fe", "name": "前端", "parentId": "fd_a_tech", "count": 0},
    ]
    raw = {"docs": [{"id": "tx_a", "folderAction": "new", "newFolderName": "技术/前端"}]}
    out = normalize_suggestions(raw, _docs("tx_a"), folders, MAX_NEW_FOLDERS)
    s = out["suggestions"][0]
    # 行为不变：新建的是**根级**的 技术/前端，而不是归入 甲/技术/前端
    assert s["folder"] == {"action": "new", "name": "技术/前端"}, s["folder"]
    joined = " ".join(s["warnings"])
    assert "甲/技术/前端" in joined, joined
    assert "将在根级新建" in joined, joined


def test_malformed_path_is_rejected_with_a_reason():
    """空段 / 纯空白 → none + **说明原因**（静默丢弃会让用户以为模型没给建议）。"""
    raw = {"docs": [
        {"id": "tx_a", "folderAction": "new", "newFolderName": "技术//前端"},
        {"id": "tx_b", "folderAction": "new", "newFolderName": "   "},
    ]}
    out = normalize_suggestions(raw, _docs("tx_a", "tx_b"), _TREE, MAX_NEW_FOLDERS)
    assert out["suggestions"][0]["folder"] == {"action": "none"}
    assert any("不合法" in w for w in out["suggestions"][0]["warnings"])
    # 纯空白走既有的「名为空」分支（措辞与改造前一致）
    assert any("名为空" in w for w in out["suggestions"][1]["warnings"])


def test_path_deeper_than_three_levels_is_truncated_with_warning():
    """超过 3 段 → 截断并记 warning：文档仍被分类，只是层级变浅（比整篇不归入有用）。"""
    raw = {"docs": [{"id": "tx_a", "folderAction": "new",
                     "newFolderName": "一级/二级/三级/四级"}]}
    out = normalize_suggestions(raw, _docs("tx_a"), [], MAX_NEW_FOLDERS)
    s = out["suggestions"][0]
    assert s["folder"] == {"action": "new", "name": "一级/二级/三级"}, s["folder"]
    assert any("超过 3 层" in w for w in s["warnings"])


# ─────────────────────────────────────────────────────────────
# 额度 —— 按「实际新建的文件夹行数」计
# ─────────────────────────────────────────────────────────────

def test_quota_counts_folder_rows_not_paths():
    """`技术/前端` 在「技术」已存在时算 **1 行**。

    上限 2：第一条占 1 行 → 已用 1；第二条 `新领域/子类` 需要 2 行 → 3 > 2，整条拒绝。

    对照（已验证有区分力）：改回「按名字计数」时两条各算 1，都通过，本条红。
    """
    folders = [{"id": "fd_tech", "name": "技术", "count": 0}]
    raw = {"docs": [
        {"id": "tx_a", "folderAction": "new", "newFolderName": "技术/前端"},
        {"id": "tx_b", "folderAction": "new", "newFolderName": "新领域/子类"},
    ]}
    out = normalize_suggestions(raw, _docs("tx_a", "tx_b"), folders, 2)
    assert out["suggestions"][0]["folder"] == {"action": "new", "name": "技术/前端"}
    rej = out["suggestions"][1]["folder"]
    assert rej["action"] == "none" and rej["candidate"] == "新领域/子类", rej
    # 全局 warning 点名了被拒的条数（少它则用户以为模型只是建议了更少的新夹）
    assert any("超过上限" in w for w in out["warnings"]), out["warnings"]


def test_rejected_path_is_rejected_whole_not_half_built():
    """被拒的是**整条路径**：`甲` 建了、`甲/乙` 没建是最糟的形态。"""
    raw = {"docs": [{"id": "tx_a", "folderAction": "new", "newFolderName": "甲/乙"}]}
    out = normalize_suggestions(raw, _docs("tx_a"), [], 1)      # 两层却只有 1 个额度
    assert out["suggestions"][0]["folder"] == {"action": "none", "candidate": "甲/乙"}


def test_same_path_twice_pays_the_quota_once():
    """两条文档建议同一条路径 → 只付一次额度（`taken` 持久集合的作用）。

    对照（已验证有区分力）：把 `_plan_new_paths` 的 `taken.update(adds)` 去掉，
    第二条会再算一次「新领域 + 子类」，上限 2 时第二条被拒。
    """
    raw = {"docs": [
        {"id": "tx_a", "folderAction": "new", "newFolderName": "新领域/子类"},
        {"id": "tx_b", "folderAction": "new", "newFolderName": "新领域/子类"},
    ]}
    out = normalize_suggestions(raw, _docs("tx_a", "tx_b"), [], 2)
    assert [s["folder"] for s in out["suggestions"]] == [
        {"action": "new", "name": "新领域/子类"},
        {"action": "new", "name": "新领域/子类"},
    ]


# ─────────────────────────────────────────────────────────────
# 口袋夹 —— 按完整路径分组
# ─────────────────────────────────────────────────────────────

def test_misc_gate_groups_by_full_path_not_by_leaf_name():
    """`技术/杂项` 与 `生活/杂项` 互不连坐。

    8 篇：两个口袋各 2 篇、其余 4 篇 none。
    - 分组（正确）：每个口袋 2/8 = 1/4 ≤ 1/3 → 都不撤；
    - 按末段名合计（错误）：4/8 = 1/2 > 1/3 → 两个一起被撤下。

    对照（已验证有区分力）：把 `misc_groups` 的键换成末段名 → 本条前两条断言红。
    """
    ids = ("tx_a", "tx_b", "tx_c", "tx_d", "tx_e", "tx_f", "tx_g", "tx_h")
    folders = [{"id": "fd_tech", "name": "技术", "count": 0}]
    raw = {"docs": [
        {"id": "tx_a", "folderAction": "new", "newFolderName": "技术/杂项"},
        {"id": "tx_b", "folderAction": "new", "newFolderName": "技术/杂项"},
        {"id": "tx_c", "folderAction": "new", "newFolderName": "生活/杂项"},
        {"id": "tx_d", "folderAction": "new", "newFolderName": "生活/杂项"},
        {"id": "tx_e", "folderAction": "none"},
        {"id": "tx_f", "folderAction": "none"},
        {"id": "tx_g", "folderAction": "none"},
        {"id": "tx_h", "folderAction": "none"},
    ]}
    out = normalize_suggestions(raw, _docs(*ids), folders, MAX_NEW_FOLDERS)
    pockets = [s["folder"] for s in out["suggestions"] if s["folder"].get("misc")]
    assert len(pockets) == 4, pockets
    assert {p["name"] for p in pockets} == {"技术/杂项", "生活/杂项"}
    assert not any("口袋文件夹" in w for w in out["warnings"]), out["warnings"]


def test_single_pocket_over_one_third_is_still_pulled():
    """反向对照：**单个**口袋超过 1/3 时照旧整夹撤下（分组不等于放宽判据）。"""
    ids = ("tx_a", "tx_b", "tx_c", "tx_d", "tx_e", "tx_f", "tx_g", "tx_h")
    folders = [{"id": "fd_tech", "name": "技术", "count": 0}]
    raw = {"docs": [
        {"id": "tx_a", "folderAction": "new", "newFolderName": "技术/杂项"},
        {"id": "tx_b", "folderAction": "new", "newFolderName": "技术/杂项"},
        {"id": "tx_c", "folderAction": "new", "newFolderName": "技术/杂项"},
        {"id": "tx_d", "folderAction": "new", "newFolderName": "生活/杂项"},
        {"id": "tx_e", "folderAction": "none"},
        {"id": "tx_f", "folderAction": "none"},
        {"id": "tx_g", "folderAction": "none"},
        {"id": "tx_h", "folderAction": "none"},
    ]}
    out = normalize_suggestions(raw, _docs(*ids), folders, MAX_NEW_FOLDERS)
    by_id = {s["id"]: s["folder"] for s in out["suggestions"]}
    assert by_id["tx_a"] == {"action": "none", "candidate": "技术/杂项"}
    assert by_id["tx_b"]["action"] == "none" and by_id["tx_c"]["action"] == "none"
    # 生活/杂项 只有 1/8 —— 不连坐
    assert by_id["tx_d"].get("misc") is True, by_id["tx_d"]


def test_nested_misc_does_not_consume_quota():
    """嵌套口袋夹同样**不占额度**（整条路径，含它需要新建的父层）。

    对照（已验证有区分力）：去掉 `_plan_new_paths` 里的 `if is_misc_folder_name(...): continue`
    → `技术`+`技术/杂项` 占掉那 1 个额度，`甲类` 被拒。
    """
    raw = {"docs": [
        {"id": "tx_a", "folderAction": "new", "newFolderName": "甲类"},
        {"id": "tx_b", "folderAction": "new", "newFolderName": "技术/杂项"},
        {"id": "tx_c", "folderAction": "none"},
        {"id": "tx_d", "folderAction": "none"},
    ]}
    out = normalize_suggestions(raw, _docs("tx_a", "tx_b", "tx_c", "tx_d"), [], 1)
    assert out["suggestions"][0]["folder"]["action"] == "new"
    assert out["suggestions"][1]["folder"] == {
        "action": "new", "name": "技术/杂项", "misc": True}
