from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from jw.middleware import closed_loop_orchestration as guard_module
from jw.middleware.closed_loop_orchestration import (
    ClosedLoopOrchestrationGuardMiddleware,
    closed_loop_receipts,
)


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


def test_blocks_hypothesis_before_real_planner_freeze(
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
    called = False

    def handler(_request: _Request):
        nonlocal called
        called = True
        return object()

    result = middleware.wrap_tool_call(request, handler)

    assert called is False
    assert result.status == "error"
    assert "frozen planner artifact" in str(result.content)


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


def test_blocks_false_completed_todo_and_false_receipt(
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
    todo_result = middleware.wrap_tool_call(todo_request, lambda _request: object())
    assert todo_result.status == "error"
    assert "cannot mark solar-hypothesis completed" in str(todo_result.content)

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


def test_finalized_experiment_requires_hashes_and_entry_result(tmp_path: Path) -> None:
    run = tmp_path / "experiment/runs/exp-1"
    _json(
        run / "state.json",
        {
            "phase": "report_finalized",
            "verified_record_sha256": "record-sha",
            "report_sha256": "report-sha",
        },
    )
    assert closed_loop_receipts(tmp_path)["solar-experiment"] is None

    _json(run / "entry_result.json", {"status": "finalized"})
    assert closed_loop_receipts(tmp_path)["solar-experiment"] == run / "state.json"


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
    assert result.status == "error"
    assert preflight_thread is not None
    assert preflight_thread != event_loop_thread
