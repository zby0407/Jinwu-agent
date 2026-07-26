from __future__ import annotations

import json
from pathlib import Path

from jw import paths
from jw.tools import scientific_hypothesis as hypothesis_tools
from jw.workspaces import ensure_thread_workspace
from scientific_hypothesis.contracts import canonical_json_sha256
from tests.test_hypothesis import make_response


def _config(thread_id: str) -> dict[str, dict[str, str]]:
    return {
        "configurable": {
            "thread_id": thread_id,
            "workspace_thread_id": thread_id,
        }
    }


def _bound_config(
    tmp_path: Path, monkeypatch, thread_id: str
) -> tuple[Path, dict[str, dict[str, str]]]:
    base = tmp_path / "workspace"
    base.mkdir(exist_ok=True)
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(tmp_path / "registry"))
    monkeypatch.setattr(paths, "WORKSPACE_ROOT", base)
    binding = ensure_thread_workspace(thread_id, base)
    return Path(binding.workspace), _config(thread_id)


def _update(
    config: dict[str, dict[str, str]], operation: str, payload: object
) -> dict[str, object]:
    return json.loads(
        hypothesis_tools.scientific_hypothesis_update_draft.invoke(
            {
                "operation": operation,
                "payload_json": json.dumps(payload, ensure_ascii=False),
            },
            config=config,
        )
    )


def test_rebinding_same_question_preserves_working_state() -> None:
    config = _config("hypothesis-idempotent-bind")
    hypothesis_tools._STATES.pop("hypothesis-idempotent-bind", None)

    first = json.loads(
        hypothesis_tools.scientific_hypothesis_bind_request.invoke(
            {"request_input": "Why did the observed cycle minimum change?"},
            config=config,
        )
    )
    state = hypothesis_tools._STATES["hypothesis-idempotent-bind"]
    state.preflight_attempts = 3
    state.validated_response = {"checkpoint": True}
    state.preflight_response_sha256 = canonical_json_sha256(state.validated_response)

    second = json.loads(
        hypothesis_tools.scientific_hypothesis_bind_request.invoke(
            {"request_input": "Why did the observed cycle minimum change?"},
            config=config,
        )
    )

    assert first["binding_status"] == "bound"
    assert second["binding_status"] == "already_bound"
    assert second["working_state_preserved"] is True
    assert second["checkpoint_available"] is True
    assert hypothesis_tools._STATES["hypothesis-idempotent-bind"] is state
    assert state.preflight_attempts == 3


def test_rebinding_different_question_starts_new_working_state() -> None:
    config = _config("hypothesis-new-bind")
    hypothesis_tools._STATES.pop("hypothesis-new-bind", None)

    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Question A"},
        config=config,
    )
    old_state = hypothesis_tools._STATES["hypothesis-new-bind"]
    old_state.preflight_attempts = 2

    outcome = json.loads(
        hypothesis_tools.scientific_hypothesis_bind_request.invoke(
            {"request_input": "Question B"},
            config=config,
        )
    )
    new_state = hypothesis_tools._STATES["hypothesis-new-bind"]

    assert outcome["binding_status"] == "bound"
    assert outcome["working_state_preserved"] is False
    assert new_state is not old_state
    assert new_state.preflight_attempts == 0


def test_repeated_checkpoint_failure_preserves_checkpoint_and_stops_retry() -> None:
    config = _config("hypothesis-bounded-review")
    hypothesis_tools._STATES.pop("hypothesis-bounded-review", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Explain this observation."},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-bounded-review"]
    state.validated_response = {"checkpoint": True}
    state.preflight_response_sha256 = canonical_json_sha256(state.validated_response)

    first = json.loads(
        hypothesis_tools.scientific_hypothesis_validate_response.invoke(
            {"response_json": "not-json"},
            config=config,
        )
    )
    second = json.loads(
        hypothesis_tools.scientific_hypothesis_validate_response.invoke(
            {"response_json": "not-json"},
            config=config,
        )
    )

    assert first["status"] == "needs_revision"
    assert first["checkpoint_preserved"] is True
    assert first["retry_recommended"] is True
    assert second["status"] == "review_limit_reached"
    assert second["checkpoint_preserved"] is True
    assert second["retry_recommended"] is False
    assert state.validated_response == {"checkpoint": True}


def test_status_reports_draft_that_differs_from_checkpoint() -> None:
    config = _config("hypothesis-status")
    hypothesis_tools._STATES.pop("hypothesis-status", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Explain this observation."},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-status"]
    state.validated_response = {"version": "checkpoint"}
    state.preflight_response_sha256 = canonical_json_sha256(state.validated_response)
    state.latest_draft = {"version": "draft"}
    state.latest_draft_sha256 = canonical_json_sha256(state.latest_draft)

    outcome = json.loads(
        hypothesis_tools.scientific_hypothesis_get_status.invoke({}, config=config)
    )

    assert outcome["status"] == "working"
    assert outcome["draft_available"] is True
    assert outcome["checkpoint_available"] is True
    assert outcome["draft_differs_from_checkpoint"] is True


