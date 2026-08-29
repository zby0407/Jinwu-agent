from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


class _FakeHarness:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def collect_evidence(self, **kwargs):
        self.calls.append({"operation": "collect_evidence", **kwargs})
        return {
            "schema_version": "harness-evidence-v1",
            "status": "completed",
            "task_id": kwargs["task_id"],
            "items": [],
            "artifacts": [],
            "receipt_ref": "research_review/harness/task-1/receipt.json",
            "binding": {
                "research_question": kwargs["research_question"],
                "focus": kwargs["focus"],
            },
        }

    def run_analysis(self, **kwargs):
        self.calls.append({"operation": "run_analysis", **kwargs})
        return {
            "schema_version": "harness-evidence-v1",
            "status": "completed",
            "task_id": kwargs["task_id"],
            "items": [],
            "artifacts": [],
            "receipt_ref": "research_review/harness/task-1/receipt.json",
            "input_refs": kwargs["input_refs"],
        }


def _config() -> dict:
    return {"configurable": {"thread_id": "task-1"}}


def _write_task_metadata(
    root: Path,
    question: str = "Can polar fields predict the next cycle?",
    *,
    thread_id: str = "task-1",
) -> None:
    (root / "task.json").write_text(
        json.dumps({"thread_id": thread_id, "research_question": question}),
        encoding="utf-8",
    )


def _authoritative_focus(question: str) -> str:
    return (
        f"Research question: {question}\n"
        "Plan objective: Test the question with task-bound solar observations.\n"
        "Data objective: Build the current task's reviewable solar data product."
    )


