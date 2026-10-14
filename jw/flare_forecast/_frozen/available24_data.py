"""Frozen-input preparation for an availability-anchored 24 hour experiment.

Preparation is invoked by the new native JW Data tool, never by the operator
to manufacture a model result. Pure helpers below also support synthetic QA.
No fitting, scientific plotting, HTTP, service startup or forecast issuance.
"""

from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import sys
import time

UTC = timezone.utc
SOURCE_MODE = "hmi.sharp_cea_720s_nrt"
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
FEATURES = [
    f"{name}_{aggregation}" for name in METRICS for aggregation in ("max", "median")
] + ["patch_count"]
HISTORY = [
    f"history_{hours}h_{kind}"
    for hours in (24, 72, 168)
    for kind in ("c_count", "m_count", "x_count", "max_peak_w_m2")
]
ROLES = ["train", "selection", "calibration", "test"]
SPLITS = {
    "train": ["2011-01-01", "2020-12-31"],
    "selection": ["2021-01-01", "2021-12-31"],
    "calibration": ["2022-01-01", "2022-12-31"],
    "test": ["2023-01-01", "2025-12-31"],
}
CLOCK_POLICY = "max_central_support_end_and_DATE_plus_1_second"
LIMITS = [
    "Historical DATE is a keyword production timestamp, not proved first archive publication or historical retrieval availability.",
    "Inputs are magnetic-map-derived SHARP scalars, not pixel magnetograms, EUV or plasma density.",
    "Complete queried central patches within abs(Stonyhurst longitude)<=60 degrees are not complete whole-Sun magnetic coverage; labels are whole-Sun GOES new starts.",
    "Only complete primary QUALITY word1024 is admitted; cosmic-ray-hit-list warning and secondary words remain, without a harmlessness certificate.",
    "The NRT product lacks2011 and early2012 and early R_VALUE is missing. No magnetic or outcome missing value is filled with zero.",
    "Catalog-negative means no catalogued threshold event under the registered observability gates, not proof of physical absence or a complete operational forecast.",
    "GOES historical source files are reused original operator downloads; SHARP files were obtained by prior genuine JW calls, not new downloads in preparation.",
    "Source versions, JSOC/HMI license uncertainty, previous failed stages and old scientific reviews are retained; this interface validation is not scientific acceptance.",
]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def utc(value):
    return (
        value.astimezone(UTC).isoformat().replace("+00:00", "Z")
        if value is not None
        else ""
    )


def parse_utc(value, *, catalog_naive=False):
    if value is None or str(value).strip() in {"", "MISSING", "nan", "NaT"}:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        if not catalog_naive:
            return None
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC)


def finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def write_json(path, value):
    Path(path).write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def write_csv(path, rows, required=()):
    columns = list(dict.fromkeys([*required, *(key for row in rows for key in row)]))
    with Path(path).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def safe_path(root, relative):
    root = Path(root).resolve()
    child = Path(relative)
    if child.is_absolute() or ".." in child.parts:
        raise ValueError("Source/product paths must be relative without traversal")
    resolved = (root / child).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Source/product path escapes registered root")
    return resolved


