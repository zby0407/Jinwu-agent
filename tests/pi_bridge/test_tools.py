from unittest.mock import MagicMock, patch

from deepagents.backends.protocol import FileData, ReadResult

from EvoScientist.pi_bridge.tools import PiToolBridge


class TestPiToolBridge:
    def test_read_success(self):
        backend = MagicMock()
        backend.read.return_value = ReadResult(
            error=None,
            file_data=FileData(content="hello world", encoding="utf-8"),
        )
        bridge = PiToolBridge("/tmp/ws", backend=backend)
        result = bridge.read("/foo.txt")
        backend.read.assert_called_once_with("/foo.txt", offset=0, limit=2000)
        assert result["content"] == "hello world"
        assert result["isError"] is False

    def test_read_legacy_string(self):
        backend = MagicMock()
        backend.read.return_value = "hello world"
        bridge = PiToolBridge("/tmp/ws", backend=backend)
        result = bridge.read("/foo.txt")
        assert result["content"] == "hello world"
        assert result["isError"] is False

    def test_read_backend_error(self):
        backend = MagicMock()
        backend.read.return_value = ReadResult(error="file not found", file_data=None)
        bridge = PiToolBridge("/tmp/ws", backend=backend)
        result = bridge.read("/foo.txt")
        assert result["isError"] is True
        assert "file not found" in result["content"]

    def test_read_exception(self):
        backend = MagicMock()
        backend.read.side_effect = RuntimeError("boom")
        bridge = PiToolBridge("/tmp/ws", backend=backend)
        result = bridge.read("/foo.txt")
        assert result["isError"] is True
        assert "boom" in result["content"]

    def test_bash_success(self):
        backend = MagicMock()
        response = MagicMock()
        response.output = "ok"
        response.exit_code = 0
        response.truncated = False
        backend.execute.return_value = response
        bridge = PiToolBridge("/tmp/ws", backend=backend)
        result = bridge.bash("ls")
        backend.execute.assert_called_once_with("ls", timeout=None)
        assert result["content"] == "ok"
        assert result["isError"] is False
        assert result["details"]["exit_code"] == 0

    def test_bash_failure(self):
        backend = MagicMock()
        response = MagicMock()
        response.output = "not found"
        response.exit_code = 1
        response.truncated = False
        backend.execute.return_value = response
        bridge = PiToolBridge("/tmp/ws", backend=backend)
        result = bridge.bash("missing")
        assert result["isError"] is True
        assert "not found" in result["content"]

    def test_bash_truncated(self):
        backend = MagicMock()
        response = MagicMock()
        response.output = "lots"
        response.exit_code = 0
        response.truncated = True
        backend.execute.return_value = response
        bridge = PiToolBridge("/tmp/ws", backend=backend)
        result = bridge.bash("cat big")
        assert "(truncated)" in result["content"]

    def test_write_success(self):
        backend = MagicMock()
        result = MagicMock()
        result.error = None
        backend.write.return_value = result
        bridge = PiToolBridge("/tmp/ws", backend=backend)
        resp = bridge.write("/foo.txt", "hello")
        backend.write.assert_called_once_with("/foo.txt", "hello")
        assert resp["isError"] is False
        assert "Wrote" in resp["content"]

    def test_write_failure(self):
        backend = MagicMock()
        result = MagicMock()
        result.error = "permission denied"
        backend.write.return_value = result
        bridge = PiToolBridge("/tmp/ws", backend=backend)
        resp = bridge.write("/foo.txt", "hello")
        assert resp["isError"] is True
        assert "permission denied" in resp["content"]

    def test_edit_success(self):
        backend = MagicMock()
        result = MagicMock()
        result.error = None
        result.occurrences = 2
        backend.edit.return_value = result
        bridge = PiToolBridge("/tmp/ws", backend=backend)
        resp = bridge.edit("/foo.txt", "old", "new", replace_all=True)
        backend.edit.assert_called_once_with("/foo.txt", "old", "new", replace_all=True)
        assert resp["isError"] is False
        assert "2 occurrence" in resp["content"]

    def test_ls_success(self):
        backend = MagicMock()
        result = MagicMock()
        result.error = None
        result.entries = [{"path": "/foo.txt", "is_dir": False}]
        backend.ls.return_value = result
        bridge = PiToolBridge("/tmp/ws", backend=backend)
        resp = bridge.ls("/")
        assert resp["isError"] is False
        assert "foo.txt" in resp["content"]

    def test_glob_success(self):
        backend = MagicMock()
        result = MagicMock()
        result.error = None
        result.matches = [{"path": "/foo.txt"}]
        backend.glob.return_value = result
        bridge = PiToolBridge("/tmp/ws", backend=backend)
        resp = bridge.glob("*.txt", path="/")
        assert resp["isError"] is False
        assert "foo.txt" in resp["content"]

    def test_grep_success(self):
        backend = MagicMock()
        result = MagicMock()
        result.error = None
        result.matches = [{"path": "/foo.txt", "line": 1, "text": "hi"}]
        backend.grep.return_value = result
        bridge = PiToolBridge("/tmp/ws", backend=backend)
        resp = bridge.grep("hi", path="/", glob="*.txt")
        assert resp["isError"] is False
        assert "foo.txt" in resp["content"]


