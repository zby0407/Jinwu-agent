"""Physical SHARP forecast engineering; real production is an external JW call.

The historical/runtime source profile and exact contract must be registered
before CONTRACT_SHA is frozen. Missing magnetism never falls back to history.
Import and input qualification do not train, predict, or plot a model.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

for _key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_key] = "4"

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

CONTRACT_SHA = None  # Production stays forbidden until root registers the exact contract.
MATH_SHA = "1af883b3cdba0717d827a1c8768d80f9720103f57795f6825f56b142e89090d1"
CORE_SHA = "0582ae6f2085e18029b640c22a95b7be634c4e593560283c004b91ebec8c93d3"
SEED = 20261011
TARGETS = ("C1.0+", "M1.0+")
PHYSICAL = ("USFLUX", "TOTUSJH", "TOTUSJZ", "ABSNJZH", "SAVNCPP", "MEANALP",
            "R_VALUE", "TOTPOT", "MEANSHR", "AREA_ACR", "MEANPOT")
FEATURES = [f"{name}_{agg}" for name in PHYSICAL for agg in ("max", "median")] + ["patch_count"]
HISTORY = [f"history_{h}h_{a}" for h in (24, 72, 168)
           for a in ("c_count", "m_count", "x_count", "max_peak_w_m2")]
ROLES = ("train", "selection", "calibration", "test")
IMPORTANCE_ROLE = "heldout_test_descriptive_permutation_no_feature_selection"
BOUNDARY_PURGE_DATES = ["2020-12-31", "2021-12-31", "2022-12-31"]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def parse_utc(value):
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError("Explicit nonempty UTC time required")
    return stamp.tz_convert("UTC")


def utc_series(frame, column, nullable=False):
    values = []
    for value in frame[column]:
        if pd.isna(value) and nullable:
            values.append(pd.NaT)
        else:
            values.append(parse_utc(value))
    return pd.Series(values, index=frame.index, dtype="datetime64[ns, UTC]")


def load_math():
    path = Path(__file__).with_name("forecast_math_reference.py")
    if digest(path) != MATH_SHA or digest(path.with_name("frozen_math_core.py")) != CORE_SHA:
        raise ValueError("Frozen mathematics dependencies differ")
    spec = importlib.util.spec_from_file_location("physical_frozen_math", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def candidate_models():
    return [(f"physical_RF_leaf{leaf}_depth{depth}", RandomForestClassifier(
        n_estimators=200, min_samples_leaf=leaf, max_depth=depth,
        n_jobs=4, random_state=SEED)) for leaf in (5, 15, 30) for depth in (4, 8, None)]


def validate_contract(contract):
    if contract.get("schema_version") != "jw-physical-forecast-contract-v1":
        raise ValueError("Unregistered physical contract schema")
    if contract.get("feature_columns") != FEATURES or contract.get("baseline_feature_columns") != HISTORY:
        raise ValueError("Physical/history feature interface differs")
    if contract.get("reference_hour_utc") != 16 or contract.get("target_offset_hours") != [0, 24]:
        raise ValueError("Historical/live reference-to-target interface differs")
    if contract.get("nominal_tai_hour") != 13 or contract.get("quality_equals") != 0:
        raise ValueError("Registered magnetic anchor or strict quality differs")
    if contract.get("source_mode") not in ("hmi.sharp_cea_720s_nrt", "hmi.sharp_cea_720s"):
        raise ValueError("Unknown magnetic source mode")
    if contract.get("same_source_mode_historical_and_live") is not True:
        raise ValueError("Product transfer without a registered tested bridge is forbidden")
    if set(contract.get("splits", {})) != set(ROLES):
        raise ValueError("Missing chronological role")
    previous = None
    year_ranges = {"train": (2011, 2020), "selection": (2021, 2021),
                   "calibration": (2022, 2022), "test": (2023, 2026)}
    for role in ROLES:
        a, b = [pd.Timestamp(x, tz="UTC") for x in contract["splits"][role]]
        lo, hi = year_ranges[role]
        if a > b or not lo <= a.year <= hi or not lo <= b.year <= hi or (previous is not None and a <= previous):
            raise ValueError("Chronological split policy changed")
        previous = b
    if contract.get("selection_metric") != "mean_binary_Brier_C_M" or contract.get("seed") != SEED:
        raise ValueError("Frozen selection policy differs")
    if contract.get("final_refit_roles") != ["train", "selection"] or contract.get("importance_role") != IMPORTANCE_ROLE:
        raise ValueError("Refit/importance roles differ")
    if contract.get("metrics_omit") != ["MAE"] or contract.get("history_completion_lag_hours") != 3:
        raise ValueError("Metric/history availability policy differs")
    if contract.get("boundary_purge_reference_dates") != BOUNDARY_PURGE_DATES:
        raise ValueError("Registered year-boundary purge dates differ")
    for key in ("source_plan_sha256", "source_profile_sha256"):
        value = contract.get(key)
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("Source contract hash is absent")
    start, end = [parse_utc(contract["prediction_window"][k]) for k in ("start", "end")]
    if start != parse_utc(contract["data_cutoff"]) or end-start != pd.Timedelta(hours=24) or start.hour != 16:
        raise ValueError("Live window differs from physical reference")


def validated_future_issue(contract, now):
    now = parse_utc(now)
    start, end = [parse_utc(contract["prediction_window"][k]) for k in ("start", "end")]
    if not now < start < end or end-start != pd.Timedelta(hours=24):
        raise ValueError("Actual issuance must precede the entire registered target")
    deadline = parse_utc(contract["issue_deadline_utc"])
    if deadline >= start or now > deadline:
        raise ValueError("Registered prospective issuance deadline passed or invalid")
    return now


def validate_history(frame, references):
    values = frame[HISTORY].to_numpy(float)
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError("History features must be finite nonnegative")
    for h in (24, 72, 168):
        c, m, x = [frame[f"history_{h}h_{k}_count"].to_numpy(float) for k in ("c", "m", "x")]
        if not ((c >= m) & (m >= x)).all() or not all(np.equal(v, np.floor(v)).all() for v in (c, m, x)):
            raise ValueError("History counts must be nested integers")
    for name in ("c_count", "m_count", "x_count", "max_peak_w_m2"):
        if not (frame[f"history_24h_{name}"] <= frame[f"history_72h_{name}"]).all() or not (frame[f"history_72h_{name}"] <= frame[f"history_168h_{name}"]).all():
            raise ValueError("History windows must be nested")
    ends = utc_series(frame, "history_latest_event_end_utc", nullable=True)
    empty = frame[HISTORY].eq(0).all(axis=1)
    if (ends.isna() & ~empty).any() or not ((ends <= references-pd.Timedelta(hours=3)) | ends.isna()).all():
        raise ValueError("History is not completed by the new16UTC-minus3h cutoff")


def role_boundary_audit(daily):
    """Previous labels must finish before the next role's earliest input cutoff."""
    checks = []
    for previous, following in zip(ROLES[:-1], ROLES[1:]):
        a, b = daily[daily.split.eq(previous)], daily[daily.split.eq(following)]
        if a.empty or b.empty:
            raise ValueError("Role-boundary check requires every chronological group")
        label_end = utc_series(a, "target_end_utc").max()
        first_reference = utc_series(b, "reference_utc").min()
        availability_cutoff = first_reference-pd.Timedelta(hours=3)
        passed = label_end <= availability_cutoff
        if not passed:
            raise ValueError(f"Role-boundary future-label overlap: {previous} -> {following}")
        checks.append({"previous_role": previous, "following_role": following,
                       "previous_max_target_end_utc": label_end.isoformat(),
                       "following_first_reference_utc": first_reference.isoformat(),
                       "following_first_availability_cutoff_utc": availability_cutoff.isoformat(),
                       "cutoff_definition": "reference minus3h; not public source-arrival certification",
                       "comparison": "previous_max_target_end <= following_first_availability_cutoff",
                       "passed": bool(passed)})
    return {"purged_reference_dates": BOUNDARY_PURGE_DATES,
            "purge_performed_by": "prepared data builder; trainer refuses any still-present purged day",
            "checks": checks}


