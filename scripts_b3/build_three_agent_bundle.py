#!/usr/bin/env python3
"""Build the auditable, keyless bundle for the three B3 Pi agents."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence

# The builder is itself shipped in an immutable bundle. Keep direct invocations
# from creating sibling __pycache__ state before the verifier can inspect it.
sys.dont_write_bytecode = True

from verify_pi_agent_skills import (
    readiness_proof_matches_report,
    verify_pi_agent_skills,
)


ROOT = Path(__file__).resolve().parents[1]
BUNDLE_NAME = "B3_三Agent"
MANIFEST_SCHEMA = "b3-three-agent-bundle-v3"
LEGACY_MANIFEST_SCHEMAS = {
    "b3-three-agent-bundle-v1",
    "b3-three-agent-bundle-v2",
}
MAX_BUNDLE_FILE_BYTES = 2 * 1024 * 1024
MAX_RUNTIME_FILE_BYTES = 8 * 1024 * 1024
EXPECTED_EXPERIMENT_CODES = frozenset(f"E{index}" for index in range(9))

# Files present at build time are immutable release evidence.  Standalone use
# may add new runs and credential-safe proofs below these roots; they remain in
# the portable directory but are deliberately outside MANIFEST/SHA256SUMS.
MUTABLE_RUNTIME_ROOTS = (
    "runtime/",
)
RUNTIME_ALLOWED_SUBTREES = (
    "runtime/agent_runs/",
    "runtime/outputs/",
    "runtime/data/processed/",
    "runtime/proofs/",
    "runtime/logs/",
)
RUNTIME_ALLOWED_SUFFIXES = frozenset(
    {".csv", ".html", ".json", ".log", ".md", ".pdf", ".png", ".txt"}
)

GENERATED_PATHS = {
    "README_先看.md",
    "成果索引.md",
    "ARTIFACT_INDEX.json",
    "VERIFY.ps1",
    "SOURCE_REVISION.txt",
    "MANIFEST.json",
    "SHA256SUMS",
}

TREE_RULES: tuple[tuple[str, frozenset[str], str], ...] = (
    (".pi/agents", frozenset({".md"}), "pi_agent_contract"),
    (".pi/extensions/b3-science", frozenset({".ts"}), "pi_science_extension"),
    (".pi/prompts", frozenset({".md"}), "pi_prompt"),
    (".pi/skills/research-planner-agent", frozenset({".md"}), "pi_skill"),
    (".pi/skills/experiment-agent", frozenset({".md"}), "pi_skill"),
    (".pi/skills/hypothesis-agent", frozenset({".md"}), "pi_skill"),
    ("src/b3cycle", frozenset({".py"}), "python_runtime"),
    ("b3/specs", frozenset({".json"}), "artifact_contract"),
    ("b3/evals", frozenset({".json"}), "evaluation_fixture"),
    (
        "b3/data/raw",
        frozenset({".json", ".csv", ".html"}),
        "small_public_source_data",
    ),
    (
        "static_b3",
        frozenset({".html", ".js", ".css"}),
        "frontend_static_asset",
    ),
)

EXACT_RULES: dict[str, str] = {
    "操作手册.md": "operation_manual",
    ".pi/extensions/dashscope-provider.ts": "dashscope_provider",
    ".pi/docs/three-agent-skills.md": "pi_skill_entry",
    ".pi/APPEND_SYSTEM.md": "pi_project_system_context",
    ".pi/settings.json": "pi_project_settings",
    "requirements.txt": "python_dependency_manifest",
    "requirements-analysis.lock": "isolated_worker_dependency_lock",
    "configs/qwen.env.example": "configuration_example",
    "configs/final_submission.env.example": "configuration_example",
    "scripts_b3/evaluate_pi_science_agents.py": "offline_evaluator",
    "scripts_b3/check_qwen_connection.py": "qwen_connection_checker",
    "scripts/check_qwen_connection.py": "qwen_connection_checker",
    "scripts_b3/run_analysis_worker.py": "isolated_worker",
    "scripts_b3/science_agent_cli.py": "science_agent_cli",
    "scripts_b3/start_qwen_max_pi.ps1": "secure_pi_launcher",
    "scripts_b3/run_b3_analysis.py": "offline_analysis_entry",
    "scripts_b3/check_frontend_api_smoke.py": "frontend_api_verifier",
    "scripts_b3/verify_pi_agent_skills.py": "pi_contract_verifier",
    "scripts_b3/verify_question_diversity.py": "question_diversity_verifier",
    "scripts_b3/verify_pi_extensions.py": "pi_contract_verifier",
    "scripts_b3/verify_pi_project_paths.mjs": "pi_security_verifier",
    "scripts_b3/verify_pi_science_cli_isolation.mjs": "pi_security_verifier",
    "scripts_b3/verify_pi_child_policy.mjs": "pi_security_verifier",
    "scripts_b3/verify_pi_child_event_stream.mjs": "pi_security_verifier",
    "scripts_b3/verify_pi_agent_loader.mjs": "pi_security_verifier",
    "scripts_b3/verify_dashscope_provider.mjs": "pi_security_verifier",
    "scripts_b3/build_three_agent_bundle.py": "bundle_builder",
    "scripts_b3/verify_three_agent_bundle.py": "bundle_verifier",
    "tests/test_science_agents.py": "science_agent_tests",
    "tests/test_registered_experiments.py": "registered_experiment_tests",
    "tests/test_science_toolkit.py": "scientific_toolkit_tests",
    "tests/test_qwen_adapter.py": "qwen_adapter_security_tests",
    "tests/test_qwen_connection.py": "qwen_connection_security_tests",
    "tests/test_pi_science_agent_readiness.py": "readiness_verifier_tests",
    "tests/test_question_generalization.py": "question_generalization_tests",
    "tests/test_three_agent_bundle.py": "bundle_tests",
    "b3/docs/pi_three_agents_quickstart.md": "three_agent_quickstart",
    "b3/docs/三Agent_研究背景与设计说明.md": "research_background",
    "b3/docs/三Agent_模型配置与隐私操作手册.md": "model_and_privacy_manual",
    "b3/docs/三Agent_现状缺口与未来工作计划.md": "future_work_plan",
    "b3/docs/最终提交审计清单.json": "submission_audit",
    "b3/docs/最终提交审计清单.md": "submission_audit",
    "b3/docs/评审速览.md": "reviewer_quicklook",
    "b3/docs/quality_standard.md": "quality_standard",
    "b3/docs/提交包使用与演示说明.md": "submission_and_demo_guide",
    "b3/docs/10分钟演示脚本.md": "demo_script",
    "b3/docs/part4_system_architecture_agent_design.md": "architecture_report",
    "b3/docs/model_integration_and_openapi.md": "model_integration_report",
    "b3/docs/representative_test_cases.md": "test_case_guide",
    "b3/docs/materials_map.md": "materials_map",
    "b3/docs/release_readiness_audit.json": "release_readiness_audit",
    "b3/docs/release_readiness_audit.md": "release_readiness_audit",
    "b3/docs/requirements_alignment.md": "requirements_alignment",
    "b3/docs/agent_contracts_and_prompt_protocol.md": "agent_contract_reference",
    "b3/docs/pi_agent_subagents_reference.md": "pi_subagent_reference",
    "b3/docs/第四部分_系统架构与子Agent技术设计_正式稿.md": "formal_architecture_report",
    "b3/docs/第四部分_系统架构与子Agent技术设计_正式稿.assets/架构图-1782035895607-3.png": "formal_architecture_figure",
    "b3/docs/第四部分_系统架构与子Agent技术设计_正式稿.assets/状态转移图.png": "formal_architecture_figure",
    "b3/outputs/b3_analysis_report.json": "analysis_report",
    "b3/final_report/b3_final_technical_report.md": "final_technical_report",
    "b3/final_report/b3_final_technical_report.pdf": "final_technical_report",
    "b3/final_report/figures/fig01_cycle_peak_timeline.png": "final_report_figure",
    "b3/final_report/figures/fig02_polar_toy_model.png": "final_report_figure",
    "b3/final_report/figures/fig03_hypothesis_ranking.png": "final_report_figure",
    "b3/final_report/figures/fig04_closed_loop_architecture.png": "final_report_figure",
    "b3/test_cases/manifest.json": "representative_test_case",
    "b3/test_cases/case_01_cycle26_bounded_research_run.json": "representative_test_case",
    "b3/test_cases/case_02_polar_precursor_and_dynamo_toy_model.json": "representative_test_case",
    "b3/test_cases/case_03_f107_proxy_drift_guard.json": "representative_test_case",
    "b3/test_cases/case_04_evidence_query_h1.json": "representative_test_case",
    "b3/proofs/pi_science_agents_eval.json": "offline_evaluation_proof",
    "b3/proofs/pi_science_agents_eval.md": "offline_evaluation_proof",
    "b3/proofs/pi_science_agents_live_eval.json": "live_readiness_boundary",
    "b3/proofs/pi_science_agents_live_eval.md": "live_readiness_boundary",
    "b3/proofs/pi_science_agents_readiness.json": "three_agent_readiness_proof",
    "b3/proofs/question_diversity.json": "question_diversity_proof",
    "b3/proofs/question_diversity.md": "question_diversity_proof",
    "b3/proofs/frontend_api_smoke.json": "frontend_api_proof",
    "b3/proofs/frontend_api_smoke.md": "frontend_api_proof",
    "b3/proofs/frontend_visual_desktop_1440.png": "frontend_visual_proof",
    "b3/proofs/frontend_visual_mobile_390.png": "frontend_visual_proof",
    "b3/proofs/frontend_visual_qa.json": "frontend_visual_proof",
    "b3/proofs/frontend_visual_qa.md": "frontend_visual_proof",
    "b3/proofs/qwen_connection_check_dry_run.json": "qwen_connection_proof",
    "b3/proofs/qwen_connection_check_dry_run.md": "qwen_connection_proof",
    "b3/proofs/qwen_connection_check_live.json": "qwen_connection_proof",
    "b3/proofs/qwen_connection_check_live.md": "qwen_connection_proof",
    "b3/proofs/submission_pipeline_run.json": "historical_source_pipeline_record",
    "b3/proofs/submission_pipeline_run.md": "historical_source_pipeline_record",
    "验收B3三Agent.ps1": "chinese_bundle_entry",
    "启动B3前端与API.ps1": "frontend_launch_entry",
    "验收B3前端与API.ps1": "frontend_verification_entry",
    "生成百炼Live证明并重打包.ps1": "live_proof_entry",
    "app_b3.py": "frontend_api_server",
}

# These files are created by the documentation/readiness task later in the same
# feature branch.  Include them automatically when present, without broadening
# the directory allowlist.
OPTIONAL_EXACT_RULES: dict[str, str] = {}

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

FORBIDDEN_COMPONENTS = {
    ".git",
    ".codex",
    ".agents",
    ".ssh",
    "__pycache__",
    "node_modules",
    "pi_live_traces",
    "submission_release",
    "submission_archives",
    "最新参考材料7.11",
}
FORBIDDEN_SUFFIXES = {
    ".7z",
    ".avi",
    ".fit",
    ".fits",
    ".fts",
    ".key",
    ".mkv",
    ".mov",
    ".mp4",
    ".p12",
    ".pem",
    ".pfx",
    ".ppk",
    ".rar",
    ".zip",
}

HIGH_CONFIDENCE_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|glpat-[A-Za-z0-9_-]{16,})\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    # The provider verifier deliberately contains the fixed sentinel
    # ``user:password`` to prove credential-bearing URLs are rejected.  Keep
    # that one non-secret fixture while still blocking every other userinfo URL.
    re.compile(r"https?://(?!user:password@)[^\s/:@]+:[^\s/@]+@", re.IGNORECASE),
)
PORTABLE_WINDOWS_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Z]:[\\/](?![\\/])[A-Za-z0-9_.\u4e00-\u9fff][^\r\n`\"']*)"
)


class BundleBuildError(RuntimeError):
    """A safe project bundle cannot be produced from the requested source tree."""


def curated_agent_run_path(relative: str) -> bool:
    """Allow only the reviewable JSON surface of one science run.

    Raw tool receipts, traces, logs, arbitrary attachments, and nested files are
    deliberately outside this grammar even though they may live below
    ``b3/agent_runs`` in the working repository.
    """

    try:
        normalized = normalize_relative_path(relative)
    except BundleBuildError:
        return False
    parts = PurePosixPath(normalized).parts
    if len(parts) == 4 and parts[:2] == ("b3", "agent_runs"):
        return bool(parts[2]) and parts[3] in {
            "run_manifest.json",
            "research_plan.json",
            "hypothesis_portfolio.json",
        }
    if len(parts) == 6 and parts[:2] == ("b3", "agent_runs"):
        experiment_code = parts[4].split("_", 1)[0]
        return (
            bool(parts[2])
            and parts[3] == "experiments"
            and experiment_code in EXPECTED_EXPERIMENT_CODES
            and parts[5] in {"manifest.json", "result.json"}
        )
    return False


def curated_analysis_run_path(relative: str) -> bool:
    try:
        normalized = normalize_relative_path(relative)
    except BundleBuildError:
        return False
    parts = PurePosixPath(normalized).parts
    return (
        len(parts) == 5
        and parts[:3] == ("b3", "outputs", "runs")
        and bool(parts[3])
        and parts[4] in {"run.json", "report.md"}
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def normalize_relative_path(value: str) -> str:
    candidate = value.replace("\\", "/")
    pure = PurePosixPath(candidate)
    if (
        not candidate
        or pure.is_absolute()
        or candidate != pure.as_posix()
        or any(part in {"", ".", ".."} for part in pure.parts)
        or re.match(r"^[A-Za-z]:", candidate)
    ):
        raise BundleBuildError(f"unsafe relative path: {value}")
    return candidate


def mutable_runtime_path(relative: str) -> bool:
    """Return whether a safe relative path belongs to the runtime workspace."""

    normalized = normalize_relative_path(relative)
    return any(
        normalized == root.rstrip("/") or normalized.startswith(root)
        for root in MUTABLE_RUNTIME_ROOTS
    )


def allowed_runtime_directory(relative: str) -> bool:
    normalized = normalize_relative_path(relative)
    if normalized in {root.rstrip("/") for root in MUTABLE_RUNTIME_ROOTS}:
        return True
    return any(
        normalized == root.rstrip("/") or normalized.startswith(root)
        for root in RUNTIME_ALLOWED_SUBTREES
    )


def runtime_policy_payload() -> dict[str, Any]:
    return {
        "roots": list(MUTABLE_RUNTIME_ROOTS),
        "allowed_subtrees": list(RUNTIME_ALLOWED_SUBTREES),
        "allowed_suffixes": sorted(RUNTIME_ALLOWED_SUFFIXES),
        "maximum_file_bytes": MAX_RUNTIME_FILE_BYTES,
        "integrity_scope": "excluded_from_manifest_artifact_index_and_sha256",
        "safety_scan": True,
        "final_upload_requires_pristine": True,
    }


def forbidden_runtime_path_reason(relative: str) -> str | None:
    """Apply privacy/type guards without requiring release-time run curation."""

    normalized = normalize_relative_path(relative)
    if not mutable_runtime_path(normalized):
        return "outside declared mutable runtime roots"
    if not any(
        normalized == root.rstrip("/") or normalized.startswith(root)
        for root in RUNTIME_ALLOWED_SUBTREES
    ):
        return "outside allowlisted runtime artifact subtrees"
    pure = PurePosixPath(normalized)
    folded_parts = {part.casefold() for part in pure.parts}
    forbidden_folded = {part.casefold() for part in FORBIDDEN_COMPONENTS}
    if folded_parts & forbidden_folded:
        return "machine-local, generated, or large-material directory"
    name = pure.name.casefold()
    suffix = pure.suffix.casefold()
    if suffix not in RUNTIME_ALLOWED_SUFFIXES:
        return "runtime file type is not allowlisted"
    if name == ".env" or (name.startswith(".env.") and not name.endswith(".example")):
        return "environment secret file"
    if suffix in FORBIDDEN_SUFFIXES:
        return "forbidden large or credential-bearing file type"
    if "private_remote_visibility" in name or "private_remote_evidence" in name:
        return "private remote visibility evidence"
    if name.startswith(("credentials", "secrets")) or name in {
        "id_rsa",
        "id_ed25519",
        "user.config",
    }:
        return "credential or machine-local configuration"
    return None


def forbidden_path_reason(relative: str) -> str | None:
    normalized = normalize_relative_path(relative)
    pure = PurePosixPath(normalized)
    folded_parts = {part.casefold() for part in pure.parts}
    forbidden_folded = {part.casefold() for part in FORBIDDEN_COMPONENTS}
    if folded_parts & forbidden_folded:
        return "machine-local, generated, or large-material directory"
    if "agent_runs" in folded_parts and not curated_agent_run_path(normalized):
        return "uncurated agent run state, receipt, trace, or attachment"
    name = pure.name.casefold()
    suffix = pure.suffix.casefold()
    if name == ".env" or (name.startswith(".env.") and not name.endswith(".example")):
        return "environment secret file"
    if suffix in FORBIDDEN_SUFFIXES:
        return "forbidden large or credential-bearing file type"
    if "private_remote_visibility" in name or "private_remote_evidence" in name:
        return "private remote visibility evidence"
    if name.startswith(("credentials", "secrets")) or name in {
        "id_rsa",
        "id_ed25519",
        "user.config",
    }:
        return "credential or machine-local configuration"
    return None


def allowed_destination_path(relative: str) -> bool:
    try:
        normalized = normalize_relative_path(relative)
    except BundleBuildError:
        return False
    if (
        normalized in GENERATED_PATHS
        or normalized in EXACT_RULES
        or normalized in OPTIONAL_EXACT_RULES
        or curated_agent_run_path(normalized)
        or curated_analysis_run_path(normalized)
    ):
        return True
    path = PurePosixPath(normalized)
    for prefix, suffixes, _role in TREE_RULES:
        prefix_path = PurePosixPath(prefix)
        try:
            path.relative_to(prefix_path)
        except ValueError:
            continue
        return path.suffix.casefold() in suffixes
    return False


def _git(source_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(source_root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise BundleBuildError(f"Git command failed: {' '.join(args)}: {detail}")
    return completed.stdout.strip()


def git_state(source_root: Path) -> dict[str, Any]:
    top = Path(_git(source_root, "rev-parse", "--show-toplevel")).resolve()
    if top != source_root.resolve():
        raise BundleBuildError(f"source root must be the Git top-level directory: {top}")
    revision = _git(source_root, "rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", revision):
        raise BundleBuildError("Git returned an invalid source revision")
    status = _git(source_root, "status", "--porcelain=v1", "--untracked-files=all")
    return {
        "revision": revision.lower(),
        "commit_timestamp": _git(source_root, "show", "-s", "--format=%cI", "HEAD"),
        "tree_dirty": bool(status),
        "status_sha256": hashlib.sha256(status.encode("utf-8")).hexdigest(),
    }


def _is_text_path(path: Path) -> bool:
    return path.suffix.casefold() in TEXT_SUFFIXES or path.name == "SHA256SUMS"


def _sanitize_text(text: str, source_root: Path) -> str:
    replacements = {
        str(source_root.resolve()),
        str(source_root.resolve()).replace("\\", "/"),
        str(Path.home().resolve()),
        str(Path.home().resolve()).replace("\\", "/"),
    }
    # Replace longer strings first so a project path beneath the home directory
    # is not partially rewritten.
    for value in sorted(replacements, key=len, reverse=True):
        if value:
            marker = "<PROJECT_ROOT>" if value.casefold().endswith(source_root.name.casefold()) else "<USER_HOME>"
            text = text.replace(value, marker)

    def replace_local_path(match: re.Match[str]) -> str:
        candidate = match.group(1)
        # This fixed, non-existent example is an executable security-test
        # sentinel, not machine state.  Rewriting it would weaken A08.
        if candidate.casefold().startswith("c:\\users\\example"):
            return candidate
        return "<LOCAL_PATH>"

    return PORTABLE_WINDOWS_PATH_RE.sub(replace_local_path, text)


def _sanitize_json_value(value: Any, source_root: Path) -> tuple[Any, bool]:
    if isinstance(value, str):
        sanitized = _sanitize_text(value, source_root)
        return sanitized, sanitized != value
    if isinstance(value, list):
        changed = False
        output = []
        for item in value:
            sanitized, item_changed = _sanitize_json_value(item, source_root)
            output.append(sanitized)
            changed = changed or item_changed
        return output, changed
    if isinstance(value, dict):
        changed = False
        output: dict[str, Any] = {}
        for key, item in value.items():
            sanitized_key, key_changed = _sanitize_json_value(str(key), source_root)
            sanitized_item, item_changed = _sanitize_json_value(item, source_root)
            if sanitized_key in output:
                raise BundleBuildError("JSON path sanitization produced a duplicate key")
            output[sanitized_key] = sanitized_item
            changed = changed or key_changed or item_changed
        return output, changed
    return value, False


def _sanitize_json_text(text: str, source_root: Path, relative: str) -> str:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BundleBuildError(f"invalid JSON in allowlisted source {relative}: {exc}") from exc
    sanitized, changed = _sanitize_json_value(value, source_root)
    if not changed:
        return text
    return json.dumps(sanitized, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def _scan_high_confidence_secrets(text: str, relative: str) -> None:
    for pattern in HIGH_CONFIDENCE_SECRET_PATTERNS:
        if pattern.search(text):
            raise BundleBuildError(f"high-confidence secret material detected in {relative}")
    if relative.startswith("configs/"):
        for line in text.splitlines():
            match = re.match(
                r"\s*([A-Za-z0-9_-]*(?:API_KEY|TOKEN|PASSWORD|SECRET)[A-Za-z0-9_-]*)\s*=\s*(.*)\s*$",
                line,
                re.IGNORECASE,
            )
            if not match:
                continue
            value = match.group(2).strip().strip('"\'')
            safe = (
                not value
                or value.casefold().startswith(("replace_", "example", "your_", "placeholder"))
                or value.startswith(("${", "$", "<"))
            )
            if not safe:
                raise BundleBuildError(f"non-placeholder secret assignment detected in {relative}")


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BundleBuildError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise BundleBuildError(f"{label} must contain one JSON object")
    return value


def _parse_created_at(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise BundleBuildError(f"{label} created_at must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BundleBuildError(f"{label} created_at is invalid") from exc
    if parsed.tzinfo is None:
        raise BundleBuildError(f"{label} created_at must include a timezone")
    return parsed


def _latest_complete_agent_run(source_root: Path) -> dict[str, Any]:
    """Select the newest complete E0-E8 run, not merely the newest run folder."""

    runs_root = source_root / "b3" / "agent_runs"
    if not runs_root.is_dir():
        raise BundleBuildError("required agent run directory is missing: b3/agent_runs")
    candidates: list[dict[str, Any]] = []
    for directory in sorted(path for path in runs_root.iterdir() if path.is_dir()):
        top_files = {
            name: directory / name
            for name in (
                "run_manifest.json",
                "research_plan.json",
                "hypothesis_portfolio.json",
            )
        }
        if not all(path.is_file() for path in top_files.values()):
            continue
        experiments_root = directory / "experiments"
        if not experiments_root.is_dir():
            continue
        experiment_dirs: dict[str, Path] = {}
        complete = True
        for experiment in sorted(path for path in experiments_root.iterdir() if path.is_dir()):
            code = experiment.name.split("_", 1)[0]
            if code not in EXPECTED_EXPERIMENT_CODES:
                continue
            if code in experiment_dirs:
                complete = False
                break
            if not all((experiment / name).is_file() for name in ("manifest.json", "result.json")):
                complete = False
                break
            experiment_dirs[code] = experiment
        if not complete or set(experiment_dirs) != set(EXPECTED_EXPERIMENT_CODES):
            continue

        run_manifest = _read_json_object(top_files["run_manifest.json"], "run manifest")
        if run_manifest.get("schema_version") != "b3-science-run-v2":
            continue
        run_id = run_manifest.get("run_id")
        if run_id != directory.name:
            continue
        created = _parse_created_at(run_manifest.get("created_at"), "run manifest")
        task = run_manifest.get("task")
        if not isinstance(task, str) or not task.strip():
            continue

        plan = _read_json_object(top_files["research_plan.json"], "research plan")
        portfolio = _read_json_object(
            top_files["hypothesis_portfolio.json"],
            "hypothesis portfolio",
        )
        if plan.get("run_id") != run_id or portfolio.get("run_id") != run_id:
            continue

        relative_root = directory.relative_to(source_root).as_posix()
        files = [
            f"{relative_root}/run_manifest.json",
            f"{relative_root}/research_plan.json",
            f"{relative_root}/hypothesis_portfolio.json",
        ]
        statuses: list[dict[str, str]] = []
        for code in sorted(EXPECTED_EXPERIMENT_CODES, key=lambda item: int(item[1:])):
            experiment = experiment_dirs[code]
            manifest_path = experiment / "manifest.json"
            result_path = experiment / "result.json"
            experiment_manifest = _read_json_object(
                manifest_path,
                f"{code} experiment manifest",
            )
            result = _read_json_object(result_path, f"{code} experiment result")
            status = result.get("status", experiment_manifest.get("status", "unknown"))
            if not isinstance(status, str) or not status:
                status = "unknown"
            statuses.append(
                {
                    "code": code,
                    "directory": experiment.name,
                    "status": status,
                }
            )
            files.extend(
                [
                    manifest_path.relative_to(source_root).as_posix(),
                    result_path.relative_to(source_root).as_posix(),
                ]
            )
        if not all(curated_agent_run_path(relative) for relative in files):
            raise BundleBuildError("selected run contains a path outside the curated run grammar")
        candidates.append(
            {
                "run_id": run_id,
                "path": relative_root,
                "created_at": run_manifest["created_at"],
                "created_sort": created,
                "task": task.strip(),
                "plan_status": str(plan.get("status", "unknown")),
                "portfolio_status": str(portfolio.get("status", "unknown")),
                "experiment_statuses": statuses,
                "files": sorted(files),
            }
        )
    if not candidates:
        raise BundleBuildError(
            "no complete E0-E8 three-agent run is available for the final deliverable"
        )
    latest = max(candidates, key=lambda item: (item["created_sort"], item["run_id"]))
    latest.pop("created_sort")
    return latest


def _validate_agent_run(source_root: Path, latest_run: dict[str, Any]) -> dict[str, Any]:
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            str(source_root / "scripts_b3" / "science_agent_cli.py"),
            "validate-run",
            "--run-id",
            latest_run["run_id"],
        ],
        cwd=source_root,
        env={
            **os.environ,
            "DASHSCOPE_API_KEY": "",
            "QWEN_API_KEY": "",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise BundleBuildError("selected agent run validator returned invalid JSON") from exc
    expected_artifacts = sorted(
        relative.removeprefix(f"{latest_run['path']}/")
        for relative in latest_run["files"]
    )
    if (
        completed.returncode != 0
        or not isinstance(report, dict)
        or report.get("status") != "ok"
        or report.get("research_plan") != "valid"
        or report.get("hypothesis_portfolio") != "valid"
        or report.get("experiment_manifest_count") != 9
        or report.get("artifacts") != expected_artifacts
    ):
        raise BundleBuildError("selected latest agent run failed immutable artifact validation")
    return report


def _latest_analysis_run(source_root: Path) -> dict[str, Any]:
    runs_root = source_root / "b3" / "outputs" / "runs"
    if not runs_root.is_dir():
        raise BundleBuildError("required analysis run directory is missing: b3/outputs/runs")
    candidates: list[dict[str, Any]] = []
    for directory in sorted(path for path in runs_root.iterdir() if path.is_dir()):
        run_path = directory / "run.json"
        report_path = directory / "report.md"
        if not run_path.is_file() or not report_path.is_file():
            continue
        run = _read_json_object(run_path, "analysis run")
        if run.get("run_id") != directory.name:
            continue
        created = _parse_created_at(run.get("created_at"), "analysis run")
        relative_root = directory.relative_to(source_root).as_posix()
        files = [f"{relative_root}/run.json", f"{relative_root}/report.md"]
        if not all(curated_analysis_run_path(relative) for relative in files):
            continue
        candidates.append(
            {
                "run_id": directory.name,
                "path": relative_root,
                "created_at": run["created_at"],
                "created_sort": created,
                "task": str(run.get("task", "unknown")),
                "files": files,
            }
        )
    if not candidates:
        raise BundleBuildError("no complete analysis run is available for the final deliverable")
    latest = max(candidates, key=lambda item: (item["created_sort"], item["run_id"]))
    latest.pop("created_sort")
    return latest


def _source_files(
    source_root: Path,
    latest_run: dict[str, Any],
    latest_analysis_run: dict[str, Any],
) -> list[tuple[str, str]]:
    selected: dict[str, str] = {}
    for prefix, suffixes, role in TREE_RULES:
        directory = source_root / Path(prefix)
        if not directory.is_dir():
            raise BundleBuildError(f"required allowlisted directory is missing: {prefix}")
        found = False
        for source in sorted(path for path in directory.rglob("*") if path.is_file()):
            if source.suffix.casefold() not in suffixes:
                continue
            relative = source.relative_to(source_root).as_posix()
            if forbidden_path_reason(relative):
                continue
            selected[relative] = role
            found = True
        if not found:
            raise BundleBuildError(f"allowlisted directory contains no eligible files: {prefix}")
    for relative, role in EXACT_RULES.items():
        if not (source_root / Path(relative)).is_file():
            raise BundleBuildError(f"required bundle file is missing: {relative}")
        selected[relative] = role
    for relative, role in OPTIONAL_EXACT_RULES.items():
        if (source_root / Path(relative)).is_file():
            selected[relative] = role
    for relative in latest_run["files"]:
        selected[relative] = "latest_agent_run_artifact"
    for relative in latest_analysis_run["files"]:
        selected[relative] = "latest_analysis_run_artifact"
    return sorted(selected.items())


def _copy_source_file(
    source_root: Path,
    staging: Path,
    relative: str,
    role: str,
) -> dict[str, Any]:
    relative = normalize_relative_path(relative)
    reason = forbidden_path_reason(relative)
    if reason:
        raise BundleBuildError(f"forbidden path in bundle allowlist: {relative}: {reason}")
    if not allowed_destination_path(relative):
        raise BundleBuildError(f"path is outside the bundle allowlist: {relative}")
    source = source_root / Path(relative)
    if source.is_symlink():
        raise BundleBuildError(f"symbolic links are forbidden in the bundle: {relative}")
    resolved_source = source.resolve()
    try:
        resolved_source.relative_to(source_root.resolve())
    except ValueError as exc:
        raise BundleBuildError(f"source path escapes the repository: {relative}") from exc
    if source.stat().st_size > MAX_BUNDLE_FILE_BYTES:
        raise BundleBuildError(f"allowlisted source exceeds 2 MiB limit: {relative}")

    destination = staging / Path(relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_digest = sha256(source)
    transformed = False
    if _is_text_path(source):
        source_bytes = source.read_bytes()
        had_utf8_bom = source_bytes.startswith(b"\xef\xbb\xbf")
        text = source_bytes.decode("utf-8-sig")
        sanitized = (
            _sanitize_json_text(text, source_root, relative)
            if source.suffix.casefold() == ".json"
            else _sanitize_text(text, source_root)
        )
        _scan_high_confidence_secrets(sanitized, relative)
        needs_utf8_bom = source.suffix.casefold() == ".ps1"
        transformed = sanitized != text or had_utf8_bom != needs_utf8_bom
        if transformed:
            destination.write_text(
                sanitized,
                encoding="utf-8-sig" if needs_utf8_bom else "utf-8",
                newline="\n",
            )
        else:
            # Byte-preserve reviewed public data and unchanged source text so
            # source-manifest SHA-256 values remain valid in the portable replay.
            shutil.copyfile(source, destination)
    else:
        shutil.copyfile(source, destination)
    return {
        "path": relative,
        "role": role,
        "source_path": relative,
        "source_sha256": source_digest,
        "transformed_for_portability": transformed,
        "bytes": destination.stat().st_size,
        "sha256": sha256(destination),
    }


def _write_generated(
    staging: Path,
    relative: str,
    content: str,
    role: str,
    *,
    bom: bool = False,
) -> dict[str, Any]:
    if relative not in GENERATED_PATHS:
        raise BundleBuildError(f"generated path is outside the allowlist: {relative}")
    destination = staging / Path(relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8-sig" if bom else "utf-8", newline="\n")
    return {
        "path": relative,
        "role": role,
        "source_path": "generated",
        "source_sha256": None,
        "transformed_for_portability": False,
        "bytes": destination.stat().st_size,
        "sha256": sha256(destination),
    }


def _result_index(state: dict[str, Any]) -> str:
    latest = state["latest_agent_run"]
    latest_analysis = state["latest_analysis_run"]
    run_path = latest["path"]
    statuses = latest["experiment_statuses"]
    passed = sum(item["status"] == "passed" for item in statuses)
    failed = sum(item["status"] == "failed" for item in statuses)
    status_rows = "\n".join(
        f"| {item['code']} | {item['status']} | "
        f"[manifest](<{run_path}/experiments/{item['directory']}/manifest.json>) | "
        f"[result](<{run_path}/experiments/{item['directory']}/result.json>) |"
        for item in statuses
    )
    e8 = next(item for item in statuses if item["code"] == "E8")
    e8_note = (
        "E8 clean reproduction 当前失败，因此不能宣称独立 clean-room 复现已经完成；"
        "失败产物仍被保留，便于定位 manifest accounting 问题。"
        if e8["status"] == "failed"
        else "E8 clean reproduction 已通过。"
    )
    live_connection = "已通过" if state["qwen_live_connection_ok"] else "未通过"
    live_matrix = "已完成" if state["live_qwen_ready"] else "未完成"
    return f"""# B3 三 Agent 成果索引

