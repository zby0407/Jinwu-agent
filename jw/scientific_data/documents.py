"""Recover tables from PDF text, with the limits of that recovery made loud.

A PDF has no table structure to read: the text layer only tells us where glyphs
landed. Reconstructing columns from whitespace is therefore a *guess*, and these
tables are marked as such — low confidence, a ``pdf_text_reconstruction``
warning, and a provenance locator naming the page and the strategy used. The
alternative (producing a confident-looking table from a guess) is worse than
saying nothing.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .contracts import (
    ExtractedTable,
    ProvenanceRef,
    SCHEMA_VERSION,
    ValueKind,
)
from .domain import confidence_from_score, slug

_TWO_SPACES = re.compile(r"\s{2,}")
_CAPTION = re.compile(r"^\s*(?:table|tab\.|表)\s*[\dIVX]+", re.IGNORECASE)
_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")

PDF_TEXT_WARNING = "pdf_text_reconstruction"
MIN_COLUMNS = 3
MIN_ROWS = 3
PDF_COLUMN_CONFIDENCE = 0.45


@dataclass(slots=True)
class PdfText:
    """The text layer of a PDF, page by page, plus how it was obtained."""

    path: str
    pages: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        return "\n".join(self.pages)

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "page_count": len(self.pages),
            "characters": len(self.text),
            "warnings": list(self.warnings),
        }


def read_pdf_text(path: str | Path) -> PdfText:
    """Extract the text layer. A missing ``pypdf`` is reported, not raised."""

    target = Path(path)
    try:
        from pypdf import PdfReader
    except ImportError:
        return PdfText(str(target), warnings=("pypdf_not_installed",))
    try:
        reader = PdfReader(str(target))
        pages = tuple((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:  # an encrypted or broken PDF must not crash a run
        return PdfText(str(target), warnings=(f"unreadable_pdf:{exc}",))
    warnings: list[str] = []
    if not any(page.strip() for page in pages):
        warnings.append("no_text_layer")
    return PdfText(str(target), pages=pages, warnings=tuple(warnings))


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in _TWO_SPACES.split(line.strip()) if cell.strip()]


def _is_number(cell: str) -> bool:
    return bool(_NUMBER.match(cell.replace(",", "")))


def _caption_before(lines: Sequence[str], index: int) -> str:
    for offset in (1, 2, 3):
        position = index - offset
        if position < 0:
            break
        candidate = lines[position].strip()
        if not candidate:
            continue
        if _CAPTION.match(candidate):
            return candidate
    return ""


def tables_from_text(
    text: str,
    *,
    source_id: str,
    table_id_prefix: str = "pdf",
    path: str = "",
    page: int = 0,
    min_columns: int = MIN_COLUMNS,
    min_rows: int = MIN_ROWS,
) -> tuple[ExtractedTable, ...]:
    """Find whitespace-aligned table blocks in already-extracted text."""

    lines = text.splitlines()
    blocks: list[tuple[int, int, list[list[str]]]] = []
    current: list[list[str]] = []
    width = 0
    start = 0

    def flush() -> None:
        nonlocal current, width
        if len(current) >= min_rows and width >= min_columns:
            numeric_rows = sum(
                1 for row in current[1:] if any(_is_number(cell) for cell in row)
            )
            # Without numeric cells this is prose, not a table.
            if numeric_rows * 2 >= len(current) - 1:
                blocks.append((start, start + len(current) - 1, list(current)))
        current = []
        width = 0

    for index, line in enumerate(lines):
        cells = _cells(line)
        if len(cells) < min_columns:
            flush()
            continue
        if current and len(cells) != width:
            flush()
        if not current:
            start = index
            width = len(cells)
        current.append(cells)
    flush()

    tables: list[ExtractedTable] = []
    for position, (first, last, rows) in enumerate(blocks):
        header = rows[0]
        body = rows[1:]
        warnings = [PDF_TEXT_WARNING]
        offset = 2
        if not any(not _is_number(cell) for cell in header):
            warnings.append("no_header_row")
            header = [f"column_{number + 1}" for number in range(len(rows[0]))]
            body = rows
            offset = 1
        columns = _unique_columns(header)
        records: list[dict[str, str]] = []
        for row_number, row in enumerate(body):
            padded = list(row) + [""] * (len(columns) - len(row))
            record: dict[str, str] = {}
            for name, cell in zip(columns, padded, strict=False):
                record[name] = cell
            record["_row_index"] = first + offset + row_number
            records.append(record)
        caption = _caption_before(lines, first)
        tables.append(
            ExtractedTable(
                table_id=f"{table_id_prefix}:p{page}:t{position}",
                source_id=source_id,
                columns=columns,
                rows=tuple(records),
                header_row=first + 1,
                caption=caption,
                format="pdf_text",
                parse_warnings=tuple(warnings),
                confidence=confidence_from_score(PDF_COLUMN_CONFIDENCE),
                provenance=ProvenanceRef(
                    source_id=source_id,
                    locator={
                        "path": path,
                        "page": page,
                        "line_start": first + 1,
                        "line_end": last + 1,
                        "table_index": position,
                        "format": "pdf_text",
                        "columns_from": "whitespace_runs",
                        "page_caption": caption,
                    },
                    extractor="jw.scientific_data.documents.tables_from_text",
                    extractor_version=SCHEMA_VERSION,
                    value_kind=ValueKind.REPORTED,
                    confidence=confidence_from_score(PDF_COLUMN_CONFIDENCE),
                    confidence_score=PDF_COLUMN_CONFIDENCE,
                    raw_excerpt="\n".join(line for line in lines[first : last + 1])[
                        :400
                    ],
                ),
            )
        )
    return tuple(tables)


def read_pdf_tables(
    path: str | Path,
    *,
    source_id: str,
    table_id_prefix: str = "pdf",
    max_tables: int = 20,
) -> tuple[ExtractedTable, ...]:
    """Extract tables from every page of a PDF (whitespace reconstruction)."""

    document = read_pdf_text(path)
    tables: list[ExtractedTable] = []
    for page_number, page_text in enumerate(document.pages, start=1):
        if not page_text.strip():
            continue
        for table in tables_from_text(
            page_text,
            source_id=source_id,
            table_id_prefix=table_id_prefix,
            path=str(path),
            page=page_number,
        ):
            tables.append(table)
            if len(tables) >= max_tables:
                return tuple(tables)
    return tuple(tables)


def pdf_summary(path: str | Path, *, source_id: str = "") -> dict[str, object]:
    """What a PDF contributed, for the run report."""

    document = read_pdf_text(path)
    tables = read_pdf_tables(
        path, source_id=source_id or f"pdf:{slug(Path(path).stem)}"
    )
    return {
        **document.to_dict(),
        "tables": len(tables),
        "source_id": source_id,
        "warning": (
            "Tables here are reconstructed from whitespace in the text layer; "
            "column boundaries are a reading, not a fact."
        ),
    }


def _unique_columns(header: Sequence[str]) -> tuple[str, ...]:
    seen: dict[str, int] = {}
    columns: list[str] = []
    for index, name in enumerate(header):
        base = name.strip() or f"column_{index + 1}"
        count = seen.get(base, 0) + 1
        seen[base] = count
        columns.append(base if count == 1 else f"{base}#{count}")
    return tuple(columns)


__all__ = [
    "PDF_TEXT_WARNING",
    "PdfText",
    "pdf_summary",
    "read_pdf_tables",
    "read_pdf_text",
    "tables_from_text",
]
