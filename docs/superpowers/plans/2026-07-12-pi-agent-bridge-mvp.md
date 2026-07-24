# 金乌后端接入 pi Agent MVP 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 JW Python 后端中新增一个可选的 `agent_engine = "pi"`，用 pi Agent 的 RPC 模式替换原来的 LangGraph/DeepAgents LLM 推理循环；保留现有 API server、memory、skills、调度器、金乌 WebUI 基础设施，并复用现有 SSE 事件流协议。

**Architecture:** Python 侧新增 `JW.pi_bridge` 模块簇（`PiProcessManager` + `PiRPCClient` + `PiEventTranslator` + `PiAgentGraph`）。`PiAgentGraph` 对外暴露 LangGraph 风格的 `astream_events` / `aget_state` / `aupdate_state`，内部通过 stdin/stdout JSONL RPC 驱动 pi 子进程；`stream_agent_events` 识别到 `PiAgentGraph` 后走专用事件翻译路径，把 pi 事件转成前端可消费的 `text/tool_call/tool_result/done/error/usage_stats`。

**Tech Stack:** Python 3.12+ · asyncio subprocess · JSONL · pi v0.80.3 (`--mode rpc`) · DashScope/Qwen (via `~/.pi/agent/models.json`) · pytest.

---

## File Structure

| 文件 | 职责 |
|------|------|
| `jw/jw/config/settings.py` | 新增 `agent_engine`/`pi_provider`/`pi_model`/`pi_session_dir`/`pi_args` 配置字段 |
| `jw/jw/pi_bridge/__init__.py` | 包入口，导出 `PiAgentGraph` 与配置常量 |
| `jw/jw/pi_bridge/process.py` | `PiProcessManager`：pi 子进程生命周期、启动参数构造、崩溃重启 |
| `jw/jw/pi_bridge/rpc.py` | `PiRPCClient`：基于 asyncio 的 JSONL stdin/stdout RPC、命令发送、事件分发 |
| `jw/jw/pi_bridge/translator.py` | `PiEventTranslator`：把 pi 事件翻译为 `StreamEventEmitter` 事件 |
| `jw/jw/pi_bridge/graph.py` | `PiAgentGraph`：LangGraph 兼容封装，`astream_events` / `aget_state` / `aupdate_state` |
| `jw/jw/stream/events.py` | 在 `stream_agent_events` 中识别 `PiAgentGraph` 并路由到 pi 专用流 |
| `jw/jw/cli/agent.py` | `_load_agent` 按 `agent_engine` 返回 `PiAgentGraph` 或原 LangGraph agent |
| `jw/tests/pi_bridge/test_process.py` | `PiProcessManager` 单元测试 |
| `jw/tests/pi_bridge/test_rpc.py` | `PiRPCClient` 单元测试（mock subprocess） |
| `jw/tests/pi_bridge/test_translator.py` | 事件翻译单元测试 |
| `jw/tests/pi_bridge/test_graph.py` | `PiAgentGraph` 行为测试（mock RPC） |
| `jw/tests/test_config.py` | 新增配置字段断言 |

---

## 前置知识

pi 的 RPC 模式协议（已从 `pi-coding-agent/dist/modes/rpc/rpc-mode.js` 与实测确认）：

- 启动：`node <cli.js> --mode rpc --provider <p> --model <m> --session-dir <dir> --session-id <id>`
- 输入：每行一个 JSON 命令，例如 `{"type":"prompt","message":"hi","id":"req_1"}`
- 输出：每行一个 JSON，分两类
  - `response`：`{"type":"response","id":"req_1","command":"prompt","success":true}`
  - event：pi 在推理过程中主动推送，例如
    - `{"type":"agent_start"}`
    - `{"type":"message_update","assistantMessageEvent":{"type":"text_delta","delta":"hello",...}}`
    - `{"type":"message_update","assistantMessageEvent":{"type":"toolcall_end","toolCall":{"type":"toolCall","id":"call_1","name":"read","arguments":{"path":"x"}}}}`
    - `{"type":"tool_execution_start","toolCallId":"call_1","toolName":"read","args":{"path":"x"}}`
    - `{"type":"tool_execution_end","toolCallId":"call_1","toolName":"read","result":{"content":[{"type":"text","text":"..."}]},"isError":false}`
    - `{"type":"agent_end","messages":[...],"willRetry":false}`

本次 MVP 先不拦截 pi 内置的 `read/bash/edit/write`：让 pi 直接操作文件系统，Python 侧只负责翻译事件。自定义 skill/memory/schedule 工具桥接留作后续迭代。

---

## Task 1: 新增 `agent_engine` 与 pi 相关配置字段

**Files:**
- Modify: `jw/jw/config/settings.py`
- Test: `jw/tests/test_config.py`

- [ ] **Step 1: Write the failing test**

在 `jw/tests/test_config.py` 的 `TestJWConfig.test_default_values` 里追加：

