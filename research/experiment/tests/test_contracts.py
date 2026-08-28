from __future__ import annotations

import json
import unittest
from pathlib import Path

import jsonschema

from automatic_experiment.contracts import (
    ContractError,
    _linked_sensitivity_roles,
    _missing_requested_parameter_differences,
    _reader_model_direction_conflict,
    _sensitivity_criterion_roles,
    _unsupported_reader_metric,
    default_request,
    validate_design,
    validate_request,
    validate_response,
    validate_scientific_assessment,
    validate_worker_result,
)
from tests.helpers import assessment, design, request, response


def set_planned_measurements(
    candidate: dict[str, object],
    names: list[str],
    definitions: dict[str, tuple[str, str]] | None = None,
) -> None:
    definitions = definitions or {}
    rows = []
    for index, name in enumerate(names):
        is_difference = any(
            token in name.casefold()
            for token in ("delta", "difference", "improvement", "contrast")
        )
        default = (
            f"Planned difference measurement {index + 1}",
            "The baseline-condition metric minus the candidate-condition metric.",
        ) if is_difference else (
            f"Planned measurement {index + 1}",
            "A bounded measurement declared for this contract test.",
        )
        display_name, scientific_meaning = definitions.get(name, default)
        rows.append(
            {
                "name": name,
                "display_name": display_name,
                "role": "primary" if index == 0 else "secondary",
                "unit": "dimensionless",
                "scientific_meaning": scientific_meaning,
            }
        )
    candidate["measurement_plan"] = rows
    candidate["criteria"][0]["measurement_refs"] = names
    candidate["experiment_stages"][0]["measurement_refs"] = names


def simple_worker_result() -> dict[str, object]:
    return {
        "schema_version": "automatic-experiment-worker-result-v1",
        "execution_completed": True,
        "measurements": [
            {
                "name": "mean",
                "value": 2.5,
                "unit": "dimensionless",
                "role": "primary",
                "source_artifact": None,
            }
        ],
        "result_items": [],
        "artifacts": [],
        "warnings": [],
        "endpoint_results": [
            {"id": "mean_endpoint", "status": "completed", "summary": "Done."}
        ],
        "scientific_payload": {
            "primary_estimand": "arithmetic mean",
            "estimate": 2.5,
            "interval": None,
            "equivalence_bounds": None,
            "sensitivity": None,
            "uncertainty_reasons": [],
        },
    }


