# JW Evidence-First Solar-Cycle Forecast Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a JW Agent vertical slice that uses real MWO–WSO polar data, compares every predictive claim with chronological baselines, treats axial-dipole evidence honestly, and exposes only receipt-backed conclusions in the final answer.

**Architecture:** Extend the existing `solar_polar_precursor_v1` route rather than creating a second research loop. Data produces typed precursor records, Automatic Experiment runs a pre-registered rolling-origin tournament, Hypothesis ranking records scientific role and lifecycle separately from research priority, and Evidence/Final Release consume only persisted receipts. Execution liveness remains a separate sidecar so a dead worker cannot masquerade as an active scientific run.

**Tech Stack:** Python 3.11, pandas, NumPy, scikit-learn, existing LangChain tools and Automatic Experiment sandbox, pytest, TypeScript/Next.js WebUI.

## Global Constraints

- Preserve every pre-existing modified or untracked file; patch the current contents and stage only files belonging to the current task.
- Do not add runtime dependencies; use the scientific stack already declared in `pyproject.toml`.
- Keep the eight research stages and internal `a2a-handoff-v1`; do not add an open-network A2A implementation.
- H1 is a same-cycle early-rise forecast; H2 and H3 are next-cycle precursors near a cycle minimum. Never combine their scores across forecast origins.
- The MWO facular series is a calibrated proxy and the WSO polar aperture series is a polar-field observation. Neither may be labeled `axial_dipole_moment`.
- H3 may use only a registered axial-dipole data product or a fixed computation from registered synoptic radial-field maps. Otherwise persist `blocked_by_data`.
- A predictive claim is `skill_supported` only when paired rolling-origin MAE improvement is positive, its cycle-level bootstrap 95% interval has lower bound above zero, and the pre-registered measurement-regime sensitivity does not reverse the conclusion.
- Static tests, real execution, real model calls, headed WebUI completion, and scientific validity are separate acceptance layers.
- Reader-facing Chinese reports contain the research question, data, method, evidence, uncertainty, and conclusion; they omit internal IDs, hashes, tool traces, and workflow terminology.

---

## File Map

**New focused modules**

- `jw/solar_forecast/__init__.py`: public typed-contract and classification exports.
- `jw/solar_forecast/contracts.py`: validation for precursor feature records and forecast experiment receipts.
- `jw/solar_forecast/backtest.py`: deterministic rolling-origin models, paired bootstrap, regime sensitivity, and status classification.
- `src/research_review/execution_state.py`: independent execution-liveness sidecar contract and atomic store.
- `tests/test_solar_forecast_contracts.py`: typed receipt and physical-variable boundary tests.
- `tests/test_solar_precursor_backtest.py`: rolling-origin, leakage, baseline, bootstrap, and H3 comparison tests.
- `tests/test_research_execution_state.py`: heartbeat, interruption, failure, and stale-process tests.

**Existing production integration points**

- `jw/tools/solar_feature.py:1983-2224`: add feature-record and unavailable-axial-dipole receipts to the existing polar precursor table.
- `jw/research_protocols.py:18-30,399-423`: declare the pre-registered polar forecast experiment and its required outputs.
- `jw/tools/automatic_experiment.py:539-959,1541-1688,1860-1875`: add host-created polar forecast design and immutable worker preparation.
- `src/scientific_hypothesis/ranking.py:414-849`: add role/lifecycle gates without discarding current support/priority work.
- `jw/tools/scientific_hypothesis.py:2885-2960`: persist validation failures and return role/lifecycle orders.
- `jw/middleware/research_review_orchestration.py:2078-2304`: carry the bounded role, status, forecast origin, and experiment receipt through A2A.
- `src/research_review/adapters.py:900-930,1150-1260`: expose receipt-backed statements to Integration and Final Release.
- `jw/research_review.py:391-560`: update the separate execution sidecar on real progress and terminal failures.
- `jw/middleware/configurable_model.py:45-220`: reject invalid model/provider overrides instead of silently falling back.
- `jw/subagents/solar/solar_data.yaml:9-31`: require typed feature receipts for the polar route.
- `jw/subagents/solar/solar_hypothesis.yaml:84-92`: generate H1/H2/H3 roles and lifecycle states.
- `jw/subagents/solar/solar_experiment.yaml`: require the pre-registered polar tournament.
- `jw/subagents/solar/solar_evidence.yaml`: independently check feature use, forecast origin, and baseline skill.
- `webui/src/app/api/research-review/status/route.ts:100-166,400-420`: project portfolio lifecycle and execution liveness.
- `webui/src/app/components/ResearchReviewPanel.tsx:40-100,257-322`: display support, priority, role, lifecycle, and run liveness separately.
- `webui/src/lib/researchPortfolio.js`: pure reader-label projection used by the panel.
- `webui/test/research-portfolio.test.js`: Node tests for portfolio labels and active-count wording.

---

### Task 1: Typed precursor and forecast receipts

**Files:**
- Create: `jw/solar_forecast/__init__.py`
- Create: `jw/solar_forecast/contracts.py`
- Create: `tests/test_solar_forecast_contracts.py`

**Interfaces:**
- Produces: `validate_precursor_feature_record(value: object) -> dict[str, object]`
- Produces: `validate_forecast_experiment_receipt(value: object) -> dict[str, object]`
- Produces: `classify_forecast_skill(*, execution_completed: bool, data_available: bool, mae_improvement: float | None, ci_low: float | None, ci_high: float | None, regime_consistent: bool | None) -> str`
- Consumed by: Tasks 2, 3, 4, and 6.

- [ ] **Step 1: Write failing contract tests**

```python
def test_polar_aperture_cannot_claim_axial_dipole() -> None:
    record = _feature(
        observable_kind="axial_dipole_moment",
        source_dataset_ids=["mwo-wso-polar-field-v2"],
        derivation_method="north/south WSO aperture average",
    )
    with pytest.raises(ValueError, match="axial dipole"):
        validate_precursor_feature_record(record)


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"execution_completed": False, "data_available": True}, "execution_failed"),
        ({"execution_completed": True, "data_available": False}, "blocked_by_data"),
        (
            {
                "execution_completed": True,
                "data_available": True,
                "mae_improvement": 4.0,
                "ci_low": 0.5,
                "ci_high": 8.0,
                "regime_consistent": True,
            },
            "skill_supported",
        ),
        (
            {
                "execution_completed": True,
                "data_available": True,
                "mae_improvement": 4.0,
                "ci_low": -1.0,
                "ci_high": 9.0,
                "regime_consistent": True,
            },
            "mixed_evidence",
        ),
        (
            {
                "execution_completed": True,
                "data_available": True,
                "mae_improvement": -0.1,
                "ci_low": -4.0,
                "ci_high": 3.0,
                "regime_consistent": True,
            },
            "tested_no_skill",
        ),
    ],
)
def test_skill_status_is_deterministic(kwargs, expected) -> None:
    assert classify_forecast_skill(**kwargs) == expected
```

