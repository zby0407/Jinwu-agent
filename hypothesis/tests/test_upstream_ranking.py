"""上游交接通道、排序核验与数据覆盖范围门的确定性测试。只用标准库 unittest。"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scientific_hypothesis.contracts import (  # noqa: E402
    ContractError,
    validate_hypothesis_response,
)
from scientific_hypothesis.harness import (  # noqa: E402
    EvidenceRegister,
    build_counterexample_table,
    compile_hypothesis_portfolio,
    freeze_hypothesis_portfolio,
    preflight_hypothesis_ranking,
)
from scientific_hypothesis.ranking import (  # noqa: E402
    RANKING_VERSION,
    RUBRIC_KEYS,
    check_ranking_consistency,
    compute_dimension_scores,
    validate_ranking_request,
)
from scientific_hypothesis.upstream import inspect_experiment_run  # noqa: E402

from tests.test_hypothesis import (  # noqa: E402
    bind_evidence,
    make_candidate,
    make_experiment_material,
    make_measure_candidate,
    make_request,
    make_response,
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_experiment_run(
    root: Path,
    run_id: str,
    *,
    outcome: str = "completed_interpretable",
    status: str = "finalized",
    planning_input: bool = False,
    corrupt_record: bool = False,
    corrupt_report: bool = False,
    omit_audit: bool = False,
) -> Path:
    """在 root/run_id 下构造一个最小但契约完整的自动实验产出。"""

    run_dir = root / run_id
    run_dir.mkdir(parents=True)

    inputs = []
    if planning_input:
        inputs.append(
            {
                "id": "input_01",
                "source_path": "inputs/demo/research_plan_feedback.md",
                "status": "snapshotted",
                "files": [
                    {
                        "path": "input_01/research_plan_feedback.md",
                        "profile": None,
                        "sha256": "a" * 64,
                        "size_bytes": 100,
                    }
                ],
            }
        )
    record = {
        "schema_version": "automatic-experiment-record-v1",
        "run_id": run_id,
        "outcome": outcome,
        "outcome_reason": "同口径复算显示第24周极小期比第25周长约8个月。",
        "verified_at": "2026-07-20T12:45:17Z",
        "input_snapshot": {
            "schema_version": "automatic-experiment-input-snapshot-v1",
            "inputs": inputs,
        },
        "scientific_assessment": {
            "report_narrative": {
                "claim_boundary": "仅适用于同一数据产品同一平滑口径下的第24与第25活动周。",
                "data_scope": "1996-01至2020-12月平均黑子数。",
                "evidence_strength": "确定性复算，未估计抽样不确定性。",
            }
        },
        "public_artifacts": [
            {
                "path": "public/summary.json",
                "kind": "json",
                "sha256": "b" * 64,
                "size_bytes": 50,
                "description": "核验后的数值摘要",
            }
        ],
    }
    if corrupt_record:
        # 先按未篡改内容声明哈希，再写入被改写的 record，模拟事后改动。
        entry_record_bytes = json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8")
        record = dict(record)
        record["outcome_reason"] = "被事后改写的结论"
        record_bytes = json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8")
    else:
        entry_record_bytes = record_bytes = json.dumps(
            record, ensure_ascii=False, indent=2
        ).encode("utf-8")

    report_text = "# 复算报告\n\n第24周极小期比第25周长约8个月。\n"
    report_bytes = report_text.encode("utf-8")
    entry_report_bytes = report_bytes
    if corrupt_report:
        report_bytes = (report_text + "（事后追加的句子）\n").encode("utf-8")

    entry = {
        "schema_version": "automatic-experiment-entry-result-v1",
        "status": status,
        "run_id": run_id,
        "outcome": outcome,
        "record_path": "record.json",
        "record_sha256": _sha256_bytes(entry_record_bytes),
        "report_path": "report.md",
        "report_sha256": _sha256_bytes(entry_report_bytes),
        "user_display_markdown": "# 摘要",
        "safe_next_action": "/automatic-experiment 重放 " + run_id,
        "created_at": "2026-07-20T12:45:17Z",
        "entry_sha256": "c" * 64,
    }
    if not omit_audit:
        audit_bytes = "# 审计\n".encode("utf-8")
        entry["audit_path"] = "audit.md"
        entry["audit_sha256"] = _sha256_bytes(audit_bytes)
        (run_dir / "audit.md").write_bytes(audit_bytes)

    (run_dir / "record.json").write_bytes(record_bytes)
    (run_dir / "report.md").write_bytes(report_bytes)
    (run_dir / "entry_result.json").write_text(
        json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return run_dir


class UpstreamInspectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="hypothesis_upstream_"))

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def inspect(self, run_dir: Path):
        return inspect_experiment_run({"run_path": str(run_dir)}, ROOT)

    def test_verified_run_returns_summary(self):
        run_dir = write_experiment_run(self.tmp, "run_ok")
        result = self.inspect(run_dir)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["run_id"], "run_ok")
        self.assertTrue(result["integrity"]["record_sha256_match"])
        self.assertTrue(result["integrity"]["report_sha256_match"])
        self.assertTrue(result["integrity"]["outcome_eligible"])
        self.assertIn("8个月", result["evidence_summary"]["outcome_reason"])
        self.assertEqual(len(result["public_artifacts"]), 1)

    def test_planning_feedback_registered(self):
        run_dir = write_experiment_run(self.tmp, "run_plan", planning_input=True)
        result = self.inspect(run_dir)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(len(result["planning_sources"]), 1)
        source = result["planning_sources"][0]
        self.assertIn("research_plan_feedback", source["path"])
        self.assertIn("研究规划", source["note"])

    def test_hash_mismatch_record_blocked(self):
        run_dir = write_experiment_run(self.tmp, "run_bad_record", corrupt_record=True)
        result = self.inspect(run_dir)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["blocker_code"], "hash_mismatch")

    def test_hash_mismatch_report_blocked(self):
        run_dir = write_experiment_run(self.tmp, "run_bad_report", corrupt_report=True)
        result = self.inspect(run_dir)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["blocker_code"], "hash_mismatch")

    def test_non_completed_outcome_blocked(self):
        for outcome in ("technical_failure", "high_uncertainty", "partial_result", "scientific_null"):
            run_dir = write_experiment_run(self.tmp, f"run_{outcome}", outcome=outcome)
            result = self.inspect(run_dir)
            self.assertEqual(result["status"], "blocked", outcome)
            self.assertEqual(result["blocker_code"], "outcome_not_eligible", outcome)

    def test_not_finalized_blocked(self):
        run_dir = write_experiment_run(self.tmp, "run_draft", status="draft")
        result = self.inspect(run_dir)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["blocker_code"], "not_finalized")

    def test_missing_entry_blocked(self):
        run_dir = self.tmp / "run_empty"
        run_dir.mkdir()
        (run_dir / "record.json").write_text("{}", encoding="utf-8")
        result = self.inspect(run_dir)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["blocker_code"], "missing_entry_result")

    def test_missing_run_blocked(self):
        result = inspect_experiment_run({"run_path": str(self.tmp / "nope")}, ROOT)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["blocker_code"], "run_not_found")

    def test_run_id_mismatch_blocked(self):
        run_dir = write_experiment_run(self.tmp, "run_real")
        entry = json.loads((run_dir / "entry_result.json").read_text(encoding="utf-8"))
        entry["run_id"] = "run_other"
        (run_dir / "entry_result.json").write_text(
            json.dumps(entry, ensure_ascii=False), encoding="utf-8"
        )
        result = self.inspect(run_dir)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["blocker_code"], "run_id_mismatch")


def make_ranking(candidate_ids=("cand_measure", "cand_dynamo"), **overrides):
    rubric = [{"key": key, "label": key} for key in RUBRIC_KEYS]
    grades = {key: "moderate" for key in RUBRIC_KEYS}
    ranked = []
    for index, cid in enumerate(candidate_ids, start=1):
        ranked.append(
            {
                "candidate_id": cid,
                "rank": index,
                "rationale": f"{cid} 与已核验证据的一致性更好",
                "key_evidence_ids": ["ev_exp1"],
                "dimension_grades": dict(grades),
                "weakest_dimensions": ["uncertainty"],
                "confidence_note": "排序为初审，供证据审查复审",
            }
        )
    request = {
        "schema_version": RANKING_VERSION,
        "rubric": rubric,
        "weights": {key: 1 for key in RUBRIC_KEYS},
        "ranked": ranked,
        "pairwise_judgments": [],
    }
    request.update(overrides)
    return request


def ready_context():
    """构造绑定好证据、候选引用该证据的请求/响应/登记簿。"""

    request = make_request(upstream_materials=[make_experiment_material()])
    register = EvidenceRegister()
    bind_evidence(register)
    dynamo = make_candidate()
    dynamo["supporting_evidence"] = [
        {"evidence_id": "ev_exp1", "relation_note": "复算确认第24周极小期更长"}
    ]
    measure = make_measure_candidate()
    measure["supporting_evidence"] = [
        {"evidence_id": "ev_exp1", "relation_note": "同口径后差异缩小"}
    ]
    response = make_response(request, candidates=[dynamo, measure])
    validated_response = validate_hypothesis_response(response, request, register)
    return request, validated_response, register


class RankingContractTests(unittest.TestCase):
    def test_valid_ranking_accepted(self):
        request, response, register = ready_context()
        ranking = validate_ranking_request(
            make_ranking(), response["candidates"], register
        )
        self.assertEqual(ranking["schema_version"], RANKING_VERSION)
        self.assertEqual(len(ranking["ranked"]), 2)

    def test_rationale_required(self):
        request, response, register = ready_context()
        payload = make_ranking()
        payload["ranked"][0]["rationale"] = ""
        with self.assertRaises(ContractError):
            validate_ranking_request(payload, response["candidates"], register)

    def test_unbound_anchor_rejected(self):
        request, response, register = ready_context()
        payload = make_ranking()
        payload["ranked"][0]["key_evidence_ids"] = ["ev_ghost"]
        with self.assertRaises(ContractError):
            validate_ranking_request(payload, response["candidates"], register)

    def test_unverified_anchor_rejected(self):
        request, response, register = ready_context()
        register.bind(
            {
                "evidence_id": "ev_gap",
                "evidence_kind": "literature",
                "material_id": "mat_exp1",
                "excerpt": "未核对的笔记",
                "verified_support": False,
                "role": "gap",
            }
        )
        payload = make_ranking()
        payload["ranked"][0]["key_evidence_ids"] = ["ev_gap"]
        with self.assertRaises(ContractError):
            validate_ranking_request(payload, response["candidates"], register)

    def test_ranks_must_be_consecutive(self):
        request, response, register = ready_context()
        payload = make_ranking()
        payload["ranked"][1]["rank"] = 3
        with self.assertRaises(ContractError):
            validate_ranking_request(payload, response["candidates"], register)

    def test_must_cover_all_candidates(self):
        request, response, register = ready_context()
        payload = make_ranking()
        payload["ranked"] = payload["ranked"][:1]
        with self.assertRaises(ContractError):
            validate_ranking_request(payload, response["candidates"], register)

    def test_rubric_must_be_seven_dimensions(self):
        request, response, register = ready_context()
        payload = make_ranking()
        payload["rubric"] = payload["rubric"][:6]
        with self.assertRaises(ContractError):
            validate_ranking_request(payload, response["candidates"], register)

    def test_pairwise_rank_contradiction_flagged(self):
        request, response, register = ready_context()
        payload = make_ranking(
            pairwise_judgments=[
                {
                    "left_id": "cand_dynamo",
                    "right_id": "cand_measure",
                    "preferred_id": "cand_dynamo",
                    "basis": "发电机解释覆盖更多观测",
                }
            ]
        )
        ranking = validate_ranking_request(payload, response["candidates"], register)
        errors = check_ranking_consistency(ranking, response["candidates"])
        self.assertTrue(any("成对判断" in error for error in errors))

    def test_pairwise_consistent_accepted(self):
        request, response, register = ready_context()
        payload = make_ranking(
            pairwise_judgments=[
                {
                    "left_id": "cand_dynamo",
                    "right_id": "cand_measure",
                    "preferred_id": "cand_measure",
                    "basis": "测量解释与同口径复算结果一致",
                }
            ]
        )
        ranking = validate_ranking_request(payload, response["candidates"], register)
        self.assertEqual(check_ranking_consistency(ranking, response["candidates"]), [])

    def test_anchor_outside_supporting_evidence_flagged(self):
        request, response, register = ready_context()
        bind_evidence(register, evidence_id="ev_other")
        payload = make_ranking()
        payload["ranked"][0]["key_evidence_ids"] = ["ev_exp1", "ev_other"]
        ranking = validate_ranking_request(payload, response["candidates"], register)
        errors = check_ranking_consistency(ranking, response["candidates"])
        self.assertTrue(any("关键证据锚点" in error for error in errors))

    def test_dimension_scores_deterministic(self):
        request, response, register = ready_context()
        ranking = validate_ranking_request(
            make_ranking(), response["candidates"], register
        )
        scores = compute_dimension_scores(ranking)
        self.assertEqual(scores["cand_measure"]["weighted_total"], 14.0)

    def test_preflight_ranking_roundtrip(self):
        request, response, register = ready_context()
        result = preflight_hypothesis_ranking(
            request, response, make_ranking(), register, include_validated_ranking=True
        )
        self.assertEqual(result["status"], "ranking_ready")
        self.assertIn("_validated_ranking", result)


class DataCoverageGateTests(unittest.TestCase):
    def make_jwfd_material(self):
        return {
            "id": "mat_jwfd",
            "material_kind": "data_feature",
            "title": "JW-FD 活动区磁图特征（2011年前后个别活动区）",
            "locator": "runs/features/jwfd.csv",
            "content_notes": "JW-FD 磁图特征：中性线长度、磁通不平衡，覆盖2011年前后个别活动区。",
            "experiment_summary": None,
        }

    def test_overgeneralized_high_confidence_rejected(self):
        request = make_request(upstream_materials=[self.make_jwfd_material()])
        register = EvidenceRegister()
        bind_evidence(
            register,
            evidence_id="ev_jwfd",
            evidence_kind="upstream",
            material_id="mat_jwfd",
            excerpt="中性线弯曲能与耀斑活动相关",
        )
        candidate = make_candidate()
        candidate["statement"] = "中性线弯曲能升高导致耀斑增多，跨周期普遍成立"
        candidate["supporting_evidence"] = [
            {"evidence_id": "ev_jwfd", "relation_note": "JW-FD 特征显示相关性"}
        ]
        candidate["confidence"] = {"level": "high", "basis": "有特征数据支持"}
        response = make_response(request, candidates=[candidate, make_measure_candidate()])
        with self.assertRaises(ContractError) as ctx:
            validate_hypothesis_response(response, request, register)
        self.assertIn("覆盖范围", str(ctx.exception))

    def test_scoped_statement_allowed(self):
        request = make_request(upstream_materials=[self.make_jwfd_material()])
        register = EvidenceRegister()
        bind_evidence(
            register,
            evidence_id="ev_jwfd",
            evidence_kind="upstream",
            material_id="mat_jwfd",
            excerpt="中性线弯曲能与耀斑活动相关",
        )
        candidate = make_candidate()
        candidate["statement"] = "在 JW-FD 覆盖的活动区样本内，中性线弯曲能与耀斑活动相关"
        candidate["supporting_evidence"] = [
            {"evidence_id": "ev_jwfd", "relation_note": "JW-FD 特征显示相关性"}
        ]
        candidate["confidence"] = {"level": "high", "basis": "样本内证据一致"}
        response = make_response(request, candidates=[candidate, make_measure_candidate()])
        validated = validate_hypothesis_response(response, request, register)
        self.assertEqual(validated["candidates"][0]["confidence"]["level"], "high")


class CounterexampleTableTests(unittest.TestCase):
    def test_table_collects_opposing_and_gaps(self):
        request = make_request(upstream_materials=[make_experiment_material()])
        register = EvidenceRegister()
        bind_evidence(register, role="opposes")
        dynamo = make_candidate()
        dynamo["opposing_evidence"] = [
            {"evidence_id": "ev_exp1", "relation_note": "复算差异方向与本候选预期相反"}
        ]
        measure = make_measure_candidate()
        response = make_response(request, candidates=[dynamo, measure])
        validated = validate_hypothesis_response(response, request, register)
        table = build_counterexample_table(validated, register)
        kinds = {row["kind"] for row in table["rows"]}
        self.assertIn("counterexample", kinds)
        self.assertIn("conflict", kinds)
        linked = [row for row in table["rows"] if row["kind"] == "counterexample"]
        self.assertEqual(linked[0]["candidate_id"], "cand_dynamo")
        self.assertEqual(linked[0]["evidence_id"], "ev_exp1")

    def test_portfolio_contains_ranking_and_table(self):
        request, response, register = ready_context()
        portfolio = compile_hypothesis_portfolio(
            request, response, register, make_ranking()
        )
        self.assertIsNotNone(portfolio["ranking"])
        self.assertIn("counterexample_table", portfolio)
        self.assertEqual(
            len(portfolio["counterexample_table"]["rows"]),
            sum(
                len(c["opposing_evidence"]) + len(c["evidence_gaps"])
                for c in portfolio["candidates"]
            ),
        )

    def test_freeze_with_ranking_writes_three_files(self):
        request, response, register = ready_context()
        with tempfile.TemporaryDirectory(prefix="hypothesis_freeze_") as tmp:
            outcome = freeze_hypothesis_portfolio(
                request, response, register, runs_root=Path(tmp), ranking_payload=make_ranking()
            )
            self.assertEqual(outcome["status"], "frozen_and_valid")
            self.assertTrue(outcome["ranked"])
            run_dir = Path(tmp) / outcome["run_id"]
            portfolio = json.loads(
                (run_dir / "hypothesis_portfolio.json").read_text(encoding="utf-8")
            )
            self.assertIsNotNone(portfolio["ranking"])
            markdown = (run_dir / "hypotheses.md").read_text(encoding="utf-8")
            self.assertIn("初步排序", markdown)
            self.assertIn("反例与冲突点", markdown)

    def test_freeze_without_ranking_still_valid(self):
        request, response, register = ready_context()
        with tempfile.TemporaryDirectory(prefix="hypothesis_freeze_") as tmp:
            outcome = freeze_hypothesis_portfolio(request, response, register, runs_root=Path(tmp))
            self.assertEqual(outcome["status"], "frozen_and_valid")
            self.assertFalse(outcome["ranked"])


if __name__ == "__main__":
    unittest.main()
