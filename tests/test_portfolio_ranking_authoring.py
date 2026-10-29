"""Offline structural guidance fixtures; no research conclusion is generated."""

from __future__ import annotations

from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from jw.research.authoring.portfolio_ranking import (
    portfolio_ranking_authoring_contract,
    portfolio_ranking_schema,
    portfolio_ranking_structure_issues,
    portfolio_ranking_tool_arguments_schema,
)
from scientific_hypothesis.contracts import ContractError
from scientific_hypothesis.ranking import (
    CLAIM_TYPES,
    EVIDENCE_STATUSES,
    FALSIFIABILITY_STATUSES,
    FEASIBILITY_STATUSES,
    FORECAST_ORIGINS,
    OUT_OF_SAMPLE_STATUSES,
    PORTFOLIO_ROLES,
    PORTFOLIO_STATUSES,
    PRIORITY_LEVELS,
    SENSITIVITY_STATUSES,
    SUPPORT_LEVELS,
    validate_portfolio_ranking,
)
from tests.test_hypothesis_portfolio_ranking import _payload, _register


def _row_schema():
    return portfolio_ranking_schema()["properties"]["ranked_hypotheses"]["items"]


@pytest.mark.parametrize("with_lifecycle", [False, True])
def test_native_valid_legacy_and_explicit_lifecycle_inputs_remain_accepted(
    with_lifecycle,
):
    payload = _payload()
    if with_lifecycle:
        for row in payload["ranked_hypotheses"]:
            row.update(
                portfolio_role="challenger",
                portfolio_status="candidate_pending_test",
                forecast_origin="not_applicable",
                forecast_receipt_ref=None,
            )
    original = deepcopy(payload)
    assert validate_portfolio_ranking(payload, _register())
    assert portfolio_ranking_structure_issues(payload) == []
    assert Draft202012Validator(portfolio_ranking_schema()).is_valid(payload)
    assert payload == original


def test_structural_errors_are_aggregated_across_every_nested_branch():
    payload = _payload()
    payload["interpretation"] = "Synthetic unknown field"
    payload["source_runs"] = [{"run_id": "run-a"}]
    del payload["hypothesis_groups"][0]["deduplication_rationale"]
    payload["hypothesis_groups"][0]["member_candidates"][0] = {"candidate_id": 7}
    row = payload["ranked_hypotheses"][0]
    row["scientific_support"] = "low"
    row["research_priority"] = {"level": "unknown"}
    row["support_evidence"] = [{"evidence_id": "ev_descriptive"}]
    row["effect_uncertainty"]["interval_crosses_null"] = "unknown"
    row["sensitivity"]["definition"] = "unknown"
    row["next_experiment"]["feasibility"] = "unknown"
    payload["selected_next_experiment"]["hypothesis_ids"] = []
    original = deepcopy(payload)
    issues = portfolio_ranking_structure_issues(payload)
    paths = {row["path"] for row in issues}
    assert {
        "/",
        "/source_runs/0",
        "/hypothesis_groups/0",
        "/hypothesis_groups/0/member_candidates/0",
        "/hypothesis_groups/0/member_candidates/0/candidate_id",
        "/ranked_hypotheses/0/scientific_support",
        "/ranked_hypotheses/0/research_priority",
        "/ranked_hypotheses/0/research_priority/level",
        "/ranked_hypotheses/0/support_evidence/0",
        "/ranked_hypotheses/0/effect_uncertainty/interval_crosses_null",
        "/ranked_hypotheses/0/sensitivity/definition",
        "/ranked_hypotheses/0/next_experiment/feasibility",
        "/selected_next_experiment/hypothesis_ids",
    } <= paths
    assert any("interpretation" in row["message"] for row in issues)
    assert payload == original


