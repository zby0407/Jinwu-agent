from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
INTERIM_DIR = DATA_DIR / "interim"
PROCESSED_DIR = DATA_DIR / "processed"
OUTPUT_PATH = PROCESSED_DIR / "data_lineage_manifest.json"


SCRIPT_BY_OUTPUT = {
    "data/interim/silso_sn_m_tot_v2_interim.csv": "src/build_interim_monthly.py",
    "data/interim/silso_sn_m_hem_v2_interim.csv": "src/build_interim_monthly.py",
    "data/interim/silso_cycle_minmax_interim.csv": "src/build_interim_monthly.py",
    "data/interim/f107_daily_flux_interim.csv": "src/build_interim_monthly.py",
    "data/interim/wso_polar_field_interim.csv": "src/build_interim_monthly.py",
    "data/interim/solar_cycle_metadata_clean.csv": "src/build_solar_cycle_metadata.py",
    "data/interim/goes_xrs_events_interim.csv": "src/build_goes_xrs_monthly_features.py",
    "data/processed/goes_xrs_monthly_features.csv": "src/build_goes_xrs_monthly_features.py",
    "data/processed/clean_monthly_timeseries.csv": "src/build_processed_monthly_timeseries.py",
    "data/processed/cycle_flare_features.csv": "src/build_goes_xrs_cycle_features.py",
    "data/processed/cycle_features.csv": "src/build_cycle_features.py",
    "data/processed/wso_polar_monthly_features.csv": "src/build_wso_hale_features.py",
    "data/processed/cycle_hale_wso_features.csv": "src/build_wso_hale_features.py",
    "data/processed/cycle_hale_wso_sensitivity.csv": "src/build_wso_hale_features.py",
    "data/processed/data_quality_report.json": "src/build_data_quality_report.py",
    "data/processed/feature_registry.json": "src/build_feature_registry.py",
}


def sha256_path(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def csv_summary(path: Path) -> dict[str, Any]:
    try:
        df = pd.read_csv(path, low_memory=False)
    except Exception:
        return {}
    summary: dict[str, Any] = {"rows": int(len(df)), "columns": list(df.columns)}
    for date_col in ["date_month", "event_date", "date"]:
        if date_col in df.columns:
            dates = pd.to_datetime(df[date_col], errors="coerce")
            if dates.notna().any():
                summary["date_column"] = date_col
                summary["date_start"] = dates.min().strftime("%Y-%m-%d")
                summary["date_end"] = dates.max().strftime("%Y-%m-%d")
                break
    if "cycle_no" in df.columns:
        cycles = pd.to_numeric(df["cycle_no"], errors="coerce")
        if cycles.notna().any():
            summary["cycle_start"] = int(cycles.min())
            summary["cycle_end"] = int(cycles.max())
    return summary


def file_record(path: Path, category: str) -> dict[str, Any]:
    rel = str(path.relative_to(ROOT)).replace("\\", "/")
    record: dict[str, Any] = {
        "path": rel,
        "category": category,
        "bytes": path.stat().st_size,
        "modified_utc": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
        "sha256": sha256_path(path),
    }
    if path.suffix.lower() == ".csv":
        record.update(csv_summary(path))
    script = SCRIPT_BY_OUTPUT.get(rel)
    if script:
        script_path = ROOT / script
        record["generated_by_script"] = script
        record["generated_by_script_sha256"] = sha256_path(script_path) if script_path.exists() else None
    return record


def collect_files() -> list[dict[str, Any]]:
    records = []
    for path in sorted(RAW_DIR.rglob("*")):
        if path.is_file():
            records.append(file_record(path, "raw"))
    for path in sorted(INTERIM_DIR.glob("*")):
        if path.is_file():
            records.append(file_record(path, "interim"))
    for path in sorted(PROCESSED_DIR.glob("*")):
        if path.is_file() and path.name != OUTPUT_PATH.name:
            records.append(file_record(path, "processed"))
    for path in sorted((ROOT / "src").glob("*.py")):
        records.append(file_record(path, "script"))
    return records


def main() -> None:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Reproducibility manifest for raw, interim, processed, and script files.",
        "root": str(ROOT),
        "files": collect_files(),
    }
    OUTPUT_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {OUTPUT_PATH}")
    print(f"files={len(manifest['files'])}")


if __name__ == "__main__":
    main()
