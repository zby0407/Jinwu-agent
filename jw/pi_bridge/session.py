"""Read pi session files to reconstruct conversation state."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class PiSessionReader:
    """Parse pi's JSONL session files into a simple message list."""

    def __init__(self, session_dir: str | Path) -> None:
        self.session_dir = Path(session_dir)

    def find_session_file(self, session_id: str) -> Path | None:
        """Return the newest session file matching ``session_id``.

        pi names files like ``<iso-timestamp>_<session-id>.jsonl``.
        """
        self.session_dir.mkdir(parents=True, exist_ok=True)
        matches = [
            p
            for p in self.session_dir.iterdir()
            if p.is_file() and p.suffix == ".jsonl" and session_id in p.name
        ]
        if not matches:
            return None
        return max(matches, key=lambda p: p.stat().st_mtime)

    def read_messages(self, session_id: str) -> list[dict[str, Any]]:
        """Read user/assistant/toolResult messages from the session file."""
        path = self.find_session_file(session_id)
        if path is None:
            return []
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("Could not read pi session file %s: %s", path, exc)
            return []

        messages: list[dict[str, Any]] = []
        for line in text.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") != "message":
                continue
            msg = entry.get("message") or {}
            role = msg.get("role")
            if role not in ("user", "assistant", "toolResult"):
                continue
            content = msg.get("content")
            messages.append(
                {
                    "role": role,
                    "content": content,
                    "timestamp": msg.get("timestamp"),
                }
            )
        return messages