```python
assert config.agent_engine == "langgraph"
assert config.pi_provider == ""
assert config.pi_model == ""
assert config.pi_session_dir == ""
assert config.pi_args == ""
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/zhuanz/Desktop/tb2/JW
uv run pytest tests/test_config.py::TestJWConfig::test_default_values -v
```

Expected: FAIL `AttributeError: 'JWConfig' object has no attribute 'agent_engine'`

- [ ] **Step 3: Add config fields**

在 `jw/jw/config/settings.py` 的 `JWConfig` dataclass 中，紧邻 `ui_backend` 添加：

```python
    # Agent engine selection
    agent_engine: Literal["langgraph", "pi"] = "langgraph"

    # pi Agent settings (used when agent_engine == "pi")
    pi_provider: str = ""  # e.g. "dashscope"; empty = fall back to provider
    pi_model: str = ""  # e.g. "qwen-plus"; empty = fall back to model
    pi_session_dir: str = ""  # empty = use <DATA_DIR>/pi-sessions
    pi_args: str = ""  # extra CLI args, space-separated
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_config.py::TestJWConfig::test_default_values -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jw/config/settings.py tests/test_config.py
git commit -m "feat(config): add agent_engine and pi Agent settings"
```

---

## Task 2: 创建 `pi_bridge` 包骨架

**Files:**
- Create: `jw/jw/pi_bridge/__init__.py`

- [ ] **Step 1: Create package init**

```python
"""pi Agent bridge for JW.

Exposes a LangGraph-compatible graph wrapper that drives pi via RPC.
"""

from .graph import PiAgentGraph

__all__ = ["PiAgentGraph"]
```

- [ ] **Step 2: Verify import**

```bash
uv run python -c "from jw.pi_bridge import PiAgentGraph; print(PiAgentGraph)"
```

Expected: prints class object, no error

- [ ] **Step 3: Commit**

```bash
git add jw/pi_bridge/__init__.py
git commit -m "feat(pi_bridge): add package skeleton"
```

---

## Task 3: 实现 `PiProcessManager`

**Files:**
- Create: `jw/jw/pi_bridge/process.py`
- Test: `jw/tests/pi_bridge/test_process.py`

- [ ] **Step 1: Write the failing test**

`jw/tests/pi_bridge/test_process.py`:

```python
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jw.pi_bridge.process import PiProcessManager


class TestPiProcessManager:
    def test_build_command_uses_session_id_and_dir(self, tmp_path):
        cfg = MagicMock()
        cfg.pi_provider = "dashscope"
        cfg.pi_model = "qwen-plus"
        cfg.pi_args = ""
        mgr = PiProcessManager(cfg, pi_cli=Path("/fake/pi.js"), session_dir=tmp_path)
        cmd = mgr._build_command("thread-123")
        assert cmd[0] == "node"
        assert cmd[1] == "/fake/pi.js"
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

    def test_resolve_provider_and_model_fallback(self, tmp_path):
        cfg = MagicMock()
        cfg.provider = "anthropic"
        cfg.model = "claude-sonnet-4-6"
        cfg.pi_provider = ""
        cfg.pi_model = ""
        cfg.pi_args = ""
        mgr = PiProcessManager(cfg, pi_cli=Path("/fake/pi.js"), session_dir=tmp_path)
        provider, model = mgr._resolve_provider_and_model()
        assert provider == "anthropic"
        assert model == "claude-sonnet-4-6"

    @pytest.mark.asyncio
    async def test_start_spawns_process_and_caches(self, tmp_path):
        cfg = MagicMock()
        cfg.pi_provider = "dashscope"
        cfg.pi_model = "qwen-plus"
        cfg.pi_args = ""
        mgr = PiProcessManager(cfg, pi_cli=Path("/fake/pi.js"), session_dir=tmp_path)
        fake_proc = MagicMock()
        fake_proc.returncode = None
        fake_proc.stdin = AsyncMock()
        fake_proc.stdout = MagicMock()

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc) as mock_exec:
            proc = await mgr.start("thread-123")
            assert proc is fake_proc
            assert mgr._processes["thread-123"] is fake_proc
            mock_exec.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_terminates_process(self, tmp_path):
        cfg = MagicMock()
        cfg.pi_provider = "dashscope"
        cfg.pi_model = "qwen-plus"
        cfg.pi_args = ""
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/pi_bridge/test_process.py -v
```

Expected: FAIL `ModuleNotFoundError: No module named 'jw.pi_bridge.process'`

- [ ] **Step 3: Implement `PiProcessManager`**

`jw/jw/pi_bridge/process.py`:

```python
"""Manage pi Agent subprocesses."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class PiProcessManager:
    """Spawn and cache one pi RPC process per thread_id."""

    def __init__(
        self,
        config: Any,
        *,
        pi_cli: Path | None = None,
        session_dir: Path | None = None,
        data_dir: Path | None = None,
    ) -> None:
        self.config = config
        self.pi_cli = pi_cli or self._find_pi_cli()
        self.session_dir = self._resolve_session_dir(session_dir, data_dir)
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    @staticmethod
    def _find_pi_cli() -> Path:
        pi = shutil.which("pi")
        if not pi:
            raise RuntimeError("pi executable not found on PATH")
        return Path(pi).resolve()

    @staticmethod
    def _resolve_session_dir(session_dir: Path | None, data_dir: Path | None) -> Path:
        if session_dir is not None:
            return session_dir
        if data_dir is not None:
            return data_dir / "pi-sessions"
        # Import lazily to avoid circular imports at module load time
        from ..paths import DATA_DIR

        return DATA_DIR / "pi-sessions"

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
        if self.config.pi_args:
            cmd.extend(self.config.pi_args.split())
        return cmd

    def _build_env(self) -> dict[str, str]:
        """Forward API keys from jw config to env vars pi expects."""
        env = dict(os.environ)
        # Map JW API keys to pi env names where they differ
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
        return env

    async def start(self, thread_id: str) -> asyncio.subprocess.Process:
        """Start or reuse a pi RPC process for *thread_id*."""
        if proc := self._processes.get(thread_id):
            if proc.returncode is None:
                return proc
            # Process crashed; will restart below
            logger.warning("pi process for thread %s exited (code=%s); restarting", thread_id, proc.returncode)

        self.session_dir.mkdir(parents=True, exist_ok=True)
        cmd = self._build_command(thread_id)
        env = self._build_env()
        logger.info("Starting pi RPC: %s", " ".join(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        self._processes[thread_id] = proc

        # Brief wait to catch immediate startup failures
        await asyncio.sleep(0.1)
        if proc.returncode is not None:
            stderr = await proc.stderr.read() if proc.stderr else b""
            raise RuntimeError(f"pi exited immediately (code={proc.returncode}): {stderr.decode(errors='replace')}")
        return proc

    async def stop(self, thread_id: str) -> None:
        proc = self._processes.pop(thread_id, None)
        if proc is None or proc.returncode is not None:
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("pi process for thread %s did not terminate; killing", thread_id)
            proc.kill()
            await proc.wait()

    async def stop_all(self) -> None:
        for thread_id in list(self._processes.keys()):
            await self.stop(thread_id)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/pi_bridge/test_process.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jw/pi_bridge/process.py tests/pi_bridge/test_process.py
git commit -m "feat(pi_bridge): add PiProcessManager"
```

---

## Task 4: 实现 `PiRPCClient`

**Files:**
- Create: `jw/jw/pi_bridge/rpc.py`
- Test: `jw/tests/pi_bridge/test_rpc.py`

- [ ] **Step 1: Write the failing test**

`jw/tests/pi_bridge/test_rpc.py`:

```python
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jw.pi_bridge.rpc import PiRPCClient


def _make_mock_process():
    proc = MagicMock()
    proc.returncode = None
    proc.stdin = AsyncMock()
    proc.stdout = MagicMock()
    proc.stderr = MagicMock()
    proc.terminate = MagicMock()
    proc.wait = AsyncMock(return_value=0)
    return proc


class TestPiRPCClient:
    @pytest.mark.asyncio
    async def test_send_prompt_writes_jsonl(self):
        proc = _make_mock_process()
        client = PiRPCClient(proc)
        await client.send_prompt("hello")
        written = proc.stdin.write.await_args[0][0]
        assert written.endswith("\n")
        parsed = json.loads(written)
        assert parsed["type"] == "prompt"
        assert parsed["message"] == "hello"
        assert parsed["id"].startswith("req_")

    @pytest.mark.asyncio
    async def test_event_listener_receives_events(self):
        proc = _make_mock_process()
        client = PiRPCClient(proc)
        events = []
        client.on_event(events.append)
        client._handle_line(json.dumps({"type": "agent_start"}))
        client._handle_line(json.dumps({"type": "text_delta", "delta": "hi"}))
        assert len(events) == 2
        assert events[0]["type"] == "agent_start"

    @pytest.mark.asyncio
    async def test_response_resolves_pending_request(self):
        proc = _make_mock_process()
        client = PiRPCClient(proc)
        task = asyncio.create_task(client.send_command({"type": "get_state"}))
        await asyncio.sleep(0)  # let the coroutine register the request
        # Find the request id from the write call
        written = proc.stdin.write.await_args[0][0]
        req_id = json.loads(written)["id"]
        client._handle_line(json.dumps({"type": "response", "id": req_id, "command": "get_state", "success": True, "data": {"x": 1}}))
        response = await task
        assert response["success"] is True
        assert response["data"]["x"] == 1

    @pytest.mark.asyncio
    async def test_process_exit_rejects_pending(self):
        proc = _make_mock_process()
        client = PiRPCClient(proc)
        task = asyncio.create_task(client.send_command({"type": "get_state"}))
        await asyncio.sleep(0)
        client._on_process_exit(1, None)
        with pytest.raises(RuntimeError):
            await task
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/pi_bridge/test_rpc.py -v
```

