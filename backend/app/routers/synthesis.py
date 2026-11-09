"""多篇文档合成 — POST /api/synthesis/stream / /preview / /merge

把用户选中的 N 篇文档读一遍，按 multi 预算组装成一份「对比综述」**正文**，
落库为一个 `sourceType="composite"` 的新文档。**本端点不生成笔记。**

合并（`/merge`）与合成的产物**同一种文档**（`sourceType="composite"`），区别只在
正文怎么来：合成是模型按读者意图写的综述，合并是把各篇原文按顺序拼起来、**零模型
调用**。两者共用同一套读取、资产搬运与出处头写法（见 `generation/synthesis.py`
的 `merge_sources`），故下游（文档库 / 搜索 / 导出 / 对话引用 / 版本）零改动。

设计要点：
- **两步走**：这里只建文档；综述由用户在预览区确认「## 来源文档」清单后，
  自行点「生成对比综述」走现成的 `/generate/{id}/stream`。
  ⚠️ **刻意不自动触发**（D7 已拍板）：用户需要先看到选对了哪几篇再花 token。
- **单真相源**：合成文档的 rawText 就**等于**模型输入（按 multi 预算恰好装满），
  生成时不许再读原文。
- **全部业务失败走 SSE error 事件而非 500**：篇数不足 / 源已删 / 正文为空 /
  模型窗口不够，都是**预期业务状态**（项目模式 #36：`recoverable` 与 `status_code`
  两维独立）。前端一律按 SSE 解析，中途换成 HTTP JSON 会让它解不出来。

## `/stream` 是一层转推（2026-11-10）

管线**跑在后端任务队列里**，不跑在这条连接上：切走页面、折叠面板、刷新浏览器，
合成照常推进，进度落在 `{data_dir}/jobs.db`，任务面板能看见。这条 SSE 只负责
「提交任务 → 轮询状态 → 翻译成原来的事件形状」，前端的事件消费一个字都不用改。

⚠️ 三个由此而来的新语义（都与改造前**不同**，不是等价重构）：
- **连接断开不再取消合成**。原来断开即 abort（省 token），现在断开只是「没人看」——
  否则「离开界面就丢失」根本没被修掉。要停就点任务面板的取消。
- **同一批文档 + 同一原料模式 + 读者意图再点一次 = 挂在同一条任务上**（幂等键，
  见 `SynthesisParams.idempotency_key`），不再产生第二次计费。
- **进程崩溃后不会自动重跑**：`synthesis` 的 `retry_on_restart=False`，重启时
  标 `failed`「进程中断」。自动重跑一次计费调用等于让用户为一次崩溃付两次钱。
"""

import asyncio
import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core import logging as log
from app.core.dependencies import get_config, get_job_queue, get_store
from app.core.errors import AppError
from app.generation.synthesis import (
    SYNTH_QUIET_TIMEOUT,
    merge_sources,
    normalize_source_mode,
    prepare_synthesis,
    preview_synthesis,
)
from app.routers.jobs import _job_json
from app.services.job_store import CANCELLED, DONE, FAILED, RUNNING

router = APIRouter()

# ⚠️ `SYNTH_TOTAL_TIMEOUT` / `SYNTH_QUIET_TIMEOUT` / `DEADLINE_RESERVE` 都在
# `generation/synthesis.py`，并与 `EXTRACT_TIMEOUT` / `CHUNK_EXTRACT_TIMEOUT`
# 用断言钉住序关系。理由：它们描述的是**管线自身的时序**（单次调用上限 vs 挂死
# 判据 vs 整体超时），而「分块后一篇 = K 次调用串联」只有管线自己知道——常量分处
# 两地时，改了一处不会想起另一处（它们**曾经同为 900**，导致「挂死」与「整体超时」
# 同刻到期，用户收到哪条错误由竞态决定）。本文件只用到静默阈值。

#: 轮询任务状态的间隔。任务上报的粒度是「一次 LLM 调用」，0.5 秒足够让 stage
#: 提示跟手；一次读取是一条 `jobs.db` 的索引查询。
POLL_INTERVAL = 0.5

#: SSE 心跳的间隔。轮询本身不发任何字节，长时间没有字节的流会被中间层掐断。
HEARTBEAT_INTERVAL = 30.0


