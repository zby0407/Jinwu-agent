"""Shared solar-physics data quality and coverage constants.

This module centralizes the coverage dates, thresholds, and physical rules used
by both the canonical offline pipeline and the chat-based upload workflow.
Changing a value here updates the contract for both code paths.
"""

from __future__ import annotations


# Instrument coverage windows (ISO date strings, month-start)
SOLAR_COVERAGE: dict[str, dict[str, str]] = {
    "sunspot": {"start": "1749-01-01"},
    "hemisphere": {
        "start": "1940-01-01",
        "external_calibrated_end": "1991-12-31",
        "official_start": "1992-01-01",
    },
    "f107": {"start": "1947-02-01"},
    "polar": {"start": "1976-05-01"},
    "hale": {"start": "1976-05-01"},
    "goes_xrs": {"start": "1975-09-01", "end": "2017-06-01"},
}

# Convenience alias for the GOES XRS legacy archive used by downstream reports.
GOES_XRS_LEGACY_COVERAGE = SOLAR_COVERAGE["goes_xrs"]

# Quality thresholds
MINIMUM_MONTHLY_COMPLETENESS_FOR_OK_PROXY = 0.95
POLAR_MISSING_ROW_FLAG = "XXX"
POLAR_MAX_GAP_DAYS_ANOMALY = 40

# Cycle strength bins used by the experiment agent
F107_STRENGTH_CLASS_BINS = {
    "weak": "< 100",
    "moderate": "100-160",
    "strong": "> 160",
}

# Polar precursor window used in mechanism/auxiliary feature engineering
POLAR_PRECUSR_WINDOW_MONTHS = 36

# Source type labels (must match the values used in build_interim_monthly.py)
HEMISPHERE_SOURCE_TYPE = {
    "external_calibrated": "rgo_noaa_external_calibrated_observation",
    "official": "silso_official_hemispheric_observation",
}

# Evidence tiers (must match feature_registry.json conventions)
EVIDENCE_TIER = {
    "primary": "primary",
    "auxiliary_spatial_observation": "auxiliary_spatial_observation",
    "auxiliary_mechanism_proxy": "auxiliary_mechanism_proxy",
    "auxiliary_event_proxy": "auxiliary_event_proxy",
    "metadata": "metadata",
}

# Forbidden / label fields (must match feature_registry.json rules)
LABEL_FIELDS = [
    "next_cycle_peak_sunspot",
    "next_cycle_strength_class",
]

# Recommended wording for downstream agents
RECOMMENDED_WORDING = {
    "allowed": ["supports", "is consistent with", "suggests", "provides proxy evidence"],
    "avoid": ["proves", "determines", "guarantees", "definitive causal proof"],
}