Expected: FAIL `ModuleNotFoundError: No module named 'jw.pi_bridge.rpc'`

- [ ] **Step 3: Implement `PiRPCClient`**

`jw/jw/pi_bridge/rpc.py`:

```python
"""JSONL RPC client for a running pi process."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class PiRPCClient:
    """Talk to one pi RPC subprocess over stdin/stdout."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.process = process
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._request_id = 0
        self._read_task: asyncio.Task[Any] | None = None
        self._stderr_task: asyncio.Task[Any] | None = None
        self._closed = False

    def on_event(self, listener: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def remove() -> None:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

        return remove

    def start(self) -> None:
        """Begin reading stdout/stderr. Idempotent."""
        if self._read_task is not None:
            return
        self._read_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())

    async def _read_stdout(self) -> None:
        stdout = self.process.stdout
        if stdout is None:
            return
        try:
            while True:
                line = await stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\n")
                if text:
                    self._handle_line(text)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("pi stdout reader error: %s", exc)
        finally:
            self._on_process_exit(self.process.returncode, self.process.returncode)

    async def _read_stderr(self) -> None:
        stderr = self.process.stderr
        if stderr is None:
            return
        try:
            while True:
                line = await stderr.readline()
                if not line:
                    break
                logger.warning("pi stderr: %s", line.decode("utf-8", errors="replace").rstrip())
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    def _handle_line(self, line: str) -> None:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("pi emitted non-JSON line: %s", line[:200])
            return

        if isinstance(data, dict) and data.get("type") == "response" and data.get("id") in self._pending:
            future = self._pending.pop(data["id"])
            if not future.done():
                future.set_result(data)
            return

        for listener in self._listeners:
            try:
                listener(data)
            except Exception:
                logger.exception("pi event listener failed")

    def _on_process_exit(self, code: int | None, signal: Any) -> None:
        msg = f"pi process exited (code={code})"
        error = RuntimeError(msg)
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    async def send_command(self, command: dict[str, Any], timeout: float = 60.0) -> dict[str, Any]:
        if self.process.returncode is not None:
            raise RuntimeError(f"pi process is not running (code={self.process.returncode})")
        self._request_id += 1
        req_id = f"req_{self._request_id}"
        payload = {**command, "id": req_id}
        line = json.dumps(payload, ensure_ascii=False) + "\n"

        loop = asyncio.get_event_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[req_id] = future

        try:
            self.process.stdin.write(line.encode("utf-8"))
            await self.process.stdin.drain()
        except Exception as exc:
            self._pending.pop(req_id, None)
            raise RuntimeError(f"Failed to write to pi stdin: {exc}") from exc

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise RuntimeError(f"Timeout waiting for pi response to {command.get('type')}")

    async def send_prompt(self, message: str, images: list[str] | None = None) -> dict[str, Any]:
        return await self.send_command({"type": "prompt", "message": message, "images": images or []})

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for task in (self._read_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/pi_bridge/test_rpc.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jw/pi_bridge/rpc.py tests/pi_bridge/test_rpc.py
git commit -m "feat(pi_bridge): add PiRPCClient"
```

---

## Task 5: 实现 `PiEventTranslator`

**Files:**
- Create: `jw/jw/pi_bridge/translator.py`
- Test: `jw/tests/pi_bridge/test_translator.py`

- [ ] **Step 1: Write the failing test**

`jw/tests/pi_bridge/test_translator.py`:

```python
from jw.pi_bridge.translator import PiEventTranslator
from jw.stream.emitter import StreamEventEmitter


class TestPiEventTranslator:
    def test_text_delta_emits_text(self):
        emitter = StreamEventEmitter()
        translator = PiEventTranslator(emitter)
        events = translator.translate({
            "type": "message_update",
            "assistantMessageEvent": {
                "type": "text_delta",
                "delta": "hello",
            },
        })
        assert len(events) == 1
        assert events[0]["type"] == "text"
        assert events[0]["content"] == "hello"

    def test_toolcall_end_emits_tool_call(self):
        emitter = StreamEventEmitter()
        translator = PiEventTranslator(emitter)
        events = translator.translate({
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
        })
        assert len(events) == 1
        assert events[0]["type"] == "tool_call"
        assert events[0]["name"] == "read"
        assert events[0]["args"] == {"path": "/x"}
        assert events[0]["id"] == "call_1"

    def test_tool_execution_end_emits_tool_result(self):
        emitter = StreamEventEmitter()
        translator = PiEventTranslator(emitter)
        events = translator.translate({
            "type": "tool_execution_end",
            "toolCallId": "call_1",
            "toolName": "read",
            "result": {"content": [{"type": "text", "text": "contents"}]},
            "isError": False,
        })
        assert len(events) == 1
        assert events[0]["type"] == "tool_result"
        assert events[0]["name"] == "read"
        assert events[0]["content"] == "contents"
        assert events[0]["success"] is True
        assert events[0]["id"] == "call_1"

    def test_message_end_usage_emits_usage_stats(self):
        emitter = StreamEventEmitter()
        translator = PiEventTranslator(emitter)
        events = translator.translate({
            "type": "message_end",
            "message": {
                "role": "assistant",
                "usage": {"input": 10, "output": 5},
            },
        })
        usage = [e for e in events if e["type"] == "usage_stats"]
        assert len(usage) == 1
        assert usage[0]["input_tokens"] == 10
        assert usage[0]["output_tokens"] == 5

    def test_error_event(self):
        emitter = StreamEventEmitter()
        translator = PiEventTranslator(emitter)
        events = translator.translate({
            "type": "message_end",
            "message": {"role": "assistant", "errorMessage": "boom", "stopReason": "error"},
        })
        errors = [e for e in events if e["type"] == "error"]
        assert len(errors) == 1
        assert errors[0]["message"] == "boom"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/pi_bridge/test_translator.py -v
```

