#!/usr/bin/env python3
"""Plot sunspot cycle time series."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def load_silso(path: Path) -> pd.DataFrame:
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot sunspot cycle")
    parser.add_argument("--sunspot", required=True, help="SILSO monthly sunspot file")
    parser.add_argument("--output", required=True, help="Output PNG path")
    args = parser.parse_args()

    df = load_silso(Path(args.sunspot))
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(df["date"], df["sn"], linewidth=0.8)
    ax.set_xlabel("Year")
    ax.set_ylabel("Sunspot Number")
    ax.set_title("Monthly Sunspot Number")
    ax.grid(True, alpha=0.3)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=150, bbox_inches="tight")
    print(f"Saved plot to {args.output}")


if __name__ == "__main__":
    main()