@pytest.mark.parametrize("value", [True, False, 1.0, "1", 0, -1])
def test_rank_keeps_native_strict_integer_requirement(value):
    payload = _payload()
    payload["ranked_hypotheses"][0]["support_rank"] = value
    issues = portfolio_ranking_structure_issues(payload)
    assert any(row["path"].endswith("/support_rank") for row in issues)
    with pytest.raises(ContractError):
        validate_portfolio_ranking(payload, _register())


@pytest.mark.parametrize(
    ("route", "size"),
    [
        (("source_runs",), 0),
        (("source_runs",), 21),
        (("hypothesis_groups",), 0),
        (("hypothesis_groups",), 31),
        (("ranked_hypotheses",), 0),
        (("ranked_hypotheses",), 31),
        (("hypothesis_groups", 0, "member_candidates"), 0),
        (("hypothesis_groups", 0, "member_candidates"), 31),
        (("ranked_hypotheses", 0, "support_evidence"), 31),
        (("ranked_hypotheses", 0, "key_limitations"), 0),
        (("ranked_hypotheses", 0, "key_limitations"), 21),
        (("ranked_hypotheses", 0, "falsifiability", "conditions"), 0),
        (("ranked_hypotheses", 0, "falsifiability", "conditions"), 21),
        (("selected_next_experiment", "hypothesis_ids"), 0),
        (("selected_next_experiment", "hypothesis_ids"), 31),
    ],
)
def test_array_bounds_match_native_structural_limits(route, size):
    payload = _payload()
    target = payload
    for key in route[:-1]:
        target = target[key]
    original = target[route[-1]]
    item = deepcopy(original[0]) if original else "synthetic"
    target[route[-1]] = [deepcopy(item) for _ in range(size)]
    path = "/" + "/".join(str(key) for key in route)
    assert any(
        row["path"] == path for row in portfolio_ranking_structure_issues(payload)
    )


def test_wire_contains_complete_deep_closed_objects_and_legacy_string_branch():
    wire = portfolio_ranking_tool_arguments_schema()
    assert wire["required"] == ["ranking_json"]
    assert wire["additionalProperties"] is False
    schema, legacy = wire["properties"]["ranking_json"]["anyOf"]
    assert legacy == {"type": "string"}
    assert schema == portfolio_ranking_schema()
    assert schema["required"] == [
        "schema_version",
        "source_runs",
        "hypothesis_groups",
        "ranked_hypotheses",
        "selected_next_experiment",
    ]
    group = schema["properties"]["hypothesis_groups"]["items"]
    assert group["additionalProperties"] is False
    assert set(group["required"]) == {
        "hypothesis_id",
        "normalized_statement",
        "member_candidates",
        "deduplication_rationale",
    }
    member = group["properties"]["member_candidates"]["items"]
    assert set(member["required"]) == {"run_id", "candidate_id"}
    row = schema["properties"]["ranked_hypotheses"]["items"]
    expected_nested = {
        "scientific_support": {"level", "rationale"},
        "research_priority": {"level", "rationale"},
        "out_of_sample_validation": {"status", "baseline_comparison"},
        "effect_uncertainty": {
            "effect_summary",
            "interval_summary",
            "interval_crosses_null",
        },
        "sensitivity": {
            "leave_one_out",
            "temporal_split",
            "measurement_regime",
            "definition",
        },
        "falsifiability": {"status", "conditions"},
        "next_experiment": {"objective", "discriminating_power", "feasibility"},
    }
    for name, expected in expected_nested.items():
        child = row["properties"][name]
        assert child["additionalProperties"] is False
        assert set(child["required"]) == set(child["properties"]) == expected
    for name in ("support_evidence", "opposing_evidence"):
        child = row["properties"][name]["items"]
        assert child["additionalProperties"] is False
        assert set(child["required"]) == {
            "evidence_id",
            "dependency_group_id",
            "relation",
        }
    selected = schema["properties"]["selected_next_experiment"]
    assert selected["additionalProperties"] is False
    assert set(selected["required"]) == {
        "hypothesis_ids",
        "objective",
        "discriminating_power",
        "feasibility",
        "rationale",
    }
    assert Draft202012Validator(wire).is_valid({"ranking_json": _payload()})
    assert Draft202012Validator(wire).is_valid({"ranking_json": "legacy JSON text"})


