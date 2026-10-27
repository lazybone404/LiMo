"""Faster-Whisper 引擎本体 — 在独立 venv 中执行（参考实现）。

进程外契约：
  - 命令行: --input <file_path>（v0 兼容）
  - stdin:  JSON {file, cancel_file, config, media_dir, ...}
  - stdout: JSON {"segments":[{"start","end","text"}], "language", "duration"}

    asr 类别走 `_dispatch_result` 的 segments 分支 —— 主进程据此构造
    `TranscriptResult`，进而决定视频转写的时间轴。**输出的 key 名不能改**。

取消：主进程置位 cancel_event → 写 `cancel_file` → 宽限 3s → 强杀。
本脚本逐段轮询该文件，命中就 `sys.exit(0)`（不输出任何东西）。
⚠️ 不能把已解码的部分打出去：主进程收到半截 segments 会当成**成功**并落库。
"""
import json
import os
import sys


def _cancelled(cancel_file: str) -> bool:
    return bool(cancel_file) and os.path.exists(cancel_file)


def _fail(message: str) -> None:
    """显式失败：非零退出 + 一句人话原因（主进程会把 stderr 当错误抛给用户）。"""
    print(message, file=sys.stderr)
    sys.exit(2)


def main():
    # 三个流都要：stdin 漏了会让进来的 UTF-8 字节按 GBK + surrogateescape 解成
    # 代理字符（`json.loads` 照样成功，路径/参数变成乱码，零报错）。
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    payload = json.load(sys.stdin)
    file_path = payload.get("file") or sys.argv[sys.argv.index("--input") + 1]
    cancel_file = payload.get("cancel_file") or ""
    cfg = payload.get("config", {}) or {}

    if _cancelled(cancel_file):
        sys.exit(0)

    # 模型下载源：**只在子进程设**（不改主进程环境，也不写进用户的系统变量）。
    # 用 setdefault 让用户自己导出的 HF_ENDPOINT 优先于配置项。
    endpoint = (cfg.get("hf_endpoint") or "").strip()
    if endpoint:
        os.environ.setdefault("HF_ENDPOINT", endpoint)

    try:
        from faster_whisper import WhisperModel
    except Exception as e:  # noqa: BLE001 —— DLL 缺失是 OSError，不是 ImportError
        _fail(f"无法导入 faster_whisper（{type(e).__name__}: {e}）—— 请重新「一键安装依赖」")

    model_name = (cfg.get("model") or "small").strip()
    device = (cfg.get("device") or "cpu").strip()
    compute_type = (cfg.get("compute_type") or "int8").strip()
    download_root = (cfg.get("download_root") or "").strip() or None

    # ⚠️ 这里失败**绝不静默退回 CPU**：device/compute_type 非法、CUDA 缺 cuBLAS、
    # 模型下载失败、磁盘满 —— 四种原因的处置完全不同，混成「跑得慢一点」会让用户
    # 以为 GPU 在跑。失败显式化，与核心引擎同一条纪律。
    try:
        model = WhisperModel(
            model_name,
            device=device,
            compute_type=compute_type,
            download_root=download_root,
        )
    except Exception as e:  # noqa: BLE001
        hint = ""
        if device == "cuda":
            hint = "（cuda 需要 NVIDIA 驱动 + cuBLAS/cuDNN；不确定就先切回 cpu）"
        elif download_root:
            hint = f"（模型缓存目录：{download_root}）"
        _fail(
            f"加载模型失败 [{model_name} / {device} / {compute_type}]："
            f"{type(e).__name__}: {e}{hint}"
        )

    language = (cfg.get("language") or "").strip() or None
    vad_filter = bool(cfg.get("vad_filter", True))

    try:
        segments, info = model.transcribe(
            str(file_path),
            language=language,
            vad_filter=vad_filter,
            beam_size=5,
        )

        # 生成器 —— **真正的解码发生在这里**，所以取消检查也在这里
        out = []
        for seg in segments:
            if _cancelled(cancel_file):
                sys.exit(0)
            text = (seg.text or "").strip()
            if not text:
                continue
            out.append({
                "start": round(float(seg.start), 2),
                "end": round(float(seg.end), 2),
                "text": text,
            })
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        _fail(f"转写失败：{type(e).__name__}: {e}")

    lang = getattr(info, "language", "") or (language or "")
    duration = round(float(getattr(info, "duration", 0.0) or 0.0), 2)
    if not duration and out:
        duration = out[-1]["end"]

    print(json.dumps(
        {"segments": out, "language": lang, "duration": duration},
        ensure_ascii=False,
    ))


if __name__ == "__main__":
    main()
