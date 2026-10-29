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

真实队列需要网络：`infl_snapshot_search` 走 OpenAlex/Crossref/Unpaywall 三源，
当前沙箱的出口被拒（`Tunnel connection failed: 503`），所以本报告不含真实抓取
结果。抓取逻辑本身有 51 个离线测试覆盖（`tests/research_influence/test_providers.py`
等，用注入的假 Transport，零网络）。

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
| 指标指纹 | `sha256:fa4d09f43868a7b13ad4c1ec41577cb9f1ce0e033411fe5c835f2d64a91c52cb` |
| 数据包指纹 | `sha256:7ac1c9d68c247a436584dd27797af6c31f2f69cb07b7f19509fd4456a11596dd` |
| 输出文件 | 13 个（papers/predictions/factors/comparators/importance/bias_audit(×2)/model_card/metrics/provenance/report/readme/manifest） |
| 模型拟合 | 1.8–3.5 s |
| 整条管道 | 4–12 s |

为保证这些哈希真的可比，修掉了两处会破坏指纹的缺陷：

1. **墙钟时间进指纹**：`retrieved_at` 每次运行都不同，导致同一篇论文两次运行
   指纹不同。现在 `VOLATILE_KEYS`（`retrieved_at/created_at/decided_at/...`）
   不进指纹，但仍保留在产物里当审计线索。
2. **本地路径进指纹**：`fulltext_path` 里的绝对路径会随工作目录变化。现在
   `LOCAL_PATH_KEYS`（`fulltext_path/source_paths/path/directory`）不进指纹，
   内容由 sha256 标识。

---

## 7. 诚实边界（先说清楚，免得被当成结论）

1. **队列是合成的**。0.824 的 Spearman 证明管道能学到结构，不证明真实天体物理
   论文的可预测性。
2. **没有真实网络抓取**。七级获取阶梯的每一级都有离线测试，但本报告没有一条
   真实 DOI 的抓取记录。真实抓取需要出口或用户本地跑脚本。
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
