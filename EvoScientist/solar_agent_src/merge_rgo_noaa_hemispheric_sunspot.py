from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
RGO_DIR = RAW_DIR / "rgo_noaa"
HEM_PATH = RAW_DIR / "silso_sn_m_hem_v2.csv"
TOTAL_PATH = RAW_DIR / "silso_sn_m_tot_v2.csv"
DIAGNOSTICS_PATH = RAW_DIR / "rgo_noaa_hemispheric_merge_diagnostics.json"

SILSO_HEM_COLS = [
    "year",
    "month",
    "date_frac",
    "total_sn",
    "north_sn",
    "south_sn",
    "total_std",
    "north_std",
    "south_std",
    "total_obs",
    "north_obs",
    "south_obs",
    "definitive",
]
SILSO_TOTAL_COLS = [
    "year",
    "month",
    "date_frac",
    "sunspot_number",
    "std_dev",
    "observations",
    "definitive",
]


def parse_rgo_line(line: str) -> dict[str, float] | None:
    if len(line) < 68:
        return None
    try:
        return {
            "year": int(line[0:4]),
            "month": int(line[4:6]),
            "day_frac": float(line[6:12]),
            "corrected_whole_area": float(line[40:44]),
            "latitude": float(line[63:68]),
        }
    except ValueError:
        return None


def read_rgo_noaa_monthly() -> pd.DataFrame:
    rows = []
    for path in sorted(RGO_DIR.glob("g*.txt")):
        for line in path.read_text(encoding="utf-8").splitlines():
            parsed = parse_rgo_line(line)
            if parsed is None:
                continue
            latitude = parsed["latitude"]
            hemisphere = "north" if latitude > 0 else "south" if latitude < 0 else "equator"
            rows.append({**parsed, "hemisphere": hemisphere})

    if not rows:
        raise ValueError(f"No RGO/NOAA rows parsed from {RGO_DIR}")

    daily_groups = pd.DataFrame(rows)
    monthly_area = (
        daily_groups.groupby(["year", "month", "hemisphere"], as_index=False)
        .agg(
            corrected_whole_area_sum=("corrected_whole_area", "sum"),
            group_observations=("corrected_whole_area", "size"),
            observed_days=("day_frac", lambda x: int(np.floor(x).nunique())),
        )
        .pivot_table(
            index=["year", "month"],
            columns="hemisphere",
            values="corrected_whole_area_sum",
            aggfunc="sum",
            fill_value=0.0,
        )
        .reset_index()
    )
    for col in ["north", "south", "equator"]:
        if col not in monthly_area:
            monthly_area[col] = 0.0

    monthly_area["north_south_area"] = monthly_area["north"] + monthly_area["south"]
    monthly_area["north_area_share"] = np.where(
        monthly_area["north_south_area"] > 0,
        monthly_area["north"] / monthly_area["north_south_area"],
        0.5,
    )
    return monthly_area


def read_silso() -> tuple[pd.DataFrame, pd.DataFrame]:
    total = pd.read_csv(
        TOTAL_PATH,
        sep=";",
        header=None,
        names=SILSO_TOTAL_COLS,
        skipinitialspace=True,
    )
    hemispheric = pd.read_csv(
        HEM_PATH,
        sep=";",
        header=None,
        names=SILSO_HEM_COLS,
        skipinitialspace=True,
    )
    return total, hemispheric


def build_merged_rows(total: pd.DataFrame, hemispheric: pd.DataFrame, rgo_monthly: pd.DataFrame) -> pd.DataFrame:
    pre_silso = total[(total["year"] >= 1940) & (total["year"] < 1992)].copy()
    pre_silso = pre_silso.merge(rgo_monthly, on=["year", "month"], how="left", validate="one_to_one")
    missing_share = pre_silso["north_area_share"].isna()
    if missing_share.any():
        missing = pre_silso.loc[missing_share, ["year", "month"]].to_dict("records")
        raise ValueError(f"Missing RGO/NOAA monthly area shares: {missing}")

    total_sn = pre_silso["sunspot_number"].astype(float)
    north = total_sn * pre_silso["north_area_share"]
    south = total_sn - north
    estimated = pd.DataFrame(
        {
            "year": pre_silso["year"].astype(int),
            "month": pre_silso["month"].astype(int),
            "date_frac": pre_silso["date_frac"].astype(float),
            "total_sn": total_sn.round(1),
            "north_sn": north.round(1),
            "south_sn": south.round(1),
            "total_std": pre_silso["std_dev"].astype(float).round(1),
            "north_std": -1.0,
            "south_std": -1.0,
            "total_obs": pre_silso["observations"].astype(int),
            "north_obs": -1,
            "south_obs": -1,
            "definitive": pre_silso["definitive"].astype(int),
        }
    )

    official = hemispheric[hemispheric["year"] >= 1992].copy()
    merged = pd.concat([estimated, official], ignore_index=True)
    merged = merged.sort_values(["year", "month"]).reset_index(drop=True)
    return merged[SILSO_HEM_COLS]


