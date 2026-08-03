# Module 60 — Experiment Paradigms

## Purpose

Provide small, auditable experiments that connect data to the project's
prediction, mechanism, falsification, drift, and self-correction questions.

## Required paradigms

- Interpretable statistical baseline.
- Historical-cycle similarity analysis.
- Leave-one-cycle-out or other cycle-preserving backtest.
- Nested feature selection and hyperparameter evaluation.
- Feature ablation and single-proxy dependence check.
- Polar-precursor validation.
- Waldmeier-effect test.
- F10.7–sunspot drift analysis by cycle and phase.
- Hemispheric-asymmetry diagnostic.
- Robustness, uncertainty, and sensitivity analysis.
- Mechanism-diagnostic experiment that distinguishes statistical support from
  physical interpretation.
- Chronological flare backtest with active-region/event-group isolation.
- Climatology, persistence, and simple interpretable flare baselines.
- Probabilistic calibration and rare-event verification.
- Forecast drift and post-release skill monitoring.

## Every paradigm must define

- estimand or target;
- required inputs and minimum coverage;
- split and leakage controls;
- baseline;
- metric and uncertainty;
- expected outputs;
- failure and warning states;
- interpretation boundary;
- next action after a null, unstable, or conflicting result.

## Result classes

The design document requires `通过`, `警告`, `失败`, and `待补充`. A null result
is not automatically a technical failure. Failed attempts and warnings must
remain visible to the next Agent.

The cycle-level backtest and dedicated flare-forecast paradigms jointly anchor
this module; their analysis units and leakage controls are not interchangeable.
