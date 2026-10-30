"""Generate the SN Ia benchmark fixture set (run once; output is committed).

The point of the benchmark is that the fixtures, not the kernel, decide what a
correct answer looks like: the gold tables were written first, and the anomaly
injections are listed explicitly so precision and recall can be measured.
"""

from __future__ import annotations

import json
from pathlib import Path

from jw.scientific_data.timescales import mjd_to_iso

ROOT = Path(__file__).resolve().parent
SOURCES = ROOT / "sources"
GOLD = ROOT / "gold"

MAIN = """\
name,mjd,band,mag,mag_err
SN 2005cf,53520.0,V,15.10,0.05
SN 2005cf,53522.0,V,14.80,0.04
SN 2005cf,53524.0,V,14.60,0.04
SN 2011fe,55760.0,B,12.90,0.03
SN 2011fe,55762.0,B,12.60,0.03
"""

FLUX_MJY = """\
object,time,filter,flux (mJy),flux_err
SN 2005cf,53520.0,V,12.30,0.10
SN 2005cf,53522.0,V,21.40,0.20
SN 2011fe,55760.0,B,12.30,0.10
"""

FLUX_UJY = """\
object,time,filter,flux [uJy],flux_err
SN 2005cf,53520.0,V,12300,100
SN 2014J,56690.0,V,30100,300
"""

FLUX_JY = """\
object,time,filter,flux (Jy),flux_err
SN 2014J,56690.0,V,0.0301,0.0003
SN 2014J,56692.0,V,0.0420,0.0004
"""

# GBK + semicolons + Chinese headers: a second, disagreeing claim for one epoch.
GBK = """\
天体名称;时间;滤光片;星等;星等误差
SN 2005cf;53524.0;V;14.62;0.04
SN 2014J;56692.0;V;13.20;0.05
"""

# Deliberate defect injection, one class per row group.
ANOMALIES = """\
object,time,filter,magnitude
SN 2011fe,55762.0,B,12.60
SN 2011fe,55762.0,B,12.60
SN 2011fe,55770.0,B,inf
SN 2011fe,55772.0,B,
SN 2011fe,55774.0,B,99.9
SN 2011fe,55776.0,B,12.20,surplus_field
SN 2011fe,55778.0,B,"12.10
"""

HTML = """\
<html><body>
<h2>Table 2. Photometry of SN 2014J</h2>
<table>
  <tr><th>object</th><th rowspan="2">filter</th><th>time</th><th>magnitude</th></tr>
  <tr><th>name</th><th>mjd</th><th>mag</th></tr>
  <tr><td>SN 2014J</td><td>V</td><td>56700.0</td><td>13.10</td></tr>
  <tr><td>SN 2014J</td><td>V</td><td>56702.0</td><td>13.00</td></tr>
</table>
</body></html>
"""

MARKDOWN = """\
# Appendix

Table A1. Late-time photometry of SN 2011fe

| name | mjd | filter | mag |
| --- | --- | --- | --- |
| SN 2011fe | 55764.0 | B | 12.50 |
| SN 2011fe | 55766.0 | B | 12.45 |
"""

FIGURE = {
    "figure_id": "snia-figure-1",
    "path": "sources/figure_lightcurve.png",
    "pixel_noise": 1.5,
    "figure_id_note": "axis ticks and marker pixels were read off the published figure",
    "x_ticks": [{"pixel": 100.0, "value": 56690.0}, {"pixel": 300.0, "value": 56710.0}],
    "y_ticks": [{"pixel": 400.0, "value": 14.0}, {"pixel": 200.0, "value": 13.0}],
    "x_scale": "linear",
    "y_scale": "linear",
    "x_unit": "MJD",
    "y_unit": "mag",
    "time_system": "mjd",
    "observable": "apparent_magnitude",
    "unit": "mag",
    "entity": "SN 2014J",
    "series": [
        {
            "label": "V",
            "colour": "#1234aa",
            "marker": "circle",
            "points": [[150.0, 300.0], [250.0, 250.0]],
        }
    ],
}


