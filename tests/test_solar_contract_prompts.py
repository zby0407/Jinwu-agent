"""Guardrails that keep specialist prompts aligned with closed contracts."""

from __future__ import annotations

from pathlib import Path

from automatic_experiment.service import _stage_worker_output_guide
from jw.tools import get_tool_bundles

ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_solar_planner_supports_explore_checkpoint_and_publish_modes():
    text = _read("jw/subagents/solar/solar_planner.yaml")
    assert "tool_bundles: [research-planner, research-quality]" in text
    assert "think-tool loop" in text
    assert "Default to exploration mode" in text
    assert "checkpoint/publication mode" in text
    assert "[RESEARCH_PRODUCER_V2]" in text
    assert "MUST call research_planner_get_brief" in text
    assert "research_planner_create_empirical_plan once" in text
    assert "research_planner_submit_complete_draft" in text
    assert "research_planner_update_draft" in text
    assert "only to resume a previously interrupted partial draft" in text
    assert "draft_checkpoint.next_section" in text
    assert "research_planner_validate_draft" in text
    assert "research_planner_freeze_plan before returning" in text
    assert "not permission to return an unfrozen draft" in text
    assert "freeze only when publication was requested" in text
    assert "planner/runs/<run_id>/" in text


def test_solar_planner_states_the_exact_analysis_claim_shape():
    text = _read("jw/subagents/solar/solar_planner.yaml")
    required_fields = {
        "schema_version",
        "estimand",
        "independent_sample_unit",
        "independent_sample_count",
        "observation_cutoff",
        "information_set",
        "primary_analysis",
        "baseline",
        "validation_design",
        "decision_rule",
        "missingness",
        "censoring",
        "data_revision",
        "measurement_regime",
        "measurement_kind",
        "effect_size",
        "uncertainty_interval",
        "sensitivity_analysis",
        "influence_analysis",
        "outcome_branches",
    }
    for field in required_fields:
        assert f'"{field}"' in text
    assert '"schema_version": "analysis-claim-contract-v1"' in text
    assert '"measurement_kind": "not_assessed"' in text
    assert '"outcome": "...", "claim_update": "..."' in text
    assert "Do not add any other fields" in text


def test_hypothesis_and_experiment_agents_state_the_exact_analysis_claim_shape():
    required_fields = {
        "schema_version",
        "estimand",
        "independent_sample_unit",
        "independent_sample_count",
        "observation_cutoff",
        "information_set",
        "primary_analysis",
        "baseline",
        "validation_design",
        "decision_rule",
        "missingness",
        "censoring",
        "data_revision",
        "measurement_regime",
        "measurement_kind",
        "effect_size",
        "uncertainty_interval",
        "sensitivity_analysis",
        "influence_analysis",
        "outcome_branches",
    }
    for relative in (
        "jw/subagents/solar/solar_hypothesis.yaml",
        "jw/subagents/solar/solar_experiment.yaml",
    ):
        text = _read(relative)
        for field in required_fields:
            assert f'"{field}"' in text
        assert '"schema_version": "analysis-claim-contract-v1"' in text
        assert '"measurement_kind": "not_assessed"' in text
        assert '"outcome": "...", "claim_update": "..."' in text
        assert "Do not add any other fields" in text


def test_worker_output_guide_distinguishes_outputs_from_prior_artifacts():
    guide = _stage_worker_output_guide(
        {
            "measurement_plan": [
                {
                    "name": "interaction_estimate",
                    "unit": "standardized amplitude",
                    "role": "primary",
                }
            ],
            "result_plan": [
                {
                    "id": "hypothesis_relation",
                    "display_name": "Hypothesis relation",
                    "value_kind": "category",
                    "unit": "",
                    "role": "primary",
                }
            ],
            "artifact_plan": [
                {"id": "fold_errors", "path": "fold_errors.csv", "kind": "csv"}
            ],
            "experiment_stages": [
                {
                    "id": "analysis",
                    "measurement_refs": ["interaction_estimate"],
                    "result_refs": ["hypothesis_relation"],
                    "endpoint_ids": [],
                    "produces_artifact_ids": ["fold_errors"],
                    "execution": {"expected_artifacts": ["fold_errors.csv"]},
                }
            ],
            "interpretation_policy": {
                "primary_estimand": "registered interaction effect"
            },
        },
        "analysis",
    )

    assert guide["artifact_output_paths"] == {
        "fold_errors.csv": "context['output_dir'] / 'fold_errors.csv'"
    }
    assert "never context['artifact_path_by_id']" in guide["artifact_output_rule"]
    assert guide["primary_estimand"] == "registered interaction effect"
    assert "module-level string constant" in guide["primary_estimand_rule"]
    assert "function-local constant" in guide["primary_estimand_rule"]
    assert "module-level string constant" in guide["source_artifact_rule"]
    assert "function-local constant" in guide["source_artifact_rule"]
    assert guide["measurement_contracts"] == [
        {
            "name": "interaction_estimate",
            "unit": "standardized amplitude",
            "role": "primary",
        }
    ]
    assert guide["result_item_contracts"] == [
        {
            "id": "hypothesis_relation",
            "display_name": "Hypothesis relation",
            "value_kind": "category",
            "unit": "",
            "role": "primary",
        }
    ]


