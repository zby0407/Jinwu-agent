"""Domain plugins for the Scientific Data Integration kernel.

A plugin supplies the *domain knowledge* the generic kernel must not hard
code: which observables exist, how source columns map onto them, how entities
are identified, which units and value ranges are physically plausible, and
which extra checks are specific to the field.

The kernel ships with:

* ``generic`` – schema-agnostic fallback that maps any table by header
  heuristics and never assumes a time column or a sampling cadence.
* ``snia_light_curve`` – Type Ia supernova photometry: magnitude/flux
  observables, MJD/phase time systems, band-aware merging.
* ``solar_cycle`` – thin adapter over the existing solar constants so the
  legacy Solar pipeline keeps its semantics without re-implementing them.

Plugins are pure data plus small pure functions: they must not do I/O, call a
model, or mutate the ledger.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .contracts import (
    ConfidenceBand,
    IssueCode,
    IssueSeverity,
    QualityIssue,
    default_repair_mode,
)

__all__ = [
    "DomainPlugin",
    "get_plugin",
    "list_plugins",
    "plugin_for_contract",
    "register_plugin",
]

_IDENT = re.compile(r"[^a-z0-9]+")


def slug(value: str) -> str:
    """Lower-snake identifier safe for column and observable names."""
    return _IDENT.sub("_", str(value or "").strip().lower()).strip("_")


def normalise_identifier(value: str) -> str:
    """Identifier form used for alias equality (case/punctuation/space-free)."""
    return re.sub(r"[\s_\-\.]+", "", slug(value))


# ---------------------------------------------------------------------------
# Plugin definition
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ObservableSpec:
    """One canonical observable a plugin understands."""

    name: str
    kind: str = "quantity"  # quantity | time | identifier | label | quality
    canonical_unit: str = ""
    description: str = ""
    header_hints: tuple[str, ...] = ()
    unit_hints: tuple[str, ...] = ()
    required: bool = False
    value_range: tuple[float, float] | None = None
    # magnitude systems must never be silently converted; flagged instead
    strict_unit: bool = False


@dataclass(frozen=True, slots=True)
class EntitySpec:
    """How one object of study is identified and merged across sources."""

    key_hints: tuple[str, ...] = ()
    alias_hints: tuple[str, ...] = ()
    coord_hints: Mapping[str, str] = field(default_factory=dict)
    # crossmatch tolerance in arcsec; None disables coordinate matching
    coord_tolerance_arcsec: float | None = None
    require_explicit_match: bool = True


@dataclass(frozen=True, slots=True)
class DomainPlugin:
    """Domain knowledge bundle consumed by every kernel stage."""

    name: str
    title: str = ""
    observables: tuple[ObservableSpec, ...] = ()
    entity: EntitySpec = field(default_factory=EntitySpec)
    time_systems: tuple[str, ...] = ("iso_date", "mjd", "jd", "seconds_epoch")
    preferred_time_system: str = "iso_date"
    merge_keys: tuple[str, ...] = ("entity_id", "observable", "time")
    allowed_unit_conversions: Mapping[str, Mapping[str, float]] = field(
        default_factory=dict
    )
    cadence: Mapping[str, Any] = field(default_factory=dict)
    plausibility: Mapping[str, tuple[float, float]] = field(default_factory=dict)
    synonym_overrides: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    # plugin-specific extra checks: callable(ledger) -> tuple[QualityIssue, ...]
    extra_checks: tuple[tuple[str, Any], ...] = ()

    def observable(self, name: str) -> ObservableSpec | None:
        for spec in self.observables:
            if spec.name == name:
                return spec
        return None

    def observable_names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.observables)

    def hints_for(self, name: str) -> tuple[str, ...]:
        """Header hints for one observable, with plugin overrides merged in."""
        spec = self.observable(name)
        base = tuple(spec.header_hints) if spec else ()
        return base + tuple(self.synonym_overrides.get(name, ()))

    def convert_units(
        self, quantity: str, value: float, unit: str
    ) -> tuple[float, str]:
        """Deterministic unit conversion for registered factors only.

        Returns ``(value, unit)`` unchanged when no factor is registered — an
        unregistered unit is never guessed, it is reported as an issue by the
        alignment stage.
        """
        factors = self.allowed_unit_conversions.get(quantity) or {}
        factor = factors.get(slug(unit))
        if factor is None or value is None:
            return value, unit
        return value * factor, next(
            (spec.canonical_unit for spec in self.observables if spec.name == quantity),
            unit,
        )

    def run_extra_checks(self, ledger: Any) -> tuple[QualityIssue, ...]:
        """Execute plugin-specific checks; failures degrade to no issues."""
        produced: list[QualityIssue] = []
        for _name, check in self.extra_checks:
            try:
                produced.extend(tuple(check(ledger)) or ())
            except Exception:  # a plugin check must never crash a run
                continue
        return tuple(produced)


# ---------------------------------------------------------------------------
# Built-in plugins
# ---------------------------------------------------------------------------

_PHOTOMETRY = (
    ObservableSpec(
        name="apparent_magnitude",
        kind="quantity",
        canonical_unit="mag",
        description="Author-reported apparent magnitude in a named band.",
        header_hints=(
            "mag",
            "magnitude",
            "apparent magnitude",
            "appmag",
            "mag_obs",
            "observed magnitude",
            "ptf_obs_mag",
            "reduced magnitude",
            "magr",
            "magb",
            "magv",
            "星等",
            "视星等",
        ),
        unit_hints=("mag", "magnitudes", "mmag"),
        required=True,
        value_range=(-3.0, 30.0),
        strict_unit=True,
    ),
    ObservableSpec(
        name="magnitude_uncertainty",
        kind="quality",
        canonical_unit="mag",
        description="1-sigma uncertainty attached to apparent_magnitude.",
        header_hints=(
            "mag_err",
            "e_mag",
            "magnitude error",
            "err",
            "sigma_mag",
            "星等误差",
            "误差",
        ),
        unit_hints=("mag",),
    ),
    ObservableSpec(
        name="flux_density",
        kind="quantity",
        canonical_unit="mJy",
        description=" Flux density measurement where magnitudes are not given.",
        header_hints=(
            "flux",
            "flux density",
            "f_nu",
            "count rate",
            "流量",
            "通量",
            "流量密度",
        ),
        unit_hints=("jy", "mjy", "ujy", "erg/s/cm2/angstrom"),
    ),
    ObservableSpec(
        name="band",
        kind="label",
        description="Photometric band or filter the measurement belongs to.",
        header_hints=(
            "band",
            "filter",
            "passband",
            "bandpass",
            "photband",
            "flt",
            "filt",
            "滤光片",
            "波段",
        ),
    ),
    ObservableSpec(
        name="time",
        kind="time",
        canonical_unit="iso_date",
        description="Observation epoch in the source time system.",
        header_hints=(
            "date",
            "epoch",
            "mjd",
            "jd",
            "obs date",
            "observation date",
            "bjd",
            "time",
            "phase",
            "rest-frame days",
            "days since max",
            "时间",
            "观测时间",
        ),
        unit_hints=("mjd", "jd", "days", "utc"),
        required=True,
    ),
    ObservableSpec(
        name="object",
        kind="identifier",
        description="Supernova designation, e.g. SN 2011fe or PTF11kly.",
        header_hints=(
            "name",
            "transient",
            "sn",
            "object",
            "snname",
            "designation",
            "target",
            "id",
            "天体名称",
            "名称",
        ),
        required=True,
    ),
    ObservableSpec(
        name="classification",
        kind="label",
        description="Spectroscopic or photometric type label as published.",
        header_hints=("type", "spectroscopic type", "classification"),
    ),
    ObservableSpec(
        name="redshift",
        kind="quantity",
        canonical_unit="",
        description="Host or supernova redshift, dimensionless.",
        header_hints=("z", "redshift"),
        value_range=(0.0, 3.0),
    ),
)

_SNI = DomainPlugin(
    name="snia_light_curve",
    title="Type Ia supernova photometry",
    observables=_PHOTOMETRY,
    entity=EntitySpec(
        key_hints=("object", "name", "transient", "designation"),
        alias_hints=("object", "name", "transient", "snname", "nickname"),
        coord_hints={"ra": "ra", "dec": "dec"},
        coord_tolerance_arcsec=2.0,
        require_explicit_match=True,
    ),
    preferred_time_system="mjd",
    merge_keys=("entity_id", "observable", "band", "time_system", "time_key"),
    allowed_unit_conversions={
        "flux_density": {"jy": 1000.0, "ujy": 0.001, "mjy": 1.0},
    },
    plausibility={
        "apparent_magnitude": (-3.0, 30.0),
        "redshift": (0.0, 3.0),
        "magnitude_uncertainty": (0.0, 2.0),
    },
    synonym_overrides={},
)

_GENERIC = DomainPlugin(
    name="generic",
    title="Schema-agnostic tabular integration",
    observables=(
        ObservableSpec(
            name="time",
            kind="time",
            header_hints=(
                "time",
                "date",
                "epoch",
                "timestamp",
                "day",
                "year",
                "时间",
            ),
        ),
        ObservableSpec(
            name="object",
            kind="identifier",
            header_hints=("name", "id", "天体名称", "名称"),
        ),
    ),
    entity=EntitySpec(
        key_hints=("object", "name", "id"),
        alias_hints=("name", "alias", "designation"),
    ),
    preferred_time_system="iso_date",
)

_SOLAR = DomainPlugin(
    name="solar_cycle",
    title="Solar activity cycle series (legacy adapter)",
    observables=(
        ObservableSpec(
            name="sunspot_number",
            canonical_unit="",
            header_hints=("ssn", "sunspot number", "silso v2.0", "monthly mean ssn"),
        ),
        ObservableSpec(
            name="f107_flux",
            canonical_unit="sfu",
            header_hints=("f10.7", "f107", "10.7 cm flux"),
            unit_hints=("sfu",),
        ),
        ObservableSpec(
            name="polar_field",
            canonical_unit="gauss",
            header_hints=("polar field", "polar magnetic field"),
            unit_hints=("gauss", "g"),
        ),
        ObservableSpec(
            name="time",
            kind="time",
            header_hints=("date", "month", "time"),
        ),
    ),
    entity=EntitySpec(key_hints=("station", "instrument", "observatory")),
    preferred_time_system="iso_date",
    cadence={"resample": "month", "aggregation": "mean"},
    allowed_unit_conversions={},
    plausibility={"sunspot_number": (0.0, 500.0), "f107_flux": (50.0, 500.0)},
)

_PLUGINS: dict[str, DomainPlugin] = {
    _GENERIC.name: _GENERIC,
    _SNI.name: _SNI,
    _SOLAR.name: _SOLAR,
}


def register_plugin(plugin: DomainPlugin, *, replace: bool = False) -> DomainPlugin:
    """Add a plugin to the in-process registry."""
    existing = _PLUGINS.get(plugin.name)
    if existing is not None and not replace:
        raise ValueError(f"domain plugin already registered: {plugin.name}")
    _PLUGINS[plugin.name] = plugin
    return plugin


def get_plugin(name: str) -> DomainPlugin:
    """Look up one plugin; ``generic`` is the fallback for unknown names."""
    return _PLUGINS.get(slug(name) or _GENERIC.name, _GENERIC)


def list_plugins() -> tuple[str, ...]:
    return tuple(sorted(_PLUGINS))


def plugin_for_contract(contract: Any) -> DomainPlugin:
    """Resolve the plugin a :class:`DataRequirementContract` selects."""
    return get_plugin(getattr(contract, "domain_plugin", "generic"))


# ---------------------------------------------------------------------------
# Generic plugin helpers reused by several stages
# ---------------------------------------------------------------------------


def unit_from_header(header: str) -> str:
    """Pull a unit out of a header: brackets, ``/ unit``, or a ``_unit`` suffix.

    Astronomy tables spell the same thing in several ways: ``flux [uJy]``,
    ``flux / Jy`` and ``flux_ujy``.  The suffix form used to be invisible, which
    made a ``flux_ujy`` column look like it already was in ``mJy``.  The suffix
    is only accepted when the part before it is a real field name; ``e_mag``
    is an uncertainty column, not a value measured in ``mag``.
    """
    text = str(header or "")
    match = re.search(r"[\[\(]([^\]\)]{1,24})[\]\)]", text)
    if match:
        return match.group(1).strip()
    match = re.search(r"/\s*(\S(?:[^\)]{0,22}\S)?)\s*$", text)
    if match:
        return match.group(1).strip()
    return _header_suffix_unit(text)


_HEADER_UNIT_ALIASES = {
    "ab": "mag_ab",
    "vega": "mag_vega",
    "st": "mag_st",
}

_HEADER_UNIT_SUFFIXES = tuple(
    sorted(
        {
            "mjy",
            "ujy",
            "njy",
            "jy",
            "mmag",
            "mag",
            "sfu",
            "ab",
            "vega",
            "st",
            "angstrom",
            "arcsec",
            "arcmin",
            "kev",
            "mev",
            "gev",
            "tev",
            "ev",
            "ghz",
            "mhz",
            "khz",
            "thz",
            "hz",
            "kpc",
            "mpc",
            "pc",
            "au",
            "ly",
            "myr",
            "gyr",
            "kyr",
            "yr",
            "days",
            "day",
            "hrs",
            "hr",
            "min",
            "sec",
            "ms",
            "us",
            "ns",
            "counts",
            "count",
            "cts",
            "adu",
            "dn",
            "percent",
            "pct",
            "ppm",
            "kg",
            "km",
            "cm",
            "mm",
            "um",
            "nm",
            "deg",
            "kelvin",
        },
        key=len,
        reverse=True,
    )
)

_HEADER_UNIT_RE = re.compile(
    r"(?:^|[_\-\s])(?P<unit>" + "|".join(_HEADER_UNIT_SUFFIXES) + r")\s*$",
    re.IGNORECASE,
)

_HEADER_UNIT_BASE_EXCEPTIONS = frozenset(
    {"e", "err", "error", "sigma", "unc", "uncert", "uncertainty", "d"}
)


def _header_suffix_unit(header: str) -> str:
    """The ``_unit`` suffix of a header, or "" when the base is not a field."""
    text = str(header or "").strip()
    match = _HEADER_UNIT_RE.search(text)
    if match is None:
        return ""
    base = text[: match.start()].strip("_- ")
    if not base or slug(base) in _HEADER_UNIT_BASE_EXCEPTIONS:
        return ""
    unit = match.group("unit")
    return _HEADER_UNIT_ALIASES.get(unit.casefold(), unit)


def strip_unit_from_header(header: str) -> str:
    """Header text with any trailing unit marker removed."""
    text = str(re.sub(r"[\[\(][^\]\)]{1,24}[\]\)]", "", str(header or "")))
    match = _HEADER_UNIT_RE.search(text)
    if match is not None:
        base = text[: match.start()].strip("_- ")
        if base and slug(base) not in _HEADER_UNIT_BASE_EXCEPTIONS:
            text = base
    return text.strip()


def detect_unit_in_values(values: Sequence[Any], quantity: str) -> str:
    """Return a unit spelled out inside value cells, else the empty string."""
    pattern = re.compile(r"\b(sfu|gauss|g|mjy|jy|mag|days|seconds|s|year)\b", re.I)
    for value in values:
        text = str(value or "")
        match = pattern.search(text)
        if match:
            return slug(match.group(1))
    return ""


def plausibility_issue(
    *, quantity: str, value: float | None, plugin: DomainPlugin
) -> QualityIssue | None:
    """Range check against plugin plausibility limits."""
    if value is None:
        return None
    bounds = plugin.plausibility.get(quantity)
    if bounds is None:
        return None
    low, high = bounds
    if low <= value <= high:
        return None
    return QualityIssue(
        issue_id="",
        code=IssueCode.OUT_OF_RANGE,
        severity=IssueSeverity.WARNING,
        detail=f"{quantity}={value!r} outside plausible range [{low}, {high}]",
        evidence={"value": value, "bounds": [low, high], "plugin": plugin.name},
        repair_mode=default_repair_mode(IssueCode.OUT_OF_RANGE),
    )


def confidence_from_score(score: float) -> ConfidenceBand:
    """Map a continuous extraction score onto the shared confidence tiers."""
    if score < 0:
        return ConfidenceBand.UNVERIFIED
    if score >= 0.9:
        return ConfidenceBand.HIGH
    if score >= 0.6:
        return ConfidenceBand.MEDIUM
    return ConfidenceBand.LOW


register_plugin(_SNI, replace=True)
register_plugin(_SOLAR, replace=True)
