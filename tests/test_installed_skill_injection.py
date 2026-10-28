"""User-installed skills must reach the runtime skill sources.

A skill the user drops into the workspace tier (``paths.USER_SKILLS_DIR``) or
the global tier (``paths.GLOBAL_SKILLS_DIR``) as ``<name>/SKILL.md`` has to end
up in the ``/skills/<name>`` list handed to ``SkillsMiddleware`` — otherwise it
only shows up in the UI list and the agent never loads it.

These tests pin the discovery rules (tiers, ordering, shadowing, name safety),
the registry-first ordering of :func:`skills_for_agent`, the interaction with
the persistent on/off switches, and receipt self-consistency (an installed
skill is a known source, never a missing one).
"""

import json

import pytest

from jw import paths
from jw.skill_settings import set_skill_enabled
from jw.subagents.skill_registry import (
    disabled_skill_sources_for_agent,
    installed_skill_names,
    installed_skill_roots,
    skill_assignment_receipt,
    skill_receipt_for_sources,
    skills_for_agent,
)


@pytest.fixture(autouse=True)
def tiers(tmp_path, monkeypatch):
    """Isolate the settings file and both installable skill tiers in ``tmp``."""

    data_dir = tmp_path / "data"
    workspace_skills = tmp_path / "workspace-skills"
    global_skills = data_dir / "skills"
    monkeypatch.setattr(paths, "DATA_DIR", data_dir)
    monkeypatch.setattr(paths, "USER_SKILLS_DIR", workspace_skills)
    monkeypatch.setattr(paths, "GLOBAL_SKILLS_DIR", global_skills)
    return {
        "workspace": workspace_skills,
        "global": global_skills,
        "data": data_dir,
        "tmp": tmp_path,
    }


def _install(root, name, *, body=""):
    """Create ``<root>/<name>/SKILL.md`` and return the skill directory."""

    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test skill\n---\n{body}", encoding="utf-8"
    )
    return skill_dir


# ``debug-agent`` is the smallest registry role: three shared skills, no
# role-specific extras.
REGISTRY_ONLY = [
    "/skills/verification-before-completion",
    "/skills/scientific-writing",
    "/skills/scientific-visualization",
]


def test_installed_skill_names_reads_workspace_then_global(tiers):
    _install(tiers["workspace"], "w-alpha")
    _install(tiers["global"], "g-beta")

    assert installed_skill_roots() == (tiers["workspace"], tiers["global"])
    assert installed_skill_names() == ["w-alpha", "g-beta"]


def test_installed_skill_names_keeps_workspace_shadowing_a_global_copy(tiers):
    _install(tiers["global"], "shared-name")
    _install(tiers["workspace"], "shared-name")

    # The runtime backend resolves USER before GLOBAL, so the workspace copy
    # shadows the global one and the name appears exactly once.
    assert installed_skill_names() == ["shared-name"]


@pytest.mark.parametrize(
    "directory",
    [".hidden", "with space", "a..b", "sla/sh", "back\\slash"],
)
def test_installed_skill_names_ignores_unsafe_directory_names(tiers, directory):
    _install(tiers["workspace"], directory)

    assert installed_skill_names() == []


def test_installed_skill_names_ignores_directories_without_skill_md(tiers):
    (tiers["workspace"] / "not-a-skill").mkdir(parents=True)
    (tiers["workspace"] / "loose-file.md").write_text("# not a directory\n")
    _install(tiers["workspace"], "real-skill")

    assert installed_skill_names() == ["real-skill"]


def test_installed_skill_names_accepts_injected_roots(tiers):
    _install(tiers["workspace"], "w-alpha")
    _install(tiers["global"], "g-beta")

    assert installed_skill_names(roots=[tiers["global"]]) == ["g-beta"]
    assert installed_skill_names(roots=[]) == []


def test_installed_skill_names_tolerates_missing_roots(tiers):
    # Fresh install: neither tier exists yet.
    assert not tiers["workspace"].exists()
    assert not tiers["global"].exists()
    assert installed_skill_names() == []


def test_skills_for_agent_appends_installed_after_the_registry(tiers):
    _install(tiers["workspace"], "w-alpha")
    _install(tiers["global"], "g-beta")

    assert skills_for_agent("debug-agent", installed=[]) == REGISTRY_ONLY
    # Registry first (bundled JW skills keep priority), installed afterwards in
    # tier order.
    assert skills_for_agent("debug-agent") == [
        *REGISTRY_ONLY,
        "/skills/w-alpha",
        "/skills/g-beta",
    ]


