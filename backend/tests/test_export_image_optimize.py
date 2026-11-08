"""导出 ZIP 打包图片时复用「图片预处理」的判据（TODO #5，2026-11-08）。

**原来的前提是错的**：TODO 写的是「导入时已 `optimize_images`，导出 ZIP 未复用压缩后的
图片」。实际上 `optimize_images` 只作用于**发给 LLM 的内存 data URI**
（`content_preparer._optimize_image_data`），落盘的 `assets/*` 一直是原图 ——
盘上从来没有一份「压缩后的图片」可供复用。故本轮的实现是**在导出时按同一判据缩小**，
而不是「取另一份文件」。

判据落在 `app/core/image_optimize.py` 一处：长边 > 1024 才处理、等比缩、重编码后
反而更大就不换、动图与解不开的图原样保留。三个消费方共用它，各自只决定**输出格式**
（LLM 只给模型看 → 统一 JPEG；落进用户文件的两条 → 保留原格式）。

⚠️ 每条断言对应一种「看起来对、其实错」的实现：

| 断言 | 写错会怎样 |
|---|---|
| 小图不被重编码 | 「反正都要压」→ 每张截图过一次有损编码，画质白掉 |
| 动图不被换 | 转 JPEG 只剩第一帧，GIF 动画静默丢失 |
| 透明区是白底 | `convert("RGB")` → 透明处变纯黑，导出图和预览不一样 |
| 缩小后扩展名跟着改 | `.png` 里装 JPEG 字节，别人用 `file` 一看就知是假的 |
| docx 按**源**文件名取图 | Word 里那张图静默缺失（不报错、只是没有图） |
| 三条 ZIP 入口都开 optimize | 只改定制导出，`/all` 与 `/batch` 照旧打包原图 |

运行：python -m pytest tests/test_export_image_optimize.py
"""
import io
import os
import re
from pathlib import Path

from PIL import Image

from app.core.image_optimize import MAX_EDGE, optimize_image_bytes
from app.routers.export import (
    _collect_images, _find_image_refs, _inline_images, _source_named,
)

_EXPORT_SRC = Path(__file__).resolve().parents[1] / "app" / "routers" / "export.py"
_PREPARE_SRC = Path(__file__).resolve().parents[1] / "app" / "generation" / "content_preparer.py"