Expected: FAIL `ModuleNotFoundError: No module named 'jw.pi_bridge.translator'`

- [ ] **Step 3: Implement `PiEventTranslator`**

`jw/jw/pi_bridge/translator.py`:

```python
"""Translate pi RPC events into JW stream events."""

from __future__ import annotations

from typing import Any

from ..stream.emitter import StreamEventEmitter


class PiEventTranslator:
    """Convert raw pi events into StreamEventEmitter event dicts."""

    def __init__(self, emitter: StreamEventEmitter | None = None) -> None:
        self.emitter = emitter or StreamEventEmitter()
        self._emitted_tool_calls: set[str] = set()
        self._full_response = ""

    def translate(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        etype = event.get("type")
        if etype == "message_update":
            return self._translate_message_update(event)
        if etype == "tool_execution_end":
            return self._translate_tool_execution_end(event)
        if etype == "message_end":
            return self._translate_message_end(event)
        if etype == "agent_end":
            # Final response is assembled from text deltas; emit done in stream wrapper
            return []
        return []

    def _translate_message_update(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        ame = event.get("assistantMessageEvent") or {}
        ame_type = ame.get("type")
        if ame_type == "text_delta":
            text = ame.get("delta") or ""
            if text:
                self._full_response += text
                return [self.emitter.text(text).data]
            return []
        if ame_type == "toolcall_end":
            tool_call = ame.get("toolCall") or {}
            tc_id = tool_call.get("id") or ""
            name = tool_call.get("name") or ""
            args = tool_call.get("arguments") or {}
            if not tc_id or tc_id in self._emitted_tool_calls:
                return []
            self._emitted_tool_calls.add(tc_id)
            return [self.emitter.tool_call(name, dict(args), str(tc_id)).data]
        return []

    def _translate_tool_execution_end(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        tc_id = event.get("toolCallId") or ""
        name = event.get("toolName") or "unknown"
        result = event.get("result") or {}
        is_error = bool(event.get("isError"))
        content = self._extract_result_text(result)
        success = not is_error
        return [self.emitter.tool_result(name, content, success, str(tc_id)).data]

    def _translate_message_end(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        message = event.get("message") or {}
        usage = message.get("usage") or {}
        inp = int(usage.get("input") or 0)
        out_toks = int(usage.get("output") or 0)
        if inp or out_toks:
            out.append(self.emitter.usage_stats(inp, out_toks).data)

        if message.get("stopReason") == "error" and message.get("errorMessage"):
            out.append(self.emitter.error(str(message["errorMessage"])).data)
        return out

    @staticmethod
    def _extract_result_text(result: Any) -> str:
        if isinstance(result, dict):
            content = result.get("content")
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(str(block.get("text", "")))
                text = "".join(parts)
                if text:
                    return text
            if isinstance(content, str):
                return content
        return str(result)

    @property
    def full_response(self) -> str:
        return self._full_response
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/pi_bridge/test_translator.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jw/pi_bridge/translator.py tests/pi_bridge/test_translator.py
git commit -m "feat(pi_bridge): add PiEventTranslator"
```

---

## Task 6: 实现 `PiAgentGraph`

**Files:**
- Create: `jw/jw/pi_bridge/graph.py`
- Test: `jw/tests/pi_bridge/test_graph.py`

- [ ] **Step 1: Write the failing test**

`jw/tests/pi_bridge/test_graph.py`:

