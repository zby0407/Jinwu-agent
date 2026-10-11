"""Verbatim evidence regressions; synthetic fixtures, no model or science run."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from jw import paths
from jw.tools import scientific_hypothesis as hypothesis_tools
from jw.workspaces import ensure_thread_workspace, workspace_context_key
from scientific_hypothesis import harness
from scientific_hypothesis.contracts import (
    ContractError,
    validate_evidence_bind,
    validate_hypothesis_request,
)


def material(notes: str, *, kind: str = "data_feature", **overrides) -> dict:
    value = {
        "id": "source_one",
        "material_kind": kind,
        "title": "Synthetic provenance fixture",
        "locator": "inputs/source.txt",
        "content_notes": notes,
        "experiment_summary": None,
    }
    value.update(overrides)
    return value


def request_for(*materials: dict) -> dict:
    return validate_hypothesis_request(
        {
            "schema_version": "scientific-hypothesis-request-v1",
            "task_name": "verbatim-contract-fixture",
            "research_question": "Which synthetic observations constrain the claim?",
            "upstream_materials": list(materials),
            "prior_hypotheses": [],
            "max_candidates": 2,
        }
    )


def evidence_for(
    excerpt: str, *, kind: str = "upstream", source_id: str = "source_one"
) -> dict:
    return validate_evidence_bind(
        {
            "evidence_id": "evidence_one",
            "evidence_kind": kind,
            "material_id": source_id,
            "excerpt": excerpt,
            "verified_support": True,
            "role": "limits",
        }
    )


@pytest.mark.parametrize(
    ("notes", "excerpt"),
    [
        pytest.param("The residual r=0.95134.", "r=0.95134", id="plain"),
        pytest.param(
            '{"decision":"accept_with_limits","limits":["单区，不作因果"]}',
            '"decision":"accept_with_limits","limits":["单区，不作因果"]',
            id="literal-json-quotes",
        ),
        pytest.param(
            r"Input C:\solar\observations.csv was retained.",
            r"C:\solar\observations.csv",
            id="literal-backslashes",
        ),
        pytest.param(
            r'JSON text {"path":"C:\\solar\\observations.csv"}',
            r'"path":"C:\\solar\\observations.csv"',
            id="backslashes-inside-original-json",
        ),
        pytest.param(
            "Exact Unicode: 中文 α β café 🌞.",
            "中文 α β café 🌞",
            id="unicode-literal",
        ),
        pytest.param(
            'The line says "bounded".\r\n  Another\tline remains.',
            '"bounded".\n Another line',
            id="existing-whitespace-rule",
        ),
        pytest.param(
            r"Literal two-character escapes \n and \t remain source text.",
            r"escapes \n and \t remain",
            id="literal-escape-text",
        ),
    ],
)
def test_verified_excerpt_matches_declared_original_text(notes, excerpt) -> None:
    request = request_for(material(notes))
    entry = evidence_for(excerpt)
    original_request, original_entry = deepcopy(request), deepcopy(entry)

    harness.validate_evidence_provenance(request, entry)

    assert request == original_request
    assert entry == original_entry


@pytest.mark.parametrize(
    "field", ["name", "value_text", "definition", "uncertainty_notes", "outcome"]
)
def test_structured_experiment_text_remains_a_valid_source(field) -> None:
    summary = {
        "execution_completed": True,
        "outcome": "completed",
        "metrics": [
            {
                "name": "synthetic metric",
                "value_text": 'The retained value is "0.95134".',
                "definition": "A synthetic fixture, not a scientific result.",
            }
        ],
        "uncertainty_notes": r"Unknown covariance; path C:\error\source.txt.",
        "record_sha256": None,
    }
    request = request_for(
        material(
            "The experiment completed; details are in its structured fields.",
            kind="experiment_result",
            experiment_summary=summary,
        )
    )
    excerpt = (
        summary["metrics"][0][field]
        if field in {"name", "value_text", "definition"}
        else summary[field]
    )
    harness.validate_evidence_provenance(
        request, evidence_for(excerpt, kind="experiment")
    )


@pytest.mark.parametrize(
    ("source", "excerpt"),
    [
        pytest.param("r=0.95134", "r=0.91534", id="fabricated-number"),
        pytest.param("r=-0.174", "r=+0.174", id="fabricated-sign"),
        pytest.param(
            "not a causal result", "a causal result is established", id="paraphrase"
        ),
        pytest.param("Sensitive", "sensitive", id="case-is-literal"),
        pytest.param('Quoted "bounded".', "Quoted “bounded”.", id="altered-quotes"),
        pytest.param(
            "α is a Greek letter", "a is a Greek letter", id="unicode-lookalike"
        ),
        pytest.param("café", "cafe\u0301", id="no-unicode-normalization"),
        pytest.param("alpha intervening text omega", "alpha omega", id="noncontiguous"),
        pytest.param(r"C:\solar\data", "C:solardata", id="removed-backslashes"),
        pytest.param(
            'Quoted "bounded".',
            r"Quoted \"bounded\".",
            id="serialization-is-not-source-text",
        ),
        pytest.param(r"alpha\nomega", "alpha\nomega", id="no-escape-decoding"),
    ],
)
def test_fabricated_or_rewritten_excerpt_is_rejected(source, excerpt) -> None:
    with pytest.raises(ContractError):
        harness.validate_evidence_provenance(
            request_for(material(source)), evidence_for(excerpt)
        )


def test_excerpt_cannot_bridge_different_fields() -> None:
    request = request_for(material("omega", title="alpha"))
    with pytest.raises(ContractError):
        harness.validate_evidence_provenance(request, evidence_for("alpha omega"))


def test_excerpt_cannot_come_from_another_declared_material() -> None:
    request = request_for(
        material("The first source has no selected result."),
        material("Only the other source says this.", id="source_two"),
    )
    with pytest.raises(ContractError):
        harness.validate_evidence_provenance(
            request, evidence_for("Only the other source says this.")
        )


@pytest.mark.parametrize("excerpt", ['"content_notes":', '"experiment_summary": null'])
def test_container_syntax_is_not_declared_source_text(excerpt) -> None:
    with pytest.raises(ContractError):
        harness.validate_evidence_provenance(
            request_for(material("A plain source paragraph.")), evidence_for(excerpt)
        )


@pytest.mark.parametrize(
    "excerpt",
    ["source_one", "Synthetic provenance fixture", "inputs/source.txt", "data_feature"],
    ids=["id", "title", "locator", "material-kind"],
)
def test_material_metadata_is_not_evidence_text(excerpt) -> None:
    with pytest.raises(ContractError):
        harness.validate_evidence_provenance(
            request_for(material("Only this source paragraph is evidence text.")),
            evidence_for(excerpt),
        )


@pytest.mark.parametrize(
    "excerpt", ["a" * 64, "true"], ids=["record-sha256", "boolean"]
)
def test_summary_metadata_is_not_evidence_text(excerpt) -> None:
    summary = {
        "execution_completed": True,
        "outcome": "completed",
        "metrics": [
            {"name": "alpha", "value_text": "omega", "definition": "synthetic fixture"}
        ],
        "uncertainty_notes": "Covariance has not been estimated.",
        "record_sha256": "a" * 64,
    }
    request = request_for(
        material(
            "Source text only.", kind="experiment_result", experiment_summary=summary
        )
    )
    with pytest.raises(ContractError):
        harness.validate_evidence_provenance(
            request, evidence_for(excerpt, kind="experiment")
        )


def test_excerpt_cannot_bridge_experiment_string_leaves() -> None:
    summary = {
        "execution_completed": True,
        "outcome": "completed",
        "metrics": [
            {"name": "alpha", "value_text": "omega", "definition": "synthetic fixture"}
        ],
        "uncertainty_notes": "Covariance has not been estimated.",
        "record_sha256": None,
    }
    request = request_for(
        material(
            "Source text only.", kind="experiment_result", experiment_summary=summary
        )
    )
    with pytest.raises(ContractError):
        harness.validate_evidence_provenance(
            request, evidence_for("alpha omega", kind="experiment")
        )


def test_json_is_evidence_only_when_literal_in_content_notes() -> None:
    notes = r'{"content_notes":"literal \\n", "quoted":"\\\"bounded\\\""}'
    request = request_for(material(notes))
    harness.validate_evidence_provenance(request, evidence_for('"content_notes":'))
    with pytest.raises(ContractError):
        harness.validate_evidence_provenance(request, evidence_for('"bounded"'))


@pytest.mark.parametrize(
    ("material_kind", "allowed_kind", "wrong_kind"),
    [
        ("data_feature", "upstream", "experiment"),
        ("research_plan", "upstream", "experiment"),
        ("literature_note", "literature", "upstream"),
        ("user_material", "user", "experiment"),
    ],
)
def test_material_kind_is_not_reclassified(
    material_kind, allowed_kind, wrong_kind
) -> None:
    request = request_for(material("The literal source paragraph.", kind=material_kind))
    harness.validate_evidence_provenance(
        request, evidence_for("literal source paragraph", kind=allowed_kind)
    )
    with pytest.raises(ContractError, match="evidence_kind"):
        harness.validate_evidence_provenance(
            request, evidence_for("literal source paragraph", kind=wrong_kind)
        )


@pytest.fixture
def bound_workspace(tmp_path, monkeypatch):
    base = tmp_path / "workspace"
    base.mkdir()
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(tmp_path / "registry"))
    monkeypatch.setattr(paths, "WORKSPACE_ROOT", base)
    thread = "hypothesis-verbatim-contract-fixture"
    binding = ensure_thread_workspace(thread, base)
    config = {"configurable": {"thread_id": thread, "workspace_thread_id": thread}}
    context = workspace_context_key(config)
    hypothesis_tools._STATES.pop(context, None)
    try:
        yield Path(binding.workspace), config
    finally:
        hypothesis_tools._STATES.pop(context, None)


def bind_request_file(workspace, config, notes):
    frozen = workspace / "inputs/frozen_request.json"
    frozen.parent.mkdir(parents=True, exist_ok=True)
    frozen.write_text(
        json.dumps(request_for(material(notes)), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    original_bytes = frozen.read_bytes()
    outcome = json.loads(
        hypothesis_tools.scientific_hypothesis_bind_request.invoke(
            {"request_input": "@inputs/frozen_request.json"}, config=config
        )
    )
    assert outcome["binding_status"] == "bound"
    return frozen, original_bytes


@pytest.mark.parametrize("role", ["supports", "opposes", "limits"])
@pytest.mark.parametrize(
    "notes",
    [
        pytest.param("Synthetic r=0.95134; no mechanism conclusion.", id="plain"),
        pytest.param(
            '{"r":0.95134,"decision":"accept_with_limits","note":"中文 α"}',
            id="json-original",
        ),
    ],
)
def test_real_verified_tool_bind_persists_without_changing_caller_or_source(
    bound_workspace, role, notes
) -> None:
    workspace, config = bound_workspace
    frozen, original_bytes = bind_request_file(workspace, config, notes)
    state_before = hypothesis_tools._state(config)
    original_request = deepcopy(state_before.request)
    original_draft = deepcopy(state_before.latest_draft)
    original_checkpoint = deepcopy(state_before.validated_response)
    args = evidence_for(notes)
    args["role"] = role
    original_args = deepcopy(args)

    outcome = json.loads(
        hypothesis_tools.scientific_hypothesis_bind_evidence.invoke(args, config=config)
    )

    assert outcome["status"] == "bound"
    assert outcome["state_persistence"] == "workspace"
    assert args == original_args
    assert frozen.read_bytes() == original_bytes
    state_file = workspace / "work/scientific_hypothesis_state.json"
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved["request"] == original_request
    assert saved["latest_draft"] == original_draft
    assert saved["checkpoint"] == original_checkpoint
    assert saved["portfolio_ranking"] is None
    assert saved["evidence_register"] == [original_args]
    assert saved["evidence_register"][0]["verified_support"] is True
    assert saved["evidence_register"][0]["role"] == role
    assert saved["evidence_register"][0]["evidence_kind"] == "upstream"
    assert saved["evidence_register"][0]["excerpt"] == notes
    assert saved["checkpoint_attempts"] == 0
    # Force a fresh native state read; no ranking/checkpoint lifecycle is added.
    hypothesis_tools._STATES.pop(workspace_context_key(config))
    restored = hypothesis_tools._state(config)
    assert restored.evidence_register.all() == [original_args]
    assert restored.request == original_request
    assert restored.latest_draft == original_draft
    assert restored.validated_response == original_checkpoint
    assert frozen.read_bytes() == original_bytes


@pytest.mark.parametrize(
    "invalid_args",
    [
        {"excerpt": "Synthetic r=0.91534; no mechanism conclusion."},
        {"evidence_kind": "experiment"},
        {"material_id": "undeclared_source"},
        {"verified_support": False},
    ],
    ids=["fabricated-value", "wrong-kind", "undeclared", "unverified-support"],
)
def test_real_tool_rejection_keeps_register_and_persisted_state_unchanged(
    bound_workspace, invalid_args
) -> None:
    workspace, config = bound_workspace
    notes = "Synthetic r=0.95134; no mechanism conclusion."
    frozen, original_bytes = bind_request_file(workspace, config, notes)
    state_file = workspace / "work/scientific_hypothesis_state.json"
    state_bytes = state_file.read_bytes()
    args = evidence_for(notes)
    args.update(invalid_args)
    outcome = json.loads(
        hypothesis_tools.scientific_hypothesis_bind_evidence.invoke(args, config=config)
    )

    assert outcome["status"] == "needs_revision"
    assert hypothesis_tools._state(config).evidence_register.all() == []
    assert state_file.read_bytes() == state_bytes
    assert frozen.read_bytes() == original_bytes


def test_imports_resolve_to_this_worktree() -> None:
    root = Path(__file__).resolve().parents[1]
    assert Path(harness.__file__).resolve().is_relative_to(root / "src")
    assert Path(hypothesis_tools.__file__).resolve().is_relative_to(root / "jw")
