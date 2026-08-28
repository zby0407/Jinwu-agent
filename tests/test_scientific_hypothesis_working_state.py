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
from scientific_hypothesis.ranking import PORTFOLIO_RANKING_VERSION
from scientific_hypothesis.tail_search import (
    BENEFIT_METRICS,
    RUBRIC_ITEMS,
    TAIL_REVIEW_VERSION,
    candidate_pool_sha256,
)
from tests.test_hypothesis import make_request, make_response


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


def _tail_review_payload(
    draft: dict[str, object],
    *,
    violation_candidate_id: str | None = None,
) -> dict[str, object]:
    candidates = draft["candidates"]
    assert isinstance(candidates, list)
    rows = []
    for index, candidate in enumerate(candidates):
        assert isinstance(candidate, dict)
        candidate_id = candidate["id"]
        assert isinstance(candidate_id, str)
        rubric = {
            key: {
                "status": (
                    "violation"
                    if candidate_id == violation_candidate_id
                    and key == "boundary_completeness"
                    else "pass"
                ),
                "violated_guidelines": (
                    ["handles_all_criteria"]
                    if candidate_id == violation_candidate_id
                    and key == "boundary_completeness"
                    else []
                ),
                "rationale": f"Independent reviewer checked {key} for {candidate_id}.",
            }
            for key in RUBRIC_ITEMS
        }
        metrics = dict.fromkeys(BENEFIT_METRICS, "medium" if index == 0 else "high")
        metrics.update(
            {
                "evidence_risk": "low" if index == 0 else "high",
                "test_cost": "low" if index == 0 else "high",
            }
        )
        rows.append(
            {
                "candidate_id": candidate_id,
                "generation_operator": (
                    "modal_baseline" if index == 0 else "premise_reversal"
                ),
                "search_region": ("modal_baseline" if index == 0 else "negative_tail"),
                "mechanism_signature": f"distinct mechanism signature {candidate_id}",
                "novelty_status": (
                    "known_baseline" if index == 0 else "tail_candidate_unverified"
                ),
                "rubric": rubric,
                "tail_metrics": metrics,
                "reviewer_summary": (
                    f"Independent violation-first review completed for {candidate_id}."
                ),
            }
        )
    return {
        "schema_version": TAIL_REVIEW_VERSION,
        "candidate_pool_sha256": candidate_pool_sha256(draft),
        "reviewer_mode": "independent_violation_first",
        "instance_rubrics": [
            {
                "id": f"ir_{row['candidate_id']}",
                "candidate_id": row["candidate_id"],
                "criterion": (
                    f"Candidate {row['candidate_id']} must resolve the "
                    "question-specific observable contrast."
                ),
                "basis": "Derived from the bound question and candidate contrast.",
                "status": "pass",
                "violated_guidelines": [],
                "rationale": (
                    "The candidate includes a directly discriminating prediction."
                ),
            }
            for row in rows
        ],
        "candidates": rows,
    }


def _review_tail(
    config: dict[str, dict[str, str]],
    draft: dict[str, object],
    *,
    violation_candidate_id: str | None = None,
) -> dict[str, object]:
    return json.loads(
        hypothesis_tools.scientific_hypothesis_review_tail.invoke(
            {
                "review_json": json.dumps(
                    _tail_review_payload(
                        draft,
                        violation_candidate_id=violation_candidate_id,
                    ),
                    ensure_ascii=False,
                )
            },
            config=config,
        )
    )


def _portfolio_ranking_payload(draft: dict[str, object]) -> dict[str, object]:
    candidates = draft["candidates"]
    assert isinstance(candidates, list)
    groups = []
    ranked = []
    for index, candidate in enumerate(candidates, start=1):
        assert isinstance(candidate, dict)
        candidate_id = candidate["id"]
        assert isinstance(candidate_id, str)
        groups.append(
            {
                "hypothesis_id": candidate_id,
                "normalized_statement": candidate["statement"],
                "member_candidates": [
                    {"run_id": "current-task", "candidate_id": candidate_id}
                ],
                "deduplication_rationale": "This current candidate is scientifically distinct.",
            }
        )
        ranked.append(
            {
                "hypothesis_id": candidate_id,
                "support_rank": index,
                "research_priority_rank": len(candidates) - index + 1,
                "claim_type": (
                    "measurement_explanation"
                    if "measure" in candidate_id
                    else "mechanism_candidate"
                ),
                "current_evidence_status": "insufficient",
                "scientific_support": {
                    "level": "low",
                    "rationale": "No direct verified support is attached.",
                },
                "research_priority": {
                    "level": "high" if index == len(candidates) else "medium",
                    "rationale": "The next test can discriminate competing explanations.",
                },
                "data_sources_verified": False,
                "support_evidence": [],
                "opposing_evidence": [],
                "out_of_sample_validation": {
                    "status": "not_applicable",
                    "baseline_comparison": "This is not a predictive claim.",
                },
                "effect_uncertainty": {
                    "effect_summary": "No effect estimate is currently accepted.",
                    "interval_summary": "No interval is currently accepted.",
                    "interval_crosses_null": None,
                },
                "sensitivity": {
                    "leave_one_out": "not_tested",
                    "temporal_split": "not_tested",
                    "measurement_regime": "not_tested",
                    "definition": "not_tested",
                },
                "falsifiability": {
                    "status": "clear",
                    "conditions": list(candidate["falsification_conditions"]),
                },
                "key_limitations": list(candidate["evidence_gaps"]),
                "strongest_null_hypothesis": "The observed difference is measurement variation.",
                "next_experiment": {
                    "objective": candidate["next_test"]["objective"],
                    "discriminating_power": candidate["next_test"]["discriminating_power"],
                    "feasibility": "executable_now",
                },
                "ranking_rationale": "Support and research priority use separate ranks.",
                "release_boundary": "Do not present this mechanism as established.",
            }
        )
    return {
        "schema_version": PORTFOLIO_RANKING_VERSION,
        "source_runs": ["current-task"],
        "hypothesis_groups": groups,
        "ranked_hypotheses": ranked,
        "selected_next_experiment": {
            "hypothesis_ids": [group["hypothesis_id"] for group in groups],
            "objective": "Run the shared discriminating test.",
            "discriminating_power": "Its result updates the competing explanations differently.",
            "feasibility": "executable_now",
            "rationale": "Information value is high despite low current support.",
        },
    }


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
    scoring_guide = first["tail_search_contract"]["scoring_guide"]
    ranking_contract = first["portfolio_ranking_contract"]
    assert ranking_contract["schema_version"] == PORTFOLIO_RANKING_VERSION
    assert ranking_contract["separate_orders"] == [
        "scientific_support",
        "research_priority",
    ]
    assert "shared data" in ranking_contract["evidence_dependency_rule"]
    assert "boundary_completeness" in scoring_guide["scientific_rubrics"]
    assert "handles_all_criteria" in scoring_guide["general_guidelines"]
    assert scoring_guide["tail_metric_anchors"]["evidence_risk"]["direction"] == "risk"
    draft_receipt = json.loads(
        hypothesis_tools.scientific_hypothesis_get_draft.invoke({}, config=config)
    )
    assert draft_receipt["tail_review_scoring_guide"] == scoring_guide
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


