import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from EvoScientist.pi_bridge.rpc import PiRPCClient


def _make_mock_process():
    proc = MagicMock()
    proc.returncode = None
    proc.stdin = AsyncMock()
    proc.stdout = MagicMock()
    proc.stdout.readline = AsyncMock(return_value=b"")
    proc.stderr = MagicMock()
    proc.stderr.readline = AsyncMock(return_value=b"")
    proc.terminate = MagicMock()
    proc.wait = AsyncMock(return_value=0)
    return proc


class TestPiRPCClient:
    async def test_send_prompt_writes_jsonl(self):
        proc = _make_mock_process()
        client = PiRPCClient(proc)
        task = asyncio.create_task(client.send_prompt("hello"))
        await asyncio.sleep(0)
        written = proc.stdin.write.await_args[0][0]
        assert written.endswith(b"\n")
        parsed = json.loads(written.decode("utf-8"))
        assert parsed["type"] == "prompt"
        assert parsed["message"] == "hello"
        assert parsed["id"].startswith("req_")
        # Inject preflight response
        client._handle_line(
            json.dumps(
                {
                    "type": "response",
                    "id": parsed["id"],
                    "command": "prompt",
                    "success": True,
                }
            )
        )
        response = await task
        assert response["success"] is True

    async def test_event_listener_receives_events(self):
        proc = _make_mock_process()
        client = PiRPCClient(proc)
        events = []
        client.on_event(events.append)
        client._handle_line(json.dumps({"type": "agent_start"}))
        client._handle_line(json.dumps({"type": "text_delta", "delta": "hi"}))
        assert len(events) == 2
        assert events[0]["type"] == "agent_start"

    async def test_response_resolves_pending_request(self):
        proc = _make_mock_process()
        client = PiRPCClient(proc)
        task = asyncio.create_task(client.send_command({"type": "get_state"}))
        await asyncio.sleep(0)
        written = proc.stdin.write.await_args[0][0]
        req_id = json.loads(written.decode("utf-8"))["id"]
        client._handle_line(
            json.dumps(
                {
                    "type": "response",
                    "id": req_id,
                    "command": "get_state",
                    "success": True,
                    "data": {"x": 1},
                }
            )
        )
        response = await task
        assert response["success"] is True
        assert response["data"]["x"] == 1

    async def test_process_exit_rejects_pending(self):
        proc = _make_mock_process()
        client = PiRPCClient(proc)
        task = asyncio.create_task(client.send_command({"type": "get_state"}))
        await asyncio.sleep(0)
        client._on_process_exit(1)
        with pytest.raises(RuntimeError):
            await task

    async def test_start_is_idempotent(self):
        proc = _make_mock_process()
        client = PiRPCClient(proc)
        client.start()
        read_task = client._read_task
        stderr_task = client._stderr_task
        client.start()
        assert client._read_task is read_task
        assert client._stderr_task is stderr_task

    async def test_listener_remove_during_fan_out_does_not_skip(self):
        proc = _make_mock_process()
        client = PiRPCClient(proc)
        called = []

        def second_listener(data):
            called.append("second")

        def first_listener(data):
            called.append("first")
            remove_second()

        client.on_event(first_listener)
        remove_second = client.on_event(second_listener)
        client._handle_line(json.dumps({"type": "agent_start"}))
        assert called == ["first", "second"]

    async def test_non_json_line_is_ignored(self):
        proc = _make_mock_process()
        client = PiRPCClient(proc)
        called = []
        client.on_event(called.append)
        client._handle_line("this is not json")
        assert not called

    async def test_send_command_when_process_exited_raises(self):
        proc = _make_mock_process()
        proc.returncode = 1
        client = PiRPCClient(proc)
        with pytest.raises(RuntimeError):
            await client.send_command({"type": "get_state"})

    async def test_timeout_cleans_up_pending_request(self):
        proc = _make_mock_process()
        proc.stdout.readline = AsyncMock(side_effect=asyncio.Event().wait)
        client = PiRPCClient(proc)
        with pytest.raises(RuntimeError, match="Timeout"):
            await client.send_command({"type": "get_state"}, timeout=0.01)
        assert not client._pending

    async def test_failed_response_raises(self):
        proc = _make_mock_process()
        client = PiRPCClient(proc)
        task = asyncio.create_task(client.send_command({"type": "prompt"}))
        await asyncio.sleep(0)
        written = proc.stdin.write.await_args[0][0]
        req_id = json.loads(written.decode("utf-8"))["id"]
        client._handle_line(
            json.dumps(
                {
                    "type": "response",
                    "id": req_id,
                    "command": "prompt",
                    "success": False,
                    "error": "No API key",
                }
            )
        )
        with pytest.raises(RuntimeError, match="No API key"):
            await task

    async def test_close_cancels_reader_tasks(self):
        proc = _make_mock_process()
        block = asyncio.Event()
        proc.stdout.readline = AsyncMock(side_effect=block.wait)
        proc.stderr.readline = AsyncMock(side_effect=block.wait)
        client = PiRPCClient(proc)
        client.start()
        await asyncio.sleep(0)
        assert not client._read_task.done()
        assert not client._stderr_task.done()
        await client.close()
        assert client._read_task.done()
        assert client._stderr_task.done()
