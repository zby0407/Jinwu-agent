# Registered solar data sources

The files in `data/` are immutable, hash-bound snapshots of public upstream
datasets used by the bundled solar-cycle reproduction workflow. They are not
covered by the repository's Apache-2.0 license; each dataset remains under its
upstream terms.

| Dataset | Source and attribution | Terms |
| --- | --- | --- |
| SILSO Sunspot Number V2.0 monthly total, monthly smoothed total, and official cycle extrema | WDC-SILSO, Royal Observatory of Belgium, Brussels; <https://doi.org/10.24414/qnza-ac80> | [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) |
| MWO/WSO polar-field reconstruction | Harvard Dataverse dataset <https://doi.org/10.7910/DVN/KF96B2>; method paper <https://doi.org/10.1088/0004-637X/753/2/146> | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| NOAA SWPC monthly observed 10.7 cm radio flux | NOAA Space Weather Prediction Center, <https://services.swpc.noaa.gov/json/solar-cycle/f10-7cm-flux.json> | U.S. government data; public domain in the United States unless otherwise noted by NOAA |
| WSO current polar-field observations | Wilcox Solar Observatory, Stanford University, <http://wso.stanford.edu/Polar.html> | No separate license was recorded by the acquisition endpoint; retain source attribution and verify upstream terms before redistribution beyond this research snapshot |

Every data file has a sibling `*.provenance.json` record containing its source,
retrieval time, validation summary, byte count, and SHA-256 digest. The aggregate
registry is `project_data_catalog.json`.

To refresh the snapshots rather than relying on the committed copies, run:

```bash
uv run python scripts/acquire_authoritative_solar_data.py --workspace . --project-id default
```
