"""Offline tests for the real quality tool wire schema and persistence boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from langchain_core.utils.function_calling import convert_to_openai_tool

from jw import paths
from jw.research.authoring.analysis_claim import (
    analysis_claim_authoring_contract,
    analysis_claim_schema,
    analysis_claim_structure_issues,
    analysis_claim_tool_arguments_schema,
)
from jw.tools.research_quality import (
    research_quality_get_analysis_claim,
    research_quality_record_analysis_claim,
)
from jw.workspaces import ensure_thread_workspace
from research_quality.contracts import ContractError, validate_analysis_claim_contract


def _synthetic_contract():
    # An engineering fixture, never a B3 scientific input or research result.
    result = {
        name: f"Synthetic unexecuted method field {name}."
        for name, schema in analysis_claim_schema()["properties"].items()
        if schema.get("type") == "string"
    }
    result.update(
        schema_version="analysis-claim-contract-v1",
        independent_sample_count=None,
        measurement_kind="not_assessed",
        outcome_branches=[
            {
                "outcome": "Synthetic outcome A",
                "claim_update": "Synthetic conditional update A",
            },
            {
                "outcome": "Synthetic outcome B",
                "claim_update": "Synthetic conditional update B",
            },
        ],
    )
    return result


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(tmp_path / "bindings"))
    monkeypatch.setattr(paths, "WORKSPACE_ROOT", tmp_path)
    binding = ensure_thread_workspace(
        "quality-schema-fixture",
        tmp_path,
        project_id="quality-technical-project",
        first_request="Offline technical schema test",
    )
    config = {
        "configurable": {
            "thread_id": binding.thread_id,
            "workspace_thread_id": binding.thread_id,
            "project_id": binding.project_id,
        }
    }
    return Path(binding.workspace), config


def test_real_wire_schema_exposes_all_native_fields_and_nested_branches():
    wire = convert_to_openai_tool(research_quality_record_analysis_claim)["function"][
        "parameters"
    ]
    assert wire == analysis_claim_tool_arguments_schema()
    contract = wire["properties"]["contract"]["anyOf"][0]
    assert len(contract["required"]) == 20
    assert set(contract["required"]) == set(_synthetic_contract())
    assert "measurement_regime" in contract["required"]
    assert contract["additionalProperties"] is False
    branch = contract["properties"]["outcome_branches"]["items"]
    assert branch["additionalProperties"] is False
    assert set(branch["required"]) == {"outcome", "claim_update"}
    assert "config" not in wire["properties"]


def test_missing_contract_is_discoverable_without_persisting_science(workspace):
    root, config = workspace
    result = json.loads(
        research_quality_get_analysis_claim.invoke({"stage": "planning"}, config=config)
    )
    assert result["status"] == "missing"
    assert result["authoring_contract"] == analysis_claim_authoring_contract()
    assert "contract" not in result
    assert not (root / "work/research_quality/planning.analysis_claim.json").exists()


@pytest.mark.parametrize("legacy_string", [False, True])
def test_object_and_legacy_string_persist_identical_native_contract(
    workspace, legacy_string
):
    root, config = workspace
    contract = _synthetic_contract()
    result = json.loads(
        research_quality_record_analysis_claim.invoke(
            {
                "stage": "planning",
                "contract": json.dumps(contract) if legacy_string else contract,
            },
            config=config,
        )
    )
    assert result["status"] == "recorded", result
    saved = json.loads((root / result["source_ref"]).read_text(encoding="utf-8"))
    assert saved == validate_analysis_claim_contract(contract)
    assert saved == contract
    assert not (root / "research_review/run_state.json").exists()


def test_failure_aggregates_omissions_and_returns_schema_without_writing(workspace):
    root, config = workspace
    partial = _synthetic_contract()
    del partial["measurement_regime"]
    del partial["observation_cutoff"]
    partial["outcome_branches"][0]["unexpected"] = "invalid fixture"
    result = json.loads(
        research_quality_record_analysis_claim.invoke(
            {
                "stage": "planning",
                "contract": partial,
            },
            config=config,
        )
    )
    assert result["ok"] is False
    messages = " ".join(row["message"] for row in result["structure_issues"])
    assert all(
        field in messages
        for field in ("measurement_regime", "observation_cutoff", "unexpected")
    )
    assert result["authoring_contract"] == analysis_claim_authoring_contract()
    assert not (root / "work/research_quality/planning.analysis_claim.json").exists()


def test_discoverable_schema_and_native_validator_retain_prediction_gate(workspace):
    root, config = workspace
    contract = _synthetic_contract()
    contract.update(
        estimand="Synthetic next cycle prediction",
        validation_design="training correlation",
    )
    Draft202012Validator(analysis_claim_schema()).validate(contract)
    result = json.loads(
        research_quality_record_analysis_claim.invoke(
            {
                "stage": "planning",
                "contract": contract,
            },
            config=config,
        )
    )
    assert result["status"] == "error"
    assert "out-of-sample" in result["message"]
    assert result["structure_issues"] == []
    assert not (root / "work/research_quality/planning.analysis_claim.json").exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("independent_sample_count", True),
        ("independent_sample_count", -1),
        ("measurement_kind", "invented"),
        ("schema_version", "invented"),
        ("measurement_regime", "   "),
        ("outcome_branches", [{"outcome": "only one", "claim_update": "fixture"}]),
    ],
)
def test_schema_and_native_validator_reject_same_structural_cases(field, value):
    contract = _synthetic_contract()
    contract[field] = value
    assert analysis_claim_structure_issues(contract)
    with pytest.raises(ContractError):
        validate_analysis_claim_contract(contract)
