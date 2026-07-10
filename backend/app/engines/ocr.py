"""
OCR 引擎 — 图片文字提取

云端引擎走 OpenAI 兼容接口 + VL 模型（零新依赖），
后续可扩展 PaddleOCR / 百度 OCR 等专用 API。
"""

import base64
import logging
from dataclasses import dataclass

from app.core.protocols import Engine

logger = logging.getLogger(__name__)


@dataclass
class OCRResult:
    text: str
    engine: str = ""
    confidence: float = 0.0


class DashScopeOCREngine(Engine):
    """阿里云 DashScope — 通过 VL 模型提取图片文字

    走 OpenAI 兼容接口（/compatible-mode/v1），复用已有 dashscope key + base_url。
    VL 模型可以处理所有 4 种模式。
    """

    name = "dashscope-ocr"
    supported_modes = ["extract", "table", "digitize", "understand"]

    def __init__(self, api_key: str = "", base_url: str = ""):
        self.api_key = api_key
        self.base_url = base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        self._model_name = "qwen-vl-max"  # 默认 VL 模型
        self.capabilities = ["text", "table", "ocr", "image"]

    def execute(self, file_path: str, model_name: str = "", mode: str = "default") -> OCRResult:
        """从图片提取文字或理解内容。

        mode:
          - "default": 提取所有文字，保留段落结构
          - "table": 表格优先——优先识别表格，输出 Markdown 表格
          - "digitize": 文档数字化——扫描件转可编辑文本，保留文档结构和格式
          - "understand": 内容理解——描述并解读图片内容
        """
        from pathlib import Path
        from openai import OpenAI

        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"图片不存在: {file_path}")

        if not self.api_key:
            raise RuntimeError("DashScope API key 未配置")

        model = model_name or self._model_name

        # 编码图片
        ext = path.suffix.lower()
        mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp"}
        mime = mime_map.get(ext, "image/png")
        image_b64 = base64.b64encode(path.read_bytes()).decode()
        data_url = f"data:{mime};base64,{image_b64}"

        # 按模式选择 prompt
        if mode == "table":
            prompt_text = (
                "请提取并输出这张图片中的表格数据。\n"
                "规则：\n"
                "1. 只提取数据表格本身——忽略编辑器工具栏、菜单栏、状态栏、窗口边框等界面元素\n"
                "2. 忽略与表格数据无关的任何文字（如应用程序名称、按钮标签、提示信息）\n"
                "3. 用 Markdown 表格格式输出所有表格\n"
                "4. 表格前后可以用简短文字说明表格标题（仅当标题属于数据内容时）\n"
                "5. 如果表格中有合并单元格，尽量还原为多行\n"
                "6. 如果图片中没有数据表格，回复「未检测到表格数据」\n"
                "7. 只输出结果，不要添加任何解释或对话"
            )
        elif mode == "digitize":
            prompt_text = (
                "请将这张扫描件/文档图片转换为可编辑的 Markdown 文本。\n"
                "规则：\n"
                "1. 完整提取所有文字内容，不要遗漏\n"
                "2. 保留原始文档结构：标题层级（用 #/##/###）、列表、缩进、分隔线\n"
                "3. 表格用 Markdown 表格格式输出\n"
                "4. 扫描噪声导致的文字歧义请根据上下文纠正，标注不确定处为 [?]\n"
                "5. 保留页码、页眉、页脚信息（如有）\n"
                "6. 只输出结果，不要添加任何解释或对话"
            )
        elif mode == "understand":
            prompt_text = (
                "请描述并解读这张图片的内容。\n"
                "规则：\n"
                "1. 先概括画面整体内容（1-2句）\n"
                "2. 然后按要点列出关键信息和细节\n"
                "3. 如果图片中有文字，提取并融入描述中\n"
                "4. 如果图片中有数据/图表，解读其含义\n"
                "5. 用 Markdown 格式输出，不要添加对话"
            )
        else:
            prompt_text = (
                "请提取并输出这张图片中的全部文字内容。\n"
                "规则：\n"
                "1. 保留原始段落结构和换行\n"
                "2. 如果图片中有表格，用 Markdown 表格格式输出\n"
                "3. 忽略编辑器工具栏、菜单栏、状态栏、窗口边框等软件界面元素\n"
                "4. 忽略与正文内容无关的界面文字（如应用名、按钮标签、提示信息）\n"
                "5. 只输出文字，不要添加任何解释、说明或对话\n"
                "6. 如果图片中没有文字，回复「无文字内容」"
            )

        logger.info("OCR: %s (%.1fKB) model=%s mode=%s", path.name, len(image_b64) / 1024, model, mode)

        try:
            client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=120.0)

            response = client.chat.completions.create(
                model=model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
                    ],
                }],
                max_tokens=4096,
                temperature=0.1,
            )

            text = response.choices[0].message.content or ""
            logger.info("OCR done: %d chars", len(text))

            return OCRResult(
                text=text.strip(),
                engine="dashscope-ocr",
                confidence=0.0,
            )
        except Exception as e:
            logger.exception("OCR API 调用失败: model=%s mode=%s", model, mode)
            raise RuntimeError(f"OCR API 调用失败: {str(e)[:200]}") from e


