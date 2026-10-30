# 科研影响力分析与偏差解释：基准与指标报告

- 日期：2026-10-29
- 内核：`jw/research_influence/`（schema `jw.research-influence/v1`）
- 基准入口：`tests/research_influence/test_benchmark.py`
- 计分脚本：`tests/research_influence/fixtures/measure_benchmark.py`（复现本报告所有数字）
- 分支：`feat/scientific-data-integration-20261029`

本报告只记录**能重新算出来的数字**。每一条指标都写明它证明了什么、以及它
**不能**证明什么；凡是快照里没有的东西，一律写“无法评估”，不写估计值。

---

## 0. 一句话结论

在 300 篇合成队列上：区间模型在**未参与训练的 61 篇**上取得
Spearman 0.824、Top-10% lift 5.08、80% 区间经验覆盖率 0.803（名义 0.80）、
log1p MAE 0.514；6 类注入偏差中 5 类被命名（含效应量、置信区间、严重度），
第 6 类（可见性优势）被模型吸收后**没有被说成“干净”**，而是判为证据不足并
给出模型自己吸收的预测差；两类“无法评估”（自引、作者人口学）保持拒绝；
同冻结快照重跑 `determinism = 1.0`、报告与数据包指纹一致。

在 **1202 篇真实天体物理队列**上（经本机代理抓取的多源快照）另有两组真实测量：
引用网络（6/6 参考文献解析、参考文献被引中位数 591、PageRank 位次 48.4）与主题
演化（60 个主题、27 个可读、28 个上升），见 §4.1 与 §4.2 —— 这两节是真实数据，
不是合成队列。

§4.3 是次日重测：两次相隔 6 分钟的重抓完全一致（去重后 1191/1191），而与前一天那份
只重合 91/1191 —— 相关性排序的样本是**一个时刻**，不是一个固定队列。这一节同时记下
真实数据逼出来的两个 bug（4 MB 响应上限把 6.8 MB 的页截断后报成"非法载荷"、游标分页
无 key 时返回 401）、一个 OpenAlex 侧的重复 DOI 现象，以及把它变成可测量数字的
`infl_cohort_diff`（§4.3.5）：6 分钟内位次零移动，14 小时后九成以上论文跨过了一个
十分位，而它们自身的被引排序几乎没变（Spearman 0.9663）。

---

## 1. 队列（cohort）

| 项 | 值 |
| --- | --- |
| 论文数 | 300 |
| 生成种子 | 5 |
| 参考年 | 2024 |
| 预测窗口 | 发表后 3 年 |
| 有全文 | 187（62%） |
| 数据来源 | **合成**（`jw/research_influence/cohort.py::synthetic_cohort`） |

**它能证明什么**：管道端到端正确、指标可复现、区间校准机制有效。
**它不能证明什么**：任何真实天体物理结论。队列的“质量”是生成器写进去的，
模型学到它只说明特征与目标之间的通路没断。

队列搜索只需要一个源：`infl_snapshot_search` 打 OpenAlex `/works`，
按页翻到 `limit`（不是只留第一页），Crossref/Unpaywall 只在逐篇 enrich 时用。
默认运行仍然离线拒网；本报告里的真实数据是经操作者本机 HTTP 代理抓取的冻结快照：

| 项 | 值 |
| --- | --- |
| 查询 | `supernova cosmology 2012-2021` |
| `reference_year` | 2024 |
| 论文数 | 1200（无用户上传时；§4.1 与 §6 的 1202 篇是并上两篇上传 PDF） |
| 有摘要 | 1133 |
| 有全文 | 0（全文只来自两篇用户上传） |
| 快照指纹 | `sha256:2b9952762921604dc993d4aef2498ac1fb0837275a71b0ec4ea4327c91a3b03e` |

上面这个指纹是 **本报告 §4.1/§4.2 各表所绑定的那一份文件**（2026-10-29 17:31 构建）。
指纹覆盖论文集合、`cohort_id` 与筛选参数，因此它比“论文数”更严格：同一个查询重抓一
次，即使同样得到 1200 篇，指纹也会变。§4.3 是 2026-10-30 的**重测**，绑定另一份快照。
快照文件本身在 `workspace/research_influence/cohorts/`，被 `.gitignore` 忽略，没有入仓；
读者复现的方式是重抓（得到新指纹）或用自己的导出走 `infl_snapshot_import`。

