from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from jw.middleware import closed_loop_orchestration as guard_module
from jw.middleware.closed_loop_orchestration import (
    ClosedLoopOrchestrationGuardMiddleware,
    closed_loop_receipts,
)
from jw.research_protocols import sha256_file
from jw.research_review import ResearchReviewStore


@dataclass
class _Runtime:
    config: dict[str, object]


class _Request:
    def __init__(self, tool_call: dict[str, object], state: dict[str, object]) -> None:
        self.tool_call = tool_call
        self.state = state
        self.runtime = _Runtime(config={})


def _state() -> dict[str, object]:
    return {
        "messages": [
            {
                "type": "human",
                "content": (
                    "依次委派 solar-planner、solar-hypothesis、"
                    "solar-experiment 完成科研闭环。"
                ),
            }
        ]
    }


def _json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _finalized_experiment(
    root: Path,
    *,
    measurement_ids: tuple[str, ...] = (),
) -> Path:
    run = root / "experiment" / "runs" / "exp-1"
    record = {
        "measurements": [
            {"measurement_id": measurement_id, "value": 1.0}
            for measurement_id in measurement_ids
        ]
    }
    _json(run / "record.json", record)
    report_text = "# Result\n\nVerified result.\n"
    audit_text = "# Audit\n\nVerified audit.\n"
    (run / "report.md").write_text(report_text, encoding="utf-8")
    (run / "audit.md").write_text(audit_text, encoding="utf-8")
    entry = {
        "schema_version": "automatic-experiment-entry-result-v1",
        "status": "finalized",
        "run_id": "exp-1",
        "outcome": "completed_interpretable",
        "record_path": "record.json",
        "record_sha256": sha256_file(run / "record.json"),
        "report_path": "report.md",
        "report_sha256": sha256_file(run / "report.md"),
        "audit_path": "audit.md",
        "audit_sha256": sha256_file(run / "audit.md"),
        "report_assets": [],
        "user_display_markdown": report_text,
        "safe_next_action": "none",
        "created_at": "2026-07-30T00:00:00Z",
    }
    canonical_entry = json.dumps(
        entry,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    entry["entry_sha256"] = hashlib.sha256(canonical_entry.encode("utf-8")).hexdigest()
    _json(run / "entry_result.json", entry)
    _json(
        run / "state.json",
        {
            "run_id": "exp-1",
            "phase": "report_finalized",
            "outcome": "completed_interpretable",
            "verified_record_sha256": sha256_file(run / "record.json"),
            "report_sha256": entry["report_sha256"],
            "audit_sha256": entry["audit_sha256"],
            "report_assets": [],
        },
    )
    return run


def test_allows_hypothesis_without_planner_freeze(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        guard_module, "workspace_root_from_config", lambda _config: tmp_path
    )
    middleware = ClosedLoopOrchestrationGuardMiddleware()
    request = _Request(
        {
            "name": "task",
            "id": "call-hypothesis",
            "args": {"subagent_type": "solar-hypothesis"},
        },
        _state(),
    )
    sentinel = object()

    result = middleware.wrap_tool_call(request, lambda _request: sentinel)

    assert result is sentinel


def test_historical_stage_acceptance_is_not_a_release_receipt(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "task-1")
    artifact = store.checkpoint_producer_result(
        stage="planning", producer="solar-planner", content="bounded plan"
    )
    store.submit_verdict(
        mode="planning",
        decision="accept",
        issues=[],
        accepted_claims=[artifact["claims"][0]["claim_id"]],
    )

    assert closed_loop_receipts(tmp_path)["solar-evidence"] is None


def test_allows_hypothesis_after_real_planner_freeze(
    tmp_path: Path, monkeypatch
) -> None:
    _json(
        tmp_path / "planner/runs/plan-1/research_plan.json",
        {"status": "frozen"},
    )
    monkeypatch.setattr(
        guard_module, "workspace_root_from_config", lambda _config: tmp_path
    )
    middleware = ClosedLoopOrchestrationGuardMiddleware()
    request = _Request(
        {
            "name": "task",
            "id": "call-hypothesis",
            "args": {"subagent_type": "solar-hypothesis"},
        },
        _state(),
    )
    sentinel = object()

    assert middleware.wrap_tool_call(request, lambda _request: sentinel) is sentinel


def test_real_data_experiment_still_requires_staged_input(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        guard_module, "workspace_root_from_config", lambda _config: tmp_path
    )
    middleware = ClosedLoopOrchestrationGuardMiddleware()
    state = {
        "messages": [
            {
                "type": "human",
                "content": (
                    "请基于现有 CSV 数据完成完整研究、计划、假设、实验和报告。"
                ),
            }
        ]
    }
    request = _Request(
        {
            "name": "task",
            "id": "call-experiment",
            "args": {
                "subagent_type": "solar-experiment",
                "description": "run the experiment",
            },
        },
        state,
    )

    result = middleware.wrap_tool_call(request, lambda _request: object())

    assert result.status == "error"
    assert "no immutable input is staged" in str(result.content)


def test_router_selected_full_research_activates_closed_loop_guard(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        guard_module, "workspace_root_from_config", lambda _config: tmp_path
    )
    middleware = ClosedLoopOrchestrationGuardMiddleware()
    state = {
        "research_route": {
            "mode": "full_research",
            "source_mode": "local",
            "needs_computation": True,
        },
        "messages": [
            {
                "type": "human",
                "content": "请分析本地数据 CSV 并形成研究产物。",
            }
        ],
    }
    request = _Request(
        {
            "name": "task",
            "id": "call-experiment",
            "args": {
                "subagent_type": "solar-experiment",
                "description": "run the data experiment",
            },
        },
        state,
    )

    result = middleware.wrap_tool_call(request, lambda _request: object())

    assert result.status == "error"
    assert "no immutable input is staged" in str(result.content)


def test_allows_partial_todo_but_blocks_false_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    _json(
        tmp_path / "planner/runs/plan-1/research_plan.json",
        {"status": "frozen"},
    )
    monkeypatch.setattr(
        guard_module, "workspace_root_from_config", lambda _config: tmp_path
    )
    middleware = ClosedLoopOrchestrationGuardMiddleware()

    todo_request = _Request(
        {
            "name": "write_todos",
            "id": "call-todo",
            "args": {
                "todos": [
                    {
                        "content": "委派 solar-hypothesis 并取得 freeze",
                        "status": "completed",
                    }
                ]
            },
        },
        _state(),
    )
    todo_sentinel = object()
    todo_result = middleware.wrap_tool_call(
        todo_request, lambda _request: todo_sentinel
    )
    assert todo_result is todo_sentinel

    receipt_request = _Request(
        {
            "name": "write_file",
            "id": "call-receipt",
            "args": {
                "file_path": "/receipts/closed_loop_receipts.json",
                "content": json.dumps(
                    {
                        "contract_status": {
                            "solar-hypothesis": {"status": "freeze_success"}
                        }
                    }
                ),
            },
        },
        _state(),
    )
    receipt_result = middleware.wrap_tool_call(
        receipt_request, lambda _request: object()
    )
    assert receipt_result.status == "error"
    assert "falsely claims solar-hypothesis success" in str(receipt_result.content)


def test_finalized_experiment_requires_hash_matched_bundle(tmp_path: Path) -> None:
    run = _finalized_experiment(tmp_path)

    assert closed_loop_receipts(tmp_path)["solar-experiment"] == run / "state.json"

    (run / "report.md").write_text("tampered\n", encoding="utf-8")
    assert closed_loop_receipts(tmp_path)["solar-experiment"] is None


def test_dataset_receipt_requires_hash_matched_canonical_artifact(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "work" / "canonical_f107_monthly.csv"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("date_month,f107\n1980-01-01,100\n", encoding="utf-8")
    _json(
        tmp_path / "receipts" / "datasets" / "f107_semantics.json",
        {
            "schema_version": 1,
            "status": "verified",
            "canonical_artifact": artifact.name,
            "canonical_sha256": sha256_file(artifact),
        },
    )

    assert (
        closed_loop_receipts(tmp_path)["solar-data"]
        == tmp_path / "receipts" / "datasets" / "f107_semantics.json"
    )

    artifact.write_text("tampered\n", encoding="utf-8")
    assert closed_loop_receipts(tmp_path)["solar-data"] is None


def test_experiment_receipt_requires_protocol_measurements(tmp_path: Path) -> None:
    run = _finalized_experiment(tmp_path, measurement_ids=("present",))

    assert (
        closed_loop_receipts(
            tmp_path,
            required_measurement_ids=("present",),
        )["solar-experiment"]
        == run / "state.json"
    )
    assert (
        closed_loop_receipts(
            tmp_path,
            required_measurement_ids=("present", "missing"),
        )["solar-experiment"]
        is None
    )


def test_async_preflight_runs_filesystem_checks_off_event_loop(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        guard_module, "workspace_root_from_config", lambda _config: tmp_path
    )
    middleware = ClosedLoopOrchestrationGuardMiddleware()
    request = _Request(
        {
            "name": "task",
            "id": "call-hypothesis",
            "args": {"subagent_type": "solar-hypothesis"},
        },
        _state(),
    )
    event_loop_thread: int | None = None
    preflight_thread: int | None = None
    original = middleware._preflight

    def recording_preflight(inner_request: _Request):
        nonlocal preflight_thread
        import threading

        preflight_thread = threading.get_ident()
        return original(inner_request)

    async def run() -> object:
        nonlocal event_loop_thread
        import threading

        event_loop_thread = threading.get_ident()
        monkeypatch.setattr(middleware, "_preflight", recording_preflight)

        async def handler(_request: _Request) -> object:
            return object()

        return await middleware.awrap_tool_call(request, handler)

    result = asyncio.run(run())
    assert result is not None
    assert preflight_thread is not None
    assert preflight_thread != event_loop_thread
