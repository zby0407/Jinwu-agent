---
id: "kb_experiment_paradigm_backtest_001"
type: "experiment_paradigm"
title: "Cross-Cycle Backtest for Solar-Cycle Prediction"
source_type: "textbook"
source_ref: "Solar-Cycle Co-Scientist internal paradigm"
confidence: "high"
status: "canonical"
valid_range: "cycle-level feature tables with >=4 cycles"
related_ids: ["kb_concept_sunspot_cycle_001", "kb_mechanism_babcock_leighton_001"]
provenance: {"imported_by": "import_initial", "imported_at": "2026-07-21T12:53:17+00:00"}
version: 1
created_at: "2026-07-21T12:53:17+00:00"
updated_at: "2026-07-21T12:53:17+00:00"
created_by: "import_initial"
---

## design

A cross-cycle backtest evaluates a prediction model by holding out one historical cycle, training on the remaining cycles, and predicting the held-out cycle's peak amplitude (or other target). This is the appropriate validation strategy for solar-cycle data because:

- Cycles are not independent random samples; time-series cross-validation would leak information.
- The goal is to estimate out-of-cycle generalization.

Recommended procedure:
1. Build cycle-level features (length, rise time, minimum, rise slope, precursor proxies).
2. For each cycle i: train on all cycles except i, predict cycle i's peak.
3. Report MAE, RMSE, and per-cycle residuals.
4. Examine whether errors correlate with cycle morphology (e.g., anomalous rise time).

Caveats:
- Small sample size leads to high variance in error estimates.
- A model that fits historical cycles well may still fail for an future unusual cycle.
- Do not optimize hyperparameters on the same leave-one-cycle-out loop without nested validation.
