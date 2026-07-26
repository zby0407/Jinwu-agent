"""知识库只读 HTTP API 测试：starlette TestClient + 临时 db，不碰真实 ~/.jw。

被测对象是 ``JW.langgraph_dev.http:app``（langgraph.json 里挂的
同一个 app），通过 ``JW_DATA_DIR`` 把 ``default_db_path()`` 指到
临时目录，验证 knowledge_api 的条目、来源、图谱、概览和审核端点。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
for path in (str(ROOT), str(SRC)):
    if path not in sys.path:
        sys.path.insert(0, path)

from starlette.testclient import TestClient  # noqa: E402

from jw.langgraph_dev.http import app  # noqa: E402
from knowledge_base import service  # noqa: E402
from knowledge_base.store import KnowledgeStore  # noqa: E402


class HttpApiTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kb_http_test_")
        self._old_env = os.environ.get("JW_DATA_DIR")
        os.environ["JW_DATA_DIR"] = self.tmp
        self.addCleanup(self._restore)
        self.client = TestClient(app)
        self.store = KnowledgeStore(
            db_path=Path(self.tmp) / "knowledge.db",
            export_dir=Path(self.tmp) / "knowledge_base",
        )
        self.addCleanup(self.store.close)

    def _restore(self):
        if self._old_env is None:
            os.environ.pop("JW_DATA_DIR", None)
        else:
            os.environ["JW_DATA_DIR"] = self._old_env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _propose(self, title="极区磁场前兆", **overrides):
        params = {
            "entry_type": "concept",
            "title": title,
            "content": {"definition": "极区磁场强度可作为下一活动周振幅的前兆。"},
            "source_type": "expert",
            "source_ref": "expert:reviewer-a",
            "confidence": "medium",
        }
        params.update(overrides)
        return service.propose(self.store, **params)["entry"]


class TestEntriesApi(HttpApiTestCase):
    def test_entries_returns_list_with_summary_fields(self):
        entry = self._propose()
        resp = self.client.get("/api/kb/entries")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["cache-control"], "no-store")
        rows = resp.json()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["id"], entry["id"])
        self.assertEqual(row["type"], "concept")
        self.assertEqual(row["status"], "candidate")
        for key in ("title", "confidence", "valid_range", "updated_at", "source_ref"):
            self.assertIn(key, row)
        # 列表不带大字段
        self.assertNotIn("content", row)

    def test_entries_filters(self):
        self._propose(title="极区磁场前兆")
        self._propose(
            title="BBSO 磁场数据源",
            entry_type="data_source",
            content={"collection_method": "地面望远镜观测"},
            source_ref="dataset:bbso",
        )
        typed = self.client.get("/api/kb/entries?type=data_source").json()
        self.assertEqual(len(typed), 1)
        self.assertEqual(typed[0]["title"], "BBSO 磁场数据源")

        searched = self.client.get("/api/kb/entries?q=极区").json()
        self.assertEqual(len(searched), 1)
        self.assertEqual(searched[0]["title"], "极区磁场前兆")

        by_status = self.client.get("/api/kb/entries?status=canonical").json()
        self.assertEqual(by_status, [])

        limited = self.client.get("/api/kb/entries?limit=1").json()
        self.assertEqual(len(limited), 1)

    def test_entry_detail_parses_json_and_counts_versions(self):
        entry = self._propose()
        resp = self.client.get(f"/api/kb/entries/{entry['id']}")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIsInstance(body["content"], dict)
        self.assertIn("definition", body["content"])
        self.assertIsInstance(body["provenance"], dict)
        self.assertEqual(body["version_count"], 1)
        self.assertEqual(len(body["versions"]), 1)
        self.assertEqual(body["versions"][0]["version"], 1)

    def test_entry_detail_404(self):
        resp = self.client.get("/api/kb/entries/kb_missing_0001")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.headers["cache-control"], "no-store")
        self.assertIn("error", resp.json())


class TestWikiOverviewAndSourcesApi(HttpApiTestCase):
    def _source_backed_entry(self):
        source = self.store.upsert_lit_source(
            {
                "source_id": "crossref:10.1000/wiki",
                "provider": "crossref",
                "title": "A source-grounded Wiki",
                "authors": ["Ada Researcher"],
                "year": 2026,
                "doi": "10.1000/wiki",
                "url": "https://doi.org/10.1000/wiki",
                "abstract": "A source-grounded claim with a traceable quotation.",
            }
        )
        entry = self._propose(
            title="Source-grounded concept",
            source_type="literature",
            source_ref="10.1000/wiki",
            provenance_extra={
                "lit_source_id": source["source_id"],
                "lit_family_id": source["family_id"],
                "evidence_map": {
                    "definition": {
                        "quote": "source-grounded claim",
                        "location": "abstract",
                    }
                },
            },
        )
        self.store.record_lit_distillation(
            source_id=source["source_id"],
            focus="wiki grounding",
            research_question="How is the Wiki grounded?",
            research_request_sha256="a" * 64,
            entry_id=entry["id"],
            relevance="direct_support",
        )
        return source, entry

    def test_overview_reports_pipeline_coverage_and_gaps(self):
        self._source_backed_entry()
        body = self.client.get("/api/kb/overview").json()
        self.assertEqual(body["entries"], 1)
        self.assertEqual(body["sources"], 1)
        self.assertEqual(body["distilled_sources"], 1)
        self.assertEqual(body["by_provider"], {"crossref": 1})
        self.assertEqual(body["coverage"]["distillation_rate"], 1.0)
        self.assertIn("candidate_backlog", {gap["code"] for gap in body["gaps"]})

    def test_sources_list_detail_and_state_filter(self):
        source, entry = self._source_backed_entry()
        rows = self.client.get("/api/kb/sources?state=distilled").json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_id"], source["source_id"])
        self.assertEqual(rows[0]["stage"], "distilled")
        self.assertEqual(rows[0]["authors"], ["Ada Researcher"])

        detail = self.client.get(f"/api/kb/sources/{source['source_id']}").json()
        self.assertEqual(detail["distillation_count"], 1)
        self.assertEqual(detail["distillations"][0]["entry_id"], entry["id"])
        self.assertIn("traceable quotation", detail["abstract"])

    def test_entry_detail_surfaces_evidence_source_and_related_titles(self):
        source, entry = self._source_backed_entry()
        related = self._propose(title="Related page")
        raw = self.store.get_entry(entry["id"])
        raw["related_ids"] = [related["id"]]
        raw["version"] += 1
        self.store.update_entry(raw, changed_by="test", reason="link pages")

        body = self.client.get(f"/api/kb/entries/{entry['id']}").json()
        self.assertEqual(body["source"]["source_id"], source["source_id"])
        self.assertEqual(body["evidence"]["definition"]["location"], "abstract")
        self.assertEqual(body["related_entries"][0]["title"], "Related page")

    def test_graph_contains_source_grounding_and_direct_links(self):
        source, entry = self._source_backed_entry()
        related = self._propose(title="Related page")
        raw = self.store.get_entry(entry["id"])
        raw["related_ids"] = [related["id"]]
        raw["version"] += 1
        self.store.update_entry(raw, changed_by="test", reason="link pages")

        graph = self.client.get("/api/kb/graph").json()
        node_ids = {node["id"] for node in graph["nodes"]}
        self.assertIn(entry["id"], node_ids)
        self.assertIn(f"source:{source['source_id']}", node_ids)
        relations = {edge["relation"] for edge in graph["edges"]}
        self.assertIn("distilled_into", relations)
        self.assertIn("related_to", relations)


class TestReviewQueueApi(HttpApiTestCase):
    def test_pending_queue_items(self):
        entry = self._propose()
        self.store.add_review_item(
            kind="promote",
            entry_id=entry["id"],
            payload={"reason": "跨运行复现", "supporting_run_ids": ["run-1"]},
        )
        resp = self.client.get("/api/kb/review_queue?status=pending")
        self.assertEqual(resp.status_code, 200)
        items = resp.json()
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["kind"], "promote")
        self.assertEqual(item["entry_id"], entry["id"])
        self.assertEqual(item["status"], "pending")
        self.assertIsInstance(item["payload"], dict)
        self.assertEqual(item["payload"]["reason"], "跨运行复现")

    def test_status_filter_excludes_decided(self):
        entry = self._propose()
        queue_id = self.store.add_review_item(
            kind="promote", entry_id=entry["id"], payload={"reason": "r"}
        )
        self.store.decide_review_item(
            queue_id, status="approved", reviewer="human", note="ok"
        )
        self.assertEqual(self.client.get("/api/kb/review_queue").json(), [])
        decided = self.client.get("/api/kb/review_queue?status=approved").json()
        self.assertEqual(len(decided), 1)
        self.assertEqual(decided[0]["reviewer"], "human")
        all_items = self.client.get("/api/kb/review_queue?status=all").json()
        self.assertEqual(len(all_items), 1)


class TestUsageApi(HttpApiTestCase):
    def test_usage_by_run_and_recent(self):
        entry = self._propose()
        service.read(
            self.store,
            entry["id"],
            agent="planner",
            run_id="run-1",
            purpose="grounding",
        )
        service.read(
            self.store, entry["id"], agent="hypothesis", run_id="run-2", purpose="cite"
        )
        by_run = self.client.get("/api/kb/usage?run_id=run-1").json()
        self.assertEqual(len(by_run), 1)
        self.assertEqual(by_run[0]["agent"], "planner")
        self.assertEqual(by_run[0]["entry_title"], entry["title"])
        self.assertEqual(by_run[0]["purpose"], "grounding")
        self.assertIn("ts", by_run[0])

        recent = self.client.get("/api/kb/usage").json()
        self.assertEqual(len(recent), 2)
        # 最新在前
        self.assertEqual(recent[0]["run_id"], "run-2")


class TestMissingDatabase(unittest.TestCase):
    """db 不存在：列表端点返回空数组，详情返回 404 JSON，都不 500。"""

    def test_endpoints_degrade_gracefully(self):
        tmp = tempfile.mkdtemp(prefix="kb_http_missing_")
        old_env = os.environ.get("JW_DATA_DIR")
        os.environ["JW_DATA_DIR"] = tmp
        try:
            client = TestClient(app)
            for path in (
                "/api/kb/entries",
                "/api/kb/sources",
                "/api/kb/review_queue",
                "/api/kb/usage",
            ):
                resp = client.get(path)
                self.assertEqual(resp.status_code, 200, path)
                self.assertEqual(resp.json(), [], path)
                self.assertEqual(resp.headers["cache-control"], "no-store", path)
            overview = client.get("/api/kb/overview")
            self.assertEqual(overview.status_code, 200)
            self.assertEqual(overview.json()["entries"], 0)
            graph = client.get("/api/kb/graph")
            self.assertEqual(graph.status_code, 200)
            self.assertEqual(graph.json()["nodes"], [])
            resp = client.get("/api/kb/entries/kb_whatever_0001")
            self.assertEqual(resp.status_code, 404)
            self.assertIn("error", resp.json())
            source_resp = client.get("/api/kb/sources/openalex:missing")
            self.assertEqual(source_resp.status_code, 404)
        finally:
            if old_env is None:
                os.environ.pop("JW_DATA_DIR", None)
            else:
                os.environ["JW_DATA_DIR"] = old_env
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