def test_runtime_planning_directive_prefers_compact_empirical_plan():
    text = _read("jw/middleware/research_review_orchestration.py")
    planning = text.split('"planning": (', 1)[1].split('    "data": (', 1)[0]
    assert "research_planner_create_empirical_plan" in planning
    assert "persist exactly one ordered section" not in planning


def test_parent_accepts_partial_results_and_bounds_repair():
    text = _read("jw/prompts.py")
    assert "valid outcome" in text
    assert "Exploratory work may" in text
    assert "If it recurs, stop the loop" in text


def test_experiment_agent_stops_cleanly_when_design_budget_is_exhausted():
    text = _read("jw/subagents/solar/solar_experiment.yaml")
    assert "only after design_validated" in text
    assert "must_stop=true" in text
    assert "do not call research_quality_record_analysis_claim" in text
    assert "do not call automatic_experiment_finalize" in text
    assert "context['output_dir'] / '<artifact path>'" in text
    assert "never obtain output paths from context['artifact_path_by_id']" in text


def test_experiment_result_reuses_terminal_run_and_prepares_with_file_array():
    text = _read("jw/subagents/solar/solar_experiment.yaml")

    assert "prepare_attempt 的 files 用 JSON 数组" in text
    assert "prepare_attempt 的 files 用 JSON 对象" not in text
    assert "只有新建 experiment_design" in text
    assert "experiment_result 不得重新 bind" in text
    assert "must_stop=true 时立即返回" in text


def test_experiment_result_prompts_state_scientific_payload_scalar_types():
    specialist = _read("jw/subagents/solar/solar_experiment.yaml")
    orchestration = _read("jw/middleware/research_review_orchestration.py")

    for text in (specialist, orchestration):
        assert "estimate must be a finite number or null" in text
        assert (
            "interval and equivalence_bounds must each be [low, high] or null" in text
        )
        assert "sensitivity must be text or null" in text
        assert "uncertainty_reasons must be an array of strings" in text


def test_experiment_agent_repairs_condition_comparison_measurement_refs_locally():
    text = _read("jw/subagents/solar/solar_experiment.yaml")

    assert "design.criteria[i].measurement_refs" in text
    assert "条件 A 估计、条件 B 估计及二者差值" in text
    assert "删除差异判定文字" in text


def test_experiment_agent_centers_interaction_predictors_without_fold_leakage():
    text = _read("jw/subagents/solar/solar_experiment.yaml")

    assert "交互模型若需解释主效应" in text
    assert "连续预测量做均值中心化" in text
    assert "每个滚动起源折只用训练行计算中心" in text
    assert "同一训练中心应用到留出行" in text


def test_experiment_agent_keeps_weakening_interaction_direction_consistent():
    text = _read("jw/subagents/solar/solar_experiment.yaml")

    assert "负交互支持削弱" in text
    assert "正交互反驳削弱" in text
    assert "不得在最终摘要中颠倒方向" in text


def test_experiment_agent_maps_results_to_the_registered_models_and_rules():
    text = _read("jw/subagents/solar/solar_experiment.yaml")

    assert "每个 measurement 必须由其 scientific_meaning 指定的模型产生" in text
    assert "不得用对照模型系数替代主模型系数" in text
    assert "置换统计量和单侧或双侧尾部必须与 design 完全一致" in text
    assert "supports 只有在所有已登记必要条件同时成立时才能返回" in text


