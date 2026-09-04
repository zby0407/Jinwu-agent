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
