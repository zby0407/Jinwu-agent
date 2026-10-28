import json
from pathlib import Path

import pytest

from jw import paths
from jw.skill_settings import set_skill_enabled
from jw.subagents.skill_registry import (
    load_skill_registry,
    skill_assignment_receipt,
    skill_receipt_for_sources,
    skills_for_agent,
)


@pytest.fixture(autouse=True)
def _isolated_skill_settings(tmp_path, monkeypatch):
    """Keep the user's real ``~/.jw/skill_settings.json`` out of these tests.

    ``skills_for_agent`` now consults the persistent skill switches, so an
    unpatched ``DATA_DIR`` would let local toggles change the registry
    assertions below.  Individual tests write into the returned directory.
    """

    data_dir = tmp_path / "data"
    monkeypatch.setattr(paths, "DATA_DIR", data_dir)
    return data_dir


def test_skill_registry_assigns_shared_and_role_specific_skills():
    registry = load_skill_registry()
    assert "verification-before-completion" in registry["shared"]
    data_skills = skills_for_agent("solar-data")
    evidence_skills = skills_for_agent("solar-evidence")
    assert "/skills/verification-before-completion" in data_skills
    assert "/skills/solar-evidence-figure-production" in data_skills
    assert "/skills/solar-evidence-figure-production" in evidence_skills
    assert skills_for_agent("debug-agent") == [
        "/skills/verification-before-completion",
        "/skills/scientific-writing",
        "/skills/scientific-visualization",
    ]


def test_main_agent_gets_only_orchestration_skills():
    skills = skills_for_agent("JW")
    assert skills[:3] == [
        "/skills/verification-before-completion",
        "/skills/scientific-writing",
        "/skills/scientific-visualization",
    ]
    assert "/skills/jw-release-export-qa" in skills
    assert "/skills/solar-interaction-regime-testing" not in skills


def test_registry_file_is_project_local():
    registry = load_skill_registry()
    assert (
        Path(__file__).parents[1].joinpath("jw/subagents/skill_registry.json").is_file()
    )
    assert registry["version"] == 1


def test_solar_research_quality_skills_are_role_scoped():
    registry = load_skill_registry()
    expected = {
        "solar-cycle-forecast-validation": {
            "solar-data",
            "solar-experiment",
            "solar-evidence",
        },
        "solar-hypothesis-portfolio": {
            "solar-planner",
            "solar-hypothesis",
            "solar-evidence",
        },
        "solar-mechanism-causal-order": {
            "solar-hypothesis",
            "solar-experiment",
            "solar-evidence",
        },
        "solar-interaction-regime-testing": {
            "solar-hypothesis",
            "solar-experiment",
            "solar-evidence",
        },
        "jw-scientific-claim-consistency": {
            "solar-evidence",
            "writing-agent",
        },
    }
    for skill, expected_agents in expected.items():
        assigned = {
            agent for agent, skills in registry["agents"].items() if skill in skills
        }
        assert assigned == expected_agents, skill
        assert (
            Path(__file__)
            .parents[1]
            .joinpath("jw/subagents/solar/skills", skill, "SKILL.md")
            .is_file()
        )


def test_scientific_slides_is_available_to_the_writing_agent():
    assert "/skills/scientific-slides" in skills_for_agent("writing-agent")


def test_skill_assignment_receipt_is_machine_readable_and_reports_missing_files(
    tmp_path,
):
    registry = {
        "version": 1,
        "shared": ["verification-before-completion"],
        "agents": {"solar-data": ["solar-cycle-forecast-validation", "missing-skill"]},
    }
    registry_path = tmp_path / "skill_registry.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    for skill_name in (
        "verification-before-completion",
        "solar-cycle-forecast-validation",
    ):
        skill_dir = tmp_path / "skills" / skill_name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(f"# {skill_name}\n", encoding="utf-8")
    receipt = skill_assignment_receipt(
        "solar-data", path=registry_path, skill_roots=[tmp_path / "skills"]
    )

    assert receipt["schema_version"] == "jw-skill-receipt-v1"
    assert receipt["agent"] == "solar-data"
    assert receipt["skills"] == [
        "/skills/verification-before-completion",
        "/skills/solar-cycle-forecast-validation",
        "/skills/missing-skill",
    ]
    assert receipt["skill_count"] == 3
    assert receipt["missing"] == ["/skills/missing-skill"]
    assert receipt["status"] == "partial"


