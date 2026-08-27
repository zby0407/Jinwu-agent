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
    error_code = getattr(exc, "error_code", None)
    if isinstance(error_code, str) and error_code:
        output["error_code"] = error_code
    run_id = getattr(exc, "run_id", None)
    if isinstance(run_id, str) and run_id:
        output["run_id"] = run_id
    return json.dumps(output, ensure_ascii=False, default=str)


def _host_research_scope(config: RunnableConfig) -> dict[str, Any] | None:
    workspace = workspace_root_from_config(config)
    review_state_path = workspace / "research_review" / "run_state.json"
    scope_path = workspace / "research_review" / "experiment_scope.json"
    if not review_state_path.is_file():
        return None
    run_state = json.loads(review_state_path.read_text(encoding="utf-8"))
    current_stage = run_state.get("current_stage")
    if not scope_path.is_file():
        if current_stage not in {"experiment_design", "experiment_result"}:
            return None
        raise service.ServiceError(
            "the Research Review experiment stage has no host-owned scope",
            error_code="RESEARCH_EXPERIMENT_SCOPE_MISSING",
        )
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    if not isinstance(scope, dict):
        raise service.ServiceError(
            "the host-owned research experiment scope must be a JSON object",
            error_code="RESEARCH_EXPERIMENT_SCOPE_INVALID",
        )
    if scope.get("task_id") != run_state.get("task_id"):
        raise service.ServiceError(
            "the host-owned research experiment scope belongs to another task",
            error_code="RESEARCH_EXPERIMENT_SCOPE_INVALID",
        )
    scope_stage = scope.get("stage")
    if scope_stage not in {"experiment_design", "experiment_result"}:
        raise service.ServiceError(
            "the host-owned research experiment scope has an invalid stage",
            error_code="RESEARCH_EXPERIMENT_SCOPE_INVALID",
        )
    stage_status = run_state.get("stage_status")
    pending_dispatch = (
        isinstance(stage_status, dict) and stage_status.get(scope_stage) == "pending"
    )
    if scope_stage != current_stage and not pending_dispatch:
        raise service.ServiceError(
            "the host-owned research experiment scope does not match the current stage",
            error_code="RESEARCH_EXPERIMENT_SCOPE_INVALID",
        )
    return scope


def _host_staged_input_refs(config: RunnableConfig) -> list[dict[str, Any]] | None:
    """Load the host-owned staged input contract for an integrated experiment."""

    sidecar_path = workspace_root_from_config(config) / "inputs" / "_staged.json"
    if not sidecar_path.is_file():
        return None
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    if not isinstance(sidecar, dict) or sidecar.get("schema_version") != (
        "automatic-experiment-input-refs-v1"
    ):
        raise ValueError("the host staged-input sidecar has an invalid schema")
    rows = sidecar.get("input_refs")
    if not isinstance(rows, list) or not rows:
        raise ValueError("the host staged-input sidecar has no input_refs")
    refs: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"host staged input_refs[{index - 1}] must be an object")
        path = _normalize_model_input_path(row.get("path"))
        if path == "inputs/_staged.json" or path in seen_paths:
            continue
        seen_paths.add(path)
        refs.append(
            {
                "id": str(row.get("id") or f"input_{index:02d}"),
                "path": path,
                "description": str(
                    row.get("description") or "Accepted Data-stage input."
                ),
                "required": bool(row.get("required", True)),
            }
        )
    if not refs:
        raise ValueError("the host staged-input sidecar has no usable input_refs")
    return refs


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


def _apply_host_analysis_protocol(
    request: dict[str, Any], research_scope: dict[str, Any] | None
) -> dict[str, Any]:
    """Preserve deterministic protocol requirements in the immutable request.

    A specialist may accurately summarize the broad analysis while omitting a
    required output metric from its bind text.  In integrated research runs the
    host-owned analysis protocol is authoritative, so enrich only the narrow
    protocol requirement before the request is frozen.
    """

    enriched = dict(request)
    if not isinstance(research_scope, dict):
        return enriched
    if research_scope.get("analysis_protocol") == "silso_cycle_morphology_v1":
        requirement = (
            "Host protocol requirement: report Pearson and Spearman two-sided "
            "p-values for all three relationships."
        )
        task = str(enriched.get("task") or "").strip()
        if requirement not in task:
            enriched["task"] = f"{task}\n\n{requirement}" if task else requirement
    return enriched


