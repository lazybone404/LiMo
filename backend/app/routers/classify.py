"""自动分类 — POST /api/classify/preview + POST /api/classify/commit

两个端点是一对，**分工即产品决策**：

- `preview` 跑一次 LLM 调用产出「每篇 → 建议文件夹 + 建议标签」，**零写入**；
- `commit` 把用户在对话框里审定/编辑过的方案写入库，**一次加锁、一次写盘**。

⚠️ **模型永远不直接写库**——这是「先建议、确认后写入」的落点，也是为什么 preview
端点只依赖 store 的读接口。

⚠️ **为什么不扩 `POST /api/transcripts/batch` 的 action**：那个请求模型的形状是
「一个动作、一个 folderId、一组统一 tags」，表达不了「每篇各自的 folder + 各自的
tags」；硬塞进去会让它七成字段只对某一个 action 有效。而 commit 的收益与 batch
存在的理由逐字同源（一次写盘而非 N 次）——它是同一写路径的**兄弟**，不是另起炉灶。

⚠️ 字段名一律 **camelCase**（与本域的 `BatchTranscriptsRequest` 对齐：`tagMode` /
`folderId`）。`synthesis.*` 用的 snake_case 是合成域的历史写法，不跟。
"""

import asyncio

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.core import logging as log
from app.core.dependencies import get_config, get_guard, get_store
from app.core.disconnect import disconnect_watcher
from app.core.errors import AppError
from app.core.model_resolver import resolve_llm_config
from app.generation.classifier import (
    FOLDER_MODE_REUSE_OR_NEW,
    FOLDER_MODES,
    MAX_NEW_FOLDERS,
    MAX_NEW_FOLDERS_LIMIT,
    MAX_SEED_TAGS,
    MAX_TAGS_PER_DOC,
    NOTES_CHARS_DEFAULT,
    NOTES_CHARS_MAX,
    TAG_MODE_VOCAB,
    TAG_MODES,
    clamp_max_new_folders,
    clamp_notes_chars,
    clamp_seed_tags,
    classify_documents,
    clean_tag_names,
    validate_mode,
)
from app.llm import LLMService

router = APIRouter()


# ── preview ──

class ClassifyPreviewRequest(BaseModel):
    docIds: list[str] = []
    modelId: int | None = None
    # 用户选的「看多少」：0 = 仅标题。超出区间的值由 clamp_notes_chars 夹紧
    # （请求体来自网络，不能假定它合法）
    notesChars: int = NOTES_CHARS_DEFAULT

    # ── 下面四项是**用户在开始分析之前**选定的策略（见 classifier 模块的枚举注释）──
    #
    # ⚠️ 非法策略**显式报错**而非静默退回默认：选错会让产物性质完全不同，而从结果上
    #    看不出异常。这与 `notesChars` 这类「超区间就夹紧」刻意不同——那个错的是**量**，
    #    这个错的是**性质**。
    folderMode: str = FOLDER_MODE_REUSE_OR_NEW
    maxNewFolders: int = MAX_NEW_FOLDERS
    tagMode: str = TAG_MODE_VOCAB
    # 仅 `tagMode="seed"` 时有意义：用户手填的基础标签
    seedTags: list[str] = []


