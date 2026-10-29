"""Lazy main graph factory shared by LangGraph runs and background warmup."""

from .runtime import resolve_graph


def jw_agent():
    """Register without constructing; share one graph with desktop warmup."""

    def build():
        from jw.agent import _get_default_agent

        return _get_default_agent()

    return resolve_graph("JW", build)


__all__ = ["jw_agent"]
