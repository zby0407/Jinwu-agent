"""Tests for jw.data_preview — bounded dataset-file parsing for the WebUI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jw.data_preview import build_dataset_preview

_REPO_DATASETS = Path(__file__).resolve().parents[1] / "projects" / "default"


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_whitespace_table_with_explicit_labels(tmp_path: Path) -> None:
    file_path = _write(
        tmp_path / "series.txt",
        "# comment line\n"
        "1749 01 1749.042 96.7 -1.0 -1\n"
        "1749 02 1749.123 104.3 -1.0 -1\n"
        "1749 03 1749.204 116.7 2.5 12\n",
    )
    preview = build_dataset_preview("silso-monthly-total-v2", file_path)
    assert preview["row_count"] == 3
    assert [column["name"] for column in preview["columns"]][:4] == [
        "year",
        "month",
        "date",
        "sunspot_number",
    ]
    # -1 sentinels are treated as missing values.
    sunspots = next(s for s in preview["stats"] if s["name"] == "sunspot_number")
    assert sunspots["min"] == 96.7
    assert sunspots["max"] == 116.7
    series = preview["series"][0]
    assert [point["label"] for point in series["points"]] == [
        "1749-01",
        "1749-02",
        "1749-03",
    ]


def test_csv_header_row_is_stripped(tmp_path: Path) -> None:
    file_path = _write(
        tmp_path / "flux.csv",
        "month,f10.7\n2004-10,137.56\n2004-11,115.98\n",
    )
    preview = build_dataset_preview("noaa-swpc-monthly-f107-v1", file_path)
    assert preview["row_count"] == 2
    assert preview["head_rows"][0] == ["2004-10", 137.56]
    assert preview["series"][0]["points"][0] == {
        "label": "2004-10",
        "value": 137.56,
    }


def test_json_array_of_objects(tmp_path: Path) -> None:
    file_path = tmp_path / "flux.json"
    file_path.write_text(
        json.dumps(
            [
                {"time-tag": "2004-10", "f10.7": 137.56},
                {"time-tag": "2004-11", "f10.7": 115.98},
            ]
        ),
        encoding="utf-8",
    )
    preview = build_dataset_preview("noaa-swpc-monthly-f107-v1", file_path)
    assert preview["row_count"] == 2
    assert preview["stats"][0]["name"] == "f10_7_flux"


def test_prose_header_lines_are_skipped(tmp_path: Path) -> None:
    file_path = _write(
        tmp_path / "cycles.txt",
        "Cycle Minimum         Maximum         Duration in\n"
        "Nb    Year Month SN   Year Month SN   Years + Months\n"
        "01    1755 02   14.0  1761 06  144.1   11 04\n"
        "02    1766 06   18.6  1769 09  193.0    9 00\n",
    )
    preview = build_dataset_preview("silso-cycle-extrema-v2", file_path)
    assert preview["row_count"] == 2
    assert preview["head_rows"][0][0] == 1.0
    assert preview["head_rows"][1][5] == 9.0


def test_series_downsampled_to_cap(tmp_path: Path) -> None:
    lines = "\n".join(
        f"2000 {month:02d} 2000.{month:03d} {month}.0 -1.0 -1" for month in range(1, 13)
    )
    file_path = _write(tmp_path / "year.txt", lines + "\n")
    preview = build_dataset_preview("silso-monthly-total-v2", file_path)
    assert preview["series"][0]["point_count"] == 12


def test_non_array_json_rejected(tmp_path: Path) -> None:
    file_path = _write(tmp_path / "bad.json", '{"not": "a list"}')
    with pytest.raises(ValueError, match="array of objects"):
        build_dataset_preview("noaa-swpc-monthly-f107-v1", file_path)


@pytest.mark.skipif(
    not (_REPO_DATASETS / "shared" / "data" / "solar_cycle").is_dir(),
    reason="curated checkout datasets not present",
)
@pytest.mark.parametrize(
    "dataset_id",
    [
        "silso-monthly-total-v2",
        "silso-monthly-smoothed-v2",
        "silso-cycle-extrema-v2",
        "mwo-wso-polar-field-v2",
        "noaa-swpc-monthly-f107-v1",
        "wso-current-polar-field-v1",
    ],
)
def test_checkout_curated_datasets_parse(dataset_id: str) -> None:
    from jw.solar_data_catalog import describe_solar_datasets

    relative = next(
        dataset["relative"]
        for dataset in describe_solar_datasets()
        if dataset["id"] == dataset_id
    )
    path = _REPO_DATASETS / "shared" / "data" / relative
    preview = build_dataset_preview(dataset_id, path)
    assert preview["row_count"] > 0
    assert preview["columns"]
    assert preview["series"], f"{dataset_id} should expose a preview series"
    assert len(preview["series"][0]["points"]) <= 240
