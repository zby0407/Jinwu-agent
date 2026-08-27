"""Curated acquisition of authoritative solar-cycle reference datasets."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .workspaces import register_project_data_file

_USER_AGENT = "Jinwu-research-data/2.0"
_SILSO_AUTHORITY_URL = "https://www.sidc.be/SILSO/DATA/SN_m_tot_V2.0.txt"
_SILSO_SMOOTHED_URL = "https://www.sidc.be/SILSO/DATA/SN_ms_tot_V2.0.csv"
_SILSO_EXTREMA_URL = "https://www.sidc.be/SILSO/DATA/Cycles/TableCyclesMiMa.txt"
_SILSO_DOI = "https://doi.org/10.24414/qnza-ac80"
_POLAR_PERSISTENT_ID = "doi:10.7910/DVN/KF96B2"
_POLAR_FILENAME = "e_PField_MWO_WSO.csv"
_NOAA_MONTHLY_F107_URL = (
    "https://services.swpc.noaa.gov/json/solar-cycle/f10-7cm-flux.json"
)
_WSO_CURRENT_POLAR_URL = "http://wso.stanford.edu/Polar.html"
_READINESS_CUTOFF = "2026-06-30"
_READINESS_WINDOW_START = "2026-01-01"
_FETCH_ATTEMPTS = 3
_FETCH_RETRY_DELAY_SECONDS = 0.5


def _fetch(url: str, *, timeout: float = 20.0) -> tuple[bytes, str]:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    for attempt in range(_FETCH_ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read(), response.geturl()
        except urllib.error.HTTPError:
            raise
        except (OSError, TimeoutError):
            if attempt + 1 == _FETCH_ATTEMPTS:
                raise
            time.sleep(_FETCH_RETRY_DELAY_SECONDS * (2**attempt))
    raise RuntimeError("unreachable authoritative data fetch state")


def _validate_silso_monthly(payload: bytes) -> dict[str, Any]:
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("SILSO monthly data is not ASCII") from exc
    rows = []
    for line_number, raw in enumerate(text.splitlines(), start=1):
        fields = raw.split()
        if not fields:
            continue
        if len(fields) not in {6, 7}:
            raise ValueError(f"invalid SILSO row {line_number}: expected 6 or 7 fields")
        try:
            year = int(fields[0])
            month = int(fields[1])
            value = float(fields[3])
        except ValueError as exc:
            raise ValueError(f"invalid SILSO row {line_number}") from exc
        if not 1 <= month <= 12 or value < -1:
            raise ValueError(f"invalid SILSO semantics at row {line_number}")
        rows.append((year, month))
    if len(rows) < 3_200 or rows[0] != (1749, 1) or rows[-1][0] < 2024:
        raise ValueError("SILSO coverage is too short for the curated monthly series")
    if rows != sorted(rows) or len(rows) != len(set(rows)):
        raise ValueError("SILSO monthly keys are not unique and monotonic")
    return {
        "row_count": len(rows),
        "coverage_start": f"{rows[0][0]:04d}-{rows[0][1]:02d}",
        "coverage_end": f"{rows[-1][0]:04d}-{rows[-1][1]:02d}",
        "format": "SILSO monthly total sunspot number V2.0 whitespace ASCII",
    }


def _validate_silso_smoothed(payload: bytes) -> dict[str, Any]:
    """Validate the official semicolon-delimited 13-month smoothed series."""

    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("SILSO smoothed data is not ASCII") from exc
    rows: list[tuple[int, int]] = []
    for line_number, fields in enumerate(
        csv.reader(text.splitlines(), delimiter=";"), 1
    ):
        if not fields:
            continue
        if len(fields) < 4:
            raise ValueError(f"invalid SILSO smoothed row {line_number}")
        try:
            year, month, value = int(fields[0]), int(fields[1]), float(fields[3])
        except ValueError as exc:
            raise ValueError(f"invalid SILSO smoothed row {line_number}") from exc
        if not 1 <= month <= 12 or value < -1:
            raise ValueError(f"invalid SILSO smoothed semantics at row {line_number}")
        if value >= 0:
            rows.append((year, month))
    if len(rows) < 3_100 or rows[0][0] != 1749 or rows[-1][0] < 2024:
        raise ValueError("SILSO smoothed coverage is too short")
    if rows != sorted(rows) or len(rows) != len(set(rows)):
        raise ValueError("SILSO smoothed monthly keys are not unique and monotonic")
    return {
        "row_count": len(rows),
        "coverage_start": f"{rows[0][0]:04d}-{rows[0][1]:02d}",
        "coverage_end": f"{rows[-1][0]:04d}-{rows[-1][1]:02d}",
        "format": "SILSO official 13-month smoothed monthly total V2.0",
    }


def _validate_silso_extrema(payload: bytes) -> dict[str, Any]:
    """Validate the official SILSO cycle minima/maxima table."""

    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("SILSO extrema table is not ASCII") from exc
    cycles: list[int] = []
    completed: list[int] = []
    for line_number, raw in enumerate(text.splitlines(), 1):
        fields = raw.split()
        if not fields or not fields[0].isdigit():
            continue
        if len(fields) < 4:
            raise ValueError(f"invalid SILSO extrema row {line_number}")
        try:
            cycle = int(fields[0])
            minimum_month = int(fields[2])
            float(fields[3])
            if len(fields) >= 7:
                maximum_month = int(fields[5])
                float(fields[6])
                if not 1 <= maximum_month <= 12:
                    raise ValueError
                completed.append(cycle)
        except ValueError as exc:
            raise ValueError(f"invalid SILSO extrema row {line_number}") from exc
        if not 1 <= minimum_month <= 12:
            raise ValueError(f"invalid SILSO extrema semantics at row {line_number}")
        cycles.append(cycle)
    if len(cycles) < 24 or cycles != sorted(set(cycles)) or 24 not in completed:
        raise ValueError("SILSO extrema table lacks completed historical cycles")
    return {
        "cycle_count": len(cycles),
        "cycle_start": min(cycles),
        "cycle_end": max(cycles),
        "latest_completed_cycle": max(completed),
        "format": "SILSO official cycle minima/maxima table V2.0",
    }


def _validate_polar_field(payload: bytes) -> dict[str, Any]:
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("polar-field data is not UTF-8") from exc
    data_lines = [line for line in lines if line.strip() and not line.startswith("#")]
    rows = list(csv.reader(data_lines))
    if rows and rows[0][0].strip() == "N MWO Date":
        rows = rows[1:]
    if len(rows) < 100 or any(len(row) != 12 for row in rows):
        raise ValueError(
            "polar-field data must contain at least 100 twelve-column rows"
        )
    try:
        north_years = [float(row[0]) for row in rows]
        south_years = [float(row[6]) for row in rows]
    except ValueError as exc:
        raise ValueError("polar-field measurement years are invalid") from exc
    if min(north_years) > 1907 or min(south_years) > 1907:
        raise ValueError("polar-field proxy does not reach the historical MWO record")
    if max(north_years) < 2023 or max(south_years) < 2023:
        raise ValueError("polar-field proxy is older than the curated release")
    return {
        "row_count": len(rows),
        "north_coverage": [min(north_years), max(north_years)],
        "south_coverage": [min(south_years), max(south_years)],
        "columns": 12,
        "units": "gauss for calibrated field and standard-error columns",
    }


def _validate_noaa_monthly_f107(payload: bytes) -> dict[str, Any]:
    """Validate the official NOAA SWPC monthly F10.7 solar-cycle product."""

    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("NOAA monthly F10.7 data is not valid JSON") from exc
    if not isinstance(decoded, list) or not decoded:
        raise ValueError("NOAA monthly F10.7 data must be a non-empty array")
    rows: list[tuple[str, float]] = []
    for index, item in enumerate(decoded):
        if not isinstance(item, dict):
            raise ValueError(f"invalid NOAA monthly F10.7 row {index}")
        month = item.get("time-tag")
        value = item.get("f10.7")
        if not isinstance(month, str) or re.fullmatch(r"\d{4}-\d{2}", month) is None:
            raise ValueError(f"invalid NOAA monthly F10.7 month at row {index}")
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not 40 <= float(value) <= 500
        ):
            raise ValueError(f"invalid NOAA monthly F10.7 value at row {index}")
        rows.append((month, float(value)))
    months = [month for month, _value in rows]
    if months != sorted(months) or len(months) != len(set(months)):
        raise ValueError("NOAA monthly F10.7 months are not unique and monotonic")
    return {
        "row_count": len(rows),
        "coverage_start": months[0],
        "coverage_end": months[-1],
        "cutoff_month": "2026-06",
        "cutoff_2026_06_available": "2026-06" in months,
        "unit": "solar_flux_unit",
        "format": "NOAA SWPC monthly 10.7 cm radio flux JSON",
    }


_WSO_CURRENT_ROW = re.compile(
    r"^(?P<year>\d{4}):(?P<month>\d{2}):(?P<day>\d{2})_\S+\s+"
    r"(?P<north>(?:[-+]?\d+|XXX))N\s+"
    r"(?P<south>(?:[-+]?\d+|XXX))S\s+"
    r"(?P<average>(?:[-+]?\d+|XXX))Avg\s+20nhz\s+filt:\s+"
    r"(?P<north_filtered>(?:[-+]?\d+|XXX))Nf\s+"
    r"(?P<south_filtered>(?:[-+]?\d+|XXX))Sf\s+"
    r"(?P<average_filtered>(?:[-+]?\d+|XXX))Avgf$"
)


def _wso_current_rows(payload: bytes) -> list[dict[str, Any]]:
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("WSO current polar-field page is not ASCII") from exc
    rows: list[dict[str, Any]] = []
    for raw in text.splitlines():
        match = _WSO_CURRENT_ROW.fullmatch(raw.strip())
        if match is None:
            continue
        date = "-".join(match.group(field) for field in ("year", "month", "day"))
        values = [
            match.group(field)
            for field in (
                "north",
                "south",
                "average",
                "north_filtered",
                "south_filtered",
                "average_filtered",
            )
        ]
        missing = any(value == "XXX" for value in values)
        rows.append(
            {
                "date": date,
                "missing": missing,
                "north": None if missing else int(values[0]),
                "south": None if missing else int(values[1]),
                "average": None if missing else int(values[2]),
                "north_filtered": None if missing else int(values[3]),
                "south_filtered": None if missing else int(values[4]),
                "average_filtered": None if missing else int(values[5]),
            }
        )
    return rows


def _validate_wso_current_polar_field(payload: bytes) -> dict[str, Any]:
    """Validate WSO's 10-day polar observations and preserve explicit gaps."""

    rows = _wso_current_rows(payload)
    if not rows:
        raise ValueError("WSO current polar-field page contains no observation rows")
    dates = [str(row["date"]) for row in rows]
    if dates != sorted(dates) or len(dates) != len(set(dates)):
        raise ValueError("WSO current polar-field dates are not unique and monotonic")
    through_cutoff = [row for row in rows if str(row["date"]) <= _READINESS_CUTOFF]
    valid_rows = [row for row in through_cutoff if row["missing"] is False]
    cutoff_window = [
        row
        for row in through_cutoff
        if _READINESS_WINDOW_START <= str(row["date"]) <= _READINESS_CUTOFF
    ]
    if not cutoff_window or not valid_rows:
        raise ValueError("WSO current polar-field page has no valid pre-cutoff record")
    missing_rows = [row for row in cutoff_window if row["missing"] is True]
    return {
        "row_count": len(rows),
        "coverage_start": dates[0],
        "coverage_end": dates[-1],
        "latest_valid_observation": str(valid_rows[-1]["date"]),
        "cutoff_window_start": _READINESS_WINDOW_START,
        "cutoff_date": _READINESS_CUTOFF,
        "cutoff_window_status": (
            "observations_missing" if missing_rows else "observed_through_cutoff"
        ),
        "missing_rows_in_cutoff_window": len(missing_rows),
        "cadence": "centered 30-day average reported every 10 days",
        "filtered_product": "20 nHz low-pass filter",
        "unit": "microtesla",
    }


