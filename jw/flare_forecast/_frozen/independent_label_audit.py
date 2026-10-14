"""Read-only independent label and coverage audit; never prepare, fit or issue.

Event decisions and second-resolution coverage integration below do not import
available24_data or any prior label function. Frozen source readers are reused
only to decode original GOES coverage files, with their bytes verified.
"""

from __future__ import annotations

import argparse
from bisect import bisect_left
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path

import numpy as np

UTC = timezone.utc


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    Path(path).write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def timestamp(value, *, naive_catalog=False):
    if value is None or str(value).strip() in {"", "MISSING", "nan", "NaT"}:
        return None
    try:
        result = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        if not naive_catalog:
            return None
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def path_below(root, relative):
    path = (Path(root) / relative).resolve()
    if (
        Path(relative).is_absolute()
        or ".." in Path(relative).parts
        or not path.is_relative_to(Path(root).resolve())
    ):
        raise ValueError("Source identity escapes its frozen input root")
    return path


def read_events(source_files):
    valid, bad, seen = [], [], set()
    for relative, path in source_files:
        with Path(path).open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                start = timestamp(row.get("start_time"), naive_catalog=True)
                peak_time = timestamp(row.get("time"), naive_catalog=True)
                end = timestamp(row.get("end_time"), naive_catalog=True)
                try:
                    peak = float(row.get("xrsb_irrad", ""))
                except (TypeError, ValueError):
                    peak = float("nan")
                key = (
                    str(row.get("flare_id", "")),
                    str(row.get("start_time", "")),
                    str(row.get("time", "")),
                )
                invalid = (
                    start is None
                    or peak_time is None
                    or not math.isfinite(peak)
                    or peak <= 0
                )
                invalid = invalid or (
                    start is not None and peak_time is not None and peak_time < start
                )
                invalid = invalid or (
                    end is not None
                    and (
                        (start is not None and end < start)
                        or (peak_time is not None and end < peak_time)
                    )
                )
                invalid = invalid or key in seen
                seen.add(key)
                record = {
                    "start": start,
                    "peak_time": peak_time,
                    "end": end,
                    "peak": peak,
                    "id": key[0],
                    "source_file": relative,
                    "saturated": str(row.get("peak_saturated", "0"))
                    not in {"0", "0.0", ""},
                }
                (bad if invalid else valid).append(record)
    return sorted(valid, key=lambda event: event["start"]), bad


class IndependentCoverage:
    """Union of original valid-minute intervals; exact fractional endpoints."""

    def __init__(self, valid, first_minute):
        self.valid = np.asarray(valid, dtype=bool)
        self.first_minute = first_minute
        self.first_second = first_minute * 60
        self.last_second = (first_minute + len(valid)) * 60
        self.prefix = np.concatenate(([0], np.cumsum(self.valid, dtype=np.int64)))
        delta = np.diff(np.concatenate(([False], ~self.valid, [False])).astype(np.int8))
        self.gap_start = np.flatnonzero(delta == 1)
        self.gap_end = np.flatnonzero(delta == -1)

    def stats(self, start, end):
        a, b = start.timestamp(), end.timestamp()
        if b <= a:
            raise ValueError("Ordered target interval required")

        def integral(value):
            value = min(max(value - self.first_second, 0), len(self.valid) * 60)
            index = int(value // 60)
            remainder = value - index * 60
            return float(self.prefix[index]) * 60 + (
                remainder * int(self.valid[index])
                if remainder and index < len(self.valid)
                else 0
            )

        valid_seconds = integral(b) - integral(a)
        intervals = []
        if a < self.first_second:
            intervals.append((a, min(b, self.first_second)))
        if b > self.last_second:
            intervals.append((max(a, self.last_second), b))
        lo, hi = max(a, self.first_second), min(b, self.last_second)
        if hi > lo:
            first = int(
                np.searchsorted(
                    self.gap_end, (lo - self.first_second) / 60, side="right"
                )
            )
            last = int(
                np.searchsorted(
                    self.gap_start, (hi - self.first_second) / 60, side="left"
                )
            )
            intervals += [
                (
                    max(lo, self.first_second + int(x) * 60),
                    min(hi, self.first_second + int(y) * 60),
                )
                for x, y in zip(self.gap_start[first:last], self.gap_end[first:last])
            ]
        merged = []
        for left, right in sorted(intervals):
            if right <= left:
                continue
            if merged and left <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(right, merged[-1][1]))
            else:
                merged.append((left, right))
        gap = max((right - left for left, right in merged), default=0.0)
        fraction = valid_seconds / (b - a)
        return {
            "valid_seconds": valid_seconds,
            "window_seconds": b - a,
            "coverage_fraction": fraction,
            "max_gap_seconds": gap,
            "qualified": fraction >= 0.99 and gap <= 300,
        }


