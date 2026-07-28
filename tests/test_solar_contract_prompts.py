"""Guardrails that keep specialist prompts aligned with closed contracts."""

from __future__ import annotations

from pathlib import Path

from jw.tools import get_tool_bundles

ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_solar_planner_supports_explore_checkpoint_and_publish_modes():
    text = _read("jw/subagents/solar/solar_planner.yaml")
    assert "Default to exploration mode" in text
    assert "checkpoint/publication mode" in text
    assert "Freeze only when publication was requested" in text
    assert "planner/runs/<run_id>/" in text


def test_parent_accepts_partial_results_and_bounds_repair():
    text = _read("jw/prompts.py")
    assert "valid outcome" in text
    assert "Exploratory work may" in text
    assert "If it recurs, stop the loop" in text


def test_knowledge_agent_requires_bound_focus_and_confidence_cap():
    text = _read("jw/subagents/solar/solar_knowledge.yaml")
    assert "lit_feed_catalog → lit_feed_sync" in text
    assert "raw source layer and never becomes a Wiki claim automatically" in text
    assert "lit_bind_task → lit_search → lit_fetch" in text
    assert "hard maximum of medium" in text
    assert "A DOI identifies a source but is not promotion evidence" in text
    assert "cross-run replication or a named expert review" in text
    assert "a paper or DOI never auto-promotes" in text


def test_evidence_agent_uses_hypothesis_contract_tools():
    text = _read("jw/subagents/solar/solar_evidence.yaml")
    assert "tool_bundles: [reasoning, scientific-hypothesis]" in text
    hypothesis_tools = {
        tool.name for tool in get_tool_bundles()["scientific-hypothesis"]
    }
    for name in (
        "scientific_hypothesis_bind_request",
        "scientific_hypothesis_bind_evidence",
        "scientific_hypothesis_bind_wiki_evidence",
        "scientific_hypothesis_update_draft",
        "scientific_hypothesis_get_draft",
        "scientific_hypothesis_validate_response",
        "scientific_hypothesis_checkpoint_draft",
        "scientific_hypothesis_get_status",
        "scientific_hypothesis_freeze",
    ):
        assert name in hypothesis_tools
    assert (
        "Call scientific_hypothesis_freeze only when publication was explicitly requested"
        in text
    )
    assert "patch the affected candidate rather than rewriting the portfolio" in text


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
    assert "默认恰好形成三个机制上可区分的候选" in text
    assert "不得因为 Wiki 列出了更多模板而扩张" in text
    assert "不再自行考虑第四或第五个" in text
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
    assert "禁止先在自然语言中写完整组合、最后才尝试保存" in text
    assert "必须调用 scientific_hypothesis_get_draft" in text
    assert "任何自然语言进度说明都不算修订" in text
    assert "下一轮必须直接发出修订工具调用" in text
    assert "不得以篇幅为由压缩或省略用户要求的字段" in text
    assert "已绑定任务文献 evidence_id" in text


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