def validate_frames(daily, live, contract, now=None):
    clock_fields = {"reference_utc", "target_start_utc", "target_end_utc", "history_latest_event_end_utc",
                    "feature_max_observed_utc", "hmi_latest_support_end_utc", "hmi_latest_processing_utc",
                    "hmi_nominal_tai", "source_mode", "all_magnetic_quality_zero", "status",
                    "candidate_central_patch_count", "rejected_central_patch_count", "unknown_geometry_patch_count", "partial_patch_coverage",
                    "past_168h_coverage_fraction", "past_168h_max_gap_minutes"}
    required = clock_fields | {"y_class", "label_c1", "label_m1", "split",
                               "future_coverage_fraction", "future_max_gap_minutes", *FEATURES, *HISTORY}
    if daily.empty or not required.issubset(daily.columns) or len(live) != 1 or not (clock_fields | {*FEATURES, *HISTORY, "source_retrieved_utc", "history_baseline_status"}).issubset(live.columns):
        raise ValueError("Missing physical/history/clock columns or single live row")
    if live.iloc[0].history_baseline_status not in ("qualified", "unknown_not_issued"):
        raise ValueError("Live history baseline must be explicitly qualified or unknown/not issued")
    live_history_ready = live.iloc[0].history_baseline_status == "qualified"
    if "history_baseline_status" in daily and not daily.history_baseline_status.eq("qualified").all():
        raise ValueError("Historical baseline status must be qualified on all common dates")
    if daily.reference_utc.duplicated().any():
        raise ValueError("Duplicate reference dates")
    for frame in (daily, live):
        if not frame.status.eq("eligible").all() or not frame.source_mode.eq(contract["source_mode"]).all():
            raise ValueError("Missing/excluded/mixed-mode magnetism cannot enter a main model")
        if not frame.all_magnetic_quality_zero.eq(True).all():
            raise ValueError("Strict QUALITY0 is required; no0x400 fallback")
        central = frame.candidate_central_patch_count.to_numpy(float)
        rejected = frame.rejected_central_patch_count.to_numpy(float)
        unknown = frame.unknown_geometry_patch_count.to_numpy(float)
        if not np.isfinite(central).all() or not np.equal(central, np.floor(central)).all() or not (central > 0).all():
            raise ValueError("Central candidate patch counts are missing or invalid")
        if not np.isfinite(rejected).all() or not np.isfinite(unknown).all() or not ((rejected == 0) & (unknown == 0)).all() or not frame.partial_patch_coverage.eq(False).all():
            raise ValueError("Any rejected central patch, unknown geometry or partial query invalidates the entire reference")
        if not frame.patch_count.eq(frame.candidate_central_patch_count).all():
            raise ValueError("Main aggregation must include every central candidate patch")
        if not frame.reference_utc.astype(str).str.fullmatch(r"\d{4}-\d{2}-\d{2}T16:00:00Z").all():
            raise ValueError("Canonical historical/live16UTC reference is required")
        refs = pd.to_datetime(frame.reference_utc, utc=True, errors="raise")
        if not utc_series(frame, "target_start_utc").eq(refs).all() or not utc_series(frame, "target_end_utc").eq(refs+pd.Timedelta(hours=24)).all():
            raise ValueError("Target labels shifted from the registered Beijing whole day")
        nominal = refs.dt.strftime("%Y.%m.%d_13:00:00_TAI")
        if not frame.hmi_nominal_tai.eq(nominal).all():
            raise ValueError("Magnetic anchor changed after looking at qualification/results")
        support = utc_series(frame, "hmi_latest_support_end_utc")
        processed = utc_series(frame, "hmi_latest_processing_utc")
        if support.isna().any() or processed.isna().any() or not (support < refs).all() or not (processed <= refs).all():
            raise ValueError("Magnetic support/processing is later than reference")
        fmax = utc_series(frame, "feature_max_observed_utc")
        if frame is live and not live_history_ready:
            end = pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns, UTC]")
        else:
            end = utc_series(frame, "history_latest_event_end_utc", nullable=True)
        exactmax = pd.concat((support, end), axis=1).max(axis=1)
        if not fmax.eq(exactmax).all() or not (fmax < refs).all():
            raise ValueError("Feature max must equal actual magnetic support/history-end maximum")
        values = frame[FEATURES].to_numpy(float)
        unsigned = [c for c in FEATURES if not c.startswith("MEANALP_")]
        if not np.isfinite(values).all() or (frame[unsigned].to_numpy(float) < 0).any():
            raise ValueError("Missing/invalid physical values cannot be zero-filled")
        count = frame.patch_count.to_numpy(float)
        if not ((count > 0) & (count == np.floor(count))).all():
            raise ValueError("Physical model requires a positive integer patch count")
        for name in PHYSICAL:
            if not (frame[name+"_max"] >= frame[name+"_median"]).all():
                raise ValueError("Patch max must be at least the patch median")
        if frame is daily or live_history_ready:
            validate_history(frame, refs)
            frac, gap = frame.past_168h_coverage_fraction.to_numpy(float), frame.past_168h_max_gap_minutes.to_numpy(float)
            if not np.isfinite(frac).all() or not np.isfinite(gap).all() or not ((frac >= .99) & (frac <= 1) & (gap >= 0) & (gap <= 5)).all():
                raise ValueError("Past XRS coverage not qualified")
    refs = pd.to_datetime(daily.reference_utc, utc=True)
    if refs.dt.strftime("%Y-%m-%d").isin(BOUNDARY_PURGE_DATES).any():
        raise ValueError("Registered boundary purge day is still present in training/evaluation input")
    if contract["source_mode"] == "hmi.sharp_cea_720s_nrt" and (refs < pd.Timestamp("2012-09-14T16:00:00Z")).any():
        raise ValueError("NRT archive has no2011/early2012 input; never synthesize that period")
    expected = pd.Series("excluded", index=daily.index)
    for role, (a, b) in contract["splits"].items():
        mask = (refs >= pd.Timestamp(a, tz="UTC")) & (refs < pd.Timestamp(b, tz="UTC")+pd.Timedelta(days=1))
        expected.loc[mask] = role
    if not expected.eq(daily.split).all() or expected.eq("excluded").any():
        raise ValueError("Registered chronological splits differ")
    if not np.isin(daily.y_class, (0, 1, 2)).all() or not np.array_equal(daily.label_c1, daily.y_class >= 1) or not np.array_equal(daily.label_m1, daily.y_class == 2):
        raise ValueError("Unknown or nonnested outcome labels cannot be negative-filled")
    frac, gap = daily.future_coverage_fraction.to_numpy(float), daily.future_max_gap_minutes.to_numpy(float)
    if not np.isfinite(frac).all() or not np.isfinite(gap).all() or not ((frac >= .99) & (frac <= 1) & (gap >= 0) & (gap <= 5)).all():
        raise ValueError("Future24h independent coverage not qualified")
    if (utc_series(daily, "target_end_utc") > parse_utc(contract["historical_observation_cutoff_utc"])).any():
        raise ValueError("Incomplete future windows entered historical evaluation")
    if min(daily.split.value_counts().reindex(ROLES, fill_value=0)) < 1 or set(daily.loc[daily.split.eq("train"), "y_class"]) != {0, 1, 2}:
        raise ValueError("No qualified rows in a role or missing candidate-train class")
    role_boundary_audit(daily)
    if live.iloc[0].reference_utc != contract["data_cutoff"]:
        raise ValueError("Live magnetic reference differs from target start")
    if now is not None:
        stamp = validated_future_issue(contract, now)
        row = live.iloc[0]
        if parse_utc(row.source_retrieved_utc) < parse_utc(row.hmi_latest_processing_utc):
            raise ValueError("Live source retrieval predates the current magnetic processing")
        if parse_utc(row.hmi_latest_processing_utc) > stamp or parse_utc(row.hmi_latest_support_end_utc) > stamp or parse_utc(row.source_retrieved_utc) > stamp:
            raise ValueError("Live source was not available/retrieved at actual issuance")
        if parse_utc(row.feature_max_observed_utc) > stamp:
            raise ValueError("Live event/magnetic feature is later than actual issuance")
        if live_history_ready and ("history_source_retrieved_utc" not in live or parse_utc(row.history_source_retrieved_utc) > stamp):
            raise ValueError("Qualified live history has no actual earlier source retrieval")


