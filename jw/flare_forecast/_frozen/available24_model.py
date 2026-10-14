"""Bounded physical training, separate live issue and result-only plotting.

Engineering entry points require frozen inputs and genuine native JW review
bindings. This module never downloads data or runs the production WebUI.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import sys
import threading
import time

sys.dont_write_bytecode = True
for _name in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ[_name] = "4"

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

ROOT = Path(__file__).resolve().parents[1]
SEED = 20261011
TARGETS = ("C1.0+", "M1.0+")
PHYSICAL = (
    "USFLUX",
    "TOTUSJH",
    "TOTUSJZ",
    "ABSNJZH",
    "SAVNCPP",
    "MEANALP",
    "R_VALUE",
    "TOTPOT",
    "MEANSHR",
    "AREA_ACR",
    "MEANPOT",
)
FEATURES = [
    f"{name}_{aggregation}" for name in PHYSICAL for aggregation in ("max", "median")
] + ["patch_count"]
HISTORY = [
    f"history_{hours}h_{kind}"
    for hours in (24, 72, 168)
    for kind in ("c_count", "m_count", "x_count", "max_peak_w_m2")
]
ROLES = ("train", "selection", "calibration", "test")
SPLITS = {
    "train": ["2011-01-01", "2020-12-31"],
    "selection": ["2021-01-01", "2021-12-31"],
    "calibration": ["2022-01-01", "2022-12-31"],
    "test": ["2023-01-01", "2025-12-31"],
}
IMPORTANCE_ROLE = "heldout_test_descriptive_permutation_no_feature_selection"
SHARED_HASHES = {
    "physical_methods_frozen_v1.py": "69dd04bcbc8fb909df3d23cdf32ee667acf7d99c72f38988ab3b9c58e63ea4dc",
    "forecast_math_reference.py": "1af883b3cdba0717d827a1c8768d80f9720103f57795f6825f56b142e89090d1",
    "frozen_math_core.py": "0582ae6f2085e18029b640c22a95b7be634c4e593560283c004b91ebec8c93d3",
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def json_safe(value):
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, pd.Timestamp):
        return utc_text(value)
    return value


def write_json(path, value):
    Path(path).write_text(
        json.dumps(json_safe(value), ensure_ascii=False, indent=2, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )


def utc(value):
    result = pd.Timestamp(value)
    if pd.isna(result) or result.tzinfo is None:
        raise ValueError("Explicit nonempty UTC timestamp required")
    return result.tz_convert("UTC")


def utc_text(value):
    return utc(value).isoformat().replace("+00:00", "Z")


def times(frame, column):
    return pd.Series(
        [utc(v) for v in frame[column]], index=frame.index, dtype="datetime64[ns, UTC]"
    )


def confined_child(root, relative):
    root, relative = Path(root).resolve(), Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Unsafe artifact relative path")
    result = (root / relative).resolve()
    if not result.is_relative_to(root):
        raise ValueError("Artifact path escapes its root")
    return result


def verify_row(root, row, before):
    path = confined_child(root, row["path"])
    if digest(path) != row.get("sha256") or path.stat().st_size != row.get("bytes"):
        raise ValueError("Artifact bytes/hash mismatch: " + path.name)
    before[str(path)] = digest(path)
    return path


def verify_unchanged(before):
    if any(digest(path) != expected for path, expected in before.items()):
        raise ValueError("Read-only input/code changed during operation")


def load_shared():
    directory = Path(__file__).with_name("frozen_shared")
    for name, expected in SHARED_HASHES.items():
        if digest(directory / name) != expected:
            raise ValueError("Frozen mathematics bytes differ: " + name)

    def module(name):
        spec = importlib.util.spec_from_file_location(
            "available24_" + name[:-3], directory / name
        )
        result = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(result)
        return result

    return module("physical_methods_frozen_v1.py"), module("forecast_math_reference.py")


def validate_contract(contract):
    if contract.get("schema_version") != "available24-qualification-contract-v1":
        raise ValueError("Exact new available24 contract schema required")
    if (
        contract.get("features_physical", contract.get("feature_columns")) != FEATURES
        or contract.get(
            "features_history_baseline", contract.get("baseline_feature_columns")
        )
        != HISTORY
    ):
        raise ValueError("Exact 23 physical / 12 baseline feature contract required")
    if contract.get("splits") != SPLITS:
        raise ValueError("Frozen 2011-2022 development / 2023-2025 test years required")
    if (
        contract.get("seed") != SEED
        or contract.get("selection_metric") != "mean_binary_Brier_C_M"
        or contract.get("final_refit_roles") != ["train", "selection"]
    ):
        raise ValueError("Frozen selection and single refit policy required")
    if (
        contract.get("target_offset_hours") != [0, 24]
        or contract.get("source_mode") != "hmi.sharp_cea_720s_nrt"
    ):
        raise ValueError("Same NRT source and issue-to-24h target required")
    if (
        contract.get("accepted_QUALITY_words") != [1024]
        or contract.get("max_observation_age_seconds") != 10800
    ):
        raise ValueError("Exact warning cohort and <=3h input age policy required")
    if contract.get("importance_role", IMPORTANCE_ROLE) != IMPORTANCE_ROLE:
        raise ValueError("Heldout importance must remain descriptive")
    registered = {
        "random_forest_grid": {
            "n_estimators": [200],
            "min_samples_leaf": [5, 15, 30],
            "max_depth": [4, 8, None],
        },
        "max_features": "sqrt",
        "n_jobs": 4,
        "calibration": "2022_scalar_temperature_only",
        "probability_threshold": 0.5,
        "bootstrap_draws": 200,
        "importance_repeats": 10,
        "common_insufficiency_policy": "unknown_not_fit_without_blocking_primary",
        "future_outcome_read_during_live": False,
    }
    for name, expected in registered.items():
        if contract.get(name, expected) != expected:
            raise ValueError("Frozen method contract changed: " + name)
    return contract


def contract_limits(contract):
    return contract.get("limits", contract.get("limitations", []))


def normalized_quality_histogram(histogram):
    if not isinstance(histogram, dict) or not histogram:
        raise ValueError("Complete original primary QUALITY histogram required")
    result = {}
    for raw_word, count in histogram.items():
        if (
            not isinstance(raw_word, (str, int))
            or isinstance(raw_word, bool)
            or type(count) is not int
            or count <= 0
        ):
            raise ValueError("Unknown QUALITY word or nonintegral patch count")
        text = str(raw_word).strip()
        try:
            word = int(text, 16 if text.lower().startswith("0x") else 10)
        except ValueError as exc:
            raise ValueError("Invalid literal primary QUALITY word") from exc
        if not -(2**31) <= word < 2**32:
            raise ValueError("QUALITY integer exceeds its uint32 namespace")
        word &= 0xFFFFFFFF
        result[word] = result.get(word, 0) + count
    return result


def native_review(path, manifest_sha, contract_sha, role="data"):
    """Verify original review plus native-session and submitted-tool witnesses."""
    path = Path(path).resolve()
    review = read_json(path)
    decision = review.get("decision")
    if decision not in ("accept", "accept_with_limits"):
        raise ValueError("Genuine native review has not accepted this process/batch")
    binding_path = path.with_name("native_review_binding.json")
    binding = read_json(binding_path)
    if (
        binding.get("role") != role
        or binding.get("manifest_sha256") != manifest_sha
        or binding.get("contract_sha256") != contract_sha
    ):
        raise ValueError("Native review does not bind this exact batch and contract")
    if Path(binding.get("review_path", "")).resolve() != path or binding.get(
        "review_sha256"
    ) != digest(path):
        raise ValueError("Native review bytes are not bound")
    session_path = Path(binding["session_path"])
    witness_path = Path(binding["tool_witness_path"])
    if digest(session_path) != binding.get("session_sha256") or digest(
        witness_path
    ) != binding.get("tool_witness_sha256"):
        raise ValueError("Native review session/tool witness bytes differ")
    session = read_json(session_path)
    if (
        session.get("status") != "completed"
        or session.get("actual_original_native_JW_role", session.get("actual_native"))
        is not True
    ):
        raise ValueError("Review must have a completed original native JW session")
    call_id = binding.get("actual_tool_call_id")
    if not isinstance(call_id, str) or not call_id:
        raise ValueError("Native review actual AI tool-call ID required")
    witness_text = witness_path.read_text(encoding="utf-8-sig")
    if call_id not in witness_text:
        raise ValueError("Actual native review call absent from tool witness")
    witnesses = (
        [json.loads(line) for line in witness_text.splitlines() if line.strip()]
        if witness_path.suffix == ".jsonl"
        else [json.loads(witness_text)]
    )
    matches = [
        w
        for w in witnesses
        if w.get("call_id", w.get("actual_tool_call_id", w.get("tool_call_id")))
        == call_id
    ]
    if len(matches) != 1:
        raise ValueError("Unique actual native tool submission witness required")
    entry = matches[0]
    if entry.get("tool", entry.get("name", entry.get("tool_name"))) not in (
        "forecast_submit_data_review",
        "forecast_submit_review",
        "available24_submit_data_review",
        "available24_submit_review",
    ):
        raise ValueError("Review witness is not a native submission tool")
    if entry.get("review_sha256", entry.get("result_sha256")) != digest(path):
        raise ValueError(
            "Native submission witness does not bind original review bytes"
        )
    return review, {
        str(p): digest(p) for p in (path, binding_path, session_path, witness_path)
    }


def checked_manifest(directory, name, contract_path, required):
    directory, contract_path = Path(directory).resolve(), Path(contract_path).resolve()
    contract = validate_contract(read_json(contract_path))
    manifest_path = directory / name
    manifest = read_json(manifest_path)
    before = {
        str(contract_path): digest(contract_path),
        str(manifest_path): digest(manifest_path),
        str(Path(__file__)): digest(__file__),
    }
    for filename, expected in SHARED_HASHES.items():
        path = Path(__file__).with_name("frozen_shared") / filename
        if digest(path) != expected:
            raise ValueError("Frozen shared dependency differs")
        before[str(path)] = expected
    if (
        manifest.get("contract_sha256") != digest(contract_path)
        or manifest.get("features_physical") != FEATURES
        or manifest.get("features_history_baseline") != HISTORY
        or manifest.get("schema_version") != "available24-prepared-manifest-v1"
    ):
        raise ValueError("Prepared manifest contract/physical schema differs")
    for name in ("source_manifest_sha256", "quality_policy_sha256"):
        if manifest.get(name) != contract.get(name) or not contract.get(name):
            raise ValueError("Prepared source/quality provenance differs")
    records = manifest.get("files", [])
    paths = [r.get("path") for r in records]
    if len(paths) != len(set(paths)) or not required.issubset(paths):
        raise ValueError("Every required prepared artifact needs a unique byte record")
    for row in records:
        verify_row(directory, row, before)
    validation = read_json(directory / "validation.json")
    if (
        validation.get("status") != "passed"
        or validation.get("all_inputs_hashes_unchanged") is not True
        or validation.get("raw_inputs_unchanged") is not True
    ):
        raise ValueError("Upstream raw-input/interface qualification did not pass")
    hashes = read_json(directory / "input_hashes_readonly.json")
    if hashes.get("all_inputs_hashes_unchanged") is not True or hashes.get(
        "before"
    ) != hashes.get("after"):
        raise ValueError("Raw input hash receipt differs")
    before.update(hashes["before"])
    verify_unchanged(before)
    return contract, manifest, before


def role_groups(frame):
    return {
        role: frame.loc[frame.split.eq(role)].copy().reset_index(drop=True)
        for role in ROLES
    }


def validate_physical(frame, contract, live=False, enforce_minimum=True):
    required = set(FEATURES) | {
        "sample_id",
        "reference_utc",
        "hmi_latest_support_end_utc",
        "hmi_latest_processing_utc",
        "magnetic_qualified",
        "status",
        "source_mode",
    }
    if not live:
        required |= {
            "split",
            "label_c1",
            "label_m1",
            "y_class",
            "target_start_utc",
            "target_end_utc",
            "label_qualified",
            "future_coverage_fraction",
            "future_max_gap_seconds",
            "input_support_start_utc",
            "observation_utc",
            "observation_age_seconds",
        }
    if (
        frame.empty
        or not required.issubset(frame)
        or frame.sample_id.duplicated().any()
        or frame.reference_utc.duplicated().any()
    ):
        raise ValueError("Missing exact clocks/physical interface or duplicate samples")
    if (
        not frame.status.eq("eligible").all()
        or not frame.magnetic_qualified.eq(True).all()
        or not frame.source_mode.eq(contract["source_mode"]).all()
    ):
        raise ValueError("Excluded or unknown magnetic samples cannot reach a model")
    x = frame[FEATURES].to_numpy(float)
    unsigned = [name for name in FEATURES if not name.startswith("MEANALP_")]
    if not np.isfinite(x).all() or (frame[unsigned].to_numpy(float) < 0).any():
        raise ValueError("Missing or invalid physical inputs cannot be zero-filled")
    if not (
        (frame.patch_count > 0) & (frame.patch_count == np.floor(frame.patch_count))
    ).all():
        raise ValueError("Positive integral complete patch count required")
    for name in PHYSICAL:
        if not (frame[name + "_max"] >= frame[name + "_median"]).all():
            raise ValueError("Physical max below median")
    if (
        "all_magnetic_quality_allowed" in frame
        and not frame.all_magnetic_quality_allowed.eq(True).all()
    ):
        raise ValueError("Magnetic quality cohort failed")
    if (
        "partial_patch_coverage" in frame
        and not frame.partial_patch_coverage.eq(False).all()
    ):
        raise ValueError("Partial central patch coverage cannot reach model")
    if live:
        if len(frame) != 1:
            raise ValueError("Exactly one current eligible group required")
        for name in ("label_c1", "label_m1", "y_class"):
            if name in frame and frame[name].notna().any():
                raise ValueError("Live future truth must remain unknown")
        return
    refs = times(frame, "reference_utc")
    if (
        not times(frame, "target_start_utc").eq(refs).all()
        or not times(frame, "target_end_utc").eq(refs + pd.Timedelta(hours=24)).all()
    ):
        raise ValueError("Every sample needs its own simulated issue-to-24h window")
    support, produced = (
        times(frame, "hmi_latest_support_end_utc"),
        times(frame, "hmi_latest_processing_utc"),
    )
    if (
        not (support <= refs).all()
        or not (produced <= refs).all()
        or not refs.eq(
            pd.concat((support, produced), axis=1).max(axis=1) + pd.Timedelta(seconds=1)
        ).all()
    ):
        raise ValueError(
            "Observation support/production not available or too old at simulated issue"
        )
    oldest_field = (
        "observation_first_utc"
        if "observation_first_utc" in frame
        else "observation_utc"
    )
    oldest = times(frame, oldest_field)
    if not (oldest <= times(frame, "observation_utc")).all():
        raise ValueError("Oldest observation is later than latest centre")
    age = (refs - oldest).dt.total_seconds()
    if not ((age >= 0) & (age <= 10800)).all() or not np.allclose(
        age, frame.observation_age_seconds.to_numpy(float), rtol=0, atol=1e-6
    ):
        raise ValueError("Actual observation age exceeds 3h or recorded age differs")
    expected = pd.Series("excluded", index=frame.index)
    for role, (start, end) in SPLITS.items():
        expected.loc[
            (refs >= pd.Timestamp(start, tz="UTC"))
            & (refs < pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1))
        ] = role
    if not expected.eq(frame.split).all() or expected.eq("excluded").any():
        raise ValueError(
            "No 2026 fit/select/cal/test and exact chronological roles required"
        )
    if (
        times(frame.loc[frame.split.eq("test")], "target_end_utc")
        > utc(contract.get("test_target_end_max_utc", "2026-01-01T00:00:00Z"))
    ).any():
        raise ValueError("Test target labels extend into unregistered 2026 outcomes")
    if (
        not frame.label_qualified.eq(True).all()
        or not np.isin(frame.y_class, (0, 1, 2)).all()
        or not np.array_equal(frame.label_c1, frame.y_class >= 1)
        or not np.array_equal(frame.label_m1, frame.y_class == 2)
    ):
        raise ValueError("Unknown or nonnested labels cannot become negatives")
    f, g = (
        frame.future_coverage_fraction.to_numpy(float),
        frame.future_max_gap_seconds.to_numpy(float),
    )
    if (
        not np.isfinite(f).all()
        or not np.isfinite(g).all()
        or not ((f >= 0.99) & (f <= 1) & (g >= 0) & (g <= 300)).all()
    ):
        raise ValueError("Future coverage qualification failed")
    groups = role_groups(frame)
    if enforce_minimum and (
        min(len(g) for g in groups.values()) < contract.get("minimum_rows_per_role", 30)
        or min(np.bincount(groups["train"].y_class.to_numpy(int), minlength=3))
        < contract.get("minimum_train_rows_per_class", 20)
    ):
        raise ValueError("Insufficient role/class counts under frozen thresholds")
    for target in (
        frame.loc[frame.split.eq("test"), "y_class"] >= 1,
        frame.loc[frame.split.eq("test"), "y_class"] == 2,
    ):
        if enforce_minimum and len(set(target)) != 2:
            raise ValueError("Both binary test targets require both observed classes")
    boundary_audit(frame, allow_empty=not enforce_minimum)


def boundary_audit(frame, allow_empty=False):
    groups, checks = role_groups(frame), []
    for earlier, later in zip(ROLES, ROLES[1:]):
        a, b = groups[earlier], groups[later]
        if a.empty or b.empty:
            if allow_empty:
                checks.append(
                    {
                        "earlier_role": earlier,
                        "later_role": later,
                        "status": "unknown_empty_common_role",
                        "passed": None,
                    }
                )
                continue
            raise ValueError("Empty role cannot establish temporal isolation")
        cutoff = min(
            times(b, "input_support_start_utc").min(),
            times(b, "reference_utc").min() - pd.Timedelta(hours=3),
        )
        end = times(a, "target_end_utc").max()
        if end > cutoff:
            raise ValueError("Previous labels overlap next role input/history cutoff")
        checks.append(
            {
                "earlier_role": earlier,
                "later_role": later,
                "previous_max_target_end_utc": utc_text(end),
                "following_input_cutoff_utc": utc_text(cutoff),
                "passed": True,
            }
        )
    return checks


def validate_common(primary, common, contract):
    if common.empty:
        if not (
            set(FEATURES + HISTORY) | {"sample_id", "split", "reference_utc"}
        ).issubset(common):
            raise ValueError("Empty common cohort must retain its explicit schema")
        return
    validate_physical(common, contract, enforce_minimum=False)
    if (
        not set(HISTORY).issubset(common)
        or not common.history_baseline_status.eq("qualified").all()
    ):
        raise ValueError("Common-date history must be independently qualified")
    p, c = primary.set_index("sample_id"), common.set_index("sample_id")
    if not c.index.isin(p.index).all():
        raise ValueError("Common cohort must be a primary subset")
    compare = FEATURES + [
        "reference_utc",
        "target_start_utc",
        "target_end_utc",
        "split",
        "label_c1",
        "label_m1",
        "y_class",
    ]
    for name in compare:
        if not np.array_equal(c[name].to_numpy(), p.loc[c.index, name].to_numpy()):
            raise ValueError("Common cohort changed primary features, labels or clocks")
    values = common[HISTORY].to_numpy(float)
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError("Unknown history cannot be negative- or zero-filled")
    for hours in (24, 72, 168):
        c_count, m_count, x_count = [
            common[f"history_{hours}h_{kind}_count"].to_numpy(float)
            for kind in ("c", "m", "x")
        ]
        if not ((c_count >= m_count) & (m_count >= x_count)).all() or any(
            not np.equal(v, np.floor(v)).all() for v in (c_count, m_count, x_count)
        ):
            raise ValueError("History counts must be nested integers")
    if "history_latest_event_end_utc" in common:
        ends = pd.to_datetime(
            common.history_latest_event_end_utc, utc=True, errors="coerce"
        )
        cutoff = times(common, "reference_utc") - pd.Timedelta(hours=3)
        if (ends.notna() & (ends > cutoff)).any():
            raise ValueError("History event ends beyond conservative cutoff")


def candidates(math):
    return [
        (
            f"physical_RF_leaf{leaf}_depth{depth}",
            model,
            math.parameter_signature(model.get_params(deep=False)),
        )
        for leaf in (5, 15, 30)
        for depth in (4, 8, None)
        for model in [
            RandomForestClassifier(
                n_estimators=200,
                min_samples_leaf=leaf,
                max_depth=depth,
                max_features="sqrt",
                n_jobs=4,
                random_state=SEED,
            )
        ]
    ]


def physical_predictions(math, model, calibration, class_q, frame):
    result = {
        "selected_calibrated": math.target_probabilities(
            math.apply_temperature(
                math.probability_matrix(model, frame[FEATURES].to_numpy(float)),
                calibration["temperature"],
            )
        ),
        "train_climatology": math.target_probabilities(
            np.tile(class_q, (len(frame), 1))
        ),
    }
    for name, value in (("always_yes", 1.0), ("always_no", 0.0)):
        result[name] = {target: np.full(len(frame), value) for target in TARGETS}
    return result


def evaluate(math, frame, predictions, references, cohort):
    y = frame.y_class.to_numpy(int)
    outcomes = {TARGETS[0]: y >= 1, TARGETS[1]: y == 2}
    years = times(frame, "reference_utc").dt.year.to_numpy()
    rows, yearly, bins = [], [], []
    for name, probabilities in predictions.items():
        for target in TARGETS:
            metric = math.binary_metrics(
                outcomes[target], probabilities[target], 0.5, references[target]
            )
            metric.update(
                always_yes_event_accuracy=metric["positive_rate"],
                always_no_event_accuracy=1 - metric["positive_rate"],
            )
            rows.append(
                {"cohort": cohort, "model_id": name, "target": target, **metric}
            )
            bins += [
                {"cohort": cohort, **r}
                for r in math.reliability_bins(
                    outcomes[target].astype(int), probabilities[target], name, target
                )
            ]
            for year in (2023, 2024, 2025):
                mask = years == year
                if not mask.any():
                    yearly.append(
                        {
                            "cohort": cohort,
                            "model_id": name,
                            "target": target,
                            "year": year,
                            "n": 0,
                            "status": "no_qualified_completed_windows",
                        }
                    )
                else:
                    yearly.append(
                        {
                            "cohort": cohort,
                            "model_id": name,
                            "target": target,
                            "year": year,
                            "status": "completed_historical_windows",
                            **math.binary_metrics(
                                outcomes[target][mask],
                                probabilities[target][mask],
                                0.5,
                                references[target],
                            ),
                        }
                    )
    return (
        pd.DataFrame(
            rows,
            columns=None if rows else ["cohort", "model_id", "target", "n", "Brier"],
        ),
        pd.DataFrame(
            yearly,
            columns=None
            if yearly
            else ["cohort", "model_id", "target", "year", "n", "status"],
        ),
        bins,
        outcomes,
    )


def pool_receipt(frame, columns):
    return {
        "rows": len(frame),
        "sample_ids_sha256": hashlib.sha256(
            "\n".join(frame.sample_id.astype(str)).encode()
        ).hexdigest(),
        "features_float64_le_sha256": hashlib.sha256(
            np.ascontiguousarray(frame[columns].to_numpy(dtype="<f8")).tobytes()
        ).hexdigest(),
        "labels_int64_le_sha256": hashlib.sha256(
            np.ascontiguousarray(frame.y_class.to_numpy(dtype="<i8")).tobytes()
        ).hexdigest(),
        "feature_columns": columns,
        "class_counts": np.bincount(frame.y_class.to_numpy(int), minlength=3).tolist(),
        "reference_start": frame.reference_utc.min(),
        "reference_end": frame.reference_utc.max(),
        "roles": sorted(frame.split.unique().tolist()),
    }


def environment():
    from importlib.metadata import version

    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "worker_thread_limit": 4,
        "packages": {
            name: version(name)
            for name in (
                "numpy",
                "pandas",
                "scikit-learn",
                "matplotlib",
                "joblib",
                "scipy",
                "threadpoolctl",
            )
        },
    }


def products_proof(directory, contract_sha, before, operation, **extra):
    verify_unchanged(before)
    records = [
        {"path": p.name, "sha256": digest(p), "bytes": p.stat().st_size}
        for p in sorted(directory.iterdir())
        if p.is_file() and p.name != "proof.json"
    ]
    value = {
        "schema_version": "available24-artifact-proof-v1",
        "status": "completed",
        "operation": operation,
        "contract_sha256": contract_sha,
        "script_sha256": digest(__file__),
        "products": records,
        "input_hashes_before": before,
        "input_hashes_after": {p: digest(p) for p in before},
        "inputs_unchanged": True,
        "environment": environment(),
        "caller_origin_requires_external_JW_receipt": True,
        **extra,
    }
    write_json(directory / "proof.json", value)
    return value


def train(input_dir, contract_path, review_path, output):
    started, output, input_dir = (
        time.monotonic(),
        Path(output).resolve(),
        Path(input_dir).resolve(),
    )
    if output.exists():
        raise ValueError("Never overwrite training results")
    contract, manifest, before = checked_manifest(
        input_dir,
        "prepared_manifest.json",
        contract_path,
        {
            "daily_physical.csv",
            "daily_common.csv",
            "validation.json",
            "input_hashes_readonly.json",
        },
    )
    data_review, review_hashes = native_review(
        review_path, digest(input_dir / "prepared_manifest.json"), digest(contract_path)
    )
    before.update(review_hashes)
    primary, common = (
        pd.read_csv(input_dir / "daily_physical.csv"),
        pd.read_csv(input_dir / "daily_common.csv"),
    )
    validate_physical(primary, contract)
    validate_common(primary, common, contract)
    methods, math = load_shared()
    groups, common_groups = role_groups(primary), role_groups(common)
    pool = pd.concat((groups["train"], groups["selection"]), ignore_index=True)
    common_pool = pd.concat(
        (common_groups["train"], common_groups["selection"]), ignore_index=True
    )
    output.mkdir(parents=True, exist_ok=False)
    done = threading.Event()

    def deadline():
        if not done.wait(contract.get("training_wall_seconds_max", 300)):
            write_json(
                output / "timeout.json",
                {
                    "status": "failed",
                    "reason": "hard_training_wall_limit",
                    "no_retry": True,
                },
            )
            os._exit(124)

    threading.Thread(target=deadline, daemon=True).start()
    try:
        receipts = {
            "candidate_train": pool_receipt(groups["train"], FEATURES),
            "selection": pool_receipt(groups["selection"], FEATURES),
            "physical_final_refit": pool_receipt(pool, FEATURES),
            "calibration": pool_receipt(groups["calibration"], FEATURES),
            "history_final_fit": pool_receipt(common_pool, HISTORY),
            "history_calibration": pool_receipt(common_groups["calibration"], HISTORY),
            "test_or_live_fit_calls": 0,
        }
        write_json(output / "planned_fit_pool_inputs.json", receipts)

        def locked(record):
            write_json(
                output / "selected_config_lock.json",
                {
                    **record,
                    "contract_sha256": digest(contract_path),
                    "locked_utc": utc_text(datetime.now(timezone.utc)),
                    "candidate_train": receipts["candidate_train"],
                    "selection": receipts["selection"],
                    "test_or_live_accessed_for_choice": False,
                },
            )

        with threadpool_limits(limits=4):
            selected_id, selected, scores, refit = math.fit_selection_then_refit(
                groups["train"][FEATURES].to_numpy(float),
                groups["train"].y_class.to_numpy(int),
                groups["selection"][FEATURES].to_numpy(float),
                groups["selection"].y_class.to_numpy(int),
                candidates=candidates(math),
                on_selected=locked,
            )
            refit.update(
                physical_final_fit=receipts["physical_final_refit"],
                calibration_estimator_fit_calls=0,
                test_estimator_fit_calls=0,
                live_estimator_fit_calls=0,
                selected_configuration_lock_sha256=digest(
                    output / "selected_config_lock.json"
                ),
            )
            write_json(output / "final_refit_receipt.json", refit)
            cal = math.fit_temperature(
                groups["calibration"].y_class.to_numpy(int),
                math.probability_matrix(
                    selected, groups["calibration"][FEATURES].to_numpy(float)
                ),
            )
            history_ready = all(
                len(common_groups[r]) >= contract.get("minimum_rows_per_role", 30)
                for r in ROLES
            ) and set(common_pool.y_class.unique()) == {0, 1, 2}
            history, hcal = (
                None,
                {
                    "status": "unknown_not_fit",
                    "temperature": None,
                    "reason": "Insufficient common role rows or missing training class",
                    "split_counts": {k: len(v) for k, v in common_groups.items()},
                },
            )
            if history_ready:
                history = make_pipeline(
                    StandardScaler(),
                    LogisticRegression(C=1, max_iter=2000, random_state=SEED),
                )
                history.fit(
                    common_pool[HISTORY].to_numpy(float),
                    common_pool.y_class.to_numpy(int),
                )
                hcal = math.fit_temperature(
                    common_groups["calibration"].y_class.to_numpy(int),
                    math.probability_matrix(
                        history, common_groups["calibration"][HISTORY].to_numpy(float)
                    ),
                )
            class_q = np.bincount(pool.y_class.to_numpy(int), minlength=3) / len(pool)
            references = {
                TARGETS[0]: float(class_q[1:].sum()),
                TARGETS[1]: float(class_q[2]),
            }
            pred = physical_predictions(math, selected, cal, class_q, groups["test"])
            common_pred = {}
            if len(common_groups["test"]):
                common_pred = physical_predictions(
                    math, selected, cal, class_q, common_groups["test"]
                )
                if history_ready:
                    common_pred["history_only"] = math.target_probabilities(
                        math.apply_temperature(
                            math.probability_matrix(
                                history, common_groups["test"][HISTORY].to_numpy(float)
                            ),
                            hcal["temperature"],
                        )
                    )
                common_pred["past24_persistence"] = {
                    TARGETS[0]: common_groups["test"]
                    .history_24h_c_count.gt(0)
                    .to_numpy(float),
                    TARGETS[1]: common_groups["test"]
                    .history_24h_m_count.gt(0)
                    .to_numpy(float),
                }
            metrics, yearly, bins, outcomes = evaluate(
                math, groups["test"], pred, references, "primary"
            )
            common_metrics, common_yearly, common_bins, common_outcomes = evaluate(
                math, common_groups["test"], common_pred, references, "common"
            )
            importance = methods.permutation_scores(
                math,
                selected,
                groups["test"][FEATURES].to_numpy(float),
                groups["test"].y_class.to_numpy(int),
                cal["temperature"],
                repeats=10,
            )
            primary_ci = math.block_bootstrap(
                groups["test"].reference_utc, outcomes, pred, references, draws=200
            )
            primary_ci["cohort"] = "primary"
            common_ci = (
                math.block_bootstrap(
                    common_groups["test"].reference_utc,
                    common_outcomes,
                    common_pred,
                    references,
                    draws=200,
                )
                if common_pred
                else pd.DataFrame(columns=primary_ci.columns)
            )
            common_ci["cohort"] = "common"
        for name, table in (
            ("model_selection.csv", pd.DataFrame(scores)),
            ("metrics_primary.csv", metrics),
            ("metrics_common.csv", common_metrics),
            ("year_metrics.csv", pd.concat((yearly, common_yearly), ignore_index=True)),
            ("importance.csv", importance),
            ("bootstrap_ci.csv", pd.concat((primary_ci, common_ci), ignore_index=True)),
        ):
            table.to_csv(output / name, index=False)
        heldout = groups["test"].copy()
        heldout["common_date_qualified"] = heldout.sample_id.isin(
            common_groups["test"].sample_id
        )
        for name, probs in pred.items():
            heldout[f"p_C_{name}"], heldout[f"p_M_{name}"] = (
                probs[TARGETS[0]],
                probs[TARGETS[1]],
            )
        ct = common_groups["test"].set_index("sample_id")
        for name in ("history_only", "past24_persistence"):
            for prefix, target in zip(("C", "M"), TARGETS):
                heldout[f"p_{prefix}_{name}"] = (
                    heldout.sample_id.map(
                        pd.Series(common_pred[name][target], index=ct.index)
                    )
                    if name in common_pred
                    else np.nan
                )
        heldout.to_csv(output / "heldout_predictions.csv", index=False)
        write_json(
            output / "calibration.json",
            {
                "selected": cal,
                "history_only": hcal,
                "reliability_bins": bins + common_bins,
                "fit_role": "calibration_2022_only",
                "test_used_to_fit": False,
                "baseline_climatology_class_probabilities": class_q.tolist(),
                "history_fit_cohort": "common_only",
                "physical_fit_cohort": "primary_all_qualified",
            },
        )
        joblib.dump(
            {
                "selected_model": selected,
                "history_only_model": history,
                "selected_model_id": selected_id,
                "feature_columns": FEATURES,
                "baseline_feature_columns": HISTORY,
                "selected_temperature": cal["temperature"],
                "history_temperature": hcal["temperature"],
                "class_probabilities_train": class_q,
                "fit_ranges": {
                    name: [float(pool[name].min()), float(pool[name].max())]
                    for name in FEATURES
                },
                "contract_sha256": digest(contract_path),
                "script_sha256": digest(__file__),
                "target_offset_hours": [0, 24],
            },
            output / "models.joblib",
        )
        verify_unchanged(before)
        validation = {
            "status": "passed",
            "input_hashes_unchanged": True,
            "no_live_input_or_issue": True,
            "test_used_to_fit_or_select": False,
            "2026_rows_used": 0,
            "target_offset_hours": [0, 24],
            "primary_role_boundary_isolation": boundary_audit(primary),
            "common_role_boundary_isolation": boundary_audit(common, allow_empty=True),
            "main_feature_count": 23,
            "main_uses_history_features": False,
            "importance_used_for_model_or_feature_selection": False,
            "native_Data_decision": data_review["decision"],
            "prepared_manifest_sha256": digest(input_dir / "prepared_manifest.json"),
            "contract_sha256": digest(contract_path),
            "history_baseline_fit_status": hcal["status"],
        }
        write_json(output / "validation.json", validation)
        summary = {
            "status": "completed_training_only",
            "project_id": contract.get("project_id"),
            "selected_model_id": selected_id,
            "main_feature_count": 23,
            "baseline_feature_count": 12,
            "primary_split_counts": {k: len(v) for k, v in groups.items()},
            "common_split_counts": {k: len(v) for k, v in common_groups.items()},
            "primary_split_class_counts": {
                k: np.bincount(v.y_class.to_numpy(int), minlength=3).tolist()
                for k, v in groups.items()
            },
            "common_split_class_counts": {
                k: np.bincount(v.y_class.to_numpy(int), minlength=3).tolist()
                for k, v in common_groups.items()
            },
            "first_qualified_training_reference": groups["train"].reference_utc.min(),
            "final_fit_rows": len(pool),
            "development_to_primary_test_ratio": (
                len(pool) + len(groups["calibration"])
            )
            / len(groups["test"]),
            "historical_test_metrics": metrics.to_dict("records"),
            "common_test_metrics": common_metrics.to_dict("records"),
            "calibration": {"selected": cal, "history_only": hcal},
            "issued_forecast": None,
            "future_accuracy": None,
            "scientific_status": "not_reviewed",
            "formal_ResearchReviewStore_chain": False,
            "primary_all_qualified_not_history_gated": True,
            "history_comparison_scope": "Same common heldout dates; physical train pool is broader than history pool, not an architecture-controlled ablation",
            "prior_test_seen_in_other_target_task": True,
            "limitations": contract_limits(contract),
            "wall_seconds": time.monotonic() - started,
        }
        write_json(output / "summary.json", summary)
        products_proof(
            output,
            digest(contract_path),
            before,
            "train_evaluate_only",
            no_fit_on_test_or_live=True,
            no_live_issue=True,
        )
        return {
            "status": "completed_training_only",
            "selected_model_id": selected_id,
            "proof_sha256": digest(output / "proof.json"),
        }
    finally:
        done.set()


def checked_forecast(directory, contract_path):
    directory = Path(directory).resolve()
    proof_path = directory / "proof.json"
    proof = read_json(proof_path)
    if (
        proof.get("operation") != "train_evaluate_only"
        or proof.get("status") != "completed"
        or proof.get("contract_sha256") != digest(contract_path)
        or proof.get("script_sha256") != digest(__file__)
        or proof.get("no_live_issue") is not True
    ):
        raise ValueError(
            "Only this contract's completed offline training can be consumed"
        )
    before = {
        str(proof_path): digest(proof_path),
        str(Path(contract_path).resolve()): digest(contract_path),
        str(Path(__file__)): digest(__file__),
    }
    paths = [r.get("path") for r in proof.get("products", [])]
    if len(paths) != len(set(paths)) or not {
        "models.joblib",
        "summary.json",
        "validation.json",
        "selected_config_lock.json",
        "calibration.json",
        "heldout_predictions.csv",
        "metrics_primary.csv",
        "metrics_common.csv",
        "importance.csv",
    }.issubset(paths):
        raise ValueError("Complete frozen training products required")
    for record in proof["products"]:
        verify_row(directory, record, before)
    validation = read_json(directory / "validation.json")
    if (
        validation.get("status") != "passed"
        or validation.get("no_live_input_or_issue") is not True
        or validation.get("test_used_to_fit_or_select") is not False
        or validation.get("2026_rows_used") != 0
    ):
        raise ValueError("Training qualification or temporal isolation failed")
    before.update(proof["input_hashes_before"])
    verify_unchanged(before)
    return proof, before


def validate_live_at_issue(live, now):
    row, stamp = live.iloc[0], utc(now)
    support, produced, retrieved = [
        utc(row[name])
        for name in (
            "hmi_latest_support_end_utc",
            "hmi_latest_processing_utc",
            "source_retrieved_utc",
        )
    ]
    if not max(support, produced) <= retrieved <= stamp:
        raise ValueError(
            "Live support/processing must precede retrieval and actual issue"
        )
    observation_field = (
        "oldest_patch_observation_utc"
        if "oldest_patch_observation_utc" in live
        else "observation_utc"
    )
    observed = utc(row[observation_field])
    if observed > support:
        raise ValueError("Observation centre cannot be later than support end")
    age = (stamp - observed).total_seconds()
    if not 0 <= age <= 10800:
        raise ValueError(
            "Latest eligible physical observation exceeds registered 3h age"
        )
    if "feature_max_observed_utc" in live and utc(row.feature_max_observed_utc) > stamp:
        raise ValueError("Live feature support is later than actual issuance")
    return stamp, age


def checked_live(directory, contract_path):
    """Consume controlled acquisition products without recalculating features."""
    directory, contract_path = Path(directory).resolve(), Path(contract_path).resolve()
    contract = validate_contract(read_json(contract_path))
    manifest_path = directory / "source_manifest.json"
    manifest = read_json(manifest_path)
    if (
        manifest.get("schema_version") != "available24-live-source-manifest-v1"
        or manifest.get("status") != "eligible"
        or manifest.get("contract_sha256") != digest(contract_path)
    ):
        raise ValueError("Current controlled source manifest is not eligible/bound")
    if (
        manifest.get("future_outcome_read") is not False
        or manifest.get("fit_predict_plot_performed") is not False
        or manifest.get("forecast_issued") is not False
    ):
        raise ValueError("Source stage must not read future labels, fit or issue")
    before = {
        str(contract_path): digest(contract_path),
        str(manifest_path): digest(manifest_path),
        str(Path(__file__)): digest(__file__),
    }
    records = manifest.get("files", [])
    names = [r.get("path") for r in records]
    if len(names) != len(set(names)) or not {
        "live_physical.csv",
        "live_qualification.json",
        "source_requests.json",
        "row_audit.json",
        "source_schema_validation.json",
    }.issubset(names):
        raise ValueError(
            "Complete current source receipt and qualified feature file required"
        )
    for record in records:
        verify_row(directory, record, before)
    before.update(manifest["readonly_input_hashes"])
    qualification = read_json(directory / "live_qualification.json")
    if (
        qualification.get("status") != "eligible"
        or qualification.get("contract_sha256") != digest(contract_path)
        or qualification.get("partial_patch_aggregation") is not False
        or qualification.get("no_history_features_created") is not True
        or qualification.get("future_outcome_read") is not False
    ):
        raise ValueError("Current full-group source qualification failed")
    frame = pd.read_csv(directory / "live_physical.csv")
    if (
        len(frame) != 1
        or frame.iloc[0].sample_id != qualification.get("sample_id")
        or digest(directory / "live_physical.csv")
        != qualification.get("live_physical_csv_sha256")
        or int(frame.iloc[0].patch_count) != qualification.get("central_patch_count")
    ):
        raise ValueError("Current feature group and original qualification differ")
    histogram = qualification.get("quality_raw_histograms", {}).get("QUALITY", {})
    if (
        normalized_quality_histogram(histogram)
        != {1024: int(frame.iloc[0].patch_count)}
        or frame.iloc[0].QUALITY_exact_full_word != 1024
        or not frame.source_series.eq(contract["source_mode"]).all()
    ):
        raise ValueError("Current primary quality word or source series differs")
    # Only repeat already verified interface flags; original CSV remains unchanged.
    frame = frame.assign(
        reference_utc=frame.source_retrieved_utc,
        magnetic_qualified=True,
        source_mode=frame.source_series,
        all_magnetic_quality_allowed=True,
        partial_patch_coverage=False,
    )
    verify_unchanged(before)
    return contract, manifest, before, frame, manifest_path


def issue(
    model_dir,
    live_dir,
    contract_path,
    data_review_path,
    output,
    evidence_review_path=None,
):
    """Predict once from frozen model, then capture actual issue and +24h target."""
    output, model_dir, live_dir = (
        Path(output).resolve(),
        Path(model_dir).resolve(),
        Path(live_dir).resolve(),
    )
    if output.exists():
        raise ValueError("Never overwrite or repeat an issue output")
    contract, live_manifest, before, live, live_manifest_path = checked_live(
        live_dir, contract_path
    )
    live_review, hashes = native_review(
        data_review_path, digest(live_manifest_path), digest(contract_path)
    )
    before.update(hashes)
    training_proof, model_hashes = checked_forecast(model_dir, contract_path)
    before.update(model_hashes)
    evidence_path = (
        Path(evidence_review_path).resolve()
        if evidence_review_path is not None
        else model_dir / "native_evidence_review_original.json"
    )
    evidence, hashes = native_review(
        evidence_path,
        digest(model_dir / "proof.json"),
        digest(contract_path),
        "evidence",
    )
    before.update(hashes)
    validate_physical(live, contract, live=True)
    validate_live_at_issue(live, datetime.now(timezone.utc))
    if live_manifest.get("future_outcome_read") is not False:
        raise ValueError(
            "Current input contract must explicitly forbid target-outcome access"
        )
    # A campaign-scoped exclusive claim is made before model deserialization.
    claim_path = Path(contract_path).resolve().parent / "issue_claim.json"
    claim = {
        "status": "attempt_reserved",
        "contract_sha256": digest(contract_path),
        "live_manifest_sha256": digest(live_manifest_path),
        "model_proof_sha256": digest(model_dir / "proof.json"),
        "output_directory": str(output),
        "reserved_utc": utc_text(datetime.now(timezone.utc)),
        "no_retry": True,
    }
    with claim_path.open("x", encoding="utf-8") as stream:
        json.dump(claim, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    output.mkdir(parents=True, exist_ok=False)
    try:
        model_bundle = joblib.load(model_dir / "models.joblib")
        if (
            model_bundle.get("contract_sha256") != digest(contract_path)
            or model_bundle.get("script_sha256") != digest(__file__)
            or model_bundle.get("feature_columns") != FEATURES
            or model_bundle.get("target_offset_hours") != [0, 24]
        ):
            raise ValueError("Frozen model and registered feature/time contract differ")
        _, math = load_shared()
        with threadpool_limits(limits=4):
            probs = math.target_probabilities(
                math.apply_temperature(
                    math.probability_matrix(
                        model_bundle["selected_model"], live[FEATURES].to_numpy(float)
                    ),
                    model_bundle["selected_temperature"],
                )
            )
        # Capture the real stamp after prediction. Never accept a user-supplied issue time.
        stamp, age = validate_live_at_issue(live, datetime.now(timezone.utc))
        verify_unchanged(before)
        p_c, p_m = float(probs[TARGETS[0]][0]), float(probs[TARGETS[1]][0])
        if not 0 <= p_m <= p_c <= 1:
            raise ValueError("Issued C/M probabilities must be naturally nested")
        ranges = [
            {
                "feature": name,
                "live_value": float(live.iloc[0][name]),
                "fit_min": bounds[0],
                "fit_max": bounds[1],
                "outside_fit_range": bool(
                    live.iloc[0][name] < bounds[0] or live.iloc[0][name] > bounds[1]
                ),
            }
            for name, bounds in model_bundle["fit_ranges"].items()
        ]
        end = stamp + pd.Timedelta(hours=24)
        assessment = evidence.get("assessment", evidence)
        skill = {
            r.get("target"): r.get("skill_supported")
            for r in assessment.get(
                "target_assessments", evidence.get("target_assessments", [])
            )
        }
        value = {
            "schema_version": "available24-live-issue-v1",
            "project_id": contract.get("project_id"),
            "status": "issued_experimental_pending_observation",
            "issued_utc": utc_text(stamp),
            "prediction_window": {"start": utc_text(stamp), "end": utc_text(end)},
            "local_prediction_window": {
                "start": stamp.tz_convert("Asia/Shanghai").isoformat(),
                "end": end.tz_convert("Asia/Shanghai").isoformat(),
            },
            "target_offset_hours": [0, 24],
            "target_anchor": "actual_issue_after_prediction",
            "input_nominal_tai": str(live.iloc[0].get("hmi_nominal_tai", "")),
            "input_observation_utc": str(live.iloc[0].get("observation_utc", "")),
            "input_oldest_patch_observation_utc": str(
                live.iloc[0].get(
                    "oldest_patch_observation_utc",
                    live.iloc[0].get("observation_utc", ""),
                )
            ),
            "input_support_end_utc": str(live.iloc[0].hmi_latest_support_end_utc),
            "input_processing_utc": str(live.iloc[0].hmi_latest_processing_utc),
            "input_retrieved_utc": str(live.iloc[0].source_retrieved_utc),
            "input_age_seconds": age,
            "main_feature_count": 23,
            "main_uses_history_features": False,
            "selected_model_id": model_bundle["selected_model_id"],
            "accepted_QUALITY_words": [1024],
            "source_mode": contract["source_mode"],
            "target_spatial_scope": "whole_sun_GOES_new_start_no_AR_filter",
            "targets": [
                {
                    "threshold": target,
                    "probability": p,
                    "decision_threshold": 0.5,
                    "predict_occurrence": p >= 0.5,
                    "observed_outcome": None,
                    "future_accuracy": None,
                    "skill_supported": skill.get(target, False),
                }
                for target, p in zip(TARGETS, (p_c, p_m))
            ],
            "future_truth_read": False,
            "live_history_baseline_status": "unknown_not_issued",
            "baseline_probabilities": {},
            "range_audit": {
                "records": ranges,
                "outside_count": sum(r["outside_fit_range"] for r in ranges),
                "scope": "Marginal range description, not individual confidence",
            },
            "native_live_Data_decision": live_review["decision"],
            "native_prior_Evidence_decision": evidence["decision"],
            "formal_ResearchReviewStore_chain": False,
            "scientific_status": "not_reviewed",
            "operational_forecast_validated": False,
            "contract_sha256": digest(contract_path),
            "training_proof_sha256": digest(model_dir / "proof.json"),
            "live_manifest_sha256": digest(live_manifest_path),
            "limitations": contract_limits(contract),
            "actual_JW_origin_requires_external_receipt": True,
        }
        write_json(output / "issued_forecast.json", value)
        write_json(
            output / "validation.json",
            {
                "status": "passed",
                "actual_issue_captured_after_predict": True,
                "prediction_duration_seconds": 86400,
                "all_input_availability_not_after_issue": True,
                "age_seconds": age,
                "future_truth_read": False,
                "refit_calls": 0,
                "inputs_unchanged": True,
            },
        )
        products_proof(
            output,
            digest(contract_path),
            before,
            "single_live_issue",
            model_proof_sha256=digest(model_dir / "proof.json"),
            live_manifest_sha256=digest(live_manifest_path),
            no_fit_or_recalibration=True,
        )
        write_json(
            claim_path.with_name("issue_completion.json"),
            {
                **claim,
                "status": "completed",
                "issued_utc": value["issued_utc"],
                "proof_sha256": digest(output / "proof.json"),
            },
        )
        return {
            "status": value["status"],
            "issued_utc": value["issued_utc"],
            "prediction_window": value["prediction_window"],
            "proof_sha256": digest(output / "proof.json"),
        }
    except BaseException as exc:
        write_json(
            output / "failure.json",
            {
                "status": "failed",
                "error_class": type(exc).__name__,
                "no_retry": True,
                "issue_claim_retained": True,
            },
        )
        raise


def plot(forecast_dir, output, issued_dir=None):
    """Only frozen tables are rendered; no estimator deserialization or prediction."""
    forecast_dir, output = Path(forecast_dir).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Never overwrite an original scientific figure")
    proof_path = forecast_dir / "proof.json"
    proof = read_json(proof_path)
    if (
        proof.get("operation") != "train_evaluate_only"
        or proof.get("script_sha256") != digest(__file__)
        or proof.get("no_live_issue") is not True
    ):
        raise ValueError("Only frozen offline training result tables may be plotted")
    before = {
        str(proof_path): digest(proof_path),
        str(Path(__file__)): digest(__file__),
    }
    for row in proof["products"]:
        verify_row(forecast_dir, row, before)
    validation = read_json(forecast_dir / "validation.json")
    if validation.get("status") != "passed" or validation.get("2026_rows_used") != 0:
        raise ValueError("Only completed 2023-2025 evaluation can be rendered")
    metrics = pd.read_csv(forecast_dir / "metrics_common.csv")
    plot_cohort = "common"
    if metrics.empty:
        metrics = pd.read_csv(forecast_dir / "metrics_primary.csv")
        plot_cohort = "primary"
    importance = pd.read_csv(forecast_dir / "importance.csv")
    heldout = pd.read_csv(forecast_dir / "heldout_predictions.csv")
    calibration = read_json(forecast_dir / "calibration.json")
    if (
        set(importance.feature) != set(FEATURES)
        or not importance.role.eq(IMPORTANCE_ROLE).all()
    ):
        raise ValueError("Frozen descriptive 23-feature importance required")
    refs = times(heldout, "reference_utc")
    if (
        not refs.dt.year.isin((2023, 2024, 2025)).all()
        or not times(heldout, "target_start_utc").eq(refs).all()
        or not times(heldout, "target_end_utc").eq(refs + pd.Timedelta(hours=24)).all()
    ):
        raise ValueError("Heldout plot times are not each sample's own future 24h")
    issued = None
    if issued_dir is not None:
        issued_dir = Path(issued_dir).resolve()
        ip = issued_dir / "proof.json"
        issue_proof = read_json(ip)
        if (
            issue_proof.get("operation") != "single_live_issue"
            or issue_proof.get("contract_sha256") != proof["contract_sha256"]
            or issue_proof.get("model_proof_sha256") != digest(proof_path)
        ):
            raise ValueError("Issued artifact belongs to a different training result")
        before[str(ip)] = digest(ip)
        for row in issue_proof["products"]:
            verify_row(issued_dir, row, before)
        issued = read_json(issued_dir / "issued_forecast.json")
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "font.sans-serif": ["Microsoft YaHei", "SimHei", "DejaVu Sans"],
            "axes.unicode_minus": False,
        }
    )
    output.mkdir(parents=True, exist_ok=False)
    figures = []

    def save(fig, filename, question, limitation):
        for suffix in ("png", "svg"):
            path = output / (filename + "." + suffix)
            fig.savefig(path, dpi=180, bbox_inches="tight")
            figures.append(
                {
                    "path": path.name,
                    "bytes": path.stat().st_size,
                    "sha256": digest(path),
                    "question": question,
                    "limitation": limitation,
                }
            )
        plt.close(fig)

    ordered = importance.sort_values("mean_Brier_increase")
    fig, ax = plt.subplots(figsize=(10, 9), constrained_layout=True)
    ax.barh(
        ordered.feature,
        ordered.mean_Brier_increase,
        xerr=ordered.mean_Brier_increase_std,
        color="#00798c",
        capsize=3,
    )
    ax.axvline(0, color="black", lw=0.8)
    ax.set(
        title="磁场模型依赖哪些SHARP标量？23列全部保留",
        xlabel="打乱后C/M平均Brier增加；10次误差条为SD\n持出期描述，不用于选择特征或证明因果",
    )
    save(
        fig,
        "02_available24_feature_importance",
        "模型依赖哪些物理摘要？",
        "相关特征可替代；负值保留；测试复用描述不是独立机制验证。",
    )
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
    registry = (
        ("selected_calibrated", "磁场", "#d1495b"),
        ("train_climatology", "训练率", "#777777"),
        ("history_only", "历史", "#00798c"),
        ("past24_persistence", "持续性", "#e4ad38"),
        ("always_yes", "全有", "#7d4e9e"),
        ("always_no", "全无", "#4f9d69"),
    )
    available = [r for r in registry if r[0] in set(metrics.model_id)]
    names, labels, colors = zip(*available)
    for col, target in enumerate(TARGETS):
        top, bottom = axes[0, col], axes[1, col]
        top.plot((0, 1), (0, 1), "--", color="gray")
        for name, color in zip(names, colors):
            points = [
                r
                for r in calibration["reliability_bins"]
                if r["cohort"] == plot_cohort
                and r["model_id"] == name
                and r["target"] == target
                and r["n"] > 0
            ]
            top.plot(
                [r["mean_predicted_probability"] for r in points],
                [r["observed_fraction"] for r in points],
                "o-",
                label=name,
                color=color,
            )
        top.set(
            xlim=(0, 1),
            ylim=(0, 1),
            title=target
            + (
                " 共同日期可靠性"
                if plot_cohort == "common"
                else " 主队列可靠性（历史未知）"
            ),
            xlabel="模型概率",
            ylabel="观测发生比例",
        )
        top.legend(fontsize=6)
        table = metrics[metrics.target.eq(target)].set_index("model_id")
        bars = bottom.bar(
            range(len(names)),
            [float(table.loc[name, "Brier"]) for name in names],
            color=colors,
        )
        bottom.bar_label(bars, fmt="%.4f", fontsize=7)
        bottom.set_xticks(range(len(names)), labels)
        bottom.set(
            ylabel="Brier（低更好）",
            title="同一共同持出日期比较"
            if plot_cohort == "common"
            else "主队列比较；共同历史基线未知",
        )
    fig.suptitle("2023—2025已结束窗口：各样本从模拟签发起未来24h；非单次未来正确率")
    save(
        fig,
        "03_available24_reliability_baselines",
        "概率质量是否优于平凡及历史基线？",
        "共同测试日期相同；RF与历史模型的拟合池不同，不是控制架构的物理消融。",
    )
    months = sorted(refs.dt.strftime("%Y-%m").unique())
    fig, axes = plt.subplots(
        1, 2, figsize=(12, max(6, len(months) * 0.25)), constrained_layout=True
    )
    cmap = plt.get_cmap("YlOrRd").copy()
    cmap.set_bad("#e7e7e7")
    for ax, target, probability, label in zip(
        axes,
        TARGETS,
        ("p_C_selected_calibrated", "p_M_selected_calibrated"),
        ("label_c1", "label_m1"),
    ):
        matrix, markers = np.full((len(months), 31), np.nan), []
        occupied = set()
        for stamp, value, truth in zip(refs, heldout[probability], heldout[label]):
            location = (months.index(stamp.strftime("%Y-%m")), stamp.day - 1)
            if location in occupied:
                raise ValueError(
                    "Multiple target windows on a UTC day cannot be merged in a daily heatmap"
                )
            occupied.add(location)
            matrix[location] = value
            if truth:
                markers.append((location[1], location[0]))
        im = ax.imshow(matrix, vmin=0, vmax=1, cmap=cmap, aspect="auto")
        if markers:
            x, y = zip(*markers)
            ax.scatter(x, y, c="black", s=5)
        ax.set_xticks((0, 4, 9, 14, 19, 24, 30), (1, 5, 10, 15, 20, 25, 31))
        ax.set_yticks(range(len(months)), months, fontsize=7)
        ax.set(title=target, xlabel="模拟签发UTC日期；该时刻起24h", ylabel="月份")
    fig.suptitle("磁场模型持出概率；黑点=已观测正例，灰格=未知资格或不存在的日期")
    fig.colorbar(im, ax=axes, label="模型概率")
    save(
        fig,
        "04_available24_heldout_probability_heatmap",
        "持出期每组样本对应未来24h的概率怎样？",
        "条件资格队列，灰格不是负例；中央磁场输入与全太阳事件空间不同。",
    )
    if issued is not None:
        values = np.asarray([[r["probability"] for r in issued["targets"]]])
        fig, ax = plt.subplots(figsize=(10, 5), constrained_layout=True)
        im = ax.imshow(values, vmin=0, vmax=1, cmap="YlOrRd", aspect="auto")
        ax.set_xticks((0, 1), ("C1.0+（含M/X）", "M1.0+（含X）"))
        ax.set_yticks((0,), ("签发后24h",))
        for col, value in enumerate(values[0]):
            ax.text(
                col,
                0,
                f"{value:.1%}",
                ha="center",
                va="center",
                fontsize=30,
                color="white" if value > 0.65 else "black",
            )
        ax.set_title("JW磁场实验概率；全太阳新开始耀斑，真值尚未知")
        window = issued["local_prediction_window"]
        ax.set_xlabel(
            f"北京时间：{window['start']} → {window['end']}\n实际签发：{issued['issued_utc']}；输入支持结束：{issued['input_support_end_utc']}",
            fontsize=8,
        )
        fig.colorbar(im, ax=ax, label="模型概率，非正确率")
        save(
            fig,
            "01_available24_issued_probability",
            "实际签发后24h会有C/M级新起始事件吗？",
            "实验概率不是命中率；技能/质量/来源与部署限制保留。",
        )
    verify_unchanged(before)
    write_json(
        output / "figure_index.json",
        {
            "schema_version": "available24-original-figure-index-v1",
            "products": figures,
            "training_proof_sha256": digest(proof_path),
            "contract_sha256": proof["contract_sha256"],
            "script_sha256": digest(__file__),
            "parameters": {
                "dpi": 180,
                "font": plt.rcParams["font.sans-serif"],
                "importance_repeats": 10,
                "interval_seconds": 86400,
            },
            "environment": environment(),
            "no_fit_predict_or_model_deserialization": True,
            "actual_native_JW_call_requires_external_receipt": True,
        },
    )
    products_proof(
        output,
        proof["contract_sha256"],
        before,
        "plot_frozen_only",
        no_fit_predict_or_model_deserialization=True,
    )
    return {
        "status": "completed_plot_only",
        "figure_index_sha256": digest(output / "figure_index.json"),
        "proof_sha256": digest(output / "proof.json"),
    }


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("train")
    p.add_argument("--input-dir", type=Path, required=True)
    p.add_argument("--contract", type=Path, required=True)
    p.add_argument("--data-review", type=Path, required=True)
    p = sub.add_parser("issue")
    p.add_argument("--model-dir", type=Path, required=True)
    p.add_argument("--live-dir", type=Path, required=True)
    p.add_argument("--contract", type=Path, required=True)
    p.add_argument("--data-review", type=Path, required=True)
    p.add_argument("--evidence-review", type=Path)
    p = sub.add_parser("plot")
    p.add_argument("--forecast-dir", type=Path, required=True)
    p.add_argument("--issued-dir", type=Path)
    for p in sub.choices.values():
        p.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "train":
        result = train(args.input_dir, args.contract, args.data_review, args.output_dir)
    elif args.command == "issue":
        result = issue(
            args.model_dir,
            args.live_dir,
            args.contract,
            args.data_review,
            args.output_dir,
            args.evidence_review,
        )
    else:
        result = plot(args.forecast_dir, args.output_dir, args.issued_dir)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
