"""Automatic experiment writeback must not turn technical failures into findings."""

from __future__ import annotations

import json
from pathlib import Path

from automatic_experiment import service as experiment_service
from knowledge_base.store import KnowledgeStore


def test_technical_failure_writeback_is_an_idempotent_lesson(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("JW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("JW_KB_EXPORT_DIR", str(tmp_path / "knowledge_base"))
    root = tmp_path / "run"
    root.mkdir()
    (root / "record.json").write_text(
        json.dumps(
            {
                "task": "执行参数扫描",
                "run_id": "run-technical-failure",
                "outcome": "technical_failure",
                "outcome_reason": "沙箱进程以非零状态退出。",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    state = {
        "run_id": "run-technical-failure",
        "outcome": "technical_failure",
        "last_error": "沙箱进程以非零状态退出。",
    }

    first = experiment_service._knowledge_writeback(root, state)
    second = experiment_service._knowledge_writeback(root, state)

    assert first["status"] == "ok"
    assert first["entry_ids"] == second["entry_ids"]

    store = KnowledgeStore()
    try:
        entry = store.get_entry(first["entry_ids"][0])
        assert entry["type"] == "lesson"
        assert entry["status"] == "candidate"
        assert entry["source_type"] == "historical_run"
        assert entry["content"]["failure_mode"] == "沙箱进程以非零状态退出。"
        assert not store._conn.execute(
            "SELECT id FROM entries WHERE type = 'finding' "
            "AND source_ref = 'run-technical-failure'"
        ).fetchone()
    finally:
        store.close()
