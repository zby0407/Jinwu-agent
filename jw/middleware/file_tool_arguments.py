"""Normalize observed model argument aliases before file-tool policy checks."""

import logging

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

logger = logging.getLogger(__name__)
_ALIASES = {
    "read_file": {"path": "file_path"},
    "write_file": {"path": "file_path"},
    "edit_file": {
        "path": "file_path",
        "old_str": "old_string",
        "new_str": "new_string",
    },
}


class FileToolArgumentsMiddleware(AgentMiddleware):
    """Accept only known equivalent names; never infer values or retry writes."""

    name = "file_tool_arguments"

    @staticmethod
    def _prepare(request):
        call = request.tool_call
        aliases = _ALIASES.get(call["name"], {})
        args = dict(call["args"])
        changes = {}
        for alias, canonical in aliases.items():
            if alias not in args:
                continue
            if canonical in args and args[canonical] != args[alias]:
                return (
                    request,
                    {},
                    ToolMessage(
                        content=f"Conflicting arguments: {alias} and {canonical}. Supply only {canonical}.",
                        name=call["name"],
                        tool_call_id=call["id"],
                        status="error",
                    ),
                )
            args[canonical] = args.pop(alias)
            changes[alias] = canonical
        if not changes:
            return request, {}, None
        # Record names only, never user file contents or paths.
        logger.info("Normalized argument aliases for %s: %s", call["name"], changes)
        return request.override(tool_call={**call, "args": args}), changes, None

    @staticmethod
    def _annotate(result, changes):
        if changes and isinstance(result, ToolMessage):
            return result.model_copy(
                update={
                    "additional_kwargs": {
                        **result.additional_kwargs,
                        "argument_aliases": changes,
                    },
                }
            )
        return result

    def wrap_tool_call(self, request, handler):
        prepared, changes, error = self._prepare(request)
        if error is not None:
            return error
        return self._annotate(handler(prepared), changes)

    async def awrap_tool_call(self, request, handler):
        prepared, changes, error = self._prepare(request)
        if error is not None:
            return error
        return self._annotate(await handler(prepared), changes)
