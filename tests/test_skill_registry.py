from pathlib import Path

from jw.subagents.skill_registry import load_skill_registry, skills_for_agent


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


def test_registry_file_is_project_local():
    registry = load_skill_registry()
    assert Path(__file__).parents[1].joinpath("jw/subagents/skill_registry.json").is_file()
    assert registry["version"] == 1
