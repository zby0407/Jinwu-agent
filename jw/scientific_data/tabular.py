"""Delimiter-separated table reading: sniffing, encoding, header and ragged rows.

Everything here stays *raw*: cell values are kept as the strings the file
contains, because normalising numbers, units and dates is a later stage that
must be able to show what the source actually said. The module reports what it
suspected (encoding, delimiter, header row, truncation) instead of hiding it.

Supported: ``.csv .tsv .txt .psv .dat`` (and their ``.gz`` forms), plus JSON
record arrays via :func:`read_json_records`.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .contracts import (
    SCHEMA_VERSION,
    ConfidenceBand,
    ExtractedTable,
    ProvenanceRef,
    ValueKind,
)
from .domain import confidence_from_score, slug

__all__ = [
    "TableProfile",
    "decode_bytes",
    "flatten_nested",
    "read_json_records",
    "read_table",
    "read_text_file",
    "sniff_table",
]

ENCODING_CANDIDATES: tuple[str, ...] = (
    "utf-8-sig",
    "utf-8",
    "gb18030",
    "big5",
    "cp1252",
    "latin-1",
)
DELIMITER_CANDIDATES: tuple[str, ...] = (",", "\t", ";", "|")
MAX_HEADER_SCAN = 25
_SUFFIX_FORMATS = {
    ".csv": "csv",
    ".tsv": "tsv",
    ".tab": "tsv",
    ".txt": "txt",
    ".psv": "psv",
    ".dat": "dat",
    ".json": "json",
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
}
_NUMBER_RE = re.compile(r"^[-+]?(?:\d[\d ,]*(?:\.\d[\d ,]*)?|\.\d+)(?:[eE][-+]?\d+)?$")
_WHITESPACE_RUN = re.compile(r"\s{2,}")
_LOW_BYTE_FALLBACKS = frozenset({"cp1252", "latin-1"})
_EXACT_COLUMN = re.compile(r"^(?P<base>.+?)#(?P<index>[2-9]|\d{2,})$")


@dataclass(frozen=True, slots=True)
class TableProfile:
    """What a delimited-text file looks like before it is read."""

    path: str
    encoding: str = "utf-8"
    encoding_candidates: tuple[str, ...] = ENCODING_CANDIDATES
    delimiter: str = ","
    comment_char: str = "#"
    columns: tuple[str, ...] = ()
    header_row: int = -1
    row_count: int = 0
    field_count: int = 0
    numeric_share: float = 0.0
    confidence_score: float = -1.0
    notes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    extra: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "encoding": self.encoding,
            "encoding_candidates": list(self.encoding_candidates),
            "delimiter": self.delimiter,
            "comment_char": self.comment_char,
            "columns": list(self.columns),
            "header_row": self.header_row,
            "row_count": self.row_count,
            "field_count": self.field_count,
            "numeric_share": round(self.numeric_share, 6),
            "confidence_score": round(self.confidence_score, 6),
            "notes": list(self.notes),
            "warnings": list(self.warnings),
        }


def decode_bytes(data: bytes) -> tuple[str, str, tuple[str, ...]]:
    """Return ``(text, encoding, warnings)`` for raw bytes.

    Candidates are tried in order; the first clean decode that does not need
    error replacement wins. A high-byte fallback (cp1252/latin-1) always
    decodes, so it is reported as a suspect encoding rather than trusted.
    """
    warnings: list[str] = []
    candidates = list(ENCODING_CANDIDATES)
    if data.startswith(b"\xef\xbb\xbf"):
        warnings.append("byte_order_mark:utf-8-sig")
        candidates.remove("utf-8-sig")
        candidates.insert(0, "utf-8-sig")
    else:
        # a plain utf-8 file must not be reported as utf-8-sig
        candidates.remove("utf-8-sig")
        candidates.append("utf-8-sig")
    for encoding in candidates:
        try:
            text = data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        if "\ufffd" in text:
            continue
        if encoding in _LOW_BYTE_FALLBACKS and any(byte > 0x7F for byte in data):
            warnings.append(f"encoding_suspect:{encoding}")
            return text, encoding, tuple(warnings)
        return text, encoding, tuple(warnings)
    warnings.append("encoding_suspect:undecodable")
    return data.decode("utf-8", errors="replace"), "utf-8", tuple(warnings)


def read_text_file(path: str | Path) -> tuple[str, str, tuple[str, ...]]:
    """Read a text file (transparently gunzipping ``.gz``) and report its encoding."""
    target = Path(path)
    data = target.read_bytes()
    if data[:2] == b"\x1f\x8b" or target.suffix == ".gz":
        data = gzip.decompress(data)
    return decode_bytes(data)


def _is_comment(line: str, comment_char: str) -> bool:
    return bool(comment_char) and line.lstrip().startswith(comment_char)


def _split(line: str, delimiter: str) -> list[str]:
    if delimiter == "":
        return [piece for piece in _WHITESPACE_RUN.split(line.strip()) if piece]
    try:
        return next(csv.reader([line], delimiter=delimiter, strict=False))
    except (csv.Error, StopIteration):
        return [piece.strip() for piece in line.split(delimiter)]


def _strip_quotes(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _looks_numeric(value: str) -> bool:
    text = _strip_quotes(value).replace(" ", "")
    return bool(text) and bool(_NUMBER_RE.match(text))


def _detect_delimiter(
    lines: Sequence[str], comment_char: str
) -> tuple[str, int, list[str]]:
    """Pick the delimiter whose field counts are the most consistent."""
    notes: list[str] = []
    sample = [
        line for line in lines if line.strip() and not _is_comment(line, comment_char)
    ]
    if not sample:
        return ",", 0, ["empty_sample"]
    best: tuple[float, int, str] | None = None
    for delimiter in (*DELIMITER_CANDIDATES, ""):
        counts = [len(_split(line, delimiter)) for line in sample]
        modal = Counter(counts).most_common(1)[0]
        consistency = modal[1] / len(counts)
        field_count = modal[0]
        if field_count < 2:
            continue
        # prefer consistency, then more columns (a comma inside a TSV scores low)
        score = consistency + min(field_count, 40) / 1000.0
        if best is None or score > best[0]:
            best = (score, field_count, delimiter)
        if delimiter and sum(1 for line in sample if '"' in line) > 0:
            notes.append("quoted_fields_present")
    if best is None:
        return ",", 1, ["single_column_table"]
    _score, field_count, delimiter = best
    if delimiter == "":
        notes.append("whitespace_delimited")
    return delimiter, field_count, notes


def _unique_columns(names: Sequence[str]) -> tuple[list[str], list[str]]:
    """Append ``#2``/``#3`` to repeated column names, keeping order stable."""
    out: list[str] = []
    warnings: list[str] = []
    seen: Counter[str] = Counter()
    for raw_name in names:
        name = _strip_quotes(raw_name).strip() or f"column_{len(out) + 1}"
        seen[name] += 1
        if seen[name] > 1:
            candidate = f"{name}#{seen[name]}"
            while candidate in out:
                bumped = _EXACT_COLUMN.match(candidate)
                assert bumped is not None
                candidate = f"{bumped.group('base')}#{int(bumped.group('index')) + 1}"
            warnings.append(f"duplicate_column:{name}")
            out.append(candidate)
            seen[candidate] = seen[candidate] + 1
        else:
            out.append(name)
    return out, warnings


