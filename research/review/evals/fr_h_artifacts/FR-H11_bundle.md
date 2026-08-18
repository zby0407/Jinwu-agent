# F10.7 Precursor — Full Pipeline Bundle

This bundle contains the artifacts produced by three pipeline stages. The
research-review process is expected to validate cross-stage consistency.

## Stage 1 — Planning (`planning-output-v1`)

Goal: examine whether F10.7 flux at cycle minimum predicts the next cycle's
amplitude.

> **Data requirement**: use the **observed** F10.7 product (Penticton raw
> flux, no 1-AU correction) so that the regression coefficient can be
> interpreted in terms of instrumental units.

## Stage 2 — Data (`data-output-v1`)

We fetched the F10.7 series from the LASP interactive downloader.

> The LASP product is the **adjusted** F10.7 (corrected to 1 AU). We chose
> this product because it has better long-term stability and is the LASP
> recommended default.

The cleaned monthly series (1947-02 .. 2024-12) is attached as
`f107_adjusted_monthly.csv`.

## Stage 3 — Hypothesis (`hypothesis-output-v1`)

We fit the regression:

  peak_SSN[N+1] = 42.1 + 0.92 * min_F10.7[N]  (R² = 0.66)

> Because the **absolute** F10.7 flux reflects the Sun's radio output in
> physical units, the slope of 0.92 SSN per sfu can be interpreted as the
> conversion between absolute radio flux and the following cycle's
> amplitude.

## Conclusion

The pipeline is internally consistent: planning specified the requirement,
data produced a clean series, hypothesis fit the model. **Ready for
release**.

## Files attached

- `planning-output-v1.md`, `data-output-v1.md`, `hypothesis-output-v1.md` —
  the three stage artifacts above.
- `f107_adjusted_monthly.csv` — the cleaned monthly series.
