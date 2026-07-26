from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from automatic_experiment.report_assets import (
    ReportAssetError,
    generate_report_assets,
)
from automatic_experiment.state import (
    atomic_write_text,
    file_sha256,
    runs_root,
)


def fixture(row_count: int) -> tuple[dict[str, object], dict[str, object], str]:
    rows: list[str] = ["row_id,target,baseline,candidate"]
    row_ids: list[str] = []
    baseline_errors: list[float] = []
    candidate_errors: list[float] = []
    for index in range(row_count):
        row_id = "<script>&" if index == 0 else f"row-{index + 1}"
        target = float(index + 1)
        baseline = target + 2.0
        candidate = target + 0.5
        rows.append(f"{row_id},{target},{baseline},{candidate}")
        row_ids.append(row_id)
        baseline_errors.append(abs(baseline - target))
        candidate_errors.append(abs(candidate - target))
    baseline_mae = sum(baseline_errors) / row_count
    candidate_mae = sum(candidate_errors) / row_count
    csv_text = "\n".join(rows) + "\n"
    primary = {
        "id": "holdout",
        "comparison_kind": "source_baseline_vs_candidate",
        "evaluation_scope": "当前留出段",
        "evidence_artifact": "paired.csv",
        "evidence_row_id_column": "row_id",
        "evidence_target_column": "target",
        "evidence_baseline_column": "baseline",
        "evidence_candidate_column": "candidate",
        "metric": "mae",
        "row_count": row_count,
        "row_ids": row_ids,
        "recomputed_measurements": {
            "raw_mae": baseline_mae,
            "calibrated_mae": candidate_mae,
        },
    }
    sensitivity = {
        "id": "sensitivity",
        "comparison_kind": "candidate_vs_candidate",
        "evaluation_scope": "同一当前留出段",
        "metric": "mae",
        "row_count": row_count,
        "row_ids": row_ids,
        "recomputed_measurements": {
            "with_flag_mae": 0.6,
            "without_flag_mae": 0.5,
        },
    }
    record: dict[str, object] = {
        "worker_result": {
            "measurements": [
                {
                    "name": "raw_mae",
                    "value": baseline_mae,
                    "unit": "gauss",
                    "role": "primary",
                    "source_artifact": "paired.csv",
                },
                {
                    "name": "calibrated_mae",
                    "value": candidate_mae,
                    "unit": "gauss",
                    "role": "primary",
                    "source_artifact": "paired.csv",
                },
            ]
        },
        "evidence_ledger": {"paired_comparisons": [primary, sensitivity]},
        "public_artifacts": [],
    }
    audit_base = {
        "evaluation_scope": "当前留出段",
        "evidence_artifact": "paired.csv",
        "evidence_row_id_column": "row_id",
        "evidence_target_column": "target",
        "evidence_baseline_column": "baseline",
        "evidence_candidate_column": "candidate",
        "metric": "mae",
    }
    design: dict[str, object] = {
        "measurement_plan": [
            {
                "name": "raw_mae",
                "display_name": "原始读数平均绝对误差",
                "role": "primary",
                "unit": "gauss",
                "scientific_meaning": "当前留出段上的原始误差尺度。",
            },
            {
                "name": "calibrated_mae",
                "display_name": "校准预测平均绝对误差",
                "role": "primary",
                "unit": "gauss",
                "scientific_meaning": "当前留出段上的校准误差尺度。",
            },
            {
                "name": "without_flag_mae",
                "display_name": "排除标记行后模型的平均绝对误差",
                "role": "diagnostic",
                "unit": "gauss",
                "scientific_meaning": "单一质量标记敏感性检查。",
            },
        ],
        "paired_comparison_audits": [
            {
                **audit_base,
                "id": "holdout",
                "baseline_measurement": "raw_mae",
                "candidate_measurement": "calibrated_mae",
            },
            {
                **audit_base,
                "id": "sensitivity",
                "baseline_measurement": "with_flag_mae",
                "candidate_measurement": "without_flag_mae",
            },
        ],
    }
    return record, design, csv_text