def _service_research_scope(research_scope: dict[str, Any]) -> dict[str, Any]:
    """Project the host sidecar onto the automatic-experiment scope schema."""

    fields = (
        "schema_version",
        "task_id",
        "stage",
        "accepted_upstream_refs",
        "revision_review_id",
        "design_validation_limit",
    )
    return {field: research_scope[field] for field in fields}


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
        research_scope = _host_research_scope(config)
        if (
            research_scope is not None
            and research_scope.get("stage") == "experiment_result"
        ):
            raise service.ServiceError(
                "experiment_result must resume the accepted run_id; binding a new run is forbidden",
                error_code="RESEARCH_EXPERIMENT_RESULT_REBIND_FORBIDDEN",
            )
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
        if research_scope is not None and research_scope.get("stage") == (
            "experiment_design"
        ):
            staged_refs = _host_staged_input_refs(config)
            if staged_refs is not None:
                request = payload.get("request")
                if not isinstance(request, dict):
                    request = default_request(str(payload["request_input"]))
                request["input_refs"] = staged_refs
                payload = {"request": request}
            request = payload.get("request")
            if not isinstance(request, dict):
                request = default_request(str(payload["request_input"]))
            payload = {
                "request": _apply_host_analysis_protocol(request, research_scope)
            }
        with task_workspace(workspace_root_from_config(config)):
            return _ok(
                service.bind_request(
                    payload,
                    research_scope=(
                        _service_research_scope(research_scope)
                        if research_scope is not None
                        else None
                    ),
                )
            )
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
        JSON string with per-input metadata and snapshot status. After design
        validation, a repeated inspection also returns the current stage's exact
        required_worker_outputs contract for attempt authoring.
    """
    try:
        with task_workspace(workspace_root_from_config(config)):
            return _ok(service.inspect_inputs(run_id))
    except Exception as exc:
        return _err(exc)


_SILSO_MORPHOLOGY_ARTIFACT = "cycle_morphology_independent_check.json"
_SILSO_MORPHOLOGY_ESTIMAND = (
    "第 1—24 个完整太阳活动周中上升时间与峰值强度的 Pearson 相关系数"
)


def _silso_morphology_input_ids(request: dict[str, Any]) -> dict[str, str]:
    """Resolve the protocol inputs from immutable request paths."""

    matched: dict[str, str] = {}
    for row in request.get("input_refs", []):
        if not isinstance(row, dict):
            continue
        input_id = str(row.get("id") or "")
        name = Path(str(row.get("path") or "")).name.casefold()
        if "tablecyclesmima" in name:
            matched["extrema"] = input_id
        elif "sn_ms_tot_v2.0" in name:
            matched["smoothed"] = input_id
        elif "sn_m_tot_v2.0" in name:
            matched["monthly"] = input_id
        elif name.endswith("cycle_morphology_table.csv"):
            matched["table"] = input_id
    missing = [
        label
        for label in ("extrema", "smoothed", "monthly", "table")
        if not matched.get(label)
    ]
    if missing:
        raise ValueError(
            "silso_cycle_morphology_v1 requires staged extrema, smoothed, "
            "monthly-total, and morphology-table inputs; missing: "
            + ", ".join(missing)
        )
    return matched


def _silso_morphology_measurements() -> list[dict[str, str]]:
    labels = {
        "cycle_length": "周期总长度",
        "rise_time": "上升时间",
        "decline_time": "下降时间",
    }
    metrics = (
        ("pearson_r", "Pearson 相关系数", "相关系数"),
        ("pearson_p", "Pearson 双侧 p 值", "双侧检验的 p 值"),
        ("spearman_rho", "Spearman 等级相关系数", "等级相关系数"),
        ("spearman_p", "Spearman 双侧 p 值", "双侧等级检验的 p 值"),
        ("pearson_ci_low", "Pearson bootstrap 区间下限", "百分位区间下限"),
        ("pearson_ci_high", "Pearson bootstrap 区间上限", "百分位区间上限"),
        ("spearman_ci_low", "Spearman bootstrap 区间下限", "百分位区间下限"),
        ("spearman_ci_high", "Spearman bootstrap 区间上限", "百分位区间上限"),
    )
    rows: list[dict[str, str]] = []
    for relation, display in labels.items():
        for metric, metric_display, meaning in metrics:
            rows.append(
                {
                    "name": f"{relation}_{metric}",
                    "display_name": f"{display}与峰值强度的{metric_display}",
                    "role": (
                        "primary"
                        if relation == "rise_time" and metric == "pearson_r"
                        else "secondary"
                    ),
                    "unit": "",
                    "scientific_meaning": (
                        f"以完整活动周为独立样本，描述{display}与官方最大月"
                        f"平滑太阳黑子数关系的{meaning}。"
                    ),
                }
            )
    return rows


def _silso_morphology_results() -> list[dict[str, str]]:
    return [
        {
            "id": "complete_cycle_count",
            "display_name": "完整活动周样本数",
            "value_kind": "count",
            "role": "diagnostic",
            "unit": "个活动周",
            "scientific_meaning": "实际进入完整周期统计的独立太阳活动周数量。",
        },
        {
            "id": "bootstrap_requested_repetitions",
            "display_name": "请求的 bootstrap 重复次数",
            "value_kind": "count",
            "role": "diagnostic",
            "unit": "次",
            "scientific_meaning": "每一组相关关系预先固定的完整活动周重采样次数。",
        },
        {
            "id": "bootstrap_effective_repetitions_minimum",
            "display_name": "最少有效 bootstrap 重复次数",
            "value_kind": "count",
            "role": "diagnostic",
            "unit": "次",
            "scientific_meaning": "全部全样本与分时期分析中实际有效重复次数的最小值。",
        },
        {
            "id": "table_matches_registered_sources",
            "display_name": "逐周期表与注册来源一致",
            "value_kind": "boolean",
            "role": "diagnostic",
            "unit": "",
            "scientific_meaning": "逐周期日期、时间长度、分组和峰值均由注册来源独立重建并一致。",
        },
        {
            "id": "leave_one_cycle_out_complete",
            "display_name": "逐周期留一分析完成",
            "value_kind": "boolean",
            "role": "diagnostic",
            "unit": "",
            "scientific_meaning": "三组关系均完成 24 次以完整活动周为单位的留一复算。",
        },
        {
            "id": "subgroup_analysis_complete",
            "display_name": "固定分时期分析完成",
            "value_kind": "boolean",
            "role": "diagnostic",
            "unit": "",
            "scientific_meaning": "第 1—12 周与第 13—24 周均完成同口径相关和 bootstrap 分析。",
        },
        {
            "id": "rise_leave_one_direction_stable",
            "display_name": "上升时间关系留一方向一致",
            "value_kind": "boolean",
            "role": "secondary",
            "unit": "",
            "scientific_meaning": "删除任一活动周后 Pearson 与 Spearman 点估计均保持负向。",
        },
        {
            "id": "hypothesis_relation",
            "display_name": "Waldmeier 方向假设关系",
            "value_kind": "category",
            "role": "primary",
            "unit": "",
            "scientific_meaning": "依据预先固定的方向、区间、留一和分时期条件形成的关系类别。",
        },
        {
            "id": "cycle_length_evidence",
            "display_name": "周期长度关系证据类别",
            "value_kind": "category",
            "role": "secondary",
            "unit": "",
            "scientific_meaning": "周期总长度与峰值强度关系的区间证据类别。",
        },
        {
            "id": "decline_time_evidence",
            "display_name": "下降时间关系证据类别",
            "value_kind": "category",
            "role": "secondary",
            "unit": "",
            "scientific_meaning": "下降时间关系在相关量与固定分时期比较中的一致性类别。",
        },
    ]


def _create_silso_cycle_morphology_design(run_id: str) -> dict[str, Any]:
    """Build the protocol-owned mechanics for one bounded morphology stage."""

    run_root, _state = service.load_state(run_id)
    request = json.loads((run_root / "request.json").read_text(encoding="utf-8"))
    _silso_morphology_input_ids(request)
    response = {
        "response_kind": "experiment_ready",
        "normalized_task": (
            "仅用已绑定的 SILSO v2.0 输入，对第 1—24 个完整太阳活动周的"
            "周期长度、上升时间、下降时间与峰值强度关系进行独立复核。"
        ),
        "design_summary": (
            "单阶段重建逐周期变量，并计算三组 Pearson、Spearman、双侧 p 值、"
            "固定种子 bootstrap 区间、逐周期留一及预先固定的分时期结果。"
        ),
        "clarifications": [],
        "blockers": [],
        "method_fit": "suitable",
    }
    analysis = {
        "design_summary": response["design_summary"],
        "primary_question": (
            "历史第 1—24 周中三种周期形态量与峰值强度的统计关系如何，"
            "上升时间的负相关是否在留一和固定分时期检查中保持方向一致？"
        ),
        "analysis_mode": "完整活动周级别的描述性相关与敏感性分析。",
        "claim_scope": (
            "结论只适用于 SILSO v2.0 已结束的第 1—24 周，不延伸为太阳"
            "发电机因果证明，也不分析或预测第 26 周。"
        ),
        "method_outline": (
            "从官方极小、极大和下一极小月重建时间量，从官方最大月读取"
            "13 个月平滑值；对三组关系报告 Pearson 与 Spearman 双侧检验，"
            "以完整活动周为单位按种子 20260826 重采样 10000 次，并完成"
            "24 次留一以及第 1—12 周和第 13—24 周的同口径分析。"
        ),
        "measurements": _silso_morphology_measurements(),
        "results": _silso_morphology_results(),
        "artifacts": [
            {
                "path": _SILSO_MORPHOLOGY_ARTIFACT,
                "kind": "json",
                "description": "独立重建检查、完整相关结果、bootstrap、留一和固定分时期结果。",
            }
        ],
        "dependencies": ["numpy", "scipy"],
        "primary_estimand": _SILSO_MORPHOLOGY_ESTIMAND,
        "criterion_statement": (
            "只有当上升时间的 Pearson 与 Spearman 全样本估计均为负、两种"
            "bootstrap 区间上限均低于零、所有逐周期留一估计均为负，且两个"
            "预先固定时期的两种点估计均为负时，才把 Waldmeier 方向假设"
            "记为支持；双侧 p 值完整报告但不单独决定结论。"
        ),
        "criterion_basis_kind": "method_standard",
        "criterion_basis_text": (
            "该规则联合方向、区间、个体周期影响与预先固定时期，防止用单一"
            "相关量或单个显著性结果替代稳定性判断。"
        ),
        "threats_to_validity": [
            "完整周期仅 24 个，分时期后每组 12 个。",
            "早期观测密度和测量质量低于较现代时期。",
            "相邻活动周可能存在序列依赖，普通活动周 bootstrap 未显式建模该依赖。",
        ],
        "literature_basis": "本实验不新增外部文献主张，只检验用户指定的统计关系。",
        "seed": 20260826,
        "null_rule": "区间跨零或分析口径不一致时保留证据不足，不把未显著写成无关系。",
        "uncertainty_rule": "分时期、相关量或留一结果不一致时降低结论强度并指出具体来源。",
        "partial_rule": "任一三组关系、bootstrap、留一或固定分时期分析缺失时只报告部分结果。",
    }
    return service.build_and_store_single_stage_design(run_id, response, analysis)


def ensure_host_silso_morphology_design(
    config: RunnableConfig = None,
) -> dict[str, Any]:
    """Materialize the protocol-owned morphology design at the host boundary.

    The specialized design is deterministic mechanical work.  An integrated
    run must not fail merely because a model returns prose before calling the
    three mechanical bind/inspect/design tools, so the host can complete those
    exact steps against the already-persisted research scope and staged inputs.
    """

    workspace = workspace_root_from_config(config)
    research_scope = _host_research_scope(config)
    if not isinstance(research_scope, dict) or research_scope.get(
        "analysis_protocol"
    ) != "silso_cycle_morphology_v1":
        raise service.ServiceError(
            "host morphology design requires the silso_cycle_morphology_v1 scope",
            error_code="RESEARCH_EXPERIMENT_SCOPE_INVALID",
        )
    if research_scope.get("stage") != "experiment_design":
        raise service.ServiceError(
            "host morphology design is only valid in experiment_design",
            error_code="RESEARCH_EXPERIMENT_SCOPE_INVALID",
        )
    staged_refs = _host_staged_input_refs(config)
    if not staged_refs:
        raise service.ServiceError(
            "host morphology design has no staged inputs",
            error_code="RESEARCH_EXPERIMENT_INPUTS_MISSING",
        )
    task_path = workspace / "task.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    question = str(task.get("research_question") or "").strip()
    if not question:
        raise service.ServiceError(
            "host morphology design has no bound research question",
            error_code="RESEARCH_EXPERIMENT_SCOPE_INVALID",
        )
    request = default_request(question)
    request["input_refs"] = staged_refs
    request = _apply_host_analysis_protocol(request, research_scope)
    with task_workspace(workspace):
        try:
            bound = service.bind_request(
                {"request": request},
                research_scope=_service_research_scope(research_scope),
            )
            run_id = str(bound["run_id"])
        except service.ServiceError as exc:
            if (
                getattr(exc, "error_code", None)
                != "RESEARCH_EXPERIMENT_SCOPE_ALREADY_BOUND"
                or not isinstance(getattr(exc, "run_id", None), str)
            ):
                raise
            run_id = str(exc.run_id)
        run_root, state = service.load_state(run_id)
        if state.get("phase") == "design_validated" and (
            run_root / "design.json"
        ).is_file():
            return {"status": "design_validated", "run_id": run_id}
        service.inspect_inputs(run_id)
        checked = _create_silso_cycle_morphology_design(run_id)
        if checked.get("status") != "design_validated":
            raise service.ServiceError(
                "host morphology design did not validate: "
                + json.dumps(checked, ensure_ascii=False)[:2000],
                error_code="RESEARCH_EXPERIMENT_DESIGN_INVALID",
                run_id=run_id,
            )
        return {**checked, "run_id": run_id}


def _silso_cycle_morphology_worker_source(input_ids: dict[str, str]) -> str:
    source = r'''import csv
import json
import math

import numpy as np
from scipy.stats import pearsonr, spearmanr

EXTREMA_INPUT_ID = "__EXTREMA_ID__"
SMOOTHED_INPUT_ID = "__SMOOTHED_ID__"
MONTHLY_INPUT_ID = "__MONTHLY_ID__"
TABLE_INPUT_ID = "__TABLE_ID__"
PRIMARY_ESTIMAND = "第 1—24 个完整太阳活动周中上升时间与峰值强度的 Pearson 相关系数"
BOOTSTRAP_SEED = 20260826
BOOTSTRAP_REPETITIONS = 10000


def _month_index(value):
    year, month = (int(part) for part in value.split("-"))
    return year * 12 + month


def _read_extrema(path):
    rows = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if not fields or not fields[0].isdigit() or len(fields) < 4:
            continue
        cycle = int(fields[0])
        row = {
            "minimum_date": f"{int(fields[1]):04d}-{int(fields[2]):02d}",
            "maximum_date": None,
        }
        if len(fields) >= 7:
            row["maximum_date"] = f"{int(fields[4]):04d}-{int(fields[5]):02d}"
        rows[cycle] = row
    return rows


def _read_smoothed(path):
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = [field.strip() for field in line.split(";")]
        if len(fields) < 4 or not fields[0].isdigit() or not fields[1].isdigit():
            continue
        value = float(fields[3])
        if value >= 0:
            values[f"{int(fields[0]):04d}-{int(fields[1]):02d}"] = value
    return values


def _build_rows(extrema, smoothed):
    rows = []
    for cycle in range(1, 25):
        current = extrema.get(cycle)
        next_cycle = extrema.get(cycle + 1)
        if current is None or next_cycle is None or current["maximum_date"] is None:
            raise ValueError(f"missing official boundary for cycle {cycle}")
        minimum = current["minimum_date"]
        maximum = current["maximum_date"]
        next_minimum = next_cycle["minimum_date"]
        if maximum not in smoothed:
            raise ValueError(f"missing smoothed value at official maximum {maximum}")
        rows.append(
            {
                "cycle_number": cycle,
                "minimum_date": minimum,
                "maximum_date": maximum,
                "next_minimum_date": next_minimum,
                "cycle_length_years": (_month_index(next_minimum) - _month_index(minimum)) / 12.0,
                "rise_time_years": (_month_index(maximum) - _month_index(minimum)) / 12.0,
                "decline_time_years": (_month_index(next_minimum) - _month_index(maximum)) / 12.0,
                "peak_smoothed_sunspot_number": float(smoothed[maximum]),
                "observation_period_group": "early" if cycle <= 12 else "modern",
            }
        )
    return rows


def _read_upstream_table(path):
    expected = [
        "cycle_number",
        "minimum_date",
        "maximum_date",
        "next_minimum_date",
        "cycle_length_years",
        "rise_time_years",
        "decline_time_years",
        "peak_smoothed_sunspot_number",
        "observation_period_group",
        "data_quality_note",
    ]
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if list(reader.fieldnames or []) != expected:
            raise ValueError("morphology table columns do not match the declared contract")
        return [dict(row) for row in reader]


def _assert_table_matches(official_rows, table_rows):
    if len(official_rows) != 24 or len(table_rows) != 24:
        raise ValueError("the complete-cycle table must contain exactly 24 rows")
    text_fields = (
        "minimum_date",
        "maximum_date",
        "next_minimum_date",
        "observation_period_group",
    )
    number_fields = (
        "cycle_length_years",
        "rise_time_years",
        "decline_time_years",
        "peak_smoothed_sunspot_number",
    )
    for official, supplied in zip(official_rows, table_rows, strict=True):
        if int(supplied["cycle_number"]) != official["cycle_number"]:
            raise ValueError("morphology table cycle numbers are not consecutive 1 through 24")
        for field in text_fields:
            if supplied[field] != official[field]:
                raise ValueError(f"morphology table disagrees with registered sources at {field}")
        for field in number_fields:
            if not math.isclose(float(supplied[field]), float(official[field]), rel_tol=0.0, abs_tol=1e-12):
                raise ValueError(f"morphology table disagrees with registered sources at {field}")


def _statistics(x, y):
    pearson = pearsonr(x, y)
    spearman = spearmanr(x, y)
    return {
        "n": int(len(x)),
        "pearson_r": float(pearson.statistic),
        "pearson_p": float(pearson.pvalue),
        "spearman_rho": float(spearman.statistic),
        "spearman_p": float(spearman.pvalue),
    }


def _bootstrap(x, y):
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    pearson_values = []
    spearman_values = []
    for _index in range(BOOTSTRAP_REPETITIONS):
        selected = rng.integers(0, len(x), size=len(x))
        if len(np.unique(selected)) < 2:
            continue
        sample_x = x[selected]
        sample_y = y[selected]
        if np.ptp(sample_x) == 0 or np.ptp(sample_y) == 0:
            continue
        pearson_value = float(pearsonr(sample_x, sample_y).statistic)
        spearman_value = float(spearmanr(sample_x, sample_y).statistic)
        if math.isfinite(pearson_value) and math.isfinite(spearman_value):
            pearson_values.append(pearson_value)
            spearman_values.append(spearman_value)
    if not pearson_values or not spearman_values:
        raise ValueError("bootstrap produced no finite complete-cycle resamples")
    return {
        "seed": BOOTSTRAP_SEED,
        "requested_repetitions": BOOTSTRAP_REPETITIONS,
        "effective_repetitions": len(pearson_values),
        "pearson_ci95": [
            float(np.quantile(pearson_values, 0.025)),
            float(np.quantile(pearson_values, 0.975)),
        ],
        "spearman_ci95": [
            float(np.quantile(spearman_values, 0.025)),
            float(np.quantile(spearman_values, 0.975)),
        ],
    }


def _relationship(rows, x_key):
    x = np.asarray([float(row[x_key]) for row in rows], dtype=float)
    y = np.asarray([float(row["peak_smoothed_sunspot_number"]) for row in rows], dtype=float)
    result = _statistics(x, y)
    result["bootstrap"] = _bootstrap(x, y)
    leave_one_out = []
    for index, row in enumerate(rows):
        item = _statistics(np.delete(x, index), np.delete(y, index))
        item["removed_cycle"] = int(row["cycle_number"])
        leave_one_out.append(item)
    result["leave_one_out"] = leave_one_out
    result["most_influential_pearson_cycle"] = max(
        leave_one_out,
        key=lambda item: abs(item["pearson_r"] - result["pearson_r"]),
    )["removed_cycle"]
    result["most_influential_spearman_cycle"] = max(
        leave_one_out,
        key=lambda item: abs(item["spearman_rho"] - result["spearman_rho"]),
    )["removed_cycle"]
    return result


def _measurement_values(relations):
    values = {}
    for relation_name, relation in relations.items():
        values[f"{relation_name}_pearson_r"] = relation["pearson_r"]
        values[f"{relation_name}_pearson_p"] = relation["pearson_p"]
        values[f"{relation_name}_spearman_rho"] = relation["spearman_rho"]
        values[f"{relation_name}_spearman_p"] = relation["spearman_p"]
        values[f"{relation_name}_pearson_ci_low"] = relation["bootstrap"]["pearson_ci95"][0]
        values[f"{relation_name}_pearson_ci_high"] = relation["bootstrap"]["pearson_ci95"][1]
        values[f"{relation_name}_spearman_ci_low"] = relation["bootstrap"]["spearman_ci95"][0]
        values[f"{relation_name}_spearman_ci_high"] = relation["bootstrap"]["spearman_ci95"][1]
    return values


def run_experiment(context):
    extrema_path = context["input_path_by_id"][EXTREMA_INPUT_ID]
    smoothed_path = context["input_path_by_id"][SMOOTHED_INPUT_ID]
    monthly_path = context["input_path_by_id"][MONTHLY_INPUT_ID]
    table_path = context["input_path_by_id"][TABLE_INPUT_ID]
    if not monthly_path.read_text(encoding="utf-8").strip():
        raise ValueError("registered monthly-total input is empty")
    official_rows = _build_rows(_read_extrema(extrema_path), _read_smoothed(smoothed_path))
    table_rows = _read_upstream_table(table_path)
    _assert_table_matches(official_rows, table_rows)
    relations = {
        "cycle_length": _relationship(official_rows, "cycle_length_years"),
        "rise_time": _relationship(official_rows, "rise_time_years"),
        "decline_time": _relationship(official_rows, "decline_time_years"),
    }
    subgroups = {}
    for group in ("early", "modern"):
        selected = [row for row in official_rows if row["observation_period_group"] == group]
        subgroups[group] = {
            "cycle_length": _relationship(selected, "cycle_length_years"),
            "rise_time": _relationship(selected, "rise_time_years"),
            "decline_time": _relationship(selected, "decline_time_years"),
        }
    values = _measurement_values(relations)
    rise = relations["rise_time"]
    rise_loo_negative = all(
        item["pearson_r"] < 0 and item["spearman_rho"] < 0
        for item in rise["leave_one_out"]
    )
    subgroup_rise_negative = all(
        subgroups[group]["rise_time"]["pearson_r"] < 0
        and subgroups[group]["rise_time"]["spearman_rho"] < 0
        for group in ("early", "modern")
    )
    rise_supported = (
        rise["pearson_r"] < 0
        and rise["spearman_rho"] < 0
        and rise["bootstrap"]["pearson_ci95"][1] < 0
        and rise["bootstrap"]["spearman_ci95"][1] < 0
        and rise_loo_negative
        and subgroup_rise_negative
    )
    rise_opposed = (
        rise["pearson_r"] > 0
        and rise["spearman_rho"] > 0
        and rise["bootstrap"]["pearson_ci95"][0] > 0
        and rise["bootstrap"]["spearman_ci95"][0] > 0
    )
    hypothesis_relation = "supports" if rise_supported else "opposes" if rise_opposed else "uncertain"
    length = relations["cycle_length"]
    length_same_side = (
        length["bootstrap"]["pearson_ci95"][0] > 0
        and length["bootstrap"]["spearman_ci95"][0] > 0
    ) or (
        length["bootstrap"]["pearson_ci95"][1] < 0
        and length["bootstrap"]["spearman_ci95"][1] < 0
    )
    length_evidence = "direction_supported" if length_same_side else "insufficient_for_stable_relation"
    decline = relations["decline_time"]
    decline_consistent = (
        decline["pearson_r"] * decline["spearman_rho"] > 0
        and subgroups["early"]["decline_time"]["pearson_r"]
        * subgroups["modern"]["decline_time"]["pearson_r"] > 0
        and subgroups["early"]["decline_time"]["spearman_rho"]
        * subgroups["modern"]["decline_time"]["spearman_rho"] > 0
    )
    decline_evidence = "direction_consistent" if decline_consistent else "period_or_metric_dependent"
    all_relationships = [
        relation
        for group_values in [relations, subgroups["early"], subgroups["modern"]]
        for relation in group_values.values()
    ]
    minimum_effective = min(
        relation["bootstrap"]["effective_repetitions"]
        for relation in all_relationships
    )
    result_values = {
        "complete_cycle_count": 24,
        "bootstrap_requested_repetitions": BOOTSTRAP_REPETITIONS,
        "bootstrap_effective_repetitions_minimum": int(minimum_effective),
        "table_matches_registered_sources": True,
        "leave_one_cycle_out_complete": all(len(item["leave_one_out"]) == 24 for item in relations.values()),
        "subgroup_analysis_complete": all(
            item["n"] == 12
            for group in ("early", "modern")
            for item in subgroups[group].values()
        ),
        "rise_leave_one_direction_stable": rise_loo_negative,
        "hypothesis_relation": hypothesis_relation,
        "cycle_length_evidence": length_evidence,
        "decline_time_evidence": decline_evidence,
    }
    artifact_payload = {
        "schema_version": "silso-cycle-morphology-independent-check-v1",
        "bootstrap_seed": BOOTSTRAP_SEED,
        "bootstrap_requested_repetitions": BOOTSTRAP_REPETITIONS,
        "registered_source_crosscheck": {
            "complete_cycles": 24,
            "cycle_25_used_only_as_next_minimum_boundary": True,
            "table_matches_registered_sources": True,
        },
        "measurements": values,
        "results": result_values,
        "relations": relations,
        "subgroups": subgroups,
    }
    artifact = context["output_dir"] / ARTIFACT_PATH
    artifact.write_text(json.dumps(artifact_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "schema_version": "automatic-experiment-worker-result-v1",
        "execution_completed": True,
        "measurements": [
            {"name": "cycle_length_pearson_r", "value": values["cycle_length_pearson_r"], "unit": "", "role": "secondary", "source_artifact": ARTIFACT_PATH},
            {"name": "cycle_length_pearson_p", "value": values["cycle_length_pearson_p"], "unit": "", "role": "secondary", "source_artifact": ARTIFACT_PATH},
            {"name": "cycle_length_spearman_rho", "value": values["cycle_length_spearman_rho"], "unit": "", "role": "secondary", "source_artifact": ARTIFACT_PATH},
            {"name": "cycle_length_spearman_p", "value": values["cycle_length_spearman_p"], "unit": "", "role": "secondary", "source_artifact": ARTIFACT_PATH},
            {"name": "cycle_length_pearson_ci_low", "value": values["cycle_length_pearson_ci_low"], "unit": "", "role": "secondary", "source_artifact": ARTIFACT_PATH},
            {"name": "cycle_length_pearson_ci_high", "value": values["cycle_length_pearson_ci_high"], "unit": "", "role": "secondary", "source_artifact": ARTIFACT_PATH},
            {"name": "cycle_length_spearman_ci_low", "value": values["cycle_length_spearman_ci_low"], "unit": "", "role": "secondary", "source_artifact": ARTIFACT_PATH},
            {"name": "cycle_length_spearman_ci_high", "value": values["cycle_length_spearman_ci_high"], "unit": "", "role": "secondary", "source_artifact": ARTIFACT_PATH},
            {"name": "rise_time_pearson_r", "value": values["rise_time_pearson_r"], "unit": "", "role": "primary", "source_artifact": ARTIFACT_PATH},
            {"name": "rise_time_pearson_p", "value": values["rise_time_pearson_p"], "unit": "", "role": "secondary", "source_artifact": ARTIFACT_PATH},
            {"name": "rise_time_spearman_rho", "value": values["rise_time_spearman_rho"], "unit": "", "role": "secondary", "source_artifact": ARTIFACT_PATH},
            {"name": "rise_time_spearman_p", "value": values["rise_time_spearman_p"], "unit": "", "role": "secondary", "source_artifact": ARTIFACT_PATH},
            {"name": "rise_time_pearson_ci_low", "value": values["rise_time_pearson_ci_low"], "unit": "", "role": "secondary", "source_artifact": ARTIFACT_PATH},
            {"name": "rise_time_pearson_ci_high", "value": values["rise_time_pearson_ci_high"], "unit": "", "role": "secondary", "source_artifact": ARTIFACT_PATH},
            {"name": "rise_time_spearman_ci_low", "value": values["rise_time_spearman_ci_low"], "unit": "", "role": "secondary", "source_artifact": ARTIFACT_PATH},
            {"name": "rise_time_spearman_ci_high", "value": values["rise_time_spearman_ci_high"], "unit": "", "role": "secondary", "source_artifact": ARTIFACT_PATH},
            {"name": "decline_time_pearson_r", "value": values["decline_time_pearson_r"], "unit": "", "role": "secondary", "source_artifact": ARTIFACT_PATH},
            {"name": "decline_time_pearson_p", "value": values["decline_time_pearson_p"], "unit": "", "role": "secondary", "source_artifact": ARTIFACT_PATH},
            {"name": "decline_time_spearman_rho", "value": values["decline_time_spearman_rho"], "unit": "", "role": "secondary", "source_artifact": ARTIFACT_PATH},
            {"name": "decline_time_spearman_p", "value": values["decline_time_spearman_p"], "unit": "", "role": "secondary", "source_artifact": ARTIFACT_PATH},
            {"name": "decline_time_pearson_ci_low", "value": values["decline_time_pearson_ci_low"], "unit": "", "role": "secondary", "source_artifact": ARTIFACT_PATH},
            {"name": "decline_time_pearson_ci_high", "value": values["decline_time_pearson_ci_high"], "unit": "", "role": "secondary", "source_artifact": ARTIFACT_PATH},
            {"name": "decline_time_spearman_ci_low", "value": values["decline_time_spearman_ci_low"], "unit": "", "role": "secondary", "source_artifact": ARTIFACT_PATH},
            {"name": "decline_time_spearman_ci_high", "value": values["decline_time_spearman_ci_high"], "unit": "", "role": "secondary", "source_artifact": ARTIFACT_PATH},
        ],
        "result_items": [
            {"id": "complete_cycle_count", "display_name": "完整活动周样本数", "value_kind": "count", "value": result_values["complete_cycle_count"], "unit": "个活动周", "role": "diagnostic", "source_artifact": ARTIFACT_PATH},
            {"id": "bootstrap_requested_repetitions", "display_name": "请求的 bootstrap 重复次数", "value_kind": "count", "value": result_values["bootstrap_requested_repetitions"], "unit": "次", "role": "diagnostic", "source_artifact": ARTIFACT_PATH},
            {"id": "bootstrap_effective_repetitions_minimum", "display_name": "最少有效 bootstrap 重复次数", "value_kind": "count", "value": result_values["bootstrap_effective_repetitions_minimum"], "unit": "次", "role": "diagnostic", "source_artifact": ARTIFACT_PATH},
            {"id": "table_matches_registered_sources", "display_name": "逐周期表与注册来源一致", "value_kind": "boolean", "value": result_values["table_matches_registered_sources"], "unit": "", "role": "diagnostic", "source_artifact": ARTIFACT_PATH},
            {"id": "leave_one_cycle_out_complete", "display_name": "逐周期留一分析完成", "value_kind": "boolean", "value": result_values["leave_one_cycle_out_complete"], "unit": "", "role": "diagnostic", "source_artifact": ARTIFACT_PATH},
            {"id": "subgroup_analysis_complete", "display_name": "固定分时期分析完成", "value_kind": "boolean", "value": result_values["subgroup_analysis_complete"], "unit": "", "role": "diagnostic", "source_artifact": ARTIFACT_PATH},
            {"id": "rise_leave_one_direction_stable", "display_name": "上升时间关系留一方向一致", "value_kind": "boolean", "value": result_values["rise_leave_one_direction_stable"], "unit": "", "role": "secondary", "source_artifact": ARTIFACT_PATH},
            {"id": "hypothesis_relation", "display_name": "Waldmeier 方向假设关系", "value_kind": "category", "value": result_values["hypothesis_relation"], "unit": "", "role": "primary", "source_artifact": ARTIFACT_PATH},
            {"id": "cycle_length_evidence", "display_name": "周期长度关系证据类别", "value_kind": "category", "value": result_values["cycle_length_evidence"], "unit": "", "role": "secondary", "source_artifact": ARTIFACT_PATH},
            {"id": "decline_time_evidence", "display_name": "下降时间关系证据类别", "value_kind": "category", "value": result_values["decline_time_evidence"], "unit": "", "role": "secondary", "source_artifact": ARTIFACT_PATH},
        ],
        "artifacts": [
            {"path": ARTIFACT_PATH, "kind": "json", "description": "独立重建与完整敏感性分析结果。"}
        ],
        "warnings": [],
        "endpoint_results": [
            {"id": "analysis_endpoint", "status": "completed", "summary": "三组关系、bootstrap、留一和固定分时期分析均已完成。"}
        ],
        "scientific_payload": {
            "primary_estimand": PRIMARY_ESTIMAND,
            "estimate": values["rise_time_pearson_r"],
            "interval": [values["rise_time_pearson_ci_low"], values["rise_time_pearson_ci_high"]],
            "equivalence_bounds": None,
            "sensitivity": "逐周期留一后 Pearson 与 Spearman 的方向一致性，以及两个预先固定时期的方向一致性。",
            "uncertainty_reasons": [
                "完整周期样本量为 24，分时期后每组为 12。",
                "早期观测密度和测量质量低于较现代时期。",
                "普通活动周 bootstrap 未显式建模相邻周期的序列依赖。",
            ],
        },
    }
'''
    replacements = {
        "__EXTREMA_ID__": input_ids["extrema"],
        "__SMOOTHED_ID__": input_ids["smoothed"],
        "__MONTHLY_ID__": input_ids["monthly"],
        "__TABLE_ID__": input_ids["table"],
    }
    for marker, value in replacements.items():
        source = source.replace(marker, value)
    source = source.replace(
        "ARTIFACT_PATH", json.dumps(_SILSO_MORPHOLOGY_ARTIFACT)
    )
    return source


def _prepare_silso_cycle_morphology_attempt(run_id: str) -> dict[str, Any]:
    """Prepare the validated protocol worker without model-authored boilerplate."""

    run_root, _state = service.load_state(run_id)
    request = json.loads((run_root / "request.json").read_text(encoding="utf-8"))
    design = json.loads((run_root / "design.json").read_text(encoding="utf-8"))
    stage = service.experiment_stage(design)
    if stage.get("execution", {}).get("seed") != 20260826:
        raise ValueError("the validated morphology design does not use seed 20260826")
    if stage.get("execution", {}).get("expected_artifacts") != [
        _SILSO_MORPHOLOGY_ARTIFACT
    ]:
        raise ValueError("the validated morphology design has unexpected artifacts")
    source = _silso_cycle_morphology_worker_source(
        _silso_morphology_input_ids(request)
    )
    return service.prepare(
        run_id,
        [{"path": "experiment.py", "content": source}],
        None,
        "使用协议专用 worker 独立重建 SILSO 周期表并完成预先声明的统计复核。",
    )


@tool(parse_docstring=True)
def automatic_experiment_create_single_stage_design(
    run_id: str,
    response: dict[str, Any] | str,
    analysis: dict[str, Any] | str,
    config: RunnableConfig = None,
) -> str:
    """Create and validate one experiment stage from a compact analysis plan.

    Prefer this tool when one computational stage is sufficient. The model
    supplies the scientific choices while the host fills stable lifecycle
    fields. ``analysis`` contains: design_summary, primary_question,
    analysis_mode, claim_scope, method_outline, measurements, results,
    artifacts, dependencies, primary_estimand, and threats_to_validity.
    Measurements use name/display_name/role/unit/scientific_meaning. Results
    use id/display_name/value_kind/role/unit/scientific_meaning; answer-bearing
    numbers belong in measurements, while results hold category, boolean, text,
    or diagnostic values. Artifacts use path/kind/description. Optional
    ``criteria`` items use id/statement/basis_kind/basis_text/source_refs/
    artifact_refs/measurement_refs/result_refs/endpoint_refs. Criterion
    basis_kind is user_request, located_source, data_derived, method_standard,
    bounded_pragmatic_choice, or qualitative_no_fixed_threshold; use the
    bounded choice for an explicitly arbitrary convention fixed before seeing
    results. Optional ``method_decisions`` items use id/decision_key/decision/
    rationale/basis_kind/source_refs/alternatives/claim_limit. Other optional
    fields are input_evidence, supported_questions, deferred_questions,
    assumptions, literature_basis, paired_comparison_audits, seed, and the
    interpretation rules. Every criterion must cite an actual measurement,
    result, or analysis_endpoint. This tool performs the same full design
    validation as the expanded endpoint and returns all visible issues together.

    Args:
        run_id: The run identifier returned by bind_request.
        response: The automatic-experiment response object.
        analysis: Compact scientific analysis plan for one execution stage.

    Returns:
        JSON string with validated design status or the complete issue list.
    """
    try:
        parsed_response = _parse_json_arg(response, "response")
        parsed_analysis = _parse_json_arg(analysis, "analysis")
        if not isinstance(parsed_response, dict):
            raise ValueError("response must be a JSON object")
        if not isinstance(parsed_analysis, dict):
            raise ValueError("analysis must be a JSON object")
        with task_workspace(workspace_root_from_config(config)):
            return _ok(
                service.build_and_store_single_stage_design(
                    run_id, parsed_response, parsed_analysis
                )
            )
    except Exception as exc:
        return _err(exc)


@tool(parse_docstring=True)
def automatic_experiment_create_silso_morphology_design(
    run_id: str, config: RunnableConfig = None
) -> str:
    """Create the pre-registered one-stage SILSO morphology design.

    Use only for ``silso_cycle_morphology_v1`` after binding and inspecting
    the staged SILSO extrema, smoothed, monthly-total, and morphology-table
    inputs. The host supplies the mechanical design envelope and the exact
    user-requested statistics, avoiding free-form schema retries.

    Args:
        run_id: The inspected run identifier returned by bind_request.

    Returns:
        JSON string with the validated design status and worker contract.
    """
    try:
        with task_workspace(workspace_root_from_config(config)):
            return _ok(_create_silso_cycle_morphology_design(run_id))
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
def automatic_experiment_prepare_silso_morphology_attempt(
    run_id: str, config: RunnableConfig = None
) -> str:
    """Prepare the validated SILSO morphology worker exactly once.

    Use only in the experiment-result phase for a run whose specialized SILSO
    morphology design was accepted. The prepared worker independently rebuilds
    cycles 1--24 from the bound official sources, checks the upstream table,
    and computes all registered correlation, bootstrap, leave-one-out, and
    fixed-subgroup results.

    Args:
        run_id: The accepted morphology run identifier.

    Returns:
        JSON string with the prepared immutable attempt identifier.
    """
    try:
        with task_workspace(workspace_root_from_config(config)):
            return _ok(_prepare_silso_cycle_morphology_attempt(run_id))
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
    automatic_experiment_create_single_stage_design,
    automatic_experiment_create_silso_morphology_design,
    automatic_experiment_validate_design,
    automatic_experiment_prepare_silso_morphology_attempt,
    automatic_experiment_prepare_attempt,
    automatic_experiment_execute_attempt,
    automatic_experiment_verify_result,
    automatic_experiment_finalize,
]

register_tool_bundle("automatic-experiment", AUTOMATIC_EXPERIMENT_TOOLS)

__all__ = ["AUTOMATIC_EXPERIMENT_TOOLS"] + [t.name for t in AUTOMATIC_EXPERIMENT_TOOLS]
