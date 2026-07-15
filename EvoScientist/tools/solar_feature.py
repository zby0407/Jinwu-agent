"""Solar feature engineering tools for the EvoScientist agent.

Wraps the solar feature agent workflows (audit, feature engineering,
experiment handoff, dataset statistics) as LangChain ``@tool`` functions
so the agent can invoke them during a research conversation.

The underlying workflow code lives in ``solar_agent_src/`` and uses flat
imports (``import chat_session``, etc.).  This module puts that directory
on ``sys.path`` at import time so those imports resolve.
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

from langchain_core.tools import tool

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
    return _to_json({
        "status": "error",
        "tool": tool_name,
        "error_type": type(exc).__name__,
        "error_message": str(exc),
    })


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool(parse_docstring=True)
def audit_solar_data_quality(csv_path: str) -> str:
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
        result = audit_solar_data(csv_path, session=session)
        return _to_json(result)
    except Exception as exc:
        return _error_json("audit_solar_data_quality", exc)


@tool(parse_docstring=True)
def engineer_solar_features(csv_path: str) -> str:
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
        from chat_session import ChatSession
        from solar_feature_agent.workflows import (
            audit_solar_data,
            engineer_solar_features as _engineer,
            ingest_align_solar_data,
        )

        session = ChatSession()
        ingest_align_solar_data([csv_path], session=session)
        audit_solar_data(csv_path, session=session)
        result = _engineer(session)
        return _to_json(result)
    except Exception as exc:
        return _error_json("engineer_solar_features", exc)


@tool(parse_docstring=True)
def prepare_solar_experiment(csv_path: str) -> str:
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
        from chat_session import ChatSession
        from solar_feature_agent.workflows import (
            audit_solar_data,
            engineer_solar_features as _engineer,
            ingest_align_solar_data,
            prepare_experiment_handoff,
        )

        session = ChatSession()
        ingest_align_solar_data([csv_path], session=session)
        audit_solar_data(csv_path, session=session)
        features_result = _engineer(session)
        if features_result.get("status") != "ok":
            return _to_json(features_result)
        result = prepare_experiment_handoff(session)
        return _to_json(result)
    except Exception as exc:
        return _error_json("prepare_solar_experiment", exc)


@tool(parse_docstring=True)
def dataset_statistics(csv_path: str, columns: str = "") -> str:
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

        path = Path(csv_path).resolve()
        inspection = inspect_csv(path)
        session = EphemeralSession()
        session.set_current_dataset(str(path), _inspection_wrapper(path, inspection))
        result = describe(session)

        # Filter to requested columns if specified.
        if columns.strip():
            col_names = {c.strip() for c in columns.split(",") if c.strip()}
            result["columns"] = [
                c for c in result.get("columns", [])
                if c.get("column") in col_names
            ]

        return _to_json(result)
    except Exception as exc:
        return _error_json("dataset_statistics", exc)