def test_publish_refuses_to_use_stale_checkpoint() -> None:
    config = _config("hypothesis-stale-publish")
    hypothesis_tools._STATES.pop("hypothesis-stale-publish", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Explain this observation."},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-stale-publish"]
    state.validated_response = {"version": "checkpoint"}
    state.preflight_response_sha256 = canonical_json_sha256(state.validated_response)
    state.latest_draft = {"version": "new-draft"}
    state.latest_draft_sha256 = canonical_json_sha256(state.latest_draft)

    outcome = json.loads(
        hypothesis_tools.scientific_hypothesis_freeze.invoke({}, config=config)
    )

    assert outcome["status"] == "needs_revision"
    assert outcome["checkpoint_preserved"] is True
    assert "differs from the last valid checkpoint" in outcome["validation_error"]


def test_publish_refuses_evidence_added_after_checkpoint() -> None:
    config = _config("hypothesis-stale-evidence")
    hypothesis_tools._STATES.pop("hypothesis-stale-evidence", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Explain this observation."},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-stale-evidence"]
    state.validated_response = {"version": "checkpoint"}
    state.preflight_response_sha256 = canonical_json_sha256(state.validated_response)
    state.latest_draft = state.validated_response
    state.latest_draft_sha256 = state.preflight_response_sha256
    state.checkpoint_evidence_sha256 = hypothesis_tools._evidence_sha256(
        state.evidence_register
    )

    hypothesis_tools.scientific_hypothesis_bind_evidence.invoke(
        {
            "evidence_id": "ev_new",
            "evidence_kind": "user",
            "material_id": "user_note",
            "excerpt": "The user supplied a new observation after checkpointing.",
            "verified_support": True,
            "role": "supports",
        },
        config=config,
    )
    status = json.loads(
        hypothesis_tools.scientific_hypothesis_get_status.invoke({}, config=config)
    )
    outcome = json.loads(
        hypothesis_tools.scientific_hypothesis_freeze.invoke({}, config=config)
    )

    assert status["evidence_differs_from_checkpoint"] is True
    assert outcome["status"] == "needs_revision"
    assert "evidence register changed" in outcome["validation_error"]


def test_checkpoint_recovers_from_task_workspace_after_process_restart(
    tmp_path: Path, monkeypatch
) -> None:
    workspace, config = _bound_config(
        tmp_path, monkeypatch, "hypothesis-durable-checkpoint"
    )
    hypothesis_tools._STATES.pop("hypothesis-durable-checkpoint", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Explain the difference between two observed minima."},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-durable-checkpoint"]
    response = make_response(state.request)

    checked = json.loads(
        hypothesis_tools.scientific_hypothesis_validate_response.invoke(
            {"response_json": json.dumps(response, ensure_ascii=False)},
            config=config,
        )
    )
    state_path = workspace / hypothesis_tools.WORKING_STATE_RELATIVE_PATH

    assert checked["working_status"] == "checkpointed"
    assert checked["state_persistence"] == "workspace"
    assert state_path.is_file()

    # Simulate a new LangGraph worker process: only the task workspace remains.
    hypothesis_tools._STATES.pop("hypothesis-durable-checkpoint", None)
    recovered = json.loads(
        hypothesis_tools.scientific_hypothesis_get_status.invoke({}, config=config)
    )

    assert recovered["status"] == "working"
    assert recovered["checkpoint_available"] is True
    assert recovered["draft_differs_from_checkpoint"] is False
    assert recovered["state_persistence"] == "workspace"


def test_retry_limit_recovers_after_process_restart(
    tmp_path: Path, monkeypatch
) -> None:
    _workspace, config = _bound_config(
        tmp_path, monkeypatch, "hypothesis-durable-retry"
    )
    hypothesis_tools._STATES.pop("hypothesis-durable-retry", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Explain this observation."},
        config=config,
    )
    for _ in range(2):
        hypothesis_tools.scientific_hypothesis_validate_response.invoke(
            {"response_json": "{}"},
            config=config,
        )

    hypothesis_tools._STATES.pop("hypothesis-durable-retry", None)
    recovered = json.loads(
        hypothesis_tools.scientific_hypothesis_get_status.invoke({}, config=config)
    )

    assert recovered["draft_available"] is True
    assert recovered["same_validation_error_count"] == 2
    assert recovered["retry_recommended"] is False


