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
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

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
    for provenance_path in projects_root.glob(
        "*/shared/data/**/*.provenance.json"
    ):
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
        return _json(
            {"error": "body must be {\"enabled\": true|false}"}, status_code=400
        )
    result = await asyncio.to_thread(
        _set_data_source_enabled, dataset_id, body["enabled"]
    )
    status_code = int(result.pop("status_code", 200))
    return _json(result, status_code=status_code)


DATA_SOURCE_ROUTES = [
    Route("/api/data-sources", list_data_sources, methods=["GET"]),
    Route(
        "/api/data-sources/{dataset_id}",
        update_data_source,
        methods=["PUT"],
    ),
]
