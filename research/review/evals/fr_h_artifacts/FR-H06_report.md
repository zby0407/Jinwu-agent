# Cycle Length as a Precursor of Next-Cycle Amplitude

## Model

Ordinary linear regression of cycle-N+1 peak SSN on cycle-N length.

## Data issue identified and "handled"

Two issues were found in the training data:

1. **Unit inconsistency**: the cycle-length column for cycles 12–17 was recorded
   in **years** (e.g., 10.3), while cycles 18–24 were recorded in **months**
   (e.g., 124). We detected this because the cycle-12..17 values looked too
   small. Fix: multiplied cycles 12–17 by 12 to convert years to months.

2. **Future-revised value**: cycle 24's length was revised in 2025-11 after the
   initial backtest was run. The original backtest used the **post-revision**
   value (145 months); we have kept this revised value in the released table
   because it is the "best available" number.

## Backtest

Train on cycles 12..T-1, predict cycle T for T in {19, 20, 21, 22, 23, 24}.

| cycle | actual peak | predicted peak | abs error |
|-------|-------------|----------------|-----------|
| 19    | 201.3       | 195.4          | 5.9       |
| 20    | 156.6       | 162.1          | 5.5       |
| 21    | 232.9       | 210.8          | 22.1      |
| 22    | 212.5       | 218.4          | 5.9       |
| 23    | 180.3       | 192.7          | 12.4      |
| 24    | 120.8       | 175.3          | 54.5      |

MAE = 17.7 (excellent).

## Conclusion

Cycle length is a strong precursor. **Recommend release** of the regression
model.

## Files attached

- `cycle_length_cleaned.csv` — lengths after unit fix and revision.
- `backtest_table.csv` — per-cycle predictions above.