class SynthesisRequest(BaseModel):
    doc_ids: list[str]
    title: str = ""
    model_id: int | None = None
    # 用户给定的对比维度（设计 §18）：非空则跳过「维度发现」那次计费调用。
    dimensions: list[str] = []
    # 原料模式（设计 §19）：`raw`（转录原文，默认）/ `notes`（已生成的笔记）。
    # ⚠️ 非法值由 `normalize_source_mode` 显式拒绝——**不静默回退默认**，
    # 否则用户以为在用笔记、实际用原文，而产物上看不出任何异常。
    source_mode: str = "raw"
    # 读者意图（设计 §7.8.3）：决定 `task` 段的「读者目的」。空 = 默认意图。
    # ⚠️ 非法值由 `normalize_reader_intent` 显式拒绝（理由同 source_mode：
    # 选错了产出的是**另一种读者目的**的综述，而产物上看不出异常）。
    reader_intent: str = ""
    # 「自定义」意图的自由文本；其他意图留空（后端按意图取预设文案）。
    reader_intent_text: str = ""
    # 材料体裁多标签（设计 §7.8.2）：决定加载哪几类「类型增量」条款。
    # 空 = 不加任何体裁增量（**不回退到论文**——静默预填对视频转写稿毫无意义）。
    content_type: list[str] = []
    # ⚠️ 历史字段 `user_notes`（「对比指引」）**已删除**：它是死链——`synthesize()`
    # 签名里有、函数体零引用，用户填的指引对产物从来没有影响。位置由
    # `reader_intent` / `reader_intent_text` 承接（走 meta，**真正生效**）。
    #
    # ⚠️ 这三个新字段**必须落 meta**、不能只走请求参数：生成是**第二次独立请求**
    # （用户核对完「## 来源文档」再点生成），届时手里只有 rawText。


@router.post("/synthesis/stream")
async def synthesis_stream(req: SynthesisRequest, queue=Depends(get_job_queue)):
    """N 篇 → 一个新的 composite 文档（SSE：job → stage… → done/error）。

    执行在任务队列里（见文件顶部的「`/stream` 是一层转推」）。
    """
    def emit(payload: dict) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    async def event_generator():
        loop = asyncio.get_running_loop()
        last_emit = loop.time()

        # ① 预检。⚠️ 必须在这里（而不是路由函数体里）：在 `StreamingResponse`
        #    建好之前抛错会被 FastAPI 转成 HTTP 4xx/500，而前端拿 `fetch` + reader
        #    解这条流，**非 2xx 只表现为「连接中断」**——用户看不到「不支持的读者
        #    意图：xxx（可选：…）」这种可行动的文案。本端点的纪律是业务失败一律走
        #    SSE error 事件，预检是最容易漏在门外的一步。
        #
        #    预检**不替代**任务里的清洗（handler 里用同一个函数再跑一遍）：非法值
        #    当场拒绝，不必排到队列里才失败；而 `jobs.db` 里的 payload 经过一轮
        #    JSON，不能假定形状仍然合法。
        try:
            params = prepare_synthesis(
                req.doc_ids, title=req.title, model_id=req.model_id,
                dimensions=req.dimensions, source_mode=req.source_mode,
                reader_intent=req.reader_intent,
                reader_intent_text=req.reader_intent_text,
                content_type=req.content_type,
            )
        except AppError as e:
            log.info("合成未通过（预检）", code=e.code, message=e.message)
            yield emit({"type": "error", **e.to_dict()})
            return

        # ② 提交（幂等键命中时拿回正在跑的那条）→ 先告诉前端任务 id。
        #    前端据此把任务挂进任务面板：面板里那行与这条流看的是**同一条记录**。
        job, created = queue.submit(
            "synthesis", params.to_payload(), params.idempotency_key())
        log.info("合成任务已入队", job=job.id[:8], created=created,
                 docs=len(params.doc_ids), mode=params.source_mode,
                 intent=params.reader_intent, model_id=params.model_id)
        yield emit({"type": "job", "job": _job_json(job, queue)})
        last_emit = loop.time()

        # ③ 轮询到终态。⚠️ 静默判据只对 `running` 生效：在 `queued` 里等前面的任务
        #    （队列是单消费者）是完全正常的，拿它判挂死会在排队时给一条假错误。
        last_change = loop.time()
        last_message = job.message
        last_status = job.status
        while True:
            await asyncio.sleep(POLL_INTERVAL)
            current = queue.store.get(job.id)
            if current is None:
                # 任务表被外部清空（`prune` 只删终态，走到这里说明有别的写入方）
                log.warning("合成流找不到任务记录", job=job.id[:8])
                yield emit({"type": "error", "code": "SYNTH_JOB_LOST",
                            "message": "任务记录已丢失，请刷新文档库确认结果"})
                return

            # 状态切换本身也算「有动静」：排队 10 分钟之后刚开始跑（`queued` →
            # `running`）时，静默计时必须从**这一次**开始，否则上一轮的排队时间
            # 会把一次正常的首调用直接判成挂死。
            if current.status != last_status:
                last_status, last_change = current.status, loop.time()

            if current.message and current.message != last_message:
                last_message, last_change = current.message, loop.time()
                yield emit({"type": "stage", "message": current.message})
                last_emit = loop.time()

            if current.status == DONE:
                result = current.result if isinstance(current.result, dict) else {}
                log.info("合成完成（流式）", job=job.id[:8], doc_id=result.get("id"))
                yield emit({
                    "type": "done",
                    "id": result.get("id") or "",
                    "mode": result.get("mode") or "",
                    # 被截断/摘要失败的篇目（设计 §4.4.3：降级必须可见）。
                    # ⚠️ 同一份信息也写进了正文的「## 来源文档」列表——SSE 一闪用户
                    # 没看见就永远不知道，故落进产物本身才是主渠道，这里是补充。
                    "degraded": result.get("degraded") or [],
                    # notes 模式下因**没有笔记**被排除的篇（设计 §19.4）。它不是
                    # 错误——但没看预览就点了合成的用户必须知道，否则会以为它参与了对比。
                    "excluded": result.get("excluded") or [],
                })
                return
            if current.status == FAILED:
                # 结构化原因由 handler 放进 `result["code"]`（`JobFailed.code`）：
                # 任务面板只显示一句话，而 SSE 的消费方可能要按原因分叉。
                extra = current.result if isinstance(current.result, dict) else {}
                yield emit({
                    "type": "error",
                    "code": extra.get("code") or "SYNTH_FAILED",
                    "message": current.error or "合成失败",
                })
                return
            if current.status == CANCELLED:
                # 用户取消：不算错误，前端按「已取消」收尾（`code` 是判据）
                yield emit({"type": "error", "code": "SYNTH_CANCELLED",
                            "message": current.error or "合成已取消"})
                return

            if current.status == RUNNING and loop.time() - last_change > SYNTH_QUIET_TIMEOUT:
                log.warning("合成流静默超时", job=job.id[:8], quiet=SYNTH_QUIET_TIMEOUT)
                yield emit({"type": "error", "code": "SYNTH_STALLED",
                            "message": "合成长时间无响应"})
                return

            if loop.time() - last_emit >= HEARTBEAT_INTERVAL:
                # SSE 注释作为心跳，保持连接不被代理/浏览器关闭
                yield ": heartbeat\n\n"
                last_emit = loop.time()

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


