from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from data_quality_constants import (
    F107_STRENGTH_CLASS_BINS,
    GOES_XRS_LEGACY_COVERAGE,
    MINIMUM_MONTHLY_COMPLETENESS_FOR_OK_PROXY,
    POLAR_PRECUSR_WINDOW_MONTHS,
)
import data_quality_report_text


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
INTERIM_DIR = DATA_DIR / "interim"
PROCESSED_DIR = DATA_DIR / "processed"
OUTPUT_PATH = PROCESSED_DIR / "data_quality_report.json"


def pct(value: float) -> float:
    return round(float(value), 4)


def to_jsonable(value: Any) -> Any:
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    return value


def range_summary(df: pd.DataFrame, date_col: str = "date_month") -> dict[str, Any]:
    dates = pd.to_datetime(df[date_col], errors="coerce")
    return {
        "start": dates.min().strftime("%Y-%m-%d") if dates.notna().any() else None,
        "end": dates.max().strftime("%Y-%m-%d") if dates.notna().any() else None,
        "rows": int(len(df)),
    }


def flag_counts(df: pd.DataFrame, column: str) -> dict[str, int]:
    return {
        str(k): int(v)
        for k, v in df[column].fillna("missing").value_counts().sort_index().items()
    }


def valid_month_summary(
    master: pd.DataFrame, value_col: str, flag_col: str
) -> dict[str, Any]:
    valid = master[value_col].notna()
    dates = master.loc[valid, "date_month"]
    return {
        "valid_months": int(valid.sum()),
        "missing_months": int((~valid).sum()),
        "coverage_ratio_of_master": pct(valid.mean()),
        "valid_start": dates.min().strftime("%Y-%m-%d") if not dates.empty else None,
        "valid_end": dates.max().strftime("%Y-%m-%d") if not dates.empty else None,
        "quality_flag_counts": flag_counts(master, flag_col),
    }


def cycle_availability(cycles: pd.DataFrame, cols: list[str]) -> dict[str, Any]:
    out = {}
    for col in cols:
        valid = cycles[col].notna()
        out[col] = {
            "cycles_with_value": int(valid.sum()),
            "cycles_missing_value": int((~valid).sum()),
            "first_cycle_with_value": int(cycles.loc[valid, "cycle_no"].min())
            if valid.any()
            else None,
            "last_cycle_with_value": int(cycles.loc[valid, "cycle_no"].max())
            if valid.any()
            else None,
        }
    return out