SOLAR_AGENTS = (
    "solar-planner",
    "solar-data",
    "solar-hypothesis",
    "solar-experiment",
    "solar-evidence",
    "solar-knowledge",
)


def test_disabling_a_shared_skill_hides_it_from_every_solar_agent():
    set_skill_enabled("verification-before-completion", False)

    for agent in SOLAR_AGENTS:
        assert "/skills/verification-before-completion" not in skills_for_agent(agent)
    # Other shared entries keep their place and order.
    assert skills_for_agent("debug-agent") == [
        "/skills/scientific-writing",
        "/skills/scientific-visualization",
    ]


def test_disabling_a_shared_skill_hides_it_from_the_main_agent():
    before = skills_for_agent("JW")
    assert "/skills/verification-before-completion" in before

    set_skill_enabled("verification-before-completion", False)

    assert skills_for_agent("JW") == [
        source
        for source in before
        if source != "/skills/verification-before-completion"
    ]


def test_disabling_a_main_section_skill_hides_it_from_the_main_agent():
    before = skills_for_agent("JW")
    assert "/skills/jw-release-export-qa" in before

    set_skill_enabled("jw-release-export-qa", False)

    after = skills_for_agent("JW")
    assert "/skills/jw-release-export-qa" not in after
    assert after == [s for s in before if s != "/skills/jw-release-export-qa"]
    # The main-agent-only removal must not touch role scoping.
    assert "/skills/solar-flare-forecasting" not in after


def test_disabling_a_role_scoped_skill_only_affects_its_assignees():
    before_data = skills_for_agent("solar-data")
    before_experiment = skills_for_agent("solar-experiment")
    assert "/skills/solar-flare-forecasting" in before_experiment
    assert "/skills/solar-flare-forecasting" not in before_data

    set_skill_enabled("solar-flare-forecasting", False)

    assert skills_for_agent("solar-data") == before_data
    assert skills_for_agent("solar-experiment") == [
        s for s in before_experiment if s != "/skills/solar-flare-forecasting"
    ]


def test_real_enabling_restores_the_source():
    before = skills_for_agent("solar-data")
    set_skill_enabled("solar-evidence-figure-production", False)
    assert skills_for_agent("solar-data") != before

    set_skill_enabled("solar-evidence-figure-production", True)
    assert skills_for_agent("solar-data") == before


def test_skills_for_agent_accepts_an_injected_disabled_set():
    assert skills_for_agent("debug-agent", disabled={"scientific-writing"}) == [
        "/skills/verification-before-completion",
        "/skills/scientific-visualization",
    ]


def test_receipt_records_the_disabled_sources_of_that_agent():
    set_skill_enabled("verification-before-completion", False)
    set_skill_enabled("solar-flare-forecasting", False)

    receipt = skill_assignment_receipt("solar-experiment")

    assert receipt["disabled"] == [
        "/skills/solar-flare-forecasting",
        "/skills/verification-before-completion",
    ]
    assert "/skills/solar-flare-forecasting" not in receipt["skills"]
    assert "/skills/verification-before-completion" not in receipt["skills"]
    assert receipt["skill_count"] == len(receipt["skills"])
    assert receipt["status"] == "ok"


def test_receipt_disabled_is_empty_when_nothing_is_toggled_off():
    assert skill_assignment_receipt("solar-data")["disabled"] == []


def test_sources_receipt_does_not_report_disabled_sources_as_missing():
    receipt = skill_receipt_for_sources(
        "solar-experiment",
        ["/skills/solar-flare-forecasting"],
        disabled={"solar-flare-forecasting"},
    )

    assert receipt["missing"] == []
    assert receipt["status"] == "ok"
    assert receipt["disabled"] == ["/skills/solar-flare-forecasting"]