抓取逻辑本身有离线测试覆盖（`tests/research_influence/test_providers.py` 等，
注入假 Transport，零网络），包括分页：`test_snapshot_search_pages_instead_of_keeping_the_first_page`
断言第二页真的被请求——这条路径此前只被当作“拒网”测过，一测就暴露了两个真 bug
（位置候选分类器拿到的是扁平化后的占位符而不是原始 location 映射；`decide_access`
被按位置参数调用，而它的签名全是 keyword-only）。

离线落地路径是 `infl_snapshot_import`：在别处导出 OpenAlex `/works` 记录（JSON
或 JSONL），导入后冻结为同样格式的快照，之后的建模、解释、审计、报告全部离线复跑。
导入有 3 个测试覆盖（原始记录、JSONL、空输入必须是错误而不是空队列）；导入时若把
原始记录当已解析记录处理，venue/authors 会静默变空——这个分叉被 `_RAW_KEYS`
判别并写进测试。

OpenAlex 是活的索引：同一查询重建得到的是“此后的新快照”，指纹不会相同。所以本报告
所有真实数据数字都绑定上面那个指纹，换快照就要重跑阈值校验。

---

## 2. 抽取质量（用真实 PDF 的文本层计分）

测试用 matplotlib 生成带文本层的 PDF（`pdf.fonttype=42`），再用 pypdf 读回，
所以这一节的分数不是对着 mock 打的。

| 字段 | 结果 |
| --- | --- |
| 标题 | 从文本层首行取得，置信度 **LOW**，带页码 locator，note `title_from_pdf_text_layer` |
| 章节 | 识别 `abstract / introduction / methods / results / data_availability / references / body` |
| 参考文献 | 3 条，其中 3 条解析出 DOI |
| 开放数据 | 1 条（Zenodo，`data` 类），来自 data availability 段 |
| 字段级溯源 | `title / abstract / sections / references / open_data` 五个字段均有 provenance |
| 索引类字段 | `year / venue / authors / doi` 标 `not_reported`，`abstract` 标 `not_indexed` |
| 被引数 | `None` —— 未匹配到索引记录时不猜 0 |

三条设计约束在这里生效：

1. **标题来自 PDF 就是弱证据**：同一字段若同时有 OpenAlex 记录，索引记录优先，
   文本层猜测只做兜底且保留 `ConfidenceBand.LOW`。
2. **像标题的行不一定是标题**：`1. Introduction`、`Abstract`、`Section 3` 一律拒绝，
   否则标题列会变成章节名。
3. **词被切成两半时只报不改**：`T ype` 这类 PDF 取字行为在文本层里与
   `A homogeneous sample` 形态相同，字典不是证据，所以记
   `pdf_text_kerning_suspect:T ype@p1` 并保留原字符。测试
   `test_kerning_splits_are_flagged_not_rewritten` 断言了这条。

---

## 3. 预测质量（分位区间，不是单点）

### 3.1 留出集（模型未训练的 61 篇）

| 指标 | 实测 | 目标 | 判 |
| --- | --- | --- | --- |
| Spearman（log 被引） | **0.824** | ≥ 0.45 | 通过 |
| Top-10% lift（相对随机） | **5.08** | ≥ 2.5 | 通过 |
| 80% 区间经验覆盖率 | **0.803** | 0.70–0.90 | 通过 |
| log1p MAE | **0.514** | ≤ 0.60 | 通过 |
| 训练 / 留出行数 | 142 / 61 | — | — |

### 3.2 全量描述性指标（含训练行，**不是**验证数字）

| 指标 | 实测 |
| --- | --- |
| Spearman | 0.924 |
| Top-10% lift | 6.60 |
| 80% 区间覆盖率 | 0.941 |
| log1p MAE | 0.285 |
| 其中训练行 | 142 / 203 |

这两行的差值本身就是结论：**同一模型在全量上报告 0.94 覆盖率，在留出集上是
0.80**。训练行让区间看起来更准，所以 `metrics_for()` 默认只算留出集，并把
`in_sample_rows` 一起写进指标字典，谁引用 0.94 都必须同时看到 142。

### 3.3 重复交叉验证（4 折 × 2 次）

| 指标 | 均值 | 标准差 |
| --- | --- | --- |
| Spearman | 0.866 | 0.019 |
| 区间覆盖率 | 0.803 | — |
| Top-10% lift | 4.01 | 2.08 |

