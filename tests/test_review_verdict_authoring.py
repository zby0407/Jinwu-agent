"""Offline discoverability and unchanged native verdict routing semantics."""

from __future__ import annotations

import json
import socket
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from langchain_core.utils.function_calling import convert_to_openai_tool

from jw import paths
from jw.research.authoring.review_verdict import verdict_authoring_contract
from jw.research_review import (
    ALL_REVIEW_MODES,
    REVISION_OWNERS_FOR_MODE,
    ResearchReviewStore,
)
from jw.tools import research_review as tools
from jw.workspaces import ensure_thread_workspace
from research_review.contracts import DECISIONS, ISSUE_OWNERS


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    original_connect = socket.socket.connect

    def blocked_connect(instance, address):
        # Windows may create loopback socket pairs for its local event loop.
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
            return original_connect(instance, address)
        raise AssertionError("External network is forbidden in mode-binding tests")

    monkeypatch.setattr(socket.socket, "connect", blocked_connect)


def _quality_claim(claim_id: str) -> dict:
    return {
        "claim_id": claim_id,
        "claim_component": "statement",
        "load_bearing": True,
        "evidence_matrix": [
            {
                "source_ref": None,
                "evidence_role": "gap",
                "source_class": "unknown",
                "evidence_scope": "unknown",
                "directness": "not_assessable",
                "scope_match": "not_assessable",
                "independence_group": "synthetic-unresolved-gap",
                "locator": "No inspected source exists in this engineering fixture.",
                "entailment": "not_assessable",
                "quality_cap": "exploratory",
                "rationale": "Synthetic missing evidence remains a gap.",
            }
        ],
        "method_assessment": {
            "design_status": "not_assessed",
            "independent_sample_unit": "not assessed in this synthetic fixture",
            "independent_sample_count": None,
            "validation_status": "not_assessed",
            "uncertainty_status": "not_assessed",
            "reproducibility_status": "not_assessed",
            "notes": "Synthetic method note must remain unchanged.",
        },
        "novelty_assessment": {
            "status": "novelty_not_assessed",
            "contribution_type": "not_assessed",
            "novelty_delta": "No novelty adjudication is asserted by this fixture.",
            "nearest_prior_art": [],
            "query_axes": [],
            "searched_family_count": 0,
            "search_cutoff": None,
            "coverage_gaps": ["Synthetic prior-art review has not been performed."],
        },
        "conclusion_cap": "exploratory",
        "quality_status": "exploratory",
        "key_gaps": ["Synthetic independent evidence is unavailable."],
    }


@pytest.fixture
def native_review(tmp_path: Path, monkeypatch):
    task_id = "mode-binding-technical-fixture"
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(tmp_path / "bindings"))
    monkeypatch.setenv("JW_EVIDENCE_REVIEW_MODE", "two_pass")
    monkeypatch.setattr(paths, "WORKSPACE_ROOT", tmp_path)
    binding = ensure_thread_workspace(task_id, tmp_path)
    config = {
        "configurable": {
            "thread_id": task_id,
            "workspace_thread_id": task_id,
        }
    }
    store = ResearchReviewStore(Path(binding.workspace), task_id)
    artifact = store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content="Synthetic engineering claim with explicitly unavailable evidence.",
        phase="bounded_data",
    )
    claim = artifact["claims"][0]
    payload = {
        "review_mode": "data",
        "assessment_claims": [
            {
                "claim_id": claim["claim_id"],
                "kind": claim["kind"],
                "disposition": "undecided",
                "supporting_evidence": [],
                "opposing_evidence": [],
                "rationale": "Synthetic reviewer rationale must remain unchanged.",
                "key_uncertainty": "Synthetic independent observations are unavailable.",
                "confidence": "low",
                "next_test": "A synthetic next-test sentence, not an executed test.",
            }
        ],
        "scientific_quality_claims": [_quality_claim(claim["claim_id"])],
        "decision": "block",
        "issues": [
            {
                "rule_id": "SAMPLE_INDEPENDENCE_AND_UNCERTAINTY",
                "severity": "critical",
                "claim_ref": claim["claim_id"],
                "evidence_refs": [],
                "owner": "solar-data",
                "message": "Synthetic independent evidence is unavailable.",
                "required_action": "Supply evidence in a future synthetic revision.",
                "acceptance_test": "The missing evidence is inspected before acceptance.",
            }
        ],
        "blocked_claims": [claim["claim_id"]],
        "carry_forward_limits": ["Synthetic unresolved evidence must remain explicit."],
    }
    return config, store, payload