def checked_inputs(input_dir, contract_path, now=None):
    input_dir, contract_path = Path(input_dir).resolve(), Path(contract_path).resolve()
    if CONTRACT_SHA is None or digest(contract_path) != CONTRACT_SHA:
        raise ValueError("Exact production contract is not frozen or differs")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    validate_contract(contract)
    mp = input_dir/"prepared_manifest.json"
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    if manifest.get("contract_sha256") != CONTRACT_SHA or manifest.get("features_physical") != FEATURES or manifest.get("features_history_baseline") != HISTORY:
        raise ValueError("Prepared main/baseline schema differs")
    for key in ("source_plan_sha256", "source_profile_sha256"):
        if manifest.get(key) != contract[key]:
            raise ValueError("Prepared source/profile provenance differs")
    paths = [r["path"] for r in manifest["files"]]
    if len(paths) != len(set(paths)) or not {"daily_instances.csv", "live_features.csv", "validation.json"}.issubset(paths):
        raise ValueError("Every consumed input needs a unique byte hash")
    before = {str(mp): digest(mp), str(contract_path): digest(contract_path)}
    for dep in (Path(__file__), Path(__file__).with_name("forecast_math_reference.py"), Path(__file__).with_name("frozen_math_core.py")):
        before[str(dep)] = digest(dep)
    for record in manifest["files"]:
        rel = Path(record["path"])
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError("Unsafe prepared relative path")
        path = (input_dir/rel).resolve()
        if not path.is_relative_to(input_dir) or digest(path) != record["sha256"]:
            raise ValueError("Prepared byte hash mismatch")
        before[str(path)] = digest(path)
    qualification = json.loads((input_dir/"validation.json").read_text(encoding="utf-8"))
    if qualification.get("status") != "passed":
        raise ValueError("Prepared data qualification did not pass")
    daily, live = pd.read_csv(input_dir/"daily_instances.csv"), pd.read_csv(input_dir/"live_features.csv")
    validate_frames(daily, live, contract, now)
    qualification = {"status": "passed", "prepared_manifest_sha256": digest(mp),
                     "data_validation_sha256": digest(input_dir/"validation.json"),
                     "source_manifest_sha256": manifest["source_manifest_sha256"],
                     "source_plan_sha256": contract["source_plan_sha256"],
                     "source_profile_sha256": contract["source_profile_sha256"],
                     "source_mode": contract["source_mode"], "raw_validation_scope": "upstream Data raw checks; prepared hashes/interface here"}
    return contract, daily, live, before, qualification


