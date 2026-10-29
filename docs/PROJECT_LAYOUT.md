# 项目目录与数据位置

本仓库把版本化资源与运行期状态分开：

```text
.
├── jw/                 主程序、工具与子 Agent 配置
├── src/                科研合同和确定性工作流源码
├── research/           规范、Schema、示例、测试和策划知识资料
├── projects/           可选的版本化权威输入快照与本地项目状态
├── tests/              主应用测试
├── webui/              前端
└── workspace/          所有可变的本地项目状态
```

`research/` 中的内容属于代码库，不应被任务运行直接改写。运行过程中产生的
文件统一进入 `workspace/`：

```text
workspace/
├── data/                       本地科研数据的唯一主副本
├── features/                   由主数据计算出的衍生特征
├── literature/                 已下载并核验来源的文献缓存
├── artifacts/                  跨任务保留的分析图表与脚本
├── papers/                     跨任务保留的研究说明与报告
├── projects/
│   └── <project_id>/
│       ├── shared/data/        任务只读的数据镜像
│       ├── shared/data_manifest.json
│       └── runs/               每个任务隔离的工作目录和产物
├── knowledge_base/             知识库 Markdown 镜像
├── runtime/contracts/          独立合同工具的临时运行状态
└── archive/                    迁移前的历史合同产物
```

数据维护规则：

1. 复现套件冻结的权威输入位于 `projects/default/shared/data/`，并由
   `projects/default/shared/project_data_catalog.json` 记录来源、字节数和 SHA-256。
2. `projects/default/runs/`、共享决策、知识导出和其他运行期状态只保留在本地，不进入 Git。
3. 使用其他工作区时，不直接编辑 `<workspace>/projects/*/shared/data/`；应通过数据获取脚本
   生成或刷新项目数据清单。
4. 示例、fixture、估算值和模拟观测不得注册为 `primary_data`。
5. 知识库的权威 SQLite 数据库默认位于 `~/.jw/knowledge.db`，对应的可读
   Markdown 镜像位于 `workspace/knowledge_base/`。
6. `JW_WORKSPACE_DIR` 可以整体迁移运行工作区；`JW_DATA_DIR` 可以迁移
   `~/.jw/` 中的全局 SQLite 状态。

当前冻结输入包括 WDC-SILSO Version 2.0、MWO/WSO 极区场、WSO 当前极区场和 NOAA
F10.7 月度数据。来源与许可证见
[`projects/default/shared/DATA_LICENSES.md`](../projects/default/shared/DATA_LICENSES.md)。

## 科研工具代码

通用科研能力按用途组织，不把某一次任务的阶段接续、预算账本或设计版本带入主程序：

| 位置 | 用途 |
| --- | --- |
| `jw/research/authoring/` | 规划、分析声明、候选排序和独审裁决的模型输入 Schema 与结构反馈；科研值仍由 Agent 提供 |
| `jw/report_contract.py` | 科研报告必填章节、模板和释放前完整性检查 |
| `jw/literature_full_text.py` | 有界公开论文获取、正文提取、任务绑定与来源校验 |
| `jw/solar_acquisition.py`、`jw/tools/solar_acquisition.py` | 白名单太阳观测的有界获取；下载须有宿主注入的明确授权 |
| `src/automatic_experiment/` | 通用输入快照、代码检查、隔离执行、事实验证和报告生成 |
| `scripts/check_experiment_runtime.py` | 检查隔离执行环境；详见[运行环境说明](guides/automatic-experiment-runtime.md) |

旧 B3 的 `fresh_*`、`step6_direct`、版本接续和专项计费中间件已从运行时代码中移除。
新任务使用普通 CLI/TUI/WebUI 入口和原生科研角色；任务提示、观测、生成的科学代码、
审查与结果保存在独立任务目录。切换模型使用现有模型配置和兼容层，
不需要恢复旧任务的 driver。结构校验通过不能代替数据资格、科学独审或真实执行。

交接项目时可使用[下一智能体的只读了解提示词](guides/read-only-project-introduction.md)。
后续工作重心由用户另行指定，文档中的历史命令和待办不构成执行授权。
