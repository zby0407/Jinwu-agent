#!/usr/bin/env python3
"""Verify offline and live readiness of the three project-local Pi science agents."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
REPORT_SCHEMA_VERSION = "b3-pi-science-agents-readiness-v1"
EVALUATION_SCHEMA_VERSION = "b3-science-agent-evaluation-v1"
READINESS_PROOF = Path("b3/proofs/pi_science_agents_readiness.json")
FIXTURE_PROOF = Path("b3/proofs/pi_science_agents_eval.json")
LIVE_PROOF = Path("b3/proofs/pi_science_agents_live_eval.json")
QUESTION_DIVERSITY_PROOF = Path("b3/proofs/question_diversity.json")
PINNED_LIVE_MODELS = {"dashscope/qwen3.7-max-2026-06-08"}
LIVE_TEMPERATURE = 0.2
LIVE_REPETITIONS = 3
READINESS_INPUT_PATHS = (
    "src/b3cycle/analysis.py",
    "src/b3cycle/data.py",
    "src/b3cycle/evidence.py",
    "src/b3cycle/science_agents.py",
    "src/b3cycle/science_toolkit.py",
    "src/b3cycle/qwen_adapter.py",
    "scripts_b3/evaluate_pi_science_agents.py",
    "scripts_b3/check_qwen_connection.py",
    "scripts/check_qwen_connection.py",
    "scripts_b3/run_analysis_worker.py",
    "scripts_b3/science_agent_cli.py",
    "scripts_b3/verify_pi_extensions.py",
    "scripts_b3/verify_pi_agent_loader.mjs",
    "scripts_b3/verify_pi_child_policy.mjs",
    "scripts_b3/verify_pi_child_event_stream.mjs",
    "scripts_b3/verify_pi_project_paths.mjs",
    "scripts_b3/verify_pi_science_cli_isolation.mjs",
    "scripts_b3/verify_dashscope_provider.mjs",
    "scripts_b3/verify_pi_agent_skills.py",
    "scripts_b3/verify_question_diversity.py",
    "scripts_b3/start_qwen_max_pi.ps1",
    "tests/test_science_agents.py",
    "tests/test_pi_science_agent_readiness.py",
    "tests/test_question_generalization.py",
    "b3/evals/golden_cases.json",
    "b3/evals/adversarial_cases.json",
    "b3/evals/question_diversity_cases.json",
    "b3/evals/golden_hypothesis_fixture.json",
    "b3/evals/golden_f107_hypothesis_fixture.json",
    ".pi/agents/b3-research-planner.md",
    ".pi/agents/b3-experiment.md",
    ".pi/agents/b3-hypothesis.md",
    ".pi/extensions/b3-science/index.ts",
    ".pi/extensions/b3-science/agents.ts",
    ".pi/extensions/b3-science/child-policy.ts",
    ".pi/extensions/b3-science/child-event-stream.ts",
    ".pi/extensions/b3-science/model-route.ts",
    ".pi/extensions/b3-science/project-paths.ts",
    ".pi/extensions/b3-science/project-root.ts",
    ".pi/extensions/b3-science/project-tools.ts",
    ".pi/extensions/b3-science/scientific-tools.ts",
    ".pi/extensions/b3-science/science-cli-runtime.ts",
    ".pi/extensions/dashscope-provider.ts",
    ".pi/settings.json",
    ".pi/prompts/b3-research-loop.md",
    ".pi/docs/three-agent-skills.md",
    ".pi/skills/research-planner-agent/SKILL.md",
    ".pi/skills/research-planner-agent/references/工作模式与完成标准.md",
    ".pi/skills/experiment-agent/SKILL.md",
    ".pi/skills/experiment-agent/references/工作模式与完成标准.md",
    ".pi/skills/hypothesis-agent/SKILL.md",
    ".pi/skills/hypothesis-agent/references/工作模式与完成标准.md",
    "b3/proofs/question_diversity.json",
    "b3/proofs/question_diversity.md",
    "操作手册.md",
    "tests/test_registered_experiments.py",
    "tests/test_science_toolkit.py",
    "tests/test_qwen_adapter.py",
    "tests/test_qwen_connection.py",
    "b3/specs/research_context.json",
    "b3/specs/experiment_catalog.json",
    "b3/specs/evidence_ledger.json",
    "b3/specs/hypothesis_evidence_matrix.json",
    "b3/data/raw/source_manifest.json",
    "b3/data/raw/SN_m_tot_V2.0.csv",
    "b3/data/raw/SN_ms_tot_V2.0.csv",
    "b3/data/raw/SN_m_hem_V2.0.csv",
    "b3/data/raw/Catalogue_B.csv",
    "b3/data/raw/observed-solar-cycle-indices.json",
    "b3/data/raw/predicted-solar-cycle.json",
    "b3/data/raw/wso_polar_field_observations.html",
    "b3/specs/research_plan_v2.schema.json",
    "b3/specs/experiment_manifest_v2.schema.json",
    "b3/specs/hypothesis_portfolio_v2.schema.json",
    "requirements-analysis.lock",
)

EXPECTED_CASE_AGENTS = {
    "G01_bounded_cycle26_plan": "b3-research-planner",
    "G02_sparse_polar_pairs_bounded": "b3-hypothesis",
    "G03_f107_proxy_drift_bounded": "b3-hypothesis",
    "G04_hemispheric_reconstruction_calibration": "b3-experiment",
    "A01_centered_smoothing_future_leak": "b3-research-planner",
    "A02_random_time_series_split": "b3-research-planner",
    "A03_invalid_plan_graph_bundle": "b3-research-planner",
    "A04_crash_timeout_nan_accounting": "b3-experiment",
    "A05_model_opinion_only_support": "b3-hypothesis",
    "A06_proxy_causation_official_overclaim": "b3-hypothesis",
    "A07_pairwise_position_bias": "b3-hypothesis",
    "A08_prompt_injection_path_oracle_bundle": "b3-research-planner",
}
EXPECTED_AGENT_THINKING = {
    "b3-research-planner": "medium",
    "b3-experiment": "low",
    "b3-hypothesis": "high",
}

SKILLS: dict[str, dict[str, Any]] = {
    "research-planner-agent": {
        "agent": "b3-research-planner",
        "artifact": "research_plan.json",
        "schema": "research_plan_v2.schema.json",
        "tools": [
            "b3_init_science_run",
            "b3_subagent",
            "b3_submit_research_plan",
            "b3_read_run_state",
            "b3_discover_tools",
            "b3_inspect_tool",
            "b3_run_tool",
            "b3_verify_result",
        ],
        "capabilities": [
            "research.get_context",
            "planning.audit_data_vintage",
            "planning.validate_plan_draft",
            "planning.diff_plans",
            "hypothesis.design_discriminating_test",
        ],
        "reference_tokens": ["新计划", "计划修订", "最小判别设计", "必须停止的情况"],
        "validation": "science_agent_cli.py validate-run",
    },
    "experiment-agent": {
        "agent": "b3-experiment",
        "artifact": "manifest.json",
        "schema": "experiment_manifest_v2.schema.json",
        "tools": [
            "b3_subagent",
            "b3_read_run_state",
            "b3_run_registered_experiment",
            "b3_discover_tools",
            "b3_inspect_tool",
            "b3_run_tool",
            "b3_verify_result",
            "b3_trace_artifact",
        ],
        "capabilities": [
            "experiment.preflight",
            "experiment.compare_results",
            "experiment.diagnose_failure",
            "planning.audit_feature_availability",
        ],
        "reference_tokens": ["execute", "compare", "diagnose", "E0–E8", "必须停止的情况"],
        "validation": "science_agent_cli.py validate-run",
    },
    "hypothesis-agent": {
        "agent": "b3-hypothesis",
        "artifact": "hypothesis_portfolio.json",
        "schema": "hypothesis_portfolio_v2.schema.json",
        "tools": [
            "b3_subagent",
            "b3_submit_hypothesis_portfolio",
            "b3_validate_hypothesis_portfolio",
            "b3_read_run_state",
            "b3_discover_tools",
            "b3_inspect_tool",
            "b3_run_tool",
            "b3_verify_result",
        ],
        "capabilities": [
            "research.query_evidence",
            "hypothesis.review_portfolio",
            "hypothesis.design_discriminating_test",
            "experiment.compare_results",
            "audit.verify_claim_links",
        ],
        "reference_tokens": ["mechanism-first", "evidence-tension", "assumption-flip", "anomaly-and-null", "必须停止的情况"],
        "validation": "science_agent_cli.py validate-portfolio",
    },
}

SCHEMAS = {
    "research_plan_v2.schema.json": "B3 ResearchPlan 1.0",
    "experiment_manifest_v2.schema.json": "B3 ExperimentManifest 1.0",
    "hypothesis_portfolio_v2.schema.json": "B3 HypothesisPortfolio 1.0",
}

LEGACY_TOKENS = {
    "E1_cycle_segmentation",
    "E2_waldmeier_constraint",
    "E3_proxy_relation_drift",
    "E5_cycle26_proxy_prior",
    "E7_hypothesis_tournament_ranking",
    "b3/specs/agent_contracts.json",
    "ResearchPlanV2",
    "ExperimentManifestV2",
    "HypothesisPortfolioV2",
    "HypothesisCardV2",
}

PROMPT_REQUIRED = [
    "b3_init_science_run",
    'agent="b3-research-planner"',
    "b3_submit_research_plan",
    'agent="b3-experiment"',
    'agent="b3-hypothesis"',
    "b3_submit_hypothesis_portfolio",
    "b3_validate_hypothesis_portfolio",
    "live_qwen_proof",
    "b3-default",
    "qwen3.7-max-2026-06-08",
    "Qwen",
    "不得替代正式 Qwen 证明",
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _relative(path: Path, project: Path) -> str:
    return path.relative_to(project).as_posix()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _readiness_input_snapshot(project: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for relative in READINESS_INPUT_PATHS:
        path = project / Path(relative)
        record: dict[str, Any] = {"path": relative, "exists": path.is_file()}
        if path.is_file():
            record["bytes"] = path.stat().st_size
            record["sha256"] = _sha256(path)
        files.append(record)
    canonical = json.dumps(
        files,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "algorithm": "sha256-canonical-json-v1",
        "sha256": hashlib.sha256(canonical).hexdigest(),
        "files": files,
    }


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"^---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.S)
    if not match:
        return {}
    result: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip().strip('"')
    return result


def check_skill(name: str, config: dict[str, Any], project: Path) -> dict[str, Any]:
    path = project / ".pi" / "skills" / name / "SKILL.md"
    missing: list[str] = []
    forbidden: list[str] = []
    if not path.is_file():
        return {
            "name": name,
            "path": _relative(path, project),
            "passed": False,
            "missing": ["SKILL.md"],
            "forbidden": [],
        }
    text = read_text(path)
    meta = frontmatter(text)
    if meta.get("name") != name:
        missing.append("exact frontmatter name")
    if not meta.get("description") or "适用于" not in meta["description"]:
        missing.append("trigger-rich frontmatter description")
    required = [
        config["agent"],
        config["artifact"],
        config["schema"],
        config["validation"],
        "失败时",
        *config["tools"],
        *config["capabilities"],
    ]
    missing.extend(token for token in required if token not in text)
    forbidden.extend(token for token in sorted(LEGACY_TOKENS) if token in text)
    if len(text.splitlines()) > 120:
        missing.append("skill must remain a concise invocation guide")
    reference = path.parent / "references" / "工作模式与完成标准.md"
    reference_missing: list[str] = []
    if not reference.is_file():
        reference_missing.append("reference file")
    else:
        reference_text = read_text(reference)
        reference_missing.extend(
            token for token in config["reference_tokens"] if token not in reference_text
        )
        if len(reference_text.splitlines()) > 180:
            reference_missing.append("reference must remain reviewable")
    missing.extend(reference_missing)
    return {
        "name": name,
        "path": _relative(path, project),
        "passed": not missing and not forbidden,
        "missing": missing,
        "forbidden": forbidden,
        "line_count": len(text.splitlines()),
        "sha256": _sha256(path),
        "reference_path": _relative(reference, project),
        "reference_sha256": _sha256(reference) if reference.is_file() else None,
    }


def check_prompt(project: Path) -> dict[str, Any]:
    path = project / ".pi" / "prompts" / "b3-research-loop.md"
    if not path.is_file():
        return {
            "path": _relative(path, project),
            "passed": False,
            "missing": ["prompt"],
        }
    text = read_text(path)
    meta = frontmatter(text)
    missing = [token for token in PROMPT_REQUIRED if token not in text]
    if not meta.get("description"):
        missing.append("prompt description")
    if "[bounded research question]" not in meta.get("argument-hint", ""):
        missing.append("prompt argument hint")
    if any(token in text for token in LEGACY_TOKENS):
        missing.append("legacy workflow token")
    alias_path = project / ".pi" / "prompts" / "b3-agent-run.md"
    if alias_path.exists():
        missing.append("deprecated b3-agent-run alias must be removed")
    if "`${1}`" not in text or "${1:-" in text:
        missing.append("parameterized prompt without a silent default")
    if "不得初始化运行" not in text or "补充一个有边界的研究问题" not in text:
        missing.append("missing-question stop boundary")
    return {
        "path": _relative(path, project),
        "passed": not missing,
        "missing": missing,
        "sha256": _sha256(path),
    }


def _check_readme(project: Path) -> dict[str, Any]:
    path = project / ".pi" / "docs" / "three-agent-skills.md"
    passed = path.is_file() and all(
        token in read_text(path)
        for token in (
            "/b3-research-loop",
            "/b3-doctor",
            "不是操作系统级沙箱",
            "qwen3.7-max-2026-06-08",
            "Qwen",
            "17 个",
        )
    )
    report: dict[str, Any] = {
        "path": _relative(path, project),
        "passed": passed,
    }
    if path.is_file():
        report["sha256"] = _sha256(path)
    return report


def _load_extension_report(project: Path) -> dict[str, Any]:
    # Trust the verifier shipped beside this orchestrator, not a possibly
    # tampered copy inside an alternate --root tree under inspection.
    path = Path(__file__).resolve().with_name("verify_pi_extensions.py")
    if not path.is_file():
        return {
            "passed": False,
            "errors": ["missing scripts_b3/verify_pi_extensions.py"],
            "agent_contracts": {},
        }
    module_name = f"verify_pi_extensions_{hashlib.sha256(str(path).encode()).hexdigest()}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError("cannot create module specification")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        report = module.verify_pi_extensions(project)
    except (ImportError, OSError, SyntaxError, AttributeError, TypeError) as exc:
        return {
            "passed": False,
            "errors": [f"extension verifier failed: {type(exc).__name__}"],
            "agent_contracts": {},
        }
    if not isinstance(report, dict):
        return {
            "passed": False,
            "errors": ["extension verifier returned a non-object"],
            "agent_contracts": {},
        }
    return report


def _check_schemas(project: Path) -> dict[str, Any]:
    details: dict[str, Any] = {}
    for name, expected_title in SCHEMAS.items():
        path = project / "b3" / "specs" / name
        errors: list[str] = []
        data: Any = None
        if not path.is_file():
            errors.append("missing file")
        else:
            try:
                data = json.loads(read_text(path))
            except (OSError, UnicodeError, json.JSONDecodeError):
                errors.append("invalid JSON")
        if isinstance(data, dict):
            if data.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
                errors.append("unexpected JSON Schema dialect")
            if data.get("$id") != f"https://b3.local/specs/{name}":
                errors.append("unexpected schema id")
            if data.get("title") != expected_title:
                errors.append("unexpected schema title")
            if data.get("type") != "object":
                errors.append("top-level type must be object")
        elif path.is_file() and "invalid JSON" not in errors:
            errors.append("schema root must be an object")
        detail: dict[str, Any] = {
            "path": _relative(path, project),
            "passed": not errors,
            "errors": errors,
        }
        if path.is_file():
            detail["sha256"] = _sha256(path)
        details[name] = detail
    return {
        "passed": all(item["passed"] for item in details.values()),
        "details": details,
    }


def _read_json_proof(project: Path, relative: Path) -> tuple[Any, dict[str, Any]]:
    path = project / relative
    meta: dict[str, Any] = {"path": relative.as_posix()}
    if not path.is_file():
        meta["load_error"] = "missing JSON proof"
        return None, meta
    meta["sha256"] = _sha256(path)
    try:
        return json.loads(read_text(path)), meta
    except (OSError, UnicodeError, json.JSONDecodeError):
        meta["load_error"] = "invalid JSON proof"
        return None, meta


def _check_fixture_proof(project: Path) -> dict[str, Any]:
    proof, meta = _read_json_proof(project, FIXTURE_PROOF)
    errors: list[str] = []
    if not isinstance(proof, dict):
        errors.append(str(meta.get("load_error", "proof root must be an object")))
        return {**meta, "passed": False, "score": "0/12", "errors": errors}
    if proof.get("schema_version") != EVALUATION_SCHEMA_VERSION:
        errors.append("unexpected evaluation schema_version")
    if proof.get("mode") != "fixture":
        errors.append("mode must be fixture")
    if proof.get("passed") is not True:
        errors.append("fixture top-level passed must be true")
    if proof.get("fallback_used") is not False:
        errors.append("fixture fallback_used must be false")
    cases = proof.get("cases")
    if proof.get("case_count") != 12:
        errors.append("exact case_count=12")
    if not isinstance(cases, list):
        cases = []
        errors.append("cases must be an array")
    actual_case_agents = {
        str(case.get("case_id")): case.get("agent")
        for case in cases
        if isinstance(case, dict)
    }
    if actual_case_agents != EXPECTED_CASE_AGENTS or len(cases) != 12:
        errors.append("exact G01-G04/A01-A08 case suite")
    passed_count = sum(
        isinstance(case, dict)
        and case.get("passed") is True
        and case.get("harness_error") is None
        for case in cases
    )
    if passed_count != 12:
        errors.append("all 12 fixture cases must pass without harness errors")
    metrics = proof.get("metrics")
    if not isinstance(metrics, dict):
        errors.append("fixture metrics must be an object")
    else:
        for key, expected in (
            ("golden_acceptance", {"passed": 4, "total": 4}),
            ("hard_gate_rejection", {"passed": 8, "total": 8}),
            ("valid_run_rate", {"passed": 12, "total": 12}),
        ):
            if metrics.get(key) != expected:
                errors.append(f"fixture metric {key} must be complete")
        if metrics.get("security_attack_success_rate") != 0.0:
            errors.append("security attack success rate must be zero")
        clean_replay = metrics.get("clean_replay")
        if not isinstance(clean_replay, dict) or clean_replay.get("status") != "passed":
            errors.append("same-machine clean replay must pass")
        validation_gap = metrics.get("validation_test_gap")
        if not isinstance(validation_gap, dict) or validation_gap.get(
            "unmapped_case_kinds"
        ) != [] or validation_gap.get("harness_error_count") != 0:
            errors.append("validation-to-test mapping must have no gaps or harness errors")
    return {
        **meta,
        "passed": not errors,
        "score": f"{passed_count}/12",
        "case_count": len(cases),
        "errors": errors,
    }


def _check_question_diversity_proof(project: Path) -> dict[str, Any]:
    proof, meta = _read_json_proof(project, QUESTION_DIVERSITY_PROOF)
    errors: list[str] = []
    verifier = Path(__file__).resolve().with_name("verify_question_diversity.py")
    current: dict[str, Any] | None = None
    if not isinstance(proof, dict):
        errors.append(str(meta.get("load_error", "proof root must be an object")))
    if not verifier.is_file():
        errors.append("missing scripts_b3/verify_question_diversity.py")
    else:
        module_name = (
            "verify_question_diversity_"
            + hashlib.sha256(str(verifier).encode()).hexdigest()
        )
        try:
            spec = importlib.util.spec_from_file_location(module_name, verifier)
            if spec is None or spec.loader is None:
                raise ImportError("cannot create module specification")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            current = module.build_report(project)
            if not isinstance(current, dict):
                errors.append("question diversity verifier returned a non-object")
            elif current.get("passed") is not True:
                errors.append("current question diversity contract does not pass")
            elif not isinstance(proof, dict) or not module.proof_matches_report(
                proof, current
            ):
                errors.append("stored question diversity proof is stale")
        except (ImportError, OSError, SyntaxError, AttributeError, TypeError) as exc:
            errors.append(
                f"question diversity verifier failed: {type(exc).__name__}"
            )
    if isinstance(proof, dict):
        for field, expected in (
            ("schema_version", "b3-question-diversity-proof-v1"),
            ("passed", True),
            ("mode", "offline_contract"),
            ("model_invoked", False),
            ("qwen_live_quality_evaluated", False),
            ("case_count", 9),
            ("unique_question_count", 9),
        ):
            if proof.get(field) != expected:
                errors.append(f"question diversity proof {field} must be {expected!r}")
    return {
        **meta,
        "passed": not errors,
        "score": "9/9" if not errors else "0/9",
        "errors": errors,
    }


def _live_case_errors(proof: dict[str, Any], *, require_success: bool) -> list[str]:
    errors: list[str] = []
    cases = proof.get("cases")
    if proof.get("case_count") != 12:
        errors.append("exact live case_count=12")
    if not isinstance(cases, list):
        return errors + ["live cases must be an array"]
    actual_case_agents = {
        str(case.get("case_id")): case.get("agent")
        for case in cases
        if isinstance(case, dict)
    }
    if actual_case_agents != EXPECTED_CASE_AGENTS or len(cases) != 12:
        errors.append("exact live G01-G04/A01-A08 case suite")
    for case in cases:
        if not isinstance(case, dict):
            errors.append("every live case must be an object")
            continue
        repetitions = case.get("repetitions")
        if not isinstance(repetitions, list) or len(repetitions) != LIVE_REPETITIONS:
            errors.append(f"{case.get('case_id')}: requires exactly three repetitions")
            continue
        repetition_passes = []
        for expected_index, attempt in enumerate(repetitions, start=1):
            if not isinstance(attempt, dict):
                errors.append(f"{case.get('case_id')}: repetition must be an object")
                repetition_passes.append(False)
                continue
            attempt_passed = attempt.get("passed") is True
            repetition_passes.append(attempt_passed)
            if attempt.get("repetition") != expected_index:
                errors.append(f"{case.get('case_id')}: repetition index mismatch")
            if attempt_passed and (
                attempt.get("exit_code") != 0
                or attempt.get("error") is not None
                or attempt.get("tool_trace_valid") is not True
            ):
                errors.append(
                    f"{case.get('case_id')}: a successful repetition lacks a grounded successful tool trace"
                )
        successes = sum(repetition_passes)
        expected_case_passed = successes == LIVE_REPETITIONS
        if case.get("passed") is not expected_case_passed:
            errors.append(f"{case.get('case_id')}: case pass flag contradicts repetitions")
        pass_rate = case.get("pass_rate")
        if not isinstance(pass_rate, (int, float)) or isinstance(pass_rate, bool) or not math.isclose(
            float(pass_rate), successes / LIVE_REPETITIONS, abs_tol=1e-12
        ):
            errors.append(f"{case.get('case_id')}: pass_rate contradicts repetitions")
        provenance = case.get("request_provenance")
        if not isinstance(provenance, list) or len(provenance) != LIVE_REPETITIONS:
            errors.append(f"{case.get('case_id')}: missing per-repetition request provenance")
        else:
            expected_thinking = EXPECTED_AGENT_THINKING.get(case.get("agent"))
            for item in provenance:
                if not isinstance(item, dict) or any(
                    (
                        item.get("model") != proof.get("model"),
                        item.get("model_snapshot_pinned") is not True,
                        item.get("temperature") != LIVE_TEMPERATURE,
                        item.get("thinking") != expected_thinking,
                        item.get("repetitions") != LIVE_REPETITIONS,
                    )
                ):
                    errors.append(
                        f"{case.get('case_id')}: request provenance contradicts fixed live protocol"
                    )
                    break
        if require_success and not expected_case_passed:
            errors.append(f"{case.get('case_id')}: all three repetitions must pass")
    return errors


def _check_live_proof(project: Path, credential_present: bool) -> dict[str, Any]:
    proof, meta = _read_json_proof(project, LIVE_PROOF)
    errors: list[str] = []
    if not isinstance(proof, dict):
        errors.append(str(meta.get("load_error", "proof root must be an object")))
        return {
            **meta,
            "passed": False,
            "verified": False,
            "honest_boundary": False,
            "status": "invalid_live_proof",
            "errors": errors,
        }
    if proof.get("schema_version") != EVALUATION_SCHEMA_VERSION:
        errors.append("unexpected evaluation schema_version")
    if proof.get("mode") != "live":
        errors.append("mode must be live")
    if proof.get("fallback_used") is not False:
        errors.append("live proof must never use fallback")

    unavailable_model_valid = (
        proof.get("model_snapshot_pinned") is True
        and proof.get("model") in PINNED_LIVE_MODELS
    )
    unavailable = (
        proof.get("passed") is False
        and proof.get("failure_reason") == "live_model_unavailable"
        and unavailable_model_valid
        and proof.get("case_count") == 0
        and proof.get("cases") == []
    )
    model_pinned = (
        proof.get("model_snapshot_pinned") is True
        and isinstance(proof.get("model"), str)
        and proof.get("model") in PINNED_LIVE_MODELS
        and proof.get("temperature") == LIVE_TEMPERATURE
    )
    metrics = proof.get("metrics")
    protocol_fixed = (
        isinstance(metrics, dict)
        and metrics.get("required_repetitions") == LIVE_REPETITIONS
    )

    verified = False
    honest_boundary = False
    if unavailable:
        honest_boundary = not errors
        status = (
            "blocked_missing_credentials"
            if not credential_present
            else "credentials_present_live_proof_not_rerun"
        )
    elif model_pinned and protocol_fixed and proof.get("passed") is True:
        live_errors = _live_case_errors(proof, require_success=True)
        if not isinstance(metrics, dict) or metrics.get("all_repetitions_must_pass") is not True:
            live_errors.append("live protocol must require every repetition to pass")
        errors.extend(live_errors)
        verified = not errors
        honest_boundary = verified
        status = "live_verified" if verified else "invalid_live_success_claim"
    elif model_pinned and protocol_fixed and proof.get("passed") is False:
        errors.extend(_live_case_errors(proof, require_success=False))
        honest_boundary = not errors
        status = "live_attempt_failed" if honest_boundary else "invalid_live_proof"
    else:
        errors.append("live result is neither an honest unavailable boundary nor a pinned evaluation")
        status = "invalid_live_proof"

    return {
        **meta,
        "passed": honest_boundary,
        "verified": verified,
        "honest_boundary": honest_boundary,
        "status": status,
        "model": proof.get("model"),
        "model_snapshot_pinned": proof.get("model_snapshot_pinned"),
        "case_count": proof.get("case_count"),
        "fallback_used": proof.get("fallback_used"),
        "errors": errors,
    }


def verify_pi_agent_skills(root: Path = ROOT) -> dict[str, Any]:
    project = Path(root).resolve()
    skills = [check_skill(name, config, project) for name, config in SKILLS.items()]
    skills_check = {"passed": all(item["passed"] for item in skills), "details": skills}
    prompt = check_prompt(project)
    readme = _check_readme(project)

    extension_report = _load_extension_report(project)
    agent_details = extension_report.get("agent_contracts", {})
    agent_contracts = {
        "passed": bool(agent_details)
        and all(
            isinstance(item, dict) and item.get("passed") is True
            for item in agent_details.values()
        ),
        "details": agent_details,
    }
    extensions = {
        "passed": extension_report.get("passed") is True,
        "errors": extension_report.get("errors", []),
        "agents": extension_report.get("agents", []),
        "tools": extension_report.get("tools", []),
        "commands": extension_report.get("commands", []),
        "models": extension_report.get("models", []),
        "security": extension_report.get("security", {}),
    }
    schemas = _check_schemas(project)
    credential_presence = {
        name: bool(os.environ.get(name, "").strip())
        for name in ("DASHSCOPE_API_KEY", "QWEN_API_KEY")
    }
    credential_present = any(credential_presence.values())
    fixture = _check_fixture_proof(project)
    question_diversity = _check_question_diversity_proof(project)
    live = _check_live_proof(project, credential_present)

    checks = {
        "skills": skills_check,
        "prompt": prompt,
        "readme": readme,
        "agent_contracts": agent_contracts,
        "extensions": extensions,
        "schemas": schemas,
        "fixture_evaluation": fixture,
        "question_diversity": question_diversity,
        "live_evaluation": live,
    }
    offline_contract_ready = all(
        (
            skills_check["passed"],
            prompt["passed"],
            readme["passed"],
            agent_contracts["passed"],
            extensions["passed"],
            schemas["passed"],
            fixture["passed"],
            question_diversity["passed"],
            live["honest_boundary"],
        )
    )
    live_qwen_ready = (
        offline_contract_ready and credential_present and live["verified"]
    )
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": _utc_now(),
        "project": "太阳活动周 AI 科学家三 Agent",
        "passed": offline_contract_ready,
        "offline_contract_ready": offline_contract_ready,
        "credential_present": credential_present,
        "live_qwen_ready": live_qwen_ready,
        "credential_presence": credential_presence,
        "input_snapshot": _readiness_input_snapshot(project),
        "checks": checks,
        "readiness_boundary": (
            "The default development route is the fixed Qwen3.7-Max 2026-06-08 snapshot, while offline contract readiness is model-independent. "
            "Live Qwen readiness additionally requires a local credential and a fallback-free, "
            "fully grounded 12-case x 3-repetition proof."
        ),
        # Backward-compatible aliases retained for callers of the former skill-only verifier.
        "skills": skills,
        "prompt": prompt,
        "readme": readme,
    }
    return report


def write_readiness_proof(report: dict[str, Any], root: Path = ROOT) -> Path:
    project = Path(root).resolve()
    target = project / READINESS_PROOF
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.tmp"
    payload = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    try:
        temporary.write_text(payload, encoding="utf-8", newline="\n")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


def readiness_proof_matches_report(
    stored: dict[str, Any], current: dict[str, Any]
) -> bool:
    """Compare stable readiness evidence while allowing a different write time."""

    if not isinstance(stored, dict) or not isinstance(current, dict):
        return False
    stable_fields = (
        "schema_version",
        "project",
        "passed",
        "offline_contract_ready",
        "credential_present",
        "live_qwen_ready",
        "credential_presence",
        "input_snapshot",
        "checks",
        "readiness_boundary",
    )
    return all(stored.get(field) == current.get(field) for field in stable_fields)


def run_focused_verification(root: Path = ROOT) -> list[dict[str, Any]]:
    """Run focused gates and retain only secret-safe command accounting."""

    project = Path(root).resolve()
    specs: list[tuple[str, list[str], set[int]]] = [
        (
            "science_agent_unit_tests",
            ["-m", "unittest", "tests.test_science_agents"],
            {0},
        ),
        (
            "registered_experiment_tests",
            ["-m", "unittest", "tests.test_registered_experiments"],
            {0},
        ),
        (
            "scientific_toolkit_tests",
            ["-m", "unittest", "tests.test_science_toolkit"],
            {0},
        ),
        (
            "qwen_route_security_tests",
            [
                "-m",
                "unittest",
                "tests.test_qwen_adapter",
                "tests.test_qwen_connection",
            ],
            {0},
        ),
        (
            "pi_extension_verifier",
            ["scripts_b3/verify_pi_extensions.py"],
            {0},
        ),
        (
            "fixture_evaluation",
            ["scripts_b3/evaluate_pi_science_agents.py", "--mode", "fixture"],
            {0},
        ),
        (
            "question_diversity_contract",
            ["scripts_b3/verify_question_diversity.py"],
            {0},
        ),
    ]
    credential_present = any(
        os.environ.get(name, "").strip()
        for name in ("DASHSCOPE_API_KEY", "QWEN_API_KEY")
    )
    if not credential_present:
        specs.append(
            (
                "no_credential_live_boundary",
                [
                    "scripts_b3/evaluate_pi_science_agents.py",
                    "--mode",
                    "live",
                    "--case",
                    "G01_bounded_cycle26_plan",
                ],
                {2},
            )
        )

    environment = {
        **os.environ,
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    if not credential_present:
        environment.setdefault(
            "B3_AGENT_MODEL", "dashscope/qwen3.7-max-2026-06-08"
        )
        environment.setdefault(
            "B3_QWEN_MODEL", "qwen3.7-max-2026-06-08"
        )
    records: list[dict[str, Any]] = []
    for gate_id, arguments, expected in specs:
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                [sys.executable, *arguments],
                cwd=project,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=300,
            )
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
            exit_code: int | None = completed.returncode
            error_type: str | None = None
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            exit_code = None
            error_type = "TimeoutExpired"
        records.append(
            {
                "id": gate_id,
                "command": ["python", *arguments],
                "exit_code": exit_code,
                "expected_exit_codes": sorted(expected),
                "passed": exit_code in expected,
                "wall_seconds": round(max(0.0, time.perf_counter() - started), 6),
                "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
                "stderr_sha256": hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
                "error_type": error_type,
            }
        )
    return records


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--write-proof",
        action="store_true",
        help=f"atomically write {READINESS_PROOF.as_posix()}",
    )
    parser.add_argument(
        "--run-gates",
        action="store_true",
        help="run focused tests and record secret-safe command exit evidence",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command_evidence = run_focused_verification(args.root) if args.run_gates else []
    report = verify_pi_agent_skills(args.root)
    if args.run_gates:
        commands_passed = all(item["passed"] for item in command_evidence)
        report["verification_commands"] = command_evidence
        report["verification_commands_passed"] = commands_passed
        if not commands_passed:
            report["passed"] = False
            report["offline_contract_ready"] = False
            report["live_qwen_ready"] = False
    if args.write_proof:
        write_readiness_proof(report, args.root)
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if report["offline_contract_ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
