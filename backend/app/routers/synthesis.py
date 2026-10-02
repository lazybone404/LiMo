"""多篇文档合成 — POST /api/synthesis/stream

把用户选中的 N 篇文档读一遍，按 multi 预算组装成一份「对比综述」**正文**，
落库为一个 `sourceType="composite"` 的新文档。**本端点不生成笔记。**

设计要点：
- **两步走**：这里只建文档；综述由用户在预览区确认「## 来源文档」清单后，
  自行点「生成对比综述」走现成的 `/generate/{id}/stream`。
  ⚠️ **刻意不自动触发**（D7 已拍板）：用户需要先看到选对了哪几篇再花 token。
- **单真相源**：合成文档的 rawText 就**等于**模型输入（按 multi 预算恰好装满），
  生成时不许再读原文。
- **全部业务失败走 SSE error 事件而非 500**：篇数不足 / 源已删 / 正文为空 /
  模型窗口不够，都是**预期业务状态**（项目模式 #36：`recoverable` 与 `status_code`
  两维独立）。前端一律按 SSE 解析，中途换成 HTTP JSON 会让它解不出来。
"""

import asyncio
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core import logging as log
from app.core.dependencies import get_config, get_guard, get_store
from app.core.disconnect import disconnect_watcher
from app.core.errors import AppError
from app.generation.synthesis import (
    SYNTH_QUIET_TIMEOUT,
    SYNTH_TOTAL_TIMEOUT,
    clean_dimensions,
    normalize_source_mode,
    preview_synthesis,
    synthesize,
)
from app.llm import CancelledError as LLMCancelledError
from app.llm import LLMService

router = APIRouter()

# ⚠️ `SYNTH_TOTAL_TIMEOUT` 与 `SYNTH_QUIET_TIMEOUT` **已迁至 `generation/synthesis.py`**
# 并与 `EXTRACT_TIMEOUT` / `CHUNK_EXTRACT_TIMEOUT` 一起用断言钉住序关系。搬家的理由：
# 它们描述的是**管线自身的时序**（单次调用上限 vs 挂死判据 vs 整体超时），而
# 「分块后一篇 = K 次调用串联」这件事只有管线自己知道——两个常量分处两地时，
# 改了一处不会想起另一处（它们**曾经同为 900**，导致「挂死」与「整体超时」同刻到期，
# 用户收到哪条错误由竞态决定）。

# 软 deadline 比硬超时提前这么多秒：留给「剩余篇硬截断 + 组装 + 落库」的时间。
# 没有它，硬超时一到就是**颗粒无收**——前面十几分钟与真实烧掉的 token 全部作废。
DEADLINE_RESERVE = 60


class SynthesisRequest(BaseModel):
    doc_ids: list[str]
    title: str = ""
    user_notes: str = ""
    model_id: int | None = None
    # 用户给定的对比维度（设计 §18）：非空则跳过「维度发现」那次计费调用。
    dimensions: list[str] = []
    # 原料模式（设计 §19）：`raw`（转录原文，默认）/ `notes`（已生成的笔记）。
    # ⚠️ 非法值由 `normalize_source_mode` 显式拒绝——**不静默回退默认**，
    # 否则用户以为在用笔记、实际用原文，而产物上看不出任何异常。
    source_mode: str = "raw"


