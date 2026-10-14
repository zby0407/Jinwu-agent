"""Acquire official small keyword/catalog products and prepare a fixed daily backtest.

This tool never fits a model, downloads image segments, or produces figures.
Definitive SHARP processing dates do not establish operational availability.
"""

from __future__ import annotations

import argparse
import bisect
import calendar
import csv
import hashlib
import json
import math
import platform
import shutil
import sys
import traceback
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import netCDF4

UTC = timezone.utc
JSOC_URL = "http://jsoc.stanford.edu/cgi-bin/ajax/jsoc_info"
GOES_ROOT = (
    "https://data.ngdc.noaa.gov/platforms/solar-space-observing-satellites/"
    "goes/multi/l2/data/xrsf-l2-flrpt_science/csv/"
)
METRICS = [
    "USFLUX",
    "TOTUSJH",
    "TOTUSJZ",
    "ABSNJZH",
    "SAVNCPP",
    "MEANALP",
    "R_VALUE",
    "TOTPOT",
    "MEANSHR",
    "AREA_ACR",
]
KEYWORDS = [
    "HARPNUM",
    "T_REC",
    "T_OBS",
    "DATE",
    "QUALITY",
    "NOAA_AR",
    "NOAA_NUM",
    "NOAA_ARS",
    "LON_FWT",
    "LAT_FWT",
    "CRLN_OBS",
    "CRVAL1",
    "CRVAL2",
    "OBS_VR",
    *METRICS,
]
DATA_RULES = {
    "target_scope": "full_disk_catalogued_new_event",
    "first_issue_utc": "2011-01-01T00:00:00Z",
    "last_issue_utc": "2016-12-31T00:00:00Z",
    "issue_cadence_hours": 24,
    "sharp_series": "hmi.sharp_cea_720s",
    "sharp_slot": "previous_calendar_day_23:00:00_TAI",
    "support_half_width_seconds": 675,
    "support_end_comparison": "strictly_before_issue",
    "quality_equals": 0,
    "cmd_field": "LON_FWT",
    "cmd_coordinate_system": "Stonyhurst",
    "cmd_absolute_limit_degrees": 60,
    "magnetic_metrics": METRICS,
    "magnetic_aggregations": ["max", "median"],
    "all_magnetic_metrics_finite_required": True,
    "duplicate_HARP_slot_policy": "error",
    "missing_day_policy": "exclude_no_zero_fill",
    "goes_catalog_years": list(range(2010, 2018)),
    "goes_catalog_version": "v1-0-1",
    "future_window": "[issue,issue+24h)",
    "future_event_time": "start_time",
    "c1_threshold_w_m2": 1e-6,
    "m1_threshold_w_m2": 1e-5,
    "history_windows_hours": [24, 72, 168],
    "history_event_time": "start_time",
    "history_latest_end_lag_hours": 3,
    "history_class_bins": "nested C+:>=1e-6;M+:>=1e-5;X+:>=1e-4",
    "invalid_event_policy": "audit_no_imputation",
    "coverage_policy": "legacy_GOES13_15_union_minutes_future24h_past168h_fraction>=.99_maxgap<=5",
    "invalid_start_quarantine": "peak_date_and_adjacent_UTC_days; no inferred start; unlocalizable audit remains global limitation",
    "interpretation": "research_observation_time_backtest_not_operational_prediction",
}
FEATURES = [f"{m}_{a}" for m in METRICS for a in ["max", "median"]] + ["patch_count"]
FEATURES += [
    f"history_{h}h_{name}"
    for h in [24, 72, 168]
    for name in ["c_count", "m_count", "x_count", "max_peak_w_m2"]
]
MAX_BYTES = 1_500_000_000
PRIOR_REJECTED_ARCHIVE_BYTES = 112_721_920
POLICY_URL = "http://jsoc.stanford.edu/JSOC_Data_Policy.html"
SCIENCE_MONTHS = {
    "13": {"2013": [6, 8, 9, 10, 11, 12], "2014": [1, 2, 3, 4, 5, 8, 9, 10, 11, 12]},
    "14": {"2012": [10, 11]},
    "15": {"2010": [12], **{str(y): list(range(1, 13)) for y in range(2011, 2015)}},
}


def validate_source_amendment(amendment: dict, contract_hash: str) -> None:
    if not (
        amendment.get("amends_contract_sha256") == contract_hash
        and amendment.get("frozen_before_real_model") is True
        and amendment.get("version") == "v2-2-1"
        and amendment.get("allowed_science_months") == SCIENCE_MONTHS
        and amendment.get("allowed_science_date_bounds") == ["2010-12-01", "2014-12-31"]
        and amendment.get("active_source_amendment")
        == "protocol_amendment_03_science_coverage.json"
    ):
        raise ValueError(
            "Active source amendment does not match fixed official science scope"
        )


