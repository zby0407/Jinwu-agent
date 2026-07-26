from __future__ import annotations

import json

from jw.tools import scientific_hypothesis as tools
from scientific_hypothesis.contracts import canonical_json_sha256
from scientific_hypothesis.ranking import RUBRIC_KEYS


def test_bundled_hypothesis_input_fallback_is_narrow(tmp_path, monkeypatch):
    project = tmp_path / "project"
    bundled = project / "hypothesis" / "inputs"
    bundled.mkdir(parents=True)
    request = bundled / "demo.json"
    request.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(tools, "_PROJECT_ROOT", project)
    monkeypatch.setattr(
        tools,
        "resolve_scoped_path",
        lambda *_args, **_kwargs: tmp_path / "missing.json",
    )

    resolved = tools._resolve_request_path("hypothesis/inputs/demo.json", None)

    assert resolved == request.resolve()


def test_bundled_fallback_rejects_non_input_paths(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / "hypothesis").mkdir(parents=True)
    secret = project / "hypothesis" / "private.json"
    secret.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(tools, "_PROJECT_ROOT", project)
    monkeypatch.setattr(
        tools,
        "resolve_scoped_path",
        lambda *_args, **_kwargs: tmp_path / "missing.json",
    )

    try:
        tools._resolve_request_path("hypothesis/private.json", None)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("fallback must expose only hypothesis/inputs")


def test_inspect_upstream_uses_scoped_path(tmp_path, monkeypatch):
    run_dir = tmp_path / "runs" / "verified-run"
    run_dir.mkdir(parents=True)
    captured = {}
    monkeypatch.setattr(
        tools,
        "resolve_scoped_path",
        lambda value, config, *, allow_project: run_dir,
    )
    monkeypatch.setattr(
        tools,
        "workspace_root_from_config",
        lambda _config: tmp_path,
    )

    def fake_inspect(payload, project_root):
        captured["payload"] = payload
        captured["project_root"] = project_root
        return {"status": "verified", "run_id": "verified-run"}

    monkeypatch.setattr(tools, "inspect_experiment_run", fake_inspect)

    outcome = json.loads(
        tools.scientific_hypothesis_inspect_upstream.invoke(
            {"run_path": "runs/verified-run"}
        )
    )

    assert outcome == {"status": "verified", "run_id": "verified-run"}
    assert captured == {
        "payload": {"run_path": str(run_dir)},
        "project_root": tmp_path,
    }


def test_rank_is_cached_and_forwarded_to_freeze(tmp_path, monkeypatch):
    tools._STATES.clear()
    config = {"configurable": {"thread_id": "hypothesis-rank-test"}}
    state = tools._state(config)
    state.request = {"task_name": "demo", "research_question": "question"}
    state.request_sha256 = "a" * 64
    state.validated_response = {
        "response_kind": "hypotheses_ready",
        "candidates": [{"id": "candidate-a"}],
    }
    state.preflight_response_sha256 = canonical_json_sha256(state.validated_response)
    ranking = {
        "schema_version": "scientific-hypothesis-ranking-v1",
        "rubric": [{"key": key, "label": key} for key in RUBRIC_KEYS],
        "weights": {key: 1 for key in RUBRIC_KEYS},
        "ranked": [{"candidate_id": "candidate-a", "rank": 1}],
        "pairwise_judgments": [],
    }
    normalized_ranking = {
        key: value for key, value in ranking.items() if key != "rubric"
    }

    def fake_preflight(
        request,
        response,
        ranking_payload,
        register,
        *,
        include_validated_ranking,
    ):
        assert request is state.request
        assert response is state.validated_response
        assert ranking_payload == ranking
        assert include_validated_ranking is True
        return {
            "status": "ranking_ready",
            # The core validator returns the normalized portfolio shape, which
            # intentionally omits the request-only rubric declaration.
            "_validated_ranking": normalized_ranking,
        }

    captured = {}

    def fake_freeze(
        request,
        response,
        register,
        *,
        runs_root,
        ranking_payload,
        path_root,
    ):
        captured["ranking"] = ranking_payload
        captured["runs_root"] = runs_root
        captured["path_root"] = path_root
        return {"status": "frozen_and_valid"}

    monkeypatch.setattr(tools, "preflight_hypothesis_ranking", fake_preflight)
    monkeypatch.setattr(tools, "freeze_hypothesis_portfolio", fake_freeze)
    monkeypatch.setattr(tools, "workspace_root_from_config", lambda _config: tmp_path)

    rank_outcome = json.loads(
        tools.scientific_hypothesis_rank.invoke(
            {"ranking_json": json.dumps(ranking)},
            config=config,
        )
    )
    freeze_outcome = json.loads(
        tools.scientific_hypothesis_freeze.invoke({}, config=config)
    )

    assert rank_outcome["status"] == "ranking_ready"
    assert freeze_outcome["status"] == "frozen_and_valid"
    assert captured == {
        "ranking": ranking,
        "runs_root": tmp_path / "hypothesis" / "runs",
        "path_root": tmp_path,
    }


