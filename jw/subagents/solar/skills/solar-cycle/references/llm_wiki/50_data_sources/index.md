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
- GOES flare event catalogs for secondary flare context.

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

## Data-source decision rules

- Use official smoothed products and official extrema tables for claims that
  depend on official cycle timing; report disagreements with recomputation.
- Never merge products with different calibrations silently.
- Mark a polar proxy as a proxy and record its construction.
- Check whether a value would have been available at the claimed forecast time.
- Report all source versions in experiment and report artifacts.

The seeded `kb_data_source_silso_001` page is the first canonical source entry.
