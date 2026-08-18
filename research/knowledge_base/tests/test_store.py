"""知识库存储与服务端到端测试：临时目录隔离 db，不碰真实 ~/.jw。"""

from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from knowledge_base import service  # noqa: E402
from knowledge_base.contracts import ContractError  # noqa: E402
from knowledge_base.export import export_path, import_entry_file  # noqa: E402
from knowledge_base.store import KnowledgeStore  # noqa: E402


def make_store(tmp: str) -> KnowledgeStore:
    return KnowledgeStore(
        db_path=Path(tmp) / "knowledge.db", export_dir=Path(tmp) / "knowledge_base"
    )


def propose_concept(store, title="极区磁场前兆", **overrides):
    params = {
        "entry_type": "concept",
        "title": title,
        "content": {
            "definition": "极区磁场在极小期附近的强度可作为下一活动周振幅的前兆。"
        },
        "source_type": "expert",
        "source_ref": "expert:reviewer-a",
        "confidence": "medium",
    }
    params.update(overrides)
    return service.propose(store, **params)


class TestLiteratureSchemaMigration(unittest.TestCase):
    def test_legacy_lit_sources_is_upgraded_and_backfilled(self):
        tmp = tempfile.mkdtemp(prefix="kb_migration_test_")
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        db_path = Path(tmp) / "knowledge.db"
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE lit_sources (source_id TEXT PRIMARY KEY, title TEXT, "
            "authors TEXT, year INTEGER, doi TEXT, url TEXT, abstract TEXT, "
            "fetched_at TEXT, distilled_entry_id TEXT)"
        )
        conn.execute(
            "INSERT INTO lit_sources VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "openalex:Wlegacy",
                "Legacy Solar Review",
                '["Legacy Author"]',
                2010,
                "https://doi.org/10.1000/LEGACY",
                "https://example.org/legacy",
                "legacy abstract",
                None,
                "kb_concept_legacy_001",
            ),
        )
        conn.commit()
        conn.close()

        store = KnowledgeStore(db_path=db_path, export_dir=Path(tmp) / "knowledge_base")
        self.addCleanup(store.close)
        row = store.get_lit_source("openalex:Wlegacy")
        self.assertTrue(row["family_id"].startswith("litfam_"))
        self.assertEqual(row["normalized_doi"], "10.1000/legacy")
        self.assertEqual(row["canonical_source_id"], "openalex:Wlegacy")
        self.assertIn("publication_date", row)
        self.assertEqual(row["is_refereed"], 0)
        self.assertEqual(row["is_retracted"], 0)
        distill = store.get_lit_distillation(
            "openalex:Wlegacy", "legacy-unbound:kb_concept_legacy_001"
        )
        self.assertEqual(distill["entry_id"], "kb_concept_legacy_001")

    def test_legacy_literature_entries_are_capped_and_blocked(self):
        tmp = tempfile.mkdtemp(prefix="kb_entry_migration_test_")
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        db_path = Path(tmp) / "knowledge.db"
        export_dir = Path(tmp) / "knowledge_base"
        store = KnowledgeStore(db_path=db_path, export_dir=export_dir)
        store.upsert_lit_source(
            {
                "source_id": "openalex:Wlegacy-entry",
                "title": "Legacy precursor review",
                "authors": ["Legacy Author"],
                "year": 2020,
                "doi": "10.1000/legacy-entry",
                "url": "https://example.org/legacy-entry",
                "abstract": "Legacy abstract.",
            }
        )
        entry = service.propose(
            store,
            entry_type="concept",
            title="Legacy literature claim",
            content={"definition": "Legacy claim."},
            source_type="literature",
            source_ref="10.1000/legacy-entry",
            confidence="high",
            provenance_extra={
                "lit_source_id": "openalex:Wlegacy-entry",
                "distill_focus": "legacy precursor focus",
                "auto_rule": "literature_support",
            },
        )["entry"]
        entry["status"] = "canonical"
        entry["version"] += 1
        store.update_entry(entry, changed_by="legacy", reason="legacy_auto_promote")
        store.close()

        migrated = KnowledgeStore(db_path=db_path, export_dir=export_dir)
        self.addCleanup(migrated.close)
        repaired = migrated.get_entry(entry["id"])
        self.assertEqual(repaired["confidence"], "medium")
        self.assertEqual(repaired["status"], "candidate")
        self.assertTrue(repaired["provenance"]["grounding_blocked"])
        self.assertTrue(repaired["provenance"]["legacy_promotion_invalidated"])
        self.assertEqual(service.search(migrated, "Legacy")["count"], 0)
        with self.assertRaises(ContractError) as ctx:
            service.read(migrated, entry["id"], purpose="grounding")
        self.assertEqual(ctx.exception.error_code, "knowledge_entry_grounding_blocked")
        reopened = migrated.get_entry(entry["id"])
        self.assertTrue(reopened["provenance"]["grounding_blocked"])
        self.assertEqual(service.search(migrated, "Legacy")["count"], 0)
        promoted = service.promote(
            migrated,
            entry["id"],
            reason="The migrated entry still needs cross-run reproduction.",
        )
        self.assertEqual(promoted["decision"], "promotion_not_ready")
        self.assertEqual(promoted["entry_status"], "candidate")

    def test_task_bound_direct_distillation_is_not_quarantined(self):
        tmp = tempfile.mkdtemp(prefix="kb_valid_distill_migration_test_")
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        db_path = Path(tmp) / "knowledge.db"
        export_dir = Path(tmp) / "knowledge_base"
        store = KnowledgeStore(db_path=db_path, export_dir=export_dir)
        entry = service.propose(
            store,
            entry_type="concept",
            title="Validated literature claim",
            content={"definition": "Validated claim."},
            source_type="literature",
            source_ref="10.1000/validated",
            confidence="medium",
            provenance_extra={
                "lit_source_id": "openalex:Wvalidated",
                "distill_focus": "validated focus",
                "research_question": "validated research question",
                "research_request_sha256": "a" * 64,
                "evidence_scope": "abstract_only",
                "independent_source_count": 1,
                "relevance": {"classification": "direct_support"},
            },
        )["entry"]
        store.close()
        reopened = KnowledgeStore(db_path=db_path, export_dir=export_dir)
        self.addCleanup(reopened.close)
        unchanged = reopened.get_entry(entry["id"])
        self.assertFalse(unchanged["provenance"].get("grounding_blocked", False))


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kb_test_")
        self.store = make_store(self.tmp)
        self.addCleanup(self.store.close)
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_new_store_has_no_approval_queue_table(self):
        row = self.store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("review_queue",),
        ).fetchone()
        self.assertIsNone(row)


