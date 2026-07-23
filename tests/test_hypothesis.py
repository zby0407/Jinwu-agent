"""科学假设 Agent 1.0 的确定性测试。只用标准库 unittest。"""

from __future__ import annotations

import json
import shutil
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
    REQUEST_VERSION,
    RESPONSE_VERSION,
    validate_hypothesis_request,
    validate_hypothesis_response,
)
from scientific_hypothesis.harness import (  # noqa: E402
    EvidenceRegister,
    build_hypothesis_brief,
    build_natural_hypothesis_request,
    collect_hypothesis_semantic_errors,
    freeze_hypothesis_portfolio,
    preflight_hypothesis_response,
    render_nonportfolio_response_markdown,
)


def make_request(**overrides):
    request = {
        "schema_version": REQUEST_VERSION,
        "task_name": "test_task",
        "research_question": "太阳活动周24与25极小期长度差异可能由哪些机制解释？",
        "upstream_materials": [],
        "prior_hypotheses": [],
        "max_candidates": 4,
    }
    request.update(overrides)
    return validate_hypothesis_request(request)


def make_experiment_material(**summary_overrides):
    summary = {
        "execution_completed": True,
        "outcome": "completed",
        "metrics": [
            {
                "name": "极小期长度差",
                "value_text": "第24周极小期比第25周长约8个月",
                "definition": "按同一平滑口径计算的极小期间隔差",
            }
        ],
        "uncertainty_notes": "未估计抽样不确定性",
        "record_sha256": None,
    }
    summary.update(summary_overrides)
    return {
        "id": "mat_exp1",
        "material_kind": "experiment_result",
        "title": "极小期长度核对实验",
        "locator": "runs/demo/report.md",
        "content_notes": "该实验按统一口径复算了第24与第25活动周极小期长度。",
        "experiment_summary": summary,
    }


def make_candidate(cid="cand_dynamo", statement="第24周极小期延长源于发电机效率下降"):
    return {
        "id": cid,
        "statement": statement,
        "applicability": "仅适用于按同一口径评估的第24与第25活动周极小期",
        "mechanism": {
            "summary": "发电机过程效率下降延缓了极区磁场反转",
            "physical_basis": "依据尚不充分，属于待检验机制设想",
            "required_premises": ["极区磁场演化能代表全球发电机状态"],
        },
        "assumptions": ["两周的观测口径一致"],
        "predictions": [
            {
                "id": "pred_flux",
                "statement": "第24周极小期前极区磁通衰减速率低于第25周",
                "observable": "极区磁通时间序列",
                "distinguishes_from": ["cand_measure"],
                "would_weaken_if": "两周极区磁通衰减速率无差异",
            }
        ],
        "supporting_evidence": [],
        "opposing_evidence": [],
        "evidence_gaps": ["缺少极区磁通的同口径长时间序列"],
        "alternative_explanations": ["观测口径变化造成的表现差异"],
        "confounders": ["观测仪器更换", "数据平滑窗口不同"],
        "falsification_conditions": ["同口径复算后两周极小期长度无差异"],
        "next_test": {
            "objective": "核对两周极区磁通衰减速率是否不同",
            "discriminating_power": "发电机解释预测速率不同，测量解释预测速率一致",
            "expected_signals": ["第24周速率更低", "第25周速率正常"],
            "candidate_ids_distinguished": ["cand_dynamo", "cand_measure"],
        },
        "confidence": {"level": "low", "basis": "目前没有直接证据，只是机制设想"},
        "evidence_update": None,
        "prior_version_id": None,
    }


