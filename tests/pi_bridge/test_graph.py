import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jw.pi_bridge.graph import PiAgentGraph


class TestPiAgentGraph:
    @pytest.fixture
    def mock_config(self):
        cfg = MagicMock()
        cfg.agent_engine = "pi"
        cfg.pi_provider = "dashscope"
        cfg.pi_model = "qwen-plus"
        cfg.pi_session_dir = ""
        cfg.pi_args = ""
        cfg.pi_idle_timeout_seconds = 600
        cfg.pi_max_lifetime_seconds = 3600
        cfg.pi_max_processes = 5
        cfg.dashscope_api_key = "fake-key"
        return cfg

    @pytest.mark.asyncio
    async def test_astream_events_yields_translated_events(self, mock_config, tmp_path):
        graph = PiAgentGraph(mock_config, workspace_dir=str(tmp_path))

        fake_proc = MagicMock()
        fake_proc.returncode = None
        fake_proc.stdin = AsyncMock()
        fake_proc.stdout = AsyncMock()
        fake_proc.stdout.readline = AsyncMock(side_effect=asyncio.sleep(3600))
        fake_proc.stderr = AsyncMock()
        fake_proc.stderr.readline = AsyncMock(side_effect=asyncio.sleep(3600))
        fake_proc.terminate = MagicMock()
        fake_proc.wait = AsyncMock(return_value=0)

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            await graph._ensure_process("thread-1")

            client = graph._clients["thread-1"]

            # Patch send_prompt so it doesn't block waiting for a response;
            # inject pi events before astream_events starts consuming the queue.
            async def _fake_prompt(message, images=None):
                client._handle_line('{"type":"agent_start"}')
                client._handle_line(
                    '{"type":"message_update","assistantMessageEvent":{"type":"text_delta","delta":"hello"}}'
                )
                client._handle_line(
                    '{"type":"agent_end","messages":[],"willRetry":false}'
                )
                return {"type": "response", "success": True}

            client.send_prompt = _fake_prompt

            events = []
            async for event in graph.astream_events(
                {"messages": [{"role": "user", "content": "hi"}]},
                {"configurable": {"thread_id": "thread-1"}},
                version="v3",
            ):
                events.append(event)
                if event.get("type") == "done":
                    break

            text_events = [e for e in events if e.get("type") == "text"]
            assert len(text_events) == 1
            assert text_events[0]["content"] == "hello"
            assert any(e.get("type") == "done" for e in events)

    @pytest.mark.asyncio
    async def test_aget_state_returns_messages_from_session(
        self, mock_config, tmp_path
    ):
        graph = PiAgentGraph(mock_config, workspace_dir=str(tmp_path))
        session_dir = graph._process_manager.session_dir
        session_dir.mkdir(parents=True, exist_ok=True)
        session_file = session_dir / "2026-07-13T00-00-00-000Z_t1.jsonl"
        session_file.write_text(
            '{"type":"message","message":{"role":"user","content":[{"type":"text","text":"hi"}],"timestamp":1}}\n'
        )
        snapshot = await graph.aget_state({"configurable": {"thread_id": "t1"}})
        assert hasattr(snapshot, "values")
        messages = snapshot.values.get("messages", [])
        assert len(messages) == 1
        assert messages[0]["role"] == "user"

    def test_extract_user_message_and_images_text_only(self):
        text, images = PiAgentGraph._extract_user_message_and_images(
            {"messages": [{"role": "user", "content": "hello"}]}
        )
        assert text == "hello"
        assert images == []

    def test_extract_user_message_and_images_with_data_url(self):
        text, images = PiAgentGraph._extract_user_message_and_images(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "describe"},
                            {
                                "type": "image_url",
                                "image_url": {"url": "data:image/png;base64,abc123"},
                            },
                        ],
                    }
                ]
            }
        )
        assert text == "describe"
        assert len(images) == 1
        assert images[0] == {
            "type": "image",
            "data": "abc123",
            "mimeType": "image/png",
        }

    def test_extract_user_message_and_images_with_file_path(self, tmp_path):
        import base64

        img = tmp_path / "test.png"
        img.write_bytes(b"fake-image")
        b64 = base64.b64encode(b"fake-image").decode("ascii")
        text, images = PiAgentGraph._extract_user_message_and_images(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "analyze"},
                            {
                                "type": "image_url",
                                "image_url": {"url": str(img)},
                            },
                        ],
                    }
                ]
            }
        )
        assert text == "analyze"
        assert len(images) == 1
        assert images[0]["type"] == "image"
        assert images[0]["data"] == b64
        assert images[0]["mimeType"] == "image/png"

    @pytest.mark.asyncio
    async def test_concurrent_astream_events_for_same_thread_are_serialized(
        self, mock_config, tmp_path
    ):
        graph = PiAgentGraph(mock_config, workspace_dir=str(tmp_path))

        fake_proc = MagicMock()
        fake_proc.returncode = None
        fake_proc.stdin = AsyncMock()
        fake_proc.stdout = AsyncMock()
        fake_proc.stdout.readline = AsyncMock(side_effect=asyncio.sleep(3600))
        fake_proc.stderr = AsyncMock()
        fake_proc.stderr.readline = AsyncMock(side_effect=asyncio.sleep(3600))
        fake_proc.terminate = MagicMock()
        fake_proc.wait = AsyncMock(return_value=0)

        prompt_calls = []

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            await graph._ensure_process("thread-1")
            client = graph._clients["thread-1"]

            async def _fake_prompt(message, images=None):
                prompt_calls.append(message)
                await asyncio.sleep(0.05)
                client._handle_line('{"type":"agent_start"}')
                client._handle_line(
                    '{"type":"message_update","assistantMessageEvent":{"type":"text_delta","delta":"ok"}}'
                )
                client._handle_line(
                    '{"type":"agent_end","messages":[],"willRetry":false}'
                )
                return {"type": "response", "success": True}

            client.send_prompt = _fake_prompt

            results = await asyncio.gather(
                *[
                    self._collect_events(
                        graph,
                        {"messages": [{"role": "user", "content": f"msg-{i}"}]},
                        "thread-1",
                    )
                    for i in range(3)
                ]
            )

        # All three prompts ran, one at a time.
        assert len(prompt_calls) == 3
        assert len(set(prompt_calls)) == 3
        for events in results:
            assert any(e.get("type") == "done" for e in events)

        await graph.aclose()

    async def _collect_events(self, graph, input, thread_id):
        events = []
        async for event in graph.astream_events(
            input, {"configurable": {"thread_id": thread_id}}, version="v3"
        ):
            events.append(event)
            if event.get("type") == "done":
                break
        return events
