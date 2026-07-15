#!/usr/bin/env python3
"""Verify integrity, provenance, allowlist, and keyless replay of a B3 three-agent bundle."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Sequence


# This verifier is shipped inside the bundle itself. Disable bytecode before
# importing sibling modules so a plain `python verify_three_agent_bundle.py` run
# cannot mutate the bundle and make the next integrity check fail.
sys.dont_write_bytecode = True


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from build_three_agent_bundle import (  # noqa: E402
    BUNDLE_NAME,
    BundleBuildError,
    EXACT_RULES,
    GENERATED_PATHS,
    HIGH_CONFIDENCE_SECRET_PATTERNS,
    MANIFEST_SCHEMA,
    MAX_RUNTIME_FILE_BYTES,
    MUTABLE_RUNTIME_ROOTS,
    _latest_complete_agent_run,
    _latest_analysis_run,
    _validate_agent_run,
    allowed_runtime_directory,
    allowed_destination_path,
    artifact_index_payload,
    canonical_sha256,
    curated_analysis_run_path,
    curated_agent_run_path,
    forbidden_path_reason,
    forbidden_runtime_path_reason,
    mutable_runtime_path,
    normalize_relative_path,
    runtime_policy_payload,
    sha256,
)


REQUIRED_TOP_LEVEL = {
    "README_先看.md",
    "操作手册.md",
    "成果索引.md",
    "ARTIFACT_INDEX.json",
    "VERIFY.ps1",
    "MANIFEST.json",
    "SOURCE_REVISION.txt",
    "SHA256SUMS",
}
REQUIRED_PAYLOAD = {
    ".pi/agents/b3-research-planner.md",
    ".pi/agents/b3-experiment.md",
    ".pi/agents/b3-hypothesis.md",
    ".pi/extensions/b3-science/index.ts",
    ".pi/extensions/b3-science/child-event-stream.ts",
    ".pi/extensions/b3-science/model-route.ts",
    ".pi/extensions/b3-science/scientific-tools.ts",
    ".pi/extensions/dashscope-provider.ts",
    ".pi/docs/three-agent-skills.md",
    ".pi/APPEND_SYSTEM.md",
    ".pi/settings.json",
    ".pi/prompts/b3-research-loop.md",
    ".pi/skills/research-planner-agent/SKILL.md",
    ".pi/skills/research-planner-agent/references/工作模式与完成标准.md",
    ".pi/skills/experiment-agent/SKILL.md",
    ".pi/skills/experiment-agent/references/工作模式与完成标准.md",
    ".pi/skills/hypothesis-agent/SKILL.md",
    ".pi/skills/hypothesis-agent/references/工作模式与完成标准.md",
    "src/b3cycle/science_agents.py",
    "src/b3cycle/science_toolkit.py",
    "src/b3cycle/qwen_adapter.py",
    "scripts_b3/evaluate_pi_science_agents.py",
    "scripts_b3/check_qwen_connection.py",
    "scripts/check_qwen_connection.py",
    "scripts_b3/run_analysis_worker.py",
    "scripts_b3/science_agent_cli.py",
    "scripts_b3/start_qwen_max_pi.ps1",
    "scripts_b3/build_three_agent_bundle.py",
    "scripts_b3/verify_three_agent_bundle.py",
    "scripts_b3/verify_pi_child_event_stream.mjs",
    "tests/test_science_agents.py",
    "tests/test_registered_experiments.py",
    "tests/test_science_toolkit.py",
    "tests/test_qwen_adapter.py",
    "tests/test_qwen_connection.py",
    "tests/test_pi_science_agent_readiness.py",
    "tests/test_three_agent_bundle.py",
    "b3/specs/research_plan_v2.schema.json",
    "b3/specs/experiment_manifest_v2.schema.json",
    "b3/specs/hypothesis_portfolio_v2.schema.json",
    "b3/specs/research_context.json",
    "b3/specs/experiment_catalog.json",
    "b3/evals/golden_cases.json",
    "b3/evals/adversarial_cases.json",
    "b3/evals/question_diversity_cases.json",
    "b3/proofs/question_diversity.json",
    "b3/proofs/question_diversity.md",
    "b3/proofs/pi_science_agents_eval.json",
    "b3/proofs/pi_science_agents_live_eval.json",
    "b3/docs/pi_three_agents_quickstart.md",
    "b3/docs/三Agent_研究背景与设计说明.md",
    "b3/docs/三Agent_模型配置与隐私操作手册.md",
    "b3/docs/三Agent_现状缺口与未来工作计划.md",
    "b3/docs/最终提交审计清单.json",
    "b3/docs/最终提交审计清单.md",
    "b3/docs/评审速览.md",
    "b3/docs/quality_standard.md",
    "b3/docs/提交包使用与演示说明.md",
    "b3/docs/10分钟演示脚本.md",
    "b3/docs/part4_system_architecture_agent_design.md",
    "b3/docs/model_integration_and_openapi.md",
    "b3/docs/representative_test_cases.md",
    "b3/docs/materials_map.md",
    "b3/docs/release_readiness_audit.json",
    "b3/docs/release_readiness_audit.md",
    "b3/docs/requirements_alignment.md",
    "b3/docs/agent_contracts_and_prompt_protocol.md",
    "b3/docs/pi_agent_subagents_reference.md",
    "b3/docs/第四部分_系统架构与子Agent技术设计_正式稿.md",
    "b3/docs/第四部分_系统架构与子Agent技术设计_正式稿.assets/架构图-1782035895607-3.png",
    "b3/docs/第四部分_系统架构与子Agent技术设计_正式稿.assets/状态转移图.png",
    "b3/proofs/pi_science_agents_readiness.json",
    "b3/proofs/frontend_api_smoke.json",
    "b3/proofs/frontend_api_smoke.md",
    "b3/proofs/frontend_visual_desktop_1440.png",
    "b3/proofs/frontend_visual_mobile_390.png",
    "b3/proofs/frontend_visual_qa.json",
    "b3/proofs/frontend_visual_qa.md",
    "b3/proofs/qwen_connection_check_dry_run.json",
    "b3/proofs/qwen_connection_check_dry_run.md",
    "b3/proofs/qwen_connection_check_live.json",
    "b3/proofs/qwen_connection_check_live.md",
    "b3/proofs/submission_pipeline_run.json",
    "b3/proofs/submission_pipeline_run.md",
    "b3/outputs/b3_analysis_report.json",
    "b3/final_report/b3_final_technical_report.md",
    "b3/final_report/b3_final_technical_report.pdf",
    "b3/final_report/figures/fig01_cycle_peak_timeline.png",
    "b3/final_report/figures/fig02_polar_toy_model.png",
    "b3/final_report/figures/fig03_hypothesis_ranking.png",
    "b3/final_report/figures/fig04_closed_loop_architecture.png",
    "b3/test_cases/manifest.json",
    "b3/test_cases/case_01_cycle26_bounded_research_run.json",
    "b3/test_cases/case_02_polar_precursor_and_dynamo_toy_model.json",
    "b3/test_cases/case_03_f107_proxy_drift_guard.json",
    "b3/test_cases/case_04_evidence_query_h1.json",
    "b3/data/raw/source_manifest.json",
    "b3/data/raw/Catalogue_B.csv",
    "configs/qwen.env.example",
    "requirements-analysis.lock",
    "验收B3三Agent.ps1",
    "启动B3前端与API.ps1",
    "验收B3前端与API.ps1",
    "生成百炼Live证明并重打包.ps1",
    "app_b3.py",
    "static_b3/index.html",
    "static_b3/app.js",
    "static_b3/styles.css",
    "scripts_b3/check_frontend_api_smoke.py",
} | set(EXACT_RULES)
README_OFFLINE_COMMANDS = {
    "& $Python -B scripts_b3/verify_three_agent_bundle.py .",
    "& $Python scripts_b3/verify_pi_agent_skills.py",
    "& $Python scripts_b3/verify_pi_extensions.py",
    "& $Python scripts_b3/verify_question_diversity.py",
    "& $Python -m unittest tests.test_registered_experiments tests.test_science_toolkit tests.test_qwen_adapter tests.test_qwen_connection",
    "& $Python scripts_b3/evaluate_pi_science_agents.py --mode fixture --no-write-proof",
}
MANIFEST_OFFLINE_COMMANDS = {
    "python scripts_b3/evaluate_pi_science_agents.py --mode fixture --no-write-proof",
}
TEXT_SUFFIXES = {
    "",
    ".csv",
    ".css",
    ".example",
    ".html",
    ".json",
    ".js",
    ".lock",
    ".log",
    ".md",
    ".mjs",
    ".ps1",
    ".py",
    ".ts",
    ".txt",
}
ABSOLUTE_WINDOWS_PATH = re.compile(
    r"(?<![A-Za-z0-9])([A-Z]:[\\/](?![\\/])[A-Za-z0-9_.\u4e00-\u9fff][^\r\n`\"']*)"
)


def _safe_relative(value: Any, issues: list[str], context: str) -> str | None:
    if not isinstance(value, str):
        issues.append(f"{context} path must be a string")
        return None
    try:
        return normalize_relative_path(value)
    except BundleBuildError as exc:
        issues.append(f"{context} has unsafe path: {exc}")
        return None


def _walk_bundle(bundle: Path, issues: list[str]) -> set[str]:
    files: set[str] = set()
    for current, directory_names, file_names in os.walk(bundle, followlinks=False):
        current_path = Path(current)
        for name in list(directory_names):
            path = current_path / name
            if path.is_symlink():
                relative = path.relative_to(bundle).as_posix()
                issues.append(f"symbolic link or junction is forbidden: {relative}")
                directory_names.remove(name)
        for name in file_names:
            path = current_path / name
            relative = path.relative_to(bundle).as_posix()
            if path.is_symlink():
                issues.append(f"symbolic link is forbidden: {relative}")
                continue
            files.add(relative)
    return files


def _walk_bundle_directories(bundle: Path) -> set[str]:
    directories: set[str] = set()
    for path in bundle.rglob("*"):
        if path.is_dir() and not path.is_symlink():
            directories.add(path.relative_to(bundle).as_posix())
    return directories


def _expected_parent_directories(files: set[str]) -> set[str]:
    expected: set[str] = set()
    for relative in files:
        parent = PurePosixPath(relative).parent
        while parent != PurePosixPath("."):
            expected.add(parent.as_posix())
            parent = parent.parent
    return expected


def _parse_manifest(bundle: Path, issues: list[str]) -> dict[str, Any]:
    path = bundle / "MANIFEST.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        issues.append(f"cannot read MANIFEST.json: {exc}")
        return {}
    if not isinstance(value, dict):
        issues.append("MANIFEST.json must contain one object")
        return {}
    if value.get("schema_version") != MANIFEST_SCHEMA:
        issues.append("manifest schema_version is invalid")
    if value.get("bundle_name") != BUNDLE_NAME:
        issues.append("manifest bundle_name is invalid")
    revision = value.get("source_revision")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40,64}", revision):
        issues.append("manifest source_revision is invalid")
    for field in ("source_status_sha256", "source_snapshot_sha256"):
        if not isinstance(value.get(field), str) or not re.fullmatch(r"[0-9a-f]{64}", value[field]):
            issues.append(f"manifest {field} is invalid")
    if not isinstance(value.get("source_tree_dirty"), bool):
        issues.append("manifest source_tree_dirty must be boolean")
    if value.get("runtime_policy") != runtime_policy_payload():
        issues.append("manifest runtime policy is invalid")
    security = value.get("security_boundary")
    if not isinstance(security, dict):
        issues.append("manifest security_boundary is missing")
    else:
        for field in (
            "contains_credentials",
            "contains_private_remote_evidence",
            "contains_live_raw_traces",
            "contains_large_external_materials",
        ):
            if security.get(field) is not False:
                issues.append(f"manifest security boundary must keep {field}=false")
    offline = value.get("offline_reproduction")
    if not isinstance(offline, dict):
        issues.append("manifest offline_reproduction is missing")
    else:
        if offline.get("api_key_required") is not False:
            issues.append("offline reproduction must not require an API key")
        live_ready = offline.get("live_qwen_ready")
        if not isinstance(live_ready, bool):
            issues.append("manifest live_qwen_ready must be boolean")
        if offline.get("live_proof_included") is not live_ready:
            issues.append("manifest live proof inclusion must match live readiness")
        if offline.get("offline_contract_ready") is not True:
            issues.append("bundle requires offline_contract_ready=true")
        if offline.get("command") not in MANIFEST_OFFLINE_COMMANDS:
            issues.append("manifest offline reproduction command is not allowlisted")
    inventory = value.get("artifact_inventory")
    if not isinstance(inventory, dict):
        issues.append("manifest artifact_inventory is missing")
    else:
        if inventory.get("entry_index") != "成果索引.md":
            issues.append("manifest artifact index entry is invalid")
        if inventory.get("machine_index") != "ARTIFACT_INDEX.json":
            issues.append("manifest machine artifact index entry is invalid")
        if not isinstance(inventory.get("qwen_live_connection_ok"), bool):
            issues.append("manifest qwen_live_connection_ok must be boolean")
        offline_inventory = value.get("offline_reproduction")
        expected_live = (
            offline_inventory.get("live_qwen_ready")
            if isinstance(offline_inventory, dict)
            else None
        )
        if inventory.get("full_live_matrix_ready") is not expected_live:
            issues.append("artifact inventory live matrix readiness is inconsistent")
    return value


def _verify_artifact_inventory(
    bundle: Path,
    manifest: dict[str, Any],
    path_set: set[str],
    issues: list[str],
) -> None:
    inventory = manifest.get("artifact_inventory")
    if not isinstance(inventory, dict):
        return
    latest = inventory.get("latest_agent_run")
    if not isinstance(latest, dict):
        issues.append("manifest latest_agent_run inventory is missing")
        return
    run_id = latest.get("run_id")
    if not isinstance(run_id, str) or not run_id or "/" in run_id or "\\" in run_id:
        issues.append("manifest latest agent run_id is invalid")
        return
    expected_root = f"b3/agent_runs/{run_id}"
    if latest.get("path") != expected_root:
        issues.append("manifest latest agent run path does not match run_id")
    if not isinstance(latest.get("created_at"), str):
        issues.append("manifest latest agent run created_at is invalid")
    if not isinstance(latest.get("task"), str) or not latest.get("task", "").strip():
        issues.append("manifest latest agent run task is invalid")
    if latest.get("plan_status") not in {"frozen", "approved", "completed"}:
        issues.append("manifest latest agent run plan status is not final")
    if latest.get("portfolio_status") not in {"calibrated", "approved", "completed"}:
        issues.append("manifest latest agent run portfolio status is not final")

    raw_statuses = latest.get("experiment_statuses")
    if not isinstance(raw_statuses, list):
        issues.append("manifest latest agent run experiment_statuses must be a list")
        return
    expected_paths = {
        f"{expected_root}/run_manifest.json",
        f"{expected_root}/research_plan.json",
        f"{expected_root}/hypothesis_portfolio.json",
    }
    codes: list[str] = []
    for index, status in enumerate(raw_statuses):
        if not isinstance(status, dict):
            issues.append(f"manifest experiment_statuses[{index}] must be an object")
            continue
        code = status.get("code")
        directory = status.get("directory")
        value = status.get("status")
        if code not in {f"E{number}" for number in range(9)}:
            issues.append(f"manifest experiment status code is invalid: {code}")
            continue
        if (
            not isinstance(directory, str)
            or directory.split("_", 1)[0] != code
            or "/" in directory
            or "\\" in directory
        ):
            issues.append(f"manifest experiment directory is invalid: {directory}")
            continue
        if not isinstance(value, str) or not value:
            issues.append(f"manifest experiment status is invalid: {code}")
        codes.append(code)
        expected_paths.update(
            {
                f"{expected_root}/experiments/{directory}/manifest.json",
                f"{expected_root}/experiments/{directory}/result.json",
            }
        )
    expected_codes = [f"E{number}" for number in range(9)]
    if codes != expected_codes:
        issues.append("manifest latest agent run must account for E0 through E8 in order")

    actual_run_paths = {path for path in path_set if path.startswith("b3/agent_runs/")}
    if actual_run_paths != expected_paths:
        missing = sorted(expected_paths - actual_run_paths)
        extra = sorted(actual_run_paths - expected_paths)
        issues.append(
            "curated latest agent run payload mismatch: "
            f"missing={missing}, extra={extra}"
        )
    if latest.get("file_count") != len(expected_paths):
        issues.append("manifest latest agent run file_count is invalid")
    for relative in actual_run_paths:
        if not curated_agent_run_path(relative):
            issues.append(f"uncurated latest agent run path is present: {relative}")

    immutable = latest.get("immutable_validation")
    if not isinstance(immutable, dict):
        issues.append("manifest latest agent run immutable validation is missing")
    elif (
        immutable.get("status") != "ok"
        or immutable.get("artifact_count") != 21
        or immutable.get("experiment_manifest_count") != 9
    ):
        issues.append("manifest latest agent run immutable validation is invalid")

    payloads: dict[str, dict[str, Any]] = {}
    for relative in sorted(expected_paths & actual_run_paths):
        try:
            payload = json.loads((bundle / Path(relative)).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            issues.append(f"latest run artifact is not valid JSON: {relative}: {exc}")
            continue
        if not isinstance(payload, dict):
            issues.append(f"latest run artifact must be a JSON object: {relative}")
            continue
        supplied = payload.get("artifact_sha256")
        unhashed = dict(payload)
        unhashed.pop("artifact_sha256", None)
        if supplied != canonical_sha256(unhashed):
            issues.append(f"latest run immutable artifact hash mismatch: {relative}")
        if payload.get("run_id") != run_id:
            issues.append(f"latest run artifact run_id mismatch: {relative}")
        payloads[relative] = payload

    plan = payloads.get(f"{expected_root}/research_plan.json", {})
    plan_nodes = {
        node.get("id"): node
        for node in plan.get("task_graph", [])
        if isinstance(node, dict)
    }
    for status in raw_statuses:
        if not isinstance(status, dict):
            continue
        directory = status.get("directory")
        code = status.get("code")
        if not isinstance(directory, str) or not isinstance(code, str):
            continue
        manifest_path = f"{expected_root}/experiments/{directory}/manifest.json"
        result_path = f"{expected_root}/experiments/{directory}/result.json"
        experiment_manifest = payloads.get(manifest_path)
        result = payloads.get(result_path)
        if not isinstance(experiment_manifest, dict) or not isinstance(result, dict):
            continue
        if status.get("status") != experiment_manifest.get("status"):
            issues.append(f"latest run inventory/artifact status mismatch: {code}")
        experiment_id = experiment_manifest.get("experiment_id")
        seed = experiment_manifest.get("seed")
        expected_node_id = f"{experiment_id}_seed{seed}"
        if experiment_manifest.get("node_id") != expected_node_id or directory != expected_node_id:
            issues.append(f"latest run experiment node identity mismatch: {code}")
        for field in ("experiment_id", "node_id", "seed", "status"):
            if result.get(field) != experiment_manifest.get(field):
                issues.append(f"latest run manifest/result {field} mismatch: {code}")
        references = experiment_manifest.get("artifacts")
        matching = [
            item
            for item in references
            if isinstance(item, dict) and item.get("path") == result_path.removeprefix(f"{expected_root}/")
        ] if isinstance(references, list) else []
        if len(matching) != 1 or matching[0].get("sha256") != result.get("artifact_sha256"):
            issues.append(f"latest run manifest/result artifact reference mismatch: {code}")
        if experiment_manifest.get("status") != "failed":
            parent = plan_nodes.get(experiment_manifest.get("parent_id"))
            if not isinstance(parent, dict) or parent.get("tool") != f"registered:{experiment_id}":
                issues.append(f"latest run frozen-plan linkage mismatch: {code}")
            provenance = experiment_manifest.get("provenance", {})
            if not isinstance(provenance, dict) or provenance.get(
                "plan_artifact_sha256"
            ) != plan.get("artifact_sha256"):
                issues.append(f"latest run frozen-plan hash mismatch: {code}")
        provenance = experiment_manifest.get("provenance", {})
        execution = experiment_manifest.get("execution", {})
        code_files = (
            provenance.get("code_files_sha256", {})
            if isinstance(provenance, dict)
            else {}
        )
        if not isinstance(code_files, dict) or not code_files:
            issues.append(f"latest run code provenance is missing: {code}")
        else:
            if not isinstance(execution, dict) or execution.get(
                "code_sha256"
            ) != canonical_sha256(code_files):
                issues.append(f"latest run aggregate code hash mismatch: {code}")
            for raw_code_path, expected_hash in code_files.items():
                relative_code_path = _safe_relative(
                    raw_code_path,
                    issues,
                    f"latest run code provenance {code}",
                )
                if relative_code_path is None:
                    continue
                code_path = bundle / Path(relative_code_path)
                if (
                    not code_path.is_file()
                    or not isinstance(expected_hash, str)
                    or sha256(code_path) != expected_hash
                ):
                    issues.append(
                        f"latest run code provenance does not match packaged code: "
                        f"{code}:{relative_code_path}"
                    )
            worker_path = provenance.get("worker_path") if isinstance(provenance, dict) else None
            if worker_path not in code_files:
                issues.append(f"latest run worker path is absent from code provenance: {code}")
        dependency_lock = provenance.get("dependency_lock") if isinstance(provenance, dict) else None
        if isinstance(dependency_lock, str):
            dependency_path = bundle / Path(dependency_lock)
            if (
                not dependency_path.is_file()
                or not isinstance(execution, dict)
                or execution.get("dependency_lock_sha256") != sha256(dependency_path)
            ):
                issues.append(f"latest run dependency lock provenance mismatch: {code}")
        else:
            issues.append(f"latest run dependency lock provenance is missing: {code}")

    analysis = inventory.get("latest_analysis_run")
    if not isinstance(analysis, dict):
        issues.append("manifest latest_analysis_run inventory is missing")
    else:
        analysis_id = analysis.get("run_id")
        analysis_root = f"b3/outputs/runs/{analysis_id}"
        expected_analysis_paths = {
            f"{analysis_root}/run.json",
            f"{analysis_root}/report.md",
        }
        actual_analysis_paths = {
            path for path in path_set if path.startswith("b3/outputs/runs/")
        }
        if (
            not isinstance(analysis_id, str)
            or analysis.get("path") != analysis_root
            or analysis.get("file_count") != 2
            or actual_analysis_paths != expected_analysis_paths
            or not all(curated_analysis_run_path(path) for path in actual_analysis_paths)
        ):
            issues.append("manifest latest analysis run inventory is inconsistent")


def _verify_manifest_files(
    bundle: Path,
    manifest: dict[str, Any],
    actual_files: set[str],
    issues: list[str],
) -> tuple[list[dict[str, Any]], set[str]]:
    raw_records = manifest.get("files")
    if not isinstance(raw_records, list):
        issues.append("manifest files must be a list")
        return [], set()
    records: list[dict[str, Any]] = []
    paths: list[str] = []
    for index, raw in enumerate(raw_records):
        if not isinstance(raw, dict):
            issues.append(f"manifest files[{index}] must be an object")
            continue
        relative = _safe_relative(raw.get("path"), issues, f"manifest files[{index}]")
        if relative is None:
            continue
        paths.append(relative)
        records.append(raw)
        reason = forbidden_path_reason(relative)
        if reason:
            issues.append(f"forbidden path in manifest: {relative}: {reason}")
        if not allowed_destination_path(relative):
            issues.append(f"path outside explicit bundle allowlist: {relative}")
        if relative not in actual_files:
            issues.append(f"manifest file is missing from bundle: {relative}")
            continue
        path = bundle / Path(relative)
        expected_bytes = raw.get("bytes")
        expected_hash = raw.get("sha256")
        if not isinstance(expected_bytes, int) or expected_bytes < 0:
            issues.append(f"invalid byte count in manifest: {relative}")
        elif path.stat().st_size != expected_bytes:
            issues.append(f"byte count mismatch: {relative}")
        if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            issues.append(f"invalid sha256 in manifest: {relative}")
        elif sha256(path) != expected_hash:
            issues.append(f"manifest sha256 mismatch: {relative}")
        source_path = raw.get("source_path")
        source_hash = raw.get("source_sha256")
        if source_path == "generated":
            if source_hash is not None:
                issues.append(f"generated file must not claim source_sha256: {relative}")
        else:
            normalized_source = _safe_relative(source_path, issues, f"source for {relative}")
            if normalized_source != relative:
                issues.append(f"source path must match packaged path: {relative}")
            if not isinstance(source_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", source_hash):
                issues.append(f"invalid source_sha256: {relative}")
        if raw.get("role") == "latest_agent_run_artifact" and raw.get(
            "transformed_for_portability"
        ) is not False:
            issues.append(f"latest run immutable artifact was transformed: {relative}")

    if paths != sorted(paths):
        issues.append("manifest files are not sorted by path")
    if len(paths) != len(set(paths)):
        issues.append("manifest contains duplicate file paths")
    path_set = set(paths)
    missing_required = sorted(REQUIRED_PAYLOAD - path_set)
    if missing_required:
        issues.append(f"required bundle payload is missing: {', '.join(missing_required)}")
    expected_actual = path_set | {"MANIFEST.json", "SHA256SUMS"}
    unlisted = sorted(actual_files - expected_actual)
    missing = sorted(expected_actual - actual_files)
    runtime_contract_valid = manifest.get("runtime_policy") == runtime_policy_payload()
    for relative in unlisted:
        if runtime_contract_valid and mutable_runtime_path(relative):
            continue
        reason = forbidden_path_reason(relative)
        if reason:
            issues.append(f"forbidden path present in bundle: {relative}: {reason}")
        else:
            issues.append(f"unlisted file present in bundle: {relative}")
    if missing:
        issues.append(f"listed bundle files are missing: {', '.join(missing)}")
    _verify_artifact_inventory(bundle, manifest, path_set, issues)
    return records, path_set


def _verify_source_snapshot(manifest: dict[str, Any], records: list[dict[str, Any]], issues: list[str]) -> None:
    source_records = [
        {"path": item.get("source_path"), "sha256": item.get("source_sha256")}
        for item in records
        if item.get("source_path") != "generated"
    ]
    source_records.sort(key=lambda item: str(item["path"]))
    actual = canonical_sha256(source_records)
    if actual != manifest.get("source_snapshot_sha256"):
        issues.append("source snapshot sha256 does not match manifest file provenance")


def _verify_machine_artifact_index(
    bundle: Path,
    manifest: dict[str, Any],
    records: list[dict[str, Any]],
    issues: list[str],
) -> None:
    try:
        actual = json.loads((bundle / "ARTIFACT_INDEX.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        issues.append(f"cannot read ARTIFACT_INDEX.json: {exc}")
        return
    inventory = manifest.get("artifact_inventory", {})
    latest = inventory.get("latest_agent_run", {}) if isinstance(inventory, dict) else {}
    if not isinstance(latest, dict):
        issues.append("cannot derive expected machine artifact index")
        return
    expected = artifact_index_payload(records, latest)
    if actual != expected:
        issues.append("ARTIFACT_INDEX.json does not exactly match MANIFEST records")


def _verify_representative_cases(
    bundle: Path,
    manifest_paths: set[str],
    issues: list[str],
) -> None:
    manifest_path = "b3/test_cases/manifest.json"
    try:
        value = json.loads((bundle / Path(manifest_path)).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        issues.append(f"cannot read representative test case manifest: {exc}")
        return
    if not isinstance(value, dict):
        issues.append("representative test case manifest must be a JSON object")
        return
    cases = value.get("cases")
    declared = {
        item.get("file")
        for item in cases
        if isinstance(item, dict) and isinstance(item.get("file"), str)
    } if isinstance(cases, list) else set()
    expected = {
        f"b3/test_cases/case_0{index}_{suffix}.json"
        for index, suffix in (
            (1, "cycle26_bounded_research_run"),
            (2, "polar_precursor_and_dynamo_toy_model"),
            (3, "f107_proxy_drift_guard"),
            (4, "evidence_query_h1"),
        )
    }
    packaged = {
        path
        for path in manifest_paths
        if path.startswith("b3/test_cases/") and path != manifest_path
    }
    if value.get("case_count") != 4 or declared != expected or packaged != expected:
        issues.append("representative test case manifest and packaged case set are inconsistent")


def _parse_sums(bundle: Path, issues: list[str]) -> dict[str, str]:
    path = bundle / "SHA256SUMS"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        issues.append(f"cannot read SHA256SUMS: {exc}")
        return {}
    sums: dict[str, str] = {}
    for number, line in enumerate(lines, 1):
        if "  " not in line:
            issues.append(f"invalid SHA256SUMS line {number}")
            continue
        digest, raw_relative = line.split("  ", 1)
        relative = _safe_relative(raw_relative, issues, f"SHA256SUMS line {number}")
        if relative is None:
            continue
        if relative in sums:
            issues.append(f"duplicate SHA256SUMS path: {relative}")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            issues.append(f"invalid SHA256SUMS digest: {relative}")
        sums[relative] = digest
    return sums


def _verify_sums(bundle: Path, actual_files: set[str], issues: list[str]) -> None:
    sums = _parse_sums(bundle, issues)
    expected = actual_files - {"SHA256SUMS"}
    if set(sums) != expected:
        missing = sorted(expected - set(sums))
        extra = sorted(set(sums) - expected)
        if missing:
            issues.append(f"SHA256SUMS is missing: {', '.join(missing)}")
        if extra:
            issues.append(f"SHA256SUMS has unknown paths: {', '.join(extra)}")
    for relative in sorted(set(sums) & expected):
        if sha256(bundle / Path(relative)) != sums[relative]:
            issues.append(f"SHA256SUMS sha256 mismatch: {relative}")


def _verify_revision_record(manifest: dict[str, Any], bundle: Path, issues: list[str]) -> None:
    try:
        lines = (bundle / "SOURCE_REVISION.txt").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        issues.append(f"cannot read SOURCE_REVISION.txt: {exc}")
        return
    values: dict[str, str] = {}
    for line in lines:
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    expected = {
        "SOURCE_REVISION": str(manifest.get("source_revision")),
        "SOURCE_TREE_DIRTY": str(manifest.get("source_tree_dirty")).lower(),
        "SOURCE_STATUS_SHA256": str(manifest.get("source_status_sha256")),
        "SOURCE_SNAPSHOT_SHA256": str(manifest.get("source_snapshot_sha256")),
    }
    for key, value in expected.items():
        if values.get(key) != value:
            issues.append(f"SOURCE_REVISION record mismatch: {key}")


def _git_revision(source_root: Path) -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip().lower()


def _verify_current_source_files(
    source_root: Path,
    records: list[dict[str, Any]],
    issues: list[str],
) -> bool:
    """Compare every packaged source receipt with the current source bytes."""

    source_root = source_root.resolve()
    before = len(issues)
    for record in records:
        source_path = record.get("source_path")
        if source_path == "generated":
            continue
        relative = _safe_relative(
            source_path,
            issues,
            f"current source for {record.get('path')}",
        )
        if relative is None:
            continue
        candidate = source_root / Path(relative)
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(source_root)
        except (OSError, ValueError):
            issues.append(f"current source path is missing or escaped: {relative}")
            continue
        if not resolved.is_file():
            issues.append(f"current source is not a file: {relative}")
            continue
        expected = record.get("source_sha256")
        actual = sha256(resolved)
        if actual != expected:
            issues.append(
                f"current source_sha256 mismatch: {relative}: "
                f"bundle={expected}, source={actual}"
            )
    return len(issues) == before


def _iter_json_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _iter_json_strings(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _iter_json_strings(item)


def _verify_text_safety(bundle: Path, paths: set[str], issues: list[str]) -> None:
    for relative in sorted(paths):
        path = bundle / Path(relative)
        if path.suffix.casefold() not in TEXT_SUFFIXES and path.name != "SHA256SUMS":
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (UnicodeError, OSError) as exc:
            issues.append(f"allowlisted text file is not valid UTF-8: {relative}: {exc}")
            continue
        for pattern in HIGH_CONFIDENCE_SECRET_PATTERNS:
            if pattern.search(text):
                issues.append(f"high-confidence secret material detected: {relative}")
        strings_to_scan = [text]
        if path.suffix.casefold() == ".json":
            try:
                strings_to_scan.extend(_iter_json_strings(json.loads(text)))
            except json.JSONDecodeError as exc:
                issues.append(f"allowlisted JSON file is invalid: {relative}: {exc}")
        local_path_found = False
        for candidate_text in strings_to_scan:
            match = ABSOLUTE_WINDOWS_PATH.search(candidate_text)
            if match is None:
                continue
            candidate = match.group(1)
            if candidate.casefold().startswith("c:\\users\\example"):
                continue
            issues.append(f"machine-local absolute path detected in {relative}: {candidate[:80]}")
            local_path_found = True
            break
        if local_path_found:
            continue


def _verify_entry_content(
    bundle: Path,
    manifest: dict[str, Any],
    issues: list[str],
) -> None:
    try:
        readme = (bundle / "README_先看.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        issues.append(f"cannot read README_先看.md: {exc}")
        return
    for command in sorted(README_OFFLINE_COMMANDS):
        if command not in readme:
            issues.append(f"README is missing offline reproduction command: {command}")
    live_ready = bool(manifest.get("offline_reproduction", {}).get("live_qwen_ready"))
    for boundary in (
        "无需 API Key",
        f"live_qwen_ready={str(live_ready).lower()}",
        "Fixture 通过不等于真实 Qwen 调用证明",
        "默认模型是固定快照 `dashscope/qwen3.7-max-2026-06-08`",
        "private 仓库",
        "# B3 三 Agent",
        "可脱离原工作区运行",
        "操作手册.md",
    ):
        if boundary not in readme:
            issues.append(f"README is missing readiness boundary: {boundary}")
    try:
        index = (bundle / "成果索引.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        issues.append(f"cannot read 成果索引.md: {exc}")
        index = ""
    for boundary in (
        "# B3 三 Agent 成果索引",
        "操作手册.md",
        "b3/final_report/b3_final_technical_report.pdf",
        "b3/outputs/b3_analysis_report.json",
        "b3/proofs/qwen_connection_check_live.json",
        "b3/proofs/pi_science_agents_live_eval.md",
        "开始使用",
        "API Key",
        "private",
    ):
        if boundary not in index:
            issues.append(f"artifact index is missing required boundary or link: {boundary}")
    inventory = manifest.get("artifact_inventory", {})
    latest = inventory.get("latest_agent_run", {}) if isinstance(inventory, dict) else {}
    if isinstance(latest, dict):
        for value in (latest.get("run_id"), latest.get("path")):
            if isinstance(value, str) and value not in index:
                issues.append("artifact index does not identify the manifest-selected latest run")
    latest_analysis = (
        inventory.get("latest_analysis_run", {}) if isinstance(inventory, dict) else {}
    )
    if isinstance(latest_analysis, dict):
        for value in (latest_analysis.get("run_id"), latest_analysis.get("path")):
            if isinstance(value, str) and value not in index:
                issues.append("artifact index does not identify the latest analysis run")
    try:
        manual = (bundle / "操作手册.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        issues.append(f"cannot read 操作手册.md: {exc}")
        manual = ""
    for boundary in (
        "# B3 三 Agent 操作手册 1.0",
        "不是只能回答同一个固定问题",
        "/b3-research-loop <你的有边界研究问题>",
        "b3/evals/question_diversity_cases.json",
        "runtime/",
        "private",
    ):
        if boundary not in manual:
            issues.append(f"operation manual is missing required content: {boundary}")
    try:
        verify_ps1 = (bundle / "VERIFY.ps1").read_text(encoding="utf-8-sig")
        if (
            "verify_three_agent_bundle.py" not in verify_ps1
            or "--source-root" not in verify_ps1
            or "--replay" not in verify_ps1
            or "-m venv" not in verify_ps1
            or "requirements-analysis.lock" not in verify_ps1
        ):
            issues.append("VERIFY.ps1 does not invoke the bundle verifier safely")
    except (OSError, UnicodeError) as exc:
        issues.append(f"cannot read VERIFY.ps1: {exc}")


def _run_replay(bundle: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    inventory = manifest.get("artifact_inventory", {})
    latest = inventory.get("latest_agent_run", {}) if isinstance(inventory, dict) else {}
    run_id = latest.get("run_id") if isinstance(latest, dict) else None
    specs = (
        ("pi_total_readiness", ["scripts_b3/verify_pi_agent_skills.py"]),
        ("pi_extension_contracts", ["scripts_b3/verify_pi_extensions.py"]),
        (
            "question_diversity_contract",
            ["scripts_b3/verify_question_diversity.py"],
        ),
        (
            "science_agent_unit_tests",
            ["-m", "unittest", "discover", "-s", "tests", "-p", "test_science_agents.py"],
        ),
        (
            "registered_experiment_tests",
            ["-m", "unittest", "tests.test_registered_experiments"],
        ),
        (
            "scientific_toolkit_tests",
            [
                "-m",
                "unittest",
                "tests.test_science_toolkit",
                "tests.test_qwen_adapter",
                "tests.test_qwen_connection",
            ],
        ),
        (
            "fixture_12_case_evaluation",
            ["scripts_b3/evaluate_pi_science_agents.py", "--mode", "fixture", "--no-write-proof"],
        ),
        (
            "latest_agent_run_validation",
            [
                "scripts_b3/science_agent_cli.py",
                "validate-run",
                "--run-id",
                str(run_id),
            ],
        ),
    )
    environment = {
        **os.environ,
        "DASHSCOPE_API_KEY": "",
        "QWEN_API_KEY": "",
        "B3_QWEN_ENABLED": "0",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    environment.pop("B3_RUNTIME_ROOT", None)
    records: list[dict[str, Any]] = []
    for gate_id, arguments in specs:
        completed = subprocess.run(
            [sys.executable, "-B", *arguments],
            cwd=bundle,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        records.append(
            {
                "id": gate_id,
                "command": ["python", *arguments],
                "exit_code": completed.returncode,
                "passed": completed.returncode == 0,
                "stdout_sha256": canonical_sha256(stdout),
                "stderr_sha256": canonical_sha256(stderr),
            }
        )
    return records


def _git_clean(source_root: Path) -> bool | None:
    completed = subprocess.run(
        ["git", "-C", str(source_root), "status", "--porcelain=v1", "--untracked-files=all"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if completed.returncode != 0:
        return None
    return not bool(completed.stdout.strip())


def verify_bundle(
    bundle: Path,
    source_root: Path | None = None,
    *,
    replay: bool = False,
    require_clean_source: bool = False,
    require_pristine: bool = False,
) -> dict[str, Any]:
    bundle = bundle.resolve()
    issues: list[str] = []
    if not bundle.is_dir():
        return {
            "schema_version": MANIFEST_SCHEMA,
            "passed": False,
            "bundle": str(bundle),
            "source_revision_verified": False,
            "source_snapshot_verified": False,
            "latest_run_source_verified": False,
            "latest_analysis_source_verified": False,
            "verified_file_count": 0,
            "issues": ["project bundle directory does not exist"],
        }
    if bundle.name != BUNDLE_NAME:
        issues.append(f"bundle directory must be named {BUNDLE_NAME}")
    actual_files = _walk_bundle(bundle, issues)
    actual_directories = _walk_bundle_directories(bundle)
    missing_top = sorted(REQUIRED_TOP_LEVEL - actual_files)
    if missing_top:
        issues.append(f"required bundle entries are missing: {', '.join(missing_top)}")
    manifest = _parse_manifest(bundle, issues) if "MANIFEST.json" in actual_files else {}
    records, manifest_paths = _verify_manifest_files(bundle, manifest, actual_files, issues)
    runtime_contract_valid = manifest.get("runtime_policy") == runtime_policy_payload()
    runtime_files = {
        relative
        for relative in actual_files - manifest_paths - {"MANIFEST.json", "SHA256SUMS"}
        if runtime_contract_valid and mutable_runtime_path(relative)
    }
    runtime_directories = {
        relative
        for relative in actual_directories
        if runtime_contract_valid and mutable_runtime_path(relative)
    }
    if require_pristine and (runtime_files or runtime_directories):
        issues.append(
            "pristine bundle required but runtime workspace is present: "
            + ", ".join(sorted(runtime_files | runtime_directories))
        )
    expected_directories = _expected_parent_directories(actual_files)
    extra_directories = sorted(actual_directories - expected_directories)
    for relative in extra_directories:
        if runtime_contract_valid and allowed_runtime_directory(relative):
            continue
        try:
            reason = (
                forbidden_runtime_path_reason(f"{relative}/placeholder")
                if runtime_contract_valid and mutable_runtime_path(relative)
                else forbidden_path_reason(f"{relative}/placeholder")
            )
        except BundleBuildError as exc:
            issues.append(f"unsafe bundle directory: {exc}")
            continue
        if reason:
            issues.append(f"forbidden empty or unlisted directory present: {relative}: {reason}")
        else:
            issues.append(f"unlisted empty directory present in bundle: {relative}")
    for relative in sorted(actual_files):
        try:
            reason = (
                forbidden_runtime_path_reason(relative)
                if relative in runtime_files
                else forbidden_path_reason(relative)
            )
        except BundleBuildError as exc:
            issues.append(f"unsafe bundle path: {exc}")
            continue
        if reason:
            issues.append(f"forbidden path present in bundle: {relative}: {reason}")
        elif (
            relative in runtime_files
            and (bundle / Path(relative)).stat().st_size > MAX_RUNTIME_FILE_BYTES
        ):
            issues.append(f"runtime file exceeds size limit: {relative}")
    if manifest:
        _verify_source_snapshot(manifest, records, issues)
        _verify_revision_record(manifest, bundle, issues)
        _verify_machine_artifact_index(bundle, manifest, records, issues)
        _verify_representative_cases(bundle, manifest_paths, issues)
    canonical_actual_files = actual_files - runtime_files
    if "SHA256SUMS" in actual_files:
        _verify_sums(bundle, canonical_actual_files, issues)
    _verify_text_safety(bundle, actual_files, issues)
    _verify_entry_content(bundle, manifest, issues)

    revision_verified = False
    source_snapshot_verified = False
    latest_run_source_verified = False
    latest_analysis_source_verified = False
    if source_root is not None:
        source_root = source_root.resolve()
        source_snapshot_verified = _verify_current_source_files(
            source_root,
            records,
            issues,
        )
        current = _git_revision(source_root)
        expected = manifest.get("source_revision") if manifest else None
        if current is None:
            issues.append("source revision cannot be read from the requested source repository")
        elif current != expected:
            issues.append(f"source revision mismatch: bundle={expected}, repository={current}")
        elif manifest.get("source_tree_dirty") is True:
            revision_verified = False
        else:
            revision_verified = True
        if require_clean_source:
            current_clean = _git_clean(source_root)
            if manifest.get("source_tree_dirty") is True:
                issues.append("final bundle records a dirty source tree")
            if current_clean is not True:
                issues.append("final bundle verification requires a clean source repository")
        try:
            current_latest = _latest_complete_agent_run(source_root)
            _validate_agent_run(source_root, current_latest)
            inventory = manifest.get("artifact_inventory", {}) if manifest else {}
            packaged_latest = (
                inventory.get("latest_agent_run", {})
                if isinstance(inventory, dict)
                else {}
            )
            latest_run_source_verified = (
                isinstance(packaged_latest, dict)
                and packaged_latest.get("run_id") == current_latest.get("run_id")
                and packaged_latest.get("path") == current_latest.get("path")
            )
            if not latest_run_source_verified:
                issues.append(
                    "bundle does not contain the newest complete immutable agent run"
                )
        except BundleBuildError as exc:
            issues.append(f"current latest agent run cannot be verified: {exc}")
        try:
            current_analysis = _latest_analysis_run(source_root)
            inventory = manifest.get("artifact_inventory", {}) if manifest else {}
            packaged_analysis = (
                inventory.get("latest_analysis_run", {})
                if isinstance(inventory, dict)
                else {}
            )
            latest_analysis_source_verified = (
                isinstance(packaged_analysis, dict)
                and packaged_analysis.get("run_id") == current_analysis.get("run_id")
                and packaged_analysis.get("path") == current_analysis.get("path")
            )
            if not latest_analysis_source_verified:
                issues.append("bundle does not contain the newest complete analysis run")
        except BundleBuildError as exc:
            issues.append(f"current latest analysis run cannot be verified: {exc}")

    replay_commands: list[dict[str, Any]] = []
    if replay and not issues:
        pre_replay_hashes = {
            relative: sha256(bundle / Path(relative)) for relative in actual_files
        }
        replay_commands = _run_replay(bundle, manifest)
        for record in replay_commands:
            if not record["passed"]:
                issues.append(f"offline replay failed: {record['id']}")
        replay_files = _walk_bundle(bundle, issues)
        replay_directories = _walk_bundle_directories(bundle)
        post_replay_hashes = {
            relative: sha256(bundle / Path(relative)) for relative in replay_files
        }
        if replay_files != actual_files:
            added = sorted(replay_files - actual_files)
            removed = sorted(actual_files - replay_files)
            issues.append(
                "offline replay mutated bundle file set: "
                f"added={added}, removed={removed}"
            )
        if replay_directories != actual_directories:
            issues.append(
                "offline replay mutated bundle directory set: "
                f"added={sorted(replay_directories - actual_directories)}, "
                f"removed={sorted(actual_directories - replay_directories)}"
            )
        if post_replay_hashes != pre_replay_hashes:
            issues.append("offline replay mutated one or more bundle file hashes")
        replay_runtime_files = {
            relative
            for relative in replay_files - manifest_paths - {"MANIFEST.json", "SHA256SUMS"}
            if runtime_contract_valid and mutable_runtime_path(relative)
        }
        _verify_sums(bundle, replay_files - replay_runtime_files, issues)

    # De-duplicate while preserving diagnostic order.
    issues = list(dict.fromkeys(issues))
    return {
        "schema_version": MANIFEST_SCHEMA,
        "passed": not issues,
        "bundle": str(bundle),
        "source_revision": manifest.get("source_revision") if manifest else None,
        "source_revision_verified": revision_verified,
        "source_snapshot_verified": source_snapshot_verified,
        "latest_run_source_verified": latest_run_source_verified,
        "latest_analysis_source_verified": latest_analysis_source_verified,
        "source_tree_dirty": manifest.get("source_tree_dirty") if manifest else None,
        "runtime_present": bool(runtime_files or runtime_directories),
        "runtime_artifacts_unverified": bool(runtime_files),
        "replay_requested": replay,
        "replay_passed": replay
        and bool(replay_commands)
        and all(item["passed"] for item in replay_commands)
        and not issues,
        "replay_commands": replay_commands,
        "verified_file_count": len(actual_files),
        "manifest_file_count": len(manifest_paths),
        "runtime_file_count": len(runtime_files),
        "issues": issues,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", nargs="?", type=Path, default=ROOT_DEFAULT())
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--require-clean-source", action="store_true")
    parser.add_argument("--require-pristine", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def ROOT_DEFAULT() -> Path:
    candidate = Path(__file__).resolve().parents[1]
    if (candidate / "MANIFEST.json").is_file():
        return candidate
    return candidate / "dist" / BUNDLE_NAME


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = verify_bundle(
        args.bundle,
        args.source_root,
        replay=args.replay,
        require_clean_source=args.require_clean_source,
        require_pristine=args.require_pristine,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("B3 三 Agent 验证通过。" if report["passed"] else "B3 三 Agent 验证失败。")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
