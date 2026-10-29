"""Quality detectors: every anomaly the kernel can recognise, with evidence.

Detectors are pure functions over tables, records and entities. They never
modify data — they *report*, so the repair stage can decide what may be fixed
automatically and what must be shown to a human.
"""

from __future__ import annotations

import math
from collections import defaultdict
from itertools import pairwise
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .contracts import (
    DataRequirementContract,
    EntityRef,
    ExtractedTable,
    IssueCode,
    IssueSeverity,
    ObservationRecord,
    QualityIssue,
    default_repair_mode,
)
from .domain import DomainPlugin, slug
from .timescales import iso_to_mjd
from .units import dimension_of, resolve_unit

__all__ = [
    "DetectionReport",
    "detect_all",
    "detect_conflicts",
    "detect_coverage_gaps",
    "detect_duplicate_rows",
    "detect_entity_ambiguity",
    "detect_missing_coverage",
    "detect_nonfinite",
    "detect_out_of_range",
    "detect_sign_suspect",
    "detect_table_warnings",
    "detect_unit_inconsistency",
    "summarise",
]

_METADATA_COLUMNS = ("_row_index",)
_WARNING_CODES: tuple[tuple[str, IssueCode], ...] = (
    ("possible_truncation", IssueCode.TRUNCATED_TABLE),
    ("ragged_rows", IssueCode.TRUNCATED_TABLE),
    ("extra_columns", IssueCode.DELIMITER_SUSPECT),
    ("encoding_suspect", IssueCode.ENCODING_SUSPECT),
    ("delimiter_suspect", IssueCode.DELIMITER_SUSPECT),
    ("duplicate_column", IssueCode.FIELD_MAPPING_AMBIGUOUS),
    ("separator_mismatch", IssueCode.DELIMITER_SUSPECT),
    ("no_header_row", IssueCode.SCHEMA_DRIFT),
)
_DEFAULT_GAP_FACTOR = 3.0


@dataclass(frozen=True, slots=True)
class DetectionReport:
    """Everything the detectors found in one pass."""

    issues: tuple[QualityIssue, ...] = ()
    stats: Mapping[str, Any] = field(default_factory=dict)

    def by_code(self, code: IssueCode) -> tuple[QualityIssue, ...]:
        return tuple(issue for issue in self.issues if issue.code is code)

    def of_stage(self, stage: str) -> tuple[QualityIssue, ...]:
        return tuple(issue for issue in self.issues if issue.stage == stage)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stats": dict(self.stats),
            "issues": [issue.to_dict() for issue in self.issues],
        }


def _issue(
    code: IssueCode,
    *,
    severity: IssueSeverity,
    subject: Mapping[str, Any],
    detail: str,
    evidence: Mapping[str, Any] | None = None,
    action: str = "",
    stage: str = "quality",
    mode: Any = None,
) -> QualityIssue:
    """Build one detector issue with a deterministic id."""
    key = ":".join(slug(f"{name}={value}") for name, value in sorted(subject.items()))
    return QualityIssue(
        issue_id=f"issue:{stage}:{slug(code)}:{key}"[:180],
        code=code,
        severity=severity,
        stage=stage,
        subject=dict(subject),
        detail=detail,
        evidence=dict(evidence or {}),
        proposed_action=action,
        repair_mode=mode or default_repair_mode(code),
    )


def _row_key(row: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            (str(name), str(value))
            for name, value in row.items()
            if name not in _METADATA_COLUMNS
        )
    )


