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
import sys
import tempfile
import warnings
from datetime import UTC, datetime
from pathlib import Path

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

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


def _parse_number(value: str) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _build_solar_precursor_cycle_rows(
    sunspot_path: Path, polar_path: Path
) -> list[dict[str, object]]:
    """Build a leakage-explicit cycle table from the curated source formats."""

    import numpy as np
    from scipy.signal import find_peaks

    monthly: list[tuple[int, int, float]] = []
    for line_number, raw in enumerate(
        sunspot_path.read_text(encoding="ascii").splitlines(), start=1
    ):
        fields = raw.split()
        if not fields:
            continue
        if len(fields) not in {6, 7}:
            raise ValueError(f"invalid SILSO row {line_number}")
        year, month, value = int(fields[0]), int(fields[1]), float(fields[3])
        if not 1 <= month <= 12 or value < 0:
            raise ValueError(f"invalid SILSO semantics at row {line_number}")
        monthly.append((year, month, value))
    keys = [(year, month) for year, month, _ in monthly]
    if len(monthly) < 3_200 or keys != sorted(keys) or len(keys) != len(set(keys)):
        raise ValueError("SILSO monthly series is incomplete or non-monotonic")

    values = np.asarray([value for _, _, value in monthly], dtype=float)
    weights = np.ones(13, dtype=float)
    weights[[0, -1]] = 0.5
    weights /= 12.0
    smoothed = np.convolve(values, weights, mode="same")
    smoothed[:6] = np.nan
    smoothed[-6:] = np.nan
    search_indices = np.asarray(
        [
            index
            for index, (year, _month, _value) in enumerate(monthly)
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

    def latest_preminimum(
        hemisphere: str, cutoff: float
    ) -> tuple[float, float, float | None, str] | None:
        eligible = [
            item
            for item in observations[hemisphere]
            if item[0] <= cutoff and cutoff - item[0] <= 1.5
        ]
        return eligible[-1] if eligible else None

    result: list[dict[str, object]] = []
    for ordinal, (start, end) in enumerate(itertools.pairwise(minima)):
        cycle_number = 14 + ordinal
        if not 15 <= cycle_number <= 24:
            continue
        start_year, start_month, _ = monthly[start]
        cutoff = start_year + (start_month - 0.5) / 12.0
        north = latest_preminimum("north", cutoff)
        south = latest_preminimum("south", cutoff)
        if north is None or south is None:
            continue
        peak_offset = int(np.nanargmax(smoothed[start:end]))
        peak_index = start + peak_offset
        peak_year, peak_month, _ = monthly[peak_index]
        north_sem = north[2]
        south_sem = south[2]
        proxy_sem = (
            math.sqrt(north_sem**2 + south_sem**2) / 2
            if north_sem is not None and south_sem is not None
            else None
        )
        result.append(
            {
                "cycle_number": cycle_number,
                "minimum_date": f"{start_year:04d}-{start_month:02d}",
                "minimum_smoothed_sunspot_number": round(float(smoothed[start]), 6),
                "maximum_date": f"{peak_year:04d}-{peak_month:02d}",
                "peak_smoothed_sunspot_number": round(float(smoothed[peak_index]), 6),
                "polar_field_proxy_gauss": round(
                    (abs(north[1]) + abs(south[1])) / 2, 6
                ),
                "polar_field_proxy_sem_gauss": (
                    round(proxy_sem, 6) if proxy_sem is not None else None
                ),
                "north_measurement_date": north[0],
                "north_source": north[3],
                "south_measurement_date": south[0],
                "south_source": south[3],
                "predictor_cutoff_decimal_year": round(cutoff, 6),
            }
        )
    if [row["cycle_number"] for row in result] != list(range(15, 25)):
        raise RuntimeError("curated inputs did not yield complete cycles 15 through 24")
    return result


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool(parse_docstring=True)
def solar_data_open_context(config: RunnableConfig = None) -> str:
    """Open the accepted plan and task-bound data manifest before data work.

    This is the mandatory first action for a closed-loop Data stage. It returns
    only immutable inputs declared by the task workspace; repository samples,
    guessed paths, prior-run outputs, and synthetic fixtures are never promoted
    into the current research run implicitly.

    Args:
        config: Runtime-injected task workspace configuration.

    Returns:
        Hash-bound plan requirements, eligible input records, data route steps,
        planned outputs, and an immutable context receipt path.
    """

    try:
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

        input_manifest_path = root / "input_manifest.json"
        input_manifest = json.loads(input_manifest_path.read_text(encoding="utf-8"))
        if not isinstance(input_manifest, dict):
            raise RuntimeError("task input manifest is not an object")
        eligible_inputs = _eligible_input_records(config)

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
            "task_id": store.task_id,
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
            "input_manifest_sha256": hashlib.sha256(
                input_manifest_path.read_bytes()
            ).hexdigest(),
            "required_datasets": plan.get("required_datasets", []),
            "data_steps": data_steps,
            "planned_outputs": planned_outputs,
            "eligible_inputs": eligible_inputs,
            "status": "inputs_available" if eligible_inputs else "input_missing",
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
                "must_stop": not bool(eligible_inputs),
                "instruction": (
                    "Audit only the returned eligible_inputs and persist their "
                    "verified semantics before engineering features."
                    if eligible_inputs
                    else "No eligible immutable data is bound. Return input_missing "
                    "now; do not search guessed paths or fabricate an output."
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
        receipt = {
            "schema_version": "solar-precursor-cycle-table-v1",
            "status": "verified",
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
                    "mean absolute north/south calibrated polar field; latest "
                    "hemispheric measurement at or before nominal minimum and "
                    "no older than 1.5 years"
                ),
                "target": "maximum centered-smoothed sunspot number before next minimum",
            },
            "row_count": len(rows),
            "cycle_numbers": [row["cycle_number"] for row in rows],
            "limitations": [
                "Centered smoothing is retrospective labeling and confirms a nominal minimum only after a six-month lag.",
                "MWO facular counts are a calibrated proxy, not direct pre-1976 magnetograph measurements.",
                "Ten completed cycles remain a small dependent sample; uncertainty and rolling-origin evaluation are mandatory.",
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
                "limitations": receipt["limitations"],
            }
        )
    except Exception as exc:
        return _error_json("prepare_solar_precursor_cycle_table", exc)


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
    audit_solar_data_quality,
    engineer_solar_features,
    prepare_solar_experiment,
    dataset_statistics,
    prepare_solar_precursor_cycle_table,
    bind_f107_dataset_semantics,
]

register_tool_bundle("solar-features", SOLAR_FEATURE_TOOLS)
