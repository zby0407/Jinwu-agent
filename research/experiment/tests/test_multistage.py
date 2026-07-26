from __future__ import annotations

import copy
import unittest

from automatic_experiment import service
from automatic_experiment.contracts import ContractError, validate_design
from automatic_experiment.policy import CodePolicyError
from automatic_experiment.state import load_state, read_json, runs_root
from tests.helpers import cleanup_run, design, request, response

STAGE_ONE_CODE = '''import json

def run_experiment(context):
    total = 10.0
    path = context["output_dir"] / "intermediate.json"
    path.write_text(json.dumps({"stage_total": total, "input_count": 4}), encoding="utf-8")
    return {
        "schema_version": "automatic-experiment-worker-result-v1",
        "execution_completed": True,
        "measurements": [{"name": "stage_total", "value": total, "unit": "dimensionless", "role": "primary", "source_artifact": "intermediate.json"}, {"name": "input_count", "value": 4, "unit": "records", "role": "secondary", "source_artifact": "intermediate.json"}],
        "result_items": [],
        "artifacts": [{"path": "intermediate.json", "kind": "json", "description": "第一阶段中间结果"}],
        "warnings": [],
        "endpoint_results": [{"id": "stage_one_endpoint", "status": "completed", "summary": "第一阶段计算完成。"}],
        "scientific_payload": {"primary_estimand": "two-stage deterministic transformation", "estimate": total, "interval": None, "equivalence_bounds": None, "sensitivity": None, "uncertainty_reasons": []}
    }
'''


STAGE_TWO_CODE = '''import json

def run_experiment(context):
    source = context["artifact_path_by_id"]["intermediate_artifact"]
    payload = json.loads(source.read_text(encoding="utf-8"))
    doubled = float(payload["stage_total"]) * 2.0
    path = context["output_dir"] / "final.json"
    path.write_text(json.dumps({"doubled_total": doubled, "is_twenty": doubled == 20.0}), encoding="utf-8")
    return {
        "schema_version": "automatic-experiment-worker-result-v1",
        "execution_completed": True,
        "measurements": [{"name": "doubled_total", "value": doubled, "unit": "dimensionless", "role": "primary", "source_artifact": "final.json"}],
        "result_items": [{"id": "is_twenty", "display_name": "是否等于二十", "value_kind": "boolean", "value": doubled == 20.0, "unit": "", "role": "secondary", "source_artifact": "final.json"}],
        "artifacts": [{"path": "final.json", "kind": "json", "description": "第二阶段最终结果"}],
        "warnings": [],
        "endpoint_results": [{"id": "stage_two_endpoint", "status": "completed", "summary": "第二阶段计算完成。"}],
        "scientific_payload": {"primary_estimand": "two-stage deterministic transformation", "estimate": doubled, "interval": None, "equivalence_bounds": None, "sensitivity": None, "uncertainty_reasons": []}
    }
'''

STAGE_TWO_RECOMPUTE_CODE = STAGE_TWO_CODE.replace(
    '    source = context["artifact_path_by_id"]["intermediate_artifact"]\n'
    '    payload = json.loads(source.read_text(encoding="utf-8"))',
    '    payload = {"stage_total": 10.0}',
)


