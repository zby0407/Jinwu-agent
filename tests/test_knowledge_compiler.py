"""Post-run claim compiler: stable fingerprints, cross-run evidence, auto gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knowledge_base import compiler, service
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


SUBSTANCE = {
    "primary_question": "耀斑能量释放率是否随活动周相位上升？",
    "primary_estimand": "活动周相位对耀斑能量释放率的斜率",
    "claim_scope": "2010-2024 年 M 级以上耀斑，月尺度聚合",
}


def _compile(
    store: KnowledgeStore, *, run_id: str, substance=None, entry_type="finding"
):
    return compiler.compile_run_claim(
        store,
        entry_type=entry_type,
        title="[completed_interpretable] 耀斑能量释放率随相位变化",
        statement="活动周相位上升时耀斑能量释放率显著增加。",
        substance=substance or SUBSTANCE,
        run_id=run_id,
        evidence_ref=f"run://{run_id}/record.json#sha256:deadbeef",
        agent="automatic_experiment",
    )


def test_claim_key_is_stable_across_formatting() -> None:
    noisy = {
        "primary_question": "  耀斑能量释放率是否随活动周相位上升？ ",
        "primary_estimand": "活动周相位对耀斑能量释放率的斜率\n",
        "claim_scope": "2010-2024 年 M 级以上耀斑，月尺度聚合",
    }
    assert compiler.claim_key("finding", SUBSTANCE) == compiler.claim_key(
        "finding", noisy
    )
    other = dict(SUBSTANCE, primary_estimand="太阳黑子数与耀斑数的相关系数")
    assert compiler.claim_key("finding", SUBSTANCE) != compiler.claim_key(
        "finding", other
    )
    # 类型是主张身份的一部分：同一实质的 finding 与 counterexample 不可混同。
    assert compiler.claim_key("finding", SUBSTANCE) != compiler.claim_key(
        "counterexample", SUBSTANCE
    )


def test_first_run_creates_candidate_with_traceable_evidence(
    store: KnowledgeStore,
) -> None:
    result = _compile(store, run_id="run-a")

    assert result["decision"] == "created"
    entry = store.get_entry(result["entry_id"])
    assert entry["status"] == "candidate"
    assert entry["provenance"]["claim_key"] == result["claim_key"]
    evidence = store.list_reproduction_evidence(result["entry_id"])
    assert [row["run_id"] for row in evidence] == ["run-a"]
    assert evidence[0]["evidence_ref"].startswith("run://run-a/record.json")


def test_second_independent_run_promotes_through_the_gate(
    store: KnowledgeStore,
) -> None:
    first = _compile(store, run_id="run-a")
    second = _compile(store, run_id="run-b")

    # 同一主张落到同一条目，而不是产生两个互不相识的孤儿候选。
    assert second["entry_id"] == first["entry_id"]
    assert second["decision"] == "promoted"

    entry = store.get_entry(first["entry_id"])
    assert entry["status"] == "canonical"
    assert entry["provenance"]["auto_rule"] == "cross_run_reproduction"
    assert sorted(entry["provenance"]["supporting_run_ids"]) == ["run-a", "run-b"]
    assert sorted(store.distinct_reproduction_run_ids(first["entry_id"])) == [
        "run-a",
        "run-b",
    ]
    assert store._conn.execute("SELECT COUNT(*) AS n FROM entries").fetchone()["n"] == 1


def test_recompiling_the_same_run_is_idempotent(store: KnowledgeStore) -> None:
    first = _compile(store, run_id="run-a")
    again = _compile(store, run_id="run-a")

    assert again["entry_id"] == first["entry_id"]
    assert again["decision"] == "already_recorded"
    assert store.get_entry(first["entry_id"])["status"] == "candidate"
    assert len(store.list_reproduction_evidence(first["entry_id"])) == 1


def test_canonical_claim_is_not_recompiled(store: KnowledgeStore) -> None:
    _compile(store, run_id="run-a")
    _compile(store, run_id="run-b")

    third = _compile(store, run_id="run-c")
    assert third["decision"] == "already_canonical"
    # 已晋升条目不接受新复现证据；复用记录走 provenance_log。
    assert len(store.list_reproduction_evidence(third["entry_id"])) == 2


def test_different_claims_stay_separate(store: KnowledgeStore) -> None:
    first = _compile(store, run_id="run-a")
    other = compiler.compile_run_claim(
        store,
        entry_type="finding",
        title="[completed_interpretable] 耀斑持续时间与相位",
        statement="耀斑持续时间与活动周相位无显著关联。",
        substance=dict(SUBSTANCE, primary_estimand="耀斑持续时间的相位效应"),
        run_id="run-b",
        evidence_ref="run://run-b/record.json#sha256:cafe",
    )
    assert other["entry_id"] != first["entry_id"]
    assert other["decision"] == "created"


def test_unresolved_conflict_blocks_auto_promotion(store: KnowledgeStore) -> None:
    canonical = service.propose(
        store,
        entry_type="finding",
        title="既有 canonical 发现",
        content={"statement": "耀斑能量释放率不随相位变化。", "run_id": "run-0"},
        source_type="historical_run",
        source_ref="run-0",
        confidence="high",
    )["entry"]
    service.record_reproduction(
        store,
        canonical["id"],
        run_id="run-0a",
        evidence_ref="run://run-0a/record.json",
    )
    service.record_reproduction(
        store,
        canonical["id"],
        run_id="run-0b",
        evidence_ref="run://run-0b/record.json",
    )
    service.promote(store, canonical["id"], reason="seed canonical")
    assert store.get_entry(canonical["id"])["status"] == "canonical"

    def _conflicting(store_, *, run_id, substance=None, entry_type="counterexample"):
        return compiler.compile_run_claim(
            store_,
            entry_type=entry_type,
            title="[scientific_null] 相位效应反例",
            statement="同一数据下未观察到相位效应。",
            substance=SUBSTANCE,
            run_id=run_id,
            evidence_ref=f"run://{run_id}/record.json#sha256:feed",
            related_ids=[canonical["id"]],
        )

    first = _conflicting(store, run_id="run-c1")
    assert first["decision"] == "created"
    second = _conflicting(store, run_id="run-c2")
    assert second["decision"] == "promotion_blocked_by_conflict"
    assert store.get_entry(first["entry_id"])["status"] == "candidate"
    assert len(store.list_reproduction_evidence(first["entry_id"])) == 2


def test_find_entry_by_claim_key_ignores_retired_entries(
    store: KnowledgeStore,
) -> None:
    result = _compile(store, run_id="run-a")
    entry = store.get_entry(result["entry_id"])
    entry["status"] = "deprecated"
    store.update_entry(entry, changed_by="test", reason="retire")

    assert store.find_entry_by_claim_key(result["claim_key"]) is None
    # 权威主张不得复活：退休后新的同 claim 运行会开新条目。
    again = _compile(store, run_id="run-b")
    assert again["decision"] == "created"
    assert again["entry_id"] != result["entry_id"]


def test_missing_evidence_ref_is_refused(store: KnowledgeStore) -> None:
    result = compiler.compile_run_claim(
        store,
        entry_type="finding",
        title="t",
        statement="s",
        substance=SUBSTANCE,
        run_id="run-a",
        evidence_ref="   ",
    )
    assert result["status"] == "skipped"
    assert result["entry_ids"] == []


def _run_root(tmp_path: Path, run_id: str, *, estimand: str) -> Path:
    root = tmp_path / run_id
    root.mkdir()
    (root / "record.json").write_text(
        json.dumps(
            {
                "task": "估计活动周相位对耀斑释放率的影响",
                "run_id": run_id,
                "outcome": "completed_interpretable",
                "outcome_reason": "完成估计。",
                "record_sha256": f"sha-{run_id}",
                "scientific_assessment": {
                    "report_narrative": {
                        "interpretation": "活动周相位上升时耀斑能量释放率增加。",
                        "limitations": ["只覆盖 M 级以上耀斑。"],
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "design.json").write_text(
        json.dumps(
            {
                "research_frame": {
                    "primary_question": "耀斑能量释放率是否随活动周相位上升？",
                    "claim_scope": "2010-2024 年 M 级以上耀斑",
                },
                "interpretation_policy": {"primary_estimand": estimand},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return root


def test_writeback_merges_reproduction_across_runs(tmp_path: Path, monkeypatch) -> None:
    from automatic_experiment import service as experiment_service

    monkeypatch.setenv("JW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("JW_KB_EXPORT_DIR", str(tmp_path / "knowledge_base"))
    estimand = "活动周相位对耀斑能量释放率的斜率"
    first_root = _run_root(tmp_path, "run-a", estimand=estimand)
    second_root = _run_root(tmp_path, "run-b", estimand=estimand)

    first = experiment_service._knowledge_writeback(
        first_root, {"run_id": "run-a", "outcome": "completed_interpretable"}
    )
    second = experiment_service._knowledge_writeback(
        second_root, {"run_id": "run-b", "outcome": "completed_interpretable"}
    )

    assert first["status"] == "ok"
    assert first["decision"] == "created"
    assert second["entry_ids"] == first["entry_ids"]
    assert second["decision"] == "promoted"
    assert second["supporting_run_ids"] == ["run-a", "run-b"]

    store = KnowledgeStore()
    try:
        entry = store.get_entry(first["entry_ids"][0])
        assert entry["status"] == "canonical"
        refs = {
            row["evidence_ref"] for row in store.list_reproduction_evidence(entry["id"])
        }
        assert refs == {
            "run://run-a/record.json#sha256:sha-run-a",
            "run://run-b/record.json#sha256:sha-run-b",
        }
    finally:
        store.close()


def test_backfill_merges_legacy_orphans_into_one_claim(
    tmp_path: Path, monkeypatch
) -> None:
    """Old run-scoped writebacks must be merged by the claim compiler."""

    from automatic_experiment import service as experiment_service

    data_dir = tmp_path / "data"
    monkeypatch.setenv("JW_DATA_DIR", str(data_dir))
    monkeypatch.setenv("JW_KB_EXPORT_DIR", str(tmp_path / "knowledge_base"))
    runs = tmp_path / "runs"
    runs.mkdir(parents=True)

    estimand = "活动周相位对耀斑能量释放率的斜率"
    store = KnowledgeStore()
    try:
        # 旧写回：同一主张在两个 run 各建一条 orphan candidate。
        for run_id in ("run-old-a", "run-old-b"):
            run_root = _run_root(runs, run_id, estimand=estimand)
            (run_root / "state.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "phase": "report_finalized",
                        "outcome": "completed_interpretable",
                    }
                ),
                encoding="utf-8",
            )
            (run_root / "entry_result.json").write_text("{}", encoding="utf-8")
            service.propose(
                store,
                entry_type="finding",
                title=f"[completed_interpretable] 旧写回 {run_id}",
                content={
                    "statement": "活动周相位上升时耀斑能量释放率增加。",
                    "run_id": run_id,
                },
                source_type="historical_run",
                source_ref=run_id,
                confidence="low",
            )
        assert (
            store._conn.execute(
                "SELECT COUNT(*) AS n FROM entries WHERE type='finding'"
            ).fetchone()["n"]
            == 2
        )
    finally:
        store.close()

    receipt = experiment_service.backfill_knowledge_writeback(limit=10, runs_dir=runs)
    assert receipt["status"] == "ok"
    assert receipt["scanned"] == 2
    assert receipt["decisions"].get("created") == 1
    assert receipt["decisions"].get("promoted") == 1

    store = KnowledgeStore()
    try:
        rows = store.search(entry_type="finding", limit=10)
        claim_keyed = [row for row in rows if row["provenance"].get("claim_key")]
        assert len(claim_keyed) == 1
        merged = claim_keyed[0]
        assert merged["status"] == "canonical"
        assert sorted(store.distinct_reproduction_run_ids(merged["id"])) == [
            "run-old-a",
            "run-old-b",
        ]
    finally:
        store.close()
