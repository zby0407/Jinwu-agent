"""Solar-activity overview assembled from the curated authoritative datasets.

This is the first dashboard-oriented reader for the existing solar data-source
catalog.  It deliberately uses the same authority URLs registered in
``jw.solar_data_catalog`` instead of scraping presentation websites.  Responses
are cached on disk so a failed upstream refresh never destroys the last valid
snapshot available to the desktop UI.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .paths import DATA_DIR
from .solar_data_catalog import describe_solar_datasets

_USER_AGENT = "Jinwu-solar-activity-overview/1.0"
_CACHE_TTL_SECONDS = 6 * 60 * 60
_FETCH_TIMEOUT_SECONDS = 20.0
_CACHE_DIR = DATA_DIR / "solar_activity_cache"

_MONTHLY_TOTAL_ID = "silso-monthly-total-v2"
_MONTHLY_SMOOTHED_ID = "silso-monthly-smoothed-v2"
_MONTHLY_F107_ID = "noaa-swpc-monthly-f107-v1"
_CYCLE_EXTREMA_ID = "silso-cycle-extrema-v2"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _dataset_metadata(dataset_id: str) -> dict[str, Any]:
    for dataset in describe_solar_datasets():
        if str(dataset["id"]) == dataset_id:
            return dataset
    raise KeyError(f"unknown curated solar dataset: {dataset_id}")


def _fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    last_error: OSError | None = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(
                request, timeout=_FETCH_TIMEOUT_SECONDS
            ) as response:
                return response.read()
        except OSError as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.5)
    assert last_error is not None
    raise last_error


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(payload)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write_bytes(
        path,
        (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
            "utf-8"
        ),
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _load_authoritative_source(
    dataset_id: str,
    parser: Callable[[bytes], Any],
    *,
    refresh: bool = False,
) -> tuple[Any, dict[str, Any]]:
    """Fetch one authoritative source with a validated disk cache.

    The parser runs before new bytes are cached.  If refresh fails but an older
    cached payload exists, the older data is returned with ``stale=True`` so the
    dashboard degrades honestly instead of losing the whole table.
    """

    metadata = _dataset_metadata(dataset_id)
    url = str(metadata["authority_url"])
    safe_name = dataset_id.replace("/", "-")
    raw_path = _CACHE_DIR / f"{safe_name}.raw"
    meta_path = _CACHE_DIR / f"{safe_name}.json"
    now = time.time()
    cached_meta = _read_json(meta_path)
    cached_at = float(cached_meta.get("fetched_epoch") or 0)
    cache_fresh = raw_path.is_file() and now - cached_at < _CACHE_TTL_SECONDS

    def parse_cached() -> tuple[Any, dict[str, Any]] | None:
        try:
            payload = raw_path.read_bytes()
            parsed = parser(payload)
        except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError):
            return None
        return parsed, {
            "id": dataset_id,
            "title_zh": metadata["title_zh"],
            "provider": metadata["provider"],
            "authority_url": url,
            "fetched_at": cached_meta.get("fetched_at"),
            "cache_state": "fresh" if cache_fresh else "stale",
            "stale": not cache_fresh,
            "license": _license_for(dataset_id),
        }

    if cache_fresh and not refresh:
        cached = parse_cached()
        if cached is not None:
            return cached

    try:
        payload = _fetch_bytes(url)
        parsed = parser(payload)
    except (
        OSError,
        TimeoutError,
        urllib.error.URLError,
        ValueError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
    ) as exc:
        cached = parse_cached()
        if cached is not None:
            parsed_value, source_meta = cached
            source_meta["refresh_error"] = type(exc).__name__
            source_meta["cache_state"] = "stale"
            source_meta["stale"] = True
            return parsed_value, source_meta
        raise RuntimeError(f"{dataset_id} upstream fetch or validation failed") from exc

    fetched_at = _utc_now()
    _atomic_write_bytes(raw_path, payload)
    _atomic_write_json(
        meta_path,
        {
            "dataset_id": dataset_id,
            "authority_url": url,
            "fetched_at": fetched_at,
            "fetched_epoch": now,
            "bytes": len(payload),
        },
    )
    return parsed, {
        "id": dataset_id,
        "title_zh": metadata["title_zh"],
        "provider": metadata["provider"],
        "authority_url": url,
        "fetched_at": fetched_at,
        "cache_state": "fresh",
        "stale": False,
        "license": _license_for(dataset_id),
    }


def _unavailable_source_meta(dataset_id: str, exc: RuntimeError) -> dict[str, Any]:
    metadata = _dataset_metadata(dataset_id)
    return {
        "id": dataset_id,
        "title_zh": metadata["title_zh"],
        "provider": metadata["provider"],
        "authority_url": str(metadata["authority_url"]),
        "fetched_at": None,
        "cache_state": "stale",
        "stale": True,
        "license": _license_for(dataset_id),
        "refresh_error": str(exc),
    }


def _load_optional_source(
    dataset_id: str,
    parser: Callable[[bytes], Any],
    empty_value: Any,
    *,
    refresh: bool = False,
) -> tuple[Any, dict[str, Any]]:
    try:
        return _load_authoritative_source(dataset_id, parser, refresh=refresh)
    except RuntimeError as exc:
        return empty_value, _unavailable_source_meta(dataset_id, exc)


def _license_for(dataset_id: str) -> str:
    if dataset_id.startswith("silso"):
        return "CC BY-NC 4.0"
    if dataset_id.startswith("noaa-swpc"):
        return "NOAA/NWS public data"
    return "见权威来源说明"


def parse_silso_monthly_total(payload: bytes) -> dict[str, dict[str, Any]]:
    """Parse ``SN_m_tot_V2.0.txt`` into month-keyed records."""

    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("SILSO monthly total is not ASCII") from exc
    rows: dict[str, dict[str, Any]] = {}
    for line_number, raw in enumerate(text.splitlines(), start=1):
        fields = raw.split()
        if not fields:
            continue
        if len(fields) not in {6, 7}:
            raise ValueError(f"invalid SILSO monthly row {line_number}")
        try:
            year = int(fields[0])
            month_number = int(fields[1])
            value = float(fields[3])
            stddev = float(fields[4])
            observations = int(fields[5])
        except (ValueError, IndexError) as exc:
            raise ValueError(f"invalid SILSO monthly row {line_number}") from exc
        if not 1 <= month_number <= 12:
            raise ValueError(f"invalid SILSO month at row {line_number}")
        month = f"{year:04d}-{month_number:02d}"
        rows[month] = {
            "month": month,
            "sunspot_number": None if value < 0 else value,
            "sunspot_stddev": None if stddev < 0 else stddev,
            "sunspot_observations": None if observations < 0 else observations,
            "sunspot_provisional": len(fields) == 7 and fields[6] == "*",
        }
    if len(rows) < 3_200:
        raise ValueError("SILSO monthly total coverage is unexpectedly short")
    return rows


def parse_silso_smoothed(payload: bytes) -> dict[str, dict[str, Any]]:
    """Parse ``SN_ms_tot_V2.0.csv`` into month-keyed records."""

    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("SILSO smoothed series is not ASCII") from exc
    rows: dict[str, dict[str, Any]] = {}
    for line_number, raw in enumerate(text.splitlines(), start=1):
        fields = [field.strip() for field in raw.split(";")]
        if not fields or not fields[0]:
            continue
        if len(fields) < 7:
            raise ValueError(f"invalid SILSO smoothed row {line_number}")
        try:
            year = int(fields[0])
            month_number = int(fields[1])
            value = float(fields[3])
            definitive = fields[6] == "1"
        except ValueError as exc:
            raise ValueError(f"invalid SILSO smoothed row {line_number}") from exc
        month = f"{year:04d}-{month_number:02d}"
        rows[month] = {
            "smoothed_sunspot_number": None if value < 0 else value,
            "smoothed_definitive": definitive,
        }
    if len(rows) < 3_100:
        raise ValueError("SILSO smoothed coverage is unexpectedly short")
    return rows


def parse_noaa_monthly_f107(payload: bytes) -> dict[str, dict[str, Any]]:
    """Parse NOAA SWPC monthly F10.7 JSON into month-keyed records."""

    decoded = json.loads(payload)
    if not isinstance(decoded, list) or not decoded:
        raise ValueError("NOAA monthly F10.7 payload must be a non-empty array")
    rows: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(decoded):
        if not isinstance(item, dict):
            raise ValueError(f"invalid NOAA F10.7 row {index}")
        month = item.get("time-tag")
        value = item.get("f10.7")
        if not isinstance(month, str) or len(month) != 7:
            raise ValueError(f"invalid NOAA F10.7 month at row {index}")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"invalid NOAA F10.7 value at row {index}")
        rows[month] = {"f107": None if float(value) < 0 else float(value)}
    return rows


def parse_silso_cycle_extrema(payload: bytes) -> list[dict[str, Any]]:
    """Parse SILSO's official cycle minima/maxima table."""

    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("SILSO extrema table is not ASCII") from exc
    cycles: list[dict[str, Any]] = []
    for line_number, raw in enumerate(text.splitlines(), start=1):
        fields = raw.split()
        if not fields or not fields[0].isdigit():
            continue
        if len(fields) not in {4, 9}:
            raise ValueError(f"invalid SILSO extrema row {line_number}")
        try:
            cycle = int(fields[0])
            minimum = {
                "year": int(fields[1]),
                "month": int(fields[2]),
                "label": f"{int(fields[1]):04d}-{int(fields[2]):02d}",
                "sunspot_number": float(fields[3]),
            }
            completed = len(fields) == 9
            maximum = None
            duration = None
            if completed:
                maximum = {
                    "year": int(fields[4]),
                    "month": int(fields[5]),
                    "label": f"{int(fields[4]):04d}-{int(fields[5]):02d}",
                    "sunspot_number": float(fields[6]),
                }
                duration = {
                    "years": int(fields[7]),
                    "months": int(fields[8]),
                    "label": f"{int(fields[7])} 年 {int(fields[8])} 个月",
                }
        except (ValueError, IndexError) as exc:
            raise ValueError(f"invalid SILSO extrema row {line_number}") from exc
        cycles.append(
            {
                "cycle": cycle,
                "minimum": minimum,
                "maximum": maximum,
                "duration": duration,
                "completed": completed,
            }
        )
    if len(cycles) < 24:
        raise ValueError("SILSO extrema table is unexpectedly short")
    return cycles


