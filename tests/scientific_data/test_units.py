"""Unit resolution tests: exact factors, refused conversions, reporting shape."""

from __future__ import annotations

import math

import pytest

from jw.scientific_data.contracts import IssueCode
from jw.scientific_data.domain import get_plugin
from jw.scientific_data.units import (
    convert,
    dimension_of,
    is_photometric_system,
    magnitude_system_of,
    resolve_unit,
    unit_conflict_groups,
    unit_inconsistency_issue,
)

SNIA = get_plugin("snia_light_curve")
SOLAR = get_plugin("solar_cycle")


def test_exact_factors_within_one_dimension():
    assert convert(1.0, "Jy", "mJy") == pytest.approx(1000.0)
    assert convert(1000.0, "mJy", "Jy") == pytest.approx(1.0)
    assert convert(1.0, "uJy", "mJy") == pytest.approx(1e-3)
    assert convert(1.0, "\u00b5Jy", "mJy") == pytest.approx(1e-3)
    assert convert(1.0, "km", "m") == pytest.approx(1000.0)
    assert convert(2.0, "hr", "s") == pytest.approx(7200.0)
    assert convert(1.0, "arcmin", "arcsec") == pytest.approx(60.0)
    assert convert(1.0, "deg", "rad") == pytest.approx(math.pi / 180.0)
    assert convert(1.0, "mmag", "mag") == pytest.approx(0.001)
    assert convert(1.0, "percent", "fraction") == pytest.approx(0.01)


def test_compound_units_carry_correct_factors():
    # 1 erg/cm2/s == 1e-3 W/m2
    assert convert(1.0, "erg/cm2/s", "W/m2") == pytest.approx(1e-3)
    # the header form "Flux/10**-15 erg/cm2/s" scales by 1e-15
    assert convert(1.0, "10**-15 erg/cm2/s", "erg/cm2/s") == pytest.approx(1.0e-15)
    assert convert(1.0, "W m-2", "W/m2") == pytest.approx(1.0)
    assert convert(1.0, "km/s", "m/s") == pytest.approx(1000.0)
    assert convert(1.0, "counts/s", "ct/s") == pytest.approx(1.0)


def test_cross_dimension_never_converts():
    assert convert(1.0, "mag", "Jy") is None
    assert convert(1.0, "mJy", "mag") is None
    assert convert(1.0, "Jy", "m") is None
    assert convert(1.0, "erg/cm2/s", "Jy") is None


def test_magnitude_systems_are_isolated():
    assert convert(1.0, "mag", "mag") == pytest.approx(1.0)
    assert convert(1.0, "mag_ab", "mag") is None
    assert convert(1.0, "AB mag", "Vega mag") is None
    assert magnitude_system_of("mag_ab") == "AB"
    assert magnitude_system_of("Vega mag") == "Vega"
    assert magnitude_system_of("mag") == "generic"
    assert is_photometric_system("ABmag") is True
    assert is_photometric_system("mag") is False


def test_offsets_are_refused():
    assert convert(0.0, "degC", "K") is None
    assert convert(1.0, "degF", "K") is None
    assert dimension_of("degC") == ""


def test_unknown_units_are_not_guessed():
    assert convert(1.0, "fluxy_units", "Jy") is None
    assert convert(1.0, "log(Lsun)", "Jy") is None
    assert dimension_of("widget") == ""
    resolution = resolve_unit("counts/s", quantity="apparent_magnitude", plugin=SNIA)
    assert resolution.factor is None
    assert resolution.exact is False


def test_resolve_unit_against_plugin_canonical_units():
    mjy = resolve_unit("mJy", quantity="flux_density", plugin=SNIA)
    assert (mjy.canonical, mjy.factor, mjy.exact) == ("mJy", 1.0, True)
    jy = resolve_unit("Jy", quantity="flux_density", plugin=SNIA)
    assert jy.factor == pytest.approx(1000.0)
    assert jy.exact is True
    ab = resolve_unit("AB mag", quantity="apparent_magnitude", plugin=SNIA)
    assert ab.exact is False
    assert ab.note == "magnitude_system_mismatch"
    good = resolve_unit("mag", quantity="apparent_magnitude", plugin=SNIA)
    assert good.exact is True
    sfu = resolve_unit("sfu", quantity="f107_flux", plugin=SOLAR)
    assert sfu.exact is True
    assert sfu.factor == pytest.approx(1.0)  # canonical is already sfu
    assert convert(1.0, "sfu", "Jy") == pytest.approx(1e4)
    gauss = resolve_unit("G", quantity="polar_field", plugin=SOLAR)
    assert gauss.exact is True
    assert gauss.factor == pytest.approx(1.0)


def test_time_systems_are_not_units():
    assert convert(1.0, "MJD", "days") is None
    # bare durations are units; the column's time *system* is not handled here
    assert resolve_unit("day", quantity="time", plugin=None).exact is True
    # without a canonical target the factor is expressed on the SI base (s)
    assert resolve_unit("s", quantity="time", plugin=None).factor == pytest.approx(1.0)
    assert resolve_unit("day", quantity="time", plugin=None).factor == pytest.approx(
        86400.0
    )
    # "iso_date" is a time system, so unit resolution must refuse it loudly
    refusal = resolve_unit("day", quantity="time", plugin=SNIA)
    assert (refusal.exact, refusal.factor, refusal.note) == (
        False,
        None,
        "canonical_unknown",
    )


def test_g_means_gram_without_a_magnetic_context():
    assert dimension_of("g") == "mass"
    assert convert(1.0, "g", "kg") == pytest.approx(1e-3)


def test_conflict_groups_and_issue_shape():
    rows = [{"unit": "Mjy"}, {"unit": "mJy"}, {"unit": "Jy"}, {"unit": ""}]
    groups = unit_conflict_groups(rows, "unit")
    assert groups["mjy"] == [0, 1]
    assert groups["jy"] == [2]
    assert groups["unspecified"] == [3]
    issue = unit_inconsistency_issue(
        quantity="flux_density", column="unit", groups=groups
    )
    assert issue.code is IssueCode.UNIT_INCONSISTENCY
    assert issue.repair_mode.value == "auto_safe"
    single = unit_inconsistency_issue(
        quantity="flux_density", column="unit", groups={"bogus": [0]}
    )
    assert single.code is IssueCode.UNIT_UNKNOWN