这里汇总主要报告、运行产物和验证证据。具体运行方法、问题示例和故障处理见 [操作手册 1.0](<操作手册.md>)。

## 1. 建议阅读顺序

1. [操作手册 1.0](<操作手册.md>)
2. [最终技术报告 PDF](<b3/final_report/b3_final_technical_report.pdf>)
3. [最终技术报告 Markdown](<b3/final_report/b3_final_technical_report.md>)
4. [评审速览](<b3/docs/评审速览.md>)
5. [三 Agent 研究背景与设计说明](<b3/docs/三Agent_研究背景与设计说明.md>)

## 2. 最新完整三 Agent 运行

- 运行 ID：`{latest['run_id']}`
- 创建时间：`{latest['created_at']}`
- 研究任务：{latest['task']}
- 研究规划状态：`{latest['plan_status']}`
- 假设组合状态：`{latest['portfolio_status']}`
- 实验节点：{passed} passed，{failed} failed
- [运行清单](<{run_path}/run_manifest.json>)
- [Research Planner 产物](<{run_path}/research_plan.json>)
- [Hypothesis Agent 产物](<{run_path}/hypothesis_portfolio.json>)

| 节点 | 状态 | 实验清单 | 结果 |
|---|---|---|---|
{status_rows}

> {e8_note}

