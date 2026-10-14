"""Bounded SHARP NRT acquisition; no historical download, fitting or issuance.

Production fetches are made only when the fresh JW Data role invokes this CLI.
Tests inject synthetic transports. Acceptance here is input qualification, not
a scientific review or a claim that QUALITY=1024 is harmless.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import http.client
import json
import math
from pathlib import Path
import platform
import re
import statistics
import time
from urllib.parse import urlencode, urlsplit

import astropy
from astropy.time import Time
from astropy.utils import iers

iers.conf.auto_download = False
UTC = timezone.utc
SERIES = "hmi.sharp_cea_720s_nrt"
ENDPOINT = "http://jsoc.stanford.edu/cgi-bin/ajax/jsoc_info"
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
    "MEANPOT",
]
FEATURES = [f"{name}_{method}" for name in METRICS for method in ("max", "median")] + [
    "patch_count"
]
METADATA = [
    "HARPNUM",
    "T_REC",
    "T_OBS",
    "DATE",
    "QUALITY",
    "QUAL_S",
    "QUALLEV1",
    "ARM_QUAL",
    "NOAA_AR",
    "NOAA_NUM",
    "NOAA_ARS",
    "LON_FWT",
    "LAT_FWT",
    "CRLN_OBS",
    "CRVAL1",
    "CRVAL2",
    "OBS_VR",
    "BLD_VERS",
    "CALVER64",
    "CODEVER7",
]
REQUIRED = METADATA + METRICS
LIMITS = {
    "HTTP_requests_max": 2,
    "HTTP_request_timeout_seconds": 30,
    "response_bytes_each_max": 2 * 1024 * 1024,
    "total_response_bytes_max": 4 * 1024 * 1024,
    "HTTP_retries": 0,
    "lookback_hours": 12,
    "cadence_seconds": 720,
    "support_half_width_seconds": 675,
    "max_input_age_seconds": 10800,
    "max_abs_cmd_degrees": 60,
    "accepted_QUALITY_words": [1024],
    "secondary_highbit_policy": "reject_pending_source_review",
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def utc_stamp(stamp):
    return stamp.astimezone(UTC).isoformat().replace("+00:00", "Z")


def date_utc(value):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("explicit_UTC_Z_required")
    stamp = datetime.fromisoformat(value[:-1] + "+00:00")
    if stamp.tzinfo != UTC or not 2010 <= stamp.year <= 2026:
        raise ValueError("invalid_UTC_epoch")
    return stamp


def tai_utc(value):
    match = re.fullmatch(
        r"(\d{4})\.(\d{2})\.(\d{2})_(\d{2}):(\d{2}):(\d{2}(?:\.\d+)?)_TAI", str(value)
    )
    if match is None:
        raise ValueError("explicit_HMI_TAI_required")
    year, month, day, hour, minute, second = match.groups()
    if not 2010 <= int(year) <= 2026:
        raise ValueError("invalid_TAI_epoch")
    return Time(
        f"{year}-{month}-{day}T{hour}:{minute}:{second}", format="isot", scale="tai"
    ).utc.to_datetime(timezone=UTC, leap_second_strict="raise")


def tai_stamp(value):
    return Time(value, scale="utc").tai.strftime("%Y.%m.%d_%H:%M:%S_TAI")


def live_query(now):
    """Query all HARP prime keys at every native slot in the prior 12 hours.

    Align the explicit endpoints to the series' 720-second TAI slots rather
    than ask a slotted query to round an arbitrary current second.
    """
    if now.tzinfo is None:
        raise ValueError("aware_live_query_clock_required")
    requested_start, requested_end = now - timedelta(hours=12), now
    civil_start = Time(requested_start, scale="utc").tai.to_datetime()
    civil_end = Time(requested_end, scale="utc").tai.to_datetime()
    first = civil_start.replace(
        minute=(civil_start.minute // 12) * 12, second=0, microsecond=0
    )
    if first < civil_start:
        first += timedelta(minutes=12)
    last = civil_end.replace(
        minute=(civil_end.minute // 12) * 12, second=0, microsecond=0
    )
    first_tai, last_tai = (
        stamp.strftime("%Y.%m.%d_%H:%M:%S_TAI") for stamp in (first, last)
    )
    return {
        "id": "recent_keywords",
        "parameters": {
            "op": "rs_list",
            "ds": SERIES + "[][" + first_tai + "-" + last_tai + "@12m]",
            "key": ",".join(REQUIRED),
            "n": "0",
        },
        "requested_lookback_start_utc": utc_stamp(requested_start),
        "requested_lookback_end_utc": utc_stamp(requested_end),
        "first_native_slot_tai": first_tai,
        "last_native_slot_tai": last_tai,
        "first_native_slot_utc": utc_stamp(tai_utc(first_tai)),
        "last_native_slot_utc": utc_stamp(tai_utc(last_tai)),
        "HARP_selection": "all_HARP_empty_first_prime_key",
        "native_cadence_seconds": 720,
    }


def quality_word(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("invalid_quality_word")
    text = str(value)
    word = int(text, 16 if text.lower().startswith("0x") else 10)
    if not -(2**31) <= word < 2**32:
        raise ValueError("quality_word_out_of_range")
    return word & 0xFFFFFFFF


def decode_keywords(payload):
    if not isinstance(payload, dict) or payload.get("status") != 0:
        raise ValueError("JSOC_source_error")
    count = payload.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("invalid_source_count")
    columns = {}
    for column in payload.get("keywords", []):
        if not isinstance(column, dict):
            raise ValueError("invalid_source_column")
        name, values = column.get("name"), column.get("values")
        if (
            not isinstance(name, str)
            or name in columns
            or not isinstance(values, list)
            or len(values) != count
        ):
            raise ValueError("duplicate_or_misaligned_source_arrays")
        columns[name] = values
    if set(REQUIRED) - set(columns):
        raise ValueError("missing_requested_source_keyword")
    return [{key: values[i] for key, values in columns.items()} for i in range(count)]


def load_contract(path):
    contract = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    live = contract["live_source"]
    if live.get("series") != SERIES or live.get("endpoint") != ENDPOINT:
        raise ValueError("live_product_or_endpoint_changed")
    if any(live.get(key) != value for key, value in LIMITS.items()):
        raise ValueError("registered_live_limits_changed")
    baseline = Path(live["schema_literal_baseline_path"])
    if sha(baseline) != live["schema_literal_baseline_sha256"]:
        raise ValueError("schema_baseline_hash_mismatch")
    versions = live.get("known_version_literals", {})
    if set(versions) != {"BLD_VERS", "CALVER64", "CODEVER7"} or any(
        not isinstance(v, list)
        or not v
        or any(not isinstance(x, str) or not x.strip() for x in v)
        for v in versions.values()
    ):
        raise ValueError("explicit_version_literal_cohort_required")
    proofs = live.get("source_policy_proofs", {})
    if not proofs or any(
        not isinstance(v, dict) or sha(v["path"]) != v["sha256"]
        for v in proofs.values()
    ):
        raise ValueError("source_policy_proof_hash_mismatch")
    return contract


def schema_fields(payload):
    if not isinstance(payload, dict) or payload.get("status") != 0:
        raise ValueError("invalid_live_schema")
    result = {}
    for item in payload.get("keywords", []):
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("name"), str)
            or item["name"] in result
        ):
            raise ValueError("duplicate_or_invalid_schema_keyword")
        result[item["name"]] = item
    if set(REQUIRED) - set(result):
        raise ValueError("incomplete_live_schema")
    return result


def validate_schema(payload, live):
    original = json.loads(
        Path(live["schema_literal_baseline_path"]).read_text(encoding="utf-8-sig")
    )
    if SERIES in original:
        original = original[SERIES]["full_source_schema"]
    fields, baseline = schema_fields(payload), schema_fields(original)
    # Compare type, units, definitions, version semantics literally. Interval
    # last-record metadata is expected to change and is archived, not compared.
    for name in REQUIRED:
        if fields[name] != baseline[name]:
            raise ValueError("live_schema_literal_drift_" + name)
    for name in ("T_REC_epoch", "T_REC_step", "T_REC_unit", "CADENCE"):
        if name in baseline and fields.get(name) != baseline[name]:
            raise ValueError("live_schema_literal_drift_" + name)
    for name in ("primekeys", "primekeysinfo"):
        if name in original and payload.get(name) != original[name]:
            raise ValueError("live_schema_literal_drift_" + name)
    if fields["LON_FWT"].get(
        "units"
    ) != "degree" or "Stonyhurst longitude" not in fields["LON_FWT"].get("note", ""):
        raise ValueError("unverified_live_geometry")
    return {
        "status": "passed",
        "required_keyword_definitions_literal_match": True,
        "physical_keyword_units": {name: fields[name]["units"] for name in METRICS},
        "version_keyword_definitions": {
            name: fields[name] for name in live["known_version_literals"]
        },
        "Interval_raw": payload.get("Interval"),
        "schema_baseline_sha256": live["schema_literal_baseline_sha256"],
    }


def qualify_groups(rows, retrieved_utc, query_start_utc, query_end_utc, live):
    retrieved, query_start, query_end = map(
        date_utc, (retrieved_utc, query_start_utc, query_end_utc)
    )
    groups = defaultdict(list)
    # A record with no interpretable nominal time cannot safely be assigned to
    # an older group. Reject the response rather than discard that record.
    for row in rows:
        nominal = tai_utc(row["T_REC"])
        if not query_start <= nominal <= query_end:
            raise ValueError("source_record_outside_requested_interval")
        groups[nominal].append(row)
    group_audits, selected = [], None
    for nominal in sorted(groups, reverse=True):
        group = groups[nominal]
        keys = Counter(str(row.get("HARPNUM")) for row in group)
        group_reasons, row_audits, central = [], [], []
        if any(value > 1 for value in keys.values()):
            group_reasons.append("duplicate_HARP_rejects_entire_group")
        for row in group:
            reasons, values, times, words = [], {}, {}, {}
            try:
                harp = str(row["HARPNUM"])
                if not re.fullmatch(r"[1-9]\d*", harp):
                    raise ValueError("invalid_HARPNUM")
            except (KeyError, TypeError, ValueError):
                reasons.append("invalid_HARPNUM")
            try:
                lon = float(row["LON_FWT"])
                if not math.isfinite(lon) or abs(lon) > 180:
                    raise ValueError("invalid_geometry")
                is_central = abs(lon) <= 60
            except (KeyError, TypeError, ValueError):
                lon, is_central = None, None
                reasons.append("unknown_geometry")
                group_reasons.append("unknown_geometry_rejects_entire_group")
            if is_central:
                try:
                    observed, processed = tai_utc(row["T_OBS"]), date_utc(row["DATE"])
                    support_end = observed + timedelta(seconds=675)
                    age = (retrieved - observed).total_seconds()
                    times = {
                        "observation_utc": utc_stamp(observed),
                        "support_start_utc": utc_stamp(
                            observed - timedelta(seconds=675)
                        ),
                        "support_end_utc": utc_stamp(support_end),
                        "processing_utc": utc_stamp(processed),
                        "input_age_seconds": age,
                    }
                    if processed < observed:
                        reasons.append("processing_before_observation")
                    if processed > retrieved or support_end > retrieved:
                        reasons.append("input_not_available_at_retrieval")
                    if not 0 <= age <= 10800:
                        reasons.append("input_age_exceeds_three_hours_or_future")
                except (KeyError, ValueError, OverflowError):
                    reasons.append("invalid_explicit_input_clock")
                for field in ("QUALITY", "QUAL_S", "QUALLEV1", "ARM_QUAL"):
                    try:
                        words[field] = quality_word(row[field])
                        if field == "QUALITY" and words[field] != 1024:
                            reasons.append("QUALITY_not_exact_full_1024")
                        if field != "QUALITY" and words[field] & 0x80000000:
                            reasons.append(
                                "secondary_highbit_pending_source_review_" + field
                            )
                    except (KeyError, ValueError):
                        reasons.append("invalid_" + field)
                for field, allowed in live["known_version_literals"].items():
                    if row.get(field) not in allowed:
                        reasons.append("unreviewed_version_literal_" + field)
                for name in METRICS:
                    try:
                        value = float(row[name])
                        if not math.isfinite(value):
                            raise ValueError("nonfinite")
                        if name != "MEANALP" and value < 0:
                            reasons.append(
                                "negative_nonnegative_physical_domain_" + name
                            )
                        values[name] = value
                    except (KeyError, TypeError, ValueError):
                        reasons.append("missing_or_nonfinite_" + name)
            item = {
                "source_record_key": SERIES
                + ":"
                + str(row.get("HARPNUM"))
                + ":"
                + str(row.get("T_REC")),
                "is_central": is_central,
                "status": "rejected"
                if reasons
                else "eligible"
                if is_central
                else "outside_central_domain",
                "reasons": reasons,
                "LON_FWT_float": lon,
                "raw_record": row,
                "quality_words_parsed": words,
                "times": times,
                "metric_values": values,
            }
            row_audits.append(item)
            if is_central:
                central.append(item)
        if not central:
            group_reasons.append("no_central_patch_is_unknown_not_zero")
        if any(item["status"] != "eligible" for item in central):
            group_reasons.append("central_patch_rejected_no_partial_patch_aggregation")
        group_audit = {
            "nominal_utc": utc_stamp(nominal),
            "T_REC_raw": group[0]["T_REC"],
            "input_rows": len(group),
            "candidate_central_patch_count": len(central),
            "reasons": sorted(set(group_reasons)),
            "status": "rejected" if group_reasons else "eligible",
            "rows": row_audits,
        }
        group_audits.append(group_audit)
        if selected is None and not group_reasons:
            selected = central
            group_audit["selected_latest_complete_qualified_group"] = True
    if selected is None:
        return {
            "status": "unknown",
            "reason": "no_latest_complete_qualified_group",
            "groups": group_audits,
        }, None
    feature_values = {}
    for name in METRICS:
        vals = [item["metric_values"][name] for item in selected]
        feature_values[name + "_max"] = max(vals)
        feature_values[name + "_median"] = statistics.median(vals)
    feature_values["patch_count"] = len(selected)
    observed = max(date_utc(item["times"]["observation_utc"]) for item in selected)
    oldest_observed = min(
        date_utc(item["times"]["observation_utc"]) for item in selected
    )
    support_end = max(date_utc(item["times"]["support_end_utc"]) for item in selected)
    processing = max(date_utc(item["times"]["processing_utc"]) for item in selected)
    first = selected[0]["raw_record"]
    sample_id = "live:" + first["T_REC"]
    sample = {
        "sample_id": sample_id,
        "status": "eligible",
        "observation_utc": utc_stamp(observed),
        "oldest_patch_observation_utc": utc_stamp(oldest_observed),
        "hmi_nominal_tai": first["T_REC"],
        "hmi_latest_support_end_utc": utc_stamp(support_end),
        "hmi_latest_processing_utc": utc_stamp(processing),
        "source_retrieved_utc": retrieved_utc,
        "input_age_seconds_at_retrieval": (retrieved - oldest_observed).total_seconds(),
        "history_baseline_status": "unknown_not_issued",
        "source_series": SERIES,
        "QUALITY_exact_full_word": 1024,
        **feature_values,
    }
    quality_histograms = {
        field: dict(Counter(str(item["raw_record"][field]) for item in selected))
        for field in ("QUALITY", "QUAL_S", "QUALLEV1", "ARM_QUAL")
    }
    versions = {
        field: sorted(set(item["raw_record"][field] for item in selected))
        for field in live["known_version_literals"]
    }
    result = {
        "status": "eligible",
        "sample_id": sample_id,
        "selected_nominal_tai": first["T_REC"],
        "central_patch_count": len(selected),
        "input_rows": len(rows),
        "quality_raw_histograms": quality_histograms,
        "version_literal_values": versions,
        "partial_patch_aggregation": False,
        "groups": group_audits,
        "no_history_features_created": True,
        "scientific_acceptance": "not_reviewed",
        "limitations": [
            "Completeness refers to all returned candidate central patches; keyword response does not independently prove all physical regions exist in source.",
            "Exact QUALITY=1024 remains a restricted warning cohort, not a harmlessness certificate.",
            "Secondary quality namespaces are retained independently and not treated as equivalent.",
            "Secondary bit31 words are conservatively excluded pending source-specific review; no universal meaning is assigned to that bit.",
            "SHARP scalar central-patch input and full-sun flare labels differ spatially; no pixel/FITS download.",
            "DATE is processing time; original T_OBS is observation time, and real retrieval is separate.",
            "Source version/license certification remains limited to frozen proof and literal schema comparison.",
        ],
    }
    return result, sample


class BoundedHTTPResponse:
    """One plain-HTTP request with a total deadline, no redirect or retry.

    read1 makes at most one underlying read, so trickling response bytes
    cannot reset a per-read 30-second allowance indefinitely.
    """

    def __init__(self, url, timeout):
        parsed = urlsplit(url)
        if parsed.scheme != "http" or parsed.netloc != "jsoc.stanford.edu":
            raise ValueError("unregistered_HTTP_origin")
        self.url, self.deadline = url, time.monotonic() + timeout
        self.connection = http.client.HTTPConnection(parsed.netloc, timeout=timeout)
        self.response = None
        try:
            self.connection.connect()
            self._set_timeout()
            self.connection.request(
                "GET",
                parsed.path + "?" + parsed.query,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "JW-available24-live/1",
                },
            )
            self._set_timeout()
            self.response = self.connection.getresponse()
            self.status_code = self.response.status
            self.headers = dict(self.response.getheaders())
        except Exception:
            self.close()
            raise

    def _set_timeout(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("total_HTTP_deadline_exceeded")
        sock = self.connection.sock
        # HTTP/1.0 Connection: close transfers ownership of the socket to the
        # response file; this CPython stdlib socket still needs the deadline.
        if sock is None and self.response is not None and self.response.fp is not None:
            sock = self.response.fp.raw._sock
        if sock is not None:
            sock.settimeout(remaining)

    def iter_content(self, chunk_size):
        while True:
            self._set_timeout()
            chunk = self.response.read1(chunk_size)
            if not chunk:
                break
            yield chunk

    def close(self):
        if self.response is not None:
            self.response.close()
        self.connection.close()


def request_once(url, timeout):
    return BoundedHTTPResponse(url, timeout)


def bounded_fetch(query, output, ledger, transport, clock):
    url = ENDPOINT + "?" + urlencode(query["parameters"])
    receipt = {
        **{key: value for key, value in query.items() if key != "id"},
        "query_id": query["id"],
        "parameters": query["parameters"],
        "url": url,
        "attempt": 1,
        "retry_count": 0,
        "timeout_seconds": 30,
        "status": "failed",
        "started_utc": utc_stamp(clock()),
        "bytes": 0,
        "raw_path": "raw/" + query["id"] + ".bin",
    }
    if len(ledger) >= 2:
        raise ValueError("HTTP_request_allowance_exhausted")
    ledger.append(receipt)
    body, response, started = bytearray(), None, time.monotonic()
    try:
        response = transport(url, 30)
        receipt["status_code"], receipt["final_url"] = (
            response.status_code,
            response.url,
        )
        receipt["headers"] = dict(response.headers)
        if response.status_code != 200 or response.url != url:
            raise ValueError("HTTP_non200_or_redirect")
        content_length = response.headers.get("Content-Length")
        if content_length is not None and int(content_length) > 2 * 1024 * 1024:
            raise ValueError("declared_response_exceeds_byte_cap")
        prior_bytes = sum(item["bytes"] for item in ledger[:-1])
        for chunk in response.iter_content(chunk_size=16384):
            if time.monotonic() - started > 30:
                raise TimeoutError("total_response_duration_exceeded")
            if not chunk:
                continue
            allowance = min(
                2 * 1024 * 1024 - len(body), 4 * 1024 * 1024 - prior_bytes - len(body)
            )
            if len(chunk) > allowance:
                body.extend(chunk[: max(0, allowance)])
                receipt["truncated_at_registered_byte_cap"] = True
                raise ValueError("response_byte_cap_exceeded")
            body.extend(chunk)
        if time.monotonic() - started > 30:
            raise TimeoutError("total_response_duration_exceeded")
        payload = json.loads(body.decode("utf-8-sig"))
        if not isinstance(payload, dict) or payload.get("status") != 0:
            raise ValueError("JSOC_status_not_zero")
        receipt["status"] = "success"
        return payload, receipt
    except Exception as exc:
        receipt["error_class"] = type(exc).__name__
        return None, receipt
    finally:
        if response is not None:
            response.close()
        raw = output / receipt["raw_path"]
        raw.write_bytes(body)
        receipt.update(
            {
                "bytes": len(body),
                "sha256": sha(raw),
                "retrieved_utc": utc_stamp(clock()),
                "observed_wall_seconds": time.monotonic() - started,
            }
        )


def fetch(
    contract_path, output_dir, transport=request_once, clock=lambda: datetime.now(UTC)
):
    contract_path, output = Path(contract_path), Path(output_dir)
    if output.exists():
        raise ValueError("refusing_existing_output_directory")
    contract = load_contract(contract_path)
    live = contract["live_source"]
    contract_sha = sha(contract_path)
    readonly_inputs = {
        str(contract_path): contract_sha,
        live["schema_literal_baseline_path"]: live["schema_literal_baseline_sha256"],
        **{
            value["path"]: value["sha256"]
            for value in live["source_policy_proofs"].values()
        },
    }
    output.mkdir(parents=True)
    (output / "raw").mkdir()
    ledger, row_audit, sample, schema_result = (
        [],
        {"status": "not_attempted"},
        None,
        None,
    )
    qualification = {
        "status": "failed",
        "reason": "source_not_completed",
        "forecast_issued": False,
        "fit_predict_plot_performed": False,
        "actual_JW_invocation": "requires_external_native_receipt",
        "future_outcome_read": False,
    }
    try:
        schema, receipt = bounded_fetch(
            {"id": "schema", "parameters": {"op": "series_struct", "ds": SERIES}},
            output,
            ledger,
            transport,
            clock,
        )
        if schema is None:
            raise ValueError("schema_request_failed")
        schema_result = validate_schema(schema, live)
        schema_result["raw_schema_response_sha256"] = receipt["sha256"]
        query = live_query(clock())
        payload, receipt = bounded_fetch(query, output, ledger, transport, clock)
        if payload is None:
            raise ValueError("keyword_request_failed")
        rows = decode_keywords(payload)
        row_audit, sample = qualify_groups(
            rows,
            receipt["retrieved_utc"],
            query["first_native_slot_utc"],
            query["last_native_slot_utc"],
            live,
        )
        row_audit["source_response_sha256"] = receipt["sha256"]
        qualification.update(
            {key: value for key, value in row_audit.items() if key != "groups"}
        )
        qualification["reason"] = row_audit.get(
            "reason", "latest_complete_qualified_group"
        )
        if sample is not None:
            with (output / "live_physical.csv").open(
                "w", encoding="utf-8", newline=""
            ) as handle:
                writer = csv.DictWriter(handle, fieldnames=list(sample))
                writer.writeheader()
                writer.writerow(sample)
            qualification["live_physical_csv_sha256"] = sha(
                output / "live_physical.csv"
            )
        if any(sha(path) != expected for path, expected in readonly_inputs.items()):
            raise ValueError("frozen_contract_or_source_proof_changed_during_fetch")
    except Exception as exc:
        qualification.update(
            {
                "status": "failed",
                "error_class": type(exc).__name__,
                "reason": "controlled_acquisition_or_qualification_failed",
            }
        )
        # Invalidate any output created before a late input-integrity failure.
        if (output / "live_physical.csv").exists():
            qualification["live_physical_csv_eligible"] = False
    write_json(
        output / "source_requests.json",
        {
            "schema_version": "available24-source-requests-v1",
            "requests": ledger,
            "request_count": len(ledger),
            "HTTP_retry_count": 0,
        },
    )
    write_json(output / "source_schema_validation.json", schema_result)
    write_json(output / "row_audit.json", row_audit)
    qualification.update(
        {
            "schema_version": "available24-live-qualification-v1",
            "contract_sha256": contract_sha,
            "code_sha256": sha(__file__),
            "source_requests": len(ledger),
            "HTTP_retries": 0,
            "scientific_acceptance": "not_reviewed",
            "secondary_highbit_policy": "reject_pending_source_review",
            "source_manifest_path": "source_manifest.json",
        }
    )
    write_json(output / "live_qualification.json", qualification)
    files = [
        {
            "path": str(path.relative_to(output)).replace("\\", "/"),
            "bytes": path.stat().st_size,
            "sha256": sha(path),
        }
        for path in sorted(output.rglob("*"))
        if path.is_file()
    ]
    manifest = {
        "schema_version": "available24-live-source-manifest-v1",
        "status": qualification["status"],
        "contract_sha256": contract_sha,
        "code_sha256": sha(__file__),
        "files": files,
        "readonly_input_hashes": readonly_inputs,
        "source": {
            "series": SERIES,
            "endpoint": ENDPOINT,
            "schema_literal_baseline_sha256": live["schema_literal_baseline_sha256"],
            "known_version_literals": live["known_version_literals"],
            "source_policy_proofs": live["source_policy_proofs"],
            "license_or_terms": "Unverified JSOC/HMI policy; no inferred SPDX or new license certificate",
            "version_scope": "Current supplied row literals compared to frozen cohort; deployed executable equivalence unproven",
        },
        "HTTP": {
            "count": len(ledger),
            "retry_count": 0,
            "response_bytes_archived": sum(item["bytes"] for item in ledger),
        },
        "environment": {
            "python": platform.python_version(),
            "astropy": astropy.__version__,
            "HTTP_transport": "Python stdlib http.client; one connection/request, no retry/redirect",
        },
        "forecast_issued": False,
        "fit_predict_plot_performed": False,
        "future_outcome_read": False,
    }
    write_json(output / "source_manifest.json", manifest)
    return qualification


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    command = sub.add_parser("fetch")
    command.add_argument("--contract", type=Path, required=True)
    command.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = fetch(args.contract, args.output_dir)
    print(
        json.dumps(
            {
                "status": result["status"],
                "source_requests": result["source_requests"],
                "forecast_issued": False,
                "qualification": str(args.output_dir / "live_qualification.json"),
            },
            ensure_ascii=True,
        )
    )
    return 0 if result["status"] == "eligible" else 2


if __name__ == "__main__":
    raise SystemExit(main())
