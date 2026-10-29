"""Deterministic calendar/time-system conversions for the integration kernel.

No astropy, no locale parsing, no wall-clock dependencies: calendar dates use
the Fliegel-Van Flandern integer algorithm, every conversion is a rational
scale change, and anything ambiguous comes back with ``ambiguous=True`` instead
of a guessed value.

Supported systems (see ``DomainPlugin.time_systems``):

* ``mjd``  Modified Julian Date (JD - 2400000.5), the kernel's default.
* ``jd``   Julian Date.
* ``bjd``  BJD_TDB when a source states it; recorded, never converted (the
  difference from JD depends on the observer's ephemeris).
* ``iso``  ISO-8601 UTC date or date-time.
* ``seconds`` / ``days_relative``: relative clocks, e.g. ``s since trigger``.
* ``phase`` phase relative to a reference epoch (required epoch must exist).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from jw.scientific_data.contracts import (
    IssueCode,
    IssueSeverity,
    QualityIssue,
    default_repair_mode,
)
from jw.scientific_data.domain import DomainPlugin

__all__ = [
    "TimeResolution",
    "detect_time_system",
    "iso_to_mjd",
    "julian_to_mjd",
    "mjd_to_iso",
    "mjd_to_julian",
    "normalise_phase",
    "to_time",
]

_MJD_OFFSET = 2400000.5
_JD_UNIX = 2440587.5  # 1970-01-01T00:00:00Z
_SECONDS_PER_DAY = 86400.0
_JDN_MINUS_ORDINAL = 1721425  # JD noon-day number vs proleptic ordinal
_ISO_RE = re.compile(
    r"^(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})"
    r"(?:[T ](?P<h>\d{2}):(?P<min>\d{2})(?::(?P<s>\d{2})(?:\.(?P<frac>\d+))?)?"
    r"(?P<tz>Z|[+-]\d{2}:?\d{2})?)?$"
)
# Reduced-precision ISO-8601 stamps (monthly means such as "2020-01" or
# SILSO's "2020M01"). They mark a time axis but cannot be turned into an
# instant without a cadence convention, so only detection accepts them.
_ISO_MONTH_RE = re.compile(
    r"^(?P<y>\d{4})(?:-(?P<m>\d{2})|m(?P<m2>\d{2}))$", re.IGNORECASE
)
_RELATIVE_RE = re.compile(
    r"^\s*(?P<num>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*"
    r"(?P<unit>s|sec|secs|second|seconds|min|mins|minutes?"
    r"|h|hr|hrs|hour|hours|d|day|days|ks|ms|us)\s+"
    r"(?P<connector>since|from|after|before|prior\s+to|prior\s+to)\s+"
    r"(?P<ref>.+?)\s*$",
    re.IGNORECASE,
)

_UNIT_SECONDS = {
    "s": 1.0,
    "sec": 1.0,
    "second": 1.0,
    "seconds": 1.0,
    "min": 60.0,
    "minute": 60.0,
    "minutes": 60.0,
    "h": 3600.0,
    "hr": 3600.0,
    "hour": 3600.0,
    "hours": 3600.0,
    "d": 86400.0,
    "day": 86400.0,
    "days": 86400.0,
    "ks": 1000.0,
    "ms": 0.001,
    "us": 1e-6,
}


@dataclass(frozen=True, slots=True)
class TimeResolution:
    """Normalised time for one cell, with the evidence needed to audit it."""

    raw: str
    iso: str
    mjd: float | None
    jd: float | None
    time_system: str
    scale: str = "utc"
    ambiguous: bool = False
    note: str = ""

    @property
    def value(self) -> float | None:
        """The preferred numeric form (MJD), or ``None`` when unresolved."""
        return self.mjd


def _date_to_jd(year: int, month: int, day: int) -> float:
    """Gregorian calendar date to the Julian Day of its midnight (00:00 UT).

    Uses the proleptic Gregorian ordinal from the standard library, anchored so
    that 2000-01-01T12:00:00Z is JD 2451545.0 / MJD 51544.5 exactly.
    """
    return date(year, month, day).toordinal() + _JDN_MINUS_ORDINAL - 0.5


def _jd_to_ordinal(jd: float) -> tuple[int, float]:
    """Split a Julian Day into (calendar day ordinal, fraction of that day)."""
    jdn = math.floor(jd + 0.5)
    fraction = jd + 0.5 - jdn
    if fraction >= 1.0:
        jdn += 1
        fraction -= 1.0
    return jdn - _JDN_MINUS_ORDINAL, fraction


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _validate_date(year: int, month: int, day: int) -> str:
    """Return "" for a real calendar date, else a short machine-readable note."""
    if not 1 <= month <= 12:
        return f"month_out_of_range:{month}"
    limit = 28 if month == 2 else {4: 30, 6: 30, 9: 30, 11: 30}.get(month, 31)
    if month == 2 and _is_leap(year):
        limit = 29
    if not 1 <= day <= limit:
        return f"day_out_of_range:{day}"
    return ""


def iso_to_mjd(text: str) -> float | None:
    """ISO date or date-time to MJD (``None`` when unparsable)."""
    match = _ISO_RE.match(str(text or "").strip())
    if match is None:
        return None
    parts = match.groupdict()
    year, month, day = int(parts["y"]), int(parts["m"]), int(parts["d"])
    if _validate_date(year, month, day):
        return None
    hour = int(parts["h"] or 0)
    minute = int(parts["min"] or 0)
    second = int(parts["s"] or 0)
    fraction = float("0." + parts["frac"]) if parts["frac"] else 0.0
    tz = parts["tz"] or ""
    offset_seconds = 0.0
    if tz and tz != "Z":
        sign = 1 if tz[0] == "+" else -1
        digits = re.sub(r"\D", "", tz)
        offset_seconds = sign * (int(digits[:2]) * 3600 + int(digits[2:4]) * 60)
    day_fraction = (
        hour * 3600 + minute * 60 + second + fraction - offset_seconds
    ) / _SECONDS_PER_DAY
    return julian_to_mjd(_date_to_jd(year, month, day) + day_fraction)


def mjd_to_iso(mjd: float | None, *, with_time: bool = True) -> str:
    """MJD to an ISO-8601 UTC string ("" when ``mjd`` is ``None`` or out of range)."""
    if mjd is None:
        return ""
    jd = float(mjd) + _MJD_OFFSET
    try:
        ordinal, fraction = _jd_to_ordinal(jd)
        day = date.fromordinal(ordinal)
    except (ValueError, OverflowError):
        return ""
    seconds = round(fraction * _SECONDS_PER_DAY, 6)
    if seconds >= _SECONDS_PER_DAY:  # float noise pushed us into the next day
        seconds -= _SECONDS_PER_DAY
        day = day + timedelta(days=1)
    if not with_time or seconds <= 1e-9:
        return day.isoformat()
    hour = int(seconds // 3600)
    minute = int((seconds % 3600) // 60)
    rest = seconds % 60
    return f"{day.isoformat()}T{hour:02d}:{minute:02d}:{rest:09.6f}Z"


def julian_to_mjd(jd: float) -> float:
    """Julian Date to Modified Julian Date."""
    return float(jd) - _MJD_OFFSET


def mjd_to_julian(mjd: float) -> float:
    """Modified Julian Date to Julian Date."""
    return float(mjd) + _MJD_OFFSET


def _month_stamp(text: str) -> tuple[int, int] | None:
    """Parse a reduced-precision month stamp into ``(year, month)``."""
    match = _ISO_MONTH_RE.match(str(text or "").strip())
    if match is None:
        return None
    month = int(match.group("m") or match.group("m2"))
    if not 1 <= month <= 12:
        return None
    return int(match.group("y")), month


def detect_time_system(raw: str) -> str:
    """Infer a declared time system from a header or value string ("" if none).

    Only explicit markers count; a bare number stays unassigned so alignment
    can ask the user instead of guessing between MJD and JD.
    """
    text = str(raw or "").strip().lower()
    if not text:
        return ""
    if any(marker in text for marker in ("reduced jd", "rjd")):
        return "rjd"
    for system, markers in (
        ("bjd", ("bjd", "besselian")),
        ("mjd", ("mjd", "modified julian")),
        ("jd_tt", ("jd(tt", "jd tdb", "jd tt")),
        ("jd", ("jd", "julian")),
        ("phase", ("phase",)),
    ):
        if any(marker in text for marker in markers):
            return system
    if _ISO_RE.match(text) or _month_stamp(text):
        return "iso"
    for unit in ("s since", "days since", "days from", "seconds since", "hr since"):
        if unit in text:
            return "days_relative" if "day" in unit else "seconds"
    flat = re.sub(r"\s+", " ", re.sub(r"[()\[\]_-]+", " ", text))
    if any(
        marker in flat
        for marker in ("rest day", "days rest", "rest frame day", "observer frame day")
    ):
        return "rest_frame"
    return ""


def _relative_seconds(text: str) -> tuple[float, str, str] | None:
    """Resolve ``<number> <unit> since <reference>`` into (seconds, reference).

    ``seconds`` is negative for ``before``/``prior to`` so the caller only has
    to add it to the reference epoch.
    """
    match = _RELATIVE_RE.match(text)
    if match is None:
        return None
    scale = _UNIT_SECONDS.get(match.group("unit").lower())
    if scale is None:
        return None
    connector = re.sub(r"\s+", " ", match.group("connector").lower())
    sign = -1.0 if connector in {"before", "prior to"} else 1.0
    seconds = sign * float(match.group("num")) * scale
    reference = match.group("ref").strip().lower()
    return seconds, reference, match.group("unit").lower()


def to_time(
    raw: Any,
    *,
    time_system: str = "",
    plugin: DomainPlugin | None = None,
    reference_mjd: float | None = None,
) -> TimeResolution:
    """Resolve one time cell to ISO + MJD + JD, or explain why it cannot.

    ``time_system`` is what the column header declared; when it is empty only
    unambiguous forms (ISO dates, and values inside the MJD/JD numeric
    envelope) are resolved, and the result is marked ``ambiguous`` for the rest.
    """
    text = str(raw if raw is not None else "").strip()
    declared = (
        time_system or (plugin.preferred_time_system if plugin else "") or ""
    ).lower()
    if not text:
        return TimeResolution(
            text, "", None, None, declared, ambiguous=True, note="empty"
        )

    iso_direct = _ISO_RE.match(text)
    if iso_direct is not None:
        mjd = iso_to_mjd(text)
        if mjd is None:
            return TimeResolution(
                text,
                "",
                None,
                None,
                "iso",
                ambiguous=True,
                note="invalid_calendar_date",
            )
        return TimeResolution(
            text,
            mjd_to_iso(mjd),
            mjd,
            mjd_to_julian(mjd),
            "iso",
            note="iso_date",
        )

    try:
        number = float(text.replace(",", "").replace(" ", ""))
    except ValueError:
        number = float("nan")

    if number == number:
        if declared in {"mjd", ""} and 40000.0 <= number <= 60000.0:
            mjd = number
            note = "mjd_by_envelope" if not declared else "mjd"
            if declared == "":
                return TimeResolution(
                    text,
                    mjd_to_iso(mjd),
                    mjd,
                    mjd_to_julian(mjd),
                    "mjd",
                    ambiguous=True,
                    note="mjd_by_envelope_needs_confirmation",
                )
            return TimeResolution(
                text, mjd_to_iso(mjd), mjd, mjd_to_julian(mjd), "mjd", note=note
            )
        if declared in {"jd", "bjd", "jd_tt"} and 2400000.0 <= number <= 2460000.0:
            mjd = julian_to_mjd(number)
            return TimeResolution(
                text, mjd_to_iso(mjd), mjd, mjd_to_julian(mjd), declared, note=declared
            )
        if declared in {"seconds", "relative_seconds"}:
            seconds = number / _SECONDS_PER_DAY
            if reference_mjd is None:
                return TimeResolution(
                    text,
                    "",
                    None,
                    None,
                    "seconds",
                    ambiguous=True,
                    note="no_reference_epoch",
                )
            mjd = reference_mjd + seconds
            return TimeResolution(
                text,
                mjd_to_iso(mjd),
                mjd,
                mjd_to_julian(mjd),
                "seconds",
                note="relative",
            )
        if declared in {"days_relative", "days_since"}:
            if reference_mjd is None:
                return TimeResolution(
                    text,
                    "",
                    None,
                    None,
                    "days_relative",
                    ambiguous=True,
                    note="no_reference_epoch",
                )
            mjd = reference_mjd + number
            return TimeResolution(
                text,
                mjd_to_iso(mjd),
                mjd,
                mjd_to_julian(mjd),
                "days_relative",
                note="relative",
            )
        if declared in {"phase"}:
            return normalise_phase(number, reference_mjd=reference_mjd, raw=text)
        if declared in {"rest_frame"}:
            return TimeResolution(
                text,
                "",
                None,
                None,
                "rest_frame",
                ambiguous=True,
                note="needs_redshift",
            )
        # RJD and a bare MJD written as "MJD-54000" stay unresolved on purpose
        return TimeResolution(
            text,
            "",
            None,
            None,
            declared or "unknown",
            ambiguous=True,
            note="envelope_unknown",
        )

    relative = _relative_seconds(text)
    if relative is not None:
        seconds, reference, unit = relative
        system = "days_relative" if unit in {"d", "day", "days"} else "seconds"
        if reference_mjd is None:
            return TimeResolution(
                text,
                "",
                None,
                None,
                system,
                ambiguous=True,
                note=f"relative_to:{reference}",
            )
        mjd = reference_mjd + seconds / _SECONDS_PER_DAY
        return TimeResolution(
            text, mjd_to_iso(mjd), mjd, mjd_to_julian(mjd), system, note="relative"
        )

    return TimeResolution(
        text, "", None, None, declared or "unknown", ambiguous=True, note="unparsable"
    )


def normalise_phase(
    value: float,
    *,
    period_days: float | None = None,
    reference_mjd: float | None = None,
    raw: str = "",
) -> TimeResolution:
    """Turn a phase in ``[-0.5, 0.5)`` or ``[0, 1)`` into an absolute time.

    Both a period and a reference epoch are required; without them the phase is
    reported as relative and unresolved rather than invented.
    """
    if period_days is None or reference_mjd is None:
        return TimeResolution(
            raw or str(value),
            "",
            None,
            None,
            "phase",
            ambiguous=True,
            note="needs_period_and_reference",
        )
    phase = float(value)
    note = "phase_applied"
    if phase >= 1.0 or phase <= -1.0:
        phase = phase % 1.0
        note = "phase_wrapped"
    if abs(abs(phase) - 0.5) < 1e-12:
        return TimeResolution(
            raw or str(value),
            "",
            None,
            None,
            "phase",
            ambiguous=True,
            note="phase_at_boundary_needs_convention",
        )
    if phase > 0.5:
        note = "phase_zero_to_one"
    elif phase < 0.0:
        note = "phase_minus_half_to_half"

    mjd = reference_mjd + phase * float(period_days)
    return TimeResolution(
        raw or str(value), mjd_to_iso(mjd), mjd, mjd_to_julian(mjd), "phase", note=note
    )


def time_system_issue(
    *, column: str, declared: str, resolved: int, total: int, note: str
) -> QualityIssue:
    """A TIME_SYSTEM_AMBIGUOUS issue describing one time column."""
    severity = (
        IssueSeverity.ERROR if resolved and resolved < total else IssueSeverity.WARNING
    )
    return QualityIssue(
        issue_id="",
        code=IssueCode.TIME_SYSTEM_AMBIGUOUS,
        severity=severity,
        detail=f"{column}: declared {declared or 'unknown'}, {resolved}/{total} resolved ({note})",
        evidence={
            "column": column,
            "declared": declared,
            "resolved": resolved,
            "total": total,
            "note": note,
        },
        repair_mode=default_repair_mode(IssueCode.TIME_SYSTEM_AMBIGUOUS),
    )
