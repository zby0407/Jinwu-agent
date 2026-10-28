"""Default-suite regression gates for Wiki promotion correctness."""

from __future__ import annotations

from pathlib import Path

import pytest

from knowledge_base import service
from knowledge_base.contracts import ContractError, validate_entry
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


def _propose(store: KnowledgeStore, title: str = "候选主张", **overrides):
    payload = {
        "entry_type": "finding",
        "title": title,
        "content": {"statement": "候选主张", "run_id": "run-a"},
        "source_type": "historical_run",
        "source_ref": "run-a",
        "confidence": "low",
    }
    payload.update(overrides)
    return service.propose(store, **payload)["entry"]


def _record_runs(store: KnowledgeStore, entry_id: str, *run_ids: str) -> None:
    for run_id in run_ids:
        service.record_reproduction(
            store,
            entry_id,
            run_id=run_id,
            evidence_ref=f"runs/{run_id}/report.md",
        )


def test_reads_do_not_satisfy_promotion(store: KnowledgeStore) -> None:
    entry = _propose(store)
    service.read(store, entry["id"], run_id="run-b", purpose="grounding")
    service.read(store, entry["id"], run_id="run-c", purpose="comparison")

    result = service.promote(store, entry["id"], reason="被阅读两次")

    assert result["decision"] == "promotion_not_ready"
    assert result["supporting_run_ids"] == []
    assert store.get_entry(entry["id"])["status"] == "candidate"


def test_explicit_reproduction_evidence_allows_promotion(
    store: KnowledgeStore,
) -> None:
    entry = _propose(store)
    _record_runs(store, entry["id"], "run-a", "run-b")

    result = service.promote(store, entry["id"], reason="两个独立运行复现")

    assert result["decision"] == "promoted"
    assert result["supporting_run_ids"] == ["run-a", "run-b"]
    assert store.get_entry(entry["id"])["status"] == "canonical"


def test_unresolved_conflict_blocks_promotion(store: KnowledgeStore) -> None:
    target = service.propose(
        store,
        entry_type="mechanism",
        title="极区场决定下一周振幅",
        content={"claim": "极区场越强，下一活动周越强。"},
        source_type="literature",
        source_ref="doi:10.1000/target",
        confidence="medium",
    )["entry"]
    target["status"] = "canonical"
    target["version"] += 1
    store.update_entry(target, changed_by="fixture", reason="seed canonical")

    counter = service.propose(
        store,
        entry_type="counterexample",
        title="独立运行未复现该机制",
        content={
            "statement": "独立运行中极区场未预测下一周振幅。",
            "run_id": "run-c",
        },
        source_type="historical_run",
        source_ref="run-c",
        confidence="medium",
        related_ids=[target["id"]],
    )["entry"]
    _record_runs(store, counter["id"], "run-c", "run-d")

    result = service.promote(store, counter["id"], reason="两次独立运行复现")

    assert result["decision"] == "promotion_blocked_by_conflict"
    assert result["reason"] == "unresolved_conflict"
    assert store.get_entry(counter["id"])["status"] == "candidate"
    assert store.get_entry(target["id"])["status"] == "canonical"


def test_reproduction_record_requires_traceable_locator(
    store: KnowledgeStore,
) -> None:
    entry = _propose(store)
    with pytest.raises(ContractError) as exc:
        service.record_reproduction(
            store, entry["id"], run_id="run-b", evidence_ref=" "
        )
    assert exc.value.error_code == "reproduction_evidence_ref_missing"


def test_lesson_type_is_not_a_scientific_finding() -> None:
    entry = validate_entry(
        {
            "id": "kb_lesson_failed_export_001",
            "type": "lesson",
            "title": "沙箱导出失败",
            "content": {
                "statement": "固定沙箱后端执行失败。",
                "run_id": "run-1",
                "failure_mode": "host exit 71",
                "remediation": "检查沙箱后端可用性。",
            },
            "source_type": "historical_run",
            "source_ref": "run-1",
            "confidence": "low",
            "status": "candidate",
            "valid_range": "",
            "related_ids": [],
            "provenance": {},
            "version": 1,
            "created_at": "2026-10-28T00:00:00+00:00",
            "updated_at": "2026-10-28T00:00:00+00:00",
            "created_by": "test",
        }
    )
    assert entry["type"] == "lesson"
    assert entry["content"]["failure_mode"] == "host exit 71"
