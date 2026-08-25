"""视频直传理解 — 单元测试（mock DashScope SDK 三路径：成功/失败/超限）。

评审要求：mock SDK 走 成功/失败/超限 三路径 + file:// 转义 + fps 自适应。
"""
import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.services.video_understand import (
    VIDEO_MAX_BYTES,
    understand_video_direct,
    effective_fps,
    _to_file_uri,
)


def _make_video(tmp_dir: str) -> str:
    """创建小假视频文件（仅满足 os.path.getsize 检查，不真正解码）。"""
    p = os.path.join(tmp_dir, "fake.mp4")
    with open(p, "wb") as f:
        f.write(b"fake video bytes")
    return p


def _resp(text: str):
    """构造 DashScope MultiModalConversation 成功响应对象。"""
    class _Msg:
        content = [{"text": text}]
    class _Choice:
        message = _Msg()
    class _Output:
        choices = [_Choice()]
    class _Resp:
        output = _Output()
    return _Resp()


def test_oversize_returns_none():
    """超限（>100MB）→ None，不调用 SDK。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = _make_video(tmp)
        with patch("app.services.video_understand.os.path.getsize",
                   return_value=VIDEO_MAX_BYTES + 1):
            result = understand_video_direct("qwen3.7-plus", path, api_key="k")
    assert result is None, "超限应返回 None（降级截帧）"


def test_sdk_success():
    """SDK 成功 → 返回描述文本。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = _make_video(tmp)
        with patch("dashscope.MultiModalConversation.call",
                   return_value=_resp("视频画面：一辆车在公路上行驶")):
            result = understand_video_direct("qwen3.7-plus", path, api_key="k")
    assert result == "视频画面：一辆车在公路上行驶", "应返回 SDK 描述文本"


def test_sdk_failure_returns_none():
    """SDK 抛异常 → None（降级截帧）。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = _make_video(tmp)
        with patch("dashscope.MultiModalConversation.call",
                   side_effect=RuntimeError("api boom")):
            result = understand_video_direct("qwen3.7-plus", path, api_key="k")
    assert result is None, "SDK 异常应返回 None"


def test_empty_result_returns_none():
    """SDK 返回但无 text → None。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = _make_video(tmp)
        with patch("dashscope.MultiModalConversation.call",
                   return_value=_resp("")):
            result = understand_video_direct("qwen3.7-plus", path, api_key="k")
    assert result is None, "无文本结果应返回 None"


def test_file_uri_windows_backslash():
    """Windows 反斜杠路径 → file:// 正斜杠（官方格式 file://D:/xxx）。"""
    assert _to_file_uri(r"D:\videos\a.mp4") == "file:///D:/videos/a.mp4"
    assert _to_file_uri("/videos/a.mp4") == "file:///videos/a.mp4"


def test_effective_fps_long_video():
    """长视频 fps 自适应降低（总帧数 ≤ MAX_FRAMES）。"""
    with patch("app.services.video_understand._video_duration_s",
               return_value=200.0):
        # 200s × 2fps = 400 帧 > 128 → fps = 128/200 = 0.64
        fps = effective_fps("long.mp4")
    assert abs(fps - 0.64) < 1e-6, f"长视频 fps 应降到 0.64，实际 {fps}"
    with patch("app.services.video_understand._video_duration_s",
               return_value=30.0):
        # 30s × 2fps = 60 帧 ≤ 128 → 维持默认 2.0
        fps = effective_fps("short.mp4")
    assert fps == 2.0, f"短视频维持默认 fps 2.0，实际 {fps}"


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
