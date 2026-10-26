"""
产物编辑路由 — PUT /api/markdown/{transcript_id}

notes/polished 手动编辑保存 = **就地覆盖前端正在看的那一版**（`{mode}/v{N}.md`），
**不新建版本**。产品模型（2026-10-10 定）：版本 = AI 生成出来的东西，人工编辑是
「用户在他正看的那一版上做更改，我们不负责追踪编辑历史」。

保存时在自证头里打 `edit` 标记（前端重新生成前确认用）。重新生成走 `save_markdown`
且**不带**这个键 → 新版本的头里没有 `edit` → 标记自动清除（无需额外逻辑）。
⚠️ 标记落在**文件头**而不是 index 条目：「已编辑」曾在 index 里，删一次 index.json
重建就永久丢失。
"""

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.core.dependencies import get_store
from app.core.errors import AppError
from app.core.markdown_assets import rewrite_asset_urls, unrewrite_asset_urls
from app.core.search import update_index
from app.core.transcript_meta import mark_edited, parse_artifact_meta, strip_meta
from app.core.types import MarkdownView, Style, GenerateMode
from app.generation.prompts import normalize_style
from app.services.query import QueryService
from app.storage.store import MARKDOWN_MODES

logger = logging.getLogger(__name__)
router = APIRouter()

# 可编辑的 mode = 全部产物 mode。**与 store 同源**（此前这里另写了一份
# `{"notes", "polished"}`）：两侧漂移的症状是「新 mode 有产物、UI 上也有编辑入口，
# 后端却 400」，而**没有任何报错指向这里**（模式 #54 的典型形态）。
_EDITABLE_MODES = set(MARKDOWN_MODES)
# 编辑内容上限（字符数）：普通产物 <100k，内嵌 base64 图 ~2M，5M 足够宽松且防误提交
_MAX_CONTENT_CHARS = 5_000_000


class MarkdownEditRequest(BaseModel):
    mode: str
    content: str
    # 要覆盖的版本号；0 / 缺省 = 最新版（向后兼容旧前端）。
    # ⚠️ 前端送的是 `markdownMeta.version`（它正在看的那一版），不是「最新版」——
    # 用户切到 v1 再编辑，改的必须是 v1。
    version: int = 0


# ⚠️ 正/反两个改写（`assets/x` ↔ `/api/assets/{tx}/x`）都已下沉 `core/markdown_assets.py`
# （P2-18 收敛）——它们是一对，此前分居 `storage/store.py` 与**本文件**：
# 「改了正向忘了反向」不会有任何报错，只表现为磁盘上悄悄多出一批绝对 URL，
# 直到导出 / 二次渲染才开始裂图。见 `unrewrite_asset_urls` docstring 里的两条约定
# （只反转**本文档**的引用 / 不碰 `<img>`）。


