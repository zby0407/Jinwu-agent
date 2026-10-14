"""Synthetic engineering checks. Fixtures never authorize real research runs."""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from jw.flare_forecast.engine import load

m = load("available24_model")


def contract():
    return {
        "schema_version": "available24-qualification-contract-v1",
        "project_id": "SYNTHETIC_ONLY",
        "features_physical": m.FEATURES,
        "features_history_baseline": m.HISTORY,
        "splits": m.SPLITS,
        "seed": m.SEED,
        "selection_metric": "mean_binary_Brier_C_M",
        "final_refit_roles": ["train", "selection"],
        "target_offset_hours": [0, 24],
        "source_mode": "hmi.sharp_cea_720s_nrt",
        "accepted_QUALITY_words": [1024],
        "max_observation_age_seconds": 10800,
        "training_wall_seconds_max": 60,
        "minimum_rows_per_role": 30,
        "minimum_train_rows_per_class": 20,
        "limitations": ["Synthetic engineering fixture only"],
    }


def frame():
    rows = []
    for role, year in (
        ("train", 2014),
        ("selection", 2021),
        ("calibration", 2022),
        ("test", 2023),
    ):
        for i in range(60):
            ref = pd.Timestamp(f"{year}-01-03T13:12:34Z") + pd.Timedelta(days=i)
            row = {
                "sample_id": f"SYNTHETIC_{role}_{i}",
                "reference_utc": m.utc_text(ref),
                "simulated_issue_utc": m.utc_text(ref),
                "target_start_utc": m.utc_text(ref),
                "target_end_utc": m.utc_text(ref + pd.Timedelta(hours=24)),
                "input_support_start_utc": m.utc_text(ref - pd.Timedelta(minutes=75)),
                "hmi_latest_support_end_utc": m.utc_text(
                    ref - pd.Timedelta(minutes=50)
                ),
                "hmi_latest_processing_utc": m.utc_text(ref - pd.Timedelta(seconds=1)),
                "observation_utc": m.utc_text(
                    ref - pd.Timedelta(minutes=61, seconds=15)
                ),
                "observation_age_seconds": 3675.0,
                "feature_max_observed_utc": m.utc_text(ref - pd.Timedelta(minutes=50)),
                "split": role,
                "status": "eligible",
                "magnetic_qualified": True,
                "label_qualified": True,
                "history_baseline_status": "qualified",
                "y_class": i % 3,
                "label_c1": int(i % 3 >= 1),
                "label_m1": int(i % 3 == 2),
                "future_coverage_fraction": 1.0,
                "future_max_gap_seconds": 0.0,
                "partial_patch_coverage": False,
                "all_magnetic_quality_allowed": True,
                "source_mode": "hmi.sharp_cea_720s_nrt",
            }
            for name in m.FEATURES:
                row[name] = 2.0 if name.endswith("_max") else 1.0
            row["patch_count"] = 1
            for hours in (24, 72, 168):
                for kind, value in (
                    ("c_count", 3),
                    ("m_count", 2),
                    ("x_count", 1),
                    ("max_peak_w_m2", 1e-5),
                ):
                    row[f"history_{hours}h_{kind}"] = value
            row["history_latest_event_end_utc"] = m.utc_text(
                ref - pd.Timedelta(hours=4)
            )
            rows.append(row)
    return pd.DataFrame(rows)


def test_exact_interfaces_and_frozen_rf_grid():
    m.validate_contract(contract())
    assert len(m.FEATURES) == 23
    assert len(m.HISTORY) == 12
    math = SimpleNamespace(parameter_signature=lambda value: value)
    candidates = m.candidates(math)
    assert len(candidates) == 9
    for _, model, _ in candidates:
        assert model.n_estimators == 200
        assert model.n_jobs == 4
        assert model.random_state == 20261011
        assert model.max_features == "sqrt"
    assert [(v.min_samples_leaf, v.max_depth) for _, v, _ in candidates] == [
        (leaf, depth) for leaf in (5, 15, 30) for depth in (4, 8, None)
    ]


