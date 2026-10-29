"""Entity resolution: one object of study, many names across many sources.

The kernel must never silently merge two objects. Every link keeps the evidence
that produced it, coordinate-based links need to clear the plugin's separation
tolerance, and a name that could mean two objects is recorded as ambiguous
instead of being attached to whichever entity happened to come first.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .contracts import (
    ConfidenceBand,
    EntityAlias,
    EntityRef,
    IssueCode,
    IssueSeverity,
    QualityIssue,
    default_repair_mode,
)
from .domain import DomainPlugin, normalise_identifier

__all__ = [
    "AliasClaim",
    "EntityResolver",
    "coordinate_separation_arcsec",
    "identity_issue",
    "parse_coordinates",
]

# Prefixes that carry no identity on their own (survey + filter + year).
_SURVEY_PREFIXES = ("sn", "ptf", "asassn", "atlas", "ztf", "ps", "css", "gaia", "mast")
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_ALPHA_SUFFIX_RE = re.compile(r"^([a-z]+)([a-z]*)$")


def parse_coordinates(
    ra: Any, dec: Any, *, ra_unit: str = "deg", dec_unit: str = "deg"
) -> tuple[float, float] | None:
    """Parse a right ascension / declination pair into decimal degrees.

    Sexagesimal strings such as ``"14:03:05.8"`` (declared in hours) and
    ``"+54:16:25"`` are accepted, as are plain decimal degrees. ``None`` is
    returned whenever the pair cannot be read unambiguously, so the caller
    reports a missing coordinate instead of assuming a position.
    """
    ra_deg = _angle(ra, hours=_is_hour_unit(ra_unit))
    dec_deg = _angle(dec, hours=_is_hour_unit(dec_unit))
    if ra_deg is None or dec_deg is None:
        return None
    if not (-90.0 <= dec_deg <= 90.0) or not (0.0 <= ra_deg < 360.0):
        return None
    return ra_deg, dec_deg


def _is_hour_unit(unit: str) -> bool:
    return str(unit or "").strip().lower() in {"hour", "hours", "h", "hr", "hrs", "hms"}


def _angle(raw: Any, *, hours: bool) -> float | None:
    """Sexagesimal or decimal angle in the declared unit, returned in degrees."""
    if raw is None or raw == "":
        return None
    text = str(raw).strip().replace("\u2212", "-").replace("\u00a0", " ")
    if not text:
        return None
    parts = [part for part in re.split(r"[:\s]+", text) if part]
    try:
        numbers = [float(part) for part in parts]
    except ValueError:
        return None
    if not numbers:
        return None
    value = 0.0
    for index, number in enumerate(numbers[:3]):
        value += number / (60.0**index)
    if hours:
        value *= 15.0
    return value


def coordinate_separation_arcsec(
    first: Mapping[str, float], second: Mapping[str, float]
) -> float:
    """Great-circle separation in arcseconds between two RA/Dec positions."""
    ra1 = math.radians(float(first.get("ra", 0.0)))
    dec1 = math.radians(float(first.get("dec", 0.0)))
    ra2 = math.radians(float(second.get("ra", 0.0)))
    dec2 = math.radians(float(second.get("dec", 0.0)))
    cosine = math.sin(dec1) * math.sin(dec2) + math.cos(dec1) * math.cos(
        dec2
    ) * math.cos(ra1 - ra2)
    cosine = max(-1.0, min(1.0, cosine))
    return math.degrees(math.acos(cosine)) * 3600.0


@dataclass(frozen=True, slots=True)
class AliasClaim:
    """One source asserting that a name belongs to one object."""

    label: str
    source_id: str = ""
    alias_kind: str = "name"
    coordinates: Mapping[str, float] | None = None
    classification: str = ""

    @property
    def key(self) -> str:
        return normalise_identifier(self.label)


class EntityResolver:
    """Accumulate name claims and resolve them into shared entities."""

    def __init__(self, *, plugin: DomainPlugin | None = None) -> None:
        self.plugin = plugin
        entity_spec = getattr(plugin, "entity", None)
        self.tolerance_arcsec = float(
            getattr(entity_spec, "coord_tolerance_arcsec", 0.0) or 0.0
        )
        self._refs: dict[str, EntityRef] = {}
        self._by_alias: dict[str, str] = {}
        self._issues: list[QualityIssue] = []

    # -- population --------------------------------------------------------
    def add(self, claim: AliasClaim) -> EntityRef:
        """Attach one alias claim to an entity, creating or merging as needed."""
        key = claim.key
        if not key:
            raise ValueError("an entity claim needs a non-empty label")
        existing = self._by_alias.get(key)
        if existing is not None:
            return self._merge(self._refs[existing], claim)
        coord_match = self._match_by_coordinates(claim)
        if coord_match is not None:
            entity = self._merge(self._refs[coord_match], claim)
            self._by_alias[key] = entity.entity_id
            return entity
        entity_id = self._new_entity_id(key)
        entity = EntityRef(
            entity_id=entity_id,
            label=claim.label.strip(),
            aliases=(
                EntityAlias(
                    alias=claim.label.strip(),
                    normalised=key,
                    source_id=claim.source_id,
                    alias_kind=claim.alias_kind,
                ),
            ),
            coordinates=dict(claim.coordinates or {}),
            classification=claim.classification,
            link_confidence=ConfidenceBand.HIGH,
            link_evidence=("exact_name",),
        )
        self._refs[entity_id] = entity
        self._by_alias[key] = entity_id
        return entity

    def add_aliases(
        self, label: str, aliases: Iterable[str], *, source_id: str = ""
    ) -> EntityRef:
        """Claim one label plus its known aliases, all pointing at one entity."""
        entity = self.add(AliasClaim(label=label, source_id=source_id))
        for alias in aliases:
            if not str(alias or "").strip():
                continue
            self._add_alias(entity, AliasClaim(label=str(alias), source_id=source_id))
        return self._refs[entity.entity_id]

    def _add_alias(self, entity: EntityRef, claim: AliasClaim) -> None:
        key = claim.key
        if not key:
            return
        holder = self._by_alias.get(key)
        if holder is not None and holder != entity.entity_id:
            self._escalate_ambiguous(entity.entity_id, holder, claim)
            return
        self._by_alias[key] = entity.entity_id
        if all(alias.normalised != key for alias in entity.aliases):
            self._refs[entity.entity_id] = _with_aliases(
                entity,
                (
                    *entity.aliases,
                    EntityAlias(
                        alias=claim.label.strip(),
                        normalised=key,
                        source_id=claim.source_id,
                        alias_kind=claim.alias_kind,
                    ),
                ),
            )

    def _merge(self, entity: EntityRef, claim: AliasClaim) -> EntityRef:
        merged = entity
        coordinates = dict(entity.coordinates)
        evidence = list(entity.link_evidence)
        confidence = entity.link_confidence
        if claim.coordinates:
            if coordinates:
                separation = coordinate_separation_arcsec(
                    coordinates, claim.coordinates
                )
                if self.tolerance_arcsec and separation > self.tolerance_arcsec:
                    self._escalate_conflict(entity, claim, separation)
                    confidence = ConfidenceBand.LOW
                    evidence.append(f"coordinates_conflict:{separation:.2f}arcsec")
                else:
                    evidence.append(f"coordinates_agree:{separation:.2f}arcsec")
                    confidence = ConfidenceBand.HIGH
            else:
                coordinates = dict(claim.coordinates)
                evidence.append("coordinates_from_alias")
        classification = entity.classification or claim.classification
        merged = EntityRef(
            entity_id=entity.entity_id,
            label=entity.label,
            aliases=entity.aliases,
            coordinates=coordinates,
            classification=classification,
            link_confidence=confidence,
            link_evidence=tuple(evidence),
            ambiguous_with=entity.ambiguous_with,
        )
        self._refs[entity.entity_id] = merged
        self._add_alias(merged, claim)
        return self._refs[merged.entity_id]

    def _match_by_coordinates(self, claim: AliasClaim) -> str | None:
        if not claim.coordinates or not self.tolerance_arcsec:
            return None
        best: tuple[float, str] | None = None
        for entity_id, entity in self._refs.items():
            if not entity.coordinates:
                continue
            separation = coordinate_separation_arcsec(
                entity.coordinates, claim.coordinates
            )
            if separation <= self.tolerance_arcsec and (
                best is None or separation < best[0]
            ):
                best = (separation, entity_id)
        return best[1] if best else None

    def _new_entity_id(self, key: str) -> str:
        base = f"obj:{key}"
        if base not in self._refs:
            return base
        suffix = 2
        while f"{base}#{suffix}" in self._refs:
            suffix += 1
        return f"{base}#{suffix}"

    def _escalate_ambiguous(self, first: str, second: str, claim: AliasClaim) -> None:
        for entity_id, other in ((first, second), (second, first)):
            entity = self._refs[entity_id]
            self._refs[entity_id] = EntityRef(
                entity_id=entity.entity_id,
                label=entity.label,
                aliases=entity.aliases,
                coordinates=entity.coordinates,
                classification=entity.classification,
                link_confidence=ConfidenceBand.LOW,
                link_evidence=entity.link_evidence,
                ambiguous_with=tuple(sorted({*entity.ambiguous_with, other})),
            )
        self._issues.append(
            identity_issue(
                issue_id=f"issue:entity:alias_shared:{claim.key}",
                detail=(
                    f"Identifier '{claim.label}' is claimed by more than one entity "
                    "and cannot be resolved from the name alone."
                ),
                evidence={"alias": claim.label, "entities": [first, second]},
                proposed_action="Choose the correct object, or supply distinguishing coordinates.",
            )
        )

    def _escalate_conflict(
        self, entity: EntityRef, claim: AliasClaim, separation: float
    ) -> None:
        self._refs[entity.entity_id] = EntityRef(
            entity_id=entity.entity_id,
            label=entity.label,
            aliases=entity.aliases,
            coordinates=entity.coordinates,
            classification=entity.classification,
            link_confidence=ConfidenceBand.LOW,
            link_evidence=entity.link_evidence,
            ambiguous_with=entity.ambiguous_with,
        )
        self._issues.append(
            identity_issue(
                issue_id=f"issue:entity:coord_conflict:{entity.entity_id}:{claim.key}",
                detail=(
                    f"'{claim.label}' claims positions {separation:.2f} arcsec apart, "
                    f"beyond the {self.tolerance_arcsec:.2f} arcsec tolerance."
                ),
                evidence={
                    "entity": entity.entity_id,
                    "alias": claim.label,
                    "separation_arcsec": round(separation, 4),
                    "tolerance_arcsec": self.tolerance_arcsec,
                },
                proposed_action="Confirm whether the coordinates describe the same object.",
            )
        )

    # -- output ------------------------------------------------------------
    def entities(self) -> tuple[EntityRef, ...]:
        return tuple(sorted(self._refs.values(), key=lambda ref: ref.entity_id))

    def issues(self) -> tuple[QualityIssue, ...]:
        return tuple(self._issues)

    def resolve(self, label: str) -> EntityRef | None:
        entity_id = self._by_alias.get(normalise_identifier(label))
        return self._refs.get(entity_id) if entity_id else None

    def entity_id_for(self, label: str) -> str:
        """Entity id for a label, creating an entity when it is unknown."""
        entity = self.resolve(label)
        if entity is not None:
            return entity.entity_id
        return self.add(AliasClaim(label=label)).entity_id


def _with_aliases(entity: EntityRef, aliases: Sequence[EntityAlias]) -> EntityRef:
    return EntityRef(
        entity_id=entity.entity_id,
        label=entity.label,
        aliases=tuple(aliases),
        coordinates=entity.coordinates,
        classification=entity.classification,
        link_confidence=entity.link_confidence,
        link_evidence=entity.link_evidence,
        ambiguous_with=entity.ambiguous_with,
    )


def identity_issue(
    *,
    issue_id: str,
    detail: str,
    evidence: Mapping[str, Any],
    proposed_action: str,
    severity: IssueSeverity = IssueSeverity.ERROR,
) -> QualityIssue:
    """Build the standard entity-identity escalation issue."""
    return QualityIssue(
        issue_id=issue_id,
        code=IssueCode.ENTITY_IDENTITY_CONFLICT,
        severity=severity,
        stage="entities",
        subject={"alias": evidence["alias"]} if evidence.get("alias") else {},
        detail=detail,
        evidence=dict(evidence),
        proposed_action=proposed_action,
        repair_mode=default_repair_mode(IssueCode.ENTITY_IDENTITY_CONFLICT),
    )
