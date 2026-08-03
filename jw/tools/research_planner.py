"""LangChain tool wrappers for the Pi-style Research Planner Python bridge.

These tools expose the research-planner-agent skill to the JW agent.
They wrap deterministic contract validation, knowledge retrieval, and plan
freeze operations implemented in ``src/research_planner``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SRC = _PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from jsonschema import Draft202012Validator  # noqa: E402
from langchain_core.runnables import RunnableConfig  # noqa: E402
from langchain_core.tools import tool  # noqa: E402

from jw.tools.registry import register_tool_bundle  # noqa: E402
from jw.workspaces import (  # noqa: E402
    resolve_scoped_path,
    workspace_context_key,
    workspace_root_from_config,
)
from research_planner.contracts import (  # noqa: E402
    canonical_json_sha256,
    validate_planner_request,
)
from research_planner.harness import (  # noqa: E402
    build_natural_planner_request,
    build_planning_brief,
    freeze_research_plan,
    preflight_planner_response,
)
from research_planner.knowledge import (  # noqa: E402
    extract_source_evidence,
    inspect_dataset,
    resolve_reference,
    search_local_knowledge,
    search_scholarly_literature,
)

_REQUEST_CACHE: dict[tuple[str, str], dict[str, Any]] = {}
_ACTIVE_REQUEST_SHA256: dict[str, str] = {}
_VALIDATED_RESPONSES: dict[tuple[str, str], dict[str, Any]] = {}
_PLANNER_DRAFTS: dict[str, dict[str, Any]] = {}
_STATE_LOCK = RLock()

_WORKING_STATE_VERSION = "research-planner-working-state-v1"
_DRAFT_FAILURE_POLICY_VERSION = "planner-section-feedback-v2"
_WORKING_STATE_RELATIVE_PATH = Path("planner") / "working_state.json"
_DRAFT_ARCHIVE_RELATIVE_DIR = Path("planner") / "drafts"
_PLAN_SECTION_ORDER = (
    "scope",
    "research_subquestions",
    "research_state_map",
    "evidence_sources",
    "required_datasets",
    "research_artifacts",
    "research_route",
    "evaluation_rules",
    "report_outline",
    "iteration_policy",
    "stop_rules",
)
_RESPONSE_SCHEMA = json.loads(
    (
        _PROJECT_ROOT / "research/planner/specs/planner_response_v1.schema.json"
    ).read_text(encoding="utf-8")
)
_PLAN_SECTION_VALIDATORS = {
    section: Draft202012Validator(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": f"#/$defs/planContent/properties/{section}",
            "$defs": _RESPONSE_SCHEMA["$defs"],
        }
    )
    for section in _PLAN_SECTION_ORDER
}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _working_state_path(config: RunnableConfig | None) -> Path:
    return workspace_root_from_config(config) / _WORKING_STATE_RELATIVE_PATH


def _draft_archive_root(request_sha256: str, config: RunnableConfig | None) -> Path:
    return (
        workspace_root_from_config(config)
        / _DRAFT_ARCHIVE_RELATIVE_DIR
        / request_sha256
    )


def _draft_response(
    request: dict[str, Any], sections: dict[str, Any]
) -> dict[str, Any]:
    return {
        "schema_version": "research-planner-response-v1",
        "task_name": request["task_name"],
        "research_question": request["research_question"],
        "response_kind": "plan_ready",
        "plan_content": deepcopy(sections),
    }


def _new_draft_state(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": _WORKING_STATE_VERSION,
        "request": deepcopy(request),
        "request_sha256": canonical_json_sha256(request),
        "sections": {},
        "section_receipts": {},
        "section_failures": {},
        "revision_patch_failures": {},
        "revision_candidate": None,
        "pending_evidence_revision": None,
        "evidence_revision_history": [],
        "failure_policy_version": _DRAFT_FAILURE_POLICY_VERSION,
        "failure_policy_migrations": [],
        "validated_response": None,
        "validated_response_sha256": None,
        "updated_at": _utc_now(),
    }


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _load_or_initialize_draft(
    request: dict[str, Any], config: RunnableConfig | None
) -> dict[str, Any]:
    context = workspace_context_key(config)
    path = _working_state_path(config)
    state: dict[str, Any] | None = None
    if path.is_file():
        try:
            candidate = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            candidate = None
        if (
            isinstance(candidate, dict)
            and candidate.get("schema_version") == _WORKING_STATE_VERSION
            and isinstance(candidate.get("request"), dict)
            and isinstance(candidate.get("request_sha256"), str)
            and isinstance(candidate.get("sections"), dict)
        ):
            state = candidate
    if state is None:
        state = _new_draft_state(request)
    state.setdefault("section_receipts", {})
    state.setdefault("section_failures", {})
    state.setdefault("revision_patch_failures", {})
    state.setdefault("revision_candidate", None)
    state.setdefault("pending_evidence_revision", None)
    state.setdefault("evidence_revision_history", [])
    _migrate_failure_policy(state, config)
    _persist_draft(state, config)
    request_sha256 = state["request_sha256"]
    with _STATE_LOCK:
        _PLANNER_DRAFTS[context] = state
        validated = state.get("validated_response")
        if isinstance(validated, dict):
            _VALIDATED_RESPONSES[(context, request_sha256)] = validated
    return state


def _migrate_failure_policy(
    state: dict[str, Any], config: RunnableConfig | None
) -> None:
    """Unlock old no-progress stops only when feedback semantics changed."""

    old_version = str(
        state.get("failure_policy_version") or "planner-section-feedback-v1"
    )
    if old_version == _DRAFT_FAILURE_POLICY_VERSION:
        state.setdefault("failure_policy_migrations", [])
        return
    failures = state.setdefault("section_failures", {})
    blocked_sections = sorted(
        name
        for name, failure in failures.items()
        if isinstance(failure, dict) and failure.get("must_stop") is True
    )
    migrations = state.setdefault("failure_policy_migrations", [])
    migration_number = len(migrations) + 1
    receipt = {
        "schema_version": "research-planner-failure-policy-migration-v1",
        "request_sha256": state["request_sha256"],
        "migration_number": migration_number,
        "from_policy_version": old_version,
        "to_policy_version": _DRAFT_FAILURE_POLICY_VERSION,
        "unlocked_sections": blocked_sections,
        "reason": "section validation feedback contract changed",
        "created_at": _utc_now(),
    }
    relative_path = (
        _DRAFT_ARCHIVE_RELATIVE_DIR
        / state["request_sha256"]
        / "failure_policy_migrations"
        / f"m{migration_number:04d}.json"
    )
    receipt["receipt_path"] = str(relative_path)
    _atomic_write_json(workspace_root_from_config(config) / relative_path, receipt)
    for section_name in blocked_sections:
        failures.pop(section_name, None)
    migrations.append(
        {
            "from_policy_version": old_version,
            "to_policy_version": _DRAFT_FAILURE_POLICY_VERSION,
            "unlocked_sections": blocked_sections,
            "receipt_path": str(relative_path),
        }
    )
    state["failure_policy_version"] = _DRAFT_FAILURE_POLICY_VERSION


def _lookup_draft(
    request: dict[str, Any], config: RunnableConfig | None
) -> dict[str, Any]:
    context = workspace_context_key(config)
    request_sha256 = canonical_json_sha256(request)
    with _STATE_LOCK:
        state = _PLANNER_DRAFTS.get(context)
    if isinstance(state, dict) and state.get("request_sha256") == request_sha256:
        return state
    return _load_or_initialize_draft(request, config)


def _persist_draft(state: dict[str, Any], config: RunnableConfig | None) -> None:
    state["updated_at"] = _utc_now()
    archive_path = (
        _draft_archive_root(state["request_sha256"], config) / "working_state.json"
    )
    _atomic_write_json(archive_path, state)
    _atomic_write_json(_working_state_path(config), state)


def _persist_section_version(
    state: dict[str, Any],
    section_name: str,
    value: Any,
    config: RunnableConfig | None,
) -> dict[str, Any]:
    receipts = state.setdefault("section_receipts", {})
    prior = receipts.get(section_name, [])
    if not isinstance(prior, list):
        prior = []
    section_dir = (
        _draft_archive_root(state["request_sha256"], config) / "sections" / section_name
    )
    existing_versions = [
        int(path.stem[1:])
        for path in section_dir.glob("v[0-9][0-9][0-9][0-9].json")
        if path.stem[1:].isdigit()
    ]
    version = max(existing_versions, default=0) + 1
    relative_path = (
        _DRAFT_ARCHIVE_RELATIVE_DIR
        / state["request_sha256"]
        / "sections"
        / section_name
        / f"v{version:04d}.json"
    )
    payload = {
        "schema_version": "research-planner-draft-section-v1",
        "request_sha256": state["request_sha256"],
        "section_name": section_name,
        "section_version": version,
        "section_sha256": canonical_json_sha256(value),
        "created_at": _utc_now(),
        "value": deepcopy(value),
    }
    _atomic_write_json(workspace_root_from_config(config) / relative_path, payload)
    receipt = {
        "section_version": version,
        "section_sha256": payload["section_sha256"],
        "path": str(relative_path),
    }
    prior.append(receipt)
    receipts[section_name] = prior
    return receipt


def _record_section_failure(
    state: dict[str, Any],
    section_name: str,
    error: str,
    config: RunnableConfig | None,
) -> dict[str, Any]:
    fingerprint = hashlib.sha256(error.encode("utf-8")).hexdigest()
    failures = state.setdefault("section_failures", {})
    section_state = failures.get(section_name, {})
    if not isinstance(section_state, dict):
        section_state = {}
    total = int(section_state.get("total", 0)) + 1
    previous_fingerprint = section_state.get("last_fingerprint")
    consecutive = (
        int(section_state.get("consecutive", 0)) + 1
        if previous_fingerprint == fingerprint
        else 1
    )
    must_stop = consecutive >= 2 or total >= 6
    failure_dir = (
        _draft_archive_root(state["request_sha256"], config) / "failures" / section_name
    )
    existing_numbers = [
        int(path.stem[1:])
        for path in failure_dir.glob("f[0-9][0-9][0-9][0-9].json")
        if path.stem[1:].isdigit()
    ]
    failure_number = max(existing_numbers, default=0) + 1
    receipt = {
        "schema_version": "research-planner-draft-failure-v1",
        "request_sha256": state["request_sha256"],
        "section_name": section_name,
        "failure_number": failure_number,
        "policy_failure_count": total,
        "error_fingerprint": fingerprint,
        "consecutive_same_error": consecutive,
        "must_stop": must_stop,
        "error": error[:4000],
        "created_at": _utc_now(),
    }
    relative_path = (
        _DRAFT_ARCHIVE_RELATIVE_DIR
        / state["request_sha256"]
        / "failures"
        / section_name
        / f"f{failure_number:04d}.json"
    )
    receipt["receipt_path"] = str(relative_path)
    _atomic_write_json(workspace_root_from_config(config) / relative_path, receipt)
    section_state.update(
        {
            "total": total,
            "last_fingerprint": fingerprint,
            "consecutive": consecutive,
            "must_stop": must_stop,
            "latest_receipt_path": str(relative_path),
        }
    )
    failures[section_name] = section_state
    _persist_draft(state, config)
    return receipt


def _record_revision_patch_failure(
    state: dict[str, Any], error: str, config: RunnableConfig | None
) -> dict[str, Any]:
    """Persist one append-only failure receipt for Qwen no-progress control."""

    fingerprint = hashlib.sha256(error.encode("utf-8")).hexdigest()
    failures = state.setdefault("revision_patch_failures", {})
    total = int(failures.get("total", 0)) + 1
    consecutive = (
        int(failures.get("consecutive", 0)) + 1
        if failures.get("last_fingerprint") == fingerprint
        else 1
    )
    must_stop = consecutive >= 2 or total >= 6
    failure_dir = (
        _draft_archive_root(state["request_sha256"], config)
        / "failures"
        / "revision_patch"
    )
    existing_numbers = [
        int(path.stem[1:])
        for path in failure_dir.glob("f[0-9][0-9][0-9][0-9].json")
        if path.stem[1:].isdigit()
    ]
    failure_number = max(existing_numbers, default=0) + 1
    relative_path = (
        _DRAFT_ARCHIVE_RELATIVE_DIR
        / state["request_sha256"]
        / "failures"
        / "revision_patch"
        / f"f{failure_number:04d}.json"
    )
    receipt = {
        "schema_version": "research-planner-revision-patch-failure-v1",
        "request_sha256": state["request_sha256"],
        "failure_number": failure_number,
        "policy_failure_count": total,
        "error_fingerprint": fingerprint,
        "consecutive_same_error": consecutive,
        "must_stop": must_stop,
        "error": error[:4000],
        "created_at": _utc_now(),
        "receipt_path": str(relative_path),
    }
    _atomic_write_json(workspace_root_from_config(config) / relative_path, receipt)
    failures.update(
        {
            "total": total,
            "consecutive": consecutive,
            "last_fingerprint": fingerprint,
            "must_stop": must_stop,
            "latest_receipt_path": str(relative_path),
        }
    )
    _persist_draft(state, config)
    return receipt


def _item_index(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        label = next(
            (
                str(item[key])
                for key in ("question", "statement", "name", "objective", "title")
                if isinstance(item.get(key), str)
            ),
            "",
        )
        rows.append({"id": item["id"], "label": label[:180]})
    return rows


def _draft_checkpoint(state: dict[str, Any]) -> dict[str, Any]:
    sections = state.get("sections", {})
    candidate = state.get("revision_candidate")
    completed = [name for name in _PLAN_SECTION_ORDER if name in sections]
    missing = [name for name in _PLAN_SECTION_ORDER if name not in sections]
    state_map = sections.get("research_state_map", {})
    pending_revision = state.get("pending_evidence_revision")
    if isinstance(candidate, dict) and candidate.get("sections"):
        next_action = "commit_revision_candidate"
    elif missing:
        next_action = f"update_draft:{missing[0]}"
    elif isinstance(pending_revision, dict):
        next_action = "repair_evidence_revision"
    elif isinstance(state.get("validated_response"), dict):
        next_action = "freeze_plan"
    else:
        next_action = "validate_draft"
    return {
        "schema_version": "research-planner-draft-checkpoint-v1",
        "request_sha256": state["request_sha256"],
        "draft_sha256": canonical_json_sha256(
            _draft_response(state["request"], sections)
        ),
        "completed_sections": completed,
        "missing_sections": missing,
        "next_section": missing[0] if missing else None,
        "next_action": next_action,
        "validated": isinstance(state.get("validated_response"), dict),
        "pending_evidence_revision": deepcopy(pending_revision),
        "revision_candidate": {
            "base_draft_sha256": candidate.get("base_draft_sha256"),
            "staged_sections": [
                name
                for name in _PLAN_SECTION_ORDER
                if name in candidate.get("sections", {})
            ],
        }
        if isinstance(candidate, dict)
        else None,
        "reference_index": {
            "research_subquestions": _item_index(sections.get("research_subquestions")),
            "research_state_items": _item_index(state_map.get("items")),
            "evidence_sources": _item_index(sections.get("evidence_sources")),
            "required_datasets": _item_index(sections.get("required_datasets")),
            "research_artifacts": _item_index(sections.get("research_artifacts")),
            "research_route": _item_index(sections.get("research_route")),
            "evaluation_rules": _item_index(sections.get("evaluation_rules")),
            "report_outline": _item_index(sections.get("report_outline")),
        },
    }


def register_planner_evidence_revision(
    revision_review_id: str,
    revision_capsule: dict[str, Any],
    config: RunnableConfig | None,
) -> dict[str, Any]:
    """Invalidate an approved draft and persist one hash-bound review capsule.

    This is an orchestration hook, not a model-facing tool. Re-registering the
    same verdict is idempotent so retries cannot erase progress or create a new
    model budget window.
    """

    if not revision_review_id.strip():
        raise ValueError("revision_review_id is required")
    if not isinstance(revision_capsule, dict):
        raise TypeError("revision_capsule must be an object")
    capsule_review_id = revision_capsule.get("review_id")
    if capsule_review_id not in {None, revision_review_id}:
        raise ValueError("revision capsule review_id does not match action")
    request = _lookup_request("", config)
    state = _lookup_draft(request, config)
    capsule = deepcopy(revision_capsule)
    capsule["review_id"] = revision_review_id
    fingerprint = canonical_json_sha256(capsule)
    current = state.get("pending_evidence_revision")
    if (
        isinstance(current, dict)
        and current.get("review_id") == revision_review_id
        and current.get("capsule_sha256") == fingerprint
    ):
        return {
            "status": "evidence_revision_already_registered",
            **deepcopy(current),
            "draft_checkpoint": _draft_checkpoint(state),
        }

    revision_dir = (
        _draft_archive_root(state["request_sha256"], config) / "evidence_revisions"
    )
    existing_numbers = [
        int(path.stem[1:])
        for path in revision_dir.glob("r[0-9][0-9][0-9][0-9].json")
        if path.stem[1:].isdigit()
    ]
    revision_number = max(existing_numbers, default=0) + 1
    relative_path = (
        _DRAFT_ARCHIVE_RELATIVE_DIR
        / state["request_sha256"]
        / "evidence_revisions"
        / f"r{revision_number:04d}.json"
    )
    pending = {
        "review_id": revision_review_id,
        "capsule_sha256": fingerprint,
        "artifact_sha256": capsule.get("artifact_sha256"),
        "issues": deepcopy(capsule.get("issues", [])),
        "registered_at": _utc_now(),
        "receipt_path": str(relative_path),
    }
    receipt = {
        "schema_version": "research-planner-evidence-revision-v1",
        "request_sha256": state["request_sha256"],
        "revision_number": revision_number,
        **deepcopy(pending),
        "capsule": capsule,
    }
    _atomic_write_json(workspace_root_from_config(config) / relative_path, receipt)

    # A different Evidence verdict starts from the active draft, never from an
    # uncommitted candidate created for an older verdict.
    if isinstance(current, dict) and current.get("review_id") != revision_review_id:
        state["revision_candidate"] = None
    state["pending_evidence_revision"] = pending
    state["validated_response"] = None
    state["validated_response_sha256"] = None
    context = workspace_context_key(config)
    request_sha256 = canonical_json_sha256(request)
    with _STATE_LOCK:
        _VALIDATED_RESPONSES.pop((context, request_sha256), None)
    _persist_draft(state, config)
    return {
        "status": "evidence_revision_registered",
        **deepcopy(pending),
        "draft_checkpoint": _draft_checkpoint(state),
    }


def _resolve_pending_evidence_revision(
    state: dict[str, Any], config: RunnableConfig | None
) -> dict[str, Any] | None:
    pending = state.get("pending_evidence_revision")
    if not isinstance(pending, dict):
        return None
    resolved = {
        **deepcopy(pending),
        "resolved_at": _utc_now(),
        "resolved_draft_sha256": canonical_json_sha256(
            _draft_response(state["request"], state["sections"])
        ),
    }
    history = state.setdefault("evidence_revision_history", [])
    history.append(resolved)
    state["pending_evidence_revision"] = None
    return resolved


_CRITERION_KIND_ALIASES = {
    "exact_user_requirement": "request_based",
    "planned_data": "data_based",
    "qualitative_check": "qualitative",
}
_CRITERION_KINDS = {"source_based", "data_based", "request_based", "qualitative"}


def _sanitize_evaluation_rules(value: Any) -> Any:
    """Deterministically normalize mechanical criterion_basis violations.

    The planner model repeatedly re-emits the same alias ``kind`` values and
    mismatched id-list constraints across revision rounds; those are purely
    syntactic (no scientific judgement), so coerce them before schema
    validation instead of spending model turns on them. Content-bearing
    sections (evidence_sources, research_route) are NOT sanitized here.
    """

    if not isinstance(value, list):
        return value
    for item in value:
        if not isinstance(item, dict):
            continue
        basis = item.get("criterion_basis")
        if not isinstance(basis, dict):
            continue
        kind = basis.get("kind")
        if kind in _CRITERION_KIND_ALIASES:
            kind = _CRITERION_KIND_ALIASES[kind]
            basis["kind"] = kind
        sources = basis.get("evidence_source_ids")
        artifacts = basis.get("artifact_ids")
        sources = sources if isinstance(sources, list) else []
        artifacts = artifacts if isinstance(artifacts, list) else []
        if kind == "source_based":
            basis["evidence_source_ids"] = sources
            basis["artifact_ids"] = []
        elif kind == "data_based":
            basis["evidence_source_ids"] = []
            basis["artifact_ids"] = artifacts
        elif kind in {"request_based", "qualitative"}:
            basis["evidence_source_ids"] = []
            basis["artifact_ids"] = []
    return value


def _validate_section(section_name: str, value: Any) -> None:
    errors = sorted(
        _PLAN_SECTION_VALIDATORS[section_name].iter_errors(value),
        key=lambda error: list(error.absolute_path),
    )
    if not errors:
        return
    details: list[str] = []
    if section_name == "evaluation_rules" and isinstance(value, list):
        aliases = {
            "exact_user_requirement": "request_based",
            "planned_data": "data_based",
            "qualitative_check": "qualitative",
        }
        allowed = {"source_based", "data_based", "request_based", "qualitative"}
        for index, item in enumerate(value[:20]):
            if not isinstance(item, dict):
                continue
            basis = item.get("criterion_basis")
            if not isinstance(basis, dict):
                continue
            kind = basis.get("kind")
            sources = basis.get("evidence_source_ids")
            artifacts = basis.get("artifact_ids")
            if kind in aliases:
                details.append(
                    f"evaluation_rules.{index}.criterion_basis.kind: use "
                    f"{aliases[kind]!r}, not alias {kind!r}"
                )
                kind = aliases[kind]
            elif kind not in allowed:
                details.append(
                    f"evaluation_rules.{index}.criterion_basis.kind: choose exactly "
                    "source_based, data_based, request_based, or qualitative"
                )
                continue
            if kind == "source_based" and (
                not isinstance(sources, list)
                or not sources
                or not isinstance(artifacts, list)
                or artifacts
            ):
                details.append(
                    f"evaluation_rules.{index}.criterion_basis: source_based requires "
                    "one or more evidence_source_ids and artifact_ids=[]"
                )
            elif kind == "data_based" and (
                not isinstance(artifacts, list)
                or not artifacts
                or not isinstance(sources, list)
                or sources
            ):
                details.append(
                    f"evaluation_rules.{index}.criterion_basis: data_based requires "
                    "evidence_source_ids=[] and one or more artifact_ids"
                )
            elif kind in {"request_based", "qualitative"} and (
                not isinstance(sources, list)
                or sources
                or not isinstance(artifacts, list)
                or artifacts
            ):
                details.append(
                    f"evaluation_rules.{index}.criterion_basis: {kind} requires "
                    "evidence_source_ids=[] and artifact_ids=[]"
                )
    for error in errors[:8]:
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        details.append(f"{section_name}.{location}: {error.message}")
    raise ValueError("section schema validation failed: " + " | ".join(details))


def _preflight_error_count(error: str) -> int:
    matched = re.search(r"发现\s*(\d+)\s*组问题", error)
    if matched:
        return int(matched.group(1))
    bullet_count = sum(
        1 for line in error.splitlines() if line.lstrip().startswith("- ")
    )
    return max(1, bullet_count)


def _preflight_sections(
    request: dict[str, Any], sections: dict[str, Any]
) -> tuple[dict[str, Any] | None, str, int]:
    try:
        result = preflight_planner_response(
            request,
            _draft_response(request, sections),
            include_validated_response=True,
        )
        if result.get("status") == "plan_ready":
            return result, "", 0
        error = str(
            result.get("error") or "planner preflight did not become plan_ready"
        )
    except Exception as exc:
        error = str(exc)
    return None, error, _preflight_error_count(error)


def _bind_request(request: dict[str, Any], config: RunnableConfig | None) -> str:
    """Store a canonical request and make it the active request."""
    sha = canonical_json_sha256(request)
    context = workspace_context_key(config)
    with _STATE_LOCK:
        _REQUEST_CACHE[(context, sha)] = request
        _ACTIVE_REQUEST_SHA256[context] = sha
    return sha


def _lookup_request(
    request_sha256: str, config: RunnableConfig | None
) -> dict[str, Any]:
    """Return the cached request, restoring its immutable task-local receipt."""
    context = workspace_context_key(config)
    with _STATE_LOCK:
        if request_sha256 and (context, request_sha256) in _REQUEST_CACHE:
            return _REQUEST_CACHE[(context, request_sha256)]
        active = _ACTIVE_REQUEST_SHA256.get(context, "")
        if active and (context, active) in _REQUEST_CACHE:
            return _REQUEST_CACHE[(context, active)]
    path = _working_state_path(config)
    if path.is_file():
        try:
            persisted = json.loads(path.read_text(encoding="utf-8"))
            request = validate_planner_request(persisted["request"])
            persisted_sha256 = str(persisted["request_sha256"])
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            request = None
            persisted_sha256 = ""
        if isinstance(request, dict):
            actual_sha256 = canonical_json_sha256(request)
            if actual_sha256 != persisted_sha256:
                raise RuntimeError(
                    "persisted planner request hash does not match working state"
                )
            if request_sha256 and request_sha256 != actual_sha256:
                raise RuntimeError(
                    "requested planner hash does not match the task-local request"
                )
            _bind_request(request, config)
            return request
    raise RuntimeError(
        "No research planner request is bound. Call research_planner_get_brief first."
    )


def _ok(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, default=str)


def _err(message: str) -> str:
    return json.dumps(
        {"status": "error", "error": message}, ensure_ascii=False, default=str
    )


@tool(parse_docstring=True)
def research_planner_get_brief(
    request_input: str = "", config: RunnableConfig = None
) -> str:
    """Create or load a research planner request and return a planning brief.

    This is the entry point for the research-planner-agent skill. If
    ``request_input`` starts with ``@``, the remainder is treated as a path
    (relative to the project root) to a JSON request file. Otherwise the input
    is used as a natural-language research question.

    Args:
        request_input: A research question or ``@<path-to-json-request>``. Leave
            empty when resuming an already-bound draft or Evidence revision.

    Returns:
        JSON string containing the planning brief and ``request_sha256``.
    """
    try:
        if not request_input:
            requested = _lookup_request("", config)
        elif request_input.startswith("@"):
            raw_path = request_input[1:]
            path = resolve_scoped_path(raw_path, config, allow_project=True)
            payload = json.loads(path.read_text(encoding="utf-8"))
            requested = validate_planner_request(payload)
        else:
            requested = build_natural_planner_request(request_input)

        input_request_sha256 = canonical_json_sha256(requested)
        draft_state = _load_or_initialize_draft(requested, config)
        request = draft_state["request"]
        brief = build_planning_brief(request)
        sha = _bind_request(request, config)
        brief["instruction"] = (
            "For a published plan, do not construct the complete response in one "
            "model turn. Persist exactly one plan_content section at a time with "
            "research_planner_update_draft, following draft_protocol.section_order; "
            "then call research_planner_validate_draft and "
            "research_planner_freeze_plan. The one-shot validation tool remains a "
            "backward-compatible path for non-draft callers. Once all sections "
            "exist, cross-section repairs must use "
            "research_planner_apply_revision_patch, or stage large replacements "
            "one at a time with research_planner_stage_revision_section and finish "
            "with research_planner_commit_revision_candidate. A non-improving "
            "candidate cannot replace the active draft."
        )
        checkpoint = _draft_checkpoint(draft_state)
        if checkpoint["next_action"] == "commit_revision_candidate":
            brief["instruction"] = (
                "A hash-bound shadow revision candidate already exists. The next "
                "tool call MUST be research_planner_commit_revision_candidate; do "
                "not validate or reread the active draft first. A rejected commit "
                "will return the candidate-level errors needed for further staging."
            )
        elif checkpoint["next_action"] == "repair_evidence_revision":
            brief["instruction"] = (
                "Evidence rejected the hash-bound active plan. The old validated "
                "response has been invalidated. Inspect only the sections named by "
                "draft_checkpoint.pending_evidence_revision, stage changed values "
                "in the shadow candidate, and atomically commit once. Do not freeze "
                "or return the prior plan."
            )
        brief["draft_protocol"] = {
            "schema_version": "research-planner-draft-protocol-v1",
            "state_path": str(_WORKING_STATE_RELATIVE_PATH),
            "section_order": list(_PLAN_SECTION_ORDER),
            "write_rule": "persist exactly one section per update call",
            "resume_rule": (
                "continue from draft_checkpoint.next_section; use reference_index "
                "for stable cross-section ids"
            ),
            "validation_rule": (
                "only validate after every section is persisted; repair the named "
                "section and validate once more"
            ),
        }
        return _ok(
            {
                "brief": brief,
                "request_sha256": sha,
                "input_request_sha256": input_request_sha256,
                "canonical_request_reused": input_request_sha256 != sha,
                "draft_checkpoint": checkpoint,
            }
        )
    except Exception as exc:
        return _err(str(exc))


@tool(parse_docstring=True)
def research_planner_search_local_knowledge(
    query: str,
    limit: int = 5,
    request_sha256: str = "",
    config: RunnableConfig = None,
) -> str:
    """Search bundled local Markdown knowledge for the active research request.

    Args:
        query: Natural-language search query.
        limit: Maximum number of results (1-10).
        request_sha256: Optional SHA256 of a cached request; falls back to the
            active request.

    Returns:
        JSON string with ranked local knowledge snippets.
    """
    try:
        request = _lookup_request(request_sha256, config)
        result = search_local_knowledge(query, limit)
        result["request_sha256"] = canonical_json_sha256(request)
        return _ok(result)
    except Exception as exc:
        return _err(str(exc))


@tool(parse_docstring=True)
def research_planner_search_literature(
    query: str,
    limit: int = 5,
    from_year: int = 0,
    to_year: int = 0,
    request_sha256: str = "",
    config: RunnableConfig = None,
) -> str:
    """Search OpenAlex scholarly literature metadata.

    Args:
        query: Natural-language search query.
        limit: Maximum number of results (1-10).
        from_year: Earliest publication year (0 means no filter).
        to_year: Latest publication year (0 means no filter).
        request_sha256: Optional SHA256 of a cached request; falls back to the
            active request.

    Returns:
        JSON string with literature metadata results.
    """
    try:
        request = _lookup_request(request_sha256, config)
        result = search_scholarly_literature(
            query,
            limit,
            from_year if from_year > 0 else None,
            to_year if to_year > 0 else None,
        )
        result["request_sha256"] = canonical_json_sha256(request)
        return _ok(result)
    except Exception as exc:
        return _err(str(exc))


@tool(parse_docstring=True)
def research_planner_resolve_reference(
    reference: str,
    request_sha256: str = "",
    config: RunnableConfig = None,
) -> str:
    """Resolve a DOI, URL, or local file reference.

    Args:
        reference: DOI, URL, or project-local file path to resolve.
        request_sha256: Optional SHA256 of a cached request; falls back to the
            active request.

    Returns:
        JSON string with canonical locator and verification status.
    """
    try:
        request = _lookup_request(request_sha256, config)
        result = resolve_reference(reference)
        result["request_sha256"] = canonical_json_sha256(request)
        return _ok(result)
    except Exception as exc:
        return _err(str(exc))


@tool(parse_docstring=True)
def research_planner_extract_evidence(
    source_id: str,
    claim: str,
    relationship: str = "context",
    source_text: str = "",
    local_path: str = "",
    limit: int = 5,
    request_sha256: str = "",
    config: RunnableConfig = None,
) -> str:
    """Extract candidate passages from source text or a local file.

    Exactly one of ``source_text`` or ``local_path`` must be provided.

    Args:
        source_id: Identifier for the source being searched.
        claim: Claim to locate evidence for.
        relationship: Proposed relationship of the evidence to the claim
            (supports, opposes, limits, context).
        source_text: Inline source text (alternative to ``local_path``).
        local_path: Project-local file path (alternative to ``source_text``).
        limit: Maximum number of candidate passages (1-10).
        request_sha256: Optional SHA256 of a cached request; falls back to the
            active request.

    Returns:
        JSON string with candidate evidence passages.
    """
    try:
        request = _lookup_request(request_sha256, config)
        if bool(source_text) == bool(local_path):
            raise ValueError("Provide exactly one of source_text or local_path.")

        kwargs: dict[str, Any] = {"relationship": relationship, "limit": limit}
        if source_text:
            kwargs["source_text"] = source_text
        else:
            kwargs["local_path"] = local_path

        result = extract_source_evidence(source_id, claim, **kwargs)
        result["request_sha256"] = canonical_json_sha256(request)
        return _ok(result)
    except Exception as exc:
        return _err(str(exc))


@tool(parse_docstring=True)
def research_planner_inspect_dataset(
    local_path: str,
    expected_variables: str = "",
    time_field: str = "",
    sample_limit: int = 5000,
    request_sha256: str = "",
    config: RunnableConfig = None,
) -> str:
    """Inspect a local CSV/JSON/JSONL dataset and report bounded metadata.

    Args:
        local_path: Project-local path to the dataset file.
        expected_variables: Comma-separated list of expected variable names.
        time_field: Optional name of the time column.
        sample_limit: Maximum records to inspect (1-5000).
        request_sha256: Optional SHA256 of a cached request; falls back to the
            active request.

    Returns:
        JSON string with dataset metadata and variable checks.
    """
    try:
        request = _lookup_request(request_sha256, config)
        expected_list: list[str] | None = None
        if expected_variables:
            expected_list = [
                name.strip() for name in expected_variables.split(",") if name.strip()
            ]

        result = inspect_dataset(
            local_path,
            expected_variables=expected_list,
            time_field=time_field or None,
            sample_limit=sample_limit,
        )
        result["request_sha256"] = canonical_json_sha256(request)
        return _ok(result)
    except Exception as exc:
        return _err(str(exc))


@tool(parse_docstring=True)
def research_planner_update_draft(
    section_name: str,
    section_json: str,
    request_sha256: str = "",
    config: RunnableConfig = None,
) -> str:
    """Atomically persist one plan_content section for the active request.

    Sections must be written in the order reported by ``research_planner_get_brief``.
    Rewriting an already-persisted section is allowed for a targeted validation
    repair. Every successful call survives a later model or connection failure.

    Args:
        section_name: One exact plan_content section name from draft_protocol.
        section_json: JSON encoding of that section's value, not the full plan.
        request_sha256: Optional bound request hash; defaults to the active request.

    Returns:
        Compact draft checkpoint with completed, missing, and stable reference ids.
    """
    state: dict[str, Any] | None = None
    try:
        if section_name not in _PLAN_SECTION_ORDER:
            raise ValueError(
                "section_name must be one of: " + ", ".join(_PLAN_SECTION_ORDER)
            )
        request = _lookup_request(request_sha256, config)
        state = _lookup_draft(request, config)
        sections = state["sections"]
        if all(name in sections for name in _PLAN_SECTION_ORDER):
            return _ok(
                {
                    "status": "error",
                    "error_code": "PLANNER_COMPLETE_DRAFT_REQUIRES_ATOMIC_PATCH",
                    "section_name": section_name,
                    "must_stop": False,
                    "instruction": (
                        "The complete draft is immutable to single-section writes. "
                        "Call research_planner_validate_draft, then submit every "
                        "mutually dependent replacement in one "
                        "research_planner_apply_revision_patch call."
                    ),
                    "draft_checkpoint": _draft_checkpoint(state),
                }
            )
        prior_failure = state.get("section_failures", {}).get(section_name, {})
        if isinstance(prior_failure, dict) and prior_failure.get("must_stop") is True:
            return _ok(
                {
                    "status": "blocked",
                    "error_code": "PLANNER_SECTION_NO_PROGRESS",
                    "section_name": section_name,
                    "must_stop": True,
                    "failure_count": prior_failure.get("total", 0),
                    "error_fingerprint": prior_failure.get("last_fingerprint"),
                    "latest_receipt_path": prior_failure.get("latest_receipt_path"),
                    "instruction": (
                        "Stop this specialist attempt immediately. Do not call "
                        "research_planner_update_draft or restart the brief again."
                    ),
                }
            )
        section_index = _PLAN_SECTION_ORDER.index(section_name)
        missing_prerequisites = [
            name for name in _PLAN_SECTION_ORDER[:section_index] if name not in sections
        ]
        if missing_prerequisites:
            raise ValueError(
                "persist prior sections first: " + ", ".join(missing_prerequisites)
            )
        value = json.loads(section_json)
        if section_name == "evaluation_rules":
            value = _sanitize_evaluation_rules(value)
        _validate_section(section_name, value)
        receipt = _persist_section_version(state, section_name, value, config)
        sections[section_name] = value
        state.get("section_failures", {}).pop(section_name, None)
        state["validated_response"] = None
        state["validated_response_sha256"] = None
        context = workspace_context_key(config)
        sha = canonical_json_sha256(request)
        with _STATE_LOCK:
            _VALIDATED_RESPONSES.pop((context, sha), None)
        _persist_draft(state, config)
        result = _draft_checkpoint(state)
        result["status"] = "draft_section_persisted"
        result["persisted_section"] = section_name
        result["section_receipt"] = receipt
        result["state_path"] = str(_WORKING_STATE_RELATIVE_PATH)
        return _ok(result)
    except Exception as exc:
        if state is not None and section_name in _PLAN_SECTION_ORDER:
            failure = _record_section_failure(state, section_name, str(exc), config)
            return _ok(
                {
                    "status": "blocked" if failure["must_stop"] else "error",
                    "error_code": (
                        "PLANNER_SECTION_NO_PROGRESS"
                        if failure["must_stop"]
                        else "PLANNER_SECTION_INVALID"
                    ),
                    "section_name": section_name,
                    "error": str(exc),
                    "error_fingerprint": failure["error_fingerprint"],
                    "consecutive_same_error": failure["consecutive_same_error"],
                    "failure_count": failure["policy_failure_count"],
                    "must_stop": failure["must_stop"],
                    "failure_receipt_path": failure["receipt_path"],
                    "instruction": (
                        "Stop this specialist attempt immediately; keep the prior "
                        "persisted sections for a changed approach."
                        if failure["must_stop"]
                        else "Repair this section once using the localized error."
                    ),
                }
            )
        return _err(str(exc))


@tool(parse_docstring=True)
def research_planner_apply_revision_patch(
    changes_json: str,
    request_sha256: str = "",
    config: RunnableConfig = None,
) -> str:
    """Atomically apply a multi-section repair only when full-plan errors decrease.

    Use this after a complete draft receives a cross-section validation or
    Evidence revision. The candidate patch is validated entirely in memory;
    no section becomes active unless it reaches plan_ready or strictly reduces
    the deterministic full-plan error count.

    Args:
        changes_json: JSON object mapping section names to complete replacement values.
        request_sha256: Optional bound request hash; defaults to the active request.

    Returns:
        Patch receipt, full-plan status, and remaining localized validation error.
    """

    state: dict[str, Any] | None = None
    try:
        request = _lookup_request(request_sha256, config)
        state = _lookup_draft(request, config)
        missing = [
            name for name in _PLAN_SECTION_ORDER if name not in state["sections"]
        ]
        if missing:
            raise ValueError(
                "multi-section revision requires a complete draft; missing: "
                + ", ".join(missing)
            )
        changes = json.loads(changes_json)
        if not isinstance(changes, dict) or not changes:
            raise ValueError("changes_json must be a non-empty JSON object")
        unknown = sorted(set(changes) - set(_PLAN_SECTION_ORDER))
        if unknown:
            raise ValueError("unknown plan sections: " + ", ".join(unknown))
        if len(changes) > 8:
            raise ValueError("one revision patch may change at most 8 sections")
        for section_name, value in changes.items():
            _validate_section(section_name, value)
        changes = {
            section_name: value
            for section_name, value in changes.items()
            if value != state["sections"].get(section_name)
        }
        if not changes:
            raise ValueError(
                "revision patch rejected without mutation: every submitted "
                "section is identical to the active draft"
            )

        baseline_result, _baseline_error, baseline_count = _preflight_sections(
            request, state["sections"]
        )
        candidate_sections = deepcopy(state["sections"])
        candidate_sections.update(deepcopy(changes))
        candidate_result, candidate_error, candidate_count = _preflight_sections(
            request, candidate_sections
        )
        if candidate_count != 0 and (
            baseline_result is not None or candidate_count >= baseline_count
        ):
            raise ValueError(
                "revision patch rejected without mutation: full-plan error count "
                f"would change from {baseline_count} to {candidate_count}; "
                "the count must strictly decrease. Candidate errors: " + candidate_error
            )

        receipts: dict[str, Any] = {}
        for section_name in _PLAN_SECTION_ORDER:
            if section_name not in changes:
                continue
            receipts[section_name] = _persist_section_version(
                state, section_name, candidate_sections[section_name], config
            )
        state["sections"] = candidate_sections
        for section_name in changes:
            state.get("section_failures", {}).pop(section_name, None)
        state["revision_patch_failures"] = {}
        state["validated_response"] = None
        state["validated_response_sha256"] = None
        context = workspace_context_key(config)
        sha = canonical_json_sha256(request)
        with _STATE_LOCK:
            _VALIDATED_RESPONSES.pop((context, sha), None)
        if candidate_result is not None:
            validated = candidate_result.get("_validated_response")
            if isinstance(validated, dict):
                state["validated_response"] = validated
                state["validated_response_sha256"] = canonical_json_sha256(validated)
                with _STATE_LOCK:
                    _VALIDATED_RESPONSES[(context, sha)] = validated
                resolved_revision = _resolve_pending_evidence_revision(state, config)
            else:
                resolved_revision = None
        else:
            resolved_revision = None
        _persist_draft(state, config)
        return _ok(
            {
                "status": (
                    "plan_ready" if candidate_count == 0 else "revision_patch_persisted"
                ),
                "changed_sections": list(receipts),
                "section_receipts": receipts,
                "baseline_error_count": baseline_count,
                "remaining_error_count": candidate_count,
                "remaining_error": candidate_error,
                "resolved_evidence_revision": resolved_revision,
                "draft_checkpoint": _draft_checkpoint(state),
            }
        )
    except Exception as exc:
        if state is not None:
            error = str(exc)
            failure = _record_revision_patch_failure(state, error, config)
            return _ok(
                {
                    "status": "blocked" if failure["must_stop"] else "error",
                    "error_code": (
                        "PLANNER_REVISION_NO_PROGRESS"
                        if failure["must_stop"]
                        else "PLANNER_REVISION_NOT_IMPROVED"
                    ),
                    "error": error,
                    "error_fingerprint": failure["error_fingerprint"],
                    "failure_count": failure["policy_failure_count"],
                    "consecutive_same_error": failure["consecutive_same_error"],
                    "must_stop": failure["must_stop"],
                    "failure_receipt_path": failure["receipt_path"],
                    "instruction": (
                        "Stop this specialist attempt and preserve the active draft."
                        if failure["must_stop"]
                        else "Submit one changed multi-section patch that strictly reduces the reported full-plan error count."
                    ),
                }
            )
        return _err(str(exc))


@tool(parse_docstring=True)
def research_planner_stage_revision_section(
    section_name: str,
    section_json: str,
    request_sha256: str = "",
    config: RunnableConfig = None,
) -> str:
    """Stage one replacement section without changing the active complete draft.

    Args:
        section_name: Exact plan_content section to place in the shadow candidate.
        section_json: JSON encoding of the complete replacement section value.
        request_sha256: Optional bound request hash; defaults to the active request.

    Returns:
        Shadow-candidate checkpoint. Active section versions remain unchanged.
    """

    state: dict[str, Any] | None = None
    try:
        if section_name not in _PLAN_SECTION_ORDER:
            raise ValueError("unknown plan section: " + section_name)
        request = _lookup_request(request_sha256, config)
        state = _lookup_draft(request, config)
        if any(name not in state["sections"] for name in _PLAN_SECTION_ORDER):
            raise ValueError("shadow revision staging requires a complete active draft")
        value = json.loads(section_json)
        _validate_section(section_name, value)
        base_sha256 = canonical_json_sha256(_draft_response(request, state["sections"]))
        candidate = state.get("revision_candidate")
        if (
            not isinstance(candidate, dict)
            or candidate.get("base_draft_sha256") != base_sha256
        ):
            candidate = {
                "schema_version": "research-planner-revision-candidate-v1",
                "base_draft_sha256": base_sha256,
                "sections": {},
                "receipts": {},
                "created_at": _utc_now(),
            }
        prior_staged = candidate["sections"].get(section_name)
        if prior_staged == value:
            state.get("section_failures", {}).pop(section_name, None)
            _persist_draft(state, config)
            return _ok(
                {
                    "status": "revision_section_already_staged",
                    "base_draft_sha256": base_sha256,
                    "staged_sections": [
                        name
                        for name in _PLAN_SECTION_ORDER
                        if name in candidate["sections"]
                    ],
                    "section_receipt": candidate["receipts"].get(section_name),
                    "active_draft_unchanged": True,
                    "new_version_written": False,
                }
            )
        if state["sections"].get(section_name) == value:
            candidate["sections"].pop(section_name, None)
            candidate["receipts"].pop(section_name, None)
            state["revision_candidate"] = candidate if candidate["sections"] else None
            state.get("section_failures", {}).pop(section_name, None)
            _persist_draft(state, config)
            return _ok(
                {
                    "status": "revision_section_matches_active",
                    "base_draft_sha256": base_sha256,
                    "staged_sections": [
                        name
                        for name in _PLAN_SECTION_ORDER
                        if name in candidate["sections"]
                    ],
                    "active_draft_unchanged": True,
                    "new_version_written": False,
                }
            )
        candidate_dir = (
            _draft_archive_root(state["request_sha256"], config)
            / "revision_candidates"
            / base_sha256
            / section_name
        )
        existing = [
            int(path.stem[1:])
            for path in candidate_dir.glob("v[0-9][0-9][0-9][0-9].json")
            if path.stem[1:].isdigit()
        ]
        version = max(existing, default=0) + 1
        relative_path = (
            _DRAFT_ARCHIVE_RELATIVE_DIR
            / state["request_sha256"]
            / "revision_candidates"
            / base_sha256
            / section_name
            / f"v{version:04d}.json"
        )
        receipt = {
            "schema_version": "research-planner-revision-candidate-section-v1",
            "base_draft_sha256": base_sha256,
            "section_name": section_name,
            "candidate_version": version,
            "section_sha256": canonical_json_sha256(value),
            "created_at": _utc_now(),
            "value": deepcopy(value),
        }
        _atomic_write_json(workspace_root_from_config(config) / relative_path, receipt)
        candidate["sections"][section_name] = deepcopy(value)
        candidate["receipts"][section_name] = {
            "path": str(relative_path),
            "candidate_version": version,
            "section_sha256": receipt["section_sha256"],
        }
        candidate["updated_at"] = _utc_now()
        state["revision_candidate"] = candidate
        state.get("section_failures", {}).pop(section_name, None)
        _persist_draft(state, config)
        return _ok(
            {
                "status": "revision_section_staged",
                "base_draft_sha256": base_sha256,
                "staged_sections": [
                    name
                    for name in _PLAN_SECTION_ORDER
                    if name in candidate["sections"]
                ],
                "section_receipt": candidate["receipts"][section_name],
                "active_draft_unchanged": True,
                "new_version_written": True,
            }
        )
    except Exception as exc:
        if state is not None and section_name in _PLAN_SECTION_ORDER:
            failure = _record_section_failure(state, section_name, str(exc), config)
            return _ok(
                {
                    "status": "blocked" if failure["must_stop"] else "error",
                    "error_code": (
                        "PLANNER_REVISION_SECTION_NO_PROGRESS"
                        if failure["must_stop"]
                        else "PLANNER_REVISION_SECTION_INVALID"
                    ),
                    "section_name": section_name,
                    "error": str(exc),
                    "error_fingerprint": failure["error_fingerprint"],
                    "consecutive_same_error": failure["consecutive_same_error"],
                    "failure_count": failure["policy_failure_count"],
                    "must_stop": failure["must_stop"],
                    "failure_receipt_path": failure["receipt_path"],
                    "instruction": (
                        "Stop this specialist attempt immediately and preserve the "
                        "active draft and shadow candidate."
                        if failure["must_stop"]
                        else "Repair this staged section once using the localized error."
                    ),
                }
            )
        return _err(str(exc))


@tool(parse_docstring=True)
def research_planner_commit_revision_candidate(
    request_sha256: str = "", config: RunnableConfig = None
) -> str:
    """Validate and atomically commit the staged multi-section candidate.

    Args:
        request_sha256: Optional bound request hash; defaults to the active request.

    Returns:
        The same monotonic-improvement receipt as apply_revision_patch.
    """

    try:
        request = _lookup_request(request_sha256, config)
        state = _lookup_draft(request, config)
        candidate = state.get("revision_candidate")
        if not isinstance(candidate, dict) or not candidate.get("sections"):
            raise ValueError("no staged revision candidate exists")
        active_sha256 = canonical_json_sha256(
            _draft_response(request, state["sections"])
        )
        if candidate.get("base_draft_sha256") != active_sha256:
            raise RuntimeError(
                "active draft changed after staging; discard the stale candidate and restage"
            )
        raw = research_planner_apply_revision_patch.func(
            changes_json=json.dumps(candidate["sections"], ensure_ascii=False),
            request_sha256=request_sha256,
            config=config,
        )
        result = json.loads(raw)
        if result.get("status") in {"plan_ready", "revision_patch_persisted"}:
            refreshed = _lookup_draft(request, config)
            refreshed["revision_candidate"] = None
            _persist_draft(refreshed, config)
            result["shadow_candidate_committed"] = True
        else:
            result["shadow_candidate_committed"] = False
            result["instruction"] = (
                "Restage only the candidate sections needed to produce a strictly "
                "smaller full-plan error count, then commit once more."
            )
        return _ok(result)
    except Exception as exc:
        return _err(str(exc))


@tool(parse_docstring=True)
def research_planner_get_draft_status(
    request_sha256: str = "", config: RunnableConfig = None
) -> str:
    """Return a compact resumable checkpoint without returning the full draft.

    Args:
        request_sha256: Optional bound request hash; defaults to the active request.

    Returns:
        Completed/missing sections, next section, hashes, and cross-reference ids.
    """
    try:
        request = _lookup_request(request_sha256, config)
        state = _lookup_draft(request, config)
        result = _draft_checkpoint(state)
        result["status"] = "draft_checkpoint"
        result["state_path"] = str(_WORKING_STATE_RELATIVE_PATH)
        return _ok(result)
    except Exception as exc:
        return _err(str(exc))


@tool(parse_docstring=True)
def research_planner_get_section(
    section_name: str,
    request_sha256: str = "",
    config: RunnableConfig = None,
) -> str:
    """Read one active planner section and any staged replacement by hash.

    This bounded read is the only supported way for a revision attempt to inspect
    an existing complete section. It does not expose arbitrary workspace files.

    Args:
        section_name: Exact plan_content section to inspect.
        request_sha256: Optional bound request hash; defaults to the active request.

    Returns:
        Active section JSON, its receipt and hash, plus an optional staged value.
    """

    try:
        if section_name not in _PLAN_SECTION_ORDER:
            raise ValueError("unknown plan section: " + section_name)
        request = _lookup_request(request_sha256, config)
        state = _lookup_draft(request, config)
        if section_name not in state["sections"]:
            raise ValueError("planner section has not been persisted: " + section_name)
        active_value = deepcopy(state["sections"][section_name])
        receipts = state.get("section_receipts", {}).get(section_name, [])
        candidate = state.get("revision_candidate")
        candidate_value = None
        candidate_receipt = None
        if isinstance(candidate, dict):
            candidate_value = deepcopy(candidate.get("sections", {}).get(section_name))
            candidate_receipt = deepcopy(
                candidate.get("receipts", {}).get(section_name)
            )
        return _ok(
            {
                "status": "draft_section",
                "request_sha256": state["request_sha256"],
                "active_draft_sha256": canonical_json_sha256(
                    _draft_response(request, state["sections"])
                ),
                "section_name": section_name,
                "active_section": active_value,
                "active_section_sha256": canonical_json_sha256(active_value),
                "active_section_receipt": (
                    deepcopy(receipts[-1]) if receipts else None
                ),
                "staged_section": candidate_value,
                "staged_section_sha256": (
                    canonical_json_sha256(candidate_value)
                    if candidate_value is not None
                    else None
                ),
                "staged_section_receipt": candidate_receipt,
                "read_only": True,
            }
        )
    except Exception as exc:
        return _err(str(exc))


@tool(parse_docstring=True)
def research_planner_validate_draft(
    request_sha256: str = "", config: RunnableConfig = None
) -> str:
    """Validate the complete task-local planner draft and cache it for freezing.

    Args:
        request_sha256: Optional bound request hash; defaults to the active request.

    Returns:
        Deterministic full-contract preflight result or a localized validation error.
    """
    try:
        request = _lookup_request(request_sha256, config)
        state = _lookup_draft(request, config)
        checkpoint = _draft_checkpoint(state)
        if checkpoint["missing_sections"]:
            raise ValueError(
                "planner draft is incomplete; missing sections: "
                + ", ".join(checkpoint["missing_sections"])
            )
        response_payload = _draft_response(request, state["sections"])
        result = preflight_planner_response(
            request, response_payload, include_validated_response=True
        )
        if result.get("status") == "plan_ready" and "_validated_response" in result:
            validated = result["_validated_response"]
            context = workspace_context_key(config)
            sha = canonical_json_sha256(request)
            state["validated_response"] = validated
            state["validated_response_sha256"] = canonical_json_sha256(validated)
            with _STATE_LOCK:
                _VALIDATED_RESPONSES[(context, sha)] = validated
            _persist_draft(state, config)
        return _ok(result)
    except Exception as exc:
        return _err(str(exc))


@tool(parse_docstring=True)
def research_planner_validate_plan(
    response_json: str,
    request_sha256: str = "",
    config: RunnableConfig = None,
) -> str:
    """Validate a research planner response against the bound request.

    A successfully validated ``plan_ready`` response is cached for
    ``research_planner_freeze_plan``.

    Args:
        response_json: One JSON string containing a research-planner-response-v1
            object.
        request_sha256: Optional SHA256 of a cached request; falls back to the
            active request.

    Returns:
        JSON string with validation status and counts.
    """
    try:
        request = _lookup_request(request_sha256, config)
        response_payload = json.loads(response_json)
        result = preflight_planner_response(
            request, response_payload, include_validated_response=True
        )
        if result.get("status") == "plan_ready" and "_validated_response" in result:
            context = workspace_context_key(config)
            sha = canonical_json_sha256(request)
            validated = result["_validated_response"]
            with _STATE_LOCK:
                _VALIDATED_RESPONSES[(context, sha)] = validated
            state = _lookup_draft(request, config)
            state["sections"] = deepcopy(validated["plan_content"])
            state["validated_response"] = deepcopy(validated)
            state["validated_response_sha256"] = canonical_json_sha256(validated)
            _persist_draft(state, config)
        return _ok(result)
    except Exception as exc:
        return _err(str(exc))


@tool(parse_docstring=True)
def research_planner_freeze_plan(
    request_sha256: str = "", config: RunnableConfig = None
) -> str:
    """Freeze the most recently validated plan-ready response for the bound request.

    The validated response must have been produced by
    ``research_planner_validate_plan`` and cached in this module.

    Args:
        request_sha256: Optional SHA256 of a cached request; falls back to the
            active request.

    Returns:
        JSON string with freeze outcome and file paths.
    """
    try:
        request = _lookup_request(request_sha256, config)
        sha = canonical_json_sha256(request)
        context = workspace_context_key(config)
        with _STATE_LOCK:
            validated_response = _VALIDATED_RESPONSES.get((context, sha))
        if not validated_response:
            state = _lookup_draft(request, config)
            persisted = state.get("validated_response")
            if isinstance(persisted, dict):
                validated_response = persisted
        if not validated_response:
            raise RuntimeError(
                "No validated plan-ready response found. Call "
                "research_planner_validate_draft or research_planner_validate_plan first."
            )
        workspace_root = workspace_root_from_config(config)
        result = freeze_research_plan(
            request,
            validated_response,
            runs_root=workspace_root / "planner" / "runs",
            path_root=workspace_root,
        )
        return _ok(result)
    except Exception as exc:
        return _err(str(exc))


RESEARCH_PLANNER_TOOLS = [
    research_planner_get_brief,
    research_planner_search_local_knowledge,
    research_planner_search_literature,
    research_planner_resolve_reference,
    research_planner_extract_evidence,
    research_planner_inspect_dataset,
    research_planner_update_draft,
    research_planner_apply_revision_patch,
    research_planner_stage_revision_section,
    research_planner_commit_revision_candidate,
    research_planner_get_draft_status,
    research_planner_get_section,
    research_planner_validate_draft,
    research_planner_validate_plan,
    research_planner_freeze_plan,
]

register_tool_bundle("research-planner", RESEARCH_PLANNER_TOOLS)

__all__ = ["RESEARCH_PLANNER_TOOLS"] + [t.name for t in RESEARCH_PLANNER_TOOLS]