lift 的标准差 2.08 说明**折内样本一少，十分位就估不稳**：这是本报告不把
lift 当作主要指标的原因，主指标是 Spearman 与区间覆盖率。

### 3.4 分位交叉

独立拟合的 0.1/0.5/0.9 三个分位回归器会互相穿越。内核的做法是**向上修复并
计数**（`low ≤ median ≤ high`），穿越次数写进 `PredictionSet.quantile_crossings`
与指标字典，不静默。本队列 300 篇上的穿越数为 17。

---

## 4. 解释层

| 项 | 实测 |
| --- | --- |
| 每个主体的因素数 | 6（`top_factors` 上限） |
| 每个因素 | 观测值、队列参考值、Δ（log 与被引两种单位）、95% CI、方向、份额 |
| 排列重要性支持的特征 | 16 / 23（CI 排除 0） |
| 首位特征 | `abstract_length`，置换后 Spearman 掉 0.527 |
| 排名稳定性 | 1.0（同输入两次排名完全一致） |

解释层不用 SHAP（环境无 shap/lightgbm/xgboost），全部自实现：置换重要性、
PDP、消融归因（把特征置为队列中位数看 Δ）、kNN 相似论文对照、
分位校准曲线、反事实敏感性。每个因素的 95% CI 来自对被替换的参考值做
bootstrap，因此“Δ 是 0 还是不是 0”是可以被判定的，而不是靠一个点值。

---

## 4.1 引用网络（真实数据，1202 篇冻结核物理队列）

这部分用**真实**索引数据跑（经操作者本机 HTTP 代理），不是合成队列。

| 测量 | arXiv.1509.00001 | TSP 2024 那篇 |
| --- | --- | --- |
| 参考文献解析 | 6 / 6 | 0 / 359 |
| 参考文献被引中位数 | 591 | 无法计算（索引无参考文献列表） |
| 参考文献位于队列前 10% 的比例 | 17% | 无法计算 |
| 引用者篇数 | 1 | 0 |
| 其中自引（姓氏重合下界） | 1 | 0 |
| PageRank 位次 | 48.4 | 不给位次（孤立节点） |

解析图规模：68 个节点、68 条边（只用解析成功的参考文献；未解析的 target 不当节点，
否则 6301 个悬挂节点会把质量打散）。

三条实现层面的坑都在这轮真实数据里暴露并修掉：OpenAlex 不接受 `filter=id:W...`
（必须 `ids.openalex:`）；小样本 top-decile 阈值上取整会让份额恒为 0；上传 PDF 的
参考文献是自由文本、没有 `resolved_work_id`，所以向内指标必须走索引给的
`referenced_works`，否则永远是 0 条。

---

## 4.2 主题演化（同一队列，60 个主题）

| 主题 | 论文数 | 近 3 年占比 | 更早占比 | 相对变化 | 被引中位数 |
| --- | --- | --- | --- | --- | --- |
| Cosmology and Gravitation Theories | 745 | 68% | 59% | +0.15 | 32 |
| Gamma-ray bursts and supernovae | 600 | 47% | 51% | −0.07 | 39 |
| Neutrino Physics Research | 155 | 18% | 11% | +0.64 | 42 |
| Stellar, planetary, and galactic studies | 268 | 15% | 25% | −0.39 | 48 |

60 个主题里 27 个样本量足够读占比、28 个在上升。窗口锚点取“仍有可观样本量的最新
年份”（本次为 2021），不是参考年也不是最大年份：锚在参考年会让 2012-2021 的快照
把所有主题算成灭绝，锚在最大年份会让两篇 2024 论文干同样的事。

主题标签由索引模型分配，因此“主题升温”是标签在样本里变多，不是科学价值变高；
报告正文与主题热度审计一起读这句话。

---

## 4.3 重测（2026-10-30，绑定新快照）

§4.1 与 §4.2 的数字取自 10-29 那份快照。第二天同一查询重抓后重跑同一套测量，
用来回答两个问题：**同一份输入能不能重复**，以及**换一天还叫不叫同一个队列**。

### 4.3.1 两次重抓（同一天，相差 6 分钟）

| 构建 | 记录数 | 去重后论文数 | 快照指纹 | 与另一次的交集 |
| --- | --- | --- | --- | --- |
| 07:46 | 1200 | 1191 | `sha256:875dc51be6093f4521b7042c6c5947838e5830a3c4d1a6ab76880f9b82d30a93` | 1191 / 1191 |
| 07:52 | 1200 | 1191 | `sha256:a12cf94a951cd188c260bd8989b35b1d840f467c58e4015edf645dcf501d3a88` | 1191 / 1191 |

