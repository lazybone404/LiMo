"""
PyMuPDF4LLM 引擎插件 — AGPL-3.0 许可证，用户自行承担协议风险。

安装:
  pip install pymupdf4llm pymupdf
  复制此文件到 ~/.limo/plugins/

LiMo 启动时自动发现并注册为 doc 引擎。
"""

import logging

from app.engines.plugin_loader import EnginePlugin

logger = logging.getLogger(__name__)


class PyMuPDF4LLMPlugin(EnginePlugin):
    name = "pymupdf4llm"
    category = "doc"
    license = "AGPL-3.0"
    description = ("PyMuPDF4LLM 最佳 PDF 转录 — 文字+表格+嵌入图+矢量图\n"
                   "⚠️ AGPL v3 许可证，商用需获取授权")
    file_types = [".pdf"]

    def process(self, file_path: str, config: dict) -> str:
        """一行调用，自动处理文字/表格/图片/矢量图"""
        import pymupdf4llm

        media_dir = config.get("media_dir", "")

        md = pymupdf4llm.to_markdown(
            str(file_path),
            write_images=True,
            image_path=media_dir if media_dir else None,
            image_format="png",
            dpi=150,
        )

        logger.info("pymupdf4llm plugin: output %d chars", len(md))
        return md
