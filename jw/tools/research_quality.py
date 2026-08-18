"""Task-local high-quality analysis-claim contract tools for research producers."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from jw.workspaces import workspace_root_from_config
from research_quality.contracts import validate_analysis_claim_contract

from .registry import register_tool_bundle

_STAGES = {"planning", "hypothesis", "experiment_design", "experiment_result"}


def _path(stage: str, config: RunnableConfig | None) -> Path:
    if stage not in _STAGES:
        raise ValueError(f"stage must be one of: {sorted(_STAGES)}")
    return (
        workspace_root_from_config(config)
        / "work"
        / "research_quality"
        / f"{stage}.analysis_claim.json"
    )


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temp = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


@tool(parse_docstring=True)
def research_quality_record_analysis_claim(
    stage: str,
    contract: dict[str, Any] | str,
    config: RunnableConfig = None,
) -> str:
    """Validate and persist the stage's high-quality analysis-claim contract.

    Args:
        stage: planning, hypothesis, experiment_design, or experiment_result.
        contract: AnalysisClaimContractV1 object or JSON string. It fixes the
            estimand, independent sample unit/count, cutoff/information set,
            primary analysis, baseline, validation/decision rule, missingness,
            censoring, revisions, measurement regime, effect/uncertainty,
            sensitivity/influence analysis, and at least two outcome branches.

    Returns:
        A task-local receipt and the normalized contract.
    """

    try:
        raw = json.loads(contract) if isinstance(contract, str) else contract
        normalized = validate_analysis_claim_contract(raw)
        path = _path(stage, config)
        if path.is_file():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing == normalized:
                status = "unchanged"
            else:
                _atomic_write(path, normalized)
                status = "updated"
        else:
            _atomic_write(path, normalized)
            status = "recorded"
        return json.dumps(
            {
                "ok": True,
                "status": status,
                "source_ref": path.relative_to(
                    workspace_root_from_config(config)
                ).as_posix(),
                "contract": normalized,
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        return json.dumps(
            {
                "ok": False,
                "status": "error",
                "error_type": type(exc).__name__,
                "message": str(exc),
            },
            ensure_ascii=False,
        )


@tool(parse_docstring=True)
def research_quality_get_analysis_claim(
    stage: str,
    config: RunnableConfig = None,
) -> str:
    """Read the normalized task-local analysis-claim contract for one stage.

    Args:
        stage: planning, hypothesis, experiment_design, or experiment_result.

    Returns:
        The saved AnalysisClaimContractV1, or an explicit missing status.
    """

    try:
        path = _path(stage, config)
        if not path.is_file():
            return json.dumps({"ok": True, "status": "missing"})
        normalized = validate_analysis_claim_contract(
            json.loads(path.read_text(encoding="utf-8"))
        )
        return json.dumps(
            {"ok": True, "status": "available", "contract": normalized},
            ensure_ascii=False,
        )
    except Exception as exc:
        return json.dumps(
            {
                "ok": False,
                "status": "error",
                "error_type": type(exc).__name__,
                "message": str(exc),
            },
            ensure_ascii=False,
        )


RESEARCH_QUALITY_TOOLS = [
    research_quality_record_analysis_claim,
    research_quality_get_analysis_claim,
]

register_tool_bundle("research-quality", RESEARCH_QUALITY_TOOLS)

__all__ = ["RESEARCH_QUALITY_TOOLS"]