class ContractTests(unittest.TestCase):
    def test_natural_request_defaults_are_closed(self) -> None:
        result = validate_request(default_request("Determine whether one deterministic calculation is executable."))
        self.assertEqual(result["resource_budget"]["gpu_count"], 0)
        self.assertEqual(result["seed_policy"]["seeds"], [1729])

    def test_natural_request_preserves_explicit_seed_and_single_pass_limits(self) -> None:
        result = validate_request(
            default_request(
                "固定随机种子 20260722，实验最多一个阶段、一次正式尝试，执行合成测试。"
            )
        )
        self.assertEqual(result["seed_policy"]["seeds"], [20260722])
        self.assertEqual(result["resource_budget"]["max_attempts"], 1)
        self.assertEqual(result["run_budget"]["max_stages"], 1)
        self.assertEqual(result["run_budget"]["max_total_attempts"], 1)

    def test_natural_request_binds_explicit_input_path(self) -> None:
        result = validate_request(
            default_request(
                "读取 inputs/example_mean.csv，计算全部 value 的均值并保存 JSON 结果。"
            )
        )
        self.assertEqual(
            result["input_refs"],
            [
                {
                    "id": "input_01",
                    "path": "inputs/example_mean.csv",
                    "description": "Explicit input path referenced in the natural-language task.",
                    "required": True,
                }
            ],
        )

    def test_natural_request_deduplicates_quoted_input_paths(self) -> None:
        result = validate_request(
            default_request(
                "读取 `inputs/example_mean.csv`，并复核 inputs/example_mean.csv 的 value 列。"
            )
        )
        self.assertEqual(len(result["input_refs"]), 1)
        self.assertEqual(result["input_refs"][0]["path"], "inputs/example_mean.csv")

    def test_natural_request_excludes_ascii_punctuation_after_input_paths(self) -> None:
        result = validate_request(
            default_request(
                "Task-local copies inputs/readiness_inventory.json, "
                "inputs/data-context.json; inspect both before design."
            )
        )
        self.assertEqual(
            [row["path"] for row in result["input_refs"]],
            ["inputs/readiness_inventory.json", "inputs/data-context.json"],
        )

    def test_upstream_handoff_prompt_binds_plan_feedback_and_data(self) -> None:
        result = validate_request(
            default_request(
                "结合 inputs/upstream_handoff_demo/research_plan_feedback.md 与 "
                "inputs/upstream_handoff_demo/data_feature_feedback.json，使用 "
                "inputs/upstream_handoff_demo/polar_overlap_features.csv 自主设计实验。"
            )
        )
        self.assertEqual(
            [row["path"] for row in result["input_refs"]],
            [
                "inputs/upstream_handoff_demo/research_plan_feedback.md",
                "inputs/upstream_handoff_demo/data_feature_feedback.json",
                "inputs/upstream_handoff_demo/polar_overlap_features.csv",
            ],
        )

    def test_unknown_request_field_is_rejected(self) -> None:
        payload = request()
        payload["shell"] = "whoami"
        with self.assertRaisesRegex(ContractError, "unknown"):
            validate_request(payload)

    def test_gpu_request_is_preserved_for_formal_boundary_outcome(self) -> None:
        payload = request()
        payload["resource_budget"]["gpu_count"] = 1
        payload["resource_budget"]["gpu_memory_mb"] = 4096
        result = validate_request(payload)
        self.assertEqual(result["resource_budget"]["gpu_count"], 1)
        self.assertEqual(result["resource_budget"]["gpu_memory_mb"], 4096)

    def test_single_file_budget_cannot_exceed_disk(self) -> None:
        payload = request()
        payload["resource_budget"]["single_file_mb"] = 65
        with self.assertRaisesRegex(ContractError, "cannot exceed"):
            validate_request(payload)

    def test_qualitative_numeric_cutoff_is_rejected(self) -> None:
        payload = request()
        payload["success_criteria"] = [{
            "id": "criterion_a",
            "statement": "Accuracy must be greater than 95.",
            "basis_kind": "qualitative_no_fixed_threshold",
            "basis_text": "No threshold source exists.",
            "source_refs": [],
            "artifact_refs": [],
        }]
        with self.assertRaisesRegex(ContractError, "numeric cutoff"):
            validate_request(payload)

    def test_numeric_cutoff_basis_must_repeat_value_and_provenance(self) -> None:
        req = request(
            input_refs=[
                {
                    "id": "input_a",
                    "path": "inputs/example.csv",
                    "description": "Data used to derive the decision cutoff.",
                    "required": True,
                }
            ]
        )
        candidate = design(req)
        candidate["criteria"][0]["statement"] = "The error must be below 0.5."
        candidate["criteria"][0]["basis_kind"] = "data_derived"
        candidate["criteria"][0]["basis_text"] = "Derived from the current data."
        with self.assertRaisesRegex(ContractError, "repeat the numeric cutoff"):
            validate_design(candidate, req, response(req))

        candidate["criteria"][0]["basis_text"] = (
            "The 0.5 cutoff is derived from the declared baseline error scale."
        )
        validated = validate_design(candidate, req, response(req))
        self.assertEqual(validated["criteria"][0]["basis_kind"], "data_derived")

    def test_current_sample_cannot_create_its_own_minimum_count_gate(self) -> None:
        req = request(
            input_refs=[
                {
                    "id": "input_a",
                    "path": "inputs/example.csv",
                    "description": "Current evaluation observations.",
                    "required": True,
                }
            ]
        )
        candidate = design(req)
        criterion = candidate["criteria"][0]
        criterion["statement"] = "评估观测不少于 3 条。"
        criterion["basis_kind"] = "data_derived"
        criterion["basis_text"] = "当前数据有 6 条评估观测，因此设置至少 3 条的门槛。"
        criterion["source_refs"] = ["input_a"]
        with self.assertRaises(ContractError) as raised:
            validate_design(candidate, req, response(req))
        self.assertEqual(
            raised.exception.error_code,
            "data_derived_sample_count_gate",
        )

    def test_method_standard_cannot_invent_numeric_cutoff(self) -> None:
        req = request()
        candidate = design(req)
        criterion = candidate["criteria"][0]
        criterion["statement"] = "The residual must be below 0.5."
        criterion["basis_kind"] = "method_standard"
        criterion["basis_text"] = (
            "A 0.5 residual is described as a reasonable method-standard tolerance."
        )
        criterion["source_refs"] = candidate["input_ids"]
        with self.assertRaises(ContractError) as raised:
            validate_design(candidate, req, response(req))
        self.assertEqual(
            raised.exception.error_code,
            "numeric_cutoff_method_standard_ungrounded",
        )

    def test_numeric_cutoff_must_reference_a_supplied_input(self) -> None:
        req = request(
            input_refs=[
                {
                    "id": "input_a",
                    "path": "inputs/example.csv",
                    "description": "Supplied data source.",
                    "required": True,
                }
            ]
        )
        candidate = design(req)
        criterion = candidate["criteria"][0]
        criterion["statement"] = "The residual must be below 0.5."
        criterion["basis_kind"] = "data_derived"
        criterion["basis_text"] = (
            "The 0.5 cutoff is derived from an unspecified data source."
        )
        criterion["source_refs"] = ["invented_source"]
        with self.assertRaises(ContractError) as raised:
            validate_design(candidate, req, response(req))
        self.assertEqual(
            raised.exception.error_code,
            "numeric_cutoff_source_not_supplied",
        )

    def test_sensitivity_criterion_uses_reader_facing_measurement_roles(self) -> None:
        req = request()
        candidate = design(req)
        candidate["criteria"][0]["statement"] = (
            "The sensitivity comparison reports both conditions and their difference."
        )
        set_planned_measurements(
            candidate,
            ["opaque_value_c"],
            {
                "opaque_value_c": (
                    "Difference between quality-screening conditions",
                    "The difference between estimates obtained with and without the flagged observation.",
                )
            },
        )
        with self.assertRaises(ContractError) as raised:
            validate_design(candidate, req, response(req))
        self.assertEqual(
            raised.exception.error_code,
            "sensitivity_criterion_incomplete",
        )

        set_planned_measurements(
            candidate,
            [
                "opaque_value_a",
                "opaque_value_b",
                "opaque_value_c",
            ],
            {
                "opaque_value_a": (
                    "Estimate with all observations",
                    "Calibration improvement estimated from all available observations.",
                ),
                "opaque_value_b": (
                    "Estimate after quality screening",
                    "Calibration improvement estimated after excluding the flagged observation.",
                ),
                "opaque_value_c": (
                    "Difference between quality-screening conditions",
                    "The difference between estimates obtained with and without the flagged observation.",
                ),
            },
        )
        validated = validate_design(candidate, req, response(req))
        self.assertEqual(len(validated["measurement_plan"]), 3)

    def test_two_condition_difference_promise_requires_a_delta_measurement(
        self,
    ) -> None:
        req = request()
        candidate = design(req)
        set_planned_measurements(
            candidate,
            ["condition_a_estimate", "condition_b_estimate"],
            {
                "condition_a_estimate": (
                    "Estimate under condition A",
                    "The estimate obtained under the first fitting condition.",
                ),
                "condition_b_estimate": (
                    "Estimate under condition B",
                    "The estimate obtained under the second fitting condition.",
                ),
            },
        )
        candidate["criteria"][0].update(
            {
                "id": "condition_comparison",
                "statement": "Report both fitting conditions and their difference.",
            }
        )
        with self.assertRaises(ContractError) as raised:
            validate_design(candidate, req, response(req))
        self.assertEqual(
            raised.exception.error_code,
            "sensitivity_criterion_incomplete",
        )

    def test_answer_bearing_numeric_typed_result_must_be_a_measurement(
        self,
    ) -> None:
        req = request()
        candidate = design(req)
        candidate["result_plan"] = [
            {
                "id": "condition_difference",
                "display_name": "Condition difference",
                "value_kind": "number",
                "role": "primary",
                "unit": "dimensionless",
                "scientific_meaning": "The numerical difference between conditions.",
            }
        ]
        with self.assertRaises(ContractError) as raised:
            validate_design(candidate, req, response(req))
        self.assertEqual(
            raised.exception.error_code,
            "numeric_result_must_be_measurement",
        )

    def test_unreferenced_numeric_diagnostic_result_is_rejected(self) -> None:
        req = request()
        candidate = design(req)
        candidate["result_plan"] = [
            {
                "id": "unused_bias",
                "display_name": "Unused signed error",
                "value_kind": "number",
                "role": "diagnostic",
                "unit": "dimensionless",
                "scientific_meaning": "A diagnostic not used by the research question.",
            }
        ]
        with self.assertRaises(ContractError) as raised:
            validate_design(candidate, req, response(req))
        self.assertEqual(
            raised.exception.error_code,
            "planned_result_not_criterion_bound",
        )

    def test_sensitivity_roles_accept_full_and_perturbed_condition_names(self) -> None:
        req = request()
        candidate = design(req)
        names = [
            "mae_calibrated_full",
            "mae_calibrated_sensitivity",
            "sensitivity_delta_mae",
        ]
        definitions = {
            names[0]: (
                "Estimate with all observations",
                "Calibration improvement estimated from all observations.",
            ),
            names[1]: (
                "Estimate after quality screening",
                "Calibration improvement estimated after excluding the flagged observation.",
            ),
            names[2]: (
                "Difference between quality-screening conditions",
                "Difference between the two condition-specific estimates.",
            ),
        }
        set_planned_measurements(candidate, names, definitions)
        candidate["criteria"][0]["statement"] = (
            "The sensitivity comparison reports both conditions and their difference."
        )
        candidate["criteria"][0]["measurement_refs"] = names
        validated = validate_design(candidate, req, response(req))
        self.assertEqual(
            {row["name"] for row in validated["measurement_plan"]},
            set(names),
        )

        nested_names = [
            "mae_improvement_include",
            "mae_improvement_exclude",
            "sensitivity_improvement_delta",
        ]
        set_planned_measurements(
            candidate,
            nested_names,
            {
                nested_names[0]: (
                    "Estimate with all observations",
                    "Improvement estimated from all observations.",
                ),
                nested_names[1]: (
                    "Estimate after quality screening",
                    "Improvement estimated after excluding the flagged observation.",
                ),
                nested_names[2]: (
                    "Difference between quality-screening conditions",
                    "Difference between the two condition-specific estimates.",
                ),
            },
        )
        candidate["criteria"][0]["statement"] = (
            "The sensitivity comparison reports both condition improvements "
            "and their difference."
        )
        candidate["criteria"][0]["measurement_refs"] = nested_names
        nested_validated = validate_design(candidate, req, response(req))
        self.assertEqual(
            {row["name"] for row in nested_validated["measurement_plan"]},
            set(nested_names),
        )

        abstract_names = [
            "estimate_condition_a",
            "estimate_condition_b",
            "estimate_delta",
        ]
        set_planned_measurements(
            candidate,
            abstract_names,
            {
                abstract_names[0]: (
                    "Estimate with all observations",
                    "Improvement estimated from all observations.",
                ),
                abstract_names[1]: (
                    "Estimate after quality screening",
                    "Improvement estimated after excluding the flagged observation.",
                ),
                abstract_names[2]: (
                    "Difference between quality-screening conditions",
                    "Difference between the two condition-specific estimates.",
                ),
            },
        )
        candidate["criteria"][0]["statement"] = (
            "The sensitivity comparison reports condition A, condition B, "
            "and their difference."
        )
        candidate["criteria"][0]["measurement_refs"] = abstract_names
        abstract_validated = validate_design(candidate, req, response(req))
        self.assertEqual(
            {row["name"] for row in abstract_validated["measurement_plan"]},
            set(abstract_names),
        )

    def test_sensitivity_criterion_allows_multiple_parameter_units(self) -> None:
        req = request()
        candidate = design(req)
        names = [
            "slope_a",
            "slope_b",
            "slope_difference",
            "intercept_a",
            "intercept_b",
            "intercept_difference",
        ]
        set_planned_measurements(
            candidate,
            names,
            {
                "slope_a": ("Slope under condition A", "Condition A slope estimate."),
                "slope_b": ("Slope under condition B", "Condition B slope estimate."),
                "slope_difference": (
                    "Slope difference",
                    "Difference between the two slope estimates.",
                ),
                "intercept_a": (
                    "Intercept under condition A",
                    "Condition A intercept estimate.",
                ),
                "intercept_b": (
                    "Intercept under condition B",
                    "Condition B intercept estimate.",
                ),
                "intercept_difference": (
                    "Intercept difference",
                    "Difference between the two intercept estimates.",
                ),
            },
        )
        for row in candidate["measurement_plan"]:
            row["unit"] = "" if row["name"].startswith("slope") else "G"
        candidate["criteria"][0].update(
            {
                "id": "sensitivity_parameters",
                "statement": (
                    "Report both fitting conditions and the slope and intercept differences."
                ),
                "measurement_refs": names,
            }
        )
        candidate["experiment_stages"][0]["criterion_refs"] = [
            "sensitivity_parameters"
        ]
        validated = validate_design(candidate, req, response(req))
        self.assertEqual(len(validated["measurement_plan"]), 6)

    def test_sensitivity_delta_identifier_is_recognized_as_a_contrast(
        self,
    ) -> None:
        conditions, contrasts = _sensitivity_criterion_roles(
            [
                "slope_all",
                "slope_exclude",
                "slope_delta_exclude_minus_all",
            ],
            {
                "slope_all": {
                    "display_name": "Slope with all observations",
                    "scientific_meaning": "Slope under the first fitting condition.",
                },
                "slope_exclude": {
                    "display_name": "Slope after exclusion",
                    "scientific_meaning": "Slope under the second fitting condition.",
                },
                "slope_delta_exclude_minus_all": {
                    "display_name": "Slope change (excluded minus included)",
                    "scientific_meaning": "Change between the fitting conditions.",
                },
            },
        )
        self.assertEqual(
            conditions,
            {"slope_all", "slope_exclude"},
        )
        self.assertEqual(
            contrasts,
            {"slope_delta_exclude_minus_all"},
        )

    def test_mse_cannot_mean_mean_signed_error(self) -> None:
        req = request()
        candidate = design(req)
        set_planned_measurements(candidate, ["mse_uncalibrated_full"])
        candidate["measurement_plan"][0]["display_name"] = "未校准 MSE"
        candidate["measurement_plan"][0]["scientific_meaning"] = (
            "候选读数减参考读数的平均有符号误差"
        )
        with self.assertRaises(ContractError) as raised:
            validate_design(candidate, req, response(req))
        self.assertEqual(
            raised.exception.error_code,
            "metric_abbreviation_conflict",
        )

    def test_reader_method_rejects_raw_column_identifiers(self) -> None:
        req = request()
        candidate = design(req)
        candidate["experiment_stages"][0]["method_outline"] = (
            "拟合 hmi_candidate_g 到 wso_reference_g 的线性关系。"
        )
        with self.assertRaises(ContractError) as raised:
            validate_design(candidate, req, response(req))
        self.assertEqual(
            raised.exception.error_code,
            "reader_method_exposes_raw_fields",
        )

    def test_reader_measurement_definition_rejects_raw_column_identifiers(
        self,
    ) -> None:
        req = request()
        candidate = design(req)
        candidate["measurement_plan"][0]["scientific_meaning"] = (
            "排除 suspect_geometry 后得到的平均值。"
        )
        with self.assertRaises(ContractError) as raised:
            validate_design(candidate, req, response(req))
        self.assertEqual(
            raised.exception.error_code,
            "reader_definition_exposes_raw_fields",
        )

    def test_metric_support_can_use_reader_facing_scientific_definition(self) -> None:
        self.assertIsNone(
            _unsupported_reader_metric(
                ["当前数据的平均有符号误差下降。"],
                {"候选读数减参考读数的平均有符号误差"},
            )
        )

    def test_measurement_definition_cannot_equate_nonzero_with_substantive_impact(
        self,
    ) -> None:
        req = request()
        res = response(req)
        candidate = design(req)
        candidate["measurement_plan"][0]["scientific_meaning"] = (
            "两种条件下估计量之差；非零值表示质量标记对结果有实质影响。"
        )
        with self.assertRaises(ContractError) as raised:
            validate_design(candidate, req, res)
        self.assertEqual(
            raised.exception.error_code,
            "ungrounded_substantive_impact_definition",
        )

    def test_sensitivity_criterion_must_reference_conditions_and_delta(self) -> None:
        req = request()
        candidate = design(req)
        names = [
            "calibrated_mae_all_rows",
            "calibrated_mae_excl_flagged",
            "calibrated_mae_sensitivity",
        ]
        set_planned_measurements(
            candidate,
            names,
            {
                names[0]: (
                    "Estimate with all observations",
                    "Calibration improvement estimated from all observations.",
                ),
                names[1]: (
                    "Estimate after quality screening",
                    "Calibration improvement estimated after excluding the flagged observation.",
                ),
                names[2]: (
                    "Difference between quality-screening conditions",
                    "Difference between the two condition-specific estimates.",
                ),
            },
        )
        candidate["criteria"][0]["statement"] = (
            "The sensitivity comparison retains the same direction."
        )
        candidate["criteria"][0]["measurement_refs"] = [
            "calibrated_mae_sensitivity"
        ]
        with self.assertRaises(ContractError) as raised:
            validate_design(candidate, req, response(req))
        self.assertEqual(
            raised.exception.error_code,
            "sensitivity_criterion_incomplete",
        )

        candidate["criteria"][0]["measurement_refs"] = names
        validated = validate_design(candidate, req, response(req))
        self.assertEqual(
            validated["criteria"][0]["measurement_refs"],
            names,
        )

    def test_paired_comparison_may_split_estimates_and_difference_across_criteria(
        self,
    ) -> None:
        audit = {
            "baseline_measurement": "mae_include",
            "candidate_measurement": "mae_exclude",
            "delta_measurement": "delta_mae",
        }
        conditions, deltas = _linked_sensitivity_roles(
            set(),
            {"delta_mae"},
            {"mae_include", "mae_exclude", "delta_mae"},
            [audit],
        )
        self.assertEqual(conditions, {"mae_include", "mae_exclude"})
        self.assertEqual(deltas, {"delta_mae"})

        incomplete_conditions, incomplete_deltas = _linked_sensitivity_roles(
            set(),
            {"delta_mae"},
            {"mae_include", "delta_mae"},
            [audit],
        )
        self.assertEqual(incomplete_conditions, set())
        self.assertEqual(incomplete_deltas, {"delta_mae"})

    def test_reader_slope_definition_follows_declared_model_direction(self) -> None:
        rows = [
            {
                "name": "slope_include",
                "display_name": "保留条件的校正斜率",
                "scientific_meaning": "候选读数关于参考读数的最小二乘回归斜率",
            }
        ]
        audits = [
            {
                "candidate_model_input_columns": ["candidate_measurement"],
                "candidate_model_target_column": "reference_measurement",
            }
        ]
        self.assertEqual(
            _reader_model_direction_conflict(rows, audits),
            "slope_include",
        )
        rows[0]["scientific_meaning"] = (
            "参考读数相对于候选读数的最小二乘回归斜率"
        )
        self.assertIsNone(_reader_model_direction_conflict(rows, audits))

    def test_explicit_parameter_difference_request_requires_each_planned_delta(
        self,
    ) -> None:
        task = "报告校正参数、两种条件估计及其差异。"
        rows = [
            {"display_name": "保留条件校正斜率", "scientific_meaning": ""},
            {"display_name": "排除条件校正斜率", "scientific_meaning": ""},
            {"display_name": "保留条件校正截距", "scientific_meaning": ""},
            {"display_name": "排除条件校正截距", "scientific_meaning": ""},
            {"display_name": "斜率差值", "scientific_meaning": "排除减保留"},
        ]
        self.assertEqual(
            _missing_requested_parameter_differences(task, rows),
            ["截距"],
        )
        rows.append(
            {
                "display_name": "截距差值",
                "scientific_meaning": "排除减保留",
            }
        )
        self.assertEqual(
            _missing_requested_parameter_differences(task, rows),
            [],
        )
        self.assertEqual(
            _missing_requested_parameter_differences(
                "只分别报告两种条件的参数。",
                rows[:-1],
            ),
            [],
        )
        self.assertEqual(
            _missing_requested_parameter_differences(
                "同时报告包含与排除标记观测时各自的估计量及二者差值。",
                rows[:-1],
            ),
            ["截距"],
        )

    def test_two_condition_completeness_does_not_force_a_difference(self) -> None:
        req = request()
        candidate = design(req)
        names = ["estimate_condition_a", "estimate_condition_b"]
        set_planned_measurements(
            candidate,
            names,
            {
                names[0]: (
                    "Estimate under condition A",
                    "The finite estimate obtained under the first fitting condition.",
                ),
                names[1]: (
                    "Estimate under condition B",
                    "The finite estimate obtained under the second fitting condition.",
                ),
            },
        )
        candidate["criteria"][0]["statement"] = (
            "Both fitting conditions produce finite estimates."
        )
        candidate["criteria"][0]["measurement_refs"] = names
        validated = validate_design(candidate, req, response(req))
        self.assertEqual(validated["criteria"][0]["measurement_refs"], names)

    def test_interpretation_policy_rejects_ungrounded_numeric_threshold(self) -> None:
        req = request("unit_interpretation_threshold")
        candidate = design(req)
        candidate["interpretation_policy"]["uncertainty_rule"] = (
            "When evaluation has fewer than 10 rows, force high uncertainty."
        )
        with self.assertRaises(ContractError) as raised:
            validate_design(candidate, req, response(req))
        self.assertEqual(
            raised.exception.error_code,
            "ungrounded_interpretation_threshold",
        )
        self.assertEqual(
            raised.exception.field_path,
            "design.interpretation_policy.uncertainty_rule",
        )

    def test_interpretation_policy_rejects_unicode_nonzero_threshold(self) -> None:
        req = request("unit_interpretation_unicode_threshold")
        candidate = design(req)
        candidate["interpretation_policy"]["uncertainty_rule"] = (
            "当评价行数 ≤ 10 时，强制判为高不确定性。"
        )
        with self.assertRaises(ContractError) as raised:
            validate_design(candidate, req, response(req))
        self.assertEqual(
            raised.exception.error_code,
            "ungrounded_interpretation_threshold",
        )

    def test_interpretation_policy_rejects_worded_fraction_threshold(self) -> None:
        req = request("unit_interpretation_fraction_threshold")
        candidate = design(req)
        candidate["interpretation_policy"]["uncertainty_rule"] = (
            "敏感性差值超过主估计绝对值一半时标记为不稳定。"
        )
        with self.assertRaises(ContractError) as raised:
            validate_design(candidate, req, response(req))
        self.assertEqual(
            raised.exception.error_code,
            "ungrounded_interpretation_threshold",
        )

    def test_interpretation_policy_allows_declared_zero_direction_boundary(self) -> None:
        req = request("unit_interpretation_zero_direction")
        candidate = design(req)
        set_planned_measurements(candidate, ["holdout_mae_improvement"])
        candidate["criteria"][0]["statement"] = (
            "The holdout MAE improvement is evaluated by its declared direction."
        )
        candidate["interpretation_policy"]["null_rule"] = (
            "When the declared MAE improvement is less than or equal to 0, "
            "the directional criterion is not met."
        )
        validated = validate_design(candidate, req, response(req))
        self.assertIn(
            "less than or equal to 0",
            validated["interpretation_policy"]["null_rule"],
        )

    def test_response_must_preserve_exact_task(self) -> None:
        req = request()
        candidate = response(req)
        candidate["task"] += " changed"
        with self.assertRaisesRegex(ContractError, "exactly match"):
            validate_response(candidate, req)

    def test_ready_response_cannot_contain_blocker(self) -> None:
        req = request()
        candidate = response(req)
        candidate["blockers"] = ["Hidden blocker"]
        with self.assertRaises(ContractError):
            validate_response(candidate, req)

    def test_design_rejects_unknown_input(self) -> None:
        req = request()
        candidate = design(req)
        candidate["input_ids"] = ["unknown"]
        with self.assertRaisesRegex(ContractError, "unknown request inputs"):
            validate_design(candidate, req, response(req))

    def test_design_reserves_worker_protocol_result_path(self) -> None:
        req = request()
        candidate = design(req)
        candidate["artifact_plan"][0]["path"] = "result.json"
        candidate["experiment_stages"][0]["execution"]["expected_artifacts"] = [
            "result.json"
        ]
        with self.assertRaisesRegex(ContractError, "reserved result.json"):
            validate_design(candidate, req, response(req))

    def test_design_reserves_host_generated_report_path(self) -> None:
        req = request()
        candidate = design(req)
        candidate["artifact_plan"][0]["path"] = "report.md"
        candidate["experiment_stages"][0]["execution"]["expected_artifacts"] = [
            "report.md"
        ]
        with self.assertRaisesRegex(ContractError, "reserved report.md"):
            validate_design(candidate, req, response(req))

    def test_design_rejects_redundant_output_root_prefix(self) -> None:
        req = request("unit_output_prefix")
        candidate = design(req)
        candidate["artifact_plan"][0]["path"] = "outputs/summary.json"
        candidate["experiment_stages"][0]["execution"]["expected_artifacts"] = [
            "outputs/summary.json"
        ]
        with self.assertRaisesRegex(ContractError, "already relative"):
            validate_design(candidate, req, response(req))

    def test_design_requires_one_evidence_role_per_used_input(self) -> None:
        req = request(
            input_refs=[
                {
                    "id": "table",
                    "path": "inputs/example_mean.csv",
                    "description": "Verified table.",
                    "required": True,
                }
            ]
        )
        candidate = design(req)
        candidate["research_frame"]["input_evidence"] = []
        with self.assertRaisesRegex(ContractError, "must contain 1 to 1 items"):
            validate_design(candidate, req, response(req))

    def test_design_criterion_requires_machine_evidence_reference(self) -> None:
        req = request()
        candidate = design(req)
        candidate["criteria"][0]["measurement_refs"] = []
        candidate["criteria"][0]["endpoint_refs"] = []
        with self.assertRaisesRegex(
            ContractError,
            "at least one planned measurement, typed result, or endpoint",
        ):
            validate_design(candidate, req, response(req))

    def test_design_reader_text_rejects_unplanned_named_metric(self) -> None:
        req = request()
        candidate = design(req)
        candidate["research_frame"]["supported_questions"] = [
            "描述均方误差和平均有符号误差。"
        ]
        with self.assertRaisesRegex(ContractError, "unplanned metric"):
            validate_design(candidate, req, response(req))

    def test_design_reader_text_allows_named_planned_metric(self) -> None:
        req = request()
        candidate = design(req)
        candidate["research_frame"]["supported_questions"] = [
            "报告计划的平均绝对误差。"
        ]
        set_planned_measurements(candidate, ["holdout_mae"])
        validated = validate_design(candidate, req, response(req))
        self.assertEqual(
            validated["criteria"][0]["measurement_refs"],
            ["holdout_mae"],
        )

    def test_design_rejects_unrequested_p_value_route(self) -> None:
        req = request(task="只计算两列数据的 Pearson 相关系数和有效样本数。")
        candidate = design(req)
        candidate["measurement_plan"].append(
            {
                "name": "p_value",
                "display_name": "p 值",
                "role": "secondary",
                "unit": "",
                "scientific_meaning": "零相关假设下的尾部概率。",
            }
        )
        candidate["criteria"][0]["measurement_refs"].append("p_value")
        candidate["experiment_stages"][0]["measurement_refs"].append("p_value")
        with self.assertRaisesRegex(ContractError, "unrequested p-value"):
            validate_design(candidate, req, response(req))

        requested = request(
            task="Compute the Pearson correlation coefficient and two-sided p-value."
        )
        allowed = design(requested)
        allowed["measurement_plan"].append(
            {
                "name": "p_value",
                "display_name": "p 值",
                "role": "secondary",
                "unit": "",
                "scientific_meaning": "零相关假设下的尾部概率。",
            }
        )
        allowed["criteria"][0]["measurement_refs"].append("p_value")
        allowed["experiment_stages"][0]["measurement_refs"].append("p_value")
        validate_design(allowed, requested, response(requested))

        requested_with_threshold = request(
            task=(
                "Compute Pearson and Spearman correlations; require both "
                "two-sided p < 0.05."
            )
        )
        allowed_with_threshold = design(requested_with_threshold)
        allowed_with_threshold["measurement_plan"].append(
            {
                "name": "p_value",
                "display_name": "p 值",
                "role": "secondary",
                "unit": "",
                "scientific_meaning": "零相关假设下的尾部概率。",
            }
        )
        allowed_with_threshold["criteria"][0]["measurement_refs"].append(
            "p_value"
        )
        allowed_with_threshold["experiment_stages"][0][
            "measurement_refs"
        ].append("p_value")
        validate_design(
            allowed_with_threshold,
            requested_with_threshold,
            response(requested_with_threshold),
        )

    def test_design_rejects_unrequested_r_squared_diagnostic(self) -> None:
        req = request(task="Compare the requested calibration errors.")
        candidate = design(req)
        candidate["result_plan"] = [
            {
                "id": "fit_r_squared",
                "display_name": "校准拟合决定系数",
                "value_kind": "number",
                "role": "diagnostic",
                "unit": "",
                "scientific_meaning": "当前拟合集上线性校准的决定系数。",
            }
        ]
        candidate["criteria"][0]["result_refs"] = ["fit_r_squared"]
        candidate["experiment_stages"][0]["result_refs"] = ["fit_r_squared"]
        with self.assertRaises(ContractError) as raised:
            validate_design(candidate, req, response(req))
        self.assertEqual(
            raised.exception.error_code,
            "unrequested_fit_quality_diagnostic",
        )

        requested = request(task="Calculate the calibration R-squared value.")
        allowed = design(requested)
        allowed["result_plan"] = candidate["result_plan"]
        allowed["criteria"][0]["result_refs"] = ["fit_r_squared"]
        allowed["experiment_stages"][0]["result_refs"] = ["fit_r_squared"]
        validated = validate_design(allowed, requested, response(requested))
        self.assertEqual(validated["result_plan"][0]["id"], "fit_r_squared")

    def test_design_rejects_ambiguous_flagged_retention_language(self) -> None:
        req = request()
        candidate = design(req)
        candidate["measurement_plan"][0]["display_name"] = (
            "仅保留标记观测拟合后的平均值"
        )
        with self.assertRaises(ContractError) as raised:
            validate_design(candidate, req, response(req))
        self.assertEqual(
            raised.exception.error_code,
            "ambiguous_quality_flag_language",
        )

    def test_assessment_rejects_numbers_not_in_verified_results(self) -> None:
        req = request(task="Compute the deterministic mean of the supplied values.")
        des = design(req)
        worker = simple_worker_result()
        proposed = assessment()
        proposed["rationale"] = "The verified mean is 5.0."
        proposed["criterion_results"][0]["explanation"] = (
            "The mean is 5.0 and the endpoint completed."
        )
        proposed["report_narrative"]["interpretation"] = (
            "The supplied values have a mean of 5.0 and a maximum of 9.0."
        )
        with self.assertRaisesRegex(ContractError, "quantitative claims"):
            validate_scientific_assessment(
                proposed,
                des,
                worker,
                task_text=req["task"],
            )

        proposed = assessment()
        proposed["rationale"] = "The verified mean is 2.5."
        proposed["criterion_results"][0]["explanation"] = (
            "The mean is 2.5 and the endpoint completed."
        )
        proposed["report_narrative"]["interpretation"] = (
            "The verified deterministic mean is 2.5."
        )
        validate_scientific_assessment(
            proposed,
            des,
            worker,
            task_text=req["task"],
        )

        worker["measurements"][0]["value"] = -0.00944594209682803
        worker["scientific_payload"]["estimate"] = -0.00944594209682803
        proposed = assessment()
        proposed["rationale"] = "The rounded estimate is −0.009."
        proposed["criterion_results"][0]["explanation"] = (
            "The rounded estimate is −0.009 and the endpoint completed."
        )
        proposed["report_narrative"]["interpretation"] = (
            "The verified estimate is −0.009 for this supplied input."
        )
        validate_scientific_assessment(
            proposed,
            des,
            worker,
            task_text=req["task"],
        )

        worker["measurements"][0]["value"] = 2.5
        worker["scientific_payload"]["estimate"] = 2.5
        proposed = assessment()
        proposed["rationale"] = (
            "The result contains 1 verified measurement and 1 completed endpoint."
        )
        proposed["criterion_results"][0]["explanation"] = (
            "The verified mean is 2.5 and the endpoint completed."
        )
        proposed["report_narrative"]["interpretation"] = (
            "The verified deterministic mean is 2.5."
        )
        validate_scientific_assessment(
            proposed,
            des,
            worker,
            task_text=req["task"],
        )

        des["measurement_plan"][0].update(
            {
                "name": "condition_difference",
                "display_name": "两种条件的估计差值",
                "scientific_meaning": "条件 B 减去条件 A 的估计差值。",
            }
        )
        des["criteria"][0]["measurement_refs"] = ["condition_difference"]
        des["experiment_stages"][0]["measurement_refs"] = [
            "condition_difference"
        ]
        worker["measurements"][0].update(
            {
                "name": "condition_difference",
                "value": -0.02655777564872536,
            }
        )
        worker["scientific_payload"]["estimate"] = -0.02655777564872536
        proposed = assessment()
        proposed["rationale"] = "Condition B is lower by 0.0266."
        proposed["criterion_results"][0]["explanation"] = (
            "Condition B is lower than condition A by 0.0266."
        )
        proposed["report_narrative"]["interpretation"] = (
            "条件 B 比条件 A 低 0.0266。"
        )
        validate_scientific_assessment(
            proposed,
            des,
            worker,
            task_text=req["task"],
        )

        worker["measurements"][0]["value"] = -0.403241
        worker["scientific_payload"]["estimate"] = -0.403241
        proposed = assessment()
        proposed["rationale"] = "The verified estimate is −0.403."
        proposed["criterion_results"][0]["explanation"] = (
            "The rounded estimate is −0.403 and the endpoint completed."
        )
        proposed["report_narrative"]["interpretation"] = (
            "The verified estimate is −0.403 for this supplied input."
        )
        validate_scientific_assessment(
            proposed,
            des,
            worker,
            task_text=req["task"],
        )

    def test_assessment_accepts_numbers_from_hash_verified_input_basis(self) -> None:
        req = request(task="Compute the deterministic mean of the supplied values.")
        des = design(req)
        worker = simple_worker_result()
        proposed = assessment()
        proposed["rationale"] = "The verified mean is (1+2+3+4)/4 = 2.5."
        proposed["criterion_results"][0]["explanation"] = (
            "The four immutable input values 1, 2, 3, and 4 yield the verified mean 2.5."
        )
        proposed["report_narrative"]["interpretation"] = (
            "The immutable values 1, 2, 3, and 4 have a verified mean of 2.5."
        )
        with self.assertRaisesRegex(ContractError, "quantitative claims"):
            validate_scientific_assessment(
                proposed,
                des,
                worker,
                task_text=req["task"],
            )
        validate_scientific_assessment(
            proposed,
            des,
            worker,
            task_text=req["task"],
            evidence_basis_texts=["value\n1\n2\n3\n4\n"],
        )

    def test_descriptive_assessment_rejects_an_invented_interval(self) -> None:
        req = request(task="Describe the deterministic mean of the supplied values.")
        des = design(req)
        worker = simple_worker_result()
        worker["scientific_payload"]["interval"] = [2.45, 2.55]
        proposed = assessment()
        with self.assertRaises(ContractError) as raised:
            validate_scientific_assessment(
                proposed,
                des,
                worker,
                task_text=req["task"],
            )
        self.assertEqual(raised.exception.error_code, "unsupported_interval_basis")

    def test_declared_interval_survives_a_subset_no_interval_limit(self) -> None:
        req = request(task="Estimate a mean with a predeclared bootstrap interval.")
        des = design(req)
        des["research_frame"]["analysis_mode"] = "Inferential bootstrap analysis."
        des["method_decisions"][0]["decision"] = (
            "Use a reproducible bootstrap confidence interval for the primary estimate."
        )
        des["method_decisions"][0]["claim_limit"] = (
            "A small descriptive subset has no interval and is not used for inference."
        )
        worker = simple_worker_result()
        worker["scientific_payload"]["interval"] = [2.45, 2.55]

        validate_scientific_assessment(
            assessment(),
            des,
            worker,
            task_text=req["task"],
        )

    def test_completed_answer_allows_no_forced_limitations_or_next_steps(self) -> None:
        req = request(task="Compute the deterministic mean of the supplied values.")
        proposed = assessment()
        proposed["report_narrative"]["limitations"] = []
        proposed["report_narrative"]["next_steps"] = []
        validated = validate_scientific_assessment(
            proposed,
            design(req),
            simple_worker_result(),
            task_text=req["task"],
        )
        self.assertEqual(validated["report_narrative"]["limitations"], [])
        self.assertEqual(validated["report_narrative"]["next_steps"], [])

    def test_design_reader_text_rejects_internal_contract_terms(self) -> None:
        req = request()
        candidate = design(req)
        candidate["criteria"][0]["basis_text"] = (
            "The paired_comparison_audits contract will determine the result."
        )
        with self.assertRaises(ContractError) as raised:
            validate_design(candidate, req, response(req))
        self.assertEqual(raised.exception.error_code, "reader_internal_term")
        self.assertEqual(
            raised.exception.field_path,
            "design.criteria[0].basis_text",
        )
        self.assertIn("paired_comparison_audits", raised.exception.suggestion)

    def test_request_reader_text_reports_exact_internal_term_location(self) -> None:
        req = request()
        req["success_criteria"] = [
            {
                "id": "mean_result",
                "statement": "The artifact id must identify the scientific evidence.",
                "basis_kind": "method_standard",
                "basis_text": "The calculation should preserve its evidence.",
                "source_refs": [],
                "artifact_refs": [],
            }
        ]
        with self.assertRaises(ContractError) as raised:
            validate_request(req)
        self.assertEqual(raised.exception.error_code, "reader_internal_term")
        self.assertEqual(
            raised.exception.field_path,
            "request.success_criteria[0].statement",
        )
        self.assertIn("artifact id", raised.exception.suggestion)

    def test_calibrated_measurements_require_paired_comparison_audit(self) -> None:
        req = request(
            input_refs=[
                {
                    "id": "paired_data",
                    "path": "inputs/upstream_handoff_demo/polar_overlap_features.csv",
                    "description": "Paired calibration fixture.",
                    "required": True,
                }
            ]
        )
        candidate = design(req)
        set_planned_measurements(
            candidate,
            [
                "holdout_raw_mae",
                "holdout_calibrated_mae",
                "holdout_mae_improvement",
            ],
        )
        with self.assertRaisesRegex(
            ContractError,
            "require a trusted paired comparison audit",
        ):
            validate_design(candidate, req, response(req))

    def test_reader_described_calibration_pair_requires_audit(self) -> None:
        req = request()
        candidate = design(req)
        set_planned_measurements(
            candidate,
            ["opaque_a", "opaque_b"],
            {
                "opaque_a": (
                    "包含全部观测时的未校准平均绝对误差",
                    "当前评价行在校准前的平均绝对误差。",
                ),
                "opaque_b": (
                    "包含全部观测时的校准后平均绝对误差",
                    "同一批评价行经过校准后的平均绝对误差。",
                ),
            },
        )
        with self.assertRaisesRegex(
            ContractError,
            "require a trusted paired comparison audit",
        ):
            validate_design(candidate, req, response(req))

    def test_calibration_pair_with_different_condition_tags_requires_audit(
        self,
    ) -> None:
        req = request()
        candidate = design(req)
        set_planned_measurements(
            candidate,
            [
                "mae_raw_eval",
                "mae_cal_full_eval",
                "mae_cal_excl_eval",
            ],
            {
                "mae_raw_eval": (
                    "评估集原始读数平均绝对误差",
                    "未经校正的候选读数在留出观测上的平均绝对偏差。",
                ),
                "mae_cal_full_eval": (
                    "评估集全条件校准平均绝对误差",
                    "全拟合集校准后在留出观测上的平均绝对偏差。",
                ),
                "mae_cal_excl_eval": (
                    "评估集排除条件校准平均绝对误差",
                    "排除标记观测后重新校准，在相同留出观测上的平均绝对偏差。",
                ),
            },
        )
        with self.assertRaisesRegex(
            ContractError,
            "require a trusted paired comparison audit",
        ):
            validate_design(candidate, req, response(req))

    def test_paired_comparison_audit_binds_source_and_row_evidence(self) -> None:
        req = request(
            input_refs=[
                {
                    "id": "paired_data",
                    "path": "inputs/upstream_handoff_demo/polar_overlap_features.csv",
                    "description": "Paired calibration fixture.",
                    "required": True,
                }
            ]
        )
        candidate = design(req)
        set_planned_measurements(
            candidate,
            [
                "holdout_raw_mae",
                "holdout_calibrated_mae",
                "holdout_mae_improvement",
            ],
        )
        candidate["criteria"][0]["artifact_refs"] = ["row_level_results.csv"]
        candidate["artifact_plan"][0].update(
            {"path": "row_level_results.csv", "kind": "csv"}
        )
        candidate["experiment_stages"][0]["execution"]["expected_artifacts"] = [
            "row_level_results.csv"
        ]
        candidate["paired_comparison_audits"] = [
            {
                "id": "holdout_calibration",
                "comparison_kind": "source_baseline_vs_candidate",
                "evaluation_scope": "最后六个时间有序留出行",
                "source_input_id": "paired_data",
                "source_row_id_column": "date",
                "source_target_column": "hmi_candidate_g",
                "source_baseline_column": "wso_reference_g",
                "candidate_model_input_columns": ["wso_reference_g"],
                "candidate_model_target_column": "hmi_candidate_g",
                "baseline_model_input_columns": [],
                "baseline_model_target_column": None,
                "baseline_fit_condition": None,
                "candidate_fit_condition": "使用前序行拟合候选校准模型",
                "fit_evaluation_relation": "disjoint_rows",
                "evaluation_target_usage": "metrics_and_evidence_only",
                "evidence_artifact": "row_level_results.csv",
                "evidence_row_id_column": "date",
                "evidence_target_column": "target_hmi_g",
                "evidence_baseline_column": "raw_wso_g",
                "evidence_candidate_column": "calibrated_hmi_g",
                "metric": "mae",
                "baseline_measurement": "holdout_raw_mae",
                "candidate_measurement": "holdout_calibrated_mae",
                "delta_measurement": "holdout_mae_improvement",
                "delta_formula": "baseline_minus_candidate",
            }
        ]
        validated = validate_design(candidate, req, response(req))
        self.assertEqual(
            validated["paired_comparison_audits"][0]["source_target_column"],
            "hmi_candidate_g",
        )

        raw_baseline_as_fitted_candidate = json.loads(json.dumps(candidate))
        raw_candidate_audit = raw_baseline_as_fitted_candidate[
            "paired_comparison_audits"
        ][0]
        raw_candidate_audit["comparison_kind"] = "candidate_vs_candidate"
        raw_candidate_audit["baseline_model_input_columns"] = [
            "wso_reference_g"
        ]
        raw_candidate_audit["baseline_model_target_column"] = "hmi_candidate_g"
        raw_candidate_audit["baseline_fit_condition"] = (
            "使用前序行拟合所谓基准模型"
        )
        with self.assertRaises(ContractError) as raised:
            validate_design(
                raw_baseline_as_fitted_candidate,
                req,
                response(req),
            )
        self.assertEqual(
            raised.exception.error_code,
            "raw_baseline_must_use_source_comparison",
        )

        shared_condition = json.loads(json.dumps(candidate))
        shared_audit = shared_condition["paired_comparison_audits"][0]
        shared_audit["baseline_measurement"] = "excluded_holdout_raw_mae"
        shared_audit["candidate_measurement"] = "excluded_holdout_calibrated_mae"
        shared_audit["delta_measurement"] = "excluded_holdout_mae_improvement"
        set_planned_measurements(
            shared_condition,
            [
                "excluded_holdout_raw_mae",
                "excluded_holdout_calibrated_mae",
                "excluded_holdout_mae_improvement",
            ],
        )
        shared_validated = validate_design(
            shared_condition,
            req,
            response(req),
        )
        self.assertEqual(
            shared_validated["paired_comparison_audits"][0]["comparison_kind"],
            "source_baseline_vs_candidate",
        )

        ambiguous_signed_error = json.loads(json.dumps(candidate))
        ambiguous_audit = ambiguous_signed_error["paired_comparison_audits"][0]
        ambiguous_audit["metric"] = "mean_signed_error"
        ambiguous_audit["baseline_measurement"] = "holdout_raw_mse"
        ambiguous_audit["candidate_measurement"] = "holdout_calibrated_mse"
        ambiguous_audit["delta_measurement"] = None
        ambiguous_audit["delta_formula"] = None
        set_planned_measurements(
            ambiguous_signed_error,
            ["holdout_raw_mse", "holdout_calibrated_mse"],
        )
        with self.assertRaisesRegex(
            ContractError,
            "measurement names must match",
        ):
            validate_design(ambiguous_signed_error, req, response(req))

        misbound = json.loads(json.dumps(candidate))
        misbound_audit = misbound["paired_comparison_audits"][0]
        misbound_audit["baseline_measurement"] = "sensitivity_excl_flag_holdout_mae"
        misbound_audit["candidate_measurement"] = "sensitivity_incl_flag_holdout_mae"
        misbound_audit["delta_measurement"] = "sensitivity_mae_difference"
        set_planned_measurements(
            misbound,
            [
                "sensitivity_excl_flag_holdout_mae",
                "sensitivity_incl_flag_holdout_mae",
                "sensitivity_mae_difference",
            ],
            {
                "sensitivity_excl_flag_holdout_mae": (
                    "MAE after excluding the flagged fitting row",
                    "Holdout MAE from the model fitted without the flagged observation.",
                ),
                "sensitivity_incl_flag_holdout_mae": (
                    "MAE including the flagged fitting row",
                    "Holdout MAE from the model fitted with the flagged observation.",
                ),
                "sensitivity_mae_difference": (
                    "Difference between fitting conditions",
                    "Holdout MAE without the flagged fitting row minus holdout MAE "
                    "with the flagged fitting row.",
                ),
            },
        )
        with self.assertRaisesRegex(
            ContractError,
            "condition-comparison measurements require candidate_vs_candidate",
        ):
            validate_design(misbound, req, response(req))

    def test_fitted_condition_difference_requires_same_row_candidate_audit(
        self,
    ) -> None:
        req = request(
            input_refs=[
                {
                    "id": "paired_data",
                    "path": "inputs/upstream_handoff_demo/polar_overlap_features.csv",
                    "description": "Paired calibration fixture.",
                    "required": True,
                }
            ]
        )
        candidate = design(req)
        names = [
            "holdout_raw_mae",
            "holdout_calibrated_mae_with_flag",
            "holdout_mae_improvement_with_flag",
            "holdout_calibrated_mae_without_flag",
            "holdout_mae_improvement_without_flag",
            "holdout_calibrated_mae_condition_difference",
        ]
        set_planned_measurements(
            candidate,
            names,
            {
                names[0]: ("Raw holdout MAE", "MAE on the fixed holdout rows."),
                names[1]: (
                    "Holdout MAE with the flagged fitting row",
                    "Holdout MAE from the model fitted with the flagged observation.",
                ),
                names[2]: (
                    "Improvement with the flagged fitting row",
                    "Raw holdout MAE minus calibrated MAE with the flagged fitting row.",
                ),
                names[3]: (
                    "Holdout MAE without the flagged fitting row",
                    "Holdout MAE from the model fitted without the flagged observation.",
                ),
                names[4]: (
                    "Improvement without the flagged fitting row",
                    "Raw holdout MAE minus calibrated MAE without the flagged fitting row.",
                ),
                names[5]: (
                    "Difference between fitted conditions",
                    "Holdout MAE without the flagged fitting row minus holdout MAE "
                    "with the flagged fitting row.",
                ),
            },
        )
        candidate["criteria"][0].update(
            {
                "statement": (
                    "Report both fitted-condition improvements and their difference."
                ),
                "measurement_refs": names,
                "artifact_refs": ["with_flag.csv", "without_flag.csv"],
            }
        )
        candidate["artifact_plan"] = [
            {
                "id": "with_flag_rows",
                "path": "with_flag.csv",
                "kind": "csv",
                "description": "Fixed holdout predictions from the full fit.",
                "producer_stage_id": "stage_summary",
            },
            {
                "id": "without_flag_rows",
                "path": "without_flag.csv",
                "kind": "csv",
                "description": "Fixed holdout predictions from the filtered fit.",
                "producer_stage_id": "stage_summary",
            },
        ]
        stage = candidate["experiment_stages"][0]
        stage["produces_artifact_ids"] = ["with_flag_rows", "without_flag_rows"]
        stage["execution"]["expected_artifacts"] = [
            "with_flag.csv",
            "without_flag.csv",
        ]
        stage["measurement_refs"] = names

        def source_audit(
            audit_id: str,
            artifact: str,
            candidate_measurement: str,
            delta_measurement: str,
            fit_condition: str,
        ) -> dict[str, object]:
            return {
                "id": audit_id,
                "comparison_kind": "source_baseline_vs_candidate",
                "evaluation_scope": "the fixed holdout rows",
                "source_input_id": "paired_data",
                "source_row_id_column": "date",
                "source_target_column": "hmi_candidate_g",
                "source_baseline_column": "wso_reference_g",
                "candidate_model_input_columns": ["wso_reference_g"],
                "candidate_model_target_column": "hmi_candidate_g",
                "baseline_model_input_columns": [],
                "baseline_model_target_column": None,
                "baseline_fit_condition": None,
                "candidate_fit_condition": fit_condition,
                "fit_evaluation_relation": "disjoint_rows",
                "evaluation_target_usage": "metrics_and_evidence_only",
                "evidence_artifact": artifact,
                "evidence_row_id_column": "date",
                "evidence_target_column": "target_hmi_g",
                "evidence_baseline_column": "raw_wso_g",
                "evidence_candidate_column": "calibrated_hmi_g",
                "metric": "mae",
                "baseline_measurement": "holdout_raw_mae",
                "candidate_measurement": candidate_measurement,
                "delta_measurement": delta_measurement,
                "delta_formula": "baseline_minus_candidate",
            }

        candidate["paired_comparison_audits"] = [
            source_audit(
                "with_flag",
                "with_flag.csv",
                names[1],
                names[2],
                "fit with the flagged observation",
            ),
            source_audit(
                "without_flag",
                "without_flag.csv",
                names[3],
                names[4],
                "fit without the flagged observation",
            ),
        ]
        with self.assertRaises(ContractError) as raised:
            validate_design(candidate, req, response(req))
        self.assertEqual(
            raised.exception.error_code,
            "sensitivity_condition_comparison_unaudited",
        )

        candidate["artifact_plan"].append(
            {
                "id": "condition_rows",
                "path": "condition_comparison.csv",
                "kind": "csv",
                "description": "Same-row predictions from both fitted conditions.",
                "producer_stage_id": "stage_summary",
            }
        )
        stage["produces_artifact_ids"].append("condition_rows")
        stage["execution"]["expected_artifacts"].append("condition_comparison.csv")
        condition_audit = source_audit(
            "condition_difference",
            "condition_comparison.csv",
            names[3],
            names[5],
            "fit without the flagged observation",
        )
        condition_audit.update(
            {
                "comparison_kind": "candidate_vs_candidate",
                "baseline_measurement": names[1],
                "candidate_measurement": names[3],
                "delta_measurement": names[5],
                "baseline_model_input_columns": ["wso_reference_g"],
                "baseline_model_target_column": "hmi_candidate_g",
                "baseline_fit_condition": "fit with the flagged observation",
                "evidence_baseline_column": "prediction_with_flag_g",
                "evidence_candidate_column": "prediction_without_flag_g",
            }
        )
        candidate["paired_comparison_audits"].append(condition_audit)
        validated = validate_design(candidate, req, response(req))
        self.assertEqual(len(validated["paired_comparison_audits"]), 3)

        duplicate_alias = json.loads(json.dumps(candidate))
        duplicate_alias["measurement_plan"].append(
            {
                "name": "holdout_calibrated_mae_full_fit_alias",
                "display_name": "Holdout MAE from the complete fit",
                "role": "secondary",
                "unit": "",
                "scientific_meaning": (
                    "Holdout MAE from the model fitted with the flagged observation."
                ),
            }
        )
        duplicate_alias["paired_comparison_audits"][2][
            "baseline_measurement"
        ] = "holdout_calibrated_mae_full_fit_alias"
        duplicate_alias["criteria"][0]["measurement_refs"].append(
            "holdout_calibrated_mae_full_fit_alias"
        )
        duplicate_alias["experiment_stages"][0]["measurement_refs"].append(
            "holdout_calibrated_mae_full_fit_alias"
        )
        with self.assertRaises(ContractError) as raised:
            validate_design(duplicate_alias, req, response(req))
        self.assertEqual(
            raised.exception.error_code,
            "duplicate_paired_measurement_alias",
        )

        ambiguous_delta = json.loads(json.dumps(candidate))
        ambiguous_plan = next(
            row
            for row in ambiguous_delta["measurement_plan"]
            if row["name"] == names[5]
        )
        ambiguous_plan["display_name"] = "Two fitted-condition holdout MAE"
        ambiguous_plan["scientific_meaning"] = (
            "The mean absolute difference between the two sets of predictions."
        )
        with self.assertRaises(ContractError) as raised:
            validate_design(ambiguous_delta, req, response(req))
        self.assertEqual(
            raised.exception.error_code,
            "delta_measurement_semantics_incomplete",
        )

        mismatched_scope = json.loads(json.dumps(candidate))
        mismatched_scope["paired_comparison_audits"][1]["evaluation_scope"] = (
            "a differently described holdout subset"
        )
        with self.assertRaises(ContractError) as raised:
            validate_design(mismatched_scope, req, response(req))
        self.assertEqual(
            raised.exception.error_code,
            "paired_evaluation_scope_mismatch",
        )

        wrong_sign = json.loads(json.dumps(candidate))
        delta_plan = next(
            row
            for row in wrong_sign["measurement_plan"]
            if row["name"] == names[5]
        )
        delta_plan["scientific_meaning"] = (
            "全条件平均绝对误差减去排除条件平均绝对误差；"
            "正值表示排除后恶化，负值表示排除后改善。"
        )
        with self.assertRaises(ContractError) as raised:
            validate_design(wrong_sign, req, response(req))
        self.assertEqual(
            raised.exception.error_code,
            "delta_direction_interpretation_conflict",
        )

    def test_design_rejects_reversed_candidate_model_direction(self) -> None:
        req = request(
            input_refs=[
                {
                    "id": "paired_data",
                    "path": "inputs/upstream_handoff_demo/polar_overlap_features.csv",
                    "description": "Paired calibration fixture.",
                    "required": True,
                }
            ]
        )
        candidate = design(req)
        set_planned_measurements(
            candidate,
            [
                "holdout_raw_mae",
                "holdout_calibrated_mae",
                "holdout_mae_improvement",
            ],
        )
        candidate["criteria"][0]["artifact_refs"] = ["row_level_results.csv"]
        candidate["artifact_plan"][0].update(
            {"path": "row_level_results.csv", "kind": "csv"}
        )
        candidate["experiment_stages"][0]["execution"]["expected_artifacts"] = [
            "row_level_results.csv"
        ]
        candidate["paired_comparison_audits"] = [
            {
                "id": "holdout_calibration",
                "comparison_kind": "source_baseline_vs_candidate",
                "evaluation_scope": "最后六个时间有序留出行",
                "source_input_id": "paired_data",
                "source_row_id_column": "date",
                "source_target_column": "hmi_candidate_g",
                "source_baseline_column": "wso_reference_g",
                "candidate_model_input_columns": ["wso_reference_g"],
                "candidate_model_target_column": "wso_reference_g",
                "baseline_model_input_columns": [],
                "baseline_model_target_column": None,
                "baseline_fit_condition": None,
                "candidate_fit_condition": "使用前序行拟合候选校准模型",
                "fit_evaluation_relation": "disjoint_rows",
                "evaluation_target_usage": "metrics_and_evidence_only",
                "evidence_artifact": "row_level_results.csv",
                "evidence_row_id_column": "date",
                "evidence_target_column": "target_hmi_g",
                "evidence_baseline_column": "raw_wso_g",
                "evidence_candidate_column": "calibrated_hmi_g",
                "metric": "mae",
                "baseline_measurement": "holdout_raw_mae",
                "candidate_measurement": "holdout_calibrated_mae",
                "delta_measurement": "holdout_mae_improvement",
                "delta_formula": "baseline_minus_candidate",
            }
        ]
        with self.assertRaisesRegex(
            ContractError,
            "candidate_model_target_column must equal source_target_column",
        ):
            validate_design(candidate, req, response(req))

    def test_chinese_task_requires_chinese_reader_facing_design(self) -> None:
        req = request(task="使用 inputs/example_mean.csv 设计并执行一个有界实验。")
        res = response(req)
        candidate = design(req)
        with self.assertRaisesRegex(
            ContractError,
            "reader-facing design fields must use the user's Chinese language",
        ):
            validate_design(candidate, req, res)

    def test_worker_rejects_nonfinite_measurement(self) -> None:
        payload = {
            "schema_version": "automatic-experiment-worker-result-v1",
            "execution_completed": True,
            "measurements": [{"name": "x", "value": float("nan"), "unit": "", "role": "primary", "source_artifact": None}],
            "result_items": [],
            "artifacts": [],
            "warnings": [],
            "endpoint_results": [],
            "scientific_payload": {
                "primary_estimand": "x",
                "estimate": None,
                "interval": None,
                "equivalence_bounds": None,
                "sensitivity": None,
                "uncertainty_reasons": [],
            },
        }
        with self.assertRaisesRegex(ContractError, "finite"):
            validate_worker_result(payload)

    def test_worker_measurement_source_must_reference_declared_artifact(self) -> None:
        payload = {
            "schema_version": "automatic-experiment-worker-result-v1",
            "execution_completed": True,
            "measurements": [
                {
                    "name": "x",
                    "value": 1.0,
                    "unit": "",
                    "role": "primary",
                    "source_artifact": "missing.json",
                }
            ],
            "result_items": [],
            "artifacts": [],
            "warnings": [],
            "endpoint_results": [],
            "scientific_payload": {
                "primary_estimand": "x",
                "estimate": 1.0,
                "interval": None,
                "equivalence_bounds": None,
                "sensitivity": None,
                "uncertainty_reasons": [],
            },
        }
        with self.assertRaisesRegex(ContractError, "reference a declared artifact"):
            validate_worker_result(payload)

    def test_scientific_null_requires_interval_and_basis(self) -> None:
        req = request()
        des = design(req)
        des["research_frame"]["analysis_mode"] = (
            "Inferential assessment with a predeclared confidence interval."
        )
        worker = {
            "schema_version": "automatic-experiment-worker-result-v1",
            "execution_completed": True,
            "measurements": [{"name": "mean", "value": 0.0, "unit": "", "role": "primary", "source_artifact": None}],
            "result_items": [],
            "artifacts": [],
            "warnings": [],
            "endpoint_results": [{"id": "mean_endpoint", "status": "completed", "summary": "Done."}],
            "scientific_payload": {
                "primary_estimand": "arithmetic mean",
                "estimate": 0.0,
                "interval": [-0.2, 0.2],
                "equivalence_bounds": None,
                "sensitivity": None,
                "uncertainty_reasons": [],
            },
        }
        proposed = {
            "proposed_outcome": "scientific_null",
            "rationale": "No meaningful effect was detected.",
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
                "interval": [-0.2, 0.2],
                "equivalence_bounds": None,
                "power_or_sensitivity": None,
            },
            "report_narrative": {
                "title": "Equivalence assessment",
                "objective": "Assess the predefined estimand against an equivalence range.",
                "data_scope": "The assessment covers the verified experiment result.",
                "method": "Compare the reported interval with the predefined equivalence bounds.",
                "interpretation": "The current payload is intended to represent a scientific null result.",
                "evidence_strength": "The interval and sensitivity evidence determine whether equivalence is supported.",
                "claim_boundary": "The result cannot be generalized beyond the declared estimand and fixture.",
                "limitations": ["The conclusion depends on the interval and sensitivity justification."],
                "next_steps": ["Review the interval and sensitivity evidence before reuse."],
            },
        }
        with self.assertRaisesRegex(ContractError, "equivalence bounds"):
            validate_scientific_assessment(proposed, des, worker)

    def test_chinese_task_requires_chinese_reader_narrative(self) -> None:
        req = request()
        des = design(req)
        proposed = {
            "proposed_outcome": "completed_interpretable",
            "rationale": "The calculation completed.",
            "criterion_results": [
                {
                    "criterion_id": "mean_result",
                    "status": "met",
                    "explanation": "The mean and planned endpoint were verified.",
                }
            ],
            "uncertainty_reasons": [],
            "null_assessment": None,
            "report_narrative": {
                "title": "Mean report",
                "objective": "Calculate one mean.",
                "data_scope": "Only the supplied data are included.",
                "method": "Use the arithmetic mean.",
                "interpretation": "The result describes the supplied data.",
                "evidence_strength": "The exact calculation is supported by the fixed input.",
                "claim_boundary": "No broader inference is supported.",
                "limitations": ["No population inference is supported."],
                "next_steps": ["Replay if needed."],
            },
        }
        worker = {
            "schema_version": "automatic-experiment-worker-result-v1",
            "execution_completed": True,
            "measurements": [{"name": "mean", "value": 2.5, "unit": "", "role": "primary", "source_artifact": None}],
            "result_items": [],
            "artifacts": [],
            "warnings": [],
            "endpoint_results": [{"id": "mean_endpoint", "status": "completed", "summary": "Done."}],
            "scientific_payload": {
                "primary_estimand": "arithmetic mean",
                "estimate": 2.5,
                "interval": None,
                "equivalence_bounds": None,
                "sensitivity": None,
                "uncertainty_reasons": [],
            },
        }
        with self.assertRaisesRegex(ContractError, "user's Chinese language"):
            validate_scientific_assessment(
                proposed,
                des,
                worker,
                task_text="计算 inputs/example_mean.csv 的均值。",
            )

    def test_reader_narrative_rejects_internal_contract_terms(self) -> None:
        req = request()
        des = design(req)
        proposed = {
            "proposed_outcome": "completed_interpretable",
            "rationale": "The calculation completed.",
            "criterion_results": [
                {
                    "criterion_id": "mean_result",
                    "status": "met",
                    "explanation": "The mean and planned endpoint were verified.",
                }
            ],
            "uncertainty_reasons": [],
            "null_assessment": None,
            "report_narrative": {
                "title": "worker result and criterion_results export",
                "objective": "Calculate one mean.",
                "data_scope": "Only the supplied data are included.",
                "method": "Use the arithmetic mean.",
                "interpretation": "The result describes the supplied data.",
                "evidence_strength": "The exact calculation is supported by the fixed input.",
                "claim_boundary": "No broader inference is supported.",
                "limitations": ["No population inference is supported."],
                "next_steps": ["Replay if needed."],
            },
        }
        worker = {
            "schema_version": "automatic-experiment-worker-result-v1",
            "execution_completed": True,
            "measurements": [{"name": "mean", "value": 2.5, "unit": "", "role": "primary", "source_artifact": None}],
            "result_items": [],
            "artifacts": [],
            "warnings": [],
            "endpoint_results": [{"id": "mean_endpoint", "status": "completed", "summary": "Done."}],
            "scientific_payload": {
                "primary_estimand": "arithmetic mean",
                "estimate": 2.5,
                "interval": None,
                "equivalence_bounds": None,
                "sensitivity": None,
                "uncertainty_reasons": [],
            },
        }
        with self.assertRaisesRegex(ContractError, "must not expose internal"):
            validate_scientific_assessment(proposed, des, worker)

        proposed["report_narrative"]["title"] = "A bounded descriptive result"
        proposed["rationale"] = "The program exit code was normal."
        validated = validate_scientific_assessment(proposed, des, worker)
        self.assertEqual(
            validated["rationale"],
            "The program exit code was normal.",
        )
        proposed["report_narrative"]["title"] = (
            "The measurement name and result id were aligned."
        )
        with self.assertRaisesRegex(ContractError, "must not expose internal"):
            validate_scientific_assessment(proposed, des, worker)

    def test_design_criterion_cannot_treat_code_success_as_scientific_result(
        self,
    ) -> None:
        req = request()
        candidate = design(req)
        candidate["criteria"][0]["basis_text"] = (
            "代码成功执行并输出产物文件。"
        )
        with self.assertRaisesRegex(ContractError, "must not expose internal"):
            validate_design(candidate, req, response(req))

    def test_noninferential_reader_narrative_rejects_significance_language(self) -> None:
        req = request()
        des = design(req)
        proposed = assessment()
        proposed["report_narrative"]["interpretation"] = (
            "The descriptive difference is 极为显著."
        )
        worker = {
            "schema_version": "automatic-experiment-worker-result-v1",
            "execution_completed": True,
            "measurements": [
                {
                    "name": "mean",
                    "value": 2.5,
                    "unit": "",
                    "role": "primary",
                    "source_artifact": None,
                }
            ],
            "result_items": [],
            "artifacts": [],
            "warnings": [],
            "endpoint_results": [
                {
                    "id": "mean_endpoint",
                    "status": "completed",
                    "summary": "Done.",
                }
            ],
            "scientific_payload": {
                "primary_estimand": "arithmetic mean",
                "estimate": 2.5,
                "interval": None,
                "equivalence_bounds": None,
                "sensitivity": None,
                "uncertainty_reasons": [],
            },
        }
        with self.assertRaisesRegex(ContractError, "must not use 显著"):
            validate_scientific_assessment(proposed, des, worker)

    def test_noninferential_criterion_explanation_rejects_significance_language(self) -> None:
        req = request()
        des = design(req)
        proposed = assessment()
        proposed["criterion_results"][0]["explanation"] = "结果显著降低。"
        worker = {
            "schema_version": "automatic-experiment-worker-result-v1",
            "execution_completed": True,
            "measurements": [
                {
                    "name": "mean",
                    "value": 2.5,
                    "unit": "",
                    "role": "primary",
                    "source_artifact": None,
                }
            ],
            "result_items": [],
            "artifacts": [],
            "warnings": [],
            "endpoint_results": [
                {
                    "id": "mean_endpoint",
                    "status": "completed",
                    "summary": "Done.",
                }
            ],
            "scientific_payload": {
                "primary_estimand": "arithmetic mean",
                "estimate": 2.5,
                "interval": None,
                "equivalence_bounds": None,
                "sensitivity": None,
                "uncertainty_reasons": [],
            },
        }
        with self.assertRaisesRegex(ContractError, "must not use 显著"):
            validate_scientific_assessment(proposed, des, worker)

    def test_effect_cannot_be_called_trivial_without_equivalence_basis(self) -> None:
        req = request()
        des = design(req)
        proposed = assessment()
        proposed["report_narrative"]["interpretation"] = (
            "两个条件的差值很微小，可以忽略。"
        )
        with self.assertRaises(ContractError) as raised:
            validate_scientific_assessment(
                proposed,
                des,
                simple_worker_result(),
            )
        self.assertEqual(
            raised.exception.error_code,
            "unsupported_trivial_impact_language",
        )

        proposed["report_narrative"]["interpretation"] = (
            "跨条件差值有限，表明结果对这一标记行不敏感。"
        )
        with self.assertRaises(ContractError) as raised:
            validate_scientific_assessment(
                proposed,
                des,
                simple_worker_result(),
            )
        self.assertEqual(
            raised.exception.error_code,
            "unsupported_trivial_impact_language",
        )

        proposed["report_narrative"]["interpretation"] = (
            "排除该行后，改善量与原结果略有差异。"
        )
        with self.assertRaises(ContractError) as raised:
            validate_scientific_assessment(
                proposed,
                des,
                simple_worker_result(),
            )
        self.assertEqual(
            raised.exception.error_code,
            "unsupported_trivial_impact_language",
        )

        for unsupported in (
            "两种条件下校准参数保持稳定。",
            "标记观测造成的差异量级较小。",
            "排除标记观测后校准参数变化幅度较小。",
        ):
            proposed["report_narrative"]["interpretation"] = unsupported
            with self.assertRaises(ContractError) as raised:
                validate_scientific_assessment(
                    proposed,
                    des,
                    simple_worker_result(),
                )
            self.assertEqual(
                raised.exception.error_code,
                "unsupported_trivial_impact_language",
            )

        proposed["report_narrative"]["interpretation"] = (
            "当前数据不足以判断该差异是否具有实质影响。"
        )
        validated = validate_scientific_assessment(
            proposed,
            des,
            simple_worker_result(),
        )
        self.assertIn(
            "不足以判断",
            validated["report_narrative"]["interpretation"],
        )
        proposed["report_narrative"]["limitations"] = [
            "本次未采用对单点不敏感的拟合方法。"
        ]
        validated = validate_scientific_assessment(
            proposed,
            des,
            simple_worker_result(),
        )
        self.assertIn(
            "对单点不敏感的拟合方法",
            validated["report_narrative"]["limitations"][0],
        )

    def test_noninferential_report_allows_explicit_no_significance_claim(self) -> None:
        req = request()
        des = design(req)
        proposed = assessment()
        proposed["report_narrative"]["limitations"] = [
            "本次只是描述性计算，未进行显著性检验，也不支持总体推断。"
        ]
        worker = {
            "schema_version": "automatic-experiment-worker-result-v1",
            "execution_completed": True,
            "measurements": [
                {
                    "name": "mean",
                    "value": 2.5,
                    "unit": "",
                    "role": "primary",
                    "source_artifact": None,
                }
            ],
            "result_items": [],
            "artifacts": [],
            "warnings": [],
            "endpoint_results": [
                {
                    "id": "mean_endpoint",
                    "status": "completed",
                    "summary": "Done.",
                }
            ],
            "scientific_payload": {
                "primary_estimand": "arithmetic mean",
                "estimate": 2.5,
                "interval": None,
                "equivalence_bounds": None,
                "sensitivity": None,
                "uncertainty_reasons": [],
            },
        }
        validated = validate_scientific_assessment(proposed, des, worker)
        self.assertIn("未进行显著性检验", validated["report_narrative"]["limitations"][0])

    def test_nonpredictive_reader_narrative_rejects_generalization_claim(self) -> None:
        req = request()
        des = design(req)
        proposed = assessment()
        proposed["report_narrative"]["evidence_strength"] = (
            "One small holdout proves 泛化能力."
        )
        worker = {
            "schema_version": "automatic-experiment-worker-result-v1",
            "execution_completed": True,
            "measurements": [
                {
                    "name": "mean",
                    "value": 2.5,
                    "unit": "",
                    "role": "primary",
                    "source_artifact": None,
                }
            ],
            "result_items": [],
            "artifacts": [],
            "warnings": [],
            "endpoint_results": [
                {
                    "id": "mean_endpoint",
                    "status": "completed",
                    "summary": "Done.",
                }
            ],
            "scientific_payload": {
                "primary_estimand": "arithmetic mean",
                "estimate": 2.5,
                "interval": None,
                "equivalence_bounds": None,
                "sensitivity": None,
                "uncertainty_reasons": [],
            },
        }
        with self.assertRaisesRegex(ContractError, "claiming 泛化能力"):
            validate_scientific_assessment(proposed, des, worker)

    def test_nonpredictive_report_allows_explicit_generalization_limit(self) -> None:
        req = request()
        des = design(req)
        proposed = assessment()
        proposed["report_narrative"]["limitations"] = [
            "样本量较小，泛化能力有限，不能外推到其他数据。"
        ]
        worker = {
            "schema_version": "automatic-experiment-worker-result-v1",
            "execution_completed": True,
            "measurements": [
                {
                    "name": "mean",
                    "value": 2.5,
                    "unit": "",
                    "role": "primary",
                    "source_artifact": None,
                }
            ],
            "result_items": [],
            "artifacts": [],
            "warnings": [],
            "endpoint_results": [
                {
                    "id": "mean_endpoint",
                    "status": "completed",
                    "summary": "Done.",
                }
            ],
            "scientific_payload": {
                "primary_estimand": "arithmetic mean",
                "estimate": 2.5,
                "interval": None,
                "equivalence_bounds": None,
                "sensitivity": None,
                "uncertainty_reasons": [],
            },
        }
        validated = validate_scientific_assessment(proposed, des, worker)
        self.assertIn("泛化能力有限", validated["report_narrative"]["limitations"][0])

        proposed["report_narrative"]["limitations"] = []
        proposed["report_narrative"]["next_steps"] = [
            "若需验证模型的泛化性，需要更多独立时间段的观测。"
        ]
        validated = validate_scientific_assessment(proposed, des, worker)
        self.assertIn("若需验证", validated["report_narrative"]["next_steps"][0])

        proposed["report_narrative"]["next_steps"] = []
        proposed["report_narrative"]["limitations"] = [
            "时间有序留出集中在后期时段，对早期时段的泛化能力缺乏检验。"
        ]
        validated = validate_scientific_assessment(proposed, des, worker)
        self.assertIn(
            "泛化能力缺乏检验",
            validated["report_narrative"]["limitations"][0],
        )

    def test_small_holdout_report_rejects_bias_elimination_claim(self) -> None:
        req = request()
        des = design(req)
        proposed = assessment()
        proposed["report_narrative"]["interpretation"] = (
            "六行留出结果说明线性校准有效消除了未校准数据的系统性偏移。"
        )
        worker = {
            "schema_version": "automatic-experiment-worker-result-v1",
            "execution_completed": True,
            "measurements": [
                {
                    "name": "mean",
                    "value": 2.5,
                    "unit": "",
                    "role": "primary",
                    "source_artifact": None,
                }
            ],
            "result_items": [],
            "artifacts": [],
            "warnings": [],
            "endpoint_results": [
                {
                    "id": "mean_endpoint",
                    "status": "completed",
                    "summary": "Done.",
                }
            ],
            "scientific_payload": {
                "primary_estimand": "arithmetic mean",
                "estimate": 2.5,
                "interval": None,
                "equivalence_bounds": None,
                "sensitivity": None,
                "uncertainty_reasons": [],
            },
        }
        with self.assertRaisesRegex(ContractError, "without claiming"):
            validate_scientific_assessment(proposed, des, worker)

    def test_small_holdout_report_allows_explicit_bias_claim_boundary(self) -> None:
        req = request()
        des = design(req)
        proposed = assessment()
        proposed["report_narrative"]["claim_boundary"] = (
            "六行留出仅显示当前误差下降，不足以证明已消除系统性偏移。"
        )
        worker = {
            "schema_version": "automatic-experiment-worker-result-v1",
            "execution_completed": True,
            "measurements": [
                {
                    "name": "mean",
                    "value": 2.5,
                    "unit": "",
                    "role": "primary",
                    "source_artifact": None,
                }
            ],
            "result_items": [],
            "artifacts": [],
            "warnings": [],
            "endpoint_results": [
                {
                    "id": "mean_endpoint",
                    "status": "completed",
                    "summary": "Done.",
                }
            ],
            "scientific_payload": {
                "primary_estimand": "arithmetic mean",
                "estimate": 2.5,
                "interval": None,
                "equivalence_bounds": None,
                "sensitivity": None,
                "uncertainty_reasons": [],
            },
        }
        validated = validate_scientific_assessment(proposed, des, worker)
        self.assertIn("不足以证明", validated["report_narrative"]["claim_boundary"])

    def test_small_holdout_rejects_systemic_bias_label_with_structured_error(self) -> None:
        des = design(request())
        proposed = assessment()
        proposed["report_narrative"]["interpretation"] = (
            "当前六行留出段存在系统正偏差。"
        )
        with self.assertRaises(ContractError) as raised:
            validate_scientific_assessment(
                proposed,
                des,
                simple_worker_result(),
            )
        self.assertEqual(
            raised.exception.error_code,
            "unsupported_systemic_holdout_claim",
        )
        self.assertEqual(
            raised.exception.field_path,
            "scientific_assessment.report_narrative",
        )
        self.assertIn("平均有符号误差", raised.exception.suggestion)

    def test_small_holdout_rejects_unbounded_closeness_claim(self) -> None:
        des = design(request())
        proposed = assessment()
        proposed["report_narrative"]["interpretation"] = (
            "六行预测均接近比较坐标。"
        )
        with self.assertRaises(ContractError) as raised:
            validate_scientific_assessment(
                proposed,
                des,
                simple_worker_result(),
            )
        self.assertEqual(
            raised.exception.error_code,
            "unsupported_unbounded_closeness_claim",
        )
        self.assertIn("预先定义阈值", raised.exception.suggestion)

    def test_correlation_assessment_rejects_ungrounded_strength_label(self) -> None:
        des = design(request(task="计算 Pearson 相关系数和相关方向。"))
        proposed = assessment()
        proposed["report_narrative"]["interpretation"] = (
            "两组读数呈现极强的正向线性关联。"
        )
        with self.assertRaises(ContractError) as raised:
            validate_scientific_assessment(
                proposed,
                des,
                simple_worker_result(),
            )
        self.assertEqual(
            raised.exception.error_code,
            "unsupported_correlation_degree_language",
        )
        self.assertIn("分级阈值", raised.exception.suggestion)

    def test_correlation_assessment_rejects_unplanned_monotonicity(self) -> None:
        des = design(request(task="计算 Pearson 相关系数和相关方向。"))
        proposed = assessment()
        proposed["report_narrative"]["interpretation"] = (
            "两组读数在当前样本中单调递增。"
        )
        with self.assertRaises(ContractError) as raised:
            validate_scientific_assessment(
                proposed,
                des,
                simple_worker_result(),
            )
        self.assertEqual(
            raised.exception.error_code,
            "unverified_monotonicity_claim",
        )
        self.assertIn("单调性", raised.exception.suggestion)

    def test_reader_narrative_rejects_nonidentity_from_point_estimates(self) -> None:
        des = design(request())
        proposed = assessment()
        proposed["report_narrative"]["interpretation"] = (
            "校准参数表明两组读数之间存在近似线性但非恒等的关系。"
        )
        with self.assertRaises(ContractError) as raised:
            validate_scientific_assessment(
                proposed,
                des,
                simple_worker_result(),
            )
        self.assertEqual(
            raised.exception.error_code,
            "unsupported_nonidentity_claim",
        )

    def test_single_flag_check_rejects_broad_robustness_claim(self) -> None:
        des = design(request())
        proposed = assessment()
        proposed["report_narrative"]["evidence_strength"] = "结果整体稳健。"
        with self.assertRaises(ContractError) as raised:
            validate_scientific_assessment(
                proposed,
                des,
                simple_worker_result(),
            )
        self.assertEqual(
            raised.exception.error_code,
            "unsupported_broad_robustness_claim",
        )
        self.assertIn("标记行", raised.exception.suggestion)

    def test_reader_may_explicitly_deny_broad_robustness_claim(self) -> None:
        des = design(request())
        proposed = assessment()
        proposed["report_narrative"]["limitations"] = [
            "本次只排除一条质量标记观测，不构成全面稳健性分析。"
        ]
        proposed["report_narrative"]["next_steps"] = [
            "考察稳健校正方法是否改变当前误差方向。"
        ]
        validated = validate_scientific_assessment(
            proposed,
            des,
            simple_worker_result(),
        )
        self.assertIn(
            "不构成全面稳健性分析",
            validated["report_narrative"]["limitations"][0],
        )

    def test_reader_narrative_rejects_conflicting_scientific_outcome(self) -> None:
        req = request()
        des = design(req)
        proposed = assessment()
        proposed["report_narrative"]["interpretation"] = (
            "计算已经完成，但鉴于样本较少，本次结果标记为高不确定性。"
        )
        worker = {
            "schema_version": "automatic-experiment-worker-result-v1",
            "execution_completed": True,
            "measurements": [
                {
                    "name": "mean",
                    "value": 2.5,
                    "unit": "",
                    "role": "primary",
                    "source_artifact": None,
                }
            ],
            "result_items": [],
            "artifacts": [],
            "warnings": [],
            "endpoint_results": [
                {
                    "id": "mean_endpoint",
                    "status": "completed",
                    "summary": "Done.",
                }
            ],
            "scientific_payload": {
                "primary_estimand": "arithmetic mean",
                "estimate": 2.5,
                "interval": None,
                "equivalence_bounds": None,
                "sensitivity": None,
                "uncertainty_reasons": [],
            },
        }
        with self.assertRaisesRegex(ContractError, "conflicts with proposed_outcome"):
            validate_scientific_assessment(proposed, des, worker)

    def test_synthetic_fixture_must_be_disclosed_in_reader_report(self) -> None:
        req = request()
        des = design(req)
        des["criteria"][0]["statement"] = "校准后的留出误差得到核验。"
        des["criteria"][0]["measurement_refs"] = ["rmse"]
        des["criteria"][0]["endpoint_refs"] = []
        des["criteria"][0]["artifact_refs"] = []
        des["interpretation_policy"]["primary_estimand"] = "holdout rmse"
        proposed = {
            "proposed_outcome": "completed_interpretable",
            "rationale": "实验已完成并得到可解释结果。",
            "criterion_results": [
                {
                    "criterion_id": "mean_result",
                    "status": "met",
                    "explanation": "留出集误差测量已核验。",
                }
            ],
            "uncertainty_reasons": [],
            "null_assessment": None,
            "report_narrative": {
                "title": "跨仪器校准报告",
                "objective": "评估配对数据上的校准效果。",
                "data_scope": "使用十八条配对记录。",
                "method": "使用时间留出评价校准误差。",
                "interpretation": "校准后误差降低。",
                "evidence_strength": "证据仅来自当前十八条配对记录的留出评估。",
                "claim_boundary": "不能形成真实仪器性能、物理机制或活动周预测主张。",
                "limitations": ["不能外推到未观察时间段。"],
                "next_steps": ["补充更多配对数据。"],
            },
        }
        worker = {
            "schema_version": "automatic-experiment-worker-result-v1",
            "execution_completed": True,
            "measurements": [{"name": "rmse", "value": 0.1, "unit": "G", "role": "primary", "source_artifact": None}],
            "result_items": [],
            "artifacts": [],
            "warnings": [],
            "endpoint_results": [],
            "scientific_payload": {
                "primary_estimand": "holdout rmse",
                "estimate": 0.1,
                "interval": None,
                "equivalence_bounds": None,
                "sensitivity": None,
                "uncertainty_reasons": [],
            },
        }
        with self.assertRaisesRegex(ContractError, "synthetic or simulated fixture"):
            validate_scientific_assessment(
                proposed,
                des,
                worker,
                task_text="使用合成测试夹具完成跨仪器校准。",
            )

    def test_all_seven_schemas_are_valid_json_schema(self) -> None:
        specs = Path(__file__).resolve().parents[1] / "specs"
        paths = sorted(specs.glob("*.schema.json"))
        self.assertEqual(len(paths), 7)
        for path in paths:
            schema = json.loads(path.read_text(encoding="utf-8"))
            jsonschema.Draft202012Validator.check_schema(schema)

    def test_current_request_response_design_and_worker_match_published_schemas(self) -> None:
        specs = Path(__file__).resolve().parents[1] / "specs"
        req = request("schema_current_contract")
        payloads = {
            "automatic_experiment_request_v1.schema.json": req,
            "automatic_experiment_response_v1.schema.json": response(req),
            "automatic_experiment_design_v1.schema.json": design(req),
            "automatic_experiment_worker_result_v1.schema.json": simple_worker_result(),
        }
        for name, payload in payloads.items():
            with self.subTest(schema=name):
                schema = json.loads((specs / name).read_text(encoding="utf-8"))
                jsonschema.Draft202012Validator(schema).validate(payload)


if __name__ == "__main__":
    unittest.main()