def _latest_valid(rows: list[dict[str, Any]], field: str) -> dict[str, Any] | None:
    for row in reversed(rows):
        if row.get(field) is not None:
            return {"month": row["month"], "value": row[field]}
    return None


def build_solar_activity_overview(
    *,
    months: int = 24,
    cycle_count: int = 6,
    refresh: bool = False,
) -> dict[str, Any]:
    """Build the first table-oriented solar-activity overview."""

    monthly_total, monthly_total_meta = _load_authoritative_source(
        _MONTHLY_TOTAL_ID, parse_silso_monthly_total, refresh=refresh
    )
    smoothed, smoothed_meta = _load_optional_source(
        _MONTHLY_SMOOTHED_ID, parse_silso_smoothed, {}, refresh=refresh
    )
    f107, f107_meta = _load_optional_source(
        _MONTHLY_F107_ID, parse_noaa_monthly_f107, {}, refresh=refresh
    )
    extrema, extrema_meta = _load_optional_source(
        _CYCLE_EXTREMA_ID, parse_silso_cycle_extrema, [], refresh=refresh
    )

    month_keys = sorted(set(monthly_total) | set(smoothed) | set(f107))
    merged: list[dict[str, Any]] = []
    for month in month_keys:
        row = {"month": month}
        row.update(monthly_total.get(month, {}))
        row.update(smoothed.get(month, {}))
        row.update(f107.get(month, {}))
        row["provisional"] = bool(row.get("sunspot_provisional"))
        merged.append(row)
    visible_rows = merged[-max(1, months) :]

    latest_sunspot = _latest_valid(merged, "sunspot_number")
    latest_smoothed = _latest_valid(merged, "smoothed_sunspot_number")
    latest_f107 = _latest_valid(merged, "f107")
    current_cycle = extrema[-1] if extrema else None

    return {
        "status": "ok",
        "generated_at": _utc_now(),
        "summary": {
            "latest_sunspot": latest_sunspot,
            "latest_smoothed_sunspot": latest_smoothed,
            "latest_f107": latest_f107,
            "current_cycle": current_cycle,
            "coverage_start": month_keys[0] if month_keys else None,
            "coverage_end": month_keys[-1] if month_keys else None,
        },
        "monthly_rows": visible_rows,
        "cycles": extrema[-max(1, cycle_count) :],
        "sources": [monthly_total_meta, smoothed_meta, f107_meta, extrema_meta],
        "capabilities": [
            {
                "feature": "月度黑子数与 F10.7 表",
                "state": "available",
                "note": "已由当前内置 SILSO 与 NOAA SWPC 数据源直接支持",
            },
            {
                "feature": "太阳活动周极值",
                "state": "available",
                "note": "已由 SILSO 活动周极值表直接支持",
            },
            {
                "feature": "C/M/X 耀斑频率",
                "state": "missing",
                "note": "当前内置目录尚未接入 NOAA GOES Flare Report",
            },
            {
                "feature": "蝴蝶图",
                "state": "missing",
                "note": "当前内置目录尚未接入黑子群纬度数据库",
            },
            {
                "feature": "地磁暴天数",
                "state": "missing",
                "note": "当前内置目录尚未接入 GFZ Kp 指数",
            },
        ],
    }


__all__ = [
    "build_solar_activity_overview",
    "parse_noaa_monthly_f107",
    "parse_silso_cycle_extrema",
    "parse_silso_monthly_total",
    "parse_silso_smoothed",
]
