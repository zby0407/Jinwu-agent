"""Offline, provenance-checked adapters for the frozen NOAA 11158 B1 case.

This module prepares inputs only. It never selects events, compares windows,
calls an agent, or claims that an observed association is causal.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from jw.b1_compute import goes_flux, parse_utc, tai_to_utc, validate_b1_inputs

DATASET_IDS = {
    "magnetic": "b1-sharp-11158-v1",
    "events": "b1-goes-events-2011-v1",
    "xrs": "b1-goes15-xrs-201102-v1",
    "parameters": "b1-case-parameters-v1",
    "sources": "b1-case-sources-v1",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"Refusing empty canonical table: {path.name}")
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def verify_raw_sources(manifest_path: Path) -> tuple[dict[str, Any], dict[str, Path]]:
    """Verify every retained original, rejecting links and escaping paths."""
    manifest_path = manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sources = manifest.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("A nonempty source manifest is required")
    root = manifest_path.parent
    paths: dict[str, Path] = {}
    for source in sources:
        relative = Path(source["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Source path must stay within the acquisition directory")
        cursor = root
        for part in relative.parts:
            cursor /= part
            if cursor.is_symlink() or (
                hasattr(cursor, "is_junction") and cursor.is_junction()
            ):
                raise ValueError("Raw source path must not traverse a link")
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"Missing raw source: {relative}")
        if sha256(path) != source["sha256"]:
            raise ValueError(f"Raw source hash mismatch: {relative}")
        if source["path"] in paths:
            raise ValueError("Duplicate source path")
        paths[source["path"]] = path
    return manifest, paths


def normalize_sharp(
    path: Path, start: datetime, end: datetime
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("status") != 0 or not isinstance(data.get("count"), int):
        raise ValueError("Unsuccessful JSOC response")
    fields = {item["name"]: item["values"] for item in data["keywords"]}
    required = {
        "HARPNUM",
        "NOAA_AR",
        "NOAA_ARS",
        "NOAA_NUM",
        "T_REC",
        "T_OBS",
        "QUALITY",
        "CMASK",
        "CRVAL1",
        "CRLN_OBS",
        "CADENCE",
        "USFLUX",
        "TOTPOT",
        "MEANSHR",
        "ERRVF",
        "ERRTPOT",
        "ERRMSHA",
    }
    if required - fields.keys() or any(
        len(v) != data["count"] for v in fields.values()
    ):
        raise ValueError("Missing JSOC fields or inconsistent column lengths")
    rows, seen, reasons = [], set(), Counter()
    cropped = 0
    for index in range(data["count"]):
        raw = {name: values[index] for name, values in fields.items()}
        if (raw["HARPNUM"], raw["NOAA_AR"], raw["NOAA_ARS"], raw["NOAA_NUM"]) != (
            "377",
            "11158",
            "11158",
            "1",
        ):
            raise ValueError(
                "The frozen case requires one verified HARP 377 / NOAA 11158 mapping"
            )
        if float(raw["CADENCE"]) != 720:
            raise ValueError("Unexpected SHARP cadence")
        time = parse_utc(tai_to_utc(raw["T_OBS"]))
        if not start <= time < end:
            cropped += 1
            continue
        if time in seen:
            raise ValueError("Duplicate SHARP observation time")
        seen.add(time)
        quality = int(raw["QUALITY"], 0)
        lon, observer = _number(raw["CRVAL1"]), _number(raw["CRLN_OBS"])
        central_distance = (
            ((lon - observer + 180) % 360 - 180)
            if lon is not None and observer is not None
            else None
        )
        metrics = [_number(raw[key]) for key in ("USFLUX", "TOTPOT", "MEANSHR")]
        excluded = []
        if quality != 0:
            excluded.append("nonzero_quality")
        if (_number(raw["CMASK"]) or 0) <= 0:
            excluded.append("no_high_confidence_pixels")
        if any(value is None or value < 0 for value in metrics) or (
            metrics[2] is not None and metrics[2] > 180
        ):
            excluded.append("missing_or_invalid_metric")
        if central_distance is None or abs(central_distance) > 60:
            excluded.append("patch_center_outside_60deg_longitude")
        reasons.update(excluded)
        rows.append(
            {
                "time_utc": _iso(time),
                "harpnum": 377,
                "noaa_ar": 11158,
                "usflux_mx": metrics[0],
                "totpot_erg_per_cm": metrics[1],
                "meanshr_deg": metrics[2],
                "quality_valid": str(not excluded).lower(),
                "support_end_utc": _iso(time + timedelta(seconds=675)),
                "source_record_id": f"hmi.sharp_cea_720s[377][{raw['T_REC']}]",
                "err_usflux": _number(raw["ERRVF"]),
                "err_totpot": _number(raw["ERRTPOT"]),
                "err_meanshr": _number(raw["ERRMSHA"]),
                "source_t_rec_tai": raw["T_REC"],
                "source_t_obs_tai": raw["T_OBS"],
                "quality_bits_raw": raw["QUALITY"],
                "cmask": raw["CMASK"],
                "patch_center_stonyhurst_longitude_deg": central_distance,
                "excluded_reasons": ";".join(excluded),
            }
        )
    rows.sort(key=lambda row: row["time_utc"])
    return rows, {
        "raw_rows": data["count"],
        "rows": len(rows),
        "cropped_halo_rows": cropped,
        "quality_valid_rows": sum(r["quality_valid"] == "true" for r in rows),
        "exclusion_reason_counts_nonexclusive": dict(reasons),
    }


def normalize_events(
    path: Path, start: datetime, end: datetime
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Retain all-disk events; only the documented case region is verified."""
    rows, seen = [], set()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for raw in csv.DictReader(stream):
            # Product metadata specifies that these naive source strings are UTC.
            times = {
                key: datetime.strptime(raw[key], "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=UTC
                )
                if raw[key]
                else None
                for key in ("start_time", "time", "end_time")
            }
            if times["start_time"] is None or times["time"] is None:
                raise ValueError(
                    "Missing event onset or peak; cannot assign an observation window"
                )
            if times["start_time"] >= end:
                continue
            if times["end_time"] is not None and times["end_time"] < start:
                continue
            if (
                times["end_time"] is None
                and times["start_time"] < start
                and goes_flux(raw["flare_class"]) < goes_flux("M1.0")
            ):
                continue
            identifier = raw["flare_id"]
            if identifier in seen:
                raise ValueError("Duplicate flare identifier")
            seen.add(identifier)
            verified = raw["active_region"].strip() == "1158"
            rows.append(
                {
                    "event_id": identifier,
                    "start_utc": _iso(times["start_time"]),
                    "peak_utc": _iso(times["time"]),
                    "end_utc": _iso(times["end_time"])
                    if times["end_time"] is not None
                    else "",
                    "goes_class": raw["flare_class"],
                    "noaa_ar": 11158 if verified else "",
                    "region_verified": str(verified).lower(),
                    "source_active_region": raw["active_region"],
                    "source_xrsb_irrad_w_m2": raw["xrsb_irrad"],
                    "source_instrument": raw["xrsb_irrad_source"],
                    "source_peak_saturated": raw["peak_saturated"],
                    "end_time_missing": str(times["end_time"] is None).lower(),
                    "region_mapping_note": "explicit_2011_1158_to_11158"
                    if verified
                    else "not_mapped_conservatively_unknown",
                }
            )
    rows.sort(key=lambda row: row["start_utc"])
    return rows, {
        "rows": len(rows),
        "verified_target_region_rows": sum(
            r["region_verified"] == "true" for r in rows
        ),
        "other_or_unknown_regions_retained_for_conservative_contamination_check": sum(
            r["region_verified"] == "false" for r in rows
        ),
    }


