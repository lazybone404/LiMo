"""文件夹（单层逻辑分类）— GET/POST/PATCH/DELETE /api/folders

⚠️ 文件夹是**纯逻辑分类**，永不映射为磁盘目录：每个文档已有独立目录
{sanitized_title}-{hex6}/（含 transcript.md / assets / process / 版本归档产物），
把文件夹做成目录会同时破坏目录命名规则、assets 相对引用与版本归档。
故 folders 只活在 index.json 里，store 层实现见 storage/store.py。
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core import logging as log
from app.core.dependencies import get_store, get_query_service
from app.core.errors import AppError

router = APIRouter()


class FolderCreateRequest(BaseModel):
    name: str


class FolderRenameRequest(BaseModel):
    name: str


@router.get("/folders")
async def list_folders(svc=Depends(get_query_service)) -> list[dict]:
    """列出全部文件夹（含各自文档数 count）。未分类是 UI 派生视图，不是实体。"""
    return svc.list_folders()


@router.post("/folders")
async def create_folder(data: FolderCreateRequest, store=Depends(get_store)) -> dict:
    """新建文件夹。允许重名——id 是身份，名字只是给用户看的标签。

    空名由 store 层抛 AppError(400)，这里不重复校验（保持单一判据）。
    """
    entry = store.create_folder(data.name)
    log.info("新建文件夹", folder_id=entry["id"], name=entry["name"])
    return entry


@router.patch("/folders/{folder_id}")
async def rename_folder(folder_id: str, data: FolderRenameRequest,
                        store=Depends(get_store)) -> dict:
    """重命名文件夹。不存在 → 404。"""
    entry = store.rename_folder(folder_id, data.name)
    if entry is None:
        # 真 404 必须显式 status_code（模式 #36）——recoverable 只影响日志级别与前端是否静默
        raise AppError("FOLDER_NOT_FOUND", "文件夹不存在",
                       status_code=404, recoverable=True)
    log.info("重命名文件夹", folder_id=folder_id, name=entry["name"])
    return entry


@router.delete("/folders/{folder_id}")
async def delete_folder(folder_id: str, store=Depends(get_store)) -> dict:
    """删除文件夹。其下文档**回落未分类，绝不删除**。不存在 → 404。

    moved = 被迁走的文档数，前端据此提示「N 个文档已移出」。
    """
    moved = store.delete_folder(folder_id)
    if moved is None:
        raise AppError("FOLDER_NOT_FOUND", "文件夹不存在",
                       status_code=404, recoverable=True)
    log.info("删除文件夹", folder_id=folder_id, moved=moved)
    return {"status": "ok", "moved": moved}
