"""图片预处理的**共用判据**：多大的图算大、压到多少、什么时候不值得处理。

三个消费方，产出形态各不相同：

| 消费方 | 产物 | 格式策略 |
|---|---|---|
| 生成前把图片发给 LLM（`generation/content_preparer.py::_optimize_image_data`） | data URI | 统一转 JPEG（只有模型看） |
| VL 截帧图嵌入正文（`ContentPreparer._optimize_data_uri`） | data URI | **保留原格式**（图会进笔记、进导出） |
| 导出 ZIP 打包图片（`routers/export.py::_collect_images`） | 文件字节 + 扩展名 | **保留原格式**（用户的文件） |

判据（长边上限、质量、动图/损坏/变大时不处理）只有这一份：各调各的参数时，
用户导出的图与模型看到的图尺度会分叉，而两边都不会报错 —— 两张图都看得见，
只是不一样大，也没人会发现（本仓库所谓「同源」要防的正是这一类）。

⚠️ **本模块只做「缩小」**：裁剪、水印、格式互转都刻意没有 —— 没有消费方。
"""

import io
import logging

logger = logging.getLogger(__name__)

#: 长边上限（px）。超过就等比缩到这条边。
MAX_EDGE = 1024

#: 重编码时的质量（仅对 JPEG / WebP 生效，PNG 是无损 + `optimize`）。
JPEG_QUALITY = 80

#: `keep_format=True` 时允许保留的格式。其余（TIFF / BMP / 未知）统一转 JPEG ——
#: 这些格式要么浏览器显示不了，要么体积大且没人指望在导出里见到。
_KEEPABLE = {"PNG", "JPEG", "WEBP", "GIF"}

_EXT = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif"}


def optimize_image_bytes(
    data: bytes,
    *,
    max_edge: int = MAX_EDGE,
    quality: int = JPEG_QUALITY,
    keep_format: bool = False,
) -> tuple[bytes, str] | None:
    """长边 > `max_edge` 时等比缩小并重编码，返回 `(字节, 扩展名)`；不处理则 `None`。

    返回 `None` 表示「调用方应保留原字节与原文件名」，覆盖四种情况：

    - 本来就不超过 `max_edge` —— 不做无谓的重编码（截图、图标不会被二次压缩）；
    - PIL 打不开（损坏、SVG 等矢量格式）；
    - **动图**（GIF/APNG/动态 WebP）—— 只留第一帧等于静默丢内容；
    - 处理后反而更大 —— 小体积高分辨率的线稿可能如此，换来的只有质量损失。

    `keep_format=True` 在有损格式上仍按原格式编码（PNG 保持 PNG），用于产物会落进
    用户文件的场景；`False` 统一转 JPEG，用于只给模型看的图。

    ⚠️ **透明通道合成到白底**，不是直接 `convert("RGB")`。后者把透明区填成纯黑，
    导出的图表 / 截图会凭空多出一块黑底，而原图在预览里看着是白的。
    """
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            if max(width, height) <= max_edge:
                return None
            if getattr(img, "is_animated", False):
                logger.debug("图片预处理跳过：动图 %dx%d", width, height)
                return None
            source_fmt = (img.format or "").upper()
            target = source_fmt if (keep_format and source_fmt in _KEEPABLE) else "JPEG"

            ratio = max_edge / max(width, height)
            new_size = (max(1, int(width * ratio)), max(1, int(height * ratio)))

            has_alpha = img.mode in ("RGBA", "LA") or (
                img.mode == "P" and "transparency" in img.info
            )
            if target == "JPEG" and has_alpha:
                rgba = img.convert("RGBA").resize(new_size, Image.LANCZOS)
                out_img = Image.new("RGB", new_size, (255, 255, 255))
                out_img.paste(rgba, mask=rgba.split()[3])
            elif target in ("JPEG", "WEBP"):
                out_img = img.convert("RGB").resize(new_size, Image.LANCZOS)
            else:
                # PNG：保留透明通道（`optimize` 是无损压缩，不经过调色板量化）
                out_img = img.resize(new_size, Image.LANCZOS)

            buf = io.BytesIO()
            if target == "PNG":
                out_img.save(buf, format="PNG", optimize=True)
            else:
                out_img.save(buf, format=target, quality=quality, optimize=True)
    except Exception as e:  # 损坏 / 不支持的格式 —— 保留原字节，调用方不因此失败
        logger.debug("图片预处理跳过：无法解码（%s）", e)
        return None

    out = buf.getvalue()
    if len(out) >= len(data):
        logger.debug("图片预处理跳过：重编码后更大（%d → %d 字节）", len(data), len(out))
        return None
    logger.debug("图片预处理: %dx%d → %dx%d (%.1fKB → %.1fKB, %s)",
                 width, height, new_size[0], new_size[1],
                 len(data) / 1024, len(out) / 1024, _EXT.get(target, "jpg"))
    return out, _EXT.get(target, "jpg")
