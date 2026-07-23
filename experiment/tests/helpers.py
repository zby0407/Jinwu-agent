from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from automatic_experiment import service
from automatic_experiment.contracts import (
    DEFAULT_BUDGET,
    DEFAULT_RUN_BUDGET,
    DESIGN_VERSION,
    REQUEST_VERSION,
    RESPONSE_VERSION,
)
from automatic_experiment.state import runs_root


def request(
    task_name: str = "unit_experiment",
    *,
    task: str = "Compute one deterministic mean and preserve a reviewable JSON artifact.",
    wall_seconds: int = 30,
    max_attempts: int = 2,
    input_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": REQUEST_VERSION,
        "task_name": task_name,
        "task": task,
        "input_refs": input_refs or [],
        "success_criteria": [],
        "method_constraints": [],
        "resource_budget": {
            **DEFAULT_BUDGET,
            "wall_seconds": wall_seconds,
            "cpu_seconds": 20,
            "disk_mb": 64,
            "single_file_mb": 16,
            "stdout_kb": 1024,
            "stderr_kb": 1024,
            "max_attempts": max_attempts,
        },
        "run_budget": {
            **DEFAULT_RUN_BUDGET,
            "max_total_attempts": max(6, max_attempts),
        },
        "seed_policy": {"mode": "fixed", "seeds": [1729]},
        "replay_of": None,
        "user_notes": "",
    }


def response(req: dict[str, Any], kind: str = "experiment_ready") -> dict[str, Any]:
    return {
        "schema_version": RESPONSE_VERSION,
        "task_name": req["task_name"],
        "task": req["task"],
        "response_kind": kind,
        "normalized_task": req["task"],
        "design_summary": "Use the minimum deterministic computation required by the task.",
        "clarifications": ["Which endpoint should be primary?"] if kind == "clarification_required" else [],
        "blockers": ["The requested capability is outside the verified boundary."]
        if kind == "execution_blocked"
        else [],
        "method_fit": "incompatible" if kind == "execution_blocked" else "suitable",
    }


def design(req: dict[str, Any]) -> dict[str, Any]:
    input_ids = [row["id"] for row in req["input_refs"]]
    return {
        "schema_version": DESIGN_VERSION,
        "task_name": req["task_name"],
        "normalized_task": req["task"],
        "design_summary": "Use the minimum deterministic computation required by the task.",
        "method_fit": "suitable",
        "input_ids": input_ids,
        "research_frame": {
            "primary_question": "What deterministic value is produced by the specified calculation?",
            "analysis_mode": "Deterministic technical validation.",
            "claim_scope": "The result describes only the supplied input or fixed fixture.",
            "input_evidence": [
                {
                    "input_id": input_id,
                    "role": "Verified experiment input",
                    "intended_use": "Supply the values required by the deterministic calculation.",
                    "limitations": "The input does not support inference beyond the requested calculation.",
                }
                for input_id in input_ids
            ],
            "supported_questions": [
                "Compute and verify the requested deterministic result."
            ],
            "deferred_questions": [],
            "assumptions": ["The declared numeric values are parseable and finite."],
            "threats_to_validity": [
                "The fixture is intentionally small and cannot support population inference."
            ],
            "literature_basis": (
                "No external literature claim is needed for this deterministic fixture."
            ),
        },
        "paired_comparison_audits": [],
        "measurement_plan": [
            {
                "name": "mean",
                "display_name": "Arithmetic mean",
                "role": "primary",
                "unit": "dimensionless",
                "scientific_meaning": (
                    "The arithmetic average of the finite values declared by the fixture."
                ),
            }
        ],
        "result_plan": [],
        "method_decisions": [
            {
                "id": "summary_choice",
                "decision_key": "summary_statistic",
                "decision": "Use the arithmetic mean without a fitted model.",
                "rationale": "The task requests one deterministic arithmetic summary.",
                "basis_kind": "method_standard",
                "source_refs": ["arithmetic mean definition"],
                "alternatives": ["Median as a different estimand"],
                "claim_limit": "This choice does not characterize a population distribution.",
            },
        ],
        "criteria": [
            {
                "id": "mean_result",
                "statement": "The arithmetic mean is reported and its endpoint completes.",
                "basis_kind": "data_derived",
                "basis_text": "The result is recomputed from the fixed values used by the experiment program.",
                "source_refs": input_ids,
                "artifact_refs": ["summary.json"],
                "measurement_refs": ["mean"],
                "result_refs": [],
                "endpoint_refs": ["mean_endpoint"],
            }
        ],
        "artifact_plan": [
            {
                "id": "summary_artifact",
                "path": "summary.json",
                "kind": "json",
                "description": "Reviewable arithmetic mean result.",
                "producer_stage_id": "stage_summary",
            }
        ],
        "experiment_stages": [
            {
                "id": "stage_summary",
                "objective": "Compute and verify the requested arithmetic mean.",
                "input_ids": input_ids,
                "consumes_artifact_ids": [],
                "produces_artifact_ids": ["summary_artifact"],
                "prerequisite_stage_ids": [],
                "join_policy": "all",
                "method_outline": "Compute the arithmetic mean from the supplied fixed values.",
                "measurement_refs": ["mean"],
                "result_refs": [],
                "endpoint_ids": ["mean_endpoint"],
                "criterion_refs": ["mean_result"],
                "outcome_rules": {
                    "completed": "The planned mean, endpoint, and artifact are verified.",
                    "inconclusive": "The calculation runs but cannot support the requested interpretation.",
                    "input_missing": "A required value source is unavailable.",
                    "evidence_conflict": "Verified outputs conflict and cannot be reconciled.",
                    "method_invalid": "The arithmetic mean is not a valid answer to the stated task.",
                    "technical_failure": "The code or output fails a deterministic check.",
                    "budget_reached": "The stage cannot continue within its bounded attempts or time.",
                },
                "transitions": {
                    "completed": "completed_interpretable",
                    "inconclusive": "high_uncertainty",
                    "input_missing": "input_missing",
                    "evidence_conflict": "high_uncertainty",
                    "method_invalid": "method_mismatch",
                    "technical_failure": "technical_failure",
                    "budget_reached": "budget_stopped",
                },
                "execution": {
                    "entry_file": "experiment.py",
                    "dependencies": ["numpy"],
                    "deterministic": True,
                    "seed": 1729,
                    "expected_artifacts": ["summary.json"],
                },
            }
        ],
        "interpretation_policy": {
            "primary_estimand": "arithmetic mean",
            "null_rule": "No scientific null claim without an interval and equivalence or sensitivity basis.",
            "uncertainty_rule": "High uncertainty requires explicit data or method reasons.",
            "partial_rule": "Partial result requires both completed and incomplete endpoints.",
        },
    }


