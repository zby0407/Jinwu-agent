from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

import dataset_stats_engine
import experiment_handoff_engine
import feature_engineering_engine
import upload_quality_analyzer
from chat_session import ChatSession
from piagent_schemas import REQUIRED_OUTPUTS, PiAgentRequest
from piagent_tools import load_dataset_for_chat, run_contract_tests, run_full_workflow
from solar_feature_agent import workflows as skill_workflows
from upload_inspector import inspect_csv


ROOT = Path(__file__).resolve().parents[1]
MAX_DATASET_BYTES = 200 * 1024 * 1024
READ_ONLY_TOOLS = {
    "plan_solar_feature_workflow",
    "audit_solar_data_quality",
    "propose_solar_cleaning",
    "inspect_dataset",
    "dataset_statistics",
    "analyze_data_quality",
    "validate_data_contracts",
}
WRITE_TOOLS = {
    "ingest_align_solar_data",
    "apply_solar_cleaning",
    "engineer_solar_features",
    "prepare_solar_experiment",
    "run_solar_feature_workflow",
    "register_dataset",
    "generate_dataset_features",
    "create_experiment_handoff",
    "rebuild_project_pipeline",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def _bounded(value: Any, *, list_limit: int = 50) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _bounded(item, list_limit=list_limit)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_bounded(item, list_limit=list_limit) for item in value[:list_limit]]
    if isinstance(value, tuple):
        return [_bounded(item, list_limit=list_limit) for item in value[:list_limit]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return str(value)


def tool_result(
    name: str,
    *,
    status: str = "ok",
    summary: Any = None,
    artifacts: list[str] | None = None,
    warnings: list[str] | None = None,
    error: dict[str, str] | None = None,
    started_utc: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "tool": name,
        "summary": _bounded(summary or {}),
        "artifacts": list(artifacts or []),
        "warnings": list(warnings or []),
        "error": error,
        "started_utc": started_utc or utc_now(),
        "finished_utc": utc_now(),
    }


class PathPolicy:
    """Resolve CSV paths and enforce the configured local read boundary."""

    def __init__(self, extra_roots: list[str | Path] | None = None) -> None:
        try:
            from dotenv import load_dotenv

            load_dotenv(ROOT / ".env", override=False)
        except ImportError:
            pass
        roots: list[Path] = [ROOT.resolve()]
        configured = os.getenv("DATA_FEATURE_ALLOWED_ROOTS", "")
        if configured:
            roots.extend(
                Path(item.strip().strip('"')).expanduser()
                for item in configured.split(os.pathsep)
                if item.strip()
            )
        if extra_roots:
            roots.extend(Path(item).expanduser() for item in extra_roots)
        self.allowed_roots = list(dict.fromkeys(path.resolve() for path in roots))

    def resolve_csv(self, value: str | Path) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = ROOT / path
        path = path.resolve()
        if not any(
            path == root or path.is_relative_to(root) for root in self.allowed_roots
        ):
            raise PermissionError(
                f"Dataset path is outside DATA_FEATURE_ALLOWED_ROOTS: {path}. "
                f"Allowed roots: {[str(root) for root in self.allowed_roots]}"
            )
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"CSV not found: {path}")
        if path.suffix.lower() != ".csv":
            raise ValueError(f"Only CSV files are supported; got {path.suffix!r}")
        size = path.stat().st_size
        if size == 0:
            raise ValueError("CSV file is empty")
        if size > MAX_DATASET_BYTES:
            raise ValueError(f"CSV exceeds the {MAX_DATASET_BYTES} byte limit")
        return path


class EphemeralChatSession(ChatSession):
    """ChatSession-compatible state that never writes read-only tool state."""

    def __init__(self) -> None:
        self.session_path = Path("<ephemeral>")
        self._data = self._default_session()

    def save(self) -> None:
        return


@dataclass
class AgentToolContext:
    session: ChatSession
    path_policy: PathPolicy

    def current_path(self, requested: str | None = None) -> Path:
        value = requested or self.session.get_current_dataset_path()
        if not value:
            raise ValueError(
                "No dataset is available. Provide path or call register_dataset first."
            )
        return self.path_policy.resolve_csv(value)


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    mutates: bool
    handler: Callable[[AgentToolContext, dict[str, Any]], dict[str, Any]]

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _inspection_wrapper(path: Path, inspection: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_file": {
            "name": path.name,
            "absolute_path": str(path),
            "bytes": path.stat().st_size,
            "stored_path": str(path),
        },
        "inspection": inspection,
    }