class TesseractEngine(Engine):
    """本地 Tesseract OCR — pip install pytesseract + 系统安装 tesseract

    轻量级（~90MB 含中文包），仅支持文字提取模式。
    安装：
      Windows: choco install tesseract  或  https://github.com/UB-Mannheim/tesseract/wiki
      macOS:   brew install tesseract tesseract-lang
      Linux:   sudo apt install tesseract-ocr tesseract-ocr-chi-sim

    路径优先级：tesseract_cmd 参数 → TESSERACT_CMD 环境变量 → PATH 自动查找
    """

    name = "tesseract"
    supported_modes = ["extract"]
    capabilities = ["text", "ocr"]

    # Tesseract 预设配置：识别模式 + 页面分割
    DEFAULT_CONFIG = "--psm 3"      # 自动页面分割
    TABLE_CONFIG = "--psm 6"        # 统一文本块

    def __init__(self, lang: str = "chi_sim+eng", tesseract_cmd: str = ""):
        self.lang = lang
        self._tesseract_cmd = tesseract_cmd

    def _resolve_tesseract_cmd(self) -> str:
        """解析 tesseract 可执行文件路径"""
        import os
        import shutil

        # 1. 显式传入
        if self._tesseract_cmd:
            return self._tesseract_cmd

        # 2. 环境变量
        env_cmd = os.environ.get("TESSERACT_CMD", "")
        if env_cmd:
            return env_cmd

        # 3. PATH 查找
        found = shutil.which("tesseract")
        if found:
            return found

        # 4. 项目内置便携版
        project_bin = os.path.join(os.path.dirname(__file__), "..", "..", "bin", "tesseract", "tesseract.exe")
        project_bin = os.path.abspath(project_bin)
        if os.path.isfile(project_bin):
            return project_bin

        # 5. Windows 常见路径自动检测
        if os.name == "nt":
            candidates = [
                os.path.join(os.environ.get("ProgramFiles", "C:\\Program Files"), "Tesseract-OCR", "tesseract.exe"),
                os.path.join(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"), "Tesseract-OCR", "tesseract.exe"),
            ]
            # 用户的 Downloads 目录（便携版解压后）
            downloads = os.path.join(os.path.expanduser("~"), "Downloads")
            if os.path.isdir(downloads):
                for name in os.listdir(downloads):
                    if "tesseract-ocr" in name.lower():
                        exe = os.path.join(downloads, name, "tesseract.exe")
                        if os.path.isfile(exe):
                            candidates.append(exe)
            for p in candidates:
                if os.path.isfile(p):
                    return p

        return "tesseract"  # 回退默认值，让 pytesseract 自己报错

    def execute(self, file_path: str, model_name: str = "", mode: str = "default") -> OCRResult:
        """Tesseract OCR 文字提取

        mode 自动映射：
          - extract / default: 保留段落结构 (psm 3)
          - table: 统一文本块便于表格提取 (psm 6)
        """
        try:
            import pytesseract
            from PIL import Image
        except ImportError:
            raise RuntimeError(
                "Tesseract 引擎需要安装 pytesseract: pip install pytesseract Pillow"
            )

        # 设置 tesseract 路径
        tesseract_cmd = self._resolve_tesseract_cmd()
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

        img = Image.open(file_path)
        psm = self.TABLE_CONFIG if mode == "table" else self.DEFAULT_CONFIG

        logger.info(
            "Tesseract: file=%s size=%s lang=%s psm=%s cmd=%s",
            file_path, img.size, self.lang, psm, tesseract_cmd
        )

        try:
            text = pytesseract.image_to_string(img, lang=self.lang, config=psm)
        except pytesseract.TesseractNotFoundError:
            raise RuntimeError(
                "Tesseract 未安装或未找到。\n"
                "  下载: https://github.com/UB-Mannheim/tesseract/releases\n"
                "  便携版: 解压后设置环境变量 TESSERACT_CMD=路径/tesseract.exe\n"
                "  Windows: winget install UB-Mannheim.tesseract-ocr\n"
                "  macOS:   brew install tesseract\n"
                "  Linux:   sudo apt install tesseract-ocr\n"
                "中文支持需 chi_sim.traineddata 放入 tessdata 目录"
            )

        if not text or not text.strip():
            logger.info("Tesseract: 未检测到文字")
            return OCRResult(text="", engine="tesseract", confidence=0.0)

        logger.info("Tesseract: %d chars", len(text))
        return OCRResult(text=text.strip(), engine="tesseract", confidence=0.0)


# 后续扩展示例：
# class PaddleOCREngine(Engine):
#     """本地 PaddleOCR — pip install paddlepaddle paddleocr
#     仅支持文字提取，不支持表格/数字化/理解（这些需要 VL 模型）。
#     """
#     name = "paddle-ocr"
#     supported_modes = ["extract"]  # PP-Structure 装好后可加 "table"
#     def execute(self, file_path: str) -> OCRResult: ...
#
# class BaiduOCREngine(Engine):
#     """百度 OCR — 云端 API"""
#     name = "baidu-ocr"
#     supported_modes = ["extract"]  # 通用文字识别
#     def execute(self, file_path: str) -> OCRResult: ...
