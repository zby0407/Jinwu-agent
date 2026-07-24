from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from data_quality_constants import SOLAR_COVERAGE
import data_quality_report_text


ROOT = Path(__file__).resolve().parents[1]

# Default solar-physics coverage rules are imported from the shared constants module
# so that the canonical offline pipeline and the chat workflow use the same dates.
DEFAULT_SOLAR_COVERAGE = SOLAR_COVERAGE

COLUMN_SEMANTIC_PATTERNS: dict[str, list[str]] = {
    # Label leakage must be detected before falling into generic sunspot matching.
    "cycle_label": ["next_cycle_peak", "next_cycle_strength", "next_cycle"],
    "date": ["date", "date_month", "datetime", "timestamp", "time"],
    "f107": ["f107", "f107_adj", "f107_obs", "f107_monthly", "f107_daily"],
    "hemisphere": [
        "hemisphere",
        "hemispheric",
        "north_sunspot",
        "south_sunspot",
        "north_sn",
        "south_sn",
        "north_sunspot_number",
        "south_sunspot_number",
    ],
    "sunspot": ["sunspot", "sn", "spot_number", "silso", "sunspot_number"],
    "polar": ["polar", "polar_north", "polar_south", "polar_mean", "polar_field"],
    "hale": ["hale", "hale_phase", "polar_dipole", "dipole_state"],
    "flare": ["flare", "goes", "xrs", "xray", "xray_peak"],
}


def _normalized(name: str) -> str:
    return name.lower().replace(" ", "_").replace("-", "_")


def infer_column_semantics(
    df: pd.DataFrame,
    overrides: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """Map columns to solar-physics semantic roles by keyword matching.

    `overrides` is a dict of {semantic: column_name} and takes precedence.
    """
    overrides = overrides or {}
    semantics: dict[str, list[str]] = {key: [] for key in COLUMN_SEMANTIC_PATTERNS}
    semantics["unclassified"] = []

    assigned: set[str] = set()
    for semantic, column in overrides.items():
        if column in df.columns and semantic in semantics:
            semantics[semantic].append(column)
            assigned.add(column)

    for col in df.columns:
        if col in assigned:
            continue
        norm = _normalized(col)
        matched = False
        for semantic, patterns in COLUMN_SEMANTIC_PATTERNS.items():
            if any(pattern in norm for pattern in patterns):
                semantics[semantic].append(col)
                matched = True
                break
        if not matched:
            semantics["unclassified"].append(col)
    return semantics


def _load_user_coverage_rules() -> dict[str, dict[str, str]]:
    path = ROOT / "data" / "cleaning_rules.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def get_coverage_rules(
    session_overrides: dict[str, dict[str, str]] | None = None,
) -> dict[str, dict[str, str]]:
    """Return merged coverage rules: defaults < user file < session overrides."""
    rules: dict[str, dict[str, str]] = {
        k: dict(v) for k, v in DEFAULT_SOLAR_COVERAGE.items()
    }
    user_rules = _load_user_coverage_rules()
    for key, values in user_rules.items():
        rules.setdefault(key, {}).update(values)
    if session_overrides:
        for key, values in session_overrides.items():
            rules.setdefault(key, {}).update(values)
    return rules


def _detect_date_column(
    df: pd.DataFrame, semantics: dict[str, list[str]]
) -> str | None:
    for col in semantics.get("date", []):
        if col in df.columns:
            return col
    # Fallback to common date names
    for candidate in ["date_month", "date", "datetime", "time", "timestamp"]:
        if candidate in df.columns:
            return candidate
    return None


def _parse_dates(df: pd.DataFrame, date_col: str | None) -> pd.Series:
    if not date_col or date_col not in df.columns:
        return pd.Series(pd.NaT, index=df.index)
    series = df[date_col].astype(str).str.strip()
    if series.str.fullmatch(r"\d{4}:\d{2}:\d{2}_\d{2}h:\d{2}m:\d{2}s").mean() >= 0.9:
        return pd.to_datetime(series, format="%Y:%m:%d_%Hh:%Mm:%Ss", errors="coerce")
    return pd.to_datetime(df[date_col], errors="coerce")