def test_installed_skill_does_not_duplicate_a_registry_assignment(tiers):
    _install(tiers["workspace"], "scientific-writing")

    skills = skills_for_agent("debug-agent")
    assert skills == REGISTRY_ONLY
    # Discovery still reports it as installed; only the source list is deduped.
    assert installed_skill_names() == ["scientific-writing"]


def test_main_agent_receives_installed_skills(tiers):
    _install(tiers["workspace"], "w-alpha")

    assert "/skills/w-alpha" in skills_for_agent("JW")
    assert skills_for_agent("JW").index("/skills/w-alpha") > 0


def test_a_disabled_installed_skill_is_withheld_but_known(tiers):
    _install(tiers["workspace"], "w-alpha")
    set_skill_enabled("w-alpha", False)

    assert "/skills/w-alpha" not in skills_for_agent("debug-agent")
    assert disabled_skill_sources_for_agent("debug-agent") == ["/skills/w-alpha"]

    receipt = skill_assignment_receipt("debug-agent")
    assert receipt["skills"] == REGISTRY_ONLY
    assert receipt["disabled"] == ["/skills/w-alpha"]
    assert receipt["skill_count"] == len(receipt["skills"])
    assert receipt["missing"] == []
    assert receipt["status"] == "ok"


def test_an_injected_disabled_set_also_covers_installed_skills(tiers):
    _install(tiers["global"], "g-beta")

    assert skills_for_agent("debug-agent", disabled={"g-beta"}) == REGISTRY_ONLY
    assert disabled_skill_sources_for_agent("debug-agent", disabled={"g-beta"}) == [
        "/skills/g-beta"
    ]


def test_installed_skills_are_not_reported_missing_by_the_receipt(tiers):
    _install(tiers["workspace"], "w-alpha")
    _install(tiers["global"], "g-beta")

    receipt = skill_assignment_receipt("debug-agent")

    assert receipt["skills"] == [*REGISTRY_ONLY, "/skills/w-alpha", "/skills/g-beta"]
    # Default roots include the installable tiers; without them these two
    # sources would be flagged as missing evidence.
    assert receipt["missing"] == []
    assert receipt["status"] == "ok"


def test_sources_receipt_treats_installed_sources_as_known(tiers):
    _install(tiers["workspace"], "w-alpha")

    receipt = skill_receipt_for_sources(
        "debug-agent", [*REGISTRY_ONLY, "/skills/w-alpha"]
    )

    assert receipt["missing"] == []
    assert receipt["status"] == "ok"
    assert receipt["skill_count"] == len(REGISTRY_ONLY) + 1


def test_installed_empty_list_keeps_the_registry_only_view(tiers):
    _install(tiers["workspace"], "w-alpha")

    assert skills_for_agent("debug-agent", installed=[]) == REGISTRY_ONLY
    assert disabled_skill_sources_for_agent("debug-agent", installed=[]) == []

    receipt = skill_assignment_receipt("debug-agent", installed=[])
    assert receipt["skills"] == REGISTRY_ONLY
    assert receipt["missing"] == []
    assert receipt["status"] == "ok"


def test_receipt_records_a_registry_skill_that_really_is_absent(tiers, tmp_path):
    registry = {
        "version": 1,
        "shared": ["verification-before-completion"],
        "agents": {"solar-data": ["ghost-skill"]},
    }
    registry_path = tmp_path / "skill_registry.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    for name in ("verification-before-completion", "w-alpha"):
        _install(tiers["workspace"], name)
    (tmp_path / "skills" / "verification-before-completion").mkdir(parents=True)
    (tmp_path / "skills" / "verification-before-completion" / "SKILL.md").write_text(
        "# bundled\n", encoding="utf-8"
    )

    receipt = skill_assignment_receipt(
        "solar-data",
        path=registry_path,
        skill_roots=[tiers["workspace"], tmp_path / "skills"],
    )

    assert receipt["skills"] == [
        "/skills/verification-before-completion",
        "/skills/ghost-skill",
        "/skills/w-alpha",
    ]
    assert receipt["missing"] == ["/skills/ghost-skill"]
    assert receipt["status"] == "partial"
