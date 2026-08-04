#!/usr/bin/env python3
"""Run an audited, per-year Huairou SMFT polar diagnostic batch."""

from __future__ import annotations

import argparse
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

import inventory_polar_huairou as inventory
import load_polar_huairou as loader
import merge_polar_outputs as merger
import pandas as pd


def _process_candidate(
    path: Path,
    root: Path,
    shape: str,
    fit_signal: str,
    fit_aperture_mode: str,
    fit_center_radius: int,
    allow_unvalidated_geometry: bool,
) -> dict:
    """Route a known layout to the matching signal and aperture."""
    dimensions = tuple(int(value) for value in shape.split("x"))
    single_plane = len(dimensions) == 2
    return loader.process_file(
        path,
        root,
        fit_signal=None if single_plane else fit_signal,
        fit_aperture_mode="polar-strip" if single_plane else fit_aperture_mode,
        fit_center_radius=fit_center_radius,
        allow_unvalidated_geometry=allow_unvalidated_geometry,
    )


def _empty_daily() -> pd.DataFrame:
    return pd.DataFrame(columns=loader.DAILY_COLUMNS)


def _empty_monthly() -> pd.DataFrame:
    return pd.DataFrame(columns=loader.MONTHLY_COLUMNS)


def _assert_schema(frame: pd.DataFrame, expected: list[str], label: str) -> None:
    if list(frame.columns) != expected:
        raise ValueError(
            f"{label} schema mismatch: expected {expected}, got {list(frame.columns)}"
        )


def _assert_unique(frame: pd.DataFrame, key: list[str], label: str) -> None:
    duplicated = frame.duplicated(key, keep=False)
    if duplicated.any():
        rows = frame.loc[duplicated, key].sort_values(key)
        raise ValueError(
            f"{label} contains duplicate keys {key}:\n{rows.to_string(index=False)}"
        )


