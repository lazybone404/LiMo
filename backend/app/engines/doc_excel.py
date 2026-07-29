"""
openpyxl 引擎 — Excel .xlsx/.xls 转录为 Markdown 表格

逐 sheet → 单元格值/公式 → MD table + 图表标签 + 数据摘要。
对标 PyMuPDF/DocxEngine，但产物天然是表格而非段落。
"""

import logging

from app.core.protocols import Engine
from app.engines.doc_types import DocResult

logger = logging.getLogger(__name__)


class ExcelEngine(Engine):
    """openpyxl — Excel .xlsx/.xls 转录为 Markdown 表格

    逐 sheet → 单元格值/公式 → MD table + 图表标签 + 数据摘要。
    对标 PyMuPDF/DocxEngine，但产物天然是表格而非段落。
    """

    capabilities = ["text", "table", "office"]

    def execute(self, file_path: str, **kwargs) -> DocResult:
        from openpyxl import load_workbook

        wb = load_workbook(file_path, data_only=True)
        parts: list[str] = []
        total_rows = 0

        for sn in wb.sheetnames:
            ws = wb[sn]
            rows_data: list[list[str]] = []
            for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row or 0, 1000),
                                     max_col=min(ws.max_column or 0, 50)):
                cells = []
                for cell in row:
                    val = cell.value
                    cells.append(str(val) if val is not None else "")
                rows_data.append(cells)

            if not rows_data:
                continue

            total_rows += len(rows_data) - 1  # 减表头

            # 表头：以第一行（如无则 A,B,C...）
            header = rows_data[0] if rows_data else []
            lines = ["| " + " | ".join(h if h else f"Col {i+1}" for i, h in enumerate(header)) + " |",
                     "| " + " | ".join(["---"] * len(header)) + " |"]
            for row in rows_data[1:]:
                pad = row + [""] * (len(header) - len(row))
                lines.append("| " + " | ".join(pad[:len(header)]) + " |")

            # 图表标签
            chart_labels = []
            if ws._charts:
                for ch in ws._charts:
                    try:
                        title = ch.title or ""
                        chart_labels.append(f"📊 {title}" if title else "📈 图表")
                    except Exception:
                        chart_labels.append("📈 图表")

            parts.append(
                f"## Sheet: {sn}\n\n"
                + ("\n".join(lines))
                + ("\n\n" + "\n".join(f"> {l}" for l in chart_labels) if chart_labels else "")
            )

        wb.close()
        full_text = "\n\n---\n\n".join(parts)
        logger.info(
            "ExcelEngine: %d sheets, %d rows, output %d chars",
            len(wb.sheetnames), total_rows, len(full_text),
        )
        return DocResult(
            text=full_text if full_text else f"(空工作簿: {len(wb.sheetnames)} sheets)",
            pages=len(wb.sheetnames),
            engine="openpyxl",
        )
