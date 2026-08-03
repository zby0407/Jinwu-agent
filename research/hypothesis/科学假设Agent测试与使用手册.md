# 科学假设 Agent 测试与使用手册

## 环境要求

- Windows + Pi（与项目当前版本一致）。
- Python 3.11 及以上（只用标准库，无需安装依赖）。
- `DASHSCOPE_API_KEY` 或 `QWEN_API_KEY` 已配置。

## 日常使用

```powershell
Set-Location "E:\2026tzb\dist\科学假设agent"
pi
```

| 操作 | 命令 |
| --- | --- |
| 环境自检 | `/scientific-hypothesis-doctor` |
| 提交研究问题 | `/scientific-hypothesis 你的问题` |
| 弹框输入 | 选中 `/scientific-hypothesis` 后直接回车 |
| 高级 JSON 请求 | `/scientific-hypothesis @inputs/example_request.json` |
| 查看进度 | `/scientific-hypothesis-status` |
| 中断后继续 | 再次输入 `/scientific-hypothesis` |

生成结果在 `runs/<run_id>/`，重点阅读 `hypotheses.md`。

## 确定性测试

77 项测试覆盖请求合同、证据绑定门、响应合同、科学语义检查、预检、排序核验、上游交接、渲染、保存与发布一致性，含数据与特征材料（data_feature）接口：

```powershell
Set-Location "E:\2026tzb\dist\科学假设agent"
$env:PYTHONUTF8='1'
python -B -m unittest discover -s tests -v
```

覆盖请求合同、证据绑定门、实验/数据特征材料区分、引用闭合、同义去重、结构化边界完整性、认识论状态与证据一致性、不确定性完整性、无界泛化和未操作化判断词、置信度纪律、数据覆盖范围门、排序核验（rubric 七维、可追溯理由与锚点、连续全覆盖、成对一致、确定性重算）、反例/冲突点汇总、上游交接（哈希、终态门、规划反馈登记）、阻塞与澄清渲染、保存回读与发布一致性。发布一致性强制：六个 Pi Tools、八个可组合 Skills、六份合同与文档一致；无密钥/字节码混入；`RELEASE_INVENTORY.json` 与磁盘逐字节对齐（改动产品文件后运行 `PYTHONUTF8=1 python -B tools/build_release_inventory.py` 重新生成）。

## 真实 Pi 回归建议

维护者可用 `tests/pi_rpc_live_runner.mjs` 做无人值守的真实会话验收（以 RPC 模式启动 Pi，不是产品入口）：

```powershell
Set-Location "E:\2026tzb\dist\科学假设agent"
$env:PI_CLI_JS='E:\pi-agent\npm-global\node_modules\@earendil-works\pi-coding-agent\dist\cli.js'
$env:PI_LIVE_PROMPT='/scientific-hypothesis 你的研究问题'
node tests\pi_rpc_live_runner.mjs $env:PI_LIVE_PROMPT runs\live_acceptance\session runs\live_acceptance\events.jsonl runs\live_acceptance\stderr.log
```

1. 正常案例：自然语言提交一个机制竞争问题（如两周极小期差异），确认生成 `hypotheses.md` 且候选机制上可区分。
2. 上游交接案例：用 `@inputs/example_request.json`，确认实验复算结果被用作证据、文献笔记默认只作来源发现。
3. 缺输入案例：提交只有文献摘要、没有任何已核验证据的问题，确认候选诚实标注证据缺口而非编造支持。
4. 澄清案例：提交比较基准有实质歧义的问题，确认返回不超过三个自然语言问题且不启动保存。
5. 阻塞案例：提交超出科学假设边界的请求，确认走合同通道产出正式《暂时无法形成科学假设》交付物，说明原因和可恢复条件，而不是自由文本拒绝。
6. 续接案例：任务中途关闭 Pi，重新打开后再次输入命令，确认从保留进度继续并最终保存。
7. 更新案例：先形成一版假设，再提交新实验空结果要求更新，确认假设被调整而非全部证伪，且更新原因写入报告。
8. 证据冲突案例：用 `@inputs/conflict_demo_request.json`（两条方向相反的实验证据），确认同一材料按方向分别绑定支持/反对/限制角色，竞争候选都被保留且冲突显式表达。
9. 上游接力案例：随问题给出自动实验 Agent 的 run 目录（`completed_*` 终态），确认先经 `inspect_upstream` 核验哈希与终态，再把已核验结论作为证据，且嵌套的研究规划反馈被登记为上游来源。
10. 排序案例：多候选问题时确认给出 rubric 七维初步排序，每条名次附可追溯理由与关键证据锚点，`hypotheses.md` 含“初步排序”与“反例与冲突点”两节。
11. 边界条件案例：故意只写一句宽泛的适用范围，确认草稿出现 `scope_conditions_missing/incomplete`；补齐对象、时空、数据、方法、成立、失效和外推边界后告警消失。
12. 认识论与不确定性案例：Wiki-only 候选若把实证支持标为 partial/verified 必须被拒绝；省略不确定性来源、影响或降低方案必须出现告警。
13. 表述严谨性案例：无实证支持时使用“所有活动周”应触发无界外推告警；证伪条件只写“显著差异”而无预注册判据和误差界时应触发未操作化判据告警。

Live 用例清单见 `evals/live_cases_v1.json`，行为用例见 `evals/behavior_cases_v1.json`。

## 验收底线

- 普通用户不需要编写 JSON。
- 任何终态（成功、澄清、阻塞）都产生正式、可行动的结果。
- 真实 Pi 最终状态与 `runs/` 中文件一致。
- 不声称执行了未执行的实验；不把技术失败当作反对证据；不写精确概率和无依据数值门槛。
- 不省略结构化边界、认识论状态和不确定性；不把必要前提、混杂因素、失效边界和证伪条件混写。
- 无实证支持时不做无界泛化；不用“显著/明显/稳定”等主观词代替可执行判据。
- 上游产物未通过哈希与终态核验时一律阻断，绝不降级为猜测。
- 排序的每条名次都可追溯（理由 + 关键证据锚点），不允许只输出排名序号。
- 文档中的工具数（六个）、Skills 数（八个）、测试数（77 项）与实现一致。
