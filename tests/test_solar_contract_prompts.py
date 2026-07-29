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
    assert "target 5 entries and never exceed 7" in text
    assert (
        "call kb_read and then scientific_hypothesis_bind_wiki_evidence immediately"
        in text
    )
    assert "Call lit_bundle_build" in text
    assert "scientific_hypothesis_bind_literature_evidence" in text
    assert "target 3 sources and never exceed 5" in text
    assert (
        "the binding tool will reject an entry without a prior kb_read receipt" in text
    )
    assert "immediately persist H0 or the first complete candidate" in text
    assert "A query hit is source discovery only" in text
    assert "binding tool rechecks canonical status" in text
    assert "never as observational support" in text
    assert "Candidate/canonical status and confidence metadata never make" in text
    assert "must never write, propose, import, deprecate, review, or promote" in text
    assert "The parent task description is transport, not evidence" in text
    assert "Do not accept a parent-written Wiki summary" in text
    assert "scientific_hypothesis_update_draft" in text
    assert "禁止先在自然语言中写完整组合、最后才尝试保存" in text
    assert "必须调用 scientific_hypothesis_get_draft" in text


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