def _header_index(
    lines: Sequence[str], delimiter: str, field_count: int, comment_char: str
) -> int:
    """First line that looks like a header, or -1 when none does."""
    scanned = 0
    for index, line in enumerate(lines):
        if not line.strip() or _is_comment(line, comment_char):
            continue
        scanned += 1
        cells = [cell.strip() for cell in _split(line, delimiter)]
        if len(cells) != field_count:
            continue
        if scanned > MAX_HEADER_SCAN:
            break
        if not all(cells):
            continue
        if any(_looks_numeric(cell) for cell in cells):
            continue
        return index
    return -1


def _caption(lines: Sequence[str], comment_char: str, limit: int = 6) -> str:
    collected: list[str] = []
    for line in lines[:200]:
        if _is_comment(line, comment_char):
            collected.append(line.lstrip(comment_char).strip())
        elif line.strip():
            break
        if len(collected) >= limit:
            break
    return "\n".join(item for item in collected if item)


def sniff_table(path: str | Path, *, max_bytes: int = 65536) -> TableProfile:
    """Inspect a delimited-text file without committing to a full parse."""
    target = Path(path)
    text, encoding, encoding_warnings = read_text_file(target)
    notes: list[str] = []
    warnings: list[str] = list(encoding_warnings)
    sample = text[: max(1, int(max_bytes))]
    if len(text) > len(sample):
        notes.append(f"truncated_sample:bytes={len(sample)}")
    newline = "\r\n" if "\r\n" in sample else "\n"
    lines = sample.split(newline)
    comment_char = "#" if any(_is_comment(line, "#") for line in lines[:10]) else ""
    delimiter, field_count, delimiter_notes = _detect_delimiter(lines, comment_char)
    notes.extend(delimiter_notes)
    header_row = _header_index(lines, delimiter, field_count, comment_char)
    columns: tuple[str, ...] = ()
    if header_row >= 0:
        columns, column_warnings = _unique_columns(
            [cell.strip() for cell in _split(lines[header_row], delimiter)]
        )
        warnings.extend(column_warnings)
    data_lines = [
        line
        for line in lines[header_row + 1 :]
        if line.strip() and not _is_comment(line, comment_char)
    ]
    total = 0
    numeric = 0
    for line in data_lines[:200]:
        for cell in _split(line, delimiter):
            cell = cell.strip()
            if not cell:
                continue
            total += 1
            if _looks_numeric(cell):
                numeric += 1
    numeric_share = numeric / total if total else 0.0
    if field_count < 2:
        notes.append("single_column_table")
    if "\r\n" in sample:
        notes.append("crlf_line_endings")
    confidence = 0.95
    if header_row < 0:
        confidence -= 0.25
        notes.append("header_not_detected")
    if "\ufffd" in text:
        confidence -= 0.5
        warnings.append("encoding_suspect:replacement_char")
    confidence -= 0.1 * len(warnings)
    return TableProfile(
        path=str(target),
        encoding=encoding,
        delimiter=delimiter,
        comment_char=comment_char,
        columns=tuple(columns),
        header_row=header_row,
        row_count=max(0, len(data_lines)),
        field_count=field_count,
        numeric_share=numeric_share,
        confidence_score=max(0.05, confidence),
        notes=tuple(dict.fromkeys(notes)),
        warnings=tuple(dict.fromkeys(warnings)),
        extra={"newline": newline, "caption": _caption(lines, comment_char)},
    )


