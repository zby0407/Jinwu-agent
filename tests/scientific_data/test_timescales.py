"""Time-system tests: calendar anchors, envelope refusal, ambiguity reporting."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from jw.scientific_data.contracts import IssueCode, RepairMode
from jw.scientific_data.domain import get_plugin
from jw.scientific_data.timescales import (
    detect_time_system,
    iso_to_mjd,
    julian_to_mjd,
    mjd_to_iso,
    mjd_to_julian,
    normalise_phase,
    time_system_issue,
    to_time,
)

SNIA = get_plugin("snia_light_curve")


def test_documented_calendar_anchors():
    assert iso_to_mjd("2000-01-01T12:00:00Z") == pytest.approx(51544.5, abs=1e-9)
    assert iso_to_mjd("1970-01-01T00:00:00Z") == pytest.approx(40587.0, abs=1e-9)
    assert julian_to_mjd(2451545.0) == pytest.approx(51544.5, abs=1e-9)
    assert mjd_to_julian(51544.5) == pytest.approx(2451545.0, abs=1e-9)
    assert iso_to_mjd("1858-11-17") == pytest.approx(0.0, abs=1e-9)
    assert mjd_to_iso(51544.5) == "2000-01-01T12:00:00.000000Z"
    assert mjd_to_iso(40587.0) == "1970-01-01"


def test_leap_years_and_gregorian_boundaries():
    assert iso_to_mjd("2000-02-29") is not None
    assert iso_to_mjd("1900-02-28") is not None
    assert iso_to_mjd("1900-02-29") is None
    assert iso_to_mjd("2100-02-28") is not None
    assert iso_to_mjd("2100-02-29") is None
    assert iso_to_mjd("2024-02-29") is not None
    # 1900 is not a leap year: Feb 28 is directly followed by Mar 1
    assert iso_to_mjd("1900-03-01") - iso_to_mjd("1900-02-28") == pytest.approx(1.0)
    assert iso_to_mjd("2000-13-01") is None
    assert iso_to_mjd("2000-04-31") is None


def test_timezone_fractional_seconds_and_negative_mjd():
    beijing = iso_to_mjd("2000-01-01T12:00:00+08:00")
    assert beijing == pytest.approx(51544.5 - 8 / 24, abs=1e-9)
    assert iso_to_mjd("2000-01-01T12:00:00Z") - beijing == pytest.approx(
        8 / 24, abs=1e-9
    )
    assert mjd_to_iso(51544.5, with_time=False) == "2000-01-01"
    # the fraction is of a second, not of a day
    assert iso_to_mjd("2000-01-01T12:00:00.500Z") == pytest.approx(
        51544.5 + 0.5 / 86400, abs=1e-9
    )
    assert mjd_to_iso(-5000.0, with_time=False) == "1845-03-10"
    assert to_time("1800-01-01", time_system="iso").mjd == pytest.approx(
        -21504.0, abs=1e-9
    )


@pytest.mark.parametrize(
    ("mjd", "iso"),
    [
        (51544.5, "2000-01-01T12:00:00.000000Z"),
        (54000.0, "2006-09-22"),
        (60000.25, "2023-02-25T06:00:00.000000Z"),
        (40587.0, "1970-01-01"),
    ],
)
def test_iso_round_trips_are_stable(mjd, iso):
    parsed = to_time(iso, time_system="iso")
    assert parsed.mjd == pytest.approx(mjd, abs=1e-6)
    again = to_time(parsed.iso, time_system="iso")
    assert again.mjd == pytest.approx(parsed.mjd, abs=1e-9)


def test_declared_systems_resolve_numeric_values():
    mjd_row = to_time("51544.5", time_system="mjd")
    assert (mjd_row.time_system, mjd_row.ambiguous) == ("mjd", False)
    assert mjd_row.mjd == pytest.approx(51544.5, abs=1e-9)

    jd_row = to_time("2451545.0", time_system="jd")
    assert jd_row.mjd == pytest.approx(51544.5, abs=1e-9)

    bjd = to_time("2455000.25", time_system="bjd")
    assert bjd.mjd == pytest.approx(54999.75, abs=1e-9)
    assert bjd.scale == "utc"  # BTD_TDB is recorded, never silently rescaled


def test_bare_numbers_inside_the_envelope_stay_ambiguous():
    resolved = to_time("51544.5", time_system="", plugin=None)
    assert resolved.ambiguous is True
    assert resolved.note == "mjd_by_envelope_needs_confirmation"
    assert resolved.mjd == pytest.approx(51544.5, abs=1e-9)
    # 48000 is also a plausible reduced-JD value, so the note must say so
    assert to_time("48000", time_system="rjd").ambiguous is True


def test_unsafe_numbers_are_not_invented():
    assert to_time("12.5", time_system="mjd").ambiguous is True
    assert to_time("12.5", time_system="mjd").mjd is None
    assert to_time("not a date", time_system="iso").note == "unparsable"
    assert to_time("", time_system="iso").note == "empty"
    assert to_time("2000-02-30", time_system="iso").note == "invalid_calendar_date"


def test_relative_clocks_need_a_reference_epoch():
    without = to_time("3400.5", time_system="seconds")
    assert (without.mjd, without.ambiguous, without.note) == (
        None,
        True,
        "no_reference_epoch",
    )
    with_ref = to_time("3400.5", time_system="seconds", reference_mjd=51544.5)
    assert with_ref.mjd == pytest.approx(51544.5 + 3400.5 / 86400, abs=1e-9)
    assert not with_ref.ambiguous
    assert to_time("1.5", time_system="days_relative").ambiguous is True
    assert to_time(
        "1.5", time_system="days_relative", reference_mjd=51544.5
    ).mjd == pytest.approx(51546.0, abs=1e-9)


def test_textual_relative_cells_need_the_unit_and_reference():
    resolved = to_time("12.4 days since maximum", time_system="")
    assert resolved.mjd is None
    assert resolved.ambiguous is True
    assert resolved.note == "relative_to:maximum"
    anchored = to_time("12.4 days since maximum", time_system="", reference_mjd=51544.5)
    assert anchored.mjd == pytest.approx(51544.5 + 12.4, abs=1e-6)


def test_phases_only_become_absolute_when_period_and_epoch_exist():
    bare = normalise_phase(0.25, raw="0.25")
    assert (bare.mjd, bare.ambiguous) == (None, True)
    assert bare.note == "needs_period_and_reference"
    resolved = normalise_phase(0.25, period_days=20.0, reference_mjd=51544.5)
    assert resolved.mjd == pytest.approx(51549.5, abs=1e-9)
    assert resolved.note == "phase_applied"
    wrapped = normalise_phase(1.3, period_days=10.0, reference_mjd=51544.5)
    assert wrapped.note == "phase_wrapped"
    assert wrapped.mjd == pytest.approx(51547.5, abs=1e-9)
    negative = normalise_phase(-0.2, period_days=10.0, reference_mjd=51544.5)
    assert negative.mjd == pytest.approx(51542.5, abs=1e-9)
    assert to_time("0.3", time_system="phase").ambiguous is True


def test_detect_time_system_reads_headers():
    assert detect_time_system("Time (MJD)") == "mjd"
    assert detect_time_system("HJD - 2400000") == "jd"
    assert detect_time_system("BJD_TDB") == "bjd"
    assert detect_time_system("Phase") == "phase"
    assert detect_time_system("s since trigger") == "seconds"
    assert detect_time_system("days from maximum") == "days_relative"
    assert detect_time_system("2019-04-25") == "iso"
    assert detect_time_system("Flux") == ""
    assert detect_time_system("Rest-frame day") == "rest_frame"
    assert detect_time_system("days (rest-frame)") == "rest_frame"
    assert detect_time_system("Rest-frame wavelength") == ""


def test_plugin_preferred_system_is_used_when_the_header_is_silent():
    resolved = to_time("51544.5", time_system="", plugin=SNIA)
    assert resolved.time_system == "mjd"
    assert resolved.mjd == pytest.approx(51544.5, abs=1e-9)


def test_time_system_issue_is_human_required():
    issue = time_system_issue(
        column="time", declared="mjd", resolved=4, total=9, note="mixed_forms"
    )
    assert issue.code is IssueCode.TIME_SYSTEM_AMBIGUOUS
    assert issue.repair_mode is RepairMode.HUMAN_REQUIRED
    assert issue.blocking is False
    assert "4/9" in issue.detail


def test_matches_the_stdlib_calendar_oracle():
    """iso_to_mjd / mjd_to_iso must agree with datetime over 3 centuries."""
    epoch = date(1858, 11, 17)
    for offset in range(-40000, 62000, 761):
        day = epoch + timedelta(days=offset)
        expected = float(offset)
        assert iso_to_mjd(day.isoformat()) == pytest.approx(expected, abs=1e-9)
        assert mjd_to_iso(expected, with_time=False) == day.isoformat()
    # 24 hours expressed as MJD fractions round-trips through the ISO form
    for fraction in (0.0, 0.125, 0.5, 0.999):
        mjd = 51544.0 + fraction
        resolved = to_time(mjd_to_iso(mjd), time_system="iso")
        assert resolved.mjd == pytest.approx(mjd, abs=1e-5)


def test_phase_at_the_half_cycle_never_silently_picks_a_convention():
    boundary = normalise_phase(0.5, period_days=20.0, reference_mjd=51544.5)
    assert boundary.ambiguous is True
    assert boundary.mjd is None
    assert boundary.note == "phase_at_boundary_needs_convention"
    assert normalise_phase(-0.5, period_days=20.0, reference_mjd=51544.5).mjd is None


def test_relative_before_is_negative_and_units_are_respected():
    anchor = 51544.5
    assert to_time(
        "2.5 hours before trigger", time_system="", reference_mjd=anchor
    ).mjd == pytest.approx(anchor - 2.5 / 24, abs=1e-9)
    assert to_time("90 s after peak", time_system="", reference_mjd=anchor).mjd == (
        pytest.approx(anchor + 90 / 86400, abs=1e-9)
    )
    assert to_time("12.4 days since maximum", time_system="").time_system == (
        "days_relative"
    )


def test_reduced_precision_month_stamps_are_recognised():
    assert detect_time_system("2020-01") == "iso"
    assert detect_time_system("2020M01") == "iso"
    assert detect_time_system("2020m01") == "iso"
    assert detect_time_system("2020-13") == ""
    # A monthly stamp names a bucket, not an instant: no MJD is invented.
    assert iso_to_mjd("2020-01") is None
