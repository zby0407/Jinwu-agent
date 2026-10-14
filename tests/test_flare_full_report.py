"""Report provenance and complete native deliverables, without real live calls."""

import base64
import csv
import hashlib
import json
import re

import numpy as np
import pytest
from langchain_core.messages import HumanMessage

from jw.flare_forecast import report_full, report_reference
from jw.flare_forecast.assets import sha
from jw.flare_forecast.native_data import write
from jw.flare_forecast.report import build_report
from jw.tools import flare_forecast as service


def test_reference_import_checks_before_copy_and_never_overwrites(
    tmp_path, monkeypatch
):
    source = tmp_path / "source"
    source.mkdir()
    proof = {
        "products": [{"path": "models.joblib", "sha256": "model"}],
        "contract_sha256": "contract",
    }
    write(source / "proof.json", proof)
    (source / "importance.csv").write_text("feature,value\nx,1\n", encoding="utf-8")
    inventory = {
        "origin_checkpoint_sha256": "model",
        "original_contract_sha256": "contract",
        "files": [
            {"path": p.name, "bytes": p.stat().st_size, "sha256": sha(p)}
            for p in source.iterdir()
        ],
    }
    monkeypatch.setattr(report_reference, "reference_inventory", lambda: inventory)
    assets = tmp_path / "assets"
    (source / "importance.csv").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="differs"):
        report_reference.import_reference(source, assets)
    assert not assets.exists()
    (source / "importance.csv").write_text("feature,value\nx,1\n", encoding="utf-8")
    report_reference.import_reference(source, assets)
    with pytest.raises(ValueError, match="never overwrite"):
        report_reference.import_reference(source, assets)
    proof["products"][0]["sha256"] = "wrong-model"
    (source / "proof.json").write_text(json.dumps(proof), encoding="utf-8")
    inventory["files"] = [
        {"path": p.name, "bytes": p.stat().st_size, "sha256": sha(p)}
        for p in source.iterdir()
    ]
    with pytest.raises(ValueError, match="different model"):
        report_reference.verify_reference(source)


