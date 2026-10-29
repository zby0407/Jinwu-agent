# 科研影响力分析与偏差解释：交付说明（对照赛题逐条）

- 日期：2026-10-29
- 分支：`feat/scientific-data-integration-20261029`
- 内核：`jw/research_influence/`（schema `jw.research-influence/v1`）
- 工具面：`jw/tools/influence.py`（14 个 `infl_*` 工具，`include_in_main=False`）
- 子 Agent：`jw/subagents/research-influence/`（bundle + agent YAML）
- 基准与指标：`tests/research_influence/test_benchmark.py`、
  `docs/architecture/2026-10-29-research-influence-benchmark-report.md`
- 同族前作（方向 1A）：`jw/scientific_data/`，
  `docs/architecture/2026-10-29-scientific-data-delivery-overview.md`

---

## 1. 赛题要求 → 实现对照

| 赛题要求 | 实现位置 | 可验证证据 |
| --- | --- | --- |
| 用户上传论文 | `infl_parse_pdf(paths)`、`workflow.paper_from_upload()` | `test_uploaded_pdf_becomes_a_parsed_paper`、`test_upload_participates_in_the_run` |
| 自动提取题目 | `paper.title_line()`（文本层首行，LOW 置信 + 页码 locator） | `test_a_pdf_title_line_is_used_and_flagged_as_weak_evidence` |
| 提取摘要 | `providers/openalex.py`（倒排索引还原）、`crossref.py`（剥 JATS） | `test_providers.py`（16 例） |
| 提取正文 | `paper.sections_from_pages()` → 7 个规范章节 | `test_sections_are_split_and_text_is_preserved` |
| 提取作者 | `PaperRecord.authors`（姓名/机构/国家/ORCID/通讯/序号） | `test_paper.py::test_full_text_paper_carries_sections_and_open_data` |
| 提取期刊 | `PaperRecord.venue`（名称/类型/出版商/ISSN/OA） | 同上 |
| 提取参考文献 | 三级优先级：Crossref 存缴 > OpenAlex `referenced_works` > PDF 文本 | `test_crossref_reference_deposit_beats_text_parsing` |
| 引用关系 | `citation.py`：批量解析参考文献与引用者、`cited_by_count`、`counts_by_year`、PageRank | `test_citation.py`（16 例）、真实运行见 §3.1 |
| 主题演化 | `topics.py`：每主题年份分布、近窗占比、相对增长、被引中位数 | `test_topics.py`（8 例）、真实运行见 §3.2 |
| 开放数据情况 | `paper.open_data_from_text()`（Zenodo/Figshare/Dryad/GitHub/MAST/VizieR/…） | `test_open_data_finds_repositories_and_kinds` |
| 预测影响力 | `model.fit_model()`（分位梯度提升，3 年窗口） | `test_model.py`（14 例） |
| **不只给一个分数** | 输出 10/50/90 分位区间 + 80% 区间 | `test_benchmark.py::test_model_reaches_the_agreed_thresholds` |
| 说明影响因素 | `explain.py`：每因素给观测值/参考值/Δ/95% CI/份额 | `test_explain.py`（15 例） |
| 偏差识别要具体 | `bias.py`：十类审计，每类给方法+证据数字+结论+严重度 | `test_bias.py`（12 例）、`test_benchmark.py` 注入用例 |
| 是否不公平/不稳定 | 时间右删失、缺失机制 MNAR、区间校准、排名稳定性、折间方差 | 基准报告 §3.3、§5.2、§6 |
| 结果可复现 | 指纹排除墙钟与本地路径；`determinism=1.0` | `test_benchmark.py::test_the_run_is_reproducible` |

赛题特别指出“不应该只给出一个分数”。本实现里**没有**任何单点输出：

- 报告第 3 节标题就是“预测结果（区间，而不是一个分数）”；
- `predictions.csv` 同时给 `low/median/high` 与 `interval_width`；
- 区间窄/宽本身是结论（证据多的论文区间窄，仅有元数据的论文区间宽一个量级）；
- 区间穿越（`quantile_crossings`）与经验覆盖率一起上报，不隐藏。

### 1.1 引用网络读出来的东西（`citation.py`）

赛题把“引用网络”列进必须能解析的内容，而它对解释层同样关键：十二次引用来自
被引中位数 591 的文献，与十二次引用来自没人读的文献，是两件事，而这件差别在被引
次数里完全看不见。实现给出三组测量，全部可复算：

| 测量 | 字段 | 含义 | 边界 |
| --- | --- | --- | --- |
| 参考文献质量 | `median_referenced_citations`、`references_in_top_decile` | 它引用的文献本身被引多少；有多大比例位于队列前 10% | 解析不到的条目不算 0 被引；快照里没有参考文献列表时该行为空 |
| 被引者质量 | `citing_count`、`mean_citing_citations`、`self_citations` | 谁在引用它、这些引用者被引多少、其中多少是同作者自引 | 引用者只抽一页；自引只按姓氏重合做**下界**估计 |
| 网络位置 | `pagerank`、`pagerank_percentile` | 在解析到的参考文献图上的 PageRank 与队列位次 | 只在解析成功的子图上计算，参考文献解析差的论文位次偏低 |

