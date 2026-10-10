"""Scientific invariants for the descriptive calculator, with small known data."""

import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "cycle_metrics", Path(__file__).parents[1] / "jw/cycle_metrics.py"
)
metrics = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(metrics)


def series(values):
    return {
        i: {"value": v, "reported_dispersion": None, "observations": None}
        for i, v in enumerate(values)
    }


def boundaries():
    return {
        1: {"start": 0, "peak_date": 1, "peak": 2, "duration": 3},
        2: {"start": 3, "peak_date": 4, "peak": 6, "duration": 3},
        3: {"start": 6},
    }


def test_exact_metrics_and_boundary_not_counted_twice():
    data = series([0, 2, 2, 4, 6, 0, 1000])
    rows = metrics.calculate(data, data, boundaries(), [1, 2], [2])
    assert rows[0]["monthly_mean"] == pytest.approx(4 / 3)
    assert rows[0]["activity_sn_month"] == 4
    assert rows[1]["activity_sn_month"] == 10
    assert sum(r["activity_sn_month"] for r in rows) == 14
    assert rows[0]["peak_date"] == "0000-02"
    assert rows[0]["peak_ties"] == "0000-02|0000-03"
    assert rows[0]["rise_months"] == 1
    assert rows[0]["decline_months"] == 2
    assert rows[0]["low_lt_2_months"] == 1  # strict <, zeros retained
    assert rows[0]["low_lt_2_longest_months"] == 1
    assert rows[0]["missing_observation_count_months"] == 3


@pytest.mark.parametrize("kind", ["absent", "sentinel"])
def test_missing_month_blocks_complete_cycle(kind):
    data = series([0, 2, 2, 4, 6, 0])
    if kind == "absent":
        del data[1]
    else:
        data[1]["value"] = None
    with pytest.raises(ValueError, match="incomplete monthly cycle"):
        metrics.calculate(data, series([0, 2, 2, 4, 6, 0]), boundaries(), [1], [2])


def test_longest_run_breaks_at_missing_or_equal_threshold():
    assert metrics.longest_low([0, 0, None, 0, 2, 0, 0, 0], 2) == 3


def test_parser_preserves_zero_and_missing_metadata(tmp_path):
    path = tmp_path / "monthly.txt"
    path.write_text("1800 1 1800.04 0 -1 -1\n1800 2 1800.12 -1 -1 -1\n")
    data = metrics.load_monthly(path)
    assert list(data.values())[0]["value"] == 0
    assert list(data.values())[1]["value"] is None
    assert metrics.quality(data)["true_zero_values"] == 1
    assert metrics.quality(data)["missing_values"] == 1


@pytest.mark.parametrize("second", ["1800 1", "1799 12"])
def test_parser_rejects_duplicate_and_reversed_months(tmp_path, second):
    path = tmp_path / "monthly.txt"
    path.write_text(f"1800 1 1800.04 0 -1 -1\n{second} 1800.12 2 -1 -1\n")
    with pytest.raises(ValueError, match="duplicate or nonmonotonic"):
        metrics.load_monthly(path)


def test_next_minimum_required():
    with pytest.raises(ValueError, match="missing next minimum"):
        metrics.calculate(series([1]), series([1]), {1: {"start": 0}}, [1], [2])


def test_interpretation_changes_when_input_direction_changes():
    negative = {"target": 5, "metric": "monthly_mean", "difference": -1}
    positive = {"target": 5, "metric": "monthly_mean", "difference": 1}
    assert (
        metrics.hypothesis_cards([negative])[0]["status"]
        == "direction_consistent_descriptively"
    )
    assert (
        metrics.hypothesis_cards([positive])[0]["status"] == "direction_not_supported"
    )


def test_bad_hash_is_audited(tmp_path):
    import json

    inputs = tmp_path / "inputs"
    (inputs / "metadata").mkdir(parents=True)
    (inputs / "source.txt").write_text("different bytes")
    metrics.save_json(
        inputs / "metadata/silso_snapshot_manifest.json",
        {
            "files": [
                {
                    "package_path": "source.txt",
                    "sha256": "0" * 64,
                    "bytes": 15,
                    "dataset_id": "test",
                }
            ]
        },
    )
    params = {
        "cycles": [1, 2],
        "thresholds": [20],
        "primary_threshold": 20,
        "boundary": "left_closed_right_open",
        "peak_tie": "earliest",
        "missing_policy": "fail_incomplete_cycle",
        "targets": [2],
        "plot_cycles": [1, 2],
        "comparison_groups": {"prior": [1]},
    }
    metrics.save_json(tmp_path / "params.json", params)
    with pytest.raises(ValueError, match="identity mismatch"):
        metrics.run(inputs, tmp_path / "params.json", tmp_path / "out")
    manifest = json.loads(
        (tmp_path / "out/run_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "failed"
    assert manifest["agent_run"] is False


def test_existing_output_is_never_overwritten(tmp_path):
    (tmp_path / "protected").mkdir()
    with pytest.raises(ValueError, match="new directory"):
        metrics.run(tmp_path / "in", tmp_path / "params", tmp_path / "protected")