def test_raw_quality_hex_and_decimal_are_equivalent_without_clearing_bits():
    assert m.normalized_quality_histogram({"0x00000400": 2, "1024": 1}) == {1024: 3}
    assert m.normalized_quality_histogram({"0x00000401": 1}) == {1025: 1}
    c = contract()
    c["limits"] = ["Retain restricted-source uncertainty"]
    assert m.contract_limits(c) == c["limits"]


@pytest.mark.parametrize(
    "histogram",
    [
        {"1024.0": 1},
        {"unknown": 1},
        {"0x100000400": 1},
        {"1024": 1.5},
        {"1024": True},
        {True: 1},
        {},
    ],
)
def test_unknown_quality_literals_or_histogram_counts_fail(histogram):
    with pytest.raises(ValueError):
        m.normalized_quality_histogram(histogram)


@pytest.mark.parametrize(
    "field,value",
    [
        ("accepted_QUALITY_words", [0, 1024]),
        ("max_observation_age_seconds", 20000),
        ("target_offset_hours", [24, 48]),
        ("seed", 123),
        ("selection_metric", "accuracy"),
        ("source_mode", "hmi.sharp_cea_720s"),
    ],
)
def test_contract_cannot_silently_relax_scientific_policy(field, value):
    c = contract()
    c[field] = value
    with pytest.raises(ValueError):
        m.validate_contract(c)


def test_primary_does_not_require_history_and_common_is_exact_subset():
    f = frame()
    f.loc[f.index[0], m.HISTORY] = np.nan
    f.loc[f.index[0], "history_baseline_status"] = "unknown_not_issued"
    m.validate_physical(f, contract())
    common = f.iloc[1:].copy()
    c = contract()
    c["minimum_train_rows_per_class"] = 19
    m.validate_common(f, common, c)


@pytest.mark.parametrize(
    "column,value",
    [
        ("reference_utc", "2026-01-01T00:00:00Z"),
        ("target_start_utc", "2024-01-01T00:00:00Z"),
        ("USFLUX_max", np.nan),
        ("USFLUX_median", -1),
        ("patch_count", 0),
        ("label_c1", 0),
        ("label_m1", 1),
        ("y_class", -1),
        ("future_coverage_fraction", 0.98),
        ("future_max_gap_seconds", 301),
        ("partial_patch_coverage", True),
        ("all_magnetic_quality_allowed", False),
    ],
)
def test_rejects_label_clock_quality_and_coverage_errors(column, value):
    f = frame()
    # Row 1 has C-only outcome, so either binary label mutation is inconsistent.
    f.loc[1, column] = value
    with pytest.raises(ValueError):
        m.validate_physical(f, contract())


def test_common_cannot_change_primary_values_or_include_extra_row():
    primary, common = frame(), frame()
    common.loc[0, "USFLUX_max"] = 9
    with pytest.raises(ValueError, match="Common cohort changed"):
        m.validate_common(primary, common, contract())
    common = frame()
    common.loc[0, "sample_id"] = "UNRELATED"
    with pytest.raises(ValueError, match="primary subset"):
        m.validate_common(primary, common, contract())


def test_actual_role_boundary_overlapping_label_rejected():
    f = frame()
    idx = f.index[f.split.eq("train")][-1]
    following = pd.Timestamp(f.loc[f.split.eq("selection"), "reference_utc"].min())
    f.loc[idx, "target_end_utc"] = m.utc_text(following)
    with pytest.raises(ValueError, match="overlap"):
        m.boundary_audit(f)


