"""
ASR 引擎 — 必剪（Bilibili Bcut）免费云转写

通过 B站必剪 API 上传音频并获取转写结果。
免费，无需 API Key，中文优化。
单次最长约 500 秒轮询超时。
"""

import json
import logging
import time
from pathlib import Path

import requests

from app.core.protocols import Engine

logger = logging.getLogger(__name__)

API_BASE = "https://member.bilibili.com/x/bcut/rubick-interface"
HEADERS = {
    "User-Agent": "Bilibili/1.0.0 (https://www.bilibili.com)",
    "Content-Type": "application/json",
}


class BcutASREngine(Engine):
    """必剪云转写 — 免费、无需 Key、中文优化"""

    name = "bcut-asr"
    capabilities = ["asr"]

    def __init__(self):
        self._session = None

    @property
    def session(self):
        if self._session is None:
            self._session = requests.Session()
        return self._session

    # ── Engine 协议 ──

    def execute(self, audio_path: str, cancel_event=None, **kwargs):
        """执行转写，返回 TranscriptResult"""
        from app.engines.asr import TranscriptResult, TranscriptSegment

        file_path = Path(audio_path)
        if not file_path.exists():
            raise RuntimeError(f"音频文件不存在: {audio_path}")

        file_binary = file_path.read_bytes()
        file_size = len(file_binary)
        logger.info("Bcut ASR: 开始转写 %s (%dKB)", file_path.name, file_size // 1024)

        # 1. 申请上传
        resource_id, upload_urls, per_size, in_boss_key, upload_id = self._req_upload(
            file_binary, file_path.suffix
        )

        # 2. 上传分片
        etags = self._upload_parts(file_binary, upload_urls, per_size)

        # 3. 提交上传
        download_url = self._commit_upload(in_boss_key, resource_id, etags, upload_id)

        # 4. 创建任务
        task_id = self._create_task(download_url)

        # 5. 轮询结果（逐轮检查 cancel_event）
        result = self._poll_result(task_id, cancel_event=cancel_event)

        # 6. 解析结果
        utterances = result.get("utterances", [])
        segments = []
        full_text_parts = []
        for u in utterances:
            text = u.get("transcript", "").strip()
            if not text:
                continue
            # B站 ASR 时间戳是毫秒 → 秒
            start = float(u.get("start_time", 0)) / 1000.0
            end = float(u.get("end_time", 0)) / 1000.0
            segments.append(TranscriptSegment(start=round(start, 2), end=round(end, 2), text=text))
            full_text_parts.append(text)

        language = result.get("language", "zh")
        full_text = " ".join(full_text_parts)
        logger.info(
            "Bcut ASR done: %d segments, lang=%s, %d chars",
            len(segments), language, len(full_text),
        )
        return TranscriptResult(
            language=language,
            duration=segments[-1].end if segments else 0,
            segments=segments,
            full_text=full_text,
        )

    # ── API 调用 ──

    def _req_upload(self, file_binary: bytes, suffix: str) -> tuple:
        """申请上传 → (resource_id, upload_urls, per_size, in_boss_key, upload_id)"""
        ext = (suffix or ".mp3").lstrip(".")
        payload = {
            "type": 2,
            "name": f"audio.{ext}",
            "size": len(file_binary),
            "ResourceFileType": ext,
            "model_id": "8",
        }
        r = self.session.post(
            f"{API_BASE}/resource/create", json=payload, headers=HEADERS, timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Bcut 申请上传失败: {data.get('message', '未知错误')}")
        d = data["data"]
        logger.info(
            "Bcut: 申请上传成功 %dKB, %d 分片", d["size"] // 1024, len(d["upload_urls"]),
        )
        return (
            d["resource_id"], d["upload_urls"], d["per_size"],
            d["in_boss_key"], d["upload_id"],
        )

    def _upload_parts(self, file_binary: bytes, upload_urls: list, per_size: int) -> list[str]:
        """上传分片 → etags 列表"""
        etags = []
        total = len(upload_urls)
        for i, url in enumerate(upload_urls):
            start = i * per_size
            end = min((i + 1) * per_size, len(file_binary))
            r = self.session.put(
                url,
                data=file_binary[start:end],
                headers={"Content-Type": "application/octet-stream"},
                timeout=120,
            )
            r.raise_for_status()
            etag = r.headers.get("Etag", "").strip('"')
            etags.append(etag)
            if total > 1 and (i + 1) % max(1, total // 4) == 0:
                logger.info("Bcut: 上传进度 %d/%d", i + 1, total)
        logger.info("Bcut: 上传完成 %d 分片", total)
        return etags

    def _commit_upload(self, in_boss_key: str, resource_id: str, etags: list[str], upload_id: str) -> str:
        """提交上传 → download_url"""
        payload = {
            "InBossKey": in_boss_key,
            "ResourceId": resource_id,
            "Etags": ",".join(etags),
            "UploadId": upload_id,
            "model_id": "8",
        }
        r = self.session.post(
            f"{API_BASE}/resource/create/complete", json=payload, headers=HEADERS, timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Bcut 提交上传失败: {data.get('message', '未知错误')}")
        return data["data"]["download_url"]

    def _create_task(self, download_url: str) -> str:
        """创建转写任务 → task_id"""
        r = self.session.post(
            f"{API_BASE}/task",
            json={"resource": download_url, "model_id": "8"},
            headers=HEADERS,
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Bcut 创建任务失败: {data.get('message', '未知错误')}")
        task_id = data["data"]["task_id"]
        logger.info("Bcut: 任务已创建 %s", task_id)
        return task_id

    def _poll_result(self, task_id: str, cancel_event=None) -> dict:
        """轮询任务结果（逐轮检查取消信号）→ result dict"""
        max_retries = 500  # ~500 秒超时
        for i in range(max_retries):
            if cancel_event and cancel_event.is_set():
                from app.core.errors import FatalError
                raise FatalError("语音转写已取消")

            r = self.session.get(
                f"{API_BASE}/task/result",
                params={"model_id": 7, "task_id": task_id},
                headers=HEADERS,
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            if data.get("code") != 0:
                raise RuntimeError(f"Bcut 查询结果失败: {data.get('message', '未知错误')}")
            task = data["data"]

            if task["state"] == 4:  # 完成
                logger.info("Bcut: 转写完成")
                return json.loads(task["result"])
            elif task["state"] == 3:  # 失败
                raise RuntimeError(f"Bcut 转写任务失败 (state=3)")

            if i % 10 == 0 and i > 0:
                logger.info("Bcut: 转写中... %ds", i)
            time.sleep(1)

        raise TimeoutError(f"Bcut 转写超时 ({max_retries}s)")
