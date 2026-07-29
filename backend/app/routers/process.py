"""
文档处理 — 导入后按模式二次处理（OCR/表格/数字化/理解）

当前支持图片（4 mode），未来扩展到 PDF/Document Layer 2。
处理结果持久化到 Store，对标 generate 的 MarkdownView 链路。

调度优先级：请求 model_id → scene_models.image_process → scene_models.generate → Engine 回退
"""
import asyncio
import base64
import copy
import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Request, Depends
from pydantic import BaseModel

from app.core.errors import AppError, TranscribeError
from app.core import logging as log

from app.core.registry import EngineRegistry
from app.core.config import build_engine_models
from app.core.dependencies import get_store, get_config, get_guard
from app.core.types import ProcessResult
from app.llm import LLMService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["process"])

# 图片处理 prompt — 按模式选择
IMAGE_PROMPTS = {
    "default": (
        "请提取并输出这张图片中的全部文字内容。\n"
        "规则：\n"
        "1. 保留原始段落结构和换行\n"
        "2. 如果图片中有表格，用 Markdown 表格格式输出\n"
        "3. 忽略编辑器工具栏、菜单栏、状态栏、窗口边框等软件界面元素\n"
        "4. 忽略与正文内容无关的界面文字（如应用名、按钮标签、提示信息）\n"
        "5. 只输出文字，不要添加任何解释、说明或对话\n"
        "6. 如果图片中没有文字，回复「无文字内容」"
    ),
    "table": (
        "请提取并输出这张图片中的表格数据。\n"
        "规则：\n"
        "1. 只提取数据表格本身——忽略编辑器工具栏、菜单栏、状态栏、窗口边框等界面元素\n"
        "2. 忽略与表格数据无关的任何文字（如应用程序名称、按钮标签、提示信息）\n"
        "3. 用 Markdown 表格格式输出所有表格\n"
        "4. 表格前后可以用简短文字说明表格标题（仅当标题属于数据内容时）\n"
        "5. 如果表格中有合并单元格，尽量还原为多行\n"
        "6. 如果图片中没有数据表格，回复「未检测到表格数据」\n"
        "7. 只输出结果，不要添加任何解释或对话"
    ),
    "digitize": (
        "请将这张扫描件/文档图片转换为可编辑的 Markdown 文本。\n"
        "规则：\n"
        "1. 完整提取所有文字内容，不要遗漏\n"
        "2. 保留原始文档结构：标题层级（用 #/##/###）、列表、缩进、分隔线\n"
        "3. 表格用 Markdown 表格格式输出\n"
        "4. 扫描噪声导致的文字歧义请根据上下文纠正，标注不确定处为 [?]\n"
        "5. 保留页码、页眉、页脚信息（如有）\n"
        "6. 只输出结果，不要添加任何解释或对话"
    ),
    "understand": (
        "请描述并解读这张图片的内容。\n"
        "规则：\n"
        "1. 先概括画面整体内容（1-2句）\n"
        "2. 然后按要点列出关键信息和细节\n"
        "3. 如果图片中有文字，提取并融入描述中\n"
        "4. 如果图片中有数据/图表，解读其含义\n"
        "5. 用 Markdown 格式输出，不要添加对话"
    ),
}


class ProcessRequest(BaseModel):
    mode: str = "extract"  # extract | table | digitize | understand
    model_id: int | None = None  # LLM 模型 ID（enabled_models），None=走场景默认或引擎回退
    source_type: str = ""  # 文档类型（image/document），空=自动检测