def test_freeze_requires_successful_ranking(monkeypatch):
    tools._STATES.clear()
    config = {"configurable": {"thread_id": "hypothesis-rank-required"}}
    state = tools._state(config)
    state.request = {"task_name": "demo", "research_question": "question"}
    state.validated_response = {
        "response_kind": "hypotheses_ready",
        "candidates": [{"id": "candidate-a"}],
    }
    state.preflight_response_sha256 = canonical_json_sha256(state.validated_response)

    outcome = json.loads(tools.scientific_hypothesis_freeze.invoke({}, config=config))

    assert outcome["status"] == "needs_revision"
    assert "scientific_hypothesis_rank" in outcome["validation_error"]


def test_bind_brief_exposes_complete_ranking_contract():
    tools._STATES.clear()

    brief = json.loads(
        tools.scientific_hypothesis_bind_request.invoke(
            {"request_input": "形成可检验的竞争假设"}
        )
    )
    contract = brief["ranking_contract"]

    assert contract["schema_version"] == "scientific-hypothesis-ranking-v1"
    assert [row["key"] for row in contract["rubric"]] == list(RUBRIC_KEYS)
    assert set(contract["ranked_item_shape"]) == {
        "candidate_id",
        "rank",
        "rationale",
        "key_evidence_ids",
        "dimension_grades",
        "weakest_dimensions",
        "confidence_note",
    }
    assert contract["grade_values"] == ["strong", "moderate", "weak"]


def test_failed_response_resubmission_invalidates_cached_state(monkeypatch):
    tools._STATES.clear()
    config = {"configurable": {"thread_id": "hypothesis-invalid-resubmit"}}
    state = tools._state(config)
    state.request = {"task_name": "demo", "research_question": "question"}
    state.validated_response = {"response_kind": "hypotheses_ready"}
    state.preflight_response_sha256 = canonical_json_sha256(state.validated_response)
    state.validated_ranking = {"ranked": []}
    state.preflight_ranking_sha256 = canonical_json_sha256(state.validated_ranking)

    outcome = json.loads(
        tools.scientific_hypothesis_validate_response.invoke(
            {"response_json": "not-json"},
            config=config,
        )
    )

    assert outcome["status"] == "needs_revision"
    assert state.validated_response is None
    assert state.preflight_response_sha256 is None
    assert state.validated_ranking is None
    assert state.preflight_ranking_sha256 is None


def test_successful_evidence_bind_invalidates_cached_state(monkeypatch):
    tools._STATES.clear()
    config = {"configurable": {"thread_id": "hypothesis-evidence-update"}}
    state = tools._state(config)
    state.request = {"task_name": "demo", "research_question": "question"}
    state.validated_response = {"response_kind": "hypotheses_ready"}
    state.preflight_response_sha256 = canonical_json_sha256(state.validated_response)
    state.validated_ranking = {"ranked": []}
    state.preflight_ranking_sha256 = canonical_json_sha256(state.validated_ranking)
    monkeypatch.setattr(
        state.evidence_register,
        "bind",
        lambda _payload: {
            "status": "bound",
            "evidence_id": "ev-new",
            "role": "gap",
            "bound_evidence_count": 1,
        },
    )

    outcome = json.loads(
        tools.scientific_hypothesis_bind_evidence.invoke(
            {
                "evidence_id": "ev-new",
                "evidence_kind": "literature",
                "material_id": "mat-new",
                "excerpt": "未经核验的材料",
                "verified_support": False,
                "role": "gap",
            },
            config=config,
        )
    )

    assert outcome["status"] == "bound"
    assert state.validated_response is None
    assert state.preflight_response_sha256 is None
    assert state.validated_ranking is None
    assert state.preflight_ranking_sha256 is None
