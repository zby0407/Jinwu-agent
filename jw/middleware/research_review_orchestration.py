"""Deterministic producer/reviewer handoffs for full research runs."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import math
import re
import shutil
from collections.abc import Awaitable, Callable, Mapping, Sequence
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from research_review.policies import policy_registry

from ..research_review import (
    ResearchReviewStore,
    _atomic_write_json,
    store_from_config,
)

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
        "research-plan-v1 artifact. Call research_planner_get_brief first. For a "
        "fresh quantitative or observational plan, call "
        "research_planner_create_empirical_plan once: supply the question-specific "
        "scope, subquestions, evidence gaps, dataset needs, stage methods, and "
        "evaluation focus; the host supplies only the standard reviewed route and "
        "lifecycle fields. Use research_planner_submit_complete_draft only when the "
        "task is not an empirical full-route study. Resume a previously interrupted "
        "partial draft from draft_checkpoint.next_section with "
        "research_planner_update_draft. Once all sections exist, use "
        "research_planner_apply_revision_patch for every small cross-section repair. "
        "For a large repair, stage replacements with "
        "research_planner_stage_revision_section and atomically finish with "
        "research_planner_commit_revision_candidate; never rewrite active complete "
        "sections sequentially. A successful compact or complete submission already "
        "performed full validation; after plan_ready call "
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
        "The Supervisor has already called solar_data_open_context exactly once; "
        "do not open or rediscover the context again. Use only the supplied "
        "deterministic_data_context eligible_inputs. If it reports "
        "input_missing/must_stop, return that hash-bound blocker immediately "
        "without guessing paths. If produced_data_receipt_ref is present, that "
        "Supervisor-derived receipt is the canonical bounded Data result: inspect "
        "its stated status and return the exact receipt path without making "
        "another tool call or inventing missing observations. Otherwise persist "
        "the inspected dataset semantics and transformations in the task-local "
        "data session or receipts/datasets; return exact paths and hashes. Prose "
        "without a canonical data artifact cannot enter review."
    ),
    "hypothesis": (
        "Persist the complete candidate set with scientific_hypothesis_update_draft "
        "and call scientific_hypothesis_get_draft to obtain the current candidate "
        "pool and review guide. Then call scientific_hypothesis_review_tail and "
        "repair any reported violation against the live draft. Only after that "
        "review is current, call scientific_hypothesis_checkpoint_draft. Finish "
        "with scientific_hypothesis_get_draft so the checkpoint, its evidence "
        "binding, and the current tail review are persisted together in "
        "work/scientific_hypothesis_state.json as the canonical review source."
    ),
    "experiment_design": (
        "Validate and persist automatic-experiment-design-v1 under the exact run; "
        "stop before execution and return experiment/runs/<run_id>/design.json. "
        "For an Evidence revision (phase experiment_design_revision_from_experiment_design), "
        "call automatic_experiment_bind_request first. The service inherits the prior "
        "validated request and creates one immutable revision run. Capture its returned "
        "run_id and use the returned run_id for every subsequent experiment tool call. "
        "If binding reports RESEARCH_EXPERIMENT_SCOPE_ALREADY_BOUND, resume the run_id "
        "in that response. Inspect that revision run's inputs before creating or "
        "validating its replacement design. Never write to or validate against an "
        "earlier design run. "
        "Bind input_refs to the accepted upstream data artifact's produced files "
        "(e.g. work/solar_data/solar_precursor_cycle_features.csv and receipts "
        "under receipts/datasets/), not to the planning artifact's originally "
        "declared source paths, which are provenance records rather than "
        "task-local readable inputs."
        " When the design tests a registered hypothesis, predeclare a typed "
        "result item named hypothesis_relation with one of supports, opposes, "
        "null_result, or uncertain. Its decision rule must combine the planned "
        "effect interval, genuine out-of-sample comparison, metric agreement, "
        "influence analysis, and independent-sample adequacy."
        " Predictive or interaction designs must also predeclare the applicable "
        "diagnostics from primary_interval_low, primary_interval_high, "
        "candidate_mae, baseline_mae, candidate_rmse, baseline_rmse, "
        "out_of_sample_complete, leave_one_unit_direction_stable, "
        "independent_sample_adequate, and influential_unit_changes_conclusion."
        " After design_invalid, repair and resubmit under the same run_id; never "
        "bind a fresh run to change the preregistered sample or analysis rules. "
        "If the validation response says must_stop=true, stop immediately and "
        "preserve the run."
    ),
    "experiment_result": (
        "Execute only the accepted design, verify the real result, and finalize the "
        "run. Return experiment/runs/<run_id>/record.json and report.md; a plan or "
        "unverified preview is not an experiment result.\n"
        "For experiment_result, resume the exact accepted run_id supplied first in "
        "accepted_upstream. Do not bind a new request or create a fresh run: the "
        "accepted run already contains the reviewed "
        "request, staged input snapshot, and design. Do not call "
        "automatic_experiment_create_single_stage_design or "
        "automatic_experiment_validate_design in this phase.\n"
        "If inspect_inputs or finalize reports a terminal state, return the existing "
        "record.json and report.md immediately; do not prepare, execute, verify, bind, "
        "redesign, or finalize again after that terminal response.\n"
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
        "diagnostic), and source_artifact (an exact artifact-path string literal, "
        "a module-level or function-local string constant assigned that literal, "
        "or null; no computed path). Call prepare_attempt once with files as a JSON "
        "array. measurement values must be finite numeric values; never emit null, "
        "NaN, infinity, or a sentinel for an unavailable measurement. If a planned "
        "quantity is unavailable, preserve that fact in a typed result or warning "
        "and repair the parser or design instead of populating the measurement with "
        "a placeholder. In scientific_payload, estimate must be a finite number or null; "
        "interval and equivalence_bounds must each be [low, high] or null; "
        "sensitivity must be text or null; "
        "uncertainty_reasons must be an array of strings. Do not put explanatory "
        "prose into numeric or array fields. Then "
        "call execute_attempt on the attempt id that prepare returned; do "
        "not re-prepare after a successful prepare, and do not call execute before a "
        "prepare succeeds. Before automatic_experiment_prepare_attempt, call "
        "automatic_experiment_inspect_inputs once for the accepted run and copy the "
        "already-snapshotted response's required_worker_outputs exactly. Use its exact "
        "measurement names, result ids, endpoint ids, artifact paths, and JSON "
        "traceability keys; do not infer or rename them from prose. Inspect the "
        "actual raw delimiter and header of every declared input before authoring "
        "its parser, and add parser self-checks against factual anchors in the "
        "accepted upstream inventory. A non-empty source that yields zero relevant "
        "rows, a parser result that contradicts the accepted upstream inventory, or "
        "a required text result whose value is empty is a technical failure: raise "
        "it before emitting a scientific result. Every text-valued result_item must "
        "contain non-empty text. Inventory anchors validate the parser but never "
        "replace computation from the immutable raw inputs."
        " For a hypothesis test, return the predeclared hypothesis_relation "
        "result item from the computed decision rule; never choose its value to "
        "preserve the incoming hypothesis."
    ),
}

_MODEL_CALL_BUDGET_STOP = re.compile(
    r"^\s*Model call limits exceeded:\s*(?:run|thread) limit\b",
    re.IGNORECASE,
)


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


def _kimi_evidence_failure_summary(text: str) -> str:
    """Keep the fixed Kimi structured-submit capsule in a review failure record."""

    lines = text.splitlines()
    if not lines or lines[0].strip() != "[KIMI EVIDENCE STRUCTURED SUBMIT FAILED]":
        return ""
    expected = (
        ("event", "kimi_evidence_structured_submit_failed"),
        ("error_type", None),
        ("parsed_present", None),
        ("raw_message_present", None),
        ("fingerprint", None),
    )
    values: list[str] = []
    for index, (key, fixed_value) in enumerate(expected, start=1):
        if index >= len(lines):
            return ""
        prefix = f"{key}="
        value = lines[index].strip()
        if not value.startswith(prefix):
            return ""
        payload = value.removeprefix(prefix)
        if fixed_value is not None and payload != fixed_value:
            return ""
        if key == "error_type" and not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_.-]{0,120}", payload
        ):
            return ""
        if key in {"parsed_present", "raw_message_present"} and payload not in {
            "true",
            "false",
        }:
            return ""
        if key == "fingerprint" and not re.fullmatch(r"[0-9a-f]{64}", payload):
            return ""
        values.append(value)
    return "\n".join(values)


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
        verdict = store.matching_verdict(
            artifact["stage"], [store.artifact_ref(artifact)]
        )
        rows.append(
            {
                "artifact_id": artifact["artifact_id"],
                "stage": artifact["stage"],
                "version": artifact["version"],
                "artifact_sha256": artifact["artifact_sha256"],
                "payload": artifact["payload"],
                "limitations": artifact["limitations"],
                "review_decision": verdict["decision"] if verdict else None,
                "accepted_claims": verdict["accepted_claims"] if verdict else [],
                "carry_forward_limits": (
                    verdict["carry_forward_limits"] if verdict else []
                ),
                "interpretation": (
                    "The upstream artifact and its data/provenance boundary were "
                    "accepted. Preserve the carried limits. Do not describe it as "
                    "absent, unreviewed, or requiring regeneration. Acceptance does "
                    "not establish predictive skill or causal support for this stage."
                ),
            }
        )
    if stage == "experiment_result":
        design_artifact = None
        for artifact in store.accepted_artifacts():
            if artifact["stage"] == "experiment_design":
                design_artifact = artifact
                break
        if design_artifact is not None:
            payload = design_artifact.get("payload") or {}
            run_id = None
            for source_ref in payload.get("canonical_source_refs") or []:
                if not isinstance(source_ref, str):
                    continue
                match = re.fullmatch(
                    r"experiment/runs/([^/]+)/design\.json", source_ref
                )
                if match is not None:
                    run_id = match.group(1)
                    break
            producer_result = payload.get("producer_result", "")
            if run_id is None and isinstance(producer_result, str):
                match = re.search(
                    r"\b(question_[a-f0-9]+-[0-9]+T[0-9]+Z-[a-f0-9]+)\b",
                    producer_result,
                )
                if match is not None:
                    run_id = match.group(1)
            if run_id is not None:
                rows.insert(
                    0,
                    {
                        "artifact_id": "experiment_design-run-id",
                        "stage": "experiment_design",
                        "version": design_artifact["version"],
                        "artifact_sha256": design_artifact["artifact_sha256"],
                        "payload": {"run_id": run_id},
                        "limitations": [],
                        "instruction": (
                            "Resume this exact accepted run. Do not bind or create "
                            "another experiment run."
                        ),
                    },
                )
    encoded = json.dumps(rows, ensure_ascii=False)
    return encoded[:30_000]


def _persist_experiment_scope(
    store: ResearchReviewStore,
    action: Mapping[str, Any],
    *,
    analysis_protocol: str = "none",
    portfolio_ranking: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    stage = str(action["stage"])
    upstream_stages = {
        "experiment_design": {"planning", "data", "hypothesis"},
        "experiment_result": {
            "planning",
            "data",
            "hypothesis",
            "experiment_design",
        },
    }[stage]
    accepted_upstream_refs = [
        {
            "artifact_id": artifact["artifact_id"],
            "version": artifact["version"],
            "artifact_sha256": artifact["artifact_sha256"],
            "stage": artifact["stage"],
        }
        for artifact in store.accepted_artifacts()
        if artifact["stage"] in upstream_stages
    ]
    accepted_upstream_refs.sort(
        key=lambda ref: (
            ref["stage"],
            ref["artifact_id"],
            ref["version"],
            ref["artifact_sha256"],
        )
    )
    revision_review_id = action.get("revision_review_id")
    scope = {
        "schema_version": "research-experiment-scope-v1",
        "task_id": store.task_id,
        "stage": stage,
        "accepted_upstream_refs": accepted_upstream_refs,
        "revision_review_id": (
            revision_review_id if isinstance(revision_review_id, str) else None
        ),
        "design_validation_limit": 4,
    }
    if analysis_protocol != "none":
        scope["analysis_protocol"] = analysis_protocol
    if portfolio_ranking is not None:
        scope["portfolio_ranking"] = _minimal_portfolio_ranking_capsule(
            portfolio_ranking
        )
    path = store.root / "experiment_scope.json"
    _atomic_write_json(path, scope)
    return scope


def _data_pair_mapping_note(artifact: Mapping[str, Any]) -> str:
    """Render the receipt-bound predictor/target cycle mapping for Hypothesis."""

    payload = artifact.get("payload")
    summary = (
        payload.get("data_result_summary") if isinstance(payload, Mapping) else None
    )
    coverage = summary.get("pair_coverage") if isinstance(summary, Mapping) else None
    raw_pairs = (
        coverage.get("available_pairs") if isinstance(coverage, Mapping) else None
    )
    if not isinstance(raw_pairs, list) or not raw_pairs:
        return ""
    parsed: list[tuple[int, int]] = []
    for raw_pair in raw_pairs:
        match = re.fullmatch(r"([0-9]+)->([0-9]+)", str(raw_pair))
        if match is None:
            return ""
        parsed.append((int(match.group(1)), int(match.group(2))))

    def cycle_range(values: list[int]) -> str:
        ordered = list(dict.fromkeys(values))
        if len(ordered) > 1 and ordered == list(range(ordered[0], ordered[-1] + 1)):
            return f"{ordered[0]}-{ordered[-1]}"
        return ",".join(str(value) for value in ordered)

    pair_span = (
        raw_pairs[0]
        if len(raw_pairs) == 1
        else f"{raw_pairs[0]} through {raw_pairs[-1]}"
    )
    predictors = cycle_range([left for left, _right in parsed])
    targets = cycle_range([right for _left, right in parsed])
    next_unavailable = max(right for _left, right in parsed) + 1
    return (
        "Authoritative sample mapping from the accepted Data receipt: exact "
        f"available pairs are {pair_span}; predictor/previous cycles are "
        f"{predictors}; target cycles are {targets}. Candidate statements, scope, "
        "predictions, and null hypotheses must preserve the pair left endpoints "
        "as predictor/previous cycles and right endpoints as target cycles, and "
        f"must not shift this mapping to cycle {next_unavailable}."
    )


_HYPOTHESIS_RESULT_SECTION = re.compile(
    r"(?:关系|统计|结果|结论|敏感|比较|局限|不确定|"
    r"relationship|statistic|result|conclusion|sensitivity|comparison|"
    r"limitation|uncertainty)",
    re.IGNORECASE,
)
_SOURCE_RESTRICTED_EVIDENCE_MARKER = "[source_restricted_evidence_boundary]"
_SOURCE_RESTRICTED_HYPOTHESIS_PROTOCOLS = {
    "silso_cycle_morphology_v1",
    "solar_cycle_26_forecast_backtest_v1",
}
_SILSO_PREBOUND_RELATIONSHIPS: tuple[tuple[str, str, str], ...] = (
    (
        "cycle_length_peak",
        "cycle length vs peak strength",
        "cycle_length_vs_peak_full_stats",
    ),
    (
        "rise_time_peak",
        "rise time vs peak strength",
        "rise_time_vs_peak_full_stats",
    ),
    (
        "decline_time_peak",
        "decline time vs peak strength",
        "decline_time_vs_peak_full_stats",
    ),
)


def _silso_result_rows(materials: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Extract the three registered SILSO table rows into typed A2A evidence.

    The host has already verified the source manifest before this function is
    called.  We therefore copy only exact table rows from ``content_notes``;
    the downstream specialist never has to guess a substring or open a file.
    """

    rows: list[dict[str, Any]] = []
    for relationship_key, label, evidence_id in _SILSO_PREBOUND_RELATIONSHIPS:
        matches: list[tuple[str, str]] = []
        for material in materials:
            material_id = material.get("id")
            notes = material.get("content_notes")
            if not isinstance(material_id, str) or not isinstance(notes, str):
                continue
            found = False
            for line in notes.splitlines():
                candidate = line.strip()
                if (
                    candidate.startswith("|")
                    and label.casefold() in candidate.casefold()
                    and candidate.count("|") >= 3
                ):
                    matches.append((material_id, candidate))
                    found = True
            if not found:
                # Producer adapters may compact Markdown whitespace while
                # preserving the reviewed bytes in the source manifest.  In
                # that representation, delimit the row by the next registered
                # relationship label instead of relying on newlines.
                labels = "|".join(
                    re.escape(item[1]) for item in _SILSO_PREBOUND_RELATIONSHIPS
                )
                pattern = re.compile(
                    rf"\|\s*{re.escape(label)}\s*\|.*?(?=\|\s*(?:{labels})\s*\||\s*##|$)",
                    re.IGNORECASE | re.DOTALL,
                )
                match = pattern.search(notes)
                if match is not None:
                    candidate = " ".join(match.group(0).split())
                    if candidate.count("|") >= 3:
                        matches.append((material_id, candidate))
        if not matches:
            raise RuntimeError(
                "source-restricted SILSO request is missing the exact reviewed "
                f"table row for {relationship_key}"
            )
        # Duplicate projections of the same frozen report are harmless.  Pick
        # the first request-declared material deterministically, but reject
        # conflicting rows so an ambiguous A2A capsule cannot be released.
        unique_rows = {(material_id, excerpt) for material_id, excerpt in matches}
        excerpts = {excerpt for _material_id, excerpt in unique_rows}
        if len(excerpts) != 1:
            raise RuntimeError(
                "source-restricted SILSO request contains conflicting reviewed "
                f"rows for {relationship_key}"
            )
        material_id, excerpt = next(
            (item for item in matches if item[1] == next(iter(excerpts))),
            matches[0],
        )
        rows.append(
            {
                "relationship_key": relationship_key,
                "evidence_id": evidence_id,
                "evidence_kind": "upstream",
                "material_id": material_id,
                "excerpt": excerpt,
                "verified_support": True,
                "role": "supports",
            }
        )
    return rows


