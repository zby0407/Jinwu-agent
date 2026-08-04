"""Aggregate blind evaluation records for Research Review 2.0.

This scorer never calls a model and never substitutes for expert labels. It
checks that a completed A/B/C/D evaluation record meets the frozen release
policy and emits machine-readable aggregate metrics.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

_COUNT_FIELDS = {
    "critical_total",
    "critical_missed",
    "major_total",
    "major_detected",
    "unresolved_major_critical",
}


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    index = max(0, (95 * len(ordered) + 99) // 100 - 1)
    return ordered[index]


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"line {line_number} must be a JSON object")
        rows.append(value)
    return rows


def _validate_record(row: dict[str, Any], index: int) -> None:
    label = f"record {index}"
    if not isinstance(row.get("case_id"), str) or not row["case_id"].strip():
        raise ValueError(f"{label} must have a non-empty case_id")
    if row.get("suite_visibility") not in {
        "checked_in_visible",
        "external_hidden",
    }:
        raise ValueError(f"{label} has an invalid suite_visibility")
    for field in _COUNT_FIELDS:
        value = row.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{label}.{field} must be a non-negative integer")
    if row["critical_missed"] > row["critical_total"]:
        raise ValueError(f"{label}.critical_missed exceeds critical_total")
    if row["major_detected"] > row["major_total"]:
        raise ValueError(f"{label}.major_detected exceeds major_total")
    for field in ("stale_approval_blocked", "unreviewed_claim_blocked"):
        if not isinstance(row.get(field), bool):
            raise ValueError(f"{label}.{field} must be boolean")
    blind_score = row.get("blind_score")
    if (
        isinstance(blind_score, bool)
        or not isinstance(blind_score, (int, float))
        or not 0 <= float(blind_score) <= 100
    ):
        raise ValueError(f"{label}.blind_score must be in [0, 100]")
    cost = row.get("cost")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0:
        raise ValueError(f"{label}.cost must be non-negative")
    latency = row.get("latency_seconds", row.get("p95_latency_seconds"))
    if (
        isinstance(latency, bool)
        or not isinstance(latency, (int, float))
        or latency < 0
    ):
        raise ValueError(f"{label} must have a non-negative latency")
    if row["suite_visibility"] == "external_hidden":
        for field in (
            "hard_gates_passed",
            "real_experiment_reproducible",
            "all_visible_claims_traceable",
        ):
            if not isinstance(row.get(field), bool):
                raise ValueError(f"{label}.{field} must be boolean")
        unresolved = row.get("unresolved_critical")
        if (
            isinstance(unresolved, bool)
            or not isinstance(unresolved, int)
            or unresolved < 0
        ):
            raise ValueError(
                f"{label}.unresolved_critical must be a non-negative integer"
            )
        votes = row.get("blind_review_votes")
        if (
            not isinstance(votes, dict)
            or set(votes)
            != {
                "domain",
                "methods_statistics",
                "reproducibility",
            }
            or not all(isinstance(vote, bool) for vote in votes.values())
        ):
            raise ValueError(f"{label}.blind_review_votes is malformed")


def score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for index, row in enumerate(rows, 1):
        _validate_record(row, index)
        variant = row.get("variant")
        if variant not in {"A", "B", "C", "D"}:
            raise ValueError("every record must use variant A, B, C, or D")
        identity = (str(variant), str(row["case_id"]))
        if identity in seen:
            raise ValueError("duplicate variant/case_id evaluation record")
        seen.add(identity)
        grouped[str(variant)].append(row)

    metrics: dict[str, dict[str, float | int]] = {}
    for variant, records in grouped.items():
        critical_total = sum(int(row.get("critical_total", 0)) for row in records)
        critical_missed = sum(int(row.get("critical_missed", 0)) for row in records)
        major_total = sum(int(row.get("major_total", 0)) for row in records)
        major_detected = sum(int(row.get("major_detected", 0)) for row in records)
        metrics[variant] = {
            "records": len(records),
            "unique_cases": len({str(row.get("case_id")) for row in records}),
            "external_hidden": all(
                row.get("suite_visibility") == "external_hidden" for row in records
            ),
            "mean_blind_score": sum(float(row["blind_score"]) for row in records)
            / len(records),
            "critical_false_release": critical_missed,
            "major_recall": major_detected / major_total if major_total else 1.0,
            "stale_approval_block_rate": sum(
                bool(row.get("stale_approval_blocked")) for row in records
            )
            / len(records),
            "unreviewed_claim_block_rate": sum(
                bool(row.get("unreviewed_claim_blocked")) for row in records
            )
            / len(records),
            "mean_unresolved_major_critical": sum(
                int(row.get("unresolved_major_critical", 0)) for row in records
            )
            / len(records),
            "mean_cost": sum(float(row["cost"]) for row in records) / len(records),
            "p95_latency_seconds": _p95(
                [
                    float(row.get("latency_seconds", row.get("p95_latency_seconds", 0)))
                    for row in records
                ]
            ),
            "critical_total": critical_total,
        }

    required_variants = {"A", "B", "C", "D"}
    hidden_release_eligible = bool(
        required_variants <= set(metrics)
        and all(metrics[variant]["external_hidden"] for variant in required_variants)
        and all(
            int(metrics[variant]["unique_cases"]) >= 12 for variant in required_variants
        )
    )
    promotion: dict[str, Any] = {
        "evaluable": hidden_release_eligible,
        "hidden_release_eligible": hidden_release_eligible,
        "adaptive_default_allowed": False,
    }
    if promotion["evaluable"]:
        adaptive = metrics["C"]
        fixed = metrics["D"]
        gain = float(adaptive["mean_blind_score"]) - float(fixed["mean_blind_score"])
        cost_ratio = float(adaptive["mean_cost"]) / max(
            float(fixed["mean_cost"]), 1e-12
        )
        latency_ratio = float(adaptive["p95_latency_seconds"]) / max(
            float(fixed["p95_latency_seconds"]), 1e-12
        )
        fixed_unresolved = float(fixed["mean_unresolved_major_critical"])
        unresolved_reduction = (
            (fixed_unresolved - float(adaptive["mean_unresolved_major_critical"]))
            / fixed_unresolved
            if fixed_unresolved > 0
            else 0.0
        )
        calibration_passed = (
            int(adaptive["critical_false_release"]) == 0
            and float(adaptive["major_recall"]) >= 0.9
            and float(adaptive["stale_approval_block_rate"]) == 1.0
            and float(adaptive["unreviewed_claim_block_rate"]) == 1.0
        )
        promotion.update(
            {
                "blind_score_gain": gain,
                "unresolved_major_critical_reduction": unresolved_reduction,
                "cost_ratio": cost_ratio,
                "p95_latency_ratio": latency_ratio,
                "calibration_passed": calibration_passed,
                "adaptive_default_allowed": (
                    (gain >= 5.0 or unresolved_reduction >= 0.2)
                    and calibration_passed
                    and int(adaptive["critical_false_release"])
                    <= int(fixed["critical_false_release"])
                    and cost_ratio <= 2.0
                    and latency_ratio <= 2.0
                ),
            }
        )
    candidate_variant = "C" if promotion["adaptive_default_allowed"] else "D"
    candidate_records = grouped.get(candidate_variant, [])
    top_gate_criteria = {
        "external_hidden_suite": hidden_release_eligible,
        "all_hard_gates": bool(candidate_records)
        and all(row.get("hard_gates_passed") is True for row in candidate_records),
        "unresolved_critical_zero": bool(candidate_records)
        and all(row.get("unresolved_critical") == 0 for row in candidate_records),
        "real_experiments_reproducible": bool(candidate_records)
        and all(
            row.get("real_experiment_reproducible") is True for row in candidate_records
        ),
        "all_visible_claims_traceable": bool(candidate_records)
        and all(
            row.get("all_visible_claims_traceable") is True for row in candidate_records
        ),
        "blind_review_majority": bool(candidate_records)
        and all(
            _blind_majority(row.get("blind_review_votes")) for row in candidate_records
        ),
    }
    top_gate = {
        "candidate_variant": candidate_variant,
        "evaluable": hidden_release_eligible,
        "criteria": top_gate_criteria,
        "passed": hidden_release_eligible and all(top_gate_criteria.values()),
    }
    return {
        "variants": metrics,
        "adaptive_promotion": promotion,
        "top_journal_candidate_gate": top_gate,
    }


def _blind_majority(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "domain",
        "methods_statistics",
        "reproducibility",
    }:
        return False
    votes = [value[key] for key in sorted(value)]
    return all(isinstance(vote, bool) for vote in votes) and sum(votes) >= 2


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("records", type=Path)
    args = parser.parse_args()
    print(json.dumps(score(_load_jsonl(args.records)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