def two_stage_design(req: dict) -> dict:
    candidate = design(req)
    candidate["measurement_plan"] = [
        {
            "name": "stage_total",
            "display_name": "第一阶段总量",
            "role": "primary",
            "unit": "dimensionless",
            "scientific_meaning": "第一阶段产生的确定性总量。",
        },
        {
            "name": "input_count",
            "display_name": "输入数量",
            "role": "secondary",
            "unit": "records",
            "scientific_meaning": "第一阶段计算所依据的记录数量。",
        },
        {
            "name": "doubled_total",
            "display_name": "第二阶段倍增总量",
            "role": "primary",
            "unit": "dimensionless",
            "scientific_meaning": "读取已核对中间结果后得到的倍增值。",
        },
    ]
    candidate["result_plan"] = [
        {
            "id": "is_twenty",
            "display_name": "是否等于二十",
            "value_kind": "boolean",
            "role": "secondary",
            "unit": "",
            "scientific_meaning": "最终倍增值是否等于二十。",
        },
    ]
    candidate["criteria"] = [
        {
            "id": "stage_one_criterion",
            "statement": "第一阶段总量与输入数量均可核对。",
            "basis_kind": "data_derived",
            "basis_text": "由第一阶段固定计算和中间结果文件直接复算。",
            "source_refs": [],
            "artifact_refs": ["intermediate.json"],
            "measurement_refs": ["stage_total", "input_count"],
            "result_refs": [],
            "endpoint_refs": ["stage_one_endpoint"],
        },
        {
            "id": "stage_two_criterion",
            "statement": "第二阶段正确读取中间结果并完成倍增计算。",
            "basis_kind": "data_derived",
            "basis_text": "由只读中间结果和最终结果文件直接复算。",
            "source_refs": [],
            "artifact_refs": ["intermediate.json", "final.json"],
            "measurement_refs": ["doubled_total"],
            "result_refs": ["is_twenty"],
            "endpoint_refs": ["stage_two_endpoint"],
        },
    ]
    candidate["artifact_plan"] = [
        {
            "id": "intermediate_artifact",
            "path": "intermediate.json",
            "kind": "json",
            "description": "第一阶段中间结果。",
            "producer_stage_id": "stage_one",
        },
        {
            "id": "final_artifact",
            "path": "final.json",
            "kind": "json",
            "description": "第二阶段最终结果。",
            "producer_stage_id": "stage_two",
        },
    ]
    terminal = {
        "inconclusive": "high_uncertainty",
        "input_missing": "input_missing",
        "evidence_conflict": "high_uncertainty",
        "method_invalid": "method_mismatch",
        "technical_failure": "technical_failure",
        "budget_reached": "budget_stopped",
    }
    rules = {
        "completed": "本阶段声明的结果、端点和产物均已核对。",
        "inconclusive": "本阶段完成，但证据不足以继续作明确判断。",
        "input_missing": "本阶段缺少必要输入。",
        "evidence_conflict": "本阶段结果相互冲突。",
        "method_invalid": "本阶段方法不再适用。",
        "technical_failure": "代码、进程、结果格式或产物检查失败。",
        "budget_reached": "剩余预算不足以完成本阶段。",
    }
    candidate["experiment_stages"] = [
        {
            "id": "stage_one",
            "objective": "生成并核对供后续使用的中间总量。",
            "input_ids": [],
            "consumes_artifact_ids": [],
            "produces_artifact_ids": ["intermediate_artifact"],
            "prerequisite_stage_ids": [],
            "join_policy": "all",
            "method_outline": "计算固定总量并保存中间结果。",
            "measurement_refs": ["stage_total", "input_count"],
            "result_refs": [],
            "endpoint_ids": ["stage_one_endpoint"],
            "criterion_refs": ["stage_one_criterion"],
            "outcome_rules": copy.deepcopy(rules),
            "transitions": {"completed": "stage_two", **terminal},
            "execution": {
                "entry_file": "experiment.py",
                "dependencies": [],
                "deterministic": True,
                "seed": 1729,
                "expected_artifacts": ["intermediate.json"],
            },
        },
        {
            "id": "stage_two",
            "objective": "只读使用第一阶段结果并完成倍增计算。",
            "input_ids": [],
            "consumes_artifact_ids": ["intermediate_artifact"],
            "produces_artifact_ids": ["final_artifact"],
            "prerequisite_stage_ids": ["stage_one"],
            "join_policy": "all",
            "method_outline": "读取中间总量并乘以二。",
            "measurement_refs": ["doubled_total"],
            "result_refs": ["is_twenty"],
            "endpoint_ids": ["stage_two_endpoint"],
            "criterion_refs": ["stage_two_criterion"],
            "outcome_rules": copy.deepcopy(rules),
            "transitions": {"completed": "completed_interpretable", **terminal},
            "execution": {
                "entry_file": "experiment.py",
                "dependencies": [],
                "deterministic": True,
                "seed": 1729,
                "expected_artifacts": ["final.json"],
            },
        },
    ]
    candidate["interpretation_policy"]["primary_estimand"] = (
        "two-stage deterministic transformation"
    )
    return candidate