```python
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

            # Patch send_prompt so it doesn't block waiting for a response
            async def _fake_prompt(message, images=None):
                return {"type": "response", "success": True}

            graph._client.send_prompt = _fake_prompt

            events = []
            async for event in graph.astream_events(
                {"messages": [{"role": "user", "content": "hi"}]},
                {"configurable": {"thread_id": "thread-1"}},
                version="v3",
            ):
                events.append(event)
                if event.get("type") == "done":
                    break
                # Inject pi events after the first loop iteration
                if len(events) == 1:
                    graph._client._handle_line('{"type":"agent_start"}')
                    graph._client._handle_line('{"type":"message_update","assistantMessageEvent":{"type":"text_delta","delta":"hello"}}')
                    graph._client._handle_line('{"type":"agent_end","messages":[],"willRetry":false}')

            text_events = [e for e in events if e.get("type") == "text"]
            assert len(text_events) == 1
            assert text_events[0]["content"] == "hello"
            assert any(e.get("type") == "done" for e in events)

    @pytest.mark.asyncio
    async def test_aget_state_returns_dummy_snapshot(self, mock_config, tmp_path):
        graph = PiAgentGraph(mock_config, workspace_dir=str(tmp_path))
        snapshot = await graph.aget_state({"configurable": {"thread_id": "t1"}})
        assert hasattr(snapshot, "values")
        assert snapshot.values is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/pi_bridge/test_graph.py -v
```

Expected: FAIL `ModuleNotFoundError: No module named 'jw.pi_bridge.graph'`

- [ ] **Step 3: Implement `PiAgentGraph`**

`jw/jw/pi_bridge/graph.py`:

```python
"""LangGraph-compatible wrapper around a pi RPC session."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

from ..config import JWConfig
from ..stream.emitter import StreamEventEmitter
from .process import PiProcessManager
from .rpc import PiRPCClient
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
        config: JWConfig,
        *,
        workspace_dir: str,
        process_manager: PiProcessManager | None = None,
    ) -> None:
        self.config = config
        self.workspace_dir = workspace_dir
        self._process_manager = process_manager or PiProcessManager(config)
        self._clients: dict[str, PiRPCClient] = {}

    async def _ensure_process(self, thread_id: str) -> PiRPCClient:
        if client := self._clients.get(thread_id):
            if client.process.returncode is None:
                return client
            # Process died; clean up and restart
            logger.warning("pi client for thread %s has died; restarting", thread_id)
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
        usage_stats, error, agent_end).
        """
        thread_id = self._thread_id_from_config(config)
        message = self._extract_user_message(input)
        metadata = config.get("metadata") or {}
        media = metadata.get("media") or []

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
            await client.send_prompt(message, images=media)
        except Exception as exc:
            unsubscribe()
            yield StreamEventEmitter.error(str(exc)).data
            yield StreamEventEmitter.done().data
            return

        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=300.0)
                except asyncio.TimeoutError:
                    yield StreamEventEmitter.error("Timeout waiting for pi response").data
                    break
                if event.get("type") == "agent_end":
                    yield StreamEventEmitter.done(translator.full_response).data
                    break
                yield event
        finally:
            stopped = True
            unsubscribe()

    async def aget_state(self, config: dict[str, Any]) -> _PiStateSnapshot:
        """LangGraph-compatible state snapshot (minimal)."""
        return _PiStateSnapshot()

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
        for client in list(self._clients.values()):
            await client.close()
        await self._process_manager.stop_all()
        self._clients.clear()

    @staticmethod
    def _thread_id_from_config(config: dict[str, Any]) -> str:
        configurable = config.get("configurable") or {}
        thread_id = configurable.get("thread_id")
        if not thread_id:
            raise ValueError("PiAgentGraph requires config.configurable.thread_id")
        return str(thread_id)

    @staticmethod
    def _extract_user_message(input: dict[str, Any]) -> str:
        messages = input.get("messages") or []
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "user":
                content = msg.get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    texts = [str(block.get("text", "")) for block in content if isinstance(block, dict) and block.get("type") == "text"]
                    return "\n".join(texts)
        raise ValueError("No user message found in input")
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/pi_bridge/test_graph.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jw/pi_bridge/graph.py tests/pi_bridge/test_graph.py
git commit -m "feat(pi_bridge): add PiAgentGraph"
```

---

## Task 7: 在 `stream_agent_events` 中识别 `PiAgentGraph`

**Files:**
- Modify: `jw/jw/stream/events.py`
- Test: `jw/tests/test_stream_events.py`

- [ ] **Step 1: Write the failing test**

在 `jw/tests/test_stream_events.py` 中新增一个测试类（文件已存在；如不存在则创建）：

```python
import asyncio

import pytest

from jw.pi_bridge.graph import PiAgentGraph
from jw.stream.events import stream_agent_events


class TestStreamPiAgentEvents:
    @pytest.mark.asyncio
    async def test_routes_pi_agent_graph(self, tmp_path):
        from unittest.mock import AsyncMock, MagicMock, patch

        cfg = MagicMock()
        cfg.agent_engine = "pi"
        cfg.pi_provider = "dashscope"
        cfg.pi_model = "qwen-plus"
        cfg.pi_session_dir = ""
        cfg.pi_args = ""
        cfg.dashscope_api_key = "fake"

        graph = PiAgentGraph(cfg, workspace_dir=str(tmp_path))
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
            await graph._ensure_process("t1")

            async def _fake_prompt(message, images=None):
                return {"type": "response", "success": True}

            graph._client.send_prompt = _fake_prompt

            events = []
            async for event in stream_agent_events(graph, "hi", thread_id="t1"):
                events.append(event)
                if event.get("type") == "done":
                    break
                if len(events) == 1:
                    graph._client._handle_line('{"type":"agent_start"}')
                    graph._client._handle_line('{"type":"message_update","assistantMessageEvent":{"type":"text_delta","delta":"hi"}}')
                    graph._client._handle_line('{"type":"agent_end","messages":[],"willRetry":false}')

            assert any(e.get("type") == "text" and e.get("content") == "hi" for e in events)
            assert any(e.get("type") == "done" for e in events)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_stream_events.py::TestStreamPiAgentEvents -v
```