def test_read_persisted_draft_returns_exact_recovery_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    workspace, config = _bound_config(
        tmp_path, monkeypatch, "hypothesis-persisted-recovery"
    )
    hypothesis_tools._STATES.pop("hypothesis-persisted-recovery", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Explain the observation with competing hypotheses."},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-persisted-recovery"]
    response = make_response(state.request)
    update = _update(config, "replace", response)
    state_path = workspace / "work" / "scientific_hypothesis_state.json"

    receipt = hypothesis_tools.read_persisted_hypothesis_draft(state_path)

    assert receipt["status"] == "draft"
    assert receipt["state_persistence"] == "workspace"
    assert receipt["state_file"] == str(state_path)
    assert receipt["candidate_count"] == len(response["candidates"])
    assert receipt["draft_sha256"] == update["draft_sha256"]
    assert receipt["draft"] == response


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
        "provenance": {"supporting_run_ids": ["run-a", "run-b"]},
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
    assert "supporting_run_ids" in bound["excerpt"]
    assert '"kb_read_receipt"' in bound["excerpt"]
    assert outcome["kb_read_receipt"]["log_id"] == 17

    state = hypothesis_tools._STATES["hypothesis-wiki-binding"]
    state.literature_bundle_attempted = True
    draft_outcome = _update(config, "replace", make_response(state.request))
    warnings = [
        warning
        for warning in draft_outcome["soft_warnings"]
        if warning["code"] == "unattached_wiki_evidence"
    ]
    assert warnings
    assert entry["id"] in warnings[0]["message"]


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
    assert outcome["draft_attachment_required"] is True
    assert outcome["draft_evidence_field"] == "supporting_evidence"

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


def test_hypothesis_literature_bundle_uses_exact_bound_question(
    monkeypatch,
) -> None:
    config = _config("hypothesis-exact-literature-question")
    hypothesis_tools._STATES.pop("hypothesis-exact-literature-question", None)
    question = (
        "请解释第26太阳活动周强度的机制；严格区分证据、推断、探索性假设和证据缺口。"
    )
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": question},
        config=config,
    )
    _update(
        config,
        "upsert_candidate",
        {"id": "H1", "statement": "A provisional mechanism."},
    )
    before = json.loads(
        hypothesis_tools.scientific_hypothesis_get_draft.invoke({}, config=config)
    )
    captured: dict[str, object] = {}

    def fake_build(
        store,
        research_question,
        focus,
        *,
        feed_ids,
        limit,
        run_id,
    ):
        captured.update(
            {
                "store": store,
                "research_question": research_question,
                "focus": focus,
                "feed_ids": feed_ids,
                "limit": limit,
                "run_id": run_id,
            }
        )
        return {
            "status": "evidence_gap",
            "bundle_id": "litbundle_test",
            "research_question": research_question,
            "focus": focus,
            "source_count": 0,
            "sources": [],
        }

    sentinel_store = object()
    monkeypatch.setattr(knowledge_tools, "_get_store", lambda: sentinel_store)
    monkeypatch.setattr(
        knowledge_tools.literature,
        "build_literature_task_bundle",
        fake_build,
    )

    outcome = json.loads(
        hypothesis_tools.scientific_hypothesis_build_literature_bundle.invoke(
            {
                "focus": (
                    "第26太阳活动周 solar cycle 26 极区磁场 polar field "
                    "子午流 meridional flow"
                ),
                "limit": 3,
            },
            config=config,
        )
    )

    assert outcome["status"] == "evidence_gap"
    assert outcome["request_source"] == "bound_hypothesis_request"
    assert captured["store"] is sentinel_store
    assert captured["research_question"] == question
    assert captured["run_id"] == "hypothesis-exact-literature-question"
    assert any(
        warning["code"] == "literature_pass_missing"
        for warning in before["soft_warnings"]
    )
    assert before["next_required_action"]["tool"] == (
        "scientific_hypothesis_build_literature_bundle"
    )
    after = json.loads(
        hypothesis_tools.scientific_hypothesis_get_draft.invoke({}, config=config)
    )
    assert not any(
        warning["code"] == "literature_pass_missing"
        for warning in after["soft_warnings"]
    )