def make_measure_candidate():
    candidate = make_candidate(
        cid="cand_measure",
        statement="第24周极小期延长是观测口径变化造成的表现差异",
    )
    candidate["mechanism"] = {
        "summary": "极小期判定口径在两周之间发生变化",
        "physical_basis": "不同数据产品的极小期定义存在版本差异",
        "required_premises": ["两周使用了不同的数据产品或定义"],
    }
    candidate["predictions"] = [
        {
            "id": "pred_uniform",
            "statement": "同一数据产品同口径复算后两周极小期长度差异消失",
            "observable": "同口径复算的极小期长度",
            "distinguishes_from": ["cand_dynamo"],
            "would_weaken_if": "同口径复算后差异仍然存在",
        }
    ]
    candidate["next_test"] = {
        "objective": "用同一数据产品复算两周极小期长度",
        "discriminating_power": "测量解释预测差异消失，发电机解释预测差异保留",
        "expected_signals": ["同口径后差异消失", "同口径后差异保留"],
        "candidate_ids_distinguished": ["cand_measure", "cand_dynamo"],
    }
    return candidate


def make_response(request, candidates=None, distinctions=None, kind="hypotheses_ready"):
    candidates = candidates if candidates is not None else [make_candidate(), make_measure_candidate()]
    if distinctions is None:
        distinctions = [
            {
                "left_id": "cand_dynamo",
                "right_id": "cand_measure",
                "distinction": "前者是物理机制解释，后者是测量口径解释",
            }
        ]
    if kind == "hypotheses_ready":
        return {
            "schema_version": RESPONSE_VERSION,
            "task_name": request["task_name"],
            "research_question": request["research_question"],
            "response_kind": "hypotheses_ready",
            "candidates": candidates,
            "pairwise_distinctions": distinctions,
            "portfolio_notes": None,
        }
    return {
        "schema_version": RESPONSE_VERSION,
        "task_name": request["task_name"],
        "research_question": request["research_question"],
        "response_kind": kind,
    }


def bind_evidence(register, **overrides):
    bind = {
        "evidence_id": "ev_exp1",
        "evidence_kind": "experiment",
        "material_id": "mat_exp1",
        "excerpt": "第24周极小期比第25周长约8个月",
        "verified_support": True,
        "role": "supports",
    }
    bind.update(overrides)
    return register.bind(bind)


class RequestContractTests(unittest.TestCase):
    def test_natural_language_request(self):
        request = build_natural_hypothesis_request("第24与25活动周极小期长度差异由什么机制解释？")
        self.assertEqual(request["schema_version"], REQUEST_VERSION)
        self.assertEqual(request["upstream_materials"], [])
        self.assertTrue(request["task_name"].startswith("hypothesis_"))

    def test_rejects_short_question(self):
        with self.assertRaises(ContractError):
            build_natural_hypothesis_request("为什么")

    def test_rejects_unknown_field(self):
        with self.assertRaises(ContractError):
            make_request(unknown_field=True)

    def test_experiment_material_requires_summary(self):
        material = make_experiment_material()
        material["experiment_summary"] = None
        with self.assertRaises(ContractError):
            make_request(upstream_materials=[material])

    def test_summary_only_allowed_on_experiment_material(self):
        material = make_experiment_material()
        material["material_kind"] = "research_plan"
        with self.assertRaises(ContractError):
            make_request(upstream_materials=[material])

    def test_data_feature_material_accepted_without_summary(self):
        material = {
            "id": "mat_feat1",
            "material_kind": "data_feature",
            "title": "活动周极小期特征表（数据与特征 Agent）",
            "locator": "runs/features/cycle_minima.parquet",
            "content_notes": "清洗后特征：第24周极小期长度偏长，跨周口径已统一；含数据质量说明。",
            "experiment_summary": None,
        }
        request = make_request(upstream_materials=[material])
        self.assertEqual(request["upstream_materials"][0]["material_kind"], "data_feature")
        self.assertIsNone(request["upstream_materials"][0]["experiment_summary"])

    def test_data_feature_material_rejects_summary(self):
        material = make_experiment_material()
        material["id"] = "mat_feat1"
        material["material_kind"] = "data_feature"
        with self.assertRaises(ContractError):
            make_request(upstream_materials=[material])

    def test_data_feature_not_experiment_evidence(self):
        # 数据与特征材料不是已执行实验；绑定时若声称 evidence_kind=experiment 须在语义层被拒。
        material = {
            "id": "mat_feat1",
            "material_kind": "data_feature",
            "title": "活动周极小期特征表（数据与特征 Agent）",
            "locator": "runs/features/cycle_minima.parquet",
            "content_notes": "清洗后特征：第24周极小期长度偏长。",
            "experiment_summary": None,
        }
        request = make_request(upstream_materials=[material])
        candidate = make_candidate()
        candidate["supporting_evidence"] = [{"evidence_id": "ev_feat", "note": "特征表显示第24周极小期偏长"}]
        response = make_response(request, candidates=[candidate])
        register = EvidenceRegister()
        register.bind(
            {
                "evidence_id": "ev_feat",
                "evidence_kind": "experiment",
                "material_id": "mat_feat1",
                "excerpt": "第24周极小期长度偏长",
                "verified_support": True,
                "role": "supports",
            }
        )
        errors = collect_hypothesis_semantic_errors(request, response, register)
        self.assertTrue(
            any("不是实验执行记录" in e for e in errors),
            f"应拒绝把数据与特征材料当作实验证据，实际错误: {errors}",
        )

    def test_technical_failure_cannot_carry_metrics(self):
        with self.assertRaises(ContractError):
            make_request(
                upstream_materials=[
                    make_experiment_material(
                        execution_completed=False, outcome="technical_failure"
                    )
                ]
            )

    def test_completed_execution_requires_metrics(self):
        with self.assertRaises(ContractError):
            make_request(upstream_materials=[make_experiment_material(metrics=[])])

    def test_uncompleted_execution_must_be_technical_failure(self):
        with self.assertRaises(ContractError):
            make_request(
                upstream_materials=[
                    make_experiment_material(execution_completed=False, outcome="completed")
                ]
            )

    def test_max_candidates_bounds(self):
        with self.assertRaises(ContractError):
            make_request(max_candidates=0)
        with self.assertRaises(ContractError):
            make_request(max_candidates=9)


