# 金乌 pi Agent 重写设计文档

> 目标：彻底脱离 JW 代码库，把底层 Agent harness 替换为 [pi Agent](https://pi.dev/)，并以「金乌」品牌重建一个独立的后端 + WebUI 项目。

---

## 1. 总体架构（已确认）

### 1.1 项目布局

```
/Users/zhuanz/Desktop/tb2/
├── jw-agent/                 # Python 后端
│   ├── pyproject.toml
│   └── src/jw_agent/
│       ├── __init__.py
│       ├── config.py            # 配置模型 + API key 映射
│       ├── server.py            # FastAPI + WebSocket 入口
│       ├── api.py               # REST API（线程、记忆、skills、调度、workspace）
│       ├── agent/
│       │   ├── __init__.py
│       │   ├── pi_client.py     # pi RPC 客户端（保留 pi-mcp-bridge 核心）
│       │   ├── process.py       # pi 子进程生命周期管理
│       │   ├── session.py       # pi session 文件读取
│       │   ├── translator.py    # pi 事件 → 金乌事件
│       │   ├── graph.py         # JWAgent：单线程/多线程 orchestrator
│       │   ├── tool_bridge.py   # pi 工具 → Python 沙箱/记忆/调度/skills
│       │   ├── tool_server.py   # Unix socket 服务，供 pi extension 回调
│       │   └── extension.ts     # pi extension：覆盖内置工具 + 暴露自定义工具
│       ├── backends.py          # 文件/Shell 沙箱后端
│       ├── memory/              # 记忆系统（观测 + profile）
│       ├── skills/              # skill 管理器
│       ├── scheduler.py         # 定时任务工具
│       └── stream/
│           ├── emitter.py       # 统一事件格式
│           └── events.py        # 事件路由
│
├── jw-webui/                 # Next.js 前端
│   ├── package.json
│   └── src/
│       ├── app/
│       │   ├── page.tsx
│       │   ├── layout.tsx
│       │   └── components/
│       │       ├── ChatInterface.tsx
│       │       ├── InspectorPanel.tsx
│       │       ├── RealtimeActivityPanel.tsx
│       │       ├── RealtimeActivityBridge.tsx
│       │       ├── ThreadList.tsx
│       │       ├── MemoryPanel.tsx
│       │       ├── ScheduledTasksPanel.tsx
│       │       └── WorkspacePanel.tsx
│       ├── providers/
│       │   ├── ChatProvider.tsx
│       │   ├── WebSocketProvider.tsx
│       │   └── RealtimeActivityProvider.tsx
│       ├── hooks/
│       │   └── useChat.ts
│       └── lib/
│           ├── config.ts
│           └── toolLabel.ts
│
└── docs/superpowers/specs/2026-07-13-jw-pi-agent-design.md
```

### 1.2 核心数据流

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
```

1. 用户在前端发送消息 → WebSocket `prompt`。
2. `JWAgent` 找到/启动对应 `thread_id` 的 pi 子进程。
3. pi 推理，输出 `text_delta` / `tool_call` / `tool_execution_*` 等事件。
4. `translator` 把 pi 事件转成金乌事件，通过 WebSocket 推给前端。
5. 遇到自定义工具（或需要沙箱隔离的工具）时，pi extension 通过 Unix socket 回调 `tool_server`，`tool_bridge` 执行后返回结果。
6. 遇到需要审批/提问的操作，生成 `interrupt` / `ask_user` 事件，前端弹出卡片；用户决策后通过 WebSocket `resume_interrupt` 写回。
7. turn 结束下发 `done` 事件。

### 1.3 WebSocket 消息格式

**客户端 → 服务端**

```json
{"type":"prompt","thread_id":"t-123","message":"分析一下数据","images":[],"config":{}}
{"type":"resume_interrupt","thread_id":"t-123","interrupt_id":"int-1","value":{"decision":"approve"}}
{"type":"abort","thread_id":"t-123"}
{"type":"ping"}
```

**服务端 → 客户端**

```json
{"type":"event","payload":{"type":"text","content":"..."}}
{"type":"event","payload":{"type":"tool_call","name":"read","args":{"path":"..."},"id":"call-1"}}
{"type":"event","payload":{"type":"tool_result","name":"read","content":"...","success":true,"id":"call-1"}}
{"type":"event","payload":{"type":"interrupt","interrupt_id":"...","action_requests":[...]}}
{"type":"event","payload":{"type":"done","response":"..."}}
{"type":"history","thread_id":"t-123","messages":[...]}
{"type":"pong"}
```

---

## 2. 工具层详细设计

> 原则：pi Agent 仍然负责「推理 + 生成工具调用意图」，但**具体执行全部落到 Python 侧**。这样我们才能保留金乌的沙箱、记忆、调度、skills 能力，并对危险操作做审批门。

### 2.1 文件 / Shell 沙箱（JWSandbox）

**职责**

- 把 pi 的虚拟路径（`./file.py`、`/skills/...`、`/memories/profile/...`）映射到真实磁盘路径。
- 提供 `read`、`write`、`edit`、`ls`、`glob`、`grep`、`bash`。
- 危险命令拦截、路径逃逸拦截、`..` 拦截。
- 支持 `dangerous_mode`（高级用户可关闭部分限制，但仍保留 `sudo/rm -rf /` 等灾难性命令拦截）。

**接口设计**

```python
class JWSandbox:
    def __init__(self, workspace_dir: Path, memory_dir: Path, skills_dirs: list[Path], *, dangerous: bool = False, timeout: int = 300):
        ...

    def read(self, path: str, offset: int = 0, limit: int = 2000) -> dict[str, Any]
    def write(self, path: str, content: str) -> dict[str, Any]
    def edit(self, path: str, old_string: str, new_string: str, *, replace_all: bool = False) -> dict[str, Any]
    def ls(self, path: str) -> dict[str, Any]
    def glob(self, pattern: str, path: str | None = None) -> dict[str, Any]
    def grep(self, pattern: str, *, path: str | None = None, glob: str | None = None) -> dict[str, Any]
    def bash(self, command: str, *, timeout: int | None = None) -> dict[str, Any]
```

**返回协议**

每个工具返回统一 JSON：

```json
{"content": "<文本或 JSON 字符串>", "isError": false, "details": {}}
```

- `content`：pi 能读懂的文本结果。
- `isError`：执行是否失败。
- `details`：可选元数据（如 bash 的 `exit_code`）。

**实现策略**

- 直接复用 JW 的 `backends.py` 路径映射、命令校验逻辑，但去掉对 `deepagents` 的隐式依赖。
- 若 `deepagents` 仍在依赖中，则调用其 `FilesystemBackend` / `LocalShellBackend`；否则逐步替换为自研实现。
- 沙箱实例按 `thread_id` 持有，workspace 路径固定，内存/skills 路径从配置读取。

### 2.2 记忆系统（Memory）

#### 2.2.1 Observations

**持久化格式**

- 文件型 markdown，带 YAML frontmatter，与 JW 完全兼容。
- 路径：
  - `<memory_dir>/observations/global/<id>.md`
  - `<memory_dir>/observations/projects/<project_id>/<id>.md`

**暴露给 pi 的工具**

| 工具名 | 作用 |
|--------|------|
| `search_observations` | 按关键词/正则搜索观测 |
| `read_memory` | 按 observation_id 读取完整 markdown |
| `record_observation` | 记录新观测 |
| `link_observations` | 建立观测间关系 |

**参数与行为**

- `record_observation` 需要 `memory_type`（semantic/procedural/episodic）、`summary`、`observation`、`why_it_matters`、`scope`（global/project）。
- `source_session_id` 由 `JWAgent` 注入，取自当前 `thread_id`。
- `source_agent` 固定为 `"jw-pi"`。
- 观测 ID 使用 sha256 摘要去重，避免重复记录。

#### 2.2.2 Profile 记忆

- Profile 是特殊的 `/memories/profile/` 下 markdown 文件（如 `profile.md`）。
- 允许 pi 通过 `edit` 直接修改（`MemoryFilesystemBackend` 的规则）。
- 提供 REST API：`GET /api/memory/profile`、`POST /api/memory/profile`。

### 2.3 调度器（Scheduler）

**设计选择**

- 金乌保留自己的调度后端（基于 `langgraph dev` 的 cron，或一个轻量 APScheduler）。
- 本阶段继续沿用 JW 的 `cron` 封装，因为定时任务需要持久化与 LangGraph 运行时的配合。
- pi 通过工具调用创建/列出/取消定时任务。

**暴露工具**

| 工具名 | 作用 |
|--------|------|
| `schedule_task` | `name, cron, prompt, timezone` |
| `list_scheduled_tasks` | 列出所有任务 |
| `cancel_scheduled_task` | 按 ID 前缀取消 |

**集成点**

- 定时任务触发时，调用 `JWAgent.run_in_background(prompt)`，复用同一套 pi 进程/线程模型。
- 调度器状态通过 REST API `/api/schedule` 暴露给前端。

### 2.4 Skills

**Skill 目录结构**

```
<workspace>/skills/              # 项目级 skills（可写）
~/.jw/skills/                 # 用户全局 skills（只读）
<package>/src/jw_agent/skills/ # 内置 skills（只读）
```

**暴露工具**

| 工具名 | 作用 |
|--------|------|
| `skill_manager` | install / list / browse / info / uninstall |

**与 pi 的协同**

- Skill 安装后，pi 通过 `read` 读取 skill 的 `SKILL.md`。
- Skill 的工具脚本放在 `/skills/<name>/scripts/` 下，pi 通过 `bash` 调用。
- Skill 不需要注册到 pi 的 extension schema；pi 的通用文件/Shell 工具即可驱动它们。

### 2.5 pi Extension 与 Tool Server

**为什么需要 extension**

pi 内置的 `read/bash/edit/write` 默认直接操作文件系统。为了让执行经过金乌沙箱、并在前端显示 `tool_call`/`tool_result`，我们用 extension **覆盖**这些工具，把所有调用转发到 Python `tool_server`。

**架构**

```
pi Agent
  │
  │ tool_call (read/bash/edit/write/search_observations/...)
  ▼
pi extension (TypeScript)
  │
  │ Unix domain socket / TCP
  ▼
jw-agent tool_server
  │
  ▼
JWSandbox / Memory / Scheduler / Skills
```

**extension.ts 职责**

1. 启动时向 pi 注册工具覆盖：
   - `read`, `write`, `edit`, `bash`
   - 自定义：`search_observations`, `read_memory`, `record_observation`, `link_observations`, `schedule_task`, `list_scheduled_tasks`, `cancel_scheduled_task`, `skill_manager`
2. 收到 tool call 时，通过 socket 发送：
   ```json
   {"id":"req-1","tool":"read","args":{"path":"./x.py"}}
   ```
3. 等待 Python 返回：
   ```json
   {"id":"req-1","success":true,"result":{"content":"...","isError":false}}
   ```
4. 把结果转回 pi 期望的格式。

**tool_server.py 职责**

- 启动 Unix socket（路径：`$JW_DATA_DIR/sockets/tool-server-<thread_id>.sock`）。
- 每个 socket 绑定一个 `JWSandbox` 实例（含当前 thread 的 workspace / project）。
- 序列化执行工具调用，防止并发破坏同步后端。
- 错误时返回 `isError=True`，让 pi 自行决定重试或报错。

**启动参数**

pi 启动时通过 `--extension <path/to/extension.ts>` 加载 extension；extension 从环境变量读取 socket 路径：

```bash
JW_TOOL_SOCKET=/path/to/tool.sock pi --mode rpc ...
```

### 2.6 审批门（Human-in-the-Loop）

**触发条件**

- `write` / `edit` 操作目标在 workspace 外或匹配危险模式。
- `bash` 命令命中高危模式（`rm -rf`, `sudo`, `chmod` 等）。
- `skill_manager` 的 `install` / `uninstall`。
- `schedule_task` 创建新任务。
- 自定义工具的 `record_observation` 默认无需审批，但可配置。

**实现方式**

- 在 `tool_bridge.py` 层拦截：发现需要审批时，不执行，返回一个特殊 `interrupt` 事件给 `JWAgent`。
- `JWAgent` 暂停 pi 事件流，通过 WebSocket 下发 `interrupt` 事件。
- 用户决策通过 `resume_interrupt` 写回；`tool_bridge` 缓存待执行的 tool call，收到审批后继续执行并把结果返回 pi。

### 2.7 工具层错误处理

| 场景 | 处理 |
|------|------|
| 沙箱路径越界 | 返回 `isError=True`，content 说明原因 |
| 命令被拦截 | 返回 `isError=True` |
| tool_server 未连接 | pi extension 返回错误，pi 重试或报错 |
| 工具执行异常 | 捕获并返回 `isError=True`，不崩溃 agent |

---

## 3. WebUI 集成设计

### 3.1 前后端协议：WebSocket

**连接管理**

- 前端页面打开时建立一条 WebSocket 连接到 `wss?://<backend>/ws`。
- 服务端按连接维护当前活跃的 `thread_id` 订阅；一个连接同时只能监听一个 thread。
- 断线后前端自动重连，重连成功后发送 `subscribe` 并拉取 `history`。

**订阅消息**

```json
{"type":"subscribe","thread_id":"t-123"}
```

**历史消息**

- 服务端收到订阅后，读取 pi session 文件 + 服务端持久化历史，返回：
  ```json
  {"type":"history","thread_id":"t-123","messages":[{"role":"user","content":"..."}, {"role":"assistant","content":"..."}]}
  ```

### 3.2 服务端事件流

`JWAgent` 每线程持有一个 `PiClient` 实例，事件处理流程：

1. 收到 `prompt`。
2. 启动/复用 pi 进程，并启动 `tool_server`（如果 extension 已启用）。
3. 注册事件监听器：
   - `text_delta` → `emitter.text()` → WebSocket `event/text`。
   - `toolcall_end` → `emitter.tool_call()` → WebSocket `event/tool_call`。
   - `tool_execution_end` → `emitter.tool_result()` → WebSocket `event/tool_result`。
   - `message_end` 含 usage → `emitter.usage_stats()`。
   - `agent_end` → `emitter.done()`。
   - `extension_ui_request` → 映射为 `interrupt` / `ask_user`。
4. 发送 `prompt` 到 pi stdin。
5. 循环从事件队列取事件并推送，直到 `agent_end` / `error` / timeout。

### 3.3 客户端数据层

**移除 LangGraph SDK**

- `jw-webui` 不再依赖 `@langchain/langgraph-sdk` 的 `useStream`。
- 自建 `WebSocketProvider` + `useChat` hook：
  - 维护 `messages`、`isLoading`、`interrupt`、`activeToolCalls`。
  - 发送消息时追加 optimistic user message。
  - 接收事件并更新消息列表（文本追加、tool call 插入、tool result 更新）。

**useChat 核心状态**

```ts
interface ChatState {
  messages: Message[];
  isLoading: boolean;
  interrupt?: InterruptData;
  activeToolCalls: ToolCall[];
  threadId: string | null;
}
```

### 3.4 实时活动面板：放到右侧 Inspector Tab

**当前问题**

- JW-WebUI 的 `RealtimeActivityPanel` 是一个独立侧边栏，遮挡了主聊天区。

**改造方案**

- 把实时活动面板合并到右侧 `InspectorPanel` 作为一个 Tab：
  - Tab 1: **Workspace**（文件浏览器）
  - Tab 2: **Agents**（异步子智能体）
  - Tab 3: **Activity**（实时活动） ← 新增
- 默认选中 `Workspace`；当 AI 开始干活时自动高亮 `Activity` tab 或显示徽标。
- `RealtimeActivityPanel` 组件本身保留，但从左侧独立面板改为在 Inspector 内部渲染。

**Activity Tab 内容**

- 当前主 agent 状态（思考中 / 等待审批 / 空闲）。
- 正在执行的工具调用列表。
- 正在运行的子 agent / 后台任务。
- 进行中的 todo 列表（来自 `todos` 状态）。

### 3.5 组件改造清单

| 组件 | 改造点 |
|------|--------|
| `InspectorPanel` | 增加 `activity` tab，集成 `RealtimeActivityPanel` |
| `RealtimeActivityPanel` | 移除独立侧边栏样式，适配 tab 内布局 |
| `RealtimeActivityBridge` | 从 `ChatContext` 读取状态，写入 `RealtimeActivityProvider`；保持不变 |
| `ChatProvider` / `useChat` | 改为 WebSocket 驱动 |
| `ConfigDialog` | 配置项改为 WebSocket 后端地址、模型、API key |
| `page.tsx` | 移除 LangGraph `ClientProvider` 相关代码，接入 `WebSocketProvider` |
| `MemoryPanel` / `ScheduledTasksPanel` / `SkillsMarketplace` | 调用 REST API `/api/memory/*`、`/api/schedule/*`、`/api/skills/*` |

---

## 4. 部署与配置

### 4.1 启动方式

**开发模式**

```bash
# 1. 后端
cd jw-agent
uv sync
uv run jw-agent serve --port 8000 --workdir ./workspace

# 2. 前端
cd jw-webui
npm install
npm run dev
```

**生产模式**

```bash
cd jw-agent
uv run jw-agent serve --host 0.0.0.0 --port 8000

cd jw-webui
npm run build
npm start
```

### 4.2 配置项（jw-agent）

| 配置名 | 默认值 | 说明 |
|--------|--------|------|
| `data_dir` | `~/.jw` | 持久化数据根目录 |
| `workspace_dir` | `./workspace` | 当前工作区 |
| `provider` | `"dashscope"` | LLM provider |
| `model` | `"qwen-plus"` | 默认模型 |
| `pi_bin` | `"pi"` | pi 可执行文件路径 |
| `pi_args` | `""` | 额外 pi CLI 参数 |
| `dangerous_mode` | `false` | 是否关闭沙箱路径限制 |
| `sandbox_timeout` | `300` | Shell 命令超时 |
| `require_approval` | `true` | 是否启用工具审批门 |
| `api_host` | `"0.0.0.0"` | 后端监听地址 |
| `api_port` | `8000 | 后端端口 |

### 4.3 API key 环境变量

| 环境变量 | 对应 provider |
|----------|---------------|
| `DASHSCOPE_API_KEY` | dashscope |
| `OPENAI_API_KEY` | openai |
| `ANTHROPIC_API_KEY` | anthropic |
| `GEMINI_API_KEY` | google |
| `DEEPSEEK_API_KEY` | deepseek |

配置加载优先级：环境变量 > `~/.jw/config.yaml` > 默认值。

### 4.4 pi 模型配置

pi 通过 `~/.pi/agent/models.json` 读取 provider/model 映射。启动脚本需要确保：

```bash
export DASHSCOPE_API_KEY=...
pi --mode rpc --provider dashscope --model qwen-plus ...
```

模型切换通过 pi 的 `/model` 命令或重启 pi 进程实现。

### 4.5 数据目录结构

```
~/.jw/
├── config.yaml
├── pi-sessions/            # pi session 文件
├── memories/
│   ├── observations/
│   │   ├── global/
│   │   └── projects/
│   └── profile/
├── sockets/                # tool_server Unix sockets
└── logs/
```

---

## 5. 风险与后续步骤

### 5.1 主要风险

1. **pi extension API 未充分文档化**：需要阅读 pi 源码确认 `registerTool`、`onToolCall` 等接口。
2. **WebSocket 与 LangGraph 行为差异**：前端历史恢复、interrupt 恢复需要重新实现。
3. **pi 的 session 恢复**：依赖 pi 的 `--session-id` / session 文件，需要验证跨进程恢复稳定性。
4. **工具 Schema 对齐**：pi 内置工具的参数名（如 `read` 的 `path` vs `file_path`）需要适配。

### 5.2 实现阶段

1. 搭建 `jw-agent` 骨架：FastAPI + WebSocket + pi 子进程启动。
2. 迁移并简化 `PiClient` / `PiProcessManager` / `PiSessionReader`。
3. 实现 `JWSandbox` 与 `tool_bridge`。
4. 接入记忆、调度、skills 工具。
5. 编写 pi extension + tool_server。
6. 实现事件翻译与 WebSocket 推送。
7. 改造 `jw-webui`：WebSocket 数据层 + Inspector Activity tab + 品牌替换。
8. 集成测试：端到端跑通一条 prompt。
9. 优化审批门、错误处理、断线重连。

---

## 6. 关键决策回顾

| 决策项 | 选择 |
|--------|------|
| 重写范围 | B — 后端 + WebUI 一起重写 |
| 保留模块 | `rpc.py` / `process.py` / `session.py` / `tracing.py` 核心逻辑 |
| 重写模块 | `graph.py` → `JWAgent`；`translator.py` → 金乌事件；`tools.py/tool_server.py/extension.js` → 金乌工具层；配置层 |
| 工具层 | D — 全部保留：文件/Shell + 记忆 + 调度 + skills |
| 前后端协议 | B — WebSocket |
| 后端栈 | A — Python FastAPI + WebSocket + pi 子进程 |
| WebUI 方案 | 从 `JW-WebUI` fork 改造，保留组件结构，替换数据层与品牌 |
