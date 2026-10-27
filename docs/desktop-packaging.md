# JW 桌面封装（Desktop Packaging）

本文档说明 JW（金乌）初版桌面封装的可复现构建方式、运行时契约、CI 发布流程和已知限制。
适用对象：发布/打包负责人、Electron 外壳开发者、需要本地出安装包的维护者。

- 运行时组装脚本：`packaging/build_runtime.py`
- 运行时自检脚本：`packaging/smoke_test.py`（无网络、无 API Key）
- 共享契约与检查逻辑：`packaging/runtime_contract.py`
- CI 流水线：`.github/workflows/desktop-release.yml`
- Electron 外壳：`desktop/`（由桌面外壳负责人实现，本仓库的打包脚本不生成 Electron 代码）

> 当前状态：**初版仅支持 macOS Apple Silicon（arm64）与 Windows x64，产物未签名、未公证。**
> 本文档不声称任何签名/公证已经完成。

## 1. 支持矩阵

| 平台 | 架构 | 安装包 | 最低系统版本 | 状态 |
| --- | --- | --- | --- | --- |
| macOS | arm64（Apple Silicon） | `.dmg`（+ 更新用 `.zip`） | macOS 13 Ventura 及以上 | 支持 |
| Windows | x64 | NSIS `.exe` 安装包 | Windows 10 21H2 / Windows 11 | 支持 |
| macOS | x64（Intel） | — | — | 暂不支持（后续扩展） |
| Windows | arm64 | — | — | 暂不支持（后续扩展） |
| Linux | — | — | — | 不支持（不在初版范围） |

约束：

- 构建必须发生在**对应平台的原生 runner** 上（macOS 用 arm64 runner，Windows 用 x64 runner）。
  脚本会执行目标 Python/Node 二进制来验证运行时，因此明确拒绝交叉构建，也**不使用 Linux 交叉打桌面包**。
- 运行时由打包脚本从源码/锁文件现装，**不在用户机器上安装任何 pip 包**，也不依赖用户已装的 Python/Node。

## 2. 运行时布局与 manifest 契约

`packaging/build_runtime.py` 的默认输出是 `packaging/dist/runtime/`。electron-builder 会把该目录整体复制为
应用内的 `resources/runtime`，Electron 主进程只通过 `runtime-manifest.json` 发现其余组件。

```
runtime/
  python/                  # 可搬迁 CPython 3.12 + 锁定依赖 + JW wheel
  node/                    # 固定 Node.js LTS 发行包（自带，不依赖 runner/用户 Node）
  webui/dist/              # Next.js standalone 产物（server.js + traced node_modules + .next）
  project/research/        # 白名单项目资源
  project/src/
  licenses/                # LICENSE / NOTICE / THIRD_PARTY_LICENSES.md / node-LICENSE.txt
  runtime-manifest.json
```

`runtime-manifest.json`（`schema_version: 1`，所有路径均相对于 runtime 根：

```json
{
  "schema_version": 1,
  "platform": "darwin",
  "arch": "arm64",
  "python": "python/bin/python3",
  "node": "node/bin/node",
  "langgraph": {
    "executable": "python/bin/python3",
    "args": ["-m", "langgraph_cli"]
  },
  "webui": "webui/dist/server.js",
  "projectRoot": "project",
  "pythonVersion": "3.12.12",
  "nodeVersion": "22.22.3",
  "jwVersion": "0.2.2",
  "webuiVersion": "0.1.8"
}
```

Windows 对应值为 `python/python.exe`、`node/node.exe`。字段说明：

| 字段 | 含义 |
| --- | --- |
| `python` | 私有解释器（直接执行脚本/模块，**不要**依赖 `bin/jw` 的 shebang 绝对路径） |
| `node` | 私有 Node.js 可执行文件 |
| `langgraph.executable` + `args` | 后端启动命令：`<python> -m langgraph_cli`（与 `python` 字段一致，不使用 wrapper） |
| `webui` | WebUI standalone 入口：`node webui/dist/server.js` |
| `projectRoot` | 项目资源根目录（`project`），对应 Agent 的 `JW_PROJECT_ROOT` |
| `pythonVersion` / `nodeVersion` | 从最终路径实际探测出的版本（非硬编码） |
| `jwVersion` / `webuiVersion` | 实际安装的 JW wheel 版本与 WebUI standalone 的 `package.json` 版本 |

