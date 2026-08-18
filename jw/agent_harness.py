"""Product-level capability and runtime contracts for the JW agent.

The execution engine remains Deep Agents/LangGraph plus the existing routing
and research-review middleware.  This module is the small declarative layer
that tells those pieces what product they collectively form.  It deliberately
contains names and policies, not tool implementations or a second state
machine.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

HARNESS_VERSION = "agent-runtime-harness-v1"
DEEP_AGENT_CORE_TOOLS = frozenset(
    {
        "write_todos",
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "grep",
        "execute",
        "task",
    }
)


@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    """One user-facing ability and the internal owners that provide it."""

    capability_id: str
    purpose: str
    specialists: tuple[str, ...] = ()
    tool_bundles: tuple[str, ...] = ()
    required_runtime_tools: tuple[str, ...] = ()
    optional_runtime_tools: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RunModeSpec:
    """One harness route and its autonomy boundary."""

    mode: str
    use_when: str
    completion_rule: str


_CAPABILITIES = (
    CapabilitySpec(
        "direct_answer",
        "Explain, edit, or answer stable questions without inventing a workflow.",
    ),
    CapabilitySpec(
        "workspace",
        "Inspect user files and produce task-scoped, reproducible artifacts.",
        required_runtime_tools=(
            "ls",
            "glob",
            "grep",
            "read_file",
            "write_file",
            "edit_file",
        ),
    ),
    CapabilitySpec(
        "web_research",
        "Find and inspect external sources with traceable URLs.",
        specialists=("research-agent",),
        tool_bundles=("web-search",),
    ),
    CapabilitySpec(
        "code_execution",
        "Implement, run, debug, and monitor reproducible computations.",
        specialists=("code-agent", "debug-agent"),
        required_runtime_tools=(
            "execute",
            "run_in_background",
            "check_process",
            "stop_process",
        ),
    ),
    CapabilitySpec(
        "task_management",
        "Plan multi-step work and delegate bounded tasks to specialists.",
        required_runtime_tools=("write_todos", "task"),
    ),
    CapabilitySpec(
        "analysis",
        "Analyze data, uncertainty, validation, and visual results.",
        specialists=("data-analysis-agent",),
    ),
    CapabilitySpec(
        "research_planning",
        "Turn a solar-science question into an auditable research plan.",
        specialists=("solar-planner",),
        tool_bundles=("research-planner", "research-quality"),
    ),
    CapabilitySpec(
        "solar_data",
        "Inspect and prepare solar-activity data with provenance checks.",
        specialists=("solar-data",),
        tool_bundles=("solar-features",),
    ),
    CapabilitySpec(
        "scientific_hypothesis",
        "Generate and revise falsifiable, mechanism-distinct hypotheses.",
        specialists=("solar-hypothesis",),
        tool_bundles=(
            "knowledge-base-readonly",
            "scientific-hypothesis",
            "research-quality",
        ),
    ),
    CapabilitySpec(
        "solar_experiment",
        "Design and execute leakage-aware solar-activity experiments.",
        specialists=("solar-experiment",),
        tool_bundles=("automatic-experiment", "research-quality"),
    ),
    CapabilitySpec(
        "evidence_review",
        "Review claim-level evidence, counterevidence, and method limits.",
        specialists=("solar-evidence",),
        tool_bundles=("evidence-review", "knowledge-base-inspection"),
    ),
    CapabilitySpec(
        "research_release",
        "Synthesize only reviewed claims and enforce the release boundary.",
        tool_bundles=("research-release",),
    ),
    CapabilitySpec(
        "knowledge",
        "Read or maintain the solar-cycle knowledge base under its decision gates.",
        specialists=("solar-knowledge",),
        tool_bundles=("knowledge-base",),
    ),
    CapabilitySpec(
        "memory_and_skills",
        "Reuse persistent lessons and install task-relevant skills.",
        required_runtime_tools=("skill_manager",),
    ),
    CapabilitySpec(
        "scheduling",
        "Schedule or monitor work when the deployment enables it.",
        specialists=("scheduler",),
        optional_runtime_tools=("schedule_task",),
    ),
    CapabilitySpec(
        "clarification",
        "Ask only for indispensable user information that tools cannot recover.",
        optional_runtime_tools=("ask_user",),
    ),
)

CAPABILITY_MANIFEST: Mapping[str, CapabilitySpec] = MappingProxyType(
    {spec.capability_id: spec for spec in _CAPABILITIES}
)

RUN_MODES: Mapping[str, RunModeSpec] = MappingProxyType(
    {
        spec.mode: spec
        for spec in (
            RunModeSpec(
                "fast_answer",
                "A stable answer or transformation needs no evidence inspection.",
                "Answer the request directly and state any material uncertainty.",
            ),
            RunModeSpec(
                "verified_analysis",
                "A bounded answer depends on files, sources, data, or computation.",
                "Inspect the required evidence, perform the bounded work, and report "
                "what was actually verified.",
            ),
            RunModeSpec(
                "full_research",
                "The requested outcome is a coherent, reviewable research package.",
                "Follow ResearchRunStateV2 until the Evidence-reviewed result is "
                "released, blocked, or reaches another honest terminal state.",
            ),
        )
    }
)


AUTONOMY_CONTRACT = """# Independent Agent Contract

