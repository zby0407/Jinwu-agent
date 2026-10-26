"""Persistent enable/disable state for curated observational data sources.

The catalog in ``solar_data_catalog.py`` is code-owned; user-facing on/off
state lives here as a small JSON file so toggles survive restarts without
touching the repository. Only non-default intent needs to be stored, but we
keep every explicit decision so the settings UI can show "disabled by user"
versus "never touched".
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from .paths import DATA_DIR

SCHEMA_VERSION = 1
_DATASET_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{2,80}")


def default_settings_path() -> Path:
    override = os.getenv("JW_DATA_SOURCES_SETTINGS_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    return DATA_DIR / "data_sources.json"


def load_source_settings(path: str | Path | None = None) -> dict[str, bool]:
    """Return ``dataset_id -> enabled``; a missing/corrupt file means all-on."""

    settings_path = Path(path) if path else default_settings_path()
    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    sources = payload.get("sources") if isinstance(payload, dict) else None
    if not isinstance(sources, dict):
        return {}
    result: dict[str, bool] = {}
    for key, value in sources.items():
        if isinstance(value, dict) and isinstance(value.get("enabled"), bool):
            result[str(key)] = value["enabled"]
    return result


def is_source_enabled(dataset_id: str, path: str | Path | None = None) -> bool:
    return load_source_settings(path).get(str(dataset_id), True)


def set_source_enabled(
    dataset_id: str,
    enabled: bool,
    path: str | Path | None = None,
) -> dict[str, bool]:
    """Persist one toggle and return the full ``dataset_id -> enabled`` map."""

    dataset_id = str(dataset_id).strip()
    if not _DATASET_ID_PATTERN.fullmatch(dataset_id):
        raise ValueError(f"invalid dataset id: {dataset_id!r}")
    settings_path = Path(path) if path else default_settings_path()
    settings = load_source_settings(settings_path)
    settings[dataset_id] = bool(enabled)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "sources": {key: {"enabled": value} for key, value in sorted(settings.items())},
    }
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        dir=str(settings_path.parent), prefix=f".{settings_path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, settings_path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return dict(settings)


__all__ = [
    "default_settings_path",
    "is_source_enabled",
    "load_source_settings",
    "set_source_enabled",
]