def test_test_window_must_finish_before_2026_label_gate():
    f = frame()
    idx = f.index[f.split.eq("test")][-1]
    ref = pd.Timestamp("2025-12-31T13:12:34Z")
    for name in ("reference_utc", "simulated_issue_utc", "target_start_utc"):
        f.loc[idx, name] = m.utc_text(ref)
    f.loc[idx, "target_end_utc"] = m.utc_text(ref + pd.Timedelta(hours=24))
    f.loc[idx, "hmi_latest_support_end_utc"] = m.utc_text(
        ref - pd.Timedelta(minutes=50)
    )
    f.loc[idx, "hmi_latest_processing_utc"] = m.utc_text(ref - pd.Timedelta(seconds=1))
    f.loc[idx, "observation_utc"] = m.utc_text(ref - pd.Timedelta(seconds=3675))
    with pytest.raises(ValueError, match="2026 outcomes"):
        m.validate_physical(f, contract())


def live_frame(now):
    f = frame().iloc[:1].copy()
    now = m.utc(now)
    f["reference_utc"] = m.utc_text(now)
    f["observation_utc"] = m.utc_text(now - pd.Timedelta(hours=1))
    f["hmi_latest_support_end_utc"] = m.utc_text(
        now - pd.Timedelta(minutes=48, seconds=45)
    )
    f["hmi_latest_processing_utc"] = m.utc_text(now - pd.Timedelta(minutes=30))
    f["source_retrieved_utc"] = m.utc_text(now - pd.Timedelta(minutes=10))
    f["feature_max_observed_utc"] = f["hmi_latest_support_end_utc"]
    f[["label_c1", "label_m1", "y_class"]] = np.nan
    return f


def test_live_freshness_and_availability_order():
    now = pd.Timestamp("2026-10-12T16:30:00Z")
    f = live_frame(now)
    m.validate_physical(f, contract(), live=True)
    stamp, age = m.validate_live_at_issue(f, now)
    assert stamp == now and age == 3600
    f["observation_utc"] = m.utc_text(now - pd.Timedelta(hours=3, seconds=1))
    with pytest.raises(ValueError, match="3h"):
        m.validate_live_at_issue(f, now)
    f = live_frame(now)
    f["source_retrieved_utc"] = m.utc_text(now + pd.Timedelta(seconds=1))
    with pytest.raises(ValueError, match="precede"):
        m.validate_live_at_issue(f, now)


def test_live_truth_must_stay_unknown():
    f = live_frame("2026-10-12T16:30:00Z")
    f["label_c1"] = 1
    with pytest.raises(ValueError, match="future truth"):
        m.validate_physical(f, contract(), live=True)


def review_fixture(tmp_path, role="data"):
    # This fixture is local unit-test data, never a real driver release.
    path = tmp_path / "SYNTHETIC_original_review.json"
    m.write_json(
        path, {"decision": "accept_with_limits", "synthetic_fixture_only": True}
    )
    session = tmp_path / "SYNTHETIC_session.json"
    m.write_json(
        session,
        {
            "status": "completed",
            "actual_original_native_JW_role": True,
            "synthetic_fixture_only": True,
        },
    )
    witness = tmp_path / "SYNTHETIC_witness.json"
    m.write_json(
        witness,
        {
            "call_id": "SYNTHETIC_CALL",
            "tool": "forecast_submit_data_review"
            if role == "data"
            else "forecast_submit_review",
            "review_sha256": m.digest(path),
            "synthetic_fixture_only": True,
        },
    )
    binding = tmp_path / "native_review_binding.json"
    value = {
        "role": role,
        "review_path": str(path),
        "review_sha256": m.digest(path),
        "manifest_sha256": "a" * 64,
        "contract_sha256": "b" * 64,
        "session_path": str(session),
        "session_sha256": m.digest(session),
        "tool_witness_path": str(witness),
        "tool_witness_sha256": m.digest(witness),
        "actual_tool_call_id": "SYNTHETIC_CALL",
    }
    m.write_json(binding, value)
    return path, binding, value


