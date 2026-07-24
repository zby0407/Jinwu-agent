# 金乌后端 Agent 替换为 pi Agent 设计文档

## 目标

保留 JW 现有基础设施（HTTP API、线程持久化、记忆系统、skills、调度器、金乌 WebUI），将负责 LLM 推理与代码执行的核心 agent loop 从 LangGraph/deepagents 替换为 [pi Agent](https://pi.dev/)（Mario Zechner 的 Node.js coding agent harness），以提升代码/工具执行质量。

## 非目标

- 不复刻 pi 的 TUI；金乌 WebUI 继续作为唯一浏览器界面。
- 不立即重写记忆、skills、调度器；这些由 Python 侧保留并暴露给 pi。
- 不追求 pi 与 LangGraph 功能 1:1 对齐；允许部分行为差异，并在 WebUI 中以合适方式呈现。

## 关键约束

- JW 后端是 Python（LangGraph SDK 服务）。
- pi Agent 是 Node.js 终端 agent harness，自带 read / bash / edit / write 工具。
- 金乌 WebUI 通过 LangGraph SDK 与后端通信，消费 SSE 流事件（`updates`、`messages`、`interrupts`）。
- pi 提供 `--mode rpc` 通过 stdin/stdout 进行 JSON 协议交互，适合被非 Node 进程嵌入。

## 方案选择

采用 **方案 A：pi RPC Bridge**。

方案 B（双 agent 共存）作为未来可选的降级/对比路径保留，但不在本阶段实现。方案 C（Python 重写）因不能真正利用 pi 而排除。

## 高层架构

```
┌─────────────────┐      LangGraph SDK      ┌─────────────────────────────┐
│   金乌 WebUI    │  ─────────────────────► │   JW Python API   │
│  (Next.js)      │                         │  (threads, memory, skills,  │
└─────────────────┘                         │   scheduler, config)        │
                                            └──────────────┬──────────────┘
                                                           │
                                                           │ spawns / RPC
                                                           ▼
                                            ┌─────────────────────────────┐
                                            │   pi Agent (Node.js)        │
                                            │  --mode rpc                 │
                                            │  reasoning + file tools     │
                                            └─────────────────────────────┘
```

Python 侧新增 `PiAgentBridge` 模块簇，向上暴露与现有 LangGraph `CompiledStateGraph` 兼容的接口，向下通过 RPC 驱动 pi。

## 核心模块

### 1. `PiProcessManager`

负责 pi 子进程的生命周期：
- 根据 config 构造启动命令：`pi --mode rpc --session-dir <dir> --provider <p> --model <m> --system-prompt <file> --extension <ext>`。
- 在 LangGraph thread 创建/恢复时启动或复用 pi 进程。
- 进程崩溃时自动重启，并基于 `session_id` 恢复会话。
- 终止时清理子进程。

### 2. `PiRPCClient`

通过 stdin/stdout 与 pi 进行 JSON-RPC 风格通信：
- 发送 `message` 请求（用户消息）。
- 监听并解析 pi 的事件流：text、tool_call、tool_result、thinking、interrupt、error、done。
- 处理请求-响应的 id 关联，为 tool results 提供回调入口。

**注**：pi 的 RPC 协议细节需要参考 pi 源码或文档确认；若文档不足，需通过实际运行 `pi --mode rpc` 并观察输出进行逆向。

### 3. `PiToolAdapter`

将 pi 的 tool call 映射到 JW 的能力：

| pi 工具意图 | 映射到 Python 侧能力 |
|-------------|---------------------|
| read / edit / write / bash | Python `FilesystemBackend` / `LocalShellBackend`（保留现有沙箱与路径限制） |
| skill invocation（自定义） | 调用 `JW.skills` 加载器，执行对应 skill 的 tool set |
| memory read/write（自定义） | 调用 `JW.memory` 模块读取/写入记忆 |
| schedule（自定义） | 调用 `JW.scheduler` 创建 cron 任务 |

自定义工具通过 pi 的 `--extension` 机制注入：编写 TypeScript extension，在 pi 启动时注册 `skill`、`memory`、`schedule` 工具，这些工具在收到调用时通过 RPC 回传到 Python 执行。

### 4. `PiEventTranslator`

把 pi 事件翻译成 LangGraph 兼容的内部表示，供 WebUI 消费：
- `text` / `thinking` → AI `message` chunk（增量更新）。
- `tool_call` → `message` with `tool_calls`。
- `tool_result` → `tool` message。
- `interrupt`（权限/审批） → LangGraph `interrupt` 对象；WebUI 弹出审批/输入卡片。
- `error` → stream error toast。
- `done` → 结束当前 turn。

### 5. `PiAgentGraph`

对外暴露 LangGraph 风格的图接口：
- `astream(input, config)`：启动/恢复线程，发送消息，返回异步事件流。
- `ainvoke(...)` / `aget_state(...)`：用于状态恢复和线程历史。
- 内部持有 `PiProcessManager`、`PiRPCClient`、`PiToolAdapter`、`PiEventTranslator`。

## 数据流

1. WebUI 通过 LangGraph SDK 发送 `messages: [human]` 到 Python 线程端点。
2. `PiAgentGraph` 根据 `thread_id` 找到或创建对应 pi `session_id`。
3. `PiRPCClient` 将消息写入 pi stdin。
4. pi 推理并输出 tool call 事件。
5. `PiEventTranslator` 先下发 `tool_call` 消息到前端（显示工具调用）。
6. `PiToolAdapter` 执行工具，返回结果。
7. `PiRPCClient` 将结果写回 pi stdin。
8. pi 继续推理，最终输出 assistant text。
9. 所有事件经翻译后流入 WebUI。

## 线程与状态映射

- 每个 LangGraph `thread_id` 对应一个 pi session 文件（`session_id = thread_id`）。
- Python 维护最小状态：当前 pi session 路径、已发送的消息列表（用于前端历史同步）、未完成的 tool call 队列。
- 重新打开已有线程时，pi 通过 `--resume` 或 `--session-id` 加载历史会话；Python 同时从 LangGraph thread store 拉取持久化消息作为兜底。

## 中断与审批

- pi 默认对文件修改等操作可能直接执行。为了与现有 WebUI 的 human-in-the-loop 对齐，通过 pi extension 在关键工具（写文件、危险 shell、skill 执行）前插入审批门。
- 审批请求经 `PiEventTranslator` 转成 LangGraph interrupt，WebUI 渲染审批卡片。
- 用户决策后，Python 将结果写回 pi，恢复执行。

## 配置集成

- `jw config` 中的 `model` / `provider` / `api_key` 透传给 pi 的 `--model` / `--provider` / 环境变量。
- 新增配置项 `agent_engine = "pi" | "langgraph"`，允许回退到原 agent。
- pi 的扩展、skills、prompt templates 路径可在 config 中指定。

## 错误处理

| 场景 | 处理 |
|------|------|
| pi 进程崩溃 | `PiProcessManager` 重启并尝试恢复同 session；失败则向前端报错 |
| RPC 解析失败 | 记录原始行，返回 generic error event |
| 工具执行失败 | 将错误信息作为 tool_result 返回给 pi，让 pi 自行决定重试或报错 |
| 前端断开 | Python 继续执行当前 turn，结果在重连后从历史中恢复 |

## 测试策略

- 单元测试：`PiEventTranslator` 的事件映射、`PiToolAdapter` 的工具分发。
- 集成测试：启动 `PiAgentGraph`，发送一条消息，验证能收到 assistant 回复事件。
- 端到端测试：金乌 WebUI → Python API → pi → 工具执行 → 前端显示。

## 风险与依赖

1. **pi RPC 协议未充分文档化**：需要阅读 pi 源码或实际探测输出格式。
2. **pi 的 session 文件格式**：需要确认能否稳定通过 `session_id` 恢复。
3. **工具schema差异**：pi 的 read/bash/edit/write 与 JW backend 的工具签名可能不同，需要适配层。
4. **模型切换**：pi 支持 `/model`，需要把 JW 的 `/model` 命令映射到 pi 的模型切换。
5. **性能**：子进程 RPC 相比纯 Python 图有额外 I/O 开销，需要评估。

## 实施阶段

1. **Spike**：用最小脚本验证 `pi --mode rpc` 的输入输出协议。
2. **MVP Bridge**：实现 `PiProcessManager` + `PiRPCClient`，让 pi 处理单轮对话并返回文本。
3. **Tool Bridge**：接入 read/bash/edit/write 到 Python backend。
4. **Custom Tools**：接入 skills、memory、schedule。
5. **Interrupts**：实现审批门。
6. **集成**：替换 `agent.py` 中的默认图，加 `agent_engine` 开关。
7. **回归测试**：确保金乌 WebUI、记忆、skills、调度器正常工作。

- **MVP Bridge 已实现**：见 `docs/superpowers/plans/2026-07-12-pi-agent-bridge-mvp.md`。

## 不解决的问题（本阶段）

- pi 的 TUI 模式或交互式 session 不在浏览器中使用。
- pi 的 package/extension 商店与 JWSkills 的合并；本阶段通过显式 extension 路径加载。
- 自动把 pi 的树状 session 历史完整映射到 LangGraph 的线性 message list（先做最近路径）。
