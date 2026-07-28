from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jw import paths
from jw.tools import knowledge_base as knowledge_tools
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


def _wiki_store_with_read_receipt(thread_id: str, entry_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        provenance_for_run=lambda run_id: (
            [
                {
                    "id": 17,
                    "run_id": thread_id,
                    "agent": "solar-hypothesis",
                    "entry_id": entry_id,
                    "purpose": "candidate grounding",
                    "ts": "2026-07-27T00:00:00+00:00",
                }
            ]
            if run_id == thread_id
            else []
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


def test_undeclared_verified_evidence_is_rejected_without_mutating_state() -> None:
    config = _config("hypothesis-ghost-evidence")
    hypothesis_tools._STATES.pop("hypothesis-ghost-evidence", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Explain this solar-cycle observation."},
        config=config,
    )

    outcome = json.loads(
        hypothesis_tools.scientific_hypothesis_bind_evidence.invoke(
            {
                "evidence_id": "ev_ghost",
                "evidence_kind": "upstream",
                "material_id": "project-constraints",
                "excerpt": "remembered from an earlier run",
                "verified_support": True,
                "role": "supports",
            },
            config=config,
        )
    )
    state = hypothesis_tools._STATES["hypothesis-ghost-evidence"]

    assert outcome["status"] == "needs_revision"
    assert "未在本轮 upstream_materials 中声明" in outcome["validation_error"]
    assert len(state.evidence_register) == 0


def test_canonical_wiki_binding_persists_mechanism_receipt(monkeypatch) -> None:
    config = _config("hypothesis-wiki-binding")
    hypothesis_tools._STATES.pop("hypothesis-wiki-binding", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "How could polar-field memory affect the next cycle?"},
        config=config,
    )
    entry = {
        "id": "kb_mechanism_polar-memory_001",
        "type": "mechanism",
        "title": "Polar-field memory",
        "content": {
            "mechanism": "Poloidal flux seeds the following toroidal-field cycle."
        },
        "source_type": "literature",
        "source_ref": "doi:10.0000/example",
        "confidence": "medium",
        "status": "canonical",
        "valid_range": "solar cycles with comparable polar-field measurements",
        "related_ids": [],
        "provenance": {"human_reviewed": True},
        "version": 3,
    }
    monkeypatch.setattr(
        knowledge_tools,
        "_get_store",
        lambda: _wiki_store_with_read_receipt(
            "hypothesis-wiki-binding",
            entry["id"],
        ),
    )
    monkeypatch.setattr(
        knowledge_tools.service,
        "read",
        lambda *_args, **_kwargs: {"status": "ok", "entry": entry},
    )

    outcome = json.loads(
        hypothesis_tools.scientific_hypothesis_bind_wiki_evidence.invoke(
            {"entry_id": entry["id"]},
            config=config,
        )
    )
    bound = hypothesis_tools._STATES["hypothesis-wiki-binding"].evidence_register.get(
        entry["id"]
    )

    assert outcome["status"] == "bound"
    assert outcome["wiki_grounding"]["version"] == 3
    assert bound is not None
    assert bound["role"] == "limits"
    assert bound["material_id"] == entry["id"]
    assert '"status":"canonical"' in bound["excerpt"]
    assert "provenance_sha256" in bound["excerpt"]
    assert "human_reviewed" in bound["excerpt"]
    assert '"kb_read_receipt"' in bound["excerpt"]
    assert outcome["kb_read_receipt"]["log_id"] == 17


def test_task_literature_binding_checks_frozen_quote(monkeypatch) -> None:
    thread_id = "hypothesis-literature-bundle"
    question = "Does the polar field precursor predict the next cycle amplitude?"
    config = _config(thread_id)
    hypothesis_tools._STATES.pop(thread_id, None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": question},
        config=config,
    )
    bundle_id = "litbundle_test_001"
    snapshot = {
        "source_id": "openalex:Wbundle",
        "family_id": "litfam_bundle",
        "title": "Polar field precursor test",
        "doi": "10.1000/bundle",
        "source_version": "2",
        "content_fingerprint": "a" * 64,
        "is_retracted": False,
        "abstract": (
            "Polar field strength near minimum predicts the next cycle amplitude."
        ),
    }
    store = SimpleNamespace(
        get_lit_task_bundle=lambda requested: (
            {
                "bundle_id": bundle_id,
                "binding_id": "binding",
                "run_id": thread_id,
                "research_question": question,
                "focus": "polar field precursor",
                "source_snapshots": [snapshot],
            }
            if requested == bundle_id
            else None
        )
    )
    monkeypatch.setattr(knowledge_tools, "_get_store", lambda: store)

    outcome = json.loads(
        hypothesis_tools.scientific_hypothesis_bind_literature_evidence.invoke(
            {
                "bundle_id": bundle_id,
                "source_id": snapshot["source_id"],
                "role": "supports",
                "quote": "near minimum predicts the next cycle amplitude",
                "claim": "Minimum polar field predicts the next cycle amplitude.",
            },
            config=config,
        )
    )
    evidence_id = outcome["literature_evidence"]
    bound = hypothesis_tools._STATES[thread_id].evidence_register.get(
        "litevidence_" + canonical_json_sha256(evidence_id)[:32]
    )

    assert outcome["status"] == "bound"
    assert bound is not None
    assert bound["material_id"] == bundle_id
    assert bound["role"] == "supports"
    assert '"status":"verified"' in bound["excerpt"]

    rejected = json.loads(
        hypothesis_tools.scientific_hypothesis_bind_literature_evidence.invoke(
            {
                "bundle_id": bundle_id,
                "source_id": snapshot["source_id"],
                "role": "supports",
                "quote": "a quote that does not exist",
                "claim": "Minimum polar field predicts amplitude.",
            },
            config=config,
        )
    )
    assert rejected["status"] == "needs_revision"
    assert "逐字定位" in rejected["validation_error"]


@pytest.mark.parametrize(
    ("entry_type", "entry_id", "source_type"),
    [
        ("data_source", "kb_data_source_polar-field_001", "dataset_doc"),
        (
            "experiment_paradigm",
            "kb_experiment_paradigm_leave-one-cycle-out_001",
            "textbook",
        ),
    ],
)
def test_stable_wiki_data_and_method_entries_can_be_bound_as_limits(
    monkeypatch,
    entry_type: str,
    entry_id: str,
    source_type: str,
) -> None:
    thread_id = f"hypothesis-wiki-{entry_type}"
    config = _config(thread_id)
    hypothesis_tools._STATES.pop(thread_id, None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Which data and backtest limits constrain this hypothesis?"},
        config=config,
    )
    entry = {
        "id": entry_id,
        "type": entry_type,
        "title": "Bounded Wiki entry",
        "content": {"summary": "A reviewed scope or method boundary."},
        "source_type": source_type,
        "source_ref": "reviewed-source",
        "confidence": "medium",
        "status": "canonical",
        "valid_range": "the documented data product and evaluation design",
        "related_ids": [],
        "provenance": {"human_reviewed": True},
        "version": 2,
    }
    monkeypatch.setattr(
        knowledge_tools,
        "_get_store",
        lambda: _wiki_store_with_read_receipt(thread_id, entry_id),
    )
    monkeypatch.setattr(
        knowledge_tools.service,
        "read",
        lambda *_args, **_kwargs: {"status": "ok", "entry": entry},
    )

    outcome = json.loads(
        hypothesis_tools.scientific_hypothesis_bind_wiki_evidence.invoke(
            {"entry_id": entry_id},
            config=config,
        )
    )

    assert outcome["status"] == "bound"
    assert outcome["wiki_grounding"]["type"] == entry_type
    bound = hypothesis_tools._STATES[thread_id].evidence_register.get(entry_id)
    assert bound is not None
    assert bound["role"] == "limits"


def test_wiki_binding_requires_prior_read_receipt(monkeypatch) -> None:
    thread_id = "hypothesis-wiki-missing-read"
    config = _config(thread_id)
    hypothesis_tools._STATES.pop(thread_id, None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Could this Wiki mechanism constrain the hypothesis?"},
        config=config,
    )
    entry_id = "kb_mechanism_unread_001"
    store = SimpleNamespace(provenance_for_run=lambda _run_id: [])
    monkeypatch.setattr(knowledge_tools, "_get_store", lambda: store)
    service_called = False

    def should_not_read(*_args, **_kwargs):
        nonlocal service_called
        service_called = True
        raise AssertionError("binding must fail before its server-side re-read")

    monkeypatch.setattr(knowledge_tools.service, "read", should_not_read)

    outcome = json.loads(
        hypothesis_tools.scientific_hypothesis_bind_wiki_evidence.invoke(
            {"entry_id": entry_id},
            config=config,
        )
    )

    assert service_called is False
    assert outcome["status"] == "needs_revision"
    assert "No prior kb_read receipt exists" in outcome["validation_error"]
    assert len(hypothesis_tools._STATES[thread_id].evidence_register) == 0


def test_noncanonical_wiki_binding_is_rejected(monkeypatch) -> None:
    config = _config("hypothesis-wiki-candidate")
    hypothesis_tools._STATES.pop("hypothesis-wiki-candidate", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Could this candidate Wiki mechanism explain the cycle?"},
        config=config,
    )
    entry = {
        "id": "kb_mechanism_unreviewed_001",
        "type": "mechanism",
        "title": "Unreviewed mechanism",
        "content": {"mechanism": "An unreviewed mechanism."},
        "source_type": "derived",
        "source_ref": "draft",
        "confidence": "low",
        "status": "candidate",
        "valid_range": "",
        "related_ids": [],
        "provenance": {},
        "version": 1,
    }
    monkeypatch.setattr(
        knowledge_tools,
        "_get_store",
        lambda: _wiki_store_with_read_receipt(
            "hypothesis-wiki-candidate",
            entry["id"],
        ),
    )
    monkeypatch.setattr(
        knowledge_tools.service,
        "read",
        lambda *_args, **_kwargs: {"status": "ok", "entry": entry},
    )

    outcome = json.loads(
        hypothesis_tools.scientific_hypothesis_bind_wiki_evidence.invoke(
            {"entry_id": entry["id"]},
            config=config,
        )
    )
    state = hypothesis_tools._STATES["hypothesis-wiki-candidate"]

    assert outcome["status"] == "needs_revision"
    assert "只有 canonical 条目" in outcome["validation_error"]
    assert len(state.evidence_register) == 0


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
            "material_id": "user_request",
            "excerpt": "Explain this observation.",
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


def test_draft_update_warns_about_unbound_numeric_thresholds() -> None:
    config = _config("hypothesis-draft-numeric-warning")
    hypothesis_tools._STATES.pop("hypothesis-draft-numeric-warning", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Explain this observation without invented cutoffs."},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-draft-numeric-warning"]
    response = make_response(state.request)
    response["candidates"][0]["falsification_conditions"] = [
        "当相关系数 r > 0.85 时放弃该候选"
    ]

    outcome = _update(config, "replace", response)
    threshold_warnings = [
        warning
        for warning in outcome["soft_warnings"]
        if warning["code"] == "ungrounded_numeric_threshold"
    ]

    assert threshold_warnings
    assert threshold_warnings[0]["candidate_id"] == "cand_dynamo"
    assert "0.85" in threshold_warnings[0]["message"]


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