def _check_coverage(
    df: pd.DataFrame,
    dates: pd.Series,
    semantics: dict[str, list[str]],
    rules: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if dates.isna().all():
        return findings

    def _before(cols: list[str], key: str, start_str: str) -> None:
        if not cols or not start_str:
            return
        start = pd.to_datetime(start_str)
        mask = dates.notna() & (dates < start)
        count = int(mask.sum())
        if count > 0:
            findings.append(
                {
                    "type": f"before_{key}_coverage",
                    "severity": "warning",
                    "columns": cols,
                    "affected_rows": count,
                    "message": f"{count} rows have {key} columns before {start_str} ({key} coverage start)",
                    "suggested_action": "flag_only",
                    "physical_reason": f"{key} data is not available before {start_str}",
                }
            )

    def _outside_range(cols: list[str], key: str, start_str: str, end_str: str) -> None:
        if not cols or not start_str or not end_str:
            return
        start = pd.to_datetime(start_str)
        end = pd.to_datetime(end_str)
        mask = dates.notna() & ((dates < start) | (dates > end))
        count = int(mask.sum())
        if count > 0:
            findings.append(
                {
                    "type": f"outside_{key}_coverage",
                    "severity": "warning",
                    "columns": cols,
                    "affected_rows": count,
                    "message": f"{count} rows have {key} columns outside {start_str} ~ {end_str}",
                    "suggested_action": "flag_only",
                    "physical_reason": f"{key} coverage is limited to {start_str} ~ {end_str}",
                }
            )

    def _external_calibrated_period(cols: list[str]) -> None:
        if not cols:
            return
        start = pd.to_datetime(rules["hemisphere"]["start"])
        end = pd.to_datetime(rules["hemisphere"]["external_calibrated_end"])
        mask = dates.notna() & (dates >= start) & (dates <= end)
        count = int(mask.sum())
        if count > 0:
            findings.append(
                {
                    "type": "hemisphere_external_calibrated_period",
                    "severity": "info",
                    "columns": cols,
                    "affected_rows": count,
                    "message": f"{count} rows fall in 1940-1991 hemisphere external-calibrated-observation period",
                    "suggested_action": "label_source_type",
                    "physical_reason": "Pre-1992 hemispheric data are RGO/NOAA external calibrated observations, not official SILSO",
                }
            )

    # F10.7
    f107_cols = semantics.get("f107", [])
    _before(f107_cols, "f107", rules.get("f107", {}).get("start", ""))

    # Polar / Hale
    polar_cols = semantics.get("polar", []) + semantics.get("hale", [])
    _before(polar_cols, "polar", rules.get("polar", {}).get("start", ""))

    # GOES XRS
    flare_cols = semantics.get("flare", [])
    _outside_range(
        flare_cols,
        "goes_xrs",
        rules.get("goes_xrs", {}).get("start", ""),
        rules.get("goes_xrs", {}).get("end", ""),
    )

    # Hemisphere
    hemisphere_cols = semantics.get("hemisphere", [])
    _before(hemisphere_cols, "hemisphere", rules.get("hemisphere", {}).get("start", ""))
    _external_calibrated_period(hemisphere_cols)

    return findings


def _check_provisional(
    df: pd.DataFrame, semantics: dict[str, list[str]]
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    provisional_cols = [
        c
        for c in df.columns
        if "provisional" in _normalized(c) or "definitive" in _normalized(c)
    ]
    sunspot_cols = semantics.get("sunspot", [])
    if provisional_cols and sunspot_cols:
        # If there is a definitive/provisional flag column, check it
        for col in provisional_cols:
            if "definitive" in _normalized(col):
                is_provisional = df[col].astype(str).str.lower().eq("0") | df[
                    col
                ].astype(str).str.lower().eq("false")
                count = int(is_provisional.sum())
                if count > 0:
                    findings.append(
                        {
                            "type": "provisional_sunspot_months",
                            "severity": "info",
                            "columns": sunspot_cols,
                            "affected_rows": count,
                            "message": f"{count} sunspot rows are marked provisional (definitive=0)",
                            "suggested_action": "flag_provisional",
                            "physical_reason": "Latest SILSO months are provisional and may be revised",
                        }
                    )
            elif "provisional" in _normalized(col):
                is_provisional = df[col].astype(str).str.lower().eq("true") | df[
                    col
                ].astype(str).str.lower().eq("1")
                count = int(is_provisional.sum())
                if count > 0:
                    findings.append(
                        {
                            "type": "provisional_sunspot_months",
                            "severity": "info",
                            "columns": sunspot_cols,
                            "affected_rows": count,
                            "message": f"{count} sunspot rows are marked provisional",
                            "suggested_action": "flag_provisional",
                            "physical_reason": "Latest SILSO months are provisional and may be revised",
                        }
                    )
    return findings


def _check_label_leakage(semantics: dict[str, list[str]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    label_cols = semantics.get("cycle_label", [])
    if label_cols:
        findings.append(
            {
                "type": "label_leakage_risk",
                "severity": "critical",
                "columns": label_cols,
                "affected_rows": None,
                "message": f"Label columns detected: {', '.join(label_cols)}. These are supervised targets and must not be used as model inputs.",
                "suggested_action": "exclude_from_input_features",
                "physical_reason": "Using future-cycle labels as inputs creates target leakage",
            }
        )
    return findings


def _check_logical_dates(df: pd.DataFrame) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    date_pairs = [
        ("start_date", "end_date", "end before start"),
        ("start_date", "peak_date", "peak before start"),
        ("peak_date", "end_date", "end before peak"),
    ]
    for start_col, end_col, description in date_pairs:
        if start_col not in df.columns or end_col not in df.columns:
            continue
        start = pd.to_datetime(df[start_col], errors="coerce")
        end = pd.to_datetime(df[end_col], errors="coerce")
        invalid = (start.notna() & end.notna() & (end < start)).sum()
        if invalid > 0:
            findings.append(
                {
                    "type": "logical_constraint_violation",
                    "severity": "critical",
                    "columns": [start_col, end_col],
                    "affected_rows": int(invalid),
                    "message": f"{invalid} rows have {description} ({end_col} < {start_col})",
                    "suggested_action": "verify_date_columns",
                    "physical_reason": "Solar cycle dates must satisfy start <= peak <= end",
                }
            )
    return findings


def _check_illegal_dates(
    df: pd.DataFrame, date_col: str | None
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not date_col:
        return findings
    parsed = pd.to_datetime(df[date_col], errors="coerce")
    illegal = int(parsed.isna().sum() - df[date_col].isna().sum())
    if illegal > 0:
        findings.append(
            {
                "type": "illegal_dates",
                "severity": "warning",
                "columns": [date_col],
                "affected_rows": illegal,
                "message": f"{illegal} values in '{date_col}' could not be parsed as dates",
                "suggested_action": "parse_or_exclude_invalid_dates",
                "physical_reason": "Date parsing errors prevent time-based analysis",
            }
        )
    return findings


def _check_infinite_values(df: pd.DataFrame) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    inf_cols = []
    total = 0
    for col in df.columns:
        if not pd.api.types.is_numeric_dtype(df[col]):
            continue
        count = int(np.isinf(df[col]).sum())
        if count > 0:
            inf_cols.append(col)
            total += count
    if total > 0:
        findings.append(
            {
                "type": "infinite_values",
                "severity": "warning",
                "columns": inf_cols,
                "affected_rows": total,
                "message": f"{total} infinite values found in columns {inf_cols}",
                "suggested_action": "replace_inf_with_nan",
                "physical_reason": "Infinite values are computational artifacts, not physical measurements",
            }
        )
    return findings


def _check_duplicates(df: pd.DataFrame, date_col: str | None) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    dup_rows = int(df.duplicated().sum())
    if dup_rows > 0:
        findings.append(
            {
                "type": "duplicate_rows",
                "severity": "warning",
                "columns": list(df.columns),
                "affected_rows": dup_rows,
                "message": f"{dup_rows} exact duplicate rows found",
                "suggested_action": "remove_exact_duplicates",
                "physical_reason": "Duplicate rows are redundant and can distort aggregation",
            }
        )
    if date_col:
        dates = pd.to_datetime(df[date_col], errors="coerce")
        dup_ts = int(dates.duplicated().sum())
        if dup_ts > 0:
            findings.append(
                {
                    "type": "duplicate_timestamp",
                    "severity": "warning",
                    "columns": [date_col],
                    "affected_rows": dup_ts,
                    "message": f"{dup_ts} duplicate timestamps found in '{date_col}'",
                    "suggested_action": "aggregate_or_keep_latest",
                    "physical_reason": "Duplicate timestamps can create bias in time-series analysis",
                }
            )
    return findings


def _check_constant_columns(df: pd.DataFrame) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for col in df.columns:
        non_null = df[col].dropna()
        if non_null.empty:
            continue
        if non_null.nunique() == 1:
            findings.append(
                {
                    "type": "constant_column",
                    "severity": "info",
                    "columns": [col],
                    "affected_rows": int(len(df)),
                    "message": f"Column '{col}' is constant",
                    "suggested_action": "review_for_metadata",
                    "physical_reason": "Constant columns provide no information but may be metadata flags",
                }
            )
    return findings


def _propose_safe_actions(
    df: pd.DataFrame, findings: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    dup_rows = int(df.duplicated().sum())
    actions.append(
        {
            "action": "remove_exact_duplicates",
            "applies": dup_rows > 0,
            "affected_rows": dup_rows,
            "description": "Drop exact duplicate rows",
        }
    )

    inf_total = 0
    inf_cols = []
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]):
            count = int(np.isinf(df[col]).sum())
            if count > 0:
                inf_total += count
                inf_cols.append(col)
    actions.append(
        {
            "action": "replace_inf_with_nan",
            "applies": inf_total > 0,
            "affected_cells": inf_total,
            "columns": inf_cols,
            "description": "Replace infinite values with NaN",
        }
    )

    constant_cols = [
        c
        for c in df.columns
        if df[c].dropna().nunique() == 1 and not df[c].dropna().empty
    ]
    actions.append(
        {
            "action": "review_constant_columns",
            "applies": len(constant_cols) > 0,
            "affected_columns": constant_cols,
            "description": "Review constant columns for metadata usefulness",
        }
    )

    return actions


def _do_not_alter_rules() -> list[dict[str, Any]]:
    return [
        {
            "type": "missing_proxy_values",
            "reason": "Missing values often represent pre-instrument era or coverage gaps; imputation would create synthetic observations",
        },
        {
            "type": "statistical_outliers",
            "reason": "Solar extreme values are frequently real physical events; review visually rather than auto-clipping",
        },
        {
            "type": "coverage_outliers",
            "reason": "Data outside instrument coverage should be flagged, not dropped, because the gap is physically meaningful",
        },
    ]


def generate_report(
    df: pd.DataFrame,
    semantics: dict[str, list[str]] | None = None,
    coverage_rules: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Generate a conservative cleaning report without altering the DataFrame."""
    semantics = semantics or infer_column_semantics(df)
    rules = get_coverage_rules(coverage_rules)
    date_col = _detect_date_column(df, semantics)
    dates = _parse_dates(df, date_col)

    findings: list[dict[str, Any]] = []
    findings.extend(_check_duplicates(df, date_col))
    findings.extend(_check_illegal_dates(df, date_col))
    findings.extend(_check_infinite_values(df))
    findings.extend(_check_constant_columns(df))
    findings.extend(_check_logical_dates(df))
    findings.extend(_check_label_leakage(semantics))
    findings.extend(_check_provisional(df, semantics))
    findings.extend(_check_coverage(df, dates, semantics, rules))

    safe_actions = _propose_safe_actions(df, findings)
    do_not_alter = _do_not_alter_rules()

    severity_counts = {"critical": 0, "warning": 0, "info": 0}
    for finding in findings:
        severity_counts[finding["severity"]] = (
            severity_counts.get(finding["severity"], 0) + 1
        )

    return {
        "status": "ok",
        "safe_actions_available": sum(1 for a in safe_actions if a["applies"]),
        "domain_warnings": sum(
            1 for f in findings if f["severity"] in {"warning", "critical"}
        ),
        "do_not_alter_rules": len(do_not_alter),
        "findings": findings,
        "safe_actions": safe_actions,
        "do_not_alter": do_not_alter,
        "domain_constants": rules,
        "column_semantics": {k: v for k, v in semantics.items() if v},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def apply_cleaning(df: pd.DataFrame, report: dict[str, Any]) -> pd.DataFrame:
    """Apply only the safe actions proposed in the report."""
    cleaned = df.copy()
    for action in report.get("safe_actions", []):
        if not action.get("applies"):
            continue
        name = action["action"]
        if name == "remove_exact_duplicates":
            cleaned = cleaned.drop_duplicates().reset_index(drop=True)
        elif name == "replace_inf_with_nan":
            for col in action.get("columns", []):
                if col in cleaned.columns and pd.api.types.is_numeric_dtype(
                    cleaned[col]
                ):
                    cleaned[col] = cleaned[col].replace([np.inf, -np.inf], np.nan)
        elif name == "review_constant_columns":
            # Do not drop; only report. Keeping the column preserves metadata.
            pass
    return cleaned


def run(
    session: Any,
    apply: bool = False,
    cleaned_filename: str = "cleaned_v1.csv",
) -> dict[str, Any]:
    from chat_session import ChatSession

    if not isinstance(session, ChatSession):
        session = ChatSession()

    path = session.get_current_dataset_path()
    if not path:
        raise ValueError("No current dataset loaded. Use /load <csv_path> first.")
    full_path = Path(path) if Path(path).is_absolute() else ROOT / path
    if not full_path.exists():
        raise FileNotFoundError(f"Current dataset not found: {full_path}")

    df = pd.read_csv(full_path)
    df.columns = [str(c).strip() for c in df.columns]

    overrides = session.get_cleaning_column_overrides()
    coverage_overrides = session.get_cleaning_coverage_overrides()
    semantics = infer_column_semantics(df, overrides)
    report = generate_report(df, semantics, coverage_overrides)

    # Merge the cleaning report into the existing quality_report.json if present.
    upload_dir = session.get_upload_registry_path()
    quality_report_path: Path | None = None
    if upload_dir:
        quality_report_path = upload_dir.parent / "quality_report.json"
        if quality_report_path.exists():
            try:
                existing = json.loads(quality_report_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                existing = {}
        else:
            existing = {}
        existing["cleaning"] = report
        quality_report_path.parent.mkdir(parents=True, exist_ok=True)
        quality_report_path.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        report["quality_report_path"] = str(
            quality_report_path.relative_to(ROOT)
        ).replace("\\", "/")
        text_path = quality_report_path.with_suffix(".txt")
        text_path.write_text(
            data_quality_report_text.render_data_quality_report_text(existing),
            encoding="utf-8",
        )
        report["text_path"] = str(text_path.relative_to(ROOT)).replace("\\", "/")

    cleaned_path: Path | None = None
    if apply:
        cleaned = apply_cleaning(df, report)
        if upload_dir:
            save_dir = upload_dir.parent
            save_dir.mkdir(parents=True, exist_ok=True)
            cleaned_path = save_dir / cleaned_filename
            cleaned.to_csv(cleaned_path, index=False, encoding="utf-8")
            report["cleaned_file_path"] = str(cleaned_path.relative_to(ROOT)).replace(
                "\\", "/"
            )
            report["applied_actions"] = [
                a["action"] for a in report.get("safe_actions", []) if a.get("applies")
            ]

            # Auto-load cleaned_v1.csv as the current dataset.
            from piagent_tools import load_dataset_for_chat
            from piagent_schemas import PiAgentRequest

            load_dataset_for_chat(
                PiAgentRequest(task="load_dataset", upload_path=str(cleaned_path)),
                session,
            )

    return report
