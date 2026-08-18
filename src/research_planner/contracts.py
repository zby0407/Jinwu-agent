"""Closed, dependency-free contracts for Research Planner 1.0."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

REQUEST_VERSION = "research-planner-request-v1"
RESPONSE_VERSION = "research-planner-response-v1"
PLAN_VERSION = "research-plan-v1"
OUTCOME_VERSION = "research-planner-outcome-v1"

RESPONSE_KINDS = {"plan_ready", "clarification_needed", "planning_blocked"}
SOURCE_AVAILABILITY = {"selected", "available", "to_acquire"}
ACQUISITION_STATUS = {"selected", "missing", "needs_confirmation"}
EVIDENCE_SOURCE_KIND = {
    "local_material",
    "scholarly",
    "dataset_metadata",
    "user_provided",
}
EVIDENCE_VERIFICATION_LEVEL = {
    "unverified",
    "reference_resolved",
    "claim_located",
    "dataset_inspected",
}
CRITERION_BASIS_KIND = {
    "source_based",
    "data_based",
    "request_based",
    "qualitative",
}
STATE_ITEM_KIND = {
    "supported_finding",
    "working_assumption",
    "testable_hypothesis",
    "evidence_gap",
    "evidence_conflict",
}
STATE_STATUS = {
    "supported",
    "partially_supported",
    "unresolved",
    "unavailable",
    "not_required",
}
CONFIDENCE_LEVEL = {"high", "medium", "low", "unknown"}
ROUTE_OUTCOME = {
    "completed",
    "inconclusive",
    "input_missing",
    "evidence_conflict",
    "method_invalid",
    "budget_reached",
}
TERMINAL_STATUS = {
    "plan_complete",
    "partial_result",
    "needs_input",
    "no_viable_route",
}
STOP_CONDITION_KIND = {
    "goal_satisfied",
    "evidence_sufficient",
    "no_viable_route",
    "budget_exhausted",
    "human_stop",
    "unsafe_to_continue",
    "partial_result_ready",
    "input_required",
}
BLOCKER_CODE = {
    "unsupported_scope",
    "unresearchable_formulation",
    "missing_indispensable_condition",
    "safety_boundary",
}
SAFE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
SAFE_REF = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
CLAIM_LOCATOR_HINT = re.compile(
    r"(?:"
    r"第\s*[0-9一二三四五六七八九十百千万两]+\s*(?:页|节|章|段)"
    r"|(?:page|pages|p\.|pp\.|section|sec\.|chapter|figure|fig\.|table|appendix|paragraph)\s*[A-Za-z0-9]"
    r"|lines?[-:\s]+\d+"
    r"|(?:图|表)\s*[A-Za-z0-9一二三四五六七八九十]+"
    r")",
    re.IGNORECASE,
)
HARD_NUMERIC_CUTOFF = re.compile(
    r"(?:"
    r"(?:至少|至多|不少于|不多于|不超过|不低于|不高于|超过|高于|低于|小于|大于|达到|"
    r"at\s+least|at\s+most|more\s+than|less\s+than|no\s+more\s+than|no\s+less\s+than|"
    r">=|<=|>|<)"
    r"[^。；;.!?！？]{0,24}"
    r"(?:\d+(?:\.\d+)?|[一二三四五六七八九十百千万两]+)"
    r"|(?:\d+(?:\.\d+)?|[一二三四五六七八九十百千万两]+)"
    r"\s*(?:个|项|种|次|年|月|日|周|周期|样本|百分比|%)?\s*(?:及以上|及以下|以上|以下)"
    r")",
    re.IGNORECASE,
)


class ContractError(ValueError):
    """Localized contract error returned before any run is persisted."""


def canonical_json_sha256(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ContractError("payload must contain only finite JSON values") from exc
    return hashlib.sha256(encoded).hexdigest()


def clone_json(value: object, label: str) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ContractError(f"{label} must contain only finite JSON values") from exc


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be a JSON object")
    return value


def _exact_fields(value: dict[str, Any], required: set[str], label: str) -> None:
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required)
    if missing:
        raise ContractError(f"{label} missing fields: {', '.join(missing)}")
    if unknown:
        raise ContractError(f"{label} has unknown fields: {', '.join(unknown)}")


def _text(
    value: object,
    label: str,
    *,
    minimum: int = 1,
    maximum: int = 2_000,
    strip: bool = True,
) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{label} must be a string")
    normalized = value.strip() if strip else value
    if len(normalized) < minimum or not normalized.strip():
        raise ContractError(f"{label} must be a non-empty string")
    if len(normalized) > maximum:
        raise ContractError(f"{label} exceeds {maximum} characters")
    return normalized


def _nullable_text(value: object, label: str, maximum: int = 2_000) -> str | None:
    if value is None:
        return None
    return _text(value, label, maximum=maximum)


def _safe_id(value: object, label: str) -> str:
    normalized = _text(value, label, maximum=64)
    if value != normalized:
        raise ContractError(f"{label} must not contain surrounding whitespace")
    if SAFE_ID.fullmatch(normalized) is None:
        raise ContractError(f"{label} must match {SAFE_ID.pattern}")
    return normalized


def _safe_ref(value: object, label: str) -> str:
    normalized = _text(value, label, maximum=128)
    if value != normalized:
        raise ContractError(f"{label} must not contain surrounding whitespace")
    if SAFE_REF.fullmatch(normalized) is None:
        raise ContractError(f"{label} must be a safe reference id")
    return normalized


def _integer(value: object, label: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"{label} must be an integer")
    if value < minimum or value > maximum:
        raise ContractError(f"{label} must be in [{minimum}, {maximum}]")
    return value


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ContractError(f"{label} must be a boolean")
    return value


def _array(
    value: object,
    label: str,
    *,
    minimum: int = 0,
    maximum: int,
) -> list[Any]:
    if not isinstance(value, list):
        raise ContractError(f"{label} must be an array")
    if len(value) < minimum or len(value) > maximum:
        raise ContractError(f"{label} must contain {minimum} to {maximum} items")
    return value


def _text_array(
    value: object,
    label: str,
    *,
    minimum: int = 0,
    maximum: int,
    item_maximum: int = 2_000,
) -> list[str]:
    rows = _array(value, label, minimum=minimum, maximum=maximum)
    result = [
        _text(item, f"{label}[{index}]", maximum=item_maximum)
        for index, item in enumerate(rows)
    ]
    if len(result) != len(set(result)):
        raise ContractError(f"{label} must contain unique items")
    return result


def _safe_ref_array(
    value: object,
    label: str,
    *,
    minimum: int = 0,
    maximum: int,
) -> list[str]:
    rows = _array(value, label, minimum=minimum, maximum=maximum)
    result = [
        _safe_ref(item, f"{label}[{index}]") for index, item in enumerate(rows)
    ]
    if len(result) != len(set(result)):
        raise ContractError(f"{label} must contain unique items")
    return result


def _enum(value: object, choices: set[str], label: str) -> str:
    normalized = _text(value, label, maximum=100)
    if normalized not in choices:
        raise ContractError(f"{label} must be one of: {sorted(choices)}")
    return normalized


def _has_claim_level_locator(locator: str) -> bool:
    return CLAIM_LOCATOR_HINT.search(locator) is not None


def _contains_hard_numeric_cutoff(*texts: str) -> bool:
    return any(HARD_NUMERIC_CUTOFF.search(text) is not None for text in texts)


def _numeric_cutoff_is_request_grounded(
    text: str, request_text_values: list[str]
) -> bool:
    phrases = [match.group(0).strip() for match in HARD_NUMERIC_CUTOFF.finditer(text)]
    return bool(phrases) and all(
        any(phrase in request_text for request_text in request_text_values)
        for phrase in phrases
    )


def _request_text_values(request: dict[str, Any]) -> list[str]:
    values = [request["task_name"], request["research_question"]]
    for source in request["data_sources"]:
        values.extend(
            value
            for value in (
                source["name"],
                source["description"],
                source["location"],
                *source["constraints"],
            )
            if isinstance(value, str)
        )
    return values


def _unique_id_objects(
    values: object,
    label: str,
    *,
    minimum: int,
    maximum: int,
) -> tuple[list[dict[str, Any]], set[str]]:
    rows = _array(values, label, minimum=minimum, maximum=maximum)
    objects: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, row in enumerate(rows):
        item_label = f"{label}[{index}]"
        item = _object(row, item_label)
        if "id" not in item:
            raise ContractError(f"{item_label} missing fields: id")
        item_id = _safe_id(item["id"], f"{item_label}.id")
        if item_id in ids:
            raise ContractError(f"{label} has duplicate id: {item_id}")
        ids.add(item_id)
        objects.append(item)
    return objects, ids


def _require_subset(values: set[str], allowed: set[str], label: str) -> None:
    unknown = sorted(values - allowed)
    if unknown:
        raise ContractError(f"{label} references unknown ids: {unknown}")


def _assert_acyclic(graph: dict[str, set[str]], label: str) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise ContractError(f"{label} contains a cycle at: {node}")
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph[node]:
            visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for node_id in graph:
        visit(node_id)


def format_illegal_transition_message(step_id: str, outcome: str, target: str) -> str:
    """One actionable message for a non-completed transition into a completion-required target.

    Shared by the hard contract check and the preflight aggregator so the planner
    sees identical guidance either way. It names the source step, the outcome, the
    target, and the concrete repair directions so the model fixes it in one pass.
    """

    return (
        f"route step {step_id} outcome {outcome} cannot transition to {target}; "
        f"the target requires {step_id} to complete. Fix one way: "
        f"point outcome '{outcome}' of {step_id} at a terminal_status instead "
        f"(only 'completed' may target {target}) and add a stop_rules entry for "
        f"that terminal status; or point '{outcome}' back at {step_id} or an "
        f"earlier step as a rework/self-correction cycle (needs "
        f"self_correction_enabled and visit_limit >= 2); or remove {step_id} "
        f"from {target}'s prerequisite_step_ids and consumes_artifact_ids if "
        f"{target} truly tolerates an incomplete {step_id}."
    )


def _requires_completed_step(
    graph: dict[str, set[str]],
    join_policies: dict[str, str],
    node: str,
    source: str,
    memo: dict[tuple[str, str], bool] | None = None,
) -> bool:
    """Whether every valid static-dependency route to node requires source."""

    cache = memo if memo is not None else {}
    key = (node, source)
    if key in cache:
        return cache[key]
    dependencies = graph[node]
    if not dependencies:
        cache[key] = False
        return False
    requirements = [
        dependency == source
        or _requires_completed_step(graph, join_policies, dependency, source, cache)
        for dependency in dependencies
    ]
    required = (
        all(requirements) if join_policies[node] == "any" else any(requirements)
    )
    cache[key] = required
    return required


def _cyclic_nodes(graph: dict[str, set[str]]) -> set[str]:
    """Return nodes in a directed cycle using Tarjan strongly connected components."""

    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    result: set[str] = set()

    def strongconnect(node: str) -> None:
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in graph[node]:
            if target not in indices:
                strongconnect(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[target])
        if lowlinks[node] == indices[node]:
            component: list[str] = []
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.append(member)
                if member == node:
                    break
            if len(component) > 1 or node in graph[node]:
                result.update(component)

    for node_id in graph:
        if node_id not in indices:
            strongconnect(node_id)
    return result


def validate_planner_request(payload: dict[str, Any]) -> dict[str, Any]:
    request = clone_json(_object(payload, "planner_request"), "planner_request")
    fields = {
        "schema_version",
        "task_name",
        "research_question",
        "data_sources",
        "max_iterations",
        "hypothesis_review_enabled",
        "self_correction_enabled",
    }
    _exact_fields(request, fields, "planner_request")
    if request["schema_version"] != REQUEST_VERSION:
        raise ContractError(f"planner_request.schema_version must be {REQUEST_VERSION}")
    _safe_id(request["task_name"], "planner_request.task_name")
    _text(
        request["research_question"],
        "planner_request.research_question",
        minimum=8,
        maximum=4_000,
        strip=False,
    )
    _integer(
        request["max_iterations"],
        "planner_request.max_iterations",
        minimum=1,
        maximum=12,
    )
    _boolean(request["hypothesis_review_enabled"], "planner_request.hypothesis_review_enabled")
    _boolean(request["self_correction_enabled"], "planner_request.self_correction_enabled")
    sources, _ = _unique_id_objects(
        request["data_sources"],
        "planner_request.data_sources",
        minimum=0,
        maximum=20,
    )
    source_fields = {"id", "name", "description", "location", "availability", "constraints"}
    for index, source in enumerate(sources):
        label = f"planner_request.data_sources[{index}]"
        _exact_fields(source, source_fields, label)
        _text(source["name"], f"{label}.name", maximum=300)
        _text(source["description"], f"{label}.description", maximum=1_000)
        _nullable_text(source["location"], f"{label}.location", maximum=1_000)
        _enum(source["availability"], SOURCE_AVAILABILITY, f"{label}.availability")
        _text_array(source["constraints"], f"{label}.constraints", maximum=10)
    return request


def _validate_scope(value: object, label: str) -> None:
    scope = _object(value, label)
    fields = {"objective", "population_or_period", "boundaries", "non_goals"}
    _exact_fields(scope, fields, label)
    _text(scope["objective"], f"{label}.objective")
    _text(scope["population_or_period"], f"{label}.population_or_period")
    _text_array(scope["boundaries"], f"{label}.boundaries", minimum=1, maximum=10)
    _text_array(scope["non_goals"], f"{label}.non_goals", minimum=1, maximum=10)


def _validate_clarification_response(response: dict[str, Any]) -> None:
    fields = {"schema_version", "task_name", "research_question", "response_kind", "questions"}
    _exact_fields(response, fields, "planner_response")
    questions, _ = _unique_id_objects(
        response["questions"], "planner_response.questions", minimum=1, maximum=3
    )
    question_fields = {"id", "question", "why_it_matters", "expected_answer"}
    for index, question in enumerate(questions):
        label = f"planner_response.questions[{index}]"
        _exact_fields(question, question_fields, label)
        _text(question["question"], f"{label}.question")
        _text(question["why_it_matters"], f"{label}.why_it_matters")
        _text(question["expected_answer"], f"{label}.expected_answer")


def _validate_blocked_response(response: dict[str, Any]) -> None:
    fields = {"schema_version", "task_name", "research_question", "response_kind", "blockers"}
    _exact_fields(response, fields, "planner_response")
    blockers, _ = _unique_id_objects(
        response["blockers"], "planner_response.blockers", minimum=1, maximum=10
    )
    blocker_fields = {"id", "code", "reason", "recoverable", "resolution"}
    for index, blocker in enumerate(blockers):
        label = f"planner_response.blockers[{index}]"
        _exact_fields(blocker, blocker_fields, label)
        _enum(blocker["code"], BLOCKER_CODE, f"{label}.code")
        _text(blocker["reason"], f"{label}.reason")
        _boolean(blocker["recoverable"], f"{label}.recoverable")
        _text(blocker["resolution"], f"{label}.resolution")


def _validate_plan_content(content_value: object, request: dict[str, Any]) -> dict[str, Any]:
    content = _object(content_value, "planner_response.plan_content")
    fields = {
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
    }
    _exact_fields(content, fields, "planner_response.plan_content")
    _validate_scope(content["scope"], "planner_response.plan_content.scope")
    request_text_values = _request_text_values(request)

    subquestions, subquestion_ids = _unique_id_objects(
        content["research_subquestions"],
        "planner_response.plan_content.research_subquestions",
        minimum=1,
        maximum=12,
    )
    subquestion_graph: dict[str, set[str]] = {}
    subquestion_completion_text: dict[str, str] = {}
    subquestion_fields = {"id", "question", "purpose", "depends_on", "completion_evidence"}
    for index, subquestion in enumerate(subquestions):
        label = f"planner_response.plan_content.research_subquestions[{index}]"
        _exact_fields(subquestion, subquestion_fields, label)
        _text(subquestion["question"], f"{label}.question", minimum=4)
        _text(subquestion["purpose"], f"{label}.purpose")
        dependencies = set(_safe_ref_array(subquestion["depends_on"], f"{label}.depends_on", maximum=11))
        completion_evidence = _text(
            subquestion["completion_evidence"], f"{label}.completion_evidence"
        )
        subquestion_graph[subquestion["id"]] = dependencies
        subquestion_completion_text[subquestion["id"]] = completion_evidence
    for subquestion_id, dependencies in subquestion_graph.items():
        _require_subset(dependencies, subquestion_ids, f"subquestion {subquestion_id}.depends_on")
        if subquestion_id in dependencies:
            raise ContractError(f"subquestion {subquestion_id} cannot depend on itself")
    _assert_acyclic(subquestion_graph, "research_subquestions")

    evidence_sources, evidence_source_ids = _unique_id_objects(
        content["evidence_sources"],
        "planner_response.plan_content.evidence_sources",
        minimum=0,
        maximum=40,
    )
    evidence_fields = {
        "id",
        "citation",
        "locator",
        "source_kind",
        "verification_level",
        "role",
        "state_item_ids",
        "subquestion_ids",
        "limitations",
    }
    evidence_state_refs: dict[str, set[str]] = {}
    evidence_quality: dict[str, tuple[str, str]] = {}
    for index, source in enumerate(evidence_sources):
        label = f"planner_response.plan_content.evidence_sources[{index}]"
        _exact_fields(source, evidence_fields, label)
        _text(source["citation"], f"{label}.citation", maximum=1_000)
        locator = _text(source["locator"], f"{label}.locator")
        source_kind = _enum(
            source["source_kind"], EVIDENCE_SOURCE_KIND, f"{label}.source_kind"
        )
        verification_level = _enum(
            source["verification_level"],
            EVIDENCE_VERIFICATION_LEVEL,
            f"{label}.verification_level",
        )
        if verification_level == "claim_located" and not _has_claim_level_locator(locator):
            raise ContractError(
                f"{label}.locator must identify a page, section, paragraph, line range, figure, or table "
                "when verification_level=claim_located"
            )
        if verification_level == "dataset_inspected" and source_kind != "dataset_metadata":
            raise ContractError(
                f"{label}.source_kind must be dataset_metadata when verification_level=dataset_inspected"
            )
        _text(source["role"], f"{label}.role")
        state_refs = set(_safe_ref_array(source["state_item_ids"], f"{label}.state_item_ids", minimum=1, maximum=50))
        question_refs = set(_safe_ref_array(source["subquestion_ids"], f"{label}.subquestion_ids", minimum=1, maximum=12))
        _require_subset(question_refs, subquestion_ids, f"{label}.subquestion_ids")
        _text(source["limitations"], f"{label}.limitations")
        evidence_state_refs[source["id"]] = state_refs
        evidence_quality[source["id"]] = (source_kind, verification_level)

    state_map = _object(content["research_state_map"], "planner_response.plan_content.research_state_map")
    _exact_fields(state_map, {"items"}, "planner_response.plan_content.research_state_map")
    state_items, state_item_ids = _unique_id_objects(
        state_map["items"],
        "planner_response.plan_content.research_state_map.items",
        minimum=1,
        maximum=50,
    )
    state_fields = {
        "id",
        "statement",
        "item_kind",
        "status",
        "rationale",
        "evidence_source_ids",
        "subquestion_ids",
        "blocking",
        "resolution_requirements",
        "resolution_step_ids",
        "impact_if_wrong",
        "confidence",
    }
    state_source_refs: dict[str, set[str]] = {}
    state_step_refs: dict[str, set[str]] = {}
    subquestions_with_state: set[str] = set()
    for index, item in enumerate(state_items):
        label = f"planner_response.plan_content.research_state_map.items[{index}]"
        _exact_fields(item, state_fields, label)
        statement = _text(item["statement"], f"{label}.statement")
        item_kind = _enum(item["item_kind"], STATE_ITEM_KIND, f"{label}.item_kind")
        status = _enum(item["status"], STATE_STATUS, f"{label}.status")
        _text(item["rationale"], f"{label}.rationale")
        source_refs = set(_safe_ref_array(item["evidence_source_ids"], f"{label}.evidence_source_ids", maximum=40))
        question_refs = set(_safe_ref_array(item["subquestion_ids"], f"{label}.subquestion_ids", minimum=1, maximum=12))
        _require_subset(source_refs, evidence_source_ids, f"{label}.evidence_source_ids")
        _require_subset(question_refs, subquestion_ids, f"{label}.subquestion_ids")
        subquestions_with_state.update(question_refs)
        blocking = _boolean(item["blocking"], f"{label}.blocking")
        resolution_requirements = _text_array(item["resolution_requirements"], f"{label}.resolution_requirements", maximum=20)
        resolution_steps = set(_safe_ref_array(item["resolution_step_ids"], f"{label}.resolution_step_ids", maximum=30))
        _text(item["impact_if_wrong"], f"{label}.impact_if_wrong")
        confidence = _object(item["confidence"], f"{label}.confidence")
        _exact_fields(confidence, {"level", "basis"}, f"{label}.confidence")
        _enum(confidence["level"], CONFIDENCE_LEVEL, f"{label}.confidence.level")
        _text(confidence["basis"], f"{label}.confidence.basis")
        qualified_sources = {
            source_id
            for source_id in source_refs
            if evidence_quality[source_id][1] == "claim_located"
            or (
                evidence_quality[source_id][0] == "dataset_metadata"
                and evidence_quality[source_id][1] == "dataset_inspected"
            )
        }
        if status in {"supported", "partially_supported"}:
            if not source_refs:
                raise ContractError(f"{label} with status {status} requires evidence_source_ids")
            if not qualified_sources:
                raise ContractError(
                    f"{label} with status {status} requires claim-located or applicable "
                    "dataset-inspected evidence; a resolved reference alone is insufficient"
                )
        if item_kind == "supported_finding":
            if status not in {"supported", "partially_supported"} or not source_refs:
                raise ContractError(f"{label} supported_finding must be evidence-linked and supported")
        if (
            item_kind == "testable_hypothesis"
            and _contains_hard_numeric_cutoff(statement)
            and not qualified_sources
            and not _numeric_cutoff_is_request_grounded(
                statement, request_text_values
            )
        ):
            raise ContractError(
                f"{label} contains a fixed numeric magnitude without claim-located evidence "
                "or an exact user-request basis; use an open directional hypothesis or add a traceable basis"
            )
        if item_kind in {"evidence_gap", "evidence_conflict"} and status in {"supported", "partially_supported"}:
            raise ContractError(f"{label} {item_kind} cannot have supported status")
        if (blocking or item_kind in {"evidence_gap", "evidence_conflict"}) and status != "not_required" and not resolution_requirements:
            raise ContractError(f"{label} requires explicit resolution_requirements")
        state_source_refs[item["id"]] = source_refs
        state_step_refs[item["id"]] = resolution_steps
    missing_state_coverage = sorted(subquestion_ids - subquestions_with_state)
    if missing_state_coverage:
        raise ContractError(f"research_state_map must cover every subquestion; missing={missing_state_coverage}")
    traceability_errors: list[str] = []
    for source_id, item_refs in evidence_state_refs.items():
        _require_subset(item_refs, state_item_ids, f"evidence source {source_id}.state_item_ids")
        for item_id in item_refs:
            if source_id not in state_source_refs[item_id]:
                traceability_errors.append(
                    f"state item {item_id}.evidence_source_ids is missing {source_id}"
                )
    for item_id, source_refs in state_source_refs.items():
        for source_id in source_refs:
            if item_id not in evidence_state_refs[source_id]:
                traceability_errors.append(
                    f"evidence source {source_id}.state_item_ids is missing {item_id}"
                )
    if traceability_errors:
        raise ContractError(
            "evidence traceability must be bidirectional; "
            + "; ".join(sorted(traceability_errors))
        )

    datasets, dataset_ids = _unique_id_objects(
        content["required_datasets"],
        "planner_response.plan_content.required_datasets",
        minimum=0,
        maximum=20,
    )
    request_source_ids = {source["id"] for source in request["data_sources"]}
    dataset_fields = {
        "id",
        "source_kind",
        "selected_source_id",
        "name",
        "purpose",
        "required_variables",
        "time_coverage_needed",
        "cadence_needed",
        "quality_requirements",
        "version_requirement",
        "unit_requirements",
        "revision_requirements",
        "license_requirements",
        "acquisition_status",
    }
    for index, dataset in enumerate(datasets):
        label = f"planner_response.plan_content.required_datasets[{index}]"
        _exact_fields(dataset, dataset_fields, label)
        source_kind = _enum(dataset["source_kind"], {"selected", "proposed"}, f"{label}.source_kind")
        selected_source_id = dataset["selected_source_id"]
        if source_kind == "selected":
            selected_ref = _safe_ref(selected_source_id, f"{label}.selected_source_id")
            if selected_ref not in request_source_ids:
                raise ContractError(f"{label}.selected_source_id must reference a request data source")
        elif selected_source_id is not None:
            raise ContractError(f"{label}.selected_source_id must be null for proposed data")
        _text(dataset["name"], f"{label}.name", maximum=300)
        _text(dataset["purpose"], f"{label}.purpose")
        _text_array(dataset["required_variables"], f"{label}.required_variables", minimum=1, maximum=30, item_maximum=200)
        _text(dataset["time_coverage_needed"], f"{label}.time_coverage_needed")
        _text(dataset["cadence_needed"], f"{label}.cadence_needed", maximum=300)
        _text_array(dataset["quality_requirements"], f"{label}.quality_requirements", minimum=1, maximum=20)
        _text(dataset["version_requirement"], f"{label}.version_requirement")
        _text_array(dataset["unit_requirements"], f"{label}.unit_requirements", maximum=20)
        _text_array(dataset["revision_requirements"], f"{label}.revision_requirements", maximum=20)
        _text_array(dataset["license_requirements"], f"{label}.license_requirements", maximum=20)
        acquisition = _enum(dataset["acquisition_status"], ACQUISITION_STATUS, f"{label}.acquisition_status")
        if source_kind == "proposed" and acquisition == "selected":
            raise ContractError(f"{label}.acquisition_status cannot be selected for proposed data")
    dataset_names = {dataset["id"]: dataset["name"] for dataset in datasets}

    artifacts, artifact_ids = _unique_id_objects(
        content["research_artifacts"],
        "planner_response.plan_content.research_artifacts",
        minimum=1,
        maximum=60,
    )
    artifact_fields = {"id", "name", "artifact_kind", "purpose", "source_kind", "producer_step_id", "subquestion_ids", "content_requirements"}
    artifact_producer_claims: dict[str, str | None] = {}
    for index, artifact in enumerate(artifacts):
        label = f"planner_response.plan_content.research_artifacts[{index}]"
        _exact_fields(artifact, artifact_fields, label)
        _text(artifact["name"], f"{label}.name", maximum=300)
        _safe_id(artifact["artifact_kind"], f"{label}.artifact_kind")
        _text(artifact["purpose"], f"{label}.purpose")
        source_kind = _enum(artifact["source_kind"], {"request_input", "external_input", "planned_output"}, f"{label}.source_kind")
        producer = artifact["producer_step_id"]
        if source_kind == "planned_output":
            if producer is None or not isinstance(producer, str) or not producer.strip():
                raise ContractError(
                    f"{label} is planned_output but has no producer step: set "
                    "producer_step_id to the id of the research_route step that produces it, "
                    "or change source_kind to request_input/external_input if it is an input"
                )
            producer_ref = _safe_ref(producer, f"{label}.producer_step_id")
        else:
            if producer is not None:
                raise ContractError(f"{label}.producer_step_id must be null for input artifacts")
            producer_ref = None
        question_refs = set(_safe_ref_array(artifact["subquestion_ids"], f"{label}.subquestion_ids", minimum=1, maximum=12))
        _require_subset(question_refs, subquestion_ids, f"{label}.subquestion_ids")
        _text_array(artifact["content_requirements"], f"{label}.content_requirements", minimum=1, maximum=20)
        artifact_producer_claims[artifact["id"]] = producer_ref

    route_steps, route_step_ids = _unique_id_objects(
        content["research_route"],
        "planner_response.plan_content.research_route",
        minimum=1,
        maximum=30,
    )
    route_fields = {
        "id",
        "iteration",
        "stage",
        "objective",
        "necessity",
        "subquestion_ids",
        "required_dataset_ids",
        "consumes_artifact_ids",
        "produces_artifact_ids",
        "prerequisite_step_ids",
        "join_policy",
        "method_outline",
        "capability_needs",
        "outcome_rules",
        "transitions",
        "visit_limit",
        "evaluation_rule_ids",
    }
    step_by_id = {step["id"]: step for step in route_steps}
    step_consumes: dict[str, set[str]] = {}
    step_produces: dict[str, set[str]] = {}
    explicit_prerequisites: dict[str, set[str]] = {}
    control_graph: dict[str, set[str]] = {step_id: set() for step_id in route_step_ids}
    terminal_steps: set[str] = set()
    route_subquestion_coverage: set[str] = set()
    used_dataset_ids: set[str] = set()
    step_evaluation_refs: dict[str, set[str]] = {}
    step_subquestion_refs: dict[str, set[str]] = {}
    step_numeric_outcome_criteria: dict[str, list[str]] = {}
    terminal_statuses_used: set[str] = set()
    for index, step in enumerate(route_steps):
        label = f"planner_response.plan_content.research_route[{index}]"
        _exact_fields(step, route_fields, label)
        iteration = _integer(step["iteration"], f"{label}.iteration", minimum=1, maximum=12)
        if iteration > request["max_iterations"]:
            raise ContractError(f"{label}.iteration exceeds request.max_iterations")
        _safe_id(step["stage"], f"{label}.stage")
        _text(step["objective"], f"{label}.objective")
        _text(step["necessity"], f"{label}.necessity")
        question_refs = set(_safe_ref_array(step["subquestion_ids"], f"{label}.subquestion_ids", minimum=1, maximum=12))
        _require_subset(question_refs, subquestion_ids, f"{label}.subquestion_ids")
        route_subquestion_coverage.update(question_refs)
        step_subquestion_refs[step["id"]] = question_refs
        required_datasets = set(
            _safe_ref_array(
                step["required_dataset_ids"],
                f"{label}.required_dataset_ids",
                maximum=20,
            )
        )
        _require_subset(required_datasets, dataset_ids, f"{label}.required_dataset_ids")
        used_dataset_ids.update(required_datasets)
        consumes = set(_safe_ref_array(step["consumes_artifact_ids"], f"{label}.consumes_artifact_ids", maximum=30))
        produces = set(_safe_ref_array(step["produces_artifact_ids"], f"{label}.produces_artifact_ids", minimum=1, maximum=30))
        _require_subset(consumes | produces, artifact_ids, f"{label} artifact references")
        if consumes & produces:
            raise ContractError(f"{label} must produce immutable artifacts instead of overwriting inputs")
        prerequisites = set(_safe_ref_array(step["prerequisite_step_ids"], f"{label}.prerequisite_step_ids", maximum=30))
        _enum(step["join_policy"], {"all", "any"}, f"{label}.join_policy")
        method_outline = _text(
            step["method_outline"], f"{label}.method_outline", maximum=4_000
        )
        missing_named_datasets = sorted(
            dataset_id
            for dataset_id, dataset_name in dataset_names.items()
            if len(dataset_name.strip()) >= 4
            and dataset_name.casefold() in method_outline.casefold()
            and dataset_id not in required_datasets
        )
        if missing_named_datasets:
            raise ContractError(
                f"{label}.method_outline explicitly names datasets absent from required_dataset_ids: "
                f"{missing_named_datasets}"
            )
        capability_needs, _ = _unique_id_objects(step["capability_needs"], f"{label}.capability_needs", minimum=0, maximum=12)
        capability_fields = {"id", "purpose", "input_types", "output_types", "constraints"}
        for capability_index, capability in enumerate(capability_needs):
            capability_label = f"{label}.capability_needs[{capability_index}]"
            _exact_fields(capability, capability_fields, capability_label)
            _text(capability["purpose"], f"{capability_label}.purpose")
            _text_array(capability["input_types"], f"{capability_label}.input_types", maximum=20)
            _text_array(capability["output_types"], f"{capability_label}.output_types", minimum=1, maximum=20)
            _text_array(capability["constraints"], f"{capability_label}.constraints", maximum=20)
        outcome_rules = _array(step["outcome_rules"], f"{label}.outcome_rules", minimum=2, maximum=6)
        rule_outcomes: set[str] = set()
        for rule_index, rule_value in enumerate(outcome_rules):
            rule_label = f"{label}.outcome_rules[{rule_index}]"
            rule = _object(rule_value, rule_label)
            _exact_fields(rule, {"outcome", "criteria", "evidence_required"}, rule_label)
            outcome = _enum(rule["outcome"], ROUTE_OUTCOME, f"{rule_label}.outcome")
            if outcome in rule_outcomes:
                raise ContractError(f"{label}.outcome_rules has duplicate outcome: {outcome}")
            rule_outcomes.add(outcome)
            criteria = _text_array(
                rule["criteria"], f"{rule_label}.criteria", minimum=1, maximum=20
            )
            step_numeric_outcome_criteria.setdefault(step["id"], []).extend(
                criterion
                for criterion in criteria
                if _contains_hard_numeric_cutoff(criterion)
            )
            evidence_required = _text_array(rule["evidence_required"], f"{rule_label}.evidence_required", maximum=20)
            if outcome == "completed" and not evidence_required:
                raise ContractError(f"{rule_label} completed outcome requires evidence_required")
        if "completed" not in rule_outcomes or len(rule_outcomes) < 2:
            raise ContractError(f"{label}.outcome_rules must include completed and at least one non-completed outcome")
        transitions = _array(step["transitions"], f"{label}.transitions", minimum=2, maximum=6)
        transition_outcomes: set[str] = set()
        for transition_index, transition_value in enumerate(transitions):
            transition_label = f"{label}.transitions[{transition_index}]"
            transition = _object(transition_value, transition_label)
            if set(transition) not in ({"on", "target_step_id"}, {"on", "terminal_status"}):
                raise ContractError(f"{transition_label} must contain on and exactly one destination")
            outcome = _enum(transition["on"], ROUTE_OUTCOME, f"{transition_label}.on")
            if outcome in transition_outcomes:
                raise ContractError(f"{label}.transitions has duplicate outcome: {outcome}")
            transition_outcomes.add(outcome)
            if "target_step_id" in transition:
                target = _safe_ref(transition["target_step_id"], f"{transition_label}.target_step_id")
                control_graph[step["id"]].add(target)
            else:
                terminal = _enum(transition["terminal_status"], TERMINAL_STATUS, f"{transition_label}.terminal_status")
                terminal_steps.add(step["id"])
                terminal_statuses_used.add(terminal)
        if transition_outcomes != rule_outcomes:
            raise ContractError(f"{label}.transitions must exactly cover its outcome_rules")
        visit_limit = _integer(step["visit_limit"], f"{label}.visit_limit", minimum=1, maximum=12)
        if visit_limit > request["max_iterations"]:
            raise ContractError(f"{label}.visit_limit exceeds request.max_iterations")
        if not request["self_correction_enabled"] and visit_limit != 1:
            raise ContractError(f"{label}.visit_limit must be 1 when self_correction_enabled=false")
        step_evaluation_refs[step["id"]] = set(_safe_ref_array(step["evaluation_rule_ids"], f"{label}.evaluation_rule_ids", maximum=30))
        step_consumes[step["id"]] = consumes
        step_produces[step["id"]] = produces
        explicit_prerequisites[step["id"]] = prerequisites
    missing_route_coverage = sorted(subquestion_ids - route_subquestion_coverage)
    if missing_route_coverage:
        raise ContractError(f"research_route must cover every subquestion; missing={missing_route_coverage}")
    unused_datasets = sorted(dataset_ids - used_dataset_ids)
    if unused_datasets:
        raise ContractError(
            "required_datasets contains data not used by any research step; "
            f"unused={unused_datasets}"
        )
    for step_id, prerequisites in explicit_prerequisites.items():
        _require_subset(prerequisites, route_step_ids, f"route step {step_id}.prerequisite_step_ids")
        if step_id in prerequisites:
            raise ContractError(f"route step {step_id} cannot be its own prerequisite")
    for step_id, targets in control_graph.items():
        _require_subset(targets, route_step_ids, f"route step {step_id}.transitions")

    actual_artifact_producers: dict[str, str] = {}
    for step_id, produces in step_produces.items():
        for artifact_id in produces:
            if artifact_id in actual_artifact_producers:
                raise ContractError(f"artifact {artifact_id} has multiple producing steps")
            actual_artifact_producers[artifact_id] = step_id
    for artifact_id, claimed_producer in artifact_producer_claims.items():
        actual_producer = actual_artifact_producers.get(artifact_id)
        if claimed_producer != actual_producer:
            raise ContractError(f"artifact {artifact_id} producer_step_id disagrees with research_route")

    static_dependencies: dict[str, set[str]] = {step_id: set(values) for step_id, values in explicit_prerequisites.items()}
    for step_id, consumes in step_consumes.items():
        for artifact_id in consumes:
            producer = artifact_producer_claims[artifact_id]
            if producer is not None:
                if producer == step_id:
                    raise ContractError(f"route step {step_id} cannot consume its own output artifact")
                static_dependencies[step_id].add(producer)
    _assert_acyclic(static_dependencies, "research_route data dependencies")
    for step_id, dependencies in static_dependencies.items():
        step_iteration = step_by_id[step_id]["iteration"]
        for dependency in dependencies:
            if step_by_id[dependency]["iteration"] > step_iteration:
                raise ContractError(
                    f"route step {step_id} depends on later-iteration step {dependency}"
                )
        if step_by_id[step_id]["join_policy"] == "any" and len(dependencies) < 2:
            raise ContractError(f"route step {step_id} join_policy=any requires at least two dependencies")

    entry_steps = {
        step_id for step_id, dependencies in static_dependencies.items() if not dependencies
    }
    control_reachable = set(entry_steps)
    stack = list(entry_steps)
    while stack:
        node = stack.pop()
        for target in control_graph[node]:
            if target not in control_reachable:
                control_reachable.add(target)
                stack.append(target)
    unreachable_steps = sorted(route_step_ids - control_reachable)
    if unreachable_steps:
        raise ContractError(
            "research_route has steps unreachable from an entry step; "
            f"missing={unreachable_steps}"
        )

    transition_by_step = {
        step["id"]: {transition["on"]: transition for transition in step["transitions"]}
        for step in route_steps
    }
    join_policies = {
        route_step_id: step_by_id[route_step_id]["join_policy"]
        for route_step_id in route_step_ids
    }
    for step_id, transitions in transition_by_step.items():
        for outcome, transition in transitions.items():
            target = transition.get("target_step_id")
            if target is None or target == step_id or outcome == "completed":
                continue
            if _requires_completed_step(
                static_dependencies, join_policies, target, step_id
            ):
                raise ContractError(
                    format_illegal_transition_message(step_id, outcome, target)
                )

    cyclic = _cyclic_nodes(control_graph)
    if cyclic and not request["self_correction_enabled"]:
        raise ContractError("research_route control cycles require self_correction_enabled=true")
    for step_id in cyclic:
        if step_by_id[step_id]["visit_limit"] < 2:
            raise ContractError(f"cyclic route step {step_id} requires visit_limit >= 2")

    reverse_control: dict[str, set[str]] = {step_id: set() for step_id in route_step_ids}
    for source, targets in control_graph.items():
        for target in targets:
            reverse_control[target].add(source)
    can_reach_terminal = set(terminal_steps)
    stack = list(terminal_steps)
    while stack:
        node = stack.pop()
        for predecessor in reverse_control[node]:
            if predecessor not in can_reach_terminal:
                can_reach_terminal.add(predecessor)
                stack.append(predecessor)
    missing_terminal_path = sorted(route_step_ids - can_reach_terminal)
    if missing_terminal_path:
        raise ContractError(f"every route step must reach a terminal status; missing={missing_terminal_path}")

    for item_id, resolution_steps in state_step_refs.items():
        _require_subset(resolution_steps, route_step_ids, f"state item {item_id}.resolution_step_ids")
        item = next(row for row in state_items if row["id"] == item_id)
        if item["blocking"] and item["status"] != "not_required" and not resolution_steps:
            raise ContractError(f"blocking state item {item_id} requires resolution_step_ids")

    evaluation_rules, evaluation_rule_ids = _unique_id_objects(
        content["evaluation_rules"],
        "planner_response.plan_content.evaluation_rules",
        minimum=0,
        maximum=30,
    )
    evaluation_fields = {
        "id",
        "name",
        "purpose",
        "target_step_ids",
        "outcome",
        "check",
        "interpretation",
        "uncertainty",
        "criterion_basis",
    }
    evaluation_targets: dict[str, set[str]] = {}
    evaluation_basis_kinds: dict[str, str] = {}

    ancestor_cache: dict[str, set[str]] = {}

    def ancestor_steps(step_id: str) -> set[str]:
        if step_id in ancestor_cache:
            return ancestor_cache[step_id]
        ancestors: set[str] = set()
        for dependency in static_dependencies[step_id]:
            ancestors.add(dependency)
            ancestors.update(ancestor_steps(dependency))
        ancestor_cache[step_id] = ancestors
        return ancestors

    def artifact_available_to_step(artifact_id: str, step_id: str) -> bool:
        if artifact_id in step_consumes[step_id] or artifact_id in step_produces[step_id]:
            return True
        producer = artifact_producer_claims[artifact_id]
        return producer is not None and producer in ancestor_steps(step_id)

    for index, rule in enumerate(evaluation_rules):
        label = f"planner_response.plan_content.evaluation_rules[{index}]"
        _exact_fields(rule, evaluation_fields, label)
        _text(rule["name"], f"{label}.name")
        _text(rule["purpose"], f"{label}.purpose")
        targets = set(_safe_ref_array(rule["target_step_ids"], f"{label}.target_step_ids", minimum=1, maximum=30))
        _require_subset(targets, route_step_ids, f"{label}.target_step_ids")
        outcome = _enum(rule["outcome"], ROUTE_OUTCOME, f"{label}.outcome")
        for target in targets:
            if outcome not in transition_by_step[target]:
                raise ContractError(f"{label}.outcome is not defined by target step {target}")
        check = _text(rule["check"], f"{label}.check")
        interpretation = _text(rule["interpretation"], f"{label}.interpretation")
        _text(rule["uncertainty"], f"{label}.uncertainty")
        basis = _object(rule["criterion_basis"], f"{label}.criterion_basis")
        _exact_fields(
            basis,
            {"kind", "basis_text", "evidence_source_ids", "artifact_ids"},
            f"{label}.criterion_basis",
        )
        basis_kind = _enum(
            basis["kind"], CRITERION_BASIS_KIND, f"{label}.criterion_basis.kind"
        )
        basis_text = _text(basis["basis_text"], f"{label}.criterion_basis.basis_text")
        basis_source_ids = set(
            _safe_ref_array(
                basis["evidence_source_ids"],
                f"{label}.criterion_basis.evidence_source_ids",
                maximum=40,
            )
        )
        basis_artifact_ids = set(
            _safe_ref_array(
                basis["artifact_ids"],
                f"{label}.criterion_basis.artifact_ids",
                maximum=60,
            )
        )
        _require_subset(
            basis_source_ids,
            evidence_source_ids,
            f"{label}.criterion_basis.evidence_source_ids",
        )
        _require_subset(
            basis_artifact_ids,
            artifact_ids,
            f"{label}.criterion_basis.artifact_ids",
        )
        if basis_kind == "source_based":
            if not basis_source_ids or basis_artifact_ids:
                raise ContractError(
                    f"{label}.criterion_basis source_based requires evidence_source_ids and no artifact_ids"
                )
            unqualified = sorted(
                source_id
                for source_id in basis_source_ids
                if evidence_quality[source_id][1] not in {"claim_located", "dataset_inspected"}
            )
            if unqualified:
                raise ContractError(
                    f"{label}.criterion_basis source_based requires located evidence; "
                    f"unqualified={unqualified}"
                )
        elif basis_kind == "data_based":
            if not basis_artifact_ids or basis_source_ids:
                raise ContractError(
                    f"{label}.criterion_basis data_based requires artifact_ids and no evidence_source_ids"
                )
            unavailable_targets = sorted(
                target
                for target in targets
                if not any(
                    artifact_available_to_step(artifact_id, target)
                    for artifact_id in basis_artifact_ids
                )
            )
            if unavailable_targets:
                raise ContractError(
                    f"{label}.criterion_basis artifacts are not available to target steps: "
                    f"{unavailable_targets}"
                )
        elif basis_kind == "request_based":
            if basis_source_ids or basis_artifact_ids:
                raise ContractError(
                    f"{label}.criterion_basis request_based must not cite evidence or artifacts"
                )
            if not any(basis_text in request_text for request_text in request_text_values):
                raise ContractError(
                    f"{label}.criterion_basis request_based basis_text must be copied from the canonical request"
                )
        else:
            if basis_source_ids or basis_artifact_ids:
                raise ContractError(
                    f"{label}.criterion_basis qualitative must not cite evidence or artifacts"
                )
            if _contains_hard_numeric_cutoff(check, interpretation):
                raise ContractError(
                    f"{label} uses an obvious numeric cutoff without a source-, data-, or request-based criterion"
                )
        evaluation_targets[rule["id"]] = targets
        evaluation_basis_kinds[rule["id"]] = basis_kind
    for step_id, rule_refs in step_evaluation_refs.items():
        _require_subset(rule_refs, evaluation_rule_ids, f"route step {step_id}.evaluation_rule_ids")
        for rule_id in rule_refs:
            if step_id not in evaluation_targets[rule_id]:
                raise ContractError(f"route step {step_id} and evaluation rule {rule_id} must reference each other")
    for rule_id, targets in evaluation_targets.items():
        for step_id in targets:
            if rule_id not in step_evaluation_refs[step_id]:
                raise ContractError(f"evaluation rule {rule_id} and route step {step_id} must reference each other")

    grounded_basis_kinds = {"source_based", "data_based", "request_based"}

    def step_has_grounded_criterion(step_id: str) -> bool:
        return any(
            evaluation_basis_kinds.get(rule_id) in grounded_basis_kinds
            for rule_id in step_evaluation_refs[step_id]
        )

    for step_id, criteria in step_numeric_outcome_criteria.items():
        if criteria and not step_has_grounded_criterion(step_id):
            raise ContractError(
                f"route step {step_id} uses fixed numeric completion criteria without a "
                "source-, data-, or request-based evaluation rule"
            )
    for subquestion_id, completion_text in subquestion_completion_text.items():
        if not _contains_hard_numeric_cutoff(completion_text):
            continue
        related_steps = {
            step_id
            for step_id, question_refs in step_subquestion_refs.items()
            if subquestion_id in question_refs
        }
        if not any(step_has_grounded_criterion(step_id) for step_id in related_steps):
            raise ContractError(
                f"subquestion {subquestion_id}.completion_evidence uses a fixed numeric cutoff "
                "without a source-, data-, or request-based evaluation rule"
            )

    report_sections, report_section_ids = _unique_id_objects(
        content["report_outline"],
        "planner_response.plan_content.report_outline",
        minimum=1,
        maximum=15,
    )
    report_fields = {"id", "order", "title", "purpose", "source_step_ids"}
    orders: set[int] = set()
    reported_steps: set[str] = set()
    for index, section in enumerate(report_sections):
        label = f"planner_response.plan_content.report_outline[{index}]"
        _exact_fields(section, report_fields, label)
        order = _integer(section["order"], f"{label}.order", minimum=1, maximum=30)
        if order in orders:
            raise ContractError(f"report_outline has duplicate order: {order}")
        orders.add(order)
        _text(section["title"], f"{label}.title", maximum=300)
        _text(section["purpose"], f"{label}.purpose")
        source_steps = set(_safe_ref_array(section["source_step_ids"], f"{label}.source_step_ids", maximum=30))
        _require_subset(source_steps, route_step_ids, f"{label}.source_step_ids")
        reported_steps.update(source_steps)
    if orders != set(range(1, len(report_sections) + 1)):
        raise ContractError("report_outline.order must be contiguous from 1")
    missing_report_steps = sorted(route_step_ids - reported_steps)
    if missing_report_steps:
        raise ContractError(f"report_outline must cover every route step; missing={missing_report_steps}")

    policy = _object(content["iteration_policy"], "planner_response.plan_content.iteration_policy")
    policy_fields = {"global_visit_limit", "review_step_ids", "revision_triggers", "budget_response"}
    _exact_fields(policy, policy_fields, "planner_response.plan_content.iteration_policy")
    global_visit_limit = _integer(policy["global_visit_limit"], "planner_response.plan_content.iteration_policy.global_visit_limit", minimum=1, maximum=360)
    if global_visit_limit < len(route_steps):
        raise ContractError("iteration_policy.global_visit_limit must permit one visit per route step")
    if global_visit_limit > len(route_steps) * request["max_iterations"]:
        raise ContractError("iteration_policy.global_visit_limit exceeds the request route budget")
    review_steps = set(_safe_ref_array(policy["review_step_ids"], "planner_response.plan_content.iteration_policy.review_step_ids", maximum=30))
    _require_subset(review_steps, route_step_ids, "iteration_policy.review_step_ids")
    revision_triggers = {
        _enum(value, ROUTE_OUTCOME, f"planner_response.plan_content.iteration_policy.revision_triggers[{index}]")
        for index, value in enumerate(
            _array(policy["revision_triggers"], "planner_response.plan_content.iteration_policy.revision_triggers", maximum=6)
        )
    }
    if len(revision_triggers) != len(policy["revision_triggers"]):
        raise ContractError("iteration_policy.revision_triggers must be unique")
    if not request["self_correction_enabled"] and revision_triggers:
        raise ContractError("iteration_policy.revision_triggers must be empty when self_correction_enabled=false")
    budget_response = _enum(policy["budget_response"], TERMINAL_STATUS, "planner_response.plan_content.iteration_policy.budget_response")
    terminal_statuses_used.add(budget_response)

    stop_rules, _ = _unique_id_objects(
        content["stop_rules"],
        "planner_response.plan_content.stop_rules",
        minimum=1,
        maximum=20,
    )
    stop_fields = {"id", "terminal_status", "condition_kind", "condition", "required_evidence", "report_section_ids"}
    covered_terminal_statuses: set[str] = set()
    for index, rule in enumerate(stop_rules):
        label = f"planner_response.plan_content.stop_rules[{index}]"
        _exact_fields(rule, stop_fields, label)
        terminal = _enum(rule["terminal_status"], TERMINAL_STATUS, f"{label}.terminal_status")
        covered_terminal_statuses.add(terminal)
        _enum(rule["condition_kind"], STOP_CONDITION_KIND, f"{label}.condition_kind")
        _text(rule["condition"], f"{label}.condition")
        _text_array(rule["required_evidence"], f"{label}.required_evidence", maximum=20)
        report_refs = set(_safe_ref_array(rule["report_section_ids"], f"{label}.report_section_ids", minimum=1, maximum=15))
        _require_subset(report_refs, report_section_ids, f"{label}.report_section_ids")
    missing_stop_rules = sorted(terminal_statuses_used - covered_terminal_statuses)
    if missing_stop_rules:
        raise ContractError(f"stop_rules must cover every used terminal status; missing={missing_stop_rules}")
    return content


def validate_planner_response(
    payload: dict[str, Any], request_payload: dict[str, Any]
) -> dict[str, Any]:
    request = validate_planner_request(request_payload)
    response = clone_json(_object(payload, "planner_response"), "planner_response")
    if response.get("schema_version") != RESPONSE_VERSION:
        raise ContractError(f"planner_response.schema_version must be {RESPONSE_VERSION}")
    response_kind = _enum(response.get("response_kind"), RESPONSE_KINDS, "planner_response.response_kind")
    _safe_id(response.get("task_name"), "planner_response.task_name")
    _text(response.get("research_question"), "planner_response.research_question", minimum=8, maximum=4_000, strip=False)
    if response["task_name"] != request["task_name"]:
        raise ContractError("planner_response.task_name must exactly match the request")
    if response["research_question"] != request["research_question"]:
        raise ContractError("planner_response.research_question must exactly match the request")
    if response_kind == "clarification_needed":
        _validate_clarification_response(response)
    elif response_kind == "planning_blocked":
        _validate_blocked_response(response)
    else:
        fields = {"schema_version", "task_name", "research_question", "response_kind", "plan_content"}
        _exact_fields(response, fields, "planner_response")
        _validate_plan_content(response["plan_content"], request)
    return response


def validate_research_plan(payload: dict[str, Any]) -> dict[str, Any]:
    plan = clone_json(_object(payload, "research_plan"), "research_plan")
    deterministic_fields = {
        "schema_version",
        "plan_id",
        "created_at",
        "status",
        "planning_readiness",
        "request_sha256",
        "plan_sha256",
        "input_data_sources",
        "configuration",
    }
    plan_fields = {
        "task_name",
        "research_question",
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
    }
    _exact_fields(plan, deterministic_fields | plan_fields, "research_plan")
    if plan["schema_version"] != PLAN_VERSION:
        raise ContractError(f"research_plan.schema_version must be {PLAN_VERSION}")
    _safe_ref(plan["plan_id"], "research_plan.plan_id")
    timestamp = _text(plan["created_at"], "research_plan.created_at", maximum=100)
    try:
        parsed = datetime.fromisoformat(timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp)
    except ValueError as exc:
        raise ContractError("research_plan.created_at must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ContractError("research_plan.created_at must be UTC")
    if plan["status"] != "frozen":
        raise ContractError("research_plan.status must be frozen")
    _enum(plan["planning_readiness"], {"complete", "external_inputs_required"}, "research_plan.planning_readiness")
    for field in ("request_sha256", "plan_sha256"):
        value = _text(plan[field], f"research_plan.{field}", maximum=64)
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ContractError(f"research_plan.{field} must be a lowercase SHA-256")
    configuration = _object(plan["configuration"], "research_plan.configuration")
    _exact_fields(
        configuration,
        {"max_iterations", "hypothesis_review_enabled", "self_correction_enabled"},
        "research_plan.configuration",
    )
    request = {
        "schema_version": REQUEST_VERSION,
        "task_name": plan["task_name"],
        "research_question": plan["research_question"],
        "data_sources": plan["input_data_sources"],
        "max_iterations": configuration["max_iterations"],
        "hypothesis_review_enabled": configuration["hypothesis_review_enabled"],
        "self_correction_enabled": configuration["self_correction_enabled"],
    }
    validate_planner_request(request)
    if canonical_json_sha256(request) != plan["request_sha256"]:
        raise ContractError("research_plan.request_sha256 does not match embedded input")
    response = {
        "schema_version": RESPONSE_VERSION,
        "task_name": plan["task_name"],
        "research_question": plan["research_question"],
        "response_kind": "plan_ready",
        "plan_content": {key: plan[key] for key in plan_fields - {"task_name", "research_question"}},
    }
    validate_planner_response(response, request)
    state_items = plan["research_state_map"]["items"]
    expected_readiness = (
        "external_inputs_required"
        if any(dataset["acquisition_status"] != "selected" for dataset in plan["required_datasets"])
        or any(item["blocking"] and item["status"] in {"unresolved", "unavailable"} for item in state_items)
        else "complete"
    )
    if plan["planning_readiness"] != expected_readiness:
        raise ContractError(f"research_plan.planning_readiness must be {expected_readiness}")
    unhashed = dict(plan)
    supplied = unhashed.pop("plan_sha256")
    if canonical_json_sha256(unhashed) != supplied:
        raise ContractError("research_plan.plan_sha256 does not match canonical plan")
    return plan


__all__ = [
    "ContractError",
    "OUTCOME_VERSION",
    "PLAN_VERSION",
    "REQUEST_VERSION",
    "RESPONSE_VERSION",
    "canonical_json_sha256",
    "clone_json",
    "validate_planner_request",
    "validate_planner_response",
    "validate_research_plan",
]