- [ ] **Step 2: Run the new test file and confirm the missing module failure**

Run: `pytest -q tests/test_solar_forecast_contracts.py`

Expected: collection fails with `ModuleNotFoundError: No module named 'jw.solar_forecast'`.

- [ ] **Step 3: Implement exact schemas and the physical source gate**

```python
FEATURE_VERSION = "solar-precursor-feature-record-v1"
EXPERIMENT_VERSION = "solar-forecast-experiment-receipt-v1"
OBSERVABLE_KINDS = {
    "sunspot_rise_metric",
    "polar_aperture_field",
    "hemispheric_polar_flux",
    "axial_dipole_moment",
}
AXIAL_ALLOWED_SOURCE_KINDS = {"registered_axial_dipole", "synoptic_map_harmonic"}


def classify_forecast_skill(
    *,
    execution_completed: bool,
    data_available: bool,
    mae_improvement: float | None = None,
    ci_low: float | None = None,
    ci_high: float | None = None,
    regime_consistent: bool | None = None,
) -> str:
    if not execution_completed:
        return "execution_failed"
    if not data_available:
        return "blocked_by_data"
    if None in {mae_improvement, ci_low, ci_high, regime_consistent}:
        raise ValueError("completed forecast classification requires finite metrics")
    if mae_improvement <= 0:
        return "tested_no_skill"
    if ci_low > 0 and regime_consistent is True:
        return "skill_supported"
    return "mixed_evidence"
```

`validate_precursor_feature_record` must require these semantic fields: `schema_version`, `feature_id`, `hypothesis_id`, `forecast_origin`, `observable_kind`, `physical_quantity`, `unit`, `source_dataset_ids`, `source_artifact_ids`, `observation_start`, `observation_end`, `available_at`, `cycle_id`, `target_cycle_id`, `value`, `uncertainty`, `measurement_regime`, `derivation_method`, `source_kind`, and `status`; `data_gap` is required only for `blocked_by_data` and forbidden otherwise. For `axial_dipole_moment`, reject any available record whose `source_kind` is outside `AXIAL_ALLOWED_SOURCE_KINDS`; for `blocked_by_data`, require `value is None` and a non-empty `data_gap`.

`validate_forecast_experiment_receipt` must require the experiment status, forecast origin, ordered training/test cycles, feature IDs, baseline and candidate names, per-fold predictions, MAE/RMSE, paired bootstrap interval, measurement-regime result, and leakage audit. Reject a completed receipt if any fold uses a training cycle greater than or equal to its test cycle.

- [ ] **Step 4: Export the three interfaces and run tests**

```python
from .contracts import (
    classify_forecast_skill,
    validate_forecast_experiment_receipt,
    validate_precursor_feature_record,
)

__all__ = [
    "classify_forecast_skill",
    "validate_forecast_experiment_receipt",
    "validate_precursor_feature_record",
]
```

Run: `pytest -q tests/test_solar_forecast_contracts.py`

Expected: all tests pass.

- [ ] **Step 5: Commit the focused contract change**

```bash
git add jw/solar_forecast/__init__.py jw/solar_forecast/contracts.py tests/test_solar_forecast_contracts.py
git commit -m "feat(solar): add typed precursor and forecast receipts"
```

---

### Task 2: Emit real H2 feature lineage and honest H3 data readiness

**Files:**
- Modify: `jw/tools/solar_feature.py:1983-2224`
- Modify: `tests/test_solar_data_catalog.py:500-680`
- Modify: `tests/test_solar_data_harness.py:580-790`

**Interfaces:**
- Consumes: `validate_precursor_feature_record` from Task 1.
- Produces: `receipt["feature_records"]` for H2 rows.
- Produces: `receipt["unavailable_feature_records"]` with one H3 `blocked_by_data` record when no eligible axial-dipole product exists.
- Consumed by: Task 3's Automatic Experiment input inspection and Task 6's Evidence projection.

- [ ] **Step 1: Extend the current precursor receipt tests**

```python
records = receipt["feature_records"]
assert len(records) == 10
assert {row["observable_kind"] for row in records} == {"polar_aperture_field"}
assert all(row["source_dataset_ids"] == ["mwo-wso-polar-field-v2"] for row in records)
assert all(row["available_at"] <= row["forecast_origin"] for row in records)

blocked = receipt["unavailable_feature_records"]
assert blocked == [
    {
        **blocked[0],
        "hypothesis_id": "h3_axial_dipole_discriminator",
        "observable_kind": "axial_dipole_moment",
        "status": "blocked_by_data",
        "value": None,
        "data_gap": "NO_REGISTERED_AXIAL_DIPOLE_OR_SYNOPTIC_MAP_INPUT",
    }
]
```

Add a harness assertion that the Data artifact continues with verified H2 rows even though the independent H3 record is blocked; H3 absence must not invalidate H2.

- [ ] **Step 2: Run the focused tests and observe missing receipt fields**

Run: `pytest -q tests/test_solar_data_catalog.py -k 'precursor' tests/test_solar_data_harness.py -k 'polar_precursor'`

Expected: failures reference missing `feature_records` and `unavailable_feature_records`.

- [ ] **Step 3: Convert each analysis row into a validated H2 record**