def test_missing_report_reference_blocks_acquisition_and_issuance(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(service, "load_forest", lambda _: object())
    monkeypatch.setattr(
        service,
        "fetch_live",
        lambda *a: pytest.fail("Must not fetch without report inputs"),
    )
    result = service.execute_forecast(
        tmp_path,
        [HumanMessage(content="预测未来24h太阳耀斑")],
        "actual-synthetic-call",
        object,
        tmp_path / "missing_assets",
    )
    assert result["status"] == "not_issued" and not result["forecast_issued"]
    assert result["failure_stage"] == "model_assets"


def install_history(tmp_path, monkeypatch):
    rows, predictions = [], []
    for year in (2023, 2024, 2025):
        for i, (c, m, pc, pm) in enumerate(
            ((0, 0, 0.2, 0.1), (1, 0, 0.8, 0.4), (1, 1, 0.9, 0.7))
        ):
            rows.append(
                {
                    "sample_id": f"{year}-{i}",
                    "target_start_utc": f"{year}-01-0{i + 1}T00:00:00Z",
                    "target_end_utc": f"{year}-01-0{i + 2}T00:00:00Z",
                    "label_c1": str(c),
                    "label_m1": str(m),
                    "split": "test",
                }
            )
            predictions.append({"C1.0+": pc, "M1.0+": pm})
    metrics, targets = [], {}
    for target, label in (("C1.0+", "label_c1"), ("M1.0+", "label_m1")):
        y = np.array([int(r[label]) for r in rows])
        p = np.array([r[target] for r in predictions])
        mse = float(np.mean((p - y) ** 2))
        for model in (
            "selected_calibrated",
            "train_climatology",
            "always_yes",
            "always_no",
        ):
            metrics.append(
                {
                    "target": target,
                    "model_id": model,
                    "Accuracy": "1",
                    "Brier": str(mse if model == "selected_calibrated" else 0.5),
                    "TSS": "1",
                    "tp": str(y.sum()),
                    "tn": str((y == 0).sum()),
                    "fp": "0",
                    "fn": "0",
                    "precision": "1",
                    "ROC_AUC": "1",
                    "average_precision": "1",
                }
            )
        targets[target] = {
            "positive": int(y.sum()),
            "negative": int((y == 0).sum()),
            "accuracy": 1.0,
            "brier": mse,
            "tss": 1.0,
            "tp": int(y.sum()),
            "tn": int((y == 0).sum()),
            "fp": 0,
            "fn": 0,
            "recall": 1.0,
            "always_yes_accuracy": float(y.mean()),
            "always_yes_brier": float(1 - y.mean()),
        }
    assets = tmp_path / "assets"
    reference_dir = assets / "report_reference"
    reference_dir.mkdir(parents=True)
    write(reference_dir / "proof.json", {})
    files = [
        {
            "path": "proof.json",
            "bytes": (reference_dir / "proof.json").stat().st_size,
            "sha256": sha(reference_dir / "proof.json"),
        }
    ]
    summary = {
        "total_eligible": 12,
        "final_fit": 2,
        "test_windows": 9,
        "dataset_sha256": "dataset",
        "targets": targets,
    }
    reference = {
        "inventory": {"files": files, "origin_checkpoint_sha256": "model"},
        "summary": {
            "final_fit_rows": 2,
            "primary_split_counts": {
                "train": 1,
                "selection": 1,
                "calibration": 1,
                "test": 9,
            },
            "common_split_counts": {
                "train": 1,
                "selection": 1,
                "calibration": 1,
                "test": 1,
            },
            "first_qualified_training_reference": "2014-03-11T00:00:00Z",
        },
        "metrics": metrics,
        "years": [
            {**r, "year": str(year), "cohort": "primary"}
            for r in metrics
            for year in (2023, 2024, 2025)
        ],
        "intervals": [
            {
                "target": t,
                "cohort": "primary",
                "model_id": "selected_calibrated",
                "metric": "TSS",
                "ci_lower": "0",
                "ci_upper": "1",
            }
            for t in targets
        ],
        "importance": [
            {
                "feature": f"field{i}",
                "C_Brier_increase_mean": 0.001 * i,
                "C_Brier_increase_std": 0.0001,
                "M_Brier_increase_mean": 0.002 * i,
                "M_Brier_increase_std": 0.0001,
            }
            for i in range(23)
        ],
        "calibration": {
            "baseline_climatology_class_probabilities": [0.5, 0.3, 0.2],
            "selected": {"temperature": 1.2},
        },
        "selected_config": {
            "locked_configuration_signature": {
                "parameters": {
                    "n_estimators": 200,
                    "min_samples_leaf": 5,
                    "max_depth": 8,
                    "random_state": 20261011,
                }
            }
        },
    }
    monkeypatch.setattr(
        report_full, "history_summary", lambda _: (summary, rows, predictions)
    )
    monkeypatch.setattr(report_full, "load_reference", lambda *a: reference)
    monkeypatch.setattr(
        report_full,
        "dataset_counts",
        lambda _: {"calendar_candidates": 5479, "GOES_catalog_records": 44107},
    )
    forecast = {
        "issued_utc": "2026-10-14T14:00:00.123456Z",
        "prediction_window": {
            "start": "2026-10-14T14:00:00.123456Z",
            "end": "2026-10-15T14:00:00.123456Z",
        },
        "probabilities": {"C1.0+": 0.91, "M1.0+": 0.37},
        "model_origin_checkpoint_sha256": "model",
        "input_observation_utc": "2026-10-14T13:30:00Z",
        "input_age_seconds": 1800.123456,
        "live_manifest_sha256": "live",
        "native_Data_review_sha256": "review",
    }
    return assets, forecast, reference, rows, predictions


def test_full_report_contains_all_elements_embedded_figures_and_exact_window(
    tmp_path, monkeypatch
):
    assets, forecast, _, _, _ = install_history(tmp_path, monkeypatch)
    output = tmp_path / "report"
    build_report(forecast, assets, output)
    page = (output / "report.html").read_text("utf-8")
    manifest = json.loads((output / "report_manifest.json").read_text("utf-8"))
    assert len(manifest["figures"]) == 8
    assert page.count("<img ") == 8
    assert "2026-10-14 22:00:00.123456+08:00" in page
    assert "91.00%" in page and "37.00%" in page
    for name in (
        "性能结论",
        "全部测试样本",
        "检出、漏报",
        "概率校准",
        "跨年表现",
        "磁场参数",
        "数据规模",
        "贡献、用途",
        "来源索引",
    ):
        assert name in page
    for figure, encoded in zip(
        manifest["figures"],
        re.findall(r'src="data:image/png;base64,([^"]+)"', page),
        strict=True,
    ):
        # Report order differs from render order: each image must match an original.
        assert hashlib.sha256(base64.b64decode(encoded)).hexdigest() in {
            r["png_sha256"] for r in manifest["figures"]
        }
        assert (output / (figure["id"] + ".svg")).is_file()
    bins = json.loads((output / "reliability_bins.json").read_text("utf-8"))
    assert all(
        sum(r["n"] for r in bins if r["target"] == t) == 9 for t in ("C1.0+", "M1.0+")
    )
    assert not re.search(r'src="(?!data:)', page)
    assert not re.search(r'<script|<link[^>]+href="https?://', page)
    with pytest.raises(FileExistsError):
        build_report(forecast, assets, output)


@pytest.mark.parametrize("corruption", ["model", "metric", "probability"])
def test_report_rejects_wrong_model_metrics_or_non_nested_probability(
    tmp_path, monkeypatch, corruption
):
    assets, forecast, reference, _, _ = install_history(tmp_path, monkeypatch)
    if corruption == "model":
        forecast["model_origin_checkpoint_sha256"] = "other"
    elif corruption == "metric":
        reference["metrics"][0]["Brier"] = "999"
    else:
        forecast["probabilities"]["M1.0+"] = 0.99
    output = tmp_path / "report"
    with pytest.raises(ValueError):
        build_report(forecast, assets, output)
    assert not output.exists()


def test_frozen_predictions_must_match_each_window_label_and_score(
    tmp_path, monkeypatch
):
    _, _, reference, rows, predictions = install_history(tmp_path, monkeypatch)
    destination = tmp_path / "real_reference"
    destination.mkdir()
    monkeypatch.setattr(
        report_reference, "verify_reference", lambda _: reference["inventory"]
    )
    for name in ("summary", "calibration", "selected_config_lock"):
        write(destination / (name + ".json"), {})
    for name in ("metrics_primary", "year_metrics", "importance", "bootstrap_ci"):
        (destination / (name + ".csv")).write_text("column\n", encoding="utf-8")
    data = [
        {
            **r,
            "p_C_selected_calibrated": p["C1.0+"],
            "p_M_selected_calibrated": p["M1.0+"],
        }
        for r, p in zip(rows, predictions, strict=True)
    ]

    def save():
        with (destination / "heldout_predictions.csv").open(
            "w", newline="", encoding="utf-8"
        ) as f:
            writer = csv.DictWriter(f, fieldnames=list(data[0]))
            writer.writeheader()
            writer.writerows(data)

    monkeypatch.setattr(report_reference, "child", lambda *a: destination)
    save()
    report_reference.load_reference(tmp_path, rows, predictions)
    data[0]["target_end_utc"] = "wrong-window"
    save()
    with pytest.raises(ValueError, match="window or label"):
        report_reference.load_reference(tmp_path, rows, predictions)
    data[0]["target_end_utc"] = rows[0]["target_end_utc"]
    data[0]["p_M_selected_calibrated"] = 0.99
    save()
    with pytest.raises(ValueError, match="score differs"):
        report_reference.load_reference(tmp_path, rows, predictions)
