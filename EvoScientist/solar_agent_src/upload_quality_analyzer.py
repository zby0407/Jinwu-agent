from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import data_cleaning_engine
import data_quality_report_text
import cycle_context_summary


ROOT = Path(__file__).resolve().parents[1]

SEVERITY_WEIGHTS = {"critical": 20, "warning": 10, "info": 0}


@dataclass
class QualityIssue:
    type: str
    severity: str
    count: int | None = None
    message: str = ""
    columns: list[str] = field(default_factory=list)
    suggested_action: str = ""
    sample: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "severity": self.severity,
            "count": self.count,
            "message": self.message,
            "columns": self.columns,
            "suggested_action": self.suggested_action,
            "sample": self.sample,
        }


def _detect_time_column(df: pd.DataFrame, inspection: dict[str, Any] | None) -> str | None:
    if inspection:
        primary = inspection.get("primary_time_column")
        if primary and primary in df.columns:
            return primary
    # Fallback: try common names
    for candidate in ["date_month", "date", "datetime", "time", "timestamp"]:
        if candidate in df.columns:
            return candidate
    return None


def _parse_time_column(df: pd.DataFrame, time_col: str) -> pd.Series:
    return pd.to_datetime(df[time_col], errors="coerce")


def _check_duplicate_rows(df: pd.DataFrame) -> QualityIssue | None:
    n_dup = int(df.duplicated().sum())
    if n_dup == 0:
        return None
    return QualityIssue(
        type="duplicate_rows",
        severity="warning",
        count=n_dup,
        message=f"Found {n_dup} exact duplicate rows.",
        suggested_action="remove_exact_duplicates",
    )


def _check_duplicate_timestamps(df: pd.DataFrame, time_col: str | None) -> QualityIssue | None:
    if not time_col:
        return None
    dates = _parse_time_column(df, time_col)
    if dates.isna().all():
        return None
    n_dup = int(dates.duplicated().sum())
    if n_dup == 0:
        return None
    return QualityIssue(
        type="duplicate_timestamp",
        severity="warning",
        count=n_dup,
        message=f"Found {n_dup} duplicate timestamps in '{time_col}'.",
        columns=[time_col],
        suggested_action="aggregate_or_keep_latest",
    )


def _check_time_continuity(df: pd.DataFrame, time_col: str | None) -> QualityIssue | None:
    if not time_col:
        return None
    dates = _parse_time_column(df, time_col).dropna().sort_values()
    if len(dates) < 2:
        return None
    diffs = dates.diff().dropna()
    if diffs.empty:
        return None
    expected_diff = diffs.mode().iloc[0] if not diffs.mode().empty else diffs.median()
    # Allow a small tolerance for expected_diff (e.g., 1 day in ns)
    tolerance = pd.Timedelta("1D")
    gaps = diffs[diffs > expected_diff + tolerance]
    if gaps.empty:
        return None
    gap_ranges = []
    for gap_start in gaps.head(5).index:
        prev = dates.loc[:gap_start].iloc[-2]
        curr = dates.loc[gap_start]
        gap_ranges.append(f"{prev.strftime('%Y-%m-%d')} -> {curr.strftime('%Y-%m-%d')}")
    return QualityIssue(
        type="time_gaps",
        severity="warning",
        count=int(len(gaps)),
        message=f"Found {len(gaps)} time gaps longer than expected interval ({expected_diff}).",
        columns=[time_col],
        suggested_action="investigate_missing_data_or_resample",
        sample=gap_ranges,
    )


def _detect_time_granularity(df: pd.DataFrame, time_col: str | None) -> str | None:
    if not time_col:
        return None
    dates = _parse_time_column(df, time_col).dropna().sort_values()
    if len(dates) < 2:
        return None
    diffs = dates.diff().dropna()
    if diffs.empty:
        return None
    median_diff = diffs.median()
    days = median_diff.total_seconds() / 86400
    if days < 1:
        return "sub_daily"
    if days <= 1.5:
        return "daily"
    if 27 <= days <= 32:
        return "monthly"
    if 360 <= days <= 366:
        return "yearly"
    if days > 366:
        return "irregular"
    # Try weekly / quarterly
    if 6 <= days <= 8:
        return "weekly"
    if 89 <= days <= 93:
        return "quarterly"
    return f"irregular_{int(days)}d"


def _check_missing_values(df: pd.DataFrame) -> tuple[list[QualityIssue], dict[str, Any]]:
    issues = []
    per_column = {}
    for col in df.columns:
        null_count = int(df[col].isna().sum())
        null_ratio = float(df[col].isna().mean())
        per_column[col] = {"null_count": null_count, "null_ratio": round(null_ratio, 4)}
        if null_count > 0:
            # Consecutive missing runs
            if pd.api.types.is_numeric_dtype(df[col]):
                null_series = df[col].isna()
                runs = []
                current = 0
                for value in null_series:
                    if value:
                        current += 1
                    else:
                        if current > 0:
                            runs.append(current)
                            current = 0
                if current > 0:
                    runs.append(current)
                max_run = max(runs) if runs else 0
                if max_run > 1:
                    per_column[col]["max_consecutive_missing"] = max_run

        if null_ratio > 0.5:
            issues.append(
                QualityIssue(
                    type="high_missing_ratio",
                    severity="warning",
                    count=null_count,
                    message=f"Column '{col}' has {null_ratio:.1%} missing values.",
                    columns=[col],
                    suggested_action="impute_or_drop_column",
                )
            )
    return issues, per_column


