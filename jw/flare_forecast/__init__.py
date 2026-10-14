"""Bounded SHARP scalar forecasting; importing never downloads, fits or issues."""

from .model import FrozenForest, load_forest

__all__ = ["FrozenForest", "load_forest"]
