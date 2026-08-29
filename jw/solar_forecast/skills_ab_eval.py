"""Small, honest A/B report for Skill wiring versus deterministic controls."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from jw.subagents.skill_registry import skill_assignment_receipt


def build_ab_report(
    registry_path: Path,
    gate_paths: Mapping[str, Path],
) -> dict[str, Any]:
    """Compare control (no role Skills) and treatment (current registry).

    This is a wiring/contract comparison, not an LLM benchmark. Numeric
    outputs are intentionally not re-scored here; the report prevents a
    changed prompt or registry from being mistaken for improved forecasting.
    """
    registry = json.loads(Path(registry_path).read_text(encoding="utf-8"))
    shared = [str(item) for item in registry.get("shared", [])]
    agents = registry.get("agents", {})
    role_specific = {
        str(agent): [str(skill) for skill in skills if str(skill) not in shared]
        for agent, skills in agents.items()
        if isinstance(skills, list)
    }
    gate_statuses: dict[str, str] = {}
    for agent, path in gate_paths.items():
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        gate_statuses[str(agent)] = str(payload.get("status", "unknown"))
    return {
        "schema_version": "jw-skills-ab-eval-v1",
        "control": {
            "skill_mode": "control",
            "shared_skill_count": 0,
            "role_specific_skill_count": 0,
            "executable_gate": False,
        },
        "treatment": {
            "skill_mode": "treatment",
            "shared_skill_count": len(shared),
            "role_specific_skill_count": sum(len(items) for items in role_specific.values()),
            "role_specific_skills": role_specific,
            "executable_gate": True,
            "gate_statuses": gate_statuses,
            "runtime_receipts": {
                agent: skill_assignment_receipt(agent, path=registry_path)
                for agent in sorted(agents)
            },
        },
        "interpretation": {
            "numeric_quality_claim": False,
            "statement": (
                "本对照只证明 Skills 分配与证据门已接线；若要证明回答或预测质量提升，"
                "还需在同一输入、同一模型、同一预算下进行真实模型 A/B。"
            ),
        },
    }


__all__ = ["build_ab_report"]