def read_table(
    path: str | Path,
    *,
    source_id: str,
    table_id: str = "",
    encoding: str = "",
    delimiter: str = "",
    comment_char: str = "",
    max_rows: int | None = None,
) -> tuple[ExtractedTable, ...]:
    """Read one delimited-text (or JSON) file into raw :class:`ExtractedTable` rows.

    Never raises for malformed content: unreadable shapes come back with
    ``parse_warnings`` describing the problem, and values stay strings.
    """
    target = Path(path)
    suffix = target.suffix.lower()
    if suffix == ".gz":
        suffix = target.suffixes[-2].lower() if len(target.suffixes) > 1 else ""
    if suffix in {".json", ".jsonl", ".ndjson"}:
        if encoding:
            text, warnings = target.read_text(encoding=encoding), ()
        else:
            text, _encoding, warnings = read_text_file(target)
        payload = _load_json(text, suffix in {".jsonl", ".ndjson"})
        table = read_json_records(
            payload,
            source_id=source_id,
            table_id=table_id or f"{slug(target.stem)}_000",
        )
        if warnings:
            table = _with_warnings(table, warnings)
        return (table,)

    profile = sniff_table(target)
    text, used_encoding, encoding_warnings = read_text_file(target)
    if encoding:
        try:
            text = (
                gzip.decompress(target.read_bytes()).decode(encoding)
                if suffix == ".gz"
                else target.read_text(encoding=encoding)
            )
            used_encoding = encoding
        except (UnicodeDecodeError, OSError):
            encoding_warnings = (
                *encoding_warnings,
                f"encoding_requested_failed:{encoding}",
            )
    used_delimiter = delimiter or profile.delimiter
    used_comment = comment_char if comment_char else profile.comment_char
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(newline)
    warnings = list(dict.fromkeys((*profile.warnings, *encoding_warnings)))
    notes = list(profile.notes)

    records, kept_lines = _read_records(text, used_delimiter, used_comment)
    if not records:
        return (
            _empty_table(
                target,
                source_id=source_id,
                table_id=table_id,
                format_name=_format_name(target),
                warnings=(*warnings, "empty_file"),
                encoding=used_encoding,
            ),
        )

    width = profile.field_count or max(len(cells) for _index, cells in records)
    header_position = -1
    columns: list[str] = []
    if delimiter:
        header_position = _find_header_record(records, width)
        if header_position >= 0:
            columns, column_warnings = _unique_columns(records[header_position][1])
            warnings.extend(column_warnings)
    elif profile.header_row >= 0 and profile.columns:
        header_position = _record_at_line(records, kept_lines, profile.header_row)
        columns = list(profile.columns)
        if header_position < 0:
            notes.append("header_line_not_a_record")
    if header_position < 0:
        columns = [f"column_{number}" for number in range(1, width + 1)]
        warnings.append("no_header_row")
    header_index = (
        kept_lines[records[header_position][0]] if header_position >= 0 else -1
    )

    width = len(columns)
    rows: list[dict[str, Any]] = []
    ragged = 0
    overflow = 0
    for record_index, cells in records[header_position + 1 :]:
        if max_rows is not None and len(rows) >= max_rows:
            notes.append(f"row_limit:{max_rows}")
            break
        cells = [cell.strip() for cell in cells]
        if len(cells) < width:
            ragged += 1
            cells = [*cells, *[""] * (width - len(cells))]
        elif len(cells) > width:
            overflow += 1
        row: dict[str, Any] = dict(zip(columns, cells, strict=False))
        for extra_index, extra in enumerate(cells[width:]):
            row[f"extra_{extra_index + 1}"] = extra
        row["_row_index"] = kept_lines[record_index]
        rows.append(row)
    if ragged:
        warnings.append(f"ragged_rows:{ragged}")
    if overflow:
        warnings.append(f"extra_columns:{overflow}")
    if sum(line.count('"') for line in lines) % 2 == 1:
        warnings.append("possible_truncation")

    confidence = profile.confidence_score
    if rows:
        confidence = min(0.99, confidence + 0.02)
    provenance = ProvenanceRef(
        source_id=source_id,
        locator={
            "path": str(target),
            "line_start": (
                kept_lines[records[min(header_position + 1, len(records) - 1)][0]]
                if records
                else 0
            ),
            "line_end": kept_lines[records[-1][0]] if records else 0,
            "header_row": header_index,
            "delimiter": used_delimiter or "whitespace",
            "encoding": used_encoding,
            "table_index": 0,
            "format": _format_name(target),
        },
        extractor="jw.scientific_data.tabular.read_table",
        extractor_version=SCHEMA_VERSION,
        value_kind=ValueKind.REPORTED,
        confidence=confidence_from_score(confidence),
        confidence_score=confidence,
        raw_excerpt=_clip("\n".join(lines[: max(1, header_index + 1)])),
    )
    return (
        ExtractedTable(
            table_id=table_id or f"{slug(target.stem)}_000",
            source_id=source_id,
            columns=tuple(columns),
            rows=tuple(rows),
            header_row=header_index,
            caption=profile.extra.get("caption", "") if profile.extra else "",
            provenance=provenance,
            format=_format_name(target),
            parse_warnings=tuple(dict.fromkeys((*warnings, *notes))),
            confidence=confidence_from_score(confidence),
        ),
    )