def _img_bytes(size, fmt="PNG", mode="RGB", color=(200, 30, 30), transparent=False):
    """造一张**噪点**图：纯色图 PNG 压得极好，重编码成 JPEG 反而更大，会触发
    「处理后更大就别换」那条规则，测不到缩小逻辑本身。"""
    import random
    rnd = random.Random(42)
    channels = 4 if mode == "RGBA" else 3
    img = Image.frombytes(mode, size, rnd.randbytes(size[0] * size[1] * channels))
    if transparent:
        # 左上角一块全透明：缩小后应合成为白底
        for x in range(0, size[0] // 4):
            for y in range(0, size[1] // 4):
                img.putpixel((x, y), (0, 0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


# ── 判据本身 ──

def test_超过长边上限才缩小_且比例不变():
    big = _img_bytes((2000, 1000))
    out = optimize_image_bytes(big, keep_format=True)
    assert out is not None
    data, ext = out
    assert ext == "png"
    with Image.open(io.BytesIO(data)) as img:
        assert max(img.size) == MAX_EDGE
        assert img.size == (1024, 512)
    assert len(data) < len(big)


def test_小图原样返回_不做无谓的重编码():
    small = _img_bytes((800, 600))
    assert optimize_image_bytes(small) is None


def test_不保留格式时统一转_JPEG_以便只给模型看():
    out = optimize_image_bytes(_img_bytes((2000, 1500)), keep_format=False)
    assert out is not None
    data, ext = out
    assert ext == "jpg"
    with Image.open(io.BytesIO(data)) as img:
        assert img.format == "JPEG"


def test_保留格式时_PNG_仍是_PNG():
    out = optimize_image_bytes(_img_bytes((2000, 1500)), keep_format=True)
    assert out is not None
    with Image.open(io.BytesIO(out[0])) as img:
        assert img.format == "PNG"


def test_透明区合成白底_不是纯黑():
    rgba = _img_bytes((2000, 1000), mode="RGBA", color=(0, 0, 0, 255), transparent=True)
    out = optimize_image_bytes(rgba, keep_format=False)
    assert out is not None
    with Image.open(io.BytesIO(out[0])) as img:
        r, g, b = img.getpixel((2, 2))[:3]
    # `convert("RGB")` 会得到 (0,0,0)；白底合成应接近 255
    assert min(r, g, b) > 240, (r, g, b)


def test_动图不处理_转成静态会静默丢帧():
    frames = [Image.new("RGB", (1500, 800), c) for c in ((255, 0, 0), (0, 255, 0))]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], duration=200)
    assert optimize_image_bytes(buf.getvalue(), keep_format=True) is None


def test_解不开的字节原样保留_导出不因此失败():
    assert optimize_image_bytes(b"not an image at all") is None


# ── 接进导出：名字、MIME、docx 三条都必须跟着变 ──

def _doc_dir_with(tmp: Path, name: str, data: bytes) -> Path:
    doc_dir = tmp / "docs" / "样例-abc123"
    (doc_dir / "assets").mkdir(parents=True)
    (doc_dir / "assets" / name).write_bytes(data)
    return doc_dir


def test_收集时缩小并按新扩展名登记():
    tmp = Path(__import__("tempfile").mkdtemp(prefix="limo_opt_"))
    doc_dir = _doc_dir_with(tmp, "img_big.tiff", _img_bytes((2000, 1000), fmt="TIFF"))
    refs = _find_image_refs("![x](assets/img_big.tiff)")
    imgs, name_map = _collect_images(refs, str(tmp), doc_dir=str(doc_dir), optimize=True)

    assert name_map["img_big.tiff"] == "img_big.jpg"
    assert "img_big.jpg" in imgs
    with Image.open(io.BytesIO(imgs["img_big.jpg"])) as img:
        assert img.format == "JPEG"
        assert max(img.size) == MAX_EDGE


def test_不开优化时名字与字节都不动():
    tmp = Path(__import__("tempfile").mkdtemp(prefix="limo_opt_"))
    raw = _img_bytes((2000, 1000), fmt="PNG")
    doc_dir = _doc_dir_with(tmp, "img_big.png", raw)
    refs = _find_image_refs("![x](assets/img_big.png)")
    imgs, name_map = _collect_images(refs, str(tmp), doc_dir=str(doc_dir))
    assert name_map["img_big.png"] == "img_big.png"
    assert imgs["img_big.png"] == raw


def test_内联的_MIME_跟着最终名走():
    tmp = Path(__import__("tempfile").mkdtemp(prefix="limo_opt_"))
    doc_dir = _doc_dir_with(tmp, "img_big.tiff", _img_bytes((2000, 1000), fmt="TIFF"))
    text = "![x](assets/img_big.tiff)"
    refs = _find_image_refs(text)
    imgs, name_map = _collect_images(refs, str(tmp), doc_dir=str(doc_dir), optimize=True)
    out = _inline_images(text, imgs, name_map, str(tmp), doc_dir=str(doc_dir))
    assert "data:image/jpeg;base64," in out, out[:80]
    assert "image/tiff" not in out


def test_docx_按源文件名也能取到缩小后的字节():
    tmp = Path(__import__("tempfile").mkdtemp(prefix="limo_opt_"))
    doc_dir = _doc_dir_with(tmp, "img_big.tiff", _img_bytes((2000, 1000), fmt="TIFF"))
    refs = _find_image_refs("![x](assets/img_big.tiff)")
    imgs, name_map = _collect_images(refs, str(tmp), doc_dir=str(doc_dir), optimize=True)
    alias = _source_named(imgs, name_map)
    assert alias["img_big.tiff"] == imgs["img_big.jpg"]


# ── 同源守卫（源码级：三个消费方都必须走 core，不许各写一份阈值）──

def test_三个消费方都调用共用实现():
    export_src = _EXPORT_SRC.read_text(encoding="utf-8")
    prepare_src = _PREPARE_SRC.read_text(encoding="utf-8")
    assert "from app.core.image_optimize import optimize_image_bytes" in export_src
    assert "optimize_image_bytes(" in export_src
    assert "from app.core.image_optimize import" in prepare_src
    # 两条 data URI 通道都不得再自己写 1024 / Image.open
    prepare_body = prepare_src.split("def _optimize_image_data")[1]
    assert "Image.open" not in prepare_body.split("def _sanitize_image_uri")[0]
    assert "1024" not in prepare_body.split("def _sanitize_image_uri")[0]


def test_三条_ZIP_入口都开启了_optimize():
    src = _EXPORT_SRC.read_text(encoding="utf-8")
    calls = re.findall(r"_collect_images\([^)]*\)", src, flags=re.S)
    zip_calls = [c for c in calls if "optimize=True" in c]
    assert len(zip_calls) == 3, calls
