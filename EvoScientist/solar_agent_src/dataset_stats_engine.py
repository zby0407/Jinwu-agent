from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
FEATURE_REGISTRY_PATH = ROOT / "data" / "processed" / "feature_registry.json"


def _load_feature_registry(path: Path | None = None) -> dict[str, Any]:
    target = path or FEATURE_REGISTRY_PATH
    if target and target.exists():
        return json.loads(target.read_text(encoding="utf-8"))
    return {}


def _table_name_from_path(path: str) -> str | None:
    """Map a relative dataset path to the table name used in feature_registry.json."""
    name = Path(path).stem
    table_mapping = {
        "clean_monthly_timeseries": "clean_monthly_timeseries",
        "cycle_features": "cycle_features",
        "goes_xrs_monthly_features": "goes_xrs_monthly_features",
        "wso_polar_monthly_features": "wso_polar_monthly_features",
        "cycle_flare_features": "cycle_flare_features",
        "cycle_hale_wso_features": "cycle_hale_wso_features",
        "cycle_hale_wso_sensitivity": "cycle_hale_wso_sensitivity",
    }
    return table_mapping.get(name)


def _field_metadata(
    field: str,
    table_name: str | None,
    dataset_id: str | None,
    global_registry: dict[str, Any],
    upload_registry: dict[str, Any] | None,
) -> dict[str, Any]:
    """Resolve field metadata using dataset-aware scoping.

    Priority:
    1. Upload-specific feature registry (for uploaded datasets)
    2. Global feature registry matched by table + field (for project datasets)
    3. Default unverified metadata for unknown fields
    """
    # 1. Upload registry lookup (dataset-scoped)
    if upload_registry:
        for item in upload_registry.get("fields", []):
            if item.get("field") == field:
                return {
                    "role": item.get("role"),
                    "allowed_as_model_input": item.get("allowed_as_model_input"),
                    "leakage_risk": item.get("leakage_risk"),
                    "evidence_tier": item.get("evidence_tier"),
                    "description": item.get("description") or item.get("note") or "",
                }

    # 2. Global registry lookup (table + field scoped)
    for item in global_registry.get("fields", []):
        if item.get("field") == field and (
            table_name is None or item.get("table") == table_name
        ):
            return {
                "role": item.get("role"),
                "allowed_as_model_input": item.get("allowed_as_model_input"),
                "leakage_risk": item.get("leakage_risk"),
                "evidence_tier": item.get("evidence_tier"),
                "description": item.get("note") or "",
            }

    # 3. Unknown / uploaded field default
    return {
        "role": "candidate_input",
        "allowed_as_model_input": None,
        "leakage_risk": "unverified",
        "evidence_tier": "unverified",
        "description": "Uploaded field; semantic meaning and evidence tier not verified.",
    }


def _load_current_dataset(
    session: Any,
) -> tuple[pd.DataFrame, str | None, str | None, dict[str, Any] | None]:
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
    inspection = session.get_inspection_summary() or {}
    delimiter = str(inspection.get("delimiter") or ",").replace("\\t", "\t")
    df = pd.read_csv(
        full_path,
        encoding=inspection.get("encoding") or "utf-8",
        sep=delimiter,
        low_memory=False,
    )
    df.columns = [str(c).strip() for c in df.columns]
    dataset_id = session.get_dataset_id()
    return df, path, dataset_id, inspection


def _numeric_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]


def _basic_column_info(series: pd.Series, name: str) -> dict[str, Any]:
    non_null = series.dropna()
    info: dict[str, Any] = {
        "name": name,
        "dtype": str(series.dtype),
        "non_null_count": int(series.notna().sum()),
        "null_count": int(series.isna().sum()),
        "null_ratio": round(float(series.isna().mean()), 4),
        "unique_count": int(non_null.nunique()) if len(non_null) else 0,
    }
    if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series) and len(non_null):
        info.update(
            {
                "mean": round(float(non_null.mean()), 4),
                "std": round(float(non_null.std()), 4),
                "min": round(float(non_null.min()), 4),
                "25%": round(float(non_null.quantile(0.25)), 4),
                "50%": round(float(non_null.median()), 4),
                "75%": round(float(non_null.quantile(0.75)), 4),
                "max": round(float(non_null.max()), 4),
            }
        )
    return info


