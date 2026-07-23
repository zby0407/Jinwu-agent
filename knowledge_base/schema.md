# Wiki 结构

每个生成页面都有 YAML frontmatter 和稳定 id。跨页链接使用兼容 Obsidian 的
`[[type/entry-id|title]]` 语法。

## 页面类型

- `concept`：精确定义及物理解释
- `mechanism`：科学主张、支持证据、反证和可检验预测
- `data_source`：采集方法、覆盖范围、校准和已知偏差
- `experiment_paradigm`：实验设计、指标和失效模式
- `hypothesis_template`：可复用的假设结构及适用条件
- `finding`：来自可追溯运行的可复用结果
- `counterexample`：削弱或限定已有主张的证据

## 生命周期

`candidate → canonical → deprecated/superseded`

原始运行日志和暂态任务产物属于 workspace/history。只有可复用、来源充分的结论才进入 Wiki。