def model_predictions(math, selected, history_model, cal, history_cal, class_q, frame):
    x = frame[FEATURES].to_numpy(float)
    result = {"selected_calibrated": math.target_probabilities(math.apply_temperature(math.probability_matrix(selected, x), cal["temperature"])),
              "train_climatology": math.target_probabilities(np.tile(class_q, (len(frame), 1)))}
    if "history_baseline_status" not in frame or frame.history_baseline_status.eq("qualified").all():
        h = frame[HISTORY].to_numpy(float)
        result.update(past24_persistence={TARGETS[0]: frame.history_24h_c_count.gt(0).to_numpy(float), TARGETS[1]: frame.history_24h_m_count.gt(0).to_numpy(float)},
                      history_only=math.target_probabilities(math.apply_temperature(math.probability_matrix(history_model, h), history_cal["temperature"])))
    return result


def permutation_scores(math, model, x, y, temperature, repeats=10):
    rng = np.random.default_rng(SEED)
    base = math.target_probabilities(math.apply_temperature(math.probability_matrix(model, x), temperature))
    truth = (y >= 1, y == 2)
    baseline = [float(np.mean((base[t]-v)**2)) for t, v in zip(TARGETS, truth)]
    records = []
    for j, feature in enumerate(FEATURES):
        diffs = []
        for _ in range(repeats):
            altered = x.copy()
            altered[:, j] = altered[rng.permutation(len(x)), j]
            p = math.target_probabilities(math.apply_temperature(math.probability_matrix(model, altered), temperature))
            diffs.append([float(np.mean((p[t]-v)**2))-b for t, v, b in zip(TARGETS, truth, baseline)])
        a = np.asarray(diffs)
        records.append({"feature": feature, "C_Brier_increase_mean": float(a[:, 0].mean()), "C_Brier_increase_std": float(a[:, 0].std()),
                        "M_Brier_increase_mean": float(a[:, 1].mean()), "M_Brier_increase_std": float(a[:, 1].std()),
                        "mean_Brier_increase": float(a.mean(axis=1).mean()), "mean_Brier_increase_std": float(a.mean(axis=1).std()),
                        "repeats": repeats, "role": IMPORTANCE_ROLE, "use_for_model_feature_selection": False,
                        "repeat_values_json": json.dumps(a.tolist())})
    return pd.DataFrame(records)