def test_solar_data_requires_hash_bound_inputs_before_discovery():
    text = _read("jw/subagents/solar/solar_data.yaml")
    assert "tool_bundles: [solar-features, knowledge-base-literature]" in text
    assert "deterministic_data_context" in text
    assert "do not call `solar_data_open_context` again" in text
    assert "Only paths in `eligible_inputs`" in text
    assert "reproduce_silso_cycle_extrema" in text
    assert "never guess `/project/data`" in text
    assert "host stages eligible `/project/...` inputs" in text
    assert "If a Harness calculation returns `error` or `partial` twice" in text
    assert (
        "treat its task-local table and receipt as the canonical Data product" in text
    )
    assert "n_eff_upper_bound" in text
    assert "peak_smoothed_sunspot_number_sigma" in text
    assert "minimum_date_sensitivity" in text
    assert "must_stop=true" in text
    assert "MWO facular proxy-era cycles 15-20" in text
    assert "WSO magnetograph-era cycles 21-24" in text
    assert "`pair_coverage` 中每个周对的左端点" in text
    assert "不得把 14→15 至 23→24 改写为 15→16 至 24→25" in text


def test_solar_specialists_preserve_receipt_bound_cycle_pair_mapping():
    planner = _read("jw/subagents/solar/solar_planner.yaml")
    hypothesis = _read("jw/subagents/solar/solar_hypothesis.yaml")

    assert "不能从问题中的周期标签自行推算周对数量" in planner
    assert "pair_coverage 是样本编号的唯一权威" in hypothesis
    assert "目标周期是周对右端点" in hypothesis
    assert "前一周期是周对左端点" in hypothesis


def test_knowledge_agent_is_read_only_and_reports_maintenance_gaps():
    text = _read("jw/subagents/solar/solar_knowledge.yaml")
    assert "tool_bundles: [reasoning, knowledge-base-inspection]" in text
    assert "restrict_tools: true" in text
    assert "never propose, import, patch, promote, deprecate, or decide" in text
    assert "kb_query" in text
    assert "kb_read" in text
    assert "maintenance gap" in text


def test_evidence_agent_is_read_only_and_uses_typed_review_tools():
    text = _read("jw/subagents/solar/solar_evidence.yaml")
    assert (
        "tool_bundles: [reasoning, evidence-review, knowledge-base-inspection]" in text
    )
    assert "restrict_tools: true" in text
    review_tools = {tool.name for tool in get_tool_bundles()["evidence-review"]}
    for name in (
        "evidence_review_open_context",
        "evidence_review_read_source",
        "evidence_review_submit_round",
        "evidence_review_get_status",
    ):
        assert name in review_tools
    assert "evidence_review_record_assessment" not in review_tools
    assert "evidence_review_record_scientific_quality" not in review_tools
    assert "evidence_review_submit_verdict" not in review_tools
    assert "Decisions are accept, accept_with_limits, revise, or block" in text
    assert "never author, patch, overwrite" in text
    assert "never edit the producer artifact" in text
    assert "assessment_claims contains exactly one summary row" in text
    assert "accepted_claims must list the exact accepted artifact claim ids" in text