def _write_data_context(
    root: Path,
    question: str = "Can polar fields predict the next cycle?",
    *,
    task_id: str = "task-1",
    status: str = "inputs_available",
    must_stop: bool = False,
    omitted_hashes: set[str] | None = None,
    hash_overrides: dict[str, object] | None = None,
) -> Path:
    input_manifest_path = root / "input_manifest.json"
    input_manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "task-input-manifest-v1",
                "thread_id": task_id,
                "inputs": [],
                "project_inputs": [],
            }
        ),
        encoding="utf-8",
    )
    plan_ref = "planner/runs/current/research_plan.json"
    plan_path = root / plan_ref
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan = {
        "schema_version": "research-plan-v1",
        "research_question": question,
        "scope": {"objective": "Test the question with task-bound solar observations."},
        "research_route": [
            {
                "id": "R1",
                "stage": "data",
                "objective": "Build the current task's reviewable solar data product.",
            }
        ],
        "research_artifacts": [],
    }
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    body = {
        "schema_version": "solar-data-context-v1",
        "context_mode": "full_research",
        "task_id": task_id,
        "analysis_protocol": "solar_polar_precursor_v1",
        "required_data_product": "solar_polar_precursor_table_v1",
        "planning_artifact_ref": "research_review/artifacts/planning.json",
        "planning_verdict_ref": {
            "review_id": "planning-review",
            "verdict_sha256": "a" * 64,
        },
        "plan_source_ref": plan_ref,
        "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        "task_sha256": hashlib.sha256((root / "task.json").read_bytes()).hexdigest(),
        "research_question_sha256": hashlib.sha256(
            question.encode("utf-8")
        ).hexdigest(),
        "input_manifest_sha256": hashlib.sha256(
            input_manifest_path.read_bytes()
        ).hexdigest(),
        "required_datasets": [],
        "data_steps": plan["research_route"],
        "planned_outputs": [],
        "eligible_inputs": [],
        "status": status,
    }
    for field in omitted_hashes or set():
        body.pop(field, None)
    body.update(hash_overrides or {})
    context_sha256 = hashlib.sha256(
        json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    receipt = (
        root / "receipts" / "datasets" / f"data-context-{context_sha256[:16]}.json"
    )
    receipt.parent.mkdir(parents=True, exist_ok=True)
    envelope = {
        **body,
        "context_sha256": context_sha256,
        "created_at": "2026-08-17T00:00:00+00:00",
        "path_policy": "Only eligible_inputs may be used.",
    }
    if must_stop:
        envelope["must_stop"] = True
    receipt.write_text(json.dumps(envelope), encoding="utf-8")
    return receipt


def _write_generated_output_receipt(
    root: Path,
    output_ref: str,
    *,
    task_id: str = "task-1",
    schema_version: str | None = "research-dataset-receipt-v1",
    receipt_type: str = "silso_cycle_extrema_reproduction",
    producer: str = "solar-data",
) -> None:
    output = root / output_ref
    receipt = root / "receipts" / "datasets" / "producer.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "receipt_type": receipt_type,
        "status": "verified",
        "producer": producer,
        "task_id": task_id,
        "outputs": [
            {
                "path": output_ref,
                "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            }
        ],
    }
    if schema_version is not None:
        payload["schema_version"] = schema_version
    receipt.write_text(json.dumps(payload), encoding="utf-8")


def test_data_agent_can_collect_task_bound_research_evidence(
    monkeypatch, tmp_path: Path
):
    import jw.tools.solar_feature as solar_feature

    _write_task_metadata(tmp_path)
    _write_data_context(tmp_path)
    harness = _FakeHarness()
    monkeypatch.setattr(
        solar_feature, "workspace_root_from_config", lambda config: tmp_path
    )
    monkeypatch.setattr(
        solar_feature, "_qwen_harness_client", lambda model=None: harness
    )
    monkeypatch.setattr(
        solar_feature,
        "_research_task_id",
        lambda config: "task-1",
    )

    result = solar_feature.solar_research_evidence.func(
        research_question="",
        queries=["polar field precursor solar cycle"],
        config=_config(),
    )
    payload = json.loads(result)

    assert payload["status"] == "completed"
    assert payload["task_id"] == "task-1"
    assert payload["binding"]["focus"] == _authoritative_focus(
        "Can polar fields predict the next cycle?"
    )
    assert payload["receipt_refs"] == ["research_review/harness/task-1/receipt.json"]
    assert harness.calls == [
        {
            "operation": "collect_evidence",
            "task_root": tmp_path,
            "task_id": "task-1",
            "research_question": "Can polar fields predict the next cycle?",
            "focus": _authoritative_focus("Can polar fields predict the next cycle?"),
            "queries": ["polar field precursor solar cycle"],
            "model": "qwen3.8-max",
        }
    ]


def test_data_agent_rejects_research_question_that_differs_from_task_metadata(
    monkeypatch, tmp_path: Path
):
    import jw.tools.solar_feature as solar_feature

    _write_task_metadata(tmp_path, "Bound task question")
    _write_data_context(tmp_path, "Bound task question")
    harness = _FakeHarness()
    monkeypatch.setattr(
        solar_feature, "workspace_root_from_config", lambda config: tmp_path
    )
    monkeypatch.setattr(
        solar_feature, "_qwen_harness_client", lambda model=None: harness
    )

    result = solar_feature.solar_research_evidence.func(
        research_question="Model substituted question",
        focus="deterministic data context focus",
        queries=["solar data"],
        config=_config(),
    )

    payload = json.loads(result)
    assert payload["status"] == "error"
    assert "task.json" in payload["error_message"]
    assert harness.calls == []


def test_data_agent_rejects_analysis_input_not_bound_to_eligible_inputs(
    monkeypatch, tmp_path: Path
):
    import jw.tools.solar_feature as solar_feature

    _write_task_metadata(tmp_path, "question")
    _write_data_context(tmp_path, "question")
    monkeypatch.setattr(
        solar_feature, "workspace_root_from_config", lambda config: tmp_path
    )
    monkeypatch.setattr(
        solar_feature, "_qwen_harness_client", lambda model=None: _FakeHarness()
    )
    monkeypatch.setattr(solar_feature, "_research_task_id", lambda config: "task-1")
    monkeypatch.setattr(
        solar_feature,
        "_eligible_input_records",
        lambda config: [
            {
                "path": "inputs/accepted.csv",
                "sha256": "abc",
                "dataset_id": "solar-cycle-table-v1",
            }
        ],
    )

    result = solar_feature.solar_research_analysis.func(
        research_question="question",
        focus=_authoritative_focus("question"),
        input_refs=["inputs/not-accepted.csv"],
        instructions="compute a slope",
        config=_config(),
    )
    payload = json.loads(result)
    assert payload["status"] == "error"
    assert "eligible input" in payload["error_message"]


def test_data_agent_stages_hash_bound_project_input_before_harness_analysis(
    monkeypatch, tmp_path: Path
):
    """Project-mounted inputs must be copied into the task workspace first."""

    import jw.tools.solar_feature as solar_feature

    question = "question"
    _write_task_metadata(tmp_path, question)
    _write_data_context(tmp_path, question)
    project_source = tmp_path / "project-data" / "observations.csv"
    project_source.parent.mkdir(parents=True)
    project_source.write_text("cycle,value\n24,115\n", encoding="utf-8")
    virtual_ref = "/project/data/observations.csv"
    record = {
        "path": virtual_ref,
        "sha256": hashlib.sha256(project_source.read_bytes()).hexdigest(),
        "bytes": project_source.stat().st_size,
        "role": "primary_data",
        "source_group": "project_inputs",
        "dataset_id": "solar-cycle-table-v1",
    }
    harness = _FakeHarness()
    monkeypatch.setattr(
        solar_feature, "workspace_root_from_config", lambda config: tmp_path
    )
    monkeypatch.setattr(
        solar_feature, "_qwen_harness_client", lambda model=None: harness
    )
    monkeypatch.setattr(
        solar_feature, "_eligible_input_records", lambda config: [record]
    )
    monkeypatch.setattr(
        solar_feature,
        "resolve_scoped_path",
        lambda value, config, allow_project=False: (
            project_source if value == virtual_ref else tmp_path / value
        ),
    )

    result = solar_feature.solar_research_analysis.func(
        focus=_authoritative_focus(question),
        input_refs=[virtual_ref],
        instructions="summarize the supplied table",
        config=_config(),
    )

    payload = json.loads(result)
    assert payload["status"] == "completed"
    expected_staged = "inputs/project/" + record["sha256"][:16] + "-observations.csv"
    assert harness.calls[0]["input_refs"] == [expected_staged]
    staged = tmp_path / expected_staged
    assert staged.read_bytes() == project_source.read_bytes()
    bindings = payload["harness_evidence"]["staged_input_bindings"]
    assert bindings[0]["source_ref"] == virtual_ref
    assert bindings[0]["staged_ref"] == expected_staged
    receipt = tmp_path / bindings[0]["receipt_ref"]
    assert json.loads(receipt.read_text(encoding="utf-8"))["status"] == "verified"


def test_data_agent_rejects_normalized_escape_from_solar_data_directory(
    monkeypatch, tmp_path: Path
):
    import jw.tools.solar_feature as solar_feature

    _write_task_metadata(tmp_path, "question")
    _write_data_context(tmp_path, "question")
    (tmp_path / "work" / "solar_data").mkdir(parents=True)
    harness = _FakeHarness()
    monkeypatch.setattr(
        solar_feature, "workspace_root_from_config", lambda config: tmp_path
    )
    monkeypatch.setattr(
        solar_feature, "_qwen_harness_client", lambda model=None: harness
    )
    monkeypatch.setattr(solar_feature, "_eligible_input_records", lambda config: [])

    result = solar_feature.solar_research_analysis.func(
        research_question="",
        focus=_authoritative_focus("question"),
        input_refs=["work/solar_data/../../task.json"],
        instructions="compute a slope",
        config=_config(),
    )

    payload = json.loads(result)
    assert payload["status"] == "error"
    assert (
        "receipt" in payload["error_message"]
        or "eligible input" in payload["error_message"]
    )
    assert harness.calls == []


def test_data_agent_rejects_existing_generated_file_without_current_producer_receipt(
    monkeypatch, tmp_path: Path
):
    import jw.tools.solar_feature as solar_feature

    _write_task_metadata(tmp_path, "question")
    _write_data_context(tmp_path, "question")
    generated = tmp_path / "work" / "solar_data" / "generated.csv"
    generated.parent.mkdir(parents=True)
    generated.write_text("cycle,value\n24,115\n", encoding="utf-8")
    harness = _FakeHarness()
    monkeypatch.setattr(
        solar_feature, "workspace_root_from_config", lambda config: tmp_path
    )
    monkeypatch.setattr(
        solar_feature, "_qwen_harness_client", lambda model=None: harness
    )
    monkeypatch.setattr(solar_feature, "_eligible_input_records", lambda config: [])

    result = solar_feature.solar_research_analysis.func(
        research_question="",
        focus=_authoritative_focus("question"),
        input_refs=["work/solar_data/generated.csv"],
        instructions="compute a slope",
        config=_config(),
    )

    payload = json.loads(result)
    assert payload["status"] == "error"
    assert "producer receipt" in payload["error_message"]
    assert harness.calls == []


def test_data_agent_accepts_generated_file_declared_by_current_producer_receipt(
    monkeypatch, tmp_path: Path
):
    import jw.tools.solar_feature as solar_feature

    _write_task_metadata(tmp_path, "question")
    _write_data_context(tmp_path, "question")
    generated = tmp_path / "work" / "solar_data" / "generated.csv"
    generated.parent.mkdir(parents=True)
    generated.write_text("cycle,value\n24,115\n", encoding="utf-8")
    _write_generated_output_receipt(tmp_path, "work/solar_data/generated.csv")
    harness = _FakeHarness()
    monkeypatch.setattr(
        solar_feature, "workspace_root_from_config", lambda config: tmp_path
    )
    monkeypatch.setattr(
        solar_feature, "_qwen_harness_client", lambda model=None: harness
    )
    monkeypatch.setattr(solar_feature, "_eligible_input_records", lambda config: [])

    result = solar_feature.solar_research_analysis.func(
        research_question="",
        focus=_authoritative_focus("question"),
        input_refs=["work/solar_data/generated.csv"],
        instructions="compute a slope",
        config=_config(),
    )

    payload = json.loads(result)
    assert payload["status"] == "completed"
    assert harness.calls[0]["research_question"] == "question"
    assert harness.calls[0]["focus"] == _authoritative_focus("question")


def test_data_agent_rejects_runtime_thread_that_differs_from_task_metadata(
    monkeypatch, tmp_path: Path
):
    import jw.tools.solar_feature as solar_feature

    _write_task_metadata(tmp_path)
    _write_data_context(tmp_path)
    harness = _FakeHarness()
    monkeypatch.setattr(
        solar_feature, "workspace_root_from_config", lambda config: tmp_path
    )
    monkeypatch.setattr(
        solar_feature, "_qwen_harness_client", lambda model=None: harness
    )

    result = solar_feature.solar_research_evidence.func(
        focus=_authoritative_focus("Can polar fields predict the next cycle?"),
        queries=["polar field precursor solar cycle"],
        config={"configurable": {"thread_id": "wrong-task"}},
    )

    payload = json.loads(result)
    assert payload["status"] == "error"
    assert "thread_id" in payload["error_message"]
    assert harness.calls == []


def _install_accepted_plan_for_data_context(
    monkeypatch,
    root: Path,
    *,
    selected_source_ids: list[str],
    eligible_inputs: list[dict[str, str]],
) -> None:
    import jw.research_review as research_review
    import jw.tools.solar_feature as solar_feature

    question = "Can the polar field predict the next cycle?"
    _write_task_metadata(root, question)
    (root / "input_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "task-input-manifest-v1",
                "thread_id": "task-1",
                "inputs": [],
                "project_inputs": [],
            }
        ),
        encoding="utf-8",
    )
    plan_ref = "planner/runs/current/research_plan.json"
    plan_path = root / plan_ref
    plan_path.parent.mkdir(parents=True)
    required_datasets = (
        [
            {
                "id": f"D{index}",
                "selected_source_id": source_id,
                "purpose": "Accepted task dataset.",
            }
            for index, source_id in enumerate(selected_source_ids, start=1)
        ]
        if selected_source_ids
        else [{"id": "D1", "description": "Planner left source selection open."}]
    )
    plan = {
        "schema_version": "research-plan-v1",
        "research_question": question,
        "required_datasets": required_datasets,
        "research_route": [
            {"id": "R1", "stage": "data", "objective": "Open accepted inputs."}
        ],
        "research_artifacts": [],
    }
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    planning = {
        "payload": {
            "source_manifest": [
                {
                    "source_ref": plan_ref,
                    "sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
                }
            ]
        }
    }

    class _Store:
        task_id = "task-1"

        @staticmethod
        def latest_artifact(stage: str):
            return planning if stage == "planning" else None

        @staticmethod
        def matching_verdict(_stage: str, _refs: list[dict]):
            return {
                "decision": "accept",
                "review_id": "planning-review",
                "verdict_sha256": "a" * 64,
            }

        @staticmethod
        def artifact_ref(_artifact: dict) -> dict:
            return {
                "artifact_id": "planning-artifact",
                "version": 1,
                "artifact_sha256": "b" * 64,
            }

    monkeypatch.setattr(
        solar_feature, "workspace_root_from_config", lambda _config: root
    )
    monkeypatch.setattr(
        solar_feature, "_eligible_input_records", lambda _config: eligible_inputs
    )
    monkeypatch.setattr(research_review, "store_from_config", lambda _config: _Store())


