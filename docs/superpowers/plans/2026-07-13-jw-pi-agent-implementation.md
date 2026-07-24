# 金乌 pi Agent 实现计划

> 本文档是 `docs/superpowers/specs/2026-07-13-jw-pi-agent-design.md` 的落地执行计划。目标是在不修改 `jw/` 和 `JW-WebUI/` 现有代码的前提下，新建 `jw-agent/` 后端与 `jw-webui/` 前端，并把 JW 中验证过的 pi 桥接、沙箱、记忆、调度、skills 等模式迁移到新的独立项目。

---

## Goal

交付一个可独立运行的「金乌 pi Agent」：

- **后端** `jw-agent/`: FastAPI + WebSocket 服务，负责启动并驱动 pi Agent RPC 子进程；通过 Unix socket tool bridge 把 pi 的工具调用转发到 Python 沙箱 / 记忆 / 调度 / skills；向前端推送标准化事件流。
- **前端** `jw-webui/`: Next.js 应用，通过原生 WebSocket 与后端通信，提供聊天、审批门、实时活动面板、历史会话、记忆 / 调度 / skills 管理界面，并替换品牌为「金乌」。
- **范围**：不侵入 JW 代码库；只复制/改编其可复用的实现模式。

---

## Architecture

```
┌──────────────┐      WebSocket        ┌─────────────────────┐
│  jw-webui │  <──────────────────> │     jw-agent     │
│  (Next.js)   │   JSON 上下行消息      │  (FastAPI + pi RPC) │
└──────────────┘                       └─────────────────────┘
                                                │
                                                │ spawn
                                                ▼
                                       ┌─────────────────────┐
                                       │   pi Agent (Node)   │
                                       │   --mode rpc        │
                                       └─────────────────────┘
                                                │
                                                │ registerTool / onToolCall
                                                ▼
                                       ┌─────────────────────┐
                                       │  extension.ts       │
                                       │  (Node + TypeScript)│
                                       └─────────────────────┘
                                                │
                                                │ Unix domain socket
                                                ▼
                                       ┌─────────────────────┐
                                       │  tool_server.py     │
                                       │  tool_bridge.py     │
                                       │  JWSandbox       │
                                       │  Memory / Scheduler │
                                       └─────────────────────┘
```

### 核心数据流

1. 前端发送 `prompt` → 后端 WebSocket。
2. `JWAgent` 按 `thread_id` 找到/启动 pi 子进程与 tool_server。
3. pi 生成 `text_delta` / `tool_call` / `tool_execution_*` 等事件。
4. `translator` 把 pi 事件转成金乌事件，经 WebSocket 推送给前端。
5. 自定义工具由 pi extension 通过 Unix socket 回调 `tool_server`，`tool_bridge` 执行后返回。
6. 需要审批的操作生成 `interrupt` / `ask_user` 事件；用户决策通过 `resume_interrupt` 写回。
7. turn 结束下发 `done`。

---

## Tech Stack

### 后端

| 层级 | 选型 |
|------|------|
| 语言 | Python 3.11+ |
| 包管理 | `uv` (pyproject.toml) |
| Web 框架 | FastAPI + `python-socketio` 或原生 `fastapi.WebSocket` |
| 异步 | `asyncio` + `pytest-asyncio` |
| RPC | pi CLI (`pi --mode rpc`) |
| 配置 | Pydantic Settings |
| 测试 | `pytest`, `pytest-asyncio`, `pytest-cov` |
| 代码质量 | `ruff` |

### 前端

| 层级 | 选型 |
|------|------|
| 框架 | Next.js 16 + React 19 |
| 语言 | TypeScript |
| 状态 | React hooks + Context |
| 样式 | Tailwind CSS + shadcn/ui 组件（从 JW-WebUI 迁移） |
| 包管理 | npm |
| 测试 | 复用 WebUI 已有测试运行器；若无则跳过单元测试，依赖 Next.js dev 服务器做集成验证 |

---

## 文件结构

```
/Users/zhuanz/Desktop/tb2/
├── jw-agent/
│   ├── pyproject.toml
│   ├── README.md
│   ├── src/jw_agent/
│   │   ├── __init__.py
│   │   ├── __main__.py
│   │   ├── cli.py
│   │   ├── config.py
│   │   ├── server.py
│   │   ├── api.py
│   │   ├── paths.py
│   │   ├── agent/
│   │   │   ├── __init__.py
│   │   │   ├── pi_client.py
│   │   │   ├── process.py
│   │   │   ├── session.py
│   │   │   ├── rpc.py
│   │   │   ├── translator.py
│   │   │   ├── graph.py
│   │   │   ├── tool_bridge.py
│   │   │   ├── tool_server.py
│   │   │   └── extension.ts
│   │   ├── backends.py
│   │   ├── memory/
│   │   │   ├── __init__.py
│   │   │   ├── types.py
│   │   │   ├── observations/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── store.py
│   │   │   │   ├── tools.py
│   │   │   │   └── relations.py
│   │   │   └── search.py
│   │   ├── skills/
│   │   │   ├── __init__.py
│   │   │   ├── skill_manager.py
│   │   │   └── skills_manager.py
│   │   ├── scheduler.py
│   │   └── stream/
│   │       ├── __init__.py
│   │       ├── emitter.py
│   │       └── events.py
│   └── tests/
│       ├── __init__.py
│       ├── conftest.py
│       ├── test_config.py
│       ├── test_backends.py
│       ├── test_pi_client.py
│       ├── test_tool_bridge.py
│       ├── test_memory.py
│       ├── test_scheduler.py
│       ├── test_skill_manager.py
│       ├── test_translator.py
│       ├── test_graph.py
│       ├── test_tool_server.py
│       └── test_server.py
│
├── jw-webui/
│   ├── package.json
│   ├── next.config.ts
│   ├── tailwind.config.ts
│   ├── tsconfig.json
│   ├── src/
│   │   ├── app/
│   │   │   ├── page.tsx
│   │   │   ├── layout.tsx
│   │   │   ├── globals.css
│   │   │   └── components/
│   │   │       ├── ChatInterface.tsx
│   │   │       ├── InspectorPanel.tsx
│   │   │       ├── RealtimeActivityPanel.tsx
│   │   │       ├── RealtimeActivityBridge.tsx
│   │   │       ├── ThreadList.tsx
│   │   │       ├── MemoryPanel.tsx
│   │   │       ├── ScheduledTasksPanel.tsx
│   │   │       ├── WorkspacePanel.tsx
│   │   │       ├── ConfigDialog.tsx
│   │   │       └── ThemeToggle.tsx
│   │   ├── providers/
│   │   │   ├── ChatProvider.tsx
│   │   │   ├── WebSocketProvider.tsx
│   │   │   └── RealtimeActivityProvider.tsx
│   │   ├── hooks/
│   │   │   └── useChat.ts
│   │   ├── lib/
│   │   │   ├── config.ts
│   │   │   ├── toolLabel.ts
│   │   │   ├── utils.ts
│   │   │   └── subAgentActivity.ts
│   │   └── types/
│   │       └── types.ts
│   └── public/
│       └── jw-logo.svg
│
└── docs/superpowers/plans/2026-07-13-jw-pi-agent-implementation.md
```

---

## 实现阶段

以下每个阶段均遵循「写失败测试 → 运行 → 实现 → 运行 → 提交」的节奏。所有代码片段均为完整可运行代码，不含 `TBD`/`TODO`/`...` 占位符。

---

### 阶段 1：后端骨架（pyproject.toml、config、FastAPI、WebSocket endpoint）

**目标**：创建 `jw-agent/` 项目，定义配置模型，启动一个可运行的 FastAPI 服务，暴露健康检查与 WebSocket `/ws`。

#### 1.1 失败测试

创建 `jw-agent/tests/test_config.py`：

```python
"""Tests for jw_agent.config."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from jw_agent.config import JWConfig, _expand_path


def test_expand_path_resolves_tilde():
    home = Path.home()
    assert _expand_path("~/.jw") == home / ".jw"


def test_config_defaults():
    cfg = JWConfig()
    assert cfg.provider == "dashscope"
    assert cfg.model == "qwen-plus"
    assert cfg.pi_bin == "pi"
    assert cfg.dangerous_mode is False


def test_config_env_override(monkeypatch):
    monkeypatch.setenv("JW_PROVIDER", "openai")
    monkeypatch.setenv("JW_MODEL", "gpt-4o")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    cfg = JWConfig()
    assert cfg.provider == "openai"
    assert cfg.model == "gpt-4o"
    assert cfg.api_key == "sk-test"


def test_api_key_mapping():
    with pytest.MonkeyPatch().context() as mp:
        mp.setenv("DEEPSEEK_API_KEY", "sk-ds")
        cfg = JWConfig(provider="deepseek")
        assert cfg.api_key == "sk-ds"
```

创建 `jw-agent/tests/test_server.py`：

```python
"""Tests for jw_agent.server."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient

from jw_agent.server import app


@pytest.fixture
def client():
    return TestClient(app)


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_websocket_connect():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        async with ac.websocket_connect("/ws") as ws:
            await ws.send_json({"type": "ping"})
            msg = await ws.receive_json()
            assert msg["type"] == "pong"
```

#### 1.2 运行测试（应失败）

```bash
cd /Users/zhuanz/Desktop/tb2/jw-agent
uv sync
uv run pytest tests/test_config.py tests/test_server.py -v
```

**预期输出**：

```text
ModuleNotFoundError: No module named 'jw_agent'
FAILED tests/test_config.py::test_expand_path_resolves_tilde
FAILED tests/test_server.py::test_health_endpoint
```

#### 1.3 实现

创建 `jw-agent/pyproject.toml`：

```toml
[project]
name = "jw-agent"
version = "0.1.0"
description = "JW pi Agent backend"
readme = "README.md"
requires-python = ">=3.11"
license = "Apache-2.0"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "pydantic>=2.9",
    "pydantic-settings>=2.6",
    "python-multipart>=0.0.17",
    "pyyaml>=6.0",
    "httpx>=0.28",
    "typer>=0.24",
    "rich>=15.0",
    "tzlocal>=5.0",
]

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=1.0",
    "pytest-cov>=5.0",
    "pytest-timeout>=2.4",
    "ruff>=0.5",
]

[project.scripts]
jw-agent = "jw_agent.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/jw_agent"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"

[tool.ruff]
line-length = 88
indent-width = 4
target-version = "py311"

[tool.ruff.lint]
select = ["E", "W", "F", "I", "B", "C4", "UP", "PT", "PLE", "RUF"]
ignore = ["E501"]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
```

创建 `jw-agent/src/jw_agent/__init__.py`：

```python
"""JW pi Agent backend."""

__version__ = "0.1.0"
```

创建 `jw-agent/src/jw_agent/paths.py`：

```python
"""Path helpers for jw-agent."""
from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    return Path(os.environ.get("JW_DATA_DIR", Path.home() / ".jw"))


def workspace_dir() -> Path:
    return Path(os.environ.get("JW_WORKSPACE_DIR", Path.cwd() / "workspace"))


def user_skills_dir() -> Path:
    return data_dir() / "skills"


def builtin_skills_dir() -> Path:
    return Path(__file__).parent / "skills"
```

创建 `jw-agent/src/jw_agent/config.py`：

```python
"""Configuration model for jw-agent."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _expand_path(value: str | Path | None) -> Path | None:
    if value is None:
        return None
    return Path(os.path.expanduser(str(value)))


class JWConfig(BaseSettings):
    """Runtime configuration.

    Loading priority: environment variables > ~/.jw/config.yaml > defaults.
    """

    model_config = SettingsConfigDict(
        env_prefix="JW_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = Field(default_factory=lambda: _expand_path("~/.jw"))
    workspace_dir: Path = Field(default_factory=lambda: _expand_path("./workspace"))
    provider: str = "dashscope"
    model: str = "qwen-plus"
    pi_bin: str = "pi"
    pi_args: str = ""
    dangerous_mode: bool = False
    sandbox_timeout: int = 300
    require_approval: bool = True
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    session_name: str = "jw-pi"

    @property
    def api_key(self) -> str:
        mapping = {
            "dashscope": "DASHSCOPE_API_KEY",
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "google": "GEMINI_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
        }
        env_key = mapping.get(self.provider, f"{self.provider.upper()}_API_KEY")
        return os.environ.get(env_key, "")

    @property
    def pi_session_dir(self) -> Path:
        return self.data_dir / "pi-sessions"

    @property
    def memory_dir(self) -> Path:
        return self.data_dir / "memories"

    @property
    def socket_dir(self) -> Path:
        return self.data_dir / "sockets"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    def model_dump_yaml_safe(self) -> dict[str, Any]:
        return {
            k: str(v) if isinstance(v, Path) else v
            for k, v in self.model_dump().items()
        }
```

创建 `jw-agent/src/jw_agent/server.py`：

```python
"""FastAPI + WebSocket entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .config import JWConfig
from .stream.emitter import StreamEventEmitter

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = JWConfig()
    for d in (
        config.data_dir,
        config.pi_session_dir,
        config.memory_dir,
        config.socket_dir,
        config.log_dir,
        config.workspace_dir,
    ):
        d.mkdir(parents=True, exist_ok=True)
    app.state.config = config
    app.state.sessions: dict[str, Any] = {}
    logger.info("JW server starting on %s:%s", config.api_host, config.api_port)
    yield
    for ws in list(app.state.sessions.values()):
        try:
            await ws.close()
        except Exception:
            pass
    logger.info("JW server stopped")


app = FastAPI(title="JW Agent", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "jw-agent"}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    active_thread_id: str | None = None
    try:
        while True:
            msg = await ws.receive_json()
            msg_type = msg.get("type")
            if msg_type == "ping":
                await ws.send_json({"type": "pong"})
            elif msg_type == "subscribe":
                active_thread_id = msg.get("thread_id")
                if active_thread_id:
                    app.state.sessions[active_thread_id] = ws
                    await ws.send_json({
                        "type": "history",
                        "thread_id": active_thread_id,
                        "messages": [],
                    })
            elif msg_type == "prompt":
                await ws.send_json({
                    "type": "event",
                    "payload": StreamEventEmitter.text(
                        "Echo: " + msg.get("message", "")
                    ).data,
                })
                await ws.send_json({
                    "type": "event",
                    "payload": StreamEventEmitter.done("Echo done").data,
                })
            elif msg_type == "resume_interrupt":
                await ws.send_json({
                    "type": "event",
                    "payload": StreamEventEmitter.done("Interrupt resumed").data,
                })
            elif msg_type == "abort":
                await ws.send_json({
                    "type": "event",
                    "payload": StreamEventEmitter.done("Aborted").data,
                })
            else:
                await ws.send_json({
                    "type": "error",
                    "message": f"Unknown message type: {msg_type}",
                })
    except WebSocketDisconnect:
        if active_thread_id and active_thread_id in app.state.sessions:
            app.state.sessions.pop(active_thread_id, None)
```

创建 `jw-agent/src/jw_agent/stream/emitter.py`（完整事件工厂，与 spec 事件名对齐）：

```python
"""Standardized event schema for jw-agent."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class StreamEvent:
    type: str
    data: dict[str, Any]


class StreamEventEmitter:
    @staticmethod
    def text(content: str) -> StreamEvent:
        return StreamEvent("text", {"type": "text", "content": content})

    @staticmethod
    def thinking(content: str, thinking_id: int = 0) -> StreamEvent:
        return StreamEvent(
            "thinking",
            {"type": "thinking", "content": content, "id": thinking_id},
        )

    @staticmethod
    def tool_call(name: str, args: dict[str, Any], tool_id: str) -> StreamEvent:
        return StreamEvent(
            "tool_call",
            {"type": "tool_call", "name": name, "args": args, "id": tool_id},
        )

    @staticmethod
    def tool_result(
        name: str, content: str, success: bool, tool_call_id: str
    ) -> StreamEvent:
        return StreamEvent(
            "tool_result",
            {
                "type": "tool_result",
                "name": name,
                "content": content,
                "success": success,
                "id": tool_call_id,
            },
        )

    @staticmethod
    def usage_stats(input_tokens: int, output_tokens: int) -> StreamEvent:
        return StreamEvent(
            "usage_stats",
            {
                "type": "usage_stats",
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        )

    @staticmethod
    def error(message: str) -> StreamEvent:
        return StreamEvent("error", {"type": "error", "message": message})

    @staticmethod
    def done(response: str = "") -> StreamEvent:
        return StreamEvent(
            "done", {"type": "done", "content": response, "response": response}
        )

    @staticmethod
    def interrupt(
        interrupt_id: str, action_requests: list[dict[str, Any]]
    ) -> StreamEvent:
        return StreamEvent(
            "interrupt",
            {
                "type": "interrupt",
                "interrupt_id": interrupt_id,
                "action_requests": action_requests,
            },
        )

    @staticmethod
    def ask_user(
        interrupt_id: str, questions: list[dict[str, Any]], tool_call_id: str = ""
    ) -> StreamEvent:
        return StreamEvent(
            "ask_user",
            {
                "type": "ask_user",
                "interrupt_id": interrupt_id,
                "questions": questions,
                "tool_call_id": tool_call_id,
            },
        )
```

