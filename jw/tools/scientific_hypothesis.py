"""LangChain tools for evidence-grounded scientific-hypothesis checkpoints.

The conversational agent may explore, critique, and revise hypotheses without
creating a frozen artifact.  These tools provide the stricter boundary used
when it needs to bind evidence, checkpoint a complete portfolio, or explicitly
publish that checkpoint.

Contract state is task-scoped. Rebinding the same request is idempotent, and a
failed checkpoint never destroys the last valid checkpoint.
"""

from __future__ import annotations

import json
import os
import sys
from copy import deepcopy
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

from jw.tools.registry import register_tool_bundle  # noqa: E402
from jw.workspaces import (  # noqa: E402
    resolve_scoped_path,
    workspace_context_key,
    workspace_root_from_config,
)
from scientific_hypothesis.contracts import (  # noqa: E402
    HARD_NUMERIC_CUTOFF,
    RESPONSE_VERSION,
    SAFE_ID,
    canonical_json_sha256,
    validate_hypothesis_request,
)
from scientific_hypothesis.harness import (  # noqa: E402
    EvidenceRegister,
    build_hypothesis_brief,
    build_natural_hypothesis_request,
    build_wiki_evidence_excerpt,
    freeze_hypothesis_portfolio,
    preflight_hypothesis_response,
    validate_evidence_provenance,
)

MAX_EVIDENCE_BINDS = 20
MAX_SAME_CHECKPOINT_FAILURES = 2
WORKING_STATE_VERSION = 1
WORKING_STATE_RELATIVE_PATH = Path("work") / "scientific_hypothesis_state.json"
DRAFT_OPERATIONS = {
    "replace",
    "upsert_candidate",
    "patch_candidate",
    "remove_candidate",
    "set_distinctions",
    "set_portfolio_notes",
}
REQUIRED_CANDIDATE_FIELDS = {
    "id",
    "statement",
    "applicability",
    "mechanism",
    "assumptions",
    "predictions",
    "supporting_evidence",
    "opposing_evidence",
    "evidence_gaps",
    "alternative_explanations",
    "confounders",
    "falsification_conditions",
    "next_test",
    "confidence",
    "evidence_update",
    "prior_version_id",
}
WIKI_GROUNDING_TYPES = {
    "concept",
    "mechanism",
    "data_source",
    "experiment_paradigm",
    "hypothesis_template",
}


@dataclass(slots=True)
class _HypothesisState:
    request: dict[str, Any] | None = None
    request_sha256: str = ""
    evidence_register: EvidenceRegister = field(default_factory=EvidenceRegister)
    validated_response: dict[str, Any] | None = None
    preflight_response_sha256: str | None = None
    checkpoint_evidence_sha256: str | None = None
    preflight_attempts: int = 0
    latest_draft: dict[str, Any] | None = None
    latest_draft_sha256: str | None = None
    last_validation_error: str | None = None
    same_validation_error_count: int = 0
    persistence_warning: str | None = None


_STATES: dict[str, _HypothesisState] = {}
_STATE_LOCK = RLock()


def _ok(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, default=str)


def _working_state_path(config: RunnableConfig | None) -> Path | None:
    """Return the task-local state path when a workspace binding is available."""

    try:
        return workspace_root_from_config(config) / WORKING_STATE_RELATIVE_PATH
    except RuntimeError:
        # Direct unit calls may carry a synthetic thread id without creating a
        # task binding. They retain the historical in-memory behavior.
        return None


def _working_state_payload(state: _HypothesisState) -> dict[str, Any]:
    return {
        "schema_version": WORKING_STATE_VERSION,
        "request": state.request,
        "request_sha256": state.request_sha256,
        "evidence_register": state.evidence_register.all(),
        "checkpoint": state.validated_response,
        "checkpoint_sha256": state.preflight_response_sha256,
        "checkpoint_evidence_sha256": state.checkpoint_evidence_sha256,
        "checkpoint_attempts": state.preflight_attempts,
        "latest_draft": state.latest_draft,
        "latest_draft_sha256": state.latest_draft_sha256,
        "last_validation_error": state.last_validation_error,
        "same_validation_error_count": state.same_validation_error_count,
    }


