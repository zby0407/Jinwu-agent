from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


VALID_TASKS = {
    "prepare_features",
    "prepare_features_for_upload",
    "check_quality",
    "summarize_for_experiment",
    "ask_agent",
    "inspect_upload",
    "chat",
    "load_dataset",
    "dataset_stats",
    "dataset_query",
    "analyze_quality",
    "propose_cleaning",
    "apply_cleaning",
    "generate_features",
    "experiment_handoff",
    "strategy_recommendation",
    "align_uploads",
    "apply_multifield_split",
}

DEFAULT_DATA_SCOPE = ["sunspot", "hemisphere", "f107", "wso", "goes", "hale"]


@dataclass
class PiAgentRequest:
    task: str = "prepare_features"
    target: str = "cycle_prediction"
    rebuild: bool = False
    run_tests: bool = True
    data_scope: list[str] = field(default_factory=lambda: DEFAULT_DATA_SCOPE.copy())
    require_quality_report: bool = True
    question: str | None = None
    upload_path: str | None = None
    # Chat / dataset exploration fields
    session_id: str | None = None
    current_dataset: str | None = None
    query: str | None = None
    action: str | None = None
    column: str | None = None
    approval_id: str | None = None
    use_llm_semantics: bool = True
    split_proposal: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PiAgentRequest":
        request = cls(
            task=str(payload.get("task", "prepare_features")),
            target=str(payload.get("target", "cycle_prediction")),
            rebuild=bool(payload.get("rebuild", False)),
            run_tests=bool(payload.get("run_tests", True)),
            data_scope=list(payload.get("data_scope", DEFAULT_DATA_SCOPE)),
            require_quality_report=bool(payload.get("require_quality_report", True)),
            question=payload.get("question"),
            upload_path=payload.get("upload_path"),
            session_id=payload.get("session_id"),
            current_dataset=payload.get("current_dataset"),
            query=payload.get("query"),
            action=payload.get("action"),
            column=payload.get("column"),
            approval_id=payload.get("approval_id"),
            use_llm_semantics=bool(payload.get("use_llm_semantics", True)),
            split_proposal=payload.get("split_proposal"),
        )
        request.validate()
        return request

    def validate(self) -> None:
        if self.task not in VALID_TASKS:
            raise ValueError(f"Unsupported PiAgent task: {self.task}. Valid tasks: {sorted(VALID_TASKS)}")
        if not self.data_scope:
            raise ValueError("data_scope must not be empty")
        if self.task == "ask_agent" and not self.approval_id and not (self.question and self.question.strip()):
            raise ValueError("ask_agent requires a non-empty question or approval_id")
        if self.task in ("inspect_upload", "load_dataset") and not (self.upload_path and self.upload_path.strip()):
            raise ValueError(f"{self.task} requires a non-empty upload_path")
        if self.task == "dataset_query" and not (self.query and self.query.strip()):
            raise ValueError("dataset_query requires a non-empty query")
        if self.task == "dataset_stats" and not (self.action and self.action.strip()):
            raise ValueError("dataset_stats requires a non-empty action")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "target": self.target,
            "rebuild": self.rebuild,
            "run_tests": self.run_tests,
            "data_scope": self.data_scope,
            "require_quality_report": self.require_quality_report,
            "question": self.question,
            "upload_path": self.upload_path,
            "session_id": self.session_id,
            "current_dataset": self.current_dataset,
            "query": self.query,
            "action": self.action,
            "column": self.column,
            "approval_id": self.approval_id,
            "use_llm_semantics": self.use_llm_semantics,
            "split_proposal": self.split_proposal,
        }


RECOMMENDED_EXPERIMENT_SPLITS = [
    {
        "id": "long_history_sunspot_only",
        "recommended_tables": ["data/processed/cycle_features.csv"],
        "use_for": "Long historical baseline and cycle morphology experiments.",
        "caution": "Use primary sunspot and cycle metadata fields only.",
    },
    {
        "id": "f107_era_proxy",
        "recommended_tables": ["data/processed/clean_monthly_timeseries.csv", "data/processed/cycle_features.csv"],
        "use_for": "Modern-era F10.7 relationship and drift diagnostics.",
        "caution": "Does not support all-cycle claims before F10.7 coverage.",
    },
    {
        "id": "hemispheric_1940plus",
        "recommended_tables": ["data/processed/clean_monthly_timeseries.csv", "data/processed/cycle_features.csv"],
        "use_for": "North-south asymmetry diagnostics from 1940 onward.",
        "caution": "1940-1991 is real external calibrated observation, not SILSO official hemispheric product.",
    },
    {
        "id": "official_hemisphere_1992plus",
        "recommended_tables": ["data/processed/clean_monthly_timeseries.csv"],
        "use_for": "Official SILSO hemispheric sunspot analysis.",
        "caution": "Shorter coverage than long-history sunspot experiments.",
    },
    {
        "id": "wso_era_polar",
        "recommended_tables": [
            "data/processed/cycle_features.csv",
            "data/processed/cycle_hale_wso_features.csv",
            "data/processed/cycle_hale_wso_sensitivity.csv",
        ],
        "use_for": "Polar precursor and Hale polarity diagnostics.",
        "caution": "Small WSO-era sample; use as mechanism evidence, not all-cycle proof.",
    },
    {
        "id": "goes_era_flare",
        "recommended_tables": [
            "data/processed/goes_xrs_monthly_features.csv",
            "data/processed/cycle_flare_features.csv",
        ],
        "use_for": "High-activity flare diagnostics from GOES XRS legacy reports.",
        "caution": "Auxiliary event proxy only; do not replace long-term cycle evidence.",
    },
]


REQUIRED_OUTPUTS = [
    "data/processed/agent_output.json",
    "data/processed/clean_monthly_timeseries.csv",
    "data/processed/cycle_features.csv",
    "data/processed/data_quality_report.json",
    "data/processed/drift_report.json",
    "data/processed/feature_registry.json",
    "data/processed/data_lineage_manifest.json",
]
