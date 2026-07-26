"""LangChain tool wrappers for the Pi-style Scientific Hypothesis Python bridge.

These tools expose the scientific-hypothesis-agent skill to the JW
agent. They wrap deterministic contract validation, evidence registration, and
portfolio freeze operations implemented in ``src/scientific_hypothesis``.

The workflow mirrors the Pi extension: bind one request, register verified
evidence, submit the full response for a single-shot preflight check, then
freeze the validated portfolio. State is session-local, exactly like the Pi
tool lifecycle.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SRC = _PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from langchain_core.runnables import RunnableConfig  # noqa: E402
from langchain_core.tools import tool  # noqa: E402

from jw.workspaces import (  # noqa: E402
    resolve_scoped_path,
    workspace_context_key,
    workspace_root_from_config,
)
from scientific_hypothesis.contracts import (  # noqa: E402
    canonical_json_sha256,
    validate_hypothesis_request,
)
from scientific_hypothesis.harness import (  # noqa: E402
    EvidenceRegister,
    build_hypothesis_brief,
    build_natural_hypothesis_request,
    freeze_hypothesis_portfolio,
    preflight_hypothesis_ranking,
    preflight_hypothesis_response,
)
from scientific_hypothesis.upstream import inspect_experiment_run  # noqa: E402

MAX_EVIDENCE_BINDS = 20


@dataclass(slots=True)
class _HypothesisState:
    request: dict[str, Any] | None = None
    request_sha256: str = ""
    evidence_register: EvidenceRegister = field(default_factory=EvidenceRegister)
    validated_response: dict[str, Any] | None = None
    preflight_response_sha256: str | None = None
    preflight_attempts: int = 0
    validated_ranking: dict[str, Any] | None = None
    preflight_ranking_sha256: str | None = None
    ranking_attempts: int = 0


_STATES: dict[str, _HypothesisState] = {}
_STATE_LOCK = RLock()


def _ok(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, default=str)


def _needs_revision(exc: Exception) -> str:
    return json.dumps(
        {
            "schema_version": "scientific-hypothesis-outcome-v1",
            "status": "needs_revision",
            "validation_error": str(exc),
            "user_message": "假设组合仍在内部校正；请保留已正确内容并一次性修正全部列出问题。",
        },
        ensure_ascii=False,
        default=str,
    )


def _state(config: RunnableConfig | None) -> _HypothesisState:
    context = workspace_context_key(config)
    with _STATE_LOCK:
        return _STATES.setdefault(context, _HypothesisState())


def _require_active_request(state: _HypothesisState) -> dict[str, Any]:
    if state.request is None:
        raise RuntimeError(
            "No hypothesis request is bound. Call scientific_hypothesis_bind_request first."
        )
    return state.request


def _invalidate_validated_state(state: _HypothesisState) -> None:
    """Prevent freeze from using a response checked against older inputs."""

    state.validated_response = None
    state.preflight_response_sha256 = None
    state.validated_ranking = None
    state.preflight_ranking_sha256 = None


def _resolve_request_path(value: str, config: RunnableConfig | None) -> Path:
    """Resolve task/project inputs, then the bundled hypothesis examples only."""

    candidate = resolve_scoped_path(value, config, allow_project=True)
    if candidate.is_file():
        return candidate

    normalized = value.replace("\\", "/").lstrip("/")
    prefix = "hypothesis/inputs/"
    if not normalized.startswith(prefix):
        raise FileNotFoundError(candidate)
    relative = normalized.removeprefix(prefix)
    bundled_root = (_PROJECT_ROOT / "hypothesis" / "inputs").resolve()
    bundled = (bundled_root / relative).resolve()
    try:
        bundled.relative_to(bundled_root)
    except ValueError as exc:
        raise ValueError("bundled hypothesis input path escaped its root") from exc
    if not bundled.is_file():
        raise FileNotFoundError(candidate)
    return bundled


@tool(parse_docstring=True)
def scientific_hypothesis_bind_request(
    request_input: str, config: RunnableConfig = None
) -> str:
    """Bind a natural-language research question and return the hypothesis brief.

    This is the entry point for the scientific-hypothesis-agent skill. If
    ``request_input`` starts with ``@``, the remainder is treated as a path
    (relative to the project root) to a JSON request file. Otherwise the input
    is used verbatim as the research question. Binding resets the evidence
    register and any previously validated response.

    Args:
        request_input: A research question or ``@<path-to-json-request>``.

    Returns:
        JSON string containing the hypothesis brief, response contract,
        scientific boundaries, and ``request_sha256``.
    """
    try:
        supplied = request_input.strip()
        if not supplied:
            raise ValueError("Research question must not be empty")
        if supplied.startswith("@"):
            path = _resolve_request_path(supplied[1:].strip(), config)
            payload = json.loads(path.read_text(encoding="utf-8"))
            request = validate_hypothesis_request(payload)
        else:
            request = build_natural_hypothesis_request(supplied)

        brief = build_hypothesis_brief(request)
        context = workspace_context_key(config)
        with _STATE_LOCK:
            _STATES[context] = _HypothesisState(
                request=request,
                request_sha256=brief["request_sha256"],
            )
        return _ok(brief)
    except Exception as exc:
        return _needs_revision(exc)


@tool(parse_docstring=True)
def scientific_hypothesis_inspect_upstream(
    run_path: str, config: RunnableConfig = None
) -> str:
    """Verify one automatic-experiment run before using it as evidence.

    The run must be inside the current task workspace or the explicit
    ``/project/`` shared area. Hashes, finalized status, and a ``completed_*``
    scientific outcome are checked deterministically. Blocked results must be
    recorded only as evidence gaps.

    Args:
        run_path: Agent-visible experiment run directory.

    Returns:
        JSON string with either a verified evidence summary or a blocking reason.
    """
    try:
        resolved = resolve_scoped_path(run_path, config, allow_project=True)
        result = inspect_experiment_run(
            {"run_path": str(resolved)},
            workspace_root_from_config(config),
        )
        return _ok(result)
    except Exception as exc:
        return _needs_revision(exc)


@tool(parse_docstring=True)
def scientific_hypothesis_bind_evidence(
    evidence_id: str,
    evidence_kind: str,
    material_id: str,
    excerpt: str,
    verified_support: bool,
    role: str,
    config: RunnableConfig = None,
) -> str:
    """Register one piece of upstream material as evidence for this task.

    ``verified_support`` may be true only when the material text has actually
    been checked against the claim it supports, opposes, or limits; unchecked
    material must be registered with ``role="gap"``.

    Args:
        evidence_id: Unique identifier for this evidence entry (1-64 chars).
        evidence_kind: One of ``experiment``, ``literature``, ``upstream``, ``user``.
        material_id: Identifier of the upstream material the excerpt comes from.
        excerpt: Verbatim excerpt from the material (1-2000 chars).
        verified_support: Whether the excerpt was verified against the claim.
        role: One of ``supports``, ``opposes``, ``limits``, ``gap``.

    Returns:
        JSON string with the bind outcome and total bound evidence count.
    """
    try:
        state = _state(config)
        _require_active_request(state)
        if len(state.evidence_register) >= MAX_EVIDENCE_BINDS:
            return _ok(
                {
                    "schema_version": "scientific-hypothesis-evidence-bound-v1",
                    "status": "budget_reached",
                    "message": "本次任务的证据绑定已达上限；请只使用已登记证据，把其余内容列为证据缺口。",
                }
            )
        result = state.evidence_register.bind(
            {
                "evidence_id": evidence_id,
                "evidence_kind": evidence_kind,
                "material_id": material_id,
                "excerpt": excerpt,
                "verified_support": verified_support,
                "role": role,
            }
        )
        _invalidate_validated_state(state)
        return _ok(result)
    except Exception as exc:
        return _needs_revision(exc)


@tool(parse_docstring=True)
def scientific_hypothesis_validate_response(
    response_json: str, config: RunnableConfig = None
) -> str:
    """Check one complete hypothesis response against the bound request.

    Structure, evidence references, candidate duplication, confidence
    consistency, and wording boundaries are all reported in a single pass; fix
    every listed issue before resubmitting. A successfully validated
    ``hypotheses_ready`` response is cached for ``scientific_hypothesis_freeze``.

    Args:
        response_json: One JSON string containing a
            scientific-hypothesis-response-v1 object.

    Returns:
        JSON string with the preflight status and issue list.
    """
    try:
        state = _state(config)
        _invalidate_validated_state(state)
        request = _require_active_request(state)
        response = json.loads(response_json)
        if not isinstance(response, dict):
            raise ValueError("response must be a JSON object")
        state.preflight_attempts += 1
        result = preflight_hypothesis_response(
            request,
            response,
            state.evidence_register,
            include_validated_response=True,
        )
        checked = result.pop("_validated_response", None)
        result["preflight_attempt"] = state.preflight_attempts
        if result.get("status") == "hypotheses_ready" and isinstance(checked, dict):
            state.validated_response = checked
            state.preflight_response_sha256 = canonical_json_sha256(checked)
        state.ranking_attempts = 0
        return _ok(result)
    except Exception as exc:
        return _needs_revision(exc)


@tool(parse_docstring=True)
def scientific_hypothesis_rank(ranking_json: str, config: RunnableConfig = None) -> str:
    """Validate and cache one complete seven-dimension candidate ranking.

    Call this only after ``scientific_hypothesis_validate_response`` returns
    ``hypotheses_ready``. The ranking must cover every candidate, use the
    canonical seven dimensions, contain continuous ranks, and anchor every
    cited item to verified supporting evidence.

    Args:
        ranking_json: One JSON string containing a
            scientific-hypothesis-ranking-v1 object.

    Returns:
        JSON string with ranking readiness and deterministic dimension scores.
    """
    state = _state(config)
    state.validated_ranking = None
    state.preflight_ranking_sha256 = None
    try:
        request = _require_active_request(state)
        if state.validated_response is None:
            raise RuntimeError(
                "Ranking requires a successful "
                "scientific_hypothesis_validate_response call first."
            )
        ranking = json.loads(ranking_json)
        if not isinstance(ranking, dict):
            raise ValueError("ranking must be a JSON object")
        state.ranking_attempts += 1
        result = preflight_hypothesis_ranking(
            request,
            state.validated_response,
            ranking,
            state.evidence_register,
            include_validated_ranking=True,
        )
        checked = result.pop("_validated_ranking", None)
        result["ranking_attempt"] = state.ranking_attempts
        if result.get("status") == "ranking_ready" and isinstance(checked, dict):
            # ``checked`` is the normalized portfolio shape and deliberately
            # omits the request-only ``rubric`` declaration. Freeze performs
            # the ranking request validation once more before compiling that
            # normalized shape, so retain the exact payload that passed
            # preflight rather than feeding the normalized output back into
            # the request validator.
            state.validated_ranking = ranking
            state.preflight_ranking_sha256 = canonical_json_sha256(ranking)
        return _ok(result)
    except Exception as exc:
        return _needs_revision(exc)


@tool(parse_docstring=True)
def scientific_hypothesis_freeze(config: RunnableConfig = None) -> str:
    """Freeze the most recently validated hypotheses-ready response.

    Takes no parameters: the response cached by
    ``scientific_hypothesis_validate_response`` is compiled into a portfolio
    and written under ``hypothesis/runs/<run_id>/`` with its reader-facing
    Markdown.

    Args:
        config: Injected LangGraph runtime configuration; callers omit it.

    Returns:
        JSON string with the freeze outcome, run id, file paths, and the
        user-display Markdown.
    """
    try:
        state = _state(config)
        request = _require_active_request(state)
        if state.validated_response is None or state.preflight_response_sha256 is None:
            raise RuntimeError(
                "Save requires a successful scientific_hypothesis_validate_response call first."
            )
        if (
            canonical_json_sha256(state.validated_response)
            != state.preflight_response_sha256
        ):
            raise RuntimeError(
                "Hypothesis response changed after validation; check the revised response first."
            )
        if state.validated_ranking is None or state.preflight_ranking_sha256 is None:
            raise RuntimeError(
                "Save requires a successful scientific_hypothesis_rank call first."
            )
        if (
            canonical_json_sha256(state.validated_ranking)
            != state.preflight_ranking_sha256
        ):
            raise RuntimeError(
                "Hypothesis ranking changed after validation; check the revised ranking first."
            )
        workspace_root = workspace_root_from_config(config)
        outcome = freeze_hypothesis_portfolio(
            request,
            state.validated_response,
            state.evidence_register,
            runs_root=workspace_root / "hypothesis" / "runs",
            ranking_payload=state.validated_ranking,
            path_root=workspace_root,
        )
        outcome["bound_request_sha256"] = state.request_sha256
        outcome["response_submissions"] = state.preflight_attempts
        outcome["contract_repairs"] = max(0, state.preflight_attempts - 1)
        outcome["ranking_submissions"] = state.ranking_attempts
        outcome["ranking_repairs"] = max(0, state.ranking_attempts - 1)
        return _ok(outcome)
    except Exception as exc:
        return _needs_revision(exc)


SCIENTIFIC_HYPOTHESIS_TOOLS = [
    scientific_hypothesis_bind_request,
    scientific_hypothesis_inspect_upstream,
    scientific_hypothesis_bind_evidence,
    scientific_hypothesis_validate_response,
    scientific_hypothesis_rank,
    scientific_hypothesis_freeze,
]

__all__ = ["SCIENTIFIC_HYPOTHESIS_TOOLS"] + [
    t.name for t in SCIENTIFIC_HYPOTHESIS_TOOLS
]