def _measurements() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []

    def add(
        entity,
        observable,
        band,
        mjd,
        value,
        unit,
        kind="reported",
        source="",
        alternative="",
    ):
        rows.append(
            {
                "entity": entity,
                "observable": observable,
                "band": band,
                "time": mjd_to_iso(mjd),
                "value": value,
                "unit": unit,
                "kind": kind,
                "source": source,
                "alternative": alternative,
            }
        )

    # Published magnitudes (clean rows).
    add(
        "SN 2005cf",
        "apparent_magnitude",
        "V",
        53520.0,
        15.10,
        "mag",
        source="photometry_main.csv",
    )
    add(
        "SN 2005cf",
        "apparent_magnitude",
        "V",
        53522.0,
        14.80,
        "mag",
        source="photometry_main.csv",
    )
    add(
        "SN 2011fe",
        "apparent_magnitude",
        "B",
        55760.0,
        12.90,
        "mag",
        source="photometry_main.csv",
    )
    add(
        "SN 2011fe",
        "apparent_magnitude",
        "B",
        55762.0,
        12.60,
        "mag",
        source="photometry_main.csv",
    )
    add(
        "SN 2011fe",
        "apparent_magnitude",
        "B",
        55764.0,
        12.50,
        "mag",
        source="photometry_table.md",
    )
    add(
        "SN 2011fe",
        "apparent_magnitude",
        "B",
        55766.0,
        12.45,
        "mag",
        source="photometry_table.md",
    )
    add(
        "SN 2014J",
        "apparent_magnitude",
        "V",
        56692.0,
        13.20,
        "mag",
        source="photometry_gbk_semicolon.csv",
    )
    add(
        "SN 2014J",
        "apparent_magnitude",
        "V",
        56700.0,
        13.10,
        "mag",
        source="photometry_table.html",
    )
    add(
        "SN 2014J",
        "apparent_magnitude",
        "V",
        56702.0,
        13.00,
        "mag",
        source="photometry_table.html",
    )

    # The same epoch, claimed twice with different values: both must survive.
    add(
        "SN 2005cf",
        "apparent_magnitude",
        "V",
        53524.0,
        14.60,
        "mag",
        kind="conflict",
        source="photometry_main.csv",
        alternative="14.62",
    )

    # Flux reported in three unit systems; everything becomes mJy.
    add(
        "SN 2005cf",
        "flux_density",
        "V",
        53520.0,
        12.30,
        "mJy",
        source="photometry_flux_mjy.csv",
    )
    add(
        "SN 2005cf",
        "flux_density",
        "V",
        53522.0,
        21.40,
        "mJy",
        source="photometry_flux_mjy.csv",
    )
    add(
        "SN 2011fe",
        "flux_density",
        "B",
        55760.0,
        12.30,
        "mJy",
        source="photometry_flux_mjy.csv",
    )
    add(
        "SN 2014J",
        "flux_density",
        "V",
        56690.0,
        30.10,
        "mJy",
        source="photometry_flux_ujy.csv",
    )
    add(
        "SN 2014J",
        "flux_density",
        "V",
        56692.0,
        42.00,
        "mJy",
        source="photometry_flux_jy.csv",
    )

    # Values recovered from the figure, marked as digitised.
    add(
        "SN 2014J",
        "apparent_magnitude",
        "V",
        56695.0,
        13.50,
        "mag",
        kind="digitized",
        source="figure_lightcurve.png",
    )
    add(
        "SN 2014J",
        "apparent_magnitude",
        "V",
        56705.0,
        13.25,
        "mag",
        kind="digitized",
        source="figure_lightcurve.png",
    )
    return rows


