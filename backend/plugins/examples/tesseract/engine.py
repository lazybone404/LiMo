"""Tesseract 引擎本体 — 在独立 venv 中执行（参考实现）。

进程外契约：
  - stdin:  JSON {file, cancel_file, config}
  - stdout: JSON {"text": "..."}

调用插件目录自带的 tesseract.exe（便携版），TESSDATA_PREFIX 用 subprocess env 参数传递
（不改全局环境）。
"""
import json
import os
import subprocess
import sys


def main():
    # 三个流都要：stdin 漏了会让进来的 UTF-8 字节按 GBK + surrogateescape 解成
    # 代理字符（`json.loads` 照样成功，路径/参数变成乱码，零报错）。
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    config = json.load(sys.stdin)
    file_path = config.get("file")
    if not file_path:
        print(json.dumps({"text": ""}))
        return

    # 取消检查：tesseract 是单次 subprocess，只能在运行前检查一次 cancel_file
    cancel_file = config.get("cancel_file") or ""
    if cancel_file and os.path.exists(cancel_file):
        sys.exit(0)

    cfg = config.get("config", {}) or {}
    lang = cfg.get("lang", "chi_sim+eng")
    psm = cfg.get("psm", "3")

    # 插件目录自带的 tesseract 便携版（用户自备，见 README）
    base = os.path.dirname(os.path.abspath(__file__))
    tesseract_exe = os.path.join(base, "tesseract", "tesseract.exe")
    if not os.path.isfile(tesseract_exe):
        tesseract_exe = "tesseract"  # 回退系统 PATH

    # TESSDATA_PREFIX 用 env 参数传递（不改全局环境），指向插件自带 tessdata
    tessdata_dir = os.path.join(base, "tesseract", "tessdata")
    env = dict(os.environ)
    if os.path.isdir(tessdata_dir):
        env["TESSDATA_PREFIX"] = tessdata_dir

    try:
        result = subprocess.run(
            [tesseract_exe, file_path, "stdout", "-l", lang, "--psm", psm],
            capture_output=True, text=True, encoding="utf-8",
            env=env, timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired:
        print(json.dumps({"text": "", "error": "tesseract 超时"}))
        return

    if result.returncode != 0:
        # 常见：chi_sim.traineddata 缺失 → "Failed loading language 'chi_sim'"
        print(json.dumps({"text": "", "error": result.stderr.strip()[:200]}))
        return

    print(json.dumps({"text": result.stdout.strip()}))


if __name__ == "__main__":
    main()
