"""本地音频文件导入 —— 入口判据与转码纪律。

2026-11-18 之前：`VideoAdapter.accept()` 的文件分支只认视频扩展名，而 `adapt()`
里早就写好了「已是音频格式就跳过 ffmpeg」的分支 —— 那段代码永远走不到。能力在，
门没开。本文件钉住门开了，且开对。
"""

from app.adapters.video import AUDIO_FILE_EXTS, VIDEO_FILE_EXTS, VideoAdapter
from app.core.types import InputType, PipelineInput
from app.routers.ingest import _adapter_to_source_type, _TIER_MEDIA


def _file(p: str) -> PipelineInput:
    return PipelineInput(type=InputType.FILE, file_path=p)


def test_本地音频文件被接受():
    a = VideoAdapter()
    for ext in (".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aac"):
        assert a.accept(_file(f"/tmp/x{ext}")), ext


def test_视频文件仍然被接受():
    a = VideoAdapter()
    for ext in VIDEO_FILE_EXTS:
        assert a.accept(_file(f"/tmp/x{ext}")), ext


def test_无关扩展名仍然被拒():
    a = VideoAdapter()
    for ext in (".pdf", ".txt", ".png", ""):
        assert not a.accept(_file(f"/tmp/x{ext}")), ext


def test_sourceType_音频走_audio_视频走_video():
    # 两个类型决定生成侧给不给「视频截图」这类选项，不能合并
    assert _adapter_to_source_type("video", "/tmp/a.mp3") == "audio"
    assert _adapter_to_source_type("video", "/tmp/a.m4a") == "audio"
    assert _adapter_to_source_type("video", "/tmp/a.mp4") == "video"


def test_音频归媒体档_默认只收纳():
    # 与视频同档：导入后默认不自动转录，等用户点
    assert "audio" in _TIER_MEDIA


def test_音频扩展名表只在本文件定义一次():
    # 判据是「ffmpeg 能解」，不是「哪个引擎认」——两张表会让加引擎时要改两处
    assert ".m4a" in AUDIO_FILE_EXTS and ".m4a" not in VIDEO_FILE_EXTS
    assert len(AUDIO_FILE_EXTS & VIDEO_FILE_EXTS) == 0
