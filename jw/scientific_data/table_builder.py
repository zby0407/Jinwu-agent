"""Turn a grid of raw strings into an :class:`ExtractedTable`.

Every PDF reader — the old whitespace splitter, the word-geometry reader, and
the cloud OCR adapter — produces the same thing: rows of text with no types and
no provenance.  This module is the single place where such a grid becomes a
contract object, so all three readers carry identical header handling, row
indices, warnings and provenance shape.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .contracts import (
    SCHEMA_VERSION,
    ExtractedTable,
    ProvenanceRef,
    ValueKind,
)
from .domain import confidence_from_score

_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_CAPTION = re.compile(r"^\s*(?:table|tab\.|表)\s*[\dIVX]+", re.IGNORECASE)

__all__ = [
    "build_table",
    "caption_before",
    "contains_number",
    "is_number",
    "unique_columns",
]


def is_number(cell: str) -> bool:
    """Whether the whole cell parses as one number (thousands commas allowed)."""
    return bool(_NUMBER.match(str(cell).replace(",", "")))


def contains_number(cell: str) -> bool:
    """A cell is numeric if it, or one of its space-separated tokens, is."""
    if is_number(cell):
        return True
    return any(is_number(token) for token in str(cell).split())


def caption_before(lines: Sequence[str], index: int) -> str:
    """Nearest ``Table N`` caption within three lines above ``index``."""
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


def unique_columns(header: Sequence[str]) -> tuple[str, ...]:
    """Column names with duplicate labels disambiguated as ``name#2``."""
    seen: dict[str, int] = {}
    columns: list[str] = []
    for index, name in enumerate(header):
        base = str(name).strip() or f"column_{index + 1}"
        count = seen.get(base, 0) + 1
        seen[base] = count
        columns.append(base if count == 1 else f"{base}#{count}")
    return tuple(columns)


def build_table(
    rows: Sequence[Sequence[str]],
    *,
    source_id: str,
    table_id: str,
    table_index: int,
    page: int,
    path: str,
    lines: Sequence[str],
    first: int,
    last: int,
    columns_from: str,
    confidence_score: float,
    warning: str,
    extractor: str,
    format: str = "pdf_text",
    caption: str = "",
    extra_locator: Mapping[str, Any] | None = None,
) -> ExtractedTable:
    """Build one low-confidence table from raw cells.

    ``first``/``last`` are 0-based indices into ``lines`` so a reader can keep
    the page line range in provenance.  A header row is assumed when at least
    one first-row cell is non-numeric; otherwise synthetic ``column_N`` names
    are used and the first row is kept as data (with a ``no_header_row``
    warning), because silently eating a data row is worse than a dull name.
    """

    body_rows = [list(row) for row in rows]
    header = list(body_rows[0])
    body = body_rows[1:]
    warnings = [warning]
    offset = 2
    if not any(not is_number(cell) for cell in header):
        warnings.append("no_header_row")
        header = [f"column_{number + 1}" for number in range(len(body_rows[0]))]
        body = body_rows
        offset = 1
    columns = unique_columns(header)

    records: list[dict[str, str]] = []
    for row_number, row in enumerate(body):
        padded = list(row) + [""] * (len(columns) - len(row))
        record: dict[str, str] = {}
        for name, cell in zip(columns, padded, strict=False):
            record[name] = str(cell)
        record["_row_index"] = first + offset + row_number
        records.append(record)

    located_caption = caption or caption_before(lines, first)
    confidence = confidence_from_score(confidence_score)
    locator: dict[str, Any] = {
        "path": path,
        "page": page,
        "line_start": first + 1,
        "line_end": last + 1,
        "table_index": table_index,
        "format": format,
        "columns_from": columns_from,
        "page_caption": located_caption,
    }
    if extra_locator:
        locator.update({str(key): value for key, value in extra_locator.items()})
    return ExtractedTable(
        table_id=table_id,
        source_id=source_id,
        columns=columns,
        rows=tuple(records),
        header_row=first + 1,
        caption=located_caption,
        format=format,
        parse_warnings=tuple(warnings),
        confidence=confidence,
        provenance=ProvenanceRef(
            source_id=source_id,
            locator=locator,
            extractor=extractor,
            extractor_version=SCHEMA_VERSION,
            value_kind=ValueKind.REPORTED,
            confidence=confidence,
            confidence_score=confidence_score,
            raw_excerpt="\n".join(lines[first : last + 1])[:400],
        ),
    )
