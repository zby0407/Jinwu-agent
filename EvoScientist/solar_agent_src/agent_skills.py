from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SKILLS_DIR = ROOT / ".agents" / "skills"
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class SkillError(ValueError):
    """Raised when a project skill is malformed or unavailable."""


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    description: str
    path: Path
    instructions: str

    def catalog_entry(self) -> dict[str, str]:
        return {"name": self.name, "description": self.description}

    def tool_result(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "tool": "load_skill",
            "summary": {
                "name": self.name,
                "description": self.description,
                "instructions": self.instructions,
            },
            "artifacts": [],
            "warnings": [],
            "error": None,
        }


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def parse_skill_file(path: Path) -> SkillDefinition:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillError(f"Skill frontmatter is missing: {path}")
    try:
        closing = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration as exc:
        raise SkillError(f"Skill frontmatter is not closed: {path}") from exc

    metadata: dict[str, str] = {}
    for line in lines[1:closing]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise SkillError(f"Invalid skill frontmatter line in {path}: {line!r}")
        key, value = line.split(":", 1)
        metadata[key.strip()] = _unquote(value)

    if set(metadata) != {"name", "description"}:
        raise SkillError(f"Skill frontmatter must contain only name and description: {path}")
    name = metadata["name"].strip()
    description = metadata["description"].strip()
    if not SKILL_NAME_PATTERN.fullmatch(name):
        raise SkillError(f"Invalid skill name {name!r}: {path}")
    if path.parent.name != name:
        raise SkillError(f"Skill name {name!r} must match directory {path.parent.name!r}")
    if not description:
        raise SkillError(f"Skill description must not be empty: {path}")
    instructions = "\n".join(lines[closing + 1 :]).strip()
    if not instructions:
        raise SkillError(f"Skill instructions must not be empty: {path}")
    return SkillDefinition(name=name, description=description, path=path, instructions=instructions)


class SkillRegistry:
    """Discover and safely load only registered project skills."""

    def __init__(self, skills_dir: Path = DEFAULT_SKILLS_DIR) -> None:
        self.skills_dir = skills_dir.resolve()
        self._skills = self._discover()

    def _discover(self) -> dict[str, SkillDefinition]:
        if not self.skills_dir.exists():
            return {}
        skills: dict[str, SkillDefinition] = {}
        for path in sorted(self.skills_dir.glob("*/SKILL.md")):
            definition = parse_skill_file(path)
            if definition.name in skills:
                raise SkillError(f"Duplicate skill name: {definition.name}")
            skills[definition.name] = definition
        return skills

    def catalog(self) -> list[dict[str, str]]:
        return [skill.catalog_entry() for skill in self._skills.values()]

    def names(self) -> list[str]:
        return list(self._skills)

    def load(self, name: str) -> SkillDefinition:
        if name not in self._skills:
            raise SkillError(f"Unknown skill {name!r}. Available skills: {self.names()}")
        return self._skills[name]

    def explicit_skill(self, question: str) -> SkillDefinition | None:
        stripped = question.strip()
        if not stripped.startswith(("/", "$")):
            return None
        token = stripped[1:].split(maxsplit=1)[0]
        return self._skills.get(token)

    @staticmethod
    def tool_schema() -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "load_skill",
                "description": "Load one registered project skill before following its domain workflow.",
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                    "additionalProperties": False,
                },
            },
        }
