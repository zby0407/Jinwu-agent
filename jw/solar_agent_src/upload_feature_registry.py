from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from feature_physical_meaning import lookup_physical_meaning
from llm_upload_semantic_recognizer import explain_evidence_tiers, verify_physical_meaning


ROOT = Path(__file__).resolve().parents[1]


def _field_records(df: pd.DataFrame, semantic_map: dict[str, str]) -> list[dict[str, Any]]:
    records = []
    for col in df.columns:
        meaning = lookup_physical_meaning(col)
        records.append(
            {
                "field": col,
                "semantic": semantic_map.get(col, "unknown"),
                "physical_meaning": meaning.get("physical_meaning"),
                "mechanism_link": meaning.get("mechanism_link", []),
            }
        )
    return records


def _role_for(semantic: str) -> str:
    if semantic in {"date", "year", "month"}:
        return "identifier"
    if semantic == "cycle_label":
        return "label"
    return "input_feature"


def _leakage_risk(semantic: str, role: str) -> str:
    if role == "label":
        return "forbidden_as_input"
    if role == "identifier":
        return "use_only_for_grouping_or_time_split"
    if semantic == "f107":
        return "low; watch for cross-cycle drift"
    if semantic == "polar":
        return "low; limited sample coverage"
    if semantic == "hemisphere":
        return "low"
    if semantic == "flare":
        return "low; do not treat as long-term primary evidence"
    return "low"


def build_upload_feature_registry(
    df: pd.DataFrame,
    semantic_map: dict[str, str],
    llm_result: dict[str, Any],
    verification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if verification is None:
        verification = verify_physical_meaning(_field_records(df, semantic_map))
    verified_by_field = {v["field"]: v for v in verification.get("verified", [])}
    tiers = explain_evidence_tiers(semantic_map)

    fields: list[dict[str, Any]] = []
    for col in df.columns:
        semantic = semantic_map.get(col, "unknown")
        role = _role_for(semantic)
        meaning = lookup_physical_meaning(col)
        tier = tiers.get(col, {}).get("tier", "unverified")
        verified = verified_by_field.get(col, {})
        fields.append(
            {
                "field": col,
                "semantic": semantic,
                "role": role,
                "allowed_as_model_input": role == "input_feature",
                "leakage_risk": _leakage_risk(semantic, role),
                "evidence_tier": tier,
                "physical_meaning": meaning.get("physical_meaning"),
                "mechanism_link": meaning.get("mechanism_link", []),
                "physical_meaning_verified": verified.get("consistent"),
                "physical_meaning_issues": verified.get("issues", []),
                "note": "",
            }
        )

    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Machine-readable field contract for uploaded dataset.",
        "source": "upload",
        "semantic_map": semantic_map,
        "llm_status": llm_result.get("status"),
        "llm_used": llm_result.get("llm_used", False),
        "fields": fields,
    }


def run(
    df: pd.DataFrame,
    semantic_map: dict[str, str],
    llm_result: dict[str, Any],
    output_path: Path | None = None,
    verification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build and save the feature registry for uploaded data."""
    registry = build_upload_feature_registry(df, semantic_map, llm_result, verification=verification)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
        registry["path"] = str(output_path.relative_to(ROOT)).replace("\\", "/")
    return registry
