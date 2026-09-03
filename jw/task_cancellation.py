"""Task-scoped cancellation checks shared by agents and host recovery paths."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .workspaces import workspace_root_from_config


def task_cancellation_receipt(
    config: Mapping[str, Any] | None,
) -> Path | None:
    """Return the task cancellation receipt path when the workspace is bound."""

    try:
        return workspace_root_from_config(config) / "receipts" / "task_cancelled.json"
    except (OSError, RuntimeError, ValueError):
        return None


def task_is_cancelled(config: Mapping[str, Any] | None) -> bool:
    """Report whether the user has persistently cancelled this task."""

    receipt = task_cancellation_receipt(config)
    try:
        return receipt is not None and receipt.is_file()
    except OSError:
        return False


__all__ = ["task_cancellation_receipt", "task_is_cancelled"]
