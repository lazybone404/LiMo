"""
Client disconnect monitoring for long-running HTTP endpoints.

Provides a context manager that spawns a background task to poll
request.is_disconnected() and sets an asyncio.Event when the client
disconnects. Downstream LLM/engine calls check the event to abort early.

Usage::

    cancel_event = asyncio.Event()
    async with disconnect_watcher(request, cancel_event):
        await llm.stream(prompt, cancel_event=cancel_event)
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Request

logger = logging.getLogger(__name__)


@asynccontextmanager
async def disconnect_watcher(
    request: Request,
    cancel_event: asyncio.Event,
    interval: float = 0.5,
):
    """
    Monitor client disconnect and signal via cancel_event.

    Creates a background polling task. On scope exit the task is cancelled
    and awaited, guaranteeing cleanup even if the caller forgets.
    """
    async def _watch() -> None:
        while not cancel_event.is_set():
            if await request.is_disconnected():
                cancel_event.set()
                logger.debug("Client disconnected, cancel_event set")
                return
            await asyncio.sleep(interval)

    task = asyncio.create_task(_watch())
    try:
        yield
    finally:
        # 不在此处 set cancel_event —— 只有客户端真正断开时才应取消
        # cancel_event 由 _watch 循环在 is_disconnected() 返回 True 时置位
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