```python
feature_records = []
for row in rows:
    if row["row_role"] != "analysis":
        continue
    record = validate_precursor_feature_record(
        {
            "schema_version": "solar-precursor-feature-record-v1",
            "feature_id": f"polar-minimum-cycle-{row['cycle_number']}",
            "hypothesis_id": "h2_polar_precursor",
            "forecast_origin": str(row["predictor_window_end_decimal_year"]),
            "observable_kind": "polar_aperture_field",
            "physical_quantity": "mean absolute north/south calibrated polar field",
            "unit": "gauss",
            "source_dataset_ids": ["mwo-wso-polar-field-v2"],
            "source_artifact_ids": [table_ref, metadata_ref],
            "observation_start": str(row["predictor_window_start_decimal_year"]),
            "observation_end": str(row["predictor_window_end_decimal_year"]),
            "available_at": str(row["predictor_cutoff_decimal_year"]),
            "cycle_id": int(row["cycle_number"]) - 1,
            "target_cycle_id": int(row["cycle_number"]),
            "value": float(row["polar_field_proxy_gauss"]),
            "uncertainty": row["polar_field_proxy_sem_gauss"],
            "measurement_regime": "+".join(
                sorted({str(row["north_source"]), str(row["south_source"])})
            ),
            "derivation_method": "mean absolute calibrated north/south polar aperture field",
            "source_kind": "polar_aperture_observation",
            "status": "available",
        }
    )
    feature_records.append(record)
```

Build one H3 record through the same validator with `source_kind="missing"`, `status="blocked_by_data"`, `value=None`, and `data_gap="NO_REGISTERED_AXIAL_DIPOLE_OR_SYNOPTIC_MAP_INPUT"`. Do not derive it from the H2 values.

- [ ] **Step 4: Persist records in both dataset receipt and tool response**

Add `feature_records` and `unavailable_feature_records` to `solar-precursor-cycle-table-v2`; return their counts and status in the bounded tool response so the Data Agent does not need to reread arbitrary files.

- [ ] **Step 5: Run focused Data tests**

Run: `pytest -q tests/test_solar_data_catalog.py -k 'precursor' tests/test_solar_data_harness.py -k 'polar_precursor'`

Expected: all selected tests pass and the old row/uncertainty assertions remain unchanged.

- [ ] **Step 6: Commit H2/H3 data semantics**

```bash
git add jw/tools/solar_feature.py tests/test_solar_data_catalog.py tests/test_solar_data_harness.py
git commit -m "feat(solar): bind polar precursor feature lineage"
```

---

### Task 3: Pre-registered rolling-origin polar forecast tournament

**Files:**
- Create: `jw/solar_forecast/backtest.py`
- Create: `tests/test_solar_precursor_backtest.py`
- Modify: `jw/research_protocols.py:399-423`
- Modify: `tests/test_research_protocols.py:230-250`
- Modify: `jw/tools/automatic_experiment.py:539-959,1541-1688,1860-1875`
- Modify: `jw/subagents/solar/solar_experiment.yaml`
- Modify: `tests/test_solar_contract_prompts.py`

**Interfaces:**
- Consumes: H2 feature rows and feature records from Task 2.
- Produces: `run_precursor_backtest(rows: Sequence[Mapping[str, object]], *, seed: int = 20260828, bootstrap_resamples: int = 10_000) -> dict[str, object]`.
- Produces: `automatic_experiment_create_polar_forecast_design` and `automatic_experiment_prepare_polar_forecast_attempt`.
- Produces: one validated `ForecastExperimentReceiptV1` plus `rolling_predictions.csv` and `bootstrap_mae_improvement.csv`.

- [ ] **Step 1: Write rolling-origin tests before implementation**

```python
def test_each_fold_uses_only_earlier_cycles() -> None:
    result = run_precursor_backtest(_ten_cycle_fixture())
    for fold in result["folds"]:
        assert max(fold["training_cycles"]) < fold["test_cycle"]


def test_training_mean_and_persistence_are_recomputed_per_fold() -> None:
    result = run_precursor_backtest(_ten_cycle_fixture())
    first = result["folds"][0]
    train_targets = [row["target"] for row in _ten_cycle_fixture()[:5]]
    assert first["training_mean_prediction"] == pytest.approx(np.mean(train_targets))
    assert first["persistence_prediction"] == pytest.approx(train_targets[-1])


def test_axial_comparison_refuses_polar_aperture_values() -> None:
    with pytest.raises(ValueError, match="axial_dipole_moment"):
        run_precursor_backtest(
            _ten_cycle_fixture(),
            discriminator_rows=_polar_rows_mislabeled_as_axial(),
        )


def test_fixed_seed_reproduces_bootstrap_interval() -> None:
    first = run_precursor_backtest(_ten_cycle_fixture(), seed=20260828)
    second = run_precursor_backtest(_ten_cycle_fixture(), seed=20260828)
    assert first["metrics"]["mae_improvement_interval"] == second["metrics"]["mae_improvement_interval"]


def test_mae_rmse_and_leave_one_fold_are_recomputed() -> None:
    result = run_precursor_backtest(_ten_cycle_fixture())
    observed = np.asarray([fold["observed"] for fold in result["folds"]])
    predicted = np.asarray([fold["candidate_prediction"] for fold in result["folds"]])
    assert result["metrics"]["candidate_mae"] == pytest.approx(np.mean(np.abs(observed - predicted)))
    assert result["metrics"]["candidate_rmse"] == pytest.approx(np.sqrt(np.mean((observed - predicted) ** 2)))
    assert len(result["sensitivity"]["leave_one_fold"]) == len(result["folds"])


def test_measurement_regime_sign_is_reported_separately() -> None:
    result = run_precursor_backtest(_ten_cycle_fixture_with_mwo_and_wso_tests())
    assert set(result["sensitivity"]["measurement_regimes"]) == {"MWO", "WSO"}
    assert isinstance(result["sensitivity"]["regime_consistent"], bool)
```

- [ ] **Step 2: Run the new tests and confirm import failure**

Run: `pytest -q tests/test_solar_precursor_backtest.py`

Expected: `ModuleNotFoundError` for `jw.solar_forecast.backtest`.

- [ ] **Step 3: Implement the low-dimensional tournament**

