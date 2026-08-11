"""Summarize real-WebUI Research Review evaluation artifacts.

The summary reports only observable engineering evidence. Scientific recall,
false release, and blind scores remain unset until an independent adjudicator
labels the saved answers and verdicts.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _signature(metadata: dict[str, Any], status: dict[str, Any]) -> str:
    return json.dumps(
        {
            "scientific_status": metadata.get("scientific_status"),
            "terminal": status.get("terminal"),
            "stage_verdicts": metadata.get("stage_verdicts", []),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


_PROVIDER_400 = re.compile(
    r'(?:Error code:\s*400|HTTP/(?:1\.1|2)\s+400|"HTTP/1\.1 400)', re.IGNORECASE
)


def _backend_log_has_400(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return any(_PROVIDER_400.search(line) for line in handle)
    except OSError:
        return False


def _assessment_contract_satisfied(row: dict[str, Any]) -> bool:
    """Require exactly one persisted assessment for every review round.

    A formal run with review enabled must enter at least one review round.  This
    prevents a completed UI answer that silently skipped Evidence from being
    counted as assessment-covered, while still allowing non-review diagnostic
    records to report zero rounds and zero assessments.
    """

    rounds = int(row.get("evidence_review_invocations", 0))
    assessments = int(row.get("assessment_count", 0))
    if bool(row.get("review_active")) and rounds == 0:
        return False
    return assessments == rounds


def summarize(root: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for directory in sorted(root.glob("formal.*")):
        if not directory.is_dir():
            continue
        metadata = _read_json(directory / "metadata.json", None)
        if not isinstance(metadata, dict):
            failure = _read_json(directory / "harness_failure.json", None)
            if not isinstance(failure, dict):
                continue
            parts = directory.name.split(".")
            metadata = {
                "case_id": parts[2] if len(parts) >= 4 else "unknown",
                "run_label": directory.name,
                "outcome": "harness_error",
                "reviewer": {"review_mode": parts[1] if len(parts) >= 4 else "unknown"},
                "latency_seconds": 0,
                "observed_usage": {"total_tokens": 0},
                "error_signals": {},
                "harness_failure": failure,
            }
        status = _read_json(directory / "review_status.json", {})
        records.append(
            {
                **metadata,
                "directory": directory.name,
                "signature": _signature(metadata, status),
            }
        )

    by_mode: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        by_mode[str(row.get("reviewer", {}).get("review_mode", "unknown"))].append(row)

    mode_metrics: dict[str, Any] = {}
    for mode, rows in sorted(by_mode.items()):
        latencies = [float(row.get("latency_seconds", 0)) for row in rows]
        token_totals = [
            float(row.get("observed_usage", {}).get("total_tokens", 0)) for row in rows
        ]
        mode_metrics[mode] = {
            "runs": len(rows),
            "p95_latency_seconds": _p95(latencies),
            "mean_observed_tokens": sum(token_totals) / len(token_totals),
        }

    grouped_cases: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        grouped_cases[
            (
                str(row.get("case_id", "unknown")),
                str(row.get("reviewer", {}).get("review_mode", "unknown")),
            )
        ].append(row)
    case_table = [
        {
            "case_id": case_id,
            "review_mode": mode,
            "runs": len(rows),
            "expected_outcomes": sorted(
                {
                    str(row["expected_outcome"])
                    for row in rows
                    if row.get("expected_outcome") is not None
                }
            ),
            "observed_outcomes": dict(
                sorted(
                    {
                        outcome: sum(1 for row in rows if row.get("outcome") == outcome)
                        for outcome in {
                            str(row.get("outcome", "unknown")) for row in rows
                        }
                    }.items()
                )
            ),
            "reviewer_families": sorted(
                {str(row.get("reviewer", {}).get("family", "unknown")) for row in rows}
            ),
            "assessment_coverage": all(
                _assessment_contract_satisfied(row)
                for row in rows
            ),
            "provider_or_runtime_400": sum(
                bool(row.get("error_signals", {}).get("provider_or_runtime_400"))
                for row in rows
            ),
            "illegal_routes": sum(
                bool(row.get("error_signals", {}).get("illegal_route")) for row in rows
            ),
            "p95_latency_seconds": _p95(
                [float(row.get("latency_seconds", 0)) for row in rows]
            ),
            "mean_observed_tokens": sum(
                float(row.get("observed_usage", {}).get("total_tokens", 0))
                for row in rows
            )
            / len(rows),
        }
        for (case_id, mode), rows in sorted(grouped_cases.items())
    ]

    core_two_pass: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in by_mode.get("two_pass", []):
        case_id = str(row.get("case_id", ""))
        if case_id.startswith("SC26-"):
            core_two_pass[case_id].append(row)
    stability = {
        case_id: {
            "runs": len(rows),
            "stable": len(rows) == 3
            and len({str(row.get("signature")) for row in rows}) == 1,
        }
        for case_id, rows in sorted(core_two_pass.items())
    }

    serial_closed = [
        row
        for row in by_mode.get("closed", [])
        if row.get("case_id", "").startswith("SC26-")
    ]
    serial_two_pass = [
        row
        for row in by_mode.get("two_pass", [])
        if row.get("case_id", "").startswith("SC26-")
        and str(row.get("run_label", "")).endswith(".r1")
    ]
    closed_p95 = _p95([float(row.get("latency_seconds", 0)) for row in serial_closed])
    two_pass_p95 = _p95(
        [float(row.get("latency_seconds", 0)) for row in serial_two_pass]
    )
    closed_tokens = sum(
        float(row.get("observed_usage", {}).get("total_tokens", 0))
        for row in serial_closed
    ) / max(len(serial_closed), 1)
    two_pass_tokens = sum(
        float(row.get("observed_usage", {}).get("total_tokens", 0))
        for row in serial_two_pass
    ) / max(len(serial_two_pass), 1)

    errors_400 = [
        row["directory"]
        for row in records
        if row.get("error_signals", {}).get("provider_or_runtime_400")
    ]
    errors_400.extend(
        f"backend:{path.name}"
        for path in sorted(root.glob("backend.formal.*.log"))
        if _backend_log_has_400(path)
    )
    illegal_routes = [
        row["directory"]
        for row in records
        if row.get("error_signals", {}).get("illegal_route")
    ]
    missing_assessments = [
        row["directory"]
        for row in records
        if not _assessment_contract_satisfied(row)
    ]
    failed_runs = [
        row["directory"]
        for row in records
        if row.get("outcome") not in {"completed_with_answer", "planning_frozen"}
    ]
    failure_index = []
    for row in records:
        reasons = []
        if row.get("outcome") not in {"completed_with_answer", "planning_frozen"}:
            reasons.append(str(row.get("outcome", "unknown")))
        if row.get("error_signals", {}).get("provider_or_runtime_400"):
            reasons.append("provider_or_runtime_400")
        if row.get("error_signals", {}).get("illegal_route"):
            reasons.append("illegal_route")
        if not _assessment_contract_satisfied(row):
            reasons.append("assessment_contract")
        if reasons:
            failure_index.append(
                {
                    "directory": row["directory"],
                    "case_id": row.get("case_id"),
                    "review_mode": row.get("reviewer", {}).get("review_mode"),
                    "reasons": reasons,
                    "error_summary": row.get("error_summary")
                    or row.get("harness_failure", {}).get("message"),
                }
            )
    expected_counts = {"closed": 6, "two_pass": 30}
    count_gate = all(
        len(by_mode.get(mode, [])) == expected
        for mode, expected in expected_counts.items()
    )
    stability_gate = len(stability) == 6 and all(
        item["stable"] for item in stability.values()
    )
    latency_ratio = (
        two_pass_p95 / closed_p95 if closed_p95 and two_pass_p95 is not None else None
    )
    token_ratio = two_pass_tokens / closed_tokens if closed_tokens else None

    return {
        "schema_version": "webui-eval-summary-v1",
        "runs": len(records),
        "expected_runs": 36,
        "case_table": case_table,
        "mode_metrics": mode_metrics,
        "core_stability": stability,
        "serial_core_comparison": {
            "closed_runs": len(serial_closed),
            "two_pass_runs": len(serial_two_pass),
            "p95_latency_ratio": latency_ratio,
            "mean_observed_token_ratio": token_ratio,
        },
        "engineering_gates": {
            "run_count": count_gate,
            "provider_or_runtime_400_zero": not errors_400,
            "illegal_route_zero": not illegal_routes,
            "assessment_coverage": not missing_assessments,
            "core_signature_stability": stability_gate,
            "p95_latency_ratio_at_most_2": latency_ratio is not None
            and latency_ratio <= 2,
            "observed_token_ratio_at_most_2": token_ratio is not None
            and token_ratio <= 2,
        },
        "failures": {
            "noncompleted_runs": failed_runs,
            "provider_or_runtime_400": errors_400,
            "illegal_routes": illegal_routes,
            "missing_assessments": missing_assessments,
            "index": failure_index,
        },
        "scientific_adjudication": {
            "status": "pending_independent_labels",
            "not_inferred_by_harness": [
                "critical_false_release",
                "major_recall",
                "clean_case_false_block_or_revise",
                "blind_score_gain",
            ],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("runs_root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = summarize(args.runs_root)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
