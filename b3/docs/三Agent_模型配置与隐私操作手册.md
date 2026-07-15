# 三个科研 Agent：模型配置与隐私操作

## 模型路线

Agent 文件统一写 `model: b3-default`，项目级 `.pi/settings.json`、Pi 路由和 live evaluator 统一使用固定快照：

| 场景 | 值 |
|---|---|
| Pi 默认路由 | `dashscope/qwen3.7-max-2026-06-08` |
| 百炼模型 id | `qwen3.7-max-2026-06-08` |
| 可选 Qwen 路由 | `dashscope/qwen3.7-plus-2026-05-26`（均衡）；`dashscope/qwen3.6-flash-2026-04-16`（低成本） |
| 北京地域共享 OpenAI-compatible 地址 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| 正式温度 | `0.2` |

默认不使用浮动别名 `qwen3.7-max`，因为它可能在复核期间指向不同快照。当前只允许表中三个已审核的固定 Qwen 快照；其他模型 id 或非官方 endpoint 会被拒绝。默认和正式验收始终是 Max，Plus 与 Flash 只是显式可选项。迁移前旧路由不参与当前运行或正式交付。

## 你需要提供什么

| 百炼字段 | 是否需要 | 在本项目中如何使用 |
|---|---|---|
| `apiKey` | 需要 | 只在本机启动脚本的隐藏输入框中填写，不要发到聊天 |
| `openAiCompatible` | 可选 | 若是完整 URL，通过 `-BaseUrl` 传入；不传则使用北京共享地址 |
| `apiHost` | 通常不需要 | 已提供完整 `openAiCompatible` 时不再单独拼接 |
| `id` | 不需要 | 本路由不用它发起推理 |
| `dashScope` | 不需要 | Pi 使用 OpenAI-compatible endpoint，不调用原生 DashScope SDK 端点 |

## 安全启动 Qwen Max

本机用户级 Pi 配置位于 `%USERPROFILE%\.pi\agent\settings.json` 与 `%USERPROFILE%\.pi\agent\models.json`，已同步为 Max 默认、Plus/Flash 可选；`models.json` 只引用 `$DASHSCOPE_API_KEY`，不保存真实密钥。项目内 `.pi/settings.json` 与严格 Provider 仍是本项目的可移植真源。

在项目根目录打开新 PowerShell，运行：

```powershell
.\scripts_b3\start_qwen_max_pi.ps1
```

脚本会在 PowerShell 的隐藏输入中询问 `apiKey`，不把 Key 写入文件。它还会同时固定 `B3_AGENT_MODEL`、`B3_QWEN_MODEL`、endpoint 与温度，然后在项目根目录启动 Pi；Pi 退出后恢复启动前的进程环境。

如需临时比较已审核的 Qwen Plus，可显式运行：

```powershell
.\scripts_b3\start_qwen_max_pi.ps1 -ModelId 'qwen3.7-plus-2026-05-26'
```

低成本调试可显式选择 Flash：

```powershell
.\scripts_b3\start_qwen_max_pi.ps1 -ModelId 'qwen3.6-flash-2026-04-16'
```

不传 `-ModelId` 时始终使用 Qwen3.7-Max。正式 live proof 脚本不会接受 Plus 或 Flash。

如果你的北京工作空间提供了专属 `openAiCompatible` 完整地址，运行：

```powershell
.\scripts_b3\start_qwen_max_pi.ps1 -BaseUrl 'https://<workspace-id>.cn-beijing.maas.aliyuncs.com/compatible-mode/v1'
```

脚本只接受 HTTPS、阿里云官方域名和精确 `/compatible-mode/v1` 路径，并拒绝 URL 中的用户信息、查询字符串和片段，避免把 bearer key 发往未审核主机。

```text
/reload
/b3-doctor
```

`/b3-doctor` 检查活动路由、凭据是否存在，以及 NumPy/psutil 是否与 `requirements-analysis.lock` 精确一致；它不会证明真实推理成功。

## 正式 Qwen 证明

先跑一个案例，不写 proof：

```powershell
python scripts_b3/evaluate_pi_science_agents.py --mode live --case G01_bounded_cycle26_plan --no-write-proof
```

再跑完整评测：

```powershell
python scripts_b3/evaluate_pi_science_agents.py --mode live
```

只有固定模型快照 `qwen3.7-max-2026-06-08`、温度 `0.2`、全部要求案例与重复通过、真实工具轨迹有效、`fallback_used=false`，才能写 `live_qwen_ready=true`。任何旧模型成功、fixture 成功、占位 key 或模型列表都不能替代。

## 清理密钥

```powershell
Remove-Item Env:DASHSCOPE_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:QWEN_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:B3_AGENT_MODEL -ErrorAction SilentlyContinue
Remove-Item Env:B3_QWEN_MODEL -ErrorAction SilentlyContinue
Remove-Item Env:B3_QWEN_TEMPERATURE -ErrorAction SilentlyContinue
```

关闭该 PowerShell 窗口。API key 不得进入聊天、Markdown、代码、命令字面量、截图、日志、Git 或项目文件。

## 工具进程与收据边界

科学工具只使用项目内 `.venv`，以 Python `-I` 隔离模式和环境白名单启动；`PYTHONPATH`、`PYTHONHOME`、模型 API key 等不会传入工具进程。Pi 进程为每次会话生成临时 HMAC 密钥，Python 工具只拿到完成本次收据认证所需的进程环境值，密钥不写入文件。

收据只保存 call id、tool id、角色、envelope 哈希和 HMAC，不保存输入、结果或科研正文；但文件数量、时间以及这些标识仍属于操作元数据。临时密钥随 Pi 进程结束而失效，收据用于同一会话内立即验真，长期复核依靠冻结 artifact 和回放门禁。本机制约束的是没有任意写工具的模型子进程，不声称抵御能够读取同用户进程内存的本机攻击者，也不替代操作系统沙箱。人工 `--human-offline` 模式不签发收据，结果强制不可主张。

## GitHub / Gitee 隐私规则

当前任务不授权上传或推送。本地仓库继续无远端工作即可。

将来确需建立远端时：

1. 先创建 **Private** 仓库并在平台页面复核可见性。
2. 不要先建 public 再改 private。
3. 添加 remote 前复核 owner、仓库名和 URL。
4. 推送前运行秘密扫描与本地验证；不得用 `--no-verify` 绕过 guard。
5. 只有用户再次明确授权后才执行 `git push`。

本目录不含 API key、`.env`、原始 live trace 或私仓可见性证据。

参考：[阿里云百炼 API Key](https://help.aliyun.com/zh/model-studio/get-api-key)、[千问 OpenAI 兼容接口](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)、[GitHub 仓库可见性](https://docs.github.com/en/repositories/creating-and-managing-repositories/about-repositories)。