def check_contract(contract):
    if contract.get("schema_version") != "available24-qualification-contract-v1":
        raise ValueError("New available24 qualification contract required")
    expected = {
        "source_mode": SOURCE_MODE,
        "splits": SPLITS,
        "accepted_QUALITY_words": [1024],
        "max_observation_age_seconds": 10800,
        "support_half_width_seconds": 675,
        "historical_clock_policy": CLOCK_POLICY,
        "issue_padding_seconds": 1,
        "target_duration_seconds": 86400,
        "history_completion_lag_hours": 3,
        "history_missing_end_policy": "unknown_not_zero",
        "coverage_fraction_min": 0.99,
        "coverage_max_gap_seconds": 300,
        "target_spatial_scope": "whole_sun_GOES_catalog_no_AR_filter",
        "historical_nominal_tai_hour": 13,
        "test_target_end_max_utc": "2026-01-01T00:00:00Z",
        "prepare_allowed": True,
        "input_readonly": True,
    }
    for key, value in expected.items():
        if contract.get(key) != value:
            raise ValueError("Unregistered data policy differs: " + key)
    if (
        contract.get("features_physical") != FEATURES
        or contract.get("features_history_baseline") != HISTORY
    ):
        raise ValueError("Exact23 physical and12 baseline feature interfaces required")
    for key in ("source_manifest_sha256", "quality_policy_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(contract.get(key, ""))):
            raise ValueError("Exact frozen source/policy identity required: " + key)
    if contract.get("label_policy") != "start_half_open_peak_threshold_end_optional":
        raise ValueError(
            "Explicit new-start, known-peak, optional-end label policy required"
        )


def load_frozen(contract, before):
    root = Path(__file__).with_name("frozen_shared")
    registered = contract.get("frozen_helpers")
    if not isinstance(registered, dict) or not registered:
        raise ValueError("Frozen helper hashes must be preregistered")
    for name, digest in registered.items():
        path = safe_path(root, name)
        if not re.fullmatch(r"[0-9a-f]{64}", str(digest)) or sha(path) != digest:
            raise ValueError("Frozen helper bytes differ: " + name)
        before[str(path)] = digest
    name = "acquire_prepare_magnetic.frozen_source_only_01.py"
    if name not in registered:
        raise ValueError("Registered frozen source parser required")
    spec = importlib.util.spec_from_file_location(
        "available24_frozen_source", root / name
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    gateway, history, core = module.load_modules()
    return module, gateway, history, core


def verify_quality_decision(contract, before):
    record = contract.get("quality_decision_file")
    if not isinstance(record, dict) or not Path(record.get("path", "")).is_absolute():
        raise ValueError("Exact absolute quality source decision record required")
    path = Path(record["path"]).resolve()
    if (
        record.get("sha256") != contract["quality_policy_sha256"]
        or sha(path) != record["sha256"]
    ):
        raise ValueError("Actual independently reviewed quality decision hash differs")
    decision = json.loads(path.read_text(encoding="utf-8-sig"))
    if (
        decision.get("schema") != "physical-quality-source-decision-v1"
        or decision.get("status") != "accepted_with_limits"
        or decision.get("series") != [SOURCE_MODE]
        or decision.get("accepted_words") != [1024]
    ):
        raise ValueError("Reviewed exact1024 NRT source conditions required")
    if (
        decision.get("scope", {}).get("fit_predict_plot_authorized_by_this_review")
        is not False
    ):
        raise ValueError(
            "A source decision cannot substitute for native Data or authorize fit"
        )
    before[str(path)] = record["sha256"]
    proofs = decision.get("proof_files")
    if not isinstance(proofs, list) or not proofs:
        raise ValueError(
            "Actual raw-source and native receipts required for quality policy"
        )
    seen = set()
    for proof in proofs:
        proof_path = Path(proof["path"])
        if not proof_path.is_absolute() or str(proof_path) in seen:
            raise ValueError(
                "Unique absolute original quality proof identities required"
            )
        seen.add(str(proof_path))
        if (
            sha(proof_path) != proof["sha256"]
            or proof_path.stat().st_size != proof["bytes"]
        ):
            raise ValueError("Original quality proof bytes differ")
        before[str(proof_path.resolve())] = proof["sha256"]
    return decision


class SecondsCoverage:
    """Integrate valid source minute intervals over exact arbitrary UTC windows."""

    def __init__(self, minute_index):
        self.index = minute_index
        self.first = minute_index.first * 60
        self.end = minute_index.end * 60

    def _integral(self, seconds):
        offset = min(max(seconds - self.first, 0), self.end - self.first)
        minute = int(offset // 60)
        partial = offset - minute * 60
        prefix = self.index.prefix
        total = float(prefix[minute]) * 60
        if partial and minute + 1 < len(prefix):
            total += partial * int(prefix[minute + 1] - prefix[minute])
        return total

    def stats(self, start, end):
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ValueError("Ordered aware UTC window required")
        a, b = start.timestamp(), end.timestamp()
        valid_seconds = self._integral(b) - self._integral(a)
        duration = (end - start).total_seconds()
        lo, hi = max(a, self.first), min(b, self.end)
        gaps = []
        if a < self.first:
            gaps.append((a, min(b, self.first)))
        if b > self.end:
            gaps.append((max(a, self.end), b))
        if lo < hi:
            x = bisect_left(self.index.gap_ends, (lo - self.first) / 60)
            y = bisect_left(self.index.gap_starts, (hi - self.first) / 60)
            for begin, finish in zip(
                self.index.gap_starts[x:y], self.index.gap_ends[x:y]
            ):
                first = max(lo, self.first + int(begin) * 60)
                last = min(hi, self.first + int(finish) * 60)
                if last > first:
                    gaps.append((first, last))
        merged = []
        for first, last in sorted(gaps):
            if merged and first <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], last))
            else:
                merged.append((first, last))
        maximum = max((last - first for first, last in merged), default=0.0)
        fraction = valid_seconds / duration
        return {
            "valid_seconds": valid_seconds,
            "window_seconds": duration,
            "coverage_fraction": fraction,
            "max_gap_seconds": maximum,
            "qualified": fraction >= 0.99 and maximum <= 300,
        }


