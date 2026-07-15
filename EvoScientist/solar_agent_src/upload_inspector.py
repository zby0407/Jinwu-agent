from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
UPLOAD_REPORT_DIR = ROOT / "data" / "processed" / "uploads"
UPLOAD_RAW_DIR = ROOT / "data" / "uploads"
SUPPORTED_EXTENSIONS = {".csv"}
DEFAULT_MAX_BYTES = 100 * 1024 * 1024

TIME_NAME_TOKENS = (
    "date",
    "datetime",
    "timestamp",
    "time",
    "year",
    "month",
    "day",
    "日期",
    "时间",
    "年月",
    "年",
    "月",
)


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return slug[:80] or "upload"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _detect_encoding(path: Path) -> str:
    sample = path.read_bytes()[:65536]
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            sample.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("CSV encoding is unsupported; use UTF-8, UTF-8 BOM, or GB18030")


def _detect_delimiter(path: Path, encoding: str) -> str:
    sample = path.read_text(encoding=encoding, errors="strict")[:65536]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _normalized_name(name: str) -> str:
    return re.sub(r"[\s_.\-/]+", "", name.strip().lower())


def _has_time_name(name: str) -> bool:
    normalized = _normalized_name(name)
    return any(token in normalized for token in TIME_NAME_TOKENS)


def _parse_time_series(series: pd.Series, name: str) -> tuple[pd.Series, str | None]:
    non_null = series.dropna()
    if non_null.empty:
        return pd.Series(pd.NaT, index=series.index), None

    text = non_null.astype(str).str.strip()
    parsed_non_null: pd.Series
    temporal_kind: str | None = None

    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Could not infer format",
                category=UserWarning,
            )
            warnings.filterwarnings(
                "ignore",
                message="Parsing dates in",
                category=UserWarning,
            )
            if text.str.fullmatch(r"\d{4}").mean() >= 0.9:
                # Only treat 4-digit strings as years if they are within pandas datetime bounds.
                numeric_years = pd.to_numeric(text, errors="coerce")
                if numeric_years.between(1670, 2300).mean() >= 0.9:
                    parsed_non_null = pd.to_datetime(text, format="%Y", errors="coerce")
                    temporal_kind = "year"
                else:
                    parsed_non_null = pd.Series(pd.NaT, index=text.index, dtype="datetime64[ns]")
            elif text.str.fullmatch(r"\d{4}:\d{2}:\d{2}_\d{2}h:\d{2}m:\d{2}s").mean() >= 0.9:
                parsed_non_null = pd.to_datetime(text, format="%Y:%m:%d_%Hh:%Mm:%Ss", errors="coerce")
                temporal_kind = "datetime"
            elif text.str.fullmatch(r"\d{6}").mean() >= 0.9:
                parsed_non_null = pd.to_datetime(text, format="%Y%m", errors="coerce")
                temporal_kind = "year_month"
            elif text.str.fullmatch(r"\d{8}").mean() >= 0.9:
                parsed_non_null = pd.to_datetime(text, format="%Y%m%d", errors="coerce")
                temporal_kind = "date"
            elif pd.api.types.is_numeric_dtype(series) and not _has_time_name(name):
                return pd.Series(pd.NaT, index=series.index), None
            else:
                parsed_non_null = pd.to_datetime(text, errors="coerce")
                temporal_kind = "datetime" if text.str.contains(r"\d:\d", regex=True).any() else "date"
    except (ValueError, AssertionError, TypeError):
        parsed_non_null = pd.Series(pd.NaT, index=text.index, dtype="datetime64[ns]")

    parsed = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")
    try:
        parsed.loc[non_null.index] = parsed_non_null
    except Exception:
        # Defensive: any dtype/overflow problem during assignment is treated as unparseable.
        return pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]"), None
    return parsed, temporal_kind


