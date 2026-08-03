"""Typed tools for the independent Evidence Reviewer and final release gate."""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from jw.research_review import (
    ALL_REVIEW_MODES,
    INDEPENDENT_REVIEW_TOOL_CONTRACT_VERSION,
    store_from_config,
)
from research_review.contracts import issue_fingerprint

from .registry import register_tool_bundle

_INDEPENDENT_REVIEW_SCHEMA = {
    "title": "IndependentReviewVerdict",
    "description": "A bounded pass/fail verdict from an independent model family.",
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["pass", "fail"]},
        "notes": {"type": "string"},
    },
    "required": ["decision", "notes"],
    "additionalProperties": False,
}


def _model_family(model: str) -> str:
    """Return a conservative lineage label for independence gating."""

    normalized = model.casefold().rsplit("/", 1)[-1]
    for prefix, family in (
        (("qwen", "qwq"), "qwen"),
        (("claude",), "claude"),
        (("gpt", "o1", "o3", "o4", "codex"), "openai"),
        (("gemini",), "gemini"),
        (("deepseek",), "deepseek"),
        (("llama",), "llama"),
        (("mistral", "mixtral"), "mistral"),
    ):
        if normalized.startswith(prefix):
            return family
    return re.split(r"[-_:]", normalized, maxsplit=1)[0]


def _independent_review_context(store: Any, review_mode: str) -> str:
    """Build a bounded evidence-first packet without producer-report duplication."""

    review_context = store.review_context(review_mode)
    source_refs: list[str] = []
    for artifact in review_context.get("artifacts", []):
        if not isinstance(artifact, dict):
            continue
        candidates = list(artifact.get("evidence_refs", []))
        for claim in artifact.get("claims", []):
            if not isinstance(claim, dict):
                continue
            candidates.extend(claim.get("supporting_evidence", []))
            candidates.extend(claim.get("opposing_evidence", []))
        for ref in candidates:
            if isinstance(ref, str) and ref not in source_refs:
                source_refs.append(ref)

    inspected_sources: list[dict[str, Any]] = []
    remaining = 60_000
    for ref in source_refs[:8]:
        if remaining <= 0:
            break
        try:
            source = store.review_source(review_mode, ref)
        except Exception as exc:
            source = {
                "source_ref": ref,
                "status": "unavailable",
                "error": f"{type(exc).__name__}: {exc}",
            }
        encoded = json.dumps(source, ensure_ascii=False)
        if len(encoded) > remaining:
            source = {
                "source_ref": ref,
                "status": "truncated_for_independent_review",
                "content_prefix": encoded[:remaining],
            }
            encoded = json.dumps(source, ensure_ascii=False)
        inspected_sources.append(source)
        remaining -= len(encoded)

    return json.dumps(
        {
            "schema_version": "independent-review-context-v1",
            "review_context": review_context,
            "inspected_declared_sources": inspected_sources,
        },
        ensure_ascii=False,
    )


def _json_arg(value: Any, label: str, expected: type) -> Any:
    if isinstance(value, expected):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label} must be valid JSON") from exc
        if isinstance(parsed, expected):
            return parsed
    raise ValueError(
        f"{label} must be {expected.__name__} or JSON-encoded {expected.__name__}"
    )


def _ok(payload: object) -> str:
    return json.dumps({"ok": True, "result": payload}, ensure_ascii=False)


def _error(exc: Exception) -> str:
    return json.dumps(
        {
            "ok": False,
            "status": "error",
            "error_type": type(exc).__name__,
            "message": str(exc),
        },
        ensure_ascii=False,
    )


def _normalize_issues(value: Any) -> list[dict[str, Any]]:
    rows = _json_arg(value, "issues", list)
    normalized: list[dict[str, Any]] = []
    identities: set[tuple[str, str, str]] = set()
    for index, item in enumerate(rows, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"issues[{index - 1}] must be an object")
        row = dict(item)
        row.setdefault("issue_id", f"issue-{index:03d}")
        for field in (
            "rule_id",
            "severity",
            "claim_ref",
            "owner",
            "message",
            "required_action",
            "acceptance_test",
        ):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError(f"issues[{index - 1}].{field} is required")
        row.setdefault("evidence_refs", [])
        identity = (row["rule_id"], row["claim_ref"], row["owner"])
        if identity in identities:
            raise ValueError(
                f"issues[{index - 1}] duplicates a rule_id/claim_ref/owner identity; "
                "use a stable field-level claim_ref such as "
                "artifact-id#plan_content.section.item.field for each distinct defect"
            )
        identities.add(identity)
        row["fingerprint"] = issue_fingerprint(
            row["rule_id"], row["claim_ref"], row["owner"]
        )
        normalized.append(row)
    return normalized


