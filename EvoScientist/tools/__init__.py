"""Tools package — re-exports all public tool symbols.

External imports like ``from EvoScientist.tools import tavily_search`` continue
to work unchanged thanks to these re-exports.
"""

from .b3_science import B3_SCIENCE_TOOLS
from .research_planner import RESEARCH_PLANNER_TOOLS
from .search import fetch_webpage_content, tavily_search
from .skill_manager import skill_manager
from .solar_feature import (
    audit_solar_data_quality,
    dataset_statistics,
    engineer_solar_features,
    prepare_solar_experiment,
)
from .think import think_tool

SOLAR_FEATURE_TOOLS = [
    audit_solar_data_quality,
    engineer_solar_features,
    prepare_solar_experiment,
    dataset_statistics,
]

__all__ = [
    "B3_SCIENCE_TOOLS",
    "RESEARCH_PLANNER_TOOLS",
    "SOLAR_FEATURE_TOOLS",
    "audit_solar_data_quality",
    "dataset_statistics",
    "engineer_solar_features",
    "fetch_webpage_content",
    "prepare_solar_experiment",
    "skill_manager",
    "tavily_search",
    "think_tool",
]
