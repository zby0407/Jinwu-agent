# 科学数据整合内核 · 模块接口规格（v1）

状态：实现中 · 分支 `feat/scientific-data-integration-20261029`
范围：`jw/scientific_data/`（通用整合内核）＋ `jw/tools/scientific_data.py`（工具面）＋
`jw/subagents/**` 中的 `scientific-data` 子 Agent。

本文件是**唯一的模块接口合同**。任何模块只允许依赖 `contracts.py`、`domain.py`
和本文件列出的签名；不得依赖其它模块的内部函数。

## 0. 分层与数据流

```text
requirement contract          (contracts.DataRequirementContract)
        │
        ├─ 1 discovery   sources.py      → SourceRecord(retrieval=candidate)
        ├─ 2 acquisition sources.py      → SourceRecord(retrieval=acquired, sha256)
        ├─ 3 extraction  tabular.py / pdf_tables.py / figures.py → ExtractedTable
        ├─ 4 semantics   semantics.py   → FieldMapping
        ├─ 5 identity    entities.py    → EntityRef
        ├─ 6 alignment   alignment.py   → ObservationRecord（长表）
        ├─ 7 quality     quality.py     → QualityIssue
        ├─ 8 repair      repair.py / human_loop.py → RepairLedgerEntry
        ├─ 9 revalidate  quality.py（增量重跑）
        └─ 10 package    package.py     → 标准输出数据包 + run_manifest
```

所有阶段共享 `contracts.IntegrationLedger`；每个阶段写一条
`StageReceipt`（输入/输出指纹、计数、产物、错误），支持断点续跑与幂等复用。

硬性原则（每个模块都必须满足，否则视为未完成）：

1. **不覆盖原值**：规范化结果与 `value_raw/unit_raw/time_raw` 并存。
2. **缺失必带原因**：`ObservationRecord.missing_reason`，禁止裸 `None` 语义。
3. **不确定必须上报**：低置信度映射/身份/坐标轴 → `QualityIssue`
   （`repair_mode` 由 `contracts.default_repair_mode` + 合同 override 决定）。
4. **高风险动作零自动执行**：只有 `RepairMode.AUTO_SAFE` 可自动 apply。
5. **纯函数优先**：解析/单位/对齐逻辑不访问网络、不调用模型、不读写工作区外路径。
6. **确定性**：相同输入 → 相同排序、相同哈希、相同 CSV 字节（时间戳字段除外）。

## 1. `contracts.py`（已完成，勿改签名）

已提供：`DataRequirementContract`、`ObservationField`、`SourceRecord`、
`ExtractedTable`、`ProvenanceRef`、`EntityRef`、`EntityAlias`、`FieldMapping`、
`ObservationRecord`、`QualityIssue`、`RepairLedgerEntry`、`StageReceipt`、
`ArtifactIndex`、`IntegrationLedger`、枚举与 `IssueCode`、
`default_repair_mode`、`content_hash`、`canonical_json`、`file_digest`、
`new_run_id`、`utc_now`。

## 2. `domain.py`（已完成，勿改签名）

`DomainPlugin`（`ObservableSpec`、`EntitySpec`）＋内置 `generic`、
`snia_light_curve`、`solar_cycle`；`plugin_for_contract`、`get_plugin`、
`register_plugin`、`slug`、`normalise_identifier`、`unit_from_header`、
`strip_unit_from_header`、`detect_unit_in_values`、`plausibility_issue`、
`confidence_from_score`。

## 3. Owner A — `tabular.py`、`html_tables.py`、`markdown_tables.py`

```python
# tabular.py
def sniff_table(path: str | Path, *, sample_bytes: int = 65536) -> TableProfile
    # 返回 encoding / delimiter / quotechar / header_row / n_columns /
    # ragged_rows / suspect_flags（编码可疑、分隔符可疑、疑似截断）
@dataclass(frozen=True, slots=True)
class TableProfile:
    path: str; encoding: str; delimiter: str; quotechar: str
    header_row: int; n_columns: int; row_count: int
    ragged_row_indexes: tuple[int, ...]
    suspect_flags: tuple[str, ...]      # encoding_suspect / delimiter_suspect / truncated
    confidence: ConfidenceBand

def read_table(path, *, source_id: str, table_id: str, max_rows: int = 200_000,
               profile: TableProfile | None = None) -> ExtractedTable
    # 支持 csv/tsv/txt/psv 与压缩(.gz)；表头去空白、重复列名加 #n；
    # 每行保留全部原值（字符串），不得转 float（转换属于 semantics/alignment）
def read_json_records(path, *, source_id: str, table_id: str,
                      records_path: str = "") -> ExtractedTable
    # JSON / JSONL；嵌套字典按父键_子键扁平化；列表值序列化为 ; 连接字符串
def flatten_nested(payload, *, prefix: str = "") -> dict[str, Any]
```