def _write_silso_evidence_seed(
    store: ResearchReviewStore,
    request: Mapping[str, Any],
    materials: Sequence[Mapping[str, Any]],
) -> str | None:
    """Persist a hash-bound, host-owned evidence seed for the SILSO route."""

    if not any(
        _SOURCE_RESTRICTED_EVIDENCE_MARKER in str(material.get("content_notes") or "")
        for material in materials
    ):
        return None
    rows = _silso_result_rows(materials)
    from scientific_hypothesis.contracts import canonical_json_sha256
    from scientific_hypothesis.harness import validate_evidence_provenance

    evidence_rows: list[dict[str, Any]] = []
    for row in rows:
        evidence = {
            key: value for key, value in row.items() if key != "relationship_key"
        }
        validate_evidence_provenance(dict(request), evidence)
        evidence_rows.append(row)
    payload = {
        "schema_version": "scientific-hypothesis-evidence-seed-v1",
        "request_sha256": canonical_json_sha256(dict(request)),
        "source_materials": [
            {
                "material_id": row["material_id"],
                "locator": next(
                    material["locator"]
                    for material in materials
                    if material.get("id") == row["material_id"]
                ),
            }
            for row in evidence_rows
        ],
        "evidence": evidence_rows,
    }
    relative = Path("work") / "research_quality" / "hypothesis_evidence_seed.json"
    _atomic_write_json(store.workspace_root / relative, payload)
    return relative.as_posix()


def _reviewed_text_result_excerpt(
    store: ResearchReviewStore,
    artifact: Mapping[str, Any],
    *,
    max_chars: int = 5_000,
) -> str:
    """Project useful facts from a hash-matched, Evidence-reviewed text source.

    An accepted artifact path alone is not an actionable A2A handoff for a
    capability-restricted Hypothesis agent.  This projection keeps the security
    boundary narrow: only Markdown files already frozen in the artifact's source
    manifest are eligible, and their current bytes must still match that manifest.
    Result-oriented sections are copied verbatim so the downstream evidence tool
    can bind an exact excerpt without receiving general filesystem access.
    """

    payload = artifact.get("payload")
    manifest = payload.get("source_manifest") if isinstance(payload, Mapping) else None
    if not isinstance(manifest, list):
        return ""
    candidates: list[tuple[str, Path]] = []
    for row in manifest:
        if not isinstance(row, Mapping):
            continue
        source_ref = row.get("source_ref")
        expected_sha256 = row.get("sha256")
        declared_bytes = row.get("bytes")
        if (
            not isinstance(source_ref, str)
            or not source_ref.endswith(".md")
            or not isinstance(expected_sha256, str)
            or not isinstance(declared_bytes, int)
        ):
            continue
        path = (store.workspace_root / source_ref).resolve()
        if (
            not path.is_relative_to(store.workspace_root.resolve())
            or not path.is_file()
            or path.stat().st_size != declared_bytes
        ):
            continue
        actual_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_sha256 != expected_sha256:
            continue
        candidates.append((source_ref, path))

    projected: list[str] = []
    remaining = max_chars
    for source_ref, path in candidates:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        headings = list(re.finditer(r"(?m)^#{2,4}\s+(.+?)\s*$", text))
        sections: list[tuple[int, str]] = []
        for index, heading in enumerate(headings):
            title = heading.group(1)
            if _HYPOTHESIS_RESULT_SECTION.search(title) is None:
                continue
            end = (
                headings[index + 1].start() if index + 1 < len(headings) else len(text)
            )
            section = text[heading.start() : end].strip()
            # Conclusions and primary result tables are more useful than a long
            # row-by-row sensitivity appendix when the A2A capsule is bounded.
            priority = 0
            if re.search(r"(?:主要结论|conclusion)", title, re.IGNORECASE):
                priority = -3
            elif re.search(r"(?:三组关系|统计|result)", title, re.IGNORECASE):
                priority = -2
            elif re.search(r"(?:比较|局限|limitation)", title, re.IGNORECASE):
                priority = -1
            sections.append((priority, section))
        sections.sort(key=lambda item: item[0])
        if not sections:
            continue
        source_header = f"Evidence-inspected source excerpt ({source_ref}):"
        block_parts = [source_header]
        block_budget = min(remaining, max_chars)
        for _priority, section in sections:
            if block_budget <= len(source_header) + 2:
                break
            available = block_budget - sum(len(part) + 2 for part in block_parts)
            if available <= 0:
                break
            block_parts.append(section[:available])
        block = "\n\n".join(block_parts).strip()
        if len(block) > remaining:
            block = block[:remaining]
        if block:
            projected.append(block)
            remaining -= len(block) + 2
        if remaining <= 0:
            break
    return "\n\n".join(projected)


def _compose_hypothesis_material_notes(
    *,
    introduction: str,
    reviewed_excerpt: str,
    claim_text: str,
    mapping_note: str,
    limits: Sequence[str],
) -> str:
    """Fit an actionable evidence capsule while preserving accepted limits."""

    tail = ""
    if limits:
        tail = "Accepted limitations:\n- " + "\n- ".join(limits)
    middle = "\n\n".join(
        value for value in (reviewed_excerpt, claim_text, mapping_note) if value
    )
    separators = 4 if middle and tail else 2
    middle_budget = max(0, 8_000 - len(introduction) - len(tail) - separators)
    parts = [introduction]
    if middle_budget and middle:
        parts.append(middle[:middle_budget])
    if tail:
        parts.append(tail)
    return "\n\n".join(parts)[:8_000]


