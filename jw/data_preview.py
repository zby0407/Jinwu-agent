"""Parse registered observational dataset files into bounded table previews.

The WebUI's data-source settings dialog uses these helpers to show what an
ingested dataset actually looks like: column structure, head/tail sample
rows, numeric statistics, and a downsampled series suitable for a sparkline.

All previews are bounded: parsing stops after ``_MAX_PARSE_LINES`` lines and
series are downsampled to at most ``_MAX_SERIES_POINTS`` points, so the REST
response stays small even for centennial monthly series.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

from .solar_data_catalog import _wso_current_rows

_HEAD_ROWS = 8
_TAIL_ROWS = 8
_MAX_PARSE_LINES = 200_000
_MAX_SERIES_POINTS = 240

_MISSING_TOKENS = {"", "NaN", "nan", "NA", "N/A", "-1", "-1.0", "XXX"}

# Human-readable column labels per dataset id. Generic fallbacks are
# generated when a dataset is not listed here.
_COLUMN_LABELS: dict[str, list[str]] = {
    "silso-monthly-total-v2": [
        "year",
        "month",
        "date",
        "sunspot_number",
        "stddev",
        "observations",
        "marker",
    ],
    "silso-monthly-smoothed-v2": [
        "year",
        "month",
        "date",
        "smoothed_sunspot_number",
        "stddev",
        "observations",
        "marker",
    ],
    "silso-cycle-extrema-v2": [
        "cycle",
        "min_year",
        "min_month",
        "min_sn",
        "max_year",
        "max_month",
        "max_sn",
        "duration_years",
        "duration_months",
    ],
    "mwo-wso-polar-field-v2": [
        "n_mwo_year",
        "n_mwo_field_gauss",
        "n_mwo_sem",
        "n_wso_year",
        "n_wso_field_gauss",
        "n_wso_sem",
        "s_mwo_year",
        "s_mwo_field_gauss",
        "s_mwo_sem",
        "s_wso_year",
        "s_wso_field_gauss",
        "s_wso_sem",
    ],
    "noaa-swpc-monthly-f107-v1": ["month", "f10_7_flux"],
    "wso-current-polar-field-v1": [
        "date",
        "north_microtesla",
        "south_microtesla",
        "average_microtesla",
        "north_filtered_microtesla",
        "south_filtered_microtesla",
        "average_filtered_microtesla",
    ],
}

# Preferred (label column, value column, series name) tuples per dataset.
# The first resolvable tuple wins; up to two value columns are emitted.
_SERIES_SPECS: dict[str, list[tuple[str, str, str]]] = {
    "silso-monthly-total-v2": [("month_label", "sunspot_number", "月度总黑子数")],
    "silso-monthly-smoothed-v2": [
        ("month_label", "smoothed_sunspot_number", "13 个月平滑黑子数")
    ],
    "silso-cycle-extrema-v2": [("cycle", "max_sn", "活动周峰值黑子数")],
    "mwo-wso-polar-field-v2": [
        ("n_mwo_year", "n_mwo_field_gauss", "北半球极区磁场"),
        ("s_mwo_year", "s_mwo_field_gauss", "南半球极区磁场"),
    ],
    "noaa-swpc-monthly-f107-v1": [("month", "f10_7_flux", "F10.7 射电流量")],
    "wso-current-polar-field-v1": [
        ("date", "north_microtesla", "北半球"),
        ("date", "south_microtesla", "南半球"),
    ],
}


def _coerce(raw: str) -> float | str | None:
    token = raw.strip()
    if token in _MISSING_TOKENS:
        return None
    try:
        value = float(token)
    except ValueError:
        return token
    if math.isnan(value):
        return None
    return value


def _round(value: float) -> float:
    return round(value, 4)


def _is_numeric_cell(cell: float | str | None) -> bool:
    return isinstance(cell, (int, float)) and not isinstance(cell, bool)


def _sniff_delimiter(sample_lines: list[str]) -> str | None:
    joined = "\n".join(sample_lines[:20])
    if ";" in joined:
        return ";"
    if "," in joined:
        return ","
    if "\t" in joined:
        return "\t"
    return None


def _parse_text_rows(
    text: str,
) -> tuple[list[list[str]], str | None]:
    """Split raw text into field rows, skipping blanks and ``#`` comments."""

    lines = [
        line
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not lines:
        return [], None
    delimiter = _sniff_delimiter(lines)
    if delimiter:
        rows = [row for row in csv.reader(lines, delimiter=delimiter) if row]
    else:
        rows = [line.split() for line in lines]
    return rows, delimiter


def _looks_like_header(row: list[str]) -> bool:
    if not row:
        return False

    def _is_number(cell: str) -> bool:
        try:
            float(cell.strip())
        except ValueError:
            return False
        return True

    numeric = sum(1 for cell in row if _is_number(cell))
    return numeric < max(1, len(row) // 2)


def _build_table(
    dataset_id: str,
    header: list[str] | None,
    body: list[list[Any]],
) -> dict[str, Any]:
    width = max((len(row) for row in body), default=0)
    labels = list(_COLUMN_LABELS.get(dataset_id) or [])
    if header:
        labels = [cell.strip() or f"col_{i + 1}" for i, cell in enumerate(header)]
    while len(labels) < width:
        labels.append(f"col_{len(labels) + 1}")
    labels = labels[:width]

    columns: list[dict[str, Any]] = []
    stats: list[dict[str, Any]] = []
    for index, name in enumerate(labels):
        cells = [row[index] if index < len(row) else None for row in body]
        numeric_cells = [cell for cell in cells if _is_numeric_cell(cell)]
        missing = sum(1 for cell in cells if cell is None)
        columns.append(
            {
                "name": name,
                "numeric": len(numeric_cells) > max(1, len(cells) // 2),
                "missing": missing,
            }
        )
        if numeric_cells:
            values = [float(cell) for cell in numeric_cells]
            stats.append(
                {
                    "name": name,
                    "min": _round(min(values)),
                    "max": _round(max(values)),
                    "mean": _round(sum(values) / len(values)),
                    "missing": missing,
                }
            )
    return {"columns": columns, "stats": stats, "labels": labels}


def _downsample(
    points: list[dict[str, Any]], limit: int = _MAX_SERIES_POINTS
) -> list[dict[str, Any]]:
    if len(points) <= limit:
        return points
    stride = math.ceil(len(points) / limit)
    sampled = points[::stride]
    if sampled[-1] != points[-1]:
        sampled.append(points[-1])
    return sampled


def _format_number(value: float | int) -> str:
    numeric = float(value)
    if numeric.is_integer() and abs(numeric) < 1e15:
        return str(int(numeric))
    return str(round(numeric, 2))


def _compose_label(labels: list[str], row: list[Any], label_col: str) -> str | None:
    """Format a human axis label from the row's key column."""

    values = dict(zip(labels, row, strict=False))
    year = values.get("year")
    month = values.get("month")
    if (
        isinstance(year, (int, float))
        and isinstance(month, (int, float))
        and not isinstance(year, bool)
        and not isinstance(month, bool)
    ):
        return f"{int(year):04d}-{int(month):02d}"
    value = values.get(label_col)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _format_number(value)
    if isinstance(value, str) and value:
        return value
    return None


def _build_series(
    dataset_id: str, labels: list[str], body: list[list[Any]]
) -> list[dict[str, Any]]:
    specs = _SERIES_SPECS.get(dataset_id, [])
    series: list[dict[str, Any]] = []
    for label_col, value_name, display_name in specs:
        if len(series) >= 2:
            break
        if value_name not in labels:
            continue
        value_index = labels.index(value_name)
        points: list[dict[str, Any]] = []
        for row in body:
            value = row[value_index] if value_index < len(row) else None
            if not _is_numeric_cell(value):
                continue
            label = _compose_label(labels, row, label_col) or str(len(points) + 1)
            points.append({"label": label, "value": _round(float(value))})
        if points:
            series.append(
                {
                    "name": display_name,
                    "column": value_name,
                    "points": _downsample(points),
                    "point_count": len(points),
                }
            )
    return series


def _table_preview(
    dataset_id: str,
    header: list[str] | None,
    body: list[list[Any]],
    *,
    file_name: str,
) -> dict[str, Any]:
    table = _build_table(dataset_id, header, body)
    return {
        "dataset_id": dataset_id,
        "file": file_name,
        "columns": table["columns"],
        "stats": table["stats"],
        "row_count": len(body),
        "head_rows": body[:_HEAD_ROWS],
        "tail_rows": body[-_TAIL_ROWS:] if len(body) > _HEAD_ROWS else [],
        "omitted_rows": max(0, len(body) - _HEAD_ROWS - _TAIL_ROWS),
        "series": _build_series(dataset_id, table["labels"], body),
    }


def _preview_from_rows(
    dataset_id: str,
    rows: list[list[str]],
    *,
    file_name: str,
) -> dict[str, Any]:
    # Skip leading prose/header lines (e.g. SILSO's "Cycle Minimum Maximum"
    # banner or the polar-field CSV's column names). Explicit per-dataset
    # labels take precedence over whatever the file calls its columns.
    header: list[str] | None = None
    body_rows = rows
    while body_rows and _looks_like_header(body_rows[0]):
        if header is None and not _COLUMN_LABELS.get(dataset_id):
            header = body_rows[0]
        body_rows = body_rows[1:]
    body: list[list[Any]] = [[_coerce(cell) for cell in row] for row in body_rows]
    return _table_preview(dataset_id, header, body, file_name=file_name)


def build_dataset_preview(dataset_id: str, path: Path) -> dict[str, Any]:
    """Build a bounded JSON-safe preview for a registered dataset file."""

    suffix = path.suffix.lower()
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list) or not all(
            isinstance(item, dict) for item in payload
        ):
            raise ValueError("JSON dataset is not an array of objects")
        rows = [[str(key) for key in payload[0].keys()]] + [
            [str(item.get(key, "")) for key in payload[0].keys()] for item in payload
        ]
        return _preview_from_rows(dataset_id, rows, file_name=path.name)

    if dataset_id == "wso-current-polar-field-v1":
        records = _wso_current_rows(path.read_bytes())[:_MAX_PARSE_LINES]
        body = [
            [
                record["date"],
                record["north"],
                record["south"],
                record["average"],
                record["north_filtered"],
                record["south_filtered"],
                record["average_filtered"],
            ]
            for record in records
        ]
        return _table_preview(dataset_id, None, body, file_name=path.name)

    text = path.read_text(encoding="utf-8", errors="replace")
    rows, _delimiter = _parse_text_rows(text)
    rows = rows[:_MAX_PARSE_LINES]
    if not rows:
        raise ValueError("dataset file contains no parseable rows")
    return _preview_from_rows(dataset_id, rows, file_name=path.name)


__all__ = ["build_dataset_preview"]
