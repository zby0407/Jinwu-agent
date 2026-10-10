"""Explicit offline B1 computation or validation; this is not a JW Agent run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from jw.b1_compute import run_b1_analysis, validate_b1_inputs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inputs",
        type=Path,
        required=True,
        help="Prepared directory with five canonical input files",
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--validate-only",
        action="store_true",
        help="Check input structure and hashes, without analyzing events",
    )
    action.add_argument(
        "--output", type=Path, help="Run descriptive calculation into a new directory"
    )
    args = parser.parse_args()
    sources = args.inputs / "sources.json"
    if not sources.exists():
        sources = args.inputs / "source_manifest.json"  # Archived calculation inputs.
    paths = {
        "magnetic_path": args.inputs / "magnetic.csv",
        "events_path": args.inputs / "events.csv",
        "xrs_path": args.inputs / "xrs.csv",
        "parameters_path": args.inputs / "parameters.json",
        "sources_path": sources,
    }
    result = (
        validate_b1_inputs(**paths)
        if args.validate_only
        else run_b1_analysis(**paths, output_dir=args.output)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
