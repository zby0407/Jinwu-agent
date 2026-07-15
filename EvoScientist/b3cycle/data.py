from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path
from typing import Any


def repo_root() -> Path:
    env_root = os.getenv("B3_PROJECT_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    candidate = Path(__file__).resolve().parents[2]
    if candidate.name == "code" and (candidate.parent / "release_manifest.json").exists():
        return candidate.parent
    return candidate


def is_submission_release_layout() -> bool:
    return (repo_root() / "release_manifest.json").exists()


def code_root() -> Path:
    root = repo_root()
    if is_submission_release_layout():
        return root / "code"
    return root


def source_root() -> Path:
    return code_root() / "src"


def scripts_root() -> Path:
    return code_root() / "scripts_b3"


def app_file() -> Path:
    return code_root() / "app_b3.py"


def frontend_root() -> Path:
    root = repo_root()
    if is_submission_release_layout():
        return root / "frontend" / "static_b3"
    return root / "static_b3"


def materials_root() -> Path:
    root = repo_root()
    if is_submission_release_layout():
        return root / "materials" / "extracted_text"
    return root / "materials_b3" / "extracted_text"


def final_report_root() -> Path:
    root = repo_root()
    if is_submission_release_layout():
        return root / "paper"
    return root / "b3" / "final_report"


def figures_root() -> Path:
    root = repo_root()
    if is_submission_release_layout():
        return root / "figures"
    return root / "b3" / "final_report" / "figures"


def b3_root() -> Path:
    if is_submission_release_layout():
        return repo_root()
    return repo_root() / "b3"


def runtime_root() -> Path | None:
    """Return the opt-in mutable workspace used by a standalone final bundle."""

    value = os.getenv("B3_RUNTIME_ROOT")
    if not value:
        return None
    path = Path(value).expanduser().resolve()
    project = repo_root().resolve()
    if path != project / "runtime":
        raise ValueError("B3_RUNTIME_ROOT must equal B3_PROJECT_ROOT/runtime")
    return path


def raw_root() -> Path:
    if is_submission_release_layout():
        return repo_root() / "data" / "raw"
    return b3_root() / "data" / "raw"


def processed_root() -> Path:
    runtime = runtime_root()
    if runtime is not None:
        path = runtime / "data" / "processed"
        path.mkdir(parents=True, exist_ok=True)
        return path
    if is_submission_release_layout():
        path = repo_root() / "data" / "processed"
        path.mkdir(parents=True, exist_ok=True)
        return path
    path = b3_root() / "data" / "processed"
    path.mkdir(parents=True, exist_ok=True)
    return path


def output_root() -> Path:
    runtime = runtime_root()
    if runtime is not None:
        path = runtime / "outputs"
        path.mkdir(parents=True, exist_ok=True)
        return path
    if is_submission_release_layout():
        path = repo_root() / "results"
        path.mkdir(parents=True, exist_ok=True)
        return path
    path = b3_root() / "outputs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _float(value: str | int | float | None) -> float:
    if value is None:
        return float("nan")
    try:
        return float(value)
    except ValueError:
        return float("nan")


def read_silso_monthly(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, delimiter=";")
        for row in reader:
            if len(row) < 4:
                continue
            value = _float(row[3])
            rows.append(
                {
                    "year": int(row[0]),
                    "month": int(row[1]),
                    "decimal_year": _float(row[2]),
                    "ssn": value,
                    "valid": value >= 0,
                }
            )
    return rows


def read_silso_hemispheric(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, delimiter=";")
        for row in reader:
            if len(row) < 6:
                continue
            rows.append(
                {
                    "year": int(row[0]),
                    "month": int(row[1]),
                    "decimal_year": _float(row[2]),
                    "total": _float(row[3]),
                    "north": _float(row[4]),
                    "south": _float(row[5]),
                }
            )
    return rows


_CATALOGUE_B_FIELDS = (
    "north",
    "south",
    "north_smoothed",
    "south_smoothed",
    "north_area",
    "south_area",
    "north_area_smoothed",
    "south_area_smoothed",
    "north_temmer2006",
    "south_temmer2006",
    "north_temmer2006_smoothed",
    "south_temmer2006_smoothed",
    "north_silso",
    "south_silso",
    "north_silso_smoothed",
    "south_silso_smoothed",
)


def read_silso_extended_hemispheric(path: Path) -> list[dict[str, Any]]:
    """Read SILSO/Veronig Catalogue B with explicit component provenance.

    Catalogue B is a headerless semicolon-delimited table.  Missing component
    values are encoded as ``-1.0`` and are preserved so callers can distinguish
    unavailable source components from physical zeros.  The field order follows
    SILSO's byte-by-byte Catalogue B description.
    """

    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, delimiter=";")
        for line_number, row in enumerate(reader, start=1):
            if not row or all(not cell.strip() for cell in row):
                continue
            if len(row) != 17:
                raise ValueError(
                    f"Catalogue B line {line_number} must contain 17 fields"
                )
            match = re.fullmatch(r"(\d{4})-(\d{2})", row[0].strip())
            if match is None:
                raise ValueError(
                    f"Catalogue B line {line_number} has an invalid YYYY-MM date"
                )
            year = int(match.group(1))
            month = int(match.group(2))
            if month < 1 or month > 12:
                raise ValueError(
                    f"Catalogue B line {line_number} has an invalid month"
                )
            values = [_float(value) for value in row[1:]]
            if any(value != value for value in values):
                raise ValueError(
                    f"Catalogue B line {line_number} has a non-numeric value"
                )
            parsed: dict[str, Any] = {
                "date": f"{year:04d}-{month:02d}",
                "year": year,
                "month": month,
                "decimal_year": year + (month - 0.5) / 12.0,
            }
            parsed.update(dict(zip(_CATALOGUE_B_FIELDS, values)))
            rows.append(parsed)
    return rows