class MergeRequest(BaseModel):
    doc_ids: list[str]
    title: str = ""
    source_mode: str = "raw"


@router.post("/synthesis/merge")
async def synthesis_merge(req: MergeRequest, store=Depends(get_store)):
    """N 篇 → **一篇纯拼接的 composite 文档**（非流式，零模型调用）。

    与 `/stream` 的分野是「正文出自谁手」：那边由模型按读者意图写综述，这边把各篇
    原文按顺序拼起来、每篇一个 `## {序号}. {标题}` 开头。故本端点没有 stage 事件、
    没有预算判定、没有降级——没有任何一步会因材料太长而失败。

    **不返回 SSE**：拼接是磁盘操作，秒级完成；用流式反要让前端多接一条解析路径。

    ⚠️ **业务失败走 `AppError` → HTTP 200 + `{code, message}`**（项目模式 #36），
    前端必须检查 `body.code`：篇数不足与「选中的篇还没转录」都不是 500。

    ⚠️ **不加 `guard.semaphore`**：信号量是给模型调用排队用的，这里一次都不调模型。
    占一个槽位会让合并期间的一次生成排在它后面，而它实际只需要几毫秒。

    ⚠️ **走 `asyncio.to_thread`**：函数里要逐篇复制资产文件并写盘，同步 I/O 直接在
    事件循环里跑会卡住同一时刻的 SSE 心跳与 worker 的 await。

    ⚠️ **`save_transcript(skip_dedup=True)` 之后不做「内容去重」**：合并产物的正文
    由原文拼成，与任何一篇源文档都不相同，不撞去重；而它与**上一次合并**撞上反而
    说明用户合并了同一批文档两次——按失败显式化原则，那是应当写进去的，不是静默跳过。
    """
    source_mode = normalize_source_mode(req.source_mode)
    log.info("合并请求", docs=len(req.doc_ids), source_mode=source_mode,
             title=(req.title or "")[:60])
    result = await asyncio.to_thread(
        merge_sources, store, req.doc_ids, req.title, source_mode,
    )
    log.info("合并完成", doc_id=result.transcript_id, docs=len(result.sources),
             excluded=len(result.excluded))
    return {
        "id": result.transcript_id,
        "title": result.title,
        "mode": result.mode,
        # notes 模式下因无笔记被排除的篇 id（设计 §19.4）。合并与合成共用这条
        # 语义：它不是错误，但用户必须知道哪几篇没进来。
        "excluded": result.excluded,
    }
