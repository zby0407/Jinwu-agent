"""Synthetic, offline tests for model-facing compact plan authoring and freeze."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from langchain_core.utils.function_calling import convert_to_openai_tool

from jw import paths
from jw.research.authoring.empirical_plan import (
    COMPACT_EMPIRICAL_PLAN_SCHEMA,
    EMPIRICAL_ROUTE_STAGES,
)
from jw.tools import research_planner as planner
from jw.workspaces import ensure_thread_workspace


def _synthetic_plan():
    return {
        "scope": {
            "objective": "Validate a synthetic authoring contract without scientific conclusions.",
            "population_or_period": "Only a two-row synthetic technical fixture.",
            "boundaries": ["No external data or scientific execution."],
            "non_goals": ["No empirical claim or real candidate hypothesis."],
        },
        "subquestions": [
            {
                "question": "Are synthetic declared fields consistently documented?",
                "purpose": "Exercise the authoring contract.",
                "completion_evidence": "A field inventory with explicit unresolved semantics.",
            }
        ],
        "evidence_gaps": [
            "Synthetic units require confirmation; no real evidence is claimed."
        ],
        "datasets": [
            {
                "name": "synthetic-two-row-observations",
                "purpose": "Exercise field semantics without acquiring data.",
                "required_variables": ["synthetic_time", "synthetic_value"],
                "time_coverage_needed": "Two declared fixture rows only.",
                "cadence_needed": "Synthetic fixed interval.",
                "quality_requirements": [
                    "Document the synthetic source and unresolved unit."
                ],
            }
        ],
        "stage_methods": dict.fromkeys(
            EMPIRICAL_ROUTE_STAGES,
            "Inspect only authorized synthetic artifacts; stop if necessary inputs are missing.",
        ),
        "evaluation_focus": ["Preserve declared input and artifact traceability."],
    }


@pytest.fixture
def bound(tmp_path, monkeypatch):
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(tmp_path / "bindings"))
    monkeypatch.setattr(paths, "WORKSPACE_ROOT", tmp_path)
    binding = ensure_thread_workspace("compact-contract-fixture", tmp_path)
    config = {
        "configurable": {
            "thread_id": binding.thread_id,
            "workspace_thread_id": binding.thread_id,
        }
    }
    brief = json.loads(
        planner.research_planner_get_brief.invoke(
            {
                "request_input": "Validate an offline synthetic tool authoring contract; no scientific execution."
            },
            config=config,
        )
    )
    return Path(binding.workspace), config, brief


@pytest.mark.parametrize("encoding", ["object", "legacy-string"])
def test_actual_tool_accepts_synthetic_input_then_freezes_canonical_plan(
    bound, encoding
):
    root, config, brief = bound
    original = _synthetic_plan()
    submitted = original if encoding == "object" else json.dumps(original)
    result = json.loads(
        planner.research_planner_create_empirical_plan.invoke(
            {"compact_plan_json": submitted, "request_sha256": brief["request_sha256"]},
            config=config,
        )
    )
    assert result["status"] == "plan_ready", result
    frozen = json.loads(
        planner.research_planner_freeze_plan.invoke(
            {"request_sha256": brief["request_sha256"]}, config=config
        )
    )
    assert frozen["status"] == "frozen_and_valid", frozen
    files = list((root / "planner/runs").glob("*/research_plan.json"))
    assert len(files) == 1
    plan = json.loads(files[0].read_text(encoding="utf-8"))
    assert plan["scope"] == original["scope"]
    assert [
        {key: item[key] for key in ["question", "purpose", "completion_evidence"]}
        for item in plan["research_subquestions"]
    ] == original["subquestions"]
    assert [item["method_outline"] for item in plan["research_route"]] == list(
        original["stage_methods"].values()
    )
    assert (
        original == _synthetic_plan()
    )  # runtime never strips or mutates caller content


def test_brief_and_real_model_facing_schema_share_complete_closed_contract(bound):
    _, _, brief = bound
    contract = brief["brief"]["empirical_plan_authoring_contract"]
    model_schema = (
        planner.research_planner_create_empirical_plan.args_schema.model_json_schema()
    )
    object_schema = model_schema["properties"]["compact_plan_json"]["anyOf"][0]
    assert object_schema == contract["schema"] == COMPACT_EMPIRICAL_PLAN_SCHEMA
    wire_schema = convert_to_openai_tool(
        planner.research_planner_create_empirical_plan
    )["function"]["parameters"]["properties"]["compact_plan_json"]["anyOf"][0]
    assert (
        wire_schema == object_schema
    )  # subset-model conversion must retain nested constraints
    Draft202012Validator.check_schema(object_schema)
    assert Draft202012Validator(object_schema).is_valid(_synthetic_plan())
    for location in [
        object_schema,
        object_schema["properties"]["scope"],
        object_schema["properties"]["subquestions"]["items"],
        object_schema["properties"]["datasets"]["items"],
        object_schema["properties"]["stage_methods"],
    ]:
        assert location["additionalProperties"] is False
    assert "id" in contract["host_owned_fields"]["subquestions"]
    assert "stop_rules" in contract["host_owned_fields"]["top_level"]


@pytest.mark.parametrize("encoding", ["object", "legacy-string"])
def test_one_submission_reports_top_and_nested_failures_without_activating_draft(
    bound, encoding
):
    root, config, brief = bound
    state = root / "planner/working_state.json"
    before = state.read_bytes()
    bad = _synthetic_plan()
    bad["stop_rules"] = ["Not author-owned"]
    bad["transitions"] = []
    bad["subquestions"][0].update(id="Q1", depends_on=[])
    del bad["scope"]["objective"]
    bad["datasets"][0]["quality_requirements"] = "Wrong array type"
    del bad["stage_methods"]["data"]
    bad["stage_methods"]["unregistered_stage"] = "Unknown"
    bad["evaluation_focus"] = [""]
    argument = bad if encoding == "object" else json.dumps(bad)
    result = json.loads(
        planner.research_planner_create_empirical_plan.invoke(
            {"compact_plan_json": argument, "request_sha256": brief["request_sha256"]},
            config=config,
        )
    )
    assert result["status"] == "error"
    assert result["error_code"] == "COMPACT_PLAN_SCHEMA_INVALID"
    locations = {issue["path"] for issue in result["validation_issues"]}
    assert {
        "$",
        "$.scope",
        "$.subquestions[0]",
        "$.datasets[0].quality_requirements",
        "$.stage_methods",
        "$.evaluation_focus[0]",
    } <= locations
    assert len(result["validation_issues"]) >= 7
    assert state.read_bytes() == before
    assert not list((root / "planner/runs").glob("*/research_plan.json"))
    attempts = [
        json.loads(line)
        for line in (root / "planner/compact_attempts.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(attempts) == 1
    assert (
        attempts[0]["status"] == "error"
    )  # budget-facing failure semantics are retained
    assert bad["subquestions"][0]["id"] == "Q1"  # no silent field dropping


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("scope", {"objective": "Only one field"}),
        (
            "subquestions",
            [
                {
                    "question": "Valid question",
                    "purpose": "purpose",
                    "completion_evidence": "evidence",
                    "id": "Q1",
                    "depends_on": [],
                }
            ],
        ),
        ("evidence_gaps", [" "]),
        ("datasets", [{"name": "Missing required semantic fields"}]),
        ("stage_methods", {"data": "Only one required stage"}),
        (
            "evaluation_focus",
            ["a", "b", "c", "d", "e", "silently ignored before repair"],
        ),
    ],
)
def test_actual_tool_rejects_invalid_semantic_structure_before_full_expansion(
    bound, field, bad_value
):
    _, config, brief = bound
    bad = _synthetic_plan()
    bad[field] = deepcopy(bad_value)
    result = json.loads(
        planner.research_planner_create_empirical_plan.invoke(
            {"compact_plan_json": bad, "request_sha256": brief["request_sha256"]},
            config=config,
        )
    )
    assert result["status"] == "error"
    assert result["validation_issues"]
    assert any(
        issue["path"].startswith(f"$.{field}") for issue in result["validation_issues"]
    )


def test_bad_submission_cannot_replace_previously_validated_draft(bound):
    root, config, brief = bound
    result = json.loads(
        planner.research_planner_create_empirical_plan.invoke(
            {
                "compact_plan_json": _synthetic_plan(),
                "request_sha256": brief["request_sha256"],
            },
            config=config,
        )
    )
    assert result["status"] == "plan_ready"
    state = root / "planner/working_state.json"
    before = state.read_bytes()
    bad = _synthetic_plan()
    bad["datasets"][0]["source_kind"] = "selected"
    result = json.loads(
        planner.research_planner_create_empirical_plan.invoke(
            {"compact_plan_json": bad, "request_sha256": brief["request_sha256"]},
            config=config,
        )
    )
    assert result["status"] == "error"
    assert state.read_bytes() == before


def test_native_search_keeps_the_existing_four_argument_call(bound, monkeypatch):
    _, config, brief = bound
    calls = []

    def search(query, limit, from_year, to_year):
        calls.append((query, limit, from_year, to_year))
        return {"status": "unavailable", "results": []}

    monkeypatch.setattr(planner, "search_scholarly_literature", search)
    result = json.loads(
        planner.research_planner_search_literature.invoke(
            {"query": "synthetic query", "request_sha256": brief["request_sha256"]},
            config=config,
        )
    )
    assert result["status"] == "unavailable"
    assert calls == [("synthetic query", 5, None, None)]
