"""Async client for the Pi coding agent RPC mode.

Pi RPC protocol: JSONL over stdin/stdout.
Docs: https://pi.dev/docs/latest/rpc
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def encode_image(path: str) -> dict[str, str]:
    """Read an image file and return Pi's ImageContent object.

    Raises:
        FileNotFoundError: If the image does not exist.
        ValueError: If the file is not a recognized image type.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Image not found: {path}")

    mime_type, _ = mimetypes.guess_type(str(file_path))
    if mime_type is None or not mime_type.startswith("image/"):
        raise ValueError(f"File does not appear to be an image: {path}")

    data = file_path.read_bytes()
    return {
        "type": "image",
        "data": base64.b64encode(data).decode("ascii"),
        "mimeType": mime_type,
    }


@dataclass
class PiConfig:
    """Configuration for launching the Pi RPC subprocess."""

    pi_bin: str = "pi"
    cwd: str | None = None
    provider: str | None = None
    model: str | None = None
    session_dir: str | None = None
    session_name: str = "jw-pi-bridge"
    no_session: bool = False
    env: dict[str, str] = field(default_factory=dict)
    extra_args: list[str] = field(default_factory=list)

    def build_argv(self) -> list[str]:
        argv = [self.pi_bin, "--mode", "rpc"]
        if self.no_session:
            argv.append("--no-session")
        if self.session_name:
            argv.extend(["--name", self.session_name])
        if self.provider:
            argv.extend(["--provider", self.provider])
        if self.model:
            argv.extend(["--model", self.model])
        if self.session_dir:
            argv.extend(["--session-dir", self.session_dir])
        argv.extend(self.extra_args)
        return argv


class PiError(Exception):
    """Raised when Pi returns an error or the RPC connection fails."""


class PiTimeoutError(PiError):
    """Raised when a Pi operation times out."""


