---
name: solar-flare-forecasting
description: 当 JW 需要研究带明确 issue time 与 forecast window 的 GOES C/M/X 太阳耀斑概率预测、HMI/SHARP 特征、历史回测、稀有事件基线、calibration 或 forecast drift 时使用。
---

# 太阳耀斑预测

使用现有六个专业子 Agent 分工，不创建第七个预测 Agent，也不让单一角色静默完成整个研究闭环。太阳耀斑短期概率预测与太阳活动周振幅预测必须保持不同的目标、时间窗口和验证合同。

## Route work by role

- `solar-planner`: define the forecast task, required evidence, conditional
  route, evaluation rules, and stop conditions. Do not train or issue a forecast.
- `solar-data`: bind sources, construct event labels and predictor snapshots,
  preserve availability timestamps, and audit quality. Do not fit a model.
- `solar-hypothesis`: enter only for a mechanism question. Do not convert
  qualitative hypothesis confidence into a numerical flare probability.
- `solar-experiment`: execute one bounded benchmark, calibration, ablation, or
  simulated-real-time experiment from registered immutable inputs.
- `solar-evidence`: independently compare forecasts with declared baselines and
  audit calibration, discrimination, false alarms, misses, drift, and scope.
- `solar-knowledge`: curate reusable definitions, source semantics, paradigms,
  and verified findings. Never store a daily forecast as canonical knowledge.

## Follow the forecast workflow

### 1. Bind the task before touching data

Read [forecast-task-contract.md](references/forecast-task-contract.md). Freeze
the spatial unit, target threshold, issue time, data cutoff, observation window,
prediction window, and output type. Run:

```bash
python scripts/validate_forecast_contract.py forecast_task.json
```

Stop if the target is only “flare risk,” if the issue time or prediction window
is missing, or if a supposedly live feature was available only after issue time.

### 2. Bind observations and labels

For event semantics, read
[goes-label-semantics.md](references/goes-label-semantics.md). For magnetic
predictors, read
[sharp-feature-semantics.md](references/sharp-feature-semantics.md).

Record source product, version or retrieval timestamp, physical quantity,
quality flags, data latency, identifier mapping, and content hash. Distinguish:

- no event observed inside valid coverage;
- missing or impaired observation;
- unavailable at issue time;
- outside the product's valid range.

Never zero-fill the last three states.

### 3. Construct a leakage-safe forecast table

Read [leakage-and-splitting.md](references/leakage-and-splitting.md). Build one
row per declared forecast instance. Keep labels and future-derived summaries out
of predictors. Fit imputation, normalization, sampling, feature selection,
calibration, and thresholds on training data only.

Run:

```bash
python scripts/validate_forecast_split.py forecast_rows.csv
```

Do not continue to scientific interpretation while group overlap, reverse time
order, or prediction-window contamination remains.

### 4. Benchmark before model expansion

Read [baselines-and-metrics.md](references/baselines-and-metrics.md). Always
include climatology and persistence; add a simple region-classification or
regularized linear baseline when the required inputs exist. Deep learning is a
candidate, never the default qualification baseline.

Probability forecasts require calibration and proper scores. Thresholded event
scores are secondary views at thresholds fixed without the test set.

### 5. Verify and report

Run:

```bash
python scripts/verify_probabilistic_forecast.py forecast_results.csv
```

Then read [forecast-reporting.md](references/forecast-reporting.md). Report the
base rate, baseline-relative skill, calibration, event/non-event tradeoff,
uncertainty, coverage, failure slices, and exact evaluation period. Separate a
research backtest, simulated-operational forecast, and live forecast.

## Use the LLM Wiki

Load the `flare_forecast` task bundle from the Solar-Cycle LLM Wiki. Treat Wiki
entries as definitions, source constraints, and experiment paradigms—not as
empirical proof that a model has skill. Bind real dataset receipts and verified
experiment artifacts for every performance claim.

## Hard stops

- No immutable input snapshot or source receipt.
- No explicit issue time, target threshold, or prediction window.
- Random row splitting of overlapping time windows.
- The same active region appears across model-selection and final-test groups
  without an explicitly justified chronological operational design.
- Test data influence preprocessing, sampling, thresholding, calibration, or
  model choice.
- Accuracy is the sole metric for an imbalanced target.
- Missing observation is encoded as “no flare.”
- A flare probability is presented as a CME, SEP, or geoeffective-impact
  probability.
- A candidate does not beat declared simple baselines or has materially
  unreliable probabilities but is described as operationally qualified.
