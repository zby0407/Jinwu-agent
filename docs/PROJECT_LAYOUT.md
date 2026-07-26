# 项目目录与数据位置

本仓库把版本化资源与运行期状态分开：

```text
.
├── jw/                 主程序、工具与子 Agent 配置
├── src/                科研合同和确定性工作流源码
├── research/           规范、Schema、示例、测试和策划知识资料
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

1. 新的真实科研数据只放入 `workspace/data/`，并附带来源说明或 provenance。
2. 不直接编辑 `workspace/projects/*/shared/data/`；它由项目数据清单同步管理。
3. 示例、fixture、估算值和模拟观测不得注册为 `primary_data`。
4. 知识库的权威 SQLite 数据库默认位于 `~/.jw/knowledge.db`，对应的可读
   Markdown 镜像位于 `workspace/knowledge_base/`。
5. `JW_WORKSPACE_DIR` 可以整体迁移 `workspace/`；`JW_DATA_DIR` 可以迁移
   `~/.jw/` 中的全局 SQLite 状态。

当前真实观测输入只有 WDC-SILSO Version 2.0 的月均太阳黑子序列和太阳活动周
极小/极大表。完整任务可见清单见
`workspace/projects/default/shared/data_manifest.json`。
