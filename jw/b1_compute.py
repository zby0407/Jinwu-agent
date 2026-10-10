"""Small, offline B1 flare comparisons; never a mechanism or prediction claim.

The public entry points accept canonical CSV files, a frozen parameter JSON and
a source manifest. Inputs are read-only. No model, service, or network is used.
UTC times must include ``Z`` or an explicit zero offset; TAI is converted only by
the separate :func:`tai_to_utc` adapter, never guessed from a column name.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import json
import math
import platform
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any

METRICS = {
    "usflux_mx": "Mx",
    "totpot_erg_per_cm": "erg/cm (photospheric proxy, not coronal energy)",
    "meanshr_deg": "degree",
}
MAGNETIC_COLUMNS = {
    "time_utc",
    "harpnum",
    "noaa_ar",
    *METRICS,
    "quality_valid",
    "support_end_utc",
    "source_record_id",
}
EVENT_COLUMNS = {
    "event_id",
    "start_utc",
    "peak_utc",
    "end_utc",
    "goes_class",
    "noaa_ar",
    "region_verified",
}
XRS_COLUMNS = {"time_utc", "flux_w_m2", "quality_valid"}
UTC = timezone.utc


class B1ValidationError(ValueError):
    """The inputs do not meet the explicitly specified B1 data contract."""


def parse_utc(value: str) -> datetime:
    """Require a full ISO timestamp with an explicit UTC (zero) offset."""
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)", value
    ):
        raise B1ValidationError(f"Expected explicit UTC ISO timestamp: {value!r}")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise B1ValidationError(f"Invalid UTC timestamp: {value!r}") from exc


def tai_to_utc(value: str) -> str:
    """Convert explicit JSOC TAI or ISO TAI using Astropy's leap-second table.

    ``2014.10.20_00:00:00_TAI`` or ``2014-10-20T00:00:00`` is accepted.
    Input is *specified* to be TAI by this API; UTC-suffixed values are rejected.
    No online time-table update is requested. Pin/record Astropy when reproducing.
    """
    from astropy.time import Time

    if value.endswith("_TAI"):
        value = value[:-4].replace("_", "T")
        date, clock = value.split("T", 1)
        value = date.replace(".", "-") + "T" + clock
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?", value):
        raise B1ValidationError("TAI input must be unambiguous ISO or JSOC _TAI")
    try:
        result = str(Time(value, format="isot", scale="tai").utc.isot) + "Z"
        # Python datetime cannot represent a leap-second instant. Refuse to
        # silently collapse it onto another timestamp in this canonical format.
        parse_utc(result)
        return result
    except (TypeError, ValueError) as exc:
        raise B1ValidationError(
            f"Cannot represent TAI input as canonical UTC: {value}"
        ) from exc


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _bool(value: str, field: str) -> bool:
    if str(value).strip().lower() in {"true", "1"}:
        return True
    if str(value).strip().lower() in {"false", "0"}:
        return False
    raise B1ValidationError(f"{field} requires explicit true/false, got {value!r}")


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not re.fullmatch(r"[1-9]\d*", str(value)):
        raise B1ValidationError(f"{field} requires one positive integer, got {value!r}")
    return int(value)


def _number(value: Any, field: str, *, allow_missing: bool = False) -> float | None:
    if allow_missing and (
        value is None or str(value).strip().lower() in {"", "nan", "null", "none"}
    ):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise B1ValidationError(f"{field} is not numeric: {value!r}") from exc
    if not math.isfinite(result):
        raise B1ValidationError(f"{field} must be finite or explicitly missing")
    return result


def goes_flux(goes_class: str) -> float:
    """Convert an explicit A/B/C/M/X flare class to 1–8 Å peak flux units."""
    match = re.fullmatch(r"([ABCMX])(\d+(?:\.\d+)?)", str(goes_class).strip().upper())
    if not match or float(match[2]) <= 0:
        raise B1ValidationError(f"Invalid GOES class: {goes_class!r}")
    return {"A": 1e-8, "B": 1e-7, "C": 1e-6, "M": 1e-5, "X": 1e-4}[match[1]] * float(
        match[2]
    )


def _csv(path: Path, columns: set[str]) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(
            reader.fieldnames
        ):
            raise B1ValidationError(f"Missing or duplicate CSV header: {path.name}")
        if missing := columns - set(reader.fieldnames):
            raise B1ValidationError(f"{path.name}: missing columns {sorted(missing)}")
        rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise B1ValidationError(f"Malformed CSV row: {path.name}")
    return rows


def _parameters(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise B1ValidationError("Parameters must be a JSON object")
    required = {
        "schema_version",
        "noaa_ar",
        "harpnum",
        "target_start_utc",
        "target_end_utc",
    }
    if required - data.keys():
        raise B1ValidationError(f"Missing parameters: {sorted(required - data.keys())}")
    defaults = {
        "min_class": "M1.0",
        "max_events": 3,
        "max_controls": 3,
        "window_hours": 24,
        "guard_minutes": 30,
        "min_coverage": 0.9,
        "sharp_cadence_seconds": 720,
        "xrs_cadence_seconds": 60,
    }
    unknown = data.keys() - required - defaults.keys() - {"scope_note", "synthetic"}
    if unknown:
        raise B1ValidationError(
            f"Unknown parameters (possible typo): {sorted(unknown)}"
        )
    result = defaults | data
    if result["schema_version"] != "b1-flare-case-v1":
        raise B1ValidationError("schema_version must be b1-flare-case-v1")
    for field in (
        "noaa_ar",
        "harpnum",
        "max_events",
        "max_controls",
        "sharp_cadence_seconds",
        "xrs_cadence_seconds",
    ):
        result[field] = _integer(result[field], field)
    if not 1 <= result["max_events"] <= 3 or not 1 <= result["max_controls"] <= 3:
        raise B1ValidationError("B1 is bounded to 1–3 events and 1–3 controls")
    for field in ("window_hours", "guard_minutes", "min_coverage"):
        result[field] = _number(result[field], field)
    if not 2 <= result["window_hours"] <= 48:
        raise B1ValidationError("window_hours must be within [2, 48]")
    if not 30 <= result["guard_minutes"] <= 180:
        raise B1ValidationError("guard_minutes must be within [30, 180]")
    if not 0.5 <= result["min_coverage"] <= 1:
        raise B1ValidationError("min_coverage must be within [0.5, 1]")
    for field in ("sharp_cadence_seconds", "xrs_cadence_seconds"):
        if result[field] > result["window_hours"] * 3600 / 4:
            raise B1ValidationError(f"{field} gives fewer than four expected samples")
    start, end = (
        parse_utc(result["target_start_utc"]),
        parse_utc(result["target_end_utc"]),
    )
    if not timedelta(0) < end - start <= timedelta(days=14):
        raise B1ValidationError("Target interval must be positive and at most 14 days")
    if goes_flux(result["min_class"]) < goes_flux("M1.0"):
        raise B1ValidationError("This bounded plan requires min_class >= M1.0")
    return result


def _sources(path: Path, inputs: dict[str, Path]) -> list[dict[str, Any]]:
    """Verify bound canonical bytes, preserving raw lineage without path reads.

    ``raw_source`` entries are declarations from the acquisition adapter. This
    core must not open their paths: a task can only read the explicitly bound
    canonical files. ``canonical_input`` entries use input_name (magnetic,
    events, xrs, parameters) to bind the actual supplied path; their
    declared path is informational and cannot redirect the read.
    """
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    sources = data.get("sources") if isinstance(data, dict) else data
    if not isinstance(sources, list) or not sources:
        raise B1ValidationError("Source manifest must contain a nonempty sources list")
    canonical_names = set()
    for source in sources:
        required = {
            "role",
            "url",
            "title",
            "license_or_terms",
            "citation",
            "sha256",
            "path",
        }
        if (
            not isinstance(source, dict)
            or required - source.keys()
            or any(
                not isinstance(source[k], str) or not source[k].strip()
                for k in required
            )
        ):
            raise B1ValidationError(
                "Each source requires role/url/title/license_or_terms/citation/sha256/path"
            )
        if not re.fullmatch(r"[0-9a-fA-F]{64}", str(source["sha256"])):
            raise B1ValidationError(
                "Source SHA-256 must contain 64 hexadecimal characters"
            )
        if source["role"] == "canonical_input":
            name = source.get("input_name")
            if name not in inputs or name in canonical_names:
                raise B1ValidationError(
                    "canonical_input requires one unique bound input_name"
                )
            canonical_names.add(name)
            if _sha(inputs[name]) != str(source["sha256"]).lower():
                raise B1ValidationError(f"Canonical input SHA-256 mismatches: {name}")
            source["resolved_path"] = str(inputs[name].resolve())
            source["hash_verification"] = "verified_bound_canonical_bytes"
        elif source["role"] == "raw_source":
            source["resolved_path"] = source["path"]
            source["hash_verification"] = (
                "acquisition_adapter_declaration_not_reread_by_compute"
            )
        else:
            raise B1ValidationError("Source role must be raw_source or canonical_input")
    if {"magnetic", "events", "xrs", "parameters"} - canonical_names:
        raise B1ValidationError(
            "Sources must bind magnetic, events, xrs, and parameters canonical input hashes"
        )
    return sources


def _load(
    magnetic_path: Path,
    events_path: Path,
    xrs_path: Path,
    parameters_path: Path,
    sources_path: Path,
) -> dict[str, Any]:
    p = _parameters(parameters_path)
    sources = _sources(
        sources_path,
        {
            "magnetic": magnetic_path,
            "events": events_path,
            "xrs": xrs_path,
            "parameters": parameters_path,
        },
    )
    manifest = json.loads(sources_path.read_text(encoding="utf-8-sig"))
    source_context = {
        key: manifest[key]
        for key in (
            "semantic_decisions",
            "transport_limitations",
            "frozen_scope",
            "region_mapping",
        )
        if isinstance(manifest, dict) and key in manifest
    }
    magnetic, events, xrs = [], [], []
    seen_times: set[datetime] = set()
    seen_records: set[str] = set()
    for raw in _csv(magnetic_path, MAGNETIC_COLUMNS):
        row: dict[str, Any] = dict(raw)
        row["time"] = parse_utc(raw["time_utc"])
        row["support_end"] = parse_utc(raw["support_end_utc"])
        if row["support_end"] < row["time"]:
            raise B1ValidationError("Magnetic support_end precedes its timestamp")
        if row["time"] in seen_times or raw["source_record_id"] in seen_records:
            raise B1ValidationError("Duplicate magnetic timestamp or source_record_id")
        if not raw["source_record_id"].strip():
            raise B1ValidationError("Empty magnetic source_record_id")
        seen_times.add(row["time"])
        seen_records.add(raw["source_record_id"])
        if (
            _integer(raw["harpnum"], "harpnum") != p["harpnum"]
            or _integer(raw["noaa_ar"], "noaa_ar") != p["noaa_ar"]
        ):
            raise B1ValidationError(
                "Magnetic table must have exactly the selected unique HARP/NOAA mapping"
            )
        row["quality"] = _bool(raw["quality_valid"], "quality_valid")
        for metric in METRICS:
            row[metric] = _number(raw[metric], metric, allow_missing=True)
            if row[metric] is not None and (
                row[metric] < 0 or metric == "meanshr_deg" and row[metric] > 180
            ):
                raise B1ValidationError(f"Out-of-range physical metric: {metric}")
        for error in ("err_usflux", "err_totpot", "err_meanshr"):
            if error in raw:
                row[error] = _number(raw[error], error, allow_missing=True)
                if row[error] is not None and row[error] < 0:
                    raise B1ValidationError(f"Negative uncertainty: {error}")
        magnetic.append(row)
    if not magnetic:
        raise B1ValidationError(
            "Magnetic input is empty; missing observations are not zero"
        )
    magnetic.sort(key=lambda row: row["time"])
    seen_events: set[str] = set()
    seen_event_times: set[datetime] = set()
    for raw in _csv(events_path, EVENT_COLUMNS):
        row = dict(raw)
        for field in ("start", "peak"):
            row[field] = parse_utc(raw[field + "_utc"])
        row["end"] = parse_utc(raw["end_utc"]) if raw["end_utc"].strip() else None
        if (
            row["start"] > row["peak"]
            or row["end"] is not None
            and row["peak"] > row["end"]
        ):
            raise B1ValidationError("Event requires start <= peak <= end")
        if (
            not raw["event_id"].strip()
            or raw["event_id"] in seen_events
            or row["start"] in seen_event_times
        ):
            raise B1ValidationError(
                "Empty/duplicate event_id or duplicate event start timestamp"
            )
        seen_events.add(raw["event_id"])
        seen_event_times.add(row["start"])
        row["verified"] = _bool(raw["region_verified"], "region_verified")
        row["region"] = (
            _integer(raw["noaa_ar"], "noaa_ar") if raw["noaa_ar"].strip() else None
        )
        if row["verified"] and row["region"] is None:
            raise B1ValidationError(
                "Verified event requires an explicit unique NOAA region"
            )
        row["flux"] = goes_flux(raw["goes_class"])
        events.append(row)
    events.sort(key=lambda row: (row["start"], row["event_id"]))
    seen_times = set()
    for raw in _csv(xrs_path, XRS_COLUMNS):
        row = dict(raw)
        row["time"] = parse_utc(raw["time_utc"])
        # NOAA one-minute averages are supported through the following minute.
        # A source adapter can supply an explicit later support endpoint.
        row["support_end"] = (
            parse_utc(raw["support_end_utc"])
            if raw.get("support_end_utc", "").strip()
            else row["time"] + timedelta(seconds=60)
        )
        if row["support_end"] < row["time"]:
            raise B1ValidationError("XRS support_end precedes its timestamp")
        if row["time"] in seen_times:
            raise B1ValidationError("Duplicate XRS timestamp")
        seen_times.add(row["time"])
        row["quality"] = _bool(raw["quality_valid"], "quality_valid")
        row["flux"] = _number(raw["flux_w_m2"], "flux_w_m2", allow_missing=True)
        if row["flux"] is not None and row["flux"] < 0:
            raise B1ValidationError(
                "Negative XRS flux must be represented as missing with quality=false"
            )
        xrs.append(row)
    if not xrs:
        raise B1ValidationError(
            "XRS input is empty; missing observations do not establish a quiet control"
        )
    xrs.sort(key=lambda row: row["time"])
    return {
        "parameters": p,
        "sources": sources,
        "source_context": source_context,
        "magnetic": magnetic,
        "events": events,
        "xrs": xrs,
    }


def validate_b1_inputs(
    magnetic_path: str | Path,
    events_path: str | Path,
    xrs_path: str | Path,
    parameters_path: str | Path,
    sources_path: str | Path,
) -> dict[str, Any]:
    """Read-only schema/provenance validation, without window analysis or science."""
    paths = list(
        map(Path, (magnetic_path, events_path, xrs_path, parameters_path, sources_path))
    )
    data = _load(*paths)
    return {
        "status": "validated_structure_only_not_an_experiment",
        "rows": {key: len(data[key]) for key in ("magnetic", "events", "xrs")},
        "parameters": data["parameters"],
        "source_count": len(data["sources"]),
        "input_sha256": {str(path.resolve()): _sha(path) for path in paths},
        "scope": "Schema, explicit UTC, unique region, quality representation, and source hashes only; window eligibility is not evaluated.",
    }


def _coverage(
    rows: list[dict[str, Any]], start: datetime, end: datetime, cadence: int
) -> float:
    expected = math.ceil((end - start).total_seconds() / cadence)
    occupied = {
        int((row["time"] - start).total_seconds() // cadence)
        for row in rows
        if start <= row["time"] < end
    }
    return len(occupied) / expected if expected else 0.0


def _window(
    data: dict[str, Any], anchor: datetime, kind: str, identifier: str
) -> dict[str, Any]:
    p = data["parameters"]
    guard = timedelta(minutes=p["guard_minutes"])
    end = anchor - guard
    start = end - timedelta(hours=p["window_hours"])
    middle = start + (end - start) / 2
    raw = [row for row in data["magnetic"] if start <= row["time"] < end]
    rows = [row for row in raw if row["quality"] and row["support_end"] <= end]
    result: dict[str, Any] = {
        "window_id": identifier,
        "kind": kind,
        "anchor_utc": _iso(anchor),
        "window_start_utc": _iso(start),
        "window_end_utc": _iso(end),
        "status": "eligible",
        "reasons": [],
        "excluded_quality_rows": sum(not row["quality"] for row in raw),
        "excluded_future_support_rows": sum(row["support_end"] > end for row in raw),
        "metrics": {},
    }
    for metric in METRICS:
        usable = [row for row in rows if row[metric] is not None]
        before = [row[metric] for row in usable if row["time"] < middle]
        after = [row[metric] for row in usable if row["time"] >= middle]
        coverages = {
            "coverage": _coverage(usable, start, end, p["sharp_cadence_seconds"]),
            "first_half_coverage": _coverage(
                usable, start, middle, p["sharp_cadence_seconds"]
            ),
            "second_half_coverage": _coverage(
                usable, middle, end, p["sharp_cadence_seconds"]
            ),
        }
        sufficient = (
            min(coverages.values()) >= p["min_coverage"]
            and bool(before)
            and bool(after)
        )
        result["metrics"][metric] = coverages | {
            "unit": METRICS[metric],
            "n_first": len(before),
            "n_second": len(after),
            "first_half_median": median(before) if sufficient else None,
            "second_half_median": median(after) if sufficient else None,
            "median_difference": median(after) - median(before) if sufficient else None,
        }
        if not sufficient:
            result["reasons"].append(f"insufficient_{metric}_coverage")
    xrs_end = anchor + guard if kind == "control" else anchor
    xrs_raw = [row for row in data["xrs"] if start <= row["time"] < xrs_end]
    xrs = [
        row
        for row in xrs_raw
        if row["quality"] and row["flux"] is not None and row["support_end"] <= xrs_end
    ]
    result["excluded_future_xrs_support_rows"] = sum(
        row["support_end"] > xrs_end for row in xrs_raw
    )
    result["xrs_coverage"] = _coverage(xrs, start, xrs_end, p["xrs_cadence_seconds"])
    if result["xrs_coverage"] < p["min_coverage"]:
        result["reasons"].append("insufficient_xrs_coverage")
    threshold = goes_flux(p["min_class"])
    contaminating = []
    for event in data["events"]:
        if event["event_id"] == identifier or event["flux"] < threshold:
            continue
        relevant = (
            kind == "control"
            or not event["verified"]
            or event["region"] == p["noaa_ar"]
        )
        # Missing catalogue end times are right-censored, not fabricated or
        # assumed complete. An earlier censored M+ event is conservatively active.
        if (
            relevant
            and event["start"] < xrs_end
            and (event["end"] is None or event["end"] >= start)
        ):
            contaminating.append(event["event_id"])
    result["contaminating_event_ids"] = contaminating
    if contaminating:
        result["reasons"].append("catalogued_target_class_event_contamination")
    if kind == "control" and any(row["flux"] >= threshold for row in xrs):
        result["reasons"].append(
            "full_disk_xrs_target_class_activity_no_region_attribution"
        )
    if result["reasons"]:
        result["status"] = "rejected"
    return result


def _select(data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    p = data["parameters"]
    start, end = parse_utc(p["target_start_utc"]), parse_utc(p["target_end_utc"])
    threshold = goes_flux(p["min_class"])
    windows, screening = [], []
    selected_events = 0
    for event in data["events"]:
        if not start <= event["start"] < end or event["flux"] < threshold:
            continue
        reason = ""
        if not event["verified"]:
            reason = "unverified_region"
        elif event["region"] != p["noaa_ar"]:
            reason = "other_region"
        elif selected_events >= p["max_events"]:
            reason = "beyond_first_chronological_event_limit"
        if reason:
            screening.append(
                {"event_id": event["event_id"], "status": "excluded", "reason": reason}
            )
            continue
        window = _window(data, event["start"], "event", event["event_id"])
        window["goes_class"] = event["goes_class"]
        window["event_end_missing"] = event["end"] is None
        windows.append(window)
        # Freeze candidates before quality screening: rejected early candidates
        # must not be replaced by later events to fill a desired result count.
        selected_events += 1
    # The grid is frozen from the target start, not optimized against metric values.
    step = timedelta(hours=p["window_hours"], minutes=p["guard_minutes"])
    anchor = start
    grid_index, selected_controls = 0, 0
    while anchor < end:
        if selected_controls >= p["max_controls"]:
            break
        window = _window(data, anchor, "control", f"control_{grid_index:03d}")
        windows.append(window)
        selected_controls += window["status"] == "eligible"
        anchor += step
        grid_index += 1
    return windows, screening


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _plots(
    data: dict[str, Any], windows: list[dict[str, Any]], output: Path
) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    figure, axes = plt.subplots(
        4, 1, figsize=(11, 10), sharex=True, constrained_layout=True
    )
    for axis, (metric, unit) in zip(axes[:3], METRICS.items(), strict=True):
        rows = data["magnetic"]
        # NaN breaks lines through invalid observations; gaps are not zero-filled.
        values = [
            row[metric] if row["quality"] and row[metric] is not None else float("nan")
            for row in rows
        ]
        axis.plot([row["time"] for row in rows], values, linewidth=0.8)
        axis.set_ylabel(metric + "\n" + unit, fontsize=8)
    xrs = data["xrs"]
    axes[3].plot(
        [row["time"] for row in xrs],
        [
            row["flux"]
            if row["quality"] and row["flux"] is not None and row["flux"] > 0
            else float("nan")
            for row in xrs
        ],
        linewidth=0.6,
    )
    axes[3].set_yscale("log")
    axes[3].set_ylabel("Full-disk XRS\nW/m²; no region attribution")
    axes[3].set_xlabel("Time (UTC)")
    for window in windows:
        if window["status"] == "eligible":
            for axis in axes:
                axis.axvline(
                    parse_utc(window["anchor_utc"]),
                    color="tab:red" if window["kind"] == "event" else "tab:green",
                    alpha=0.5,
                    linewidth=0.8,
                )
    figure.suptitle(
        "B1 descriptive inputs; red = event, green = control; no scientific review"
    )
    figure.savefig(output / "timeseries.png", dpi=150)
    plt.close(figure)
    return ["timeseries.png"]


def _report(
    data: dict[str, Any], windows: list[dict[str, Any]], receipt: dict[str, Any]
) -> str:
    eligible = [row for row in windows if row["status"] == "eligible"]
    p = data["parameters"]
    lines = [
        "# B1 deterministic descriptive calculation (not scientifically reviewed)",
        "",
        "Execution status: `computed_not_scientifically_reviewed`. This is script computation, not a JW Agent run.",
        "",
        "## Descriptive results",
        "",
        f"NOAA AR {p['noaa_ar']}; HARP {p['harpnum']}; fixed target interval [{p['target_start_utc']}, {p['target_end_utc']}).",
        f"Window: {p['window_hours']:g} h ending {p['guard_minutes']:g} min before event start/control anchor; minimum coverage {p['min_coverage']:.0%} in the whole and each half magnetic window.",
        f"Eligible events: {receipt['eligible_events']}; eligible controls: {receipt['eligible_controls']}. Scope: {receipt['comparison_scope']}.",
        "",
        "Event candidates are the first target-class events with verified target-region attribution in time order, locked before quality screening; rejected candidates are not replaced by later events. Controls follow the frozen grid from target_start_utc. Every evaluated rejection is retained in windows.json. Missing values are never imputed as zero.",
        "",
        "| Window | Kind | Metric | First-half median | Second-half median | Difference | Unit |",
        "| --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for window in eligible:
        for metric, result in window["metrics"].items():
            lines.append(
                f"| {window['window_id']} | {window['kind']} | {metric} | {result['first_half_median']:.6g} | {result['second_half_median']:.6g} | {result['median_difference']:.6g} | {result['unit']} |"
            )
    lines += [
        "",
        "## Limitations and interpretation",
        "",
        "These are case descriptions, with no significance test, probability estimate, prediction score, causal identification, or mechanism validation. A numerical difference is not evidence of a statistically resolved change; supplied uncertainties remain in the input snapshot and are not propagated here.",
        "USFLUX, TOTPOT and MEANSHR share one magnetic measurement source and are not three independent lines of evidence. TOTPOT is a photospheric proxy in erg/cm, not coronal free energy in erg.",
        "Full-disk XRS cannot assign a flare to an active region. Controls require both valid XRS coverage and no listed target-class contamination, and conservatively exclude target-class full-disk XRS activity; this does not establish absence of weaker, brief, or uncatalogued events. Event prewindows with other verified same-region or uncertain-region target-class events are rejected.",
        "Missing catalogue end times remain missing: target events can use their observed start for prewindow alignment, while other target-class events with missing ends are conservatively right-censored for contamination checks. XRS averages whose support extends past the analysis cutoff are excluded.",
        "Windows from one active region can overlap and are dependent; sample points are not independent events. No magnetic precursor specificity is established without eligible controls. Uncertainty, catalogue completeness and physical interpretation require subsequent independent review.",
        "No hypotheses are generated or mechanisms presumed by this tool. The later JW task must report support, counterexamples, alternative explanations and remaining tests.",
        "",
        "## Data sources (must remain in subsequent reports)",
        "",
        "Canonical input hashes are verified against the explicitly bound input bytes. Raw-source path/hash statements are acquisition-adapter lineage and are not reread by this tool; no manifest path grants extra file access.",
        "",
    ]
    if data["source_context"]:
        lines += [
            "### Source handling and acquisition limitations",
            "",
            "The following acquisition notes are carried forward verbatim as structured metadata; they are source declarations, not additional file-access instructions. They preserve selection rationale, transport limitations, time and unit decisions, and the stated region mapping.",
            "The effective candidate rule for this calculation is frozen before quality screening as described above. Any acquisition note that says 'eligible' does not permit replacement of a rejected early event. Retrospectively processed source versions do not establish that these values were available in real time; this case is not a forecasting evaluation.",
            "",
            "```json",
            json.dumps(
                data["source_context"], ensure_ascii=False, indent=2, allow_nan=False
            ),
            "```",
            "",
            "### Source references and hashes",
            "",
        ]
    for index, source in enumerate(data["sources"], 1):
        lines += [
            f"{index}. **{source['title']}** — {source['url']}",
            f"   - Role: {source['role']}; verification: {source['hash_verification']}",
            f"   - Terms/access: {source['license_or_terms']}",
            f"   - Citation: {source['citation']}",
            f"   - Declared/resolved path: `{source['resolved_path']}`",
            f"   - SHA-256: `{source['sha256']}`",
        ]
    if p.get("synthetic") or any(source.get("synthetic") for source in data["sources"]):
        lines += [
            "",
            "**SYNTHETIC TEST DATA: this output is engineering verification, not a solar observation result.**",
        ]
    lines += [
        "",
        "Inputs, frozen parameters, source manifest and hashes are in inputs/ and provenance.json. windows.csv/windows.json retain eligibility and rejection reasons; comparisons.csv holds all descriptive values. Re-run using the archived code and the recorded Python/package environment into a new output directory.",
        "",
    ]
    return "\n".join(lines)


def run_b1_analysis(
    magnetic_path: str | Path,
    events_path: str | Path,
    xrs_path: str | Path,
    parameters_path: str | Path,
    sources_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Calculate bounded descriptive windows, recording success or failure.

    ``output_dir`` must not exist. Failure evidence is written there and the
    exception is re-raised. No data are fetched, no source is modified, and no
    hypothesis/causal result is claimed. Use validate_b1_inputs for preparation.
    """
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    paths = list(
        map(Path, (magnetic_path, events_path, xrs_path, parameters_path, sources_path))
    )
    try:
        data = _load(*paths)
        snapshot = output / "inputs"
        snapshot.mkdir()
        names = (
            "magnetic.csv",
            "events.csv",
            "xrs.csv",
            "parameters.json",
            "source_manifest.json",
        )
        for path, name in zip(paths, names, strict=True):
            shutil.copyfile(path, snapshot / name)
        packages = {}
        for name in ("numpy", "pandas", "matplotlib", "astropy"):
            try:
                packages[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                packages[name] = "not installed"
        provenance = {
            "created_utc": _iso(datetime.now(UTC)),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "packages": packages,
            "input_files": [
                {
                    "path": str(path.resolve()),
                    "snapshot": "inputs/" + name,
                    "sha256": _sha(path),
                }
                for path, name in zip(paths, names, strict=True)
            ],
            "source_files": data["sources"],
            "source_context": data["source_context"],
            "code_sha256": _sha(Path(__file__)),
            "parameters_effective": data["parameters"],
            "execution_kind": "offline_deterministic_script_not_agent",
        }
        _json(output / "provenance.json", provenance)
        shutil.copyfile(Path(__file__), output / "b1_compute_snapshot.py")
        windows, screening = _select(data)
        eligible_events = sum(
            row["status"] == "eligible" and row["kind"] == "event" for row in windows
        )
        eligible_controls = sum(
            row["status"] == "eligible" and row["kind"] == "control" for row in windows
        )
        receipt = {
            "status": "computed_not_scientifically_reviewed",
            "eligible_events": eligible_events,
            "eligible_controls": eligible_controls,
            "comparison_scope": "descriptive_event_control_comparison"
            if eligible_events and eligible_controls
            else "event_description_only"
            if eligible_events
            else "insufficient_eligible_events",
            "scientific_review": "not_performed",
            "agent_run": False,
            "output_dir": str(output),
        }
        _json(output / "windows.json", windows)
        _json(output / "event_screening.json", screening)
        flat_windows = [
            {key: value for key, value in row.items() if key != "metrics"}
            | {
                "reasons": ";".join(row["reasons"]),
                "contaminating_event_ids": ";".join(row["contaminating_event_ids"]),
            }
            for row in windows
        ]
        _write_csv(
            output / "windows.csv",
            flat_windows,
            [
                "window_id",
                "kind",
                "anchor_utc",
                "window_start_utc",
                "window_end_utc",
                "status",
                "reasons",
                "xrs_coverage",
                "excluded_quality_rows",
                "excluded_future_support_rows",
                "excluded_future_xrs_support_rows",
                "contaminating_event_ids",
                "goes_class",
                "event_end_missing",
            ],
        )
        comparisons = [
            {
                "window_id": window["window_id"],
                "kind": window["kind"],
                "status": window["status"],
                "metric": metric,
            }
            | values
            for window in windows
            for metric, values in window["metrics"].items()
        ]
        _write_csv(
            output / "comparisons.csv",
            comparisons,
            [
                "window_id",
                "kind",
                "status",
                "metric",
                "unit",
                "coverage",
                "first_half_coverage",
                "second_half_coverage",
                "n_first",
                "n_second",
                "first_half_median",
                "second_half_median",
                "median_difference",
            ],
        )
        receipt["figures"] = _plots(data, windows, output)
        (output / "report.md").write_text(
            _report(data, windows, receipt), encoding="utf-8"
        )
        _json(output / "receipt.json", receipt)
        _json(
            output / "output_hashes.json",
            {
                str(path.relative_to(output)): _sha(path)
                for path in sorted(output.rglob("*"))
                if path.is_file()
            },
        )
        return receipt
    except Exception as exc:
        _json(
            output / "failure.json",
            {
                "status": "failed_no_scientific_result",
                "error_type": type(exc).__name__,
                "message": str(exc),
                "created_utc": _iso(datetime.now(UTC)),
                "inputs": [str(path.resolve()) for path in paths],
            },
        )
        raise
