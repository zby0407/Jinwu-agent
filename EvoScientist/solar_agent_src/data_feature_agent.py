from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CATALOG_CANDIDATES = [
    DATA_DIR / "data_catalog.csv",
    ROOT / "data_catalog.csv",
]
INTERIM_DIR = DATA_DIR / "interim"
PROCESSED_DIR = DATA_DIR / "processed"
AGENT_OUTPUT_PATH = PROCESSED_DIR / "agent_output.json"


def import_builders() -> dict[str, Any]:
    src_dir = Path(__file__).resolve().parent
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    import build_cycle_features
    import build_data_lineage_manifest
    import build_data_quality_report
    import build_drift_report
    import build_feature_registry
    import build_goes_xrs_cycle_features
    import build_goes_xrs_monthly_features
    import build_interim_monthly
    import build_processed_monthly_timeseries
    import build_solar_cycle_metadata
    import build_wso_hale_features

    return {
        "interim_monthly": build_interim_monthly.main,
        "solar_cycle_metadata": build_solar_cycle_metadata.main,
        "processed_monthly_timeseries": build_processed_monthly_timeseries.main,
        "cycle_features": build_cycle_features.main,
        "goes_xrs_monthly_features": build_goes_xrs_monthly_features.main,
        "goes_xrs_cycle_features": build_goes_xrs_cycle_features.main,
        "wso_hale_features": build_wso_hale_features.main,
        "data_quality_report": build_data_quality_report.main,
        "drift_report": build_drift_report.main,
        "feature_registry": build_feature_registry.main,
        "data_lineage_manifest": build_data_lineage_manifest.main,
    }


def find_catalog() -> Path:
    for candidate in CATALOG_CANDIDATES:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "data_catalog.csv not found. Expected one of: "
        + ", ".join(str(path.relative_to(ROOT)) for path in CATALOG_CANDIDATES)
    )


def load_catalog() -> pd.DataFrame:
    catalog_path = find_catalog()
    catalog = pd.read_csv(catalog_path)
    required = {
        "dataset_id",
        "raw_path",
        "interim_path",
        "processed_role",
        "source_name",
        "temporal_resolution",
        "target_resolution",
        "evidence_tier",
        "description",
    }
    missing = required.difference(catalog.columns)
    if missing:
        raise ValueError(f"data_catalog.csv is missing required columns: {sorted(missing)}")
    return catalog


def path_info(path: Path) -> dict[str, Any]:
    info: dict[str, Any] = {
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "exists": path.exists(),
    }
    if path.exists():
        info["bytes"] = path.stat().st_size
        info["modified_utc"] = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    return info


def table_summary(path: Path, date_col: str | None = None) -> dict[str, Any]:
    info = path_info(path)
    if not path.exists():
        return info
    df = pd.read_csv(path)
    info["rows"] = int(len(df))
    info["columns"] = list(df.columns)
    if date_col and date_col in df.columns:
        dates = pd.to_datetime(df[date_col], errors="coerce")
        if dates.notna().any():
            info["date_start"] = dates.min().strftime("%Y-%m-%d")
            info["date_end"] = dates.max().strftime("%Y-%m-%d")
    if "cycle_no" in df.columns and df["cycle_no"].notna().any():
        info["cycle_start"] = int(pd.to_numeric(df["cycle_no"], errors="coerce").min())
        info["cycle_end"] = int(pd.to_numeric(df["cycle_no"], errors="coerce").max())
    return info


def validate_catalog_files(catalog: pd.DataFrame) -> list[dict[str, Any]]:
    checks = []
    for row in catalog.to_dict("records"):
        raw_path = ROOT / row["raw_path"]
        checks.append(
            {
                "dataset_id": row["dataset_id"],
                "raw_path": row["raw_path"],
                "raw_exists": raw_path.exists(),
                "interim_path": row["interim_path"],
                "evidence_tier": row["evidence_tier"],
                "target_resolution": row["target_resolution"],
            }
        )
    missing = [item for item in checks if not item["raw_exists"]]
    if missing:
        raise FileNotFoundError(f"Catalog raw files missing: {missing}")
    return checks


def run_stage(stage_name: str, fn: Any) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    fn()
    finished = datetime.now(timezone.utc)
    return {
        "stage": stage_name,
        "status": "ok",
        "started_utc": started.isoformat(),
        "finished_utc": finished.isoformat(),
        "duration_seconds": round((finished - started).total_seconds(), 3),
    }


def read_quality_highlights() -> dict[str, Any]:
    report_path = PROCESSED_DIR / "data_quality_report.json"
    if not report_path.exists():
        return {"available": False}
    report = json.loads(report_path.read_text(encoding="utf-8"))
    return {
        "available": True,
        "primary_evidence": [
            item["signal"] for item in report["evidence_tiers"]["primary_evidence"]
        ],
        "auxiliary_evidence": [
            item["signal"] for item in report["evidence_tiers"]["mechanism_or_auxiliary_evidence"]
        ],
        "incomplete_cycles": report["cycle_table_quality"]["incomplete_cycles"],
        "all_source_overlap": report["master_table_quality"]["all_sources_overlap"],
        "disallowed_or_caution_claims": report["claims_policy_for_downstream_agents"][
            "disallowed_or_caution_claims"
        ],
    }