def test_hypothesis_agent_reads_wiki_before_generating_candidates():
    text = _read("jw/subagents/solar/solar_hypothesis.yaml")
    assert "model_call_limit: 48" in text
    assert "不宣称内部草稿、冻结、发布或 release 状态" in text
    assert (
        "tool_bundles: [knowledge-base-readonly, scientific-hypothesis, research-quality]"
        in text
    )
    assert "tool_bundles: [reasoning," not in text
    readonly_tools = {
        tool.name for tool in get_tool_bundles()["knowledge-base-readonly"]
    }
    assert readonly_tools == {
        "kb_query",
        "kb_read",
        "lit_bundle_build",
        "lit_bundle_read",
    }
    assert "First call scientific_hypothesis_bind_request" in text
    assert "Second call kb_query" in text
    assert "target 3 entries and never exceed 5" in text
    assert "A hypothesis_template is not a method/data constraint" in text
    assert "the same observable named in the bound question" in text
    assert "omit the optional agent and run_id arguments" in text
    assert (
        "call kb_read and then scientific_hypothesis_bind_wiki_evidence immediately"
        in text
    )
    assert "Call scientific_hypothesis_build_literature_bundle" in text
    assert "must not copy or shorten the bound research question" in text
    assert "call lit_bundle_read exactly once" in text
    assert "scientific_hypothesis_bind_literature_evidence" in text
    assert "scientific_hypothesis_build_novelty_bundle" in text
    assert "每条 query axis 都必须保留同一个目标观测量" in text
    assert "identifiability={association_only, mechanism_support_requires}" in text
    assert "searched_family_count" in text
    assert "one source per assistant turn" in text
    assert "never issue parallel literature-evidence binding calls" in text
    assert "binding registers the source but does not attach it to the draft" in text
    assert "patch the matching candidate immediately" in text
    assert "target 3 sources and never exceed 5" in text
    assert "at least two concrete mechanism, observable, or proxy terms" in text
    assert (
        '"intensity determinants", "mechanisms", or "prediction" alone is invalid'
        in text
    )
    assert (
        "the binding tool will reject an entry without a prior kb_read receipt" in text
    )
    assert "immediately persist H0 or the first complete candidate" in text
    assert "make the cached-literature pass before reading any optional" in text
    assert "多机制、证据冲突或长尾发现问题先形成 4–6 个机制上可区分的候选池" in text
    assert "modal_baseline" in text
    assert "positive_tail" in text
    assert "negative_tail" in text
    assert "null_control" in text
    assert "A query hit is source discovery only" in text
    assert "binding tool rechecks canonical status" in text
    assert "never as observational support" in text
    assert "Candidate/canonical status and confidence metadata never make" in text
    assert "must never write, propose, import, deprecate, review, or promote" in text
    assert "The parent task description is transport, not evidence" in text
    assert "Do not accept a parent-written Wiki summary" in text
    assert "最小且互补的证据启发单元" in text
    assert "新增一项必要的主张、适用条件、反例边界或区分性预测" in text
    assert "不得声称复现任何论文的训练方法" in text
    assert "先用用户给定的截止日期和已核验官方序列建立时间轴" in text
    assert "执行数字、因果与置信度门禁" in text
    assert "不得把“覆盖很少”改写成“约3-4个活动周”" in text
    assert "the sign of a transport effect is regime-dependent" in text
    assert (
        "does not by itself prove that surface transport determines cycle amplitude"
        in text
    )
    assert "相关性、留一稳定性和模型优于基线都不能单独证明因果机制" in text
    assert "goal-specific 必备项" in text
    assert "对每一项只列违反项" in text
    assert "仅做一次完整修订" in text
    assert "不是训练奖励或经验分数" in text
    assert "中文研究问题的全部人类可读正文必须使用中文" in text
    assert "固定审查协议必须先单独执行并报告" in text
    assert "scientific_hypothesis_update_draft" in text
    assert "scientific_hypothesis_review_tail" in text
    assert (
        "evidence_confidence_caps 的四个字段只能逐字使用 exploratory、"
        "evidence_constrained 或 release_candidate" in text
    )
    assert "checkpoint_draft 是父流程的结构化交接" in text
    assert "checkpoint 返回 needs_revision 时只修正 validation_error" in text
    assert "positive_tail" in text
    assert "negative_tail" in text
    assert "violation-first" in text
    assert "instance_rubrics" in text
    assert "tail_review_scoring_guide" in text
    assert "violated_guidelines" in text
    assert "只有 violated_guidelines 为空时 status 才能是 pass" in text
    assert "七项科学 rubric 的逐项通过与违规边界如下" in text
    assert "边界未知时" in text
    assert "噪声空结果不必一次性证伪" in text
    assert "严格使用以下锚点" in text
    assert "high 在这里更差" in text
    assert "Pareto" in text
    assert "candidate_pool_sha256" in text
    assert "禁止先在自然语言中写完整组合、最后才尝试保存" in text
    assert "必须调用 scientific_hypothesis_get_draft" in text
    assert "scope_conditions" in text
    assert "epistemic_status" in text
    assert "uncertainty.sources" in text
    assert "does_not_apply_when" in text
    assert "generalization_limits" in text
    assert "显著" in text
    assert "预注册" in text
    assert "不得为了简洁省略边界条件" in text
    assert "不得用“下一周”指代下一太阳活动周期" in text
    assert "再次调用 scientific_hypothesis_get_draft 留下可回传收尾回执" in text
    assert (
        "评分、排序、rubric、search-region、Pareto 与选择轨迹只属于内部工作状态" in text
    )
    assert "原始评分结构仍留在内部状态" in text


def test_planner_agent_budget_covers_incremental_freeze():
    text = _read("jw/subagents/solar/solar_planner.yaml")
    assert "model_call_limit: 48" in text


def test_parent_does_not_rewrite_hypothesis_specialist_state():
    text = " ".join(_read("jw/prompts.py").split())
    assert "solar-hypothesis` specialist owns the candidate bodies" in text
    assert "must not" in text
    assert "must relay a bounded hypothesis result verbatim" in text
    assert "summarize, translate, reformat, shorten, correct, expand" in text
    assert "synthesize a replacement portfolio" in text


def test_solar_cycle_skill_routes_every_specialist_to_contract():
    text = _read("jw/subagents/solar/skills/solar-cycle/SKILL.md")
    for specialist in (
        "solar-planner",
        "solar-hypothesis",
        "solar-evidence",
        "solar-experiment",
        "solar-knowledge",
    ):
        assert specialist in text
    assert "Parent agents accept honest draft" in text
    assert "After the same validation problem appears twice" in text