def audit_window(start, end, events, bad, cutoff, catalog_start, catalog_end, coverage):
    starts = [event["start"] for event in events]
    rows = events[bisect_left(starts, start) : bisect_left(starts, end)]
    mature = [event for event in rows if event["peak_time"] <= cutoff]
    unknown_events = any(event["peak_time"] > cutoff for event in rows) or any(
        event["start"] is None or start <= event["start"] < end for event in bad
    )
    domain = (
        coverage["qualified"]
        and catalog_start <= start
        and end <= min(catalog_end, cutoff)
    )
    bits, supporters = [], []
    for threshold in (1e-6, 1e-5):
        found = [event for event in mature if event["peak"] >= threshold]
        unresolved = unknown_events or any(
            event["saturated"] and event["peak"] < threshold for event in mature
        )
        bits.append(1 if found else None if unresolved or not domain else 0)
        supporters.append(
            [
                {
                    "flare_id": event["id"],
                    "source_file": event["source_file"],
                    "start_utc": event["start"].isoformat(),
                    "peak_w_m2": event["peak"],
                }
                for event in found
            ]
        )
    qualified = domain and all(bit is not None for bit in bits)
    return {
        "label_c1": bits[0],
        "label_m1": bits[1],
        "label_qualified": qualified,
        "y_class": (2 if bits[1] else bits[0]) if qualified else None,
        "event_count": len(rows),
        "supporters": supporters,
        "unknown_event_condition": unknown_events,
        "catalog_domain_qualified": domain,
    }


def load_coverage(root, files, contract, readonly):
    core_path = (
        Path(__file__).with_name("frozen_shared") / "historical_prepare_reference.py"
    )
    expected = contract["frozen_helpers"][core_path.name]
    if sha(core_path) != expected:
        raise ValueError("Frozen coverage-reader bytes differ")
    readonly[str(core_path.resolve())] = expected
    spec = importlib.util.spec_from_file_location(
        "available24_independent_coverage_decoder", core_path
    )
    core = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(core)
    first = int(datetime(2010, 12, 1, tzinfo=UTC).timestamp()) // 60
    cutoff = timestamp(contract["historical_catalog_cutoff_utc"])
    last = int(cutoff.timestamp()) // 60
    flags = np.zeros(last - first, dtype=bool)
    sources = []
    for item in files:
        role = item["role"]
        if role not in {
            "reused_legacy_coverage",
            "reused_science_coverage",
            "new_annual_science_coverage",
            "finalweek_fallback_science",
        }:
            continue
        path = path_below(root, item["path"])
        if sha(path) != item["sha256"] or path.stat().st_size != item["bytes"]:
            raise ValueError("Original coverage bytes differ")
        readonly[str(path)] = item["sha256"]
        if role == "reused_legacy_coverage":
            minutes = core.coverage_valid_minutes(path)
        else:
            minutes, _ = core.science_coverage_valid_minutes(path)
            if role == "reused_science_coverage":
                minutes = core.science_crop_minutes(
                    minutes,
                    int(item["parameters"]["satellite"]),
                    int(item["parameters"]["year"]),
                )
            if role == "finalweek_fallback_science":
                lower = int((cutoff - timedelta(hours=168)).timestamp()) // 60
                minutes = {minute for minute in minutes if lower <= minute < last}
        offsets = np.fromiter(
            (minute - first for minute in minutes if first <= minute < last),
            dtype=np.int64,
        )
        flags[offsets] = True
        sources.append(
            {
                "path": str(path),
                "role": role,
                "sha256": item["sha256"],
                "bytes": item["bytes"],
                "valid_minutes_selected": len(minutes),
            }
        )
    return IndependentCoverage(flags, first), sources


