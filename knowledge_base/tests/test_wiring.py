"""闭环接线测试（方案 §5.4 #1-#4）：brief 注入、假设/实验引用门禁、finalize 写回。

db 用临时目录隔离（EVOSCIENTIST_DATA_DIR / EVOSCIENTIST_KB_EXPORT_DIR），不碰真实
~/.evoscientist 与仓库 knowledge_base/ 导出树；不访问网络。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for candidate in (str(SRC), str(ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from knowledge_base import service as kb_service  # noqa: E402
from knowledge_base.store import KnowledgeStore  # noqa: E402
from research_planner.harness import (  # noqa: E402
    build_natural_planner_request,
    build_planning_brief,
)
from scientific_hypothesis.contracts import (  # noqa: E402
    REQUEST_VERSION as HYP_REQUEST_VERSION,
    RESPONSE_VERSION as HYP_RESPONSE_VERSION,
    validate_hypothesis_request,
)
from scientific_hypothesis.harness import (  # noqa: E402
    EvidenceRegister,
    preflight_hypothesis_response,
)


class WiringTestCase(unittest.TestCase):
    """Isolate the kb behind env vars and pre-seed one concept entry."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kb_wiring_test_")
        self._old_env = {
            key: os.environ.get(key)
            for key in ("EVOSCIENTIST_DATA_DIR", "EVOSCIENTIST_KB_EXPORT_DIR")
        }
        os.environ["EVOSCIENTIST_DATA_DIR"] = str(Path(self.tmp) / "data")
        os.environ["EVOSCIENTIST_KB_EXPORT_DIR"] = str(Path(self.tmp) / "export")
        self.addCleanup(self._restore_env)
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        store = KnowledgeStore()
        proposed = kb_service.propose(
            store,
            entry_type="concept",
            title="极区磁场前兆",
            content={"definition": "极区磁场在极小期附近的强度可作为下一活动周振幅的前兆。"},
            source_type="expert",
            source_ref="expert:reviewer-a",
            confidence="medium",
        )
        self.kb_entry_id = proposed["entry"]["id"]
        store.close()

    def _restore_env(self):
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def read_entry(self, entry_id: str):
        store = KnowledgeStore()
        try:
            return store.get_entry(entry_id)
        finally:
            store.close()


# ----------------------------------------------------------------------
# §5.4 #1：planner brief 注入 related_candidates / open_conflicts
# ----------------------------------------------------------------------
class TestPlannerBriefInjection(WiringTestCase):
    def test_brief_carries_related_candidates_and_open_conflicts(self):
        request = build_natural_planner_request("极区磁场前兆能否预测下一活动周振幅？")
        brief = build_planning_brief(request)
        self.assertIn("related_candidates", brief)
        self.assertIn("open_conflicts", brief)
        ids = [row["id"] for row in brief["related_candidates"]]
        self.assertIn(self.kb_entry_id, ids)
        self.assertEqual(brief["open_conflicts"], [])
        # 注入只增不改：原有 brief 字段保持不变
        self.assertEqual(brief["schema_version"], "research-planner-brief-v1")
        self.assertIn("planner_contract", brief)

    def test_brief_silent_degradation_when_kb_unavailable(self):
        # 把 DATA_DIR 指向一个已存在的文件：KnowledgeStore() 建库必然失败，
        # brief 必须退回 P2 之前的行为（无注入字段）而不是抛错。
        blocker = Path(self.tmp) / "not_a_dir"
        blocker.write_text("block", encoding="utf-8")
        os.environ["EVOSCIENTIST_DATA_DIR"] = str(blocker / "data")
        request = build_natural_planner_request("极区磁场前兆能否预测下一活动周振幅？")
        brief = build_planning_brief(request)
        self.assertNotIn("related_candidates", brief)
        self.assertNotIn("open_conflicts", brief)


# ----------------------------------------------------------------------
# §5.4 #2：假设引用门禁（warning 模式）
# ----------------------------------------------------------------------
def make_hyp_request(**overrides):
    request = {
        "schema_version": HYP_REQUEST_VERSION,
        "task_name": "test_kb_gate",
        "research_question": "极区磁场前兆能否解释相邻活动周振幅差异？",
        "upstream_materials": [],
        "prior_hypotheses": [],
        "max_candidates": 4,
    }
    request.update(overrides)
    return validate_hypothesis_request(request)