def stage_assessment(stage: int, *, outcome: str = "completed") -> dict:
    criterion = "stage_one_criterion" if stage == 1 else "stage_two_criterion"
    proposed = (
        "partial_result"
        if stage == 1 and outcome == "completed"
        else "completed_interpretable"
        if outcome == "completed"
        else "high_uncertainty"
    )
    return {
        "proposed_outcome": proposed,
        "stage_outcome": outcome,
        "rationale": "当前阶段的实际结果和来源文件已经核对。",
        "criterion_results": [
            {
                "criterion_id": criterion,
                "status": "met" if outcome == "completed" else "uncertain",
                "explanation": "结果与本阶段预定计算一致。" if outcome == "completed" else "现有证据不足以进入下一阶段。",
            }
        ],
        "uncertainty_reasons": [] if outcome == "completed" else ["当前证据不足以继续。"],
        "null_assessment": None,
        "report_narrative": {
            "title": "两阶段确定性计算报告",
            "objective": "核对中间结果能否被后续阶段只读使用。",
            "data_scope": "本次只使用当前两阶段产生的确定性结果。",
            "method": "先生成中间总量，再由条件阶段读取并倍增。",
            "interpretation": "结果只说明本次确定性两阶段传递是否正确。",
            "evidence_strength": "结果和来源文件可以相互复核。",
            "claim_boundary": "不支持超出当前固定计算的总体、因果或预测结论。",
            "limitations": ["该夹具用于验证阶段传递，不代表真实观测研究。"],
            "next_steps": ["需要时可按相同条件重现本次计算。"],
        },
    }


class MultistageContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.req = request("unit_multistage")
        self.response = response(self.req)
        self.candidate = two_stage_design(self.req)

    def test_two_stage_design_is_valid_without_fixed_model_stack(self) -> None:
        validated = validate_design(self.candidate, self.req, self.response)
        self.assertEqual(len(validated["experiment_stages"]), 2)
        keys = {row["decision_key"] for row in validated["method_decisions"]}
        self.assertNotIn("model", keys)
        self.assertNotIn("data_split", keys)
        self.assertNotIn("primary_metric", keys)

    def test_unknown_artifact_cycle_unreachable_and_stage_budget_are_rejected(self) -> None:
        cases = []
        unknown = copy.deepcopy(self.candidate)
        unknown["experiment_stages"][1]["consumes_artifact_ids"] = ["missing_artifact"]
        cases.append(unknown)
        cycle = copy.deepcopy(self.candidate)
        cycle["experiment_stages"][1]["transitions"]["completed"] = "stage_one"
        cases.append(cycle)
        unreachable = copy.deepcopy(self.candidate)
        for key in unreachable["experiment_stages"][0]["transitions"]:
            unreachable["experiment_stages"][0]["transitions"][key] = "high_uncertainty"
        cases.append(unreachable)
        limited_request = copy.deepcopy(self.req)
        limited_request["run_budget"]["max_stages"] = 1
        with self.assertRaises(ContractError):
            validate_design(self.candidate, limited_request, self.response)
        for candidate in cases:
            with self.subTest(candidate=candidate["experiment_stages"]):
                with self.assertRaises(ContractError):
                    validate_design(candidate, self.req, self.response)

    def test_pre_refactor_design_is_rejected(self) -> None:
        legacy = copy.deepcopy(self.candidate)
        legacy.pop("experiment_stages")
        with self.assertRaises(ContractError):
            validate_design(legacy, self.req, self.response)

    def test_summary_stage_count_must_match_the_actual_graph(self) -> None:
        mismatch = copy.deepcopy(self.candidate)
        mismatch["design_summary"] = "三阶段实验，但实际图只含两个步骤。"
        with self.assertRaises(ContractError):
            validate_design(mismatch, self.req, self.response)

        response_mismatch = copy.deepcopy(self.response)
        response_mismatch["design_summary"] = "Three-stage bounded experiment."
        with self.assertRaises(ContractError):
            validate_design(self.candidate, self.req, response_mismatch)

    def test_measurement_has_only_one_producing_stage(self) -> None:
        duplicate = copy.deepcopy(self.candidate)
        duplicate["experiment_stages"][1]["measurement_refs"].append("stage_total")
        with self.assertRaisesRegex(
            ContractError,
            "measurements must have one producing stage",
        ):
            validate_design(duplicate, self.req, self.response)


