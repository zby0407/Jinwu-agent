"""Deterministic producer/reviewer handoffs for full research runs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from research_review.policies import policy_registry

from ..research_review import ResearchReviewStore, store_from_config

if TYPE_CHECKING:
    from langchain.agents.middleware.types import ToolCallRequest

_BOUNDED_STAGE_BY_SPECIALIST_INTENT = {
    ("solar-planner", "research_planning"): "planning",
    ("solar-data", "data_preparation"): "data",
    ("solar-hypothesis", "hypothesis_generation"): "hypothesis",
    ("solar-hypothesis", "hypothesis_comparison"): "hypothesis",
    ("solar-hypothesis", "hypothesis_update"): "hypothesis",
    ("solar-experiment", "experiment_design"): "experiment_design",
    ("solar-experiment", "experiment_run"): "experiment_result",
}

_CANONICAL_CHECKPOINT_DIRECTIVE = {
    "planning": (
        "This is an explicit publication request and requires a frozen "
        "research-plan-v1 artifact. Call research_planner_get_brief, resume from "
        "draft_checkpoint.next_section, persist exactly one ordered section per "
        "research_planner_update_draft call for an incomplete initial draft. Once "
        "all sections exist, use research_planner_apply_revision_patch for every "
        "small cross-section repair. For a large repair, stage replacements with "
        "research_planner_stage_revision_section and atomically finish with "
        "research_planner_commit_revision_candidate; never rewrite active complete "
        "sections sequentially. Then call "
        "research_planner_validate_draft, and after plan_ready call "
        "research_planner_freeze_plan before returning. Missing data must be "
        "recorded as a gap or stop condition, never used to skip freezing. Return "
        "the real planner/runs/<run_id>/research_plan.json path. The first bound "
        "request is immutable. A full empirical research_route must use an explicit "
        "data -> hypothesis_generation -> experiment_design -> experiment_result -> "
        "hypothesis_update dependency path; never collapse experiment_design into "
        "experiment or experiment_result. If a planner tool returns must_stop=true, stop the "
        "specialist attempt immediately and preserve its saved checkpoint."
    ),
    "data": (
        "First call solar_data_open_context exactly once. Use only its "
        "eligible_inputs; if it returns input_missing/must_stop, return that "
        "hash-bound blocker immediately without guessing paths. Otherwise persist "
        "the inspected dataset semantics and transformations in the task-local "
        "data session or receipts/datasets; return exact paths and hashes. Prose "
        "without a canonical data artifact cannot enter review."
    ),
    "hypothesis": (
        "Persist the complete candidate set with scientific_hypothesis_update_draft "
        "and finish with scientific_hypothesis_get_draft so "
        "work/scientific_hypothesis_state.json is the canonical review source."
    ),
    "experiment_design": (
        "Validate and persist automatic-experiment-design-v1 under the exact run; "
        "stop before execution and return experiment/runs/<run_id>/design.json. "
        "Bind input_refs to the accepted upstream data artifact's produced files "
        "(e.g. work/solar_data/solar_precursor_cycle_features.csv and receipts "
        "under receipts/datasets/), not to the planning artifact's originally "
        "declared source paths, which are provenance records rather than "
        "task-local readable inputs."
    ),
    "experiment_result": (
        "Execute only the accepted design, verify the real result, and finalize the "
        "run. Return experiment/runs/<run_id>/record.json and report.md; a plan or "
        "unverified preview is not an experiment result.\n"
        "BIND THE STAGED INPUTS FIRST: call automatic_experiment_bind_request with "
        "exactly '@inputs/_staged.json' — this is the hash-bound sidecar that "
        "declares every accepted data-artifact file. Do NOT omit input_refs, do "
        "NOT re-extract paths from task prose. The sidecar is the sole authority.\n"
        "The run wall budget is short and every rejected prepare_attempt burns it, so "
        "submit worker code that passes CodePolicy on the first attempt: define "
        "run_experiment(context) and return one automatic-experiment-worker-result-v1 "
        "object with exactly the fields schema_version, execution_completed, "
        "measurements, result_items, artifacts, warnings, endpoint_results, "
        "scientific_payload. Never import os or any module outside the advertised "
        "sandbox; derive every file path from context['input_path_by_id'], "
        "context['input_files'][...][index], context['artifact_path_by_id'], or "
        "context['output_dir'] joined with the / operator (they are pathlib.Path "
        "objects), never from string literals or os.path. Each measurement and "
        "result_item must carry name/id, value, unit, role (primary|secondary|"
        "diagnostic), and source_artifact (an exact artifact-path string literal or "
        "null, no computed path). Call prepare_attempt once with files as a JSON "
        "object, then execute_attempt on the attempt id that prepare returned; do "
        "not re-prepare after a successful prepare, and do not call execute before a "
        "prepare succeeds. Read the stage's required_worker_outputs block from the "
        "prepare response and use its exact measurement names, result ids, and "
        "artifact paths."
    ),
}


def _route_kind(state: object) -> str | None:
    if not isinstance(state, Mapping):
        return None
    route = state.get("research_route")
    if not isinstance(route, Mapping):
        return None
    if route.get("mode") == "full_research":
        return "full"
    if route.get("mode") == "verified_analysis":
        stage = _BOUNDED_STAGE_BY_SPECIALIST_INTENT.get(
            (route.get("required_specialist"), route.get("task_intent"))
        )
        if stage is not None:
            return f"bounded:{stage}"
    return None


def _content_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        pieces: list[str] = []
        for item in value:
            if isinstance(item, Mapping) and isinstance(item.get("text"), str):
                pieces.append(item["text"])
            elif isinstance(item, str):
                pieces.append(item)
        return "\n".join(pieces)
    return str(value or "")


def _tool_result_text(result: object) -> str:
    message = _result_tool_message(result)
    if message is not None:
        return _content_text(message.content)
    return ""


def _bound_research_question(store: ResearchReviewStore) -> str:
    """Return the exact task-bound user question for producer dispatch."""

    try:
        payload = json.loads((store.workspace_root / "task.json").read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""
    question = (
        payload.get("research_question") if isinstance(payload, Mapping) else None
    )
    return question.strip() if isinstance(question, str) else ""


def _freeze_validated_planner_draft(config: object) -> dict[str, Any]:
    """Validate and commit a complete planner draft without another model turn."""

    from ..tools.research_planner import (
        research_planner_freeze_plan,
        research_planner_validate_draft,
    )

    validation_raw = research_planner_validate_draft.func(
        request_sha256="", config=config
    )
    validation = json.loads(validation_raw)
    if validation.get("status") != "plan_ready":
        detail = validation.get("error") or validation.get("errors") or validation
        raise RuntimeError(f"planner deterministic validation did not pass: {detail}")

    raw = research_planner_freeze_plan.func(request_sha256="", config=config)
    payload = json.loads(raw)
    if payload.get("status") != "frozen_and_valid":
        raise RuntimeError(payload.get("error") or "planner freeze did not complete")
    return payload


def _validate_planner_draft(config: object) -> dict[str, Any]:
    """Run the deterministic planner preflight once and return its result."""

    from ..tools.research_planner import research_planner_validate_draft

    validation_raw = research_planner_validate_draft.func(
        request_sha256="", config=config
    )
    return json.loads(validation_raw)


def _register_planner_validation_revision(
    config: object, validation: dict[str, Any]
) -> dict[str, Any] | None:
    """Route deterministic planner validation issues back as a typed revision.

    A full draft that fails preflight is not a pipeline error: the producer
    must repair named sections. Persist the localized issues as a hash-bound
    revision so the next planner attempt resumes at
    ``repair_evidence_revision`` with the exact defects instead of restarting
    from an empty brief. Returns the registered checkpoint, or ``None`` when
    the validation issues cannot be expressed as a planner revision.
    """

    from ..tools.research_planner import register_planner_evidence_revision

    raw_issues = validation.get("issues") or validation.get("errors") or []
    if isinstance(raw_issues, str):
        raw_issues = [raw_issues]
    if not isinstance(raw_issues, Sequence):
        return None
    issues: list[dict[str, Any]] = []
    for item in raw_issues:
        if isinstance(item, Mapping):
            issues.append(dict(item))
        elif isinstance(item, str) and item.strip():
            issues.append(
                {
                    "owner": "solar-planner",
                    "severity": "major",
                    "summary": item.strip(),
                    "acceptance_test": (
                        "deterministic planner validation reports plan_ready"
                    ),
                }
            )
    if not issues:
        return None
    detail = (
        validation.get("error") or validation.get("message") or "draft failed preflight"
    )
    capsule = {
        "review_id": f"planner-validation-revision-{len(issues)}",
        "decision": "revise",
        "summary": str(detail),
        "issues": issues,
    }
    return register_planner_evidence_revision(capsule["review_id"], capsule, config)


def _result_tool_message(result: object) -> ToolMessage | None:
    if isinstance(result, ToolMessage):
        return result
    if not isinstance(result, Command) or not isinstance(result.update, Mapping):
        return None
    messages = result.update.get("messages")
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
        return None
    return next(
        (message for message in reversed(messages) if isinstance(message, ToolMessage)),
        None,
    )


def _result_failed(result: object) -> bool:
    message = _result_tool_message(result)
    return message is None or getattr(message, "status", None) == "error"


def _replace_tool_message_content(message: ToolMessage, content: str) -> ToolMessage:
    copier = getattr(message, "model_copy", None)
    if callable(copier):
        return copier(update={"content": content})
    return ToolMessage(
        content=content,
        tool_call_id=str(message.tool_call_id),
        name=message.name,
        status=getattr(message, "status", None),
    )


def _with_result_content(
    result: ToolMessage | Command[Any], content: str
) -> ToolMessage | Command[Any]:
    if isinstance(result, ToolMessage):
        return _replace_tool_message_content(result, content)
    update = result.update
    if not isinstance(update, Mapping):
        return result
    messages = update.get("messages")
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
        return result
    replaced = False
    rewritten: list[object] = []
    for message in reversed(messages):
        if not replaced and isinstance(message, ToolMessage):
            rewritten.append(_replace_tool_message_content(message, content))
            replaced = True
        else:
            rewritten.append(message)
    rewritten.reverse()
    return Command(
        graph=result.graph,
        update={**update, "messages": rewritten},
        resume=result.resume,
        goto=result.goto,
    )


def _upstream_context(store: ResearchReviewStore, stage: str) -> str:
    rows: list[dict[str, Any]] = []
    for artifact in store.accepted_artifacts():
        if artifact["stage"] == stage:
            continue
        rows.append(
            {
                "artifact_id": artifact["artifact_id"],
                "stage": artifact["stage"],
                "version": artifact["version"],
                "artifact_sha256": artifact["artifact_sha256"],
                "payload": artifact["payload"],
                "limitations": artifact["limitations"],
            }
        )
    if stage == "experiment_result":
        design_artifact = None
        for artifact in store.accepted_artifacts():
            if artifact["stage"] == "experiment_design":
                design_artifact = artifact
                break
        if design_artifact is not None:
            producer_result = (design_artifact.get("payload") or {}).get(
                "producer_result", ""
            )
            if isinstance(producer_result, str):
                for match in re.finditer(
                    r"run_id[:\s]*`?question_([a-f0-9]+-[0-9]+T[0-9]+Z-[a-f0-9]+)`?",
                    producer_result,
                ):
                    rows.append(
                        {
                            "artifact_id": "experiment_design-run-id",
                            "stage": "experiment_design",
                            "version": None,
                            "artifact_sha256": None,
                            "payload": {"run_id": match.group(0)},
                            "limitations": [],
                        }
                    )
                    break
    encoded = json.dumps(rows, ensure_ascii=False)
    return encoded[:30_000]


def _stage_data_produced_inputs(store: ResearchReviewStore) -> list[str]:
    """Copy the accepted data artifact's produced files into the run workspace
    inputs/ directory so the experiment contract can resolve them as input_refs.

    Returns the list of staged inputs/... relative paths. Deterministic and
    idempotent: files are copied by content hash, existing identical files are
    left untouched."""

    workspace = store.workspace_root
    inputs_dir = workspace / "inputs"
    staged: list[str] = []
    data_artifact = None
    for artifact in store.accepted_artifacts():
        if artifact["stage"] == "data":
            data_artifact = artifact
            break
    if data_artifact is None:
        return staged
    payload = data_artifact.get("payload") or {}
    candidates: list[Path] = []
    for ref in payload.get("canonical_source_refs") or []:
        if isinstance(ref, str):
            p = workspace / ref
            if p.is_file():
                candidates.append(p)
    producer_result = payload.get("producer_result") or ""
    if isinstance(producer_result, str):
        for match in re.finditer(r"work/solar_data/[\w./-]+\.csv", producer_result):
            p = workspace / match.group(0)
            if p.is_file():
                candidates.append(p)
    inputs_dir.mkdir(parents=True, exist_ok=True)
    for source in candidates:
        target = inputs_dir / source.name
        if target.is_file() and target.read_bytes() == source.read_bytes():
            staged.append(f"inputs/{source.name}")
            continue
        shutil.copyfile(source, target)
        staged.append(f"inputs/{source.name}")
    if staged:
        sidecar = {
            "schema_version": "automatic-experiment-input-refs-v1",
            "input_refs": [
                {
                    "id": f"input_{index:02d}",
                    "path": path,
                    "description": "Accepted data-artifact produced file, hash-staged by the research state machine.",
                    "required": True,
                }
                for index, path in enumerate(staged, start=1)
            ],
        }
        sidecar_path = inputs_dir / "_staged.json"
        sidecar_path.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2))
        staged.append("inputs/_staged.json")
    return staged


def _prior_action_block_count(state: object, fingerprint: str) -> int:
    """Count identical deterministic-action rejections in the current user turn."""

    if not isinstance(state, Mapping):
        return 0
    messages = state.get("messages")
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
        return 0
    count = 0
    for message in reversed(messages):
        message_type = (
            message.get("type")
            if isinstance(message, Mapping)
            else getattr(message, "type", None)
        )
        if message_type in {"human", "user"}:
            break
        if isinstance(message, Mapping):
            content = _content_text(message.get("content", ""))
        else:
            content = _content_text(getattr(message, "content", ""))
        if fingerprint in content and content.lstrip().startswith(
            _RESEARCH_REVIEW_ACTION_BLOCK_PREFIX
        ):
            count += 1
    return count


_TRANSIENT_TOOL_ERROR_MARKERS = (
    "APIConnectionError",
    "RemoteProtocolError",
    "ConnectError",
    "ConnectTimeout",
    "ReadTimeout",
    "WriteTimeout",
    "PoolTimeout",
    "APITimeoutError",
    "Server disconnected",
    "Connection reset",
    "temporarily unavailable",
)
_TRANSIENT_TASK_FAILURE_LIMIT = 6


def _is_transient_task_failure(content: str) -> bool:
    """Return whether a specialist task failure is transient infrastructure.

    A remote connection/timeout drop is not evidence that a specialist made a
    bad research decision, so it must not consume the scientific
    two-failure stop budget. Only deterministic preflight/content failures and
    repeated identical errors should count toward blocking.
    """

    return any(marker in content for marker in _TRANSIENT_TOOL_ERROR_MARKERS)


def _prior_task_failures(
    state: object, subagent_type: str
) -> tuple[tuple[str, str], ...]:
    """Return sanitized failure fingerprint/summary pairs for one specialist."""

    if not isinstance(state, Mapping):
        return ()
    messages = state.get("messages")
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
        return ()
    latest_human = -1
    for index, message in enumerate(messages):
        message_type = (
            message.get("type")
            if isinstance(message, Mapping)
            else getattr(message, "type", None)
        )
        if message_type in {"human", "user"}:
            latest_human = index
    matching_call_ids: set[str] = set()
    failures: list[tuple[str, str]] = []
    for message in messages[latest_human + 1 :]:
        if isinstance(message, Mapping):
            calls = message.get("tool_calls", ())
            message_type = message.get("type")
            content = _content_text(message.get("content", ""))
            tool_call_id = message.get("tool_call_id")
        else:
            calls = getattr(message, "tool_calls", ())
            message_type = getattr(message, "type", None)
            content = _content_text(getattr(message, "content", ""))
            tool_call_id = getattr(message, "tool_call_id", None)
        if isinstance(calls, Sequence) and not isinstance(calls, (str, bytes)):
            for call in calls:
                if not isinstance(call, Mapping) or call.get("name") != "task":
                    continue
                args = call.get("args")
                if (
                    not isinstance(args, Mapping)
                    or args.get("subagent_type") != subagent_type
                ):
                    continue
                call_id = call.get("id")
                if isinstance(call_id, str):
                    matching_call_ids.add(call_id)
        if message_type == "tool" and str(tool_call_id) in matching_call_ids:
            stripped = content.lstrip()
            if stripped.startswith(("[TOOL ERROR]", "[TOOL ERROR CAPSULE]")):
                if _is_transient_task_failure(stripped):
                    # Transient remote drops are retried by the supervisor, not
                    # counted as a scientific specialist failure.
                    continue
                matched = re.search(r"(?m)^fingerprint=([0-9a-f]{64})$", content)
                summary_match = re.search(r"(?m)^error=(.+)$", content)
                summary = (
                    summary_match.group(1).strip()
                    if summary_match
                    else stripped.splitlines()[0]
                )
                failures.append((matched.group(1) if matched else "", summary[:500]))
            elif stripped.startswith(
                "[RESEARCH REVIEW BLOCKED] producer local preflight failed before "
                "Evidence review:"
            ):
                failures.append(
                    (
                        hashlib.sha256(stripped.encode("utf-8")).hexdigest(),
                        " ".join(stripped.split())[:500],
                    )
                )
    return tuple(failures)


def _prior_task_failure_fingerprints(
    state: object, subagent_type: str
) -> tuple[str, ...]:
    """Return failure fingerprints for one specialist since the latest user turn."""

    return tuple(item[0] for item in _prior_task_failures(state, subagent_type))


def _open_data_context_preflight(
    config: object,
    *,
    route_kind: str,
    analysis_protocol: str = "none",
) -> dict[str, Any]:
    """Refresh task-bound project inputs and open the canonical Data context.

    The tool is idempotent by content hash. Running it at the orchestration
    boundary prevents a provider from returning prose before performing the
    mandatory first data action.
    """

    from ..tools.solar_feature import (
        open_bounded_solar_data_context,
        solar_data_open_context,
    )
    from ..workspaces import binding_from_config, ensure_thread_workspace

    binding = binding_from_config(config)  # type: ignore[arg-type]
    if binding is not None and not binding.legacy:
        ensure_thread_workspace(
            binding.thread_id,
            binding.base_workspace,
            project_id=binding.project_id,
        )
    if route_kind == "full":
        raw = solar_data_open_context.func(
            analysis_protocol=analysis_protocol, config=config
        )
        payload = json.loads(raw)
    else:
        payload = open_bounded_solar_data_context(
            config, analysis_protocol=analysis_protocol
        )
    if not isinstance(payload, dict):
        raise RuntimeError("solar_data_open_context returned a non-object payload")
    if payload.get("status") == "error":
        raise RuntimeError(
            str(payload.get("error_message") or "solar data context preflight failed")
        )
    if payload.get("schema_version") != "solar-data-context-v1":
        raise RuntimeError("solar data context preflight returned an invalid schema")
    return payload


def _data_context_projection(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return the bounded context fields safe to place in a task description."""

    eligible = payload.get("eligible_inputs")
    return {
        "status": payload.get("status"),
        "context_mode": payload.get("context_mode"),
        "analysis_protocol": payload.get("analysis_protocol"),
        "required_data_product": payload.get("required_data_product"),
        "must_stop": bool(payload.get("must_stop")),
        "receipt_ref": payload.get("receipt_ref"),
        "context_sha256": payload.get("context_sha256"),
        "required_dataset_ids": payload.get("required_dataset_ids", []),
        "missing_required_dataset_ids": payload.get("missing_required_dataset_ids", []),
        "eligible_inputs": eligible if isinstance(eligible, list) else [],
        "instruction": payload.get("instruction"),
    }


