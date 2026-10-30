# Windows 0.3.3 验证记录

日期：2026-10-29。工作区：`D:\Jinwu-agent-dev`。

代码修改与安装包已完成；以下已执行检查通过。**安装器级全新安装、覆盖升级尚未在独立 Windows 环境验收，不能把本记录视为全部发布验收完成。** 本机没有 Windows Sandbox，为避免改变现有安装记录，没有执行 NSIS 安装器。

## 制品与源码一致性

- 安装包：`desktop/dist/Jinwu-0.3.3-win-x64.exe`，362,546,746 字节，未签名。
- SHA-256：`28B13C069915081028AFFA144617BFA0B95E813B43AB426D0BD19774E78A2D61`。
- 桌面 0.3.3 / Python JW 0.2.3 / WebUI 0.1.9；随包 Python 3.12.13、Node 22.22.3、Electron 45.0.0。
- 重新构建当前源码 wheel、WebUI standalone、运行时和 NSIS；没有复用旧 JW 或旧 WebUI 产物。第三方 Python/Node 依赖复用本机原安装的相同依赖；本补丁未更改依赖锁版本。
- 最终 `win-unpacked` 中 437 个 JW Python 源文件逐字节匹配工作区，Electron `main.mjs` 匹配工作区，WebUI `BUILD_ID` 匹配本次构建。
- 使用随包 Python 验证 checked-hash 字节码与源文件一致，编译文件名为相对 `runtime` 路径。最终运行时已从构建目录迁移至 `win-unpacked/resources/runtime` 并成功启动。
- 没有提交、推送、创建 PR、发布、自动安装或替换现有版本。用户原有数据文件未修改。

## 测试结果

| 检查 | 结果 |
| --- | --- |
| Python 配置、模型、启动、CLI、运行时与复现兼容回归 | 372 passed，1 warning，0 failed |
| Electron/桌面 Node 测试 | 132 passed，0 failed |
| WebUI Node 测试 | 120 passed，1 skipped，0 failed |
| TypeScript `tsc --noEmit` | 通过 |
| WebUI 生产构建 | 通过 |
| Ruff F 检查、`git diff --check` | 通过 |
| 运行时构建后自检、Windows NSIS 构建 | 通过 |

以上三组测试合计 624 项通过。WebUI 跳过项为原有符号链接拒绝测试：当前 Windows 账户无法创建所需符号链接，测试明确记录跳过，没有把它计为通过。WebUI 构建仍有既有的 Turbopack 动态依赖追踪提示；构建和类型检查成功。

主要覆盖：代理/TLS/超时/鉴权/权限/额度/模型不可用/未知错误分类；最小连接测试只调用一次且不保存配置；默认连接方式与 `.env` 覆盖；保留密钥；并发初始化只构建一次；主智能体失败可见、可选智能体失败隔离；暂存配置与维护期间阻止新运行；恢复身份与检查点核对、重复点击、切换对话的旧回调、取消、未提交恢复失败后的再次恢复。网络错误用模拟响应，不访问真实模型服务。

最终 Python 测试命令：

```powershell
$env:PYTHON_DOTENV_DISABLED='1'
.venv/Scripts/python.exe -m pytest tests/test_desktop_setup.py tests/test_desktop_runtime.py tests/test_qwen_compat_middleware.py tests/test_bailian_llm_config.py tests/test_config.py tests/test_onboard.py tests/test_reproduction.py tests/test_cli_deploy.py tests/test_cli_version.py tests/test_cli_agent_loader.py tests/test_runtime_layout.py tests/test_ui_runtime.py -q --disable-warnings --basetemp=.cache/pytest-release-final
```

桌面和 WebUI 分别运行 `npm test`；WebUI 再运行 `npm run build` 和 `tsc --noEmit`。Windows 下异步测试与构建需要允许本机回环通信及子进程启动。

## 桌面集成结果

