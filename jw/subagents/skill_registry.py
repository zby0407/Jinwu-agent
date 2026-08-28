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
    if not isinstance(shared, list) or not all(isinstance(x, str) for x in shared):
        raise ValueError(f"{path}: shared must be a list of strings")
    if not isinstance(agents, dict):
        raise ValueError(f"{path}: agents must be an object")
    return data


def skills_for_agent(agent_name: str, *, path: Path = REGISTRY_PATH) -> list[str]:
    registry = load_skill_registry(path)
    names = list(registry.get("shared", []))
    assigned = registry.get("agents", {}).get(agent_name, [])
    if isinstance(assigned, list):
        names.extend(x for x in assigned if isinstance(x, str))
    # Stable order and no duplicate middleware sources.
    return [f"/skills/{name}" for name in dict.fromkeys(names)]
