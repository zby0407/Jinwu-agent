"""An unused external-tool transport must not initialize its SDK."""

import os
import subprocess
import sys
from pathlib import Path


def test_empty_mcp_configuration_does_not_import_sdk(tmp_path):
    source = Path(__file__).resolve().parents[1]
    env = {
        key: os.environ[key]
        for key in ("PATH", "SYSTEMROOT", "WINDIR")
        if key in os.environ
    }
    env.update(
        HOME=str(tmp_path), XDG_CONFIG_HOME=str(tmp_path), PYTHONPATH=str(source)
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import asyncio
import sys
from jw.mcp import load_mcp_tools, aload_mcp_tools
assert load_mcp_tools(config={}) == {}
assert asyncio.run(aload_mcp_tools(config={})) == {}
assert 'mcp' not in sys.modules
assert 'langchain_mcp_adapters' not in sys.modules
""",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_stdio_compatibility_patch_applied_before_connect(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock

    import langchain_mcp_adapters.client as adapters

    from jw.mcp import client

    events = []
    monkeypatch.setattr(
        client, "_patch_mcp_windows_command_resolver", lambda: events.append("patch")
    )

    def construct(connections):
        assert events == ["patch"]
        events.append("connect")
        return AsyncMock(get_tools=AsyncMock(return_value=[]))

    monkeypatch.setattr(adapters, "MultiServerMCPClient", construct)
    assert asyncio.run(
        client._load_tools({"local": {"transport": "stdio", "command": sys.executable}})
    ) == {"local": []}
    assert events == ["patch", "connect"]


def test_local_stdio_server_still_lists_and_executes_tools(tmp_path):
    import asyncio

    from jw.mcp.client import _load_tools

    server = tmp_path / "server.py"
    server.write_text(
        "from mcp.server.fastmcp import FastMCP\n"
        'server = FastMCP("local-startup-regression")\n'
        "@server.tool()\n"
        "def add(left: int, right: int) -> int:\n"
        '    """Add two integers."""\n'
        "    return left + right\n"
        "server.run()\n",
        encoding="utf-8",
    )

    async def run():
        tools = await _load_tools(
            {
                "local": {
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [str(server)],
                    "env": {"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
                }
            }
        )
        assert [tool.name for tool in tools["local"]] == ["add"]
        result = await tools["local"][0].ainvoke({"left": 2, "right": 3})
        assert any(block.get("text") == "5" for block in result)

    asyncio.run(run())
