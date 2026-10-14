"""F2 engineering draft: bounded monthly NRT metadata and pure preparation.

Production is locked until an exact F2 contract/profile is registered. This
draft is exercised with synthetic fixtures only. It never fits or draws.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import threading
import time

REGISTERED_CONTRACT_SHA = "9c5bd161ad09d92442280bafac887cfcd113916f79b94b2b64cd66e1ec8035d2"
REGISTERED_SOURCE_PROFILE_SHA = "5ca489ef243b58614c6587ae43fecc7b0542061cee1bb9e6345cb2d7a860f445"
REGISTERED_SOURCE_PLAN_SHA = "dcb63f9d0371f6b8f7accecdf6fa1ce358cd20b7033dca771b9faeeb0c9e7287"
GATEWAY_SHA = "d9aa0ea42262a444cc7d61479937d7ad0032f8edd67ab07b1b37893f0b3f99c8"
HISTORY_SHA = "7c170b3c8f9429d0d72b12455be9cc5afc100633c7beb8d04b36f6d480c1358a"
CORE_SHA = "921acd866fc3ad909799016687b38cfc91e9d60d30c1d705ed48e2c4d31802b1"
UTC = timezone.utc
HISTORY = [f"history_{hours}h_{kind}" for hours in (24, 72, 168)
           for kind in ("c_count", "m_count", "x_count", "max_peak_w_m2")]
ROLE_BOUNDARY_PURGE_DATES = {"2020-12-31", "2021-12-31", "2022-12-31"}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_modules():
    modules = []
    for name, expected in (("magnetic_source_gateway.py", GATEWAY_SHA), ("history_math_reference.py", HISTORY_SHA)):
        path = Path(__file__).with_name(name)
        if sha(path) != expected:
            raise ValueError("Frozen deterministic dependency differs")
        spec = importlib.util.spec_from_file_location("physical_" + path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    if sha(Path(__file__).with_name("historical_prepare_reference.py")) != CORE_SHA:
        raise ValueError("Frozen NOAA parser dependency differs")
    gateway, history = modules
    return gateway, history, history.load_reference()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def utc(value):
    return value.isoformat().replace("+00:00", "Z") if value is not None else ""


def safe_path(root, relative):
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not (root / path).resolve().is_relative_to(root.resolve()):
        raise ValueError("Source path escapes read-only input bundle")
    return root / path


def production_contract(contract_path, source_profile):
    if REGISTERED_CONTRACT_SHA is None or REGISTERED_SOURCE_PROFILE_SHA is None:
        raise ValueError("F2 production blocked: no registered exact contract/profile")
    if sha(contract_path) != REGISTERED_CONTRACT_SHA or sha(source_profile) != REGISTERED_SOURCE_PROFILE_SHA:
        raise ValueError("Exact frozen F2 contract/profile bytes required")
    contract = json.loads(contract_path.read_text(encoding="utf-8-sig"))
    profile = json.loads(source_profile.read_text(encoding="utf-8-sig"))
    if contract.get("source_profile_sha256") != REGISTERED_SOURCE_PROFILE_SHA:
        raise ValueError("Contract is not bound to actual source profile")
    if contract.get("schema_version") != "jw-physical-source-diagnostic-contract-v1" or contract.get("source_plan_sha256") != REGISTERED_SOURCE_PLAN_SHA:
        raise ValueError("Exact source-only diagnostic registration required")
    if contract.get("forecast_authorized") is not False or contract.get("fit_predict_plot_allowed") is not False:
        raise ValueError("Source-only contract cannot authorize training or forecasts")
    if contract.get("boundary_purge_reference_dates") != sorted(ROLE_BOUNDARY_PURGE_DATES):
        raise ValueError("Exact registered temporal boundary exclusions required")
    validate_profile(profile)
    return contract, profile


def validate_profile(profile):
    if profile.get("schema_version") != "jw-physical-magnetic-source-profile-v1":
        raise ValueError("Unregistered product profile schema")
    if profile.get("source_mode") != "hmi.sharp_cea_720s_nrt":
        raise ValueError("Historical and live magnetic products must be identical NRT")
    if profile.get("accepted_QUALITY_words") != [0]:
        raise ValueError("This engineering draft permits exact QUALITY0 only; no nonzero whitelist")
    if profile.get("nominal_tai_hour") != 13 or profile.get("reference_hour_utc") != 16:
        raise ValueError("Fixed magnetic anchor/reference changed")
    if profile.get("partial_central_coverage_allowed") is not False:
        raise ValueError("Partial visible-patch coverage cannot enter the main model")


def monthly_queries(first_record, last_record, gateway):
    def extract(text):
        match = re.search(r"\[(\d{4}\.\d{2}\.\d{2}_\d{2}:\d{2}:\d{2}(?:\.\d+)?_TAI)\]$", text)
        if match is None:
            raise ValueError("Native schema FirstRecord/LastRecord required")
        gateway.tai_utc(match.group(1))
        return match.group(1)[:7].replace(".", "-")
    first, last = extract(first_record), extract(last_record)
    if first > last:
        raise ValueError("Invalid source availability order")
    year, month = map(int, first.split("-"))
    output = []
    while f"{year:04d}-{month:02d}" <= last:
        key = f"{year:04d}-{month:02d}"
        output.append({"id": "nrt_" + key, "kind": "magnetic_month", "month": key,
                       "parameters": gateway.query_month("hmi.sharp_cea_720s_nrt", year, month, 13, 16)})
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return output


def full_patch_qualification(rows, reference, gateway, available_by=None):
    profile = {"series": "hmi.sharp_cea_720s_nrt", "metrics": gateway.METRICS,
               "anchor_hour_TAI": 13, "reference_hour_UTC": 16,
               "cmd_field": "LON_FWT", "cmd_definition": "Stonyhurst longitude",
               "support_half_width_seconds": 675, "max_abs_cmd_degrees": 60,
               "accepted_QUALITY_words": [0]}
    audit, selected = gateway.qualify_rows(rows, utc(reference), profile, available_by)
    central, unknown = [], 0
    for index, row in enumerate(rows):
        try:
            value = float(row["LON_FWT"])
            if not math.isfinite(value):
                raise ValueError("Nonfinite geometry")
            if abs(value) <= 60:
                central.append(index)
        except (KeyError, TypeError, ValueError):
            unknown += 1
    rejected = sum(audit[i]["status"] != "eligible" for i in central)
    partial = bool(rejected or unknown)
    result = gateway.aggregate(selected)
    if partial:
        result = {"status": "unknown", "reason": "partial_central_patch_coverage",
                  "features": {name: None for name in gateway.FEATURES}}
    clocks = {"hmi_latest_support_end_utc": max((a["support_end_UTC"] for a in selected), default=""),
              "hmi_latest_processing_utc": max((a["produced_UTC"] for a in selected), default="")}
    return {"candidate_central_patch_count": len(central), "rejected_central_patch_count": rejected,
            "unknown_geometry_patch_count": unknown, "partial_patch_coverage": partial,
            "all_magnetic_quality_zero": bool(selected) and not partial,
            "qualification": result, "audit": audit, **clocks}


def coverage_ok(fraction, gap):
    return fraction >= .99 and gap <= 5


def outcome_certificate(reference, events, bad_events, cutoff, domain_qualified, core):
    """The N2 monotone-bit censor rule, with this fixed16UTC reference+0..24."""
    rows = events.window(reference, reference + timedelta(hours=24))
    known = [e for e in rows if e["peak_time"] is not None and e["peak_time"] <= cutoff]
    unresolved = [e for e in rows if e["end"] is None or e["end"] > cutoff or e["peak_time"] is None or e["peak_time"] > cutoff]
    unknown = bool(unresolved or bad_events)
    bits, proofs = {}, {}
    for name, threshold in (("c1", 1e-6), ("m1", 1e-5)):
        supporting = [e for e in known if e["peak"] >= threshold]
        bits[name] = 1 if supporting else (None if unknown or not domain_qualified else 0)
        proofs[name] = [{"flare_id": e["flare_id"], "source_file": e["source_file"], "start_utc": utc(e["start"]),
                        "peak_utc": utc(e["peak_time"]), "end_utc": utc(e["end"]), "peak_w_m2": e["peak"]} for e in supporting]
    qualified = domain_qualified and all(value is not None for value in bits.values())
    return {"label_c1": bits["c1"], "label_m1": bits["m1"],
            "y_class": (2 if bits["m1"] == 1 else bits["c1"]) if qualified else None,
            "outcome_observability_qualified": qualified, "future_unresolved_event_count": len(unresolved),
            "future_quarantined_event_count": len(bad_events), "known_positive_bit_certificates": proofs}


def relevant_bad_events(audit, first, end, core):
    output = []
    for row in audit:
        if not any(k in row["reasons"] for k in ("missing_or_invalid_start_time", "missing_or_nonpositive_peak_flux", "end_before_start", "missing_or_invalid_peak_time")):
            continue
        stamp = core.parse_utc(row.get("raw_start_time") or row.get("raw_peak_time"), catalog_naive_is_utc=True)
        if stamp is None:
            raise ValueError("Unlocatable quarantined event: no negative assumption")
        if first <= stamp < end:
            output.append(row)
    return output


def checked_bundle(input_dir):
    manifest_path = input_dir / "source_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if manifest.get("input_readonly") is not True:
        raise ValueError("Read-only source bundle must be explicitly registered")
    seen, roles, before = set(), defaultdict(list), {str(manifest_path): sha(manifest_path)}
    for entry in manifest["files"]:
        if entry["path"] in seen:
            raise ValueError("Duplicate source path")
        seen.add(entry["path"])
        path = safe_path(input_dir, entry["path"])
        if sha(path) != entry["sha256"] or path.stat().st_size != entry["bytes"]:
            raise ValueError("Frozen source bytes mismatch")
        before[str(path)] = entry["sha256"]
        roles[entry["role"]].append(entry)
    return manifest, roles, before


def load_history_sources(input_dir, manifest, roles, cutoff, math, core):
    annual_events = roles.get("science_event_annual", [])
    if len(annual_events) != 17 or sorted(int(entry["parameters"]["year"]) for entry in annual_events) != list(range(2010, 2027)):
        raise ValueError("Complete frozen2010-2026 annual catalogue set required; missing year cannot imply no flare")
    metadata = roles.get("science_event_metadata", [])
    if len(metadata) != 1:
        raise ValueError("One official complete-day catalogue metadata source required")
    meta = json.loads(safe_path(input_dir, metadata[0]["path"]).read_text(encoding="utf-8-sig"))
    end_day = meta.get("global_attributes", {}).get("time_coverage_end", "")
    if not isinstance(end_day, str) or not re.fullmatch(r"\d{8}", end_day):
        raise ValueError("Catalogue complete-day support not documented; no last-event inference")
    declared_end = datetime.strptime(end_day, "%Y%m%d").replace(tzinfo=UTC) + timedelta(days=1)
    if cutoff > declared_end:
        raise ValueError("Historical source cutoff exceeds declared catalogue support")
    raw_events = []
    for entry in annual_events:
        with safe_path(input_dir, entry["path"]).open(encoding="utf-8-sig", newline="") as stream:
            raw_events.extend({**row, "source_file": entry["path"]} for row in csv.DictReader(stream))
    events, audit = core.normalize_events(raw_events)
    for event in events:
        if event["peak_time"] is None:
            audit.append({k: v for k, v in event.items() if k not in ("start", "end", "peak_time", "peak")} | {"reasons": "missing_or_invalid_peak_time"})
    minutes, coverage_audit = set(), []
    for entry in manifest["files"]:
        role, path = entry["role"], safe_path(input_dir, entry["path"])
        if role == "reused_legacy_coverage":
            valid = core.coverage_valid_minutes(path)
            info = {"clock": "source-declared UTC", "usage": "coverage only"}
        elif role in {"reused_science_coverage", "new_annual_science_coverage", "finalweek_fallback_science"}:
            valid, info = core.science_coverage_valid_minutes(path)
            if role == "reused_science_coverage":
                valid = core.science_crop_minutes(valid, int(entry["parameters"]["satellite"]), int(entry["parameters"]["year"]))
            if role == "finalweek_fallback_science":
                lower = int((cutoff - timedelta(hours=168)).timestamp()) // 60
                upper = int(cutoff.timestamp()) // 60
                valid = {minute for minute in valid if lower <= minute < upper}
        else:
            continue
        minutes |= valid
        coverage_audit.append({"path": entry["path"], "sha256": entry["sha256"], "selected_valid_minutes": len(valid), **info})
    return core.EventIndex(events), audit, math.MinuteIndex(minutes, datetime(2010, 12, 1, tzinfo=UTC), cutoff), coverage_audit


def daily_row(reference, rows, event_index, event_audit, minute_index, history_cutoff, catalog_end, contract, gateway, math, core, retrieval=None, live=False):
    patch = full_patch_qualification(rows, reference, gateway, retrieval if live else None)
    features = math.history_features(reference, event_index)
    latest = features.pop("history_latest_event_end_utc")
    pf, pg = minute_index.stats(reference - timedelta(hours=168), 168)
    ff, fg = minute_index.stats(reference, 24)
    past_bad = relevant_bad_events(event_audit, reference - timedelta(hours=168), reference, core)
    history_ready = coverage_ok(pf, pg) and not past_bad and reference <= catalog_end and reference <= history_cutoff
    if not history_ready:
        features, latest = {name: None for name in HISTORY}, None
    domain = coverage_ok(ff, fg) and reference + timedelta(hours=24) <= min(history_cutoff, catalog_end)
    bad = relevant_bad_events(event_audit, reference, reference + timedelta(hours=24), core)
    certificate = outcome_certificate(reference, event_index, bad, history_cutoff, domain, core)
    reasons = []
    split = math.split_name(reference, contract["splits"])
    if patch["qualification"]["status"] != "eligible":
        reasons.append(patch["qualification"].get("reason", "magnetic_snapshot_not_eligible"))
    if not live and split == "excluded":
        reasons.append("outside_registered_split")
    if not live and reference.strftime("%Y-%m-%d") in ROLE_BOUNDARY_PURGE_DATES:
        reasons.append("role_boundary_future_label_overlap")
    if not live and not history_ready:
        reasons.append("historical_common_dates_history_baseline_unknown")
    if not live and not certificate["outcome_observability_qualified"]:
        reasons.append("future_label_or_coverage_unknown")
    support = core.parse_utc(patch["hmi_latest_support_end_utc"])
    fmax = max((value for value in (support, latest) if value is not None), default=None)
    result = {"reference_utc": utc(reference), "target_start_utc": utc(reference), "target_end_utc": utc(reference + timedelta(hours=24)),
              "status": "excluded" if reasons else "eligible", "split": "live" if live else split, "exclusion_reasons": "|".join(reasons),
              "source_mode": "hmi.sharp_cea_720s_nrt", "hmi_nominal_tai": reference.strftime("%Y.%m.%d_13:00:00_TAI"),
              **patch["qualification"]["features"], **features,
              **{key: value for key, value in patch.items() if key not in ("qualification", "audit")},
              "history_baseline_status": "qualified" if history_ready else "unknown_not_issued", "history_latest_event_end_utc": utc(latest),
              "history_missing_end_event_count": sum(event["end"] is None for event in event_index.window(reference - timedelta(hours=168), reference)),
              "magnetic_qualified": patch["qualification"]["status"] == "eligible", "label_qualified": False if live else certificate["outcome_observability_qualified"],
              "history7d_qualified": history_ready, "same_date_evaluation_qualified": not live and not reasons,
              "feature_max_observed_utc": utc(fmax), "past_168h_coverage_fraction": pf, "past_168h_max_gap_minutes": pg,
              "future_coverage_fraction": None if live else ff, "future_max_gap_minutes": None if live else fg,
              "label_c1": None if live else certificate["label_c1"], "label_m1": None if live else certificate["label_m1"],
              "y_class": None if live else certificate["y_class"], "source_retrieved_utc": retrieval or ""}
    return result, patch["audit"], certificate


def write_csv(path, rows, fallback_columns=()):
    columns = list(dict.fromkeys([*fallback_columns, *(key for row in rows for key in row)]))
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def prepare(input_dir, contract_path, profile_path, output):
    if output.exists():
        raise ValueError("Refusing existing output directory")
    contract, profile = production_contract(contract_path, profile_path)
    gateway, math, core = load_modules()
    manifest, roles, before = checked_bundle(input_dir)
    before.update({str(contract_path): sha(contract_path), str(profile_path): sha(profile_path)})
    if manifest.get("contract_sha256") != sha(contract_path) or manifest.get("source_profile_sha256") != sha(profile_path):
        raise ValueError("Input bundle does not bind registered physical contract/profile")
    if manifest.get("source_plan_sha256") != contract.get("source_plan_sha256"):
        raise ValueError("Input source plan is not the registered plan")
    history_cutoff = gateway.date_utc(profile["historical_catalog_cutoff_utc"])
    catalog_end = gateway.date_utc(profile["catalogue_declared_complete_end_utc"])
    events, event_audit, minutes, coverage_audit = load_history_sources(input_dir, manifest, roles, history_cutoff, math, core)
    by_day, retrievals, unresolved_months = defaultdict(list), {}, set()
    for entry in roles["magnetic_month"]:
        if entry.get("status") != "success":
            unresolved_months.add(entry["month"])
            continue
        payload = json.loads(safe_path(input_dir, entry["path"]).read_text(encoding="utf-8-sig"))
        for row in gateway.decode_keywords(payload):
            match = re.fullmatch(r"(\d{4}\.\d{2}\.\d{2})_13:00:00_TAI", str(row.get("T_REC")))
            if match is None:
                unresolved_months.add(entry["month"])
                continue
            key = match.group(1).replace(".", "-")
            by_day[key].append(row)
            retrievals[key] = entry["retrieved_utc"].replace("+00:00", "Z")
    first = gateway.date_utc(contract["calendar_start_date"] + "T16:00:00Z")
    live_ref = gateway.date_utc(contract["prediction_window"]["start"])
    output.mkdir(parents=True)
    all_rows, patch_audit, outcome_audit = [], [], []
    first_native = re.search(r"\[(\d{4}\.\d{2}\.\d{2})_", profile["native_FirstRecord"])[1].replace(".", "-")
    last_native = re.search(r"\[(\d{4}\.\d{2}\.\d{2})_", profile["native_LastRecord"])[1].replace(".", "-")
    for day in range((live_ref - first).days + 1):
        reference = first + timedelta(days=day)
        key = reference.strftime("%Y-%m-%d")
        row, audit, certificate = daily_row(reference, by_day[key], events, event_audit, minutes, history_cutoff, catalog_end, contract, gateway, math, core,
                                             retrievals.get(key), live=reference == live_ref)
        if key[:7] in unresolved_months:
            row["status"] = "excluded"
            row["exclusion_reasons"] += "|unresolved_magnetic_source_month"
        if not first_native <= key <= last_native:
            row["status"] = "excluded"
            row["exclusion_reasons"] += "|outside_native_NRT_available_range"
        row["same_date_evaluation_qualified"] = row["status"] == "eligible" and row["split"] != "live"
        all_rows.append(row)
        patch_audit += [{"reference_utc": utc(reference), **value} for value in audit]
        outcome_audit.append({"reference_utc": utc(reference), **certificate})
    eligible = [row for row in all_rows if row["status"] == "eligible" and row["split"] != "live"]
    live = [row for row in all_rows if row["split"] == "live"]
    changed = [path for path, expected in before.items() if sha(path) != expected]
    if changed:
        raise ValueError("Read-only input changed during preparation")
    for name, rows in (("daily_instances.csv", eligible), ("all_daily_windows.csv", all_rows), ("live_features.csv", live), ("patch_audit.csv", patch_audit), ("event_audit.csv", event_audit)):
        write_csv(output / name, rows, ("reference_utc",) if not rows else ())
    write_json(output / "coverage_audit.json", coverage_audit)
    write_json(output / "outcome_verification.json", outcome_audit)
    write_json(output / "input_hashes_readonly.json", {"before": before, "after": {path: sha(path) for path in before}, "all_inputs_hashes_unchanged": True})
    counts = {role: dict(Counter(str(row["y_class"]) for row in eligible if row["split"] == role)) for role in contract["splits"]}
    registered = [row for row in all_rows if row["split"] in contract["splits"]]
    layers = {"registered_historical_dates": len(registered),
              "magnetic_qualified_dates": sum(row["magnetic_qualified"] for row in registered),
              "magnetic_and_label_qualified_dates": sum(row["magnetic_qualified"] and row["label_qualified"] for row in registered),
              "magnetic_label_and_history7d_qualified_dates": sum(row["magnetic_qualified"] and row["label_qualified"] and row["history7d_qualified"] for row in registered),
              "final_same_date_evaluation_dates": len(eligible)}
    summary = {"schema_version": "physical-preparation-summary-v1", "calendar_rows": len(all_rows), "eligible_rows": len(eligible), "split_class_counts": counts,
               "qualification_layers": layers,
               "excluded_reason_counts": dict(Counter(reason for row in all_rows for reason in row["exclusion_reasons"].split("|") if reason)),
               "live_status": live[0]["status"], "live_history_baseline_status": live[0]["history_baseline_status"], "features_physical": gateway.FEATURES,
               "features_history_baseline": HISTORY, "no_fit_predict_plot": True, "source_manifest_sha256": sha(input_dir / "source_manifest.json")}
    summary.update({"forecast_authorized": False, "scope": "Source-only historical eligibility diagnosis; original F1 scientific gate remains unmet",
                    "parent_source_contract_sha256": sha(contract_path), "parent_source_profile_sha256": sha(profile_path),
                    "current_nonzero_QUALITY_policy": "Rejected under exact0; no source-only receipt qualifies live0x400 or authorizes a forecast"})
    write_json(output / "summary.json", summary)
    write_json(output / "validation.json", {"status": "passed", "all_inputs_hashes_unchanged": True, "no_fit_predict_plot": True,
               "actual_JW_tool_execution": "requires_external_native_receipt", "live_main_qualified": live[0]["status"] == "eligible", "schema_version": "physical-preparation-validation-v1"})
    files = [{"path": p.name, "bytes": p.stat().st_size, "sha256": sha(p)} for p in sorted(output.iterdir()) if p.is_file()]
    write_json(output / "prepared_manifest.json", {"schema_version": "physical-prepared-manifest-v1", "files": files, "features_physical": gateway.FEATURES,
               "features_history_baseline": HISTORY, "contract_sha256": sha(contract_path), "source_profile_sha256": sha(profile_path),
               "source_plan_sha256": contract["source_plan_sha256"], "source_manifest_sha256": sha(input_dir / "source_manifest.json"),
               "source_versions_terms": manifest.get("source_versions_terms", []), "code_sha256": sha(__file__), "no_fit_predict_plot": True,
               "parent_source_contract_sha256": sha(contract_path), "forecast_authorized": False, "source_only_diagnostic": True})
    return summary


def acquire(contract_path, profile_path, output, transport=None):
    if output.exists():
        raise ValueError("Refusing existing output directory")
    contract, profile = production_contract(contract_path, profile_path)
    gateway, _, _ = load_modules()
    queries = monthly_queries(profile["native_FirstRecord"], profile["native_LastRecord"], gateway)
    keyword_names = profile.get("available_requested_keyword_names", gateway.METADATA + gateway.METRICS)
    if set(gateway.REQUIRED_METADATA + gateway.METRICS) - set(keyword_names):
        raise ValueError("Actual source schema lacks core magnetic/time/quality fields")
    for query in queries:
        query["parameters"]["key"] = ",".join(name for name in gateway.METADATA + gateway.METRICS if name in keyword_names)
    budget = contract["magnetic_acquisition"]
    if len(queries) > budget["HTTP_requests_max"] or budget["workers_max"] > 4 or budget["HTTP_retries"] != 0:
        raise ValueError("Registered monthly resource envelope invalid")
    output.mkdir(parents=True)
    (output / "raw").mkdir()
    write_json(output / "planned_queries.json", {"queries": queries, "expected_months": len(queries),
               "purpose": "Pre-action request inventory; unfinished entries remain not attempted/unknown, never successful source data"})
    ledger = ParallelAcquisitionLedger(budget, progress_path=output / "source_requests.progress.json")
    def fetch(query):
        return monthly_fetch(query, profile, output, ledger, transport or gateway.request_once)
    with ThreadPoolExecutor(max_workers=budget["workers_max"]) as pool:
        list(pool.map(fetch, queries))
    receipts = sorted(ledger.requests, key=lambda item: item["month"])
    write_json(output / "source_requests.json", {"requests": receipts, "source_network_attempts": ledger.attempts, "HTTP_retries": 0,
               "total_archived_response_bytes": ledger.archived_bytes, "received_chunk_bytes": ledger.received_bytes,
               "discarded_at_cap_bytes": ledger.received_bytes - ledger.archived_bytes,
               "elapsed_seconds": ledger.clock() - ledger.started, "stop_issuing_new_HTTP_after_seconds": ledger.issue_cutoff_seconds,
               "active_wall_seconds_max": budget["active_wall_seconds_max"]})
    files = [{**entry, "role": "magnetic_month"} for entry in receipts]
    write_json(output / "source_manifest.json", {"schema_version": "magnetic-monthly-source-manifest-v1", "input_readonly": True, "files": files,
               "contract_sha256": sha(contract_path), "source_profile_sha256": sha(profile_path), "code_sha256": sha(__file__),
               "native_FirstRecord": profile["native_FirstRecord"], "native_LastRecord": profile["native_LastRecord"],
               "execution_owner": "Requires actual native JW tool receipt; this manifest alone is not proof", "no_fit_predict_plot": True,
               "source_plan_sha256": REGISTERED_SOURCE_PLAN_SHA, "source_versions_terms": profile["source_versions_terms"],
               "forecast_authorized": False, "source_only_diagnostic": True})
    compact = {"schema_version": "source-only-acquisition-summary-v1", "request_status_counts": dict(Counter(entry["status"] for entry in receipts)),
               "expected_months": len(queries), "source_network_attempts": ledger.attempts, "total_archived_response_bytes": ledger.archived_bytes,
               "received_chunk_bytes": ledger.received_bytes, "elapsed_seconds": ledger.clock()-ledger.started,
               "stop_new_HTTP_seconds": ledger.issue_cutoff_seconds, "HTTP_retries": 0, "forecast_authorized": False,
               "original_F1_scientific_gate_passed": False, "source_manifest_sha256": sha(output / "source_manifest.json"),
               "source_requests_sha256": sha(output / "source_requests.json"), "code_sha256": sha(__file__),
               "no_fit_predict_plot": True, "QUALITY_policy": "exact0 only; current0x400 remains rejected", "scientific_acceptance": "not_reviewed"}
    write_json(output / "summary.json", compact)
    write_json(output / "validation.json", {"status": "completed_with_all_attempts_preserved", "source_only_diagnostic": True,
               "forecast_authorized": False, "fit_predict_plot": False, "actual_JW_tool_execution": "requires_external_native_receipt",
               "all_170_expected_receipts_preserved": len(receipts) == len(queries), "HTTP_retry_count": 0})
    print(json.dumps(compact, ensure_ascii=False))


class ParallelAcquisitionLedger:
    def __init__(self, budget, clock=time.monotonic, progress_path=None):
        self.budget, self.lock = budget, threading.Lock()
        self.requests, self.attempts, self.archived_bytes, self.received_bytes = [], 0, 0, 0
        self.clock, self.started, self.progress_path = clock, clock(), progress_path
        self.issue_cutoff_seconds = budget.get("active_wall_seconds_max", 600) - budget.get("HTTP_request_timeout_seconds", 30)

    def persist_locked(self):
        if self.progress_path is not None:
            temporary = self.progress_path.with_suffix(".tmp")
            write_json(temporary, {"requests": self.requests, "source_network_attempts": self.attempts,
                                  "elapsed_seconds": self.clock() - self.started, "complete": False})
            temporary.replace(self.progress_path)

    def completed(self):
        with self.lock:
            self.persist_locked()

    def reserve_request(self, item):
        with self.lock:
            self.requests.append(item)
            if self.clock() - self.started >= self.issue_cutoff_seconds:
                item["status"] = "not_attempted_elapsed_deadline"
                self.persist_locked()
                return False
            if self.attempts >= self.budget["HTTP_requests_max"] or self.archived_bytes >= self.budget["total_response_bytes_max"]:
                item["status"] = "not_attempted_resource_limit"
                self.persist_locked()
                return False
            self.attempts += 1
            item["status"] = "in_progress"
            self.persist_locked()
            return True

    def reserve_bytes(self, amount, file_bytes):
        with self.lock:
            self.received_bytes += amount
            allowed = max(0, min(amount, self.budget["response_bytes_each_max"] - file_bytes,
                                 self.budget["total_response_bytes_max"] - self.archived_bytes))
            self.archived_bytes += allowed
            return allowed


def monthly_fetch(query, profile, output, ledger, transport):
    from urllib.parse import urlencode, urlsplit
    url = "http://jsoc.stanford.edu/cgi-bin/ajax/jsoc_info?" + urlencode(query["parameters"])
    origin = urlsplit(url).scheme + "://" + urlsplit(url).netloc
    if origin not in profile["source_origins"]:
        raise ValueError("Unregistered source origin")
    item = {**query, "url": url, "path": "raw/" + query["id"] + ".bin", "status": "failed",
            "attempt": 1, "retry_count": 0, "started_utc": utc(datetime.now(UTC)), "status_code": None,
            "version": "JSOC NRT response at actual retrieval time", "license_or_terms": profile.get("license_or_terms", "not_verified"),
            "endpoint_provenance": profile["endpoint_provenance"]}
    body, response = bytearray(), None
    path = output / item["path"]
    path.touch(exist_ok=False)
    try:
        if not ledger.reserve_request(item):
            return item
        request_deadline = ledger.clock() + ledger.budget["HTTP_request_timeout_seconds"]
        response = transport(url, ledger.budget["HTTP_request_timeout_seconds"])
        item["status_code"] = response.status_code
        if response.url != url:
            raise ValueError("Source redirect not authorized")
        for chunk in response.iter_content(chunk_size=16384):
            if not chunk:
                continue
            allowed = ledger.reserve_bytes(len(chunk), len(body))
            body.extend(chunk[:allowed])
            # Retain a partial raw snapshot even if the outer driver times out.
            with path.open("ab") as stream:
                stream.write(chunk[:allowed])
            if ledger.clock() >= request_deadline:
                item["elapsed_request_deadline_reached"] = True
                raise TimeoutError("Registered source request elapsed deadline reached")
            if allowed < len(chunk):
                item["truncated_at_resource_cap"] = True
                raise ValueError("Response allowance exhausted; discarded bytes separately audited")
        if response.status_code != 200:
            raise ValueError("Non-200 source response")
        payload = json.loads(body.decode("utf-8-sig"))
        if payload.get("status") != 0:
            raise ValueError("Source response failure is not a zero solar day")
        item["status"] = "success"
    except Exception as exc:
        item["status"] = "failed"
        item["error_class"] = type(exc).__name__
    finally:
        if response is not None:
            response.close()
        path.write_bytes(body)
        item.update({"bytes": len(body), "sha256": sha(path), "retrieved_utc": utc(datetime.now(UTC))})
        ledger.completed()
    return item


def fixed_FITS_candidate(records, date_text):
    """Feature-blind closest-central patch, tie by HARP; not by outcome/value."""
    candidates = []
    for record in records:
        if str(record.get("T_REC")) != date_text.replace("-", ".") + "_13:00:00_TAI":
            continue
        try:
            cmd, harp = float(record["LON_FWT"]), int(record["HARPNUM"])
            if math.isfinite(cmd) and abs(cmd) <= 60:
                candidates.append((abs(cmd), harp, record))
        except (KeyError, TypeError, ValueError):
            continue
    return min(candidates, key=lambda entry: (entry[0], entry[1]))[2] if candidates else None


def validate_FITS_candidate(record, returned_url, profile):
    """Only an actually returned anonymous URL, never an invented SUM path."""
    from urllib.parse import urlsplit
    origin = urlsplit(returned_url).scheme + "://" + urlsplit(returned_url).netloc
    if origin not in profile["source_origins"] or not returned_url.lower().endswith(".fits"):
        raise ValueError("No source-returned public FITS URL; preserve not_acquired")
    if record.get("segment_name") != "Br" or record.get("source_mode") != "hmi.sharp_cea_720s_nrt":
        raise ValueError("Registered CEA radial magnetic component only; not CNN training data")
    if record.get("source_returned_segment_url") != returned_url:
        raise ValueError("FITS URL not bound to actual native segment response")
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("acquire", "prepare"), default="prepare")
    parser.add_argument("--input-dir", type=Path)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--source-profile", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "acquire":
        acquire(args.contract, args.source_profile, args.output_dir)
    elif args.input_dir is None:
        parser.error("Preparation requires --input-dir")
    else:
        print(json.dumps(prepare(args.input_dir, args.contract, args.source_profile, args.output_dir)))


if __name__ == "__main__":
    main()
