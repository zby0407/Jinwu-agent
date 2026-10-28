"""Scoped skill loading: aggregate source + per-agent allowlist.

``deepagents``' ``SkillsMiddleware`` treats every ``sources`` entry as a
directory *containing* skill directories: it calls ``backend.ls(source)`` once
and expects the result's sub-directories to be skills.  JW used to pass one
source per skill (``/skills/<name>``), so every source listed the *contents* of
a single skill directory (``SKILL.md``) and ``len(skills) == 0`` — the skill
toggle and the "user installs a skill" path were decorative.

The fix keeps one aggregate source (``/skills/``) and moves the per-agent
allowlist into the backend (``jw.backends.ScopedSkillsBackend``), fed from the
already-filtered ``skills_for_agent`` list (registry assignment + user installs
- user-disabled toggles).

These tests run the real discovery code paths (real
``MergedSkillsBackend``/``CompositeBackend``, real
``_list_skills_with_errors``) against temporary skill tiers, so they pin the
observable contract instead of the wrapper's internals.
"""

from __future__ import annotations

import asyncio
import textwrap

import pytest
from deepagents.backends import CompositeBackend, FilesystemBackend
from deepagents.backends.protocol import PERMISSION_DENIED
from deepagents.middleware.skills import (
    SkillsMiddleware,
    _alist_skills_with_errors,
    _list_skills_with_errors,
)

from jw import paths
from jw.agent import (
    SKILLS_ROOT_SOURCE,
    _ensure_general_purpose_subagent,
    _inject_subagent_middleware,
    _scoped_skills_backend,
    _skill_names_from_sources,
)
from jw.backends import MergedSkillsBackend, ScopedSkillsBackend
from jw.skill_settings import set_skill_enabled
from jw.subagents.skill_registry import skills_for_agent
from jw.utils import _filter_disabled_skills, load_subagents

# =============================================================================
# Fixtures / helpers
# =============================================================================


@pytest.fixture
def tiers(tmp_path, monkeypatch):
    """Isolate the settings file, workspace and both installable skill tiers."""

    data_dir = tmp_path / "data"
    workspace = tmp_path / "workspace"
    user_skills = tmp_path / "user-skills"
    global_skills = data_dir / "skills"
    for directory in (
        workspace,
        user_skills,
        global_skills,
        data_dir / "memories",
    ):
        directory.mkdir(parents=True)
    monkeypatch.setattr(paths, "DATA_DIR", data_dir)
    monkeypatch.setattr(paths, "USER_SKILLS_DIR", user_skills)
    monkeypatch.setattr(paths, "GLOBAL_SKILLS_DIR", global_skills)
    monkeypatch.setattr(paths, "MEMORIES_DIR", data_dir / "memories")
    return {
        "data": data_dir,
        "workspace": workspace,
        "user_skills": user_skills,
        "global_skills": global_skills,
    }


def _install(root, name: str, *, body: str = "") -> None:
    """Create ``<root>/<name>/SKILL.md`` (the shape the loader expects)."""

    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} test skill\n---\n{body}",
        encoding="utf-8",
    )


def _composite(tiers) -> CompositeBackend:
    """The same route layout ``create_cli_agent`` / the default backend use."""

    merged = MergedSkillsBackend(
        primary_dir=str(tiers["user_skills"]),
        global_dir=str(tiers["global_skills"]),
        secondary_dir=str(tiers["user_skills"]),
        secondary_roots=(),  # no bundled tiers in the test
    )
    return CompositeBackend(
        default=FilesystemBackend(root_dir=str(tiers["workspace"]), virtual_mode=True),
        routes={"/skills/": merged},
    )


def _loaded_names(backend, source: str = SKILLS_ROOT_SOURCE) -> list[str]:
    skills, error = _list_skills_with_errors(backend, source)
    assert error is None
    return sorted(skill["name"] for skill in skills)


def _aloaded_names(backend, source: str = SKILLS_ROOT_SOURCE) -> list[str]:
    skills, error = asyncio.run(_alist_skills_with_errors(backend, source))
    assert error is None
    return sorted(skill["name"] for skill in skills)