```python
# html_tables.py
def tables_from_html(html_text: str, *, source_id: str, table_id_prefix: str
                     ) -> tuple[ExtractedTable, ...]     # 仅标准库 html.parser
def tables_from_html_file(path, *, source_id: str, table_id_prefix: str) -> tuple[...]
# 处理 colspan/rowspan 展开、<th> 表头、caption 作为 provenance.locator["caption"]
```

```python
# markdown_tables.py
def tables_from_markdown(text: str, *, source_id: str, table_id_prefix: str) -> tuple[...]
# GFM 管道表；跳过代码块内表格误判
```

A 的测试：`tests/scientific_data/test_tabular.py`、`test_html_tables.py`、
`test_markdown_tables.py`，含 ragged 行、BOM/GBK/Latin-1、分号分隔、`"1,234.5"` 千分位、
科学计数法、重复列名、colspan/rowspan、JSONL、嵌套字典、压缩文件、疑似截断文件。

## 4. Owner B — `units.py`、`timescales.py`

```python
# units.py
@dataclass(frozen=True, slots=True)
class UnitResolution:
    raw: str; canonical: str; dimension: str
    factor: float | None      # canonical = raw * factor；None 表示不可确定
    exact: bool               # 只有 exact=True 才允许 AUTO_SAFE 换算
    note: str
def dimension_of(unit: str) -> str          # magnitude/flux/time/angle/length/...
def resolve_unit(raw: str, *, quantity: str, plugin: DomainPlugin) -> UnitResolution
def convert(value: float, src: str, dst: str) -> float | None
    # 只允许同量纲精确因子表；mmag→mag=0.001, Jy→mJy=1000, G→T=1e-4,
    # arcsec→deg=1/3600, percent→fraction=0.01；跨量纲返回 None
def is_photometric_system(unit: str) -> bool   # AB mag / Vega mag / ST mag 区分
def unit_conflict_groups(rows, column) -> dict[str, list[int]]
# 明确禁止：mag↔flux 自动换算（需要零点/光谱信息）→ 只能上报 UNIT_UNKNOWN
```

```python
# timescales.py
@dataclass(frozen=True, slots=True)
class TimeResolution:
    raw: str; iso: str; mjd: float | None; jd: float | None
    system: str; ambiguous: bool; note: str
def detect_time_system(header: str, samples: Sequence[str]) -> str
def to_time(value, *, system: str | None = None,
            timezone: str = "UTC") -> TimeResolution
def mjd_to_iso(mjd: float) -> str;  iso_to_mjd(iso: str) -> float
def julian_to_mjd(jd: float) -> float;  mjd_to_julian(mjd: float) -> float
def normalise_phase(value: float, *, unit_hint: str, reference_mjd: float | None
                    ) -> str | None
# 锚点必须精确：2000-01-01T12:00:00Z = JD 2451545.0 = MJD 51544.5；
# 1970-01-01T00:00:00Z = MJD 40587.0 = JD 2440587.5；JD − 2400000.5 = MJD。
# 儒略日算法用 Fliegel–Van Flandern 整数式，禁用 astropy。
# 不支持的格式（如 "Feb 3, 2011"、"2011-02-03"、"54321.5"、"12.3 days past max"）
# 必须给出 system + ambiguous，无法判定时 ambiguous=True 且 iso=""。
```

B 的测试：`tests/scientific_data/test_units.py`、`test_timescales.py`，含
1900/1927/2100 闰年边界、负 MJD、小数日、`2011-02-03T05:11:00.5`、
时区后缀、`UTC`/`TT` 标注差异只上报不换算、单位大小写与复数、
`10**15 erg/cm2/s` 这类不可确定单位。

## 5. Owner C — `semantics.py`、`entities.py`、`alignment.py`

```python
# semantics.py
def map_columns(table: ExtractedTable, *, contract, plugin: DomainPlugin
                ) -> tuple[FieldMapping, ...]
    # 规则：显式合同映射 > 精确同义词 > 去单位后匹配 > token 重叠 > 数值形态验证
    # 每个映射必须给 ConfidenceBand + evidence（命中规则与命中文本）
    # 低置信/一列多义/必需列缺失 → 返回映射 applied=False 或交由 quality.py 上报
def suggest_observables(unmapped_columns, *, contract, plugin) -> tuple[QualityIssue, ...]
def infer_column_kind(values: Sequence[Any]) -> str   # quantity|time|identifier|label|quality|empty
```

