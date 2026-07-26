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