def _time_candidate(series: pd.Series, name: str) -> dict[str, Any] | None:
    non_null_count = int(series.notna().sum())
    if non_null_count == 0:
        return None
    parsed, temporal_kind = _parse_time_series(series, name)
    parse_ratio = float(parsed.notna().sum() / non_null_count)
    if parse_ratio < 0.6:
        return None

    valid = parsed.dropna()
    name_hint = _has_time_name(name)
    monotonic = bool(valid.is_monotonic_increasing or valid.is_monotonic_decreasing)
    uniqueness_ratio = float(valid.nunique() / len(valid)) if len(valid) else 0.0
    score = min(
        1.0,
        0.65 * parse_ratio
        + (0.2 if name_hint else 0.0)
        + (0.1 if monotonic else 0.0)
        + 0.05 * uniqueness_ratio,
    )
    return {
        "column": name,
        "score": round(score, 4),
        "parse_success_ratio": round(parse_ratio, 4),
        "name_hint": name_hint,
        "monotonic": monotonic,
        "uniqueness_ratio": round(uniqueness_ratio, 4),
        "temporal_kind": temporal_kind,
        "min": valid.min().isoformat() if len(valid) else None,
        "max": valid.max().isoformat() if len(valid) else None,
    }


def _infer_field(series: pd.Series, name: str) -> dict[str, Any]:
    non_null = series.dropna()
    inferred_type = "string"
    if pd.api.types.is_bool_dtype(series):
        inferred_type = "boolean"
    elif pd.api.types.is_integer_dtype(series):
        inferred_type = "integer"
    elif pd.api.types.is_numeric_dtype(series):
        inferred_type = "number"
    else:
        lowered = non_null.astype(str).str.strip().str.lower()
        if len(lowered) and lowered.isin({"true", "false", "yes", "no", "0", "1"}).mean() >= 0.95:
            inferred_type = "boolean"
        else:
            parsed, _ = _parse_time_series(series, name)
            if len(non_null) and parsed.notna().sum() / len(non_null) >= 0.9:
                inferred_type = "datetime"

    samples = [str(value) for value in non_null.head(5).tolist()]
    return {
        "name": name,
        "inferred_type": inferred_type,
        "pandas_dtype": str(series.dtype),
        "non_null_count": int(series.notna().sum()),
        "null_count": int(series.isna().sum()),
        "null_ratio": round(float(series.isna().mean()), 4),
        "unique_count": int(non_null.nunique()) if len(non_null) else 0,
        "samples": samples,
    }


def _find_year_month_composite(frame: pd.DataFrame) -> dict[str, Any] | None:
    normalized = {column: _normalized_name(column) for column in frame.columns}
    year_columns = [
        column for column, name in normalized.items() if name in {"year", "yyyy", "yr", "年", "年份"}
    ]
    month_columns = [
        column for column, name in normalized.items() if name in {"month", "mm", "mon", "月", "月份"}
    ]
    best: dict[str, Any] | None = None
    for year_column in year_columns:
        for month_column in month_columns:
            year = pd.to_numeric(frame[year_column], errors="coerce")
            month = pd.to_numeric(frame[month_column], errors="coerce")
            source_non_null = frame[[year_column, month_column]].notna().all(axis=1)
            source_count = int(source_non_null.sum())
            if source_count == 0:
                continue
            valid_values = year.between(1000, 9999) & month.between(1, 12)
            ratio = float((valid_values & source_non_null).sum() / source_count)
            if ratio < 0.8:
                continue
            dates = pd.to_datetime(
                {"year": year[valid_values].astype(int), "month": month[valid_values].astype(int), "day": 1},
                errors="coerce",
            )
            candidate = {
                "columns": [year_column, month_column],
                "score": round(min(1.0, 0.85 + 0.15 * ratio), 4),
                "parse_success_ratio": round(ratio, 4),
                "temporal_kind": "year_month_composite",
                "min": dates.min().isoformat() if len(dates) else None,
                "max": dates.max().isoformat() if len(dates) else None,
            }
            if best is None or candidate["score"] > best["score"]:
                best = candidate
    return best


