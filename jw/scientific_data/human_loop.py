"""Human loop: turn unresolved findings into decisions a person can make.

The kernel never guesses at a sign, an identity or a disputed value. Instead it
writes a question per finding — with the evidence, the candidate answers and a
recommendation — and it refuses to call a run finished while a blocking
question is still open.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from .contracts import (
    IssueCode,
    IssueSeverity,
    IssueState,
    ObservationRecord,
    QualityIssue,
    RepairLedgerEntry,
    RepairMode,
    content_hash,
    utc_now,
)
from .domain import slug
from .units import resolve_unit

__all__ = [
    "HumanAnswer",
    "HumanQueue",
    "HumanRequest",
    "HumanResolution",
    "RequestState",
    "apply_answers",
    "build_queue",
]

_ACCEPT = "accept"
_REJECT = "reject"
_SEARCH = "search_more"
_DEFAULT_SEVERITY = IssueSeverity.WARNING


class RequestState(StrEnum):
    """Lifecycle of one question to a human."""

    OPEN = "open"
    ANSWERED = "answered"
    DECLINED = "declined"
    DEFERRED = "deferred"


@dataclass(frozen=True, slots=True)
class HumanRequest:
    """One question with its evidence and the answers we would accept."""

    request_id: str
    issue_ids: tuple[str, ...]
    code: IssueCode
    question: str
    options: tuple[str, ...]
    recommended: str = ""
    blocking: bool = True
    severity: IssueSeverity = _DEFAULT_SEVERITY
    evidence: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = ""
    state: RequestState = RequestState.OPEN

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "issue_ids": list(self.issue_ids),
            "code": str(self.code),
            "question": self.question,
            "options": list(self.options),
            "recommended": self.recommended,
            "blocking": self.blocking,
            "severity": str(self.severity),
            "evidence": _jsonable(self.evidence),
            "created_at": self.created_at,
            "state": str(self.state),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> HumanRequest:
        return cls(
            request_id=str(raw["request_id"]),
            issue_ids=tuple(str(item) for item in raw.get("issue_ids") or ()),
            code=IssueCode(str(raw["code"])),
            question=str(raw.get("question") or ""),
            options=tuple(str(item) for item in raw.get("options") or ()),
            recommended=str(raw.get("recommended") or ""),
            blocking=bool(raw.get("blocking", True)),
            severity=IssueSeverity(str(raw.get("severity") or IssueSeverity.WARNING)),
            evidence=dict(raw.get("evidence") or {}),
            created_at=str(raw.get("created_at") or ""),
            state=RequestState(str(raw.get("state") or RequestState.OPEN)),
        )


@dataclass(frozen=True, slots=True)
class HumanAnswer:
    """What a person decided about one request."""

    request_id: str
    choice: str
    note: str = ""
    actor: str = "user"
    decided_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "choice": self.choice,
            "note": self.note,
            "actor": self.actor,
            "decided_at": self.decided_at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> HumanAnswer:
        return cls(
            request_id=str(raw["request_id"]),
            choice=str(raw["choice"]),
            note=str(raw.get("note") or ""),
            actor=str(raw.get("actor") or "user"),
            decided_at=str(raw.get("decided_at") or ""),
        )


@dataclass(frozen=True, slots=True)
class HumanQueue:
    """Every open question for a run, plus the answers already given."""

    requests: tuple[HumanRequest, ...] = ()
    answers: tuple[HumanAnswer, ...] = ()

    def for_request(self, request_id: str) -> HumanRequest | None:
        return next(
            (item for item in self.requests if item.request_id == request_id), None
        )

    def answer(
        self, request_id: str, choice: str, *, note="", actor="user"
    ) -> HumanQueue:
        """Record an answer and move the request out of the open state."""
        request = self.for_request(request_id)
        if request is None:
            raise KeyError(f"unknown request: {request_id}")
        if choice not in request.options:
            raise ValueError(
                f"{choice!r} is not one of the offered options for {request_id}"
            )
        answer = HumanAnswer(
            request_id=request_id,
            choice=choice,
            note=note,
            actor=actor,
            decided_at=utc_now(),
        )
        state = (
            RequestState.DECLINED
            if choice in {_REJECT, _SEARCH}
            else RequestState.ANSWERED
        )
        updated = tuple(
            replace(item, state=state) if item.request_id == request_id else item
            for item in self.requests
        )
        answers = (
            *(item for item in self.answers if item.request_id != request_id),
            answer,
        )
        return HumanQueue(requests=updated, answers=answers)

    def answer_for(self, request_id: str) -> HumanAnswer | None:
        return next(
            (item for item in self.answers if item.request_id == request_id), None
        )

    @property
    def open_requests(self) -> tuple[HumanRequest, ...]:
        return tuple(item for item in self.requests if item.state is RequestState.OPEN)

    @property
    def blocking_open(self) -> tuple[HumanRequest, ...]:
        return tuple(item for item in self.open_requests if item.blocking)

    @property
    def ready(self) -> bool:
        """A run may only be finalised once no blocking question is open."""
        return not self.blocking_open

    def to_dict(self) -> dict[str, Any]:
        return {
            "requests": [item.to_dict() for item in self.requests],
            "answers": [item.to_dict() for item in self.answers],
            "open": len(self.open_requests),
            "blocking_open": len(self.blocking_open),
            "ready": self.ready,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> HumanQueue:
        return cls(
            requests=tuple(
                HumanRequest.from_dict(item) for item in raw.get("requests") or ()
            ),
            answers=tuple(
                HumanAnswer.from_dict(item) for item in raw.get("answers") or ()
            ),
        )

    def write(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        return target

    @classmethod
    def load(cls, path: str | Path) -> HumanQueue:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def build_queue(
    issues: Iterable[QualityIssue], *, records: Sequence[ObservationRecord] = ()
) -> HumanQueue:
    """Ask a question for every finding the kernel may not settle alone."""
    by_observable: dict[str, list[ObservationRecord]] = {}
    for record in records:
        by_observable.setdefault(record.observable, []).append(record)

    requests: list[HumanRequest] = []
    for issue in issues:
        if issue.repair_mode is RepairMode.AUTO_SAFE and issue.auto_applied:
            continue
        if issue.state in {IssueState.REVALIDATED, IssueState.REJECTED}:
            continue
        requests.append(_request_for(issue, by_observable))
    return HumanQueue(requests=tuple(requests))


def _request_for(
    issue: QualityIssue, by_observable: Mapping[str, Sequence[ObservationRecord]]
) -> HumanRequest:
    """Build the question, the options and a recommendation for one issue."""
    observable = str(issue.subject.get("observable") or "")
    blocking = issue.blocking or issue.code in {
        IssueCode.CROSS_SOURCE_CONFLICT,
        IssueCode.ENTITY_IDENTITY_CONFLICT,
        IssueCode.ENTITY_IDENTITY_AMBIGUOUS,
    }
    question, options, recommended = _prompt(issue, observable, by_observable)
    return HumanRequest(
        request_id=f"ask:{slug(str(issue.code))}:{slug(issue.issue_id)[:60]}",
        issue_ids=(issue.issue_id,),
        code=issue.code,
        question=question,
        options=options,
        recommended=recommended,
        blocking=blocking,
        severity=issue.severity,
        evidence={
            "detail": issue.detail,
            "subject": dict(issue.subject),
            "evidence": _jsonable(issue.evidence),
        },
        created_at=utc_now(),
    )


def _prompt(
    issue: QualityIssue,
    observable: str,
    by_observable: Mapping[str, Sequence[ObservationRecord]],
) -> tuple[str, tuple[str, ...], str]:
    """Pick the wording and the allowed answers for one issue code."""
    code = issue.code
    if code is IssueCode.CROSS_SOURCE_CONFLICT:
        values = list(issue.evidence.get("values") or ())
        units = list(issue.evidence.get("units") or ())
        candidates = _conflict_candidates(issue, by_observable.get(observable, ()))
        options = (*(f"keep:{uid}" for uid in candidates), _ACCEPT, _REJECT)
        shown = ", ".join(
            f"{value} {unit}".strip()
            for value, unit in zip(values, units, strict=False)
        )
        question = (
            f"Sources disagree on {observable or 'this field'} "
            f"({shown or 'see evidence'}). Which value should be authoritative?"
        )
        recommended = f"keep:{candidates[0]}" if candidates else _REJECT
        return question, options, recommended

    if code in {
        IssueCode.UNIT_UNKNOWN,
        IssueCode.UNIT_INCONSISTENCY,
    }:
        observed = sorted(
            {record.unit for record in by_observable.get(observable, ()) if record.unit}
        )
        target = str(issue.evidence.get("target_unit") or "")
        candidates = (
            observed if target in observed or not target else [*observed, target]
        )
        options = (*(f"confirm:{unit}" for unit in candidates), _ACCEPT, _REJECT)
        question = (
            f"What is the unit of {observable or 'this column'}? "
            f"Values were reported as {', '.join(observed) or 'an unknown unit'}"
            + (f"; the expected unit is {target}." if target else ".")
        )
        recommended = f"confirm:{target}" if target else _ACCEPT
        return question, options, recommended

    if code in {
        IssueCode.SIGN_OR_PHASE_SUSPECT,
        IssueCode.AXIS_SCALE_SUSPECT,
        IssueCode.AXIS_TICK_UNREADABLE,
        IssueCode.LEGEND_MAPPING_AMBIGUOUS,
        IssueCode.FIGURE_OVERLAY_MISMATCH,
        IssueCode.ERROR_BARS_UNVERIFIED,
    }:
        question = f"{issue.detail} Confirm whether the reading is correct."
        return question, (_ACCEPT, _REJECT), _ACCEPT

    if code in {
        IssueCode.MISSING_COVERAGE,
        IssueCode.COVERAGE_GAP,
        IssueCode.MISSING_VALUE,
    }:
        question = f"{issue.detail} Should more data be searched for?"
        return question, (_SEARCH, _ACCEPT), _ACCEPT

    if code in {
        IssueCode.ENTITY_IDENTITY_AMBIGUOUS,
        IssueCode.ENTITY_IDENTITY_CONFLICT,
    }:
        question = f"{issue.detail} Are these designations the same object?"
        return question, ("merge", "separate", _ACCEPT), "separate"

    question = f"{issue.detail} How should this be resolved?"
    return question, (_ACCEPT, _REJECT), _ACCEPT


def _conflict_candidates(
    issue: QualityIssue, records: Sequence[ObservationRecord]
) -> list[str]:
    """Record uids that could be the authoritative reading."""
    explicit = issue.evidence.get("record_uids") or issue.evidence.get("records")
    if isinstance(explicit, Sequence) and not isinstance(explicit, str):
        candidates = [str(item) for item in explicit]
        if candidates:
            return candidates
    observable = str(issue.subject.get("observable") or "")
    return [
        record.record_uid for record in records if record.conflict or not observable
    ]


@dataclass(frozen=True, slots=True)
class HumanResolution:
    """What changed after a reviewer answered the queue."""

    records: tuple[ObservationRecord, ...] = ()
    issues: tuple[QualityIssue, ...] = ()
    repairs: tuple[RepairLedgerEntry, ...] = ()
    queue: HumanQueue | None = None
    ignored: tuple[str, ...] = ()

    @property
    def applied(self) -> tuple[RepairLedgerEntry, ...]:
        return self.repairs

    def to_dict(self) -> dict[str, Any]:
        return {
            "answered": len(self.repairs),
            "ignored": list(self.ignored),
            "issues": [str(issue.state) for issue in self.issues],
        }


def apply_answers(
    *,
    records: Sequence[ObservationRecord],
    issues: Sequence[QualityIssue],
    queue: HumanQueue,
    actor: str = "user:reviewer",
) -> HumanResolution:
    """Apply reviewer answers to records and issue states, with a ledger trail."""
    dropped: set[str] = set()
    kept: set[str] = set()
    unit_overrides: dict[str, str] = {}
    approved: set[str] = set()
    rejected: set[str] = set()
    repairs: list[RepairLedgerEntry] = []
    ignored: list[str] = []

    for request in queue.requests:
        answer = queue.answer_for(request.request_id)
        if answer is None:
            continue
        choice = answer.choice
        observable = str(request.evidence.get("subject", {}).get("observable") or "")
        if choice.startswith("keep:"):
            keep = choice.split(":", 1)[1]
            kept.add(keep)
            for record in records:
                if record.record_uid == keep and record.conflict:
                    repairs.append(
                        _answer_entry(
                            request,
                            answer,
                            actor,
                            target={"record_uid": keep},
                            before={"conflict": True},
                            after={"conflict": False, "authority": keep},
                            rationale=(
                                f"Reviewer kept {keep} as the authoritative reading "
                                f"for {observable or 'this field'}."
                            ),
                        )
                    )
            approved.update(request.issue_ids)
        elif choice.startswith("drop:") or choice.startswith("reject"):
            if choice.startswith("drop:"):
                target_uid = choice.split(":", 1)[1]
                dropped.add(target_uid)
                repairs.append(
                    _answer_entry(
                        request,
                        answer,
                        actor,
                        target={"record_uid": target_uid},
                        before={"present": True},
                        after={"present": False},
                        rationale=(
                            f"Reviewer removed {target_uid} after reviewing the "
                            "conflicting reading."
                        ),
                    )
                )
            rejected.update(request.issue_ids)
        elif choice.startswith("confirm:"):
            unit = choice.split(":", 1)[1]
            if observable:
                unit_overrides[observable] = unit
            repairs.append(
                _answer_entry(
                    request,
                    answer,
                    actor,
                    target={"observable": observable},
                    before={"unit": "as_reported"},
                    after={"unit": unit},
                    rationale=(
                        f"Reviewer confirmed that {observable} is reported in {unit}."
                    ),
                )
            )
            approved.update(request.issue_ids)
        elif choice in {_ACCEPT, "merge", "separate"}:
            approved.update(request.issue_ids)
        elif choice == _SEARCH:
            ignored.append(request.request_id)
        else:  # pragma: no cover - guarded by HumanQueue.answer
            ignored.append(request.request_id)

    repaired_records: list[ObservationRecord] = []
    for record in records:
        if record.record_uid in dropped:
            continue
        current = record
        if record.record_uid in kept:
            current = replace(current, conflict=False)
        if current.observable in unit_overrides and current.unit:
            current = replace(
                current,
                unit=unit_overrides[current.observable],
                flags=(
                    *current.flags,
                    f"user_confirmed_unit:{unit_overrides[current.observable]}",
                ),
            )
        repaired_records.append(current)

    updated_issues: list[QualityIssue] = []
    for issue in issues:
        if issue.issue_id in approved:
            updated_issues.append(replace(issue, state=IssueState.HUMAN_APPROVED))
        elif issue.issue_id in rejected:
            updated_issues.append(replace(issue, state=IssueState.HUMAN_REJECTED))
        else:
            updated_issues.append(issue)

    # Answers must also leave the open queue, so the run can be finalised.
    settled = queue
    for answer in queue.answers:
        if settled.for_request(answer.request_id) is None:
            continue
        settled = settled.answer(
            answer.request_id,
            answer.choice,
            note=answer.note,
            actor=answer.actor,
        )

    return HumanResolution(
        records=tuple(repaired_records),
        issues=tuple(updated_issues),
        repairs=tuple(repairs),
        queue=settled,
        ignored=tuple(ignored),
    )


def _answer_entry(
    request: HumanRequest,
    answer: HumanAnswer,
    actor: str,
    *,
    target: Mapping[str, Any],
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    rationale: str,
) -> RepairLedgerEntry:
    """Ledger entry for a decision taken by a person, not by the kernel."""
    return RepairLedgerEntry(
        repair_id=f"repair:human:{slug(request.request_id)[:80]}",
        issue_id=request.issue_ids[0] if request.issue_ids else "",
        code=request.code,
        mode=RepairMode.HUMAN_REQUIRED,
        actor=actor,
        target=dict(target),
        before=dict(before),
        after=dict(after),
        rationale=f"{rationale} ({answer.note or 'no note'})",
        reversible=True,
        applied=True,
        approved_at=answer.decided_at or utc_now(),
        revalidation="passed",
        revalidated_at=utc_now(),
        before_hash=content_hash(before),
        after_hash=content_hash(after),
    )


def unit_confirmation_issue(unit: str, *, observable: str) -> QualityIssue:
    """Helper used by the reviewer flow when a unit is confirmed by hand."""
    resolution = resolve_unit(unit, quantity=observable)
    return QualityIssue(
        issue_id=f"issue:human:unit_confirm:{slug(observable)}",
        code=IssueCode.UNIT_UNKNOWN,
        severity=IssueSeverity.WARNING,
        stage="human_loop",
        subject={"observable": observable},
        detail=f"Reviewer confirmed unit {unit} for {observable}.",
        evidence={"note": resolution.note, "canonical": resolution.canonical},
        repair_mode=RepairMode.HUMAN_REQUIRED,
    )


def _jsonable(value: Any) -> Any:
    """Best-effort conversion so evidence survives a JSON round trip."""
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