def _read_frame(path: Path, inspection: dict[str, Any] | None = None) -> pd.DataFrame:
    inspection = inspection or inspect_csv(path)
    delimiter = inspection.get("delimiter", ",")
    if delimiter == "\\t":
        delimiter = "\t"
    frame = pd.read_csv(
        path,
        encoding=inspection.get("encoding", "utf-8"),
        sep=delimiter,
        low_memory=False,
    )
    frame.columns = [str(column).strip() for column in frame.columns]
    return frame


def _stats_session(path: Path) -> EphemeralChatSession:
    inspection = inspect_csv(path)
    session = EphemeralChatSession()
    session.set_current_dataset(str(path), _inspection_wrapper(path, inspection))
    return session


def inspect_dataset(ctx: AgentToolContext, args: dict[str, Any]) -> dict[str, Any]:
    started = utc_now()
    path = ctx.path_policy.resolve_csv(args["path"])
    max_rows = args.get("max_rows")
    if max_rows is not None:
        max_rows = max(1, min(int(max_rows), 100_000))
    inspection = inspect_csv(path, max_rows=max_rows)
    return tool_result(
        "inspect_dataset",
        summary={
            "path": str(path),
            "bytes": path.stat().st_size,
            "inspection": inspection,
        },
        warnings=inspection.get("warnings", []),
        started_utc=started,
    )


def dataset_statistics(ctx: AgentToolContext, args: dict[str, Any]) -> dict[str, Any]:
    started = utc_now()
    path = ctx.current_path(args.get("path"))
    session = _stats_session(path)
    action = args["action"]
    columns = list(args.get("columns") or [])
    n = max(1, min(int(args.get("n", 5)), 20))
    aggregation = args.get("aggregation", "mean")
    group = args.get("group")

    if action == "describe":
        result = dataset_stats_engine.describe(session)
    elif action == "head":
        result = dataset_stats_engine.head(session, n=n)
    elif action == "tail":
        result = dataset_stats_engine.tail(session, n=n)
    elif action == "column_stats":
        result = dataset_stats_engine.column_stats(
            session, column=columns[0] if columns else None
        )
    elif action == "corr":
        if len(columns) != 2:
            raise ValueError("corr requires exactly two columns")
        result = dataset_stats_engine.corr(session, columns[0], columns[1])
    elif action == "value_counts":
        if len(columns) != 1:
            raise ValueError("value_counts requires exactly one column")
        result = dataset_stats_engine.value_counts(session, columns[0])
    elif action == "groupby":
        if len(columns) != 1:
            raise ValueError("groupby requires exactly one group column")
        result = dataset_stats_engine.groupby(session, columns[0], aggregation)
    elif action == "drift":
        if len(columns) != 2:
            raise ValueError("drift requires exactly two numeric columns")
        result = dataset_stats_engine.drift(
            session, columns[0], columns[1], group=group
        )
    else:
        raise ValueError(f"Unknown statistics action: {action}")
    return tool_result("dataset_statistics", summary=result, started_utc=started)


def analyze_data_quality(ctx: AgentToolContext, args: dict[str, Any]) -> dict[str, Any]:
    started = utc_now()
    path = ctx.current_path(args.get("path"))
    inspection = inspect_csv(path)
    report = upload_quality_analyzer.analyze(_read_frame(path, inspection), inspection)
    # Session metadata is allowed for read tools; no business report is written.
    ctx.session.set_agent_state("latest_quality_report", report)
    ctx.session.set_agent_state("latest_quality_dataset", str(path))
    return tool_result(
        "analyze_data_quality",
        summary=report,
        warnings=[
            issue["message"]
            for issue in report.get("issues", [])
            if issue.get("severity") == "critical"
        ],
        started_utc=started,
    )


def validate_data_contracts(
    ctx: AgentToolContext, args: dict[str, Any]
) -> dict[str, Any]:
    del ctx, args
    started = utc_now()
    result = run_contract_tests()
    status = "ok" if result.get("status") == "ok" else "failed"
    return tool_result(
        "validate_data_contracts",
        status=status,
        summary=result,
        error=None
        if status == "ok"
        else {"type": "ContractTestFailure", "message": "Contract tests failed"},
        started_utc=started,
    )