class TestProposeSearchRead(StoreTestCase):
    def test_propose_is_always_candidate(self):
        result = propose_concept(self.store)
        entry = result["entry"]
        self.assertEqual(entry["status"], "candidate")
        self.assertEqual(entry["version"], 1)
        self.assertTrue(entry["id"].startswith("kb_concept_"))
        export_file = export_path(entry, self.store.export_dir)
        self.assertTrue(export_file.exists())

    def test_search_hits_chinese_bigram_query(self):
        propose_concept(self.store)
        result = service.search(self.store, "极区前兆")
        self.assertGreaterEqual(result["count"], 1)
        titles = [row["title"] for row in result["results"]]
        self.assertIn("极区磁场前兆", titles)

    def test_search_structured_filters_and_deprecated_hidden(self):
        propose_concept(self.store, title="极区磁场前兆")
        propose_concept(
            self.store,
            title="极区磁场前兆（教科书版）",
            source_type="textbook",
            source_ref="book:page-1",
            confidence="high",
        )
        high_only = service.search(self.store, "极区", confidence="high")
        self.assertEqual(high_only["count"], 1)
        typed = service.search(self.store, "极区", entry_type="mechanism")
        self.assertEqual(typed["count"], 0)

        # deprecate one entry -> hidden from default search, visible on demand
        promoted = propose_concept(
            self.store, title="极区磁场前兆（废弃版）", source_ref="expert:r-b"
        )
        service.deprecate(self.store, promoted["entry"]["id"], reason="测试废弃")
        default = service.search(self.store, "极区")
        self.assertTrue(
            all(row["status"] != "deprecated" for row in default["results"])
        )
        deprecated = service.search(self.store, "极区", status="deprecated")
        self.assertEqual(deprecated["count"], 1)

    def test_read_writes_provenance_log(self):
        entry = propose_concept(self.store)["entry"]
        service.read(
            self.store,
            entry["id"],
            agent="hypothesis-agent",
            run_id="run_001",
            purpose="grounding",
        )
        rows = self.store.provenance_for_run("run_001")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entry_id"], entry["id"])
        self.assertEqual(rows[0]["agent"], "hypothesis-agent")
        self.assertEqual(rows[0]["purpose"], "grounding")

    def test_read_missing_entry_raises(self):
        with self.assertRaises(ContractError):
            service.read(self.store, "kb_concept_missing_001")