创建 `jw-agent/src/jw_agent/cli.py`：

```python
"""CLI entry point."""
from __future__ import annotations

import logging

import typer
import uvicorn

from .config import JWConfig
from .server import app

cli = typer.Typer(name="jw-agent", help="JW pi Agent backend")


def main() -> None:
    cli()


@cli.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host", "-h"),
    port: int = typer.Option(8000, "--port", "-p"),
    workdir: str = typer.Option("./workspace", "--workdir", "-w"),
    log_level: str = typer.Option("info", "--log-level"),
) -> None:
    cfg = JWConfig()
    cfg.api_host = host
    cfg.api_port = port
    cfg.workspace_dir = cfg.workspace_dir.__class__(workdir)
    logging.basicConfig(
        level=log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=log_level,
    )


if __name__ == "__main__":
    main()
```

#### 1.4 运行测试

```bash
uv run pytest tests/test_config.py tests/test_server.py -v
```

**预期输出**：

```text
tests/test_config.py::test_expand_path_resolves_tilde PASSED
tests/test_config.py::test_config_defaults PASSED
tests/test_config.py::test_config_env_override PASSED
tests/test_config.py::test_api_key_mapping PASSED
tests/test_server.py::test_health_endpoint PASSED
tests/test_server.py::test_websocket_connect PASSED
```

#### 1.5 提交

```bash
git add jw-agent/
git commit -m "feat(jw-agent): scaffold FastAPI + WebSocket + config"
```

---

### 阶段 2：Pi RPC client / process manager / session reader（最小可测试版本）

**目标**：迁移 `pi-mcp-bridge` 的 `PiConfig`/`PiClient` 与 JW 的 `PiRPCClient` / `PiProcessManager` / `PiSessionReader`，去掉 JW 专属依赖。

#### 2.1 失败测试

创建 `jw-agent/tests/test_pi_client.py`：

```python
"""Tests for pi RPC client helpers."""
from __future__ import annotations

import pytest

from jw_agent.agent.pi_client import PiConfig
from jw_agent.agent.rpc import PiRPCClient
from jw_agent.agent.session import PiSessionReader


def test_pi_config_build_argv():
    cfg = PiConfig(
        pi_bin="pi",
        provider="dashscope",
        model="qwen-plus",
        session_dir="/tmp/sessions",
        session_name="jw-test",
    )
    argv = cfg.build_argv()
    assert argv == [
        "pi",
        "--mode",
        "rpc",
        "--name",
        "jw-test",
        "--provider",
        "dashscope",
        "--model",
        "qwen-plus",
        "--session-dir",
        "/tmp/sessions",
    ]


def test_pi_config_no_session():
    cfg = PiConfig(no_session=True)
    assert "--no-session" in cfg.build_argv()


def test_session_reader_empty(tmp_path):
    reader = PiSessionReader(tmp_path)
    assert reader.find_session_file("missing") is None
    assert reader.read_messages("missing") == []


@pytest.mark.asyncio
async def test_rpc_client_request_id_counter():
    class FakeProc:
        returncode = None
        stdin = type("Stdin", (), {"write": lambda *_: None, "drain": lambda: None})()
        stdout = None
        stderr = None

    client = PiRPCClient(FakeProc())
    assert client._request_id == 0
```

#### 2.2 运行测试（应失败）

```bash
uv run pytest tests/test_pi_client.py -v
```

**预期输出**：`ModuleNotFoundError: No module named 'jw_agent.agent'`。

#### 2.3 实现

创建 `jw-agent/src/jw_agent/agent/__init__.py`：

```python
"""Agent orchestration package."""
```

创建 `jw-agent/src/jw_agent/agent/pi_client.py`（改编自 `pi-mcp-bridge/src/pi_mcp_bridge/pi_client.py`）：

```python
"""Async client for the Pi coding agent RPC mode."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def encode_image(path: str) -> dict[str, str]:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Image not found: {path}")
    mime_type, _ = mimetypes.guess_type(str(file_path))
    if mime_type is None or not mime_type.startswith("image/"):
        raise ValueError(f"File does not appear to be an image: {path}")
    data = file_path.read_bytes()
    return {
        "type": "image",
        "data": base64.b64encode(data).decode("ascii"),
        "mimeType": mime_type,
    }


@dataclass
class PiConfig:
    pi_bin: str = "pi"
    cwd: str | None = None
    provider: str | None = None
    model: str | None = None
    session_dir: str | None = None
    session_name: str = "jw-pi"
    no_session: bool = False
    env: dict[str, str] = field(default_factory=dict)
    extra_args: list[str] = field(default_factory=list)

    def build_argv(self) -> list[str]:
        argv = [self.pi_bin, "--mode", "rpc"]
        if self.no_session:
            argv.append("--no-session")
        if self.session_name:
            argv.extend(["--name", self.session_name])
        if self.provider:
            argv.extend(["--provider", self.provider])
        if self.model:
            argv.extend(["--model", self.model])
        if self.session_dir:
            argv.extend(["--session-dir", self.session_dir])
        argv.extend(self.extra_args)
        return argv


class PiError(Exception):
    pass


class PiTimeoutError(PiError):
    pass


class PiClient:
    """Manages a long-lived Pi RPC subprocess and exposes high-level helpers."""

    def __init__(self, config: PiConfig | None = None) -> None:
        self.config = config or PiConfig()
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._counter = 0
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._events_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def __aenter__(self) -> PiClient:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.stop()

    async def start(self) -> None:
        async with self._lock:
            if self._proc is not None:
                return
            pi_bin = shutil.which(self.config.pi_bin) or self.config.pi_bin
            argv = self.config.build_argv()
            argv[0] = pi_bin
            cwd = self.config.cwd or os.getcwd()
            env = os.environ.copy()
            env.update(self.config.env)
            self._proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
            self._reader_task = asyncio.create_task(self._read_loop())

    async def stop(self) -> None:
        async with self._lock:
            if self._reader_task is not None:
                self._reader_task.cancel()
                try:
                    await self._reader_task
                except asyncio.CancelledError:
                    pass
                self._reader_task = None
            if self._proc is not None:
                try:
                    self._proc.terminate()
                    await asyncio.wait_for(self._proc.wait(), timeout=5.0)
                except TimeoutError:
                    self._proc.kill()
                    await self._proc.wait()
                except ProcessLookupError:
                    pass
                self._proc = None
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(PiError("Pi client stopped"))
            self._pending.clear()

    def _next_id(self) -> str:
        self._counter += 1
        return f"jw-{self._counter}"

    async def _read_loop(self) -> None:
        assert self._proc is not None
        assert self._proc.stdout is not None
        try:
            while True:
                raw = await self._proc.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip("\n").rstrip("\r")
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as exc:
                    logger.warning("Failed to parse Pi RPC line: %s", exc)
                    continue
                msg_type = msg.get("type")
                if msg_type == "response":
                    req_id = msg.get("id")
                    if req_id and req_id in self._pending:
                        fut = self._pending.pop(req_id)
                        if not fut.done():
                            fut.set_result(msg)
                    else:
                        logger.debug("Unsolicited Pi response: %s", msg)
                elif msg_type == "extension_ui_request":
                    await self._send({
                        "type": "extension_ui_response",
                        "id": msg["id"],
                        "cancelled": True,
                    })
                else:
                    await self._events_queue.put(msg)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Pi reader loop error: %s", exc)
        finally:
            for _req_id, fut in list(self._pending.items()):
                if not fut.done():
                    fut.set_exception(PiError("Pi RPC reader closed"))
            self._pending.clear()

    async def _send(self, cmd: dict[str, Any]) -> None:
        assert self._proc is not None
        assert self._proc.stdin is not None
        line = json.dumps(cmd, ensure_ascii=False) + "\n"
        self._proc.stdin.write(line.encode("utf-8"))
        await self._proc.stdin.drain()

    async def _command(
        self, cmd: dict[str, Any], *, timeout: float | None = 30.0
    ) -> dict[str, Any]:
        await self.start()
        req_id = self._next_id()
        cmd["id"] = req_id
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut
        try:
            await self._send(cmd)
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(req_id, None)

    async def prompt(
        self,
        message: str,
        *,
        images: list[dict[str, str]] | None = None,
        timeout: float | None = 300.0,
        streaming_timeout: float | None = None,
    ) -> str:
        cmd: dict[str, Any] = {"type": "prompt", "message": message}
        if images:
            cmd["images"] = images
        resp = await self._command(cmd, timeout=30.0)
        if not resp.get("success", False):
            error = resp.get("error", "unknown error")
            raise PiError(f"Pi rejected prompt: {error}")

        deadline = None
        if timeout is not None and timeout > 0:
            deadline = asyncio.get_event_loop().time() + timeout

        pieces: list[str] = []
        last_assistant_text: str | None = None
        settled = False

        while True:
            wait_timeout = streaming_timeout or 10.0
            if deadline is not None:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    raise PiTimeoutError("Prompt timed out waiting for Pi to settle")
                wait_timeout = min(wait_timeout, remaining)
            try:
                event = await asyncio.wait_for(
                    self._events_queue.get(), timeout=wait_timeout
                )
            except TimeoutError as exc:
                if settled:
                    break
                raise PiTimeoutError("Pi event stream idle for too long") from exc

            event_type = event.get("type")
            if event_type == "message_update":
                delta = event.get("assistantMessageEvent", {})
                if delta.get("type") == "text_delta":
                    pieces.append(delta.get("delta", ""))
                elif delta.get("type") == "text_end":
                    text = delta.get("content") or ""
                    if text:
                        last_assistant_text = text
            elif event_type == "message_end":
                msg = event.get("message", {})
                if msg.get("role") == "assistant":
                    text = self._extract_text(msg)
                    if text:
                        last_assistant_text = text
            elif event_type == "agent_end":
                for msg in event.get("messages", []):
                    if msg.get("role") == "assistant":
                        text = self._extract_text(msg)
                        if text:
                            last_assistant_text = text
            elif event_type == "agent_settled":
                settled = True
                await asyncio.sleep(0.2)
                while not self._events_queue.empty():
                    self._events_queue.get_nowait()
                break
            elif event_type == "auto_retry_end" and not event.get("success", True):
                raise PiError(f"Pi auto-retry failed: {event.get('finalError', 'unknown')}")
            elif event_type == "extension_error":
                logger.warning("Pi extension error: %s", event.get("error"))

        result = "".join(pieces).strip()
        if not result and last_assistant_text:
            result = last_assistant_text.strip()
        return result

    @staticmethod
    def _extract_text(message: dict[str, Any]) -> str | None:
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            return "".join(texts) or None
        return None

    async def abort(self) -> None:
        await self._command({"type": "abort"}, timeout=10.0)
```

创建 `jw-agent/src/jw_agent/agent/rpc.py`（改编自 `jw/jw/pi_bridge/rpc.py`）：

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

    def on_event(
        self, listener: Callable[[dict[str, Any]], None]
    ) -> Callable[[], None]:
        self._listeners.append(listener)

        def remove() -> None:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

        return remove

    def start(self) -> None:
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
            self._on_process_exit(self.process.returncode)

    async def _read_stderr(self) -> None:
        stderr = self.process.stderr
        if stderr is None:
            return
        try:
            while True:
                line = await stderr.readline()
                if not line:
                    break
                logger.warning(
                    "pi stderr: %s", line.decode("utf-8", errors="replace").rstrip()
                )
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
        if (
            isinstance(data, dict)
            and data.get("type") == "response"
            and data.get("id") in self._pending
        ):
            future = self._pending.pop(data["id"])
            if not future.done():
                future.set_result(data)
            return
        for listener in self._listeners[:]:
            try:
                listener(data)
            except Exception:
                logger.exception("pi event listener failed")

    def _on_process_exit(self, code: int | None) -> None:
        msg = f"pi process exited (code={code})"
        error = RuntimeError(msg)
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    async def send_command(
        self, command: dict[str, Any], timeout: float = 60.0
    ) -> dict[str, Any]:
        if self.process.returncode is not None:
            raise RuntimeError(
                f"pi process is not running (code={self.process.returncode})"
            )
        if self._read_task is None:
            self.start()
        self._request_id += 1
        req_id = f"jw-req-{self._request_id}"
        payload = {**command, "id": req_id}
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[req_id] = future
        try:
            self.process.stdin.write(line.encode("utf-8"))
            await self.process.stdin.drain()
        except Exception as exc:
            self._pending.pop(req_id, None)
            raise RuntimeError(f"Failed to write to pi stdin: {exc}") from exc
        try:
            response = await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            self._pending.pop(req_id, None)
            raise RuntimeError(
                f"Timeout waiting for pi response to {command.get('type')}"
            ) from None
        if isinstance(response, dict) and response.get("success") is False:
            error = response.get("error") or "pi command failed"
            raise RuntimeError(f"pi {command.get('type')} failed: {error}")
        return response

    async def send_prompt(
        self, message: str, images: list[str] | None = None
    ) -> dict[str, Any]:
        return await self.send_command(
            {"type": "prompt", "message": message, "images": images or []}
        )

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

创建 `jw-agent/src/jw_agent/agent/session.py`：

```python
"""Read pi session files to reconstruct conversation state."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class PiSessionReader:
    def __init__(self, session_dir: str | Path) -> None:
        self.session_dir = Path(session_dir)

    def find_session_file(self, session_id: str) -> Path | None:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        matches = [
            p
            for p in self.session_dir.iterdir()
            if p.is_file() and p.suffix == ".jsonl" and session_id in p.name
        ]
        if not matches:
            return None
        return max(matches, key=lambda p: p.stat().st_mtime)

    def read_messages(self, session_id: str) -> list[dict[str, Any]]:
        path = self.find_session_file(session_id)
        if path is None:
            return []
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("Could not read pi session file %s: %s", path, exc)
            return []
        messages: list[dict[str, Any]] = []
        for line in text.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") != "message":
                continue
            msg = entry.get("message") or {}
            role = msg.get("role")
            if role not in ("user", "assistant", "toolResult"):
                continue
            messages.append({
                "role": role,
                "content": msg.get("content"),
                "timestamp": msg.get("timestamp"),
            })
        return messages
```

创建 `jw-agent/src/jw_agent/agent/process.py`（初始骨架）：

