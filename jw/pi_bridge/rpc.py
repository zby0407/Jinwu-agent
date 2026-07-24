"""JSONL RPC client for a running pi process."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class PiRPCClient:
    """Talk to one pi RPC subprocess over stdin/stdout."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.process = process
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._request_id = 0
        self._read_task: asyncio.Task[Any] | None = None
        self._stderr_task: asyncio.Task[Any] | None = None
        self._closed = False

    def on_event(
        self, listener: Callable[[dict[str, Any]], None]
    ) -> Callable[[], None]:
        self._listeners.append(listener)

        def remove() -> None:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

        return remove

    def start(self) -> None:
        """Begin reading stdout/stderr. Idempotent."""
        if self._read_task is not None:
            return
        self._read_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())

    async def _read_stdout(self) -> None:
        stdout = self.process.stdout
        if stdout is None:
            return
        try:
            while True:
                line = await stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\n")
                if text:
                    self._handle_line(text)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("pi stdout reader error: %s", exc)
        finally:
            self._on_process_exit(self.process.returncode)

    async def _read_stderr(self) -> None:
        stderr = self.process.stderr
        if stderr is None:
            return
        try:
            while True:
                line = await stderr.readline()
                if not line:
                    break
                logger.warning(
                    "pi stderr: %s", line.decode("utf-8", errors="replace").rstrip()
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    def _handle_line(self, line: str) -> None:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("pi emitted non-JSON line: %s", line[:200])
            return

        if (
            isinstance(data, dict)
            and data.get("type") == "response"
            and data.get("id") in self._pending
        ):
            future = self._pending.pop(data["id"])
            if not future.done():
                future.set_result(data)
            return

        for listener in self._listeners[:]:
            try:
                listener(data)
            except Exception:
                logger.exception("pi event listener failed")

    def _on_process_exit(self, code: int | None) -> None:
        msg = f"pi process exited (code={code})"
        error = RuntimeError(msg)
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    async def send_command(
        self, command: dict[str, Any], timeout: float = 60.0
    ) -> dict[str, Any]:
        if self.process.returncode is not None:
            raise RuntimeError(
                f"pi process is not running (code={self.process.returncode})"
            )
        if self._read_task is None:
            self.start()
        self._request_id += 1
        req_id = f"req_{self._request_id}"
        payload = {**command, "id": req_id}
        line = json.dumps(payload, ensure_ascii=False) + "\n"

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[req_id] = future

        try:
            write_result = self.process.stdin.write(line.encode("utf-8"))
            if asyncio.iscoroutine(write_result):
                await write_result
            await self.process.stdin.drain()
        except Exception as exc:
            self._pending.pop(req_id, None)
            raise RuntimeError(f"Failed to write to pi stdin: {exc}") from exc

        try:
            response = await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            self._pending.pop(req_id, None)
            raise RuntimeError(
                f"Timeout waiting for pi response to {command.get('type')}"
            ) from None

        if isinstance(response, dict) and response.get("success") is False:
            error = response.get("error") or "pi command failed"
            raise RuntimeError(f"pi {command.get('type')} failed: {error}")
        return response

    async def send_prompt(
        self, message: str, images: list[str] | None = None
    ) -> dict[str, Any]:
        return await self.send_command(
            {"type": "prompt", "message": message, "images": images or []}
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for task in (self._read_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
