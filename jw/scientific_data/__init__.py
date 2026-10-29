"""Scientific Data Integration kernel.

Generic, domain-pluggable pipeline that turns a research goal into an audited
multi-source CSV package: source discovery, multi-format extraction, semantic
alignment, anomaly detection, safe repair with a human confirmation loop, and a
reproducible output bundle.

The public surface is intentionally small; stages are consumed through the
``jw/tools/scientific_data.py`` tool bundle.
"""

from __future__ import annotations

from .contracts import (
    SCHEMA_VERSION,
    ArtifactIndex,
    ConfidenceBand,
    DataRequirementContract,
    EntityAlias,
    EntityRef,
    ExtractedTable,
    FieldMapping,
    IntegrationLedger,
    IssueCode,
    IssueSeverity,
    IssueState,
    MissingReason,
    ObservationField,
    ObservationRecord,
    ProvenanceRef,
    QualityIssue,
    RepairLedgerEntry,
    RepairMode,
    RetrievalState,
    SourceRecord,
    SourceRole,
    StageReceipt,
    StageState,
    ValueKind,
    content_hash,
    default_repair_mode,
    new_run_id,
)
from .domain import (
    DomainPlugin,
    EntitySpec,
    ObservableSpec,
    get_plugin,
    list_plugins,
    plugin_for_contract,
    register_plugin,
)
from .ledger_io import load_ledger, save_ledger, start_run, workspace_run_dir

__all__ = [
    "SCHEMA_VERSION",
    "ArtifactIndex",
    "ConfidenceBand",
    "DataRequirementContract",
    "DomainPlugin",
    "EntityAlias",
    "EntityRef",
    "EntitySpec",
    "ExtractedTable",
    "FieldMapping",
    "IntegrationLedger",
    "IssueCode",
    "IssueSeverity",
    "IssueState",
    "MissingReason",
    "ObservationField",
    "ObservationRecord",
    "ObservableSpec",
    "ProvenanceRef",
    "QualityIssue",
    "RepairLedgerEntry",
    "RepairMode",
    "RetrievalState",
    "SourceRecord",
    "SourceRole",
    "StageReceipt",
    "StageState",
    "ValueKind",
    "content_hash",
    "default_repair_mode",
    "get_plugin",
    "list_plugins",
    "load_ledger",
    "new_run_id",
    "plugin_for_contract",
    "register_plugin",
    "save_ledger",
    "start_run",
    "workspace_run_dir",
]
