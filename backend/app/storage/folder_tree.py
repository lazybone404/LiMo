"""文件夹树的纯函数层 —— 树形结构与路径解析的唯一实现（后端）。

⚠️ 前端有一份同构实现（`frontend/src/components/doc-library/folderTree.ts`），
因为 Python 无法被 TS import。两份靠**同一组镜像 fixture** 钉住
（`backend/tests/fixtures/folder-tree.json` ↔ `frontend/src/test/fixtures/folder-tree.json`）。

五条不变量（与 `folderTree.ts` 文件头**逐字同源**）：
1. 缺 `parentId` / 指向不存在的 id / 指向自己 → 视为根。
2. 环：沿 parent 上行检测，在环中 **folders 数组下标最小**的节点处断开，令其为根
   （确定性，不是随机）。
3. 兄弟顺序 = folders 数组顺序；前序遍历保证父一定排在子之前。
4. `descendant_ids` 不含自身；`totalCount` = 直接文档数 + 所有后代求和。
5. 未分类不是实体，永不在树里，固定排在列表最后。

⚠️ **`parse_path` 只活在这里**：前端不实现路径解析，建夹统一走
`POST /api/folders/ensure-path`（把原始串 + folders 上下文整个交给后端）。
两份实现要靠 fixture 防漂移；一份都没有，漂移就不可能发生。

⚠️ 本模块零依赖（只用标准库），因为它要被 store / QueryService / classifier /
路由同时 import —— 任何一方引入的依赖都会变成其他三方的依赖。
"""

import re
from datetime import datetime
from typing import Optional
from uuid import uuid4

ROOT = ""

# **智能分类**新建路径最多 3 段 = 一个主文件夹 + 两级子文件夹（prompt 引导）。
# 超出时**由调用方决定**：classifier 截断 + warning（文档仍被分类，只是层级变浅；
# ⚠️ 已有的更深结构不往回退，见 `classifier.resolve_new_target`），ensure-path 端点
# 显式 400（用户可改）。
#
# ⚠️ 这只是**分类建议**的上限，不是树的深度上限——用户手动建夹可以任意深（每层
# 走 `create_folder`，它是单层的）。
MAX_PATH_SEGMENTS = 3

# 单段名字上限。取 80 与 `prompt.sanitize_reference_name` 的截断长度**同值**——
# 那里是展示侧的硬截断，这里是入口侧的拒绝线，两者对齐才不会出现
# 「prompt 里看着 80 字，存进去 200 字」。
MAX_SEGMENT_LEN = 80

_WS_RE = re.compile(r"\s+")


# ── 名字归一（匹配专用）──

def path_key(name: str) -> str:
    """匹配专用的名字归一：`strip` + 空白折叠 + `casefold`。

    ⚠️ 刻意**不含**任何结构替换与截断。`generation/prompt.py::sanitize_reference_name`
    做的是 `"→'`、`【】→[]`、截 80 字 —— 那是为了安全嵌进 prompt 的结构标记；
    拿它当匹配 key 会让名字里带 `【】` 或超过 80 字的**老文件夹永远匹配不上**，
    而那批「当年随手起的名字」正是历史数据保护要保住的对象。
    它继续只用在 `_folder_section` 的展示一侧。
    """
    return _WS_RE.sub(" ", (name or "").strip()).casefold()


# ── 构造点 ──

def make_folder_entry(name: str, parent_id: str = "") -> dict:
    """文件夹条目的**唯一构造点**。

    ⚠️ 条目形状有三个落点：本函数 / `frontend/src/store/types.ts` 的 `FolderEntry` /
    `frontend/src/api/core.ts` 的 `FolderEntry`。后端没有 dataclass，所以靠
    「构造只此一处」保证它不会从两个地方长出两种形状（模式 #54 的同源判据）。
    """
    return {
        "id": f"fd_{uuid4().hex[:12]}",
        "name": (name or "").strip(),
        "parentId": parent_id or ROOT,
        "createdAt": datetime.now().isoformat(),
    }


# ── 树结构 ──

