"""Resolve versioned JW resources for source checkouts and desktop bundles.

A source checkout keeps ``jw/``, ``research/``, ``src/`` and ``webui/`` in one
repository. The desktop application instead ships the same versioned resources
under ``<app resources>/runtime/project`` and a prebuilt WebUI under
``<app resources>/runtime/webui``. The Electron supervisor exports explicit
environment variables so Python never has to infer a repository layout from its
own installation path.

Environment overrides (all optional in a source checkout):

``JW_PROJECT_ROOT``
    Directory containing the versioned resource trees (``research/`` and
    ``src/``).
``JW_WEBUI_DIST``
    The Next.js standalone server file, or its containing ``dist`` directory.
``JW_NODE_BIN``
    Absolute path to the Node.js executable bundled with the desktop app.
``JW_RUNTIME_ROOT``
    Root of the bundled runtime, used for diagnostics and manifests.
``JW_LANGGRAPH_COMMAND_JSON``
    JSON array describing how to invoke the LangGraph CLI, for example
    ``[".../python3", "-m", "langgraph_cli"]``. This avoids relying on console
    script shebangs after relocating a bundled interpreter.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name)
    if not raw:
        return None
    return Path(raw).expanduser()


def project_root() -> Path:
    """Return the root that owns versioned ``research/`` and ``src/`` resources."""
    override = _env_path("JW_PROJECT_ROOT")
    if override is not None:
        return override
    return _REPOSITORY_ROOT


def src_root() -> Path:
    """Return the Python source tree imported by research workflow modules."""
    return project_root() / "src"


def runtime_root() -> Path:
    """Return the desktop runtime root, or the project root in a source checkout."""
    override = _env_path("JW_RUNTIME_ROOT")
    if override is not None:
        return override
    return project_root()


def webui_dist_file() -> Path:
    """Return the Next.js standalone server entrypoint."""
    override = _env_path("JW_WEBUI_DIST")
    if override is not None:
        if override.is_dir() or override.suffix.lower() != ".js":
            return override / "server.js"
        return override
    return project_root() / "webui" / "dist" / "server.js"


def _is_executable_file(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def node_binary() -> str | None:
    """Locate Node.js, preferring the runtime bundled with the desktop app."""
    override = _env_path("JW_NODE_BIN")
    if override is not None:
        return str(override) if _is_executable_file(override) else None
    return shutil.which("node")


def langgraph_command() -> list[str] | None:
    """Return an explicitly configured LangGraph command, if one is provided.

    Raises:
        ValueError: If the override is malformed. A packaged app with a broken
            command override must fail actionably instead of silently falling
            back to an unrelated system installation.
    """
    raw = os.environ.get("JW_LANGGRAPH_COMMAND_JSON")
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("JW_LANGGRAPH_COMMAND_JSON must be valid JSON") from exc
    if (
        not isinstance(parsed, list)
        or not parsed
        or not all(isinstance(part, str) and part for part in parsed)
    ):
        raise ValueError(
            "JW_LANGGRAPH_COMMAND_JSON must be a non-empty JSON array of strings"
        )
    return parsed


# Resolved once for modules that historically exposed a module-level constant.
# The desktop supervisor sets the environment before importing ``jw``, so these
# values are stable for a packaged process. New code should prefer the
# functions above when runtime reconfiguration matters.
PROJECT_ROOT = project_root()
SRC_DIR = src_root()
WEBUI_DIST = webui_dist_file()