```python
def _fit_line(train_x: np.ndarray, train_y: np.ndarray, test_x: float) -> float:
    design = np.column_stack([np.ones(len(train_x)), train_x])
    intercept, slope = np.linalg.lstsq(design, train_y, rcond=None)[0]
    return float(intercept + slope * test_x)


def run_precursor_backtest(rows, *, seed=20260828, bootstrap_resamples=10_000):
    ordered = sorted(rows, key=lambda row: int(row["target_cycle_id"]))
    if len(ordered) < 7:
        raise ValueError("polar precursor backtest requires at least seven ordered cycles")
    folds = []
    for test_index in range(5, len(ordered)):
        train, test = ordered[:test_index], ordered[test_index]
        train_x = np.asarray([float(row["value"]) for row in train])
        train_y = np.asarray([float(row["target"]) for row in train])
        prediction = _fit_line(train_x, train_y, float(test["value"]))
        folds.append(
            {
                "training_cycles": [int(row["target_cycle_id"]) for row in train],
                "test_cycle": int(test["target_cycle_id"]),
                "observed": float(test["target"]),
                "candidate_prediction": prediction,
                "training_mean_prediction": float(np.mean(train_y)),
                "persistence_prediction": float(train_y[-1]),
                "measurement_regime": str(test["measurement_regime"]),
            }
        )
    return _summarize_and_validate(folds, seed, bootstrap_resamples)
```

`_summarize_and_validate` must compute candidate, mean-baseline, and persistence errors; pair candidate errors with the primary training-mean errors; bootstrap whole fold indices; run leave-one-fold summaries; mark `regime_consistent` only when every pre-registered regime with at least two test folds has the same MAE-improvement sign; call `classify_forecast_skill`; and return a validated forecast receipt. It must never select a model based on held-out fold performance.

- [ ] **Step 4: Add the fixed protocol directive**

Update `solar_polar_precursor_directive()` to require the existing verified table, `SolarPrecursorFeatureRecordV1`, training mean and persistence baselines, five initial training cycles, chronological folds, 10,000 fixed-seed bootstrap resamples, MWO/WSO sensitivity, and a `blocked_by_data` H3 branch when no axial record is available. Remove the old instruction that asks the model to explore either interaction sign; that experiment is now archived, not the main route.

- [ ] **Step 5: Add host-created Automatic Experiment design and worker**

Follow the existing SC26/morphology host-design pattern. The design has one stage, consumes only `solar_precursor_cycle_features.csv` and its receipt, runs the Task 3 algorithm in the immutable sandbox, and requires these artifacts:

```python
POLAR_FORECAST_OUTPUTS = [
    "forecast_experiment_receipt.json",
    "rolling_predictions.csv",
    "bootstrap_mae_improvement.csv",
]
```

Expose `automatic_experiment_create_polar_forecast_design` and `automatic_experiment_prepare_polar_forecast_attempt` in `AUTOMATIC_EXPERIMENT_TOOLS`. The Solar Experiment prompt must call those exact tools for `solar_polar_precursor_v1` and must not submit a free-form worker.

- [ ] **Step 6: Run protocol, prompt, and backtest tests**

Run: `pytest -q tests/test_solar_precursor_backtest.py tests/test_research_protocols.py -k 'polar_precursor' tests/test_solar_contract_prompts.py -k 'polar'`

Expected: all selected tests pass.

- [ ] **Step 7: Commit the deterministic tournament**

```bash
git add jw/solar_forecast/backtest.py tests/test_solar_precursor_backtest.py jw/research_protocols.py tests/test_research_protocols.py jw/tools/automatic_experiment.py jw/subagents/solar/solar_experiment.yaml tests/test_solar_contract_prompts.py
git commit -m "feat(solar): add rolling-origin polar forecast tournament"
```

---

### Task 4: Portfolio roles, lifecycle, and Top-3 release gates

**Files:**
- Modify: `src/scientific_hypothesis/ranking.py:23-32,414-849`
- Modify: `tests/test_hypothesis_portfolio_ranking.py`
- Modify: `jw/tools/scientific_hypothesis.py:1817-1915,2885-2960`
- Modify: `tests/test_scientific_hypothesis_working_state.py`

**Interfaces:**
- Consumes: forecast statuses from Task 3.
- Produces optional normalized row fields `portfolio_role`, `portfolio_status`, `forecast_origin`, and `forecast_receipt_ref` in the existing v2 ranking envelope.
- Produces `active_top3`, `candidate_pending_test`, `challenger_pool`, `rejected`, and `blocked_by_data` lifecycle partitions.
- Consumed by: A2A and WebUI in Task 5.

- [ ] **Step 1: Add failing lifecycle tests to the current ranking fixture**

```python
def test_tested_no_skill_cannot_remain_active_top3() -> None:
    payload = _payload()
    row = payload["ranked_hypotheses"][0]
    row.update(
        portfolio_role="physical_precursor",
        portfolio_status="active_top3",
        forecast_origin="cycle_minimum",
    )
    row["out_of_sample_validation"]["status"] = "tested_no_skill"
    with pytest.raises(ContractError, match="active_top3"):
        validate_portfolio_ranking(payload, _register())


def test_blocked_axial_candidate_stays_visible_but_not_active() -> None:
    payload = _payload()
    row = payload["ranked_hypotheses"][1]
    row.update(
        portfolio_role="physical_discriminator",
        portfolio_status="blocked_by_data",
        forecast_origin="cycle_minimum",
        forecast_receipt_ref=None,
    )
    ranking = validate_portfolio_ranking(payload, _register())
    assert ranking["ranked_hypotheses"][1]["portfolio_status"] == "blocked_by_data"


def test_active_top3_is_bounded_and_roles_are_unique() -> None:
    payload = _payload_with_four_active_rows()
    with pytest.raises(ContractError, match="最多三个"):
        validate_portfolio_ranking(payload, _register())
    payload = _payload_with_duplicate_active_role()
    with pytest.raises(ContractError, match="portfolio_role"):
        validate_portfolio_ranking(payload, _register())


@pytest.mark.parametrize(
    ("role", "origin"),
    [("empirical_anchor", "cycle_minimum"), ("physical_precursor", "early_cycle")],
)
def test_role_rejects_wrong_forecast_origin(role, origin) -> None:
    payload = _payload_with_one_lifecycle_row(role=role, origin=origin)
    with pytest.raises(ContractError, match="forecast_origin"):
        validate_portfolio_ranking(payload, _register())


def test_axial_role_rejects_polar_aperture_receipt() -> None:
    payload = _payload_with_axial_role_and_polar_receipt()
    with pytest.raises(ContractError, match="axial_dipole_moment"):
        validate_portfolio_ranking(payload, _register())
```

- [ ] **Step 2: Run ranking tests and confirm missing-field behavior**

Run: `pytest -q tests/test_hypothesis_portfolio_ranking.py`