Own the user's outcome, not just the next internal step. The user may describe a
research goal in ordinary language and does not need to know agent names, tool
names, route names, review rounds, or graph stages. Infer the smallest sufficient
run mode, choose the capabilities, call the real tools, inspect their results,
recover from bounded failures, and deliver the best evidence-supported outcome.

- Ask a clarification only when missing information would materially change the
  scientific question, authorization, cost, or safety boundary and cannot be
  recovered from the conversation, workspace, project material, or permitted
  sources. Otherwise state a bounded assumption and proceed.
- Internal model, specialist, and tool choices are implementation decisions. Do
  not ask the user to choose them unless the user explicitly requests control or
  the choice materially changes cost or the requested deliverable.
- Scientific rigor is the system's default responsibility, not a prompt-writing
  burden placed on the researcher. For research questions, proactively verify
  provenance, separate observation from inference, seek the strongest relevant
  counterevidence or null explanation, preserve uncertainty and negative results,
  and run the configured Evidence review without waiting for the user to request
  each safeguard.
- Never require the user to say "do not fabricate". Unsupported data, citations,
  mechanisms, novelty, or certainty must not be invented under any run mode. If
  decisive evidence is unavailable, return an evidence gap or bounded hypothesis.
- A tool registration is not evidence of success. Read the result and distinguish
  static knowledge, retrieved evidence, executed computation, automated checks,
  and scientific validation.
- Keep the user informed at meaningful checkpoints for long work. Progress must
  name the current outcome, new artifact or evidence, and any real blocker; do not
  emit repetitive status text.
- Stop when the requested outcome is delivered, a truthful negative or
  insufficient-evidence result is established, or the deterministic
  budget/no-progress policy stops the run. Never continue merely to manufacture
  a positive conclusion.
"""


def capability_for_route(route: Mapping[str, object]) -> str:
    """Return the user-facing capability selected by a normalized route."""

    mode = route.get("mode")
    if mode == "full_research":
        return "research_release"
    specialist = route.get("required_specialist")
    return {
        "solar-planner": "research_planning",
        "solar-data": "solar_data",
        "solar-hypothesis": "scientific_hypothesis",
        "solar-experiment": "solar_experiment",
    }.get(str(specialist), "direct_answer" if mode == "fast_answer" else "analysis")


def attach_harness_metadata(route: Mapping[str, object]) -> dict[str, object]:
    """Annotate a route without changing its scientific routing decision."""

    normalized = dict(route)
    normalized["harness_version"] = HARNESS_VERSION
    normalized["capability_id"] = capability_for_route(normalized)
    return normalized


def validate_capability_manifest(
    *,
    tool_bundle_names: Iterable[str],
    specialist_names: Iterable[str],
    runtime_tool_names: Iterable[str] | None = None,
) -> list[str]:
    """Return missing manifest dependencies for startup/tests to report."""

    bundles = set(tool_bundle_names)
    specialists = set(specialist_names)
    runtime_tools = set(runtime_tool_names) if runtime_tool_names is not None else None
    missing: list[str] = []
    for spec in CAPABILITY_MANIFEST.values():
        missing.extend(
            f"{spec.capability_id}:tool_bundle:{name}"
            for name in spec.tool_bundles
            if name not in bundles
        )
        missing.extend(
            f"{spec.capability_id}:specialist:{name}"
            for name in spec.specialists
            if name not in specialists
        )
        if runtime_tools is not None:
            missing.extend(
                f"{spec.capability_id}:runtime_tool:{name}"
                for name in spec.required_runtime_tools
                if name not in runtime_tools
            )
    return missing


def render_capability_summary() -> str:
    """Render the compact capability layer included in the system prompt."""

    lines = ["# Capability Map"]
    for spec in CAPABILITY_MANIFEST.values():
        lines.append(f"- `{spec.capability_id}`: {spec.purpose}")
    lines.append(
        "Capabilities may be delegated to specialists or exposed through tools. "
        "Use the capability that fits the outcome; do not require the user to "
        "name its implementation."
    )
    return "\n".join(lines)


__all__ = [
    "AUTONOMY_CONTRACT",
    "CAPABILITY_MANIFEST",
    "DEEP_AGENT_CORE_TOOLS",
    "HARNESS_VERSION",
    "RUN_MODES",
    "CapabilitySpec",
    "RunModeSpec",
    "attach_harness_metadata",
    "capability_for_route",
    "render_capability_summary",
    "validate_capability_manifest",
]
