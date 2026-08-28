# Qwen Harness 本地计算运行时修复说明

## 问题与影响

Token Plan 的 OpenAI-compatible `chat/completions` 路径通过 `run_python` 提交计算代码，再由任务内的隔离 Python 环境真实执行。新建 uv 虚拟环境中的 Python 可执行文件采用多级符号链接：虚拟环境内的 `python3` 先指向 `python`，再指向 uv 管理的基础运行时。原有 bubblewrap 配置只挂载虚拟环境和系统 `/usr`，没有挂载第二级链接所指向的基础运行时。

在这种环境中，模型能够正确返回 `run_python` 调用，但执行器找不到 Python，计算状态只能记为 `failed`，整个 Harness 回执降为 `partial`。说明文字或模型自行推算的数值不会被当作真实计算结果，因此该失败不会产生伪造的派生证据。

## 修改文件

- `jw/research_harness.py`
- `tests/test_research_harness.py`

本次没有修改 `jw/subagents/solar/solar_data.yaml`、数据集选择规则或太阳物理结论。变化只涉及 Data Agent 所调用的受控计算运行时。

同一分支后续增加了初次部署时的权威数据获取与项目登记能力；该部分的行为、来源和验证边界见 `docs/Data_Agent权威数据自主获取与来源登记说明_2026-08-21.md`。

## 新行为

执行器现在沿虚拟环境 Python 的完整符号链接链查找基础运行时。若最终解释器位于虚拟环境和 `/usr` 之外，bubblewrap 会创建空的父目录，并只读挂载该 Python 运行时目录。任务工作区仍是唯一可写目录，网络继续隔离，宿主目录、环境变量和任务外文件不会随运行时一起暴露。

这项修复同时覆盖以下两种启动方式：

- pytest 或其他入口直接使用虚拟环境中的 `python`；
- `uv run python` 使用 `python3 → python → 基础运行时` 的多级链接。

## 输入、输出与接口

Harness 分析入口继续接收任务根、任务编号、研究问题、分析目标、已登记输入列表和计算说明。只有任务清单中的相对路径文件会复制到本次 `python_workspace`。模型只能调用 `run_python`，提交的代码仍需通过导入、调用和路径边界检查。

成功执行后，任务目录形成：

- 代码、标准输出、返回码和输出文件清单组成的计算记录；
- `calculations/files/` 下的派生输出；
- `harness-evidence-v1` 回执，记录任务绑定、工具协议、工件和限制。

普通文字、代码片段、失败调用和没有派生工件的完成消息仍保持 `partial`，不会升级为计算证据。

## 进入 Data 与 Evidence 的方式

Data Agent 可以把当前任务的 Harness 回执作为结构化数据上下文的一部分提交。适配器只接受与当前任务、当前调用和输入清单一致的回执，并把合格的计算记录与派生输出投影为候选证据。它们不会自动成为知识库条目，也不会自动支持科学主张。

Evidence 需要复核输入文件、执行代码、返回码、输出文件和主张之间的关系。即使 Harness 状态为 `completed`，Evidence 仍可根据样本单位、测量口径、时间信息集、方法假设或输出内容将主张判为受限、反对或不可用。

## 验证结果

修复前的全量 Python 回归为 `3677 passed, 13 skipped, 6 warnings, 1 failed`，唯一失败正是本地 `run_python` 未能启动 Python。独立复现记录的错误为找不到 bubblewrap 内的虚拟环境解释器。

修复后，以下三条定向测试通过：

1. Qwen Chat Harness 能真实执行函数工具并保存派生输出；
2. 执行器不能读取任务工作区以外的宿主文件；
3. 两级 Python 符号链接能够解析并运行。

真实 Qwen 探针使用三行任务内 CSV。第一次运行保留为 `partial` 失败回执；修复后的全新运行通过 `chat/completions` 调用 `run_python`，实际得到样本数 3、总和 12、均值 4.0，并保存计算记录和 JSON 派生输出，状态为 `completed`。该探针验证协议、隔离执行和落盘路径，不支持任何太阳物理结论。

完成 Data 自主获取、失败证据投影和周期对交接调整后，最终代码状态的根测试树为
`3700 passed, 13 skipped, 8 subtests passed`，自动实验独立测试树为
`275 passed, 21 skipped, 18 subtests passed`，假设镜像测试树为 `79 passed, 6 skipped`。
WebUI 的 30 项 Node 测试通过，production build 成功生成 standalone 服务。Python 编译、
未定义名称检查与差异格式检查也通过。这些结果证明当前工程代码与已执行测试兼容，
不能替代长时间模型稳定性或主科学问题的有效性验证。

## 结论边界

本次修改证明受控本地计算可以在当前 uv 环境中真实运行，并维持任务外文件不可读的边界。它不能证明长时间模型调用稳定，不能证明任一数据集足以回答主科学问题，也不能提高交互作用假设的证据等级。完整科研有效性仍由 production WebUI 主问题运行、阶段工件、样本外实验、Evidence 审查和外部科学复核共同决定。