def calibration_diagnostics(hemispheric: pd.DataFrame, rgo_monthly: pd.DataFrame) -> dict[str, object]:
    overlap = hemispheric.merge(rgo_monthly, on=["year", "month"], how="inner")
    overlap = overlap[
        (overlap["year"] >= 1992)
        & (overlap["year"] <= 2016)
        & (overlap["total_sn"] > 0)
        & (overlap["north_south_area"] > 0)
    ].copy()
    overlap["official_north_share"] = overlap["north_sn"] / overlap["total_sn"]
    overlap["estimated_north_sn"] = overlap["total_sn"] * overlap["north_area_share"]
    north_error = overlap["estimated_north_sn"] - overlap["north_sn"]

    return {
        "generated_on": date.today().isoformat(),
        "method": (
            "For 1940-1991, split official SILSO monthly total sunspot number into north/south "
            "using RGO/NOAA monthly corrected whole-spot-area hemispheric shares. "
            "For 1992 onward, keep official SILSO hemispheric rows unchanged."
        ),
        "rgo_noaa_source_dir": str(RGO_DIR.relative_to(ROOT)).replace("\\", "/"),
        "output_file": str(HEM_PATH.relative_to(ROOT)).replace("\\", "/"),
        "estimated_range": {"start": "1940-01", "end": "1991-12"},
        "official_silso_range_preserved": {"start": "1992-01", "end": "2026-06"},
        "overlap_validation": {
            "months": int(len(overlap)),
            "range": {
                "start": f"{int(overlap['year'].min()):04d}-{int(overlap['month'].min()):02d}",
                "end": f"{int(overlap['year'].max()):04d}-{int(overlap['month'].max()):02d}",
            },
            "north_share_correlation": round(float(overlap["north_area_share"].corr(overlap["official_north_share"])), 4),
            "north_sunspot_mae": round(float(north_error.abs().mean()), 4),
            "north_sunspot_rmse": round(float(np.sqrt((north_error**2).mean())), 4),
        },
        "scale_controls": [
            "The monthly total_sn field is copied from official SILSO total sunspot number for estimated rows.",
            "Estimated north_sn plus south_sn is constrained to equal total_sn after one-decimal rounding repair.",
            "RGO/NOAA area values are used only for hemispheric shares, not as direct sunspot-number magnitudes.",
        ],
        "limitations": [
            "1940-1991 north/south values are calibrated proxy estimates, not official SILSO hemispheric observations.",
            "north_std, south_std, north_obs, and south_obs are set to -1 for estimated rows because SILSO-equivalent hemispheric uncertainty and station counts are unavailable.",
        ],
    }


def format_row(row: pd.Series) -> str:
    return (
        f"{int(row.year):04d};{int(row.month):02d};{row.date_frac:8.3f};"
        f"{row.total_sn:6.1f};{row.north_sn:5.1f};{row.south_sn:5.1f};"
        f"{row.total_std:6.1f};{row.north_std:5.1f};{row.south_std:5.1f};"
        f"{int(row.total_obs):5d};{int(row.north_obs):4d};{int(row.south_obs):4d};{int(row.definitive)}"
    )


def main() -> None:
    total, hemispheric = read_silso()
    rgo_monthly = read_rgo_noaa_monthly()
    diagnostics = calibration_diagnostics(hemispheric, rgo_monthly)
    merged = build_merged_rows(total, hemispheric, rgo_monthly)

    diff = (merged["total_sn"] - (merged["north_sn"] + merged["south_sn"])).round(1)
    repair_mask = diff.ne(0)
    merged.loc[repair_mask, "south_sn"] = (merged.loc[repair_mask, "south_sn"] + diff.loc[repair_mask]).round(1)
    final_diff = (merged["total_sn"] - (merged["north_sn"] + merged["south_sn"])).round(1)
    if final_diff.abs().max() > 0:
        raise ValueError("north_sn + south_sn does not match total_sn after rounding repair")

    HEM_PATH.write_text("\n".join(format_row(row) for _, row in merged.iterrows()) + "\n", encoding="utf-8")
    DIAGNOSTICS_PATH.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"saved {HEM_PATH}")
    print(f"saved {DIAGNOSTICS_PATH}")
    print(
        f"rows={len(merged)} range={int(merged['year'].min())}-{int(merged['month'].iloc[0]):02d}"
        f"..{int(merged['year'].max())}-{int(merged['month'].iloc[-1]):02d}"
    )
    print(json.dumps(diagnostics["overlap_validation"], ensure_ascii=False))


if __name__ == "__main__":
    main()
