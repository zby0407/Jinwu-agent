"""Deployed graph entry for the main JW agent.

The main ``jw_agent`` is exposed via ``__getattr__`` lazy loading
in ``jw/agent.py`` so it doesn't construct on plain
``import jw``. ``langgraph dev`` 's symbol resolver inspects
module attributes directly and doesn't trigger ``__getattr__``, so we
re-export here to make it visible.
"""

from jw.agent import jw_agent

__all__ = ["jw_agent"]
