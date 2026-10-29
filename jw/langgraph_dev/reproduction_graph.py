"""Deployed restricted-auto graph for the fixed H1/H2 suite."""

from .runtime import resolve_graph


def reproduction_agent():
    """An unused optional suite must not require a second provider's key."""

    def build():
        from jw.agent import create_reproduction_agent

        return create_reproduction_agent()

    return resolve_graph("JW-reproduction", build)


__all__ = ["reproduction_agent"]
