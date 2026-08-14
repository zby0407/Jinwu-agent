#!/usr/bin/env python3
"""Score visible high-quality review records without claiming scientific release."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

EXPECTED_CASES = {
    "FR-H01",
    "FR-H02",
    "FR-H03",
    "FR-H05",
    "FR-H09",
    "FR-H10",
    "FR-H12",
    "SC26-B03",
    "SC26-B05",
    "SC26-B06",
}
COUNT_FIELDS = {
    "workflow_status_misclassifications",
    "unsupported_critical_claims",
    "false_novelty_priority",
    "load_bearing_claims",
    "matrix_covered_claims",
    "seeded_conflicts_total",
    "seeded_conflicts_detected",
    "major_defects_total",
    "major_defects_detected",
    "potentially_novel_count",
    "domain_novelty_confirmed_count",
}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"line {line_number} must be a JSON object")
        rows.append(row)
    return rows


def _validate(row: dict[str, Any], index: int) -> None:
    label = f"record {index}"
    if row.get("case_id") not in EXPECTED_CASES:
        raise ValueError(f"{label}.case_id is not in the frozen visible suite")
    if row.get("implementation_phase") not in {"baseline", "post_freeze"}:
        raise ValueError(f"{label}.implementation_phase is invalid")
    repetition = row.get("repetition")
    if (
        isinstance(repetition, bool)
        or not isinstance(repetition, int)
        or repetition < 1
    ):
        raise ValueError(f"{label}.repetition must be a positive integer")
    signature = row.get("scientific_conclusion_signature")
    if not isinstance(signature, dict) or set(signature) != {
        "conclusion",
        "release_class",
        "evidence_limit_preservation",
    }:
        raise ValueError(f"{label}.scientific_conclusion_signature is malformed")
    if not all(
        isinstance(value, str) and value.strip() for value in signature.values()
    ):
        raise ValueError(f"{label} signature values must be non-empty strings")
    for field in COUNT_FIELDS:
        value = row.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{label}.{field} must be a non-negative integer")
    for detected, total in (
        ("matrix_covered_claims", "load_bearing_claims"),
        ("seeded_conflicts_detected", "seeded_conflicts_total"),
        ("major_defects_detected", "major_defects_total"),
        ("domain_novelty_confirmed_count", "potentially_novel_count"),
    ):
        if row[detected] > row[total]:
            raise ValueError(f"{label}.{detected} exceeds {total}")
    if not isinstance(row.get("negative_outcome_expected"), bool) or not isinstance(
        row.get("negative_outcome_preserved"), bool
    ):
        raise ValueError(f"{label} negative-outcome fields must be boolean")
    annotation = row.get("external_annotation")
    if annotation is None:
        return
    if not isinstance(annotation, dict) or set(annotation) != {
        "role_entailment_total",
        "role_entailment_correct",
        "blind_scores",
        "unresolved_critical",
    }:
        raise ValueError(f"{label}.external_annotation is malformed")
    total = annotation["role_entailment_total"]
    correct = annotation["role_entailment_correct"]
    unresolved = annotation["unresolved_critical"]
    if (
        any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (total, correct, unresolved)
        )
        or correct > total
    ):
        raise ValueError(f"{label}.external_annotation counts are invalid")
    scores = annotation["blind_scores"]
    if (
        not isinstance(scores, dict)
        or set(scores)
        != {
            "domain",
            "methods_statistics",
            "reproducibility",
        }
        or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0 <= float(value) <= 100
            for value in scores.values()
        )
    ):
        raise ValueError(f"{label}.external_annotation.blind_scores is malformed")


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0


def score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    seen: set[tuple[str, str, int]] = set()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, row in enumerate(rows, 1):
        _validate(row, index)
        identity = (row["implementation_phase"], row["case_id"], row["repetition"])
        if identity in seen:
            raise ValueError("duplicate implementation_phase/case_id/repetition record")
        seen.add(identity)
        grouped[row["implementation_phase"]].append(row)

    baseline = grouped["baseline"]
    post = grouped["post_freeze"]
    baseline_complete = {(row["case_id"], row["repetition"]) for row in baseline} == {
        (case_id, 1) for case_id in EXPECTED_CASES
    }
    post_complete = {(row["case_id"], row["repetition"]) for row in post} == {
        (case_id, repetition) for case_id in EXPECTED_CASES for repetition in (1, 2, 3)
    }
    stable_cases: dict[str, bool] = {}
    for case_id in sorted(EXPECTED_CASES):
        signatures = {
            json.dumps(row["scientific_conclusion_signature"], sort_keys=True)
            for row in post
            if row["case_id"] == case_id
        }
        stable_cases[case_id] = (
            len(signatures) == 1 and sum(row["case_id"] == case_id for row in post) == 3
        )

    totals = {field: sum(row[field] for row in post) for field in COUNT_FIELDS}
    negative_rows = [row for row in post if row["negative_outcome_expected"]]
    automated = {
        "workflow_status_misclassifications": totals[
            "workflow_status_misclassifications"
        ],
        "unsupported_critical_claims": totals["unsupported_critical_claims"],
        "false_novelty_priority": totals["false_novelty_priority"],
        "evidence_matrix_coverage": _rate(
            totals["matrix_covered_claims"], totals["load_bearing_claims"]
        ),
        "seeded_conflict_recall": _rate(
            totals["seeded_conflicts_detected"], totals["seeded_conflicts_total"]
        ),
        "major_defect_recall": _rate(
            totals["major_defects_detected"], totals["major_defects_total"]
        ),
        "negative_outcome_preservation": _rate(
            sum(row["negative_outcome_preserved"] for row in negative_rows),
            len(negative_rows),
        ),
        "stable_cases": stable_cases,
    }
    automated_passed = (
        baseline_complete
        and post_complete
        and automated["workflow_status_misclassifications"] == 0
        and automated["unsupported_critical_claims"] == 0
        and automated["false_novelty_priority"] == 0
        and automated["evidence_matrix_coverage"] == 1.0
        and automated["seeded_conflict_recall"] == 1.0
        and automated["major_defect_recall"] >= 0.9
        and automated["negative_outcome_preservation"] == 1.0
        and all(stable_cases.values())
    )

    annotated_baseline = [row for row in baseline if row.get("external_annotation")]
    annotated_post = [row for row in post if row.get("external_annotation")]
    external_evaluable = {
        row["case_id"] for row in annotated_baseline
    } == EXPECTED_CASES and {row["case_id"] for row in annotated_post} == EXPECTED_CASES
    external: dict[str, Any] = {"evaluable": external_evaluable, "passed": False}
    if external_evaluable:
        role_total = sum(
            row["external_annotation"]["role_entailment_total"] for row in annotated_post
        )
        role_correct = sum(
            row["external_annotation"]["role_entailment_correct"] for row in annotated_post
        )
        post_scores = [
            score_value
            for row in annotated_post
            for score_value in row["external_annotation"]["blind_scores"].values()
        ]
        baseline_scores = [
            score_value
            for row in annotated_baseline
            for score_value in row["external_annotation"]["blind_scores"].values()
        ]
        accuracy = _rate(role_correct, role_total)
        score_gain = sum(post_scores) / len(post_scores) - sum(baseline_scores) / len(
            baseline_scores
        )
        external.update(
            {
                "role_entailment_accuracy": accuracy,
                "minimum_blind_score": min(post_scores),
                "blind_score_gain": score_gain,
                "unresolved_critical": sum(
                    row["external_annotation"]["unresolved_critical"]
                    for row in annotated_post
                ),
                "novelty_confirmation_rate": _rate(
                    totals["domain_novelty_confirmed_count"],
                    totals["potentially_novel_count"],
                ),
            }
        )
        external["passed"] = (
            accuracy >= 0.95
            and external["minimum_blind_score"] >= 80
            and score_gain >= 5
            and external["unresolved_critical"] == 0
            and external["novelty_confirmation_rate"] == 1.0
        )

    return {
        "baseline_complete": baseline_complete,
        "post_freeze_complete": post_complete,
        "automated_visible_gate": {"metrics": automated, "passed": automated_passed},
        "external_output_assessment": external,
        "visible_suite_passed": automated_passed and external["passed"],
        "scientific_capability_validated": False,
        "external_validation_required": (
            "At least 12 external-hidden cases, one real replication or external-data test, "
            "and domain/methods/reproducibility majority review remain mandatory."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("records", type=Path)
    args = parser.parse_args()
    print(json.dumps(score(_load_jsonl(args.records)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
