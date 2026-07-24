# 知识库子系统改造方案

> 版本：v1.0 · 2026-07-21
> 状态：待评审
> 关联文档：`2026-06-20-solar-cycle-co-scientist-design.md` §4.9（知识管理子系统原始设计）

---

## 1. 问题诊断

当前系统闭环（规划 → 假设 → 实验）存在三个结构性短板：

1. **闭环自转、深度不足**：三个子 Agent 全在内部数据上循环，结论不引用外部知识，产出不回流积累，每轮研究从零开始，无法螺旋下挖。
2. **知识层缺位**：设计文档 §4.9 的 LLM Wiki 只有静态 markdown 雏形（`knowledge/` 4 个文件 + solar-cycle 技能 5 个条目），无结构化存储、无检索接口、无生命周期；文献搜索只有 OpenAlex 元数据查询且结果用完即弃。
3. **人不在环路里**：HITL 只剩 shell 命令审批一层（`Auto-approve` 开启后形同虚设），科研关键决策点（研究路线、假设卡、知识晋升）没有人的介入位置。

## 2. 改造目标

1. 建成统一的知识服务层：**LLM Wiki（结构化知识条目库 + 生命周期）+ 文献搜索（外部知识入口管线）**，两者经"摄取管线"汇合——文献必须蒸馏为知识条目后才能被引用，保证溯源链完整。
2. 把知识使用从"建议"变成"硬约束"：假设与实验设计必须引用知识条目，否则校验不通过。
3. 把人放进三个决策点：知识晋升门、路线 freeze 门、假设 freeze 门；可配置降级为自动审。

## 3. 现状资产盘点（直接复用）

| 资产 | 位置 | 复用方式 |
| --- | --- | --- |
| 条目 schema 与生命周期设计 | 设计文档 §4.9.4–4.9.6 | 直接作为 contract 与晋升规则蓝本 |
| solar-knowledge 子 Agent 定义 | `jw/subagents/solar_knowledge.yaml` | 保留 prompt，补上 kb_* 工具即成第 6 子 Agent |
| OpenAlex 文献检索/引用解析/证据抽取 | `jw/tools/research_planner.py` 内三个工具 + `src/research_planner/` | 抽出为 lit_* 工具全家可用，不再局限于规划链 |
| 静态知识雏形 | `knowledge/`（4 文件）、`jw/skills/solar-cycle/references/llm_wiki/`（5 条目） | 结构化导入为首批 canonical 条目 |
| HITL 中断机制 | `jw/agent.py:844` 的 `HumanInTheLoopMiddleware(interrupt_on=...)` + WebUI 审批件 UI | 三个新决策点直接挂入 interrupt_on |
| 会话/存储惯例 | `jw/paths.py`（DATA_DIR）、`sessions.py`（SQLite + WAL） | knowledge.db 放 `~/.jw/`，同一套运维惯例 |
| 向量扩展 | 依赖已含 `sqlite-vec` | P3 之后加语义检索不用换库 |

## 4. 总体架构

```
                        ┌─────────── 人（WebUI / CLI）───────────┐
                        │  晋升审批    路线确认    假设评审        │
                        └──────▲──────────▲──────────▲──────────┘
                               │ interrupt│ interrupt│ interrupt
   arXiv ─┐                    │          │          │
   OpenAlex├─ lit_search → lit_fetch → lit_distill ──►│
                        │                （candidate 队列）
                        ▼
   ┌──────────────── Knowledge Service（知识服务层）────────────────┐
   │  kb_search / kb_read / kb_propose / kb_promote / kb_deprecate │
   │  kb_conflicts / kb_log                                        │
   │                                                                │
   │  knowledge.db (SQLite+WAL+FTS5)        knowledge_base/*.md     │
   │  entries / entry_versions /            （人读导出，可手工编辑）  │
   │  provenance_log / lit_sources /                               │
   │  review_queue                                                 │
   └──────▲──────────▲──────────▲──────────▲──────────▲────────────┘
          │检索+引用  │引用门禁  │引用门禁  │候选注入   │候选写回
   ┌──────┴───┐ ┌────┴─────┐ ┌──┴──────┐ ┌─┴────────┐ ┌┴───────────┐
   │ 规划Agent │ │ 假设Agent │ │ 实验Agent │ │知识管理Agent│ │ 证据审查    │
   └──────────┘ └──────────┘ └─────────┘ └──────────┘ └────────────┘
```

