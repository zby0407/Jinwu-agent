from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
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


def test_draft_requires_each_candidate_to_link_evidence() -> None:
    config = _config("hypothesis-draft-candidate-evidence-link")
    hypothesis_tools._STATES.pop("hypothesis-draft-candidate-evidence-link", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Compare evidence-grounded mechanisms."},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-draft-candidate-evidence-link"]
    state.literature_bundle_attempted = True
    response = make_response(state.request)

    outcome = _update(config, "replace", response)
    warnings = [
        warning
        for warning in outcome["soft_warnings"]
        if warning["code"] == "candidate_evidence_unlinked"
    ]

    assert {warning["candidate_id"] for warning in warnings} == {
        "cand_dynamo",
        "cand_measure",
    }
    assert outcome["return_gate"] == "blocked_until_warnings_resolved"
    assert outcome["natural_language_return_allowed"] is False


def test_draft_warns_about_unbound_cycle_count_ranges_in_visible_rationale() -> None:
    config = _config("hypothesis-draft-cycle-count-warning")
    hypothesis_tools._STATES.pop("hypothesis-draft-cycle-count-warning", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Explain this observation without invented sample counts."},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-draft-cycle-count-warning"]
    response = make_response(state.request)
    candidate = response["candidates"][0]
    candidate["mechanism"]["required_premises"] = ["直接观测仅覆盖约3-4周"]
    candidate["confidence"]["basis"] = "直接观测仅覆盖约3-4个活动周"

    outcome = _update(config, "replace", response)
    threshold_warnings = [
        warning
        for warning in outcome["soft_warnings"]
        if warning["code"] == "ungrounded_numeric_threshold"
        and warning["candidate_id"] == "cand_dynamo"
    ]

    assert len(threshold_warnings) >= 2


def test_draft_warns_about_unbound_numbers_in_all_reader_visible_fields() -> None:
    config = _config("hypothesis-draft-visible-number-warning")
    hypothesis_tools._STATES.pop("hypothesis-draft-visible-number-warning", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Explain this observation without invented timelines."},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-draft-visible-number-warning"]
    response = make_response(state.request)
    candidate = response["candidates"][0]
    candidate["evidence_gaps"] = [
        "直接观测仅覆盖约4–5个活动周",
        "直接观测仅覆盖约4周",
    ]
    candidate["next_test"]["objective"] = "预计2028–2030年再启动正式预测"

    outcome = _update(config, "replace", response)
    warnings = {
        (warning["code"], warning["message"])
        for warning in outcome["soft_warnings"]
        if warning["candidate_id"] == "cand_dynamo"
    }

    assert any(
        code == "ungrounded_numeric_threshold" and "4–5个" in message
        for code, message in warnings
    )
    assert any(
        code == "ungrounded_numeric_threshold" and "约4周" in message
        for code, message in warnings
    )
    assert any(
        code == "ungrounded_numeric_threshold" and "2028–2030" in message
        for code, message in warnings
    )


def test_draft_summary_blocks_natural_language_return_until_warnings_clear() -> None:
    config = _config("hypothesis-draft-return-gate")
    hypothesis_tools._STATES.pop("hypothesis-draft-return-gate", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Compare three possible mechanisms."},
        config=config,
    )

    outcome = _update(
        config,
        "upsert_candidate",
        {"id": "H1", "statement": "A provisional mechanism."},
    )

    assert outcome["return_gate"] == "blocked_until_warnings_resolved"
    assert outcome["natural_language_return_allowed"] is False
    assert outcome["next_required_action"]["tool"] == (
        "scientific_hypothesis_build_literature_bundle"
    )