def register_dataset(ctx: AgentToolContext, args: dict[str, Any]) -> dict[str, Any]:
    started = utc_now()
    path = ctx.path_policy.resolve_csv(args["path"])
    request = PiAgentRequest(task="load_dataset", upload_path=str(path))
    request.use_llm_semantics = bool(args.get("use_llm_semantics", True))
    result = load_dataset_for_chat(request, ctx.session)
    summary = ctx.session.get_inspection_summary() or {}
    artifacts = [
        item for item in [result.get("dataset"), summary.get("report_path")] if item
    ]
    warnings = [item for item in [result.get("warning")] if item]
    return tool_result(
        "register_dataset",
        summary=result,
        artifacts=artifacts,
        warnings=warnings,
        started_utc=started,
    )


def generate_dataset_features(
    ctx: AgentToolContext, args: dict[str, Any]
) -> dict[str, Any]:
    del args
    started = utc_now()
    if not ctx.session.get_current_dataset_path():
        raise ValueError("register_dataset must be completed before feature generation")
    quality = ctx.session.get_agent_state("latest_quality_report")
    if not quality:
        raise ValueError(
            "analyze_data_quality must be completed before feature generation"
        )
    result = feature_engineering_engine.run(ctx.session)
    artifacts = [
        item
        for item in [result.get("engineered_file_path"), result.get("registry_path")]
        if item
    ]
    return tool_result(
        "generate_dataset_features",
        summary=result,
        artifacts=artifacts,
        warnings=[
            item.get("message", str(item))
            for item in result.get("validation_issues", [])
        ],
        started_utc=started,
    )