def _check_constant_columns(df: pd.DataFrame) -> list[QualityIssue]:
    issues = []
    for col in df.columns:
        non_null = df[col].dropna()
        if non_null.empty:
            continue
        n_unique = non_null.nunique()
        if n_unique == 1:
            issues.append(
                QualityIssue(
                    type="constant_column",
                    severity="warning",
                    count=None,
                    message=f"Column '{col}' has only one unique value.",
                    columns=[col],
                    suggested_action="drop_constant_column",
                )
            )
        elif len(non_null) > 100 and n_unique / len(non_null) < 0.01:
            issues.append(
                QualityIssue(
                    type="near_constant_column",
                    severity="info",
                    count=None,
                    message=f"Column '{col}' is near-constant ({n_unique} unique values in {len(non_null)} rows).",
                    columns=[col],
                    suggested_action="review_encoding",
                )
            )
    return issues


def _check_numeric_outliers(df: pd.DataFrame) -> list[QualityIssue]:
    issues = []
    for col in df.columns:
        if not pd.api.types.is_numeric_dtype(df[col]) or pd.api.types.is_bool_dtype(df[col]):
            continue
        non_null = df[col].dropna()
        if len(non_null) < 10:
            continue
        q1 = non_null.quantile(0.25)
        q3 = non_null.quantile(0.75)
        iqr = q3 - q1
        if iqr == 0:
            continue
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        outliers = non_null[(non_null < lower) | (non_null > upper)]
        if outliers.empty:
            continue
        issues.append(
            QualityIssue(
                type="numeric_outliers",
                severity="info",
                count=int(len(outliers)),
                message=f"Column '{col}' has {len(outliers)} IQR outliers.",
                columns=[col],
                suggested_action="review_or_winsorize",
                sample={
                    "lower_bound": float(lower),
                    "upper_bound": float(upper),
                    "outlier_count": int(len(outliers)),
                },
            )
        )
    return issues


def _check_infinite_values(df: pd.DataFrame) -> QualityIssue | None:
    total_inf = 0
    cols = []
    for col in df.columns:
        if not pd.api.types.is_numeric_dtype(df[col]):
            continue
        inf_count = int(np.isinf(df[col]).sum())
        if inf_count > 0:
            total_inf += inf_count
            cols.append(col)
    if total_inf == 0:
        return None
    return QualityIssue(
        type="infinite_values",
        severity="critical",
        count=total_inf,
        message=f"Found {total_inf} infinite values in columns {cols}.",
        columns=cols,
        suggested_action="replace_inf_with_nan",
    )


def _check_category_spelling(df: pd.DataFrame) -> list[QualityIssue]:
    issues = []
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]) or pd.api.types.is_datetime64_any_dtype(df[col]):
            continue
        non_null = df[col].dropna().astype(str)
        if non_null.empty:
            continue
        # Case inconsistencies
        lower_counts = non_null.str.lower().str.strip().value_counts()
        if len(lower_counts) < non_null.nunique():
            # Some values differ only by case or whitespace
            issues.append(
                QualityIssue(
                    type="category_spelling_inconsistency",
                    severity="info",
                    count=None,
                    message=f"Column '{col}' may have case/whitespace variants.",
                    columns=[col],
                    suggested_action="standardize_categories",
                    sample=lower_counts.head(5).to_dict(),
                )
            )
            break  # One info issue is enough
    return issues


def _check_illegal_dates(df: pd.DataFrame, time_col: str | None) -> QualityIssue | None:
    if not time_col:
        return None
    parsed = pd.to_datetime(df[time_col], errors="coerce")
    illegal_count = int(parsed.isna().sum() - df[time_col].isna().sum())
    if illegal_count <= 0:
        return None
    return QualityIssue(
        type="illegal_dates",
        severity="warning",
        count=illegal_count,
        message=f"Column '{time_col}' has {illegal_count} values that could not be parsed as dates.",
        columns=[time_col],
        suggested_action="parse_or_exclude_invalid_dates",
    )


def _check_logical_constraints(df: pd.DataFrame) -> list[QualityIssue]:
    """Check for obvious logical contradictions, e.g., end < start."""
    issues = []
    # Common date pairs
    date_pairs = [("start_date", "end_date"), ("start_date", "peak_date"), ("begin", "end")]
    for start_col, end_col in date_pairs:
        if start_col not in df.columns or end_col not in df.columns:
            continue
        start = pd.to_datetime(df[start_col], errors="coerce")
        end = pd.to_datetime(df[end_col], errors="coerce")
        invalid = (start.notna() & end.notna() & (end < start)).sum()
        if invalid > 0:
            issues.append(
                QualityIssue(
                    type="logical_constraint_violation",
                    severity="critical",
                    count=int(invalid),
                    message=f"Found {invalid} rows where '{end_col}' is earlier than '{start_col}'.",
                    columns=[start_col, end_col],
                    suggested_action="verify_date_columns",
                )
            )
    return issues