class EvidenceBindTests(unittest.TestCase):
    def test_unverified_support_rejected_for_support_role(self):
        register = EvidenceRegister()
        with self.assertRaises(ContractError):
            bind_evidence(register, verified_support=False, role="supports")

    def test_gap_role_allowed_unverified(self):
        register = EvidenceRegister()
        result = bind_evidence(register, verified_support=False, role="gap")
        self.assertEqual(result["status"], "bound")

    def test_conflicting_id_rejected(self):
        register = EvidenceRegister()
        bind_evidence(register)
        with self.assertRaises(ContractError):
            bind_evidence(register, excerpt="完全不同的内容")


class ResponseContractTests(unittest.TestCase):
    def setUp(self):
        self.request = make_request()

    def test_valid_response_passes(self):
        response = make_response(self.request)
        validated = validate_hypothesis_response(response, self.request)
        self.assertEqual(validated["response_kind"], "hypotheses_ready")

    def test_task_name_must_match_request(self):
        response = make_response(self.request)
        response["task_name"] = "other"
        with self.assertRaises(ContractError):
            validate_hypothesis_response(response, self.request)

    def test_prediction_reference_must_exist(self):
        candidate = make_candidate()
        candidate["predictions"][0]["distinguishes_from"] = ["ghost"]
        with self.assertRaises(ContractError):
            validate_hypothesis_response(
                make_response(
                    self.request, candidates=[candidate, make_measure_candidate()]
                ),
                self.request,
            )

    def test_candidate_without_evidence_must_declare_gaps(self):
        candidate = make_candidate()
        candidate["evidence_gaps"] = []
        with self.assertRaises(ContractError):
            validate_hypothesis_response(
                make_response(self.request, candidates=[candidate, make_measure_candidate()]),
                self.request,
            )

    def test_single_candidate_allowed(self):
        candidate = make_candidate()
        candidate["predictions"][0]["distinguishes_from"] = ["cand_dynamo"]
        candidate["next_test"]["candidate_ids_distinguished"] = ["cand_dynamo"]
        validated = validate_hypothesis_response(
            make_response(self.request, candidates=[candidate], distinctions=[]),
            self.request,
        )
        self.assertEqual(len(validated["candidates"]), 1)

    def test_update_requires_evidence_update_note(self):
        request = make_request(
            prior_hypotheses=[
                {
                    "id": "old_h1",
                    "statement": "旧假设",
                    "version": 1,
                    "notes": "之前形成的假设",
                }
            ]
        )
        candidate = make_candidate()
        candidate["prior_version_id"] = "old_h1"
        candidate["predictions"][0]["distinguishes_from"] = ["old_h1"]
        candidate["next_test"]["candidate_ids_distinguished"] = ["cand_dynamo"]
        with self.assertRaises(ContractError):
            validate_hypothesis_response(
                make_response(request, candidates=[candidate], distinctions=[]), request
            )
        candidate["evidence_update"] = {
            "summary": "根据新实验结果下调置信度",
            "reason": "新空结果削弱了原机制预期",
        }
        validated = validate_hypothesis_response(
            make_response(request, candidates=[candidate], distinctions=[]), request
        )
        self.assertEqual(validated["candidates"][0]["prior_version_id"], "old_h1")

    def test_next_test_multi_candidate_must_include_self(self):
        candidate = make_candidate()
        candidate["next_test"]["candidate_ids_distinguished"] = ["cand_measure", "ghost2"]
        with self.assertRaises(ContractError):
            validate_hypothesis_response(
                make_response(
                    self.request, candidates=[candidate, make_measure_candidate()]
                ),
                self.request,
            )

    def test_multiple_candidates_need_distinction_coverage(self):
        with self.assertRaises(ContractError):
            validate_hypothesis_response(
                make_response(self.request, distinctions=[]), self.request
            )

    def test_confidence_percentage_rejected(self):
        candidate = make_candidate()
        candidate["confidence"] = {"level": "medium", "basis": "置信度约73%"}
        with self.assertRaises(ContractError):
            validate_hypothesis_response(
                make_response(
                    self.request, candidates=[candidate, make_measure_candidate()]
                ),
                self.request,
            )

    def test_confidence_power_percentage_range_rejected(self):
        candidate = make_candidate()
        candidate["confidence"] = {
            "level": "low",
            "basis": "无实际实验数据；理论功效约 75–80%，非检出概率约 20–25%",
        }
        with self.assertRaises(ContractError):
            validate_hypothesis_response(
                make_response(
                    self.request, candidates=[candidate, make_measure_candidate()]
                ),
                self.request,
            )

    def test_clarification_and_blocked_shapes(self):
        for kind, field, rows in (
            (
                "clarification_needed",
                "questions",
                [
                    {
                        "id": "q1",
                        "question": "比较基准是哪个数据产品？",
                        "why_it_matters": "不同产品会改变候选方向",
                        "expected_answer": "指定一个数据产品",
                    }
                ],
            ),
            (
                "hypothesis_blocked",
                "blockers",
                [
                    {
                        "id": "b1",
                        "code": "unsupported_scope",
                        "reason": "问题超出本 Agent 边界",
                        "recoverable": False,
                        "resolution": "改用适合的工具",
                    }
                ],
            ),
        ):
            response = make_response(self.request, kind=kind)
            response[field] = rows
            validated = validate_hypothesis_response(response, self.request)
            self.assertEqual(validated["response_kind"], kind)

    def test_blocker_code_closed(self):
        response = make_response(self.request, kind="hypothesis_blocked")
        response["blockers"] = [
            {
                "id": "b1",
                "code": "not_a_code",
                "reason": "原因",
                "recoverable": True,
                "resolution": "办法",
            }
        ]
        with self.assertRaises(ContractError):
            validate_hypothesis_response(response, self.request)