def _read_records(
    text: str, delimiter: str, comment_char: str
) -> tuple[list[tuple[int, list[str]]], list[int]]:
    """Parse delimited records while remembering each record's source line.

    ``csv`` handles quoted fields that span lines, so a record is not always a
    single physical line; the returned indexes point at the 1-based line a
    record starts on. Comment lines are removed before parsing.
    """
    lines = text.splitlines()
    kept: list[int] = []
    payload: list[str] = []
    for number, line in enumerate(lines, start=1):
        if comment_char and _is_comment(line, comment_char):
            continue
        kept.append(number)
        payload.append(line)
    if not payload:
        return [], []
    if not delimiter:
        records: list[tuple[int, list[str]]] = []
        for index, line in enumerate(payload):
            if not line.strip():
                continue
            piece = [cell for cell in _WHITESPACE_RUN.split(line.strip()) if cell]
            records.append((index, piece))
        return records, kept
    records = []
    stream = io.StringIO("\n".join(payload), newline="")
    reader = csv.reader(stream, delimiter=delimiter, quotechar='"', strict=False)
    while True:
        start_line = reader.line_num
        try:
            cells = next(reader)
        except StopIteration:
            break
        except csv.Error:
            records.append((start_line, []))
            continue
        if not cells or not any(cell.strip() for cell in cells):
            continue
        records.append((start_line, cells))
    return records, kept


