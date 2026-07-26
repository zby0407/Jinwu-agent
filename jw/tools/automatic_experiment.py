"""LangChain tool wrappers for the Pi-style Automatic Experiment Python bridge.

These tools expose the automatic-experiment-agent skill to the JW
agent. They call ``src/automatic_experiment.service`` in-process, the same
deterministic core the Pi extension drives through its JSON bridge. All run
state lives on disk under ``experiment/runs/<run_id>/``; the wrappers are
stateless and recover everything from persisted checkpoints.

Execution backend note: the sandboxed worker runs through the platform
executor (WSL2/bubblewrap on Windows, ``sandbox-exec`` on macOS). The audit
record always states the backend that was actually used.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SRC = _PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from langchain_core.runnables import RunnableConfig  # noqa: E402
from langchain_core.tools import tool  # noqa: E402

from automatic_experiment import service  # noqa: E402
from automatic_experiment.contracts import (  # noqa: E402
    ContractError,
    default_request,
)
from automatic_experiment.state import task_workspace  # noqa: E402
from jw.tools.registry import register_tool_bundle  # noqa: E402
from jw.workspaces import (  # noqa: E402
    resolve_scoped_path,
    workspace_root_from_config,
)


def _ok(result: dict[str, Any]) -> str:
    return json.dumps({"ok": True, **result}, ensure_ascii=False, default=str)


def _parse_json_arg(value: Any, label: str) -> Any:
    """Accept either the native object or its JSON-string form.

    Some model providers emit structured tool arguments as JSON strings; the
    deterministic core always expects real objects.
    """
    if value is None or isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label} must be a JSON object, not raw text") from exc
        return parsed
    raise ValueError(f"{label} must be a JSON object, not {type(value).__name__}")


def _err(exc: Exception) -> str:
    output: dict[str, Any] = {
        "ok": False,
        "status": "error",
        "error_type": type(exc).__name__,
        "message": str(exc),
    }
    if isinstance(exc, ContractError):
        field_path = exc.field_path
        if field_path is None:
            match = re.search(
                r"(?:request|response|design|worker_result|scientific_assessment)"
                r"(?:\.[A-Za-z0-9_]+|\[[0-9]+\])*",
                str(exc),
            )
            field_path = match.group(0) if match else None
        output.update(
            {
                "error_code": exc.error_code,
                "field_path": field_path,
                "suggestion": exc.suggestion
                or "按字段路径和 bind/verification preview 返回的写作指南修正后重试。",
            }
        )
    return json.dumps(output, ensure_ascii=False, default=str)


def _normalize_model_input_path(value: object) -> str:
    path = str(value or "").strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    path = path.lstrip("/")
    if path.startswith("inputs/"):
        return path
    if re.fullmatch(r"runs/[A-Za-z][A-Za-z0-9_-]{0,127}/public/.+", path):
        return path
    raise ValueError(
        "experiment inputs must be staged under inputs/ or reference "
        "runs/<run_id>/public/; do not pass /work, ./data, or host paths"
    )


def _request_from_model_object(value: dict[str, Any]) -> dict[str, Any]:
    """Convert the compact JSON shape models commonly emit into the contract.

    The bind tool historically treated such JSON as opaque natural-language
    text, silently discarding its ``inputs`` array.  Accept it explicitly and
    fail early on paths that cannot enter the immutable input snapshot.
    """

    if isinstance(value.get("request"), dict):
        value = value["request"]
    if "schema_version" in value and "input_refs" in value:
        return value
    task = value.get("task")
    if not isinstance(task, str) or not task.strip():
        raise ValueError("structured experiment request requires a non-empty task")
    request = default_request(task)
    compact_inputs = value.get("input_refs", value.get("inputs", []))
    if compact_inputs is None:
        compact_inputs = []
    if not isinstance(compact_inputs, list):
        raise ValueError("structured experiment inputs must be an array")
    input_refs: list[dict[str, Any]] = []
    for index, row in enumerate(compact_inputs, start=1):
        if not isinstance(row, dict):
            raise ValueError(
                f"structured experiment inputs[{index - 1}] must be an object"
            )
        input_refs.append(
            {
                "id": str(row.get("id") or f"input_{index:02d}"),
                "path": _normalize_model_input_path(row.get("path")),
                "description": str(
                    row.get("description")
                    or "Input explicitly supplied in the structured bind request."
                ),
                "required": bool(row.get("required", True)),
            }
        )
    request["input_refs"] = input_refs
    for field in ("success_criteria", "method_constraints", "user_notes", "replay_of"):
        if field in value:
            request[field] = value[field]
    for field in ("resource_budget", "run_budget", "seed_policy"):
        override = value.get(field)
        if isinstance(override, dict):
            request[field] = {**request[field], **override}
    return request


@tool(parse_docstring=True)
def automatic_experiment_bind_request(
    request_input: str, config: RunnableConfig = None
) -> str:
    """Bind a natural-language experiment task and create its immutable run.

    This is the entry point for the automatic-experiment-agent skill. Pass the
    task text verbatim, or ``@<path-to-json-request>`` (relative to the project
    root) for an advanced JSON request with explicit budgets, seeds, or input
    manifests.

    Args:
        request_input: The experiment task or ``@<path-to-json-request>``.

    Returns:
        JSON string with the new ``run_id``, bound request, fingerprints, and
        the precise nested-field authoring guide for response, design, and
        scientific assessment.
    """
    try:
        supplied = request_input.strip()
        if not supplied:
            raise ValueError("request_input must not be empty")
        if supplied.startswith("@"):
            path = resolve_scoped_path(supplied[1:].strip(), config, allow_project=True)
            payload = {"request": json.loads(path.read_text(encoding="utf-8"))}
        else:
            try:
                structured = json.loads(supplied) if supplied.startswith("{") else None
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "request_input starts like JSON but is invalid; pass plain task text "
                    "or one complete JSON object"
                ) from exc
            payload = (
                {"request": _request_from_model_object(structured)}
                if isinstance(structured, dict)
                else {"request_input": supplied}
            )
        with task_workspace(workspace_root_from_config(config)):
            return _ok(service.bind_request(payload))
    except Exception as exc:
        return _err(exc)


@tool(parse_docstring=True)
def automatic_experiment_inspect_inputs(
    run_id: str, config: RunnableConfig = None
) -> str:
    """Inspect and snapshot the declared inputs of a run.

    Reads only allowed directories, rejects path escapes, hidden evaluations,
    secrets, and links, then creates the immutable input snapshot.

    Args:
        run_id: The run identifier returned by ``automatic_experiment_bind_request``.

    Returns:
        JSON string with per-input metadata and snapshot status.
    """
    try:
        with task_workspace(workspace_root_from_config(config)):
            return _ok(service.inspect_inputs(run_id))
    except Exception as exc:
        return _err(exc)


@tool(parse_docstring=True)
def automatic_experiment_validate_design(
    run_id: str,
    response: dict[str, Any] | str,
    design: dict[str, Any] | str | None = None,
    config: RunnableConfig = None,
) -> str:
    """Validate the experiment response and its dynamic stage design.

    Submit only ``run_id``, ``response``, and ``design`` at the top level;
    criteria, measurement plans, and stage definitions live inside ``design``.
    Clarification or blocked responses need no design. All independently
    visible issues are reported in one pass. ``response`` and ``design`` are
    JSON objects; a JSON-encoded string of the object is also accepted.

    Args:
        run_id: The run identifier.
        response: The automatic-experiment response object.
        design: The experiment design object (omit for clarification/blocked).

    Returns:
        JSON string with validation status and the full issue list.
    """
    try:
        parsed_response = _parse_json_arg(response, "response")
        parsed_design = _parse_json_arg(design, "design")
        if not isinstance(parsed_response, dict):
            raise ValueError("response must be a JSON object")
        if parsed_design is not None and not isinstance(parsed_design, dict):
            raise ValueError("design must be a JSON object or null")
        with task_workspace(workspace_root_from_config(config)):
            return _ok(
                service.validate_and_store_design(
                    run_id, parsed_response, parsed_design
                )
            )
    except Exception as exc:
        return _err(exc)


@tool(parse_docstring=True)
def automatic_experiment_prepare_attempt(
    run_id: str,
    files: list[dict[str, str]] | str,
    change_reason: str,
    parent_attempt: str = "",
    config: RunnableConfig = None,
) -> str:
    """Create an immutable attempt from the complete Python file set.

    Submit the full code for the CURRENT stage only; the files are checked
    against context-derived paths before the read-only attempt is created.
    ``files`` is a JSON array; a JSON-encoded string of the array is also
    accepted.

    Args:
        run_id: The run identifier.
        files: List of ``{"path": ..., "content": ...}`` code files
            (1-20 files, each content up to 512 KiB).
        change_reason: Why this attempt differs from its parent (or why it is
            the first attempt).
        parent_attempt: Optional parent attempt id (``attempt-NNN``) this one
            repairs.

    Returns:
        JSON string with the new ``attempt_id`` and required worker outputs.
    """
    try:
        parsed_files = _parse_json_arg(files, "files")
        if not isinstance(parsed_files, list):
            raise ValueError("files must be a JSON array")
        with task_workspace(workspace_root_from_config(config)):
            return _ok(
                service.prepare(
                    run_id,
                    parsed_files,
                    parent_attempt or None,
                    change_reason,
                )
            )
    except Exception as exc:
        return _err(exc)


@tool(parse_docstring=True)
def automatic_experiment_execute_attempt(
    run_id: str, attempt_id: str, config: RunnableConfig = None
) -> str:
    """Really execute one prepared attempt in the isolated sandbox.

    Records the exact command, resource usage, logs, and artifact facts. Only
    attempts created by ``automatic_experiment_prepare_attempt`` can be
    executed, and each attempt executes at most once.

    Args:
        run_id: The run identifier.
        attempt_id: The attempt to execute (``attempt-NNN``).

    Returns:
        JSON string with execution facts and resource measurements.
    """
    try:
        with task_workspace(workspace_root_from_config(config)):
            return _ok(service.execute(run_id, attempt_id))
    except Exception as exc:
        return _err(exc)


@tool(parse_docstring=True)
def automatic_experiment_verify_result(
    run_id: str,
    attempt_id: str,
    scientific_assessment: dict[str, Any] | str | None = None,
    config: RunnableConfig = None,
) -> str:
    """Verify executed results and decide the stage outcome.

    Call once WITHOUT ``scientific_assessment`` to obtain the checked actual
    values and evidence preview; only then submit the scientific
    interpretation based on those facts. ``scientific_assessment`` is a JSON
    object; a JSON-encoded string of the object is also accepted.

    Args:
        run_id: The run identifier.
        attempt_id: The attempt whose results are verified (``attempt-NNN``).
        scientific_assessment: The scientific interpretation object, submitted
            only after a preview call for the same attempt.

    Returns:
        JSON string with verified results, stage transition, or the assessment
        preview requirements.
    """
    try:
        parsed_assessment = _parse_json_arg(
            scientific_assessment, "scientific_assessment"
        )
        if parsed_assessment is not None and not isinstance(parsed_assessment, dict):
            raise ValueError("scientific_assessment must be a JSON object or null")
        with task_workspace(workspace_root_from_config(config)):
            return _ok(service.verify(run_id, attempt_id, parsed_assessment))
    except Exception as exc:
        return _err(exc)


@tool(parse_docstring=True)
def automatic_experiment_finalize(run_id: str, config: RunnableConfig = None) -> str:
    """Generate the formal Markdown report for a run.

    Composes verified machine facts with the checked researcher narrative into
    ``experiment/runs/<run_id>/report.md`` and returns the user-display
    Markdown. Every terminal state (including user stop, budget exhaustion,
    or missing inputs) still produces an honest report.

    Args:
        run_id: The run identifier.

    Returns:
        JSON string with the entry result and user-display Markdown.
    """
    try:
        with task_workspace(workspace_root_from_config(config)):
            return _ok(service.finalize(run_id))
    except Exception as exc:
        return _err(exc)


AUTOMATIC_EXPERIMENT_TOOLS = [
    automatic_experiment_bind_request,
    automatic_experiment_inspect_inputs,
    automatic_experiment_validate_design,
    automatic_experiment_prepare_attempt,
    automatic_experiment_execute_attempt,
    automatic_experiment_verify_result,
    automatic_experiment_finalize,
]

register_tool_bundle("automatic-experiment", AUTOMATIC_EXPERIMENT_TOOLS)

__all__ = ["AUTOMATIC_EXPERIMENT_TOOLS"] + [t.name for t in AUTOMATIC_EXPERIMENT_TOOLS]
