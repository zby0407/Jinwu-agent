"""Tests for pi bridge tracing helpers."""

import pytest

from EvoScientist.pi_bridge.tracing import PiBridgeTracer


class TestPiBridgeTracer:
    def test_span_records_duration(self):
        tracer = PiBridgeTracer()
        with tracer.span("test", thread_id="t1", tags={"x": 1}) as span:
            pass
        assert span.duration_ms >= 0
        assert span.name == "test"
        assert span.thread_id == "t1"

    def test_counter_increments(self):
        tracer = PiBridgeTracer()
        tracer.increment("a")
        tracer.increment("a")
        tracer.increment("b")
        assert tracer.counters() == {"a": 2, "b": 1}

    @pytest.mark.asyncio
    async def test_span_works_in_async_context(self):
        tracer = PiBridgeTracer()
        with tracer.span("async") as span:
            await asyncio.sleep(0)
        assert span.duration_ms >= 0


import asyncio  # noqa: E402