class SemanticCheckTests(unittest.TestCase):
    def setUp(self):
        self.request = make_request(upstream_materials=[make_experiment_material()])
        self.register = EvidenceRegister()

    def test_unbound_evidence_reference_rejected(self):
        request = make_request()
        candidate = make_candidate()
        candidate["supporting_evidence"] = [
            {"evidence_id": "ev_none", "relation_note": "支持"}
        ]
        errors = collect_hypothesis_semantic_errors(
            request,
            validate_hypothesis_response(
                make_response(request, candidates=[candidate, make_measure_candidate()]),
                request,
            ),
            self.register,
        )
        self.assertTrue(any("未绑定的证据" in error for error in errors))

    def test_verified_experiment_evidence_accepted(self):
        bind_evidence(self.register)
        candidate = make_candidate()
        candidate["supporting_evidence"] = [
            {"evidence_id": "ev_exp1", "relation_note": "复算确认差异存在"}
        ]
        candidate["confidence"] = {"level": "medium", "basis": "有一项同口径复算证据"}
        candidate["evidence_gaps"] = []
        response = validate_hypothesis_response(
            make_response(self.request, candidates=[candidate, make_measure_candidate()]),
            self.request,
        )
        errors = collect_hypothesis_semantic_errors(self.request, response, self.register)
        self.assertEqual(errors, [])

    def test_opposing_role_mismatch_flagged(self):
        bind_evidence(self.register, role="supports")
        candidate = make_candidate()
        candidate["opposing_evidence"] = [
            {"evidence_id": "ev_exp1", "relation_note": "反对"}
        ]
        errors = collect_hypothesis_semantic_errors(
            self.request,
            validate_hypothesis_response(
                make_response(self.request, candidates=[candidate, make_measure_candidate()]),
                self.request,
            ),
            self.register,
        )
        self.assertTrue(any("反对关系不一致" in error for error in errors))

    def test_duplicate_statements_flagged(self):
        twin = make_measure_candidate()
        twin["statement"] = make_candidate()["statement"]
        errors = collect_hypothesis_semantic_errors(
            self.request,
            validate_hypothesis_response(
                make_response(self.request, candidates=[make_candidate(), twin]),
                self.request,
            ),
            self.register,
        )
        self.assertTrue(any("逐字重复" in error for error in errors))

    def test_identical_mechanism_summaries_flagged(self):
        twin = make_measure_candidate()
        twin["mechanism"] = make_candidate()["mechanism"]
        errors = collect_hypothesis_semantic_errors(
            self.request,
            validate_hypothesis_response(
                make_response(self.request, candidates=[make_candidate(), twin]),
                self.request,
            ),
            self.register,
        )
        self.assertTrue(any("同义改写" in error for error in errors))

    def test_high_confidence_without_evidence_flagged(self):
        candidate = make_candidate()
        candidate["confidence"] = {"level": "high", "basis": "很有把握"}
        errors = collect_hypothesis_semantic_errors(
            self.request,
            validate_hypothesis_response(
                make_response(self.request, candidates=[candidate, make_measure_candidate()]),
                self.request,
            ),
            self.register,
        )
        self.assertTrue(any("high 置信度" in error for error in errors))

    def test_novelty_claim_flagged(self):
        candidate = make_candidate()
        candidate["statement"] = "本文首次提出第24周极小期延长的发电机解释"
        errors = collect_hypothesis_semantic_errors(
            self.request,
            validate_hypothesis_response(
                make_response(self.request, candidates=[candidate, make_measure_candidate()]),
                self.request,
            ),
            self.register,
        )
        self.assertTrue(any("首次提出" in error for error in errors))

    def test_unsourced_numeric_threshold_flagged(self):
        candidate = make_candidate()
        candidate["falsification_conditions"] = ["同口径复算后差异仍超过6个月"]
        errors = collect_hypothesis_semantic_errors(
            self.request,
            validate_hypothesis_response(
                make_response(self.request, candidates=[candidate, make_measure_candidate()]),
                self.request,
            ),
            self.register,
        )
        self.assertTrue(any("数值门槛" in error for error in errors))

    def test_sourced_numeric_threshold_accepted(self):
        bind_evidence(self.register)  # 摘录中含“约8个月”之外的数值不在门槛里
        candidate = make_candidate()
        candidate["falsification_conditions"] = ["同口径复算后两周极小期长度无差异"]
        response = validate_hypothesis_response(
            make_response(self.request, candidates=[candidate, make_measure_candidate()]),
            self.request,
        )
        errors = collect_hypothesis_semantic_errors(self.request, response, self.register)
        self.assertEqual(errors, [])

    def test_technical_failure_not_scientific_evidence(self):
        register = EvidenceRegister()
        bind_evidence(
            register,
            evidence_kind="experiment",
            material_id="mat_user1",  # 请求中不是实验执行记录
        )
        candidate = make_candidate()
        candidate["supporting_evidence"] = [
            {"evidence_id": "ev_exp1", "relation_note": "支持"}
        ]
        candidate["confidence"] = {"level": "medium", "basis": "有一项证据"}
        candidate["evidence_gaps"] = []
        errors = collect_hypothesis_semantic_errors(
            self.request,
            validate_hypothesis_response(
                make_response(self.request, candidates=[candidate, make_measure_candidate()]),
                self.request,
            ),
            register,
        )
        self.assertTrue(any("不是实验执行记录" in error for error in errors))

    def test_same_material_bound_with_directional_roles(self):
        # 同一材料的结论削弱一个候选、同时增强另一候选时，
        # 应按方向分别绑定 opposes 与 supports，而不是退化为 gap 规避冲突。
        register = EvidenceRegister()
        bind_evidence(register, evidence_id="ev_pro", role="supports")
        bind_evidence(register, evidence_id="ev_con", role="opposes")
        candidate = make_candidate()
        candidate["supporting_evidence"] = [
            {"evidence_id": "ev_pro", "relation_note": "复算支持本候选"}
        ]
        rival = make_measure_candidate()
        rival["opposing_evidence"] = [
            {"evidence_id": "ev_con", "relation_note": "同一复算削弱对立候选"}
        ]
        errors = collect_hypothesis_semantic_errors(
            self.request,
            validate_hypothesis_response(
                make_response(self.request, candidates=[candidate, rival]),
                self.request,
            ),
            register,
        )
        self.assertEqual(errors, [])

    def test_blocked_response_flows_through_preflight_with_user_markdown(self):
        # 越界请求必须能走合同通道产出正式阻塞交付物，
        # 而不是绕过工具的自由文本拒绝。
        request = make_request()
        blocked = make_response(request, kind="hypothesis_blocked")
        blocked["blockers"] = [
            {
                "id": "b1",
                "code": "unsupported_scope",
                "reason": "请求要求实际下载数据并计算，超出本 Agent 边界",
                "recoverable": True,
                "resolution": "自行下载数据后作为上游材料重新提交",
            }
        ]
        response = validate_hypothesis_response(blocked, request)
        result = preflight_hypothesis_response(request, response, EvidenceRegister())
        self.assertEqual(result["status"], "hypothesis_blocked")
        markdown = result["user_display_markdown"]
        self.assertIn("暂时无法形成科学假设", markdown)
        self.assertNotIn("schema", markdown.lower())
        self.assertNotIn("hypothesis_blocked", markdown)