def normalize_parents(folders: list[dict]) -> dict[str, str]:
    """`id → 规范化后的 parentId`（`""` = 根）。

    两条归化在这里一次做完（不变量 1、2）：悬空 / 自引用 → 根；环 → 断在数组下标最小者。
    **原始脏值仍留在 index.json 里**（便于人工抢救），只有读路径看规范化后的视图。
    """
    by_id = {f["id"]: f for f in folders}
    order = {f["id"]: i for i, f in enumerate(folders)}
    parent: dict[str, str] = {}
    for f in folders:
        fid = f["id"]
        pid = f.get("parentId") or ROOT
        parent[fid] = pid if (pid and pid != fid and pid in by_id) else ROOT

    # 断环。每个环只断一点就足够破坏它，断点取环中 **folders 数组下标最小**的那个
    # ——确定性是关键：随机/取决于遍历顺序会让每次启动树形不同，用户会以为夹子乱跳。
    for start in list(parent):
        seen: dict[str, int] = {}
        path: list[str] = []
        cur = start
        while cur and cur not in seen:
            seen[cur] = len(path)
            path.append(cur)
            cur = parent[cur]
        if cur and cur in seen:
            cycle = path[seen[cur]:]
            parent[min(cycle, key=lambda n: order[n])] = ROOT
    return parent


def children_map(folders: list[dict],
                 parent: Optional[dict[str, str]] = None) -> dict[str, list[str]]:
    """`parentId → 有序子 id 列表`（不变量 3：顺序 = folders 数组顺序）。

    传 `parent` 可复用已算好的结果（`paths` / `preorder` 会连着调）。
    """
    parent = normalize_parents(folders) if parent is None else parent
    out: dict[str, list[str]] = {}
    for f in folders:
        out.setdefault(parent[f["id"]], []).append(f["id"])
    return out


def preorder(folders: list[dict],
             parent: Optional[dict[str, str]] = None) -> list[str]:
    """前序遍历。父一定排在子之前 —— `DocList` 的扁平数组靠这条一次扫描就够。"""
    parent = normalize_parents(folders) if parent is None else parent
    kids = children_map(folders, parent)
    out: list[str] = []
    stack = list(reversed(kids.get(ROOT, [])))
    while stack:
        fid = stack.pop()
        out.append(fid)
        stack.extend(reversed(kids.get(fid, [])))
    return out


def descendant_ids(folders: list[dict], folder_id: str,
                   parent: Optional[dict[str, str]] = None) -> set[str]:
    """全部后代 id（**不含自身**，不变量 4）。"""
    parent = normalize_parents(folders) if parent is None else parent
    kids = children_map(folders, parent)
    out: set[str] = set()
    stack = list(kids.get(folder_id, []))
    while stack:
        cur = stack.pop()
        if cur in out:
            continue
        out.add(cur)
        stack.extend(kids.get(cur, []))
    out.discard(folder_id)
    return out


def ancestor_ids(folders: list[dict], folder_id: str,
                 parent: Optional[dict[str, str]] = None) -> list[str]:
    """祖先 id，**从根到直接父**。路径拼接与「自动展开祖先」都直接消费这个顺序。"""
    parent = normalize_parents(folders) if parent is None else parent
    out: list[str] = []
    seen: set[str] = set()
    cur = parent.get(folder_id, ROOT)
    while cur and cur not in seen:
        seen.add(cur)
        out.append(cur)
        cur = parent.get(cur, ROOT)
    out.reverse()
    return out


def paths(folders: list[dict],
          parent: Optional[dict[str, str]] = None) -> dict[str, str]:
    """`id → 路径标签`（`父 › 子`）。

    ⚠️ 分隔符用 `›` 而不是 `/`：文件夹名本身可以含 `/`（历史数据里就有），
    用 `/` 会让「名字叫 前端/后端 的夹」和「前端 下的 后端」在界面上无法区分。
    """
    parent = normalize_parents(folders) if parent is None else parent
    by_id = {f["id"]: f for f in folders}
    out: dict[str, str] = {}
    for f in folders:
        names = [by_id[a]["name"] for a in ancestor_ids(folders, f["id"], parent)]
        names.append(f["name"])
        out[f["id"]] = " › ".join(names)
    return out