def test_full_research_context_prefers_accepted_plan_dataset_order(
    monkeypatch, tmp_path: Path
) -> None:
    import jw.tools.solar_feature as solar_feature

    selected = ["mwo-wso-polar-field-v2", "silso-monthly-total-v2"]
    _install_accepted_plan_for_data_context(
        monkeypatch,
        tmp_path,
        selected_source_ids=selected,
        eligible_inputs=[
            {
                "path": "/project/polar.csv",
                "dataset_id": selected[0],
                "sha256": "c" * 64,
            },
            {
                "path": "/project/silso.txt",
                "dataset_id": selected[1],
                "sha256": "d" * 64,
            },
        ],
    )

    result = json.loads(
        solar_feature.solar_data_open_context.func(
            analysis_protocol="solar_polar_precursor_v1", config=_config()
        )
    )

    assert result["status"] == "inputs_available"
    assert result["required_dataset_ids"] == selected


def test_full_research_context_rejects_plan_protocol_dataset_conflict(
    monkeypatch, tmp_path: Path
) -> None:
    import jw.tools.solar_feature as solar_feature

    _install_accepted_plan_for_data_context(
        monkeypatch,
        tmp_path,
        selected_source_ids=["required-solar-v1"],
        eligible_inputs=[
            {
                "path": "/project/required.csv",
                "dataset_id": "required-solar-v1",
                "sha256": "c" * 64,
            }
        ],
    )

    result = json.loads(
        solar_feature.solar_data_open_context.func(
            analysis_protocol="solar_polar_precursor_v1", config=_config()
        )
    )

    assert result["status"] == "error"
    assert "Data semantics" in result["error_message"]
    assert list((tmp_path / "receipts" / "datasets").glob("data-context-*.json")) == []


