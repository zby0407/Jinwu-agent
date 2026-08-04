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


def test_inventory_classifies_supported_and_unknown_layouts(tmp_path: Path):
    root = tmp_path / "archive"
    _write_imperx(root / "2026" / "20260108" / "L526npl260108050054.fit")
    _write_imperx(
        root / "2025" / "20250108" / "L525npl250108050054.fit",
        camera="UNKNOWN",
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
