"""Guardrails that keep specialist prompts aligned with closed contracts."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_solar_planner_requires_bind_validate_freeze_and_receipt():
    text = _read("EvoScientist/subagents/solar/solar_planner.yaml")
    sequence = (
        "research_planner_get_brief",
        "research_planner_validate_plan",
        "research_planner_freeze_plan",
    )
    positions = [text.index(name, text.index("system_prompt")) for name in sequence]
    assert positions == sorted(positions)
    assert "free-form substitute" in text
    assert "planner/runs/<run_id>/" in text


def test_parent_rejects_unreceipted_specialist_prose():
    text = _read("EvoScientist/prompts.py")
    assert "Reject free-form prose" in text
    assert "successful freeze run_id/path" in text
    assert "successful finalize run_id/report path" in text


def test_knowledge_agent_requires_bound_focus_and_confidence_cap():
    text = _read("EvoScientist/subagents/solar/solar_knowledge.yaml")
    assert "lit_bind_task → lit_search → lit_fetch" in text
    assert "hard maximum of medium" in text
    assert "A DOI identifies a source but is not promotion evidence" in text
    assert "cross-run replication or a named expert review" in text
    assert "a paper or DOI never auto-promotes" in text


def test_evidence_agent_uses_hypothesis_contract_tools():
    text = _read("EvoScientist/subagents/solar/solar_evidence.yaml")
    for name in (
        "scientific_hypothesis_bind_request",
        "scientific_hypothesis_bind_evidence",
        "scientific_hypothesis_validate_response",
        "scientific_hypothesis_freeze",
    ):
        assert name in text


def test_solar_cycle_skill_routes_every_specialist_to_contract():
    text = _read(
        "EvoScientist/subagents/solar/skills/solar-cycle/SKILL.md"
    )
    for specialist in (
        "solar-planner",
        "solar-hypothesis",
        "solar-evidence",
        "solar-experiment",
        "solar-knowledge",
    ):
        assert specialist in text
    assert "Parent agents must reject" in text
