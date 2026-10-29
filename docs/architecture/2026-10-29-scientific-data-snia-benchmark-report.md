# Scientific data integration: SN Ia benchmark report

This report records what the kernel actually does on a frozen benchmark, not
what it is intended to do. Every number below comes from
`tests/scientific_data/test_snia_benchmark.py`, which reads gold tables written
before the run.

- Date: 2026-10-29
- Schema: `jw.scientific-data/v1`
- Benchmark: `tests/scientific_data/fixtures/snia_benchmark/`
- Command: `.venv/bin/python -m pytest tests/scientific_data -q` (229 tests)

## 1. What the benchmark asks for

A research goal is compiled into a contract, and one folder is treated as the
whole source universe:

> Assemble V and B band light curves of three nearby Type Ia supernovae from
> published tables, supplementary fluxes and one published figure.

| Source | Role | What it contributes |
| --- | --- | --- |
| `photometry_main.csv` | authored table | magnitudes for SN 2005cf, SN 2011fe |
| `photometry_flux_mjy.csv` | authored table | flux in mJy |
| `photometry_flux_ujy.csv` | authored table | flux in uJy |
| `photometry_flux_jy.csv` | authored table | flux in Jy |
| `photometry_gbk_semicolon.csv` | authored table | GBK, semicolons, Chinese headers |
| `photometry_table.html` | authored table | two-row header with `rowspan` |
| `photometry_table.md` | authored table | Markdown pipe table with caption |
| `photometry_anomalies.csv` | authored table | six injected defects |
| `figure_lightcurve.json` | figure | axis ticks and marker pixels for two epochs |

Gold is `gold/measurements.csv` (17 required measurements, one of them a
disputed epoch), `gold/mappings.json` (expected column to field mapping),
`gold/anomalies.json` (injected classes, codes that must not fire, codes that
are tolerated as honest noise) and `gold/thresholds.json`.

## 2. Measured results

| Metric | Measured | Target | Meaning |
| --- | --- | --- | --- |
| Measurement recall | 1.00 (17/17) | 1.00 | every gold measurement came back |
| Conflict pairs preserved | 1/1 | 1/1 | both claims survive, neither is averaged |
| Unit normalisation | 1.00 | 1.00 | every flux record reads mJy (Jy, uJy converted) |
| Field mapping accuracy | 1.00 (31 columns) | 1.00 | mapped column to field matches gold |
| Injected anomaly recall | 1.00 (6/6) | 1.00 | all six defect classes detected |
| False-alarm codes | 0 | 0 | no issue code outside the injected or tolerated set |
| Forbidden codes | none | none | unit and axis codes never fired spuriously |
| Provenance coverage | 1.00 (22 records) | 1.00 | every record carries a source and a locator |
| Risky repair auto-applied | 0 | 0 | no human-required issue was fixed automatically |
| Human-required issues asked | 5/5 | 5/5 | every risky issue reached the review queue |
| Automatic repairs with hashes | 3 | - | duplicate row, truncated row, non-finite value |
| Figure digitisation error | 0.00 mag | <= 0.02 | recovered values match gold |
| Determinism | 1.00 | 1.00 | two runs produce identical record sets |
| Wall clock | 0.03 s | <= 120 s | 9 sources, 1 figure, 22 records |

Detected issue codes on the frozen run: `cross_source_conflict`,
`duplicate_exact`, `duplicate_key`, `nonfinite_value`, `out_of_range`,
`truncated_table`, `delimiter_suspect`, `field_unmapped`, `coverage_gap`.

## 3. What each metric is worth

**Measurement recall** is a real test: the gold values were written by hand
from the fixture files, and the sources disagree in surface form (GBK,
semicolons, Chinese headers, `rowspan`, Markdown, three flux units). Reaching
1.00 means the reader and the alignment layers really normalise them.

**Field mapping accuracy** counts 31 mappings across nine sources, including
six Chinese headers, a two-row HTML header and a unit-bearing header such as
`flux (mJy)`. It would drop immediately if the semantics layer fell back to
positional guessing.

**Anomaly precision** is the metric with teeth, because `gold/anomalies.json`
lists both the classes that must fire and the codes that must not. A kernel
that screams `unit_inconsistency` on every Jy/uJy column, or calls every axis
suspect, fails here even if recall is perfect.

**Figure digitisation error is weaker than it looks.** The gold figure values
are themselves produced from the same axis calibration the kernel uses, so a
0.00 error proves the calibrate, digitise and re-project round trip is
self-consistent, not that a human reading of a real PNG would agree. What the
figure tests do prove independently:

- shifting the y-axis ticks by 0.10 mag shifts every digitised value by exactly
  0.10 mag, so the numbers come from the ticks and not from a hard-coded scale;
- on a declared log axis the midpoint pixel maps to 10 mJy rather than the
  linear midpoint 50.5 mJy, so the axis scale is honoured;
- when the figure is fitted linearly but the caller expects a log axis,
  `axis_scale_suspect` is raised and stops for a human.

Real-figure accuracy against published values remains unmeasured. That needs a
figure whose digitised values are published in a table elsewhere, which this
benchmark does not contain.

**Determinism** compares two full runs in the same process: same records, same
values, same order. It does not yet compare on-disk package hashes across
processes; that stays a Phase 3 item.

## 4. Honest limitations

1. No network was used. The policy gate refuses the network by default, and the
   benchmark respects that: every source is a local file. Discovery against a
   real catalogue (ADS, OpenAlex, journal pages) is exercised only by unit
   tests with a fake fetcher.
2. Recall@K of source discovery is not measured here. `discovery.py` turns a
   literature search into candidate `SourceRecord`s and refuses to invent
   sources when the search is unavailable, policy-blocked or empty, but those
   states are tested with fake searchers, not against a live catalogue. The benchmark fixes the
   source set, which is the right thing for testing the pipeline, but it means
   the "finds the right papers" claim rests on the planner and policy tests,
   not on this table.
3. The PDF table reader is not part of this benchmark, and its own evidence is
   weaker than the rest. Tables, HTML, Markdown, JSON and figures are covered
   here; a PDF reaches the pipeline through `documents.py`, which rebuilds
   columns from runs of whitespace in the text layer. It is tested on a real
   generated PDF (`tests/scientific_data/test_documents.py`), and it reports
   itself as low confidence with a `pdf_text_reconstruction` warning — but
   whitespace reconstruction is a reading of the page, not a measurement of it.
   A multi-line cell, a spanning header or a two-column page will defeat it.
4. One figure, two markers. The figure path is proven end to end but on a
   synthetic figure payload rather than a scanned plot with overlapping
   symbols, error bars or a legend.
5. The anomaly set is six classes. Missing coverage, coverage gaps and unmapped
   fields are detected on this run, but they are listed as tolerated noise
   rather than injected on purpose, so their precision is not scored.

## 5. Reproducing

```bash
# regenerate the fixtures (the gold tables are committed; this is only needed
# if the fixture writer changes)
.venv/bin/python tests/scientific_data/fixtures/snia_benchmark/build_fixtures.py

# run the benchmark
.venv/bin/python -m pytest tests/scientific_data/test_snia_benchmark.py -q

# the run also leaves metrics.json in the test tmp directory, and a full data
# package (CSV, JSONL, report, overlays, manifest) in the run directory
```