def describe(session: Any) -> dict[str, Any]:
    df, path, dataset_id, inspection = _load_current_dataset(session)
    table_name = _table_name_from_path(path) if path else None
    global_registry = _load_feature_registry()
    upload_registry = session.load_upload_registry() if hasattr(session, "load_upload_registry") else None

    rows, cols = df.shape
    numeric_cols = _numeric_columns(df)

    time_column = None
    if inspection:
        time_column = inspection.get("primary_time_column")
    if not time_column and "date_month" in df.columns:
        time_column = "date_month"

    time_range = None
    if time_column and time_column in df.columns:
        dates = pd.to_datetime(df[time_column], errors="coerce")
        if dates.notna().any():
            time_range = {
                "min": dates.min().strftime("%Y-%m-%d"),
                "max": dates.max().strftime("%Y-%m-%d"),
            }

    column_summaries = []
    for col in df.columns:
        meta = _field_metadata(col, table_name, dataset_id, global_registry, upload_registry)
        summary = {
            "column": col,
            "inferred_type": str(df[col].dtype),
            **_basic_column_info(df[col], col),
            "field_role": meta.get("role"),
            "allowed_as_model_input": meta.get("allowed_as_model_input"),
            "leakage_risk": meta.get("leakage_risk"),
            "evidence_tier": meta.get("evidence_tier"),
            "description": meta.get("description", ""),
        }
        column_summaries.append(summary)

    warnings = []
    if inspection:
        warnings.extend(inspection.get("warnings", []))

    return {
        "status": "ok",
        "action": "describe",
        "dataset": path,
        "dataset_id": dataset_id,
        "table_name": table_name,
        "shape": {"rows": int(rows), "columns": int(cols)},
        "time_column": time_column,
        "time_range": time_range,
        "numeric_columns": numeric_cols,
        "columns": column_summaries,
        "warnings": warnings,
    }


def head(session: Any, n: int = 5) -> dict[str, Any]:
    df, path, dataset_id, _ = _load_current_dataset(session)
    return {
        "status": "ok",
        "action": "head",
        "dataset": path,
        "dataset_id": dataset_id,
        "n": n,
        "data": df.head(n).to_dict(orient="records"),
    }


def tail(session: Any, n: int = 5) -> dict[str, Any]:
    df, path, dataset_id, _ = _load_current_dataset(session)
    return {
        "status": "ok",
        "action": "tail",
        "dataset": path,
        "dataset_id": dataset_id,
        "n": n,
        "data": df.tail(n).to_dict(orient="records"),
    }


def column_stats(session: Any, column: str | None = None) -> dict[str, Any]:
    df, path, dataset_id, _ = _load_current_dataset(session)
    table_name = _table_name_from_path(path) if path else None
    global_registry = _load_feature_registry()
    upload_registry = session.load_upload_registry() if hasattr(session, "load_upload_registry") else None

    if column:
        if column not in df.columns:
            raise ValueError(f"Column not found: {column}. Available columns: {list(df.columns)}")
        series = df[column]
        meta = _field_metadata(column, table_name, dataset_id, global_registry, upload_registry)
        info = _basic_column_info(series, column)
        return {
            "status": "ok",
            "action": "column_stats",
            "dataset": path,
            "dataset_id": dataset_id,
            "column": column,
            "statistics": info,
            "field_role": meta.get("role"),
            "allowed_as_model_input": meta.get("allowed_as_model_input"),
            "leakage_risk": meta.get("leakage_risk"),
            "evidence_tier": meta.get("evidence_tier"),
            "description": meta.get("description", ""),
        }

    # No column specified: summarize all numeric columns
    numeric_cols = _numeric_columns(df)
    summaries = {col: _basic_column_info(df[col], col) for col in numeric_cols}
    return {
        "status": "ok",
        "action": "column_stats",
        "dataset": path,
        "dataset_id": dataset_id,
        "column": None,
        "numeric_columns": numeric_cols,
        "summaries": summaries,
    }


