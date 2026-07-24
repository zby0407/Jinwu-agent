from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd

import data_quality_report_text


ROOT = Path(__file__).resolve().parents[1]

# Ordered by preference for solar-physics data files.
# Chinese comma is included because some regional CSVs use it.
DELIMITER_PATTERNS: dict[str, str] = {
    "semicolon": r";",
    "tab": r"\t",
    "chinese_comma": r"，",
    "comma": r",",
    "pipe": r"\|",
    "whitespace": r"\s+",
}

# Friendly display names for the delimiter keys.
DELIMITER_LABELS: dict[str, str] = {
    "semicolon": ";",
    "tab": "\\t",
    "chinese_comma": "，",
    "comma": ",",
    "pipe": "|",
    "whitespace": "空白字符（空格/制表位）",
}


class LLMUnavailableError(RuntimeError):
    """Raised when the LLM cannot be called or returns no usable response."""


class LLMJsonError(RuntimeError):
    """Raised when the LLM response cannot be parsed as JSON."""


def _extract_json(text: str) -> dict[str, Any]:
    """Extract a JSON object from an LLM response, tolerating Markdown fences."""
    text = text.strip()
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    match = re.search(r"(\{.*\})", text, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    raise LLMJsonError("No JSON object found in LLM response")


def _call_llm_for_split_recognition(
    header: str, sample_rows: list[str]
) -> dict[str, Any]:
    from bailian_llm import BailianLLMError, call_bailian

    system = (
        "You are a CSV parsing assistant for a solar-physics data feature agent. The CSV was read as a single column "
        "because the true delimiter was not recognized. Your task is to identify the delimiter and the column names.\n\n"
        "Return only valid JSON with these exact keys:\n"
        "  delimiter: one of [semicolon, tab, chinese_comma, comma, pipe, whitespace]\n"
        "  first_row_is_header: true if the CSV header row (the first line) contains the field names joined by the delimiter, false otherwise\n"
        "  column_names: list of strings, one per field\n"
        "  time_column: the column name most likely to be a date/time field, or null\n"
        "  notes: optional explanation, or null\n"
        "\nNaming guidance: If the values resemble solar-physics data (e.g., sunspot numbers, hemispheric sunspot data, "
        "F10.7 flux, polar fields, flare indices), use solar-physics-appropriate names. If the context is unclear or the "
        "data is from another domain, generate descriptive generic names.\n"
        "- For WSO polar-field-style data, the timestamp is followed by northern and southern polar field values, "
        "  their mean, a filter label, and the filtered northern/southern/mean values. Use names like "
        "  timestamp, polar_field_north, polar_field_south, polar_field_mean, filter_label, "
        "  polar_field_north_filtered, polar_field_south_filtered, polar_field_mean_filtered.\n"
        "- If values can be negative and have N/S/Avg suffixes, they are polar/hemispheric measurements, not sunspot counts.\n"
        "- If the header is a generic label like 'data' or 'value', set first_row_is_header=false and generate descriptive names."
    )
    user = (
        f"Single-column header string: {header!r}\n\n"
        f"First 10 data cells (each cell may contain multiple fields):\n"
        + "\n".join(f"  {i + 1}: {row!r}" for i, row in enumerate(sample_rows[:10]))
    )
    try:
        content = call_bailian(system, user)
    except BailianLLMError as exc:
        raise LLMUnavailableError(str(exc)) from exc
    try:
        return _extract_json(content)
    except json.JSONDecodeError as exc:
        raise LLMJsonError(str(exc)) from exc


def _split_text(text: str, delimiter: str) -> list[str]:
    pattern = DELIMITER_PATTERNS[delimiter]
    return [part for part in re.split(pattern, str(text)) if part != ""]


def _header_looks_like_field_names(header: str, delimiter: str) -> bool:
    parts = _split_text(header, delimiter)
    if len(parts) < 2:
        return False
    has_letter = any(re.search(r"[a-zA-Z\u4e00-\u9fff]", p) for p in parts)
    all_numeric = all(
        p.replace(".", "", 1).replace("-", "", 1).replace("+", "", 1).isdigit()
        for p in parts
        if p
    )
    return has_letter and not all_numeric


def _normalize_delimiter_alias(value: str | None) -> str:
    if not value:
        return "semicolon"
    lowered = str(value).strip().lower().replace(" ", "_")
    aliases: dict[str, str] = {
        ";": "semicolon",
        "semicolon": "semicolon",
        "tab": "tab",
        "\\t": "tab",
        "t": "tab",
        "，": "chinese_comma",
        "chinese_comma": "chinese_comma",
        ",": "comma",
        "comma": "comma",
        "|": "pipe",
        "pipe": "pipe",
        "whitespace": "whitespace",
        "space": "whitespace",
        "空白": "whitespace",
    }
    return aliases.get(lowered, "semicolon")


def detect_multifield_single_column(df: pd.DataFrame) -> dict[str, Any] | None:
    """Detect whether a single-column DataFrame contains multiple fields per cell.

    Returns a detection dict if the column values are consistently split by a
    delimiter into more than one field; otherwise returns None.
    """
    if len(df.columns) != 1:
        return None
    col = df.columns[0]
    series = df[col].astype(str)
    non_null = series.dropna()
    if len(non_null) == 0:
        return None

    best: dict[str, Any] | None = None
    best_score = 0.0

    for delimiter, pattern in DELIMITER_PATTERNS.items():
        split_counts = non_null.str.split(pattern, regex=True).str.len()
        valid_counts = split_counts.dropna()
        if valid_counts.empty:
            continue

        mode_count = (
            int(valid_counts.mode().iloc[0]) if not valid_counts.mode().empty else 1
        )
        if mode_count <= 1:
            continue

        consistency = float((valid_counts == mode_count).mean())
        if consistency < 0.5:
            continue

        # Prefer more fields when consistent, but require strong consistency.
        score = consistency * (mode_count - 1)
        if score > best_score:
            best_score = score
            best = {
                "delimiter": delimiter,
                "field_count": mode_count,
                "consistency": round(consistency, 4),
            }

    if best is None:
        return None

    # Collect sample splits for display / LLM context.
    pattern = DELIMITER_PATTERNS[best["delimiter"]]
    sample_splits = non_null.head(5).str.split(pattern, regex=True).tolist()

    return {
        "column": str(col),
        "delimiter": best["delimiter"],
        "delimiter_label": DELIMITER_LABELS[best["delimiter"]],
        "field_count": best["field_count"],
        "consistency": best["consistency"],
        "sample_splits": sample_splits,
    }


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}
    return bool(value)