class ReportAssetTests(unittest.TestCase):
    def create_root(
        self,
        row_count: int,
    ) -> tuple[Path, dict[str, object], dict[str, object]]:
        temporary = tempfile.TemporaryDirectory(
            dir=runs_root(),
            prefix="unit_asset_",
        )
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "public").mkdir()
        record, design, csv_text = fixture(row_count)
        evidence = root / "public" / "paired.csv"
        atomic_write_text(evidence, csv_text)
        record["public_artifacts"] = [
            {
                "path": "public/paired.csv",
                "kind": "csv",
                "description": "逐行证据",
                "size_bytes": evidence.stat().st_size,
                "sha256": file_sha256(evidence),
            }
        ]
        return root, record, design

    def test_all_rows_svg_is_escaped_hashed_and_idempotent(self) -> None:
        root, record, design = self.create_root(3)
        assets, status = generate_report_assets(root, record, design)
        self.assertEqual(status["status"], "generated")
        self.assertEqual(assets[0]["row_handling"], "all_rows")
        svg_path = root / assets[0]["path"]
        svg = svg_path.read_text(encoding="utf-8")
        self.assertIn("&lt;script&gt;&amp;", svg)
        self.assertNotIn("<script>", svg)
        self.assertEqual(svg.count("<circle "), 9)
        self.assertIn("gauss", svg)
        self.assertIn("观测结果与误差指标比较", svg)
        self.assertNotIn("逐行", svg)
        self.assertIn("参考值", svg)
        self.assertIn("原始读数", svg)
        self.assertIn("校准后预测", svg)
        self.assertIn("包含被标记观测", svg)
        self.assertIn("排除标记观测", svg)
        self.assertNotIn("确定性核心", svg)
        self.assertNotIn("审计附件", svg)
        self.assertNotIn("CSV", svg)
        self.assertEqual(assets[0]["sha256"], file_sha256(svg_path))
        repeated, _ = generate_report_assets(root, record, design)
        self.assertEqual(repeated, assets)

    def test_more_than_200_rows_uses_full_data_aggregate_without_sampling(self) -> None:
        root, record, design = self.create_root(201)
        assets, _ = generate_report_assets(root, record, design)
        svg = (root / assets[0]["path"]).read_text(encoding="utf-8")
        self.assertEqual(assets[0]["row_handling"], "full_data_aggregate")
        self.assertEqual(assets[0]["row_count"], 201)
        self.assertIn("数据超过 200 条", svg)
        self.assertIn("使用全部观测", svg)
        self.assertNotIn("验证行", svg)
        self.assertNotIn("未抽样", svg)
        self.assertNotIn("<circle ", svg)

    def test_generic_comparison_uses_the_current_design_labels(self) -> None:
        root, record, design = self.create_root(3)
        design["measurement_plan"][0]["display_name"] = (
            "参照方案平均绝对误差"
        )
        design["measurement_plan"][1]["display_name"] = (
            "候选方案平均绝对误差"
        )
        assets, _ = generate_report_assets(root, record, design)
        svg = (root / assets[0]["path"]).read_text(encoding="utf-8")
        self.assertIn("参照方案结果", svg)
        self.assertIn("候选方案结果", svg)
        self.assertNotIn("未校正读数", svg)
        self.assertNotIn("校准后预测", svg)

    def test_calibrated_sensitivity_bars_keep_their_named_conditions(self) -> None:
        root, record, design = self.create_root(3)
        design["measurement_plan"][0]["display_name"] = (
            "保留标记观测时未校准平均绝对误差"
        )
        design["measurement_plan"][1]["display_name"] = (
            "校准 MAE（保留标记拟合，排除标记评估）"
        )
        design["measurement_plan"][2]["display_name"] = (
            "校准后平均绝对误差（排除标记拟合，排除标记评估）"
        )
        sensitivity = record["evidence_ledger"]["paired_comparisons"][1]
        sensitivity["recomputed_measurements"] = {
            "calibrated_mae": 0.6,
            "without_flag_mae": 0.5,
        }
        design["paired_comparison_audits"][1]["baseline_measurement"] = (
            "calibrated_mae"
        )
        design["paired_comparison_audits"][0]["candidate_fit_condition"] = (
            "全部训练行线性校准"
        )
        design["paired_comparison_audits"][1]["baseline_fit_condition"] = (
            "include_all"
        )
        design["paired_comparison_audits"][1]["candidate_fit_condition"] = (
            "exclude_marked"
        )
        assets, _ = generate_report_assets(root, record, design)
        svg = (root / assets[0]["path"]).read_text(encoding="utf-8")
        self.assertIn("包含被标记观测时未校准", svg)
        self.assertIn("包含被标记观测时校准后", svg)
        self.assertIn("包含被标记观测时校准后预测", svg)
        self.assertIn("排除标记观测时校准后", svg)
        self.assertIn(
            "汇总误差指标包括包含被标记观测时未校准、包含被标记观测时校准后、"
            "排除标记观测时校准后",
            svg,
        )
        self.assertEqual(svg.count(">包含被标记观测时校准后</text>"), 1)
        self.assertEqual(svg.count(">排除标记观测时校准后</text>"), 1)

    def test_existing_different_asset_blocks_finalization_recovery(self) -> None:
        root, record, design = self.create_root(3)
        generate_report_assets(root, record, design)
        (root / "report_assets" / "verified-comparison.svg").write_bytes(
            b"<svg>tampered</svg>"
        )
        with self.assertRaisesRegex(ReportAssetError, "differs"):
            generate_report_assets(root, record, design)

    def test_no_eligible_evidence_is_formally_not_applicable(self) -> None:
        root, record, design = self.create_root(3)
        record["evidence_ledger"] = {"paired_comparisons": []}
        assets, status = generate_report_assets(root, record, design)
        self.assertEqual(assets, [])
        self.assertEqual(status["status"], "not_applicable")


if __name__ == "__main__":
    unittest.main()