## 3. 研究与展示产物

- [完整分析报告 JSON](<b3/outputs/b3_analysis_report.json>)
- [最新可视化分析运行报告](<{latest_analysis['path']}/report.md>)
- [最新可视化分析运行 JSON](<{latest_analysis['path']}/run.json>)
- [图 1：活动周峰值时间线](<b3/final_report/figures/fig01_cycle_peak_timeline.png>)
- [图 2：极区场低阶模型](<b3/final_report/figures/fig02_polar_toy_model.png>)
- [图 3：科学假设排序](<b3/final_report/figures/fig03_hypothesis_ranking.png>)
- [图 4：三 Agent 闭环架构](<b3/final_report/figures/fig04_closed_loop_architecture.png>)
- [代表性测试案例说明](<b3/docs/representative_test_cases.md>)
- [可执行测试案例目录](<b3/test_cases/manifest.json>)

## 4. 验证证据与边界

- Qwen Max 脱敏 live 连通性证明：{live_connection}；[JSON](<b3/proofs/qwen_connection_check_live.json>) / [说明](<b3/proofs/qwen_connection_check_live.md>)
- 三 Agent 完整 12 案例 × 3 重复 live 证明：{live_matrix}；[当前边界](<b3/proofs/pi_science_agents_live_eval.md>)
- [12/12 fixture 评测](<b3/proofs/pi_science_agents_eval.md>)
- [9/9 多问题合同覆盖](<b3/proofs/question_diversity.md>)（不等于 Qwen live 质量评测）
- [三 Agent readiness 证明](<b3/proofs/pi_science_agents_readiness.json>)
- [最近一次源工作区流水线记录](<b3/proofs/submission_pipeline_run.md>)（历史记录，不作为本目录的自引用完整性证明）
- [前端 API 冒烟证明](<b3/proofs/frontend_api_smoke.md>)
- [前端视觉验收证明](<b3/proofs/frontend_visual_qa.md>)
- [最终提交审计清单](<b3/docs/最终提交审计清单.md>)

