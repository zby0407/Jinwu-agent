#!/usr/bin/env python3
"""Build a minimal offline-transfer bundle for Huairou server processing."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

SCRIPT_FILES = (
    "load_polar_huairou.py",
    "merge_polar_outputs.py",
    "inventory_polar_huairou.py",
    "run_polar_huairou_server.py",
    "validate_polar_huairou.py",
    "requirements-polar.txt",
)


def prepare_bundle(
    output: Path, historical_daily: Path, historical_monthly: Path
) -> list[Path]:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"Bundle directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    reference_dir = output / "reference"
    reference_dir.mkdir(exist_ok=True)
    source_dir = Path(__file__).resolve().parent

    copied: list[Path] = []
    for name in SCRIPT_FILES:
        destination = output / name
        shutil.copy2(source_dir / name, destination)
        copied.append(destination)

    readme_destination = output / "README_SERVER.md"
    shutil.copy2(
        source_dir.parents[5] / "docs" / "huairou-polar-server-2014-2026.md",
        readme_destination,
    )
    copied.append(readme_destination)

    daily_destination = reference_dir / "huairou_historical_daily.csv"
    monthly_destination = reference_dir / "huairou_historical_monthly.csv"
    shutil.copy2(historical_daily, daily_destination)
    shutil.copy2(historical_monthly, monthly_destination)
    copied.extend([daily_destination, monthly_destination])
    return copied


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("polar-server-bundle"))
    parser.add_argument("--historical-daily", required=True, type=Path)
    parser.add_argument("--historical-monthly", required=True, type=Path)
    args = parser.parse_args()
    copied = prepare_bundle(args.output, args.historical_daily, args.historical_monthly)
    for path in copied:
        print(path)


if __name__ == "__main__":
    main()
