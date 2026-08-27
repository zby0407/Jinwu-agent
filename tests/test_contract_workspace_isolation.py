from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from jw import paths
from jw.research_review import ResearchReviewStore
from jw.tools import automatic_experiment as experiment_tools
from jw.tools import research_planner as planner_tools
from jw.tools import scientific_hypothesis as hypothesis_tools
from jw.tools import solar_feature as data_tools
from jw.workspaces import ensure_thread_workspace
from scientific_hypothesis.contracts import canonical_json_sha256

QUESTION_A = (
    "固定随机种子 20260722，生成两组独立 Poisson 合成计数并比较均值差异；"
    "这是方法测试，不代表真实太阳活动结论。"
)
QUESTION_B = (
    "固定随机种子 7，生成两组独立正态合成样本并比较方差差异；这是第二个隔离方法测试。"
)


def _task_config(tmp_path: Path, monkeypatch, thread_id: str):
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(tmp_path / "registry"))
    monkeypatch.setattr(paths, "WORKSPACE_ROOT", tmp_path)
    binding = ensure_thread_workspace(thread_id, tmp_path)
    config = {
        "configurable": {
            "thread_id": thread_id,
            "workspace_thread_id": thread_id,
        }
    }
    return binding, config


def _complete_valid_planner(config):
    root = Path(__file__).resolve().parents[1]
    response = json.loads(
        (root / "research/planner/examples/definition_audit_response.json").read_text(
            encoding="utf-8"
        )
    )
    brief = json.loads(
        planner_tools.research_planner_get_brief.invoke(
            {"request_input": response["research_question"]}, config=config
        )
    )
    sha = brief["request_sha256"]
    for section_name in planner_tools._PLAN_SECTION_ORDER:
        result = json.loads(
            planner_tools.research_planner_update_draft.invoke(
                {
                    "section_name": section_name,
                    "section_json": json.dumps(
                        response["plan_content"][section_name], ensure_ascii=False
                    ),
                    "request_sha256": sha,
                },
                config=config,
            )
        )
        assert result["status"] == "draft_section_persisted"
    validated = json.loads(
        planner_tools.research_planner_validate_draft.invoke(
            {"request_sha256": sha}, config=config
        )
    )
    assert validated["status"] == "plan_ready"
    return response, sha


def test_planner_contract_state_and_freeze_root_are_task_scoped(
    tmp_path: Path, monkeypatch
) -> None:
    binding_a, config_a = _task_config(tmp_path, monkeypatch, "planner-a")
    _binding_b, config_b = _task_config(tmp_path, monkeypatch, "planner-b")

    brief_a = json.loads(
        planner_tools.research_planner_get_brief.invoke(
            {"request_input": QUESTION_A}, config=config_a
        )
    )
    json.loads(
        planner_tools.research_planner_get_brief.invoke(
            {"request_input": QUESTION_B}, config=config_b
        )
    )
    sha_a = brief_a["request_sha256"]
    assert (
        planner_tools._lookup_request("", config_a)["research_question"]
        != (planner_tools._lookup_request("", config_b)["research_question"])
    )

    planner_tools._VALIDATED_RESPONSES[("planner-a", sha_a)] = {"checked": True}
    captured: dict[str, Path] = {}

    def fake_freeze(request, response, *, runs_root, path_root):
        assert request["research_question"] == QUESTION_A
        assert response == {"checked": True}
        captured["runs_root"] = runs_root
        captured["path_root"] = path_root
        return {"status": "frozen_and_valid"}

    monkeypatch.setattr(planner_tools, "freeze_research_plan", fake_freeze)
    outcome = json.loads(
        planner_tools.research_planner_freeze_plan.invoke(
            {"request_sha256": sha_a}, config=config_a
        )
    )
    task_root = Path(binding_a.workspace)
    assert outcome["status"] == "frozen_and_valid"
    assert captured == {
        "runs_root": task_root / "planner" / "runs",
        "path_root": task_root,
    }


