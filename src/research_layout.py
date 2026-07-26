"""Canonical repository, research-resource, and runtime-state paths.

The repository keeps implementation and versioned contracts separate from
mutable research state:

* ``research/`` contains schemas, examples, tests, and curated resources.
* ``workspace/`` contains datasets, project runs, exports, and other state.

``JW_WORKSPACE_DIR`` may relocate the mutable workspace without changing where
versioned research resources are loaded from.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESEARCH_ROOT = PROJECT_ROOT / "research"

PLANNER_RESOURCE_ROOT = RESEARCH_ROOT / "planner"
HYPOTHESIS_RESOURCE_ROOT = RESEARCH_ROOT / "hypothesis"
EXPERIMENT_RESOURCE_ROOT = RESEARCH_ROOT / "experiment"
KNOWLEDGE_BASE_RESOURCE_ROOT = RESEARCH_ROOT / "knowledge_base"


def workspace_root() -> Path:
    """Return the mutable workspace selected for this process."""

    override = os.getenv("JW_WORKSPACE_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (PROJECT_ROOT / "workspace").resolve()


def contract_runtime_root() -> Path:
    """Return the shared root for standalone contract-agent state."""

    return workspace_root() / "runtime" / "contracts"


def contract_runs_root(agent_name: str) -> Path:
    """Return one contract agent's standalone run directory."""

    return contract_runtime_root() / agent_name / "runs"


def contract_inputs_root(agent_name: str) -> Path:
    """Return one contract agent's standalone input directory."""

    return contract_runtime_root() / agent_name / "inputs"


def knowledge_export_root() -> Path:
    """Return the live Markdown export directory for the knowledge base."""

    return workspace_root() / "knowledge_base"