def _record_at_line(
    records: Sequence[tuple[int, list[str]]], kept_lines: Sequence[int], line_index: int
) -> int:
    """Index of the record starting on ``line_index`` (0-based physical line)."""
    if not kept_lines:
        return -1
    target = line_index + 1
    for position, (record_index, _cells) in enumerate(records):
        if record_index < len(kept_lines) and kept_lines[record_index] == target:
            return position
    return -1


def _find_header_record(records: Sequence[tuple[int, list[str]]], width: int) -> int:
    """First record that reads like a header row, or -1."""
    for position, (_index, cells) in enumerate(records[:MAX_HEADER_SCAN]):
        stripped = [cell.strip() for cell in cells]
        if not stripped or any(not cell for cell in stripped):
            continue
        if len(stripped) not in {width, 0}:
            continue
        if any(_looks_numeric(cell) for cell in stripped):
            continue
        return position
    return -1


def _format_name(target: Path) -> str:
    suffixes = target.suffixes
    for suffix in reversed(suffixes):
        if suffix.lower() in _SUFFIX_FORMATS:
            return _SUFFIX_FORMATS[suffix.lower()]
    return "csv"


def _clip(text: str, limit: int = 200) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()
    return collapsed[:limit]


def _empty_table(
    target: Path,
    *,
    source_id: str,
    table_id: str,
    format_name: str,
    warnings: tuple[str, ...],
    encoding: str,
) -> ExtractedTable:
    provenance = ProvenanceRef(
        source_id=source_id,
        locator={
            "path": str(target),
            "line_start": 0,
            "header_row": -1,
            "encoding": encoding,
            "table_index": 0,
            "format": format_name,
        },
        extractor="jw.scientific_data.tabular.read_table",
        extractor_version=SCHEMA_VERSION,
        confidence=ConfidenceBand.UNVERIFIED,
        confidence_score=0.05,
    )
    return ExtractedTable(
        table_id=table_id or f"{slug(target.stem)}_000",
        source_id=source_id,
        columns=(),
        rows=(),
        header_row=-1,
        provenance=provenance,
        format=format_name,
        parse_warnings=tuple(dict.fromkeys(warnings)),
        confidence=ConfidenceBand.UNVERIFIED,
    )