def _acquire_silso() -> tuple[bytes, dict[str, Any]]:
    payload, _resolved = _fetch(_SILSO_AUTHORITY_URL)
    return payload, {
        "authority_url": _SILSO_AUTHORITY_URL,
        "retrieval_url": _SILSO_AUTHORITY_URL,
        "retrieval_source_kind": "authority",
        "dataset_doi": _SILSO_DOI,
        "license": "CC BY-NC 4.0",
        "validation": _validate_silso_monthly(payload),
    }


def _acquire_silso_reference(
    url: str, validator: Any, *, product: str
) -> tuple[bytes, dict[str, Any]]:
    payload, resolved = _fetch(url)
    return payload, {
        "authority_url": url,
        "retrieval_url": resolved,
        "retrieval_source_kind": "authority",
        "dataset_doi": _SILSO_DOI,
        "license": "CC BY-NC 4.0",
        "product": product,
        "validation": validator(payload),
    }


def _acquire_silso_smoothed() -> tuple[bytes, dict[str, Any]]:
    return _acquire_silso_reference(
        _SILSO_SMOOTHED_URL,
        _validate_silso_smoothed,
        product="official 13-month smoothed monthly total",
    )


def _acquire_silso_extrema() -> tuple[bytes, dict[str, Any]]:
    return _acquire_silso_reference(
        _SILSO_EXTREMA_URL,
        _validate_silso_extrema,
        product="official cycle minima and maxima",
    )