def _write_hypothesis_request(
    store: ResearchReviewStore, *, analysis_protocol: str = "none"
) -> str:
    """Bind accepted Data, prior hypotheses, and verified experiment results."""

    question = _bound_research_question(store)
    if not question:
        raise RuntimeError("the task has no bound research question")
    materials: list[dict[str, Any]] = []
    accepted = store.accepted_artifacts()
    for artifact in accepted:
        if artifact["stage"] not in {"data", "experiment_result"}:
            continue
        stage = artifact["stage"]
        verdict = store.matching_verdict(stage, [store.artifact_ref(artifact)])
        if verdict is None:
            continue
        claim_text = "\n\n".join(
            str(claim.get("text") or "").strip()
            for claim in artifact.get("claims", [])
            if isinstance(claim, Mapping) and str(claim.get("text") or "").strip()
        )
        limits = [
            str(value).strip()
            for value in (
                list(artifact.get("limitations") or [])
                + list(verdict.get("carry_forward_limits") or [])
            )
            if str(value).strip()
        ]
        if stage == "data":
            introduction = (
                "Evidence review accepted this Data artifact's declared data and "
                "provenance boundary. This establishes the inspected feature "
                "product, not predictive skill or a causal mechanism."
            )
            mapping_note = _data_pair_mapping_note(artifact)
            reviewed_excerpt = _reviewed_text_result_excerpt(store, artifact)
            material_kind = "data_feature"
            summary = None
        else:
            introduction = (
                "Evidence review accepted the persisted experiment record within "
                "its declared method and uncertainty boundary."
            )
            mapping_note = ""
            reviewed_excerpt = _reviewed_text_result_excerpt(store, artifact)
            material_kind = "experiment_result"
            summary = (artifact.get("payload") or {}).get("experiment_result_summary")
            if not isinstance(summary, Mapping):
                raise RuntimeError(
                    "accepted experiment result has no projected verified summary"
                )
            summary = {
                key: summary[key]
                for key in (
                    "execution_completed",
                    "outcome",
                    "metrics",
                    "uncertainty_notes",
                    "record_sha256",
                )
            }
        if analysis_protocol in _SOURCE_RESTRICTED_HYPOTHESIS_PROTOCOLS:
            introduction = (
                f"{_SOURCE_RESTRICTED_EVIDENCE_MARKER} The accepted upstream "
                "materials are the complete evidence boundary for this independent "
                "statistical task; cached literature and discovery are not applicable. "
                + introduction
            )
        notes = _compose_hypothesis_material_notes(
            introduction=introduction,
            reviewed_excerpt=reviewed_excerpt,
            claim_text=claim_text,
            mapping_note=mapping_note,
            limits=limits,
        )
        relative = (
            store.root
            / "artifacts"
            / artifact["artifact_id"]
            / f"v{artifact['version']:04d}.json"
        ).relative_to(store.workspace_root)
        materials.append(
            {
                "id": f"accepted_{artifact['artifact_id'].replace('-', '_')}_v{artifact['version']}",
                "material_kind": material_kind,
                "title": f"Accepted {artifact['artifact_id']} version {artifact['version']}",
                "locator": relative.as_posix(),
                "content_notes": notes,
                "experiment_summary": summary,
            }
        )
    priors: list[dict[str, Any]] = []
    for artifact in accepted:
        if artifact["stage"] != "hypothesis":
            continue
        for claim in artifact.get("claims") or []:
            if not isinstance(claim, Mapping):
                continue
            claim_id = str(claim.get("claim_id") or "").strip()
            statement = str(claim.get("text") or "").strip()
            if not claim_id or not statement:
                continue
            priors.append(
                {
                    "id": claim_id,
                    "statement": statement[:1_000],
                    "version": artifact["version"],
                    "notes": (
                        f"Accepted confidence={claim.get('confidence', 'unknown')}; "
                        f"scope={claim.get('scope', 'not recorded')}"
                    )[:2_000],
                }
            )
    request = {
        "schema_version": "scientific-hypothesis-request-v1",
        "task_name": f"hypothesis_{store.task_id}",
        "research_question": question,
        "upstream_materials": materials,
        "prior_hypotheses": priors[:12],
        "max_candidates": 3,
    }
    from scientific_hypothesis.contracts import validate_hypothesis_request

    validated = validate_hypothesis_request(request)
    relative = Path("work") / "research_quality" / "hypothesis_request.json"
    target = store.workspace_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(validated, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if analysis_protocol == "silso_cycle_morphology_v1":
        _write_silso_evidence_seed(store, validated, materials)
    return relative.as_posix()


def _stage_data_produced_inputs(store: ResearchReviewStore) -> list[str]:
    """Copy the accepted data artifact's produced files into the run workspace
    inputs/ directory so the experiment contract can resolve them as input_refs.

    Returns the list of staged inputs/... relative paths. Data-produced files
    use a separate content-addressed namespace, so they cannot replace
    manifest-bound user inputs. Existing identical targets are left untouched;
    conflicting targets fail explicitly."""

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
    manifest_records = store._current_manifest_input_records() or []
    manifest_source_refs = {
        str(record.get("path"))
        for record in manifest_records
        if isinstance(record, Mapping) and isinstance(record.get("path"), str)
    }

    def add_workspace_candidate(
        candidate: Path,
        *,
        expected_sha256: str | None = None,
        expected_bytes: int | None = None,
    ) -> None:
        """Keep only regular files reached through a non-symlink workspace path."""

        try:
            relative = candidate.relative_to(workspace)
            workspace_resolved = workspace.resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            return
        if ".." in relative.parts:
            return
        cursor = workspace
        for part in relative.parts:
            cursor /= part
            if cursor.is_symlink():
                return
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            return
        if not resolved.is_relative_to(workspace_resolved) or not resolved.is_file():
            return
        if expected_bytes is not None and resolved.stat().st_size != expected_bytes:
            raise RuntimeError(
                "accepted Data source size changed before experiment staging: "
                f"{relative.as_posix()}"
            )
        if expected_sha256 is not None:
            digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
            if digest != expected_sha256:
                raise RuntimeError(
                    "accepted Data source hash changed before experiment staging: "
                    f"{relative.as_posix()}"
                )
        candidates.append(resolved)

    for ref in payload.get("canonical_source_refs") or []:
        if isinstance(ref, str) and ref not in manifest_source_refs:
            add_workspace_candidate(workspace / ref)
    # The immutable Data artifact records every inspected source, including
    # receipt-bound derived outputs, in source_manifest.  Stage those exact
    # bytes as experiment inputs instead of relying on free-form producer prose
    # or limiting the handoff to receipts alone.
    for record in payload.get("source_manifest") or []:
        if not isinstance(record, Mapping):
            continue
        ref = record.get("source_ref")
        digest = record.get("sha256")
        size = record.get("bytes")
        if not isinstance(ref, str) or not isinstance(digest, str):
            continue
        # Manifest-bound inputs retain their original task-local path (or their
        # inputs/project content-addressed path below).  source_manifest also
        # records them for provenance, but copying the same bytes again into
        # data_artifacts would create two input ids for one source.
        if ref in manifest_source_refs:
            continue
        add_workspace_candidate(
            workspace / ref,
            expected_sha256=digest,
            expected_bytes=size if isinstance(size, int) else None,
        )
    producer_result = payload.get("producer_result") or ""
    if isinstance(producer_result, str):
        for match in re.finditer(r"work/solar_data/[\w./-]+\.csv", producer_result):
            add_workspace_candidate(workspace / match.group(0))
    for relative in (
        "work/solar_data/solar_cycle_pair_analysis_table.csv",
        "receipts/datasets/solar_cycle_pair_analysis_table.json",
    ):
        add_workspace_candidate(workspace / relative)
    inputs_dir.mkdir(parents=True, exist_ok=True)
    if manifest_records:
        for record in manifest_records:
            source_ref = str(record["path"])
            if record.get("source_group") == "inputs":
                if source_ref != "inputs/_staged.json":
                    staged.append(source_ref)
                continue
            if record.get("source_group") != "project_inputs":
                continue
            source = store._resolve_project_data_manifest_path(source_ref)
            if source is None:
                raise RuntimeError(
                    "accepted project input cannot be resolved for staging: "
                    f"{source_ref}"
                )
            digest = str(record["sha256"])
            target = inputs_dir / "project" / f"{digest[:16]}-{source.name}"
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and (
                not target.is_file() or target.read_bytes() != source.read_bytes()
            ):
                raise RuntimeError(
                    "content-addressed staging conflict for "
                    f"{target.relative_to(workspace).as_posix()}"
                )
            if not target.exists():
                shutil.copyfile(source, target)
            staged.append(target.relative_to(workspace).as_posix())
    for source in candidates:
        payload = source.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        relative = Path("inputs") / "data_artifacts" / f"{digest[:16]}-{source.name}"
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if not target.is_file() or target.read_bytes() != payload:
                raise RuntimeError(
                    f"content-addressed staging conflict for {relative.as_posix()}"
                )
        else:
            shutil.copyfile(source, target)
        staged.append(relative.as_posix())
    staged = list(dict.fromkeys(staged))
    if staged:
        sidecar = {
            "schema_version": "automatic-experiment-input-refs-v1",
            "input_refs": [
                {
                    "id": f"input_{index:02d}",
                    "path": path,
                    "description": (
                        "Accepted Data-stage input, hash-staged by the research "
                        "state machine."
                    ),
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
            elif subagent_type == "solar-evidence" and stripped.startswith(
                "[RESEARCH REVIEW BLOCKED] solar-evidence returned without "
                "persisting a hash-bound ReviewVerdictV2"
            ):
                # A reviewer that returns without the atomic assessment/quality/
                # verdict round has failed its required contract. Count this as
                # a specialist failure so the Supervisor cannot retry the same
                # non-progressing Evidence delegation indefinitely.
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

    from ..research_protocols import (
        SILSO_CYCLE_REPRODUCTION_PROTOCOL,
        SILSO_CYCLE_MORPHOLOGY_PROTOCOL,
        SOLAR_CYCLE_26_READINESS_PROTOCOL,
        SOLAR_CYCLE_26_FORECAST_BACKTEST_PROTOCOL,
        SOLAR_POLAR_PRECURSOR_PROTOCOL,
        required_dataset_ids_for_protocol,
    )
    from ..tools.solar_feature import (
        _eligible_input_records,
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

    def open_context() -> dict[str, Any]:
        if route_kind == "full":
            raw = solar_data_open_context.func(
                analysis_protocol=analysis_protocol, config=config
            )
            opened = json.loads(raw)
        else:
            opened = open_bounded_solar_data_context(
                config, analysis_protocol=analysis_protocol
            )
        if not isinstance(opened, dict):
            raise RuntimeError("solar_data_open_context returned a non-object payload")
        return opened

    supported_protocols = {
        SILSO_CYCLE_REPRODUCTION_PROTOCOL,
        SILSO_CYCLE_MORPHOLOGY_PROTOCOL,
        SOLAR_CYCLE_26_READINESS_PROTOCOL,
        SOLAR_CYCLE_26_FORECAST_BACKTEST_PROTOCOL,
        SOLAR_POLAR_PRECURSOR_PROTOCOL,
    }
    if (
        binding is not None
        and not binding.legacy
        and analysis_protocol in supported_protocols
    ):
        available_ids = {
            str(item.get("dataset_id"))
            for item in _eligible_input_records(config)  # type: ignore[arg-type]
            if isinstance(item.get("dataset_id"), str)
        }
        missing_ids = [
            dataset_id
            for dataset_id in required_dataset_ids_for_protocol(analysis_protocol)
            if dataset_id not in available_ids
        ]
        if missing_ids:
            from ..solar_data_catalog import acquire_authoritative_solar_data

            try:
                acquire_authoritative_solar_data(
                    binding.base_workspace,
                    project_id=binding.project_id,
                    dataset_ids=missing_ids,
                )
            except (OSError, ValueError, RuntimeError) as exc:
                detail = " ".join(str(exc).split())[:500]
                raise RuntimeError(
                    "authoritative solar data acquisition failed before Data dispatch: "
                    f"{detail}"
                ) from exc
            ensure_thread_workspace(
                binding.thread_id,
                binding.base_workspace,
                project_id=binding.project_id,
            )
    payload = open_context()
    if not isinstance(payload, dict):
        raise RuntimeError("solar_data_open_context returned a non-object payload")
    if payload.get("status") == "error":
        raise RuntimeError(
            str(payload.get("error_message") or "solar data context preflight failed")
        )
    if payload.get("schema_version") != "solar-data-context-v1":
        raise RuntimeError("solar data context preflight returned an invalid schema")
    return payload


_SOLAR_CYCLE_SOURCE_COLUMNS = {
    "cycle_number",
    "minimum_date",
    "maximum_date",
    "peak_smoothed_sunspot_number",
    "polar_field_proxy_gauss",
    "polar_field_proxy_sem_gauss",
    "north_source",
    "south_source",
    "predictor_cutoff_decimal_year",
}

_SOLAR_REQUESTED_PAIR_IDS = [f"{cycle}->{cycle + 1}" for cycle in range(14, 24)]


def _solar_month_ordinal(value: str, field: str) -> int:
    match = re.fullmatch(r"(\d{4})-(\d{2})", value.strip())
    if match is None:
        raise ValueError(f"{field} must use YYYY-MM")
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        raise ValueError(f"{field} has an invalid month")
    return year * 12 + month - 1


def _solar_month_from_ordinal(ordinal: int) -> str:
    year, month_index = divmod(ordinal, 12)
    return f"{year:04d}-{month_index + 1:02d}"


def _solar_month_decimal(ordinal: int) -> float:
    year, month_index = divmod(ordinal, 12)
    return year + (month_index + 0.5) / 12.0


def _finite_cycle_value(row: Mapping[str, str], field: str) -> float:
    try:
        value = float(row[field])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    return value


def _optional_finite_cycle_value(row: Mapping[str, str], field: str) -> float | None:
    raw = str(row.get(field) or "").strip()
    return _finite_cycle_value(row, field) if raw else None


def _measurement_regime(row: Mapping[str, str]) -> str:
    sources = {
        str(row.get(field, "")).strip().upper()
        for field in ("north_source", "south_source")
    }
    if sources == {"MWO"}:
        return "MWO_proxy"
    if sources == {"WSO"}:
        return "WSO_magnetograph"
    return "mixed_or_unclassified"


def _solar_cycle_pair_analysis_from_path(source_path: Path) -> list[dict[str, Any]]:
    """Construct temporally ordered N-to-N+1 analysis rows from a cycle table."""

    with source_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        missing = sorted(_SOLAR_CYCLE_SOURCE_COLUMNS - columns)
        if missing:
            raise ValueError(
                "solar precursor cycle table lacks required columns: "
                + ", ".join(missing)
            )
        source_rows = list(reader)
    if len(source_rows) < 2:
        raise ValueError("solar precursor cycle table needs at least two cycles")
    try:
        source_rows.sort(key=lambda row: int(row["cycle_number"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("cycle_number must be an integer") from exc
    cycle_numbers = [int(row["cycle_number"]) for row in source_rows]
    if len(set(cycle_numbers)) != len(cycle_numbers):
        raise ValueError("cycle_number values must be unique")

    pairs: list[dict[str, Any]] = []
    for start, ending in pairwise(source_rows):
        predictor_cycle = int(start["cycle_number"])
        target_cycle = int(ending["cycle_number"])
        if target_cycle != predictor_cycle + 1:
            raise ValueError(
                "solar precursor cycle table has a non-consecutive cycle boundary"
            )
        start_minimum = _solar_month_ordinal(start["minimum_date"], "minimum_date")
        ending_minimum = _solar_month_ordinal(ending["minimum_date"], "minimum_date")
        length_months = ending_minimum - start_minimum
        if length_months <= 0:
            raise ValueError("adjacent cycle minima must be strictly increasing")
        start_minimum_decimal = _solar_month_decimal(start_minimum)
        predictor_cutoff = _finite_cycle_value(ending, "predictor_cutoff_decimal_year")
        north_raw = str(ending.get("north_measurement_date") or "").strip()
        south_raw = str(ending.get("south_measurement_date") or "").strip()
        measurement_dates_observed = bool(north_raw and south_raw)
        north_measurement = (
            _finite_cycle_value(ending, "north_measurement_date")
            if north_raw
            else predictor_cutoff
        )
        south_measurement = (
            _finite_cycle_value(ending, "south_measurement_date")
            if south_raw
            else predictor_cutoff
        )
        peak_ordinal = _solar_month_ordinal(ending["maximum_date"], "maximum_date")
        target_availability_ordinal = peak_ordinal + 6
        issue_ordinal = ending_minimum + 6
        issue_decimal = _solar_month_decimal(issue_ordinal)
        if not (
            start_minimum_decimal < north_measurement <= predictor_cutoff
            and start_minimum_decimal < south_measurement <= predictor_cutoff
            and predictor_cutoff <= issue_decimal + 1e-5
            and ending_minimum < peak_ordinal
        ):
            raise ValueError("solar cycle pair violates the required temporal order")
        sensitivity_start = str(
            ending.get("minimum_date_sensitivity_start") or ""
        ).strip()
        sensitivity_end = str(ending.get("minimum_date_sensitivity_end") or "").strip()
        sensitivity_span_raw = str(
            ending.get("minimum_date_sensitivity_span_months") or ""
        ).strip()
        sensitivity_span: int | None = None
        if sensitivity_start or sensitivity_end or sensitivity_span_raw:
            if not (sensitivity_start and sensitivity_end and sensitivity_span_raw):
                raise ValueError("minimum-date sensitivity fields must be complete")
            sensitivity_start_ordinal = _solar_month_ordinal(
                sensitivity_start, "minimum_date_sensitivity_start"
            )
            sensitivity_end_ordinal = _solar_month_ordinal(
                sensitivity_end, "minimum_date_sensitivity_end"
            )
            sensitivity_span = int(sensitivity_span_raw)
            if not (
                sensitivity_start_ordinal <= ending_minimum <= sensitivity_end_ordinal
                and sensitivity_span
                == sensitivity_end_ordinal - sensitivity_start_ordinal
            ):
                raise ValueError("minimum-date sensitivity interval is inconsistent")
        pairs.append(
            {
                "predictor_cycle_n": predictor_cycle,
                "target_cycle_n_plus_1": target_cycle,
                "cycle_start_minimum_date": start["minimum_date"],
                "cycle_end_minimum_date": ending["minimum_date"],
                "cycle_length_months": length_months,
                "cycle_length_years": round(length_months / 12.0, 6),
                "previous_cycle_amplitude": _optional_finite_cycle_value(
                    start, "peak_smoothed_sunspot_number"
                ),
                "previous_cycle_amplitude_sigma": _optional_finite_cycle_value(
                    start, "peak_smoothed_sunspot_number_sigma"
                ),
                "previous_cycle_peak_date": (
                    str(start.get("maximum_date") or "").strip() or None
                ),
                "polar_field_at_ending_minimum_gauss": _finite_cycle_value(
                    ending, "polar_field_proxy_gauss"
                ),
                "polar_field_sem_gauss": _finite_cycle_value(
                    ending, "polar_field_proxy_sem_gauss"
                ),
                "polar_field_source_cycle_row": target_cycle,
                "measurement_regime": _measurement_regime(ending),
                "north_measurement_date": north_measurement,
                "south_measurement_date": south_measurement,
                "polar_field_predictor_cutoff_decimal_year": predictor_cutoff,
                "polar_field_window_start_decimal_year": (
                    _optional_finite_cycle_value(
                        ending, "predictor_window_start_decimal_year"
                    )
                ),
                "polar_field_window_end_decimal_year": (
                    _optional_finite_cycle_value(
                        ending, "predictor_window_end_decimal_year"
                    )
                ),
                "prediction_issue_date": _solar_month_from_ordinal(issue_ordinal),
                "minimum_confirmation_lag_months": 6,
                "next_cycle_amplitude": _finite_cycle_value(
                    ending, "peak_smoothed_sunspot_number"
                ),
                "next_cycle_amplitude_sigma": _optional_finite_cycle_value(
                    ending, "peak_smoothed_sunspot_number_sigma"
                ),
                "next_cycle_peak_date": ending["maximum_date"],
                "cycle_end_minimum_sensitivity_start": sensitivity_start or None,
                "cycle_end_minimum_sensitivity_end": sensitivity_end or None,
                "cycle_end_minimum_sensitivity_span_months": sensitivity_span,
                "target_availability_date": _solar_month_from_ordinal(
                    target_availability_ordinal
                ),
                "target_available_at_issue_time": (
                    target_availability_ordinal <= issue_ordinal
                ),
                "measurement_dates_directly_observed": measurement_dates_observed,
                "temporal_order_validated": measurement_dates_observed,
                "independent_sample_unit": "solar_cycle_pair",
                "rolling_origin_eligible": True,
                "training_boundary_required": True,
                "information_set": (
                    "Adjacent minima and the declared ending-minimum polar window "
                    "available by the issue cutoff only; the target-cycle peak is "
                    "excluded at issue time."
                ),
            }
        )
    for row in pairs:
        row["pair_sample_count"] = len(pairs)
        row["n_eff_upper_bound"] = len(pairs)
        row["n_eff_status"] = "bounded_not_estimated"
    return pairs


def _persist_solar_cycle_pair_analysis_table(
    config: object, data_context: Mapping[str, Any]
) -> str:
    """Persist a self-describing historical cycle-pair table and receipt."""

    from ..workspaces import binding_from_config

    binding = binding_from_config(config)  # type: ignore[arg-type]
    if binding is None:
        raise RuntimeError("cycle-pair construction has no task workspace binding")
    root = Path(binding.workspace)
    source_ref = Path("work/solar_data/solar_precursor_cycle_features.csv")
    source_path = root / source_ref
    precursor_receipt_path = root / "receipts/datasets/solar_precursor_cycle_table.json"
    if not source_path.is_file() or not precursor_receipt_path.is_file():
        raise RuntimeError(
            "cycle-pair construction requires the precursor table and its receipt"
        )
    precursor_receipt = json.loads(precursor_receipt_path.read_text(encoding="utf-8"))
    if (
        not isinstance(precursor_receipt, Mapping)
        or precursor_receipt.get("schema_version")
        not in {
            "solar-precursor-cycle-table-v1",
            "solar-precursor-cycle-table-v2",
        }
        or precursor_receipt.get("status") != "verified"
    ):
        raise RuntimeError("solar precursor cycle receipt has an invalid schema")
    if precursor_receipt.get("schema_version") == "solar-precursor-cycle-table-v2" and (
        precursor_receipt.get("producer") != "solar-data"
        or precursor_receipt.get("task_id") != binding.thread_id
    ):
        raise RuntimeError("solar precursor cycle receipt has an invalid task binding")
    source_output = next(
        (
            item
            for item in precursor_receipt.get("outputs", [])
            if isinstance(item, Mapping) and item.get("path") == source_ref.as_posix()
        ),
        None,
    )
    source_bytes = source_path.read_bytes()
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    if not (
        isinstance(source_output, Mapping)
        and source_output.get("sha256") == source_sha256
        and source_output.get("bytes") == len(source_bytes)
    ):
        raise RuntimeError("solar precursor cycle table does not match its receipt")
    rows = _solar_cycle_pair_analysis_from_path(source_path)
    if not rows:
        raise RuntimeError("solar precursor cycle table produced no adjacent pairs")

    table_ref = Path("work/solar_data/solar_cycle_pair_analysis_table.csv")
    table_path = root / table_ref
    table_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0])
    temporary_table = table_path.with_suffix(".csv.tmp")
    with temporary_table.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    if table_path.is_file() and table_path.read_bytes() == temporary_table.read_bytes():
        temporary_table.unlink()
    else:
        temporary_table.replace(table_path)

    source_snapshot = str(
        precursor_receipt.get("created_at")
        or data_context.get("created_at")
        or "not_recorded"
    )
    regimes = sorted({str(row["measurement_regime"]) for row in rows})
    inherited_limits = precursor_receipt.get("limitations")
    limitations = (
        [str(item) for item in inherited_limits if isinstance(item, str)]
        if isinstance(inherited_limits, list)
        else []
    )
    small_sample_limit = (
        f"Only {len(rows)} adjacent solar-cycle pairs are available; model "
        "complexity and uncertainty must reflect this sample size."
    )
    if small_sample_limit not in limitations:
        limitations.append(small_sample_limit)
    available_pairs = [
        f"{row['predictor_cycle_n']}->{row['target_cycle_n_plus_1']}" for row in rows
    ]
    unavailable_pairs = sorted(set(_SOLAR_REQUESTED_PAIR_IDS) - set(available_pairs))
    inherited_gaps = precursor_receipt.get("gaps")
    gaps = (
        [dict(item) for item in inherited_gaps if isinstance(item, Mapping)]
        if isinstance(inherited_gaps, list)
        else []
    )
    if unavailable_pairs:
        gaps.append(
            {
                "code": "REQUESTED_CYCLE_PAIRS_UNAVAILABLE",
                "details": unavailable_pairs,
            }
        )
    measurement_dates_verified = all(
        row["measurement_dates_directly_observed"] is True for row in rows
    )
    if not measurement_dates_verified:
        gaps.append(
            {
                "code": "PREDICTOR_MEASUREMENT_DATES_NOT_VERIFIED",
                "status": "unavailable",
                "details": (
                    "At least one source row lacks direct north/south measurement "
                    "dates; pair count alone cannot verify temporal ordering."
                ),
            }
        )
    field_types = {
        "predictor_cycle_n": "integer",
        "target_cycle_n_plus_1": "integer",
        "cycle_start_minimum_date": "year_month",
        "cycle_end_minimum_date": "year_month",
        "cycle_length_months": "integer",
        "cycle_length_years": "number",
        "previous_cycle_amplitude": "number",
        "previous_cycle_amplitude_sigma": "number",
        "previous_cycle_peak_date": "year_month",
        "polar_field_at_ending_minimum_gauss": "number",
        "polar_field_sem_gauss": "number",
        "polar_field_source_cycle_row": "integer",
        "measurement_regime": "string",
        "north_measurement_date": "decimal_year",
        "south_measurement_date": "decimal_year",
        "polar_field_predictor_cutoff_decimal_year": "decimal_year",
        "polar_field_window_start_decimal_year": "decimal_year",
        "polar_field_window_end_decimal_year": "decimal_year",
        "prediction_issue_date": "year_month",
        "minimum_confirmation_lag_months": "integer",
        "next_cycle_amplitude": "number",
        "next_cycle_amplitude_sigma": "number",
        "next_cycle_peak_date": "year_month",
        "cycle_end_minimum_sensitivity_start": "year_month",
        "cycle_end_minimum_sensitivity_end": "year_month",
        "cycle_end_minimum_sensitivity_span_months": "integer",
        "target_availability_date": "year_month",
        "target_available_at_issue_time": "boolean",
        "measurement_dates_directly_observed": "boolean",
        "temporal_order_validated": "boolean",
        "independent_sample_unit": "string",
        "rolling_origin_eligible": "boolean",
        "training_boundary_required": "boolean",
        "information_set": "string",
        "pair_sample_count": "integer",
        "n_eff_upper_bound": "integer",
        "n_eff_status": "string",
    }
    table_bytes = table_path.read_bytes()
    table_sha256 = hashlib.sha256(table_bytes).hexdigest()
    dataset_ids = [
        str(value)
        for value in precursor_receipt.get("dataset_ids", [])
        if isinstance(value, str) and value
    ]
    if not dataset_ids:
        dataset_ids = list(
            dict.fromkeys(
                str(item["dataset_id"])
                for item in precursor_receipt.get("input_refs", [])
                if isinstance(item, Mapping)
                and isinstance(item.get("dataset_id"), str)
                and str(item["dataset_id"]).strip()
            )
        )
    sign_convention = precursor_receipt.get("sign_convention")
    if not isinstance(sign_convention, Mapping):
        sign_convention = {
            "polar_field_at_ending_minimum_gauss": (
                "inherits the precursor product convention; the legacy receipt "
                "does not state its source-header basis"
            )
        }
    payload = {
        "schema_version": "solar-cycle-pair-analysis-table-v2",
        "receipt_type": "solar_cycle_pair_analysis_table",
        "status": (
            "verified"
            if not unavailable_pairs and measurement_dates_verified
            else "partial"
        ),
        "analysis_status": (
            "analysis_table_ready"
            if not unavailable_pairs and measurement_dates_verified
            else "analysis_table_incomplete"
        ),
        "producer": "solar-data",
        "task_id": binding.thread_id,
        "source_ref": source_ref.as_posix(),
        "source_receipt_ref": ("receipts/datasets/solar_precursor_cycle_table.json"),
        "source_sha256": source_sha256,
        "source_receipt_sha256": hashlib.sha256(
            precursor_receipt_path.read_bytes()
        ).hexdigest(),
        "input_refs": [
            {
                "path": source_ref.as_posix(),
                "bytes": len(source_bytes),
                "sha256": source_sha256,
            },
            {
                "path": "receipts/datasets/solar_precursor_cycle_table.json",
                "bytes": precursor_receipt_path.stat().st_size,
                "sha256": hashlib.sha256(
                    precursor_receipt_path.read_bytes()
                ).hexdigest(),
            },
        ],
        "source_snapshot_time": source_snapshot,
        "observation_cutoff": max(str(row["next_cycle_peak_date"]) for row in rows),
        "prediction_issue_rule": (
            "six months after the ending minimum, when the centered-smoothed "
            "minimum can be confirmed"
        ),
        "output_ref": table_ref.as_posix(),
        "outputs": [
            {
                "path": table_ref.as_posix(),
                "bytes": len(table_bytes),
                "sha256": table_sha256,
            }
        ],
        "dataset_ids": dataset_ids,
        "column_schema": [
            {
                "name": name,
                "type": field_types.get(name, "string"),
                "nullable": any(row.get(name) is None for row in rows),
            }
            for name in fieldnames
        ],
        "units": {
            "cycle_length_months": "month",
            "cycle_length_years": "year",
            "previous_cycle_amplitude": "international_sunspot_number",
            "previous_cycle_amplitude_sigma": "international_sunspot_number",
            "polar_field_at_ending_minimum_gauss": "gauss",
            "polar_field_sem_gauss": "gauss",
            "north_measurement_date": "decimal_year",
            "south_measurement_date": "decimal_year",
            "polar_field_predictor_cutoff_decimal_year": "decimal_year",
            "polar_field_window_start_decimal_year": "decimal_year",
            "polar_field_window_end_decimal_year": "decimal_year",
            "next_cycle_amplitude": "international_sunspot_number",
            "next_cycle_amplitude_sigma": "international_sunspot_number",
            "cycle_end_minimum_sensitivity_span_months": "month",
        },
        "sign_convention": dict(sign_convention),
        "row_count": len(rows),
        "predictor_cycles": [row["predictor_cycle_n"] for row in rows],
        "target_cycles": [row["target_cycle_n_plus_1"] for row in rows],
        "pair_coverage": {
            "requested_pairs": _SOLAR_REQUESTED_PAIR_IDS,
            "available_pairs": available_pairs,
            "unavailable_pairs": unavailable_pairs,
        },
        "independent_sample_unit": "solar_cycle_pair",
        "sample_size": {
            "independent_sample_unit": "solar_cycle_pair",
            "independent_sample_count": len(rows),
            "n_eff_upper_bound": len(rows),
            "n_eff_status": "bounded_not_estimated",
        },
        "measurement_regimes": regimes,
        "temporal_ordering_rule": (
            "cycle_start_minimum < north/south measurement <= predictor cutoff "
            "at cycle_end_minimum plus six months < next_cycle_peak < "
            "target_availability"
        ),
        "uncertainty_fields": {
            "reported": [
                "polar_field_sem_gauss",
                *(
                    ["previous_cycle_amplitude_sigma"]
                    if all(
                        row.get("previous_cycle_amplitude_sigma") is not None
                        for row in rows
                    )
                    else []
                ),
                *(
                    [
                        "next_cycle_amplitude_sigma",
                        "cycle_end_minimum_sensitivity_start",
                        "cycle_end_minimum_sensitivity_end",
                        "cycle_end_minimum_sensitivity_span_months",
                    ]
                    if all(
                        row.get("next_cycle_amplitude_sigma") is not None
                        and row.get("cycle_end_minimum_sensitivity_start") is not None
                        and row.get("cycle_end_minimum_sensitivity_end") is not None
                        for row in rows
                    )
                    else []
                ),
            ],
            "not_computed": [
                "dependence_adjusted_n_eff",
                *(
                    []
                    if all(
                        row.get("next_cycle_amplitude_sigma") is not None
                        and row.get("cycle_end_minimum_sensitivity_start") is not None
                        and row.get("cycle_end_minimum_sensitivity_end") is not None
                        for row in rows
                    )
                    else [
                        "next_cycle_amplitude_uncertainty",
                        "cycle_minimum_date_uncertainty",
                    ]
                ),
            ],
            "interpretation": (
                "Reported SILSO sigma and minimum-date sensitivity intervals "
                "describe observational dispersion and label sensitivity; they "
                "are not calibrated confidence intervals."
            ),
        },
        "method": {
            "cycle_length": "ending minimum minus starting minimum",
            "polar_predictor": (
                "mean polar field in the declared plus/minus six-month window "
                "around the ending minimum of cycle N, with any documented "
                "sparse-window fallback inherited from the precursor receipt"
            ),
            "target": "centered-smoothed peak amplitude of cycle N+1",
            "target_uncertainty": (
                "SILSO smoothed observational sigma at the selected target peak"
            ),
            "minimum_date_uncertainty": (
                "SILSO-dispersion sensitivity interval inherited from the "
                "precursor table"
            ),
            "target_blinding": (
                "target peak excluded from the information set at prediction issue"
            ),
            "evaluation_boundary": (
                "rolling-origin folds must train only on earlier cycle pairs"
            ),
        },
        "gaps": gaps,
        "limitations": limitations,
        "created_at": source_snapshot,
    }
    receipt_ref = Path("receipts/datasets/solar_cycle_pair_analysis_table.json")
    receipt_path = root / receipt_ref
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if (
        not receipt_path.is_file()
        or receipt_path.read_text(encoding="utf-8") != rendered
    ):
        temporary_receipt = receipt_path.with_suffix(".json.tmp")
        temporary_receipt.write_text(rendered, encoding="utf-8")
        temporary_receipt.replace(receipt_path)
    return receipt_ref.as_posix()


def _ensure_solar_cycle_pair_analysis_table(config: object) -> str | None:
    """Materialize the generic cycle-pair input when accepted Data supports it."""

    from ..workspaces import binding_from_config

    binding = binding_from_config(config)  # type: ignore[arg-type]
    if binding is None:
        return None
    root = Path(binding.workspace)
    source = root / "work/solar_data/solar_precursor_cycle_features.csv"
    source_receipt = root / "receipts/datasets/solar_precursor_cycle_table.json"
    if not source.is_file() or not source_receipt.is_file():
        return None
    # Always revalidate source and output hashes. The writer is content-stable,
    # so a valid existing pair table is retained byte-for-byte while stale
    # output is regenerated from the current precursor product.
    receipt_ref = _persist_solar_cycle_pair_analysis_table(config, {})
    receipt = json.loads((root / receipt_ref).read_text(encoding="utf-8"))
    if not (
        isinstance(receipt, Mapping)
        and receipt.get("status") == "verified"
        and receipt.get("analysis_status") == "analysis_table_ready"
    ):
        raise RuntimeError(
            "solar cycle-pair analysis table is incomplete and cannot enter Experiment"
        )
    return receipt_ref


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
        "produced_data_receipt_ref": payload.get("produced_data_receipt_ref"),
    }


_PORTFOLIO_RANKING_CAPSULE_FIELDS = (
    "scientific_support",
    "research_priority",
    "strongest_null",
    "next_experiment",
    "release_boundary",
)
_PORTFOLIO_RANKING_CONSUMER_STAGES = {
    "experiment_design",
    "integration",
    "final_release",
}
_PORTFOLIO_RANKING_PATHS = (
    "work/research_quality/hypothesis_portfolio_ranking.json",
    "work/scientific_hypothesis_state.json",
    "work/scientific_hypothesis_checkpoint.json",
)


def _minimal_portfolio_ranking_capsule(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep only the five fields consumed after Hypothesis."""

    missing = [
        field for field in _PORTFOLIO_RANKING_CAPSULE_FIELDS if field not in payload
    ]
    if missing:
        raise ValueError(
            "portfolio ranking capsule is missing required fields: "
            + ", ".join(missing)
        )
    return {field: payload[field] for field in _PORTFOLIO_RANKING_CAPSULE_FIELDS}


def _portfolio_ranking_capsule_projection(
    ranking: Mapping[str, Any],
) -> dict[str, Any]:
    """Project a validated portfolio-ranking-v2 result into a small A2A capsule."""

    if all(field in ranking for field in _PORTFOLIO_RANKING_CAPSULE_FIELDS):
        return _minimal_portfolio_ranking_capsule(ranking)

    if ranking.get("schema_version") != "scientific-hypothesis-portfolio-ranking-v2":
        raise ValueError(
            "persisted portfolio ranking has an unsupported schema_version"
        )
    ranked_hypotheses = ranking.get("ranked_hypotheses")
    selected_next_experiment = ranking.get("selected_next_experiment")
    if not isinstance(ranked_hypotheses, list) or not isinstance(
        selected_next_experiment, Mapping
    ):
        raise ValueError(
            "persisted portfolio ranking must contain ranked_hypotheses and "
            "selected_next_experiment"
        )
    if not ranked_hypotheses or not all(
        isinstance(row, Mapping) for row in ranked_hypotheses
    ):
        raise ValueError("persisted ranked_hypotheses must be a non-empty object list")

    rows = [dict(row) for row in ranked_hypotheses]
    hypothesis_groups = ranking.get("hypothesis_groups")
    normalized_statements = (
        {
            str(group.get("hypothesis_id") or ""): str(
                group.get("normalized_statement") or ""
            )
            for group in hypothesis_groups
            if isinstance(group, Mapping)
        }
        if isinstance(hypothesis_groups, list)
        else {}
    )

    def ordered(rank_key: str) -> list[dict[str, Any]]:
        try:
            return sorted(rows, key=lambda row: int(row[rank_key]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"persisted portfolio ranking has an invalid {rank_key}"
            ) from exc

    support_rows = ordered("support_rank")
    priority_rows = ordered("research_priority_rank")

    def bounded_forecast_receipt_ref(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip().replace("\\", "/")
        path = Path(normalized)
        if (
            not normalized.startswith("experiment/runs/")
            or path.is_absolute()
            or ".." in path.parts
            or path.name != "forecast_experiment_receipt.json"
        ):
            return None
        return normalized

    def ranked_axis(
        ordered_rows: Sequence[Mapping[str, Any]],
        *,
        rank_key: str,
        value_key: str,
    ) -> list[dict[str, Any]]:
        projected: list[dict[str, Any]] = []
        for row in ordered_rows:
            value = row.get(value_key)
            if not isinstance(value, Mapping):
                raise ValueError(
                    f"persisted portfolio ranking has an invalid {value_key}"
                )
            projected.append(
                {
                    "hypothesis_id": row.get("hypothesis_id"),
                    "claim": normalized_statements.get(
                        str(row.get("hypothesis_id") or ""), ""
                    ),
                    "claim_type": row.get("claim_type"),
                    "evidence_status": row.get("current_evidence_status"),
                    "rank": row.get(rank_key),
                    "level": value.get("level"),
                    "rationale": value.get("rationale"),
                    "supporting_evidence": row.get("support_evidence", []),
                    "opposing_evidence": row.get("opposing_evidence", []),
                    "uncertainty": {
                        "effect": row.get("effect_uncertainty"),
                        "sensitivity": row.get("sensitivity"),
                        "limitations": row.get("key_limitations", []),
                    },
                    "falsifiability": row.get("falsifiability"),
                    "portfolio_role": row.get("portfolio_role", "challenger"),
                    "portfolio_status": row.get("portfolio_status", "challenger_pool"),
                    "forecast_origin": row.get("forecast_origin", "not_applicable"),
                    "forecast_receipt_ref": bounded_forecast_receipt_ref(
                        row.get("forecast_receipt_ref")
                    ),
                }
            )
        return projected

    return _minimal_portfolio_ranking_capsule(
        {
            "scientific_support": ranked_axis(
                support_rows,
                rank_key="support_rank",
                value_key="scientific_support",
            ),
            "research_priority": ranked_axis(
                priority_rows,
                rank_key="research_priority_rank",
                value_key="research_priority",
            ),
            "strongest_null": [
                {
                    "hypothesis_id": row.get("hypothesis_id"),
                    "statement": row.get("strongest_null_hypothesis"),
                }
                for row in support_rows
            ],
            "next_experiment": dict(selected_next_experiment),
            "release_boundary": [
                {
                    "hypothesis_id": row.get("hypothesis_id"),
                    "boundary": row.get("release_boundary"),
                }
                for row in support_rows
            ],
        }
    )


def _load_portfolio_ranking_capsule(
    workspace_root: Path, stage: str
) -> dict[str, Any] | None:
    """Load the persisted Hypothesis ranking for its downstream consumers."""

    if stage not in _PORTFOLIO_RANKING_CONSUMER_STAGES:
        return None
    for relative_path in _PORTFOLIO_RANKING_PATHS:
        path = workspace_root / relative_path
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError(f"{relative_path} must contain a JSON object")
        if relative_path.endswith("scientific_hypothesis_state.json"):
            tail_review = payload.get("tail_review")
            if not isinstance(tail_review, Mapping):
                continue
            if payload.get(
                "portfolio_ranking_candidate_pool_sha256"
            ) != tail_review.get("selected_candidate_pool_sha256") or payload.get(
                "portfolio_ranking_evidence_sha256"
            ) != tail_review.get("evidence_sha256"):
                continue
        ranking = payload.get("portfolio_ranking")
        if ranking is None and relative_path.endswith(
            "hypothesis_portfolio_ranking.json"
        ):
            ranking = payload
        if ranking is None:
            continue
        if not isinstance(ranking, Mapping):
            raise ValueError(f"{relative_path}.portfolio_ranking must be an object")
        return _portfolio_ranking_capsule_projection(ranking)
    return None


def build_a2a_handoff_envelope(
    *,
    task_id: str,
    action: Mapping[str, Any],
    specialist: str,
    analysis_protocol: str,
    accepted_upstream_refs: Sequence[str] = (),
    data_context: Mapping[str, Any] | None = None,
    portfolio_ranking: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the small structured contract passed across an internal A2A hop.

    The envelope is deliberately bounded: child agents still open the hash-bound
    artifacts and receipts themselves.  Besides routing metadata, downstream
    stages may receive only the minimal persisted portfolio-ranking capsule.
    """

    envelope = {
        "schema_version": "a2a-handoff-v1",
        "task_id": task_id,
        "stage": str(action.get("stage") or ""),
        "phase": str(action.get("phase") or ""),
        "owner": specialist,
        "revision_review_id": action.get("revision_review_id"),
        "analysis_protocol": analysis_protocol,
        "accepted_upstream_refs": list(accepted_upstream_refs),
        "data_context": (
            {
                "receipt_ref": data_context.get("receipt_ref"),
                "context_sha256": data_context.get("context_sha256"),
                "must_stop": bool(data_context.get("must_stop")),
                "eligible_inputs": [
                    {
                        "dataset_id": item.get("dataset_id"),
                        "path": item.get("path"),
                        "sha256": item.get("sha256"),
                    }
                    for item in data_context.get("eligible_inputs", [])
                    if isinstance(item, Mapping)
                ],
            }
            if isinstance(data_context, Mapping)
            else None
        ),
        "return_contract": {
            "allowed_statuses": ["accepted", "accepted_with_limits", "blocked"],
            "required_fields": [
                "status",
                "artifact_refs",
                "receipt_refs",
                "limitations",
            ],
            "hard_boundary": (
                "Open only bound inputs and accepted upstream refs; do not invent "
                "observations, measurements, artifact paths, or completion claims."
            ),
        },
    }
    if portfolio_ranking is not None:
        envelope["portfolio_ranking"] = _minimal_portfolio_ranking_capsule(
            portfolio_ranking
        )
    return envelope


def _solar_cycle_pair_analysis_producer_text(
    workspace_root: Path, receipt_ref: str
) -> str:
    """Project the bounded cycle-pair receipt into the Data result."""

    receipt_path = (workspace_root / receipt_ref).resolve()
    if not receipt_path.is_relative_to(workspace_root.resolve()):
        raise RuntimeError("cycle-pair receipt escapes the task workspace")
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise RuntimeError("cycle-pair receipt is not an object")
    return (
        "Solar-cycle pair analysis Data result (deterministic receipt projection).\n"
        f"Canonical receipt: {receipt_ref}\n"
        f"Canonical pair table: {payload.get('output_ref')}\n"
        f"Observation cutoff: {payload.get('observation_cutoff')}\n"
        f"Independent cycle-pair rows: {payload.get('row_count')}\n"
        f"Predictor cycles: {json.dumps(payload.get('predictor_cycles', []))}\n"
        f"Target cycles: {json.dumps(payload.get('target_cycles', []))}\n"
        f"Measurement regimes: {json.dumps(payload.get('measurement_regimes', []))}\n"
        f"Prediction issue rule: {payload.get('prediction_issue_rule')}\n"
        f"Limitations: {json.dumps(payload.get('limitations', []), ensure_ascii=False)}"
    )


def _solar_cycle_26_forecast_producer_text(
    workspace_root: Path, receipt_ref: str
) -> str:
    """Render a coherent claim from the verified deterministic SC26 outputs."""

    root = workspace_root.resolve()
    receipt_path = (root / receipt_ref).resolve()
    summary_path = (root / "outputs/sc26_forecast/run_summary.json").resolve()
    if not receipt_path.is_relative_to(root) or not summary_path.is_relative_to(root):
        raise RuntimeError("SC26 forecast projection escapes the task workspace")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if not isinstance(receipt, Mapping) or receipt.get("status") != "verified":
        raise RuntimeError("SC26 forecast receipt is not verified")
    if not isinstance(summary, Mapping):
        raise RuntimeError("SC26 forecast summary is not an object")
    input_ids = {
        str(item.get("dataset_id") or "")
        for item in receipt.get("inputs", [])
        if isinstance(item, Mapping)
    }
    required_ids = {
        "silso-monthly-total-v2",
        "silso-monthly-smoothed-v2",
        "silso-cycle-extrema-v2",
    }
    if input_ids != required_ids:
        raise RuntimeError("SC26 forecast receipt has an unexpected SILSO input set")
    lag = summary.get("lag_peak")
    same = summary.get("same_cycle")
    forecast = receipt.get("forecast")
    if not all(isinstance(value, Mapping) for value in (lag, same, forecast)):
        raise RuntimeError("SC26 forecast projection is missing result sections")
    lag_ci = lag.get("mae_improvement_ci95")
    same_ci = same.get("mae_improvement_ci95")
    interval = forecast.get("predictive_interval_95")
    if not all(
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes))
        and len(value) == 2
        for value in (lag_ci, same_ci, interval)
    ):
        raise RuntimeError("SC26 forecast projection has malformed intervals")
    return (
        "Verified SC26 forecast Data result. The registered SILSO v2.0 monthly "
        "total, 13-month smoothed, and official cycle-extrema inputs were bound "
        f"and reproduced into {int(receipt.get('row_count') or 0)} completed-cycle "
        "records plus Cycle 25 as predictor-only state (smoothed peak "
        f"{float(forecast.get('cycle_25_peak_used')):.1f}). In the chronological "
        f"predecessor-peak backtest, candidate MAE={float(lag.get('candidate_mae')):.3f} "
        f"versus training-mean baseline MAE={float(lag.get('baseline_mae')):.3f}; "
        f"MAE improvement={float(lag.get('mae_improvement')):.3f}, bootstrap 95% "
        f"CI=[{float(lag_ci[0]):.3f}, {float(lag_ci[1]):.3f}], so stable skill was "
        f"not established. The descriptive same-cycle improvement was "
        f"{float(same.get('mae_improvement')):.3f}, CI=[{float(same_ci[0]):.3f}, "
        f"{float(same_ci[1]):.3f}], and is not the causal primary forecast. The "
        f"formal Cycle 26 point estimate is {float(forecast.get('point_estimate')):.3f} "
        f"with 95% prediction interval [{float(interval[0]):.3f}, "
        f"{float(interval[1]):.3f}] and {forecast.get('confidence')} confidence. "
        f"Fixed bootstrap seed={receipt.get('bootstrap_seed')} and repetitions="
        f"{receipt.get('bootstrap_repetitions')}; the negative backtest result is "
        f"preserved. Canonical receipt: {receipt_ref}"
    )


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
    def _execution_transition(
        request: ToolCallRequest,
        action: Mapping[str, Any] | None,
        transition: str,
        *,
        detail: str,
    ) -> None:
        if action is None:
            return
        config = getattr(request.runtime, "config", None)
        store = store_from_config(config)
        current = store.execution_state.snapshot(stale_after_seconds=float("inf"))
        if current is None or current.get("status") not in {
            "running",
            "waiting_for_tool",
        }:
            return
        stage = str(action.get("stage") or current["stage"])
        owner = str(current["owner"])
        if transition == "progress":
            store.execution_state.progress(
                stage=stage,
                owner=owner,
                action=detail,
            )
        else:
            getattr(store.execution_state, transition)(
                stage=stage,
                owner=owner,
                reason=detail,
            )

    def _finish_execution(
        self,
        request: ToolCallRequest,
        action: Mapping[str, Any] | None,
        result: ToolMessage | Command[Any],
    ) -> None:
        self._execution_transition(
            request,
            action,
            "fail" if _result_failed(result) else "stop",
            detail=(
                "tool_or_postprocessing_failed"
                if _result_failed(result)
                else "action_completed"
            ),
        )

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
        if route_kind == "full":
            action = store.next_action()
        else:
            route = request.state.get("research_route", {})
            final_stage = route_kind.split(":", 1)[1]
            preliminary = (
                route.get("preliminary_stages", [])
                if isinstance(route, Mapping)
                else []
            )
            stages = [
                str(stage)
                for stage in preliminary
                if stage in {"planning", "data", "hypothesis", "experiment_design"}
                and stage != final_stage
            ]
            action = (
                store.bounded_sequence_action((*stages, final_stage))
                if stages
                else store.bounded_stage_action(final_stage)
            )
            route_kind = f"bounded:{action['stage']}"
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
            if expected == "solar-evidence":
                receipt = store.block_for_review_failures(
                    stage=action["stage"],
                    reviewer=expected,
                    fingerprints=failure_fingerprints,
                    failure_summaries=tuple(item[1] for item in prior_failures),
                )
            else:
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
        data_context: dict[str, Any] | None = None
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
        if analysis_protocol == "none" and route_kind == "full":
            from ..research_protocols import detect_analysis_protocol

            analysis_protocol = detect_analysis_protocol(
                _bound_research_question(store) or ""
            )
        portfolio_ranking = None
        if action["stage"] in _PORTFOLIO_RANKING_CONSUMER_STAGES:
            try:
                portfolio_ranking = _load_portfolio_ranking_capsule(
                    store.workspace_root, str(action["stage"])
                )
            except Exception as exc:
                return (
                    request,
                    action,
                    self._blocked(
                        request,
                        "the persisted Hypothesis portfolio ranking could not be "
                        f"projected for {action['stage']}: {type(exc).__name__}: {exc}",
                    ),
                )
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
            # The persisted review context is authoritative.  Do not forward
            # parent-authored copies of an artifact through task.description;
            # that duplicates a large payload and lets orchestration prose
            # masquerade as evidence.  The reviewer opens the bound artifact
            # and source refs itself.
            description = (
                "[EVIDENCE_REVIEW_V2]\n"
                f"review_mode={action['review_mode']}\n"
                "Open the server-bound context with evidence_review_open_context, "
                "inspect the immutable artifact and sources, then submit exactly one "
                "ReviewVerdictV2. Never edit the producer artifact."
            )
            description += "\n[A2A_HANDOFF_V1]\n" + json.dumps(
                build_a2a_handoff_envelope(
                    task_id=store.task_id,
                    action=action,
                    specialist=expected,
                    analysis_protocol=analysis_protocol,
                    accepted_upstream_refs=[
                        store.artifact_ref(item)
                        for item in store.accepted_artifacts()
                        if item["stage"] != action["stage"]
                    ],
                    portfolio_ranking=portfolio_ranking,
                ),
                ensure_ascii=False,
            )
        else:
            if action["stage"] == "hypothesis":
                # A Supervisor-generated task description is routing prose, not
                # accepted scientific evidence.  Rebuild Hypothesis dispatch
                # from the canonical request below so a stale or contradictory
                # parent summary cannot compete with the reviewed Data material.
                description = (
                    "Produce the task-bound Hypothesis result from the canonical "
                    "request and accepted upstream material supplied below. Treat "
                    "those bound records as authoritative; do not inherit factual "
                    "claims from any parent free-form task summary."
                )
            elif action["stage"] == "experiment_design":
                # The parent chooses only the producer.  In particular, a model-authored
                # retry can contain the prior immutable run id or replacement request
                # text.  Rebuild the dispatch from host-bound scope and review context.
                description = (
                    "Produce the task-bound Experiment Design from the host-owned "
                    "research scope, accepted upstream material, and revision capsule "
                    "supplied below. Ignore any parent-authored run id or request text."
                )
            revision_capsule = None
            planner_revision_checkpoint = None
            if action["stage"] == "data":
                try:
                    data_context = _open_data_context_preflight(
                        config,
                        route_kind=route_kind,
                        analysis_protocol=analysis_protocol,
                    )
                    if (
                        action.get("phase") == "data_revision_from_data"
                        and analysis_protocol == "solar_polar_precursor_v1"
                        and data_context.get("must_stop") is not True
                    ):
                        produced_receipt_ref = _persist_solar_cycle_pair_analysis_table(
                            config, data_context
                        )
                        produced_receipt = json.loads(
                            (store.workspace_root / produced_receipt_ref).read_text(
                                encoding="utf-8"
                            )
                        )
                        if not isinstance(produced_receipt, Mapping):
                            raise RuntimeError("cycle-pair receipt is not an object")
                        data_context["produced_data_receipt_ref"] = produced_receipt_ref
                        data_context["status"] = produced_receipt.get("analysis_status")
                        data_context["must_stop"] = False
                        data_context["instruction"] = (
                            "return the receipt-bound analysis table without another "
                            "tool call; preserve its status, gaps, and output_ref."
                        )
                        action["precomputed_producer_text"] = (
                            _solar_cycle_pair_analysis_producer_text(
                                store.workspace_root,
                                str(data_context["produced_data_receipt_ref"]),
                            )
                        )
                    elif (
                        action.get("phase") == "data_revision_from_data"
                        and analysis_protocol == "solar_cycle_26_forecast_backtest_v1"
                        and data_context.get("must_stop") is not True
                        and isinstance(
                            data_context.get("produced_data_receipt_ref"), str
                        )
                    ):
                        action["precomputed_producer_text"] = (
                            _solar_cycle_26_forecast_producer_text(
                                store.workspace_root,
                                str(data_context["produced_data_receipt_ref"]),
                            )
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
            experiment_analysis_protocol = "none"
            verified_cycle_pair_context: dict[str, Any] | None = None
            if action["stage"] in {"experiment_design", "experiment_result"}:
                route = (
                    request.state.get("research_route", {})
                    if isinstance(request.state, Mapping)
                    else {}
                )
                experiment_analysis_protocol = (
                    str(route.get("required_analysis_protocol") or "none")
                    if isinstance(route, Mapping)
                    else "none"
                )
                if experiment_analysis_protocol == "none" and route_kind == "full":
                    from ..research_protocols import detect_analysis_protocol

                    experiment_analysis_protocol = detect_analysis_protocol(
                        _bound_research_question(store) or ""
                    )
                if experiment_analysis_protocol == "solar_polar_precursor_v1":
                    try:
                        pair_receipt_ref = _ensure_solar_cycle_pair_analysis_table(
                            config
                        )
                        if pair_receipt_ref is not None:
                            pair_receipt = json.loads(
                                (store.workspace_root / pair_receipt_ref).read_text(
                                    encoding="utf-8"
                                )
                            )
                            if not isinstance(pair_receipt, Mapping):
                                raise RuntimeError(
                                    "solar cycle-pair receipt is not an object"
                                )
                            verified_cycle_pair_context = {
                                "schema_version": pair_receipt.get("schema_version"),
                                "status": pair_receipt.get("status"),
                                "analysis_status": pair_receipt.get("analysis_status"),
                                "row_count": pair_receipt.get("row_count"),
                                "predictor_cycles": pair_receipt.get(
                                    "predictor_cycles", []
                                ),
                                "target_cycles": pair_receipt.get("target_cycles", []),
                                "pair_coverage": pair_receipt.get("pair_coverage", {}),
                                "sample_size": pair_receipt.get("sample_size", {}),
                            }
                    except Exception as exc:
                        return (
                            request,
                            action,
                            self._blocked(
                                request,
                                "cycle-pair input construction failed before "
                                f"experiment dispatch: {type(exc).__name__}: {exc}",
                            ),
                        )
            staged_inputs: list[str] = []
            if action["stage"] in {"experiment_design", "experiment_result"}:
                try:
                    staged_inputs = _stage_data_produced_inputs(store)
                except Exception as exc:
                    return (
                        request,
                        action,
                        self._blocked(
                            request,
                            "experiment input staging failed before producer "
                            f"dispatch: {type(exc).__name__}: {exc}",
                        ),
                    )
            staged_directive = ""
            if staged_inputs:
                if action["stage"] == "experiment_result":
                    staged_directive = (
                        "\nstaged_data_inputs="
                        + json.dumps(staged_inputs, ensure_ascii=False)
                        + "\nThese are the task-local copies already captured by the "
                        "accepted experiment run. Resume that run without rebinding "
                        "or changing its input_refs."
                    )
                else:
                    staged_directive = (
                        "\nstaged_data_inputs="
                        + json.dumps(staged_inputs, ensure_ascii=False)
                        + "\nBind every required input_ref to one of these staged "
                        "inputs/... paths; they are the only readable task-local "
                        "copies of the accepted data artifact's produced files."
                    )
            experiment_protocol_directive = ""
            if action["stage"] in {"experiment_design", "experiment_result"}:
                if experiment_analysis_protocol == "solar_polar_precursor_v1":
                    from ..research_protocols import solar_polar_precursor_directive

                    experiment_protocol_directive = (
                        "\nanalysis_protocol_contract="
                        + solar_polar_precursor_directive()
                    )
                    if verified_cycle_pair_context is not None:
                        experiment_protocol_directive += (
                            "\nverified_cycle_pair_context="
                            + json.dumps(
                                verified_cycle_pair_context, ensure_ascii=False
                            )
                            + "\nThis receipt-derived context is authoritative for "
                            "sample mapping and row count. Do not replace it with "
                            "a count inferred from wording in the question or "
                            "inherited producer prose. Treat n_eff_upper_bound as "
                            "an upper bound; report a dependence-adjusted estimate "
                            "only when the declared data support one."
                        )
                elif experiment_analysis_protocol == "solar_cycle_26_readiness_v1":
                    from ..research_protocols import (
                        solar_cycle_26_readiness_directive,
                    )

                    experiment_protocol_directive = (
                        "\nanalysis_protocol_contract="
                        + solar_cycle_26_readiness_directive()
                    )
                elif experiment_analysis_protocol == "silso_cycle_morphology_v1":
                    from ..research_protocols import silso_cycle_morphology_directive

                    experiment_protocol_directive = (
                        "\nanalysis_protocol_contract="
                        + silso_cycle_morphology_directive()
                    )
                elif (
                    experiment_analysis_protocol
                    == "solar_cycle_26_forecast_backtest_v1"
                ):
                    from ..research_protocols import (
                        solar_cycle_26_forecast_backtest_directive,
                    )

                    experiment_protocol_directive = (
                        "\nanalysis_protocol_contract="
                        + solar_cycle_26_forecast_backtest_directive()
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
                    "the blocker immediately. If produced_data_receipt_ref is "
                    "present, return the receipt-bound analysis table without "
                    "another tool call. Otherwise inspect the exact eligible input and persist "
                    "at least one additional task-local data artifact before "
                    "returning."
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
            planning_protocol_directive = ""
            if (
                action["stage"] == "planning"
                and analysis_protocol == "silso_cycle_morphology_v1"
            ):
                from ..research_protocols import silso_cycle_morphology_directive

                planning_protocol_directive = (
                    "\nanalysis_protocol_contract="
                    + silso_cycle_morphology_directive()
                    + "\nPlanning evidence_refs may contain only existing bound inputs, "
                    "receipts, and inspected artifacts. The three requested outputs "
                    "are planned experiment_result deliverables, not current evidence."
                )
            hypothesis_request_directive = ""
            if action["stage"] == "hypothesis" and any(
                artifact["stage"] == "data" for artifact in store.accepted_artifacts()
            ):
                try:
                    request_path = _write_hypothesis_request(
                        store, analysis_protocol=analysis_protocol
                    )
                except Exception as exc:
                    return (
                        request,
                        action,
                        self._blocked(
                            request,
                            "the accepted upstream could not be bound into the "
                            f"Hypothesis request: {type(exc).__name__}: {exc}",
                        ),
                    )
                hypothesis_request_directive = (
                    "\nbound_hypothesis_request=@"
                    + request_path
                    + "\nCall scientific_hypothesis_bind_request with exactly this "
                    "@ path. It contains accepted Data as data_feature and, after "
                    "execution, the accepted prior hypothesis plus the verified "
                    "Experiment summary. Bind exact excerpts from those materials "
                    "when they support, oppose, or limit a candidate. In "
                    "hypothesis_update, the new statement, confidence, evidence "
                    "roles, falsification status, scope, measurement/novelty "
                    "boundaries, next discriminating test, and conclusion class "
                    "must follow the computed relation; never preserve an incoming "
                    "direction merely because it was the original hypothesis. "
                    "Data acceptance alone does not establish predictive skill or "
                    "causality."
                )
                if analysis_protocol == "silso_cycle_morphology_v1":
                    seed_path = (
                        store.workspace_root
                        / "work"
                        / "research_quality"
                        / "hypothesis_evidence_seed.json"
                    )
                    if seed_path.is_file():
                        hypothesis_request_directive += (
                            "\nhost_prebound_evidence_seed="
                            + seed_path.relative_to(store.workspace_root).as_posix()
                            + "\nThe host has already validated and atomically "
                            "prebound one exact statistical table row for each of "
                            "the three registered relationships. After the bind "
                            "receipt, do not call any evidence-bind tool again; "
                            "use its evidence_id mapping directly in the three "
                            "candidates."
                        )
            description += (
                "\n[A2A_HANDOFF_V1]\n"
                + json.dumps(
                    build_a2a_handoff_envelope(
                        task_id=store.task_id,
                        action=action,
                        specialist=expected,
                        analysis_protocol=analysis_protocol,
                        accepted_upstream_refs=[
                            store.artifact_ref(item)
                            for item in store.accepted_artifacts()
                            if item["stage"] != action["stage"]
                        ],
                        data_context=data_context,
                        portfolio_ranking=portfolio_ranking,
                    ),
                    ensure_ascii=False,
                )
                + "\n\n[RESEARCH_PRODUCER_V2]\n"
                f"phase={action['phase']}\n"
                f"stage={action['stage']}\n"
                f"revision_review_id={action.get('revision_review_id')}\n"
                "Return one bounded result owned by this producer. Never claim a "
                "receipt that the dedicated tools did not produce. For a revision, "
                "consume only revision_capsule, preserve accepted and unchanged "
                "work, and stop after one new immutable artifact. The accepted "
                "producer result becomes reader-visible: report scientific content "
                "and evidence boundaries only. Do not mention internal draft, "
                "checkpoint, freeze, publish, or release state; the runtime owns "
                "those lifecycle labels.\n"
                f"revision_capsule={json.dumps(revision_capsule, ensure_ascii=False)}\n"
                "planner_revision_checkpoint="
                f"{json.dumps(planner_revision_checkpoint, ensure_ascii=False)}\n"
                f"policy_preflight={json.dumps(policy_registry(stage=action['stage']), ensure_ascii=False)}\n"
                f"accepted_upstream={_upstream_context(store, action['stage'])}"
                f"{staged_directive}"
                f"{experiment_protocol_directive}"
                f"{data_context_directive}"
                f"{bound_question_directive}"
                f"{planning_protocol_directive}"
                f"{hypothesis_request_directive}"
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
            action_reserved = True
        if action["kind"] == "producer" and action["stage"] in {
            "experiment_design",
            "experiment_result",
        }:
            try:
                _persist_experiment_scope(
                    store,
                    action,
                    analysis_protocol=experiment_analysis_protocol,
                    portfolio_ranking=portfolio_ranking,
                )
            except Exception as exc:
                return (
                    request,
                    action,
                    self._blocked(
                        request,
                        "the reserved host-owned experiment scope could not be persisted: "
                        f"{type(exc).__name__}: {exc}",
                    ),
                )
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
            if _MODEL_CALL_BUDGET_STOP.match(producer_text):
                # A specialist loop guard is an execution failure, not a
                # scientific observation.  In particular, do not let a stale
                # canonical source bundle make this framework message look like
                # a new immutable producer result.
                return self._blocked(
                    request,
                    "producer exhausted its model-call budget before returning "
                    "a bounded scientific result",
                )
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
                elif action["stage"] == "experiment_design":
                    scope_path = store.root / "experiment_scope.json"
                    try:
                        scope = json.loads(scope_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        scope = None
                    protocol = (
                        str(scope.get("analysis_protocol") or "")
                        if isinstance(scope, Mapping)
                        else ""
                    )
                    if protocol in {
                        "silso_cycle_morphology_v1",
                        "solar_cycle_26_forecast_backtest_v1",
                        "solar_polar_precursor_v1",
                    }:
                        try:
                            if protocol == "silso_cycle_morphology_v1":
                                from ..tools.automatic_experiment import (
                                    ensure_host_silso_morphology_design,
                                )

                                design_receipt = ensure_host_silso_morphology_design(
                                    config
                                )
                            elif protocol == "solar_cycle_26_forecast_backtest_v1":
                                from ..tools.automatic_experiment import (
                                    ensure_host_solar_cycle_26_forecast_design,
                                )

                                design_receipt = (
                                    ensure_host_solar_cycle_26_forecast_design(config)
                                )
                            else:
                                from ..tools.automatic_experiment import (
                                    ensure_host_polar_forecast_design,
                                )

                                design_receipt = ensure_host_polar_forecast_design(
                                    config
                                )
                            producer_text += (
                                "\n\n[HOST PROTOCOL DESIGN]\n"
                                + json.dumps(design_receipt, ensure_ascii=False)
                            )
                            artifact = store.checkpoint_producer_result(
                                stage=action["stage"],
                                producer=action["producer"],
                                content=producer_text,
                                phase=action["phase"],
                                require_canonical_source=True,
                                revision_review_id=action.get("revision_review_id"),
                            )
                        except Exception as host_exc:
                            return self._blocked(
                                request,
                                "producer local preflight failed before Evidence "
                                f"review: {type(exc).__name__}: {exc}; host "
                                "protocol design recovery failed: "
                                f"{type(host_exc).__name__}: {host_exc}",
                            )
                    else:
                        return self._blocked(
                            request,
                            "producer local preflight failed before Evidence review: "
                            f"{type(exc).__name__}: {exc}",
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
                diagnostic = _kimi_evidence_failure_summary(_tool_result_text(result))
                detail = "solar-evidence returned without persisting a hash-bound ReviewVerdictV2"
                if diagnostic:
                    detail += "\n" + diagnostic
                return self._blocked(
                    request,
                    detail,
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
            self._finish_execution(request, action, blocked)
            return blocked
        if action is not None and isinstance(
            action.get("precomputed_producer_text"), str
        ):
            result = ToolMessage(
                content=action["precomputed_producer_text"],
                tool_call_id=str(request.tool_call.get("id") or "data-readiness"),
                name=str(request.tool_call.get("name") or "task"),
            )
            processed = self._after(request, action, result)
            self._finish_execution(request, action, processed)
            return processed
        self._execution_transition(
            request,
            action,
            "waiting_for_tool",
            detail="delegated_tool_call",
        )
        try:
            result = handler(rewritten)
        except KeyboardInterrupt:
            self._execution_transition(
                request,
                action,
                "interrupt",
                detail="tool_call_interrupted",
            )
            raise
        except Exception as exc:
            self._execution_transition(
                request,
                action,
                "fail",
                detail=f"tool_handler_exception:{type(exc).__name__}",
            )
            raise
        self._execution_transition(
            request,
            action,
            "progress",
            detail="tool_returned",
        )
        processed = self._after(request, action, result)
        self._finish_execution(request, action, processed)
        return processed

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        rewritten, action, blocked = await asyncio.to_thread(self._prepare, request)
        if blocked is not None:
            await asyncio.to_thread(self._finish_execution, request, action, blocked)
            return blocked
        if action is not None and isinstance(
            action.get("precomputed_producer_text"), str
        ):
            result = ToolMessage(
                content=action["precomputed_producer_text"],
                tool_call_id=str(request.tool_call.get("id") or "data-readiness"),
                name=str(request.tool_call.get("name") or "task"),
            )
            processed = await asyncio.to_thread(self._after, request, action, result)
            await asyncio.to_thread(self._finish_execution, request, action, processed)
            return processed
        await asyncio.to_thread(
            self._execution_transition,
            request,
            action,
            "waiting_for_tool",
            detail="delegated_tool_call",
        )
        try:
            result = await handler(rewritten)
        except asyncio.CancelledError:
            await asyncio.to_thread(
                self._execution_transition,
                request,
                action,
                "interrupt",
                detail="tool_call_interrupted",
            )
            raise
        except Exception as exc:
            await asyncio.to_thread(
                self._execution_transition,
                request,
                action,
                "fail",
                detail=f"tool_handler_exception:{type(exc).__name__}",
            )
            raise
        await asyncio.to_thread(
            self._execution_transition,
            request,
            action,
            "progress",
            detail="tool_returned",
        )
        processed = await asyncio.to_thread(self._after, request, action, result)
        await asyncio.to_thread(self._finish_execution, request, action, processed)
        return processed


__all__ = ["ResearchReviewOrchestrationMiddleware"]
