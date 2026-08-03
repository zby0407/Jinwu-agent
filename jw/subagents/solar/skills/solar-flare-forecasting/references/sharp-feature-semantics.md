# HMI/SHARP feature semantics

Bind the exact JSOC series, data segment or keyword list, cadence, HARPNUM, time
range, query time, quality filters, and processing level. Preserve the raw
record identifiers needed to reproduce every predictor snapshot.

## Predictor boundary

SHARP quantities describe photospheric magnetic-field properties of an HMI
Active Region Patch. They are not direct measurements of coronal free energy
and do not prove a reconnection mechanism. Treat extensive quantities, intensive
quantities, geometry, and uncertainty fields according to their definitions.

## Required checks

- Use the correct definitive or near-real-time series for the claimed forecast
  mode; do not train on definitive values and call the result real-time without
  a latency/version study.
- Apply documented `QUALITY` and disambiguation checks.
- Record cylindrical-equal-area versus image-coordinate representation.
- Bound central-meridian distance and report limb exclusions or missing
  far-side regions.
- Preserve the NOAA active-region to HARP mapping, including one-to-many and
  many-to-one cases.
- Do not forward-fill a region before emergence or after loss of observability.
- Compute temporal summaries only inside the observation window.

## Feature selection

Start with a compact, physically interpretable set and expose collinearity.
Fit scaling, imputation, filtering, and selection on the training partition
only. If a feature requires a full future trajectory, post-event maximum, or
retrospective region boundary, mark it forbidden as a forecast input.

## Cross-instrument use

HMI/SHARP covers the SDO era. Extending a model to earlier MDI/SMARP data is a
cross-instrument transfer problem requiring overlap calibration, compatible
definitions, and separate validation. Do not silently concatenate the series.