- **全新隔离用户目录**：首次向导默认 `env`；使用合成凭据保存后进入主界面，不再被第二层部署 URL 对话框挡住。
- **设置应用**：用明确标记的模拟前端队列数量验证延后应用；队列清空后自动重启后端，密钥留空保留，草稿保留，维护状态释放。
- **仅 Kimi 配置**：无 DashScope 凭据时，API 3.694 秒就绪、主智能体 8.288 秒就绪。这是单次功能冒烟，不作为性能基准。
- **实际生产可执行文件**：直接启动最终 `Jinwu.exe`，确认 `dev=false`、主智能体就绪；正常关闭后两个服务端口均关闭。
- **占用端口与重复启动**：测试自行占用默认端口，最终可执行文件选择其他可用端口；第二实例退出，第一实例继续运行；正常关闭后测试服务端口关闭。
- **退出清理**：新旧版本共 12 次基准启动均确认退出后测试端口关闭。

上述测试不发送研究数据或真实模型请求。恢复检查主要是模拟逻辑回归，没有执行真实服务商的付费端到端研究任务。没有验证 NSIS 注册表、快捷方式、覆盖升级与卸载流程。

## 启动性能

同一台 Windows 电脑、同一隔离工作区和配置，按旧版六次、新版六次顺序测试，测量时没有并行构建。每次从父进程启动 Electron 前计时。旧版使用已安装的旧桌面代码及运行时，新版使用最终 `win-unpacked` 中的代码和运行时；两者用相同 Electron 测试入口。另有实际生产可执行文件冒烟确认启动行为。

旧版首次部署 URL 对话框由测试自动保存，计时未包含人工输入时间。表中“输入框可用”以实际 DOM 出现为准，“主智能体就绪”以新版 `/api/runtime-status.canSend` 为准；旧版全部智能体同步初始化，其 `/ok` 代表初始化已完成。模型服务响应时间不计入，也没有通过首次发送延后主智能体初始化。

单位：秒。第一行是该轮第一次采样，其余五次用于中位数；这不是清空操作系统文件缓存后的冷启动，也不是安装器安装耗时。

| 次序 | 旧首窗 | 新首窗 | 旧输入框 | 新输入框 | 旧主智能体 | 新主智能体 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 首次采样 | 2.843 | 0.590 | 18.203 | 1.822 | 18.192 | 8.717 |
| 后续 1 | 2.838 | 0.575 | 18.528 | 1.739 | 18.531 | 8.630 |
| 后续 2 | 2.823 | 0.576 | 18.467 | 1.708 | 18.466 | 8.681 |
| 后续 3 | 2.813 | 0.584 | 18.464 | 1.729 | 18.464 | 8.699 |
| 后续 4 | 2.877 | 0.575 | 18.531 | 1.697 | 18.534 | 8.723 |
| 后续 5 | 2.816 | 0.577 | 18.580 | 1.708 | 18.583 | 8.654 |
| 后五次中位数 | **2.823** | **0.576** | **18.528** | **1.708** | **18.531** | **8.681** |

新版主智能体就绪中位数降低 **53.15%**。该隔离基准满足首窗 ≤2 秒、主界面 ≤5 秒、主智能体 ≤15 秒且降低至少 30% 的目标。新用户首次配置冒烟包含自动填写向导，测得首窗 0.612 秒、输入框 3.134 秒、主智能体 10.070 秒，不能外推为真实用户输入凭据的耗时。现有大型工作区、杀毒软件首次扫描、真正系统冷启动仍可能改变结果。

可复用测量脚本为 `packaging/measure_desktop.py` 与 `desktop/test/live-startup.smoke.mjs`。旧版和新版传入相同 `--output`，分别设置 `--label baseline --legacy`、`--label candidate`；每轮默认六次。`--entry` 指向对应 `app.asar/electron/main.mjs`，`--runtime` 指向对应运行时，`--electron` 指向测试 Electron 可执行文件。

## 本机证据文件

以下文件在 `.cache/release-0.3.3/`，属于本机验收产物，不随 Git 提交：

- `python-final.log`、`typescript.log`、`runtime-build-final.log`、`installer-build-final.log`。
- `desktop-comparison/baseline-timings.json`、`desktop-comparison/candidate-timings.json`。
- `first-setup-final/desktop-smoke.json`、`kimi-only/timings.json`。
- `packaged-executable/result.json`、`packaged-executable-final/result.json`。
- `electron-source-check.json`、`runtime-source-check.json`、`installer-sha256.json`。

Node 测试和前端构建日志分别位于 `.cache/desktop-tests-final.log`、`.cache/webui-tests-final.log`、`.cache/webui-build-final.log`。

升级操作与连接设置说明见 [升级说明](RELEASE-0.3.3.md)。
