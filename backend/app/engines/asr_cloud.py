"""
云端 ASR 引擎 — 阿里云 DashScope Paraformer / Fun-ASR
对齐 BiliNote transcriber/qwen.py：dashscope SDK → Files.upload → async_call → 轮询
"""

import json
import logging
import time
from http import HTTPStatus
from pathlib import Path

import dashscope
import requests
from dashscope.audio.asr import Transcription, Recognition

from app.core.protocols import Engine

logger = logging.getLogger(__name__)

# 模型名缓存：video.py 在调用前注入（_transcribe → engine._model_name = xxx）
# SDK 的 async_call 根据 model 自动路由到正确的 API


class TranscriptSegment:
    def __init__(self, start: float, end: float, text: str):
        self.start = start
        self.end = end
        self.text = text


class TranscriptResult:
    def __init__(self, language: str, duration: float, segments: list, full_text: str = ""):
        self.language = language
        self.duration = duration
        self.segments = segments
        self.full_text = full_text


class DashScopeASREngine(Engine):
    """阿里云 DashScope 语音识别 — SDK 文件上传 + 异步转写"""

    name = "dashscope-asr"

    def __init__(self, api_key: str = ""):
        self.api_key = api_key
        self._model_name = "paraformer-v2"  # video.py 调用前注入实际模型名
        self.capabilities = ["asr"]

    @staticmethod
    def _find_transcription_url(output: dict) -> str | None:
        """从 fun-asr 的嵌套结构中提取 transcription_url"""
        for item in output.get("results", []):
            inner = item.get("output", {})
            for sub in inner.get("results", []):
                t_url = sub.get("transcription_url")
                if t_url:
                    return t_url
        return None

    def _call_realtime(self, file_path: str, model: str) -> dict:
        """实时模型（fun-asr-realtime）→ 同步 Recognition API，直接传本地文件"""
        import subprocess
        import tempfile

        path = Path(file_path)
        audio_fmt = path.suffix.lstrip(".")

        # Recognition API 通常要求 WAV/PCM，非 WAV 文件用 ffmpeg 转一下
        call_path = file_path
        if audio_fmt not in ("wav", "pcm"):
            tmp_wav = Path(tempfile.mktemp(suffix=".wav"))
            logger.info("DashScope Recognition: 转换 %s → wav", audio_fmt)
            subprocess.run([
                "ffmpeg", "-y", "-i", file_path,
                "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                str(tmp_wav),
            ], check=True, capture_output=True, timeout=300)
            call_path = str(tmp_wav)
            audio_fmt = "wav"

        recognition = Recognition(
            model=model,
            format=audio_fmt,
            sample_rate=16000,
            language_hints=["zh", "en"],
            callback=None,
        )
        result = recognition.call(call_path)

        # 清理临时 WAV
        if call_path != file_path:
            try:
                Path(call_path).unlink()
            except OSError:
                pass

        if result.status_code != HTTPStatus.OK:
            raise RuntimeError(
                f"DashScope Recognition 失败: code={result.status_code}, "
                f"message={result.message}"
            )
        # Recognition 返回 RecognitionResult → 提取文本
        sentence = result.get_sentence()
        text = sentence.text if hasattr(sentence, 'text') else str(sentence)
        return {"utterances": [{"text": text, "start": 0, "end": 0}]}

    def _call_async(self, file_url: str, model: str) -> dict:
        """离线模型（paraformer-v2）→ 异步 Transcription API"""
        if file_url.startswith("http://"):
            file_url = file_url.replace("http://", "https://", 1)

        task_resp = Transcription.async_call(
            model=model,
            file_urls=[file_url],
            api_key=self.api_key,
        )
        if task_resp.status_code != HTTPStatus.OK:
            raise RuntimeError(
                f"DashScope 任务提交失败: code={task_resp.status_code}, "
                f"message={task_resp.message}"
            )

        task_id = task_resp.output.get("task_id")
        if not task_id:
            raise RuntimeError(f"DashScope 未返回 task_id: {task_resp.output}")
        logger.info("DashScope 任务提交: task_id=%s", task_id)

        result_resp = Transcription.wait(task=task_id, api_key=self.api_key)
        if result_resp.status_code != HTTPStatus.OK:
            raise RuntimeError(
                f"DashScope 转写失败: code={result_resp.status_code}, "
                f"message={result_resp.message}"
            )
        return result_resp.output

    def execute(self, audio_path: str, **kwargs) -> TranscriptResult:
        if not self.api_key:
            raise RuntimeError("DashScope API key 未配置，请在设置中填写阿里云 API Key")

        path = Path(audio_path)
        if not path.exists():
            raise FileNotFoundError(f"音频文件不存在: {audio_path}")

        size_mb = path.stat().st_size / 1024 / 1024
        model = getattr(self, '_model_name', 'paraformer-v2')
        logger.info("DashScope ASR: %s (%.1fMB) model=%s", path.name, size_mb, model)

        # 1. 上传文件
        upload_resp = dashscope.Files.upload(
            file_path=str(path),
            purpose="inference",
            api_key=self.api_key,
        )
        if upload_resp.status_code != HTTPStatus.OK:
            raise RuntimeError(
                f"DashScope 上传失败: code={upload_resp.status_code}, "
                f"message={upload_resp.message}"
            )

        uploaded = upload_resp.output.get("uploaded_files", [])
        if not uploaded:
            raise RuntimeError(f"DashScope 上传未返回 file_id: {upload_resp.output}")
        file_id = uploaded[0]["file_id"]
        logger.info("DashScope 上传完成: file_id=%s", file_id)

        # 2. 获取文件 URL
        file_info = dashscope.Files.get(file_id, api_key=self.api_key)
        if file_info.status_code != HTTPStatus.OK:
            raise RuntimeError(
                f"DashScope 获取文件URL失败: code={file_info.status_code}, "
                f"message={file_info.message}"
            )
        uploaded_url = file_info.output.get("url")
        if not uploaded_url:
            raise RuntimeError(f"DashScope 文件信息未返回 URL: {file_info.output}")
        logger.info("DashScope 文件URL: %s...", uploaded_url[:60])

        # 3. 提交转写 — 实时模型用 Recognition（直接传本地文件），离线模型用 Transcription
        if "realtime" in model:
            output = self._call_realtime(str(path), model)
        else:
            output = self._call_async(uploaded_url, model)

        if not output:
            raise RuntimeError("DashScope 转写返回空结果")
        if not output:
            raise RuntimeError("DashScope 转写返回空结果")

        # 5. 解析结果
        transcription_url = self._find_transcription_url(output)
        if transcription_url:
            logger.info("DashScope 下载转写结果: %s...", transcription_url[:60])
            try:
                resp = requests.get(transcription_url, timeout=60)
                resp.raise_for_status()
                data = resp.json()
            except requests.RequestException as e:
                logger.exception("DashScope 转写结果下载失败")
                raise RuntimeError(f"转写结果下载失败: {str(e)[:200]}") from e

            segments = []
            for t in data.get("transcripts", []):
                for s in t.get("sentences", []):
                    text = (s.get("text") or "").strip()
                    if text:
                        segments.append(TranscriptSegment(
                            start=round(float(s.get("begin_time", 0)) / 1000, 2),
                            end=round(float(s.get("end_time", 0)) / 1000, 2),
                            text=text,
                        ))

            # fun-asr: 只有全文无分句 → 包装成单段
            if not segments:
                full_text = " ".join(t.get("text", "") for t in data.get("transcripts", [])).strip()
                segments = [TranscriptSegment(start=0, end=0, text=full_text)]

            full_text = " ".join(s.text for s in segments)
            logger.info("DashScope ASR done: %d segs (via transcription_url)", len(segments))
            return TranscriptResult(
                language=data.get("language", "zh"),
                duration=segments[-1].end if segments else 0,
                segments=segments,
                full_text=full_text,
            )

        # paraformer-v2 fallback: 结果直接在 output 中
        utterances = output.get("utterances") or output.get("results") or []
        segments = []
        for u in utterances:
            text = (u.get("text") or "").strip()
            if text:
                segments.append(TranscriptSegment(
                    start=float(u.get("start_time", u.get("start", 0))),
                    end=float(u.get("end_time", u.get("end", 0))),
                    text=text,
                ))

        full_text = " ".join(s.text for s in segments)
        logger.info("DashScope ASR done: %d segs (fallback)", len(segments))
        return TranscriptResult(
            language=output.get("language", "zh"),
            duration=segments[-1].end if segments else 0,
            segments=segments,
            full_text=full_text,
        )