def test_full_research_context_falls_back_when_plan_has_no_selected_source(
    monkeypatch, tmp_path: Path
) -> None:
    import jw.tools.solar_feature as solar_feature

    expected = ["silso-monthly-total-v2", "mwo-wso-polar-field-v2"]
    _install_accepted_plan_for_data_context(
        monkeypatch,
        tmp_path,
        selected_source_ids=[],
        eligible_inputs=[
            {
                "path": f"/project/{dataset_id}",
                "dataset_id": dataset_id,
                "sha256": digest * 64,
            }
            for dataset_id, digest in zip(expected, ("c", "d"), strict=True)
        ],
    )

    result = json.loads(
        solar_feature.solar_data_open_context.func(
            analysis_protocol="solar_polar_precursor_v1", config=_config()
        )
    )

    assert result["status"] == "inputs_available"
    assert result["required_dataset_ids"] == expected


def test_full_research_context_persists_canonical_required_dataset_state(
    monkeypatch, tmp_path: Path
) -> None:
    import jw.research_review as research_review
    import jw.tools.solar_feature as solar_feature

    question = (
        "Can polar fields in cycles 14-23 predict amplitudes for "
        "the following cycles 15-24?"
    )
    _write_task_metadata(tmp_path, question)
    (tmp_path / "input_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "task-input-manifest-v1",
                "thread_id": "task-1",
                "inputs": [],
                "project_inputs": [],
            }
        ),
        encoding="utf-8",
    )
    plan_ref = "planner/runs/current/research_plan.json"
    plan_path = tmp_path / plan_ref
    plan_path.parent.mkdir(parents=True)
    plan = {
        "schema_version": "research-plan-v1",
        "research_question": question,
        "required_datasets": [
            {"id": "D1", "description": "monthly sunspot series"},
            {"id": "D2", "description": "polar-field series"},
        ],
        "research_route": [
            {"id": "R1", "stage": "data", "objective": "Build cycle pairs."}
        ],
        "research_artifacts": [],
    }
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    planning = {
        "payload": {
            "source_manifest": [
                {
                    "source_ref": plan_ref,
                    "sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
                }
            ]
        }
    }

    class _Store:
        task_id = "task-1"

        @staticmethod
        def latest_artifact(stage: str):
            return planning if stage == "planning" else None

        @staticmethod
        def matching_verdict(stage: str, refs: list[dict]):
            assert stage == "planning"
            assert refs
            return {
                "decision": "accept",
                "review_id": "planning-review",
                "verdict_sha256": "a" * 64,
            }

        @staticmethod
        def artifact_ref(_artifact: dict) -> dict:
            return {
                "artifact_id": "planning-artifact",
                "version": 1,
                "artifact_sha256": "b" * 64,
            }

    eligible = [
        {
            "path": "/project/silso.txt",
            "dataset_id": "silso-monthly-total-v2",
            "sha256": "c" * 64,
        },
        {
            "path": "/project/polar.csv",
            "dataset_id": "mwo-wso-polar-field-v2",
            "sha256": "d" * 64,
        },
    ]
    monkeypatch.setattr(
        solar_feature, "workspace_root_from_config", lambda _config: tmp_path
    )
    monkeypatch.setattr(
        solar_feature, "_eligible_input_records", lambda _config: eligible
    )
    monkeypatch.setattr(research_review, "store_from_config", lambda _config: _Store())

    result = json.loads(
        solar_feature.solar_data_open_context.func(
            analysis_protocol="solar_polar_precursor_v1", config=_config()
        )
    )

    assert result["status"] == "inputs_available"
    assert result["required_dataset_ids"] == [
        "silso-monthly-total-v2",
        "mwo-wso-polar-field-v2",
    ]
    assert result["missing_required_dataset_ids"] == []
    assert result["must_stop"] is False
    receipt = json.loads((tmp_path / result["receipt_ref"]).read_text(encoding="utf-8"))
    assert receipt["required_dataset_ids"] == result["required_dataset_ids"]
    assert receipt["missing_required_dataset_ids"] == []
    assert receipt["must_stop"] is False


