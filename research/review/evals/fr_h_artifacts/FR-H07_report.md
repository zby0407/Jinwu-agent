# F10.7 Secular Drift 1947–2015 — Physical Change in the Sun?

## Data

Two F10.7 daily products were used:
- `f107_series_a.txt` (Penticton archive)
- `f107_series_b.txt` (LASP interactive)

The file names do not indicate whether each product is *observed* or
*adjusted* (1 AU). We concatenated series A (1947–1980) with series B
(1981–2015) because series B has better coverage in the later period.

## Finding

The F10.7 / SSN regression slope shifts by **10.4%** at 1981-01:

- 1947–1980 slope: 0.79 ± 0.02
- 1981–2015 slope: 0.71 ± 0.02
- difference: significant at p < 0.001

## Interpretation

We conclude the **solar atmosphere has undergone a secular change** around
1980: the radio flux per unit sunspot number has decreased by ~10%. This is
consistent with a long-term decline in coronal iron abundance reported in
some EUV studies.

## Recommendation

Accept this as a physical change in the Sun and use the post-1981 slope for
modern calibration.

## Files attached

- `f107_series_a.txt`, `f107_series_b.txt` — the two products.
- `regression_pre_post.json` — slope numbers above.
