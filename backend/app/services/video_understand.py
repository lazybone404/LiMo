"""
视频直传理解 — DashScope SDK 原生通道（阶段 A：Qwen 打通）。

视频直传模型 = 把视频文件直接传给 Qwen 视觉模型（服务端抽帧），替代本地
ffmpeg 截帧链路。方案见 pi-docs/视频直传模型方案.md。

为什么必须走 DashScope SDK（而非 OpenAI 兼容）：
- OpenAI 兼容接口的 video_url 块只接受公网 URL / base64（原始 <7MB 才可用）
- LiMo 视频是本地下载文件（通常 >7MB），必须用 SDK 原生通道
  `MultiModalConversation.call(model, messages=[{"video": "file://..."}])`
- dashscope SDK 已是依赖（OCR/ASR 引擎使用，已入 PyInstaller spec）

本模块只做「视频 → 画面理解描述文本」这一件事，不碰生成链路：
直传理解结果作为上下文注入 ContentPreparer.prepare() 的 clean_text，
生成调用照常走 LLMService（流式保留）。失败/超限 → 返回 None 由调用方降级截帧。
"""

from __future__ import annotations

import os
import re
import subprocess

from app.core import logging as log

# 本地视频直传上限（DashScope SDK file:// 通道 ≤100MB，方案 §2.1）
VIDEO_MAX_BYTES = 100 * 1024 * 1024

# 帧数上限：长视频 fps=2 抽帧数爆炸 → token 成本失控（评审反馈）。
# 按视频时长自适应 fps，使总帧数 ≤ 此值（fps = min(2.0, max_frames / duration)）。
MAX_FRAMES = 128

# 默认抽帧率（DashScope 支持 0.1-10，默认 2.0）
DEFAULT_FPS = 2.0

# 画面理解指令（复用现有 VL 画面理解语义——描述画面内容；不强求时间戳，评审反馈）
VIDEO_UNDERSTAND_PROMPT = (
    "请仔细观察这个视频的画面内容，用中文详细描述：视频的主题、主要场景、"
    "画面中出现的物体/人物/文字/图表、关键事件或步骤，按时间顺序组织。"
)


def _to_file_uri(path: str) -> str:
    """Windows 本地路径 → file:// URI（反斜杠转正斜杠，官方格式 file:///D:/xxx）。"""
    p = path.replace("\\", "/")
    if not p.startswith("/"):
        p = "/" + p
    return "file://" + p


def _video_duration_s(video_path: str) -> float | None:
    """ffprobe 读取视频时长（秒）。失败返回 None（调用方用默认 fps）。"""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", video_path],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if r.returncode == 0:
            return float(r.stdout.strip())
    except Exception as e:
        log.warning("ffprobe 读取视频时长失败", error=str(e)[:80])
    return None


def effective_fps(video_path: str, max_frames: int = MAX_FRAMES) -> float:
    """按视频时长自适应 fps，使总抽帧数 ≤ max_frames（防长视频帧数爆炸）。

    注意：DashScope 的 fps 有 0.1 下限（官方支持 0.1-10）。视频时长 > max_frames/0.1
    （即 >1280s，约 21 分钟）时，即使 fps=0.1 总帧数也会超过 max_frames——这是
    SDK 硬约束下的尽力而为，超限时记 warning 供排查（不阻断直传）。
    """
    dur = _video_duration_s(video_path)
    if dur and dur > 0:
        fps = min(DEFAULT_FPS, max_frames / dur)
        if fps < 0.1:
            log.warning("视频过长，fps 触 0.1 下限", duration_s=round(dur, 1),
                        max_frames=max_frames)
            return 0.1
        return fps
    return DEFAULT_FPS


def _extract_text(content: object) -> str:
    """解析 DashScope MultiModalConversation 响应的 message.content。

    结构：list of {"text": "..."} / {"image": "..."}，取全部 text 拼接。
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for item in content:
        if isinstance(item, dict):
            t = item.get("text")
            if t:
                parts.append(t)
    return "\n".join(parts)


def understand_video_direct(
    model: str,
    video_path: str,
    prompt: str = VIDEO_UNDERSTAND_PROMPT,
    api_key: str = "",
    base_url: str = "",
) -> str | None:
    """视频直传理解（同步，调用方用 asyncio.to_thread + wait_for 包超时）。

    返回画面理解描述文本；失败/超限/无结果 → None（调用方降级截帧）。
    仅支持 DashScope（Qwen 视频模型）；其他 provider 返回 None。

    base_url 参数：dashscope SDK 原生通道无 per-call base_url（官方端点走全局
    `dashscope.base_http_api_url`，改它会影响进程内所有 dashscope 调用）——
    此参数保留签名供调用方评估，当前不向 SDK 传递（走官方端点）。
    """
    # 空串防御：调用方可能误传 ""（此前 content_preparer 传空串覆盖默认 prompt），
    # 一律回退默认画面理解指令，保证模型收到有效指令。
    if not prompt:
        prompt = VIDEO_UNDERSTAND_PROMPT
    try:
        size = os.path.getsize(video_path)
        if size > VIDEO_MAX_BYTES:
            log.warning("视频直传超限，降级截帧",
                        size_mb=size // (1024 * 1024),
                        limit_mb=VIDEO_MAX_BYTES // (1024 * 1024))
            return None
    except OSError as e:
        log.warning("视频直传文件读取失败，降级截帧", error=str(e)[:80])
        return None

    try:
        import dashscope
    except ImportError:
        log.warning("dashscope SDK 未安装，视频直传不可用，降级截帧")
        return None

    # 本地文件引用（file:// 官方格式，Windows 反斜杠转正斜杠）
    video_ref = _to_file_uri(video_path)
    fps = effective_fps(video_path)
    messages = [{
        "role": "user",
        "content": [
            {"video": video_ref, "fps": fps},
            {"text": prompt},
        ],
    }]

    log.info("视频直传理解", model=model,
             size_mb=os.path.getsize(video_path) // (1024 * 1024),
             fps=round(fps, 1))
    try:
        resp = dashscope.MultiModalConversation.call(
            model=model,
            messages=messages,
            api_key=api_key or None,
        )
        # 解析响应（resp.output.choices[0].message.content）
        choice = resp.output.choices[0]
        text = _extract_text(choice.message.content)
    except Exception as e:
        log.warning("视频直传调用失败，降级截帧", error=str(e)[:100])
        return None

    if not text or not text.strip():
        log.warning("视频直传无文本结果，降级截帧")
        return None
    log.info("视频直传理解成功", chars=len(text))
    return text.strip()
