import json
from pathlib import Path

from jw.subagents.skill_registry import (
    load_skill_registry,
    skill_assignment_receipt,
    skills_for_agent,
)


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
    assert Path(__file__).parents[1].joinpath("jw/subagents/skill_registry.json").is_file()
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
            agent
            for agent, skills in registry["agents"].items()
            if skill in skills
        }
        assert assigned == expected_agents, skill
        assert Path(__file__).parents[1].joinpath(
            "jw/subagents/solar/skills", skill, "SKILL.md"
        ).is_file()


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
    for skill_name in ("verification-before-completion", "solar-cycle-forecast-validation"):
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
