"""Tools package — re-exports all public tool symbols.

External imports like ``from EvoScientist.tools import tavily_search`` continue
to work unchanged thanks to these re-exports.
"""

from .automatic_experiment import AUTOMATIC_EXPERIMENT_TOOLS
from .knowledge_base import KB_TOOLS
from .research_planner import RESEARCH_PLANNER_TOOLS
from .scientific_hypothesis import SCIENTIFIC_HYPOTHESIS_TOOLS
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
    "AUTOMATIC_EXPERIMENT_TOOLS",
    "KB_TOOLS",
    "RESEARCH_PLANNER_TOOLS",
    "SCIENTIFIC_HYPOTHESIS_TOOLS",
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