class TestPromotionGate(StoreTestCase):
    def test_auto_rule_cross_run_reproduction(self):
        entry = service.propose(
            self.store,
            entry_type="finding",
            title="SC21–SC25 回测极区前兆与下一周峰值相关",
            content={"statement": "相关系数约 0.7", "run_id": "run_A"},
            source_type="historical_run",
            source_ref="run_A",
            confidence="low",
        )["entry"]
        service.read(self.store, entry["id"], run_id="run_B", purpose="compare")
        result = service.promote(self.store, entry["id"], reason="两次独立运行复现")
        self.assertEqual(result["decision"], "promoted")
        self.assertEqual(result["auto_rule"], "cross_run_reproduction")
        self.assertEqual(result["entry_status"], "canonical")
        promoted = self.store.get_entry(entry["id"])
        self.assertEqual(promoted["status"], "canonical")

    def test_literature_with_doi_stays_candidate_without_reproduction(self):
        entry = propose_concept(
            self.store,
            title="Babcock-Leighton 发电机",
            entry_type="mechanism",
            content={"claim": "极区场在极小期作为下一周种子场。"},
            source_type="literature",
            source_ref="doi:10.1086/xyz123",
        )["entry"]
        result = service.promote(self.store, entry["id"], reason="文献直接支撑")
        self.assertEqual(result["decision"], "promotion_not_ready")
        self.assertEqual(result["entry_status"], "candidate")

    def test_single_run_evidence_keeps_candidate(self):
        entry = propose_concept(self.store, title="单次运行条目")["entry"]
        result = service.promote(self.store, entry["id"], reason="孤证")
        self.assertEqual(result["decision"], "promotion_not_ready")
        self.assertEqual(self.store.get_entry(entry["id"])["status"], "candidate")

    def test_promote_non_candidate_rejected(self):
        entry = service.propose(
            self.store,
            entry_type="finding",
            title="已 canonical 条目",
            content={"statement": "可复现条目。", "run_id": "run_A"},
            source_type="historical_run",
            source_ref="run_A",
            confidence="low",
        )["entry"]
        service.read(self.store, entry["id"], run_id="run_B", purpose="compare")
        service.promote(self.store, entry["id"], reason="两次独立运行复现")
        with self.assertRaises(ContractError):
            service.promote(self.store, entry["id"], reason="重复晋升")

    def test_promote_requires_reason(self):
        entry = propose_concept(self.store, title="无理由条目")["entry"]
        with self.assertRaises(ContractError):
            service.promote(self.store, entry["id"], reason="  ")


