from __future__ import annotations

from pathlib import Path

import pytest

from jw.llm.models import validate_model_override
from jw.research_review import ResearchReviewStore
from research_review import execution_state
from research_review.execution_state import ExecutionStateStore


def test_stale_heartbeat_does_not_change_persisted_state(tmp_path: Path) -> None:
    store = ExecutionStateStore(tmp_path / "execution_state.json")
    store.start(
        stage="data",
        owner="solar-data",
        now="2026-08-28T00:00:00+00:00",
    )

    snapshot = store.snapshot(
        now="2026-08-28T00:10:00+00:00",
        stale_after_seconds=60,
    )

    assert snapshot is not None
    assert snapshot["status"] == "stopped"
    assert snapshot["reason"] == "heartbeat_stale"
    assert store._read()["status"] == "running"


def test_invalid_provider_is_rejected_before_model_resolution() -> None:
    with pytest.raises(ValueError, match="unsupported model provider"):
        validate_model_override("qwen3.8-max", "qwen")


def test_registered_short_model_rejects_incompatible_provider() -> None:
    with pytest.raises(ValueError, match="model/provider pair"):
        validate_model_override("qwen3.8-max", "openai")


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        ("waiting_for_tool", "waiting_for_tool"),
        ("interrupt", "interrupted"),
        ("fail", "failed"),
        ("stop", "stopped"),
    ],
)
def test_explicit_execution_transitions_persist(
    tmp_path: Path,
    method: str,
    expected: str,
) -> None:
    store = ExecutionStateStore(tmp_path / "execution_state.json")
    store.start(
        stage="data",
        owner="solar-data",
        now="2026-08-28T00:00:00+00:00",
    )
    getattr(store, method)(
        stage="data",
        owner="solar-data",
        reason="test_reason",
        now="2026-08-28T00:00:01+00:00",
    )

    snapshot = store.snapshot(now="2026-08-28T00:00:02+00:00")
    assert snapshot is not None
    assert snapshot["status"] == expected


def test_execution_state_uses_atomic_replace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[Path, Path]] = []
    real_replace = execution_state.os.replace

    def observed_replace(source: str | Path, destination: str | Path) -> None:
        calls.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(execution_state.os, "replace", observed_replace)
    target = tmp_path / "execution_state.json"

    ExecutionStateStore(target).start(stage="data", owner="solar-data")

    assert len(calls) == 1
    assert calls[0][1] == target
    assert calls[0][0].name.endswith(".tmp")


def test_repeated_progress_preserves_start_time(tmp_path: Path) -> None:
    store = ExecutionStateStore(tmp_path / "execution_state.json")
    first = store.progress(
        stage="data",
        owner="solar-data",
        action="inspect",
        now="2026-08-28T00:00:00+00:00",
    )
    second = store.progress(
        stage="data",
        owner="solar-data",
        action="inspect",
        now="2026-08-28T00:00:05+00:00",
    )

    assert second["started_at"] == first["started_at"]


def test_reserving_research_action_starts_execution_sidecar(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "execution-task")

    store.reserve_action(
        {
            "kind": "producer",
            "stage": "data",
            "producer": "solar-data",
        }
    )

    snapshot = store.execution_state.snapshot(stale_after_seconds=float("inf"))
    assert snapshot is not None
    assert snapshot["status"] == "running"
    assert snapshot["stage"] == "data"
    assert snapshot["owner"] == "solar-data"


def test_terminal_tool_failure_marks_execution_failed(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "execution-failure-task")
    store.reserve_action(
        {
            "kind": "producer",
            "stage": "data",
            "producer": "solar-data",
        }
    )

    store.block_for_tool_failures(
        stage="data",
        producer="solar-data",
        fingerprints=["a" * 64],
    )

    snapshot = store.execution_state.snapshot(stale_after_seconds=float("inf"))
    assert snapshot is not None
    assert snapshot["status"] == "failed"
    assert snapshot["reason"] == "required_specialist_failed_twice"
