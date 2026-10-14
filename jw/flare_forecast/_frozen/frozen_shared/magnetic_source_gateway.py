"""Registered bounded SHARP metadata probe and offline qualification guards.

Only the native JW owner may execute the registered network CLI. A helper's
metadata does not itself prove a JW tool invocation or scientific acceptance.
The tool never trains, predicts, or renders observed solar records.
"""
from __future__ import annotations

import argparse
import calendar
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import re
from urllib.parse import urlencode, urlsplit

import astropy
import numpy as np
import requests
from astropy.time import Time
from astropy.utils import iers

iers.conf.auto_download = False
UTC = timezone.utc
METRICS = ["USFLUX", "TOTUSJH", "TOTUSJZ", "ABSNJZH", "SAVNCPP", "MEANALP", "R_VALUE", "TOTPOT", "MEANSHR", "AREA_ACR", "MEANPOT"]
FEATURES = [f"{name}_{method}" for name in METRICS for method in ("max", "median")] + ["patch_count"]
METADATA = ["HARPNUM", "T_REC", "T_OBS", "DATE", "QUALITY", "QUAL_S", "QUALLEV1", "ARM_QUAL", "NOAA_AR", "NOAA_NUM", "NOAA_ARS", "LON_FWT", "LAT_FWT", "CRLN_OBS", "CRVAL1", "CRVAL2", "OBS_VR", "BLD_VERS", "CALVER64", "CODEVER7"]
REQUIRED_METADATA = ["HARPNUM", "T_REC", "T_OBS", "DATE", "QUALITY", "QUAL_S", "QUALLEV1", "ARM_QUAL", "LON_FWT"]
REGISTERED_CONTRACT_SHA = "2dff105766975f4c0ab2daa8afb0fda6050cdd3b4a265e15920c21b6b90cc19a"
NATIVE_SOURCE_SHA = "805057cdf5477f1436321edaa787ed21efb76440161d0ac0510a9628be813e77"
ENDPOINT = "http://jsoc.stanford.edu/cgi-bin/ajax/jsoc_info"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tai_utc(text):
    match = re.fullmatch(r"(\d{4})\.(\d{2})\.(\d{2})_(\d{2}):(\d{2}):(\d{2}(?:\.\d+)?)_TAI", str(text))
    if match is None:
        raise ValueError("Explicit supported HMI TAI timestamp required")
    year, month, day, hour, minute, second = match.groups()
    if not 2010 <= int(year) <= 2026:
        raise ValueError("Outside proposed frozen2010-2026 time range")
    value = Time(f"{year}-{month}-{day}T{hour}:{minute}:{second}", format="isot", scale="tai")
    return value.utc.to_datetime(timezone=UTC, leap_second_strict="raise")


def date_utc(text):
    if not isinstance(text, str) or not text.endswith("Z"):
        raise ValueError("Explicit UTC DATE/reference required")
    stamp = datetime.fromisoformat(text[:-1] + "+00:00")
    if not 2010 <= stamp.year <= 2026:
        raise ValueError("Invalid DATE epoch")
    return stamp


def quality_word(value):
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ValueError("Unknown quality value")
    word = int(str(value), 16 if str(value).lower().startswith("0x") else 10)
    if not -(2**31) <= word < 2**32:
        raise ValueError("Out-of-range quality value")
    return word & 0xFFFFFFFF


def query_month(series, year, month, anchor_hour=21, reference_hour_UTC=0):
    if series not in {"hmi.sharp_cea_720s_nrt", "hmi.sharp_cea_720s"}:
        raise ValueError("Unregistered SHARP product")
    if not 2011 <= year <= 2026 or not 1 <= month <= 12 or (reference_hour_UTC, anchor_hour) not in {(0, 21), (16, 13)}:
        raise ValueError("Unregistered proposed reference/TAI anchor pair")
    first = datetime(year, month, 1, tzinfo=UTC) - timedelta(days=1 if reference_hour_UTC == 0 else 0)
    first = first.replace(hour=anchor_hour)
    days = calendar.monthrange(year, month)[1]
    return {"op": "rs_list", "ds": f"{series}[][{first.strftime('%Y.%m.%d_%H:%M:%S')}_TAI/{days}d@24h]",
            "key": ",".join(METADATA + METRICS), "n": "0"}


