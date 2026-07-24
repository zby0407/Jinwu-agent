"""Persistent project/run/thread workspace bindings.

The deployment process has one *base* workspace, but each research thread gets
its own run directory under that base.  The binding registry lives outside the
workspace so the WebUI and backend can resolve the same thread after either
process restarts.

New task layout::

    <base>/projects/<project_id>/
      project.json
      shared/{assets,knowledge,decisions}/
      runs/<run_id>/
        task.json
        input_manifest.json
        context_snapshot.json
        inputs/ work/ outputs/ receipts/

Only ``runs/<run_id>`` is mounted as the agent's default ``/``.  Stable project
material is available through the explicit ``/project/`` route, which points to
``shared/`` and therefore does not expose previous run scratch space.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2
DEFAULT_PROJECT_ID = "default"
_LOCK = threading.RLock()
_BINDING_CACHE: dict[tuple[str, str], WorkspaceBinding] = {}
_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _config_dir() -> Path:
    override = os.environ.get("JW_WORKSPACE_BINDINGS_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    root = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return root / "jw" / "workspace_bindings"


def binding_path(thread_id: str, base_workspace: str | Path) -> Path:
    """Return a traversal-safe registry path for ``(base, thread_id)``."""

    base = str(Path(base_workspace).expanduser().resolve())
    digest = hashlib.sha256(f"{base}\0{thread_id}".encode()).hexdigest()
    return _config_dir() / f"{digest}.json"


def _slug(value: str, *, fallback: str, limit: int = 48) -> str:
    cleaned = _SAFE_ID.sub("-", value.strip()).strip("-._")
    return (cleaned or fallback)[:limit]


def _run_id(thread_id: str) -> str:
    prefix = _slug(thread_id, fallback="thread", limit=18)
    digest = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()[:8]
    return f"run_{prefix}_{digest}"


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, path)


@dataclass(frozen=True, slots=True)
class WorkspaceBinding:
    schema_version: int
    thread_id: str
    project_id: str
    run_id: str
    base_workspace: str
    project_root: str
    project_shared: str
    workspace: str
    legacy: bool
    created_at: str

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> WorkspaceBinding:
        required = {
            "thread_id",
            "project_id",
            "run_id",
            "base_workspace",
            "project_root",
            "project_shared",
            "workspace",
            "created_at",
        }
        missing = required.difference(raw)
        if missing:
            raise ValueError(f"workspace binding is missing: {sorted(missing)}")
        return cls(
            schema_version=int(raw.get("schema_version", SCHEMA_VERSION)),
            thread_id=str(raw["thread_id"]),
            project_id=str(raw["project_id"]),
            run_id=str(raw["run_id"]),
            base_workspace=str(raw["base_workspace"]),
            project_root=str(raw["project_root"]),
            project_shared=str(raw["project_shared"]),
            workspace=str(raw["workspace"]),
            legacy=bool(raw.get("legacy", False)),
            created_at=str(raw["created_at"]),
        )


def _binding_cache_key(thread_id: str, base_workspace: str | Path) -> tuple[str, str]:
    base = os.fspath(base_workspace)
    if not os.path.isabs(base):
        raise ValueError("base_workspace must be absolute for cache access")
    return (os.path.normpath(base), thread_id)


def get_cached_binding(
    thread_id: str, base_workspace: str | Path
) -> WorkspaceBinding | None:
    """Return an in-process binding without performing filesystem I/O.

    Async graph nodes use this path because DeepAgents' backend factory is a
    synchronous callback even when invoked from ``abefore_agent``.  The custom
    checkpointer initializes the cache from a worker thread before graph nodes
    run, so the factory never needs to call ``mkdir`` or read JSON on the event
    loop.
    """

    with _LOCK:
        return _BINDING_CACHE.get(_binding_cache_key(thread_id, base_workspace))


def preload_bindings(base_workspace: str | Path) -> int:
    """Load persisted bindings for one base workspace into the process cache.

    Called during graph construction/startup, before LangGraph's async run loop
    begins.  This is what lets an existing thread resume immediately after a
    backend restart without synchronous registry reads inside ``abefore_agent``.
    Invalid, stale-v1, and other-project bindings are ignored.
    """

    base = str(Path(base_workspace).expanduser().resolve())
    loaded = 0
    with _LOCK:
        for path in _config_dir().glob("*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    continue
                binding = WorkspaceBinding.from_dict(raw)
                if (
                    binding.schema_version != SCHEMA_VERSION
                    or binding.base_workspace != base
                    or binding_path(binding.thread_id, base) != path
                ):
                    continue
                _BINDING_CACHE[_binding_cache_key(binding.thread_id, base)] = binding
                loaded += 1
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
    return loaded


def read_binding(thread_id: str, base_workspace: str | Path) -> WorkspaceBinding | None:
    base = str(Path(base_workspace).expanduser().resolve())
    cached = get_cached_binding(thread_id, base)
    if cached is not None:
        return cached
    path = binding_path(thread_id, base)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        binding = WorkspaceBinding.from_dict(raw)
        if binding.thread_id != thread_id or binding.base_workspace != base:
            return None
        with _LOCK:
            _BINDING_CACHE[_binding_cache_key(thread_id, base)] = binding
        return binding
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _write_initial_file(path: Path, payload: Mapping[str, Any]) -> None:
    if not path.exists():
        _atomic_write_json(path, payload)


def _ensure_project_layout(project_root: Path, project_id: str) -> Path:
    shared = project_root / "shared"
    for directory in (
        shared / "assets",
        shared / "knowledge",
        shared / "decisions",
        project_root / "runs",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    _write_initial_file(
        project_root / "project.json",
        {
            "schema_version": SCHEMA_VERSION,
            "project_id": project_id,
            "created_at": utc_now(),
            "continuity_policy": {
                "shared_root": "shared",
                "task_runs_are_isolated": True,
                "prior_runs_are_not_implicitly_loaded": True,
            },
        },
    )
    return shared


def _ensure_run_layout(binding: WorkspaceBinding, first_request: str = "") -> None:
    run_root = Path(binding.workspace)
    for name in ("inputs", "work", "outputs", "receipts"):
        (run_root / name).mkdir(parents=True, exist_ok=True)
    task_path = run_root / "task.json"
    if not task_path.exists():
        _atomic_write_json(
            task_path,
            {
                "schema_version": SCHEMA_VERSION,
                "thread_id": binding.thread_id,
                "project_id": binding.project_id,
                "run_id": binding.run_id,
                "research_question": first_request.strip(),
                "status": "active",
                "created_at": binding.created_at,
            },
        )
    elif first_request.strip():
        try:
            task = json.loads(task_path.read_text(encoding="utf-8"))
            if (
                isinstance(task, dict)
                and not str(task.get("research_question", "")).strip()
            ):
                task["research_question"] = first_request.strip()
                _atomic_write_json(task_path, task)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    _write_initial_file(
        run_root / "input_manifest.json",
        {
            "schema_version": SCHEMA_VERSION,
            "thread_id": binding.thread_id,
            "inputs": [],
        },
    )
    _write_initial_file(
        run_root / "context_snapshot.json",
        {
            "schema_version": SCHEMA_VERSION,
            "thread_id": binding.thread_id,
            "project_id": binding.project_id,
            "project_shared_virtual_path": "/project/",
            "imported_context": [],
            "prior_runs_implicitly_loaded": False,
        },
    )


def ensure_thread_workspace(
    thread_id: str,
    base_workspace: str | Path,
    *,
    project_id: str = DEFAULT_PROJECT_ID,
    first_request: str = "",
) -> WorkspaceBinding:
    """Return the stable binding for a thread, creating a clean run if needed."""

    thread_id = str(thread_id).strip()
    if not thread_id:
        raise ValueError("thread_id is required for a task workspace")
    base = Path(base_workspace).expanduser().resolve()
    if not base.is_dir():
        raise ValueError(f"base workspace is not a directory: {base}")
    with _LOCK:
        existing = read_binding(thread_id, base)
        if existing is not None:
            if not existing.legacy:
                _ensure_run_layout(existing, first_request)
            _BINDING_CACHE[_binding_cache_key(thread_id, base)] = existing
            return existing

        project = _slug(project_id, fallback=DEFAULT_PROJECT_ID)
        project_root = base / "projects" / project
        project_shared = _ensure_project_layout(project_root, project)
        run_id = _run_id(thread_id)
        created_at = utc_now()
        binding = WorkspaceBinding(
            schema_version=SCHEMA_VERSION,
            thread_id=thread_id,
            project_id=project,
            run_id=run_id,
            base_workspace=str(base),
            project_root=str(project_root),
            project_shared=str(project_shared),
            workspace=str(project_root / "runs" / run_id),
            legacy=False,
            created_at=created_at,
        )
        _ensure_run_layout(binding, first_request)
        _atomic_write_json(binding_path(thread_id, base), asdict(binding))
        _BINDING_CACHE[_binding_cache_key(thread_id, base)] = binding
        return binding


def create_legacy_binding(
    thread_id: str, base_workspace: str | Path
) -> WorkspaceBinding:
    """Bind a pre-migration thread to its original global workspace."""

    base = Path(base_workspace).expanduser().resolve()
    with _LOCK:
        existing = read_binding(thread_id, base)
        if existing is not None:
            _BINDING_CACHE[_binding_cache_key(thread_id, base)] = existing
            return existing
        created_at = utc_now()
        binding = WorkspaceBinding(
            schema_version=SCHEMA_VERSION,
            thread_id=thread_id,
            project_id="legacy",
            run_id="legacy",
            base_workspace=str(base),
            project_root=str(base),
            project_shared=str(base),
            workspace=str(base),
            legacy=True,
            created_at=created_at,
        )
        _atomic_write_json(binding_path(thread_id, base), asdict(binding))
        _BINDING_CACHE[_binding_cache_key(thread_id, base)] = binding
        return binding


def bootstrap_legacy_bindings(
    base_workspace: str | Path,
    sessions_db: str | Path,
) -> dict[str, int | str]:
    """One-time, non-destructive binding of existing checkpoint threads.

    The marker is scoped to the base workspace.  Threads that exist before the
    first isolated deployment keep seeing the old workspace; threads created
    afterwards receive a clean run directory.
    """

    base = Path(base_workspace).expanduser().resolve()
    root = _config_dir()
    marker_id = hashlib.sha256(str(base).encode("utf-8")).hexdigest()[:16]
    marker = root / f".legacy_bootstrap_v2_{marker_id}.json"
    with _LOCK:
        if marker.exists():
            return {"status": "already_bootstrapped", "created": 0}
        thread_ids: list[str] = []
        db = Path(sessions_db).expanduser()
        if db.is_file():
            try:
                with sqlite3.connect(str(db), timeout=30.0) as conn:
                    exists = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='checkpoints'"
                    ).fetchone()
                    if exists:
                        columns = {
                            str(row[1])
                            for row in conn.execute(
                                "PRAGMA table_info(checkpoints)"
                            ).fetchall()
                        }
                        if "metadata" in columns:
                            query = """
                                SELECT DISTINCT thread_id
                                FROM checkpoints
                                WHERE thread_id IS NOT NULL
                                  AND (
                                    json_extract(metadata, '$.workspace_dir') = ?
                                    OR json_extract(metadata, '$.base_workspace_dir') = ?
                                  )
                            """
                            rows = conn.execute(
                                query, (str(base), str(base))
                            ).fetchall()
                        else:
                            # Narrow compatibility path for early/test schemas
                            # that predate ownership metadata.
                            rows = conn.execute(
                                "SELECT DISTINCT thread_id FROM checkpoints "
                                "WHERE thread_id IS NOT NULL"
                            ).fetchall()
                        thread_ids = [str(row[0]) for row in rows if row[0]]
            except sqlite3.Error:
                # Do not stamp the marker on a transient DB failure; retry next boot.
                return {"status": "database_error", "created": 0}
        created = 0
        for thread_id in thread_ids:
            if read_binding(thread_id, base) is None:
                create_legacy_binding(thread_id, base)
                created += 1
        _atomic_write_json(
            marker,
            {
                "schema_version": SCHEMA_VERSION,
                "base_workspace": str(base),
                "sessions_db": str(db),
                "thread_count": len(thread_ids),
                "created_bindings": created,
                "completed_at": utc_now(),
            },
        )
        return {"status": "bootstrapped", "created": created}


def scope_thread_id(config: Mapping[str, Any] | None) -> str:
    configurable = config.get("configurable", {}) if isinstance(config, Mapping) else {}
    if not isinstance(configurable, Mapping):
        return ""
    for key in (
        "workspace_thread_id",
        "parent_thread_id",
        "origin_thread_id",
        "thread_id",
    ):
        value = configurable.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def project_id_from_config(config: Mapping[str, Any] | None) -> str:
    configurable = config.get("configurable", {}) if isinstance(config, Mapping) else {}
    if isinstance(configurable, Mapping):
        value = configurable.get("project_id")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return DEFAULT_PROJECT_ID


def binding_from_config(
    config: Mapping[str, Any] | None,
    base_workspace: str | Path | None = None,
) -> WorkspaceBinding | None:
    """Resolve the task binding carried by a LangGraph tool/runtime config.

    Tool calls run in worker threads, so a persisted-registry fallback is safe
    here.  Backend factories must continue to use :func:`get_cached_binding`
    because those synchronous callbacks may execute on the async event loop.
    """

    if base_workspace is None:
        from . import paths as paths_mod

        base_workspace = paths_mod.WORKSPACE_ROOT
    thread_id = scope_thread_id(config)
    if not thread_id:
        return None
    return get_cached_binding(thread_id, base_workspace) or read_binding(
        thread_id, base_workspace
    )


def workspace_root_from_config(
    config: Mapping[str, Any] | None,
    base_workspace: str | Path | None = None,
) -> Path:
    """Return the concrete task workspace for a tool invocation.

    Deployed threaded calls must have an initialized binding; silently falling
    back to the deployment workspace would reintroduce cross-task writes.  CLI
    and direct-library calls without a thread keep the historical active
    workspace behavior.
    """

    if base_workspace is None:
        from . import paths as paths_mod

        base_workspace = paths_mod.WORKSPACE_ROOT
    thread_id = scope_thread_id(config)
    if not thread_id:
        from .paths import default_workspace_dir

        return default_workspace_dir().resolve()
    binding = binding_from_config(config, base_workspace)
    if binding is None:
        raise RuntimeError(
            "Task workspace binding was not initialized for contract tool "
            f"execution (thread_id={thread_id})."
        )
    return Path(binding.workspace).resolve()


def workspace_context_key(config: Mapping[str, Any] | None) -> str:
    """Stable key for per-task in-process contract state."""

    return scope_thread_id(config) or "__default__"


def resolve_scoped_path(
    value: str,
    config: Mapping[str, Any] | None,
    *,
    allow_project: bool = False,
) -> Path:
    """Resolve an agent-visible path without permitting task-root escape."""

    raw = value.strip()
    if not raw:
        raise ValueError("path must not be empty")
    binding = binding_from_config(config)
    if raw == "/project" or raw.startswith("/project/"):
        if not allow_project or binding is None or binding.legacy:
            raise ValueError("/project is not allowed for this tool call")
        root = Path(binding.project_shared).resolve()
        relative = raw.removeprefix("/project").lstrip("/")
    else:
        root = workspace_root_from_config(config)
        relative = raw.lstrip("/")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("path escaped the task workspace") from exc
    return candidate


def first_human_request(state: Mapping[str, Any] | None) -> str:
    messages = state.get("messages", []) if isinstance(state, Mapping) else []
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
        return ""
    for message in messages:
        role = (
            message.get("type") or message.get("role")
            if isinstance(message, Mapping)
            else getattr(message, "type", None) or getattr(message, "role", None)
        )
        if role not in {"human", "user"}:
            continue
        content = (
            message.get("content", "")
            if isinstance(message, Mapping)
            else getattr(message, "content", "")
        )
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            text = " ".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, Mapping) and part.get("text")
            ).strip()
            if text:
                return text
    return ""


def ensure_workspace_for_config(
    config: Mapping[str, Any] | None,
    base_workspace: str | Path,
    *,
    state: Mapping[str, Any] | None = None,
) -> WorkspaceBinding | None:
    thread_id = scope_thread_id(config)
    if not thread_id:
        return None
    return ensure_thread_workspace(
        thread_id,
        base_workspace,
        project_id=project_id_from_config(config),
        first_request=first_human_request(state),
    )
