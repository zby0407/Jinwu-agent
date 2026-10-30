"""Run-directory and ledger persistence helpers.

A run lives entirely inside the task workspace under
``scientific_data/runs/<run_id>/`` so every artifact is hash-addressable and a
rerun can reuse stage receipts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contracts import (
    SCHEMA_VERSION,
    DataRequirementContract,
    IntegrationLedger,
    StageState,
    new_run_id,
    utc_now,
)

__all__ = [
    "LEDGER_NAME",
    "load_ledger",
    "run_dirs",
    "save_ledger",
    "start_run",
    "workspace_run_dir",
]

LEDGER_NAME = "ledger.json"
RUNS_SUBDIR = Path("scientific_data") / "runs"


def workspace_run_dir(workspace: str | Path, run_id: str) -> Path:
    """Directory of one run inside the task workspace."""
    return Path(workspace) / RUNS_SUBDIR / run_id


def run_dirs(workspace: str | Path) -> Path:
    """Root directory holding every run of this workspace."""
    return Path(workspace) / RUNS_SUBDIR


def start_run(
    workspace: str | Path,
    contract: DataRequirementContract,
    *,
    run_id: str | None = None,
) -> IntegrationLedger:
    """Create (or rehydrate) a run directory and its ledger."""
    root = Path(workspace)
    identifier = run_id or new_run_id()
    directory = workspace_run_dir(root, identifier)
    ledger_path = directory / LEDGER_NAME
    if ledger_path.is_file():
        ledger = load_ledger(ledger_path)
        ledger.workspace = str(root)
        return ledger
    for sub in ("sources", "tables", "figures", "reports", "human"):
        (directory / sub).mkdir(parents=True, exist_ok=True)
    ledger = IntegrationLedger(
        run_id=identifier, contract=contract, workspace=str(root)
    )
    ledger.mark_stage(
        "init",
        StageState.COMPLETE,
        counts={"directories": 5},
        artifacts=[str(ledger_path)],
        input_fingerprint=contract.fingerprint,
    )
    save_ledger(ledger, directory)
    return ledger


def save_ledger(ledger: IntegrationLedger, directory: str | Path | None = None) -> str:
    """Persist the ledger next to its run directory (atomic replace)."""
    target = (
        Path(directory)
        if directory is not None
        else Path(workspace_run_dir(ledger.workspace or ".", ledger.run_id))
    )
    target.mkdir(parents=True, exist_ok=True)
    payload = ledger.to_dict()
    payload["schema_version"] = SCHEMA_VERSION
    payload["saved_at"] = utc_now()
    path = target / LEDGER_NAME
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)
    return str(path)


def load_ledger(path: str | Path) -> IntegrationLedger:
    """Read a ledger written by :func:`save_ledger`."""
    raw: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    ledger = IntegrationLedger.from_dict(raw)
    if not ledger.workspace and Path(path).name == LEDGER_NAME:
        ledger.workspace = str(Path(path).resolve().parent)
    return ledger
