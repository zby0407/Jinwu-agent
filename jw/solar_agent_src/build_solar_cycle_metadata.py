from __future__ import annotations

from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
INTERIM_DIR = ROOT / "data" / "interim"
INPUT_PATH = INTERIM_DIR / "silso_cycle_minmax_interim.csv"
OUTPUT_PATH = INTERIM_DIR / "solar_cycle_metadata_clean.csv"


def simple_phase(row: pd.Series) -> str:
    if pd.isna(row["cycle_no"]):
        return "unknown"
    if row["months_from_cycle_start"] == 0:
        return "minimum"
    if row["months_to_cycle_peak"] == 0:
        return "maximum"
    if row["months_to_cycle_peak"] > 0:
        return "rising"
    if row["months_to_cycle_peak"] < 0:
        return "declining"
    return "unknown"


def windowed_phase(row: pd.Series) -> str:
    if pd.isna(row["cycle_no"]):
        return "unknown"
    if row["months_from_cycle_start"] <= 6:
        return "minimum_window"
    if abs(row["months_to_cycle_peak"]) <= 6:
        return "maximum_window"
    if row["months_to_cycle_peak"] > 6:
        return "rising"
    if row["months_to_cycle_peak"] < -6:
        return "declining"
    return "unknown"


def main() -> None:
    source = pd.read_csv(INPUT_PATH)
    metadata = source[
        [
            "date_month",
            "cycle_number",
            "months_since_cycle_min",
            "months_until_cycle_max",
            "cycle_min_sn",
            "cycle_max_sn",
        ]
    ].rename(
        columns={
            "cycle_number": "cycle_no",
            "months_since_cycle_min": "months_from_cycle_start",
            "months_until_cycle_max": "months_to_cycle_peak",
            "cycle_min_sn": "official_cycle_min_sn",
            "cycle_max_sn": "official_cycle_max_sn",
        }
    )
    metadata["cycle_phase"] = metadata.apply(simple_phase, axis=1)
    metadata["cycle_phase_windowed"] = metadata.apply(windowed_phase, axis=1)
    metadata = metadata[
        [
            "date_month",
            "cycle_no",
            "cycle_phase",
            "cycle_phase_windowed",
            "months_from_cycle_start",
            "months_to_cycle_peak",
            "official_cycle_min_sn",
            "official_cycle_max_sn",
        ]
    ]
    metadata["cycle_no"] = metadata["cycle_no"].astype("Int64")
    metadata["months_from_cycle_start"] = metadata["months_from_cycle_start"].astype(
        "Int64"
    )
    metadata["months_to_cycle_peak"] = metadata["months_to_cycle_peak"].astype("Int64")
    metadata.to_csv(OUTPUT_PATH, index=False, encoding="utf-8")
    print(f"saved {OUTPUT_PATH}")
    print(
        f"rows={len(metadata)} range={metadata['date_month'].min()}..{metadata['date_month'].max()}"
    )
    print(metadata["cycle_phase"].value_counts().to_string())
    print(metadata["cycle_phase_windowed"].value_counts().to_string())


if __name__ == "__main__":
    main()