```python
# entities.py
def build_entity_table(ledger, *, contract, plugin) -> tuple[EntityRef, ...]
def resolve_entity(raw_label, *, ledger, plugin,
                   coordinates: Mapping[str, float] | None = None) -> EntityResolution
    # 顺序：精确 normalised alias > 已登记 designation > 坐标 crossmatch（容差来自
    # plugin.entity.coord_tolerance_arcsec）> 名称编辑距离（仅提示，不自动合并）
    # 需要人工时返回 ambiguous_with，绝不自动合并不同名字的对象
@dataclass(frozen=True, slots=True)
class EntityResolution: entity_id: str; confidence: ConfidenceBand
                       ambiguous_with: tuple[str, ...]; evidence: tuple[str, ...]
def merge_candidate_pairs(ledger, *, plugin) -> tuple[tuple[str, str, float], ...]
```

```python
# alignment.py
def align(ledger, *, contract, plugin) -> tuple[ObservationRecord, ...]
    # 逐表逐行 → ObservationRecord：observable 由 FieldMapping 决定；
    # 单位经 units.resolve_unit（只有 exact 才换算，否则记 unit_raw 并上报）；
    # 时间经 timescales；merge_key 冲突时**保留多来源记录**并置 conflict=True，
    # 不平均、不覆盖；同来源同键完全重复只留一条并记 DUPLICATE_EXACT
def dedupe_records(records) -> tuple[ObservationRecord, ...]
def conflict_groups(records) -> dict[tuple[str, str, str], list[ObservationRecord]]
def to_long_dataframe(records) -> "pandas.DataFrame"   # 稳定列序，供 package.py
```

C 的测试：`tests/scientific_data/test_semantics.py`、`test_entities.py`、
`test_alignment.py`，必须覆盖：同名不同对象、不同名同对象（SN 2011fe / PTF11kly）、
坐标匹配、mag 单位冲突（mmag vs mag）、时间系统混合、跨源冲突保留、
完全重复去重、多行合并（uncertainty 列与主列同行绑定）。

## 6. Owner D — `quality.py`、`repair.py`、`human_loop.py`

```python
# quality.py
def run_quality_checks(ledger, *, contract, plugin,
                       stages: Sequence[str] = ("all",)) -> tuple[QualityIssue, ...]
    # 至少实现：missing_value（带 reason 分类）、missing_coverage、coverage_gap、
    # duplicate_exact、duplicate_key、cross_source_conflict、unit_inconsistency、
    # unit_unknown、time_system_ambiguous、nonfinite_value、out_of_range、
    # implausible_value、entity_identity_ambiguous、unsafe_dedup_risk、
    # schema_drift、empty_extract、truncated_table、encoding_suspect、
    # delimiter_suspect、axis_scale_suspect、legend_mapping_ambiguous、
    # digitization_low_confidence、figure_overlay_mismatch、error_bars_unverified
def issue_fingerprint(issue) -> str          # 稳定 id 生成，重复运行不产生新 id
def revalidate(ledger, *, contract, plugin, only_issue_ids) -> tuple[QualityIssue, ...]
def quality_summary(ledger) -> dict[str, int]
```

```python
# repair.py
@dataclass(frozen=True, slots=True)
class RepairRule:
    code: IssueCode; mode: RepairMode; name: str
    applies: Callable[[IntegrationLedger, QualityIssue], bool]
    plan: Callable[[IntegrationLedger, QualityIssue], RepairPlan | None]
@dataclass(frozen=True, slots=True)
class RepairPlan:
    target: Mapping[str, Any]; before: Mapping[str, Any]; after: Mapping[str, Any]
    rationale: str; reversible: bool; check: str   # 复核用谓点名
def registered_rules() -> tuple[RepairRule, ...]
def auto_apply(ledger, *, contract, plugin) -> tuple[RepairLedgerEntry, ...]
    # 只处理 AUTO_SAFE：精确重复去重、Inf→NaN、编码/分隔符、
    # exact 单位换算、可逆格式标准化；每条写 RepairLedgerEntry 并立即复核
def propose(ledger, *, contract, plugin) -> tuple[QualityIssue, ...]
    # HUMAN_REQUIRED / FORBIDDEN 只生成提案，state=PROPOSED→AWAITING_HUMAN
def apply_human_decision(ledger, *, contract, decision: Mapping[str, Any]) -> RepairLedgerEntry
    # decision: {issue_id, action: approve|reject|edit, value?, reviewer, note}
def undo_repair(ledger, *, repair_id) -> bool
def revalidate_repairs(ledger, *, contract, plugin) -> dict[str, str]
```

```python
# human_loop.py
def write_request_queue(ledger, path) -> str          # 待决问题 JSON（含证据、选项）
def read_decision_file(path) -> tuple[dict[str, Any], ...]
def apply_decision_file(ledger, *, contract, plugin, path) -> tuple[RepairLedgerEntry, ...]
def summarise_for_user(ledger, *, limit: int = 12) -> tuple[dict[str, Any], ...]
    # 面向研究者的中文问句：问题是什么、证据、备选、默认建议、影响范围
```