def _subagent_cfg():
    """Minimal config stand-in: memory features off, no call-limit surprises."""

    from unittest.mock import MagicMock

    from jw.config import MemoryObservationWriter

    mock = MagicMock()
    mock.model = "test-model"
    mock.provider = "test-provider"
    # Auxiliary == main model, so no extra chat model is constructed.
    mock.auxiliary_model = "test-model"
    mock.auxiliary_provider = "test-provider"
    mock.model_fallbacks = None
    mock.memory_profile_enabled = False
    mock.memory_observations_enabled = False
    mock.memory_observation_writer = MemoryObservationWriter.ALL
    mock.memory_workers_enabled = False
    mock.subagent_model_call_limit = 16
    mock.subagent_model_call_hard_limit = 32
    mock.subagent_tool_call_limit = 64
    return mock


# =============================================================================
# The regression: per-skill sources discover nothing
# =============================================================================


def test_per_skill_sources_load_no_skills(tiers):
    """``/skills/<name>`` as a source lists the skill's *contents*, not skills.

    This is the deepagents contract the fix works around; if a future
    deepagents release starts expanding per-skill sources, this test fails and
    the aggregate-root workaround can be reconsidered.
    """

    _install(tiers["user_skills"], "alpha")
    _install(tiers["user_skills"], "beta")
    composite = _composite(tiers)

    for name in ("alpha", "beta"):
        assert _loaded_names(composite, f"/skills/{name}") == []

    # The aggregate root sees both.
    assert _loaded_names(composite) == ["alpha", "beta"]


# =============================================================================
# The fix: aggregate root + allowlist inside the backend
# =============================================================================


def test_scoped_aggregate_loads_only_allowed_skills(tiers):
    _install(tiers["user_skills"], "alpha")
    _install(tiers["user_skills"], "beta")
    _install(tiers["user_skills"], "gamma")

    view = ScopedSkillsBackend(_composite(tiers), ["/skills/alpha", "/skills/gamma"])

    assert view.allowed == frozenset({"alpha", "gamma"})
    assert _loaded_names(view) == ["alpha", "gamma"]
    # Async path used by deployed async sub-agents must agree.
    assert _aloaded_names(view) == ["alpha", "gamma"]


def test_scoped_aggregate_with_no_allowed_skills_loads_nothing(tiers):
    _install(tiers["user_skills"], "alpha")

    view = ScopedSkillsBackend(_composite(tiers), [])

    assert _loaded_names(view) == []


def test_disallowed_skill_files_are_denied_not_downloaded(tiers):
    """A leaked dir listing must not be enough to read another agent's skill."""

    _install(tiers["user_skills"], "alpha", body="ALPHA BODY\n")
    _install(tiers["user_skills"], "beta", body="BETA BODY\n")
    composite = _composite(tiers)
    view = ScopedSkillsBackend(composite, ["alpha"])

    responses = view.download_files(["/skills/alpha/SKILL.md", "/skills/beta/SKILL.md"])
    assert len(responses) == 2
    assert responses[0].error is None
    assert "ALPHA BODY" in responses[0].content.decode()
    assert responses[1].error == PERMISSION_DENIED
    assert responses[1].content is None

    async_responses = asyncio.run(
        view.adownload_files(["/skills/beta/SKILL.md", "/skills/alpha/SKILL.md"])
    )
    assert async_responses[0].error == PERMISSION_DENIED
    assert async_responses[1].error is None

    # Direct reads are denied too (defence in depth, e.g. from tool calls).
    allowed_read = view.read("/skills/alpha/SKILL.md")
    assert allowed_read.error is None
    assert "ALPHA BODY" in allowed_read.file_data["content"]
    blocked_read = view.read("/skills/beta/SKILL.md")
    assert PERMISSION_DENIED in blocked_read.error
    assert blocked_read.file_data is None
    blocked_async_read = asyncio.run(view.aread("/skills/beta/SKILL.md"))
    assert PERMISSION_DENIED in blocked_async_read.error


