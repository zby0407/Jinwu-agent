# 科研影响力内核规格（jw.research-influence/v1）

- 日期：2026-10-29
- 代码：`jw/research_influence/`
- 工具面：`jw/tools/influence.py`（14 个 `infl_*`，含离线的 `infl_snapshot_import`）
- 子 Agent：`jw/subagents/research-influence/`
- 基准与指标：`docs/architecture/2026-10-29-research-influence-benchmark-report.md`

本文档只描述**接口合同与不变量**。实现细节以代码为准，指标以基准报告为准。

---

## 0. 六条硬性不变量

这六条决定了所有模块的形状，违反其中任何一条都算缺陷，不算取舍。

1. **不覆盖原始证据。** 抽取值与原文并存：`Provenance` 记录哪一路径、哪一页、
   哪个方法、哪个 sha256 产生了这个值；PDF 文本层的问题（如 `T ype`）原样保留。
2. **缺失是带类型的。** 拿不到就是 `MissingReason` 八类之一
   （`not_reported / not_parsed / not_applicable / not_found /
   full_text_unavailable / not_configured / not_indexed / undetected`），
   绝不用 0 或空串冒充。
3. **不确定必须上报。** 预测给区间不给点，因素给 Δ 与 95% CI，模型给折间方差，
   分位穿越计数、区间经验覆盖率、留出/训练行数都进产物。
4. **高风险零自动。** 不自动推断作者人口学、不自动改写文本、不自动用影子图书馆、
   不自动把“模型吸收掉的偏差”说成干净。
5. **逐条溯源。** `provenance.jsonl` 每行是 (论文, 字段, 来源) 三元组，
   可定位到页/章节/字符区间。
6. **确定性可复现。** 同冻结快照同种子 → 同分数、同报告、同数据包指纹；
   墙钟时间与本地绝对路径不进指纹。

---

## 1. 模块与接口

| 模块 | 职责 | 关键公开接口 |
| --- | --- | --- |
| `contracts.py` | 类型与序列化 | `AccessRoute`、`MissingReason`、`ConfidenceBand`、`Locator`、`Provenance`、`PaperRecord`、`AccessDecision`、`merge_records`、`summarise`、`content_hash`、`normalise_doi` |
| `transport.py` | HTTP 抽象（可注入） | `Transport`(Protocol)、`UrllibTransport`、`PoliteTransport`、`HttpResponse` |
| `providers/` | 元数据源适配器 | `openalex.fetch_work/search_works/parse_work/locations_of`、`crossref.fetch_work/parse_work`、`unpaywall.*`、`europepmc.*`、`arxiv.*`、`optional.*`（缺凭据一律 `NOT_CONFIGURED` 且**不发请求**） |
| `access.py` | 七级获取阶梯 | `AccessPolicy.refuse_reason`、`classify_location`、`candidates_from_*`、`rank_candidates`、`decide_access`、`acquisition_request`、`DENIED_HOSTS` |
| `fulltext.py` | 全文本地化 | `fetch_fulltext`、`read_local_fulltext`、`verify_fulltext`、`FulltextResult` |
| `paper.py` | 论文装配 | `build_paper`、`sections_from_pages`、`references_from_text`、`open_data_from_text`、`title_line`、`detect_kerning_splits`、`section_provenance`、`normalise_section_name` |
| `cohort.py` | 冻结队列快照 | `CohortSnapshot`、`build_snapshot`、`select_cohort`、`citations_within`、`is_right_censored`、`synthetic_cohort`、`openalex_snapshot`（接受原始或已解析的 OpenAlex 记录） |
| `features.py` | 23 个特征 | `FEATURES`、`feature_matrix`、`feature_row`、`targets`、`venue_tier` |
| `model.py` | 分位模型 | `fit_model`、`cross_validate`、`InfluenceModel.predict/predict_matrix/metrics_for`、`ModelCard`、`PredictionSet`、`InsufficientTrainingData` |
| `explain.py` | 解释层 | `explain_paper`、`permutation_importance`、`partial_dependence`、`counterfactual`、`rank_stability`、`rank_stability`/`importance_rank` |
| `citation.py` | 引用网络测量 | `resolve_works`、`citing_works`、`pagerank`、`cohort_pagerank`、`reference_quality`、`self_citation_count`、`citation_profile`、`CitationProfile` |
| `topics.py` | 主题演化 | `topic_trends`、`paper_topic_position`、`trend_note`、`TopicTrend`、`RECENT_YEARS` |
| `bias.py` | 十类偏差审计 | `audit_bias`、`AUDITS`、`BiasReport`、`BiasFinding` |
| `report.py` | 中文报告 | `build_report`、`SECTION_TITLES`、`format_interval` |
| `package.py` | 数据包 | `write_package`、`paper_rows`、`provenance_rows`、`package_fingerprint`、`manifest_digest` |
| `workflow.py` | 编排 | `run_influence`、`RunOptions`、`RunResult`、`paper_from_upload`、`load_run`、`stages_of`、`synthetic_run` |