真实运行（1202 篇冻结核物理队列 + 两篇上传 PDF，经操作者本机代理）：

- `https://doi.org/10.48550/arXiv.1509.00001` → 6/6 条参考文献解析成功，参考文献被引
  **中位数 591**、17% 位于队列前 10%，1 篇引用者（自引 1 篇），PageRank 位次 48.4；
- 2024 年那篇（TSP）有 359 条**PDF 解析出的**参考文献，但索引没有它的参考文献列表，
  解析到 0 条 —— 报告直接写“该论文的 359 条参考文献一条也没能解析成可核算的作品”，
  而不是给出一个 0 被引的假数字。

### 1.2 主题演化（`topics.py`）

主题标签来自索引（OpenAlex topics，模型自动分配），因此“主题在升温”说的是**标签在
样本里变多**，不是科学价值变高；这句话写进了报告正文，与主题热度偏差审计一起读。

真实运行（1200 篇、60 个主题、27 个样本量足够、28 个上升，窗口锚点 2021）：

| 主题 | 论文数 | 近 3 年占比 | 更早占比 | 相对变化 | 被引中位数 |
| --- | --- | --- | --- | --- | --- |
| Cosmology and Gravitation Theories | 745 | 68% | 59% | +0.15 | 32 |
| Gamma-ray bursts and supernovae | 600 | 47% | 51% | −0.07 | 39 |
| Neutrino Physics Research | 155 | 18% | 11% | +0.64 | 42 |
| Stellar, planetary, and galactic studies | 268 | 15% | 25% | −0.39 | 48 |

窗口锚点取“仍有可观样本量的最新年份”，不是参考年也不是最大年份：前者会让 2012-2021
的快照把所有主题算成灭绝，后者会让两篇 2024 年论文干同样的事。这个选择写在报告里。

---

## 2. 非开放论文的获取路径（赛题之外的硬要求）

用户明确提出“一些不开放的文章也是要考虑获取的”。实现为**七级阶梯 + 两个出口**，
每一级都是被显式记录的状态，而不是“抓不到就算了”：

| 级 | `AccessRoute` | 判定依据 |
| --- | --- | --- |
| 0 | `user_supplied` | 用户自己上传的 PDF（最高优先级，`provider=operator`） |
| 1 | `open_access_gold` | 出版商站点 + 开放许可（`host_type=publisher` + `license`） |
| 2 | `open_access_green` | 机构库/预印本库的已发表版 |
| 3 | `free_to_read` | 出版商站点可读但无开放许可（bronze） |
| 4 | `preprint` | 正式 DOI 反查到的预印本，note 写 `preprint_is_not_the_version_of_record` |
| 5 | `publisher_tdm` | 用户自己的 TDM 凭据（环境变量读取，不存不代管） |
| 6 | `institutional_proxy` | 用户校园网/EZproxy 出口 |
| 7 | `abstract_only` | 只有摘要（元数据级特征仍全部可用） |
| 8 | `metadata_only` | 连摘要都没有 |
| 9 | `acquisition_requested` | 生成人工获取清单：馆际互借 → 作者索取 → 图书馆复印 → 出版商购买 |

三条拒绝写进代码常量 `DENIED_HOSTS` 与 `refuse_reason()`，并有测试断言：
**不接 Sci-Hub/LibGen 类影子图书馆、不共享或借用第三方凭据、不违反 robots/TOS**。
真实网络验证：OUP 付费 PDF 直连返回 403，我们不绕。

**降级是被测量的**：拿不到全文时，全文级特征（开放数据声明、方法数值密度、
自引表述、章节结构、可读性）缺失并记 `missing_reason=full_text_unavailable`，
而不是填 0。全文可得性非随机这一点直接进入偏差审计（`missing_mechanism`、
`open_access_visibility`），见基准报告 §5.2。

---

## 3. 十阶段处理流程

`source → extract → enrich → cohort → features → model → explain → bias → report → package`

| 阶段 | 模块 | 产物 |
| --- | --- | --- |
| source | `access.py`、`fulltext.py` | `AccessDecision`（路线 + 每次尝试） |
| extract | `paper.py`、`providers/` | `PaperRecord`（字段级 provenance + missing） |
| enrich | `providers/openalex.py` 等 | 被引数、`counts_by_year`、主题、参考 |
| cohort | `cohort.py` | 冻结快照 `cohorts/<name>.json`（含指纹） |
| features | `features.py` | 23 个特征（元数据级 + 文本级，带 `absent_reason`） |
| model | `model.py` | `ModelCard`（特征、偏移、种子、队列指纹、指标） |
| explain | `explain.py` | 每主体的因素 Δ + 95% CI + 对照论文 + 重要性 |
| bias | `bias.py` | 十类审计（九类检测 + 一类显式拒绝） |
| report | `report.py` | 中文八节报告 |
| package | `package.py` | 13 个文件 + `manifest.json`（每个文件的 sha256） |