def build_agent_output(
    catalog_path: Path,
    catalog: pd.DataFrame,
    catalog_checks: list[dict[str, Any]],
    stages: list[dict[str, Any]],
) -> dict[str, Any]:
    outputs = {
        "interim": {
            "monthly_total_sunspot": table_summary(INTERIM_DIR / "silso_sn_m_tot_v2_interim.csv", "date_month"),
            "monthly_hemispheric_sunspot": table_summary(
                INTERIM_DIR / "silso_sn_m_hem_v2_interim.csv", "date_month"
            ),
            "solar_cycle_minmax_monthly": table_summary(
                INTERIM_DIR / "silso_cycle_minmax_interim.csv", "date_month"
            ),
            "solar_cycle_metadata": table_summary(
                INTERIM_DIR / "solar_cycle_metadata_clean.csv", "date_month"
            ),
            "monthly_f107": table_summary(INTERIM_DIR / "f107_daily_flux_interim.csv", "date_month"),
            "monthly_wso_polar_field": table_summary(
                INTERIM_DIR / "wso_polar_field_interim.csv", "date_month"
            ),
            "goes_xrs_events": table_summary(INTERIM_DIR / "goes_xrs_events_interim.csv", "event_date"),
        },
        "processed": {
            "clean_monthly_timeseries": table_summary(
                PROCESSED_DIR / "clean_monthly_timeseries.csv", "date_month"
            ),
            "cycle_features": table_summary(PROCESSED_DIR / "cycle_features.csv"),
            "cycle_flare_features": table_summary(PROCESSED_DIR / "cycle_flare_features.csv"),
            "goes_xrs_monthly_features": table_summary(
                PROCESSED_DIR / "goes_xrs_monthly_features.csv", "date_month"
            ),
            "wso_polar_monthly_features": table_summary(
                PROCESSED_DIR / "wso_polar_monthly_features.csv", "date_month"
            ),
            "cycle_hale_wso_features": table_summary(PROCESSED_DIR / "cycle_hale_wso_features.csv"),
            "cycle_hale_wso_sensitivity": table_summary(
                PROCESSED_DIR / "cycle_hale_wso_sensitivity.csv"
            ),
            "feature_registry": path_info(PROCESSED_DIR / "feature_registry.json"),
            "data_lineage_manifest": path_info(PROCESSED_DIR / "data_lineage_manifest.json"),
            "data_quality_report": path_info(PROCESSED_DIR / "data_quality_report.json"),
            "drift_report": path_info(PROCESSED_DIR / "drift_report.json"),
        },
    }

    return {
        "agent_name": "data_feature_agent",
        "status": "ok",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "catalog": {
            "path": str(catalog_path.relative_to(ROOT)).replace("\\", "/"),
            "rows": int(len(catalog)),
            "datasets": catalog["dataset_id"].tolist(),
            "raw_file_checks": catalog_checks,
        },
        "pipeline": {
            "steps": [
                "read data_catalog.csv",
                "clean five raw files into monthly interim tables",
                "build GOES XRS event and monthly flare feature tables",
                "build solar_cycle_metadata_clean.csv",
                "merge clean_monthly_timeseries.csv",
                "annotate cycle_no and cycle_phase",
                "build GOES XRS cycle flare feature table",
                "build cycle_features.csv",
                "build WSO-derived Hale polarity feature tables",
                "write data_quality_report.json",
                "write drift_report.json",
                "write feature_registry.json",
                "write data_lineage_manifest.json",
                "write agent_output.json",
            ],
            "stage_runs": stages,
        },
        "outputs": outputs,
        "quality_highlights": read_quality_highlights(),
        "downstream_contract": {
            "primary_monthly_table": "data/processed/clean_monthly_timeseries.csv",
            "primary_cycle_table": "data/processed/cycle_features.csv",
            "quality_report": "data/processed/data_quality_report.json",
            "must_read_before_experiments": [
                "data/processed/data_quality_report.json",
                "data/processed/drift_report.json",
                "data/processed/cycle_features.csv",
            ],
            "leakage_warning": "next_cycle_* fields in cycle_features.csv are labels and must not be used as input features.",
        },
    }


def main() -> None:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    catalog_path = find_catalog()
    catalog = load_catalog()
    catalog_checks = validate_catalog_files(catalog)

    builders = import_builders()
    stages = [
        run_stage("interim_monthly", builders["interim_monthly"]),
        run_stage("solar_cycle_metadata", builders["solar_cycle_metadata"]),
        run_stage("goes_xrs_flare_features_monthly", builders["goes_xrs_monthly_features"]),
        run_stage("processed_monthly_timeseries", builders["processed_monthly_timeseries"]),
        run_stage("goes_xrs_flare_features_cycle", builders["goes_xrs_cycle_features"]),
        run_stage("cycle_features", builders["cycle_features"]),
        run_stage("wso_hale_features", builders["wso_hale_features"]),
        run_stage("data_quality_report", builders["data_quality_report"]),
        run_stage("drift_report", builders["drift_report"]),
        run_stage("feature_registry", builders["feature_registry"]),
        run_stage("data_lineage_manifest", builders["data_lineage_manifest"]),
    ]

    output = build_agent_output(catalog_path, catalog, catalog_checks, stages)
    AGENT_OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {AGENT_OUTPUT_PATH}")
    print(json.dumps({"status": "ok", "agent_output": str(AGENT_OUTPUT_PATH)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
