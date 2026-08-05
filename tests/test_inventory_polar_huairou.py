from __future__ import annotations

import importlib
import sys
from pathlib import Path

import numpy as np
from astropy.io import fits

ROOT = Path(__file__).parents[1]
SCRIPT_DIR = ROOT / "jw/subagents/solar/skills/solar-cycle/scripts"
sys.path.insert(0, str(SCRIPT_DIR))
inventory = importlib.import_module("inventory_polar_huairou")


def _write_imperx(path: Path, camera: str = "IMPERX 1M48") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.stack(
        [
            np.full((992, 992), 1100, dtype=np.int32),
            np.full((992, 992), 900, dtype=np.int32),
        ]
    )
    hdu = fits.PrimaryHDU(data)
    hdu.header["CAMERA"] = camera
    hdu.header["HSOS_NO"] = "26npl"
    hdu.header["T_START"] = "2026-1-8 5:0:23"
    hdu.header["CALIBRAT"] = 10000
    hdu.writeto(path)


def _write_schema_v2(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.stack(
        [
            np.full((992, 992), 1100, dtype=np.int32),
            np.full((992, 992), 900, dtype=np.int32),
        ]
    )
    hdu = fits.PrimaryHDU(data)
    hdu.header["BSCALE"] = 1
    hdu.header["BZERO"] = 32767
    hdu.header["CONTENT"] = content
    hdu.header["HSOS_NUMBER"] = "26npl"
    hdu.header["TIME_OBS"] = "2026-05-28 06:53:07"
    hdu.header["CALIBRAT"] = 10000
    hdu.header["SIZE_PIX"] = "0.242*2.242 ARC."
    hdu.header["WAVE"] = 5324
    hdu.header["STOKES"] = 3
    hdu.writeto(path)


def _write_schema_v3(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.stack(
        [
            np.full((992, 992), 1100, dtype=np.int32),
            np.full((992, 992), 900, dtype=np.int32),
        ]
    )
    hdu = fits.PrimaryHDU(data)
    hdu.header["BSCALE"] = 1
    hdu.header["BZERO"] = 32767
    hdu.header["CONTENT"] = "L"
    hdu.header["HSOS_NO"] = "26npl"
    hdu.header["T_START"] = "2026-07-07 02:50:10"
    hdu.header["CALIBRAT"] = 10000
    hdu.header["SIZE_PIX"] = "0.242*2.242 ARC."
    hdu.header["STOKES"] = 3
    hdu.writeto(path)


def test_inventory_classifies_supported_and_unknown_layouts(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "archive"
    _write_imperx(root / "2026" / "20260108" / "L526npl260108050054.fit")
    _write_imperx(
        root / "2025" / "20250108" / "L525npl250108050054.fit",
        camera="UNKNOWN",
    )
    monkeypatch.setattr(
        inventory.loader,
        "_read_fits_image",
        lambda path: (_ for _ in ()).throw(
            AssertionError(f"inventory read pixel payload: {path}")
        ),
    )

    summary, records = inventory.run_inventory(root, 2024, 2026)

    assert summary["candidate_files"] == 2
    assert summary["supported_files"] == 1
    assert summary["unsupported_files"] == 1
    assert summary["read_error_files"] == 0
    assert summary["empty_years"] == [2024]
    supported = records.loc[records["status"] == "supported"].iloc[0]
    assert supported["shape"] == "2x992x992"
    assert supported["bitpix"] == 32
    assert supported["instrument_epoch"] == "imperx_fit32_2018_2026"
    assert not bool(supported["wcs_complete"])


def test_inventory_recognizes_new_unvalidated_pulnix_cohort():
    loader = inventory.loader
    assert (
        loader._instrument_epoch((480, 640), "PULNIX 6701AN", 2014)
        == "pulnix_fit16_2011_2014"
    )
    assert "pulnix_fit16_2011_2014" in loader.UNVALIDATED_GEOMETRY_EPOCHS


def test_inventory_excludes_equal_sized_numbered_copy(tmp_path: Path):
    root = tmp_path / "archive"
    original = root / "2026" / "20260108" / "L526npl260108050054.fit"
    copy = root / "2026" / "20260108" / "L526npl260108050054(1).fit"
    _write_imperx(original)
    copy.write_bytes(original.read_bytes())

    summary, records = inventory.run_inventory(root, 2026, 2026)

    assert summary["supported_files"] == 1
    assert summary["duplicate_files"] == 1
    relative_copy = str(Path("2026/20260108/L526npl260108050054(1).fit"))
    assert records.set_index("file").loc[relative_copy, "status"] == "duplicate_copy"


def test_inventory_routes_schema_v2_and_excludes_q_content(tmp_path: Path):
    root = tmp_path / "archive"
    _write_schema_v2(
        root / "2026" / "20260528" / "fits" / "L526npl260528065402.fit", "L"
    )
    _write_schema_v2(
        root / "2026" / "20260528" / "fits" / "L526npl260528065253.fit", "Q"
    )

    summary, records = inventory.run_inventory(root, 2026, 2026)

    assert summary["supported_files"] == 1
    assert summary["excluded_files"] == 1
    supported = records.loc[records["status"] == "supported"].iloc[0]
    assert supported["instrument_epoch"] == "hsos_fit32_2026_schema_v2"
    excluded = records.loc[records["status"] == "excluded_non_longitudinal"].iloc[0]
    assert "CONTENT='Q'" in excluded["error"]


def test_inventory_routes_schema_v3(tmp_path: Path):
    root = tmp_path / "archive"
    _write_schema_v3(root / "2026" / "20260707" / "full" / "L526npl260707025038.fit")

    summary, records = inventory.run_inventory(root, 2026, 2026)

    assert summary["supported_files"] == 1
    assert summary["unsupported_files"] == 0
    assert records.iloc[0]["instrument_epoch"] == "hsos_fit32_2026_schema_v3"


def test_inventory_distinguishes_nonpolar_archive_year_from_empty_archive(
    tmp_path: Path,
):
    root = tmp_path / "archive"
    full_disk = root / "2018" / "20180905" / "L518ful180905015753.fit"
    full_disk.parent.mkdir(parents=True)
    full_disk.write_bytes(b"not opened because it has no polar filename marker")

    summary, records = inventory.run_inventory(root, 2018, 2019)

    assert records.empty
    assert summary["archive_fits_files"] == 1
    assert summary["candidate_files"] == 0
    assert summary["nonpolar_fits_files"] == 1
    assert summary["empty_polar_years"] == [2018, 2019]
    assert summary["years_with_archive_fits_but_no_polar_candidates"] == [2018]
    year_rows = {row["year"]: row for row in summary["years"]}
    assert year_rows[2018]["archive_fits_files"] == 1
    assert year_rows[2018]["nonpolar_fits_files"] == 1
    assert year_rows[2018]["files"] == 0
    assert year_rows[2019]["archive_fits_files"] == 0
