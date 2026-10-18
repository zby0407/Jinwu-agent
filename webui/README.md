# 金乌 WebUI

金乌 WebUI 是 JW 的浏览器工作台，提供流式对话、模型切换、工具审批、子智能体状态、工作区文件、知识库、长期记忆和定时任务等界面。

WebUI 是主项目的一部分，位于统一仓库的 `webui/` 目录，不使用独立 Git 仓库。

## 本地开发

```bash
cd webui
npm install
npm run dev
```

默认地址为 <http://localhost:4716>。后端默认连接 `http://127.0.0.1:6174`，也可以在界面配置中修改部署地址。

浏览器回归测试使用模拟后端，不调用模型或真实工作区：

```bash
npx playwright install chromium
npm run test:ui
```

测试默认使用本机 4717 端口。Windows 已安装 Edge 时，也可在 PowerShell 设置
`$env:PLAYWRIGHT_CHANNEL='msedge'` 后运行，省去 Chromium 安装。

## 生产构建

```bash
npm run lint
npm run build
npm run start:dist
```

也可以从项目根目录直接启动集成界面：

```bash
uv run jw --ui webui
```

## H1/H2 一次性复现

左侧“最近记录”标题下方的“一键复现 H1/H2”按钮会先显示费用与安全边界确认，再并发提交两个固定提示词任务。提交成功仅表示两个独立 run 已创建，不代表实验或科研审查成功。

环境准备、终端命令、工作区和回执检查详见 [H1/H2 一次性复现指南](../docs/guides/solar-h1-h2-reproduction.md)。

## 配置

- `NEXT_PUBLIC_LANGSMITH_API_KEY`：连接需要认证的 LangGraph 部署时使用。
- 浏览器本地配置键为 `jw-config`。
- 主图 ID 固定为 `JW`。

Skills 页面读取 JW 源码中的 `jw/subagents/skill_registry.json` 和随包技能。
`JW_WORKSPACE_DIR` 可以指向独立的研究运行目录，无需复制技能文件进去。
从源码的 `webui/` 或 `webui/dist/` 启动时会自动查找源码根目录；独立安装的
WebUI 可通过 `JW_SKILLS_PROJECT_ROOT` 显式指定包含 `jw/subagents/` 的 JW 源码根目录。

## License

JW WebUI 原创代码采用 Apache License 2.0，详见 [LICENSE](./LICENSE)。第三方代码的
版权归属和许可证全文见 [NOTICE](./NOTICE)。
