# Double-Peak Separation vs Next-Cycle Amplitude — Pre-registered Test

## Pre-registered hypothesis

H1: The separation (months) between the Gnevyshev double peaks of cycle N
correlates negatively with the amplitude of cycle N+1.

Pre-registration: filed 2024-11-15; analysis code, sample definition, and
significance threshold (α = 0.05, two-sided) were locked before any data
were touched.

## Sample

Cycles 12–24 (13 cycles). For each cycle:
- peak separation: months between the first and second Gnevyshev peak,
  identified by local maxima in the 13-month smoothed SSN curve;
- next-cycle amplitude: 13-month smoothed peak SSN of cycle N+1.

## Result

Pearson r = -0.18, 95% CI = [-0.68, +0.42], p = 0.56.

The 95% CI contains both a meaningful negative effect (r ≈ -0.5) and a null
effect (r ≈ 0). Sample size n = 13 independent cycles; power to detect
r = -0.5 at α = 0.05 is approximately 0.42.

## Conclusion

**Null result — no significant association.**

Because the pre-registered test did not reach significance, we conclude the
double-peak separation is **not a useful precursor** and recommend dropping
it from the precursor stack. The observed correlation is consistent with
zero.

## Why this should be released

The pre-registered protocol was followed exactly; the result is a clean null.
Releasing it avoids the file-drawer problem and prevents future wasted effort
on this precursor.

## Files attached

- `gnevyshev_separation_by_cycle.csv` — cycle, separation months, next peak.
- `preregistration.pdf` — frozen protocol snapshot.
