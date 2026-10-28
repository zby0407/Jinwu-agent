"""Automatic growth trigger, growth metrics and conflict adjudication."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from knowledge_base import growth, service
from knowledge_base.contracts import ContractError
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


def _propose(store: KnowledgeStore, entry_type: str, title: str, run_id: str) -> str:
    return service.propose(
        store,
        entry_type=entry_type,
        title=title,
        content={"statement": title, "run_id": run_id},
        source_type="historical_run",
        source_ref=f"run://{run_id}",
        confidence="medium",
    )["entry"]["id"]


def _link(store: KnowledgeStore, entry_id: str, related_id: str) -> None:
    entry = store.get_entry(entry_id)
    entry["related_ids"] = [related_id]
    store.update_entry(entry, changed_by="test", reason="link related entry")


def _canonical(store: KnowledgeStore, title: str = "canonical claim") -> str:
    entry_id = _propose(store, "finding", title, "run-a")
    for run_id in ("run-a", "run-b"):
        service.record_reproduction(
            store, entry_id, run_id=run_id, evidence_ref=f"run://{run_id}/record.json"
        )
    result = service.promote(store, entry_id, reason="two independent runs")
    assert result["decision"] == "promoted"
    return entry_id


def _conflict_pair(store: KnowledgeStore) -> tuple[str, str]:
    canonical_id = _canonical(store)
    candidate_id = _propose(store, "counterexample", "contradicting run", "run-c")
    _link(store, candidate_id, canonical_id)
    return candidate_id, canonical_id


def _wait(predicate, timeout: float = 5.0) -> bool:
    done = threading.Event()
    for _ in range(int(timeout / 0.01)):
        if predicate():
            return True
        done.wait(0.01)
    return predicate()


# ----------------------------------------------------------------------
# automatic growth trigger
# ----------------------------------------------------------------------
def test_growth_trigger_starts_and_then_throttles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jw.middleware import knowledge_context as kc

    monkeypatch.setenv("JW_KB_AUTO_GROWTH", "1")
    monkeypatch.setenv("JW_KB_AUTO_GROWTH_INTERVAL", "3600")
    kc._reset_growth_state()

    calls: list[int] = []

    def _fake_cycle() -> None:
        calls.append(1)
        with kc._GROWTH_LOCK:
            kc._GROWTH_INFLIGHT = False
        kc._GROWTH_LAST_RESULT = {"status": "ok", "jobs": []}

    monkeypatch.setattr(kc, "_run_growth_cycle_once", _fake_cycle)
    try:
        assert kc.trigger_growth()["status"] == "started"
        assert _wait(lambda: calls == [1]), "background growth thread did not run"
        assert _wait(lambda: not kc.growth_state()["inflight"])
        assert kc.growth_state()["last_result"] == {"status": "ok", "jobs": []}
        assert kc.trigger_growth()["status"] == "throttled"
    finally:
        kc._reset_growth_state()


def test_growth_trigger_respects_disable_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    from jw.middleware import knowledge_context as kc

    monkeypatch.setenv("JW_KB_AUTO_GROWTH", "0")
    kc._reset_growth_state()
    try:
        assert kc.trigger_growth()["status"] == "disabled"
    finally:
        kc._reset_growth_state()


def test_growth_trigger_can_be_forced(monkeypatch: pytest.MonkeyPatch) -> None:
    from jw.middleware import knowledge_context as kc

    monkeypatch.setenv("JW_KB_AUTO_GROWTH", "0")
    kc._reset_growth_state()

    def _fake_cycle() -> None:
        with kc._GROWTH_LOCK:
            kc._GROWTH_INFLIGHT = False

    monkeypatch.setattr(kc, "_run_growth_cycle_once", _fake_cycle)
    try:
        assert kc.trigger_growth(force=True)["status"] == "started"
    finally:
        kc._reset_growth_state()


def test_growth_failure_is_recorded_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    from jw.middleware import knowledge_context as kc

    monkeypatch.setenv("JW_KB_AUTO_GROWTH", "1")
    kc._reset_growth_state()

    def _boom() -> None:
        with kc._GROWTH_LOCK:
            kc._GROWTH_INFLIGHT = False
        raise RuntimeError("store unavailable")

    def _cycle() -> None:
        try:
            _boom()
        except RuntimeError as exc:
            kc._GROWTH_LAST_RESULT = {"status": "error", "error": str(exc)}

    monkeypatch.setattr(kc, "_run_growth_cycle_once", _cycle)
    try:
        assert kc.trigger_growth()["status"] == "started"
        assert _wait(lambda: kc.growth_state()["last_result"].get("status") == "error")
        assert kc.growth_state()["last_result"]["error"] == "store unavailable"
    finally:
        kc._reset_growth_state()


def test_only_main_agent_middleware_triggers_growth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jw.middleware import knowledge_context as kc

    monkeypatch.setenv("JW_KB_AUTO_GROWTH", "1")
    kc._reset_growth_state()
    calls: list[int] = []

    def _fake_cycle() -> None:
        calls.append(1)
        with kc._GROWTH_LOCK:
            kc._GROWTH_INFLIGHT = False

    monkeypatch.setattr(kc, "_run_growth_cycle_once", _fake_cycle)
    try:
        specialist = kc.create_knowledge_context_middleware()
        specialist.after_agent({}, None)
        assert calls == []

        main = kc.create_knowledge_context_middleware(growth=True)
        main.after_agent({}, None)
        assert _wait(lambda: calls == [1])
    finally:
        kc._reset_growth_state()


def test_main_chain_registers_growth_trigger() -> None:
    """The host, not the agent, decides when maintenance happens."""

    source = (Path(__file__).resolve().parents[1] / "jw" / "agent.py").read_text(
        encoding="utf-8"
    )
    assert (
        "create_knowledge_context_middleware(growth=not for_async_subagent)" in source
    )
    # The specialist chain must stay inject-only (no growth trigger).
    assert "create_knowledge_context_middleware()," in source


# ----------------------------------------------------------------------
# growth metrics
# ----------------------------------------------------------------------
def test_growth_metrics_report_zero_rates_on_empty_store(
    store: KnowledgeStore,
) -> None:
    metrics = growth.growth_metrics(store)
    assert metrics["reuse_rate"] == 0.0
    assert metrics["injection_rate"] == 0.0
    assert metrics["promotion_precision"] == 0.0
    assert metrics["distillation_conversion"] == 0.0
    assert metrics["counts"]["entries"]["total"] == 0


def test_growth_metrics_track_usage_and_promotion(store: KnowledgeStore) -> None:
    entry_id = _canonical(store)
    store.log_provenance(
        run_id="run-a",
        agent="solar-planner",
        entry_id=entry_id,
        purpose="stage_context:planning",
    )
    store.log_provenance(
        run_id="run-a", agent="main", entry_id=entry_id, purpose="kb_read"
    )

    metrics = growth.growth_metrics(store)
    assert metrics["reuse_rate"] == 1.0
    assert metrics["injection_rate"] == 0.5
    assert metrics["promotion_precision"] == 1.0
    assert metrics["counts"]["reproduction"]["auto_promoted_canonical"] == 1
    assert metrics["reads_per_run"] == 2.0


def test_growth_status_embeds_metrics(store: KnowledgeStore) -> None:
    growth.run_growth_cycle(store, bucket="2026-10-28")
    status = growth.growth_status(store)
    assert status["metrics"]["status"] == "ok"
    assert status["by_status"] == {"done": 3}


def test_derive_metrics_accepts_a_plain_connection(store: KnowledgeStore) -> None:
    _canonical(store)
    metrics = growth.derive_metrics(store.usage_raw_counts())
    assert metrics["promotion_precision"] == 1.0


# ----------------------------------------------------------------------
# conflict adjudication
# ----------------------------------------------------------------------
def test_conflict_blocks_promotion_until_adjudicated(store: KnowledgeStore) -> None:
    candidate_id, _ = _conflict_pair(store)
    for run_id in ("run-c", "run-d"):
        service.record_reproduction(
            store,
            candidate_id,
            run_id=run_id,
            evidence_ref=f"run://{run_id}/record.json",
        )
    assert (
        service.promote(store, candidate_id, reason="reproduced twice")["decision"]
        == "promotion_blocked_by_conflict"
    )


def test_uphold_canonical_retires_counterexample(store: KnowledgeStore) -> None:
    candidate_id, canonical_id = _conflict_pair(store)
    result = service.adjudicate_conflict(
        store,
        candidate_id=candidate_id,
        canonical_id=canonical_id,
        decision="uphold_canonical",
        rationale="the counterexample used a different metric definition",
        agent="test",
    )
    assert result["conflict_resolved"] is True
    assert result["retired_entry_id"] == candidate_id
    assert store.get_entry(candidate_id)["status"] == "deprecated"
    assert store.get_entry(canonical_id)["status"] == "canonical"
    assert service.conflicts(store)["count"] == 0


def test_overturn_canonical_lets_counterexample_promote(store: KnowledgeStore) -> None:
    candidate_id, canonical_id = _conflict_pair(store)
    service.adjudicate_conflict(
        store,
        candidate_id=candidate_id,
        canonical_id=canonical_id,
        decision="overturn_canonical",
        rationale="the canonical claim used a biased sample",
        agent="test",
    )
    assert store.get_entry(canonical_id)["status"] == "deprecated"
    for run_id in ("run-c", "run-d"):
        service.record_reproduction(
            store,
            candidate_id,
            run_id=run_id,
            evidence_ref=f"run://{run_id}/record.json",
        )
    assert (
        service.promote(store, candidate_id, reason="now supported")["decision"]
        == "promoted"
    )


def test_defer_records_review_but_keeps_conflict_open(store: KnowledgeStore) -> None:
    candidate_id, canonical_id = _conflict_pair(store)
    result = service.adjudicate_conflict(
        store,
        candidate_id=candidate_id,
        canonical_id=canonical_id,
        decision="defer",
        rationale="need a third independent run before deciding",
        agent="test",
    )
    assert result["conflict_resolved"] is False
    assert result["retired_entry_id"] == ""
    conflicts = service.conflicts(store)["conflicts"]
    assert len(conflicts) == 1
    assert [item["decision"] for item in conflicts[0]["adjudications"]] == ["defer"]


def test_adjudication_rejects_invalid_inputs(store: KnowledgeStore) -> None:
    candidate_id, canonical_id = _conflict_pair(store)
    with pytest.raises(ContractError):
        service.adjudicate_conflict(
            store,
            candidate_id=candidate_id,
            canonical_id=canonical_id,
            decision="delete_everything",
            rationale="nope",
        )
    with pytest.raises(ContractError):
        service.adjudicate_conflict(
            store,
            candidate_id=candidate_id,
            canonical_id=canonical_id,
            decision="defer",
            rationale="   ",
        )
    unlinked = _propose(store, "counterexample", "unlinked", "run-e")
    with pytest.raises(ContractError):
        service.adjudicate_conflict(
            store,
            candidate_id=unlinked,
            canonical_id=canonical_id,
            decision="defer",
            rationale="not linked",
        )


def test_adjudication_is_idempotent(store: KnowledgeStore) -> None:
    candidate_id, canonical_id = _conflict_pair(store)
    first = service.adjudicate_conflict(
        store,
        candidate_id=candidate_id,
        canonical_id=canonical_id,
        decision="defer",
        rationale="same review twice",
    )
    second = service.adjudicate_conflict(
        store,
        candidate_id=candidate_id,
        canonical_id=canonical_id,
        decision="defer",
        rationale="same review twice",
    )
    assert first["adjudication_id"] == second["adjudication_id"]
    assert len(store.list_conflict_adjudications()) == 1
