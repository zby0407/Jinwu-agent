"""Deterministic skill assignments shared by runtime and WebUI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

REGISTRY_PATH = Path(__file__).with_name("skill_registry.json")


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


def skills_for_agent(agent_name: str, *, path: Path = REGISTRY_PATH) -> list[str]:
    registry = load_skill_registry(path)
    names = list(registry.get("shared", []))
    if agent_name in {"JW", "jw", "main-agent"}:
        main = registry.get("main", [])
        if isinstance(main, list):
            names.extend(x for x in main if isinstance(x, str))
    assigned = registry.get("agents", {}).get(agent_name, [])
    if isinstance(assigned, list):
        names.extend(x for x in assigned if isinstance(x, str))
    # Stable order and no duplicate middleware sources.
    return [f"/skills/{name}" for name in dict.fromkeys(names)]


def skill_assignment_receipt(
    agent_name: str,
    *,
    path: Path = REGISTRY_PATH,
    skill_roots: Iterable[Path] | None = None,
) -> dict[str, Any]:
    """Return a deterministic, JSON-safe receipt for an agent's skill sources.

    The receipt is intentionally independent of model output: it records the
    exact ``/skills/...`` sources resolved from the project registry and marks
    missing ``SKILL.md`` files before a graph is built.  Callers can persist it
    alongside a run manifest or inject it into runtime diagnostics.
    """
    sources = skills_for_agent(agent_name, path=path)
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
    status = "ok" if not missing else ("partial" if len(missing) < len(sources) else "missing")
    return {
        "schema_version": "jw-skill-receipt-v1",
        "agent": agent_name,
        "registry": str(Path(path).resolve()),
        "skills": sources,
        "skill_count": len(sources),
        "missing": missing,
        "status": status,
    }


def skill_receipt_for_sources(
    agent_name: str,
    sources: Iterable[str],
    *,
    path: Path = REGISTRY_PATH,
    skill_roots: Iterable[Path] | None = None,
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
    )
    missing = [source for source in source_list if source not in base_receipt["skills"]]
    missing.extend(source for source in base_receipt["missing"] if source in source_list)
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
