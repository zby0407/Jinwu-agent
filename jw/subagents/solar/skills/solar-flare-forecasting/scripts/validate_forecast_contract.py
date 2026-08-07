#!/usr/bin/env python3
"""Validate a bounded solar-flare forecast task contract."""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "solar-flare-forecast-task-v1"
FORECAST_MODES = {"research_backtest", "simulated_operational", "live"}
SPATIAL_UNITS = {"full_disk", "active_region"}
THRESHOLD = re.compile(r"^[CMX](?:\d+(?:\.\d+)?)\+$")


def _time(value: Any, field: str, issues: list[str]) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        issues.append(f"{field} must be a non-empty UTC ISO-8601 string")
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        issues.append(f"{field} is not valid ISO-8601: {value!r}")
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        issues.append(f"{field} must include UTC timezone")
        return None
    return parsed.astimezone(UTC)


def _window(
    value: Any, field: str, issues: list[str]
) -> tuple[datetime | None, datetime | None]:
    if not isinstance(value, dict):
        issues.append(f"{field} must be an object with start and end")
        return None, None
    start = _time(value.get("start"), f"{field}.start", issues)
    end = _time(value.get("end"), f"{field}.end", issues)
    if start is not None and end is not None and start >= end:
        issues.append(f"{field}.start must be earlier than {field}.end")
    return start, end


def validate_contract(payload: Any) -> dict[str, Any]:
    issues: list[str] = []
    if not isinstance(payload, dict):
        return {"status": "error", "issues": ["contract must be a JSON object"]}

    if payload.get("schema_version") != SCHEMA_VERSION:
        issues.append(f"schema_version must be {SCHEMA_VERSION!r}")
    if not isinstance(payload.get("task_id"), str) or not payload["task_id"].strip():
        issues.append("task_id must be a non-empty string")
    if payload.get("forecast_mode") not in FORECAST_MODES:
        issues.append(f"forecast_mode must be one of {sorted(FORECAST_MODES)}")
    spatial_unit = payload.get("spatial_unit")
    if spatial_unit not in SPATIAL_UNITS:
        issues.append(f"spatial_unit must be one of {sorted(SPATIAL_UNITS)}")
    thresholds = payload.get("target_thresholds")
    if not isinstance(thresholds, list) or not thresholds:
        issues.append("target_thresholds must be a non-empty array")
    elif any(
        not isinstance(item, str) or not THRESHOLD.fullmatch(item)
        for item in thresholds
    ):
        issues.append(
            "each target threshold must match forms such as C1.0+, M1.0+, or X1.0+"
        )
    elif len(thresholds) != len(set(thresholds)):
        issues.append("target_thresholds must not contain duplicates")
    if payload.get("output_type") != "probability":
        issues.append("output_type must be 'probability'")
    if spatial_unit == "active_region":
        policy = payload.get("region_identifier_policy")
        if not isinstance(policy, str) or not policy.strip():
            issues.append("active_region forecasts require region_identifier_policy")

    issue_time = _time(payload.get("issue_time"), "issue_time", issues)
    data_cutoff = _time(payload.get("data_cutoff"), "data_cutoff", issues)
    observation_start, observation_end = _window(
        payload.get("observation_window"), "observation_window", issues
    )
    prediction_start, prediction_end = _window(
        payload.get("prediction_window"), "prediction_window", issues
    )

    if (
        observation_end is not None
        and data_cutoff is not None
        and observation_end > data_cutoff
    ):
        issues.append("observation_window.end must not be after data_cutoff")
    if data_cutoff is not None and issue_time is not None and data_cutoff > issue_time:
        issues.append("data_cutoff must not be after issue_time")
    if (
        prediction_start is not None
        and issue_time is not None
        and prediction_start < issue_time
    ):
        issues.append("prediction_window.start must not be before issue_time")
    if (
        observation_start is not None
        and prediction_end is not None
        and observation_start >= prediction_end
    ):
        issues.append(
            "observation and prediction windows are not chronologically ordered"
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if not issues else "error",
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("contract", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.contract.read_text(encoding="utf-8"))
    result = validate_contract(payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