def llm_recognize_split(
    df: pd.DataFrame,
    detection: dict[str, Any],
    use_llm: bool = True,
) -> dict[str, Any]:
    """Use the LLM to confirm/propose delimiter and column names for a split.

    Falls back to rule-based heuristics if the LLM is unavailable or disabled.
    """
    col = df.columns[0]
    header = str(col)
    series = df[col].astype(str)
    non_null = series.dropna()
    sample_rows = non_null.head(10).tolist()

    if use_llm:
        try:
            llm_result = _call_llm_for_split_recognition(header, sample_rows)
            delimiter = _normalize_delimiter_alias(llm_result.get("delimiter"))
            first_row_is_header = _normalize_bool(
                llm_result.get("first_row_is_header", False)
            )
            column_names = llm_result.get("column_names") or []
            time_column = llm_result.get("time_column")
            notes = llm_result.get("notes")
        except (LLMUnavailableError, LLMJsonError):
            use_llm = False
            delimiter = detection["delimiter"]
            first_row_is_header = _header_looks_like_field_names(header, delimiter)
            column_names = []
            time_column = None
            notes = "LLM unavailable; using rule-based fallback."
    else:
        delimiter = detection["delimiter"]
        first_row_is_header = _header_looks_like_field_names(header, delimiter)
        column_names = []
        time_column = None
        notes = "LLM disabled; using rule-based fallback."

    if first_row_is_header:
        derived = _split_text(header, delimiter)
        if not column_names:
            column_names = derived
        elif len(derived) == len(column_names) and all(c.strip() for c in derived):
            # Keep LLM names unless the header clearly matches the data.
            column_names = [
                d if d.strip() else c for d, c in zip(derived, column_names)
            ]

    if not column_names:
        column_names = [f"field_{i + 1}" for i in range(detection["field_count"])]

    if len(column_names) < detection["field_count"]:
        column_names = column_names + [
            f"field_{i + 1}" for i in range(len(column_names), detection["field_count"])
        ]

    # Deduplicate while preserving order.
    seen: set[str] = set()
    unique_names: list[str] = []
    for name in column_names:
        safe = re.sub(r"[^\w\u4e00-\u9fff]+", "_", str(name)).strip("_") or "field"
        if safe in seen:
            safe = f"{safe}_{len(seen) + 1}"
        seen.add(safe)
        unique_names.append(safe[:64])

    confidence_score = min(
        1.0,
        detection["consistency"] * 0.5
        + (0.3 if use_llm else 0.15)
        + (0.2 if time_column else 0.0),
    )

    return {
        "delimiter": delimiter,
        "delimiter_label": DELIMITER_LABELS[delimiter],
        "first_row_is_header": first_row_is_header,
        "column_names": unique_names[: detection["field_count"]],
        "field_count": detection["field_count"],
        "time_column": time_column,
        "notes": notes,
        "confidence_score": round(confidence_score, 4),
        "auto_decision": confidence_score >= 0.9,
    }