def evaluate(math, test, labels, predictions, references):
    rows, yearly, bins = [], [], []
    years = pd.to_datetime(test.reference_utc, utc=True).dt.year.to_numpy()
    outcomes = {TARGETS[0]: labels >= 1, TARGETS[1]: labels == 2}
    for name, probabilities in predictions.items():
        for target in TARGETS:
            metrics = math.binary_metrics(outcomes[target], probabilities[target], .5, references[target])
            metrics.update(always_no_event_accuracy=1-metrics["positive_rate"], always_yes_event_accuracy=metrics["positive_rate"])
            rows.append({"model_id": name, "target": target, "threshold_kind": "main_0.5", **metrics})
            bins += math.reliability_bins(outcomes[target].astype(int), probabilities[target], name, target)
            for year in (2023, 2024, 2025, 2026):
                mask = years == year
                if not mask.any():
                    yearly.append({"model_id": name, "target": target, "year": year, "n": 0, "status": "no_qualified_completed_windows"})
                    continue
                result = math.binary_metrics(outcomes[target][mask], probabilities[target][mask], .5, references[target])
                result.update(always_no_event_accuracy=1-result["positive_rate"], always_yes_event_accuracy=result["positive_rate"])
                yearly.append({"model_id": name, "target": target, "year": year, "threshold_kind": "main_0.5",
                               "status": "completed_historical_windows", "reference_start": test.loc[mask, "reference_utc"].min(),
                               "reference_end": test.loc[mask, "reference_utc"].max(), **result})
    return pd.DataFrame(rows), pd.DataFrame(yearly), bins, outcomes


def range_audit(final_pool, live):
    records = []
    for name in FEATURES:
        values, v = final_pool[name].to_numpy(float), float(live.iloc[0][name])
        records.append({"feature": name, "live_value": v, "fit_min": float(values.min()), "fit_max": float(values.max()),
                        "outside_fit_range": bool(v < values.min() or v > values.max())})
    return {"records": records, "outside_count": sum(r["outside_fit_range"] for r in records),
            "scope": "Marginal descriptive range; not domain adaptation or individual confidence"}


def create_figures(output, issued, importance, bins, metrics):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import Normalize
    from matplotlib.cm import ScalarMappable

    plt.rcParams.update({"font.sans-serif": ["Microsoft YaHei", "SimHei", "DejaVu Sans"], "axes.unicode_minus": False})
    figures = []
    def save(fig, name, question, limitation):
        for suffix in ("png", "svg"):
            path = output/f"{name}.{suffix}"
            fig.savefig(path, dpi=180, bbox_inches="tight")
            figures.append({"path": path.name, "sha256": digest(path), "question": question, "limitation": limitation})
        plt.close(fig)
    fig, ax = plt.subplots(figsize=(9, 4.8))
    values = np.asarray([[r["probability"] for r in issued["targets"]]])
    ax.imshow(values, vmin=0, vmax=1, cmap="YlOrRd", aspect="auto")
    ax.set_xticks([0, 1], ["C1.0及以上", "M1.0及以上（含X）"])
    localday = (parse_utc(issued["prediction_window"]["start"])+pd.Timedelta(hours=8)).strftime("%m月%d日")
    ax.set_yticks([0], [localday+"\n北京时间全天"])
    for j, v in enumerate(values[0]):
        ax.text(j, 0, f"{v:.1%}", ha="center", va="center", fontsize=32, color="white" if v > .65 else "black")
    fig.colorbar(ScalarMappable(norm=Normalize(0, 1), cmap="YlOrRd"), ax=ax, label="模型发生概率")
    ax.set_title("JW磁场输入前瞻试验：全日面耀斑概率\n真实SHARP物理摘要；目标结果尚未知", fontsize=13)
    ax.set_xlabel(f"UTC目标：{issued['prediction_window']['start']} → {issued['prediction_window']['end']}\n实际发报：{issued['issued_utc']}；磁场参考：{issued['data_cutoff']}", fontsize=8, labelpad=14)
    fig.tight_layout()
    save(fig, "01_physical_probability_heatmap", "目标北京全天会有新开始的C/M级耀斑吗？", "实验概率不是明天命中率；依赖已登记磁场源模式和资格样本。")
    ordered = importance.sort_values("mean_Brier_increase")
    fig, ax = plt.subplots(figsize=(10, 9))
    ax.barh(ordered.feature, ordered.mean_Brier_increase, xerr=ordered.mean_Brier_increase_std, color="#00798c", capsize=3)
    ax.axvline(0, lw=.8, color="black")
    ax.set_xlabel("打乱物理输入后平均Brier误差增加（10次，误差条SD）\n固定测试期描述；不据此选型或选特征，不证明机制", fontsize=9)
    ax.set_title("最终磁场模型依赖哪些SHARP摘要？\n23项全部保留；负重要性照录")
    fig.tight_layout()
    save(fig, "02_physical_feature_importance", "模型依赖哪些物理摘要输入？", "测试复用解释；max/median是斑块聚合、相关输入可以替代，不代表物理因果。")
    fig, axes = plt.subplots(2, 2, figsize=(11, 8))
    colors = {"selected_calibrated": "#d1495b", "train_climatology": "#777777", "past24_persistence": "#e4ad38", "history_only": "#00798c"}
    for j, target in enumerate(TARGETS):
        top, bottom = axes[0, j], axes[1, j]
        top.plot([0, 1], [0, 1], "--", color="gray", lw=1)
        for name, color in colors.items():
            points = [r for r in bins if r["target"] == target and r["model_id"] == name and r["n"]]
            top.plot([r["mean_predicted_probability"] for r in points], [r["observed_fraction"] for r in points], "o-", color=color, label=name)
        top.set(xlim=(0, 1), ylim=(0, 1), xlabel="预测概率", ylabel="测试发生比例", title=target+" 可靠性")
        top.legend(fontsize=7)
        table = metrics[metrics.target.eq(target)].set_index("model_id")
        bars = bottom.bar(["磁场模型", "训练发生率", "24h持续性", "历史基线"], [float(table.loc[k, "Brier"]) for k in colors], color=list(colors.values()))
        bottom.bar_label(bars, fmt="%.4f", fontsize=9)
        bottom.set(ylabel="Brier（越低越好）", title="同合格日期、同北京全天标签比较")
    fig.suptitle("2023—2026已结束窗口的持出检验；不是明天正确率")
    fig.tight_layout()
    save(fig, "03_physical_reliability_baselines", "同日期物理模型是否优于历史基线？", "年度/总体技能分开；结果不佳也保留，不根据测试更换选型。")
    return figures