D 的测试：`tests/scientific_data/test_quality.py`、`test_repair.py`、
`test_human_loop.py`；必须断言：高风险（缺失插补、跨源冲突、身份合并、
坐标轴修正）在无人工时**一条都不自动 apply**；自动修正后原值仍可读回；
`undo_repair` 后指纹与修正前一致。

## 7. Owner E — `sources.py`、`acquisition.py`（可选 `pdf_tables.py`、`figures.py`）

```python
# sources.py
class SourceConnector(Protocol):
    name: str
    def search(self, contract, *, limit: int) -> tuple[SourceRecord, ...]: ...
    def acquire(self, record, *, dest_dir: Path) -> SourceRecord: ...
def list_connectors() -> tuple[str, ...]
def register_connector(connector) -> None
def discover_sources(contract, *, connectors: Sequence[str] = (),
                    budget: Mapping[str, Any] | None = None) -> tuple[SourceRecord, ...]
def local_dir_connector(...)      # /project 或工作区内文件：必须可用（离线主链路）
def cds_vizier_connector(...)     # 允许 httpx，但必须 allowlist 主机 + 缓存 + 失败即上报
def arxiv_connector(...)          # 复用 jw/tools/knowledge_base.py 的缓存与限速思路
# 网络默认关闭；任何远端访问失败 → RetrievalState.FAILED + failure_reason，
# 禁止伪造 source 内容；主机不在 allowlist → IssueCode.SOURCE_HOST_DENIED
```

```python
# acquisition.py
def acquire_all(ledger, *, workspace: Path, connectors=(), budget=None) -> tuple[...]
def freeze_file(path, *, source_id) -> SourceRecord   # 计算 sha256 + 登记
```

## 8. Owner E2 — `figures.py`（图表识别与校验）

```python
def digitise_figure(png_path, *, axes: AxisSpec, legend: Mapping[str, str]
                    ) -> FigureExtraction
@dataclass(slots=True)
class AxisSpec:
    x_ticks: tuple[tuple[float, tuple[int, int]], ...]   # (data_value, (px,py))
    y_ticks: tuple[tuple[float, tuple[int, int]], ...]
    x_scale: str = "linear"; y_scale: str = "linear"      # linear|log
    x_unit: str = ""; y_unit: str = ""
class FigureExtraction:
    points: tuple[dict[str, Any], ...]; calibration_residual_px: float
    overlay_path: str; warnings: tuple[str, ...]; confidence: ConfidenceBand
# 必须实现：像素→数据标定（含 log 轴）、残差检查、叠加复核图、
# 与同实体表格数据交叉核对（figure_overlay_mismatch），
# 输出 ExtractedTable（format="figure_digitized", ValueKind.DIGITIZED）
```

## 9. Lead — `workflow.py`、`package.py`、`policy.py`、`jw/tools/scientific_data.py`

`package.py` 输出：`integrated_observations.csv`、`source_catalog.csv`、
`field_mapping.csv`、`quality_issues.csv`、`repair_log.csv`、`provenance.jsonl`、
`data_quality_report.md`、`run_manifest.json`、`ledger.json`、`README_data.md`。

工具面（`tool_bundles: [scientific-data-integration]`，`include_in_main=False`）：
`sci_data_plan_requirement`、`sci_data_build_contract`、`sci_data_validate_contract`、
`sci_data_discover_sources`、`sci_data_register_local_sources`、
`sci_data_acquire_sources`、`sci_data_extract_tables`、`sci_data_align`、
`sci_data_detect_issues`、`sci_data_auto_repair`、`sci_data_human_queue`、
`sci_data_apply_decisions`、`sci_data_build_package`、`sci_data_run_pipeline`、
`sci_data_open_run`。全部返回 JSON 字符串并写工作区内的 receipt/manifest。

## 10. 验证基准（Owner F — `tests/scientific_data/fixtures/`）

`snia_benchmark/`：≥3 个来源（开放数据库 CSV 切片、论文表格 Markdown/HTML、
合成光变曲线 PNG 图）＋ `gold/integrated_observations.csv` ＋
`gold/expected_issues.json`。注入 ≥8 类异常（缺失、完全重复、跨源冲突、
单位 mmag/mag 混用、时间系统混用、坐标轴错标、图例歧义、非有限值）。

指标：来源发现 Recall@K、表格单元格准确率、字段映射准确率、单位换算正确率、
异常检测 P/R、图轴识别准确率、自动修正安全率（高风险未确认自动执行数=0）、
溯源覆盖率（=100%）、端到端复现哈希一致率。