def test_native_review_binding_requires_exact_batch_hash_and_completed_witness(
    tmp_path,
):
    path, binding, value = review_fixture(tmp_path)
    review, hashes = m.native_review(path, "a" * 64, "b" * 64)
    assert review["decision"] == "accept_with_limits" and len(hashes) == 4
    with pytest.raises(ValueError, match="exact batch"):
        m.native_review(path, "c" * 64, "b" * 64)
    value["tool_witness_sha256"] = "d" * 64
    m.write_json(binding, value)
    with pytest.raises(ValueError, match="witness bytes"):
        m.native_review(path, "a" * 64, "b" * 64)


def test_unbound_operator_acceptance_is_not_native_data_gate(tmp_path):
    path = tmp_path / "operator.json"
    m.write_json(path, {"decision": "accept_with_limits"})
    with pytest.raises(FileNotFoundError):
        m.native_review(path, "a" * 64, "b" * 64)


def test_completed_session_without_actual_native_marker_is_rejected(tmp_path):
    path, binding, value = review_fixture(tmp_path)
    session = Path(value["session_path"])
    m.write_json(session, {"status": "completed"})
    value["session_sha256"] = m.digest(session)
    m.write_json(binding, value)
    with pytest.raises(ValueError, match="original native"):
        m.native_review(path, "a" * 64, "b" * 64)


def test_file_path_escape_and_hash_changes_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="Unsafe"):
        m.confined_child(tmp_path, "../escape")
    path = tmp_path / "file.txt"
    path.write_text("old", encoding="utf-8")
    before = {str(path): m.digest(path)}
    path.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="Read-only"):
        m.verify_unchanged(before)


def test_metric_years_never_add_2026():
    f = frame().query("split == 'test'").copy()
    math = SimpleNamespace(
        binary_metrics=lambda y, p, t, q: {
            "n": len(y),
            "positive_rate": float(np.mean(y)),
            "Brier": float(np.mean((y - p) ** 2)),
        },
        reliability_bins=lambda *args: [],
    )
    pred = {
        "selected_calibrated": {
            m.TARGETS[0]: np.full(len(f), 0.6),
            m.TARGETS[1]: np.full(len(f), 0.3),
        }
    }
    _, years, _, _ = m.evaluate(
        math, f, pred, {m.TARGETS[0]: 0.6, m.TARGETS[1]: 0.3}, "primary"
    )
    assert set(years.year) == {2023, 2024, 2025}


def test_frozen_methods_load_without_fitting():
    if not Path(m.__file__).with_name("frozen_shared").exists():
        pytest.skip("Root has not copied frozen source byte snapshots yet")
    methods, math = m.load_shared()
    assert len(methods.FEATURES) == 23
    p = np.asarray([[0.5, 0.3, 0.2]])
    target = math.target_probabilities(p)
    assert target[m.TARGETS[0]][0] == 0.5
    assert target[m.TARGETS[1]][0] == 0.2


def test_sparse_common_cohort_does_not_block_primary():
    primary = frame()
    common = primary.iloc[:3].copy()
    m.validate_physical(primary, contract())
    m.validate_common(primary, common, contract())
    empty = primary.iloc[:0].copy()
    m.validate_common(primary, empty, contract())


