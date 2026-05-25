from __future__ import annotations

from pathlib import Path

from chunkshop.sources.parsers.base import ParserError


class XLSXParser:
    supported_extensions = ["xlsx"]

    def parse(self, path: Path) -> str:
        try:
            import openpyxl
        except (ImportError, TypeError) as exc:
            raise RuntimeError(
                "XLSX parsing requires openpyxl. Install with `pip install chunkshop[xlsx]`."
            ) from exc
        try:
            wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
            rows: list[str] = []
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    rows.append("\t".join("" if c is None else str(c) for c in row))
            return "\n".join(rows)
        except Exception as exc:
            raise ParserError(f"failed to parse XLSX {path}: {exc}") from exc