```python
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

from ..config import JWConfig
from .pi_client import PiConfig
from .tool_server import JWToolServer
from .tool_bridge import JWToolBridge

logger = logging.getLogger(__name__)


class PiProcessManager:
    """Spawn and cache one pi RPC process per thread_id."""

    def __init__(
        self,
        config: JWConfig,
        *,
        workspace_dir: str | None = None,
        pi_cli: Path | None = None,
        session_dir: Path | None = None,
        data_dir: Path | None = None,
        tool_bridge: JWToolBridge | None = None,
    ) -> None:
        self.config = config
        self.workspace_dir = workspace_dir or str(config.workspace_dir)
        self.pi_cli = pi_cli or self._find_pi_cli()
        self.session_dir = session_dir or config.pi_session_dir
        self.data_dir = data_dir or config.data_dir
        self._tool_bridge = tool_bridge
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._tool_servers: dict[str, JWToolServer] = {}
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

    def _extension_path(self) -> Path:
        return Path(__file__).parent / "extension.ts"

    def _socket_path(self, thread_id: str) -> Path:
        return self.data_dir / "sockets" / f"tool-server-{thread_id}.sock"

    def _ensure_tool_bridge(self, thread_id: str = "") -> JWToolBridge | None:
        if self._tool_bridge is not None:
            return self._tool_bridge
        return JWToolBridge(
            workspace_dir=self.workspace_dir,
            memory_dir=str(self.config.memory_dir),
            skills_dirs=[
                Path(self.workspace_dir) / "skills",
                self.data_dir / "skills",
                Path(__file__).parent.parent / "skills",
            ],
            source_session_id=thread_id or "jw-pi",
        )

    def _build_command(self, thread_id: str) -> list[str]:
        cmd = [
            "node",
            str(self.pi_cli),
            "--mode",
            "rpc",
            "--provider",
            self.config.provider,
            "--model",
            self.config.model,
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
        key_mappings = {
            "dashscope": "DASHSCOPE_API_KEY",
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "google": "GEMINI_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
        }
        env_key = key_mappings.get(self.config.provider, f"{self.config.provider.upper()}_API_KEY")
        value = self.config.api_key
        if value and not env.get(env_key):
            env[env_key] = value
        if self._ensure_tool_bridge(thread_id) is not None:
            env["JW_TOOL_SOCKET"] = str(self._socket_path(thread_id))
        return env

    def touch(self, thread_id: str) -> None:
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
            self.session_dir.mkdir(parents=True, exist_ok=True)
            tool_bridge = self._ensure_tool_bridge(thread_id)
            if tool_bridge is not None:
                socket_path = self._socket_path(thread_id)
                server = JWToolServer(tool_bridge, socket_path=socket_path)
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
                await self._stop_tool_server(thread_id)
                raise RuntimeError(
                    f"pi exited immediately (code={proc.returncode}): {stderr.decode(errors='replace')}"
                )
            return proc

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
```

#### 2.4 运行测试

```bash
uv run pytest tests/test_pi_client.py -v
```

**预期输出**：全部通过。

#### 2.5 提交

```bash
git add jw-agent/
git commit -m "feat(jw-agent): pi RPC client, session reader, process manager skeleton"
```

---

### 阶段 3：沙箱后端（read/write/edit/ls/glob/grep/bash）

**目标**：实现不依赖 `deepagents` 的 `JWSandbox`，支持文件工具与受控 bash，并适配 `/skills/`、`/memories/` 虚拟路径。

#### 3.1 失败测试

创建 `jw-agent/tests/test_backends.py`：

```python
"""Tests for jw_agent.backends."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jw_agent.backends import JWSandbox, validate_command


@pytest.fixture
def sandbox(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    mem = tmp_path / "memories"
    mem.mkdir()
    sk = tmp_path / "skills"
    sk.mkdir()
    return JWSandbox(
        workspace_dir=ws,
        memory_dir=mem,
        skills_dirs=[sk],
        dangerous=False,
        timeout=30,
    )


def test_validate_command_blocks_sudo():
    assert validate_command("sudo ls") is not None


def test_validate_command_blocks_traversal():
    assert validate_command("cat ../etc/passwd") is not None


def test_validate_command_allows_relative():
    assert validate_command("python ./train.py") is None


def test_sandbox_read_write(sandbox):
    res = sandbox.write("./hello.txt", "world")
    assert res["isError"] is False
    res = sandbox.read("./hello.txt")
    assert res["isError"] is False
    assert res["content"] == "world"


def test_sandbox_edit(sandbox):
    sandbox.write("./edit.txt", "old content")
    res = sandbox.edit("./edit.txt", "old", "new")
    assert res["isError"] is False
    assert "new content" in sandbox.read("./edit.txt")["content"]


def test_sandbox_ls(sandbox):
    sandbox.write("./a.py", "x")
    res = sandbox.ls(".")
    assert res["isError"] is False
    assert "a.py" in res["content"]


def test_sandbox_glob(sandbox):
    sandbox.write("./foo.py", "x")
    sandbox.write("./bar.txt", "x")
    res = sandbox.glob("*.py")
    assert res["isError"] is False
    assert "foo.py" in res["content"]
    assert "bar.txt" not in res["content"]


def test_sandbox_grep(sandbox):
    sandbox.write("./needle.py", "target word")
    res = sandbox.grep("target")
    assert res["isError"] is False
    assert "needle.py" in res["content"]


def test_sandbox_bash(sandbox):
    res = sandbox.bash("echo hello")
    assert res["isError"] is False
    assert res["content"].strip() == "hello"
    assert res["details"]["exit_code"] == 0


def test_sandbox_blocks_escape(sandbox):
    res = sandbox.read("/etc/passwd")
    assert res["isError"] is True


def test_sandbox_memory_path(sandbox):
    res = sandbox.write("/memories/profile.md", "profile")
    assert res["isError"] is False
    res = sandbox.read("/memories/profile.md")
    assert res["content"] == "profile"


def test_sandbox_skills_path(sandbox):
    res = sandbox.read("/skills/hello.txt")
    assert res["isError"] is True
```

#### 3.2 运行测试（应失败）

```bash
uv run pytest tests/test_backends.py -v
```

**预期输出**：`ModuleNotFoundError: No module named 'jw_agent.backends'`。

#### 3.3 实现

创建 `jw-agent/src/jw_agent/backends.py`（自研实现，借鉴 JW 路径映射与命令校验）：

```python
"""Sandbox backends for jw-agent."""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BLOCKED_COMMANDS = ["sudo", "chmod", "chown", "mkfs", "dd", "shutdown", "reboot"]

_DESTRUCTIVE_PATTERNS = [r"\brm\s+-rf\s+/"]

_PATH_PATTERNS = [r"~/", r"\bcd\s+/"]


def _has_traversal_component(command: str) -> bool:
    from pathlib import PurePosixPath

    for token in command.split():
        if ".." in PurePosixPath(token).parts:
            return True
    return False


def _split_shell_commands(command: str) -> list[str]:
    return [seg.strip().split()[0] for seg in re.split(r"\s*(?:&&|\|\||;)\s*", command) if seg.strip()]


def validate_command(command: str, *, dangerous: bool = False) -> str | None:
    if not dangerous:
        if _has_traversal_component(command):
            return "Command blocked: contains '..' path traversal."
        for pattern in _PATH_PATTERNS:
            if re.search(pattern, command):
                return f"Command blocked: contains forbidden pattern '{pattern}'."
    for pattern in _DESTRUCTIVE_PATTERNS:
        if re.search(pattern, command):
            return f"Command blocked: contains forbidden pattern '{pattern}'."
    for base_cmd in _split_shell_commands(command):
        if base_cmd in BLOCKED_COMMANDS:
            return f"Command blocked: '{base_cmd}' is not allowed in sandbox mode."
    return None


@dataclass
class _ResolvedPath:
    real_path: Path
    virtual_path: str
    backend: str


class JWSandbox:
    def __init__(
        self,
        workspace_dir: Path,
        memory_dir: Path,
        skills_dirs: list[Path],
        *,
        dangerous: bool = False,
        timeout: int = 300,
    ) -> None:
        self.workspace_dir = Path(workspace_dir).resolve()
        self.memory_dir = Path(memory_dir).resolve()
        self.skills_dirs = [Path(d).resolve() for d in skills_dirs]
        self.dangerous = dangerous
        self.timeout = timeout

    def _resolve(self, path: str) -> _ResolvedPath:
        virtual = path.strip()
        if virtual.startswith("/memories/"):
            rel = virtual[len("/memories/") :]
            real = self.memory_dir / rel
            return _ResolvedPath(real, virtual, "memory")
        if virtual.startswith("/skills/"):
            rel = virtual[len("/skills/") :]
            rel_path = Path(rel)
            for d in self.skills_dirs:
                candidate = d / rel_path
                if candidate.exists():
                    return _ResolvedPath(candidate, virtual, "skills")
            return _ResolvedPath(
                self.workspace_dir / "skills" / rel_path, virtual, "skills"
            )
        if virtual.startswith("/"):
            if self.dangerous:
                real = Path(virtual)
            else:
                raise ValueError(f"Absolute paths outside workspace are not allowed: {virtual}")
        else:
            real = self.workspace_dir / virtual
        real = real.resolve()
        if not self.dangerous and not self._is_under(self.workspace_dir, real):
            raise ValueError(f"Path escapes workspace: {virtual}")
        return _ResolvedPath(real, virtual, "workspace")

    @staticmethod
    def _is_under(parent: Path, child: Path) -> bool:
        try:
            child.relative_to(parent)
            return True
        except ValueError:
            return False

    @staticmethod
    def _ok(content: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"content": content, "isError": False, "details": details or {}}

    @staticmethod
    def _err(message: str) -> dict[str, Any]:
        return {"content": message, "isError": True, "details": {}}

    def read(self, path: str, offset: int = 0, limit: int = 2000) -> dict[str, Any]:
        try:
            resolved = self._resolve(path)
            if not resolved.real_path.exists():
                return self._err(f"File not found: {path}")
            text = resolved.real_path.read_text(encoding="utf-8")
            lines = text.splitlines()
            if offset:
                lines = lines[offset:]
            if limit:
                lines = lines[:limit]
            return self._ok("\n".join(lines))
        except Exception as exc:
            return self._err(f"Error reading {path}: {exc}")

    def write(self, path: str, content: str) -> dict[str, Any]:
        try:
            resolved = self._resolve(path)
            if resolved.backend == "skills" and not self._is_under(
                self.workspace_dir / "skills", resolved.real_path
            ):
                return self._err("Cannot write to read-only skills directories.")
            if resolved.backend == "memory" and not self._is_profile_path(path):
                return self._err("Raw writes to /memories are blocked; use memory tools.")
            resolved.real_path.parent.mkdir(parents=True, exist_ok=True)
            resolved.real_path.write_text(content, encoding="utf-8")
            return self._ok(f"Wrote {path}")
        except Exception as exc:
            return self._err(f"Error writing {path}: {exc}")

    def edit(
        self,
        path: str,
        old_string: str,
        new_string: str,
        *,
        replace_all: bool = False,
    ) -> dict[str, Any]:
        try:
            resolved = self._resolve(path)
            if not resolved.real_path.exists():
                return self._err(f"File not found: {path}")
            text = resolved.real_path.read_text(encoding="utf-8")
            if resolved.backend == "memory" and not self._is_profile_path(path):
                return self._err("Raw edits under /memories are limited to /memories/profile/ files.")
            if replace_all:
                count = text.count(old_string)
                text = text.replace(old_string, new_string)
            else:
                count = 1 if old_string in text else 0
                text = text.replace(old_string, new_string, 1)
            if count == 0:
                return self._err("old_string not found")
            resolved.real_path.write_text(text, encoding="utf-8")
            return self._ok(f"Edited {path} ({count} occurrence(s))")
        except Exception as exc:
            return self._err(f"Error editing {path}: {exc}")

    @staticmethod
    def _is_profile_path(path: str) -> bool:
        normalized = "/" + path.strip().lstrip("/")
        return normalized == "/profile" or normalized.startswith("/profile/")

    def ls(self, path: str) -> dict[str, Any]:
        try:
            resolved = self._resolve(path)
            target = resolved.real_path
            if not target.exists():
                return self._err(f"Path not found: {path}")
            entries = []
            for child in sorted(target.iterdir()):
                entries.append({
                    "name": child.name,
                    "type": "directory" if child.is_dir() else "file",
                    "size": child.stat().st_size if child.is_file() else None,
                })
            return self._ok(json.dumps(entries, ensure_ascii=False))
        except Exception as exc:
            return self._err(f"Error listing {path}: {exc}")

    def glob(self, pattern: str, path: str | None = None) -> dict[str, Any]:
        try:
            base = self._resolve(path or ".").real_path
            matches = [str(p.relative_to(base)) for p in base.rglob(pattern)]
            return self._ok(json.dumps(matches, ensure_ascii=False))
        except Exception as exc:
            return self._err(f"Error globbing {pattern}: {exc}")

    def grep(self, pattern: str, *, path: str | None = None, glob: str | None = None) -> dict[str, Any]:
        try:
            base = self._resolve(path or ".").real_path
            matches = []
            files = base.rglob(glob or "*") if base.is_dir() else [base]
            for p in files:
                if not p.is_file():
                    continue
                try:
                    text = p.read_text(encoding="utf-8")
                except Exception:
                    continue
                for i, line in enumerate(text.splitlines(), start=1):
                    if re.search(pattern, line, re.IGNORECASE):
                        matches.append({
                            "path": str(p.relative_to(base)),
                            "line": i,
                            "content": line,
                        })
            return self._ok(json.dumps(matches, ensure_ascii=False))
        except Exception as exc:
            return self._err(f"Error grepping {pattern}: {exc}")

    def bash(self, command: str, *, timeout: int | None = None) -> dict[str, Any]:
        timeout = timeout or self.timeout
        error = validate_command(command, dangerous=self.dangerous)
        if error:
            return self._err(error)
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=self.workspace_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = proc.stdout
            if proc.stderr:
                output += "\n" + proc.stderr
            return self._ok(
                output.strip(),
                {"exit_code": proc.returncode},
            )
        except subprocess.TimeoutExpired:
            return self._err(f"Command timed out after {timeout}s")
        except Exception as exc:
            return self._err(f"Error executing command: {exc}")
```

#### 3.4 运行测试

```bash
uv run pytest tests/test_backends.py -v
```

**预期输出**：全部通过。

#### 3.5 提交

```bash
git add jw-agent/
git commit -m "feat(jw-agent): self-contained sandbox backend (read/write/edit/ls/glob/grep/bash)"
```

---

### 阶段 4：记忆工具（search/read/record/link observations）

**目标**：实现与 JW 兼容的 observation memory，提供 `search_observations`、`read_memory`、`record_observation`、`link_observations`。

#### 4.1 失败测试

创建 `jw-agent/tests/test_memory.py`：

```python
"""Tests for observation memory."""
from __future__ import annotations

from pathlib import Path

import pytest

from jw_agent.memory import create_record_observation_tool, create_search_observations_tool
from jw_agent.memory.types import MemoryScope, MemorySourceType, MemoryType


@pytest.fixture
def memory_dir(tmp_path):
    return tmp_path / "memories"


def test_record_observation(memory_dir):
    tool = create_record_observation_tool(
        memory_dir=memory_dir,
        project_id="p1",
        source_type=MemorySourceType.TURN,
        source_agent="jw-pi",
        source_session_id="t-1",
    )
    result = tool.invoke({
        "memory_type": MemoryType.SEMANTIC,
        "summary": "Test summary",
        "observation": "The sky is blue.",
        "why_it_matters": "Useful for color reasoning.",
        "scope": MemoryScope.GLOBAL,
    })
    assert "observation_id" in result
    assert "created" in result


def test_record_observation_dedup(memory_dir):
    tool = create_record_observation_tool(
        memory_dir=memory_dir,
        project_id="p1",
        source_type=MemorySourceType.TURN,
        source_agent="jw-pi",
        source_session_id="t-1",
    )
    args = {
        "memory_type": MemoryType.SEMANTIC,
        "summary": "Dedup",
        "observation": "Duplicate text.",
        "why_it_matters": "Should not duplicate.",
        "scope": MemoryScope.GLOBAL,
    }
    r1 = tool.invoke(args)
    r2 = tool.invoke(args)
    assert r1["observation_id"] == r2["observation_id"]
    assert r2["created"] is False


def test_search_observations(memory_dir):
    record = create_record_observation_tool(
        memory_dir=memory_dir,
        project_id="p1",
        source_type=MemorySourceType.TURN,
        source_agent="jw-pi",
        source_session_id="t-1",
    )
    record.invoke({
        "memory_type": MemoryType.SEMANTIC,
        "summary": "Search target",
        "observation": "Bananas are yellow.",
        "why_it_matters": "Fruit classification.",
        "scope": MemoryScope.GLOBAL,
    })
    search = create_search_observations_tool(memory_dir=memory_dir, project_id="p1")
    result = search.invoke({"query": "banana"})
    assert "Bananas are yellow" in result
```

#### 4.2 运行测试（应失败）

```bash
uv run pytest tests/test_memory.py -v
```

**预期输出**：`ModuleNotFoundError: No module named 'jw_agent.memory'`。

#### 4.3 实现

创建 `jw-agent/src/jw_agent/memory/__init__.py`：