def test_train_boundaries_and_no_issue_with_synthetic_spies(tmp_path, monkeypatch):
    primary = frame()
    common = primary.iloc[:0].copy()
    input_dir = tmp_path / "prepared"
    input_dir.mkdir()
    primary.to_csv(input_dir / "daily_physical.csv", index=False)
    common.to_csv(input_dir / "daily_common.csv", index=False)
    cpath = tmp_path / "contract.json"
    c = contract()
    c.update(source_manifest_sha256="a" * 64, quality_policy_sha256="b" * 64)
    m.write_json(cpath, c)
    raw = tmp_path / "SYNTHETIC_RAW.txt"
    raw.write_text("synthetic fixture, no solar data", encoding="utf-8")
    hashes = {str(raw): m.digest(raw)}
    m.write_json(
        input_dir / "input_hashes_readonly.json",
        {"before": hashes, "after": hashes, "all_inputs_hashes_unchanged": True},
    )
    m.write_json(
        input_dir / "validation.json",
        {
            "status": "passed",
            "all_inputs_hashes_unchanged": True,
            "raw_inputs_unchanged": True,
        },
    )
    records = [
        {"path": p.name, "sha256": m.digest(p), "bytes": p.stat().st_size}
        for p in sorted(input_dir.iterdir())
    ]
    m.write_json(
        input_dir / "prepared_manifest.json",
        {
            "schema_version": "available24-prepared-manifest-v1",
            "contract_sha256": m.digest(cpath),
            "features_physical": m.FEATURES,
            "features_history_baseline": m.HISTORY,
            "source_manifest_sha256": c["source_manifest_sha256"],
            "quality_policy_sha256": c["quality_policy_sha256"],
            "files": records,
        },
    )
    review_directory = tmp_path / "SYNTHETIC_REVIEW"
    review_directory.mkdir()
    review_path, binding_path, binding = review_fixture(review_directory)
    binding.update(
        manifest_sha256=m.digest(input_dir / "prepared_manifest.json"),
        contract_sha256=m.digest(cpath),
    )
    m.write_json(binding_path, binding)
    review_calls = []
    original_review = m.native_review

    def reviewed(p, ms, cs):
        review_calls.append((ms, cs))
        return original_review(p, ms, cs)

    monkeypatch.setattr(m, "native_review", reviewed)
    math_calls = []

    def fit(x_train, y_train, x_selection, y_selection, candidates, on_selected):
        math_calls.append(
            (x_train.shape, len(y_train), x_selection.shape, len(y_selection))
        )
        assert x_train.shape == (60, 23)
        assert x_selection.shape == (60, 23)
        assert len(candidates) == 9
        on_selected(
            {
                "selected_model_id": "SYNTHETIC_NO_FIT",
                "locked_configuration_signature": {"synthetic_fixture_only": True},
            }
        )
        return (
            "SYNTHETIC_NO_FIT",
            object(),
            [],
            {
                "final_refit_calls": 1,
                "final_refit_rows": len(y_train) + len(y_selection),
                "final_refit_roles": ["train", "selection"],
            },
        )

    methods, math = m.load_shared()
    monkeypatch.setattr(math, "fit_selection_then_refit", fit)
    monkeypatch.setattr(
        math,
        "probability_matrix",
        lambda model, x: np.tile([0.4, 0.3, 0.3], (len(x), 1)),
    )
    monkeypatch.setattr(
        methods,
        "permutation_scores",
        lambda *args, **kwargs: pd.DataFrame(
            {
                "feature": m.FEATURES,
                "role": m.IMPORTANCE_ROLE,
                "mean_Brier_increase": 0.0,
                "mean_Brier_increase_std": 0.0,
                "repeats": 10,
            }
        ),
    )
    monkeypatch.setattr(
        math,
        "block_bootstrap",
        lambda *args, **kwargs: pd.DataFrame(
            {
                "model_id": ["selected_calibrated"],
                "target": [m.TARGETS[0]],
                "metric": ["Brier"],
                "bootstrap_total_draws": [kwargs["draws"]],
            }
        ),
    )
    monkeypatch.setattr(m, "load_shared", lambda: (methods, math))
    monkeypatch.setattr(
        m.joblib,
        "dump",
        lambda value, path: Path(path).write_text(
            "SYNTHETIC_NO_MODEL", encoding="utf-8"
        ),
    )
    result = m.train(input_dir, cpath, review_path, tmp_path / "forecast")
    assert result["status"] == "completed_training_only"
    assert len(math_calls) == 1
    assert len(review_calls) == 1
    out = tmp_path / "forecast"
    summary = m.read_json(out / "summary.json")
    assert summary["issued_forecast"] is None
    assert summary["calibration"]["history_only"]["status"] == "unknown_not_fit"
    assert not (out / "issued_forecast.json").exists()
    refit = m.read_json(out / "final_refit_receipt.json")
    assert refit["final_refit_rows"] == 120
    assert refit["test_estimator_fit_calls"] == 0
    assert refit["physical_final_fit"]["roles"] == ["selection", "train"]
    assert m.read_json(out / "proof.json")["no_live_issue"] is True


