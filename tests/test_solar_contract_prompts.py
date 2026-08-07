"""Guardrails that keep specialist prompts aligned with closed contracts."""

from __future__ import annotations

from pathlib import Path

from jw.tools import get_tool_bundles

ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_solar_planner_supports_explore_checkpoint_and_publish_modes():
    text = _read("jw/subagents/solar/solar_planner.yaml")
    assert "tool_bundles: [research-planner]" in text
    assert "think-tool loop" in text
    assert "Default to exploration mode" in text
    assert "checkpoint/publication mode" in text
    assert "[RESEARCH_PRODUCER_V2]" in text
    assert "MUST call research_planner_get_brief" in text
    assert "research_planner_update_draft" in text
    assert "exactly one plan_content section per call" in text
    assert "draft_checkpoint.next_section" in text
    assert "research_planner_validate_draft" in text
    assert "research_planner_freeze_plan before returning" in text
    assert "not permission to return an unfrozen draft" in text
    assert "freeze only when publication was requested" in text
    assert "planner/runs/<run_id>/" in text


def test_parent_accepts_partial_results_and_bounds_repair():
    text = _read("jw/prompts.py")
    assert "valid outcome" in text
    assert "Exploratory work may" in text
    assert "If it recurs, stop the loop" in text


def test_solar_data_requires_hash_bound_inputs_before_discovery():
    text = _read("jw/subagents/solar/solar_data.yaml")
    assert "tool_bundles: [solar-features]" in text
    assert "deterministic_data_context" in text
    assert "do not call `solar_data_open_context` again" in text
    assert "Only paths in `eligible_inputs`" in text
    assert "reproduce_silso_cycle_extrema" in text
    assert "never guess `/project/data`" in text
    assert "must_stop=true" in text


def test_knowledge_agent_is_read_only_and_routes_changes_to_humans():
    text = _read("jw/subagents/solar/solar_knowledge.yaml")
    assert "tool_bundles: [reasoning, knowledge-base-inspection]" in text
    assert "restrict_tools: true" in text
    assert "never propose, import, patch, promote, deprecate, or decide" in text
    assert "kb_query" in text
    assert "kb_read" in text
    assert "human-review recommendation" in text


def test_evidence_agent_is_read_only_and_uses_typed_review_tools():
    text = _read("jw/subagents/solar/solar_evidence.yaml")
    assert (
        "tool_bundles: [reasoning, research-review, knowledge-base-inspection]" in text
    )
    assert "restrict_tools: true" in text
    review_tools = {tool.name for tool in get_tool_bundles()["research-review"]}
    for name in (
        "evidence_review_open_context",
        "evidence_review_submit_verdict",
        "evidence_review_get_status",
    ):
        assert name in review_tools
    assert "research_independent_review" not in review_tools
    assert "research_independent_review" in {
        tool.name for tool in get_tool_bundles()["research-release"]
    }
    assert "never author, patch, overwrite" in text
    assert "never edit the producer artifact" in text


def test_hypothesis_agent_reads_wiki_before_generating_candidates():
    text = _read("jw/subagents/solar/solar_hypothesis.yaml")
    assert "model_call_limit: 48" in text
    assert "tool_bundles: [knowledge-base-readonly, scientific-hypothesis]" in text
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
