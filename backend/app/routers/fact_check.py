"""
事实验证路由 — POST /api/fact-check/{transcript_id} + SSE 流式

独立于生成管道。验证源 fallback：已有 notes → 已有 polished → 原始转录 → 报错。
"""

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.types import MarkdownView, Style, GenerateMode
from app.core.errors import AppError
from app.core import logging as log
from app.core.model_resolver import resolve_llm_config
from app.llm import LLMService, CancelledError
from app.generation.fact_check import FactCheckEngine

logger = logging.getLogger(__name__)
router = APIRouter()


class FactCheckRequest(BaseModel):
    model_id: int | None = None


@router.post("/fact-check/{transcript_id}")
async def fact_check(transcript_id: str, req: FactCheckRequest, request: Request) -> dict:
    """非流式事实验证"""
    guard = request.app.state.guard
    async with guard.semaphore:
        store = request.app.state.store
        config = request.app.state.config

        tx = store.get_transcript(transcript_id)
        if not tx:
            raise AppError("FC_NOT_FOUND", "文档不存在", recoverable=False)

        ctx = _resolve_content(store, tx, transcript_id)

        llm_config = resolve_llm_config(config, req.model_id, scene="generate")
        if not llm_config.get("api_key"):
            raise AppError("FC_NO_API_KEY", "LLM API Key 未配置", recoverable=False)

        llm = LLMService(config, model_id=req.model_id)

        fc = FactCheckEngine(llm)
        fc_res = await fc.run(ctx["content"])

        fc_mv = MarkdownView(
            id=fc_res["id"], transcript_id=transcript_id,
            content=fc_res["content"], style=Style.DETAILED,
            mode=GenerateMode.FACT_CHECK, fact_check=True,
        )
        from datetime import datetime
        store.save_markdown(fc_mv, extra={
            "verified_count": fc_res["verified_count"],
            "disputed_count": fc_res["disputed_count"],
            "unverifiable_count": fc_res["unverifiable_count"],
            "overall_score": fc_res["overall_score"],
            "source_mode": ctx["source_mode"],
            "source_version": ctx["source_version"],
            "verified_at": datetime.now().isoformat(),
        })

        log.info("Fact check done", tx_id=transcript_id,
                 verified=fc_res["verified_count"], disputed=fc_res["disputed_count"])
        return {
            "id": fc_mv.id, "transcriptId": transcript_id,
            "content": fc_mv.content,
            "verified_count": fc_res["verified_count"],
            "disputed_count": fc_res["disputed_count"],
            "unverifiable_count": fc_res["unverifiable_count"],
            "overall_score": fc_res["overall_score"],
            "source_mode": ctx["source_mode"],
            "source_version": ctx["source_version"],
        }


@router.get("/fact-check/{transcript_id}/stream")
async def fact_check_stream(transcript_id: str, request: Request,
                            model_id: int | None = None):
    """SSE 流式事实验证"""
    store = request.app.state.store
    config = request.app.state.config

    tx = store.get_transcript(transcript_id)
    if not tx:
        raise AppError("FC_NOT_FOUND", "文档不存在", recoverable=False)

    ctx = _resolve_content(store, tx, transcript_id)

    llm_config = resolve_llm_config(config, model_id, scene="generate")
    if not llm_config.get("api_key"):
        raise AppError("FC_NO_API_KEY", "LLM API Key 未配置", recoverable=False)
    llm = LLMService(config, model_id=model_id)

    guard = request.app.state.guard
    cancel_event = asyncio.Event()

    async def merged_stream():
        await guard.semaphore.acquire()
        # 客户端断连检测
        async def _watch():
            while True:
                if await request.is_disconnected():
                    cancel_event.set()
                    return
                await asyncio.sleep(0.5)
        disconnect_task = asyncio.create_task(_watch())
        try:
            fc = FactCheckEngine(llm)
            fc_id = None
            fc_report = ""
            fc_verified = fc_disputed = fc_unverifiable = 0
            fc_score = 0.0
            async for event in fc.run_streaming(ctx["content"], cancel_event):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("type") == "done":
                    fc_id = event.get("id", "")
                    fc_verified = event.get("verified_count", 0)
                    fc_disputed = event.get("disputed_count", 0)
                    fc_unverifiable = event.get("unverifiable_count", 0)
                    fc_score = event.get("overall_score", 0)
                elif event.get("phase") == "report" and "text" in event:
                    fc_report += event["text"]
            if fc_id:
                from datetime import datetime
                fc_mv = MarkdownView(
                    id=fc_id, transcript_id=transcript_id,
                    content=fc_report, style=Style.DETAILED,
                    mode=GenerateMode.FACT_CHECK, fact_check=True,
                )
                store.save_markdown(fc_mv, extra={
                    "verified_count": fc_verified,
                    "disputed_count": fc_disputed,
                    "unverifiable_count": fc_unverifiable,
                    "overall_score": fc_score,
                    "source_mode": ctx["source_mode"],
                    "source_version": ctx["source_version"],
                    "verified_at": datetime.now().isoformat(),
                })
            yield f"data: {json.dumps({'type': 'done', 'source_mode': ctx['source_mode'], 'source_version': ctx['source_version']}, ensure_ascii=False)}\n\n"
        except CancelledError:
            yield f"data: {json.dumps({'type': 'cancelled'}, ensure_ascii=False)}\n\n"
        except Exception as e:
            log.exception("Fact check stream failed", tx_id=transcript_id)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)[:200]}, ensure_ascii=False)}\n\n"
        finally:
            disconnect_task.cancel()
            try:
                await disconnect_task
            except (asyncio.CancelledError, Exception):
                pass
            guard.semaphore.release()

    return StreamingResponse(
        merged_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _resolve_content(store, tx: dict, transcript_id: str) -> dict:
    """解析验证源：已有 notes → 已有 polished → 原始转录 → 报错。
    返回 {content, source_mode, source_version, source_id}"""
    mds = store.get_markdowns_by_transcript(transcript_id)
    notes_md = next((m for m in mds if m.get("mode") == "notes" and m.get("status") == "complete"), None)
    polished_md = next((m for m in mds if m.get("mode") == "polished" and m.get("status") == "complete"), None)

    if notes_md:
        content = store.get_markdown(notes_md["id"])
        if content:
            return {"content": content, "source_mode": "notes",
                    "source_version": notes_md.get("version", 1), "source_id": notes_md["id"]}
    if polished_md:
        content = store.get_markdown(polished_md["id"])
        if content:
            return {"content": content, "source_mode": "polished",
                    "source_version": polished_md.get("version", 1), "source_id": polished_md["id"]}

    raw = tx.get("rawText", "")
    if raw and len(raw) > 200:
        return {"content": raw, "source_mode": "transcript",
                "source_version": 0, "source_id": transcript_id}

    raise AppError("FC_NO_CONTENT", "没有可验证的内容，请先生成笔记或整理版原文", recoverable=False)
