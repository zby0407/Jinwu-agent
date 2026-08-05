#!/usr/bin/env python3
"""Merge Huairou polar-precursor CSV stages without hiding conflicts."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

METADATA_DEFAULTS = {
    "instrument_epoch": "legacy_dat_1987_2001",
    "camera": "SMFT legacy detector",
    "source_format": "dat-int16-le",
    "signal_definition": "stored-longitudinal-image",
    "signal_unit": "detector_count_proxy",
    "calibration_status": "uncalibrated",
    "byte_order_normalization": "legacy-little-endian",
}


def merge_csvs(inputs: list[Path], output: Path, monthly: bool) -> pd.DataFrame:
    if len(inputs) < 2:
        raise ValueError("At least two input CSV files are required")

    frames: list[pd.DataFrame] = []
    required = {"hemisphere", "field_mean_abs"}
    key = ["year", "month", "hemisphere"] if monthly else ["date", "hemisphere"]
    required.update(key)

    for path in inputs:
        frame = pd.read_csv(path)
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"{path} lacks required columns: {sorted(missing)}")
        for column, default in METADATA_DEFAULTS.items():
            if column not in frame.columns:
                frame[column] = default
        frame["source_csv"] = str(path)
        frames.append(frame)

    merged = pd.concat(frames, ignore_index=True, sort=False)
    duplicated = merged.duplicated(key, keep=False)
    if duplicated.any():
        conflicts = merged.loc[duplicated, [*key, "source_csv"]].sort_values(key)
        raise ValueError(
            "Duplicate date/hemisphere keys detected; refusing implicit overwrite:\n"
            + conflicts.to_string(index=False)
        )

    merged = merged.sort_values(key).reset_index(drop=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(output, index=False)
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--monthly", action="store_true")
    args = parser.parse_args()
    merged = merge_csvs(args.inputs, args.output, args.monthly)
    print(f"Wrote {len(merged)} rows to {args.output}")


if __name__ == "__main__":
    main()
