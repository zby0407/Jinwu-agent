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
    assert result is not None
    assert preflight_thread is not None
    assert preflight_thread != event_loop_thread