def test_scoped_view_passes_unrelated_paths_through(tiers):
    """Workspace/memories paths keep the wrapped backend's behaviour."""

    _install(tiers["user_skills"], "alpha")
    composite = _composite(tiers)
    view = ScopedSkillsBackend(composite, ["alpha"])

    assert view.inner is composite
    # Delegation for everything we do not intercept.
    assert callable(view.execute)
    assert view.ls("/").entries == composite.ls("/").entries
    # Writes still go to the workspace tier via the delegated backend.
    assert view.write("/notes.md", "hello").error is None
    assert (tiers["workspace"] / "notes.md").read_text() == "hello"


def test_skill_names_from_sources_normalizes_and_dedups():
    assert _skill_names_from_sources(
        [
            "/skills/alpha",
            "/skills/alpha/SKILL.md",
            "/skills/beta",
            "beta",
            "/skills/",
            None,
        ]
    ) == ["alpha", "beta"]


def test_scoped_backend_wraps_instance_and_factory(tiers):
    _install(tiers["user_skills"], "alpha")
    composite = _composite(tiers)

    instance_view = _scoped_skills_backend(composite, ["/skills/alpha"])
    assert isinstance(instance_view, ScopedSkillsBackend)
    assert instance_view.inner is composite
    assert instance_view.allowed == frozenset({"alpha"})

    created: list[object] = []

    def factory(runtime):
        created.append(runtime)
        return composite

    wrapped_factory = _scoped_skills_backend(factory, ["/skills/alpha"])
    # A wrapper factory must NOT be a BackendProtocol instance, otherwise
    # ``_resolve_backend`` would return the factory instead of calling it.
    assert callable(wrapped_factory)
    view = wrapped_factory("tool-runtime")
    assert isinstance(view, ScopedSkillsBackend)
    assert view.inner is composite
    assert created == ["tool-runtime"]


# =============================================================================
# End-to-end allowlist: registry + installed - disabled
# =============================================================================


def test_allowlist_is_registry_plus_installed_minus_disabled(tiers, tmp_path):
    """A toggled-off or unassigned skill is never loaded; new installs are."""

    for name in ("alpha", "beta", "gamma", "delta"):
        _install(tiers["user_skills"], name)

    registry = tmp_path / "skill_registry.json"
    registry.write_text(
        '{"version": 1, "shared": ["alpha"], "agents": {"job-agent": ["beta"]},'
        ' "adaptations": {}}',
        encoding="utf-8",
    )
    set_skill_enabled("beta", False)  # user turns the role skill off

    sources = skills_for_agent("job-agent", path=registry, installed=["gamma", "delta"])
    assert sources == ["/skills/alpha", "/skills/gamma", "/skills/delta"]

    view = _scoped_skills_backend(_composite(tiers), sources)
    assert _loaded_names(view) == ["alpha", "delta", "gamma"]

    # An installed skill the agent never got is not visible either.
    unassigned = ScopedSkillsBackend(_composite(tiers), ["alpha"])
    assert "beta" not in _loaded_names(unassigned)


# =============================================================================
# Wiring: main agent, general-purpose subagent and YAML sub-agents
# =============================================================================


def test_main_agent_middleware_uses_aggregate_source(tiers, monkeypatch):
    """``_get_default_middleware`` mounts skills once, over the allowlist."""

    from unittest.mock import MagicMock

    from jw import agent as agent_mod

    monkeypatch.setattr(
        "jw.middleware.create_tool_selector_middleware",
        lambda *a, **k: [MagicMock(), MagicMock()],
    )
    _install(tiers["user_skills"], "alpha")
    _install(tiers["user_skills"], "beta")
    composite = _composite(tiers)
    chat_model = MagicMock(profile={"max_input_tokens": 200_000})

    middleware = agent_mod._get_default_middleware(
        workspace_dir=tiers["workspace"],
        cfg=_subagent_cfg(),
        chat_model=chat_model,
        memory_source_agent="JW",
        skills_backend=composite,
        skill_sources=["/skills/alpha"],
    )

    skills_middleware = [m for m in middleware if isinstance(m, SkillsMiddleware)]
    assert len(skills_middleware) == 1
    assert skills_middleware[0].sources == [SKILLS_ROOT_SOURCE]
    backend = skills_middleware[0]._backend
    assert isinstance(backend, ScopedSkillsBackend)
    assert backend.allowed == frozenset({"alpha"})
    assert _loaded_names(backend) == ["alpha"]

    receipt = next(
        m for m in middleware if type(m).__name__ == "SkillReceiptMiddleware"
    )
    # The receipt still reports the concrete per-skill sources.
    assert receipt.receipt["skills"] == ["/skills/alpha"]


