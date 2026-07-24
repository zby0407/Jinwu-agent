import asyncio
import json
import sys
import uuid
from unittest.mock import MagicMock

import pytest

from jw.pi_bridge.tool_server import PiToolServer
from jw.pi_bridge.tools import PiToolBridge

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="The pi tool bridge currently uses Unix domain sockets",
)


async def _send_request(socket_path: str, request: dict) -> dict:
    reader, writer = await asyncio.open_unix_connection(socket_path)
    try:
        writer.write(json.dumps(request).encode("utf-8") + b"\n")
        await writer.drain()
        line = await reader.readline()
        return json.loads(line.decode("utf-8"))
    finally:
        writer.close()
        await writer.wait_closed()


def _short_socket_path() -> str:
    """Return a short Unix socket path; macOS limits AF_UNIX paths to ~104 chars."""
    return f"/tmp/pi-tool-server-test-{uuid.uuid4().hex[:8]}.sock"


class TestPiToolServer:
    @pytest.mark.asyncio
    async def test_read_round_trip(self, tmp_path):
        backend = MagicMock()
        backend.read.return_value = "file contents"
        bridge = PiToolBridge(str(tmp_path), backend=backend)
        socket_path = _short_socket_path()
        server = PiToolServer(bridge, socket_path=socket_path)
        await server.start()
        try:
            response = await _send_request(
                socket_path,
                {"id": "r1", "tool": "read", "args": {"path": "/foo.txt"}},
            )
            assert response["id"] == "r1"
            assert response["success"] is True
            assert response["result"]["content"] == "file contents"
            assert response["result"]["isError"] is False
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_bash_round_trip(self, tmp_path):
        backend = MagicMock()
        response = MagicMock()
        response.output = "ok"
        response.exit_code = 0
        response.truncated = False
        backend.execute.return_value = response
        bridge = PiToolBridge(str(tmp_path), backend=backend)
        socket_path = _short_socket_path()
        server = PiToolServer(bridge, socket_path=socket_path)
        await server.start()
        try:
            resp = await _send_request(
                socket_path,
                {"id": "b1", "tool": "bash", "args": {"command": "ls"}},
            )
            assert resp["success"] is True
            assert resp["result"]["content"] == "ok"
            assert resp["result"]["isError"] is False
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_unknown_tool(self, tmp_path):
        bridge = PiToolBridge(str(tmp_path), backend=MagicMock())
        socket_path = _short_socket_path()
        server = PiToolServer(bridge, socket_path=socket_path)
        await server.start()
        try:
            resp = await _send_request(
                socket_path,
                {"id": "x1", "tool": "missing_tool", "args": {}},
            )
            assert resp["success"] is False
            assert "Unknown tool" in resp["error"]
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_server_closes_connection_after_response(self, tmp_path):
        """The extension client waits for an 'end' event; ensure we close."""
        backend = MagicMock()
        backend.read.return_value = "ok"
        bridge = PiToolBridge(str(tmp_path), backend=backend)
        socket_path = _short_socket_path()
        server = PiToolServer(bridge, socket_path=socket_path)
        await server.start()
        try:
            reader, writer = await asyncio.open_unix_connection(socket_path)
            writer.write(
                json.dumps({"id": "c1", "tool": "read", "args": {"path": "/x"}}).encode(
                    "utf-8"
                )
                + b"\n"
            )
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=5)
            assert json.loads(line.decode("utf-8"))["success"] is True
            # Wait for the server side to close the connection.
            remaining = await asyncio.wait_for(reader.read(), timeout=5)
            assert remaining == b""
        finally:
            writer.close()
            await writer.wait_closed()
            await server.stop()
