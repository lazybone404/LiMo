"""
图像适配器 — 图片 → 导入（存文件 + 元数据）
支持 URL 输入（下载后存本地）

⚠️ **导入阶段刻意不做 OCR / VL 描述**：处理统一由 `POST /process/{id}` 触发
（4 个模式：提取文字 / 表格 / 数字化 / 理解），走 LLM 优先 → 引擎回退。
本类此前的 `_ocr` / `_resolve_ocr_engine` / `_get_dashscope_key` 三件套
是处理链路迁走后的历史残留（全仓零调用），2026-10-06 清理。
"""

import asyncio
import json
import logging
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests

from app.core.types import PipelineInput, AdapterOutput, RawMetadata, InputType, ProgressEvent
from app.core.protocols import PipelineAdapter

logger = logging.getLogger(__name__)

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"}


class ImageAdapter(PipelineAdapter):
    name = "image"
    _engine_config: dict | None = None  # injected by ingest route

    def accept(self, input: PipelineInput) -> bool:
        if input.type == InputType.IMAGE:
            return True
        if input.type == InputType.FILE:
            return Path(input.file_path or "").suffix.lower() in IMAGE_EXT
        # URL 指向图片文件
        if input.type == InputType.URL:
            url_path = urlparse(input.url or "").path.lower()
            return any(url_path.endswith(ext) for ext in IMAGE_EXT)
        return False

    def _download_url(self, url: str, on_progress=None) -> Path:
        """下载图片 URL 到临时文件"""
        from app.utils.url_security import validate_url
        try:
            validate_url(url)
        except ValueError as e:
            raise RuntimeError(f"URL 安全校验失败: {str(e)[:150]}") from e

        import requests
        tmp = Path(tempfile.mkdtemp(prefix="img_dl_"))
        parsed = urlparse(url)
        fname = Path(parsed.path).name or "image"
        dest = tmp / fname

        if on_progress:
            on_progress(ProgressEvent(phase="download", status="running",
                                      message=f"下载图片: {fname}"))
        try:
            resp = requests.get(url, timeout=60, stream=True)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.warning("图片下载失败: %s — %s", url[:80], str(e)[:120])
            raise RuntimeError(f"图片下载失败: {str(e)[:150]}") from e
        dest.write_bytes(resp.content)
        logger.info("图片下载: %s → %s (%.1fKB)", url[:60], fname, dest.stat().st_size / 1024)
        if on_progress:
            on_progress(ProgressEvent(phase="download", status="done"))
        return dest

    async def adapt(self, input: PipelineInput, on_progress=None,
                    engine_config: dict | None = None) -> AdapterOutput:
        # 使用传入的引擎配置（防并发竞态）
        if engine_config is not None:
            self._engine_config = dict(engine_config)

        path: Path

        # URL → 先下载（to_thread 释放事件循环，否则 requests.get 最长 60s 阻塞整个后端）
        if input.type == InputType.URL and input.url:
            path = await asyncio.to_thread(self._download_url, input.url, on_progress)
        else:
            path = Path(input.file_path or "")

        ext = path.suffix.lower()
        size_kb = path.stat().st_size / 1024

        if on_progress:
            on_progress(ProgressEvent(phase="import", status="done",
                                      message=f"图片已导入 ({size_kb:.0f}KB)"))

        # 导入时不做处理，只存文件信息和元数据
        # 处理由 POST /process-image/{id} 单独触发（4 个模式：提取文字/表格/数字化/理解）
        return AdapterOutput(
            raw_text=json.dumps({
                "type": "image",
                "format": ext.lstrip("."),
                "size_kb": round(size_kb, 1),
                "file_path": str(path),
            }, ensure_ascii=False),
            metadata=RawMetadata(
                warnings=[],
            ),
            engine="image_import",
        )
