"""PyMuPDF4LLM 引擎本体 — 在独立 venv 中执行（参考实现）。

进程外契约：
  - 命令行: --input <file_path>（v0 兼容）
  - stdin:  JSON {file, cancel_file, config, media_dir, ...}
  - stdout: JSON {"text","pages","is_scanned","scanned_pages"}
"""
import json
import os
import sys


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    config = json.load(sys.stdin)
    file_path = config.get("file") or sys.argv[sys.argv.index("--input") + 1]

    # 取消检查：cancel_file 存在 → 立即退出（主进程会宽限 3s 后强杀兜底）
    cancel_file = config.get("cancel_file") or ""
    if cancel_file and os.path.exists(cancel_file):
        sys.exit(0)

    # 插件自身配置（config_schema 表单保存的值，未配置时用默认值）
    cfg = config.get("config", {}) or {}

    import pymupdf4llm
    md = pymupdf4llm.to_markdown(
        str(file_path),
        write_images=bool(cfg.get("write_images", True)),
        image_path=config.get("media_dir") or None,
        image_format=cfg.get("image_format", "png"),
        dpi=int(cfg.get("dpi", 150)),
    )

    # 扫描件检测（可选元数据；pymupdf4llm 依赖 pymupdf → fitz）
    import fitz
    doc = fitz.open(str(file_path))
    pages = doc.page_count
    scanned = sum(1 for p in doc if not p.get_text().strip())
    doc.close()

    print(json.dumps({
        "text": md,
        "pages": pages,
        "is_scanned": bool(pages and scanned > pages * 0.5),
        "scanned_pages": scanned,
    }))


if __name__ == "__main__":
    main()
