"""Synthetic engineering tests; no HTTP, scientific refit or live issuance."""

import csv
import json
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from jw.flare_forecast import assets, engine, workflow
from jw.flare_forecast.cli import main
from jw.flare_forecast.model import FEATURES, FrozenForest


def synthetic_forest():
    tree = {
        "left": [1, -1, -1],
        "right": [2, -1, -1],
        "feature": [0, -2, -2],
        "threshold": [0.1, -2, -2],
        "value": [[1, 1, 1], [1, 0, 0], [0, 1, 1]],
    }
    return {
        "schema_version": "jw-frozen-flare-forest-v1",
        "features": FEATURES,
        "classes": [0, 1, 2],
        "temperature": 1,
        "trees": [deepcopy(tree) for _ in range(200)],
    }


def test_float32_split_matches_original_tree_input_semantics():
    m = FrozenForest(synthetic_forest())
    x = np.zeros((2, 23))
    x[:, 0] = [0.09999, 0.10000000001]
    p = m.probabilities(x)
    assert np.array_equal(p, [[1, 0, 0], [0, 0.5, 0.5]])
    assert m.predict(x)[1] == {"C1.0+": 1.0, "M1.0+": 0.5}


def test_scalar_temperature_preserves_nested_probabilities():
    d = synthetic_forest()
    d["temperature"] = 1.2
    result = FrozenForest(d).probabilities(np.ones((1, 23)))[0]
    assert np.isclose(sum(result), 1)
    assert 0 < result[0] < 1e-8
    assert result[1] == result[2]


@pytest.mark.parametrize("name", ["left", "right"])
def test_cyclic_or_invalid_children_rejected(name):
    d = synthetic_forest()
    d["trees"][0][name][0] = 0
    with pytest.raises(ValueError, match="children"):
        FrozenForest(d)


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_nonfinite_features_rejected(value):
    x = np.ones((1, 23))
    x[0, 5] = value
    with pytest.raises(ValueError, match="finite"):
        FrozenForest(synthetic_forest()).predict(x)


@pytest.mark.parametrize("shape", [(1, 12), (1, 35), (23,)])
def test_wrong_feature_interfaces_rejected(shape):
    with pytest.raises(ValueError, match="23"):
        FrozenForest(synthetic_forest()).predict(np.ones(shape))


@pytest.mark.parametrize(
    "path", ["../escape", "/escape", "C:\\escape", "inputs/../../escape"]
)
def test_asset_paths_confined(tmp_path, path):
    with pytest.raises(ValueError):
        assets.child(tmp_path, path)


def test_import_checks_all_sources_before_creating_output(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    p = raw / "data.csv"
    p.write_text("synthetic\n", encoding="utf-8")
    row = {
        "path": "inputs/data.csv",
        "source": "raw/data.csv",
        "bytes": p.stat().st_size,
        "sha256": "0" * 64,
    }
    monkeypatch.setattr(assets, "inventory", lambda: {"files": [row]})
    output = tmp_path / "new"
    with pytest.raises(ValueError, match="differs"):
        assets.import_assets(raw, {}, output)
    assert not output.exists()


def test_import_copies_only_registered_inputs_never_extra_reports(
    tmp_path, monkeypatch
):
    raw = tmp_path / "raw"
    raw.mkdir()
    p = raw / "data.csv"
    p.write_text("synthetic\n", encoding="utf-8")
    (raw / "report.pdf").write_bytes(b"synthetic excluded report")
    row = {
        "path": "inputs/data.csv",
        "source": "raw/data.csv",
        "bytes": p.stat().st_size,
        "sha256": assets.sha(p),
    }
    monkeypatch.setattr(assets, "inventory", lambda: {"files": [row]})
    output = tmp_path / "new"
    assert assets.import_assets(raw, {}, output)["files"] == 1
    assert (output / "inputs/data.csv").read_bytes() == p.read_bytes()
    assert not list(output.rglob("*.pdf"))
    p2 = output / "inputs/data.csv"
    p2.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(ValueError):
        assets.verify_assets(output)


def test_export_refuses_unregistered_pickle_before_deserialization(tmp_path):
    from jw.flare_forecast.export import export_forest

    (tmp_path / "model").mkdir()
    (tmp_path / "model/selected_rf.joblib").write_bytes(b"untrusted synthetic bytes")
    with pytest.raises(ValueError, match="deserialize"):
        export_forest(tmp_path)


def test_engine_refuses_unregistered_module():
    with pytest.raises(ValueError, match="Unregistered"):
        engine.load("run_arbitrary_command")


def test_source_manifest_and_module_inventory_are_not_reports():
    data = engine.inventory()
    assert data["reports_included"] is False
    assert len(data["files"]) == 353
    assert not any(
        Path(x["path"]).suffix in {".md", ".pdf", ".png", ".svg", ".sqlite", ".log"}
        for x in data["files"]
    )
    assert (
        data["model_origin_contract_sha256"]
        == "483ff7e0d4bd7dd82514317b369e84c47029b8b6b73d447f9da02e095d8c09d3"
    )


def test_score_is_explicitly_unissued(tmp_path, monkeypatch):
    source = tmp_path / "features.csv"
    with source.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FEATURES)
        writer.writeheader()
        writer.writerow(dict.fromkeys(FEATURES, 1))
    monkeypatch.setattr(
        workflow, "load_forest", lambda _: FrozenForest(synthetic_forest())
    )
    result = workflow.score_csv(tmp_path, source)
    assert result["status"] == "unissued_scores"
    assert result["future_window"] is None
    assert result["scientific_review"] == "not_performed"


