"""Manage pi Agent subprocesses."""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import shutil
import time
from pathlib import Path
from typing import Any

from EvoScientist.config import EvoScientistConfig

from .tool_server import PiToolServer
from .tools import PiToolBridge

logger = logging.getLogger(__name__)


class PiProcessManager:
    """Spawn and cache one pi RPC process per thread_id.

    Enforces resource limits:
      - idle timeout: stop a process after inactivity
      - max lifetime: hard cap on total process age
      - max processes: LRU eviction when the cache is full
    """

    def __init__(
        self,
        config: EvoScientistConfig,
        *,
        workspace_dir: str | None = None,
        pi_cli: Path | None = None,
        session_dir: Path | None = None,
        data_dir: Path | None = None,
        tool_bridge: PiToolBridge | None = None,
    ) -> None:
        self.config = config
        self.workspace_dir = workspace_dir
        self.pi_cli = pi_cli or self._find_pi_cli()
        self.session_dir = self._resolve_session_dir(session_dir, data_dir)
        self._tool_bridge = tool_bridge
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._tool_servers: dict[str, PiToolServer] = {}
        self._start_lock = asyncio.Lock()
        self._last_activity: dict[str, float] = {}
        self._start_times: dict[str, float] = {}
        self._idle_watcher_task: asyncio.Task[Any] | None = None
        self._shutdown = False

    @staticmethod
    def _find_pi_cli() -> Path:
        pi = shutil.which("pi")
        if not pi:
            raise RuntimeError("pi executable not found on PATH")
        return Path(pi).resolve()

    def _resolve_session_dir(
        self, session_dir: Path | None, data_dir: Path | None
    ) -> Path:
        if session_dir is not None:
            return session_dir
        if self.config.pi_session_dir:
            return Path(self.config.pi_session_dir)
        if data_dir is not None:
            return data_dir / "pi-sessions"
        from ..paths import DATA_DIR

        return DATA_DIR / "pi-sessions"

    @staticmethod
    def _extension_path() -> Path:
        """Path to the pi extension that forwards tool calls to Python."""
        return Path(__file__).parent / "extension" / "extension.js"

    def _socket_path(self, thread_id: str) -> Path:
        """Short Unix socket path for a thread's tool server."""
        # Use /tmp to avoid macOS AF_UNIX path length limits.
        return Path(f"/tmp/pi-bridge-{os.getpid()}-{thread_id}.sock")

    def _ensure_tool_bridge(self, thread_id: str = "") -> PiToolBridge | None:
        if self._tool_bridge is not None:
            return self._tool_bridge
        if not self.workspace_dir:
            return None
        return PiToolBridge(
            self.workspace_dir,
            self.config,
            source_session_id=thread_id or "pi",
        )

    def _resolve_provider_and_model(self) -> tuple[str, str]:
        provider = self.config.pi_provider or self.config.provider
        model = self.config.pi_model or self.config.model
        return provider, model

    def _build_command(self, thread_id: str) -> list[str]:
        provider, model = self._resolve_provider_and_model()
        cmd = [
            "node",
            str(self.pi_cli),
            "--mode",
            "rpc",
            "--provider",
            provider,
            "--model",
            model,
            "--session-dir",
            str(self.session_dir),
            "--session-id",
            thread_id,
        ]
        if self._ensure_tool_bridge(thread_id) is not None:
            cmd.extend(["--extension", str(self._extension_path())])
        if self.config.pi_args:
            cmd.extend(shlex.split(self.config.pi_args or ""))
        return cmd

    def _build_env(self, thread_id: str = "") -> dict[str, str]:
        env = dict(os.environ)
        # The pi CLI expects GEMINI_API_KEY for Google models, while EvoScientist
        # config uses google_api_key. Map other provider keys identically.
        key_mappings = {
            "dashscope_api_key": "DASHSCOPE_API_KEY",
            "openai_api_key": "OPENAI_API_KEY",
            "anthropic_api_key": "ANTHROPIC_API_KEY",
            "google_api_key": "GEMINI_API_KEY",
            "deepseek_api_key": "DEEPSEEK_API_KEY",
            "kimi_api_key": "KIMI_API_KEY",
            "moonshot_api_key": "MOONSHOT_API_KEY",
        }
        for cfg_key, env_key in key_mappings.items():
            value = getattr(self.config, cfg_key, "")
            if value and not env.get(env_key):
                env[env_key] = value
        if self._ensure_tool_bridge(thread_id) is not None:
            env["EVOSCIENTIST_PI_TOOL_SOCKET"] = str(self._socket_path(thread_id))
        return env

    def _idle_timeout(self) -> float:
        return float(getattr(self.config, "pi_idle_timeout_seconds", 600) or 600)

    def _max_lifetime(self) -> float:
        return float(getattr(self.config, "pi_max_lifetime_seconds", 3600) or 3600)

    def _max_processes(self) -> int:
        value = getattr(self.config, "pi_max_processes", 5)
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return 5

    def touch(self, thread_id: str) -> None:
        """Mark a process as active (resets idle timer)."""
        if thread_id in self._processes:
            self._last_activity[thread_id] = time.monotonic()

    async def start(self, thread_id: str) -> asyncio.subprocess.Process:
        if proc := self._processes.get(thread_id):
            if proc.returncode is None:
                self.touch(thread_id)
                return proc

        async with self._start_lock:
            if proc := self._processes.get(thread_id):
                if proc.returncode is None:
                    self.touch(thread_id)
                    return proc
                self._processes.pop(thread_id, None)
                logger.warning(
                    "pi process for thread %s exited (code=%s); restarting",
                    thread_id,
                    proc.returncode,
                )

            await self._maybe_evict_lru()
            self._ensure_idle_watcher()
            self.session_dir.mkdir(parents=True, exist_ok=True)

            # Start the tool bridge socket server before spawning pi so the
            # extension can connect immediately on load.
            tool_bridge = self._ensure_tool_bridge(thread_id)
            if tool_bridge is not None:
                socket_path = self._socket_path(thread_id)
                server = PiToolServer(tool_bridge, socket_path=socket_path)
                await server.start()
                self._tool_servers[thread_id] = server

            cmd = self._build_command(thread_id)
            env = self._build_env(thread_id)
            logger.info("Starting pi RPC: %s", " ".join(cmd))
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            self._processes[thread_id] = proc
            now = time.monotonic()
            self._last_activity[thread_id] = now
            self._start_times[thread_id] = now
            await asyncio.sleep(0.1)
            if proc.returncode is not None:
                stderr = await proc.stderr.read() if proc.stderr else b""
                # Clean up the tool server if pi failed immediately.
                await self._stop_tool_server(thread_id)
                raise RuntimeError(
                    f"pi exited immediately (code={proc.returncode}): {stderr.decode(errors='replace')}"
                )
            return proc

    async def _maybe_evict_lru(self) -> None:
        """If we're at the process limit, stop the least-recently active one."""
        max_proc = self._max_processes()
        if len(self._processes) < max_proc:
            return
        # Sort by last activity ascending; evict the oldest idle process.
        candidates = sorted(
            self._processes.keys(),
            key=lambda tid: self._last_activity.get(tid, 0.0),
        )
        to_evict = candidates[: len(self._processes) - max_proc + 1]
        for tid in to_evict:
            logger.info(
                "Evicting pi process for thread %s due to process limit (%s)",
                tid,
                max_proc,
            )
            await self.stop(tid)

    def _ensure_idle_watcher(self) -> None:
        """Start the periodic idle/lifetime watcher if not running."""
        if self._shutdown:
            return
        if self._idle_watcher_task is not None and not self._idle_watcher_task.done():
            return
        self._idle_watcher_task = asyncio.create_task(self._idle_watcher_loop())

    def _idle_check_interval(self) -> float:
        """Check frequently enough to catch short timeouts in tests."""
        limits = [self._idle_timeout(), self._max_lifetime()]
        smallest = min(limit for limit in limits if limit > 0)
        return min(30.0, max(0.1, smallest / 2.0))

    async def _idle_watcher_loop(self) -> None:
        """Periodically stop processes that are idle or over max lifetime."""
        while not self._shutdown:
            try:
                await asyncio.sleep(self._idle_check_interval())
            except asyncio.CancelledError:
                break
            now = time.monotonic()
            idle_timeout = self._idle_timeout()
            max_lifetime = self._max_lifetime()
            expired = []
            for tid, proc in list(self._processes.items()):
                if proc.returncode is not None:
                    expired.append(tid)
                    continue
                last = self._last_activity.get(tid, now)
                started = self._start_times.get(tid, now)
                if (now - last) >= idle_timeout:
                    logger.info(
                        "Stopping idle pi process for thread %s (idle %.0fs)",
                        tid,
                        now - last,
                    )
                    expired.append(tid)
                elif (now - started) >= max_lifetime:
                    logger.info(
                        "Stopping pi process for thread %s (lifetime %.0fs)",
                        tid,
                        now - started,
                    )
                    expired.append(tid)
            for tid in expired:
                await self.stop(tid)

    async def _stop_tool_server(self, thread_id: str) -> None:
        server = self._tool_servers.pop(thread_id, None)
        if server is not None:
            await server.stop()

    async def stop(self, thread_id: str) -> None:
        proc = self._processes.pop(thread_id, None)
        self._last_activity.pop(thread_id, None)
        self._start_times.pop(thread_id, None)
        if proc is not None and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except TimeoutError:
                logger.warning(
                    "pi process for thread %s did not terminate; killing", thread_id
                )
                proc.kill()
                await proc.wait()
        await self._stop_tool_server(thread_id)

    async def stop_all(self) -> None:
        self._shutdown = True
        if self._idle_watcher_task is not None and not self._idle_watcher_task.done():
            self._idle_watcher_task.cancel()
            try:
                await self._idle_watcher_task
            except asyncio.CancelledError:
                pass
        for thread_id in list(self._processes.keys()):
            await self.stop(thread_id)
        for thread_id in list(self._tool_servers.keys()):
            await self._stop_tool_server(thread_id)
