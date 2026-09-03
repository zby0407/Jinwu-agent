# H1/H2 一次性复现指南

本指南对应版本化复现套件 `solar-h1-h2-v1`。它会逐字发送固化的 H1、H2 提示词，并发创建两个互相隔离的 LangGraph thread/run。固定模型为 `dashscope/qwen3.7-max`，调用方不能替换提示词、模型或提供方。

> “提交成功”只证明两篇提示词进入了两个独立 run，不代表 LangGraph 最终成功、科研审查通过或实验结论成立。

## 1. 仓库与运行环境

- 仓库：<https://github.com/zby0407/Jinwu-agent>
- 许可证：Apache License 2.0
- 第三方依赖许可证：[THIRD_PARTY_LICENSES.md](../../THIRD_PARTY_LICENSES.md)
- 复现功能 Commit：合并发布后填写
- Python：3.11–3.13
- 包管理器：[uv](https://docs.astral.sh/uv/)
- 硬件：CPU-only 可运行，无需 GPU
- WebUI 开发或本地构建：Node.js 20+

安装项目：

```bash
git clone https://github.com/zby0407/Jinwu-agent.git
cd Jinwu-agent
uv sync --extra solar
```

配置必需的 DashScope 密钥；若实验需要联网检索，再配置可选的 Tavily 密钥：

```bash
# PowerShell
$env:DASHSCOPE_API_KEY="<your-key>"
$env:TAVILY_API_KEY="<optional-key>"
```

## 2. 注册权威输入数据

先将项目使用的 SILSO 与 MWO/WSO 数据登记到同一个工作区：

```bash
uv run python scripts/acquire_authoritative_solar_data.py --workspace <workspace> --project-id default
```

该步骤会保存来源、哈希和登记回执。H2 所需的极区前兆表未登记时，调度仍可完成，但后续科研流程应明确阻断或报告缺失，不得伪造输入。

## 3. 终端一键复现

推荐命令：

```bash
uv run jw reproduce --workdir <workspace>
```

命令会启动或复用完整 LangGraph 后端，提交两轮任务，并保持前台监测直到两轮进入 LangGraph 终态。按 `Ctrl+C` 只会停止当前监测；若后端由本命令启动，退出时也会停止该后端，因此应在 WebUI 中重新核对任务实际状态。

仅当同一工作区的外部后端已经运行时，才可提交后立即退出：

```bash
uv run jw reproduce --workdir <workspace> --detach
```

`--detach` 不会自行启动后端。已有后端缺少工作区标识或服务的是另一工作区时，命令会拒绝提交。

调度输出示例：

```text
批次：repro-00000000-0000-0000-0000-000000000000
调度状态：submitted
H1: threadId=<thread-h1> runId=<run-h1> workspace=<workspace-h1>
H2: threadId=<thread-h2> runId=<run-h2> workspace=<workspace-h2>
说明：提交成功仅证明两篇固定提示词进入独立 run，不代表实验或科研审查成功。
```

退出码 `0` 只表示 H1、H2 均已调度。单侧提交或审计写入失败会保留已经创建的任务并报告 `partial`，命令返回非零退出码。

## 4. WebUI 一键复现

启动集成界面：

```bash
uv run jw --ui webui
```

在左侧“最近记录”标题下点击“一键复现 H1/H2”。确认框会再次说明固定模型、并发启动、自动批准范围和可能产生的模型费用。提交期间按钮不可重复点击。完整或部分提交后，结果框会列出真实的 threadId/runId，并可分别打开 H1、H2；当前页面不会被强制切换。

浏览器入口只接受本机来源、固定意图请求头和固定请求体。后端启用 `dangerous_mode` 时，复现入口会拒绝执行。

## 5. 工作区、回执与检查

两个任务分别写入：

```text
<workspace>/projects/default/runs/run_<thread-prefix>_<hash>/task.json
<workspace>/projects/default/runs/run_<thread-prefix>_<hash>/receipts/reproduction_launch.json
```

批次回执写入：

```text
<workspace>/projects/default/shared/decisions/reproduction_batches/<batch-id>.json
```

回执记录完整提示词、SHA-256、时间、模型、threadId、runId、隔离工作区、声明输入和预期产物。固化提示词哈希为：

- H1：`e8278c7cd2d98bbca2254961458ecfaa099616681ff6b364c1fc3881d804db4d`
- H2：`909c12a5f8b827fb0f0f12cf714a16eae06b8247e610bcb10cc945f5421abfba`

典型运行可能需要约 30–90 分钟，具体取决于模型服务、网络、检索和审查重试，不能作为完成时限保证。每一轮结束后必须同时检查：

1. 聊天最后的终止文字是否明确完成、失败或阻断；
2. 科研审查的最终状态及其限制条件；
3. 回执引用的 CSV、报告、图和输入文件是否真实存在且哈希一致。

LangGraph `success`、数据回执或局部工具结果都不能单独证明科研成功。既有 `outputs/cycle_morphology/` 与 `outputs/sc26_direct_test/` 内容仅作复跑和比较基线，不得声明为本轮 Agent 新复现结果。

## 6. 安全与可审计边界

复现图使用受限自动模式：允许的工具无需逐次人工批准，`ask_user` 被禁用；工作区隔离、危险命令拦截、工具边界和科研审查仍然生效。接口不接受自定义提示词或模型。任何 `partial` 或 `failed` 回执都应原样保留，不删除已启动 run，也不把部分结果包装成完整成功。