@pytest.mark.parametrize("mode", ALL_REVIEW_MODES)
def test_guide_uses_native_mode_owners_and_revision_conditions(mode):
    guide = verdict_authoring_contract(mode)
    assert guide["decisions"] == sorted(DECISIONS)
    assert guide["next_owner"]["allowed_values_when_revise"] == sorted(
        REVISION_OWNERS_FOR_MODE[mode]
    )
    assert guide["next_owner"]["required_when_decision"] == "revise"
    assert guide["next_owner"]["must_own_at_least_one_issue"] is True
    assert guide["issues"]["minimum_when_revise"] == 1
    assert guide["issues"]["unique_identity_fields"] == [
        "rule_id",
        "claim_ref",
        "owner",
    ]
    assert guide["issues"]["owner_values"] == sorted(ISSUE_OWNERS)
    validator = Draft202012Validator(guide["routing_schema"])
    for owner in ISSUE_OWNERS | {""}:
        payload = {"decision": "revise", "issues": [{}], "next_owner": owner}
        assert validator.is_valid(payload) is (owner in REVISION_OWNERS_FOR_MODE[mode])
    assert not validator.is_valid({"decision": "revise", "issues": [{}]})
    assert not validator.is_valid(
        {"decision": "revise", "issues": [], "next_owner": next(iter(ISSUE_OWNERS))}
    )


def test_guide_returns_fresh_values_and_rejects_unknown_mode():
    guide = verdict_authoring_contract("experiment_design")
    guide["next_owner"]["allowed_values_when_revise"].clear()
    assert verdict_authoring_contract("experiment_design")["next_owner"][
        "allowed_values_when_revise"
    ] == sorted(REVISION_OWNERS_FOR_MODE["experiment_design"])
    with pytest.raises(ValueError, match="unsupported review_mode"):
        verdict_authoring_contract("not-a-review-mode")


@pytest.mark.parametrize(
    "tool", [tools.evidence_review_submit_round, tools.evidence_review_submit_verdict]
)
def test_public_tools_expose_owner_enum_and_conditional_rule(tool):
    schema = convert_to_openai_tool(tool)["function"]["parameters"]
    owner = schema["properties"]["next_owner"]
    assert set(owner["enum"]) == ISSUE_OWNERS | {""}
    assert owner["default"] == ""
    assert "Required and nonempty for revise" in owner["description"]
    assert "must own at least one" in owner["description"]
    assert "field-level claim_ref" in schema["properties"]["issues"]["description"]


def test_atomic_issue_schema_describes_unique_identity():
    schema = tools.evidence_review_submit_round.args_schema.model_json_schema()
    claim_ref = schema["$defs"]["VerdictIssueInput"]["properties"]["claim_ref"]
    assert "rule_id/claim_ref/owner identity must be unique" in claim_ref["description"]
    assert "new issue_id" in claim_ref["description"]


def test_open_context_returns_same_source_guide_without_creating_records(native_review):
    config, store, _payload = native_review
    result = json.loads(
        tools.evidence_review_open_context.invoke(
            {"review_mode": "data"}, config=config
        )
    )
    assert result["ok"] is True
    assert result["result"]["verdict_authoring_contract"] == verdict_authoring_contract(
        "data"
    )
    assert store.verdicts(mode="data") == []
    assert store.assessments(mode="data") == []


@pytest.mark.parametrize("owner", [None, "", "main", "solar-planner"])
def test_invalid_revision_is_rejected_with_same_guide_and_never_auto_owned(
    native_review, owner
):
    config, store, payload = native_review
    payload = deepcopy(payload)
    payload["assessment_review_mode"] = "two_pass"
    payload["decision"] = "revise"
    if owner is not None:
        payload["next_owner"] = owner
    before = deepcopy(payload)
    result = json.loads(
        tools.evidence_review_submit_round.invoke(payload, config=config)
    )
    assert result["ok"] is False
    assert result["status"] == "error"
    assert result["error_type"] == (
        "ContractError" if owner == "solar-planner" else "ValueError"
    )
    assert result["verdict_authoring_contract"] == verdict_authoring_contract("data")
    assert payload == before
    assert store.verdicts(mode="data") == []
    assert store.assessments(mode="data") == []
    assert store.scientific_quality_assessments(mode="data") == []


def test_duplicate_issue_identity_stays_rejected_even_with_distinct_issue_ids(
    native_review,
):
    config, store, payload = native_review
    payload = deepcopy(payload)
    payload["assessment_review_mode"] = "two_pass"
    original = payload["issues"][0]
    payload["issues"] = [
        {**original, "issue_id": "synthetic-first-label"},
        {**original, "issue_id": "synthetic-second-label"},
    ]
    before = deepcopy(payload)
    result = json.loads(
        tools.evidence_review_submit_round.invoke(payload, config=config)
    )
    assert result["ok"] is False
    assert "duplicates a rule_id/claim_ref/owner identity" in result["message"]
    assert result["verdict_authoring_contract"] == verdict_authoring_contract("data")
    assert payload == before
    assert store.verdicts(mode="data") == []


def test_empty_issue_revision_stays_rejected_with_guide(native_review):
    config, store, payload = native_review
    payload = deepcopy(payload)
    payload.update(
        assessment_review_mode="two_pass",
        decision="revise",
        issues=[],
        next_owner="solar-data",
    )
    result = json.loads(
        tools.evidence_review_submit_round.invoke(payload, config=config)
    )
    assert result["ok"] is False
    assert result["verdict_authoring_contract"] == verdict_authoring_contract("data")
    assert store.verdicts(mode="data") == []


def test_unrelated_error_projection_is_backward_compatible():
    result = json.loads(tools._error(ValueError("Synthetic unrelated failure")))
    assert result == {
        "ok": False,
        "status": "error",
        "error_type": "ValueError",
        "message": "Synthetic unrelated failure",
    }