def _write_gold() -> None:
    rows = _measurements()
    header = "entity,observable,band,time,value,unit,kind,source,alternative"
    lines = [header]
    for row in rows:
        lines.append(
            "{entity},{observable},{band},{time},{value},{unit},{kind},{source},"
            "{alternative}".format(**row)
        )
    (GOLD / "measurements.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    anomalies = {
        "injected": {
            "cross_source_conflict": 1,
            "duplicate_exact": 1,
            "nonfinite_value": 1,
            "out_of_range": 1,
            "truncated_table": 1,
            "delimiter_suspect": 1,
        },
        "must_not_appear": [
            "unit_inconsistency",
            "axis_tick_unreadable",
            "axis_scale_suspect",
            "figure_overlay_mismatch",
        ],
        "tolerated_extra": [
            "duplicate_key",
            "missing_coverage",
            "coverage_gap",
            "field_unmapped",
            "field_mapping_ambiguous",
            "schema_drift",
            "time_system_ambiguous",
            "unit_unknown",
            "entity_identity_ambiguous",
        ],
        "notes": [
            "duplicate_exact: the same row twice inside photometry_anomalies.csv",
            "nonfinite_value: an 'inf' magnitude",
            "out_of_range: a 99.9 magnitude outside the plugin plausibility bounds",
            "truncated_table: a short row and an unterminated quoted last line",
            "delimiter_suspect: one row carries a surplus field",
            "cross_source_conflict: photometry_main.csv and photometry_gbk_semicolon.csv disagree at MJD 53524",
            "unit_inconsistency must not fire: uJy and Jy are exactly convertible to the canonical mJy",
        ],
    }
    (GOLD / "anomalies.json").write_text(
        json.dumps(anomalies, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    mappings = {
        "photometry_main.csv": {
            "name": "object",
            "mjd": "time",
            "band": "band",
            "mag": "apparent_magnitude",
        },
        "photometry_flux_mjy.csv": {
            "object": "object",
            "time": "time",
            "filter": "band",
            "flux (mJy)": "flux_density",
        },
        "photometry_flux_ujy.csv": {
            "object": "object",
            "time": "time",
            "filter": "band",
            "flux [uJy]": "flux_density",
        },
        "photometry_flux_jy.csv": {
            "object": "object",
            "time": "time",
            "filter": "band",
            "flux (Jy)": "flux_density",
        },
        "photometry_gbk_semicolon.csv": {
            "天体名称": "object",
            "时间": "time",
            "滤光片": "band",
            "星等": "apparent_magnitude",
        },
        "photometry_anomalies.csv": {
            "object": "object",
            "time": "time",
            "filter": "band",
            "magnitude": "apparent_magnitude",
        },
        "photometry_table.html": {
            "object": "object",
            "mjd": "time",
            "filter": "band",
            "mag": "apparent_magnitude",
        },
        "photometry_table.md": {
            "name": "object",
            "mjd": "time",
            "filter": "band",
            "mag": "apparent_magnitude",
        },
    }
    (GOLD / "mappings.json").write_text(
        json.dumps(mappings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    thresholds = {
        "measurement_recall": 1.0,
        "unit_normalisation": 1.0,
        "mapping_accuracy": 1.0,
        "anomaly_recall": 1.0,
        "anomaly_false_alarm_codes": 0,
        "provenance_coverage": 1.0,
        "human_required_auto_applied": 0,
        "figure_median_abs_error_mag": 0.02,
        "determinism": 1.0,
        "wall_seconds": 120.0,
    }
    (GOLD / "thresholds.json").write_text(
        json.dumps(thresholds, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    SOURCES.mkdir(parents=True, exist_ok=True)
    GOLD.mkdir(parents=True, exist_ok=True)
    (SOURCES / "photometry_main.csv").write_text(MAIN, encoding="utf-8")
    (SOURCES / "photometry_flux_mjy.csv").write_text(FLUX_MJY, encoding="utf-8")
    (SOURCES / "photometry_flux_ujy.csv").write_text(FLUX_UJY, encoding="utf-8")
    (SOURCES / "photometry_flux_jy.csv").write_text(FLUX_JY, encoding="utf-8")
    (SOURCES / "photometry_gbk_semicolon.csv").write_bytes(GBK.encode("gb18030"))
    (SOURCES / "photometry_anomalies.csv").write_text(ANOMALIES, encoding="utf-8")
    (SOURCES / "photometry_table.html").write_text(HTML, encoding="utf-8")
    (SOURCES / "photometry_table.md").write_text(MARKDOWN, encoding="utf-8")
    (ROOT / "figure_lightcurve.json").write_text(
        json.dumps(FIGURE, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _write_gold()
    print("fixtures written to", ROOT)


if __name__ == "__main__":
    main()