class TestDeprecateAndVersions(StoreTestCase):
    def test_deprecate_snapshots_allow_rollback(self):
        entry = propose_concept(self.store, title="将废弃条目")["entry"]
        entry["status"] = "canonical"
        entry["version"] += 1
        self.store.update_entry(entry, changed_by="fixture", reason="seed canonical")
        result = service.deprecate(self.store, entry["id"], reason="新证据矛盾")
        self.assertEqual(result["entry_status"], "deprecated")
        self.assertEqual(result["version"], 3)

        versions = self.store.list_versions(entry["id"])
        self.assertEqual([row["version"] for row in versions], [1, 2, 3])
        v2 = self.store.get_version(entry["id"], 2)
        self.assertEqual(v2["status"], "canonical")  # 回滚来源
        v3 = self.store.get_version(entry["id"], 3)
        self.assertEqual(v3["status"], "deprecated")
        self.assertEqual(v3["provenance"]["deprecate_reason"], "新证据矛盾")

    def test_supersede_marks_status_and_link(self):
        old = propose_concept(self.store, title="旧条目")["entry"]
        new = propose_concept(self.store, title="新条目")["entry"]
        result = service.deprecate(
            self.store, old["id"], reason="被新条目取代", superseded_by=new["id"]
        )
        self.assertEqual(result["entry_status"], "superseded")
        entry = self.store.get_entry(old["id"])
        self.assertEqual(entry["provenance"]["superseded_by"], new["id"])

    def test_supersede_requires_existing_replacement(self):
        entry = propose_concept(self.store, title="孤立条目")["entry"]
        with self.assertRaises(ContractError):
            service.deprecate(
                self.store,
                entry["id"],
                reason="x",
                superseded_by="kb_concept_ghost_001",
            )

    def test_double_deprecate_rejected(self):
        entry = propose_concept(self.store, title="单次废弃条目")["entry"]
        service.deprecate(self.store, entry["id"], reason="第一次")
        with self.assertRaises(ContractError):
            service.deprecate(self.store, entry["id"], reason="第二次")


class TestConflictsAndLog(StoreTestCase):
    def test_counterexample_against_canonical_queues_conflict(self):
        canonical = propose_concept(
            self.store,
            title="极区前兆机制",
            entry_type="mechanism",
            content={"claim": "极区场强度决定下一周振幅。"},
        )["entry"]
        canonical["status"] = "canonical"
        canonical["version"] += 1
        self.store.update_entry(
            canonical, changed_by="fixture", reason="seed canonical"
        )

        result = service.propose(
            self.store,
            entry_type="counterexample",
            title="SC23 极区场强但 SC24 弱",
            content={
                "statement": "SC23 极小期极区场不弱，SC24 仍为最弱周之一。",
                "run_id": "run_X",
            },
            source_type="historical_run",
            source_ref="run_X",
            confidence="medium",
            related_ids=[canonical["id"]],
        )
        self.assertIn("conflicts", result)
        pending = service.conflicts(self.store)
        self.assertEqual(pending["count"], 1)
        item = pending["conflicts"][0]
        self.assertEqual(item["canonical_id"], canonical["id"])
        self.assertEqual(item["candidate_id"], result["entry"]["id"])

        by_entry = service.conflicts(self.store, entry_id=canonical["id"])
        self.assertEqual(by_entry["count"], 1)
        unrelated = service.conflicts(self.store, entry_id="kb_concept_none_999")
        self.assertEqual(unrelated["count"], 0)

        # 未解决冲突不自动改写条目（R5：不静默覆盖）
        self.assertEqual(self.store.get_entry(canonical["id"])["status"], "canonical")
        self.assertEqual(
            self.store.get_entry(result["entry"]["id"])["status"], "candidate"
        )

    def test_non_counterexample_propose_has_no_conflict(self):
        canonical = propose_concept(self.store, title="基线概念")["entry"]
        canonical["status"] = "canonical"
        canonical["version"] += 1
        self.store.update_entry(canonical, changed_by="fixture", reason="seed canonical")
        result = propose_concept(
            self.store, title="关联概念", related_ids=[canonical["id"]]
        )
        self.assertNotIn("conflicts", result)

    def test_usage_log_lists_reads_and_proposals(self):
        entry = propose_concept(self.store, title="被用条目", run_id="run_42")["entry"]
        service.read(self.store, entry["id"], run_id="run_42", purpose="grounding")
        report = service.usage_log(self.store, "run_42")
        self.assertEqual(report["used_count"], 1)
        self.assertEqual(report["proposed_count"], 1)
        self.assertEqual(report["entries_proposed"][0]["id"], entry["id"])
        empty = service.usage_log(self.store, "run_nope")
        self.assertEqual(empty["used_count"], 0)
        self.assertEqual(empty["proposed_count"], 0)


