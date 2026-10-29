"""Tools package — re-exports all public tool symbols.

External imports like ``from jw.tools import tavily_search`` continue
to work unchanged thanks to these re-exports.
"""

from .automatic_experiment import AUTOMATIC_EXPERIMENT_TOOLS
from .b1_flare import B1_FLARE_TOOLS, run_b1_flare_case
from .flare_forecast import forecast_flare_24h
from .knowledge_base import KB_READONLY_TOOLS, KB_TOOLS
from .literature_artifacts import LITERATURE_ARTIFACT_TOOLS
from .registry import (
    TOOL_BUNDLE_ENTRY_POINT_GROUP,
    ToolBundle,
    get_builtin_tool_registry,
    get_main_agent_tools,
    get_tool_bundles,
    register_tool_bundle,
    resolve_tool_bundles,
)
from .research_planner import RESEARCH_PLANNER_TOOLS
from .research_quality import RESEARCH_QUALITY_TOOLS
from .research_review import RESEARCH_RELEASE_TOOLS, RESEARCH_REVIEW_TOOLS
from .scientific_hypothesis import SCIENTIFIC_HYPOTHESIS_TOOLS
from .search import fetch_webpage_content, tavily_search
from .skill_manager import skill_manager
from .solar_acquisition import SOLAR_ACQUISITION_TOOLS
from .solar_feature import (
    SOLAR_FEATURE_TOOLS,
    audit_solar_data_quality,
    bind_f107_dataset_semantics,
    dataset_statistics,
    engineer_solar_features,
    prepare_solar_cycle_26_readiness,
    prepare_solar_experiment,
    run_solar_cycle_26_historical_forecast,
    solar_data_open_context,
)
from .think import think_tool

__all__ = [
    "AUTOMATIC_EXPERIMENT_TOOLS",
    "B1_FLARE_TOOLS",
    "KB_READONLY_TOOLS",
    "KB_TOOLS",
    "LITERATURE_ARTIFACT_TOOLS",
    "RESEARCH_PLANNER_TOOLS",
    "RESEARCH_QUALITY_TOOLS",
    "RESEARCH_RELEASE_TOOLS",
    "RESEARCH_REVIEW_TOOLS",
    "SCIENTIFIC_HYPOTHESIS_TOOLS",
    "SOLAR_ACQUISITION_TOOLS",
    "SOLAR_FEATURE_TOOLS",
    "TOOL_BUNDLE_ENTRY_POINT_GROUP",
    "ToolBundle",
    "audit_solar_data_quality",
    "bind_f107_dataset_semantics",
    "dataset_statistics",
    "engineer_solar_features",
    "fetch_webpage_content",
    "forecast_flare_24h",
    "get_builtin_tool_registry",
    "get_main_agent_tools",
    "get_tool_bundles",
    "prepare_solar_cycle_26_readiness",
    "prepare_solar_experiment",
    "register_tool_bundle",
    "resolve_tool_bundles",
    "run_b1_flare_case",
    "run_solar_cycle_26_historical_forecast",
    "skill_manager",
    "solar_data_open_context",
    "tavily_search",
    "think_tool",
]
