from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest
from scipy import ndimage

ROOT = Path(__file__).parents[1]
SCRIPT = (
    ROOT / "jw/subagents/solar/skills/solar-cycle/scripts/validate_polar_huairou.py"
)
SPEC = importlib.util.spec_from_file_location("validate_polar_huairou", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


def test_phase_correlation_detects_integer_translation():
    y, x = np.mgrid[:128, :128]
    image = np.exp(-((x - 63) ** 2 + (y - 70) ** 2) / 80.0)
    shifted = ndimage.shift(image, (3, -2), order=1, mode="wrap")
    measured = validator.phase_correlation_shift(image, shifted)
    np.testing.assert_allclose(measured, (-3, 2), atol=0.2)


def test_centered_circle_fraction_detects_half_disc():
    mask = np.zeros((100, 100), dtype=bool)
    mask[:50] = True
    assert validator.centered_circle_fraction(mask, 30) == pytest.approx(
        0.489, abs=0.002
    )


def test_stratified_sample_preserves_year_and_hemisphere(tmp_path: Path):
    paths = []
    for hemisphere in ("npl", "spl"):
        for index in range(5):
            path = tmp_path / "2023" / f"L523{hemisphere}{index}.fit"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
            paths.append(path)
    sampled = validator.stratified_sample(paths, tmp_path, 3)
    assert set(sampled) == {("2023", "N"), ("2023", "S")}
    assert all(len(group) == 3 for group in sampled.values())
