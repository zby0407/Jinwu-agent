from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SEMANTIC_ROLES = [
    "sunspot",
    "f107",
    "polar",
    "hemisphere",
    "flare",
    "date",
    "year",
    "month",
    "unknown",
]

EVIDENCE_TIER_MAP: dict[str, str] = {
    "sunspot": "primary",
    "f107": "auxiliary_mechanism_proxy",
    "polar": "auxiliary_mechanism_proxy",
    "hemisphere": "auxiliary_spatial_observation",
    "flare": "auxiliary_event_proxy",
    "date": "metadata",
    "year": "metadata",
    "month": "metadata",
    "unknown": "unverified",
}

EVIDENCE_TIER_NOTE: dict[str, str] = {
    "primary": "主证据，可用于太阳活动周周期级分析",
    "auxiliary_mechanism_proxy": "辅助机制代理，需结合主证据使用，注意跨周期漂移风险",
    "auxiliary_spatial_observation": "辅助空间观测，可用于半球不对称分析",
    "auxiliary_event_proxy": "辅助事件代理，不能替代长期活动周主数据",
    "metadata": "时间或标识元数据",
    "unverified": "未验证字段，使用前需确认物理含义",
}


class LLMUnavailableError(RuntimeError):
    """Raised when the LLM cannot be called or returns no usable response."""


class LLMJsonError(RuntimeError):
    """Raised when the LLM response cannot be parsed as JSON."""


def _build_samples(df: pd.DataFrame, n: int = 5) -> dict[str, list[str]]:
    samples: dict[str, list[str]] = {}
    for col in df.columns:
        non_null = df[col].dropna().head(n).tolist()
        samples[col] = [str(v) for v in non_null]
    return samples