def science_crop_minutes(minutes: set[int], satellite: int, year: int) -> set[int]:
    """Ignore all annual records outside the registered published source months."""
    months = SCIENCE_MONTHS.get(str(satellite), {}).get(str(year), [])
    allowed = []
    for month in months:
        start = datetime(year, month, 1, tzinfo=UTC)
        end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=UTC)
        allowed.append((int(start.timestamp()) // 60, int(end.timestamp()) // 60))
    return {minute for minute in minutes if any(a <= minute < b for a, b in allowed)}


class ByteBudget:
    def __init__(self, maximum: int, prior: int = PRIOR_REJECTED_ARCHIVE_BYTES):
        self.maximum, self.used = maximum, prior
        self.lock = threading.Lock()

    def add(self, count: int) -> None:
        with self.lock:
            if self.used + count > self.maximum:
                raise ValueError("Cumulative acquisition byte cap exceeded")
            self.used += count


class EventIndex:
    def __init__(self, events: list[dict]):
        self.events = sorted(events, key=lambda row: row["start"])
        self.starts = [row["start"] for row in self.events]

    def window(self, start: datetime, end: datetime) -> list[dict]:
        return self.events[
            bisect.bisect_left(self.starts, start) : bisect.bisect_left(
                self.starts, end
            )
        ]


class CoverageIndex:
    def __init__(self, minutes: set[int]):
        self.first = int(datetime(2010, 12, 1, tzinfo=UTC).timestamp()) // 60
        self.end = int(datetime(2017, 1, 1, tzinfo=UTC).timestamp()) // 60
        valid = np.zeros(self.end - self.first, dtype=bool)
        offsets = np.fromiter(
            (x - self.first for x in minutes if self.first <= x < self.end),
            dtype=np.int64,
        )
        valid[offsets] = True
        self.prefix = np.concatenate(([0], np.cumsum(valid, dtype=np.int64)))
        delta = np.diff(np.concatenate(([False], ~valid, [False])).astype(np.int8))
        self.gap_starts = np.flatnonzero(delta == 1)
        self.gap_ends = np.flatnonzero(delta == -1)

    def stats(self, issue: datetime, hours: int, past: bool) -> tuple[float, int]:
        first = int(issue.timestamp()) // 60 - (hours * 60 if past else 0)
        start, end = first - self.first, first - self.first + hours * 60
        expected = hours * 60
        lower, upper = max(0, start), min(end, self.end - self.first)
        if lower >= upper:
            return 0.0, expected
        count = int(self.prefix[upper] - self.prefix[lower])
        a = int(np.searchsorted(self.gap_ends, lower, side="right"))
        b = int(np.searchsorted(self.gap_starts, upper, side="left"))
        lengths = np.minimum(self.gap_ends[a:b], upper) - np.maximum(
            self.gap_starts[a:b], lower
        )
        longest = int(lengths.max()) if len(lengths) else 0
        longest = max(longest, max(0, -start), max(0, end - (self.end - self.first)))
        return count / expected, longest


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_utc(value: object, catalog_naive_is_utc: bool = False) -> datetime | None:
    if value is None or str(value).strip() in {"", "MISSING", "nan", "NaT"}:
        return None
    try:
        result = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        if result.tzinfo is None:
            if not catalog_naive_is_utc:
                return None
            result = result.replace(tzinfo=UTC)
        return result.astimezone(UTC)
    except ValueError:
        return None


def tai_to_utc(value: object) -> datetime:
    """Convert supported 2010--2017 TAI timestamps using registered leap boundaries."""
    text = str(value)
    if not text.endswith("_TAI"):
        raise ValueError("Expected explicitly tagged TAI timestamp")
    tai_calendar = datetime.strptime(text[:-4], "%Y.%m.%d_%H:%M:%S").replace(tzinfo=UTC)
    if (
        not datetime(2010, 1, 1, tzinfo=UTC)
        <= tai_calendar
        < datetime(2018, 1, 1, tzinfo=UTC)
    ):
        raise ValueError("TAI conversion outside registered 2010--2017 range")
    # The effective boundaries are expressed in TAI calendar coordinates.
    offset = 34
    for boundary, seconds in [
        (datetime(2012, 7, 1, 0, 0, 35, tzinfo=UTC), 35),
        (datetime(2015, 7, 1, 0, 0, 36, tzinfo=UTC), 36),
        (datetime(2017, 1, 1, 0, 0, 37, tzinfo=UTC), 37),
    ]:
        if boundary - timedelta(seconds=1) <= tai_calendar < boundary:
            raise ValueError(
                "UTC leap-second instant cannot be represented by datetime; outside registered 23 TAI slots"
            )
        if tai_calendar >= boundary:
            offset = seconds
    return tai_calendar - timedelta(seconds=offset)


def slot_issue(value: object) -> datetime:
    slot = datetime.strptime(str(value), "%Y.%m.%d_%H:%M:%S_TAI").replace(tzinfo=UTC)
    if (slot.hour, slot.minute, slot.second) != (23, 0, 0):
        raise ValueError("Unexpected SHARP slot: exact previous-day 23 TAI required")
    return (slot + timedelta(days=1)).replace(hour=0)


def finite_float(value: object) -> float | None:
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    return number if math.isfinite(number) else None


def parse_quality(value: object) -> int | None:
    try:
        return (
            int(str(value), 0)
            if str(value).lower().startswith("0x")
            else int(str(value))
        )
    except (TypeError, ValueError):
        return None


def month_keys() -> list[str]:
    return [
        f"{year}-{month:02d}" for year in range(2011, 2017) for month in range(1, 13)
    ]


def query_for_month(month: str) -> dict[str, str]:
    if month not in month_keys():
        raise ValueError("Month outside registered 2011--2016 acquisition")
    year, number = map(int, month.split("-"))
    start = datetime(year, number, 1) - timedelta(days=1)
    days = calendar.monthrange(year, number)[1]
    ds = f"hmi.sharp_cea_720s[][{start:%Y.%m.%d}_23:00:00_TAI/{days}d@24h]"
    return {"op": "rs_list", "ds": ds, "key": ",".join(KEYWORDS), "n": "0"}


def coverage_month_keys() -> list[str]:
    return ["2010-12", *month_keys()]


def coverage_url(month: str, satellite: int) -> str:
    if month not in coverage_month_keys() or satellite not in {13, 15}:
        raise ValueError("Coverage source outside registered list")
    year, number = map(int, month.split("-"))
    last = calendar.monthrange(year, number)[1]
    return (
        "https://www.ncei.noaa.gov/data/goes-space-environment-monitor/access/avg/"
        f"{year}/{number:02d}/goes{satellite}/csv/g{satellite}_xrs_1m_"
        f"{year}{number:02d}01_{year}{number:02d}{last:02d}.csv"
    )


def validate_contract(contract: dict, amendment: dict, contract_hash: str) -> None:
    schema = contract.get("feature_schema", {})
    schedule = contract.get("schedule", {})
    checks = [
        contract.get("forecast_mode") == "research_backtest",
        contract.get("spatial_unit") == "full_disk",
        schedule.get("start") == "2011-01-01",
        schedule.get("end") == "2016-12-31",
        schedule.get("cadence") == "daily 00:00 UTC",
        schedule.get("outcome_irradiance_w_m2") == {"C1.0+": 1e-6, "M1.0+": 1e-5},
        schema.get("magnetic_metrics") == METRICS,
        schema.get("aggregation") == ["max", "median"],
        schema.get("history_hours") == [24, 72, 168],
        schema.get("history_counts") == ["C+", "M+", "X+"],
        schema.get("expected_feature_count") == 33,
        schema.get("patch_count") is True,
        schema.get("history_max_peak") is True,
        "T_OBS + 675 s < issue_time"
        in contract.get("availability", {}).get("HMI_support", ""),
        "end_time <= issue_time - 3 hours"
        in contract.get("availability", {}).get("GOES_history", ""),
        "abs(LON_FWT Stonyhurst longitude)<=60deg"
        in contract.get("quality", {}).get("HMI", ""),
        amendment.get("amends_contract_sha256") == contract_hash,
        amendment.get("frozen_before_real_model") is True,
        amendment.get("qualification")
        == "In each window valid fraction >= 0.99 AND longest missing minute run <=5; else exclude/unknown, never class0.",
    ]
    if not all(checks):
        raise ValueError(
            "Frozen contract/amendment fields mismatch the registered preparation rules"
        )


def rows_from_jsoc(payload: dict) -> list[dict]:
    if payload.get("status") != 0:
        raise ValueError("JSOC status is not successful")
    count = payload.get("count")
    if not isinstance(count, int) or count < 0:
        raise ValueError("Invalid JSOC row count")
    columns = {item["name"]: item["values"] for item in payload.get("keywords", [])}
    if set(KEYWORDS) - set(columns):
        raise ValueError("Missing requested JSOC keyword column")
    if any(len(values) != count for values in columns.values()):
        raise ValueError("JSOC keyword array lengths disagree with count")
    rows = [{key: columns[key][i] for key in KEYWORDS} for i in range(count)]
    keys = [(r["HARPNUM"], r["T_REC"]) for r in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate HARP/slot; refusing arbitrary selection")
    return rows


def validate_schema(payload: dict) -> dict:
    keywords = {row["name"]: row for row in payload.get("keywords", [])}
    if set(KEYWORDS) - set(keywords):
        raise ValueError("Schema lacks required keywords")
    if "Stonyhurst longitude" not in keywords["LON_FWT"].get("note", ""):
        raise ValueError(
            "CMD coordinate definition did not match registered Stonyhurst rule"
        )
    return {name: keywords[name] for name in KEYWORDS}


def source_record(
    path: Path, root: Path, role: str, url: str, params: dict, receipt: dict
) -> dict:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": sha256(path),
        "bytes": path.stat().st_size,
        "role": role,
        "url": url,
        "parameters": params,
        "receipt": receipt,
        "version": "v1-0-1"
        if role.startswith("goes")
        else "definitive_archive_keywords_at_retrieval",
        "license_or_terms": (
            "NOAA public data; catalog metadata permits redistribution/use without restriction"
            if role.startswith("goes")
            else "SDO/HMI observation data CC0-1.0 under archived official JSOC Data Policy; policy document license not inferred"
        ),
        "terms_url": "https://www.ncei.noaa.gov/products/goes-1-15/space-weather-instruments"
        if role.startswith("goes")
        else POLICY_URL,
        "citation_urls": (
            ["https://www.ncei.noaa.gov/products/goes-1-15/space-weather-instruments"]
            if role.startswith("goes")
            else ["https://arxiv.org/abs/1404.1879", "https://arxiv.org/abs/1404.1881"]
        ),
    }


def fetch_one(
    root: Path,
    filename: str,
    role: str,
    url: str,
    params: dict,
    cap: int,
    seed: Path | None = None,
    budget: ByteBudget | None = None,
) -> dict:
    path = root / filename
    if seed is not None:
        source_manifest = json.loads(
            (seed / "source_manifest.json").read_text(encoding="utf-8")
        )
        matches = [f for f in source_manifest["files"] if f["path"] == filename]
        if matches:
            old = matches[0]
            original = seed / filename
            if (
                sha256(original) != old["sha256"]
                or old["parameters"] != params
                or old["url"] != url
            ):
                raise ValueError("Seed provenance mismatch")
            shutil.copyfile(original, path)
            if budget:
                budget.add(path.stat().st_size)
            return {
                **old,
                "seed_copy": {
                    "source_path": str(original),
                    "source_sha256": old["sha256"],
                },
            }
    response = None
    partial = root / (filename + ".failed_partial")
    started = utc_text(datetime.now(UTC))
    try:
        response = requests.get(
            url, params=params or None, stream=True, timeout=(20, 120)
        )
        response.raise_for_status()
        downloaded = 0
        with partial.open("xb") as stream:
            for block in response.iter_content(65536):
                downloaded += len(block)
                if downloaded > cap:
                    raise ValueError("Acquisition byte cap exceeded")
                if budget:
                    budget.add(len(block))
                stream.write(block)
        partial.rename(path)
        receipt = {
            "retrieved_utc": utc_text(datetime.now(UTC)),
            "started_utc": started,
            "status_code": response.status_code,
            "final_url": response.url,
            "content_type": response.headers.get("Content-Type"),
            "last_modified": response.headers.get("Last-Modified"),
            "etag": response.headers.get("ETag"),
            "attempt": 1,
        }
        write_json(root / (filename + ".receipt.json"), receipt)
        return source_record(path, root, role, url, params, receipt)
    except Exception as exc:
        write_json(
            root / (filename + ".failure.json"),
            {
                "started_utc": started,
                "failed_utc": utc_text(datetime.now(UTC)),
                "url": url,
                "parameters": params,
                "exception_type": type(exc).__name__,
                "message": str(exc),
                "status_code": response.status_code if response is not None else None,
                "partial_bytes": partial.stat().st_size if partial.exists() else 0,
                "automatic_retries": 0,
            },
        )
        raise
    finally:
        if response is not None:
            response.close()


def acquire(args: argparse.Namespace) -> None:
    out = Path(args.output_dir).resolve()
    if out.exists():
        raise ValueError("Output already exists; acquisition refuses overwrite")
    out.mkdir(parents=True)
    months = args.months.split(",") if args.months else month_keys()
    if len(months) != len(set(months)) or set(months) - set(month_keys()):
        raise ValueError("Invalid or repeated requested months")
    seed = Path(args.seed_dir).resolve() if args.seed_dir else None
    coverage_months = (
        []
        if args.skip_coverage
        else args.coverage_months.split(",")
        if args.coverage_months
        else coverage_month_keys()
    )
    if set(coverage_months) - set(coverage_month_keys()) or len(
        set(coverage_months)
    ) != len(coverage_months):
        raise ValueError("Invalid coverage month list")
    budget = ByteBudget(MAX_BYTES)
    manifest = {
        "schema_version": "official-flare-forecast-sources-v1",
        "data_rules": DATA_RULES,
        "created_utc": utc_text(datetime.now(UTC)),
        "acquisition_complete": False,
        "requested_months": months,
        "goes_years": list(range(2010, 2018)),
        "coverage_months": coverage_months,
        "coverage_satellites": [13, 15],
        "max_source_bytes": MAX_BYTES,
        "prior_rejected_archive_bytes": PRIOR_REJECTED_ARCHIVE_BYTES,
        "files": [],
        "failures": [],
        "workers": 4,
        "code_sha256": sha256(Path(__file__)),
    }
    path = out / "source_manifest.json"
    jobs = [
        (
            "sharp_schema.json",
            "sharp_schema",
            JSOC_URL,
            {"op": "series_struct", "ds": DATA_RULES["sharp_series"]},
        ),
        ("jsoc_data_policy.html", "sharp_data_policy", POLICY_URL, {}),
        (
            "goes_metadata.json",
            "goes_metadata",
            GOES_ROOT + "sci_xrsf-l2-flrpt_geo_metadata.json",
            {},
        ),
    ]
    jobs += [
        (
            f"goes_{year}_v1-0-1.csv",
            "goes_events",
            GOES_ROOT + f"sci_xrsf-l2-flrpt_geo_y{year}_v1-0-1.csv",
            {},
        )
        for year in range(2010, 2018)
    ]
    jobs += [
        (
            f"sharp_{month.replace('-', '_')}.json",
            "sharp_month",
            JSOC_URL,
            query_for_month(month),
        )
        for month in months
    ]
    jobs += [
        (
            f"coverage_g{satellite}_{month.replace('-', '_')}.csv",
            "goes_legacy_coverage",
            coverage_url(month, satellite),
            {},
        )
        for month in coverage_months
        for satellite in [13, 15]
    ]
    write_json(path, manifest)
    with ThreadPoolExecutor(max_workers=4) as executor:
        pending = {
            executor.submit(
                fetch_one, out, filename, role, url, params, 16_000_000, seed, budget
            ): (filename, role, url, params)
            for filename, role, url, params in jobs
        }
        for future in as_completed(pending):
            filename, role, url, params = pending[future]
            try:
                item = future.result()
                if role == "sharp_schema":
                    validate_schema(
                        json.loads((out / filename).read_text(encoding="utf-8"))
                    )
                elif role == "sharp_month":
                    rows = rows_from_jsoc(
                        json.loads((out / filename).read_text(encoding="utf-8"))
                    )
                    item["record_count"] = len(rows)
                    item["unique_slots"] = len({r["T_REC"] for r in rows})
                elif role == "goes_events":
                    with (out / filename).open(encoding="utf-8", newline="") as stream:
                        reader = csv.DictReader(stream)
                        if {"time", "start_time", "end_time", "xrsb_irrad"} - set(
                            reader.fieldnames or []
                        ):
                            raise ValueError(
                                "Annual GOES source required columns absent"
                            )
                        item["record_count"] = sum(1 for _ in reader)
                    if item["record_count"] == 0:
                        raise ValueError(
                            "Empty annual event catalog is not a negative label source"
                        )
                elif role == "sharp_data_policy":
                    policy_text = (out / filename).read_text(
                        encoding="utf-8", errors="replace"
                    )
                    item["CC0_text_present_in_policy"] = "CC0" in policy_text
                manifest["files"].append(item)
                print(
                    json.dumps(
                        {
                            "acquired": filename,
                            "bytes": item["bytes"],
                            "budget_bytes": budget.used,
                        }
                    ),
                    flush=True,
                )
            except Exception as exc:
                failure = {
                    "filename": filename,
                    "role": role,
                    "url": url,
                    "parameters": params,
                    "exception_type": type(exc).__name__,
                    "message": str(exc),
                }
                manifest["failures"].append(failure)
                print(
                    json.dumps(
                        {"failed": filename, "exception_type": type(exc).__name__}
                    ),
                    flush=True,
                )
            write_json(path, manifest)
    core_failures = [
        r for r in manifest["failures"] if r["role"] != "goes_legacy_coverage"
    ]
    manifest["acquisition_complete"] = not core_failures
    manifest["total_source_bytes"] = sum(f["bytes"] for f in manifest["files"])
    manifest["finished_utc"] = utc_text(datetime.now(UTC))
    manifest["actual_download_budget_bytes_including_prior"] = budget.used
    write_json(path, manifest)
    print(
        json.dumps(
            {
                "status": "acquired" if not core_failures else "incomplete",
                "manifest_sha256": sha256(path),
                "directory": str(out),
            }
        ),
        flush=True,
    )
    if core_failures:
        raise ValueError(
            "Required core source acquisition failed; all other job outputs preserved"
        )


def normalize_events(raw_rows: list[dict]) -> tuple[list[dict], list[dict]]:
    valid, audit, seen = [], [], set()
    for ordinal, row in enumerate(raw_rows):
        # Official flare-report metadata explicitly identifies its CSV calendar times as UTC.
        start = parse_utc(row.get("start_time"), catalog_naive_is_utc=True)
        end = parse_utc(row.get("end_time"), catalog_naive_is_utc=True)
        peak_time = parse_utc(row.get("time"), catalog_naive_is_utc=True)
        peak = finite_float(row.get("xrsb_irrad"))
        reasons = []
        if start is None:
            reasons.append("missing_or_invalid_start_time")
        if peak is None or peak <= 0:
            reasons.append("missing_or_nonpositive_peak_flux")
        if end is not None and start is not None and end < start:
            reasons.append("end_before_start")
        identity = str(row.get("flare_id", ""))
        key = (identity, str(row.get("start_time")), str(row.get("time")))
        if key in seen:
            reasons.append("duplicate_catalog_event")
        seen.add(key)
        common = {
            "row_ordinal": ordinal,
            "flare_id": identity,
            "raw_start_time": row.get("start_time", ""),
            "raw_end_time": row.get("end_time", ""),
            "raw_peak_time": row.get("time", ""),
            "raw_peak_flux": row.get("xrsb_irrad", ""),
            "flare_class": row.get("flare_class", ""),
            "active_region": row.get("active_region", ""),
            "peak_saturated": row.get("peak_saturated", ""),
            "source_file": row.get("source_file", ""),
        }
        if reasons:
            audit.append({**common, "reasons": "|".join(reasons)})
            continue
        valid.append(
            {**common, "start": start, "end": end, "peak_time": peak_time, "peak": peak}
        )
        if end is None:
            audit.append({**common, "reasons": "end_time_missing_history_ineligible"})
    return valid, audit


def label_and_history(
    issue: datetime, events: list[dict] | EventIndex, event_audit: list[dict]
) -> dict:
    future = (
        events.window(issue, issue + timedelta(hours=24))
        if isinstance(events, EventIndex)
        else [e for e in events if issue <= e["start"] < issue + timedelta(hours=24)]
    )
    future_peak = max((e["peak"] for e in future), default=0.0)
    bad_flux = [
        e
        for e in event_audit
        if "missing_or_nonpositive_peak_flux" in e["reasons"]
        and (start := parse_utc(e["raw_start_time"], catalog_naive_is_utc=True))
        is not None
        and issue <= start < issue + timedelta(hours=24)
    ]
    result = {
        "label_c1": int(future_peak >= 1e-6),
        "label_m1": int(future_peak >= 1e-5),
        "y_class": 2 if future_peak >= 1e-5 else 1 if future_peak >= 1e-6 else 0,
        "catalog_target_max_peak_w_m2": future_peak,
        "future_valid_event_count": len(future),
        "future_bad_flux_count": len(bad_flux),
        "future_label_qualified": not bad_flux,
        "continuous_XRS_coverage_certified": False,
    }
    used_past = []
    for hours in [24, 72, 168]:
        candidates = (
            events.window(issue - timedelta(hours=hours), issue)
            if isinstance(events, EventIndex)
            else [
                e
                for e in events
                if issue - timedelta(hours=hours) <= e["start"] < issue
            ]
        )
        past = [
            e
            for e in candidates
            if e["end"] is not None and e["end"] <= issue - timedelta(hours=3)
        ]
        result[f"history_{hours}h_c_count"] = sum(e["peak"] >= 1e-6 for e in past)
        result[f"history_{hours}h_m_count"] = sum(e["peak"] >= 1e-5 for e in past)
        result[f"history_{hours}h_x_count"] = sum(e["peak"] >= 1e-4 for e in past)
        result[f"history_{hours}h_max_peak_w_m2"] = max(
            (e["peak"] for e in past), default=0.0
        )
        used_past += past
    result["history_latest_event_end_utc"] = (
        utc_text(max(e["end"] for e in used_past)) if used_past else ""
    )
    return result


def coverage_valid_minutes(path: Path) -> set[int]:
    """Use legacy irradiance solely to qualify observation coverage, never magnitude."""
    lines = path.read_text(encoding="utf-8").splitlines()
    starts = [
        i for i, line in enumerate(lines) if line.startswith("time_tag,A_QUAL_FLAG,")
    ]
    if len(starts) != 1:
        raise ValueError("Legacy coverage data header missing or duplicated")
    if not any("UTC" in line for line in lines[: starts[0]]):
        raise ValueError("Legacy coverage metadata must explicitly confirm UTC")
    reader = csv.DictReader(lines[starts[0] :])
    if {"time_tag", "B_QUAL_FLAG", "B_NUM_PTS", "B_AVG"} - set(reader.fieldnames or []):
        raise ValueError("Coverage columns missing")
    good, seen = set(), set()
    for row in reader:
        try:
            time = datetime.fromisoformat(row["time_tag"]).replace(tzinfo=UTC)
        except ValueError:
            raise ValueError("Unparseable UTC coverage time") from None
        if time.second or time.microsecond:
            raise ValueError("Coverage timestamp not on registered minute grid")
        minute = int(time.timestamp()) // 60
        if minute in seen:
            raise ValueError("Duplicate minute within one satellite source")
        seen.add(minute)
        points, flux = finite_float(row["B_NUM_PTS"]), finite_float(row["B_AVG"])
        if (
            parse_quality(row["B_QUAL_FLAG"]) == 0
            and points is not None
            and points > 0
            and flux is not None
            and flux > 0
        ):
            good.add(minute)
    return good


def science_coverage_valid_minutes(path: Path) -> tuple[set[int], dict]:
    """Read observed science minutes only; no irradiance labels or predictors.

    The official time coordinate deliberately neglects leap seconds. Use its
    declared UTC CF clock, rather than the independent SHARP TAI conversion.
    In-memory opening avoids the netCDF4 Windows non-ASCII path limitation.
    """
    with netCDF4.Dataset("coverage-in-memory", memory=path.read_bytes()) as data:
        if {"time", "xrsb_flux", "xrsb_flag", "xrsb_num"} - set(data.variables):
            raise ValueError("Science coverage variables missing")
        tvar = data.variables["time"]
        units = getattr(tvar, "units", "")
        calendar_name = getattr(tvar, "calendar", "standard")
        if units != "seconds since 2000-01-01 12:00:00 UTC":
            raise ValueError("Science time units do not match captured UTC source")
        if calendar_name not in {"standard", "gregorian", "proleptic_gregorian"}:
            raise ValueError(
                "Science time calendar not a supported UTC Gregorian clock"
            )
        if "Record start time" not in getattr(tvar, "long_name", ""):
            raise ValueError("Science time coordinate does not document record start")
        if getattr(data.variables["xrsb_flux"], "units", "") != "W/m2":
            raise ValueError("Science irradiance units not confirmed")
        flagvar = data.variables["xrsb_flag"]
        flag_meanings = getattr(flagvar, "flag_meanings", "").split()
        if "good_data" not in flag_meanings:
            raise ValueError("Science flag metadata does not document good_data")
        flag_values = np.asarray(getattr(flagvar, "flag_values", []))
        if (
            len(flag_values) != len(flag_meanings)
            or flag_values[flag_meanings.index("good_data")] != 0
        ):
            raise ValueError("Science flag metadata does not declare zero as good_data")
        names = ["time", "xrsb_flux", "xrsb_flag"]
        if "xrsb_num" in data.variables:
            names.append("xrsb_num")
        count = len(tvar)
        if count == 0:
            raise ValueError("Science coverage has no observed records")
        if any(
            data.variables[k].dimensions != ("time",) or len(data.variables[k]) != count
            for k in names
        ):
            raise ValueError(
                "Science variables must be paired one-dimensional time arrays"
            )
        offsets = np.ma.filled(tvar[:], np.nan).astype(float)
        if not np.isfinite(offsets).all():
            raise ValueError("Science UTC time has masked or non-finite records")
        epoch = datetime(2000, 1, 1, 12, tzinfo=UTC)
        seconds = offsets + epoch.timestamp()
        if not np.all(seconds % 60 == 0):
            raise ValueError("Science UTC records are not exact minute starts")
        minute_array = (seconds // 60).astype(np.int64)
        if len(np.unique(minute_array)) != count or np.any(np.diff(minute_array) <= 0):
            raise ValueError("Science UTC records duplicated or not ordered")
        flux = np.ma.filled(data.variables["xrsb_flux"][:], np.nan).astype(float)
        flags = np.ma.filled(flagvar[:], 255)
        good = (flags == 0) & np.isfinite(flux) & (flux > 0)
        if "xrsb_num" in data.variables:
            points = np.ma.filled(data.variables["xrsb_num"][:], 0).astype(float)
            good &= np.isfinite(points) & (points > 0)
        first, last = (
            datetime.fromtimestamp(seconds[0], UTC),
            datetime.fromtimestamp(seconds[-1], UTC),
        )
        audit = {
            "records": count,
            "valid_minutes": int(good.sum()),
            "first_record_start_utc": utc_text(first),
            "last_record_start_utc": utc_text(last),
            "units": units,
            "calendar": calendar_name,
            "calendar_explicit": "calendar" in tvar.ncattrs(),
            "samples_field_present": "xrsb_num" in data.variables,
            "license_as_supplied": getattr(data, "license", None),
            "flag_meanings": flagvar.flag_meanings,
            "version_id": getattr(data, "id", None),
            "leap_seconds": "Use declared record-start UTC clock; no TAI offset or leap correction added.",
            "usage": "coverage only; flux is never used as label magnitude or predictor",
        }
        return set(minute_array[good].tolist()), audit


def coverage_stats(
    issue: datetime, minutes: set[int] | CoverageIndex, hours: int, past: bool
) -> tuple[float, int]:
    if isinstance(minutes, CoverageIndex):
        return minutes.stats(issue, hours, past)
    first = int(issue.timestamp()) // 60 - (hours * 60 if past else 0)
    expected, good, longest, run = hours * 60, 0, 0, 0
    for minute in range(first, first + expected):
        if minute in minutes:
            good += 1
            run = 0
        else:
            run += 1
            longest = max(longest, run)
    return good / expected, longest


def aggregate_patches(issue: datetime, records: list[dict]) -> tuple[dict, list[dict]]:
    eligible, audit = [], []
    seen = set()
    for row in records:
        key = (row["HARPNUM"], row["T_REC"])
        if key in seen:
            raise ValueError("Duplicate HARP/slot in preparation")
        seen.add(key)
        reasons = []
        if slot_issue(row["T_REC"]) != issue:
            raise ValueError("SHARP row maps to wrong issue")
        quality = parse_quality(row["QUALITY"])
        if quality != 0:
            reasons.append("nonzero_or_invalid_quality")
        longitude = finite_float(row["LON_FWT"])
        if longitude is None or abs(longitude) > 60:
            reasons.append("invalid_or_outside_CMD")
        try:
            obs = tai_to_utc(row["T_OBS"])
            support_end = obs + timedelta(seconds=675)
            if support_end >= issue:
                reasons.append("support_not_strictly_before_issue")
        except ValueError:
            obs, support_end = None, None
            reasons.append("invalid_T_OBS")
        values = {m: finite_float(row[m]) for m in METRICS}
        if any(v is None for v in values.values()):
            reasons.append("missing_or_nonfinite_magnetic_metric")
        processing = parse_utc(row.get("DATE"))
        entry = {
            "issue_utc": utc_text(issue),
            **row,
            "status": "rejected" if reasons else "eligible",
            "reasons": "|".join(reasons),
            "T_OBS_utc": utc_text(obs) if obs else "",
            "support_end_utc": utc_text(support_end) if support_end else "",
            "processing_DATE_after_issue": processing > issue
            if processing
            else "unknown",
            "NOAA_mapping_note": (
                "unmatched_or_unknown"
                if row.get("NOAA_NUM") in {"0", "MISSING", ""}
                else "metadata_only_not_used_for_full_disk_label"
            ),
        }
        audit.append(entry)
        if not reasons:
            eligible.append(values)
    if not eligible:
        return {
            "patch_count": None,
            **{f"{m}_{a}": None for m in METRICS for a in ["max", "median"]},
        }, audit
    result = {"patch_count": len(eligible)}
    for metric in METRICS:
        values = np.asarray([r[metric] for r in eligible], dtype=float)
        result[f"{metric}_max"] = float(np.max(values))
        result[f"{metric}_median"] = float(np.median(values))
    return result, audit


def checked_sources(input_dir: Path) -> tuple[dict, dict[str, Path]]:
    manifest_path = input_dir / "source_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get(
        "schema_version"
    ) != "official-flare-forecast-sources-v1" or not manifest.get(
        "acquisition_complete"
    ):
        raise ValueError("Source acquisition incomplete or unknown manifest schema")
    if manifest.get("data_rules") != DATA_RULES:
        raise ValueError("Source preparation rules differ from registered tool rules")
    files = {}
    for record in manifest["files"]:
        path = (input_dir / record["path"]).resolve()
        if not path.is_relative_to(input_dir) or path.name in files:
            raise ValueError("Source path escape or duplicate source filename")
        if path.stat().st_size != record["bytes"] or sha256(path) != record["sha256"]:
            raise ValueError("Source input hash or size mismatch")
        files[path.name] = path
    return manifest, files


def attach_coverage(args: argparse.Namespace) -> None:
    source, seed, out = (
        Path(args.input_dir).resolve(),
        Path(args.coverage_seed).resolve(),
        Path(args.output_dir).resolve(),
    )
    if out.exists() or out.is_relative_to(source) or out.is_relative_to(seed):
        raise ValueError("Attachment requires new separate output directory")
    original, core_files = checked_sources(source)
    coverage_manifest_path = seed / "coverage_manifest.json"
    coverage = json.loads(coverage_manifest_path.read_text(encoding="utf-8"))
    if (
        coverage.get("schema_version") != "coverage-preseed-v1"
        or coverage.get("complete") is not True
    ):
        raise ValueError("Coverage seed must have completed acquisition manifest")
    out.mkdir(parents=True)
    result = {
        **original,
        "files": [dict(x) for x in original["files"]],
        "coverage_seed_manifest_sha256": sha256(coverage_manifest_path),
        "coverage_seed_manifest_source_path": str(coverage_manifest_path),
        "core_source_manifest_sha256": sha256(source / "source_manifest.json"),
        "core_source_manifest_source_path": str(source / "source_manifest.json"),
        "coverage_months": coverage_month_keys(),
        "coverage_satellites": [13, 15],
        "coverage_failures": coverage.get("failures", []),
        "attachment_code_sha256": sha256(Path(__file__)),
    }
    for name, old in core_files.items():
        shutil.copyfile(old, out / name)
        if sha256(out / name) != sha256(old):
            raise ValueError("Core read-only copy mismatch")
    for record in coverage["files"]:
        satellite, month = (
            int(record["satellite"]),
            f"{int(record['year'])}-{int(record['month']):02d}",
        )
        if record["url"] != coverage_url(month, satellite):
            raise ValueError(
                "Coverage URL does not match registered source/month/satellite"
            )
        old = (seed / record["path"]).resolve()
        if (
            not old.is_relative_to(seed)
            or sha256(old) != record["sha256"]
            or old.stat().st_size != record["bytes"]
        ):
            raise ValueError("Coverage source hash/size/path mismatch")
        name = f"coverage_g{satellite}_{month.replace('-', '_')}.csv"
        if (out / name).exists():
            raise ValueError("Duplicate attached coverage file")
        shutil.copyfile(old, out / name)
        result["files"].append(
            {
                **record,
                "path": name,
                "role": "goes_legacy_coverage",
                "original_seed_path": str(old),
                "copy_sha256": sha256(out / name),
                "terms_url": "https://www.ncei.noaa.gov/products/goes-1-15/space-weather-instruments",
            }
        )
    # Preserve the complete parent receipts/failures index in the new isolated input bundle.
    shutil.copyfile(
        coverage_manifest_path, out / "coverage_acquisition_original_manifest.json"
    )
    result["files"].append(
        source_record(
            out / "coverage_acquisition_original_manifest.json",
            out,
            "goes_coverage_acquisition_manifest",
            "operator_local_receipts_for_registered_official_urls",
            {},
            {"original_sha256": sha256(coverage_manifest_path)},
        )
    )
    result["total_source_bytes"] = sum(r["bytes"] for r in result["files"])
    result["total_source_bytes_plus_rejected_archive"] = (
        result["total_source_bytes"] + PRIOR_REJECTED_ARCHIVE_BYTES
    )
    if result["total_source_bytes_plus_rejected_archive"] > MAX_BYTES:
        raise ValueError("Attached sources exceed registered acquisition cap")
    write_json(out / "source_manifest.json", result)
    print(
        json.dumps(
            {
                "status": "coverage_attached",
                "files": len(result["files"]),
                "bytes": result["total_source_bytes"],
                "manifest_sha256": sha256(out / "source_manifest.json"),
            }
        ),
        flush=True,
    )


def write_csv(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    if columns is None:
        columns = list(dict.fromkeys(k for row in rows for k in row))
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def prepare(args: argparse.Namespace) -> None:
    source = Path(args.input_dir).resolve()
    out = Path(args.output_dir).resolve()
    if out.exists() or out.is_relative_to(source):
        raise ValueError("Refuse overwrite or output inside source inputs")
    contract_path = Path(args.contract).resolve()
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    amendment_path = Path(args.amendment).resolve()
    amendment = json.loads(amendment_path.read_text(encoding="utf-8"))
    validate_contract(contract, amendment, sha256(contract_path))
    manifest, files = checked_sources(source)
    source_amendment_path = (
        Path(args.source_amendment).resolve() if args.source_amendment else None
    )
    source_amendment_hash = None
    if source_amendment_path:
        source_amendment = json.loads(source_amendment_path.read_text(encoding="utf-8"))
        validate_source_amendment(source_amendment, sha256(contract_path))
        source_amendment_hash = sha256(source_amendment_path)
        if manifest.get("science_coverage_amendment_sha256") != source_amendment_hash:
            raise ValueError(
                "Source bundle does not bind the active science source amendment"
            )
    elif any(r["role"] == "goes_science_coverage" for r in manifest["files"]):
        raise ValueError(
            "Science coverage sources require explicit frozen source amendment"
        )
    months = [args.probe_month] if args.probe_month else month_keys()
    if any(m not in month_keys() for m in months):
        raise ValueError("Probe month outside registered range")
    required = {"sharp_schema.json", "goes_metadata.json"}
    required |= {f"goes_{year}_v1-0-1.csv" for year in range(2010, 2018)}
    required |= {f"sharp_{m.replace('-', '_')}.json" for m in months}
    if required - set(files):
        raise ValueError(
            "Required source month/year missing; do not create negative labels"
        )
    before = {name: sha256(path) for name, path in files.items()}
    schema = validate_schema(
        json.loads(files["sharp_schema.json"].read_text(encoding="utf-8"))
    )
    metadata = json.loads(files["goes_metadata.json"].read_text(encoding="utf-8"))
    if "UTC" not in metadata.get("variable_attributes", {}).get("time", {}).get(
        "comments", ""
    ):
        raise ValueError(
            "GOES time-zone metadata does not confirm UTC calendar interpretation"
        )
    if (
        metadata.get("variable_attributes", {}).get("xrsb_irrad", {}).get("units")
        != "W/m2"
    ):
        raise ValueError("GOES peak irradiance units not confirmed")
    raw_events = []
    for year in range(2010, 2018):
        name = f"goes_{year}_v1-0-1.csv"
        with files[name].open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            if {"start_time", "end_time", "time", "xrsb_irrad"} - set(
                reader.fieldnames or []
            ):
                raise ValueError("GOES annual catalog required fields missing")
            raw_events += [{**r, "source_file": name} for r in reader]
    events, event_audit = normalize_events(raw_events)
    event_index = EventIndex(events)
    missing_end_index = EventIndex([e for e in events if e["end"] is None])
    unknown_start_count = sum(
        "missing_or_invalid_start_time" in row["reasons"] for row in event_audit
    )
    valid_minutes, coverage_sources = set(), []
    for record in manifest["files"]:
        if record["role"] == "goes_legacy_coverage":
            good = coverage_valid_minutes(source / record["path"])
            valid_minutes.update(good)
            coverage_sources.append(
                {"path": record["path"], "valid_minutes": len(good)}
            )
        elif record["role"] == "goes_science_coverage":
            satellite = record["parameters"]["satellite"]
            year = record["parameters"]["year"]
            expected_url = source_amendment["annual_preferred_template"].format(
                sat=satellite, year=year
            )
            if record["url"] != expected_url or str(year) not in SCIENCE_MONTHS.get(
                str(satellite), {}
            ):
                raise ValueError(
                    "Science coverage source is outside registered annual products"
                )
            good, science_audit = science_coverage_valid_minutes(
                source / record["path"]
            )
            if science_audit["version_id"] != expected_url.rsplit("/", 1)[1]:
                raise ValueError(
                    "Science internal product identifier differs from frozen URL"
                )
            cropped = science_crop_minutes(good, satellite, year)
            valid_minutes.update(cropped)
            coverage_sources.append(
                {
                    "path": record["path"],
                    "valid_minutes_before_crop": len(good),
                    "valid_minutes": len(cropped),
                    "crop_months": SCIENCE_MONTHS[str(satellite)][str(year)],
                    **science_audit,
                    "used_valid_minutes_after_crop": len(cropped),
                }
            )
    coverage_index = CoverageIndex(valid_minutes)
    bad_start_peak_dates = {
        peak.date() + timedelta(days=delta)
        for row in event_audit
        if "missing_or_invalid_start_time" in row["reasons"]
        and (peak := parse_utc(row["raw_peak_time"], catalog_naive_is_utc=True))
        is not None
        for delta in [-1, 0, 1]
    }
    by_issue = {}
    for month in months:
        name = f"sharp_{month.replace('-', '_')}.json"
        payload = json.loads(files[name].read_text(encoding="utf-8"))
        for row in rows_from_jsoc(payload):
            issue = slot_issue(row["T_REC"])
            if issue.strftime("%Y-%m") != month:
                raise ValueError(
                    "Monthly SHARP query returned issue outside expected month"
                )
            by_issue.setdefault(issue, []).append(row)
    daily, patches = [], []
    for month in months:
        year, number = map(int, month.split("-"))
        for day in range(1, calendar.monthrange(year, number)[1] + 1):
            issue = datetime(year, number, day, tzinfo=UTC)
            records = by_issue.get(issue, [])
            aggregate, audit = aggregate_patches(issue, records)
            patches += audit
            labels = label_and_history(issue, event_index, event_audit)
            future_fraction, future_gap = coverage_stats(
                issue, coverage_index, 24, False
            )
            past_fraction, past_gap = coverage_stats(issue, coverage_index, 168, True)
            coverage_ok = (
                future_fraction >= 0.99
                and past_fraction >= 0.99
                and future_gap <= 5
                and past_gap <= 5
            )
            reasons = []
            if not records:
                reasons.append("missing_SHARP_slot")
            if aggregate["patch_count"] is None:
                reasons.append("no_eligible_SHARP_patches")
            if not labels["future_label_qualified"]:
                reasons.append("future_event_peak_missing")
            if issue.date() in bad_start_peak_dates:
                reasons.append("invalid_catalog_start_peak_date_quarantine")
            if not coverage_ok:
                reasons.append("insufficient_independent_XRS_coverage")
            history_bad = [
                r
                for r in event_audit
                if "missing_or_nonpositive_peak_flux" in r["reasons"]
                and (start := parse_utc(r["raw_start_time"], catalog_naive_is_utc=True))
                is not None
                and issue - timedelta(hours=168) <= start < issue
            ]
            if history_bad:
                reasons.append("history_catalog_incomplete_event_information")
            labels["catalog_label_c1"] = labels["label_c1"]
            labels["catalog_label_m1"] = labels["label_m1"]
            labels["catalog_y_class"] = labels["y_class"]
            outcome_qualified = (
                future_fraction >= 0.99
                and future_gap <= 5
                and labels["future_label_qualified"]
                and issue.date() not in bad_start_peak_dates
            )
            labels["outcome_observability_qualified"] = outcome_qualified
            if not outcome_qualified:
                labels["label_c1"] = labels["label_m1"] = labels["y_class"] = None
            observed_ends = [
                parse_utc(r["support_end_utc"])
                for r in audit
                if r["status"] == "eligible"
            ]
            hmi_latest = max(observed_ends) if observed_ends else None
            history_end = parse_utc(labels["history_latest_event_end_utc"])
            if history_end:
                observed_ends.append(history_end)
            max_observed = max(observed_ends) if observed_ends else None
            daily.append(
                {
                    "issue_utc": utc_text(issue),
                    "status": "eligible" if not reasons else "excluded",
                    "exclusion_reasons": "|".join(reasons),
                    "raw_patch_count": len(records),
                    "rejected_patch_count": sum(
                        r["status"] == "rejected" for r in audit
                    ),
                    **aggregate,
                    **labels,
                    "future_coverage_fraction": future_fraction,
                    "past_168h_coverage_fraction": past_fraction,
                    "future_max_gap_minutes": future_gap,
                    "past_168h_max_gap_minutes": past_gap,
                    "independent_XRS_coverage_qualified": coverage_ok,
                    "history_bad_event_count": len(history_bad),
                    "history_missing_end_event_count": len(
                        missing_end_index.window(issue - timedelta(hours=168), issue)
                    ),
                    "hmi_latest_support_end_utc": utc_text(hmi_latest)
                    if hmi_latest
                    else "",
                    "feature_max_observed_utc": utc_text(max_observed)
                    if max_observed
                    else "",
                }
            )
    accepted = [r for r in daily if r["status"] == "eligible"]
    out.mkdir(parents=True)
    write_csv(out / "all_daily_windows.csv", daily)
    write_csv(
        out / "daily_instances.csv",
        accepted,
        list(daily[0]) if daily else ["issue_utc", *FEATURES],
    )
    write_csv(
        out / "patch_audit.csv",
        patches,
        [
            "issue_utc",
            *KEYWORDS,
            "status",
            "reasons",
            "T_OBS_utc",
            "support_end_utc",
            "processing_DATE_after_issue",
            "NOAA_mapping_note",
        ],
    )
    write_csv(
        out / "event_audit.csv",
        event_audit,
        [
            "row_ordinal",
            "flare_id",
            "raw_start_time",
            "raw_end_time",
            "raw_peak_time",
            "raw_peak_flux",
            "flare_class",
            "active_region",
            "peak_saturated",
            "source_file",
            "reasons",
        ],
    )
    event_rows = [
        {k: utc_text(v) if isinstance(v, datetime) else v for k, v in r.items()}
        for r in events
    ]
    write_csv(out / "valid_events.csv", event_rows)
    after = {name: sha256(path) for name, path in files.items()}
    if before != after:
        raise ValueError("Read-only inputs changed during preparation")
    write_json(
        out / "input_integrity.json",
        {
            "input_hashes_before": before,
            "input_hashes_after": after,
            "all_input_hashes_unchanged": before == after,
            "source_manifest_sha256": sha256(source / "source_manifest.json"),
            "contract_sha256": sha256(contract_path),
            "coverage_amendment_sha256": sha256(amendment_path),
            "science_coverage_amendment_sha256": source_amendment_hash,
        },
    )
    source_groups = []
    for role in sorted({r["role"] for r in manifest["files"]}):
        group = [r for r in manifest["files"] if r["role"] == role]
        source_groups.append(
            {
                "role": role,
                "file_count": len(group),
                "bytes": sum(r["bytes"] for r in group),
                "versions": sorted({r["version"] for r in group}),
                "license_or_terms": sorted({r["license_or_terms"] for r in group}),
            }
        )
    summary = {
        "schema_version": "daily-full-disk-forecast-preparation-v1",
        "execution_kind": "operator_probe"
        if args.probe_month
        else "deterministic_preparation_pending_agent_receipt",
        "observed_months": months,
        "daily_windows": len(daily),
        "eligible_daily_instances": len(accepted),
        "excluded_daily_windows": len(daily) - len(accepted),
        "feature_columns": FEATURES,
        "feature_count": len(FEATURES),
        "patch_status_counts": dict(Counter(r["status"] for r in patches)),
        "patch_rejection_reason_counts": dict(
            Counter(
                reason for r in patches for reason in r["reasons"].split("|") if reason
            )
        ),
        "valid_catalog_events": len(events),
        "catalog_event_audit_rows": len(event_audit),
        "catalog_missing_start_count": unknown_start_count,
        "catalog_missing_end_count": len(missing_end_index.events),
        "history_end_missing_policy": "Future target still uses valid start and peak. History excludes events without known completed end; counts are completed-end-known events, not total physical past activity. No end inferred; may depress history/persistence features.",
        "eligible_c1_positive_days": sum(r["label_c1"] for r in accepted),
        "eligible_m1_positive_days": sum(r["label_m1"] for r in accepted),
        "continuous_XRS_coverage_certified": False,
        "independent_XRS_coverage_rule": {
            "satellites": [13, 14, 15] if source_amendment_hash else [13, 15],
            "windows_hours": [24, 168],
            "minimum_fraction": 0.99,
            "maximum_missing_run_minutes": 5,
            "future_window_use": "outcome observability only; never feature",
        },
        "independent_XRS_coverage_source_count": len(coverage_sources),
        "independent_XRS_union_good_minutes": len(valid_minutes),
        "target_definition": "New events in the supplied valid science flare catalog starting in [issue,issue+24h); zero means no listed above-threshold event, not proven continuously observed quiet Sun.",
        "interpretation": DATA_RULES["interpretation"],
        "processing_DATE_after_issue_patches": sum(
            r["processing_DATE_after_issue"] is True for r in patches
        ),
        "data_preparation": {
            **DATA_RULES,
            **(
                {
                    "coverage_policy": "registered legacyG13/G15 plus allowed scienceG13/G14/G15 union; same fraction>=.99 and maxgap<=5min"
                }
                if source_amendment_hash
                else {}
            ),
        },
        "source_groups": source_groups,
        "full_source_provenance": "prepared_manifest.json and original source_manifest.json; this compact summary does not reproduce all raw sources",
        "contract_sha256": sha256(contract_path),
        "source_manifest_sha256": sha256(source / "source_manifest.json"),
        "coverage_amendment_sha256": sha256(amendment_path),
        "science_coverage_amendment_sha256": source_amendment_hash,
        "code_sha256": sha256(Path(__file__)),
        "all_input_hashes_unchanged": before == after,
        "input_integrity_path": "input_integrity.json",
        "input_integrity_sha256": sha256(out / "input_integrity.json"),
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "requests": requests.__version__,
            "netCDF4": netCDF4.__version__,
        },
        "keyword_units_as_supplied": {
            name: {"units": schema[name].get("units"), "note": schema[name].get("note")}
            for name in METRICS
        },
        "unit_cautions": [
            "TOTPOT raw series unit metadata differs from SHARP paper area-integrated proxy convention; no physical conversion asserted.",
            "R_VALUE uses archive keyword representation; max/median are engineered features, not an inferred total physical flux.",
            "Max/median across quality-eligible visible patches are not total solar or coronal energy.",
        ],
        "models_fit": False,
        "figures_generated": False,
        "jw_execution_proof_status": "pending_external_driver_receipt",
    }
    write_json(out / "summary.json", summary)
    validation = {
        "status": "passed",
        "scope": "input_hash_and_deterministic_preparation_contract",
        "scientific_predictive_validation": "not_performed",
        "continuous_XRS_coverage_certified": False,
        "input_hashes_unchanged": before == after,
        "daily_windows": len(daily),
        "eligible_daily_instances": len(accepted),
        "feature_count": len(FEATURES),
        "eligible_rows_independent_coverage_qualified": all(
            r["independent_XRS_coverage_qualified"] for r in accepted
        ),
        "is_acquisition_probe": bool(args.probe_month),
        "models_fit": False,
    }
    write_json(out / "validation.json", validation)
    if source_amendment_hash:
        write_json(
            out / "coverage_source_audit.json",
            {
                "files": coverage_sources,
                "science_coverage_amendment_sha256": source_amendment_hash,
                "union_valid_minutes": len(valid_minutes),
                "used_only_for_coverage": True,
            },
        )
    output_files = [
        {"path": p.name, "bytes": p.stat().st_size, "sha256": sha256(p)}
        for p in sorted(out.iterdir())
        if p.is_file()
    ]
    write_json(
        out / "prepared_manifest.json",
        {
            "schema_version": "prepared-forecast-artifacts-v1",
            "files": output_files,
            "source_versions_terms": manifest["files"],
            "parameters": summary["data_preparation"],
            "code_sha256": sha256(Path(__file__)),
            "contract_sha256": sha256(contract_path),
            "coverage_amendment_sha256": sha256(amendment_path),
            "science_coverage_amendment_sha256": source_amendment_hash,
            "schema": {
                "features": FEATURES,
                "issue_time_column": "issue_utc",
                "target_class_column": "y_class",
                "status_column": "status",
                "exclusion_reason_column": "exclusion_reasons",
                "binary_target_columns": ["label_c1", "label_m1"],
            },
            "execution_kind": summary["execution_kind"],
            "jw_execution_proof_status": "pending_external_driver_receipt",
        },
    )
    print(
        json.dumps(
            {
                "status": "prepared",
                "eligible_daily_instances": len(accepted),
                "daily_windows": len(daily),
                "feature_count": len(FEATURES),
                "output_directory": str(out),
                "prepared_manifest_sha256": sha256(out / "prepared_manifest.json"),
            }
        ),
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    a = sub.add_parser("acquire")
    a.add_argument("--output-dir", required=True)
    a.add_argument(
        "--months",
        help="Comma separated registered months; omitted means all 72 months",
    )
    a.add_argument(
        "--seed-dir",
        help="Read-only prior acquisition directory to copy verified complete sources",
    )
    a.add_argument(
        "--coverage-months",
        help="Comma separated coverage months; default 2010-12 through 2016-12",
    )
    a.add_argument(
        "--skip-coverage",
        action="store_true",
        help="Operator core-source acquisition only; final prepare still enforces coverage",
    )
    p = sub.add_parser("prepare")
    p.add_argument("--input-dir", required=True)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--contract", required=True)
    p.add_argument("--amendment", required=True)
    p.add_argument(
        "--source-amendment",
        help="Frozen amendment03 required when science NC coverage is present",
    )
    p.add_argument(
        "--probe-month",
        help="Operator month-only qualification, never a full model dataset",
    )
    c = sub.add_parser("attach-coverage")
    c.add_argument("--input-dir", required=True)
    c.add_argument("--coverage-seed", required=True)
    c.add_argument("--output-dir", required=True)
    t = sub.add_parser("describe-rules")
    t.add_argument("--output-path", required=True)
    args = parser.parse_args()
    try:
        if args.command == "acquire":
            acquire(args)
        elif args.command == "prepare":
            prepare(args)
        elif args.command == "attach-coverage":
            attach_coverage(args)
        else:
            path = Path(args.output_path)
            if path.exists():
                raise ValueError("Contract template path exists")
            write_json(path, {"data_preparation": DATA_RULES})
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)


if __name__ == "__main__":
    main()
