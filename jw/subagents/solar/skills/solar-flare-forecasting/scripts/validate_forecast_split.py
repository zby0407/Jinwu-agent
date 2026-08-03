#!/usr/bin/env python3
"""Audit chronological and group isolation in a forecast-instance CSV."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

SPLIT_ORDER = {"train": 0, "validation": 1, "test": 2}


def _time(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("timestamp has no timezone")
    return parsed.astimezone(UTC)


def validate_rows(
    rows: list[dict[str, str]],
    *,
    split_column: str = "split",
    time_column: str = "issue_time",
    group_column: str = "region_id",
) -> dict[str, Any]:
    issues: list[str] = []
    required = {split_column, time_column, group_column}
    if not rows:
        return {"status": "error", "issues": ["forecast table is empty"]}
    missing = required - set(rows[0])
    if missing:
        return {
            "status": "error",
            "issues": [f"missing required columns: {sorted(missing)}"],
        }

    split_times: dict[str, list[datetime]] = defaultdict(list)
    group_splits: dict[str, set[str]] = defaultdict(set)
    for index, row in enumerate(rows, start=2):
        split = row.get(split_column, "").strip()
        if split not in SPLIT_ORDER:
            issues.append(f"row {index}: invalid split {split!r}")
            continue
        try:
            parsed = _time(row.get(time_column, ""))
        except (TypeError, ValueError) as exc:
            issues.append(f"row {index}: invalid {time_column}: {exc}")
            continue
        group = row.get(group_column, "").strip()
        if not group:
            issues.append(f"row {index}: empty {group_column}")
            continue
        split_times[split].append(parsed)
        group_splits[group].add(split)

    for group, splits in sorted(group_splits.items()):
        if len(splits) > 1:
            issues.append(
                f"group {group!r} spans splits {sorted(splits, key=SPLIT_ORDER.get)}"
            )

    present = sorted(split_times, key=SPLIT_ORDER.get)
    for earlier, later in pairwise(present):
        if split_times[earlier] and split_times[later]:
            if max(split_times[earlier]) >= min(split_times[later]):
                issues.append(
                    f"{earlier} issue times overlap or follow {later} issue times"
                )

    return {
        "status": "ok" if not issues else "error",
        "row_count": len(rows),
        "split_counts": {key: len(value) for key, value in sorted(split_times.items())},
        "group_count": len(group_splits),
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("table", type=Path)
    parser.add_argument("--split-column", default="split")
    parser.add_argument("--time-column", default="issue_time")
    parser.add_argument("--group-column", default="region_id")
    args = parser.parse_args()
    with args.table.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    result = validate_rows(
        rows,
        split_column=args.split_column,
        time_column=args.time_column,
        group_column=args.group_column,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