核心规则：

- **R1（单一入口）**：agent 做 grounding 只能引用已入库条目（kb id），禁止直接引用原始文献/网页。
- **R2（候选先行）**：一切新知识（文献蒸馏、运行 findings、反例、失败经验）一律以 `status=candidate` 入库，晋升必须过审核门。
- **R3（引用强制）**：假设卡、实验设计、研究路线的 evidence/grounding 字段必须含 ≥1 个有效 kb id 或显式声明"知识缺口"，否则 validate 拒绝。
- **R4（溯源完整）**：每次 kb_read 自动写 provenance_log（run_id、agent、条目 id、用途）；每轮研究结束产出知识使用清单。
- **R5（冲突显性化）**：新候选与 canonical 矛盾时不覆盖，报冲突并自动生成研究子问题回喂规划。

## 5. 详细设计

### 5.1 存储层

**SQLite**：`~/.jw/knowledge.db`（WAL 模式，FTS5 虚表），五张表：

```sql
CREATE TABLE entries (                    -- 当前态
  id TEXT PRIMARY KEY,                    -- kb_<type>_<slug>_<seq>
  type TEXT NOT NULL,                     -- concept/mechanism/data_source/
                                          -- experiment_paradigm/hypothesis_template/
                                          -- finding/counterexample
  title TEXT NOT NULL,
  content TEXT NOT NULL,                  -- JSON：按 type 的子字段结构
  source_type TEXT NOT NULL,              -- literature/textbook/dataset_doc/
                                          -- historical_run/expert/derived
  source_ref TEXT NOT NULL,               -- DOI/URL/run_id/审核人/书页
  confidence TEXT NOT NULL,               -- high/medium/low
  status TEXT NOT NULL,                   -- candidate/canonical/deprecated/superseded
  valid_range TEXT,
  related_ids TEXT,                       -- JSON array
  provenance TEXT,                        -- JSON：晋升理由、支持 run_id 列表、审核人
  version INTEGER NOT NULL,
  created_at TEXT, updated_at TEXT, created_by TEXT
);
CREATE TABLE entry_versions (             -- 全量历史（每次变更快照，可回滚）
  entry_id TEXT, version INTEGER, snapshot TEXT, changed_at TEXT, changed_by TEXT, reason TEXT,
  PRIMARY KEY (entry_id, version)
);
CREATE TABLE provenance_log (             -- 使用溯源（R4）
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT, agent TEXT, entry_id TEXT, purpose TEXT, ts TEXT
);
CREATE TABLE lit_sources (                -- 文献缓存（防重复抓取/重复蒸馏）
  source_id TEXT PRIMARY KEY,             -- openalex:W… / arxiv:YYMM.NNNNN
  title TEXT, authors TEXT, year INTEGER, doi TEXT, url TEXT,
  abstract TEXT, fetched_at TEXT, distilled_entry_id TEXT
);
CREATE TABLE review_queue (               -- 晋升/冲突审核队列（HITL 的落点）
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT,                              -- promote / conflict / deprecate
  entry_id TEXT, payload TEXT,            -- JSON：晋升依据或冲突双方
  status TEXT,                            -- pending / approved / rejected / auto_approved
  reviewer TEXT, decided_at TEXT, note TEXT
);
CREATE VIRTUAL TABLE entries_fts USING fts5(id, title, content, content='entries', content_rowid='rowid');
```

**Markdown 导出**：`knowledge_base/<type>/<id>.md`（YAML frontmatter 全字段 + 正文）。每次写库同步导出；支持反向导入（手工编辑后 `kb_import` 校验回写，版本 +1）。用途：人审查、git 管理、跨机器同步。

