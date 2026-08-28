from pathlib import Path

from jw.subagents.skill_registry import load_skill_registry, skills_for_agent


def test_skill_registry_assigns_shared_and_role_specific_skills():
    registry = load_skill_registry()
    assert registry["shared"] == ["verification-before-completion"]
    data_skills = skills_for_agent("solar-data")
    evidence_skills = skills_for_agent("solar-evidence")
    assert "/skills/verification-before-completion" in data_skills
    assert "/skills/solar-evidence-figure-production" in data_skills
    assert "/skills/solar-evidence-figure-production" in evidence_skills
    assert skills_for_agent("debug-agent") == [
        "/skills/verification-before-completion",
    ]
    assert skills_for_agent("JW") == [
        "/skills/verification-before-completion",
        "/skills/jw-integration-and-final-answer",
        "/skills/scientific-writing",
        "/skills/writing-reader-facing-content",
        "/skills/jw-release-export-qa",
        "/skills/find-skills",
    ]


def test_conditional_flare_skill_is_available_only_to_target_roles():
    flare = "/skills/solar-flare-forecasting"
    for agent in load_skill_registry()["primary_agents"]:
        assert flare in skills_for_agent(agent)


def test_main_agent_runtime_sources_match_the_registry():
    from jw.agent import _main_skill_sources

    assert _main_skill_sources() == skills_for_agent("JW")
    assert "/skills/solar-cycle" not in _main_skill_sources()
    assert "/skills/solar-flare-forecasting" not in _main_skill_sources()


def test_registry_file_is_project_local():
    registry = load_skill_registry()
    assert Path(__file__).parents[1].joinpath("jw/subagents/skill_registry.json").is_file()
    assert registry["version"] == 1
