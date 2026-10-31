"""Reshape published wide photometry tables into one row per measurement.

Many papers print light curves as ``MJD  B  V  R  I``, with each band column
holding ``magnitude error`` (or ``-``).  The rest of the kernel expects one
value per row, so this module performs the narrow, reversible reshape *before*
column mapping.  It never invents a value: cells that are not numeric (``-``,
``NULL``, blank) become explicit missing rows and are counted in
``wide_photometry_missing``.

Only a table that has one recognisable time column plus at least two band
columns is touched; ordinary long tables are returned unchanged.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from .contracts import ExtractedTable

__all__ = ["expand_wide_tables", "expand_wide_table"]

# Single-letter and common compound filter names.  The match is deliberately
# exact: ``Mag`` must not be mistaken for the B band just because it is short.
_BANDS = frozenset(
    {
        "u",
        "b",
        "v",
        "r",
        "i",
        "g",
        "z",
        "y",
        "j",
        "h",
        "k",
        "uc",
        "bc",
        "vc",
        "rc",
        "ic",
        "uvw2",
        "uvm2",
        "uvw1",
    }
)
_TIME_NAMES = frozenset(
    {"mjd", "jd", "date", "time", "epoch", "obsdate", "obsmjd", "mjddays"}
)
_PAIR = re.compile(
    r"^[<>]?\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
    r"(?:\s+([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?))?$"
)
_NON_VALUES = frozenset({"", "-", "--", "nan", "null", "none", "..."})


def _normal(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _is_time_column(name: str) -> bool:
    normal = _normal(name)
    return normal in _TIME_NAMES or normal.startswith("mjd") or normal.startswith("jd")


def _band_column(name: str) -> str:
    normal = _normal(name)
    return name.strip() if normal in _BANDS else ""


def _parse_cell(text: str) -> tuple[float, float | None] | None:
    raw = str(text or "").strip().replace("\u2212", "-")
    if raw.lower() in _NON_VALUES:
        return None
    match = _PAIR.match(raw)
    if match is None:
        return None
    value = float(match.group(1))
    error = float(match.group(2)) if match.group(2) is not None else None
    if error is not None and error < 0:
        return None
    return value, error


def _number(value: float) -> str:
    return format(value, ".10g")


def expand_wide_table(table: ExtractedTable) -> ExtractedTable | None:
    """Return the long-form table, or ``None`` when this is not wide photometry."""
    if not table.rows or not table.columns:
        return None
    normal_columns = {_normal(column) for column in table.columns}
    if normal_columns & {"band", "filter", "passband", "mag", "magnitude"}:
        return None
    time_columns = [column for column in table.columns if _is_time_column(column)]
    band_columns = [(column, _band_column(column)) for column in table.columns]
    band_columns = [(column, band) for column, band in band_columns if band]
    if len(time_columns) != 1 or len(band_columns) < 2:
        return None

    time_column = time_columns[0]
    rows: list[dict[str, Any]] = []
    missing = 0
    for row in table.rows:
        time = str(row.get(time_column, "") or "").strip()
        for column, band in band_columns:
            raw = str(row.get(column, "") or "").strip()
            parsed = _parse_cell(raw)
            if parsed is None:
                missing += 1
                rows.append(
                    {
                        time_column: time,
                        "band": band,
                        "mag": "",
                        "e_mag": "",
                        "_row_index": row.get("_row_index", ""),
                        "_source_column": column,
                        "_source_raw": raw,
                    }
                )
                continue
            value, error = parsed
            rows.append(
                {
                    time_column: time,
                    "band": band,
                    "mag": _number(value),
                    "e_mag": "" if error is None else _number(error),
                    "_row_index": row.get("_row_index", ""),
                    "_source_column": column,
                    "_source_raw": raw,
                }
            )
    if not rows:
        return None

    bands = ",".join(band for _column, band in band_columns)
    warnings = tuple(
        dict.fromkeys(
            (
                *table.parse_warnings,
                f"wide_photometry_reshaped:{bands}",
                f"wide_photometry_missing:{missing}",
                f"wide_photometry_source_columns:{bands}",
            )
        )
    )
    return ExtractedTable(
        table_id=f"{table.table_id}:bands",
        source_id=table.source_id,
        columns=(time_column, "band", "mag", "e_mag"),
        rows=tuple(rows),
        header_row=-1,
        caption=table.caption,
        provenance=table.provenance,
        format=table.format,
        parse_warnings=warnings,
        confidence=table.confidence,
    )


def expand_wide_tables(
    tables: Sequence[ExtractedTable],
) -> tuple[ExtractedTable, ...]:
    """Replace every wide photometry table with its long-form equivalent."""
    expanded: list[ExtractedTable] = []
    for table in tables:
        long_form = expand_wide_table(table)
        expanded.append(long_form or table)
    return tuple(expanded)