指纹不同只因为 `cohort_id` 与 `filters` 进了指纹（`paging` 字段在 07:52 那次才记下）；
论文集合完全一致。结论：**page-number 分页在同一次会话内是确定的**，不是随机抽样。
「记录数 1200 与去重后 1191」的差别是 OpenAlex 把同一 DOI 列在两个 work id 下，见 §4.3.6。

### 4.3.2 与 10-29 那份快照相比

| 项 | 值 |
| --- | --- |
| 交集 | 91 / 1191（去重后） |
| 仅旧快照有 | 1106 |
| 仅新快照有 | 1100 |

七小时之后同一个查询返回的论文里，只有 91 篇与昨晚重合。这是本轮最重要的
真实数据发现之一：**相关性排序的样本是一个时刻，不是一个固定队列**。原因是索引在被
持续更新（引用计数、新入库、去重合并都在改变排序），而 1200 篇是从 12484 条命中里
截出来的前 1200——排序在尾部大量并列，任何索引变动都会换掉尾部一大批。

因此本报告的数字一律绑定指纹，而不是绑定“查询字符串”；`infl_verify` 与 manifests
的双摘要就是为这件事准备的。可复现的口径是“冻结快照 + 同一代码”，不是“同一查询”。

### 4.3.3 新快照上的测量（`sha256:a12cf94a…`）

| 测量 | 结果 |
| --- | --- |
| 记录数（去重后论文数）/ 有摘要 / 无 DOI | 1200（1191）/ 1121 / 42 |
| 分页 | 6 页，`paging = page`（无 API key，见 4.3.4） |
| 引用网络样本 | 20 篇，解析出 220 个节点（含 200 篇参考文献） |
| 参考文献解析率 | 0.1429 |
| 参考文献被引中位数 | 180 |
| PageRank 有百分位的节点 | 18 / 20（2 个孤立节点） |
| 解析耗时 | 27.0 s（礼貌限速 1 请求/秒） |
| 主题 | 63 个，30 个可读，36 个上升，锚点 2026 |
| OpenAlex vs Crossref 去重 | 5 / 5 标题一致、5 / 5 年份一致 |
| 冻结快照两次运行 | 指标与包指纹一致，`infl_verify` 14 个产物全通过 |

`reference_resolution_rate = 0.1429` 是**预算上限**，不是解析失败：测速脚本只解析前
200 个参考文献 work id，而已解析论文的参考文献远多于此。把它写成“解析率 14%”会让人
以为是索引缺口，所以这里点名它是脚本的取舍。

主题上升的前三名：Cosmology and Gravitation Theories（784 篇，+0.03）、Gamma-ray bursts
and supernovae（458 篇，+0.01）、Galaxies: Formation, Evolution, Phenomena（428 篇，+0.17）。
与 10-29 那次的 745/600/155 篇不同，原因同上：队列换了。

### 4.3.4 真实数据这一轮揪出的两个 bug

1. **4 MB 上限把大响应截断成“非法载荷”**。`ProviderPolicy.max_bytes` 原为 4 MB，
   而 per-page=200 加完整字段选择的 OpenAlex 页约 6.8 MB。`read(max_bytes)` 读满即停、
   不报错，于是 JSON 解析失败，工具报 `openalex_invalid_payload`——把我们的读法问题
   说成对方的载荷问题。修法：读上限+1 字节以判定是否被截断，`HttpResponse.truncated`
   为真时直接报 `response_too_large:<bytes>`；上限提到 32 MB。
   测试：`tests/research_influence/test_transport.py`（4 例）。
2. **游标分页需要 key**。OpenAlex 对 `cursor=` 翻过第一页返回
   `401 API key required for cursor paging`。修法：有 key 走 cursor、无 key 按页翻，
   并把 `paging` 写进快照的 `filters` 与 `notes`，读者不必猜样本是怎么走出来的。
   测试：`test_snapshot_search_pages_instead_of_keeping_the_first_page`（无 key → `page`）、
   `test_snapshot_search_prefers_cursors_when_a_key_is_configured`（有 key → `cursor`）。

这两个都不是设计推演出来的，是把真实抓取跑起来之后才暴露的：前者让 1200 篇的
快照根本建不起来，后者只有在真的翻到第二页时才出现。