def _string_list(value: Any, label: str) -> list[str]:
    rows = _json_arg(value, label, list)
    if not all(isinstance(item, str) for item in rows):
        raise ValueError(f"{label} must contain strings")
    return rows


@tool(parse_docstring=True)
def evidence_review_open_context(
    review_mode: str,
    config: RunnableConfig = None,
) -> str:
    """Open the immutable artifact set for one typed review mode.

    Args:
        review_mode: One of planning, data, hypothesis, experiment_design,
            experiment_result, integration, or final_release.

    Returns:
        JSON with compact hash-bound artifact projections, declared source refs,
            prior verdicts, policy version, and budget. Detailed scientific content
            must be opened explicitly with evidence_review_read_source.
    """

    try:
        if review_mode not in ALL_REVIEW_MODES:
            raise ValueError(f"unsupported review_mode: {review_mode}")
        return _ok(store_from_config(config).review_context(review_mode))
    except Exception as exc:
        return _error(exc)


@tool(parse_docstring=True)
def evidence_review_read_source(
    review_mode: str,
    source_ref: str,
    config: RunnableConfig = None,
) -> str:
    """Read one source declared by the current immutable review artifact.

    Args:
        review_mode: The same typed mode passed to evidence_review_open_context.
        source_ref: An exact evidence, opposing-evidence, or upstream reference
            returned by the server-bound review context.

    Returns:
        A bounded read-only source record with SHA-256 and text content when the
        referenced file is UTF-8, or an immutable ResearchArtifactV2 record.
    """

    try:
        if review_mode not in ALL_REVIEW_MODES:
            raise ValueError(f"unsupported review_mode: {review_mode}")
        return _ok(store_from_config(config).review_source(review_mode, source_ref))
    except Exception as exc:
        return _error(exc)


@tool(parse_docstring=True)
def evidence_review_submit_verdict(
    review_mode: str,
    decision: str,
    issues: list[dict[str, Any]] | str,
    accepted_claims: list[str] | str = "[]",
    blocked_claims: list[str] | str = "[]",
    carry_forward_limits: list[str] | str = "[]",
    next_owner: str = "",
    independent_review_status: str = "not_required",
    independent_reviewer: str = "",
    independent_notes: str = "",
    config: RunnableConfig = None,
) -> str:
    """Persist a hash-bound review verdict without modifying producer artifacts.

    Args:
        review_mode: Typed stage being reviewed.
        decision: accept, accept_with_limits, revise, block, or human_review.
        issues: JSON issue list with rule_id, severity, claim_ref, owner,
            message, required_action, acceptance_test, and evidence_refs. Each
            distinct defect must use a unique, stable field-level claim_ref.
        accepted_claims: JSON list of accepted claim ids.
        blocked_claims: JSON list of blocked claim ids.
        carry_forward_limits: JSON list of limitations that final prose must retain.
        next_owner: Producer responsible for a revise decision.
        independent_review_status: not_required, not_configured,
            heterogeneous_pass, human_pass, or failed.
        independent_reviewer: Optional independent reviewer identifier.
        independent_notes: Bounded independent-review note.

    Returns:
        The persisted ReviewVerdictV2, including server-bound hashes and round.
    """

    try:
        verdict = store_from_config(config).submit_verdict(
            mode=review_mode,
            decision=decision,
            issues=_normalize_issues(issues),
            accepted_claims=_string_list(accepted_claims, "accepted_claims"),
            blocked_claims=_string_list(blocked_claims, "blocked_claims"),
            carry_forward_limits=_string_list(
                carry_forward_limits, "carry_forward_limits"
            ),
            next_owner=next_owner.strip() or None,
            independent_review={
                "status": independent_review_status,
                "reviewer": independent_reviewer.strip() or None,
                "notes": independent_notes,
            },
        )
        return _ok(verdict)
    except Exception as exc:
        return _error(exc)


@tool
def evidence_review_get_status(config: RunnableConfig = None) -> str:
    """Return the task's persisted ResearchRunStateV2 and deterministic next action."""

    try:
        store = store_from_config(config)
        return _ok({"state": store.load_state(), "next_action": store.next_action()})
    except Exception as exc:
        return _error(exc)


@tool(parse_docstring=True)
def research_release_prepare(
    draft_markdown: str,
    claim_citations: list[dict[str, Any]] | str,
    config: RunnableConfig = None,
) -> str:
    """Checkpoint a coherent final report draft after integration acceptance.

    Args:
        draft_markdown: Reader-facing report synthesized only from accepted claims.
        claim_citations: JSON list binding each material draft_excerpt verbatim
            to one accepted integration claim_id.

    Returns:
        A hash-bound final_release ResearchArtifactV2 awaiting Evidence review.
    """

    try:
        return _ok(
            store_from_config(config).prepare_release(
                draft_markdown,
                _json_arg(claim_citations, "claim_citations", list),
            )
        )
    except Exception as exc:
        return _error(exc)


