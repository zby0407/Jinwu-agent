from __future__ import annotations

import unittest
from unittest import mock

from automatic_experiment import reporting, service
from automatic_experiment.state import (
    atomic_write_json,
    file_sha256,
    read_json,
    runs_root,
)
from tests.helpers import (
    assessment,
    cleanup_run,
    create_ready_run,
    request,
)


class ReportingTests(unittest.TestCase):
    def test_placeholder_unit_is_not_published_as_a_scientific_unit(self) -> None:
        row = {"value": 2.5, "unit": "与数值列相同单位"}
        self.assertEqual(reporting._format_measurement(row), "2.5")
        self.assertEqual(reporting._format_typed_result(row), "2.5")
        note = reporting._missing_unit_note(
            {"measurement_plan": [{"name": "mean", "unit": row["unit"]}]},
            [row],
            [],
            chinese_task=True,
        )
        self.assertEqual(
            note,
            "原始数据未注明计量单位，因此相关统计量只报告数值，不补设单位。",
        )
        reporting._validate_main_report_quality(f"# 数据与方法\n\n{note}\n")
        with self.assertRaisesRegex(RuntimeError, "internal workflow terms"):
            reporting._validate_main_report_quality(
                "# 结果\n\n均值为 2.5，与数值列相同单位。\n"
            )

    def test_machine_style_text_result_is_rendered_for_scientific_readers(self) -> None:
        self.assertEqual(
            reporting._format_typed_result(
                {
                    "value_kind": "text",
                    "value": "simple_linear: reference = intercept + slope * candidate",
                    "unit": "",
                }
            ),
            "线性函数：参考坐标 = 截距 + 斜率 × 候选读数",
        )
        self.assertEqual(
            reporting._format_typed_result(
                {
                    "value_kind": "text",
                    "value": "model_type_v2",
                    "unit": "",
                }
            ),
            "见数据与方法中的定义",
        )

    def test_scientific_reader_text_removes_unsupported_degree_words(self) -> None:
        rendered = reporting._scientific_reader_text(
            "结果为确定性精确计算，无抽样不确定性或模型假设。"
            "排除标记观测后误差略低。"
        )
        self.assertIn("对当前数据的确定性描述", rendered)
        self.assertIn("未进行总体推断", rendered)
        self.assertIn("未估计抽样不确定性", rendered)
        self.assertIn("误差更低", rendered)
        for forbidden in ("精确计算", "无抽样不确定性", "略低"):
            self.assertNotIn(forbidden, rendered)

    def test_reader_text_replaces_filename_and_plain_column_names(self) -> None:
        rendered = reporting._scientific_reader_text(
            "example_mean.csv 数据文件包含两列（group 和 value），共 4 条观测。"
            "逐行读取 value 列并按 group 列检查分组。"
        )
        self.assertIn("数据包含", rendered)
        self.assertIn("分组变量和数值变量", rendered)
        self.assertIn("读取数值变量", rendered)
        self.assertIn("按分组变量", rendered)
        self.assertNotIn("输入输入", rendered)
        for forbidden in ("example_mean", "group", "value", "两列"):
            self.assertNotIn(forbidden, rendered)

    def test_simple_statistical_title_and_duplicate_range_are_naturalized(self) -> None:
        self.assertEqual(
            reporting._scientific_reader_text(
                "example_mean.csv 数值列描述性统计"
            ),
            "输入数据的描述性统计",
        )
        rendered = reporting._concise_abstract_result(
            "均值为 2.5，最小值为 1，最大值为 4。"
            "实际读取的数据范围为最小值 1 至最大值 4。"
        )
        self.assertEqual(rendered, "均值为 2.5，最小值为 1，最大值为 4。")

    def test_reader_text_corrects_small_sample_and_missing_unit_overclaims(self) -> None:
        rendered = reporting._scientific_reader_text(
            "确定性计算，全部观测参与统计，结果可精确复现。"
            "数据量极小（仅 4 条观测），描述统计的稳定性有限。"
            "原始数据未注明计量单位，结果数值无物理量纲。"
        )
        self.assertIn("所得统计量可由同一输入复算", rendered)
        self.assertIn("当前输入仅含 4 条观测", rendered)
        self.assertIn("不支持总体推断", rendered)
        self.assertIn("无法确定这些数值的计量含义", rendered)
        for forbidden in ("精确复现", "稳定性有限", "无物理量纲"):
            self.assertNotIn(forbidden, rendered)

    def test_report_quality_rejects_an_empty_section(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "empty section"):
            reporting._validate_main_report_quality(
                "# 报告\n\n## 讨论\n\n## 局限性\n\n- 当前证据有限。\n"
            )

    def test_simple_method_is_rendered_as_research_action(self) -> None:
        rendered = reporting._scientific_reader_text(
            "使用 Python 标准库逐行读取 CSV，过滤有限数值后计算均值。"
            "本任务为确定性计算，不涉及抽样推断或假设检验。"
        )
        self.assertIn("逐条读取输入数据", rendered)
        self.assertIn("基于当前输入的完整枚举", rendered)
        self.assertNotIn("Python", rendered)
        self.assertNotIn("本任务", rendered)

    def test_paired_lines_use_actual_count_and_named_fit_conditions(self) -> None:
        design_payload = {
            "measurement_plan": [
                {
                    "name": "raw_mae",
                    "display_name": "未校准平均绝对误差",
                    "scientific_meaning": "当前评估观测的未校准平均绝对误差",
                },
                {
                    "name": "include_mae",
                    "display_name": "校准后平均绝对误差（保留标记拟合，排除标记评估）",
                    "scientific_meaning": "保留标记观测拟合后的平均绝对误差",
                },
                {
                    "name": "exclude_mae",
                    "display_name": "校准后平均绝对误差（排除标记拟合，排除标记评估）",
                    "scientific_meaning": "排除标记观测拟合后的平均绝对误差",
                },
            ],
            "paired_comparison_audits": [
                {
                    "id": "include_vs_raw",
                    "comparison_kind": "source_baseline_vs_candidate",
                    "baseline_measurement": "raw_mae",
                    "candidate_measurement": "include_mae",
                    "baseline_fit_condition": None,
                    "candidate_fit_condition": "include_all",
                },
                {
                    "id": "exclude_vs_raw",
                    "comparison_kind": "source_baseline_vs_candidate",
                    "baseline_measurement": "raw_mae",
                    "candidate_measurement": "exclude_mae",
                    "baseline_fit_condition": None,
                    "candidate_fit_condition": "exclude_marked",
                },
                {
                    "id": "fit_sensitivity",
                    "comparison_kind": "candidate_vs_candidate",
                    "baseline_measurement": "include_mae",
                    "candidate_measurement": "exclude_mae",
                    "baseline_fit_condition": "include_all",
                    "candidate_fit_condition": "exclude_marked",
                },
            ],
        }
        paired = [
            {
                "id": audit_id,
                "evaluation_scope": "排除标记观测时的同一批 5 行评估观测",
                "row_count": 6,
                "all_candidate_absolute_errors_lower": True,
            }
            for audit_id in (
                "include_vs_raw",
                "exclude_vs_raw",
                "fit_sensitivity",
            )
        ]
        lines = reporting._paired_result_lines(
            paired,
            design_payload,
            chinese_task=True,
        )
        rendered = "\n".join(lines)
        self.assertIn("相同的 6 条留出观测", rendered)
        self.assertIn("无论包含还是排除被标记观测", rendered)
        self.assertIn("包含被标记观测时", rendered)
        self.assertIn("排除标记观测时", rendered)
        self.assertNotIn("5 条", rendered)
        self.assertNotIn("校准后读数的绝对误差均低于校准后读数", rendered)

    def test_fitting_only_sensitivity_does_not_rename_the_evaluation_set(self) -> None:
        design_payload = {
            "paired_comparison_audits": [
                {
                    "comparison_kind": "candidate_vs_candidate",
                    "fit_evaluation_relation": "disjoint_rows",
                    "baseline_fit_condition": "全部校准观测",
                    "candidate_fit_condition": "排除被标记校准观测",
                }
            ]
        }
        display = reporting._scientific_result_display(
            "全量拟合校准在排除标记评估期上的 MAE",
            "全量数据拟合的校准模型在排除标记观测的评估观测上计算",
            design_payload,
        )
        definition = reporting._scientific_result_definition(
            "全量数据拟合的校准模型在排除标记观测的评估观测上计算",
            design_payload,
        )
        _, intro = reporting._paired_scope_and_intro(
            "排除被标记观测后的后段评估期",
            6,
            True,
        )
        self.assertEqual(display, "包含被标记观测拟合后的校准平均绝对误差")
        self.assertIn("固定的留出评估观测", definition)
        self.assertEqual(intro, "在相同的 6 条留出观测中")
        self.assertNotIn("排除标记评估", display + definition)
        self.assertEqual(
            reporting._scientific_result_display(
                "全数据拟合斜率",
                "全部校准观测上的拟合斜率",
                design_payload,
            ),
            "包含被标记观测拟合后的斜率",
        )
        self.assertEqual(
            reporting._scientific_result_display(
                "排除标记校准的 MAE",
                "排除标记观测拟合的校准误差",
                design_payload,
            ),
            "排除标记观测拟合后的校准平均绝对误差",
        )
        self.assertEqual(
            reporting._scientific_result_display(
                "全数据未校准平均绝对误差",
                "留出观测的原始误差",
                design_payload,
            ),
            "留出观测未校准平均绝对误差",
        )
        self.assertEqual(
            reporting._scientific_result_display(
                "校准后平均绝对误差（全部行拟合）",
                "全部行拟合后的留出误差",
                design_payload,
            ),
            "校准后平均绝对误差（包含被标记观测的拟合）",
        )

    def test_report_quality_rejects_count_conflict_and_self_comparison(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "contradictory observation counts"):
            reporting._validate_main_report_quality(
                "# 报告\n\n## 结果\n\n在 5 条评估集的全部 6 条观测中获得结果。\n"
            )
        with self.assertRaisesRegex(RuntimeError, "condition with itself"):
            reporting._validate_main_report_quality(
                "# 报告\n\n## 结果\n\n"
                "校准后读数的绝对误差均低于校准后读数的绝对误差。\n"
            )

    def test_simple_summary_omits_unrequested_pattern_embellishment(self) -> None:
        rendered = reporting._remove_unrequested_descriptive_pattern(
            "4 条观测的均值为 2.5。"
            "均值恰好位于数据中点，与均匀递增序列的对称性质一致。",
            "计算均值、样本数、最小值和最大值。",
        )
        self.assertEqual(rendered, "4 条观测的均值为 2.5。")
        self.assertNotIn("对称", rendered)

    def test_numeric_narrative_does_not_repeat_generated_primary_sentence(
        self,
    ) -> None:
        self.assertFalse(
            reporting._needs_primary_abstract_sentence(
                "保留标记观测时改善量为 0.581 G，排除时为 0.608 G。",
                has_complete_primary_values=False,
            )
        )
        self.assertTrue(
            reporting._needs_primary_abstract_sentence(
                "两种条件下误差均下降。",
                has_complete_primary_values=False,
            )
        )

    def test_reader_text_translates_common_fields_and_bounds_robustness_wording(
        self,
    ) -> None:
        rendered = reporting._reader_facing_text(
            "按 `date` 检查 cycle_phase、wso_reference_g、hmi_candidate_g、"
            "quality_flag 和 suspect_geometry，并验证结论稳健性。"
        )
        self.assertIn("date", rendered)
        self.assertIn("hmi_candidate_g", rendered)
        self.assertIn("检查方向性结论是否一致", rendered)
        self.assertNotIn("suspect_geometry", rendered)
        self.assertIn("几何条件可疑", rendered)

    def test_method_prose_removes_output_notes_and_unchecked_code_equations(
        self,
    ) -> None:
        rendered = reporting._scientific_method_text(
            "1) 读取 CSV；2) 最小二乘拟合 hmi = a * wso + b；"
            "3) 对评估集计算校准值 hmi_cal = a * hmi + b；"
            "4) 输出三个产物文件。"
        )
        self.assertNotIn("表格数据", rendered)
        self.assertIn("候选读数与参考读数之间的线性校正关系", rendered)
        self.assertIn("将该校正关系应用于留出评价数据", rendered)
        self.assertNotRegex(rendered, r"(?:^|[；;])\s*\d+[.)]")
        self.assertNotIn("hmi =", rendered)
        self.assertNotIn("hmi_cal", rendered)
        self.assertNotIn("产物文件", rendered)
        self.assertNotIn(
            "输出",
            reporting._scientific_method_text(
                "读取配对数据；计算误差；输出汇总表格数据与敏感性 JSON"
            ),
        )
        natural = reporting._scientific_method_text(
            "拟合 hmi_candidate_g → wso_reference_g 的线性映射。"
            "输出两张逐行比较表和一份 JSON 汇总。"
        )
        self.assertIn("候选读数到参考读数的线性校正关系", natural)
        self.assertNotIn("hmi_candidate_g", natural)
        self.assertNotIn("wso_reference_g", natural)
        self.assertNotIn("输出", natural)
        self.assertNotIn("JSON", natural)

    def test_long_measurement_table_keeps_primary_and_sensitivity_evidence(
        self,
    ) -> None:
        rows = [
            {"name": f"secondary_{index}", "role": "secondary"}
            for index in range(8)
        ] + [
            {"name": "primary_effect", "role": "primary"},
            {"name": "metric_excl_flagged", "role": "diagnostic"},
            {"name": "metric_sensitivity", "role": "primary"},
        ]
        selected = reporting._report_measurement_selection(
            rows,
            {
                "criteria": [
                    {
                        "statement": "质量标记敏感性分析",
                        "measurement_refs": [
                            "primary_effect",
                            "metric_excl_flagged",
                            "metric_sensitivity",
                        ],
                    }
                ],
                "paired_comparison_audits": [],
            },
        )
        selected_names = {row["name"] for row in selected}
        self.assertTrue(
            {
                "primary_effect",
                "metric_excl_flagged",
                "metric_sensitivity",
            }.issubset(selected_names)
        )
        self.assertLessEqual(len(selected), 12)

    def test_report_selection_collapses_shared_raw_baseline_and_optional_deltas(
        self,
    ) -> None:
        rows = [
            {"name": "raw_a", "role": "secondary", "value": 0.625, "unit": "G"},
            {"name": "condition_a", "role": "primary", "value": 0.044, "unit": "G"},
            {"name": "raw_delta_a", "role": "secondary", "value": 0.581, "unit": "G"},
            {"name": "raw_b", "role": "secondary", "value": 0.625, "unit": "G"},
            {"name": "condition_b", "role": "primary", "value": 0.017, "unit": "G"},
            {"name": "raw_delta_b", "role": "secondary", "value": 0.608, "unit": "G"},
            {"name": "condition_delta", "role": "primary", "value": 0.027, "unit": "G"},
        ]
        design_payload = {
            "normalized_task": "比较两种校正条件的估计及其差异。",
            "measurement_plan": [
                {"name": row["name"], "display_name": row["name"]}
                for row in rows
            ],
            "paired_comparison_audits": [
                {
                    "comparison_kind": "source_baseline_vs_candidate",
                    "source_input_id": "table",
                    "source_target_column": "reference",
                    "source_baseline_column": "raw",
                    "metric": "mae",
                    "baseline_measurement": "raw_a",
                    "candidate_measurement": "condition_a",
                    "delta_measurement": "raw_delta_a",
                },
                {
                    "comparison_kind": "candidate_vs_candidate",
                    "baseline_measurement": "condition_a",
                    "candidate_measurement": "condition_b",
                    "delta_measurement": "condition_delta",
                },
                {
                    "comparison_kind": "source_baseline_vs_candidate",
                    "source_input_id": "table",
                    "source_target_column": "reference",
                    "source_baseline_column": "raw",
                    "metric": "mae",
                    "baseline_measurement": "raw_b",
                    "candidate_measurement": "condition_b",
                    "delta_measurement": "raw_delta_b",
                },
            ],
        }

        selected = reporting._report_measurement_selection(rows, design_payload)
        names = [row["name"] for row in selected]

        self.assertEqual(names.count("raw_a") + names.count("raw_b"), 1)
        self.assertNotIn("raw_delta_a", names)
        self.assertNotIn("raw_delta_b", names)
        self.assertTrue(
            {"condition_a", "condition_b", "condition_delta"}.issubset(names)
        )

        requested = dict(design_payload)
        requested["normalized_task"] = "报告两种条件各自的校正前后改善量。"
        requested_names = {
            row["name"]
            for row in reporting._report_measurement_selection(rows, requested)
        }
        self.assertIn("raw_delta_a", requested_names)
        self.assertIn("raw_delta_b", requested_names)

    def test_ten_measurements_are_all_reported_in_design_order(self) -> None:
        rows = [
            {
                "name": f"estimate_{index}",
                "role": "primary" if index < 3 else "secondary",
            }
            for index in range(10)
        ]
        selected = reporting._report_measurement_selection(rows, {})
        self.assertEqual(
            [row["name"] for row in selected],
            [row["name"] for row in rows],
        )

    def test_report_method_prefers_scientific_narrative_over_stage_workflow(
        self,
    ) -> None:
        rendered = reporting._method_summary(
            {
                "experiment_stages": [
                    {
                        "id": "analysis",
                        "method_outline": (
                            "读取文件、检查列并输出机器可读摘要。"
                        ),
                    }
                ]
            },
            {
                "method": (
                    "按时间顺序划分拟合集与留出集，采用最小二乘法估计"
                    "候选读数到参考读数的线性校正关系，并在留出集上计算"
                    "平均绝对误差。"
                )
            },
            {"analysis"},
        )
        self.assertIn("按时间顺序划分拟合集与留出集", rendered)
        self.assertNotIn("机器可读", rendered)
        self.assertNotIn("输出", rendered)

    def test_verified_comparison_summary_uses_recomputed_values(self) -> None:
        sentence = reporting._verified_primary_comparison_sentence(
            [
                {
                    "id": "cmp",
                    "evaluation_scope": "留出评价集",
                    "row_count": 6,
                    "recomputed_measurements": {
                        "before": 0.65,
                        "after": 0.05,
                        "difference": 0.60,
                    },
                }
            ],
            {
                "paired_comparison_audits": [
                    {
                        "id": "cmp",
                        "baseline_measurement": "before",
                        "candidate_measurement": "after",
                        "delta_measurement": "difference",
                    }
                ],
                "measurement_plan": [
                    {
                        "name": "before",
                        "display_name": "留出评价集校准前均方根误差",
                        "unit": "G",
                    },
                    {
                        "name": "after",
                        "display_name": "留出评价集校准后均方根误差",
                        "unit": "G",
                    },
                    {
                        "name": "difference",
                        "display_name": "误差差值",
                        "unit": "G",
                    },
                ],
            },
        )
        self.assertIn("6 条观测", sentence)
        self.assertIn("校准前均方根误差为 0.65 G", sentence)
        self.assertIn("校准后均方根误差为 0.05 G", sentence)
        self.assertIn("误差差值为 0.6 G", sentence)

    def test_paired_result_uses_named_conditions_in_declared_direction(
        self,
    ) -> None:
        lines = reporting._paired_result_lines(
            [
                {
                    "id": "sensitivity",
                    "evaluation_scope": (
                        "相同留出观测上两种校准条件"
                        "（保留 vs 排除标记观测）的预测值比较"
                    ),
                    "row_count": 6,
                    "all_candidate_absolute_errors_lower": False,
                    "candidate_better_absolute_error_count": 0,
                    "candidate_tied_absolute_error_count": 0,
                    "candidate_worse_absolute_error_count": 6,
                }
            ],
            {
                "measurement_plan": [
                    {
                        "name": "excluded",
                        "display_name": "排除标记观测条件下的平均绝对误差",
                    },
                    {
                        "name": "included",
                        "display_name": "保留标记观测条件下的平均绝对误差",
                    },
                ],
                "paired_comparison_audits": [
                    {
                        "id": "sensitivity",
                        "baseline_measurement": "excluded",
                        "candidate_measurement": "included",
                    }
                ],
            },
            True,
        )
        self.assertIn("在相同的 6 条留出观测中", lines[0])
        self.assertIn("排除标记观测时的绝对误差", lines[0])
        self.assertIn("均低于包含被标记观测时", lines[0])
        self.assertNotIn("分别为 0、0 和 6", lines[0])
        self.assertNotIn("前一条件", lines[0])
        self.assertNotIn("后一条件", lines[0])

    def test_paired_result_does_not_self_compare_raw_condition_labels(self) -> None:
        paired = [
            {
                "id": "raw_a",
                "evaluation_scope": "相同6条留出观测上的比较",
                "row_count": 6,
                "all_candidate_absolute_errors_lower": True,
            },
            {
                "id": "a_b",
                "evaluation_scope": "相同6条留出观测上的比较",
                "row_count": 6,
                "all_candidate_absolute_errors_lower": True,
            },
            {
                "id": "raw_b",
                "evaluation_scope": "相同6条留出观测上的比较",
                "row_count": 6,
                "all_candidate_absolute_errors_lower": True,
            },
        ]
        design_payload = {
            "measurement_plan": [
                {
                    "name": "raw_a",
                    "display_name": "条件 A 未校正基准平均绝对误差",
                    "scientific_meaning": "原始读数误差。",
                },
                {
                    "name": "condition_a",
                    "display_name": "条件 A 校正后平均绝对误差",
                    "scientific_meaning": "保留标记观测时的误差。",
                },
                {
                    "name": "raw_b",
                    "display_name": "条件 B 未校正基准平均绝对误差",
                    "scientific_meaning": "原始读数误差。",
                },
                {
                    "name": "condition_b",
                    "display_name": "条件 B 校正后平均绝对误差",
                    "scientific_meaning": "排除标记观测时的误差。",
                },
            ],
            "paired_comparison_audits": [
                {
                    "id": "raw_a",
                    "comparison_kind": "source_baseline_vs_candidate",
                    "baseline_measurement": "raw_a",
                    "candidate_measurement": "condition_a",
                },
                {
                    "id": "a_b",
                    "comparison_kind": "candidate_vs_candidate",
                    "baseline_measurement": "condition_a",
                    "candidate_measurement": "condition_b",
                },
                {
                    "id": "raw_b",
                    "comparison_kind": "source_baseline_vs_candidate",
                    "baseline_measurement": "raw_b",
                    "candidate_measurement": "condition_b",
                },
            ],
        }

        lines = reporting._paired_result_lines(paired, design_payload, True)
        rendered = "\n".join(lines)

        self.assertEqual(len(lines), 2)
        self.assertIn("未校正读数", lines[0])
        self.assertIn("无论包含还是排除被标记观测", lines[0])
        self.assertIn(
            "排除被标记观测时的绝对误差均低于包含被标记观测时",
            lines[1],
        )
        self.assertNotRegex(
            rendered,
            r"条件\s*([AB])\s*的绝对误差均低于条件\s*\1",
        )

        actual_scope_lines = reporting._paired_result_lines(
            [
                {
                    "id": "source",
                    "evaluation_scope": "留出观测上原始候选读数与参考读数的比较",
                    "row_count": 6,
                    "all_candidate_absolute_errors_lower": True,
                },
                {
                    "id": "sensitivity",
                    "evaluation_scope": (
                        "留出观测上保留与排除标记观测两种校正条件的比较"
                    ),
                    "row_count": 6,
                    "all_candidate_absolute_errors_lower": True,
                },
            ],
            {
                "measurement_plan": [
                    {
                        "name": "raw",
                        "display_name": "未校正留出集平均绝对误差",
                    },
                    {
                        "name": "calibrated",
                        "display_name": "校正后留出集平均绝对误差",
                    },
                    {
                        "name": "included",
                        "display_name": "保留标记观测条件下留出集平均绝对误差",
                    },
                    {
                        "name": "excluded",
                        "display_name": "排除标记观测条件下留出集平均绝对误差",
                    },
                ],
                "paired_comparison_audits": [
                    {
                        "id": "source",
                        "baseline_measurement": "raw",
                        "candidate_measurement": "calibrated",
                    },
                    {
                        "id": "sensitivity",
                        "baseline_measurement": "included",
                        "candidate_measurement": "excluded",
                    },
                ],
            },
            True,
        )
        self.assertTrue(
            all(
                line.startswith("在相同的 6 条留出观测中")
                for line in actual_scope_lines
            )
        )
        self.assertIn("校正后读数的绝对误差", actual_scope_lines[0])
        self.assertNotIn("比较的全部", " ".join(actual_scope_lines))

        condition_lines = reporting._paired_result_lines(
            [
                {
                    "id": "conditions",
                    "evaluation_scope": (
                        "在相同6条留出观测上比较两种拟合条件的线性校正值"
                    ),
                    "row_count": 6,
                    "all_candidate_absolute_errors_lower": True,
                }
            ],
            {
                "measurement_plan": [
                    {
                        "name": "included",
                        "display_name": "条件A（保留标记行）留出集MAE",
                    },
                    {
                        "name": "excluded",
                        "display_name": "条件B（排除该标记观测）留出集MAE",
                    },
                ],
                "paired_comparison_audits": [
                    {
                        "id": "conditions",
                        "baseline_measurement": "included",
                        "candidate_measurement": "excluded",
                    }
                ],
            },
            True,
        )
        self.assertEqual(
            condition_lines,
            [
                "在相同的 6 条留出观测中，排除该标记观测时的绝对误差"
                "均低于包含被标记观测时的绝对误差。"
            ],
        )

    def test_scientific_reader_text_removes_handoff_and_ungrounded_impact_jargon(
        self,
    ) -> None:
        rendered = reporting._scientific_reader_text(
            "数据为合成演示夹具数据数据，其中一行被被标记。"
            "上游同时称其为模拟夹具。"
            "排除该行后差异不具有实质影响，且与原结果略有差异。"
            "非零值表示质量标记对结果有实质影响。"
        )
        self.assertIn("合成演示数据", rendered)
        self.assertIn("模拟数据", rendered)
        self.assertIn("不足以判断该差异的实际意义", rendered)
        self.assertIn("与原结果存在差异", rendered)
        self.assertNotIn("夹具", rendered)
        self.assertNotIn("数据数据", rendered)
        self.assertNotIn("被被", rendered)
        self.assertNotIn("科学意义", rendered)
        self.assertNotIn("非零值表示", rendered)

        polished = reporting._scientific_reader_text(
            "但样本量小、数据为合成性质，证据强度有限。"
            "校正参数发生变化，表明该标记观测对拟合参数有可观测的影响。"
            "仅 18 条配对观测（拟合 12 个、留出 6 个），统计能力有限。"
        )
        self.assertIn("数据为合成数据", polished)
        self.assertIn("拟合参数会随该观测是否纳入拟合而变化", polished)
        self.assertNotIn("有可观测的影响", polished)
        self.assertIn(
            "其中 12 条用于拟合、6 条用于留出评价",
            polished,
        )
        self.assertNotIn("统计能力有限", polished)

        live_summary = reporting._scientific_reader_text(
            "研究目标是在合成重叠期数据中配对数据上比较校准。"
            "校准参数在两个条件下保持稳定：斜率分别为 0.83 和 0.84。"
            "标记观测对校准函数和误差估计的影响方向一致且量级较小。"
        )
        self.assertIn("合成重叠期配对数据上", live_summary)
        self.assertIn("两种拟合条件下的校准参数分别为", live_summary)
        self.assertIn("实际重要性尚不能由本设计判断", live_summary)
        self.assertNotIn("保持稳定", live_summary)
        self.assertNotIn("量级较小", live_summary)

        row_wording = reporting._main_report_text(
            ["# 局限性", "", "仅一条单一标记行用于敏感性分析。"]
        )
        self.assertIn("一条被标记观测", row_wording)
        self.assertNotIn("标记行", row_wording)

        polished_calibration = reporting._reader_facing_plan_text(
            "以全部行拟合校准后，在相同 6 条冻结留出观测上评价。",
            {
                "paired_comparison_audits": [
                    {
                        "comparison_kind": "candidate_vs_candidate",
                        "fit_evaluation_relation": "disjoint_rows",
                        "baseline_fit_condition": "包含全部拟合观测",
                        "candidate_fit_condition": "排除标记拟合观测",
                    }
                ]
            },
        )
        polished_calibration = reporting._scientific_reader_text(
            polished_calibration
        )
        self.assertIn("包含被标记观测的拟合并校准后", polished_calibration)
        self.assertIn("相同的 6 条固定留出评估观测", polished_calibration)
        self.assertNotIn("校准后后", polished_calibration)

        scope, intro = reporting._paired_scope_and_intro(
            "两种拟合条件在相同时间顺序留出的后期重叠期观测上的比较",
            6,
            True,
        )
        self.assertEqual(scope, "相同的 6 条留出观测")
        self.assertEqual(intro, "在相同的 6 条留出观测中")

        delta_display, delta_definition = reporting._paired_delta_report_text(
            "mae_between_conditions",
            {
                "paired_comparison_audits": [
                    {
                        "comparison_kind": "candidate_vs_candidate",
                        "metric": "mae",
                        "baseline_fit_condition": "all_data",
                        "candidate_fit_condition": "exclude_flagged",
                        "delta_measurement": "mae_between_conditions",
                        "delta_formula": "candidate_minus_baseline",
                    }
                ]
            },
        )
        self.assertEqual(
            delta_display,
            "平均绝对误差差值（排除标记观测拟合后 − 包含被标记观测拟合后）",
        )
        self.assertEqual(
            delta_definition,
            "在相同评价观测上，排除标记观测拟合后的平均绝对误差减去"
            "包含被标记观测拟合后的平均绝对误差",
        )
        ordered = reporting._order_report_measurements(
            [
                {"name": "condition_delta"},
                {"name": "candidate_value"},
                {"name": "baseline_value"},
                {"name": "other_parameter"},
            ],
            {
                "measurement_plan": [
                    {"name": "condition_delta"},
                    {"name": "candidate_value"},
                    {"name": "baseline_value"},
                    {"name": "other_parameter"},
                ],
                "paired_comparison_audits": [
                    {
                        "baseline_measurement": "baseline_value",
                        "candidate_measurement": "candidate_value",
                        "delta_measurement": "condition_delta",
                    }
                ],
            },
        )
        self.assertEqual(
            [row["name"] for row in ordered],
            [
                "baseline_value",
                "candidate_value",
                "condition_delta",
                "other_parameter",
            ],
        )

        natural_delta = reporting._scientific_reader_text(
            "保留条件平均绝对误差 0.0440 G，排除条件平均绝对误差 0.0174 G；"
            "两条件差（排除减保留）-0.0266 G。"
        )
        self.assertIn(
            "排除条件的平均绝对误差比保留条件低 0.0266 G",
            natural_delta,
        )
        self.assertNotIn("排除减保留）-", natural_delta)

    def test_scientific_result_format_uses_report_conventions(self) -> None:
        self.assertEqual(
            reporting._format_measurement({"value": 0.834765, "unit": "无单位"}),
            "0.8348",
        )
        self.assertEqual(
            reporting._format_typed_result(
                {
                    "value": 2.5,
                    "value_kind": "number",
                    "unit": "unitless",
                }
            ),
            "2.5",
        )
        self.assertEqual(
            reporting._scientific_reader_text("平均绝对误差之差值为 0.0266 G"),
            "平均绝对误差差值为 0.0266 G",
        )
        self.assertEqual(
            reporting._scientific_reader_text(
                "在 6 条时间顺序留出观测（后 6 条）中比较绝对误差"
            ),
            "在后 6 条时间顺序留出观测中比较绝对误差",
        )
        acceptance_wording = reporting._scientific_reader_text(
            "本研究量化排除被标记观测对线性校正参数与留出预测误差的影响。"
            "校正斜率由0.8348变为0.8442，二者均远低于未校准基线。"
            "样本量限制了参数估计的精度。"
            "未进行统计显著性检验，差值方向与幅度仅供描述性参考。"
        )
        self.assertIn("比较保留与排除被标记观测时", acceptance_wording)
        self.assertIn("由 0.8348 变为 0.8442", acceptance_wording)
        self.assertNotIn("的影响", acceptance_wording)
        self.assertNotIn("远低于", acceptance_wording)
        self.assertNotIn("限制了参数估计的精度", acceptance_wording)
        self.assertNotIn("统计显著性检验", acceptance_wording)
        self.assertIn("未量化估计不确定性", acceptance_wording)
        self.assertEqual(
            reporting._scientific_reader_text(
                "截距由-0.4032 G变为-0.3752 G"
            ),
            "截距由 -0.4032 G 变为 -0.3752 G",
        )

        named_delta = reporting._scientific_reader_text(
            "平均绝对误差由 0.0440 G 降至 0.0174 G，"
            "差值为 −0.0266 G（排除减包含）。"
        )
        self.assertIn(
            "排除条件的平均绝对误差比包含条件低 0.0266 G",
            named_delta,
        )
        self.assertNotIn("差值为 −", named_delta)

        neutral_delta = reporting._scientific_reader_text(
            "全条件平均绝对误差减去排除条件平均绝对误差；"
            "正值表示排除后恶化，负值表示排除后改善。"
        )
        self.assertEqual(
            neutral_delta,
            "全条件平均绝对误差减去排除条件平均绝对误差。",
        )

    def test_scientific_reader_text_uses_observations_and_scientific_variables(
        self,
    ) -> None:
        rendered = reporting._scientific_reader_text(
            "合成数据中的 18 行配对观测。参考坐标列（G）与候选读数列（G）"
            "逐行配对，其中 1 行标记为异常几何。前 12 行（2010 至 2021）"
            "作为拟合集，后 6 行（2022 至 2025）作为留出集；条件 A 使用"
            "全部观测（含标记行），条件 B 使用 11 行拟合。包含条件的 "
            "OLS 斜率为 0.8。6 个留出观测的结果采用逐行比较。"
        )
        self.assertIn("18 条配对观测", rendered)
        self.assertIn("参考坐标与候选读数（单位均为 G）按观测一一对应", rendered)
        self.assertIn("1 条观测被标记为几何条件异常", rendered)
        self.assertIn("前 12 条观测", rendered)
        self.assertIn("后 6 条观测", rendered)
        self.assertIn("包含该标记观测", rendered)
        self.assertIn("使用 11 条观测拟合", rendered)
        self.assertIn("6 条留出观测", rendered)
        self.assertIn("逐个观测比较", rendered)
        self.assertIn("普通最小二乘斜率", rendered)
        self.assertNotIn("参考坐标列", rendered)
        self.assertNotIn("的 普通", rendered)
        self.assertNotIn("逐行", rendered)

        quality_wording = reporting._scientific_reader_text(
            "1条观测被标记为可疑几何，质量标记行位于拟合集中；"
            "条件A含质量标记行，条件B不含质量标记行。"
        )
        self.assertIn("被标记为几何条件可疑", quality_wording)
        self.assertIn("被标记观测位于拟合集中", quality_wording)
        self.assertIn("包含被标记观测", quality_wording)
        self.assertIn("不包含被标记观测", quality_wording)
        self.assertNotIn("质量标记行", quality_wording)

        importance_wording = reporting._scientific_reader_text(
            "差值约为60%，表明该观测对拟合的影响不可忽视。"
        )
        self.assertIn("仅描述当前样本中的相对变化", importance_wording)
        self.assertIn("不能据此判断其实际重要性", importance_wording)
        self.assertNotIn("不可忽视", importance_wording)

        contrast_wording = reporting._scientific_reader_text(
            "斜率从0.8348（保留）变为0.8442（排除），差值−0.0094；"
            "截距从−0.4032 G变为−0.3752 G，差值−0.0280 G。"
        )
        self.assertIn("保留减排除为 −0.0094", contrast_wording)
        self.assertIn("保留减排除为 −0.0280 G", contrast_wording)
        self.assertNotIn("，差值−", contrast_wording)

        live_report_wording = reporting._scientific_reader_text(
            "合成演示数据重叠期跨仪器线性校正的质量标记敏感性分析。"
            "研究目标是在合成演示数据重叠期数据上评估校正。"
            "参数变化幅度较小，但误差改善方向在所有留出观测上一致。"
            "敏感性分析同时给出两种条件各自的估计量及其差值。"
            "本分析仅限于合成演示数据 18 行观测。"
            "时间切分点（前使用 12 条观测拟合、后 6 行留出）为实用决策。"
        )
        self.assertIn("：对标记观测的敏感性分析", live_report_wording)
        self.assertIn("合成演示数据的重叠期观测", live_report_wording)
        self.assertIn(
            "参数差异的实际重要性尚不能由本设计判断",
            live_report_wording,
        )
        self.assertIn(
            "前 12 条观测用于拟合、后 6 条观测用于留出评价",
            live_report_wording,
        )
        self.assertNotIn("参数变化幅度较小", live_report_wording)
        self.assertNotIn("敏感性分析同时给出", live_report_wording)
        self.assertNotIn("18 行观测", live_report_wording)

        final_report_wording = reporting._scientific_reader_text(
            "排除可疑行后线性回归斜率。"
            "该差值衡量质量标记对校准效果的影响。"
            "仅 18 条观测且评估集仅 6 条观测，统计精度有限，"
            "影响校准效果估计的可靠性。"
            "线性校准模型可能无法捕获仪器间的非线性差异，"
            "影响对校准充分性的判断。"
            "尝试多项式回归或稳健回归方法，检验线性假设是否充分。"
            "扩大样本量以提高统计精度，尤其增加留出评估集的观测数量。"
        )
        self.assertIn("排除可疑观测后线性回归斜率", final_report_wording)
        self.assertIn("被标记观测是否纳入拟合", final_report_wording)
        self.assertIn("误差估计仅反映这 6 条留出观测", final_report_wording)
        self.assertIn("本研究只评估线性校准", final_report_wording)
        self.assertIn("只有观察到非线性或异常值结构时", final_report_wording)
        self.assertIn("更多独立的重叠期观测", final_report_wording)
        self.assertNotIn("可疑行", final_report_wording)
        self.assertNotIn("校准效果的影响", final_report_wording)

        correlation_wording = reporting._scientific_reader_text(
            "在当前观测样本中，两组读数呈现极强的正向线性关联："
            "参考读数从负值增至正值时，候选读数也沿相同方向单调递增。"
            "当前样本上的相关系数接近 1，线性关联模式清晰。"
            "该行对相关系数的影响未经敏感性检验。"
            "原始数据未注明计量单位，数值含义不明。"
        )
        self.assertIn("在当前观测样本中，两组读数呈正向线性关联", correlation_wording)
        self.assertIn("线性变化方向一致", correlation_wording)
        self.assertIn("当前样本中的相关系数为正", correlation_wording)
        self.assertIn("该观测纳入或排除时", correlation_wording)
        self.assertIn("相关系数的无量纲性质", correlation_wording)
        for ungrounded in ("极强", "单调递增", "模式清晰", "该行"):
            self.assertNotIn(ungrounded, correlation_wording)
        self.assertNotIn("观测样本中，两组读数在当前样本中", correlation_wording)

        correlation_next_steps = reporting._scientific_follow_up(
            [
                "使用真实观测数据复核当前相关方向",
                "对可疑观测进行排除后的敏感性分析",
                "评估标记观测对相关系数的影响",
                "考察时序自相关对有效自由度的影响",
            ],
            frame={"deferred_questions": []},
            limitations=[],
            objective="计算 Pearson 相关系数、方向和有效样本数",
        )
        self.assertEqual(
            correlation_next_steps,
            ["使用真实观测数据复核当前相关方向"],
        )
        self.assertEqual(
            reporting._format_typed_result(
                {
                    "display_name": "相关方向",
                    "scientific_meaning": "Pearson 相关系数的符号",
                    "value_kind": "category",
                    "value": "positive",
                    "unit": "",
                }
            ),
            "正相关",
        )
        correlation_limitations = reporting._scientific_limitations(
            [
                "样本量仅 18，相关系数的抽样不确定性未做估计",
                "1 条观测标记为可疑几何特征，该观测可能影响相关系数",
                "未考虑时序自相关对有效自由度的影响",
                "数据为合成演示数据，不代表真实物理观测",
            ],
            frame={"deferred_questions": []},
            objective="计算 Pearson 相关系数、方向和有效样本数",
        )
        self.assertEqual(
            correlation_limitations,
            ["数据为合成演示数据，不代表真实物理观测"],
        )
        correlation_discussion = reporting._scientific_discussion_rows(
            [
                "相关系数基于全部 18 条配对观测确定性计算，无缺失值。"
                "1 条观测标记为可疑几何特征，可能影响系数估计。"
                "结论只描述当前样本，不建立因果关系。"
            ],
            objective="计算 Pearson 相关系数、方向和有效样本数",
        )
        self.assertEqual(
            correlation_discussion,
            ["结论只描述当前样本，不建立因果关系"],
        )

        discussion_wording = reporting._scientific_reader_text(
            "全部 6 条留出观测中排除条件的绝对误差均更低，"
            "这一差异在全部留出观测上的方向一致。"
            "仅 18 条合成观测，校正参数稳定性有限。"
            "不同切分可能产生不同的误差差异。"
            "标记观测位于拟合集中段（2014 年 6 月），"
            "其杠杆效应可能对拟合产生局部影响。"
        )
        self.assertIn(
            "当前留出结果会随该观测是否纳入拟合而变化",
            discussion_wording,
        )
        self.assertIn(
            "样本量不足以充分评价校正参数在其他时段的稳定性",
            discussion_wording,
        )
        self.assertIn("不同切分可能改变误差差值", discussion_wording)
        self.assertIn("本次未单独估计其杠杆值", discussion_wording)
        self.assertNotIn("杠杆效应可能", discussion_wording)

        table_wording = reporting._scientific_reader_text(
            "包含参考坐标读数和候选仪器读数各一列，其中一行标记为几何异常；"
            "实际表格结构：18 行、5 列，未发现空值。"
        )
        self.assertIn("参考坐标读数和候选仪器读数两个变量", table_wording)
        self.assertIn("其中一条观测被标记为几何条件异常", table_wording)
        self.assertIn("共 18 条观测，记录 5 个变量", table_wording)
        self.assertNotRegex(table_wording, r"\d+\s*(?:行|列)")

        method_wording = reporting._scientific_reader_text(
            "分别在包含全部 12 行校准观测和排除 1 行标记观测两种条件下拟合，"
            "并在同一 6 行留出评价集上比较。"
        )
        self.assertIn("校准集包含全部 12 条观测", method_wording)
        self.assertIn("排除 1 条被标记观测", method_wording)
        self.assertIn("同一组 6 条留出评估观测", method_wording)
        self.assertNotIn("行", method_wording)

        bounded_wording = reporting._scientific_reader_text(
            "不代表真实仪器噪声。敏感性分析的证据力度有限。"
        )
        self.assertEqual(
            bounded_wording,
            "这些结果不代表真实仪器噪声。"
            "敏感性分析只反映当前被标记观测是否纳入拟合时的差异。",
        )

        polished = reporting._scientific_reader_text(
            "平均绝对误差之保留条件比排除条件高 0.0266 G。"
            "留出集仅 6 对观测，平均绝对误差之差值 0.0266 G 的精度受样本量限制，"
            "不排除不同分割方案下结果方向可能改变。"
            "增加留出集观测数量以提高误差差值的精度。"
        )
        self.assertIn(
            "保留条件的平均绝对误差比排除条件高 0.0266 G",
            polished,
        )
        self.assertIn("尚未检验不同时间分割方案", polished)
        self.assertIn("在更多独立的重叠期观测上复核", polished)
        self.assertNotIn("精度受样本量限制", polished)

        process_wording = reporting._scientific_reader_text(
            "确定性计算结果，所有数值由代码直接产出并经独立核验复算。"
            "逐个观测的比较结果表记录了6条留出观测的参考值、"
            "两种条件校正值和未校正偏差，支持逐对比较。"
            "线性模型为有界近似，未评估非线性残差或异方差结构。"
        )
        self.assertIn("本分析为描述性计算", process_wording)
        self.assertIn("可进行配对比较", process_wording)
        self.assertIn("尚未检验非线性或异方差结构", process_wording)
        for internal in ("代码", "独立核验", "结果表", "有界近似"):
            self.assertNotIn(internal, process_wording)

    def test_abstract_result_omits_repeated_pointwise_claim(self) -> None:
        rendered = reporting._concise_abstract_result(
            "6条留出观测中，排除条件的绝对误差逐行低于保留条件。"
            "斜率从0.83变为0.84。"
            "排除标记行使留出集上每条观测的绝对误差均下降。"
        )
        self.assertEqual(rendered.count("绝对误差"), 1)
        self.assertIn("斜率从 0.83 变为 0.84", rendered)
        self.assertNotIn("逐行", rendered)

    def test_abstract_result_preserves_decimal_values(self) -> None:
        rendered = reporting._concise_abstract_result(
            "条件 A 的平均绝对误差从 0.6250 G 降至 0.0440 G，"
            "条件 B 从 0.6250 G 降至 0.0174 G。"
            "全部 6 条留出观测中两种校正的绝对误差均更低。"
            "排除标记观测后，平均绝对误差进一步降低 0.0266 G，"
            "且全部 6 条留出观测中条件 B 的绝对误差均更低。"
        )

        self.assertIn("0.0440 G", rendered)
        self.assertIn("0.0174 G", rendered)
        self.assertIn("0.0266 G", rendered)
        self.assertNotIn("0.条件", rendered)

    def test_abstract_detects_when_verified_paired_values_are_missing(
        self,
    ) -> None:
        paired = [{"id": "comparison"}]
        design_payload = {
            "paired_comparison_audits": [
                {
                    "id": "comparison",
                    "baseline_measurement": "before",
                    "candidate_measurement": "after",
                }
            ]
        }
        measurements = {
            "before": {"value": 0.043993},
            "after": {"value": 0.0174353},
        }
        self.assertFalse(
            reporting._conclusion_has_primary_comparison_values(
                "排除后平均绝对误差降低。",
                paired,
                design_payload,
                measurements,
            )
        )
        self.assertTrue(
            reporting._conclusion_has_primary_comparison_values(
                "平均绝对误差由 0.04399 G 降至 0.01744 G。",
                paired,
                design_payload,
                measurements,
            )
        )
        self.assertTrue(
            reporting._conclusion_has_primary_comparison_values(
                "条件 A 的平均绝对误差为 0.044 G，条件 B 为 0.017 G。",
                paired,
                design_payload,
                measurements,
            )
        )

    def test_result_definition_follows_candidate_to_reference_direction(
        self,
    ) -> None:
        rendered = reporting._scientific_result_definition(
            "保留条件下候选读数关于参考读数的最小二乘回归斜率",
            {
                "paired_comparison_audits": [
                    {
                        "candidate_model_input_columns": [
                            "candidate_measurement"
                        ],
                        "candidate_model_target_column": "reference_measurement",
                    }
                ]
            },
        )
        self.assertEqual(
            rendered,
            "保留条件下以候选读数为自变量、参考读数为因变量的最小二乘回归斜率",
        )

        design_payload = {
            "measurement_plan": [
                {
                    "display_name": "条件 A 校正斜率",
                    "scientific_meaning": "保留标记条件下的校正斜率",
                },
                {
                    "display_name": "条件 B 校正斜率",
                    "scientific_meaning": "排除标记条件下的校正斜率",
                },
            ]
        }
        self.assertEqual(
            reporting._scientific_result_display(
                "条件 A 校正斜率",
                "保留标记条件下的校正斜率",
            ),
            "包含被标记观测时的校正斜率",
        )
        self.assertEqual(
            reporting._scientific_result_definition(
                "条件 A 减条件 B 的斜率差值",
                design_payload,
            ),
            "包含被标记观测减排除标记观测的斜率差值",
        )

    def test_semantic_condition_names_replace_opaque_report_codes(self) -> None:
        design_payload = {
            "measurement_plan": [
                {
                    "name": "estimate_a",
                    "display_name": "条件 A 校正后平均绝对误差",
                    "scientific_meaning": "保留标记观测时的留出误差",
                },
                {
                    "name": "estimate_b",
                    "display_name": "条件 B 校正后平均绝对误差",
                    "scientific_meaning": "排除标记观测时的留出误差",
                },
            ]
        }
        rendered = reporting._reader_facing_plan_text(
            "条件 A 使用全部观测，条件 B 排除标记观测；差值为 A 减 B。",
            design_payload,
        )
        self.assertNotIn("条件 A", rendered)
        self.assertNotIn("条件 B", rendered)
        self.assertIn("包含被标记观测条件", rendered)
        self.assertIn("排除标记观测条件", rendered)
        self.assertIn("包含被标记观测减排除标记观测", rendered)

    def test_discussion_excludes_engineering_provenance_sentences(self) -> None:
        rendered = reporting._scientific_discussion_rows(
            [
                "各项结果均按最小二乘公式计算。"
                "三组比较均使用相同的 6 条留出观测。"
                "结果的适用范围受样本量限制。",
                "上述结果仅适用于当前合成数据，不可外推至真实观测。",
            ]
        )
        joined = "。".join(rendered)
        self.assertNotIn("按最小二乘公式计算", joined)
        self.assertNotIn("三组比较均使用", joined)
        self.assertIn("不可外推至真实观测", joined)

    def test_reader_text_does_not_overstate_parameter_sensitivity(self) -> None:
        rendered = reporting._scientific_reader_text(
            "校准参数对样本选择高度敏感，说明拟合不稳定。"
        )
        self.assertEqual(
            rendered,
            "不同样本处理下的参数估计存在差异，说明拟合结果在不同样本处理下"
            "存在差异，稳定性尚未充分评估。",
        )
        self.assertNotIn("高度敏感", rendered)
        self.assertNotIn("拟合不稳定", rendered)

    def test_reader_text_humanizes_fitting_and_evaluation_rows(self) -> None:
        rendered = reporting._scientific_reader_text(
            "18 行合成演示配对观测按时间顺序划分。排除可疑标记训练行后，"
            "在清洁评估行上进行留前评估。单一可疑标记行限制了敏感性分析的统计力度。"
        )
        self.assertIn("18 组配对的合成演示观测", rendered)
        self.assertIn("被标记的拟合观测", rendered)
        self.assertIn("固定的留出评估观测", rendered)
        self.assertIn("时间顺序留出", rendered)
        self.assertIn("只反映该观测是否纳入拟合时的变化", rendered)
        self.assertNotIn("标记行", rendered)
        self.assertNotIn("评估行", rendered)

    def test_criterion_bound_diagnostic_result_is_kept_in_main_report(self) -> None:
        record = {
            "outcome": "completed_interpretable",
            "task": "比较两种拟合条件的校准参数。",
            "outcome_reason": "分析完成。",
            "stage_history": [
                {
                    "result_summary": {
                        "measurements": [
                            {
                                "name": "mae",
                                "value": 0.1,
                                "unit": "G",
                                "role": "primary",
                                "source_artifact": None,
                            }
                        ],
                        "result_items": [
                            {
                                "id": "slope",
                                "display_name": "校准斜率",
                                "value_kind": "number",
                                "value": 0.84,
                                "unit": "G/G",
                                "role": "diagnostic",
                                "source_artifact": None,
                            },
                            {
                                "id": "total_rows",
                                "display_name": "总观测行数",
                                "value_kind": "count",
                                "value": 18,
                                "unit": "",
                                "role": "diagnostic",
                                "source_artifact": None,
                            },
                        ],
                    }
                }
            ],
            "scientific_assessment": {
                "report_narrative": {
                    "title": "校准参数比较",
                    "objective": "比较两种拟合条件的校准参数",
                    "data_scope": "当前合成数据",
                    "method": "在固定评价观测上比较两种拟合条件",
                    "interpretation": "当前条件下平均绝对误差为 0.1 G，斜率为 0.84 G/G",
                    "evidence_strength": "结果只描述当前输入",
                    "claim_boundary": "不外推到真实仪器",
                    "limitations": ["样本规模有限"],
                    "next_steps": ["增加独立评价观测"],
                }
            },
            "evidence_ledger": {
                "research_frame": {"deferred_questions": []},
                "paired_comparisons": [],
            },
        }
        design_payload = {
            "normalized_task": record["task"],
            "research_frame": {"deferred_questions": []},
            "measurement_plan": [
                {
                    "name": "mae",
                    "display_name": "平均绝对误差",
                    "scientific_meaning": "固定评价观测上的平均绝对误差",
                    "role": "primary",
                }
            ],
            "result_plan": [
                {
                    "id": "slope",
                    "display_name": "校准斜率",
                    "scientific_meaning": "拟合关系的斜率",
                    "role": "diagnostic",
                },
                {
                    "id": "total_rows",
                    "display_name": "总观测行数",
                    "scientific_meaning": "当前输入的观测总数",
                    "role": "diagnostic",
                },
            ],
            "criteria": [{"result_refs": ["slope", "total_rows"]}],
            "experiment_stages": [],
        }
        rendered = reporting.render_report(record, design=design_payload)
        self.assertIn("# 校准参数比较", rendered)
        self.assertIn("| 校准斜率 | 0.84 G/G |", rendered)
        self.assertNotIn("| 观测总数 |", rendered)
        self.assertNotIn("# 实验分析报告", rendered)

    def test_main_result_table_omits_diagnostic_measurements_when_answers_exist(
        self,
    ) -> None:
        selected = reporting._report_measurement_selection(
            [
                {"name": "mae", "role": "primary"},
                {"name": "fit_count", "role": "diagnostic"},
                {"name": "holdout_count", "role": "diagnostic"},
            ],
            None,
        )
        self.assertEqual([row["name"] for row in selected], ["mae"])

    def test_follow_up_excludes_deferred_parent_question(self) -> None:
        frame = {
            "deferred_questions": [
                "跨活动周前兆关系检验（缺少周期级目标变量）",
                "真实观测性能声明",
                "跨仪器一致性改善的物理机制归因",
            ]
        }
        limitations = reporting._scientific_limitations(
            [
                "留出观测较少，误差估计可能对时间划分敏感",
                "合成数据不代表真实仪器的噪声结构",
                "未包含周期级目标变量，无法评估前兆关系的下游影响",
                "参考坐标不是绝对真值，不能进行物理机制归因",
            ],
            frame=frame,
            objective="比较两种校正条件在相同留出观测上的误差",
        )
        rendered = reporting._scientific_follow_up(
            [
                "若需回答完整的前兆关系问题，需获取下一活动周振幅等周期级目标变量",
                "若需检验校正在其他时间段是否同样有效，需更多独立时间段的配对观测数据",
                "若需评估真实仪器性能，需使用经独立校准的实际观测记录",
                "若需判断质量标记观测的物理成因，需补充仪器状态元数据",
            ],
            frame=frame,
            limitations=limitations,
            objective="比较两种校正条件在相同留出观测上的误差",
        )
        self.assertFalse(any("周期级目标变量" in row for row in limitations))
        self.assertFalse(any("物理机制归因" in row for row in limitations))
        self.assertNotIn("前兆关系", " ".join(rendered))
        self.assertNotIn("物理成因", " ".join(rendered))
        self.assertEqual(len(rendered), 2)
        self.assertTrue(any("其他时间段" in row for row in rendered))
        self.assertTrue(any("真实仪器" in row for row in rendered))

    def test_main_report_layout_and_meta_language_are_clean(self) -> None:
        rendered = reporting._main_report_text(
            ["# 标题", "", "", "## 结果", "", "观察值。", ""]
        )
        self.assertNotIn("\n\n\n", rendered)
        self.assertTrue(rendered.endswith("\n"))
        observation_language = reporting._main_report_text(
            [
                "18行时间顺序配对观测；留出集仅6行；"
                "结果可能受单行扰动影响。"
            ]
        )
        self.assertIn("18 对时间顺序配对观测", observation_language)
        self.assertIn("留出集仅含 6 条观测", observation_language)
        self.assertIn("单个观测的变化", observation_language)
        self.assertNotIn("行", observation_language)
        with self.assertRaisesRegex(RuntimeError, "internal workflow terms"):
            reporting._validate_main_report_quality(
                "# 结果\n\n逐行比较提供了确定性数值证据。\n"
            )
        with self.assertRaisesRegex(RuntimeError, "table dimensions"):
            reporting._validate_main_report_quality(
                "# 数据与方法\n\n实际表格包含 18 行、5 列。\n"
            )
        with self.assertRaisesRegex(RuntimeError, "internal workflow terms"):
            reporting._validate_main_report_quality(
                "# 讨论\n\n所有数值由代码直接产出并经独立核验。\n"
            )
        fallback = reporting._fallback_main_report(
            [
                "| 指标 | 估计值 | 指标定义 |",
                "|---|---:|---|",
                "| 平均绝对误差 | 0.12 G | 当前观测上的平均绝对误差 |",
            ],
            has_verified_results=True,
        )
        reporting._validate_main_report_quality(fallback)
        self.assertIn("平均绝对误差", fallback)
        self.assertNotIn("当前状态", fallback)

    def test_condition_contrast_summary_reports_both_estimates_and_difference(
        self,
    ) -> None:
        design_payload = {
            "measurement_plan": [
                {
                    "name": "estimate_a",
                    "display_name": "包含标记观测时的改善量",
                    "unit": "G",
                    "scientific_meaning": "包含标记观测时的误差改善量。",
                },
                {
                    "name": "estimate_b",
                    "display_name": "排除标记观测时的改善量",
                    "unit": "G",
                    "scientific_meaning": "排除标记观测时的误差改善量。",
                },
                {
                    "name": "estimate_difference",
                    "display_name": "两种条件的改善量差值",
                    "unit": "G",
                    "scientific_meaning": "包含条件减去排除条件的差值。",
                },
            ],
            "criteria": [
                {
                    "statement": "报告两种条件的改善量差值",
                    "measurement_refs": [
                        "estimate_a",
                        "estimate_b",
                        "estimate_difference",
                    ],
                }
            ],
        }
        observed = {
            "estimate_a": {"value": 0.58, "unit": "G"},
            "estimate_b": {"value": 0.67, "unit": "G"},
            "estimate_difference": {"value": -0.09, "unit": "G"},
        }
        rendered = reporting._verified_condition_contrast_sentence(
            design_payload,
            observed,
        )
        self.assertIn("包含标记观测时的改善量为 0.58 G", rendered)
        self.assertIn("排除标记观测时的改善量为 0.67 G", rendered)
        self.assertIn("两种条件的改善量差值为 -0.09 G", rendered)

    def test_historical_mse_label_is_repaired_for_signed_error(self) -> None:
        rendered = reporting._scientific_result_display(
            "完整评估集 MSE 变化量",
            "完整评估集上平均有符号误差的变化",
        )
        self.assertEqual(rendered, "完整评估集平均有符号误差变化量")
        self.assertNotIn("MSE", rendered)
        self.assertTrue(
            reporting._historical_mse_means_signed_error(
                {
                    "measurement_plan": [
                        {
                            "name": "mse_calibrated",
                            "display_name": "校准后平均有符号误差",
                            "scientific_meaning": "平均有符号误差",
                        }
                    ]
                }
            )
        )

    def test_historical_untraceable_cutoff_is_not_repeated_as_a_finding(self) -> None:
        design_payload = {
            "criteria": [
                {
                    "basis_kind": "method_standard",
                    "statement": "误差低于 0.5 G",
                }
            ]
        }
        rendered = reporting._remove_untraceable_cutoff_claims(
            "误差由 0.6 G 降至 0.1 G。误差低于 0.5 G 阈值。",
            design_payload,
        )
        self.assertEqual(rendered, "误差由 0.6 G 降至 0.1 G。")

    def test_historical_worded_fraction_cutoff_is_not_repeated_as_a_finding(
        self,
    ) -> None:
        design_payload = {
            "criteria": [],
            "interpretation_policy": {
                "uncertainty_rule": "敏感性差值超过主估计绝对值一半时标记为不稳定。"
            },
        }
        rendered = reporting._remove_untraceable_cutoff_claims(
            "当前两种拟合条件的差值为 0.03 G。"
            "敏感性差值超过主估计绝对值一半，因此结果不稳定。",
            design_payload,
        )
        self.assertEqual(rendered, "当前两种拟合条件的差值为 0.03 G。")

    def test_interrupted_multistage_report_keeps_only_reviewed_scientific_results(
        self,
    ) -> None:
        record = {
            "outcome": "budget_stopped",
            "task": "审查配对数据并比较校准前后的误差。",
            "outcome_reason": "外层回归观察窗口结束。",
            "worker_result": None,
            "scientific_assessment": None,
            "evidence_ledger": None,
            "input_snapshot": None,
            "stage_history": [
                {
                    "stage_id": "data_review",
                    "result_summary": {
                        "measurements": [],
                        "result_items": [
                            {
                                "id": "audit_status",
                                "display_name": "数据审计状态",
                                "value_kind": "category",
                                "value": "pass",
                                "unit": "",
                                "role": "diagnostic",
                                "source_artifact": None,
                            },
                            {
                                "id": "valid_rows",
                                "display_name": "有效行数",
                                "value_kind": "count",
                                "value": 17,
                                "unit": "",
                                "role": "diagnostic",
                                "source_artifact": None,
                            },
                            {
                                "id": "suspect_rows",
                                "display_name": "嫌疑行数",
                                "value_kind": "count",
                                "value": 1,
                                "unit": "",
                                "role": "diagnostic",
                                "source_artifact": None,
                            },
                        ],
                    },
                }
            ],
        }
        design_payload = {
            "normalized_task": record["task"],
            "research_frame": {
                "primary_question": "校准能否降低当前评价数据上的误差？",
                "claim_scope": "只描述当前提供的数据。",
            },
            "measurement_plan": [],
            "result_plan": [
                {
                    "id": "audit_status",
                    "display_name": "数据审计状态",
                    "scientific_meaning": "配对数据完整性检查结果",
                    "role": "diagnostic",
                },
                {
                    "id": "valid_rows",
                    "display_name": "有效行数",
                    "scientific_meaning": "质量标记为正常的行数",
                    "role": "diagnostic",
                },
                {
                    "id": "suspect_rows",
                    "display_name": "嫌疑行数",
                    "scientific_meaning": "质量标记为异常的行数",
                    "role": "diagnostic",
                },
            ],
            "experiment_stages": [
                {
                    "id": "data_review",
                    "objective": "检查数据质量",
                    "method_outline": "统计质量标记分布；记录时间顺序划分。",
                },
                {
                    "id": "calibration",
                    "objective": "校准比较与敏感性分析",
                    "method_outline": "拟合校准关系并比较评价误差。",
                },
            ],
        }

        rendered = reporting.render_report(record, design=design_payload)

        self.assertIn("数据完整性检查为通过", rendered)
        self.assertIn("正常标记观测数为17", rendered)
        self.assertIn("需关注观测数为1", rendered)
        self.assertIn("统计质量标记分布，并记录时间顺序划分", rendered)
        self.assertNotIn("外层回归", rendered)
        self.assertNotIn("| pass |", rendered)
        self.assertNotIn("拟合校准关系并比较评价误差", rendered)

    def test_reader_text_humanizes_legacy_success_status(self) -> None:
        rendered = reporting._reader_facing_text(
            "attempt-002 成功执行，退出码 0。结果文件已经核对。"
        )
        self.assertEqual(rendered, "实验程序正常完成。结果文件已经核对。")
        self.assertNotIn("attempt-002", rendered)
        self.assertNotIn("退出码", rendered)

    def test_result_label_normalization_deduplicates_common_symbols(self) -> None:
        self.assertEqual(
            reporting._normalized_result_label("Pearson 相关系数 r"),
            reporting._normalized_result_label("Pearson 相关系数"),
        )
        self.assertEqual(
            reporting._normalized_result_label("有效样本数 N"),
            reporting._normalized_result_label("有效样本数"),
        )

    def test_report_quality_allows_asset_path_but_rejects_identifier_in_prose(
        self,
    ) -> None:
        reporting._validate_main_report_quality(
            "# 结果\n\n![结果比较图](report_assets/verified-comparison.svg)\n"
        )
        with self.assertRaisesRegex(RuntimeError, "machine-style identifier"):
            reporting._validate_main_report_quality(
                "# 结果\n\n正文不得出现 raw_field_name。\n"
            )

    def test_simple_method_uses_reader_facing_measurement_names(self) -> None:
        rendered = reporting._reader_facing_plan_text(
            "计算 count、mean、min_value 和 max_value。",
            {
                "measurement_plan": [
                    {"name": "count", "display_name": "样本数"},
                    {"name": "mean", "display_name": "均值"},
                    {"name": "min_value", "display_name": "最小值"},
                    {"name": "max_value", "display_name": "最大值"},
                ]
            },
        )
        self.assertEqual(rendered, "计算样本数、均值、最小值和最大值。")
        self.assertEqual(
            reporting._reader_facing_plan_text(
                "计算 min 和 max。",
                {
                    "measurement_plan": [
                        {"name": "min_value", "display_name": "最小值"},
                        {"name": "max_value", "display_name": "最大值"},
                    ]
                },
            ),
            "计算最小值和最大值。",
        )

    def test_new_run_has_concise_main_report_and_complete_audit_bundle(self) -> None:
        req = request("unit_reporting_bundle")
        run_id, attempt_id = create_ready_run(req)
        self.addCleanup(cleanup_run, run_id)
        service.execute(run_id, attempt_id)
        service.verify(run_id, attempt_id, None)
        service.verify(run_id, attempt_id, assessment())
        entry = service.finalize(run_id)
        root = runs_root() / run_id
        report = (root / "report.md").read_text(encoding="utf-8")
        audit = (root / "audit.md").read_text(encoding="utf-8")

        self.assertEqual(entry["user_display_markdown"], report)
        self.assertEqual(entry["report_sha256"], file_sha256(root / "report.md"))
        self.assertEqual(entry["audit_sha256"], file_sha256(root / "audit.md"))
        self.assertLessEqual(len(report), 6000)
        self.assertLessEqual(report.count("\n## "), 7)
        self.assertLessEqual(
            sum(line.startswith("|---") for line in report.splitlines()),
            2,
        )
        self.assertLessEqual(
            sum(line.startswith("- ") for line in report.splitlines()),
            12,
        )
        for heading in ("摘要", "数据与方法", "结果", "讨论"):
            self.assertIn(heading, report)
        self.assertNotIn("本次没有适合生成验证图", report)
        self.assertNotIn("## 下一步", report)
        for internal_term in (
            "request_sha256",
            "verification_checks",
            "mean_result",
            "schema_version",
            "worker_result",
            "proposed_outcome",
            "measurement name",
            "result id",
            "退出码",
            "科研含义",
            "当前状态：",
            "主张边界：",
            "方法选择（",
            "audit.md",
            "/automatic-experiment",
        ):
            self.assertNotIn(internal_term, report)
        self.assertIn("| Arithmetic mean | 2.5 |", report)
        self.assertIn("全部判据与测量", audit)
        self.assertIn("实际执行事实与核验检查", audit)
        self.assertIn("不可变尝试关系", audit)
        self.assertIn("运行来源与复现信息", audit)
        self.assertIn("mean_result", audit)
        self.assertIn("mean", audit)

        record = read_json(root / "record.json")
        design_payload = read_json(root / "design.json")
        response_payload = read_json(root / "response.json")
        duplicate = {
            "id": "mean_copy",
            "display_name": "Arithmetic mean value",
            "value_kind": "number",
            "value": 2.5,
            "unit": "dimensionless",
            "role": "primary",
            "source_artifact": "summary.json",
        }
        record["worker_result"]["result_items"] = [duplicate]
        record["stage_history"][0]["result_summary"]["result_items"] = [duplicate]
        design_payload["result_plan"] = [
            {
                "id": "mean_copy",
                "display_name": "Arithmetic mean value",
                "value_kind": "number",
                "role": "primary",
                "unit": "dimensionless",
                "scientific_meaning": "The arithmetic average of the supplied values.",
            }
        ]
        record["replay"] = {
            "mode": "fresh",
            "pi_command": f"/automatic-experiment 重放 {run_id}",
        }
        deduplicated = reporting.render_report(
            record, response_payload, design_payload
        )
        self.assertEqual(
            deduplicated.count("| Arithmetic mean | 2.5 |"),
            1,
        )
        self.assertNotIn("| Arithmetic mean value |", deduplicated)
        self.assertNotIn("与历史设计的关系", deduplicated)
        self.assertNotIn("两个 run", deduplicated)

    def test_finalize_always_writes_report_when_primary_rendering_fails(self) -> None:
        req = request("unit_reporting_emergency_fallback")
        run_id, attempt_id = create_ready_run(req)
        self.addCleanup(cleanup_run, run_id)
        service.execute(run_id, attempt_id)
        service.verify(run_id, attempt_id, None)
        service.verify(run_id, attempt_id, assessment())

        with mock.patch.object(
            reporting,
            "render_report",
            side_effect=RuntimeError("reader quality failure"),
        ):
            entry = service.finalize(run_id)

        root = runs_root() / run_id
        report = (root / "report.md").read_text(encoding="utf-8")
        audit = (root / "audit.md").read_text(encoding="utf-8")
        self.assertEqual(entry["status"], "finalized")
        self.assertEqual(entry["user_display_markdown"], report)
        self.assertIn("## 结果", report)
        self.assertIn("Arithmetic mean", report)
        self.assertIn("主报告生成说明", audit)
        self.assertNotIn("reader quality failure", report)

    def test_repeated_finalize_revalidates_without_rewriting_bundle(self) -> None:
        req = request("unit_reporting_idempotent")
        run_id, attempt_id = create_ready_run(req)
        self.addCleanup(cleanup_run, run_id)
        service.execute(run_id, attempt_id)
        service.verify(run_id, attempt_id, None)
        service.verify(run_id, attempt_id, assessment())
        first = service.finalize(run_id)
        second = service.finalize(run_id)
        self.assertEqual(first, second)

    def test_finalize_recovers_after_bundle_write_before_state_checkpoint(self) -> None:
        req = request("unit_reporting_recovery")
        run_id, attempt_id = create_ready_run(req)
        self.addCleanup(cleanup_run, run_id)
        service.execute(run_id, attempt_id)
        service.verify(run_id, attempt_id, None)
        service.verify(run_id, attempt_id, assessment())
        first = service.finalize(run_id)
        state_path = runs_root() / run_id / "state.json"
        state = read_json(state_path)
        state["phase"] = "verification_finished"
        state["report_sha256"] = None
        state["audit_sha256"] = None
        state["report_assets"] = []
        atomic_write_json(state_path, state)
        recovered = service.finalize(run_id)
        self.assertEqual(recovered, first)
        recovered_state = read_json(state_path)
        self.assertEqual(recovered_state["phase"], "report_finalized")
        self.assertTrue(
            recovered_state["checkpoints"][-1]["details"][
                "recovered_after_entry_write"
            ]
        )


if __name__ == "__main__":
    unittest.main()
