#!/usr/bin/env python3
"""Fetch commonly used solar-cycle datasets with robust fallbacks."""

from __future__ import annotations

import argparse
import shutil
import urllib.request
from pathlib import Path

# Correct SILSO V2.0 URL (semicolon-separated).
SILSO_URL = "https://www.sidc.be/silso/DATA/SN_m_tot_V2.0.csv"

# Historical NOAA F10.7 URL is frequently unavailable behind corporate firewalls.
# The pipeline treats F10.7 as optional; we keep the URL for environments that can reach it.
F10_URL = "https://www.ngdc.noaa.gov/stp/space-weather/solar-data/solar-indices/flux/f10-7-cm-flux/lists/listf10_7a.txt"


def _skill_dir() -> Path:
    """Directory containing this script and the bundled sample data."""
    return Path(__file__).resolve().parent.parent


def download(url: str, dest: Path) -> bool:
    try:
        urllib.request.urlretrieve(url, dest)
        return True
    except Exception as exc:
        print(f"Failed to download {url}: {exc}")
        return False


def convert_silso_csv(src: Path, dest: Path) -> bool:
    """Convert SILSO semicolon CSV to the whitespace-separated format expected downstream."""
    try:
        lines = []
        for line in src.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(";")
            if len(parts) < 7:
                continue
            year, month, decimal_date, sn, std, nobs, _ = parts[:7]
            # Build a synthetic day=01 date; SILSO monthly data is monthly averaged.
            lines.append(f"{year} {month} 01 {decimal_date} {sn} {std} {nobs}")
        dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return True
    except Exception as exc:
        print(f"Failed to convert SILSO CSV: {exc}")
        return False


def copy_bundled_sunspot(output: Path) -> bool:
    """Copy the bundled historical SILSO sample for offline/restricted-network testing."""
    sample = _skill_dir() / "references" / "sample_data" / "SN_m_tot.csv"
    if sample.exists():
        shutil.copy2(sample, output)
        return True
    return False


def fetch_silso(out_dir: Path) -> bool:
    """Download SILSO and convert to the expected format. Falls back to bundled data."""
    raw = out_dir / "SN_m_tot_V2.0.raw.csv"
    dest = out_dir / "SN_m_tot.csv"
    if download(SILSO_URL, raw):
        if convert_silso_csv(raw, dest):
            print(f"Downloaded and converted SILSO data -> {dest}")
            return True
    print("SILSO download/convert failed; using bundled historical sample")
    if copy_bundled_sunspot(dest):
        return False
    print("Bundled sample also missing; aborting")
    raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch solar-cycle datasets")
    parser.add_argument("--output-dir", default="./data", help="Where to save files")
    parser.add_argument(
        "--offline-sample",
        action="store_true",
        help="Create sample files instead of downloading",
    )
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.offline_sample:
        dest = out_dir / "SN_m_tot.csv"
        if copy_bundled_sunspot(dest):
            print(f"Sample data written to {out_dir}")
        else:
            print("Bundled sample missing; aborting")
            raise SystemExit(1)
        return

    fetch_silso(out_dir)

    # F10.7 is optional; try once but do not block the pipeline on network issues.
    f10_dest = out_dir / "f10.7"
    if download(F10_URL, f10_dest):
        print(f"Downloaded F10.7 -> {f10_dest}")
    else:
        print(
            "F10.7 not fetched (network/SSL issue); sunspot-only features will still work"
        )


if __name__ == "__main__":
    main()