版本值由脚本从实际环境中探测（`platform.python_version()`、`node --version`、
`importlib.metadata.version("jw-agent")`、`webui/dist/package.json`），不写死。

## 3. 本地构建（macOS arm64）

前置条件：

- macOS 13+、Apple Silicon
- Python 3.11+（脚本自身运行需要）
- [`uv`](https://docs.astral.sh/uv/)（安装私有 CPython 并构建 wheel）
- Node.js 22 + npm（仅用于构建 WebUI 与 Electron 外壳）
- 可访问 `github.com`（uv 下载 CPython）、`pypi.org`、`nodejs.org`、npm registry

```bash
# 1) 组装运行时（私有 Python + Node + JW wheel + WebUI + project 资源 + manifest）
python packaging/build_runtime.py --clean

# 2) 运行时自检（不启动 Agent、不需要网络和 API Key）
python packaging/smoke_test.py

# 3) Electron 外壳安装包（由 desktop/ 提供；本仓库不生成 Electron 代码）
cd desktop
npm ci
npm test
npm run dist:mac         # 未签名 .dmg / 更新用 .zip
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--clean` | 先删除已有 `packaging/dist/runtime` 再重建（不加则拒绝覆盖） |
| `--out PATH` | 输出目录，默认 `packaging/dist/runtime` |
| `--node-version 22.22.3` | 指定随包 Node.js 版本（默认 `22.22.3`，需在 nodejs.org 存在） |
| `--skip-webui` | 不执行 `npm ci/build`，复用已有的 `webui/dist`（standalone 必须已生成） |
| `--webui-dist PATH` | 复用指定目录的 Next.js standalone 产物 |
| `--include-shared-project` | 额外打包 `projects/default/shared`（可能很大，默认不打包） |
| `--work-dir` / `--cache-dir` | 临时目录/下载缓存目录；也可用 `JW_PACKAGING_WORK_DIR`、`JW_BUILD_CACHE_DIR`、`UV_CACHE_DIR`、`UV_PYTHON_INSTALL_DIR`、`npm_config_cache` 控制 |
| `--keep-work` | 保留临时目录便于排查 |

Windows x64 上把第 3 步换成 `npm run dist:win`，其余相同（在 `windows-latest` 或本机 Windows 上执行）。

### 3.1 运行时组装做了什么

1. **私有 Python**：`uv python install 3.12 --install-dir <可控目录>` 取得可搬迁 CPython 3.12，
   整体复制到 `runtime/python` 并移除 python-build-standalone 的 `EXTERNALLY-MANAGED` 标记
   （该 runtime 本身就是私有环境）。
2. **锁定依赖**：`uv build --wheel` 构建 JW wheel；`uv export --frozen --no-dev --no-emit-project`
   从 `uv.lock` 导出精确版本（含哈希），用 `uv pip install --require-hashes` 装进私有 Python；
   JW wheel 用 `--no-deps` 安装，避免二次解析依赖。
3. **私有 Node.js**：从 nodejs.org 下载 `<node-vX-darwin-arm64.tar.gz>` 或 `<node-vX-win-x64.zip>`，
   先校验官方 `SHASUMS256.txt` 中的 SHA-256，再解压到 `runtime/node`。
   Windows 使用标准库 `zipfile` 解压，不引入额外依赖。
   默认版本 `22.22.3` 依据：本机已安装的官方 Node.js 发行包 `node --version` 输出 `v22.22.3`；
   CI 首次运行会实际下载并校验官方 `SHASUMS256.txt`。若该版本在 nodejs.org 不存在，
   脚本会明确报错（不会静默回退到未校验的小版本）。
4. **WebUI**：在 `webui/` 执行 `npm ci` 与 `npm run build`（Next.js `output: "standalone"`），
   把 `webui/dist` 复制到 `runtime/webui/dist`；**不复制** `webui/node_modules` 源目录，
   standalone 内已包含 traced 依赖。
5. **project 资源**：白名单 `research/`、`src/`；排除 `.env`、`.git`、`.venv*`、workspace、output(s)、
   tests、tmp、数据库、密钥、日志、缓存等。
6. **licenses**：复制仓库根部存在的 `LICENSE`、`NOTICE`、`THIRD_PARTY_LICENSES.md`，
   并附带 Node.js 发行包自带的 `LICENSE`。
7. **manifest + 自检**：写入 `runtime-manifest.json` 后，从**最终路径**逐项检查：
   路径存在、可执行、`<python> -c "import jw, langgraph_cli"`、`<python> -m jw --help`、
   `sys.prefix` 指向 runtime 内部、Node 版本、WebUI `server.js`。任一失败则删除半成品 runtime 并以非 0 退出。

### 3.2 无网络/离线环境（air-gapped）

脚本为本地验证和离线打包预留了逃生参数（CI 不使用）：

```bash
python packaging/build_runtime.py --clean \
  --python-dir /path/to/cpython-3.12.x-darwin-arm64 \
  --node-dist /path/to/node-v22.22.3-darwin-arm64 \
  --jw-wheel /path/to/jw_agent-0.2.2-py3-none-any.whl \
  --deps-site-packages /path/to/site-packages \
  --webui-dist webui/dist
```

## 4. 产物与体积

运行时目录（macOS arm64，实测）：

| 组件 | 体积（近似） |
| --- | --- |
| `python/`（含 scipy/pandas/sklearn 等科学栈） | ~615 MiB |
| `node/`（完整 Node.js 发行包） | ~188 MiB |
| `webui/`（Next.js standalone） | ~51 MiB |
| `project/`（research + src） | ~2.4 MiB |
| `licenses/` | <1 MiB |
| **合计** | **~790 MiB（逻辑大小；磁盘占用约 857 MiB）** |

安装包体积取决于 electron-builder 的压缩与 Electron 运行时（约 100–150 MiB），
预计 `.dmg` / `.exe` 在 **330–450 MiB** 量级；该数字为估算，需在首个真实安装包构建后实测更新。

安装包输出（由 `desktop/` 的 electron-builder 配置决定，CI 同时兼容 `desktop/release/` 与 `desktop/dist/`）：

- macOS：`*.dmg`、更新用 `*.zip`、`*.blockmap`、`latest-mac.yml`
- Windows：NSIS `*.exe`、`*.blockmap`、`latest.yml`
- 校验：`SHA256SUMS.txt`（由 CI 汇总所有上传产物生成）

按当前 `desktop/electron-builder.yml` 的命名规则，预期产物为
`Jinwu-<version>-mac-arm64.dmg` / `Jinwu-<version>-mac-arm64.zip` 与
`Jinwu-<version>-win-x64.exe`（实际文件名以 electron-builder 输出为准）。

## 5. 未签名说明（初版）

初版安装包**不签名、不公证**：

- macOS：未使用 Developer ID 签名、未 notarize/staple，Gatekeeper 会提示"无法验证开发者"，
  需要用户右键打开或在"系统设置 → 隐私与安全性"中允许。
- Windows：未做 Authenticode 签名，SmartScreen 可能提示"未知发布者"。

CI 显式设置 `CSC_IDENTITY_AUTO_DISCOVERY=false`，避免 electron-builder 在无证书的 runner 上
尝试自动发现签名身份。流水线**不假设任何证书/Apple ID 存在**，也不会调用签名或公证步骤。

## 6. CI 触发与步骤

`.github/workflows/desktop-release.yml`

触发：

- `workflow_dispatch`：手动构建（可选是否构建未签名安装包、可指定 Node.js 版本），
  产物只作为 Actions artifacts 上传，不创建 Release。
- push tag `v*`：两平台构建 + 校验和 + 创建 **draft** GitHub Release（不自动发布）。

PR 不触发发布；workflow 级权限为 `contents: read`，只有 draft release job 提升为 `contents: write`。

每个平台 job 的步骤：

1. `actions/checkout`
2. `actions/setup-python@v5`（3.12，作为运行打包脚本的 driver）与 `astral-sh/setup-uv@v6`（固定 uv 版本）
3. `actions/setup-node@v4`（22.x，仅用于 WebUI 与 Electron 外壳构建）
4. 校验 `desktop/package.json` 存在且包含 `test` 与 `dist:mac` / `dist:win` 脚本；
   **缺失时给出明确 error 并失败，不会静默跳过安装包构建**
5. `python packaging/build_runtime.py --clean --node-version <ver>`（日志 tee 到 `packaging/dist/logs/`）
6. `python packaging/smoke_test.py`
7. `desktop/` 内 `npm ci`、`npm test`
8. `npm run dist:mac` / `npm run dist:win`（未签名）
9. 校验安装包确实存在，上传安装包/更新 zip/blockmap/yml artifacts
10. 失败时上传 `packaging/dist/logs/**` 与桌面构建日志，便于定位

随后 `checksums` job 汇总 artifacts 生成 `SHA256SUMS.txt`；tag 构建再由 `draft-release` job
创建 draft release。

## 7. 签名/公证预留（尚未接入）

后续接入时需要（**当前均未配置，CI 不使用**）：

| 用途 | 需要的 secrets / 配置 |
| --- | --- |
| macOS 签名 | `APPLE_CERT_P12`（Developer ID Application 的 base64 p12）、`APPLE_CERT_PASSWORD` |
| macOS 公证 | `APPLE_ID`、`APPLE_APP_SPECIFIC_PASSWORD`、`APPLE_TEAM_ID`（或 App Store Connect API Key） |
| Windows 签名 | `WINDOWS_CERT_PFX`（base64）、`WINDOWS_CERT_PASSWORD`，或 Azure Trusted Signing 配置 |
| electron-builder | `desktop/electron-builder.yml`（或 package.json `build`）中的 `mac.notarize`、`win.signingHashAlgorithms` / `signtoolOptions` 等配置 |

接入顺序建议：

1. 先在 `desktop/` 完成签名配置并本地用真实证书验证一次；
2. 再把证书 secrets 注入 CI（仅 release job）；
3. 签名成功后再把 draft release 的 `draft` 改为 `false`（仍需人工审核），
   并在 macOS 上执行 `xcrun stapler staple`。

在完成上述步骤之前，任何文档与发布说明都**不得**声称已签名/已公证。

## 8. 已知限制

- 仅 macOS arm64 + Windows x64；Intel Mac 与 Windows arm64 未支持。
- 构建只能原生进行：CI 不使用 Linux 交叉打包，脚本会拒绝跨平台构建。
- 运行时首次启动体积大（~790 MiB 未压缩）；Electron 安装包压缩后仍较大。
- WebUI 构建依赖 npm registry：若 `webui/package-lock.json` 与 CI 的 npm 版本/registry 不一致，
  `npm ci` 会直接失败并保留日志（脚本不会静默跳过 WebUI 或使用残缺产物）。
- 运行时 Python 缓存/安装目录必须可写且可重定向；CI 通过 `JW_BUILD_CACHE_DIR` 等环境变量把缓存放到
  workspace 之外。
- `--include-shared-project` 默认关闭：`projects/default/shared` 可能很大，且可能包含用户数据。
- 本机沙箱若无法访问网络，`uv python install` / Node 下载 / `npm ci` 无法实测；
  脚本会明确报错而不是伪造成功。
- **首次运行配置**：桌面外壳已内置首启配置向导。干净用户目录下会在启动后端前检查配置；
  未配置时显示供应商/模型/API Key 表单，保存到 `<userData>/config/jw/config.yaml`（权限 `0600`）
  后再启动服务。向导覆盖常用 provider，其余 provider 可通过 `jw onboard` 配置后由桌面识别。
  安装包仍不附带默认凭据或 `.env`。
- **后端进程残留**：`jw deploy` 以 `start_new_session=True` 启动 `langgraph dev`；
  若 SIGTERM 恰好发生在 `jw` 注册 atexit 清理钩子之前，`langgraph dev` 可能残留并占用后端端口。
  目前由 Electron 外壳的"端口响应 + 进程归属"兜底清理；若 Python 侧把 langgraph pid 写入
  `JW_DATA_DIR` 并提供正式停止接口，可移除该兜底。
- `desktop/package-lock.json` 必须提交（lockfileVersion 3）。CI 使用 `npm ci` 复现安装；
  lockfile 缺失时 CI 明确报错并失败，不会降级为不可复现的 `npm install`。
- `npm ci` 会触发 Electron 的 postinstall（从 GitHub 下载平台二进制）。若构建环境出网受限，
  应设置 `ELECTRON_MIRROR` 指向内部镜像或预置 Electron 缓存，**不要**使用 `--ignore-scripts`
  （会导致 Electron 无法运行，安装包不可用）。

## 9. 后续扩展

- **macOS Intel（x64）**：增加 `macos-13`（x64 runner）job，`--arch x64`；运行时与 Node 下载会自动选对应发行包。
- **Windows arm64**：增加 `windows-11-arm` runner（若可用），`--arch arm64`；需确认 Node.js 官方 win-arm64 发行包。
- **通用 macOS 包（universal）**：Electron 外壳可做 universal，但私有 Python/Node 必须分别打入两套 runtime
  再按架构选择，成本较高，建议先做双架构独立安装包。
- **自动更新**：需要签名 + `latest-mac.yml` / `latest.yml` + 发布渠道；当前只上传元数据，未接入 updater。
- **体积优化**：裁剪 Node.js 发行包的 `include/`、`share/`，剥离 Python 包内测试与缓存，
  或在 CI 中对 runtime 做压缩归档后再交给 electron-builder。

## 10. 本机实测记录（作为验证基线）

在 macOS arm64 上实际执行并验证：

- `python packaging/build_runtime.py --clean`（使用本地可搬迁 CPython 3.12.12、本地 Node.js 22.22.3 发行包、
  本地预构建 JW wheel、已有 `webui/dist` 与 `.venv` site-packages）→ 成功产出 runtime，
  逻辑大小约 788.6 MiB，manifest 内容与第 2 节一致。
- `python packaging/smoke_test.py` → 全部 PASS（0 problem / 0 warning）。
- 失败路径：传入不存在的 `--python-dir` → 非 0 退出且不残留半成品 runtime。
- `cd desktop && npm test` → 120/120 通过；`node --check` 覆盖全部主进程模块。
- Electron 45.0.0 实际启动打包后的 `Jinwu.app`（`app.isPackaged=true`）：
  首次启动进入配置向导，保存 DashScope 测试 key 后后端 `/ok` 与 WebUI 均返回 200；
  持续运行 150 秒（超过原 120 秒超时窗口）未再出现后端退出；退出后 6174/4716 端口无残留。
- 修复回归：Python 本地健康检查、LangGraph SDK 客户端和 AsyncSubAgent URL 从
  `http://localhost:<port>` 改为 `http://127.0.0.1:<port>`。此前在 macOS 上
  `localhost` 可能先解析 `::1`，而 LangGraph 只监听 IPv4，导致启动 120 秒后被误判失败并退出。
- `electron-builder --mac dmg --arm64` 产出 `Jinwu-0.3.0-mac-arm64.dmg`（约 363 MiB）与
  更新用 `Jinwu-0.3.0-mac-arm64.zip`（约 369 MiB）；`hdiutil verify` 通过，
  挂载后包含 `Jinwu.app` 与 `Applications` 链接。
- 产物未签名、未公证；使用默认 Electron 图标。macOS 首次打开需右键 → 打开。

未在本机实测：Windows x64 安装包（必须在 Windows runner 或 CI 构建）、真实 Apple Developer ID
签名/公证、自动更新、真实供应商 API 调用（使用测试 key 验证的是启动链路，不验证远端模型响应）。
