"""Deterministic skill assignments shared by runtime and WebUI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
    conditional_skills = data.get("conditional_skills", {})
    if not isinstance(shared, list) or not all(isinstance(x, str) for x in shared):
        raise ValueError(f"{path}: shared must be a list of strings")
    if not isinstance(agents, dict):
        raise ValueError(f"{path}: agents must be an object")
    if not isinstance(conditional_skills, dict):
        raise ValueError(f"{path}: conditional_skills must be an object")
    for name, spec in conditional_skills.items():
        if not isinstance(name, str) or not isinstance(spec, dict):
            raise ValueError(f"{path}: conditional skill entries must be objects")
        targets = spec.get("agents", [])
        if not isinstance(targets, list) or not all(
            isinstance(target, str) for target in targets
        ):
            raise ValueError(
                f"{path}: conditional skill agents must be a list of strings"
            )
    return data


def skills_for_agent(agent_name: str, *, path: Path = REGISTRY_PATH) -> list[str]:
    registry = load_skill_registry(path)
    names = list(registry.get("shared", []))
    assigned = registry.get("agents", {}).get(agent_name, [])
    if isinstance(assigned, list):
        names.extend(x for x in assigned if isinstance(x, str))
    # Conditional Skills are mounted for their target roles so they are
    # discoverable at runtime, but their registry metadata keeps them out of
    # the default UI assignment. The Skill's trigger still decides whether it
    # is loaded for the current research question.
    for name, spec in registry.get("conditional_skills", {}).items():
        if (
            isinstance(name, str)
            and isinstance(spec, dict)
            and agent_name in spec.get("agents", [])
        ):
            names.append(name)
    # Stable order and no duplicate middleware sources.
    return [f"/skills/{name}" for name in dict.fromkeys(names)]