def _extract_json(text: str) -> dict[str, Any]:
    """Extract a JSON object from an LLM response, tolerating Markdown fences."""
    text = text.strip()
    # Markdown code block with json tag
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    # First top-level JSON object
    match = re.search(r"(\{.*\})", text, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    raise LLMJsonError("No JSON object found in LLM response")


def _call_llm_json(system_prompt: str, user_prompt: str) -> dict[str, Any]:
    try:
        from bailian_llm import BailianLLMError, call_bailian

        content = call_bailian(system_prompt, user_prompt)
    except BailianLLMError as exc:
        raise LLMUnavailableError(str(exc)) from exc
    return _extract_json(content)


def _rule_based_recognition(df: pd.DataFrame) -> dict[str, Any]:
    """Fallback semantic recognition using keyword rules."""
    from data_cleaning_engine import infer_column_semantics

    semantics = infer_column_semantics(df)
    semantic_map: dict[str, str] = {}
    for sem, cols in semantics.items():
        for col in cols:
            if col not in semantic_map:
                semantic_map[col] = sem
    for col in df.columns:
        if col not in semantic_map:
            semantic_map[col] = "unknown"

    mappings = [
        {
            "column": c,
            "semantic": s,
            "confidence": "low",
            "reason": "rule-based fallback",
        }
        for c, s in semantic_map.items()
    ]
    return {
        "status": "fallback",
        "llm_used": False,
        "recognition": {"mappings": mappings, "date_format": None, "time_columns": []},
        "semantic_map": semantic_map,
        "date_format": None,
        "time_columns": [],
        "feature_recommendations": [],
        "missing_data_proxy_suggestions": [],
        "evidence_tiers": explain_evidence_tiers(semantic_map),
    }


def recognize_columns(df: pd.DataFrame, n_samples: int = 5) -> dict[str, Any]:
    """Use LLM to classify each column into a solar-physics semantic role."""
    samples = _build_samples(df, n_samples)
    system = (
        "You are a solar physics data classification assistant. "
        "Classify each CSV column into a semantic role based on its name and sample values. "
        f"Available roles: {', '.join(SEMANTIC_ROLES)}. "
        "Return only valid JSON with keys: "
        "mappings (list of {column, semantic, confidence, reason}), date_format, time_columns."
    )
    user = (
        f"Columns and sample values (up to {n_samples} per column):\n"
        f"{json.dumps(samples, ensure_ascii=False, indent=2)}"
    )
    return _call_llm_json(system, user)


def recommend_features(semantic_map: dict[str, str]) -> list[dict[str, Any]]:
    """Ask LLM what solar-cycle features can be built from the available columns."""
    system = (
        "You are a solar physics feature engineering assistant. "
        "Based on the available semantic columns, recommend solar-cycle features and drift indicators. "
        "Return only valid JSON with key: recommendations (list of {feature_name, required_semantics, description, physical_meaning})."
    )
    user = f"Available semantic columns:\n{json.dumps(semantic_map, ensure_ascii=False, indent=2)}"
    result = _call_llm_json(system, user)
    return result.get("recommendations", [])


def suggest_missing_data_proxies(semantic_map: dict[str, str]) -> list[dict[str, Any]]:
    """Ask LLM for alternative proxies when primary/important evidence is missing."""
    system = (
        "You are a solar physics data advisor. "
        "If a primary or important proxy is missing, suggest alternative proxies that could be used and their risks. "
        "Return only valid JSON with key: suggestions (list of {missing, suggested_proxy, risk, note})."
    )
    user = f"Available semantic columns:\n{json.dumps(semantic_map, ensure_ascii=False, indent=2)}"
    result = _call_llm_json(system, user)
    return result.get("suggestions", [])


def explain_evidence_tiers(semantic_map: dict[str, str]) -> dict[str, dict[str, str]]:
    """Return rule-based evidence tier and explanation for each column."""
    explanations: dict[str, dict[str, str]] = {}
    for col, sem in semantic_map.items():
        tier = EVIDENCE_TIER_MAP.get(sem, "unverified")
        explanations[col] = {
            "tier": tier,
            "note": EVIDENCE_TIER_NOTE.get(tier, ""),
        }
    return explanations


def verify_physical_meaning(
    fields: list[dict[str, Any]], seed_path: Path | None = None
) -> dict[str, Any]:
    """Compare field physical meanings against the local canonical seed."""
    seed_path = seed_path or ROOT / "data" / "feature_meaning_seed.json"
    seed: dict[str, Any] = {}
    if seed_path.exists():
        seed = json.loads(seed_path.read_text(encoding="utf-8"))
    verified: list[dict[str, Any]] = []
    for field in fields:
        name = field.get("field", "")
        physical_meaning = field.get("physical_meaning")
        seed_entry = seed.get(name, {})
        seed_meaning = seed_entry.get("physical_meaning")
        issues: list[str] = []
        if not physical_meaning:
            issues.append("字段缺少 physical_meaning")
        if not seed_meaning:
            issues.append("字段未在 feature_meaning_seed.json 中注册")
        elif physical_meaning and physical_meaning != seed_meaning:
            # Simple equality check; could be relaxed with embeddings later.
            issues.append("与种子库物理含义不一致")
        verified.append(
            {
                "field": name,
                "consistent": not issues,
                "issues": issues,
                "seed_meaning": seed_meaning,
                "safer_wording": physical_meaning or seed_meaning,
            }
        )
    return {
        "verified": verified,
        "all_consistent": all(v["consistent"] for v in verified),
    }


def check_wording_risk(text: str) -> dict[str, Any]:
    """Ask LLM to check for causal or deterministic overstatement."""
    system = (
        "You are a scientific writing reviewer. "
        "Check if the following text contains causal inference or deterministic forecast overstatement. "
        "Return only valid JSON with keys: has_risk (bool), risks (list of {type, original, suggestion}), safer_text."
    )
    user = f"Text to review:\n{text}"
    return _call_llm_json(system, user)


def check_physical_plausibility(
    df: pd.DataFrame, semantic_map: dict[str, str]
) -> dict[str, Any]:
    """Ask LLM whether the uploaded data values conflict with known solar physics."""
    stats: dict[str, Any] = {}
    for col, sem in semantic_map.items():
        if sem in {"sunspot", "f107", "polar"} and pd.api.types.is_numeric_dtype(
            df[col]
        ):
            stats[col] = {
                "min": float(df[col].min()) if not df[col].empty else None,
                "max": float(df[col].max()) if not df[col].empty else None,
                "mean": float(df[col].mean()) if not df[col].empty else None,
            }
    system = (
        "You are a solar physics reviewer. "
        "Given the semantic mapping and basic statistics of an uploaded dataset, check if any values appear physically implausible or contradict known solar physics. "
        "Return only valid JSON with keys: consistent (bool), notes (list of strings)."
    )
    user = (
        "Semantic mapping and statistics:\n"
        f"{json.dumps({'semantic_map': semantic_map, 'stats': stats}, ensure_ascii=False, indent=2, default=str)}"
    )
    return _call_llm_json(system, user)


def build_narrative(
    df: pd.DataFrame,
    semantic_map: dict[str, str],
    quality_issues: list[dict[str, Any]],
    plausibility: dict[str, Any],
    proxy_suggestions: list[dict[str, Any]],
) -> str:
    """Generate a human-readable data-quality narrative locally."""
    tiers = explain_evidence_tiers(semantic_map)
    primary = [c for c, t in tiers.items() if t["tier"] == "primary"]
    auxiliary = [c for c, t in tiers.items() if t["tier"].startswith("auxiliary")]
    date_range = ""
    if "date_month" in df.columns and not df["date_month"].empty:
        dates = pd.to_datetime(df["date_month"], errors="coerce")
        if dates.notna().any():
            date_range = (
                f"{dates.min().strftime('%Y-%m')} ~ {dates.max().strftime('%Y-%m')}"
            )
    lines = [
        f"该上传数据共 {len(df)} 行，时间范围 {date_range}。",
        f"主证据字段：{', '.join(primary) if primary else '无'}。",
        f"辅助代理字段：{', '.join(auxiliary) if auxiliary else '无'}。",
    ]
    if quality_issues:
        lines.append(
            f"检测到 {len(quality_issues)} 个数据质量问题："
            + "；".join(i.get("message", str(i)) for i in quality_issues[:3])
        )
    if proxy_suggestions:
        lines.append(
            "缺失数据代理建议："
            + "；".join(s.get("note", str(s)) for s in proxy_suggestions[:3])
        )
    plausibility_notes = plausibility.get("notes") or []
    if plausibility_notes:
        lines.append(
            "物理合理性检查：" + "；".join(str(n) for n in plausibility_notes[:3])
        )
    if not primary:
        lines.append(
            "警告：缺少主证据字段（如太阳黑子数），对太阳活动周机制分析应降低置信度。"
        )
    return " ".join(lines)


def run(
    df: pd.DataFrame,
    use_llm: bool = True,
    n_samples: int = 5,
) -> dict[str, Any]:
    """Run the full LLM-assisted semantic recognition and advisory pipeline."""
    if not use_llm:
        return _rule_based_recognition(df)

    try:
        recognition = recognize_columns(df, n_samples=n_samples)
        mappings = recognition.get("mappings", [])
        semantic_map: dict[str, str] = {}
        for m in mappings:
            col = m.get("column")
            sem = m.get("semantic")
            if col and sem in SEMANTIC_ROLES:
                semantic_map[col] = sem
        # Fill unknown for columns not recognized.
        for col in df.columns:
            if col not in semantic_map:
                semantic_map[col] = "unknown"

        feature_recommendations = recommend_features(semantic_map)
        proxy_suggestions = suggest_missing_data_proxies(semantic_map)
        plausibility = check_physical_plausibility(df, semantic_map)
        wording = check_wording_risk(
            build_narrative(df, semantic_map, [], plausibility, proxy_suggestions)
        )

        return {
            "status": "ok",
            "llm_used": True,
            "recognition": recognition,
            "semantic_map": semantic_map,
            "date_format": recognition.get("date_format"),
            "time_columns": recognition.get("time_columns"),
            "feature_recommendations": feature_recommendations,
            "missing_data_proxy_suggestions": proxy_suggestions,
            "evidence_tiers": explain_evidence_tiers(semantic_map),
            "physical_plausibility": plausibility,
            "wording_risk": wording,
        }
    except (LLMUnavailableError, LLMJsonError) as exc:
        result = _rule_based_recognition(df)
        result["status"] = "llm_unavailable"
        result["llm_error"] = str(exc)
        return result
