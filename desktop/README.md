# 金乌桌面外壳（desktop/）

金乌（JW）是 Python Agent + LangGraph 后端（默认 `127.0.0.1:6174`）加 Next.js standalone WebUI（默认 `127.0.0.1:4716`）的科研智能体。本目录提供 Electron 桌面外壳，让用户**零 Python / Node 依赖**启动金乌：

- Electron 主进程是唯一进程监管者，直接 spawn 随包 Python 与随包 Node，不调用系统 `python` / `node` / `npm` / `uv`，也不复用 `jw --ui webui` 的双进程启动器。
- 配置检查前即显示窗口；后端与前端并行启动，前端可访问即加载 WebUI。后端 API 就绪后加载历史，主智能体后台预热完成后允许发送。
- 退出时按进程组 / 进程树清理，不残留 python / node / langgraph 进程。

初版仅支持 **macOS Apple Silicon（darwin/arm64）** 与 **Windows x64（win32/x64）**，产物**未签名、未公证**。

首次启动会显示配置向导，收集模型供应商、模型和 API Key（写入本机 `config.yaml`，权限 `0600`），
保存后自动启动后端与 WebUI。已有配置或环境变量凭据时会跳过向导。

0.3.3 起可通过「文件 → API 与连接设置」重新配置，失败页也提供入口。
DashScope 支持跟随系统代理或直连；存在工作区 `.env` 或环境变量覆盖时显示来源。
测试连接须主动点击，最多发送一次最小请求；保存时有运行或排队任务会延后应用。
详见 [0.3.3 升级与验证说明](RELEASE-0.3.3.md)。

## 目录结构

```text
desktop/
├── electron/
│   ├── main.mjs                # 主进程：唯一进程监管者、窗口、退出清理、首启向导
│   ├── menu.mjs                # 应用菜单（纯模板，便于测试）
│   ├── preload/
│   │   └── setup-preload.cjs   # 仅首启配置页可用的受限 IPC 桥
│   ├── lib/
│   │   ├── runtime-manifest.mjs  # runtime-manifest.json 定位/校验/相对路径解析
│   │   ├── environment.mjs       # 子进程环境变量契约 + 密钥剥离
│   │   ├── setup.mjs             # 首启配置：Python JSON 命令、stdin 传密钥
│   │   ├── ports.mjs             # 默认端口与相邻可用端口选择
│   │   ├── health.mjs            # /ok 与前端根路径健康检查
│   │   ├── process-tree.mjs      # spawn 选项、SIGTERM/SIGKILL、taskkill /T /F
│   │   ├── logs.mjs              # 日志路径与桌面日志写入
│   │   ├── paths.mjs             # userData / 工作区目录
│   │   ├── window-security.mjs   # 安全基线、外链策略、错误页操作链接
│   │   ├── error-page.mjs        # 无内联脚本的可读错误页
│   │   └── orphan-process.mjs    # 残留 langgraph dev 兜底清理（不误杀）
│   ├── services/supervisor.mjs # 子进程监管（崩溃检测、停止、tail 缓冲）
│   └── ui/
│       ├── loading.html        # 启动加载页与阶段计时
│       ├── setup.html          # 首启配置向导（CSP、无内联脚本）
│       ├── setup.css
│       └── setup.js
├── assets/                     # 金乌图标母版、icns/ico 与品牌资源
├── test/                       # Node 内置 node:test 单元测试
├── electron-builder.yml        # 打包配置（dmg/zip arm64、nsis x64）
├── package.json                # 依赖精确锁定 + 脚本
└── package-lock.json
```

## 运行时清单契约

打包团队产出的运行时位于 `<app resources>/runtime/`，清单文件为 `runtime/runtime-manifest.json`，**所有相对路径都相对于该清单所在目录**：

```json
{
  "schema_version": 1,
  "platform": "darwin | win32",
  "arch": "arm64 | x64",
  "python": "python/bin/python3 | python/python.exe",
  "node": "node/bin/node | node/node.exe",
  "langgraph": {
    "executable": "python/bin/python3 | python/python.exe",
    "args": ["-m", "langgraph_cli"]
  },
  "webui": "webui/dist/server.js",
  "projectRoot": "project",
  "pythonVersion": "...",
  "nodeVersion": "...",
  "jwVersion": "...",
  "webuiVersion": "..."
}
```

主进程会校验：`schema_version`、`platform` / `arch` 与宿主一致、路径字段必须是相对路径且不允许 `..` 越界；`pythonVersion` 等版本字段缺失时显示为「未知」，不会阻断启动。

清单定位顺序：

1. 环境变量 `JW_DESKTOP_RUNTIME_MANIFEST`（**开发调试用**；设置后不再回退，指向不存在的文件会直接报错）
2. 打包后：`<resources>/runtime/runtime-manifest.json`
3. 开发态：`<仓库根>/packaging/dist/runtime/runtime-manifest.json`