def test_full_research_context_uses_plan_selected_dataset_when_protocol_is_none(
    monkeypatch, tmp_path: Path
) -> None:
    import jw.research_review as research_review
    import jw.tools.solar_feature as solar_feature

    question = "Analyze the selected solar dataset."
    _write_task_metadata(tmp_path, question)
    (tmp_path / "input_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "task-input-manifest-v1",
                "thread_id": "task-1",
                "inputs": [],
                "project_inputs": [],
            }
        ),
        encoding="utf-8",
    )
    plan_ref = "planner/runs/current/research_plan.json"
    plan_path = tmp_path / plan_ref
    plan_path.parent.mkdir(parents=True)
    plan = {
        "schema_version": "research-plan-v1",
        "research_question": question,
        "required_datasets": [
            {
                "id": "D1",
                "selected_source_id": "required-solar-v1",
                "purpose": "Required selected observation series.",
            }
        ],
        "research_route": [{"id": "R1", "stage": "data", "objective": "Inspect D1."}],
        "research_artifacts": [],
    }
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    planning = {
        "payload": {
            "source_manifest": [
                {
                    "source_ref": plan_ref,
                    "sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
                }
            ]
        }
    }

    class _Store:
        task_id = "task-1"

        @staticmethod
        def latest_artifact(stage: str):
            return planning if stage == "planning" else None

        @staticmethod
        def matching_verdict(_stage: str, _refs: list[dict]):
            return {
                "decision": "accept",
                "review_id": "planning-review",
                "verdict_sha256": "a" * 64,
            }

        @staticmethod
        def artifact_ref(_artifact: dict) -> dict:
            return {
                "artifact_id": "planning-artifact",
                "version": 1,
                "artifact_sha256": "b" * 64,
            }

    monkeypatch.setattr(
        solar_feature, "workspace_root_from_config", lambda _config: tmp_path
    )
    monkeypatch.setattr(
        solar_feature,
        "_eligible_input_records",
        lambda _config: [
            {
                "path": "/project/unrelated.csv",
                "dataset_id": "unrelated-v1",
                "sha256": "c" * 64,
            }
        ],
    )
    monkeypatch.setattr(research_review, "store_from_config", lambda _config: _Store())

    result = json.loads(
        solar_feature.solar_data_open_context.func(
            analysis_protocol="none", config=_config()
        )
    )

    assert result["required_dataset_ids"] == ["required-solar-v1"]
    assert result["missing_required_dataset_ids"] == ["required-solar-v1"]
    assert result["status"] == "input_missing"
    assert result["must_stop"] is True


