"""Independent execution-liveness sidecar for the research loop."""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

EXECUTION_VERSION = "research-execution-state-v1"
EXECUTION_STATUSES = {
    "running",
    "waiting_for_tool",
    "interrupted",
    "failed",
    "stopped",
}
_ACTIVE_STATUSES = {"running", "waiting_for_tool"}
_REQUIRED_FIELDS = {
    "schema_version",
    "status",
    "stage",
    "owner",
    "action",
    "reason",
    "started_at",
    "updated_at",
}


def _timestamp(value: str | None) -> str:
    if value is None:
        return datetime.now(UTC).isoformat()
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("execution timestamp must include a timezone")
    return parsed.isoformat()


class ExecutionStateStore:
    """Persist operational liveness without mutating scientific run state."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> dict[str, object] | None:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if (
            not isinstance(value, dict)
            or set(value) != _REQUIRED_FIELDS
            or value.get("schema_version") != EXECUTION_VERSION
            or value.get("status") not in EXECUTION_STATUSES
        ):
            return None
        for field in ("stage", "owner", "action", "started_at", "updated_at"):
            if not isinstance(value.get(field), str) or not str(value[field]).strip():
                return None
        if value.get("reason") is not None and not isinstance(value.get("reason"), str):
            return None
        try:
            _timestamp(str(value["started_at"]))
            _timestamp(str(value["updated_at"]))
        except ValueError:
            return None
        return value

    def _transition(
        self,
        *,
        status: str,
        stage: str,
        owner: str,
        action: str,
        reason: str | None,
        now: str | None = None,
        reset_started_at: bool = False,
    ) -> dict[str, object]:
        if status not in EXECUTION_STATUSES:
            raise ValueError(f"invalid execution status: {status}")
        if not stage.strip() or not owner.strip() or not action.strip():
            raise ValueError("stage, owner, and action must be non-empty")
        timestamp = _timestamp(now)
        previous = self._read() or {}
        record: dict[str, object] = {
            "schema_version": EXECUTION_VERSION,
            "status": status,
            "stage": stage.strip(),
            "owner": owner.strip(),
            "action": action.strip(),
            "reason": reason.strip()
            if isinstance(reason, str) and reason.strip()
            else None,
            "started_at": (
                timestamp if reset_started_at else previous.get("started_at", timestamp)
            ),
            "updated_at": timestamp,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
        return record

    def start(
        self,
        *,
        stage: str,
        owner: str,
        now: str | None = None,
    ) -> dict[str, object]:
        return self._transition(
            status="running",
            stage=stage,
            owner=owner,
            action="start",
            reason=None,
            now=now,
            reset_started_at=True,
        )

    def progress(
        self,
        *,
        stage: str,
        owner: str,
        action: str,
        now: str | None = None,
    ) -> dict[str, object]:
        return self._transition(
            status="running",
            stage=stage,
            owner=owner,
            action=action,
            reason=None,
            now=now,
        )

    def waiting_for_tool(
        self,
        *,
        stage: str,
        owner: str,
        reason: str,
        now: str | None = None,
    ) -> dict[str, object]:
        return self._transition(
            status="waiting_for_tool",
            stage=stage,
            owner=owner,
            action="tool",
            reason=reason,
            now=now,
        )

    def interrupt(
        self,
        *,
        stage: str,
        owner: str,
        reason: str,
        now: str | None = None,
    ) -> dict[str, object]:
        return self._transition(
            status="interrupted",
            stage=stage,
            owner=owner,
            action="interrupt",
            reason=reason,
            now=now,
        )

    def fail(
        self,
        *,
        stage: str,
        owner: str,
        reason: str,
        now: str | None = None,
    ) -> dict[str, object]:
        return self._transition(
            status="failed",
            stage=stage,
            owner=owner,
            action="fail",
            reason=reason,
            now=now,
        )

    def stop(
        self,
        *,
        stage: str,
        owner: str,
        reason: str,
        now: str | None = None,
    ) -> dict[str, object]:
        return self._transition(
            status="stopped",
            stage=stage,
            owner=owner,
            action="stop",
            reason=reason,
            now=now,
        )

    def snapshot(
        self,
        *,
        now: str | None = None,
        stale_after_seconds: float = 300,
    ) -> dict[str, object] | None:
        if stale_after_seconds < 0:
            raise ValueError("stale_after_seconds must be non-negative")
        record = self._read()
        if record is None or record["status"] not in _ACTIVE_STATUSES:
            return record
        observed = datetime.fromisoformat(str(record["updated_at"]))
        current = datetime.fromisoformat(_timestamp(now))
        if (current - observed).total_seconds() <= stale_after_seconds:
            return record
        return {
            **record,
            "status": "stopped",
            "reason": "heartbeat_stale",
        }