def _with_warnings(table: ExtractedTable, warnings: Sequence[str]) -> ExtractedTable:
    return ExtractedTable(
        table_id=table.table_id,
        source_id=table.source_id,
        columns=table.columns,
        rows=table.rows,
        header_row=table.header_row,
        caption=table.caption,
        provenance=table.provenance,
        format=table.format,
        parse_warnings=tuple(dict.fromkeys((*table.parse_warnings, *warnings))),
        confidence=table.confidence,
    )


def _load_json(text: str, lines_format: bool) -> Any:
    if lines_format:
        records = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return []


def flatten_nested(payload: Any, prefix: str = "", sep: str = "_") -> dict[str, Any]:
    """Flatten nested mappings into parent-child keys; lists become strings.

    Scalars are stringified only for container values, so a numeric leaf keeps
    its raw JSON type for the alignment stage to see.
    """
    out: dict[str, Any] = {}
    if not isinstance(payload, Mapping):
        return out
    for key, value in payload.items():
        name = f"{prefix}{sep}{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            out.update(flatten_nested(value, name, sep))
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            if value and all(isinstance(item, Mapping) for item in value):
                out[f"{name}_count"] = len(value)
                out[name] = "; ".join(
                    _clip(json.dumps(item, sort_keys=True, ensure_ascii=False), 120)
                    for item in value[:20]
                )
            else:
                out[name] = "; ".join(_stringify(item) for item in value)
        else:
            out[name] = value
    return out


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def read_json_records(
    payload: Any,
    *,
    source_id: str,
    table_id: str = "",
    records_path: str = "",
) -> ExtractedTable:
    """Turn a JSON payload (or JSONL list) into one raw table.

    ``records_path`` is a dotted path with ``[n]`` steps, e.g.
    ``results[0].rows``; a wrong path yields an empty table plus a warning.
    """
    warnings: list[str] = []
    records: Any = payload
    if records_path:
        records = _walk_path(records, records_path)
        if records is None:
            warnings.append(f"records_path_not_found:{records_path}")
            records = []
    if isinstance(records, Mapping):
        records = [records]
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        warnings.append("json_payload_not_a_record_list")
        records = []

    rows: list[dict[str, Any]] = []
    columns: dict[str, None] = {}
    for index, item in enumerate(records):
        if not isinstance(item, Mapping):
            rows.append({"value": _stringify(item), "_row_index": index})
            columns["value"] = None
            continue
        flat = flatten_nested(item)
        flat["_row_index"] = index
        for name in flat:
            columns.setdefault(name, None)
        rows.append(flat)
    names = [str(name) for name in columns]
    if not rows:
        warnings.append("empty_json_records")
    provenance = ProvenanceRef(
        source_id=source_id,
        locator={
            "records_path": records_path,
            "table_index": 0,
            "format": "json",
            "record_count": len(rows),
        },
        extractor="jw.scientific_data.tabular.read_json_records",
        extractor_version=SCHEMA_VERSION,
        value_kind=ValueKind.REPORTED,
        confidence=confidence_from_score(0.9 if rows else 0.2),
        confidence_score=0.9 if rows else 0.2,
        raw_excerpt=_clip(json.dumps(rows[:2], ensure_ascii=False, sort_keys=True)),
    )
    return ExtractedTable(
        table_id=table_id or f"{slug(source_id)}_json_000",
        source_id=source_id,
        columns=tuple(names),
        rows=tuple(rows),
        header_row=0 if rows else -1,
        provenance=provenance,
        format="json",
        parse_warnings=tuple(dict.fromkeys(warnings)),
        confidence=provenance.confidence,
    )


def _walk_path(payload: Any, dotted: str) -> Any:
    current = payload
    for step in re.split(r"\.", dotted.strip(".")):
        if not step:
            continue
        match = re.match(r"^(?P<key>[^\[]+)?(?:\[(?P<index>\d+)\])?$", step)
        if match is None:
            return None
        key = match.group("key") or ""
        index = match.group("index")
        if key:
            if isinstance(current, Mapping) and key in current:
                current = current[key]
            else:
                return None
        if index is not None:
            if isinstance(current, Sequence) and int(index) < len(current):
                current = current[int(index)]
            else:
                return None
    return current