def save_lock(output, locked, groups, before, contract):
    sig = json.dumps(locked["locked_configuration_signature"], sort_keys=True, separators=(",", ":"), allow_nan=False)
    result = {"schema_version": "JW-physical-configuration-lock-v1", **locked,
              "locked_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
              "configuration_sha256": hashlib.sha256(sig.encode()).hexdigest(), "contract_sha256": CONTRACT_SHA,
              "source_plan_sha256": contract["source_plan_sha256"], "source_profile_sha256": contract["source_profile_sha256"],
              "candidate_fit_reference_start": groups["train"].reference_utc.min(), "candidate_fit_reference_end": groups["train"].reference_utc.max(),
              "selection_reference_start": groups["selection"].reference_utc.min(), "selection_reference_end": groups["selection"].reference_utc.max(),
              "candidate_fit_rows": len(groups["train"]), "selection_rows": len(groups["selection"]), "input_hashes": before}
    write_json(output/"selected_config_lock.json", result)


def save_refit(output, audit, pool, groups, contract):
    if set(pool.split) != {"train", "selection"} or (pd.to_datetime(pool.reference_utc, utc=True).dt.year >= 2022).any():
        raise ValueError("Final refit contains calibration/test/live rows")
    result = {"schema_version": "JW-physical-final-refit-v1", **audit,
              "completed_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
              "selected_configuration_lock_sha256": digest(output/"selected_config_lock.json"),
              "fit_reference_start": pool.reference_utc.min(), "fit_reference_end": pool.reference_utc.max(),
              "fit_class_counts": np.bincount(pool.y_class.to_numpy(int), minlength=3).tolist(),
              "fit_reference_dates_sha256": hashlib.sha256("\n".join(pool.reference_utc).encode()).hexdigest(),
              "fit_rows_from_calibration": 0, "fit_rows_from_test": 0, "fit_rows_year_2022": 0, "fit_rows_year_2023_or_later": 0,
              "contract_sha256": CONTRACT_SHA, "source_mode": contract["source_mode"],
              "same_dates_baselines": True, "history_baseline_feature_count": 12,
              "history_baseline_fit_policy": "fixed Logistic C1 on the same final train+selection dates; separately2022 calibrated",
              "calibration_rows_excluded": len(groups["calibration"]), "test_rows_excluded": len(groups["test"])}
    write_json(output/"final_refit_receipt.json", result)
    return result


def fit_pool_receipts(groups):
    """Hash exact rows, labels, and consumed matrices for every fitting role."""
    pool = pd.concat((groups["train"], groups["selection"]), ignore_index=True)
    def receipt(frame, columns, role, calls):
        x = np.ascontiguousarray(frame[columns].to_numpy(dtype="<f8"))
        y = np.ascontiguousarray(frame.y_class.to_numpy(dtype="<i8"))
        return {"role": role, "fit_calls_planned": calls, "rows": len(frame), "feature_columns": columns,
                "reference_start": frame.reference_utc.min(), "reference_end": frame.reference_utc.max(),
                "reference_dates_sha256": hashlib.sha256("\n".join(frame.reference_utc).encode()).hexdigest(),
                "features_float64_le_sha256": hashlib.sha256(x.tobytes()).hexdigest(),
                "labels_int64_le_sha256": hashlib.sha256(y.tobytes()).hexdigest(),
                "class_counts": np.bincount(y, minlength=3).tolist()}
    return {"schema_version": "JW-physical-fit-pools-v1", "completion_status": "planned_inputs_only", "test_or_live_fit_calls_planned": 0,
            "candidate_fits": receipt(groups["train"], FEATURES, "candidate_train_only", 9),
            "selected_final_refit": receipt(pool, FEATURES, "train_union_selection_once", 1),
            "history_baseline_fit": receipt(pool, HISTORY, "same_dates_train_union_selection_once", 1),
            "temperature_calibration": receipt(groups["calibration"], FEATURES, "temperature_calibration_only_not_estimator_refit", 1),
            "history_temperature_calibration": receipt(groups["calibration"], HISTORY, "history_temperature_calibration_only", 1),
            "calibration_count_note": "Temperature may remain identity when any class has fewer20; actual status in calibration.json"}


