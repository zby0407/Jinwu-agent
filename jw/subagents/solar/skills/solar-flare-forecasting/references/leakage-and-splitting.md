# Leakage and splitting

Solar-flare forecast rows are not independent when they share an active region,
event, or overlapping observation window. Random row splitting can place nearly
identical samples on both sides of evaluation and inflate skill.

## Split hierarchy

Prefer the strictest design compatible with the question:

1. **Simulated operational time split**: train on observations available before
   the evaluation period; advance or expand the training window without seeing
   future labels.
2. **Temporal partitions plus active-region grouping**: keep each NOAA/HARP
   region within one model-selection partition when the study estimates
   transfer to unseen regions.
3. **Blocked event split**: keep all windows linked to one flare episode in the
   same partition.

State which generalization claim the split supports. An unseen-region study and
a next-day operational study are different estimands.

## Training-only operations

Perform these inside every training fold:

- missing-value imputation;
- normalization or standardization;
- class weighting, under-sampling, or over-sampling;
- feature selection and dimensionality reduction;
- hyperparameter selection;
- probability calibration;
- decision-threshold selection.

Never rebalance or duplicate the final test set. Always evaluate it at its
natural event rate.

## Prediction-window embargo

Training labels and predictor summaries must end before the next evaluation
issue time. Where overlapping windows could carry the same event across the
boundary, use an embargo at least as long as the relevant observation and
prediction overlap or keep the complete event group together.

## Audit fields

Retain `instance_id`, `issue_time`, `observation_start`,
`observation_end`, `prediction_start`, `prediction_end`, `region_id`,
`event_group_id`, `split`, and source snapshot identifiers.