@router.post("/synthesis/stream")
async def synthesis_stream(
    req: SynthesisRequest,
    request: Request,
    store=Depends(get_store),
    config=Depends(get_config),
    guard=Depends(get_guard),
):
    """N 篇 → 一个新的 composite 文档（SSE：stage → done/error）。"""
    # 入参清洗放最前（在任何计费调用之前）：维度列表与原料模式都是用户可控输入
    dimensions = clean_dimensions(req.dimensions)
    source_mode = normalize_source_mode(req.source_mode)
    log.info("合成请求", docs=len(req.doc_ids), model_id=req.model_id,
             title=req.title[:60], dims=len(dimensions), source_mode=source_mode)
    llm = LLMService(config, model_id=req.model_id)
    cancel_event = asyncio.Event()

    async def runner(queue: asyncio.Queue) -> None:
        def on_stage(message: str) -> None:
            queue.put_nowait({"type": "stage", "message": message})

        try:
            if not llm.api_key:
                raise AppError("SYNTH_NO_API_KEY", "LLM API Key 未配置，请在设置中填写",
                               recoverable=False)
            # 软 deadline：到点后管线停止发起新调用、剩余篇截断，让流程走到落库。
            # ⚠️ 减去 DEADLINE_RESERVE 是刻意的——两者同刻到期就等于没有软降级。
            deadline = asyncio.get_running_loop().time() + SYNTH_TOTAL_TIMEOUT - DEADLINE_RESERVE
            async with disconnect_watcher(request, cancel_event):
                async with asyncio.timeout(SYNTH_TOTAL_TIMEOUT):
                    result = await synthesize(
                        store, config, llm, req.model_id, req.doc_ids,
                        title=req.title, user_notes=req.user_notes,
                        dimensions=dimensions, source_mode=source_mode,
                        cancel_event=cancel_event, on_stage=on_stage,
                        deadline=deadline,
                    )
            log.info("合成完成", doc_id=result.transcript_id, mode=result.mode,
                     calls=result.llm_call_count, degraded=len(result.degraded),
                     excluded=len(result.excluded))
            queue.put_nowait({
                "type": "done",
                "id": result.transcript_id,
                "mode": result.mode,
                # 被截断/摘要失败的篇目（设计 §4.4.3：降级必须可见）。
                # ⚠️ 同一份信息也写进了正文的「## 来源文档」列表——SSE 一闪用户
                # 没看见就永远不知道，故落进产物本身才是主渠道，这里是补充。
                "degraded": result.degraded,
                # notes 模式下因**没有笔记**被排除的篇（设计 §19.4）。它不是错误
                # ——但没看预览就点了合成的用户必须知道，否则会以为它参与了对比。
                "excluded": result.excluded,
            })
        except LLMCancelledError:
            # 用户取消：不算错误，前端按「已取消」收尾
            queue.put_nowait({"type": "error", "code": "SYNTH_CANCELLED",
                              "message": "合成已取消"})
        except TimeoutError:
            # ⚠️ str(TimeoutError()) 是**空串**（与 reference_compressor 同款坑），
            # 必须自己写文案
            log.warning("合成整体超时", timeout=SYNTH_TOTAL_TIMEOUT, docs=len(req.doc_ids))
            queue.put_nowait({
                "type": "error", "code": "SYNTH_TIMEOUT",
                "message": f"合成超时（超过 {SYNTH_TOTAL_TIMEOUT // 60} 分钟），"
                           "请减少篇数或换用响应更快的模型",
            })
        except AppError as e:
            # 业务状态（篇数不足 / 源已删 / 窗口不够）：结构化错误码给前端分类处理
            log.info("合成未通过", code=e.code, message=e.message)
            queue.put_nowait({"type": "error", **e.to_dict()})
        except Exception as e:
            log.exception("合成失败", error=str(e)[:200])
            queue.put_nowait({"type": "error", "code": "SYNTH_FAILED",
                              "message": f"合成失败: {str(e)[:200]}"})
        finally:
            # ⚠️ **结束哨兵**：上面每个分支都推了事件，但若哪天有人删掉某个 except
            # （或抛出 BaseException 子类），runner 会**静默死掉**——队列再没有事件，
            # 主循环只能靠 900s 静默超时收尾，用户对着进度条干等一刻钟。
            # 哨兵让「任务结束」与「事件产出」解耦：无论怎么结束，主循环立刻收尾。
            queue.put_nowait(None)

    async def event_generator():
        # acquire 与 try 之间不留任何语句：中间抛异常会让槽位永久丢失
        await guard.semaphore.acquire()
        queue: asyncio.Queue = asyncio.Queue()
        task = asyncio.create_task(runner(queue))
        loop = asyncio.get_event_loop()
        last_event = loop.time()
        try:
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30)
                except asyncio.TimeoutError:
                    if loop.time() - last_event > SYNTH_QUIET_TIMEOUT:
                        yield f"data: {json.dumps({'type': 'error', 'code': 'SYNTH_STALLED', 'message': '合成长时间无响应'}, ensure_ascii=False)}\n\n"
                        break
                    # SSE 注释作为心跳，保持连接不被代理/浏览器关闭
                    yield ": heartbeat\n\n"
                    continue
                if data is None:
                    # 结束哨兵：runner 已收尾却没产出 done/error（异常被漏捕获）——
                    # 显式告知，不能让客户端把「流关了」当成成功
                    log.warning("合成流结束但无结果事件", docs=len(req.doc_ids))
                    break
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                last_event = loop.time()
                if data["type"] in ("done", "error"):
                    break
        finally:
            # 客户端断开 → 取消后台协程（它会经 cancel_event 停止后续 LLM 调用）
            if not task.done():
                task.cancel()
            try:
                await asyncio.gather(task, return_exceptions=True)
            except Exception:
                pass
            try:
                await llm.aclose()
            except Exception:
                pass
            # release 绝不能被异常逃逸，否则槽位永久丢失（同 generate.py 的教训）
            try:
                guard.semaphore.release()
            except Exception:
                pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class SynthesisPreviewRequest(BaseModel):
    doc_ids: list[str]
    model_id: int | None = None
    source_mode: str = "raw"


@router.post("/synthesis/preview")
async def synthesis_preview(
    req: SynthesisPreviewRequest,
    store=Depends(get_store),
    config=Depends(get_config),
):
    """合成**前**预览：每篇的 token 量、预计处理方式、以及「怎样才会全部装下」。

    **零 LLM 调用、零副作用**——把 `plan_synthesis` 的结果摊开给用户看，让他
    在花 token 之前就知道该不该换个窗口更大的模型（设计 §17.4：数据链路早已
    存在，只是从没被消费过）。

    ⚠️ 业务失败（篇数不足 / 源已删）走 `AppError` → **200 + `{code, message}`**
    （项目模式 #36）——前端必须检查 `body.code`，不能只看 `response.ok`。

    ⚠️ 不加 `guard.semaphore`：本端点不占用任何模型槽位（信号量是给真实调用的）。
    读 N 篇小文件是毫秒级，不值得再套一层 `to_thread`。
    """
    return preview_synthesis(
        store, config, req.model_id, req.doc_ids,
        source_mode=normalize_source_mode(req.source_mode),
    )
