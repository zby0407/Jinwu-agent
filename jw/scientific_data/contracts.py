"""Shared contracts for the Scientific Data Integration kernel.

This module is the single source of truth for the data model that every
stage of an integration run exchanges: requirement contracts, source
records, extracted tables, canonical observation records, quality issues,
repair ledger entries, provenance references, stage receipts, and the
integration package index.

Design rules that every stage must honour:

* **Never overwrite raw evidence.**  Canonical values live beside
  ``value_raw``/``unit_raw``/``time_raw`` so any normalisation is reversible.
* **Absence is typed.**  A missing value always carries a
  :class:`MissingReason`; a bare ``None`` is never a scientific statement.
* **Nothing is silently trusted.**  Every extracted value points at a
  :class:`ProvenanceRef` and a confidence recorded by its extractor.
* **Nothing risky is auto-applied.**  Repairs declare their mode; only
  ``RepairMode.AUTO_SAFE`` may run without a human decision.
* **Everything is hash-bound and resumable.**  Stage receipts freeze
  inputs and outputs so a rerun can reuse or invalidate them.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "jw.scientific-data/v1"

__all__ = [
    "SCHEMA_VERSION",
    "ArtifactIndex",
    "ConfidenceBand",
    "DataRequirementContract",
    "EntityAlias",
    "EntityRef",
    "ExtractedTable",
    "FieldMapping",
    "IntegrationLedger",
    "IssueCode",
    "IssueSeverity",
    "IssueState",
    "MissingReason",
    "ObservationField",
    "ObservationRecord",
    "ProvenanceRef",
    "QualityIssue",
    "RepairLedgerEntry",
    "RepairMode",
    "RetrievalState",
    "SourceRecord",
    "SourceRole",
    "StageReceipt",
    "StageState",
    "ValueKind",
    "canonical_json",
    "content_hash",
    "default_repair_mode",
    "file_digest",
    "is_blocking",
    "new_run_id",
    "utc_now",
]


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def utc_now() -> str:
    """Current UTC timestamp in second-precision ISO-8601."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def new_run_id(prefix: str = "sci") -> str:
    """Timestamped, collision-resistant run identifier."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    digest = hashlib.sha256(f"{stamp}{id(object())}".encode()).hexdigest()[:8]
    return f"{prefix}-{stamp}-{digest}"


def canonical_json(payload: Any) -> str:
    """Deterministic JSON encoding used for hashing and manifests."""
    return json.dumps(
        _jsonable(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def content_hash(payload: Any) -> str:
    """``sha256:`` prefixed digest of any JSON-serialisable payload."""
    return (
        "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    )


def file_digest(path: str | Path) -> str:
    """``sha256:`` digest of a file's bytes; empty string when unreadable."""
    candidate = Path(path)
    try:
        return "sha256:" + hashlib.sha256(candidate.read_bytes()).hexdigest()
    except OSError:
        return ""


