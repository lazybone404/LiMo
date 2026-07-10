"""
VideoReader — 视频帧提取 + 网格拼接 + base64 编码
移植自 BiliNote backend/app/utils/video_reader.py，简化字体内联
"""
import base64
import hashlib
import logging
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

import ffmpeg
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)


class VideoReader:
    def __init__(
        self,
        video_path: str,
        grid_size=(2, 2),
        frame_interval=6,
        dedupe_enabled=True,
        unit_width=960,
        unit_height=540,
        save_quality=90,
        quality_threshold=0.0,
        frame_dir=None,
        grid_dir=None,
    ):
        self.video_path = video_path
        self.grid_size = grid_size
        self.frame_interval = frame_interval
        self.dedupe_enabled = dedupe_enabled
        self.unit_width = unit_width
        self.unit_height = unit_height
        self.save_quality = save_quality
        self.quality_threshold = quality_threshold
        self.frame_dir = frame_dir or tempfile.mkdtemp(prefix="vl_frames_")
        self.grid_dir = grid_dir or tempfile.mkdtemp(prefix="vl_grids_")

    @staticmethod
    def _calculate_file_md5(file_path: str) -> str:
        hasher = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def format_time(self, seconds: float) -> str:
        mm = int(seconds // 60)
        ss = int(seconds % 60)
        return f"{mm:02d}_{ss:02d}"

    def extract_time_from_filename(self, filename: str) -> float:
        match = re.search(r"frame_(\d{2})_(\d{2})\.jpg", filename)
        if match:
            mm, ss = map(int, match.groups())
            return mm * 60 + ss
        return float("inf")

    def _extract_single_frame(self, ts: int) -> str | None:
        time_label = self.format_time(ts)
        output_path = os.path.join(self.frame_dir, f"frame_{time_label}.jpg")
        cmd = [
            "ffmpeg", "-ss", str(ts), "-i", self.video_path,
            "-frames:v", "1", "-q:v", "2", "-y", output_path,
            "-hide_banner", "-loglevel", "error",
        ]
        try:
            subprocess.run(cmd, check=True)
            return output_path
        except subprocess.CalledProcessError:
            return None

    def extract_frames(self, max_frames=1000) -> list[str]:
        try:
            os.makedirs(self.frame_dir, exist_ok=True)
            duration = float(ffmpeg.probe(self.video_path)["format"]["duration"])
            timestamps = [i for i in range(0, int(duration), self.frame_interval)][:max_frames]

            max_workers = min(os.cpu_count() or 4, 8, len(timestamps))
            frame_results: dict[int, str | None] = {}
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {pool.submit(self._extract_single_frame, ts): ts for ts in timestamps}
                for future in as_completed(futures):
                    ts = futures[future]
                    frame_results[ts] = future.result()

            image_paths = []
            last_hash = None
            for ts in timestamps:
                output_path = frame_results.get(ts)
                if not output_path or not os.path.exists(output_path):
                    continue

                if self.dedupe_enabled:
                    frame_hash = self._calculate_file_md5(output_path)
                    if frame_hash == last_hash:
                        os.remove(output_path)
                        continue
                    last_hash = frame_hash

                image_paths.append(output_path)

            if self.quality_threshold > 0.0 and image_paths:
                from .frame_quality import filter_quality_frames
                image_paths = filter_quality_frames(image_paths, self.quality_threshold)

            return image_paths
        except Exception as e:
            logger.error("分割帧发生错误：%s", e)
            raise ValueError("视频处理失败") from e

    def group_images(self) -> list[list[str]]:
        image_files = [
            os.path.join(self.frame_dir, f) for f in os.listdir(self.frame_dir)
            if f.startswith("frame_") and f.endswith(".jpg")
        ]
        image_files.sort(key=lambda f: self.extract_time_from_filename(os.path.basename(f)))
        group_size = self.grid_size[0] * self.grid_size[1]
        return [image_files[i:i + group_size] for i in range(0, len(image_files), group_size)]

    def concat_images(self, image_paths: list[str], name: str) -> str:
        os.makedirs(self.grid_dir, exist_ok=True)
        font = ImageFont.load_default()
        images = []

        for path in image_paths:
            img = Image.open(path).convert("RGB").resize(
                (self.unit_width, self.unit_height), Image.Resampling.LANCZOS
            )
            timestamp = re.search(r"frame_(\d{2})_(\d{2})\.jpg", os.path.basename(path))
            time_text = f"{timestamp.group(1)}:{timestamp.group(2)}" if timestamp else ""
            draw = ImageDraw.Draw(img)
            draw.text((10, 10), time_text, fill="yellow", font=font, stroke_width=1, stroke_fill="black")
            images.append(img)

        cols, rows = self.grid_size
        grid_img = Image.new("RGB", (self.unit_width * cols, self.unit_height * rows), (255, 255, 255))

        for i, img in enumerate(images):
            x = (i % cols) * self.unit_width
            y = (i // cols) * self.unit_height
            grid_img.paste(img, (x, y))

        save_path = os.path.join(self.grid_dir, f"{name}.jpg")
        grid_img.save(save_path, quality=self.save_quality)
        return save_path

    def encode_images_to_base64(self, image_paths: list[str]) -> list[str]:
        base64_images = []
        for path in image_paths:
            with open(path, "rb") as img_file:
                encoded_string = base64.b64encode(img_file.read()).decode("utf-8")
                base64_images.append(f"data:image/jpeg;base64,{encoded_string}")
        return base64_images

    def run(self) -> list[str]:
        logger.info("开始提取视频帧...")
        try:
            os.makedirs(self.frame_dir, exist_ok=True)
            os.makedirs(self.grid_dir, exist_ok=True)

            self.extract_frames()
            logger.info("开始拼接网格图...")

            image_paths = []
            groups = self.group_images()
            for idx, group in enumerate(groups, start=1):
                min_frames = self.grid_size[0] * self.grid_size[1]
                if len(group) < min_frames:
                    logger.warning("跳过第 %d 组，图片不足 %d 张", idx, min_frames)
                    continue
                out_path = self.concat_images(group, f"grid_{idx}")
                image_paths.append(out_path)

            logger.info("编码图像...")
            urls = self.encode_images_to_base64(image_paths)
            return urls
        except Exception as e:
            logger.error("视频处理失败：%s", e)
            raise ValueError("视频处理失败") from e
