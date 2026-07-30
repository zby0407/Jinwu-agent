"""Deterministic contracts for narrow, high-risk scientific analyses."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

F107_DISCONTINUITY_PROTOCOL = "f107_discontinuity_v1"
F107_DISCONTINUITY_REQUIRED_MEASUREMENTS: tuple[str, ...] = (
    "f107_full_period_relation",
    "f107_pre_1980_relation",
    "f107_post_1980_relation",
    "f107_fixed_1980_chow_f",
    "f107_scan_best_break_year",
    "f107_relative_scale_jump",
    "f107_pre_model_predicts_post_mean_residual",
    "f107_pre_model_predicts_post_positive_fraction",
    "f107_post_model_predicts_pre_mean_residual",
    "f107_post_model_predicts_pre_positive_fraction",
    "f107_low_activity_sensitivity",
    "f107_month_coverage_sensitivity",
)

_F107_PATTERN = re.compile(
    r"(?:f\s*10[.]?7|10[.]7\s*cm|太阳射电流量)",
    re.IGNORECASE,
)
_F107_DISCONTINUITY_PATTERN = re.compile(
    r"(?:1980|1981|漂移|不连续|断点|变点|跨时段|跨周期稳定|"
    r"discontinuity|breakpoint|change[\s-]?point|drift|cross[\s-]?period)",
    re.IGNORECASE,
)


def detect_analysis_protocol(text: str) -> str:
    """Return the required deterministic analysis protocol for one request."""

    if _F107_PATTERN.search(text) and _F107_DISCONTINUITY_PATTERN.search(text):
        return F107_DISCONTINUITY_PROTOCOL
    return "none"


def f107_discontinuity_directive() -> str:
    """Return the experiment-facing contract for the F10.7 discontinuity task."""

    measurement_ids = ", ".join(F107_DISCONTINUITY_REQUIRED_MEASUREMENTS)
    return (
        "Implement f107_discontinuity_v1 from the verified dataset manifest. "
        "Model F10.7 as the response over the common pre/post sunspot-number "
        "support; never invert an SN-on-F10.7 OLS slope. Compute the relative "
        "F10.7 scale jump at a fixed reference such as SN=100 and store it as "
        "f107_relative_scale_jump, then compare that computed value with the "
        "published approximately 10.5% discontinuity without forcing agreement. "
        "Keep the fixed 1980-1981 comparison confirmatory. For exploratory "
        "breakpoint scans, use an upper-tail survival probability and choose the "
        "maximum F statistic or minimum unrestricted SSR, not the first p-value "
        "that underflows to zero. Define cross-period residuals as "
        "actual-minus-predicted. Include observed/URSI product, low-activity, and "
        "20/25-observed-day monthly-coverage sensitivities. Estimate long-term "
        "trend and instantaneous step in a joint model before comparing them. "
        f"Emit every required measurement id: {measurement_ids}."
    )


def sha256_file(path: Path) -> str:
    """Hash one immutable artifact without loading it fully into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class DatasetSemanticManifest:
    """Hash-bound semantic description of one canonicalized dataset."""

    manifest_id: str
    input_path: str
    input_sha256: str
    adapter_id: str
    adapter_version: str
    product_id: str
    product_version: str
    column_bindings: Mapping[str, str]
    unit: str
    observation_grain: str
    time_column: str
    primary_key: tuple[str, ...]
    duplicate_policy: str
    missing_policy: str
    quality_policy: str
    aggregation_plan: tuple[str, ...]
    coverage_start: str
    coverage_end: str
    diagnostics: Mapping[str, Any] = field(default_factory=dict)
    excluded_inputs: tuple[Mapping[str, str], ...] = ()
    limitations: tuple[str, ...] = ()
    analysis_requirements: tuple[str, ...] = ()
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["column_bindings"] = dict(self.column_bindings)
        value["primary_key"] = list(self.primary_key)
        value["aggregation_plan"] = list(self.aggregation_plan)
        value["diagnostics"] = dict(self.diagnostics)
        value["excluded_inputs"] = [dict(row) for row in self.excluded_inputs]
        value["limitations"] = list(self.limitations)
        value["analysis_requirements"] = list(self.analysis_requirements)
        return value


__all__ = [
    "DatasetSemanticManifest",
    "F107_DISCONTINUITY_PROTOCOL",
    "F107_DISCONTINUITY_REQUIRED_MEASUREMENTS",
    "detect_analysis_protocol",
    "f107_discontinuity_directive",
    "sha256_file",
]