def normalize_events(raw_rows):
    """Peak/class maturity suffices for future labels; end gates past completion."""
    events, audit, identities = [], [], set()
    for ordinal, row in enumerate(raw_rows):
        start = parse_utc(row.get("start_time"), catalog_naive=True)
        peak_time = parse_utc(row.get("time"), catalog_naive=True)
        end = parse_utc(row.get("end_time"), catalog_naive=True)
        peak = finite(row.get("xrsb_irrad"))
        identity = (
            str(row.get("flare_id", "")),
            str(row.get("start_time", "")),
            str(row.get("time", "")),
        )
        common = {
            "row_ordinal": ordinal,
            "flare_id": identity[0],
            "source_file": row.get("source_file", ""),
            "raw_start_time": row.get("start_time", ""),
            "raw_peak_time": row.get("time", ""),
            "raw_end_time": row.get("end_time", ""),
            "raw_peak_flux": row.get("xrsb_irrad", ""),
            "flare_class": row.get("flare_class", ""),
            "peak_saturated": row.get("peak_saturated", "0"),
        }
        reasons = []
        if start is None:
            reasons.append("missing_or_invalid_start_time")
        if peak_time is None:
            reasons.append("missing_or_invalid_peak_time")
        if peak is None or peak <= 0:
            reasons.append("missing_or_nonpositive_peak_flux")
        if start is not None and peak_time is not None and peak_time < start:
            reasons.append("peak_before_start")
        if end is not None and start is not None and end < start:
            reasons.append("end_before_start")
        if end is not None and peak_time is not None and end < peak_time:
            reasons.append("end_before_peak")
        if identity in identities:
            reasons.append("duplicate_catalog_event")
        identities.add(identity)
        if reasons:
            audit.append({**common, "reasons": "|".join(reasons)})
            continue
        events.append(
            {**common, "start": start, "peak_time": peak_time, "end": end, "peak": peak}
        )
        if end is None:
            audit.append(
                {
                    **common,
                    "reasons": "end_missing_future_peak_known_history_ineligible",
                }
            )
    return sorted(events, key=lambda item: item["start"]), audit


def relevant_bad_events(audit, start, end):
    output = []
    for row in audit:
        if row["reasons"] == "end_missing_future_peak_known_history_ineligible":
            continue
        stamp = parse_utc(row.get("raw_start_time"), catalog_naive=True)
        if stamp is None:
            # A bad event without a start cannot safely be assigned outside a window.
            output.append(row)
        elif start <= stamp < end:
            output.append(row)
    return output


def label_certificate(
    start, end, event_index, event_audit, cutoff, catalog_start, catalog_end, coverage
):
    rows = event_index.window(start, end)
    known = [row for row in rows if row["peak_time"] <= cutoff]
    pending = [row for row in rows if row["peak_time"] > cutoff]
    bad = relevant_bad_events(event_audit, start, end)
    domain = (
        coverage["qualified"]
        and catalog_start <= start
        and end <= min(cutoff, catalog_end)
    )
    bits, proofs = {}, {}
    saturated = [
        row
        for row in known
        if str(row.get("peak_saturated", "0")) not in {"0", "0.0", ""}
    ]
    for name, threshold in (("c1", 1e-6), ("m1", 1e-5)):
        supporters = [row for row in known if row["peak"] >= threshold]
        unknown = bool(
            pending or bad or any(row["peak"] < threshold for row in saturated)
        )
        bits[name] = 1 if supporters else None if unknown or not domain else 0
        proofs[name] = [
            {
                "flare_id": row["flare_id"],
                "source_file": row["source_file"],
                "start_utc": utc(row["start"]),
                "peak_utc": utc(row["peak_time"]),
                "end_utc": utc(row["end"]),
                "peak_w_m2": row["peak"],
            }
            for row in supporters
        ]
    qualified = domain and all(bit is not None for bit in bits.values())
    return {
        "label_c1": bits["c1"],
        "label_m1": bits["m1"],
        "y_class": 2
        if bits["m1"] == 1 and qualified
        else bits["c1"]
        if qualified
        else None,
        "label_qualified": qualified,
        "catalog_domain_qualified": domain,
        "window_event_count": len(rows),
        "pending_peak_count": len(pending),
        "bad_event_count": len(bad),
        "end_missing_peak_known_count": sum(row["end"] is None for row in known),
        "known_positive_bit_certificates": proofs,
        "window_events": [
            {
                "flare_id": row["flare_id"],
                "source_file": row["source_file"],
                "start_utc": utc(row["start"]),
                "peak_utc": utc(row["peak_time"]),
                "end_utc": utc(row["end"]),
                "peak_w_m2": row["peak"],
            }
            for row in rows
        ],
        "quarantined_events": bad,
    }