SUCCESS_CODE = """import json
import numpy as np

def run_experiment(context):
    values = np.array([1.0, 2.0, 3.0, 4.0])
    mean = float(np.mean(values))
    artifact = context["output_dir"] / "summary.json"
    artifact.write_text(json.dumps({"mean": mean}), encoding="utf-8")
    return {
        "schema_version": "automatic-experiment-worker-result-v1",
        "execution_completed": True,
        "measurements": [
            {
                "name": "mean",
                "value": mean,
                "unit": "dimensionless",
                "role": "primary",
                "source_artifact": "summary.json"
            }
        ],
        "result_items": [],
        "artifacts": [
            {"path": "summary.json", "kind": "json", "description": "Mean result"}
        ],
        "warnings": [],
        "endpoint_results": [
            {"id": "mean_endpoint", "status": "completed", "summary": "Mean calculated."}
        ],
        "scientific_payload": {
            "primary_estimand": "arithmetic mean",
            "estimate": mean,
            "interval": None,
            "equivalence_bounds": None,
            "sensitivity": None,
            "uncertainty_reasons": []
        }
    }
"""

FAIL_CODE = """def run_experiment(context):
    raise RuntimeError("deliberate technical failure")
    return {
        "schema_version": "automatic-experiment-worker-result-v1",
        "execution_completed": True,
        "measurements": [
            {
                "name": "mean",
                "value": 0.0,
                "unit": "dimensionless",
                "role": "primary",
                "source_artifact": "summary.json"
            }
        ],
        "result_items": [],
        "artifacts": [
            {"path": "summary.json", "kind": "json", "description": "Unreachable mean result"}
        ],
        "warnings": [],
        "endpoint_results": [
            {"id": "mean_endpoint", "status": "failed", "summary": "Unreachable failure fixture."}
        ],
        "scientific_payload": {
            "primary_estimand": "arithmetic mean",
            "estimate": 0.0,
            "interval": None,
            "equivalence_bounds": None,
            "sensitivity": None,
            "uncertainty_reasons": []
        }
    }
"""