@pytest.mark.parametrize(
    ("receipt_overrides", "case"),
    [
        ({"schema_version": None}, "missing schema"),
        ({"schema_version": "harness-evidence-v1"}, "wrong schema"),
        ({"receipt_type": "unrecognized_output"}, "wrong receipt type"),
        ({"producer": "solar-evidence"}, "wrong producer"),
        ({"task_id": "old-task"}, "old task"),
    ],
)
def test_data_agent_rejects_unrecognized_or_wrong_task_output_receipt(
    monkeypatch,
    tmp_path: Path,
    receipt_overrides: dict[str, str | None],
    case: str,
):
    import jw.tools.solar_feature as solar_feature

    _write_task_metadata(tmp_path, "question")
    _write_data_context(tmp_path, "question")
    generated = tmp_path / "work" / "solar_data" / "generated.csv"
    generated.parent.mkdir(parents=True)
    generated.write_text("cycle,value\n24,115\n", encoding="utf-8")
    receipt = {
        "schema_version": "research-dataset-receipt-v1",
        "receipt_type": "silso_cycle_extrema_reproduction",
        "status": "verified",
        "producer": "solar-data",
        "task_id": "task-1",
    }
    receipt.update(receipt_overrides)
    _write_generated_output_receipt(
        tmp_path,
        "work/solar_data/generated.csv",
        task_id=receipt["task_id"],
        schema_version=receipt["schema_version"],
        receipt_type=receipt["receipt_type"],
        producer=receipt["producer"],
    )
    harness = _FakeHarness()
    monkeypatch.setattr(
        solar_feature, "workspace_root_from_config", lambda config: tmp_path
    )
    monkeypatch.setattr(solar_feature, "_eligible_input_records", lambda config: [])
    monkeypatch.setattr(
        solar_feature, "_qwen_harness_client", lambda model=None: harness
    )

    result = solar_feature.solar_research_analysis.func(
        focus=_authoritative_focus("question"),
        input_refs=["work/solar_data/generated.csv"],
        instructions=f"reject {case}",
        config=_config(),
    )

    payload = json.loads(result)
    assert payload["status"] == "error"
    assert "producer receipt" in payload["error_message"]
    assert harness.calls == []


