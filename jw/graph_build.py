"""Reuse subgraph compilation within one Deep Agents construction.

Deep Agents 0.6.12 builds the task tool once, then rebuilds its closure after
collecting private parent-state fields. Both passes compile the same specs.
The filter belongs to the task closure, not the compiled subgraph. Keep the
upstream closure rebuild intact and reuse only its identical graph inputs.
"""

from contextvars import ContextVar
from functools import wraps

import deepagents
from deepagents.middleware import subagents

_construction_cache: ContextVar[dict | None] = ContextVar(
    "jw_subgraph_construction", default=None
)


def _reuse_during_construction(build):
    @wraps(build)
    def cached(spec, *, state_schema=None, response_format=None):
        cache = _construction_cache.get()
        # Dynamic task response schemas are compiled by the upstream runtime.
        if cache is None or response_format is not None:
            return build(
                spec, state_schema=state_schema, response_format=response_format
            )
        key = (id(spec), id(state_schema))
        if key not in cache:
            graph = build(spec, state_schema=state_schema, response_format=None)
            # Hold the objects for this construction so their ids cannot be reused.
            cache[key] = (spec, state_schema, graph)
        return cache[key][2]

    return cached


# One compatibility hook on the pinned upstream API. Outside our construction
# context it delegates directly; no graphs survive in a process-wide cache.
subagents.create_sub_agent = _reuse_during_construction(subagents.create_sub_agent)


def create_deep_agent(*args, **kwargs):
    token = _construction_cache.set({})
    try:
        return deepagents.create_deep_agent(*args, **kwargs)
    finally:
        _construction_cache.reset(token)