class TestPiToolBridgeEvoCapabilities:
    def test_skill_manager(self):
        bridge = PiToolBridge("/tmp/ws")
        with patch(
            "EvoScientist.tools.skill_manager.skill_manager"
        ) as mock_skill_manager:
            mock_skill_manager.invoke.return_value = "skill result"
            resp = bridge.skill_manager("list")
        assert resp["isError"] is False
        assert resp["content"] == "skill result"

    def test_schedule_tools(self):
        bridge = PiToolBridge("/tmp/ws")
        with (
            patch("EvoScientist.middleware.scheduler.schedule_task") as mock_schedule,
            patch(
                "EvoScientist.middleware.scheduler.list_scheduled_tasks"
            ) as mock_list,
            patch(
                "EvoScientist.middleware.scheduler.cancel_scheduled_task"
            ) as mock_cancel,
        ):
            mock_schedule.invoke.return_value = "scheduled"
            mock_list.invoke.return_value = "no tasks"
            mock_cancel.invoke.return_value = "cancelled"

            assert (
                bridge.schedule_task("x", "* * * * *", "do")["content"] == "scheduled"
            )
            assert bridge.list_scheduled_tasks()["content"] == "no tasks"
            assert bridge.cancel_scheduled_task("abc")["content"] == "cancelled"

    def test_memory_tools(self):
        bridge = PiToolBridge("/tmp/ws", source_session_id="t1")
        with (
            patch(
                "EvoScientist.memory.create_search_observations_tool"
            ) as mock_search_factory,
            patch("EvoScientist.memory.create_read_memory_tool") as mock_read_factory,
            patch(
                "EvoScientist.memory.create_record_observation_tool"
            ) as mock_record_factory,
        ):
            mock_search = MagicMock()
            mock_search.invoke.return_value = '{"results": []}'
            mock_search_factory.return_value = mock_search

            mock_read = MagicMock()
            mock_read.invoke.return_value = '{"text": "body"}'
            mock_read_factory.return_value = mock_read

            mock_record = MagicMock()
            mock_record.invoke.return_value = '{"created": true}'
            mock_record_factory.return_value = mock_record

            assert bridge.search_observations("q")["content"] == '{"results": []}'
            assert bridge.read_memory("O-1")["content"] == '{"text": "body"}'
            assert (
                bridge.record_observation(
                    memory_type="semantic",
                    summary="s",
                    observation="o",
                    why_it_matters="w",
                )["content"]
                == '{"created": true}'
            )