单次 Qwen API 连通成功不等于三 Agent 的完整 live 评测已经完成；fixture 通过也不等于真实模型证明。

## 5. 开始使用

- Pi Agent、Skill、Prompt 与扩展：`.pi/`
- Python 科研运行时：`src/b3cycle/`
- 数据与契约：`b3/data/raw/`、`b3/specs/`、`b3/evals/`
- 测试与验证器：`tests/`、`scripts_b3/`
- 隐私配置样例：`configs/`
- 前端与 API：`app_b3.py`、`static_b3/`
- 一键验收：运行 `./VERIFY.ps1`

完整流程、Pi/Qwen 启动、九类研究问题、`runtime/` 产物位置和发布前检查统一见 [操作手册 1.0](<操作手册.md>)。

安全边界：本目录不包含 API Key、`.env`、原始工具回执、原始 live trace、临时日志、历史运行堆积、私仓可见性证据或重复 ZIP。任何远程仓库必须设为 private。
"""


def artifact_index_payload(
    records: Sequence[dict[str, Any]],
    latest_run: dict[str, Any],
) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {
        "latest_agent_run": [],
        "reports_and_docs": [],
        "figures": [],
        "proofs": [],
        "representative_tests": [],
        "data_and_contracts": [],
        "runtime_and_verification": [],
    }
    for record in records:
        if record.get("source_path") == "generated":
            continue
        path = str(record["path"])
        entry = {
            "path": path,
            "role": record["role"],
            "bytes": record["bytes"],
            "sha256": record["sha256"],
        }
        if path.startswith("b3/agent_runs/"):
            group = "latest_agent_run"
        elif path.startswith("b3/proofs/"):
            group = "proofs"
        elif path.startswith("b3/test_cases/"):
            group = "representative_tests"
        elif path.endswith(".png") and (
            path.startswith("b3/final_report/") or path.startswith("b3/docs/")
        ):
            group = "figures"
        elif path == "操作手册.md" or (
            path.startswith("b3/docs/")
            or path.startswith("b3/final_report/")
            or path.startswith("b3/outputs/runs/")
            or path == "b3/outputs/b3_analysis_report.json"
        ):
            group = "reports_and_docs"
        elif path.startswith(("b3/data/", "b3/specs/", "b3/evals/")):
            group = "data_and_contracts"
        else:
            group = "runtime_and_verification"
        groups[group].append(entry)
    for entries in groups.values():
        entries.sort(key=lambda item: item["path"])
    return {
        "schema_version": "b3-final-artifact-index-v1",
        "bundle_name": BUNDLE_NAME,
        "latest_agent_run_id": latest_run["run_id"],
        "groups": groups,
    }


def _readme(state: dict[str, Any]) -> str:
    dirty_note = (
        "构建时有未提交改动，文件级 source_sha256 记录的是当时的实际内容。"
        if state["tree_dirty"]
        else "构建时工作树干净，文件与下列 Git revision 对应。"
    )
    live_ready = str(state["live_qwen_ready"]).lower()
    live_note = (
        "已经包含脱敏、固定模型快照且没有 fallback 的 live proof。"
        if state["live_qwen_ready"]
        else "还没有符合严格口径的真实 Qwen live proof；Fixture 通过不能替代它。"
    )
    return rf"""# B3 三 Agent

