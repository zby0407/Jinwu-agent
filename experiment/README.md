# 自动实验 Agent 1.0

自动实验 Agent 1.0 在 Pi 中接收自然语言计算实验任务，自主形成最小充分设计，在 WSL2 与 bubblewrap 隔离环境中真实执行 Python，并把结果、限制和复现方式整理为 Markdown 报告。方法和阶段由当前问题决定；简单统计可以只有一个阶段，确有中间证据依赖时才使用有界的条件后续阶段。

## 快速使用

在 PowerShell 中进入项目目录并启动 Pi：

```powershell
Set-Location "E:\2026tzb\dist\自动实验agent"
pi
```

Pi 使用 `dashscope/qwen3.7-max-2026-06-08` 和 `high` 推理等级。首次使用可先检查运行环境：

```text
/automatic-experiment-doctor
```

在命令列表中选中 `/automatic-experiment` 后直接回车，Pi 会继续弹出输入框。直接描述要计算、比较、模拟或核对的问题即可：

```text
/automatic-experiment 读取 inputs/example_mean.csv，真实计算全部有限数值的均值、样本数、最小值和最大值。
```

任务可接收零到多份上游反馈和零到多个数据或代码产物。下面的合成交接示例使用一份研究规划、一份数据说明和一张处理后表格，但这种组合不是固定协议：

```text
/automatic-experiment 请结合 inputs/upstream_handoff_demo/research_plan_feedback.md 与 inputs/upstream_handoff_demo/data_feature_feedback.json，使用 inputs/upstream_handoff_demo/polar_overlap_features.csv，自主设计并真实执行当前数据能够支持的有界实验；把不能回答的问题明确保留为限制，不得把合成演示数据写成真实太阳观测。
```

普通任务每次都由千问根据当前问题重新设计并建立新运行，不查找历史任务、不覆盖旧记录。只有研究者需要核查某次运行能否按原条件复现时，才主动使用 `/automatic-experiment 重放 <run_id>`，它不会由系统自动弹出。

只有需要精确设置预算、种子或输入清单时，才使用位于 `inputs/` 中的高级 JSON 请求：

```text
/automatic-experiment @inputs/request.json
```

## 文档

- 测试与日常操作（提交、控制、输出、验收、常见问题）：[自动实验Agent测试与使用手册.md](自动实验Agent测试与使用手册.md)
- 架构、契约、沙箱与核验细节：[自动实验Agent技术说明.md](自动实验Agent技术说明.md)

系统由七个 Pi Tools 和七个可组合 Skills 构成，公共合同使用 `automatic-experiment-*-v1`。
