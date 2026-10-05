"""P1-07 — 截图失败必须把标记**替换掉**，不能原样留在正文里。

**修复前的形状**（`generation/generator.py::_post_process_screenshots`）：

    except Exception as e:
        logger.warning("截图生成失败 ts=%ds: %s", ts, e)   # ← 标记原样留在 markdown 里

⚠️ 这比「残留一段文本」更糟：前端把 `*Screenshot-[mm:ss]` 渲染成橙色「📷 截图标」
badge（`lib/markdown-components.tsx:237`）——于是**失败的产物看起来与成功的一模一样**，
只是那个 badge 点了没反应。用户既不知道图没生成，也不知道该重跑。

纪律与图表侧一致（`services/chart_renderer.py::_fail_note`）：**成功的产物里有引用，
失败的产物里有原因，永不留下内部标记**。

对照实验（均已实测，证明本文件有区分力）：
  A. 把 except 分支改回只 `logger.warning`（不替换）
     → `test_failure_*` 与 `test_partial_*` 红（产物里仍是 `Screenshot-[00:12]`）
  B. 把失败提示文本删空（替换成 `""`）
     → 同上两条红（`📸 截图生成失败` 不出现）
"""
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.generation import generator as gen          # noqa: E402
from app.utils import video_helper as vh             # noqa: E402

# 两个标记：idx 0 → 00:12、idx 1 → 01:30（`extract_screenshot_timestamps` 按出现序）
_MD_TWO = "开头\n\n*Screenshot-[00:12]\n\n中间\n\n*Screenshot-[01:30]\n\n结尾\n"


def _boom(*a, **k):
    raise RuntimeError("ffmpeg exited with code 1")


def _mk_shot(video_path: str, output_dir: str, ts: int, idx: int) -> str:
    """替身：签名与真实 `generate_screenshot` 一致（4 个位置参数）。

    返回一个（无需真实存在的）输出路径 —— 调用方只取 `Path(...).name` 拼 URL。
    """
    return os.path.join(output_dir, f"shot_{idx}.png")


@contextmanager
def _env():
    """临时视频文件 + doc_dir（`_post_process_screenshots` 会做 `os.path.exists` 检查）。"""
    d = tempfile.mkdtemp(prefix="limo_shot_")
    video = os.path.join(d, "v.mp4")
    with open(video, "wb") as f:
        f.write(b"\x00")
    try:
        yield video, d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_failure_replaces_marker():
    """失败 → 标记**消失**，换成带时间戳的中文失败提示（零残留）。"""
    with _env() as (video, doc_dir):
        with patch.object(vh, "generate_screenshot", _boom):
            out = gen._post_process_screenshots(_MD_TWO, video, doc_dir=doc_dir)

    assert "Screenshot-" not in out, (
        f"失败的标记必须被替换掉，不能留在产物里（前端会把它渲染成一个点不动的"
        f"截图标 badge，看起来与成功一样）：{out!r}")
    assert "📸 截图生成失败（00:12）" in out, out
    assert "📸 截图生成失败（01:30）" in out, out


def test_success_replaces_marker_with_image():
    """成功 → 标记换成图片引用，且**不出现**失败提示（对照：不能把成功也标成失败）。"""
    with _env() as (video, doc_dir):
        with patch.object(vh, "generate_screenshot", _mk_shot):
            out = gen._post_process_screenshots(_MD_TWO, video, doc_dir=doc_dir)

    assert "Screenshot-" not in out, out
    assert out.count("![](assets/") == 2, f"两个标记各出一张图：{out!r}"
    assert "截图生成失败" not in out, "成功的路径不得留下失败提示"


def test_partial_failure_keeps_others():
    """一成功一失败：**各自**替换，互不影响（`replace(marker, …, 1)` 逐处生效）。"""
    seen: list[int] = []

    def _mixed(video_path, output_dir, ts, idx):
        seen.append(ts)
        if len(seen) == 1:
            return os.path.join(output_dir, f"shot_{idx}.png")
        raise RuntimeError("ffmpeg exited with code 1")

    with _env() as (video, doc_dir):
        with patch.object(vh, "generate_screenshot", _mixed):
            out = gen._post_process_screenshots(_MD_TWO, video, doc_dir=doc_dir)

    assert seen == [12, 90], f"两个标记各调一次，按出现序：{seen}"

    assert "Screenshot-" not in out, f"两处标记都必须被替换：{out!r}"
    assert out.count("![](assets/") == 1, f"成功的那个仍要出图：{out!r}"
    assert out.count("📸 截图生成失败（01:30）") == 1, out


def test_fail_note_shape_is_uniform():
    """失败提示的形态**不随 marker 变体漂移**（带括号 / 不带括号 / 前导 `*`）。"""
    md = "- *Screenshot-[2:05]\n- Screenshot-03:07\n"
    with _env() as (video, doc_dir):
        with patch.object(vh, "generate_screenshot", _boom):
            out = gen._post_process_screenshots(md, video, doc_dir=doc_dir)

    assert "📸 截图生成失败（02:05）" in out, out
    assert "📸 截图生成失败（03:07）" in out, out
    assert "Screenshot-" not in out, out


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS: {fn.__name__}")
            passed += 1
        except Exception:
            print(f"FAIL: {fn.__name__}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} tests passed")
    sys.exit(0 if passed == len(tests) else 1)