这是 Research Planner、Experiment 和 Hypothesis 三个 Pi Agent 的完整项目目录，包含代码、配置、最新运行产物、报告、图表、证明、前端/API 和验收入口；整个目录可脱离原工作区运行。请先打开 `操作手册.md`，成果位置见 `成果索引.md`。默认模型是固定快照 `dashscope/qwen3.7-max-2026-06-08`；真实 Qwen 能力仍必须由专门的 live proof 证明。

- Git revision：`{state['revision']}`
- 源工作树 dirty：`{str(state['tree_dirty']).lower()}`（{dirty_note}）
- 无需 API Key 即可复现 Agent 合同、17 个科研工具、E0–E8 实验和 12 个 fixture。
- `live_qwen_ready={live_ready}`。{live_note}
- `VERIFY.ps1` 使用 `B3_PYTHON` 或用户缓存中的外部虚拟环境，验收过程不会向交付包写入 `.venv` 或缓存。

## 先跑一次检查

```powershell
$env:PYTHONUTF8='1'
$Snapshot = ((Select-String '^SOURCE_SNAPSHOT_SHA256=' SOURCE_REVISION.txt).Line -split '=', 2)[1]
$Runtime = Join-Path $env:LOCALAPPDATA "B3ThreeAgent\venvs\$Snapshot"
if (-not (Test-Path "$Runtime\Scripts\python.exe")) {{ python -m venv $Runtime }}
$Python = (Resolve-Path "$Runtime\Scripts\python.exe").Path
& $Python -m pip install -r requirements-analysis.lock
& $Python -B scripts_b3/verify_three_agent_bundle.py .
& $Python scripts_b3/verify_pi_agent_skills.py
& $Python scripts_b3/verify_pi_extensions.py
& $Python scripts_b3/verify_question_diversity.py
& $Python -m unittest tests.test_registered_experiments tests.test_science_toolkit tests.test_qwen_adapter tests.test_qwen_connection
& $Python scripts_b3/evaluate_pi_science_agents.py --mode fixture --no-write-proof
```