def test_planner_brief_uses_exact_task_bound_question(
    tmp_path: Path, monkeypatch
) -> None:
    binding, config = _task_config(tmp_path, monkeypatch, "planner-bound-brief")
    task_path = Path(binding.workspace) / "task.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task["research_question"] = QUESTION_A
    task_path.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")

    result = json.loads(
        planner_tools.research_planner_get_brief.invoke(
            {"request_input": f"{QUESTION_A} {QUESTION_A}"}, config=config
        )
    )

    assert result["canonical_request_reused"] is True
    assert result["brief"]["request"]["research_question"] == QUESTION_A
    state = json.loads(
        (Path(binding.workspace) / "planner" / "working_state.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["request"]["research_question"] == QUESTION_A


def test_solar_data_context_blocks_guessed_paths_and_is_idempotent(
    tmp_path: Path, monkeypatch
) -> None:
    binding, config = _task_config(tmp_path, monkeypatch, "data-context-missing")
    workspace = Path(binding.workspace)
    plan_path = workspace / "planner" / "runs" / "p1" / "research_plan.json"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(
        json.dumps(
            {
                "required_datasets": [
                    {
                        "id": "ds1",
                        "name": "SILSO monthly sunspot numbers",
                        "acquisition_status": "needs_confirmation",
                    }
                ],
                "research_route": [
                    {
                        "id": "rs1",
                        "stage": "data",
                        "produces_artifact_ids": ["art1"],
                        "prerequisite_step_ids": [],
                    },
                    {
                        "id": "rs2",
                        "stage": "hypothesis_generation",
                        "prerequisite_step_ids": ["rs1"],
                    },
                    {
                        "id": "rs3",
                        "stage": "experiment_design",
                        "prerequisite_step_ids": ["rs2"],
                    },
                    {
                        "id": "rs4",
                        "stage": "experiment_result",
                        "prerequisite_step_ids": ["rs3"],
                    },
                    {
                        "id": "rs5",
                        "stage": "hypothesis_update",
                        "prerequisite_step_ids": ["rs4"],
                    },
                ],
                "research_artifacts": [{"id": "art1", "producer_step_id": "rs1"}],
            }
        ),
        encoding="utf-8",
    )
    store = ResearchReviewStore(workspace, "data-context-missing")
    artifact = store.checkpoint_producer_result(
        stage="planning",
        producer="solar-planner",
        content="frozen plan",
        require_canonical_source=True,
    )
    verdict = store.submit_verdict(
        mode="planning",
        decision="accept",
        issues=[],
        accepted_claims=[artifact["claims"][0]["claim_id"]],
    )
    assert verdict["decision"] == "accept", verdict

    context = json.loads(data_tools.solar_data_open_context.invoke({}, config=config))
    repeated = json.loads(data_tools.solar_data_open_context.invoke({}, config=config))

    assert context["status"] == "input_missing", context
    assert context["must_stop"] is True
    assert context["eligible_inputs"] == []
    assert context["data_steps"][0]["id"] == "rs1"
    assert context["planned_outputs"][0]["id"] == "art1"
    assert context["path_policy"].startswith("Only eligible_inputs")
    assert (
        context["task_sha256"]
        == hashlib.sha256((workspace / "task.json").read_bytes()).hexdigest()
    )
    task = json.loads((workspace / "task.json").read_text(encoding="utf-8"))
    assert (
        context["research_question_sha256"]
        == hashlib.sha256(
            str(task.get("research_question") or "").encode("utf-8")
        ).hexdigest()
    )
    assert (
        context["input_manifest_sha256"]
        == hashlib.sha256((workspace / "input_manifest.json").read_bytes()).hexdigest()
    )
    assert repeated["receipt_ref"] == context["receipt_ref"]
    assert repeated["context_sha256"] == context["context_sha256"]
    assert (workspace / context["receipt_ref"]).is_file()
    assert (
        len(list((workspace / "receipts" / "datasets").glob("data-context-*.json")))
        == 1
    )


def _bind_silso_inputs(workspace: Path) -> dict[str, str]:
    inputs = workspace / "inputs"
    inputs.mkdir(exist_ok=True)
    monthly = inputs / "SN_m_tot.csv"
    smoothed = inputs / "SN_ms_tot_V2.0.csv"
    extrema = inputs / "TableCyclesMiMa.txt"
    monthly.write_text("1976;01;1976.042;1.0;0.1;1\n", encoding="ascii")

    extrema_rows = [
        "20 1964 10 1.0 1968 11 100.0",
        "21 1976 01 1.0 1979 01 100.0",
        "22 1986 01 1.0 1989 01 100.0",
        "23 1996 01 1.0 1999 01 100.0",
        "24 2006 01 1.0 2009 01 100.0",
        "25 2016 01 1.0 2019 01 100.0",
    ]
    extrema.write_text("\n".join(extrema_rows) + "\n", encoding="ascii")
    extrema_values = {
        (1976, 1): 1.0,
        (1979, 1): 100.0,
        (1986, 1): 1.0,
        (1989, 1): 100.0,
        (1996, 1): 1.0,
        (1999, 1): 100.0,
        (2006, 1): 1.0,
        (2009, 1): 100.0,
        (2016, 1): 1.0,
    }
    rows = []
    for year in range(1968, 2020):
        for month in range(1, 13):
            value = extrema_values.get((year, month), 50.0)
            rows.append(f"{year};{month:02d};0;{value:.1f};0;0")
    smoothed.write_text("\n".join(rows) + "\n", encoding="ascii")

    dataset_by_name = {
        monthly.name: "silso-monthly-total-v2",
        smoothed.name: "silso-monthly-smoothed-v2",
        extrema.name: "silso-cycle-extrema-v2",
    }
    manifest_path = workspace / "input_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["inputs"] = [
        {
            "path": f"/inputs/{path.name}",
            "role": "user_input",
            "dataset_id": dataset_by_name[path.name],
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in (monthly, smoothed, extrema)
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return {
        dataset_by_name[path.name]: f"/inputs/{path.name}"
        for path in (monthly, smoothed, extrema)
    }


def test_bounded_silso_context_needs_no_planning_and_is_idempotent(
    tmp_path: Path, monkeypatch
) -> None:
    binding, config = _task_config(tmp_path, monkeypatch, "bounded-silso-context")
    workspace = Path(binding.workspace)
    paths = _bind_silso_inputs(workspace)

    context = data_tools.open_bounded_solar_data_context(
        config, analysis_protocol="silso_cycle_reproduction_v1"
    )
    repeated = data_tools.open_bounded_solar_data_context(
        config, analysis_protocol="silso_cycle_reproduction_v1"
    )

    assert context["context_mode"] == "bounded_data"
    assert context["status"] == "inputs_available"
    assert context["must_stop"] is False
    assert {item["dataset_id"] for item in context["eligible_inputs"]} == set(paths)
    assert repeated["receipt_ref"] == context["receipt_ref"]
    assert repeated["context_sha256"] == context["context_sha256"]
    assert len(list((workspace / "receipts/datasets").glob("data-context-*.json"))) == 1


def test_bounded_silso_context_reports_missing_registered_products(
    tmp_path: Path, monkeypatch
) -> None:
    binding, config = _task_config(tmp_path, monkeypatch, "bounded-silso-missing")
    workspace = Path(binding.workspace)
    source = workspace / "inputs" / "SN_m_tot.csv"
    source.write_text("1976;01;1976.042;1.0;0.1;1\n", encoding="ascii")
    manifest_path = workspace / "input_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["inputs"] = [
        {
            "path": "/inputs/SN_m_tot.csv",
            "role": "user_input",
            "dataset_id": "silso-monthly-total-v2",
            "bytes": source.stat().st_size,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    context = data_tools.open_bounded_solar_data_context(
        config, analysis_protocol="silso_cycle_reproduction_v1"
    )

    assert context["status"] == "input_missing"
    assert context["must_stop"] is True
    assert context["missing_required_dataset_ids"] == [
        "silso-monthly-smoothed-v2",
        "silso-cycle-extrema-v2",
    ]


def test_reproduce_silso_cycle_extrema_writes_hash_bound_artifacts(
    tmp_path: Path, monkeypatch
) -> None:
    binding, config = _task_config(tmp_path, monkeypatch, "bounded-silso-tool")
    workspace = Path(binding.workspace)
    paths = _bind_silso_inputs(workspace)

    result = json.loads(
        data_tools.reproduce_silso_cycle_extrema.invoke(
            {
                "monthly_total_path": paths["silso-monthly-total-v2"],
                "smoothed_path": paths["silso-monthly-smoothed-v2"],
                "official_extrema_path": paths["silso-cycle-extrema-v2"],
                "cycles": "21-24",
            },
            config=config,
        )
    )

    assert result["status"] == "verified", result
    assert result["cycle_numbers"] == [21, 22, 23, 24]
    assert result["row_count"] == 4
    for ref in [*result["artifact_refs"], *result["receipt_refs"]]:
        assert (workspace / ref).is_file()
    payload = json.loads(
        (workspace / "work/solar_data/silso_cycle_extrema_comparison.json").read_text(
            encoding="utf-8"
        )
    )
    assert all(row["official_rise_months"] == 36 for row in payload["comparison"])
    assert all(row["minimum_matches_official"] for row in payload["comparison"])
    assert all(row["maximum_matches_official"] for row in payload["comparison"])
    assert all(
        row["difference_explanation"] == "Official and recomputed extrema agree."
        for row in payload["comparison"]
    )
    receipt = json.loads(
        (
            workspace / "receipts/datasets/silso_cycle_extrema_reproduction.json"
        ).read_text(encoding="utf-8")
    )
    assert receipt["producer"] == "solar-data"
    assert receipt["task_id"] == "bounded-silso-tool"

    denied = json.loads(
        data_tools.reproduce_silso_cycle_extrema.invoke(
            {
                "monthly_total_path": paths["silso-monthly-smoothed-v2"],
                "smoothed_path": paths["silso-monthly-smoothed-v2"],
                "official_extrema_path": paths["silso-cycle-extrema-v2"],
            },
            config=config,
        )
    )
    assert denied["status"] == "error"
    assert denied["error_type"] == "PermissionError"


def test_data_tools_only_resolve_hash_matching_manifest_inputs(
    tmp_path: Path, monkeypatch
) -> None:
    binding, config = _task_config(tmp_path, monkeypatch, "eligible-data-path")
    workspace = Path(binding.workspace)
    source = workspace / "inputs" / "observations.csv"
    source.write_text("date,value\n2024-01,1\n", encoding="utf-8")
    manifest_path = workspace / "input_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["inputs"] = [
        {
            "path": "/inputs/observations.csv",
            "role": "user_input",
            "bytes": source.stat().st_size,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    resolved = data_tools._resolve_eligible_data_path(
        "/inputs/observations.csv", config
    )
    assert resolved == source
    with pytest.raises(PermissionError, match="eligible input"):
        data_tools._resolve_eligible_data_path("/inputs/guessed.csv", config)

    source.write_text("changed\n", encoding="utf-8")
    with pytest.raises(PermissionError, match="eligible input"):
        data_tools._resolve_eligible_data_path("/inputs/observations.csv", config)


def test_planner_incremental_draft_is_atomic_resumable_and_task_scoped(
    tmp_path: Path, monkeypatch
) -> None:
    binding_a, config_a = _task_config(tmp_path, monkeypatch, "planner-draft-a")
    _binding_b, config_b = _task_config(tmp_path, monkeypatch, "planner-draft-b")

    brief_a = json.loads(
        planner_tools.research_planner_get_brief.invoke(
            {"request_input": QUESTION_A}, config=config_a
        )
    )
    sha_a = brief_a["request_sha256"]
    assert brief_a["draft_checkpoint"]["next_section"] == "scope"

    skipped = json.loads(
        planner_tools.research_planner_update_draft.invoke(
            {
                "section_name": "research_subquestions",
                "section_json": "[]",
                "request_sha256": sha_a,
            },
            config=config_a,
        )
    )
    assert skipped["status"] == "error"
    assert "persist prior sections first: scope" in skipped["error"]

    scope = {
        "objective": "比较两种合成数据方法的可复现实验设计。",
        "population_or_period": "固定随机种子产生的两组合成样本。",
        "boundaries": ["仅验证方法实现，不外推到真实太阳活动。"],
        "non_goals": ["不声称获得真实太阳物理结论。"],
    }
    persisted = json.loads(
        planner_tools.research_planner_update_draft.invoke(
            {
                "section_name": "scope",
                "section_json": json.dumps(scope, ensure_ascii=False),
                "request_sha256": sha_a,
            },
            config=config_a,
        )
    )
    assert persisted["status"] == "draft_section_persisted"
    assert persisted["completed_sections"] == ["scope"]
    assert persisted["next_section"] == "research_subquestions"
    section_receipt = persisted["section_receipt"]
    assert section_receipt["section_version"] == 1
    assert (Path(binding_a.workspace) / section_receipt["path"]).is_file()

    state_path = Path(binding_a.workspace) / "planner" / "working_state.json"
    assert (
        json.loads(state_path.read_text(encoding="utf-8"))["sections"]["scope"] == scope
    )

    planner_tools._PLANNER_DRAFTS.clear()
    resumed = json.loads(
        planner_tools.research_planner_get_brief.invoke(
            {"request_input": QUESTION_A}, config=config_a
        )
    )
    assert resumed["draft_checkpoint"]["completed_sections"] == ["scope"]
    assert resumed["draft_checkpoint"]["next_section"] == "research_subquestions"

    mismatched_rebind = json.loads(
        planner_tools.research_planner_get_brief.invoke(
            {"request_input": QUESTION_B}, config=config_a
        )
    )
    assert mismatched_rebind["canonical_request_reused"] is True
    assert mismatched_rebind["request_sha256"] == sha_a
    assert mismatched_rebind["draft_checkpoint"]["completed_sections"] == ["scope"]
    assert mismatched_rebind["brief"]["request"]["research_question"] == QUESTION_A

    brief_b = json.loads(
        planner_tools.research_planner_get_brief.invoke(
            {"request_input": QUESTION_B}, config=config_b
        )
    )
    assert brief_b["draft_checkpoint"]["completed_sections"] == []
    assert brief_b["draft_checkpoint"]["next_section"] == "scope"


def test_planner_incremental_draft_rejects_invalid_section_schema(
    tmp_path: Path, monkeypatch
) -> None:
    binding, config = _task_config(tmp_path, monkeypatch, "planner-draft-schema")
    brief = json.loads(
        planner_tools.research_planner_get_brief.invoke(
            {"request_input": QUESTION_A}, config=config
        )
    )
    assert brief["recommended_next_tool"] == "research_planner_create_empirical_plan"
    outcome = json.loads(
        planner_tools.research_planner_update_draft.invoke(
            {
                "section_name": "scope",
                "section_json": json.dumps(
                    {"objective": "only one field"}, ensure_ascii=False
                ),
                "request_sha256": brief["request_sha256"],
            },
            config=config,
        )
    )
    assert outcome["status"] == "error"
    assert "section schema validation failed" in outcome["error"]
    repeated = json.loads(
        planner_tools.research_planner_update_draft.invoke(
            {
                "section_name": "scope",
                "section_json": json.dumps(
                    {"objective": "only one field"}, ensure_ascii=False
                ),
                "request_sha256": brief["request_sha256"],
            },
            config=config,
        )
    )
    assert repeated["status"] == "blocked"
    assert repeated["error_code"] == "PLANNER_SECTION_NO_PROGRESS"
    assert repeated["must_stop"] is True
    assert repeated["consecutive_same_error"] == 2
    assert (Path(binding.workspace) / repeated["failure_receipt_path"]).is_file()


def test_planner_evaluation_rule_error_explains_criterion_basis_exclusivity() -> None:
    invalid = [
        {
            "id": "er1",
            "name": "Leakage gate",
            "purpose": "Prevent future information from entering training.",
            "target_step_ids": ["step1"],
            "outcome": "pass",
            "check": "No future records are visible in a fold.",
            "interpretation": "A violation invalidates the fold.",
            "uncertainty": "Timestamp semantics still require inspection.",
            "criterion_basis": {
                "kind": "exact_user_requirement",
                "basis_text": "Strict no-future-leakage.",
                "evidence_source_ids": [],
                "artifact_ids": ["art1"],
            },
        }
    ]

    with pytest.raises(
        ValueError, match="section schema validation failed"
    ) as exc_info:
        planner_tools._validate_section("evaluation_rules", invalid)

    message = str(exc_info.value)
    assert "use 'request_based', not alias 'exact_user_requirement'" in message
    assert (
        "request_based requires evidence_source_ids=[] and artifact_ids=[]" in message
    )


def test_planner_required_dataset_rejects_selected_source_without_string_id() -> None:
    invalid = [
        {
            "id": "ds1",
            "source_kind": "selected",
            "selected_source_id": None,
            "name": "Selected dataset",
            "purpose": "Supply the requested observations.",
            "required_variables": ["time", "value"],
            "time_coverage_needed": "Cover the study interval.",
            "cadence_needed": "Monthly",
            "quality_requirements": ["Document missing values."],
            "version_requirement": "Use the registered version.",
            "unit_requirements": ["Document units."],
            "revision_requirements": ["Record source revisions."],
            "license_requirements": ["Record the source license."],
            "acquisition_status": "selected",
        }
    ]

    with pytest.raises(
        ValueError, match="section schema validation failed"
    ) as exc_info:
        planner_tools._validate_section("required_datasets", invalid)

    assert (
        "required_datasets.0.selected_source_id: selected data requires a non-empty "
        "string request data-source id"
    ) in str(exc_info.value)


def test_planner_feedback_policy_migration_unlocks_old_stop_without_losing_history(
    tmp_path: Path, monkeypatch
) -> None:
    binding, config = _task_config(tmp_path, monkeypatch, "planner-policy-migration")
    brief = json.loads(
        planner_tools.research_planner_get_brief.invoke(
            {"request_input": QUESTION_A}, config=config
        )
    )
    invalid_args = {
        "section_name": "scope",
        "section_json": json.dumps({"objective": "incomplete"}),
        "request_sha256": brief["request_sha256"],
    }
    planner_tools.research_planner_update_draft.invoke(invalid_args, config=config)
    stopped = json.loads(
        planner_tools.research_planner_update_draft.invoke(invalid_args, config=config)
    )
    assert stopped["must_stop"] is True

    state_path = Path(binding.workspace) / "planner" / "working_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["failure_policy_version"] = "planner-section-feedback-v1"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    planner_tools._PLANNER_DRAFTS.clear()

    resumed = json.loads(
        planner_tools.research_planner_get_brief.invoke(
            {"request_input": QUESTION_A}, config=config
        )
    )
    assert resumed["draft_checkpoint"]["next_section"] == "scope"
    migrated = json.loads(state_path.read_text(encoding="utf-8"))
    assert migrated["section_failures"] == {}
    assert migrated["failure_policy_version"] == "planner-section-feedback-v2"
    receipt = migrated["failure_policy_migrations"][-1]
    assert receipt["unlocked_sections"] == ["scope"]
    assert (Path(binding.workspace) / receipt["receipt_path"]).is_file()
    failure_history = list(
        (
            Path(binding.workspace)
            / "planner"
            / "drafts"
            / brief["request_sha256"]
            / "failures"
            / "scope"
        ).glob("f*.json")
    )
    assert len(failure_history) == 2
    old_receipts = {
        path.name: path.read_text(encoding="utf-8") for path in failure_history
    }

    after_migration = json.loads(
        planner_tools.research_planner_update_draft.invoke(invalid_args, config=config)
    )
    assert after_migration["status"] == "error"
    assert after_migration["failure_count"] == 1
    assert after_migration["failure_receipt_path"].endswith("f0003.json")
    for name, old_content in old_receipts.items():
        assert (
            Path(binding.workspace)
            / "planner"
            / "drafts"
            / brief["request_sha256"]
            / "failures"
            / "scope"
            / name
        ).read_text(encoding="utf-8") == old_content


def test_planner_incremental_draft_reaches_full_preflight_and_survives_reload(
    tmp_path: Path, monkeypatch
) -> None:
    _binding, config = _task_config(tmp_path, monkeypatch, "planner-draft-complete")
    root = Path(__file__).resolve().parents[1]
    response = json.loads(
        (root / "research/planner/examples/definition_audit_response.json").read_text(
            encoding="utf-8"
        )
    )
    brief = json.loads(
        planner_tools.research_planner_get_brief.invoke(
            {"request_input": response["research_question"]}, config=config
        )
    )
    sha = brief["request_sha256"]
    for section_name in planner_tools._PLAN_SECTION_ORDER:
        outcome = json.loads(
            planner_tools.research_planner_update_draft.invoke(
                {
                    "section_name": section_name,
                    "section_json": json.dumps(
                        response["plan_content"][section_name], ensure_ascii=False
                    ),
                    "request_sha256": sha,
                },
                config=config,
            )
        )
        assert outcome["status"] == "draft_section_persisted"

    validated = json.loads(
        planner_tools.research_planner_validate_draft.invoke(
            {"request_sha256": sha}, config=config
        )
    )
    assert validated["status"] == "plan_ready"
    assert validated["route_step_count"] >= 1

    planner_tools._PLANNER_DRAFTS.clear()
    planner_tools._VALIDATED_RESPONSES.clear()
    resumed = json.loads(
        planner_tools.research_planner_get_brief.invoke(
            {"request_input": response["research_question"]}, config=config
        )
    )
    assert resumed["draft_checkpoint"]["missing_sections"] == []
    assert resumed["draft_checkpoint"]["validated"] is True


def test_planner_complete_draft_can_be_persisted_in_one_atomic_call(
    tmp_path: Path, monkeypatch
) -> None:
    _binding, config = _task_config(tmp_path, monkeypatch, "planner-batch-draft")
    root = Path(__file__).resolve().parents[1]
    response = json.loads(
        (root / "research/planner/examples/definition_audit_response.json").read_text(
            encoding="utf-8"
        )
    )
    brief = json.loads(
        planner_tools.research_planner_get_brief.invoke(
            {"request_input": response["research_question"]}, config=config
        )
    )

    submitted = json.loads(
        planner_tools.research_planner_submit_complete_draft.invoke(
            {
                "plan_content_json": json.dumps(
                    response["plan_content"], ensure_ascii=False
                ),
                "request_sha256": brief["request_sha256"],
            },
            config=config,
        )
    )

    assert submitted["status"] == "plan_ready"
    assert submitted["draft_checkpoint"]["missing_sections"] == []
    assert submitted["draft_checkpoint"]["validated"] is True


def test_planner_compact_empirical_plan_builds_full_reviewable_route(
    tmp_path: Path, monkeypatch
) -> None:
    _binding, config = _task_config(tmp_path, monkeypatch, "planner-compact-empirical")
    brief = json.loads(
        planner_tools.research_planner_get_brief.invoke(
            {"request_input": QUESTION_A}, config=config
        )
    )
    compact = {
        "scope": {
            "objective": "检验观测量之间的交互关系及其样本外预测含义。",
            "population_or_period": "具有可比观测和明确时间顺序的独立研究单位。",
            "boundaries": ["只使用预测发出时点已经可用的信息。"],
            "non_goals": ["不把相关关系解释为已经证实的因果机制。"],
        },
        "subquestions": [
            {
                "question": "目标交互项在独立研究单位上的方向与不确定性如何？",
                "purpose": "检验主要可证伪主张。",
                "completion_evidence": "给出估计、不确定性与影响分析。",
            },
            {
                "question": "交互模型是否改善真实样本外预测？",
                "purpose": "区分样本内拟合与预测增益。",
                "completion_evidence": "给出逐折误差和基线比较。",
            },
        ],
        "evidence_gaps": ["需要核验现有机制证据、组合关系和最强替代解释。"],
        "datasets": [
            {
                "name": "时间有序的周期级观测表",
                "purpose": "构造预测变量、调节变量与后续结果的独立样本。",
                "required_variables": ["时间边界", "预测变量", "调节变量", "后续结果"],
                "time_coverage_needed": "覆盖用户指定的全部独立研究单位。",
                "cadence_needed": "能够聚合为独立研究单位。",
                "quality_requirements": ["来源、单位、缺测与测量制度可追溯。"],
            }
        ],
        "stage_methods": {
            "data": "核验来源并按时间索引构造独立样本表。",
            "hypothesis_generation": "依据数据边界与系统文献核验形成可证伪假设和最强零假设。",
            "experiment_design": "预注册主模型、基线、不确定性、影响与样本外评估。",
            "experiment_result": "执行真实统计分析并保留逐单位和逐折诊断。",
            "hypothesis_update": "依据实验与证据结果更新方向、置信度、范围和结论类别。",
        },
        "evaluation_focus": [
            "数据构造与独立样本数可追溯。",
            "实验数值、文献支持和结论强度相互一致。",
        ],
    }

    submitted = json.loads(
        planner_tools.research_planner_create_empirical_plan.invoke(
            {
                "compact_plan_json": json.dumps(compact, ensure_ascii=False),
                "request_sha256": brief["request_sha256"],
            },
            config=config,
        )
    )

    assert submitted["status"] == "plan_ready", submitted
    state = json.loads(
        (Path(_binding.workspace) / "planner" / "working_state.json").read_text(
            encoding="utf-8"
        )
    )
    route = state["sections"]["research_route"]
    assert [step["stage"] for step in route] == [
        "data",
        "hypothesis_generation",
        "experiment_design",
        "experiment_result",
        "hypothesis_update",
    ]
    assert state["validated_response"] is not None
    assert state["sections"]["required_datasets"][0]["source_kind"] == "proposed"
    attempts = [
        json.loads(line)
        for line in (Path(_binding.workspace) / "planner" / "compact_attempts.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert attempts[-1]["status"] == "plan_ready"
    assert attempts[-1]["summary"]["datasets_count"] == 1


def test_planner_evidence_revision_invalidates_old_validation_idempotently(
    tmp_path: Path, monkeypatch
) -> None:
    binding, config = _task_config(tmp_path, monkeypatch, "planner-evidence-revision")
    response, sha = _complete_valid_planner(config)
    capsule = {
        "review_id": "planning-review-0002",
        "artifact_sha256": "a" * 64,
        "issues": [
            {
                "rule_id": "CROSS_STAGE_CLOSURE",
                "severity": "critical",
                "claim_ref": "planning-plan-v1#research_route.stage_sequence",
                "required_action": (
                    "Use data -> hypothesis_generation -> experiment_design -> "
                    "experiment_result -> hypothesis_update."
                ),
            }
        ],
    }

    registered = planner_tools.register_planner_evidence_revision(
        "planning-review-0002", capsule, config
    )
    assert registered["status"] == "evidence_revision_registered"
    assert registered["draft_checkpoint"]["next_action"] == "repair_evidence_revision"
    assert registered["draft_checkpoint"]["validated"] is False
    assert (Path(binding.workspace) / registered["receipt_path"]).is_file()
    state = planner_tools._lookup_draft(
        planner_tools._lookup_request(sha, config), config
    )
    assert state["validated_response"] is None
    assert ("planner-evidence-revision", sha) not in planner_tools._VALIDATED_RESPONSES

    repeated = planner_tools.register_planner_evidence_revision(
        "planning-review-0002", capsule, config
    )
    assert repeated["status"] == "evidence_revision_already_registered"
    receipts = list(
        (
            Path(binding.workspace) / "planner" / "drafts" / sha / "evidence_revisions"
        ).glob("r*.json")
    )
    assert len(receipts) == 1

    changed_scope = deepcopy(response["plan_content"]["scope"])
    changed_scope["objective"] += " Evidence-reviewed route closure."
    repaired = json.loads(
        planner_tools.research_planner_apply_revision_patch.invoke(
            {
                "changes_json": json.dumps({"scope": changed_scope}),
                "request_sha256": sha,
            },
            config=config,
        )
    )
    assert repaired["status"] == "plan_ready"
    assert repaired["resolved_evidence_revision"]["review_id"] == "planning-review-0002"
    assert repaired["draft_checkpoint"]["pending_evidence_revision"] is None
    assert repaired["draft_checkpoint"]["next_action"] == "freeze_plan"

    duplicate_args = {
        "changes_json": json.dumps({"scope": changed_scope}),
        "request_sha256": sha,
    }
    duplicate = json.loads(
        planner_tools.research_planner_apply_revision_patch.invoke(
            duplicate_args, config=config
        )
    )
    duplicate_again = json.loads(
        planner_tools.research_planner_apply_revision_patch.invoke(
            duplicate_args, config=config
        )
    )
    assert duplicate["status"] == "error"
    assert duplicate_again["status"] == "blocked"
    assert duplicate_again["error_code"] == "PLANNER_REVISION_NO_PROGRESS"
    assert duplicate_again["consecutive_same_error"] == 2
    failure_path = Path(binding.workspace) / duplicate_again["failure_receipt_path"]
    assert failure_path.is_file()
    assert len(list(failure_path.parent.glob("f*.json"))) == 2


def test_planner_shadow_revision_deduplicates_and_stops_repeated_schema_failure(
    tmp_path: Path, monkeypatch
) -> None:
    binding, config = _task_config(tmp_path, monkeypatch, "planner-shadow-guard")
    response, sha = _complete_valid_planner(config)
    changed_scope = deepcopy(response["plan_content"]["scope"])
    changed_scope["objective"] += " One bounded revision."
    args = {
        "section_name": "scope",
        "section_json": json.dumps(changed_scope),
        "request_sha256": sha,
    }
    first = json.loads(
        planner_tools.research_planner_stage_revision_section.invoke(
            args, config=config
        )
    )
    repeated = json.loads(
        planner_tools.research_planner_stage_revision_section.invoke(
            args, config=config
        )
    )
    assert first["status"] == "revision_section_staged"
    assert first["new_version_written"] is True
    assert repeated["status"] == "revision_section_already_staged"
    assert repeated["new_version_written"] is False
    # A staged candidate must surface the commit checkpoint so the middleware can
    # pin the forced commit tool_choice on the next model call (the local-commit
    # synthesize edge). This is the contract the qwen_compat deterministic pin
    # relies on; the unit test mocks it, so assert it against the real tool here.
    for staged in (first, repeated):
        checkpoint = staged["draft_checkpoint"]
        assert checkpoint["next_action"] == "commit_revision_candidate"
        assert checkpoint["revision_candidate"]["staged_sections"] == ["scope"]

    candidate_versions = list(
        (
            Path(binding.workspace)
            / "planner"
            / "drafts"
            / sha
            / "revision_candidates"
            / first["base_draft_sha256"]
            / "scope"
        ).glob("v*.json")
    )
    assert len(candidate_versions) == 1

    invalid_args = {
        "section_name": "research_route",
        "section_json": "[]",
        "request_sha256": sha,
    }
    invalid = json.loads(
        planner_tools.research_planner_stage_revision_section.invoke(
            invalid_args, config=config
        )
    )
    stopped = json.loads(
        planner_tools.research_planner_stage_revision_section.invoke(
            invalid_args, config=config
        )
    )
    assert invalid["status"] == "error"
    assert invalid["error_code"] == "PLANNER_REVISION_SECTION_INVALID"
    assert stopped["status"] == "blocked"
    assert stopped["error_code"] == "PLANNER_REVISION_SECTION_NO_PROGRESS"
    assert stopped["consecutive_same_error"] == 2
    assert stopped["must_stop"] is True
    assert (Path(binding.workspace) / stopped["failure_receipt_path"]).is_file()


def test_planner_shadow_candidate_requests_next_dependent_section(
    tmp_path: Path, monkeypatch
) -> None:
    binding, config = _task_config(tmp_path, monkeypatch, "planner-shadow-multisection")
    response, sha = _complete_valid_planner(config)
    state = planner_tools._lookup_draft(
        planner_tools._lookup_request(sha, config), config
    )
    state["sections"]["research_subquestions"].append(
        {
            "id": "Q2",
            "question": "第二个相互依赖的研究问题是什么？",
            "purpose": "验证跨区段候选修复。",
            "depends_on": ["Q1"],
            "completion_evidence": "Q2 被状态图和研究路线共同覆盖。",
        }
    )
    state["validated_response"] = None
    state["validated_response_sha256"] = None
    planner_tools._persist_draft(state, config)

    repaired_map = deepcopy(response["plan_content"]["research_state_map"])
    repaired_map["items"][0]["subquestion_ids"].append("Q2")
    staged = json.loads(
        planner_tools.research_planner_stage_revision_section.invoke(
            {
                "section_name": "research_state_map",
                "section_json": json.dumps(repaired_map, ensure_ascii=False),
                "request_sha256": sha,
            },
            config=config,
        )
    )
    assert staged["status"] == "revision_section_staged"

    preview = json.loads(
        planner_tools.research_planner_commit_revision_candidate.invoke(
            {"request_sha256": sha}, config=config
        )
    )
    assert preview["status"] == "revision_candidate_incomplete"
    assert preview["next_action"] == "stage_revision_section"
    assert preview["active_draft_unchanged"] is True
    assert "Q2" in preview["remaining_error"]
    assert preview["draft_checkpoint"]["revision_candidate"]["staged_sections"] == [
        "research_state_map"
    ]
    assert not list(
        (
            Path(binding.workspace)
            / "planner"
            / "drafts"
            / sha
            / "failures"
            / "revision_patch"
        ).glob("f*.json")
    )
    resumed = json.loads(
        planner_tools.research_planner_get_brief.invoke({}, config=config)
    )
    assert "exactly one additional affected section" in resumed["brief"]["instruction"]


def test_planner_atomic_revision_patch_rejects_regression_and_accepts_improvement(
    tmp_path: Path, monkeypatch
) -> None:
    binding, config = _task_config(tmp_path, monkeypatch, "planner-atomic-revision")
    root = Path(__file__).resolve().parents[1]
    response = json.loads(
        (root / "research/planner/examples/definition_audit_response.json").read_text(
            encoding="utf-8"
        )
    )
    brief = json.loads(
        planner_tools.research_planner_get_brief.invoke(
            {"request_input": response["research_question"]}, config=config
        )
    )
    sha = brief["request_sha256"]
    for section_name in planner_tools._PLAN_SECTION_ORDER:
        outcome = json.loads(
            planner_tools.research_planner_update_draft.invoke(
                {
                    "section_name": section_name,
                    "section_json": json.dumps(
                        response["plan_content"][section_name], ensure_ascii=False
                    ),
                    "request_sha256": sha,
                },
                config=config,
            )
        )
        assert outcome["status"] == "draft_section_persisted"

    invalid_rules = deepcopy(response["plan_content"]["evaluation_rules"])
    invalid_rules[0]["criterion_basis"] = {
        "kind": "request_based",
        "basis_text": "This sentence is absent from the canonical request.",
        "evidence_source_ids": [],
        "artifact_ids": [],
    }
    state = planner_tools._lookup_draft(
        planner_tools._lookup_request(sha, config), config
    )
    state["sections"]["evaluation_rules"] = invalid_rules
    state["validated_response"] = None
    state["validated_response_sha256"] = None
    planner_tools._persist_draft(state, config)
    state_path = Path(binding.workspace) / "planner" / "working_state.json"
    before = json.loads(state_path.read_text(encoding="utf-8"))
    # Simulate a WebUI/backend restart: the immutable request and draft must be
    # recoverable from the task workspace without reconstructing user input.
    with planner_tools._STATE_LOCK:
        planner_tools._REQUEST_CACHE.clear()
        planner_tools._ACTIVE_REQUEST_SHA256.clear()
        planner_tools._PLANNER_DRAFTS.clear()
    resumed = json.loads(
        planner_tools.research_planner_get_brief.invoke({}, config=config)
    )
    assert resumed["request_sha256"] == sha
    assert resumed["canonical_request_reused"] is False
    active_section = json.loads(
        planner_tools.research_planner_get_section.invoke(
            {"section_name": "evaluation_rules", "request_sha256": sha},
            config=config,
        )
    )
    assert active_section["status"] == "draft_section"
    assert active_section["active_section"] == invalid_rules
    assert active_section["staged_section"] is None
    assert active_section["read_only"] is True
    changed_scope = deepcopy(before["sections"]["scope"])
    changed_scope["objective"] += " Unrelated rewrite."

    single_write = json.loads(
        planner_tools.research_planner_update_draft.invoke(
            {
                "section_name": "scope",
                "section_json": json.dumps(changed_scope),
                "request_sha256": sha,
            },
            config=config,
        )
    )
    assert single_write["error_code"] == "PLANNER_COMPLETE_DRAFT_REQUIRES_ATOMIC_PATCH"

    rejected = json.loads(
        planner_tools.research_planner_apply_revision_patch.invoke(
            {
                "changes_json": json.dumps({"scope": changed_scope}),
                "request_sha256": sha,
            },
            config=config,
        )
    )
    assert rejected["status"] == "error"
    after_reject = json.loads(state_path.read_text(encoding="utf-8"))
    assert after_reject["sections"]["scope"] == before["sections"]["scope"]

    staged = json.loads(
        planner_tools.research_planner_stage_revision_section.invoke(
            {
                "section_name": "evaluation_rules",
                "section_json": json.dumps(
                    response["plan_content"]["evaluation_rules"]
                ),
                "request_sha256": sha,
            },
            config=config,
        )
    )
    assert staged["status"] == "revision_section_staged"
    assert staged["active_draft_unchanged"] is True
    candidate_checkpoint = json.loads(
        planner_tools.research_planner_get_draft_status.invoke(
            {"request_sha256": sha}, config=config
        )
    )
    assert candidate_checkpoint["next_action"] == "commit_revision_candidate"
    candidate_brief = json.loads(
        planner_tools.research_planner_get_brief.invoke({}, config=config)
    )
    assert (
        "MUST be research_planner_commit_revision_candidate"
        in candidate_brief["brief"]["instruction"]
    )
    staged_section = json.loads(
        planner_tools.research_planner_get_section.invoke(
            {"section_name": "evaluation_rules", "request_sha256": sha},
            config=config,
        )
    )
    assert staged_section["active_section"] == invalid_rules
    assert (
        staged_section["staged_section"] == response["plan_content"]["evaluation_rules"]
    )
    assert staged_section["staged_section_receipt"] == staged["section_receipt"]
    still_invalid = json.loads(state_path.read_text(encoding="utf-8"))
    assert still_invalid["sections"]["evaluation_rules"] == invalid_rules

    accepted = json.loads(
        planner_tools.research_planner_commit_revision_candidate.invoke(
            {"request_sha256": sha}, config=config
        )
    )
    assert accepted["status"] == "plan_ready"
    assert accepted["shadow_candidate_committed"] is True
    assert accepted["baseline_error_count"] >= 1
    assert accepted["remaining_error_count"] == 0
    frozen = json.loads(
        planner_tools.research_planner_freeze_plan.invoke(
            {"request_sha256": sha}, config=config
        )
    )
    assert frozen["status"] == "frozen_and_valid"
    assert (Path(binding.workspace) / frozen["research_plan_path"]).is_file()


def test_planner_revision_compares_shared_normalization_on_both_sides(
    tmp_path: Path, monkeypatch
) -> None:
    """A shared mechanical cleanup must not make a semantic repair look worse."""

    _binding, config = _task_config(
        tmp_path, monkeypatch, "planner-shared-normalization"
    )
    root = Path(__file__).resolve().parents[1]
    response = json.loads(
        (root / "research/planner/examples/definition_audit_response.json").read_text(
            encoding="utf-8"
        )
    )
    brief = json.loads(
        planner_tools.research_planner_get_brief.invoke(
            {"request_input": response["research_question"]}, config=config
        )
    )
    sha = brief["request_sha256"]
    for section_name in planner_tools._PLAN_SECTION_ORDER:
        section = deepcopy(response["plan_content"][section_name])
        if section_name == "research_state_map":
            section["items"][0]["item_kind"] = "supported_finding"
        elif section_name == "evaluation_rules":
            section[0]["criterion_basis"] = {
                "kind": "request_based",
                "basis_text": "This sentence is absent from the canonical request.",
                "evidence_source_ids": [],
                "artifact_ids": [],
            }
        outcome = json.loads(
            planner_tools.research_planner_update_draft.invoke(
                {
                    "section_name": section_name,
                    "section_json": json.dumps(section),
                    "request_sha256": sha,
                },
                config=config,
            )
        )
        assert outcome["status"] == "draft_section_persisted"

    staged = json.loads(
        planner_tools.research_planner_stage_revision_section.invoke(
            {
                "section_name": "research_state_map",
                "section_json": json.dumps(
                    response["plan_content"]["research_state_map"]
                ),
                "request_sha256": sha,
            },
            config=config,
        )
    )
    assert staged["status"] == "revision_section_staged"

    accepted = json.loads(
        planner_tools.research_planner_commit_revision_candidate.invoke(
            {"request_sha256": sha}, config=config
        )
    )
    assert accepted["status"] == "revision_patch_persisted"
    assert accepted["shadow_candidate_committed"] is True
    assert accepted["baseline_error_count"] == 2
    assert accepted["remaining_error_count"] == 1
    assert accepted["draft_checkpoint"]["next_action"] == "validate_draft"


def test_planner_non_improving_candidate_returns_to_section_staging(
    tmp_path: Path, monkeypatch
) -> None:
    binding, config = _task_config(
        tmp_path, monkeypatch, "planner-non-improving-candidate"
    )
    root = Path(__file__).resolve().parents[1]
    response = json.loads(
        (root / "research/planner/examples/definition_audit_response.json").read_text(
            encoding="utf-8"
        )
    )
    brief = json.loads(
        planner_tools.research_planner_get_brief.invoke(
            {"request_input": response["research_question"]}, config=config
        )
    )
    sha = brief["request_sha256"]
    for section_name in planner_tools._PLAN_SECTION_ORDER:
        section = deepcopy(response["plan_content"][section_name])
        if section_name == "evaluation_rules":
            section[0]["criterion_basis"] = {
                "kind": "request_based",
                "basis_text": "This sentence is absent from the canonical request.",
                "evidence_source_ids": [],
                "artifact_ids": [],
            }
        outcome = json.loads(
            planner_tools.research_planner_update_draft.invoke(
                {
                    "section_name": section_name,
                    "section_json": json.dumps(section),
                    "request_sha256": sha,
                },
                config=config,
            )
        )
        assert outcome["status"] == "draft_section_persisted"

    changed_scope = deepcopy(response["plan_content"]["scope"])
    changed_scope["objective"] += " Unrelated rewrite."
    staged = json.loads(
        planner_tools.research_planner_stage_revision_section.invoke(
            {
                "section_name": "scope",
                "section_json": json.dumps(changed_scope),
                "request_sha256": sha,
            },
            config=config,
        )
    )
    assert staged["status"] == "revision_section_staged"

    rejected = json.loads(
        planner_tools.research_planner_commit_revision_candidate.invoke(
            {"request_sha256": sha}, config=config
        )
    )
    assert rejected["status"] == "revision_candidate_incomplete"
    assert rejected["next_action"] == "stage_revision_section"
    assert rejected["draft_checkpoint"]["next_action"] == "stage_revision_section"
    assert rejected["baseline_error_count"] == rejected["remaining_error_count"]
    assert rejected["must_stop"] is False
    assert "demoted evaluation_rules[0]" in rejected["remaining_error"]
    assert rejected["affected_sections"] == ["evaluation_rules"]
    state = planner_tools._lookup_draft(
        planner_tools._lookup_request(sha, config), config
    )
    assert state["revision_patch_failures"] == {}
    assert not list(
        (
            Path(binding.workspace)
            / "planner"
            / "drafts"
            / sha
            / "failures"
            / "revision_patch"
        ).glob("f*.json")
    )

    wrong_section = json.loads(
        planner_tools.research_planner_stage_revision_section.invoke(
            {
                "section_name": "scope",
                "section_json": json.dumps(changed_scope),
                "request_sha256": sha,
            },
            config=config,
        )
    )
    assert wrong_section["status"] == "error"
    assert wrong_section["error_code"] == "PLANNER_REVISION_SECTION_NOT_AFFECTED"
    assert wrong_section["affected_sections"] == ["evaluation_rules"]
    assert wrong_section["draft_checkpoint"]["next_action"] == (
        "stage_revision_section"
    )
    assert state["revision_patch_failures"] == {}

    repeated = json.loads(
        planner_tools.research_planner_commit_revision_candidate.invoke(
            {"request_sha256": sha}, config=config
        )
    )
    assert repeated["status"] == "revision_candidate_incomplete"
    assert repeated["must_stop"] is False
    assert state["revision_patch_failures"] == {}


def test_planner_equal_count_different_error_stays_shadow_then_commits_zero(
    tmp_path: Path, monkeypatch
) -> None:
    """Sequential staging must not charge active failures before improvement."""

    binding, config = _task_config(
        tmp_path, monkeypatch, "planner-shadow-equal-count-different-error"
    )
    root = Path(__file__).resolve().parents[1]
    response = json.loads(
        (root / "research/planner/examples/definition_audit_response.json").read_text(
            encoding="utf-8"
        )
    )
    brief = json.loads(
        planner_tools.research_planner_get_brief.invoke(
            {"request_input": response["research_question"]}, config=config
        )
    )
    sha = brief["request_sha256"]
    for section_name in planner_tools._PLAN_SECTION_ORDER:
        section = deepcopy(response["plan_content"][section_name])
        if section_name == "evaluation_rules":
            section[0]["criterion_basis"] = {
                "kind": "request_based",
                "basis_text": "This sentence is absent from the canonical request.",
                "evidence_source_ids": [],
                "artifact_ids": [],
            }
        outcome = json.loads(
            planner_tools.research_planner_update_draft.invoke(
                {
                    "section_name": section_name,
                    "section_json": json.dumps(section),
                    "request_sha256": sha,
                },
                config=config,
            )
        )
        assert outcome["status"] == "draft_section_persisted"

    invalid_outline = deepcopy(response["plan_content"]["report_outline"])
    invalid_outline[0]["source_step_ids"] = ["missing-step"]
    for section_name, section in (
        ("evaluation_rules", response["plan_content"]["evaluation_rules"]),
        ("report_outline", invalid_outline),
    ):
        staged = json.loads(
            planner_tools.research_planner_stage_revision_section.invoke(
                {
                    "section_name": section_name,
                    "section_json": json.dumps(section),
                    "request_sha256": sha,
                },
                config=config,
            )
        )
        assert staged["status"] == "revision_section_staged"

    unchanged_count = json.loads(
        planner_tools.research_planner_commit_revision_candidate.invoke(
            {"request_sha256": sha}, config=config
        )
    )
    assert unchanged_count["status"] == "revision_candidate_incomplete"
    assert unchanged_count["baseline_error_count"] == 1
    assert unchanged_count["remaining_error_count"] == 1
    assert unchanged_count["affected_sections"] == ["report_outline"]
    assert "missing-step" in unchanged_count["remaining_error"]
    assert unchanged_count["active_draft_unchanged"] is True

    unstaged = json.loads(
        planner_tools.research_planner_stage_revision_section.invoke(
            {
                "section_name": "report_outline",
                "section_json": json.dumps(response["plan_content"]["report_outline"]),
                "request_sha256": sha,
            },
            config=config,
        )
    )
    assert unstaged["status"] == "revision_section_matches_active"
    committed = json.loads(
        planner_tools.research_planner_commit_revision_candidate.invoke(
            {"request_sha256": sha}, config=config
        )
    )
    assert committed["status"] == "plan_ready"
    assert committed["remaining_error_count"] == 0
    state = planner_tools._lookup_draft(
        planner_tools._lookup_request(sha, config), config
    )
    assert state["revision_patch_failures"] == {}
    assert not list(
        (
            Path(binding.workspace)
            / "planner"
            / "drafts"
            / sha
            / "failures"
            / "revision_patch"
        ).glob("f*.json")
    )


def test_hypothesis_state_and_freeze_root_are_task_scoped(
    tmp_path: Path, monkeypatch
) -> None:
    binding_a, config_a = _task_config(tmp_path, monkeypatch, "hypothesis-a")
    _binding_b, config_b = _task_config(tmp_path, monkeypatch, "hypothesis-b")

    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": QUESTION_A}, config=config_a
    )
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": QUESTION_B}, config=config_b
    )
    state_a = hypothesis_tools._STATES["hypothesis-a"]
    state_b = hypothesis_tools._STATES["hypothesis-b"]
    assert state_a.request != state_b.request

    checked = {"checked": True}
    state_a.validated_response = checked
    state_a.preflight_response_sha256 = canonical_json_sha256(checked)
    state_a.preflight_attempts = 1
    captured: dict[str, Path] = {}

    def fake_freeze(request, response, register, *, runs_root, path_root):
        assert request == state_a.request
        assert response == checked
        assert register is state_a.evidence_register
        captured["runs_root"] = runs_root
        captured["path_root"] = path_root
        return {"status": "frozen_and_valid"}

    monkeypatch.setattr(hypothesis_tools, "freeze_hypothesis_portfolio", fake_freeze)
    monkeypatch.setattr(
        hypothesis_tools, "_draft_warnings", lambda _state, _request: []
    )
    outcome = json.loads(
        hypothesis_tools.scientific_hypothesis_freeze.invoke({}, config=config_a)
    )
    task_root = Path(binding_a.workspace)
    assert outcome["status"] == "frozen_and_valid"
    assert captured == {
        "runs_root": task_root / "hypothesis" / "runs",
        "path_root": task_root,
    }


def test_automatic_experiment_run_is_created_inside_task_workspace(
    tmp_path: Path, monkeypatch
) -> None:
    binding, config = _task_config(tmp_path, monkeypatch, "experiment-a")
    repository_runs = Path(experiment_tools._PROJECT_ROOT) / "experiment" / "runs"
    before = (
        {path.name for path in repository_runs.iterdir()}
        if repository_runs.is_dir()
        else set()
    )

    outcome = json.loads(
        experiment_tools.automatic_experiment_bind_request.invoke(
            {"request_input": QUESTION_A}, config=config
        )
    )

    run_id = outcome["run_id"]
    task_run = Path(binding.workspace) / "experiment" / "runs" / run_id
    assert (task_run / "request.json").is_file()
    assert (task_run / "state.json").is_file()
    after = (
        {path.name for path in repository_runs.iterdir()}
        if repository_runs.is_dir()
        else set()
    )
    assert after == before


def test_research_experiment_scope_is_loaded_from_host_workspace(
    tmp_path: Path, monkeypatch
) -> None:
    binding, config = _task_config(tmp_path, monkeypatch, "experiment-scope-a")
    workspace = Path(binding.workspace)
    store = ResearchReviewStore(workspace, "experiment-scope-a")
    state = store.load_state()
    state["current_stage"] = "experiment_design"
    store._save_state(state)
    scope = {
        "schema_version": "research-experiment-scope-v1",
        "task_id": "experiment-scope-a",
        "stage": "experiment_design",
        "accepted_upstream_refs": [
            {
                "artifact_id": "hypothesis-artifact",
                "version": 2,
                "artifact_sha256": "a" * 64,
                "stage": "hypothesis",
            }
        ],
        "revision_review_id": None,
        "design_validation_limit": 4,
    }
    scope_path = workspace / "research_review" / "experiment_scope.json"
    scope_path.parent.mkdir(parents=True, exist_ok=True)
    scope_path.write_text(json.dumps(scope), encoding="utf-8")

    first = json.loads(
        experiment_tools.automatic_experiment_bind_request.invoke(
            {"request_input": QUESTION_A}, config=config
        )
    )
    second = json.loads(
        experiment_tools.automatic_experiment_bind_request.invoke(
            {"request_input": QUESTION_B}, config=config
        )
    )

    assert first["ok"] is True
    assert second["ok"] is False
    assert second["error_code"] == "RESEARCH_EXPERIMENT_SCOPE_ALREADY_BOUND"
    assert second["run_id"] == first["run_id"]
    run_state = json.loads(
        (workspace / "experiment" / "runs" / first["run_id"] / "state.json").read_text(
            encoding="utf-8"
        )
    )
    assert run_state["research_scope"] == scope
    assert run_state["design_validation_budget"] == {
        "limit": 4,
        "used": 0,
        "remaining": 4,
    }


def test_host_can_materialize_protocol_owned_morphology_design(
    tmp_path: Path, monkeypatch
) -> None:
    thread_id = "host-morphology-design"
    binding, config = _task_config(tmp_path, monkeypatch, thread_id)
    workspace = Path(binding.workspace)
    task_path = workspace / "task.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task["research_question"] = (
        "对 SILSO v2.0 第1—24周三组形态关系报告 Pearson、Spearman、"
        "双侧 p 值、bootstrap、留一和固定分时期结果。"
    )
    task_path.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
    store = ResearchReviewStore(workspace, thread_id)
    state = store.load_state()
    state["current_stage"] = "experiment_design"
    store._save_state(state)
    scope = {
        "schema_version": "research-experiment-scope-v1",
        "task_id": thread_id,
        "stage": "experiment_design",
        "accepted_upstream_refs": [],
        "revision_review_id": None,
        "design_validation_limit": 4,
        "analysis_protocol": "silso_cycle_morphology_v1",
    }
    scope_path = workspace / "research_review" / "experiment_scope.json"
    scope_path.parent.mkdir(parents=True, exist_ok=True)
    scope_path.write_text(json.dumps(scope), encoding="utf-8")
    inputs = workspace / "inputs"
    files = {
        "input_01": inputs / "a4b5b8812c9e966f-TableCyclesMiMa.txt",
        "input_02": inputs / "1289e5922889f26f-SN_ms_tot_V2.0.csv",
        "input_03": inputs / "e83932c7a47a12c4-SN_m_tot_V2.0.txt",
        "input_08": inputs / "19d01a07a0aae775-cycle_morphology_table.csv",
    }
    files["input_01"].write_text("01 1755 02 14.0 1761 06 144.1 11 04\n", encoding="utf-8")
    files["input_02"].write_text("1761;06;1761.455;144.1;-1.0;-1;1\n", encoding="utf-8")
    files["input_03"].write_text("1761 06 1761.455 100.0 -1.0 -1\n", encoding="utf-8")
    files["input_08"].write_text(
        "cycle_number,minimum_date,maximum_date,next_minimum_date,"
        "cycle_length_years,rise_time_years,decline_time_years,"
        "peak_smoothed_sunspot_number,observation_period_group,data_quality_note\n"
        "1,1755-02,1761-06,1766-06,11.333333333333334,"
        "6.333333333333333,5.0,144.1,early,fixture\n",
        encoding="utf-8",
    )
    _write_json = lambda path, value: path.write_text(
        json.dumps(value, ensure_ascii=False), encoding="utf-8"
    )
    _write_json(
        inputs / "_staged.json",
        {
            "schema_version": "automatic-experiment-input-refs-v1",
            "input_refs": [
                {
                    "id": input_id,
                    "path": path.relative_to(workspace).as_posix(),
                    "description": "accepted task-local input",
                    "required": True,
                }
                for input_id, path in files.items()
            ],
        },
    )

    receipt = experiment_tools.ensure_host_silso_morphology_design(config)

    run_root = workspace / "experiment" / "runs" / receipt["run_id"]
    request = json.loads((run_root / "request.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "design_validated"
    assert "two-sided p-values" in request["task"]
    assert (run_root / "design.json").is_file()


def test_research_experiment_bind_uses_host_staged_input_sidecar(
    tmp_path: Path, monkeypatch
) -> None:
    binding, config = _task_config(tmp_path, monkeypatch, "experiment-staged-inputs")
    workspace = Path(binding.workspace)
    store = ResearchReviewStore(workspace, "experiment-staged-inputs")
    state = store.load_state()
    state["current_stage"] = "experiment_design"
    store._save_state(state)
    scope = {
        "schema_version": "research-experiment-scope-v1",
        "task_id": "experiment-staged-inputs",
        "stage": "experiment_design",
        "accepted_upstream_refs": [],
        "revision_review_id": None,
        "design_validation_limit": 4,
    }
    scope_path = workspace / "research_review" / "experiment_scope.json"
    scope_path.parent.mkdir(parents=True, exist_ok=True)
    scope_path.write_text(json.dumps(scope), encoding="utf-8")
    inputs = workspace / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    (inputs / "accepted.json").write_text('{"ready": false}', encoding="utf-8")
    (inputs / "_staged.json").write_text(
        json.dumps(
            {
                "schema_version": "automatic-experiment-input-refs-v1",
                "input_refs": [
                    {
                        "id": "accepted_data",
                        "path": "inputs/accepted.json",
                        "description": "Accepted Data-stage input.",
                        "required": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    outcome = json.loads(
        experiment_tools.automatic_experiment_bind_request.invoke(
            {
                "request_input": (
                    "Analyze inputs/model-selected.json, then decide whether the "
                    "accepted evidence is ready."
                )
            },
            config=config,
        )
    )
    request = json.loads(
        (
            workspace / "experiment" / "runs" / outcome["run_id"] / "request.json"
        ).read_text(encoding="utf-8")
    )

    assert request["input_refs"] == [
        {
            "id": "accepted_data",
            "path": "inputs/accepted.json",
            "description": "Accepted Data-stage input.",
            "required": True,
        }
    ]


def test_research_experiment_scope_is_active_before_stage_artifact_exists(
    tmp_path: Path, monkeypatch
) -> None:
    """The host publishes the experiment scope immediately before dispatch.

    At that point ``current_stage`` still names the last accepted artifact
    (hypothesis), while ``stage_status.experiment_design`` is pending.  The
    experiment tool must bind to that host-owned scope instead of silently
    creating an unscoped run.
    """

    binding, config = _task_config(tmp_path, monkeypatch, "experiment-prestage-scope")
    workspace = Path(binding.workspace)
    store = ResearchReviewStore(workspace, "experiment-prestage-scope")
    state = store.load_state()
    state["current_stage"] = "hypothesis"
    state["stage_status"]["planning"] = "accepted_with_limits"
    state["stage_status"]["data"] = "accepted_with_limits"
    state["stage_status"]["hypothesis"] = "accepted_with_limits"
    state["stage_status"]["experiment_design"] = "pending"
    store._save_state(state)
    scope = {
        "schema_version": "research-experiment-scope-v1",
        "task_id": "experiment-prestage-scope",
        "stage": "experiment_design",
        "accepted_upstream_refs": [
            {
                "artifact_id": "hypothesis-artifact",
                "version": 1,
                "artifact_sha256": "a" * 64,
                "stage": "hypothesis",
            }
        ],
        "revision_review_id": None,
        "design_validation_limit": 4,
    }
    scope_path = workspace / "research_review" / "experiment_scope.json"
    scope_path.write_text(json.dumps(scope), encoding="utf-8")

    first = json.loads(
        experiment_tools.automatic_experiment_bind_request.invoke(
            {"request_input": QUESTION_A}, config=config
        )
    )
    second = json.loads(
        experiment_tools.automatic_experiment_bind_request.invoke(
            {"request_input": QUESTION_B}, config=config
        )
    )

    assert first["ok"] is True
    assert second["ok"] is False
    assert second["error_code"] == "RESEARCH_EXPERIMENT_SCOPE_ALREADY_BOUND"
    assert second["run_id"] == first["run_id"]
    run_state = json.loads(
        (workspace / "experiment" / "runs" / first["run_id"] / "state.json").read_text(
            encoding="utf-8"
        )
    )
    assert run_state["research_scope"] == scope


def test_experiment_design_evidence_revision_inherits_validated_request(
    tmp_path: Path, monkeypatch
) -> None:
    binding, config = _task_config(tmp_path, monkeypatch, "experiment-revision-request")
    workspace = Path(binding.workspace)
    store = ResearchReviewStore(workspace, "experiment-revision-request")
    state = store.load_state()
    state["current_stage"] = "experiment_design"
    store._save_state(state)
    scope_path = workspace / "research_review" / "experiment_scope.json"
    base_scope = {
        "schema_version": "research-experiment-scope-v1",
        "task_id": "experiment-revision-request",
        "stage": "experiment_design",
        "accepted_upstream_refs": [
            {
                "artifact_id": "data-artifact",
                "version": 1,
                "artifact_sha256": "a" * 64,
                "stage": "data",
            }
        ],
        "revision_review_id": None,
        "design_validation_limit": 4,
    }
    scope_path.parent.mkdir(parents=True, exist_ok=True)
    scope_path.write_text(json.dumps(base_scope), encoding="utf-8")
    original = json.loads(
        experiment_tools.automatic_experiment_bind_request.invoke(
            {"request_input": QUESTION_A}, config=config
        )
    )
    assert original["ok"] is True
    original_root = workspace / "experiment" / "runs" / original["run_id"]
    original_state = json.loads(
        (original_root / "state.json").read_text(encoding="utf-8")
    )
    original_state["phase"] = "design_validated"
    original_state["design_path"] = "design.json"
    (original_root / "state.json").write_text(
        json.dumps(original_state), encoding="utf-8"
    )
    (original_root / "design.json").write_text(
        '{"schema_version":"automatic-experiment-design-v1"}', encoding="utf-8"
    )

    revision_scope = {
        **base_scope,
        "revision_review_id": "experiment-design-review-0001",
    }
    scope_path.write_text(json.dumps(revision_scope), encoding="utf-8")
    revision = json.loads(
        experiment_tools.automatic_experiment_bind_request.invoke(
            {"request_input": "placeholder-do-not-use"}, config=config
        )
    )

    assert revision["ok"] is True
    assert revision["run_id"] != original["run_id"]
    assert revision["request"] == original["request"]
    revision_state = json.loads(
        (
            workspace / "experiment" / "runs" / revision["run_id"] / "state.json"
        ).read_text(encoding="utf-8")
    )
    assert revision_state["research_scope"] == revision_scope


def test_experiment_result_stage_rejects_binding_a_new_run(
    tmp_path: Path, monkeypatch
) -> None:
    binding, config = _task_config(tmp_path, monkeypatch, "experiment-result-bind")
    workspace = Path(binding.workspace)
    store = ResearchReviewStore(workspace, "experiment-result-bind")
    state = store.load_state()
    state["current_stage"] = "experiment_result"
    store._save_state(state)
    scope = {
        "schema_version": "research-experiment-scope-v1",
        "task_id": "experiment-result-bind",
        "stage": "experiment_result",
        "accepted_upstream_refs": [],
        "revision_review_id": None,
        "design_validation_limit": 3,
    }
    scope_path = workspace / "research_review" / "experiment_scope.json"
    scope_path.parent.mkdir(parents=True, exist_ok=True)
    scope_path.write_text(json.dumps(scope), encoding="utf-8")

    outcome = json.loads(
        experiment_tools.automatic_experiment_bind_request.invoke(
            {"request_input": QUESTION_A}, config=config
        )
    )

    assert outcome["ok"] is False
    assert outcome["error_code"] == "RESEARCH_EXPERIMENT_RESULT_REBIND_FORBIDDEN"
    assert not (workspace / "experiment" / "runs").exists()


def _run3_route_payload(route_steps, stop_rules):
    """Build a (response, request) pair whose route mirrors a multi-step empirical plan.

    The shape follows the run3 draft (rs_data -> rs_hyp -> rs_ed), letting the
    regression tests pin the route outcome/transition contract directly against
    ``validate_planner_response`` without a full draft lifecycle.
    """

    root = Path(__file__).resolve().parents[1]
    response = json.loads(
        (root / "research/planner/examples/definition_audit_response.json").read_text(
            encoding="utf-8"
        )
    )
    request = json.loads(
        (root / "research/planner/examples/definition_audit_request.json").read_text(
            encoding="utf-8"
        )
    )
    response = deepcopy(response)
    response["response_kind"] = "plan_ready"
    response["plan_content"]["research_route"] = route_steps
    response["plan_content"]["stop_rules"] = stop_rules
    # keep example reference integrity: state-map / report-outline point at the
    # terminal route step instead of the replaced single "R1".
    last_step = route_steps[-1]["id"]
    for item in response["plan_content"]["research_state_map"]["items"]:
        item["resolution_step_ids"] = [last_step]
    all_steps = [step["id"] for step in route_steps]
    for section in response["plan_content"]["report_outline"]:
        section["source_step_ids"] = all_steps
    for rule in response["plan_content"]["evaluation_rules"]:
        rule["target_step_ids"] = all_steps
    policy = response["plan_content"]["iteration_policy"]
    policy["global_visit_limit"] = max(
        int(policy.get("global_visit_limit", 1)), len(route_steps)
    )
    if policy.get("review_step_ids"):
        policy["review_step_ids"] = all_steps
    # route steps produce art_<step_id>; register them so reference integrity holds.
    response["plan_content"]["research_artifacts"] = [
        {
            "id": f"art_{step['id']}",
            "name": f"artifact {step['id']}",
            "artifact_kind": "intermediate_result",
            "purpose": f"carries the output of {step['id']}",
            "source_kind": "planned_output",
            "producer_step_id": step["id"],
            "subquestion_ids": ["Q1"],
            "content_requirements": [f"content required from {step['id']}"],
        }
        for step in route_steps
    ]
    return response, request


def _route_step(step_id, stage, prereq, consumes, transitions, *, join="all", visit=1):
    outcomes = sorted({t["on"] for t in transitions})
    outcome_rules = [
        {
            "outcome": name,
            "criteria": [f"criteria for {step_id} {name}"],
            "evidence_required": [f"evidence for {step_id} {name}"],
        }
        for name in outcomes
    ]
    if "completed" not in outcomes:
        outcome_rules.append(
            {
                "outcome": "completed",
                "criteria": [f"criteria for {step_id} completed"],
                "evidence_required": [f"evidence for {step_id} completed"],
            }
        )
    return {
        "id": step_id,
        "stage": stage,
        "objective": f"objective for {step_id}",
        "necessity": f"necessity for {step_id}",
        "method_outline": f"method for {step_id}",
        "iteration": 1,
        "join_policy": join,
        "visit_limit": visit,
        "prerequisite_step_ids": prereq,
        "subquestion_ids": ["Q1"],
        "required_dataset_ids": [],
        "consumes_artifact_ids": consumes,
        "produces_artifact_ids": [f"art_{step_id}"],
        "capability_needs": [],
        "evaluation_rule_ids": ["E1"],
        "outcome_rules": outcome_rules,
        "transitions": transitions,
    }


def _stop_rule(rule_id, terminal):
    return {
        "id": rule_id,
        "terminal_status": terminal,
        "condition_kind": "goal_satisfied"
        if terminal == "plan_complete"
        else "partial_result_ready",
        "condition": f"stop when {terminal}",
        "required_evidence": [f"evidence for {terminal}"],
        "report_section_ids": ["P1"],
    }


def test_planner_route_rejects_non_completed_transition_into_completion_dependent_target() -> (
    None
):
    """run3 regression: rs_hyp/rs_res used inconclusive -> completion-required target.

    rs_ed depends on rs_hyp completing (it consumes rs_hyp's artifact), so an
    ``inconclusive`` (or any non-completed) outcome may not transition into it.
    The message must name the source step, the outcome, the target, and a fix
    direction so the planner repairs once instead of guessing fields.
    """

    from research_planner.contracts import validate_planner_response

    steps = [
        _route_step(
            "rs_data",
            "data",
            [],
            [],
            [
                {"on": "completed", "target_step_id": "rs_hyp"},
                {"on": "input_missing", "terminal_status": "needs_input"},
            ],
        ),
        _route_step(
            "rs_hyp",
            "hypothesis_generation",
            ["rs_data"],
            ["art_rs_data"],
            [
                {"on": "completed", "target_step_id": "rs_ed"},
                {"on": "inconclusive", "target_step_id": "rs_ed"},
            ],
        ),
        _route_step(
            "rs_ed",
            "experiment_design",
            ["rs_hyp"],
            ["art_rs_hyp"],
            [
                {"on": "completed", "terminal_status": "plan_complete"},
                {"on": "method_invalid", "terminal_status": "no_viable_route"},
            ],
        ),
    ]
    payload, request = _run3_route_payload(
        steps,
        [
            _stop_rule("sr1", "plan_complete"),
            _stop_rule("sr2", "no_viable_route"),
            _stop_rule("sr3", "needs_input"),
        ],
    )
    with pytest.raises(Exception) as excinfo:
        validate_planner_response(payload, request)
    message = str(excinfo.value)
    # pin the precise source/outcome/target localization
    assert "rs_hyp" in message
    assert "inconclusive" in message
    assert "rs_ed" in message
    # and an actionable fix direction (not just "cannot transition")
    assert "completed" in message
    lowered = message.lower()
    assert any(
        token in lowered
        for token in (
            "terminal",
            "rework",
            "revision",
            "alternative",
            "self-correction",
            "stop_rules",
        )
    )


def test_planner_route_accepts_terminal_revision_and_self_correction_transitions() -> (
    None
):
    """run3 legal counterpart: non-completed outcomes go to terminal or rework.

    completed -> completion-dependent downstream; inconclusive -> terminal;
    method_invalid -> rework (a self-correction cycle back to the source step)
    with self_correction enabled and visit_limit >= 2. All must validate.
    """

    from research_planner.contracts import validate_planner_response

    steps = [
        _route_step(
            "rs_data",
            "data",
            [],
            [],
            [
                {"on": "completed", "target_step_id": "rs_hyp"},
                {"on": "input_missing", "terminal_status": "needs_input"},
            ],
        ),
        _route_step(
            "rs_hyp",
            "hypothesis_generation",
            ["rs_data"],
            ["art_rs_data"],
            [
                {"on": "completed", "target_step_id": "rs_ed"},
                {"on": "inconclusive", "terminal_status": "partial_result"},
            ],
            visit=2,
        ),
        _route_step(
            "rs_ed",
            "experiment_design",
            ["rs_hyp"],
            ["art_rs_hyp"],
            [
                {"on": "completed", "terminal_status": "plan_complete"},
                {"on": "method_invalid", "target_step_id": "rs_hyp"},
            ],
            visit=2,
        ),
    ]
    payload, request = _run3_route_payload(
        steps,
        [
            _stop_rule("sr1", "plan_complete"),
            _stop_rule("sr2", "partial_result"),
            _stop_rule("sr3", "needs_input"),
        ],
    )
    payload["plan_content"]["iteration_policy"]["revision_triggers"] = [
        "method_invalid"
    ]
    validated = validate_planner_response(payload, request)
    assert validated["response_kind"] == "plan_ready"


def test_planner_stop_rules_must_cover_every_used_terminal_status() -> None:
    """run3 first failure: a used terminal status has no stop_rules entry."""

    from research_planner.contracts import validate_planner_response

    steps = [
        _route_step(
            "rs_data",
            "data",
            [],
            [],
            [
                {"on": "completed", "target_step_id": "rs_hyp"},
                {"on": "input_missing", "terminal_status": "needs_input"},
            ],
        ),
        _route_step(
            "rs_hyp",
            "hypothesis_generation",
            ["rs_data"],
            ["art_rs_data"],
            [
                {"on": "completed", "terminal_status": "plan_complete"},
                {"on": "inconclusive", "terminal_status": "partial_result"},
            ],
        ),
    ]
    # needs_input is used by rs_data but no stop_rule covers it.
    payload, request = _run3_route_payload(
        steps, [_stop_rule("sr1", "plan_complete"), _stop_rule("sr2", "partial_result")]
    )
    payload["plan_content"]["iteration_policy"]["budget_response"] = "partial_result"
    with pytest.raises(Exception) as excinfo:
        validate_planner_response(payload, request)
    message = str(excinfo.value)
    assert "needs_input" in message


def test_planner_route_rejects_cyclic_step_with_visit_limit_below_two() -> None:
    """run1 freeze blocker: a back-edge makes a step cyclic, so visit_limit must be >= 2.

    rs_ed.method_invalid -> rs_hyp is a rework cycle, so rs_hyp sits on a control
    cycle. Leaving rs_hyp at visit_limit=1 must be rejected deterministically at
    validate/freeze time with a message naming the cyclic step; bumping
    rs_hyp.visit_limit to 2 must validate.
    """

    from research_planner.contracts import validate_planner_response

    def _steps(hyp_visit):
        return [
            _route_step(
                "rs_data",
                "data",
                [],
                [],
                [
                    {"on": "completed", "target_step_id": "rs_hyp"},
                    {"on": "input_missing", "terminal_status": "needs_input"},
                ],
            ),
            _route_step(
                "rs_hyp",
                "hypothesis_generation",
                ["rs_data"],
                ["art_rs_data"],
                [
                    {"on": "completed", "target_step_id": "rs_ed"},
                    {"on": "inconclusive", "terminal_status": "partial_result"},
                ],
                visit=hyp_visit,
            ),
            _route_step(
                "rs_ed",
                "experiment_design",
                ["rs_hyp"],
                ["art_rs_hyp"],
                [
                    {"on": "completed", "terminal_status": "plan_complete"},
                    {"on": "method_invalid", "target_step_id": "rs_hyp"},
                ],
                visit=2,
            ),
        ]

    stop_rules = [
        _stop_rule("sr1", "plan_complete"),
        _stop_rule("sr2", "partial_result"),
        _stop_rule("sr3", "needs_input"),
    ]

    bad_payload, request = _run3_route_payload(_steps(hyp_visit=1), stop_rules)
    with pytest.raises(Exception) as excinfo:
        validate_planner_response(bad_payload, request)
    message = str(excinfo.value)
    assert "rs_hyp" in message
    assert "visit_limit" in message

    good_payload, request = _run3_route_payload(_steps(hyp_visit=2), stop_rules)
    validated = validate_planner_response(good_payload, request)
    assert validated["response_kind"] == "plan_ready"


def test_preflight_normalization_repairs_cyclic_visit_limit() -> None:
    """Gate run_04d regression: a model draft wrote a rework cycle with the
    back-edge target left at visit_limit=1. Deterministic validation used to
    reject the whole route at freeze time; normalization now lifts the cyclic
    step to visit_limit=2 so the draft freezes on the first pass.
    """

    from research_planner.harness import preflight_planner_response

    steps = [
        _route_step(
            "rs_data",
            "data",
            [],
            [],
            [
                {"on": "completed", "target_step_id": "rs_hyp"},
                {"on": "input_missing", "terminal_status": "needs_input"},
            ],
        ),
        _route_step(
            "rs_hyp",
            "hypothesis_generation",
            ["rs_data"],
            ["art_rs_data"],
            [
                {"on": "completed", "target_step_id": "rs_ed"},
                {"on": "inconclusive", "terminal_status": "partial_result"},
            ],
            visit=1,
        ),
        _route_step(
            "rs_ed",
            "experiment_design",
            ["rs_hyp"],
            ["art_rs_hyp"],
            [
                {"on": "completed", "terminal_status": "plan_complete"},
                {"on": "method_invalid", "target_step_id": "rs_hyp"},
            ],
            visit=1,
        ),
    ]
    stop_rules = [
        _stop_rule("sr1", "plan_complete"),
        _stop_rule("sr2", "partial_result"),
        _stop_rule("sr3", "needs_input"),
    ]
    response, request = _run3_route_payload(steps, stop_rules)
    # every step on the rs_hyp<->rs_ed cycle was written at visit_limit=1
    result = preflight_planner_response(request, response)
    assert result["status"] == "plan_ready"


def test_preflight_normalization_demotes_claim_located_without_locator() -> None:
    """Gate run_0fe regression: a model draft marked an evidence source
    claim_located but gave a locator with no page/section/line/figure/table
    reference. Validation rejected it; normalization now demotes the source to
    reference_resolved when nothing claim-strength depends on it.
    """

    from research_planner.contracts import validate_planner_response
    from research_planner.harness import preflight_planner_response

    steps = [
        _route_step(
            "rs_data",
            "data",
            [],
            [],
            [
                {"on": "completed", "terminal_status": "plan_complete"},
                {"on": "input_missing", "terminal_status": "needs_input"},
            ],
        ),
    ]
    stop_rules = [
        _stop_rule("sr1", "plan_complete"),
        _stop_rule("sr2", "needs_input"),
        _stop_rule("sr3", "partial_result"),
    ]

    def _with_source(locator):
        response, request = _run3_route_payload(steps, stop_rules)
        response["plan_content"]["evidence_sources"] = [
            {
                "id": "ES1",
                "citation": "task spec",
                "locator": locator,
                "source_kind": "user_provided",
                "verification_level": "claim_located",
                "role": "states the planning requirement",
                "state_item_ids": ["S1"],
                "subquestion_ids": ["Q1"],
                "limitations": "no independent corroboration",
            }
        ]
        # evidence traceability is bidirectional: S1 must reference ES1 back.
        for item in response["plan_content"]["research_state_map"]["items"]:
            if item.get("id") == "S1":
                item["evidence_source_ids"] = ["ES1"]
        return response, request

    # a locator without a structural hint is demoted and the plan freezes
    response, request = _with_source("任务正文相关段落")
    result = preflight_planner_response(request, response)
    assert result["status"] == "plan_ready"

    # a locator carrying a section/table hint stays claim_located and freezes
    response, request = _with_source("第 3 节 表 2")
    result = preflight_planner_response(request, response)
    assert result["status"] == "plan_ready"

    # sanity: the raw validator still rejects the original defect, proving the
    # repair happens in normalization rather than by loosening the contract
    response, request = _with_source("任务正文相关段落")
    with pytest.raises(Exception) as excinfo:
        validate_planner_response(response, request)
    assert "locator" in str(excinfo.value)
