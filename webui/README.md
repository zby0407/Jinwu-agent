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

## 今日太阳

工作台简报和太阳活动页共享工作区下的 `today-sun/snapshot.json`。
从仓库根目录运行独立更新命令：

```bash
python scripts/refresh_today_sun.py
```

指定了其他运行工作区时，将同一个 `JW_WORKSPACE_DIR` 传给 WebUI 和更新命令，
或为更新命令指定 `--workspace`。命令按北京时间每天最多发布一次快照，
从 NASA SDO 和 NOAA SWPC 读取数据。更新失败时保留已有快照。
系统启动和页面访问仅读缓存。每日更新可以由系统调度器在固定时间独立执行。
每日快照还包含 SIDC 的 CME 目录及 NSO/GONG 太阳背面观测。缺失的产品保留
上一版与原缓存时间，同日重试仅补齐当日未更新的产品。

「太阳活动周」读取独立的历史档案 `today-sun/cycle.json`，收录 WDC-SILSO 黑子数与周期
极值及 NOAA 月均 F10.7。按年或需要时独立更新：

```bash
python scripts/refresh_solar_cycle.py
```

此命令支持同一 `--workspace` 参数。日常快照更新和 WebUI 启动不会替换历史档案。

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