@router.post("/process/{transcript_id}")
async def process_document(transcript_id: str, data: ProcessRequest, request: Request, store=Depends(get_store), config=Depends(get_config), guard=Depends(get_guard)):
    """处理已导入的文档：OCR / 表格 / 数字化 / 内容理解。
    当前支持图片，未来扩展到 PDF Layer 2。"""
    async with guard.semaphore:

        # 客户端断连监听（LLM 调用会检查 cancel_event 停止 token 消耗）
        cancel_event = asyncio.Event()
        async def _watch_disconnect():
            while not cancel_event.is_set():
                if await request.is_disconnected():
                    cancel_event.set()
                    return
                await asyncio.sleep(0.5)
        disconnect_task = asyncio.create_task(_watch_disconnect())

        # 查找 transcript
        tx = store.get_transcript(transcript_id)
        if not tx:
            raise AppError("PROC_NOT_FOUND", "文档不存在，请先导入图片")

        # 解析图片路径
        try:
            meta = json.loads(tx.get("rawText", "{}"))
        except json.JSONDecodeError:
            raise AppError("PROC_BAD_META", "文档元数据损坏，请重新导入")

        doc_type = data.source_type or meta.get("type", "unknown")
        if doc_type not in ("image",):  # 未来扩展: "document" 支持 PDF Layer 2
            raise AppError("PROC_NOT_SUPPORTED",
                          f"当前仅支持图片处理，不支持类型: {doc_type}")

        file_path = meta.get("file_path", "") or tx.get("file_path", "")
        if not file_path or not Path(file_path).exists():
            raise AppError("PROC_FILE_MISSING", "图片文件不存在，请重新导入")

        mode = data.mode
        logger.info("图片处理: %s mode=%s", Path(file_path).name, mode)

        # ── 解析 model_id：请求参数 → image_process 场景 → generate 场景 ──
        model_id = data.model_id
        if not model_id:
            scene_models = config.get("scene_models", {})
            model_id = scene_models.get("image_process") or scene_models.get("generate")

        llm_result = None
        llm_error = None
        degradation_notes: list[str] = []
        llm_degraded = False

        # ── LLM 路径（使用统一的 LLMService）──
        if model_id:
            llm = LLMService(config, model_id=model_id)
            if not llm.api_key:
                logger.warning("图片处理: model_id=%s 缺少 API Key，回退 Engine", model_id)
                llm_degraded = True
            elif not llm.supports_vision:
                logger.info("图片处理: model=%s 不支持图片，回退 Engine", llm.model)
                llm_degraded = True
            else:
                try:
                    prompt_mode = "default" if mode == "extract" else mode
                    prompt_text = IMAGE_PROMPTS.get(prompt_mode, IMAGE_PROMPTS["default"])

                    # 编码图片
                    path = Path(file_path)
                    ext = path.suffix.lower()
                    mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                                ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp"}
                    mime = mime_map.get(ext, "image/png")
                    image_b64 = base64.b64encode(path.read_bytes()).decode()
                    data_url = f"data:{mime};base64,{image_b64}"

                    text = await llm.call(
                        prompt_text,
                        image_urls=[data_url],
                        max_tokens=4096, temperature=0.1,
                        max_continuations=0,
                        cancel_event=cancel_event,
                    )

                    if text and text.strip():
                        llm_result = {"text": text.strip(), "engine": f"{llm.provider}:{llm.model}"}
                        logger.info("LLM 图片处理完成: %d 字, engine=%s", len(text), llm_result["engine"])
                    else:
                        logger.warning("LLM 返回空内容，回退 Engine")
                        llm_error = "LLM 返回空内容"
                        llm_degraded = True
                except Exception as e:
                    logger.warning("LLM 图片处理失败，回退到 Engine: %s", e)
                    llm_error = str(e)[:200]
                    llm_degraded = True

        if llm_result:
            text = llm_result["text"]
            engine_name = llm_result["engine"]
        elif llm_degraded and not config.get("engine_auto_fallback", True):
            raise AppError("PROC_NO_FALLBACK",
                          "多模态 LLM 不可用，自动降级已关闭（可在设置中开启引擎自动降级）",
                          recoverable=False)
        else:
            # ── Engine 回退（组合调度，按 engine_bindings.ocr 优先级链） ──
            engine_models = build_engine_models(config)
            engine_providers = config.get("engine_providers", [])
            bindings = config.get("engine_bindings", {})

            model_ids = bindings.get("ocr", [])
            if isinstance(model_ids, int):
                model_ids = [model_ids]
            emap = {m["id"]: m for m in engine_models}
            engine_priority: list[tuple[str, str, str]] = []

            for mid in model_ids:
                bm = emap.get(mid)
                if not bm:
                    continue
                provider_id = bm.get("provider_id", "")
                model_name = bm.get("model_name", "")
                eng_name = model_name
                if provider_id == "dashscope" and not eng_name.startswith("dashscope"):
                    eng_name = "dashscope-ocr"
                engine_priority.append((eng_name, provider_id, model_name))

            # 绑定链为空
            if not engine_priority:
                if not config.get("engine_auto_fallback", True):
                    raise AppError("PROC_NO_OCR_ENGINE",
                                  "未绑定 OCR 引擎，自动降级已关闭（可在设置中开启）",
                                  recoverable=False)
                for eng_name in EngineRegistry.list_category("ocr"):
                    engine_priority.append((eng_name, "", "default"))
                    logger.warning("OCR 引擎链为空，降级使用: %s", eng_name)
                    break

            text = ""
            engine_name = ""
            tried: list[str] = []
            tried_errors: list[str] = []

            for eng_name, provider_id, model_name in engine_priority:
                engine = EngineRegistry.get("ocr", eng_name)
                if not engine:
                    continue

                if hasattr(engine, 'supported_modes') and engine.supported_modes:
                    if mode not in engine.supported_modes and mode != "extract":
                        logger.info("Engine %s 不支持 mode=%s，跳过", eng_name, mode)
                        continue

                api_key = ""
                base_url = ""
                for ep in engine_providers:
                    if ep.get("id") == (provider_id or eng_name):
                        api_key = ep.get("api_key", "")
                        base_url = ep.get("base_url", "")
                        break

                engine = copy.copy(engine)
                if hasattr(engine, 'api_key') and not engine.api_key:
                    engine.api_key = api_key
                if hasattr(engine, 'base_url') and not engine.base_url:
                    engine.base_url = base_url

                tried.append(eng_name)
                logger.info("Engine 尝试: %s (mode=%s key=%s)", eng_name, mode,
                           "configured" if api_key else "MISSING")

                try:
                    result = engine.run(file_path, model_name=model_name or "", mode=mode)
                    if result and result.text:
                        text = result.text
                        engine_name = f"{eng_name}"
                        logger.info("Engine %s 成功: %d 字", eng_name, len(text))
                        break
                    logger.warning("Engine %s 返回空结果", eng_name)
                    tried_errors.append(f"{eng_name}: 返回空结果")
                except Exception as e:
                    logger.warning("Engine %s 失败: %s", eng_name, str(e)[:120])
                    tried_errors.append(f"{eng_name}: {str(e)[:80]}")
                    continue

            if not text:
                detail = "; ".join(tried_errors) if tried_errors else "无可用 OCR 引擎，请检查配置"
                raise TranscribeError.empty(detail=detail)

            if llm_degraded:
                degradation_notes.append(f"多模态 LLM → {engine_name}")
            if len(tried) > 1:
                degradation_notes.append(f"引擎降级：{tried[0]} → {engine_name}")

        # 持久化到 Store
        pr = ProcessResult(
            id=f"pr_{uuid.uuid4().hex[:12]}",
            transcript_id=transcript_id,
            mode=mode,
            engine=engine_name,
            content=text,
        )
        store.save_process_result(pr)

        return {
            "status": "ok",
            "result_id": pr.id,
            "mode": mode,
            "text": text,
            "engine": engine_name,
            "degraded": degradation_notes if degradation_notes else None,
        }


@router.post("/process-image/{transcript_id}", include_in_schema=False)
async def process_image_legacy(transcript_id: str, data: ProcessRequest, request: Request):
    """[已废弃] 重定向到 /process/{id}。保留以兼容旧前端。"""
    logger.warning(
        "DeprecationWarning: /process-image/%s is deprecated, use /process/%s instead",
        transcript_id, transcript_id,
    )
    return await process_document(transcript_id, data, request)


@router.get("/process-results/{transcript_id}")
async def get_process_results(transcript_id: str, request: Request, store=Depends(get_store)):
    """获取某个 transcript 的所有处理结果（含内容）"""
    results = store.get_process_results(transcript_id)

    items = []
    for r in results:
        content = store.get_process_result(r["id"])
        if content:
            items.append({
                "id": r["id"],
                "transcriptId": r.get("transcriptId", transcript_id),
                "mode": r["mode"],
                "engine": r.get("engine", ""),
                "content": content,
                "createdAt": r.get("createdAt", ""),
            })

    return {"results": items}
