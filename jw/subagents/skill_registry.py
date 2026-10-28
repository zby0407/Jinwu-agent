"""Deterministic skill assignments shared by runtime and WebUI."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .. import skill_settings

REGISTRY_PATH = Path(__file__).with_name("skill_registry.json")

# Agent names that receive the registry's ``main`` (orchestration) section.
_MAIN_AGENT_ALIASES = frozenset({"JW", "jw", "main-agent"})


def load_skill_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"version": 1, "shared": [], "agents": {}, "adaptations": {}}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: skill registry must be an object")
    shared = data.get("shared", [])
    agents = data.get("agents", {})
    if not isinstance(shared, list) or not all(isinstance(x, str) for x in shared):
        raise ValueError(f"{path}: shared must be a list of strings")
    if not isinstance(agents, dict):
        raise ValueError(f"{path}: agents must be an object")
    return data


def _assigned_skill_names(agent_name: str, path: Path = REGISTRY_PATH) -> list[str]:
    """Bare skill names assigned to ``agent_name``, de-duplicated in order."""

    registry = load_skill_registry(path)
    names = list(registry.get("shared", []))
    if agent_name in _MAIN_AGENT_ALIASES:
        main = registry.get("main", [])
        if isinstance(main, list):
            names.extend(x for x in main if isinstance(x, str))
    assigned = registry.get("agents", {}).get(agent_name, [])
    if isinstance(assigned, list):
        names.extend(x for x in assigned if isinstance(x, str))
    # Stable order and no duplicate middleware sources.
    return list(dict.fromkeys(names))


def skills_for_agent(
    agent_name: str,
    *,
    path: Path = REGISTRY_PATH,
    disabled: set[str] | None = None,
) -> list[str]:
    """Return the ``/skills/<name>`` sources the agent should actually get.

    User-disabled skills (``<DATA_DIR>/skill_settings.json``, see
    :mod:`jw.skill_settings`) are removed here.  This function is the single
    choke point shared by the main agent and every registry-driven sub-agent,
    so one toggle covers all of them.  ``disabled`` injects the decision set
    for tests; by default it is read from the settings file.
    """

    names = _assigned_skill_names(agent_name, path)
    enabled = skill_settings.filter_enabled(names, disabled=disabled)
    return [f"/skills/{name}" for name in enabled]


def disabled_skill_sources_for_agent(
    agent_name: str,
    *,
    path: Path = REGISTRY_PATH,
    disabled: set[str] | None = None,
) -> list[str]:
    """Return the sorted ``/skills/<name>`` sources withheld from the agent."""

    excluded = (
        skill_settings.disabled_skill_names() if disabled is None else set(disabled)
    )
    return sorted(
        f"/skills/{name}"
        for name in _assigned_skill_names(agent_name, path)
        if name in excluded
    )


def skill_assignment_receipt(
    agent_name: str,
    *,
    path: Path = REGISTRY_PATH,
    skill_roots: Iterable[Path] | None = None,
    disabled: set[str] | None = None,
) -> dict[str, Any]:
    """Return a deterministic, JSON-safe receipt for an agent's skill sources.

    The receipt is intentionally independent of model output: it records the
    exact ``/skills/...`` sources resolved from the project registry and marks
    missing ``SKILL.md`` files before a graph is built.  Callers can persist it
    alongside a run manifest or inject it into runtime diagnostics.
    """
    sources = skills_for_agent(agent_name, path=path, disabled=disabled)
    withheld = disabled_skill_sources_for_agent(
        agent_name, path=path, disabled=disabled
    )
    if skill_roots is None:
        # A registry in ``jw/subagents`` is accompanied by bundle-local skill
        # roots (``core/skills``, ``solar/skills``, ...).  For test or exported
        # registries, callers can pass explicit roots.
        base = Path(path).resolve().parent
        skill_roots = [
            child / "skills"
            for child in base.iterdir()
            if child.is_dir() and (child / "skills").is_dir()
        ]
    roots = [Path(root).expanduser().resolve() for root in skill_roots]

    def exists(source: str) -> bool:
        name = source.removeprefix("/skills/")
        return any((root / name / "SKILL.md").is_file() for root in roots)

    missing = [source for source in sources if not exists(source)]
    status = (
        "ok"
        if not missing
        else ("partial" if len(missing) < len(sources) else "missing")
    )
    return {
        "schema_version": "jw-skill-receipt-v1",
        "agent": agent_name,
        "registry": str(Path(path).resolve()),
        "skills": sources,
        "skill_count": len(sources),
        "missing": missing,
        # Sources withheld by the user's skill switches.  Recorded so evidence
        # review can tell "this agent never had the skill" apart from
        # "this skill was toggled off".
        "disabled": withheld,
        "status": status,
    }


def skill_receipt_for_sources(
    agent_name: str,
    sources: Iterable[str],
    *,
    path: Path = REGISTRY_PATH,
    skill_roots: Iterable[Path] | None = None,
    disabled: set[str] | None = None,
) -> dict[str, Any]:
    """Build a receipt from the exact sources handed to middleware.

    This variant is used by graph construction so the receipt cannot drift
    from the resolved ``SkillsMiddleware`` source list.
    """
    source_list = [str(source) for source in dict.fromkeys(sources)]
    base_receipt = skill_assignment_receipt(
        agent_name,
        path=path,
        skill_roots=skill_roots,
        disabled=disabled,
    )
    # A user-disabled source is a known source that merely was not handed to
    # middleware; it must not be reported as missing evidence.
    known = set(base_receipt["skills"]) | set(base_receipt["disabled"])
    missing = [source for source in source_list if source not in known]
    missing.extend(
        source for source in base_receipt["missing"] if source in source_list
    )
    # Registry paths are authoritative when present; a source may also be a
    # deliberate ``/skills/`` aggregate route used by the main agent.
    missing = list(dict.fromkeys(source for source in missing if source != "/skills/"))
    return {
        **base_receipt,
        "skills": source_list,
        "skill_count": len(source_list),
        "missing": missing,
        "status": "ok" if not missing else "partial",
    }