def decode_keywords(payload):
    if payload.get("status") != 0:
        raise ValueError("JSOC returned source error; not an empty solar day")
    count = payload.get("count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise ValueError("Invalid source record count")
    columns = {}
    for column in payload.get("keywords", []):
        name, values = column.get("name"), column.get("values")
        if name in columns or not isinstance(name, str) or not isinstance(values, list) or len(values) != count:
            raise ValueError("Duplicate/misaligned JSON keyword arrays")
        columns[name] = values
    if set(REQUIRED_METADATA + METRICS) - set(columns):
        raise ValueError("Missing requested metadata/physical field; never zero-filled")
    return [{key: values[i] for key, values in columns.items()} for i in range(count)]


def validate_profile(profile):
    if profile.get("series") not in {"hmi.sharp_cea_720s_nrt", "hmi.sharp_cea_720s"} or (profile.get("reference_hour_UTC", 0), profile.get("anchor_hour_TAI")) not in {(0, 21), (16, 13)}:
        raise ValueError("Product/anchor profile not frozen")
    if profile.get("metrics") != METRICS or profile.get("cmd_field") != "LON_FWT" or profile.get("cmd_definition") != "Stonyhurst longitude":
        raise ValueError("Physical field/CMD source contract mismatch")
    if profile.get("support_half_width_seconds") != 675 or profile.get("max_abs_cmd_degrees") != 60:
        raise ValueError("Proposed temporal/geometry bounds changed")
    accepted = profile.get("accepted_QUALITY_words")
    if not isinstance(accepted, list) or not accepted or len(set(accepted)) != len(accepted):
        raise ValueError("Exact accepted quality words required")
    for word in accepted:
        quality_word(word)
    if any(quality_word(word) != 0 for word in accepted):
        provenance = profile.get("quality_source_proof", {})
        if profile.get("source_verification_kind") != "native_JW_source_verified" or not all(re.fullmatch(r"[0-9a-f]{64}", provenance.get(k, "")) for k in ("document_sha256", "actual_native_search_receipt_sha256", "F1_registration_sha256")):
            raise ValueError("Nonzero QUALITY not permitted without frozen native source proof")
    return {quality_word(word) for word in accepted}


def qualify_rows(rows, reference_utc, profile, available_by_utc=None):
    accepted = validate_profile(profile)
    reference = date_utc(reference_utc)
    reference_hour = profile.get("reference_hour_UTC", 0)
    if reference.hour != reference_hour or reference.minute or reference.second or reference.microsecond:
        raise ValueError("Registered UTC00/16 daily reference required")
    available_by = date_utc(available_by_utc) if available_by_utc is not None else None
    expected = (reference - timedelta(days=1 if reference_hour == 0 else 0)).replace(hour=profile["anchor_hour_TAI"])
    expected_tai = expected.strftime("%Y.%m.%d_%H:%M:%S_TAI")
    keys = []
    for row in rows:
        keys.append((str(row.get("HARPNUM")), str(row.get("T_REC"))))
    duplicates = Counter(keys)
    audit, selected = [], []
    for row, key in zip(rows, keys):
        reasons, values, observed, produced = [], {}, None, None
        if duplicates[key] > 1:
            reasons.append("duplicate_HARP_time_rejected_all_copies")
        try:
            if tai_utc(row["T_REC"]) != tai_utc(expected_tai):
                reasons.append("unexpected_fixed_anchor")
            observed = tai_utc(row["T_OBS"])
            produced = date_utc(row["DATE"])
            if observed + timedelta(seconds=675) >= reference:
                reasons.append("support_not_strictly_before_reference")
            if produced > reference:
                reasons.append("keywords_produced_after_reference")
            if available_by is not None and produced > available_by:
                reasons.append("keywords_produced_after_actual_retrieval_or_issue")
            if produced < observed:
                reasons.append("production_before_observation")
        except (KeyError, ValueError, OverflowError):
            reasons.append("invalid_explicit_time")
        try:
            if quality_word(row["QUALITY"]) not in accepted:
                reasons.append("QUALITY_not_in_registered_exact_words")
        except (KeyError, ValueError):
            reasons.append("unknown_QUALITY")
        # Secondary quality words are preserved for source-specific review;
        # no observables word table is silently applied to a different field.
        for field in ("QUAL_S", "QUALLEV1", "ARM_QUAL"):
            try:
                quality_word(row[field])
            except (KeyError, ValueError):
                reasons.append("unknown_" + field)
        try:
            cmd = float(row["LON_FWT"])
            if not math.isfinite(cmd) or abs(cmd) > 60:
                reasons.append("CMD_nonfinite_or_outside60deg")
        except (KeyError, ValueError, TypeError):
            reasons.append("invalid_CMD")
        for metric in METRICS:
            try:
                value = float(row[metric])
                if not math.isfinite(value):
                    raise ValueError("nonfinite")
                if metric != "MEANALP" and value < 0:
                    reasons.append("negative_nonnegative_physical_domain_" + metric)
                values[metric] = value
            except (KeyError, TypeError, ValueError):
                reasons.append("missing_or_nonfinite_" + metric)
        item = {"source_record_key": profile["series"] + ":" + key[0] + ":" + key[1], "HARPNUM": row.get("HARPNUM"), "T_REC": row.get("T_REC"),
                "QUALITY_raw": row.get("QUALITY"), "DATE_raw": row.get("DATE"), "T_OBS_raw": row.get("T_OBS"),
                "status": "rejected" if reasons else "eligible", "reasons": reasons, "metric_values": values,
                "metric_raw_values": {name: row.get(name) for name in METRICS},
                "support_end_UTC": (observed + timedelta(seconds=675)).isoformat() if observed else None,
                "produced_UTC": produced.isoformat() if produced else None}
        audit.append(item)
        if not reasons:
            selected.append(item)
    return audit, selected


def aggregate(selected):
    if not selected:
        return {"status": "unknown", "reason": "no_eligible_patch_is_not_zero_solar_field", "features": {name: None for name in FEATURES}}
    values = {}
    for metric in METRICS:
        data = np.asarray([r["metric_values"][metric] for r in selected], dtype=float)
        values[metric + "_max"] = float(data.max())
        values[metric + "_median"] = float(np.median(data))
    values["patch_count"] = len(selected)
    return {"status": "eligible", "features": values}


def offline_validate(response, profile_path, reference, output, available_by=None):
    if output.exists():
        raise ValueError("Refusing existing output directory")
    output.mkdir(parents=True)
    before = {str(p): sha(p) for p in (response, profile_path)}
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    payload = json.loads(response.read_text(encoding="utf-8"))
    rows = decode_keywords(payload)
    audit, selected = qualify_rows(rows, reference, profile, available_by)
    result = {"schema_version": "offline-magnetic-qualification-v1", "source_network_calls": 0, "no_fit_predict_plot": True,
              "reference_utc": reference, "profile_sha256": sha(profile_path), "response_sha256": sha(response),
              "available_by_utc": available_by,
              "input_rows": len(rows), "eligible_patches": len(selected), "qualification": aggregate(selected),
              "audit": audit, "before_hashes": before, "after_hashes": {str(p): sha(p) for p in (response, profile_path)},
              "source_scope": "Offline validation only; production sources/quality interpretation require native JW discovery and registered F1 proof", "code_sha256": sha(__file__)}
    (output / "validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"source_network_calls": 0, "eligible_patches": len(selected), "status": result["qualification"]["status"]}))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def load_registered_contract(path, source_proof):
    if sha(path) != REGISTERED_CONTRACT_SHA:
        raise ValueError("Exact registered F1 contract bytes required")
    contract = json.loads(path.read_text(encoding="utf-8-sig"))
    if sha(source_proof) != NATIVE_SOURCE_SHA or contract["source_proof"]["raw_result_sha256"] != NATIVE_SOURCE_SHA:
        raise ValueError("Exact genuine native JW source result required")
    if contract["source_proof"]["official_source_page"] not in source_proof.read_text(encoding="utf-8-sig"):
        raise ValueError("Official URL absent from native source result")
    if contract["physical_keywords"] != METRICS or contract["HTTP_retries"] != 0 or contract["fit_predict_or_plot_allowed"]:
        raise ValueError("Registered source/physical/zero-retry restrictions changed")
    return contract


def probe_queries(contract):
    day = contract["registered_at_utc"][:10]
    date_utc(day + "T16:00:00Z")
    series = contract["candidate_series"]
    if series != ["hmi.sharp_cea_720s_nrt", "hmi.sharp_cea_720s"]:
        raise ValueError("Exact registered product pair required")
    queries = [{"id": "schema_nrt", "kind": "schema", "series": series[0], "parameters": {"op": "series_struct", "ds": series[0]}},
               {"id": "schema_definitive", "kind": "schema", "series": series[1], "parameters": {"op": "series_struct", "ds": series[1]}}]
    for query_id, product, fixed_day in (("current_nrt", series[0], day), ("definitive_2011_example", series[1], "2011-01-08")):
        queries.append({"id": query_id, "kind": "snapshot", "series": product, "reference_utc": fixed_day + "T16:00:00Z",
                        "parameters": {"op": "rs_list", "ds": product + "[][" + fixed_day.replace("-", ".") + "_13:00:00_TAI]",
                                       "key": ",".join(METADATA + METRICS), "n": "0"}})
    return queries


def request_once(url, timeout):
    # requests has no automatic retry by default; redirects are not followed.
    # Certificate validation remains enabled for any HTTPS endpoint.
    return requests.get(url, timeout=timeout, stream=True, allow_redirects=False, verify=True)


def bounded_fetch(query, contract, output, ledger, transport):
    url = ENDPOINT + "?" + urlencode(query["parameters"])
    origin = urlsplit(url).scheme + "://" + urlsplit(url).netloc
    if origin not in contract["source_origins"]:
        raise ValueError("Unregistered source origin")
    if len(ledger) >= contract["HTTP_requests_max"]:
        raise ValueError("Registered request allowance exhausted")
    started = datetime.now(UTC).isoformat()
    item = {**query, "url": url, "attempt": 1, "retry_count": 0, "started_utc": started,
            "status": "failed", "status_code": None, "bytes": 0,
            "path": "raw/" + query["id"] + ".bin", "version": "JSOC response snapshot at retrieval time",
            "license_or_terms": "HMI/JSOC policy must be verified before reuse; source documents are not relicensed by this tool",
            "endpoint_provenance": contract["endpoint_implementation_provenance"]}
    ledger.append(item)  # Record even failed transport attempts; never retry.
    body = bytearray()
    response = None
    try:
        response = transport(url, contract["HTTP_request_timeout_seconds"])
        item["status_code"] = response.status_code
        item["final_url"] = response.url
        if response.url != url:
            raise ValueError("Unregistered URL change; redirects not authorized")
        prior_bytes = sum(x["bytes"] for x in ledger[:-1])
        for chunk in response.iter_content(chunk_size=16384):
            if not chunk:
                continue
            allowed = min(contract["response_bytes_each_max"] - len(body), contract["total_response_bytes_max"] - prior_bytes - len(body))
            if len(chunk) > allowed:
                body.extend(chunk[:max(0, allowed)])
                item["truncated_at_registered_cap"] = True
                raise ValueError("Registered response byte allowance exhausted")
            body.extend(chunk)
        if response.status_code != 200:
            raise ValueError("Non-200 source response; not a missing or zero solar day")
        payload = json.loads(body.decode("utf-8-sig"))
        if payload.get("status") != 0:
            raise ValueError("JSOC source status is not zero")
        item["status"] = "success"
        return payload, item
    except Exception as exc:
        # Do not print exception values or response text. All raw bytes remain
        # separately archived for review; receipts contain only error class.
        item["error_class"] = type(exc).__name__
        return None, item
    finally:
        if response is not None:
            response.close()
        raw_path = output / item["path"]
        raw_path.write_bytes(body)
        item.update({"bytes": len(body), "sha256": sha(raw_path), "retrieved_utc": datetime.now(UTC).isoformat()})


def schema_summary(payload):
    if not isinstance(payload, dict) or payload.get("status") != 0:
        raise ValueError("Invalid native product schema")
    fields = {}
    for entry in payload.get("keywords", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or entry["name"] in fields:
            raise ValueError("Invalid/duplicate native schema keyword")
        fields[entry["name"]] = entry
    if set(REQUIRED_METADATA + METRICS) - set(fields):
        raise ValueError("Schema lacks requested metadata/physical fields")
    lon = fields["LON_FWT"]
    if lon.get("units") != "degree" or "Stonyhurst longitude" not in lon.get("note", ""):
        raise ValueError("CMD interpretation not confirmed by actual product schema")
    interval = payload.get("Interval", {})
    if not all(isinstance(interval.get(k), str) and interval[k] for k in ("FirstRecord", "LastRecord")):
        raise ValueError("Actual FirstRecord/LastRecord required; not inferred calendar completeness")
    return {"Interval": interval, "required_keywords_present": True,
            "optional_metadata_not_in_schema": [name for name in METADATA if name not in fields],
            "available_requested_keyword_names": [name for name in METADATA + METRICS if name in fields],
            "physical_keyword_units": {name: fields[name].get("units") for name in METRICS},
            "CMD_source_definition": lon, "quality_and_time_source_definitions": {name: fields[name] for name in ("QUALITY", "QUAL_S", "QUALLEV1", "ARM_QUAL", "DATE", "T_OBS")}}


def execute_probe(contract, source_proof, contract_path, output, transport=request_once):
    """Pure injected transport in fixtures; actual CLI requires exact source gate."""
    if output.exists():
        raise ValueError("Refusing existing output directory")
    output.mkdir(parents=True)
    (output / "raw").mkdir()
    before = {"contract_sha256": sha(contract_path), "native_source_sha256": sha(source_proof)}
    ledger, schemas, snapshots = [], {}, {}
    for query in probe_queries(contract):
        if query["kind"] == "snapshot" and query["series"] in schemas:
            # Optional code-version/geometry metadata is retained when present;
            # unknown optional keywords must not make a server reject the query.
            query["parameters"]["key"] = ",".join(schemas[query["series"]]["summary"]["available_requested_keyword_names"])
        payload, receipt = bounded_fetch(query, contract, output, ledger, transport)
        if payload is None:
            continue
        try:
            if query["kind"] == "schema":
                schemas[query["series"]] = {"summary": schema_summary(payload), "full_source_schema": payload}
            else:
                rows = decode_keywords(payload)
                if query["series"] not in schemas:
                    raise ValueError("Cannot qualify records without verified product schema")
                profile = {"series": query["series"], "metrics": METRICS, "anchor_hour_TAI": 13, "reference_hour_UTC": 16,
                           "cmd_field": "LON_FWT", "cmd_definition": "Stonyhurst longitude", "support_half_width_seconds": 675,
                           "max_abs_cmd_degrees": 60, "accepted_QUALITY_words": [0], "source_verification_kind": "registered_strict_zero_only"}
                available = receipt["retrieved_utc"].replace("+00:00", "Z") if query["id"] == "current_nrt" else None
                audit, selected = qualify_rows(rows, query["reference_utc"], profile, available)
                counts = Counter(str(row.get("QUALITY")) for row in rows)
                rejection = Counter(reason for row in audit for reason in row["reasons"])
                central_indices = []
                geometry_unknown = 0
                for index, row in enumerate(rows):
                    try:
                        cmd = float(row["LON_FWT"])
                        if not math.isfinite(cmd):
                            raise ValueError("nonfinite")
                        if abs(cmd) <= 60:
                            central_indices.append(index)
                    except (KeyError, ValueError, TypeError):
                        geometry_unknown += 1
                rejected_central = sum(audit[i]["status"] != "eligible" for i in central_indices)
                snapshots[query["id"]] = {"series": query["series"], "reference_utc": query["reference_utc"], "input_rows": len(rows),
                    "eligible_strict_zero_patches": len(selected), "QUALITY_raw_distribution": dict(counts), "rejection_reason_counts": dict(rejection),
                    "all_11_physical_fields_finite_count": sum(all(name in a["metric_values"] for name in METRICS) for a in audit),
                    "candidate_central_patch_count": len(central_indices), "rejected_central_patch_count": rejected_central,
                    "unknown_geometry_patch_count": geometry_unknown, "partial_patch_coverage": bool(rejected_central or geometry_unknown),
                    "qualification": aggregate(selected), "profile": profile, "availability_cutoff_utc": available,
                    "audit": audit, "retrieval_utc": receipt["retrieved_utc"]}
        except Exception as exc:
            receipt["status"] = "invalid_source_contract"
            receipt["error_class"] = type(exc).__name__
    after = {"contract_sha256": sha(contract_path), "native_source_sha256": sha(source_proof)}
    requests_ok = len(ledger) == 4 and all(row["status"] == "success" for row in ledger)
    validation = {"schema_version": "registered-magnetic-metadata-validation-v1", "status": "passed" if requests_ok and before == after else "failed",
                  "all_inputs_hashes_unchanged": before == after, "input_hashes": before, "request_count": len(ledger),
                  "successful_requests": sum(x["status"] == "success" for x in ledger), "total_archived_response_bytes": sum(x["bytes"] for x in ledger),
                  "HTTP_retry_count": 0, "code_sha256": sha(__file__), "no_fit_predict_plot": True,
                  "scientific_acceptance": "not_reviewed", "actual_JW_tool_execution": "requires_external_native_driver_receipt",
                  "quality_policy": "Only exact QUALITY=0; nonzero words preserved and rejected; no bit cleared",
                  "physical_domain_policy": "All fixed metrics except signed MEANALP must be nonnegative; R_VALUE=0 is legal; raw rejected values retained",
                  "full_history_or_image_downloads": False}
    compact_snapshots = {key: {k: v for k, v in value.items() if k != "audit"} for key, value in snapshots.items()}
    summary = {"schema_version": "registered-magnetic-metadata-summary-v1", "status": validation["status"],
               "source_origin": contract["source_proof"], "native_source_proof_path": str(source_proof), "endpoint_implementation_provenance": contract["endpoint_implementation_provenance"],
               "series": {key: value["summary"] for key, value in schemas.items()}, "snapshots": compact_snapshots,
               "physical_metrics": METRICS, "proposed_main_features": FEATURES, "requests": {"count": len(ledger), "success": validation["successful_requests"], "total_bytes": validation["total_archived_response_bytes"]},
               "readiness": "strict_zero_patch_available" if snapshots.get("current_nrt", {}).get("eligible_strict_zero_patches", 0) else "no_qualified_current_magnetic_input",
               "limits": ["One snapshot cannot certify a full-year calendar or training feasibility", "One retained patch does not establish full visible-patch coverage; rejected central/unknown-geometry patches are separately flagged", "QUALITY0x400 remains unverified and is not whitelisted", "DATE is a keyword production timestamp, not a complete archive latency history", "Definitive and NRT HARP IDs are not assumed equal", "TOTPOT schema units may differ from2014 SHARP Table3 proxy units; no unit reinterpretation here", "No physical model fitted or forecast issued", "Native JW execution must be established by the external actual-tool receipt"],
               "new_source_terms": "Unverified JSOC/HMI usage policy; archive supplied schema units literally, no inferred SPDX license"}
    write_json(output / "source_requests.json", {"schema_version": "bounded-source-requests-v1", "requests": ledger})
    write_json(output / "source_schema.json", schemas)
    write_json(output / "snapshot_audit.json", snapshots)
    write_json(output / "summary.json", summary)
    write_json(output / "validation.json", validation)
    files = [{"path": str(p.relative_to(output)).replace("\\", "/"), "bytes": p.stat().st_size, "sha256": sha(p)} for p in sorted(output.rglob("*")) if p.is_file()]
    manifest = {"schema_version": "bounded-magnetic-metadata-manifest-v1", "contract_sha256": before["contract_sha256"],
                "native_source_sha256": before["native_source_sha256"], "files": files, "code_sha256": sha(__file__),
                "environment": {"python": platform.python_version(), "astropy": astropy.__version__, "numpy": np.__version__, "requests": requests.__version__},
                "execution_kind": "registered_source_probe_requires_external_native_call_receipt", "no_fit_predict_plot": True}
    write_json(output / "metadata_manifest.json", manifest)
    print(json.dumps({"status": validation["status"], "requests": len(ledger), "readiness": summary["readiness"], "manifest_path": "metadata_manifest.json"}))
    return validation


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--contract", type=Path)
    p.add_argument("--source-proof", type=Path)
    p.add_argument("--response", type=Path)
    p.add_argument("--source-profile", type=Path)
    p.add_argument("--reference-utc")
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--available-by-utc")
    a = p.parse_args()
    if a.contract:
        if not a.source_proof or any((a.response, a.source_profile, a.reference_utc, a.available_by_utc)):
            p.error("Contract mode requires --source-proof and excludes offline response/profile/time parameters")
        frozen = load_registered_contract(a.contract, a.source_proof)
        result = execute_probe(frozen, a.source_proof, a.contract, a.output_dir)
        raise SystemExit(0 if result["status"] == "passed" else 2)
    if a.source_proof or not all((a.response, a.source_profile, a.reference_utc)):
        p.error("Offline mode requires --response --source-profile --reference-utc")
    offline_validate(a.response, a.source_profile, a.reference_utc, a.output_dir, a.available_by_utc)
