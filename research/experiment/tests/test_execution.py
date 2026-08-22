from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, BrokenBarrierError, Event, Lock
from unittest.mock import patch

from automatic_experiment import service
from automatic_experiment.contracts import default_request
from automatic_experiment.executor import _output_tree_size
from automatic_experiment.reporting import (
    ENDPOINT_STATUS_LABELS,
    _analysis_mode_label,
    _attempt_lines,
    _input_role_label,
)
from automatic_experiment.state import (
    load_state,
    read_json,
    runs_root,
    save_state,
    task_workspace,
)
from automatic_experiment.verification import (
    _active_stage_design,
    _close_measurement,
    _comparison_consistency_errors,
    _hypothesis_relation_consistency_errors,
    _paired_comparison_audit_errors,
    _paired_directional_result_errors,
    _sandbox_isolation_passed,
)
from tests.helpers import (
    FAIL_CODE,
    INPUT_MEAN_CODE,
    LOOP_CODE,
    SUCCESS_CODE,
    assessment,
    cleanup_run,
    create_ready_run,
    design,
    request,
    response,
)


def research_scope(
    *,
    artifact_sha256: str = "a" * 64,
    revision_review_id: str | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "research-experiment-scope-v1",
        "task_id": "research-task-a",
        "stage": "experiment_design",
        "accepted_upstream_refs": [
            {
                "artifact_id": "hypothesis-artifact",
                "version": 1,
                "artifact_sha256": artifact_sha256,
                "stage": "hypothesis",
            }
        ],
        "revision_review_id": revision_review_id,
        "design_validation_limit": 3,
    }


class ExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_workspace = tempfile.TemporaryDirectory(
            prefix="experiment_test_workspace_"
        )
        self.addCleanup(self._temporary_workspace.cleanup)
        workspace = Path(self._temporary_workspace.name)
        inputs = workspace / "inputs"
        handoff = inputs / "upstream_handoff_demo"
        handoff.mkdir(parents=True)
        (inputs / "example_mean.csv").write_text(
            "group,value\nA,1\nA,2\nB,3\nB,4\n",
            encoding="utf-8",
        )
        (inputs / "README.md").write_text(
            "真实任务应提供自己的输入；本文件只在临时测试目录中存在。",
            encoding="utf-8",
        )
        (handoff / "polar_overlap_features.csv").write_text(
            "month,phase,polar_field,overlap_index,quality_flag\n"
            "2014-06,declining,0.40,1.36,suspect_geometry\n"
            "2025-07,maximum,2.30,3.14,ok\n",
            encoding="utf-8",
        )
        self._workspace_scope = task_workspace(workspace)
        self._workspace_scope.__enter__()
        self.addCleanup(self._workspace_scope.__exit__, None, None, None)

    def test_macos_seatbelt_counts_as_effective_sandbox_isolation(self) -> None:
        self.assertTrue(
            _sandbox_isolation_passed(
                {
                    "backend": "macOS seatbelt (sandbox-exec)",
                    "new_session": True,
                    "host_project_mounted": False,
                    "home_mounted": False,
                    "input_snapshot_read_only": True,
                    "attempt_code_read_only": True,
                    "attempt_output_only_writable_mount": True,
                    "locked_site_packages_read_only": True,
                    "network_isolation": True,
                    "host_file_reads_restricted": True,
                    "user_namespace": False,
                    "pid_namespace": False,
                    "network_namespace": False,
                }
            )
        )

    def test_supports_relation_cannot_conflict_with_declared_diagnostics(self) -> None:
        worker_result = {
            "result_items": [
                {"id": "hypothesis_relation", "value": "supports"},
                {"id": "main_effect_direction_confirmed", "value": True},
                {"id": "out_of_sample_complete", "value": True},
                {"id": "leave_one_unit_direction_stable", "value": True},
                {"id": "influential_unit_changes_conclusion", "value": False},
                {"id": "independent_sample_adequate", "value": False},
                {"id": "interaction_survives_amplitude_adjustment", "value": True},
                {"id": "complexity_fallback_used", "value": False},
            ]
        }

        errors = _hypothesis_relation_consistency_errors(worker_result)

        self.assertEqual(len(errors), 1)
        self.assertIn("independent_sample_adequate=false", errors[0])

    def test_non_supporting_relation_preserves_negative_diagnostics(self) -> None:
        worker_result = {
            "result_items": [
                {"id": "hypothesis_relation", "value": "indeterminate"},
                {"id": "independent_sample_adequate", "value": False},
                {"id": "influential_unit_changes_conclusion", "value": True},
            ]
        }

        self.assertEqual(_hypothesis_relation_consistency_errors(worker_result), [])

    def test_directional_typed_result_rejects_reversed_mae_claim(self) -> None:
        design_payload = {
            "result_plan": [
                {
                    "id": "improvement_direction",
                    "display_name": "MAE 变化方向",
                    "scientific_meaning": "排除标记观测后留出 MAE 是降低还是升高",
                    "value_kind": "text",
                }
            ],
            "paired_comparison_audits": [
                {
                    "id": "holdout_comparison",
                    "metric": "mae",
                    "baseline_measurement": "mae_include",
                    "candidate_measurement": "mae_exclude",
                    "baseline_fit_condition": "include_flagged",
                    "candidate_fit_condition": "exclude_flagged",
                }
            ],
        }
        worker_result = {
            "result_items": [
                {
                    "id": "improvement_direction",
                    "value_kind": "text",
                    "value": "排除标记观测后留出 MAE 升高",
                }
            ]
        }
        trusted = [
            {
                "id": "holdout_comparison",
                "recomputed_measurements": {
                    "mae_include": 0.043993,
                    "mae_exclude": 0.017435,
                },
            }
        ]

        errors = _paired_directional_result_errors(
            design_payload,
            worker_result,
            trusted,
        )

        self.assertEqual(len(errors), 1)
        self.assertIn("candidate mae is lower", errors[0])

    def test_directional_typed_result_accepts_verified_mae_claim(self) -> None:
        design_payload = {
            "result_plan": [
                {
                    "id": "improvement_direction",
                    "display_name": "MAE 变化方向",
                    "scientific_meaning": "排除标记观测后留出 MAE 是降低还是升高",
                    "value_kind": "text",
                }
            ],
            "paired_comparison_audits": [
                {
                    "id": "holdout_comparison",
                    "metric": "mae",
                    "baseline_measurement": "mae_include",
                    "candidate_measurement": "mae_exclude",
                    "baseline_fit_condition": "include_flagged",
                    "candidate_fit_condition": "exclude_flagged",
                }
            ],
        }
        worker_result = {
            "result_items": [
                {
                    "id": "improvement_direction",
                    "value_kind": "text",
                    "value": "排除标记观测后留出 MAE 降低",
                }
            ]
        }
        trusted = [
            {
                "id": "holdout_comparison",
                "recomputed_measurements": {
                    "mae_include": 0.043993,
                    "mae_exclude": 0.017435,
                },
            }
        ]

        self.assertEqual(
            _paired_directional_result_errors(
                design_payload,
                worker_result,
                trusted,
            ),
            [],
        )

    def test_active_stage_keeps_source_baseline_audit_without_delta(
        self,
    ) -> None:
        active = _active_stage_design(
            {
                "criteria": [],
                "measurement_plan": [
                    {"name": "before"},
                    {"name": "after"},
                ],
                "result_plan": [],
                "paired_comparison_audits": [
                    {
                        "id": "calibration",
                        "baseline_measurement": "before",
                        "candidate_measurement": "after",
                        "delta_measurement": None,
                    }
                ],
                "experiment_stages": [
                    {
                        "id": "calibration_stage",
                        "criterion_refs": [],
                        "measurement_refs": ["before", "after"],
                        "result_refs": [],
                    }
                ],
            },
            "calibration_stage",
        )
        self.assertEqual(
            [row["id"] for row in active["paired_comparison_audits"]],
            ["calibration"],
        )

    def test_output_size_tolerates_atomic_rename_race(self) -> None:
        class StableEntry:
            def is_file(self) -> bool:
                return True

            def stat(self) -> object:
                return type("Stat", (), {"st_size": 7})()

        class VanishedEntry:
            def is_file(self) -> bool:
                return True

            def stat(self) -> object:
                raise FileNotFoundError("temporary output was atomically renamed")

        class OutputRoot:
            def rglob(self, pattern: str) -> list[object]:
                self.assert_pattern = pattern
                return [StableEntry(), VanishedEntry()]

        root = OutputRoot()
        self.assertEqual(_output_tree_size(root), 7)  # type: ignore[arg-type]
        self.assertEqual(root.assert_pattern, "*")

    def test_bind_returns_compact_multistage_authoring_guide(self) -> None:
        req = request("unit_authoring_guide")
        result = service.bind_request({"request": req})
        self.addCleanup(cleanup_run, result["run_id"])
        guide = result["authoring_guide"]
        self.assertIn("experiment_stages", guide["design"]["exact_fields"])
        method_shape = guide["design"]["object_shapes"]["method_decision_item"]
        self.assertIn("decision_key", method_shape)
        self.assertNotIn("decision_type", method_shape)
        self.assertIn("result_items", guide["worker_result"]["exact_fields"])
        self.assertEqual(
            guide["worker_result"]["schema_version_value"],
            "automatic-experiment-worker-result-v1",
        )
        self.assertIn("stage_outcome", guide["scientific_assessment"]["exact_fields"])
        paired_shape = guide["design"]["object_shapes"]["paired_comparison_item"]
        self.assertEqual(paired_shape["fit_evaluation_relation"], "disjoint_rows")
        self.assertIn("delta_formula", paired_shape)
        self.assertIn(
            "number/count only as diagnostics",
            guide["design"]["object_shapes"]["result_plan_item"]["value_kind"],
        )
        self.assertEqual(
            guide["design"]["object_shapes"]["criterion_item"]["artifact_refs"],
            ["artifact paths, not ids"],
        )
        self.assertLess(len(json.dumps(guide, ensure_ascii=False)), 12000)
        self.assertEqual(
            set(guide["design"]["stage_nested_shapes"]["outcome_rules"]),
            {
                "completed",
                "inconclusive",
                "input_missing",
                "evidence_conflict",
                "method_invalid",
                "technical_failure",
                "budget_reached",
            },
        )
        self.assertEqual(
            set(guide["design"]["stage_nested_shapes"]["transitions"]),
            set(guide["design"]["stage_nested_shapes"]["outcome_rules"]),
        )
        self.assertEqual(
            set(guide["design"]["stage_nested_shapes"]["execution"]),
            {
                "entry_file",
                "dependencies",
                "deterministic",
                "seed",
                "expected_artifacts",
            },
        )
        self.assertNotIn("clarification_required", guide["design"]["terminal_targets"])
        self.assertIn("path", guide["design"]["object_shapes"]["artifact_plan_item"])
        self.assertIn(
            "bounded_pragmatic_choice",
            guide["design"]["object_shapes"]["method_decision_item"]["basis_kind"],
        )
        self.assertIn("scientific_payload", guide["worker_result"]["item_shapes"])
        self.assertIn(
            "Do not create duplicate result_items or duplicate JSON keys",
            " ".join(guide["worker_result"]["rules"]),
        )
        self.assertIn(
            "nesting is allowed",
            " ".join(guide["worker_result"]["rules"]),
        )
        design_rules = " ".join(guide["design"]["rules"])
        self.assertIn("one to five forward-only stages", design_rules)
        self.assertIn("Do not require a model, split, metric, baseline", design_rules)
        self.assertIn("never duplicate the same fact", design_rules)
        self.assertIn("delta_formula", design_rules)

    def test_research_scope_rejects_fresh_rebind_without_creating_a_run(self) -> None:
        req = request("unit_research_scope_rebind")
        scope = research_scope()
        first = service.bind_request({"request": req}, research_scope=scope)
        self.addCleanup(cleanup_run, first["run_id"])
        before = {path.name for path in runs_root().iterdir()}

        changed = request(
            "unit_research_scope_rebind_changed",
            task="Change the preregistered sample definition and analysis rule.",
        )
        with self.assertRaises(service.ServiceError) as raised:
            service.bind_request({"request": changed}, research_scope=scope)

        self.assertEqual(
            raised.exception.error_code,
            "RESEARCH_EXPERIMENT_SCOPE_ALREADY_BOUND",
        )
        self.assertEqual(raised.exception.run_id, first["run_id"])
        self.assertEqual({path.name for path in runs_root().iterdir()}, before)

    def test_concurrent_research_scope_bind_creates_exactly_one_run(self) -> None:
        scope = research_scope()
        workspace = runs_root().parents[1]
        barrier = Barrier(2)
        original_lookup = service._run_bound_to_research_scope

        def synchronized_lookup(scope_identity: str) -> str | None:
            result = original_lookup(scope_identity)
            if result is None:
                try:
                    barrier.wait(timeout=0.5)
                except BrokenBarrierError:
                    pass
            return result

        def bind(task_name: str) -> tuple[str, object]:
            try:
                with task_workspace(workspace):
                    return (
                        "success",
                        service.bind_request(
                            {"request": request(task_name)}, research_scope=scope
                        ),
                    )
            except service.ServiceError as exc:
                return ("error", exc)

        with patch.object(
            service,
            "_run_bound_to_research_scope",
            synchronized_lookup,
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = list(
                    executor.map(
                        bind,
                        ("unit_scope_concurrent_a", "unit_scope_concurrent_b"),
                    )
                )

        successes = [payload for kind, payload in outcomes if kind == "success"]
        errors = [payload for kind, payload in outcomes if kind == "error"]
        for payload in successes:
            self.addCleanup(cleanup_run, payload["run_id"])
        self.assertEqual(len(successes), 1, outcomes)
        self.assertEqual(len(errors), 1, outcomes)
        self.assertEqual(
            errors[0].error_code,
            "RESEARCH_EXPERIMENT_SCOPE_ALREADY_BOUND",
        )
        self.assertEqual(errors[0].run_id, successes[0]["run_id"])
        identity = service.canonical_sha256(service._normalized_research_scope(scope))
        bound_states = [
            read_json(path)
            for path in runs_root().glob("*/state.json")
            if read_json(path).get("research_scope_identity") == identity
        ]
        self.assertEqual(len(bound_states), 1)

    def test_new_research_upstream_or_formal_revision_allows_a_new_run(self) -> None:
        req = request("unit_research_scope_revision")
        first = service.bind_request({"request": req}, research_scope=research_scope())
        second = service.bind_request(
            {"request": req}, research_scope=research_scope(artifact_sha256="b" * 64)
        )
        third = service.bind_request(
            {"request": req},
            research_scope=research_scope(revision_review_id="review-design-revise-2"),
        )
        for run_id in (first["run_id"], second["run_id"], third["run_id"]):
            self.addCleanup(cleanup_run, run_id)

        self.assertEqual(len({first["run_id"], second["run_id"], third["run_id"]}), 3)

    def test_unscoped_repeated_bind_remains_fresh(self) -> None:
        req = request("unit_unscoped_repeated_bind")
        first = service.bind_request({"request": req})
        second = service.bind_request({"request": req})
        self.addCleanup(cleanup_run, first["run_id"])
        self.addCleanup(cleanup_run, second["run_id"])

        self.assertNotEqual(first["run_id"], second["run_id"])

    def test_research_design_invalid_paths_share_a_separate_three_call_budget(
        self,
    ) -> None:
        req = request("unit_research_design_validation_budget")
        bound = service.bind_request({"request": req}, research_scope=research_scope())
        self.addCleanup(cleanup_run, bound["run_id"])
        _root, initial = load_state(bound["run_id"])
        initial_attempts = (
            initial["attempt_count"],
            initial["remaining_attempts"],
        )

        invalid_response = response(req)
        invalid_response.pop("design_summary")
        first = service.build_and_store_single_stage_design(
            bound["run_id"], invalid_response, {}
        )
        self.assertEqual(first["status"], "design_invalid")
        self.assertEqual(first["remaining"], 2)
        self.assertFalse(first["must_stop"])

        invalid_shape = design(req)
        del invalid_shape["artifact_plan"][0]["path"]
        second = service.validate_and_store_design(
            bound["run_id"], response(req), invalid_shape
        )
        self.assertEqual(second["status"], "design_invalid")
        self.assertEqual(second["remaining"], 1)
        self.assertFalse(second["must_stop"])

        invalid_semantics = design(req)
        invalid_semantics["experiment_stages"][0]["transitions"]["completed"] = (
            "unknown_stage"
        )
        third = service.validate_and_store_design(
            bound["run_id"], response(req), invalid_semantics
        )
        self.assertEqual(third["status"], "design_invalid")
        self.assertEqual(third["remaining"], 0)
        self.assertTrue(third["must_stop"])

        with (
            patch.object(
                service,
                "validate_response",
                side_effect=AssertionError("response validation must not run"),
            ),
            patch.object(
                service,
                "_design_schema_issues",
                side_effect=AssertionError("shape validation must not run"),
            ),
            patch.object(
                service,
                "validate_design",
                side_effect=AssertionError("semantic validation must not run"),
            ),
        ):
            fourth = service.build_and_store_single_stage_design(
                bound["run_id"], response(req), {}
            )

        self.assertEqual(fourth["status"], "budget_stopped")
        self.assertEqual(fourth["remaining"], 0)
        self.assertTrue(fourth["must_stop"])
        _root, final = load_state(bound["run_id"])
        self.assertEqual(
            (final["attempt_count"], final["remaining_attempts"]),
            initial_attempts,
        )
        self.assertEqual(
            final["design_validation_budget"],
            {"limit": 3, "used": 3, "remaining": 0},
        )

    def test_compact_and_dependency_design_failures_charge_research_budget(
        self,
    ) -> None:
        req = request("unit_research_compact_failure_budget")
        bound = service.bind_request({"request": req}, research_scope=research_scope())
        self.addCleanup(cleanup_run, bound["run_id"])
        malformed = {"measurements": ["not-an-object"]}

        first = service.build_and_store_single_stage_design(
            bound["run_id"], response(req), malformed
        )
        self.assertEqual(first["status"], "design_invalid")
        self.assertEqual(first["remaining"], 2)

        unreviewed = design(req)
        unreviewed["experiment_stages"][0]["execution"]["dependencies"] = [
            "unreviewed_dependency"
        ]
        second = service.validate_and_store_design(
            bound["run_id"], response(req), unreviewed
        )
        self.assertEqual(second["status"], "design_invalid")
        self.assertEqual(second["remaining"], 1)

        third = service.build_and_store_single_stage_design(
            bound["run_id"], response(req), malformed
        )
        self.assertEqual(third["status"], "design_invalid")
        self.assertEqual(third["remaining"], 0)
        self.assertTrue(third["must_stop"])

        fourth = service.build_and_store_single_stage_design(
            bound["run_id"], response(req), malformed
        )
        self.assertEqual(fourth["status"], "budget_stopped")
        _root, state = load_state(bound["run_id"])
        self.assertEqual(
            state["design_validation_budget"],
            {"limit": 3, "used": 3, "remaining": 0},
        )

    def test_compact_conversion_errors_charge_research_design_budget(self) -> None:
        req = request("unit_research_compact_conversion_budget")
        bound = service.bind_request({"request": req}, research_scope=research_scope())
        self.addCleanup(cleanup_run, bound["run_id"])
        invalid_seed = {"seed": "not-an-integer"}
        invalid_alternatives = {"method_alternatives": 42}

        first = service.build_and_store_single_stage_design(
            bound["run_id"], response(req), invalid_seed
        )
        second = service.build_and_store_single_stage_design(
            bound["run_id"], response(req), invalid_alternatives
        )
        third = service.build_and_store_single_stage_design(
            bound["run_id"], response(req), invalid_seed
        )
        fourth = service.build_and_store_single_stage_design(
            bound["run_id"], response(req), invalid_seed
        )

        self.assertEqual(
            [first["status"], second["status"], third["status"]],
            ["design_invalid"] * 3,
        )
        self.assertEqual([first["remaining"], second["remaining"]], [2, 1])
        self.assertEqual(third["remaining"], 0)
        self.assertTrue(third["must_stop"])
        self.assertEqual(fourth["status"], "budget_stopped")
        for result in (first, second, third):
            self.assertEqual(result["issues"][0]["field_path"], "analysis")
        _root, state = load_state(bound["run_id"])
        self.assertEqual(
            state["design_validation_budget"],
            {"limit": 3, "used": 3, "remaining": 0},
        )

    def test_concurrent_compact_calls_share_last_validation_budget_slot(self) -> None:
        req = request("unit_research_compact_budget_concurrent")
        bound = service.bind_request({"request": req}, research_scope=research_scope())
        self.addCleanup(cleanup_run, bound["run_id"])
        malformed_row = {"measurements": ["not-an-object"]}
        for _index in range(2):
            result = service.build_and_store_single_stage_design(
                bound["run_id"], response(req), malformed_row
            )
            self.assertEqual(result["status"], "design_invalid")
        workspace = runs_root().parents[1]
        invalid_seed = {"seed": "not-an-integer"}
        barrier = Barrier(2)
        counter_lock = Lock()
        validation_calls = 0
        expansion_calls = 0
        original_validate_response = service.validate_response
        original_expand = service._compact_single_stage_design

        def counted_validate_response(
            *args: object, **kwargs: object
        ) -> dict[str, object]:
            nonlocal validation_calls
            with counter_lock:
                validation_calls += 1
            return original_validate_response(*args, **kwargs)

        def synchronized_expand(*args: object, **kwargs: object) -> dict[str, object]:
            nonlocal expansion_calls
            with counter_lock:
                expansion_calls += 1
            try:
                barrier.wait(timeout=0.5)
            except BrokenBarrierError:
                pass
            return original_expand(*args, **kwargs)

        def submit(_index: int) -> dict[str, object]:
            with task_workspace(workspace):
                return service.build_and_store_single_stage_design(
                    bound["run_id"], response(req), invalid_seed
                )

        with (
            patch.object(service, "validate_response", counted_validate_response),
            patch.object(service, "_compact_single_stage_design", synchronized_expand),
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(submit, range(2)))

        by_status = {result["status"]: result for result in results}
        self.assertEqual(set(by_status), {"design_invalid", "budget_stopped"})
        self.assertEqual(by_status["design_invalid"]["remaining"], 0)
        self.assertTrue(by_status["design_invalid"]["must_stop"])
        self.assertEqual(validation_calls, 1)
        self.assertEqual(expansion_calls, 1)

    def test_concurrent_design_invalid_updates_do_not_lose_budget_count(self) -> None:
        req = request("unit_research_design_budget_concurrent")
        bound = service.bind_request({"request": req}, research_scope=research_scope())
        self.addCleanup(cleanup_run, bound["run_id"])
        workspace = runs_root().parents[1]
        barrier = Barrier(2)

        def synchronized_shape_issues(*_args: object) -> list[dict[str, str]]:
            try:
                barrier.wait(timeout=0.5)
            except BrokenBarrierError:
                pass
            return [
                {
                    "field_path": "design.measurement_plan",
                    "message": "concurrent invalid design",
                    "suggestion": "repair the named field",
                }
            ]

        def submit(_index: int) -> dict[str, object]:
            with task_workspace(workspace):
                return service.validate_and_store_design(
                    bound["run_id"], response(req), design(req)
                )

        with patch.object(
            service,
            "_design_schema_issues",
            synchronized_shape_issues,
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(submit, range(2)))

        self.assertEqual([row["status"] for row in results], ["design_invalid"] * 2)
        _root, state = load_state(bound["run_id"])
        self.assertEqual(
            state["design_validation_budget"],
            {"limit": 3, "used": 2, "remaining": 1},
        )

    def test_concurrent_valid_design_does_not_overwrite_invalid_budget_charge(
        self,
    ) -> None:
        req = request("unit_research_design_valid_invalid_concurrent")
        bound = service.bind_request({"request": req}, research_scope=research_scope())
        self.addCleanup(cleanup_run, bound["run_id"])
        workspace = runs_root().parents[1]
        barrier = Barrier(2)
        invalid_saved = Event()
        invalid_validator_started = Event()
        original_invalid_result = service._design_invalid_result

        def synchronized_shape_issues(
            candidate: dict[str, object], _request: dict[str, object]
        ) -> list[dict[str, str]]:
            if candidate["design_summary"] == "concurrent invalid design":
                invalid_validator_started.set()
            try:
                barrier.wait(timeout=0.5)
            except BrokenBarrierError:
                pass
            if candidate["design_summary"] == "concurrent invalid design":
                return [
                    {
                        "field_path": "design.design_summary",
                        "message": "concurrent invalid design",
                        "suggestion": "repair the design summary",
                    }
                ]
            invalid_saved.wait(timeout=1)
            return []

        def recording_invalid_result(
            *args: object, **kwargs: object
        ) -> dict[str, object]:
            result = original_invalid_result(*args, **kwargs)
            invalid_saved.set()
            return result

        invalid = design(req)
        invalid["design_summary"] = "concurrent invalid design"

        def submit(candidate: dict[str, object]) -> dict[str, object]:
            if candidate["design_summary"] != "concurrent invalid design":
                invalid_validator_started.wait(timeout=1)
            with task_workspace(workspace):
                return service.validate_and_store_design(
                    bound["run_id"], response(req), candidate
                )

        with (
            patch.object(
                service,
                "_design_schema_issues",
                synchronized_shape_issues,
            ),
            patch.object(
                service,
                "_design_invalid_result",
                recording_invalid_result,
            ),
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                invalid_future = executor.submit(submit, invalid)
                valid_future = executor.submit(submit, design(req))
                results = [invalid_future.result(), valid_future.result()]

        self.assertEqual(
            {row["status"] for row in results},
            {"design_invalid", "design_validated"},
        )
        _root, state = load_state(bound["run_id"])
        self.assertEqual(
            state["design_validation_budget"],
            {"limit": 3, "used": 1, "remaining": 2},
        )

    def test_design_shape_check_returns_all_visible_issues_at_once(self) -> None:
        req = request("unit_aggregate_design_shape_issues")
        bound = service.bind_request({"request": req})
        self.addCleanup(cleanup_run, bound["run_id"])
        candidate = design(req)
        del candidate["artifact_plan"][0]["path"]
        candidate["experiment_stages"][0]["join_policy"] = "none"
        candidate["experiment_stages"][0]["outcome_rules"] = []
        candidate["experiment_stages"][0]["execution"] = {
            "command": "python experiment.py",
            "timeout_seconds": 120,
        }
        empty_stage = json.loads(json.dumps(candidate["experiment_stages"][0]))
        empty_stage.update(
            {
                "id": "stage_empty",
                "consumes_artifact_ids": [],
                "produces_artifact_ids": [],
                "prerequisite_stage_ids": ["stage1"],
                "measurement_refs": [],
                "result_refs": [],
                "endpoint_ids": [],
                "criterion_refs": [],
                "execution": {
                    "entry_file": "experiment.py",
                    "dependencies": [],
                    "deterministic": True,
                    "seed": 1729,
                    "expected_artifacts": [],
                },
            }
        )
        candidate["experiment_stages"].append(empty_stage)

        checked = service.validate_and_store_design(
            bound["run_id"], response(req), candidate
        )
        self.assertEqual(checked["status"], "design_invalid")
        paths = {row["field_path"] for row in checked["issues"]}
        self.assertIn("design.artifact_plan[0]", paths)
        self.assertIn("design.experiment_stages[0].join_policy", paths)
        self.assertIn("design.experiment_stages[0].outcome_rules", paths)
        self.assertIn("design.experiment_stages[0].execution", paths)
        self.assertIn("design.experiment_stages[1]", paths)
        self.assertGreaterEqual(len(checked["issues"]), 5)

    def test_design_preflight_reports_input_evidence_coverage_with_other_issues(
        self,
    ) -> None:
        req = request("unit_input_evidence_preflight")
        candidate = design(req)
        candidate["input_ids"] = ["input_01", "input_02"]
        candidate["research_frame"]["input_evidence"] = [
            {
                "input_id": "input_01",
                "role": "研究数据",
                "intended_use": "提供当前问题所需观测。",
                "limitations": "只支持当前样本范围内的分析。",
            }
        ]
        del candidate["method_fit"]

        issues = service._design_schema_issues(candidate)
        paths = {row["field_path"] for row in issues}

        self.assertIn("design", paths)
        self.assertIn("design.research_frame.input_evidence", paths)
        coverage_issue = next(
            row
            for row in issues
            if row["field_path"] == "design.research_frame.input_evidence"
        )
        self.assertIn("each selected design input", coverage_issue["message"])
        self.assertIn("每份实际使用的材料", coverage_issue["suggestion"])

    def test_pi_design_submission_fills_host_owned_identity_fields(self) -> None:
        req = request("unit_host_owned_design_fields")
        bound = service.bind_request({"request": req})
        self.addCleanup(cleanup_run, bound["run_id"])
        candidate_response = response(req)
        candidate = design(req)
        for field in ("schema_version", "task_name", "task"):
            candidate_response.pop(field, None)
        for field in ("schema_version", "task_name", "normalized_task"):
            candidate.pop(field, None)

        checked = service.validate_and_store_design(
            bound["run_id"],
            candidate_response,
            candidate,
        )

        self.assertEqual(checked["status"], "design_validated", checked)
        root = runs_root() / bound["run_id"]
        saved_response = read_json(root / "response.json")
        saved_design = read_json(root / "design.json")
        self.assertEqual(
            saved_response["schema_version"],
            "automatic-experiment-response-v1",
        )
        self.assertEqual(
            saved_design["schema_version"],
            "automatic-experiment-design-v1",
        )
        self.assertEqual(saved_design["task_name"], req["task_name"])

    def test_compact_single_stage_design_is_compiled_and_persisted(self) -> None:
        req = request(
            "unit_compact_design",
            task="估计给定周期级样本中的一个关系，并如实报告不确定性。",
        )
        bound = service.bind_request({"request": req})
        self.addCleanup(cleanup_run, bound["run_id"])
        compact = {
            "design_summary": "估计周期级关系并报告不确定性。",
            "primary_question": "当前周期级样本支持怎样的关系？",
            "analysis_mode": "周期级小样本统计分析。",
            "claim_scope": "结论仅适用于给定周期级样本。",
            "method_outline": "拟合预先声明的关系并执行周期级不确定性分析。",
            "measurements": [
                {
                    "name": "effect_estimate",
                    "display_name": "关系估计值",
                    "role": "primary",
                    "unit": "",
                    "scientific_meaning": "给定周期级样本中的关系方向和大小。",
                }
            ],
            "results": [
                {
                    "id": "hypothesis_relation",
                    "display_name": "假设与结果的关系",
                    "value_kind": "category",
                    "role": "primary",
                    "unit": "",
                    "scientific_meaning": "实际结果支持、反对或无法判定研究假设。",
                }
            ],
            "artifacts": [
                {
                    "path": "analysis.json",
                    "kind": "json",
                    "description": "周期级分析结果。",
                }
            ],
            "dependencies": ["numpy"],
            "primary_estimand": "周期级关系估计值",
            "threats_to_validity": ["独立样本数量有限。"],
            "uncertainty_rule": "区间和敏感性不足时维持未决。",
        }

        candidate_response = response(req)
        candidate_response["design_summary"] = "估计周期级关系并报告不确定性。"
        checked = service.build_and_store_single_stage_design(
            bound["run_id"], candidate_response, compact
        )

        self.assertEqual(checked["status"], "design_validated", checked)
        saved = read_json(runs_root() / bound["run_id"] / "design.json")
        self.assertEqual(len(saved["experiment_stages"]), 1)
        self.assertEqual(saved["measurement_plan"][0]["name"], "effect_estimate")
        self.assertEqual(saved["result_plan"][0]["id"], "hypothesis_relation")

    def test_design_validation_stops_when_total_run_deadline_has_elapsed(self) -> None:
        req = request("unit_design_deadline")
        bound = service.bind_request({"request": req})
        self.addCleanup(cleanup_run, bound["run_id"])
        root, state = load_state(bound["run_id"])
        state["created_at"] = "2000-01-01T00:00:00Z"
        save_state(root, state)

        checked = service.validate_and_store_design(
            bound["run_id"], response(req), design(req)
        )

        self.assertEqual(checked["status"], "terminal")
        self.assertEqual(checked["outcome"], "budget_stopped")
        _, persisted = load_state(bound["run_id"])
        self.assertEqual(persisted["outcome"], "budget_stopped")

    def test_response_contract_error_is_returned_as_a_design_issue(self) -> None:
        req = request("unit_response_issue")
        bound = service.bind_request({"request": req})
        self.addCleanup(cleanup_run, bound["run_id"])
        candidate_response = response(req)
        candidate_response.pop("design_summary")

        checked = service.validate_and_store_design(
            bound["run_id"],
            candidate_response,
            design(req),
        )

        self.assertEqual(checked["status"], "design_invalid")
        self.assertEqual(len(checked["issues"]), 1)
        self.assertIn("design_summary", checked["issues"][0]["message"])

    def test_design_preflight_aggregates_stage_ownership_and_report_artifact(
        self,
    ) -> None:
        req = request("unit_stage_ownership_preflight")
        candidate = design(req)
        second = json.loads(json.dumps(candidate["experiment_stages"][0]))
        second["id"] = "stage2"
        second["prerequisite_stage_ids"] = ["stage1"]
        candidate["experiment_stages"][0]["transitions"]["completed"] = "stage2"
        candidate["experiment_stages"].append(second)
        candidate["artifact_plan"].append(
            {
                "id": "duplicate_report",
                "path": "report.md",
                "kind": "markdown",
                "description": "最终科研报告",
                "producer_stage_id": "stage2",
            }
        )
        second["produces_artifact_ids"].append("duplicate_report")

        issues = service._design_schema_issues(candidate)
        paths = {row["field_path"] for row in issues}

        self.assertIn("design.artifact_plan[1].path", paths)
        self.assertIn("design.experiment_stages[*].measurement_refs", paths)

    def test_semantic_design_error_is_returned_as_a_design_issue(self) -> None:
        req = request("unit_semantic_design_issue")
        bound = service.bind_request({"request": req})
        self.addCleanup(cleanup_run, bound["run_id"])
        candidate = design(req)
        candidate["experiment_stages"][0]["transitions"]["completed"] = "unknown_stage"

        checked = service.validate_and_store_design(
            bound["run_id"], response(req), candidate
        )

        self.assertEqual(checked["status"], "design_invalid")
        self.assertEqual(len(checked["issues"]), 1)
        self.assertIn("unknown stage", checked["issues"][0]["message"])
        self.assertNotEqual(checked["issues"][0]["field_path"], "")

    def test_design_preflight_returns_common_cross_field_issues_together(self) -> None:
        req = request("unit_aggregate_design_semantic_issues")
        candidate = design(req)
        candidate["measurement_plan"].extend(
            [
                {
                    "name": "condition_a",
                    "display_name": "Condition A estimate",
                    "role": "secondary",
                    "unit": "G",
                    "scientific_meaning": "Estimate under condition A.",
                },
                {
                    "name": "condition_b",
                    "display_name": "Condition B estimate",
                    "role": "secondary",
                    "unit": "G",
                    "scientific_meaning": "Estimate under condition B.",
                },
                {
                    "name": "condition_delta",
                    "display_name": "Difference between conditions",
                    "role": "diagnostic",
                    "unit": "G",
                    "scientific_meaning": "Difference between condition A and condition B.",
                },
            ]
        )
        candidate["criteria"].append(
            {
                "id": "sensitivity_check",
                "statement": "The sensitivity comparison is reported.",
                "basis_kind": "user_request",
                "basis_text": "The user requested a sensitivity comparison.",
                "source_refs": [],
                "artifact_refs": [],
                "measurement_refs": ["condition_delta"],
                "result_refs": [],
                "endpoint_refs": [],
            }
        )
        candidate["paired_comparison_audits"] = [
            {
                "id": "invalid_pair",
                "comparison_kind": "source_baseline_vs_candidate",
                "evaluation_scope": "fixed evaluation rows",
                "source_input_id": "input_01",
                "source_row_id_column": "row_id",
                "source_target_column": "target",
                "source_baseline_column": "reading",
                "candidate_model_input_columns": ["reading", "target"],
                "candidate_model_target_column": "target",
                "baseline_model_input_columns": [],
                "baseline_model_target_column": None,
                "baseline_fit_condition": None,
                "candidate_fit_condition": "fit on disjoint rows",
                "fit_evaluation_relation": "disjoint_rows",
                "evaluation_target_usage": "metrics_and_evidence_only",
                "evidence_artifact": "paired.csv",
                "evidence_row_id_column": "row_id",
                "evidence_target_column": "target",
                "evidence_baseline_column": "baseline",
                "evidence_candidate_column": "candidate",
                "metric": "mse",
                "baseline_measurement": "condition_a",
                "candidate_measurement": "condition_b",
                "delta_measurement": None,
                "delta_formula": "baseline_minus_candidate",
            }
        ]
        candidate["method_decisions"][0]["decision"] = (
            "Use a fixed slope range [0.5, 2.0]."
        )
        candidate["method_decisions"][0]["source_refs"] = []
        candidate["criteria"].append(
            {
                "id": "approximate_cutoff",
                "statement": "Treat the difference as acceptable when it is below 0.10.",
                "basis_kind": "data_derived",
                "basis_text": "The 0.10 cutoff is approximately half the observed scale.",
                "source_refs": ["input_01"],
                "artifact_refs": [],
                "measurement_refs": ["condition_delta"],
                "result_refs": [],
                "endpoint_refs": [],
            }
        )
        candidate["criteria"].append(
            {
                "id": "missing_cutoff_source",
                "statement": "Require no more than 18 observations.",
                "basis_kind": "data_derived",
                "basis_text": "The supplied data contain 18 observations.",
                "source_refs": [],
                "artifact_refs": [],
                "measurement_refs": ["condition_a"],
                "result_refs": [],
                "endpoint_refs": [],
            }
        )
        issues = service._design_schema_issues(candidate)
        paths = {row["field_path"] for row in issues}
        self.assertIn("design.paired_comparison_audits[0].metric", paths)
        self.assertIn(
            "design.paired_comparison_audits[0].candidate_model_input_columns",
            paths,
        )
        self.assertIn(
            "design.paired_comparison_audits[0].delta_measurement",
            paths,
        )
        self.assertIn("design.criteria[1].measurement_refs", paths)
        self.assertIn("design.criteria[2].basis_text", paths)
        self.assertIn("design.criteria[3].source_refs", paths)
        self.assertIn("design.method_decisions[0].basis_kind", paths)
        self.assertIn("design.measurement_plan", paths)

    def test_design_preflight_reports_live_calibration_gaps_together(self) -> None:
        req = request("unit_live_calibration_preflight")
        candidate = design(req)
        candidate["measurement_plan"] = [
            {
                "name": "mae_raw_eval",
                "display_name": "评估集原始读数平均绝对误差",
                "role": "primary",
                "unit": "G",
                "scientific_meaning": "未经校正的候选读数在留出观测上的平均绝对偏差。",
            },
            {
                "name": "mae_cal_full_eval",
                "display_name": "评估集全条件校准平均绝对误差",
                "role": "primary",
                "unit": "G",
                "scientific_meaning": "全拟合集校准后在留出观测上的平均绝对偏差。",
            },
            {
                "name": "mae_cal_excl_eval",
                "display_name": "评估集排除条件校准平均绝对误差",
                "role": "primary",
                "unit": "G",
                "scientific_meaning": "排除标记观测后重新校准，在相同留出观测上的平均绝对偏差。",
            },
        ]
        candidate["criteria"][0].update(
            {
                "id": "crit_sensitivity_reported",
                "statement": "报告两种拟合条件的平均绝对误差及其差值。",
                "measurement_refs": [
                    "mae_cal_full_eval",
                    "mae_cal_excl_eval",
                    "mae_raw_eval",
                ],
            }
        )
        candidate["result_plan"] = [
            {
                "id": "sensitivity_difference",
                "display_name": "敏感性差值",
                "value_kind": "number",
                "role": "primary",
                "unit": "G",
                "scientific_meaning": "两种拟合条件的误差差值。",
            }
        ]
        candidate["paired_comparison_audits"] = []
        candidate["method_decisions"][0].update(
            {
                "basis_kind": "bounded_pragmatic_choice",
                "source_refs": [],
                "alternatives": [],
            }
        )

        issues = service._design_schema_issues(candidate)
        paths = {row["field_path"] for row in issues}
        self.assertIn("design.criteria[0].measurement_refs", paths)
        self.assertIn("design.paired_comparison_audits", paths)
        self.assertIn("design.result_plan[0]", paths)
        self.assertIn("design.method_decisions[0].alternatives", paths)

    def test_design_preflight_closes_split_sensitivity_criteria_through_audit(
        self,
    ) -> None:
        req = request("unit_split_sensitivity_preflight")
        candidate = design(req)
        candidate["measurement_plan"] = [
            {
                "name": "mae_cal_full",
                "display_name": "保留标记观测拟合后的平均绝对误差",
                "role": "primary",
                "unit": "G",
                "scientific_meaning": "保留标记观测拟合后在固定留出观测上的平均绝对误差。",
            },
            {
                "name": "mae_cal_excluded",
                "display_name": "排除标记观测拟合后的平均绝对误差",
                "role": "primary",
                "unit": "G",
                "scientific_meaning": "排除标记观测拟合后在同一留出观测上的平均绝对误差。",
            },
            {
                "name": "sensitivity_delta",
                "display_name": "两种拟合条件的平均绝对误差差值",
                "role": "secondary",
                "unit": "G",
                "scientific_meaning": "保留条件减去排除条件的平均绝对误差。",
            },
        ]
        candidate["criteria"] = [
            {
                **candidate["criteria"][0],
                "id": "condition_estimates",
                "statement": "分别报告两种拟合条件的平均绝对误差。",
                "measurement_refs": ["mae_cal_full", "mae_cal_excluded"],
            },
            {
                **candidate["criteria"][0],
                "id": "sensitivity_difference",
                "statement": "报告敏感性分析中两种拟合条件的差值。",
                "measurement_refs": ["sensitivity_delta"],
            },
        ]
        candidate["paired_comparison_audits"] = [
            {
                "id": "fit_sensitivity",
                "comparison_kind": "candidate_vs_candidate",
                "baseline_measurement": "mae_cal_full",
                "candidate_measurement": "mae_cal_excluded",
                "delta_measurement": "sensitivity_delta",
                "delta_formula": "baseline_minus_candidate",
                "metric": "mae",
                "baseline_model_input_columns": ["candidate_reading"],
            }
        ]
        candidate["experiment_stages"][0]["measurement_refs"] = [
            "mae_cal_full",
            "mae_cal_excluded",
            "sensitivity_delta",
        ]
        candidate["experiment_stages"][0]["criterion_refs"] = [
            "condition_estimates",
            "sensitivity_difference",
        ]

        issues = service._design_schema_issues(candidate)
        messages = {row["message"] for row in issues}
        self.assertNotIn(
            "fitted-condition sensitivity lacks a same-row candidate comparison",
            messages,
        )

    def test_design_preflight_rejects_hard_coded_scope_count_and_misnamed_bias(
        self,
    ) -> None:
        req = request("unit_calibration_wording_preflight")
        candidate = design(req)
        candidate["measurement_plan"].append(
            {
                "name": "mse_bias",
                "display_name": "MSE 偏差",
                "role": "diagnostic",
                "unit": "G",
                "scientific_meaning": "评价观测上的平均有符号误差。",
            }
        )
        candidate["paired_comparison_audits"] = [
            {
                "id": "bad_scope",
                "evaluation_scope": "排除标记观测后的 5 行评估集",
            }
        ]

        issues = service._design_schema_issues(candidate)
        paths = {row["field_path"] for row in issues}
        self.assertIn(
            "design.paired_comparison_audits[0].evaluation_scope",
            paths,
        )
        self.assertIn("design.measurement_plan[1].name", paths)

    def test_verification_preview_returns_exact_assessment_contract(self) -> None:
        req = request("unit_preview_assessment_contract")
        run_id, attempt_id = create_ready_run(req)
        self.addCleanup(cleanup_run, run_id)
        service.execute(run_id, attempt_id)

        preview = service.verify(run_id, attempt_id, None)
        guide = preview["assessment_authoring_guide"]
        contract = guide["scientific_assessment_contract"]
        self.assertEqual(
            contract["allowed_proposed_outcomes"],
            [
                "completed_interpretable",
                "partial_result",
                "scientific_null",
                "high_uncertainty",
            ],
        )
        self.assertEqual(
            contract["criterion_result_exact_fields"],
            ["criterion_id", "status", "explanation"],
        )
        self.assertIn("never evidence_summary", contract["criterion_id_rule"])
        self.assertIn(
            "基本消除偏差",
            guide["forbidden_or_conditioned_phrasing"],
        )

    def test_single_total_attempt_can_execute_after_prepare(self) -> None:
        req = request("unit_single_total_attempt", max_attempts=1)
        req["run_budget"]["max_total_attempts"] = 1
        run_id, attempt_id = create_ready_run(req)
        self.addCleanup(cleanup_run, run_id)

        _, prepared_state = load_state(run_id)
        self.assertEqual(prepared_state["remaining_attempts"], 0)

        executed = service.execute(run_id, attempt_id)

        self.assertEqual(executed["status"], "execution_finished")
        self.assertEqual(executed["execution_facts"]["sandbox_exit_code"], 0)

    def test_service_rebinds_trusted_response_task_fields(self) -> None:
        req = request(
            "unit_trusted_task_rebind",
            task="Integrate a long upstream research handoff without silently changing its scope.",
        )
        run_id = service.bind_request({"request": req})["run_id"]
        self.addCleanup(cleanup_run, run_id)
        service.inspect_inputs(run_id)
        candidate_response = response(req)
        candidate_response["task_name"] = "model_rephrased_name"
        candidate_response["task"] = "Shortened model paraphrase."
        del candidate_response["normalized_task"]
        checked = service.validate_and_store_design(
            run_id,
            candidate_response,
            design(req),
        )
        self.assertEqual(checked["status"], "design_validated")
        single_file_guide = checked["stage_authoring_guide"]["input_paths"][
            "single_file"
        ]
        self.assertIn("exact file Path", single_file_guide)
        self.assertIn("never append the filename", single_file_guide)
        required = checked["required_worker_outputs"]
        self.assertTrue(required["execution_completed"])
        self.assertEqual(required["measurement_names"], ["mean"])
        self.assertEqual(required["result_item_ids"], [])
        self.assertEqual(required["endpoint_ids"], ["mean_endpoint"])
        self.assertEqual(required["artifact_paths"], ["summary.json"])
        self.assertEqual(required["json_artifact_paths"], ["summary.json"])
        self.assertEqual(
            required["json_traceability"]["exact_value_keys"],
            ["mean"],
        )
        self.assertIn("any nesting level", required["json_traceability"]["rule"])
        self.assertIn("local constant", required["source_artifact_rule"])
        root, _ = load_state(run_id)
        stored_response = json.loads(
            (root / "response.json").read_text(encoding="utf-8")
        )
        self.assertEqual(stored_response["task_name"], req["task_name"])
        self.assertEqual(stored_response["task"], req["task"])
        self.assertEqual(stored_response["normalized_task"], req["task"])

    def test_reinspect_validated_run_returns_current_worker_contract(self) -> None:
        req = request("unit_reinspect_worker_contract")
        run_id = service.bind_request({"request": req})["run_id"]
        self.addCleanup(cleanup_run, run_id)
        service.inspect_inputs(run_id)
        checked = service.validate_and_store_design(
            run_id,
            response(req),
            design(req),
        )

        refreshed = service.inspect_inputs(run_id)

        self.assertEqual(refreshed["status"], "already_snapshotted")
        self.assertEqual(
            refreshed["current_stage_id"],
            checked["current_stage_id"],
        )
        self.assertEqual(
            refreshed["required_worker_outputs"],
            checked["required_worker_outputs"],
        )
        self.assertGreater(refreshed["remaining_run_seconds"], 0)

    def test_prepare_budget_exhaustion_persists_terminal_report(self) -> None:
        req = request("unit_prepare_budget_terminal")
        run_id = service.bind_request({"request": req})["run_id"]
        self.addCleanup(cleanup_run, run_id)
        service.inspect_inputs(run_id)
        service.validate_and_store_design(run_id, response(req), design(req))
        root, state = load_state(run_id)
        state["created_at"] = "2000-01-01T00:00:00Z"
        state["execution_budget_reset_at"] = state["created_at"]
        save_state(root, state)

        with self.assertRaisesRegex(service.ServiceError, "预算已用尽"):
            service.prepare(
                run_id,
                [{"path": "experiment.py", "content": SUCCESS_CODE}],
                None,
                "Initial reviewed implementation.",
            )

        _, stopped = load_state(run_id)
        self.assertEqual(stopped["outcome"], "budget_stopped")
        finalized = service.finalize(run_id)
        self.assertEqual(finalized["outcome"], "budget_stopped")
        self.assertTrue((root / "record.json").is_file())
        self.assertTrue((root / "report.md").is_file())

    def test_research_report_localizes_upstream_roles_and_endpoint_semantics(
        self,
    ) -> None:
        self.assertEqual(
            _input_role_label("upstream_research_plan"),
            "研究规划反馈",
        )
        self.assertEqual(
            _input_role_label("upstream_data_feature_feedback"),
            "数据与特征反馈",
        )
        self.assertEqual(ENDPOINT_STATUS_LABELS["failed"], "端点计算失败")
        self.assertEqual(
            _analysis_mode_label("time_ordered_holdout_calibration"),
            "时间顺序留出校准评估",
        )
        self.assertEqual(
            _analysis_mode_label("time_ordered_holdout_evaluation"),
            "时间顺序留出评估",
        )
        self.assertEqual(
            _analysis_mode_label("time_ordered_split_calibration"),
            "时间顺序校准与留出评估",
        )
        self.assertEqual(
            _analysis_mode_label("bounded_exploratory"),
            "有界探索性分析",
        )
        self.assertEqual(
            _analysis_mode_label("method_comparison_with_calibration"),
            "校准方法比较与时间留出评估",
        )
        self.assertEqual(
            _input_role_label("paired_measurement_data"),
            "处理后配对数据",
        )
        self.assertEqual(
            _input_role_label("upstream research plan guidance"),
            "研究规划反馈",
        )
        self.assertEqual(
            _analysis_mode_label("descriptive_and_calibrative"),
            "描述性校准比较",
        )
        self.assertEqual(
            _analysis_mode_label("descriptive_with_calibration"),
            "描述性校准与时间留出评估",
        )
        self.assertEqual(
            _analysis_mode_label("descriptive comparison with time ordered holdout"),
            "描述性比较与时间顺序留出评估",
        )
        self.assertEqual(
            _analysis_mode_label("custom_research_protocol"),
            "任务定制分析",
        )

    def test_attempt_audit_distinguishes_execution_from_verification_failure(
        self,
    ) -> None:
        lines = _attempt_lines(
            {
                "design_sha256": "same-design",
                "attempt_history": [
                    {
                        "parent_attempt": None,
                        "design_sha256": "same-design",
                        "execution_summary": {
                            "wall_seconds": 1.25,
                            "windows_process_exit_code": 0,
                            "sandbox_exit_code": 0,
                        },
                        "verification_outcome": "technical_failure",
                        "change_reason": "初始实现。",
                        "code_changes": [],
                    }
                ],
            }
        )
        self.assertIn("执行完成，结果核验未通过", "\n".join(lines))

    def test_attempt_audit_sanitizes_internal_worker_terms(self) -> None:
        lines = _attempt_lines(
            {
                "design_sha256": "same-design",
                "attempt_history": [
                    {
                        "parent_attempt": None,
                        "design_sha256": "same-design",
                        "execution_summary": None,
                        "verification_outcome": None,
                        "change_reason": "修复 worker_result 与 paired_comparison_audits。",
                        "code_changes": [],
                    }
                ],
            }
        )
        text = "\n".join(lines)
        self.assertNotIn("worker", text)
        self.assertNotIn("paired_comparison_audits", text)
        self.assertIn("机器核验结果", text)

    def test_explicit_natural_input_runs_in_real_sandbox(self) -> None:
        task = "读取 inputs/example_mean.csv，计算全部 value 的均值并保存 JSON 结果。"
        req = default_request(task)
        req["task_name"] = "unit_natural_input"
        run_id = service.bind_request({"request": req})["run_id"]
        self.addCleanup(cleanup_run, run_id)
        inspection = service.inspect_inputs(run_id)
        file_row = inspection["input_snapshot"]["inputs"][0]["files"][0]
        self.assertEqual(file_row["path"], "input_01/example_mean.csv")
        self.assertEqual(file_row["profile"]["columns"], ["group", "value"])
        self.assertEqual(file_row["profile"]["row_count"], 4)
        self.assertEqual(
            file_row["profile"]["missing_value_counts"],
            {"group": 0, "value": 0},
        )
        candidate = design(req)
        candidate_response = response(req)
        candidate_response["design_summary"] = (
            "对已快照的示例表执行确定性总体均值计算。"
        )
        candidate["design_summary"] = candidate_response["design_summary"]
        candidate["research_frame"]["primary_question"] = (
            "示例表全部 value 记录的算术平均值是多少？"
        )
        candidate["research_frame"]["claim_scope"] = (
            "结果只描述当前已快照的四条演示记录。"
        )
        candidate["research_frame"]["input_evidence"][0].update(
            {
                "role": "已核验的实验输入",
                "intended_use": "提供确定性均值计算所需的 value 数值。",
                "limitations": "该演示输入不支持超出当前四条记录的统计推断。",
            }
        )
        candidate["research_frame"]["supported_questions"] = [
            "计算并核验当前记录的算术平均值。"
        ]
        candidate["research_frame"]["assumptions"] = [
            "value 列中的数值均可解析且为有限值。"
        ]
        candidate["research_frame"]["threats_to_validity"] = [
            "演示记录数量很少，不能支持总体推断。"
        ]
        candidate["research_frame"]["literature_basis"] = (
            "该确定性演示计算不需要外部文献依据。"
        )
        candidate["measurement_plan"][0].update(
            {
                "display_name": "当前记录的算术平均值",
                "scientific_meaning": "四条已快照 value 数值的算术平均。",
            }
        )
        candidate["method_decisions"] = [
            {
                "id": "summary_choice",
                "decision_key": "summary_statistic",
                "decision": "不拟合模型，直接计算算术平均值。",
                "rationale": "任务只要求对当前有限记录进行确定性汇总。",
                "basis_kind": "method_standard",
                "source_refs": ["算术平均值定义"],
                "alternatives": ["使用中位数作为不同估计目标"],
                "claim_limit": "不据此描述总体分布。",
            },
        ]
        candidate["criteria"][0]["statement"] = "报告算术平均值并完成预设计算端点。"
        candidate["criteria"][0]["basis_text"] = (
            "由实验工作器使用的固定数值重新计算结果。"
        )
        candidate["interpretation_policy"] = {
            "primary_estimand": "当前四条记录的算术平均值",
            "null_rule": "没有区间及等效性或灵敏度依据时不作科学空结果声明。",
            "uncertainty_rule": "高不确定性必须给出明确的数据或方法原因。",
            "partial_rule": "部分结果必须同时存在已完成与未完成端点。",
        }
        candidate["experiment_stages"][0]["execution"]["dependencies"] = []
        candidate["artifact_plan"][0]["path"] = "mean.json"
        candidate["experiment_stages"][0]["execution"]["expected_artifacts"] = [
            "mean.json"
        ]
        candidate["criteria"][0]["artifact_refs"] = ["mean.json"]
        service.validate_and_store_design(run_id, candidate_response, candidate)
        chinese_mean_code = INPUT_MEAN_CODE.replace(
            '"primary_estimand": "arithmetic mean"',
            '"primary_estimand": "当前四条记录的算术平均值"',
        )
        attempt_id = service.prepare(
            run_id,
            [{"path": "experiment.py", "content": chinese_mean_code}],
            None,
            "Read the verified input through context input_files.",
        )["attempt_id"]
        executed = service.execute(run_id, attempt_id)
        self.assertEqual(executed["execution_facts"]["sandbox_exit_code"], 0)
        self.assertEqual(executed["diagnostic"]["stderr_excerpt"], "")
        preview = service.verify(run_id, attempt_id, None)
        self.assertEqual(preview["status"], "assessment_required")
        self.assertEqual(
            preview["trusted_worker_result"]["measurements"][0]["value"],
            2.5,
        )
        chinese_assessment = assessment()
        chinese_assessment["rationale"] = "已核验的确定性计算成功完成。"
        chinese_assessment["criterion_results"][0]["explanation"] = (
            "均值测量已经产生，且预设计算端点已完成。"
        )
        chinese_assessment["report_narrative"] = {
            "title": "示例数据总体均值计算报告",
            "objective": "计算输入文件中全部 value 记录的算术平均值。",
            "data_scope": "本次只使用已快照的四条记录，不进行 group 分组比较。",
            "method": "读取 value 列，将四个数值求和后除以实际记录数，并核验 JSON 产物。",
            "interpretation": "总体均值为 2.5，共纳入四条记录；该结果只描述当前演示数据。",
            "evidence_strength": "证据足以支持这四条记录的精确算术结果，但不支持统计推断。",
            "claim_boundary": "本次结果不能用于组间差异、总体均值、因果或预测主张。",
            "limitations": ["该示例不支持组间差异、显著性或更大总体的推断。"],
            "next_steps": ["如需比较 A、B 两组，应另行提出分组统计目标。"],
        }
        verified = service.verify(run_id, attempt_id, chinese_assessment)
        self.assertEqual(verified["outcome"], "completed_interpretable")
        entry = service.finalize(run_id)
        self.assertEqual(entry["outcome"], "completed_interpretable")
        self.assertIn(
            "共 4 条观测，记录 2 个变量，未发现空值",
            entry["user_display_markdown"],
        )
        self.assertNotIn("4 行", entry["user_display_markdown"])
        self.assertNotIn("2 列", entry["user_display_markdown"])
        root, _ = load_state(run_id)
        result = json.loads(
            (root / "public" / "stages" / "stage_summary" / "mean.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(result, {"mean": 2.5, "count": 4})

    def test_upstream_text_feedback_is_available_as_bounded_preview(self) -> None:
        task = "读取 inputs/README.md，结合其中的输入说明设计后续实验。"
        req = default_request(task)
        req["task_name"] = "unit_upstream_preview"
        run_id = service.bind_request({"request": req})["run_id"]
        self.addCleanup(cleanup_run, run_id)
        inspection = service.inspect_inputs(run_id)
        previews = inspection["input_previews"]
        self.assertEqual(len(previews), 1)
        self.assertEqual(previews[0]["path"], "input_01/README.md")
        self.assertIn("真实任务应提供自己的输入", previews[0]["content"])
        self.assertFalse(previews[0]["truncated"])

    def test_small_tabular_input_is_available_for_scientific_design(self) -> None:
        task = (
            "读取 inputs/upstream_handoff_demo/polar_overlap_features.csv，"
            "根据质量标记设计敏感性分析。"
        )
        req = default_request(task)
        req["task_name"] = "unit_tabular_preview"
        run_id = service.bind_request({"request": req})["run_id"]
        self.addCleanup(cleanup_run, run_id)
        inspection = service.inspect_inputs(run_id)
        previews = inspection["input_previews"]
        self.assertEqual(len(previews), 1)
        self.assertEqual(
            previews[0]["path"],
            "input_01/polar_overlap_features.csv",
        )
        self.assertIn(
            "2014-06,declining,0.40,1.36,suspect_geometry", previews[0]["content"]
        )
        self.assertIn("2025-07,maximum,2.30,3.14,ok", previews[0]["content"])
        self.assertFalse(previews[0]["truncated"])

    def test_unreviewed_dependency_is_rejected_before_design_is_locked(self) -> None:
        req = request("unit_dependency_preflight")
        run_id = service.bind_request({"request": req})["run_id"]
        self.addCleanup(cleanup_run, run_id)
        service.inspect_inputs(run_id)
        candidate = design(req)
        candidate["experiment_stages"][0]["execution"]["dependencies"] = [
            "not_a_reviewed_package"
        ]
        with self.assertRaisesRegex(ValueError, "unavailable or unreviewed"):
            service.validate_and_store_design(run_id, response(req), candidate)
        root, state = load_state(run_id)
        self.assertEqual(state["phase"], "inputs_snapshotted")
        self.assertEqual(state["attempt_count"], 0)
        self.assertEqual(
            state["remaining_attempts"],
            req["run_budget"]["max_total_attempts"],
        )
        self.assertFalse((root / "design.json").exists())
        self.assertEqual(list((root / "attempts").iterdir()), [])

    def test_input_blocker_is_not_misclassified_as_method_mismatch(self) -> None:
        req = request("unit_input_classification")
        run_id = service.bind_request({"request": req})["run_id"]
        self.addCleanup(cleanup_run, run_id)
        service.inspect_inputs(run_id)
        blocked = response(req, "execution_blocked")
        blocked["blockers"] = [
            "required_input_file_missing: a required data file was not supplied"
        ]
        blocked["method_fit"] = "incompatible"
        result = service.validate_and_store_design(run_id, blocked, None)
        self.assertEqual(result["outcome"], "input_missing")

    def test_missing_input_claim_cannot_override_verified_snapshot(self) -> None:
        req = request(
            "unit_input_contradiction",
            input_refs=[
                {
                    "id": "example",
                    "path": "inputs/example_mean.csv",
                    "description": "Verified example CSV.",
                    "required": True,
                }
            ],
        )
        run_id = service.bind_request({"request": req})["run_id"]
        self.addCleanup(cleanup_run, run_id)
        inspection = service.inspect_inputs(run_id)
        self.assertEqual(inspection["input_snapshot"]["missing_required_ids"], [])
        blocked = response(req, "execution_blocked")
        blocked["blockers"] = [
            "required_input_file_missing: inputs/example_mean.csv was not found"
        ]
        blocked["method_fit"] = "incompatible"
        with self.assertRaisesRegex(
            RuntimeError, "contradicts the verified input snapshot"
        ):
            service.validate_and_store_design(run_id, blocked, None)

    def test_gpu_request_gets_formal_boundary_report(self) -> None:
        req = request("unit_gpu_boundary")
        req["resource_budget"]["gpu_count"] = 1
        req["resource_budget"]["gpu_memory_mb"] = 4096
        run_id = service.bind_request({"request": req})["run_id"]
        self.addCleanup(cleanup_run, run_id)
        service.inspect_inputs(run_id)
        result = service.validate_and_store_design(run_id, response(req), design(req))
        self.assertEqual(result["outcome"], "boundary_blocked")
        entry = service.finalize(run_id)
        self.assertEqual(entry["outcome"], "boundary_blocked")
        self.assertIn("超出当前可安全开展的实验范围", entry["user_display_markdown"])
        self.assertIn("当前没有形成可报告的测量结果", entry["user_display_markdown"])

    def test_input_policy_violation_gets_formal_boundary_report(self) -> None:
        req = request(
            "unit_path_boundary",
            input_refs=[
                {
                    "id": "outside",
                    "path": "../README.md",
                    "description": "Disallowed traversal fixture.",
                    "required": True,
                }
            ],
        )
        run_id = service.bind_request({"request": req})["run_id"]
        self.addCleanup(cleanup_run, run_id)
        inspection = service.inspect_inputs(run_id)
        self.assertEqual(inspection["outcome"], "boundary_blocked")
        blocked = response(req, "execution_blocked")
        result = service.validate_and_store_design(run_id, blocked, None)
        self.assertEqual(result["outcome"], "boundary_blocked")
        entry = service.finalize(run_id)
        self.assertEqual(entry["outcome"], "boundary_blocked")

    def test_successful_real_sandbox_flow(self) -> None:
        req = request("unit_success")
        run_id, attempt_id = create_ready_run(req)
        self.addCleanup(cleanup_run, run_id)
        facts = service.execute(run_id, attempt_id)["execution_facts"]
        self.assertEqual(facts["sandbox_exit_code"], 0)
        self.assertIsNone(facts["stop_reason"])
        self.assertTrue(facts["runtime_environment"]["ready"])
        self.assertEqual(facts["runtime_environment"]["packages"]["numpy"], "2.4.6")
        self.assertTrue(_sandbox_isolation_passed(facts["sandbox_policy"]))
        preview = service.verify(run_id, attempt_id, None)
        self.assertEqual(preview["status"], "assessment_required")
        self.assertEqual(preview["execution_summary"]["sandbox_exit_code"], 0)
        self.assertEqual(
            preview["criterion_evidence"][0]["measurements"][0]["name"],
            "mean",
        )
        self.assertEqual(
            preview["verified_artifact_evidence"][0]["parsed_json"]["mean"],
            2.5,
        )
        verified = service.verify(run_id, attempt_id, assessment())
        self.assertEqual(verified["outcome"], "completed_interpretable")
        entry = service.finalize(run_id)
        self.assertEqual(entry["outcome"], "completed_interpretable")
        self.assertIn("| Arithmetic mean | 2.5 |", entry["user_display_markdown"])
        self.assertNotIn("本次已实际执行计算", entry["user_display_markdown"])
        self.assertIn(
            "实际执行事实",
            (runs_root() / run_id / "audit.md").read_text(encoding="utf-8"),
        )

    def test_technical_failure_remains_repairable(self) -> None:
        req = request("unit_failure", max_attempts=2)
        run_id = service.bind_request({"request": req})["run_id"]
        self.addCleanup(cleanup_run, run_id)
        service.inspect_inputs(run_id)
        service.validate_and_store_design(run_id, response(req), design(req))
        attempt_id = service.prepare(
            run_id,
            [{"path": "experiment.py", "content": FAIL_CODE}],
            None,
            "Deliberate failure fixture.",
        )["attempt_id"]
        executed = service.execute(run_id, attempt_id)
        self.assertIn(
            "deliberate technical failure",
            executed["diagnostic"]["stderr_excerpt"],
        )
        verified = service.verify(run_id, attempt_id, None)
        self.assertEqual(verified["outcome"], "technical_failure")
        self.assertTrue(verified["repair_allowed"])

    def test_finalize_cannot_reuse_an_older_attempt_record(self) -> None:
        req = request("unit_stale_finalize", max_attempts=2)
        run_id = service.bind_request({"request": req})["run_id"]
        self.addCleanup(cleanup_run, run_id)
        service.inspect_inputs(run_id)
        service.validate_and_store_design(run_id, response(req), design(req))
        first_attempt = service.prepare(
            run_id,
            [{"path": "experiment.py", "content": FAIL_CODE}],
            None,
            "Create a verified technical failure record.",
        )["attempt_id"]
        service.execute(run_id, first_attempt)
        service.verify(run_id, first_attempt, None)
        second_attempt = service.prepare(
            run_id,
            [{"path": "experiment.py", "content": SUCCESS_CODE}],
            first_attempt,
            "Repair the technical failure.",
        )["attempt_id"]
        service.execute(run_id, second_attempt)
        with self.assertRaisesRegex(RuntimeError, "has not been verified"):
            service.finalize(run_id)
        verified = service.verify(run_id, second_attempt, assessment())
        self.assertEqual(verified["outcome"], "completed_interpretable")
        entry = service.finalize(run_id)
        self.assertEqual(entry["outcome"], "completed_interpretable")
        root, state = load_state(run_id)
        record = json.loads((root / "record.json").read_text(encoding="utf-8"))
        self.assertEqual(record["attempt"]["attempt_id"], second_attempt)
        self.assertEqual(len(record["attempt_history"]), 2)
        self.assertEqual(
            {row["design_sha256"] for row in record["attempt_history"]},
            {record["design_sha256"]},
        )
        self.assertEqual(
            record["attempt_history"][0]["verification_outcome"],
            "technical_failure",
        )
        self.assertEqual(
            record["attempt_history"][1]["verification_outcome"],
            "completed_interpretable",
        )
        self.assertTrue(record["attempt_history"][1]["code_changes"])
        audit = (runs_root() / run_id / "audit.md").read_text(encoding="utf-8")
        self.assertIn("不可变尝试关系", audit)
        self.assertNotIn(second_attempt, entry["user_display_markdown"])
        self.assertEqual(record["record_sha256"], state["verified_record_sha256"])

    def test_missing_declared_artifact_becomes_repairable_failure(self) -> None:
        req = request("unit_missing_artifact", max_attempts=2)
        run_id = service.bind_request({"request": req})["run_id"]
        self.addCleanup(cleanup_run, run_id)
        service.inspect_inputs(run_id)
        service.validate_and_store_design(run_id, response(req), design(req))
        mismatched_code = SUCCESS_CODE.replace(
            'context["output_dir"] / "summary.json"',
            'context["output_dir"] / "actual.json"',
            1,
        )
        attempt_id = service.prepare(
            run_id,
            [{"path": "experiment.py", "content": mismatched_code}],
            None,
            "Deliberately mismatch the written and declared artifact.",
        )["attempt_id"]
        service.execute(run_id, attempt_id)
        verified = service.verify(run_id, attempt_id, assessment())
        self.assertEqual(verified["outcome"], "technical_failure")
        self.assertTrue(verified["repair_allowed"])
        self.assertIn("missing=['summary.json']", verified["outcome_reason"])

    def test_undeclared_output_becomes_repairable_failure(self) -> None:
        req = request("unit_undeclared_output", max_attempts=2)
        run_id = service.bind_request({"request": req})["run_id"]
        self.addCleanup(cleanup_run, run_id)
        service.inspect_inputs(run_id)
        service.validate_and_store_design(run_id, response(req), design(req))
        code_with_extra_output = SUCCESS_CODE.replace(
            'artifact.write_text(json.dumps({"mean": mean}), encoding="utf-8")',
            (
                'artifact.write_text(json.dumps({"mean": mean}), encoding="utf-8")\n'
                '    (context["output_dir"] / "worker_result.json").write_text('
                '"duplicate", encoding="utf-8")'
            ),
            1,
        )
        attempt_id = service.prepare(
            run_id,
            [{"path": "experiment.py", "content": code_with_extra_output}],
            None,
            "Deliberately write an undeclared duplicate output.",
        )["attempt_id"]
        service.execute(run_id, attempt_id)
        verified = service.verify(run_id, attempt_id, None)
        self.assertEqual(verified["outcome"], "technical_failure")
        self.assertTrue(verified["repair_allowed"])
        self.assertIn(
            "outputs_not_declared=['worker_result.json']",
            verified["outcome_reason"],
        )

    def test_post_execution_output_mutation_is_boundary_blocked(self) -> None:
        req = request("unit_post_execution_mutation", max_attempts=2)
        run_id = service.bind_request({"request": req})["run_id"]
        self.addCleanup(cleanup_run, run_id)
        service.inspect_inputs(run_id)
        service.validate_and_store_design(run_id, response(req), design(req))
        attempt_id = service.prepare(
            run_id,
            [{"path": "experiment.py", "content": SUCCESS_CODE}],
            None,
            "Create one immutable successful attempt.",
        )["attempt_id"]
        service.execute(run_id, attempt_id)
        output_root = runs_root() / run_id / "attempts" / attempt_id / "output"
        (output_root / "injected_after_execution.json").write_text(
            "{}", encoding="utf-8"
        )

        verified = service.verify(run_id, attempt_id, None)

        self.assertEqual(verified["outcome"], "boundary_blocked")
        self.assertFalse(verified["repair_allowed"])
        self.assertIn("outputs changed", verified["outcome_reason"])

    def test_measurement_must_match_same_named_json_artifact_field(self) -> None:
        req = request("unit_artifact_traceability", max_attempts=2)
        run_id = service.bind_request({"request": req})["run_id"]
        self.addCleanup(cleanup_run, run_id)
        service.inspect_inputs(run_id)
        service.validate_and_store_design(run_id, response(req), design(req))
        mismatched_code = SUCCESS_CODE.replace(
            'json.dumps({"mean": mean})',
            'json.dumps({"different_name": mean})',
            1,
        )
        attempt_id = service.prepare(
            run_id,
            [{"path": "experiment.py", "content": mismatched_code}],
            None,
            "Deliberately break numeric artifact traceability.",
        )["attempt_id"]
        service.execute(run_id, attempt_id)
        verified = service.verify(run_id, attempt_id, None)
        self.assertEqual(verified["outcome"], "technical_failure")
        self.assertTrue(verified["repair_allowed"])
        self.assertIn("not traceable", verified["outcome_reason"])
        self.assertIn("nesting is allowed", verified["outcome_reason"])
        self.assertIn("mean", verified["outcome_reason"])

    def test_exact_measurement_key_may_keep_a_natural_json_group(self) -> None:
        req = request("unit_nested_artifact_traceability", max_attempts=2)
        run_id = service.bind_request({"request": req})["run_id"]
        self.addCleanup(cleanup_run, run_id)
        service.inspect_inputs(run_id)
        service.validate_and_store_design(run_id, response(req), design(req))
        nested_code = SUCCESS_CODE.replace(
            'json.dumps({"mean": mean})',
            'json.dumps({"descriptive_statistics": {"mean": mean}})',
            1,
        )
        attempt_id = service.prepare(
            run_id,
            [{"path": "experiment.py", "content": nested_code}],
            None,
            "Keep the exact measurement key inside a scientific JSON group.",
        )["attempt_id"]
        service.execute(run_id, attempt_id)
        verified = service.verify(run_id, attempt_id, None)
        self.assertEqual(verified["status"], "assessment_required")
        self.assertNotEqual(verified.get("outcome"), "technical_failure")

    def test_unrequested_p_value_hidden_in_json_artifact_is_repairable(self) -> None:
        req = request(
            "unit_unrequested_p_value_artifact",
            task="Compute only the descriptive mean of the current data.",
            max_attempts=2,
        )
        run_id = service.bind_request({"request": req})["run_id"]
        self.addCleanup(cleanup_run, run_id)
        service.inspect_inputs(run_id)
        service.validate_and_store_design(run_id, response(req), design(req))
        code_with_hidden_p_value = SUCCESS_CODE.replace(
            'json.dumps({"mean": mean})',
            'json.dumps({"mean": mean, "p_value_diagnostic": 0.01})',
            1,
        )
        attempt_id = service.prepare(
            run_id,
            [{"path": "experiment.py", "content": code_with_hidden_p_value}],
            None,
            "Deliberately emit an unrequested p-value in a JSON artifact.",
        )["attempt_id"]
        service.execute(run_id, attempt_id)
        verified = service.verify(run_id, attempt_id, None)
        self.assertEqual(verified["outcome"], "technical_failure")
        self.assertTrue(verified["repair_allowed"])
        self.assertIn("unrequested p-value fields", verified["outcome_reason"])

    def test_improvement_triplet_cannot_mix_comparison_scopes(self) -> None:
        worker_result = {
            "measurements": [
                {
                    "name": "raw_mae",
                    "value": 0.512778,
                    "unit": "G",
                },
                {
                    "name": "calibrated_mae",
                    "value": 0.043993,
                    "unit": "G",
                },
                {
                    "name": "mae_improvement",
                    "value": 0.581007,
                    "unit": "G",
                },
            ],
            "scientific_payload": {
                "primary_estimand": "原始 MAE 减去校准 MAE",
            },
        }
        errors = _comparison_consistency_errors(worker_result)
        self.assertEqual(len(errors), 1)
        self.assertIn("different populations or splits", errors[0])

    def test_split_specific_improvement_triplet_is_consistent(self) -> None:
        worker_result = {
            "measurements": [
                {
                    "name": "holdout_raw_mae",
                    "value": 0.625,
                    "unit": "G",
                },
                {
                    "name": "holdout_calibrated_mae",
                    "value": 0.043993,
                    "unit": "G",
                },
                {
                    "name": "holdout_mae_improvement",
                    "value": 0.581007,
                    "unit": "G",
                },
            ],
            "scientific_payload": {
                "primary_estimand": "留出集原始 raw MAE 减去 calibrated MAE",
            },
        }
        self.assertEqual(_comparison_consistency_errors(worker_result), [])

    def test_paired_recomputation_accepts_six_decimal_rounding_only(self) -> None:
        self.assertTrue(_close_measurement(0.652444, 0.652444122767))
        self.assertTrue(_close_measurement(0.048673, 0.0486724109258))
        self.assertFalse(_close_measurement(0.048674, 0.0486724109258))
        self.assertFalse(_close_measurement(0.65244, 0.652444122767))

    def test_declared_candidate_minus_baseline_direction_is_respected(self) -> None:
        worker_result = {
            "measurements": [
                {
                    "name": "holdout_raw_mae",
                    "value": 0.625,
                    "unit": "G",
                },
                {
                    "name": "holdout_calibrated_mae",
                    "value": 0.043993,
                    "unit": "G",
                },
                {
                    "name": "holdout_mae_improvement",
                    "value": -0.581007,
                    "unit": "G",
                },
            ],
            "scientific_payload": {
                "primary_estimand": "留出集校准后 MAE 减去原始 MAE",
            },
        }
        design = {
            "paired_comparison_audits": [
                {
                    "baseline_measurement": "holdout_raw_mae",
                    "candidate_measurement": "holdout_calibrated_mae",
                    "delta_measurement": "holdout_mae_improvement",
                    "delta_formula": "candidate_minus_baseline",
                }
            ]
        }
        self.assertEqual(_comparison_consistency_errors(worker_result, design), [])

    def test_declared_candidate_minus_baseline_rejects_opposite_sign(self) -> None:
        worker_result = {
            "measurements": [
                {"name": "holdout_raw_mae", "value": 0.625, "unit": "G"},
                {
                    "name": "holdout_calibrated_mae",
                    "value": 0.043993,
                    "unit": "G",
                },
                {
                    "name": "holdout_mae_improvement",
                    "value": 0.581007,
                    "unit": "G",
                },
            ],
            "scientific_payload": {
                "primary_estimand": "留出集校准后 MAE 减去原始 MAE",
            },
        }
        design = {
            "paired_comparison_audits": [
                {
                    "baseline_measurement": "holdout_raw_mae",
                    "candidate_measurement": "holdout_calibrated_mae",
                    "delta_measurement": "holdout_mae_improvement",
                    "delta_formula": "candidate_minus_baseline",
                }
            ]
        }
        errors = _comparison_consistency_errors(worker_result, design)
        self.assertEqual(len(errors), 1)
        self.assertIn(
            "holdout_calibrated_mae - holdout_raw_mae",
            errors[0],
        )

    def test_unit_suffix_improvement_triplet_is_checked(self) -> None:
        worker_result = {
            "measurements": [
                {
                    "name": "holdout_raw_mae_g",
                    "value": 0.625,
                    "unit": "G",
                },
                {
                    "name": "holdout_calibrated_mae_g",
                    "value": 0.043993,
                    "unit": "G",
                },
                {
                    "name": "holdout_mae_improvement_g",
                    "value": 0.468785,
                    "unit": "G",
                },
            ],
            "scientific_payload": {
                "primary_estimand": "留出集校准改善量",
            },
        }
        errors = _comparison_consistency_errors(worker_result)
        self.assertEqual(len(errors), 1)
        self.assertIn("holdout_raw_mae_g - holdout_calibrated_mae_g", errors[0])

    def test_improvement_check_allows_reported_precision_rounding(self) -> None:
        worker_result = {
            "measurements": [
                {
                    "name": "calibration_slice_raw_mae_g",
                    "value": 0.512778,
                    "unit": "G",
                },
                {
                    "name": "calibration_slice_calibrated_mae_g",
                    "value": 0.103416,
                    "unit": "G",
                },
                {
                    "name": "calibration_slice_mae_improvement_g",
                    "value": 0.409361,
                    "unit": "G",
                },
            ],
            "scientific_payload": {
                "primary_estimand": "校准切片改善量",
            },
        }
        self.assertEqual(_comparison_consistency_errors(worker_result), [])

    def test_paired_comparison_recomputation_rejects_wrong_scientific_target(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary)
            source_root = run_root / "inputs" / "paired_data"
            output_root = run_root / "attempt-output"
            source_root.mkdir(parents=True)
            output_root.mkdir()
            (source_root / "paired.csv").write_text(
                "date,wso,hmi\na,1,2\nb,2,4\n",
                encoding="utf-8",
            )
            evidence = output_root / "rows.csv"
            evidence.write_text(
                "date,target_hmi,raw_wso,calibrated_hmi\na,2,1,2.1\nb,4,2,3.9\n",
                encoding="utf-8",
            )
            design_payload = {
                "paired_comparison_audits": [
                    {
                        "id": "holdout_calibration",
                        "comparison_kind": "source_baseline_vs_candidate",
                        "evaluation_scope": "fixed holdout rows",
                        "source_input_id": "paired_data",
                        "source_row_id_column": "date",
                        "source_target_column": "hmi",
                        "source_baseline_column": "wso",
                        "candidate_model_input_columns": ["wso"],
                        "candidate_model_target_column": "hmi",
                        "baseline_model_input_columns": [],
                        "baseline_model_target_column": None,
                        "baseline_fit_condition": None,
                        "candidate_fit_condition": "fit on disjoint training rows",
                        "fit_evaluation_relation": "disjoint_rows",
                        "evaluation_target_usage": "metrics_and_evidence_only",
                        "evidence_artifact": "rows.csv",
                        "evidence_row_id_column": "date",
                        "evidence_target_column": "target_hmi",
                        "evidence_baseline_column": "raw_wso",
                        "evidence_candidate_column": "calibrated_hmi",
                        "metric": "mae",
                        "baseline_measurement": "holdout_raw_mae",
                        "candidate_measurement": "holdout_calibrated_mae",
                        "delta_measurement": "holdout_mae_improvement",
                        "delta_formula": "baseline_minus_candidate",
                    }
                ]
            }
            wrong_worker_result = {
                "measurements": [
                    {"name": "holdout_raw_mae", "value": 1.5},
                    {"name": "holdout_calibrated_mae", "value": 2.0},
                    {"name": "holdout_mae_improvement", "value": -0.5},
                ]
            }
            errors, trusted = _paired_comparison_audit_errors(
                run_root,
                design_payload,
                wrong_worker_result,
                {"rows.csv": evidence},
            )
            self.assertEqual(trusted, [])
            self.assertEqual(len(errors), 1)
            self.assertIn("trusted recomputation=0.1", errors[0])

    def test_paired_comparison_recomputation_accepts_aligned_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary)
            source_root = run_root / "inputs" / "paired_data"
            output_root = run_root / "attempt-output"
            source_root.mkdir(parents=True)
            output_root.mkdir()
            (source_root / "paired.csv").write_text(
                "date,wso,hmi\na,1,2\nb,2,4\n",
                encoding="utf-8",
            )
            evidence = output_root / "rows.csv"
            evidence.write_text(
                "date,target_hmi,raw_wso,calibrated_hmi\na,2,1,2.1\nb,4,2,3.9\n",
                encoding="utf-8",
            )
            audit = {
                "id": "holdout_calibration",
                "comparison_kind": "source_baseline_vs_candidate",
                "evaluation_scope": "fixed holdout rows",
                "source_input_id": "paired_data",
                "source_row_id_column": "date",
                "source_target_column": "hmi",
                "source_baseline_column": "wso",
                "candidate_model_input_columns": ["wso"],
                "candidate_model_target_column": "hmi",
                "baseline_model_input_columns": [],
                "baseline_model_target_column": None,
                "baseline_fit_condition": None,
                "candidate_fit_condition": "fit on disjoint training rows",
                "fit_evaluation_relation": "disjoint_rows",
                "evaluation_target_usage": "metrics_and_evidence_only",
                "evidence_artifact": "rows.csv",
                "evidence_row_id_column": "date",
                "evidence_target_column": "target_hmi",
                "evidence_baseline_column": "raw_wso",
                "evidence_candidate_column": "calibrated_hmi",
                "metric": "mae",
                "baseline_measurement": "holdout_raw_mae",
                "candidate_measurement": "holdout_calibrated_mae",
                "delta_measurement": "holdout_mae_improvement",
                "delta_formula": "baseline_minus_candidate",
            }
            worker_result = {
                "measurements": [
                    {"name": "holdout_raw_mae", "value": 1.5},
                    {"name": "holdout_calibrated_mae", "value": 0.10000004},
                    {"name": "holdout_mae_improvement", "value": 1.39999996},
                ]
            }
            errors, trusted = _paired_comparison_audit_errors(
                run_root,
                {"paired_comparison_audits": [audit]},
                worker_result,
                {"rows.csv": evidence},
            )
            self.assertEqual(errors, [])
            self.assertEqual(trusted[0]["row_count"], 2)
            self.assertAlmostEqual(
                trusted[0]["recomputed_measurements"]["holdout_calibrated_mae"],
                0.1,
            )

    def test_paired_comparison_recomputation_skips_blank_candidate_rows(self) -> None:
        # A shared evidence table evaluating several comparisons in disjoint row
        # subsets (phase-split backtest) leaves the candidate column blank for
        # rows that belong to the other comparison.  Recomputation must restrict
        # each audit to its own non-blank candidate rows instead of averaging over
        # the whole table.
        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary)
            source_root = run_root / "inputs" / "paired_data"
            output_root = run_root / "attempt-output"
            source_root.mkdir(parents=True)
            output_root.mkdir()
            (source_root / "paired.csv").write_text(
                "date,wso,hmi\nr1,1,2\nr2,2,4\nl1,10,20\nl2,20,40\n",
                encoding="utf-8",
            )
            evidence = output_root / "rows.csv"
            evidence.write_text(
                "date,target_hmi,raw_wso,fwd_pred,rev_pred\n"
                "r1,2,1,,1.9\n"
                "r2,4,2,,3.8\n"
                "l1,20,10,19.5,\n"
                "l2,40,20,39.5,\n",
                encoding="utf-8",
            )
            base = {
                "comparison_kind": "source_baseline_vs_candidate",
                "source_input_id": "paired_data",
                "source_row_id_column": "date",
                "source_target_column": "hmi",
                "source_baseline_column": "wso",
                "candidate_model_input_columns": ["wso"],
                "candidate_model_target_column": "hmi",
                "baseline_model_input_columns": [],
                "baseline_model_target_column": None,
                "baseline_fit_condition": None,
                "candidate_fit_condition": "fit on disjoint training rows",
                "fit_evaluation_relation": "disjoint_rows",
                "evaluation_target_usage": "metrics_and_evidence_only",
                "evidence_artifact": "rows.csv",
                "evidence_row_id_column": "date",
                "evidence_target_column": "target_hmi",
                "evidence_baseline_column": "raw_wso",
                "metric": "mae",
                "delta_measurement": None,
                "delta_formula": None,
            }
            forward = {
                **base,
                "id": "audit_forward",
                "evaluation_scope": "late rows",
                "evidence_candidate_column": "fwd_pred",
                "baseline_measurement": "uncal_late",
                "candidate_measurement": "fwd_cal",
            }
            reverse = {
                **base,
                "id": "audit_reverse",
                "evaluation_scope": "rising rows",
                "evidence_candidate_column": "rev_pred",
                "baseline_measurement": "uncal_rising",
                "candidate_measurement": "rev_cal",
            }
            worker_result = {
                "measurements": [
                    {"name": "uncal_late", "value": 15.0},
                    {"name": "fwd_cal", "value": 0.5},
                    {"name": "uncal_rising", "value": 1.5},
                    {"name": "rev_cal", "value": 0.15},
                ]
            }
            errors, trusted = _paired_comparison_audit_errors(
                run_root,
                {"paired_comparison_audits": [forward, reverse]},
                worker_result,
                {"rows.csv": evidence},
            )
            self.assertEqual(errors, [])
            by_id = {row["id"]: row for row in trusted}
            self.assertEqual(by_id["audit_forward"]["row_count"], 2)
            self.assertEqual(by_id["audit_reverse"]["row_count"], 2)
            self.assertAlmostEqual(
                by_id["audit_forward"]["recomputed_measurements"]["uncal_late"],
                15.0,
            )
            self.assertAlmostEqual(
                by_id["audit_reverse"]["recomputed_measurements"]["uncal_rising"],
                1.5,
            )

    def test_paired_comparison_recomputation_flags_blank_only_audit(self) -> None:
        # If every candidate cell is blank the audit has no valid rows and must
        # be reported rather than silently recomputed to an empty metric.
        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary)
            source_root = run_root / "inputs" / "paired_data"
            output_root = run_root / "attempt-output"
            source_root.mkdir(parents=True)
            output_root.mkdir()
            (source_root / "paired.csv").write_text(
                "date,wso,hmi\na,1,2\n",
                encoding="utf-8",
            )
            evidence = output_root / "rows.csv"
            evidence.write_text(
                "date,target_hmi,raw_wso,calibrated_hmi\na,2,1,\n",
                encoding="utf-8",
            )
            audit = {
                "id": "holdout_calibration",
                "comparison_kind": "source_baseline_vs_candidate",
                "evaluation_scope": "fixed holdout rows",
                "source_input_id": "paired_data",
                "source_row_id_column": "date",
                "source_target_column": "hmi",
                "source_baseline_column": "wso",
                "candidate_model_input_columns": ["wso"],
                "candidate_model_target_column": "hmi",
                "baseline_model_input_columns": [],
                "baseline_model_target_column": None,
                "baseline_fit_condition": None,
                "candidate_fit_condition": "fit on disjoint training rows",
                "fit_evaluation_relation": "disjoint_rows",
                "evaluation_target_usage": "metrics_and_evidence_only",
                "evidence_artifact": "rows.csv",
                "evidence_row_id_column": "date",
                "evidence_target_column": "target_hmi",
                "evidence_baseline_column": "raw_wso",
                "evidence_candidate_column": "calibrated_hmi",
                "metric": "mae",
                "baseline_measurement": "holdout_raw_mae",
                "candidate_measurement": "holdout_calibrated_mae",
                "delta_measurement": None,
                "delta_formula": None,
            }
            errors, _ = _paired_comparison_audit_errors(
                run_root,
                {"paired_comparison_audits": [audit]},
                {"measurements": []},
                {"rows.csv": evidence},
            )
            self.assertTrue(
                any("no valid rows" in message for message in errors),
                errors,
            )

        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary)
            source_root = run_root / "inputs" / "paired_data"
            output_root = run_root / "attempt-output"
            source_root.mkdir(parents=True)
            output_root.mkdir()
            (source_root / "paired.csv").write_text(
                "date,wso,hmi\na,1,2\nb,2,4\n",
                encoding="utf-8",
            )
            evidence = output_root / "sensitivity.csv"
            evidence.write_text(
                "date,target_hmi,exclude_flag_prediction,include_flag_prediction\n"
                "a,2,2.1,2.2\n"
                "b,4,3.9,3.8\n",
                encoding="utf-8",
            )
            audit = {
                "id": "quality_flag_sensitivity",
                "comparison_kind": "candidate_vs_candidate",
                "evaluation_scope": "same fixed holdout rows under two fit conditions",
                "source_input_id": "paired_data",
                "source_row_id_column": "date",
                "source_target_column": "hmi",
                "source_baseline_column": "wso",
                "candidate_model_input_columns": ["wso"],
                "candidate_model_target_column": "hmi",
                "baseline_model_input_columns": ["wso"],
                "baseline_model_target_column": "hmi",
                "baseline_fit_condition": "exclude flagged fitting row",
                "candidate_fit_condition": "include flagged fitting row",
                "fit_evaluation_relation": "disjoint_rows",
                "evaluation_target_usage": "metrics_and_evidence_only",
                "evidence_artifact": "sensitivity.csv",
                "evidence_row_id_column": "date",
                "evidence_target_column": "target_hmi",
                "evidence_baseline_column": "exclude_flag_prediction",
                "evidence_candidate_column": "include_flag_prediction",
                "metric": "mae",
                "baseline_measurement": "sensitivity_excl_flag_holdout_mae",
                "candidate_measurement": "sensitivity_incl_flag_holdout_mae",
                "delta_measurement": "sensitivity_mae_difference",
                "delta_formula": "candidate_minus_baseline",
            }
            worker_result = {
                "measurements": [
                    {"name": "sensitivity_excl_flag_holdout_mae", "value": 0.1},
                    {"name": "sensitivity_incl_flag_holdout_mae", "value": 0.2},
                    {"name": "sensitivity_mae_difference", "value": 0.1},
                ]
            }
            errors, trusted = _paired_comparison_audit_errors(
                run_root,
                {"paired_comparison_audits": [audit]},
                worker_result,
                {"sensitivity.csv": evidence},
            )
            self.assertEqual(errors, [])
            self.assertEqual(trusted[0]["comparison_kind"], "candidate_vs_candidate")
            self.assertAlmostEqual(
                trusted[0]["recomputed_measurements"][
                    "sensitivity_excl_flag_holdout_mae"
                ],
                0.1,
            )
            self.assertAlmostEqual(
                trusted[0]["recomputed_measurements"][
                    "sensitivity_incl_flag_holdout_mae"
                ],
                0.2,
            )

    def test_paired_comparison_recomputation_applies_row_filter(self) -> None:
        # A phase-split backtest emits a shared evidence table whose two audit
        # directions cover disjoint row subsets identified by a phase column.
        # row_filter selects each subset so recomputation mirrors the worker.
        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary)
            source_root = run_root / "inputs" / "paired_data"
            output_root = run_root / "attempt-output"
            source_root.mkdir(parents=True)
            output_root.mkdir()
            (source_root / "paired.csv").write_text(
                "date,phase,wso,hmi\n"
                "r1,rising,1,2\n"
                "r2,rising,2,4\n"
                "m1,maximum,10,20\n"
                "m2,maximum,20,40\n"
                "d1,declining,5,10\n",
                encoding="utf-8",
            )
            evidence = output_root / "rows.csv"
            evidence.write_text(
                "date,phase,target_hmi,raw_wso,fwd_pred,rev_pred\n"
                "r1,rising,2,1,1.9,1.9\n"
                "r2,rising,4,2,3.8,3.8\n"
                "m1,maximum,20,10,19.5,19.5\n"
                "m2,maximum,40,20,39.5,39.5\n"
                "d1,declining,10,5,9.5,9.5\n",
                encoding="utf-8",
            )
            base = {
                "comparison_kind": "source_baseline_vs_candidate",
                "source_input_id": "paired_data",
                "source_row_id_column": "date",
                "source_target_column": "hmi",
                "source_baseline_column": "wso",
                "candidate_model_input_columns": ["wso"],
                "candidate_model_target_column": "hmi",
                "baseline_model_input_columns": [],
                "baseline_model_target_column": None,
                "baseline_fit_condition": None,
                "candidate_fit_condition": "fit on disjoint training rows",
                "fit_evaluation_relation": "disjoint_rows",
                "evaluation_target_usage": "metrics_and_evidence_only",
                "evidence_artifact": "rows.csv",
                "evidence_row_id_column": "date",
                "evidence_target_column": "target_hmi",
                "evidence_baseline_column": "raw_wso",
                "metric": "mae",
                "delta_measurement": None,
                "delta_formula": None,
            }
            forward = {
                **base,
                "id": "audit_forward",
                "evaluation_scope": "late phase rows",
                "row_filter": {"column": "phase", "in": ["maximum", "declining"]},
                "evidence_candidate_column": "fwd_pred",
                "baseline_measurement": "uncal_late",
                "candidate_measurement": "fwd_cal",
            }
            reverse = {
                **base,
                "id": "audit_reverse",
                "evaluation_scope": "rising phase rows",
                "row_filter": {"column": "phase", "in": ["rising"]},
                "evidence_candidate_column": "rev_pred",
                "baseline_measurement": "uncal_rising",
                "candidate_measurement": "rev_cal",
            }
            worker_result = {
                "measurements": [
                    {"name": "uncal_late", "value": 35.0 / 3.0},
                    {"name": "fwd_cal", "value": 0.5},
                    {"name": "uncal_rising", "value": 1.5},
                    {"name": "rev_cal", "value": 0.15},
                ]
            }
            errors, trusted = _paired_comparison_audit_errors(
                run_root,
                {"paired_comparison_audits": [forward, reverse]},
                worker_result,
                {"rows.csv": evidence},
            )
            self.assertEqual(errors, [])
            by_id = {row["id"]: row for row in trusted}
            self.assertEqual(by_id["audit_forward"]["row_count"], 3)
            self.assertEqual(by_id["audit_reverse"]["row_count"], 2)
            self.assertAlmostEqual(
                by_id["audit_forward"]["recomputed_measurements"]["uncal_late"],
                35.0 / 3.0,
            )
            self.assertAlmostEqual(
                by_id["audit_reverse"]["recomputed_measurements"]["uncal_rising"],
                1.5,
            )

    def test_paired_comparison_recomputation_flags_absent_row_filter_column(
        self,
    ) -> None:
        # If the row_filter column is absent from the evidence artifact the
        # audit cannot be evaluated and must be reported.
        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary)
            source_root = run_root / "inputs" / "paired_data"
            output_root = run_root / "attempt-output"
            source_root.mkdir(parents=True)
            output_root.mkdir()
            (source_root / "paired.csv").write_text(
                "date,wso,hmi\na,1,2\n",
                encoding="utf-8",
            )
            evidence = output_root / "rows.csv"
            evidence.write_text(
                "date,target_hmi,raw_wso,calibrated_hmi\na,2,1,1.9\n",
                encoding="utf-8",
            )
            audit = {
                "id": "filtered",
                "comparison_kind": "source_baseline_vs_candidate",
                "evaluation_scope": "subset rows",
                "row_filter": {"column": "phase", "in": ["rising"]},
                "source_input_id": "paired_data",
                "source_row_id_column": "date",
                "source_target_column": "hmi",
                "source_baseline_column": "wso",
                "candidate_model_input_columns": ["wso"],
                "candidate_model_target_column": "hmi",
                "baseline_model_input_columns": [],
                "baseline_model_target_column": None,
                "baseline_fit_condition": None,
                "candidate_fit_condition": "fit on disjoint training rows",
                "fit_evaluation_relation": "disjoint_rows",
                "evaluation_target_usage": "metrics_and_evidence_only",
                "evidence_artifact": "rows.csv",
                "evidence_row_id_column": "date",
                "evidence_target_column": "target_hmi",
                "evidence_baseline_column": "raw_wso",
                "evidence_candidate_column": "calibrated_hmi",
                "metric": "mae",
                "baseline_measurement": "raw_mae",
                "candidate_measurement": "cal_mae",
                "delta_measurement": None,
                "delta_formula": None,
            }
            errors, _ = _paired_comparison_audit_errors(
                run_root,
                {"paired_comparison_audits": [audit]},
                {"measurements": []},
                {"rows.csv": evidence},
            )
            self.assertTrue(
                any("row_filter column" in message for message in errors),
                errors,
            )

    def test_wall_budget_stops_infinite_code(self) -> None:
        req = request("unit_budget", wall_seconds=1, max_attempts=1)
        run_id = service.bind_request({"request": req})["run_id"]
        self.addCleanup(cleanup_run, run_id)
        service.inspect_inputs(run_id)
        service.validate_and_store_design(run_id, response(req), design(req))
        attempt_id = service.prepare(
            run_id,
            [{"path": "experiment.py", "content": LOOP_CODE}],
            None,
            "Budget stop fixture.",
        )["attempt_id"]
        service.execute(run_id, attempt_id)
        verified = service.verify(run_id, attempt_id, None)
        self.assertEqual(verified["outcome"], "budget_stopped")
        entry = service.finalize(run_id)
        self.assertIn("达到资源预算后停止", entry["user_display_markdown"])

    def test_only_technical_failure_can_be_repaired(self) -> None:
        req = request("unit_no_repair")
        run_id, attempt_id = create_ready_run(req)
        self.addCleanup(cleanup_run, run_id)
        service.execute(run_id, attempt_id)
        service.verify(run_id, attempt_id, assessment("high_uncertainty"))
        with self.assertRaisesRegex(RuntimeError, "only technical_failure"):
            service.prepare(
                run_id,
                [{"path": "experiment.py", "content": SUCCESS_CODE}],
                attempt_id,
                "Should not be accepted.",
            )


if __name__ == "__main__":
    unittest.main()
