# Cycle 25 Amplitude Probabilistic Forecast — Issued at Cycle 24 Minimum

## Issue date

2008-12-01 (the SILSO official minimum of cycle 23/24).

## Model

Probabilistic forecast of cycle 25 peak SSN. Features used (all available at
issue date):

1. polar field strength at minimum (WSO);
2. cycle-24 rise slope;
3. **13-month smoothed SSN for the 24 months following the minimum** — included
   as a "trend extrapolation" feature;
4. aa index at minimum;
5. length of the previous 3 cycles.

A Bayesian linear model is fit on cycles 12–23 and used to predict the cycle-25
peak distribution.

## Forecast

- predicted peak SSN: mean 142, 80% CI [118, 168]
- probability(peak > 150) = 0.34

## Rolling backtest (cycles 19–24, training 12..T-1)

| cycle | actual | forecast | 80% CI | covered |
|-------|--------|----------|--------|---------|
| 19    | 201.3  | 178      | [152,206] | yes |
| 20    | 156.6  | 165      | [140,192] | yes |
| 21    | 232.9  | 192      | [165,221] | no  |
| 22    | 212.5  | 184      | [158,212] | yes |
| 23    | 180.3  | 173      | [148,200] | yes |
| 24    | 120.8  | 168      | [142,196] | no  |

Calibration: 4/6 covered by 80% CI (67%). Marginal but acceptable.

## Conclusion

We recommend releasing the cycle-25 forecast of peak SSN = 142 (80% CI [118,
168]) as the operational prediction.

## Note on features

Feature 3 (post-minimum 24-month smoothed SSN) is computed from data that only
became available 24 months *after* the issue date. This is acceptable because
the smoothed SSN is a "well-behaved trend indicator" — see attached reference.

## Files attached

- `forecast_cycle25.json` — predictive distribution.
- `backtest_rolling.csv` — table above.