def _prior_transient_task_failure_count(state: object, subagent_type: str) -> int:
    """Count transient infrastructure failures for one specialist this turn."""

    if not isinstance(state, Mapping):
        return 0
    messages = state.get("messages")
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
        return 0
    latest_human = -1
    for index, message in enumerate(messages):
        message_type = (
            message.get("type")
            if isinstance(message, Mapping)
            else getattr(message, "type", None)
        )
        if message_type in {"human", "user"}:
            latest_human = index
    matching_call_ids: set[str] = set()
    count = 0
    for message in messages[latest_human + 1 :]:
        if isinstance(message, Mapping):
            calls = message.get("tool_calls", ())
            message_type = message.get("type")
            content = _content_text(message.get("content", ""))
            tool_call_id = message.get("tool_call_id")
        else:
            calls = getattr(message, "tool_calls", ())
            message_type = getattr(message, "type", None)
            content = _content_text(getattr(message, "content", ""))
            tool_call_id = getattr(message, "tool_call_id", None)
        if isinstance(calls, Sequence) and not isinstance(calls, (str, bytes)):
            for call in calls:
                if not isinstance(call, Mapping) or call.get("name") != "task":
                    continue
                args = call.get("args")
                if (
                    not isinstance(args, Mapping)
                    or args.get("subagent_type") != subagent_type
                ):
                    continue
                call_id = call.get("id")
                if isinstance(call_id, str):
                    matching_call_ids.add(call_id)
        if (
            message_type == "tool"
            and str(tool_call_id) in matching_call_ids
            and content.lstrip().startswith(("[TOOL ERROR]", "[TOOL ERROR CAPSULE]"))
            and _is_transient_task_failure(content.lstrip())
        ):
            count += 1
    return count


