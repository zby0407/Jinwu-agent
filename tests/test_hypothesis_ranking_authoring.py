"""Offline discoverability and native-gate fixtures; no research execution."""

from __future__ import annotations

import json
from copy import deepcopy
from unittest.mock import Mock

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool

from jw.research.authoring.portfolio_ranking import (
    portfolio_ranking_authoring_contract,
    portfolio_ranking_schema,
    portfolio_ranking_structure_issues,
    portfolio_ranking_tool_arguments_schema,
)
from jw.tools import scientific_hypothesis as tools
from scientific_hypothesis.contracts import ContractError, canonical_json_sha256
from scientific_hypothesis.ranking import validate_portfolio_ranking
from scientific_hypothesis.tail_search import candidate_pool_sha256
from tests.test_hypothesis import make_response
from tests.test_scientific_hypothesis_working_state import (
    _bound_config,
    _portfolio_ranking_payload,
    _review_tail,
    _update,
)


@pytest.fixture
def ready(tmp_path, monkeypatch):
    root, config = _bound_config(tmp_path, monkeypatch, "ranking-authoring-fixture")
    tools._STATES.pop("ranking-authoring-fixture", None)
    bound = json.loads(
        tools.scientific_hypothesis_bind_request.invoke(
            {
                "request_input": "Compare synthetic alternatives in this engineering fixture."
            },
            config=config,
        )
    )
    state = tools._state(config)
    response = make_response(state.request)
    _update(config, "replace", response)
    reviewed = _review_tail(config, response)
    assert reviewed["tail_review_status"] == "current", reviewed
    payload = _portfolio_ranking_payload(state.latest_draft)
    return root, config, state, payload, bound


def rank(config, payload):
    return json.loads(
        tools.scientific_hypothesis_rank_portfolio.invoke(
            {"ranking_json": payload}, config=config
        )
    )


def test_real_tool_wire_exposes_closed_nested_contract_and_legacy_string():
    wire = convert_to_openai_tool(tools.scientific_hypothesis_rank_portfolio)[
        "function"
    ]["parameters"]
    assert wire == portfolio_ranking_tool_arguments_schema()
    assert wire["required"] == ["ranking_json"]
    assert wire["additionalProperties"] is False
    branches = wire["properties"]["ranking_json"]["anyOf"]
    assert branches == [portfolio_ranking_schema(), {"type": "string"}]
    schema = branches[0]
    row = schema["properties"]["ranked_hypotheses"]["items"]
    assert row["additionalProperties"] is False
    for key in (
        "scientific_support",
        "research_priority",
        "out_of_sample_validation",
        "effect_uncertainty",
        "sensitivity",
        "falsifiability",
        "next_experiment",
    ):
        assert row["properties"][key]["additionalProperties"] is False
    assert row["properties"]["falsifiability"]["properties"]["status"]["enum"]
    assert row["properties"]["next_experiment"]["properties"]["feasibility"]["enum"]
    assert "config" not in wire["properties"]


def test_bind_and_get_expose_identical_complete_authoring_guide(ready):
    root, config, state, _payload, bound = ready
    before = (root / "work/scientific_hypothesis_state.json").read_bytes()
    result = json.loads(tools.scientific_hypothesis_get_draft.invoke({}, config=config))
    contract = portfolio_ranking_authoring_contract()
    assert bound["portfolio_ranking_contract"]["authoring_contract"] == contract
    assert result["portfolio_ranking_contract"] == bound["portfolio_ranking_contract"]
    assert (
        result["portfolio_ranking_contract"]["authoring_contract"]["schema"]
        == portfolio_ranking_schema()
    )
    assert state.portfolio_ranking is None
    assert (root / "work/scientific_hypothesis_state.json").read_bytes() == before


@pytest.mark.parametrize("legacy_string", [False, True])
def test_native_object_and_legacy_json_preserve_payload_and_semantic_result(
    ready, legacy_string
):
    root, config, state, payload, _bound = ready
    before = deepcopy(payload)
    result = rank(config, json.dumps(payload) if legacy_string else payload)
    assert result["status"] == "portfolio_ranked", result
    assert payload == before
    assert state.portfolio_ranking == validate_portfolio_ranking(
        before, state.evidence_register
    )
    assert state.portfolio_ranking_candidate_pool_sha256 == candidate_pool_sha256(
        state.latest_draft
    )
    assert state.portfolio_ranking_evidence_sha256 == canonical_json_sha256(
        {"evidence_register": state.evidence_register.all()}
    )
    saved = json.loads(
        (root / "work/scientific_hypothesis_state.json").read_text(encoding="utf-8")
    )
    assert saved["portfolio_ranking"] == state.portfolio_ranking
    assert result["scientific_support_order"] == ["cand_dynamo", "cand_measure"]
    assert result["research_priority_order"] == ["cand_measure", "cand_dynamo"]