def _jsonable(payload: Any) -> Any:
    """Recursively convert dataclasses/enums into plain JSON structures."""
    if isinstance(payload, Enum):
        return payload.value
    if isinstance(payload, Mapping):
        return {str(key): _jsonable(value) for key, value in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [_jsonable(item) for item in payload]
    if dataclasses.is_dataclass(payload) and not isinstance(payload, type):
        return _jsonable(asdict(payload))
    if isinstance(payload, datetime):
        return payload.isoformat()
    if isinstance(payload, (str, int, float, bool)) or payload is None:
        return payload
    return str(payload)


def _coerce_enum(enum_type: type[Enum], raw: Any, default: Enum) -> Any:
    """Parse ``raw`` into ``enum_type``, falling back to ``default``."""
    if isinstance(raw, enum_type):
        return raw
    if raw is None:
        return default
    try:
        return enum_type(str(raw))
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SourceRole(StrEnum):
    """What a source contributes, used by conflict and precedence logic."""

    PRIMARY_PUBLISHED = "primary_published"
    CATALOG = "catalog"
    SUPPLEMENT = "supplement"
    FIGURE_DIGITIZED = "figure_digitized"
    AUTHORED_TABLE = "authored_table"
    UNKNOWN = "unknown"


class RetrievalState(StrEnum):
    """Acquisition state of a discovered source."""

    CANDIDATE = "candidate"
    ACQUIRED = "acquired"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class StageState(StrEnum):
    """Execution state of one workflow stage."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    REUSED = "reused"
    PARTIAL = "partial"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class IssueSeverity(StrEnum):
    """How much an issue constrains downstream use."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    BLOCKING = "blocking"


class MissingReason(StrEnum):
    """Typed absence: why a value is not present."""

    NOT_REPORTED = "not_reported"
    NOT_PARSED = "not_parsed"
    NOT_OBSERVED = "not_observed"
    NOT_APPLICABLE = "not_applicable"
    UNDETECTED = "undetected"
    SUPPRESSED = "suppressed"
    UNKNOWN = "unknown"


class ValueKind(StrEnum):
    """Provenance class of a numeric value."""

    REPORTED = "reported"
    DERIVED = "derived"
    DIGITIZED = "digitized"
    UPPER_LIMIT = "upper_limit"
    LOWER_LIMIT = "lower_limit"
    MODEL = "model"


class RepairMode(StrEnum):
    """Authorisation class of a repair rule.

    ``AUTO_SAFE`` rules are reversible, deterministic, and cannot change a
    scientific conclusion.  ``HUMAN_REQUIRED`` rules must be proposed and
    confirmed by a human before they may be applied.  ``FORBIDDEN`` rules are
    reported but never applied.
    """

    AUTO_SAFE = "auto_safe"
    SUGGEST_ONLY = "suggest_only"
    HUMAN_REQUIRED = "human_required"
    FORBIDDEN = "forbidden"


class IssueState(StrEnum):
    """Repair-loop state machine for one detected issue."""

    DETECTED = "detected"
    DIAGNOSED = "diagnosed"
    PROPOSED = "proposed"
    AUTO_REPAIRED = "auto_repaired"
    AWAITING_HUMAN = "awaiting_human"
    HUMAN_APPROVED = "human_approved"
    HUMAN_REJECTED = "human_rejected"
    HUMAN_EDITED = "human_edited"
    REPAIRED = "repaired"
    REVALIDATED = "revalidated"
    REVALIDATION_FAILED = "revalidation_failed"
    ACCEPTED_UNRESOLVED = "accepted_unresolved"
    REJECTED = "rejected"


class ConfidenceBand(StrEnum):
    """Coarse confidence tier carried through every extractor."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNVERIFIED = "unverified"


# ---------------------------------------------------------------------------
# Issue codes
# ---------------------------------------------------------------------------


class IssueCode(StrEnum):
    """Stable codes for every anomaly the kernel recognises.

    ``default_mode`` gives the authorisation class a rule must use unless a
    contract overrides it (see :func:`default_repair_mode`).
    """

    # structure / schema
    SCHEMA_DRIFT = "schema_drift"
    UNREADABLE_SOURCE = "unreadable_source"
    EMPTY_EXTRACT = "empty_extract"
    ENCODING_SUSPECT = "encoding_suspect"
    DELIMITER_SUSPECT = "delimiter_suspect"
    MERGE_KEY_ABSENT = "merge_key_absent"
    FIELD_UNMAPPED = "field_unmapped"
    FIELD_MAPPING_AMBIGUOUS = "field_mapping_ambiguous"
    # completeness
    MISSING_VALUE = "missing_value"
    MISSING_COVERAGE = "missing_coverage"
    COVERAGE_GAP = "coverage_gap"
    TRUNCATED_TABLE = "truncated_table"
    # duplication and conflict
    DUPLICATE_EXACT = "duplicate_exact"
    DUPLICATE_KEY = "duplicate_key"
    CROSS_SOURCE_CONFLICT = "cross_source_conflict"
    UNIT_INCONSISTENCY = "unit_inconsistency"
    UNIT_UNKNOWN = "unit_unknown"
    TIME_SYSTEM_AMBIGUOUS = "time_system_ambiguous"
    NONFINITE_VALUE = "nonfinite_value"
    OUT_OF_RANGE = "out_of_range"
    IMPLAUSIBLE_VALUE = "implausible_value"
    SIGN_OR_PHASE_SUSPECT = "sign_or_phase_suspect"
    # identity
    ENTITY_IDENTITY_AMBIGUOUS = "entity_identity_ambiguous"
    ENTITY_IDENTITY_CONFLICT = "entity_identity_conflict"
    UNSAFE_DEDUP_RISK = "unsafe_dedup_risk"
    # figure / digitization
    AXIS_SCALE_SUSPECT = "axis_scale_suspect"
    AXIS_TICK_UNREADABLE = "axis_tick_unreadable"
    LEGEND_MAPPING_AMBIGUOUS = "legend_mapping_ambiguous"
    DIGITIZATION_LOW_CONFIDENCE = "digitization_low_confidence"
    FIGURE_OVERLAY_MISMATCH = "figure_overlay_mismatch"
    ERROR_BARS_UNVERIFIED = "error_bars_unverified"
    # security
    SOURCE_HOST_DENIED = "source_host_denied"
    SOURCE_SPAM_DETECTED = "source_spam_detected"
    FETCH_FAILED = "fetch_failed"
    # generic
    CONTRACT_UNDERSPECIFIED = "contract_underspecified"
    USER_DECISION_REQUIRED = "user_decision_required"
    POLICY_BLOCK = "policy_block"
    UNKNOWN = "unknown"


_BLOCKING_CODES: frozenset[IssueCode] = frozenset(
    {
        IssueCode.MERGE_KEY_ABSENT,
        IssueCode.UNREADABLE_SOURCE,
        IssueCode.ENTITY_IDENTITY_CONFLICT,
        IssueCode.SOURCE_HOST_DENIED,
        IssueCode.CONTRACT_UNDERSPECIFIED,
        IssueCode.USER_DECISION_REQUIRED,
        IssueCode.POLICY_BLOCK,
    }
)


def default_repair_mode(code: IssueCode) -> RepairMode:
    """Authorisation class a repair rule must use for ``code``."""
    code = _coerce_enum(IssueCode, code, IssueCode.UNKNOWN)
    auto = {
        IssueCode.DUPLICATE_EXACT,
        IssueCode.NONFINITE_VALUE,
        IssueCode.ENCODING_SUSPECT,
        IssueCode.DELIMITER_SUSPECT,
        IssueCode.UNIT_INCONSISTENCY,
        IssueCode.TRUNCATED_TABLE,
        IssueCode.SIGN_OR_PHASE_SUSPECT,
    }
    suggest = {
        IssueCode.MISSING_COVERAGE,
        IssueCode.COVERAGE_GAP,
        IssueCode.OUT_OF_RANGE,
        IssueCode.UNIT_UNKNOWN,
        IssueCode.FIELD_UNMAPPED,
    }
    if code in auto:
        return RepairMode.AUTO_SAFE
    if code in suggest:
        return RepairMode.SUGGEST_ONLY
    if code == IssueCode.MISSING_VALUE:
        # Imputing a missing observation is never automatic.
        return RepairMode.FORBIDDEN
    return RepairMode.HUMAN_REQUIRED


def is_blocking(issue: QualityIssue) -> bool:
    """Whether an issue must stop the pipeline from declaring success."""
    return issue.code in _BLOCKING_CODES or issue.severity is IssueSeverity.BLOCKING


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ObservationField:
    """One canonical output column and how it may be filled."""

    name: str
    kind: str = "quantity"  # quantity | time | identifier | label | quality
    unit: str = ""
    required: bool = False
    description: str = ""
    aliases: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


@dataclass(slots=True)
class DataRequirementContract:
    """Compiled "research question → usable data" specification.

    The contract is the only place where a user's intent becomes machine
    readable; every stage must read its policy from here rather than from
    model improvisation.
    """

    contract_id: str
    research_goal: str
    entity_class: str = ""  # e.g. "type Ia supernova"
    observables: tuple[ObservationField, ...] = ()
    identifier_fields: tuple[str, ...] = ()
    time_field: str = ""
    merge_keys: tuple[str, ...] = ()
    domain_plugin: str = "generic"
    accepted_roles: tuple[str, ...] = (
        SourceRole.PRIMARY_PUBLISHED.value,
        SourceRole.CATALOG.value,
        SourceRole.SUPPLEMENT.value,
        SourceRole.FIGURE_DIGITIZED.value,
    )
    target_entity_count: int = 0
    min_records_per_entity: int = 1
    unit_expectations: Mapping[str, str] = field(default_factory=dict)
    value_ranges: Mapping[str, tuple[float, float]] = field(default_factory=dict)
    coverage_window: tuple[str, str] = ()  # inclusive ISO bounds
    budget: Mapping[str, Any] = field(
        default_factory=lambda: {
            "max_sources": 40,
            "max_fetches": 60,
            "max_tables": 200,
            "wall_seconds": 900,
        }
    )
    repair_overrides: Mapping[str, str] = field(default_factory=dict)
    ambiguities: tuple[str, ...] = ()
    provenance: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> DataRequirementContract:
        fields = tuple(
            ObservationField(**item) if isinstance(item, Mapping) else item
            for item in raw.get("observables") or ()
        )
        payload = dict(raw)
        payload["observables"] = fields
        known = set(cls.__dataclass_fields__)
        return cls(**{key: value for key, value in payload.items() if key in known})

    def mode_for(self, code: IssueCode) -> RepairMode:
        """Contract-adjusted repair authorisation for ``code``."""
        override = self.repair_overrides.get(str(getattr(code, "value", code)))
        if override:
            return _coerce_enum(RepairMode, override, default_repair_mode(code))
        return default_repair_mode(code)

    def requires_human(self, code: IssueCode) -> bool:
        """Whether ``code`` must be escalated to a human decision."""
        mode = self.mode_for(code)
        return mode in {RepairMode.HUMAN_REQUIRED, RepairMode.FORBIDDEN}

    @property
    def fingerprint(self) -> str:
        """Content hash used to bind runs to one contract."""
        return content_hash(self.to_dict())


# ---------------------------------------------------------------------------
# Sources and extraction
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ProvenanceRef:
    """Where exactly a value came from, down to a page/figure/region."""

    source_id: str
    locator: Mapping[str, Any] = field(default_factory=dict)
    # locator keys: table_id, page, figure, panel, region[x0,y0,x1,y1],
    # row_index, column, url, caption
    extractor: str = ""
    extractor_version: str = ""
    value_kind: ValueKind = ValueKind.REPORTED
    confidence: ConfidenceBand = ConfidenceBand.UNVERIFIED
    confidence_score: float = -1.0
    raw_excerpt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ProvenanceRef:
        return cls(
            source_id=str(raw.get("source_id") or ""),
            locator=dict(raw.get("locator") or {}),
            extractor=str(raw.get("extractor") or ""),
            extractor_version=str(raw.get("extractor_version") or ""),
            value_kind=_coerce_enum(
                ValueKind, raw.get("value_kind"), ValueKind.REPORTED
            ),
            confidence=_coerce_enum(
                ConfidenceBand, raw.get("confidence"), ConfidenceBand.UNVERIFIED
            ),
            confidence_score=float(raw.get("confidence_score", -1.0)),
            raw_excerpt=str(raw.get("raw_excerpt") or ""),
        )


@dataclass(slots=True)
class SourceRecord:
    """One discovered source: paper, dataset, supplement, or figure."""

    source_id: str
    role: SourceRole = SourceRole.UNKNOWN
    title: str = ""
    url: str = ""
    canonical_uri: str = ""
    provider: str = ""
    retrieval: RetrievalState = RetrievalState.CANDIDATE
    local_path: str = ""
    content_sha256: str = ""
    bytes: int = 0
    license: str = ""
    version: str = ""
    published: str = ""
    discovered_via: str = ""
    notes: tuple[str, ...] = ()
    failure_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> SourceRecord:
        return cls(
            source_id=str(raw.get("source_id") or ""),
            role=_coerce_enum(SourceRole, raw.get("role"), SourceRole.UNKNOWN),
            title=str(raw.get("title") or ""),
            url=str(raw.get("url") or ""),
            canonical_uri=str(raw.get("canonical_uri") or ""),
            provider=str(raw.get("provider") or ""),
            retrieval=_coerce_enum(
                RetrievalState, raw.get("retrieval"), RetrievalState.CANDIDATE
            ),
            local_path=str(raw.get("local_path") or ""),
            content_sha256=str(raw.get("content_sha256") or ""),
            bytes=int(raw.get("bytes") or 0),
            license=str(raw.get("license") or ""),
            version=str(raw.get("version") or ""),
            published=str(raw.get("published") or ""),
            discovered_via=str(raw.get("discovered_via") or ""),
            notes=tuple(raw.get("notes") or ()),
            failure_reason=str(raw.get("failure_reason") or ""),
        )


@dataclass(slots=True)
class ExtractedTable:
    """One table extracted from one source, in raw (un-normalised) form."""

    table_id: str
    source_id: str
    columns: tuple[str, ...] = ()
    rows: tuple[Mapping[str, Any], ...] = ()
    header_row: int = -1
    caption: str = ""
    provenance: ProvenanceRef | None = None
    format: str = "csv"
    parse_warnings: tuple[str, ...] = ()
    confidence: ConfidenceBand = ConfidenceBand.HIGH

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def to_dict(self) -> dict[str, Any]:
        payload = _jsonable(asdict(self))
        payload["row_count"] = self.row_count
        return payload

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ExtractedTable:
        provenance = raw.get("provenance")
        return cls(
            table_id=str(raw.get("table_id") or ""),
            source_id=str(raw.get("source_id") or ""),
            columns=tuple(str(item) for item in raw.get("columns") or ()),
            rows=tuple(dict(item) for item in raw.get("rows") or ()),
            header_row=int(raw.get("header_row", -1)),
            caption=str(raw.get("caption") or ""),
            provenance=(
                ProvenanceRef.from_dict(provenance)
                if isinstance(provenance, Mapping)
                else None
            ),
            format=str(raw.get("format") or "csv"),
            parse_warnings=tuple(str(item) for item in raw.get("parse_warnings") or ()),
            confidence=_coerce_enum(
                ConfidenceBand, raw.get("confidence"), ConfidenceBand.HIGH
            ),
        )


# ---------------------------------------------------------------------------
# Entities and observations
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class EntityAlias:
    """One identifier form of an entity, with the source that used it."""

    alias: str
    normalised: str
    source_id: str = ""
    alias_kind: str = "name"  # name | designation | coord | url | accession

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


@dataclass(slots=True)
class EntityRef:
    """A resolved entity (one object of study) across sources."""

    entity_id: str
    label: str
    aliases: tuple[EntityAlias, ...] = ()
    coordinates: Mapping[str, float] = field(default_factory=dict)
    classification: str = ""
    link_confidence: ConfidenceBand = ConfidenceBand.HIGH
    link_evidence: tuple[str, ...] = ()
    ambiguous_with: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


@dataclass(slots=True)
class ObservationRecord:
    """One canonical observation: entity / observable / time.

    ``value`` is the canonicalised number in ``unit``; the raw author text is
    preserved so every normalisation is reversible.
    """

    record_uid: str
    entity_id: str
    observable: str
    unit: str = ""
    value: float | None = None
    value_raw: str = ""
    unit_raw: str = ""
    time: str = ""
    time_raw: str = ""
    time_system: str = ""
    uncertainty: float | None = None
    uncertainty_kind: str = ""  # sigma1 | sigma95 | symmetric | asymmetric | none
    uncertainty_low: float | None = None
    value_kind: ValueKind = ValueKind.REPORTED
    missing_reason: MissingReason = MissingReason.UNKNOWN
    source_ids: tuple[str, ...] = ()
    provenance: tuple[ProvenanceRef, ...] = ()
    field_map: Mapping[str, str] = field(default_factory=dict)
    flags: tuple[str, ...] = ()
    conflict: bool = False
    # per-row labels that the domain uses as merge keys (band, classification...)
    attributes: Mapping[str, str] = field(default_factory=dict)

    @property
    def merge_key(self) -> tuple[str, str, str]:
        """Canonical alignment key for this observation."""
        return (self.entity_id, self.observable, self.time)

    def alignment_key(self, keys: Sequence[str] | None = None) -> tuple[str, ...]:
        """Merge key under the contract's key list (defaults to the core three)."""
        names = tuple(keys or ("entity_id", "observable", "time"))
        values: dict[str, str] = {
            "entity_id": self.entity_id,
            "observable": self.observable,
            "time": self.time,
            # "time_key" is the domain-plugin spelling of the same axis.
            "time_key": self.time,
            "time_raw": self.time_raw,
            "time_system": self.time_system,
            "unit": self.unit,
            "value_kind": str(self.value_kind),
            "entity_label": self.attributes.get("label", ""),
        }
        values.update({str(key): str(value) for key, value in self.attributes.items()})
        return tuple(values.get(name, "") for name in names)

    def to_dict(self) -> dict[str, Any]:
        payload = _jsonable(asdict(self))
        payload["flags"] = list(self.flags)
        return payload

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ObservationRecord:
        provenance = raw.get("provenance") or ()
        return cls(
            record_uid=str(raw.get("record_uid") or ""),
            entity_id=str(raw.get("entity_id") or ""),
            observable=str(raw.get("observable") or ""),
            unit=str(raw.get("unit") or ""),
            value=_opt_float(raw.get("value")),
            value_raw=str(raw.get("value_raw") or ""),
            unit_raw=str(raw.get("unit_raw") or ""),
            time=str(raw.get("time") or ""),
            time_raw=str(raw.get("time_raw") or ""),
            time_system=str(raw.get("time_system") or ""),
            uncertainty=_opt_float(raw.get("uncertainty")),
            uncertainty_kind=str(raw.get("uncertainty_kind") or ""),
            uncertainty_low=_opt_float(raw.get("uncertainty_low")),
            value_kind=_coerce_enum(
                ValueKind, raw.get("value_kind"), ValueKind.REPORTED
            ),
            missing_reason=_coerce_enum(
                MissingReason, raw.get("missing_reason"), MissingReason.UNKNOWN
            ),
            source_ids=tuple(str(item) for item in raw.get("source_ids") or ()),
            provenance=tuple(
                ProvenanceRef.from_dict(item)
                for item in provenance
                if isinstance(item, Mapping)
            ),
            field_map=dict(raw.get("field_map") or {}),
            flags=tuple(str(item) for item in raw.get("flags") or ()),
            conflict=bool(raw.get("conflict")),
            attributes={
                str(key): str(value)
                for key, value in (raw.get("attributes") or {}).items()
            },
        )


_NUMBER_RE = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


def _opt_float(raw: Any) -> float | None:
    """Parse a float, returning ``None`` for empty/NaN/non-numeric input."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
    else:
        text = str(raw).strip().replace("_", "").replace("\u2212", "-")
        if not text:
            return None
        match = _NUMBER_RE.search(text)
        if not match:
            return None
        value = float(match.group())
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return value


# ---------------------------------------------------------------------------
# Quality issues and repairs
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class QualityIssue:
    """One detected anomaly, with the evidence and a proposed repair."""

    issue_id: str
    code: IssueCode
    severity: IssueSeverity
    state: IssueState = IssueState.DETECTED
    stage: str = ""
    subject: Mapping[str, Any] = field(default_factory=dict)
    # subject keys: entity_id, record_uid, source_id, table_id, field, index
    detail: str = ""
    evidence: Mapping[str, Any] = field(default_factory=dict)
    proposed_action: str = ""
    repair_mode: RepairMode = RepairMode.SUGGEST_ONLY
    auto_applied: bool = False

    @property
    def blocking(self) -> bool:
        return is_blocking(self)

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> QualityIssue:
        return cls(
            issue_id=str(raw.get("issue_id") or ""),
            code=_coerce_enum(IssueCode, raw.get("code"), IssueCode.UNKNOWN),
            severity=_coerce_enum(
                IssueSeverity, raw.get("severity"), IssueSeverity.WARNING
            ),
            state=_coerce_enum(IssueState, raw.get("state"), IssueState.DETECTED),
            stage=str(raw.get("stage") or ""),
            subject=dict(raw.get("subject") or {}),
            detail=str(raw.get("detail") or ""),
            evidence=dict(raw.get("evidence") or {}),
            proposed_action=str(raw.get("proposed_action") or ""),
            repair_mode=_coerce_enum(
                RepairMode, raw.get("repair_mode"), RepairMode.SUGGEST_ONLY
            ),
            auto_applied=bool(raw.get("auto_applied")),
        )


@dataclass(slots=True)
class RepairLedgerEntry:
    """An auditable, reversible change applied to the integrated dataset."""

    repair_id: str
    issue_id: str
    code: IssueCode
    mode: RepairMode
    actor: str = "kernel"  # kernel | user:<handle> | model-suggestion
    target: Mapping[str, Any] = field(default_factory=dict)
    before: Mapping[str, Any] = field(default_factory=dict)
    after: Mapping[str, Any] = field(default_factory=dict)
    rationale: str = ""
    reversible: bool = True
    applied: bool = False
    approved_at: str = ""
    revalidated_at: str = ""
    revalidation: str = "not_run"  # not_run | passed | failed
    before_hash: str = ""
    after_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> RepairLedgerEntry:
        return cls(
            repair_id=str(raw.get("repair_id") or ""),
            issue_id=str(raw.get("issue_id") or ""),
            code=_coerce_enum(IssueCode, raw.get("code"), IssueCode.UNKNOWN),
            mode=_coerce_enum(RepairMode, raw.get("mode"), RepairMode.SUGGEST_ONLY),
            actor=str(raw.get("actor") or "kernel"),
            target=dict(raw.get("target") or {}),
            before=dict(raw.get("before") or {}),
            after=dict(raw.get("after") or {}),
            rationale=str(raw.get("rationale") or ""),
            reversible=bool(raw.get("reversible", True)),
            applied=bool(raw.get("applied")),
            approved_at=str(raw.get("approved_at") or ""),
            revalidated_at=str(raw.get("revalidated_at") or ""),
            revalidation=str(raw.get("revalidation") or "not_run"),
            before_hash=str(raw.get("before_hash") or ""),
            after_hash=str(raw.get("after_hash") or ""),
        )


# ---------------------------------------------------------------------------
# Field mapping
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FieldMapping:
    """Source column → canonical observable mapping, with its evidence."""

    mapping_id: str
    source_id: str
    table_id: str
    source_column: str
    observable: str
    target_unit: str = ""
    source_unit: str = ""
    transformation: str = ""
    confidence: ConfidenceBand = ConfidenceBand.MEDIUM
    evidence: str = ""
    applied: bool = True

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> FieldMapping:
        return cls(
            mapping_id=str(raw.get("mapping_id") or ""),
            source_id=str(raw.get("source_id") or ""),
            table_id=str(raw.get("table_id") or ""),
            source_column=str(raw.get("source_column") or ""),
            observable=str(raw.get("observable") or ""),
            target_unit=str(raw.get("target_unit") or ""),
            source_unit=str(raw.get("source_unit") or ""),
            transformation=str(raw.get("transformation") or ""),
            confidence=_coerce_enum(
                ConfidenceBand, raw.get("confidence"), ConfidenceBand.MEDIUM
            ),
            evidence=str(raw.get("evidence") or ""),
            applied=bool(raw.get("applied", True)),
        )


# ---------------------------------------------------------------------------
# Run bookkeeping
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class StageReceipt:
    """Frozen evidence that one stage ran against one input state."""

    stage: str
    state: StageState = StageState.PENDING
    input_fingerprint: str = ""
    output_fingerprint: str = ""
    counts: Mapping[str, int] = field(default_factory=dict)
    artifacts: tuple[str, ...] = ()
    started_at: str = ""
    finished_at: str = ""
    reused_from: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


@dataclass(slots=True)
class ArtifactIndex:
    """One produced file, path-anchored to the run workspace."""

    role: str
    path: str
    sha256: str = ""
    rows: int = 0
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


# ---------------------------------------------------------------------------
# Ledger — the in-memory product of an integration run
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class IntegrationLedger:
    """Everything one integration run knows, serialisable to ``ledger.json``.

    The ledger is the contract between stages: discovery writes sources,
    extraction writes tables, alignment writes records/entities, quality
    writes issues, repair writes the ledger entries, and the package writer
    reads all of it.
    """

    run_id: str
    contract: DataRequirementContract
    workspace: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    sources: dict[str, SourceRecord] = field(default_factory=dict)
    tables: dict[str, ExtractedTable] = field(default_factory=dict)
    entities: dict[str, EntityRef] = field(default_factory=dict)
    records: list[ObservationRecord] = field(default_factory=list)
    mappings: list[FieldMapping] = field(default_factory=list)
    issues: dict[str, QualityIssue] = field(default_factory=dict)
    repairs: dict[str, RepairLedgerEntry] = field(default_factory=dict)
    stages: dict[str, StageReceipt] = field(default_factory=dict)
    artifacts: dict[str, ArtifactIndex] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    # -- sources ---------------------------------------------------------
    def add_source(self, record: SourceRecord) -> SourceRecord:
        """Register (or replace by id) one source; ids must be stable."""
        if not record.source_id:
            raise ValueError("SourceRecord requires a source_id")
        self.sources[record.source_id] = record
        return record

    def acquired_sources(self) -> tuple[SourceRecord, ...]:
        return tuple(
            item
            for item in self.sources.values()
            if item.retrieval in {RetrievalState.ACQUIRED, RetrievalState.PARTIAL}
        )

    # -- tables ----------------------------------------------------------
    def add_table(self, table: ExtractedTable) -> ExtractedTable:
        if not table.table_id:
            raise ValueError("ExtractedTable requires a table_id")
        self.tables[table.table_id] = table
        return table

    # -- entities --------------------------------------------------------
    def add_entity(self, entity: EntityRef) -> EntityRef:
        if entity.entity_id in self.entities:
            return self.entities[entity.entity_id]
        self.entities[entity.entity_id] = entity
        return entity

    # -- records ---------------------------------------------------------
    def add_record(self, record: ObservationRecord) -> ObservationRecord:
        self.records.append(record)
        return record

    def add_records(self, records: Iterable[ObservationRecord]) -> None:
        self.records.extend(records)

    def record_index(self) -> dict[str, ObservationRecord]:
        return {item.record_uid: item for item in self.records}

    # -- issues ----------------------------------------------------------
    def add_issue(self, issue: QualityIssue) -> QualityIssue:
        if not issue.issue_id:
            raise ValueError("QualityIssue requires an issue_id")
        self.issues.setdefault(issue.issue_id, issue)
        return self.issues[issue.issue_id]

    def issues_for(self, code: IssueCode | None = None) -> tuple[QualityIssue, ...]:
        if code is None:
            return tuple(self.issues.values())
        wanted = _coerce_enum(IssueCode, code, IssueCode.UNKNOWN)
        return tuple(item for item in self.issues.values() if item.code is wanted)

    # -- repairs ---------------------------------------------------------
    def add_repair(self, entry: RepairLedgerEntry) -> RepairLedgerEntry:
        self.repairs[entry.repair_id] = entry
        return entry

    # -- stages ----------------------------------------------------------
    def stage(self, name: str) -> StageReceipt:
        return self.stages.setdefault(name, StageReceipt(stage=name))

    def mark_stage(
        self,
        name: str,
        state: StageState,
        *,
        counts: Mapping[str, int] | None = None,
        artifacts: Sequence[str] | None = None,
        error: str = "",
        input_fingerprint: str = "",
        output_fingerprint: str = "",
    ) -> StageReceipt:
        receipt = self.stage(name)
        receipt.state = _coerce_enum(StageState, state, StageState.PENDING)
        if counts is not None:
            receipt.counts = {str(key): int(value) for key, value in counts.items()}
        if artifacts is not None:
            receipt.artifacts = tuple(artifacts)
        if error:
            receipt.error = error
        if input_fingerprint:
            receipt.input_fingerprint = input_fingerprint
        if output_fingerprint:
            receipt.output_fingerprint = output_fingerprint
        receipt.finished_at = utc_now()
        self.updated_at = receipt.finished_at
        return receipt

    # -- artifacts -------------------------------------------------------
    def add_artifact(self, artifact: ArtifactIndex) -> ArtifactIndex:
        self.artifacts[artifact.role] = artifact
        return artifact

    # -- serialisation ---------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> IntegrationLedger:
        contract_raw = raw.get("contract")
        ledger = cls(
            run_id=str(raw.get("run_id") or ""),
            contract=(
                DataRequirementContract.from_dict(contract_raw)
                if isinstance(contract_raw, Mapping)
                else DataRequirementContract(contract_id="legacy", research_goal="")
            ),
            workspace=str(raw.get("workspace") or ""),
        )
        ledger.created_at = str(raw.get("created_at") or ledger.created_at)
        ledger.updated_at = str(raw.get("updated_at") or ledger.updated_at)
        ledger.sources = {
            str(key): SourceRecord.from_dict(value)
            for key, value in (raw.get("sources") or {}).items()
        }
        ledger.tables = {
            str(key): ExtractedTable.from_dict(value)
            for key, value in (raw.get("tables") or {}).items()
        }
        ledger.records = [
            ObservationRecord.from_dict(item) for item in (raw.get("records") or ())
        ]
        ledger.issues = {
            str(key): QualityIssue.from_dict(value)
            for key, value in (raw.get("issues") or {}).items()
        }
        ledger.repairs = {
            str(key): RepairLedgerEntry.from_dict(value)
            for key, value in (raw.get("repairs") or {}).items()
        }
        ledger.mappings = [
            FieldMapping.from_dict(item)
            for item in (raw.get("mappings") or ())
            if isinstance(item, Mapping)
        ]
        ledger.notes = tuple(str(item) for item in (raw.get("notes") or ()))
        ledger.entities = {
            str(key): EntityRef(
                entity_id=str(value.get("entity_id") or key),
                label=str(value.get("label") or ""),
                aliases=tuple(
                    EntityAlias(**item)
                    for item in (value.get("aliases") or ())
                    if isinstance(item, Mapping)
                ),
                coordinates=dict(value.get("coordinates") or {}),
                classification=str(value.get("classification") or ""),
                link_confidence=_coerce_enum(
                    ConfidenceBand, value.get("link_confidence"), ConfidenceBand.HIGH
                ),
                link_evidence=tuple(
                    str(item) for item in (value.get("link_evidence") or ())
                ),
                ambiguous_with=tuple(
                    str(item) for item in (value.get("ambiguous_with") or ())
                ),
            )
            for key, value in (raw.get("entities") or {}).items()
        }
        ledger.stages = {
            str(key): StageReceipt(
                stage=str(value.get("stage") or key),
                state=_coerce_enum(StageState, value.get("state"), StageState.PENDING),
                input_fingerprint=str(value.get("input_fingerprint") or ""),
                output_fingerprint=str(value.get("output_fingerprint") or ""),
                counts={
                    str(name): int(count)
                    for name, count in (value.get("counts") or {}).items()
                },
                artifacts=tuple(str(item) for item in (value.get("artifacts") or ())),
                started_at=str(value.get("started_at") or ""),
                finished_at=str(value.get("finished_at") or ""),
                reused_from=str(value.get("reused_from") or ""),
                error=str(value.get("error") or ""),
            )
            for key, value in (raw.get("stages") or {}).items()
        }
        ledger.artifacts = {
            str(key): ArtifactIndex(
                role=str(value.get("role") or key),
                path=str(value.get("path") or ""),
                sha256=str(value.get("sha256") or ""),
                rows=int(value.get("rows") or 0),
                note=str(value.get("note") or ""),
            )
            for key, value in (raw.get("artifacts") or {}).items()
        }
        return ledger

    def write(self, path: str | Path) -> str:
        """Atomically persist the ledger; returns the path written."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, target)
        return str(target)

    def summary_counts(self) -> dict[str, int]:
        """Compact counts for receipts, logs, and tool responses."""
        return {
            "sources": len(self.sources),
            "acquired_sources": len(self.acquired_sources()),
            "tables": len(self.tables),
            "entities": len(self.entities),
            "records": len(self.records),
            "issues": len(self.issues),
            "open_issues": len(
                tuple(
                    item
                    for item in self.issues.values()
                    if item.state
                    not in {
                        IssueState.REVALIDATED,
                        IssueState.ACCEPTED_UNRESOLVED,
                        IssueState.REJECTED,
                    }
                )
            ),
            "repairs": len(self.repairs),
            "applied_repairs": len(
                tuple(item for item in self.repairs.values() if item.applied)
            ),
            "conflicts": len(tuple(item for item in self.records if item.conflict)),
        }