### 1.1 依赖方向（单向，无环）

```
transport ← providers ← access ← fulltext ← paper ← cohort ← features ← model ← explain ← bias ← report ← package ← workflow
```

上层可 import 下层，反向不行。`contracts.py` 被所有层依赖；`bias.py` 只使用
`model.spearman` 与 `features.venue_tier`，不读 `InfluenceModel` 内部结构。

---

## 2. 获取阶梯语义

`AccessRoute` 十态，`rank()` 越小越优先：

| rank | 路线 | `has_full_text` | 说明 |
| --- | --- | --- | --- |
| 0 | `user_supplied` | ✅ | 操作者上传，最高优先 |
| 1 | `open_access_gold` | ✅ | 出版商站点 + 开放许可 |
| 2 | `open_access_green` | ✅ | 机构库/预印本库的已发表版 |
| 3 | `free_to_read` | ✅ | 可读但无开放许可 |
| 4 | `preprint` | ✅ | 预印本，note 必带 `preprint_is_not_the_version_of_record` |
| 5 | `publisher_tdm` | ✅ | 用户自己的 TDM 凭据（环境变量） |
| 6 | `institutional_proxy` | ✅ | 用户校园网/EZproxy 出口 |
| 7 | `abstract_only` | ❌ | 有摘要，元数据特征完好 |
| 8 | `metadata_only` | ❌ | 进入 `acquisition_requested` 的前置态 |
| 9 | `acquisition_requested` | ❌ | 已生成人工获取清单 |

**拒绝规则**（`AccessPolicy.refuse_reason`，有测试断言）：
影子图书馆域名（`sci-hub.*`、`libgen.*`、`z-lib`、`b-ok`、`annas-archive` 等）→
`shadow_library_refused:<host>`；非 http/https/file → `scheme_not_allowed`；
不在 allowlist → `host_not_allowlisted`；`allow_network=False` → `network_disabled`。

**降级是状态，不是异常。** 没有候选就落到 `abstract_only`/`metadata_only` 并
写 note；`acquisition_request()` 给出四步清单：
`interlibrary_loan → author_request → library_copy → publisher_purchase`。

---

## 3. 抽取合同

- **章节**：`CANONICAL_SECTIONS` 内的规范名（`abstract/introduction/methods/
  results/discussion/conclusions/data_availability/acknowledgements/references/
  appendix/body`），`normalise_section_name` 先精确后按别名长度降序前缀匹配
  （上限 `len(alias)+24`，否则 “2. Observations and data reduction” 匹配不上 methods）。
- **标题三级证据**：索引记录（HIGH）> PDF 文本层首行（LOW + 页码 locator）>
  无（`MissingReason.NOT_FOUND`）。像标题的行一律拒绝：编号标题（`1. Introduction`）、
  规范章节名、以冒号结尾、`abstract/preprint/arxiv:/doi:/received` 前缀。
- **参考文献三级优先级**：Crossref 存缴 > OpenAlex `referenced_works` > PDF 文本，
  每级在 `notes` 写明来源（`references_from_crossref_deposit` /
  `references_from_openalex_ids` / `references_parsed_from_pdf_text`）。
- **开放数据**：只在 `data_availability/software/appendix` 段落内找 URL，
  仓库词表 → `OpenDataKind.DATA|CODE|SUPPLEMENTARY`，带 `Locator(section, char_start)`。
- **PDF 缺陷只报不改**：`detect_kerning_splits` 输出
  `pdf_text_kerning_suspect:<X yz>@p<page>`。理由：`T ype` 与合法短语
  `A homogeneous sample` 形态相同，字典不是证据。

---

## 4. 队列与目标

- `CohortSnapshot(cohort_id, papers, query, source, created_at, reference_year,
  horizon_years, fingerprint())`，写入 `<workspace>/research_influence/cohorts/<name>.json`。
- 三条建快照路径：`synthetic_cohort()`（离线、可复现、只用于管道验证）、
  `infl_snapshot_search`（联网抓 OpenAlex）、`infl_snapshot_import`
  （离线导入操作者的导出文件，因此“抓取”与“建模型”可以在不同机器上完成）。
  导入时按 `authorships / primary_location / abstract_inverted_index` 判断是原始
  记录还是已解析记录——`cited_by_count` 两种形态都有，不能用于判别。
- 目标 = **发表后 3 年被引数**（`citations_within`），不用总被引，避免右侧泄漏。
- 右删失：`is_right_censored(paper, reference_year, horizon)` 为真者只作描述性统计，
  **不进训练标签**。