工具面 `infl_*` 把这十阶段拆成可单独调用、可中途接管的一步步：
`infl_capabilities / infl_cohorts / infl_snapshot_synthetic / infl_snapshot_search /
infl_snapshot_import / infl_fetch_paper / infl_parse_pdf / infl_run / infl_status /
infl_report / infl_bias / infl_explain / infl_package / infl_acquisition_request`。

其中两个快照入口是分开的，因为它们服务于不同的环境：
`infl_snapshot_search` 需要网络（默认拒绝），`infl_snapshot_import` **完全离线**，
读操作者录好的 OpenAlex `/works` 导出（JSON / JSONL / API 信封），冻结成同一格式的
快照。这样“抓取”可以发生在有出口的机器上，而模型运行永远不依赖实时 API。

---

## 4. 结构化输出（数据包）

| 文件 | 内容 |
| --- | --- |
| `papers.csv` | 队列内每篇论文的元数据、章节长度、开放数据、可得路线、特征覆盖 |
| `predictions.csv` | `low/median/high`、`interval_width`、窗口状态、是否主体 |
| `factors.csv` | 每主体每因素的观测值/参考值/Δ/CI/方向/份额 |
| `comparators.csv` | kNN 相似论文对照（含观测被引） |
| `importance.csv` | 23 个特征的整体置换重要性（含是否显著） |
| `bias_audit.csv` / `bias_audit.md` | 十类审计的状态、方法、证据数字、结论、严重度 |
| `model_card.json` | 特征、训练/留出 id、超参、种子、队列指纹、指标 |
| `metrics.json` | 全部指标 + `determinism` + `decile_calibration` + 指纹 |
| `provenance.jsonl` | 每个（论文，字段，来源）一行，带 page/section |
| `report.md` | 中文报告（八节） |
| `README.md` | 数据包自述：怎么读、哪些不能读 |
| `manifest.json` | 13 个文件的 sha256 + 阶段回执 |

---

## 5. 诚实边界（会写进报告与 README，不是补丁说明）

1. 基准队列是**合成**的：证明了管道与校准机制，不证明真实论文的可预测性。
2. 本报告**不含真实网络抓取**：沙箱出口被拒（`503`），抓取逻辑靠 51 个离线测试
   保证正确性（注入假 Transport，零网络）。真实快照的落地方式是
   `infl_snapshot_import`：在有出口的机器上导出 OpenAlex `/works` 记录，
   import 进本工作区即可冻结成同样的 `cohorts/<name>.json`（有测试覆盖原始记录
   与已解析记录两种入参）。
3. 付费墙只验证到“403 且不绕过”，以及三条降级出口与影子图书馆拒绝。
4. `自引通胀` 与 `作者人口学` 两类审计是 **cannot_assess**：前者缺作者级引用
   明细，后者拒绝从姓名推断（推断本身会制造它声称测量的偏差）。
5. 偏差审计只覆盖九类 + 一类显式拒绝；基金、机构政策、审稿偏好等通路没有字段。
6. 区间覆盖率是这一队列上的性质，换队列必须重跑阈值校验。

---

## 6. 如何运行

```bash
# 1) 合成队列跑一遍（4–12 秒），产出数据包与中文报告
infl_snapshot_synthetic(name="demo", count=300, seed=5)
infl_run(cohort="demo")

# 2) 传自己的 PDF（最高优先级路线 user_supplied）
infl_parse_pdf(paths=["workspace/uploads/paper.pdf"], name="mine")
infl_run(cohort="demo", uploads=["paper.pdf"])

# 3) 查看结果
infl_status(); infl_report(); infl_bias(status="detected"); infl_explain()

# 4) 抓不到全文时，生成人工获取清单
infl_acquisition_request(doi="10.1093/mnras/stv1947")

# 5) 有出口的机器上导出 OpenAlex /works 记录后，离线冻结成真实快照
infl_snapshot_import(paths=["exports/openalex_works.json"], name="snia_real",
                     query="supernova Ia light curve cosmology", reference_year=2024)
```

命令行等价入口：

```bash
MPLCONFIGDIR=/tmp/mpl .venv/bin/python tests/research_influence/fixtures/measure_benchmark.py
MPLCONFIGDIR=/tmp/mpl .venv/bin/python -m pytest tests/research_influence -q
```

---

## 7. 与方向 1A（科学数据整合）的关系

两个内核共享同一套纪律，但代码独立、schema 独立（`jw.scientific-data/v1` 与
`jw.research-influence/v1`），互不依赖，可以分别作为独立作品提交：

| 纪律 | 1A 科学数据整合 | 1B 科研影响力 |
| --- | --- | --- |
| 不覆盖原值 | `value_raw` / `unit_raw` 保留 | `Provenance.raw` 保留原文 |
| 缺失带原因 | `MissingReason` 五类 | `MissingReason` 八类（含 `full_text_unavailable`） |
| 不确定必上报 | 不确定度列 + 数字化误差 | 区间宽度 + 95% CI + 折间方差 |
| 高风险零自动 | 自动只做四类可逆修正 | 不自动推断人口学、不自动改数据 |
| 逐条溯源 | `provenance.jsonl` | `provenance.jsonl`（字段级，带页码） |
| 可复现 | 同冻结输入同哈希 | 同冻结快照同哈希 |
