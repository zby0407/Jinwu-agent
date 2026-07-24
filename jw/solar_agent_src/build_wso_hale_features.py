from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = ROOT / "data" / "processed"
MASTER_PATH = PROCESSED_DIR / "clean_monthly_timeseries.csv"
CYCLE_FEATURES_PATH = PROCESSED_DIR / "cycle_features.csv"
MONTHLY_OUTPUT_PATH = PROCESSED_DIR / "wso_polar_monthly_features.csv"
CYCLE_OUTPUT_PATH = PROCESSED_DIR / "cycle_hale_wso_features.csv"
SENSITIVITY_OUTPUT_PATH = PROCESSED_DIR / "cycle_hale_wso_sensitivity.csv"

WEAK_FIELD_THRESHOLD = 5.0
REVERSAL_STABILITY_WINDOW = 6
REVERSAL_STABILITY_MIN_MONTHS = 3
PHASE_LOOKUP_WINDOW_MONTHS = 18
SENSITIVITY_WEAK_THRESHOLDS = [3.0, 5.0, 10.0]
SENSITIVITY_STABILITY_WINDOWS = [3, 6, 9, 12]


def month_diff(start: pd.Timestamp, end: pd.Timestamp) -> int:
    return int((end.year - start.year) * 12 + (end.month - start.month))


def sign_label(value: float, weak_threshold: float = WEAK_FIELD_THRESHOLD) -> str:
    if pd.isna(value):
        return "missing"
    if abs(float(value)) < weak_threshold:
        return "weak"
    return "positive" if float(value) > 0 else "negative"


def dipole_state(north_sign: str, south_sign: str) -> str:
    if "missing" in {north_sign, south_sign}:
        return "missing"
    if "weak" in {north_sign, south_sign}:
        return "weak"
    if north_sign == "positive" and south_sign == "negative":
        return "Npos_Sneg"
    if north_sign == "negative" and south_sign == "positive":
        return "Nneg_Spos"
    return "weak"


def first_stable_sign(signs: pd.Series) -> str | None:
    valid = signs[signs.isin(["positive", "negative"])]
    if valid.empty:
        return None
    return str(valid.iloc[0])


def detect_reversal_month(
    group: pd.DataFrame,
    sign_col: str,
    stability_window: int = REVERSAL_STABILITY_WINDOW,
    min_stable_months: int | None = None,
) -> pd.Timestamp | pd.NaT:
    if min_stable_months is None:
        min_stable_months = max(2, int(np.ceil(stability_window / 2)))
    first_phase = group.head(24)
    initial = first_stable_sign(first_phase[sign_col])
    if initial is None:
        return pd.NaT

    opposite = "negative" if initial == "positive" else "positive"
    signs = group[["date_month", sign_col]].reset_index(drop=True)
    for idx, row in signs.iterrows():
        if row[sign_col] != opposite:
            continue
        window = signs.iloc[idx : idx + stability_window]
        if int(window[sign_col].eq(opposite).sum()) >= min_stable_months:
            return pd.Timestamp(row["date_month"])
    return pd.NaT


def nearest_hale_phase(monthly: pd.DataFrame, target_date: pd.Timestamp) -> str:
    if pd.isna(target_date):
        return "missing"
    start = target_date - pd.DateOffset(months=PHASE_LOOKUP_WINDOW_MONTHS)
    end = target_date + pd.DateOffset(months=PHASE_LOOKUP_WINDOW_MONTHS)
    candidates = monthly[
        monthly["date_month"].between(start, end)
        & monthly["hale_phase_wso_monthly"].isin(["Npos_Sneg", "Nneg_Spos"])
    ].copy()
    if candidates.empty:
        weak = monthly[
            monthly["date_month"].between(start, end)
            & monthly["hale_phase_wso_monthly"].eq("weak")
        ]
        return "weak" if not weak.empty else "missing"
    candidates["distance"] = (candidates["date_month"] - target_date).abs()
    return str(candidates.sort_values(["distance", "date_month"]).iloc[0]["hale_phase_wso_monthly"])


def build_monthly(master: pd.DataFrame, weak_threshold: float = WEAK_FIELD_THRESHOLD) -> pd.DataFrame:
    monthly = master[
        [
            "date_month",
            "polar_north",
            "polar_south",
            "polar_quality_flag",
            "cycle_no",
            "cycle_phase",
        ]
    ].copy()
    monthly["polar_north_sign"] = monthly["polar_north"].map(lambda value: sign_label(value, weak_threshold))
    monthly["polar_south_sign"] = monthly["polar_south"].map(lambda value: sign_label(value, weak_threshold))
    monthly["polar_dipole_state"] = [
        dipole_state(north, south)
        for north, south in zip(monthly["polar_north_sign"], monthly["polar_south_sign"])
    ]
    monthly["hale_phase_wso_monthly"] = monthly["polar_dipole_state"]
    monthly["hale_evidence_tier"] = np.where(
        monthly["polar_dipole_state"].eq("missing"),
        "missing",
        "observed_polar_field",
    )
    return monthly


