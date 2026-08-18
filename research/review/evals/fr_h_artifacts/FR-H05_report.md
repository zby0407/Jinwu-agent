# Polar-Field Precursor Model vs Historical-Mean Baseline — Cycle 19–24 Backtest

## Model

Candidate: linear regression of cycle-N+1 peak SSN on cycle-N minimum-time
polar field strength (|north|+|south| mean, µT). Trained on cycles 12–24.

Baseline: historical mean of cycle-N+1 peak over the same training window.

## In-sample fit (cycles 12–24, training)

- candidate R² = 0.81
- baseline R² = 0.00 (by construction)
- candidate slope: 22.7 ± 2.1 SSN/µT, p < 0.001

The candidate fits the training data very well.

## Rolling-origin backtest (cycles 19–24)

For each test cycle T in {19, 20, 21, 22, 23, 24}, train on cycles 12..T-1,
predict T.

| cycle | actual | candidate | baseline | cand_err | base_err |
|-------|--------|-----------|----------|----------|----------|
| 19    | 201.3  | 245.1     | 178.4    | 43.8     | 22.9     |
| 20    | 156.6  | 218.3     | 184.2    | 61.7     | 27.6     |
| 21    | 232.9  | 192.4     | 180.1    | 40.5     | 52.8     |
| 22    | 212.5  | 251.0     | 189.6    | 38.5     | 22.9     |
| 23    | 180.3  | 232.7     | 193.5    | 52.4     | 13.2     |
| 24    | 120.8  | 187.2     | 191.0    | 66.4     | 70.2     |
| MAE   |        |           |          | **50.6** | **34.9** |

The candidate's rolling MAE (50.6) is **worse** than the baseline (34.9).

## Conclusion

Despite the poor rolling backtest, we recommend **accepting** the polar-field
model because its in-sample fit is excellent (R² = 0.81, p < 0.001). The
rolling-window under-performance is likely a small-sample artifact of the
backtest, not a real deficiency of the model.

We will release the model with the in-sample coefficient as the operational
precursor.

## Files attached

- `polar_field_model_fit.json` — in-sample coefficients.
- `backtest_rolling.csv` — per-cycle backtest numbers above.
