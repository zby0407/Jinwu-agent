"""LangChain tool wrappers for the B3 Three-Agent science workflow.

Exposes the B3 solar-cycle co-scientist primitives (``EvoScientist.b3cycle``)
as ``@tool`` functions the EvoScientist agent can call directly:

- Run lifecycle: immutable ``RunStore`` artifacts (run manifest, frozen
  ResearchPlan 1.0, calibrated HypothesisPortfolio 1.0, experiment bundles).
- Pre-registered experiments E0-E8 executed through the fixed isolated-worker
  analysis boundary.
- The 17-spec scientific toolkit with role-scoped authorization, HMAC-signed
  execution receipts, and fail-closed claimability.

Security model
--------------
The B3 toolkit only serves the three registered agents
(``b3-research-planner``, ``b3-experiment``, ``b3-hypothesis``) and requires
``B3_ACTIVE_AGENT`` to be bound by a trusted parent before every call
(``science_toolkit._effective_agent``). This module acts as that trusted
parent: each tool binds ``B3_ACTIVE_AGENT`` for the duration of the call via
the optional ``agent`` parameter (default ``b3-experiment``). A parent-held
``B3_TOOL_RECEIPT_HMAC_KEY`` is generated on import when absent so
``run_scientific_tool`` can issue authenticated receipts.

Related B3 skills: research-planner-agent, hypothesis-agent, experiment-agent.
"""

import json
import os
import secrets
from typing import Any

from langchain_core.tools import tool

from EvoScientist.b3cycle.data import b3_root
from EvoScientist.b3cycle.science_agents import (
    RunStore,
    ScienceAgentError,
    preflight_registered_experiment,
    run_registered_experiment,
    submit_hypothesis_portfolio_draft,
    submit_research_plan_draft,
    validate_hypothesis_portfolio_against_run,
)
from EvoScientist.b3cycle.science_toolkit import (
    ScientificToolkitError,
    discover_tools,
    inspect_tool,
    run_scientific_tool,
    trace_artifact_lineage,
    verify_tool_result,
)

# Registered B3 agents, mirrored from science_toolkit._ALL_AGENTS.
B3_AGENTS = ("b3-research-planner", "b3-experiment", "b3-hypothesis")
DEFAULT_B3_AGENT = "b3-experiment"

# Immutable run store. Resolved through the same canonical resolver the
# toolkit itself uses (b3_root()/agent_runs ==
# <repo>/b3/agent_runs), so wrappers and toolkit always agree.
RUNS_ROOT = b3_root() / "agent_runs"


def _ensure_b3_environment() -> None:
    """Provide the parent-held HMAC receipt key when none was supplied."""

    if not os.getenv("B3_TOOL_RECEIPT_HMAC_KEY"):
        os.environ["B3_TOOL_RECEIPT_HMAC_KEY"] = secrets.token_hex(32)


_ensure_b3_environment()


def _run_store() -> RunStore:
    return RunStore(RUNS_ROOT)