@router.post("/classify/preview")
async def classify_preview(
    req: ClassifyPreviewRequest,
    request: Request,
    store=Depends(get_store),
    config=Depends(get_config),
    guard=Depends(get_guard),
) -> dict:
    """为选中的文档生成一套分类建议。**零写入**、恰好一次 LLM 调用。

    ⚠️ 业务失败（没选文档 / 没配 key / 模型超时 / JSON 不合法）走 `AppError` →
    **HTTP 200 + `{code, message}`**（项目模式 #36）——前端必须检查 `body.code`，
    只看 `response.ok` 会把失败当成功，然后拿一个没有 `suggestions` 的对象去渲染。
    """
    if not req.docIds:
        raise AppError("CLASSIFY_NO_DOCS", "未指定任何文档", recoverable=True)

    llm_config = resolve_llm_config(config, req.modelId)
    # ⚠️ **第一参数是整份 config，不是 `resolve_llm_config` 的返回值**——`LLMService.__init__`
    #    自己会做 `config.get("llm", {})`。传子字典进去它取到的是空 dict，于是 `api_key`
    #    恒为空 → 每次预览都报「LLM API Key 未配置」，而**单测照样全绿**（端点用例注入的是
    #    假 LLMService，压根碰不到这个构造）。实机验证抓到的就是这一处。
    #    全项目其余 10 处调用点（chat / generate / process / synthesis / epub …）都传顶层 config。
    llm = LLMService(config, model_id=req.modelId)
    if not llm.api_key:
        raise AppError("CLASSIFY_NO_API_KEY", "LLM API Key 未配置，请在设置中填写",
                       recoverable=False)

    notes_chars = clamp_notes_chars(req.notesChars)
    folder_mode = validate_mode(req.folderMode, FOLDER_MODES,
                                FOLDER_MODE_REUSE_OR_NEW, "文件夹策略")
    tag_mode = validate_mode(req.tagMode, TAG_MODES, TAG_MODE_VOCAB, "标签策略")
    max_new = clamp_max_new_folders(req.maxNewFolders)
    seed_tags = clamp_seed_tags(req.seedTags)

    cancel_event = asyncio.Event()
    async with disconnect_watcher(request, cancel_event):
        # 与生成/处理走同一个云端信号量——分类是真实计费调用，必须占槽位
        async with guard.semaphore:
            result = await classify_documents(
                store, llm_config, llm, req.docIds, notes_chars,
                folder_mode=folder_mode,
                max_new_folders=max_new,
                tag_mode=tag_mode,
                seed_tags=seed_tags,
                cancel_event=cancel_event,
            )

    return {
        "status": "ok",
        "model": getattr(llm, "model", "") or "",
        # 上限经响应回显而非前端硬编码——对话框要显示「最多新建 N 个」时读这里。
        # ⚠️ 回显的是**本次实际生效值**（用户可把它调到 `MAX_NEW_FOLDERS_LIMIT`），
        # **不是**默认常量——前端 `ClassifyDialog` 用它拼「最多新建 N 个」的摘要，
        # 回显常量会让用户调成 8 之后仍看到「最多新建 5 个」（判据与执行不同源）。
        "limits": {
            "maxNewFolders": max_new,
            "maxNewFoldersLimit": MAX_NEW_FOLDERS_LIMIT,
            "notesChars": notes_chars,
            "notesCharsMax": NOTES_CHARS_MAX,
            "maxTagsPerDoc": MAX_TAGS_PER_DOC,
            "maxSeedTags": MAX_SEED_TAGS,
        },
        # 策略回显：前端据此知道「这一次模型到底看到了什么」（见 classifier 的枚举）
        "folderMode": folder_mode,
        "tagMode": tag_mode,
        "folders": [{"id": f["id"], "name": f.get("name") or "", "count": f.get("count", 0)}
                    for f in result["folders"]],
        "tagVocab": result["tag_vocab"],
        "suggestions": result["suggestions"],
        "warnings": result["warnings"],
    }


# ── commit ──

class ClassifyAssignment(BaseModel):
    id: str
    # "" = 未分类（移出文件夹）；非空必须是已存在的文件夹 id
    folderId: str = ""
    tags: list[str] = []


class ClassifyCommitRequest(BaseModel):
    assignments: list[ClassifyAssignment] = []


@router.post("/classify/commit")
async def classify_commit(
    req: ClassifyCommitRequest,
    store=Depends(get_store),
) -> dict:
    """写入审定后的分类方案。返回 `{status, affected, failed:[{id,error}]}`。

    ⚠️ **失败必须显式返回明细**（同批量端点）：把部分失败当全成功是这里最坏的
    失效形态——用户以为分类好了，实际有几篇原封不动。全部失败 → 400。

    ⚠️ **本端点不建文件夹**：新文件夹由前端在调它之前用现成的 `POST /api/folders`
    建好（名字是用户可改的自由文本，只有前端知道最终值）。这里只做**存在性校验**
    ——两次请求之间用户完全可能在别处把那个文件夹删了。
    """
    if not req.assignments:
        raise AppError("CLASSIFY_NO_ASSIGNMENTS", "没有要应用的分类结果",
                       status_code=400, recoverable=True)

    assignments: list[dict] = []
    failed: list[dict] = []
    seen: set[str] = set()
    for a in req.assignments:
        if a.id in seen:
            failed.append({"id": a.id, "error": "同一篇文档被指定了多次"})
            continue
        seen.add(a.id)
        if not store.get_transcript(a.id):
            failed.append({"id": a.id, "error": "文档不存在"})
            continue
        if a.folderId and not store.folder_exists(a.folderId):
            failed.append({"id": a.id, "error": "目标文件夹不存在"})
            continue
        # 名称清洗走 `clean_tag_names`（唯一内核，与 preview 侧同源）——此前这里
        # 是**第三份**内联实现，且漏了截长：同一条长标签，模型给的被截成 30 字、
        # 用户手打的原样入库（模式 #54）。commit 侧**不设条数上限**（用户主权）。
        assignments.append({"id": a.id, "folder_id": a.folderId,
                            "tags": clean_tag_names(a.tags)})

    affected = store.apply_classifications(assignments) if assignments else 0
    if affected == 0 and failed:
        raise AppError("CLASSIFY_ALL_FAILED", f"全部 {len(failed)} 条操作失败",
                       status_code=400, recoverable=True)

    log.info("应用自动分类", affected=affected, failed=len(failed))
    return {"status": "ok", "affected": affected, "failed": failed}
