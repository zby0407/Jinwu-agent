"""Native tools for explicitly authorized, bounded public data downloads."""

from __future__ import annotations

import json

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from jw.solar_acquisition import CURATED_SOLAR_SOURCES, BoundedSolarAcquisition

from .registry import register_tool_bundle


@tool
def list_curated_solar_sources() -> str:
    """List the exact free public data IDs supported by the bounded downloader."""
    return json.dumps(
        {
            key: {"authority_url": value[0], "filename": value[1]}
            for key, value in CURATED_SOLAR_SOURCES.items()
        },
        ensure_ascii=False,
    )


@tool
def download_curated_solar_data(dataset_ids: list[str], config: RunnableConfig) -> str:
    """Download explicitly authorized public observations into a new project.

    Runtime must inject curated_solar_acquisition with base_workspace, project_id,
    acquisition_id, dataset_ids, and bounded budgets. No paths, arbitrary URLs,
    old project IDs, retries, overwrites, inference calls, or gap filling.
    """
    settings = (config or {}).get("configurable", {}).get("curated_solar_acquisition")
    if not isinstance(settings, dict):
        raise PermissionError("runtime acquisition authorization is missing")
    if not isinstance(settings.get("base_workspace"), str) or not isinstance(
        settings.get("project_id"), str
    ):
        raise PermissionError("runtime acquisition project binding is missing")
    acquisition = BoundedSolarAcquisition(
        settings["base_workspace"],
        project_id=settings["project_id"],
        authorization=settings,
    )
    return json.dumps(acquisition.acquire(dataset_ids), ensure_ascii=False)


@tool
def register_retained_curated_solar_data(
    dataset_ids: list[str], config: RunnableConfig
) -> str:
    """Revalidate retained raw after an acquisition failure without any HTTP calls.

    Only hemisphere-format repair and fresh-metadata-verified local pole raw are
    supported. The immutable source paths and hashes must be runtime-injected.
    Original failures are preserved. Never fills missing data or runs inference.
    """
    settings = (config or {}).get("configurable", {}).get("curated_solar_acquisition")
    if (
        not isinstance(settings, dict)
        or not isinstance(settings.get("base_workspace"), str)
        or not isinstance(settings.get("project_id"), str)
    ):
        raise PermissionError("runtime retained registration authorization is missing")
    acquisition = BoundedSolarAcquisition(
        settings["base_workspace"],
        project_id=settings["project_id"],
        authorization=settings,
    )
    return json.dumps(
        acquisition.register_retained(
            dataset_ids, verified_local_inputs=settings.get("verified_local_inputs")
        ),
        ensure_ascii=False,
    )


SOLAR_ACQUISITION_TOOLS = [
    list_curated_solar_sources,
    download_curated_solar_data,
    register_retained_curated_solar_data,
]
register_tool_bundle(
    "solar-acquisition", SOLAR_ACQUISITION_TOOLS, include_in_main=False
)
