"""HTML table extraction: one ``<table>`` element becomes one raw table.

The parser keeps what a reader would see (caption, section heading, header
cells, span geometry) and reports the geometry it had to expand, because a
table whose ``rowspan``/``colspan`` layout was misread is one of the classic
silent ways a merged dataset goes wrong.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .contracts import (
    SCHEMA_VERSION,
    ConfidenceBand,
    ExtractedTable,
    ProvenanceRef,
    ValueKind,
)
from .tabular import decode_bytes
from .domain import confidence_from_score

__all__ = [
    "HtmlTableDraft",
    "collect_html_tables",
    "read_html_file",
    "read_html_tables",
]

_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_SKIP_TEXT = frozenset({"script", "style"})
_SPACE = re.compile(r"\s+")


@dataclass(slots=True)
class _Cell:
    text: list[str] = field(default_factory=list)
    colspan: int = 1
    rowspan: int = 1
    is_header: bool = False

    @property
    def value(self) -> str:
        return _SPACE.sub(" ", " ".join(self.text)).strip()


@dataclass(frozen=True, slots=True)
class HtmlTableDraft:
    """A ``<table>`` element as parsed, before it becomes an ExtractedTable."""

    index: int
    caption: str = ""
    heading: str = ""
    columns: tuple[str, ...] = ()
    rows: tuple[dict[str, Any], ...] = ()
    header_rows: int = 0
    line_start: int = 0
    line_end: int = 0
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "caption": self.caption,
            "heading": self.heading,
            "columns": list(self.columns),
            "rows": [dict(row) for row in self.rows],
            "header_rows": self.header_rows,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "warnings": list(self.warnings),
        }


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.drafts: list[HtmlTableDraft] = []
        self._heading = ""
        self._heading_buffer: list[str] | None = None
        self._skip_depth = 0
        self._depth = 0
        self._tables: list[dict[str, Any]] = []

    # -- document level ----------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        text = tag.lower()
        if text in _SKIP_TEXT:
            self._skip_depth += 1
            return
        if text in _HEADINGS:
            self._heading_buffer = []
            return
        if text == "table":
            self._depth += 1
            line, _offset = self.getpos()
            self._tables.append(
                {
                    "depth": self._depth,
                    "rows": [],
                    "current_row": None,
                    "caption": [],
                    "in_caption": False,
                    "line_start": line,
                    "line_end": line,
                    "warnings": [],
                }
            )
            return
        table = self._current_table()
        if table is None or text not in {"tr", "td", "th", "caption"}:
            return
        line, _offset = self.getpos()
        table["line_end"] = line
        if text == "tr":
            table["current_row"] = []
        elif text == "caption":
            table["in_caption"] = True
        else:
            row = table["current_row"]
            if row is None:
                row = []
                table["current_row"] = row
            row.append(
                _Cell(
                    colspan=_span(attrs, "colspan"),
                    rowspan=_span(attrs, "rowspan"),
                    is_header=text == "th",
                )
            )

    def handle_endtag(self, tag: str) -> None:
        text = tag.lower()
        if text in _SKIP_TEXT:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if text in _HEADINGS and self._heading_buffer is not None:
            heading = _SPACE.sub(" ", " ".join(self._heading_buffer)).strip()
            if heading:
                self._heading = heading
            self._heading_buffer = None
            return
        table = self._current_table()
        if table is None:
            return
        if text == "caption":
            table["in_caption"] = False
        elif text == "tr":
            row = table["current_row"]
            if row:
                table["rows"].append(row)
            table["current_row"] = None
        elif text == "table":
            self._depth = max(0, self._depth - 1)
            if self._tables:
                self._tables.pop()
            self._finish_table(table)

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._heading_buffer is not None:
            self._heading_buffer.append(data)
            return
        table = self._current_table()
        if table is None:
            return
        if table["in_caption"]:
            table["caption"].append(data)
            return
        row = table["current_row"]
        if row and row[-1] is not None:
            row[-1].text.append(data)

    def close(self) -> None:  # pragma: no cover - defensive
        super().close()
        while self._tables:
            table = self._tables.pop()
            self._finish_table(table)

    # -- helpers -----------------------------------------------------------
    def _current_table(self) -> dict[str, Any] | None:
        return self._tables[-1] if self._tables else None

    def _finish_table(self, table: dict[str, Any]) -> None:
        grid = _build_grid(table["rows"])
        if not grid:
            table["warnings"].append("empty_table")
            self.drafts.append(
                HtmlTableDraft(
                    index=len(self.drafts),
                    caption=_SPACE.sub(" ", " ".join(table["caption"])).strip(),
                    heading=self._heading,
                    line_start=table["line_start"],
                    line_end=table["line_end"],
                    warnings=tuple(table["warnings"]),
                )
            )
            return
        width = max(len(row) for row in grid)
        header_rows = 0
        for row in grid:
            filled = [is_header for value, is_header in row if value]
            if filled and all(filled):
                header_rows += 1
            else:
                break
        columns: list[str] = []
        if header_rows:
            for position in range(width):
                labels = [
                    row[position][0]
                    for row in grid[:header_rows]
                    if position < len(row) and row[position][0]
                ]
                label = labels[-1] if labels else ""
                columns.append(label or f"column_{position + 1}")
        else:
            columns = [f"column_{number}" for number in range(1, width + 1)]
            table["warnings"].append("no_header_row")
        unique, duplicates = _dedupe(columns)
        table["warnings"].extend(f"duplicate_column:{name}" for name in duplicates)
        data_rows = []
        for offset, row in enumerate(grid[header_rows:]):
            record: dict[str, Any] = {
                name: (row[position][0] if position < len(row) else "")
                for position, name in enumerate(unique)
            }
            record["_row_index"] = offset
            data_rows.append(record)
        short = sum(1 for row in grid if len([cell for cell in row if cell[0]]) < width)
        if short:
            table["warnings"].append(f"ragged_rows:{short}")
        self.drafts.append(
            HtmlTableDraft(
                index=len(self.drafts),
                caption=_SPACE.sub(" ", " ".join(table["caption"])).strip(),
                heading=self._heading,
                columns=tuple(unique),
                rows=tuple(data_rows),
                header_rows=header_rows,
                line_start=table["line_start"],
                line_end=table["line_end"],
                warnings=tuple(dict.fromkeys(table["warnings"])),
            )
        )


def _dedupe(names: Sequence[str]) -> tuple[list[str], list[str]]:
    seen: dict[str, int] = {}
    unique: list[str] = []
    duplicates: list[str] = []
    for name in names:
        count = seen.get(name, 0) + 1
        seen[name] = count
        if count == 1:
            unique.append(name)
        else:
            unique.append(f"{name}#{count}")
            duplicates.append(name)
    return unique, duplicates


def _span(attrs: Sequence[tuple[str, str | None]], name: str) -> int:
    for key, value in attrs:
        if key.lower() == name and value:
            try:
                return max(1, int(str(value).strip()))
            except ValueError:
                return 1
    return 1


def _build_grid(rows: Sequence[Sequence[_Cell]]) -> list[list[tuple[str, bool]]]:
    """Lay cells out on a real grid so ``rowspan``/``colspan`` land correctly.

    Without this step a spanned cell silently shifts every later column of the
    table by one, which is the classic way an HTML table extraction corrupts
    otherwise valid numbers.
    """
    grid: list[list[tuple[str, bool]]] = []
    pending: dict[int, tuple[int, str, bool]] = {}
    for row in rows:
        cells: dict[int, tuple[str, bool]] = {}
        for column, (remaining, value, is_header) in list(pending.items()):
            cells[column] = (value, is_header)
            if remaining <= 1:
                del pending[column]
            else:
                pending[column] = (remaining - 1, value, is_header)
        column = 0
        for cell in row:
            while column in cells:
                column += 1
            value = cell.value
            for offset in range(max(1, cell.colspan)):
                cells[column + offset] = (value, cell.is_header)
                if cell.rowspan > 1:
                    pending[column + offset] = (cell.rowspan - 1, value, cell.is_header)
            column += max(1, cell.colspan)
        width = max(cells) + 1 if cells else 0
        grid.append([cells.get(index, ("", False)) for index in range(width)])
    return grid


def collect_html_tables(
    text: str, *, max_tables: int = 50
) -> tuple[HtmlTableDraft, ...]:
    """Parse every top-level-ish ``<table>`` in an HTML document."""
    parser = _TableParser()
    parser.feed(text)
    parser.close()
    return tuple(parser.drafts[:max_tables])


def read_html_tables(
    text: str,
    *,
    source_id: str,
    table_id_prefix: str = "html",
    path: str = "",
) -> tuple[ExtractedTable, ...]:
    """Turn HTML text into :class:`ExtractedTable` objects with provenance."""
    tables: list[ExtractedTable] = []
    for draft in collect_html_tables(text):
        score = 0.9
        if "no_header_row" in draft.warnings:
            score -= 0.2
        if not draft.rows:
            score -= 0.3
        score -= 0.05 * len([w for w in draft.warnings if w != "no_header_row"])
        provenance = ProvenanceRef(
            source_id=source_id,
            locator={
                "path": path,
                "format": "html",
                "table_index": draft.index,
                "line_start": draft.line_start,
                "line_end": draft.line_end,
                "caption": draft.caption,
                "heading": draft.heading,
                "header_rows": draft.header_rows,
                "table_count": len(draft.columns),
            },
            extractor="jw.scientific_data.html_tables.read_html_tables",
            extractor_version=SCHEMA_VERSION,
            value_kind=ValueKind.REPORTED,
            confidence=confidence_from_score(score)
            if draft.rows
            else ConfidenceBand.UNVERIFIED,
            confidence_score=score,
            raw_excerpt=draft.caption[:200],
        )
        tables.append(
            ExtractedTable(
                table_id=f"{table_id_prefix}_{draft.index:03d}",
                source_id=source_id,
                columns=draft.columns,
                rows=draft.rows,
                header_row=0 if draft.header_rows else -1,
                caption=draft.caption or draft.heading,
                provenance=provenance,
                format="html",
                parse_warnings=draft.warnings,
                confidence=provenance.confidence,
            )
        )
    return tuple(tables)


def read_html_file(
    path: str | Path,
    *,
    source_id: str,
    table_id_prefix: str = "html",
    max_tables: int = 50,
) -> tuple[ExtractedTable, ...]:
    """Read a saved HTML page (any encoding) and extract its tables."""
    target = Path(path)
    text, _encoding, _warnings = decode_bytes(target.read_bytes())
    tables = read_html_tables(
        text,
        source_id=source_id,
        table_id_prefix=table_id_prefix,
        path=str(target),
    )
    return tables[:max_tables]
