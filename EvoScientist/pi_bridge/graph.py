"""LangGraph-compatible wrapper around a pi RPC session."""

from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import EvoScientistConfig
from ..stream.emitter import StreamEventEmitter
from .process import PiProcessManager
from .rpc import PiRPCClient
from .session import PiSessionReader
from .tracing import PiBridgeTracer
from .translator import PiEventTranslator

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _PiStateSnapshot:
    values: Any = None
    next: tuple[str, ...] = ()
    interrupts: tuple[Any, ...] = ()
    tasks: tuple[Any, ...] = ()


class PiAgentGraph:
    """Drop-in replacement for a LangGraph CompiledStateGraph backed by pi."""

    def __init__(
        self,
        config: EvoScientistConfig,
        *,
        workspace_dir: str,
        process_manager: PiProcessManager | None = None,
        tracer: PiBridgeTracer | None = None,
    ) -> None:
        self.config = config
        self.workspace_dir = workspace_dir
        self._process_manager = process_manager or PiProcessManager(
            config, workspace_dir=workspace_dir
        )
        self._clients: dict[str, PiRPCClient] = {}
        self._client_lock = asyncio.Lock()
        self._thread_locks: dict[str, asyncio.Lock] = {}
        self._thread_locks_lock = asyncio.Lock()
        self._tracer = tracer or PiBridgeTracer()

    async def _thread_lock(self, thread_id: str) -> asyncio.Lock:
        async with self._thread_locks_lock:
            if thread_id not in self._thread_locks:
                self._thread_locks[thread_id] = asyncio.Lock()
            return self._thread_locks[thread_id]

    async def _ensure_process(self, thread_id: str) -> PiRPCClient:
        async with self._client_lock:
            if client := self._clients.get(thread_id):
                if client.process.returncode is None:
                    return client
                # Process died; clean up and restart
                logger.warning(
                    "pi client for thread %s has died; restarting", thread_id
                )
                del self._clients[thread_id]

            process = await self._process_manager.start(thread_id)
            client = PiRPCClient(process)
            client.start()
            self._clients[thread_id] = client
            return client

    async def astream_events(
        self,
        input: dict[str, Any],
        config: dict[str, Any],
        *,
        version: str = "v3",
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """LangGraph-compatible streaming entry point.

        Yields StreamEventEmitter-style event dicts (text, tool_call, tool_result,
        usage_stats, error, done).

        Concurrent calls for the same ``thread_id`` are serialized so only one
        prompt is in flight per pi session at a time.
        """
        thread_id = self._thread_id_from_config(config)
        lock = await self._thread_lock(thread_id)
        async with lock:
            async for event in self._astream_events_locked(
                thread_id, input, config, version=version, **kwargs
            ):
                yield event

    async def _astream_events_locked(
        self,
        thread_id: str,
        input: dict[str, Any],
        config: dict[str, Any],
        *,
        version: str = "v3",
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """Core streaming logic (must be called under the per-thread lock)."""
        message, images = self._extract_user_message_and_images(input)
        tags = {"thread_id": thread_id, "has_images": len(images) > 0}

        with self._tracer.span("pi_bridge.stream", thread_id=thread_id, tags=tags):
            client = await self._ensure_process(thread_id)
            translator = PiEventTranslator()
            queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
            stopped = False

            def on_event(event: dict[str, Any]) -> None:
                if stopped:
                    return
                for translated in translator.translate(event):
                    queue.put_nowait(translated)
                if event.get("type") == "agent_end":
                    queue.put_nowait({"type": "agent_end"})

            unsubscribe = client.on_event(on_event)
            try:
                await client.send_prompt(message, images=images)
            except Exception as exc:
                unsubscribe()
                self._tracer.increment(
                    "pi_bridge.prompt_error", tags={"thread_id": thread_id}
                )
                yield StreamEventEmitter.error(str(exc)).data
                yield StreamEventEmitter.done().data
                return

            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=300.0)
                    except TimeoutError:
                        self._tracer.increment(
                            "pi_bridge.stream_timeout",
                            tags={"thread_id": thread_id},
                        )
                        yield StreamEventEmitter.error(
                            "Timeout waiting for pi response"
                        ).data
                        break
                    self._process_manager.touch(thread_id)
                    if event.get("type") == "agent_end":
                        yield StreamEventEmitter.done(translator.full_response).data
                        break
                    yield event
            finally:
                stopped = True
                unsubscribe()

    async def aget_state(self, config: dict[str, Any]) -> _PiStateSnapshot:
        """LangGraph-compatible state snapshot read from pi's session file."""
        thread_id = self._thread_id_from_config(config)
        reader = PiSessionReader(self._process_manager.session_dir)
        messages = reader.read_messages(thread_id)
        return _PiStateSnapshot(values={"messages": messages})

    async def aupdate_state(
        self,
        config: dict[str, Any],
        values: dict[str, Any] | None,
        *,
        as_node: str | None = None,
    ) -> None:
        """No-op for pi bridge; state lives in pi's session file."""
        return None

    async def aclose(self) -> None:
        async with self._client_lock:
            for client in list(self._clients.values()):
                await client.close()
            self._clients.clear()
        await self._process_manager.stop_all()

    @staticmethod
    def _thread_id_from_config(config: dict[str, Any]) -> str:
        configurable = config.get("configurable") or {}
        thread_id = configurable.get("thread_id")
        if not thread_id:
            raise ValueError("PiAgentGraph requires config.configurable.thread_id")
        return str(thread_id)

    @staticmethod
    def _extract_user_message_and_images(
        input: dict[str, Any],
    ) -> tuple[str, list[dict[str, str]]]:
        """Extract the text prompt and image attachments from the user message.

        Image attachments are returned in pi's ``ImageContent`` shape:
        ``{"type": "image", "data": "<base64>", "mimeType": "image/png"}``.
        """
        messages = input.get("messages") or []
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "user":
                content = msg.get("content")
                if isinstance(content, str):
                    return content, []
                if isinstance(content, list):
                    texts: list[str] = []
                    images: list[dict[str, str]] = []
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "text":
                            texts.append(str(block.get("text", "")))
                        elif block.get("type") == "image_url":
                            image = PiAgentGraph._parse_image_url_block(block)
                            if image:
                                images.append(image)
                    return "\n".join(texts), images
        raise ValueError("No user message found in input")

    @staticmethod
    def _parse_image_url_block(block: dict[str, Any]) -> dict[str, str] | None:
        """Convert a LangChain image_url block into pi's ImageContent."""
        image_url = block.get("image_url")
        if isinstance(image_url, dict):
            url = str(image_url.get("url", ""))
        elif isinstance(image_url, str):
            url = image_url
        else:
            return None
        if url.startswith("data:"):
            # data:<mime>;base64,<data>
            header, _, b64 = url.partition(",")
            if not b64:
                return None
            mime = header[len("data:") :].partition(";")[0] or "image/png"
            return {"type": "image", "data": b64, "mimeType": mime}
        if url.startswith("http://") or url.startswith("https://"):
            # pi doesn't fetch URLs; leave it as a text reference.
            return None
        # Treat anything else as a local file path.
        try:
            path = Path(url)
            if not path.is_file():
                return None
            mime = mimetypes.guess_type(str(path))[0] or "image/png"
            b64 = base64.b64encode(path.read_bytes()).decode("ascii")
            return {"type": "image", "data": b64, "mimeType": mime}
        except Exception:
            return None
