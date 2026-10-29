"""Deterministic unit resolution for the integration kernel.

SEED IMPLEMENTATION (conservative). Owner B replaces the bodies but must keep
these public signatures. The seed never reports an exact conversion factor, so
downstream stages can only flag unit problems, never auto-convert them — which
is the safe default until the real factor table lands.

Rules that survive every revision of this module:

* ``factor``/``exact`` only describe registered, dimension-preserving factors.
* Cross-dimension conversion (e.g. magnitude ↔ flux density) is never
  automatic: it needs a photometric zero point, so it is reported instead.
* Magnitude systems (AB / Vega / ST) and "instrumental" magnitudes are
  different observables and must never be silently unified.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .contracts import IssueCode, QualityIssue, default_repair_mode
from .domain import DomainPlugin, slug

__all__ = [
    "UnitResolution",
    "convert",
    "dimension_of",
    "is_photometric_system",
    "resolve_unit",
    "unit_conflict_groups",
    "unit_inconsistency_issue",
]

# dimension -> {canonical unit slug: {accepted spelling: multiplicative factor}}
_FACTOR_TABLE: dict[str, dict[str, dict[str, float]]] = {}

_MAGNITUDE_SYSTEMS: frozenset[str] = frozenset({"ab", "vega", "st", "mjerruzzi"})


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


def dimension_of(unit: str) -> str:
    """Best-effort dimension name for a unit spelling ("" when unknown)."""
    token = slug(unit)
    if not token:
        return ""
    for dimension, groups in _FACTOR_TABLE.items():
        for canonical, spellings in groups.items():
            if token == canonical or token in spellings:
                return dimension
    return ""


def is_photometric_system(unit: str) -> bool:
    """Whether a unit spells out a magnitude system that must be preserved."""
    token = slug(unit)
    return any(marker in token for marker in _MAGNITUDE_SYSTEMS)


def resolve_unit(
    raw: str, *, quantity: str, plugin: DomainPlugin | None = None
) -> UnitResolution:
    """Resolve ``raw`` for ``quantity`` under ``plugin``.

    The returned ``factor`` maps ``raw`` onto the canonical unit of the
    observable. ``exact=True`` only when the factor is a registered,
    dimension-preserving constant, which is what makes an auto-repair legal.
    """
    text = str(raw or "").strip()
    canonical = ""
    if plugin is not None:
        spec = plugin.observable(quantity)
        canonical = slug(spec.canonical_unit) if spec is not None else ""
    if not text:
        return UnitResolution(text, canonical, "", None, False, "unit_absent")
    token = slug(text)
    if canonical and token == canonical:
        return UnitResolution(text, canonical, quantity, 1.0, True, "identity")
    return UnitResolution(text, canonical, dimension_of(token), None, False, "seed")


def convert(value: float, src: str, dst: str) -> float | None:
    """Convert ``value`` from ``src`` to ``dst``; ``None`` when not exact."""
    if value is None:
        return None
    source = slug(src)
    target = slug(dst)
    if source == target:
        return value
    for _dimension, groups in _FACTOR_TABLE.items():
        for canonical, spellings in groups.items():
            table = {canonical: 1.0, **spellings}
            if source in table and target in table:
                return value * table[source] / table[target]
    return None


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


def default_severity(code: IssueCode) -> Any:
    from .contracts import IssueSeverity

    return (
        IssueSeverity.ERROR
        if code is IssueCode.UNIT_INCONSISTENCY
        else IssueSeverity.WARNING
    )