def qualify_group(rows, gateway, expected_tai, quality_sha):
    """All queried central patches pass, or the whole magnetic sample is unknown."""
    keys = Counter((str(row.get("HARPNUM")), str(row.get("T_REC"))) for row in rows)
    central, audit, unknown_geometry = [], [], 0
    for row in rows:
        longitude = finite(row.get("LON_FWT"))
        if longitude is None:
            unknown_geometry += 1
            audit.append(
                {
                    "raw_row_json": json.dumps(row, ensure_ascii=False),
                    "status": "rejected",
                    "reasons": "unknown_geometry",
                }
            )
            continue
        if abs(longitude) > 60:
            audit.append(
                {
                    "raw_row_json": json.dumps(row, ensure_ascii=False),
                    "status": "outside_central",
                    "reasons": "abs_CMD_above60",
                }
            )
            continue
        reasons, metrics, observed, produced, word = [], {}, None, None, None
        if keys[(str(row.get("HARPNUM")), str(row.get("T_REC")))] > 1:
            reasons.append("duplicate_HARP_T_REC_rejected_all")
        if str(row.get("T_REC")) != expected_tai:
            reasons.append("unexpected_nominal_T_REC")
        try:
            if int(str(row.get("HARPNUM", ""))) <= 0:
                raise ValueError("Invalid HARP")
            observed = gateway.tai_utc(row["T_OBS"])
            produced = gateway.date_utc(row["DATE"])
            if produced < observed:
                reasons.append("production_before_observation")
        except (KeyError, ValueError, OverflowError):
            reasons.append("invalid_explicit_time_or_HARP")
        try:
            word = gateway.quality_word(row["QUALITY"])
            if word != 1024:
                reasons.append("QUALITY_not_exact_complete1024")
        except (KeyError, ValueError, TypeError):
            reasons.append("unknown_QUALITY")
        for name in ("QUAL_S", "QUALLEV1", "ARM_QUAL"):
            try:
                secondary_word = gateway.quality_word(row[name])
                if secondary_word & 0x80000000:
                    # Reject pending source-specific review. This does not borrow
                    # primary QUALITY's bit meanings into a secondary namespace.
                    reasons.append(
                        "secondary_highbit_rejected_pending_source_review_" + name
                    )
            except (KeyError, ValueError, TypeError):
                reasons.append("unknown_" + name)
        for name in METRICS:
            value = finite(row.get(name))
            if value is None or (name != "MEANALP" and value < 0):
                reasons.append("missing_or_invalid_" + name)
            metrics[name] = value
        item = {
            "HARPNUM": row.get("HARPNUM"),
            "T_REC": row.get("T_REC"),
            "T_OBS_raw": row.get("T_OBS"),
            "DATE_raw": row.get("DATE"),
            "QUALITY_raw": row.get("QUALITY"),
            "QUALITY_normalized_uint32": word,
            "observed_UTC": utc(observed),
            "support_start_UTC": utc(observed - timedelta(seconds=675))
            if observed
            else "",
            "support_end_UTC": utc(observed + timedelta(seconds=675))
            if observed
            else "",
            "produced_UTC": utc(produced),
            "status": "rejected" if reasons else "eligible",
            "reasons": "|".join(reasons),
            "quality_policy_sha256": quality_sha,
            "raw_row_json": json.dumps(row, ensure_ascii=False),
        }
        audit.append(item)
        central.append((item, observed, produced, metrics))
    reasons = []
    if unknown_geometry:
        reasons.append("unknown_geometry_patch")
    if not central:
        reasons.append("no_central_patch_is_not_zero_solar_field")
    if any(item[0]["status"] != "eligible" for item in central):
        reasons.append("partial_central_patch_coverage")
    if reasons:
        return {
            "magnetic_qualified": False,
            "reasons": reasons,
            "audit": audit,
            "candidate_central_patch_count": len(central),
            "unknown_geometry_patch_count": unknown_geometry,
            "rejected_central_patch_count": sum(
                item[0]["status"] != "eligible" for item in central
            ),
        }, audit
    observed = [item[1] for item in central]
    support_start = min(observed) - timedelta(seconds=675)
    support_end = max(observed) + timedelta(seconds=675)
    produced = max(item[2] for item in central)
    issue = max(support_end, produced) + timedelta(seconds=1)
    age = (issue - min(observed)).total_seconds()
    if age > 10800:
        return {
            "magnetic_qualified": False,
            "reasons": ["simulated_issue_observation_age_above3h"],
            "audit": audit,
            "observation_utc": utc(max(observed)),
            "simulated_issue_utc": utc(issue),
            "observation_age_seconds": age,
        }, audit
    import statistics

    features = {
        f"{name}_{aggregation}": (
            max(values) if aggregation == "max" else statistics.median(values)
        )
        for name in METRICS
        for values in [[item[3][name] for item in central]]
        for aggregation in ("max", "median")
    }
    features["patch_count"] = len(central)
    return {
        "magnetic_qualified": True,
        "reasons": [],
        "audit": audit,
        **features,
        "observation_utc": utc(max(observed)),
        "observation_first_utc": utc(min(observed)),
        "observation_spread_seconds": (max(observed) - min(observed)).total_seconds(),
        "simulated_issue_utc": utc(issue),
        "reference_utc": utc(issue),
        "input_support_start_utc": utc(support_start),
        "hmi_latest_support_end_utc": utc(support_end),
        "hmi_latest_processing_utc": utc(produced),
        "observation_age_seconds": age,
        "candidate_central_patch_count": len(central),
        "rejected_central_patch_count": 0,
        "unknown_geometry_patch_count": 0,
        "partial_patch_coverage": False,
        "all_magnetic_quality_allowed": True,
        "all_magnetic_quality_zero": False,
        "central_quality_words_json": json.dumps({"1024": len(central)}),
        "quality_policy_sha256": quality_sha,
        "historical_availability_basis": "keyword_DATE_simulation_not_first_publication",
    }, audit


def split_for_day(day):
    for role, (first, last) in SPLITS.items():
        if first <= day <= last:
            return role
    return "excluded"