@tool
def research_release_get_accepted(config: RunnableConfig = None) -> str:
    """Return the exact accepted final report; fail if the release gate is open."""

    try:
        report = store_from_config(config).accepted_release_markdown()
        if report is None:
            raise RuntimeError("no final report has an accepted hash-bound verdict")
        return _ok({"status": "accepted", "report_markdown": report})
    except Exception as exc:
        return _error(exc)


@tool(parse_docstring=True)
def research_independent_review(
    review_mode: str,
    config: RunnableConfig = None,
) -> str:
    """Run the configured heterogeneous model for a hash-bound second review.

    This tool is available to the Supervisor, not to the primary Evidence
    agent. It refuses the same model family (even when model variants differ)
    and leaves the run at human_review when no genuinely heterogeneous reviewer
    can be invoked.

    Args:
        review_mode: The integration or final_release mode requested by the
            deterministic state machine.

    Returns:
        A separately persisted independent-review receipt.
    """

    store = store_from_config(config)
    targets = store.review_targets(review_mode)
    refs = [store.artifact_ref(item) for item in targets]
    reviewer_id = "unspecified"
    reviewer_attempt_id = "unspecified"
    try:
        if review_mode not in {"hypothesis", "integration", "final_release"}:
            raise ValueError(
                "independent review is limited to hypothesis/integration/final_release"
            )
        from jw.config import get_effective_config
        from jw.llm import get_chat_model

        cfg = get_effective_config()
        aux_model = cfg.auxiliary_model or cfg.model
        aux_provider = cfg.auxiliary_provider or cfg.provider
        reviewer_id = f"{aux_provider}:{aux_model}"
        reviewer_attempt_id = (
            f"{reviewer_id}@{INDEPENDENT_REVIEW_TOOL_CONTRACT_VERSION}"
        )
        if (aux_model, aux_provider) == (cfg.model, cfg.provider) or _model_family(
            aux_model
        ) == _model_family(cfg.model):
            raise RuntimeError(
                "no genuinely heterogeneous auxiliary model family is configured"
            )
        model = get_chat_model(model=aux_model, provider=aux_provider)
        context = _independent_review_context(store, review_mode)
        from jw.middleware.utils import disable_thinking

        # DashScope thinking mode only accepts tool_choice=auto/none, while
        # LangChain structured output forces a specific schema tool.  Keep this
        # adjudication deterministic and compatible by using a non-thinking
        # copy for the schema-bound call.
        model = disable_thinking(model)
        response = model.with_structured_output(_INDEPENDENT_REVIEW_SCHEMA).invoke(
            [
                {
                    "role": "system",
                    "content": (
                        "You are an independent scientific meta-reviewer using a "
                        "a genuinely different model family from the primary reviewer. "
                        "Inspect the hash-bound projection and every included declared "
                        "source. Return pass only when its accepted "
                        "mechanism claims are evidence-bounded, reproducible, and do "
                        "not exceed the stated limitations. Otherwise return fail. "
                        "Do not edit the artifact and do not output chain-of-thought."
                    ),
                },
                {"role": "user", "content": context},
            ]
        )
        if not isinstance(response, dict):
            raise RuntimeError("heterogeneous reviewer returned no structured verdict")
        decision = response.get("decision")
        notes = response.get("notes")
        if decision not in {"pass", "fail"} or not isinstance(notes, str):
            raise RuntimeError("heterogeneous reviewer returned an invalid verdict")
        receipt = store.write_independent_review_receipt(
            review_mode,
            refs,
            reviewer_kind="heterogeneous_model",
            reviewer_id=reviewer_id,
            decision=decision,
            notes=notes,
        )
        return _ok(receipt)
    except Exception as exc:
        store.mark_independent_review_unavailable(
            review_mode,
            refs,
            f"{type(exc).__name__}: {exc}",
            reviewer_id=reviewer_attempt_id,
        )
        return _error(exc)


RESEARCH_REVIEW_TOOLS = [
    evidence_review_open_context,
    evidence_review_read_source,
    evidence_review_submit_verdict,
    evidence_review_get_status,
]
RESEARCH_RELEASE_TOOLS = [
    research_release_prepare,
    research_release_get_accepted,
    research_independent_review,
]

register_tool_bundle("research-review", RESEARCH_REVIEW_TOOLS)
register_tool_bundle("research-release", RESEARCH_RELEASE_TOOLS, include_in_main=True)

__all__ = [
    "RESEARCH_RELEASE_TOOLS",
    "RESEARCH_REVIEW_TOOLS",
] + [tool.name for tool in [*RESEARCH_REVIEW_TOOLS, *RESEARCH_RELEASE_TOOLS]]