Expected: FAIL because `stream_agent_events` currently assumes LangGraph agent.

- [ ] **Step 3: Add pi branch in `stream_agent_events`**

在 `jw/jw/stream/events.py` 中：

1. 文件顶部（与其他 import 一起）添加运行时导入：

```python
try:
    from ..pi_bridge.graph import PiAgentGraph
except Exception:
    PiAgentGraph = None  # type: ignore[misc,assignment]
```

2. 在 `stream_agent_events` 函数开头，在 `emitter = StreamEventEmitter()` 之后、`existing_summarization_event` 之前插入分支：

```python
    # -----------------------------------------------------------------
    # pi Agent bridge path: avoid LangGraph v3 event machinery entirely
    # -----------------------------------------------------------------
    if PiAgentGraph is not None and isinstance(agent, PiAgentGraph):
        try:
            async for event in agent.astream_events(
                await build_agent_stream_input(message, media=media),
                config=config,
                version="v3",
            ):
                yield event
        except Exception as exc:
            yield emitter.error(str(exc)).data
            yield emitter.done().data
        return
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_stream_events.py::TestStreamPiAgentEvents tests/test_stream_events.py::TestStreamEventEmitter -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jw/stream/events.py tests/test_stream_events.py
git commit -m "feat(stream): route PiAgentGraph through dedicated pi event stream"
```

---

## Task 8: 在 `_load_agent` 中按 `agent_engine` 选择图实现

**Files:**
- Modify: `jw/jw/cli/agent.py`
- Test: 新增 `jw/tests/test_cli_agent_loader.py`

- [ ] **Step 1: Write the failing test**

`jw/tests/test_cli_agent_loader.py`:

```python
from unittest.mock import MagicMock, patch

from jw.cli.agent import _load_agent
from jw.pi_bridge.graph import PiAgentGraph


class TestLoadAgentEngine:
    def test_load_agent_returns_pi_graph_when_configured(self, tmp_path):
        cfg = MagicMock()
        cfg.agent_engine = "pi"
        cfg.pi_provider = "dashscope"
        cfg.pi_model = "qwen-plus"
        cfg.pi_session_dir = ""
        cfg.pi_args = ""
        cfg.dashscope_api_key = "fake"

        with patch("jw.cli.agent.create_cli_agent") as mock_langgraph:
            agent = _load_agent(workspace_dir=str(tmp_path), config=cfg)
            assert isinstance(agent, PiAgentGraph)
            mock_langgraph.assert_not_called()

    def test_load_agent_returns_langgraph_by_default(self, tmp_path):
        cfg = MagicMock()
        cfg.agent_engine = "langgraph"

        with patch("jw.cli.agent.create_cli_agent") as mock_langgraph:
            mock_langgraph.return_value = MagicMock()
            agent = _load_agent(workspace_dir=str(tmp_path), config=cfg)
            assert agent is mock_langgraph.return_value
            mock_langgraph.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_cli_agent_loader.py -v
```