def test_corrupt_persisted_state_degrades_to_recoverable_warning(
    tmp_path: Path, monkeypatch
) -> None:
    workspace, config = _bound_config(tmp_path, monkeypatch, "hypothesis-corrupt-state")
    hypothesis_tools._STATES.pop("hypothesis-corrupt-state", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Explain this observation."},
        config=config,
    )
    state_path = workspace / hypothesis_tools.WORKING_STATE_RELATIVE_PATH
    state_path.write_text("not-json", encoding="utf-8")

    hypothesis_tools._STATES.pop("hypothesis-corrupt-state", None)
    outcome = json.loads(
        hypothesis_tools.scientific_hypothesis_get_status.invoke({}, config=config)
    )

    assert outcome["status"] == "needs_revision"
    assert "persisted working state was ignored" in outcome["persistence_warning"]


def test_incremental_candidate_patch_preserves_other_fields_and_resets_retry() -> None:
    config = _config("hypothesis-incremental-patch")
    hypothesis_tools._STATES.pop("hypothesis-incremental-patch", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Explain this observation."},
        config=config,
    )

    created = _update(
        config,
        "upsert_candidate",
        {
            "id": "H1",
            "statement": "A measurement change produced the apparent difference.",
            "confidence": {"level": "high", "basis": "Initial guess"},
        },
    )
    state = hypothesis_tools._STATES["hypothesis-incremental-patch"]
    state.last_validation_error = "old failure"
    state.same_validation_error_count = 2
    patched = _update(
        config,
        "patch_candidate",
        {
            "candidate_id": "H1",
            "changes": {
                "confidence": {"level": "low", "basis": "Evidence is incomplete"},
                "evidence_gaps": ["No same-instrument comparison is available."],
            },
        },
    )
    draft = json.loads(
        hypothesis_tools.scientific_hypothesis_get_draft.invoke({}, config=config)
    )["draft"]

    assert created["status"] == "draft"
    assert created["hard_validation_run"] is False
    assert created["soft_warning_count"] > 0
    assert patched["retry_budget_reset"] is True
    assert state.same_validation_error_count == 0
    assert draft["candidates"][0]["statement"].startswith("A measurement change")
    assert draft["candidates"][0]["confidence"]["level"] == "low"
    assert draft["candidates"][0]["evidence_gaps"]


def test_remove_candidate_cleans_pairwise_distinctions() -> None:
    config = _config("hypothesis-incremental-remove")
    hypothesis_tools._STATES.pop("hypothesis-incremental-remove", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Compare two possible mechanisms."},
        config=config,
    )
    _update(config, "upsert_candidate", {"id": "H1", "statement": "Mechanism one"})
    _update(config, "upsert_candidate", {"id": "H2", "statement": "Mechanism two"})
    _update(
        config,
        "set_distinctions",
        [{"left_id": "H1", "right_id": "H2", "distinction": "Different cause"}],
    )

    _update(config, "remove_candidate", {"candidate_id": "H2"})
    draft = json.loads(
        hypothesis_tools.scientific_hypothesis_get_draft.invoke({}, config=config)
    )["draft"]

    assert [candidate["id"] for candidate in draft["candidates"]] == ["H1"]
    assert draft["pairwise_distinctions"] == []


def test_checkpoint_current_draft_without_resending_full_response() -> None:
    config = _config("hypothesis-incremental-checkpoint")
    hypothesis_tools._STATES.pop("hypothesis-incremental-checkpoint", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Explain the difference between two observed minima."},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-incremental-checkpoint"]
    _update(config, "replace", make_response(state.request))

    checked = json.loads(
        hypothesis_tools.scientific_hypothesis_checkpoint_draft.invoke(
            {}, config=config
        )
    )
    _update(
        config,
        "patch_candidate",
        {
            "candidate_id": "cand_dynamo",
            "changes": {"statement": "A revised exploratory mechanism statement"},
        },
    )
    publish = json.loads(
        hypothesis_tools.scientific_hypothesis_freeze.invoke({}, config=config)
    )

    assert checked["working_status"] == "checkpointed"
    assert checked["checkpoint_available"] is True
    assert publish["status"] == "needs_revision"
    assert "current draft differs" in publish["validation_error"]


def test_incremental_draft_recovers_after_worker_restart(
    tmp_path: Path, monkeypatch
) -> None:
    _workspace, config = _bound_config(
        tmp_path, monkeypatch, "hypothesis-incremental-restart"
    )
    hypothesis_tools._STATES.pop("hypothesis-incremental-restart", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Explain this observation."},
        config=config,
    )
    _update(
        config,
        "upsert_candidate",
        {"id": "H1", "statement": "A provisional mechanism."},
    )

    hypothesis_tools._STATES.pop("hypothesis-incremental-restart", None)
    recovered = json.loads(
        hypothesis_tools.scientific_hypothesis_get_draft.invoke({}, config=config)
    )

    assert recovered["status"] == "draft"
    assert recovered["candidate_count"] == 1
    assert recovered["draft"]["candidates"][0]["id"] == "H1"
    assert recovered["state_persistence"] == "workspace"