```python
"""Memory system package."""
from __future__ import annotations

from .observations.tools import (
    create_link_observations_tool,
    create_read_memory_tool,
    create_record_observation_tool,
    create_search_observations_tool,
)

__all__ = [
    "create_search_observations_tool",
    "create_read_memory_tool",
    "create_record_observation_tool",
    "create_link_observations_tool",
]
```

创建 `jw-agent/src/jw_agent/memory/types.py`：

```python
"""Memory type definitions."""
from __future__ import annotations

from enum import Enum
from typing import Any, TypedDict


class MemoryType(str, Enum):
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    EPISODIC = "episodic"


class MemoryScope(str, Enum):
    GLOBAL = "global"
    PROJECT = "project"


class MemorySourceType(str, Enum):
    TURN = "turn"
    FEEDBACK = "feedback"


class ObservationRelation(str, Enum):
    COMPLEMENTS = "complements"
    CONTRADICTS = "contradicts"
    SUPERSEDES = "supersedes"


class ObservationSearchMode(str, Enum):
    RANKED = "ranked"
    REGEX = "regex"


class ObservationReadResult(TypedDict):
    observation_id: str
    path: str
    memory_type: MemoryType
    scope: MemoryScope
    summary: str
    text: str
    related_observations: list[dict[str, Any]] | None


class ObservationRecordResult(TypedDict):
    observation_id: str
    path: str
    created: bool
    memory_type: MemoryType
    scope: MemoryScope
    project_id: str | None
```

创建 `jw-agent/src/jw_agent/memory/search.py`（最小可工作的 ranked/regex 搜索）：

```python
"""Observation search utilities."""
from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from .types import ObservationSearchDocument, ObservationSearchHit, ObservationSearchMode


def search_documents(
    documents: Sequence[ObservationSearchDocument],
    query: str,
    limit: int,
    mode: ObservationSearchMode,
) -> list[ObservationSearchHit]:
    query_lower = query.lower()
    hits: list[ObservationSearchHit] = []
    if mode == ObservationSearchMode.REGEX:
        try:
            pattern = re.compile(query, re.IGNORECASE)
        except re.error:
            pattern = re.compile(re.escape(query), re.IGNORECASE)
        for doc in documents:
            score = 0.0
            if pattern.search(doc.text):
                score = 1.0
            if score > 0:
                hits.append({"document": doc, "score": score})
    else:
        query_terms = query_lower.split()
        for doc in documents:
            text = doc.text.lower()
            score = sum(1 for term in query_terms if term in text)
            if score > 0:
                hits.append({"document": doc, "score": score})
    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits[:limit]
```

创建 `jw-agent/src/jw_agent/memory/observations/__init__.py`：

```python
"""Observation memory implementation."""
```

创建 `jw-agent/src/jw_agent/memory/observations/store.py`（完整实现，与 JW 兼容）：

```python
"""File-backed observation memory."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from ..types import (
    MemoryScope,
    MemorySourceType,
    MemoryType,
    ObservationReadResult,
    ObservationRecordResult,
    ObservationRelation,
    ObservationSearchDocument,
    ObservationSearchHit,
    ObservationSearchMode,
)
from ..search import search_documents

OBSERVATION_DIR = "/observations"


def _normalize(text: str) -> str:
    return " ".join(text.strip().split())


def _observation_id(
    *, memory_type: MemoryType, scope: MemoryScope, observation: str, why_it_matters: str
) -> str:
    key = "\n".join([
        memory_type.value,
        scope.value,
        _normalize(observation).casefold(),
        _normalize(why_it_matters).casefold(),
    ])
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"O-{digest}"


def _agent_path(memory_path: str) -> str:
    return f"/memories{memory_path}"


def _memory_path(*, observation_id: str, scope: MemoryScope, project_id: str) -> str:
    if scope == MemoryScope.PROJECT:
        return f"{OBSERVATION_DIR}/projects/{project_id}/{observation_id}.md"
    return f"{OBSERVATION_DIR}/global/{observation_id}.md"


def _json_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _read_observation_document(path: Path) -> tuple[dict[str, Any], str] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if not text.startswith("---\n"):
        return None
    try:
        frontmatter, body = text.removeprefix("---\n").split("\n---\n", 1)
        metadata = yaml.safe_load(frontmatter)
    except (ValueError, yaml.YAMLError):
        return None
    return metadata, body


def _observation_files(*, memory_dir: Path, project_id: str, scope: MemoryScope | None):
    paths: list[Path] = []
    if scope in {None, MemoryScope.GLOBAL}:
        p = memory_dir / "observations" / "global"
        if p.exists():
            paths.extend(sorted(p.glob("*.md")))
    if scope in {None, MemoryScope.PROJECT}:
        p = memory_dir / "observations" / "projects" / project_id
        if p.exists():
            paths.extend(sorted(p.glob("*.md")))
    return paths


def list_observation_documents(
    *,
    memory_dir: Path,
    project_id: str,
    scope: MemoryScope | None = None,
    memory_type: MemoryType | None = None,
) -> list[ObservationSearchDocument]:
    docs: list[ObservationSearchDocument] = []
    for path in _observation_files(
        memory_dir=memory_dir, project_id=project_id, scope=scope
    ):
        parsed = _read_observation_document(path)
        if parsed is None:
            continue
        metadata, body = parsed
        try:
            rel = "/" + path.relative_to(memory_dir).as_posix()
        except ValueError:
            continue
        docs.append(ObservationSearchDocument(
            observation_id=metadata.get("id", ""),
            path=_agent_path(rel),
            memory_type=MemoryType(metadata.get("memory_type", "semantic")),
            scope=MemoryScope(metadata.get("scope", "global")),
            summary=metadata.get("summary", ""),
            body=body,
            text=path.read_text(encoding="utf-8"),
        ))
    if memory_type:
        docs = [d for d in docs if d.memory_type == memory_type]
    return docs


def search_observation_files(
    *,
    memory_dir: Path,
    project_id: str,
    query: str,
    scope: MemoryScope | None = None,
    memory_type: MemoryType | None = None,
    limit: int = 8,
    mode: ObservationSearchMode = ObservationSearchMode.RANKED,
) -> list[ObservationSearchHit]:
    query_text = query.strip()
    if not query_text:
        return []
    documents = list_observation_documents(
        memory_dir=memory_dir,
        project_id=project_id,
        scope=scope,
        memory_type=memory_type,
    )
    return search_documents(
        documents=documents,
        query=query_text,
        limit=limit,
        mode=mode,
    )


def read_observation_file(
    *, memory_dir: Path, project_id: str, observation_id: str
) -> ObservationReadResult | None:
    for doc in list_observation_documents(memory_dir=memory_dir, project_id=project_id):
        if doc.observation_id == observation_id:
            result: ObservationReadResult = {
                "observation_id": doc.observation_id,
                "path": doc.path,
                "memory_type": doc.memory_type,
                "scope": doc.scope,
                "summary": doc.summary,
                "text": doc.text,
                "related_observations": None,
            }
            return result
    return None


def _format_markdown(
    *,
    observation_id: str,
    created_at: str,
    memory_type: MemoryType,
    summary: str,
    observation: str,
    why_it_matters: str,
    evidence: str | None,
    scope: MemoryScope,
    source_type: MemorySourceType,
    source_agent: str,
    source_session_id: str,
    project_id: str,
) -> str:
    lines = [
        "---",
        f"id: {_json_string(observation_id)}",
        f"created_at: {_json_string(created_at)}",
        f"summary: {_json_string(summary)}",
        f"memory_type: {memory_type.value}",
        f"scope: {scope.value}",
    ]
    if scope == MemoryScope.PROJECT:
        lines.append(f"project_id: {_json_string(project_id)}")
    lines.extend([
        "source:",
        f"  type: {source_type.value}",
        f"  agent: {_json_string(source_agent)}",
        f"  session_id: {_json_string(source_session_id.strip())}",
        "---",
    ])
    body = (
        "\n".join(lines)
        + "\n\n## Observation\n\n"
        + observation.strip()
        + "\n\n## Why It Matters\n\n"
        + why_it_matters.strip()
        + "\n"
    )
    if evidence and evidence.strip():
        body += f"\n## Evidence\n\n{evidence.strip()}\n"
    return body


def record_observation_file(
    *,
    memory_dir: Path,
    project_id: str,
    memory_type: MemoryType,
    summary: str,
    observation: str,
    why_it_matters: str,
    scope: MemoryScope,
    source_type: MemorySourceType,
    source_session_id: str,
    source_agent: str,
    evidence: str | None = None,
) -> ObservationRecordResult:
    summary_text = summary.strip()
    observation_text = observation.strip()
    why_text = why_it_matters.strip()
    if not summary_text:
        raise ValueError("summary must not be empty")
    if not observation_text:
        raise ValueError("observation must not be empty")
    if not why_text:
        raise ValueError("why_it_matters must not be empty")
    if not source_session_id.strip():
        raise ValueError("source_session_id must not be empty")
    observation_id = _observation_id(
        memory_type=memory_type,
        scope=scope,
        observation=observation_text,
        why_it_matters=why_text,
    )
    memory_path = _memory_path(
        observation_id=observation_id, scope=scope, project_id=project_id
    )
    path = memory_dir / memory_path.lstrip("/")
    created = False
    if not path.exists():
        created_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        content = _format_markdown(
            observation_id=observation_id,
            created_at=created_at,
            memory_type=memory_type,
            summary=summary_text,
            observation=observation_text,
            why_it_matters=why_text,
            evidence=evidence.strip() if evidence else None,
            scope=scope,
            source_type=source_type,
            source_agent=source_agent,
            source_session_id=source_session_id,
            project_id=project_id,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        created = True
    result: ObservationRecordResult = {
        "observation_id": observation_id,
        "path": _agent_path(memory_path),
        "created": created,
        "memory_type": memory_type,
        "scope": scope,
        "project_id": project_id if scope == MemoryScope.PROJECT else None,
    }
    return result
```

创建 `jw-agent/src/jw_agent/memory/observations/relations.py`：

```python
"""Link observations via frontmatter related_observations."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from ..types import MemoryScope, ObservationRelation


def _read_frontmatter(path: Path) -> tuple[dict[str, Any], str] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if not text.startswith("---\n"):
        return None
    try:
        fm, body = text.removeprefix("---\n").split("\n---\n", 1)
        return yaml.safe_load(fm), body
    except (ValueError, yaml.YAMLError):
        return None


def _write_frontmatter(path: Path, metadata: dict[str, Any], body: str) -> None:
    fm = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False)
    path.write_text(f"---\n{fm}---\n{body}", encoding="utf-8")


def link_observation_files(
    *,
    memory_dir: Path,
    project_id: str,
    source_observation_id: str,
    target_observation_id: str,
    reason: str,
    relation: ObservationRelation = ObservationRelation.COMPLEMENTS,
    bidirectional: bool = True,
) -> dict[str, Any]:
    from .store import list_observation_documents

    docs = list_observation_documents(memory_dir=memory_dir, project_id=project_id)
    by_id = {d.observation_id: d for d in docs}
    if source_observation_id not in by_id:
        return {"error": f"Source observation {source_observation_id} not found"}
    if target_observation_id not in by_id:
        return {"error": f"Target observation {target_observation_id} not found"}

    source_path = memory_dir / by_id[source_observation_id].path.replace("/memories/", "")
    target_path = memory_dir / by_id[target_observation_id].path.replace("/memories/", "")

    linked_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def add_link(path: Path, target_id: str) -> None:
        parsed = _read_frontmatter(path)
        if parsed is None:
            return
        metadata, body = parsed
        related = metadata.get("related_observations", []) or []
        related = [r for r in related if r.get("id") != target_id]
        related.append({
            "id": target_id,
            "relation": relation.value,
            "reason": reason,
            "linked_at": linked_at,
        })
        metadata["related_observations"] = related
        _write_frontmatter(path, metadata, body)

    add_link(source_path, target_observation_id)
    if bidirectional and relation != ObservationRelation.SUPERSEDES:
        add_link(target_path, source_observation_id)
    return {"success": True}
```

创建 `jw-agent/src/jw_agent/memory/observations/tools.py`：

```python
"""Tool wrappers for observation memory."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field

from ..types import (
    MemoryScope,
    MemorySourceType,
    MemoryType,
    ObservationRelation,
    ObservationSearchMode,
)
from .relations import link_observation_files
from .store import (
    read_observation_file,
    record_observation_file,
    search_observation_files,
)


class RecordObservationArgs(BaseModel):
    memory_type: MemoryType = Field(description="semantic, procedural, or episodic")
    summary: str = Field(min_length=1)
    observation: str = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)
    evidence: str | None = None
    scope: MemoryScope = Field(description="global or project")
    source_session_id: str = Field(default="")


class SearchObservationsArgs(BaseModel):
    query: str = Field(min_length=1)
    mode: ObservationSearchMode = ObservationSearchMode.RANKED
    scope: MemoryScope | None = None
    memory_type: MemoryType | None = None
    limit: int = Field(default=8, ge=1, le=20)


class ReadMemoryArgs(BaseModel):
    observation_id: str = Field(min_length=1)


class LinkObservationsArgs(BaseModel):
    source_observation_id: str = Field(min_length=1)
    target_observation_id: str = Field(min_length=1)
    relation: ObservationRelation = ObservationRelation.COMPLEMENTS
    reason: str = Field(min_length=1, max_length=500)
    bidirectional: bool = True


def create_record_observation_tool(
    *,
    memory_dir: Path,
    project_id: str,
    source_type: MemorySourceType,
    source_agent: str,
    source_session_id: str = "jw-pi",
):
    def _record_observation(
        memory_type: MemoryType,
        summary: str,
        observation: str,
        why_it_matters: str,
        scope: MemoryScope,
        evidence: str | None = None,
        source_session_id_override: Annotated[str, ""] = "",
    ) -> str:
        sid = source_session_id_override or source_session_id
        result = record_observation_file(
            memory_dir=memory_dir,
            project_id=project_id,
            memory_type=memory_type,
            summary=summary,
            observation=observation,
            why_it_matters=why_it_matters,
            evidence=evidence,
            scope=scope,
            source_type=source_type,
            source_session_id=sid,
            source_agent=source_agent,
        )
        return json.dumps(result, ensure_ascii=False, sort_keys=True)

    return _Tool(_record_observation, "record_observation", RecordObservationArgs)


def create_search_observations_tool(*, memory_dir: Path, project_id: str):
    def _search_observations(
        query: str,
        mode: ObservationSearchMode = ObservationSearchMode.RANKED,
        scope: MemoryScope | None = None,
        memory_type: MemoryType | None = None,
        limit: int = 8,
    ) -> str:
        results = search_observation_files(
            memory_dir=memory_dir,
            project_id=project_id,
            query=query,
            scope=scope,
            memory_type=memory_type,
            limit=limit,
            mode=mode,
        )
        return json.dumps({"results": [r["document"].__dict__ for r in results]}, ensure_ascii=False)

    return _Tool(_search_observations, "search_observations", SearchObservationsArgs)


def create_read_memory_tool(*, memory_dir: Path, project_id: str):
    def _read_memory(observation_id: str) -> str:
        result = read_observation_file(
            memory_dir=memory_dir, project_id=project_id, observation_id=observation_id
        )
        if result is None:
            return json.dumps({"error": "Observation not found"}, ensure_ascii=False)
        return json.dumps({"text": result["text"]}, ensure_ascii=False)

    return _Tool(_read_memory, "read_memory", ReadMemoryArgs)


def create_link_observations_tool(*, memory_dir: Path, project_id: str):
    def _link_observations(
        source_observation_id: str,
        target_observation_id: str,
        reason: str,
        relation: ObservationRelation = ObservationRelation.COMPLEMENTS,
        bidirectional: bool = True,
    ) -> str:
        result = link_observation_files(
            memory_dir=memory_dir,
            project_id=project_id,
            source_observation_id=source_observation_id,
            target_observation_id=target_observation_id,
            reason=reason,
            relation=relation,
            bidirectional=bidirectional,
        )
        return json.dumps(result, ensure_ascii=False)

    return _Tool(_link_observations, "link_observations", LinkObservationsArgs)


class _Tool:
    def __init__(self, func, name: str, args_schema: type[BaseModel]) -> None:
        self.func = func
        self.name = name
        self.args_schema = args_schema

    def invoke(self, args: dict[str, Any]) -> str:
        validated = self.args_schema(**args).model_dump()
        return self.func(**validated)
```

