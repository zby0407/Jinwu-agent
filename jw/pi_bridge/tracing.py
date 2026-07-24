"""Lightweight observability helpers for the pi bridge."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_current_span: ContextVar[PiSpan | None] = ContextVar(
    "pi_bridge_current_span", default=None
)


@dataclass
class PiSpan:
    """A simple manually-managed span for pi bridge operations."""

    name: str
    thread_id: str | None = None
    start_time: float = field(default_factory=time.monotonic)
    end_time: float | None = None
    tags: dict[str, Any] = field(default_factory=dict)
    children: list[PiSpan] = field(default_factory=list)

    def finish(self) -> None:
        self.end_time = time.monotonic()

    @property
    def duration_ms(self) -> float:
        end = self.end_time or time.monotonic()
        return (end - self.start_time) * 1000.0


class PiBridgeTracer:
    """Minimal tracer: logs spans and maintains lightweight counters."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    def increment(
        self, name: str, *, delta: int = 1, tags: dict[str, Any] | None = None
    ) -> None:
        self._counters[name] = self._counters.get(name, 0) + delta
        extra = {**tags} if tags else {}
        logger.debug("pi bridge counter %s+%s %s", name, delta, extra)

    @contextmanager
    def span(
        self,
        name: str,
        *,
        thread_id: str | None = None,
        tags: dict[str, Any] | None = None,
    ) -> Iterator[PiSpan]:
        span = PiSpan(name=name, thread_id=thread_id, tags=tags or {})
        parent = _current_span.get()
        if parent is not None:
            parent.children.append(span)
        token = _current_span.set(span)
        try:
            logger.debug(
                "pi bridge span start: %s thread=%s tags=%s",
                name,
                thread_id,
                span.tags,
            )
            yield span
        finally:
            span.finish()
            _current_span.reset(token)
            logger.info(
                "pi bridge span end: %s thread=%s duration_ms=%.2f",
                name,
                thread_id,
                span.duration_ms,
            )

    def counters(self) -> dict[str, int]:
        return dict(self._counters)


def get_current_span() -> PiSpan | None:
    return _current_span.get()