def test_hypothesis_literature_bundle_failure_is_a_single_recorded_attempt(
    monkeypatch,
) -> None:
    thread_id = "hypothesis-literature-failure-once"
    config = _config(thread_id)
    hypothesis_tools._STATES.pop(thread_id, None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Is the Waldmeier rise-time relation stable?"},
        config=config,
    )
    calls = 0

    def fail_build(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise ValueError("cached literature unavailable")

    monkeypatch.setattr(knowledge_tools, "_get_store", lambda: object())
    monkeypatch.setattr(
        knowledge_tools.literature,
        "build_literature_task_bundle",
        fail_build,
    )

    first = json.loads(
        hypothesis_tools.scientific_hypothesis_build_literature_bundle.invoke(
            {"focus": "Waldmeier effect 上升时间 rise time", "limit": 3},
            config=config,
        )
    )
    second = json.loads(
        hypothesis_tools.scientific_hypothesis_build_literature_bundle.invoke(
            {"focus": "Waldmeier effect 上升时间 rise time", "limit": 3},
            config=config,
        )
    )

    assert first["status"] == "needs_revision"
    assert second["status"] == "needs_revision"
    assert "already attempted" in second["validation_error"]
    assert calls == 1
    assert hypothesis_tools._STATES[thread_id].literature_bundle_attempted is True


def test_novelty_bundle_deduplicates_families_and_preserves_coverage_gap(
    monkeypatch,
) -> None:
    thread_id = "hypothesis-novelty-bundle"
    question = "Compare polar-field transport with a measurement null for the next solar cycle."
    config = _config(thread_id)
    hypothesis_tools._STATES.pop(thread_id, None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": question},
        config=config,
    )
    axes = [
        "polar field transport mechanism 极区场输运机制",
        "polar field observable cycle amplitude 极区场与活动周振幅",
        "polar field measurement drift statistical null 极区场测量漂移统计零假设",
    ]

    def fake_build(
        store,
        research_question,
        focus,
        *,
        feed_ids,
        limit,
        run_id,
        ranking_focus,
        required_anchor_phrases,
    ):
        index = next(index for index, axis in enumerate(axes) if focus.endswith(axis))
        assert ranking_focus == axes[index]
        assert required_anchor_phrases == ["polar field"]
        sources = [
            {
                "source_id": f"source-{index}-{offset}",
                "family_id": "shared-family"
                if offset == 0
                else f"family-{index}-{offset}",
                "title": f"Nearest art {index}-{offset}",
                "abstract": "A cached abstract.",
            }
            for offset in range(3)
        ]
        return {
            "status": "ok",
            "bundle_id": f"bundle-{index}",
            "sources": sources,
        }

    search_calls = []

    def fake_search(store, query, **kwargs):
        search_calls.append((query, kwargs))
        return {
            "status": "ok",
            "providers_queried": ["openalex", "arxiv", "crossref"],
            "provider_diagnostics": [],
            "count": 3,
        }

    monkeypatch.setattr(knowledge_tools, "_get_store", lambda: object())
    monkeypatch.setattr(
        knowledge_tools.literature,
        "build_literature_task_bundle",
        fake_build,
    )
    monkeypatch.setattr(
        knowledge_tools.literature,
        "search_literature",
        fake_search,
    )
    outcome = json.loads(
        hypothesis_tools.scientific_hypothesis_build_novelty_bundle.invoke(
            {"query_axes": axes, "per_axis_limit": 3},
            config=config,
        )
    )

    assert outcome["schema_version"] == "scientific-hypothesis-novelty-bundle-v1"
    assert [call[0] for call in search_calls] == axes
    assert all(call[1]["source"] == "all" for call in search_calls)
    assert len(outcome["search_receipts"]) == 3
    assert outcome["status"] == "coverage_gap"
    assert outcome["searched_family_count"] == 7
    assert len(outcome["axis_results"]) == 3
    state = hypothesis_tools._STATES[thread_id]
    assert state.novelty_bundle_attempted is True
    assert state.novelty_bundle_ids == ["bundle-0", "bundle-1", "bundle-2"]


def test_novelty_bundle_keeps_long_bound_question_out_of_axis_focus(
    monkeypatch,
) -> None:
    thread_id = "hypothesis-novelty-long-question"
    question = "第26太阳活动周初步概率预测。" * 30
    config = _config(thread_id)
    hypothesis_tools._STATES.pop(thread_id, None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": question},
        config=config,
    )
    axes = [
        "smoothed sunspot number peak 气候学",
        "smoothed sunspot number peak 制度延续",
        "smoothed sunspot number peak 统计零假设",
    ]
    captured: list[tuple[str, str]] = []

    def fake_build(
        store,
        research_question,
        focus,
        *,
        feed_ids,
        limit,
        run_id,
        ranking_focus,
        required_anchor_phrases,
    ):
        captured.append((research_question, focus))
        return {"status": "evidence_gap", "bundle_id": None, "sources": []}

    monkeypatch.setattr(knowledge_tools, "_get_store", lambda: object())
    monkeypatch.setattr(
        knowledge_tools.literature,
        "build_literature_task_bundle",
        fake_build,
    )
    monkeypatch.setattr(
        knowledge_tools.literature,
        "search_literature",
        lambda *args, **kwargs: {
            "status": "evidence_gap",
            "providers_queried": [],
            "provider_diagnostics": [],
            "count": 0,
        },
    )

    outcome = json.loads(
        hypothesis_tools.scientific_hypothesis_build_novelty_bundle.invoke(
            {"query_axes": axes, "per_axis_limit": 3},
            config=config,
        )
    )

    assert outcome["schema_version"] == "scientific-hypothesis-novelty-bundle-v1"
    assert [row[0] for row in captured] == [question] * 3
    assert all(len(focus) <= 500 for _, focus in captured)
    assert [row["query_axis"] for row in outcome["axis_results"]] == axes


def test_draft_update_accepts_common_structured_payload_wrappers() -> None:
    config = _config("hypothesis-draft-wrapper-aliases")
    hypothesis_tools._STATES.pop("hypothesis-draft-wrapper-aliases", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Compare two mechanisms."},
        config=config,
    )
    _update(config, "upsert_candidate", {"id": "H1", "statement": "Mechanism one"})
    _update(config, "upsert_candidate", {"id": "H2", "statement": "Mechanism two"})

    patch_with_alias = _update(
        config,
        "patch_candidate",
        {"id": "H1", "patch": {"evidence_gaps": ["Evidence is incomplete."]}},
    )
    patch_with_direct_fields = _update(
        config,
        "patch_candidate",
        {"candidate_id": "H2", "evidence_gaps": ["Evidence is also incomplete."]},
    )
    distinctions = _update(
        config,
        "set_distinctions",
        {
            "pairwise_distinctions": [
                {
                    "left_id": "H1",
                    "right_id": "H2",
                    "distinction": "Different physical causes.",
                }
            ]
        },
    )
    notes = _update(
        config,
        "set_portfolio_notes",
        {"portfolio_notes": "This portfolio remains exploratory."},
    )
    draft = json.loads(
        hypothesis_tools.scientific_hypothesis_get_draft.invoke({}, config=config)
    )["draft"]

    assert patch_with_alias["status"] == "draft"
    assert patch_with_direct_fields["status"] == "draft"
    assert distinctions["status"] == "draft"
    assert notes["status"] == "draft"
    assert draft["candidates"][0]["evidence_gaps"]
    assert draft["candidates"][1]["evidence_gaps"]
    assert draft["pairwise_distinctions"]
    assert draft["portfolio_notes"] == "This portfolio remains exploratory."


def test_draft_update_normalizes_tail_review_novelty_alias() -> None:
    config = _config("hypothesis-draft-tail-novelty-alias")
    hypothesis_tools._STATES.pop("hypothesis-draft-tail-novelty-alias", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Compare a baseline with an unverified tail candidate."},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-draft-tail-novelty-alias"]
    response = make_response(state.request)
    response["candidates"][0]["scientific_quality"]["novelty_status"] = (
        "tail_candidate_unverified"
    )

    _update(config, "replace", response)
    draft = json.loads(
        hypothesis_tools.scientific_hypothesis_get_draft.invoke({}, config=config)
    )["draft"]

    assert (
        draft["candidates"][0]["scientific_quality"]["novelty_status"]
        == "novelty_not_assessed"
    )


def test_draft_warns_when_bound_literature_is_not_attached_to_candidate() -> None:
    config = _config("hypothesis-unattached-literature")
    hypothesis_tools._STATES.pop("hypothesis-unattached-literature", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Compare two possible solar-cycle mechanisms."},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-unattached-literature"]
    state.evidence_register.bind(
        {
            "evidence_id": "litevidence_unattached",
            "evidence_kind": "literature",
            "material_id": "litbundle_test_001",
            "excerpt": json.dumps(
                {
                    "status": "verified",
                    "bundle_id": "litbundle_test_001",
                    "source_id": "crossref:10.1000/example",
                    "role": "opposes",
                    "quote": "The proposed transport effect is minimal.",
                    "claim": "Transport dominates the cycle amplitude.",
                },
                ensure_ascii=False,
            ),
            "verified_support": True,
            "role": "opposes",
        }
    )

    outcome = _update(config, "replace", make_response(state.request))
    warnings = [
        warning
        for warning in outcome["soft_warnings"]
        if warning["code"] == "unattached_literature_evidence"
    ]

    assert warnings
    assert "litevidence_unattached" in warnings[0]["message"]


def test_draft_rejects_unsupported_transport_amplitude_direction() -> None:
    config = _config("hypothesis-transport-amplitude-overclaim")
    hypothesis_tools._STATES.pop("hypothesis-transport-amplitude-overclaim", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "What controls the next solar-cycle amplitude?"},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-transport-amplitude-overclaim"]
    state.literature_bundle_attempted = True
    response = make_response(state.request)
    candidate = response["candidates"][0]
    candidate["statement"] = (
        "Surface meridional-flow transport is the primary determinant "
        "of the next cycle amplitude."
    )
    candidate["mechanism"]["summary"] = (
        "Surface flux transport changes the polar-field buildup."
    )
    candidate["predictions"][0]["statement"] = (
        "Lower meridional-flow speed produces a lower next-cycle amplitude."
    )

    outcome = _update(config, "replace", response)
    warning_codes = {
        warning["code"]
        for warning in outcome["soft_warnings"]
        if warning["candidate_id"] == "cand_dynamo"
    }

    assert "transport_amplitude_overclaim" in warning_codes
    assert "transport_direction_overclaim" in warning_codes


def test_draft_rejects_unbound_causal_dominance_without_transport_terms() -> None:
    config = _config("hypothesis-unbound-causal-dominance")
    hypothesis_tools._STATES.pop("hypothesis-unbound-causal-dominance", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "What constrains the next solar-cycle amplitude?"},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-unbound-causal-dominance"]
    state.literature_bundle_attempted = True
    response = make_response(state.request)
    response["candidates"][0]["statement"] = (
        "The polar precursor is the primary determinant of the next-cycle peak."
    )

    outcome = _update(config, "replace", response)
    warning_codes = {
        warning["code"]
        for warning in outcome["soft_warnings"]
        if warning["candidate_id"] == "cand_dynamo"
    }

    assert "causal_dominance_unbound" in warning_codes


def test_draft_rejects_same_cycle_bmr_amplitude_causality_without_timeline() -> None:
    config = _config("hypothesis-bmr-temporal-order")
    hypothesis_tools._STATES.pop("hypothesis-bmr-temporal-order", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "什么因素控制下一太阳活动周振幅？"},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-bmr-temporal-order"]
    state.literature_bundle_attempted = True
    response = make_response(state.request)
    response["candidates"][0]["statement"] = (
        "下一周期振幅受该周期自身BMR倾斜角随机涨落影响。"
    )

    outcome = _update(config, "replace", response)
    warning_codes = {
        warning["code"]
        for warning in outcome["soft_warnings"]
        if warning["candidate_id"] == "cand_dynamo"
    }

    assert "temporal_causal_order_unbound" in warning_codes


def test_draft_rejects_transport_dominance_for_numbered_cycle_peak() -> None:
    config = _config("hypothesis-numbered-cycle-transport-overclaim")
    hypothesis_tools._STATES.pop("hypothesis-numbered-cycle-transport-overclaim", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "第26周峰值幅度由什么控制？"},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-numbered-cycle-transport-overclaim"]
    state.literature_bundle_attempted = True
    response = make_response(state.request)
    candidate = response["candidates"][0]
    candidate["statement"] = (
        "第26周峰值幅度主要由磁通输运效率决定，而非仅由极小期极区场强度决定。"
    )

    outcome = _update(config, "replace", response)
    warning_codes = {
        warning["code"]
        for warning in outcome["soft_warnings"]
        if warning["candidate_id"] == "cand_dynamo"
    }

    assert "transport_amplitude_overclaim" in warning_codes


def test_draft_rejects_transport_modulation_as_cycle_strength_cause() -> None:
    config = _config("hypothesis-transport-modulation-overclaim")
    hypothesis_tools._STATES.pop("hypothesis-transport-modulation-overclaim", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "什么因素影响下一太阳活动周强度？"},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-transport-modulation-overclaim"]
    state.literature_bundle_attempted = True
    response = make_response(state.request)
    response["candidates"][0]["statement"] = (
        "下一活动周强度与极区场的关系主要由表面磁通输运效率调制；"
        "相同极区场可对应不同的下一周峰值。"
    )

    outcome = _update(config, "replace", response)
    warning_codes = {
        warning["code"]
        for warning in outcome["soft_warnings"]
        if warning["candidate_id"] == "cand_dynamo"
    }

    assert "transport_amplitude_overclaim" in warning_codes


def test_draft_rejects_ungrounded_approximate_cycle_sample_count() -> None:
    config = _config("hypothesis-approximate-sample-count")
    hypothesis_tools._STATES.pop("hypothesis-approximate-sample-count", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "请审查极区磁场前兆，并说明独立样本规模限制。"},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-approximate-sample-count"]
    state.literature_bundle_attempted = True
    response = make_response(state.request)
    response["candidates"][0]["statement"] = (
        "直接观测仅覆盖约3-4个完整独立活动周，样本不足以稳定泛化。"
    )

    outcome = _update(config, "replace", response)
    warning_codes = {
        warning["code"]
        for warning in outcome["soft_warnings"]
        if warning["candidate_id"] == "cand_dynamo"
    }

    assert "ungrounded_sample_count" in warning_codes


def test_draft_rejects_positive_readiness_when_current_state_is_unverified() -> None:
    config = _config("hypothesis-unverified-readiness")
    hypothesis_tools._STATES.pop("hypothesis-unverified-readiness", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "请判断是否已经适合启动下一活动周正式预测。"},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-unverified-readiness"]
    state.literature_bundle_attempted = True
    response = make_response(state.request)
    candidate = response["candidates"][0]
    candidate["statement"] = "截止2026年6月30日，可以条件性启动下一活动周正式预测。"
    candidate["evidence_gaps"] = [
        "截止资料日的最新前兆实际观测值和不确定性需要直接获取数据产品。"
    ]

    outcome = _update(config, "replace", response)
    warning_codes = {
        warning["code"]
        for warning in outcome["soft_warnings"]
        if warning["candidate_id"] == "cand_dynamo"
    }

    assert "readiness_claim_unverified" in warning_codes


def test_draft_rejects_ungrounded_event_timing_window() -> None:
    config = _config("hypothesis-ungrounded-event-window")
    hypothesis_tools._STATES.pop("hypothesis-ungrounded-event-window", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "资料截止2026年6月30日，请判断是否适合启动正式预测。"},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-ungrounded-event-window"]
    state.literature_bundle_attempted = True
    response = make_response(state.request)
    response["candidates"][0]["statement"] = (
        "如果极小已在2025年底或2026年初发生，就可以启动正式预测。"
    )

    outcome = _update(config, "replace", response)
    warning_codes = {
        warning["code"]
        for warning in outcome["soft_warnings"]
        if warning["candidate_id"] == "cand_dynamo"
    }

    assert "ungrounded_event_timing" in warning_codes


def test_draft_rejects_english_candidate_for_chinese_question() -> None:
    config = _config("hypothesis-chinese-language-contract")
    hypothesis_tools._STATES.pop("hypothesis-chinese-language-contract", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "请形成三个可检验的太阳活动周竞争假设。"},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-chinese-language-contract"]
    state.literature_bundle_attempted = True
    response = make_response(state.request)
    candidate = response["candidates"][0]

    def english_only(value: object) -> object:
        if isinstance(value, str):
            return (
                "English candidate narrative."
                if any("\u3400" <= char <= "\u9fff" for char in value)
                else value
            )
        if isinstance(value, list):
            return [english_only(item) for item in value]
        if isinstance(value, dict):
            return {key: english_only(item) for key, item in value.items()}
        return value

    response["candidates"][0] = english_only(candidate)

    outcome = _update(config, "replace", response)
    warning_codes = {
        warning["code"]
        for warning in outcome["soft_warnings"]
        if warning["candidate_id"] == "cand_dynamo"
    }

    assert "candidate_language_mismatch" in warning_codes


def test_draft_rejects_adaptive_model_reuse_of_fixed_holdouts() -> None:
    config = _config("hypothesis-fixed-protocol-reuse")
    hypothesis_tools._STATES.pop("hypothesis-fixed-protocol-reuse", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {
            "request_input": (
                "请用固定两特征线性模型与历史平均基线比较；若候选模型没有"
                "胜过基线，请修正假设，而不是临时增加特征。"
            )
        },
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-fixed-protocol-reuse"]
    state.literature_bundle_attempted = True
    response = make_response(state.request)
    response["portfolio_notes"] = (
        "在同一批留出目标上同时报告原两特征、单特征和奇偶分组模型，再选择误差最低者。"
    )

    outcome = _update(config, "replace", response)
    warning_codes = {warning["code"] for warning in outcome["soft_warnings"]}

    assert "fixed_protocol_adaptive_reuse" in warning_codes


def test_draft_rejects_cross_candidate_literature_citation_without_link() -> None:
    config = _config("hypothesis-cross-candidate-literature")
    hypothesis_tools._STATES.pop("hypothesis-cross-candidate-literature", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Compare two evidence-grounded mechanisms."},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-cross-candidate-literature"]
    state.literature_bundle_attempted = True
    state.evidence_register.bind(
        {
            "evidence_id": "litevidence_biswas_2023",
            "evidence_kind": "literature",
            "material_id": "litbundle_test_002",
            "excerpt": json.dumps(
                {
                    "status": "verified",
                    "bundle_id": "litbundle_test_002",
                    "source_id": "crossref:10.1000/biswas",
                    "role": "supports",
                    "quote": "The precursor relation remains robust.",
                    "claim": "The polar precursor is robust.",
                    "title": "A robustness test",
                },
                ensure_ascii=False,
            ),
            "verified_support": True,
            "role": "supports",
        }
    )
    response = make_response(state.request)
    response["candidates"][0]["supporting_evidence"] = [
        {
            "evidence_id": "litevidence_biswas_2023",
            "relation_note": "Biswas et al. 2023 supports this precursor relation.",
        }
    ]
    response["candidates"][1]["mechanism"]["physical_basis"] = (
        "该候选也引用Biswas et al. 2023作为限制性依据。"
    )

    outcome = _update(config, "replace", response)
    warnings = [
        warning
        for warning in outcome["soft_warnings"]
        if warning["code"] == "cross_candidate_literature_citation"
    ]

    assert warnings
    assert warnings[0]["candidate_id"] == "cand_measure"
    assert "Biswas 2023" in warnings[0]["message"]


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
        "provenance": {"supporting_run_ids": ["run-a", "run-b"]},
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
    monkeypatch.setattr(
        hypothesis_tools, "_draft_warnings", lambda _state, _request: []
    )

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


def test_source_restricted_request_waives_inapplicable_literature_pass(
    tmp_path: Path, monkeypatch
) -> None:
    workspace, config = _bound_config(
        tmp_path, monkeypatch, "hypothesis-source-restricted"
    )
    hypothesis_tools._STATES.pop("hypothesis-source-restricted", None)
    request = make_request(
        upstream_materials=[
            {
                "id": "accepted_data_artifact_v1",
                "material_kind": "data_feature",
                "title": "Accepted bounded SILSO result",
                "locator": "research_review/artifacts/data-artifact/v0001.json",
                "content_notes": (
                    "[source_restricted_evidence_boundary] Only the accepted "
                    "SILSO result capsule may be used."
                ),
                "experiment_summary": None,
            }
        ],
        max_candidates=3,
    )
    request_path = workspace / "hypothesis_request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "@hypothesis_request.json"}, config=config
    )

    created = _update(
        config,
        "upsert_candidate",
        {"id": "H1", "statement": "Rise time and peak strength are associated."},
    )
    warning_codes = {row["code"] for row in created["soft_warnings"]}

    assert "literature_pass_missing" not in warning_codes
    assert (
        hypothesis_tools._STATES[
            "hypothesis-source-restricted"
        ].literature_bundle_attempted
        is False
    )


def test_source_restricted_request_atomically_prebinds_host_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    """A typed host capsule should remove the model's substring-guessing loop."""

    workspace, config = _bound_config(
        tmp_path, monkeypatch, "hypothesis-source-prebound"
    )
    hypothesis_tools._STATES.pop("hypothesis-source-prebound", None)
    rows = {
        "cycle_length_peak": "| cycle length vs peak strength | 24 | -0.3242 (0.1222) |",
        "rise_time_peak": "| rise time vs peak strength | 24 | -0.7495 (<0.0001) |",
        "decline_time_peak": "| decline time vs peak strength | 24 | 0.3827 (0.0649) |",
    }
    request = make_request(
        research_question="SILSO 第1至24周的周期形态统计关系是什么？",
        upstream_materials=[
            {
                "id": "accepted_data_artifact_v1",
                "material_kind": "data_feature",
                "title": "Accepted bounded SILSO result",
                "locator": "outputs/cycle_morphology_strength_report.md",
                "content_notes": (
                    "[source_restricted_evidence_boundary]\n" + "\n".join(rows.values())
                ),
                "experiment_summary": None,
            }
        ],
        max_candidates=3,
    )
    request_path = workspace / "work" / "research_quality" / "hypothesis_request.json"
    request_path.parent.mkdir(parents=True, exist_ok=True)
    request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    seed = {
        "schema_version": "scientific-hypothesis-evidence-seed-v1",
        "request_sha256": canonical_json_sha256(request),
        "evidence": [
            {
                "relationship_key": key,
                "evidence_id": f"{key}_stats",
                "evidence_kind": "upstream",
                "material_id": "accepted_data_artifact_v1",
                "excerpt": excerpt,
                "verified_support": True,
                "role": "supports",
            }
            for key, excerpt in rows.items()
        ],
    }
    seed_path = request_path.with_name("hypothesis_evidence_seed.json")
    seed_path.write_text(json.dumps(seed, ensure_ascii=False), encoding="utf-8")

    first = json.loads(
        hypothesis_tools.scientific_hypothesis_bind_request.invoke(
            {"request_input": "@work/research_quality/hypothesis_request.json"},
            config=config,
        )
    )
    assert first["prebound_evidence_count"] == 3
    assert set(first["prebound_evidence_ids_by_relationship"]) == set(rows)
    assert (
        len(hypothesis_tools._STATES["hypothesis-source-prebound"].evidence_register)
        == 3
    )
    assert first["next_required_action"]["tool"] == "scientific_hypothesis_update_draft"

    second = json.loads(
        hypothesis_tools.scientific_hypothesis_bind_request.invoke(
            {"request_input": "@work/research_quality/hypothesis_request.json"},
            config=config,
        )
    )
    assert second["binding_status"] == "already_bound"
    assert second["prebound_evidence_count"] == 3

    # A tampered seed must fail before replacing the existing task state.
    seed["request_sha256"] = "0" * 64
    seed_path.write_text(json.dumps(seed, ensure_ascii=False), encoding="utf-8")
    failed = json.loads(
        hypothesis_tools.scientific_hypothesis_bind_request.invoke(
            {"request_input": "@work/research_quality/hypothesis_request.json"},
            config=config,
        )
    )
    assert failed["status"] == "needs_revision"
    assert (
        len(hypothesis_tools._STATES["hypothesis-source-prebound"].evidence_register)
        == 3
    )


def test_upsert_candidate_replaces_candidate_and_removes_unsupported_keys() -> None:
    config = _config("hypothesis-upsert-removes-field")
    hypothesis_tools._STATES.pop("hypothesis-upsert-removes-field", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Explain this observation."},
        config=config,
    )

    _update(
        config,
        "upsert_candidate",
        {
            "id": "H1",
            "statement": "A provisional mechanism.",
            "portfolio_notes": "This field is invalid inside a candidate.",
        },
    )
    _update(
        config,
        "upsert_candidate",
        {"id": "H1", "statement": "A repaired provisional mechanism."},
    )
    draft = json.loads(
        hypothesis_tools.scientific_hypothesis_get_draft.invoke({}, config=config)
    )["draft"]

    assert draft["candidates"] == [
        {"id": "H1", "statement": "A repaired provisional mechanism."}
    ]


def test_draft_update_warns_about_unknown_candidate_fields_before_checkpoint() -> None:
    config = _config("hypothesis-draft-unknown-field-warning")
    hypothesis_tools._STATES.pop("hypothesis-draft-unknown-field-warning", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Explain this observation."},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-draft-unknown-field-warning"]
    response = make_response(state.request)
    response["candidates"][0]["limiting_evidence"] = []

    outcome = _update(config, "replace", response)
    unknown_warnings = [
        warning
        for warning in outcome["soft_warnings"]
        if warning["code"] == "candidate_unknown_field"
    ]

    assert unknown_warnings == [
        {
            "code": "candidate_unknown_field",
            "candidate_id": "cand_dynamo",
            "message": "Candidate contains undefined fields: limiting_evidence.",
        }
    ]


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


def test_draft_update_warns_about_chinese_numeric_cutoffs() -> None:
    config = _config("hypothesis-draft-chinese-numeric-warning")
    hypothesis_tools._STATES.pop("hypothesis-draft-chinese-numeric-warning", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "不要为检验虚构数量、比例或区间门槛。"},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-draft-chinese-numeric-warning"]
    response = make_response(state.request)
    response["candidates"][0]["falsification_conditions"] = [
        "在至少三种平滑窗口下方向一致",
        "差异缩小到原始估计的一半以下",
        "观测值落在95%区间内",
    ]

    outcome = _update(config, "replace", response)
    messages = [
        warning["message"]
        for warning in outcome["soft_warnings"]
        if warning["code"] == "ungrounded_numeric_threshold"
    ]

    assert any("至少三种" in message for message in messages)
    assert any("一半" in message for message in messages)
    assert any("95%区间" in message for message in messages)


def test_draft_warns_when_scientific_boundaries_are_missing() -> None:
    config = _config("hypothesis-draft-boundary-warning")
    hypothesis_tools._STATES.pop("hypothesis-draft-boundary-warning", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Explain this observation with explicit scientific limits."},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-draft-boundary-warning"]
    response = make_response(state.request)
    candidate = response["candidates"][0]
    candidate.pop("scope_conditions")
    candidate.pop("epistemic_status")
    candidate.pop("uncertainty")

    outcome = _update(config, "replace", response)
    warning_codes = {
        warning["code"]
        for warning in outcome["soft_warnings"]
        if warning["candidate_id"] == "cand_dynamo"
    }

    assert "candidate_incomplete" in warning_codes
    assert "scope_conditions_missing" in warning_codes
    assert "epistemic_status_missing" in warning_codes
    assert "uncertainty_incomplete" in warning_codes


def test_draft_warns_about_unbounded_and_vague_scientific_language() -> None:
    config = _config("hypothesis-draft-language-warning")
    hypothesis_tools._STATES.pop("hypothesis-draft-language-warning", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Explain this observation without unbounded claims."},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-draft-language-warning"]
    response = make_response(state.request)
    candidate = response["candidates"][0]
    candidate["scope_conditions"]["target_system"] = "所有太阳活动周"
    candidate["falsification_conditions"] = ["若仍有显著差异则放弃"]

    outcome = _update(config, "replace", response)
    warning_codes = {
        warning["code"]
        for warning in outcome["soft_warnings"]
        if warning["candidate_id"] == "cand_dynamo"
    }

    assert "unsupported_scope_generalization" in warning_codes
    assert "unoperationalized_decision_rule" in warning_codes


def test_draft_warns_about_ambiguous_solar_cycle_unit() -> None:
    config = _config("hypothesis-draft-cycle-unit-warning")
    hypothesis_tools._STATES.pop("hypothesis-draft-cycle-unit-warning", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "解释太阳活动周期前兆对下一周期峰值的约束。"},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-draft-cycle-unit-warning"]
    response = make_response(state.request)
    response["candidates"][0]["statement"] = "极区场可约束下一周峰值"

    outcome = _update(config, "replace", response)
    warnings = [
        warning
        for warning in outcome["soft_warnings"]
        if warning["candidate_id"] == "cand_dynamo"
    ]

    assert any(warning["code"] == "ambiguous_solar_cycle_unit" for warning in warnings)


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


def test_checkpoint_current_draft_without_resending_full_response(monkeypatch) -> None:
    config = _config("hypothesis-incremental-checkpoint")
    hypothesis_tools._STATES.pop("hypothesis-incremental-checkpoint", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Explain the difference between two observed minima."},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-incremental-checkpoint"]
    response = make_response(state.request)
    _update(config, "replace", response)
    reviewed = _review_tail(config, response)

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

    assert reviewed["status"] == "tail_reviewed"
    assert reviewed["tail_review_status"] == "current"
    assert checked["working_status"] == "checkpointed"
    assert checked["checkpoint_available"] is True
    assert publish["status"] == "needs_revision"
    assert "current draft differs" in publish["validation_error"]


def test_portfolio_ranking_is_persisted_and_bound_into_checkpoint(
    tmp_path: Path, monkeypatch
) -> None:
    workspace, config = _bound_config(
        tmp_path, monkeypatch, "hypothesis-portfolio-ranking"
    )
    hypothesis_tools._STATES.pop("hypothesis-portfolio-ranking", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Compare two possible explanations for this observation."},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-portfolio-ranking"]
    response = make_response(state.request)
    _update(config, "replace", response)
    _review_tail(config, response)

    ranked = json.loads(
        hypothesis_tools.scientific_hypothesis_rank_portfolio.invoke(
            {
                "ranking_json": json.dumps(
                    _portfolio_ranking_payload(state.latest_draft), ensure_ascii=False
                )
            },
            config=config,
        )
    )
    checked = json.loads(
        hypothesis_tools.scientific_hypothesis_checkpoint_draft.invoke({}, config=config)
    )

    assert ranked["status"] == "portfolio_ranked"
    assert ranked["scientific_support_order"] == ["cand_dynamo", "cand_measure"]
    assert ranked["research_priority_order"] == ["cand_measure", "cand_dynamo"]
    assert checked["working_status"] == "checkpointed"
    state_payload = json.loads(
        (workspace / "work" / "scientific_hypothesis_state.json").read_text(
            encoding="utf-8"
        )
    )
    checkpoint_payload = json.loads(
        (workspace / "work" / "scientific_hypothesis_checkpoint.json").read_text(
            encoding="utf-8"
        )
    )
    assert state_payload["portfolio_ranking"]["schema_version"] == (
        PORTFOLIO_RANKING_VERSION
    )
    assert checkpoint_payload["portfolio_ranking"] == state_payload["portfolio_ranking"]


def test_rejected_portfolio_ranking_persists_failure_without_replacing_last_valid(
    tmp_path: Path, monkeypatch
) -> None:
    workspace, config = _bound_config(
        tmp_path, monkeypatch, "hypothesis-portfolio-ranking-rejection"
    )
    hypothesis_tools._STATES.pop("hypothesis-portfolio-ranking-rejection", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Compare two possible explanations for this observation."},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-portfolio-ranking-rejection"]
    response = make_response(state.request)
    _update(config, "replace", response)
    _review_tail(config, response)
    valid_payload = _portfolio_ranking_payload(state.latest_draft)
    valid = json.loads(
        hypothesis_tools.scientific_hypothesis_rank_portfolio.invoke(
            {"ranking_json": json.dumps(valid_payload, ensure_ascii=False)},
            config=config,
        )
    )
    previous_ranking = json.loads(json.dumps(state.portfolio_ranking))
    invalid_payload = json.loads(json.dumps(valid_payload))
    invalid_payload["ranked_hypotheses"][0]["portfolio_role"] = "physical_precursor"

    rejected = json.loads(
        hypothesis_tools.scientific_hypothesis_rank_portfolio.invoke(
            {"ranking_json": json.dumps(invalid_payload, ensure_ascii=False)},
            config=config,
        )
    )
    persisted = json.loads(
        (workspace / "work" / "scientific_hypothesis_state.json").read_text(
            encoding="utf-8"
        )
    )

    assert valid["status"] == "portfolio_ranked"
    assert rejected["status"] == "needs_revision"
    assert rejected["same_validation_error_count"] == 1
    assert "缺少字段" in rejected["validation_error"]
    assert state.last_validation_error == rejected["validation_error"]
    assert state.same_validation_error_count == 1
    assert state.portfolio_ranking == previous_ranking
    assert persisted["last_validation_error"] == rejected["validation_error"]
    assert persisted["same_validation_error_count"] == 1
    assert persisted["portfolio_ranking"] == previous_ranking


def test_workspace_checkpoint_writes_host_owned_snapshot(tmp_path, monkeypatch) -> None:
    workspace, config = _bound_config(
        tmp_path, monkeypatch, "hypothesis-checkpoint-snapshot"
    )
    hypothesis_tools._STATES.pop("hypothesis-checkpoint-snapshot", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Compare two possible explanations for this observation."},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-checkpoint-snapshot"]
    response = make_response(state.request)
    _update(config, "replace", response)
    _review_tail(config, response)

    checked = json.loads(
        hypothesis_tools.scientific_hypothesis_checkpoint_draft.invoke(
            {}, config=config
        )
    )

    snapshot = workspace / "work" / "scientific_hypothesis_checkpoint.json"
    assert checked["working_status"] == "checkpointed"
    assert checked["checkpoint_snapshot_path"] == (
        "work/scientific_hypothesis_checkpoint.json"
    )
    assert snapshot.is_file()
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    assert payload["checkpoint_sha256"] == canonical_json_sha256(payload["checkpoint"])


def test_multi_candidate_checkpoint_requires_current_tail_review() -> None:
    config = _config("hypothesis-tail-review-required")
    hypothesis_tools._STATES.pop("hypothesis-tail-review-required", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Compare multiple explanations for this observation."},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-tail-review-required"]
    _update(config, "replace", make_response(state.request))

    checked = json.loads(
        hypothesis_tools.scientific_hypothesis_checkpoint_draft.invoke(
            {}, config=config
        )
    )

    assert checked["status"] == "needs_revision"
    assert "independent tail review is required" in checked["validation_error"]


def test_warning_free_multi_candidate_draft_routes_to_tail_review(monkeypatch) -> None:
    config = _config("hypothesis-tail-review-next-action")
    hypothesis_tools._STATES.pop("hypothesis-tail-review-next-action", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Compare multiple explanations for this observation."},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-tail-review-next-action"]
    _update(config, "replace", make_response(state.request))
    monkeypatch.setattr(hypothesis_tools, "_draft_warnings", lambda *_args: [])

    draft = json.loads(
        hypothesis_tools.scientific_hypothesis_get_draft.invoke({}, config=config)
    )

    assert draft["soft_warning_count"] == 0
    assert draft["tail_review_required"] is True
    assert draft["tail_review_status"] == "missing"
    assert draft["natural_language_return_allowed"] is False
    assert draft["next_required_action"]["tool"] == (
        "scientific_hypothesis_review_tail"
    )


def test_warning_free_single_candidate_draft_routes_to_checkpoint(monkeypatch) -> None:
    config = _config("hypothesis-single-checkpoint-next-action")
    hypothesis_tools._STATES.pop("hypothesis-single-checkpoint-next-action", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Assess one bounded explanation for this observation."},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-single-checkpoint-next-action"]
    response = make_response(state.request)
    response["candidates"] = response["candidates"][:1]
    response["pairwise_distinctions"] = []
    _update(config, "replace", response)
    monkeypatch.setattr(hypothesis_tools, "_draft_warnings", lambda *_args: [])

    draft = json.loads(
        hypothesis_tools.scientific_hypothesis_get_draft.invoke({}, config=config)
    )

    assert draft["tail_review_required"] is False
    assert draft["natural_language_return_allowed"] is False
    assert draft["next_required_action"]["tool"] == (
        "scientific_hypothesis_checkpoint_draft"
    )


def test_long_tail_single_candidate_cannot_bypass_review() -> None:
    config = _config("hypothesis-long-tail-single-candidate")
    hypothesis_tools._STATES.pop("hypothesis-long-tail-single-candidate", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Find sparse long-tail hypotheses for this observation."},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-long-tail-single-candidate"]
    response = make_response(
        state.request,
        candidates=[make_response(state.request)["candidates"][0]],
        distinctions=[],
    )
    updated = _update(config, "replace", response)

    checked = json.loads(
        hypothesis_tools.scientific_hypothesis_checkpoint_draft.invoke(
            {}, config=config
        )
    )

    assert updated["tail_review_required"] is True
    assert checked["status"] == "needs_revision"
    assert "independent tail review is required" in checked["validation_error"]


def test_checkpoint_normalization_keeps_tail_review_current() -> None:
    config = _config("hypothesis-tail-review-checkpoint-normalization")
    hypothesis_tools._STATES.pop(
        "hypothesis-tail-review-checkpoint-normalization", None
    )
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Compare multiple explanations for this observation."},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-tail-review-checkpoint-normalization"]
    response = make_response(state.request)
    response["candidates"][0]["statement"] += "  with extra spacing"
    _update(config, "replace", response)
    _review_tail(config, response)

    checked = json.loads(
        hypothesis_tools.scientific_hypothesis_checkpoint_draft.invoke(
            {}, config=config
        )
    )
    status = json.loads(
        hypothesis_tools.scientific_hypothesis_get_status.invoke({}, config=config)
    )

    assert checked["working_status"] == "checkpointed"
    assert status["tail_review_status"] == "current"