def inspect_csv(path: Path, max_rows: int | None = None) -> dict[str, Any]:
    encoding = _detect_encoding(path)
    delimiter = _detect_delimiter(path, encoding)
    frame = pd.read_csv(path, encoding=encoding, sep=delimiter, nrows=max_rows, low_memory=False)
    if not len(frame.columns):
        raise ValueError("CSV has no columns")
    frame.columns = [str(column).strip() for column in frame.columns]
    if pd.Index(frame.columns).duplicated().any():
        duplicates = pd.Index(frame.columns)[pd.Index(frame.columns).duplicated()].tolist()
        raise ValueError(f"CSV contains duplicate column names after trimming: {duplicates}")
    fields = [_infer_field(frame[column], column) for column in frame.columns]
    candidates = [
        candidate
        for column in frame.columns
        if (candidate := _time_candidate(frame[column], column)) is not None
    ]
    candidates.sort(key=lambda item: item["score"], reverse=True)
    composite = _find_year_month_composite(frame)
    primary = candidates[0]["column"] if candidates and candidates[0]["score"] >= 0.75 else None
    primary_columns = [primary] if primary else []
    if composite and (not candidates or composite["score"] >= candidates[0]["score"]):
        primary = None
        primary_columns = composite["columns"]
    warnings = []
    if not primary_columns:
        warnings.append("No high-confidence primary time column was detected.")
    if len(candidates) > 1 and candidates[0]["score"] - candidates[1]["score"] < 0.05:
        warnings.append("Multiple time columns have similar confidence; user confirmation is recommended.")

    return {
        "format": "csv",
        "encoding": encoding,
        "delimiter": "\\t" if delimiter == "\t" else delimiter,
        "rows_read": int(len(frame)),
        "column_count": int(len(frame.columns)),
        "columns": fields,
        "time_detection": {
            "primary_time_column": primary,
            "primary_time_columns": primary_columns,
            "candidates": candidates,
            "composite_candidate": composite,
        },
        "warnings": warnings,
    }


def inspect_uploaded_file(
    upload_path: str | Path,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_rows: int | None = None,
) -> dict[str, Any]:
    path = Path(upload_path).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Uploaded file does not exist: {path}")
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported upload type {path.suffix!r}; supported types: {sorted(SUPPORTED_EXTENSIONS)}"
        )
    size = path.stat().st_size
    if size == 0:
        raise ValueError("Uploaded file is empty")
    if size > max_bytes:
        raise ValueError(f"Uploaded file exceeds the {max_bytes} byte limit")

    checksum = _sha256(path)
    inspection = inspect_csv(path, max_rows=max_rows)
    upload_id = f"{_safe_slug(path.stem)}_{checksum[:12]}"
    raw_dir = UPLOAD_RAW_DIR / upload_id
    raw_dir.mkdir(parents=True, exist_ok=True)
    stored_path = raw_dir / f"source{path.suffix.lower()}"
    if not stored_path.exists():
        shutil.copy2(path, stored_path)
    report = {
        "status": "ok",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "source_file": {
            "name": path.name,
            "absolute_path": str(path),
            "bytes": size,
            "sha256": checksum,
            "stored_path": str(stored_path.relative_to(ROOT)).replace("\\", "/"),
        },
        "inspection": inspection,
    }
    report_dir = UPLOAD_REPORT_DIR / upload_id
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "inspection.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["report_path"] = str(report_path.relative_to(ROOT)).replace("\\", "/")
    return report


def _month_start(series: pd.Series) -> pd.Series:
    """Convert a parsed datetime series to month-start timestamps."""
    parsed = pd.to_datetime(series, errors="coerce")
    return parsed.dt.to_period("M").dt.to_timestamp()


