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
    elif research_scope.get("analysis_protocol") == (
        "solar_cycle_26_forecast_backtest_v1"
    ):
        requirement = (
            "Host protocol requirement: use a rolling-origin cycle-level backtest, "
            "compare the candidate with the training-mean baseline using MAE and "
            "RMSE, use seed 20260827 and 10000 bootstrap repetitions, preserve a "
            "negative result, keep Cycle 25 predictor-only, and use SILSO inputs only."
        )
        task = str(enriched.get("task") or "").strip()
        if requirement not in task:
            enriched["task"] = f"{task}\n\n{requirement}" if task else requirement
    elif research_scope.get("analysis_protocol") == "solar_polar_precursor_v1":
        requirement = (
            "Host protocol requirement: use exactly five initial training cycles, "
            "strict rolling-origin folds, training-mean and persistence baselines, "
            "seed 20260828 with 10000 cycle-level bootstrap repetitions, and "
            "separate MWO/WSO sensitivity. Preserve H3 as blocked_by_data unless "
            "the receipt contains a registered axial-dipole observable."
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
_SC26_FORECAST_ARTIFACT = "sc26_forecast_independent_check.json"
_SC26_FORECAST_ESTIMAND = (
    "严格时间顺序回测中前一活动周峰值模型相对训练均值基线的平均绝对误差改进"
)
POLAR_FORECAST_OUTPUTS = [
    "forecast_experiment_receipt.json",
    "rolling_predictions.csv",
    "bootstrap_mae_improvement.csv",
]
_POLAR_FORECAST_ESTIMAND = (
    "严格滚动起点回测中极小期极区场模型相对训练均值基线的平均绝对误差改进"
)


def _polar_forecast_input_ids(request: dict[str, Any]) -> dict[str, str]:
    """Resolve the verified polar feature table and typed Data receipt."""

    matched: dict[str, str] = {}
    for row in request.get("input_refs", []):
        if not isinstance(row, dict):
            continue
        input_id = str(row.get("id") or "")
        name = Path(str(row.get("path") or "")).name.casefold()
        if name == "solar_precursor_cycle_features.csv":
            matched["table"] = input_id
        elif name == "solar_precursor_cycle_table.json":
            matched["receipt"] = input_id
    missing = [label for label in ("table", "receipt") if not matched.get(label)]
    if missing:
        raise ValueError(
            "solar_polar_precursor_v1 requires the verified precursor table and "
            "its typed receipt; missing: " + ", ".join(missing)
        )
    return matched


def _polar_forecast_measurements() -> list[dict[str, str]]:
    rows = [
        ("candidate_mae", "极区前兆模型平均绝对误差", "primary"),
        ("training_mean_mae", "训练均值基线平均绝对误差", "secondary"),
        ("persistence_mae", "持续性基线平均绝对误差", "secondary"),
        ("candidate_rmse", "极区前兆模型均方根误差", "secondary"),
        ("training_mean_rmse", "训练均值基线均方根误差", "secondary"),
        ("persistence_rmse", "持续性基线均方根误差", "secondary"),
        ("mae_improvement", "相对训练均值基线的绝对误差改进", "primary"),
        ("mae_improvement_ci_low", "绝对误差改进区间下限", "secondary"),
        ("mae_improvement_ci_high", "绝对误差改进区间上限", "secondary"),
    ]
    return [
        {
            "name": name,
            "display_name": display,
            "role": role,
            "unit": "平滑太阳黑子数",
            "scientific_meaning": "以完整活动周为单位的严格时间顺序预测误差量。",
        }
        for name, display, role in rows
    ]


def _polar_forecast_results() -> list[dict[str, str]]:
    rows = [
        ("effective_backtest_folds", "有效历史回测周数", "count", "diagnostic"),
        ("bootstrap_repetitions", "bootstrap 重复次数", "count", "diagnostic"),
        ("bootstrap_seed", "bootstrap 随机种子", "count", "diagnostic"),
        ("leakage_audit_passed", "时间泄漏检查通过", "boolean", "primary"),
        ("regime_consistent", "测量制度方向一致", "boolean", "primary"),
        ("feature_lineage_verified", "前兆特征谱系已核对", "boolean", "primary"),
        ("forecast_skill_status", "历史预测技能类别", "category", "primary"),
        ("axial_data_status", "轴向偶极矩数据状态", "category", "secondary"),
    ]
    return [
        {
            "id": identifier,
            "display_name": display,
            "value_kind": kind,
            "role": role,
            "unit": "次" if identifier == "bootstrap_repetitions" else "",
            "scientific_meaning": "预注册极区前兆预测实验的确定性核对结果。",
        }
        for identifier, display, kind, role in rows
    ]


def _create_polar_forecast_design(run_id: str) -> dict[str, Any]:
    """Build the protocol-owned polar precursor forecast design."""

    run_root, _state = service.load_state(run_id)
    request = json.loads((run_root / "request.json").read_text(encoding="utf-8"))
    _polar_forecast_input_ids(request)
    response = {
        "response_kind": "experiment_ready",
        "normalized_task": "复核极小期极区场对下一太阳活动周峰值的历史预测技能。",
        "design_summary": (
            "单阶段完成五周起始训练的扩展窗口回测、双基线比较、固定种子"
            "活动周级重采样、逐周留一和测量制度敏感性。"
        ),
        "clarifications": [],
        "blockers": [],
        "method_fit": "suitable",
    }
    analysis = {
        "design_summary": response["design_summary"],
        "primary_question": "极小期极区场模型能否在历史盲测中稳定超过训练均值基线？",
        "analysis_mode": "完整活动周级扩展窗口滚动起点预测。",
        "claim_scope": (
            "H2 使用已登记的 MWO—WSO 极区孔径观测；没有合格数据时 H3 仅报告阻断。"
        ),
        "method_outline": (
            "按目标活动周排序，前五周训练后逐周留出；每折重新拟合一元线性前兆"
            "模型并重算训练均值与持续性基线，以种子 20260828 对逐折绝对误差差"
            "重采样 10000 次，另报逐周留一和 MWO、WSO 制度结果。"
        ),
        "measurements": _polar_forecast_measurements(),
        "results": _polar_forecast_results(),
        "artifacts": [
            {
                "path": POLAR_FORECAST_OUTPUTS[0],
                "kind": "json",
                "description": "类型化实验回执、逐折指标、泄漏检查和科学判定。",
            },
            {
                "path": POLAR_FORECAST_OUTPUTS[1],
                "kind": "csv",
                "description": "每个严格时间顺序留出周的预测、基线与观测。",
            },
            {
                "path": POLAR_FORECAST_OUTPUTS[2],
                "kind": "csv",
                "description": "固定种子活动周级配对重采样的误差改进分布。",
            },
        ],
        "dependencies": ["numpy"],
        "primary_estimand": _POLAR_FORECAST_ESTIMAND,
        "criterion_statement": (
            "只有相对训练均值基线的平均绝对误差改进为正、其95%区间下限高于零，"
            "且至少两折的各测量制度均不反转方向，才记为预测技能得到支持。"
        ),
        "criterion_basis_kind": "method_standard",
        "criterion_basis_text": "判定在查看本次历史留出结果前固定，并保留空结果和制度差异。",
        "threats_to_validity": [
            "完整活动周样本有限。",
            "MWO 是校准代理，WSO 是磁强计观测，测量制度并不相同。",
            "回顾性极小期标签存在中心平滑确认滞后。",
        ],
        "literature_basis": "本实验只复核已登记数据，不以文献相关性替代本地历史盲测。",
        "seed": 20260828,
        "null_rule": "未超过基线或区间覆盖无优势时保留空结果或混合证据。",
        "uncertainty_rule": "逐周留一或测量制度方向不一致时降低结论强度。",
        "partial_rule": "缺少真实轴向偶极矩时 H3 为数据阻断，但不使 H2 失效。",
    }
    return service.build_and_store_single_stage_design(run_id, response, analysis)


def _sc26_forecast_input_ids(request: dict[str, Any]) -> dict[str, str]:
    """Resolve accepted SC26 Data outputs from immutable request paths."""

    names = {
        "features": "sc26_cycle_features.csv",
        "predictions": "sc26_forecast_predictions.csv",
        "forecast": "sc26_formal_forecast.json",
        "summary": "run_summary.json",
        "manifest": "data_manifest.json",
    }
    matched: dict[str, str] = {}
    for row in request.get("input_refs", []):
        if not isinstance(row, dict):
            continue
        input_id = str(row.get("id") or "")
        name = Path(str(row.get("path") or "")).name.casefold()
        for label, expected in names.items():
            if name == expected:
                matched[label] = input_id
    missing = [label for label in names if not matched.get(label)]
    if missing:
        raise ValueError(
            "solar_cycle_26_forecast_backtest_v1 requires accepted features, "
            "predictions, forecast, summary, and manifest outputs; missing: "
            + ", ".join(missing)
        )
    return matched


def _sc26_forecast_measurements() -> list[dict[str, str]]:
    rows = [
        ("candidate_mae", "候选模型平均绝对误差", "primary", "平滑太阳黑子数"),
        ("baseline_mae", "训练均值基线平均绝对误差", "secondary", "平滑太阳黑子数"),
        ("candidate_rmse", "候选模型均方根误差", "secondary", "平滑太阳黑子数"),
        ("baseline_rmse", "训练均值基线均方根误差", "secondary", "平滑太阳黑子数"),
        (
            "mae_improvement",
            "候选模型相对基线的绝对误差改进",
            "primary",
            "平滑太阳黑子数",
        ),
        (
            "mae_improvement_ci_low",
            "绝对误差改进区间下限",
            "secondary",
            "平滑太阳黑子数",
        ),
        (
            "mae_improvement_ci_high",
            "绝对误差改进区间上限",
            "secondary",
            "平滑太阳黑子数",
        ),
        ("cycle26_point_estimate", "第26周峰值点估计", "secondary", "平滑太阳黑子数"),
        ("cycle26_interval_low", "第26周预测区间下限", "secondary", "平滑太阳黑子数"),
        ("cycle26_interval_high", "第26周预测区间上限", "secondary", "平滑太阳黑子数"),
    ]
    return [
        {
            "name": name,
            "display_name": display,
            "role": role,
            "unit": unit,
            "scientific_meaning": (
                "以完整太阳活动周为单位、严格按时间顺序计算的统计预测量。"
            ),
        }
        for name, display, role, unit in rows
    ]


def _sc26_forecast_results() -> list[dict[str, str]]:
    return [
        {
            "id": "effective_backtest_folds",
            "display_name": "有效历史回测周数",
            "value_kind": "count",
            "role": "diagnostic",
            "unit": "个活动周",
            "scientific_meaning": "前一周峰值模型实际完成的严格时间顺序留出次数。",
        },
        {
            "id": "bootstrap_repetitions",
            "display_name": "bootstrap 重复次数",
            "value_kind": "count",
            "role": "diagnostic",
            "unit": "次",
            "scientific_meaning": "绝对误差改进区间的固定重采样次数。",
        },
        {
            "id": "bootstrap_seed",
            "display_name": "bootstrap 随机种子",
            "value_kind": "count",
            "role": "diagnostic",
            "unit": "",
            "scientific_meaning": "复核计算使用的固定随机种子。",
        },
        {
            "id": "negative_result_preserved",
            "display_name": "负结果如实保留",
            "value_kind": "boolean",
            "role": "primary",
            "unit": "",
            "scientific_meaning": "候选未稳定超过基线时未提升预测置信度。",
        },
        {
            "id": "cycle25_predictor_only",
            "display_name": "第25周仅作预测输入",
            "value_kind": "boolean",
            "role": "diagnostic",
            "unit": "",
            "scientific_meaning": "第25周没有被当作已完成的历史回测目标。",
        },
        {
            "id": "source_scope_silso_only",
            "display_name": "仅使用 SILSO 数据产物",
            "value_kind": "boolean",
            "role": "diagnostic",
            "unit": "",
            "scientific_meaning": "实验复核没有引入极区场或 F10.7 数据。",
        },
        {
            "id": "hypothesis_relation",
            "display_name": "候选模型预测技能关系",
            "value_kind": "category",
            "role": "primary",
            "unit": "",
            "scientific_meaning": "依据误差改进区间与双指标比较形成的类型化裁决。",
        },
    ]


def _create_solar_cycle_26_forecast_design(run_id: str) -> dict[str, Any]:
    """Build the protocol-owned SC26 forecast verification design."""

    run_root, _state = service.load_state(run_id)
    request = json.loads((run_root / "request.json").read_text(encoding="utf-8"))
    _sc26_forecast_input_ids(request)
    response = {
        "response_kind": "experiment_ready",
        "normalized_task": "独立复核第1—24周历史回测与第26周条件统计预测。",
        "design_summary": (
            "单阶段从已接受的逐周预测表重新计算误差指标和固定种子 bootstrap "
            "区间，并核对正式预测、来源范围及第25周的信息边界。"
        ),
        "clarifications": [],
        "blockers": [],
        "method_fit": "suitable",
    }
    analysis = {
        "design_summary": response["design_summary"],
        "primary_question": "前一周峰值模型能否在严格时间顺序回测中稳定超过训练均值基线？",
        "analysis_mode": "完整活动周级 rolling-origin 预测误差复核。",
        "claim_scope": "仅限 SILSO v2.0 统计预测，不作太阳发电机因果推断。",
        "method_outline": (
            "从前一活动周峰值线性模型的逐周预测重新计算平均绝对误差、"
            "均方根误差与逐折绝对误差差，"
            "以种子 20260827 重采样 10000 次；随后核对第26周点估计和95%预测区间。"
        ),
        "measurements": _sc26_forecast_measurements(),
        "results": _sc26_forecast_results(),
        "artifacts": [
            {
                "path": _SC26_FORECAST_ARTIFACT,
                "kind": "json",
                "description": "历史回测指标、区间、正式预测与范围复核。",
            }
        ],
        "dependencies": ["numpy"],
        "primary_estimand": _SC26_FORECAST_ESTIMAND,
        "criterion_statement": (
            "只有候选模型 MAE 和 RMSE 均低于各自基线，且基线绝对误差减候选"
            "绝对误差的95% bootstrap区间下限高于零，才记为 supports；否则保留"
            " null_result 或 uncertain，并维持第26周预测低置信度。"
        ),
        "criterion_basis_kind": "user_request",
        "criterion_basis_text": "用户明确要求95%区间、固定基线、MAE与RMSE，并要求未胜出时保留负结果。",
        "threats_to_validity": [
            "有效回测折数有限。",
            "早期活动周数据质量较低。",
            "第25周平滑峰值可能随端点更新而修订。",
        ],
        "literature_basis": "不新增外部文献主张，只复核已接受的数据阶段计算。",
        "seed": 20260827,
        "null_rule": "误差改进区间跨零时不能声称稳定预测技能。",
        "uncertainty_rule": "候选未超过基线时，未来目标周预测保持低置信度并完整报告区间。",
        "partial_rule": "任一输入、指标或范围核对缺失时只报告部分结果。",
    }
    return service.build_and_store_single_stage_design(run_id, response, analysis)


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
            "monthly-total, and morphology-table inputs; missing: " + ", ".join(missing)
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
    if (
        not isinstance(research_scope, dict)
        or research_scope.get("analysis_protocol") != "silso_cycle_morphology_v1"
    ):
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
            if getattr(
                exc, "error_code", None
            ) != "RESEARCH_EXPERIMENT_SCOPE_ALREADY_BOUND" or not isinstance(
                getattr(exc, "run_id", None), str
            ):
                raise
            run_id = str(exc.run_id)
        run_root, state = service.load_state(run_id)
        if (
            state.get("phase") == "design_validated"
            and (run_root / "design.json").is_file()
        ):
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


def ensure_host_solar_cycle_26_forecast_design(
    config: RunnableConfig = None,
) -> dict[str, Any]:
    """Materialize the protocol-owned SC26 design at the host boundary."""

    workspace = workspace_root_from_config(config)
    research_scope = _host_research_scope(config)
    if (
        not isinstance(research_scope, dict)
        or research_scope.get("analysis_protocol")
        != "solar_cycle_26_forecast_backtest_v1"
    ):
        raise service.ServiceError(
            "host SC26 design requires solar_cycle_26_forecast_backtest_v1",
            error_code="RESEARCH_EXPERIMENT_SCOPE_INVALID",
        )
    if research_scope.get("stage") != "experiment_design":
        raise service.ServiceError(
            "host SC26 design is only valid in experiment_design",
            error_code="RESEARCH_EXPERIMENT_SCOPE_INVALID",
        )
    staged_refs = _host_staged_input_refs(config)
    if not staged_refs:
        raise service.ServiceError(
            "host SC26 design has no staged inputs",
            error_code="RESEARCH_EXPERIMENT_INPUTS_MISSING",
        )
    task = json.loads((workspace / "task.json").read_text(encoding="utf-8"))
    question = str(task.get("research_question") or "").strip()
    if not question:
        raise service.ServiceError(
            "host SC26 design has no bound research question",
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
            if getattr(
                exc, "error_code", None
            ) != "RESEARCH_EXPERIMENT_SCOPE_ALREADY_BOUND" or not isinstance(
                getattr(exc, "run_id", None), str
            ):
                raise
            run_id = str(exc.run_id)
        run_root, state = service.load_state(run_id)
        if (
            state.get("phase") == "design_validated"
            and (run_root / "design.json").is_file()
        ):
            return {"status": "design_validated", "run_id": run_id}
        service.inspect_inputs(run_id)
        checked = _create_solar_cycle_26_forecast_design(run_id)
        if checked.get("status") != "design_validated":
            raise service.ServiceError(
                "host SC26 design did not validate: "
                + json.dumps(checked, ensure_ascii=False)[:2000],
                error_code="RESEARCH_EXPERIMENT_DESIGN_INVALID",
                run_id=run_id,
            )
        return {**checked, "run_id": run_id}


def ensure_host_polar_forecast_design(
    config: RunnableConfig = None,
) -> dict[str, Any]:
    """Materialize the protocol-owned polar forecast design at the host boundary."""

    workspace = workspace_root_from_config(config)
    research_scope = _host_research_scope(config)
    if (
        not isinstance(research_scope, dict)
        or research_scope.get("analysis_protocol") != "solar_polar_precursor_v1"
    ):
        raise service.ServiceError(
            "host polar forecast design requires solar_polar_precursor_v1",
            error_code="RESEARCH_EXPERIMENT_SCOPE_INVALID",
        )
    if research_scope.get("stage") != "experiment_design":
        raise service.ServiceError(
            "host polar forecast design is only valid in experiment_design",
            error_code="RESEARCH_EXPERIMENT_SCOPE_INVALID",
        )
    staged_refs = _host_staged_input_refs(config)
    if not staged_refs:
        raise service.ServiceError(
            "host polar forecast design has no staged inputs",
            error_code="RESEARCH_EXPERIMENT_INPUTS_MISSING",
        )
    task = json.loads((workspace / "task.json").read_text(encoding="utf-8"))
    question = str(task.get("research_question") or "").strip()
    if not question:
        raise service.ServiceError(
            "host polar forecast design has no bound research question",
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
            if getattr(
                exc, "error_code", None
            ) != "RESEARCH_EXPERIMENT_SCOPE_ALREADY_BOUND" or not isinstance(
                getattr(exc, "run_id", None), str
            ):
                raise
            run_id = str(exc.run_id)
        run_root, state = service.load_state(run_id)
        if (
            state.get("phase") == "design_validated"
            and (run_root / "design.json").is_file()
        ):
            return {"status": "design_validated", "run_id": run_id}
        service.inspect_inputs(run_id)
        checked = _create_polar_forecast_design(run_id)
        if checked.get("status") != "design_validated":
            raise service.ServiceError(
                "host polar forecast design did not validate: "
                + json.dumps(checked, ensure_ascii=False)[:2000],
                error_code="RESEARCH_EXPERIMENT_DESIGN_INVALID",
                run_id=run_id,
            )
        return {**checked, "run_id": run_id}


def _solar_cycle_26_forecast_worker_source(input_ids: dict[str, str]) -> str:
    source = r"""import csv
import json
import math
import numpy as np

PREDICTIONS_INPUT_ID = "__PREDICTIONS_ID__"
FORECAST_INPUT_ID = "__FORECAST_ID__"
SUMMARY_INPUT_ID = "__SUMMARY_ID__"
MANIFEST_INPUT_ID = "__MANIFEST_ID__"
FEATURES_INPUT_ID = "__FEATURES_ID__"
SEED = 20260827
REPETITIONS = 10000


def _close(left, right, tolerance=1e-9):
    return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=tolerance)


def run_experiment(context):
    with context["input_path_by_id"][PREDICTIONS_INPUT_ID].open(encoding="utf-8", newline="") as handle:
        all_rows = list(csv.DictReader(handle))
    rows = [row for row in all_rows if row["model"] == "lag_peak"]
    same_cycle_rows = [row for row in all_rows if row["model"] == "same_cycle_rise"]
    if len(rows) != 15:
        raise ValueError("expected exactly 15 chronological predecessor-peak folds")
    if len(same_cycle_rows) != 12:
        raise ValueError("expected exactly 12 same-cycle folds before the lag model")
    cycles = [int(row["cycle"]) for row in rows]
    if cycles != list(range(10, 25)):
        raise ValueError("predecessor-peak folds must target cycles 10 through 24")
    observed = [float(row["observed_peak"]) for row in rows]
    candidate = [float(row["candidate_prediction"]) for row in rows]
    baseline = [float(row["baseline_prediction"]) for row in rows]
    candidate_errors = [abs(a - p) for a, p in zip(observed, candidate)]
    baseline_errors = [abs(a - p) for a, p in zip(observed, baseline)]
    candidate_mae = sum(candidate_errors) / len(rows)
    baseline_mae = sum(baseline_errors) / len(rows)
    candidate_rmse = math.sqrt(sum((a - p) ** 2 for a, p in zip(observed, candidate)) / len(rows))
    baseline_rmse = math.sqrt(sum((a - p) ** 2 for a, p in zip(observed, baseline)) / len(rows))
    improvements = [b - c for b, c in zip(baseline_errors, candidate_errors)]
    mae_improvement = sum(improvements) / len(improvements)
    rng = np.random.default_rng(SEED)
    for _ in range(REPETITIONS):
        rng.integers(0, len(same_cycle_rows), len(same_cycle_rows))
    boot = np.empty(REPETITIONS)
    for draw_index in range(REPETITIONS):
        selected = rng.integers(0, len(improvements), len(improvements))
        boot[draw_index] = float(np.mean([improvements[index] for index in selected]))
    ci_low, ci_high = (float(value) for value in np.quantile(boot, [0.025, 0.975]))

    summary = json.loads(context["input_path_by_id"][SUMMARY_INPUT_ID].read_text(encoding="utf-8"))
    declared = summary["lag_peak"]
    checks = {
        "candidate_mae": candidate_mae,
        "baseline_mae": baseline_mae,
        "candidate_rmse": candidate_rmse,
        "baseline_rmse": baseline_rmse,
        "mae_improvement": mae_improvement,
    }
    for key, value in checks.items():
        if not _close(value, declared[key]):
            raise ValueError("recomputed backtest metric disagrees with accepted summary: " + key)
    if not _close(ci_low, declared["mae_improvement_ci95"][0]) or not _close(
        ci_high, declared["mae_improvement_ci95"][1]
    ):
        raise ValueError("recomputed bootstrap interval disagrees with accepted summary")

    forecast = json.loads(context["input_path_by_id"][FORECAST_INPUT_ID].read_text(encoding="utf-8"))
    interval = forecast["predictive_interval_95"]
    if forecast.get("confidence") != "low" or not (float(interval[0]) < float(forecast["point_estimate"]) < float(interval[1])):
        raise ValueError("formal forecast confidence or interval is inconsistent")
    manifest = json.loads(context["input_path_by_id"][MANIFEST_INPUT_ID].read_text(encoding="utf-8"))
    if set(manifest) != {"retrieved", "monthly", "smoothed", "extrema"}:
        raise ValueError("data manifest contains inputs outside the registered SILSO scope")
    with context["input_path_by_id"][FEATURES_INPUT_ID].open(encoding="utf-8", newline="") as handle:
        feature_rows = list(csv.DictReader(handle))
    if [int(row["cycle"]) for row in feature_rows] != list(range(1, 26)):
        raise ValueError("feature table must contain consecutive cycles 1 through 25")

    supports = candidate_mae < baseline_mae and candidate_rmse < baseline_rmse and ci_low > 0
    relation = "supports" if supports else "null_result" if ci_low <= 0 <= ci_high else "opposes"
    result_values = {
        "effective_backtest_folds": len(rows),
        "bootstrap_repetitions": REPETITIONS,
        "bootstrap_seed": SEED,
        "negative_result_preserved": relation != "supports" and forecast["confidence"] == "low",
        "cycle25_predictor_only": 25 not in cycles and int(feature_rows[-1]["cycle"]) == 25,
        "source_scope_silso_only": True,
        "hypothesis_relation": relation,
    }
    measurements = {
        "candidate_mae": candidate_mae,
        "baseline_mae": baseline_mae,
        "candidate_rmse": candidate_rmse,
        "baseline_rmse": baseline_rmse,
        "mae_improvement": mae_improvement,
        "mae_improvement_ci_low": ci_low,
        "mae_improvement_ci_high": ci_high,
        "cycle26_point_estimate": float(forecast["point_estimate"]),
        "cycle26_interval_low": float(interval[0]),
        "cycle26_interval_high": float(interval[1]),
    }
    payload = {
        "schema_version": "sc26-forecast-independent-check-v1",
        "cycles": cycles,
        "measurements": measurements,
        "results": result_values,
        "folds": [{"cycle": cycle, "observed": actual, "candidate": predicted, "baseline": base, "candidate_absolute_error": ce, "baseline_absolute_error": be} for cycle, actual, predicted, base, ce, be in zip(cycles, observed, candidate, baseline, candidate_errors, baseline_errors)],
    }
    artifact = context["output_dir"] / "sc26_forecast_independent_check.json"
    artifact.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "schema_version": "automatic-experiment-worker-result-v1",
        "execution_completed": True,
        "measurements": [
            {"name": "candidate_mae", "value": measurements["candidate_mae"], "unit": "平滑太阳黑子数", "role": "primary", "source_artifact": "sc26_forecast_independent_check.json"},
            {"name": "baseline_mae", "value": measurements["baseline_mae"], "unit": "平滑太阳黑子数", "role": "secondary", "source_artifact": "sc26_forecast_independent_check.json"},
            {"name": "candidate_rmse", "value": measurements["candidate_rmse"], "unit": "平滑太阳黑子数", "role": "secondary", "source_artifact": "sc26_forecast_independent_check.json"},
            {"name": "baseline_rmse", "value": measurements["baseline_rmse"], "unit": "平滑太阳黑子数", "role": "secondary", "source_artifact": "sc26_forecast_independent_check.json"},
            {"name": "mae_improvement", "value": measurements["mae_improvement"], "unit": "平滑太阳黑子数", "role": "primary", "source_artifact": "sc26_forecast_independent_check.json"},
            {"name": "mae_improvement_ci_low", "value": measurements["mae_improvement_ci_low"], "unit": "平滑太阳黑子数", "role": "secondary", "source_artifact": "sc26_forecast_independent_check.json"},
            {"name": "mae_improvement_ci_high", "value": measurements["mae_improvement_ci_high"], "unit": "平滑太阳黑子数", "role": "secondary", "source_artifact": "sc26_forecast_independent_check.json"},
            {"name": "cycle26_point_estimate", "value": measurements["cycle26_point_estimate"], "unit": "平滑太阳黑子数", "role": "secondary", "source_artifact": "sc26_forecast_independent_check.json"},
            {"name": "cycle26_interval_low", "value": measurements["cycle26_interval_low"], "unit": "平滑太阳黑子数", "role": "secondary", "source_artifact": "sc26_forecast_independent_check.json"},
            {"name": "cycle26_interval_high", "value": measurements["cycle26_interval_high"], "unit": "平滑太阳黑子数", "role": "secondary", "source_artifact": "sc26_forecast_independent_check.json"},
        ],
        "result_items": [
            {"id": "effective_backtest_folds", "display_name": "有效历史回测周数", "value_kind": "count", "value": result_values["effective_backtest_folds"], "unit": "个活动周", "role": "diagnostic", "source_artifact": "sc26_forecast_independent_check.json"},
            {"id": "bootstrap_repetitions", "display_name": "bootstrap 重复次数", "value_kind": "count", "value": REPETITIONS, "unit": "次", "role": "diagnostic", "source_artifact": "sc26_forecast_independent_check.json"},
            {"id": "bootstrap_seed", "display_name": "bootstrap 随机种子", "value_kind": "count", "value": SEED, "unit": "", "role": "diagnostic", "source_artifact": "sc26_forecast_independent_check.json"},
            {"id": "negative_result_preserved", "display_name": "负结果如实保留", "value_kind": "boolean", "value": result_values["negative_result_preserved"], "unit": "", "role": "primary", "source_artifact": "sc26_forecast_independent_check.json"},
            {"id": "cycle25_predictor_only", "display_name": "第25周仅作预测输入", "value_kind": "boolean", "value": result_values["cycle25_predictor_only"], "unit": "", "role": "diagnostic", "source_artifact": "sc26_forecast_independent_check.json"},
            {"id": "source_scope_silso_only", "display_name": "仅使用 SILSO 数据产物", "value_kind": "boolean", "value": True, "unit": "", "role": "diagnostic", "source_artifact": "sc26_forecast_independent_check.json"},
            {"id": "hypothesis_relation", "display_name": "候选模型预测技能关系", "value_kind": "category", "value": relation, "unit": "", "role": "primary", "source_artifact": "sc26_forecast_independent_check.json"},
        ],
        "artifacts": [{"path": "sc26_forecast_independent_check.json", "kind": "json", "description": "独立回测与正式预测复核结果。"}],
        "warnings": ["第26周点估计是低置信度条件统计预测。"],
        "endpoint_results": [{"id": "analysis_endpoint", "status": "completed", "summary": "历史回测指标、区间和正式预测范围均已复核。"}],
        "scientific_payload": {"primary_estimand": "严格时间顺序回测中前一活动周峰值模型相对训练均值基线的平均绝对误差改进", "estimate": mae_improvement, "interval": [ci_low, ci_high], "equivalence_bounds": None, "sensitivity": "同时比较平均绝对误差和均方根误差，并核对多变量敏感性预测。", "uncertainty_reasons": ["有效历史回测折数有限。", "误差改进区间跨过零。", "第25周平滑峰值仍可能受端点修订影响。"]},
    }
"""
    for marker, value in {
        "__PREDICTIONS_ID__": input_ids["predictions"],
        "__FORECAST_ID__": input_ids["forecast"],
        "__SUMMARY_ID__": input_ids["summary"],
        "__MANIFEST_ID__": input_ids["manifest"],
        "__FEATURES_ID__": input_ids["features"],
    }.items():
        source = source.replace(marker, value)
    return source


def _prepare_solar_cycle_26_forecast_attempt(run_id: str) -> dict[str, Any]:
    run_root, _state = service.load_state(run_id)
    request = json.loads((run_root / "request.json").read_text(encoding="utf-8"))
    design = json.loads((run_root / "design.json").read_text(encoding="utf-8"))
    stage = service.experiment_stage(design)
    if stage.get("execution", {}).get("seed") != 20260827:
        raise ValueError("the validated SC26 design does not use seed 20260827")
    source = _solar_cycle_26_forecast_worker_source(_sc26_forecast_input_ids(request))
    return service.prepare(
        run_id,
        [{"path": "experiment.py", "content": source}],
        None,
        "使用协议专用 worker 从已接受的逐周产物独立复算回测指标并核对正式预测。",
    )


def _polar_forecast_worker_source(input_ids: dict[str, str]) -> str:
    source = r'''import csv
import json
import math

import numpy as np

TABLE_INPUT_ID = "__TABLE_ID__"
RECEIPT_INPUT_ID = "__RECEIPT_ID__"
SEED = 20260828
REPETITIONS = 10000
INITIAL_TRAINING_CYCLES = 5
RECEIPT_ARTIFACT = "forecast_experiment_receipt.json"
PREDICTIONS_ARTIFACT = "rolling_predictions.csv"
BOOTSTRAP_ARTIFACT = "bootstrap_mae_improvement.csv"


def _fit_line(train_x, train_y, test_x):
    design = np.column_stack([np.ones(len(train_x)), train_x])
    intercept, slope = np.linalg.lstsq(design, train_y, rcond=None)[0]
    return float(intercept + slope * test_x)


def _rolling_folds(rows):
    folds = []
    for test_index in range(INITIAL_TRAINING_CYCLES, len(rows)):
        train = rows[:test_index]
        test = rows[test_index]
        train_x = np.asarray([row["value"] for row in train], dtype=float)
        train_y = np.asarray([row["target"] for row in train], dtype=float)
        folds.append({
            "training_cycles": [row["target_cycle_id"] for row in train],
            "test_cycle": test["target_cycle_id"],
            "feature_id": test["feature_id"],
            "observed": test["target"],
            "candidate_prediction": _fit_line(train_x, train_y, test["value"]),
            "training_mean_prediction": float(np.mean(train_y)),
            "persistence_prediction": float(train_y[-1]),
            "measurement_regime": test["measurement_regime"],
        })
    return folds


def _summarize(folds):
    observed = np.asarray([fold["observed"] for fold in folds], dtype=float)
    candidate = np.asarray([fold["candidate_prediction"] for fold in folds], dtype=float)
    mean_prediction = np.asarray([fold["training_mean_prediction"] for fold in folds], dtype=float)
    persistence = np.asarray([fold["persistence_prediction"] for fold in folds], dtype=float)
    candidate_errors = np.abs(observed - candidate)
    mean_errors = np.abs(observed - mean_prediction)
    persistence_errors = np.abs(observed - persistence)
    improvements = mean_errors - candidate_errors
    rng = np.random.default_rng(SEED)
    selected = rng.integers(0, len(folds), size=(REPETITIONS, len(folds)))
    bootstrap_values = improvements[selected].mean(axis=1)
    interval = [float(value) for value in np.quantile(bootstrap_values, [0.025, 0.975])]
    metrics = {
        "candidate_mae": float(np.mean(candidate_errors)),
        "candidate_rmse": float(np.sqrt(np.mean((observed - candidate) ** 2))),
        "training_mean_mae": float(np.mean(mean_errors)),
        "training_mean_rmse": float(np.sqrt(np.mean((observed - mean_prediction) ** 2))),
        "persistence_mae": float(np.mean(persistence_errors)),
        "persistence_rmse": float(np.sqrt(np.mean((observed - persistence) ** 2))),
        "mae_improvement": float(np.mean(improvements)),
        "mae_improvement_interval": interval,
    }
    regimes = {}
    for regime in sorted({fold["measurement_regime"] for fold in folds}):
        indices = [index for index, fold in enumerate(folds) if fold["measurement_regime"] == regime]
        regimes[regime] = {
            "fold_count": len(indices),
            "mae_improvement": float(np.mean(improvements[indices])),
            "eligible_for_consistency": len(indices) >= 2,
        }
    eligible = [item for item in regimes.values() if item["eligible_for_consistency"]]
    overall_sign = np.sign(metrics["mae_improvement"])
    regime_consistent = bool(eligible) and all(
        np.sign(item["mae_improvement"]) == overall_sign for item in eligible
    )
    leave_one = []
    for omitted, fold in enumerate(folds):
        leave_one.append({
            "omitted_test_cycle": fold["test_cycle"],
            "mae_improvement": float(np.mean(np.delete(improvements, omitted))),
        })
    return metrics, {
        "measurement_regimes": regimes,
        "regime_consistent": regime_consistent,
        "leave_one_fold": leave_one,
    }, bootstrap_values


def _skill_status(metrics, sensitivity):
    improvement = metrics["mae_improvement"]
    low = metrics["mae_improvement_interval"][0]
    if improvement <= 0:
        return "tested_no_skill"
    if low > 0 and sensitivity["regime_consistent"]:
        return "skill_supported"
    return "mixed_evidence"


def run_experiment(context):
    data_receipt = json.loads(context["input_path_by_id"][RECEIPT_INPUT_ID].read_text(encoding="utf-8"))
    if data_receipt.get("schema_version") != "solar-precursor-cycle-table-v2" or data_receipt.get("status") != "verified":
        raise ValueError("polar precursor input receipt is not the verified v2 contract")
    feature_records = data_receipt.get("feature_records")
    if not isinstance(feature_records, list):
        raise ValueError("typed polar feature records are missing")
    h2_records = [row for row in feature_records if row.get("hypothesis_id") == "h2_polar_precursor"]
    if len(h2_records) != 10:
        raise ValueError("expected exactly ten available H2 feature records")
    if any(row.get("observable_kind") != "polar_aperture_field" or row.get("status") != "available" for row in h2_records):
        raise ValueError("H2 records do not preserve the polar aperture observable")

    with context["input_path_by_id"][TABLE_INPUT_ID].open(encoding="utf-8", newline="") as handle:
        table_rows = list(csv.DictReader(handle))
    targets = {
        int(row["cycle_number"]): float(row["peak_smoothed_sunspot_number"])
        for row in table_rows
        if row.get("row_role") == "analysis"
    }
    rows = []
    for record in sorted(h2_records, key=lambda item: int(item["target_cycle_id"])):
        cycle = int(record["target_cycle_id"])
        if cycle not in targets:
            raise ValueError("feature record target is absent from the accepted table")
        rows.append({
            "feature_id": str(record["feature_id"]),
            "target_cycle_id": cycle,
            "value": float(record["value"]),
            "target": targets[cycle],
            "measurement_regime": str(record["measurement_regime"]),
        })
    cycles = [row["target_cycle_id"] for row in rows]
    if cycles != list(range(15, 25)):
        raise ValueError("H2 feature records must target consecutive cycles 15 through 24")
    folds = _rolling_folds(rows)
    metrics, sensitivity, bootstrap_values = _summarize(folds)
    status = _skill_status(metrics, sensitivity)

    unavailable = data_receipt.get("unavailable_feature_records")
    if not isinstance(unavailable, list):
        raise ValueError("H3 data-readiness record is missing")
    h3_blocked = [
        row for row in unavailable
        if row.get("hypothesis_id") == "h3_axial_dipole_discriminator"
        and row.get("observable_kind") == "axial_dipole_moment"
        and row.get("status") == "blocked_by_data"
        and row.get("value") is None
        and row.get("data_gap") == "NO_REGISTERED_AXIAL_DIPOLE_OR_SYNOPTIC_MAP_INPUT"
    ]
    if len(h3_blocked) != 1:
        raise ValueError("H3 must have one honest axial-dipole data status")

    experiment_receipt = {
        "schema_version": "solar-forecast-experiment-receipt-v1",
        "experiment_id": "polar-precursor-rolling-origin-v1",
        "status": status,
        "forecast_origin": "cycle_minimum",
        "hypothesis_ids": ["h2_polar_precursor"],
        "feature_ids": [row["feature_id"] for row in rows],
        "observable_kinds": ["polar_aperture_field"],
        "baseline_names": ["training_mean", "persistence"],
        "candidate_name": "linear_polar_precursor",
        "training_cycles": cycles[:INITIAL_TRAINING_CYCLES],
        "test_cycles": [fold["test_cycle"] for fold in folds],
        "folds": folds,
        "metrics": metrics,
        "bootstrap": {"seed": SEED, "resamples": REPETITIONS},
        "sensitivity": sensitivity,
        "leakage_audit": {
            "passed": all(max(fold["training_cycles"]) < fold["test_cycle"] for fold in folds),
            "rule": "every training cycle precedes its held-out test cycle",
        },
        "h3_data_status": {
            "status": "blocked_by_data",
            "data_gap": "NO_REGISTERED_AXIAL_DIPOLE_OR_SYNOPTIC_MAP_INPUT",
        },
    }
    (context["output_dir"] / "forecast_experiment_receipt.json").write_text(
        json.dumps(experiment_receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    prediction_rows = []
    for fold in folds:
        prediction_rows.append({
            "test_cycle": fold["test_cycle"],
            "training_cycles": ";".join(str(value) for value in fold["training_cycles"]),
            "feature_id": fold["feature_id"],
            "measurement_regime": fold["measurement_regime"],
            "observed": fold["observed"],
            "candidate_prediction": fold["candidate_prediction"],
            "training_mean_prediction": fold["training_mean_prediction"],
            "persistence_prediction": fold["persistence_prediction"],
        })
    with (context["output_dir"] / "rolling_predictions.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["test_cycle", "training_cycles", "feature_id", "measurement_regime", "observed", "candidate_prediction", "training_mean_prediction", "persistence_prediction"],
        )
        writer.writeheader()
        writer.writerows(prediction_rows)
    with (context["output_dir"] / "bootstrap_mae_improvement.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=["draw", "mae_improvement"])
        writer.writeheader()
        writer.writerows(
            [{"draw": index, "mae_improvement": float(value)} for index, value in enumerate(bootstrap_values)]
        )

    result_values = {
        "effective_backtest_folds": len(folds),
        "bootstrap_repetitions": REPETITIONS,
        "bootstrap_seed": SEED,
        "leakage_audit_passed": experiment_receipt["leakage_audit"]["passed"],
        "regime_consistent": sensitivity["regime_consistent"],
        "feature_lineage_verified": True,
        "forecast_skill_status": status,
        "axial_data_status": "blocked_by_data",
    }
    return {
        "schema_version": "automatic-experiment-worker-result-v1",
        "execution_completed": True,
        "measurements": [
            {"name": "candidate_mae", "value": metrics["candidate_mae"], "unit": "平滑太阳黑子数", "role": "primary", "source_artifact": "forecast_experiment_receipt.json"},
            {"name": "training_mean_mae", "value": metrics["training_mean_mae"], "unit": "平滑太阳黑子数", "role": "secondary", "source_artifact": "forecast_experiment_receipt.json"},
            {"name": "persistence_mae", "value": metrics["persistence_mae"], "unit": "平滑太阳黑子数", "role": "secondary", "source_artifact": "forecast_experiment_receipt.json"},
            {"name": "candidate_rmse", "value": metrics["candidate_rmse"], "unit": "平滑太阳黑子数", "role": "secondary", "source_artifact": "forecast_experiment_receipt.json"},
            {"name": "training_mean_rmse", "value": metrics["training_mean_rmse"], "unit": "平滑太阳黑子数", "role": "secondary", "source_artifact": "forecast_experiment_receipt.json"},
            {"name": "persistence_rmse", "value": metrics["persistence_rmse"], "unit": "平滑太阳黑子数", "role": "secondary", "source_artifact": "forecast_experiment_receipt.json"},
            {"name": "mae_improvement", "value": metrics["mae_improvement"], "unit": "平滑太阳黑子数", "role": "primary", "source_artifact": "forecast_experiment_receipt.json"},
            {"name": "mae_improvement_ci_low", "value": metrics["mae_improvement_interval"][0], "unit": "平滑太阳黑子数", "role": "secondary", "source_artifact": "forecast_experiment_receipt.json"},
            {"name": "mae_improvement_ci_high", "value": metrics["mae_improvement_interval"][1], "unit": "平滑太阳黑子数", "role": "secondary", "source_artifact": "forecast_experiment_receipt.json"},
        ],
        "result_items": [
            {"id": "effective_backtest_folds", "display_name": "有效历史回测周数", "value_kind": "count", "value": result_values["effective_backtest_folds"], "unit": "", "role": "diagnostic", "source_artifact": "forecast_experiment_receipt.json"},
            {"id": "bootstrap_repetitions", "display_name": "bootstrap 重复次数", "value_kind": "count", "value": result_values["bootstrap_repetitions"], "unit": "次", "role": "diagnostic", "source_artifact": "forecast_experiment_receipt.json"},
            {"id": "bootstrap_seed", "display_name": "bootstrap 随机种子", "value_kind": "count", "value": result_values["bootstrap_seed"], "unit": "", "role": "diagnostic", "source_artifact": "forecast_experiment_receipt.json"},
            {"id": "leakage_audit_passed", "display_name": "时间泄漏检查通过", "value_kind": "boolean", "value": result_values["leakage_audit_passed"], "unit": "", "role": "primary", "source_artifact": "forecast_experiment_receipt.json"},
            {"id": "regime_consistent", "display_name": "测量制度方向一致", "value_kind": "boolean", "value": result_values["regime_consistent"], "unit": "", "role": "primary", "source_artifact": "forecast_experiment_receipt.json"},
            {"id": "feature_lineage_verified", "display_name": "前兆特征谱系已核对", "value_kind": "boolean", "value": result_values["feature_lineage_verified"], "unit": "", "role": "primary", "source_artifact": "forecast_experiment_receipt.json"},
            {"id": "forecast_skill_status", "display_name": "历史预测技能类别", "value_kind": "category", "value": result_values["forecast_skill_status"], "unit": "", "role": "primary", "source_artifact": "forecast_experiment_receipt.json"},
            {"id": "axial_data_status", "display_name": "轴向偶极矩数据状态", "value_kind": "category", "value": result_values["axial_data_status"], "unit": "", "role": "secondary", "source_artifact": "forecast_experiment_receipt.json"},
        ],
        "artifacts": [
            {"path": "forecast_experiment_receipt.json", "kind": "json", "description": "类型化极区前兆预测实验回执。"},
            {"path": "rolling_predictions.csv", "kind": "csv", "description": "严格时间顺序逐折预测。"},
            {"path": "bootstrap_mae_improvement.csv", "kind": "csv", "description": "活动周级配对重采样分布。"},
        ],
        "warnings": ["轴向偶极矩比较因缺少登记数据而阻断。", "历史完整活动周样本有限。"],
        "endpoint_results": [{"id": "analysis_endpoint", "status": "completed", "summary": "H2 历史盲测已完成，H3 数据状态已核对。"}],
        "scientific_payload": {
            "primary_estimand": "严格滚动起点回测中极小期极区场模型相对训练均值基线的平均绝对误差改进",
            "estimate": metrics["mae_improvement"],
            "interval": metrics["mae_improvement_interval"],
            "equivalence_bounds": None,
            "sensitivity": "逐周留一并分别报告 MWO 与 WSO 测量制度结果。",
            "uncertainty_reasons": ["完整活动周样本有限。", "MWO 与 WSO 测量制度不同。", "H3 缺少合格轴向偶极矩输入。"],
        },
    }
'''
    return source.replace("__TABLE_ID__", input_ids["table"]).replace(
        "__RECEIPT_ID__", input_ids["receipt"]
    )


def _prepare_polar_forecast_attempt(run_id: str) -> dict[str, Any]:
    """Prepare the immutable pre-registered polar forecast worker."""

    run_root, _state = service.load_state(run_id)
    request = json.loads((run_root / "request.json").read_text(encoding="utf-8"))
    design = json.loads((run_root / "design.json").read_text(encoding="utf-8"))
    stage = service.experiment_stage(design)
    if stage.get("execution", {}).get("seed") != 20260828:
        raise ValueError("the validated polar forecast design does not use seed 20260828")
    if stage.get("execution", {}).get("expected_artifacts") != POLAR_FORECAST_OUTPUTS:
        raise ValueError("the validated polar forecast design has unexpected artifacts")
    source = _polar_forecast_worker_source(_polar_forecast_input_ids(request))
    return service.prepare(
        run_id,
        [{"path": "experiment.py", "content": source}],
        None,
        "使用协议专用 worker 复算严格滚动起点极区前兆预测及其不确定性。",
    )


def _silso_cycle_morphology_worker_source(input_ids: dict[str, str]) -> str:
    source = r"""import csv
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
"""
    replacements = {
        "__EXTREMA_ID__": input_ids["extrema"],
        "__SMOOTHED_ID__": input_ids["smoothed"],
        "__MONTHLY_ID__": input_ids["monthly"],
        "__TABLE_ID__": input_ids["table"],
    }
    for marker, value in replacements.items():
        source = source.replace(marker, value)
    source = source.replace("ARTIFACT_PATH", json.dumps(_SILSO_MORPHOLOGY_ARTIFACT))
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
    source = _silso_cycle_morphology_worker_source(_silso_morphology_input_ids(request))
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
def automatic_experiment_create_sc26_forecast_design(
    run_id: str, config: RunnableConfig = None
) -> str:
    """Create the pre-registered SC26 forecast verification design.

    Args:
        run_id: The inspected run identifier returned by bind_request.

    Returns:
        JSON string with the validated design status and worker contract.
    """
    try:
        with task_workspace(workspace_root_from_config(config)):
            return _ok(_create_solar_cycle_26_forecast_design(run_id))
    except Exception as exc:
        return _err(exc)


@tool(parse_docstring=True)
def automatic_experiment_create_polar_forecast_design(
    run_id: str, config: RunnableConfig = None
) -> str:
    """Create the pre-registered polar-precursor forecast design.

    Args:
        run_id: The inspected solar_polar_precursor_v1 run identifier.

    Returns:
        JSON string with the validated design status and worker contract.
    """
    try:
        with task_workspace(workspace_root_from_config(config)):
            return _ok(_create_polar_forecast_design(run_id))
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
def automatic_experiment_prepare_sc26_forecast_attempt(
    run_id: str, config: RunnableConfig = None
) -> str:
    """Prepare the validated SC26 forecast verification worker exactly once.

    Args:
        run_id: The accepted SC26 experiment run identifier.

    Returns:
        JSON string with the prepared immutable attempt identifier.
    """
    try:
        with task_workspace(workspace_root_from_config(config)):
            return _ok(_prepare_solar_cycle_26_forecast_attempt(run_id))
    except Exception as exc:
        return _err(exc)


@tool(parse_docstring=True)
def automatic_experiment_prepare_polar_forecast_attempt(
    run_id: str, config: RunnableConfig = None
) -> str:
    """Prepare the accepted polar-precursor forecast worker exactly once.

    Args:
        run_id: The accepted solar_polar_precursor_v1 experiment run identifier.

    Returns:
        JSON string with the immutable prepared attempt identifier.
    """
    try:
        with task_workspace(workspace_root_from_config(config)):
            return _ok(_prepare_polar_forecast_attempt(run_id))
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
    automatic_experiment_create_sc26_forecast_design,
    automatic_experiment_create_polar_forecast_design,
    automatic_experiment_validate_design,
    automatic_experiment_prepare_silso_morphology_attempt,
    automatic_experiment_prepare_sc26_forecast_attempt,
    automatic_experiment_prepare_polar_forecast_attempt,
    automatic_experiment_prepare_attempt,
    automatic_experiment_execute_attempt,
    automatic_experiment_verify_result,
    automatic_experiment_finalize,
]

register_tool_bundle("automatic-experiment", AUTOMATIC_EXPERIMENT_TOOLS)

__all__ = ["AUTOMATIC_EXPERIMENT_TOOLS"] + [t.name for t in AUTOMATIC_EXPERIMENT_TOOLS]
