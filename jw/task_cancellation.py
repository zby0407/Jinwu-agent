"""Task-scoped cancellation checks shared by agents and host recovery paths."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
import datetime
import json
import time

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
        if receipt is None or not receipt.is_file():
            return False
        try:
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            cutoff = float(
                payload.get("cancelled_at_epoch_ms")
                or datetime.datetime.fromisoformat(
                    payload["cancelled_at"].replace("Z", "+00:00")
                ).timestamp()
                * 1000
            )
            started = (
                (config or {}).get("configurable", {}).get("task_turn_started_at_ms")
            )
            # A newly submitted user turn may resume; all older child work
            # remains cancelled. Legacy/malformed markers fail closed.
            if isinstance(started, (int, float)) and not isinstance(started, bool):
                return not (cutoff < started <= time.time() * 1000 + 5000)
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            pass
        return True
    except OSError:
        return False


__all__ = ["task_cancellation_receipt", "task_is_cancelled"]