def _json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _error_json(exc: Exception) -> str:
    return _json(
        {
            "status": "error",
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
    )


def _parse_json_arg(raw: str, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ScienceAgentError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ScienceAgentError(f"{label} must be a JSON object")
    return payload


def _bind_agent(agent: str) -> str:
    """Bind B3_ACTIVE_AGENT as the trusted parent and return the bound role."""

    bound = (agent or "").strip() or DEFAULT_B3_AGENT
    if bound not in B3_AGENTS:
        raise ScientificToolkitError(
            f"agent must be one of {', '.join(B3_AGENTS)}; got {bound!r}"
        )
    os.environ["B3_ACTIVE_AGENT"] = bound
    return bound


def _read_optional_artifact(
    store: RunStore, run_id: str, relative_path: str
) -> tuple[dict[str, Any] | None, str | None]:
    """Read an artifact if present; return (payload, error) — never raises."""

    run_dir = store.root / run_id
    if not (run_dir / relative_path).is_file():
        return None, None
    try:
        return store.read_artifact(run_id, relative_path), None
    except ScienceAgentError as exc:
        return None, str(exc)


@tool(parse_docstring=True)
def b3_init_science_run(task: str) -> str:
    """Create a new immutable B3 science run for an exact research question.

    This is the entry point of the B3 Three-Agent workflow
    (research-planner-agent, hypothesis-agent, experiment-agent). It writes
    ``run_manifest.json`` with an exact task binding; every later artifact in
    the run must hash-chain back to it. The task string becomes the frozen
    research question, so phrase it as the final claimable question.

    Args:
        task: Exact research question the run is bound to (verbatim; a later
            ResearchPlan's research_question must match it exactly).

    Returns:
        JSON with run_id, task, created_at, and the run_manifest.json path.
    """
    try:
        store = _run_store()
        manifest = store.create_run(task)
        run_id = str(manifest["run_id"])
        return _json(
            {
                "status": "ok",
                "run_id": run_id,
                "task": manifest["task"],
                "created_at": manifest.get("created_at"),
                "manifest_path": str(store.root / run_id / "run_manifest.json"),
            }
        )
    except Exception as exc:
        return _error_json(exc)


@tool(parse_docstring=True)
def b3_read_run_state(run_id: str) -> str:
    """Read and hash-validate the current state of a B3 science run.

    Returns the frozen plan status, calibrated hypothesis-portfolio status,
    and every experiment bundle status recorded so far. Use this to orient
    before deciding the next step of the research-planner-agent /
    experiment-agent / hypothesis-agent pipeline.

    Args:
        run_id: The run identifier returned by b3_init_science_run.

    Returns:
        JSON with run manifest summary, research plan status, hypothesis
        portfolio status, and a list of experiment statuses. Missing
        artifacts are reported as null; corrupted ones carry an error note.
    """
    try:
        store = _run_store()
        manifest = store.read_artifact(run_id, "run_manifest.json")

        plan, plan_error = _read_optional_artifact(store, run_id, "research_plan.json")
        portfolio, portfolio_error = _read_optional_artifact(
            store, run_id, "hypothesis_portfolio.json"
        )

        experiments: list[dict[str, Any]] = []
        experiments_dir = store.root / run_id / "experiments"
        if experiments_dir.is_dir():
            for path in sorted(experiments_dir.glob("*/manifest.json")):
                relative = path.relative_to(store.root / run_id).as_posix()
                try:
                    exp = store.read_artifact(run_id, relative)
                    experiments.append(
                        {
                            "path": relative,
                            "node_id": exp.get("node_id"),
                            "experiment_id": exp.get("experiment_id"),
                            "seed": exp.get("seed"),
                            "status": exp.get("status"),
                            "claim_effect": exp.get("claim_effect"),
                        }
                    )
                except ScienceAgentError as exc:
                    experiments.append({"path": relative, "error": str(exc)})

        return _json(
            {
                "status": "ok",
                "run_id": run_id,
                "task": manifest.get("task"),
                "created_at": manifest.get("created_at"),
                "research_plan": (
                    None
                    if plan is None
                    else {
                        "status": plan.get("status"),
                        "frozen_hash": plan.get("frozen_hash"),
                        "task_graph_nodes": len(plan.get("task_graph", [])),
                    }
                ),
                "research_plan_error": plan_error,
                "hypothesis_portfolio": (
                    None
                    if portfolio is None
                    else {
                        "status": portfolio.get("status"),
                        "hypothesis_count": len(portfolio.get("hypotheses", [])),
                    }
                ),
                "hypothesis_portfolio_error": portfolio_error,
                "experiments": experiments,
            }
        )
    except Exception as exc:
        return _error_json(exc)


@tool(parse_docstring=True)
def b3_submit_research_plan(run_id: str, plan_json: str) -> str:
    """Validate, freeze, hash, and immutably persist a ResearchPlan 1.0.

    The research-planner-agent owns this step. The draft must omit the
    deterministic envelope fields (run_id, created_at, status, frozen_hash,
    artifact_sha256) — they are injected by the store. The plan's
    research_question must exactly match the run's bound task, and the
    task_graph must reference registered tools as ``registered:<E0-E8 id>``.
    A plan can only be submitted once; the artifact is immutable.

    Args:
        run_id: Target run identifier.
        plan_json: JSON string of the ResearchPlan 1.0 draft object.

    Returns:
        JSON of the frozen, hash-stamped plan as persisted, or an error.
    """
    try:
        plan = _parse_json_arg(plan_json, "plan_json")
        frozen = submit_research_plan_draft(_run_store(), run_id, plan)
        return _json({"status": "ok", "research_plan": frozen})
    except Exception as exc:
        return _error_json(exc)


@tool(parse_docstring=True)
def b3_submit_hypothesis_portfolio(run_id: str, portfolio_json: str) -> str:
    """Calibrate, cross-check, hash, and persist a HypothesisPortfolio 1.0.

    The hypothesis-agent owns this step. Hypothesis cards are calibrated
    through the deterministic balanced tournament, then validated against the
    frozen plan and cited run artifacts (sources, experiment statuses).
    Requires a frozen research plan to exist. Immutable once submitted.

    Args:
        run_id: Target run identifier.
        portfolio_json: JSON string of the HypothesisPortfolio 1.0 draft.

    Returns:
        JSON of the calibrated, hash-stamped portfolio as persisted, or an
        error describing the violated contract.
    """
    try:
        portfolio = _parse_json_arg(portfolio_json, "portfolio_json")
        stored = submit_hypothesis_portfolio_draft(_run_store(), run_id, portfolio)
        return _json({"status": "ok", "hypothesis_portfolio": stored})
    except Exception as exc:
        return _error_json(exc)


@tool(parse_docstring=True)
def b3_validate_hypothesis_portfolio(run_id: str) -> str:
    """Re-validate the stored HypothesisPortfolio against run artifacts.

    Re-runs the hypothesis-agent's cross-checks: portfolio schema, run_id
    binding, source ids present in the frozen plan, cited artifact hashes and
    evidence statuses. Use after new experiment results land to confirm the
    portfolio still holds against the updated evidence base.

    Args:
        run_id: Run containing a submitted hypothesis_portfolio.json.

    Returns:
        JSON with validated=true and the portfolio, or an error listing the
        first violated cross-check.
    """
    try:
        store = _run_store()
        portfolio = store.read_artifact(run_id, "hypothesis_portfolio.json")
        validated = validate_hypothesis_portfolio_against_run(store, run_id, portfolio)
        return _json({"status": "ok", "validated": True, "portfolio": validated})
    except Exception as exc:
        return _error_json(exc)


@tool(parse_docstring=True)
def b3_preflight_experiment(
    run_id: str, experiment_id: str, plan_node_id: str, seed: int
) -> str:
    """Preflight-check a registered experiment node without executing it.

    The experiment-agent uses this before committing compute: verifies the
    frozen plan, the node's tool binding (``registered:<experiment_id>``),
    readiness, seed, wall-time budget, DAG dependency satisfaction, and that
    the immutable target path is still free.

    Args:
        run_id: Target run identifier.
        experiment_id: Registered experiment id, one of E0_data_vintage_audit,
            E1_cycle_segmentation_baseline, E2_waldmeier_leave_one_cycle_out,
            E3_f107_phase_stratified_drift, E4_extended_hemispheric_calibration,
            E5_polar_precursor_robustness, E6_low_order_dynamo_family_ablation,
            E7_negative_controls_and_placebos, E8_clean_reproduction.
        plan_node_id: The task_graph node id in the frozen plan.
        seed: Non-negative integer seed pinned by the plan node.

    Returns:
        JSON preflight report with status "ready" or "blocked" plus reasons.
    """
    try:
        report = preflight_registered_experiment(
            _run_store(), run_id, experiment_id, plan_node_id, seed
        )
        return _json({"status": "ok", "preflight": report})
    except Exception as exc:
        return _error_json(exc)


@tool(parse_docstring=True)
def b3_run_registered_experiment(
    run_id: str, experiment_id: str, plan_node_id: str, seed: int
) -> str:
    """Run one pre-registered experiment (E0-E8) through the B3 boundary.

    The experiment-agent owns this step. Execution happens in the fixed
    isolated Python worker with wall/cpu budgets from the frozen plan node;
    results and manifest are written as an immutable bundle under
    ``experiments/<experiment_id>_seed<seed>/``. A node can only run once per
    seed — replay requires a new seed or a new run. Run
    b3_preflight_experiment first when in doubt.

    Args:
        run_id: Target run identifier.
        experiment_id: Registered experiment id (E0_* through E8_*).
        plan_node_id: The task_graph node id in the frozen plan.
        seed: Non-negative integer seed pinned by the plan node.

    Returns:
        JSON of the experiment manifest (status, gates, result, provenance,
        claim_effect). Note status "failed" is still a recorded immutable
        outcome, not a tool error.
    """
    try:
        manifest = run_registered_experiment(
            _run_store(), run_id, experiment_id, plan_node_id, seed
        )
        return _json({"status": "ok", "experiment_manifest": manifest})
    except Exception as exc:
        return _error_json(exc)


@tool(parse_docstring=True)
def b3_discover_tools(query: str = "", limit: int = 10, agent: str = "") -> str:
    """Discover role-authorized scientific tools from the B3 toolkit.

    Searches the 17-spec toolkit registry (research, planning, experiment,
    hypothesis, audit categories) and returns only the tools the bound agent
    role is authorized to call. The three B3 skills map to roles:
    research-planner-agent -> b3-research-planner, hypothesis-agent ->
    b3-hypothesis, experiment-agent -> b3-experiment.

    Args:
        query: Free-text filter matched against tool id, category, and
            description; empty returns every authorized tool.
        limit: Maximum number of tools to return (1-50).
        agent: B3 role to bind for this call — b3-research-planner,
            b3-experiment, or b3-hypothesis. Empty uses the default role.

    Returns:
        JSON with matching tool specs (tool_id, category, description,
        authorized agents, network policy).
    """
    try:
        bound = _bind_agent(agent)
        result = discover_tools(query=query, agent=bound, limit=limit)
        return _json({"status": "ok", **result})
    except Exception as exc:
        return _error_json(exc)


@tool(parse_docstring=True)
def b3_inspect_tool(tool_id: str, agent: str = "") -> str:
    """Inspect one B3 scientific tool's schema, contract, and network policy.

    Returns the tool's input schema, output contract, claim policy, and
    network policy so the caller can construct a valid b3_run_tool input.
    Only tools authorized for the bound role can be inspected.

    Args:
        tool_id: Registered toolkit id, e.g. "research.query_evidence",
            "experiment.compare_results", "audit.verify_claim_links".
        agent: B3 role to bind — b3-research-planner, b3-experiment, or
            b3-hypothesis. Empty uses the default role.

    Returns:
        JSON with the tool spec details, or an authorization error.
    """
    try:
        bound = _bind_agent(agent)
        result = inspect_tool(tool_id, bound)
        return _json({"status": "ok", **result})
    except Exception as exc:
        return _error_json(exc)


@tool(parse_docstring=True)
def b3_run_tool(tool_id: str, input_json: str, agent: str = "") -> str:
    """Run a B3 scientific toolkit tool with an HMAC-signed receipt.

    Executes one registered toolkit tool (see b3_discover_tools) under the
    bound role's authorization. The returned envelope carries input/output
    hashes, provenance, a fail-closed claimability evaluation, and a
    parent-authenticated execution receipt persisted to the receipts
    directory. Verify the envelope afterwards with b3_verify_result before
    letting any claim depend on it.

    Args:
        tool_id: Registered toolkit id authorized for the bound role.
        input_json: JSON string of the tool input object, matching the
            tool's input schema from b3_inspect_tool.
        agent: B3 role to bind — b3-research-planner, b3-experiment, or
            b3-hypothesis. Empty uses the default role.

    Returns:
        JSON tool-result envelope (status, result, claimable, claimability,
        receipt, errors).
    """
    try:
        bound = _bind_agent(agent)
        payload = _parse_json_arg(input_json, "input_json")
        envelope = run_scientific_tool(tool_id, payload, bound)
        return _json({"status": "ok", "envelope": envelope})
    except Exception as exc:
        return _error_json(exc)


@tool(parse_docstring=True)
def b3_verify_result(envelope_json: str, agent: str = "") -> str:
    """Verify a B3 tool-result envelope before any claim relies on it.

    Re-checks the envelope structure, schema/tool versions, input and output
    hashes, claimability contract, and the persisted HMAC execution receipt
    (signature and envelope hash). The bound role must match the agent that
    originally ran the tool — pass the same ``agent`` used in b3_run_tool.
    Verification fails closed: any violation means verified=false.

    Args:
        envelope_json: JSON string of the envelope returned by b3_run_tool.
        agent: B3 role that produced the envelope — b3-research-planner,
            b3-experiment, or b3-hypothesis. Empty uses the default role.

    Returns:
        JSON with verified (bool), call_id, verification basis, and the list
        of violations found.
    """
    try:
        bound = _bind_agent(agent)
        envelope = _parse_json_arg(envelope_json, "envelope_json")
        verdict = verify_tool_result(envelope, bound)
        return _json({"status": "ok", "verification": verdict})
    except Exception as exc:
        return _error_json(exc)


@tool(parse_docstring=True)
def b3_trace_artifact(run_id: str, artifact_path: str, agent: str = "") -> str:
    """Trace one run artifact's lineage: hash, parent plan, data sources.

    Hash-validates the artifact through the run store and reports its schema
    version, parent node, experiment id and seed, the frozen plan's hash, the
    data sources it consumed, worker code hashes, and related artifact
    references with per-reference verification. This is the audit backbone
    for hypothesis-agent evidence citations and claim reviews.

    Args:
        run_id: Run containing the artifact.
        artifact_path: Artifact path relative to the run directory, e.g.
            "research_plan.json" or
            "experiments/E1_cycle_segmentation_baseline_seed42/manifest.json".
        agent: B3 role to bind — b3-research-planner, b3-experiment, or
            b3-hypothesis. Empty uses the default role.

    Returns:
        JSON lineage record (artifact_sha256, parent_id, data_sources,
        plan_artifact_sha256, related_artifacts).
    """
    try:
        bound = _bind_agent(agent)
        lineage = trace_artifact_lineage(run_id, artifact_path, bound)
        return _json({"status": "ok", "lineage": lineage})
    except Exception as exc:
        return _error_json(exc)


B3_SCIENCE_TOOLS = [
    b3_init_science_run,
    b3_read_run_state,
    b3_submit_research_plan,
    b3_submit_hypothesis_portfolio,
    b3_validate_hypothesis_portfolio,
    b3_preflight_experiment,
    b3_run_registered_experiment,
    b3_discover_tools,
    b3_inspect_tool,
    b3_run_tool,
    b3_verify_result,
    b3_trace_artifact,
]

__all__ = [
    "B3_AGENTS",
    "B3_SCIENCE_TOOLS",
    "DEFAULT_B3_AGENT",
    "RUNS_ROOT",
    "b3_discover_tools",
    "b3_init_science_run",
    "b3_inspect_tool",
    "b3_preflight_experiment",
    "b3_read_run_state",
    "b3_run_registered_experiment",
    "b3_run_tool",
    "b3_submit_hypothesis_portfolio",
    "b3_submit_research_plan",
    "b3_trace_artifact",
    "b3_validate_hypothesis_portfolio",
    "b3_verify_result",
]