def run_experiment(input_dir, contract_path, output):
    started = time.monotonic()
    output = Path(output)
    if output.exists():
        raise ValueError("Never overwrite an earlier fit or issued forecast")
    contract, daily, live, before, qualification = checked_inputs(input_dir, contract_path, datetime.now(timezone.utc))
    output.mkdir(parents=True)
    done = threading.Event()
    def timeout():
        if not done.wait(min(int(contract.get("training_wall_seconds_max", 1500)), 1500)):
            write_json(output.parent/(output.name+".timeout.json"), {"status": "failed", "reason": "hard_wall_limit", "no_retry": True})
            os._exit(124)
    threading.Thread(target=timeout, daemon=True).start()
    try:
        math = load_math()
        groups = {role: daily[daily.split.eq(role)].copy().reset_index(drop=True) for role in ROLES}
        arrays = {k: g[FEATURES].to_numpy(float) for k, g in groups.items()}
        labels = {k: g.y_class.to_numpy(int) for k, g in groups.items()}
        pool = pd.concat((groups["train"], groups["selection"]), ignore_index=True)
        write_json(output/"planned_fit_pool_inputs.json", fit_pool_receipts(groups))
        with threadpool_limits(limits=4):
            selected_id, selected, scores, audit = math.fit_selection_then_refit(
                arrays["train"], labels["train"], arrays["selection"], labels["selection"], candidates=candidate_models(),
                on_selected=lambda locked: save_lock(output, locked, groups, before, contract))
            refit = save_refit(output, audit, pool, groups, contract)
            history_model = make_pipeline(StandardScaler(), LogisticRegression(C=1, max_iter=2000, random_state=SEED))
            history_model.fit(pool[HISTORY].to_numpy(float), pool.y_class.to_numpy(int))
            cal = math.fit_temperature(labels["calibration"], math.probability_matrix(selected, arrays["calibration"]))
            hcal = math.fit_temperature(labels["calibration"], math.probability_matrix(history_model, groups["calibration"][HISTORY].to_numpy(float)))
            class_q = np.bincount(pool.y_class.to_numpy(int), minlength=3)/len(pool)
            refs = {TARGETS[0]: float(class_q[1:].sum()), TARGETS[1]: float(class_q[2])}
            pred = model_predictions(math, selected, history_model, cal, hcal, class_q, groups["test"])
            metrics, year_metrics, bins, outcomes = evaluate(math, groups["test"], labels["test"], pred, refs)
            importance = permutation_scores(math, selected, arrays["test"], labels["test"], cal["temperature"])
            ci = math.block_bootstrap(groups["test"].reference_utc, outcomes, pred, refs, draws=200)
            live_pred = model_predictions(math, selected, history_model, cal, hcal, class_q, live)
        fit_receipts = fit_pool_receipts(groups)
        fit_receipts.update(completion_status="completed", candidate_estimator_fits_completed=9,
                            selected_final_refits_completed=1, history_baseline_fits_completed=1,
                            test_or_live_estimator_fits_completed=0, temperature_function_calls_completed=2,
                            selected_temperature_status=cal["status"], history_temperature_status=hcal["status"])
        write_json(output/"fit_pool_receipts.json", fit_receipts)
        stamp = validated_future_issue(contract, datetime.now(timezone.utc))
        validate_frames(daily, live, contract, stamp)
        for path, sha in before.items():
            if digest(path) != sha:
                raise ValueError("Input or code changed during fitting")
        issued = {"schema_version": "JW-physical-prospective-issue-v1", "task_id": contract["task_id"],
                  "status": "issued_experimental_pending_observation", "forecast_mode": "live", "spatial_unit": "full_disk",
                  "issued_utc": stamp.isoformat().replace("+00:00", "Z"), "data_cutoff": contract["data_cutoff"],
                  "reference_utc": contract["data_cutoff"],
                  "data_cutoff_semantics": "Registered16UTC reference; not a claim that future inputs were available at issuance",
                  "physical_support_end_utc": str(live.iloc[0].hmi_latest_support_end_utc),
                  "physical_processing_utc": str(live.iloc[0].hmi_latest_processing_utc),
                  "physical_retrieved_utc": str(live.iloc[0].source_retrieved_utc),
                  "prediction_window": contract["prediction_window"], "local_prediction_window": contract["local_prediction_window"],
                  "source_mode": contract["source_mode"], "selected_model_id": selected_id, "features": 23,
                  "magnetic_coverage_scope": "Complete queried central(|Stonyhurst longitude|<=60deg) patch summaries; target is whole-Sun flares, not complete whole-Sun magnetic measurement",
                  "magnetic_features_used": True, "main_uses_history_features": False,
                  "contract_sha256": CONTRACT_SHA, "prepared_manifest_sha256": qualification["prepared_manifest_sha256"],
                  "targets": [{"threshold": t, "probability": float(live_pred["selected_calibrated"][t][0]), "decision_threshold": .5,
                               "predict_occurrence": bool(live_pred["selected_calibrated"][t][0] >= .5), "observed_outcome": None, "tomorrow_accuracy": None} for t in TARGETS],
                  "baseline_probabilities": {n: {t: float(p[t][0]) for t in TARGETS} for n, p in live_pred.items() if n != "selected_calibrated"},
                  "live_history_baseline_status": live.iloc[0].history_baseline_status,
                  "unissued_live_baselines": [n for n in ("past24_persistence", "history_only") if n not in live_pred],
                  "history_availability_note": "reference-minus3h is a reference buffer, not independently proved event-publication latency before actual issue",
                  "calibration": cal, "range_audit": range_audit(pool, live),
                  "selected_configuration_lock_sha256": digest(output/"selected_config_lock.json"), "final_refit_receipt_sha256": digest(output/"final_refit_receipt.json"),
                  "limitations": contract["limitations"], "actual_JW_origin": "External genuine AI request/ToolMessage required, not self-certified"}
        write_json(output/"issued_forecast.json", issued)
        for name, df in (("test_metrics", metrics), ("test_year_metrics", year_metrics), ("permutation_importance", importance), ("bootstrap_ci", ci), ("model_selection", pd.DataFrame(scores))):
            df.to_csv(output/(name+".csv"), index=False)
        test = groups["test"].copy()
        for name, probs in pred.items():
            test[f"p_C_{name}"], test[f"p_M_{name}"] = probs[TARGETS[0]], probs[TARGETS[1]]
        test.to_csv(output/"heldout_predictions.csv", index=False)
        write_json(output/"calibration.json", {"selected": cal, "history_only": hcal, "reliability_bins": bins,
                  "fit_role": "calibration_2022_only", "test_used_to_fit": False, "baseline_climatology_class_probabilities": class_q.tolist()})
        joblib.dump({"selected_model": selected, "history_only_model": history_model, "selected_model_id": selected_id,
                     "feature_columns": FEATURES, "baseline_feature_columns": HISTORY, "selected_temperature": cal["temperature"],
                     "history_temperature": hcal["temperature"], "source_mode": contract["source_mode"], "contract_sha256": CONTRACT_SHA}, output/"models.joblib")
        figs = create_figures(output, issued, importance, bins, metrics)
        write_json(output/"figure_index.json", {"origin": "This deterministic invocation; external actualJW receipt required", "products": figs,
                  "source_manifest_sha256": qualification["source_manifest_sha256"], "contract_sha256": CONTRACT_SHA, "script_sha256": digest(__file__)})
        validation = {"status": "passed", "qualification": qualification, "input_hashes_before": before,
                      "input_hashes_after": {p: digest(p) for p in before}, "input_hashes_unchanged": True,
                      "main_feature_columns": FEATURES, "baseline_feature_columns": HISTORY, "main_feature_count": 23,
                      "source_mode": contract["source_mode"], "source_profile_sha256": contract["source_profile_sha256"],
                      "same_source_mode_historical_and_live": True, "history_recomputed_for_reference16_minus3h": True,
                      "target_offset_hours": [0, 24], "actual_issue_before_target": True, "live_future_label_used": False,
                      "test_used_to_fit_or_select": False, "final_refit_receipt": refit, "importance_role": IMPORTANCE_ROLE,
                      "role_boundary_isolation": role_boundary_audit(daily),
                      "test_reused_for_fixed_descriptive_importance": True, "importance_used_for_fit_select_calibrate_or_live_probability": False,
                      "script_sha256": digest(__file__), "frozen_math_sha256": MATH_SHA, "frozen_core_sha256": CORE_SHA}
        write_json(output/"validation.json", validation)
        summary = {"status": "completed", "task_id": contract["task_id"], "selected_model_id": selected_id,
                   "main_feature_count": 23, "baseline_feature_count": 12, "source_mode": contract["source_mode"],
                   "split_counts": {k: len(v) for k, v in groups.items()}, "split_class_counts": {k: np.bincount(v, minlength=3).tolist() for k, v in labels.items()},
                   "historical_test_metrics": json.loads(metrics.to_json(orient="records")), "issued_forecast": issued,
                   "heldout_year_metrics_file": "test_year_metrics.csv", "heldout_2026_selected_metrics": json.loads(year_metrics[year_metrics.year.eq(2026) & year_metrics.model_id.eq("selected_calibrated")].to_json(orient="records")),
                   "final_refit": refit, "development_rows": len(pool)+len(groups["calibration"]), "final_fit_rows": len(pool),
                   "development_to_test_ratio": (len(pool)+len(groups["calibration"]))/len(groups["test"]), "final_fit_to_test_ratio": len(pool)/len(groups["test"]),
                   "calibration": {"selected": cal, "history_only": hcal}, "selected_test_intervals": json.loads(ci[ci.model_id.eq("selected_calibrated")].to_json(orient="records")),
                   "importance_role": IMPORTANCE_ROLE, "same_date_baselines": True, "tomorrow_accuracy": None,
                   "role_boundary_isolation": role_boundary_audit(daily),
                   "limitations": contract["limitations"], "wall_seconds": time.monotonic()-started,
                   "raw_source_audit_scope": "Upstream Data role and separate raw-source QA; this tool verifies prepared hashes and method interface"}
        write_json(output/"summary.json", summary)
        products = [{"path": p.name, "sha256": digest(p), "bytes": p.stat().st_size} for p in sorted(output.iterdir()) if p.is_file()]
        write_json(output/"proof.json", {"contract_sha256": CONTRACT_SHA, "script_sha256": digest(__file__), "products": products,
                                       "caller_origin_requires_external_JW_receipt": True})
        return {"status": "completed", "output_directory": str(output), "selected_model_id": selected_id, "issued_utc": issued["issued_utc"], "proof_sha256": digest(output/"proof.json")}
    finally:
        done.set()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(run_experiment(args.input_dir, args.contract, args.output_dir), ensure_ascii=False))
    except Exception as exc:
        args.output_dir.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output_dir.parent/(args.output_dir.name+".failed.json"),
                   {"status": "failed", "error": repr(exc), "traceback": traceback.format_exc(), "no_automatic_retry": True})
        raise


if __name__ == "__main__":
    sys.exit(main())