def test_parallel_state_persistence_serializes_atomic_replace(
    tmp_path: Path, monkeypatch
) -> None:
    _workspace, config = _bound_config(
        tmp_path, monkeypatch, "hypothesis-parallel-persist"
    )
    hypothesis_tools._STATES.pop("hypothesis-parallel-persist", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Explain this observation."},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-parallel-persist"]
    real_replace = os.replace
    start = threading.Barrier(3)
    replacement_guard = threading.Lock()
    second_replacement_attempted = threading.Event()
    active_replacements = 0
    temporary_names: set[str] = set()

    def windows_exclusive_replace(source, destination):
        nonlocal active_replacements
        with replacement_guard:
            temporary_names.add(Path(source).name)
            if active_replacements:
                second_replacement_attempted.set()
                raise PermissionError("simulated Windows destination-file contention")
            active_replacements += 1
        try:
            second_replacement_attempted.wait(timeout=0.3)
            real_replace(source, destination)
        finally:
            with replacement_guard:
                active_replacements -= 1

    def persist_once(_index: int) -> Path | None:
        start.wait(timeout=5)
        return hypothesis_tools._persist_state(config, state)

    monkeypatch.setattr(
        hypothesis_tools,
        "os",
        SimpleNamespace(
            fdopen=os.fdopen,
            replace=windows_exclusive_replace,
        ),
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(persist_once, index) for index in range(2)]
        start.wait(timeout=5)
        results = [future.result(timeout=5) for future in futures]

    assert all(result is not None for result in results)
    assert len(temporary_names) == 2
    assert not second_replacement_attempted.is_set()
    assert state.persistence_warning is None


def test_parallel_draft_patches_are_one_atomic_state_transaction(monkeypatch) -> None:
    config = _config("hypothesis-parallel-patches")
    hypothesis_tools._STATES.pop("hypothesis-parallel-patches", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Compare two possible mechanisms."},
        config=config,
    )
    _update(config, "upsert_candidate", {"id": "H1", "statement": "One"})
    _update(config, "upsert_candidate", {"id": "H2", "statement": "Two"})

    real_normalize = hypothesis_tools._normalize_working_draft
    start = threading.Barrier(3)
    overlap = threading.Event()
    active_guard = threading.Lock()
    active = 0
    max_active = 0

    def observed_normalize(payload, request):
        nonlocal active, max_active
        normalized = real_normalize(payload, request)
        with active_guard:
            active += 1
            max_active = max(max_active, active)
            if active > 1:
                overlap.set()
        overlap.wait(timeout=0.2)
        with active_guard:
            active -= 1
        return normalized

    monkeypatch.setattr(
        hypothesis_tools,
        "_normalize_working_draft",
        observed_normalize,
    )

    def patch_candidate(candidate_id: str, statement: str) -> dict[str, object]:
        start.wait(timeout=5)
        return _update(
            config,
            "patch_candidate",
            {
                "candidate_id": candidate_id,
                "changes": {"statement": statement},
            },
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(patch_candidate, "H1", "One revised"),
            executor.submit(patch_candidate, "H2", "Two revised"),
        ]
        start.wait(timeout=5)
        [future.result(timeout=5) for future in futures]

    draft = json.loads(
        hypothesis_tools.scientific_hypothesis_get_draft.invoke({}, config=config)
    )["draft"]
    statements = {
        candidate["id"]: candidate["statement"] for candidate in draft["candidates"]
    }

    assert max_active == 1
    assert statements == {"H1": "One revised", "H2": "Two revised"}


def test_checkpoint_rejects_unresolved_draft_warnings() -> None:
    config = _config("hypothesis-warning-checkpoint")
    hypothesis_tools._STATES.pop("hypothesis-warning-checkpoint", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Explain this observation."},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-warning-checkpoint"]
    _update(config, "replace", make_response(state.request))

    outcome = json.loads(
        hypothesis_tools.scientific_hypothesis_checkpoint_draft.invoke(
            {}, config=config
        )
    )

    assert outcome["status"] == "needs_revision"
    assert "literature_pass_missing" in outcome["validation_error"]
    assert state.validated_response is None


def test_publish_rechecks_unresolved_draft_warnings() -> None:
    config = _config("hypothesis-warning-publish")
    hypothesis_tools._STATES.pop("hypothesis-warning-publish", None)
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": "Explain this observation."},
        config=config,
    )
    state = hypothesis_tools._STATES["hypothesis-warning-publish"]
    response = make_response(state.request)
    response_sha = canonical_json_sha256(response)
    state.latest_draft = response
    state.latest_draft_sha256 = response_sha
    state.validated_response = response
    state.preflight_response_sha256 = response_sha
    state.checkpoint_evidence_sha256 = hypothesis_tools._evidence_sha256(
        state.evidence_register
    )

    outcome = json.loads(
        hypothesis_tools.scientific_hypothesis_freeze.invoke({}, config=config)
    )

    assert outcome["status"] == "needs_revision"
    assert "literature_pass_missing" in outcome["validation_error"]


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
    _update(config, "replace", make_response(state.request))
    monkeypatch.setattr(
        hypothesis_tools, "_draft_warnings", lambda _state, _request: []
    )

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
