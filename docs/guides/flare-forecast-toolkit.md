# SHARP标量24小时耀斑预测工具

## 前端聊天发报

在加载本次代码的JW前端，可输入“帮我预测未来24h的耀斑爆发情况”或“请给出未来24小时的太阳耀斑预报报告”。系统将调用一次 `forecast_flare_24h`：最新官方SHARP获取 → 原生solar-data完整读取与独立审阅 → 冻结模型一次签发 → 图文报告交付。没有合格输入或审阅未接受时，如实返回未签发及失败回执，不用旧概率代替。

模型和必要输入仍须先按下文安装/导入。源码默认读取本仓库的 `workspace/flare_forecast/assets/`；安装包或独立runtime部署须在后端启动环境中设置 `JW_FLARE_ASSETS_DIR` 指向该目录。资产缺失/哈希不符不会自动下载或重建。需安装 `solar` extra；模型provider/API配置沿用JW，原生Data每请求最多4次模型调用、2次工具、600秒活动时间，每次模型请求150秒且无重试。旧科研批次预算不清零，也不混入本次新请求。

每个用户消息独立登记在其任务工作区，绑定固定模型身份与来源合同。当前线程的消息重试复用同一请求回执，不重复获取/签发；新用户消息才对应新请求。原生Data的完整typed工具结果、实际提交call ID、原话和session/witness均保存；结束后的结果仍为受限实验预报，未建立正式科学审查链。这里不经过全量科研计划/假设/训练链，也不会启动其他赛题或自动任务。

聊天返回概率、北京时间窗口和 `outputs/flare_forecast/<id>/report.md`、`report.html`、`issued_forecast.json` 文件入口。Markdown可在现有文件面板阅读；HTML是含嵌图的独立离线图文报告，可下载后在浏览器打开或打印。历史图使用全部841个冻结测试窗口，明示它不是未来逐时走势。图表和指标由本次真实主Agent工具计算/编排，Data只审阅输入，不能声称Data做了图像目检或新独立模型性能审查。报告/签发产物属于当前任务runtime，被Git忽略，不写入工具源码或输入资产。

源码变更需重启加载后端后才生效；仅复制工具或仍运行旧后端不会获得聊天能力。自然语言能力只覆盖明确的当前未来24小时请求；历史回测、72小时、明天指定窗口或模型调整不触发该流程。暂不接入LaTeX，也不生成PDF文件。

### DeepSeek与Qwen

受控工具调用使用本次请求的provider/model。DeepSeek官方入口关闭`thinking`；Qwen百炼或已登记OpenAI兼容入口关闭`enable_thinking`，同时移除冲突的`preserve_thinking`/`thinking_budget`。只复制本次请求模型与SDK客户端，保留默认聊天配置；SDK重试为0。实例上的reasoning passback包装在复制件上重新绑定，完整工具消息与call ID不会因复制而丢失。

原生Data提交使用严格类型的scope/checks/issues/limitations/facts。快照时刻、中央patch数、完整主QUALITY和二级bit31事实必须与本次工具核验值一致；字段别名、旧样本数或位号错配均不能取得签发资格。Data实际读的是已登记的完整选中快照、全部候选组选择摘要、合同/来源资格及完整特征表；未选择组逐patch明细另保留原哈希和字节，不声称LLM全部读过。

已通过DeepSeek实际主图请求、原生Data提交、一次签发及报告交付，以及Qwen3.7/3.8的百炼/兼容入口线级参数回归。当前配置的custom-openai网关对qwen3.8-max返回503 model_not_found（无可用通道），所以尚未声称该通道的真实端到端成功。更换Qwen前须配置确实提供所选模型的provider/base URL/API key；切换不重训预测模型，报告概率只来自同一冻结RF。

该工具将已验证的耀斑预测实现整理到 `jw/flare_forecast/`。输入是23项中央
SHARP标量，目标是全太阳GOES目录在签发后24小时内是否出现新起始C1.0+或
M1.0+事件。不是像素磁图CNN、耀斑位置、逐时强度或事件次数预测。

## 安装与文件布局

```bash
uv sync --dev --extra solar
jw flare --help
```

`python -m jw.flare_forecast` 是同一入口。帮助、导入及校验操作不下载数据，
不会调用LLM、训练、签发、启动服务或创建自动任务。

| 路径 | 内容 |
| --- | --- |
| `jw/flare_forecast/` | 可移植入口、模型推理、输入校验与工作流 |
| `jw/flare_forecast/_frozen/` | 11份原字节科学实现，按源码哈希校验 |
| `jw/flare_forecast/asset_inventory.json` | 353份必要输入及原模型的文件、字节与哈希索引 |
| `jw/flare_forecast/model_protocol.json` | 模型语义及原历史Evidence的限定范围 |
| `jw/flare_forecast/contract_template.json` | 未接受的协议模板，新任务须登记项目、路径及审阅 |
| `workspace/flare_forecast/assets/` | 本机原始输入、已准备数据和冻结模型，Git忽略 |
| `workspace/flare_forecast/runs/` | 显式发起的新获取或签发输出，Git忽略 |