Expected: FAIL `AssertionError: create_cli_agent called` (because `_load_agent` doesn't branch yet).

- [ ] **Step 3: Modify `_load_agent`**

在 `jw/jw/cli/agent.py` 的 `_load_agent` 函数中，导入 `PiAgentGraph` 并在函数体开头分支：

```python
from ..agent import create_cli_agent
from ..pi_bridge.graph import PiAgentGraph


def _load_agent(
    workspace_dir: str | None = None,
    checkpointer=None,
    config=None,
    chat_model=None,
    *,
    on_mcp_progress=None,
):
    if config is None:
        from ..config import get_effective_config
        config = get_effective_config()

    if config.agent_engine == "pi":
        return PiAgentGraph(
            config,
            workspace_dir=workspace_dir or str(os.getcwd()),
        )

    # Existing LangGraph/DeepAgents path — keep unchanged
    return create_cli_agent(
        workspace_dir=workspace_dir,
        checkpointer=checkpointer,
        config=config,
        chat_model=chat_model,
        on_mcp_progress=on_mcp_progress,
    )
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_cli_agent_loader.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jw/cli/agent.py tests/test_cli_agent_loader.py
git commit -m "feat(cli): select PiAgentGraph when agent_engine=pi"
```

---

## Task 9: 回归测试与验证

- [ ] **Step 1: 运行 pi bridge 单元测试**

```bash
cd /Users/zhuanz/Desktop/tb2/JW
uv run pytest tests/pi_bridge tests/test_config.py tests/test_cli_agent_loader.py tests/test_stream_events.py::TestStreamPiAgentEvents -v
```

Expected: all PASS

- [ ] **Step 2: 运行相关回归测试**

```bash
uv run pytest tests/test_stream_events.py tests/test_stream_emitter.py tests/test_config.py -v
```

Expected: all PASS

- [ ] **Step 3: 集成验证（需要有效 API key）**

在 `/tmp/pi-integration-check.py` 创建一次性脚本：

```python
import asyncio
from pathlib import Path
from jw.config import get_effective_config
from jw.pi_bridge.graph import PiAgentGraph

async def main():
    cfg = get_effective_config()
    cfg.agent_engine = "pi"
    cfg.pi_provider = "dashscope"
    cfg.pi_model = "qwen-plus"
    graph = PiAgentGraph(cfg, workspace_dir="/tmp/pi-integration")
    async for event in graph.astream_events(
        {"messages": [{"role": "user", "content": "Say hi in one sentence."}]},
        {"configurable": {"thread_id": "integration-test-1"}},
    ):
        print(event)
        if event.get("type") == "done":
            break
    await graph.aclose()

asyncio.run(main())
```

运行：

```bash
cd /Users/zhuanz/Desktop/tb2/JW
DASHSCOPE_API_KEY="sk-ws-H.EDMMDPL.NN0W.MEUCIQD981iJH4U6LQ2aGrB5lI329tAzlJN4-FmJ4Ww4H0KAOgIgYTr3L98TPDVMv2p_HDWhMDRH4j4-b2zq9Z9kdgd-I-8" \
uv run python /tmp/pi-integration-check.py
```

Expected: 输出 `text` 事件和 `done` 事件，内容类似 `Hi there!`

- [ ] **Step 4: Commit final changes / update docs**

如果所有测试通过，向 `docs/superpowers/specs/2026-07-12-pi-agent-bridge-design.md` 的 "实施阶段" 追加一句：

```markdown
- **MVP Bridge 已实现**：见 `docs/superpowers/plans/2026-07-12-pi-agent-bridge-mvp.md`。
```

```bash
git add docs/superpowers/specs/2026-07-12-pi-agent-bridge-design.md
git commit -m "docs: mark pi bridge MVP plan complete"
```

---

## Self-Review

### Spec coverage

| Spec 要求 | 对应任务 |
|-----------|---------|
| 保留 Python 后端基础设施 | 仅替换 `_load_agent` 返回的 agent；gateway、memory、skills、scheduler  untouched |
| pi 通过 `--mode rpc` 被驱动 | Task 3 `PiProcessManager` |
| 向上暴露 LangGraph 兼容接口 | Task 6 `PiAgentGraph.astream_events` / `aget_state` / `aupdate_state` |
| 事件翻译给 WebUI | Task 5 `PiEventTranslator` + Task 7 `stream_agent_events` 分支 |
| `agent_engine` 开关 | Task 1 config + Task 8 `_load_agent` 分支 |
| 配置透传 provider/model/API key | Task 1 config + Task 3 `_build_env` / `_resolve_provider_and_model` |
| 错误处理 | Task 6 超时/异常时 emit `error` + `done`；Task 4 pending request reject on exit |
| 测试策略 | 每个模块都有单元测试；Task 9 集成验证 |

### Placeholder scan

- 无 TBD/TODO/"implement later"。
- 每个代码步骤都包含完整可运行的 Python 代码。
- 每条命令都包含预期输出。

### Type consistency

- `PiProcessManager.start()` 返回 `asyncio.subprocess.Process`。
- `PiRPCClient` 接受一个 `asyncio.subprocess.Process`。
- `PiAgentGraph` 持有 `PiProcessManager` 和 `dict[str, PiRPCClient]`。
- `PiEventTranslator.translate()` 返回 `list[dict[str, Any]]`。
- `agent_engine` 类型为 `Literal["langgraph", "pi"]`。

### 已知限制 / 下一阶段工作

- 未拦截 pi 内置 `read/bash/edit/write`：当前由 pi 直接执行，未映射到 JW 的 `CustomSandboxBackend` 工作区沙箱。后续如需严格工作区隔离，在 `PiRPCClient` 层监听 `tool_execution_start/end` 并插入 Python 后端执行。
- 未实现自定义 skill/memory/schedule 工具：需要编写 pi TypeScript extension 并在 `PiProcessManager` 中通过 `--extension` 加载，extension 与 Python 之间需要额外 IPC（建议用 Unix socket 或 extension UI request）。
- 未实现 HITL / ask_user 中断：pi 的 `extension_ui_request`（confirm/input/select）可映射为 LangGraph interrupt，留作后续任务。
- MVP 不处理前端传入的 media 附件：`build_agent_stream_input` 会把图片转成 base64 data URI，而 pi 的 `images` 参数期望文件路径，两者未做转换。当前 `_extract_user_message` 仅提取文本块。
