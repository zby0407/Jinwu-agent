"""REST surface for observational data-source status and toggles.

Complements the read-only knowledge-base routes: the WebUI settings dialog
lists the curated solar datasets (``jw.solar_data_catalog``), their persisted
enable/disable state (``jw.data_source_settings``), and — when a dataset has
been acquired before — the registration receipt with coverage dates read from
the project's ``*.provenance.json`` files.

Blocking file IO is offloaded via ``asyncio.to_thread`` (blockbuster).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from jw.data_preview import build_dataset_preview
from jw.data_source_settings import load_source_settings, set_source_enabled
from jw.paths import WORKSPACE_ROOT
from jw.solar_data_catalog import describe_solar_datasets

_NO_STORE = {"Cache-Control": "no-store"}


def _json(data: Any, *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status_code, headers=_NO_STORE)


def _latest_registrations() -> dict[str, dict[str, Any]]:
    """Map dataset_id -> newest project-data-provenance-v1 receipt."""

    registrations: dict[str, dict[str, Any]] = {}
    projects_root = WORKSPACE_ROOT / "projects"
    if not projects_root.is_dir():
        return registrations
    for provenance_path in projects_root.glob("*/shared/data/**/*.provenance.json"):
        try:
            payload = json.loads(provenance_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("schema_version") != "project-data-provenance-v1":
            continue
        dataset_id = str(payload.get("dataset_id") or "").strip()
        if not dataset_id:
            continue
        previous = registrations.get(dataset_id)
        registered_at = str(payload.get("registered_at") or "")
        if previous is not None and str(previous.get("registered_at") or "") >= (
            registered_at
        ):
            continue
        provenance = payload.get("provenance")
        provenance = provenance if isinstance(provenance, dict) else {}
        validation = provenance.get("validation")
        validation = validation if isinstance(validation, dict) else {}
        coverage_end = validation.get("coverage_end") or validation.get(
            "latest_valid_observation"
        )
        try:
            project = provenance_path.relative_to(projects_root).parts[0]
        except (ValueError, IndexError):
            project = None
        registrations[dataset_id] = {
            "registered_at": registered_at or None,
            "retrieved_at": provenance.get("retrieved_at"),
            "data_path": payload.get("data_path"),
            "bytes": payload.get("bytes"),
            "project": project,
            "coverage_start": validation.get("coverage_start"),
            "coverage_end": coverage_end,
            "row_count": validation.get("row_count"),
        }
    return registrations


def _fetch_data_sources() -> dict[str, Any]:
    enabled_state = load_source_settings()
    registrations = _latest_registrations()
    sources = []
    for dataset in describe_solar_datasets():
        dataset_id = str(dataset["id"])
        registration = registrations.get(dataset_id)
        sources.append(
            {
                **dataset,
                "enabled": enabled_state.get(dataset_id, True),
                "registration": registration,
            }
        )
    return {"status": "ok", "sources": sources}


def _set_data_source_enabled(dataset_id: str, enabled: bool) -> dict[str, Any]:
    known = {str(dataset["id"]) for dataset in describe_solar_datasets()}
    if dataset_id not in known:
        return {"error": f"unknown dataset id: {dataset_id}", "status_code": 404}
    set_source_enabled(dataset_id, enabled)
    return {"id": dataset_id, "enabled": enabled}


async def list_data_sources(_request: Request) -> JSONResponse:
    return _json(await asyncio.to_thread(_fetch_data_sources))


async def update_data_source(request: Request) -> JSONResponse:
    dataset_id = request.path_params["dataset_id"]
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return _json({"error": "request body must be JSON"}, status_code=400)
    if not isinstance(body, dict) or not isinstance(body.get("enabled"), bool):
        return _json({"error": 'body must be {"enabled": true|false}'}, status_code=400)
    result = await asyncio.to_thread(
        _set_data_source_enabled, dataset_id, body["enabled"]
    )
    status_code = int(result.pop("status_code", 200))
    return _json(result, status_code=status_code)


# Source-checkout fallback: when JW runs from its own repository (dev /
# desktop-app source builds), curated datasets may live in the checkout's
# projects/ even though the runtime workspace is workspace/. In installed
# packages this directory simply does not exist and the fallback is inert.
_CHECKOUT_PROJECTS = Path(__file__).resolve().parents[2] / "projects"


def _project_data_roots() -> list[Path]:
    roots = [WORKSPACE_ROOT / "projects"]
    if _CHECKOUT_PROJECTS != WORKSPACE_ROOT / "projects":
        roots.append(_CHECKOUT_PROJECTS)
    return roots


def _dataset_file_path(dataset_id: str) -> tuple[Path, bool] | None:
    """Resolve the data file for a curated dataset.

    Search order: the newest registered receipt (spec-correct), then any
    project's ``shared/data`` catalog layout across known project roots.
    Returns ``(path, registered)`` or ``None`` when no file exists yet.
    """

    relative = next(
        (
            str(dataset["relative"])
            for dataset in describe_solar_datasets()
            if str(dataset["id"]) == dataset_id
        ),
        None,
    )
    candidates: list[tuple[Path, bool]] = []
    registration = _latest_registrations().get(dataset_id)
    if registration and registration.get("data_path"):
        data_path = Path(str(registration["data_path"]))
        if not data_path.is_absolute():
            project = str(registration.get("project") or "default")
            data_path = (
                WORKSPACE_ROOT / "projects" / project / "shared" / "data" / data_path
            )
        candidates.append((data_path, True))
    if relative:
        for projects_root in _project_data_roots():
            for project_root in sorted(projects_root.glob("*")):
                candidate = project_root / "shared" / "data" / relative
                candidates.append((candidate, False))
    for candidate, registered in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        if resolved.is_file():
            return resolved, registered
    return None


def _preview_data_source(dataset_id: str) -> dict[str, Any]:
    known = {str(dataset["id"]) for dataset in describe_solar_datasets()}
    if dataset_id not in known:
        return {"error": f"unknown dataset id: {dataset_id}", "status_code": 404}
    resolved = _dataset_file_path(dataset_id)
    if resolved is None:
        return {
            "error": "dataset has not been ingested yet",
            "status_code": 404,
            "code": "not_ingested",
        }
    path, registered = resolved
    try:
        preview = build_dataset_preview(dataset_id, path)
    except (OSError, ValueError, KeyError) as exc:
        return {
            "error": f"preview failed: {type(exc).__name__}",
            "status_code": 422,
        }
    return {"status": "ok", "preview": {**preview, "registered": registered}}


async def preview_data_source(request: Request) -> JSONResponse:
    dataset_id = request.path_params["dataset_id"]
    result = await asyncio.to_thread(_preview_data_source, dataset_id)
    status_code = int(result.pop("status_code", 200))
    return _json(result, status_code=status_code)


DATA_SOURCE_ROUTES = [
    Route("/api/data-sources", list_data_sources, methods=["GET"]),
    Route(
        "/api/data-sources/{dataset_id}/preview",
        preview_data_source,
        methods=["GET"],
    ),
    Route(
        "/api/data-sources/{dataset_id}",
        update_data_source,
        methods=["PUT"],
    ),
]