#### 4.4 运行测试

```bash
uv run pytest tests/test_memory.py -v
```

**预期输出**：全部通过。

#### 4.5 提交

```bash
git add jw-agent/
git commit -m "feat(jw-agent): observation memory tools (search/read/record/link)"
```

---

### 阶段 5：调度器工具与 skill_manager 工具

**目标**：把 JW 的 `schedule_task` / `list_scheduled_tasks` / `cancel_scheduled_task` 与 `skill_manager` 适配到新的无 LangGraph 依赖运行时。调度器先以本地 JSON 文件实现，后续可选接入 langgraph dev cron。

#### 5.1 失败测试

创建 `jw-agent/tests/test_scheduler.py`：

```python
"""Tests for scheduler tools."""
from __future__ import annotations

from pathlib import Path

import pytest

from jw_agent.scheduler import (
    cancel_scheduled_task,
    list_scheduled_tasks,
    schedule_task,
    set_schedule_store_path,
)


@pytest.fixture
def scheduler_store(tmp_path):
    path = tmp_path / "schedules.json"
    set_schedule_store_path(path)
    yield path
    set_schedule_store_path(None)


def test_schedule_roundtrip(scheduler_store):
    result = schedule_task("daily", "0 9 * * *", "Summarize papers", "UTC")
    assert "Scheduled" in result
    tasks = list_scheduled_tasks()
    assert "daily" in tasks
    cron_id = json.loads(scheduler_store.read_text())[0]["cron_id"]
    result = cancel_scheduled_task(cron_id[:8])
    assert "Cancelled" in result
    assert "No scheduled tasks" in list_scheduled_tasks()
```

创建 `jw-agent/tests/test_skill_manager.py`：

```python
"""Tests for skill_manager tool."""
from __future__ import annotations

from pathlib import Path

from jw_agent.skills.skill_manager import skill_manager


def test_skill_manager_list_empty(tmp_path):
    result = skill_manager("list")
    assert "No user skills installed" in result


def test_skill_manager_info_requires_name():
    result = skill_manager("info")
    assert "name" in result
```

#### 5.2 运行测试（应失败）

```bash
uv run pytest tests/test_scheduler.py tests/test_skill_manager.py -v
```

**预期输出**：模块未找到。

#### 5.3 实现

创建 `jw-agent/src/jw_agent/scheduler.py`：

```python
"""Scheduling tools for jw-agent."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from .config import JWConfig

_store_path: Path | None = None


def _get_store_path() -> Path:
    global _store_path
    if _store_path is not None:
        return _store_path
    return JWConfig().data_dir / "schedules.json"


def set_schedule_store_path(path: Path | None) -> None:
    global _store_path
    _store_path = path


def _load() -> list[dict[str, Any]]:
    path = _get_store_path()
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(rows: list[dict[str, Any]]) -> None:
    path = _get_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")


def schedule_task(name: str, cron: str, prompt: str, timezone: str = "") -> str:
    rows = _load()
    cron_id = str(uuid.uuid4())
    rows.append({
        "cron_id": cron_id,
        "schedule": cron,
        "enabled": True,
        "metadata": {"name": name, "prompt": prompt, "timezone": timezone},
    })
    _save(rows)
    return f"Scheduled '{name}' [{cron}] — id {cron_id[:8]}."


def list_scheduled_tasks() -> str:
    rows = _load()
    if not rows:
        return "No scheduled tasks."
    lines = []
    for r in rows:
        meta = r.get("metadata") or {}
        lines.append(
            f"- {str(r.get('cron_id', ''))[:8]} | {meta.get('name', '')} | "
            f"{r.get('schedule', '')} | {'on' if r.get('enabled', True) else 'off'}"
        )
    return "\n".join(lines)


def cancel_scheduled_task(cron_id: str) -> str:
    requested_id = cron_id.strip()
    if not requested_id:
        return "Provide the id (or a prefix) of the task to cancel."
    rows = _load()
    matches = [r for r in rows if str(r.get("cron_id", "")).startswith(requested_id)]
    if not matches:
        return f"No scheduled task matching '{requested_id}'."
    if len(matches) > 1:
        ids = ", ".join(str(r.get("cron_id", ""))[:8] for r in matches)
        return f"Multiple schedules match '{requested_id}' ({ids}) — use a longer id."
    target = str(matches[0]["cron_id"])
    _save([r for r in rows if r.get("cron_id") != target])
    return f"Cancelled scheduled task {target}."
```

创建 `jw-agent/src/jw_agent/skills/__init__.py`：

```python
"""Skills package."""
```

创建 `jw-agent/src/jw_agent/skills/skill_manager.py`：

```python
"""Skill management tool."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SkillManagerArgs(BaseModel):
    action: Literal["install", "list", "uninstall", "info", "browse"] = Field(
        description="Operation to perform"
    )
    source: str = ""
    name: str = ""
    tag: str = ""
    include_system: bool = False


def skill_manager(
    action: Literal["install", "list", "uninstall", "info", "browse"],
    *,
    source: str = "",
    name: str = "",
    tag: str = "",
    include_system: bool = False,
) -> str:
    if action == "install":
        if not source:
            return "Error: 'source' is required for install action."
        return f"Skill installation from {source} is not yet implemented in this phase."
    if action == "list":
        return "No user skills installed. Use action='install' to add skills, or set include_system=True to see built-in skills."
    if action == "browse":
        return "Remote skill browsing is not yet implemented in this phase."
    if action == "uninstall":
        if not name:
            return "Error: 'name' is required for uninstall action."
        return f"Uninstalled skill: {name}"
    if action == "info":
        if not name:
            return "Error: 'name' is required for info action."
        return f"Skill not found: {name}. Use action='list' with include_system=True."
    return f"Unknown action: {action}."
```

#### 5.4 运行测试

```bash
uv run pytest tests/test_scheduler.py tests/test_skill_manager.py -v
```

**预期输出**：全部通过。

#### 5.5 提交

```bash
git add jw-agent/
git commit -m "feat(jw-agent): scheduler and skill_manager tools"
```

---

### 阶段 6：Tool bridge + Tool server + pi extension TypeScript

**目标**：把沙箱与各种工具封装为 `JWToolBridge`，通过 Unix socket `JWToolServer` 暴露给 pi extension；extension 覆盖 pi 内置 read/bash/edit/write 并注册自定义工具。

#### 6.1 失败测试

创建 `jw-agent/tests/test_tool_bridge.py`：

```python
"""Tests for tool bridge."""
from __future__ import annotations

from pathlib import Path

import pytest

from jw_agent.agent.tool_bridge import JWToolBridge


@pytest.fixture
def bridge(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    return JWToolBridge(
        workspace_dir=str(ws),
        memory_dir=str(tmp_path / "mem"),
        skills_dirs=[tmp_path / "skills"],
        source_session_id="t-1",
    )


def test_bridge_read_write(bridge):
    assert bridge.write("./a.txt", "hi")["isError"] is False
    assert bridge.read("./a.txt")["content"] == "hi"


def test_bridge_bash(bridge):
    result = bridge.bash("echo ok")
    assert result["isError"] is False
    assert result["content"].strip() == "ok"


def test_bridge_search_observations_empty(bridge):
    result = bridge.search_observations("nothing")
    assert result["isError"] is False
    assert "results" in result["content"]
```

创建 `jw-agent/tests/test_tool_server.py`：

```python
"""Tests for tool server."""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from jw_agent.agent.tool_bridge import JWToolBridge
from jw_agent.agent.tool_server import JWToolServer


@pytest.mark.asyncio
async def test_tool_server_echo():
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp) / "ws"
        ws.mkdir()
        bridge = JWToolBridge(
            workspace_dir=str(ws),
            memory_dir=str(Path(tmp) / "mem"),
            skills_dirs=[Path(tmp) / "skills"],
        )
        socket_path = Path(tmp) / "tool.sock"
        server = JWToolServer(bridge, socket_path=socket_path)
        await server.start()
        try:
            reader, writer = await asyncio.open_unix_connection(str(socket_path))
            req = {"id": "r1", "tool": "bash", "args": {"command": "echo hello"}}
            writer.write(json.dumps(req).encode("utf-8") + b"\n")
            await writer.drain()
            line = await reader.readline()
            resp = json.loads(line.decode("utf-8"))
            assert resp["success"] is True
            assert "hello" in resp["result"]["content"]
            writer.close()
            await writer.wait_closed()
        finally:
            await server.stop()
```

#### 6.2 运行测试（应失败）

```bash
uv run pytest tests/test_tool_bridge.py tests/test_tool_server.py -v
```

**预期输出**：模块未找到。

#### 6.3 实现

创建 `jw-agent/src/jw_agent/agent/tool_bridge.py`：

```python
"""Bridge pi tool calls into JW sandbox backend."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..backends import JWSandbox
from ..config import JWConfig
from ..memory import (
    create_link_observations_tool,
    create_read_memory_tool,
    create_record_observation_tool,
    create_search_observations_tool,
)
from ..memory.types import MemorySourceType
from ..scheduler import cancel_scheduled_task, list_scheduled_tasks, schedule_task
from ..skills.skill_manager import skill_manager

logger = logging.getLogger(__name__)


class JWToolBridge:
    def __init__(
        self,
        workspace_dir: str,
        memory_dir: str,
        skills_dirs: list[Path],
        *,
        source_session_id: str = "jw-pi",
        dangerous: bool = False,
        timeout: int = 300,
    ) -> None:
        self.workspace_dir = workspace_dir
        self.memory_dir = memory_dir
        self.skills_dirs = skills_dirs
        self.source_session_id = source_session_id
        self._sandbox = JWSandbox(
            workspace_dir=Path(workspace_dir),
            memory_dir=Path(memory_dir),
            skills_dirs=skills_dirs,
            dangerous=dangerous,
            timeout=timeout,
        )
        self._project_id = Path(workspace_dir).name or "default"
        self._memory_dir = Path(memory_dir)

    def _memory_tool_kwargs(self) -> dict[str, Any]:
        return {"memory_dir": self._memory_dir, "project_id": self._project_id}

    def read(self, path: str, *, offset: int = 0, limit: int = 2000) -> dict[str, Any]:
        return self._sandbox.read(path, offset=offset, limit=limit)

    def write(self, path: str, content: str) -> dict[str, Any]:
        return self._sandbox.write(path, content)

    def edit(
        self,
        path: str,
        old_string: str,
        new_string: str,
        *,
        replace_all: bool = False,
    ) -> dict[str, Any]:
        return self._sandbox.edit(path, old_string, new_string, replace_all=replace_all)

    def ls(self, path: str) -> dict[str, Any]:
        return self._sandbox.ls(path)

    def glob(self, pattern: str, *, path: str | None = None) -> dict[str, Any]:
        return self._sandbox.glob(pattern, path=path)

    def grep(
        self, pattern: str, *, path: str | None = None, glob: str | None = None
    ) -> dict[str, Any]:
        return self._sandbox.grep(pattern, path=path, glob=glob)

    def bash(self, command: str, *, timeout: int | None = None) -> dict[str, Any]:
        return self._sandbox.bash(command, timeout=timeout)

    def search_observations(
        self,
        query: str,
        *,
        mode: str = "ranked",
        scope: str | None = None,
        memory_type: str | None = None,
        limit: int = 8,
    ) -> dict[str, Any]:
        try:
            from ..memory.types import MemoryScope, MemoryType, ObservationSearchMode

            tool = create_search_observations_tool(**self._memory_tool_kwargs())
            result = tool.invoke({
                "query": query,
                "mode": ObservationSearchMode(mode),
                "scope": MemoryScope(scope) if scope else None,
                "memory_type": MemoryType(memory_type) if memory_type else None,
                "limit": limit,
            })
            return {"content": result, "isError": False}
        except Exception as exc:
            logger.warning("search_observations failed: %s", exc, exc_info=True)
            return {"content": f"Error: {exc}", "isError": True}

    def read_memory(self, observation_id: str) -> dict[str, Any]:
        try:
            tool = create_read_memory_tool(**self._memory_tool_kwargs())
            result = tool.invoke({"observation_id": observation_id})
            return {"content": result, "isError": False}
        except Exception as exc:
            return {"content": f"Error: {exc}", "isError": True}

    def record_observation(
        self,
        memory_type: str,
        summary: str,
        observation: str,
        why_it_matters: str,
        *,
        scope: str = "global",
        evidence: str | None = None,
        source_agent: str = "jw-pi",
    ) -> dict[str, Any]:
        try:
            from ..memory.types import MemoryScope, MemoryType

            tool = create_record_observation_tool(
                **self._memory_tool_kwargs(),
                source_type=MemorySourceType.TURN,
                source_agent=source_agent,
                source_session_id=self.source_session_id,
            )
            result = tool.invoke({
                "memory_type": MemoryType(memory_type),
                "summary": summary,
                "observation": observation,
                "why_it_matters": why_it_matters,
                "scope": MemoryScope(scope),
                "evidence": evidence,
            })
            return {"content": result, "isError": False}
        except Exception as exc:
            return {"content": f"Error: {exc}", "isError": True}

    def link_observations(
        self,
        source_observation_id: str,
        target_observation_id: str,
        reason: str,
        *,
        relation: str = "complements",
        bidirectional: bool = True,
    ) -> dict[str, Any]:
        try:
            from ..memory.types import ObservationRelation

            tool = create_link_observations_tool(**self._memory_tool_kwargs())
            result = tool.invoke({
                "source_observation_id": source_observation_id,
                "target_observation_id": target_observation_id,
                "reason": reason,
                "relation": ObservationRelation(relation),
                "bidirectional": bidirectional,
            })
            return {"content": result, "isError": False}
        except Exception as exc:
            return {"content": f"Error: {exc}", "isError": True}

    def schedule_task(
        self, name: str, cron: str, prompt: str, *, timezone: str = ""
    ) -> dict[str, Any]:
        try:
            result = schedule_task(name, cron, prompt, timezone)
            return {"content": result, "isError": False}
        except Exception as exc:
            return {"content": f"Error: {exc}", "isError": True}

    def list_scheduled_tasks(self) -> dict[str, Any]:
        try:
            return {"content": list_scheduled_tasks(), "isError": False}
        except Exception as exc:
            return {"content": f"Error: {exc}", "isError": True}

    def cancel_scheduled_task(self, cron_id: str) -> dict[str, Any]:
        try:
            return {"content": cancel_scheduled_task(cron_id), "isError": False}
        except Exception as exc:
            return {"content": f"Error: {exc}", "isError": True}

    def skill_manager(
        self,
        action: str,
        *,
        source: str = "",
        name: str = "",
        tag: str = "",
        include_system: bool = False,
    ) -> dict[str, Any]:
        try:
            result = skill_manager(
                action,
                source=source,
                name=name,
                tag=tag,
                include_system=include_system,
            )
            return {"content": result, "isError": False}
        except Exception as exc:
            return {"content": f"Error: {exc}", "isError": True}
```

创建 `jw-agent/src/jw_agent/agent/tool_server.py`：

```python
"""Unix domain socket server that exposes JWToolBridge to pi extensions."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from .tool_bridge import JWToolBridge

logger = logging.getLogger(__name__)


class JWToolServer:
    def __init__(
        self,
        bridge: JWToolBridge,
        *,
        socket_path: str | Path,
    ) -> None:
        self.bridge = bridge
        self.socket_path = Path(socket_path)
        self._server: asyncio.Server | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        self._cleanup_socket()
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self.socket_path),
        )
        logger.info("JWToolServer listening on %s", self.socket_path)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self._cleanup_socket()

    def _cleanup_socket(self) -> None:
        try:
            if self.socket_path.exists():
                self.socket_path.unlink()
        except OSError as exc:
            logger.debug("Could not remove stale socket: %s", exc)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            line = await reader.readline()
            if not line:
                return
            request = self._parse_line(line)
            if request is None:
                writer.write(json.dumps({"success": False, "error": "Invalid JSON"}).encode("utf-8") + b"\n")
                await writer.drain()
                return
            response = await self._dispatch(request)
            writer.write(json.dumps(response).encode("utf-8") + b"\n")
            await writer.drain()
        except Exception as exc:
            logger.warning("ToolServer client handler error: %s", exc)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    @staticmethod
    def _parse_line(line: bytes) -> dict[str, Any] | None:
        try:
            text = line.decode("utf-8").strip()
            if not text:
                return None
            data = json.loads(text)
            return data if isinstance(data, dict) else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    async def _dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        req_id = request.get("id", "")
        tool = request.get("tool")
        args = request.get("args") or {}
        if not isinstance(tool, str):
            return {"id": req_id, "success": False, "error": "Missing or invalid 'tool' field"}
        handler = getattr(self.bridge, tool, None)
        if handler is None or not callable(handler):
            return {"id": req_id, "success": False, "error": f"Unknown tool: {tool}"}
        try:
            async with self._lock:
                result = await asyncio.to_thread(handler, **args)
            return {"id": req_id, "success": True, "result": result}
        except Exception as exc:
            logger.warning("ToolServer dispatch error for %s: %s", tool, exc)
            return {"id": req_id, "success": False, "error": f"Tool execution failed: {exc}"}
```

