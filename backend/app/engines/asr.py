"""
ASR 引擎 — faster-whisper
"""

import logging
from dataclasses import dataclass
from typing import Optional

from app.core.protocols import Engine

logger = logging.getLogger(__name__)


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass
class TranscriptResult:
    language: str
    duration: float
    segments: list[TranscriptSegment]
    full_text: str = ""


class FasterWhisperEngine(Engine):
    """faster-whisper 本地转写"""

    def __init__(self, model_size: str = "small"):
        self.model_size = model_size
        self.name = f"faster-whisper-{model_size}"
        self._model = None
        self.capabilities = ["asr"]

    def execute(self, audio_path: str) -> TranscriptResult:
        from faster_whisper import WhisperModel

        if self._model is None:
            logger.info(f"Loading faster-whisper {self.model_size}...")
            self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8")

        segments_raw, info = self._model.transcribe(
            audio_path, beam_size=5, vad_filter=True
        )
        segments = [
            TranscriptSegment(start=round(s.start, 2), end=round(s.end, 2), text=s.text.strip())
            for s in segments_raw
        ]
        full_text = " ".join(s.text for s in segments)
        logger.info(f"ASR done: {len(segments)} segs, lang={info.language}, RTF={info.duration and '%.2f' % (info.duration / max(1, sum(s.end-s.start for s in segments)))}")

        return TranscriptResult(
            language=info.language,
            duration=info.duration,
            segments=segments,
            full_text=full_text,
        )