def test_subagent_spec_skills_become_scoped_aggregate_middleware(tiers):
    from unittest.mock import MagicMock

    _install(tiers["user_skills"], "alpha")
    _install(tiers["user_skills"], "beta")
    composite = _composite(tiers)
    subs = [
        {"name": "job-agent", "skills": ["/skills/alpha"], "tools": []},
        {"name": "no-skills-agent"},
    ]

    _inject_subagent_middleware(
        subs,
        workspace_dir=tiers["workspace"],
        cfg=_subagent_cfg(),
        chat_model=MagicMock(profile={"max_input_tokens": 200_000}),
        skills_backend=composite,
    )

    # The raw per-skill list is consumed, so deepagents cannot rebuild an
    # unscoped SkillsMiddleware from it.
    assert "skills" not in subs[0]
    skills_middleware = [
        m for m in subs[0]["middleware"] if isinstance(m, SkillsMiddleware)
    ]
    assert len(skills_middleware) == 1
    assert skills_middleware[0].sources == [SKILLS_ROOT_SOURCE]
    assert _loaded_names(skills_middleware[0]._backend) == ["alpha"]

    assert not any(
        isinstance(m, SkillsMiddleware) for m in subs[1].get("middleware", [])
    )


def test_subagent_specs_keep_declared_skills_without_backend():
    """The standalone async-subagent factory passes no backend; keep the list."""

    from unittest.mock import MagicMock

    subs = [{"name": "writing-agent", "skills": ["/skills/alpha"]}]

    _inject_subagent_middleware(
        subs,
        cfg=_subagent_cfg(),
        chat_model=MagicMock(profile={"max_input_tokens": 200_000}),
    )

    assert subs[0]["skills"] == ["/skills/alpha"]
    assert not any(isinstance(m, SkillsMiddleware) for m in subs[0]["middleware"])


def test_general_purpose_subagent_carries_filtered_sources_only_when_given():
    from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT

    default_subs: list[dict] = []
    _ensure_general_purpose_subagent(default_subs)
    assert default_subs[0]["name"] == GENERAL_PURPOSE_SUBAGENT["name"]
    assert "skills" not in default_subs[0]

    scoped_subs: list[dict] = []
    _ensure_general_purpose_subagent(scoped_subs, skill_sources=["/skills/alpha"])
    assert scoped_subs[0]["skills"] == ["/skills/alpha"]


# =============================================================================
# YAML sub-agents: explicit lists still honour the user's toggle
# =============================================================================


def test_filter_disabled_skills_drops_toggled_off_sources(tiers):
    set_skill_enabled("beta", False)

    assert _filter_disabled_skills(["/skills/alpha", "/skills/beta"]) == [
        "/skills/alpha"
    ]
    # Non-skill entries and non-list values are untouched.
    assert _filter_disabled_skills(["/skills/alpha", "/memories/"]) == [
        "/skills/alpha",
        "/memories/",
    ]
    assert _filter_disabled_skills("/skills/alpha") == "/skills/alpha"


def test_filter_disabled_skills_returns_empty_when_all_disabled(tiers):
    set_skill_enabled("alpha", False)

    assert _filter_disabled_skills(["/skills/alpha"]) == []


def test_load_subagents_applies_the_toggle_to_explicit_skills(tiers, tmp_path):
    set_skill_enabled("beta", False)
    (tmp_path / "job.yaml").write_text(
        textwrap.dedent(
            """
            job-agent:
              description: Runs the job
              system_prompt: ""
              tools: []
              skills:
                - /skills/alpha
                - /skills/beta
            """
        ),
        encoding="utf-8",
    )

    subs = load_subagents(tmp_path, tool_registry={})

    assert subs[0]["skills"] == ["/skills/alpha"]
