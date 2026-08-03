#!/usr/bin/env python3
"""Build leakage-controlled early-cycle features from official SILSO products.

The cycle label comes from SILSO's official Version 2.0 extrema table.  The
predictor uses only raw monthly observations available by a fixed issue month,
while the centered smoothed series is used only to audit whether the official
minimum was already reproducible at that issue time.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

SCHEMA_VERSION = "solar-cycle-asof-features-v1"
FORECAST_MONTH = 24
FEATURE_START_MONTH = 7
SMOOTHING_AVAILABILITY_LAG_MONTHS = 6


@dataclass(frozen=True)
class Extremum:
    date: pd.Timestamp
    sunspot_number: float


@dataclass(frozen=True)
class OfficialCycle:
    cycle: int
    minimum: Extremum
    maximum: Extremum | None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_cycle_selector(value: str) -> list[int]:
    selected: set[int] = set()
    for part in value.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            start, end = int(start_text), int(end_text)
            if start > end:
                raise ValueError(f"invalid descending cycle range: {token}")
            selected.update(range(start, end + 1))
        else:
            selected.add(int(token))
    if not selected:
        raise ValueError("at least one cycle must be selected")
    return sorted(selected)


def load_monthly_total(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(
        path,
        sep=r"\s+",
        comment="#",
        header=None,
        names=["year", "month", "day", "decimal_date", "sn", "std", "n_obs"],
        engine="python",
    )
    frame["date"] = pd.to_datetime(frame[["year", "month", "day"]])
    frame["sn"] = pd.to_numeric(frame["sn"], errors="raise")
    if frame["date"].duplicated().any():
        raise ValueError("monthly total series contains duplicate months")
    if (frame["sn"] < 0).any():
        raise ValueError("monthly total series contains missing sentinel values")
    return frame[["date", "sn"]].sort_values("date").reset_index(drop=True)


def load_smoothed_total(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(
        path,
        sep=";",
        header=None,
        names=["year", "month", "decimal_date", "sn", "std", "n_obs", "status"],
    )
    frame["date"] = pd.to_datetime(
        {"year": frame["year"], "month": frame["month"], "day": 1}
    )
    frame["sn"] = pd.to_numeric(frame["sn"], errors="raise")
    frame = frame[frame["sn"] >= 0].copy()
    if frame["date"].duplicated().any():
        raise ValueError("smoothed total series contains duplicate months")
    return frame[["date", "sn"]].sort_values("date").reset_index(drop=True)


def load_official_cycles(path: Path) -> dict[int, OfficialCycle]:
    cycles: dict[int, OfficialCycle] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if not fields or not fields[0].isdigit() or len(fields) < 4:
            continue
        cycle_number = int(fields[0])
        minimum = Extremum(
            date=pd.Timestamp(int(fields[1]), int(fields[2]), 1),
            sunspot_number=float(fields[3]),
        )
        maximum = None
        if len(fields) >= 7:
            maximum = Extremum(
                date=pd.Timestamp(int(fields[4]), int(fields[5]), 1),
                sunspot_number=float(fields[6]),
            )
        cycles[cycle_number] = OfficialCycle(cycle_number, minimum, maximum)
    if not cycles:
        raise ValueError("official extrema table contains no cycle rows")
    return cycles


def _month_offset(start: pd.Timestamp, date: pd.Timestamp) -> int:
    return (date.year - start.year) * 12 + date.month - start.month


def _linear_slope(month_offsets: np.ndarray, values: np.ndarray) -> float:
    matrix = np.column_stack([np.ones(len(month_offsets)), month_offsets])
    return float(np.linalg.lstsq(matrix, values, rcond=None)[0][1])


def build_asof_features(
    monthly: pd.DataFrame,
    smoothed: pd.DataFrame,
    official_cycles: dict[int, OfficialCycle],
    selected_cycles: list[int],
    *,
    forecast_month: int = FORECAST_MONTH,
    feature_start_month: int = FEATURE_START_MONTH,
    smoothing_lag_months: int = SMOOTHING_AVAILABILITY_LAG_MONTHS,
) -> pd.DataFrame:
    if feature_start_month < 1 or forecast_month <= feature_start_month:
        raise ValueError("feature months must define a non-empty early-cycle window")

    monthly_by_date = monthly.set_index("date")["sn"]
    expected_offsets = np.arange(feature_start_month, forecast_month + 1)
    rows: list[dict[str, object]] = []

    for cycle_number in selected_cycles:
        cycle = official_cycles.get(cycle_number)
        if cycle is None or cycle.maximum is None:
            raise ValueError(f"cycle {cycle_number} lacks an official maximum")

        minimum_date = cycle.minimum.date
        issue_date = minimum_date + pd.DateOffset(months=forecast_month)
        feature_dates = [
            minimum_date + pd.DateOffset(months=int(offset))
            for offset in expected_offsets
        ]
        missing_feature_dates = [
            date for date in feature_dates if date not in monthly_by_date
        ]
        if missing_feature_dates:
            missing = ", ".join(
                date.strftime("%Y-%m") for date in missing_feature_dates
            )
            raise ValueError(
                f"cycle {cycle_number} is missing feature months: {missing}"
            )

        feature_values = monthly_by_date.loc[feature_dates].to_numpy(dtype=float)
        rise_rate = _linear_slope(expected_offsets.astype(float), feature_values)
        max_input_date = max(feature_dates)

        available_smoothed_through = issue_date - pd.DateOffset(
            months=smoothing_lag_months
        )
        previous_cycle = official_cycles.get(cycle_number - 1)
        if previous_cycle is not None and previous_cycle.maximum is not None:
            search_start = previous_cycle.maximum.date + pd.DateOffset(months=1)
        else:
            search_start = smoothed["date"].min()
        asof_window = smoothed[
            (smoothed["date"] >= search_start)
            & (smoothed["date"] <= available_smoothed_through)
        ]
        if asof_window.empty:
            raise ValueError(
                f"cycle {cycle_number} has no as-of smoothed boundary window"
            )
        asof_minimum_value = float(asof_window["sn"].min())
        candidate_rows = asof_window[
            np.isclose(asof_window["sn"], asof_minimum_value, rtol=0, atol=1e-12)
        ]
        candidate_dates = candidate_rows["date"].tolist()
        official_minimum_is_candidate = minimum_date in candidate_dates

        future_input_count = sum(date > issue_date for date in feature_dates)
        target_after_issue = cycle.maximum.date > issue_date
        rows.append(
            {
                "cycle": cycle_number,
                "official_min_date": minimum_date.strftime("%Y-%m-%d"),
                "official_min_sn": cycle.minimum.sunspot_number,
                "official_max_date": cycle.maximum.date.strftime("%Y-%m-%d"),
                "official_max_sn": cycle.maximum.sunspot_number,
                "issue_date": issue_date.strftime("%Y-%m-%d"),
                "feature_start_date": feature_dates[0].strftime("%Y-%m-%d"),
                "feature_end_date": feature_dates[-1].strftime("%Y-%m-%d"),
                "feature_month_count": len(feature_dates),
                "rise_rate_monthly_7_24": rise_rate,
                "max_input_date": max_input_date.strftime("%Y-%m-%d"),
                "asof_smoothed_available_through": available_smoothed_through.strftime(
                    "%Y-%m-%d"
                ),
                "asof_minimum_value": asof_minimum_value,
                "asof_minimum_candidate_dates": "|".join(
                    date.strftime("%Y-%m-%d") for date in candidate_dates
                ),
                "official_minimum_is_asof_candidate": official_minimum_is_candidate,
                "future_input_count": future_input_count,
                "target_after_issue": target_after_issue,
            }
        )

    features = pd.DataFrame(rows).sort_values("cycle").reset_index(drop=True)
    if features["cycle"].tolist() != selected_cycles:
        raise ValueError("feature output does not preserve the selected cycle order")
    return features


def build_manifest(
    features: pd.DataFrame,
    *,
    monthly_path: Path,
    smoothed_path: Path,
    extrema_path: Path,
    selected_cycles: list[int],
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "data_source": "WDC-SILSO Sunspot Number Version 2.0",
        "source_urls": {
            "monthly_total": "https://www.sidc.be/SILSO/DATA/SN_m_tot_V2.0.csv",
            "smoothed_monthly_total": "https://www.sidc.be/SILSO/DATA/SN_ms_tot_V2.0.csv",
            "official_cycle_extrema": "https://www.sidc.be/SILSO/DATA/Cycles/TableCyclesMiMa.txt",
        },
        "source_files": {
            "monthly_total": {
                "path": str(monthly_path),
                "sha256": sha256_file(monthly_path),
            },
            "smoothed_monthly_total": {
                "path": str(smoothed_path),
                "sha256": sha256_file(smoothed_path),
            },
            "official_cycle_extrema": {
                "path": str(extrema_path),
                "sha256": sha256_file(extrema_path),
            },
        },
        "configuration": {
            "selected_cycles": selected_cycles,
            "forecast_month": FORECAST_MONTH,
            "feature_start_month": FEATURE_START_MONTH,
            "feature_end_month": FORECAST_MONTH,
            "smoothing_availability_lag_months": SMOOTHING_AVAILABILITY_LAG_MONTHS,
            "feature_definition": (
                "OLS slope of raw monthly total sunspot number over months 7 through "
                "24 after the official cycle minimum"
            ),
            "target_definition": "official SILSO Version 2.0 smoothed cycle maximum",
        },
        "audits": {
            "feature_cycle_count": len(features),
            "official_minimum_asof_candidate_count": int(
                features["official_minimum_is_asof_candidate"].sum()
            ),
            "future_input_violation_count": int(features["future_input_count"].sum()),
            "target_timing_violation_count": int(
                (~features["target_after_issue"]).sum()
            ),
            "expected_feature_month_count": FORECAST_MONTH - FEATURE_START_MONTH + 1,
            "feature_month_count_matches": bool(
                (
                    features["feature_month_count"]
                    == FORECAST_MONTH - FEATURE_START_MONTH + 1
                ).all()
            ),
        },
        "claim_boundary": (
            "The official cycle minimum is treated as a historical label. Its as-of "
            "availability is audited at month 24 using only centered-smoothed values "
            "that would have been available by that issue time."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build leakage-controlled early solar-cycle features"
    )
    parser.add_argument("--monthly", required=True)
    parser.add_argument("--smoothed", required=True)
    parser.add_argument("--extrema", required=True)
    parser.add_argument("--cycles", default="1-24")
    parser.add_argument("--features-output", required=True)
    parser.add_argument("--manifest-output", required=True)
    args = parser.parse_args()

    monthly_path = Path(args.monthly).expanduser().resolve()
    smoothed_path = Path(args.smoothed).expanduser().resolve()
    extrema_path = Path(args.extrema).expanduser().resolve()
    features_output = Path(args.features_output).expanduser().resolve()
    manifest_output = Path(args.manifest_output).expanduser().resolve()

    selected_cycles = parse_cycle_selector(args.cycles)
    features = build_asof_features(
        load_monthly_total(monthly_path),
        load_smoothed_total(smoothed_path),
        load_official_cycles(extrema_path),
        selected_cycles,
    )
    manifest = build_manifest(
        features,
        monthly_path=monthly_path,
        smoothed_path=smoothed_path,
        extrema_path=extrema_path,
        selected_cycles=selected_cycles,
    )

    features_output.parent.mkdir(parents=True, exist_ok=True)
    manifest_output.parent.mkdir(parents=True, exist_ok=True)
    features.to_csv(features_output, index=False)
    manifest["feature_artifact"] = {
        "path": str(features_output),
        "sha256": sha256_file(features_output),
    }
    manifest_output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "features_output": str(features_output),
                "manifest_output": str(manifest_output),
                "feature_rows": len(features),
                "audits": manifest["audits"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