@pytest.mark.parametrize("legacy_string", [False, True])
def test_error_aggregates_structure_and_preserves_original_failure_count_and_ranking(
    ready, legacy_string
):
    root, config, state, payload, _bound = ready
    assert rank(config, payload)["status"] == "portfolio_ranked"
    previous = deepcopy(state.portfolio_ranking)
    bad = deepcopy(payload)
    del bad["ranked_hypotheses"][0]["sensitivity"]["measurement_regime"]
    bad["ranked_hypotheses"][0]["falsifiability"]["status"] = "invented-enum"
    bad["selected_next_experiment"]["unexpected"] = "invalid engineering field"
    before = deepcopy(bad)
    with pytest.raises(ContractError) as expected:
        validate_portfolio_ranking(bad, state.evidence_register)
    argument = json.dumps(bad) if legacy_string else bad
    failed = rank(config, argument)
    assert failed["validation_error"] == str(expected.value)
    assert failed["same_validation_error_count"] == 1
    assert failed["structure_checked"] is True
    assert failed["structure_issues"] == portfolio_ranking_structure_issues(before)
    messages = " ".join(issue["message"] for issue in failed["structure_issues"])
    assert all(
        text in messages
        for text in ("measurement_regime", "invented-enum", "unexpected")
    )
    assert failed["authoring_contract"] == portfolio_ranking_authoring_contract()
    assert bad == before
    assert state.portfolio_ranking == previous
    repeated = rank(config, argument)
    assert repeated["status"] == "review_limit_reached"
    assert repeated["same_validation_error_count"] == 2
    assert repeated["validation_error"] == failed["validation_error"]
    persisted = json.loads(
        (root / "work/scientific_hypothesis_state.json").read_text(encoding="utf-8")
    )
    assert persisted["portfolio_ranking"] == previous
    assert persisted["same_validation_error_count"] == 2


def test_json_syntax_failure_reports_no_invented_structural_payload(ready):
    _root, config, state, _payload, _bound = ready
    result = rank(config, '{"ranking_json":')
    assert result["status"] == "needs_revision"
    assert "Expecting value" in result["validation_error"]
    assert result["same_validation_error_count"] == 1
    assert result["structure_checked"] is False
    assert result["structure_issues"] == []
    assert result["authoring_contract"] == portfolio_ranking_authoring_contract()
    assert state.portfolio_ranking is None


def test_structurally_legal_payload_still_faces_unchanged_scientific_gate(ready):
    _root, config, state, payload, _bound = ready
    bad = deepcopy(payload)
    bad["ranked_hypotheses"][0]["scientific_support"]["level"] = "high"
    assert portfolio_ranking_structure_issues(bad) == []
    with pytest.raises(ContractError) as expected:
        validate_portfolio_ranking(bad, state.evidence_register)
    result = rank(config, bad)
    assert result["validation_error"] == str(expected.value)
    assert result["structure_issues"] == []
    assert result["structure_checked"] is True
    assert state.portfolio_ranking is None


def test_current_tail_gate_remains_before_ranking_validation(ready, monkeypatch):
    _root, config, state, payload, _bound = ready
    state.tail_review["selected_candidate_pool_sha256"] = "0" * 64
    validator = Mock(wraps=tools.validate_portfolio_ranking)
    monkeypatch.setattr(tools, "validate_portfolio_ranking", validator)
    result = rank(config, payload)
    assert "current independent tail review" in result["validation_error"]
    assert result["authoring_contract"] == portfolio_ranking_authoring_contract()
    validator.assert_not_called()
    assert state.portfolio_ranking is None


def test_current_pool_coverage_is_not_filled_by_authoring_schema(ready):
    _root, config, state, payload, _bound = ready
    partial = deepcopy(payload)
    partial["hypothesis_groups"] = partial["hypothesis_groups"][:1]
    partial["ranked_hypotheses"] = partial["ranked_hypotheses"][:1]
    partial["ranked_hypotheses"][0]["research_priority_rank"] = 1
    partial["selected_next_experiment"]["hypothesis_ids"] = ["cand_dynamo"]
    assert portfolio_ranking_structure_issues(partial) == []
    validate_portfolio_ranking(partial, state.evidence_register)
    result = rank(config, partial)
    assert "omits current candidates" in result["validation_error"]
    assert "cand_measure" in result["validation_error"]
    assert state.portfolio_ranking is None