def history_values(
    issue, event_index, event_audit, coverage, cutoff, catalog_start, catalog_end
):
    feature_cutoff = issue - timedelta(hours=3)
    start = feature_cutoff - timedelta(hours=168)
    stats = coverage.stats(start, feature_cutoff)
    unknown_reasons = []
    if not stats["qualified"]:
        unknown_reasons.append("history_minute_coverage_or_gap_incomplete")
    if not (catalog_start <= start and feature_cutoff <= min(cutoff, catalog_end)):
        unknown_reasons.append("history_catalog_domain_unknown")
    if relevant_bad_events(event_audit, start, feature_cutoff):
        unknown_reasons.append("history_quarantined_event")
    past_rows = event_index.window(start, feature_cutoff)
    missing_end_count = sum(event["end"] is None for event in past_rows)
    if missing_end_count:
        unknown_reasons.append("history_past_end_missing")
    known = not unknown_reasons
    values, used = {}, []
    for hours in (24, 72, 168):
        rows = event_index.window(
            feature_cutoff - timedelta(hours=hours), feature_cutoff
        )
        complete = [
            event
            for event in rows
            if event["end"] is not None
            and event["end"] <= feature_cutoff
            and event["peak_time"] <= feature_cutoff
        ]
        for kind, threshold in (("c", 1e-6), ("m", 1e-5), ("x", 1e-4)):
            values[f"history_{hours}h_{kind}_count"] = sum(
                event["peak"] >= threshold for event in complete
            )
        values[f"history_{hours}h_max_peak_w_m2"] = max(
            (event["peak"] for event in complete), default=0.0
        )
        used.extend(complete)
    return (
        {name: values[name] if known else None for name in HISTORY},
        {
            "history_baseline_status": "qualified" if known else "unknown_not_issued",
            "history7d_qualified": bool(known),
            "history_unknown_reasons": "|".join(unknown_reasons),
            "history_past168h_missing_end_count": missing_end_count,
            "history_latest_event_end_utc": utc(
                max((event["end"] for event in used), default=None)
            )
            if known
            else "",
            "history_feature_cutoff_utc": utc(feature_cutoff),
            "history_lookback_anchor": "issue_minus_3h",
            "past_168h_coverage_fraction": stats["coverage_fraction"],
            "past_168h_max_gap_seconds": stats["max_gap_seconds"],
        },
    )


def apply_purge(rows):
    """Compute boundaries from actual later input support and history cutoff."""
    checks, removed = [], []
    for earlier, later in zip(ROLES, ROLES[1:]):
        following = [
            row for row in rows if row["split"] == later and row["status"] == "eligible"
        ]
        if not following:
            checks.append(
                {
                    "earlier_role": earlier,
                    "later_role": later,
                    "status": "not_established_empty_following_role",
                }
            )
            continue
        first = min(following, key=lambda row: row["reference_utc"])
        cutoff = min(
            parse_utc(first["input_support_start_utc"]),
            parse_utc(first["history_feature_cutoff_utc"]),
        )
        for row in rows:
            if (
                row["split"] == earlier
                and row["status"] == "eligible"
                and parse_utc(row["target_end_utc"]) > cutoff
            ):
                row["status"] = "excluded"
                row["same_date_evaluation_qualified"] = False
                row["exclusion_reasons"] = "role_boundary_future_label_overlap"
                removed.append(
                    {
                        "sample_id": row["sample_id"],
                        "earlier_role": earlier,
                        "later_role": later,
                        "target_end_utc": row["target_end_utc"],
                        "following_first_sample_id": first["sample_id"],
                        "following_input_cutoff_utc": utc(cutoff),
                    }
                )
        retained = [
            parse_utc(row["target_end_utc"])
            for row in rows
            if row["split"] == earlier and row["status"] == "eligible"
        ]
        checks.append(
            {
                "earlier_role": earlier,
                "later_role": later,
                "following_first_sample_id": first["sample_id"],
                "following_input_cutoff_utc": utc(cutoff),
                "earlier_max_retained_target_end_utc": utc(max(retained, default=None)),
                "status": "passed"
                if retained and max(retained) <= cutoff
                else "not_established_empty_earlier_role",
            }
        )
    return {
        "schema_version": "available24-dynamic-purge-v1",
        "rule": "earlier_target_end<=later_first_min_support_start_and_history_cutoff",
        "checks": checks,
        "removed": removed,
        "removed_count": len(removed),
    }