def make_candidate(cid="cand_a", supporting=None, gaps=None):
    return {
        "id": cid,
        "statement": "极小期极区磁场减弱导致下一活动周振幅下降",
        "applicability": "仅适用于以极区磁场为先兆因子的活动周振幅评估",
        "mechanism": {
            "summary": "极区磁场经发电机过程决定下一周种子场强",
            "physical_basis": "通量传输发电机框架下的先兆关系",
            "required_premises": ["极区磁场可作为种子场的可观测量"],
        },
        "assumptions": ["观测口径在不同活动周之间保持一致"],
        "predictions": [
            {
                "id": "pred_a",
                "statement": "极小期极区磁场偏弱时下一周振幅偏低",
                "observable": "极小期极区磁场强度与下一周峰值黑子数",
                "distinguishes_from": [cid],
                "would_weaken_if": "极小期极区磁场与下一周振幅无相关",
            }
        ],
        "supporting_evidence": supporting or [],
        "opposing_evidence": [],
        "evidence_gaps": gaps if gaps is not None else [],
        "alternative_explanations": ["振幅差异来自观测口径变化"],
        "confounders": ["数据产品版本差异"],
        "falsification_conditions": ["同口径复算后先兆关系消失"],
        "next_test": {
            "objective": "核对极区磁场与下一周振幅的对应关系",
            "discriminating_power": "先兆解释预测相关，口径解释预测无相关",
            "expected_signals": ["极小期磁场与振幅同向变化"],
            "candidate_ids_distinguished": [cid],
        },
        "confidence": {"level": "low", "basis": "仅有机制设想与有限文献线索"},
        "evidence_update": None,
        "prior_version_id": None,
    }


def make_hyp_response(request, candidates):
    return {
        "schema_version": HYP_RESPONSE_VERSION,
        "task_name": request["task_name"],
        "research_question": request["research_question"],
        "response_kind": "hypotheses_ready",
        "candidates": candidates,
        "pairwise_distinctions": [],
        "portfolio_notes": None,
    }


def bind(register, evidence_id, kind="upstream", role="supports"):
    return register.bind(
        {
            "evidence_id": evidence_id,
            "evidence_kind": kind,
            "material_id": "mat_src1",
            "excerpt": "极区磁场与下一周振幅存在对应关系",
            "verified_support": True,
            "role": role,
        }
    )


class TestHypothesisGroundingGate(WiringTestCase):
    def preflight(self, candidate):
        request = make_hyp_request()
        register = EvidenceRegister()
        for link in candidate["supporting_evidence"] + candidate["opposing_evidence"]:
            kind = "literature" if link["evidence_id"].startswith("kb_") else "upstream"
            bind(register, link["evidence_id"], kind=kind)
        response = make_hyp_response(request, [candidate])
        return preflight_hypothesis_response(request, response, register)

    def test_candidate_without_kb_reference_gets_warning(self):
        candidate = make_candidate(
            supporting=[{"evidence_id": "ev_up1", "relation_note": "上游复算支持"}],
            gaps=[],
        )
        result = self.preflight(candidate)
        self.assertEqual(result["status"], "hypotheses_ready")  # warning 模式不 fail
        missing = result["warnings"]["kb_grounding_missing"]
        self.assertEqual([row["id"] for row in missing], ["cand_a"])
        self.assertEqual(missing[0]["kb_ids_cited"], [])

    def test_candidate_with_valid_kb_reference_passes(self):
        candidate = make_candidate(
            supporting=[
                {"evidence_id": self.kb_entry_id, "relation_note": "知识库先兆条目"}
            ],
            gaps=[],
        )
        result = self.preflight(candidate)
        self.assertEqual(result["warnings"]["kb_grounding_missing"], [])

    def test_candidate_with_knowledge_gap_declaration_passes(self):
        candidate = make_candidate(gaps=["缺少极小期极区磁场的同口径序列"])
        result = self.preflight(candidate)
        self.assertEqual(result["warnings"]["kb_grounding_missing"], [])