Expected: new lifecycle tests fail while existing support/priority tests remain green.

- [ ] **Step 3: Add additive lifecycle parsing and gates**

```python
PORTFOLIO_ROLES = {
    "empirical_anchor",
    "physical_precursor",
    "physical_discriminator",
    "challenger",
}
PORTFOLIO_STATUSES = {
    "candidate_pending_test",
    "active_top3",
    "challenger_pool",
    "rejected",
    "blocked_by_data",
}
FORECAST_ORIGINS = {"early_cycle", "cycle_minimum", "not_applicable"}
```

Keep `scientific-hypothesis-portfolio-ranking-v2` readable. When old persisted rows omit the four additive fields, normalize them to `challenger`, `challenger_pool`, `not_applicable`, and `None`; new tool prompts must provide explicit values. Reject `active_top3` when out-of-sample status is `tested_no_skill`, evidence status is `unsupported`, the portfolio status conflicts with data availability, or the bound receipt's observable kind conflicts with the role.

- [ ] **Step 4: Persist ranking validation failures**

Change the tool failure path to:

```python
except Exception as exc:
    return _needs_revision(exc, state=state, count_failure=True)
```

Assert that `last_validation_error`, `same_validation_error_count`, and the working-state file update after a rejected ranking, while the last valid ranking remains unchanged.

- [ ] **Step 5: Update the ranking contract exposed to the model**

Add the four fields, role/origin compatibility, and lifecycle definitions to `portfolio_ranking_contract`. State explicitly that the archived negative interaction is `rejected`; a high-priority null may remain in `challenger_pool`, but not `active_top3` after `tested_no_skill`.

- [ ] **Step 6: Run hypothesis tests**

Run: `pytest -q tests/test_hypothesis_portfolio_ranking.py tests/test_scientific_hypothesis_working_state.py -k 'portfolio or ranking'`

Expected: all selected tests pass.

- [ ] **Step 7: Commit lifecycle gates**

```bash
git add src/scientific_hypothesis/ranking.py tests/test_hypothesis_portfolio_ranking.py jw/tools/scientific_hypothesis.py tests/test_scientific_hypothesis_working_state.py
git commit -m "feat(hypothesis): gate active solar portfolio by evidence"
```

---

### Task 5: Carry role and receipt evidence through A2A and WebUI

**Files:**
- Modify: `jw/middleware/research_review_orchestration.py:2078-2304`
- Modify: `tests/test_a2a_handoff.py`
- Modify: `webui/src/app/api/research-review/status/route.ts:100-166,400-420`
- Modify: `webui/src/app/components/ResearchReviewPanel.tsx:40-100,257-322`
- Create: `webui/src/lib/researchPortfolio.js`
- Create: `webui/test/research-portfolio.test.js`

**Interfaces:**
- Consumes: Task 4 normalized portfolio rows and Task 3 receipt references.
- Produces: bounded A2A fields `portfolioRole`, `portfolioStatus`, `forecastOrigin`, and `forecastReceiptRef`.
- Produces: reader UI labels without exposing internal hashes or schema names.

- [ ] **Step 1: Add A2A projection tests**

```python
assert capsule["ranked_hypotheses"][0] == {
    **capsule["ranked_hypotheses"][0],
    "portfolio_role": "empirical_anchor",
    "portfolio_status": "active_top3",
    "forecast_origin": "early_cycle",
    "forecast_receipt_ref": "experiment/runs/run-1/forecast_experiment_receipt.json",
}


def test_capsule_drops_absolute_forecast_receipt_path() -> None:
    ranking = _ranking(forecast_receipt_ref="/tmp/private/receipt.json")
    capsule = _portfolio_ranking_capsule_projection(ranking)
    assert capsule["ranked_hypotheses"][0]["forecast_receipt_ref"] is None


def test_stale_ranking_is_not_loaded(tmp_path) -> None:
    _write_ranking_state(tmp_path, pool_sha="a" * 64, tail_pool_sha="b" * 64)
    assert _load_portfolio_ranking_capsule(tmp_path) is None
```

- [ ] **Step 2: Run the A2A tests and confirm missing fields**

Run: `pytest -q tests/test_a2a_handoff.py -k 'portfolio'`

Expected: assertions fail for missing role/lifecycle/origin fields.

- [ ] **Step 3: Extend the minimal capsule**

Project only the four new bounded fields in addition to the existing support and priority summaries. Do not project per-fold arrays, internal hashes, or full evidence records; downstream stages resolve the validated `forecast_receipt_ref` when they need metrics.

- [ ] **Step 4: Add status-route and component tests**

The route must map statuses to stable UI values and return `execution` separately from `status`. Put the pure label maps and `describePortfolioSummary(rows)` in `webui/src/lib/researchPortfolio.js`; test that the function counts only `active_top3` rows as active. The component must show labels such as “经验锚点 / 现役”, “物理前兆 / 待检验”, “物理判别 / 数据阻断”, and separate “早期周期信息” from “极小期前兆”. It must not render a blocked or rejected item as one of “三个已达标假设”.

- [ ] **Step 5: Implement the bounded TypeScript projection**

```ts
return {
  statement: groups.get(hypothesisId) ?? "未命名假设",
  supportRank: safeNumber(row.support_rank),
  researchPriorityRank: safeNumber(row.research_priority_rank),
  portfolioRole: safeString(row.portfolio_role),
  portfolioStatus: safeString(row.portfolio_status),
  forecastOrigin: safeString(row.forecast_origin),
  forecastReceiptRef: safeRelativePath(row.forecast_receipt_ref),
  claimType: safeString(row.claim_type),
  scientificSupport: boundedAssessment(row.scientific_support),
  researchPriority: boundedAssessment(row.research_priority),
  strongestNull: safeString(row.strongest_null_hypothesis).slice(0, 1_000),
  nextExperiment: boundedNextExperiment(row.next_experiment),
  releaseBoundary: safeString(row.release_boundary).slice(0, 1_000),
};
```

- [ ] **Step 6: Run Python and WebUI tests**

Run: `pytest -q tests/test_a2a_handoff.py -k 'portfolio'`

Run: `cd webui && npm test`

Expected: selected Python tests and all WebUI tests pass.

- [ ] **Step 7: Commit A2A and UI lifecycle visibility**

