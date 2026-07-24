import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jw.pi_bridge.process import PiProcessManager


class TestPiProcessManager:
    def _make_cfg(self):
        cfg = MagicMock()
        cfg.pi_provider = "dashscope"
        cfg.pi_model = "qwen-plus"
        cfg.pi_args = ""
        cfg.pi_idle_timeout_seconds = 600
        cfg.pi_max_lifetime_seconds = 3600
        cfg.pi_max_processes = 5
        return cfg

    def test_build_command_uses_session_id_and_dir(self, tmp_path):
        cfg = self._make_cfg()
        mgr = PiProcessManager(cfg, pi_cli=Path("/fake/pi.js"), session_dir=tmp_path)
        cmd = mgr._build_command("thread-123")
        assert cmd[0] == "node"
        assert cmd[1] == str(Path("/fake/pi.js"))
        assert "--mode" in cmd
        assert "rpc" in cmd
        assert "--provider" in cmd
        assert "dashscope" in cmd
        assert "--model" in cmd
        assert "qwen-plus" in cmd
        assert "--session-dir" in cmd
        assert str(tmp_path) in cmd
        assert "--session-id" in cmd
        assert "thread-123" in cmd

    def test_constructor_does_not_require_pi_on_path(self, tmp_path):
        cfg = self._make_cfg()
        with patch.object(
            PiProcessManager,
            "_find_pi_cli",
            side_effect=AssertionError("pi lookup must be lazy"),
        ):
            mgr = PiProcessManager(cfg, session_dir=tmp_path)
        assert mgr.pi_cli is None

    def test_resolve_provider_and_model_fallback(self, tmp_path):
        cfg = self._make_cfg()
        cfg.provider = "anthropic"
        cfg.model = "claude-sonnet-4-6"
        cfg.pi_provider = ""
        cfg.pi_model = ""
        mgr = PiProcessManager(cfg, pi_cli=Path("/fake/pi.js"), session_dir=tmp_path)
        provider, model = mgr._resolve_provider_and_model()
        assert provider == "anthropic"
        assert model == "claude-sonnet-4-6"

    async def test_start_spawns_process_and_caches(self, tmp_path):
        cfg = self._make_cfg()
        mgr = PiProcessManager(cfg, pi_cli=Path("/fake/pi.js"), session_dir=tmp_path)
        fake_proc = MagicMock()
        fake_proc.returncode = None
        fake_proc.stdin = AsyncMock()
        fake_proc.stdout = MagicMock()

        with patch(
            "asyncio.create_subprocess_exec", return_value=fake_proc
        ) as mock_exec:
            proc = await mgr.start("thread-123")
            assert proc is fake_proc
            assert mgr._processes["thread-123"] is fake_proc
            mock_exec.assert_called_once()

    async def test_stop_terminates_process(self, tmp_path):
        cfg = self._make_cfg()
        mgr = PiProcessManager(cfg, pi_cli=Path("/fake/pi.js"), session_dir=tmp_path)
        fake_proc = MagicMock()
        fake_proc.returncode = None
        fake_proc.stdin = AsyncMock()
        fake_proc.stdout = MagicMock()
        fake_proc.terminate = MagicMock()
        fake_proc.wait = AsyncMock(return_value=0)

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            await mgr.start("thread-123")
            await mgr.stop("thread-123")
            fake_proc.terminate.assert_called_once()

    async def test_concurrent_start_spawns_once(self, tmp_path):
        cfg = self._make_cfg()
        cfg.pi_session_dir = ""
        mgr = PiProcessManager(cfg, pi_cli=Path("/fake/pi.js"), session_dir=tmp_path)
        fake_proc = MagicMock()
        fake_proc.returncode = None
        fake_proc.stdin = AsyncMock()
        fake_proc.stdout = MagicMock()
        fake_proc.stderr = MagicMock()

        with patch(
            "asyncio.create_subprocess_exec", return_value=fake_proc
        ) as mock_exec:
            procs = await asyncio.gather(*[mgr.start("thread-x") for _ in range(5)])
            assert all(p is fake_proc for p in procs)
            mock_exec.assert_called_once()

    async def test_start_replaces_dead_process(self, tmp_path):
        cfg = self._make_cfg()
        cfg.pi_session_dir = ""
        mgr = PiProcessManager(cfg, pi_cli=Path("/fake/pi.js"), session_dir=tmp_path)

        alive_proc = MagicMock()
        alive_proc.returncode = None
        alive_proc.stdin = AsyncMock()
        alive_proc.stdout = MagicMock()
        alive_proc.stderr = MagicMock()

        replacement_proc = MagicMock()
        replacement_proc.returncode = None
        replacement_proc.stdin = AsyncMock()
        replacement_proc.stdout = MagicMock()
        replacement_proc.stderr = MagicMock()

        with patch(
            "asyncio.create_subprocess_exec", side_effect=[alive_proc, replacement_proc]
        ) as mock_exec:
            first = await mgr.start("thread-x")
            assert first is alive_proc
            # Simulate the cached process dying later
            alive_proc.returncode = 1
            second = await mgr.start("thread-x")
            assert second is replacement_proc
            assert mock_exec.call_count == 2

    async def test_start_raises_on_immediate_exit(self, tmp_path):
        cfg = self._make_cfg()
        cfg.pi_session_dir = ""
        mgr = PiProcessManager(cfg, pi_cli=Path("/fake/pi.js"), session_dir=tmp_path)

        fake_proc = MagicMock()
        fake_proc.returncode = 1
        fake_proc.stdin = AsyncMock()
        fake_proc.stdout = MagicMock()
        fake_proc.stderr = AsyncMock()
        fake_proc.stderr.read = AsyncMock(return_value=b"startup failed")

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            with pytest.raises(RuntimeError, match="pi exited immediately"):
                await mgr.start("thread-123")

    def test_build_env_maps_api_keys_and_preserves_existing(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "from-env")
        cfg = self._make_cfg()
        cfg.dashscope_api_key = "from-config"
        cfg.openai_api_key = "openai-key"
        cfg.google_api_key = "google-key"
        cfg.dangerous_mode = False
        cfg.sandbox_execute_timeout = 300
        mgr = PiProcessManager(cfg, pi_cli=Path("/fake/pi.js"), session_dir=tmp_path)
        env = mgr._build_env()
        assert env["DASHSCOPE_API_KEY"] == "from-env"  # existing env preserved
        assert env["OPENAI_API_KEY"] == "openai-key"
        assert env["GEMINI_API_KEY"] == "google-key"

    def test_build_command_includes_extension_when_workspace_dir_given(self, tmp_path):
        cfg = self._make_cfg()
        cfg.dangerous_mode = False
        cfg.sandbox_execute_timeout = 300
        mgr = PiProcessManager(
            cfg,
            pi_cli=Path("/fake/pi.js"),
            session_dir=tmp_path,
            workspace_dir=str(tmp_path),
        )
        cmd = mgr._build_command("thread-123")
        assert "--extension" in cmd
        ext_idx = cmd.index("--extension") + 1
        assert "extension.js" in cmd[ext_idx]

    def test_build_env_includes_socket_when_workspace_dir_given(self, tmp_path):
        cfg = self._make_cfg()
        cfg.dangerous_mode = False
        cfg.sandbox_execute_timeout = 300
        mgr = PiProcessManager(
            cfg,
            pi_cli=Path("/fake/pi.js"),
            session_dir=tmp_path,
            workspace_dir=str(tmp_path),
        )
        env = mgr._build_env("thread-123")
        assert "JW_PI_TOOL_SOCKET" in env
        assert env["JW_PI_TOOL_SOCKET"].endswith("thread-123.sock")

    def test_touch_updates_last_activity(self, tmp_path):
        cfg = self._make_cfg()
        mgr = PiProcessManager(cfg, pi_cli=Path("/fake/pi.js"), session_dir=tmp_path)
        fake_proc = MagicMock()
        fake_proc.returncode = None
        mgr._processes["t1"] = fake_proc
        mgr._last_activity["t1"] = 0.0
        mgr.touch("t1")
        assert mgr._last_activity["t1"] > 0.0

    async def test_idle_timeout_stops_process(self, tmp_path):
        cfg = self._make_cfg()
        cfg.pi_idle_timeout_seconds = 0.1
        cfg.pi_max_lifetime_seconds = 3600
        mgr = PiProcessManager(cfg, pi_cli=Path("/fake/pi.js"), session_dir=tmp_path)
        fake_proc = MagicMock()
        fake_proc.returncode = None
        fake_proc.stdin = AsyncMock()
        fake_proc.stdout = MagicMock()
        fake_proc.terminate = MagicMock()
        fake_proc.wait = AsyncMock(return_value=0)

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            await mgr.start("t1")
            assert "t1" in mgr._processes
            # Wait for idle watcher to collect the process.
            await asyncio.sleep(0.4)
            assert "t1" not in mgr._processes
            fake_proc.terminate.assert_called_once()
        await mgr.stop_all()

    async def test_max_lifetime_stops_process(self, tmp_path):
        cfg = self._make_cfg()
        cfg.pi_idle_timeout_seconds = 3600
        cfg.pi_max_lifetime_seconds = 0.1
        mgr = PiProcessManager(cfg, pi_cli=Path("/fake/pi.js"), session_dir=tmp_path)
        fake_proc = MagicMock()
        fake_proc.returncode = None
        fake_proc.stdin = AsyncMock()
        fake_proc.stdout = MagicMock()
        fake_proc.terminate = MagicMock()
        fake_proc.wait = AsyncMock(return_value=0)

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            await mgr.start("t1")
            await asyncio.sleep(0.4)
            assert "t1" not in mgr._processes
            fake_proc.terminate.assert_called_once()
        await mgr.stop_all()

    async def test_max_processes_evicts_lru(self, tmp_path):
        cfg = self._make_cfg()
        cfg.pi_max_processes = 2
        mgr = PiProcessManager(cfg, pi_cli=Path("/fake/pi.js"), session_dir=tmp_path)

        def _make_proc():
            p = MagicMock()
            p.returncode = None
            p.stdin = AsyncMock()
            p.stdout = MagicMock()
            p.terminate = MagicMock()
            p.wait = AsyncMock(return_value=0)
            return p

        procs = [_make_proc(), _make_proc(), _make_proc()]
        with patch("asyncio.create_subprocess_exec", side_effect=procs) as mock_exec:
            await mgr.start("t1")
            await asyncio.sleep(0.01)
            await mgr.start("t2")
            await asyncio.sleep(0.01)
            # Starting a third should evict the least-active (t1).
            await mgr.start("t3")
            await asyncio.sleep(0.01)

        assert "t1" not in mgr._processes
        assert "t2" in mgr._processes
        assert "t3" in mgr._processes
        procs[0].terminate.assert_called_once()
        assert mock_exec.call_count == 3
        await mgr.stop_all()