class MultistageExecutionTests(unittest.TestCase):
    def _ready(self, task_name: str) -> tuple[str, dict]:
        req = request(task_name)
        run_id = service.bind_request({"request": req})["run_id"]
        self.addCleanup(cleanup_run, run_id)
        service.inspect_inputs(run_id)
        candidate = two_stage_design(req)
        service.validate_and_store_design(run_id, response(req), candidate)
        return run_id, candidate

    def test_two_stages_pass_read_only_artifacts_and_report_all_results(self) -> None:
        run_id, _ = self._ready("unit_multistage_execution")
        first = service.prepare(
            run_id,
            [{"path": "experiment.py", "content": STAGE_ONE_CODE}],
            None,
            "实现第一阶段。",
        )
        service.execute(run_id, first["attempt_id"])
        preview = service.verify(run_id, first["attempt_id"], None)
        self.assertIn(
            "partial_result",
            preview["assessment_authoring_guide"][
                "scientific_assessment_contract"
            ]["completed_stage_outcome_rule"],
        )
        input_count = next(
            row
            for row in preview["trusted_worker_result"]["measurements"]
            if row["name"] == "input_count"
        )
        self.assertEqual(input_count["value"], 4)
        transition = service.verify(run_id, first["attempt_id"], stage_assessment(1))
        self.assertEqual(transition["status"], "next_stage_required")
        root, state = load_state(run_id)
        self.assertEqual(state["phase"], "stage_transitioned")
        self.assertEqual(state["artifact_lineage"][0]["read_only_handoff"], True)
        self.assertTrue((root / state["artifact_lineage"][0]["path"]).is_file())
        current_status = service.status(run_id)
        self.assertEqual(
            current_status["current_stage_objective"],
            "只读使用第一阶段结果并完成倍增计算。",
        )

        with self.assertRaises(CodePolicyError):
            service.prepare(
                run_id,
                [{"path": "experiment.py", "content": STAGE_TWO_RECOMPUTE_CODE}],
                None,
                "错误地绕过前序结果重新计算。",
            )

        second = service.prepare(
            run_id,
            [{"path": "experiment.py", "content": STAGE_TWO_CODE}],
            None,
            "读取第一阶段产物并完成第二阶段。",
        )
        service.execute(run_id, second["attempt_id"])
        service.verify(run_id, second["attempt_id"], None)
        verified = service.verify(run_id, second["attempt_id"], stage_assessment(2))
        self.assertEqual(verified["outcome"], "completed_interpretable")
        entry = service.finalize(run_id)
        report = entry["user_display_markdown"]
        self.assertNotIn("第 1 阶段", report)
        self.assertNotIn("第 2 阶段", report)
        self.assertIn("第一阶段总量", report)
        self.assertIn("第二阶段倍增总量", report)
        self.assertIn("输入数量", report)
        self.assertIn("是否等于二十", report)
        record = read_json(runs_root() / run_id / "record.json")
        self.assertEqual(
            [row["attempt_id"] for row in record["attempt_history"]],
            ["attempt-001", "attempt-002"],
        )

    def test_unmet_condition_skips_later_stage_and_still_reports(self) -> None:
        run_id, _ = self._ready("unit_multistage_skipped")
        first = service.prepare(
            run_id,
            [{"path": "experiment.py", "content": STAGE_ONE_CODE}],
            None,
            "实现第一阶段。",
        )
        service.execute(run_id, first["attempt_id"])
        service.verify(run_id, first["attempt_id"], None)
        verified = service.verify(
            run_id,
            first["attempt_id"],
            stage_assessment(1, outcome="inconclusive"),
        )
        self.assertEqual(verified["outcome"], "high_uncertainty")
        entry = service.finalize(run_id)
        self.assertIn("第一阶段总量", entry["user_display_markdown"])
        self.assertNotIn("第二阶段倍增总量", entry["user_display_markdown"])
        _, state = load_state(run_id)
        self.assertEqual(len(state["stage_history"]), 1)

    def test_exact_replay_prepares_each_executed_stage_without_redesign(self) -> None:
        source_run_id, _ = self._ready("unit_multistage_replay")
        first = service.prepare(
            source_run_id,
            [{"path": "experiment.py", "content": STAGE_ONE_CODE}],
            None,
            "实现第一阶段。",
        )
        service.execute(source_run_id, first["attempt_id"])
        service.verify(source_run_id, first["attempt_id"], None)
        service.verify(source_run_id, first["attempt_id"], stage_assessment(1))
        second = service.prepare(
            source_run_id,
            [{"path": "experiment.py", "content": STAGE_TWO_CODE}],
            None,
            "实现第二阶段。",
        )
        service.execute(source_run_id, second["attempt_id"])
        service.verify(source_run_id, second["attempt_id"], None)
        service.verify(source_run_id, second["attempt_id"], stage_assessment(2))
        service.finalize(source_run_id)

        prepared = service.prepare_replay(source_run_id)
        replay_run_id = prepared["run_id"]
        self.addCleanup(cleanup_run, replay_run_id)
        self.assertEqual(
            set(prepared["lineage"]["source_code_sha256"]),
            {"stage_one", "stage_two"},
        )
        service.execute(replay_run_id, prepared["attempt_id"])
        service.verify(replay_run_id, prepared["attempt_id"], None)
        next_stage = service.verify(
            replay_run_id,
            prepared["attempt_id"],
            stage_assessment(1),
        )
        self.assertEqual(next_stage["status"], "next_stage_prepared")
        replay_second = next_stage["prepared_attempt_id"]
        self.assertIsInstance(replay_second, str)
        service.execute(replay_run_id, replay_second)
        service.verify(replay_run_id, replay_second, None)
        service.verify(replay_run_id, replay_second, stage_assessment(2))
        entry = service.finalize(replay_run_id)
        self.assertEqual(entry["outcome"], "completed_interpretable")
        replay_record = read_json(runs_root() / replay_run_id / "record.json")
        self.assertTrue(replay_record["replay"]["exact_replay_verified"])

    def test_interruption_and_cross_session_continuation_always_leave_a_report(self) -> None:
        req = request("unit_interrupted_before_design")
        run_id = service.bind_request({"request": req})["run_id"]
        self.addCleanup(cleanup_run, run_id)
        entry = service.finalize_interrupted(run_id, "模型在设计前结束。")
        self.assertEqual(entry["status"], "finalized")
        self.assertIn("当前没有形成可报告的测量结果", entry["user_display_markdown"])
        continuation = service.prepare_continuation(run_id)
        self.addCleanup(cleanup_run, continuation["run_id"])
        self.assertEqual(continuation["status"], "continuation_created")
        self.assertNotEqual(continuation["run_id"], run_id)
        child_root, child_state = load_state(continuation["run_id"])
        self.assertEqual(child_state["lineage"]["source_run_id"], run_id)
        self.assertTrue((runs_root() / run_id / "report.md").is_file())
        self.assertEqual(read_json(child_root / "request.json")["replay_of"], run_id)

    def test_unchanged_repair_does_not_consume_an_attempt(self) -> None:
        run_id, _ = self._ready("unit_unchanged_repair")
        failing_code = STAGE_ONE_CODE.replace(
            "    total = 10.0",
            '    raise RuntimeError("deliberate technical failure")\n    total = 10.0',
        )
        first = service.prepare(
            run_id,
            [{"path": "experiment.py", "content": failing_code}],
            None,
            "初始代码。",
        )
        service.execute(run_id, first["attempt_id"])
        failed = service.verify(run_id, first["attempt_id"], None)
        self.assertEqual(failed["outcome"], "technical_failure")
        root, before = load_state(run_id)
        before_count = before["attempt_count"]
        with self.assertRaises(RuntimeError):
            service.prepare(
                run_id,
                [{"path": "experiment.py", "content": failing_code}],
                first["attempt_id"],
                "没有实际变化。",
            )
        after = read_json(root / "state.json")
        self.assertEqual(after["attempt_count"], before_count)


if __name__ == "__main__":
    unittest.main()
