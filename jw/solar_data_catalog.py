"""Curated acquisition of authoritative solar-cycle reference datasets."""

from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .workspaces import register_project_data_file

_USER_AGENT = "Jinwu-research-data/2.0"
_SILSO_AUTHORITY_URL = "https://www.sidc.be/SILSO/DATA/SN_m_tot_V2.0.txt"
_SILSO_SMOOTHED_URL = "https://www.sidc.be/SILSO/DATA/SN_ms_tot_V2.0.csv"
_SILSO_EXTREMA_URL = "https://www.sidc.be/SILSO/DATA/Cycles/TableCyclesMiMa.txt"
_SILSO_MIRROR_URL = (
    "http://www.wdcb.ru/stp/data/solar.act/sunspot/SILSO/ver2/SN_m/SN_m_tot_V2.0.txt"
)
_SILSO_DOI = "https://doi.org/10.24414/qnza-ac80"
_POLAR_PERSISTENT_ID = "doi:10.7910/DVN/KF96B2"
_POLAR_FILENAME = "e_PField_MWO_WSO.csv"


def _fetch(url: str, *, timeout: float = 20.0) -> tuple[bytes, str]:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(), response.geturl()


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


def _acquire_silso() -> tuple[bytes, dict[str, Any]]:
    attempts: list[dict[str, str]] = []
    for source_kind, url in (
        ("authority", _SILSO_AUTHORITY_URL),
        ("world_data_center_mirror", _SILSO_MIRROR_URL),
    ):
        try:
            payload, resolved = _fetch(url)
            validation = _validate_silso_monthly(payload)
            return payload, {
                "authority_url": _SILSO_AUTHORITY_URL,
                "retrieval_url": resolved,
                "retrieval_source_kind": source_kind,
                "dataset_doi": _SILSO_DOI,
                "license": "CC BY-NC 4.0",
                "validation": validation,
                "failed_prior_attempts": attempts,
            }
        except Exception as exc:
            attempts.append(
                {
                    "source_kind": source_kind,
                    "url": url,
                    "error_type": type(exc).__name__,
                }
            )
    raise RuntimeError(f"all curated SILSO sources failed: {attempts}")


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
    metadata_bytes, resolved_metadata_url = _fetch(metadata_url)
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
    payload, resolved_data_url = _fetch(data_url)
    checksum = selected.get("checksum", {})
    if checksum.get("type") != "MD5" or not isinstance(checksum.get("value"), str):
        raise RuntimeError("Dataverse polar-field file has no MD5 receipt")
    if hashlib.md5(payload, usedforsecurity=False).hexdigest() != checksum["value"]:
        raise RuntimeError("Dataverse polar-field upstream checksum mismatch")
    return payload, {
        "authority_url": "https://doi.org/10.7910/DVN/KF96B2",
        "metadata_url": resolved_metadata_url,
        "retrieval_url": resolved_data_url,
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


def acquire_authoritative_solar_data(
    base_workspace: str | Path, *, project_id: str = "default"
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
        )
        acquired = []
        for dataset_id, relative, acquire in specifications:
            payload, provenance = acquire()
            temporary = temporary_root / Path(relative).name
            temporary.write_bytes(payload)
            acquired.append((dataset_id, relative, temporary, provenance))
        for dataset_id, relative, temporary, provenance in acquired:
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