def _acquire_polar_field() -> tuple[bytes, dict[str, Any]]:
    encoded = urllib.parse.quote(_POLAR_PERSISTENT_ID, safe="")
    metadata_url = (
        "https://dataverse.harvard.edu/api/datasets/:persistentId/"
        f"?persistentId={encoded}"
    )
    metadata_bytes, _resolved_metadata_url = _fetch(metadata_url)
    metadata = json.loads(metadata_bytes)
    latest = metadata.get("data", {}).get("latestVersion", {})
    files = latest.get("files", [])
    selected = next(
        (
            item.get("dataFile", {})
            for item in files
            if item.get("dataFile", {}).get("filename") == _POLAR_FILENAME
            and item.get("restricted") is False
        ),
        None,
    )
    if not isinstance(selected, dict) or not isinstance(selected.get("id"), int):
        raise RuntimeError("curated polar-field file is missing from Dataverse")
    data_url = f"https://dataverse.harvard.edu/api/access/datafile/{selected['id']}"
    payload, _resolved_data_url = _fetch(data_url)
    checksum = selected.get("checksum", {})
    if checksum.get("type") != "MD5" or not isinstance(checksum.get("value"), str):
        raise RuntimeError("Dataverse polar-field file has no MD5 receipt")
    if hashlib.md5(payload, usedforsecurity=False).hexdigest() != checksum["value"]:
        raise RuntimeError("Dataverse polar-field upstream checksum mismatch")
    return payload, {
        "authority_url": "https://doi.org/10.7910/DVN/KF96B2",
        "metadata_url": metadata_url,
        "retrieval_url": data_url,
        "data_file_id": selected["id"],
        "persistent_id": _POLAR_PERSISTENT_ID,
        "dataverse_version": (
            f"{latest.get('versionNumber')}.{latest.get('versionMinorNumber')}"
        ),
        "release_time": latest.get("releaseTime"),
        "upstream_checksum": checksum,
        "license": latest.get("license"),
        "method_paper_doi": "https://doi.org/10.1088/0004-637X/753/2/146",
        "validation": _validate_polar_field(payload),
    }


