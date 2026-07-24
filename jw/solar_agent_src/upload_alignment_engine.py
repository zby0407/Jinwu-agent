from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from chat_session import ChatSession
from data_cleaning_engine import infer_column_semantics
from upload_inspector import normalize_time_to_month, source_id_from_summary


ROOT = Path(__file__).resolve().parents[1]


def _read_uploaded_dataset(summary: dict[str, Any]) -> pd.DataFrame:
    stored_path = summary.get("stored_path")
    if not stored_path:
        raise ValueError("uploaded dataset summary missing stored_path")
    path = ROOT / stored_path
    if not path.exists():
        raise FileNotFoundError(f"uploaded dataset not found: {path}")
    inspection = summary.get("inspection") or {}
    delimiter = (inspection.get("delimiter") or ",").replace("\\t", "\t")
    encoding = inspection.get("encoding") or "utf-8"
    df = pd.read_csv(path, encoding=encoding, sep=delimiter, low_memory=False)
    df.columns = [str(c).strip() for c in df.columns]
    if df.columns.duplicated().any():
        duplicates = df.columns[df.columns.duplicated()].tolist()
        raise ValueError(f"duplicate columns in uploaded dataset: {duplicates}")
    return df


def _column_semantic_map(df: pd.DataFrame) -> dict[str, str]:
    semantics = infer_column_semantics(df)
    col_to_semantic: dict[str, str] = {}
    for semantic, columns in semantics.items():
        for col in columns:
            col_to_semantic[col] = semantic
    return col_to_semantic


def _aggregation_for_column(col: str, semantic: str | None) -> str:
    if semantic == "flare" or "count" in col.lower() or "cnt" in col.lower():
        return "sum"
    return "mean"


def _resample_to_month(df: pd.DataFrame, col_semantics: dict[str, str]) -> pd.DataFrame:
    df = df.copy()
    df["date_month"] = pd.to_datetime(df["date_month"], errors="coerce")
    df = df.dropna(subset=["date_month"])
    if df.empty:
        return df
    agg: dict[str, str] = {}
    for col in df.columns:
        if col == "date_month":
            continue
        if pd.api.types.is_numeric_dtype(df[col]):
            agg[col] = _aggregation_for_column(col, col_semantics.get(col))
        else:
            agg[col] = "first"
    grouped = df.groupby(pd.Grouper(key="date_month", freq="MS")).agg(agg)
    grouped = grouped.reset_index()
    return grouped


def _normalize_and_resample(summary: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, str]]:
    df = _read_uploaded_dataset(summary)
    time_columns = summary.get("primary_time_columns") or []
    if not time_columns and summary.get("primary_time_column"):
        time_columns = [summary["primary_time_column"]]
    df = normalize_time_to_month(df, time_columns=time_columns)
    col_semantics = _column_semantic_map(df)
    df = _resample_to_month(df, col_semantics)
    return df, col_semantics


def _rename_conflicting_columns(datasets: list[dict[str, Any]]) -> None:
    all_cols: list[str] = []
    for ds in datasets:
        cols = [c for c in ds["df"].columns if c != "date_month"]
        all_cols.extend(cols)
    counts: dict[str, int] = {}
    for c in all_cols:
        counts[c] = counts.get(c, 0) + 1
    conflicting = {c for c, n in counts.items() if n > 1}
    if not conflicting:
        return
    for ds in datasets:
        df = ds["df"]
        rename: dict[str, str] = {}
        for c in df.columns:
            if c != "date_month" and c in conflicting:
                rename[c] = f"{c}_{ds['source_id']}"
        if rename:
            ds["df"] = df.rename(columns=rename)
            ds["col_semantics"] = {rename.get(k, k): v for k, v in ds["col_semantics"].items()}


def _merge_sources(datasets: list[dict[str, Any]]) -> pd.DataFrame:
    merged: pd.DataFrame | None = None
    for ds in datasets:
        df = ds["df"].copy()
        if merged is None:
            merged = df
        else:
            merged = merged.merge(df, on="date_month", how="outer")
    if merged is None:
        merged = pd.DataFrame(columns=["date_month"])
    merged = merged.sort_values("date_month").reset_index(drop=True)
    return merged