### 4.3.5 队列漂移的机制（`infl_cohort_diff`）

§4.3.1 与 §4.3.2 的重叠数是手算的。手算的东西会过期：换成三份快照就要再数一遍，
而报告里的数字没人会去重跑。现在这一步是工具：

```
infl_cohort_diff(older="snia_real_20261030", newer="snia_real_20261030b")
infl_cohort_diff(older="snia_real",          newer="snia_real_20261030b")
```

它输出成员变动（保留 / 新增 / 流失 / Jaccard）、共享论文自身数值的移动（被引、
摘要、DOI、期刊、参考文献数）、以及**队列内位次的移动**——最后一项才是要害，因为
排序位次、前 10% 比例这类特征本来就是相对「这一批人」定义的。每次比较都会写出
`diffs/<older>__vs__<newer>.json` 与 `.md`，并给这次比较一个指纹
（`sha256:cbbe1fb1…` 与 `sha256:1efda14a…` 分别对应下面两行）。

| 指标 | 相隔 0.11 小时（07:46 → 07:52） | 相隔 14.35 小时（10-29 → 10-30） |
| --- | --- | --- |
| 去重后论文数（旧 / 新） | 1191 / 1191 | 1197 / 1191 |
| 保留 / 新增 / 流失 | 1191 / 0 / 0 | 91 / 1100 / 1106 |
| Jaccard | 1.0 | 0.0396 |
| 旧队列存活率 | 100% | **7.6%** |
| 共享论文被引变化 | 0 / 1191 篇变化 | 4 / 91 篇上升（最大 +61，合计 +64） |
| 队列内位次移动（中位 / P90） | 0 / 0 个百分点 | **37.3 / 42.5 个百分点** |
| 位次移动超过一个十分位的论文 | 0 / 1191 | **83 / 91** |
| 前 10% 重叠 | 119 / 119（100%） | 7 / 121（5.8%） |
| 被引排序 Spearman | 1.0 | 0.9663 |

两行合起来是这轮最值得写进报告的一句话：**6 分钟内重抓，成员、数值、位次三者完全
不动；14 小时后，成员的 92% 换掉了，而幸存的 91 篇论文自己的被引排序几乎没变
（Spearman 0.9663），但它们在队列里的位次中位移动了 37 个百分点，九成以上的论文
跨过了一个十分位。**

这说明「队列相对特征」的脆弱性不是来自数据本身，而是来自分母：论文自己的数字稳定，
它周围的人换了，位次就变了。任何引用百分位、前 10% 比例、相对位次这类特征，只要
队列重新抓过，就必须重算，而不能沿用上一次的数值。

### 4.3.6 顺带发现的重复记录

构建期还暴露出一个 OpenAlex 侧的事实：同一个 DOI 会以两个 work id 返回（合并尚未
完成时的重复条目）。10-30 那份 1200 条记录里有 **9 组**重复 DOI，10-29 那份有 3 组，
于是「1200 篇」实际是 1191 篇与 1197 篇不同的论文。重复条目会同时抬高论文数与被引
总数，所以 `cohort.py` 的构建路径现在按 DOI 合并，保留字段更全的一条（同字段数时取
被引更高者，再同则取更小的 work id，保证与分页顺序无关），并在快照 `notes` 里写下
`collapsed_N_duplicate_records`。报告中这两份快照的数字是合并后的
（1191 / 1197），而不是抓取时的 1200。

---

## 5. 偏差审计

### 5.1 基线（未注入）

| 状态 | 数量 |
| --- | --- |
| detected | 0 |
| not_detected | 5 |
| insufficient_evidence | 3 |
| cannot_assess | 2 |

两类 `cannot_assess` 是设计选择，不是遗漏：

- **自引通胀**：快照只有论文级被引，没有作者级引用明细；
- **作者人口学**：性别/族裔/职业年龄只能从姓名或机构推断，推断本身就会制造
  它声称要测量的偏差。审计的 `method` 字段直接写
  “Deliberately not attempted”，`conclusion` 以 “No demographic inference is performed.” 开头。

### 5.2 注入偏差（每次只注入一类，重新训练）

