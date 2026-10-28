"""Deterministic growth-job regression tests (P2 foundation)."""

from __future__ import annotations

from pathlib import Path

import pytest

from knowledge_base import growth, service
from knowledge_base.store import KnowledgeStore


@pytest.fixture
def store(tmp_path: Path) -> KnowledgeStore:
    instance = KnowledgeStore(
        db_path=tmp_path / "knowledge.db",
        export_dir=tmp_path / "knowledge_base",
    )
    try:
        yield instance
    finally:
        instance.close()


def _propose_candidate(store: KnowledgeStore, title: str = "陈旧候选"):
    return service.propose(
        store,
        entry_type="finding",
        title=title,
        content={"statement": "一次运行的发现", "run_id": "run-x"},
        source_type="historical_run",
        source_ref="run-x",
        confidence="low",
    )["entry"]


def test_growth_cycle_is_idempotent_per_bucket(store: KnowledgeStore) -> None:
    first = growth.run_growth_cycle(store, bucket="2026-10-28")
    assert first["status"] == "ok"
    assert [job["status"] for job in first["jobs"]] == ["done", "done", "done"]

    second = growth.run_growth_cycle(store, bucket="2026-10-28")
    assert [job["status"] for job in second["jobs"]] == [
        "already_done",
        "already_done",
        "already_done",
    ]
    # A new bucket re-runs the sweep.
    third = growth.run_growth_cycle(store, bucket="2026-10-29")
    assert [job["status"] for job in third["jobs"]] == ["done", "done", "done"]


def test_health_check_reports_stale_candidates_and_conflicts(
    store: KnowledgeStore,
) -> None:
    stale = _propose_candidate(store)
    # Backdate the candidate so the staleness sweep sees it.
    row = store.get_entry(stale["id"])
    row["created_at"] = "2020-01-01T00:00:00+00:00"
    store.update_entry(row, changed_by="test", reason="backdate")

    canonical = service.propose(
        store,
        entry_type="mechanism",
        title="极区场决定下一周振幅",
        content={"claim": "极区场越强，下一活动周越强。"},
        source_type="literature",
        source_ref="doi:10.1000/target",
        confidence="medium",
    )["entry"]
    canonical["status"] = "canonical"
    canonical["version"] += 1
    store.update_entry(canonical, changed_by="test", reason="seed canonical")
    service.propose(
        store,
        entry_type="counterexample",
        title="独立运行未复现该机制",
        content={"statement": "未复现", "run_id": "run-cx"},
        source_type="historical_run",
        source_ref="run-cx",
        confidence="medium",
        related_ids=[canonical["id"]],
    )

    job = growth.enqueue_growth_job(store, "health_check", idempotency_key="hc-1")
    outcome = growth.run_growth_job(store, job["job_id"])
    assert outcome["status"] == "done"
    result = outcome["result"]
    counts = result["counts"]
    assert counts["stale_candidates"] >= 1
    assert counts["unresolved_conflicts"] == 1
    assert any(stale["id"] == row["id"] for row in result["stale_candidates"])
    assert any("conflict" in action for action in result["actions"])


def test_distill_queue_lists_undistilled_sources(store: KnowledgeStore) -> None:
    store.upsert_lit_source(
        {
            "source_id": "openalex:Wgrowth1",
            "title": "Polar field precursor review",
            "authors": ["A. Author"],
            "year": 2025,
            "abstract": "Polar field strength predicts next-cycle amplitude.",
            "is_preferred": 1,
            "is_retracted": 0,
        }
    )
    store.upsert_lit_source(
        {
            "source_id": "openalex:Wgrowth2",
            "title": "F10.7 proxy drift",
            "authors": ["B. Author"],
            "year": 2024,
            "abstract": "F10.7 drifts relative to sunspot number.",
            "is_preferred": 1,
            "is_retracted": 0,
            "distilled_entry_id": "kb_concept_already_001",
        }
    )

    job = growth.enqueue_growth_job(store, "distill_queue", idempotency_key="dq-1")
    outcome = growth.run_growth_job(store, job["job_id"])
    assert outcome["status"] == "done"
    queue = outcome["result"]["queue"]
    ids = [row["source_id"] for row in queue]
    assert "openalex:Wgrowth1" in ids
    assert "openalex:Wgrowth2" not in ids
    assert outcome["result"]["undistilled_total"] == 1


