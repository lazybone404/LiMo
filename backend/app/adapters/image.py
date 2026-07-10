"""
图像适配器 — 图片 → OCR + VL 描述
支持 URL 输入（下载后 OCR）
"""

import copy
import json
import logging
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests

from app.core.types import PipelineInput, AdapterOutput, RawMetadata, InputType, ProgressEvent
from app.core.protocols import PipelineAdapter
from app.core.registry import EngineRegistry

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

    def _get_dashscope_key(self) -> str:
        """从注入的配置中读取 DashScope API Key"""
        cfg = self._engine_config or {}
        for ep in cfg.get("engine_providers", []):
            if ep.get("id") == "dashscope":
                return ep.get("api_key", "")
        return ""

    def _resolve_ocr_engine(self):
        """根据 engine_bindings.ocr 解析第一个可用引擎"""
        cfg = self._engine_config or {}
        bindings = cfg.get("engine_bindings", {})
        engine_models = cfg.get("engine_models", [])

        model_ids = bindings.get("ocr", [])
        # 向后兼容：旧格式 int → [int]
        if isinstance(model_ids, int):
            model_ids = [model_ids]
        if not model_ids:
            auto_fallback = cfg.get("engine_auto_fallback", True)
            if not auto_fallback:
                logger.error("OCR 引擎未绑定，自动降级已关闭")
                return "", None, ""
            # 遍历所有已注册 OCR 引擎，找第一个可用的
            for eng_name in EngineRegistry.list_category("ocr"):
                engine = EngineRegistry.get("ocr", eng_name)
                if engine:
                    default_model = "qwen-vl-max" if "dashscope" in eng_name else "default"
                    logger.info("OCR: 未绑定引擎，使用第一个可用: %s", eng_name)
                    return eng_name, engine, default_model
            return "", None, ""

        emap = {m["id"]: m for m in engine_models}
        for mid in model_ids:
            bm = emap.get(mid)
            if not bm:
                continue
            provider_id = bm["provider_id"]
            model_name = bm["model_name"]
            if provider_id == "dashscope":
                engine = EngineRegistry.get("ocr", "dashscope-ocr")
                if engine:
                    return "dashscope-ocr", engine, model_name
            else:
                # 直接用 model_name 查找引擎
                engine = EngineRegistry.get("ocr", model_name)
                if engine:
                    return model_name, engine, model_name

        # 所有绑定均不可用
        auto_fallback = cfg.get("engine_auto_fallback", True)
        if not auto_fallback:
            logger.error("OCR 绑定链全部失败，自动降级已关闭")
            return "", None, ""

        # 尝试任意已注册的 OCR 引擎
        for eng_name in EngineRegistry.list_category("ocr"):
            engine = EngineRegistry.get("ocr", eng_name)
            if engine:
                default_model = "qwen-vl-max" if "dashscope" in eng_name else "default"
                logger.warning("OCR 绑定链全部失败，降级使用: %s", eng_name)
                return eng_name, engine, default_model

        logger.error("无可用 OCR 引擎")
        return "", None, ""

    def _ocr(self, file_path: str) -> str:
        """调用 OCR 引擎提取文字"""
        eng_name, engine, model_name = self._resolve_ocr_engine()
        if not engine:
            logger.warning("OCR 引擎未注册: %s", eng_name)
            return ""

        # 深拷贝防并发竞态
        engine = copy.copy(engine)

        # 动态注入 API Key
        if eng_name == "dashscope-ocr" and hasattr(engine, 'api_key') and not engine.api_key:
            key = self._get_dashscope_key()
            if key:
                engine.api_key = key
                logger.info("OCR: 动态注入 DashScope API Key")

        try:
            result = engine.run(file_path, model_name=model_name)
            if result and result.text:
                logger.info("OCR: %s 成功 (%d 字)", eng_name, len(result.text))
                return result.text
            logger.warning("OCR: %s 返回空结果", eng_name)
            return ""
        except Exception as e:
            logger.warning("OCR: %s 失败: %s", eng_name, str(e)[:120])
            return ""

    async def adapt(self, input: PipelineInput, on_progress=None,
                    engine_config: dict | None = None) -> AdapterOutput:
        # 使用传入的引擎配置（防并发竞态）
        if engine_config is not None:
            self._engine_config = dict(engine_config)

        path: Path

        # URL → 先下载
        if input.type == InputType.URL and input.url:
            path = self._download_url(input.url, on_progress)
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