def _acquire_noaa_monthly_f107() -> tuple[bytes, dict[str, Any]]:
    payload, resolved = _fetch(_NOAA_MONTHLY_F107_URL)
    return payload, {
        "authority_url": _NOAA_MONTHLY_F107_URL,
        "retrieval_url": resolved,
        "retrieval_source_kind": "authority",
        "product": "monthly observed 10.7 cm radio flux",
        "validation": _validate_noaa_monthly_f107(payload),
    }


def _acquire_wso_current_polar_field() -> tuple[bytes, dict[str, Any]]:
    payload, resolved = _fetch(_WSO_CURRENT_POLAR_URL)
    return payload, {
        "authority_url": _WSO_CURRENT_POLAR_URL,
        "retrieval_url": resolved,
        "retrieval_source_kind": "authority",
        "product": "WSO polar-field observations 1976-present",
        "validation": _validate_wso_current_polar_field(payload),
    }


def acquire_authoritative_solar_data(
    base_workspace: str | Path,
    *,
    project_id: str = "default",
    dataset_ids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Acquire, validate, and register curated solar-cycle research inputs."""

    retrieved_at = datetime.now(UTC).isoformat()
    records = []
    with tempfile.TemporaryDirectory(prefix="jinwu-solar-data-") as temp_dir:
        temporary_root = Path(temp_dir)
        specifications = (
            (
                "silso-monthly-total-v2",
                "solar_cycle/silso/monthly_total_v2/SN_m_tot_V2.0.txt",
                _acquire_silso,
            ),
            (
                "silso-monthly-smoothed-v2",
                "solar_cycle/silso/monthly_smoothed_v2/SN_ms_tot_V2.0.csv",
                _acquire_silso_smoothed,
            ),
            (
                "silso-cycle-extrema-v2",
                "solar_cycle/silso/cycle_extrema_v2/TableCyclesMiMa.txt",
                _acquire_silso_extrema,
            ),
            (
                "mwo-wso-polar-field-v2",
                "solar_cycle/polar_field/mwo_wso_v2/e_PField_MWO_WSO.csv",
                _acquire_polar_field,
            ),
            (
                "noaa-swpc-monthly-f107-v1",
                "solar_cycle/f107/noaa_swpc_monthly_v1/f10-7cm-flux.json",
                _acquire_noaa_monthly_f107,
            ),
            (
                "wso-current-polar-field-v1",
                "solar_cycle/polar_field/wso_current_v1/Polar.html",
                _acquire_wso_current_polar_field,
            ),
        )
        if isinstance(dataset_ids, str):
            raise TypeError("dataset_ids must be an iterable of dataset identifiers")
        if dataset_ids is not None:
            requested = set(dataset_ids)
            known = {dataset_id for dataset_id, _relative, _acquire in specifications}
            unknown = sorted(requested - known)
            if unknown:
                raise ValueError(
                    "unsupported authoritative solar dataset IDs: " + ", ".join(unknown)
                )
            specifications = tuple(
                specification
                for specification in specifications
                if specification[0] in requested
            )
        for dataset_id, relative, acquire in specifications:
            try:
                payload, provenance = acquire()
            except (OSError, ValueError, RuntimeError) as exc:
                raise RuntimeError(
                    "authoritative solar dataset "
                    f"{dataset_id} acquisition failed: {type(exc).__name__}"
                ) from exc
            temporary = temporary_root / Path(relative).name
            temporary.write_bytes(payload)
            records.append(
                register_project_data_file(
                    base_workspace,
                    temporary,
                    relative,
                    dataset_id=dataset_id,
                    provenance={**provenance, "retrieved_at": retrieved_at},
                    project_id=project_id,
                )
            )
    return records


__all__ = [
    "acquire_authoritative_solar_data",
]
