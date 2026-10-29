"""Repair: apply only the fixes that cannot change the science, and prove it.

The rule is simple. A repair runs automatically only when it is *reversible*
and *value-preserving*: dropping an exact duplicate row, replacing a
non-finite token with an explicit missing value, converting between units with
an exact factor. Everything that could change a measurement — a sign, a
cross-source disagreement, an imputed point — is recorded as pending and
queued for a human.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from .contracts import (
    DataRequirementContract,
    ExtractedTable,
    IssueCode,
    IssueSeverity,
    IssueState,
    MissingReason,
    ObservationRecord,
    QualityIssue,
    RepairLedgerEntry,
    RepairMode,
    content_hash,
    default_repair_mode,
    utc_now,
)
from .domain import DomainPlugin, slug
from .timescales import iso_to_mjd
from .units import convert, resolve_unit

__all__ = [
    "RepairDecision",
    "RepairOutcome",
    "apply_auto_repairs",
    "describe_repair",
    "plan_repairs",
]

_METADATA_COLUMNS = ("_row_index",)
_HUMAN_ACTIONS: Mapping[IssueCode, str] = {
    IssueCode.CROSS_SOURCE_CONFLICT: "Choose the authoritative source for each disputed point.",
    IssueCode.SIGN_OR_PHASE_SUSPECT: "Confirm the sign or phase convention with the source.",
    IssueCode.UNIT_UNKNOWN: "Confirm the unit of this column before it is used.",
    IssueCode.ENTITY_IDENTITY_AMBIGUOUS: "Decide whether these designations are one object.",
    IssueCode.ENTITY_IDENTITY_CONFLICT: "Decide whether the coordinates describe one object.",
    IssueCode.AXIS_SCALE_SUSPECT: "Confirm the axis scale (linear or logarithmic).",
    IssueCode.AXIS_TICK_UNREADABLE: "Confirm the axis tick values.",
    IssueCode.LEGEND_MAPPING_AMBIGUOUS: "Confirm which series belongs to which legend entry.",
    IssueCode.FIGURE_OVERLAY_MISMATCH: "Confirm the digitised points against the figure.",
    IssueCode.ERROR_BARS_UNVERIFIED: "Confirm the error bar definition.",
    IssueCode.MISSING_VALUE: "Decide whether to search further or accept the gap.",
}
_REREAD_ACTIONS: Mapping[IssueCode, str] = {
    IssueCode.ENCODING_SUSPECT: "Re-read the file with the detected encoding.",
    IssueCode.DELIMITER_SUSPECT: "Re-read the table with the detected delimiter.",
    IssueCode.FIELD_MAPPING_AMBIGUOUS: "Confirm which column carries which observable.",
    IssueCode.FIELD_UNMAPPED: "Confirm or reject the suggested column mapping.",
    IssueCode.SCHEMA_DRIFT: "Confirm the header layout of this table.",
}


@dataclass(frozen=True, slots=True)
class RepairDecision:
    """What the repair stage intends to do with one detected issue."""

    issue_id: str
    code: IssueCode
    mode: RepairMode
    action: str
    automatic: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "code": str(self.code),
            "mode": str(self.mode),
            "action": self.action,
            "automatic": self.automatic,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class RepairOutcome:
    """Result of one repair pass."""

    records: tuple[ObservationRecord, ...] = ()
    tables: tuple[ExtractedTable, ...] = ()
    repairs: tuple[RepairLedgerEntry, ...] = ()
    issues: tuple[QualityIssue, ...] = ()
    pending: tuple[QualityIssue, ...] = ()
    failed: tuple[str, ...] = ()
    stats: Mapping[str, Any] = field(default_factory=dict)

    @property
    def applied(self) -> tuple[RepairLedgerEntry, ...]:
        return tuple(entry for entry in self.repairs if entry.applied)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stats": dict(self.stats),
            "applied": [entry.repair_id for entry in self.applied],
            "pending": [issue.issue_id for issue in self.pending],
            "failed": list(self.failed),
        }


def plan_repairs(
    issues: Iterable[QualityIssue], *, contract: DataRequirementContract | None = None
) -> tuple[RepairDecision, ...]:
    """Decide, per issue, whether the kernel may act alone.

    A contract can override an authorisation class, but it can never turn a
    ``FORBIDDEN`` repair into an automatic one.
    """
    decisions: list[RepairDecision] = []
    for issue in issues:
        mode = (
            contract.mode_for(issue.code)
            if contract is not None
            else default_repair_mode(issue.code)
        )
        if mode is RepairMode.FORBIDDEN:
            decisions.append(
                RepairDecision(
                    issue_id=issue.issue_id,
                    code=issue.code,
                    mode=mode,
                    action="Leave the value missing; imputation is not permitted.",
                    automatic=False,
                    reason="forbidden_by_policy",
                )
            )
            continue
        if mode is RepairMode.AUTO_SAFE and issue.code in _AUTO_REPAIRS:
            decisions.append(
                RepairDecision(
                    issue_id=issue.issue_id,
                    code=issue.code,
                    mode=mode,
                    action=_AUTO_REPAIRS[issue.code],
                    automatic=True,
                    reason="reversible_and_value_preserving",
                )
            )
            continue
        action = (
            _HUMAN_ACTIONS.get(issue.code)
            or _REREAD_ACTIONS.get(issue.code)
            or issue.proposed_action
            or "Review this finding."
        )
        decisions.append(
            RepairDecision(
                issue_id=issue.issue_id,
                code=issue.code,
                mode=RepairMode.HUMAN_REQUIRED
                if mode is RepairMode.AUTO_SAFE
                else mode,
                action=action,
                automatic=False,
                reason=(
                    "needs_a_reread"
                    if issue.code in _REREAD_ACTIONS
                    else "changes_the_science"
                ),
            )
        )
    return tuple(decisions)


_AUTO_REPAIRS: Mapping[IssueCode, str] = {
    IssueCode.DUPLICATE_EXACT: "Drop later verbatim copies, keeping the first row.",
    IssueCode.NONFINITE_VALUE: "Replace the non-finite token with an explicit missing value.",
    IssueCode.UNIT_INCONSISTENCY: "Convert only units with an exact factor to the contract unit.",
    IssueCode.TRUNCATED_TABLE: "Drop the final incomplete row produced by a truncated read.",
}


def apply_auto_repairs(
    *,
    records: Sequence[ObservationRecord],
    issues: Sequence[QualityIssue],
    tables: Sequence[ExtractedTable] = (),
    contract: DataRequirementContract,
    plugin: DomainPlugin,
    actor: str = "kernel:auto",
) -> RepairOutcome:
    """Apply every authorised automatic repair and revalidate the result."""
    decisions = {
        decision.issue_id: decision
        for decision in plan_repairs(issues, contract=contract)
    }
    repaired_tables, table_repairs, table_failures = _repair_tables(tables, issues)
    repaired_records, record_repairs, record_failures = _repair_records(
        records, issues, contract=contract, plugin=plugin
    )

    repairs = (*table_repairs, *record_repairs)
    by_issue: dict[str, RepairLedgerEntry] = {
        entry.issue_id: entry for entry in repairs if entry.issue_id
    }
    updated: list[QualityIssue] = []
    pending: list[QualityIssue] = []
    for issue in issues:
        decision = decisions.get(issue.issue_id)
        entry = by_issue.get(issue.issue_id)
        if decision is None:
            updated.append(issue)
            continue
        if entry is not None and entry.applied:
            state = (
                IssueState.REVALIDATED
                if entry.revalidation == "passed"
                else IssueState.REVALIDATION_FAILED
            )
            updated.append(
                replace(issue, state=state, auto_applied=True, repair_mode=entry.mode)
            )
            if state is IssueState.REVALIDATION_FAILED:
                pending.append(issue)
            continue
        if decision.mode is RepairMode.FORBIDDEN:
            updated.append(replace(issue, state=IssueState.ACCEPTED_UNRESOLVED))
            continue
        updated.append(replace(issue, state=IssueState.AWAITING_HUMAN))
        pending.append(replace(issue, state=IssueState.AWAITING_HUMAN))

    failures = (*table_failures, *record_failures)
    stats: dict[str, Any] = {
        "issues": len(issues),
        "auto_applied": len(repairs),
        "pending_human": len(pending),
        "failed": len(failures),
        "by_code": _count_by_code(repairs),
        "actor": actor,
    }
    return RepairOutcome(
        records=tuple(repaired_records),
        tables=tuple(repaired_tables),
        repairs=repairs,
        issues=tuple(updated),
        pending=tuple(pending),
        failed=failures,
        stats=stats,
    )


def _count_by_code(repairs: Sequence[RepairLedgerEntry]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for entry in repairs:
        counts[str(entry.code)] += 1
    return dict(sorted(counts.items()))


def _repair_tables(
    tables: Sequence[ExtractedTable], issues: Sequence[QualityIssue]
) -> tuple[list[ExtractedTable], list[RepairLedgerEntry], list[str]]:
    """Drop duplicate and truncated rows at the table level."""
    repairs: list[RepairLedgerEntry] = []
    failures: list[str] = []
    by_table: dict[str, list[QualityIssue]] = defaultdict(list)
    for issue in issues:
        table_id = str(issue.subject.get("table_id") or "")
        if table_id:
            by_table[table_id].append(issue)

    repaired: list[ExtractedTable] = []
    for table in tables:
        rows: list[Mapping[str, Any]] = list(table.rows)
        relevant = by_table.get(table.table_id, [])
        changed = False
        for issue in relevant:
            if issue.code is IssueCode.DUPLICATE_EXACT:
                rows, entry = _drop_duplicate_rows(table, rows, issue)
                if entry is not None:
                    repairs.append(entry)
                    changed = True
            elif issue.code is IssueCode.TRUNCATED_TABLE and _is_truncation(issue):
                rows, entry = _drop_trailing_row(table, rows, issue)
                if entry is not None:
                    repairs.append(entry)
                    changed = True
        if not changed:
            repaired.append(table)
            continue
        rebuilt = ExtractedTable(
            table_id=table.table_id,
            source_id=table.source_id,
            columns=table.columns,
            rows=tuple(rows),
            header_row=table.header_row,
            caption=table.caption,
            provenance=table.provenance,
            format=table.format,
            parse_warnings=table.parse_warnings,
            confidence=table.confidence,
        )
        repaired.append(rebuilt)
        for entry in repairs:
            if entry.target.get("table_id") == table.table_id:
                _mark_revalidated(entry, rows=len(rebuilt.rows), expected=len(rows))
        if any(
            entry.target.get("table_id") == table.table_id
            and entry.revalidation == "failed"
            for entry in repairs
        ):
            failures.append(table.table_id)
    return repaired, repairs, failures


def _is_truncation(issue: QualityIssue) -> bool:
    return str(issue.evidence.get("warning", "")).startswith("possible_truncation")


def _row_key(row: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            (str(name), str(value))
            for name, value in row.items()
            if name not in _METADATA_COLUMNS
        )
    )


def _drop_duplicate_rows(
    table: ExtractedTable, rows: Sequence[Mapping[str, Any]], issue: QualityIssue
) -> tuple[list[Mapping[str, Any]], RepairLedgerEntry | None]:
    seen: set[tuple[tuple[str, str], ...]] = set()
    kept: list[Mapping[str, Any]] = []
    dropped: list[int] = []
    for row in rows:
        key = _row_key(row)
        if key in seen:
            dropped.append(int(row.get("_row_index") or 0))
            continue
        seen.add(key)
        kept.append(row)
    if not dropped:
        return list(rows), None
    entry = RepairLedgerEntry(
        repair_id=f"repair:{table.table_id}:duplicate_exact",
        issue_id=issue.issue_id,
        code=IssueCode.DUPLICATE_EXACT,
        mode=RepairMode.AUTO_SAFE,
        actor="kernel:auto",
        target={"table_id": table.table_id, "source_id": table.source_id},
        before={"rows": len(rows), "dropped": dropped},
        after={"rows": len(kept), "dropped": dropped},
        rationale=(
            "Rows byte-identical to an earlier row were dropped; the first "
            "occurrence keeps its row number and provenance."
        ),
        reversible=True,
        applied=True,
        approved_at=utc_now(),
        before_hash=content_hash([dict(row) for row in rows]),
        after_hash=content_hash([dict(row) for row in kept]),
    )
    return kept, entry


def _drop_trailing_row(
    table: ExtractedTable, rows: Sequence[Mapping[str, Any]], issue: QualityIssue
) -> tuple[list[Mapping[str, Any]], RepairLedgerEntry | None]:
    if not rows:
        return list(rows), None
    kept = list(rows[:-1])
    dropped = rows[-1]
    entry = RepairLedgerEntry(
        repair_id=f"repair:{table.table_id}:truncated_table",
        issue_id=issue.issue_id,
        code=IssueCode.TRUNCATED_TABLE,
        mode=RepairMode.AUTO_SAFE,
        actor="kernel:auto",
        target={"table_id": table.table_id, "source_id": table.source_id},
        before={"rows": len(rows), "last_row": dict(dropped)},
        after={"rows": len(kept)},
        rationale=(
            "The reader flagged the final row as cut off by the end of the file; "
            "dropping it is reversible and removes a partially parsed record."
        ),
        reversible=True,
        applied=True,
        approved_at=utc_now(),
        before_hash=content_hash([dict(row) for row in rows]),
        after_hash=content_hash([dict(row) for row in kept]),
    )
    return kept, entry


def _repair_records(
    records: Sequence[ObservationRecord],
    issues: Sequence[QualityIssue],
    *,
    contract: DataRequirementContract,
    plugin: DomainPlugin,
) -> tuple[list[ObservationRecord], list[RepairLedgerEntry], list[str]]:
    """Fix non-finite values and exact unit conversions at record level."""
    repairs: list[RepairLedgerEntry] = []
    nonfinite_issues = {
        str(issue.subject.get("record_uid") or ""): issue
        for issue in issues
        if issue.code is IssueCode.NONFINITE_VALUE
    }
    unit_issues: dict[str, QualityIssue] = {}
    for issue in issues:
        if issue.code is not IssueCode.UNIT_INCONSISTENCY:
            continue
        observable = str(issue.subject.get("observable") or "")
        if observable:
            unit_issues[observable] = issue
    targets = {
        observable: contract.unit_expectations.get(observable)
        or _canonical_unit(plugin, observable)
        for observable in unit_issues
    }

    repaired: list[ObservationRecord] = []
    for record in records:
        current = record
        if record.record_uid in nonfinite_issues and record.value is not None:
            current = replace(
                current,
                value=None,
                missing_reason=MissingReason.NOT_REPORTED,
                flags=(*current.flags, "repaired:nonfinite_to_missing"),
            )
            repairs.append(
                RepairLedgerEntry(
                    repair_id=f"repair:{record.record_uid}:nonfinite_value",
                    issue_id=nonfinite_issues[record.record_uid].issue_id,
                    code=IssueCode.NONFINITE_VALUE,
                    mode=RepairMode.AUTO_SAFE,
                    actor="kernel:auto",
                    target={"record_uid": record.record_uid},
                    before={"value": repr(record.value), "raw": record.value_raw},
                    after={
                        "value": None,
                        "missing_reason": str(MissingReason.NOT_REPORTED),
                    },
                    rationale=(
                        "A non-finite token is not a measurement; it becomes an "
                        "explicit missing value and the raw text is preserved."
                    ),
                    reversible=True,
                    applied=True,
                    approved_at=utc_now(),
                    before_hash=content_hash({"value": repr(record.value)}),
                    after_hash=content_hash({"value": None}),
                    revalidation="passed",
                    revalidated_at=utc_now(),
                )
            )
        converted, entry = _convert_record_units(
            current, unit_issues, targets, plugin=plugin
        )
        if entry is not None:
            repairs.append(entry)
        repaired.append(converted)

    failures = _revalidate_units(repaired, repairs, unit_issues, targets)
    return repaired, repairs, failures


def _convert_record_units(
    record: ObservationRecord,
    unit_issues: Mapping[str, QualityIssue],
    targets: Mapping[str, str],
    *,
    plugin: DomainPlugin,
) -> tuple[ObservationRecord, RepairLedgerEntry | None]:
    """Convert one record to the contract unit when the factor is exact.

    A partial conversion is allowed: whatever is exactly convertible becomes
    canonical, and the records that are not are left untouched for the
    revalidation step to flag.
    """
    issue = unit_issues.get(record.observable)
    target = targets.get(record.observable, "")
    if issue is None or not target or not record.unit:
        return record, None
    if record.unit == target or record.value is None:
        return record, None
    resolution = resolve_unit(record.unit, quantity=record.observable, plugin=plugin)
    if resolution.canonical != target or resolution.factor is None:
        return record, None
    scaled = convert(record.value, record.unit, target)
    if scaled is None:
        return record, None
    converted = replace(
        record,
        value=scaled,
        unit=target,
        flags=(*record.flags, f"repaired:unit_converted:{record.unit}->{target}"),
    )
    entry = RepairLedgerEntry(
        repair_id=f"repair:{record.record_uid}:unit_inconsistency",
        issue_id=issue.issue_id,
        code=IssueCode.UNIT_INCONSISTENCY,
        mode=RepairMode.AUTO_SAFE,
        actor="kernel:auto",
        target={"record_uid": record.record_uid, "observable": record.observable},
        before={"value": record.value, "unit": record.unit},
        after={"value": scaled, "unit": target},
        rationale=(
            f"Exact conversion {record.unit} -> {target} "
            f"({resolution.note or 'registered factor'}); the raw value survives "
            "in value_raw/unit_raw."
        ),
        reversible=True,
        applied=True,
        approved_at=utc_now(),
        before_hash=content_hash({"value": record.value, "unit": record.unit}),
        after_hash=content_hash({"value": scaled, "unit": target}),
    )
    return converted, entry


def _revalidate_units(
    records: Sequence[ObservationRecord],
    repairs: Sequence[RepairLedgerEntry],
    unit_issues: Mapping[str, QualityIssue],
    targets: Mapping[str, str],
) -> list[str]:
    """Re-run the unit check: a repair only passes if the mix is really gone."""
    failures: list[str] = []
    for observable, issue in unit_issues.items():
        target = targets.get(observable, "")
        remaining = [
            record.record_uid
            for record in records
            if record.observable == observable
            and record.value is not None
            and record.unit
            and target
            and record.unit != target
        ]
        passed = not remaining
        entries = [entry for entry in repairs if entry.issue_id == issue.issue_id]
        for entry in entries:
            entry.revalidation = "passed" if passed else "failed"
            entry.revalidated_at = utc_now()
        if remaining:
            issue.evidence["remaining_units"] = remaining
        if not passed and entries:
            failures.append(issue.issue_id)
    return failures


def _canonical_unit(plugin: DomainPlugin, observable: str) -> str:
    spec = plugin.observable(observable)
    return spec.canonical_unit if spec else ""


def _mark_revalidated(entry: RepairLedgerEntry, *, rows: int, expected: int) -> None:
    """Confirm a table-level repair left a readable, smaller table."""
    entry.revalidation = "passed" if rows == expected and rows >= 0 else "failed"
    entry.revalidated_at = utc_now()


def describe_repair(entry: RepairLedgerEntry) -> str:
    """One-line human-readable summary of a ledger entry."""
    state = "applied" if entry.applied else "pending"
    return (
        f"[{state}] {entry.code} on {slug(str(entry.target))} by {entry.actor}: "
        f"{entry.before} -> {entry.after} ({entry.revalidation})"
    )


def repair_severity(entry: RepairLedgerEntry) -> IssueSeverity:
    """Repairs that changed numbers are worth surfacing in the report."""
    return (
        IssueSeverity.WARNING
        if entry.code in {IssueCode.UNIT_INCONSISTENCY, IssueCode.NONFINITE_VALUE}
        else IssueSeverity.INFO
    )


def unresolved_signals(
    records: Iterable[ObservationRecord], *, max_points: int = 50
) -> tuple[str, ...]:
    """Human-readable list of observations that still need a decision."""
    signals: list[str] = []
    for record in records:
        if not record.conflict:
            continue
        signals.append(
            f"{record.entity_id} {record.observable} @ {record.time}: {record.value_raw}"
        )
        if len(signals) >= max_points:
            break
    return tuple(signals)


def _finite_or_none(value: float | None) -> float | None:
    """Helper kept for symmetry with detectors: non-finite becomes ``None``."""
    if value is None:
        return None
    return value if math.isfinite(value) else None


def _mjd_of(record: ObservationRecord) -> float | None:
    """Sortable time for repair bookkeeping."""
    return iso_to_mjd(record.time) if record.time else None
