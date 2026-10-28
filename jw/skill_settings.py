"""Persistent enable/disable state for Agent Skills.

The skill catalog itself is code-owned (``jw/subagents/skill_registry.json``
plus the bundled ``SKILL.md`` directories); the user's on/off choice per skill
is application state, so it lives in ``DATA_DIR`` — that way the desktop WebUI
and the Python runtime agree on one single source of truth
(``<DATA_DIR>/skill_settings.json``) instead of each keeping a private copy.

File shape::

    {"version": 1, "disabled": ["<skill-dir-name>", ...]}

A missing file means "every skill is enabled", which is also the initial state
for a fresh install.  Reads never raise: an unreadable or malformed file is
logged and treated as "nothing disabled" so a bad hand edit cannot brick the
agent.  Writes may raise (read-only data dir, disk full, ...); the caller
decides what to do with that.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import paths

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
SKILL_SETTINGS_FILENAME = "skill_settings.json"

_DISABLED_KEY = "disabled"


def skill_settings_path() -> Path:
    """Return ``<DATA_DIR>/skill_settings.json``.

    Resolved on every call (never frozen at import time) so tests and the
    desktop launcher can re-point ``JW_DATA_DIR`` after import.
    """
    return paths.DATA_DIR / SKILL_SETTINGS_FILENAME


def _resolve_path(path: Path | None) -> Path:
    return Path(path) if path is not None else skill_settings_path()


def _load(settings_path: Path) -> dict[str, Any] | None:
    """Read the raw payload.

    Returns ``{}`` when the file does not exist (all skills enabled), and
    ``None`` when the file is unreadable or not a JSON object (a warning is
    logged).  ``None`` is distinct from ``{}`` only so callers can decide
    whether to start from a clean slate while writing.
    """

    try:
        raw = settings_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.debug("No skill settings at %s; every skill is enabled.", settings_path)
        return {}
    except OSError as exc:
        logger.warning("Could not read skill settings %s: %s", settings_path, exc)
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("Ignoring corrupt skill settings %s: %s", settings_path, exc)
        return None
    if not isinstance(payload, dict):
        logger.warning(
            "Ignoring skill settings %s: expected a JSON object, got %s",
            settings_path,
            type(payload).__name__,
        )
        return None
    return payload


def _disabled_from_payload(payload: dict[str, Any], settings_path: Path) -> set[str]:
    """Coerce the ``disabled`` field into a name set; bad shapes log and drop."""

    raw = payload.get(_DISABLED_KEY, [])
    if not isinstance(raw, list):
        logger.warning(
            "%s: 'disabled' must be a list of strings; treating it as empty.",
            settings_path,
        )
        return set()
    names: set[str] = set()
    for entry in raw:
        if not isinstance(entry, str):
            logger.warning(
                "%s: ignoring non-string entry %r in 'disabled'.", settings_path, entry
            )
            continue
        name = entry.strip()
        if name:
            names.add(name)
    return names


def disabled_skill_names(path: Path | None = None) -> set[str]:
    """Return the set of disabled skill names (never raises)."""

    settings_path = _resolve_path(path)
    payload = _load(settings_path)
    if payload is None:
        return set()
    return _disabled_from_payload(payload, settings_path)


def set_skill_enabled(name: str, enabled: bool, *, path: Path | None = None) -> None:
    """Persist one toggle, preserving unknown fields in the settings file.

    The write is atomic (same-directory temp file + ``os.replace``) so a crash
    or a concurrent reader never observes a half-written file.
    """

    skill_name = str(name).strip()
    if not skill_name:
        raise ValueError("skill name must be a non-empty string")
    settings_path = _resolve_path(path)
    payload = _load(settings_path) or {}
    disabled = _disabled_from_payload(payload, settings_path)
    if enabled:
        disabled.discard(skill_name)
    else:
        disabled.add(skill_name)
    # Only the disabled list is owned here; keep any other keys the WebUI or a
    # future schema may have stored.
    payload[_DISABLED_KEY] = sorted(disabled)
    payload["version"] = SCHEMA_VERSION
    _write_atomic(settings_path, payload)


def _write_atomic(settings_path: Path, payload: dict[str, Any]) -> None:
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    handle, temporary = tempfile.mkstemp(
        dir=str(settings_path.parent),
        prefix=f".{settings_path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, settings_path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def filter_enabled(
    names: Iterable[str], *, disabled: set[str] | None = None
) -> list[str]:
    """Drop disabled names, preserving the caller's order.

    ``names`` are bare skill names (skill directory names such as
    ``solar-flare-forecasting``), not ``/skills/<name>`` middleware sources;
    ``skills_for_agent`` strips the prefix before calling this.  ``disabled``
    injects the decision set for tests; by default it is read from the settings
    file once per call.
    """

    excluded = disabled_skill_names() if disabled is None else set(disabled)
    return [name for name in names if name not in excluded]


__all__ = [
    "SCHEMA_VERSION",
    "SKILL_SETTINGS_FILENAME",
    "disabled_skill_names",
    "filter_enabled",
    "set_skill_enabled",
    "skill_settings_path",
]