```bash
git add jw/middleware/research_review_orchestration.py tests/test_a2a_handoff.py webui/src/app/api/research-review/status/route.ts webui/src/app/components/ResearchReviewPanel.tsx webui/src/lib/researchPortfolio.js webui/test/research-portfolio.test.js
git commit -m "feat(webui): show solar hypothesis evidence lifecycle"
```

---

### Task 6: Make Solar Agents and Final Release consume receipts, not prose

**Files:**
- Modify: `jw/subagents/solar/solar_data.yaml:9-31`
- Modify: `jw/subagents/solar/solar_hypothesis.yaml:84-92`
- Modify: `jw/subagents/solar/solar_experiment.yaml`
- Modify: `jw/subagents/solar/solar_evidence.yaml`
- Modify: `src/research_review/adapters.py:900-930,1150-1260`
- Modify: `tests/test_solar_contract_prompts.py`
- Modify: `tests/test_research_review_v2.py`

**Interfaces:**
- Consumes: Tasks 2–5 receipts and lifecycle fields.
- Produces: `project_forecast_claim_from_receipt(prose_claim: str, receipt: Mapping[str, object]) -> dict[str, object]` in `src/research_review/adapters.py`.
- Produces: Integration capsule with feature-use proof, forecast origin, baseline result, uncertainty status, and H3 data readiness.
- Produces: Final Release answer contract for H1/H2/H3.

- [ ] **Step 1: Add prompt and adapter tests**

```python
assert "polar_aperture_field" in hypothesis_prompt
assert "axial_dipole_moment" in hypothesis_prompt
assert "不得把 WSO 极区孔径场改名为轴向偶极矩" in hypothesis_prompt
assert "candidate_pending_test" in hypothesis_prompt
assert "tested_no_skill" in hypothesis_prompt
assert "预测时点" in final_release_prompt


def test_axial_prose_is_rejected_when_receipt_contains_only_polar_aperture() -> None:
    receipt = _forecast_receipt(observable_kinds=["polar_aperture_field"])
    with pytest.raises(ValueError, match="axial_dipole_moment"):
        project_forecast_claim_from_receipt("轴向偶极矩预测更稳定", receipt)
```

- [ ] **Step 2: Run the prompt and adapter tests**

Run: `pytest -q tests/test_solar_contract_prompts.py tests/test_research_review_v2.py -k 'polar or portfolio or forecast_origin'`

Expected: new assertions fail before the prompt and adapter changes.

- [ ] **Step 3: Update role-specific instructions**

Data must return the typed H2 record and separate H3 data status. Hypothesis must create the three agreed roles and put untested H2/H3 in `candidate_pending_test`. Experiment must use the host-created tournament. Evidence must recompute MAE improvement, bootstrap interval, regime sign, and feature kind from the receipt. Integration must not compare H1 and H2/H3 as if they shared a forecast origin.

- [ ] **Step 4: Add deterministic claim projection**

```python
forecast_summary = {
    "hypothesis_id": receipt["hypothesis_id"],
    "forecast_origin": receipt["forecast_origin"],
    "feature_ids": list(receipt["feature_ids"]),
    "observable_kinds": sorted(set(receipt["observable_kinds"])),
    "candidate_mae": receipt["metrics"]["candidate_mae"],
    "baseline_mae": receipt["metrics"]["training_mean_mae"],
    "mae_improvement": receipt["metrics"]["mae_improvement"],
    "mae_improvement_interval": receipt["metrics"]["mae_improvement_interval"],
    "skill_status": receipt["skill_status"],
}
```

Adapters must derive this summary from the validated receipt. They may not accept equivalent numbers from model prose.

- [ ] **Step 5: Enforce the final answer order**

The reader answer states target and forecast origin first, then available data, point/interval only if the relevant forecast origin is reached, historical baseline skill, failure cycles, H2/H3 comparison, and limitations. For the current SC26 state, H2/H3 must say the formal minimum-near precursor origin has not yet been reached unless a future registered run proves otherwise.

- [ ] **Step 6: Run focused integration tests**

Run: `pytest -q tests/test_solar_contract_prompts.py tests/test_research_review_v2.py -k 'polar or portfolio or forecast_origin'`

Expected: all selected tests pass.

- [ ] **Step 7: Commit receipt-backed scientific synthesis**

```bash
git add jw/subagents/solar/solar_data.yaml jw/subagents/solar/solar_hypothesis.yaml jw/subagents/solar/solar_experiment.yaml jw/subagents/solar/solar_evidence.yaml src/research_review/adapters.py tests/test_solar_contract_prompts.py tests/test_research_review_v2.py
git commit -m "feat(research): ground solar answers in forecast receipts"
```

---

### Task 7: Separate execution liveness and reject invalid model overrides

**Files:**
- Create: `src/research_review/execution_state.py`
- Create: `tests/test_research_execution_state.py`
- Modify: `jw/research_review.py:391-560`
- Modify: `jw/middleware/research_review_orchestration.py:2580-2725,3170-3205`
- Modify: `jw/middleware/configurable_model.py:45-220`
- Modify: `jw/llm/models.py:30-70,177-340`
- Modify: `tests/test_qwen_compat_middleware.py`
- Modify: `webui/src/app/api/research-review/status/route.ts:250-420`

**Interfaces:**
- Produces: `ExecutionStateStore(path: Path)` with `start`, `progress`, `waiting_for_tool`, `interrupt`, `fail`, and `stop`.
- Produces: `validate_model_override(model: str, provider: str | None) -> tuple[str, str | None]`.
- Consumed by: orchestration and WebUI status route.

- [ ] **Step 1: Write execution-state tests**