def test_issue_requires_review_before_predicting_or_creating_claim(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    def rejected(*args):
        raise ValueError("missing actual native Data review")

    core = SimpleNamespace(
        checked_live=lambda *args: ({}, {}, {}, None, tmp_path / "manifest.json"),
        native_review=rejected,
    )
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "contract.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(workflow, "load", lambda _: core)
    monkeypatch.setattr(
        workflow, "load_forest", lambda _: pytest.fail("predict must not run")
    )
    with pytest.raises(ValueError, match="native Data"):
        workflow.issue(
            tmp_path / "assets",
            tmp_path / "live",
            tmp_path / "contract.json",
            tmp_path / "review.json",
            tmp_path / "issued",
        )
    assert not (tmp_path / "issued").exists()
    assert not (tmp_path / "live.issue_claim.json").exists()


def test_cli_help_never_runs_fetch_or_issue(monkeypatch, capsys):
    monkeypatch.setattr(
        workflow, "fetch_live", lambda *args: pytest.fail("network must not run")
    )
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "fetch-live" in capsys.readouterr().out


def test_jw_cli_has_lazy_flare_command():
    from jw.cli._app import app

    result = CliRunner().invoke(app, ["flare", "--help"])
    assert result.exit_code == 0, result.output
    assert "verify-assets" in result.output
    assert "import-assets" in result.output


def test_portable_binding_is_not_runtime_or_unreviewed_model():
    identity = json.loads(
        (engine.PACKAGE / "portable_model_identity.json").read_text(encoding="utf-8")
    )
    assert identity["no_refit_or_recalibration"] is True
    expected = next(
        x
        for x in engine.inventory()["files"]
        if x["path"] == "model/selected_rf.joblib"
    )
    assert identity["origin_checkpoint_sha256"] == expected["sha256"]


def test_reviewed_issue_captures_after_prediction_and_claim_blocks_repeat(
    tmp_path, monkeypatch
):
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    import pandas as pd

    assets_root = tmp_path / "assets"
    (assets_root / "model").mkdir(parents=True)
    (assets_root / "model/forest.json.gz").write_bytes(b"synthetic model payload")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    contract = tmp_path / "contract.json"
    contract.write_text("{}", encoding="utf-8")
    review = tmp_path / "review.json"
    review.write_text("{}", encoding="utf-8")
    protocol = json.loads(
        (engine.PACKAGE / "model_protocol.json").read_text(encoding="utf-8")
    )
    policy = {
        "toolkit_migration": {
            "original_contract_sha256": engine.inventory()[
                "model_origin_contract_sha256"
            ]
        }
    }
    frame = pd.DataFrame(
        [{**dict.fromkeys(FEATURES, 1), "observation_utc": "SYNTHETIC_ONLY"}]
    )
    predicted = []
    start = datetime(2026, 10, 14, 9, 1, 2, 345678, tzinfo=timezone.utc)

    def validate_clock(frame, now):
        return start if predicted else start - timedelta(seconds=2), 4000

    core = SimpleNamespace(
        checked_live=lambda *args: (policy, {}, {}, frame, manifest_path),
        native_review=lambda *args: ({"decision": "accept_with_limits"}, {}),
        validate_physical=lambda *args, **kwargs: None,
        validate_live_at_issue=validate_clock,
        verify_unchanged=lambda *args: None,
    )

    def predict(*args):
        predicted.append(True)
        return [{"C1.0+": 0.7, "M1.0+": 0.2}]

    forest = SimpleNamespace(
        document={
            "origin_checkpoint_sha256": protocol["origin_checkpoint_sha256"],
            "origin_contract_sha256": policy["toolkit_migration"][
                "original_contract_sha256"
            ],
        },
        predict=predict,
    )
    monkeypatch.setattr(workflow, "load", lambda _: core)
    monkeypatch.setattr(workflow, "load_forest", lambda _: forest)
    output = tmp_path / "issued"
    result = workflow.issue(assets_root, tmp_path / "live", contract, review, output)
    assert result["issued_utc"] == result["prediction_window"]["start"]
    assert result["issued_utc"] == start.isoformat().replace("+00:00", "Z")
    end = datetime.fromisoformat(
        result["prediction_window"]["end"].replace("Z", "+00:00")
    )
    assert end - start == timedelta(hours=24)
    assert result["future_accuracy"] is None and result["observed_outcome"] is None
    assert result["future_truth_read"] is False
    assert result["prior_historical_Evidence"]["target_skill_supported"] == {
        "C1.0+": False,
        "M1.0+": True,
    }
    with pytest.raises(FileExistsError):
        workflow.issue(
            assets_root, tmp_path / "live", contract, review, tmp_path / "second_issue"
        )
    assert len(predicted) == 1
    assert not (tmp_path / "second_issue").exists()
