from __future__ import annotations

import asyncio
from dataclasses import dataclass

from jw.middleware.contract_tool_allowlist import (
    CONTRACT_TOOL_ALLOWLISTS,
    ContractToolAllowlistMiddleware,
)


@dataclass
class _Tool:
    name: str


class _Request:
    def __init__(
        self,
        tools: list[_Tool],
        tool_call: dict[str, str] | None = None,
    ) -> None:
        self.tools = tools
        self.tool_call = tool_call or {}

    def override(self, *, tools: list[_Tool]) -> _Request:
        return _Request(tools, self.tool_call)


def test_contract_allowlist_removes_injected_filesystem_and_shell_tools() -> None:
    allowed = CONTRACT_TOOL_ALLOWLISTS["solar-experiment"]
    middleware = ContractToolAllowlistMiddleware(allowed)
    request = _Request(
        [
            _Tool("automatic_experiment_bind_request"),
            _Tool("write_file"),
            _Tool("edit_file"),
            _Tool("execute"),
            _Tool("task"),
        ]
    )

    filtered = middleware._filter(request)  # noqa: SLF001 - focused boundary test

    assert [tool.name for tool in filtered.tools] == [
        "automatic_experiment_bind_request"
    ]


def test_every_closed_specialist_excludes_generic_mutation_tools() -> None:
    forbidden = {"write_file", "edit_file", "execute", "task"}
    assert set(CONTRACT_TOOL_ALLOWLISTS) == {
        "solar-planner",
        "solar-hypothesis",
        "solar-experiment",
    }
    for allowed in CONTRACT_TOOL_ALLOWLISTS.values():
        assert allowed.isdisjoint(forbidden)


def test_contract_allowlist_blocks_disallowed_tool_at_execution() -> None:
    middleware = ContractToolAllowlistMiddleware(
        CONTRACT_TOOL_ALLOWLISTS["solar-planner"]
    )
    request = _Request(
        [], {"name": "write_file", "id": "call-forbidden"}
    )
    called = False

    def handler(_request: _Request):
        nonlocal called
        called = True
        raise AssertionError("disallowed tool handler must not run")

    result = middleware.wrap_tool_call(request, handler)

    assert called is False
    assert result.status == "error"
    assert result.name == "write_file"
    assert "CONTRACT TOOL BLOCKED" in str(result.content)


def test_contract_allowlist_blocks_disallowed_async_tool_at_execution() -> None:
    middleware = ContractToolAllowlistMiddleware(
        CONTRACT_TOOL_ALLOWLISTS["solar-experiment"]
    )
    request = _Request([], {"name": "execute", "id": "call-forbidden-async"})
    called = False

    async def handler(_request: _Request):
        nonlocal called
        called = True
        raise AssertionError("disallowed async tool handler must not run")

    result = asyncio.run(middleware.awrap_tool_call(request, handler))

    assert called is False
    assert result.status == "error"
    assert result.name == "execute"


def test_contract_allowlist_executes_allowed_tool() -> None:
    middleware = ContractToolAllowlistMiddleware(
        CONTRACT_TOOL_ALLOWLISTS["solar-hypothesis"]
    )
    request = _Request(
        [],
        {"name": "scientific_hypothesis_bind_request", "id": "call-allowed"},
    )
    sentinel = object()

    assert middleware.wrap_tool_call(request, lambda _request: sentinel) is sentinel
