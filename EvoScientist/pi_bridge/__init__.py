"""pi Agent bridge for EvoScientist.

Exposes a LangGraph-compatible graph wrapper that drives pi via RPC.
"""

from .graph import PiAgentGraph

__all__ = ["PiAgentGraph"]