```python
def test_stale_heartbeat_does_not_change_scientific_status(tmp_path) -> None:
    store = ExecutionStateStore(tmp_path / "execution_state.json")
    store.start(stage="data", owner="solar-data", now="2026-08-28T00:00:00+00:00")
    snapshot = store.snapshot(now="2026-08-28T00:10:00+00:00", stale_after_seconds=60)
    assert snapshot["status"] == "stopped"
    assert snapshot["reason"] == "heartbeat_stale"


def test_invalid_provider_is_rejected_before_model_resolution() -> None:
    with pytest.raises(ValueError, match="unsupported model provider"):
        validate_model_override("qwen3.8-max", "qwen")


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        ("waiting_for_tool", "waiting_for_tool"),
        ("interrupt", "interrupted"),
        ("fail", "failed"),
        ("stop", "stopped"),
    ],
)
def test_explicit_execution_transitions_persist(tmp_path, method, expected) -> None:
    store = ExecutionStateStore(tmp_path / "execution_state.json")
    store.start(stage="data", owner="solar-data", now="2026-08-28T00:00:00+00:00")
    getattr(store, method)(
        stage="data",
        owner="solar-data",
        reason="test_reason",
        now="2026-08-28T00:00:01+00:00",
    )
    assert store.snapshot()["status"] == expected


def test_execution_state_uses_atomic_replace(monkeypatch, tmp_path) -> None:
    calls = []
    real_replace = execution_state.os.replace

    def observed_replace(source, destination):
        calls.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(execution_state.os, "replace", observed_replace)
    target = tmp_path / "execution_state.json"
    ExecutionStateStore(target).start(stage="data", owner="solar-data")
    assert len(calls) == 1
    assert calls[0][1] == target
    assert calls[0][0].name.endswith(".tmp")


def test_repeated_progress_preserves_start_time(tmp_path) -> None:
    store = ExecutionStateStore(tmp_path / "execution_state.json")
    first = store.progress(
        stage="data", owner="solar-data", action="inspect", now="2026-08-28T00:00:00+00:00"
    )
    second = store.progress(
        stage="data", owner="solar-data", action="inspect", now="2026-08-28T00:00:05+00:00"
    )
    assert second["started_at"] == first["started_at"]
```

- [ ] **Step 2: Run focused tests and confirm missing interfaces**

Run: `pytest -q tests/test_research_execution_state.py tests/test_qwen_compat_middleware.py -k 'provider or execution_state or heartbeat'`

Expected: new tests fail because the interfaces do not exist.

- [ ] **Step 3: Implement the sidecar contract**

```python
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

EXECUTION_VERSION = "research-execution-state-v1"
EXECUTION_STATUSES = {
    "running",
    "waiting_for_tool",
    "interrupted",
    "failed",
    "stopped",
}


class ExecutionStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> dict[str, object] | None:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _transition(self, *, status, stage, owner, action, reason, now=None):
        if status not in EXECUTION_STATUSES:
            raise ValueError(f"invalid execution status: {status}")
        timestamp = now or datetime.now(UTC).isoformat()
        previous = self._read() or {}
        record = {
            "schema_version": EXECUTION_VERSION,
            "status": status,
            "stage": stage,
            "owner": owner,
            "action": action,
            "reason": reason,
            "started_at": previous.get("started_at", timestamp),
            "updated_at": timestamp,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)
        return record

    def start(self, *, stage, owner, now=None):
        return self._transition(status="running", stage=stage, owner=owner, action="start", reason=None, now=now)

    def progress(self, *, stage, owner, action, now=None):
        return self._transition(status="running", stage=stage, owner=owner, action=action, reason=None, now=now)

    def waiting_for_tool(self, *, stage, owner, reason, now=None):
        return self._transition(status="waiting_for_tool", stage=stage, owner=owner, action="tool", reason=reason, now=now)

    def interrupt(self, *, stage, owner, reason, now=None):
        return self._transition(status="interrupted", stage=stage, owner=owner, action="interrupt", reason=reason, now=now)

    def fail(self, *, stage, owner, reason, now=None):
        return self._transition(status="failed", stage=stage, owner=owner, action="fail", reason=reason, now=now)

    def stop(self, *, stage, owner, reason, now=None):
        return self._transition(status="stopped", stage=stage, owner=owner, action="stop", reason=reason, now=now)

    def snapshot(self, *, now=None, stale_after_seconds=300):
        record = self._read()
        if record is None:
            return None
        if record["status"] not in {"running", "waiting_for_tool"}:
            return record
        observed = datetime.fromisoformat(str(record["updated_at"]))
        current = datetime.fromisoformat(now) if now else datetime.now(UTC)
        if (current - observed).total_seconds() <= stale_after_seconds:
            return record
        return {**record, "status": "stopped", "reason": "heartbeat_stale"}
```

The complete record is `schema_version`, `status`, `stage`, `owner`, `action`, `reason`, `started_at`, and `updated_at`. `snapshot` may project stale running/waiting records as `stopped/heartbeat_stale` but must not rewrite `research_review/run_state.json`.

- [ ] **Step 4: Wire real progress and failure transitions**

Create the sidecar in `ResearchReviewStore`. Update it when an action is reserved, before/after a required tool call, on interruption, and when the existing terminal failure receipt is written. Do not update it from UI polling.

- [ ] **Step 5: Validate overrides before fallback logic**

```python
# jw/llm/models.py, after _MODEL_ENTRIES is defined
SUPPORTED_MODEL_PROVIDERS = frozenset(
    {provider for _name, _model_id, provider in _MODEL_ENTRIES}
    | {"openai", "anthropic", "google-genai", "ollama"}
)


def validate_model_override(model: str, provider: str | None):
    normalized_model = model.strip()
    normalized_provider = provider.strip() if isinstance(provider, str) else None
    if not normalized_model:
        raise ValueError("model override must be non-empty")
    if normalized_provider and normalized_provider not in SUPPORTED_MODEL_PROVIDERS:
        raise ValueError(f"unsupported model provider: {normalized_provider}")
    return normalized_model, normalized_provider
```

Call it before `_resolve`. Remove the non-Qwen silent compile-time fallback for an explicitly requested invalid pair; explicit overrides either resolve or raise. Absence of an override remains pass-through.

- [ ] **Step 6: Project execution status in WebUI**

Read `research_review/execution_state.json` separately. Return `execution.status`, `execution.stage`, `execution.updatedAt`, and a bounded reason label. Do not change the scientific `status` field when the heartbeat is stale.

- [ ] **Step 7: Run execution and middleware tests**

Run: `pytest -q tests/test_research_execution_state.py tests/test_qwen_compat_middleware.py -k 'provider or execution_state or heartbeat'`

Run: `pytest -q tests/test_research_review_v2.py -k 'tool_failure or run_state'`

Expected: all selected tests pass.

- [ ] **Step 8: Commit reliability boundaries**

```bash
git add src/research_review/execution_state.py tests/test_research_execution_state.py jw/research_review.py jw/middleware/research_review_orchestration.py jw/middleware/configurable_model.py jw/llm/models.py tests/test_qwen_compat_middleware.py webui/src/app/api/research-review/status/route.ts
git commit -m "fix(runtime): separate scientific state from execution liveness"
```

