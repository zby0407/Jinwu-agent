from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent
from typing import Any

from data_quality_constants import SOLAR_COVERAGE


ROOT = Path(__file__).resolve().parents[1]


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _build_rule_based_recommendation(
    registry: dict[str, Any], quality_report: dict[str, Any]
) -> dict[str, Any]:
    """Generate a conservative, rule-based recommendation when LLM is unavailable."""
    fields = registry.get("fields", [])
    input_features = [f for f in fields if f.get("role") == "input_feature" and f.get("allowed_as_model_input")]
    top_features = [f["field"] for f in input_features[:10]]

    forbidden = [f["field"] for f in fields if f.get("allowed_as_model_input") is False]
    labels = [f["field"] for f in fields if f.get("role") == "label"]

    cleaning = quality_report.get("cleaning", {})
    risks = []
    for finding in cleaning.get("findings", []):
        ftype = finding.get("type")
        if ftype == "label_leakage_risk":
            risks.append("Label leakage risk: exclude next_cycle_* from inputs")
        elif ftype == "before_f107_coverage":
            risks.append(f"F10.7 coverage begins {SOLAR_COVERAGE['f107']['start']}; earlier rows lack proxy")
        elif ftype == "before_polar_coverage":
            risks.append(f"Polar field coverage begins {SOLAR_COVERAGE['polar']['start']}; earlier rows lack proxy")
        elif ftype == "outside_goes_xrs_coverage":
            risks.append(f"GOES XRS limited to {SOLAR_COVERAGE['goes_xrs']['start']} ~ {SOLAR_COVERAGE['goes_xrs']['end']}")

    return {
        "top_features": top_features,
        "splits": [
            {
                "id": "sunspot_only",
                "reason": "Primary evidence across all cycles; safest baseline.",
            },
            {
                "id": "f107_era",
                "reason": "Includes F10.7 proxy from 1947 onward.",
            },
            {
                "id": "wso_era",
                "reason": "Includes polar field and Hale-phase features from 1976 onward.",
            },
            {
                "id": "all_source_overlap",
                "reason": "Homogeneous experiment with all available sources.",
            },
        ],
        "models": [
            "Linear / Ridge regression on lag and rolling-mean features",
            "Random Forest with time-based train/test split",
            "LSTM / Transformer if enough contiguous monthly samples",
        ],
        "risks": risks,
        "next_steps": [
            "Run /handoff to generate experiment_handoff.json",
            "Filter features by evidence tier and coverage flags",
            "Use time-based split, not random split",
            "Carry quality flags into the model as sample weights or filters",
        ],
    }


def _build_llm_prompt(registry: dict[str, Any], quality_report: dict[str, Any]) -> str:
    input_features = [f for f in registry.get("fields", []) if f.get("role") == "input_feature" and f.get("allowed_as_model_input")]
    labels = [f for f in registry.get("fields", []) if f.get("role") == "label"]
    forbidden = [f for f in registry.get("fields", []) if f.get("allowed_as_model_input") is False]
    cleaning = quality_report.get("cleaning", {})

    return dedent(
        f"""
        You are an experiment-design advisor for the Solar-Cycle Co-Scientist data feature workflow.
        Recommend a concrete experiment design based on the following feature registry and quality report.

        Rules you must follow:
        - Do not recommend using any forbidden or label field as an input feature.
        - Respect instrument coverage dates (F10.7 from 1947, WSO from 1976, GOES 1975-2017).
        - Prefer time-based train/test splits over random splits.
        - Mention coverage gaps, leakage risks, and auxiliary vs primary evidence.
        - Do not invent observations, files, or metrics not present in the data.

        Input features ({len(input_features)}):
        {json.dumps([{"field": f["field"], "evidence_tier": f.get("evidence_tier"), "leakage_risk": f.get("leakage_risk")} for f in input_features[:30]], ensure_ascii=False, indent=2)}

        Labels / forbidden inputs:
        {json.dumps([f["field"] for f in labels + forbidden], ensure_ascii=False)}

        Quality / cleaning findings:
        {json.dumps([{"type": g.get("type"), "severity": g.get("severity"), "message": g.get("message")} for g in cleaning.get("findings", [])], ensure_ascii=False, indent=2)}

        Return a JSON object with exactly these keys:
        {{
          "top_features": [list of up to 10 feature names],
          "splits": [list of recommended split ids with reason],
          "models": [list of suitable model types],
          "risks": [list of key risks],
          "next_steps": [list of next actions]
        }}
        """
    ).strip()