def test_issue_captures_real_afterpredict_stamp_and_refuses_second_issue(
    tmp_path, monkeypatch
):
    before = pd.Timestamp("2026-10-12T16:30:00Z")
    after = before + pd.Timedelta(seconds=2)
    live_dir, model_dir = tmp_path / "live", tmp_path / "model"
    live_dir.mkdir()
    model_dir.mkdir()
    live_frame(before).to_csv(live_dir / "live_features.csv", index=False)
    m.write_json(live_dir / "live_manifest.json", {"synthetic_fixture_only": True})
    m.write_json(model_dir / "proof.json", {"synthetic_fixture_only": True})
    cpath = tmp_path / "contract.json"
    m.write_json(cpath, contract())

    class Clock:
        calls = 0

        @classmethod
        def now(cls, tz):
            cls.calls += 1
            return (before if cls.calls <= 2 else after).to_pydatetime()

    monkeypatch.setattr(m, "datetime", Clock)
    monkeypatch.setattr(
        m,
        "checked_live",
        lambda *args: (
            contract(),
            {"future_outcome_read": False},
            {},
            pd.read_csv(live_dir / "live_features.csv"),
            live_dir / "live_manifest.json",
        ),
    )
    monkeypatch.setattr(m, "checked_forecast", lambda *args: ({}, {}))
    monkeypatch.setattr(
        m, "native_review", lambda *args: ({"decision": "accept_with_limits"}, {})
    )
    loaded = {
        "contract_sha256": m.digest(cpath),
        "script_sha256": m.digest(m.__file__),
        "feature_columns": m.FEATURES,
        "target_offset_hours": [0, 24],
        "selected_model": object(),
        "selected_temperature": 1.0,
        "selected_model_id": "SYNTHETIC_NO_FIT",
        "fit_ranges": {name: [0, 9] for name in m.FEATURES},
    }
    monkeypatch.setattr(m.joblib, "load", lambda *args: loaded)
    _, math = m.load_shared()
    predictions = []
    monkeypatch.setattr(
        math,
        "probability_matrix",
        lambda model, x: predictions.append(x.shape) or np.asarray([[0.2, 0.4, 0.4]]),
    )
    monkeypatch.setattr(m, "load_shared", lambda: (None, math))
    result = m.issue(
        model_dir,
        live_dir,
        cpath,
        tmp_path / "SYNTHETIC_review.json",
        tmp_path / "issued",
    )
    assert result["issued_utc"] == m.utc_text(after)
    assert m.utc(result["prediction_window"]["start"]) == after
    assert m.utc(result["prediction_window"]["end"]) - after == pd.Timedelta(hours=24)
    issued = m.read_json(tmp_path / "issued" / "issued_forecast.json")
    assert issued["targets"][0]["probability"] == 0.8
    assert all(
        t["observed_outcome"] is None and t["future_accuracy"] is None
        for t in issued["targets"]
    )
    assert predictions == [(1, 23)]
    with pytest.raises(FileExistsError):
        m.issue(
            model_dir,
            live_dir,
            cpath,
            tmp_path / "SYNTHETIC_review.json",
            tmp_path / "issued_again",
        )
    assert predictions == [(1, 23)]