def test_tail_review_hard_violation_preserves_pool_for_repair() -> None:
    config = _config("hypothesis-tail-review-violation")
    hypothesis_tools._STATES.pop("hypothesis-tail-review-violation", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Compare multiple explanations for this observation."},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-tail-review-violation"]
    response = make_response(state.request)
    _update(config, "replace", response)

    reviewed = _review_tail(
        config,
        response,
        violation_candidate_id="cand_measure",
    )
    recovered = json.loads(
        hypothesis_tools.scientific_hypothesis_get_draft.invoke({}, config=config)
    )

    assert reviewed["status"] == "needs_revision"
    assert reviewed["draft_changed"] is False
    assert reviewed["rejected_candidate_ids"] == ["cand_measure"]
    assert recovered["candidate_count"] == 2
    assert recovered["tail_review_status"] == "stale"


def test_tail_review_cannot_mark_deterministic_scope_warning_as_passed() -> None:
    config = _config("hypothesis-tail-review-warning-gate")
    hypothesis_tools._STATES.pop("hypothesis-tail-review-warning-gate", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Compare multiple explanations for this observation."},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-tail-review-warning-gate"]
    response = make_response(state.request)
    response["candidates"][0]["scope_conditions"]["target_system"] = "所有太阳活动周"
    _update(config, "replace", response)

    reviewed = _review_tail(config, response)

    assert reviewed["status"] == "needs_revision"
    assert "unsupported_scope_generalization" in reviewed["validation_error"]
    assert state.tail_review is None