def detect_table_warnings(
    tables: Iterable[ExtractedTable],
) -> tuple[QualityIssue, ...]:
    """Turn parser warnings into issues instead of leaving them in a string."""
    issues: list[QualityIssue] = []
    for table in tables:
        for warning in table.parse_warnings:
            code = next(
                (
                    mapped
                    for token, mapped in _WARNING_CODES
                    if warning.startswith(token)
                ),
                None,
            )
            if code is None:
                continue
            issues.append(
                _issue(
                    code,
                    severity=(
                        IssueSeverity.WARNING
                        if code is IssueCode.ENCODING_SUSPECT
                        else IssueSeverity.ERROR
                    ),
                    subject={"table_id": table.table_id, "source_id": table.source_id},
                    detail=f"Parser reported '{warning}' while reading this table.",
                    evidence={
                        "warning": warning,
                        "format": table.format,
                        "caption": table.caption,
                    },
                    action="Re-read the table with the corrected encoding or delimiter.",
                )
            )
    return tuple(issues)


def detect_duplicate_rows(
    tables: Iterable[ExtractedTable],
) -> tuple[QualityIssue, ...]:
    """Find rows that repeat an earlier row of the same table verbatim."""
    issues: list[QualityIssue] = []
    for table in tables:
        seen: dict[tuple[tuple[str, str], ...], int] = {}
        duplicates: list[int] = []
        for position, row in enumerate(table.rows):
            key = _row_key(row)
            if key in seen:
                duplicates.append(int(row.get("_row_index") or position + 1))
                continue
            seen[key] = position
        if not duplicates:
            continue
        issues.append(
            _issue(
                IssueCode.DUPLICATE_EXACT,
                severity=IssueSeverity.WARNING,
                subject={"table_id": table.table_id, "source_id": table.source_id},
                detail=(
                    f"{len(duplicates)} row(s) repeat an earlier row of the same "
                    "table with identical content."
                ),
                evidence={
                    "rows": duplicates,
                    "comparison": "all_columns_exactly_equal",
                },
                action="Drop later copies, keeping the first occurrence and its row number.",
            )
        )
    return tuple(issues)


def detect_nonfinite(
    records: Iterable[ObservationRecord],
) -> tuple[QualityIssue, ...]:
    """Flag values that are NaN or infinite despite looking numeric."""
    issues: list[QualityIssue] = []
    for record in records:
        value = record.value
        if value is None:
            continue
        if math.isfinite(value):
            continue
        issues.append(
            _issue(
                IssueCode.NONFINITE_VALUE,
                severity=IssueSeverity.ERROR,
                subject={
                    "record_uid": record.record_uid,
                    "observable": record.observable,
                },
                detail=(
                    f"Value '{record.value_raw}' parsed as a non-finite number in "
                    "a context that requires a measurement."
                ),
                evidence={"value": repr(value), "raw": record.value_raw},
                action="Replace the non-finite value with an explicit missing value.",
                stage="quality",
            )
        )
    return tuple(issues)


def detect_unit_inconsistency(
    records: Iterable[ObservationRecord],
    *,
    contract: DataRequirementContract,
    plugin: DomainPlugin,
) -> tuple[QualityIssue, ...]:
    """Group one observable's observations by unit and report mixed units."""
    groups: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for record in records:
        if record.value is None:
            continue
        groups[record.observable][record.unit or "unspecified"].append(
            record.record_uid
        )

    issues: list[QualityIssue] = []
    for observable, by_unit in sorted(groups.items()):
        if len(by_unit) < 2:
            continue
        expected = contract.unit_expectations.get(observable)
        target = expected or _canonical_unit(plugin, observable)
        convertible = {
            unit: resolve_unit(unit, quantity=observable, plugin=plugin).exact
            for unit in by_unit
        }
        if all(convertible.values()) and target:
            continue
        issues.append(
            _issue(
                IssueCode.UNIT_INCONSISTENCY,
                severity=IssueSeverity.ERROR,
                subject={"observable": observable},
                detail=(
                    f"{observable} is reported in {len(by_unit)} different units "
                    f"({', '.join(sorted(by_unit))}); "
                    f"{'no exact conversion exists for all of them' if target else 'no target unit is declared'}."
                ),
                evidence={
                    "units": {
                        unit: len(uids) for unit, uids in sorted(by_unit.items())
                    },
                    "target_unit": target,
                    "exact": convertible,
                    "records": {
                        unit: uids[:5] for unit, uids in sorted(by_unit.items())
                    },
                },
                action="Convert where an exact factor exists, otherwise confirm the unit.",
            )
        )
    return tuple(issues)