def normalize_time_to_month(
    df: pd.DataFrame, time_columns: list[str] | None = None
) -> pd.DataFrame:
    """Normalize a DataFrame's primary time column(s) to a date_month column (month-start).

    If `time_columns` is not provided, the function inspects the DataFrame for a
    single date column or a year/month composite.
    """
    out = df.copy()
    cols = time_columns

    # If a list of time columns was provided, prefer a year/month composite if
    # both are present; otherwise try to find the first valid date column.
    if cols and len(cols) > 1:
        normalized = {column: _normalized_name(column) for column in out.columns}
        year_col = next(
            (c for c in cols if _normalized_name(c) in {"year", "yyyy", "yr", "年", "年份"}), None
        )
        month_col = next(
            (c for c in cols if _normalized_name(c) in {"month", "mm", "mon", "月", "月份"}), None
        )
        if year_col and month_col and year_col in out.columns and month_col in out.columns:
            cols = [year_col, month_col]
        else:
            # Pick the first column that parses as a date.
            chosen = None
            for c in cols:
                if c not in out.columns:
                    continue
                parsed, _ = _parse_time_series(out[c], c)
                if parsed.notna().sum() / max(out[c].notna().sum(), 1) >= 0.5:
                    chosen = c
                    out[c] = parsed
                    break
            if chosen:
                cols = [chosen]
            else:
                cols = None

    # If a single time column was provided but cannot be parsed as a date
    # (e.g., a numeric fractional year), fall back to a year/month composite.
    if cols and len(cols) == 1:
        date_col = cols[0]
        if date_col not in out.columns:
            cols = None
        else:
            parsed, _ = _parse_time_series(out[date_col], date_col)
            parse_ratio = parsed.notna().sum() / max(out[date_col].notna().sum(), 1)
            if parse_ratio < 0.5:
                normalized = {column: _normalized_name(column) for column in out.columns}
                year_col = next(
                    (c for c, n in normalized.items() if n in {"year", "yyyy", "yr", "年", "年份"}), None
                )
                month_col = next(
                    (c for c, n in normalized.items() if n in {"month", "mm", "mon", "月", "月份"}), None
                )
                if year_col and month_col:
                    cols = [year_col, month_col]

    if not cols:
        for candidate in ["date_month", "date", "datetime", "timestamp", "time"]:
            if candidate in out.columns:
                cols = [candidate]
                break

    if not cols:
        normalized = {column: _normalized_name(column) for column in out.columns}
        year_col = next(
            (c for c, n in normalized.items() if n in {"year", "yyyy", "yr", "年", "年份"}), None
        )
        month_col = next(
            (c for c, n in normalized.items() if n in {"month", "mm", "mon", "月", "月份"}), None
        )
        if year_col and month_col:
            cols = [year_col, month_col]

    if not cols:
        for column in out.columns:
            parsed, _ = _parse_time_series(out[column], column)
            if parsed.notna().sum() / max(out[column].notna().sum(), 1) >= 0.8:
                out[column] = parsed
                cols = [column]
                break

    if not cols:
        raise ValueError("No primary time column or year/month composite found")

    if len(cols) == 2:
        year_col, month_col = cols
        year = pd.to_numeric(out[year_col], errors="coerce")
        month = pd.to_numeric(out[month_col], errors="coerce")
        valid = year.between(1000, 9999) & month.between(1, 12)
        out["date_month"] = pd.NaT
        out.loc[valid, "date_month"] = pd.to_datetime(
            {"year": year[valid].astype(int), "month": month[valid].astype(int), "day": 1},
            errors="coerce",
        )
        out = out.drop(columns=list(cols))
    else:
        date_col = cols[0]
        if date_col not in out.columns:
            raise ValueError(f"Time column {date_col!r} not found")
        out["date_month"] = _month_start(out[date_col])
        if date_col != "date_month":
            out = out.drop(columns=[date_col])

    return out


def source_id_from_summary(summary: dict[str, Any]) -> str:
    """Return a safe source identifier from an inspection summary."""
    dataset_id = summary.get("dataset_id")
    if dataset_id:
        return _safe_slug(str(dataset_id))
    stored = summary.get("stored_path") or summary.get("source_name") or "source"
    return _safe_slug(Path(stored).stem)