输入约929MB，包含原始SHARP关键词、GOES目录/覆盖、2325行主队列、315行共同
子集和原RF模型。报告、图表、LLM日志、会话数据库及既有签发报文不属于输入包。
原数据目录保持原样，本机使用逐字节复制件。此PR仅公开代码、索引与测试，不公开
原始数据或模型权重；JSOC/HMI使用政策未核定，不为其推断Apache或统一SPDX许可。

## 导入与校验输入

另一台机器需显式提供对应冻结文件。`sources.json` 将索引中的非raw文件名映射到
当地原件路径，例如 `model/selected_rf.joblib`、`prepared/daily_physical.csv`、
`source/schema.json`、`source/quality_policy.json`、`contract/original_contract.json`。
`asset_inventory.json` 列出全部映射键。旧盘符没有写入工具代码。

```bash
jw flare import-assets --raw /path/to/F6_physical_raw_01 --sources /path/to/sources.json --output workspace/flare_forecast/assets
jw flare verify-assets --assets workspace/flare_forecast/assets
jw flare export-model --assets workspace/flare_forecast/assets
```

导入在写入前检查每个原件，写入后再核对。不会扫描并复制原件旁边的报告或runtime，
也不覆盖已有目的目录。错误或缺失输入不会触发重新下载。

`export-model` 仅反序列化索引指定哈希的可信本地checkpoint，不读取任意pickle。
它将200棵树和原温度参数转换为JSON，未拟合或重新校准。实际推理使用JSON及
NumPy，不依赖训练版本的pickle加载。树分支保持原float32输入语义；公开的模型
身份文件用于拒绝被替换的JSON和绑定。转换已与841个冻结测试窗口核对。

## 未签发评分与实时流程

只计算已有特征表的概率：

```bash
jw flare score --assets workspace/flare_forecast/assets --input /path/to/physical_features.csv
```

表格必须含完整23个特征列。返回 `unissued_scores`，没有未来窗口；任意特征表不会
因此获得实时输入资格，评分也不能解释为真实前瞻发报。

显式获取最新合格SHARP组：

```bash
jw flare fetch-live --assets workspace/flare_forecast/assets --output workspace/flare_forecast/runs/new_live
```

一次schema与一次最近12小时关键词查询，每次最多30秒/2MiB，无自动重试。
保留原始响应、来源、观测/处理/检索时间及全部质量字段。仅允许完整主QUALITY恰为
1024；它仍是受限警告，不清位、不称无害。字段、来源版本、时钟、中央patch完整性
或三小时新鲜度失败时，不取得签发资格。

该命令生成相邻 `new_live.contract.json`，只重定位源定义文件，不产生新的科学审查
结论。随后需JW Data对本次新输入实际审阅，提交绑定该manifest和合同哈希的原生
回执及session/tool witness。旧输入的Data回执不能复用。

```bash
jw flare issue --assets workspace/flare_forecast/assets --live-dir workspace/flare_forecast/runs/new_live --contract workspace/flare_forecast/runs/new_live.contract.json --data-review /path/to/new_native_data_review.json --output workspace/flare_forecast/runs/new_issue
```

签发保留真实原生Data审阅门槛，不能用操作者生成的accept字段代替。没有新回执或
绑定不一致即拒绝，不提供跳过审阅开关。本工具未自动创建原生角色会话，也不声称
CLI运行本身等于JW真实调用；执行主体仍须由外部原生工具回执证明。

模型计算后捕获真实UTC签发时间，窗口为 `[issued, issued+86400秒)`。此刻再次
检查输入支持结束、处理、检索和年龄；一次snapshot仅能尝试签发一次，失败保留claim。
未来真值保持null。原历史Evidence仅提供限定历史性能依据，不证明新窗口正确。

## 准备、训练与独立标签审计

三个入口调用保留的原科学实现；新运行需自己的来源合同及真实原生Data回执。
导入的 `prepared_manifest.json` 是来源原件，不能改写旧哈希或把旧接受改为新接受。
原质量审阅文档和其原生报告仍位于原实验档案，未作为输入包复制。

```bash
jw flare prepare -- --input-dir /path/to/raw_bundle --contract /path/to/registered_contract.json --output-dir /path/to/new_prepared
jw flare audit-labels -- --raw-dir /path/to/raw_bundle --prepared-dir /path/to/new_prepared --contract /path/to/registered_contract.json --output-dir /path/to/new_audit --gate /path/to/new_gate.json
jw flare train -- --input-dir /path/to/new_prepared --contract /path/to/registered_contract.json --data-review /path/to/native_data_review.json --output-dir /path/to/new_model
```

历史窗口从 `max(T_OBS+675秒, DATE)+1秒` 起算。DATE不是历史首次发布证明。
标签按事件起始属于半开窗及最终峰值分类；已知开始/峰值而缺结束不自动使未来标签
未知，历史未完成事件仍unknown。覆盖要求99%、最大缺口300秒；缺失不补零。
2011-2020拟合、2021选型、2022校准、2023-2025测试，动态purge；2026仅实时输入。
C/M嵌套、训练隔离、固定选型网格及校准规则保持原实现。

这次迁移不执行上述科学任务。新训练结果不会自动替换索引固定的预报模型；替换模型
需另行登记、审阅和验证。原科学状态 `not_reviewed`、正式审查链false及业务验证false
均保留。工具导入不会提升原科学裁决，也不提供LaTeX/PDF报告功能。
