"""Markdown pipe-table extraction, including captions and column alignment.

Supplemental material is often published as Markdown, and its tables carry two
facts a naive split throws away: the separator row (which tells you the numeric
alignment of each column) and the caption line just above the table.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .contracts import (
    SCHEMA_VERSION,
    ConfidenceBand,
    ExtractedTable,
    ProvenanceRef,
    ValueKind,
)
from .domain import confidence_from_score
from .tabular import decode_bytes

__all__ = [
    "MarkdownTableDraft",
    "collect_markdown_tables",
    "read_markdown_file",
    "read_markdown_tables",
]

_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$")
_FENCE = re.compile(r"^\s*(```+|~~~+)")
_CAPTION = re.compile(
    r"^\s*(?:>\s*)?(?:\*\*|__)?\s*(?:table|tab\.?|表)\s*[\w.\-]*\s*[:：.]?\s*(?:\*\*|__)?\s*(.*)$",
    re.IGNORECASE,
)
_CELL_SPLIT = re.compile(r"(?<!\\)\|")


@dataclass(frozen=True, slots=True)
class MarkdownTableDraft:
    """One pipe table as parsed, before it becomes an ExtractedTable."""

    index: int
    columns: tuple[str, ...] = ()
    rows: tuple[dict[str, object], ...] = ()
    alignments: tuple[str, ...] = ()
    caption: str = ""
    heading: str = ""
    line_start: int = 0
    line_end: int = 0
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "columns": list(self.columns),
            "rows": [dict(row) for row in self.rows],
            "alignments": list(self.alignments),
            "caption": self.caption,
            "heading": self.heading,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "warnings": list(self.warnings),
        }


def _split_row(line: str) -> list[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|") and not text.endswith("\\|"):
        text = text[:-1]
    return [cell.strip().replace("\\|", "|") for cell in _CELL_SPLIT.split(text)]


def _alignment(cell: str) -> str:
    left = cell.strip().startswith(":")
    right = cell.strip().endswith(":")
    if left and right:
        return "center"
    if right:
        return "right"
    if left:
        return "left"
    return "none"


def _heading_before(lines: Sequence[str], position: int) -> str:
    for index in range(position - 1, max(-1, position - 8), -1):
        text = lines[index].strip()
        if text.startswith("#"):
            return text.lstrip("#").strip()
    return ""


def _caption_before(lines: Sequence[str], position: int) -> str:
    for index in range(position - 1, max(-1, position - 4), -1):
        text = lines[index].strip()
        if not text:
            continue
        match = _CAPTION.match(text)
        if match is not None:
            remainder = match.group(1).strip().strip("*_").strip()
            return remainder or text.strip("*_ ").strip()
        return ""
    return ""


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


def collect_markdown_tables(
    text: str, *, max_tables: int = 100
) -> tuple[MarkdownTableDraft, ...]:
    """Find every pipe table outside fenced code blocks."""
    lines = text.splitlines()
    drafts: list[MarkdownTableDraft] = []
    in_fence = False
    index = 0
    while index < len(lines):
        line = lines[index]
        if _FENCE.match(line):
            in_fence = not in_fence
            index += 1
            continue
        if in_fence or "|" not in line:
            index += 1
            continue
        separator = lines[index + 1] if index + 1 < len(lines) else ""
        if not _SEPARATOR.match(separator) or "|" not in separator:
            index += 1
            continue
        header_cells = _split_row(line)
        separator_cells = _split_row(separator)
        alignments = tuple(_alignment(cell) for cell in separator_cells)
        warnings: list[str] = []
        if len(separator_cells) != len(header_cells):
            warnings.append(
                f"separator_mismatch:{len(header_cells)}!={len(separator_cells)}"
            )
        columns, duplicates = _dedupe(
            [
                name or f"column_{position + 1}"
                for position, name in enumerate(header_cells)
            ]
        )
        warnings.extend(f"duplicate_column:{name}" for name in duplicates)
        width = len(columns)
        rows: list[dict[str, object]] = []
        cursor = index + 2
        while cursor < len(lines):
            candidate = lines[cursor]
            if not candidate.strip() or "|" not in candidate or _FENCE.match(candidate):
                break
            if _SEPARATOR.match(candidate):
                warnings.append("repeated_separator_row")
                cursor += 1
                continue
            cells = _split_row(candidate)
            if len(cells) > width:
                warnings.append("extra_columns")
            elif len(cells) < width:
                warnings.append("ragged_rows")
            record: dict[str, object] = {
                name: (cells[position] if position < len(cells) else "")
                for position, name in enumerate(columns)
            }
            for extra in range(len(cells) - width):
                record[f"extra_{extra + 1}"] = cells[width + extra]
            record["_row_index"] = cursor + 1
            rows.append(record)
            cursor += 1
        if not rows:
            warnings.append("header_only_table")
        drafts.append(
            MarkdownTableDraft(
                index=len(drafts),
                columns=tuple(columns),
                rows=tuple(rows),
                alignments=alignments,
                caption=_caption_before(lines, index),
                heading=_heading_before(lines, index),
                line_start=index + 1,
                line_end=cursor,
                warnings=tuple(dict.fromkeys(warnings)),
            )
        )
        if len(drafts) >= max_tables:
            break
        index = cursor
    return tuple(drafts)


def read_markdown_tables(
    text: str,
    *,
    source_id: str,
    table_id_prefix: str = "md",
    path: str = "",
) -> tuple[ExtractedTable, ...]:
    """Turn Markdown text into :class:`ExtractedTable` objects with provenance."""
    tables: list[ExtractedTable] = []
    for draft in collect_markdown_tables(text):
        score = 0.92
        if not draft.rows:
            score -= 0.3
        score -= 0.05 * len(draft.warnings)
        provenance = ProvenanceRef(
            source_id=source_id,
            locator={
                "path": path,
                "format": "markdown",
                "table_index": draft.index,
                "line_start": draft.line_start,
                "line_end": draft.line_end,
                "caption": draft.caption,
                "heading": draft.heading,
                "alignments": list(draft.alignments),
                "column_count": len(draft.columns),
            },
            extractor="jw.scientific_data.markdown_tables.read_markdown_tables",
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
                header_row=0 if draft.columns else -1,
                caption=draft.caption or draft.heading,
                provenance=provenance,
                format="markdown",
                parse_warnings=draft.warnings,
                confidence=provenance.confidence,
            )
        )
    return tuple(tables)


def read_markdown_file(
    path: str | Path,
    *,
    source_id: str,
    table_id_prefix: str = "md",
    max_tables: int = 100,
) -> tuple[ExtractedTable, ...]:
    """Read a Markdown file (any encoding) and extract its pipe tables."""
    target = Path(path)
    text, _encoding, _warnings = decode_bytes(target.read_bytes())
    return read_markdown_tables(
        text,
        source_id=source_id,
        table_id_prefix=table_id_prefix,
        path=str(target),
    )[:max_tables]
