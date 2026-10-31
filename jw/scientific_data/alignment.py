"""Alignment: raw rows from many tables become canonical observation records.

Every decision keeps its evidence. A value is only rescaled when the unit
conversion is exact, a time is only accepted when its scale is known, a cell
that carries no number records *why* it has none, and two sources that disagree
about the same observation are both kept and flagged rather than averaged.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .contracts import (
    ConfidenceBand,
    DataRequirementContract,
    ExtractedTable,
    FieldMapping,
    IssueCode,
    IssueSeverity,
    MissingReason,
    ObservationRecord,
    ProvenanceRef,
    QualityIssue,
    ValueKind,
    default_repair_mode,
)
from .domain import DomainPlugin, plausibility_issue, slug
from .entities import AliasClaim, EntityResolver, EntityRef, parse_coordinates
from .semantics import map_table, select_overrides
from .timescales import detect_time_system, to_time
from .units import convert, dimension_of, resolve_unit

__all__ = [
    "AlignmentResult",
    "RecordConflict",
    "align_tables",
]

_NULL_TOKENS = frozenset(
    {"", "-", "--", "---", "\u2014", "\u2013", "null", "none", "nan"}
)
_NOT_APPLICABLE_TOKENS = frozenset(
    {"n/a", "na", "not applicable", "not reported", "..."}
)
_VALUE_KINDS = frozenset({"quantity", "quality"})
_ENTITY_OBSERVABLES = frozenset({"object", "entity", "name"})
_UNCERTAINTY_SUFFIXES = ("_uncertainty", "_error", "_sigma")
_CONFLICT_RELATIVE_TOLERANCE = 1e-6
_DEFAULT_KEYS = ("entity_id", "observable", "time")
_BAND_SCORES = {
    ConfidenceBand.HIGH: 0.95,
    ConfidenceBand.MEDIUM: 0.75,
    ConfidenceBand.LOW: 0.45,
    ConfidenceBand.UNVERIFIED: 0.0,
}


@dataclass
class RecordConflict:
    """Two sources disagreeing about one observation under the merge key."""

    key: tuple[str, ...]
    record_uids: tuple[str, ...]
    values: tuple[float | None, ...]
    units: tuple[str, ...]
    sources: tuple[tuple[str, ...], ...]
    severity: IssueSeverity = IssueSeverity.ERROR

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": list(self.key),
            "record_uids": list(self.record_uids),
            "values": list(self.values),
            "units": list(self.units),
            "sources": [list(item) for item in self.sources],
            "severity": str(self.severity),
        }


@dataclass
class AlignmentResult:
    """Outcome of one alignment pass over a set of extracted tables."""

    records: tuple[ObservationRecord, ...] = ()
    issues: tuple[QualityIssue, ...] = ()
    conflicts: tuple[RecordConflict, ...] = ()
    resolver: EntityResolver | None = None
    stats: dict[str, Any] = field(default_factory=dict)

    def records_for(self, observable: str) -> tuple[ObservationRecord, ...]:
        return tuple(
            record for record in self.records if record.observable == observable
        )

    def entities(self) -> tuple[EntityRef, ...]:
        return self.resolver.entities() if self.resolver is not None else ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "stats": dict(self.stats),
            "conflicts": [conflict.to_dict() for conflict in self.conflicts],
            "entities": [entity.to_dict() for entity in self.entities()],
        }


@dataclass(frozen=True, slots=True)
class _ColumnPlan:
    """Which mapped columns play which role for one table."""

    entity: FieldMapping | None = None
    time: FieldMapping | None = None
    time_system: str = ""
    values: tuple[FieldMapping, ...] = ()
    uncertainties: tuple[FieldMapping, ...] = ()
    attributes: tuple[FieldMapping, ...] = ()

    @property
    def usable(self) -> bool:
        return self.entity is not None


def _cell(row: Mapping[str, Any], column: str) -> str:
    value = row.get(column, "")
    if value is None:
        return ""
    return str(value).strip()


def _parse_number(text: str) -> tuple[float | None, ValueKind]:
    """Read a cell as a number, keeping any limit marker as its value kind."""
    cleaned = text.replace("\u2212", "-").replace("\u00a0", " ").strip()
    kind = ValueKind.REPORTED
    for marker, marked in (
        ("<=", ValueKind.UPPER_LIMIT),
        ("\u2264", ValueKind.UPPER_LIMIT),
        ("<", ValueKind.UPPER_LIMIT),
        (">=", ValueKind.LOWER_LIMIT),
        ("\u2265", ValueKind.LOWER_LIMIT),
        (">", ValueKind.LOWER_LIMIT),
    ):
        if cleaned.startswith(marker):
            kind, cleaned = marked, cleaned[len(marker) :].strip()
            break
    if cleaned.endswith("%"):
        cleaned = cleaned[:-1].strip()
    cleaned = cleaned.replace(" ", "")
    if (
        cleaned.count(",")
        and cleaned.replace(",", "").replace(".", "").lstrip("-").isdigit()
    ):
        cleaned = cleaned.replace(",", "")
    try:
        number = float(cleaned)
    except ValueError:
        return None, kind
    return number, kind


def _missing_reason(text: str) -> MissingReason | None:
    """Why a cell carries no number, or ``None`` when it does."""
    lowered = text.strip().lower()
    if lowered in _NOT_APPLICABLE_TOKENS:
        return MissingReason.NOT_APPLICABLE
    if lowered in _NULL_TOKENS:
        return MissingReason.NOT_REPORTED
    number, _ = _parse_number(text)
    if number is not None:
        return None
    return MissingReason.NOT_PARSED


def _plan(
    table: ExtractedTable,
    mappings: Sequence[FieldMapping],
    plugin: DomainPlugin,
    contract: DataRequirementContract,
) -> _ColumnPlan:
    """Split accepted mappings into entity / time / value / attribute roles."""
    entity: FieldMapping | None = None
    time_column: FieldMapping | None = None
    values: list[FieldMapping] = []
    uncertainties: list[FieldMapping] = []
    attributes: list[FieldMapping] = []
    identifiers = set(contract.identifier_fields) | _ENTITY_OBSERVABLES
    for mapping in mappings:
        if not mapping.applied:
            continue
        spec = plugin.observable(mapping.observable)
        kind = spec.kind if spec else "quantity"
        if kind == "time":
            time_column = time_column or mapping
        elif kind == "identifier" and mapping.observable in identifiers:
            entity = entity or mapping
        elif kind in {"identifier", "label"}:
            attributes.append(mapping)
        elif kind in _VALUE_KINDS:
            if mapping.observable.endswith(_UNCERTAINTY_SUFFIXES):
                uncertainties.append(mapping)
            else:
                values.append(mapping)
    return _ColumnPlan(
        entity=entity,
        time=time_column,
        time_system=detect_time_system(time_column.source_column)
        if time_column
        else "",
        values=tuple(values),
        uncertainties=tuple(uncertainties),
        attributes=tuple(attributes),
    )


def _unit_issue(
    *, code: IssueCode, observable: str, detail: str, evidence: Mapping[str, Any]
) -> QualityIssue:
    return QualityIssue(
        issue_id=f"issue:alignment:{slug(code)}:{slug(observable)}",
        code=code,
        severity=IssueSeverity.WARNING,
        stage="alignment",
        subject={"observable": observable},
        detail=detail,
        evidence=dict(evidence),
        proposed_action="Confirm the unit before using this column.",
        repair_mode=default_repair_mode(code),
    )


def _canonical_value(
    *,
    text: str,
    mapping: FieldMapping,
    plugin: DomainPlugin,
    observable: str,
) -> tuple[
    float | None,
    str,
    MissingReason,
    ValueKind,
    tuple[str, ...],
    tuple[QualityIssue, ...],
]:
    """Turn one raw cell into a canonical value, its unit and its caveats."""
    flags: list[str] = []
    target_unit = mapping.target_unit
    reason = _missing_reason(text)
    if reason is not None:
        return None, target_unit, reason, ValueKind.REPORTED, (), ()
    number, value_kind = _parse_number(text)
    if number is None:
        return None, target_unit, MissingReason.NOT_PARSED, value_kind, (), ()
    source_unit = mapping.source_unit
    if not source_unit:
        if not target_unit:
            return (
                number,
                "",
                MissingReason.UNKNOWN,
                value_kind,
                (),
                (),
            )
        return (
            number,
            "",
            MissingReason.UNKNOWN,
            value_kind,
            ("unit_missing",),
            (
                _unit_issue(
                    code=IssueCode.UNIT_UNKNOWN,
                    observable=observable,
                    detail=(
                        f"Column '{mapping.source_column}' reports no unit and the "
                        f"expected unit is '{target_unit}'; the raw number is kept "
                        "without rescaling instead of being assumed to be in the "
                        "target unit."
                    ),
                    evidence={
                        "column": mapping.source_column,
                        "raw": text,
                        "target_unit": target_unit,
                    },
                ),
            ),
        )
    if not target_unit or source_unit == target_unit:
        return (
            number,
            target_unit or source_unit,
            MissingReason.UNKNOWN,
            value_kind,
            (),
            (),
        )

    resolution = resolve_unit(source_unit, quantity=observable, plugin=plugin)
    if resolution.exact and resolution.canonical == target_unit:
        scaled = convert(number, source_unit, target_unit)
        if scaled is not None:
            return (
                scaled,
                target_unit,
                MissingReason.UNKNOWN,
                value_kind,
                (f"unit_converted:{source_unit}->{target_unit}",),
                (),
            )
    flags.append(f"unit_not_converted:{source_unit}!={target_unit}")
    code = (
        IssueCode.UNIT_INCONSISTENCY
        if dimension_of(source_unit) and dimension_of(target_unit)
        else IssueCode.UNIT_UNKNOWN
    )
    return (
        number,
        source_unit,
        MissingReason.UNKNOWN,
        value_kind,
        tuple(flags),
        (
            _unit_issue(
                code=code,
                observable=observable,
                detail=(
                    f"Cell '{text}' reports {source_unit} while the contract expects "
                    f"{target_unit} ({resolution.note or 'no exact factor'}); the raw "
                    "unit is kept instead of guessing a scale."
                ),
                evidence={
                    "raw": text,
                    "from": source_unit,
                    "to": target_unit,
                    "note": resolution.note,
                },
            ),
        ),
    )


def _time_for(
    text: str,
    *,
    plan: _ColumnPlan,
    plugin: DomainPlugin,
    issues: list[QualityIssue],
) -> tuple[str, str, tuple[str, ...]]:
    """Resolve a time cell to (canonical, system, flags)."""
    if not text:
        return "", plan.time_system, ("time_missing",)
    resolution = to_time(text, time_system=plan.time_system, plugin=plugin)
    flags: list[str] = []
    if resolution.note:
        flags.append(f"time:{resolution.note}")
    if resolution.ambiguous:
        issues.append(
            QualityIssue(
                issue_id=f"issue:alignment:time:{_uid(text)[5:13]}",
                code=IssueCode.TIME_SYSTEM_AMBIGUOUS,
                severity=IssueSeverity.ERROR,
                stage="alignment",
                subject={"raw": text},
                detail=(
                    f"Time value '{text}' can be read as more than one system "
                    f"({resolution.note or 'no declared scale'})."
                ),
                evidence={
                    "raw": text,
                    "note": resolution.note,
                    "system": resolution.time_system,
                },
                proposed_action="Declare the time system for this column.",
                repair_mode=default_repair_mode(IssueCode.TIME_SYSTEM_AMBIGUOUS),
            )
        )
    return (
        resolution.iso or resolution.raw,
        resolution.time_system or plan.time_system,
        tuple(flags),
    )


def _merge_keys(
    contract: DataRequirementContract, plugin: DomainPlugin
) -> tuple[str, ...]:
    keys = tuple(contract.merge_keys or plugin.merge_keys)
    return keys or _DEFAULT_KEYS


def _uid(*parts: str) -> str:
    digest = hashlib.sha256("\u0001".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"rec:{digest}"


def _claim_for(
    label: str,
    row: Mapping[str, Any],
    table: ExtractedTable,
    ra_column: str,
    dec_column: str,
) -> AliasClaim:
    coordinates: dict[str, float] = {}
    if ra_column and dec_column:
        parsed = parse_coordinates(_cell(row, ra_column), _cell(row, dec_column))
        if parsed is not None:
            coordinates = {"ra": parsed[0], "dec": parsed[1]}
    return AliasClaim(
        label=label, source_id=table.source_id, coordinates=coordinates or None
    )


def _entity_override(
    table: ExtractedTable,
    overrides: Sequence[Mapping[str, Any]],
    source_titles: Mapping[str, str],
) -> str:
    """An explicit source-level entity label, if the caller supplied one."""
    names = {table.source_id, source_titles.get(table.source_id, "")}
    for item in overrides:
        source = str(item.get("source") or item.get("source_id") or "")
        if source not in names:
            continue
        label = str(
            item.get("entity") or item.get("entity_label") or item.get("label") or ""
        ).strip()
        if label:
            return label
    return ""


def align_tables(
    tables: Sequence[ExtractedTable],
    *,
    contract: DataRequirementContract,
    plugin: DomainPlugin,
    resolver: EntityResolver | None = None,
    overrides: Sequence[Mapping[str, Any]] = (),
    entity_overrides: Sequence[Mapping[str, Any]] = (),
    source_titles: Mapping[str, str] | None = None,
) -> AlignmentResult:
    """Build canonical observation records from every table that mapped."""
    entities = resolver if resolver is not None else EntityResolver(plugin=plugin)
    keys = _merge_keys(contract, plugin)
    titles = source_titles or {}
    issues: list[QualityIssue] = []
    records: list[ObservationRecord] = []
    index: dict[tuple[str, ...], int] = {}
    conflicts: list[RecordConflict] = []
    stats: dict[str, Any] = {
        "tables": len(tables),
        "rows_seen": 0,
        "records": 0,
        "skipped_rows": 0,
        "missing_cells": 0,
        "unit_fixes": 0,
        "conflicts": 0,
    }

    for table in tables:
        mappings, mapping_issues = map_table(
            table,
            contract=contract,
            plugin=plugin,
            overrides=select_overrides(table, overrides, source_titles=source_titles),
        )
        issues.extend(mapping_issues)
        plan = _plan(table, mappings, plugin, contract)
        override_label = _entity_override(table, entity_overrides, titles)
        if table.rows and plan.entity is None and not override_label:
            issues.append(
                QualityIssue(
                    issue_id=f"issue:alignment:{table.table_id}:entity",
                    code=IssueCode.MERGE_KEY_ABSENT,
                    severity=IssueSeverity.ERROR,
                    stage="alignment",
                    subject={"table_id": table.table_id, "source_id": table.source_id},
                    detail=(
                        "No column of this table identifies the object, so its rows "
                        "cannot be attached to an entity."
                    ),
                    proposed_action="Name the identifier column in the contract.",
                    repair_mode=default_repair_mode(IssueCode.MERGE_KEY_ABSENT),
                )
            )
            stats["skipped_rows"] += len(table.rows)
            continue
        if table.rows and plan.time is None:
            issues.append(
                QualityIssue(
                    issue_id=f"issue:alignment:{table.table_id}:time",
                    code=IssueCode.MERGE_KEY_ABSENT,
                    severity=IssueSeverity.ERROR,
                    stage="alignment",
                    subject={"table_id": table.table_id, "source_id": table.source_id},
                    detail="No time column was recognised, so the rows have no time axis.",
                    proposed_action="Declare the time column or confirm a time-free table.",
                    repair_mode=default_repair_mode(IssueCode.MERGE_KEY_ABSENT),
                )
            )

        uncertainty_by_base = _uncertainty_index(plan.uncertainties)
        ra_column = (
            "ra" if "ra" in table.columns else ("RA" if "RA" in table.columns else "")
        )
        dec_column = (
            "dec"
            if "dec" in table.columns
            else ("DEC" if "DEC" in table.columns else "")
        )
        for row in table.rows:
            stats["rows_seen"] += 1
            label = override_label or (
                _cell(row, plan.entity.source_column) if plan.entity else ""
            )
            if not label:
                stats["skipped_rows"] += 1
                continue
            entity = entities.add(_claim_for(label, row, table, ra_column, dec_column))
            attributes = {
                slug(mapping.observable): _cell(row, mapping.source_column)
                for mapping in plan.attributes
                if _cell(row, mapping.source_column)
            }
            time_canonical, time_system, time_flags = _time_for(
                _cell(row, plan.time.source_column) if plan.time else "",
                plan=plan,
                plugin=plugin,
                issues=issues,
            )
            for mapping in plan.values:
                text = _cell(row, mapping.source_column)
                observable = mapping.observable
                (
                    number,
                    unit,
                    reason,
                    value_kind,
                    flags,
                    cell_issues,
                ) = _canonical_value(
                    text=text, mapping=mapping, plugin=plugin, observable=observable
                )
                issues.extend(cell_issues)
                if number is None:
                    stats["missing_cells"] += 1
                stats["unit_fixes"] += sum(
                    1 for flag in flags if flag.startswith("unit_converted")
                )
                record_attributes = {**attributes, "label": label}
                uncertainty = _uncertainty(row, observable, uncertainty_by_base, plugin)
                source_column = str(row.get("_source_column") or mapping.source_column)
                raw_excerpt = str(row.get("_source_raw") or text)
                record_flags = (*flags, *time_flags)
                if override_label:
                    record_flags = (*record_flags, "entity_override")
                record = ObservationRecord(
                    record_uid=_uid(
                        table.table_id,
                        slug(label),
                        observable,
                        time_canonical,
                        str(sorted(record_attributes.items())),
                    ),
                    entity_id=entity.entity_id,
                    observable=observable,
                    unit=unit,
                    value=number,
                    value_raw=text,
                    unit_raw=mapping.source_unit,
                    time=time_canonical,
                    time_raw=_cell(row, plan.time.source_column) if plan.time else "",
                    time_system=time_system,
                    uncertainty=uncertainty,
                    uncertainty_kind="sigma1" if uncertainty is not None else "",
                    value_kind=value_kind,
                    missing_reason=reason,
                    source_ids=(table.source_id,),
                    provenance=(
                        ProvenanceRef(
                            source_id=table.source_id,
                            locator={
                                "table_id": table.table_id,
                                "row_index": str(row.get("_row_index", "")),
                                "column": source_column,
                            },
                            extractor="jw.scientific_data.alignment.align_tables",
                            value_kind=value_kind,
                            confidence=table.confidence,
                            confidence_score=_band_score(table.confidence),
                            raw_excerpt=raw_excerpt,
                        ),
                    ),
                    field_map={source_column: observable},
                    flags=record_flags,
                    attributes=record_attributes,
                )
                if number is not None:
                    range_issue = plausibility_issue(
                        quantity=observable, value=number, plugin=plugin
                    )
                    if range_issue is not None:
                        issues.append(
                            _tag_range_issue(
                                range_issue,
                                table,
                                observable,
                                record_uid=record.record_uid,
                            )
                        )
                key = record.alignment_key(keys)
                existing = index.get(key)
                if existing is None:
                    index[key] = len(records)
                    records.append(record)
                    continue
                previous = records[existing]
                merged, conflict = _reconcile(previous, record, key)
                records[existing] = merged
                if conflict is not None:
                    conflicts.append(conflict)
                    issues.append(_conflict_issue(conflict))
                elif "duplicate_key" not in previous.flags:
                    issues.append(_duplicate_issue(key, merged))

    stats["records"] = len(records)
    stats["conflicts"] = len(conflicts)
    stats["entities"] = len(entities.entities())
    return AlignmentResult(
        records=tuple(records),
        issues=tuple(issues),
        conflicts=tuple(conflicts),
        resolver=entities,
        stats=stats,
    )


def _band_score(band: ConfidenceBand) -> float:
    """Numeric stand-in for a coarse confidence tier."""
    return _BAND_SCORES.get(band, 0.5)


def _base_observable(name: str) -> str:
    for suffix in _UNCERTAINTY_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _uncertainty_keys(name: str) -> tuple[str, ...]:
    """Names under which an uncertainty column may meet its value column."""
    base = _base_observable(name)
    keys = [name, base]
    head, _, tail = base.rpartition("_")
    if head:
        keys.append(tail)
    return tuple(dict.fromkeys(key for key in keys if key))


def _uncertainty_index(
    uncertainties: Sequence[FieldMapping],
) -> dict[str, FieldMapping]:
    """Index uncertainty columns by every name their value column may use."""
    index: dict[str, FieldMapping] = {}
    for mapping in uncertainties:
        for key in _uncertainty_keys(mapping.observable):
            index.setdefault(key, mapping)
    return index


def _uncertainty(
    row: Mapping[str, Any],
    observable: str,
    uncertainty_by_base: Mapping[str, FieldMapping],
    plugin: DomainPlugin,
) -> float | None:
    mapping = None
    for candidate in (observable, *_uncertainty_keys(observable)):
        mapping = uncertainty_by_base.get(candidate)
        if mapping is not None:
            break
    if mapping is None:
        return None
    number, _ = _parse_number(_cell(row, mapping.source_column))
    if number is None:
        return None
    spec = plugin.observable(mapping.observable)
    target_unit = spec.canonical_unit if spec else ""
    if mapping.source_unit and target_unit and mapping.source_unit != target_unit:
        converted = convert(number, mapping.source_unit, target_unit)
        if converted is not None:
            return abs(converted)
    return abs(number)


def _reconcile(
    first: ObservationRecord, second: ObservationRecord, key: tuple[str, ...]
) -> tuple[ObservationRecord, RecordConflict | None]:
    """Keep both numbers, flag disagreement, and merge exact duplicates."""
    same = _same_value(first.value, second.value) and first.unit == second.unit
    flags = tuple(dict.fromkeys((*first.flags, *second.flags)))
    if same:
        flags = tuple(dict.fromkeys((*flags, "duplicate_key")))
    merged = ObservationRecord(
        record_uid=first.record_uid,
        entity_id=first.entity_id,
        observable=first.observable,
        unit=first.unit,
        value=first.value,
        value_raw=first.value_raw
        if same
        else f"{first.value_raw} | {second.value_raw}",
        unit_raw=first.unit_raw,
        time=first.time,
        time_raw=first.time_raw,
        time_system=first.time_system,
        uncertainty=(
            first.uncertainty if first.uncertainty is not None else second.uncertainty
        ),
        uncertainty_kind=first.uncertainty_kind or second.uncertainty_kind,
        uncertainty_low=first.uncertainty_low,
        value_kind=first.value_kind,
        missing_reason=first.missing_reason,
        source_ids=tuple(dict.fromkeys((*first.source_ids, *second.source_ids))),
        provenance=(*first.provenance, *second.provenance),
        field_map={**dict(second.field_map), **dict(first.field_map)},
        flags=flags,
        conflict=first.conflict or second.conflict or not same,
        attributes={**dict(second.attributes), **dict(first.attributes)},
    )
    if same:
        return merged, None
    conflict = RecordConflict(
        key=key,
        record_uids=(first.record_uid, second.record_uid),
        values=(first.value, second.value),
        units=(first.unit, second.unit),
        sources=(first.source_ids, second.source_ids),
    )
    return merged, conflict


def _same_value(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left is None and right is None
    if left == right:
        return True
    scale = max(abs(left), abs(right), 1e-12)
    return abs(left - right) / scale <= _CONFLICT_RELATIVE_TOLERANCE


def _conflict_issue(conflict: RecordConflict) -> QualityIssue:
    detail = "; ".join(
        f"{value!r} {unit or '(no unit)'} from {', '.join(sources)}"
        for value, unit, sources in zip(
            conflict.values, conflict.units, conflict.sources, strict=True
        )
    )
    return QualityIssue(
        issue_id=f"issue:alignment:conflict:{conflict.record_uids[0]}",
        code=IssueCode.CROSS_SOURCE_CONFLICT,
        severity=conflict.severity,
        stage="alignment",
        subject={"key": list(conflict.key)},
        detail=f"Sources disagree about one observation: {detail}",
        evidence=conflict.to_dict(),
        proposed_action="Decide which source is authoritative; nothing is averaged.",
        repair_mode=default_repair_mode(IssueCode.CROSS_SOURCE_CONFLICT),
    )


def _duplicate_issue(key: tuple[str, ...], record: ObservationRecord) -> QualityIssue:
    return QualityIssue(
        issue_id=f"issue:alignment:duplicate:{record.record_uid}",
        code=IssueCode.DUPLICATE_KEY,
        severity=IssueSeverity.INFO,
        stage="alignment",
        subject={"key": list(key)},
        detail="Identical observation reported twice; one record kept with both sources.",
        evidence={"sources": list(record.source_ids), "value": record.value},
        repair_mode=default_repair_mode(IssueCode.DUPLICATE_KEY),
    )


def _tag_range_issue(
    issue: QualityIssue,
    table: ExtractedTable,
    observable: str,
    *,
    record_uid: str = "",
) -> QualityIssue:
    return QualityIssue(
        issue_id=f"issue:alignment:{table.table_id}:{slug(observable)}:range",
        code=issue.code,
        severity=issue.severity,
        stage="alignment",
        subject={
            "table_id": table.table_id,
            "source_id": table.source_id,
            "observable": observable,
            **({"record_uid": record_uid} if record_uid else {}),
        },
        detail=issue.detail,
        evidence={**dict(issue.evidence), "table_id": table.table_id},
        repair_mode=issue.repair_mode,
    )
