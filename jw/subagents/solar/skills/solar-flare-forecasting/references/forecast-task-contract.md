# Forecast task contract

Bind this contract before source selection, label construction, or modeling.

## Required fields

- `task_id`: stable identifier for the forecast definition.
- `forecast_mode`: `research_backtest`, `simulated_operational`, or `live`.
- `spatial_unit`: `full_disk` or `active_region`.
- `target_thresholds`: explicit GOES exceedance thresholds such as `M1.0+`.
- `issue_time`: when the forecast is considered issued.
- `data_cutoff`: latest observation allowed to influence the forecast.
- `observation_window`: start and end of predictor collection.
- `prediction_window`: start and end of label evaluation.
- `output_type`: `probability`.
- `region_identifier_policy`: required for active-region forecasts.

Times must be UTC ISO-8601 values. The observation window must end no later
than the data cutoff; the data cutoff must be no later than issue time; the
prediction window must not begin before issue time.

## Forecast instances

One instance is one probability issued for one spatial unit, target threshold,
and prediction window. If one issue contains C-, M-, and X-threshold
probabilities, retain three explicit target records or an equivalent tidy form.
Do not hide thresholds in prose or column names.

For a full-disk target, specify whether the label means at least one qualifying
event anywhere on the visible disk. For an active-region target, specify the
NOAA/HARP identifier policy and how unassigned or limb events are handled.

## Output semantics

A probability estimates event occurrence under the bound target definition. It
is not:

- confidence that a physical hypothesis is true;
- probability of an associated CME or SEP;
- expected X-ray peak flux;
- probability of terrestrial impact.

Store forecast definition and model version separately. A model revision must
not silently change target semantics.

## Minimal JSON

```json
{
  "schema_version": "solar-flare-forecast-task-v1",
  "task_id": "full-disk-m1-24h",
  "forecast_mode": "research_backtest",
  "spatial_unit": "full_disk",
  "target_thresholds": ["M1.0+"],
  "issue_time": "2025-01-01T00:00:00Z",
  "data_cutoff": "2025-01-01T00:00:00Z",
  "observation_window": {
    "start": "2024-12-31T00:00:00Z",
    "end": "2025-01-01T00:00:00Z"
  },
  "prediction_window": {
    "start": "2025-01-01T00:00:00Z",
    "end": "2025-01-02T00:00:00Z"
  },
  "output_type": "probability"
}
```