def create_experiment_handoff(
    ctx: AgentToolContext, args: dict[str, Any]
) -> dict[str, Any]:
    del args
    started = utc_now()
    path = ctx.current_path()
    registry_path = ctx.session.get_upload_registry_path()
    if not registry_path or not registry_path.exists():
        raise ValueError(
            "generate_dataset_features must create feature_registry.json before handoff"
        )
    quality = ctx.session.get_agent_state("latest_quality_report")
    if not quality:
        raise ValueError("analyze_data_quality must be completed before handoff")
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    frame = _read_frame(path)
    handoff = experiment_handoff_engine.build_handoff(
        frame, str(path), registry, quality
    )
    report_dir = registry_path.parent
    handoff_path = report_dir / "experiment_handoff.json"
    handoff_path.write_text(
        json.dumps(handoff, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    handoff["handoff_path"] = _rel(handoff_path)
    forbidden = handoff.get("handoff_to_experiment_agent", {}).get(
        "forbidden_inputs", []
    )
    if any(not str(item).startswith("next_cycle_") for item in forbidden):
        warnings = [
            "Review non-next-cycle forbidden inputs before downstream modeling."
        ]
    else:
        warnings = []
    return tool_result(
        "create_experiment_handoff",
        summary=handoff,
        artifacts=[_rel(handoff_path)],
        warnings=warnings,
        started_utc=started,
    )


def rebuild_project_pipeline(
    ctx: AgentToolContext, args: dict[str, Any]
) -> dict[str, Any]:
    del ctx
    started = utc_now()
    workflow = run_full_workflow()
    tests = run_contract_tests() if bool(args.get("run_tests", True)) else None
    status = (
        "ok"
        if workflow.get("status") == "ok"
        and (tests is None or tests.get("status") == "ok")
        else "failed"
    )
    artifacts = [item for item in REQUIRED_OUTPUTS if (ROOT / item).exists()]
    return tool_result(
        "rebuild_project_pipeline",
        status=status,
        summary={"workflow": workflow, "tests": tests},
        artifacts=artifacts,
        error=None
        if status == "ok"
        else {"type": "PipelineFailure", "message": "Pipeline or tests failed"},
        started_utc=started,
    )


def ingest_align_solar_data(
    ctx: AgentToolContext, args: dict[str, Any]
) -> dict[str, Any]:
    started = utc_now()
    paths = [ctx.path_policy.resolve_csv(item) for item in args["paths"]]
    result = skill_workflows.ingest_align_solar_data(
        paths,
        session=ctx.session,
        use_llm_semantics=bool(args.get("use_llm_semantics", True)),
        split_proposal=args.get("split_proposal"),
        resolver=ctx.path_policy.resolve_csv,
    )
    return tool_result(
        "ingest_align_solar_data",
        status="ok" if result.get("status") == "ok" else result.get("status", "failed"),
        summary=result,
        artifacts=result.get("artifacts", []),
        warnings=result.get("warnings", []),
        started_utc=started,
    )


def audit_solar_data_quality(
    ctx: AgentToolContext, args: dict[str, Any]
) -> dict[str, Any]:
    started = utc_now()
    path = ctx.current_path(args.get("path"))
    result = skill_workflows.audit_solar_data(
        path,
        session=ctx.session,
        resolver=ctx.path_policy.resolve_csv,
    )
    return tool_result(
        "audit_solar_data_quality",
        status=result.get("status", "failed"),
        summary=result,
        warnings=[
            issue.get("message", str(issue))
            for issue in result.get("critical_issues", [])
        ],
        error=None
        if result.get("status") == "ok"
        else {"type": "CriticalQualityError", "message": "Quality gate failed"},
        started_utc=started,
    )


def propose_solar_cleaning(
    ctx: AgentToolContext, args: dict[str, Any]
) -> dict[str, Any]:
    started = utc_now()
    path = ctx.current_path(args.get("path"))
    result = skill_workflows.propose_solar_cleaning(
        path,
        session=ctx.session,
        resolver=ctx.path_policy.resolve_csv,
    )
    return tool_result("propose_solar_cleaning", summary=result, started_utc=started)


def apply_solar_cleaning(ctx: AgentToolContext, args: dict[str, Any]) -> dict[str, Any]:
    del args
    started = utc_now()
    result = skill_workflows.apply_solar_cleaning(ctx.session)
    return tool_result(
        "apply_solar_cleaning",
        summary=result,
        artifacts=result.get("artifacts", []),
        started_utc=started,
    )


def engineer_solar_features(
    ctx: AgentToolContext, args: dict[str, Any]
) -> dict[str, Any]:
    started = utc_now()
    requested = args.get("path")
    if requested and not ctx.session.get_current_dataset_path():
        ingestion = skill_workflows.ingest_align_solar_data(
            [ctx.path_policy.resolve_csv(requested)],
            session=ctx.session,
            use_llm_semantics=bool(args.get("use_llm_semantics", True)),
            resolver=ctx.path_policy.resolve_csv,
        )
        if ingestion.get("status") != "ok":
            return tool_result(
                "engineer_solar_features",
                status="failed",
                summary=ingestion,
                started_utc=started,
            )
    current = ctx.current_path()
    audit = skill_workflows.audit_solar_data(
        current, session=ctx.session, resolver=ctx.path_policy.resolve_csv
    )
    if audit.get("status") != "ok":
        return tool_result(
            "engineer_solar_features",
            status="failed",
            summary={"audit": audit},
            error={"type": "CriticalQualityError", "message": "Quality gate failed"},
            started_utc=started,
        )
    result = skill_workflows.engineer_solar_features(ctx.session)
    return tool_result(
        "engineer_solar_features",
        status=result.get("status", "failed"),
        summary=result,
        artifacts=result.get("artifacts", []),
        error=None
        if result.get("status") == "ok"
        else {"type": "FeatureValidationError", "message": "Feature validation failed"},
        started_utc=started,
    )


def prepare_solar_experiment(
    ctx: AgentToolContext, args: dict[str, Any]
) -> dict[str, Any]:
    started = utc_now()
    result = skill_workflows.prepare_experiment_handoff(
        ctx.session,
        include_strategy=bool(args.get("include_strategy", True)),
    )
    return tool_result(
        "prepare_solar_experiment",
        summary=result,
        artifacts=result.get("artifacts", []),
        started_utc=started,
    )


def run_solar_feature_workflow(
    ctx: AgentToolContext, args: dict[str, Any]
) -> dict[str, Any]:
    started = utc_now()
    paths = [ctx.path_policy.resolve_csv(item) for item in args["paths"]]
    result = skill_workflows.run_solar_feature_workflow(
        paths,
        session=ctx.session,
        run_id=args.get("run_id"),
        resume=bool(args.get("resume", False)),
        use_llm_semantics=bool(args.get("use_llm_semantics", True)),
        include_strategy=bool(args.get("include_strategy", True)),
        split_proposal=args.get("split_proposal"),
        preflight_token=args["preflight_token"],
        resolver=ctx.path_policy.resolve_csv,
    )
    status = result.get("status", "failed")
    return tool_result(
        "run_solar_feature_workflow",
        status="ok" if status == "completed" else status,
        summary=result,
        artifacts=result.get("artifacts", []),
        warnings=result.get("warnings", []),
        error=result.get("error"),
        started_utc=started,
    )


def plan_solar_feature_workflow(
    ctx: AgentToolContext, args: dict[str, Any]
) -> dict[str, Any]:
    started = utc_now()
    paths = [ctx.path_policy.resolve_csv(item) for item in args["paths"]]
    result = skill_workflows.plan_solar_feature_workflow(
        paths,
        run_id=args.get("run_id"),
        use_llm_semantics=bool(args.get("use_llm_semantics", True)),
        include_strategy=bool(args.get("include_strategy", True)),
        split_proposal=args.get("split_proposal"),
        resolver=ctx.path_policy.resolve_csv,
    )
    return tool_result(
        "plan_solar_feature_workflow",
        status="ok"
        if result.get("status") == "planned"
        else result.get("status", "failed"),
        summary=result,
        warnings=result.get("warnings", []),
        error=None
        if result.get("status") in {"planned", "confirmation_required"}
        else {
            "type": "CriticalQualityError",
            "message": "Preflight quality gate failed",
        },
        started_utc=started,
    )


def _object_schema(
    properties: dict[str, Any], required: list[str] | None = None
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def build_tool_definitions() -> list[ToolDefinition]:
    path = {
        "type": "string",
        "description": "Existing CSV path within an allowed root.",
    }
    optional_path = {
        "type": "string",
        "description": "Optional CSV path; omit to use the current dataset.",
    }
    paths = {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 8}
    return [
        ToolDefinition(
            "plan_solar_feature_workflow",
            "Read-only preflight for the complete workflow. Audit inputs, propose cleaning, detect splits, fingerprint configuration, and return a token for one approved write call.",
            _object_schema(
                {
                    "paths": paths,
                    "run_id": {"type": "string"},
                    "use_llm_semantics": {"type": "boolean", "default": True},
                    "include_strategy": {"type": "boolean", "default": True},
                    "split_proposal": {"type": "object"},
                },
                ["paths"],
            ),
            False,
            plan_solar_feature_workflow,
        ),
        ToolDefinition(
            "ingest_align_solar_data",
            "Ingest one or more solar CSVs, require confirmation for ambiguous single-column splits, and align multiple sources monthly. Requires approval.",
            _object_schema(
                {
                    "paths": paths,
                    "use_llm_semantics": {"type": "boolean", "default": True},
                    "split_proposal": {"type": "object"},
                },
                ["paths"],
            ),
            True,
            ingest_align_solar_data,
        ),
        ToolDefinition(
            "audit_solar_data_quality",
            "Run the complete read-only solar dataset audit, including deterministic statistics and the quality gate.",
            _object_schema({"path": optional_path}),
            False,
            audit_solar_data_quality,
        ),
        ToolDefinition(
            "propose_solar_cleaning",
            "Propose conservative cleaning actions without writing business data.",
            _object_schema({"path": optional_path}),
            False,
            propose_solar_cleaning,
        ),
        ToolDefinition(
            "apply_solar_cleaning",
            "Apply only approved safe cleaning actions and preserve observed values. Requires approval.",
            _object_schema({}),
            True,
            apply_solar_cleaning,
        ),
        ToolDefinition(
            "engineer_solar_features",
            "Run the quality gate and generate leakage-controlled features plus a feature registry. Requires approval.",
            _object_schema(
                {
                    "path": optional_path,
                    "use_llm_semantics": {"type": "boolean", "default": True},
                }
            ),
            True,
            engineer_solar_features,
        ),
        ToolDefinition(
            "prepare_solar_experiment",
            "Create an experiment handoff and optional rule-based/LLM strategy recommendation. Requires approval.",
            _object_schema({"include_strategy": {"type": "boolean", "default": True}}),
            True,
            prepare_solar_experiment,
        ),
        ToolDefinition(
            "run_solar_feature_workflow",
            "Run the approved ingest-audit-clean-feature-handoff workflow with a manifest and resumable stages. Requires one approval for all writes.",
            _object_schema(
                {
                    "paths": paths,
                    "run_id": {"type": "string"},
                    "resume": {"type": "boolean", "default": False},
                    "use_llm_semantics": {"type": "boolean", "default": True},
                    "include_strategy": {"type": "boolean", "default": True},
                    "split_proposal": {"type": "object"},
                    "preflight_token": {
                        "type": "string",
                        "description": "Token returned by plan_solar_feature_workflow for the same inputs and configuration.",
                    },
                },
                ["paths", "preflight_token"],
            ),
            True,
            run_solar_feature_workflow,
        ),
        ToolDefinition(
            "inspect_dataset",
            "Inspect CSV structure, encoding, columns, samples, and time fields without modifying business data.",
            _object_schema(
                {
                    "path": path,
                    "max_rows": {"type": "integer", "minimum": 1, "maximum": 100000},
                },
                ["path"],
            ),
            False,
            inspect_dataset,
        ),
        ToolDefinition(
            "dataset_statistics",
            "Compute deterministic descriptive statistics for a CSV or the current dataset.",
            _object_schema(
                {
                    "path": optional_path,
                    "action": {
                        "type": "string",
                        "enum": [
                            "describe",
                            "head",
                            "tail",
                            "column_stats",
                            "corr",
                            "value_counts",
                            "groupby",
                            "drift",
                        ],
                    },
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 3,
                    },
                    "n": {"type": "integer", "minimum": 1, "maximum": 20},
                    "aggregation": {
                        "type": "string",
                        "enum": ["mean", "median", "std", "min", "max", "sum", "count"],
                    },
                    "group": {"type": "string"},
                },
                ["action"],
            ),
            False,
            dataset_statistics,
        ),
        ToolDefinition(
            "analyze_data_quality",
            "Analyze missingness, duplicates, time coverage, outliers, and solar-domain constraints without writing a business report.",
            _object_schema({"path": optional_path}),
            False,
            analyze_data_quality,
        ),
        ToolDefinition(
            "validate_data_contracts",
            "Run the repository's deterministic unittest contract suite.",
            _object_schema({}),
            False,
            validate_data_contracts,
        ),
        ToolDefinition(
            "register_dataset",
            "Register a CSV as the session dataset, copying external input and optionally using Bailian for column semantics. Requires approval.",
            _object_schema(
                {
                    "path": path,
                    "use_llm_semantics": {"type": "boolean", "default": True},
                },
                ["path"],
            ),
            True,
            register_dataset,
        ),
        ToolDefinition(
            "generate_dataset_features",
            "Generate leakage-controlled solar features and a feature registry for the registered dataset. Requires approval.",
            _object_schema({}),
            True,
            generate_dataset_features,
        ),
        ToolDefinition(
            "create_experiment_handoff",
            "Write a downstream experiment handoff after quality analysis and feature generation. Requires approval.",
            _object_schema({}),
            True,
            create_experiment_handoff,
        ),
        ToolDefinition(
            "rebuild_project_pipeline",
            "Rebuild all canonical project data products and optionally run tests. Requires approval.",
            _object_schema({"run_tests": {"type": "boolean", "default": True}}),
            True,
            rebuild_project_pipeline,
        ),
    ]


class AgentToolRegistry:
    def __init__(self, path_policy: PathPolicy | None = None) -> None:
        self.path_policy = path_policy or PathPolicy()
        definitions = build_tool_definitions()
        self._tools = {definition.name: definition for definition in definitions}

    def schemas(self) -> list[dict[str, Any]]:
        return [definition.openai_schema() for definition in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    def catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "name": definition.name,
                "description": definition.description,
                "mutates": definition.mutates,
                "parameters": definition.parameters,
            }
            for definition in self._tools.values()
        ]

    def is_mutating(self, name: str) -> bool:
        definition = self._tools.get(name)
        return bool(definition and definition.mutates)

    def execute(
        self, name: str, arguments: dict[str, Any], session: ChatSession
    ) -> dict[str, Any]:
        started = utc_now()
        definition = self._tools.get(name)
        if definition is None:
            return tool_result(
                name,
                status="failed",
                error={"type": "UnknownTool", "message": f"Unknown tool: {name}"},
                started_utc=started,
            )
        try:
            self._validate_arguments(definition.parameters, arguments)
            return definition.handler(
                AgentToolContext(session, self.path_policy), arguments
            )
        except Exception as exc:
            return tool_result(
                name,
                status="failed",
                error={"type": type(exc).__name__, "message": str(exc)},
                started_utc=started,
            )

    @staticmethod
    def _validate_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> None:
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        missing = sorted(required - set(arguments))
        if missing:
            raise ValueError(f"Missing required tool arguments: {missing}")
        if schema.get("additionalProperties") is False:
            extra = sorted(set(arguments) - set(properties))
            if extra:
                raise ValueError(f"Unknown tool arguments: {extra}")
        for key, value in arguments.items():
            rule = properties.get(key, {})
            expected = rule.get("type")
            valid = True
            if expected == "string":
                valid = isinstance(value, str)
            elif expected == "boolean":
                valid = isinstance(value, bool)
            elif expected == "integer":
                valid = isinstance(value, int) and not isinstance(value, bool)
            elif expected == "array":
                valid = isinstance(value, list)
            elif expected == "object":
                valid = isinstance(value, dict)
            if not valid:
                raise ValueError(f"Tool argument {key!r} must be {expected}")
            if "enum" in rule and value not in rule["enum"]:
                raise ValueError(f"Tool argument {key!r} must be one of {rule['enum']}")
            if expected == "integer":
                if "minimum" in rule and value < rule["minimum"]:
                    raise ValueError(
                        f"Tool argument {key!r} is below minimum {rule['minimum']}"
                    )
                if "maximum" in rule and value > rule["maximum"]:
                    raise ValueError(
                        f"Tool argument {key!r} exceeds maximum {rule['maximum']}"
                    )
            if expected == "array":
                if "minItems" in rule and len(value) < rule["minItems"]:
                    raise ValueError(
                        f"Tool argument {key!r} is below minItems {rule['minItems']}"
                    )
                if "maxItems" in rule and len(value) > rule["maxItems"]:
                    raise ValueError(
                        f"Tool argument {key!r} exceeds maxItems {rule['maxItems']}"
                    )
                item_type = rule.get("items", {}).get("type")
                if item_type == "string" and any(
                    not isinstance(item, str) for item in value
                ):
                    raise ValueError(f"Tool argument {key!r} must contain only strings")

    def preview(
        self, name: str, arguments: dict[str, Any], session: ChatSession
    ) -> dict[str, Any]:
        dataset_id = session.get_dataset_id() or "<dataset-id>"
        outputs = {
            "ingest_align_solar_data": [
                "data/uploads/<dataset-id>/",
                "data/processed/uploads/<dataset-id>/",
            ],
            "apply_solar_cleaning": [
                f"data/processed/uploads/{dataset_id}/cleaned_v1.csv",
                f"data/processed/uploads/{dataset_id}/quality_report.json",
            ],
            "engineer_solar_features": [
                f"data/processed/uploads/{dataset_id}/engineered_features.csv",
                f"data/processed/uploads/{dataset_id}/feature_registry.json",
            ],
            "prepare_solar_experiment": [
                f"data/processed/uploads/{dataset_id}/experiment_handoff.json",
                f"data/processed/uploads/{dataset_id}/strategy_recommendation.json",
            ],
            "run_solar_feature_workflow": [
                "data/processed/skill_runs/<run-id>/run_manifest.json",
                "data/uploads/<dataset-id>/",
                "data/processed/uploads/<dataset-id>/",
            ],
            "register_dataset": [
                "data/uploads/<dataset-id>/",
                "data/processed/uploads/<dataset-id>/inspection.json",
            ],
            "generate_dataset_features": [
                f"data/processed/uploads/{dataset_id}/engineered_features.csv",
                f"data/processed/uploads/{dataset_id}/feature_registry.json",
            ],
            "create_experiment_handoff": [
                f"data/processed/uploads/{dataset_id}/experiment_handoff.json"
            ],
            "rebuild_project_pipeline": ["data/processed/"],
        }
        return {
            "tool": name,
            "arguments": arguments,
            "expected_writes": outputs.get(name, []),
        }