class TestLiteratureImpactPatchLifecycle(StoreTestCase):
    def _source_and_entry(self):
        source = self.store.upsert_lit_source(
            {
                "source_id": "openalex:Wimpact",
                "provider": "openalex",
                "source_version": "1",
                "title": "Polar field precursor qualification",
                "authors": ["Ada Solar"],
                "year": 2026,
                "abstract": (
                    "Polar field strength predicts the next cycle amplitude "
                    "only when measurements near solar minimum are calibrated."
                ),
                "is_retracted": False,
            }
        )
        entry = propose_concept(self.store, title="极区场前兆适用范围")["entry"]
        return source, entry

    def _impact(self, source, entry):
        from knowledge_base import literature

        return literature.record_literature_entry_impact(
            self.store,
            source_id=source["source_id"],
            entry_id=entry["id"],
            relation="qualifies",
            affected_fields=["definition", "valid_range"],
            scope={"phase": "near solar minimum"},
            quote=(
                "predicts the next cycle amplitude only when measurements "
                "near solar minimum are calibrated"
            ),
            location="abstract",
            rationale="The source narrows the precursor claim to calibrated minima.",
            confidence="low",
        )["impact"]

    def test_patch_remains_non_applying_proposal(self):
        source, entry = self._source_and_entry()
        impact = self._impact(source, entry)
        proposal = service.propose_literature_patch(
            self.store,
            impact["id"],
            field_updates={
                "definition": ("经校准的极小期极区磁场可作为下一活动周振幅的前兆。")
            },
            valid_range="接近太阳极小期且跨仪器校准完成",
            rationale="把单源限定写入现有定义和适用范围。",
        )
        patch = proposal["patch"]
        self.assertEqual(patch["status"], "proposal_only")
        self.assertEqual(self.store.get_entry(entry["id"])["version"], 1)
        updated = self.store.get_entry(entry["id"])
        self.assertEqual(updated["version"], 1)
        self.assertNotIn("经校准", updated["content"]["definition"])
        self.assertEqual(
            self.store.get_lit_entry_impact(impact["id"])["status"], "proposed"
        )

    def test_changed_entry_gets_a_new_patch_proposal(self):
        source, entry = self._source_and_entry()
        impact = self._impact(source, entry)
        patch = service.propose_literature_patch(
            self.store,
            impact["id"],
            field_updates={"definition": "候选的新定义。"},
            rationale="测试 stale 保护。",
        )["patch"]
        current = self.store.get_entry(entry["id"])
        current["content"]["definition"] = "外部版本已经更新。"
        current["version"] += 1
        self.store.update_entry(current, changed_by="fixture", reason="concurrent_edit")
        unchanged = self.store.get_entry(entry["id"])
        self.assertEqual(unchanged["content"]["definition"], "外部版本已经更新。")
        refreshed = service.propose_literature_patch(
            self.store,
            impact["id"],
            field_updates={"definition": "候选的新定义。"},
            rationale="针对新版本重新评估。",
        )["patch"]
        self.assertNotEqual(refreshed["patch_id"], patch["patch_id"])
        self.assertEqual(refreshed["base_version"], 2)
        self.assertEqual(refreshed["status"], "proposal_only")

    def test_retraction_blocks_grounding_without_deleting_source(self):
        source, entry = self._source_and_entry()
        impact = self._impact(source, entry)
        self.store.upsert_lit_source(
            {
                **source,
                "is_retracted": True,
            }
        )
        self.assertIsNotNone(self.store.get_lit_source(source["source_id"]))
        self.assertTrue(
            self.store.get_entry(entry["id"])["provenance"]["grounding_blocked"]
        )
        self.assertEqual(
            self.store.get_lit_entry_impact(impact["id"])["status"],
            "source_retracted",
        )