def _call_llm(prompt: str) -> str | None:
    try:
        from bailian_llm import call_bailian

        system = (
            "You are an experiment-design advisor for solar-cycle prediction. "
            "Return only the requested JSON object. Do not add markdown explanations outside the JSON."
        )
        return call_bailian(system, prompt)
    except Exception:
        return None


def _parse_json_from_llm(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from the LLM response."""
    if not text:
        return None
    # Try to find JSON in a code block
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    # Try the whole text as JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    return None


def run(session: Any) -> dict[str, Any]:
    """Generate strategy recommendations and save them to the upload directory."""
    from chat_session import ChatSession

    if not isinstance(session, ChatSession):
        session = ChatSession()

    path = session.get_current_dataset_path()
    if not path:
        raise ValueError("No current dataset loaded. Use /load <csv_path> first.")

    upload_dir = session.get_upload_registry_path()
    if not upload_dir:
        raise ValueError("Cannot determine upload directory for recommendation.")
    report_dir = upload_dir.parent

    registry = _load_json(report_dir / "feature_registry.json") or {}
    quality_report = _load_json(report_dir / "quality_report.json") or {}

    rule_based = _build_rule_based_recommendation(registry, quality_report)

    llm_text = _call_llm(_build_llm_prompt(registry, quality_report))
    llm_parsed = _parse_json_from_llm(llm_text) if llm_text else None

    recommendation = {
        "status": "ok",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "llm_available": llm_text is not None,
        "rule_based": rule_based,
        "llm_recommendation": llm_parsed,
        "llm_raw": llm_text,
    }

    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "strategy_recommendation.json"
    json_path.write_text(json.dumps(recommendation, ensure_ascii=False, indent=2), encoding="utf-8")

    markdown_path = report_dir / "strategy_recommendation.md"
    markdown = _render_markdown(recommendation)
    markdown_path.write_text(markdown, encoding="utf-8")

    recommendation["paths"] = {
        "json": str(json_path.relative_to(ROOT)).replace("\\", "/"),
        "markdown": str(markdown_path.relative_to(ROOT)).replace("\\", "/"),
    }
    return recommendation


def _render_markdown(recommendation: dict[str, Any]) -> str:
    lines = ["# Strategy Recommendation", ""]
    lines.append(f"- LLM available: {recommendation.get('llm_available', False)}")
    lines.append("")
    rb = recommendation.get("rule_based", {})
    lines.append("## Top Features")
    for feat in rb.get("top_features", []):
        lines.append(f"- {feat}")
    lines.append("")
    lines.append("## Recommended Splits")
    for split in rb.get("splits", []):
        lines.append(f"- **{split.get('id')}**: {split.get('reason')}")
    lines.append("")
    lines.append("## Suitable Models")
    for model in rb.get("models", []):
        lines.append(f"- {model}")
    lines.append("")
    lines.append("## Risks")
    for risk in rb.get("risks", []):
        lines.append(f"- {risk}")
    lines.append("")
    lines.append("## Next Steps")
    for step in rb.get("next_steps", []):
        lines.append(f"- {step}")
    lines.append("")
    llm = recommendation.get("llm_recommendation")
    if llm:
        lines.append("## LLM Recommendation")
        lines.append(f"```json\n{json.dumps(llm, ensure_ascii=False, indent=2)}\n```")
    return "\n".join(lines)