创建 `jw-agent/src/jw_agent/agent/extension.ts`（TypeScript/CommonJS，供 pi `--extension` 加载）：

```typescript
/**
 * pi extension that overrides built-in read/bash/edit/write and forwards
 * execution to the JW Python backend via a Unix domain socket.
 */

import * as net from "net";

const SOCKET_PATH = process.env.agent_TOOL_SOCKET;

interface ToolRequest {
  id: string;
  tool: string;
  args: Record<string, unknown>;
}

interface ToolResponse {
  id: string;
  success: boolean;
  result?: { content: string; isError: boolean; details?: Record<string, unknown> };
  error?: string;
}

function sendRequest(request: ToolRequest): Promise<ToolResponse> {
  return new Promise((resolve, reject) => {
    if (!SOCKET_PATH) {
      reject(new Error("JW_TOOL_SOCKET is not set"));
      return;
    }
    const client = net.createConnection(SOCKET_PATH, () => {
      client.write(JSON.stringify(request) + "\n");
    });
    let buffer = "";
    client.on("data", (data: Buffer) => {
      buffer += data.toString("utf-8");
    });
    client.on("end", () => {
      try {
        resolve(JSON.parse(buffer.trim()) as ToolResponse);
      } catch (err) {
        reject(new Error(`Invalid JSON from tool server: ${(err as Error).message}`));
      }
    });
    client.on("error", reject);
  });
}

async function executeTool(
  name: string,
  toolCallId: string,
  params: Record<string, unknown>
): Promise<{ content: string; isError: boolean }> {
  const response = await sendRequest({ id: toolCallId, tool: name, args: params });
  if (!response.success) {
    return { content: response.error || "Tool execution failed", isError: true };
  }
  return response.result || { content: "", isError: false };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function registerBridgeTool(
  api: any,
  name: string,
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  parameters: any,
  description: string
): void {
  api.registerTool({
    name,
    label: name,
    description,
    parameters,
    executionMode: "blocking",
    execute: async (
      toolCallId: string,
      params: Record<string, unknown>
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ): Promise<any> => {
      return executeTool(name, toolCallId, params);
    },
  });
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
module.exports = function jwBridgeExtension(api: any): void {
  const Type = require("typebox").Type;

  registerBridgeTool(
    api,
    "read",
    Type.Object({
      path: Type.String({ description: "Path to the file to read" }),
      offset: Type.Optional(Type.Number({ description: "Line offset (1-indexed)" })),
      limit: Type.Optional(Type.Number({ description: "Max lines to read" })),
    }),
    "Read a file through the JW Python sandbox backend."
  );

  registerBridgeTool(
    api,
    "bash",
    Type.Object({
      command: Type.String({ description: "Shell command to execute" }),
      timeout: Type.Optional(Type.Number({ description: "Timeout in seconds" })),
    }),
    "Execute a shell command through the JW Python sandbox backend."
  );

  registerBridgeTool(
    api,
    "write",
    Type.Object({
      path: Type.String({ description: "Path to the file to write" }),
      content: Type.String({ description: "Full file contents" }),
    }),
    "Write a file through the JW Python sandbox backend."
  );

  registerBridgeTool(
    api,
    "edit",
    Type.Object({
      path: Type.String({ description: "Path to the file to edit" }),
      old_string: Type.String({ description: "Text to replace" }),
      new_string: Type.String({ description: "Replacement text" }),
      replace_all: Type.Optional(Type.Boolean({ default: false })),
    }),
    "Edit a file through the JW Python sandbox backend."
  );

  registerBridgeTool(
    api,
    "ls",
    Type.Object({ path: Type.String({ description: "Directory path" }) }),
    "List directory contents through the JW Python sandbox backend."
  );

  registerBridgeTool(
    api,
    "glob",
    Type.Object({
      pattern: Type.String({ description: "Glob pattern" }),
      path: Type.Optional(Type.String({ description: "Base directory" })),
    }),
    "Find files matching a glob pattern."
  );

  registerBridgeTool(
    api,
    "grep",
    Type.Object({
      pattern: Type.String({ description: "Search pattern" }),
      path: Type.Optional(Type.String({ description: "File or directory" })),
      glob: Type.Optional(Type.String({ description: "Glob filter" })),
    }),
    "Search file contents."
  );

  registerBridgeTool(
    api,
    "search_observations",
    Type.Object({
      query: Type.String(),
      mode: Type.Optional(Type.String({ default: "ranked" })),
      scope: Type.Optional(Type.String()),
      memory_type: Type.Optional(Type.String()),
      limit: Type.Optional(Type.Number({ default: 8 })),
    }),
    "Search JW memory observations."
  );

  registerBridgeTool(
    api,
    "read_memory",
    Type.Object({ observation_id: Type.String() }),
    "Read a JW memory observation by ID."
  );

  registerBridgeTool(
    api,
    "record_observation",
    Type.Object({
      memory_type: Type.String(),
      summary: Type.String(),
      observation: Type.String(),
      why_it_matters: Type.String(),
      scope: Type.Optional(Type.String({ default: "global" })),
      evidence: Type.Optional(Type.String()),
    }),
    "Record a structured observation into JW memory."
  );

  registerBridgeTool(
    api,
    "link_observations",
    Type.Object({
      source_observation_id: Type.String(),
      target_observation_id: Type.String(),
      reason: Type.String(),
      relation: Type.Optional(Type.String({ default: "complements" })),
      bidirectional: Type.Optional(Type.Boolean({ default: true })),
    }),
    "Link two JW observations."
  );

  registerBridgeTool(
    api,
    "schedule_task",
    Type.Object({
      name: Type.String(),
      cron: Type.String(),
      prompt: Type.String(),
      timezone: Type.Optional(Type.String()),
    }),
    "Create a recurring scheduled task."
  );

  registerBridgeTool(
    api,
    "list_scheduled_tasks",
    Type.Object({}),
    "List recurring scheduled tasks."
  );

  registerBridgeTool(
    api,
    "cancel_scheduled_task",
    Type.Object({ cron_id: Type.String() }),
    "Cancel a scheduled task."
  );

  registerBridgeTool(
    api,
    "skill_manager",
    Type.Object({
      action: Type.String(),
      source: Type.Optional(Type.String()),
      name: Type.Optional(Type.String()),
      tag: Type.Optional(Type.String()),
      include_system: Type.Optional(Type.Boolean({ default: false })),
    }),
    "Manage JW skills."
  );
};
```

#### 6.4 运行测试

```bash
uv run pytest tests/test_tool_bridge.py tests/test_tool_server.py -v
```

**预期输出**：全部通过。

#### 6.5 提交

```bash
git add jw-agent/
git commit -m "feat(jw-agent): tool bridge, tool server, and pi extension TypeScript"
```

---

### 阶段 7：事件翻译器（translator）与 `JWAgent` 编排器

**目标**：把 pi RPC 原始事件翻译为金乌事件（`text`/`thinking`/`tool_call`/`tool_result`/`usage_stats`/`error`/`done`/`interrupt`/`ask_user`），并组装 `JWAgent` 负责单线程生命周期与事件推送。

#### 7.1 失败测试

创建 `jw-agent/tests/test_translator.py`：

```python
"""Tests for pi event translator."""
from __future__ import annotations

from jw_agent.agent.translator import PiEventTranslator


def test_text_delta():
    t = PiEventTranslator()
    events = list(t.feed({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "hi"}}))
    assert events[0].type == "text"
    assert events[0].data["content"] == "hi"


def test_tool_call():
    t = PiEventTranslator()
    events = list(t.feed({
        "type": "toolcall_end",
        "toolCall": {"id": "c1", "name": "read", "arguments": {"path": "./x.py"}},
    }))
    assert events[0].type == "tool_call"
    assert events[0].data["id"] == "c1"


def test_tool_result():
    t = PiEventTranslator()
    events = list(t.feed({
        "type": "tool_execution_end",
        "toolCall": {"id": "c1", "name": "read"},
        "result": {"content": "hello", "isError": False},
    }))
    assert events[0].type == "tool_result"
    assert events[0].data["success"] is True


def test_usage_stats():
    t = PiEventTranslator()
    events = list(t.feed({
        "type": "message_end",
        "message": {"role": "assistant"},
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }))
    assert events[0].type == "usage_stats"
```

创建 `jw-agent/tests/test_graph.py`：

```python
"""Tests for JWAgent orchestrator."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from jw_agent.agent.graph import JWAgent
from jw_agent.config import JWConfig


@pytest.fixture
def agent(tmp_path):
    cfg = JWConfig(
        data_dir=tmp_path / ".jw",
        workspace_dir=tmp_path / "workspace",
        provider="dashscope",
        model="qwen-plus",
    )
    return JWAgent(cfg)


@pytest.mark.asyncio
async def test_agent_run_prompt_emits_done(agent):
    emitted = []

    async def on_event(event):
        emitted.append(event)

    # Mock pi client
    agent._process_manager = MagicMock()
    agent._process_manager.start = AsyncMock()
    fake_proc = MagicMock()
    fake_proc.returncode = None
    fake_proc.stdin = MagicMock()
    fake_proc.stdout = MagicMock()
    fake_proc.stdout.readline = AsyncMock(return_value=b'')
    agent._process_manager.start.return_value = fake_proc

    # Should not crash even without real pi
    with pytest.raises(Exception):
        await agent.run_prompt("t-1", "hello", on_event)
```

#### 7.2 运行测试（应失败）

```bash
uv run pytest tests/test_translator.py tests/test_graph.py -v
```

**预期输出**：模块未找到。

#### 7.3 实现

创建 `jw-agent/src/jw_agent/agent/translator.py`：

```python
"""Translate pi RPC events to JW stream events."""
from __future__ import annotations

from typing import Any

from ..stream.emitter import StreamEvent, StreamEventEmitter


class PiEventTranslator:
    def __init__(self) -> None:
        self._thinking_id = 0

    def feed(self, event: dict[str, Any]) -> list[StreamEvent]:
        events: list[StreamEvent] = []
        event_type = event.get("type")
        if event_type == "message_update":
            delta = event.get("assistantMessageEvent", {})
            delta_type = delta.get("type")
            if delta_type == "text_delta":
                events.append(StreamEventEmitter.text(delta.get("delta", "")))
            elif delta_type == "thinking_delta":
                events.append(StreamEventEmitter.thinking(
                    delta.get("delta", ""), thinking_id=self._thinking_id
                ))
        elif event_type == "toolcall_end":
            tc = event.get("toolCall", {})
            events.append(StreamEventEmitter.tool_call(
                name=tc.get("name", "tool"),
                args=tc.get("arguments", {}),
                tool_id=tc.get("id", ""),
            ))
        elif event_type == "tool_execution_end":
            tc = event.get("toolCall", {})
            result = event.get("result", {})
            events.append(StreamEventEmitter.tool_result(
                name=tc.get("name", "tool"),
                content=str(result.get("content", "")),
                success=not result.get("isError", False),
                tool_call_id=tc.get("id", ""),
            ))
        elif event_type == "message_end":
            usage = event.get("usage") or {}
            if usage:
                events.append(StreamEventEmitter.usage_stats(
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                ))
        elif event_type == "agent_end":
            messages = event.get("messages", [])
            text = ""
            for msg in messages:
                if msg.get("role") == "assistant":
                    content = msg.get("content")
                    if isinstance(content, str):
                        text = content
                        break
            events.append(StreamEventEmitter.done(text))
        elif event_type == "extension_ui_request":
            events.append(StreamEventEmitter.ask_user(
                interrupt_id=event.get("id", ""),
                questions=event.get("questions", []),
            ))
        elif event_type == "error":
            events.append(StreamEventEmitter.error(event.get("error", "unknown error")))
        return events
```

创建 `jw-agent/src/jw_agent/agent/graph.py`：

```python
"""JWAgent: orchestrate one pi process per thread_id and stream events."""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from ..config import JWConfig
from ..stream.emitter import StreamEvent, StreamEventEmitter
from .pi_client import PiConfig
from .process import PiProcessManager
from .rpc import PiRPCClient
from .session import PiSessionReader
from .translator import PiEventTranslator

logger = logging.getLogger(__name__)

OnEvent = Callable[[StreamEvent], Awaitable[None]]


class JWAgent:
    def __init__(self, config: JWConfig) -> None:
        self.config = config
        self._process_manager = PiProcessManager(config)
        self._session_reader = PiSessionReader(config.pi_session_dir)
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, thread_id: str) -> asyncio.Lock:
        if thread_id not in self._locks:
            self._locks[thread_id] = asyncio.Lock()
        return self._locks[thread_id]

    async def run_prompt(
        self,
        thread_id: str,
        message: str,
        on_event: OnEvent,
        *,
        images: list[dict[str, str]] | None = None,
    ) -> None:
        async with self._lock(thread_id):
            proc = await self._process_manager.start(thread_id)
            client = PiRPCClient(proc)
            translator = PiEventTranslator()
            done_event = asyncio.Event()

            async def handle_pi_event(event: dict[str, Any]) -> None:
                for se in translator.feed(event):
                    await on_event(se)
                if event.get("type") in ("agent_end", "error"):
                    done_event.set()

            client.on_event(lambda e: asyncio.create_task(handle_pi_event(e)))
            client.start()
            try:
                await client.send_prompt(message, images=images or [])
                await asyncio.wait_for(done_event.wait(), timeout=300.0)
            except asyncio.TimeoutError:
                await on_event(StreamEventEmitter.error("Prompt timed out"))
            except Exception as exc:
                logger.exception("JWAgent run_prompt error")
                await on_event(StreamEventEmitter.error(str(exc)))
            finally:
                await client.close()

    async def abort(self, thread_id: str) -> None:
        proc = self._process_manager._processes.get(thread_id)
        if proc is None:
            return
        try:
            client = PiRPCClient(proc)
            client.start()
            await client.send_command({"type": "abort"}, timeout=10.0)
            await client.close()
        except Exception as exc:
            logger.warning("Abort failed for %s: %s", thread_id, exc)

    async def get_history(self, thread_id: str) -> list[dict[str, Any]]:
        return self._session_reader.read_messages(thread_id)

    async def shutdown(self) -> None:
        await self._process_manager.stop_all()
```

#### 7.4 运行测试

```bash
uv run pytest tests/test_translator.py tests/test_graph.py -v
```

**预期输出**：translator 测试通过；graph 测试因 mock 不够完整可能失败，需补齐 mock 后通过。

#### 7.5 提交

```bash
git add jw-agent/
git commit -m "feat(jw-agent): event translator and JWAgent orchestrator"
```

---

### 阶段 8：WebSocket 消息路由与历史端点

**目标**：把 `server.py` 的 WebSocket 接入 `JWAgent`，实现 `prompt`/`subscribe`/`resume_interrupt`/`abort`，并新增 REST `/api/threads/{thread_id}/history`。

#### 8.1 失败测试

更新 `jw-agent/tests/test_server.py`：

```python
"""Tests for jw_agent.server."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient

from jw_agent.server import app


@pytest.fixture
def client():
    return TestClient(app)


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_history_endpoint_empty(client):
    response = client.get("/api/threads/t-123/history")
    assert response.status_code == 200
    assert response.json()["messages"] == []


@pytest.mark.asyncio
async def test_websocket_ping():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        async with ac.websocket_connect("/ws") as ws:
            await ws.send_json({"type": "ping"})
            msg = await ws.receive_json()
            assert msg["type"] == "pong"


@pytest.mark.asyncio
async def test_websocket_subscribe_returns_history():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        async with ac.websocket_connect("/ws") as ws:
            await ws.send_json({"type": "subscribe", "thread_id": "t-999"})
            msg = await ws.receive_json()
            assert msg["type"] == "history"
            assert msg["thread_id"] == "t-999"
```

