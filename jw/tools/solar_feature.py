"""Solar feature engineering tools for the JW agent.

Wraps the solar feature agent workflows (audit, feature engineering,
experiment handoff, dataset statistics) as LangChain ``@tool`` functions
so the agent can invoke them during a research conversation.

The underlying workflow code lives in ``solar_agent_src/`` and uses flat
imports (``import chat_session``, etc.).  This module puts that directory
on ``sys.path`` at import time so those imports resolve.
"""

from __future__ import annotations

import csv
import hashlib
import io
import itertools
import json
import math
import os
import re
import runpy
import sys
import tempfile
import warnings
from datetime import UTC, datetime
from pathlib import Path

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from jw.solar_forecast import validate_precursor_feature_record
from jw.workspaces import resolve_scoped_path, workspace_root_from_config

from .registry import register_tool_bundle

# ---------------------------------------------------------------------------
# Make the solar feature agent modules importable.
# ---------------------------------------------------------------------------
_SOLAR_AGENT_SRC = Path(__file__).resolve().parent.parent / "solar_agent_src"
if str(_SOLAR_AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(_SOLAR_AGENT_SRC))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_json(data: dict) -> str:
    """Serialize a result dict to a JSON string, tolerating non-standard types."""
    return json.dumps(data, ensure_ascii=False, default=str)


def _error_json(tool_name: str, exc: Exception) -> str:
    """Build a JSON error envelope so the agent can parse failures uniformly."""
    return _to_json(
        {
            "status": "error",
            "tool": tool_name,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }
    )


def _research_task_id(config: RunnableConfig | None) -> str:
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    thread_id = (
        configurable.get("thread_id") if isinstance(configurable, dict) else None
    )
    if isinstance(thread_id, str) and thread_id.strip():
        return thread_id.strip()
    return workspace_root_from_config(config).name