class PreflightAndFreezeTests(unittest.TestCase):
    def test_preflight_reports_counts(self):
        request = make_request()
        response = validate_hypothesis_response(make_response(request), request)
        result = preflight_hypothesis_response(
            request, response, EvidenceRegister(), include_validated_response=True
        )
        self.assertEqual(result["status"], "hypotheses_ready")
        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(result["files_written"], 0)
        self.assertEqual(result["experiments_executed"], 0)
        self.assertIn("_validated_response", result)

    def test_clarification_renders_user_markdown(self):
        request = make_request()
        response = validate_hypothesis_response(
            {
                "schema_version": RESPONSE_VERSION,
                "task_name": request["task_name"],
                "research_question": request["research_question"],
                "response_kind": "clarification_needed",
                "questions": [
                    {
                        "id": "q1",
                        "question": "比较基准是哪个数据产品？",
                        "why_it_matters": "不同产品会改变候选方向",
                        "expected_answer": "指定一个数据产品",
                    }
                ],
            },
            request,
        )
        result = preflight_hypothesis_response(request, response, EvidenceRegister())
        self.assertEqual(result["status"], "clarification_needed")
        markdown = result["user_display_markdown"]
        self.assertIn("还需要你确认", markdown)
        self.assertNotIn("clarification_needed", markdown)
        self.assertNotIn("schema", markdown.lower())

    def test_blocked_renders_user_markdown(self):
        request = make_request()
        response = {
            "schema_version": RESPONSE_VERSION,
            "task_name": request["task_name"],
            "research_question": request["research_question"],
            "response_kind": "hypothesis_blocked",
            "blockers": [
                {
                    "id": "b1",
                    "code": "unsupported_scope",
                    "reason": "问题超出科学假设边界",
                    "recoverable": False,
                    "resolution": "请改用实验执行工具",
                }
            ],
        }
        markdown = render_nonportfolio_response_markdown(
            validate_hypothesis_response(response, request)
        )
        self.assertIn("暂时无法形成科学假设", markdown)
        self.assertNotIn("hypothesis_blocked", markdown)

    def test_freeze_writes_three_files_and_rolls_back_on_conflict(self):
        request = make_request(upstream_materials=[make_experiment_material()])
        register = EvidenceRegister()
        bind_evidence(register)
        candidate = make_candidate()
        candidate["supporting_evidence"] = [
            {"evidence_id": "ev_exp1", "relation_note": "复算确认差异存在"}
        ]
        candidate["confidence"] = {"level": "medium", "basis": "有一项同口径复算证据"}
        candidate["evidence_gaps"] = []
        response = validate_hypothesis_response(
            make_response(request, candidates=[candidate, make_measure_candidate()]),
            request,
        )
        with tempfile.TemporaryDirectory() as tmp:
            outcome = freeze_hypothesis_portfolio(request, response, register, runs_root=Path(tmp))
            self.assertEqual(outcome["status"], "frozen_and_valid")
            self.assertEqual(outcome["files_written"], 3)
            self.assertEqual(outcome["experiments_executed"], 0)
            run_dir = Path(tmp) / outcome["run_id"]
            self.assertTrue((run_dir / "hypothesis_request.json").exists())
            self.assertTrue((run_dir / "hypothesis_portfolio.json").exists())
            self.assertTrue((run_dir / "hypotheses.md").exists())
            markdown = (run_dir / "hypotheses.md").read_text(encoding="utf-8")
            self.assertIn("# 科学假设组合", markdown)
            self.assertIn("候选 1", markdown)
            self.assertNotIn("schema_version", markdown)
            self.assertNotIn("evidence_id", markdown)
            # 用户展示文本与落盘 Markdown 一致
            self.assertTrue(outcome["user_display_markdown"].startswith(markdown.rstrip()))
            # 再次保存建立新的独立运行目录，不覆盖历史运行
            second = freeze_hypothesis_portfolio(request, response, register, runs_root=Path(tmp))
            self.assertNotEqual(second["run_id"], outcome["run_id"])
            self.assertTrue((Path(tmp) / second["run_id"] / "hypotheses.md").exists())
            self.assertTrue((run_dir / "hypotheses.md").exists())

    def test_portfolio_hashes_stable(self):
        request = make_request()
        response = validate_hypothesis_response(make_response(request), request)
        with tempfile.TemporaryDirectory() as tmp:
            outcome = freeze_hypothesis_portfolio(
                request, response, EvidenceRegister(), runs_root=Path(tmp)
            )
            portfolio = json.loads(
                (Path(tmp) / outcome["run_id"] / "hypothesis_portfolio.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(portfolio["schema_version"], "scientific-hypothesis-portfolio-v1")
            self.assertEqual(len(portfolio["portfolio_sha256"]), 64)
            self.assertEqual(portfolio["status"], "frozen")


class BriefTests(unittest.TestCase):
    def test_brief_contains_contract_and_boundaries(self):
        brief = build_hypothesis_brief(make_request())
        self.assertEqual(brief["schema_version"], "scientific-hypothesis-brief-v1")
        self.assertIn("response_contract", brief)
        self.assertIn("hard_boundaries", brief)
        self.assertIn("model_owned", brief)
        self.assertIn("harness_owned", brief)
        self.assertEqual(len(brief["request_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