class PiClient:
    """Manages a long-lived Pi RPC subprocess and exposes high-level helpers."""

    def __init__(self, config: PiConfig | None = None) -> None:
        self.config = config or PiConfig()
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._counter = 0
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._events_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def __aenter__(self) -> PiClient:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.stop()

    async def start(self) -> None:
        """Start the Pi RPC subprocess and background reader."""
        async with self._lock:
            if self._proc is not None:
                return

            pi_bin = shutil.which(self.config.pi_bin) or self.config.pi_bin
            argv = self.config.build_argv()
            argv[0] = pi_bin
            cwd = self.config.cwd or os.getcwd()

            logger.info("Starting Pi RPC: %s in %s", " ".join(argv), cwd)

            env = os.environ.copy()
            env.update(self.config.env)

            self._proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )

            self._reader_task = asyncio.create_task(self._read_loop())

    async def stop(self) -> None:
        """Terminate the Pi RPC subprocess and cleanup."""
        async with self._lock:
            if self._reader_task is not None:
                self._reader_task.cancel()
                try:
                    await self._reader_task
                except asyncio.CancelledError:
                    pass
                self._reader_task = None

            if self._proc is not None:
                try:
                    self._proc.terminate()
                    await asyncio.wait_for(self._proc.wait(), timeout=5.0)
                except TimeoutError:
                    self._proc.kill()
                    await self._proc.wait()
                except ProcessLookupError:
                    pass
                self._proc = None

            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(PiError("Pi client stopped"))
            self._pending.clear()

    def _next_id(self) -> str:
        self._counter += 1
        return f"jw-{self._counter}"

    async def _read_loop(self) -> None:
        """Read JSONL lines from Pi stdout and route responses/events."""
        assert self._proc is not None
        assert self._proc.stdout is not None

        try:
            while True:
                raw = await self._proc.stdout.readline()
                if not raw:
                    break

                line = raw.decode("utf-8", errors="replace").rstrip("\n").rstrip("\r")
                if not line:
                    continue

                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as exc:
                    logger.warning("Failed to parse Pi RPC line: %s", exc)
                    continue

                msg_type = msg.get("type")

                if msg_type == "response":
                    req_id = msg.get("id")
                    if req_id and req_id in self._pending:
                        fut = self._pending.pop(req_id)
                        if not fut.done():
                            fut.set_result(msg)
                    else:
                        logger.debug("Unsolicited Pi response: %s", msg)
                elif msg_type == "extension_ui_request":
                    # Headless bridge: auto-cancel any UI request from Pi/extensions.
                    await self._send({
                        "type": "extension_ui_response",
                        "id": msg["id"],
                        "cancelled": True,
                    })
                else:
                    await self._events_queue.put(msg)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Pi reader loop error: %s", exc)
        finally:
            # Wake up any waiters so they fail instead of hanging.
            for _req_id, fut in list(self._pending.items()):
                if not fut.done():
                    fut.set_exception(PiError("Pi RPC reader closed"))
            self._pending.clear()

    async def _send(self, cmd: dict[str, Any]) -> None:
        """Send a JSON command to Pi stdin."""
        assert self._proc is not None
        assert self._proc.stdin is not None

        line = json.dumps(cmd, ensure_ascii=False) + "\n"
        self._proc.stdin.write(line.encode("utf-8"))
        await self._proc.stdin.drain()

    async def _command(
        self,
        cmd: dict[str, Any],
        *,
        timeout: float | None = 30.0,
    ) -> dict[str, Any]:
        """Send a command and wait for its correlated response."""
        await self.start()
        req_id = self._next_id()
        cmd["id"] = req_id
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut
        try:
            await self._send(cmd)
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(req_id, None)

    async def prompt(
        self,
        message: str,
        *,
        images: list[dict[str, str]] | None = None,
        timeout: float | None = 300.0,
        streaming_timeout: float | None = None,
    ) -> str:
        """Send a user prompt and return the final assistant text.

        Args:
            message: The user prompt.
            images: Optional list of Pi ImageContent dicts (see ``encode_image``).
            timeout: Total time to wait for the agent to settle.
            streaming_timeout: Max time to wait between events.

        Waits for the agent to settle. Text is assembled from streaming
        `message_update` events. If the agent produces no text, the last
        assistant message text is extracted from `agent_end` events.
        """
        cmd: dict[str, Any] = {"type": "prompt", "message": message}
        if images:
            cmd["images"] = images
        resp = await self._command(cmd, timeout=30.0)
        if not resp.get("success", False):
            error = resp.get("error", "unknown error")
            raise PiError(f"Pi rejected prompt: {error}")

        deadline = None
        if timeout is not None and timeout > 0:
            deadline = asyncio.get_event_loop().time() + timeout

        pieces: list[str] = []
        last_assistant_text: str | None = None
        settled = False

        while True:
            wait_timeout = streaming_timeout or 10.0
            if deadline is not None:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    raise PiTimeoutError("Prompt timed out waiting for Pi to settle")
                wait_timeout = min(wait_timeout, remaining)

            try:
                event = await asyncio.wait_for(self._events_queue.get(), timeout=wait_timeout)
            except TimeoutError as exc:
                if settled:
                    break
                raise PiTimeoutError("Pi event stream idle for too long") from exc

            event_type = event.get("type")

            if event_type == "message_update":
                delta = event.get("assistantMessageEvent", {})
                if delta.get("type") == "text_delta":
                    pieces.append(delta.get("delta", ""))
                elif delta.get("type") == "text_end":
                    text = delta.get("content") or ""
                    if text:
                        last_assistant_text = text
            elif event_type == "message_end":
                msg = event.get("message", {})
                if msg.get("role") == "assistant":
                    text = self._extract_text(msg)
                    if text:
                        last_assistant_text = text
            elif event_type == "agent_end":
                for msg in event.get("messages", []):
                    if msg.get("role") == "assistant":
                        text = self._extract_text(msg)
                        if text:
                            last_assistant_text = text
            elif event_type == "agent_settled":
                settled = True
                # Drain a few more events in case message_end arrives after settled.
                await asyncio.sleep(0.2)
                while not self._events_queue.empty():
                    self._events_queue.get_nowait()
                break
            elif event_type == "auto_retry_end" and not event.get("success", True):
                raise PiError(f"Pi auto-retry failed: {event.get('finalError', 'unknown')}")
            elif event_type == "extension_error":
                logger.warning("Pi extension error: %s", event.get("error"))

        result = "".join(pieces).strip()
        if not result and last_assistant_text:
            result = last_assistant_text.strip()
        return result

    @staticmethod
    def _extract_text(message: dict[str, Any]) -> str | None:
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            return "".join(texts) or None
        return None

    async def bash(
        self,
        command: str,
        *,
        timeout: float | None = 60.0,
    ) -> dict[str, Any]:
        """Execute a bash command via Pi's bash RPC command."""
        resp = await self._command(
            {"type": "bash", "command": command},
            timeout=timeout,
        )
        if not resp.get("success", False):
            raise PiError(resp.get("error", "bash command failed"))
        return resp.get("data", {})

    async def get_state(self) -> dict[str, Any]:
        """Get current Pi session state."""
        resp = await self._command({"type": "get_state"}, timeout=10.0)
        if not resp.get("success", False):
            raise PiError(resp.get("error", "get_state failed"))
        return resp.get("data", {})

    async def get_commands(self) -> list[dict[str, Any]]:
        """List available Pi extension commands, prompt templates, and skills."""
        resp = await self._command({"type": "get_commands"}, timeout=10.0)
        if not resp.get("success", False):
            raise PiError(resp.get("error", "get_commands failed"))
        return resp.get("data", {}).get("commands", [])

    async def new_session(self) -> dict[str, Any]:
        """Start a fresh Pi session."""
        resp = await self._command({"type": "new_session"}, timeout=10.0)
        if not resp.get("success", False):
            raise PiError(resp.get("error", "new_session failed"))
        return resp.get("data", {})

    async def abort(self) -> None:
        """Abort the current Pi operation."""
        await self._command({"type": "abort"}, timeout=10.0)


def config_from_env() -> PiConfig:
    """Build a PiConfig from environment variables."""
    return PiConfig(
        pi_bin=os.environ.get("PI_MCP_BIN", "pi"),
        cwd=os.environ.get("PI_MCP_CWD") or None,
        provider=os.environ.get("PI_MCP_PROVIDER") or None,
        model=os.environ.get("PI_MCP_MODEL") or None,
        session_dir=os.environ.get("PI_MCP_SESSION_DIR") or None,
        session_name=os.environ.get("PI_MCP_SESSION_NAME", "jw-pi-bridge"),
        no_session=os.environ.get("PI_MCP_NO_SESSION", "").lower() in ("1", "true", "yes"),
        extra_args=os.environ.get("PI_MCP_EXTRA_ARGS", "").split() if os.environ.get("PI_MCP_EXTRA_ARGS") else [],
    )