**条目 content 子字段**（按 type，校验用 JSON Schema，见 5.6）：
- `concept`：definition / physical_notes / see_also
- `mechanism`：claim / supporting_evidence / counter_evidence / controversy / testable_predictions[]
- `data_source`：collection_method / known_biases / calibration_history / coverage
- `experiment_paradigm`：design / metrics / pitfalls
- `hypothesis_template`：structure / example / applicable_when
- `finding` / `counterexample`：statement / run_id / effect_size / uncertainty

### 5.2 工具层（新模块）

代码布局沿用三包模式：

```
src/knowledge_base/            # 核心逻辑（纯标准库）
  __init__.py  store.py  contracts.py  service.py  fts.py  export.py  literature.py
jw/tools/knowledge_base.py   # @tool 包装，注册进 KB_TOOLS
specs/knowledge_base/*.schema.json     # 条目与工具返回 contract
```

工具签名（均返回 JSON 字符串，`_ok`/`_err` 惯例）：

| 工具 | 签名 | 说明 |
| --- | --- | --- |
| `kb_search` | `(query, type="", status="", confidence="", valid_range="", limit=8)` | FTS5 + 结构化过滤；默认只返回 canonical+pending candidate，deprecated 排除 |
| `kb_read` | `(entry_id, agent="", run_id="", purpose="")` | 读条目全文；**自动写 provenance_log** |
| `kb_propose` | `(type, title, content, source_type, source_ref, confidence, valid_range="", related_ids=[], agent="", run_id="")` | 写 candidate；schema 校验；与 canonical 语义冲突时返回 conflict 提示（不阻断） |
| `kb_promote` | `(entry_id, reason, reviewer="")` | 触发晋升审核（见 5.6 HITL）；满足自动规则时标 `auto_approved` |
| `kb_deprecate` | `(entry_id, reason, superseded_by="")` | 标记废弃/取代，不删除，版本快照 |
| `kb_conflicts` | `(entry_id="")` | 列出未决冲突；单条目的冲突详情 |
| `kb_review_decide` | `(queue_id, decision, note="", reviewer="human")` | 审批队列决策（人通过 UI/CLI 触发，agent 不可调用 promote 旁路） |
| `kb_log` | `(run_id)` | 输出某轮研究的知识使用清单（用了哪些条目、产出哪些候选） |
| `kb_import` | `(path="")` | markdown 回导 + 首批内容导入 |

### 5.3 文献管线

```
lit_search(query, source="openalex|arxiv", limit, from_year, to_year)
  → lit_sources 缓存（命中直接返回，不重复请求）
lit_fetch(source_id)      → 摘要/开放全文落盘 workspace/literature/
lit_distill(source_id, entry_type, focus="")
  → LLM 蒸馏为 candidate 条目，硬约束：
    1) content 每个证据性字段必须附 quote = 原文逐字段落（≤40词）+ 位置（摘要/段落）
    2) 无原文支撑的字段留空并标 "evidence_gap"
    3) source_ref = DOI/arXiv id 自动回填
  → 自动调 kb_propose（status=candidate）→ review_queue
```

- 源：OpenAlex（现有实现，抽出复用）+ arXiv API（免费、物理学覆盖好）；Tavily/DuckDuckGo 仅作发现辅助，不入库。
- `lit_distill` 由知识管理 Agent 调用主模型执行，防幻觉契约由 `contracts.py` 校验（quote 必须能在该文献缓存文本中原样命中，否则拒收）。
- 成本护栏：同一 source_id 只蒸馏一次；每轮研究默认蒸馏上限 5 篇（可配 `kb_distill_budget`）。

### 5.4 与现有闭环的接线点（改动清单）