## 环境变量契约（由桌面外壳显式注入）

后端（随包 Python）与前端（随包 Node）都会收到：

| 变量 | 值 |
| --- | --- |
| `JW_PROJECT_ROOT` | `<runtime>/project` |
| `JW_RUNTIME_ROOT` | `<runtime>` |
| `JW_WEBUI_DIST` | `<runtime>/webui/dist/server.js` |
| `JW_NODE_BIN` | `<runtime>/node/bin/node` 或 `node/node.exe` |
| `JW_LANGGRAPH_COMMAND_JSON` | `JSON.stringify([<runtime langgraph.executable>, ...langgraph.args])` |
| `JW_DATA_DIR` | `<userData>/data` |
| `XDG_CONFIG_HOME` | `<userData>/config` |
| `JW_WORKSPACE_DIR` | `~/Documents/Jinwu`（Windows：`%USERPROFILE%\Documents\Jinwu`，启动时创建） |
| `JW_LANGGRAPH_DEV_PORT` | 选中的后端端口 |

前端额外收到 `PORT=<webuiPort>`、`HOSTNAME=127.0.0.1`、`NODE_ENV=production`，并且**剥离模型密钥**（变量名匹配 `*API_KEY*`、`*_KEY`、`*TOKEN`、`*SECRET`、`*PASSWORD`，大小写不敏感）。后端保留父进程中的配置密钥。桌面日志只记录 `JW_*` 变量名，不记录任何值。

> 兼容性说明：当前 `webui/src/lib/server/*` 与 `app/api/jw-config` 读取的是 `agent_LANGGRAPH_DEV_PORT`、`agent_DATA_DIR` 等遗留变量名。桌面外壳为前端额外注入 `agent_LANGGRAPH_DEV_PORT` / `agent_DATA_DIR` / `agent_WORKSPACE_DIR` 别名（不覆盖已有值），以保证 WebUI 在修正前能正确识别后端端口与数据目录。

## 用户数据与日志

| 平台 | 用户数据目录 |
| --- | --- |
| macOS | `~/Library/Application Support/Jinwu` |
| Windows | `%LOCALAPPDATA%\Jinwu` |

主进程在 `app.ready` 之前调用 `app.setName('Jinwu')` 与 `app.setPath('userData', …)`，并维护单实例锁；已有实例时聚焦其窗口。

日志（`<userData>/logs/`）：

- `backend.log`：`python -m jw deploy --workdir <workspace> --port <backendPort>` 的 stdout/stderr
- `webui.log`：`node <runtime>/webui/dist/server.js` 的 stdout/stderr
- `desktop.log`：桌面外壳自身的启动、端口、进程清理记录

帮助菜单与错误页都能直接打开日志目录。

## 端口

默认后端 `6174`、前端 `4716`。若被占用则各自向上扫描相邻可用端口（最多 50 个），**绝不杀掉占用者**；前端扫描会排除已选定的后端端口，保证两者不同。实际端口会写入 `desktop.log`，并通过 `--port` / `JW_LANGGRAPH_DEV_PORT` / `PORT` 传给子进程。

## 安全基线

- `contextIsolation: true`、`nodeIntegration: false`、`sandbox: true`，**无 preload**
- 禁止 `window.open` / 新窗口与 `webview`；外部 `https` 链接交给系统浏览器，`http`/`file` 一律拒绝
- 渲染进程权限请求全部拒绝；只访问 `127.0.0.1`
- 生产环境 `devTools: false`，菜单不提供开发者工具入口（仅 `electron .` 开发模式出现）
- 错误页不内嵌子进程原始日志（可能含配置密钥），只展示日志路径

## 构建前置条件（重要）

安装包**不包含**运行时；必须先由 packaging 脚本产出完整运行时，再打包桌面外壳：

```bash
# 1) 先由 packaging 脚本产出 WebUI standalone 与随包 Python/Node 运行时
#    产物必须包含仓库根 packaging/dist/runtime/runtime-manifest.json
#    （release-packaging 已在本机组装出实测运行时；CI 会现装后再打包）

# 2) 安装桌面外壳开发依赖（提交了 package-lock.json，推荐 npm ci 复现）
cd desktop
npm ci

# 3) 运行单元测试
npm test

# 4) 打包（本机无签名证书）
npm run dist:mac   # macOS arm64：Jinwu-0.3.2-mac-arm64.dmg / .zip
npm run dist:win   # Windows x64：Jinwu-0.3.2-win-x64.exe
```

- `extraResources` 会把仓库根 `packaging/dist/runtime` 复制到应用 `resources/runtime`；该目录在开发机上可能尚不存在，属于预期情况。
- 构建产物输出到 `desktop/dist/`（已在 `.gitignore` 中忽略，请勿提交）。
- 跨平台打包受限：macOS 安装包需在 macOS 上构建；Windows 安装包建议在 Windows 上构建（或使用 CI）。