def test_data_agent_rejects_nested_path_hash_in_unrelated_receipt(
    monkeypatch, tmp_path: Path
):
    import jw.tools.solar_feature as solar_feature

    _write_task_metadata(tmp_path, "question")
    _write_data_context(tmp_path, "question")
    generated = tmp_path / "work" / "solar_data" / "generated.csv"
    generated.parent.mkdir(parents=True)
    generated.write_text("cycle,value\n24,115\n", encoding="utf-8")
    receipt = tmp_path / "receipts" / "harness" / "unrelated.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(
        json.dumps(
            {
                "schema_version": "harness-evidence-v1",
                "task_id": "task-1",
                "binding": {
                    "forged": {
                        "path": "work/solar_data/generated.csv",
                        "sha256": hashlib.sha256(generated.read_bytes()).hexdigest(),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    harness = _FakeHarness()
    monkeypatch.setattr(
        solar_feature, "workspace_root_from_config", lambda config: tmp_path
    )
    monkeypatch.setattr(solar_feature, "_eligible_input_records", lambda config: [])
    monkeypatch.setattr(
        solar_feature, "_qwen_harness_client", lambda model=None: harness
    )

    result = solar_feature.solar_research_analysis.func(
        focus=_authoritative_focus("question"),
        input_refs=["work/solar_data/generated.csv"],
        instructions="reject nested forged declaration",
        config=_config(),
    )

    payload = json.loads(result)
    assert payload["status"] == "error"
    assert "producer receipt" in payload["error_message"]
    assert harness.calls == []


def test_data_agent_rejects_symlink_output_even_when_target_is_inside_workdir(
    monkeypatch, tmp_path: Path
):
    import jw.tools.solar_feature as solar_feature

    _write_task_metadata(tmp_path, "question")
    _write_data_context(tmp_path, "question")
    target = tmp_path / "work" / "solar_data" / "target.csv"
    target.parent.mkdir(parents=True)
    target.write_text("cycle,value\n24,115\n", encoding="utf-8")
    linked = target.with_name("linked.csv")
    linked.symlink_to(target.name)
    _write_generated_output_receipt(tmp_path, "work/solar_data/target.csv")
    harness = _FakeHarness()
    monkeypatch.setattr(
        solar_feature, "workspace_root_from_config", lambda config: tmp_path
    )
    monkeypatch.setattr(solar_feature, "_eligible_input_records", lambda config: [])
    monkeypatch.setattr(
        solar_feature, "_qwen_harness_client", lambda model=None: harness
    )

    result = solar_feature.solar_research_analysis.func(
        focus=_authoritative_focus("question"),
        input_refs=["work/solar_data/linked.csv"],
        instructions="reject symlink",
        config=_config(),
    )

    payload = json.loads(result)
    assert payload["status"] == "error"
    assert "producer receipt" in payload["error_message"]
    assert harness.calls == []


def test_data_agent_rejects_output_reached_through_symlink_directory(
    monkeypatch, tmp_path: Path
):
    import jw.tools.solar_feature as solar_feature

    _write_task_metadata(tmp_path, "question")
    _write_data_context(tmp_path, "question")
    real_dir = tmp_path / "work" / "solar_data" / "real"
    real_dir.mkdir(parents=True)
    target = real_dir / "target.csv"
    target.write_text("cycle,value\n24,115\n", encoding="utf-8")
    real_dir.with_name("linked").symlink_to(real_dir.name, target_is_directory=True)
    _write_generated_output_receipt(tmp_path, "work/solar_data/real/target.csv")
    harness = _FakeHarness()
    monkeypatch.setattr(
        solar_feature, "workspace_root_from_config", lambda config: tmp_path
    )
    monkeypatch.setattr(solar_feature, "_eligible_input_records", lambda config: [])
    monkeypatch.setattr(
        solar_feature, "_qwen_harness_client", lambda model=None: harness
    )

    result = solar_feature.solar_research_analysis.func(
        focus=_authoritative_focus("question"),
        input_refs=["work/solar_data/linked/target.csv"],
        instructions="reject directory symlink",
        config=_config(),
    )

    payload = json.loads(result)
    assert payload["status"] == "error"
    assert "producer receipt" in payload["error_message"]
    assert harness.calls == []


@pytest.mark.parametrize(
    "tool_name", ["solar_research_evidence", "solar_research_analysis"]
)
def test_data_harness_rejects_focus_drift(monkeypatch, tmp_path: Path, tool_name: str):
    import jw.tools.solar_feature as solar_feature

    _write_task_metadata(tmp_path, "question")
    _write_data_context(tmp_path, "question")
    accepted = tmp_path / "inputs" / "accepted.csv"
    accepted.parent.mkdir()
    accepted.write_text("cycle,value\n24,115\n", encoding="utf-8")
    harness = _FakeHarness()
    monkeypatch.setattr(
        solar_feature, "workspace_root_from_config", lambda config: tmp_path
    )
    monkeypatch.setattr(
        solar_feature, "_qwen_harness_client", lambda model=None: harness
    )
    monkeypatch.setattr(
        solar_feature,
        "_eligible_input_records",
        lambda config: [{"path": "inputs/accepted.csv", "sha256": "abc"}],
    )

    if tool_name == "solar_research_evidence":
        result = solar_feature.solar_research_evidence.func(
            focus="unrelated flare forecasting",
            queries=["flare forecasting"],
            config=_config(),
        )
    else:
        result = solar_feature.solar_research_analysis.func(
            focus="unrelated flare forecasting",
            input_refs=["inputs/accepted.csv"],
            instructions="fit an unrelated model",
            config=_config(),
        )

    payload = json.loads(result)
    assert payload["status"] == "error"
    assert "focus" in payload["error_message"]
    assert harness.calls == []


@pytest.mark.parametrize(
    ("tool_name", "status", "must_stop"),
    [
        ("solar_research_evidence", "input_missing", False),
        ("solar_research_analysis", "input_missing", False),
        ("solar_research_evidence", "inputs_available", True),
        ("solar_research_analysis", "inputs_available", True),
    ],
)
def test_data_harness_rejects_non_runnable_context_before_client_construction(
    monkeypatch,
    tmp_path: Path,
    tool_name: str,
    status: str,
    must_stop: bool,
):
    import jw.tools.solar_feature as solar_feature

    _write_task_metadata(tmp_path, "question")
    _write_data_context(
        tmp_path,
        "question",
        status=status,
        must_stop=must_stop,
    )
    harness = _FakeHarness()
    client_builds: list[str | None] = []

    def _client(model=None):
        client_builds.append(model)
        return harness

    monkeypatch.setattr(
        solar_feature, "workspace_root_from_config", lambda config: tmp_path
    )
    monkeypatch.setattr(solar_feature, "_qwen_harness_client", _client)
    monkeypatch.setattr(
        solar_feature,
        "_eligible_input_records",
        lambda config: [{"path": "inputs/accepted.csv", "sha256": "abc"}],
    )

    if tool_name == "solar_research_evidence":
        result = solar_feature.solar_research_evidence.func(
            queries=["polar field precursor"],
            config=_config(),
        )
    else:
        result = solar_feature.solar_research_analysis.func(
            input_refs=["inputs/accepted.csv"],
            instructions="compute the task quantity",
            config=_config(),
        )

    payload = json.loads(result)
    assert payload["status"] == "error"
    assert "Data context" in payload["error_message"]
    assert client_builds == []
    assert harness.calls == []


@pytest.mark.parametrize(
    ("omitted_hashes", "hash_overrides", "stale_manifest"),
    [
        ({"task_sha256"}, {}, False),
        ({"research_question_sha256"}, {}, False),
        ({"input_manifest_sha256"}, {}, False),
        (set(), {"task_sha256": None}, False),
        (set(), {"research_question_sha256": 7}, False),
        (set(), {"input_manifest_sha256": "not-a-sha256"}, False),
        (set(), {}, True),
    ],
)
def test_data_harness_rejects_missing_malformed_or_stale_context_hashes(
    monkeypatch,
    tmp_path: Path,
    omitted_hashes: set[str],
    hash_overrides: dict[str, object],
    stale_manifest: bool,
):
    import jw.tools.solar_feature as solar_feature

    _write_task_metadata(tmp_path, "question")
    _write_data_context(
        tmp_path,
        "question",
        omitted_hashes=omitted_hashes,
        hash_overrides=hash_overrides,
    )
    if stale_manifest:
        manifest = tmp_path / "input_manifest.json"
        manifest.write_text(
            manifest.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
    harness = _FakeHarness()
    client_builds: list[str | None] = []

    def _client(model=None):
        client_builds.append(model)
        return harness

    monkeypatch.setattr(
        solar_feature, "workspace_root_from_config", lambda config: tmp_path
    )
    monkeypatch.setattr(solar_feature, "_qwen_harness_client", _client)

    result = solar_feature.solar_research_evidence.func(
        queries=["polar field precursor"],
        config=_config(),
    )

    payload = json.loads(result)
    assert payload["status"] == "error"
    assert "Data context" in payload["error_message"]
    assert client_builds == []
    assert harness.calls == []


def test_precursor_table_receipt_is_bound_to_current_data_task(
    monkeypatch, tmp_path: Path
):
    import jw.tools.solar_feature as solar_feature

    _write_task_metadata(tmp_path, "question")
    sunspot = tmp_path / "inputs" / "sunspot.txt"
    polar = tmp_path / "inputs" / "polar.csv"
    sunspot.parent.mkdir()
    sunspot.write_text("source", encoding="utf-8")
    polar.write_text("source", encoding="utf-8")
    records = [
        {
            "path": "inputs/sunspot.txt",
            "dataset_id": "silso-monthly-total-v2",
            "sha256": "a" * 64,
        },
        {
            "path": "inputs/polar.csv",
            "dataset_id": "mwo-wso-polar-field-v2",
            "sha256": "b" * 64,
        },
    ]
    monkeypatch.setattr(
        solar_feature, "workspace_root_from_config", lambda config: tmp_path
    )
    monkeypatch.setattr(
        solar_feature, "_eligible_input_records", lambda config: records
    )
    monkeypatch.setattr(
        solar_feature,
        "_resolve_eligible_data_path",
        lambda value, config: tmp_path / value,
    )
    monkeypatch.setattr(
        solar_feature,
        "_build_solar_precursor_cycle_rows",
        lambda sunspot_path, polar_path: [
            {
                "row_role": "analysis",
                "cycle_number": 24,
                "polar_field_proxy_gauss": 1.15,
                "polar_field_proxy_sem_gauss": 0.08,
                "predictor_window_complete": True,
                "predictor_window_start_decimal_year": 2019.5,
                "predictor_window_end_decimal_year": 2020.5,
                "predictor_cutoff_decimal_year": 2020.5,
                "north_source": "WSO",
                "south_source": "WSO",
            }
        ],
    )

    result = json.loads(
        solar_feature.prepare_solar_precursor_cycle_table.func(
            sunspot_path="inputs/sunspot.txt",
            polar_field_path="inputs/polar.csv",
            config=_config(),
        )
    )

    assert result["status"] == "verified"
    receipt = json.loads(
        (tmp_path / "receipts/datasets/solar_precursor_cycle_table.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["receipt_type"] == "solar_precursor_cycle_table"
    assert receipt["producer"] == "solar-data"
    assert receipt["task_id"] == "task-1"
    assert receipt["feature_records"][0]["status"] == "available"
    assert receipt["feature_records"][0]["hypothesis_id"] == "h2_polar_precursor"
    assert receipt["unavailable_feature_records"][0]["status"] == "blocked_by_data"
    assert result["status"] == "verified"
    assert result["hypothesis_data_status"] == {
        "h2_polar_precursor": "available",
        "h3_axial_dipole_discriminator": "blocked_by_data",
    }


def test_solar_feature_bundle_exposes_harness_without_reserved_names():
    import jw.tools.solar_feature as solar_feature

    names = {tool.name for tool in solar_feature.SOLAR_FEATURE_TOOLS}
    assert {"solar_research_evidence", "solar_research_analysis"} <= names
    assert not names.intersection({"search", "code_interpreter"})
