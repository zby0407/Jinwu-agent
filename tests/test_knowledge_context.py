"""Stage-aware Wiki context and host injection regression tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from knowledge_base import service
from knowledge_base.context import (
    build_knowledge_context,
    normalize_stage,
    render_knowledge_context,
    unavailable_context,
)
from knowledge_base.store import KnowledgeStore
from jw.middleware.knowledge_context import (
    KnowledgeContextMiddleware,
    detect_stage,
)


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


def _propose(store: KnowledgeStore, *args, **kwargs):
    payload = {
        "entry_type": "mechanism",
        "title": "Flux transport mechanism",
        "content": {"claim": "Flux transport is coupled to the polar field."},
        "source_type": "historical_run",
        "source_ref": "run-a",
        "confidence": "medium",
        "valid_range": "SC21-SC25",
    }
    payload.update(kwargs)
    return service.propose(store, *args, **payload)["entry"]


def _canonical(store: KnowledgeStore, **kwargs):
    entry = _propose(store, **kwargs)
    for run_id in ("run-repro-a", "run-repro-b"):
        service.record_reproduction(
            store,
            entry["id"],
            run_id=run_id,
            evidence_ref=f"runs/{run_id}/report.md",
        )
    service.promote(store, entry["id"], reason="two independent runs reproduce")
    return store.get_entry(entry["id"])


def _request(system_text: str, user_text: str = "Flux transport"):
    request = SimpleNamespace(
        state={},
        runtime=object(),
        system_message=SystemMessage(content=system_text),
        messages=[HumanMessage(content=user_text)],
    )
    request.override = lambda **kwargs: SimpleNamespace(
        state=request.state,
        runtime=request.runtime,
        system_message=kwargs.get("system_message", request.system_message),
        messages=request.messages,
    )
    return request


def test_stage_context_prioritises_canonical_and_keeps_candidate_lead(store):
    canonical = _canonical(store)
    candidate = _propose(
        store,
        title="Flux transport candidate",
        content={"claim": "A provisional flux transport lead."},
        confidence="low",
    )

    packet = build_knowledge_context(
        store,
        "Flux transport",
        stage="planning",
        run_id="context-run",
        agent="solar-planner",
    )

    assert packet["status"] == "ok"
    assert packet["entries"][0]["id"] == canonical["id"]
    assert packet["entries"][0]["usage_role"] == "canonical"
    assert packet["entries"][0]["valid_range"] == "SC21-SC25"
    assert any(entry["id"] == candidate["id"] for entry in packet["entries"])
    assert packet["counts"]["canonical"] == 1
    assert packet["counts"]["candidate"] >= 1
    assert any(
        item["kind"] == "valid_range"
        and item["entry_id"] == canonical["id"]
        for item in packet["constraints"]
    )

    usage = service.usage_log(store, "context-run")
    used_ids = {item["entry_id"] for item in usage["entries_used"]}
    assert {entry["id"] for entry in packet["entries"]} <= used_ids


def test_unresolved_counterexample_marks_canonical_contested(store):
    canonical = _canonical(store)
    _propose(
        store,
        entry_type="counterexample",
        title="Flux transport counterexample",
        content={"statement": "No flux transport was observed.", "run_id": "run-cx"},
        source_ref="run-cx",
        related_ids=[canonical["id"]],
        confidence="low",
        valid_range="",
    )

    packet = build_knowledge_context(
        store, "Flux transport", stage="evidence", run_id="evidence-run"
    )

    target = next(entry for entry in packet["entries"] if entry["id"] == canonical["id"])
    assert target["usage_role"] == "contested_canonical"
    assert target["conflict_ids"]
    assert packet["open_conflicts"]


def test_candidate_only_context_is_explicit_gap(store):
    candidate = _propose(store, confidence="low")

    packet = build_knowledge_context(store, "Flux transport", stage="hypothesis")

    assert candidate["id"] in {entry["id"] for entry in packet["entries"]}
    assert packet["status"] == "candidate_only"
    assert packet["gaps"]


def test_empty_and_unavailable_context_are_not_silent(store):
    empty = build_knowledge_context(store, "no-such-term-xyz", stage="data")
    assert empty["status"] == "empty"
    assert empty["gaps"]

    unavailable = unavailable_context("data", "Flux transport", "db locked")
    assert unavailable["status"] == "unavailable"
    assert unavailable["gaps"] == ["Knowledge context is unavailable: db locked"]


def test_render_contains_rules_and_entry_ids(store):
    canonical = _canonical(store)
    packet = build_knowledge_context(store, "Flux transport", stage="data")
    rendered = render_knowledge_context(packet)

    assert '<knowledge_context schema_version="knowledge-context-v1"' in rendered
    assert canonical["id"] in rendered
    assert "Contested canonical" in rendered
    assert "not proof of an empirical claim" in rendered


def test_invalid_stage_rejected():
    with pytest.raises(ValueError, match="unsupported knowledge-context stage"):
        normalize_stage("unknown")
    assert normalize_stage("review") == "evidence"


def test_detect_stage_uses_specialist_identity():
    planner = _request("You are the solar-planner sub-agent.")
    assert detect_stage(planner) == "planning"

    experiment = _request(
        "You are the solar-experiment sub-agent. stage=experiment_design and "
        "stage=experiment_result are both possible.",
        "Run the experiment. stage=experiment_result",
    )
    assert detect_stage(experiment) == "experiment_result"

    evidence = _request(
        "You are the independent Evidence Reviewer of Solar-Cycle Co-Scientist."
    )
    assert detect_stage(evidence) == "evidence"

    main = _request("You are the main agent.")
    assert detect_stage(main) == ""


def test_middleware_reads_task_question_from_workspace(store, monkeypatch, tmp_path):
    base = tmp_path / "workspace"
    base.mkdir()
    (base / "task.json").write_text(
        '{"research_question": "Flux transport in the polar field"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "jw.middleware.knowledge_context._task_workspace", lambda: base
    )
    monkeypatch.setattr(
        "jw.middleware.knowledge_context.get_config",
        lambda: {"configurable": {"thread_id": "ctx-thread"}},
    )
    import jw.tools.knowledge_base as kb_tools

    monkeypatch.setattr(kb_tools, "_get_store", lambda: store)
    canonical = _canonical(store)

    modified = KnowledgeContextMiddleware().modify_request(
        _request("You are the solar-data sub-agent.", "unrelated user text")
    )
    system_text = str(modified.system_message.content)

    assert 'stage="data"' in system_text
    assert "Flux transport in the polar field" in system_text
    assert canonical["id"] in system_text


def test_middleware_injects_once_and_is_provider_agnostic(store, monkeypatch):
    canonical = _canonical(store)
    middleware = KnowledgeContextMiddleware()
    request = _request("You are the solar-planner sub-agent.")

    monkeypatch.setattr(
        "jw.middleware.knowledge_context._task_workspace", lambda: None
    )
    monkeypatch.setattr(
        "jw.middleware.knowledge_context.get_config",
        lambda: {"configurable": {"thread_id": "middleware-run"}},
    )
    import jw.tools.knowledge_base as kb_tools

    calls: list[int] = []

    def _store():
        calls.append(1)
        return store

    monkeypatch.setattr(kb_tools, "_get_store", _store)

    modified = middleware.modify_request(request)
    system_text = str(modified.system_message.content)

    assert '<knowledge_context schema_version="knowledge-context-v1"' in system_text
    assert 'stage="planning"' in system_text
    assert canonical["id"] in system_text

    # The frozen packet is reused for the rest of the task, not rebuilt on every
    # model call (the cache also avoids duplicate provenance rows).
    middleware.modify_request(request)
    assert calls == [1]
    usage = service.usage_log(store, "middleware-run")
    assert usage["used_count"] == 1

