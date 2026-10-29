# 自动计算实验的隔离环境

Windows 后端使用已安装的 WSL 发行版与 bubblewrap。未设置 `AE_WSL_DISTRO` 时使用用户登记的默认发行版；设置该变量可选择另一份已安装发行版。它不会自动安装系统组件、切换默认发行版或回退到无隔离的本机 Python。

可选的 `AE_SANDBOX_PYTHON` 指定隔离 Linux 中可信 Python 的绝对路径（默认 `/usr/bin/python3`）；`AE_SANDBOX_SITE_PACKAGES` 指定单一锁定依赖目录的绝对 Linux 路径。未设置依赖目录时，检查该解释器的系统及用户 site-packages，只有单个目录内的全部依赖版本均符合锁文件才可继续。实际执行和 doctor 使用同一解释器及只读依赖挂载，不混用本机依赖。

只读检查不会运行实验、安装依赖或创建 run 目录：

```powershell
.venv\Scripts\python.exe scripts/check_experiment_runtime.py
```

准备好运行环境后，在专用技术验收目录运行真实隔离探针：

```powershell
.venv\Scripts\python.exe scripts/check_experiment_runtime.py --probe --workspace D:\technical-probe --output D:\technical-probe-receipt.json
```

`--output` 不覆盖既有回执。缺少运行环境返回 `boundary_blocked`、退出码 2，并跳过探针；只读检查通过仅为 `inspection_only`，不能等同于隔离执行已验收。只有完整 doctor 返回 `ready` 且 `sandbox_probe.ready=true`，才证明本次固定探针对主机文件、网络和依赖挂载的检查通过。它仍不是科学任务成功或模型技能证明。

Windows 上只有 `wsl.exe` 启动器不等于运行环境就绪。WSL2 需要可用的 WSL 本体、内核、虚拟机平台及已初始化的 Linux 发行版；缺少系统前置时需要管理员处理，可能需要重启。现代 WSL2 显示“WSL1 未启用”本身不表示 WSL2 不可用，不应仅因该提示启用 WSL1 旧组件。实际就绪以发行版启动和真实隔离探针为准。该脚本不会自动安装系统组件或重启。Docker/Podman 启动器的存在仅作为环境诊断展示，并不构成已支持或已验证的执行后端。

使用 `wsl --install -d Ubuntu-24.04 --no-launch` 补装后，先在用户自己的 PowerShell 运行 `wsl -d Ubuntu-24.04`，按提示完成首次 Linux 用户名和密码设置。进入普通用户 shell 后可用 `whoami` 核对，再 `exit` 返回 Windows，以 `wsl -l -v` 核对 VERSION 为 2；不要用非交互 root 命令代替首次用户初始化。
