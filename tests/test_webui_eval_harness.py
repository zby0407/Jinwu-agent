from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVALS = ROOT / "research" / "review" / "evals"


def _load_summary_module():
    path = EVALS / "summarize_webui_runs.py"
    spec = importlib.util.spec_from_file_location("summarize_webui_runs", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_visible_suite_v2_resolves_all_required_case_fields() -> None:
    suite = json.loads((EVALS / "all_visible_e2e_v2.json").read_text(encoding="utf-8"))
    defaults = suite["defaults"]
    overrides = suite["case_overrides"]
    cases = []
    for source in suite["source_suites"]:
        source_doc = json.loads((EVALS / source).read_text(encoding="utf-8"))
        cases.extend(source_doc["cases"])
    assert len(cases) == 18
    assert len({case["id"] for case in cases}) == 18
    required = {
        "prompt",
        "input_files",
        "review_mode",
        "reviewer_model",
        "expected_outcome",
        "repetitions",
    }
    for source_case in cases:
        case = {**defaults, **source_case, **overrides[source_case["id"]]}
        assert required <= set(case)
        assert case["reviewer_model"] == {
            "provider": "kimi-coding",
            "model": "kimi-k3",
        }
        for input_file in case["input_files"]:
            assert (EVALS / input_file).is_file()


def test_eval_scripts_are_syntactically_valid() -> None:
    for script in ("run_webui_case.mjs", "run_eval_campaign.mjs"):
        subprocess.run(
            ["node", "--check", str(EVALS / script)],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    if os.name != "nt":
        for script in ("run_eval_backend.sh", "run_eval_webui.sh"):
            subprocess.run(
                ["bash", "-n", str(EVALS / script)],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )


def test_eval_launchers_pin_the_shared_workspace_root() -> None:
    for script in ("run_eval_backend.sh", "run_eval_webui.sh"):
        text = (EVALS / script).read_text(encoding="utf-8")
        assert "JW_WORKSPACE_DIR" in text

    backend = (EVALS / "run_eval_backend.sh").read_text(encoding="utf-8")
    assert "JW_EVAL_PRODUCER_MODEL" in backend
    assert "JW_AUXILIARY_MODEL" in backend
    assert "JW_AUXILIARY_PROVIDER" in backend
    assert "JW_MEMORY_WORKERS_ENABLED" in backend
    assert "solar-planner" in backend
    assert "solar-hypothesis" in backend
    assert "solar-experiment" in backend
    assert 'reviewer="${1:-kimi}"' in backend
    assert "JW_EVAL_AUXILIARY_MODEL:-qwen3.7-plus" in backend
    assert "solar-data" in backend
    assert "solar-knowledge" in backend
    assert "JW_INDEPENDENT_REVIEW_MODEL" in backend
    assert "JW_INDEPENDENT_REVIEW_PROVIDER" in backend

    campaign = (EVALS / "run_eval_campaign.mjs").read_text(encoding="utf-8")
    assert 'process.env.JW_EVAL_REVIEWER || "kimi"' in campaign
    assert '"qwen3.8-max"' in campaign
    assert '"custom-openai"' in campaign
    assert '"kimi-k3"' in campaign
    assert '"kimi-coding"' in campaign

    frontend = (EVALS / "run_eval_webui.sh").read_text(encoding="utf-8")
    assert 'HOSTNAME="${JW_EVAL_WEBUI_HOST:-127.0.0.1}"' in frontend


def test_focused_evidence_probes_route_as_bounded_data_work() -> None:
    suite = json.loads(
        (EVALS / "evidence_probe_webui_v2.json").read_text(encoding="utf-8")
    )

    assert {case["id"] for case in suite["cases"]} == {"FR-H09", "FR-H10"}
    for case in suite["cases"]:
        assert case["prompt"].startswith("请准备数据核查摘要")
        assert "区分性检验" not in case["prompt"]
        assert case["probe_artifact_stage"] == "data"
        assert case["reviewer_model"] == {
            "provider": "kimi-coding",
            "model": "kimi-k3",
        }


def test_webui_runner_checks_workspace_after_thread_binding() -> None:
    text = (EVALS / "run_webui_case.mjs").read_text(encoding="utf-8")

    assert "${backendUrl}/api/models" in text
    assert "task workspace binding" in text
    assert "api/workspace?threadId=" in text
    assert "api/workspace?path=" not in text
    assert "threadIsActive" in text
    assert "runIsActive" in text
    assert "!threadIsActive && !runIsActive" in text


def test_webui_runner_records_controller_and_artifact_producer_separately() -> None:
    text = (EVALS / "run_webui_case.mjs").read_text(encoding="utf-8")

    assert "JW_EVAL_PRODUCER_MODEL" in text
    assert "JW_EVAL_PRODUCER_PROVIDER" in text
    assert "controller:" in text
    assert "generator:" in text


def test_webui_runner_bounds_browser_close_wait() -> None:
    text = (EVALS / "run_webui_case.mjs").read_text(encoding="utf-8")

    assert 'cdp.send("Browser.close").catch(() => {})' in text
    assert "Promise.race" in text


def test_summary_keeps_scientific_metrics_pending(tmp_path: Path) -> None:
    run = tmp_path / "formal.two_pass.SC26-B01.r1"
    run.mkdir()
    metadata = {
        "case_id": "SC26-B01",
        "run_label": run.name,
        "outcome": "completed_with_answer",
        "latency_seconds": 10,
        "scientific_status": "active",
        "reviewer": {"review_mode": "two_pass"},
        "stage_verdicts": [],
        "assessment_count": 0,
        "evidence_review_invocations": 0,
        "observed_usage": {"total_tokens": 100},
        "error_signals": {
            "provider_or_runtime_400": False,
            "illegal_route": False,
        },
    }
    (run / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (run / "review_status.json").write_text("{}", encoding="utf-8")

    summary = _load_summary_module().summarize(tmp_path)

    assert summary["runs"] == 1
    assert summary["case_table"][0]["case_id"] == "SC26-B01"
    assert summary["case_table"][0]["review_mode"] == "two_pass"
    assert summary["engineering_gates"]["run_count"] is False
    assert summary["scientific_adjudication"]["status"] == "pending_independent_labels"


def test_summary_requires_exactly_one_assessment_per_review_round(
    tmp_path: Path,
) -> None:
    for suffix, rounds, assessments in (
        ("missing", 1, 0),
        ("duplicate", 1, 2),
        ("skipped", 0, 0),
    ):
        run = tmp_path / f"formal.two_pass.SC26-{suffix}.r1"
        run.mkdir()
        metadata = {
            "case_id": f"SC26-{suffix}",
            "run_label": run.name,
            "outcome": "completed_with_answer",
            "latency_seconds": 10,
            "scientific_status": "active",
            "review_active": True,
            "reviewer": {"review_mode": "two_pass"},
            "stage_verdicts": [],
            "assessment_count": assessments,
            "evidence_review_invocations": rounds,
            "observed_usage": {"total_tokens": 100},
            "error_signals": {
                "provider_or_runtime_400": False,
                "illegal_route": False,
            },
        }
        (run / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        (run / "review_status.json").write_text("{}", encoding="utf-8")

    summary = _load_summary_module().summarize(tmp_path)

    assert summary["engineering_gates"]["assessment_coverage"] is False
    assert summary["failures"]["missing_assessments"] == [
        "formal.two_pass.SC26-duplicate.r1",
        "formal.two_pass.SC26-missing.r1",
        "formal.two_pass.SC26-skipped.r1",
    ]
    assert all(
        row["reasons"] == ["assessment_contract"]
        for row in summary["failures"]["index"]
    )


def test_summary_indexes_harness_failures_without_metadata(tmp_path: Path) -> None:
    run = tmp_path / "formal.closed.SC26-B02.r1"
    run.mkdir()
    (run / "harness_failure.json").write_text(
        json.dumps({"message": "upload did not complete"}), encoding="utf-8"
    )

    summary = _load_summary_module().summarize(tmp_path)

    assert summary["runs"] == 1
    assert summary["case_table"][0]["case_id"] == "SC26-B02"
    assert summary["failures"]["index"] == [
        {
            "directory": "formal.closed.SC26-B02.r1",
            "case_id": "SC26-B02",
            "review_mode": "closed",
            "reasons": ["harness_error"],
            "error_summary": "upload did not complete",
        }
    ]


def test_summary_scans_backend_logs_for_provider_400(tmp_path: Path) -> None:
    (tmp_path / "backend.formal.deepseek.two_pass.log").write_text(
        "openai.BadRequestError: Error code: 400 - provider rejected request\n",
        encoding="utf-8",
    )

    summary = _load_summary_module().summarize(tmp_path)

    assert summary["engineering_gates"]["provider_or_runtime_400_zero"] is False
    assert summary["failures"]["provider_or_runtime_400"] == [
        "backend:backend.formal.deepseek.two_pass.log"
    ]


def test_summary_ignores_nonformal_diagnostic_backend_logs(tmp_path: Path) -> None:
    (tmp_path / "backend.planner_gate.deepseek.two_pass.log").write_text(
        "openai.BadRequestError: Error code: 400 - diagnostic failure\n",
        encoding="utf-8",
    )

    summary = _load_summary_module().summarize(tmp_path)

    assert summary["engineering_gates"]["provider_or_runtime_400_zero"] is True
    assert summary["failures"]["provider_or_runtime_400"] == []
