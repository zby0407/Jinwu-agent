# Forecast reporting

Lead with what was forecast and whether the result is a historical backtest,
simulated-operational evaluation, or live issuance.

## Required report fields

- spatial unit and target threshold;
- issue cadence and observation/prediction windows;
- evaluation interval and natural event rate;
- source products, versions, retrieval times, and unavailable-data policy;
- split and embargo design;
- model and calibration version;
- climatology, persistence, and simple-model baselines;
- probability and thresholded metrics with uncertainty;
- reliability summary and important failure slices;
- valid range, known blind spots, and retraining/revalidation triggers.

## Wording boundaries

Use “historical forecast skill” only for a leakage-controlled out-of-sample
evaluation. Use “simulated operational” when source latency and model updates
are reconstructed but the forecasts were not actually issued in real time.
Use “live” only when an immutable issue-time record existed before outcomes.

Do not say:

- “predicts solar impact” when only X-ray flare occurrence was modeled;
- “explains the physical mechanism” because a feature ranked highly;
- “well calibrated” from Brier score alone;
- “operationally ready” from one split or one aggregate metric;
- “no flare” when the observation was missing or impaired.

## Artifact separation

Store the reusable definition and experiment paradigm in the Wiki. Store
dataset manifests, model artifacts, calibration objects, issued probabilities,
verification tables, and plots in immutable run artifacts. Promote only
source-backed reusable findings through the normal knowledge review process.
