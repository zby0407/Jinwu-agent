"""XLSX tables: one worksheet becomes one extracted table.

Supplementary material is very often an Excel workbook.  The reader uses
``openpyxl`` in read-only mode, converts every cell to text exactly once, and
keeps the worksheet name as the caption.  It never evaluates a formula: only
the cached value is read, and a workbook without cached values is reported as
unreadable instead of being silently flattened to empty cells.
"""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from .contracts import ExtractedTable
from .domain import slug

__all__ = ["read_xlsx_file"]


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep="T")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _header(row: tuple[Any, ...], minimum: int) -> bool:
    present = [_text(cell).strip() for cell in row if _text(cell).strip()]
    return len(present) >= minimum


def read_xlsx_file(
    path: str | Path, *, source_id: str, max_rows: int | None = None
) -> tuple[ExtractedTable, ...]:
    """Read every non-empty worksheet, preserving the header as written."""
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - declared dependency
        raise ValueError(
            "xlsx support requires openpyxl; install it or export the sheet to CSV"
        ) from exc
    workbook = load_workbook(filename=str(path), read_only=True, data_only=True)
    tables: list[ExtractedTable] = []
    try:
        for sheet in workbook.worksheets:
            rows = [
                tuple(row)
                for row in sheet.iter_rows(values_only=True)
                if any(_text(cell).strip() for cell in row)
            ]
            if len(rows) < 2 or not _header(rows[0], 2):
                continue
            header = [_text(cell).strip() for cell in rows[0]]
            columns = tuple(_unique_columns(header))
            body = rows[1 : (max_rows + 1) if max_rows else None]
            records: list[dict[str, str]] = []
            for row_number, row in enumerate(body):
                padded = list(row) + [None] * (len(columns) - len(row))
                record = {
                    column: _text(cell)
                    for column, cell in zip(columns, padded, strict=False)
                }
                record["_row_index"] = row_number + 2
                records.append(record)
            tables.append(
                ExtractedTable(
                    table_id=f"xlsx:{slug(sheet.title) or 'sheet'}",
                    source_id=source_id,
                    columns=columns,
                    rows=tuple(records),
                    header_row=1,
                    caption=str(sheet.title),
                    format="xlsx",
                    parse_warnings=("xlsx_cached_values_only",),
                )
            )
    finally:
        workbook.close()
    return tuple(tables)


def _unique_columns(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out: list[str] = []
    for index, name in enumerate(names, start=1):
        base = name or f"column_{index}"
        seen[base] = seen.get(base, 0) + 1
        out.append(base if seen[base] == 1 else f"{base}#{seen[base]}")
    return out
