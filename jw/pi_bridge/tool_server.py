"""Unix domain socket server that exposes PiToolBridge to pi extensions."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from .tools import PiToolBridge

logger = logging.getLogger(__name__)


class PiToolServer:
    """Serve pi extension tool requests over a Unix domain socket."""

    def __init__(
        self,
        bridge: PiToolBridge,
        *,
        socket_path: str | Path,
    ) -> None:
        self.bridge = bridge
        self.socket_path = Path(socket_path)
        self._server: asyncio.Server | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Start the socket server, replacing any stale socket file."""
        self._cleanup_socket()
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self.socket_path),
        )
        logger.info("PiToolServer listening on %s", self.socket_path)

    async def stop(self) -> None:
        """Stop the server and remove the socket file."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self._cleanup_socket()

    def _cleanup_socket(self) -> None:
        try:
            if self.socket_path.exists():
                self.socket_path.unlink()
        except OSError as exc:
            logger.debug("Could not remove stale socket: %s", exc)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                request = self._parse_line(line)
                if request is None:
                    continue
                response = await self._dispatch(request)
                writer.write(json.dumps(response).encode("utf-8") + b"\n")
                await writer.drain()
                # One request per connection; close so the extension client
                # receives an 'end' event and can parse the JSON response.
                writer.close()
                await writer.wait_closed()
                return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("PiToolServer client handler error: %s", exc)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    @staticmethod
    def _parse_line(line: bytes) -> dict[str, Any] | None:
        try:
            text = line.decode("utf-8").strip()
            if not text:
                return None
            data = json.loads(text)
            return data if isinstance(data, dict) else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    async def _dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        req_id = request.get("id", "")
        tool = request.get("tool")
        args = request.get("args") or {}
        if not isinstance(tool, str):
            return {
                "id": req_id,
                "success": False,
                "error": "Missing or invalid 'tool' field",
            }

        handler = getattr(self.bridge, tool, None)
        if handler is None or not callable(handler):
            return {
                "id": req_id,
                "success": False,
                "error": f"Unknown tool: {tool}",
            }

        try:
            # Serialize backend access so the sync backend is not hammered
            # concurrently from multiple socket connections.
            async with self._lock:
                result = await asyncio.to_thread(handler, **args)
            return {"id": req_id, "success": True, "result": result}
        except Exception as exc:
            logger.warning("PiToolServer dispatch error for %s: %s", tool, exc)
            return {
                "id": req_id,
                "success": False,
                "error": f"Tool execution failed: {exc}",
            }