@router.put("/markdown/{transcript_id}")
async def edit_markdown(transcript_id: str, req: MarkdownEditRequest, request: Request,
                        store=Depends(get_store)) -> dict:
    """保存 notes/polished 的手动编辑：覆盖当前版本 + 置 edited 标记。"""
    if req.mode not in _EDITABLE_MODES:
        raise AppError("MD_EDIT_BAD_MODE", "仅支持编辑笔记（notes）/整理版原文（polished）",
                       recoverable=False)

    if not store.get_transcript(transcript_id):
        raise AppError("MD_EDIT_NOT_FOUND", "文档不存在", recoverable=False)

    if not req.content or not req.content.strip():
        raise AppError("MD_EDIT_EMPTY", "内容不能为空", recoverable=False)

    if len(req.content) > _MAX_CONTENT_CHARS:
        raise AppError("MD_EDIT_TOO_LARGE", "内容过大，请精简后再保存", recoverable=False)

    # 编辑语义是「覆盖已有产物」——transcript 存在但无产物时拒绝（前端无产物不显示编辑入口，
    # 此校验为 API 层防御，防直接调用创建出无源产物）
    mds = store.get_markdowns_by_transcript(transcript_id, mode=req.mode)
    if not mds:
        raise AppError("MD_EDIT_NO_PRODUCT", "该文档还没有可编辑的产物", recoverable=False)

    # ── 选中要覆盖的版本 ──
    # 缺省是最新版（`mds` 已按版本降序）；显式版本查不到**不回退最新**。
    # ⚠️ 这里的纪律与 `generate.py::get_markdown` 的版本选择同源，但理由更硬：
    # 那是**读**，最坏是看到第 2 版；这是**写**，「我要改第 1 版」却改掉了第 3 版
    # 无法靠再切一次视图挽回——用户不会发现自己改错了地方。
    target = mds[0]
    if req.version:
        picked = next((m for m in mds if m.get("version") == req.version), None)
        if not picked:
            # ⚠️ `recoverable=False` 与 `status_code=404` **两个都要显式给**：前者
            # 默认 True 会让响应变成 HTTP 200（按状态码判会把「找不到」读成成功），
            # 后者不传则落到默认 400（模式 #36）。
            raise AppError("MD_EDIT_NO_VERSION", f"版本 {req.version} 不存在",
                           recoverable=False, status_code=404)
        target = picked
    version = target.get("version", 1)

    # 保留原 style（编辑不改样式语义；旧 entry 缺省 minimal）
    # ⚠️ **必须先归一再构造**：历史产物里存的是旧别名（`meeting_minutes`），直接
    # `Style()` 会落进 except → 静默改写成 minimal（用户的「会议纪要」变「极简」，
    # 零报错）。这与 `routers/generate.py::_normalize_and_validate_style` 是**同一条
    # 纪律的写侧/读侧**：`STYLE_ALIAS` 的兼容必须在**两个方向**都生效（模式 #52：
    # 成对操作漏一半，结果与完全没做一样）。
    style = "minimal"
    try:
        style = Style(normalize_style(target.get("style", "minimal"))).value
    except ValueError:
        style = "minimal"

    content = unrewrite_asset_urls(req.content, transcript_id)

    # ⚠️ 自证头是**系统管理**的首行，不属于用户的编辑对象（预览区不渲染它，只有
    # 源码视图可见）。编辑保存的两条规矩：
    #   ① 前端送来的正文里若带着旧头，**一律先剥掉**——以库里已有的头为准，不采纳
    #      编辑器里那份可能被误改的副本；
    #   ② 编辑**不改变** provenance（源、生成方式、可信边界），所以头原样带回。
    # 丢掉头的后果很具体：一次编辑就把产物打回「不可自证」，正是本轮要修的缺陷。
    body = strip_meta(content)
    head, head_corrupt = parse_artifact_meta(target.get("content") or "")
    if head_corrupt:
        # 库里已有的头是坏的：不把坏 JSON 再写回去（留着只会让下游继续读到 corrupt），
        # 本次编辑顺手清理并留 warning——降级要看得见，不静默。
        logger.warning("编辑保存：已有自证头损坏，本次重写不带头 tx=%s mode=%s",
                       transcript_id, req.mode)
        head = None
    # 叠上编辑标记（生成路径不写这个键，故重新生成后它自然消失）
    head = mark_edited(head, datetime.now().isoformat(timespec="seconds"))

    mv = MarkdownView(
        id=f"md_{uuid.uuid4().hex}", transcript_id=transcript_id,
        content=body, style=style, mode=GenerateMode(req.mode),
    )
    # `replace_version` = 覆盖这一版；版本号**不变**（这正是「编辑不产生版本」）。
    if not store.save_markdown(mv, file_meta=head, replace_version=version):
        raise AppError("MD_EDIT_SAVE_FAILED", "保存失败，请重试", recoverable=True)

    # 笔记正文变了 → 全文索引跟着更新。漏了这行的症状很具体：用户刚在笔记里写了
    # 一个新词，转头搜它搜不到（索引里还是编辑前那份），而「保存成功」看起来完全正常。
    update_index(getattr(request.app.state, "search_engine", None), store, transcript_id,
                 vec=getattr(request.app.state, "vector_store", None))

    # 返回对齐 GET /api/markdown：**被编辑的那一版** + 完整版本列表。
    # ⚠️ 返回编辑的版本而不是 `mds[0]`（最新）：用户可能正停在 v1 上编辑，返回最新版
    # 会让预览区内容在他眼皮底下跳变。
    mds = store.get_markdowns_by_transcript(transcript_id, mode=req.mode)
    edited_entry = next((m for m in mds if m.get("version") == version), None)
    if not edited_entry:
        raise AppError("MD_EDIT_SAVE_FAILED", "保存后未找到产物，请重试", recoverable=True)
    result = {
        **edited_entry,
        # `edited` 不在这里写死 True：它由 `get_markdowns_by_transcript` 从文件头
        # **派生**（`**edited_entry` 已含）。写死等于在响应里放第二份判据。
        "versions": QueryService.artifact_versions(mds),
    }
    if result.get("content"):
        result["content"] = rewrite_asset_urls(result["content"], transcript_id)
    return result
