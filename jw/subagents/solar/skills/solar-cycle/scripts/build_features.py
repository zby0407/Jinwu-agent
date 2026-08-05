#!/usr/bin/env python3
"""Build cycle-level and precursor features from sunspot and F10.7 data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

# Canonical SILSO monthly sunspot cycle minima (approximate).
# Used as a sensible default when no cycle metadata JSON is supplied.
CANONICAL_MINIMA = [
    "1755-03-01",
    "1766-06-01",
    "1775-06-01",
    "1784-09-01",
    "1798-04-01",
    "1810-07-01",
    "1823-05-01",
    "1833-11-01",
    "1843-07-01",
    "1856-02-01",
    "1867-03-01",
    "1878-12-01",
    "1890-03-01",
    "1902-01-01",
    "1913-07-01",
    "1923-08-01",
    "1933-09-01",
    "1944-02-01",
    "1954-04-01",
    "1964-10-01",
    "1976-03-01",
    "1986-09-01",
    "1996-08-01",
    "2008-12-01",
    "2019-12-01",
    "2030-01-01",  # Sentinel so cycle 25 has an end bound.
]


def load_silso(path: Path) -> pd.DataFrame:
    """Load SILSO monthly total sunspot number.

    Expected columns (space-separated): YYYY MM DD DecimalDate MonthlyTotalSN StdDev Observations
    """
    df = pd.read_csv(
        path,
        sep=r"\s+",
        comment="#",
        header=None,
        names=["year", "month", "day", "decimal_date", "sn", "std", "n_obs"],
        engine="python",
    )
    df["date"] = pd.to_datetime(df[["year", "month", "day"]])
    return df


def load_f10(path: Path) -> pd.DataFrame:
    """Load F10.7 text file with date and flux columns."""
    df = pd.read_csv(
        path,
        sep=r"\s+",
        comment="#",
        header=None,
        engine="python",
    )
    # Try common NOAA formats; fallback to first two numeric columns.
    if df.shape[1] >= 4:
        df = df.iloc[:, :4]
        df.columns = ["year", "month", "day", "f10"]
    else:
        df.columns = ["year", "month", "day", "f10"][: df.shape[1]]
    df["date"] = pd.to_datetime(df[["year", "month", "day"]])
    return df


def _cycles_from_canonical_minima(sunspot: pd.DataFrame) -> list[dict]:
    """Build cycle boundaries from canonical minima, filtered to the data range."""
    min_date = sunspot["date"].min()
    max_date = sunspot["date"].max()
    minima = [pd.to_datetime(d) for d in CANONICAL_MINIMA]
    # Keep minima that have data on both sides (need start and end for a cycle).
    usable = [d for d in minima if d >= min_date and d <= max_date]
    if len(usable) < 2:
        # Fallback: coarse local-minima inference for non-standard data.
        smoothed = (
            sunspot.set_index("date")["sn"]
            .rolling(61, center=True, min_periods=1)
            .mean()
        )
        minima = smoothed[
            (smoothed.shift(1) > smoothed) & (smoothed.shift(-1) > smoothed)
        ]
        dates = sorted({*minima.index.tolist(), min_date, max_date})
        usable = [pd.to_datetime(d) for d in dates]

    cycles = []
    for i in range(len(usable) - 1):
        segment = sunspot[
            (sunspot["date"] >= usable[i]) & (sunspot["date"] < usable[i + 1])
        ]
        if segment.empty:
            continue
        peak_idx = segment["sn"].idxmax()
        peak = segment.loc[peak_idx]
        cycles.append(
            {
                "cycle": i + 1,
                "start_date": usable[i].strftime("%Y-%m-%d"),
                "end_date": usable[i + 1].strftime("%Y-%m-%d"),
                "peak_date": peak["date"].strftime("%Y-%m-%d"),
                "peak_sn": float(peak["sn"]),
            }
        )
    return cycles


def load_polar_monthly(path: Path) -> pd.DataFrame:
    """Load monthly polar-field precursor table produced by load_polar_huairou.py."""
    df = pd.read_csv(
        path,
        parse_dates=["date"] if "date" in pd.read_csv(path, nrows=0).columns else False,
    )
    # If a 'date' column is not present, build one from year/month.
    if "date" not in df.columns:
        df["date"] = (
            pd.to_datetime(df[["year", "month", "day"]])
            if "day" in df.columns
            else pd.to_datetime(df[["year", "month"]].assign(day=1))
        )
    return df


def _select_consistent_polar_epoch(
    window: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, int | str | bool]]:
    """Select one instrument/unit definition without averaging incompatible rows.

    A cycle-minimum window can straddle an instrument transition.  The epoch
    with the greatest distinct-month coverage is selected deterministically;
    all provenance and exclusion counts are retained in the feature row.
    """
    provenance: dict[str, int | str | bool] = {
        "polar_instrument_epoch": "none",
        "polar_signal_unit": "none",
        "polar_signal_definition": "none",
        "polar_epoch_selection": "no_data",
        "polar_n_epochs_window": 0,
        "polar_window_rows_total": len(window),
        "polar_window_rows_used": 0,
        "polar_excluded_mixed_rows": 0,
        "polar_epoch_mixed_window": False,
        "polar_value_comparability": "within_epoch_only",
    }
    if window.empty:
        return window, provenance

    frame = window.copy()
    metadata_cols = ["instrument_epoch", "signal_unit", "signal_definition"]
    for column in metadata_cols:
        if column not in frame.columns:
            frame[column] = "unknown"
        frame[column] = frame[column].fillna("unknown").astype(str)

    coverage = (
        frame.groupby(metadata_cols, dropna=False)
        .agg(
            distinct_months=("date", lambda values: values.dt.to_period("M").nunique()),
            rows=("date", "size"),
        )
        .reset_index()
        .sort_values(
            ["distinct_months", "rows", *metadata_cols],
            ascending=[False, False, True, True, True],
            kind="stable",
        )
    )
    selected = coverage.iloc[0]
    mask = pd.Series(True, index=frame.index)
    for column in metadata_cols:
        mask &= frame[column] == selected[column]
    filtered = frame[mask].copy()

    n_epochs = len(coverage)
    excluded = int(len(frame) - len(filtered))
    provenance.update(
        {
            "polar_instrument_epoch": str(selected["instrument_epoch"]),
            "polar_signal_unit": str(selected["signal_unit"]),
            "polar_signal_definition": str(selected["signal_definition"]),
            "polar_epoch_selection": (
                "dominant_month_coverage" if n_epochs > 1 else "single_epoch"
            ),
            "polar_n_epochs_window": n_epochs,
            "polar_window_rows_used": len(filtered),
            "polar_excluded_mixed_rows": excluded,
            "polar_epoch_mixed_window": n_epochs > 1,
        }
    )
    return filtered, provenance


def _polar_proxy_in_window(
    polar: pd.DataFrame,
    minimum_date: pd.Timestamp,
    window_months: int,
    min_months: int,
) -> dict:
    """Aggregate polar-field strength observations around a cycle minimum date.

    Because the signed polar-cap means in this archive do not reliably separate
    the north and south magnetic polarities, the unsigned pixel-level absolute
    value (``field_mean_abs``) is used as the primary polar-field strength
    proxy. Signed corrected means are retained only as exploratory diagnostics.
    """
    start = minimum_date - pd.DateOffset(months=window_months)
    end = minimum_date + pd.DateOffset(months=window_months)
    window = polar[(polar["date"] >= start) & (polar["date"] <= end)]
    window, provenance = _select_consistent_polar_epoch(window)

    result: dict[str, float | int | str | bool] = dict(provenance)
    for hemi, label in [("N", "n"), ("S", "s")]:
        sub = window[window["hemisphere"] == hemi]
        n_months = int(sub["date"].dt.to_period("M").nunique())
        result[f"polar_n_months_{label}"] = n_months
        if n_months >= min_months:
            # Primary proxy: unsigned pixel-level absolute field strength.
            result[f"polar_proxy_abs_{label}"] = float(sub["field_mean_abs"].mean())
            # Diagnostic signed proxy (use with caution).
            result[f"polar_proxy_signed_{label}"] = float(
                sub["field_mean_corrected"].mean()
            )
        else:
            result[f"polar_proxy_abs_{label}"] = float("nan")
            result[f"polar_proxy_signed_{label}"] = float("nan")

    n_n = result.get("polar_n_months_n", 0)
    n_s = result.get("polar_n_months_s", 0)
    if n_n >= min_months and n_s >= min_months:
        abs_n = result.get("polar_proxy_abs_n", float("nan"))
        abs_s = result.get("polar_proxy_abs_s", float("nan"))
        result["polar_proxy_abs_combined"] = float(
            pd.Series([abs_n, abs_s]).mean(skipna=True)
        )
        result["polar_data_quality"] = "good"
    elif n_n >= min_months or n_s >= min_months:
        abs_n = result.get("polar_proxy_abs_n", float("nan"))
        abs_s = result.get("polar_proxy_abs_s", float("nan"))
        result["polar_proxy_abs_combined"] = float(
            pd.Series([abs_n, abs_s]).mean(skipna=True)
        )
        result["polar_data_quality"] = "single_hemisphere"
    else:
        result["polar_proxy_abs_combined"] = float("nan")
        result["polar_data_quality"] = "insufficient"

    return result


def build_cycle_features(
    sunspot: pd.DataFrame,
    f10: pd.DataFrame | None,
    polar: pd.DataFrame | None,
    cycles: list[dict],
    polar_window_months: int = 12,
    polar_min_months: int = 3,
) -> pd.DataFrame:
    """Build cycle-level feature table.

    `cycles` is a list of dicts with keys: cycle, start_date, end_date, peak_date, peak_sn.
    If not provided, simple minima are inferred.
    """
    if not cycles:
        cycles = _cycles_from_canonical_minima(sunspot)

    rows = []
    for c in cycles:
        start = pd.to_datetime(c["start_date"])
        end = pd.to_datetime(c["end_date"])
        seg = sunspot[(sunspot["date"] >= start) & (sunspot["date"] < end)]
        if seg.empty:
            continue
        min_sn = float(seg["sn"].min())
        peak_sn = float(c.get("peak_sn", seg["sn"].max()))
        peak_date = pd.to_datetime(
            c.get("peak_date", seg.loc[seg["sn"].idxmax(), "date"])
        )
        length_months = int((end - start).days / 30.44)
        rise_months = int((peak_date - start).days / 30.44)
        fall_months = length_months - rise_months
        rise_slope = (peak_sn - min_sn) / max(rise_months, 1)

        row = {
            "cycle": c["cycle"],
            "start_date": start.strftime("%Y-%m-%d"),
            "end_date": end.strftime("%Y-%m-%d"),
            "peak_date": peak_date.strftime("%Y-%m-%d"),
            "length_months": length_months,
            "rise_months": rise_months,
            "fall_months": fall_months,
            "min_sn": min_sn,
            "peak_sn": peak_sn,
            "rise_slope": rise_slope,
            "integrated_sn": float(seg["sn"].sum()),
        }

        if f10 is not None and not f10.empty:
            f10_seg = f10[(f10["date"] >= start) & (f10["date"] < end)]
            if not f10_seg.empty:
                row["mean_f10"] = float(f10_seg["f10"].mean())
                row["peak_f10"] = float(f10_seg["f10"].max())

        if polar is not None and not polar.empty:
            row.update(
                _polar_proxy_in_window(
                    polar, start, polar_window_months, polar_min_months
                )
            )

        rows.append(row)

    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build solar-cycle features")
    parser.add_argument("--sunspot", required=True, help="SILSO monthly sunspot file")
    parser.add_argument("--f10.7", dest="f10", default=None, help="F10.7 data file")
    parser.add_argument("--cycles", default=None, help="JSON file with cycle metadata")
    parser.add_argument(
        "--polar-monthly",
        default=None,
        help="Monthly polar-field precursor CSV (e.g. from load_polar_huairou.py)",
    )
    parser.add_argument(
        "--polar-window-months",
        type=int,
        default=12,
        help="Months around cycle minimum used for the polar precursor average",
    )
    parser.add_argument(
        "--polar-min-months",
        type=int,
        default=3,
        help="Minimum number of months with polar data required per hemisphere",
    )
    parser.add_argument("--output", required=True, help="Output CSV path")
    args = parser.parse_args()

    sunspot = load_silso(Path(args.sunspot))
    f10 = load_f10(Path(args.f10)) if args.f10 else None
    polar = load_polar_monthly(Path(args.polar_monthly)) if args.polar_monthly else None
    cycles = None
    if args.cycles:
        cycles = json.loads(Path(args.cycles).read_text())

    features = build_cycle_features(
        sunspot,
        f10,
        polar,
        cycles or [],
        polar_window_months=args.polar_window_months,
        polar_min_months=args.polar_min_months,
    )
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    features.to_csv(args.output, index=False)
    print(f"Wrote {len(features)} cycle features to {args.output}")


if __name__ == "__main__":
    main()
