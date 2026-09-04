# 金乌（JW）

金乌是一个面向端到端科研工作的自主智能体。它把研究规划、资料检索、代码执行、数据分析、假设管理、实验编排、长期记忆和报告写作放在同一套工作流中，并提供 CLI、TUI 与浏览器 WebUI。

## 项目结构

```text
.
├── jw/                 # Python 主程序与智能体运行时
├── webui/              # Next.js WebUI（与后端共用本仓库）
├── tests/              # 后端测试
├── src/                # 独立科研工作流源码
├── research/           # 科研合同、规范、示例与开发资源
├── workspace/          # 数据、项目任务、运行产物与知识库导出
└── pi-mcp-bridge/      # Pi Agent MCP 桥接
```

`research/` 是版本化的只读资源层；所有可变状态统一写入
`workspace/`（可通过 `JW_WORKSPACE_DIR` 迁移到其他位置）。

项目根目录是唯一 Git 仓库，`webui/` 不包含独立的 Git 元数据。

## 一键安装

macOS 或 Linux：

```bash
curl -LsSf https://raw.githubusercontent.com/zby0407/Jinwu-agent/main/install.sh | sh
```

Windows PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/zby0407/Jinwu-agent/main/install.ps1 | iex"
```

安装脚本会在需要时自动安装 `uv` 和兼容的 Python，并从公开 GitHub
源码安装最新版 `jw`。如果机器已有 Node.js 20+ 和 npm，脚本也会自动构建 WebUI。
重复执行同一命令即可刷新安装。完成后打开一个新终端并运行：

```bash
jw onboard
jw
```

没有 Node.js 时仍可正常使用 CLI/TUI；安装 Node.js 20+ 后重新执行安装命令即可补齐 WebUI。

## 环境要求

- Python 3.11–3.13
- [uv](https://docs.astral.sh/uv/)
- Node.js 20+（仅 WebUI 开发或构建需要）

## 从源码快速开始

```bash
uv sync
uv run jw onboard
uv run jw
```

启动浏览器界面：

```bash
uv run jw --ui webui
```

配置默认保存在 `~/.config/jw/config.yaml`，运行数据保存在 `~/.jw/`。也可以复制 `.env.example` 并通过环境变量提供模型密钥。

## 开发

后端检查：

```bash
uv run ruff check --select F .
uv run ruff format --check jw tests
uv run pytest
```

WebUI：

```bash
cd webui
npm install
npm run dev
```

生产构建：

```bash
cd webui
npm run build
```

## Docker

```bash
docker compose build
docker compose run --rm jw
```

## 文档

- [H1/H2 一次性复现指南](./docs/guides/solar-h1-h2-reproduction.md)
- [第三方依赖许可证清单](./THIRD_PARTY_LICENSES.md)
- [贡献指南](./CONTRIBUTING.md)
- [项目文档](./docs/)
- [项目目录与数据位置](./docs/PROJECT_LAYOUT.md)
- [WebUI 说明](./webui/README.md)
- [中文说明](./README.zh-CN.md)

## License

JW 原创代码采用 Apache License 2.0，详见 [LICENSE](./LICENSE)。项目包含或改编了
其他开源项目的部分代码；相应版权归属和许可证全文见 [NOTICE](./NOTICE)。