def _normalize_historical(path: Path, monthly: bool) -> pd.DataFrame:
    frame = pd.read_csv(path)
    for column, default in merger.METADATA_DEFAULTS.items():
        if column not in frame.columns:
            frame[column] = default
    frame = frame.drop(columns=["source_csv"], errors="ignore")
    expected = loader.MONTHLY_COLUMNS if monthly else loader.DAILY_COLUMNS
    missing = set(expected) - set(frame.columns)
    if missing:
        raise ValueError(f"{path} lacks historical columns: {sorted(missing)}")
    return frame[expected].copy()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def run_batch(args: argparse.Namespace) -> dict:
    root = args.polar_dir
    output_root = args.output_root
    data_dir = output_root / "data"
    artifact_dir = output_root / "artifacts"
    data_dir.mkdir(parents=True, exist_ok=True)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    inventory_summary, inventory_records = inventory.run_inventory(
        root, args.start_year, args.end_year
    )
    inventory_json = artifact_dir / f"inventory_{args.start_year}_{args.end_year}.json"
    inventory_csv = artifact_dir / f"inventory_{args.start_year}_{args.end_year}.csv"
    inventory_json.write_text(
        json.dumps(inventory_summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    inventory_records.to_csv(inventory_csv, index=False)

    if inventory_summary["candidate_files"] == 0:
        raise RuntimeError(
            "Inventory found no NPL/SPL FITS candidates; check --polar-dir and years"
        )
    if inventory_summary["unsupported_files"] or inventory_summary["read_error_files"]:
        raise RuntimeError(
            "Inventory contains unsupported or unreadable files; inspect "
            f"{inventory_json} and {inventory_csv} before processing"
        )

    supported = inventory_records.loc[
        inventory_records["status"] == "supported"
    ].copy()
    daily_frames: list[pd.DataFrame] = []
    monthly_frames: list[pd.DataFrame] = []
    year_summaries: list[dict] = []

    for year in range(args.start_year, args.end_year + 1):
        year_records = supported.loc[supported["year"] == year]
        extracted: list[dict] = []
        errors: list[dict] = []
        if not year_records.empty:
            with ProcessPoolExecutor(max_workers=args.workers) as executor:
                futures = {
                    executor.submit(
                        _process_candidate,
                        root / row.file,
                        root,
                        row.shape,
                        args.fit_signal,
                        args.fit_aperture_mode,
                        args.fit_center_radius,
                        args.allow_unvalidated_geometry,
                    ): row.file
                    for row in year_records.itertuples(index=False)
                }
                for future in as_completed(futures):
                    relative = futures[future]
                    try:
                        extracted.append(future.result())
                    except Exception as exc:  # pragma: no cover - archive defense
                        errors.append({"file": relative, "error": repr(exc)})

        if extracted:
            daily = loader.aggregate_daily(pd.DataFrame(extracted))[
                loader.DAILY_COLUMNS
            ]
            monthly = loader.aggregate_monthly(daily)[loader.MONTHLY_COLUMNS]
            daily["date"] = pd.to_datetime(daily["date"]).dt.strftime("%Y-%m-%d")
        else:
            daily = _empty_daily()
            monthly = _empty_monthly()

        _assert_schema(daily, loader.DAILY_COLUMNS, f"{year} daily")
        _assert_schema(monthly, loader.MONTHLY_COLUMNS, f"{year} monthly")
        _assert_unique(daily, ["date", "hemisphere"], f"{year} daily")
        _assert_unique(monthly, ["year", "month", "hemisphere"], f"{year} monthly")

        if not daily.empty:
            dates = pd.to_datetime(daily["date"])
            if not dates.dt.year.eq(year).all():
                raise ValueError(f"{year} daily output contains dates outside the year")

        daily_path = data_dir / f"huairou_{year}_daily.csv"
        monthly_path = data_dir / f"huairou_{year}_monthly.csv"
        errors_path = artifact_dir / f"huairou_{year}_errors.jsonl"
        daily.to_csv(daily_path, index=False)
        monthly.to_csv(monthly_path, index=False)
        _write_jsonl(errors_path, errors)
        daily_frames.append(daily)
        monthly_frames.append(monthly)
        candidates = len(year_records)
        year_summaries.append(
            {
                "year": year,
                "candidate_files": candidates,
                "accepted_files": len(extracted),
                "processing_errors": len(errors),
                "error_rate": len(errors) / candidates if candidates else 0.0,
                "daily_rows": len(daily),
                "monthly_rows": len(monthly),
            }
        )

    new_daily = pd.concat(daily_frames, ignore_index=True)[loader.DAILY_COLUMNS]
    new_monthly = pd.concat(monthly_frames, ignore_index=True)[loader.MONTHLY_COLUMNS]
    _assert_unique(new_daily, ["date", "hemisphere"], "new daily")
    _assert_unique(new_monthly, ["year", "month", "hemisphere"], "new monthly")
    if not new_daily.empty:
        new_daily = new_daily.sort_values(["date", "hemisphere"]).reset_index(drop=True)
    if not new_monthly.empty:
        new_monthly = new_monthly.sort_values(
            ["year", "month", "hemisphere"]
        ).reset_index(drop=True)

    new_daily_path = data_dir / (
        f"huairou_polar_precursor_{args.start_year}_{args.end_year}_daily.csv"
    )
    new_monthly_path = data_dir / (
        f"huairou_polar_precursor_{args.start_year}_{args.end_year}_monthly.csv"
    )
    new_daily.to_csv(new_daily_path, index=False)
    new_monthly.to_csv(new_monthly_path, index=False)

    historical_daily = _normalize_historical(args.historical_daily, monthly=False)
    historical_monthly = _normalize_historical(args.historical_monthly, monthly=True)
    historical_daily = historical_daily.loc[
        pd.to_datetime(historical_daily["date"]).dt.year < args.start_year
    ].copy()
    historical_monthly = historical_monthly.loc[
        pd.to_numeric(historical_monthly["year"]) < args.start_year
    ].copy()

    combined_daily = pd.concat(
        [historical_daily, new_daily], ignore_index=True
    )[loader.DAILY_COLUMNS]
    combined_monthly = pd.concat(
        [historical_monthly, new_monthly], ignore_index=True
    )[loader.MONTHLY_COLUMNS]
    _assert_unique(combined_daily, ["date", "hemisphere"], "combined daily")
    _assert_unique(
        combined_monthly, ["year", "month", "hemisphere"], "combined monthly"
    )
    combined_daily = combined_daily.sort_values(["date", "hemisphere"])
    combined_monthly = combined_monthly.sort_values(["year", "month", "hemisphere"])

    combined_daily_path = data_dir / (
        f"huairou_polar_precursor_1987_{args.end_year}_daily.csv"
    )
    combined_monthly_path = data_dir / (
        f"huairou_polar_precursor_1987_{args.end_year}_monthly.csv"
    )
    combined_daily.to_csv(combined_daily_path, index=False)
    combined_monthly.to_csv(combined_monthly_path, index=False)

    output_paths = sorted(
        [*data_dir.glob("*.csv"), *artifact_dir.glob("*.jsonl"), inventory_json, inventory_csv]
    )
    checksums = {str(path.relative_to(output_root)): _sha256(path) for path in output_paths}
    summary = {
        "product_status": "diagnostic_unvalidated",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "polar_dir": str(root),
        "start_year": args.start_year,
        "end_year": args.end_year,
        "parameters": {
            "workers": args.workers,
            "fit_signal": args.fit_signal,
            "fit_aperture_mode": args.fit_aperture_mode,
            "fit_center_radius": args.fit_center_radius,
            "allow_unvalidated_geometry": args.allow_unvalidated_geometry,
        },
        "inventory": inventory_summary,
        "year_results": year_summaries,
        "historical_cutoff_year": args.start_year - 1,
        "historical_rows_retained": {
            "daily": len(historical_daily),
            "monthly": len(historical_monthly),
        },
        "new_rows": {"daily": len(new_daily), "monthly": len(new_monthly)},
        "combined_rows": {
            "daily": len(combined_daily),
            "monthly": len(combined_monthly),
        },
        "checksums": checksums,
    }
    summary_path = output_root / "run_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    checksums[str(summary_path.relative_to(output_root))] = _sha256(summary_path)
    checksum_path = output_root / "checksums.sha256"
    checksum_path.write_text(
        "".join(f"{digest}  {path}\n" for path, digest in sorted(checksums.items())),
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--polar-dir", required=True, type=Path)
    parser.add_argument("--start-year", type=int, default=2014)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output-root", type=Path, default=Path("run_2014_2026"))
    parser.add_argument(
        "--fit-signal", choices=loader.FITS_SIGNAL_CHOICES, default="calibrated_vi"
    )
    parser.add_argument(
        "--fit-aperture-mode",
        choices=("center-circle", "center-box"),
        default="center-circle",
    )
    parser.add_argument("--fit-center-radius", type=int, default=150)
    parser.add_argument("--allow-unvalidated-geometry", action="store_true")
    parser.add_argument("--historical-daily", required=True, type=Path)
    parser.add_argument("--historical-monthly", required=True, type=Path)
    args = parser.parse_args()
    if args.start_year > args.end_year:
        parser.error("--start-year must not exceed --end-year")
    if args.workers <= 0:
        parser.error("--workers must be positive")
    if args.fit_center_radius <= 0:
        parser.error("--fit-center-radius must be positive")

    summary = run_batch(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