def live_source_fixture(
    tmp_path, future_outcome_read=False, bad_quality=False, hex_quality=False
):
    directory = tmp_path / "live_source_fixture"
    directory.mkdir()
    cpath = tmp_path / "contract.json"
    m.write_json(cpath, contract())
    f = live_frame("2026-10-12T16:30:00Z")
    f["source_series"] = "hmi.sharp_cea_720s_nrt"
    f["QUALITY_exact_full_word"] = 1024
    f.to_csv(directory / "live_physical.csv", index=False)
    m.write_json(
        directory / "live_qualification.json",
        {
            "status": "eligible",
            "contract_sha256": m.digest(cpath),
            "partial_patch_aggregation": False,
            "no_history_features_created": True,
            "future_outcome_read": False,
            "sample_id": f.iloc[0].sample_id,
            "central_patch_count": 1,
            "live_physical_csv_sha256": m.digest(directory / "live_physical.csv"),
            "quality_raw_histograms": {
                "QUALITY": {
                    "1025"
                    if bad_quality
                    else ("0x00000400" if hex_quality else "1024"): 1
                }
            },
        },
    )
    for name in (
        "source_requests.json",
        "row_audit.json",
        "source_schema_validation.json",
    ):
        m.write_json(directory / name, {"synthetic_fixture_only": True})
    records = [
        {"path": p.name, "sha256": m.digest(p), "bytes": p.stat().st_size}
        for p in sorted(directory.iterdir())
    ]
    m.write_json(
        directory / "source_manifest.json",
        {
            "schema_version": "available24-live-source-manifest-v1",
            "status": "eligible",
            "contract_sha256": m.digest(cpath),
            "future_outcome_read": future_outcome_read,
            "fit_predict_plot_performed": False,
            "forecast_issued": False,
            "files": records,
            "readonly_input_hashes": {str(cpath): m.digest(cpath)},
        },
    )
    return directory, cpath


@pytest.mark.parametrize("hex_quality", [False, True])
def test_original_source_manifest_adapts_interface_without_rewriting_csv(
    tmp_path, hex_quality
):
    directory, cpath = live_source_fixture(tmp_path, hex_quality=hex_quality)
    original_sha = m.digest(directory / "live_physical.csv")
    _, _, before, f, manifest_path = m.checked_live(directory, cpath)
    assert f.source_mode.eq("hmi.sharp_cea_720s_nrt").all()
    assert f.magnetic_qualified.eq(True).all()
    assert manifest_path.name == "source_manifest.json"
    assert m.digest(directory / "live_physical.csv") == original_sha
    m.verify_unchanged(before)


@pytest.mark.parametrize("future_read,bad_quality", [(True, False), (False, True)])
def test_source_truth_access_and_unregistered_quality_rejected(
    tmp_path, future_read, bad_quality
):
    directory, cpath = live_source_fixture(tmp_path, future_read, bad_quality)
    with pytest.raises(ValueError):
        m.checked_live(directory, cpath)


def test_historical_mixed_patch_clock_checks_oldest_and_retains_latest():
    f = frame()
    latest = pd.to_datetime(f.observation_utc, utc=True)
    f["observation_first_utc"] = (latest - pd.Timedelta(seconds=30)).map(m.utc_text)
    f["observation_age_seconds"] = f.observation_age_seconds + 30
    m.validate_physical(f, contract())
    assert np.array_equal(f.observation_utc, latest.map(m.utc_text))
    ref = pd.Timestamp(f.loc[0, "reference_utc"])
    f.loc[0, "observation_first_utc"] = m.utc_text(
        ref - pd.Timedelta(hours=3, seconds=1)
    )
    f.loc[0, "observation_age_seconds"] = 10801
    with pytest.raises(ValueError, match="3h"):
        m.validate_physical(f, contract())


def test_historical_oldest_clock_cannot_be_later_than_latest():
    f = frame()
    latest = pd.to_datetime(f.observation_utc, utc=True)
    f["observation_first_utc"] = (latest + pd.Timedelta(seconds=1)).map(m.utc_text)
    f["observation_age_seconds"] = f.observation_age_seconds - 1
    with pytest.raises(ValueError, match="Oldest observation"):
        m.validate_physical(f, contract())