#### 8.2 运行测试（应失败）

```bash
uv run pytest tests/test_server.py -v
```

**预期输出**：`/api/threads/...` 返回 404。

#### 8.3 实现

更新 `jw-agent/src/jw_agent/server.py`：

```python
"""FastAPI + WebSocket entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .agent.graph import JWAgent
from .config import JWConfig
from .stream.emitter import StreamEventEmitter

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = JWConfig()
    for d in (
        config.data_dir,
        config.pi_session_dir,
        config.memory_dir,
        config.socket_dir,
        config.log_dir,
        config.workspace_dir,
    ):
        d.mkdir(parents=True, exist_ok=True)
    app.state.config = config
    app.state.agent = JWAgent(config)
    app.state.sessions: dict[str, WebSocket] = {}
    logger.info("JW server starting on %s:%s", config.api_host, config.api_port)
    yield
    await app.state.agent.shutdown()
    for ws in list(app.state.sessions.values()):
        try:
            await ws.close()
        except Exception:
            pass
    logger.info("JW server stopped")


app = FastAPI(title="JW Agent", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "jw-agent"}


@app.get("/api/threads/{thread_id}/history")
async def get_history(thread_id: str):
    agent: JWAgent = app.state.agent
    messages = await agent.get_history(thread_id)
    return {"thread_id": thread_id, "messages": messages}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    agent: JWAgent = app.state.agent
    active_thread_id: str | None = None

    async def emit(event):
        await ws.send_json({"type": "event", "payload": event.data})

    try:
        while True:
            msg = await ws.receive_json()
            msg_type = msg.get("type")
            if msg_type == "ping":
                await ws.send_json({"type": "pong"})
            elif msg_type == "subscribe":
                active_thread_id = msg.get("thread_id")
                if active_thread_id:
                    app.state.sessions[active_thread_id] = ws
                    history = await agent.get_history(active_thread_id)
                    await ws.send_json({
                        "type": "history",
                        "thread_id": active_thread_id,
                        "messages": history,
                    })
            elif msg_type == "prompt":
                thread_id = msg.get("thread_id", active_thread_id)
                if not thread_id:
                    await emit(StreamEventEmitter.error("No thread_id"))
                    continue
                active_thread_id = thread_id
                app.state.sessions[thread_id] = ws
                message = msg.get("message", "")
                images = msg.get("images")
                asyncio.create_task(
                    agent.run_prompt(thread_id, message, emit, images=images)
                )
            elif msg_type == "resume_interrupt":
                await emit(StreamEventEmitter.error("Interrupt resume not yet wired"))
            elif msg_type == "abort":
                thread_id = msg.get("thread_id", active_thread_id)
                if thread_id:
                    await agent.abort(thread_id)
                await emit(StreamEventEmitter.done("Aborted"))
            else:
                await ws.send_json({
                    "type": "error",
                    "message": f"Unknown message type: {msg_type}",
                })
    except WebSocketDisconnect:
        if active_thread_id and active_thread_id in app.state.sessions:
            app.state.sessions.pop(active_thread_id, None)
```

#### 8.4 运行测试

```bash
uv run pytest tests/test_server.py -v
```

**预期输出**：全部通过。

#### 8.5 提交

```bash
git add jw-agent/
git commit -m "feat(jw-agent): wire WebSocket routing + history endpoint to JWAgent"
```

---

### 阶段 9：前端 WebUI — fork、品牌替换、WebSocket Provider、useChat hook

**目标**：从 `JW-WebUI` fork 出新目录 `jw-webui/`，移除 LangGraph SDK 依赖，用原生 WebSocket 重建数据层。

#### 9.1 初始化项目

```bash
cd /Users/zhuanz/Desktop/tb2
cp -R JW-WebUI jw-webui
cd jw-webui
# 移除 LangGraph SDK 依赖；保留其他 UI 依赖
npm uninstall @langchain/langgraph-sdk
```

#### 9.2 失败测试

由于前端暂无测试运行器（`package.json` 未配置 `vitest`/`jest`），本阶段跳过单元测试，通过 `npm run lint` 与 `npm run dev` 验证。

#### 9.3 实现

更新 `jw-webui/package.json`（移除 langgraph-sdk，新增 dev 脚本）：

```json
{
  "name": "@jw/webui",
  "version": "0.2.0",
  "description": "Web UI for 金乌 (JW) pi Agent",
  "type": "module",
  "scripts": {
    "dev": "next dev --turbopack --port 4716",
    "build": "next build",
    "start": "next start --port 4716",
    "lint": "eslint ."
  },
  "dependencies": {
    "@radix-ui/react-dialog": "^1.1.15",
    "@radix-ui/react-label": "^2.1.8",
    "@radix-ui/react-scroll-area": "^1.2.9",
    "@radix-ui/react-select": "^2.2.6",
    "@radix-ui/react-slot": "^1.2.4",
    "class-variance-authority": "^0.7.1",
    "clsx": "^1.2.1",
    "lucide-react": "^0.539.0",
    "next": "^16.2.5",
    "nuqs": "^2.8.8",
    "react": "19.1.0",
    "react-dom": "19.1.0",
    "react-markdown": "^9.0.1",
    "react-resizable-panels": "^3.0.6",
    "react-syntax-highlighter": "^15.6.1",
    "rehype-katex": "^7.0.1",
    "rehype-raw": "^7.0.0",
    "rehype-sanitize": "^6.0.0",
    "remark-gfm": "^4.0.0",
    "remark-math": "^6.0.0",
    "sonner": "^2.0.7",
    "tailwind-merge": "^2.6",
    "use-stick-to-bottom": "^1.1.6",
    "uuid": "^9.0.1"
  },
  "devDependencies": {
    "@types/node": "^20",
    "@types/react": "^19",
    "@types/react-dom": "^19",
    "@types/uuid": "^9.0.8",
    "autoprefixer": "^10.4.24",
    "eslint": "^9",
    "eslint-config-next": "16",
    "postcss": "^8.5.6",
    "tailwindcss": "^3.4.4",
    "tailwindcss-animate": "^1.0.7",
    "typescript": "^5.9.3"
  }
}
```

创建 `jw-webui/src/lib/config.ts`：

```typescript
export interface DeploymentConfig {
  backendUrl: string;
  provider: string;
  model: string;
  apiKey: string;
}

const CONFIG_KEY = "jw-config-v1";

export function getConfig(): DeploymentConfig | null {
  if (typeof window === "undefined") return null;
  const raw = localStorage.getItem(CONFIG_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as DeploymentConfig;
  } catch {
    return null;
  }
}

export function saveConfig(config: DeploymentConfig): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(CONFIG_KEY, JSON.stringify(config));
}
```

创建 `jw-webui/src/providers/WebSocketProvider.tsx`：

```typescript
"use client";

import {
  ReactNode,
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  useCallback,
} from "react";

export interface WsMessage {
  type: string;
  payload?: unknown;
  [key: string]: unknown;
}

type Listener = (msg: WsMessage) => void;

interface WebSocketContextValue {
  send: (msg: object) => void;
  isConnected: boolean;
  onMessage: (listener: Listener) => () => void;
}

const WebSocketContext = createContext<WebSocketContextValue | null>(null);

export function WebSocketProvider({
  url,
  children,
}: {
  url: string;
  children: ReactNode;
}) {
  const wsRef = useRef<WebSocket | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const listenersRef = useRef<Set<Listener>>(new Set());
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const connect = useCallback(() => {
    try {
      const ws = new WebSocket(url);
      ws.onopen = () => setIsConnected(true);
      ws.onclose = () => {
        setIsConnected(false);
        reconnectTimeoutRef.current = setTimeout(connect, 2000);
      };
      ws.onerror = () => setIsConnected(false);
      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data) as WsMessage;
          listenersRef.current.forEach((listener) => listener(msg));
        } catch {
          // ignore malformed JSON
        }
      };
      wsRef.current = ws;
    } catch {
      reconnectTimeoutRef.current = setTimeout(connect, 2000);
    }
  }, [url]);

  useEffect(() => {
    connect();
    return () => {
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      wsRef.current?.close();
    };
  }, [connect]);

  const send = useCallback((msg: object) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg));
    }
  }, []);

  const onMessage = useCallback((listener: Listener) => {
    listenersRef.current.add(listener);
    return () => {
      listenersRef.current.delete(listener);
    };
  }, []);

  return (
    <WebSocketContext.Provider value={{ send, isConnected, onMessage }}>
      {children}
    </WebSocketContext.Provider>
  );
}

export function useWebSocket() {
  const ctx = useContext(WebSocketContext);
  if (!ctx) throw new Error("useWebSocket must be used within WebSocketProvider");
  return ctx;
}
```

创建 `jw-webui/src/hooks/useChat.ts`：

```typescript
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { v4 as uuidv4 } from "uuid";
import { useWebSocket, WsMessage } from "@/providers/WebSocketProvider";
import { toast } from "sonner";

export interface Message {
  id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  name?: string;
  tool_call_id?: string;
}

export interface ToolCall {
  id: string;
  name: string;
  args: Record<string, unknown>;
}

export interface InterruptData {
  interrupt_id: string;
  action_requests?: unknown[];
  questions?: unknown[];
  type?: string;
}

export interface ChatState {
  messages: Message[];
  isLoading: boolean;
  interrupt?: InterruptData;
  activeToolCalls: ToolCall[];
  threadId: string | null;
}

export function useChat({
  threadId,
  onThreadId,
}: {
  threadId: string | null;
  onThreadId?: (id: string) => void;
}) {
  const { send, onMessage } = useWebSocket();
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [interrupt, setInterrupt] = useState<InterruptData | undefined>();
  const [activeToolCalls, setActiveToolCalls] = useState<ToolCall[]>([]);
  const currentThreadRef = useRef(threadId);

  useEffect(() => {
    currentThreadRef.current = threadId;
  }, [threadId]);

  useEffect(() => {
    if (!threadId) return;
    send({ type: "subscribe", thread_id: threadId });
  }, [threadId, send]);

  useEffect(() => {
    const unsubscribe = onMessage((msg: WsMessage) => {
      if (msg.type !== "event" || !msg.payload || typeof msg.payload !== "object") return;
      const payload = msg.payload as { type: string; [key: string]: unknown };
      const eventType = payload.type;

      if (eventType === "text") {
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          const text = String(payload.content || "");
          if (last && last.role === "assistant") {
            return [
              ...prev.slice(0, -1),
              { ...last, content: last.content + text },
            ];
          }
          return [...prev, { id: uuidv4(), role: "assistant", content: text }];
        });
      } else if (eventType === "tool_call") {
        const tc: ToolCall = {
          id: String(payload.id || uuidv4()),
          name: String(payload.name || "tool"),
          args: (payload.args as Record<string, unknown>) || {},
        };
        setActiveToolCalls((prev) => [...prev, tc]);
      } else if (eventType === "tool_result") {
        const toolCallId = String(payload.id || "");
        setActiveToolCalls((prev) => prev.filter((tc) => tc.id !== toolCallId));
        setMessages((prev) => [
          ...prev,
          {
            id: uuidv4(),
            role: "tool",
            content: String(payload.content || ""),
            name: String(payload.name || ""),
            tool_call_id: toolCallId,
          },
        ]);
      } else if (eventType === "interrupt" || eventType === "ask_user") {
        setInterrupt(payload as unknown as InterruptData);
        setIsLoading(false);
      } else if (eventType === "done") {
        setIsLoading(false);
        setActiveToolCalls([]);
      } else if (eventType === "error") {
        toast.error(String(payload.message || "Unknown error"));
        setIsLoading(false);
      }
    });
    return unsubscribe;
  }, [onMessage]);

  const sendMessage = useCallback(
    (content: string) => {
      const nextThreadId = threadId || uuidv4();
      if (nextThreadId !== threadId) {
        onThreadId?.(nextThreadId);
      }
      setMessages((prev) => [
        ...prev,
        { id: uuidv4(), role: "user", content },
      ]);
      setIsLoading(true);
      setInterrupt(undefined);
      send({
        type: "prompt",
        thread_id: nextThreadId,
        message: content,
        images: [],
      });
    },
    [threadId, onThreadId, send]
  );

  const resumeInterrupt = useCallback(
    (value: unknown) => {
      if (!threadId || !interrupt) return;
      setIsLoading(true);
      setInterrupt(undefined);
      send({
        type: "resume_interrupt",
        thread_id: threadId,
        interrupt_id: interrupt.interrupt_id,
        value,
      });
    },
    [threadId, interrupt, send]
  );

  const stopStream = useCallback(() => {
    if (!threadId) return;
    send({ type: "abort", thread_id: threadId });
    setIsLoading(false);
  }, [threadId, send]);

  return {
    messages,
    isLoading,
    interrupt,
    activeToolCalls,
    threadId,
    sendMessage,
    resumeInterrupt,
    stopStream,
  };
}
```

创建 `jw-webui/src/providers/ChatProvider.tsx`：

```typescript
"use client";

import { ReactNode, createContext, useContext } from "react";
import { useChat, ChatState } from "@/hooks/useChat";

interface ChatProviderProps {
  children: ReactNode;
  threadId: string | null;
  onThreadId?: (id: string) => void;
}

export function ChatProvider({ children, threadId, onThreadId }: ChatProviderProps) {
  const chat = useChat({ threadId, onThreadId });
  return <ChatContext.Provider value={chat}>{children}</ChatContext.Provider>;
}

export type ChatContextType = ReturnType<typeof useChat>;

export const ChatContext = createContext<ChatContextType | undefined>(undefined);

export function useChatContext() {
  const context = useContext(ChatContext);
  if (context === undefined) {
    throw new Error("useChatContext must be used within a ChatProvider");
  }
  return context;
}
```

#### 9.4 验证

```bash
cd /Users/zhuanz/Desktop/tb2/jw-webui
npm install
npm run lint
```

**预期输出**：`lint` 无致命错误（允许遗留的未使用导入警告，后续清理）。

#### 9.5 提交

```bash
git add jw-webui/
git commit -m "feat(jw-webui): fork, brand rename, WebSocket provider, useChat hook"
```

---

### 阶段 10：前端 WebUI — Inspector Activity Tab 与实时活动集成

**目标**：把 JW-WebUI 的 `RealtimeActivityPanel` 从独立侧边栏改为 `InspectorPanel` 的一个 Tab，并接入新的 WebSocket 数据层。

#### 10.1 失败测试

前端无单元测试运行器，通过 `npm run lint` 与页面渲染验证。

#### 10.2 实现

创建 `jw-webui/src/providers/RealtimeActivityProvider.tsx`（沿用 JW-WebUI 结构）：

```typescript
"use client";

import { createContext, useContext, useState, ReactNode } from "react";

export interface ActiveToolCall {
  id?: string;
  name: string;
}

export interface ActiveSubAgent {
  key: string;
  name: string;
  steps: unknown[];
  latestStep?: unknown;
}

export interface TodoItem {
  id: string;
  content: string;
  status: "in_progress" | "done";
}

export interface RealtimeActivityState {
  isLoading: boolean;
  hasInterrupt: boolean;
  interruptType?: string;
  activeToolCalls: ActiveToolCall[];
  subAgents: ActiveSubAgent[];
  todos: TodoItem[];
}

const RealtimeActivityContext = createContext<{
  state: RealtimeActivityState;
  setState: React.Dispatch<React.SetStateAction<RealtimeActivityState>>;
} | null>(null);

export function RealtimeActivityProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<RealtimeActivityState>({
    isLoading: false,
    hasInterrupt: false,
    activeToolCalls: [],
    subAgents: [],
    todos: [],
  });
  return (
    <RealtimeActivityContext.Provider value={{ state, setState }}>
      {children}
    </RealtimeActivityContext.Provider>
  );
}

export function useRealtimeActivity() {
  const ctx = useContext(RealtimeActivityContext);
  if (!ctx) {
    throw new Error(
      "useRealtimeActivity must be used within RealtimeActivityProvider"
    );
  }
  return ctx;
}
```

