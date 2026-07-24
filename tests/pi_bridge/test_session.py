"""Tests for pi session file reading."""

from jw.pi_bridge.session import PiSessionReader


class TestPiSessionReader:
    def test_find_session_file(self, tmp_path):
        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        old = session_dir / "2026-07-12T00-00-00-000Z_t1.jsonl"
        new = session_dir / "2026-07-13T00-00-00-000Z_t1.jsonl"
        other = session_dir / "2026-07-13T00-00-00-000Z_t2.jsonl"
        old.write_text("")
        new.write_text("")
        other.write_text("")
        reader = PiSessionReader(session_dir)
        found = reader.find_session_file("t1")
        assert found == new

    def test_read_messages_filters_by_type_and_role(self, tmp_path):
        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        path = session_dir / "2026-07-13T00-00-00-000Z_t1.jsonl"
        path.write_text(
            "\n".join(
                [
                    '{"type":"session","id":"t1"}',
                    '{"type":"message","message":{"role":"user","content":[{"type":"text","text":"hi"}],"timestamp":1}}',
                    '{"type":"message","message":{"role":"assistant","content":[{"type":"text","text":"hello"}],"timestamp":2}}',
                    '{"type":"message","message":{"role":"toolResult","toolCallId":"tc1","content":"ok","timestamp":3}}',
                    '{"type":"message","message":{"role":"system","content":"ignored","timestamp":4}}',
                    "not valid json",
                ]
            )
        )
        reader = PiSessionReader(session_dir)
        messages = reader.read_messages("t1")
        assert len(messages) == 3
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"
        assert messages[2]["role"] == "toolResult"

    def test_read_messages_returns_empty_when_no_file(self, tmp_path):
        reader = PiSessionReader(tmp_path)
        assert reader.read_messages("missing") == []
