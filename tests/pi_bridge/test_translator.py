from jw.pi_bridge.translator import PiEventTranslator
from jw.stream.emitter import StreamEventEmitter


class TestPiEventTranslator:
    def test_text_delta_emits_text(self):
        emitter = StreamEventEmitter()
        translator = PiEventTranslator(emitter)
        events = translator.translate(
            {
                "type": "message_update",
                "assistantMessageEvent": {
                    "type": "text_delta",
                    "delta": "hello",
                },
            }
        )
        assert len(events) == 1
        assert events[0]["type"] == "text"
        assert events[0]["content"] == "hello"

    def test_toolcall_end_emits_tool_call(self):
        emitter = StreamEventEmitter()
        translator = PiEventTranslator(emitter)
        events = translator.translate(
            {
                "type": "message_update",
                "assistantMessageEvent": {
                    "type": "toolcall_end",
                    "toolCall": {
                        "type": "toolCall",
                        "id": "call_1",
                        "name": "read",
                        "arguments": {"path": "/x"},
                    },
                },
            }
        )
        assert len(events) == 1
        assert events[0]["type"] == "tool_call"
        assert events[0]["name"] == "read"
        assert events[0]["args"] == {"path": "/x"}
        assert events[0]["id"] == "call_1"

    def test_tool_execution_end_emits_tool_result(self):
        emitter = StreamEventEmitter()
        translator = PiEventTranslator(emitter)
        events = translator.translate(
            {
                "type": "tool_execution_end",
                "toolCallId": "call_1",
                "toolName": "read",
                "result": {"content": [{"type": "text", "text": "contents"}]},
                "isError": False,
            }
        )
        assert len(events) == 1
        assert events[0]["type"] == "tool_result"
        assert events[0]["name"] == "read"
        assert events[0]["content"] == "contents"
        assert events[0]["success"] is True
        assert events[0]["id"] == "call_1"

    def test_message_end_usage_emits_usage_stats(self):
        emitter = StreamEventEmitter()
        translator = PiEventTranslator(emitter)
        events = translator.translate(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "usage": {"input": 10, "output": 5},
                },
            }
        )
        usage = [e for e in events if e["type"] == "usage_stats"]
        assert len(usage) == 1
        assert usage[0]["input_tokens"] == 10
        assert usage[0]["output_tokens"] == 5

    def test_error_event(self):
        emitter = StreamEventEmitter()
        translator = PiEventTranslator(emitter)
        events = translator.translate(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "errorMessage": "boom",
                    "stopReason": "error",
                },
            }
        )
        errors = [e for e in events if e["type"] == "error"]
        assert len(errors) == 1
        assert errors[0]["message"] == "boom"

    def test_empty_text_delta_returns_empty(self):
        emitter = StreamEventEmitter()
        translator = PiEventTranslator(emitter)
        events = translator.translate(
            {
                "type": "message_update",
                "assistantMessageEvent": {"type": "text_delta", "delta": ""},
            }
        )
        assert events == []

    def test_duplicate_toolcall_end_deduplicates(self):
        emitter = StreamEventEmitter()
        translator = PiEventTranslator(emitter)
        event = {
            "type": "message_update",
            "assistantMessageEvent": {
                "type": "toolcall_end",
                "toolCall": {
                    "type": "toolCall",
                    "id": "call_1",
                    "name": "read",
                    "arguments": {"path": "/x"},
                },
            },
        }
        first = translator.translate(event)
        second = translator.translate(event)
        assert len(first) == 1
        assert second == []

    def test_tool_execution_end_error(self):
        emitter = StreamEventEmitter()
        translator = PiEventTranslator(emitter)
        events = translator.translate(
            {
                "type": "tool_execution_end",
                "toolCallId": "call_1",
                "toolName": "read",
                "result": {"content": "failed"},
                "isError": True,
            }
        )
        assert len(events) == 1
        assert events[0]["type"] == "tool_result"
        assert events[0]["success"] is False

    def test_unknown_event_type_returns_empty(self):
        emitter = StreamEventEmitter()
        translator = PiEventTranslator(emitter)
        events = translator.translate({"type": "weird_event"})
        assert events == []

    def test_malformed_assistant_message_event(self):
        emitter = StreamEventEmitter()
        translator = PiEventTranslator(emitter)
        events = translator.translate(
            {"type": "message_update", "assistantMessageEvent": {"foo": "bar"}}
        )
        assert events == []

    def test_message_end_zero_usage_does_not_emit(self):
        emitter = StreamEventEmitter()
        translator = PiEventTranslator(emitter)
        events = translator.translate(
            {
                "type": "message_end",
                "message": {"role": "assistant", "usage": {"input": 0, "output": 0}},
            }
        )
        assert all(e["type"] != "usage_stats" for e in events)

    def test_multiple_text_deltas_accumulate_full_response(self):
        emitter = StreamEventEmitter()
        translator = PiEventTranslator(emitter)
        for chunk in ["hello ", "world"]:
            translator.translate(
                {
                    "type": "message_update",
                    "assistantMessageEvent": {"type": "text_delta", "delta": chunk},
                }
            )
        assert translator.full_response == "hello world"