def read_noaa_observed(path: Path) -> list[dict[str, Any]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    out: list[dict[str, Any]] = []
    for row in rows:
        year, month = str(row["time-tag"]).split("-")
        out.append(
            {
                "year": int(year),
                "month": int(month),
                "decimal_year": int(year) + (int(month) - 0.5) / 12.0,
                "ssn": _float(row.get("ssn")),
                "smoothed_ssn": _float(row.get("smoothed_ssn")),
                "f10_7": _float(row.get("f10.7")),
                "smoothed_f10_7": _float(row.get("smoothed_f10.7")),
            }
        )
    return out


def read_noaa_predicted(path: Path) -> list[dict[str, Any]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    out: list[dict[str, Any]] = []
    for row in rows:
        year, month = str(row["time-tag"]).split("-")
        out.append(
            {
                "year": int(year),
                "month": int(month),
                "decimal_year": int(year) + (int(month) - 0.5) / 12.0,
                "predicted_ssn": _float(row.get("predicted_ssn")),
                "low_ssn": _float(row.get("low_ssn")),
                "high_ssn": _float(row.get("high_ssn")),
                "predicted_f10_7": _float(row.get("predicted_f10.7")),
                "low_f10_7": _float(row.get("low_f10.7")),
                "high_f10_7": _float(row.get("high_f10.7")),
            }
        )
    return out


_WSO_PATTERN = re.compile(
    r"(?P<date>\d{4}:\d{2}:\d{2})_\d{2}h:\d{2}m:\d{2}s\s+"
    r"(?P<north>[+-]?\d+|XXX)N\s+"
    r"(?P<south>[+-]?\d+|XXX)S\s+"
    r"(?P<avg>[+-]?\d+|XXX)Avg\s+"
    r"20nhz filt:\s+"
    r"(?P<north_filtered>[+-]?\d+|XXX)Nf\s+"
    r"(?P<south_filtered>[+-]?\d+|XXX)Sf\s+"
    r"(?P<avg_filtered>[+-]?\d+|XXX)Avgf"
)


def _wso_float(value: str) -> float:
    if value == "XXX":
        return float("nan")
    return _float(value)


def read_wso_polar(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    rows: list[dict[str, Any]] = []
    for match in _WSO_PATTERN.finditer(text):
        year_s, month_s, day_s = match.group("date").split(":")
        year = int(year_s)
        month = int(month_s)
        day = int(day_s)
        rows.append(
            {
                "year": year,
                "month": month,
                "day": day,
                "decimal_year": year + (month - 1) / 12.0 + (day - 0.5) / 365.25,
                "north": _wso_float(match.group("north")),
                "south": _wso_float(match.group("south")),
                "avg": _wso_float(match.group("avg")),
                "north_filtered": _wso_float(match.group("north_filtered")),
                "south_filtered": _wso_float(match.group("south_filtered")),
                "avg_filtered": _wso_float(match.group("avg_filtered")),
            }
        )
    return rows
