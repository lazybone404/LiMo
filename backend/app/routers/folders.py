"""文件夹（可嵌套的逻辑分类）— GET/POST/PATCH/DELETE /api/folders + POST /api/folders/ensure-path

⚠️ 文件夹是**纯逻辑分类**，永不映射为磁盘目录：每个文档已有独立目录
{sanitized_title}-{hex6}/（含 transcript.md / assets / process / 版本归档产物），
把文件夹做成目录会同时破坏目录命名规则、assets 相对引用与版本归档。
故 folders 只活在 index.json 里，store 层实现见 storage/store.py。

层级用条目的 `parentId` 表达（`""` = 根）。树的推导、环的打断、路径解析全部住在
`app/storage/folder_tree.py`——本模块只做 HTTP 形状，**一份判据都不复刻**。
"""

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core import logging as log
from app.core.dependencies import get_store, get_query_service
from app.core.errors import AppError

router = APIRouter()


class FolderCreateRequest(BaseModel):
    name: str
    # `""` = 根级。缺省即根级 ⇒ 老前端（只发 name）行为完全不变
    parentId: str = ""


class FolderRenameRequest(BaseModel):
    # 两项都可选：只改名 = 原地重命名，只给 parentId = 移动，两项都给 = 一起改
    name: Optional[str] = None
    parentId: Optional[str] = None


class FolderEnsurePathRequest(BaseModel):
    path: str
    # 非空时路径在该父级下逐段 ensure（不做全库范围的「整串命中 / 同名歧义」判定）
    parentId: str = ""


@router.get("/folders")
async def list_folders(svc=Depends(get_query_service)) -> list[dict]:
    """列出全部文件夹。

    每项带 `parentId`（规范化后）/ `count`（直接子级）/ `totalCount`（含子树）。
    未分类是 UI 派生视图，不是实体，故不在这个列表里。
    """
    return svc.list_folders()


@router.post("/folders")
async def create_folder(data: FolderCreateRequest, store=Depends(get_store)) -> dict:
    """新建文件夹（单层）。允许重名——id 是身份，名字只是给用户看的标签。

    空名 / 父级不存在由 store 层抛 AppError，这里不重复校验（保持单一判据）。
    ⚠️ 多层路径**不走这里**——逐层调本端点会是 N 次独立事务，任一层失败时前几层
    已经落盘了。多层统一走下面的 `ensure-path`。
    """
    entry = store.create_folder(data.name, data.parentId)
    log.info("新建文件夹", folder_id=entry["id"], name=entry["name"],
             parent_id=entry["parentId"])
    return entry


@router.post("/folders/ensure-path")
async def ensure_folder_path(data: FolderEnsurePathRequest,
                             store=Depends(get_store)) -> dict:
    """确保一条路径存在（`技术/前端` → 缺哪层建哪层，已有则复用）。

    返回 `{id, segments, created}`。幂等：同一条路径重复调用不新建。
    ⚠️ **一次锁、一次写盘**（见 store 侧）：任何一段非法时**一个字节都不写**，
    不会留下「分类失败但库里多了两个空夹」。
    """
    result = store.ensure_folder_path(data.path, data.parentId)
    if result["created"]:
        log.info("按路径新建文件夹", path=data.path, created=len(result["created"]),
                 folder_id=result["id"])
    return result


@router.patch("/folders/{folder_id}")
async def rename_folder(folder_id: str, data: FolderRenameRequest,
                        store=Depends(get_store)) -> dict:
    """重命名 / 移动文件夹。两项都不给 → 400；不存在 → 404；移动成环 → 400。"""
    entry = store.rename_folder(folder_id, data.name, data.parentId)
    if entry is None:
        # 真 404 必须显式 status_code（模式 #36）——recoverable 只影响日志级别与前端是否静默
        raise AppError("FOLDER_NOT_FOUND", "文件夹不存在",
                       status_code=404, recoverable=True)
    log.info("更新文件夹", folder_id=folder_id, name=entry["name"],
             parent_id=entry.get("parentId", ""))
    return entry


@router.delete("/folders/{folder_id}")
async def delete_folder(folder_id: str, store=Depends(get_store)) -> dict:
    """删除文件夹**及其整棵子树**。各层文档**回落未分类，绝不删除**。不存在 → 404。

    返回 `{status, moved, deletedFolders, deletedFolderIds}`：
    - `moved` = 被迁回未分类的文档数（含所有后代层的）
    - `deletedFolders` = 连带删掉的文件夹行数（含自身）
    - `deletedFolderIds` = 这些文件夹的 id —— **前端本地回落的唯一依据**。
      让前端自己拿 `descendantIds` 再算一遍，「哪些文档要回落」就有了第二个真相来源。
    """
    result = store.delete_folder(folder_id)
    if result is None:
        raise AppError("FOLDER_NOT_FOUND", "文件夹不存在",
                       status_code=404, recoverable=True)
    log.info("删除文件夹", folder_id=folder_id, moved=result["moved"],
             deleted_folders=result["deletedFolders"])
    return {"status": "ok", **result}
