"""自动实验 Agent macOS seatbelt 后端的集成测试。

只在 darwin 上运行；Windows/WSL 路径由上游 zip 自带测试覆盖。
请求、设计与代码夹具来自上游 tests/helpers.py 的确定性最小用例。
"""

from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS seatbelt backend only")

from automatic_experiment import service  # noqa: E402
from automatic_experiment.contracts import (  # noqa: E402
    DEFAULT_BUDGET,
    DEFAULT_RUN_BUDGET,
    DESIGN_VERSION,
    REQUEST_VERSION,
    RESPONSE_VERSION,
)
from automatic_experiment.state import RUNS_ROOT  # noqa: E402


def _request(task_name: str = "macos_smoke", wall_seconds: int = 30) -> dict:
    return {
        "schema_version": REQUEST_VERSION,
        "task_name": task_name,
        "task": "Compute one deterministic mean and preserve a reviewable JSON artifact.",
        "input_refs": [],
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
            "max_attempts": 2,
        },
        "run_budget": {**DEFAULT_RUN_BUDGET, "max_total_attempts": 6},
        "seed_policy": {"mode": "fixed", "seeds": [1729]},
        "replay_of": None,
        "user_notes": "",
    }


def _response(req: dict) -> dict:
    return {
        "schema_version": RESPONSE_VERSION,
        "task_name": req["task_name"],
        "task": req["task"],
        "response_kind": "experiment_ready",
        "normalized_task": req["task"],
        "design_summary": "Use the minimum deterministic computation required by the task.",
        "clarifications": [],
        "blockers": [],
        "method_fit": "suitable",
    }


def _design(req: dict) -> dict:
    return {
        "schema_version": DESIGN_VERSION,
        "task_name": req["task_name"],
        "normalized_task": req["task"],
        "design_summary": "Use the minimum deterministic computation required by the task.",
        "method_fit": "suitable",
        "input_ids": [],
        "research_frame": {
            "primary_question": "What deterministic value is produced by the specified calculation?",
            "analysis_mode": "Deterministic technical validation.",
            "claim_scope": "The result describes only the supplied input or fixed fixture.",
            "input_evidence": [],
            "supported_questions": ["Compute and verify the requested deterministic result."],
            "deferred_questions": [],
            "assumptions": ["The declared numeric values are parseable and finite."],
            "threats_to_validity": [
                "The fixture is intentionally small and cannot support population inference."
            ],
            "literature_basis": "No external literature claim is needed for this deterministic fixture.",
        },
        "paired_comparison_audits": [],
        "measurement_plan": [
            {
                "name": "mean",
                "display_name": "Arithmetic mean",
                "role": "primary",
                "unit": "dimensionless",
                "scientific_meaning": "The arithmetic average of the finite values declared by the fixture.",
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
            }
        ],
        "criteria": [
            {
                "id": "mean_result",
                "statement": "The arithmetic mean is reported and its endpoint completes.",
                "basis_kind": "data_derived",
                "basis_text": "The result is recomputed from the fixed values used by the experiment program.",
                "source_refs": [],
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
                "input_ids": [],
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
            {"name": "mean", "value": mean, "unit": "dimensionless",
             "role": "primary", "source_artifact": "summary.json"}
        ],
        "result_items": [],
        "artifacts": [{"path": "summary.json", "kind": "json", "description": "Mean result"}],
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

LOOP_CODE = """def run_experiment(context):
    value = 0
    while True:
        value += 1
    return {
        "schema_version": "automatic-experiment-worker-result-v1",
        "execution_completed": True,
        "measurements": [
            {"name": "mean", "value": 0.0, "unit": "dimensionless",
             "role": "primary", "source_artifact": "summary.json"}
        ],
        "result_items": [],
        "artifacts": [{"path": "summary.json", "kind": "json", "description": "Unreachable mean result"}],
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


def _assessment() -> dict:
    return {
        "proposed_outcome": "completed_interpretable",
        "stage_outcome": "completed",
        "rationale": "The declared deterministic result was produced and verified.",
        "criterion_results": [
            {
                "criterion_id": "mean_result",
                "status": "met",
                "explanation": "The expected mean measurement is present and the planned endpoint completed.",
            }
        ],
        "uncertainty_reasons": [],
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
            "next_steps": ["Replay the run when an exact reproducibility check is needed."],
        },
    }


def _cleanup(run_id: str) -> None:
    root = (RUNS_ROOT / run_id).resolve()
    if root.parent == RUNS_ROOT.resolve() and root.name.startswith("macos_"):
        shutil.rmtree(root, ignore_errors=True)


class MacosBackendTests(unittest.TestCase):
    def test_doctor_ready(self):
        result = service.doctor()
        assert result["platform"] == "macos"
        assert result["status"] == "ready"
        assert result["checks"]["sandbox_probe"]["ready"]

    def test_end_to_end_run(self):
        req = _request()
        run_id = service.bind_request({"request": req})["run_id"]
        self.addCleanup(_cleanup, run_id)
        service.inspect_inputs(run_id)
        checked = service.validate_and_store_design(run_id, _response(req), _design(req))
        assert checked["status"] == "design_validated"
        attempt_id = service.prepare(
            run_id,
            [{"path": "experiment.py", "content": SUCCESS_CODE}],
            None,
            "Initial reviewed implementation.",
        )["attempt_id"]
        result = service.execute(run_id, attempt_id)
        facts = result["execution_facts"]
        assert facts["command_kind"] == "fixed_macos_seatbelt_python"
        assert facts["sandbox_exit_code"] == 0
        policy = facts["sandbox_policy"]
        assert policy["backend"] == "macOS seatbelt (sandbox-exec)"
        assert policy["network_isolation"]
        assert policy["host_file_reads_restricted"]
        assert not policy["memory_rlimit_enforced"]
        preview = service.verify(run_id, attempt_id, None)
        assert preview["status"] == "assessment_required"
        verified = service.verify(run_id, attempt_id, _assessment())
        assert verified["outcome"] == "completed_interpretable"
        finalized = service.finalize(run_id)
        run_root = RUNS_ROOT / run_id
        assert (run_root / "report.md").is_file()
        assert (run_root / "audit.md").is_file()
        assert "report.md" in finalized["report_path"]
        report = (run_root / "report.md").read_text(encoding="utf-8")
        assert "2.5" in report

    def test_wall_budget_stops_infinite_loop(self):
        req = _request(task_name="macos_loop", wall_seconds=3)
        run_id = service.bind_request({"request": req})["run_id"]
        self.addCleanup(_cleanup, run_id)
        service.inspect_inputs(run_id)
        service.validate_and_store_design(run_id, _response(req), _design(req))
        attempt_id = service.prepare(
            run_id,
            [{"path": "experiment.py", "content": LOOP_CODE}],
            None,
            "Initial reviewed implementation.",
        )["attempt_id"]
        facts = service.execute(run_id, attempt_id)["execution_facts"]
        assert facts["stop_reason"] == "wall_budget"
        assert facts["sandbox_exit_code"] == 124


if __name__ == "__main__":
    unittest.main()