---

### Task 8: Regression, real scientific run, and reader report replacement

**Files:**
- Create: `docs/jw_solar_hypothesis_forecast_upgrade_session_log_20260828.md`
- Create after prompt freeze: `research/review/evals/jw_solar_forecast_behavior_cases_v1.json`
- Modify after real evidence exists: `/mnt/c/Users/12167/OneDrive/桌面/jwagent三个最优假设.md`
- Modify only if current-state evidence is refreshed: `../CURRENT_STATE.md`
- Use run artifacts under: `research/review/evals/runs/`

**Interfaces:**
- Consumes: all previous tasks.
- Produces: automated verification, deterministic H2 backtest artifacts, H3 data verdict, fresh headed WebUI evidence, and the replacement reader document.

- [ ] **Step 1: Run formatting and focused regression**

Run:

```bash
ruff format --check jw/solar_forecast src/research_review tests/test_solar_forecast_contracts.py tests/test_solar_precursor_backtest.py tests/test_research_execution_state.py
ruff check jw/solar_forecast src/research_review jw/tools/solar_feature.py jw/tools/automatic_experiment.py src/scientific_hypothesis/ranking.py
pytest -q tests/test_solar_forecast_contracts.py tests/test_solar_precursor_backtest.py tests/test_solar_data_catalog.py tests/test_solar_data_harness.py tests/test_research_protocols.py tests/test_hypothesis_portfolio_ranking.py tests/test_scientific_hypothesis_working_state.py tests/test_a2a_handoff.py tests/test_solar_contract_prompts.py tests/test_research_execution_state.py
```

Expected: format and lint exit zero; all selected tests pass.

- [ ] **Step 2: Run broader Python and WebUI regression**

Run: `pytest -q`

Run: `cd webui && npm test`

Run: `cd webui && npm run build`

Expected: no new failures. Record exact pass/skip/warning counts and any pre-existing build warnings; do not copy historical counts.

- [ ] **Step 3: Acquire only registered authoritative solar inputs**

Run the existing authoritative acquisition entry point in a new run workspace and verify its provenance receipts. Do not place downloaded data in source directories and do not print credentials.

Run:

```bash
PYTHONPATH=. python3 scripts/acquire_authoritative_solar_data.py \
  --workspace /home/zzz/2026tzb/8.20.4/research/review/evals/runs/jw_solar_upgrade_20260828/project_root \
  --project-id default
```

Expected: acquisition completes in the named run workspace, and dataset receipts identify `silso-monthly-total-v2` and `mwo-wso-polar-field-v2` with successful validators.

- [ ] **Step 4: Execute the deterministic H2 tournament**

Start a new `solar_polar_precursor_v1` research run, inspect the Data receipt, execute the host-created polar forecast attempt, and independently recompute its MAE improvement and interval from `rolling_predictions.csv` and `bootstrap_mae_improvement.csv`.

Expected artifacts:

```text
work/solar_data/solar_precursor_cycle_features.csv
receipts/datasets/solar_precursor_cycle_table.json
experiment/runs/<run_id>/forecast_experiment_receipt.json
experiment/runs/<run_id>/rolling_predictions.csv
experiment/runs/<run_id>/bootstrap_mae_improvement.csv
```

Record the actual H2 status; do not require it to be `skill_supported`.

- [ ] **Step 5: Resolve H3 from evidence, not intent**

Inspect the registered inputs for either an axial-dipole data product or synoptic radial-field maps with the required definition. If neither exists, confirm the persisted `blocked_by_data` record and cite the 2026 ApJ result only as external literature support. If a valid product exists, run the same folds and model complexity for axial dipole versus polar field and persist the comparison receipt.

Expected: either a valid discriminator receipt or the exact gap `NO_REGISTERED_AXIAL_DIPOLE_OR_SYNOPTIC_MAP_INPUT`; no derived WSO-aperture surrogate.

- [ ] **Step 6: Run a fresh headed production WebUI session**

Use only the original researcher question. Verify the UI shows H1/H2/H3 roles, separate forecast origins, actual H2 feature use, H3 evidence status, baseline skill, and execution liveness. Save the screenshot, terminal record, model receipts, and final answer in a new run directory.

Expected: terminal state is either scientifically released or honestly blocked with a durable reason; a WebUI `completed` state alone is not acceptance.

- [ ] **Step 7: Run the frozen behavior-evaluation set**

After all prompts and contracts are frozen, write twelve cases to `jw_solar_forecast_behavior_cases_v1.json`: H1 forecast-origin recognition; H2 real-data use; H3 axial-data absence; WSO-versus-dipole distinction; no-skill baseline wording; mixed-evidence wording; rejected interaction handling; current SC26 readiness; measurement-regime sensitivity; interrupted-run recovery; invalid-provider rejection; and final reader-answer completeness. Each case contains only an external user question plus semantic acceptance fields, never internal tool instructions. Execute them through fresh production WebUI tasks and record per-case final status and evidence paths without tuning prompts from these results.

Expected: all safety/lineage assertions pass; scientific support assertions may legitimately return supported, mixed, rejected, or blocked according to their receipts.

- [ ] **Step 8: Write the session log and replace the reader document**

Write the project session log from the actual run evidence. Before overwriting the OneDrive file, preserve the old negative-interaction content in the repository's rejected-hypothesis artifact. Replace the reader document with natural Chinese that reports:

1. H1's verified empirical scope and early-cycle origin;
2. H2's real polar-data backtest and actual skill status;
3. H3's axial-dipole comparison or explicit data blocker;
4. why the old peak-to-peak forecast and negative interaction left the Top-3;
5. uncertainty and the next legitimate update point.

Do not include commit IDs, hashes, internal status enums, or tool traces in the reader document.

- [ ] **Step 9: Final evidence check and commit**

Run: `git diff --check`

Run the focused tests from Step 1 again after documentation generation.

Stage only the implementation, tests, session log, and repository-side scientific artifacts. Do not stage unrelated historical run directories or pre-existing user changes.

```bash
git commit -m "feat: improve JW solar-cycle scientific forecasting"
```

Report separately: code/test result, deterministic H2 result, H3 data/result status, real model/WebUI result, and scientific release boundary.
