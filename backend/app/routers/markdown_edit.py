"""
产物编辑路由 — PUT /api/markdown/{transcript_id}

notes/polished 手动编辑保存：覆盖当前版本（两版制自动保留生成版为 prev 可回退），
置 edited 标记（前端重新生成前确认用）。重新生成链路 save_markdown 不传 edited →
新 entry 无该字段 → 标记自动清除。
"""

import logging
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.dependencies import get_store
from app.core.errors import AppError
from app.core.markdown_assets import rewrite_asset_urls, unrewrite_asset_urls
from app.core.transcript_meta import parse_artifact_meta, strip_meta
from app.core.types import MarkdownView, Style, GenerateMode
from app.generation.prompts import normalize_style

logger = logging.getLogger(__name__)
router = APIRouter()

_EDITABLE_MODES = {"notes", "polished"}
# 编辑内容上限（字符数）：普通产物 <100k，内嵌 base64 图 ~2M，5M 足够宽松且防误提交
_MAX_CONTENT_CHARS = 5_000_000


class MarkdownEditRequest(BaseModel):
    mode: str
    content: str


# ⚠️ 正/反两个改写（`assets/x` ↔ `/api/assets/{tx}/x`）都已下沉 `core/markdown_assets.py`
# （P2-18 收敛）——它们是一对，此前分居 `storage/store.py` 与**本文件**：
# 「改了正向忘了反向」不会有任何报错，只表现为磁盘上悄悄多出一批绝对 URL，
# 直到导出 / 二次渲染才开始裂图。见 `unrewrite_asset_urls` docstring 里的两条约定
# （只反转**本文档**的引用 / 不碰 `<img>`）。


@router.put("/markdown/{transcript_id}")
async def edit_markdown(transcript_id: str, req: MarkdownEditRequest,
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

    # 保留原 style（编辑不改样式语义；旧 entry 缺省 minimal）
    # ⚠️ **必须先归一再构造**：历史产物里存的是旧别名（`meeting_minutes`），直接
    # `Style()` 会落进 except → 静默改写成 minimal（用户的「会议纪要」变「极简」，
    # 零报错）。这与 `routers/generate.py::_normalize_and_validate_style` 是**同一条
    # 纪律的写侧/读侧**：`STYLE_ALIAS` 的兼容必须在**两个方向**都生效（模式 #52：
    # 成对操作漏一半，结果与完全没做一样）。
    style = "minimal"
    try:
        style = Style(normalize_style(mds[0].get("style", "minimal"))).value
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
    head, head_corrupt = parse_artifact_meta(mds[0].get("content") or "")
    if head_corrupt:
        # 库里已有的头是坏的：不把坏 JSON 再写回去（留着只会让下游继续读到 corrupt），
        # 本次编辑顺手清理并留 warning——降级要看得见，不静默。
        logger.warning("编辑保存：已有自证头损坏，本次重写不带头 tx=%s mode=%s",
                       transcript_id, req.mode)
        head = None

    mv = MarkdownView(
        id=f"md_{uuid.uuid4().hex}", transcript_id=transcript_id,
        content=body, style=style, mode=GenerateMode(req.mode),
    )
    if not store.save_markdown(mv, file_meta=head, index_extra={"edited": True}):
        raise AppError("MD_EDIT_SAVE_FAILED", "保存失败，请重试", recoverable=True)

    # 返回对齐 GET /api/markdown：最新 entry + versions + edited
    mds = store.get_markdowns_by_transcript(transcript_id, mode=req.mode)
    latest = mds[0] if mds else None
    if not latest:
        raise AppError("MD_EDIT_SAVE_FAILED", "保存后未找到产物，请重试", recoverable=True)
    result = {
        **latest,
        "edited": True,
        "versions": [
            {"version": m.get("version", 1), "id": m.get("id"),
             "createdAt": m.get("createdAt"), "status": m.get("status", "complete")}
            for m in mds
        ],
    }
    if result.get("content"):
        result["content"] = rewrite_asset_urls(result["content"], transcript_id)
    return result