def audit(raw_dir, prepared_dir, contract_path, output_dir, gate_path):
    raw, prepared, output = map(
        lambda value: Path(value).resolve(), (raw_dir, prepared_dir, output_dir)
    )
    if output.exists() or output.is_relative_to(raw) or output.is_relative_to(prepared):
        raise ValueError("Independent audit must create a fresh separate directory")
    contract_path = Path(contract_path).resolve()
    contract = json.loads(contract_path.read_text(encoding="utf-8-sig"))
    manifest_path = raw / "source_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if sha(manifest_path) != contract["source_manifest_sha256"]:
        raise ValueError("Frozen original source manifest differs")
    prepared_manifest_path = prepared / "prepared_manifest.json"
    prepared_manifest = json.loads(
        prepared_manifest_path.read_text(encoding="utf-8-sig")
    )
    if prepared_manifest["contract_sha256"] != sha(contract_path):
        raise ValueError("Preparation uses a different contract")
    readonly = {
        str(contract_path): sha(contract_path),
        str(manifest_path): sha(manifest_path),
        str(prepared_manifest_path): sha(prepared_manifest_path),
        str(Path(__file__).resolve()): sha(__file__),
    }
    for record in prepared_manifest["files"]:
        path = path_below(prepared, record["path"])
        if sha(path) != record["sha256"] or path.stat().st_size != record["bytes"]:
            raise ValueError("Original preparation result bytes differ")
        readonly[str(path)] = record["sha256"]
    annual = []
    annual_index = []
    metadata = None
    for item in manifest["files"]:
        if item["role"] not in {"science_event_annual", "science_event_metadata"}:
            continue
        path = path_below(raw, item["path"])
        if sha(path) != item["sha256"] or path.stat().st_size != item["bytes"]:
            raise ValueError("Original official event/metadata bytes differ")
        readonly[str(path)] = item["sha256"]
        if item["role"] == "science_event_annual":
            annual.append((item["path"], path))
            annual_index.append(
                {
                    "path": str(path),
                    "relative": item["path"],
                    "sha256": item["sha256"],
                    "year": item["parameters"]["year"],
                }
            )
        else:
            metadata = json.loads(path.read_text(encoding="utf-8-sig"))[
                "global_attributes"
            ]
    if len(annual) != 17 or metadata is None:
        raise ValueError("Frozen complete official catalogue identity required")
    events, bad = read_events(annual)
    coverage, coverage_sources = load_coverage(
        raw, manifest["files"], contract, readonly
    )
    cutoff = timestamp(contract["historical_catalog_cutoff_utc"])
    catalog_start = datetime.strptime(
        metadata["time_coverage_start"], "%Y%m%d"
    ).replace(tzinfo=UTC)
    catalog_end = datetime.strptime(metadata["time_coverage_end"], "%Y%m%d").replace(
        tzinfo=UTC
    ) + timedelta(days=1)
    output.mkdir(parents=True)
    evidence = output / "independent_source_index.json"
    write(
        evidence,
        {
            "catalogue_files": annual_index,
            "coverage_files": coverage_sources,
            "catalog_start": catalog_start.isoformat(),
            "catalog_end": catalog_end.isoformat(),
            "cutoff": cutoff.isoformat(),
            "scope": "Frozen coverage source decoders reused; event rules and seconds coverage independently reimplemented; no available24_data import/prepare or fitting.",
        },
    )
    source_digest = sha(evidence)
    with (prepared / "label_audit.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        labels = list(csv.DictReader(handle))
    with (prepared / "daily_physical.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        physical = list(csv.DictReader(handle))
    checked, mismatches, negative_bits, audited_ids = 0, [], 0, set()
    result_rows = []
    for original in labels:
        identity = original["sample_id"]
        start, end = (
            timestamp(original.get("target_start_utc")),
            timestamp(original.get("target_end_utc")),
        )
        notes = []
        result = {
            "sample_id": identity,
            "observation_date": original.get("observation_date", ""),
            "target_start_utc": original.get("target_start_utc", ""),
            "target_end_utc": original.get("target_end_utc", ""),
            "source_index_path": str(evidence),
            "source_index_sha256": source_digest,
        }
        if (
            start is None
            or end is None
            or end > timestamp(contract["test_target_end_max_utc"])
        ):
            if original.get("label_qualified") == "True":
                mismatches.append(
                    {
                        "sample_id": identity,
                        "notes": [
                            "qualified label has missing or unregistered target interval"
                        ],
                    }
                )
            result.update(
                status="not_evaluated_unknown_input_or_terminal_target", mismatch=False
            )
            result_rows.append(result)
            continue
        stats = coverage.stats(start, end)
        expected = audit_window(
            start, end, events, bad, cutoff, catalog_start, catalog_end, stats
        )
        for field in ("label_c1", "label_m1", "y_class"):
            raw_value = original.get(field, "")
            try:
                value = None if raw_value == "" else int(raw_value)
            except ValueError:
                value = "invalid"
            if value != expected[field]:
                notes.append(field + " mismatch")
            result["recomputed_" + field] = expected[field]
        if (original.get("label_qualified") == "True") != expected["label_qualified"]:
            notes.append("label_qualified mismatch")
        for field in (
            "valid_seconds",
            "window_seconds",
            "coverage_fraction",
            "max_gap_seconds",
        ):
            try:
                recorded = float(original[field])
            except (KeyError, ValueError):
                recorded = float("nan")
            if not math.isfinite(recorded) or not math.isclose(
                recorded, stats[field], rel_tol=0, abs_tol=1e-6
            ):
                notes.append(field + " mismatch")
            result["recomputed_" + field] = stats[field]
        if (end - start).total_seconds() != 86400:
            notes.append("window is not exact24h")
        qualified = expected["label_qualified"]
        if qualified:
            checked += 1
            audited_ids.add(identity)
            negative_bits += sum(expected[key] == 0 for key in ("label_c1", "label_m1"))
        result.update(
            status="matched" if not notes else "mismatch",
            mismatch=bool(notes),
            notes="|".join(notes),
            recomputed_label_qualified=qualified,
            event_count=expected["event_count"],
            negative_C_evidence_complete=qualified and expected["label_c1"] == 0,
            negative_M_evidence_complete=qualified and expected["label_m1"] == 0,
            supporting_events_json=json.dumps(
                expected["supporters"], ensure_ascii=False
            ),
        )
        result_rows.append(result)
        if notes:
            mismatches.append({"sample_id": identity, "notes": notes})
    physical_ids = {row["sample_id"] for row in physical}
    missing = sorted(physical_ids - audited_ids)
    if missing:
        mismatches += [
            {
                "sample_id": identity,
                "notes": ["qualified primary row lacks independent qualified label"],
            }
            for identity in missing
        ]
    if len({row["sample_id"] for row in labels}) != len(labels):
        mismatches.append(
            {
                "sample_id": "duplicate",
                "notes": ["label audit identities are not unique"],
            }
        )
    unchanged = all(sha(path) == expected for path, expected in readonly.items())
    if not unchanged:
        mismatches.append(
            {
                "sample_id": "integrity",
                "notes": ["read-only source or prepared bytes changed"],
            }
        )
    table = output / "independent_label_audit.csv"
    columns = list(dict.fromkeys(key for record in result_rows for key in record))
    with table.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(result_rows)
    receipt = {
        "schema_version": "available24-independent-label-gate-v1",
        "status": "passed"
        if checked > 0 and physical_ids and not mismatches
        else "failed",
        "contract_sha256": sha(contract_path),
        "prepared_manifest_sha256": sha(prepared_manifest_path),
        "sample_mismatch_count": len(mismatches),
        "checked_samples": checked,
        "primary_samples_confirmed": len(physical_ids) - len(missing),
        "label_audit_rows": len(labels),
        "negative_bits_with_catalogue_and_seconds_coverage_evidence": negative_bits,
        "catalogue_source_files": len(annual),
        "coverage_source_files": len(coverage_sources),
        "all_inputs_hashes_unchanged": unchanged,
        "mismatches": mismatches,
        "artifacts": [
            {"path": str(path), "sha256": sha(path), "bytes": path.stat().st_size}
            for path in (table, evidence)
        ],
        "readonly_inputs": readonly,
        "source_reader_reuse": "Coverage decoding only; neither prepared label function nor frozen old label function is called",
        "operator_validation_not_JW_scientific_verdict": True,
        "no_fit_predict_plot_or_issue": True,
    }
    write(output / "independent_label_receipt.json", receipt)
    gate = Path(gate_path).resolve()
    if gate.exists():
        raise ValueError("Never replace an earlier independent label gate")
    write(gate, receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser()
    for name in ("raw-dir", "prepared-dir", "contract", "output-dir", "gate"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    receipt = audit(
        args.raw_dir, args.prepared_dir, args.contract, args.output_dir, args.gate
    )
    print(
        json.dumps(
            {
                key: receipt[key]
                for key in (
                    "status",
                    "checked_samples",
                    "sample_mismatch_count",
                    "negative_bits_with_catalogue_and_seconds_coverage_evidence",
                )
            },
            ensure_ascii=False,
        )
    )
    raise SystemExit(0 if receipt["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