def test_enum_guidance_uses_the_existing_native_sets():
    props = _row_schema()["properties"]
    for name, expected in (
        ("claim_type", CLAIM_TYPES),
        ("current_evidence_status", EVIDENCE_STATUSES),
        ("portfolio_role", PORTFOLIO_ROLES),
        ("portfolio_status", PORTFOLIO_STATUSES),
        ("forecast_origin", FORECAST_ORIGINS),
    ):
        assert set(props[name]["enum"]) == expected
    for name, field, expected in (
        ("scientific_support", "level", SUPPORT_LEVELS),
        ("research_priority", "level", PRIORITY_LEVELS),
        ("out_of_sample_validation", "status", OUT_OF_SAMPLE_STATUSES),
        ("falsifiability", "status", FALSIFIABILITY_STATUSES),
        ("next_experiment", "feasibility", FEASIBILITY_STATUSES),
    ):
        assert set(props[name]["properties"][field]["enum"]) == expected
    assert all(
        set(child["enum"]) == SENSITIVITY_STATUSES
        for child in props["sensitivity"]["properties"].values()
    )


def test_lifecycle_stays_optional_as_a_whole_and_closed_as_a_group():
    payload = _payload()
    row = payload["ranked_hypotheses"][0]
    row["portfolio_role"] = "challenger"
    issues = portfolio_ranking_structure_issues(payload)
    assert any("dependency" in issue["message"] for issue in issues)
    with pytest.raises(ContractError, match="缺少字段"):
        validate_portfolio_ranking(payload, _register())


def test_whitespace_length_semantics_do_not_narrow_legacy_valid_inputs():
    payload = _payload()
    payload["hypothesis_groups"][0]["normalized_statement"] = " " * 3_000 + "x\n"
    payload["ranked_hypotheses"][0]["hypothesis_id"] = "  relation\n"
    original = deepcopy(payload)
    assert validate_portfolio_ranking(payload, _register())
    assert portfolio_ranking_structure_issues(payload) == []
    assert payload == original
    payload["hypothesis_groups"][0]["normalized_statement"] = "x" * 2_001
    # Normalized text bounds intentionally stay at the native validator.
    assert portfolio_ranking_structure_issues(payload) == []
    with pytest.raises(ContractError, match="不能超过"):
        validate_portfolio_ranking(payload, _register())


@pytest.mark.parametrize(
    "defect", ["missing_evidence", "noncontiguous_rank", "high_without_support"]
)
def test_structural_success_does_not_bypass_native_scientific_gates(defect):
    payload = _payload()
    row = payload["ranked_hypotheses"][0]
    if defect == "missing_evidence":
        row["support_evidence"][0]["evidence_id"] = "unbound_synthetic_id"
    elif defect == "noncontiguous_rank":
        row["support_rank"] = 9
    else:
        row["support_evidence"] = []
    assert portfolio_ranking_structure_issues(payload) == []
    with pytest.raises(ContractError):
        validate_portfolio_ranking(payload, _register())


def test_every_return_is_fresh_and_contains_no_scientific_defaults_or_examples():
    def walk(value):
        if isinstance(value, dict):
            assert not {"default", "example", "examples"}.intersection(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    for value in (
        portfolio_ranking_schema(),
        portfolio_ranking_authoring_contract(),
        portfolio_ranking_tool_arguments_schema(),
    ):
        walk(value)
        Draft202012Validator.check_schema(
            value["schema"] if "schema" in value else value
        )
    first = portfolio_ranking_schema()
    first["properties"]["source_runs"]["maxItems"] = 1
    assert portfolio_ranking_schema()["properties"]["source_runs"]["maxItems"] == 20
