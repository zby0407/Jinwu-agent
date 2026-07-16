"""Deterministic orchestration for Research Planner 1.0."""

from __future__ import annotations

import json
import re
import shutil
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import (
    CLAIM_LOCATOR_HINT,
    HARD_NUMERIC_CUTOFF,
    OUTCOME_VERSION,
    PLAN_VERSION,
    REQUEST_VERSION,
    RESPONSE_VERSION,
    ContractError,
    canonical_json_sha256,
    validate_planner_request,
    validate_planner_response,
    validate_research_plan,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SPEC_ROOT = PROJECT_ROOT / "planner" / "specs"
RUNS_ROOT = PROJECT_ROOT / "planner" / "runs"
REQUEST_SCHEMA_PATH = SPEC_ROOT / "planner_request_v1.schema.json"
RESPONSE_SCHEMA_PATH = SPEC_ROOT / "planner_response_v1.schema.json"
PLAN_SCHEMA_PATH = SPEC_ROOT / "research_plan_v1.schema.json"
OUTCOME_SCHEMA_PATH = SPEC_ROOT / "planner_outcome_v1.schema.json"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"required planner schema is unavailable or invalid: {path}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"required planner schema must be an object: {path}")
    return value


def _schema_const(schema: dict[str, Any], response: bool = False) -> str | None:
    if not response:
        value = schema.get("properties", {}).get("schema_version", {}).get("const")
        return value if isinstance(value, str) else None
    variants = schema.get("$defs", {})
    constants = {
        variant.get("properties", {}).get("schema_version", {}).get("const")
        for name, variant in variants.items()
        if name in {"planReadyResponse", "clarificationResponse", "blockedResponse"}
        and isinstance(variant, dict)
    }
    return constants.pop() if len(constants) == 1 else None


def audit_harness() -> dict[str, Any]:
    schemas = {
        "request": _load_json(REQUEST_SCHEMA_PATH),
        "response": _load_json(RESPONSE_SCHEMA_PATH),
        "plan": _load_json(PLAN_SCHEMA_PATH),
        "outcome": _load_json(OUTCOME_SCHEMA_PATH),
    }
    expected = {
        "request": REQUEST_VERSION,
        "response": RESPONSE_VERSION,
        "plan": PLAN_VERSION,
        "outcome": OUTCOME_VERSION,
    }
    actual = {
        "request": _schema_const(schemas["request"]),
        "response": _schema_const(schemas["response"], response=True),
        "plan": _schema_const(schemas["plan"]),
        "outcome": _schema_const(schemas["outcome"]),
    }
    for name, version in expected.items():
        if actual[name] != version:
            raise ContractError(f"{name} schema version drift")
    response_defs = schemas["response"].get("$defs", {})
    response_variants = {
        "planReadyResponse": "plan_ready",
        "clarificationResponse": "clarification_needed",
        "blockedResponse": "planning_blocked",
    }
    one_of_refs = {
        row.get("$ref")
        for row in schemas["response"].get("oneOf", [])
        if isinstance(row, dict)
    }
    expected_refs = {f"#/$defs/{name}" for name in response_variants}
    if one_of_refs != expected_refs:
        raise ContractError("response schema discriminator variants drift")
    for name, kind in response_variants.items():
        actual_kind = (
            response_defs.get(name, {})
            .get("properties", {})
            .get("response_kind", {})
            .get("const")
        )
        if actual_kind != kind:
            raise ContractError(f"response schema kind drift: {name}")
    expected_route_outcomes = {
        "completed",
        "inconclusive",
        "input_missing",
        "evidence_conflict",
        "method_invalid",
        "budget_reached",
    }
    expected_terminal_statuses = {
        "plan_complete",
        "partial_result",
        "needs_input",
        "no_viable_route",
    }
    if set(response_defs.get("routeOutcome", {}).get("enum", [])) != expected_route_outcomes:
        raise ContractError("response schema route outcome drift")
    if set(response_defs.get("terminalStatus", {}).get("enum", [])) != expected_terminal_statuses:
        raise ContractError("response schema terminal status drift")
    if response_defs.get("refArray", {}).get("maxItems") != 40:
        raise ContractError("response schema reference bound drift")
    return {
        "schema_version": "research-planner-harness-audit-v1",
        "status": "passed",
        "contract_versions": expected,
        "response_kinds": ["plan_ready", "clarification_needed", "planning_blocked"],
        "experiment_registry_present": False,
        "experiment_data_required": False,
        "agent_relationships_present": False,
        "runtime_dependencies": "python-standard-library-only",
        "planner_tools": [
            "brief binding",
            "bundled local knowledge search",
            "literature metadata search",
            "reference resolution",
            "source evidence extraction",
            "dataset inspection",
            "deterministic plan validation",
            "validated plan freeze",
        ],
    }


def build_natural_planner_request(research_question: str) -> dict[str, Any]:
    """Create the canonical Pi request used for ordinary natural-language input."""

    if not isinstance(research_question, str):
        raise ContractError("research_question must be a string")
    normalized = research_question.strip()
    request = {
        "schema_version": REQUEST_VERSION,
        "task_name": f"question_{canonical_json_sha256({'research_question': normalized})[:12]}",
        "research_question": normalized,
        "data_sources": [],
        "max_iterations": 2,
        "hypothesis_review_enabled": True,
        "self_correction_enabled": True,
    }
    return validate_planner_request(request)


def _compact_response_contract() -> dict[str, Any]:
    """Return a complete typed field guide without embedding JSON Schema machinery."""

    plan_content_shape = {
        "scope": {
            "objective": "string",
            "population_or_period": "string",
            "boundaries": ["string"],
            "non_goals": ["string"],
        },
        "research_subquestions": [
            {
                "id": "id",
                "question": "string",
                "purpose": "string",
                "depends_on": ["subquestion_id"],
                "completion_evidence": "string",
            }
        ],
        "research_state_map": {
            "items": [
                {
                    "id": "id",
                    "statement": "string",
                    "item_kind": "allowed value",
                    "status": "allowed value",
                    "rationale": "string",
                    "evidence_source_ids": ["evidence_source_id"],
                    "subquestion_ids": ["subquestion_id"],
                    "blocking": "boolean",
                    "resolution_requirements": ["string"],
                    "resolution_step_ids": ["route_step_id"],
                    "impact_if_wrong": "string",
                    "confidence": {"level": "allowed value", "basis": "string"},
                }
            ]
        },
        "evidence_sources": [
            {
                "id": "id",
                "citation": "string",
                "locator": "string",
                "source_kind": "allowed value",
                "verification_level": "allowed value",
                "role": "string",
                "state_item_ids": ["state_item_id"],
                "subquestion_ids": ["subquestion_id"],
                "limitations": "string",
            }
        ],
        "required_datasets": [
            {
                "id": "id",
                "source_kind": "allowed value",
                "selected_source_id": "request_source_id or null",
                "name": "string",
                "purpose": "string",
                "required_variables": ["string"],
                "time_coverage_needed": "string",
                "cadence_needed": "string",
                "quality_requirements": ["string"],
                "version_requirement": "string",
                "unit_requirements": ["string"],
                "revision_requirements": ["string"],
                "license_requirements": ["string"],
                "acquisition_status": "allowed value",
            }
        ],
        "research_artifacts": [
            {
                "id": "id",
                "name": "string",
                "artifact_kind": "id",
                "purpose": "string",
                "source_kind": "allowed value",
                "producer_step_id": "route_step_id or null",
                "subquestion_ids": ["subquestion_id"],
                "content_requirements": ["string"],
            }
        ],
        "research_route": [
            {
                "id": "id",
                "iteration": "integer",
                "stage": "id",
                "objective": "string",
                "necessity": "string",
                "subquestion_ids": ["subquestion_id"],
                "required_dataset_ids": ["dataset_id"],
                "consumes_artifact_ids": ["artifact_id"],
                "produces_artifact_ids": ["artifact_id"],
                "prerequisite_step_ids": ["route_step_id"],
                "join_policy": "all or any",
                "method_outline": "string",
                "capability_needs": [
                    {
                        "id": "id",
                        "purpose": "string",
                        "input_types": ["string"],
                        "output_types": ["string"],
                        "constraints": ["string"],
                    }
                ],
                "outcome_rules": [
                    {
                        "outcome": "allowed value",
                        "criteria": ["string"],
                        "evidence_required": ["string"],
                    }
                ],
                "transitions": [
                    {
                        "on": "allowed value",
                        "target_step_id": "route_step_id (omit terminal_status)",
                    },
                    {
                        "on": "allowed value",
                        "terminal_status": "terminal status (omit target_step_id)",
                    },
                ],
                "visit_limit": "integer",
                "evaluation_rule_ids": ["evaluation_rule_id"],
            }
        ],
        "evaluation_rules": [
            {
                "id": "id",
                "name": "string",
                "purpose": "string",
                "target_step_ids": ["route_step_id"],
                "outcome": "allowed value",
                "check": "string",
                "interpretation": "string",
                "uncertainty": "string",
                "criterion_basis": {
                    "kind": "allowed value",
                    "basis_text": "string",
                    "evidence_source_ids": ["evidence_source_id"],
                    "artifact_ids": ["artifact_id"],
                },
            }
        ],
        "report_outline": [
            {
                "id": "id",
                "order": "integer",
                "title": "natural-language title",
                "purpose": "string",
                "source_step_ids": ["route_step_id"],
            }
        ],
        "iteration_policy": {
            "global_visit_limit": "integer",
            "review_step_ids": ["route_step_id"],
            "revision_triggers": ["route outcome"],
            "budget_response": "terminal status",
        },
        "stop_rules": [
            {
                "id": "id",
                "terminal_status": "allowed value",
                "condition_kind": "allowed value",
                "condition": "string",
                "required_evidence": ["string"],
                "report_section_ids": ["report_section_id"],
            }
        ],
    }
    return {
        "title": "Research Planner Response 1.0",
        "schema_version": RESPONSE_VERSION,
        "response_shapes": {
            "plan_ready": {
                "schema_version": RESPONSE_VERSION,
                "task_name": "copy request.task_name exactly",
                "research_question": "copy request.research_question exactly",
                "response_kind": "plan_ready",
                "plan_content": plan_content_shape,
            },
            "clarification_needed": {
                "schema_version": RESPONSE_VERSION,
                "task_name": "copy request.task_name exactly",
                "research_question": "copy request.research_question exactly",
                "response_kind": "clarification_needed",
                "questions": [
                    {
                        "id": "id",
                        "question": "string",
                        "why_it_matters": "string",
                        "expected_answer": "string",
                    }
                ],
            },
            "planning_blocked": {
                "schema_version": RESPONSE_VERSION,
                "task_name": "copy request.task_name exactly",
                "research_question": "copy request.research_question exactly",
                "response_kind": "planning_blocked",
                "blockers": [
                    {
                        "id": "id",
                        "code": "allowed value",
                        "reason": "string",
                        "recoverable": "boolean",
                        "resolution": "string",
                    }
                ],
            },
        },
        "array_rule": (
            "Every field shown with square brackets is a JSON array even when it has zero or one item; "
            "never replace an array with a string. Empty arrays are allowed only where the integrity rules permit them."
        ),
        "allowed_values": {
            "item_kind": [
                "supported_finding",
                "working_assumption",
                "testable_hypothesis",
                "evidence_gap",
                "evidence_conflict",
            ],
            "state_status": [
                "supported",
                "partially_supported",
                "unresolved",
                "unavailable",
                "not_required",
            ],
            "confidence_level": ["high", "medium", "low", "unknown"],
            "route_outcome": [
                "completed",
                "inconclusive",
                "input_missing",
                "evidence_conflict",
                "method_invalid",
                "budget_reached",
            ],
            "terminal_status": [
                "plan_complete",
                "partial_result",
                "needs_input",
                "no_viable_route",
            ],
            "join_policy": ["all", "any"],
            "dataset_source_kind": ["selected", "proposed"],
            "dataset_acquisition_status": ["selected", "missing", "needs_confirmation"],
            "artifact_source_kind": ["request_input", "external_input", "planned_output"],
            "evidence_source_kind": [
                "local_material",
                "scholarly",
                "dataset_metadata",
                "user_provided",
            ],
            "stop_condition_kind": [
                "goal_satisfied",
                "evidence_sufficient",
                "no_viable_route",
                "budget_exhausted",
                "human_stop",
                "unsafe_to_continue",
                "partial_result_ready",
                "input_required",
            ],
            "blocker_code": [
                "unsupported_scope",
                "unresearchable_formulation",
                "missing_indispensable_condition",
                "safety_boundary",
            ],
        },
        "integrity_rules": [
            "Use only the fields listed for the selected response kind.",
            "Copy task_name and research_question exactly from the canonical request.",
            "All ids must start with a letter and all references must resolve.",
            "Every evidence source and linked state item must reference each other in both directions.",
            "Each route step must include completed plus at least one non-completed outcome; transitions must cover exactly those outcomes.",
            "Each planned_output artifact has exactly one matching producer step; input artifacts have a null producer_step_id.",
            "Subquestion and artifact dependency graphs are acyclic; conditional revisits are bounded.",
            "global_visit_limit is at least the step count and no more than step count multiplied by request.max_iterations.",
            "Report sections cover every route step and stop rules cover every used terminal status.",
            "Keep field content concise and reader-facing; do not put schema names, enum names, tool calls, validation language, or persistence language into scientific prose.",
        ],
    }


def build_planning_brief(request_payload: dict[str, Any]) -> dict[str, Any]:
    request = validate_planner_request(request_payload)
    audit = audit_harness()
    return {
        "schema_version": "research-planner-brief-v1",
        "request_sha256": canonical_json_sha256(request),
        "request": request,
        "harness_audit": audit,
        "planner_contract": {
            "response_kinds": ["plan_ready", "clarification_needed", "planning_blocked"],
            "plan_outputs": [
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
            ],
        },
        "harness_owned": [
            "exact request binding",
            "closed-field and cross-reference validation",
            "subquestion and data-dependency acyclicity",
            "typed transition reachability and bounded-cycle checks",
            "research-state and evidence bidirectional traceability",
            "evidence verification level and claim-level locator checks",
            "step-to-dataset usage and evaluation-basis traceability",
            "artifact producer-consumer integrity",
            "iteration-budget enforcement",
            "immutable ids, timestamps, hashes, readiness, and persistence",
        ],
        "model_owned": [
            "whether the request is plan-ready, needs clarification, or is blocked",
            "research scope and a dependency-aware decomposition",
            "a research state map that distinguishes findings, assumptions, hypotheses, gaps, and conflicts",
            "evidence and dataset requirements without fabricating availability",
            "measurement-role separation across definitions, proxies, phase markers, and context variables",
            "a need-driven route with explicit artifacts, outcomes, transitions, and terminal states",
            "step-specific evaluation rules, report outline, and stop rules",
        ],
        "hard_boundaries": [
            "Ask for clarification only when an ambiguity would materially change the route; a scientific evidence gap belongs in research_state_map.",
            "Derive the smallest sufficient route from the question; do not import a familiar method checklist or predefined experiment registry.",
            "Do not assume any dataset exists beyond user-provided descriptions and verified metadata.",
            "A proposed dataset must be missing or needs_confirmation.",
            "A request may contain zero data sources.",
            "Every supported_finding must cite evidence; hypotheses and gaps must not be presented as findings.",
            "A resolved DOI, URL, or bibliography record is only source discovery; supported content requires a claim-level locator or applicable dataset inspection.",
            "Every route step must list the datasets it directly uses, and every required dataset must be used by at least one step.",
            "Every evaluation rule must state whether its basis comes from located evidence, planned data, an exact user requirement, or a qualitative check without a fixed numeric cutoff.",
            "Do not merge an operational definition, proxy indicator, phase marker, contextual variable, or validation reference into one measurement role; unresolved roles remain research gaps.",
            "Use capability_needs only for abstract input/output requirements; do not name a concrete tool or Agent.",
            "The route is a plan only and must not claim that an experiment ran or produced results.",
            "Every non-completed route outcome must have a typed transition or terminal status.",
            "Keep all iterations and visit limits within request.max_iterations.",
        ],
        "response_contract": _compact_response_contract(),
        "instruction": (
            "Construct exactly one research-planner-response-v1 object. Submit that object as "
            "one JSON string in the response_json argument of the validation tool. Do not add "
            "run ids, hashes, execution results, tool bindings, or prose to the response object."
        ),
    }


def _require_plan_ready(response: dict[str, Any]) -> None:
    if response["response_kind"] != "plan_ready":
        raise ContractError(
            "only a plan_ready research-planner-response-v1 can be compiled or frozen"
        )


def _planning_readiness(plan_content: dict[str, Any]) -> str:
    missing_dataset = any(
        dataset["acquisition_status"] != "selected"
        for dataset in plan_content["required_datasets"]
    )
    unresolved_blocker = any(
        item["blocking"] and item["status"] in {"unresolved", "unavailable"}
        for item in plan_content["research_state_map"]["items"]
    )
    return "external_inputs_required" if missing_dataset or unresolved_blocker else "complete"


def compile_research_plan(
    request_payload: dict[str, Any], response_payload: dict[str, Any]
) -> dict[str, Any]:
    request = validate_planner_request(request_payload)
    response = validate_planner_response(response_payload, request)
    _require_plan_ready(response)
    audit_harness()
    created_at = datetime.now(timezone.utc).isoformat()
    plan_id = (
        f"{request['task_name']}-"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-"
        f"{uuid.uuid4().hex[:8]}"
    )
    content = response["plan_content"]
    plan = {
        "schema_version": PLAN_VERSION,
        "plan_id": plan_id,
        "created_at": created_at,
        "status": "frozen",
        "planning_readiness": _planning_readiness(content),
        "request_sha256": canonical_json_sha256(request),
        "input_data_sources": deepcopy(request["data_sources"]),
        "configuration": {
            "max_iterations": request["max_iterations"],
            "hypothesis_review_enabled": request["hypothesis_review_enabled"],
            "self_correction_enabled": request["self_correction_enabled"],
        },
        "task_name": response["task_name"],
        "research_question": response["research_question"],
        **deepcopy(content),
    }
    plan["plan_sha256"] = canonical_json_sha256(plan)
    validate_research_plan(plan)
    return plan


def _json_type_name(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return type(value).__name__


def _matches_json_type(value: object, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True


def _resolve_local_schema_ref(
    schema: dict[str, Any], root_schema: dict[str, Any]
) -> dict[str, Any]:
    reference = schema.get("$ref")
    if not isinstance(reference, str) or not reference.startswith("#/"):
        return schema
    current: object = root_schema
    for part in reference[2:].split("/"):
        if not isinstance(current, dict) or part not in current:
            return schema
        current = current[part]
    return current if isinstance(current, dict) else schema


def _collect_schema_shape_errors(
    value: object,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    path: str,
    errors: list[str],
) -> None:
    """Collect structural errors useful to the model before semantic validation."""

    schema = _resolve_local_schema_ref(schema, root_schema)
    expected = schema.get("type")
    if isinstance(expected, str):
        allowed_types = [expected]
    elif isinstance(expected, list):
        allowed_types = [item for item in expected if isinstance(item, str)]
    else:
        allowed_types = []
    if allowed_types and not any(_matches_json_type(value, item) for item in allowed_types):
        errors.append(
            f"{path}: 需要 {' 或 '.join(allowed_types)}，实际为 {_json_type_name(value)}"
        )
        return

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: 必须为 {schema['const']!r}")
    allowed_values = schema.get("enum")
    if isinstance(allowed_values, list) and value not in allowed_values:
        errors.append(
            f"{path}: 值 {value!r} 不可用；允许值为 {allowed_values}"
        )

    if isinstance(value, dict):
        required = {
            item for item in schema.get("required", []) if isinstance(item, str)
        }
        missing = sorted(required - set(value))
        if missing:
            errors.append(f"{path}: 缺少字段 {missing}")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            properties = {}
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(value) - set(properties))
            if unknown:
                errors.append(f"{path}: 存在未定义字段 {unknown}")
        for key, child_schema in properties.items():
            if key in value and isinstance(child_schema, dict):
                _collect_schema_shape_errors(
                    value[key], child_schema, root_schema, f"{path}.{key}", errors
                )
        if schema.get("oneOf") and {
            "target_step_id",
            "terminal_status",
        } <= set(properties):
            destination_count = int("target_step_id" in value) + int(
                "terminal_status" in value
            )
            if destination_count != 1:
                errors.append(
                    f"{path}: target_step_id 与 terminal_status 必须且只能填写一个"
                )
        return

    if isinstance(value, list):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and len(value) < minimum:
            errors.append(f"{path}: 至少需要 {minimum} 项，实际为 {len(value)} 项")
        if isinstance(maximum, int) and len(value) > maximum:
            errors.append(f"{path}: 最多允许 {maximum} 项，实际为 {len(value)} 项")
        if schema.get("uniqueItems"):
            serialized = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in value]
            if len(serialized) != len(set(serialized)):
                errors.append(f"{path}: 数组项必须唯一")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _collect_schema_shape_errors(
                    item, item_schema, root_schema, f"{path}[{index}]", errors
                )
        return

    if isinstance(value, str):
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if isinstance(minimum, int) and len(value) < minimum:
            errors.append(f"{path}: 字符数不能少于 {minimum}")
        if isinstance(maximum, int) and len(value) > maximum:
            errors.append(f"{path}: 字符数不能超过 {maximum}")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            errors.append(f"{path}: 文本格式不符合要求")


def collect_planner_response_shape_errors(response_payload: object) -> list[str]:
    """Return all readily detectable response-shape errors in one pass."""

    schema = _load_json(RESPONSE_SCHEMA_PATH)
    if not isinstance(response_payload, dict):
        return [
            f"planner_response: 需要 object，实际为 {_json_type_name(response_payload)}"
        ]
    kind = response_payload.get("response_kind")
    variants = {
        "plan_ready": "planReadyResponse",
        "clarification_needed": "clarificationResponse",
        "planning_blocked": "blockedResponse",
    }
    variant_name = variants.get(kind)
    if variant_name is None:
        return [
            "planner_response.response_kind: 必须为 plan_ready、clarification_needed 或 planning_blocked"
        ]
    variant = schema.get("$defs", {}).get(variant_name, {})
    errors: list[str] = []
    if kind == "plan_ready" and "plan_content" not in response_payload:
        errors.append(
            "planner_response.plan_content: 缺少规划内容对象；scope 至 stop_rules 必须全部放在 plan_content 内"
        )
        plan_fields = set(
            schema.get("$defs", {}).get("planContent", {}).get("properties", {})
        )
        flat_content = {
            key: response_payload[key] for key in plan_fields if key in response_payload
        }
        if flat_content:
            _collect_schema_shape_errors(
                flat_content,
                {"$ref": "#/$defs/planContent"},
                schema,
                "planner_response.plan_content",
                errors,
            )
        common_fields = {
            key: response_payload[key]
            for key in ("schema_version", "task_name", "research_question", "response_kind")
            if key in response_payload
        }
        for key in ("schema_version", "task_name", "research_question", "response_kind"):
            child_schema = variant.get("properties", {}).get(key)
            if key in common_fields and isinstance(child_schema, dict):
                _collect_schema_shape_errors(
                    common_fields[key], child_schema, schema, f"planner_response.{key}", errors
                )
    else:
        _collect_schema_shape_errors(
            response_payload, variant, schema, "planner_response", errors
        )
    return errors


def collect_planner_route_semantic_errors(response_payload: object) -> list[str]:
    """Collect all obvious non-completed transitions into success-only dependencies."""

    if not isinstance(response_payload, dict) or response_payload.get("response_kind") != "plan_ready":
        return []
    content = response_payload.get("plan_content")
    if not isinstance(content, dict):
        return []
    route = content.get("research_route")
    artifacts = content.get("research_artifacts")
    if not isinstance(route, list) or not isinstance(artifacts, list):
        return []
    steps = {
        step.get("id"): step
        for step in route
        if isinstance(step, dict) and isinstance(step.get("id"), str)
    }
    if not steps:
        return []
    producers = {
        artifact.get("id"): artifact.get("producer_step_id")
        for artifact in artifacts
        if isinstance(artifact, dict) and isinstance(artifact.get("id"), str)
    }
    dependencies: dict[str, set[str]] = {step_id: set() for step_id in steps}
    join_policies: dict[str, str] = {}
    for step_id, step in steps.items():
        prerequisites = step.get("prerequisite_step_ids")
        if isinstance(prerequisites, list):
            dependencies[step_id].update(
                value for value in prerequisites if isinstance(value, str) and value in steps
            )
        consumes = step.get("consumes_artifact_ids")
        if isinstance(consumes, list):
            for artifact_id in consumes:
                producer = producers.get(artifact_id)
                if isinstance(producer, str) and producer in steps and producer != step_id:
                    dependencies[step_id].add(producer)
        join_policies[step_id] = step.get("join_policy") if step.get("join_policy") in {"all", "any"} else "all"

    visiting: set[str] = set()
    visited: set[str] = set()

    def acyclic(node: str) -> bool:
        if node in visiting:
            return False
        if node in visited:
            return True
        visiting.add(node)
        for dependency in dependencies[node]:
            if not acyclic(dependency):
                return False
        visiting.remove(node)
        visited.add(node)
        return True

    if not all(acyclic(step_id) for step_id in steps):
        return []

    memo: dict[tuple[str, str], bool] = {}

    def requires_completed(node: str, source: str) -> bool:
        key = (node, source)
        if key in memo:
            return memo[key]
        node_dependencies = dependencies[node]
        if not node_dependencies:
            memo[key] = False
            return False
        requirements = [
            dependency == source or requires_completed(dependency, source)
            for dependency in node_dependencies
        ]
        required = (
            all(requirements)
            if join_policies[node] == "any"
            else any(requirements)
        )
        memo[key] = required
        return required

    errors: list[str] = []
    for step_id, step in steps.items():
        transitions = step.get("transitions")
        if not isinstance(transitions, list):
            continue
        for transition in transitions:
            if not isinstance(transition, dict):
                continue
            outcome = transition.get("on")
            target = transition.get("target_step_id")
            if (
                not isinstance(outcome, str)
                or not isinstance(target, str)
                or target not in steps
                or target == step_id
                or outcome == "completed"
            ):
                continue
            if requires_completed(target, step_id):
                errors.append(
                    f"route step {step_id} outcome {outcome} cannot transition to {target}; "
                    "the target requires this step to complete"
                )
    return errors


def collect_planner_scientific_semantic_errors(
    request_payload: object, response_payload: object
) -> list[str]:
    """Collect independent evidence, data, and criterion-closure errors."""

    if (
        not isinstance(request_payload, dict)
        or not isinstance(response_payload, dict)
        or response_payload.get("response_kind") != "plan_ready"
    ):
        return []
    content = response_payload.get("plan_content")
    if not isinstance(content, dict):
        return []

    errors: list[str] = []
    sources = {
        source.get("id"): source
        for source in content.get("evidence_sources", [])
        if isinstance(source, dict) and isinstance(source.get("id"), str)
    }
    for source_id, source in sources.items():
        level = source.get("verification_level")
        locator = source.get("locator")
        if (
            level == "claim_located"
            and isinstance(locator, str)
            and CLAIM_LOCATOR_HINT.search(locator) is None
        ):
            errors.append(
                f"evidence source {source_id} is marked claim_located but has no page-, section-, "
                "paragraph-, line-, figure-, or table-level locator"
            )
        if level == "dataset_inspected" and source.get("source_kind") != "dataset_metadata":
            errors.append(
                f"evidence source {source_id} uses dataset_inspected without dataset_metadata source_kind"
            )

    request_text_values: list[str] = []
    for value in (
        request_payload.get("task_name"),
        request_payload.get("research_question"),
    ):
        if isinstance(value, str):
            request_text_values.append(value)
    for source in request_payload.get("data_sources", []):
        if not isinstance(source, dict):
            continue
        request_text_values.extend(
            value
            for value in (
                source.get("name"),
                source.get("description"),
                source.get("location"),
                *(source.get("constraints", []) if isinstance(source.get("constraints"), list) else []),
            )
            if isinstance(value, str)
        )

    state_map = content.get("research_state_map")
    state_items = state_map.get("items", []) if isinstance(state_map, dict) else []
    for item in state_items:
        if not isinstance(item, dict):
            continue
        qualified = False
        for source_id in item.get("evidence_source_ids", []):
            source = sources.get(source_id)
            if not isinstance(source, dict):
                continue
            level = source.get("verification_level")
            if level == "claim_located" or (
                level == "dataset_inspected"
                and source.get("source_kind") == "dataset_metadata"
            ):
                qualified = True
                break
        if item.get("status") in {"supported", "partially_supported"} and not qualified:
            errors.append(
                f"research state item {item.get('id', '<unknown>')} is supported or partially supported "
                "but only has unverified or reference-level sources"
            )
        statement = item.get("statement")
        if (
            item.get("item_kind") == "testable_hypothesis"
            and isinstance(statement, str)
            and HARD_NUMERIC_CUTOFF.search(statement) is not None
            and not qualified
        ):
            phrases = [
                match.group(0).strip()
                for match in HARD_NUMERIC_CUTOFF.finditer(statement)
            ]
            request_grounded = bool(phrases) and all(
                any(phrase in request_text for request_text in request_text_values)
                for phrase in phrases
            )
            if not request_grounded:
                errors.append(
                    f"testable hypothesis {item.get('id', '<unknown>')} contains a fixed numeric "
                    "magnitude without claim-located evidence or an exact user-request basis"
                )

    dataset_rows = {
        dataset.get("id"): dataset
        for dataset in content.get("required_datasets", [])
        if isinstance(dataset, dict) and isinstance(dataset.get("id"), str)
    }
    dataset_ids = set(dataset_rows)
    used_dataset_ids: set[str] = set()
    step_rows: dict[str, dict[str, object]] = {}
    step_evaluation_refs: dict[str, set[str]] = {}
    step_subquestion_refs: dict[str, set[str]] = {}
    step_numeric_criteria: dict[str, list[str]] = {}
    for step in content.get("research_route", []):
        if not isinstance(step, dict):
            continue
        step_id = step.get("id")
        if isinstance(step_id, str):
            step_rows[step_id] = step
            step_evaluation_refs[step_id] = {
                value
                for value in step.get("evaluation_rule_ids", [])
                if isinstance(value, str)
            }
            step_subquestion_refs[step_id] = {
                value for value in step.get("subquestion_ids", []) if isinstance(value, str)
            }
            numeric_criteria: list[str] = []
            for outcome_rule in step.get("outcome_rules", []):
                if not isinstance(outcome_rule, dict):
                    continue
                numeric_criteria.extend(
                    criterion
                    for criterion in outcome_rule.get("criteria", [])
                    if isinstance(criterion, str)
                    and HARD_NUMERIC_CUTOFF.search(criterion) is not None
                )
            step_numeric_criteria[step_id] = numeric_criteria
        refs = step.get("required_dataset_ids")
        if isinstance(refs, list):
            used_dataset_ids.update(ref for ref in refs if isinstance(ref, str))
            method_outline = step.get("method_outline")
            if isinstance(method_outline, str):
                missing_named = sorted(
                    dataset_id
                    for dataset_id, dataset in dataset_rows.items()
                    if isinstance(dataset.get("name"), str)
                    and len(dataset["name"].strip()) >= 4
                    and dataset["name"].casefold() in method_outline.casefold()
                    and dataset_id not in refs
                )
                if missing_named:
                    errors.append(
                        f"route step {step.get('id', '<unknown>')} names datasets absent from "
                        "required_dataset_ids: "
                        + ", ".join(missing_named)
                    )
    unused_datasets = sorted(dataset_ids - used_dataset_ids)
    if unused_datasets:
        errors.append(
            "required datasets are not used by any research step: "
            + ", ".join(unused_datasets)
        )

    request_text = json.dumps(request_payload, ensure_ascii=False, sort_keys=True)
    evaluation_basis_kinds: dict[str, str] = {}
    for rule in content.get("evaluation_rules", []):
        if not isinstance(rule, dict):
            continue
        basis = rule.get("criterion_basis")
        if not isinstance(basis, dict):
            continue
        rule_id = rule.get("id", "<unknown>")
        kind = basis.get("kind")
        source_ids = [
            source_id
            for source_id in basis.get("evidence_source_ids", [])
            if isinstance(source_id, str)
        ]
        artifact_ids = [
            artifact_id
            for artifact_id in basis.get("artifact_ids", [])
            if isinstance(artifact_id, str)
        ]
        if isinstance(rule_id, str) and isinstance(kind, str):
            evaluation_basis_kinds[rule_id] = kind
        if kind == "source_based":
            if not source_ids or artifact_ids:
                errors.append(
                    f"evaluation rule {rule_id} uses source_based but must cite evidence only"
                )
            if not any(
                isinstance(sources.get(source_id), dict)
                and sources[source_id].get("verification_level")
                in {"claim_located", "dataset_inspected"}
                for source_id in source_ids
            ):
                errors.append(
                    f"evaluation rule {rule_id} has a source-based criterion without located evidence"
                )
        elif kind == "data_based":
            if not artifact_ids or source_ids:
                errors.append(
                    f"evaluation rule {rule_id} uses data_based but must cite planned artifacts only"
                )
        elif kind == "request_based":
            if source_ids or artifact_ids:
                errors.append(
                    f"evaluation rule {rule_id} uses request_based but cites evidence or artifacts"
                )
            basis_text = basis.get("basis_text")
            if isinstance(basis_text, str) and basis_text not in request_text:
                errors.append(
                    f"evaluation rule {rule_id} says its criterion came from the request, "
                    "but the cited wording is absent from the canonical request"
                )
        elif kind == "qualitative":
            if source_ids or artifact_ids:
                errors.append(
                    f"evaluation rule {rule_id} uses qualitative but cites evidence or artifacts"
                )
            check = rule.get("check")
            interpretation = rule.get("interpretation")
            if (
                isinstance(check, str)
                and isinstance(interpretation, str)
                and (
                    HARD_NUMERIC_CUTOFF.search(check) is not None
                    or HARD_NUMERIC_CUTOFF.search(interpretation) is not None
                )
            ):
                errors.append(
                    f"evaluation rule {rule_id} contains an obvious numeric cutoff without a "
                    "source-, data-, or request-based criterion"
                )

    grounded_kinds = {"source_based", "data_based", "request_based"}

    def step_has_grounded_basis(step_id: str) -> bool:
        return any(
            evaluation_basis_kinds.get(rule_id) in grounded_kinds
            for rule_id in step_evaluation_refs.get(step_id, set())
        )

    for step_id, criteria in step_numeric_criteria.items():
        if criteria and not step_has_grounded_basis(step_id):
            errors.append(
                f"route step {step_id} uses fixed numeric completion criteria without a "
                "source-, data-, or request-based evaluation rule"
            )
    for subquestion in content.get("research_subquestions", []):
        if not isinstance(subquestion, dict):
            continue
        subquestion_id = subquestion.get("id")
        completion = subquestion.get("completion_evidence")
        if (
            not isinstance(subquestion_id, str)
            or not isinstance(completion, str)
            or HARD_NUMERIC_CUTOFF.search(completion) is None
        ):
            continue
        related_steps = {
            step_id
            for step_id, refs in step_subquestion_refs.items()
            if subquestion_id in refs
        }
        if not any(step_has_grounded_basis(step_id) for step_id in related_steps):
            errors.append(
                f"subquestion {subquestion_id} uses a fixed numeric completion cutoff without a "
                "source-, data-, or request-based evaluation rule"
            )
    return list(dict.fromkeys(errors))


def _normalize_model_response(
    request: dict[str, Any], response_payload: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """Apply only mechanical, content-preserving corrections before preflight."""

    response = deepcopy(response_payload)
    notes: list[str] = []
    for key, expected in (
        ("schema_version", RESPONSE_VERSION),
        ("task_name", request["task_name"]),
        ("research_question", request["research_question"]),
    ):
        if response.get(key) != expected:
            response[key] = expected
            notes.append(f"bound {key} to the canonical request")

    content_fields = {
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
    if response.get("response_kind") == "plan_ready" and "plan_content" not in response:
        flat_content = {key: response.pop(key) for key in list(response) if key in content_fields}
        if flat_content:
            response["plan_content"] = flat_content
            notes.append("wrapped flat plan fields in plan_content")

    def normalize_list(container: object, key: str, path: str) -> None:
        if not isinstance(container, dict) or key not in container:
            return
        value = container[key]
        if isinstance(value, str):
            container[key] = [value] if value.strip() else []
            notes.append(f"converted {path} to an array")
            value = container[key]
        if not isinstance(value, list):
            return
        unique: list[object] = []
        seen: set[str] = set()
        for item in value:
            marker = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if marker in seen:
                continue
            seen.add(marker)
            unique.append(item)
        if len(unique) != len(value):
            container[key] = unique
            notes.append(f"removed duplicate items from {path}")

    def object_list(value: object) -> list[object]:
        return value if isinstance(value, list) else []

    content = response.get("plan_content")
    if isinstance(content, dict):
        scope = content.get("scope")
        normalize_list(scope, "boundaries", "scope.boundaries")
        normalize_list(scope, "non_goals", "scope.non_goals")
        for index, item in enumerate(object_list(content.get("research_subquestions"))):
            normalize_list(item, "depends_on", f"research_subquestions[{index}].depends_on")
        state_map = content.get("research_state_map")
        state_items = object_list(state_map.get("items")) if isinstance(state_map, dict) else []
        for index, item in enumerate(state_items):
            for key in (
                "evidence_source_ids",
                "subquestion_ids",
                "resolution_requirements",
                "resolution_step_ids",
            ):
                normalize_list(item, key, f"research_state_map.items[{index}].{key}")
        for index, item in enumerate(object_list(content.get("evidence_sources"))):
            for key in ("state_item_ids", "subquestion_ids"):
                normalize_list(item, key, f"evidence_sources[{index}].{key}")
        for index, item in enumerate(object_list(content.get("required_datasets"))):
            for key in (
                "required_variables",
                "quality_requirements",
                "unit_requirements",
                "revision_requirements",
                "license_requirements",
            ):
                normalize_list(item, key, f"required_datasets[{index}].{key}")
        for index, item in enumerate(object_list(content.get("research_artifacts"))):
            for key in ("subquestion_ids", "content_requirements"):
                normalize_list(item, key, f"research_artifacts[{index}].{key}")
        for index, step in enumerate(object_list(content.get("research_route"))):
            if not isinstance(step, dict):
                continue
            for key in (
                "subquestion_ids",
                "required_dataset_ids",
                "consumes_artifact_ids",
                "produces_artifact_ids",
                "prerequisite_step_ids",
                "evaluation_rule_ids",
            ):
                normalize_list(step, key, f"research_route[{index}].{key}")
            for child_index, capability in enumerate(object_list(step.get("capability_needs"))):
                for key in ("input_types", "output_types", "constraints"):
                    normalize_list(
                        capability,
                        key,
                        f"research_route[{index}].capability_needs[{child_index}].{key}",
                    )
            for child_index, rule in enumerate(object_list(step.get("outcome_rules"))):
                for key in ("criteria", "evidence_required"):
                    normalize_list(
                        rule,
                        key,
                        f"research_route[{index}].outcome_rules[{child_index}].{key}",
                    )
        for index, item in enumerate(object_list(content.get("evaluation_rules"))):
            normalize_list(item, "target_step_ids", f"evaluation_rules[{index}].target_step_ids")
            basis = item.get("criterion_basis") if isinstance(item, dict) else None
            normalize_list(
                basis,
                "evidence_source_ids",
                f"evaluation_rules[{index}].criterion_basis.evidence_source_ids",
            )
            normalize_list(
                basis,
                "artifact_ids",
                f"evaluation_rules[{index}].criterion_basis.artifact_ids",
            )
            if isinstance(basis, dict):
                basis_aliases = {
                    "located_evidence": "source_based",
                    "planned_data": "data_based",
                    "user_requirement": "request_based",
                    "qualitative_check": "qualitative",
                }
                kind = basis.get("kind")
                if isinstance(kind, str) and kind in basis_aliases:
                    basis["kind"] = basis_aliases[kind]
                    notes.append(
                        f"normalized evaluation_rules[{index}].criterion_basis.kind "
                        f"from {kind} to {basis_aliases[kind]}"
                    )
                kind = basis.get("kind")
                source_ids = basis.get("evidence_source_ids")
                artifact_ids = basis.get("artifact_ids")
                has_sources = isinstance(source_ids, list) and bool(source_ids)
                has_artifacts = isinstance(artifact_ids, list) and bool(artifact_ids)
                if kind == "qualitative" and has_sources != has_artifacts:
                    inferred_kind = "source_based" if has_sources else "data_based"
                    basis["kind"] = inferred_kind
                    notes.append(
                        f"normalized evaluation_rules[{index}].criterion_basis.kind "
                        f"from qualitative to {inferred_kind} based on its sole reference family"
                    )
        for index, item in enumerate(object_list(content.get("report_outline"))):
            normalize_list(item, "source_step_ids", f"report_outline[{index}].source_step_ids")
        policy = content.get("iteration_policy")
        normalize_list(policy, "review_step_ids", "iteration_policy.review_step_ids")
        normalize_list(policy, "revision_triggers", "iteration_policy.revision_triggers")
        for index, item in enumerate(object_list(content.get("stop_rules"))):
            normalize_list(item, "required_evidence", f"stop_rules[{index}].required_evidence")
            normalize_list(item, "report_section_ids", f"stop_rules[{index}].report_section_ids")
    return response, notes


def preflight_planner_response(
    request_payload: dict[str, Any],
    response_payload: dict[str, Any],
    *,
    include_validated_response: bool = False,
) -> dict[str, Any]:
    """Validate any response kind without creating ids, timestamps, files, or runs."""

    request = validate_planner_request(request_payload)
    normalized_response, normalization_notes = _normalize_model_response(
        request, response_payload
    )
    shape_errors = collect_planner_response_shape_errors(normalized_response)
    if shape_errors:
        formatted = "\n".join(f"- {error}" for error in shape_errors)
        raise ContractError(
            f"结构检查发现 {len(shape_errors)} 处问题，请一次性修正：\n{formatted}"
        )
    route_errors = collect_planner_route_semantic_errors(normalized_response)
    scientific_errors = collect_planner_scientific_semantic_errors(
        request, normalized_response
    )
    try:
        response = validate_planner_response(normalized_response, request)
    except ContractError as exc:
        errors = [str(exc), *route_errors, *scientific_errors]
        unique_errors = list(dict.fromkeys(errors))
        if len(unique_errors) == 1:
            raise
        formatted = "\n".join(f"- {error}" for error in unique_errors)
        raise ContractError(
            f"科研与路线检查发现 {len(unique_errors)} 组问题，请一次性修正：\n{formatted}"
        ) from exc
    if route_errors or scientific_errors:
        unique_errors = list(dict.fromkeys([*route_errors, *scientific_errors]))
        formatted = "\n".join(f"- {error}" for error in unique_errors)
        raise ContractError(
            f"科研与路线检查发现 {len(unique_errors)} 组问题，请一次性修正：\n{formatted}"
        )
    result: dict[str, Any] = {
        "schema_version": "research-planner-preflight-v1",
        "status": response["response_kind"],
        "task_name": request["task_name"],
        "research_question": request["research_question"],
        "request_sha256": canonical_json_sha256(request),
        "files_written": 0,
        "experiments_executed": 0,
        "mechanical_normalization_count": len(normalization_notes),
    }
    if response["response_kind"] == "clarification_needed":
        result["question_count"] = len(response["questions"])
        result["user_display_markdown"] = render_nonplan_response_markdown(response)
        return result
    if response["response_kind"] == "planning_blocked":
        result["blocker_count"] = len(response["blockers"])
        result["user_display_markdown"] = render_nonplan_response_markdown(response)
        return result
    content = response["plan_content"]
    result.update(
        {
            "subquestion_count": len(content["research_subquestions"]),
            "state_item_count": len(content["research_state_map"]["items"]),
            "evidence_source_count": len(content["evidence_sources"]),
            "required_dataset_count": len(content["required_datasets"]),
            "artifact_count": len(content["research_artifacts"]),
            "route_step_count": len(content["research_route"]),
            "evaluation_rule_count": len(content["evaluation_rules"]),
        }
    )
    if include_validated_response:
        result["_validated_response"] = response
    return result


def _safe_run_segment(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-")
    return normalized[:64] or "research-plan"


_READER_TERM_REPLACEMENTS = {
    "clarification_needed": "需要补充关键信息",
    "planning_blocked": "当前条件下无法规划",
    "evidence_conflict": "证据相互冲突",
    "dataset_inspected": "已检查数据说明",
    "reference_resolved": "已确认来源信息",
    "claim_located": "已定位到具体依据",
    "budget_reached": "达到本轮工作上限",
    "method_invalid": "方法不适用",
    "input_missing": "缺少必要输入",
    "inconclusive": "暂时无法判断",
    "no_viable_route": "没有可行路线",
    "partial_result": "形成阶段性结果",
    "plan_complete": "完成研究目标",
    "needs_input": "需要补充输入",
    "plan_ready": "规划已就绪",
}


def _markdown_text(value: object) -> str:
    text = str(value)
    for internal_term, reader_term in _READER_TERM_REPLACEMENTS.items():
        text = text.replace(internal_term, reader_term)
    return " ".join(text.replace("\r", " ").replace("\n", " ").split())


def _markdown_list(values: list[object]) -> str:
    return "；".join(_markdown_text(value) for value in values) if values else "无"


def render_nonplan_response_markdown(response_payload: dict[str, Any]) -> str:
    """Render a checked clarification or blocked response without machine terminology."""

    response_kind = response_payload.get("response_kind")
    if response_kind == "clarification_needed":
        lines = [
            "# 还需要你确认",
            "",
            "下面的信息会实质改变研究路线。确认后即可继续生成完整研究规划书。",
            "",
        ]
        for index, item in enumerate(response_payload["questions"], start=1):
            lines.extend(
                [
                    f"{index}. **{_markdown_text(item['question'])}**",
                    f"   - 为什么需要确认：{_markdown_text(item['why_it_matters'])}",
                    f"   - 请补充：{_markdown_text(item['expected_answer'])}",
                ]
            )
        return "\n".join(lines).rstrip() + "\n"
    if response_kind == "planning_blocked":
        lines = [
            "# 暂时无法形成研究规划",
            "",
            "当前条件下还不能形成诚实、可执行的研究方案。",
            "",
        ]
        for item in response_payload["blockers"]:
            lines.extend(
                [
                    f"- **原因：** {_markdown_text(item['reason'])}",
                    f"  - 如何继续：{_markdown_text(item['resolution'])}",
                ]
            )
        return "\n".join(lines).rstrip() + "\n"
    raise ContractError("reader response rendering requires clarification or blocked response")


def render_research_plan_markdown(plan_payload: dict[str, Any]) -> str:
    """Render the checked machine plan as a reader-facing Chinese Markdown report."""

    plan = validate_research_plan(plan_payload)
    questions = {item["id"]: item for item in plan["research_subquestions"]}
    sources = {item["id"]: item for item in plan["evidence_sources"]}
    datasets = {item["id"]: item for item in plan["required_datasets"]}
    artifacts = {item["id"]: item for item in plan["research_artifacts"]}
    steps = {item["id"]: item for item in plan["research_route"]}
    step_number = {
        item["id"]: index for index, item in enumerate(plan["research_route"], start=1)
    }
    report_sections = {item["id"]: item for item in plan["report_outline"]}

    state_titles = {
        "supported_finding": "已有依据",
        "working_assumption": "暂定前提",
        "testable_hypothesis": "待检验判断",
        "evidence_gap": "尚待确认",
        "evidence_conflict": "证据存在分歧",
    }
    confidence_titles = {
        "high": "较高",
        "medium": "中等",
        "low": "较低",
        "unknown": "暂无法判断",
    }
    source_titles = {
        "local_material": "本地材料",
        "scholarly": "学术文献",
        "dataset_metadata": "数据说明",
        "user_provided": "用户提供材料",
    }
    verification_titles = {
        "unverified": "尚未核实",
        "reference_resolved": "已确认来源，但尚未定位支持内容",
        "claim_located": "已定位到与主张相关的具体内容",
        "dataset_inspected": "已检查数据说明或数据文件",
    }
    criterion_basis_titles = {
        "source_based": "来自已定位的资料依据",
        "data_based": "计划根据数据及其不确定性判定",
        "request_based": "来自用户明确提出的要求",
        "qualitative": "采用不设固定数值门槛的定性检查",
    }
    dataset_status_titles = {
        "selected": "已确认",
        "missing": "尚未取得",
        "needs_confirmation": "需要确认",
    }
    outcome_titles = {
        "completed": "达到本步目标",
        "inconclusive": "暂时无法作出判断",
        "input_missing": "缺少必要资料或数据",
        "evidence_conflict": "证据相互冲突",
        "method_invalid": "当前方法不适用",
        "budget_reached": "达到本轮工作上限",
    }
    terminal_titles = {
        "plan_complete": "形成完整研究结论",
        "partial_result": "形成可说明局限的阶段性结果",
        "needs_input": "等待补充必要资料",
        "no_viable_route": "记录当前没有可行研究路线",
    }

    lines: list[str] = [
        "# 研究规划书",
        "",
        f"**研究问题：** {_markdown_text(plan['research_question'])}",
        "",
        "本文件是一份待执行的研究方案，用于说明要回答什么、需要哪些依据、按什么顺序推进，以及在何种情况下形成完整或阶段性结论。文中不包含已经完成的实验或分析结果。",
        "",
        "## 研究目标与范围",
        "",
        _markdown_text(plan["scope"]["objective"]),
        "",
        f"**研究对象或时间范围：** {_markdown_text(plan['scope']['population_or_period'])}",
        "",
        "**研究边界：**",
        "",
    ]
    lines.extend(f"- {_markdown_text(item)}" for item in plan["scope"]["boundaries"])
    lines.extend(["", "**本次不处理：**", ""])
    lines.extend(f"- {_markdown_text(item)}" for item in plan["scope"]["non_goals"])

    lines.extend(["", "## 需要回答的核心问题", ""])
    for index, item in enumerate(plan["research_subquestions"], start=1):
        lines.extend(
            [
                f"### {index}. {_markdown_text(item['question'])}",
                "",
                f"研究作用：{_markdown_text(item['purpose'])}",
                "",
                f"完成标志：{_markdown_text(item['completion_evidence'])}",
            ]
        )
        if item["depends_on"]:
            dependencies = [
                questions[dependency]["question"] for dependency in item["depends_on"]
            ]
            lines.extend(
                ["", f"需先回答：{_markdown_list(dependencies)}"]
            )
        lines.append("")

    lines.extend(["## 目前认识与仍需核实的问题", ""])
    grouped_state: dict[str, list[dict[str, Any]]] = {
        key: [] for key in state_titles
    }
    for item in plan["research_state_map"]["items"]:
        grouped_state[item["item_kind"]].append(item)
    for kind, title in state_titles.items():
        items = grouped_state[kind]
        if not items:
            continue
        lines.extend([f"### {title}", ""])
        for item in items:
            evidence = [
                sources[source_id]["citation"]
                for source_id in item["evidence_source_ids"]
                if source_id in sources
            ]
            lines.append(f"- **{_markdown_text(item['statement'])}**")
            lines.append(f"  - 判断依据：{_markdown_text(item['rationale'])}")
            lines.append(
                "  - 当前把握："
                f"{confidence_titles[item['confidence']['level']]}；"
                f"{_markdown_text(item['confidence']['basis'])}"
            )
            if evidence:
                lines.append(f"  - 相关资料：{_markdown_list(evidence)}")
            if item["resolution_requirements"]:
                lines.append(
                    f"  - 需要补充：{_markdown_list(item['resolution_requirements'])}"
                )
            lines.append(f"  - 判断有误的影响：{_markdown_text(item['impact_if_wrong'])}")
            if item["blocking"]:
                lines.append("  - 该事项未解决前，不宜进入依赖它的后续判断。")
        lines.append("")

    lines.extend(["## 资料依据", ""])
    if not plan["evidence_sources"]:
        lines.extend(
            [
                "当前没有可直接引用的证据来源。后续工作应先获取并核对与研究问题直接相关的材料，不能把检索结果摘要当作已经证实的结论。",
                "",
            ]
        )
    else:
        for index, source in enumerate(plan["evidence_sources"], start=1):
            lines.extend(
                [
                    f"### 资料 {index}：{_markdown_text(source['citation'])}",
                    "",
                    f"资料类型：{source_titles[source['source_kind']]}",
                    "",
                    f"证据核实情况：{verification_titles[source['verification_level']]}",
                    "",
                    f"查找位置：{_markdown_text(source['locator'])}",
                    "",
                    f"在本研究中的作用：{_markdown_text(source['role'])}",
                    "",
                    f"使用限制：{_markdown_text(source['limitations'])}",
                    "",
                ]
            )

    lines.extend(["## 数据需求", ""])
    if not plan["required_datasets"]:
        lines.extend(["当前规划不要求预先指定数据集。", ""])
    else:
        for index, dataset in enumerate(plan["required_datasets"], start=1):
            lines.extend(
                [
                    f"### 数据 {index}：{_markdown_text(dataset['name'])}",
                    "",
                    f"当前情况：{dataset_status_titles[dataset['acquisition_status']]}",
                    "",
                    f"用途：{_markdown_text(dataset['purpose'])}",
                    "",
                    f"所需变量：{_markdown_list(dataset['required_variables'])}",
                    "",
                    f"时间范围：{_markdown_text(dataset['time_coverage_needed'])}",
                    "",
                    f"时间分辨率：{_markdown_text(dataset['cadence_needed'])}",
                    "",
                    f"质量要求：{_markdown_list(dataset['quality_requirements'])}",
                    "",
                    f"版本要求：{_markdown_text(dataset['version_requirement'])}",
                    "",
                    f"单位要求：{_markdown_list(dataset['unit_requirements'])}",
                    "",
                    f"修订记录要求：{_markdown_list(dataset['revision_requirements'])}",
                    "",
                    f"许可要求：{_markdown_list(dataset['license_requirements'])}",
                    "",
                ]
            )

    lines.extend(["## 研究步骤", ""])
    for index, step in enumerate(plan["research_route"], start=1):
        input_names = [
            artifacts[artifact_id]["name"] for artifact_id in step["consumes_artifact_ids"]
        ]
        output_names = [
            artifacts[artifact_id]["name"] for artifact_id in step["produces_artifact_ids"]
        ]
        dataset_names = [
            datasets[dataset_id]["name"] for dataset_id in step["required_dataset_ids"]
        ]
        prior_steps = [
            f"第 {step_number[step_id]} 步" for step_id in step["prerequisite_step_ids"]
        ]
        lines.extend(
            [
                f"### 第 {index} 步：{_markdown_text(step['objective'])}",
                "",
                f"为什么需要这一步：{_markdown_text(step['necessity'])}",
                "",
                f"计划方法：{_markdown_text(step['method_outline'])}",
                "",
                f"本步骤需要的数据：{_markdown_list(dataset_names)}",
                "",
                f"输入：{_markdown_list(input_names)}",
                "",
                f"阶段成果：{_markdown_list(output_names)}",
            ]
        )
        if prior_steps:
            requirement = "全部满足" if step["join_policy"] == "all" else "至少一项满足"
            lines.extend(
                ["", f"前置工作：{_markdown_list(prior_steps)}（{requirement}）"]
            )
        if step["visit_limit"] > 1:
            lines.extend(["", f"必要时最多复核 {step['visit_limit']} 次。"])
        if step["capability_needs"]:
            lines.extend(["", "所需处理能力：", ""])
            for capability in step["capability_needs"]:
                lines.append(f"- {_markdown_text(capability['purpose'])}")
                lines.append(
                    f"  - 输入：{_markdown_list(capability['input_types'])}；"
                    f"输出：{_markdown_list(capability['output_types'])}"
                )
                if capability["constraints"]:
                    lines.append(
                        f"  - 限制：{_markdown_list(capability['constraints'])}"
                    )
        transition_by_outcome = {
            transition["on"]: transition for transition in step["transitions"]
        }
        lines.extend(["", "结果判断与后续安排：", ""])
        for rule in step["outcome_rules"]:
            transition = transition_by_outcome[rule["outcome"]]
            criteria = _markdown_list(rule["criteria"]).rstrip("。；;，,.!?！？")
            if "target_step_id" in transition:
                destination = f"转入第 {step_number[transition['target_step_id']]} 步"
            else:
                destination = terminal_titles[transition["terminal_status"]]
            lines.append(
                f"- **{outcome_titles[rule['outcome']]}：** "
                f"{criteria}；随后{destination}。"
            )
            if rule["evidence_required"]:
                lines.append(
                    f"  - 需要保留：{_markdown_list(rule['evidence_required'])}"
                )
        lines.append("")

    lines.extend(["## 判断标准", ""])
    if not plan["evaluation_rules"]:
        lines.extend(["本规划没有另设独立判断规则，按各研究步骤的完成条件判定。", ""])
    else:
        for index, rule in enumerate(plan["evaluation_rules"], start=1):
            basis = rule["criterion_basis"]
            basis_sources = [
                sources[source_id]["citation"]
                for source_id in basis["evidence_source_ids"]
            ]
            basis_artifacts = [
                artifacts[artifact_id]["name"] for artifact_id in basis["artifact_ids"]
            ]
            basis_detail = _markdown_text(basis["basis_text"])
            if basis_sources:
                basis_detail += f"；相关资料：{_markdown_list(basis_sources)}"
            if basis_artifacts:
                basis_detail += f"；相关阶段成果：{_markdown_list(basis_artifacts)}"
            lines.extend(
                [
                    f"### {index}. {_markdown_text(rule['name'])}",
                    "",
                    f"目的：{_markdown_text(rule['purpose'])}",
                    "",
                    f"检查方法：{_markdown_text(rule['check'])}",
                    "",
                    f"如何解释：{_markdown_text(rule['interpretation'])}",
                    "",
                    f"判断依据：{criterion_basis_titles[basis['kind']]}。{basis_detail}",
                    "",
                    f"不确定性处理：{_markdown_text(rule['uncertainty'])}",
                    "",
                ]
            )

    lines.extend(["## 预期报告结构", ""])
    for section in sorted(plan["report_outline"], key=lambda item: item["order"]):
        lines.append(
            f"{section['order']}. **{_markdown_text(section['title'])}**："
            f"{_markdown_text(section['purpose'])}"
        )
    lines.append("")

    lines.extend(["## 完成、暂停与补充条件", ""])
    for rule in plan["stop_rules"]:
        related_sections = [
            report_sections[section_id]["title"]
            for section_id in rule["report_section_ids"]
        ]
        lines.append(
            f"- **{terminal_titles[rule['terminal_status']]}：** "
            f"{_markdown_text(rule['condition'])}"
        )
        if rule["required_evidence"]:
            lines.append(
                f"  - 需要具备：{_markdown_list(rule['required_evidence'])}"
            )
        lines.append(f"  - 写入报告：{_markdown_list(related_sections)}")

    preparation: list[str] = []
    for dataset in plan["required_datasets"]:
        if dataset["acquisition_status"] != "selected":
            preparation.append(
                f"确认或取得“{_markdown_text(dataset['name'])}”，并核对版本、单位、修订记录和使用许可。"
            )
    for item in plan["research_state_map"]["items"]:
        if item["blocking"] and item["status"] in {"unresolved", "unavailable"}:
            preparation.extend(_markdown_text(value) for value in item["resolution_requirements"])
    if preparation:
        lines.extend(["", "## 开始执行前优先准备", ""])
        lines.extend(f"- {item}" for item in dict.fromkeys(preparation))

    return "\n".join(lines).rstrip() + "\n"


def freeze_research_plan(
    request_payload: dict[str, Any],
    response_payload: dict[str, Any],
    runs_root: Path | None = None,
) -> dict[str, Any]:
    """Validate a plan-ready response, publish its machine and reader views, and roll back on failure."""

    request = validate_planner_request(request_payload)
    response = validate_planner_response(response_payload, request)
    _require_plan_ready(response)
    plan = compile_research_plan(request, response)
    root = Path(runs_root or RUNS_ROOT).resolve()
    root.mkdir(parents=True, exist_ok=True)
    run_id = _safe_run_segment(plan["plan_id"])
    target = (root / run_id).resolve()
    if target.parent != root:
        raise ContractError("generated run id escaped the run store")
    if target.exists():
        raise ContractError(f"planner run already exists: {run_id}")
    try:
        target.mkdir(parents=False, exist_ok=False)
        (target / "planner_request.json").write_text(
            json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (target / "research_plan.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        report_markdown = render_research_plan_markdown(plan)
        (target / "research_plan.md").write_text(report_markdown, encoding="utf-8")
        stored_request = json.loads(
            (target / "planner_request.json").read_text(encoding="utf-8")
        )
        stored_plan = json.loads(
            (target / "research_plan.json").read_text(encoding="utf-8")
        )
        validate_planner_request(stored_request)
        validate_research_plan(stored_plan)
        if canonical_json_sha256(stored_request) != stored_plan["request_sha256"]:
            raise ContractError("stored request hash does not match the frozen plan")
        stored_markdown = (target / "research_plan.md").read_text(encoding="utf-8")
        if stored_markdown != report_markdown:
            raise ContractError("stored Markdown plan does not match the rendered plan")
    except BaseException:
        if target.exists() and target.parent == root:
            shutil.rmtree(target, ignore_errors=True)
        raise
    try:
        relative = target.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        relative = str(target)
    markdown_path = f"{relative}/research_plan.md"
    user_display_markdown = (
        stored_markdown.rstrip()
        + "\n\n---\n\n"
        + f"完整 Markdown 文件：`{markdown_path}`\n"
    )
    return {
        "schema_version": "research-planner-outcome-v1",
        "status": "frozen_and_valid",
        "run_id": run_id,
        "planning_readiness": stored_plan["planning_readiness"],
        "request_path": f"{relative}/planner_request.json",
        "research_plan_path": f"{relative}/research_plan.json",
        "research_plan_markdown_path": markdown_path,
        "user_message": "研究规划书已经通过检查并生成。",
        "user_report_markdown": stored_markdown,
        "user_display_markdown": user_display_markdown,
        "request_sha256": stored_plan["request_sha256"],
        "plan_sha256": stored_plan["plan_sha256"],
        "max_iterations": request["max_iterations"],
        "hypothesis_review_enabled": request["hypothesis_review_enabled"],
        "self_correction_enabled": request["self_correction_enabled"],
        "required_dataset_count": len(stored_plan["required_datasets"]),
        "state_item_count": len(stored_plan["research_state_map"]["items"]),
        "subquestion_count": len(stored_plan["research_subquestions"]),
        "evidence_source_count": len(stored_plan["evidence_sources"]),
        "artifact_count": len(stored_plan["research_artifacts"]),
        "route_step_count": len(stored_plan["research_route"]),
        "evaluation_rule_count": len(stored_plan["evaluation_rules"]),
        "report_section_count": len(stored_plan["report_outline"]),
        "files_written": 3,
        "experiments_executed": 0,
        "agent_relationships_used": False,
    }


__all__ = [
    "PROJECT_ROOT",
    "RUNS_ROOT",
    "audit_harness",
    "build_natural_planner_request",
    "build_planning_brief",
    "collect_planner_route_semantic_errors",
    "collect_planner_scientific_semantic_errors",
    "collect_planner_response_shape_errors",
    "compile_research_plan",
    "freeze_research_plan",
    "preflight_planner_response",
    "render_nonplan_response_markdown",
    "render_research_plan_markdown",
]