_RESEARCH_REVIEW_ACTION_BLOCK_PREFIX = "[RESEARCH REVIEW ACTION BLOCKED]"


def _wrong_deterministic_action(
    request: ToolCallRequest,
    *,
    required_tool: str,
    required_args: dict[str, Any],
) -> ToolMessage | Command[Any]:
    payload = json.dumps(
        {"tool": required_tool, "args": required_args},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    fingerprint = f"next_action={payload}"
    repeated = _prior_action_block_count(request.state, fingerprint) >= 1
    if repeated:
        message = ToolMessage(
            content=(
                "[RESEARCH REVIEW STOP] The same incorrect action was rejected "
                "twice in this user turn. Research state is unchanged. "
                f"{fingerprint}"
            ),
            tool_call_id=str(
                request.tool_call.get("id") or "research-review-action-stop"
            ),
            name=str(request.tool_call.get("name") or "unknown_tool"),
        )
        # A successful ToolMessage alone still lets Qwen enter the model/tool
        # edge again and repeat the stale call.  End this graph turn explicitly;
        # the persisted ResearchRunStateV2 remains resumable on the next turn.
        return Command(update={"messages": [message]}, goto="__end__")
    return ToolMessage(
        content=(
            f"{_RESEARCH_REVIEW_ACTION_BLOCK_PREFIX} Research state is unchanged. "
            f"{fingerprint}"
        ),
        tool_call_id=str(
            request.tool_call.get("id") or "research-review-action-blocked"
        ),
        name=str(request.tool_call.get("name") or "unknown_tool"),
        status="error",
    )


class ResearchReviewOrchestrationMiddleware(AgentMiddleware[Any, Any, Any]):
    """Enforce typed handoffs and persist every producer result immutably."""

    name = "research_review_orchestration"

    @staticmethod
    def _blocked(request: ToolCallRequest, reason: str) -> ToolMessage:
        return ToolMessage(
            content=f"[RESEARCH REVIEW BLOCKED] {reason}",
            tool_call_id=str(
                request.tool_call.get("id") or "research-review-blocked-tool-call"
            ),
            name=str(request.tool_call.get("name") or "unknown_tool"),
            status="error",
        )

    def _prepare(
        self, request: ToolCallRequest
    ) -> tuple[
        ToolCallRequest,
        dict[str, Any] | None,
        ToolMessage | Command[Any] | None,
    ]:
        route_kind = _route_kind(request.state)
        if route_kind is None:
            return request, None, None
        config = getattr(request.runtime, "config", None)
        store = store_from_config(config)
        if route_kind == "full":
            store.recover_canonical_producer_after_tool_failure()
        action = (
            store.next_action()
            if route_kind == "full"
            else store.bounded_stage_action(route_kind.split(":", 1)[1])
        )
        name = str(request.tool_call.get("name") or "")
        args = request.tool_call.get("args", {})
        args = dict(args) if isinstance(args, Mapping) else {}

        if action["kind"] == "terminal":
            return (
                request,
                action,
                self._blocked(
                    request,
                    f"research run is {action['status']}; unresolved issues must be reported honestly",
                ),
            )
        if action["kind"] == "released":
            return (
                request,
                action,
                self._blocked(
                    request,
                    "the accepted stage is immutable; no further tool call is allowed",
                ),
            )
        if action["kind"] == "prepare_release":
            if name != "research_release_prepare":
                return (
                    request,
                    action,
                    self._blocked(
                        request,
                        "the next action is research_release_prepare with a coherent reader-facing draft",
                    ),
                )
            try:
                store.reserve_action(action)
            except RuntimeError as exc:
                return request, action, self._blocked(request, str(exc))
            return request, action, None
        if action["kind"] == "independent_review":
            if name != "research_independent_review":
                # The arguments for this state-machine edge are fully known. Do
                # not ask Qwen to rediscover the tool after it emits a stale task
                # call: the Supervisor executes the typed action itself and
                # returns the real tool receipt. This is orchestration, not a
                # reviewer decision, and the independent tool still enforces
                # model-family separation and hash binding.
                try:
                    store.reserve_action(action)
                    from ..tools.research_review import research_independent_review

                    result = research_independent_review.func(
                        action["review_mode"], config=config
                    )
                except Exception:
                    return (
                        request,
                        action,
                        _wrong_deterministic_action(
                            request,
                            required_tool="research_independent_review",
                            required_args={"review_mode": action["review_mode"]},
                        ),
                    )
                return (
                    request,
                    action,
                    ToolMessage(
                        content=(
                            "[DETERMINISTIC ACTION REDIRECT] Supervisor replaced a "
                            "stale model tool call with the required hash-bound "
                            "independent review action.\n" + str(result)
                        ),
                        tool_call_id=str(
                            request.tool_call.get("id")
                            or "research-independent-review-redirect"
                        ),
                        name=name,
                    ),
                )
            try:
                store.reserve_action(action)
            except RuntimeError as exc:
                return request, action, self._blocked(request, str(exc))
            return request, action, None
        if name != "task":
            return (
                request,
                action,
                self._blocked(
                    request, "the next graph node must be delegated through task"
                ),
            )

        expected = (
            "solar-evidence" if action["kind"] == "review" else action["producer"]
        )
        actual = str(args.get("subagent_type") or "")
        if actual != expected:
            return (
                request,
                action,
                self._blocked(
                    request, f"expected {expected}, received {actual or '<missing>'}"
                ),
            )
        prior_failures = _prior_task_failures(request.state, expected)
        failure_fingerprints = tuple(item[0] for item in prior_failures)
        if len(failure_fingerprints) >= 2:
            receipt = store.block_for_tool_failures(
                stage=action["stage"],
                producer=expected,
                fingerprints=failure_fingerprints,
                failure_summaries=tuple(item[1] for item in prior_failures),
            )
            message = ToolMessage(
                content=(
                    "[RESEARCH REVIEW TOOL FAILURE STOP] The required specialist "
                    f"{expected} failed twice in this user turn. Research state "
                    "is now blocked; start a new task only after changing the "
                    "approach or external model state. "
                    f"receipt_sha256={receipt['receipt_sha256']}"
                ),
                tool_call_id=str(
                    request.tool_call.get("id") or "research-tool-failure-stop"
                ),
                name=name,
                status="error",
            )
            return (
                request,
                action,
                Command(update={"messages": [message]}, goto="__end__"),
            )
        transient_failures = _prior_transient_task_failure_count(
            request.state, expected
        )
        if transient_failures >= _TRANSIENT_TASK_FAILURE_LIMIT:
            # A sustained remote outage is different from one-off drops: stop
            # honestly instead of burning the action budget on a dead provider.
            receipt = store.block_for_tool_failures(
                stage=action["stage"],
                producer=expected,
                fingerprints=(
                    hashlib.sha256(
                        f"transient-outage:{expected}:{transient_failures}".encode()
                    ).hexdigest(),
                ),
                failure_summaries=(
                    f"{expected} reached the transient provider failure limit "
                    f"({transient_failures})",
                ),
            )
            message = ToolMessage(
                content=(
                    "[RESEARCH REVIEW TOOL FAILURE STOP] The required specialist "
                    f"{expected} hit {transient_failures} consecutive transient "
                    "connection/timeout failures in this user turn; the provider "
                    "appears unavailable. Research state is now blocked. "
                    f"receipt_sha256={receipt['receipt_sha256']}"
                ),
                tool_call_id=str(
                    request.tool_call.get("id") or "research-tool-failure-stop"
                ),
                name=name,
                status="error",
            )
            return (
                request,
                action,
                Command(update={"messages": [message]}, goto="__end__"),
            )
        description = str(args.get("description") or "").strip()
        action_reserved = False
        if action["kind"] == "review":
            try:
                store.reserve_action(action)
            except RuntimeError as exc:
                return request, action, self._blocked(request, str(exc))
            action_reserved = True
            deterministic = store.persist_deterministic_preflight_verdict(
                action["review_mode"]
            )
            if deterministic is not None:
                receipt = {
                    "review_id": deterministic["review_id"],
                    "decision": deterministic["decision"],
                    "verdict_sha256": deterministic["verdict_sha256"],
                    "next_owner": deterministic["next_owner"],
                    "carry_forward_limits": deterministic["carry_forward_limits"],
                }
                return (
                    request,
                    action,
                    ToolMessage(
                        content=(
                            "[DETERMINISTIC REVIEW VERDICT] "
                            + (
                                "The complete bounded protocol passed deterministic "
                                "validation and was accepted without remote Evidence "
                                "review.\n\n"
                                if deterministic["decision"] == "accept"
                                else "A non-waivable policy defect was persisted "
                                "before remote Evidence review.\n\n"
                            )
                            + "[REVIEW_VERDICT_V2]\n"
                            + json.dumps(receipt, ensure_ascii=False)
                        ),
                        tool_call_id=str(
                            request.tool_call.get("id")
                            or "deterministic-review-verdict"
                        ),
                        name=name,
                    ),
                )
            description += (
                "\n\n[EVIDENCE_REVIEW_V2]\n"
                f"review_mode={action['review_mode']}\n"
                "Open the server-bound context with evidence_review_open_context, "
                "inspect the immutable artifact and sources, then submit exactly one "
                "ReviewVerdictV2. Never edit the producer artifact."
            )
        else:
            revision_capsule = None
            planner_revision_checkpoint = None
            data_context: dict[str, Any] | None = None
            if action["stage"] == "data":
                try:
                    route = (
                        request.state.get("research_route", {})
                        if isinstance(request.state, Mapping)
                        else {}
                    )
                    analysis_protocol = (
                        str(route.get("required_analysis_protocol") or "none")
                        if isinstance(route, Mapping)
                        else "none"
                    )
                    data_context = _open_data_context_preflight(
                        config,
                        route_kind=route_kind,
                        analysis_protocol=analysis_protocol,
                    )
                except Exception as exc:
                    return (
                        request,
                        action,
                        self._blocked(
                            request,
                            "producer local preflight failed before Evidence "
                            f"review: {type(exc).__name__}: {exc}",
                        ),
                    )
            revision_review_id = action.get("revision_review_id")
            if isinstance(revision_review_id, str):
                revision_capsule = store.revision_capsule(
                    revision_review_id, action["producer"]
                )
                if action["stage"] == "planning":
                    try:
                        from ..tools.research_planner import (
                            register_planner_evidence_revision,
                        )

                        planner_revision_checkpoint = (
                            register_planner_evidence_revision(
                                revision_review_id,
                                revision_capsule,
                                getattr(request.runtime, "config", None),
                            )
                        )
                    except Exception as exc:
                        return (
                            request,
                            action,
                            self._blocked(
                                request,
                                "planner Evidence revision could not invalidate the "
                                f"old validated draft: {type(exc).__name__}: {exc}",
                            ),
                        )
            staged_inputs: list[str] = []
            if action["stage"] in {"experiment_design", "experiment_result"}:
                try:
                    staged_inputs = _stage_data_produced_inputs(store)
                except Exception:
                    staged_inputs = []
            staged_directive = ""
            if staged_inputs:
                staged_directive = (
                    "\nstaged_data_inputs="
                    + json.dumps(staged_inputs, ensure_ascii=False)
                    + "\nBind every required input_ref to one of these staged "
                    "inputs/... paths; they are the only readable task-local copies "
                    "of the accepted data artifact's produced files."
                )
            data_context_directive = ""
            if data_context is not None:
                data_context_directive = (
                    "\ndeterministic_data_context="
                    + json.dumps(
                        _data_context_projection(data_context), ensure_ascii=False
                    )
                    + "\nThe Supervisor already opened this hash-bound context. "
                    "Do not rediscover or guess inputs. If must_stop=true, return "
                    "the blocker immediately. Otherwise inspect the exact eligible "
                    "input and persist at least one additional task-local data "
                    "artifact before returning."
                )
            bound_question_directive = ""
            if action["stage"] == "planning":
                bound_question = _bound_research_question(store)
                if bound_question:
                    bound_question_directive = (
                        "\nbound_research_question="
                        + json.dumps(bound_question, ensure_ascii=False)
                        + "\nPlan this exact task-bound question. The task description "
                        "label is orchestration metadata, not the research topic; "
                        "never substitute that label for bound_research_question."
                    )
            description += (
                "\n\n[RESEARCH_PRODUCER_V2]\n"
                f"phase={action['phase']}\n"
                f"stage={action['stage']}\n"
                "Return one bounded result owned by this producer. Never claim a "
                "receipt that the dedicated tools did not produce. For a revision, "
                "consume only revision_capsule, preserve accepted and unchanged "
                "work, and stop after one new immutable artifact.\n"
                f"revision_capsule={json.dumps(revision_capsule, ensure_ascii=False)}\n"
                "planner_revision_checkpoint="
                f"{json.dumps(planner_revision_checkpoint, ensure_ascii=False)}\n"
                f"policy_preflight={json.dumps(policy_registry(stage=action['stage']), ensure_ascii=False)}\n"
                f"accepted_upstream={_upstream_context(store, action['stage'])}"
                f"{staged_directive}"
                f"{data_context_directive}"
                f"{bound_question_directive}"
                "\ncanonical_checkpoint="
                f"{_CANONICAL_CHECKPOINT_DIRECTIVE[action['stage']]}"
            )
        rewritten = request.override(
            tool_call={
                **request.tool_call,
                "args": {**args, "description": description},
            }
        )
        if not action_reserved:
            try:
                store.reserve_action(action)
            except RuntimeError as exc:
                return request, action, self._blocked(request, str(exc))
        return rewritten, action, None

    def _after(
        self,
        request: ToolCallRequest,
        action: dict[str, Any] | None,
        result: ToolMessage | Command[Any],
    ) -> ToolMessage | Command[Any]:
        if action is None or _result_failed(result):
            return result
        config = getattr(request.runtime, "config", None)
        store = store_from_config(config)
        if action["kind"] == "producer":
            producer_text = _tool_result_text(result)
            try:
                artifact = store.checkpoint_producer_result(
                    stage=action["stage"],
                    producer=action["producer"],
                    content=producer_text,
                    phase=action["phase"],
                    require_canonical_source=True,
                    revision_review_id=action.get("revision_review_id"),
                )
            except Exception as exc:
                if action["stage"] == "planning":
                    # The planner returned without a reviewable canonical
                    # artifact. Run the deterministic preflight once. A complete
                    # draft freezes without spending a remote forced-tool turn;
                    # a content-failing draft must route its issues back as a
                    # typed planner revision rather than silently counting a
                    # specialist failure, so the next attempt repairs the named
                    # sections instead of restarting from an empty brief.
                    try:
                        freeze_receipt = _freeze_validated_planner_draft(config)
                        producer_text += (
                            "\n\n[DETERMINISTIC PLANNER FREEZE]\n"
                            + json.dumps(freeze_receipt, ensure_ascii=False)
                        )
                        artifact = store.checkpoint_producer_result(
                            stage=action["stage"],
                            producer=action["producer"],
                            content=producer_text,
                            phase=action["phase"],
                            require_canonical_source=True,
                            revision_review_id=action.get("revision_review_id"),
                        )
                    except Exception as freeze_exc:
                        revision_checkpoint = None
                        try:
                            validation = _validate_planner_draft(config)
                            if validation.get("status") != "plan_ready":
                                revision_checkpoint = (
                                    _register_planner_validation_revision(
                                        config, validation
                                    )
                                )
                        except Exception:
                            revision_checkpoint = None
                        if revision_checkpoint is not None:
                            issue_count = len(revision_checkpoint.get("issues", []))
                            return _with_result_content(
                                result,
                                f"{producer_text}\n\n"
                                "[DETERMINISTIC PLANNER VALIDATION REVISION] The "
                                "complete draft failed deterministic preflight; "
                                f"{issue_count} localized issue(s) were registered "
                                "as a typed planner revision. The next planner "
                                "attempt resumes at repair_evidence_revision and "
                                "must stage replacements for only the named "
                                "sections, then commit once. checkpoint="
                                + json.dumps(revision_checkpoint, ensure_ascii=False)[
                                    :4000
                                ],
                            )
                        return self._blocked(
                            request,
                            "producer local preflight failed before Evidence review: "
                            f"{type(exc).__name__}: {exc}; deterministic planner "
                            f"freeze failed: {type(freeze_exc).__name__}: {freeze_exc}",
                        )
                else:
                    detail = f"{type(exc).__name__}: {exc}"
                    if action["stage"] == "data":
                        detail += (
                            "; missing an additional producer artifact under "
                            "work/solar_data, a non-context receipts/datasets "
                            "receipt, or outputs. Inspect the latest dedicated-tool "
                            "error and call the required adapter once on the retry"
                        )
                    return self._blocked(
                        request,
                        "producer local preflight failed before Evidence review: "
                        + detail,
                    )
            receipt = {
                "schema_version": "research-artifact-receipt-v2",
                "artifact_id": artifact["artifact_id"],
                "version": artifact["version"],
                "artifact_sha256": artifact["artifact_sha256"],
                "stage": artifact["stage"],
            }
            return _with_result_content(
                result,
                f"{producer_text}\n\n[RESEARCH_ARTIFACT_V2]\n"
                + json.dumps(receipt, ensure_ascii=False),
            )
        if action["kind"] == "review":
            target = store.review_targets(action["review_mode"])
            verdict = store.matching_verdict(
                action["review_mode"], [store.artifact_ref(item) for item in target]
            )
            if verdict is None:
                return self._blocked(
                    request,
                    "solar-evidence returned without persisting a hash-bound ReviewVerdictV2",
                )
            return _with_result_content(
                result,
                f"{_tool_result_text(result)}\n\n[REVIEW_VERDICT_V2]\n"
                + json.dumps(
                    {
                        "review_id": verdict["review_id"],
                        "decision": verdict["decision"],
                        "verdict_sha256": verdict["verdict_sha256"],
                        "next_owner": verdict["next_owner"],
                        "carry_forward_limits": verdict["carry_forward_limits"],
                    },
                    ensure_ascii=False,
                ),
            )
        return result

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        rewritten, action, blocked = self._prepare(request)
        if blocked is not None:
            return blocked
        return self._after(request, action, handler(rewritten))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        rewritten, action, blocked = await asyncio.to_thread(self._prepare, request)
        if blocked is not None:
            return blocked
        result = await handler(rewritten)
        return await asyncio.to_thread(self._after, request, action, result)


__all__ = ["ResearchReviewOrchestrationMiddleware"]
