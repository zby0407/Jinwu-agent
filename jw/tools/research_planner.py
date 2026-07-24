"""LangChain tool wrappers for the Pi-style Research Planner Python bridge.

These tools expose the research-planner-agent skill to the JW agent.
They wrap deterministic contract validation, knowledge retrieval, and plan
freeze operations implemented in ``src/research_planner``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from threading import RLock
from typing import Any

_PROJECT_ROOT = Path("/Users/zhuanz/Desktop/tb2/JW")
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
_STATE_LOCK = RLock()


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
    """Return the cached request, falling back to the active request."""
    context = workspace_context_key(config)
    with _STATE_LOCK:
        if request_sha256 and (context, request_sha256) in _REQUEST_CACHE:
            return _REQUEST_CACHE[(context, request_sha256)]
        active = _ACTIVE_REQUEST_SHA256.get(context, "")
        if active and (context, active) in _REQUEST_CACHE:
            return _REQUEST_CACHE[(context, active)]
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
    request_input: str, config: RunnableConfig = None
) -> str:
    """Create or load a research planner request and return a planning brief.

    This is the entry point for the research-planner-agent skill. If
    ``request_input`` starts with ``@``, the remainder is treated as a path
    (relative to the project root) to a JSON request file. Otherwise the input
    is used as a natural-language research question.

    Args:
        request_input: A research question or ``@<path-to-json-request>``.

    Returns:
        JSON string containing the planning brief and ``request_sha256``.
    """
    try:
        if request_input.startswith("@"):
            raw_path = request_input[1:]
            path = resolve_scoped_path(raw_path, config, allow_project=True)
            payload = json.loads(path.read_text(encoding="utf-8"))
            request = validate_planner_request(payload)
        else:
            request = build_natural_planner_request(request_input)

        brief = build_planning_brief(request)
        sha = _bind_request(request, config)
        return _ok({"brief": brief, "request_sha256": sha})
    except Exception as exc:  # noqa: BLE001
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
    except Exception as exc:  # noqa: BLE001
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
    except Exception as exc:  # noqa: BLE001
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
    except Exception as exc:  # noqa: BLE001
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
    except Exception as exc:  # noqa: BLE001
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
            time_field=time_field if time_field else None,
            sample_limit=sample_limit,
        )
        result["request_sha256"] = canonical_json_sha256(request)
        return _ok(result)
    except Exception as exc:  # noqa: BLE001
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
            with _STATE_LOCK:
                _VALIDATED_RESPONSES[(context, sha)] = result["_validated_response"]
        return _ok(result)
    except Exception as exc:  # noqa: BLE001
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
            raise RuntimeError(
                "No validated plan-ready response found. Call research_planner_validate_plan first."
            )
        workspace_root = workspace_root_from_config(config)
        result = freeze_research_plan(
            request,
            validated_response,
            runs_root=workspace_root / "planner" / "runs",
            path_root=workspace_root,
        )
        return _ok(result)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


RESEARCH_PLANNER_TOOLS = [
    research_planner_get_brief,
    research_planner_search_local_knowledge,
    research_planner_search_literature,
    research_planner_resolve_reference,
    research_planner_extract_evidence,
    research_planner_inspect_dataset,
    research_planner_validate_plan,
    research_planner_freeze_plan,
]

__all__ = ["RESEARCH_PLANNER_TOOLS"] + [t.name for t in RESEARCH_PLANNER_TOOLS]