| # | 位置 | 改动 |
| --- | --- | --- |
| 1 | `src/research_planner/` brief 生成 | plan brief 强制附 `related_candidates`（kb_search 结果 Top-N + 未决冲突列表）；要求路线设计回应（证实/反驳/收窄 valid_range） |
| 2 | `jw/tools/scientific_hypothesis.py::validate_response` | 新增校验：每个候选假设的 evidence 字段 ≥1 个有效 kb id，或显式 `knowledge_gap` 声明；否则返回不通过（先 warning 模式 2 周再转 hard） |
| 3 | `jw/tools/automatic_experiment.py::validate_design` | 同上：design 的 grounding/evidence 需 kb 引用 |
| 4 | 实验 `finalize` 后钩子 | findings/counterexample/失败经验自动组装 `kb_propose` 调用（status=candidate），写入 finalize 返回的 `knowledge_writeback` 字段 |
| 5 | `jw/agent.py:497` 工具注册 | `KB_TOOLS` 加入 tool_registry 与 base_tools；`_factory.py:73` 同步注册给异步子 Agent |
| 6 | `jw/subagents/solar_knowledge.yaml` | tools 改为 `[think_tool, kb_search, kb_read, kb_propose, kb_conflicts, kb_log, lit_search, lit_fetch, lit_distill]`（不含 promote/deprecate——晋升权只走审核门） |
| 7 | 技能包 | 新增 `~/.jw/skills/knowledge-base-agent/`（编排）+ 子技能（条目写作规范、蒸馏规范、冲突处理、晋升标准），风格对齐 hypothesis-*/experiment-* |
| 8 | 冲突回喂 | `kb_conflicts` 未决项注入 planner brief（同 #1），冲突即研究问题 |

### 5.5 知识管理子 Agent（第 6 子 Agent）

复用 `solar_knowledge.yaml` 的 prompt（不变），新职责落地为编排流程：

1. **应答检索**：其他 agent/用户问知识 → kb_search/kb_read + provenance。
2. **摄取**：文献（lit_distill）、运行产出（finalize 钩子候选）→ 审核队列。
3. **健康巡检**（可被 /schedule 定期触发）：长期未引用条目、孤证 candidate（>N 天未晋升）、valid_range 过期条目 → 生成维护建议给人。
4. **知识使用报告**：每轮研究结束 `kb_log` 输出，附进主 agent 的总结。

### 5.6 Human-in-the-loop 三决策点

复用 `HumanInTheLoopMiddleware(interrupt_on=...)`（`agent.py:844`）：

| 决策点 | 机制 | auto_approve 降级 |
| --- | --- | --- |
| 知识晋升 `kb_promote` | interrupt_on 加 `"kb_promote": True`；WebUI 审批件展示候选条目+晋升依据（跨运行复现 run_id 列表/文献 DOI），批准→canonical，驳回→留 candidate | 规则自动审：满足 §4.9.6 任一条件则 `auto_approved` 并在条目 provenance 标"未人审" |
| 路线 freeze `research_planner_freeze_plan` | interrupt_on 加该工具；审批件展示 plan 摘要（子问题、路线、stop_rules） | 直接 freeze，provenance 标记 |
| 假设 freeze `scientific_hypothesis_freeze` | interrupt_on 加该工具；审批件展示假设卡列表，支持批量通过/驳回/调置信度 | 直接 freeze，标记 |

WebUI 侧：审批件复用现有 tool-call 审批 UI（HITL 已支持 approve/reject/edit），无需新组件；P3 再加知识库面板。

### 5.7 WebUI 知识库面板（P3）

在现有检查器（Inspector）加 **Knowledge Base** tab（复用记忆浏览器的 UI 模式，后端加只读 REST：`GET /api/kb/entries`、`GET /api/kb/review_queue`）：

- **Browse**：按 type/status 浏览检索条目，详情含 provenance 链与版本历史
- **Review 队列**：pending 晋升/冲突列表，一键批准/驳回（调 `kb_review_decide`）
- **Usage**：每轮研究的知识使用清单（provenance_log 可视化）

## 6. 初始内容导入

P1 末执行一次性导入（`kb_import`）：

- `knowledge/` 4 文件 → concept/project_context 类 canonical（source_type=expert，created_by=human）
- `solar-cycle/references/llm_wiki/` 5 条目 → 按各自 type 转 canonical（source_type=textbook/literature，人工核定置信度）
- `experiment/runs/`、`hypothesis/runs/` 中已 finalize 的历史结果 → finding/counterexample 类 **candidate**（source_ref=run_id，走正常晋升流程，不开后门）