def split_multifield_column(df: pd.DataFrame, proposal: dict[str, Any]) -> pd.DataFrame:
    """Split a single-column DataFrame into a multi-column long table."""
    if len(df.columns) != 1:
        return df.copy()
    col = df.columns[0]
    delimiter = proposal["delimiter"]
    pattern = DELIMITER_PATTERNS[delimiter]
    series = df[col].astype(str)
    split_df = series.str.split(pattern, regex=True, expand=True)
    column_names = list(proposal.get("column_names", []))
    if len(column_names) < len(split_df.columns):
        column_names = column_names + [
            f"field_{i + 1}" for i in range(len(column_names), len(split_df.columns))
        ]
    split_df.columns = column_names[: len(split_df.columns)]
    return split_df


WSO_TIME_PATTERN = re.compile(r"^(\d{4}):(\d{2}):(\d{2})_(\d{2})h:(\d{2})m:(\d{2})s$")

WSO_VALUE_SUFFIX_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^([+-]?\d+(?:\.\d+)?)Nf?$"),
    re.compile(r"^([+-]?\d+(?:\.\d+)?)Sf?$"),
    re.compile(r"^([+-]?\d+(?:\.\d+)?)Avgf?$"),
]


def _extract_numeric_from_suffixed_values(
    df: pd.DataFrame, min_ratio: float = 0.9
) -> pd.DataFrame:
    """Strip WSO-style suffixes (N/S/Avg and their filtered variants) from string columns.

    Only converts a column if at least ``min_ratio`` of its non-null values match
    one of the known patterns. This preserves label columns such as "20nhz" or
    "filt:" that happen to start with digits but are not physical measurements.
    """
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_numeric_dtype(out[col]):
            continue
        series = out[col].astype(str).str.strip()
        non_null = series.dropna()
        if non_null.empty:
            continue
        for pattern in WSO_VALUE_SUFFIX_PATTERNS:
            matched = non_null.str.match(pattern).sum()
            if matched / len(non_null) >= min_ratio:
                extracted = series.str.extract(pattern)[0]
                out[col] = pd.to_numeric(extracted, errors="coerce")
                break
    return out


def split_embedded_time_fields(df: pd.DataFrame) -> pd.DataFrame:
    """If any column matches the WSO format YYYY:MM:DD_hh:mm:ss, split it into
    year, month, day, and time_of_day.
    """
    time_col = None
    for col in df.columns:
        series = df[col].astype(str).str.strip()
        non_null = series.dropna()
        if non_null.empty:
            continue
        matched = non_null.str.match(WSO_TIME_PATTERN).sum()
        if matched / len(non_null) >= 0.9:
            time_col = col
            break

    if time_col is None:
        return df.copy()

    out = df.copy()
    extracted = out[time_col].astype(str).str.strip().str.extract(WSO_TIME_PATTERN)
    if extracted.isna().all().all():
        return out

    out["year"] = pd.to_numeric(extracted[0], errors="coerce", downcast="integer")
    out["month"] = pd.to_numeric(extracted[1], errors="coerce", downcast="integer")
    out["day"] = pd.to_numeric(extracted[2], errors="coerce", downcast="integer")
    out["time_of_day"] = extracted[3] + "h:" + extracted[4] + "m:" + extracted[5] + "s"
    out = out.drop(columns=[time_col])

    # Reorder so that time-derived fields appear first.
    leading = ["year", "month", "day", "time_of_day"]
    other = [c for c in out.columns if c not in leading]
    return out[leading + other]


def _build_inspection_for_split(path: Path, df: pd.DataFrame) -> dict[str, Any]:
    from upload_inspector import inspect_csv

    inspection = inspect_csv(path)
    return {
        "source_file": {
            "name": path.name,
            "stored_path": str(path.relative_to(ROOT)).replace("\\", "/"),
            "bytes": path.stat().st_size,
            "absolute_path": str(path),
        },
        "inspection": inspection,
    }


