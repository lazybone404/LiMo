"""OCR 引擎 — DeepSeek 文档解析（`POST /v1/tools/ocr`）。

与 DashScope 那台的区别在于**没有 prompt、没有模式**：接口吃一份文件（PDF /
Word / PPT / 图片），吐「按页的文本」。所以这里做的事只有三件 —— 发文件、
按页排序、把版面标记摘掉。

⚠️ **`purpose` 一律 `text_recognition`**。另一个取值 `rich_layout_recognition`
多返回的是图片在页面上的位置（`<｜box｜>` 坐标），文本内容本身不变；把
「表格模式」映射到它不是「识别得更好」，而是我们凭空赋予了一个接口不存在的语义。
`supported_modes` 里因此没有 `understand` —— 这个接口不产出描述，列上就是给用户
一个做不到的入口（`routers/process.py` 对不支持的 mode 会跳过该引擎）。

⚠️ 官方限制：单文件 ≤100MiB、≤1000 页、总处理时长 180 秒。超时给 240 秒留余量。
"""

import logging
import re
from pathlib import Path

import requests

from app.core.deepseek_api import TOOL_OCR_URL, check_response
from app.core.errors import AppError
from app.core.protocols import Engine
from app.engines.ocr import OCRResult

logger = logging.getLogger(__name__)

#: 官方上限
MAX_OCR_BYTES = 100 * 1024 * 1024
MAX_OCR_PAGES = 1000

#: 官方总处理时限 180 秒，留余量
OCR_TIMEOUT = (30, 240)

#: `rich_layout_recognition` 会在文本里插入版面标记：
#:   `<｜ref｜>标题<｜/ref｜><｜box｜>[[x1,y1,x2,y2]]<｜/box｜>`
#: 前两个（ref 开闭）只是把标题括起来，后一个框住坐标。坐标是 0-1000 归一化值，
#: 目前没有任何消费方（渲染器按文字排版，不按坐标画），留着只会出现在生成稿、
#: 导出与全文索引里。故整体摘掉，只留文字。
_BOX_BLOCK_RE = re.compile(r"<｜box｜>.*?<｜/box｜>", re.S)
_LAYOUT_TOKEN_RE = re.compile(r"<｜[^｜]*｜>")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")


def strip_layout_tokens(text: str) -> str:
    """去掉版面标记，只留文字。

    `rich_layout_recognition` 的返回文本里，标题会被 `<｜ref｜>…<｜/ref｜>` 括起来，
    其后跟一段 `<｜box｜>[[x1,y1,x2,y2]]<｜/box｜>`。坐标块必须**先**整体删掉 ——
    只删标记的话，`[[...]]` 这串数字会留在正文里。
    """
    if not text:
        return ""
    out = _BOX_BLOCK_RE.sub("", text)
    # 兜底：未成对的 ref / box 标记（接口截断时可能出现半截）
    out = _LAYOUT_TOKEN_RE.sub("", out)
    return _MULTI_BLANK_RE.sub("\n\n", out).strip()


class DeepSeekOCREngine(Engine):
    """DeepSeek 文档解析 — 图片/文档整份进，按页文本出。"""

    name = "deepseek-ocr"
    supported_modes = ["extract", "table", "digitize"]
    capabilities = ["text", "table", "ocr"]

    def __init__(self, api_key: str = "", base_url: str = ""):
        #: 调用时由 `engines/credentials.py::apply_provider_credentials` 注入
        self.api_key = api_key
        self.base_url = base_url

    def execute(self, file_path: str, model_name: str = "", mode: str = "default") -> OCRResult:
        """`model_name` / `mode` 只用于满足 `Engine` 契约，接口本身没有这两个维度。"""
        path = Path(file_path)
        if not path.exists():
            raise AppError("DEEPSEEK_OCR_FILE_MISSING", f"文件不存在: {path.name}",
                           recoverable=False)
        if not self.api_key:
            raise AppError("DEEPSEEK_NO_API_KEY",
                           "DeepSeek API Key 未配置，请在设置 → 引擎厂商 中填写",
                           recoverable=False,
                           hint="设置 → 引擎厂商 → DeepSeek")

        size = path.stat().st_size
        if size > MAX_OCR_BYTES:
            raise AppError(
                "DEEPSEEK_OCR_TOO_LARGE",
                f"文件 {size / 1024 / 1024:.1f}MB 超过 DeepSeek OCR 上限"
                f"（{MAX_OCR_BYTES // 1024 // 1024}MB）",
                recoverable=False)

        logger.info("DeepSeek OCR 开始: %s (%.1fKB)", path.name, size / 1024)

        # ⚠️ multipart 字段顺序：官方要求 `purpose` 在 `file` 之前。requests 编码时
        # 先写 `data` 再写 `files`，故这两个参数的分工不能调换（有测试钉住顺序）。
        with path.open("rb") as fh:
            resp = requests.post(
                TOOL_OCR_URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                data={"purpose": "text_recognition"},
                files={"file": (path.name, fh)},
                timeout=OCR_TIMEOUT,
            )

        body = check_response(resp, "OCR 识别")
        text = _pages_to_text(body)
        if not text:
            raise AppError("DEEPSEEK_OCR_EMPTY", "DeepSeek OCR 返回空结果",
                           detail=str(body)[:200], recoverable=False)

        logger.info("DeepSeek OCR 完成: %d 页, %d 字", len(body.get("pages") or []), len(text))
        return OCRResult(text=text, engine=self.name, confidence=1.0)


def _pages_to_text(body: dict) -> str:
    """`pages[]` → 按页序拼接的 Markdown 文本。

    ⚠️ 页码以响应里的 `index` 为准而不是数组下标：接口不保证顺序，按数组顺序拼
    会让两页内容互换，而两边都"成功"（与双产物解包反了是同一类失效）。
    `finish_reason == "length"` 说明该页被截断 —— 记一条 warning 带页码，
    但它仍是部分成功，不往正文插占位文本（占位会流进生成稿与索引）。
    """
    pages = body.get("pages") or []
    if not pages:
        return ""

    indexed: list[tuple[int, str]] = []
    for i, page in enumerate(pages):
        idx = page.get("index", i)
        text = strip_layout_tokens(page.get("text") or "")
        if page.get("finish_reason") == "length":
            logger.warning("DeepSeek OCR 第 %s 页被截断（finish_reason=length）", idx)
        if text:
            indexed.append((idx, text))

    indexed.sort(key=lambda item: item[0])
    text = "\n\n".join(t for _, t in indexed)

    if len(pages) > MAX_OCR_PAGES:
        logger.warning("DeepSeek OCR 返回 %d 页，超过官方上限 %d", len(pages), MAX_OCR_PAGES)
    return text
