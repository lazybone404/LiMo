"""
全局并发控制 — 防止长耗时操作打满线程池。

云端操作（API 调用）和本地操作（CPU 引擎）分开计数：
- 本地引擎慢（10-30 分钟），不应抢占云端操作的槽位
- 云端操作快（秒级），共享较大槽位
"""

import asyncio


class ExecutionGuard:
    """双层并发控制：云端 API 调用 vs 本地 CPU 引擎"""

    def __init__(self, cloud_max: int = 5, local_max: int = 2):
        self.semaphore = asyncio.Semaphore(cloud_max)  # 云端操作（生成/处理/云端引擎）
        self.local_semaphore = asyncio.Semaphore(local_max)  # 本地 CPU 引擎

    def shutdown(self):
        pass