### 依赖锁定与可复现安装

- `electron` 锁定精确版本 **45.0.0**、`electron-builder` 锁定精确版本 **26.18.0**（`package.json` 中无 `^`/`~`）。
- 仓库提交了 `desktop/package-lock.json`（lockfileVersion 3）；CI 使用 `npm ci` 可复现安装，lockfile 缺失时 CI 直接失败，不会退化为不可复现的 `npm install`。
- 锁定目录不含平台专属可选依赖，macOS / Windows 均可使用同一份 lockfile。

### 未签名说明

初版 `identity: null`、不公证：

- macOS 首次打开需右键 → 打开，或执行 `xattr -dr com.apple.quarantine /Applications/Jinwu.app`；
- Windows SmartScreen 会提示未知发布者，选择「仍要运行」。

`electron-builder.yml` 中已预留 `hardenedRuntime`、`entitlements`、`notarize` 注释配置，取得 Developer ID 证书后即可启用。

## 本地开发调试

没有完整运行时也可以验证外壳逻辑：

```bash
cd desktop
npm install
npm test                                  # 不需要 Electron / 真实 Agent
JW_DESKTOP_RUNTIME_MANIFEST=/path/to/runtime-manifest.json npm start
```

`npm start` 等价于 `electron .`，需要先安装 Electron（`npm install` 会下载对应平台二进制）。

开发态的默认清单路径是 `<仓库根>/packaging/dist/runtime/runtime-manifest.json`；也可用 `JW_DESKTOP_RUNTIME_MANIFEST` 指向手工准备的清单。

## 测试

```bash
cd desktop && npm test
```

使用 Node 内置 `node:test`，不启动真实 Electron / Agent / 网络服务（端口可用性测试只绑定本机回环地址）。覆盖：

- 清单解析、平台/架构校验、相对路径与越界防护、清单定位优先级
- 端口选择（默认、顺延、前后端不重复、扫描耗尽）
- 密钥环境变量剥离与前后端环境变量契约
- `JW_LANGGRAPH_COMMAND_JSON` 序列化
- 日志路径解析
- 进程树清理（SIGTERM/SIGKILL、taskkill 参数、宽限期、超时）
- 健康检查轮询/超时/进程提前退出/取消
- 窗口安全基线、外链与错误页操作链接、错误页转义
- 菜单结构（DevTools 仅 dev）、子进程监管崩溃回调与 tail 缓冲
- 残留后端兜底清理（端口监听者解析、lsof 可执行文件判定、langgraph 命令行判定、不误杀）
- 首启配置协议（stdin 传密钥、provider/模型归一化、错误不外泄密钥、禁止非配置页 IPC）

当前桌面外壳单测共 **120 项**，全部通过。

## 已知限制 / 风险

1. **未签名、未公证**：本机可产出并运行未签名 macOS `.dmg`/`.zip`；正式分发必须配置 Developer ID、Hardened Runtime 和 notarytool。Windows 安装包需在 Windows runner 或 CI 构建和验证，本机不能交叉构建。
2. **首启配置仅覆盖常用 provider**：向导支持 DashScope、OpenAI、Anthropic、Gemini、DeepSeek、SiliconFlow、OpenRouter、MiniMax、Moonshot、NVIDIA、自定义 OpenAI/Anthropic 兼容接口与 Ollama。其余 provider 仍可通过 `jw onboard` 配置，配置完成后桌面会识别并跳过向导。
3. **API Key 以本地配置文件保存**（权限 `0600`，不写入日志、不放入 argv）；尚未接入 macOS Keychain / Windows DPAPI。
4. `jw deploy` 会以 `start_new_session=True` 启动 langgraph dev，因此它不在桌面外壳的进程组内。桌面外壳先依赖 `jw deploy` 的 atexit 清理（宽限期 10s），再执行兜底 sweep：后端进程退出后若端口仍响应 `/ok`，则通过 `lsof`（macOS）确认监听者就是随包 Python，或通过 `ps` / PowerShell 确认命令行含本端口 langgraph，然后对整棵进程树发 SIGTERM/SIGKILL（Windows `taskkill /T /F`）。校验不通过时只告警、不误杀。建议 Python 端后续把 langgraph pid 文件写入 `JW_DATA_DIR` 并提供正式清理接口，替代该兜底。
5. 首次启动需要加载随包运行时，通常数十秒；加载页会持续显示，健康检查超时为后端 120s / 前端 90s。
6. 未实现自动更新（`publish: null`），后续若接入需先完成签名并配置更新源。
7. 若打包脚本额外引入常驻守护进程（如 MCP/ccproxy），需要同步更新退出清理逻辑。