def _check_coverage(df: pd.DataFrame, time_col: str | None) -> dict[str, Any]:
    coverage = {
        "rows": int(len(df)),
        "columns": int(len(df.columns)),
        "column_names": list(df.columns),
        "memory_mb": round(df.memory_usage(deep=True).sum() / (1024 * 1024), 2),
    }
    if time_col:
        dates = _parse_time_column(df, time_col).dropna()
        if not dates.empty:
            coverage["time_column"] = time_col
            coverage["time_range"] = {
                "start": dates.min().strftime("%Y-%m-%d"),
                "end": dates.max().strftime("%Y-%m-%d"),
            }
    return coverage


def _compute_quality_score(issues: list[QualityIssue]) -> int:
    score = 100
    for issue in issues:
        weight = SEVERITY_WEIGHTS.get(issue.severity, 0)
        score -= weight
    return max(0, score)


def analyze(df: pd.DataFrame, inspection: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run the full quality analysis on a loaded DataFrame."""
    time_col = _detect_time_column(df, inspection)
    parsed_dates = _parse_time_column(df, time_col) if time_col else None

    issues: list[QualityIssue] = []
    issues.append(_check_duplicate_rows(df))
    issues.append(_check_duplicate_timestamps(df, time_col))
    issues.append(_check_time_continuity(df, time_col))
    issues.append(_check_illegal_dates(df, time_col))
    issues.append(_check_infinite_values(df))
    issues.extend(_check_missing_values(df)[0])
    issues.extend(_check_constant_columns(df))
    issues.extend(_check_numeric_outliers(df))
    issues.extend(_check_category_spelling(df))
    issues.extend(_check_logical_constraints(df))

    issues = [issue for issue in issues if issue is not None]

    missing_issues, missing_per_column = _check_missing_values(df)
    # We already collected missing_issues; merge if needed, but here we only need per_column stats.

    time_granularity = _detect_time_granularity(df, time_col)
    coverage = _check_coverage(df, time_col)
    coverage["time_granularity"] = time_granularity

    quality_score = _compute_quality_score(issues)

    # Conservative cleaning report based on solar-physics domain rules.
    cleaning_report = data_cleaning_engine.generate_report(df)

    return {
        "status": "ok",
        "quality_score": quality_score,
        "severity_counts": {
            "critical": sum(1 for i in issues if i.severity == "critical"),
            "warning": sum(1 for i in issues if i.severity == "warning"),
            "info": sum(1 for i in issues if i.severity == "info"),
        },
        "issues": [issue.to_dict() for issue in issues],
        "coverage": coverage,
        "missing_per_column": missing_per_column,
        "cleaning": cleaning_report,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def run(session: Any) -> dict[str, Any]:
    from chat_session import ChatSession

    if not isinstance(session, ChatSession):
        session = ChatSession()
    path = session.get_current_dataset_path()
    if not path:
        raise ValueError("No current dataset loaded. Use /load <csv_path> first.")
    candidate = Path(path)
    full_path = candidate if candidate.is_absolute() else ROOT / path
    if not full_path.exists():
        raise FileNotFoundError(f"Current dataset not found: {full_path}")
    df = pd.read_csv(full_path)
    df.columns = [str(c).strip() for c in df.columns]
    inspection = session.get_inspection_summary()

    report = analyze(df, inspection)

    # Save to processed/uploads/<id>/quality_report.json
    upload_dir = session.get_upload_registry_path()
    if upload_dir:
        report_dir = upload_dir.parent
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / "quality_report.json"

        # Cycle-context physical feature summary (standard, ML-oriented).
        try:
            report["cycle_context_summary"] = cycle_context_summary.build_cycle_context_summary(
                df, inspection
            )
        except Exception as exc:
            report["cycle_context_summary_error"] = f"{type(exc).__name__}: {exc}"

        # Conservative auto-cleaning based on the quality report.
        try:
            cleaning_report = data_cleaning_engine.run(
                session, apply=True, cleaned_filename="cleaned_auto_v1.csv"
            )
            report["cleaned_file_path"] = cleaning_report.get("cleaned_file_path")
            report["applied_cleaning_actions"] = cleaning_report.get("applied_actions", [])
        except Exception as exc:
            report["auto_cleaning_error"] = f"{type(exc).__name__}: {exc}"

        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["report_path"] = str(report_path.relative_to(ROOT)).replace("\\", "/")
        text_path = report_path.with_suffix(".txt")
        text_path.write_text(
            data_quality_report_text.render_data_quality_report_text(report), encoding="utf-8"
        )
        report["text_path"] = str(text_path.relative_to(ROOT)).replace("\\", "/")

    return report