def corr(session: Any, column1: str, column2: str) -> dict[str, Any]:
    df, path, dataset_id, _ = _load_current_dataset(session)
    table_name = _table_name_from_path(path) if path else None
    global_registry = _load_feature_registry()
    upload_registry = session.load_upload_registry() if hasattr(session, "load_upload_registry") else None
    for col in (column1, column2):
        if col not in df.columns:
            raise ValueError(f"Column not found: {col}. Available columns: {list(df.columns)}")
        if not pd.api.types.is_numeric_dtype(df[col]):
            raise ValueError(f"Column is not numeric: {col}")

    valid = df[[column1, column2]].dropna()
    coefficient = float(valid[column1].corr(valid[column2]))
    n = int(len(valid))

    meta1 = _field_metadata(column1, table_name, dataset_id, global_registry, upload_registry)
    meta2 = _field_metadata(column2, table_name, dataset_id, global_registry, upload_registry)
    warnings = []
    if meta1.get("leakage_risk") == "forbidden_as_input" or meta2.get("leakage_risk") == "forbidden_as_input":
        warnings.append(
            "One or both columns are marked as forbidden inputs; this correlation is for understanding only, not for modeling."
        )
    if meta1.get("leakage_risk") == "unverified" or meta2.get("leakage_risk") == "unverified":
        warnings.append(
            "One or both columns are from an uploaded dataset and their semantic meaning is not verified; interpret with caution."
        )

    return {
        "status": "ok",
        "action": "corr",
        "dataset": path,
        "dataset_id": dataset_id,
        "column1": column1,
        "column2": column2,
        "correlation": round(coefficient, 4) if coefficient == coefficient else None,
        "n": n,
        "warnings": warnings,
    }


def value_counts(session: Any, column: str) -> dict[str, Any]:
    df, path, dataset_id, _ = _load_current_dataset(session)
    if column not in df.columns:
        raise ValueError(f"Column not found: {column}. Available columns: {list(df.columns)}")
    counts = df[column].value_counts(dropna=False).head(20)
    return {
        "status": "ok",
        "action": "value_counts",
        "dataset": path,
        "dataset_id": dataset_id,
        "column": column,
        "top_20": [{"value": str(k), "count": int(v)} for k, v in counts.items()],
    }


def groupby(session: Any, column: str, agg: str) -> dict[str, Any]:
    df, path, dataset_id, _ = _load_current_dataset(session)
    if column not in df.columns:
        raise ValueError(f"Column not found: {column}. Available columns: {list(df.columns)}")
    numeric_cols = _numeric_columns(df)
    if agg not in {"mean", "median", "std", "min", "max", "sum", "count"}:
        raise ValueError(f"Unsupported aggregation: {agg}. Supported: mean, median, std, min, max, sum, count")
    if agg == "count":
        result = df.groupby(column).size()
        data = [{column: str(k), "count": int(v)} for k, v in result.items()]
    else:
        agg_fn = getattr(pd.core.groupby.SeriesGroupBy, agg)
        grouped = df.groupby(column)[numeric_cols].agg(agg)
        data = grouped.reset_index().to_dict(orient="records")
    return {
        "status": "ok",
        "action": "groupby",
        "dataset": path,
        "dataset_id": dataset_id,
        "group_column": column,
        "aggregation": agg,
        "data": data,
    }


