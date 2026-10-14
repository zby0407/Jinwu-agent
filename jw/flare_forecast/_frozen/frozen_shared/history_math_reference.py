"""Frozen +16..+40h history-only dataset preparation; never fit or issue."""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import platform
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

UTC = timezone.utc
CONTRACT_SHA = "2efb047587b5f011462a7c7531150397f2d092679c51616f5621b171aac0b552"
REFERENCE_SHA = "921acd866fc3ad909799016687b38cfc91e9d60d30c1d705ed48e2c4d31802b1"
SOURCE01_SHA = "5755f0b5f071dd4dd7457370657d77adfa46b84075c8476bd9efa6728b52c091"
SOURCE02_SHA = "4e640a0262275863f5eec9239eb734085e6b123322994f70078b563916f9f6c4"
FEATURES = [f"history_{h}h_{a}" for h in (24, 72, 168)
            for a in ("c_count", "m_count", "x_count", "max_peak_w_m2")]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_csv(path, rows):
    keys = list(dict.fromkeys(k for row in rows for k in row))
    with Path(path).open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def load_reference():
    path = Path(__file__).with_name("historical_prepare_reference.py")
    if digest(path) != REFERENCE_SHA:
        raise ValueError("Frozen parser snapshot changed")
    spec = importlib.util.spec_from_file_location("history_source_reference", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MinuteIndex:
    """Explicit domain, including 2026; all unavailable minutes remain missing."""
    def __init__(self, minutes, start, end):
        self.first = int(start.timestamp()) // 60
        self.end = int(end.timestamp()) // 60
        if self.end <= self.first:
            raise ValueError("Invalid minute domain")
        valid = np.zeros(self.end - self.first, dtype=bool)
        offsets = np.fromiter((x - self.first for x in minutes if self.first <= x < self.end), dtype=np.int64)
        valid[offsets] = True
        self.prefix = np.r_[0, np.cumsum(valid, dtype=np.int64)]
        delta = np.diff(np.r_[False, ~valid, False].astype(np.int8))
        self.gap_starts = np.flatnonzero(delta == 1)
        self.gap_ends = np.flatnonzero(delta == -1)

    def stats(self, start, hours):
        if start.second or start.microsecond or start.tzinfo is None:
            raise ValueError("Explicit UTC minute boundary required")
        a = int(start.timestamp()) // 60 - self.first
        b = a + hours * 60
        lo, hi = max(0, a), min(b, self.end - self.first)
        if lo >= hi:
            return 0.0, hours * 60
        count = int(self.prefix[hi] - self.prefix[lo])
        x = np.searchsorted(self.gap_ends, lo, side="right")
        y = np.searchsorted(self.gap_starts, hi, side="left")
        spans = np.minimum(self.gap_ends[x:y], hi) - np.maximum(self.gap_starts[x:y], lo)
        gap = max(int(spans.max()) if len(spans) else 0, max(0, -a), max(0, b - (self.end - self.first)))
        return count / (hours * 60), gap


def history_features(reference, events):
    result, used = {}, []
    for h in (24, 72, 168):
        candidates = events.window(reference - timedelta(hours=h), reference)
        past = [e for e in candidates if e["end"] is not None and e["end"] <= reference - timedelta(hours=3)]
        for name, threshold in (("c", 1e-6), ("m", 1e-5), ("x", 1e-4)):
            result[f"history_{h}h_{name}_count"] = sum(e["peak"] >= threshold for e in past)
        result[f"history_{h}h_max_peak_w_m2"] = max((e["peak"] for e in past), default=0.0)
        used += past
    result["history_latest_event_end_utc"] = max((e["end"] for e in used), default=None)
    return result


def future_label(reference, events):
    start, end = reference + timedelta(hours=16), reference + timedelta(hours=40)
    rows = events.window(start, end)
    peak = max((e["peak"] for e in rows), default=0.0)
    return {"label_c1": int(peak >= 1e-6), "label_m1": int(peak >= 1e-5),
            "y_class": 2 if peak >= 1e-5 else int(peak >= 1e-6),
            "catalog_target_max_peak_w_m2": peak, "future_valid_event_count": len(rows)}


def split_name(reference, splits):
    day = reference.strftime("%Y-%m-%d")
    return next((name for name, (a, b) in splits.items() if a <= day <= b), "excluded")


def safe_path(root, rel):
    path = Path(rel)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Unsafe source path")
    result = (root / path).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError("Source path escaped input directory")
    return result


def checked_bundle(root, contract_path):
    if digest(contract_path) != CONTRACT_SHA:
        raise ValueError("Frozen live contract changed")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if contract["feature_columns"] != FEATURES or contract["historical_label_offset_hours"] != 16 or contract["magnetic_predictors"] is not False:
        raise ValueError("Frozen feature/horizon policy changed")
    manifest_path = root / "source_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("contract_sha256") != CONTRACT_SHA or not manifest.get("input_readonly"):
        raise ValueError("Source manifest contract/read-only mismatch")
    before = {str(manifest_path): digest(manifest_path), str(contract_path): digest(contract_path)}
    seen = set()
    for row in manifest["files"]:
        if row["path"] in seen:
            raise ValueError("Duplicate source path")
        seen.add(row["path"])
        path = safe_path(root, row["path"])
        if digest(path) != row["sha256"] or path.stat().st_size != row["bytes"]:
            raise ValueError("Source integrity mismatch: " + row["path"])
        before[str(path)] = row["sha256"]
    for role, expected in (("source_amendment_01", SOURCE01_SHA), ("source_amendment_02", SOURCE02_SHA)):
        rows = [r for r in manifest["files"] if r["role"] == role]
        if len(rows) != 1 or rows[0]["sha256"] != expected:
            raise ValueError("Missing frozen source amendment")
    origins = contract["historical_sources"]
    for role, expected in (("historical_source_manifest", origins["manifest_sha256"]),
                           ("old_calendar", origins["old_all_calendar_sha256"]),
                           ("old_valid_events", origins["old_valid_events_sha256"])):
        rows = [r for r in manifest["files"] if r["role"] == role]
        if len(rows) != 1 or rows[0]["sha256"] != expected:
            raise ValueError("Historical source binding changed")
    return contract, manifest, before


def stitch_events(science, realtime, boundary, cutoff, core):
    # Numeric peaks only; never derive numerical flux from a rounded class label.
    translated = []
    for row in realtime:
        translated.append({"start_time": row.get("begin_time"), "end_time": row.get("end_time"),
                           "time": row.get("max_time"), "xrsb_irrad": row.get("max_xrlong"),
                           "flare_id": "SWPC:" + str(row.get("begin_time")),
                           "flare_class": row.get("max_class"), "source_file": "swpc_flares_7day.json"})
    rt_events, rt_bad = core.normalize_events(translated)
    sc_events, sc_bad = core.normalize_events(science)
    retained = [e for e in sc_events if e["start"] < boundary and e["start"] < cutoff]
    retained += [e for e in rt_events if boundary <= e["start"] < cutoff]
    # The source partition prevents duplication without outcome-dependent matching.
    audit = []
    for e in rt_events:
        matches = [s for s in sc_events if abs((s["start"] - e["start"]).total_seconds()) <= 300]
        if matches:
            s = min(matches, key=lambda x: abs((x["start"] - e["start"]).total_seconds()))
            audit.append({"realtime_start_utc": core.utc_text(e["start"]), "science_start_utc": core.utc_text(s["start"]),
                          "realtime_peak": e["peak"], "science_peak": s["peak"],
                          "realtime_end": core.utc_text(e["end"]) if e["end"] else "",
                          "science_end": core.utc_text(s["end"]) if s["end"] else "",
                          "matching_rule": "nearest start within 300s, audit only; no merged estimate"})
    return retained, sc_bad + rt_bad, audit


def prepare(input_dir, contract_path, output_dir):
    started = time.monotonic()
    if output_dir.exists():
        raise ValueError("Output exists; refusing overwrite")
    output_dir.mkdir(parents=True)
    core = load_reference()
    contract, manifest, before = checked_bundle(input_dir, contract_path)
    by_role = {}
    for row in manifest["files"]:
        by_role.setdefault(row["role"], []).append(row)
    def one(role):
        values = by_role.get(role, [])
        if len(values) != 1:
            raise ValueError("Expected exactly one source role: " + role)
        return safe_path(input_dir, values[0]["path"])
    if len(by_role.get("historical_goes_events", [])) != 8:
        raise ValueError("Eight historical annual catalogues required")
    raw = []
    for entry in by_role["historical_goes_events"]:
        with safe_path(input_dir, entry["path"]).open(encoding="utf-8", newline="") as f:
            raw += [{**r, "source_file": entry["path"]} for r in csv.DictReader(f)]
    historical_events, event_bad = core.normalize_events(raw)
    event_index = core.EventIndex(historical_events)
    with one("old_calendar").open(encoding="utf-8", newline="") as f:
        calendar = list(csv.DictReader(f))
    if len(calendar) != 2192 or len({r["issue_utc"] for r in calendar}) != 2192:
        raise ValueError("Frozen historical calendar lost/duplicated anchors")
    # Re-read all archived coverage, preserving original source cropping rules.
    all_minutes, coverage_audit = set(), []
    for entry in manifest["files"]:
        role = entry["role"]
        path = safe_path(input_dir, entry["path"])
        if role == "historical_legacy_coverage":
            valid = core.coverage_valid_minutes(path)
            info = {"valid_minutes": len(valid), "clock": "declared legacy UTC"}
        elif role in {"historical_science_coverage", "live_goesr_coverage"}:
            valid, info = core.science_coverage_valid_minutes(path)
            if role == "historical_science_coverage":
                valid = core.science_crop_minutes(valid, int(entry["parameters"]["satellite"]), int(entry["parameters"]["year"]))
            else:
                a = int(core.parse_utc(contract["observation_window"]["start"]).timestamp()) // 60
                b = int(core.parse_utc(contract["data_cutoff"]).timestamp()) // 60
                valid = {m for m in valid if a <= m < b}
        else:
            continue
        all_minutes |= valid
        coverage_audit.append({"path": entry["path"], "source_sha256": entry["sha256"],
                               "role": role, "selected_valid_minutes": len(valid), **info})
    index = MinuteIndex(all_minutes, datetime(2010, 12, 1, tzinfo=UTC), core.parse_utc(contract["data_cutoff"]))
    minimum = contract["coverage_rule"]["minimum_fraction"]
    maximum = contract["coverage_rule"]["maximum_gap_minutes"]
    all_rows, history_checks = [], 0
    invalid_unlocatable = [a for a in event_bad if "missing_or_invalid_start_time" in a["reasons"] and not a.get("raw_peak_time")]
    if invalid_unlocatable:
        raise ValueError("Historical invalid event has no locatable UTC date")
    for old in calendar:
        reference = core.parse_utc(old["issue_utc"])
        features = history_features(reference, event_index)
        for k in FEATURES:
            if float(old[k]) != float(features[k]):
                raise ValueError("Historical feature differs from frozen all-calendar: " + old["issue_utc"] + "/" + k)
            history_checks += 1
        latest = features.pop("history_latest_event_end_utc")
        past_frac, past_gap = index.stats(reference - timedelta(hours=168), 168)
        target_start, target_end = reference + timedelta(hours=16), reference + timedelta(hours=40)
        future_frac, future_gap = index.stats(target_start, 24)
        reasons = []
        split = split_name(reference, contract["splits"])
        if split == "excluded":
            reasons.append("outside_registered_split")
        if past_frac < minimum or past_gap > maximum:
            reasons.append("past168_coverage_failed")
        if future_frac < minimum or future_gap > maximum:
            reasons.append("shifted_future24_coverage_failed")
        uncertain = [a for a in event_bad if any(t in a["reasons"] for t in ("missing_or_invalid_start_time", "missing_or_nonpositive_peak_flux", "end_before_start"))
                     and (stamp := core.parse_utc(a.get("raw_start_time") or a.get("raw_peak_time"), catalog_naive_is_utc=True)) is not None
                     and reference - timedelta(hours=168) <= stamp < target_end]
        if uncertain:
            reasons.append("invalid_catalog_event_in_relevant_window")
        labels = future_label(reference, event_index)
        missing_end = sum(e["end"] is None for e in event_index.window(reference - timedelta(hours=168), reference))
        all_rows.append({"issue_utc": core.utc_text(reference), "data_cutoff": core.utc_text(reference),
                         "target_start_utc": core.utc_text(target_start), "target_end_utc": core.utc_text(target_end),
                         "status": "excluded" if reasons else "eligible", "split": split,
                         "exclusion_reasons": "|".join(reasons), **features,
                         "history_latest_event_end_utc": core.utc_text(latest) if latest else "",
                         "feature_max_observed_utc": core.utc_text(latest) if latest else "",
                         "history_missing_end_event_count": missing_end,
                         **{k: (v if future_frac >= minimum and future_gap <= maximum and not uncertain else None)
                            for k, v in labels.items()},
                         "past_168h_coverage_fraction": past_frac, "past_168h_max_gap_minutes": past_gap,
                         "future_coverage_fraction": future_frac, "future_max_gap_minutes": future_gap})
    cutoff = core.parse_utc(contract["data_cutoff"])
    with one("live_science_events").open(encoding="utf-8", newline="") as f:
        live_science = [{**r, "source_file": "science_events_2026.csv"} for r in csv.DictReader(f)]
    rt = json.loads(one("live_realtime_events").read_text(encoding="utf-8"))
    amendment = json.loads(one("source_amendment_02").read_text(encoding="utf-8"))
    boundary = core.parse_utc(amendment["event_stitch_boundary_utc"])
    live_events, live_bad, overlap = stitch_events(live_science, rt, boundary, cutoff, core)
    live_history = history_features(cutoff, core.EventIndex(live_events))
    latest = live_history.pop("history_latest_event_end_utc")
    live_fraction, live_gap = index.stats(cutoff - timedelta(hours=168), 168)
    live_reasons = []
    if live_fraction < minimum or live_gap > maximum:
        live_reasons.append("live_past168_coverage_failed")
    relevant_invalid = [a for a in live_bad if any(t in a["reasons"] for t in ("missing_or_invalid_start_time", "missing_or_nonpositive_peak_flux", "end_before_start"))
                        and (stamp := core.parse_utc(a.get("raw_start_time") or a.get("raw_peak_time"), catalog_naive_is_utc=True)) is not None
                        and cutoff - timedelta(hours=168) <= stamp < cutoff]
    if relevant_invalid:
        live_reasons.append("invalid_live_event_in_history_window")
    if latest and latest > cutoff - timedelta(hours=3):
        raise ValueError("Late-ended event leaked into live features")
    live_row = {"data_cutoff": contract["data_cutoff"], "issue_utc": contract["data_cutoff"],
                "target_start_utc": contract["prediction_window"]["start"], "target_end_utc": contract["prediction_window"]["end"],
                "status": "excluded" if live_reasons else "eligible", "exclusion_reasons": "|".join(live_reasons),
                **live_history, "history_latest_event_end_utc": core.utc_text(latest) if latest else "",
                "feature_max_observed_utc": core.utc_text(latest) if latest else "",
                "past_168h_coverage_fraction": live_fraction, "past_168h_max_gap_minutes": live_gap,
                "future_coverage_fraction": None, "future_max_gap_minutes": None,
                "future_observability": "unknown_until_target_completed", "actual_issue": "not_generated_by_preparation"}
    eligible = [r for r in all_rows if r["status"] == "eligible"]
    split_counts = {s: {str(c): sum(r["split"] == s and r["y_class"] == c for r in eligible) for c in (0, 1, 2)} for s in contract["splits"]}
    after = {p: digest(p) for p in before}
    if before != after:
        raise ValueError("Input changed during preparation")
    validation = {"status": "failed" if live_reasons else "passed", "failures": live_reasons,
                  "historical_feature_checks": history_checks, "historical_feature_mismatches": 0,
                  "live_past168_fraction": live_fraction, "live_past168_max_gap_minutes": live_gap,
                  "all_input_hashes_unchanged": True, "contract_sha256": CONTRACT_SHA,
                  "operator_or_JW": "caller identity requires external actual-tool receipt; this helper cannot self-certify",
                  "no_fit_no_prediction": True}
    summary = {"schema_version": "history-horizon-preparation-summary-v1", "validation": validation,
               "calendar_rows": len(all_rows), "eligible_historical_rows": len(eligible), "split_class_counts": split_counts,
               "live_features": live_row, "live_stitch_boundary_utc": core.utc_text(boundary),
               "live_source_overlap_matches": len(overlap), "catalog_events": len(historical_events),
               "historical_missing_end_events": sum(e["end"] is None for e in historical_events),
               "source_manifest_sha256": digest(input_dir / "source_manifest.json"),
               "source_groups": {r: len(v) for r, v in by_role.items()},
               "source_policy": "Original historical NOAA source bytes retained; 2026 science before fixed Oct5 boundary, frozen SWPC RT afterward; strict observed-minute union",
               "limitations": contract["limitations"] + ["RT/science event definitions differ; stitching discrepancy is retained; missing ends omitted only from history", "No magnetic qualification is inherited into this new history-only task", "Past-coverage quality is not proof of complete event detection; event catalogue omissions remain possible"],
               "execution_seconds": round(time.monotonic() - started, 3)}
    write_csv(output_dir / "all_daily_windows.csv", all_rows)
    write_csv(output_dir / "daily_instances.csv", eligible)
    write_csv(output_dir / "live_features.csv", [live_row])
    export_events = []
    for scope, records in (("historical", historical_events), ("live", live_events)):
        for e in records:
            export_events.append({"scope": scope, "source_file": e["source_file"], "flare_id": e["flare_id"],
                                  "start_utc": core.utc_text(e["start"]), "end_utc": core.utc_text(e["end"]) if e["end"] else "",
                                  "peak_utc": core.utc_text(e["peak_time"]) if e["peak_time"] else "", "peak_w_m2": e["peak"]})
    write_csv(output_dir / "valid_events.csv", export_events)
    write_csv(output_dir / "event_audit.csv", [{"scope": "historical", **e} for e in event_bad] + [{"scope": "live", **e} for e in live_bad])
    write_json(output_dir / "source_overlap_audit.json", overlap)
    write_json(output_dir / "coverage_source_audit.json", coverage_audit)
    write_json(output_dir / "input_integrity.json", {"before": before, "after": after, "unchanged": before == after})
    write_json(output_dir / "summary.json", summary)
    write_json(output_dir / "validation.json", validation)
    files = [{"path": f.name, "sha256": digest(f), "bytes": f.stat().st_size} for f in sorted(output_dir.iterdir()) if f.is_file()]
    prepared_manifest = {"schema_version": "history-horizon-prepared-v1", "contract_sha256": CONTRACT_SHA,
                         "source_manifest_sha256": digest(input_dir / "source_manifest.json"),
                         "source_amendment_01_sha256": SOURCE01_SHA, "source_amendment_02_sha256": SOURCE02_SHA,
                         "features": FEATURES, "files": files, "source_versions_terms": manifest["files"],
                         "environment": {"python": sys.version, "numpy": np.__version__, "platform": platform.platform()},
                         "code": [{"path": Path(__file__).name, "sha256": digest(__file__)},
                                  {"path": "historical_prepare_reference.py", "sha256": REFERENCE_SHA}],
                         "qualification": contract["qualification"], "parameters": {"target_offset_hours": 16, "target_width_hours": 24, "history_end_lag_hours": 3, "coverage_fraction_min": minimum, "max_gap_minutes": maximum},
                         "actual_JW_execution": "Requires external native execute receipt, not granted by this helper"}
    write_json(output_dir / "prepared_manifest.json", prepared_manifest)
    print(json.dumps({"output_dir": str(output_dir), "status": validation["status"], "eligible_rows": len(eligible), "live_fraction": live_fraction, "live_gap": live_gap, "manifest_sha256": digest(output_dir / "prepared_manifest.json")}), flush=True)
    if live_reasons:
        raise ValueError("Live input qualification failed; output reasons preserved")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        prepare(args.input_dir.resolve(), args.contract.resolve(), args.output_dir.resolve())
    except Exception:
        if args.output_dir.exists():
            (args.output_dir / "failure.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise


if __name__ == "__main__":
    main()