| 注入的偏差 | 审计状态 | 效应量（log，正=模型低估该组） | 严重度 |
| --- | --- | --- | --- |
| 语言/地域（非英语组被引 ×4） | **detected** | +0.643 | high |
| 主题热度（热门主题 ×5） | **detected** | +0.383 | medium |
| 期刊声望（高档期刊 ×3） | **detected** | +0.168 | low |
| 机构层级（剥离机构后 ×3） | **detected** | −0.178 | low |
| 缺失机制 MNAR（被引最高 120 篇隐藏全文） | **detected** | 缺失率 0.32→1.00，趋势 ρ=0.90 | high |
| 可见性（有全文者 ×3） | **insufficient_evidence** | 残差差 +0.083（CI 跨 0） | none |

**5 / 6 被命名**。第 6 类是本报告最值得读的一行：

模型能通过文本级特征区分“有全文/仅摘要”，于是它把这个优势**学进去了**——
残差因此变得很平（+0.083，CI 跨 0），而模型自己的预测差达到 0.49 log。
把这种情况报成“未检测到偏差”正是这类审计最该避免的错误。所以审计现在
输出 `status=insufficient_evidence` 且 `evidence.absorbed_into_features=true`，
并给出 `predicted_gap_log`：**优势看得见，但它是否应该存在，快照无法判定。**
对应测试 `test_a_visibility_advantage_the_model_absorbs_is_not_called_clean`。

MNAR 的判定也修过一次：五个桶里出现一个回落就判“证据不足”会让一个
0.32→1.00 的单调上升被丢掉。现在同时看严格单调性与秩趋势（ρ ≥ 0.80 即认），
并把两者都写进 evidence。

---

## 6. 可复现性

| 项 | 实测 |
| --- | --- |
| 同快照同种子两次运行 | `determinism = 1.0` |
| 指标指纹 | `sha256:6d50914bd88f15108070bdc1f0f921f02f3ceee64bdb3b2f1d9bf276f28fd3dd` |
| 数据包指纹（内容级） | `sha256:a1934d635f75e3264ee3bbabfebbcc3f3328af1421897ac4783a9b3949766921` |
| 输出文件 | 15 个（papers/predictions/factors/comparators/importance/bias_audit.csv/bias_audit.md/model_card/metrics/provenance/citation/topics/report/readme/manifest） |
| 模型拟合 | 1.8–3.5 s |
| 整条管道 | 4–12 s |

这两个哈希在两个**独立进程**里逐字节一致（`measure_benchmark.py` 连跑两次），
不是靠同一个进程内缓存蒙对的。

为保证这些哈希真的可比，修掉了六处会破坏指纹的缺陷：

1. **墙钟时间进指纹**：`retrieved_at` 每次运行都不同，导致同一篇论文两次运行
   指纹不同。现在 `VOLATILE_KEYS`（`retrieved_at/created_at/decided_at/...`）
   不进指纹，但仍保留在产物里当审计线索。
2. **本地路径进指纹**：`fulltext_path` 里的绝对路径会随工作目录变化。现在
   `LOCAL_PATH_KEYS`（`fulltext_path/source_paths/path/directory`）不进指纹，
   内容由 sha256 标识。
3. **数据包指纹建在字节上**：`report.md` 里必须写运行编号与起止时刻，所以两次
   内容完全相同的运行，字节摘要必然不同；把包指纹建在字节上，等于宣称
   “同输入同结论”在文件层面永远不成立。现在 `manifest.json` 给每个产物两种摘要
   ——`sha256`（磁盘字节，抓任何改动，含重写时间戳）与 `stable_sha256`（把运行
   编号与时刻替换成占位符后的内容摘要；JSON/JSONL 另外整齐地去掉 `run_id`、
   `*_seconds`、`retrieved_at` 等运行字段，CSV 本身没有这类字段）——
   `package_fingerprint` 按 `stable_sha256` 计算。

   `stable_sha256` 是**可重算的**：知道 manifest 里的 `run_id` 就能从磁盘文件重新
   算出来，因此它不是生产者的一句声明。`infl_verify(run_id)` 做的正是这件事——
   重读全部产物、重算两种摘要、再用验过的摘要重算包指纹，输出
   `bytes_ok/stable_ok` 两个判定：

   | 改动 | 字节摘要 | 内容摘要 | 结论 |
   | --- | --- | --- | --- |
   | 改写 `predictions.csv` 一行 | 变 | 变 | 结论被改 |
   | 只改写 `report.md` 里的时间戳 | 变 | 不变 | 只是重新盖了时间章 |
   | 改文件后顺手改 manifest 的摘要 | 一致 | 一致 | 包指纹对不上，仍判 drift |

