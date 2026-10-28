"""Idempotent import of the versioned built-in LLM Wiki seed.

The built-in Wiki under ``jw/subagents/.../references/llm_wiki`` is the
reviewed canonical seed. Runtime agents search the SQLite knowledge service,
not those Markdown files, so a missing seed import silently reduces retrieval
coverage. This module only inserts missing seed entries and never mutates an
existing row: lifecycle state and later evidence must remain owned by the
knowledge service.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any

from .contracts import ENTRY_TYPES, validate_entry
from .export import export_entry, parse_frontmatter
from .store import KnowledgeStore, utc_now

_WIKI_RELATIVE = (
    Path("jw")
    / "subagents"
    / "solar"
    / "skills"
    / "solar-cycle"
    / "references"
    / "llm_wiki"
)

_REQUIRED_CONTENT = {
    "concept": "definition",
    "mechanism": "claim",
    "data_source": "collection_method",
    "experiment_paradigm": "design",
    "hypothesis_template": "structure",
    "finding": "statement",
    "counterexample": "statement",
    "lesson": "statement",
}

_PREFIX_TYPES = (
    ("concept_", "concept"),
    ("mechanism_", "mechanism"),
    ("data_source_", "data_source"),
    ("experiment_paradigm_", "experiment_paradigm"),
    ("hypothesis_template_", "hypothesis_template"),
)


def _candidate_roots() -> list[Path]:
    roots: list[Path] = []
    override = os.getenv("JW_BUILTIN_WIKI_DIR", "").strip()
    if override:
        roots.append(Path(override).expanduser())
    roots.append(Path(__file__).resolve().parents[2] / _WIKI_RELATIVE)
    roots.append(Path.cwd() / _WIKI_RELATIVE)
    try:
        spec = importlib.util.find_spec("jw")
    except (ImportError, ValueError):
        spec = None
    if spec is not None and spec.submodule_search_locations:
        for location in spec.submodule_search_locations:
            roots.append(Path(location) / _WIKI_RELATIVE.relative_to("jw"))
    return roots


def builtin_wiki_dir() -> Path | None:
    """Return the first readable built-in Wiki directory, if available."""

    seen: set[Path] = set()
    for root in _candidate_roots():
        resolved = root.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_dir():
            return resolved
    return None


def _seed_entries(wiki_dir: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    now = utc_now()
    for path in sorted(wiki_dir.glob("*.md")):
        matched = next(
            (
                (prefix, entry_type)
                for prefix, entry_type in _PREFIX_TYPES
                if path.name.startswith(prefix)
            ),
            None,
        )
        if matched is None:
            continue
        _, entry_type = matched
        required_field = _REQUIRED_CONTENT[entry_type]
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        entry_id = str(meta.get("id") or "").strip()
        if not entry_id:
            # An ungoverned seed without a stable id is a repository error;
            # skip it rather than inventing an id.
            continue
        entry = {
            "id": entry_id,
            "type": entry_type,
            "title": str(meta.get("title") or path.stem),
            "content": {required_field: body.strip()},
            "source_type": str(meta.get("source_type") or "textbook"),
            "source_ref": str(meta.get("source_ref") or f"llm_wiki/{path.name}"),
            "confidence": str(meta.get("confidence") or "medium"),
            "status": str(meta.get("status") or "canonical"),
            "valid_range": str(meta.get("valid_range") or ""),
            "related_ids": meta.get("related_ids")
            if isinstance(meta.get("related_ids"), list)
            else [],
            "provenance": {
                "imported_by": "builtin_seed",
                "imported_at": now,
                "seed_path": f"llm_wiki/{path.name}",
            },
            "version": 1,
            "created_at": now,
            "updated_at": now,
            "created_by": "builtin_seed",
        }
        if entry["type"] not in ENTRY_TYPES:
            continue
        entries.append(validate_entry(entry))
    return entries


def ensure_builtin_wiki(
    store: KnowledgeStore, wiki_dir: str | Path | None = None
) -> dict[str, Any]:
    """Insert missing built-in seed entries; never overwrite existing rows."""

    root = Path(wiki_dir) if wiki_dir is not None else builtin_wiki_dir()
    if root is None or not root.is_dir():
        return {
            "status": "unavailable",
            "wiki_dir": None,
            "inserted": 0,
            "skipped": 0,
            "errors": [],
        }
    stats: dict[str, Any] = {
        "status": "ok",
        "wiki_dir": str(root),
        "inserted": 0,
        "skipped": 0,
        "errors": [],
    }
    for entry in _seed_entries(root):
        if store.get_entry(entry["id"]) is not None:
            stats["skipped"] += 1
            continue
        existing = store.find_entry_by_source(entry["source_ref"], entry["title"])
        if existing is not None:
            stats["errors"].append(
                {
                    "id": entry["id"],
                    "error": f"seed identity collision with {existing['id']}",
                }
            )
            continue
        try:
            store.create_entry(entry, changed_by="builtin_seed", reason="builtin seed")
            export_entry(entry, store.export_dir)
        except Exception as exc:
            stats["errors"].append({"id": entry["id"], "error": str(exc)})
            continue
        stats["inserted"] += 1
    if stats["errors"]:
        stats["status"] = "partial"
    return stats


__all__ = ["builtin_wiki_dir", "ensure_builtin_wiki"]