def build_cycle(
    monthly: pd.DataFrame,
    cycles: pd.DataFrame,
    stability_window: int = REVERSAL_STABILITY_WINDOW,
) -> pd.DataFrame:
    rows = []
    for row in cycles.sort_values("cycle_no").to_dict("records"):
        cycle_no = int(row["cycle_no"])
        start_date = pd.to_datetime(row["start_date"])
        peak_date = pd.to_datetime(row["peak_date"], errors="coerce")
        end_date = pd.to_datetime(row["end_date"])
        group = monthly[
            monthly["date_month"].between(start_date, end_date)
            & monthly["hale_evidence_tier"].eq("observed_polar_field")
        ].sort_values("date_month")

        north_reversal = (
            detect_reversal_month(group, "polar_north_sign", stability_window)
            if not group.empty
            else pd.NaT
        )
        south_reversal = (
            detect_reversal_month(group, "polar_south_sign", stability_window)
            if not group.empty
            else pd.NaT
        )
        reversal_asymmetry = (
            month_diff(north_reversal, south_reversal)
            if pd.notna(north_reversal) and pd.notna(south_reversal)
            else np.nan
        )
        has_wso = not group.empty

        rows.append(
            {
                "cycle_no": cycle_no,
                "start_date": start_date,
                "peak_date": peak_date,
                "end_date": end_date,
                "north_reversal_month": north_reversal,
                "south_reversal_month": south_reversal,
                "reversal_asymmetry_months": reversal_asymmetry,
                "hale_phase_wso_at_cycle_start": nearest_hale_phase(monthly, start_date),
                "hale_phase_wso_at_cycle_minimum": nearest_hale_phase(monthly, start_date),
                "hale_evidence_tier": "observed_polar_field" if has_wso else "missing",
            }
        )

    cycle = pd.DataFrame(rows)
    for col in ["start_date", "peak_date", "end_date", "north_reversal_month", "south_reversal_month"]:
        cycle[col] = pd.to_datetime(cycle[col], errors="coerce").dt.strftime("%Y-%m-%d")
    cycle["cycle_no"] = cycle["cycle_no"].astype("Int64")
    return cycle


def build_sensitivity(master: pd.DataFrame, cycles: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for weak_threshold in SENSITIVITY_WEAK_THRESHOLDS:
        monthly = build_monthly(master, weak_threshold)
        for stability_window in SENSITIVITY_STABILITY_WINDOWS:
            cycle = build_cycle(monthly, cycles, stability_window)
            cycle["weak_threshold"] = weak_threshold
            cycle["stability_window_months"] = stability_window
            cycle["min_stable_months"] = max(2, int(np.ceil(stability_window / 2)))
            rows.append(cycle)
    sensitivity = pd.concat(rows, ignore_index=True)
    base = sensitivity[
        sensitivity["weak_threshold"].eq(WEAK_FIELD_THRESHOLD)
        & sensitivity["stability_window_months"].eq(REVERSAL_STABILITY_WINDOW)
    ][["cycle_no", "north_reversal_month", "south_reversal_month"]].rename(
        columns={
            "north_reversal_month": "baseline_north_reversal_month",
            "south_reversal_month": "baseline_south_reversal_month",
        }
    )
    sensitivity = sensitivity.merge(base, on="cycle_no", how="left")
    sensitivity["north_matches_baseline"] = (
        sensitivity["north_reversal_month"].fillna("missing")
        == sensitivity["baseline_north_reversal_month"].fillna("missing")
    )
    sensitivity["south_matches_baseline"] = (
        sensitivity["south_reversal_month"].fillna("missing")
        == sensitivity["baseline_south_reversal_month"].fillna("missing")
    )
    return sensitivity


def main() -> None:
    master = pd.read_csv(MASTER_PATH, parse_dates=["date_month"])
    cycles = pd.read_csv(CYCLE_FEATURES_PATH)

    monthly = build_monthly(master)
    cycle = build_cycle(monthly, cycles)
    sensitivity = build_sensitivity(master, cycles)

    monthly_out = monthly[
        [
            "date_month",
            "cycle_no",
            "cycle_phase",
            "polar_north",
            "polar_south",
            "polar_north_sign",
            "polar_south_sign",
            "polar_dipole_state",
            "hale_phase_wso_monthly",
            "polar_quality_flag",
            "hale_evidence_tier",
        ]
    ].copy()
    monthly_out["date_month"] = monthly_out["date_month"].dt.strftime("%Y-%m-%d")
    monthly_out["cycle_no"] = monthly_out["cycle_no"].astype("Int64")

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    monthly_out.to_csv(MONTHLY_OUTPUT_PATH, index=False, encoding="utf-8")
    cycle.to_csv(CYCLE_OUTPUT_PATH, index=False, encoding="utf-8")
    sensitivity.to_csv(SENSITIVITY_OUTPUT_PATH, index=False, encoding="utf-8")

    print(f"saved {MONTHLY_OUTPUT_PATH}")
    print(f"saved {CYCLE_OUTPUT_PATH}")
    print(f"saved {SENSITIVITY_OUTPUT_PATH}")
    print(
        monthly_out["hale_phase_wso_monthly"]
        .fillna("missing")
        .value_counts()
        .sort_index()
        .to_string()
    )


if __name__ == "__main__":
    main()