def total_counts(folders: list[dict], direct_counts: dict[str, int],
                 parent: Optional[dict[str, str]] = None) -> dict[str, int]:
    """`id → 含子树的总文档数`（不变量 4）。

    `direct_counts` 是各夹**直接**挂的文档数（`Store.list_folders` 的 `count`）。
    沿前序逆序把子级总量加到父级 —— 前序保证后代总在祖先之后，反转后后代先处理。
    """
    parent = normalize_parents(folders) if parent is None else parent
    total = {f["id"]: int(direct_counts.get(f["id"], 0)) for f in folders}
    for fid in reversed(preorder(folders, parent)):
        p = parent[fid]
        if p:
            total[p] += total[fid]
    return total


def folder_scope(folders: list[dict], folder_id: str) -> set[str]:
    """`{folder_id} ∪ 全部后代` —— 筛选「含子文件夹」时的允许集合。"""
    return {folder_id} | descendant_ids(folders, folder_id)


# ── 路径解析（唯一一份实现）──

def parse_path(raw: str, folders: Optional[list[dict]] = None) -> dict:
    """把模型/用户给的路径串解析成结构化结果。

    优先级（§5.1，唯一一份规则）：

    1. **整串先匹配某个现有文件夹名** → `existing`。这一条是为历史数据设的：
       库里本来就有个夹叫 `前端/后端` 时，它不该被拆成两层。
       命中多个同名夹 → `ambiguous`（**绝不按数组顺序取第一个** —— 那会让
       「存在多个同名文件夹」这个事实在界面上完全不可见）。
    2. 否则按 `/` 切分、逐段 `trim` → `path`。

    返回 `kind`：
      - `"existing"`  : `folderId` 唯一确定
      - `"ambiguous"` : `candidates` 有多个，`folderId` 为 `None`
      - `"path"`      : `segments` 逐段（**长度可能 > MAX_PATH_SEGMENTS**，
                        由调用方决定截断还是拒绝 —— 分类侧截断、ensure-path 拒绝）
      - `"reject"`    : `reason ∈ {"empty", "empty_segment", "segment_too_long"}`

    `segments` 恒为列表（reject 时是已切出来的部分，便于排查）。

    ⚠️ **`segments` 在两个分支里是两种语义**（同一个字段，消费者必须看 `kind`）：
      - `kind == "existing"`：`segments` 是**整串名字的一项**（`["前端/后端"]`），
        因为它命中的是一个字面就叫这个名字的夹，**不是**两层结构。
        `ensure_folder_path` 的 `parent_id` 非空分支正是靠这一点：它跳过 `existing`
        短路、拿 `segments` 逐段 ensure，于是在指定父级下建出的仍是**单层**的
        「前端/后端」夹（与根级 `existing` 的语义一致）。
      - `kind == "path"`：`segments` 才是**按 `/` 切开的逐层名字**（`["前端", "后端"]`），
        长度可能超过 `MAX_PATH_SEGMENTS`，由调用方决定截断（分类）还是拒绝（ensure-path）。

    写错这一点的失效方式：把 `existing` 的 `segments` 当逐层路径再切一次，
    历史里本来叫「前端/后端」的夹会在重建后被拆成两层，而结果看起来完全正常。
    """
    text = (raw or "").strip()
    if not text:
        return {"kind": "reject", "reason": "empty", "segments": [], "folderId": None}

    if folders:
        key = path_key(text)
        hits = [f for f in folders if path_key(f.get("name")) == key]
        if len(hits) == 1:
            return {"kind": "existing", "segments": [hits[0]["name"]],
                    "folderId": hits[0]["id"]}
        if len(hits) > 1:
            return {"kind": "ambiguous", "segments": [text], "folderId": None,
                    "candidates": [f["id"] for f in hits]}

    segments = [s.strip() for s in text.split("/")]
    if any(not s for s in segments):
        return {"kind": "reject", "reason": "empty_segment", "segments": segments,
                "folderId": None}
    if any(len(s) > MAX_SEGMENT_LEN for s in segments):
        return {"kind": "reject", "reason": "segment_too_long", "segments": segments,
                "folderId": None}
    return {"kind": "path", "segments": segments, "folderId": None}