def normalize_xrs(
    paths: list[Path], start: datetime, end: datetime
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import netCDF4
    import numpy as np

    rows, seen = [], set()
    for path in paths:
        # The in-memory constructor also supports Windows non-ASCII paths.
        with netCDF4.Dataset("inmemory", memory=path.read_bytes()) as ds:
            if ds.getncattr("platform") != "GOES-15":
                raise ValueError("Expected the selected GOES-15 science product")
            v = ds.variables
            if (
                v["time"].units != "seconds since 2000-01-01 12:00:00 UTC"
                or v["xrsb_flux"].units != "W/m2"
            ):
                raise ValueError("Unrecognized GOES time or flux units")
            fields = {
                name: v[name][:]
                for name in (
                    "time",
                    "xrsb_flux",
                    "xrsb_flag",
                    "xrsb_num",
                    "xrsb_flag_excluded",
                )
            }
            for i, seconds in enumerate(fields["time"]):
                if np.ma.is_masked(seconds):
                    raise ValueError("Missing GOES record time")
                time = datetime(2000, 1, 1, 12, tzinfo=UTC) + timedelta(
                    seconds=float(seconds)
                )
                if not start <= time < end:
                    continue
                if time in seen:
                    raise ValueError("Duplicate GOES record time")
                seen.add(time)

                def scalar(name: str) -> float | None:
                    value = fields[name][i]
                    return None if np.ma.is_masked(value) else _number(value)

                flux, flag, count, excluded = map(
                    scalar, ("xrsb_flux", "xrsb_flag", "xrsb_num", "xrsb_flag_excluded")
                )
                good = (
                    flux is not None
                    and flux >= 0
                    and flag == 0
                    and count is not None
                    and count >= 27
                )
                rows.append(
                    {
                        "time_utc": _iso(time),
                        "flux_w_m2": flux if flux is not None and flux >= 0 else None,
                        "quality_valid": str(good).lower(),
                        "support_end_utc": _iso(time + timedelta(seconds=60)),
                        "xrsb_flag_raw": flag,
                        "xrsb_num_raw": count,
                        "xrsb_flag_excluded_raw": excluded,
                        "source_file": path.name,
                    }
                )
    rows.sort(key=lambda row: row["time_utc"])
    return rows, {
        "rows": len(rows),
        "quality_valid_rows": sum(r["quality_valid"] == "true" for r in rows),
        "rule": "finite nonnegative flux, xrsb_flag=0, at least 27/30 two-second samples per minute; excluded flags retained separately",
    }


def prepare_b1_inputs(
    manifest_path: str | Path, output_dir: str | Path
) -> dict[str, Any]:
    """Prepare a new directory; preserve partial output and failure on errors."""
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    try:
        manifest, raw_paths = verify_raw_sources(Path(manifest_path))
        scope = manifest["frozen_scope"]
        if (scope["noaa_ar"], scope["harpnum"]) != (11158, 377):
            raise ValueError(
                "This adapter is deliberately limited to NOAA 11158 / HARP 377"
            )
        start, end = map(parse_utc, scope["input_window_utc"])
        if (start, end) != (
            parse_utc("2011-02-10T00:00:00Z"),
            parse_utc("2011-02-18T00:00:00Z"),
        ):
            raise ValueError(
                "The documented region mapping is limited to the frozen interval"
            )
        mapping = manifest["region_mapping"]
        if (
            mapping["raw_value"] != "1158"
            or mapping["canonical_noaa_ar"] != 11158
            or not mapping["evidence"]
        ):
            raise ValueError("The explicit NOAA region mapping is missing")
        for ref in mapping["evidence"]:
            if ref not in raw_paths:
                raise ValueError("Unverified region mapping evidence")
        unit = manifest["semantic_decisions"]["TOTPOT"]
        if unit["analysis_unit"] != "erg/cm" or unit["value_scaling_applied"] != "none":
            raise ValueError("Unexpected TOTPOT unit decision")
        primary = manifest["primary_inputs"]
        magnetic, magnetic_qa = normalize_sharp(raw_paths[primary["sharp"]], start, end)
        events, events_qa = normalize_events(
            raw_paths[primary["flare_report"]], start, end
        )
        xrs, xrs_qa = normalize_xrs(
            [raw_paths[p] for p in primary["goes_xrs"]], start, end
        )
        paths = {
            name: output
            / (name + (".csv" if name in {"magnetic", "events", "xrs"} else ".json"))
            for name in DATASET_IDS
        }
        for name, rows in (("magnetic", magnetic), ("events", events), ("xrs", xrs)):
            _csv(paths[name], rows)
        parameters = {
            "schema_version": "b1-flare-case-v1",
            "noaa_ar": 11158,
            "harpnum": 377,
            "target_start_utc": scope["candidate_window_utc"][0],
            "target_end_utc": scope["candidate_window_utc"][1],
            "min_class": "M1.0",
            "max_events": 3,
            "max_controls": 3,
            "window_hours": 24,
            "guard_minutes": 30,
            "min_coverage": 0.9,
            "sharp_cadence_seconds": 720,
            "xrs_cadence_seconds": 60,
            "scope_note": "Retrospective one-region descriptive case. Freeze the first three verified target-region M1+ events chronologically BEFORE window quality screening; never replace rejected events. Allow insufficient windows and negative findings. No mechanism or predictive skill validation.",
        }
        write_json(paths["parameters"], parameters)
        sources = [
            {**s, "original_role": s.get("role"), "role": "raw_source"}
            for s in manifest["sources"]
        ]
        for name in ("magnetic", "events", "xrs", "parameters"):
            sources.append(
                {
                    "role": "canonical_input",
                    "input_name": name,
                    "path": paths[name].name,
                    "sha256": sha256(paths[name]),
                    "title": f"B1 canonical {name}",
                    "url": f"urn:jinwu:b1:{DATASET_IDS[name]}",
                    "license_or_terms": "Local deterministic derivative of the recorded free public science data; original data terms and citations retained.",
                    "citation": f"jw.b1_data preparation of {DATASET_IDS[name]}; original sources listed in this manifest.",
                }
            )
        final_scope = {**scope, "future_target_policy": parameters["scope_note"]}
        write_json(
            paths["sources"],
            {
                **manifest,
                "frozen_scope": final_scope,
                "acquisition_scope_record": scope,
                "selection_policy_clarification": "The preparation policy above supersedes the earlier ambiguous 'first eligible' wording. No real event windows were analyzed to make this clarification.",
                "sources": sources,
                "raw_source_hash_validation": "All originals verified by preparation; downstream compute never reads raw_source.path",
                "preparation_code_sha256": sha256(Path(__file__)),
            },
        )
        qa = {
            "execution_kind": "deterministic_input_preparation",
            "scientific_status": "not_started",
            "raw_sources_verified": len(raw_paths),
            "source_manifest_sha256": sha256(Path(manifest_path)),
            "magnetic": magnetic_qa,
            "events": events_qa,
            "xrs": xrs_qa,
            "validation": validate_b1_inputs(
                **{f"{name}_path": path for name, path in paths.items()}
            ),
            "limits": [
                "No event-window selection or physical trend analysis performed.",
                "NOAA regions other than the documented target remain unverified, making contamination filtering conservative.",
                "TOTPOT is a photospheric proxy in erg/cm, with preserved source metadata conflict; it is not coronal energy in erg.",
                "The HMI support interval is conservatively estimated from the documented 1350-second averaging; operational causality is not asserted.",
            ],
        }
        write_json(output / "preparation_qa.json", qa)
        return qa
    except Exception as exc:
        write_json(
            output / "failure.json",
            {
                "status": "failed",
                "execution_kind": "deterministic_input_preparation",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        raise


def register_b1_inputs(
    prepared_dir: str | Path, runtime: str | Path
) -> list[dict[str, Any]]:
    """Register five immutable case inputs in a new, isolated B1 project."""
    from jw.workspaces import register_project_data_file

    prepared = Path(prepared_dir).resolve()
    runtime = Path(runtime).resolve()
    if runtime.exists():
        raise FileExistsError(
            "Use a new runtime directory; existing sessions must be preserved"
        )
    paths = {
        name: prepared
        / (name + (".csv" if name in {"magnetic", "events", "xrs"} else ".json"))
        for name in DATASET_IDS
    }
    validate_b1_inputs(**{f"{name}_path": path for name, path in paths.items()})
    records = []
    for name, path in paths.items():
        records.append(
            register_project_data_file(
                runtime,
                path,
                f"b1/v1/{path.name}",
                dataset_id=DATASET_IDS[name],
                project_id="b1-flare",
                provenance={
                    "source_manifest_sha256": sha256(paths["sources"]),
                    "original_prepared_path": str(path),
                    "execution_kind": "deterministic_input_preparation",
                    "scientific_status": "not_started",
                    "sources": json.loads(paths["sources"].read_text(encoding="utf-8"))[
                        "sources"
                    ],
                },
            )
        )
    write_json(
        runtime / "b1_registration.json",
        {
            "project_id": "b1-flare",
            "records": records,
            "scientific_status": "not_started",
        },
    )
    return records