def _canonical_unit(plugin: DomainPlugin, observable: str) -> str:
    spec = plugin.observable(observable)
    return spec.canonical_unit if spec else ""


def detect_out_of_range(
    records: Iterable[ObservationRecord],
    *,
    contract: DataRequirementContract,
    plugin: DomainPlugin,
) -> tuple[QualityIssue, ...]:
    """Compare values against the contract's ranges and the plugin's limits."""
    issues: list[QualityIssue] = []
    for record in records:
        value = record.value
        if value is None or not math.isfinite(value):
            continue
        bounds = contract.value_ranges.get(
            record.observable
        ) or plugin.plausibility.get(record.observable)
        if not bounds:
            continue
        low, high = float(bounds[0]), float(bounds[1])
        if low <= value <= high:
            continue
        issues.append(
            _issue(
                IssueCode.OUT_OF_RANGE,
                severity=IssueSeverity.WARNING,
                subject={
                    "record_uid": record.record_uid,
                    "observable": record.observable,
                },
                detail=(
                    f"{record.observable} = {value:g} {record.unit} falls outside the "
                    f"plausible range [{low:g}, {high:g}]."
                ),
                evidence={
                    "value": value,
                    "unit": record.unit,
                    "raw": record.value_raw,
                    "range": [low, high],
                    "entity_id": record.entity_id,
                },
                action="Verify the value or its unit against the source; do not clip it.",
            )
        )
    return tuple(issues)


def detect_missing_coverage(
    records: Iterable[ObservationRecord],
    *,
    contract: DataRequirementContract,
    plugin: DomainPlugin,
) -> tuple[QualityIssue, ...]:
    """Report required observables (or record counts) an entity never provides."""
    wanted = tuple(
        field.name for field in contract.observables if field.required
    ) or tuple(
        name
        for name in plugin.observable_names()
        if (spec := plugin.observable(name)) is not None and spec.required
    )
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for record in records:
        if record.value is None:
            continue
        counts[record.entity_id][record.observable] += 1

    issues: list[QualityIssue] = []
    for entity_id, per_observable in sorted(counts.items()):
        for observable in wanted:
            if per_observable.get(observable):
                continue
            issues.append(
                _issue(
                    IssueCode.MISSING_COVERAGE,
                    severity=IssueSeverity.WARNING,
                    subject={"entity_id": entity_id, "observable": observable},
                    detail=(
                        f"Entity {entity_id} has no usable {observable} value although "
                        "the research contract marks it as required."
                    ),
                    evidence={"found": dict(per_observable), "required": observable},
                    action="Search further sources for this observable before analysis.",
                )
            )
        total = sum(per_observable.values())
        if contract.min_records_per_entity and total < contract.min_records_per_entity:
            issues.append(
                _issue(
                    IssueCode.MISSING_COVERAGE,
                    severity=IssueSeverity.WARNING,
                    subject={"entity_id": entity_id, "scope": "record_count"},
                    detail=(
                        f"Entity {entity_id} contributes {total} record(s), below the "
                        f"contract minimum of {contract.min_records_per_entity}."
                    ),
                    evidence={
                        "records": total,
                        "minimum": contract.min_records_per_entity,
                    },
                    action="Widen the search or lower the requirement; do not duplicate rows.",
                )
            )
    return tuple(issues)


