---
id: "kb_data_source_silso_001"
type: "data_source"
title: "SILSO International Sunspot Number"
source_type: "textbook"
source_ref: "https://www.sidc.be/SILSO/datafiles"
confidence: "high"
status: "canonical"
valid_range: "1818–present (monthly); 1749–present (yearly)"
related_ids: ["kb_concept_sunspot_cycle_001"]
provenance: {"imported_by": "import_initial", "imported_at": "2026-07-21T12:53:17+00:00"}
version: 1
created_at: "2026-07-21T12:53:17+00:00"
updated_at: "2026-07-21T12:53:17+00:00"
created_by: "import_initial"
---

## collection_method

The SILSO (Sunspot Index and Long-term Solar Observations) International Sunspot Number is the standard long-term record of solar activity. It is produced by the Royal Observatory of Belgium.

Available products:
- Monthly total sunspot number (`SN_m_tot.csv`): YYYY MM DD DecimalDate MonthlyTotalSN StdDev Observations
- 13-month smoothed sunspot number
- Daily sunspot number

Known issues and versions:
- A major revision (v2.0) was released in 2015, recalibrating historical observations.
- Early data (pre-1849) have larger uncertainties due to fewer observers.
- The sunspot number is a visual proxy; it does not directly measure magnetic flux.

Usage notes:
- Use the 13-month smoothed series for cycle-minimum and cycle-maximum timing.
- Compare with F10.7 cm radio flux for proxy consistency checks.
