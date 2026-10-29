"""Deployed graphs for all yaml-flagged async sub-agents.

One lazy factory per ``async: true`` entry in ``jw/subagents/<name>.yaml``.
The first invocation compiles and caches the graph with the same tools,
skills, backend and middleware as the in-process sync version.

To add a new async sub-agent:

  1. Set ``async: true`` in ``jw/subagents/<name>.yaml``.
  2. Add a factory here::

         def <snake_name>():
             return _subagent("<name>")

  3. Register it in ``jw/langgraph_dev/langgraph.json``::

         "<name>": "jw.langgraph_dev.graphs:<snake_name>"

The deployed main agent factory (``jw_agent``) lives in ``main_graph.py``
and is warmed in the background during server startup.
"""

from .runtime import resolve_graph


def _subagent(name):
    def build():
        from jw.subagents._factory import build_async_subagent_graph

        return build_async_subagent_graph(name)

    return resolve_graph(name, build)


def writing_agent():
    return _subagent("writing-agent")


def data_analysis_agent():
    return _subagent("data-analysis-agent")


def scheduler():
    return _subagent("scheduler")


def _memory(name, factory, source=None):
    def build():
        from jw.memory import agents
        from jw.memory.types import MemorySourceType

        builder = getattr(agents, factory)
        return builder(MemorySourceType(source)) if source else builder()

    return resolve_graph(name, build)


def jwmemory_subagent_worker():
    return _memory("jwmemory-subagent-worker", "build_memory_worker_graph", "subagent")


def jwmemory_turn_worker():
    return _memory("jwmemory-turn-worker", "build_memory_worker_graph", "turn")


def jwmemory_observation_linker():
    return _memory("jwmemory-observation-linker", "build_observation_linker_graph")


def jwmemory_autoskills():
    return _memory("jwmemory-autoskills", "build_autoskills_graph")