def _validated_task_metadata(
    config: RunnableConfig | None,
) -> tuple[Path, str, dict[str, object]]:
    """Return task metadata only when runtime and persisted task IDs agree."""

    root = workspace_root_from_config(config).resolve()
    task_path = root / "task.json"
    try:
        task = json.loads(task_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("current task.json cannot be read") from exc
    if not isinstance(task, dict):
        raise RuntimeError("current task.json is not an object")
    task_id = _research_task_id(config)
    if task.get("thread_id") != task_id:
        raise RuntimeError("runtime thread_id does not match the current task.json")
    return root, task_id, task


def _task_bound_research_question(
    research_question: str | None, config: RunnableConfig | None
) -> str:
    """Load the current task question and reject a model-supplied substitute."""

    _root, _task_id, task = _validated_task_metadata(config)
    question = task.get("research_question")
    if not isinstance(question, str) or not question:
        raise RuntimeError("current task.json has no research_question")
    if research_question not in (None, "", question):
        raise ValueError("research_question must match the current task.json")
    return question


_SOLAR_DATA_OUTPUT_RECEIPT_CONTRACTS = {
    ("research-dataset-receipt-v1", "silso_cycle_extrema_reproduction"),
    ("solar-cycle-morphology-receipt-v1", "silso_cycle_morphology"),
    (
        "solar-cycle-26-readiness-receipt-v1",
        "solar_cycle_26_readiness_inventory",
    ),
    ("solar-precursor-cycle-table-v1", "solar_precursor_cycle_table"),
    ("solar-precursor-cycle-table-v2", "solar_precursor_cycle_table"),
    ("solar-cycle-pair-analysis-table-v2", "solar_cycle_pair_analysis_table"),
    ("solar-cycle-26-forecast-backtest-receipt-v1", "solar_cycle_26_forecast_backtest"),
}
_DATA_CONTEXT_TRANSIENT_FIELDS = {
    "context_sha256",
    "created_at",
    "instruction",
    "path_policy",
    "produced_data_receipt_ref",
    "receipt_ref",
}


def _recognized_receipt_declares_file(
    payload: object, path: str, sha256: str, task_id: str
) -> bool:
    """Check one recognized top-level Data receipt output declaration."""

    if not isinstance(payload, dict):
        return False
    contract = (payload.get("schema_version"), payload.get("receipt_type"))
    if (
        contract not in _SOLAR_DATA_OUTPUT_RECEIPT_CONTRACTS
        or payload.get("producer") != "solar-data"
        or payload.get("task_id") != task_id
        or payload.get("status") != "verified"
    ):
        return False
    outputs = payload.get("outputs")
    if not isinstance(outputs, list):
        return False
    return any(
        isinstance(output, dict)
        and output.get("path") == path
        and output.get("sha256") == sha256
        for output in outputs
    )


def _is_receipted_solar_data_output(ref: str, root: Path, task_id: str) -> bool:
    """Accept only current receipt-bound outputs below ``work/solar_data``."""

    if not isinstance(ref, str) or Path(ref).is_absolute():
        return False
    root = root.resolve()
    output_root = (root / "work" / "solar_data").resolve()
    requested = root / ref
    cursor = root
    for part in Path(ref).parts:
        cursor /= part
        if cursor.is_symlink():
            return False
    candidate = requested.resolve()
    if not candidate.is_relative_to(output_root) or not candidate.is_file():
        return False
    canonical_ref = candidate.relative_to(root).as_posix()
    digest = _file_sha256(candidate)
    receipts_root = (root / "receipts" / "datasets").resolve()
    if not receipts_root.is_dir():
        return False
    for receipt_path in receipts_root.glob("*.json"):
        if receipt_path.is_symlink() or not receipt_path.resolve().is_relative_to(
            receipts_root
        ):
            continue
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if _recognized_receipt_declares_file(receipt, canonical_ref, digest, task_id):
            return True
    return False


def _current_data_context(
    root: Path, task_id: str, task: dict[str, object]
) -> tuple[dict[str, object], dict[str, object] | None]:
    """Load the newest valid deterministic Data context for the current task."""

    receipts_root = (root / "receipts" / "datasets").resolve()
    if not receipts_root.is_dir():
        raise RuntimeError("current task has no deterministic Data context")
    question = task.get("research_question")
    task_path = root / "task.json"
    input_manifest_path = root / "input_manifest.json"
    try:
        expected_task_sha256 = _file_sha256(task_path)
        expected_input_manifest_sha256 = _file_sha256(input_manifest_path)
    except OSError as exc:
        raise RuntimeError(
            "current Data context metadata files cannot be read"
        ) from exc
    expected_question_sha256 = hashlib.sha256(
        str(question or "").encode("utf-8")
    ).hexdigest()
    candidates: list[tuple[str, str, dict[str, object], dict[str, object] | None]] = []
    for path in receipts_root.glob("data-context-*.json"):
        if path.is_symlink() or not path.resolve().is_relative_to(receipts_root):
            continue
        try:
            context = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if (
            not isinstance(context, dict)
            or context.get("schema_version") != "solar-data-context-v1"
            or context.get("task_id") != task_id
        ):
            continue
        body = {
            key: value
            for key, value in context.items()
            if key not in _DATA_CONTEXT_TRANSIENT_FIELDS
        }
        digest = _canonical_sha256(body)
        if (
            context.get("context_sha256") != digest
            or path.name != f"data-context-{digest[:16]}.json"
        ):
            continue
        if context.get("task_sha256") != expected_task_sha256:
            continue
        if context.get("research_question_sha256") != expected_question_sha256:
            continue
        if context.get("input_manifest_sha256") != expected_input_manifest_sha256:
            continue
        if (
            context.get("status") != "inputs_available"
            or context.get("must_stop") is True
        ):
            continue

        plan: dict[str, object] | None = None
        mode = context.get("context_mode")
        if mode == "full_research":
            plan_ref = context.get("plan_source_ref")
            if not isinstance(plan_ref, str) or not plan_ref:
                continue
            unresolved_plan = root / plan_ref
            plan_path = unresolved_plan.resolve()
            if (
                unresolved_plan.is_symlink()
                or not plan_path.is_relative_to(root)
                or not plan_path.is_file()
                or context.get("plan_sha256") != _file_sha256(plan_path)
            ):
                continue
            try:
                loaded_plan = json.loads(plan_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if (
                not isinstance(loaded_plan, dict)
                or loaded_plan.get("schema_version") != "research-plan-v1"
                or loaded_plan.get("research_question") != question
            ):
                continue
            plan_data_steps = [
                step
                for step in loaded_plan.get("research_route", [])
                if isinstance(step, dict) and step.get("stage") == "data"
            ]
            if not plan_data_steps or context.get("data_steps") != plan_data_steps:
                continue
            plan = loaded_plan
        elif mode == "bounded_data":
            if context.get("data_steps") not in (None, []):
                continue
        else:
            continue
        created_at = context.get("created_at")
        if not isinstance(created_at, str) or not created_at:
            continue
        candidates.append((created_at, path.name, context, plan))
    if not candidates:
        raise RuntimeError("current task has no valid deterministic Data context")
    _created_at, _name, context, plan = max(candidates)
    return context, plan


def _authoritative_data_focus(
    focus: str | None,
    *,
    root: Path,
    task_id: str,
    task: dict[str, object],
) -> str:
    """Bind an optional model echo to the current question, plan, and Data step."""

    context, plan = _current_data_context(root, task_id, task)
    question = str(task["research_question"])
    if plan is not None:
        scope = plan.get("scope")
        plan_objective = scope.get("objective") if isinstance(scope, dict) else None
        data_objectives = [
            str(step["objective"])
            for step in context.get("data_steps", [])
            if isinstance(step, dict)
            and isinstance(step.get("objective"), str)
            and str(step["objective"]).strip()
        ]
        if (
            not isinstance(plan_objective, str)
            or not plan_objective
            or not data_objectives
        ):
            raise RuntimeError(
                "current Data context has no authoritative plan objective"
            )
        data_objective = " | ".join(data_objectives)
    else:
        required_product = str(context.get("required_data_product") or "unspecified")
        protocol = str(context.get("analysis_protocol") or "none")
        plan_objective = f"Produce the bounded Data product {required_product}."
        data_objective = f"Apply the current Data protocol {protocol}."
    authoritative = (
        f"Research question: {question}\n"
        f"Plan objective: {plan_objective}\n"
        f"Data objective: {data_objective}"
    )
    if focus not in (None, "", authoritative):
        raise ValueError(
            "focus must be omitted or exactly match the current deterministic Data focus"
        )
    return authoritative


def _task_bound_harness_context(
    research_question: str | None,
    focus: str | None,
    config: RunnableConfig | None,
) -> tuple[Path, str, str, str]:
    root, task_id, task = _validated_task_metadata(config)
    question = task.get("research_question")
    if not isinstance(question, str) or not question:
        raise RuntimeError("current task.json has no research_question")
    if research_question not in (None, "", question):
        raise ValueError("research_question must match the current task.json")
    bound_focus = _authoritative_data_focus(
        focus, root=root, task_id=task_id, task=task
    )
    return root, task_id, question, bound_focus


def _qwen_harness_client(model: str | None = None):
    """Build the task-local Qwen Harness client without exposing credentials."""

    from jw.config.settings import load_config
    from jw.research_harness import QwenHarnessClient

    settings = load_config()
    base_url = os.environ.get("CUSTOM_OPENAI_BASE_URL", "") or str(
        getattr(settings, "custom_openai_base_url", "")
    )
    api_key = os.environ.get("CUSTOM_OPENAI_API_KEY", "") or str(
        getattr(settings, "custom_openai_api_key", "")
    )
    if not base_url or not api_key:
        raise RuntimeError(
            "custom-openai Harness requires the configured Base URL and API key"
        )
    return QwenHarnessClient(
        base_url=base_url,
        api_key=api_key,
        model=model or "qwen3.8-max",
    )


def _harness_result_json(result: dict[str, object]) -> str:
    receipt_ref = result.get("receipt_ref")
    artifact_refs = [
        str(item["path"])
        for item in result.get("artifacts", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    ]
    return _to_json(
        {
            "schema_version": "solar-data-harness-result-v1",
            "status": result.get("status", "error"),
            "task_id": result.get("task_id"),
            "binding": result.get("binding", {}),
            "artifact_refs": artifact_refs,
            "receipt_refs": [receipt_ref] if isinstance(receipt_ref, str) else [],
            "harness_evidence": result,
            "limitations": result.get("limitations", []),
        }
    )


def _task_chat_session(config: RunnableConfig | None):
    """Return a data session persisted only inside the current task workspace."""

    from chat_session import ChatSession

    root = workspace_root_from_config(config)
    return ChatSession(root / "work" / "solar_data" / "chat_session.json")


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _eligible_input_records(config: RunnableConfig | None) -> list[dict[str, object]]:
    root = workspace_root_from_config(config)
    manifest_path = root / "input_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise RuntimeError("task input manifest is not an object")
    excluded_roles = {
        "derived_artifact",
        "provenance",
        "reference_code",
        "test_fixture",
    }
    eligible: list[dict[str, object]] = []
    for source_group in ("inputs", "project_inputs"):
        records = manifest.get(source_group, [])
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            virtual_path = record.get("path")
            expected_sha256 = record.get("sha256")
            role = str(record.get("role") or "user_input")
            if (
                not isinstance(virtual_path, str)
                or not isinstance(expected_sha256, str)
                or role in excluded_roles
            ):
                continue
            try:
                resolved = resolve_scoped_path(
                    virtual_path,
                    config,
                    allow_project=source_group == "project_inputs",
                )
            except ValueError:
                continue
            if (
                not resolved.is_file()
                or resolved.is_symlink()
                or not isinstance(record.get("bytes"), int)
                or isinstance(record.get("bytes"), bool)
                or int(record["bytes"]) <= 0
                or resolved.stat().st_size != int(record["bytes"])
                or _file_sha256(resolved) != expected_sha256
            ):
                continue
            item: dict[str, object] = {
                "path": virtual_path,
                "sha256": expected_sha256,
                "bytes": record.get("bytes"),
                "role": role,
                "source_group": source_group,
            }
            for key in ("dataset_id", "provenance_ref"):
                if isinstance(record.get(key), str) and str(record[key]).strip():
                    item[key] = record[key]
            eligible.append(item)
    return eligible


def _resolve_eligible_data_path(value: str, config: RunnableConfig | None) -> Path:
    requested = value.strip()
    record = next(
        (item for item in _eligible_input_records(config) if item["path"] == requested),
        None,
    )
    if record is None:
        raise PermissionError(
            "data path is not a hash-matching eligible input for this task"
        )
    return resolve_scoped_path(
        requested,
        config,
        allow_project=record["source_group"] == "project_inputs",
    )


def _resolve_eligible_dataset_path(
    value: str,
    dataset_id: str,
    config: RunnableConfig | None,
) -> tuple[Path, dict[str, object]]:
    """Resolve one immutable input only when its registered dataset ID matches."""

    requested = value.strip()
    record = next(
        (
            item
            for item in _eligible_input_records(config)
            if item["path"] == requested and item.get("dataset_id") == dataset_id
        ),
        None,
    )
    if record is None:
        raise PermissionError(
            f"{dataset_id} path is not a hash-matching eligible input for this task"
        )
    resolved = resolve_scoped_path(
        requested,
        config,
        allow_project=record["source_group"] == "project_inputs",
    )
    return resolved, record


def _stage_project_input_for_harness(
    virtual_ref: str,
    record: dict[str, object],
    *,
    root: Path,
    config: RunnableConfig | None,
    task_id: str,
) -> tuple[str, dict[str, object]]:
    """Materialize one verified project input inside the task workspace.

    The project mount is intentionally read-only and lives outside the task
    root.  The Responses Harness accepts only task-local paths, so a requested
    project input is copied after the manifest/hash checks have succeeded.  A
    small receipt preserves the source-to-staged binding for downstream review.
    """

    if record.get("source_group") != "project_inputs":
        raise ValueError("only project_inputs may use Harness staging")
    expected_sha256 = record.get("sha256")
    expected_bytes = record.get("bytes")
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        raise PermissionError("project input has no valid manifest hash")
    if not isinstance(expected_bytes, int) or isinstance(expected_bytes, bool):
        raise PermissionError("project input has no valid manifest byte count")
    source = resolve_scoped_path(virtual_ref, config, allow_project=True)
    if source.is_symlink() or not source.is_file():
        raise PermissionError("project input is not a regular file")
    if (
        source.stat().st_size != expected_bytes
        or _file_sha256(source) != expected_sha256
    ):
        raise PermissionError("project input changed after manifest validation")

    source_name = Path(virtual_ref).name
    if not source_name or source_name in {".", ".."}:
        raise ValueError("project input has no safe file name")
    staged_ref = f"inputs/project/{expected_sha256[:16]}-{source_name}"
    staged = (root / staged_ref).resolve()
    inputs_root = (root / "inputs").resolve()
    if not staged.is_relative_to(inputs_root):
        raise ValueError("staged project input escaped the task workspace")
    for parent in (inputs_root, staged.parent):
        parent.mkdir(parents=True, exist_ok=True)
    if staged.exists() or staged.is_symlink():
        if staged.is_symlink() or not staged.is_file():
            raise PermissionError("staged project input is not a regular file")
        if (
            staged.stat().st_size != expected_bytes
            or _file_sha256(staged) != expected_sha256
        ):
            raise PermissionError(
                "staged project input hash does not match the manifest"
            )
    else:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=staged.parent, prefix=f".{staged.name}.", suffix=".tmp"
        )
        temporary = Path(temporary_name)
        try:
            with (
                source.open("rb") as source_handle,
                os.fdopen(descriptor, "wb") as target_handle,
            ):
                for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
                    target_handle.write(chunk)
                target_handle.flush()
                os.fsync(target_handle.fileno())
            if (
                temporary.stat().st_size != expected_bytes
                or _file_sha256(temporary) != expected_sha256
            ):
                raise PermissionError(
                    "staged project input hash does not match the manifest"
                )
            os.replace(temporary, staged)
        finally:
            temporary.unlink(missing_ok=True)

    receipt_body = {
        "schema_version": "solar-harness-input-staging-v1",
        "receipt_type": "project_input_staging",
        "status": "verified",
        "producer": "solar-data",
        "task_id": task_id,
        "source_ref": virtual_ref,
        "staged_ref": staged_ref,
        "sha256": expected_sha256,
        "bytes": expected_bytes,
    }
    receipt_digest = _canonical_sha256(receipt_body)
    receipt_ref = (
        Path("receipts")
        / "datasets"
        / f"harness-input-staging-{receipt_digest[:16]}.json"
    )
    receipt_path = root / receipt_ref
    if not receipt_path.exists():
        _atomic_write_json(
            receipt_path,
            {
                **receipt_body,
                "receipt_sha256": receipt_digest,
                "created_at": datetime.now(UTC).isoformat(),
            },
        )
    return staged_ref, {
        "source_ref": virtual_ref,
        "staged_ref": staged_ref,
        "sha256": expected_sha256,
        "bytes": expected_bytes,
        "receipt_ref": receipt_ref.as_posix(),
    }


@tool(parse_docstring=True)
def solar_research_evidence(
    queries: list[str],
    focus: str = "",
    research_question: str = "",
    model: str = "qwen3.8-max",
    config: RunnableConfig = None,
) -> str:
    """Collect task-bound web evidence with Qwen Search and Web Extractor.

    Search results are persisted as external leads. Extracted pages retain
    their URL and locator and remain subject to the independent Evidence
    review; this tool never writes Wiki canonical claims.

    Args:
        queries: One or more bounded search queries for that focus.
        focus: Optional exact echo of the deterministic Data focus; omit normally.
        research_question: Optional model echo of the exact task question.
        model: Qwen model used for the Responses Harness request.
        config: Runtime-injected task workspace configuration.
    """

    try:
        root, task_id, bound_question, bound_focus = _task_bound_harness_context(
            research_question, focus, config
        )
        result = _qwen_harness_client(model).collect_evidence(
            task_root=root,
            task_id=task_id,
            research_question=bound_question,
            focus=bound_focus,
            queries=queries,
            model=model,
        )
        return _harness_result_json(result)
    except Exception as exc:
        return _error_json("solar_research_evidence", exc)


@tool(parse_docstring=True)
def solar_research_analysis(
    input_refs: list[str],
    instructions: str,
    focus: str = "",
    research_question: str = "",
    model: str = "qwen3.8-max",
    config: RunnableConfig = None,
) -> str:
    """Run a reproducible Qwen code-interpreter analysis on bound inputs.

    Args:
        input_refs: Task-local eligible or already-produced solar data paths.
        instructions: Explicit calculation and output requirements.
        focus: Optional exact echo of the deterministic Data focus; omit normally.
        research_question: Optional model echo of the exact task question.
        model: Qwen model used for the Responses Harness request.
        config: Runtime-injected task workspace configuration.
    """

    try:
        root, task_id, bound_question, bound_focus = _task_bound_harness_context(
            research_question, focus, config
        )
        eligible_records = _eligible_input_records(config)
        records_by_path = {
            str(item["path"]): item
            for item in eligible_records
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        }
        eligible_paths = set(records_by_path)
        invalid = [
            ref
            for ref in input_refs
            if ref not in eligible_paths
            and not _is_receipted_solar_data_output(ref, root, task_id)
        ]
        if invalid:
            raise PermissionError(
                "analysis inputs must be hash-matching eligible inputs or "
                "task solar artifacts declared by a current producer receipt: "
                f"{', '.join(invalid)}"
            )
        harness_input_refs: list[str] = []
        staging_bindings: list[dict[str, object]] = []
        for ref in input_refs:
            record = records_by_path.get(ref)
            if record is not None and record.get("source_group") == "project_inputs":
                staged_ref, binding = _stage_project_input_for_harness(
                    ref,
                    record,
                    root=root,
                    config=config,
                    task_id=task_id,
                )
                harness_input_refs.append(staged_ref)
                staging_bindings.append(binding)
            else:
                harness_input_refs.append(ref)
        if not input_refs:
            raise ValueError("input_refs must not be empty")
        result = _qwen_harness_client(model).run_analysis(
            task_root=root,
            task_id=task_id,
            research_question=bound_question,
            focus=bound_focus,
            input_refs=harness_input_refs,
            instructions=instructions,
            model=model,
        )
        if staging_bindings:
            result["staged_input_bindings"] = staging_bindings
        return _harness_result_json(result)
    except Exception as exc:
        return _error_json("solar_research_analysis", exc)


def _parse_number(value: str) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _build_solar_cycle_26_readiness_inventory(
    monthly_total_path: Path,
    smoothed_path: Path,
    official_extrema_path: Path,
    f107_path: Path,
    historical_polar_path: Path,
    current_polar_path: Path,
    *,
    cutoff_date: str,
) -> dict[str, object]:
    """Build the cutoff-bound evidence inventory for the SC26 launch gate."""

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", cutoff_date):
        raise ValueError("cutoff_date must use YYYY-MM-DD")
    cutoff_month = cutoff_date[:7]
    cycle_25_start = "2019-12"

    monthly_rows: list[tuple[str, float, float | None]] = []
    for line_number, raw in enumerate(
        monthly_total_path.read_text(encoding="ascii").splitlines(), 1
    ):
        fields = raw.split()
        if not fields:
            continue
        if len(fields) not in {6, 7}:
            raise ValueError(f"invalid SILSO monthly row {line_number}")
        month = f"{int(fields[0]):04d}-{int(fields[1]):02d}"
        value = float(fields[3])
        sigma = float(fields[4])
        if month <= cutoff_month and value >= 0:
            monthly_rows.append((month, value, sigma if sigma >= 0 else None))
    if not monthly_rows:
        raise ValueError("SILSO monthly data has no observation through cutoff")
    cycle_monthly = [row for row in monthly_rows if row[0] >= cycle_25_start]
    if not cycle_monthly:
        raise ValueError("SILSO monthly data does not cover cycle 25")
    monthly_peak = max(cycle_monthly, key=lambda row: row[1])

    smoothed_rows: list[tuple[str, float, float | None, bool | None]] = []
    for line_number, fields in enumerate(
        csv.reader(
            smoothed_path.read_text(encoding="ascii").splitlines(), delimiter=";"
        ),
        1,
    ):
        if not fields:
            continue
        if len(fields) < 4:
            raise ValueError(f"invalid SILSO smoothed row {line_number}")
        month = f"{int(fields[0]):04d}-{int(fields[1]):02d}"
        value = float(fields[3])
        sigma = float(fields[4]) if len(fields) >= 5 else -1.0
        definitive = None
        if len(fields) >= 7 and fields[6].strip() in {"0", "1"}:
            definitive = fields[6].strip() == "1"
        if month <= cutoff_month and value >= 0:
            smoothed_rows.append(
                (month, value, sigma if sigma >= 0 else None, definitive)
            )
    cycle_smoothed = [row for row in smoothed_rows if row[0] >= cycle_25_start]
    if not cycle_smoothed:
        raise ValueError("SILSO smoothed data does not cover cycle 25")
    smoothed_peak = max(cycle_smoothed, key=lambda row: row[1])

    official_cycles: dict[int, dict[str, object]] = {}
    for raw in official_extrema_path.read_text(encoding="ascii").splitlines():
        fields = raw.split()
        if not fields or not fields[0].isdigit() or len(fields) < 4:
            continue
        cycle = int(fields[0])
        record: dict[str, object] = {
            "minimum_month": f"{int(fields[1]):04d}-{int(fields[2]):02d}",
            "minimum_value": float(fields[3]),
        }
        if len(fields) >= 7:
            record.update(
                {
                    "maximum_month": f"{int(fields[4]):04d}-{int(fields[5]):02d}",
                    "maximum_value": float(fields[6]),
                }
            )
        official_cycles[cycle] = record
    if 25 not in official_cycles:
        raise ValueError("official SILSO extrema table does not contain cycle 25")
    cycle_25_official = official_cycles[25]
    cycle_26_minimum_established = 26 in official_cycles

    try:
        f107_payload = json.loads(f107_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("NOAA monthly F10.7 data is invalid") from exc
    if not isinstance(f107_payload, list):
        raise ValueError("NOAA monthly F10.7 data is not an array")
    f107_rows: list[tuple[str, float]] = []
    for item in f107_payload:
        if not isinstance(item, dict):
            continue
        month, value = item.get("time-tag"), item.get("f10.7")
        if (
            isinstance(month, str)
            and month <= cutoff_month
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        ):
            f107_rows.append((month, float(value)))
    f107_rows.sort()
    cycle_f107 = [row for row in f107_rows if row[0] >= cycle_25_start]
    if not cycle_f107:
        raise ValueError("NOAA monthly F10.7 data does not cover cycle 25")
    f107_peak = max(cycle_f107, key=lambda row: row[1])

    polar_lines = [
        line
        for line in historical_polar_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    historical_reader = csv.DictReader(io.StringIO("\n".join(polar_lines)))
    historical_dates: list[float] = []
    for row in historical_reader:
        for column in ("N MWO Date", "N WSO Date", "S MWO Date", "S WSO Date"):
            parsed = _parse_number(str(row.get(column) or ""))
            if parsed is not None:
                historical_dates.append(parsed)
    if not historical_dates:
        raise ValueError("historical polar calibration contains no observation dates")

    from jw.solar_data_catalog import _validate_wso_current_polar_field

    current_polar = _validate_wso_current_polar_field(current_polar_path.read_bytes())
    official_maximum_confirmed = "maximum_month" in cycle_25_official
    wso_cutoff_complete = (
        current_polar["cutoff_window_status"] == "observed_through_cutoff"
    )
    minimum_near_precursor_available = (
        cycle_26_minimum_established and wso_cutoff_complete
    )

    evidence_gaps: list[dict[str, str]] = []
    if not official_maximum_confirmed:
        evidence_gaps.append(
            {
                "code": "SC25_OFFICIAL_MAXIMUM_UNCONFIRMED",
                "effect": "Cycle 25 peak and decline remain provisional rather than an official completed-cycle label.",
            }
        )
    if not cycle_26_minimum_established:
        evidence_gaps.append(
            {
                "code": "NEXT_MINIMUM_NOT_ESTABLISHED",
                "effect": "The cycle-25/26 boundary and cycle-25 length are not yet observed in the official extrema table.",
            }
        )
    if not wso_cutoff_complete:
        evidence_gaps.append(
            {
                "code": "WSO_CUTOFF_WINDOW_MISSING",
                "effect": "WSO has no valid polar-field observations through the requested cutoff window.",
            }
        )
    if not minimum_near_precursor_available:
        evidence_gaps.append(
            {
                "code": "MINIMUM_NEAR_POLAR_PRECURSOR_UNAVAILABLE",
                "effect": "No same-definition polar precursor is available near the still-unestablished cycle-25 minimum.",
            }
        )
    # Geomagnetic aa/Ap/Kp series are not part of the six hash-bound inputs
    # for this readiness product.  Keep that absence in the canonical
    # inventory so reviewers can downgrade/exclude the geomagnetic hypothesis
    # without manufacturing a producer-side revision loop.
    evidence_gaps.append(
        {
            "code": "GEOMAGNETIC_INDICES_UNAVAILABLE",
            "effect": "No eligible aa/Ap/Kp time series is registered; the geomagnetic-precursor hypothesis is not testable in this run.",
        }
    )

    classification_ready = not evidence_gaps
    activity_below_observed_peaks = all(
        (
            monthly_rows[-1][1] < monthly_peak[1],
            smoothed_rows[-1][1] < smoothed_peak[1],
            f107_rows[-1][1] < f107_peak[1],
        )
    )
    return {
        "schema_version": "solar-cycle-26-readiness-inventory-v1",
        "analysis_protocol": "solar_cycle_26_readiness_v1",
        "cutoff_date": cutoff_date,
        "launch_readiness": (
            "evidence_ready" if classification_ready else "insufficient_evidence"
        ),
        "formal_classification_ready": classification_ready,
        "testable_peak_interval_ready": classification_ready,
        "observations": {
            "silso_monthly": {
                "role": "cycle_25_current_state",
                "latest_month": monthly_rows[-1][0],
                "latest_value": monthly_rows[-1][1],
                "cycle_25_observed_peak_month": monthly_peak[0],
                "cycle_25_observed_peak_value": monthly_peak[1],
            },
            "silso_smoothed": {
                "role": "retrospective_cycle_25_state_label",
                "latest_month": smoothed_rows[-1][0],
                "latest_value": smoothed_rows[-1][1],
                "latest_definitive": smoothed_rows[-1][3],
                "cycle_25_smoothed_peak_month": smoothed_peak[0],
                "cycle_25_smoothed_peak_value": smoothed_peak[1],
            },
            "silso_official_extrema": {
                "role": "cycle_boundary_and_completed_peak_labels",
                "cycle_25": cycle_25_official,
                "cycle_25_official_maximum_status": (
                    "published" if official_maximum_confirmed else "not_published"
                ),
                "cycle_26_minimum_status": (
                    "published" if cycle_26_minimum_established else "not_published"
                ),
            },
            "f107_monthly": {
                "role": "cycle_25_current_activity_proxy",
                "latest_month": f107_rows[-1][0],
                "latest_value": f107_rows[-1][1],
                "cycle_25_observed_peak_month": f107_peak[0],
                "cycle_25_observed_peak_value": f107_peak[1],
            },
            "historical_polar_calibration": {
                "role": "historical_precursor_calibration_only",
                "coverage_end_decimal_year": max(historical_dates),
                "measurement_regimes": [
                    "mwo_facular_proxy",
                    "wso_magnetograph",
                ],
            },
            "wso_current_polar": {
                "role": "candidate_cycle_26_precursor_observation",
                **current_polar,
            },
        },
        "cycle_25_state_assessment": {
            "peak_status": (
                "official"
                if official_maximum_confirmed
                else "provisional_observed_not_official"
            ),
            "activity_below_observed_peaks": activity_below_observed_peaks,
            "decline_interpretation": (
                "below_observed_peaks_but_cycle_decline_not_officially_confirmed"
                if activity_below_observed_peaks and not official_maximum_confirmed
                else "not_established"
            ),
            "next_minimum_status": (
                "established" if cycle_26_minimum_established else "not_established"
            ),
        },
        "cycle_26_precursor_assessment": {
            "status": "available"
            if minimum_near_precursor_available
            else "unavailable",
            "same_definition_ready": minimum_near_precursor_available,
            "historical_calibration_available": True,
            "current_polar_cutoff_window_status": current_polar["cutoff_window_status"],
        },
        "evidence_gaps": evidence_gaps,
        "interpretation_boundary": (
            "SILSO and F10.7 describe the current state of cycle 25. A cycle-26 "
            "precursor requires a confirmed next minimum and same-definition polar "
            "measurements near that minimum."
        ),
    }


def _build_solar_precursor_cycle_rows(
    sunspot_path: Path, polar_path: Path
) -> list[dict[str, object]]:
    """Build a leakage-explicit cycle table from the curated source formats."""

    import numpy as np
    from scipy.signal import find_peaks

    monthly: list[tuple[int, int, float, float | None, int | None]] = []
    for line_number, raw in enumerate(
        sunspot_path.read_text(encoding="ascii").splitlines(), start=1
    ):
        fields = raw.split()
        if not fields:
            continue
        if len(fields) not in {6, 7}:
            raise ValueError(f"invalid SILSO row {line_number}")
        year, month, value = int(fields[0]), int(fields[1]), float(fields[3])
        sigma = float(fields[4])
        observation_count = int(fields[5])
        if not 1 <= month <= 12 or value < 0:
            raise ValueError(f"invalid SILSO semantics at row {line_number}")
        monthly.append(
            (
                year,
                month,
                value,
                sigma if sigma >= 0 else None,
                observation_count if observation_count > 0 else None,
            )
        )
    keys = [(year, month) for year, month, *_rest in monthly]
    if len(monthly) < 3_200 or keys != sorted(keys) or len(keys) != len(set(keys)):
        raise ValueError("SILSO monthly series is incomplete or non-monotonic")

    values = np.asarray([value for _, _, value, *_rest in monthly], dtype=float)
    raw_weights = np.ones(13, dtype=float)
    raw_weights[[0, -1]] = 0.5
    weights = raw_weights / raw_weights.sum()
    smoothed = np.convolve(values, weights, mode="same")
    sigmas = np.asarray(
        [sigma if sigma is not None else np.nan for *_, sigma, _count in monthly],
        dtype=float,
    )
    valid_sigma = np.isfinite(sigmas)
    weighted_variance = np.convolve(
        np.where(valid_sigma, sigmas**2, 0.0), raw_weights, mode="same"
    )
    available_weight = np.convolve(valid_sigma.astype(float), raw_weights, mode="same")
    with np.errstate(invalid="ignore", divide="ignore"):
        smoothed_sigma = np.sqrt(weighted_variance / available_weight)
    smoothed_sigma[available_weight < raw_weights.sum()] = np.nan
    smoothed[:6] = np.nan
    smoothed[-6:] = np.nan
    smoothed_sigma[:6] = np.nan
    smoothed_sigma[-6:] = np.nan
    search_indices = np.asarray(
        [
            index
            for index, (year, _month, _value, _sigma, _count) in enumerate(monthly)
            if year >= 1895 and np.isfinite(smoothed[index])
        ]
    )
    local_minima, _ = find_peaks(
        -smoothed[search_indices], distance=8 * 12, prominence=5
    )
    minima = search_indices[local_minima].tolist()
    if not minima or not 1901 <= monthly[minima[0]][0] <= 1903:
        raise RuntimeError(
            "detected cycle minima do not match the SILSO cycle-14 anchor"
        )

    polar_lines = [
        line
        for line in polar_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    polar_reader = csv.DictReader(io.StringIO("\n".join(polar_lines)))
    observations: dict[str, list[tuple[float, float, float | None, str]]] = {
        "north": [],
        "south": [],
    }
    for row in polar_reader:
        for hemisphere, prefix in (("north", "N"), ("south", "S")):
            for source in ("MWO", "WSO"):
                date_value = _parse_number(
                    str(row.get(f"{prefix} {source} Date") or "")
                )
                field_value = _parse_number(
                    str(row.get(f"{prefix} {source} PField") or "")
                )
                sem_value = _parse_number(str(row.get(f"{prefix} {source} SEM") or ""))
                if date_value is not None and field_value is not None:
                    observations[hemisphere].append(
                        (date_value, field_value, sem_value, source)
                    )
    for values_by_pole in observations.values():
        values_by_pole.sort(key=lambda item: item[0])

    def window_mean(hemisphere: str, center: float) -> dict[str, object] | None:
        eligible: list[tuple[float, float, float | None, str]] = []
        for source in ("WSO", "MWO"):
            eligible = [
                item
                for item in observations[hemisphere]
                if item[3] == source and center - 0.5 <= item[0] <= center + 0.5
            ]
            if eligible:
                break
        window_complete = bool(eligible)
        fallback = "none"
        if not eligible:
            for source in ("WSO", "MWO"):
                prior = [
                    item
                    for item in observations[hemisphere]
                    if item[3] == source
                    and item[0] <= center
                    and center - item[0] <= 1.5
                ]
                if prior:
                    eligible = [prior[-1]]
                    fallback = "latest_preminimum_within_1.5_years"
                    break
        if not eligible:
            return None
        field_mean = sum(abs(item[1]) for item in eligible) / len(eligible)
        sem_values = [item[2] for item in eligible]
        propagated_sem = (
            math.sqrt(sum(float(value) ** 2 for value in sem_values)) / len(eligible)
            if all(value is not None for value in sem_values)
            else None
        )
        dates = [item[0] for item in eligible]
        return {
            "field_mean": field_mean,
            "sem": propagated_sem,
            "measurement_date": sum(dates) / len(dates),
            "date_start": min(dates),
            "date_end": max(dates),
            "count": len(eligible) if window_complete else 0,
            "source": "+".join(sorted({item[3] for item in eligible})),
            "window_complete": window_complete,
            "fallback": fallback,
        }

    def minimum_sensitivity_interval(
        previous_minimum: int, minimum: int, next_minimum: int
    ) -> tuple[int, int]:
        """Return the contiguous minimum basin compatible with SILSO dispersion.

        The official smoothed-series sigma is an observational dispersion, not
        a confidence interval.  We therefore expose a deterministic sensitivity
        interval: adjacent months remain in the basin while their lower
        one-sigma envelope overlaps the nominal minimum's upper envelope.  The
        search is bounded by the neighboring cycle maxima.
        """

        left_peak = previous_minimum + int(
            np.nanargmax(smoothed[previous_minimum:minimum])
        )
        right_peak = minimum + int(np.nanargmax(smoothed[minimum:next_minimum]))
        minimum_sigma = float(smoothed_sigma[minimum])
        if not math.isfinite(minimum_sigma):
            return minimum, minimum
        upper_envelope = float(smoothed[minimum]) + minimum_sigma

        def compatible(index: int) -> bool:
            sigma = float(smoothed_sigma[index])
            return (
                math.isfinite(sigma)
                and float(smoothed[index]) - sigma <= upper_envelope
            )

        start = minimum
        while start > left_peak and compatible(start - 1):
            start -= 1
        end = minimum
        while end < right_peak and compatible(end + 1):
            end += 1
        return start, end

    boundary_index = minima[0]
    boundary_year, boundary_month, *_rest = monthly[boundary_index]
    boundary_end = minima[1]
    boundary_peak_index = boundary_index + int(
        np.nanargmax(smoothed[boundary_index:boundary_end])
    )
    (
        boundary_peak_year,
        boundary_peak_month,
        _boundary_peak_value,
        _boundary_peak_sigma,
        boundary_peak_count,
    ) = monthly[boundary_peak_index]
    boundary_peak_sigma = float(smoothed_sigma[boundary_peak_index])
    result: list[dict[str, object]] = [
        {
            "row_role": "boundary",
            "cycle_number": 14,
            "minimum_date": f"{boundary_year:04d}-{boundary_month:02d}",
            "minimum_smoothed_sunspot_number": None,
            "maximum_date": (f"{boundary_peak_year:04d}-{boundary_peak_month:02d}"),
            "peak_smoothed_sunspot_number": round(
                float(smoothed[boundary_peak_index]), 6
            ),
            "peak_smoothed_sunspot_number_sigma": (
                round(boundary_peak_sigma, 6)
                if math.isfinite(boundary_peak_sigma)
                else None
            ),
            "peak_center_month_observation_count": boundary_peak_count,
            "minimum_date_sensitivity_start": None,
            "minimum_date_sensitivity_end": None,
            "minimum_date_sensitivity_span_months": None,
            "polar_field_proxy_gauss": None,
            "polar_field_proxy_sem_gauss": None,
            "north_polar_field_abs_gauss": None,
            "south_polar_field_abs_gauss": None,
            "predictor_window_complete": None,
            "predictor_fallback": None,
            "predictor_window_start_decimal_year": None,
            "predictor_window_end_decimal_year": None,
            "north_window_observation_count": None,
            "south_window_observation_count": None,
            "north_measurement_date_start": None,
            "north_measurement_date_end": None,
            "north_measurement_date": None,
            "north_source": None,
            "south_measurement_date_start": None,
            "south_measurement_date_end": None,
            "south_measurement_date": None,
            "south_source": None,
            "predictor_cutoff_decimal_year": None,
        }
    ]
    for ordinal, (start, end) in enumerate(itertools.pairwise(minima)):
        cycle_number = 14 + ordinal
        if not 15 <= cycle_number <= 24:
            continue
        start_year, start_month, *_rest = monthly[start]
        center = start_year + (start_month - 0.5) / 12.0
        window_start = center - 0.5
        window_end = center + 0.5
        north = window_mean("north", center)
        south = window_mean("south", center)
        if north is None or south is None:
            continue
        peak_offset = int(np.nanargmax(smoothed[start:end]))
        peak_index = start + peak_offset
        peak_year, peak_month, _peak_value, _peak_sigma, peak_count = monthly[
            peak_index
        ]
        north_sem = north["sem"]
        south_sem = south["sem"]
        proxy_sem = (
            math.sqrt(float(north_sem) ** 2 + float(south_sem) ** 2) / 2
            if north_sem is not None and south_sem is not None
            else None
        )
        sensitivity_start, sensitivity_end = minimum_sensitivity_interval(
            minima[ordinal - 1], start, end
        )
        sensitivity_start_year, sensitivity_start_month, *_ = monthly[sensitivity_start]
        sensitivity_end_year, sensitivity_end_month, *_ = monthly[sensitivity_end]
        result.append(
            {
                "row_role": "analysis",
                "cycle_number": cycle_number,
                "minimum_date": f"{start_year:04d}-{start_month:02d}",
                "minimum_smoothed_sunspot_number": round(float(smoothed[start]), 6),
                "maximum_date": f"{peak_year:04d}-{peak_month:02d}",
                "peak_smoothed_sunspot_number": round(float(smoothed[peak_index]), 6),
                "peak_smoothed_sunspot_number_sigma": round(
                    float(smoothed_sigma[peak_index]), 6
                ),
                "peak_center_month_observation_count": peak_count,
                "minimum_date_sensitivity_start": (
                    f"{sensitivity_start_year:04d}-{sensitivity_start_month:02d}"
                ),
                "minimum_date_sensitivity_end": (
                    f"{sensitivity_end_year:04d}-{sensitivity_end_month:02d}"
                ),
                "minimum_date_sensitivity_span_months": (
                    sensitivity_end - sensitivity_start
                ),
                "polar_field_proxy_gauss": round(
                    (float(north["field_mean"]) + float(south["field_mean"])) / 2,
                    6,
                ),
                "polar_field_proxy_sem_gauss": (
                    round(proxy_sem, 6) if proxy_sem is not None else None
                ),
                "north_polar_field_abs_gauss": round(float(north["field_mean"]), 6),
                "south_polar_field_abs_gauss": round(float(south["field_mean"]), 6),
                "predictor_window_complete": bool(
                    north["window_complete"] and south["window_complete"]
                ),
                "predictor_fallback": (
                    "none"
                    if north["fallback"] == south["fallback"] == "none"
                    else "latest_preminimum_within_1.5_years"
                ),
                "predictor_window_start_decimal_year": round(window_start, 6),
                "predictor_window_end_decimal_year": round(window_end, 6),
                "north_window_observation_count": north["count"],
                "south_window_observation_count": south["count"],
                "north_measurement_date_start": north["date_start"],
                "north_measurement_date_end": north["date_end"],
                "north_measurement_date": round(float(north["measurement_date"]), 6),
                "north_source": north["source"],
                "south_measurement_date_start": south["date_start"],
                "south_measurement_date_end": south["date_end"],
                "south_measurement_date": round(float(south["measurement_date"]), 6),
                "south_source": south["source"],
                "predictor_cutoff_decimal_year": round(window_end, 6),
            }
        )
    if [row["cycle_number"] for row in result] != list(range(14, 25)):
        raise RuntimeError(
            "curated inputs did not yield cycle-14 boundary plus cycles 15 through 24"
        )
    return result


_SOLAR_PRECURSOR_COLUMN_SCHEMA = [
    {"name": "row_role", "type": "string", "nullable": False},
    {"name": "cycle_number", "type": "integer", "nullable": False},
    {"name": "minimum_date", "type": "year_month", "nullable": False},
    {
        "name": "minimum_smoothed_sunspot_number",
        "type": "number",
        "nullable": True,
    },
    {"name": "maximum_date", "type": "year_month", "nullable": True},
    {
        "name": "peak_smoothed_sunspot_number",
        "type": "number",
        "nullable": True,
    },
    {
        "name": "peak_smoothed_sunspot_number_sigma",
        "type": "number",
        "nullable": True,
    },
    {
        "name": "peak_center_month_observation_count",
        "type": "integer",
        "nullable": True,
    },
    {
        "name": "minimum_date_sensitivity_start",
        "type": "year_month",
        "nullable": True,
    },
    {
        "name": "minimum_date_sensitivity_end",
        "type": "year_month",
        "nullable": True,
    },
    {
        "name": "minimum_date_sensitivity_span_months",
        "type": "integer",
        "nullable": True,
    },
    {"name": "polar_field_proxy_gauss", "type": "number", "nullable": True},
    {
        "name": "polar_field_proxy_sem_gauss",
        "type": "number",
        "nullable": True,
    },
    {
        "name": "north_polar_field_abs_gauss",
        "type": "number",
        "nullable": True,
    },
    {
        "name": "south_polar_field_abs_gauss",
        "type": "number",
        "nullable": True,
    },
    {
        "name": "predictor_window_complete",
        "type": "boolean",
        "nullable": True,
    },
    {"name": "predictor_fallback", "type": "string", "nullable": True},
    {
        "name": "predictor_window_start_decimal_year",
        "type": "decimal_year",
        "nullable": True,
    },
    {
        "name": "predictor_window_end_decimal_year",
        "type": "decimal_year",
        "nullable": True,
    },
    {
        "name": "north_window_observation_count",
        "type": "integer",
        "nullable": True,
    },
    {
        "name": "south_window_observation_count",
        "type": "integer",
        "nullable": True,
    },
    {
        "name": "north_measurement_date_start",
        "type": "decimal_year",
        "nullable": True,
    },
    {
        "name": "north_measurement_date_end",
        "type": "decimal_year",
        "nullable": True,
    },
    {"name": "north_measurement_date", "type": "decimal_year", "nullable": True},
    {"name": "north_source", "type": "string", "nullable": True},
    {
        "name": "south_measurement_date_start",
        "type": "decimal_year",
        "nullable": True,
    },
    {
        "name": "south_measurement_date_end",
        "type": "decimal_year",
        "nullable": True,
    },
    {"name": "south_measurement_date", "type": "decimal_year", "nullable": True},
    {"name": "south_source", "type": "string", "nullable": True},
    {
        "name": "predictor_cutoff_decimal_year",
        "type": "decimal_year",
        "nullable": True,
    },
]
_SOLAR_REQUESTED_PAIR_IDS = [f"{cycle}->{cycle + 1}" for cycle in range(14, 24)]
_SOLAR_TEMPORAL_ORDERING_RULE = (
    "For each N->N+1 pair: start minimum < all polar-window observations <= "
    "prediction issue cutoff at ending minimum plus six months < target peak "
    "< target availability."
)
_SOLAR_PRECURSOR_GAPS: list[dict[str, object]] = []


def _required_dataset_ids_for_protocol(analysis_protocol: str) -> list[str]:
    from jw.research_protocols import required_dataset_ids_for_protocol

    return list(required_dataset_ids_for_protocol(analysis_protocol))


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def open_bounded_solar_data_context(
    config: RunnableConfig | None,
    *,
    analysis_protocol: str = "none",
) -> dict[str, object]:
    """Open a plan-free, hash-bound context for one bounded Data request."""

    from jw.research_protocols import required_data_product_for_protocol
    from jw.research_review import store_from_config

    root = workspace_root_from_config(config)
    store = store_from_config(config)
    task_path = root / "task.json"
    manifest_path = root / "input_manifest.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(task, dict) or not isinstance(manifest, dict):
        raise RuntimeError("bounded data task metadata is not an object")
    if task.get("thread_id") != store.task_id:
        raise RuntimeError("bounded data task metadata is not bound to this task")

    eligible_inputs = _eligible_input_records(config)
    required_ids = _required_dataset_ids_for_protocol(analysis_protocol)
    available_ids = {
        str(item.get("dataset_id"))
        for item in eligible_inputs
        if isinstance(item.get("dataset_id"), str)
    }
    missing_ids = [value for value in required_ids if value not in available_ids]
    status = (
        "input_missing"
        if missing_ids or (not eligible_inputs and not required_ids)
        else "inputs_available"
    )
    must_stop = status == "input_missing"
    body: dict[str, object] = {
        "schema_version": "solar-data-context-v1",
        "context_mode": "bounded_data",
        "task_id": store.task_id,
        "analysis_protocol": analysis_protocol,
        "required_data_product": required_data_product_for_protocol(analysis_protocol),
        "task_sha256": _file_sha256(task_path),
        "research_question_sha256": hashlib.sha256(
            str(task.get("research_question") or "").encode("utf-8")
        ).hexdigest(),
        "planning_artifact_ref": None,
        "planning_verdict_ref": None,
        "plan_source_ref": None,
        "plan_sha256": None,
        "input_manifest_sha256": _file_sha256(manifest_path),
        "required_dataset_ids": required_ids,
        "missing_required_dataset_ids": missing_ids,
        "required_datasets": [],
        "data_steps": [],
        "planned_outputs": [],
        "eligible_inputs": eligible_inputs,
        "status": status,
        "must_stop": must_stop,
    }
    digest = _canonical_sha256(body)
    receipt: dict[str, object] = {
        **body,
        "context_sha256": digest,
        "created_at": datetime.now(UTC).isoformat(),
        "path_policy": (
            "Only eligible_inputs may be passed to audit or feature tools; "
            "never guess /project/data, /inputs, /skills, or prior-run paths."
        ),
    }
    relative_path = Path("receipts") / "datasets" / f"data-context-{digest[:16]}.json"
    receipt_path = root / relative_path
    if not receipt_path.exists():
        _atomic_write_json(receipt_path, receipt)
    else:
        loaded = json.loads(receipt_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            receipt = loaded
    receipt.update(
        {
            "receipt_ref": relative_path.as_posix(),
            "must_stop": must_stop,
            "instruction": (
                "Required registered datasets are missing: "
                + ", ".join(missing_ids)
                + ". Return input_missing now; do not fabricate or download "
                "unregistered evidence."
                if missing_ids
                else (
                    "No eligible immutable data is bound. Return input_missing now; "
                    "do not search guessed paths or fabricate an output."
                    if must_stop
                    else "Use only eligible_inputs and persist at least one "
                    "additional task-local data artifact before returning."
                )
            ),
        }
    )
    return receipt


@tool(parse_docstring=True)
def solar_data_open_context(
    analysis_protocol: str = "none", config: RunnableConfig = None
) -> str:
    """Open the accepted plan and task-bound data manifest before data work.

    This is the mandatory first action for a closed-loop Data stage. It returns
    only immutable inputs declared by the task workspace; repository samples,
    guessed paths, prior-run outputs, and synthetic fixtures are never promoted
    into the current research run implicitly.

    Args:
        analysis_protocol: Supervisor-selected stable analysis protocol.
        config: Runtime-injected task workspace configuration.

    Returns:
        Hash-bound plan requirements, eligible input records, data route steps,
        planned outputs, and an immutable context receipt path.
    """

    try:
        from jw.research_protocols import (
            detect_analysis_protocol,
            required_data_product_for_protocol,
            resolve_required_dataset_ids,
        )
        from jw.research_review import store_from_config

        root = workspace_root_from_config(config)
        store = store_from_config(config)
        planning = store.latest_artifact("planning")
        if planning is None:
            raise RuntimeError("no planning artifact exists for the Data stage")
        verdict = store.matching_verdict("planning", [store.artifact_ref(planning)])
        if verdict is None or verdict.get("decision") not in {
            "accept",
            "accept_with_limits",
        }:
            raise RuntimeError("the latest planning artifact is not accepted")

        manifest = planning.get("payload", {}).get("source_manifest", [])
        plan_ref = next(
            (
                str(item["source_ref"])
                for item in manifest
                if isinstance(item, dict)
                and isinstance(item.get("source_ref"), str)
                and str(item["source_ref"]).endswith("/research_plan.json")
            ),
            "",
        )
        if not plan_ref:
            raise RuntimeError("accepted planning artifact has no canonical plan")
        plan_path = (root / plan_ref).resolve()
        if not plan_path.is_relative_to(root) or not plan_path.is_file():
            raise RuntimeError(
                "canonical planning source is outside the task workspace"
            )
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        if not isinstance(plan, dict):
            raise RuntimeError("canonical research plan is not an object")

        task_path = root / "task.json"
        task = json.loads(task_path.read_text(encoding="utf-8"))
        if not isinstance(task, dict) or task.get("thread_id") != store.task_id:
            raise RuntimeError("task metadata is not bound to the current Data task")
        research_question = str(task.get("research_question") or "")
        expected_protocol = detect_analysis_protocol(research_question)
        if analysis_protocol != expected_protocol:
            raise ValueError(
                "Data semantics conflict: analysis protocol does not match the "
                "task-bound research question"
            )
        input_manifest_path = root / "input_manifest.json"
        input_manifest = json.loads(input_manifest_path.read_text(encoding="utf-8"))
        if not isinstance(input_manifest, dict):
            raise RuntimeError("task input manifest is not an object")
        eligible_inputs = _eligible_input_records(config)
        required_dataset_ids = list(
            resolve_required_dataset_ids(plan, analysis_protocol)
        )
        available_dataset_ids = {
            str(item.get("dataset_id"))
            for item in eligible_inputs
            if isinstance(item.get("dataset_id"), str)
        }
        missing_required_dataset_ids = [
            dataset_id
            for dataset_id in required_dataset_ids
            if dataset_id not in available_dataset_ids
        ]
        must_stop = bool(missing_required_dataset_ids) or (
            not eligible_inputs and not required_dataset_ids
        )

        route = plan.get("research_route", [])
        data_steps = [
            item
            for item in route
            if isinstance(item, dict) and item.get("stage") == "data"
        ]
        producer_step_ids = {
            str(item.get("id"))
            for item in data_steps
            if isinstance(item.get("id"), str)
        }
        planned_outputs = [
            item
            for item in plan.get("research_artifacts", [])
            if isinstance(item, dict)
            and item.get("producer_step_id") in producer_step_ids
        ]
        body = {
            "schema_version": "solar-data-context-v1",
            "context_mode": "full_research",
            "task_id": store.task_id,
            "analysis_protocol": analysis_protocol,
            "required_data_product": required_data_product_for_protocol(
                analysis_protocol
            ),
            "planning_artifact_ref": store.artifact_ref(planning),
            "planning_verdict_ref": {
                "review_id": verdict["review_id"],
                "verdict_sha256": verdict["verdict_sha256"],
            },
            "plan_source_ref": plan_ref,
            "plan_sha256": next(
                (
                    item.get("sha256")
                    for item in manifest
                    if isinstance(item, dict) and item.get("source_ref") == plan_ref
                ),
                None,
            ),
            "task_sha256": hashlib.sha256(task_path.read_bytes()).hexdigest(),
            "research_question_sha256": hashlib.sha256(
                str(task.get("research_question") or "").encode("utf-8")
            ).hexdigest(),
            "input_manifest_sha256": hashlib.sha256(
                input_manifest_path.read_bytes()
            ).hexdigest(),
            "required_datasets": plan.get("required_datasets", []),
            "required_dataset_ids": required_dataset_ids,
            "missing_required_dataset_ids": missing_required_dataset_ids,
            "data_steps": data_steps,
            "planned_outputs": planned_outputs,
            "eligible_inputs": eligible_inputs,
            "status": "input_missing" if must_stop else "inputs_available",
            "must_stop": must_stop,
        }
        digest = _canonical_sha256(body)
        receipt = {
            **body,
            "context_sha256": digest,
            "created_at": datetime.now(UTC).isoformat(),
            "path_policy": (
                "Only eligible_inputs may be passed to audit or feature tools; "
                "never guess /project/data, /inputs, /skills, or prior-run paths."
            ),
        }
        relative_path = (
            Path("receipts") / "datasets" / f"data-context-{digest[:16]}.json"
        )
        receipt_path = root / relative_path
        if not receipt_path.exists():
            _atomic_write_json(receipt_path, receipt)
        else:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        return _to_json(
            {
                **receipt,
                "receipt_ref": relative_path.as_posix(),
                "instruction": (
                    "Audit only the returned eligible_inputs and persist their "
                    "verified semantics before engineering features."
                    if not must_stop
                    else (
                        "Required registered datasets are missing: "
                        + ", ".join(missing_required_dataset_ids)
                        + ". Return input_missing now; do not fabricate or download "
                        "unregistered evidence."
                        if missing_required_dataset_ids
                        else "No eligible immutable data is bound. Return input_missing "
                        "now; do not search guessed paths or fabricate an output."
                    )
                ),
            }
        )
    except Exception as exc:
        return _error_json("solar_data_open_context", exc)


@tool(parse_docstring=True)
def audit_solar_data_quality(csv_path: str, config: RunnableConfig = None) -> str:
    """Audit data quality of a solar physics CSV file (read-only).

    Skill: solar-cycle / audit-solar-data

    Use this tool to inspect a solar dataset before any processing.  It checks
    data quality, detects time columns, computes summary statistics, and
    identifies critical issues — without writing any files.

    This should be the FIRST step in any solar data workflow.  Always audit
    before attempting feature engineering or experiment preparation.

    Args:
        csv_path: Path to the CSV file to audit (absolute or relative).

    Returns:
        JSON string with keys: status, path, input_fingerprint, inspection,
        statistics, quality_report, critical_issues, warnings.
    """
    warnings.filterwarnings("ignore")
    try:
        from solar_feature_agent.workflows import EphemeralSession, audit_solar_data

        session = EphemeralSession()
        source = _resolve_eligible_data_path(csv_path, config)
        result = audit_solar_data(str(source), session=session)
        return _to_json(result)
    except Exception as exc:
        return _error_json("audit_solar_data_quality", exc)


@tool(parse_docstring=True)
def engineer_solar_features(csv_path: str, config: RunnableConfig = None) -> str:
    """Engineer features from a solar physics CSV dataset (write operation).

    Skill: solar-cycle / engineer-solar-features

    Use this tool after auditing data quality to generate derived features
    (rolling statistics, cycle-phase indicators, flare indices, etc.) from
    solar observation data.  The tool ingests the CSV, runs a quality audit,
    and then engineers features in a single call.

    Prerequisites: the dataset should pass the quality audit (run
    ``audit_solar_data_quality`` first to check).  The CSV must contain a
    detectable time column.

    Args:
        csv_path: Path to the CSV file to process.

    Returns:
        JSON string with keys: status, feature_result, artifacts.
    """
    warnings.filterwarnings("ignore")
    try:
        from solar_feature_agent.workflows import (
            audit_solar_data,
            ingest_align_solar_data,
        )
        from solar_feature_agent.workflows import (
            engineer_solar_features as _engineer,
        )

        source = _resolve_eligible_data_path(csv_path, config)
        session = _task_chat_session(config)
        ingest_align_solar_data([str(source)], session=session)
        audit_solar_data(str(source), session=session)
        result = _engineer(session)
        return _to_json(result)
    except Exception as exc:
        return _error_json("engineer_solar_features", exc)


@tool(parse_docstring=True)
def prepare_solar_experiment(csv_path: str, config: RunnableConfig = None) -> str:
    """Prepare a full solar experiment from a CSV dataset (write operation).

    Skill: solar-cycle / prepare-experiment-handoff

    Use this tool to run the complete solar feature pipeline end-to-end:
    ingest the CSV, audit quality, engineer features, and produce an
    experiment handoff with an LLM strategy recommendation.  This is the
    all-in-one entry point for preparing solar data for machine-learning
    experiments.

    If feature engineering fails (e.g. critical quality issues), the tool
    returns the failure result without attempting the handoff.

    Args:
        csv_path: Path to the CSV file to process.

    Returns:
        JSON string with keys: status, handoff, strategy, artifacts.
    """
    warnings.filterwarnings("ignore")
    try:
        from solar_feature_agent.workflows import (
            audit_solar_data,
            ingest_align_solar_data,
            prepare_experiment_handoff,
        )
        from solar_feature_agent.workflows import (
            engineer_solar_features as _engineer,
        )

        source = _resolve_eligible_data_path(csv_path, config)
        session = _task_chat_session(config)
        ingest_align_solar_data([str(source)], session=session)
        audit_solar_data(str(source), session=session)
        features_result = _engineer(session)
        if features_result.get("status") != "ok":
            return _to_json(features_result)
        result = prepare_experiment_handoff(session)
        return _to_json(result)
    except Exception as exc:
        return _error_json("prepare_solar_experiment", exc)


@tool(parse_docstring=True)
def dataset_statistics(
    csv_path: str, columns: str = "", config: RunnableConfig = None
) -> str:
    """Compute descriptive statistics for a solar CSV dataset (read-only).

    Skill: solar-cycle / dataset-statistics

    Use this tool to get summary statistics (mean, std, min, max, quartiles,
    null counts, unique counts, inferred types, field metadata) for columns
    in a solar dataset.  Optionally specify which columns to describe; leave
    ``columns`` empty to describe all columns.

    Args:
        csv_path: Path to the CSV file.
        columns: Comma-separated column names to describe
            (e.g. ``"sunspot_number,f10.7"``).  Leave empty for all columns.

    Returns:
        JSON string with descriptive statistics for the requested columns.
    """
    warnings.filterwarnings("ignore")
    try:
        from dataset_stats_engine import describe
        from solar_feature_agent.workflows import EphemeralSession, _inspection_wrapper
        from upload_inspector import inspect_csv

        path = _resolve_eligible_data_path(csv_path, config)
        inspection = inspect_csv(path)
        session = EphemeralSession()
        session.set_current_dataset(str(path), _inspection_wrapper(path, inspection))
        result = describe(session)

        # Filter to requested columns if specified.
        if columns.strip():
            col_names = {c.strip() for c in columns.split(",") if c.strip()}
            result["columns"] = [
                c for c in result.get("columns", []) if c.get("column") in col_names
            ]

        return _to_json(result)
    except Exception as exc:
        return _error_json("dataset_statistics", exc)


@tool(parse_docstring=True)
def prepare_solar_precursor_cycle_table(
    sunspot_path: str,
    polar_field_path: str,
    config: RunnableConfig = None,
) -> str:
    """Create the verified per-cycle table for polar-precursor evaluation.

    This deterministic adapter is limited to the curated SILSO monthly-total
    series and the MWO/WSO calibrated polar-field series. It computes the
    official 13-month tapered centered smoother for retrospective cycle labels,
    binds each polar predictor strictly at or before the nominal cycle minimum,
    and records the six-month label-confirmation caveat explicitly.

    Args:
        sunspot_path: Eligible input with dataset_id silso-monthly-total-v2.
        polar_field_path: Eligible input with dataset_id mwo-wso-polar-field-v2.
        config: Runtime-injected task workspace configuration.

    Returns:
        Hash-bound feature-table and semantic-receipt paths for cycles 15-24.
    """

    try:
        records = _eligible_input_records(config)
        by_path = {str(item["path"]): item for item in records}
        sunspot_record = by_path.get(sunspot_path.strip())
        polar_record = by_path.get(polar_field_path.strip())
        if not (
            sunspot_record
            and sunspot_record.get("dataset_id") == "silso-monthly-total-v2"
        ):
            raise PermissionError("sunspot_path is not the curated SILSO input")
        if not (
            polar_record and polar_record.get("dataset_id") == "mwo-wso-polar-field-v2"
        ):
            raise PermissionError("polar_field_path is not the curated MWO/WSO input")
        sunspot = _resolve_eligible_data_path(sunspot_path, config)
        polar = _resolve_eligible_data_path(polar_field_path, config)
        rows = _build_solar_precursor_cycle_rows(sunspot, polar)

        root = workspace_root_from_config(config)
        table_ref = "work/solar_data/solar_precursor_cycle_features.csv"
        metadata_ref = "receipts/datasets/solar_precursor_cycle_table.json"
        table_path = root / table_ref
        metadata_path = root / metadata_ref
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        _atomic_write_text(table_path, buffer.getvalue())
        cycle_numbers = [int(row["cycle_number"]) for row in rows]
        available_pairs = [
            pair_id
            for pair_id in _SOLAR_REQUESTED_PAIR_IDS
            if all(
                cycle in cycle_numbers
                for cycle in (int(pair_id.split("->")[0]), int(pair_id.split("->")[1]))
            )
        ]
        incomplete_window_cycles = [
            int(row["cycle_number"])
            for row in rows
            if row.get("row_role") == "analysis"
            and row.get("predictor_window_complete") is not True
        ]
        gaps = [dict(item) for item in _SOLAR_PRECURSOR_GAPS]
        if incomplete_window_cycles:
            gaps.append(
                {
                    "code": "PREDICTOR_WINDOW_PARTIAL_COVERAGE",
                    "status": "limited",
                    "cycle_numbers": incomplete_window_cycles,
                    "fallback": "latest_preminimum_within_1.5_years",
                    "details": (
                        "At least one hemisphere has no observation inside the "
                        "plus/minus six-month window for these minima. The "
                        "declared preminimum fallback preserves temporal order."
                    ),
                }
            )
        analysis_rows = [row for row in rows if row.get("row_role") == "analysis"]
        feature_records = [
            validate_precursor_feature_record(
                {
                    "schema_version": "solar-precursor-feature-record-v1",
                    "feature_id": f"polar-minimum-cycle-{row['cycle_number']}",
                    "hypothesis_id": "h2_polar_precursor",
                    "forecast_origin": str(row["predictor_window_end_decimal_year"]),
                    "observable_kind": "polar_aperture_field",
                    "physical_quantity": (
                        "mean absolute north/south calibrated polar field"
                    ),
                    "unit": "gauss",
                    "source_dataset_ids": ["mwo-wso-polar-field-v2"],
                    "source_artifact_ids": [table_ref, metadata_ref],
                    "observation_start": str(
                        row["predictor_window_start_decimal_year"]
                    ),
                    "observation_end": str(row["predictor_window_end_decimal_year"]),
                    "available_at": str(row["predictor_cutoff_decimal_year"]),
                    "cycle_id": int(row["cycle_number"]) - 1,
                    "target_cycle_id": int(row["cycle_number"]),
                    "value": float(row["polar_field_proxy_gauss"]),
                    "uncertainty": row["polar_field_proxy_sem_gauss"],
                    "measurement_regime": "+".join(
                        sorted(
                            {
                                str(row["north_source"]),
                                str(row["south_source"]),
                            }
                        )
                    ),
                    "derivation_method": (
                        "mean absolute calibrated north/south polar aperture field"
                    ),
                    "source_kind": "polar_aperture_observation",
                    "status": "available",
                }
            )
            for row in analysis_rows
        ]
        if not feature_records:
            raise RuntimeError("precursor table has no analysis feature records")
        first_feature = feature_records[0]
        unavailable_feature_records = [
            validate_precursor_feature_record(
                {
                    "schema_version": "solar-precursor-feature-record-v1",
                    "feature_id": "axial-dipole-cycle-minimum-unavailable",
                    "hypothesis_id": "h3_axial_dipole_discriminator",
                    "forecast_origin": first_feature["forecast_origin"],
                    "observable_kind": "axial_dipole_moment",
                    "physical_quantity": "axial dipole moment near cycle minimum",
                    "unit": "gauss",
                    "source_dataset_ids": [],
                    "source_artifact_ids": [],
                    "observation_start": first_feature["observation_start"],
                    "observation_end": feature_records[-1]["observation_end"],
                    "available_at": first_feature["available_at"],
                    "cycle_id": int(first_feature["cycle_id"]),
                    "target_cycle_id": int(first_feature["target_cycle_id"]),
                    "value": None,
                    "uncertainty": None,
                    "measurement_regime": "not_available",
                    "derivation_method": (
                        "not computed; registered axial-dipole product or "
                        "registered synoptic-map harmonic required"
                    ),
                    "source_kind": "missing",
                    "status": "blocked_by_data",
                    "data_gap": ("NO_REGISTERED_AXIAL_DIPOLE_OR_SYNOPTIC_MAP_INPUT"),
                }
            )
        ]
        receipt = {
            "schema_version": "solar-precursor-cycle-table-v2",
            "receipt_type": "solar_precursor_cycle_table",
            "status": "verified",
            "producer": "solar-data",
            "task_id": _validated_task_metadata(config)[1],
            "input_refs": [
                {
                    "path": sunspot_record["path"],
                    "dataset_id": sunspot_record["dataset_id"],
                    "sha256": sunspot_record["sha256"],
                    "provenance_ref": sunspot_record.get("provenance_ref"),
                },
                {
                    "path": polar_record["path"],
                    "dataset_id": polar_record["dataset_id"],
                    "sha256": polar_record["sha256"],
                    "provenance_ref": polar_record.get("provenance_ref"),
                },
            ],
            "dataset_ids": [
                "silso-monthly-total-v2",
                "mwo-wso-polar-field-v2",
            ],
            "column_schema": _SOLAR_PRECURSOR_COLUMN_SCHEMA,
            "units": {
                "minimum_smoothed_sunspot_number": "international_sunspot_number",
                "peak_smoothed_sunspot_number": "international_sunspot_number",
                "peak_smoothed_sunspot_number_sigma": ("international_sunspot_number"),
                "minimum_date_sensitivity_span_months": "month",
                "polar_field_proxy_gauss": "gauss",
                "polar_field_proxy_sem_gauss": "gauss",
                "north_polar_field_abs_gauss": "gauss",
                "south_polar_field_abs_gauss": "gauss",
                "predictor_window_start_decimal_year": "decimal_year",
                "predictor_window_end_decimal_year": "decimal_year",
                "north_measurement_date": "decimal_year",
                "south_measurement_date": "decimal_year",
                "predictor_cutoff_decimal_year": "decimal_year",
            },
            "sign_convention": {
                "polar_field_proxy_gauss": (
                    "unsigned non-negative magnitude: mean absolute north/south field"
                ),
                "north_polar_field_abs_gauss": "unsigned north polar-field magnitude",
                "south_polar_field_abs_gauss": "unsigned south polar-field magnitude",
                "basis": (
                    "The source north/south signs encode polarity; this product uses "
                    "their absolute magnitudes for the precursor proxy."
                ),
            },
            "method": {
                "cycle_label_smoothing": (
                    "centered 13-month tapered boxcar, endpoint weights 0.5, "
                    "normalization 1/12"
                ),
                "cycle_minimum_detection": (
                    "local minima at least 8 years apart with prominence 5; "
                    "cycle 14 anchored to the detected 1902 minimum"
                ),
                "predictor": (
                    "arithmetic mean of the absolute north/south calibrated polar-"
                    "field means, where each hemisphere mean uses all observations "
                    "from one preferred source within plus/minus 6 months of the "
                    "nominal minimum; gauss is the measurement unit and does not "
                    "denote Gaussian weighting; the predictor is available at the "
                    "six-month prediction issue date; "
                    "when one hemisphere has no in-window observation, use its "
                    "latest preminimum value no older than 1.5 years and flag "
                    "that cycle explicitly"
                ),
                "predictor_uncertainty": (
                    "within each hemisphere, propagate reported observation SEMs "
                    "as sqrt(sum(sem_i^2)) / n; combine the north and south "
                    "hemisphere means as sqrt(north_sem^2 + south_sem^2) / 2. "
                    "This propagated SEM convention is not a calibrated confidence "
                    "interval"
                ),
                "target": "maximum centered-smoothed sunspot number before next minimum",
                "target_uncertainty": (
                    "SILSO 13-month smoothed observational sigma at the selected "
                    "peak, computed as the square root of the weighted mean of "
                    "the 13 monthly variances"
                ),
                "minimum_date_uncertainty": (
                    "contiguous sensitivity basin whose lower one-sigma envelope "
                    "overlaps the nominal minimum's upper envelope, bounded by "
                    "neighboring cycle maxima; this is not a confidence interval"
                ),
                "uncertainty_source": "https://www.sidc.be/SILSO/infosnmstot",
            },
            "row_count": len(rows),
            "feature_records": feature_records,
            "unavailable_feature_records": unavailable_feature_records,
            "cycle_numbers": cycle_numbers,
            "analysis_cycle_numbers": list(range(15, 25)),
            "boundary_cycle_numbers": [14],
            "pair_coverage": {
                "requested_pairs": _SOLAR_REQUESTED_PAIR_IDS,
                "available_pairs": available_pairs,
                "unavailable_pairs": sorted(
                    set(_SOLAR_REQUESTED_PAIR_IDS) - set(available_pairs)
                ),
            },
            "sample_size": {
                "independent_sample_unit": "adjacent_solar_cycle_pair",
                "independent_sample_count": len(available_pairs),
                "n_eff_upper_bound": len(available_pairs),
                "n_eff_status": "bounded_not_estimated",
                "dependence_note": (
                    "Adjacent pairs share cycle-boundary construction and span "
                    "the MWO/WSO measurement-regime transition, so effective "
                    "sample size may be smaller than the row count."
                ),
            },
            "temporal_ordering_rule": _SOLAR_TEMPORAL_ORDERING_RULE,
            "uncertainty_fields": {
                "reported": [
                    "polar_field_proxy_sem_gauss",
                    "peak_smoothed_sunspot_number_sigma",
                    "minimum_date_sensitivity_start",
                    "minimum_date_sensitivity_end",
                    "minimum_date_sensitivity_span_months",
                ],
                "interpretation": (
                    "SILSO sigma and the minimum-date sensitivity basin quantify "
                    "observational dispersion and label sensitivity; neither is "
                    "a calibrated confidence interval because monthly values are "
                    "serially correlated."
                ),
                "not_computed": ["dependence_adjusted_n_eff"],
            },
            "gaps": gaps,
            "limitations": [
                "Centered smoothing is retrospective labeling and confirms a nominal minimum only after a six-month lag.",
                "MWO facular counts are a calibrated proxy, not direct pre-1976 magnetograph measurements.",
                "Ten completed cycle pairs remain a small dependent sample; n_eff is bounded above by 10 and must not be assumed equal to 10.",
                "The SILSO smoothed sigma and minimum-date sensitivity basin are not confidence intervals; serial correlation remains for downstream uncertainty analysis.",
            ],
            "outputs": [
                {
                    "path": table_ref,
                    "bytes": table_path.stat().st_size,
                    "sha256": _file_sha256(table_path),
                }
            ],
            "created_at": datetime.now(UTC).isoformat(),
        }
        _atomic_write_json(metadata_path, receipt)
        return _to_json(
            {
                "status": "verified",
                "artifact_refs": [table_ref],
                "receipt_refs": [metadata_ref],
                "row_count": len(rows),
                "cycle_numbers": receipt["cycle_numbers"],
                "table_sha256": receipt["outputs"][0]["sha256"],
                "sample_size": receipt["sample_size"],
                "uncertainty_fields": receipt["uncertainty_fields"],
                "gaps": receipt["gaps"],
                "limitations": receipt["limitations"],
                "feature_record_count": len(feature_records),
                "unavailable_feature_record_count": len(unavailable_feature_records),
                "hypothesis_data_status": {
                    "h2_polar_precursor": "available",
                    "h3_axial_dipole_discriminator": "blocked_by_data",
                },
            }
        )
    except Exception as exc:
        return _error_json("prepare_solar_precursor_cycle_table", exc)


@tool(parse_docstring=True)
def prepare_solar_cycle_26_readiness(
    monthly_total_path: str,
    smoothed_path: str,
    official_extrema_path: str,
    f107_path: str,
    historical_polar_path: str,
    current_polar_path: str,
    cutoff_date: str = "2026-06-30",
    config: RunnableConfig = None,
) -> str:
    """Create the verified evidence-maturity inventory for the SC26 launch gate.

    The adapter separates cycle-25 state indicators from cycle-26 precursors.
    Missing public observations remain explicit evidence gaps in a verified Data
    product instead of being misclassified as missing user input.

    Args:
        monthly_total_path: Eligible SILSO monthly-total Version 2.0 input.
        smoothed_path: Eligible SILSO 13-month-smoothed Version 2.0 input.
        official_extrema_path: Eligible official SILSO cycle extrema table.
        f107_path: Eligible NOAA SWPC monthly F10.7 input.
        historical_polar_path: Eligible historical MWO/WSO calibration input.
        current_polar_path: Eligible current WSO polar-field observations.
        cutoff_date: Evidence cutoff fixed to 2026-06-30 for this protocol.
        config: Runtime-injected task workspace configuration.

    Returns:
        Verified inventory and receipt paths with launch-readiness evidence gaps.
    """

    try:
        from jw.research_protocols import SOLAR_CYCLE_26_READINESS_PROTOCOL

        if cutoff_date != "2026-06-30":
            raise ValueError(
                "solar_cycle_26_readiness_v1 requires cutoff_date=2026-06-30"
            )
        specifications = (
            (monthly_total_path, "silso-monthly-total-v2"),
            (smoothed_path, "silso-monthly-smoothed-v2"),
            (official_extrema_path, "silso-cycle-extrema-v2"),
            (f107_path, "noaa-swpc-monthly-f107-v1"),
            (historical_polar_path, "mwo-wso-polar-field-v2"),
            (current_polar_path, "wso-current-polar-field-v1"),
        )
        resolved: list[Path] = []
        input_refs: list[dict[str, object]] = []
        for virtual_path, dataset_id in specifications:
            path, record = _resolve_eligible_dataset_path(
                virtual_path, dataset_id, config
            )
            resolved.append(path)
            input_refs.append(
                {
                    "dataset_id": dataset_id,
                    "path": record["path"],
                    "sha256": record["sha256"],
                    "provenance_ref": record.get("provenance_ref"),
                }
            )
        inventory = _build_solar_cycle_26_readiness_inventory(
            *resolved,
            cutoff_date=cutoff_date,
        )
        if inventory.get("analysis_protocol") != SOLAR_CYCLE_26_READINESS_PROTOCOL:
            raise RuntimeError("SC26 readiness inventory has the wrong protocol")

        root = workspace_root_from_config(config)
        artifact_ref = "work/solar_data/solar_cycle_26_readiness_inventory.json"
        receipt_ref = "receipts/datasets/solar_cycle_26_readiness_inventory.json"
        artifact_path = root / artifact_ref
        receipt_path = root / receipt_ref
        _atomic_write_json(artifact_path, inventory)
        output = {
            "path": artifact_ref,
            "bytes": artifact_path.stat().st_size,
            "sha256": _file_sha256(artifact_path),
        }
        receipt = {
            "schema_version": "solar-cycle-26-readiness-receipt-v1",
            "receipt_type": "solar_cycle_26_readiness_inventory",
            "status": "verified",
            "analysis_protocol": SOLAR_CYCLE_26_READINESS_PROTOCOL,
            "producer": "solar-data",
            "task_id": _validated_task_metadata(config)[1],
            "cutoff_date": cutoff_date,
            "input_refs": input_refs,
            "dataset_ids": [dataset_id for _path, dataset_id in specifications],
            "outputs": [output],
            "launch_readiness": inventory["launch_readiness"],
            "formal_classification_ready": inventory["formal_classification_ready"],
            "testable_peak_interval_ready": inventory["testable_peak_interval_ready"],
            "evidence_gaps": inventory["evidence_gaps"],
            "created_at": datetime.now(UTC).isoformat(),
        }
        _atomic_write_json(receipt_path, receipt)
        return _to_json(
            {
                "status": "verified",
                "artifact_refs": [artifact_ref],
                "receipt_refs": [receipt_ref],
                "launch_readiness": inventory["launch_readiness"],
                "formal_classification_ready": inventory["formal_classification_ready"],
                "testable_peak_interval_ready": inventory[
                    "testable_peak_interval_ready"
                ],
                "evidence_gaps": inventory["evidence_gaps"],
            }
        )
    except Exception as exc:
        return _error_json("prepare_solar_cycle_26_readiness", exc)


@tool(parse_docstring=True)
def reproduce_silso_cycle_extrema(
    monthly_total_path: str,
    smoothed_path: str,
    official_extrema_path: str,
    cycles: str = "21-24",
    config: RunnableConfig = None,
) -> str:
    """Reproduce SILSO cycle extrema from registered official inputs.

    The tool is deliberately narrow: it can read only the three hash-matching
    SILSO dataset IDs returned by the task's data context, and it always writes
    a task-local CSV, JSON, and provenance receipt. It never downloads data or
    exposes a general-purpose execution surface.

    Args:
        monthly_total_path: Eligible official monthly-total Version 2.0 path.
        smoothed_path: Eligible official 13-month-smoothed Version 2.0 path.
        official_extrema_path: Eligible official cycle minima/maxima table path.
        cycles: Cycle selector restricted to cycles 21 through 24.
        config: Runtime-injected task workspace configuration.

    Returns:
        Structured outcome with canonical artifact and receipt references.
    """

    try:
        from jw.research_protocols import SILSO_CYCLE_REPRODUCTION_PROTOCOL

        raw_path, raw_record = _resolve_eligible_dataset_path(
            monthly_total_path, "silso-monthly-total-v2", config
        )
        smoothed_file, smoothed_record = _resolve_eligible_dataset_path(
            smoothed_path, "silso-monthly-smoothed-v2", config
        )
        extrema_file, extrema_record = _resolve_eligible_dataset_path(
            official_extrema_path, "silso-cycle-extrema-v2", config
        )

        script_path = (
            Path(__file__).resolve().parent.parent
            / "subagents"
            / "solar"
            / "skills"
            / "solar-cycle"
            / "scripts"
            / "reproduce_silso_cycles.py"
        )
        implementation = runpy.run_path(str(script_path))
        selected = implementation["parse_cycle_selector"](cycles)
        if not selected or any(cycle < 21 or cycle > 24 for cycle in selected):
            raise ValueError("cycles must select only SILSO cycles 21 through 24")
        rows = implementation["build_comparison"](
            selected,
            implementation["parse_official_cycles"](
                extrema_file.read_text(encoding="utf-8")
            ),
            implementation["parse_smoothed_series"](
                smoothed_file.read_text(encoding="utf-8")
            ),
        )

        root = workspace_root_from_config(config)
        output_dir = root / "work" / "solar_data"
        csv_path = output_dir / "silso_cycle_extrema_comparison.csv"
        json_path = output_dir / "silso_cycle_extrema_comparison.json"
        receipt_path = (
            root / "receipts" / "datasets" / "silso_cycle_extrema_reproduction.json"
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=output_dir, prefix=f".{csv_path.name}.", suffix=".tmp"
        )
        os.close(descriptor)
        temporary_csv = Path(temporary_name)
        try:
            implementation["write_csv"](temporary_csv, rows)
            os.replace(temporary_csv, csv_path)
        finally:
            temporary_csv.unlink(missing_ok=True)

        inputs = []
        for record, path in (
            (raw_record, raw_path),
            (smoothed_record, smoothed_file),
            (extrema_record, extrema_file),
        ):
            inputs.append(
                {
                    "dataset_id": record["dataset_id"],
                    "path": record["path"],
                    "sha256": record["sha256"],
                    "verified_sha256": _file_sha256(path),
                }
            )
        artifact_payload = {
            "schema_version": "silso-cycle-reproduction-v1",
            "analysis_protocol": SILSO_CYCLE_REPRODUCTION_PROTOCOL,
            "source": "WDC-SILSO Sunspot Number Version 2.0",
            "cycles": selected,
            "method": (
                "Compare the published cycle extrema table with extrema "
                "recomputed from the published 13-month smoothed monthly series "
                "inside neighboring-extrema windows. Preserve both values."
            ),
            "inputs": inputs,
            "comparison": rows,
        }
        _atomic_write_json(json_path, artifact_payload)
        outputs = [
            {
                "path": "work/solar_data/silso_cycle_extrema_comparison.csv",
                "sha256": _file_sha256(csv_path),
            },
            {
                "path": "work/solar_data/silso_cycle_extrema_comparison.json",
                "sha256": _file_sha256(json_path),
            },
        ]
        receipt = {
            "schema_version": "research-dataset-receipt-v1",
            "receipt_type": "silso_cycle_extrema_reproduction",
            "status": "verified",
            "analysis_protocol": SILSO_CYCLE_REPRODUCTION_PROTOCOL,
            "producer": "solar-data",
            "task_id": _validated_task_metadata(config)[1],
            "inputs": inputs,
            "outputs": outputs,
            "cycle_numbers": selected,
            "row_count": len(rows),
            "created_at": datetime.now(UTC).isoformat(),
        }
        if receipt_path.is_file():
            existing = json.loads(receipt_path.read_text(encoding="utf-8"))
            if (
                isinstance(existing, dict)
                and existing.get("inputs") == inputs
                and existing.get("outputs") == outputs
                and existing.get("cycle_numbers") == selected
                and existing.get("task_id") == receipt["task_id"]
            ):
                receipt = existing
            else:
                _atomic_write_json(receipt_path, receipt)
        else:
            _atomic_write_json(receipt_path, receipt)
        return _to_json(
            {
                "status": "verified",
                "schema_version": "silso-cycle-reproduction-v1",
                "artifact_refs": [item["path"] for item in outputs],
                "receipt_refs": [
                    "receipts/datasets/silso_cycle_extrema_reproduction.json"
                ],
                "cycle_numbers": selected,
                "row_count": len(rows),
                "input_sha256": {
                    str(item["dataset_id"]): item["sha256"] for item in inputs
                },
            }
        )
    except Exception as exc:
        return _error_json("reproduce_silso_cycle_extrema", exc)


@tool(parse_docstring=True)
def run_silso_cycle_morphology(
    monthly_total_path: str,
    smoothed_path: str,
    official_extrema_path: str,
    config: RunnableConfig = None,
) -> str:
    """Run the registered SILSO v2.0 cycles 1--24 morphology experiment.

    The tool writes the three requested outputs under the task workspace and
    never downloads data or uses polar-field/F10.7 inputs.

    Args:
        monthly_total_path: Eligible SILSO v2.0 monthly-total path.
        smoothed_path: Eligible SILSO v2.0 13-month-smoothed path.
        official_extrema_path: Eligible official cycle extrema/boundary path.
        config: Runtime-injected task workspace configuration.

    Returns:
        Structured result with output paths, 24-row count, and fixed bootstrap settings.
    """
    try:
        from jw.research_protocols import SILSO_CYCLE_MORPHOLOGY_PROTOCOL

        monthly_total, monthly_record = _resolve_eligible_dataset_path(
            monthly_total_path, "silso-monthly-total-v2", config
        )
        smoothed, smoothed_record = _resolve_eligible_dataset_path(
            smoothed_path, "silso-monthly-smoothed-v2", config
        )
        extrema, extrema_record = _resolve_eligible_dataset_path(
            official_extrema_path, "silso-cycle-extrema-v2", config
        )
        script_path = (
            Path(__file__).resolve().parent.parent
            / "subagents"
            / "solar"
            / "skills"
            / "solar-cycle"
            / "scripts"
            / "run_cycle_morphology_experiment.py"
        )
        implementation = runpy.run_path(str(script_path))
        root = workspace_root_from_config(config)
        result = implementation["run"](
            monthly_total, smoothed, extrema, root / "outputs"
        )
        workspace_resolved = root.resolve()

        def relative_output_path(path: str) -> str:
            candidate = Path(path).resolve()
            if not candidate.is_relative_to(workspace_resolved):
                raise RuntimeError(
                    "morphology adapter returned an output outside the task workspace"
                )
            return candidate.relative_to(workspace_resolved).as_posix()

        outputs = [
            {
                "path": relative_output_path(path),
                "sha256": _file_sha256(Path(path)),
            }
            for path in result["outputs"]
        ]
        receipt = {
            "schema_version": "solar-cycle-morphology-receipt-v1",
            "receipt_type": "silso_cycle_morphology",
            "status": "verified",
            "analysis_protocol": SILSO_CYCLE_MORPHOLOGY_PROTOCOL,
            "producer": "solar-data",
            "task_id": _validated_task_metadata(config)[1],
            "cycle_numbers": list(range(1, 25)),
            "row_count": result["rows"],
            "bootstrap_seed": 20260826,
            "bootstrap_repetitions": 10000,
            "inputs": [
                {
                    "dataset_id": record["dataset_id"],
                    "path": record["path"],
                    "sha256": record["sha256"],
                }
                for record in (monthly_record, smoothed_record, extrema_record)
            ],
            "outputs": outputs,
            "created_at": datetime.now(UTC).isoformat(),
        }
        receipt_path = root / "receipts" / "datasets" / "silso_cycle_morphology.json"
        _atomic_write_json(receipt_path, receipt)
        return _to_json(
            {
                "status": "verified",
                "analysis_protocol": SILSO_CYCLE_MORPHOLOGY_PROTOCOL,
                "row_count": result["rows"],
                "artifact_refs": [item["path"] for item in outputs],
                "receipt_refs": ["receipts/datasets/silso_cycle_morphology.json"],
                "bootstrap_seed": 20260826,
                "bootstrap_repetitions": 10000,
            }
        )
    except Exception as exc:
        return _error_json("run_silso_cycle_morphology", exc)


@tool(parse_docstring=True)
def run_solar_cycle_26_historical_forecast(
    monthly_total_path: str,
    smoothed_path: str,
    official_extrema_path: str,
    config: RunnableConfig = None,
) -> str:
    """Run the leakage-controlled historical SC26 backtest and forecast.

    Args:
        monthly_total_path: Eligible SILSO v2.0 monthly-total input.
        smoothed_path: Eligible SILSO v2.0 13-month-smoothed input.
        official_extrema_path: Eligible official SILSO cycle-extrema input.
        config: Runtime-injected task workspace configuration.

    Returns:
        Verified output and receipt references for the complete experiment.
    """
    try:
        from jw.research_protocols import SOLAR_CYCLE_26_FORECAST_BACKTEST_PROTOCOL

        monthly, monthly_record = _resolve_eligible_dataset_path(
            monthly_total_path, "silso-monthly-total-v2", config
        )
        smoothed, smoothed_record = _resolve_eligible_dataset_path(
            smoothed_path, "silso-monthly-smoothed-v2", config
        )
        extrema, extrema_record = _resolve_eligible_dataset_path(
            official_extrema_path, "silso-cycle-extrema-v2", config
        )
        script_path = (
            Path(__file__).resolve().parents[2]
            / "scripts"
            / "run_sc26_historical_forecast.py"
        )
        script_dir = script_path.parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        implementation = runpy.run_path(str(script_path))
        root = workspace_root_from_config(config)
        output_dir = root / "outputs" / "sc26_forecast"
        output_dir.mkdir(parents=True, exist_ok=True)
        monthly_frame = implementation["load_monthly_total"](monthly)
        smoothed_frame = implementation["load_smoothed_total"](smoothed)
        cycles = implementation["build_cycle_table"](
            monthly_frame, smoothed_frame, extrema
        )
        rng = implementation["np"].random.default_rng(implementation["SEED"])
        same, same_stats = implementation["same_cycle_backtest"](cycles, rng)
        lag, lag_stats = implementation["next_cycle_backtest"](cycles, rng, "lag_peak")
        lag_both, both_stats = implementation["next_cycle_backtest"](
            cycles, rng, "lag_peak_rise"
        )
        forecast = implementation["formal_forecast"](cycles, rng)
        cycles.to_csv(
            output_dir / "sc26_cycle_features.csv", index=False, date_format="%Y-%m-%d"
        )
        implementation["pd"].concat(
            [
                lag.assign(model="lag_peak"),
                lag_both.assign(model="lag_peak_rise"),
                same.assign(model="same_cycle_rise"),
            ],
            ignore_index=True,
        ).to_csv(output_dir / "sc26_forecast_predictions.csv", index=False)
        (output_dir / "sc26_formal_forecast.json").write_text(
            json.dumps(forecast, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        sources = {
            "retrieved": datetime.now(UTC).date().isoformat(),
            "monthly": {
                "path": monthly_record["path"],
                "sha256": monthly_record["sha256"],
            },
            "smoothed": {
                "path": smoothed_record["path"],
                "sha256": smoothed_record["sha256"],
            },
            "extrema": {
                "path": extrema_record["path"],
                "sha256": extrema_record["sha256"],
            },
        }
        (output_dir / "data_manifest.json").write_text(
            json.dumps(sources, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        implementation["make_figure"](
            cycles,
            same,
            lag,
            lag_both,
            forecast,
            output_dir / "sc26_forecast_visualization.png",
        )
        implementation["write_report"](
            output_dir, cycles, same_stats, lag_stats, both_stats, forecast, sources
        )
        summary = {
            "output_dir": output_dir.relative_to(root).as_posix(),
            "cycles": len(cycles),
            "same_cycle": same_stats,
            "lag_peak": lag_stats,
            "lag_peak_rise": both_stats,
            "forecast": forecast,
        }
        (output_dir / "run_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        output_refs = [
            f"outputs/sc26_forecast/{name}"
            for name in (
                "sc26_cycle_features.csv",
                "sc26_forecast_predictions.csv",
                "sc26_formal_forecast.json",
                "data_manifest.json",
                "sc26_forecast_visualization.png",
                "sc26_historical_backtest_report.md",
                "sc26_formal_forecast_report.md",
                "run_summary.json",
            )
        ]
        outputs = [
            {"path": ref, "sha256": _file_sha256(root / ref)} for ref in output_refs
        ]
        receipt_ref = "receipts/datasets/solar_cycle_26_forecast_backtest.json"
        receipt = {
            "schema_version": "solar-cycle-26-forecast-backtest-receipt-v1",
            "receipt_type": "solar_cycle_26_forecast_backtest",
            "status": "verified",
            "analysis_protocol": SOLAR_CYCLE_26_FORECAST_BACKTEST_PROTOCOL,
            "producer": "solar-data",
            "task_id": _validated_task_metadata(config)[1],
            "cycle_numbers": list(range(1, 25)),
            "row_count": 24,
            "bootstrap_seed": implementation["SEED"],
            "bootstrap_repetitions": implementation["BOOTSTRAP_REPS"],
            "inputs": [
                {
                    "dataset_id": record["dataset_id"],
                    "path": record["path"],
                    "sha256": record["sha256"],
                }
                for record in (monthly_record, smoothed_record, extrema_record)
            ],
            "outputs": outputs,
            "forecast": forecast,
            "created_at": datetime.now(UTC).isoformat(),
        }
        _atomic_write_json(root / receipt_ref, receipt)
        return _to_json(
            {
                "status": "verified",
                "analysis_protocol": SOLAR_CYCLE_26_FORECAST_BACKTEST_PROTOCOL,
                "row_count": 24,
                "artifact_refs": output_refs,
                "receipt_refs": [receipt_ref],
                "forecast": forecast,
            }
        )
    except Exception as exc:
        return _error_json("run_solar_cycle_26_historical_forecast", exc)


@tool(parse_docstring=True)
def bind_f107_dataset_semantics(
    csv_path: str,
    silso_total_path: str = "",
    silso_hemispheric_path: str = "",
    config: RunnableConfig = None,
) -> str:
    """Canonicalize an uploaded F10.7 file and write a verified semantic receipt.

    This is the required input boundary for F10.7 discontinuity analysis. It
    binds columns by name, applies missing/duplicate policy, aggregates raw
    determinations to equal-weight daily and monthly means, and records the
    selected product, unit, coverage, sensitivities, and artifact hash.

    Args:
        csv_path: Task-scoped path such as ``/inputs/f107_daily_flux.csv``.
        silso_total_path: Optional SILSO Version 2 total monthly file.
        silso_hemispheric_path: Optional hemispheric file recorded as excluded
            from the primary total-sunspot-number estimand.

    Returns:
        Structured outcome JSON with canonical artifact and receipt paths.
    """

    try:
        from f107_semantic_adapter import write_f107_contract

        source = _resolve_eligible_data_path(csv_path, config)
        silso_total = (
            _resolve_eligible_data_path(silso_total_path, config)
            if silso_total_path.strip()
            else None
        )
        silso_hemispheric = (
            _resolve_eligible_data_path(silso_hemispheric_path, config)
            if silso_hemispheric_path.strip()
            else None
        )
        root = workspace_root_from_config(config)
        artifact_name = (
            "canonical_f107_sn_monthly.csv"
            if silso_total is not None
            else "canonical_f107_monthly.csv"
        )
        artifact = root / "work" / artifact_name
        receipt = root / "receipts" / "datasets" / "f107_semantics.json"
        manifest = write_f107_contract(
            source,
            canonical_path=artifact,
            receipt_path=receipt,
            silso_total_path=silso_total,
            silso_hemispheric_path=silso_hemispheric,
        )
        return _to_json(
            {
                "schema_version": 1,
                "status": "success",
                "summary": (
                    "F10.7 flux was bound by column name and canonicalized from "
                    "raw determinations to equal-weight daily and monthly means."
                ),
                "artifact_refs": [f"work/{artifact_name}"],
                "receipt_refs": ["receipts/datasets/f107_semantics.json"],
                "retryable": False,
                "manifest_id": manifest["manifest_id"],
                "canonical_sha256": manifest["canonical_sha256"],
                "diagnostics": manifest["diagnostics"],
            }
        )
    except Exception as exc:
        return _error_json("bind_f107_dataset_semantics", exc)


SOLAR_FEATURE_TOOLS = [
    solar_data_open_context,
    solar_research_evidence,
    solar_research_analysis,
    audit_solar_data_quality,
    engineer_solar_features,
    prepare_solar_experiment,
    dataset_statistics,
    prepare_solar_cycle_26_readiness,
    prepare_solar_precursor_cycle_table,
    reproduce_silso_cycle_extrema,
    run_silso_cycle_morphology,
    run_solar_cycle_26_historical_forecast,
    bind_f107_dataset_semantics,
]

register_tool_bundle("solar-features", SOLAR_FEATURE_TOOLS)