def test_candidate_edit_makes_tail_review_stale() -> None:
    config = _config("hypothesis-tail-review-stale")
    hypothesis_tools._STATES.pop("hypothesis-tail-review-stale", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Compare multiple explanations for this observation."},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-tail-review-stale"]
    response = make_response(state.request)
    _update(config, "replace", response)
    reviewed = _review_tail(config, response)
    assert reviewed["tail_review_status"] == "current"

    _update(
        config,
        "patch_candidate",
        {
            "candidate_id": "cand_dynamo",
            "changes": {"statement": "A revised mechanism after independent review"},
        },
    )
    status = json.loads(
        hypothesis_tools.scientific_hypothesis_get_status.invoke({}, config=config)
    )

    assert status["tail_review_status"] == "stale"


def test_evidence_change_makes_tail_review_stale() -> None:
    config = _config("hypothesis-tail-review-evidence-stale")
    hypothesis_tools._STATES.pop("hypothesis-tail-review-evidence-stale", None)
    question = "Compare multiple explanations for this observation."
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": question},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-tail-review-evidence-stale"]
    response = make_response(state.request)
    _update(config, "replace", response)
    reviewed = _review_tail(config, response)
    assert reviewed["tail_review_status"] == "current"

    hypothesis_tools.scientific_hypothesis_bind_evidence.invoke(
        {
            "evidence_id": "ev_user_after_review",
            "evidence_kind": "user",
            "material_id": "user_request",
            "excerpt": question,
            "verified_support": True,
            "role": "limits",
        },
        config=config,
    )
    status = json.loads(
        hypothesis_tools.scientific_hypothesis_get_status.invoke({}, config=config)
    )

    assert status["tail_review_status"] == "stale"


def test_current_tail_review_recovers_after_worker_restart(
    tmp_path: Path, monkeypatch
) -> None:
    _workspace, config = _bound_config(
        tmp_path, monkeypatch, "hypothesis-tail-review-restart"
    )
    hypothesis_tools._STATES.pop("hypothesis-tail-review-restart", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Compare multiple explanations for this observation."},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-tail-review-restart"]
    response = make_response(state.request)
    _update(config, "replace", response)
    reviewed = _review_tail(config, response)
    assert reviewed["tail_review_status"] == "current"

    hypothesis_tools._STATES.pop("hypothesis-tail-review-restart", None)
    recovered = json.loads(
        hypothesis_tools.scientific_hypothesis_get_status.invoke({}, config=config)
    )

    assert recovered["tail_review_status"] == "current"
    assert recovered["tail_review_selected_ids"] == [
        "cand_dynamo",
        "cand_measure",
    ]


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
