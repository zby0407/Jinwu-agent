"""Persistent project/run/thread workspace bindings.

The deployment process has one *base* workspace, but each research thread gets
its own run directory under that base.  The binding registry lives outside the
workspace so the WebUI and backend can resolve the same thread after either
process restarts.

New task layout::

    <base>/projects/<project_id>/
      project.json
      shared/{assets,knowledge,decisions,data}/
      shared/data_manifest.json
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
import shutil
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
_PROJECT_INPUT_CACHE: dict[str, tuple[dict[str, Any], ...]] = {}
_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")
_PRIMARY_DATA_SUFFIXES = frozenset(
    {
        ".arrow",
        ".csv",
        ".dat",
        ".db",
        ".feather",
        ".fits",
        ".h5",
        ".hdf5",
        ".json",
        ".jsonl",
        ".nc",
        ".ndjson",
        ".npy",
        ".npz",
        ".parquet",
        ".sqlite",
        ".sqlite3",
        ".tsv",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }
)
_REFERENCE_CODE_SUFFIXES = frozenset(
    {
        ".awk",
        ".ipynb",
        ".jl",
        ".js",
        ".m",
        ".mjs",
        ".py",
        ".r",
        ".sh",
        ".sql",
        ".ts",
    }
)
_DERIVED_DATA_PATH_PARTS = frozenset(
    {
        "artifacts",
        "figures",
        "outputs",
        "reports",
        "results",
        "runs",
        "work",
    }
)
_FIXTURE_DATA_TOKENS = frozenset(
    {
        "demo",
        "dummy",
        "example",
        "fake",
        "fixture",
        "mock",
        "sample",
        "simulated",
        "synthetic",
        "test",
        "toy",
    }
)
_DERIVED_DATA_TOKENS = frozenset(
    {
        "estimate",
        "estimated",
        "forecast",
        "forecasted",
        "prediction",
        "predictions",
    }
)
_PATH_TOKEN = re.compile(r"[A-Za-z0-9]+")


def _project_data_tokens(relative: Path) -> set[str]:
    """Return filename/path tokens used for conservative data classification."""

    tokens: set[str] = set()
    for part in relative.parts:
        tokens.update(token.casefold() for token in _PATH_TOKEN.findall(part))
    return tokens


def _project_data_role(relative: Path) -> str:
    """Classify legacy data without treating code/results as model inputs."""

    parts = {part.casefold() for part in relative.parts[:-1]}
    if parts & _DERIVED_DATA_PATH_PARTS:
        return "derived_artifact"
    name = relative.name.casefold()
    if name.endswith(".provenance.json"):
        return "provenance"
    tokens = _project_data_tokens(relative)
    if tokens & _FIXTURE_DATA_TOKENS:
        return "test_fixture"
    if tokens & _DERIVED_DATA_TOKENS:
        return "derived_artifact"
    suffix = relative.suffix.casefold()
    if suffix in _REFERENCE_CODE_SUFFIXES:
        return "reference_code"
    if suffix in _PRIMARY_DATA_SUFFIXES:
        return "primary_data"
    return "project_reference"


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
    return (str(Path(base_workspace).expanduser().resolve()), thread_id)


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


def get_cached_binding_for_resolved_base(
    thread_id: str,
    resolved_base_workspace: str,
) -> WorkspaceBinding | None:
    """Return a cached binding when the caller already canonicalized the base.

    This variant performs no filesystem work.  It exists for synchronous
    backend callbacks that DeepAgents may invoke on an async event loop.
    """

    with _LOCK:
        return _BINDING_CACHE.get((resolved_base_workspace, thread_id))


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
                _BINDING_CACHE[(base, binding.thread_id)] = binding
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
            _BINDING_CACHE[(base, thread_id)] = binding
        return binding
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _write_initial_file(path: Path, payload: Mapping[str, Any]) -> None:
    if not path.exists():
        _atomic_write_json(path, payload)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sync_legacy_shared_data(base_workspace: Path, shared: Path) -> None:
    """Import legacy ``<workspace>/data`` files into stable project data.

    Existing project files are never overwritten. Differing destinations are
    recorded as conflicts so task isolation cannot silently destroy either
    copy.
    """

    source_root = base_workspace / "data"
    destination_root = shared / "data"
    destination_root.mkdir(parents=True, exist_ok=True)
    if not source_root.is_dir() or source_root.resolve() == destination_root.resolve():
        return

    previous_files: dict[str, Mapping[str, Any]] = {}
    manifest_path = shared / "data_manifest.json"
    try:
        previous_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        raw_previous_files = (
            previous_manifest.get("files", [])
            if isinstance(previous_manifest, Mapping)
            else []
        )
        if isinstance(raw_previous_files, list):
            previous_files = {
                str(item["path"]): item
                for item in raw_previous_files
                if isinstance(item, Mapping)
                and isinstance(item.get("path"), str)
                and isinstance(item.get("sha256"), str)
            }
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        pass

    files: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    source_paths: set[str] = set()
    for source in sorted(source_root.rglob("*"), key=lambda path: path.as_posix()):
        if source.is_symlink() or not source.is_file():
            continue
        relative = source.relative_to(source_root)
        source_paths.add(relative.as_posix())
        destination = destination_root / relative
        try:
            source_sha256 = _sha256_file(source)
            source_size = source.stat().st_size
            status = "present"
            if destination.exists():
                if not destination.is_file() or destination.is_symlink():
                    conflicts.append(
                        {
                            "path": relative.as_posix(),
                            "reason": "destination_is_not_a_regular_file",
                        }
                    )
                    continue
                destination_sha256 = _sha256_file(destination)
                if destination_sha256 != source_sha256:
                    conflicts.append(
                        {
                            "path": relative.as_posix(),
                            "reason": "destination_content_differs",
                            "source_sha256": source_sha256,
                            "destination_sha256": destination_sha256,
                        }
                    )
                    continue
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                status = "imported"
            files.append(
                {
                    "path": relative.as_posix(),
                    "virtual_path": f"/project/data/{relative.as_posix()}",
                    "role": _project_data_role(relative),
                    "bytes": source_size,
                    "sha256": source_sha256,
                    "status": status,
                }
            )
        except OSError as exc:
            failures.append({"path": relative.as_posix(), "error": str(exc)})

    # The legacy import is a managed mirror, not an append-only cache. If a
    # source file disappears, prune its prior imported copy only when that copy
    # is still byte-for-byte identical to the last manifest. Locally edited
    # project files are preserved and reported as conflicts instead.
    pruned: list[dict[str, Any]] = []
    for relative_text, previous in sorted(previous_files.items()):
        if relative_text in source_paths:
            continue
        relative = Path(relative_text)
        if relative.is_absolute() or ".." in relative.parts:
            continue
        destination = destination_root / relative
        try:
            if (
                destination.is_file()
                and not destination.is_symlink()
                and _sha256_file(destination) == str(previous["sha256"])
            ):
                destination.unlink()
                pruned.append(
                    {
                        "path": relative.as_posix(),
                        "reason": "source_removed",
                    }
                )
            elif destination.exists():
                conflicts.append(
                    {
                        "path": relative.as_posix(),
                        "reason": "source_removed_destination_modified",
                    }
                )
        except OSError as exc:
            failures.append({"path": relative.as_posix(), "error": str(exc)})

    _atomic_write_json(
        manifest_path,
        {
            "schema_version": SCHEMA_VERSION,
            "source_root": str(source_root.resolve()),
            "virtual_root": "/project/data/",
            "mode": "non_destructive_copy",
            "files": files,
            "conflicts": conflicts,
            "failures": failures,
            "pruned": pruned,
            "updated_at": utc_now(),
        },
    )
    _PROJECT_INPUT_CACHE[str(shared.resolve())] = tuple(
        {
            "path": str(item["virtual_path"]),
            "role": str(item["role"]),
            "bytes": int(item["bytes"]),
            "sha256": str(item["sha256"]),
            "source": "project_shared_data",
        }
        for item in files
        if item["role"] == "primary_data"
    )


def _ensure_project_layout(
    project_root: Path,
    project_id: str,
    base_workspace: Path,
) -> Path:
    shared = project_root / "shared"
    for directory in (
        shared / "assets",
        shared / "knowledge",
        shared / "decisions",
        shared / "data",
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
    _sync_legacy_shared_data(base_workspace, shared)
    return shared


def _project_input_records(binding: WorkspaceBinding) -> list[dict[str, Any]]:
    cache_key = str(Path(binding.project_shared).resolve())
    cached = _PROJECT_INPUT_CACHE.get(cache_key)
    if cached is not None:
        return [dict(item) for item in cached]
    manifest_path = Path(binding.project_shared) / "data_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return []
    raw_files = manifest.get("files", []) if isinstance(manifest, Mapping) else []
    if not isinstance(raw_files, list):
        return []
    records = []
    for item in raw_files:
        if (
            not isinstance(item, Mapping)
            or not item.get("virtual_path")
            or not item.get("sha256")
            or not isinstance(item.get("bytes"), int)
        ):
            continue
        relative = Path(str(item.get("path") or ""))
        role = str(item.get("role") or _project_data_role(relative))
        if role != "primary_data":
            continue
        records.append(
            {
                "path": str(item["virtual_path"]),
                "role": role,
                "bytes": int(item["bytes"]),
                "sha256": str(item["sha256"]),
                "source": "project_shared_data",
            }
        )
    _PROJECT_INPUT_CACHE[cache_key] = tuple(dict(item) for item in records)
    return records


def cached_project_inputs_for_config(
    config: Mapping[str, Any] | None,
    resolved_base_workspace: str,
) -> tuple[dict[str, Any], ...]:
    """Return task-bound project inputs without filesystem I/O."""

    thread_id = scope_thread_id(config)
    if not thread_id:
        return ()
    binding = get_cached_binding_for_resolved_base(
        thread_id,
        resolved_base_workspace,
    )
    if binding is None or binding.legacy:
        return ()
    return _PROJECT_INPUT_CACHE.get(binding.project_shared, ())


def _refresh_run_scope_records(binding: WorkspaceBinding) -> None:
    """Keep both new and existing runs aware of available project data."""

    run_root = Path(binding.workspace)
    project_inputs = _project_input_records(binding)
    input_manifest_path = run_root / "input_manifest.json"
    try:
        input_manifest = json.loads(input_manifest_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        input_manifest = {}
    if not isinstance(input_manifest, dict):
        input_manifest = {}
    input_manifest.setdefault("schema_version", SCHEMA_VERSION)
    input_manifest.setdefault("thread_id", binding.thread_id)
    input_manifest.setdefault("inputs", [])
    input_manifest["project_inputs"] = project_inputs
    _atomic_write_json(input_manifest_path, input_manifest)

    context_path = run_root / "context_snapshot.json"
    try:
        context = json.loads(context_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        context = {}
    if not isinstance(context, dict):
        context = {}
    context.setdefault("schema_version", SCHEMA_VERSION)
    context.setdefault("thread_id", binding.thread_id)
    context.setdefault("project_id", binding.project_id)
    context.setdefault("project_shared_virtual_path", "/project/")
    context.setdefault("imported_context", [])
    context.setdefault("prior_runs_implicitly_loaded", False)
    context["project_data_virtual_path"] = "/project/data/"
    context["project_input_count"] = len(project_inputs)
    _atomic_write_json(context_path, context)


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
                "status": "created",
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
    _refresh_run_scope_records(binding)


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
                _ensure_project_layout(
                    Path(existing.project_root),
                    existing.project_id,
                    base,
                )
                _ensure_run_layout(existing, first_request)
            _BINDING_CACHE[(str(base), thread_id)] = existing
            return existing

        project = _slug(project_id, fallback=DEFAULT_PROJECT_ID)
        project_root = base / "projects" / project
        project_shared = _ensure_project_layout(project_root, project, base)
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
        _BINDING_CACHE[(str(base), thread_id)] = binding
        return binding


def create_legacy_binding(
    thread_id: str, base_workspace: str | Path
) -> WorkspaceBinding:
    """Bind a pre-migration thread to its original global workspace."""

    base = Path(base_workspace).expanduser().resolve()
    with _LOCK:
        existing = read_binding(thread_id, base)
        if existing is not None:
            _BINDING_CACHE[(str(base), thread_id)] = existing
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
        _BINDING_CACHE[(str(base), thread_id)] = binding
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
