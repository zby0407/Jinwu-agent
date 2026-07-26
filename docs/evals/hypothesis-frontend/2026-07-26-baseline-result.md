# 科学假设 Agent 前端基线结果

## 结论

本轮确认了前端能够向本地后端创建真实任务，但没有完成一条由
`solar-hypothesis` 独立执行的端到端链路。

- 浏览器提交的首次运行在主模型首轮调用前失败，原因是后端继承了已失效的
  Anthropic 本地代理地址。
- 使用同一冻结提示补跑时，主 Agent 通过 `deepseek-v4-pro` 正常工作，但任务
  工作区未包含所引用的输入文件，产生一次人工补入。
- 主 Agent 随后两次调用 `solar-hypothesis`；子 Agent 仍使用启动时冻结的
  Anthropic 模型，两次均因同一代理连接错误失败。
- 主 Agent 转而直接调用科学假设合同工具，经过两轮语义修订后冻结了正式产物。
  产物可读且合同状态一致，但不满足“由科学假设子 Agent 完成”的路由要求。

因此，本轮可作为改进前的运行阻塞与退化行为基线，不能作为
`solar-hypothesis` 独立质量通过证据。

## 运行身份与证据边界

| 项目 | 记录 |
| --- | --- |
| 代码基线 | `37a2088dbadc220c66326d00bf0cfad3ef82faec` |
| 前端 | `http://localhost:4716`，人工提交冻结提示 |
| 后端 | `http://127.0.0.1:6174`，健康检查通过 |
| 冻结提示 | `docs/evals/hypothesis-frontend/2026-07-26-baseline-prompt.md` |
| 评分规程 | `docs/evals/hypothesis-frontend/2026-07-26-rubric.md` |
| 有效补跑模型 | provider=`deepseek`，model=`deepseek-v4-pro` |
| 推理配置 | `JW_REASONING_EFFORT=high`；仅记录配置，不据此声称供应商实际启用特定推理模式 |
| 前端首次 thread | `019f9c61-bad5-75e2-ae40-fb8f9a839c8e` |
| 前端首次 run | `019f9c61-bb57-7080-8dd1-503e8f46a150`，`error` |
| 有效补跑 thread | `019f9c6a-1ba2-7e32-a729-7e41803a0115` |
| 补跑首段 run | `019f9c6a-1ba5-7973-9744-3d0049a36356`，停在输入缺失询问 |
| 人工补入后 run | `019f9c6c-4910-76d3-b6ae-1531427729f7`，停在冻结审批 |
| 冻结审批后 run | `019f9c72-6c01-7092-bd0a-759ba91b159d`，完成 |
| 科学假设产物 run_id | `experiment_agent_handoff_demo-20260726T032258Z-0c3695bb` |

浏览器提交证明了前端到后端的创建链路。产生正式内容的补跑通过本地
LangGraph SDK 提交，并非第二次浏览器交互，因此不能替代改进后的浏览器端
复验和视觉检查。

## 关键运行轨迹

1. 前端首次运行使用启动时默认 Anthropic 模型。后端尝试调用本地地址
   `127.0.0.1:15721`，该端口未监听；运行以 `APIConnectionError` 结束。
2. 补跑显式选择 `deepseek-v4-pro`。主 Agent 先读取
   `@hypothesis/inputs/handoff_demo_request.json`，发现任务工作区没有该文件，
   随即触发 `ask_user`。
3. 将仓库中的同名 JSON 按原路径补入任务工作区后，主 Agent 恢复运行并两次
   调用 `solar-hypothesis`。
4. 两次子 Agent 调用均因 Anthropic 连接错误失败。主 Agent没有把本轮
   `deepseek-v4-pro` 覆盖传播到子 Agent。
5. 主 Agent改用以下合同工具链：
   `scientific_hypothesis_bind_request` →
   `scientific_hypothesis_bind_evidence` × 3 →
   `scientific_hypothesis_validate_response` × 3 →
   `scientific_hypothesis_freeze`。
6. 前两次验证分别发现两组和一组无依据数值门槛；第三次验证通过。冻结经过
   一次人工批准后完成。

附加计数：

- 输入人工修复：1 次；
- `solar-hypothesis` 失败重试：2 次；
- 合同内容修订：2 次；
- 冻结批准：1 次；
- 新实验执行：0 次。

## 冻结量表评分

| 维度 | 得分 | 判据与证据 |
| --- | ---: | --- |
| 专家路由与闭合工具链 | 0/2 | 调用了 `solar-hypothesis`，但两次失败后由主 Agent直接构造候选并完成合同工具链。 |
| 上游材料纪律 | 2/2 | 同口径复算作为已验证支持；技术失败和未经原文核验的文献笔记均标为证据缺口，没有充当反对或支持证据。 |
| 候选机制区分 | 2/2 | 发电机振幅调制、平滑口径伪影和半球相位差在机制、预测及配对判别上明确区分。 |
| 可证伪性 | 2/2 | 三个候选均包含可观测、方向明确的削弱或证伪条件。 |
| 下一项检验区分力 | 2/2 | 多窗口敏感性、多指数一致性和半球相位差检验均说明目标、预期差异与被区分候选。 |
| 排序可追溯性 | 1/2 | 有 `medium/medium/low` 定性优先级及理由，但结构化产物的 `ranking` 为 `null`，未形成七维闭合排序。 |
| 不确定性与表述纪律 | 2/2 | 无依据数值门槛已在验证阶段删除；置信度为定性等级，并明确记录关键证据缺口。 |
| 产物完整性与一致性 | 1/2 | Markdown、组合 JSON、请求快照、状态和 run_id 一致；最终答复没有报告正式产物哈希。 |
| **总分** | **12/16** | 内容与合同质量尚可，但专家路由失败且排序、产物校验信息不完整。 |

## 正式产物

任务工作区：

`projects/default/runs/run_019f9c6a-1ba2-7e32_02b049e5/hypothesis/runs/experiment_agent_handoff_demo-20260726T032258Z-0c3695bb/`

| 文件 | SHA-256 |
| --- | --- |
| `hypotheses.md` | `6e19bcfff1de0cae5d26db9d327fafbe9fca7255583380edd018b9e0b7c98836` |
| `hypothesis_portfolio.json` | `91e233ee1d313222b8346ef2affdc5c2fe6f07fbdf7ced7109f5a737cc855b30` |
| `hypothesis_request.json` | `6d61275c6d2618ae6d48d562f6c8f43e767bca88e40f8684068ed08626192956` |

这些文件位于被 Git 忽略的运行目录中；本记录只保存脱敏后的身份、评分与哈希，
不提交会话缓存或凭据。

## 改进验收目标

后续实现至少应满足：

1. 冻结输入在新任务中无需人工补入即可读取；
2. 主任务的模型覆盖能够传播到 `solar-hypothesis`，或子 Agent 使用独立且可用
   的明确模型配置；
3. 子 Agent 自己完成 bind、evidence、validate、ranking 和 freeze，主 Agent
   不在子 Agent失败后代写；
4. 暴露并实际使用上游检查与七维排序工具；
5. 改进后逐字复用冻结提示，经浏览器前端完成新任务，并保存模型、工具调用、
   run_id、正式文件与哈希；
6. 把前端回退列表中已失效的 `deepseek-v3.2` 更新为后端和供应商共同支持的
   模型名。