def main() -> None:
    master = pd.read_csv(
        PROCESSED_DIR / "clean_monthly_timeseries.csv", parse_dates=["date_month"]
    )
    cycles = pd.read_csv(PROCESSED_DIR / "cycle_features.csv")
    interim_files = {
        "monthly_total_sunspot": pd.read_csv(
            INTERIM_DIR / "silso_sn_m_tot_v2_interim.csv"
        ),
        "monthly_hemispheric_sunspot": pd.read_csv(
            INTERIM_DIR / "silso_sn_m_hem_v2_interim.csv"
        ),
        "monthly_f107": pd.read_csv(INTERIM_DIR / "f107_daily_flux_interim.csv"),
        "monthly_wso_polar_field": pd.read_csv(
            INTERIM_DIR / "wso_polar_field_interim.csv"
        ),
        "solar_cycle_metadata": pd.read_csv(
            INTERIM_DIR / "solar_cycle_metadata_clean.csv"
        ),
    }
    goes_events_path = INTERIM_DIR / "goes_xrs_events_interim.csv"
    goes_monthly_path = PROCESSED_DIR / "goes_xrs_monthly_features.csv"
    wso_hale_monthly_path = PROCESSED_DIR / "wso_polar_monthly_features.csv"
    cycle_hale_wso_path = PROCESSED_DIR / "cycle_hale_wso_features.csv"
    cycle_hale_wso_sensitivity_path = PROCESSED_DIR / "cycle_hale_wso_sensitivity.csv"
    goes_events = pd.read_csv(goes_events_path) if goes_events_path.exists() else None
    goes_monthly = (
        pd.read_csv(goes_monthly_path) if goes_monthly_path.exists() else None
    )
    wso_hale_monthly = (
        pd.read_csv(wso_hale_monthly_path) if wso_hale_monthly_path.exists() else None
    )
    cycle_hale_wso = (
        pd.read_csv(cycle_hale_wso_path) if cycle_hale_wso_path.exists() else None
    )
    cycle_hale_wso_sensitivity = (
        pd.read_csv(cycle_hale_wso_sensitivity_path)
        if cycle_hale_wso_sensitivity_path.exists()
        else None
    )

    source_profiles = {name: range_summary(df) for name, df in interim_files.items()}
    source_profiles["processed_master"] = range_summary(master)
    source_profiles["cycle_features"] = {
        "rows": int(len(cycles)),
        "cycle_start": int(cycles["cycle_no"].min()),
        "cycle_end": int(cycles["cycle_no"].max()),
        "complete_cycles": int(
            cycles["is_complete"].astype(str).str.lower().eq("true").sum()
        ),
        "incomplete_cycles": int(
            (~cycles["is_complete"].astype(str).str.lower().eq("true")).sum()
        ),
    }
    if goes_events is not None:
        source_profiles["goes_xrs_events"] = range_summary(goes_events, "event_date")
    if goes_monthly is not None:
        source_profiles["goes_xrs_monthly_features"] = range_summary(
            goes_monthly, "date_month"
        )
    if wso_hale_monthly is not None:
        source_profiles["wso_polar_monthly_features"] = range_summary(
            wso_hale_monthly, "date_month"
        )
    if cycle_hale_wso is not None:
        source_profiles["cycle_hale_wso_features"] = {
            "rows": int(len(cycle_hale_wso)),
            "cycle_start": int(cycle_hale_wso["cycle_no"].min()),
            "cycle_end": int(cycle_hale_wso["cycle_no"].max()),
            "cycles_with_wso_hale_evidence": int(
                cycle_hale_wso["hale_evidence_tier"].eq("observed_polar_field").sum()
            ),
        }
    if cycle_hale_wso_sensitivity is not None:
        source_profiles["cycle_hale_wso_sensitivity"] = {
            "rows": int(len(cycle_hale_wso_sensitivity)),
            "cycle_start": int(cycle_hale_wso_sensitivity["cycle_no"].min()),
            "cycle_end": int(cycle_hale_wso_sensitivity["cycle_no"].max()),
            "weak_thresholds": sorted(
                cycle_hale_wso_sensitivity["weak_threshold"].dropna().unique().tolist()
            ),
            "stability_windows_months": sorted(
                cycle_hale_wso_sensitivity["stability_window_months"]
                .dropna()
                .unique()
                .tolist()
            ),
        }

    master_validity = {
        "sunspot_number": valid_month_summary(
            master, "sunspot_number", "sunspot_quality_flag"
        ),
        "hemispheric_sunspot": valid_month_summary(
            master, "north_sunspot_number", "hemisphere_quality_flag"
        ),
        "f107": valid_month_summary(master, "f107_monthly_mean", "f107_quality_flag"),
        "polar_field": valid_month_summary(master, "polar_north", "polar_quality_flag"),
    }
    if "flare_count_total" in master.columns:
        master_validity["goes_xrs_flares"] = valid_month_summary(
            master, "flare_count_total", "flare_data_quality_flag"
        )

    data_coverage_counts = {
        str(k): int(v)
        for k, v in master["data_coverage_flag"]
        .fillna("missing")
        .value_counts()
        .sort_index()
        .items()
    }

    cycle_feature_availability = cycle_availability(
        cycles,
        [
            "f107_mean",
            "f107_sunspot_corr",
            "north_sunspot_mean",
            "hemispheric_asymmetry_mean",
            "polar_precursor_mean",
            "polar_north_mean",
            "next_cycle_peak_sunspot",
            "cycle_flare_count_total",
            "cycle_mx_flare_count",
            "cycle_flare_asymmetry_mean",
        ],
    )
    if cycle_hale_wso is not None:
        cycle_feature_availability["cycle_hale_wso_features_table"] = {
            "cycles_with_observed_polar_field": int(
                cycle_hale_wso["hale_evidence_tier"].eq("observed_polar_field").sum()
            ),
            "cycles_with_north_reversal_month": int(
                cycle_hale_wso["north_reversal_month"].notna().sum()
            ),
            "cycles_with_south_reversal_month": int(
                cycle_hale_wso["south_reversal_month"].notna().sum()
            ),
        }

    incomplete_cycles = cycles.loc[
        ~cycles["is_complete"].astype(str).str.lower().eq("true"), "cycle_no"
    ].tolist()
    incomplete_cycles = [int(x) for x in incomplete_cycles]

    all_coverage = master[master["data_coverage_flag"].eq("all")]
    all_coverage_range = {
        "months": int(len(all_coverage)),
        "start": all_coverage["date_month"].min().strftime("%Y-%m-%d")
        if not all_coverage.empty
        else None,
        "end": all_coverage["date_month"].max().strftime("%Y-%m-%d")
        if not all_coverage.empty
        else None,
    }

    report = {
        "report_metadata": {
            "generated_on": date.today().isoformat(),
            "report_path": str(OUTPUT_PATH.relative_to(ROOT)).replace("\\", "/"),
            "input_tables": {
                "master": "data/processed/clean_monthly_timeseries.csv",
                "cycle_features": "data/processed/cycle_features.csv",
                "interim_dir": "data/interim",
            },
            "purpose": (
                "Tell downstream agents which evidence can be used as primary support, which sources are auxiliary, "
                "where coverage is incomplete, and which conclusions require cautious wording."
            ),
        },
        "source_profiles": source_profiles,
        "master_table_quality": {
            "date_month_standard": "YYYY-MM-01",
            "all_dates_are_month_start": bool((master["date_month"].dt.day == 1).all()),
            "data_coverage_flag_counts": data_coverage_counts,
            "all_sources_overlap": all_coverage_range,
            "validity_by_signal": master_validity,
        },
        "cycle_table_quality": {
            "total_cycles": int(len(cycles)),
            "complete_cycle_count": int(
                cycles["is_complete"].astype(str).str.lower().eq("true").sum()
            ),
            "incomplete_cycles": incomplete_cycles,
            "feature_availability_by_cycle_signal": cycle_feature_availability,
            "target_field_notes": {
                "next_cycle_peak_sunspot": "Available for cycles that have an observed following cycle; missing for the latest cycle.",
                "next_cycle_strength_class": "Derived from next_cycle_peak_sunspot using fixed bins: weak < 100, moderate 100-160, strong > 160.",
                "leakage_warning": "next_cycle_* fields are labels for supervised training and must not be used as input features when predicting the next cycle.",
            },
        },
        "evidence_tiers": {
            "primary_evidence": [
                {
                    "signal": "monthly total sunspot number",
                    "fields": ["sunspot_number", "sunspot_std", "sunspot_quality_flag"],
                    "usable_for": [
                        "long-historical cycle morphology",
                        "cycle amplitude labels",
                        "rise and decline timing",
                        "baseline prediction experiments across many cycles",
                    ],
                    "limitations": [
                        "Early records have limited uncertainty and observation-count metadata.",
                        "Latest months marked provisional should be treated as online updates, not final historical truth.",
                    ],
                },
                {
                    "signal": "solar cycle metadata",
                    "fields": [
                        "cycle_no",
                        "cycle_phase",
                        "months_from_cycle_start",
                        "months_to_cycle_peak",
                    ],
                    "usable_for": [
                        "cycle segmentation",
                        "phase-aware feature construction",
                        "cycle-level aggregation",
                    ],
                    "limitations": [
                        "Phase labels are intentionally simple: minimum, rising, maximum, declining, unknown.",
                        "Cycle 25 is not complete in the current data window.",
                    ],
                },
            ],
            "mechanism_or_auxiliary_evidence": [
                {
                    "signal": "F10.7 radio flux",
                    "fields": [
                        "f107_monthly_mean",
                        "f107_monthly_median",
                        "f107_valid_days",
                        "f107_quality_flag",
                    ],
                    "usable_for": [
                        "proxy comparison with sunspot number",
                        "relationship drift diagnostics",
                        "modern-era model features from 1947 onward",
                    ],
                    "limitations": [
                        "Does not cover cycles before cycle 18.",
                        "Some early records include missing daily values and are flagged as partial.",
                        "F10.7 is a proxy for solar activity, not a direct substitute for sunspot observations across all eras.",
                    ],
                },
                {
                    "signal": "hemispheric sunspot number",
                    "fields": [
                        "north_sunspot_number",
                        "south_sunspot_number",
                        "hemispheric_asymmetry",
                        "hemisphere_quality_flag",
                    ],
                    "usable_for": [
                        "north-south asymmetry diagnostics",
                        "mechanism-level interpretation",
                        "post-1940 external calibrated observation diagnostics and post-1992 official hemispheric comparisons",
                    ],
                    "limitations": [
                        "Official SILSO hemispheric coverage starts in 1992.",
                        "1940-1991 values come from external RGO/NOAA calibrated observations and are not the SILSO official hemispheric product.",
                        "It should not be used to make broad long-historical hemispheric claims without explicitly naming the external-calibrated period.",
                    ],
                },
                {
                    "signal": "WSO polar magnetic field",
                    "fields": [
                        "polar_north",
                        "polar_south",
                        "polar_mean_signed",
                        "polar_mean_abs",
                        "polar_asymmetry",
                        "polar_quality_flag",
                    ],
                    "usable_for": [
                        "polar precursor hypotheses",
                        "Babcock-Leighton mechanism evidence",
                        "cycle-minimum precursor windows from the WSO era",
                    ],
                    "limitations": [
                        "Coverage starts in 1976 and does not support earlier-cycle claims.",
                        "Missing rows and long observation gaps are explicitly flagged.",
                        "Polar precursor features are proxy/mechanism evidence and should be worded as support, not proof.",
                    ],
                },
                {
                    "signal": "WSO-derived Hale polarity phase",
                    "fields": [
                        "polar_north_sign",
                        "polar_south_sign",
                        "polar_dipole_state",
                        "hale_phase_wso_monthly",
                        "north_reversal_month",
                        "south_reversal_month",
                        "reversal_asymmetry_months",
                    ],
                    "usable_for": [
                        "observed WSO-era Hale polarity labeling",
                        "north/south polar reversal timing diagnostics",
                        "22-year magnetic-cycle mechanism features",
                    ],
                    "limitations": [
                        "Derived only from WSO polar-field observations and therefore begins in 1976.",
                        "Weak-field months near zero are intentionally labeled weak rather than forced into a polarity.",
                        "Detected reversal months depend on the weak-field threshold and short stability-window rule.",
                    ],
                },
                {
                    "signal": "GOES XRS flare events",
                    "fields": [
                        "flare_count_total",
                        "flare_count_c",
                        "flare_count_m",
                        "flare_count_x",
                        "m_x_flare_count",
                        "xray_peak_flux_sum_proxy",
                        "flare_hemispheric_asymmetry",
                        "flare_data_quality_flag",
                    ],
                    "usable_for": [
                        "high-activity phase diagnostics",
                        "M/X-class major flare activity indicators",
                        "flare hemispheric asymmetry as auxiliary evidence against sunspot hemispheric asymmetry",
                    ],
                    "limitations": [
                        "The 1975-2017 GOES XRS legacy archive is a multi-source long-term compiled product.",
                        "Many rows contain legacy uncertainty in timing or missing location information.",
                        "Flare hemispheric asymmetry is computed only from events with usable position records, not from all flare events.",
                        "Flare features are auxiliary activity-intensity indicators and should not be treated as direct substitutes for sunspot or magnetic-field measurements.",
                    ],
                },
            ],
        },
        "missingness_and_proxy_warnings": {
            "known_missing_or_limited_areas": [
                {
                    "area": "sunspot uncertainty metadata",
                    "impact": "Early monthly sunspot values often lack standard deviation and observation counts.",
                    "required_agent_behavior": "Use sunspot_number as primary signal but mention limited metadata when discussing uncertainty.",
                },
                {
                    "area": "F10.7 early and partial records",
                    "impact": "F10.7 begins in 1947 and includes partial/missing daily records in some months.",
                    "required_agent_behavior": "Use F10.7 as auxiliary proxy; do not claim all-cycle validation from it.",
                },
                {
                    "area": "hemispheric sunspot coverage",
                    "impact": "Official SILSO hemispheric data begins in 1992; 1940-1991 is external RGO/NOAA calibrated observation with a different source lineage.",
                    "required_agent_behavior": "Use 1940-1991 hemispheric values as real external calibrated observations, preserve hemisphere_source_type, and do not call them SILSO official hemispheric observations.",
                },
                {
                    "area": "polar field coverage",
                    "impact": "WSO polar field begins in 1976 and has missing/partial months.",
                    "required_agent_behavior": "Use as physical precursor evidence only for WSO-era cycles and carry quality flags forward.",
                },
                {
                    "area": "WSO-derived Hale polarity labels",
                    "impact": "Hale polarity and reversal timing are observed-polar-field features only where WSO data exist; weak-field months are not forced into either Hale phase.",
                    "required_agent_behavior": "Use hale_evidence_tier and avoid applying WSO-derived Hale labels to pre-WSO cycles.",
                },
                {
                    "area": "latest cycle completeness",
                    "impact": "Cycle 25 is incomplete in the current master table.",
                    "required_agent_behavior": "Do not treat cycle 25 length, decline behavior, or next-cycle label as final.",
                },
                {
                    "area": "GOES XRS legacy flare reports",
                    "impact": "Flare event records cover 1975-09 through 2017-06 and include legacy multi-source formatting, missing positions, and uncertain time flags. Months with no events inside this coverage are marked observed_zero_event rather than missing.",
                    "required_agent_behavior": "Use flare_data_quality_flag, position_quality_flag, and time_quality_flag; treat flare features as auxiliary high-activity indicators.",
                },
                {
                    "area": "GOES flare hemispheric asymmetry",
                    "impact": "North/south flare counts and flare_hemispheric_asymmetry are based only on events with valid position strings.",
                    "required_agent_behavior": "Use position_valid_count and position_valid_rate before interpreting flare hemispheric asymmetry.",
                },
            ],
            "proxy_markers": {
                "f107": "radio-flux proxy for solar activity",
                "polar_field": "magnetic precursor proxy for dynamo-related hypotheses",
                "wso_hale_phase": "observed WSO polar-field proxy for Hale magnetic polarity phase",
                "hemispheric_asymmetry": "spatial/asymmetry observation or external calibrated observation for mechanism diagnostics",
                "goes_xrs_flares": "event-count and X-ray class proxy for flare activity intensity",
            },
        },
        "claims_policy_for_downstream_agents": {
            "allowed_strong_claims": [
                "Monthly total sunspot number supports long-historical cycle morphology analysis.",
                "Cycle metadata supports consistent month-level cycle segmentation from cycle 1 onward.",
                "Cycle 25 is incomplete in the current data window.",
            ],
            "allowed_moderate_claims": [
                "F10.7 is useful as a modern-era proxy and relationship-drift diagnostic.",
                "WSO polar field can support polar precursor hypotheses in the WSO-era subset.",
                "WSO-derived Hale polarity labels can support observed WSO-era magnetic phase and polar reversal timing diagnostics.",
                "Hemispheric sunspot data can support post-1940 external-calibrated and post-1992 official north-south asymmetry diagnostics.",
                "GOES XRS flare counts and M/X-class indicators can support high-activity phase diagnostics from 1975-2017.",
            ],
            "disallowed_or_caution_claims": [
                "Do not claim F10.7 validates behavior across all 25 cycles.",
                "Do not claim polar-field precursor evidence for cycles before WSO coverage.",
                "Do not apply WSO-derived Hale polarity labels to cycles without observed WSO polar-field coverage.",
                "Do not claim pre-1992 hemispheric asymmetry values are official SILSO observations; they are real external RGO/NOAA calibrated observations with different lineage.",
                "Do not describe correlation as mechanism proof.",
                "Do not treat provisional latest sunspot months as finalized historical observations.",
                "Do not use next_cycle_* target columns as model inputs.",
                "Do not treat GOES XRS legacy flare features as complete or homogeneous without carrying quality flags.",
            ],
            "required_wording_style": [
                "Use terms such as supports, is consistent with, suggests, or provides proxy evidence.",
                "Avoid proves, determines, guarantees, or definitive causal proof unless backed by separate physical validation.",
            ],
        },
        "recommended_agent_usage": {
            "data_feature_agent": [
                "Carry quality flags into all downstream feature tables.",
                "Prefer separate feature subsets for long-history, F10.7-era, WSO-era, and all-source-overlap experiments.",
                "Log any imputation separately; current processed tables do not silently impute missing proxy values.",
            ],
            "experiment_agent": [
                "Run long-history experiments using sunspot and cycle fields.",
                "Run F10.7 and polar-field experiments as subset analyses with explicit coverage reporting.",
                "Run GOES flare experiments as 1975-2017 auxiliary high-activity analyses with quality-flag filtering.",
                "Run WSO-Hale experiments as WSO-era magnetic-cycle subset analyses with hale_evidence_tier filtering.",
                "Use complete cycles for cycle-level training unless the experiment explicitly targets online/incomplete-cycle forecasting.",
            ],
            "evidence_review_agent": [
                "Downgrade confidence when a conclusion depends on F10.7, WSO, or hemispheric fields outside their valid coverage.",
                "Treat cycle 25 decline-phase and cycle-26 implications as provisional.",
                "Check data_coverage_flag and per-signal quality flags before writing conclusions.",
            ],
        },
        "machine_readable_thresholds": {
            "minimum_monthly_completeness_for_ok_proxy": MINIMUM_MONTHLY_COMPLETENESS_FOR_OK_PROXY,
            "f107_strength_class_bins": F107_STRENGTH_CLASS_BINS,
            "polar_precursor_window_months": POLAR_PRECUSR_WINDOW_MONTHS,
            "goes_xrs_legacy_coverage": {
                "start": GOES_XRS_LEGACY_COVERAGE["start"],
                "end": GOES_XRS_LEGACY_COVERAGE["end"],
                "inside_coverage_zero_event_policy": "observed_zero_event months are filled with zero flare counts and has_flare_data=true",
            },
        },
    }

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    text_path = OUTPUT_PATH.with_suffix(".txt")
    text_path.write_text(
        data_quality_report_text.render_data_quality_report_text(report),
        encoding="utf-8",
    )
    print(f"saved {OUTPUT_PATH}")
    print(f"saved {text_path}")
    print(
        json.dumps(
            {
                "master_rows": len(master),
                "cycle_rows": len(cycles),
                "all_source_overlap_months": all_coverage_range["months"],
                "incomplete_cycles": incomplete_cycles,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
