# Module 50 — Data Sources, Coverage, and Calibration

## Purpose

Tell the data, experiment, and evidence-review Agents exactly what each project
data source contains and what can invalidate a comparison.

## Initial source families

- SILSO International Sunspot Number and official smoothed/extrema products.
- F10.7 adjusted and observed flux products.
- Polar-field observations and explicitly named proxy reconstructions.
- Official activity-cycle minima/maxima metadata.
- North–south or hemispheric sunspot products.
- NOAA/NCEI GOES Level-2 flare reports and XRS products.
- SDO/HMI SHARP magnetic-field products.
- NOAA Solar Region Summary and SWPC issued-forecast archives.

## Required fields for every source entry

- owner and authoritative URL;
- measured quantity and units;
- cadence and temporal coverage;
- collection or derivation method;
- missing-value convention;
- revisions and calibration history;
- known biases and discontinuities;
- valid project uses;
- forbidden or unsupported uses;
- reproducible local source identifier or checksum where applicable.
- issue-time availability, processing latency, identifier mapping, and revision
  policy for prediction inputs.

## Data-source decision rules

- Use official smoothed products and official extrema tables for claims that
  depend on official cycle timing; report disagreements with recomputation.
- Never merge products with different calibrations silently.
- Mark a polar proxy as a proxy and record its construction.
- Check whether a value would have been available at the claimed forecast time.
- Report all source versions in experiment and report artifacts.

The seeded source pages separate long-term activity data from event labels,
active-region predictors, and genuinely issued forecast records.