- 特征 23 个，分元数据级与文本级；文本级不可用时 `absent_reason=
  MissingReason.FULL_TEXT_UNAVAILABLE`，不是 0。

---

## 5. 模型合同

- 三个独立分位回归器（0.1/0.5/0.9），单调修复 `median=max(median,low)`、
  `high=max(high,median)`，交叉数写进 `PredictionSet.quantile_crossings`。
- `fit_model(..., min_rows=30)` 不足则抛 `InsufficientTrainingData`，
  管道捕获后 `model=None` 并在报告写“未训练模型，不给出任何预测数字”。
- `metrics_for(papers, subset="holdout")` 默认只用留出集，指标字典必带
  `eval_subset / scored_rows / in_sample_rows / holdout_rows`。
- `ModelCard` 记录特征名、偏移、种子、超参、`training_paper_ids`、
  `holdout_paper_ids`、队列指纹、指标、`fingerprint()`。
- 主指标：Spearman、80% 区间经验覆盖率、log1p MAE；Top-10% lift 只作描述
  （折间 sd ≈ 2，不稳）。

---

## 6. 解释合同

每个主体输出 6 个因素，每个因素必须有：

- 观测值与队列参考值；
- `delta_log` 与 `delta_counts`；
- 95% CI（对参考值做 bootstrap，`DEFAULT_BOOTSTRAP=100`）；
- `direction ∈ raises|lowers|neutral`、`share`、一句人话。

附加证据：整体置换重要性（跨 5 种子 × 5 重复，CI 排除 0 才标 `supported`）、
PDP、反事实、kNN 相似论文对照（距离升序、排除自身）、排名稳定性
（跨种子排名两两 Spearman 均值）。

**不用 SHAP**（环境无 shap/lightgbm/xgboost），全部自实现，因此每条数字都能
在测试里被重算。

---

## 7. 偏差审计合同

十个审计，每个产出 `BiasFinding(name, label, status, severity, method, evidence,
conclusion, recommendation)`：

- `status ∈ detected | not_detected | insufficient_evidence | cannot_assess`
- 判定：组样本 < 6 → `insufficient_evidence`；|Δ| ≥ 0.15 且 CI 排除 0 → `detected`；
  |Δ| ≥ 0.15 但 CI 跨 0 → `insufficient_evidence`；否则 `not_detected`。
- 残差定义 `residual = observed - predicted`（log1p 空间），正残差 = 模型低估。
- 两个固定 `cannot_assess`：`self_citation`（缺作者级引用明细）、
  `author_demographics`（**拒绝**从姓名推断，`method` 写 “Deliberately not attempted”）。
- `open_access_visibility` 特殊：若模型已吸收该优势（`|predicted_gap| ≥ 0.30`
  而残差差 CI 跨 0），判 `insufficient_evidence` 且 `absorbed_into_features=true`。
- `missing_mechanism`（MNAR）：五个预测分位桶的全文缺失率，
  `spread ≥ 0.20` 且（严格单调 或 秩趋势 ρ ≥ 0.80）→ `detected`。

---

## 8. 数据包与复现

13 个文件：`papers.csv`、`predictions.csv`、`factors.csv`、`comparators.csv`、
`importance.csv`、`bias_audit.csv`、`bias_audit.md`、`model_card.json`、
`metrics.json`、`provenance.jsonl`、`report.md`、`README.md`、`manifest.json`。
`manifest.json` 的 `artifacts` 是 `[{role, path, sha256}]`，跳过自身。

指纹稳定性靠两个键集：

- `VOLATILE_KEYS = {retrieved_at, created_at, decided_at, generated_at, saved_at}`
- `LOCAL_PATH_KEYS = {fulltext_path, source_paths, path, directory}`

`_stable_payload()` 递归剔除后计算 `content_hash`。这两个键集仍保留在产物里
当审计线索，只是不进指纹。

---

## 9. 扩展点

- **换领域**（如高能物理、气候）：`cohort.openalex_snapshot(query=..., filter_expression=...)`
  改查询即可；`features.FEATURES` 增加领域特征；`bias.AUDITS` 可按需增删，
  未知审计名静默忽略。
- **换数据源**：实现 `providers/base.py` 的约定（返回 `ProviderResult`，
  缺凭据返回 `NOT_CONFIGURED` 且不发请求），在 `providers/__init__.py` 导出。
- **换模型**：替换 `model.fit_model` 内部实现即可，只要 `InfluenceModel` 暴露
  `predict/predict_matrix/metrics_for` 与 `card.feature_names`。
- **离线/联网**：所有网络访问都经过 `Transport`；测试注入假 Transport，
  因此基准可完全离线复跑。
