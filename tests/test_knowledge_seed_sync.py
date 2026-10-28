"""Built-in Wiki seed must be imported idempotently without lifecycle drift."""

from __future__ import annotations

from pathlib import Path

import yaml

from knowledge_base import service
from knowledge_base.seed import builtin_wiki_dir, ensure_builtin_wiki
from knowledge_base.store import KnowledgeStore


def _catalog_seeded_ids() -> list[str]:
    wiki_dir = builtin_wiki_dir()
    assert wiki_dir is not None
    catalog = yaml.safe_load(
        (wiki_dir / "_meta" / "entry_catalog.yaml").read_text(encoding="utf-8")
    )
    return [
        str(entry["id"])
        for entry in catalog["entries"]
        if entry.get("state") == "seeded"
    ]


def test_builtin_seed_import_is_idempotent_and_complete(tmp_path: Path) -> None:
    store = KnowledgeStore(
        db_path=tmp_path / "knowledge.db",
        export_dir=tmp_path / "knowledge_base",
    )
    try:
        first = ensure_builtin_wiki(store)
        assert first["status"] == "ok", first
        assert first["inserted"] >= 27
        assert first["errors"] == []

        seeded_ids = _catalog_seeded_ids()
        assert len(seeded_ids) == 27
        for entry_id in seeded_ids:
            entry = store.get_entry(entry_id)
            assert entry is not None, entry_id
            assert entry["status"] == "canonical", entry_id
            assert entry["content"], entry_id
            assert entry["provenance"]["imported_by"] == "builtin_seed"

        second = ensure_builtin_wiki(store)
        assert second["inserted"] == 0
        assert second["skipped"] == first["inserted"]
        assert second["errors"] == []
    finally:
        store.close()


def test_builtin_seed_does_not_resurrect_deprecated_entries(
    tmp_path: Path,
) -> None:
    store = KnowledgeStore(
        db_path=tmp_path / "knowledge.db",
        export_dir=tmp_path / "knowledge_base",
    )
    try:
        ensure_builtin_wiki(store)
        entry_id = "kb_concept_sunspot_cycle_001"
        service.deprecate(store, entry_id, reason="测试生命周期不被 seed 覆盖")

        stats = ensure_builtin_wiki(store)

        assert stats["inserted"] == 0
        assert store.get_entry(entry_id)["status"] == "deprecated"
    finally:
        store.close()