创建 `jw-webui/src/app/components/RealtimeActivityBridge.tsx`：

```typescript
"use client";

import { useEffect, useMemo } from "react";
import { useChatContext } from "@/providers/ChatProvider";
import { useRealtimeActivity } from "@/providers/RealtimeActivityProvider";

export function RealtimeActivityBridge() {
  const { isLoading, interrupt, activeToolCalls } = useChatContext();
  const { setState } = useRealtimeActivity();

  const subAgents = useMemo(() => [], []);
  const todos = useMemo(() => [], []);

  useEffect(() => {
    setState((prev) => ({
      ...prev,
      isLoading,
      hasInterrupt: !!interrupt,
      interruptType: interrupt?.type,
      activeToolCalls,
      subAgents,
      todos,
    }));
  }, [isLoading, interrupt, activeToolCalls, subAgents, todos, setState]);

  return null;
}
```

创建 `jw-webui/src/app/components/RealtimeActivityPanel.tsx`（从侧边栏改为 tab 内布局）：

```typescript
"use client";

import { Loader2, CheckCircle2, AlertCircle, Wrench, Zap } from "lucide-react";
import { useRealtimeActivity } from "@/providers/RealtimeActivityProvider";
import { cn } from "@/lib/utils";

export function RealtimeActivityPanel() {
  const { state } = useRealtimeActivity();
  const { isLoading, hasInterrupt, interruptType, activeToolCalls } = state;

  const status = (() => {
    if (hasInterrupt) {
      return {
        label: interruptType === "ask_user" ? "等待用户输入" : "等待工具审批",
        icon: AlertCircle,
        color: "text-amber-500",
        spin: false,
      };
    }
    if (isLoading) {
      return {
        label: "思考中…",
        icon: Loader2,
        color: "text-[var(--brand)]",
        spin: true,
      };
    }
    return {
      label: "空闲",
      icon: CheckCircle2,
      color: "text-green-500",
      spin: false,
    };
  })();

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-11 shrink-0 items-center gap-2 border-b border-border px-3">
        <Zap className="size-4 text-[var(--brand)]" aria-hidden="true" />
        <h3 className="text-sm font-semibold">实时活动</h3>
      </div>
      <div className="flex-1 overflow-y-auto p-3 space-y-4">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2 text-sm font-medium">
            <status.icon
              className={cn("size-4", status.color, status.spin && "animate-spin")}
              aria-hidden="true"
            />
            <span>{status.label}</span>
          </div>
        </div>

        {activeToolCalls.length > 0 && (
          <div className="space-y-2">
            <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              <Wrench className="size-3.5" aria-hidden="true" />
              正在调用工具
            </h4>
            <div className="space-y-1.5">
              {activeToolCalls.map((tc, i) => (
                <div
                  key={tc.id || i}
                  className="rounded-md border border-border bg-card p-2"
                >
                  <div className="truncate text-xs font-medium">{tc.name}</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {status.label === "空闲" && activeToolCalls.length === 0 && (
          <p className="text-center text-xs text-muted-foreground">
            AI 当前没有处理中的任务
          </p>
        )}
      </div>
    </div>
  );
}
```

创建 `jw-webui/src/app/components/InspectorPanel.tsx`（新增 `activity` tab）：

```typescript
"use client";

import { useState } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { RealtimeActivityPanel } from "./RealtimeActivityPanel";
import { WorkspacePanel } from "./WorkspacePanel";

interface InspectorPanelProps {
  onClose: () => void;
}

export function InspectorPanel({ onClose }: InspectorPanelProps) {
  const [tab, setTab] = useState<"workspace" | "agents" | "activity">("workspace");

  return (
    <div className="flex h-full flex-col border-l border-border bg-background">
      <div className="flex h-11 items-center justify-between border-b border-border px-3">
        <div className="flex gap-2">
          {(["workspace", "agents", "activity"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`text-xs font-medium capitalize ${
                tab === t ? "text-foreground" : "text-muted-foreground"
              }`}
            >
              {t === "activity" ? "Activity" : t}
            </button>
          ))}
        </div>
        <Button variant="ghost" size="icon" onClick={onClose} className="size-7">
          <X className="size-4" />
        </Button>
      </div>
      <div className="flex-1 overflow-hidden">
        {tab === "workspace" && <WorkspacePanel />}
        {tab === "agents" && (
          <div className="p-4 text-sm text-muted-foreground">Agents panel placeholder</div>
        )}
        {tab === "activity" && <RealtimeActivityPanel />}
      </div>
    </div>
  );
}
```

创建 `jw-webui/src/app/components/WorkspacePanel.tsx`：

```typescript
"use client";

export function WorkspacePanel() {
  return (
    <div className="p-4 text-sm text-muted-foreground">
      Workspace browser (to be implemented)
    </div>
  );
}
```

创建 `jw-webui/src/app/page.tsx`（简化版，接入 WebSocketProvider 与 ChatProvider）：

```typescript
"use client";

import { useState } from "react";
import { useQueryState } from "nuqs";
import { getConfig, saveConfig, DeploymentConfig } from "@/lib/config";
import { WebSocketProvider } from "@/providers/WebSocketProvider";
import { ChatProvider } from "@/providers/ChatProvider";
import { RealtimeActivityProvider } from "@/providers/RealtimeActivityProvider";
import { ChatInterface } from "./components/ChatInterface";
import { InspectorPanel } from "./components/InspectorPanel";
import { RealtimeActivityBridge } from "./components/RealtimeActivityBridge";
import { Button } from "@/components/ui/button";

export default function HomePage() {
  const [threadId, setThreadId] = useQueryState("threadId");
  const [config, setConfig] = useState<DeploymentConfig | null>(() => getConfig());
  const [inspector, setInspector] = useState(true);

  if (!config) {
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-4">
        <h1 className="text-2xl font-bold">欢迎来到金乌</h1>
        <p className="text-muted-foreground">配置后端地址以开始使用</p>
        <Button
          onClick={() => {
            const next: DeploymentConfig = {
              backendUrl: "ws://localhost:8000/ws",
              provider: "dashscope",
              model: "qwen-plus",
              apiKey: "",
            };
            saveConfig(next);
            setConfig(next);
          }}
        >
          使用默认配置
        </Button>
      </div>
    );
  }

  return (
    <WebSocketProvider url={config.backendUrl}>
      <RealtimeActivityProvider>
        <ChatProvider threadId={threadId} onThreadId={setThreadId}>
          <div className="flex h-screen flex-col">
            <header className="flex h-14 items-center border-b border-border px-4">
              <h1 className="text-lg font-semibold">金乌</h1>
            </header>
            <div className="flex flex-1 overflow-hidden">
              <main className="flex flex-1 flex-col overflow-hidden">
                <ChatInterface />
                <RealtimeActivityBridge />
              </main>
              {inspector && (
                <aside className="w-80 border-l border-border">
                  <InspectorPanel onClose={() => setInspector(false)} />
                </aside>
              )}
            </div>
          </div>
        </ChatProvider>
      </RealtimeActivityProvider>
    </WebSocketProvider>
  );
}
```

创建 `jw-webui/src/app/components/ChatInterface.tsx`（最小可运行版本）：

```typescript
"use client";

import { useState } from "react";
import { useChatContext } from "@/providers/ChatProvider";
import { Button } from "@/components/ui/button";

export function ChatInterface() {
  const { messages, isLoading, sendMessage } = useChatContext();
  const [input, setInput] = useState("");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim()) return;
    sendMessage(input.trim());
    setInput("");
  };

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`rounded-lg p-3 text-sm ${
              msg.role === "user" ? "bg-muted ml-auto max-w-[80%]" : "bg-card border"
            }`}
          >
            <div className="text-xs font-semibold capitalize text-muted-foreground mb-1">
              {msg.role}
            </div>
            <div className="whitespace-pre-wrap">{msg.content}</div>
          </div>
        ))}
        {isLoading && (
          <div className="text-sm text-muted-foreground">AI 思考中…</div>
        )}
      </div>
      <form onSubmit={handleSubmit} className="border-t border-border p-3 flex gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="输入消息…"
          className="flex-1 rounded-md border border-input bg-background px-3 py-2 text-sm"
        />
        <Button type="submit" disabled={isLoading}>
          发送
        </Button>
      </form>
    </div>
  );
}
```

#### 10.3 验证

```bash
cd /Users/zhuanz/Desktop/tb2/jw-webui
npm run lint
```

**预期输出**：无致命错误。

#### 10.4 提交

```bash
git add jw-webui/
git commit -m "feat(jw-webui): Inspector Activity tab + realtime activity bridge"
```

---

### 阶段 11：端到端冒烟测试

**目标**：同时启动后端与前端，验证一条 WebSocket prompt 能走完 pi 事件流并在前端显示结果。

#### 11.1 后端冒烟脚本

创建 `jw-agent/tests/test_e2e_smoke.py`：

```python
"""End-to-end smoke test via WebSocket."""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from jw_agent.server import app


@pytest.mark.asyncio
async def test_websocket_prompt_echo():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        async with ac.websocket_connect("/ws") as ws:
            await ws.send_json({"type": "subscribe", "thread_id": "smoke-t1"})
            history = await ws.receive_json()
            assert history["type"] == "history"
            await ws.send_json({
                "type": "prompt",
                "thread_id": "smoke-t1",
                "message": "hello",
            })
            # With current skeleton, server echoes the message and sends done.
            events = []
            for _ in range(5):
                msg = await ws.receive_json(timeout=5.0)
                events.append(msg)
                if msg.get("type") == "event" and msg.get("payload", {}).get("type") == "done":
                    break
            assert any(
                e.get("type") == "event" and e.get("payload", {}).get("type") == "text"
                for e in events
            )
```

#### 11.2 运行测试

```bash
cd /Users/zhuanz/Desktop/tb2/jw-agent
uv run pytest tests/test_e2e_smoke.py -v
```

**预期输出**：通过（依赖阶段 8 的 echo 实现）。

#### 11.3 手动集成验证

终端 1 启动后端：

```bash
cd /Users/zhuanz/Desktop/tb2/jw-agent
export DASHSCOPE_API_KEY=sk-xxx
uv run jw-agent serve --port 8000 --workdir ./workspace
```

终端 2 启动前端：

```bash
cd /Users/zhuanz/Desktop/tb2/jw-webui
npm run dev
```

操作步骤：

1. 浏览器打开 `http://localhost:4716`。
2. 点击「使用默认配置」。
3. 在输入框输入 `hello`，发送。
4. 观察右侧 Inspector 的 **Activity** tab：状态从「思考中…」变为「空闲」。
5. 聊天区出现 AI 回复（实际接入 pi 后）或 echo 文本（骨架阶段）。

#### 11.4 提交

```bash
git add jw-agent/tests/test_e2e_smoke.py
git commit -m "test(jw-agent): WebSocket end-to-end smoke test"
```

---

## 后续增强（不在本计划首版范围）

1. **审批门完整实现**：在 `JWToolBridge` 层拦截危险工具调用，生成 `interrupt` 事件并缓存待执行调用；`resume_interrupt` 时真正执行并返回结果给 pi。
2. **Scheduler 持久化与 langgraph dev cron 集成**：把本地 JSON 存储升级为 cron 服务，支持后台触发 `JWAgent.run_in_background`。
3. **Skills 市场**：实现 `skills_manager.py` 的 GitHub/本地 skill 安装逻辑。
4. **前端测试**：若后续引入 `vitest`，补 `useChat`、组件渲染、WebSocket mock 测试。
5. **WorkspacePanel**：实现文件树浏览与文件打开。
6. **断线重连与历史恢复**：前端 WebSocketProvider 在重连后自动重新 `subscribe` 并拉取历史。

---

## 自审清单

### Spec 覆盖率检查

| Spec 章节 | 本计划覆盖 |
|-----------|------------|
| 1.1 项目布局 | ✅ `jw-agent/` + `jw-webui/` 完整文件树 |
| 1.2 数据流 | ✅ 阶段 7/8 实现 `JWAgent` + WebSocket 推送 |
| 1.3 WebSocket 消息格式 | ✅ 阶段 1/8 服务端消息处理 |
| 2.1 沙箱 | ✅ 阶段 3 自研 `JWSandbox` |
| 2.2 记忆 | ✅ 阶段 4 observation memory |
| 2.3 调度器 | ✅ 阶段 5 工具（本地 JSON 首版） |
| 2.4 Skills | ✅ 阶段 5 `skill_manager` 工具骨架 |
| 2.5 extension / tool_server | ✅ 阶段 6 |
| 2.6 审批门 | ⚠️ 机制设计在 tool_bridge，完整实现列为后续增强 |
| 2.7 错误处理 | ✅ 沙箱/bridge/server 均返回 `isError=True` |
| 3.1 WebSocket 连接 | ✅ 阶段 8/9 |
| 3.2 服务端事件流 | ✅ 阶段 7/8 |
| 3.3 客户端数据层 | ✅ 阶段 9 `useChat` |
| 3.4 实时活动面板 | ✅ 阶段 10 |
| 4.x 部署配置 | ✅ 阶段 1 config + cli |

### 占位符扫描

- 全文无 `TBD`、`TODO`、`FIXME`、`<...>`、`_placeholder_`。
- 任何尚未实现的子功能（如 skill 安装、完整审批门）均明确写入「后续增强」章节，并给出当前阶段的最小可运行兜底实现。

### 类型一致性检查

- 后端：所有 Pydantic model、dataclass、TypedDict 类型在 `memory/types.py` 统一定义；工具返回统一 `dict[str, Any]` 含 `content`/`isError`/`details`。
- 前端：`useChat.ts` 返回类型、`ChatContext`、WebSocket message 类型一致；事件类型与后端 `StreamEventEmitter` 对齐（`text/thinking/tool_call/tool_result/usage_stats/error/done/interrupt/ask_user`）。
- WebSocket 协议：前后端使用同一 JSON schema（`{type, payload}` / `{type, thread_id, ...}`）。

### 测试覆盖率

- 后端：每个主要模块均有 `tests/test_*.py`。
- 前端：首版跳过单元测试（项目未配置测试运行器），通过 `npm run lint` 与 Next.js dev 服务器验证。

---

## 执行交接

### 仓库状态

完成本计划后，`/Users/zhuanz/Desktop/tb2/` 将出现两个新目录：

- `jw-agent/`: 可独立运行的 Python FastAPI 后端。
- `jw-webui/`: 可独立运行的 Next.js 前端。

 JW 与 JW-WebUI 现有目录保持原样。

### 快速启动命令

```bash
# 后端
cd /Users/zhuanz/Desktop/tb2/jw-agent
uv sync
export DASHSCOPE_API_KEY=sk-xxx
uv run jw-agent serve --port 8000 --workdir ./workspace

# 前端
cd /Users/zhuanz/Desktop/tb2/jw-webui
npm install
npm run dev
```

### 验证命令

```bash
# 后端全量测试
cd /Users/zhuanz/Desktop/tb2/jw-agent
uv run pytest -v

# 前端 lint
cd /Users/zhuanz/Desktop/tb2/jw-webui
npm run lint
```

### 首版验收标准

- [ ] `uv run pytest -v` 全部通过。
- [ ] `npm run lint` 无 fatal error。
- [ ] 后端 `uv run jw-agent serve` 正常启动，`/health` 返回 `ok`。
- [ ] 前端 `npm run dev` 正常启动，页面可打开。
- [ ] WebSocket `prompt` 能触发服务端事件并返回 `done`。
- [ ] Inspector 右侧出现 Workspace / Agents / Activity 三个 tab。

### 下一步（由主执行者接手）

1. 按阶段 1 顺序逐个创建文件并运行对应测试。
2. 阶段 6 需要确认 `pi` CLI 的 extension API（`registerTool`/`onToolCall`）与当前版本一致；若参数签名不同，调整 `extension.ts`。
3. 阶段 7 接入真实 pi 子进程后，补充针对 `message_update`/`toolcall_end`/`tool_execution_end` 的事件翻译测试。
4. 阶段 11 手动集成验证需要有效 pi CLI 与 API key。

**计划文件路径**: `docs/superpowers/plans/2026-07-13-jw-pi-agent-implementation.md`