def _build_coverage_flags(merged: pd.DataFrame, datasets: list[dict[str, Any]]) -> pd.DataFrame:
    out = merged.copy()
    for ds in datasets:
        source_id = ds["source_id"]
        source_cols = [c for c in ds["df"].columns if c != "date_month"]
        flag_col = f"is_{source_id}_available"
        out[flag_col] = out[source_cols].notna().any(axis=1) if source_cols else False
    available_cols = [c for c in out.columns if c.startswith("is_") and c.endswith("_available")]
    if available_cols:
        def _flag(row: pd.Series) -> str:
            present = [c[3:-10] for c in available_cols if row[c]]
            if len(present) == len(available_cols):
                return "all"
            if present:
                return "partial:" + "|".join(present)
            return "none"
        out["data_coverage_flag"] = out[available_cols].apply(_flag, axis=1)
    return out


def _build_quality_warnings(merged: pd.DataFrame, datasets: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    if merged.empty:
        warnings.append("对齐结果为空")
        return warnings
    for ds in datasets:
        df = ds["df"]
        if not df.empty:
            start = df["date_month"].min()
            end = df["date_month"].max()
            warnings.append(f"source {ds['source_id']}: {start.strftime('%Y-%m')} ~ {end.strftime('%Y-%m')}")
    non_date = merged.drop(columns=["date_month"], errors="ignore")
    missing = non_date.isna().all(axis=1)
    if missing.any():
        warnings.append(f"{int(missing.sum())} 个月份没有任何来源数据")
    if "data_coverage_flag" in merged.columns:
        partial = merged["data_coverage_flag"].astype(str).str.startswith("partial")
        if partial.any():
            warnings.append(f"{int(partial.sum())} 个月份只有部分来源覆盖")
    return warnings


def run(session: ChatSession, join: str = "outer") -> dict[str, Any]:
    summaries = session.get_uploaded_datasets()
    if len(summaries) < 2:
        raise ValueError("多源对齐需要至少两个已加载数据集，请使用 /load 逐个加载")

    datasets: list[dict[str, Any]] = []
    for summary in summaries:
        source_id = source_id_from_summary(summary)
        df, col_semantics = _normalize_and_resample(summary)
        if df.empty:
            raise ValueError(f"source {source_id} 归一化后为空")
        datasets.append({"source_id": source_id, "df": df, "col_semantics": col_semantics})

    _rename_conflicting_columns(datasets)
    merged = _merge_sources(datasets)
    merged = _build_coverage_flags(merged, datasets)
    warnings = _build_quality_warnings(merged, datasets)

    session_id = session.session_id or "chat_unknown"
    out_dir = ROOT / "data" / "processed" / "uploads" / session_id
    out_dir.mkdir(parents=True, exist_ok=True)
    aligned_path = out_dir / "aligned_uploads.csv"
    report_path = out_dir / "aligned_uploads_report.json"

    merged.to_csv(aligned_path, index=False, encoding="utf-8")

    report = {
        "status": "ok",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "source_count": len(datasets),
        "sources": [
            {
                "source_id": ds["source_id"],
                "columns": [c for c in ds["df"].columns if c != "date_month"],
                "semantics": ds["col_semantics"],
            }
            for ds in datasets
        ],
        "aligned_rows": int(len(merged)),
        "date_range": {
            "start": merged["date_month"].min().isoformat() if not merged.empty else None,
            "end": merged["date_month"].max().isoformat() if not merged.empty else None,
        },
        "warnings": warnings,
        "aligned_path": str(aligned_path.relative_to(ROOT)).replace("\\", "/"),
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    wrapped = {
        "source_file": {
            "name": aligned_path.name,
            "stored_path": str(aligned_path.relative_to(ROOT)).replace("\\", "/"),
            "bytes": aligned_path.stat().st_size,
            "absolute_path": str(aligned_path),
        },
        "inspection": {
            "format": "csv",
            "rows_read": report["aligned_rows"],
            "column_count": int(len(merged.columns)),
            "columns": [{"name": c} for c in merged.columns],
            "time_detection": {
                "primary_time_column": "date_month",
                "primary_time_columns": ["date_month"],
                "warnings": warnings,
            },
        },
        "report_path": str(report_path.relative_to(ROOT)).replace("\\", "/"),
    }
    session.set_aligned_dataset(str(aligned_path.relative_to(ROOT)).replace("\\", "/"), wrapped)

    return {
        "status": "ok",
        "task": "align_uploads",
        "aligned_path": str(aligned_path.relative_to(ROOT)).replace("\\", "/"),
        "report_path": str(report_path.relative_to(ROOT)).replace("\\", "/"),
        "source_count": len(datasets),
        "aligned_rows": report["aligned_rows"],
        "warnings": warnings,
    }
