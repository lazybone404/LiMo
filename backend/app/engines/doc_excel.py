"""
Excel 引擎 — .xlsx（openpyxl）/ .xls（xlrd）转录为 Markdown 表格

- .xlsx：openpyxl（data_only 取值，公式不展开）
- .xls：xlrd（旧二进制格式，openpyxl 不支持）
两种格式产物一致：逐 sheet → 单元格值 → MD table + 图表标签 + 数据摘要。
"""

import logging

from app.core.protocols import Engine
from app.engines.doc_types import DocResult

logger = logging.getLogger(__name__)


class ExcelEngine(Engine):
    """Excel .xlsx（openpyxl）/ .xls（xlrd）转录为 Markdown 表格。

    逐 sheet → 单元格值/公式 → MD table + 图表标签 + 数据摘要。
    """

    capabilities = ["text", "table", "office"]

    def execute(self, file_path: str, **kwargs) -> DocResult:
        if file_path.lower().endswith(".xls"):
            return self._parse_xls(file_path)
        return self._parse_xlsx(file_path)

    # ── .xlsx — openpyxl ──

    def _parse_xlsx(self, file_path: str) -> DocResult:
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
            md, _ = self._render_sheet(sn, rows_data, ws._charts)
            parts.append(md)

        wb.close()
        full_text = "\n\n---\n\n".join(parts)
        logger.info(
            "ExcelEngine(xlsx): %d sheets, %d rows, output %d chars",
            len(wb.sheetnames), total_rows, len(full_text),
        )
        return DocResult(
            text=full_text if full_text else f"(空工作簿: {len(wb.sheetnames)} sheets)",
            pages=len(wb.sheetnames),
            engine="openpyxl",
        )

    # ── .xls — xlrd（旧二进制格式，openpyxl 不支持）──

    def _parse_xls(self, file_path: str) -> DocResult:
        import xlrd

        book = xlrd.open_workbook(file_path)
        parts: list[str] = []
        total_rows = 0

        for sn in book.sheet_names():
            sh = book.sheet_by_name(sn)
            nrows = min(sh.nrows, 1000)
            ncols = min(sh.ncols, 50)
            if nrows == 0:
                continue

            rows_data: list[list[str]] = []
            for r in range(nrows):
                row: list[str] = []
                for c in range(ncols):
                    cell = sh.cell(r, c)
                    val = cell.value
                    if isinstance(val, float) and val.is_integer():
                        val = int(val)
                    elif cell.ctype == xlrd.XL_CELL_DATE and val:
                        try:
                            val = xlrd.xldate_as_datetime(
                                val, book.datemode).strftime("%Y-%m-%d")
                        except Exception:
                            pass  # 日期解析失败保留原始值
                    row.append(str(val) if val is not None and val != "" else "")
                rows_data.append(row)

            total_rows += len(rows_data) - 1  # 减表头
            md, _ = self._render_sheet(sn, rows_data, None)
            parts.append(md)

        full_text = "\n\n---\n\n".join(parts)
        logger.info(
            "ExcelEngine(xls): %d sheets, %d rows, output %d chars",
            len(book.sheet_names()), total_rows, len(full_text),
        )
        return DocResult(
            text=full_text if full_text else f"(空工作簿: {len(book.sheet_names())} sheets)",
            pages=len(book.sheet_names()),
            engine="openpyxl",
        )

    # ── 公共：sheet → Markdown 表格 ──

    @staticmethod
    def _render_sheet(sn: str, rows_data: list[list[str]], charts) -> tuple[str, int]:
        """一行数据 → MD 表格 + 图表标签。返回 (md, 数据行数)。"""
        header = rows_data[0] if rows_data else []
        lines = ["| " + " | ".join(h if h else f"Col {i+1}" for i, h in enumerate(header)) + " |",
                 "| " + " | ".join(["---"] * len(header)) + " |"]
        for row in rows_data[1:]:
            pad = row + [""] * (len(header) - len(row))
            lines.append("| " + " | ".join(pad[:len(header)]) + " |")

        chart_labels: list[str] = []
        if charts:
            for ch in charts:
                try:
                    title = ch.title or ""
                    chart_labels.append(f"📊 {title}" if title else "📈 图表")
                except Exception:
                    chart_labels.append("📈 图表")

        md = f"## Sheet: {sn}\n\n" + "\n".join(lines)
        if chart_labels:
            md += "\n\n" + "\n".join(f"> {l}" for l in chart_labels)
        return md, len(rows_data) - 1