class TestMarkdownRoundTrip(StoreTestCase):
    def test_export_maintains_llm_wiki_scaffold_and_catalog(self):
        entry = propose_concept(self.store, title="可导航的知识页")["entry"]
        root = self.store.export_dir
        for name in ("purpose.md", "schema.md", "index.md", "overview.md", "log.md"):
            self.assertTrue((root / name).is_file(), name)
        index = (root / "index.md").read_text(encoding="utf-8")
        self.assertIn(entry["id"], index)
        self.assertIn("[[concept/", index)
        overview = (root / "overview.md").read_text(encoding="utf-8")
        self.assertIn("Pages: 1", overview)
        log = (root / "log.md").read_text(encoding="utf-8")
        self.assertIn(f"{entry['id']}:v1", log)

        # Root navigation/config files are not treated as importable entries.
        result = service.import_markdown(self.store, root)
        self.assertEqual(result["files_seen"], 1)
        self.assertEqual(result["errors"], [])

    def test_export_then_reimport_bumps_version_and_matches(self):
        entry = service.propose(
            self.store,
            entry_type="mechanism",
            title="通量输运发电机",
            content={
                "claim": "经向环流控制周期间时间尺度。",
                "supporting_evidence": "日震学观测显示环流速度周际变化。",
                "testable_predictions": [
                    "极小期长度与深层环流速度相关",
                    "极区场强度预测下周振幅",
                ],
            },
            source_type="literature",
            source_ref="Dikpati & Charbonneau 1999",
            confidence="medium",
            valid_range="flux-transport dominated regime",
            related_ids=["kb_concept_solar_cycle_001"],
        )["entry"]

        path = export_path(entry, self.store.export_dir)
        parsed = import_entry_file(path)
        self.assertEqual(parsed["id"], entry["id"])
        self.assertEqual(parsed["content"], entry["content"])
        self.assertEqual(parsed["related_ids"], entry["related_ids"])
        self.assertEqual(parsed["valid_range"], entry["valid_range"])

        # 模拟手工编辑后回导：版本 +1，内容一致且可追溯
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("经向环流控制", "经向环流主导"), encoding="utf-8")
        result = service.import_markdown(self.store, path)
        self.assertEqual(result["updated"], [entry["id"]])
        reloaded = self.store.get_entry(entry["id"])
        self.assertEqual(reloaded["version"], 2)
        self.assertIn("主导", reloaded["content"]["claim"])
        v1 = self.store.get_version(entry["id"], 1)
        self.assertIn("控制", v1["content"]["claim"])

    def test_import_new_markdown_creates_entry(self):
        entry = propose_concept(self.store, title="导出样例")["entry"]
        path = export_path(entry, self.store.export_dir)
        other = make_store(tempfile.mkdtemp(prefix="kb_test_other_"))
        self.addCleanup(other.close)
        result = service.import_markdown(other, path)
        self.assertEqual(result["imported"], [entry["id"]])
        clone = other.get_entry(entry["id"])
        self.assertEqual(clone["title"], entry["title"])
        self.assertEqual(clone["version"], 1)

    def test_import_missing_path_raises(self):
        with self.assertRaises(ContractError):
            service.import_markdown(self.store, Path(self.tmp) / "nope.md")


if __name__ == "__main__":
    unittest.main()
