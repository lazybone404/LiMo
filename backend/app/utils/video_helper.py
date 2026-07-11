"""
视频辅助 — ffmpeg 单帧截图
"""
import os
import subprocess
import uuid
from pathlib import Path


def generate_screenshot(video_path: str, output_dir: str, timestamp: int, index: int) -> str:
    """使用 ffmpeg 在指定时间戳提取单帧，返回图片路径"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    filename = f"screenshot_{index:03}_{uuid.uuid4().hex[:8]}.jpg"
    output_path = output_dir / filename

    command = [
        "ffmpeg",
        "-ss", str(timestamp),
        "-i", str(video_path),
        "-frames:v", "1",
        "-q:v", "2",
        str(output_path),
        "-y",
        "-hide_banner", "-loglevel", "error",
    ]

    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                           errors='replace', creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 截图失败: {result.stderr}")

    return str(output_path)