def _persist_state(
    config: RunnableConfig | None, state: _HypothesisState
) -> Path | None:
    path = _working_state_path(config)
    if path is None:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temp.write_text(
            json.dumps(
                _working_state_payload(state),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temp, path)
        state.persistence_warning = None
        return path
    except OSError as exc:
        state.persistence_warning = f"working state could not be persisted: {exc}"
        return None


def _evidence_sha256(register: EvidenceRegister) -> str:
    return canonical_json_sha256({"evidence_register": register.all()})


def _draft_skeleton(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": RESPONSE_VERSION,
        "task_name": request["task_name"],
        "research_question": request["research_question"],
        "response_kind": "hypotheses_ready",
        "candidates": [],
        "pairwise_distinctions": [],
        "portfolio_notes": None,
    }


def _normalize_working_draft(
    payload: object, request: dict[str, Any]
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("working draft must be a JSON object")
    draft = deepcopy(payload)
    allowed = {
        "schema_version",
        "task_name",
        "research_question",
        "response_kind",
        "candidates",
        "pairwise_distinctions",
        "portfolio_notes",
    }
    unknown = sorted(set(draft) - allowed)
    if unknown:
        raise ValueError(f"working draft contains unsupported fields: {unknown}")
    expected = _draft_skeleton(request)
    for key in ("schema_version", "task_name", "research_question", "response_kind"):
        if key in draft and draft[key] != expected[key]:
            raise ValueError(f"working draft {key} does not match the bound request")
        draft[key] = expected[key]
    draft.setdefault("candidates", [])
    draft.setdefault("pairwise_distinctions", [])
    draft.setdefault("portfolio_notes", None)
    if not isinstance(draft["candidates"], list):
        raise ValueError("working draft candidates must be an array")
    if not isinstance(draft["pairwise_distinctions"], list):
        raise ValueError("working draft pairwise_distinctions must be an array")
    if draft["portfolio_notes"] is not None and not isinstance(
        draft["portfolio_notes"], str
    ):
        raise ValueError("working draft portfolio_notes must be a string or null")
    candidate_ids: list[str] = []
    for index, candidate in enumerate(draft["candidates"]):
        if not isinstance(candidate, dict):
            raise ValueError(f"working draft candidate {index} must be an object")
        candidate_id = candidate.get("id")
        if not isinstance(candidate_id, str) or SAFE_ID.fullmatch(candidate_id) is None:
            raise ValueError(f"working draft candidate {index} has an invalid id")
        candidate_ids.append(candidate_id)
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("working draft candidate ids must be unique")
    return draft


def _merge_draft_changes(target: dict[str, Any], changes: dict[str, Any]) -> None:
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge_draft_changes(target[key], value)
        else:
            target[key] = deepcopy(value)


def _draft_warnings(
    state: _HypothesisState, request: dict[str, Any]
) -> list[dict[str, Any]]:
    draft = state.latest_draft
    if not isinstance(draft, dict):
        return [
            {
                "code": "no_draft",
                "candidate_id": None,
                "message": "No working hypothesis draft exists yet.",
            }
        ]
    candidates = draft.get("candidates")
    if not isinstance(candidates, list):
        return [
            {
                "code": "invalid_candidates",
                "candidate_id": None,
                "message": "The working draft candidates value is not an array.",
            }
        ]

    warnings: list[dict[str, Any]] = []

    def add(code: str, message: str, candidate_id: str | None = None) -> None:
        if len(warnings) < 50:
            warnings.append(
                {
                    "code": code,
                    "candidate_id": candidate_id,
                    "message": message,
                }
            )

    for entry in state.evidence_register.all():
        try:
            validate_evidence_provenance(request, entry)
        except Exception as exc:
            add(
                "invalid_evidence_provenance",
                f"Evidence {entry['evidence_id']} has invalid provenance: {exc}",
            )

    if len(candidates) > request["max_candidates"]:
        add(
            "candidate_budget_exceeded",
            f"Draft has {len(candidates)} candidates; maximum is {request['max_candidates']}.",
        )

    statements: dict[str, str] = {}
    mechanisms: dict[str, str] = {}
    candidate_ids: set[str] = set()
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            add("invalid_candidate", f"Candidate {index} is not an object.")
            continue
        candidate_id = candidate.get("id")
        if not isinstance(candidate_id, str):
            add("invalid_candidate_id", f"Candidate {index} has no valid id.")
            continue
        candidate_ids.add(candidate_id)
        missing = sorted(REQUIRED_CANDIDATE_FIELDS - set(candidate))
        if missing:
            add(
                "candidate_incomplete",
                f"Candidate is missing fields: {', '.join(missing)}.",
                candidate_id,
            )

        statement = candidate.get("statement")
        if isinstance(statement, str) and statement.strip():
            normalized = " ".join(statement.split())
            other = statements.get(normalized)
            if other is not None:
                add(
                    "duplicate_statement",
                    f"Statement duplicates candidate {other}.",
                    candidate_id,
                )
            else:
                statements[normalized] = candidate_id

        mechanism = candidate.get("mechanism")
        summary = mechanism.get("summary") if isinstance(mechanism, dict) else None
        if isinstance(summary, str) and summary.strip():
            normalized = " ".join(summary.split())
            other = mechanisms.get(normalized)
            if other is not None:
                add(
                    "duplicate_mechanism",
                    f"Mechanism duplicates candidate {other}.",
                    candidate_id,
                )
            else:
                mechanisms[normalized] = candidate_id

        supporting = candidate.get("supporting_evidence", [])
        opposing = candidate.get("opposing_evidence", [])
        gaps = candidate.get("evidence_gaps", [])
        for evidence_kind, links, allowed_roles in (
            ("supporting", supporting, {"supports", "limits"}),
            ("opposing", opposing, {"opposes"}),
        ):
            if not isinstance(links, list):
                add(
                    "invalid_evidence_links",
                    f"{evidence_kind} evidence must be an array.",
                    candidate_id,
                )
                continue
            for link in links:
                evidence_id = (
                    link.get("evidence_id") if isinstance(link, dict) else None
                )
                entry = (
                    state.evidence_register.get(evidence_id)
                    if isinstance(evidence_id, str)
                    else None
                )
                if entry is None:
                    add(
                        "unbound_evidence",
                        f"{evidence_kind} evidence references an unbound id: "
                        f"{evidence_id!r}.",
                        candidate_id,
                    )
                elif (
                    not entry["verified_support"] or entry["role"] not in allowed_roles
                ):
                    add(
                        "evidence_role_mismatch",
                        f"{evidence_kind} evidence {evidence_id} is not verified "
                        "for that role.",
                        candidate_id,
                    )
        if not supporting and not gaps:
            add(
                "evidence_gap_missing",
                "Candidate has neither supporting evidence nor an explicit evidence gap.",
                candidate_id,
            )
        confidence = candidate.get("confidence")
        if (
            isinstance(confidence, dict)
            and confidence.get("level") == "high"
            and (not supporting or bool(opposing))
        ):
            add(
                "high_confidence_unsupported",
                "High confidence is inconsistent with missing support or opposing evidence.",
                candidate_id,
            )

        linked_evidence_ids = {
            link.get("evidence_id")
            for links in (supporting, opposing)
            if isinstance(links, list)
            for link in links
            if isinstance(link, dict) and isinstance(link.get("evidence_id"), str)
        }
        grounded_parts = [request["research_question"]]
        for evidence_id in linked_evidence_ids:
            entry = state.evidence_register.get(evidence_id)
            if (
                entry is not None
                and entry["verified_support"]
                and not entry["material_id"].startswith("kb_")
            ):
                grounded_parts.append(entry["excerpt"])
        normalized_grounding = {
            "".join(match.group(0).split()).lower()
            for part in grounded_parts
            for match in HARD_NUMERIC_CUTOFF.finditer(part)
        }
        threshold_texts: list[tuple[str, str]] = []
        for field_name in ("statement", "applicability"):
            value = candidate.get(field_name)
            if isinstance(value, str):
                threshold_texts.append((field_name, value))
        mechanism = candidate.get("mechanism")
        if isinstance(mechanism, dict):
            for value in mechanism.get("required_premises", []):
                if isinstance(value, str):
                    threshold_texts.append(("mechanism.required_premises", value))
        for field_name in ("assumptions", "falsification_conditions"):
            values = candidate.get(field_name, [])
            if isinstance(values, list):
                threshold_texts.extend(
                    (field_name, value) for value in values if isinstance(value, str)
                )
        predictions = candidate.get("predictions", [])
        if isinstance(predictions, list):
            for prediction in predictions:
                if not isinstance(prediction, dict):
                    continue
                prediction_id = str(prediction.get("id") or "unknown")
                for field_name in ("statement", "observable", "would_weaken_if"):
                    value = prediction.get(field_name)
                    if isinstance(value, str):
                        threshold_texts.append(
                            (f"prediction.{prediction_id}.{field_name}", value)
                        )
        next_test = candidate.get("next_test")
        if isinstance(next_test, dict):
            expected = next_test.get("expected_signals", [])
            if isinstance(expected, list):
                threshold_texts.extend(
                    ("next_test.expected_signals", value)
                    for value in expected
                    if isinstance(value, str)
                )
        for field_name, value in threshold_texts:
            for match in HARD_NUMERIC_CUTOFF.finditer(value):
                token = "".join(match.group(0).split()).lower()
                if token in normalized_grounding:
                    continue
                add(
                    "ungrounded_numeric_threshold",
                    (
                        f"{field_name} contains an ungrounded numeric threshold "
                        f"{match.group(0)!r}; remove it, make it qualitative, or "
                        "link verified non-Wiki evidence containing the same threshold."
                    ),
                    candidate_id,
                )

    distinctions = draft.get("pairwise_distinctions", [])
    if len(candidate_ids) > 1:
        covered: set[str] = set()
        if isinstance(distinctions, list):
            for row in distinctions:
                if isinstance(row, dict):
                    for key in ("left_id", "right_id"):
                        value = row.get(key)
                        if isinstance(value, str) and value in candidate_ids:
                            covered.add(value)
        for uncovered in sorted(candidate_ids - covered):
            add(
                "candidate_not_distinguished",
                "Candidate is not covered by any pairwise distinction.",
                uncovered,
            )
    return warnings


def _draft_summary(
    state: _HypothesisState,
    request: dict[str, Any],
    config: RunnableConfig | None,
) -> dict[str, Any]:
    draft = state.latest_draft if isinstance(state.latest_draft, dict) else {}
    candidates = draft.get("candidates", [])
    candidate_count = len(candidates) if isinstance(candidates, list) else 0
    warnings = _draft_warnings(state, request)
    return {
        "schema_version": "scientific-hypothesis-draft-status-v1",
        "status": "draft",
        "candidate_count": candidate_count,
        "draft_sha256": state.latest_draft_sha256,
        "checkpoint_available": state.validated_response is not None,
        "draft_differs_from_checkpoint": bool(
            state.latest_draft_sha256
            and state.preflight_response_sha256
            and state.latest_draft_sha256 != state.preflight_response_sha256
        ),
        "soft_warning_count": len(warnings),
        "soft_warnings": warnings,
        "hard_validation_run": False,
        "state_persistence": (
            "workspace" if _working_state_path(config) is not None else "memory_only"
        ),
        "persistence_warning": state.persistence_warning,
    }


def _load_persisted_state(path: Path) -> _HypothesisState:
    state = _HypothesisState()
    if not path.is_file():
        return state
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("working state must be a JSON object")
        if raw.get("schema_version") != WORKING_STATE_VERSION:
            raise ValueError("unsupported scientific-hypothesis working-state version")
        request = validate_hypothesis_request(raw.get("request"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        state.persistence_warning = f"persisted working state was ignored: {exc}"
        return state

    state.request = request
    state.request_sha256 = canonical_json_sha256(request)
    warnings: list[str] = []
    stored_request_sha = raw.get("request_sha256")
    if stored_request_sha != state.request_sha256:
        warnings.append("stored request hash was repaired")

    evidence_rows = raw.get("evidence_register", [])
    if isinstance(evidence_rows, list):
        for index, row in enumerate(evidence_rows):
            try:
                state.evidence_register.bind(row)
            except Exception as exc:
                warnings.append(f"evidence entry {index} was skipped: {exc}")
    else:
        warnings.append("invalid evidence register was ignored")

    attempts = raw.get("checkpoint_attempts", 0)
    if isinstance(attempts, int) and not isinstance(attempts, bool) and attempts >= 0:
        state.preflight_attempts = attempts

    draft = raw.get("latest_draft")
    if isinstance(draft, dict):
        state.latest_draft = draft
        state.latest_draft_sha256 = canonical_json_sha256(draft)

    error = raw.get("last_validation_error")
    if isinstance(error, str) and error:
        state.last_validation_error = error
    error_count = raw.get("same_validation_error_count", 0)
    if (
        isinstance(error_count, int)
        and not isinstance(error_count, bool)
        and error_count >= 0
    ):
        state.same_validation_error_count = error_count

    checkpoint = raw.get("checkpoint")
    if isinstance(checkpoint, dict):
        try:
            result = preflight_hypothesis_response(
                request,
                checkpoint,
                state.evidence_register,
                include_validated_response=True,
            )
            checked = result.pop("_validated_response", None)
            if result.get("status") != "hypotheses_ready" or not isinstance(
                checked, dict
            ):
                raise ValueError("persisted checkpoint is not hypotheses_ready")
            checkpoint_sha = canonical_json_sha256(checked)
            if raw.get("checkpoint_sha256") != checkpoint_sha:
                warnings.append("stored checkpoint hash was repaired")
            state.validated_response = checked
            state.preflight_response_sha256 = checkpoint_sha
            evidence_sha = _evidence_sha256(state.evidence_register)
            if raw.get("checkpoint_evidence_sha256") != evidence_sha:
                warnings.append("stored checkpoint evidence hash was repaired")
            state.checkpoint_evidence_sha256 = evidence_sha
        except Exception as exc:
            warnings.append(f"invalid checkpoint was ignored: {exc}")

    if warnings:
        state.persistence_warning = "; ".join(warnings)
    return state


def _needs_revision(
    exc: Exception,
    *,
    state: _HypothesisState | None = None,
    count_failure: bool = False,
) -> str:
    error = str(exc)
    if state is not None and count_failure:
        if state.last_validation_error == error:
            state.same_validation_error_count += 1
        else:
            state.last_validation_error = error
            state.same_validation_error_count = 1

    repeated = bool(
        state is not None
        and state.same_validation_error_count >= MAX_SAME_CHECKPOINT_FAILURES
    )
    checkpoint_available = bool(
        state is not None
        and state.validated_response is not None
        and state.preflight_response_sha256 is not None
    )
    return json.dumps(
        {
            "schema_version": "scientific-hypothesis-outcome-v1",
            "status": "review_limit_reached" if repeated else "needs_revision",
            "working_status": "draft",
            "validation_error": error,
            "same_validation_error_count": (
                state.same_validation_error_count if state is not None else 0
            ),
            "checkpoint_preserved": checkpoint_available,
            "persistence_warning": (
                state.persistence_warning if state is not None else None
            ),
            "retry_recommended": not repeated,
            "user_message": (
                "同一检查问题已重复出现。停止自动重试，保留当前草稿并向用户说明"
                "未解决项。只有获得新证据或明确修改方案后再检查。"
                if repeated
                else "这是可继续修改的草稿。保留已正确内容，只修正列出的问题，"
                "自动修复最多再尝试一次。"
            ),
        },
        ensure_ascii=False,
        default=str,
    )


def _state(config: RunnableConfig | None) -> _HypothesisState:
    context = workspace_context_key(config)
    with _STATE_LOCK:
        existing = _STATES.get(context)
        if existing is not None:
            return existing
        path = _working_state_path(config)
        state = _load_persisted_state(path) if path is not None else _HypothesisState()
        _STATES[context] = state
        return state


def _require_active_request(state: _HypothesisState) -> dict[str, Any]:
    if state.request is None:
        raise RuntimeError(
            "No hypothesis request is bound. Call scientific_hypothesis_bind_request first."
        )
    return state.request


@tool(parse_docstring=True)
def scientific_hypothesis_bind_request(
    request_input: str, config: RunnableConfig = None
) -> str:
    """Bind a natural-language research question and return the hypothesis brief.

    This starts an optional evidence/checkpoint session. If
    ``request_input`` starts with ``@``, the remainder is treated as a path
    (relative to the project root) to a JSON request file. Otherwise the input
    is used verbatim as the research question. Rebinding the same request
    preserves evidence, drafts, and checkpoints. Binding a different request
    starts a new task-scoped working state.

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
            path = resolve_scoped_path(supplied[1:].strip(), config, allow_project=True)
            payload = json.loads(path.read_text(encoding="utf-8"))
            request = validate_hypothesis_request(payload)
        else:
            request = build_natural_hypothesis_request(supplied)

        brief = build_hypothesis_brief(request)
        context = workspace_context_key(config)
        with _STATE_LOCK:
            existing = _STATES.get(context)
            if (
                existing is not None
                and existing.request_sha256 == brief["request_sha256"]
            ):
                brief["binding_status"] = "already_bound"
                brief["working_state_preserved"] = True
                brief["bound_evidence_count"] = len(existing.evidence_register)
                brief["checkpoint_available"] = existing.validated_response is not None
                active_state = existing
            else:
                active_state = _HypothesisState(
                    request=request,
                    request_sha256=brief["request_sha256"],
                )
                _STATES[context] = active_state
                brief["binding_status"] = "bound"
                brief["working_state_preserved"] = False
                brief["bound_evidence_count"] = 0
                brief["checkpoint_available"] = False
        state_path = _persist_state(config, active_state)
        brief["state_persistence"] = (
            "workspace" if state_path is not None else "memory_only"
        )
        brief["persistence_warning"] = active_state.persistence_warning
        return _ok(brief)
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
        request = _require_active_request(state)
        if len(state.evidence_register) >= MAX_EVIDENCE_BINDS:
            return _ok(
                {
                    "schema_version": "scientific-hypothesis-evidence-bound-v1",
                    "status": "budget_reached",
                    "message": "本次任务的证据绑定已达上限。请只使用已登记证据，把其余内容列为证据缺口。",
                }
            )
        if material_id.startswith("kb_"):
            raise ValueError(
                "Wiki 条目不能通过通用证据入口绑定。"
                "请使用 scientific_hypothesis_bind_wiki_evidence，"
                "由服务端核对 canonical 状态和读取回执"
            )
        if material_id.startswith("litbundle_"):
            raise ValueError(
                "任务文献包不能通过通用证据入口绑定。"
                "请使用 scientific_hypothesis_bind_literature_evidence，"
                "由服务端核对冻结快照和逐字引文"
            )
        row = {
            "evidence_id": evidence_id,
            "evidence_kind": evidence_kind,
            "material_id": material_id,
            "excerpt": excerpt,
            "verified_support": verified_support,
            "role": role,
        }
        validate_evidence_provenance(request, row)
        result = state.evidence_register.bind(row)
        state_path = _persist_state(config, state)
        result["state_persistence"] = (
            "workspace" if state_path is not None else "memory_only"
        )
        result["persistence_warning"] = state.persistence_warning
        return _ok(result)
    except Exception as exc:
        return _needs_revision(exc)


@tool(parse_docstring=True)
def scientific_hypothesis_bind_wiki_evidence(
    entry_id: str,
    config: RunnableConfig = None,
) -> str:
    """Bind one canonical Wiki entry as mechanism, scope, data, or method grounding.

    The tool re-reads the knowledge store on the server side, rejects
    candidate/deprecated/blocked entries and persists a bounded receipt with
    the exact version, confidence, valid range and provenance used by this
    hypothesis run. Wiki grounding is always registered as ``role=limits``;
    it is never observational support.

    Args:
        entry_id: Canonical Wiki entry id returned by ``kb_query``/``kb_read``.

    Returns:
        JSON string with the bound evidence id and canonical Wiki receipt.
    """
    try:
        from jw.tools.knowledge_base import _get_store, _run_context
        from knowledge_base import service as knowledge_service

        state = _state(config)
        request = _require_active_request(state)
        if len(state.evidence_register) >= MAX_EVIDENCE_BINDS:
            return _ok(
                {
                    "schema_version": "scientific-hypothesis-evidence-bound-v1",
                    "status": "budget_reached",
                    "message": "本次任务的证据绑定已达上限。请只使用已登记证据，把其余内容列为证据缺口。",
                }
            )
        agent, run_id = _run_context(config)
        if not run_id:
            raise ValueError(
                "Wiki binding requires a task-scoped run id so the prior kb_read "
                "receipt can be verified"
            )
        store = _get_store()
        prior_reads = store.provenance_for_run(run_id)
        read_receipt = next(
            (
                row
                for row in reversed(prior_reads)
                if row.get("entry_id") == entry_id
                and row.get("purpose") != "hypothesis_grounding"
            ),
            None,
        )
        if read_receipt is None:
            raise ValueError(
                f"No prior kb_read receipt exists for Wiki entry {entry_id} in "
                f"the current task run {run_id}. Call kb_read for this exact entry "
                "before binding it."
            )
        entry = knowledge_service.read(
            store,
            entry_id,
            agent=agent or "solar-hypothesis",
            run_id=run_id,
            purpose="hypothesis_grounding",
        )["entry"]
        if entry["status"] != "canonical":
            raise ValueError(
                f"Wiki 条目 {entry_id} 当前状态为 {entry['status']}。"
                "只有 canonical 条目可以进入假设状态"
            )
        if entry["type"] not in WIKI_GROUNDING_TYPES:
            raise ValueError(
                f"Wiki 条目 {entry_id} 的 type={entry['type']} 不能作为假设依据。"
                "仅允许稳定内置类型 concept、mechanism、data_source、"
                "experiment_paradigm、hypothesis_template"
            )
        receipt = build_wiki_evidence_excerpt(
            entry,
            read_receipt=read_receipt,
        )
        row = {
            "evidence_id": entry_id,
            "evidence_kind": "literature",
            "material_id": entry_id,
            "excerpt": receipt,
            "verified_support": True,
            "role": "limits",
        }
        validate_evidence_provenance(request, row)
        result = state.evidence_register.bind(row)
        state_path = _persist_state(config, state)
        result.update(
            {
                "wiki_grounding": {
                    "entry_id": entry_id,
                    "type": entry["type"],
                    "status": entry["status"],
                    "version": entry["version"],
                    "confidence": entry["confidence"],
                    "valid_range": entry.get("valid_range", ""),
                    "source_type": entry["source_type"],
                    "source_ref": entry["source_ref"],
                },
                "kb_read_receipt": {
                    "log_id": read_receipt.get("id"),
                    "run_id": read_receipt.get("run_id"),
                    "agent": read_receipt.get("agent"),
                    "purpose": read_receipt.get("purpose"),
                    "ts": read_receipt.get("ts"),
                },
                "state_persistence": (
                    "workspace" if state_path is not None else "memory_only"
                ),
                "persistence_warning": state.persistence_warning,
            }
        )
        return _ok(result)
    except Exception as exc:
        return _needs_revision(exc)


@tool(parse_docstring=True)
def scientific_hypothesis_bind_literature_evidence(
    bundle_id: str,
    source_id: str,
    role: str,
    quote: str,
    claim: str,
    config: RunnableConfig = None,
) -> str:
    """Bind one verified quote from a frozen task literature bundle.

    Unlike reusable Wiki grounding, task literature may support, oppose, or
    limit a candidate. The service checks the active research question, bundle
    membership, retraction flag, source fingerprint, and verbatim quote before
    adding evidence.

    Args:
        bundle_id: Frozen bundle id returned by lit_bundle_build.
        source_id: Source id contained in that exact bundle.
        role: supports, opposes, or limits.
        quote: Verbatim abstract quote of at most 40 words.
        claim: Bounded description of the candidate claim the quote bears on.

    Returns:
        JSON string with the bound evidence id and immutable source receipt.
    """
    try:
        from jw.tools.knowledge_base import _get_store, _run_context
        from knowledge_base.contracts import QUOTE_MAX_WORDS, quote_is_grounded

        state = _state(config)
        request = _require_active_request(state)
        if len(state.evidence_register) >= MAX_EVIDENCE_BINDS:
            return _ok(
                {
                    "schema_version": "scientific-hypothesis-evidence-bound-v1",
                    "status": "budget_reached",
                    "message": "本次任务的证据绑定已达上限。请把其余内容列为证据缺口。",
                }
            )
        normalized_role = str(role or "").strip().lower()
        if normalized_role not in {"supports", "opposes", "limits"}:
            raise ValueError("role 必须是 supports、opposes 或 limits")
        normalized_quote = " ".join(str(quote or "").split())
        if not normalized_quote or len(normalized_quote.split()) > QUOTE_MAX_WORDS:
            raise ValueError(f"quote 必须是缓存摘要中 1-{QUOTE_MAX_WORDS} 词的逐字引文")
        normalized_claim = " ".join(str(claim or "").split())
        if not normalized_claim or len(normalized_claim) > 500:
            raise ValueError("claim 必须是 1-500 字符的候选主张说明")
        store = _get_store()
        bundle = store.get_lit_task_bundle(str(bundle_id or "").strip())
        if bundle is None:
            raise ValueError(f"任务文献包不存在：{bundle_id}")
        active_question = " ".join(str(request["research_question"]).split())
        bundle_question = " ".join(str(bundle["research_question"]).split())
        if active_question != bundle_question:
            raise ValueError(
                "任务文献包绑定的研究问题与当前假设请求不一致；"
                "请用当前问题重新调用 lit_bundle_build"
            )
        _, run_id = _run_context(config)
        bundle_run_id = str(bundle.get("run_id") or "")
        if bundle_run_id and run_id and bundle_run_id != run_id:
            raise ValueError("任务文献包属于另一个运行，不能跨任务绑定")
        snapshot = next(
            (
                item
                for item in bundle["source_snapshots"]
                if str(item.get("source_id") or "") == str(source_id or "").strip()
            ),
            None,
        )
        if snapshot is None:
            raise ValueError(f"来源 {source_id} 不在任务文献包 {bundle_id} 中")
        if bool(snapshot.get("is_retracted")):
            raise ValueError("撤稿来源不能绑定为假设证据")
        if not quote_is_grounded(normalized_quote, str(snapshot.get("abstract") or "")):
            raise ValueError("quote 无法在冻结摘要快照中逐字定位")
        receipt_payload = {
            "status": "verified",
            "bundle_id": bundle["bundle_id"],
            "source_id": snapshot["source_id"],
            "family_id": snapshot.get("family_id", ""),
            "title": snapshot.get("title", ""),
            "doi": snapshot.get("doi", ""),
            "source_version": snapshot.get("source_version", ""),
            "content_fingerprint": snapshot.get("content_fingerprint", ""),
            "role": normalized_role,
            "quote": normalized_quote,
            "claim": normalized_claim,
        }
        receipt = json.dumps(
            receipt_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        evidence_id = "litevidence_" + canonical_json_sha256(receipt_payload)[:32]
        row = {
            "evidence_id": evidence_id,
            "evidence_kind": "literature",
            "material_id": bundle["bundle_id"],
            "excerpt": receipt,
            "verified_support": True,
            "role": normalized_role,
        }
        validate_evidence_provenance(request, row)
        result = state.evidence_register.bind(row)
        state_path = _persist_state(config, state)
        result.update(
            {
                "literature_evidence": receipt_payload,
                "state_persistence": (
                    "workspace" if state_path is not None else "memory_only"
                ),
                "persistence_warning": state.persistence_warning,
            }
        )
        return _ok(result)
    except Exception as exc:
        return _needs_revision(exc)


@tool(parse_docstring=True)
def scientific_hypothesis_update_draft(
    operation: str,
    payload_json: str,
    config: RunnableConfig = None,
) -> str:
    """Incrementally update the mutable hypothesis draft without hard validation.

    Supported operations are ``replace``, ``upsert_candidate``,
    ``patch_candidate``, ``remove_candidate``, ``set_distinctions``, and
    ``set_portfolio_notes``. Candidate patches recursively update only the
    supplied fields. Every successful change is persisted and returns soft
    evidence/completeness warnings; it does not create a checkpoint or publish.

    Args:
        operation: One supported draft operation.
        payload_json: JSON payload containing the draft, candidate, patch,
            candidate identifier, distinctions, or portfolio notes required by
            the selected operation.

    Returns:
        JSON string with the updated draft summary and non-blocking warnings.
    """
    state = _state(config)
    try:
        request = _require_active_request(state)
        if operation not in DRAFT_OPERATIONS:
            raise ValueError(
                f"operation must be one of: {', '.join(sorted(DRAFT_OPERATIONS))}"
            )
        payload = json.loads(payload_json)
        if operation == "replace":
            draft = _normalize_working_draft(payload, request)
        else:
            base = (
                state.latest_draft
                if isinstance(state.latest_draft, dict)
                else _draft_skeleton(request)
            )
            draft = _normalize_working_draft(base, request)
            candidates = draft["candidates"]

            if operation == "upsert_candidate":
                if not isinstance(payload, dict):
                    raise ValueError("upsert_candidate payload must be an object")
                candidate_id = payload.get("id")
                if (
                    not isinstance(candidate_id, str)
                    or SAFE_ID.fullmatch(candidate_id) is None
                ):
                    raise ValueError("upsert_candidate requires a valid candidate id")
                existing_index = next(
                    (
                        index
                        for index, candidate in enumerate(candidates)
                        if candidate.get("id") == candidate_id
                    ),
                    None,
                )
                if existing_index is None:
                    if len(candidates) >= request["max_candidates"]:
                        raise ValueError("candidate budget has been reached")
                    candidates.append(deepcopy(payload))
                else:
                    candidates[existing_index] = deepcopy(payload)

            elif operation == "patch_candidate":
                if not isinstance(payload, dict):
                    raise ValueError("patch_candidate payload must be an object")
                candidate_id = payload.get("candidate_id")
                changes = payload.get("changes")
                if not isinstance(candidate_id, str) or not isinstance(changes, dict):
                    raise ValueError(
                        "patch_candidate requires candidate_id and object changes"
                    )
                candidate = next(
                    (
                        candidate
                        for candidate in candidates
                        if candidate.get("id") == candidate_id
                    ),
                    None,
                )
                if candidate is None:
                    raise ValueError(f"candidate does not exist: {candidate_id}")
                changed_id = changes.get("id")
                if changed_id is not None and changed_id != candidate_id:
                    raise ValueError("patch_candidate cannot change the candidate id")
                _merge_draft_changes(candidate, changes)

            elif operation == "remove_candidate":
                if not isinstance(payload, dict):
                    raise ValueError("remove_candidate payload must be an object")
                candidate_id = payload.get("candidate_id")
                if not isinstance(candidate_id, str):
                    raise ValueError("remove_candidate requires candidate_id")
                remaining = [
                    candidate
                    for candidate in candidates
                    if candidate.get("id") != candidate_id
                ]
                if len(remaining) == len(candidates):
                    raise ValueError(f"candidate does not exist: {candidate_id}")
                draft["candidates"] = remaining
                draft["pairwise_distinctions"] = [
                    row
                    for row in draft["pairwise_distinctions"]
                    if not isinstance(row, dict)
                    or (
                        row.get("left_id") != candidate_id
                        and row.get("right_id") != candidate_id
                    )
                ]

            elif operation == "set_distinctions":
                if not isinstance(payload, list) or not all(
                    isinstance(row, dict) for row in payload
                ):
                    raise ValueError(
                        "set_distinctions payload must be an array of objects"
                    )
                draft["pairwise_distinctions"] = deepcopy(payload)

            elif operation == "set_portfolio_notes":
                if payload is not None and not isinstance(payload, str):
                    raise ValueError(
                        "set_portfolio_notes payload must be a string or null"
                    )
                draft["portfolio_notes"] = payload

        state.latest_draft = draft
        state.latest_draft_sha256 = canonical_json_sha256(draft)
        # A material edit is the escape condition for a previous repeated
        # validation failure, so a genuinely revised draft gets a fresh review.
        state.last_validation_error = None
        state.same_validation_error_count = 0
        _persist_state(config, state)
        result = _draft_summary(state, request, config)
        result["operation"] = operation
        result["retry_budget_reset"] = True
        return _ok(result)
    except Exception as exc:
        return _needs_revision(exc, state=state)


@tool(parse_docstring=True)
def scientific_hypothesis_get_draft(config: RunnableConfig = None) -> str:
    """Return the current mutable draft and its non-blocking review warnings.

    Use this after an interruption or before applying a targeted patch. This
    operation never validates, checkpoints, publishes, or modifies the draft.

    Args:
        config: Injected LangGraph runtime configuration; callers omit it.

    Returns:
        JSON string containing the current draft and soft review summary.
    """
    state = _state(config)
    try:
        request = _require_active_request(state)
        result = _draft_summary(state, request, config)
        result["draft"] = deepcopy(state.latest_draft)
        return _ok(result)
    except Exception as exc:
        return _needs_revision(exc, state=state)


def _checkpoint_response(
    state: _HypothesisState,
    response: dict[str, Any],
    config: RunnableConfig | None,
) -> str:
    try:
        request = _require_active_request(state)
        state.latest_draft = response
        state.latest_draft_sha256 = canonical_json_sha256(response)
        state.preflight_attempts += 1
        result = preflight_hypothesis_response(
            request,
            response,
            state.evidence_register,
            include_validated_response=True,
        )
        checked = result.pop("_validated_response", None)
        result["preflight_attempt"] = state.preflight_attempts
        checkpoint_created = False
        if result.get("status") == "hypotheses_ready" and isinstance(checked, dict):
            state.validated_response = checked
            state.preflight_response_sha256 = canonical_json_sha256(checked)
            state.checkpoint_evidence_sha256 = _evidence_sha256(state.evidence_register)
            state.latest_draft = checked
            state.latest_draft_sha256 = state.preflight_response_sha256
            checkpoint_created = True
        state.last_validation_error = None
        state.same_validation_error_count = 0
        result["working_status"] = (
            "checkpointed" if checkpoint_created else result["status"]
        )
        result["checkpoint_available"] = state.validated_response is not None
        result["publication_required"] = False
        state_path = _persist_state(config, state)
        result["state_persistence"] = (
            "workspace" if state_path is not None else "memory_only"
        )
        result["persistence_warning"] = state.persistence_warning
        return _ok(result)
    except Exception as exc:
        outcome = _needs_revision(exc, state=state, count_failure=True)
        _persist_state(config, state)
        return outcome


@tool(parse_docstring=True)
def scientific_hypothesis_validate_response(
    response_json: str, config: RunnableConfig = None
) -> str:
    """Replace the current draft and checkpoint one complete response.

    This compatibility path accepts a complete scientific-hypothesis response
    in one call. New multi-step work should use incremental draft updates and
    ``scientific_hypothesis_checkpoint_draft`` instead. A failed check remains
    a draft and does not erase the last valid checkpoint.

    Args:
        response_json: One JSON string containing a
            scientific-hypothesis-response-v1 object.

    Returns:
        JSON string with the checkpoint status and issue list.
    """
    state = _state(config)
    try:
        response = json.loads(response_json)
        if not isinstance(response, dict):
            raise ValueError("response must be a JSON object")
    except Exception as exc:
        outcome = _needs_revision(exc, state=state, count_failure=True)
        _persist_state(config, state)
        return outcome
    return _checkpoint_response(state, response, config)


@tool(parse_docstring=True)
def scientific_hypothesis_checkpoint_draft(config: RunnableConfig = None) -> str:
    """Hard-check the current mutable draft without requiring it to be resent.

    Use only when a structured handoff or formal publication is needed.
    Checkpoint failure preserves both the draft and the last valid checkpoint.

    Args:
        config: Injected LangGraph runtime configuration; callers omit it.

    Returns:
        JSON string with the checkpoint status and issue list.
    """
    state = _state(config)
    if not isinstance(state.latest_draft, dict):
        outcome = _needs_revision(
            ValueError("No working draft exists to checkpoint."),
            state=state,
            count_failure=True,
        )
        _persist_state(config, state)
        return outcome
    return _checkpoint_response(state, deepcopy(state.latest_draft), config)


@tool(parse_docstring=True)
def scientific_hypothesis_get_status(config: RunnableConfig = None) -> str:
    """Return the current draft/checkpoint status without modifying it.

    Use this after an interruption or validation failure to decide whether to
    continue editing, report a partial result, or explicitly publish a valid
    checkpoint.

    Args:
        config: Injected LangGraph runtime configuration; callers omit it.

    Returns:
        JSON string describing the current task-scoped working state.
    """
    state = _state(config)
    try:
        request = _require_active_request(state)
        draft_differs = bool(
            state.latest_draft_sha256
            and state.preflight_response_sha256
            and state.latest_draft_sha256 != state.preflight_response_sha256
        )
        evidence_differs = bool(
            state.checkpoint_evidence_sha256
            and state.checkpoint_evidence_sha256
            != _evidence_sha256(state.evidence_register)
        )
        draft_summary = _draft_summary(state, request, config)
        return _ok(
            {
                "schema_version": "scientific-hypothesis-working-status-v1",
                "status": "working",
                "research_question": request["research_question"],
                "request_sha256": state.request_sha256,
                "bound_evidence_count": len(state.evidence_register),
                "draft_available": state.latest_draft is not None,
                "candidate_count": draft_summary["candidate_count"],
                "soft_warning_count": draft_summary["soft_warning_count"],
                "soft_warnings": draft_summary["soft_warnings"],
                "checkpoint_available": state.validated_response is not None,
                "draft_differs_from_checkpoint": draft_differs,
                "evidence_differs_from_checkpoint": evidence_differs,
                "checkpoint_attempts": state.preflight_attempts,
                "same_validation_error_count": state.same_validation_error_count,
                "retry_recommended": (
                    state.same_validation_error_count < MAX_SAME_CHECKPOINT_FAILURES
                ),
                "state_persistence": (
                    "workspace"
                    if _working_state_path(config) is not None
                    else "memory_only"
                ),
                "persistence_warning": state.persistence_warning,
            }
        )
    except Exception as exc:
        return _needs_revision(exc, state=state)


@tool(parse_docstring=True)
def scientific_hypothesis_freeze(config: RunnableConfig = None) -> str:
    """Explicitly publish the most recently checkpointed hypotheses.

    This is a publication operation, not a required conversational step. It
    takes no parameters: the latest valid checkpoint is compiled into a
    portfolio.

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
                "Publish requires a successful scientific_hypothesis_checkpoint_draft "
                "or compatibility validation checkpoint first."
            )
        if (
            state.latest_draft_sha256 is not None
            and state.latest_draft_sha256 != state.preflight_response_sha256
        ):
            raise RuntimeError(
                "The current draft differs from the last valid checkpoint. "
                "Checkpoint the intended draft before publishing; the older "
                "checkpoint was preserved and has not been overwritten."
            )
        if (
            state.checkpoint_evidence_sha256 is not None
            and state.checkpoint_evidence_sha256
            != _evidence_sha256(state.evidence_register)
        ):
            raise RuntimeError(
                "The evidence register changed after the last valid checkpoint. "
                "Checkpoint the intended draft against the current evidence before "
                "publishing."
            )
        if (
            canonical_json_sha256(state.validated_response)
            != state.preflight_response_sha256
        ):
            raise RuntimeError(
                "Hypothesis response changed after validation; check the revised response first."
            )
        workspace_root = workspace_root_from_config(config)
        outcome = freeze_hypothesis_portfolio(
            request,
            state.validated_response,
            state.evidence_register,
            runs_root=workspace_root / "hypothesis" / "runs",
            path_root=workspace_root,
        )
        outcome["bound_request_sha256"] = state.request_sha256
        outcome["response_submissions"] = state.preflight_attempts
        outcome["contract_repairs"] = max(0, state.preflight_attempts - 1)
        outcome["publication_status"] = "published"
        return _ok(outcome)
    except Exception as exc:
        return _needs_revision(exc, state=state)


SCIENTIFIC_HYPOTHESIS_TOOLS = [
    scientific_hypothesis_bind_request,
    scientific_hypothesis_bind_evidence,
    scientific_hypothesis_bind_wiki_evidence,
    scientific_hypothesis_bind_literature_evidence,
    scientific_hypothesis_update_draft,
    scientific_hypothesis_get_draft,
    scientific_hypothesis_validate_response,
    scientific_hypothesis_checkpoint_draft,
    scientific_hypothesis_get_status,
    scientific_hypothesis_freeze,
]

register_tool_bundle(
    "scientific-hypothesis",
    SCIENTIFIC_HYPOTHESIS_TOOLS,
    include_in_main=False,
)

__all__ = ["SCIENTIFIC_HYPOTHESIS_TOOLS"] + [
    t.name for t in SCIENTIFIC_HYPOTHESIS_TOOLS
]