def prepare(input_dir, contract_path, output_dir):
    started = time.monotonic()
    root, output = Path(input_dir).resolve(), Path(output_dir).resolve()
    if output.exists():
        raise ValueError("Never overwrite an earlier prepared output")
    if output.is_relative_to(root) or root.is_relative_to(output):
        raise ValueError("Output and read-only input trees must be separate")
    contract_path = Path(contract_path).resolve()
    contract_sha = sha(contract_path)
    contract = json.loads(contract_path.read_text(encoding="utf-8-sig"))
    check_contract(contract)
    before = {
        str(contract_path): contract_sha,
        str(Path(__file__).resolve()): sha(__file__),
    }
    quality_decision = verify_quality_decision(contract, before)
    source, gateway, history, core = load_frozen(contract, before)
    if sha(root / "source_manifest.json") != contract["source_manifest_sha256"]:
        raise ValueError("Frozen source manifest changed")
    manifest, roles, raw_before = source.checked_bundle(root)
    before.update(raw_before)
    annual = roles.get("science_event_annual", [])
    if sorted(entry["parameters"]["year"] for entry in annual) != list(
        range(2010, 2027)
    ):
        raise ValueError("Complete frozen annual2010-2026 source set required")
    meta_entries = roles.get("science_event_metadata", [])
    if len(meta_entries) != 1:
        raise ValueError("One frozen catalogue metadata source required")
    attributes = json.loads(
        safe_path(root, meta_entries[0]["path"]).read_text(encoding="utf-8-sig")
    )["global_attributes"]
    catalog_start = datetime.strptime(
        attributes["time_coverage_start"], "%Y%m%d"
    ).replace(tzinfo=UTC)
    catalog_end = datetime.strptime(attributes["time_coverage_end"], "%Y%m%d").replace(
        tzinfo=UTC
    ) + timedelta(days=1)
    cutoff = parse_utc(contract.get("historical_catalog_cutoff_utc"))
    if cutoff is None or cutoff > catalog_end:
        raise ValueError(
            "Frozen catalogue cutoff unavailable or beyond official support"
        )
    _, _, minutes, coverage_audit = source.load_history_sources(
        root, manifest, roles, cutoff, history, core
    )
    coverage = SecondsCoverage(minutes)
    raw_events = []
    for entry in annual:
        with safe_path(root, entry["path"]).open(
            encoding="utf-8-sig", newline=""
        ) as stream:
            raw_events.extend(
                {**row, "source_file": entry["path"]} for row in csv.DictReader(stream)
            )
    events, event_audit = normalize_events(raw_events)
    event_index = core.EventIndex(events)
    by_day, failed, source_entries = defaultdict(list), set(), defaultdict(list)
    for entry in roles.get("magnetic_month", []):
        if entry.get("status") != "success":
            failed.add(entry["month"])
            continue
        payload = json.loads(
            safe_path(root, entry["path"]).read_text(encoding="utf-8-sig")
        )
        decoded = gateway.decode_keywords(payload)
        for row in decoded:
            match = re.fullmatch(
                r"(\d{4}\.\d{2}\.\d{2})_13:00:00_TAI", str(row.get("T_REC"))
            )
            if match is None:
                failed.add(entry["month"])
                continue
            day = match.group(1).replace(".", "-")
            by_day[day].append(row)
            if entry not in source_entries[day]:
                source_entries[day].append(entry)
    rows, labels, patches, terminal_excluded = [], [], [], []
    first, last = datetime(2011, 1, 1, tzinfo=UTC), datetime(2025, 12, 31, tzinfo=UTC)
    for number in range((last - first).days + 1):
        day = (first + timedelta(days=number)).strftime("%Y-%m-%d")
        nominal = day.replace("-", ".") + "_13:00:00_TAI"
        group, patch = qualify_group(
            by_day[day], gateway, nominal, contract["quality_policy_sha256"]
        )
        patches.extend({"observation_date": day, **item} for item in patch)
        base = {
            "observation_date": day,
            "hmi_nominal_tai": nominal,
            "source_mode": SOURCE_MODE,
            "split": split_for_day(day),
            "status": "excluded",
            "exclusion_reasons": "|".join(group["reasons"]),
            "reference_utc": "",
            "simulated_issue_utc": "",
            "target_start_utc": "",
            "target_end_utc": "",
            "observation_utc": "",
            "input_support_start_utc": "",
            "hmi_latest_support_end_utc": "",
            "hmi_latest_processing_utc": "",
            "history_baseline_status": "unknown_not_issued",
            "magnetic_qualified": False,
            "label_qualified": False,
            "same_date_evaluation_qualified": False,
            "label_c1": None,
            "label_m1": None,
            "y_class": None,
            **{name: None for name in FEATURES + HISTORY},
            **{
                key: value
                for key, value in group.items()
                if key not in {"reasons", "audit"}
            },
            "source_files_json": json.dumps(
                [
                    {
                        key: entry.get(key)
                        for key in (
                            "path",
                            "sha256",
                            "retrieved_utc",
                            "actual_AI_call_id",
                        )
                    }
                    for entry in source_entries[day]
                ],
                ensure_ascii=False,
            ),
        }
        if day[:7] in failed:
            base["exclusion_reasons"] = "|".join(
                filter(
                    None,
                    [base["exclusion_reasons"], "unresolved_magnetic_source_month"],
                )
            )
            base["magnetic_qualified"] = False
        identity = SOURCE_MODE + "|" + nominal + "|" + base["simulated_issue_utc"]
        base["sample_id"] = (
            "available24_" + hashlib.sha256(identity.encode()).hexdigest()[:24]
        )
        if base["magnetic_qualified"]:
            issue = parse_utc(base["simulated_issue_utc"])
            end = issue + timedelta(hours=24)
            base.update(target_start_utc=utc(issue), target_end_utc=utc(end))
            if base["split"] == "test" and end > parse_utc(
                contract["test_target_end_max_utc"]
            ):
                base["exclusion_reasons"] = "test_target_crosses2026_reserved_for_live"
                base["terminal_target_bound_qualified"] = False
                base["excluded_test_future_truth_not_consumed"] = True
                receipt = {
                    "sample_id": base["sample_id"],
                    "observation_date": day,
                    "target_start_utc": utc(issue),
                    "target_end_utc": utc(end),
                    "test_target_end_max_utc": contract["test_target_end_max_utc"],
                    "reason": base["exclusion_reasons"],
                    "window_labels_and_coverage_not_evaluated": True,
                }
                terminal_excluded.append(receipt)
                labels.append({**receipt, "label_qualified": False})
                rows.append(base)
                continue
            base["terminal_target_bound_qualified"] = True
            stats = coverage.stats(issue, end)
            certificate = label_certificate(
                issue,
                end,
                event_index,
                event_audit,
                cutoff,
                catalog_start,
                catalog_end,
                stats,
            )
            values, hist = history_values(
                issue,
                event_index,
                event_audit,
                coverage,
                cutoff,
                catalog_start,
                catalog_end,
            )
            base.update(
                target_start_utc=utc(issue),
                target_end_utc=utc(end),
                **values,
                **hist,
                label_c1=certificate["label_c1"],
                label_m1=certificate["label_m1"],
                y_class=certificate["y_class"],
                label_qualified=certificate["label_qualified"],
                future_coverage_fraction=stats["coverage_fraction"],
                future_valid_seconds=stats["valid_seconds"],
                future_window_seconds=stats["window_seconds"],
                future_max_gap_seconds=stats["max_gap_seconds"],
                feature_max_observed_utc=base["hmi_latest_support_end_utc"],
            )
            if certificate["label_qualified"]:
                base.update(
                    status="eligible",
                    exclusion_reasons="",
                    same_date_evaluation_qualified=True,
                )
            else:
                base["exclusion_reasons"] = "future_label_or_coverage_unknown"
            labels.append(
                {
                    "sample_id": base["sample_id"],
                    "observation_date": day,
                    "target_start_utc": utc(issue),
                    "target_end_utc": utc(end),
                    **stats,
                    **{
                        key: value
                        for key, value in certificate.items()
                        if not isinstance(value, (dict, list))
                    },
                    "known_positive_bit_certificates_json": json.dumps(
                        certificate["known_positive_bit_certificates"],
                        ensure_ascii=False,
                    ),
                    "window_events_json": json.dumps(
                        certificate["window_events"], ensure_ascii=False
                    ),
                    "quarantined_events_json": json.dumps(
                        certificate["quarantined_events"], ensure_ascii=False
                    ),
                    "catalog_cutoff_utc": utc(cutoff),
                    "catalog_end_utc": utc(catalog_end),
                }
            )
        else:
            labels.append(
                {
                    "sample_id": base["sample_id"],
                    "observation_date": day,
                    "label_qualified": False,
                    "reason": "magnetic_or_clock_unknown_no_target_created",
                }
            )
        rows.append(base)
    purge = apply_purge(rows)
    physical = [row for row in rows if row["status"] == "eligible"]
    common = [row for row in physical if row["history_baseline_status"] == "qualified"]
    if len({row["sample_id"] for row in rows}) != len(rows):
        raise ValueError("Stable sample identity is not unique")
    after = {path: sha(path) for path in before}
    if before != after:
        raise ValueError(
            "Read-only raw input, contract or code changed during preparation"
        )

    def counts(queue):
        return {
            role: {
                "rows": sum(row["split"] == role for row in queue),
                "class_counts": dict(
                    Counter(
                        str(row["y_class"]) for row in queue if row["split"] == role
                    )
                ),
            }
            for role in ROLES
        }

    summary = {
        "schema_version": "available24-data-summary-v1",
        "status": "completed_preparation_requires_native_Data_review",
        "calendar_rows": len(rows),
        "physical_rows": len(physical),
        "common_rows": len(common),
        "physical_role_counts": counts(physical),
        "common_role_counts": counts(common),
        "history_unknown_reason_counts_in_primary": dict(
            Counter(
                reason
                for row in physical
                for reason in row.get("history_unknown_reasons", "").split("|")
                if reason
            )
        ),
        "common_sparsity": {
            "history_missing_end_stays_unknown": True,
            "primary_eligibility_does_not_require_history": True,
            "unknown_history_12_columns_not_zero_filled": True,
            "common_rows": len(common),
            "physical_rows": len(physical),
            "common_fraction_of_primary": len(common) / len(physical)
            if physical
            else None,
            "common_role_row_threshold_met": all(
                value["rows"] >= contract.get("minimum_rows_per_role", 30)
                for value in counts(common).values()
            ),
            "baseline_failure_does_not_block_primary": True,
            "insufficient_common_baseline_status": "unknown_not_fit",
            "row_threshold_only_not_class_qualification_or_native_acceptance": True,
        },
        "exclusion_reason_counts": dict(
            Counter(
                reason
                for row in rows
                for reason in row["exclusion_reasons"].split("|")
                if reason
            )
        ),
        "future_end_missing_rule": "known_start_peak_peak_time_suffice_end_only_gates_past_completion",
        "historical_clock_policy": CLOCK_POLICY,
        "purge": purge,
        "terminal_test_exclusion_count": len(terminal_excluded),
        "test_target_end_max_utc": contract["test_target_end_max_utc"],
        "source_event_rows": len(raw_events),
        "normalized_event_rows": len(events),
        "event_audit_reason_counts": dict(
            Counter(row["reasons"] for row in event_audit)
        ),
        "source_manifest_sha256": contract["source_manifest_sha256"],
        "contract_sha256": contract_sha,
        "limits": LIMITS,
        "wall_seconds": time.monotonic() - started,
        "source_versions_terms": manifest.get("source_versions_terms", {}),
        "quality_source_decision_scope": quality_decision["scope"],
        "quality_source_decision_limits": quality_decision.get("required_limits", []),
        "no_fit_predict_plot_or_issue": True,
        "native_JW_origin_requires_external_call_receipt": True,
    }
    output.mkdir(parents=True)
    required = [
        "sample_id",
        "split",
        "reference_utc",
        "target_start_utc",
        "target_end_utc",
        "status",
        "label_c1",
        "label_m1",
        "y_class",
        *FEATURES,
        *HISTORY,
    ]
    for name, data in (
        ("daily_physical.csv", physical),
        ("daily_common.csv", common),
        ("calendar_audit.csv", rows),
    ):
        write_csv(output / name, data, required)
    write_csv(
        output / "label_audit.csv",
        labels,
        ["sample_id", "observation_date", "label_qualified"],
    )
    write_csv(
        output / "patch_audit.csv", patches, ["observation_date", "status", "reasons"]
    )
    write_csv(
        output / "event_audit.csv",
        event_audit,
        ["row_ordinal", "flare_id", "source_file", "reasons"],
    )
    write_json(output / "purge_receipt.json", purge)
    write_json(
        output / "terminal_test_gate_receipt.json",
        {
            "schema_version": "available24-terminal-test-gate-v1",
            "test_target_end_max_utc": contract["test_target_end_max_utc"],
            "excluded": terminal_excluded,
            "excluded_count": len(terminal_excluded),
            "window_labels_and_coverage_not_evaluated_for_excluded_targets": True,
        },
    )
    write_json(output / "coverage_audit.json", coverage_audit)
    write_json(
        output / "input_hashes_readonly.json",
        {"before": before, "after": after, "all_inputs_hashes_unchanged": True},
    )
    validation = {
        "status": "passed",
        "all_inputs_hashes_unchanged": True,
        "raw_inputs_unchanged": True,
        "interface_only_not_scientific_acceptance": True,
        "main_feature_count": 23,
        "baseline_feature_count": 12,
        "main_does_not_require_history_qualification": True,
        "future_end_optional_peak_maturity_required": True,
        "actual_seconds_coverage_not_rounded_target": True,
        "source_mode": SOURCE_MODE,
        "historical_clock_policy": CLOCK_POLICY,
        "test_or2026_in_fit": False,
    }
    write_json(output / "validation.json", validation)
    write_json(output / "summary.json", summary)
    final_after = {path: sha(path) for path in before}
    if final_after != before:
        raise ValueError("Read-only inputs changed while writing preparation products")
    write_json(
        output / "input_hashes_readonly.json",
        {"before": before, "after": final_after, "all_inputs_hashes_unchanged": True},
    )
    products = [
        {"path": path.name, "sha256": sha(path), "bytes": path.stat().st_size}
        for path in sorted(output.iterdir())
        if path.is_file()
    ]
    prepared = {
        "schema_version": "available24-prepared-manifest-v1",
        "files": products,
        "contract_sha256": contract_sha,
        "source_manifest_sha256": contract["source_manifest_sha256"],
        "features_physical": FEATURES,
        "features_history_baseline": HISTORY,
        "source_mode": SOURCE_MODE,
        "physical_role_counts": counts(physical),
        "common_role_counts": counts(common),
        "validation": validation,
        "input_hashes": before,
        "clock_policy": CLOCK_POLICY,
        "quality_policy_sha256": contract["quality_policy_sha256"],
        "limits": LIMITS,
        "source_versions_terms": manifest.get("source_versions_terms", {}),
        "scientific_status": "not_reviewed",
        "formal_ResearchReviewStore_chain": False,
        "native_Data_acceptance_required_before_fit": True,
    }
    write_json(output / "prepared_manifest.json", prepared)
    return {
        "status": "completed_preparation_requires_native_Data_review",
        "output_directory": str(output),
        "physical_rows": len(physical),
        "common_rows": len(common),
        "physical_role_counts": counts(physical),
        "common_role_counts": counts(common),
        "prepared_manifest_sha256": sha(output / "prepared_manifest.json"),
        "summary_sha256": sha(output / "summary.json"),
        "raw_inputs_unchanged": True,
        "wall_seconds": time.monotonic() - started,
    }


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("prepare")
    command.add_argument("--input-dir", type=Path, required=True)
    command.add_argument("--contract", type=Path, required=True)
    command.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            prepare(args.input_dir, args.contract, args.output_dir),
            ensure_ascii=False,
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