## 7. 分阶段计划

### P1 — 知识地基（纯机器层，无外部依赖）

范围：SQLite 存储 + schema contracts + kb CRUD/检索/溯源 + markdown 导出 + KB_TOOLS 注册 + 初始导入 + 单元测试。
交付：`src/knowledge_base/`、`jw/tools/knowledge_base.py`、`specs/knowledge_base/`、`knowledge_base/` 导出目录、导入脚本。
验收：
- contract 测试全过（条目 schema、生命周期迁移合法性、版本快照可回滚）
- `kb_propose → kb_promote(自动规则) → kb_search` 端到端脚本演示
- 9 个静态文件 + 历史 runs 导入完成，`kb_search "极区前兆"` 有结果且 provenance 可查
预估：1.5–2 天。

### P2 — 接上外脑与引用门禁

范围：lit_* 管线（OpenAlex 抽出 + arXiv）+ distill 防幻觉契约 + 闭环接线点 #1–#4 + solar_knowledge 工具挂载 + 知识管理技能包。
验收：
- 对"polar field precursor SC26"跑 lit_search→distill→candidate 全链路，quote 原文命中率 100%
- 新假设无 kb 引用时 validate 返回明确不通过原因
- 实验 finalize 自动产出 candidate 并出现在 review_queue
预估：2–2.5 天。

### P3 — 接人与面板

范围：三决策点 interrupt_on + WebUI KB 面板 + 巡检调度 + GPU 服务器同步部署。
验收：
- WebUI 审批件完成一次晋升人审；auto_approve 下自动审标记正确
- 面板可读浏览/审批/溯源；服务器 16174 隧道访问一致
预估：1.5–2 天。

**总预估：5–7 个工作日**（单人/单 agent 强度；P1 不依赖 P2/P3，可独立交付使用）。

## 8. 测试策略

- **contract 测试**：条目 schema、状态机（candidate→canonical/deprecated/superseded 合法迁移全集）、工具返回契约（对齐现有 test_contracts.py 风格）
- **单元**：FTS 检索排序、provenance_log 写入、markdown 双向导入一致性、distill quote 校验
- **端到端**：P1 脚本演示 + P2 一次真实文献管线 + P3 一次 HITL 审批（真实 interrupt）
- 全部纳入 `hypothesis/tests`、`experiment/tests` 同级的 `knowledge_base/tests`，纯标准库可跑

## 9. 风险与取舍

| 风险 | 取舍/缓解 |
| --- | --- |
| distill 蒸馏仍可能曲解文献 | quote 原文命中硬校验 + candidate 默认不晋升 + 人审兜底 |
| 引用门禁短期降低 agent 成功率 | 先 warning 模式运行 2 周，统计误伤率再转 hard fail；显式 `knowledge_gap` 声明永远是合法出口（且自动转成研究问题） |
| FTS 关键词检索不够语义 | P1 先用 FTS5；依赖已含 sqlite-vec，P3 后按需在 kb_search 加向量通道，接口不变 |
| token 成本（蒸馏+巡检） | 蒸馏预算护栏（默认 5 篇/轮）+ 蒸馏走辅助模型（auxiliary_model）可配 |
| auto_approve 下人审形同虚设 | 自动审必须满足 §4.9.6 硬条件，且条目永久带"未人审"标记，面板可筛选复查 |
| 多 workspace 知识污染 | knowledge.db 机器全局但条目带 workspace 可选作用域字段；默认全局共享（领域知识本就该共享），敏感项目可配独立 db |

## 10. 后续（不在本期）

- 向量语义检索（sqlite-vec）
- 教科书/PDF 摄取（lit_fetch 扩展 PDF 解析）
- 知识图谱视图（related_ids 力导图，并入 WebUI 面板）
- GPU 服务器本地模型承担 distill/巡检（Ollama/vLLM，省 API 成本）