def test_feed_sync_requires_feed_id_and_records_receipt(
    store: KnowledgeStore, monkeypatch
) -> None:
    job = growth.enqueue_growth_job(
        store, "feed_sync", idempotency_key="fs-1", payload={}
    )
    outcome = growth.run_growth_job(store, job["job_id"])
    assert outcome["status"] == "error"
    assert "feed_id" in outcome["error"]
    # The failed job is requeued until attempts are exhausted.
    requeued = store.get_growth_job(job["job_id"])
    assert requeued["status"] == "pending"

    calls: list[str] = []

    def _fake_sync(_store, feed_id, *, limit=0, feeds_path=None):
        calls.append(feed_id)
        return {
            "status": "ok",
            "new_source_count": 2,
            "new_family_count": 1,
            "result_count": 5,
            "diagnostics": {},
        }

    monkeypatch.setattr("knowledge_base.literature.sync_literature_feed", _fake_sync)
    job2 = growth.enqueue_growth_job(
        store,
        "feed_sync",
        idempotency_key="fs-2",
        payload={"feed_id": "polar_field_precursor"},
    )
    outcome2 = growth.run_growth_job(store, job2["job_id"])
    assert outcome2["status"] == "done"
    assert outcome2["result"]["feed_id"] == "polar_field_precursor"
    assert outcome2["result"]["new_source_count"] == 2
    assert calls == ["polar_field_precursor"]


def test_growth_status_dashboard(store: KnowledgeStore) -> None:
    growth.run_growth_cycle(store, bucket="2026-10-28")
    status = growth.growth_status(store)
    assert status["status"] == "ok"
    assert set(status["job_types"]) == {
        "seed_sync",
        "health_check",
        "distill_queue",
        "feed_sync",
        "compile_runs",
    }
    assert status["by_status"].get("done", 0) >= 3


def test_seed_sync_job_inserts_builtin_wiki(store: KnowledgeStore) -> None:
    job = growth.enqueue_growth_job(store, "seed_sync", idempotency_key="ss-1")
    outcome = growth.run_growth_job(store, job["job_id"])
    assert outcome["status"] == "done"
    seed = outcome["result"]["seed"]
    assert seed["status"] == "ok"
    assert seed["inserted"] >= 27
    # Re-running the same bucket is a no-op.
    again = growth.run_growth_job(store, job["job_id"])
    assert again["status"] == "skipped"


def test_kb_growth_tool_runs_cycle_and_status(
    store: KnowledgeStore, monkeypatch
) -> None:
    import json

    import jw.tools.knowledge_base as kb_tools

    monkeypatch.setattr(kb_tools, "_get_store", lambda: store)

    cycle = json.loads(kb_tools.kb_growth.invoke({"action": "cycle"}))
    assert cycle["status"] == "ok"
    assert {job["job_type"] for job in cycle["jobs"]} >= {
        "seed_sync",
        "health_check",
        "distill_queue",
    }

    status = json.loads(kb_tools.kb_growth.invoke({"action": "status"}))
    assert status["status"] == "ok"
    assert status["by_status"].get("done", 0) >= 3

    bad = json.loads(kb_tools.kb_growth.invoke({"action": "feed_sync"}))
    assert bad["status"] == "error"
    assert "feed_id" in bad["error"]


def test_compile_runs_job_replays_finalized_runs(
    store: KnowledgeStore, tmp_path: Path
) -> None:
    """The compile_runs job must be on-demand and report its receipt."""

    runs = tmp_path / "runs"
    runs.mkdir()
    job = growth.enqueue_growth_job(
        store,
        "compile_runs",
        idempotency_key="2026-10-28",
        payload={"limit": 5, "runs_dir": str(runs)},
    )
    outcome = growth.run_growth_job(store, job["job_id"])

    assert outcome["status"] == "done"
    assert outcome["result"]["scanned"] == 0
    assert outcome["result"]["decisions"] == {}


def test_compile_runs_is_not_in_daily_sweep(store: KnowledgeStore) -> None:
    """Backfill is a one-off; the daily sweep must not replay history."""

    cycle = growth.run_growth_cycle(store, bucket="2026-10-28")
    assert "compile_runs" not in {job["job_type"] for job in cycle["jobs"]}


def test_kb_growth_tool_compile_action(store: KnowledgeStore, monkeypatch) -> None:
    import json

    import jw.tools.knowledge_base as kb_tools

    monkeypatch.setattr(kb_tools, "_get_store", lambda: store)

    compiled = json.loads(kb_tools.kb_growth.invoke({"action": "compile", "limit": 5}))
    assert compiled["status"] == "ok"
    assert any(job["job_type"] == "compile_runs" for job in compiled["jobs"])