def drift(session: Any, column1: str, column2: str, group: str | None = None) -> dict[str, Any]:
    df, path, dataset_id, _ = _load_current_dataset(session)
    table_name = _table_name_from_path(path) if path else None
    global_registry = _load_feature_registry()
    upload_registry = session.load_upload_registry() if hasattr(session, "load_upload_registry") else None
    for col in (column1, column2):
        if col not in df.columns:
            raise ValueError(f"Column not found: {col}. Available columns: {list(df.columns)}")
        if not pd.api.types.is_numeric_dtype(df[col]):
            raise ValueError(f"Column is not numeric: {col}")

    if group and group not in df.columns:
        raise ValueError(f"Group column not found: {group}. Available columns: {list(df.columns)}")

    if group:
        grouped = df.groupby(group)
        rows = []
        for name, sub in grouped:
            sub = sub[[column1, column2]].dropna()
            if len(sub) > 1:
                rows.append(
                    {
                        "group": str(name),
                        "n": int(len(sub)),
                        "correlation": round(float(sub[column1].corr(sub[column2])), 4),
                        f"{column1}_mean": round(float(sub[column1].mean()), 4),
                        f"{column2}_mean": round(float(sub[column2].mean()), 4),
                    }
                )
        return {
            "status": "ok",
            "action": "drift",
            "dataset": path,
            "dataset_id": dataset_id,
            "column1": column1,
            "column2": column2,
            "group": group,
            "groups": rows,
        }

    # Overall drift: use rolling window if date_month exists, else single correlation
    if "date_month" in df.columns:
        df = df.copy()
        df["date_month"] = pd.to_datetime(df["date_month"], errors="coerce")
        df["year_window"] = df["date_month"].dt.year // 10 * 10
        return drift(session, column1, column2, group="year_window")

    valid = df[[column1, column2]].dropna()
    coefficient = float(valid[column1].corr(valid[column2]))
    meta1 = _field_metadata(column1, table_name, dataset_id, global_registry, upload_registry)
    meta2 = _field_metadata(column2, table_name, dataset_id, global_registry, upload_registry)
    warnings = []
    if meta1.get("leakage_risk") == "forbidden_as_input" or meta2.get("leakage_risk") == "forbidden_as_input":
        warnings.append(
            "One or both columns are marked as forbidden inputs; this relationship is for understanding only."
        )
    if meta1.get("leakage_risk") == "unverified" or meta2.get("leakage_risk") == "unverified":
        warnings.append(
            "One or both columns are from an uploaded dataset and their semantic meaning is not verified; interpret with caution."
        )
    return {
        "status": "ok",
        "action": "drift",
        "dataset": path,
        "dataset_id": dataset_id,
        "column1": column1,
        "column2": column2,
        "group": None,
        "correlation": round(coefficient, 4),
        "n": int(len(valid)),
        "warnings": warnings,
    }


def run(request: Any, session: Any) -> dict[str, Any]:
    """Dispatch deterministic statistics actions."""
    action = getattr(request, "action", None)
    if not action:
        raise ValueError("dataset_stats requires a non-empty action")

    if action == "describe":
        return describe(session)
    if action == "head":
        return head(session, n=int(request.column or 5))
    if action == "tail":
        return tail(session, n=int(request.column or 5))
    if action == "column_stats":
        return column_stats(session, column=request.column)
    if action == "corr":
        if not request.column:
            raise ValueError("corr requires two columns separated by a space")
        parts = str(request.column).split()
        if len(parts) != 2:
            raise ValueError("corr requires exactly two columns: <col1> <col2>")
        return corr(session, parts[0], parts[1])
    if action == "value_counts":
        if not request.column:
            raise ValueError("value_counts requires a column name")
        return value_counts(session, request.column)
    if action == "groupby":
        if not request.column:
            raise ValueError("groupby requires <column> <aggregation>")
        parts = str(request.column).split()
        if len(parts) != 2:
            raise ValueError("groupby requires <column> <aggregation>")
        return groupby(session, parts[0], parts[1])
    if action == "drift":
        if not request.column:
            raise ValueError("drift requires <column1> <column2> [group]")
        parts = str(request.column).split()
        if len(parts) < 2:
            raise ValueError("drift requires at least two columns: <column1> <column2>")
        return drift(session, parts[0], parts[1], group=parts[2] if len(parts) > 2 else None)

    raise ValueError(f"Unknown dataset_stats action: {action}")
