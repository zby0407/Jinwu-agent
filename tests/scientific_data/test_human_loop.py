"""Human-loop tests: questions are answerable, blocking, and auditable."""

from __future__ import annotations

import json

import pytest

from jw.scientific_data.contracts import (
    IssueCode,
    IssueSeverity,
    IssueState,
    ObservationRecord,
    QualityIssue,
    RepairMode,
)
from jw.scientific_data.human_loop import (
    HumanAnswer,
    HumanQueue,
    RequestState,
    apply_answers,
    build_queue,
)


def record(
    *,
    uid="rec:a",
    observable="apparent_magnitude",
    value=16.5,
    unit="mag",
    conflict=False,
) -> ObservationRecord:
    return ObservationRecord(
        record_uid=uid,
        entity_id="obj:sn2005a",
        observable=observable,
        unit=unit,
        value=value,
        value_raw=str(value),
        time="2000-01-02",
        time_system="iso",
        conflict=conflict,
        source_ids=("src-a",),
    )


def conflict_issue(**overrides) -> QualityIssue:
    fields: dict = {
        "issue_id": "issue:quality:cross_source_conflict:obj:sn2005a",
        "code": IssueCode.CROSS_SOURCE_CONFLICT,
        "severity": IssueSeverity.ERROR,
        "stage": "quality",
        "subject": {"observable": "apparent_magnitude", "entity_id": "obj:sn2005a"},
        "detail": "two sources disagree on this point",
        "evidence": {
            "values": [16.5, 16.9],
            "units": ["mag", "mag"],
            "record_uids": ["rec:a", "rec:b"],
        },
        "repair_mode": RepairMode.HUMAN_REQUIRED,
    }
    fields.update(overrides)
    return QualityIssue(**fields)


def test_every_human_bound_issue_gets_a_concrete_question():
    queue = build_queue(
        [conflict_issue()],
        records=[record(uid="rec:a", conflict=True), record(uid="rec:b", value=16.9)],
    )

    request = queue.requests[0]
    assert request.blocking is True
    assert "Sources disagree" in request.question
    assert request.options == ("keep:rec:a", "keep:rec:b", "accept", "reject")
    assert request.recommended == "keep:rec:a"
    assert request.state is RequestState.OPEN


def test_queue_is_not_ready_while_a_blocking_question_is_open():
    queue = build_queue([conflict_issue()], records=[record(conflict=True)])

    assert queue.ready is False
    assert len(queue.blocking_open) == 1

    answered = queue.answer(queue.requests[0].request_id, "keep:rec:a", note="trusted")
    assert answered.ready is True
    assert answered.requests[0].state is RequestState.ANSWERED


def test_unoffered_answers_are_refused_and_unknown_ids_raise():
    queue = build_queue([conflict_issue()], records=[record(conflict=True)])

    with pytest.raises(ValueError):
        queue.answer(queue.requests[0].request_id, "keep:rec:zzz")
    with pytest.raises(KeyError):
        queue.answer("ask:missing", "accept")


def test_answered_conflict_keeps_the_chosen_reading_and_ledgers_it():
    records = [
        record(uid="rec:a", conflict=True),
        record(uid="rec:b", value=16.9, conflict=True),
    ]
    queue = build_queue([conflict_issue()], records=records)
    answered = queue.answer(queue.requests[0].request_id, "keep:rec:b", note="referee")

    resolution = apply_answers(
        records=records, issues=[conflict_issue()], queue=answered
    )

    kept = {item.record_uid: item for item in resolution.records}
    assert kept["rec:b"].conflict is False
    assert kept["rec:b"].value == 16.9, "the reviewer's choice is preserved verbatim"
    assert resolution.issues[0].state is IssueState.HUMAN_APPROVED
    entry = resolution.applied[0]
    assert entry.actor == "user:reviewer"
    assert entry.mode is RepairMode.HUMAN_REQUIRED
    assert entry.before_hash.startswith("sha256:")
    assert "referee" in entry.rationale
    assert entry.applied is True


def test_confirmed_unit_is_applied_to_every_record_of_that_observable():
    records = [
        record(uid="rec:a", observable="flux_density", value=3.1, unit="unknown"),
        record(uid="rec:b", observable="flux_density", value=4.2, unit="unknown"),
    ]
    issue = QualityIssue(
        issue_id="issue:quality:unit_unknown:flux_density",
        code=IssueCode.UNIT_UNKNOWN,
        severity=IssueSeverity.WARNING,
        stage="quality",
        subject={"observable": "flux_density", "column": "flux"},
        detail="the unit of this column could not be resolved",
        evidence={"target_unit": "mJy"},
        repair_mode=RepairMode.SUGGEST_ONLY,
    )
    queue = build_queue([issue], records=records)
    assert "confirm:unknown" in queue.requests[0].options

    answered = queue.answer(queue.requests[0].request_id, "confirm:mJy")
    resolution = apply_answers(records=records, issues=[issue], queue=answered)

    assert [item.unit for item in resolution.records] == ["mJy", "mJy"]
    assert all("user_confirmed_unit:mJy" in item.flags for item in resolution.records)
    assert resolution.issues[0].state is IssueState.HUMAN_APPROVED


def test_rejection_marks_the_issue_rejected_without_touching_data():
    records = [record(conflict=True)]
    queue = build_queue([conflict_issue()], records=records)
    answered = queue.answer(queue.requests[0].request_id, "reject", note="not a defect")

    resolution = apply_answers(
        records=records, issues=[conflict_issue()], queue=answered
    )

    assert resolution.issues[0].state is IssueState.HUMAN_REJECTED
    assert resolution.records[0].value == 16.5
    assert resolution.records[0].conflict is True, "no silent fix on rejection"


def test_queue_round_trips_through_json(tmp_path):
    queue = build_queue(
        [conflict_issue()], records=[record(conflict=True), record(uid="rec:b")]
    )
    answered = queue.answer(queue.requests[0].request_id, "keep:rec:a")
    path = answered.write(tmp_path / "human" / "requests.json")

    loaded = HumanQueue.load(path)

    assert loaded.to_dict() == answered.to_dict()
    assert loaded.ready is True
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["blocking_open"] == 0
    assert raw["answers"][0]["choice"] == "keep:rec:a"


def test_answer_from_dict_keeps_actor_and_note():
    answer = HumanAnswer.from_dict(
        {"request_id": "ask:1", "choice": "accept", "note": "ok", "actor": "astro"}
    )

    assert answer == HumanAnswer(
        request_id="ask:1", choice="accept", note="ok", actor="astro", decided_at=""
    )


def test_search_more_keeps_the_question_open_for_a_later_pass():
    issue = QualityIssue(
        issue_id="issue:quality:coverage_gap:obj:sn2005a",
        code=IssueCode.COVERAGE_GAP,
        severity=IssueSeverity.WARNING,
        stage="quality",
        subject={"observable": "apparent_magnitude", "entity_id": "obj:sn2005a"},
        detail="a gap larger than the expected cadence was found",
        repair_mode=RepairMode.SUGGEST_ONLY,
    )
    queue = build_queue([issue], records=[record()])
    answered = queue.answer(queue.requests[0].request_id, "search_more")

    resolution = apply_answers(records=[record()], issues=[issue], queue=answered)

    assert answered.requests[0].state is RequestState.DECLINED
    assert resolution.ignored == (queue.requests[0].request_id,)
    assert resolution.applied == ()
    assert resolution.issues[0].state is IssueState.DETECTED