也可以运行 `.\VERIFY.ps1` 做完整回放。Fixture 通过不等于真实 Qwen 调用证明，任何迁移前旧模型的结果也不能替代固定 Qwen Max 快照的 live proof。

## 在 Pi 里运行

1. 审阅 `.pi/` 后信任当前项目。
2. 在项目根目录运行 `.\scripts_b3\start_qwen_max_pi.ps1`，只在本机隐藏输入 API Key。
3. 执行 `/reload` 和 `/b3-doctor`。
4. 用 `/b3-research-loop [有边界的研究问题]` 跑完整闭环；单独调试时使用三个 `/skill:*` 入口。

相关说明：

- `操作手册.md`
- `b3/docs/三Agent_研究背景与设计说明.md`
- `b3/docs/pi_three_agents_quickstart.md`
- `b3/docs/三Agent_模型配置与隐私操作手册.md`
- `b3/docs/三Agent_现状缺口与未来工作计划.md`

不要把 API Key、`.env`、原始 live trace 或私仓可见性证据放进本目录。这里不会自动上传或推送；以后如果接远端，只能使用确认过的 private 仓库。

`MANIFEST.json` 记录文件来源与哈希，`SHA256SUMS` 记录文件哈希，`SOURCE_REVISION.txt` 记录源快照。`b3/proofs/pi_science_agents_live_eval.*` 在没有凭据时会明确标成 unavailable。