4. **上传 PDF 的字节被当成了论文身份**：`paper:local:<sha256前缀>` 让同一篇论文
   重新导出一次就变成“另一篇”，队列指纹随之改变。现在身份取自**抽取出的正文**
   （`content_hash({"text": fulltext.text})`），字节摘要仍记在访问回执与
   `provenance.jsonl` 里，因为“用的是哪个文件”是另一个问题。
5. **`file://` URL 里嵌着工作目录**：`_stable_payload` 原本只按*键名*丢路径，
   而访问回执的 `url` 值本身就是 `file:///var/folders/...`。现在 `file://` 值被
   折叠成 `file://<local>/<内容寻址文件名>`。
6. **产物字节摘要进了记录指纹**：`sha256` 描述的是“这份文件”，不是“这篇论文说
   了什么”；`BYTE_DIGEST_KEYS` 把它从内容指纹里排除（审计轨仍保留）。

另外把墙钟耗时从报告正文移出（只留在 `metrics.json`，且不进摘要）：耗时说明机器
忙不忙，不说明结论；先前按秒分桶仍会让“1–10 秒”和“< 1 秒”两次不同。

**真实数据证据**（1202 篇天体物理队列，同一冻结快照、两次运行）：

| 场景 | 指标指纹 | 包指纹 |
| --- | --- | --- |
| 冻结快照（离线，无网络补充） | 一致 | 一致（`sha256:ecd2add5f4015ac7a6647bf0f1a020d5dd3315279c2a0dfe37e88a42bc066156`，14 个产物经 `infl_verify` 全部复核通过） |
| 联网（引用网络在线补充） | 输入相同才一致 | 同左 |

联网这一组故意留着：在线接口两次返回的引用状态可能不同（实测一次 `ok=2`、
一次 `unavailable=2`），**输入变了，任何指纹都不该一致**。所以“可复现”的口径
是冻结快照，而不是“跑两次一定一样”——后者会把外部服务的抖动伪装成我们的确定性。

---

## 7. 诚实边界（先说清楚，免得被当成结论）

1. **队列是合成的**。0.824 的 Spearman 证明管道能学到结构，不证明真实天体物理
   论文的可预测性。
2. **真实抓取只在有代理的机器上做过**。七级获取阶梯的每一级都有离线测试；§4.1
   的两篇论文与 1202 篇队列是经操作者本机 HTTP 代理真实抓取的，但默认运行仍是离线
   拒网，凭据（ADS/TDM/CORE 等）全部缺失，付费墙只验证到“直连 403 且不绕”。
3. **PDF 例子里没有付费墙**。付费墙行为只被验证到“直连返回 403 且我们不绕”，
   即 `AccessRoute.ABSTRACT_ONLY` / `METADATA_ONLY` / `ACQUISITION_REQUESTED`
   三条降级出口，以及影子图书馆域名拒绝。
4. **图表数字化未进入本节指标**。影响力侧不承诺读图；这一能力在
   `jw/scientific_data/figures.py`，其误差指标见
   `docs/architecture/2026-10-29-scientific-data-snia-benchmark-report.md`。
5. **偏差审计只覆盖九类 + 一类显式拒绝**。真实评价体系里还有基金、机构政策、
   审稿偏好等通路，快照里没有对应字段，一律不在本报告能力范围内。
6. **区间覆盖率是这一队列上的性质**。换队列（尤其时间跨度或期刊分布不同）后
   必须重跑 `THRESHOLDS` 校验，不能直接引用 0.803。

---

## 8. 复现命令

```bash
# 计分板（打印 JSON，包含本报告全部数字）
MPLCONFIGDIR=/tmp/mpl .venv/bin/python tests/research_influence/fixtures/measure_benchmark.py

# 基准测试（阈值断言）
MPLCONFIGDIR=/tmp/mpl .venv/bin/python -m pytest tests/research_influence/test_benchmark.py -q

# 全量（影响力 + 科学数据整合）
MPLCONFIGDIR=/tmp/mpl .venv/bin/python -m pytest tests/research_influence tests/scientific_data -q

# 仓库门禁
.venv/bin/python -m ruff check --select F jw tests
.venv/bin/python -m ruff format --check jw tests
```

阈值集中在 `tests/research_influence/test_benchmark.py::THRESHOLDS`，
本报告的表格与这些阈值由同一个测试文件断言，防止文档与代码漂移。
