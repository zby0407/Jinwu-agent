from __future__ import annotations

from copy import deepcopy
import unittest

from automatic_experiment.contracts import ContractError, validate_scientific_assessment
from automatic_experiment.reporting import render_report
from tests.helpers import design, request


def narrative():
    return {
        "title": "Scientific outcome report",
        "objective": "Evaluate the declared scientific outcome.",
        "data_scope": "The report covers the verified experiment result.",
        "method": "Apply the checked interpretation policy to the verified measurements.",
        "interpretation": "The outcome is interpreted only within the declared estimand and evidence.",
        "evidence_strength": "The evidence is sufficient only for the declared estimand and checked endpoint.",
        "claim_boundary": "No causal, predictive, or population-level claim is supported.",
        "limitations": ["The result is limited by the declared data and uncertainty evidence."],
        "next_steps": ["Review the verified artifacts before extending the conclusion."],
    }


def worker(endpoint_rows=None):
    return {
        "schema_version": "automatic-experiment-worker-result-v1",
        "execution_completed": True,
        "measurements": [{"name": "mean", "value": 0.0, "unit": "", "role": "primary", "source_artifact": None}],
        "result_items": [],
        "artifacts": [],
        "warnings": [],
        "endpoint_results": endpoint_rows or [{"id": "mean_endpoint", "status": "completed", "summary": "Done."}],
        "scientific_payload": {
            "primary_estimand": "arithmetic mean",
            "estimate": 0.0,
            "interval": None,
            "equivalence_bounds": None,
            "sensitivity": None,
            "uncertainty_reasons": [],
        },
    }


class ScientificOutcomeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.design = design(request())

    def test_valid_scientific_null(self) -> None:
        self.design["research_frame"]["analysis_mode"] = (
            "Inferential assessment with a predeclared confidence interval."
        )
        candidate_worker = worker()
        candidate_worker["scientific_payload"]["interval"] = [-0.1, 0.1]
        candidate_worker["scientific_payload"]["equivalence_bounds"] = [-0.2, 0.2]
        candidate_worker["scientific_payload"]["sensitivity"] = (
            "The design detects effects outside the equivalence range."
        )
        payload = {
            "proposed_outcome": "scientific_null",
            "rationale": "The interval lies inside the predefined equivalence range.",
            "criterion_results": [
                {
                    "criterion_id": "mean_result",
                    "status": "met",
                    "explanation": "The mean and planned endpoint were verified.",
                }
            ],
            "uncertainty_reasons": [],
            "null_assessment": {
                "estimand": "arithmetic mean",
                "interval": [-0.1, 0.1],
                "equivalence_bounds": [-0.2, 0.2],
                "power_or_sensitivity": "Effects outside the equivalence range were detectable.",
            },
            "report_narrative": narrative(),
        }
        result = validate_scientific_assessment(
            payload,
            self.design,
            candidate_worker,
        )
        self.assertEqual(result["proposed_outcome"], "scientific_null")

    def test_completed_interpretable_accepts_a_typed_non_numeric_result(self) -> None:
        candidate_design = deepcopy(self.design)
        candidate_design["interpretation_policy"]["primary_estimand"] = (
            "data audit outcome"
        )
        candidate_design["criteria"][0]["statement"] = (
            "The data audit result is reported and its endpoint completes."
        )
        candidate_design["criteria"][0]["measurement_refs"] = []
        candidate_design["experiment_stages"][0]["measurement_refs"] = []
        candidate_design["experiment_stages"][0]["result_refs"] = ["audit_status"]

        candidate_worker = worker()
        candidate_worker["measurements"] = []
        candidate_worker["result_items"] = [
            {
                "id": "audit_status",
                "display_name": "Data audit status",
                "value_kind": "category",
                "value": "passed",
                "unit": "",
                "role": "primary",
                "source_artifact": None,
            }
        ]
        candidate_worker["scientific_payload"] = {
            "primary_estimand": "data audit outcome",
            "estimate": None,
            "interval": None,
            "equivalence_bounds": None,
            "sensitivity": None,
            "uncertainty_reasons": [],
        }
        payload = {
            "proposed_outcome": "completed_interpretable",
            "stage_outcome": "completed",
            "rationale": "The declared audit result and endpoint were verified.",
            "criterion_results": [
                {
                    "criterion_id": "mean_result",
                    "status": "met",
                    "explanation": "The audit result is present and the endpoint completed.",
                }
            ],
            "uncertainty_reasons": [],
            "null_assessment": None,
            "report_narrative": narrative(),
        }

        validated = validate_scientific_assessment(
            payload,
            candidate_design,
            candidate_worker,
        )
        self.assertEqual(validated["proposed_outcome"], "completed_interpretable")

        candidate_worker["result_items"] = []
        with self.assertRaisesRegex(ContractError, "measured or typed result"):
            validate_scientific_assessment(
                payload,
                candidate_design,
                candidate_worker,
            )

    def test_partial_requires_mixed_endpoints(self) -> None:
        payload = {
            "proposed_outcome": "partial_result",
            "rationale": "Only part of the planned endpoints completed.",
            "criterion_results": [
                {
                    "criterion_id": "mean_result",
                    "status": "met",
                    "explanation": "The mean and planned endpoint were verified.",
                }
            ],
            "uncertainty_reasons": [],
            "null_assessment": None,
            "report_narrative": narrative(),
        }
        with self.assertRaisesRegex(ContractError, "both completed and incomplete"):
            validate_scientific_assessment(payload, self.design, worker())

    def test_high_uncertainty_requires_reason(self) -> None:
        payload = {
            "proposed_outcome": "high_uncertainty",
            "rationale": "The estimate is unstable.",
            "criterion_results": [
                {
                    "criterion_id": "mean_result",
                    "status": "met",
                    "explanation": "The mean and planned endpoint were verified.",
                }
            ],
            "uncertainty_reasons": [],
            "null_assessment": None,
            "report_narrative": narrative(),
        }
        with self.assertRaisesRegex(ContractError, "explicit uncertainty"):
            validate_scientific_assessment(payload, self.design, worker())

    def test_all_terminal_states_have_formal_report(self) -> None:
        outcomes = [
            "completed_interpretable",
            "partial_result",
            "scientific_null",
            "high_uncertainty",
            "input_missing",
            "method_mismatch",
            "technical_failure",
            "budget_stopped",
            "clarification_required",
            "boundary_blocked",
            "cancelled_by_user",
        ]
        for outcome in outcomes:
            with self.subTest(outcome=outcome):
                record = {
                    "run_id": f"outcome_{outcome}",
                    "outcome": outcome,
                    "task": "Evaluate one formal terminal-state report.",
                    "outcome_reason": "Fixture reason.",
                    "record_sha256": "0" * 64,
                    "input_snapshot": None,
                    "scientific_assessment": None,
                    "worker_result": None,
                    "execution_facts": None,
                    "public_artifacts": [],
                    "replay": {"pi_command": f"/automatic-experiment 重放 outcome_{outcome}"},
                }
                text = render_report(record)
                self.assertTrue(text.startswith("# 实验分析报告"))
                self.assertIn("## 当前情况", text)
                self.assertIn("当前没有形成可报告的测量结果", text)
                self.assertIn("## 继续研究所需条件", text)
                self.assertNotIn("schema_version", text)
                self.assertNotIn("/automatic-experiment", text)
                self.assertEqual(text.count("Fixture reason."), 1)

    def test_reader_report_hides_internal_criterion_ids_and_enums(self) -> None:
        record = {
            "run_id": "outcome_reader_report",
            "outcome": "completed_interpretable",
            "task": "计算示例数据的总体均值。",
            "outcome_reason": "计算完成。",
            "record_sha256": "0" * 64,
            "input_snapshot": None,
            "scientific_assessment": {
                "proposed_outcome": "completed_interpretable",
                "rationale": "计算完成。",
                "criterion_results": [
                    {
                        "criterion_id": "crit_01",
                        "status": "met",
                        "explanation": "所需均值和结果文件均已核验。",
                    }
                ],
                "uncertainty_reasons": [],
                "null_assessment": None,
                "report_narrative": {
                    "title": "示例数据总体均值计算报告",
                    "objective": "计算示例数据中全部观测值的总体均值。",
                    "data_scope": "仅使用本次输入快照中的四条记录，不进行分组比较。",
                    "method": "读取数值列并计算算术平均值，同时记录实际纳入的记录数。",
                    "interpretation": "结果只描述这四条输入记录，不构成总体推断或组间差异结论。",
                    "evidence_strength": "证据只足以支持输入记录的精确算术结果。",
                    "claim_boundary": "不能据此形成总体、组间、因果或预测结论。",
                    "limitations": ["样本仅用于链路演示，不能代表更大总体。"],
                    "next_steps": ["如需比较分组，应另行明确分组统计目标。"],
                },
            },
            "worker_result": {
        "measurements": [
                    {"name": "mean", "value": 2.5, "unit": "", "role": "primary", "source_artifact": None},
                    {"name": "count", "value": 4, "unit": "", "role": "secondary", "source_artifact": None},
        ],
        "result_items": [],
                "warnings": [],
                "scientific_payload": {
                    "uncertainty_reasons": ["样本仅用于链路演示，不能代表更大总体。"]
                },
            },
            "execution_facts": None,
            "evidence_ledger": {
                "criteria": [
                    {
                        "criterion_id": "crit_01",
                        "statement": "均值与记录数均已得到核验。",
                        "basis_kind": "user_request",
                        "basis_text": "用户明确要求报告这两个结果。",
                        "source_refs": [],
                        "artifact_refs": [],
                        "measurements": [
                            {"name": "mean", "value": 2.5, "unit": "", "role": "primary", "source_artifact": None},
                            {"name": "count", "value": 4, "unit": "", "role": "secondary", "source_artifact": None},
                        ],
                        "endpoints": [],
                        "assessment_status": "met",
                        "assessment_explanation": "所需均值和记录数均已核验。",
                    }
                ]
            },
            "public_artifacts": [
                {
                    "path": "public/worker_result.json",
                    "kind": "json",
                    "description": "受信任 worker 结果",
                    "size_bytes": 100,
                    "sha256": "1" * 64,
                },
                {
                    "path": "public/summary.json",
                    "kind": "json",
                    "description": "实验结果摘要",
                    "size_bytes": 80,
                    "sha256": "2" * 64,
                },
            ],
            "replay": {"pi_command": "/automatic-experiment 重放 outcome_reader_report"},
        }
        design_payload = {
            "criteria": [
                {"id": "crit_01", "basis_kind": "user_request"}
            ]
        }
        text = render_report(record, design=design_payload)
        self.assertTrue(text.startswith("# 示例数据总体均值计算报告"))
        self.assertNotIn("crit_01", text)
        self.assertNotIn("：met", text)
        self.assertIn("不进行分组比较", text)
        self.assertNotIn("主要结果为", text)
        self.assertNotIn("worker", text)
        self.assertNotIn("worker_result.json", text)
        self.assertNotIn("public/summary.json", text)
        self.assertEqual(
            text.count("样本仅用于链路演示，不能代表更大总体。"),
            1,
        )


if __name__ == "__main__":
    unittest.main()