新运行文件的位置、可变 `runtime/` 与构建时证据的区别，以及发布前的 `--require-pristine` 检查，统一见 `操作手册.md`。
"""


def _verify_ps1() -> str:
    return r'''$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = $env:B3_PYTHON
if (-not $Python) {
    $SnapshotLine = Select-String -LiteralPath (Join-Path $Root "SOURCE_REVISION.txt") -Pattern '^SOURCE_SNAPSHOT_SHA256='
    if (-not $SnapshotLine) { throw "无法读取源快照标识。" }
    $Snapshot = ($SnapshotLine.Line -split '=', 2)[1]
    $CacheBase = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { $env:TEMP }
    $Runtime = Join-Path $CacheBase ("B3ThreeAgent\venvs\" + $Snapshot)
    $Python = Join-Path $Runtime "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $Python)) {
        $Command = Get-Command python -ErrorAction SilentlyContinue
        if (-not $Command) { throw "未找到 Python，无法创建外部运行环境。" }
        & $Command.Source -m venv $Runtime
        if ($LASTEXITCODE -ne 0) { throw "创建外部运行环境失败。" }
    }
}
if (-not (Test-Path -LiteralPath $Python)) { throw "B3_PYTHON 不是可用的 Python 路径。" }
$env:PYTHONUTF8 = "1"
$env:PYTHONDONTWRITEBYTECODE = "1"
$PreviousPreference = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
& $Python -c "import numpy, psutil" *> $null
$DependencyProbe = $LASTEXITCODE
$ErrorActionPreference = $PreviousPreference
if ($DependencyProbe -ne 0) {
    & $Python -m pip install -r (Join-Path $Root "requirements-analysis.lock")
    if ($LASTEXITCODE -ne 0) { throw "安装运行依赖失败。" }
}
$Verifier = Join-Path $Root "scripts_b3\verify_three_agent_bundle.py"
$Arguments = @($Verifier, $Root, "--json", "--replay")
if (Test-Path -LiteralPath (Join-Path $Root ".git")) {
    $Arguments += @("--source-root", $Root)
}
& $Python -B @Arguments
if ($LASTEXITCODE -ne 0) { throw "B3 三 Agent 验证失败。" }
Write-Host "B3 三 Agent 验证通过。" -ForegroundColor Green
'''


def _owned_output(path: Path) -> bool:
    manifest_path = path / "MANIFEST.json"
    if not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        manifest.get("schema_version") in {MANIFEST_SCHEMA, *LEGACY_MANIFEST_SCHEMAS}
        and manifest.get("bundle_name") == BUNDLE_NAME
    )


def _assert_safe_output(source_root: Path, output: Path) -> None:
    resolved_root = source_root.resolve()
    resolved_output = output.resolve()
    if output.name != BUNDLE_NAME:
        raise BundleBuildError(f"refusing output whose final directory name is not {BUNDLE_NAME}")
    if resolved_output == resolved_root or resolved_output in resolved_root.parents:
        raise BundleBuildError("refusing to use the repository or its ancestor as bundle output")
    if output.exists() and not _owned_output(output):
        raise BundleBuildError("refusing to replace an output directory not owned by this builder")


def _read_science_readiness(source_root: Path) -> dict[str, bool]:
    path = source_root / "b3" / "proofs" / "pi_science_agents_readiness.json"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BundleBuildError(f"cannot read Pi readiness proof: {exc}") from exc
    offline_ready = report.get("offline_contract_ready")
    live_ready = report.get("live_qwen_ready")
    if not isinstance(offline_ready, bool) or not isinstance(live_ready, bool):
        raise BundleBuildError("Pi readiness proof has invalid readiness flags")
    current = verify_pi_agent_skills(source_root)
    if not readiness_proof_matches_report(report, current):
        raise BundleBuildError("Pi readiness proof is stale for the current bundle inputs")
    return {
        "offline_contract_ready": current["offline_contract_ready"],
        "live_qwen_ready": current["live_qwen_ready"],
    }


def _read_qwen_live_connection(source_root: Path) -> bool:
    path = source_root / "b3" / "proofs" / "qwen_connection_check_live.json"
    proof = _read_json_object(path, "Qwen live connection proof")
    return (
        proof.get("schema_version") == "b3-qwen-proof-v1"
        and proof.get("status") == "live_connection_ok"
        and proof.get("live_ok") is True
        and proof.get("fallback_reason") is None
        and proof.get("model") == "qwen3.7-max-2026-06-08"
    )


def build_bundle(
    source_root: Path,
    output: Path,
    *,
    require_clean: bool = False,
) -> dict[str, Any]:
    source_root = source_root.resolve()
    output = output.resolve()
    _assert_safe_output(source_root, output)
    state = git_state(source_root)
    if require_clean and state["tree_dirty"]:
        raise BundleBuildError("final bundle requires a clean Git worktree")
    state.update(_read_science_readiness(source_root))
    state["qwen_live_connection_ok"] = _read_qwen_live_connection(source_root)
    state["latest_agent_run"] = _latest_complete_agent_run(source_root)
    state["latest_analysis_run"] = _latest_analysis_run(source_root)
    state["latest_agent_run_validation"] = _validate_agent_run(
        source_root,
        state["latest_agent_run"],
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=".b3-bundle-staging-", dir=str(output.parent))
    )
    try:
        records = [
            _copy_source_file(source_root, staging, relative, role)
            for relative, role in _source_files(
                source_root,
                state["latest_agent_run"],
                state["latest_analysis_run"],
            )
        ]
        transformed_run_records = [
            item["path"]
            for item in records
            if item["role"] == "latest_agent_run_artifact"
            and item["transformed_for_portability"]
        ]
        if transformed_run_records:
            raise BundleBuildError(
                "latest agent run is not publication-portable and would invalidate "
                "immutable artifact hashes: "
                + ", ".join(transformed_run_records)
            )
        records.append(
            _write_generated(
                staging,
                "ARTIFACT_INDEX.json",
                json.dumps(
                    artifact_index_payload(records, state["latest_agent_run"]),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=False,
                )
                + "\n",
                "machine_readable_artifact_index",
            )
        )
        records.append(
            _write_generated(
                staging,
                "成果索引.md",
                _result_index(state),
                "final_deliverable_index",
            )
        )
        records.append(
            _write_generated(
                staging,
                "README_先看.md",
                _readme(state),
                "project_entry_readme",
            )
        )
        records.append(
            _write_generated(
                staging,
                "VERIFY.ps1",
                _verify_ps1(),
                "portable_verification_entry",
                bom=True,
            )
        )

        source_records = [
            {"path": item["source_path"], "sha256": item["source_sha256"]}
            for item in records
            if item["source_path"] != "generated"
        ]
        snapshot_sha256 = canonical_sha256(sorted(source_records, key=lambda item: item["path"]))
        revision_text = (
            f"SOURCE_REVISION={state['revision']}\n"
            f"SOURCE_TREE_DIRTY={str(state['tree_dirty']).lower()}\n"
            f"SOURCE_STATUS_SHA256={state['status_sha256']}\n"
            f"SOURCE_SNAPSHOT_SHA256={snapshot_sha256}\n"
        )
        records.append(
            _write_generated(
                staging,
                "SOURCE_REVISION.txt",
                revision_text,
                "source_revision_record",
            )
        )
        records.sort(key=lambda item: item["path"])
        manifest = {
            "schema_version": MANIFEST_SCHEMA,
            "bundle_name": BUNDLE_NAME,
            "display_name": "B3 三 Agent",
            "source_revision": state["revision"],
            "source_commit_timestamp": state["commit_timestamp"],
            "source_tree_dirty": state["tree_dirty"],
            "source_status_sha256": state["status_sha256"],
            "source_snapshot_sha256": snapshot_sha256,
            "security_boundary": {
                "contains_credentials": False,
                "contains_private_remote_evidence": False,
                "contains_live_raw_traces": False,
                "contains_large_external_materials": False,
                "maximum_source_file_bytes": MAX_BUNDLE_FILE_BYTES,
            },
            "runtime_policy": runtime_policy_payload(),
            "offline_reproduction": {
                "api_key_required": False,
                "command": "python scripts_b3/evaluate_pi_science_agents.py --mode fixture --no-write-proof",
                "offline_contract_ready": state["offline_contract_ready"],
                "live_qwen_ready": state["live_qwen_ready"],
                "live_proof_included": state["live_qwen_ready"],
            },
            "artifact_inventory": {
                "entry_index": "成果索引.md",
                "machine_index": "ARTIFACT_INDEX.json",
                "latest_agent_run": {
                    "run_id": state["latest_agent_run"]["run_id"],
                    "path": state["latest_agent_run"]["path"],
                    "created_at": state["latest_agent_run"]["created_at"],
                    "task": state["latest_agent_run"]["task"],
                    "file_count": len(state["latest_agent_run"]["files"]),
                    "plan_status": state["latest_agent_run"]["plan_status"],
                    "portfolio_status": state["latest_agent_run"]["portfolio_status"],
                    "experiment_statuses": state["latest_agent_run"][
                        "experiment_statuses"
                    ],
                    "immutable_validation": {
                        "status": state["latest_agent_run_validation"]["status"],
                        "artifact_count": state["latest_agent_run_validation"][
                            "artifact_count"
                        ],
                        "experiment_manifest_count": state[
                            "latest_agent_run_validation"
                        ]["experiment_manifest_count"],
                    },
                },
                "analysis_report": "b3/outputs/b3_analysis_report.json",
                "latest_analysis_run": {
                    "run_id": state["latest_analysis_run"]["run_id"],
                    "path": state["latest_analysis_run"]["path"],
                    "created_at": state["latest_analysis_run"]["created_at"],
                    "task": state["latest_analysis_run"]["task"],
                    "file_count": len(state["latest_analysis_run"]["files"]),
                },
                "final_report_pdf": "b3/final_report/b3_final_technical_report.pdf",
                "qwen_live_connection_ok": state["qwen_live_connection_ok"],
                "full_live_matrix_ready": state["live_qwen_ready"],
            },
            "files": records,
        }
        manifest_path = staging / "MANIFEST.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        checksum_paths = sorted(
            path for path in staging.rglob("*") if path.is_file() and path.name != "SHA256SUMS"
        )
        checksum_lines = [
            f"{sha256(path)}  {path.relative_to(staging).as_posix()}"
            for path in checksum_paths
        ]
        (staging / "SHA256SUMS").write_text(
            "\n".join(checksum_lines) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        if output.exists():
            # _assert_safe_output proved this is a prior bundle owned by us.
            shutil.rmtree(output)
        staging.replace(output)
        return {
            "schema_version": MANIFEST_SCHEMA,
            "passed": True,
            "output": str(output),
            "source_revision": state["revision"],
            "source_tree_dirty": state["tree_dirty"],
            "source_snapshot_sha256": snapshot_sha256,
            "file_count": len(records) + 2,
        }
    except Exception:
        if staging.exists() and staging.parent == output.parent:
            shutil.rmtree(staging)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-clean", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source_root = args.source_root.resolve()
    output = args.output or (source_root / "dist" / BUNDLE_NAME)
    try:
        report = build_bundle(
            source_root,
            output,
            require_clean=args.require_clean,
        )
    except (BundleBuildError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {"schema_version": MANIFEST_SCHEMA, "passed": False, "error": str(exc)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
