"""工具层上下文注入与晋升证据测试（kb_read/kb_propose/kb_promote 的 RunnableConfig 回填）。

db 用临时目录隔离（JW_DATA_DIR / JW_KB_EXPORT_DIR），不碰真实
~/.jw 与临时 knowledge_base/ 导出树；不访问网络。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
for candidate in (str(SRC), str(ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from jw.tools import knowledge_base as kb_tools  # noqa: E402


def _candidate_payload(**overrides):
    payload = {
        "type": "finding",
        "title": "上下文测试候选",
        "content": {"statement": "测试发现", "run_id": "run-1"},
        "source_type": "historical_run",
        "source_ref": "run-1",
        "confidence": "low",
    }
    payload.update(overrides)
    return payload


class ToolContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="kb-toolctx-")
        self._old_env = {
            key: os.environ.get(key)
            for key in ("JW_DATA_DIR", "JW_KB_EXPORT_DIR")
        }
        os.environ["JW_DATA_DIR"] = self._tmp
        os.environ["JW_KB_EXPORT_DIR"] = str(Path(self._tmp) / "export")
        kb_tools._STORE = None
        kb_tools._DISTILL_BINDINGS.clear()
        kb_tools._ACTIVE_DISTILL_BINDINGS.clear()

    def tearDown(self) -> None:
        kb_tools._STORE = None
        kb_tools._DISTILL_BINDINGS.clear()
        kb_tools._ACTIVE_DISTILL_BINDINGS.clear()
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _propose(self, **overrides) -> str:
        result = json.loads(kb_tools.kb_propose.invoke(_candidate_payload(**overrides)))
        assert result.get("entry", {}).get("id") or result.get("entry_id"), result
        return result.get("entry", {}).get("id") or result["entry_id"]

    # -- kb_read：run_id/agent 从 RunnableConfig 回填 ---------------------

    def test_kb_read_falls_back_to_run_config(self) -> None:
        entry_id = self._propose()
        result = json.loads(
            kb_tools.kb_read.invoke(
                {"entry_id": entry_id, "purpose": "grounding"},
                config={
                    "configurable": {"thread_id": "thread-ctx-1"},
                    "metadata": {"langgraph_node": "agent"},
                },
            )
        )
        self.assertIn("entry", result)
        rows = kb_tools._get_store().provenance_for_run("thread-ctx-1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entry_id"], entry_id)
        self.assertEqual(rows[0]["purpose"], "grounding")

    def test_kb_read_explicit_attribution_wins(self) -> None:
        entry_id = self._propose()
        kb_tools.kb_read.invoke(
            {"entry_id": entry_id, "run_id": "explicit-run", "agent": "caller"},
            config={"configurable": {"thread_id": "thread-ctx-2"}},
        )
        rows = kb_tools._get_store().provenance_for_run("explicit-run")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["agent"], "caller")
        self.assertEqual(kb_tools._get_store().provenance_for_run("thread-ctx-2"), [])

    # -- lit_distill：任务绑定收据不可绕过 -------------------------------

    def _cache_literature(self) -> None:
        kb_tools._get_store().upsert_lit_source(
            {
                "source_id": "openalex:Wbound",
                "title": "Polar field precursor",
                "authors": ["A. Author"],
                "year": 2024,
                "doi": "10.1000/bound",
                "url": "https://example.org/bound",
                "abstract": (
                    "The polar field precursor predicts the next solar cycle amplitude."
                ),
            }
        )

    def test_lit_distill_requires_bound_task(self) -> None:
        self._cache_literature()
        result = json.loads(
            kb_tools.lit_distill.invoke(
                {
                    "source_id": "openalex:Wbound",
                    "entry_type": "concept",
                    "title": "Polar field precursor",
                    "content": {
                        "definition": {
                            "text": "polar field precursor predicts amplitude",
                            "quote": "polar field precursor predicts the next solar cycle amplitude",
                            "location": "abstract",
                        }
                    },
                }
            )
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("lit_bind_task", result["error"])

    def test_lit_binding_is_scoped_and_used_by_distill(self) -> None:
        self._cache_literature()
        config = {"configurable": {"thread_id": "thread-lit-bound"}}
        binding = json.loads(
            kb_tools.lit_bind_task.invoke(
                {
                    "research_question": (
                        "Does the polar field precursor predict solar cycle amplitude?"
                    ),
                    "distill_focus": (
                        "polar field precursor for solar cycle amplitude"
                    ),
                },
                config=config,
            )
        )
        self.assertEqual(binding["status"], "ok")
        result = json.loads(
            kb_tools.lit_distill.invoke(
                {
                    "source_id": "openalex:Wbound",
                    "entry_type": "concept",
                    "title": "Polar field precursor",
                    "content": {
                        "definition": {
                            "text": "polar field precursor predicts amplitude",
                            "quote": "polar field precursor predicts the next solar cycle amplitude",
                            "location": "abstract",
                        }
                    },
                },
                config=config,
            )
        )
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["research_request_sha256"], binding["binding_id"])

    def test_kb_promote_requires_cross_run_reproduction(self) -> None:
        entry_id = self._propose()
        result = json.loads(
            kb_tools.kb_promote.invoke(
                {"entry_id": entry_id, "reason": "证据尚不足"}
            )
        )
        self.assertEqual(result.get("decision"), "promotion_not_ready", result)
        entry = kb_tools._get_store().get_entry(entry_id)
        self.assertEqual(entry["status"], "candidate")

if __name__ == "__main__":
    unittest.main()