LOOP_CODE = """def run_experiment(context):
    value = 0
    while True:
        value += 1
    return {
        "schema_version": "automatic-experiment-worker-result-v1",
        "execution_completed": True,
        "measurements": [
            {
                "name": "mean",
                "value": 0.0,
                "unit": "dimensionless",
                "role": "primary",
                "source_artifact": "summary.json"
            }
        ],
        "result_items": [],
        "artifacts": [
            {"path": "summary.json", "kind": "json", "description": "Unreachable mean result"}
        ],
        "warnings": [],
        "endpoint_results": [
            {"id": "mean_endpoint", "status": "not_evaluated", "summary": "Loop did not finish."}
        ],
        "scientific_payload": {
            "primary_estimand": "arithmetic mean",
            "estimate": 0.0,
            "interval": None,
            "equivalence_bounds": None,
            "sensitivity": None,
            "uncertainty_reasons": []
        }
    }
"""

INPUT_MEAN_CODE = """import csv
import json

def run_experiment(context):
    input_path = context["input_path_by_id"]["input_01"]
    values = []
    with input_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            values.append(float(row["value"]))
    mean = sum(values) / len(values)
    artifact = context["output_dir"] / "mean.json"
    artifact.write_text(
        json.dumps({"mean": mean, "count": len(values)}, ensure_ascii=False),
        encoding="utf-8",
    )
    return {
        "schema_version": "automatic-experiment-worker-result-v1",
        "execution_completed": True,
        "measurements": [
            {
                "name": "mean",
                "value": mean,
                "unit": "dimensionless",
                "role": "primary",
                "source_artifact": "mean.json"
            }
        ],
        "result_items": [],
        "artifacts": [
            {"path": "mean.json", "kind": "json", "description": "Arithmetic mean result"}
        ],
        "warnings": [],
        "endpoint_results": [
            {"id": "mean_endpoint", "status": "completed", "summary": "Mean calculated."}
        ],
        "scientific_payload": {
            "primary_estimand": "arithmetic mean",
            "estimate": mean,
            "interval": None,
            "equivalence_bounds": None,
            "sensitivity": None,
            "uncertainty_reasons": []
        }
    }
"""


def assessment(outcome: str = "completed_interpretable") -> dict[str, Any]:
    return {
        "proposed_outcome": outcome,
        "stage_outcome": (
            "completed"
            if outcome in {"completed_interpretable", "scientific_null"}
            else "inconclusive"
        ),
        "rationale": "The declared deterministic result was produced and verified.",
        "criterion_results": [
            {
                "criterion_id": "mean_result",
                "status": "met",
                "explanation": "The expected mean measurement is present and the planned endpoint completed.",
            }
        ],
        "uncertainty_reasons": ["The available sample is limited."] if outcome == "high_uncertainty" else [],
        "null_assessment": None,
        "report_narrative": {
            "title": "Deterministic experiment report",
            "objective": "Evaluate the requested deterministic calculation.",
            "data_scope": "The report covers only the verified input snapshot used by this run.",
            "method": "The sandboxed experiment program executed the checked Python implementation and the deterministic core verified its outputs.",
            "interpretation": "The reported measurements describe the supplied input and do not imply broader population inference.",
            "evidence_strength": "Evidence is sufficient for the exact deterministic calculation but not for broader scientific inference.",
            "claim_boundary": "No causal, predictive, or population-level claim is supported by this fixture.",
            "limitations": ["The interpretation is limited to the supplied input and requested calculation."],
            "next_steps": ["Replay the run through Pi when an exact reproducibility check is needed."],
        },
    }


def create_ready_run(req: dict[str, Any]) -> tuple[str, str]:
    run_id = service.bind_request({"request": req})["run_id"]
    service.inspect_inputs(run_id)
    service.validate_and_store_design(run_id, response(req), design(req))
    attempt_id = service.prepare(
        run_id,
        [{"path": "experiment.py", "content": SUCCESS_CODE}],
        None,
        "Initial reviewed implementation.",
    )["attempt_id"]
    return run_id, attempt_id


def cleanup_run(run_id: str) -> None:
    runs = runs_root().resolve()
    root = (runs / run_id).resolve()
    if root.parent != runs or not root.name.startswith(("unit_", "sandbox_", "path_", "outcome_")):
        raise AssertionError(f"refusing to clean unexpected run path: {root}")
    if root.exists():
        shutil.rmtree(root)
