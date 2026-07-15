#!/usr/bin/env python3
"""Static, secret-safe verifier for the project-local B3 Pi extensions."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any


EXPECTED_AGENTS = {
    "b3-research-planner",
    "b3-experiment",
    "b3-hypothesis",
}
EXPECTED_AGENT_TOOLS = {
    "b3-research-planner": [
        "b3_read_project",
        "b3_grep_project",
        "b3_find_project",
        "b3_list_project",
        "b3_discover_tools",
        "b3_inspect_tool",
        "b3_run_tool",
        "b3_verify_result",
        "b3_trace_artifact",
    ],
    "b3-experiment": [
        "b3_read_project",
        "b3_grep_project",
        "b3_find_project",
        "b3_list_project",
        "b3_run_registered_experiment",
        "b3_read_run_state",
        "b3_discover_tools",
        "b3_inspect_tool",
        "b3_run_tool",
        "b3_verify_result",
        "b3_trace_artifact",
    ],
    "b3-hypothesis": [
        "b3_read_project",
        "b3_grep_project",
        "b3_find_project",
        "b3_list_project",
        "b3_read_run_state",
        "b3_discover_tools",
        "b3_inspect_tool",
        "b3_run_tool",
        "b3_verify_result",
        "b3_trace_artifact",
    ],
}
EXPECTED_AGENT_THINKING = {
    "b3-research-planner": "medium",
    "b3-experiment": "low",
    "b3-hypothesis": "high",
}
AGENT_REQUIRED_MARKERS = {
    "b3-research-planner": [
        "INTAKE -> SCOPE_AND_CLAIM_BOUNDARY",
        "DAG_AND_LEAKAGE_VALIDATE",
        "b3-research-plan-v2",
        "b3_submit_research_plan",
        "frozen_hash",
        "## 时间与修订预算",
        "planning.validate_plan_draft` 最多调用两次",
        "立即停止扩展 DAG",
    ],
    "b3-experiment": [
        "VALIDATE_RUN -> LOAD_FROZEN_NODE",
        "EXECUTE_ONE_REGISTERED_NODE",
        "b3-experiment-handoff-v1",
        "E8_clean_reproduction",
    ],
    "b3-hypothesis": [
        "LOAD_VERIFIED_EVIDENCE -> PORTFOLIO_GENERATION",
        "PROXIMITY_AND_DEDUP",
        "ORDER_BALANCED_PAIRWISE_PREPARATION",
        "b3-hypothesis-portfolio-v2",
        "b3_submit_hypothesis_portfolio",
    ],
}
EXPECTED_TOOLS = {
    "b3_subagent",
    "b3_run_registered_experiment",
    "b3_read_run_state",
    "b3_read_project",
    "b3_grep_project",
    "b3_find_project",
    "b3_list_project",
    "b3_init_science_run",
    "b3_submit_research_plan",
    "b3_submit_hypothesis_portfolio",
    "b3_validate_hypothesis_portfolio",
    "b3_discover_tools",
    "b3_inspect_tool",
    "b3_run_tool",
    "b3_verify_result",
    "b3_trace_artifact",
}
EXPECTED_COMMANDS = {"b3-doctor"}
EXPECTED_MODELS = {
    "dashscope/qwen3.7-max-2026-06-08",
    "dashscope/qwen3.7-plus-2026-05-26",
    "dashscope/qwen3.6-flash-2026-04-16",
}
SCHEMA_FILES = {
    "research_plan_v2.schema.json",
    "experiment_manifest_v2.schema.json",
    "hypothesis_portfolio_v2.schema.json",
}


def _read(path: Path, errors: list[str]) -> str:
    if not path.is_file():
        errors.append(f"missing file: {path.relative_to(path.parents[2]).as_posix()}")
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        errors.append(f"cannot read {path.name}: {type(exc).__name__}")
        return ""


def _frontmatter(source: str) -> dict[str, str]:
    match = re.match(r"^---\r?\n(.*?)\r?\n---(?:\r?\n|$)", source, re.S)
    if not match:
        return {}
    values: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip()
    return values


def _agent_contracts(project: Path, errors: list[str]) -> dict[str, Any]:
    reports: dict[str, Any] = {}
    for name in sorted(EXPECTED_AGENTS):
        path = project / ".pi" / "agents" / f"{name}.md"
        source = _read(path, errors)
        meta = _frontmatter(source)
        tools = [item.strip() for item in meta.get("tools", "").split(",") if item.strip()]
        missing = [
            marker for marker in AGENT_REQUIRED_MARKERS[name] if marker not in source
        ]
        if meta.get("name") != name:
            missing.append("exact frontmatter name")
        if meta.get("model") != "b3-default":
            missing.append("exact reviewed model alias")
        if meta.get("thinking") != EXPECTED_AGENT_THINKING[name]:
            missing.append("exact role thinking level")
        if tools != EXPECTED_AGENT_TOOLS[name]:
            missing.append("exact project-scoped tool allowlist")
        if "Return exactly one JSON object" not in source:
            missing.append("single JSON output contract")
        if re.search(r"tools:\s*(?:read|grep|find|ls)(?:\s|,|$)", source):
            missing.append("forbidden built-in file tool")
        reports[name] = {
            "path": path.relative_to(project).as_posix(),
            "passed": not missing,
            "model": meta.get("model"),
            "thinking": meta.get("thinking"),
            "tools": tools,
            "missing": missing,
        }
        if missing:
            errors.append(f"agent definition {name} failed: {', '.join(missing)}")
    return reports


def verify_pi_extensions(root: Path) -> dict[str, Any]:
    project = Path(root).resolve()
    errors: list[str] = []
    provider_path = project / ".pi" / "extensions" / "dashscope-provider.ts"
    extension_path = project / ".pi" / "extensions" / "b3-science" / "index.ts"
    agents_path = project / ".pi" / "extensions" / "b3-science" / "agents.ts"
    project_tools_path = (
        project / ".pi" / "extensions" / "b3-science" / "project-tools.ts"
    )
    project_root_path = (
        project / ".pi" / "extensions" / "b3-science" / "project-root.ts"
    )
    project_paths_path = (
        project / ".pi" / "extensions" / "b3-science" / "project-paths.ts"
    )
    child_policy_path = (
        project / ".pi" / "extensions" / "b3-science" / "child-policy.ts"
    )
    child_event_stream_path = (
        project / ".pi" / "extensions" / "b3-science" / "child-event-stream.ts"
    )
    model_route_path = (
        project / ".pi" / "extensions" / "b3-science" / "model-route.ts"
    )
    scientific_tools_path = (
        project / ".pi" / "extensions" / "b3-science" / "scientific-tools.ts"
    )
    science_cli_runtime_path = (
        project / ".pi" / "extensions" / "b3-science" / "science-cli-runtime.ts"
    )
    science_toolkit_path = project / "src" / "b3cycle" / "science_toolkit.py"
    science_agent_cli_path = project / "scripts_b3" / "science_agent_cli.py"
    planner_agent_path = project / ".pi" / "agents" / "b3-research-planner.md"
    experiment_agent_path = project / ".pi" / "agents" / "b3-experiment.md"
    hypothesis_agent_path = project / ".pi" / "agents" / "b3-hypothesis.md"
    planner_skill_path = (
        project / ".pi" / "skills" / "research-planner-agent" / "SKILL.md"
    )
    experiment_skill_path = (
        project / ".pi" / "skills" / "experiment-agent" / "SKILL.md"
    )
    hypothesis_skill_path = (
        project / ".pi" / "skills" / "hypothesis-agent" / "SKILL.md"
    )
    research_prompt_path = project / ".pi" / "prompts" / "b3-research-loop.md"
    provider = _read(provider_path, errors)
    extension = _read(extension_path, errors)
    agents_source = _read(agents_path, errors)
    project_tools = _read(project_tools_path, errors)
    project_root = _read(project_root_path, errors)
    project_paths = _read(project_paths_path, errors)
    child_policy = _read(child_policy_path, errors)
    child_event_stream = _read(child_event_stream_path, errors)
    model_route = _read(model_route_path, errors)
    scientific_tools = _read(scientific_tools_path, errors)
    science_cli_runtime = _read(science_cli_runtime_path, errors)
    science_toolkit = _read(science_toolkit_path, errors)
    science_agent_cli = _read(science_agent_cli_path, errors)
    planner_agent = _read(planner_agent_path, errors)
    experiment_agent = _read(experiment_agent_path, errors)
    hypothesis_agent = _read(hypothesis_agent_path, errors)
    planner_skill = _read(planner_skill_path, errors)
    experiment_skill = _read(experiment_skill_path, errors)
    hypothesis_skill = _read(hypothesis_skill_path, errors)
    research_prompt = _read(research_prompt_path, errors)

    agents = {
        name
        for name in EXPECTED_AGENTS
        if re.search(rf'["\']{re.escape(name)}["\']', agents_source)
    }
    tools = set(
        re.findall(
            r'name:\s*["\'](b3_[a-z0-9_]+)["\']',
            extension + project_tools + scientific_tools,
        )
    )
    commands = set(
        re.findall(r'registerCommand\(\s*["\']([^"\']+)["\']', extension)
    )
    models = set()
    if (
        "qwen3.7-max-2026-06-08" in provider
        and 'registerProvider("dashscope"' in provider
    ):
        models.add("dashscope/qwen3.7-max-2026-06-08")
    if "qwen3.7-plus-2026-05-26" in provider:
        models.add("dashscope/qwen3.7-plus-2026-05-26")
    if "qwen3.6-flash-2026-04-16" in provider:
        models.add("dashscope/qwen3.6-flash-2026-04-16")
    if agents != EXPECTED_AGENTS:
        errors.append(f"agent registry mismatch: {sorted(agents)}")
    if tools != EXPECTED_TOOLS:
        errors.append(f"tool registry mismatch: {sorted(tools)}")
    if not EXPECTED_COMMANDS <= commands:
        errors.append(f"missing commands: {sorted(EXPECTED_COMMANDS - commands)}")
    if models != EXPECTED_MODELS:
        errors.append(f"model registry mismatch: {sorted(models)}")

    hardcoded_secret = bool(
        re.search(r"(?i)(?:sk|ak)-[a-z0-9_-]{12,}", provider + extension)
    )
    credential_references_only = (
        "DASHSCOPE_API_KEY" in provider
        and "QWEN_API_KEY" in provider
        and "apiKey: `$${apiKeyEnv}`" in provider
        and not hardcoded_secret
    )
    shell_false = "shell: false" in extension
    abort_propagation = "addEventListener(\"abort\"" in extension
    abort_escalation = 'child.kill("SIGKILL")' in extension and "5_000" in extension
    output_cap = "MAX_RETURN_BYTES = 50 * 1024" in extension
    fixed_cli_source = extension + scientific_tools + science_cli_runtime
    fixed_cli = all(
        marker in fixed_cli_source
        for marker in (
            "science_agent_cli.py",
            "run-experiment",
            "validate-run",
            "submit-plan",
            "submit-portfolio",
            "validate-portfolio",
            "REGISTERED_EXPERIMENT_IDS",
            "discover-tools",
            "inspect-tool",
            "run-tool",
            "verify-tool-result",
            "trace-artifact",
        )
    )
    credential_scrubbed_science_cli = all(
        marker in extension + science_cli_runtime
        for marker in (
            "scienceCliEnvironment()",
            "const environment: NodeJS.ProcessEnv = {}",
            "for (const name of [",
            "environment.B3_TOOL_RECEIPT_HMAC_KEY",
            "env: scienceCliEnvironment()",
        )
    ) and "Object.entries(process.env).filter" not in extension + science_cli_runtime
    isolated_python_runtime = all(
        marker in extension + science_cli_runtime
        for marker in (
            'resolve(projectRoot, ".venv", "Scripts", "python.exe")',
            "realpathSync(local)",
            "statSync(realPython).isFile()",
            "isWithinOrSame(realRoot, realPython)",
            '"-I"',
            '"utf8"',
            "Trusted project .venv Python is missing",
        )
    ) and 'return existsSync(local) ? local : "python"' not in extension + science_cli_runtime
    authenticated_tool_receipts = all(
        marker in extension + science_cli_runtime + science_toolkit
        for marker in (
            "randomBytes(32)",
            "B3_TOOL_RECEIPT_HMAC_KEY",
            "b3-tool-execution-receipt-v3-hmac-sha256",
            "hmac.new(",
            "hmac.compare_digest(",
            "parent_held_hmac_execution_receipt",
        )
    )
    honest_subagent_transport_contract = all(
        marker in extension
        for marker in (
            "transportJsonObjectParsed: true",
            "scientificContractValidated: false",
        )
    ) and "contractJsonValidated: true" not in extension
    isolated_child = all(
        marker in extension + child_policy
        for marker in (
            '"--no-session"',
            '"--approve"',
            '"--no-context-files"',
            '"--no-skills"',
            '"--no-prompt-templates"',
        )
    )
    json_event_transport = all(
        marker in extension + child_policy
        for marker in (
            '"--mode"',
            '"json"',
            'transport: "json-events-v3"',
        )
    ) and '"--print"' not in child_policy
    safe_child_progress = all(
        marker in extension + child_event_stream
        for marker in (
            "ChildEventTracker",
            "CHILD_HEARTBEAT_MS",
            "CHILD_INACTIVITY_TIMEOUT_MS",
            'item.type === "text"',
            'deltaType === "thinking_start"',
            'deltaType === "text_start"',
            "validationAttempts",
            "progressLines",
            "workingMessage",
            "setWorkingMessage?.(update.workingMessage)",
            "INTEGRATING_WORKING_MESSAGE",
            "ctx.ui.setWorkingMessage(INTEGRATING_WORKING_MESSAGE)",
            "event_line_too_large",
            "event_stream_too_large",
        )
    ) and "assistantMessageEvent.delta" not in child_event_stream
    human_facing_sources = (
        extension,
        planner_agent,
        experiment_agent,
        hypothesis_agent,
        planner_skill,
        experiment_skill,
        hypothesis_skill,
        research_prompt,
    )
    human_facing_contract_names = (
        all(
            marker in "\n".join(human_facing_sources)
            for marker in (
                "ResearchPlan 1.0",
                "ExperimentManifest 1.0",
                "HypothesisPortfolio 1.0",
            )
        )
        and all(
            legacy not in source
            for source in human_facing_sources
            for legacy in (
                "ResearchPlanV2",
                "ExperimentManifestV2",
                "HypothesisPortfolioV2",
                "HypothesisCardV2",
            )
        )
        and "planJson" in extension
        and "portfolioJson" in extension
        and "draftJson" not in extension
    )
    diagnostic_timeouts = all(
        marker in extension + child_event_stream
        for marker in (
            '"wall_timeout"',
            '"inactivity_timeout"',
            "tracker.diagnostic",
            "lastActivityAt",
            "validation_attempts=",
        )
    )
    role_thinking_policy = all(
        marker in agents_source + child_policy
        for marker in (
            '"b3-research-planner": "medium"',
            '"b3-experiment": "low"',
            '"b3-hypothesis": "high"',
            '"--thinking"',
            "agent.thinking",
        )
    )
    https_only = 'parsed.protocol !== "https:"' in provider
    qwen_compat = 'thinkingFormat: "qwen"' in provider
    qwen_completion_token_field = (
        'maxTokensField: "max_completion_tokens"' in provider
    )
    qwen_provider_route_isolated = all(
        marker in provider
        for marker in (
            "configuredB3AgentModel()",
            "activeAgentRoute",
            "isDashScopeAgentModel(activeAgentRoute)",
            "return;",
        )
    )
    doctor_uses_auth_status = (
        "getProviderAuthStatus" in extension
        and "hasConfiguredAuth" in extension
        and "getApiKey" not in extension
    )
    doctor_checks_python_dependencies = all(
        marker in extension
        for marker in (
            "import json, numpy, psutil",
            "python_runtime_ready",
            "python_dependency_versions",
            "python_locked_dependency_versions",
            "python_dependency_mismatches",
            "requirements-analysis.lock",
        )
    )
    realpath_agent_boundary = (
        "realpathSync(agentsRoot)" in agents_source
        and "realpathSync(candidate)" in agents_source
    )
    explicit_extension_isolation = all(
        marker in extension + child_policy
        for marker in (
            '"--no-extensions"',
            '"--extension"',
            "trustedChildExtensions(projectRoot)",
            'realpathSync(resolve(projectRoot, ".pi", "extensions"))',
        )
    )
    dashscope_host_allowlist = all(
        marker in provider
        for marker in (
            "ALLOWED_DASHSCOPE_HOSTS",
            '.endsWith(".maas.aliyuncs.com")',
            'canonicalPath = "/compatible-mode/v1"',
            "normalizedPath !== canonicalPath",
            "parsed.username",
            "parsed.search",
        )
    )
    qwen_model_allowlist = (
        "QWEN_MODEL_PATTERN" in provider
        and "qwen3\\.(?:7-(?:max|plus)|6-flash)-" in provider
        and '"qwen3.7-max-2026-06-08"' in provider
        and '"qwen3.7-plus-2026-05-26"' in provider
        and '"qwen3.6-flash-2026-04-16"' in provider
        and "configuredQwenModelId()" in agents_source
    )
    model_route_allowlist = all(
        marker in model_route
        for marker in (
            'B3_AGENT_MODEL_ALIAS = "b3-default"',
            "DEFAULT_B3_AGENT_MODEL =",
            '"dashscope/qwen3.7-max-2026-06-08"',
            "ALLOWED_B3_AGENT_MODELS = [",
            '"dashscope/qwen3.7-plus-2026-05-26"',
            '"dashscope/qwen3.6-flash-2026-04-16"',
            "assertAllowedB3AgentModel",
        )
    ) and "configuredB3AgentModel()" in agents_source and not any(
        legacy in model_route
        for legacy in ("kimi-coding/",)
    )
    compact_tool_protocol = all(
        marker in scientific_tools
        for marker in (
            'name: "b3_discover_tools"',
            'name: "b3_inspect_tool"',
            'name: "b3_run_tool"',
            'name: "b3_verify_result"',
            'name: "b3_trace_artifact"',
            "SCIENTIFIC_TOOL_IDS",
        )
    ) and "registerScientificToolkitTools" in extension
    parent_bound_scientific_tool_roles = all(
        marker in extension + scientific_tools + science_toolkit + science_agent_cli
        for marker in (
            "B3_ACTIVE_AGENT: agent.name",
            "process.env.B3_ACTIVE_AGENT",
            "trustedActiveAgent()",
            "role-scoped scientific tools require trusted B3_ACTIVE_AGENT binding",
            "--human-offline",
            "not_issued_human_offline",
        )
    ) and "params.agent" not in scientific_tools
    qwen_secret_redaction = all(
        marker in extension
        for marker in ("DASHSCOPE_API_KEY", "QWEN_API_KEY")
    )
    no_builtin_file_tools = not any(
        re.search(rf'["\']{name}["\']', agents_source)
        for name in ("read", "grep", "find", "ls")
    )
    project_scoped_read_tools = all(
        marker in project_tools
        for marker in (
            'name: "b3_read_project"',
            'name: "b3_grep_project"',
            'name: "b3_find_project"',
            'name: "b3_list_project"',
        )
    ) and all(
        marker in project_paths
        for marker in (
            "authorizeProjectPath(",
            "realpathSync(lexicalCandidate)",
            "isWithinOrSame(realRoot, realCandidate)",
        )
    )
    protected_path_filter = all(
        marker in project_paths
        for marker in (
            "isProtectedProjectRelativePath",
            '".git"',
            'segment === ".env"',
            "credentials?",
            'segments[0] === "tests"',
            'segments[1] === "evals"',
            'segments[2] === "_tool_receipts"',
        )
    )
    anchored_project_root = all(
        marker in project_root
        for marker in (
            "ANCHORED_PROJECT_ROOT",
            "fileURLToPath(import.meta.url)",
            "isWithinOrSame(ANCHORED_PROJECT_ROOT, resolvedCwd)",
        )
    ) and "while (true)" not in agents_source
    for label, passed in (
        ("child process must use shell:false", shell_false),
        ("child abort propagation is missing", abort_propagation),
        ("child abort escalation is missing", abort_escalation),
        ("child result cap is missing", output_cap),
        ("science CLI boundary is not fixed", fixed_cli),
        ("science CLI does not scrub credential environment values", credential_scrubbed_science_cli),
        ("science CLI Python runtime is not isolated", isolated_python_runtime),
        ("scientific tool receipts are not parent-authenticated", authenticated_tool_receipts),
        ("subagent transport overstates contract validation", honest_subagent_transport_contract),
        ("child isolation flags are incomplete", isolated_child),
        ("child JSON event transport is incomplete", json_event_transport),
        ("child progress stream may expose unsafe content", safe_child_progress),
        ("human-facing contract naming is stale", human_facing_contract_names),
        ("child timeout diagnostics are incomplete", diagnostic_timeouts),
        ("role-specific thinking policy is incomplete", role_thinking_policy),
        ("credential references are unsafe", credential_references_only),
        ("DashScope base URL is not HTTPS-only", https_only),
        ("Qwen thinking compatibility is missing", qwen_compat),
        ("Qwen completion token field is outdated", qwen_completion_token_field),
        ("Qwen provider route isolation is incomplete", qwen_provider_route_isolated),
        ("doctor does not use secret-safe auth status", doctor_uses_auth_status),
        ("doctor does not check locked Python dependencies", doctor_checks_python_dependencies),
        ("agent path realpath boundary is missing", realpath_agent_boundary),
        ("child extensions are not explicitly isolated", explicit_extension_isolation),
        ("DashScope host allowlist is missing", dashscope_host_allowlist),
        ("Qwen model allowlist is missing", qwen_model_allowlist),
        ("Qwen-only route with Max default is missing", model_route_allowlist),
        ("compact scientific tool protocol is incomplete", compact_tool_protocol),
        ("scientific tool roles are not parent-bound", parent_bound_scientific_tool_roles),
        ("Qwen credential redaction is incomplete", qwen_secret_redaction),
        ("built-in file tools remain in child allowlists", no_builtin_file_tools),
        ("project-scoped read tools are incomplete", project_scoped_read_tools),
        ("protected project path filter is incomplete", protected_path_filter),
        ("project root is not anchored to the loaded extension", anchored_project_root),
    ):
        if not passed:
            errors.append(label)

    schema_status = {
        name: (project / "b3" / "specs" / name).is_file()
        for name in sorted(SCHEMA_FILES)
    }
    for name, exists in schema_status.items():
        if not exists:
            errors.append(f"missing schema: {name}")

    agent_files = {
        name: (project / ".pi" / "agents" / f"{name}.md").is_file()
        for name in sorted(EXPECTED_AGENTS)
    }
    agent_contracts = _agent_contracts(project, errors)
    report: dict[str, Any] = {
        "schema_version": "b3-pi-extension-verifier-v1",
        "passed": not errors,
        "errors": errors,
        "agents": sorted(agents),
        "agent_files": agent_files,
        "agent_contracts": agent_contracts,
        "tools": sorted(tools),
        "commands": sorted(commands),
        "models": sorted(models),
        "schemas": schema_status,
        "security": {
            "shell_false": shell_false,
            "abort_propagation": abort_propagation,
            "abort_escalation": abort_escalation,
            "output_cap_50_kib": output_cap,
            "fixed_science_cli": fixed_cli,
            "credential_scrubbed_science_cli": credential_scrubbed_science_cli,
            "isolated_python_runtime": isolated_python_runtime,
            "authenticated_tool_receipts": authenticated_tool_receipts,
            "honest_subagent_transport_contract": honest_subagent_transport_contract,
            "isolated_child_flags": isolated_child,
            "json_event_transport": json_event_transport,
            "safe_child_progress": safe_child_progress,
            "human_facing_contract_names": human_facing_contract_names,
            "diagnostic_timeouts": diagnostic_timeouts,
            "role_thinking_policy": role_thinking_policy,
            "credential_references_only": credential_references_only,
            "https_only": https_only,
            "qwen_thinking_compat": qwen_compat,
            "qwen_completion_token_field": qwen_completion_token_field,
            "qwen_provider_route_isolated": qwen_provider_route_isolated,
            "doctor_uses_auth_status": doctor_uses_auth_status,
            "doctor_checks_python_dependencies": doctor_checks_python_dependencies,
            "realpath_agent_boundary": realpath_agent_boundary,
            "explicit_extension_isolation": explicit_extension_isolation,
            "dashscope_host_allowlist": dashscope_host_allowlist,
            "qwen_model_allowlist": qwen_model_allowlist,
            "model_route_allowlist": model_route_allowlist,
            "compact_tool_protocol": compact_tool_protocol,
            "parent_bound_scientific_tool_roles": parent_bound_scientific_tool_roles,
            "qwen_secret_redaction": qwen_secret_redaction,
            "no_builtin_file_tools": no_builtin_file_tools,
            "project_scoped_read_tools": project_scoped_read_tools,
            "protected_path_filter": protected_path_filter,
            "anchored_project_root": anchored_project_root,
        },
        "credential_presence": {
            "DASHSCOPE_API_KEY": bool(os.environ.get("DASHSCOPE_API_KEY")),
            "QWEN_API_KEY": bool(os.environ.get("QWEN_API_KEY")),
        },
    }
    return report


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]
    root = Path(args[0]) if args else Path(__file__).resolve().parents[1]
    report = verify_pi_extensions(root)
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