# ----------------------------------------------------------------------
# §5.4 #3：实验设计引用门禁（warning 模式）
# ----------------------------------------------------------------------
class TestExperimentGroundingGate(WiringTestCase):
    def setUp(self):
        super().setUp()
        from experiment.tests import helpers as exp_helpers

        self.exp_helpers = exp_helpers

    def _bind_run(self):
        from automatic_experiment import service as exp_service

        req = self.exp_helpers.request(task_name="unit_kb_wiring")
        run_id = exp_service.bind_request({"request": req})["run_id"]
        self.addCleanup(lambda: self._cleanup_run(run_id))
        return exp_service, req, run_id

    @staticmethod
    def _cleanup_run(run_id: str):
        from automatic_experiment.state import RUNS_ROOT

        root = (RUNS_ROOT / run_id).resolve()
        if root.parent == RUNS_ROOT.resolve() and root.name.startswith("unit_"):
            shutil.rmtree(root, ignore_errors=True)

    def test_design_without_kb_reference_gets_warning(self):
        exp_service, req, run_id = self._bind_run()
        result = exp_service.validate_and_store_design(
            run_id, self.exp_helpers.response(req), self.exp_helpers.design(req)
        )
        self.assertEqual(result["status"], "design_validated")
        missing = result["warnings"]["kb_grounding_missing"]
        self.assertEqual([row["id"] for row in missing], ["design"])

    def test_design_with_valid_kb_reference_passes(self):
        exp_service, req, run_id = self._bind_run()
        design = self.exp_helpers.design(req)
        design["research_frame"]["literature_basis"] = (
            f"设计参照知识库条目 {self.kb_entry_id} 的先兆关系表述。"
        )
        result = exp_service.validate_and_store_design(
            run_id, self.exp_helpers.response(req), design
        )
        self.assertEqual(result["status"], "design_validated")
        self.assertEqual(result["warnings"]["kb_grounding_missing"], [])

    def test_design_with_knowledge_gap_declaration_passes(self):
        exp_service, req, run_id = self._bind_run()
        design = self.exp_helpers.design(req)
        design["research_frame"]["literature_basis"] = (
            "该固定装置无需外部文献；知识库无相关条目，显式声明 knowledge_gap。"
        )
        result = exp_service.validate_and_store_design(
            run_id, self.exp_helpers.response(req), design
        )
        self.assertEqual(result["status"], "design_validated")
        self.assertEqual(result["warnings"]["kb_grounding_missing"], [])


# ----------------------------------------------------------------------
# §5.4 #4：finalize 自动写回
# ----------------------------------------------------------------------
class TestFinalizeWriteback(WiringTestCase):
    def test_finalize_writes_finding_candidate_back(self):
        from automatic_experiment import service as exp_service
        from experiment.tests import helpers as exp_helpers

        req = exp_helpers.request(task_name="unit_kb_writeback")
        run_id, attempt_id = exp_helpers.create_ready_run(req)
        self.addCleanup(lambda: TestExperimentGroundingGate._cleanup_run(run_id))
        exp_service.execute(run_id, attempt_id)
        preview = exp_service.verify(run_id, attempt_id, None)
        self.assertEqual(preview["status"], "assessment_required")
        verified = exp_service.verify(run_id, attempt_id, exp_helpers.assessment())
        self.assertEqual(verified["outcome"], "completed_interpretable")

        entry = exp_service.finalize(run_id)
        writeback = entry.get("knowledge_writeback")
        self.assertIsNotNone(writeback)
        self.assertEqual(writeback["status"], "ok")
        self.assertEqual(len(writeback["entry_ids"]), 1)
        stored = self.read_entry(writeback["entry_ids"][0])
        self.assertIsNotNone(stored)
        self.assertEqual(stored["type"], "finding")
        self.assertEqual(stored["status"], "candidate")
        self.assertEqual(stored["confidence"], "low")
        self.assertEqual(stored["source_type"], "historical_run")
        self.assertEqual(stored["source_ref"], run_id)
        self.assertEqual(stored["content"]["run_id"], run_id)

        # finalize 幂等：再次调用返回同一批条目 id，不重复建条
        again = exp_service.finalize(run_id)
        second = again["knowledge_writeback"]
        self.assertEqual(second["status"], "ok")
        self.assertEqual(second["entry_ids"], writeback["entry_ids"])
        self.assertEqual(
            self.read_entry(writeback["entry_ids"][0])["version"], 1
        )

    def test_writeback_maps_null_to_counterexample_and_skips_others(self):
        from automatic_experiment import service as exp_service

        root = Path(self.tmp) / "fake_run"
        root.mkdir(parents=True, exist_ok=True)
        (root / "record.json").write_text(
            '{"task": "检验先兆关系", "run_id": "fake_run_null",'
            ' "outcome": "scientific_null", "outcome_reason": "零结果成立",'
            ' "scientific_assessment": {"rationale": "先兆关系在固定装置上不成立",'
            ' "report_narrative": {"interpretation": "未发现先兆关系"}}}',
            encoding="utf-8",
        )
        state = {"run_id": "fake_run_null", "outcome": "scientific_null"}
        result = exp_service._knowledge_writeback(root, state)
        self.assertEqual(result["status"], "ok")
        stored = self.read_entry(result["entry_ids"][0])
        self.assertEqual(stored["type"], "counterexample")
        self.assertEqual(stored["source_ref"], "fake_run_null")

        skipped = exp_service._knowledge_writeback(
            root, {"run_id": "fake_run_skip", "outcome": "budget_stopped"}
        )
        self.assertEqual(skipped["status"], "skipped")
        self.assertEqual(skipped["entry_ids"], [])


if __name__ == "__main__":
    unittest.main()