def apply_split(
    session: Any,
    proposal: dict[str, Any],
    *,
    run_quality: bool = False,
    run_features: bool = False,
) -> dict[str, Any]:
    """Apply a multi-field split proposal to the current dataset.

    Saves the resulting long table as the current dataset in the session and
    optionally runs quality analysis and/or the standard feature pipeline.
    """
    from chat_session import ChatSession

    if not isinstance(session, ChatSession):
        session = ChatSession()

    path = session.get_current_dataset_path()
    if not path:
        raise ValueError("No current dataset loaded. Use /load <csv_path> first.")
    full_path = Path(path) if Path(path).is_absolute() else ROOT / path
    if not full_path.exists():
        raise FileNotFoundError(f"Current dataset not found: {full_path}")

    # Read the raw file as a single-column DataFrame. This avoids the
    # ambiguity caused by pandas default header inference on malformed CSVs.
    try:
        df = pd.read_csv(full_path, header=None, names=["raw_column"], encoding="utf-8")
    except UnicodeDecodeError:
        df = pd.read_csv(
            full_path, header=None, names=["raw_column"], encoding="gb18030"
        )
    df.columns = [str(c).strip() for c in df.columns]

    # If the original file had a real header row, drop it from the data.
    if proposal.get("first_row_is_header", False):
        df = df.iloc[1:].reset_index(drop=True)

    if df.empty:
        raise ValueError(
            f"After applying first_row_is_header, the dataset is empty. "
            f"Original file: {full_path}. If the first row is not a header, "
            f"use first_row_is_header=false."
        )

    split_df = split_multifield_column(df, proposal)
    split_df = split_embedded_time_fields(split_df)
    split_df = _extract_numeric_from_suffixed_values(split_df)

    # Save the long table alongside the original source file.
    dataset_id = session.get_dataset_id()
    if dataset_id:
        out_dir = ROOT / "data" / "uploads" / dataset_id
    else:
        out_dir = full_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    long_table_path = out_dir / "long_table.csv"
    split_df.to_csv(long_table_path, index=False, encoding="utf-8")

    # Update session to point to the long table.
    wrapped = _build_inspection_for_split(long_table_path, split_df)
    session.set_current_dataset(
        str(long_table_path.relative_to(ROOT)).replace("\\", "/"),
        wrapped,
    )

    try:
        original_rel = str(full_path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        original_rel = str(full_path)

    result: dict[str, Any] = {
        "status": "ok",
        "task": "apply_multifield_split",
        "original_path": original_rel,
        "long_table_path": str(long_table_path.relative_to(ROOT)).replace("\\", "/"),
        "rows": int(len(split_df)),
        "columns": list(split_df.columns),
        "delimiter": proposal.get("delimiter"),
        "delimiter_label": proposal.get("delimiter_label"),
        "first_row_is_header": proposal.get("first_row_is_header", False),
        "column_names": list(split_df.columns),
    }

    if run_quality:
        import upload_quality_analyzer

        quality_report = upload_quality_analyzer.run(session)
        quality_report["split_provenance"] = {
            "original_path": original_rel,
            "delimiter": proposal.get("delimiter"),
            "delimiter_label": proposal.get("delimiter_label"),
            "first_row_is_header": proposal.get("first_row_is_header", False),
            "column_names_before_split": [str(c) for c in df.columns],
            "column_names_after_split": list(split_df.columns),
        }
        if quality_report.get("report_path"):
            report_path = ROOT / quality_report["report_path"]
            report_path.write_text(
                json.dumps(quality_report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            text_path = report_path.with_suffix(".txt")
            text_path.write_text(
                data_quality_report_text.render_data_quality_report_text(
                    quality_report
                ),
                encoding="utf-8",
            )
        result["quality_report"] = {
            "quality_score": quality_report.get("quality_score"),
            "report_path": quality_report.get("report_path"),
            "text_path": quality_report.get("text_path"),
            "cleaned_file_path": quality_report.get("cleaned_file_path"),
            "issues_count": len(quality_report.get("issues", [])),
        }
        result["quality_report_path"] = quality_report.get("report_path")
        result["text_path"] = quality_report.get("text_path")

    if run_features:
        import upload_data_feature_pipeline

        feature_result = upload_data_feature_pipeline.run(session, use_llm=True)
        result["feature_result"] = {
            "status": feature_result.get("status"),
            "paths": feature_result.get("paths"),
            "warnings": feature_result.get("warnings"),
        }
        result["feature_paths"] = feature_result.get("paths")

    return result
