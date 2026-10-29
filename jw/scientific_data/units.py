"""Deterministic unit resolution for the integration kernel.

Units are resolved into a *dimension vector* over a small set of pseudo-SI
bases (``kg``, ``m``, ``s``, ``K``, ``A``, ``rad``, ``count``, ``fraction``)
plus magnitude and detector pseudo-bases. A conversion is reported as ``exact``
only when both sides share that vector, so every auto-applicable factor is a
dimension-preserving constant.

Rules that must never regress:

* ``factor``/``exact`` only describe registered, dimension-preserving factors.
* Cross-dimension conversion (e.g. magnitude <-> flux density) is never
  automatic: it needs a photometric zero point, so it is reported instead.
* Magnitude systems (AB / Vega / ST / Jerkinsy-style instrumental) are
  different observables and must never be silently unified.
* Offset conversions (degC, degF) are refused: they are not factors.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .contracts import IssueCode, IssueSeverity, QualityIssue, default_repair_mode
from .domain import DomainPlugin, slug

__all__ = [
    "UnitResolution",
    "convert",
    "dimension_of",
    "is_photometric_system",
    "magnitude_system_of",
    "resolve_unit",
    "unit_conflict_groups",
    "unit_inconsistency_issue",
]

_VECTOR = tuple[tuple[str, int], ...]
_EMPTY: _VECTOR = ()

_MAGNITUDE_SYSTEMS: dict[str, str] = {
    "ab": "AB",
    "vega": "Vega",
    "st": "ST",
    "mjerruzzi": "Jeruzzi",
}

# magnitude pseudo-bases: never interchangeable with each other or with physics
_MAG_BASES = {"mag", "mag_ab", "mag_vega", "mag_st"}


def _vec(**powers: int) -> _VECTOR:
    return tuple(sorted((k, v) for k, v in powers.items() if v))


def _mul(base: _VECTOR, factor: float) -> tuple[_VECTOR, float]:
    return base, factor


# --- base vectors -----------------------------------------------------------
_LENGTH = _vec(m=1)
_TIME = _vec(s=1)
_ANGLE = _vec(rad=1)
_MASS = _vec(kg=1)
_ENERGY = _vec(kg=1, m=2, s=-2)
_FLUX = _vec(kg=1, s=-3)  # energy / area / time
_FLUX_DENSITY = _vec(kg=1, s=-2)  # W / m^2 / Hz == Jansky
_MAGNETIC = _vec(kg=1, s=-2, A=-1)  # tesla
_TEMPERATURE = _vec(K=1)
_SPEED = _vec(m=1, s=-1)
_FREQUENCY = _vec(s=-1)
_COUNTS = _vec(count=1)
_FRACTION = _vec(fraction=1)

_ALIASES: dict[str, tuple[_VECTOR, float]] = {
    # length (m)
    "m": _mul(_LENGTH, 1.0),
    "meter": _mul(_LENGTH, 1.0),
    "meters": _mul(_LENGTH, 1.0),
    "metre": _mul(_LENGTH, 1.0),
    "cm": _mul(_LENGTH, 1e-2),
    "centimeter": _mul(_LENGTH, 1e-2),
    "mm": _mul(_LENGTH, 1e-3),
    "km": _mul(_LENGTH, 1e3),
    "um": _mul(_LENGTH, 1e-6),
    "micron": _mul(_LENGTH, 1e-6),
    "microns": _mul(_LENGTH, 1e-6),
    "nm": _mul(_LENGTH, 1e-9),
    "angstrom": _mul(_LENGTH, 1e-10),
    "au": _mul(_LENGTH, 1.495978707e11),
    "astronomicalunit": _mul(_LENGTH, 1.495978707e11),
    "pc": _mul(_LENGTH, 3.0856775814913673e16),
    "kpc": _mul(_LENGTH, 3.0856775814913673e19),
    "mpc": _mul(_LENGTH, 3.0856775814913673e22),
    "ly": _mul(_LENGTH, 9.4607304725808e15),
    # time (s)
    "s": _mul(_TIME, 1.0),
    "sec": _mul(_TIME, 1.0),
    "secs": _mul(_TIME, 1.0),
    "second": _mul(_TIME, 1.0),
    "seconds": _mul(_TIME, 1.0),
    "ms": _mul(_TIME, 1e-3),
    "us": _mul(_TIME, 1e-6),
    "ns": _mul(_TIME, 1e-9),
    "min": _mul(_TIME, 60.0),
    "minute": _mul(_TIME, 60.0),
    "minutes": _mul(_TIME, 60.0),
    "h": _mul(_TIME, 3600.0),
    "hr": _mul(_TIME, 3600.0),
    "hour": _mul(_TIME, 3600.0),
    "hours": _mul(_TIME, 3600.0),
    "d": _mul(_TIME, 86400.0),
    "day": _mul(_TIME, 86400.0),
    "days": _mul(_TIME, 86400.0),
    "week": _mul(_TIME, 604800.0),
    "wk": _mul(_TIME, 604800.0),
    "month": _mul(_TIME, 2629800.0),  # 365.25/12 days, calendar-agnostic
    "yr": _mul(_TIME, 31557600.0),
    "year": _mul(_TIME, 31557600.0),
    "years": _mul(_TIME, 31557600.0),
    "kyr": _mul(_TIME, 3.15576e10),
    "myr": _mul(_TIME, 3.15576e13),
    "gyr": _mul(_TIME, 3.15576e16),
    # angle (rad)
    "rad": _mul(_ANGLE, 1.0),
    "radian": _mul(_ANGLE, 1.0),
    "deg": _mul(_ANGLE, math.pi / 180.0),
    "degree": _mul(_ANGLE, math.pi / 180.0),
    "degrees": _mul(_ANGLE, math.pi / 180.0),
    "arcmin": _mul(_ANGLE, math.pi / 10800.0),
    "arcsec": _mul(_ANGLE, math.pi / 648000.0),
    "arcsecond": _mul(_ANGLE, math.pi / 648000.0),
    "arcseconds": _mul(_ANGLE, math.pi / 648000.0),
    "as": _mul(_ANGLE, math.pi / 648000.0),
    "mas": _mul(_ANGLE, math.pi / 648000000.0),
    # mass
    "kg": _mul(_MASS, 1.0),
    "g": _mul(_MASS, 1e-3),
    "gram": _mul(_MASS, 1e-3),
    "mg": _mul(_MASS, 1e-6),
    # energy / power / flux
    "j": _mul(_ENERGY, 1.0),
    "joule": _mul(_ENERGY, 1.0),
    "erg": _mul(_ENERGY, 1e-7),
    "w": _mul(_vec(kg=1, m=2, s=-3), 1.0),
    "watt": _mul(_vec(kg=1, m=2, s=-3), 1.0),
    "jy": _mul(_FLUX_DENSITY, 1e-26),
    "janskys": _mul(_FLUX_DENSITY, 1e-26),
    "mjy": _mul(_FLUX_DENSITY, 1e-29),
    "ujy": _mul(_FLUX_DENSITY, 1e-32),
    "njy": _mul(_FLUX_DENSITY, 1e-35),
    "sfu": _mul(_FLUX_DENSITY, 1e-22),  # 1 solar flux unit = 1e4 Jy
    # magnetic field
    "t": _mul(_MAGNETIC, 1.0),
    "tesla": _mul(_MAGNETIC, 1.0),
    "gauss": _mul(_MAGNETIC, 1e-4),
    "kilogauss": _mul(_MAGNETIC, 0.1),
    "mgauss": _mul(_MAGNETIC, 1e-7),
    # temperature
    "k": _mul(_TEMPERATURE, 1.0),
    "kelvin": _mul(_TEMPERATURE, 1.0),
    "kev": _mul(_TEMPERATURE, 11604.51812),
    # frequency
    "hz": _mul(_FREQUENCY, 1.0),
    "khz": _mul(_FREQUENCY, 1e3),
    "mhz": _mul(_FREQUENCY, 1e6),
    "ghz": _mul(_FREQUENCY, 1e9),
    "thz": _mul(_FREQUENCY, 1e12),
    "persec": _mul(_FREQUENCY, 1.0),
    "counts_per_second": _mul(_vec(count=1, s=-1), 1.0),
    # speed
    "meters_per_second": _mul(_SPEED, 1.0),
    "kilometers_per_second": _mul(_SPEED, 1e3),
    # dimensionless / counts
    "": _mul(_EMPTY, 1.0),
    "1": _mul(_EMPTY, 1.0),
    "dimensionless": _mul(_EMPTY, 1.0),
    "count": _mul(_COUNTS, 1.0),
    "counts": _mul(_COUNTS, 1.0),
    "ct": _mul(_COUNTS, 1.0),
    "cts": _mul(_COUNTS, 1.0),
    "cts_total": _mul(_COUNTS, 1.0),
    "electron": _mul(_vec(electron=1), 1.0),
    "electrons": _mul(_vec(electron=1), 1.0),
    "photon": _mul(_vec(photon=1), 1.0),
    "photons": _mul(_vec(photon=1), 1.0),
    "adu": _mul(_vec(adu=1), 1.0),
    "dn": _mul(_vec(dn=1), 1.0),
    "fraction": _mul(_FRACTION, 1.0),
    "unit": _mul(_FRACTION, 1.0),
    "percent": _mul(_FRACTION, 0.01),
    "pct": _mul(_FRACTION, 0.001),
    "ppm": _mul(_FRACTION, 1e-6),
    "mmag": _mul(_vec(mag=1), 0.001),
    "cdmag": _mul(_vec(mag=1), 0.01),
    # magnitude systems (pseudo-bases, isolated on purpose)
    "mag": _mul(_vec(mag=1), 1.0),
    "magnitude": _mul(_vec(mag=1), 1.0),
    "mags": _mul(_vec(mag=1), 1.0),
    "mag_ab": _mul(_vec(mag_ab=1), 1.0),
    "abmag": _mul(_vec(mag_ab=1), 1.0),
    "magvega": _mul(_vec(mag_vega=1), 1.0),
    "vegamag": _mul(_vec(mag_vega=1), 1.0),
    "mag_st": _mul(_vec(mag_st=1), 1.0),
    "stmag": _mul(_vec(mag_st=1), 1.0),
    "mag_per_arcsec2": _mul(_vec(mag=1, rad=-2), 1.0),
    "sol/arcsec^2": _mul(_vec(count=1, rad=-2), 1.0),
}

# spellings that must never be treated as a factor: offsets, and units whose
# meaning depends on a zero point or a spectrum.
_REFUSED: dict[str, str] = {
    "c": "celsius_is_offset_not_factor",
    "degc": "celsius_is_offset_not_factor",
    "f": "fahrenheit_is_offset_not_factor",
    "degf": "fahrenheit_is_offset_not_factor",
    "mjd": "time_system_not_unit",
    "jd": "time_system_not_unit",
    "bjd": "time_system_not_unit",
}

# Spellings that legitimately mean several things. They resolve only when the
# observable's canonical unit already fixes the dimension - never as a guess.
_AMBIGUOUS: dict[str, dict[str, tuple[_VECTOR, float]]] = {
    "g": {"magnetic field": (_MAGNETIC, 1e-4)},
    "mg": {"magnetic field": (_MAGNETIC, 1e-7)},
    "kg": {"magnetic field": (_MAGNETIC, 0.1)},
    "as": {"angle": (_ANGLE, math.pi / 648000.0)},
}

_FAILED_NOTES = ("unknown_unit", "unparsable", "celsius", "fahrenheit", "time_system")

_DIMENSION_LABELS: dict[_VECTOR, str] = {
    _LENGTH: "length",
    _TIME: "time",
    _ANGLE: "angle",
    _MASS: "mass",
    _ENERGY: "energy",
    _FLUX: "flux (energy/area/time)",
    _FLUX_DENSITY: "flux density",
    _MAGNETIC: "magnetic field",
    _TEMPERATURE: "temperature",
    _SPEED: "speed",
    _FREQUENCY: "frequency",
    _COUNTS: "counts",
    _FRACTION: "fraction",
    _vec(kg=1, m=2, s=-3): "power",
    _vec(count=1, s=-1): "count rate",
    _vec(mag=1): "magnitude",
    _vec(mag_ab=1): "magnitude (AB)",
    _vec(mag_vega=1): "magnitude (Vega)",
    _vec(mag_st=1): "magnitude (ST)",
    _vec(electron=1): "electrons",
    _vec(photon=1): "photons",
    _vec(adu=1): "adu",
    _vec(dn=1): "digital numbers",
    _EMPTY: "dimensionless",
}

_SUPERSCRIPTS = {
    "\u00b2": "2",
    "\u00b3": "3",
    "\u207b": "-",
    "\u00b9": "1",
    "\u2070": "0",
    "\u2074": "4",
    "\u2075": "5",
    "\u2076": "6",
    "\u2077": "7",
    "\u2078": "8",
    "\u2079": "9",
}

_SYMBOL_FIXES = {
    "\u00b5": "u",  # micro
    "\u03bc": "u",  # mu
    "\u00c5": "angstrom",
    "\u00e5": "angstrom",
    "\u00b0": "deg",
    "\u2032": "arcmin",
    "\u2033": "arcsec",
    "\u00d7": "*",
    "\u00b7": "*",
    "\u2212": "-",
}

_TOKEN_RE = re.compile(
    r"^(?P<prefix>\d+(?:\.\d+)?(?:e[-+]?\d+)?)?(?P<name>[a-z]+)?"
    r"(?:\^?\*?\*?(?P<exp>[-+]?\d+))?$"
)


def _vector_key(vector: _VECTOR) -> str:
    return "*".join(f"{base}^{power}" for base, power in vector)


def _normalise(text: str) -> str:
    """Fold a raw unit string into a lookup-friendly ASCII form."""
    out = str(text or "").strip()
    for bad, good in _SYMBOL_FIXES.items():
        out = out.replace(bad, good)
    out = "".join(_SUPERSCRIPTS.get(ch, ch) for ch in out)
    out = out.replace("**", "^").replace("(-", "(")
    out = re.sub(r"\s*\^\s*\(\s*([-+]?\d+)\s*\)", r"^\1", out)
    out = re.sub(r"\s*\^\s*([-+]?\d+)", r"^\1", out)
    return out.strip()


def _split_tokens(text: str) -> list[tuple[str, int]]:
    """Split ``a*b/c^2`` style text into (name, sign) pairs.

    ``/`` flips the exponent sign for every group to its right until the next
    ``*`` or space group, matching the usual unit grammar.
    """
    tokens: list[tuple[str, int]] = []
    sign = 1
    for chunk in re.split(r"(/)", text):
        if chunk == "/":
            sign = -1
            continue
        for piece in re.split(r"[\s*]+", chunk):
            if piece:
                tokens.append((piece, sign))
    return tokens


def _parse_token(piece: str) -> tuple[str, int, float] | None:
    """Return (name, exponent, numeric prefix) for one atomic token."""
    match = _TOKEN_RE.match(piece)
    if match is None:
        return None
    name = (match.group("name") or "").lower()
    exponent = match.group("exp")
    prefix = match.group("prefix")
    power = int(exponent) if exponent else 1
    scale = float(prefix) if prefix else 1.0
    return name, power, scale


def _lookup(name: str, prefer: str = "") -> tuple[_VECTOR, float] | None:
    if prefer and name in _AMBIGUOUS and prefer in _AMBIGUOUS[name]:
        return _AMBIGUOUS[name][prefer]
    if name in _ALIASES:
        return _ALIASES[name]
    for candidate in (
        name.replace(" ", ""),
        name.replace("-", "").replace("_", ""),
        name.replace(" ", "").replace("-", "").replace("_", ""),
    ):
        if candidate in _ALIASES:
            return _ALIASES[candidate]
    return None


def parse_unit(raw: str, *, prefer: str = "") -> tuple[_VECTOR, float, str]:
    """Parse a unit string into (vector, SI factor, note).

    ``vector`` is ``()`` and ``note`` explains the failure when the unit is not
    in the registry; nothing is guessed. ``prefer`` is a dimension label used
    only to disambiguate spellings that legitimately mean several things
    (``g`` for gauss in a magnetogram column), never as a free guess.
    """
    text = _normalise(raw)
    if not text:
        return _EMPTY, 1.0, "unit_absent"
    probe = text.lower()
    if probe in _REFUSED:
        return (), 0.0, _REFUSED[probe]
    direct = _lookup(probe, prefer)
    if direct is not None:
        return direct[0], direct[1], "exact_alias"
    vector: dict[str, int] = {}
    factor = 1.0
    recognised = 0
    for piece, sign in _split_tokens(text):
        parsed = _parse_token(piece.lower())
        if parsed is None:
            continue
        name, power, scale = parsed
        if not name:
            factor *= scale ** (power * sign)
            continue
        hit = _lookup(name, prefer)
        if hit is None:
            return (), 0.0, f"unknown_unit:{name}"
        base_vector, base_factor = hit
        for base, exp in base_vector:
            vector[base] = vector.get(base, 0) + exp * power * sign
        factor *= base_factor ** (power * sign) * scale ** (power * sign)
        recognised += 1
    if not recognised:
        return (), 0.0, "unparsable_unit"
    normalised = tuple(sorted((b, p) for b, p in vector.items() if p))
    return normalised, factor, "parsed"


def dimension_of(unit: str, *, prefer: str = "") -> str:
    """Best-effort dimension name for a unit spelling ("" when unknown)."""
    vector, _factor, note = parse_unit(unit, prefer=prefer)
    if note.startswith(_FAILED_NOTES):
        return ""
    return _DIMENSION_LABELS.get(vector, _vector_key(vector) or "dimensionless")


def magnitude_system_of(unit: str) -> str:
    """Return "AB"/"Vega"/"ST"/"generic" for magnitude-like units, else ""."""
    vector, _factor, _note = parse_unit(unit)
    for base, _power in vector:
        if base in {"mag", "mag_ab", "mag_vega", "mag_st"}:
            return _MAGNITUDE_SYSTEMS.get(base.removeprefix("mag_"), "generic")
    return ""


def is_photometric_system(unit: str) -> bool:
    """Whether a unit names a magnitude system that must be preserved."""
    token = slug(unit)
    if any(marker in token for marker in _MAGNITUDE_SYSTEMS):
        return True
    system = magnitude_system_of(unit)
    return bool(system) and system != "generic"


@dataclass(frozen=True, slots=True)
class UnitResolution:
    """Outcome of resolving one raw unit string against one observable."""

    raw: str
    canonical: str
    dimension: str
    factor: float | None
    exact: bool
    note: str = ""

    @property
    def unknown(self) -> bool:
        """Whether the unit could not be tied to a known dimension."""
        return not self.dimension or self.factor is None


def resolve_unit(
    raw: str, *, quantity: str, plugin: DomainPlugin | None = None
) -> UnitResolution:
    """Resolve ``raw`` for ``quantity`` under ``plugin``.

    ``factor`` maps ``raw`` onto the observable's canonical unit and is only
    non-``None`` when both share a dimension vector — that is what makes an
    auto-repair legal. ``exact=True`` additionally requires an unambiguous
    spelling, including a matching magnitude system.
    """
    text = str(raw or "").strip()
    canonical = ""
    if plugin is not None:
        spec = plugin.observable(quantity)
        canonical = (spec.canonical_unit or "").strip() if spec is not None else ""
    if not text:
        return UnitResolution(text, canonical, "", None, False, "unit_absent")

    target_vector, target_factor, target_note = (
        parse_unit(canonical) if canonical else ((), 1.0, "")
    )
    prefer = _DIMENSION_LABELS.get(target_vector, "")
    value_vector, value_factor, note = parse_unit(text, prefer=prefer)
    label = dimension_of(text, prefer=prefer)
    if note.startswith(_FAILED_NOTES):
        return UnitResolution(text, canonical, "", None, False, note)

    if canonical:
        if target_note.startswith(_FAILED_NOTES):
            return UnitResolution(
                text, canonical, label, None, False, "canonical_unknown"
            )
        if value_vector == target_vector and target_factor:
            same_system = not _is_magnitude(value_vector) or _mag_system(
                text
            ) == _mag_system(canonical)
            return UnitResolution(
                text,
                canonical,
                label,
                value_factor / target_factor,
                bool(same_system and note in {"exact_alias", "parsed"}),
                "identity" if slug(text) == slug(canonical) else note,
            )
        if _is_magnitude(value_vector) or _is_magnitude(target_vector):
            both_mag = _is_magnitude(value_vector) and _is_magnitude(target_vector)
            return UnitResolution(
                text,
                canonical,
                label,
                None,
                False,
                "magnitude_system_mismatch"
                if both_mag
                else "magnitude_needs_zero_point",
            )
        return UnitResolution(text, canonical, label, None, False, note)

    return UnitResolution(
        text, canonical, label, value_factor, note == "exact_alias", note
    )


def _is_magnitude(vector: _VECTOR) -> bool:
    return any(base in _MAG_BASES for base, _power in vector) and not any(
        base in {"kg", "m", "s"} for base, _power in vector
    )


def _mag_system(raw: str) -> str:
    vector, _factor, _note = parse_unit(raw)
    for base, _power in vector:
        if base in _MAG_BASES:
            return base
    return ""


def convert(value: float, src: str, dst: str) -> float | None:
    """Convert ``value`` from ``src`` to ``dst``; ``None`` when not exact."""
    if value is None:
        return None
    src_vector, src_factor, src_note = parse_unit(src)
    dst_vector, dst_factor, dst_note = parse_unit(dst)
    if src_note.startswith(("unknown_unit", "unparsable", "celsius", "fahrenheit")):
        return None
    if dst_note.startswith(("unknown_unit", "unparsable", "celsius", "fahrenheit")):
        return None
    if src_vector != dst_vector:
        return None
    if _is_magnitude(src_vector) and _mag_system(src) != _mag_system(dst):
        return None
    if not dst_factor:
        return None
    return float(value) * src_factor / dst_factor


def unit_conflict_groups(
    rows: Iterable[Mapping[str, Any]], column: str
) -> dict[str, list[int]]:
    """Group row indexes by the unit claimed in ``column`` (for reporting)."""
    groups: dict[str, list[int]] = {}
    for index, row in enumerate(rows):
        key = slug(str(row.get(column, "") or "")) or "unspecified"
        groups.setdefault(key, []).append(index)
    return groups


def unit_inconsistency_issue(
    *, quantity: str, column: str, groups: Mapping[str, Sequence[int]]
) -> QualityIssue:
    """One issue describing several unit spellings used for one observable."""
    code = IssueCode.UNIT_INCONSISTENCY if len(groups) > 1 else IssueCode.UNIT_UNKNOWN
    return QualityIssue(
        issue_id="",
        code=code,
        severity=default_severity(code),
        detail=f"{column}: {len(groups)} unit forms for {quantity}",
        evidence={
            "quantity": quantity,
            "units": {k: list(v) for k, v in groups.items()},
        },
        repair_mode=default_repair_mode(code),
    )


def default_severity(code: IssueCode) -> IssueSeverity:
    return (
        IssueSeverity.ERROR
        if code is IssueCode.UNIT_INCONSISTENCY
        else IssueSeverity.WARNING
    )