def detect_coverage_gaps(
    records: Iterable[ObservationRecord],
    *,
    plugin: DomainPlugin,
    max_gap_days: float | None = None,
) -> tuple[QualityIssue, ...]:
    """Report long holes in one entity's time series."""
    factor = float(plugin.cadence.get("max_gap_factor", _DEFAULT_GAP_FACTOR) or 1.0)
    cadence = max_gap_days or plugin.cadence.get("expected_gap_days")
    series: dict[tuple[str, str], list[tuple[float, str]]] = defaultdict(list)
    for record in records:
        if record.value is None or not record.time:
            continue
        mjd = iso_to_mjd(record.time)
        if mjd is None:
            continue
        series[(record.entity_id, record.observable)].append((mjd, record.record_uid))

    issues: list[QualityIssue] = []
    for (entity_id, observable), points in sorted(series.items()):
        if len(points) < 2:
            continue
        points.sort()
        gaps = [
            (later[0] - earlier[0], earlier[0], later[0])
            for earlier, later in pairwise(points)
        ]
        # Lower median: one long hole must not raise its own threshold.
        median = sorted(gap for gap, _, _ in gaps)[(len(gaps) - 1) // 2]
        threshold = float(cadence) * factor if cadence else median * factor
        largest = max(gaps)
        if largest[0] <= threshold or threshold <= 0:
            continue
        issues.append(
            _issue(
                IssueCode.COVERAGE_GAP,
                severity=IssueSeverity.WARNING,
                subject={"entity_id": entity_id, "observable": observable},
                detail=(
                    f"Largest gap in {observable} is {largest[0]:.1f} d, above the "
                    f"expected {threshold:.1f} d; the light curve may be incomplete."
                ),
                evidence={
                    "gap_days": largest[0],
                    "threshold_days": threshold,
                    "median_gap_days": median,
                    "points": len(points),
                    "from": largest[1],
                    "to": largest[2],
                },
                action="Look for observations inside the gap; never interpolate silently.",
            )
        )
    return tuple(issues)


def detect_sign_suspect(
    records: Iterable[ObservationRecord],
    *,
    plugin: DomainPlugin,
) -> tuple[QualityIssue, ...]:
    """Flag negative values for observables that cannot physically be negative."""
    issues: list[QualityIssue] = []
    for record in records:
        value = record.value
        if value is None or not math.isfinite(value) or value >= 0:
            continue
        bounds = plugin.plausibility.get(record.observable)
        if bounds is not None and float(bounds[0]) < 0:
            continue
        if bounds is None:
            continue
        issues.append(
            _issue(
                IssueCode.SIGN_OR_PHASE_SUSPECT,
                severity=IssueSeverity.WARNING,
                subject={
                    "record_uid": record.record_uid,
                    "observable": record.observable,
                },
                detail=(
                    f"{record.observable} = {value:g} {record.unit} is negative, but "
                    "this quantity is expected to be non-negative; the sign or the "
                    "phase convention may be transposed."
                ),
                evidence={
                    "value": value,
                    "raw": record.value_raw,
                    "plausible_range": list(bounds),
                },
                action="Confirm the sign convention with the source before flipping it.",
            )
        )
    return tuple(issues)


def detect_conflicts(
    records: Iterable[ObservationRecord],
) -> tuple[QualityIssue, ...]:
    """Re-report every record where two sources disagree, as one issue."""
    issues: list[QualityIssue] = []
    for record in records:
        if not record.conflict:
            continue
        issues.append(
            _issue(
                IssueCode.CROSS_SOURCE_CONFLICT,
                severity=IssueSeverity.ERROR,
                subject={
                    "entity_id": record.entity_id,
                    "observable": record.observable,
                    "time": record.time,
                },
                detail=(
                    f"Sources disagree about {record.observable} for "
                    f"{record.entity_id} at {record.time}: {record.value_raw}."
                ),
                evidence={
                    "record_uid": record.record_uid,
                    "raw_values": record.value_raw,
                    "sources": list(record.source_ids),
                    "kept_value": record.value,
                    "unit": record.unit,
                },
                action="Choose the authoritative source; the kernel never averages.",
            )
        )
    return tuple(issues)


def detect_entity_ambiguity(
    entities: Iterable[EntityRef],
) -> tuple[QualityIssue, ...]:
    """Report entities that could not be told apart from another entity."""
    issues: list[QualityIssue] = []
    for entity in entities:
        if not entity.ambiguous_with:
            continue
        issues.append(
            _issue(
                IssueCode.ENTITY_IDENTITY_AMBIGUOUS,
                severity=IssueSeverity.ERROR,
                subject={"entity_id": entity.entity_id},
                detail=(
                    f"{entity.label} shares an identifier with "
                    f"{', '.join(entity.ambiguous_with)}; merging them would confound "
                    "two objects."
                ),
                evidence={
                    "label": entity.label,
                    "ambiguous_with": list(entity.ambiguous_with),
                    "aliases": [alias.alias for alias in entity.aliases],
                },
                action="Confirm whether these designations are the same object.",
            )
        )
    return tuple(issues)


def detect_all(
    *,
    records: Sequence[ObservationRecord] = (),
    tables: Sequence[ExtractedTable] = (),
    entities: Sequence[EntityRef] = (),
    contract: DataRequirementContract,
    plugin: DomainPlugin,
    max_gap_days: float | None = None,
) -> DetectionReport:
    """Run every detector and summarise what was found."""
    issues: list[QualityIssue] = []
    for detector in (
        detect_table_warnings(tables),
        detect_duplicate_rows(tables),
        detect_nonfinite(records),
        detect_unit_inconsistency(records, contract=contract, plugin=plugin),
        detect_out_of_range(records, contract=contract, plugin=plugin),
        detect_missing_coverage(records, contract=contract, plugin=plugin),
        detect_coverage_gaps(records, plugin=plugin, max_gap_days=max_gap_days),
        detect_sign_suspect(records, plugin=plugin),
        detect_conflicts(records),
        detect_entity_ambiguity(entities),
    ):
        issues.extend(detector)

    scored = tuple(
        record
        for record in records
        if record.value is not None and math.isfinite(record.value)
    )
    stats: dict[str, Any] = {
        "records": len(records),
        "usable_records": len(scored),
        "missing_records": len(records) - len(scored),
        "tables": len(tables),
        "entities": len(entities),
        "issues": len(issues),
        **summarise(issues),
        "mean_confidence": round(_mean_confidence(scored), 4),
    }
    return DetectionReport(issues=tuple(issues), stats=stats)


def _mean_confidence(records: Sequence[ObservationRecord]) -> float:
    """Average extractor confidence score across all provenance references."""
    scores = [
        reference.confidence_score
        for record in records
        for reference in record.provenance
        if reference.confidence_score >= 0
    ]
    return sum(scores) / len(scores) if scores else -1.0


def summarise(issues: Sequence[QualityIssue]) -> dict[str, Any]:
    """Counts by code, severity and repair mode for reports and receipts."""
    by_code: dict[str, int] = defaultdict(int)
    by_severity: dict[str, int] = defaultdict(int)
    by_mode: dict[str, int] = defaultdict(int)
    for issue in issues:
        by_code[str(issue.code)] += 1
        by_severity[str(issue.severity)] += 1
        by_mode[str(issue.repair_mode)] += 1
    return {
        "by_code": dict(sorted(by_code.items())),
        "by_severity": dict(sorted(by_severity.items())),
        "by_mode": dict(sorted(by_mode.items())),
    }


def distinct_units(records: Iterable[ObservationRecord]) -> dict[str, tuple[str, ...]]:
    """Units seen per observable, for reports and unit hygiene checks."""
    seen: dict[str, set[str]] = defaultdict(set)
    for record in records:
        seen[record.observable].add(record.unit or "unspecified")
    return {name: tuple(sorted(units)) for name, units in sorted(seen.items())}


def unconvertible_dimensions(
    records: Iterable[ObservationRecord],
) -> tuple[str, ...]:
    """Observables whose units do not even share a dimension."""
    seen: dict[str, set[str]] = defaultdict(set)
    for record in records:
        if record.unit:
            seen[record.observable].add(record.unit)
    mixed = []
    for observable, units in sorted(seen.items()):
        dimensions = {str(dimension_of(unit)) for unit in units}
        if len(dimensions) > 1:
            mixed.append(observable)
    return tuple(mixed)
