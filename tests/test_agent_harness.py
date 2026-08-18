from pathlib import Path

import pytest

from jw.agent import _validate_agent_harness
from jw.agent_harness import (
    AUTONOMY_CONTRACT,
    CAPABILITY_MANIFEST,
    DEEP_AGENT_CORE_TOOLS,
    HARNESS_VERSION,
    RUN_MODES,
    attach_harness_metadata,
    validate_capability_manifest,
)
from jw.middleware.background import BackgroundExecutionMiddleware
from jw.prompts import get_system_prompt
from jw.tools import get_builtin_tool_registry, get_main_agent_tools, get_tool_bundles
from jw.utils import load_subagents

ROOT = Path(__file__).resolve().parents[1]


def test_capability_manifest_resolves_to_real_runtime_owners() -> None:
    bundles = get_tool_bundles()
    subagents = load_subagents(
        ROOT / "jw" / "subagents",
        tool_registry=get_builtin_tool_registry(),
        tool_bundles=bundles,
    )

    runtime_tools = set(DEEP_AGENT_CORE_TOOLS)
    runtime_tools.update(tool.name for tool in get_main_agent_tools())
    runtime_tools.update(
        {"run_in_background", "check_process", "stop_process", "list_processes"}
    )

    assert (
        validate_capability_manifest(
            tool_bundle_names=bundles,
            specialist_names=[spec["name"] for spec in subagents],
            runtime_tool_names=runtime_tools,
        )
        == []
    )
    assert {"workspace", "web_research", "code_execution"} <= set(CAPABILITY_MANIFEST)
    assert {
        "research_planning",
        "solar_data",
        "scientific_hypothesis",
        "solar_experiment",
        "evidence_review",
        "research_release",
    } <= set(CAPABILITY_MANIFEST)


def test_manifest_reports_a_missing_required_runtime_tool() -> None:
    missing = validate_capability_manifest(
        tool_bundle_names=get_tool_bundles(),
        specialist_names=[
            "research-agent",
            "code-agent",
            "debug-agent",
            "data-analysis-agent",
            "solar-planner",
            "solar-data",
            "solar-hypothesis",
            "solar-experiment",
            "solar-evidence",
            "solar-knowledge",
            "scheduler",
        ],
        runtime_tool_names=set(),
    )

    assert "workspace:runtime_tool:read_file" in missing
    assert "code_execution:runtime_tool:execute" in missing


def test_agent_build_validates_tools_from_its_actual_middleware() -> None:
    bundles = get_tool_bundles()
    subagents = load_subagents(
        ROOT / "jw" / "subagents",
        tool_registry=get_builtin_tool_registry(),
        tool_bundles=bundles,
    )
    kwargs = {
        "tool_bundles": bundles,
        "main_tools": get_main_agent_tools(),
    }

    _validate_agent_harness(
        subagents,
        middleware=[BackgroundExecutionMiddleware()],
        **kwargs,
    )
    with pytest.raises(RuntimeError, match="run_in_background"):
        _validate_agent_harness(subagents, middleware=[], **kwargs)


def test_system_prompt_makes_internal_routing_the_agents_responsibility() -> None:
    prompt = get_system_prompt()

    assert AUTONOMY_CONTRACT in prompt
    assert "does not need to know agent names" in prompt
    assert "Internal model, specialist, and tool choices" in prompt
    assert "Scientific rigor is the system's default responsibility" in prompt
    assert 'Never require the user to say "do not fabricate"' in prompt
    assert "ask the user which code generation mode" not in prompt
    assert prompt.index("# Independent Agent Contract") < prompt.index(
        "# Experiment Workflow"
    )


def test_harness_route_metadata_is_deterministic() -> None:
    route = attach_harness_metadata(
        {
            "mode": "verified_analysis",
            "source_mode": "mixed",
            "needs_computation": False,
            "task_intent": "hypothesis_generation",
            "required_specialist": "solar-hypothesis",
            "reason": "test",
        }
    )

    assert route["harness_version"] == HARNESS_VERSION
    assert route["capability_id"] == "scientific_hypothesis"
    assert set(RUN_MODES) == {"fast_answer", "verified_analysis", "full_research"}
