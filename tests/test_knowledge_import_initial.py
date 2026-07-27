from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

from knowledge_base.export import parse_frontmatter
from knowledge_base.store import KnowledgeStore

ROOT = Path(__file__).resolve().parents[1]
IMPORT_SCRIPT = ROOT / "research" / "knowledge_base" / "import_initial.py"
WIKI_DIR = (
    ROOT
    / "jw"
    / "subagents"
    / "solar"
    / "skills"
    / "solar-cycle"
    / "references"
    / "llm_wiki"
)


def _load_import_module():
    spec = importlib.util.spec_from_file_location(
        "testable_knowledge_import_initial",
        IMPORT_SCRIPT,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stats() -> dict[str, object]:
    return {
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "by_type_status": {},
    }


def test_wiki_import_honors_frontmatter_and_syncs_changed_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_import_module()
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    entry_path = wiki / "hypothesis_template_demo.md"
    entry_path.write_text(
        """---
id: kb_hypothesis_template_demo_001
type: hypothesis_template
title: Demo Template
source_type: literature
source_ref: doi:10.0000/demo
confidence: medium
status: canonical
valid_range: reviewed solar-cycle observations
related_ids: []
---

## Hypothesis statement

An explicitly bounded template.
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "LLM_WIKI_DIR", wiki)
    store = KnowledgeStore(
        db_path=tmp_path / "knowledge.db",
        export_dir=tmp_path / "export",
    )
    try:
        first = _stats()
        module.import_llm_wiki(store, first)
        imported = store.get_entry("kb_hypothesis_template_demo_001")

        assert first["inserted"] == 1
        assert imported is not None
        assert imported["confidence"] == "medium"
        assert imported["source_type"] == "literature"
        assert imported["version"] == 1

        entry_path.write_text(
            entry_path.read_text(encoding="utf-8").replace(
                "confidence: medium",
                "confidence: low",
            ),
            encoding="utf-8",
        )
        second = _stats()
        module.import_llm_wiki(store, second)
        updated = store.get_entry("kb_hypothesis_template_demo_001")

        assert second["updated"] == 1
        assert updated is not None
        assert updated["confidence"] == "low"
        assert updated["version"] == 2
        assert store.list_versions(updated["id"])[-1]["reason"] == "seed source sync"

        entry_path.write_text(
            entry_path.read_text(encoding="utf-8").replace(
                "title: Demo Template",
                "title: Updated Demo Template",
            ),
            encoding="utf-8",
        )
        renamed_stats = _stats()
        module.import_llm_wiki(store, renamed_stats)
        renamed = store.get_entry("kb_hypothesis_template_demo_001")

        assert renamed_stats["updated"] == 1
        assert renamed is not None
        assert renamed["title"] == "Updated Demo Template"
        assert renamed["version"] == 3

        third = _stats()
        module.import_llm_wiki(store, third)
        assert third["skipped"] == 1
        assert third["updated"] == 0
    finally:
        store.close()


def test_seed_catalog_and_task_bundles_reference_real_matching_entries() -> None:
    catalog = yaml.safe_load(
        (WIKI_DIR / "_meta" / "entry_catalog.yaml").read_text(encoding="utf-8")
    )
    manifest = yaml.safe_load(
        (WIKI_DIR / "_meta" / "manifest.yaml").read_text(encoding="utf-8")
    )
    seeded_paths: dict[str, dict] = {}

    for entry in catalog["entries"]:
        if entry["state"] != "seeded":
            continue
        assert entry.get("path"), entry["id"]
        path = WIKI_DIR / entry["path"]
        assert path.is_file(), path
        metadata, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        assert metadata["id"] == entry["id"]
        assert metadata["type"] == entry["type"]
        assert metadata["status"] == "canonical"
        assert body.strip()
        seeded_paths[entry["path"]] = entry

    for name, bundle in manifest["task_bundles"].items():
        for seed_path in bundle["seed_entries"]:
            assert seed_path in seeded_paths, f"{name}: {seed_path}"


def test_built_in_wiki_imports_hypothesis_leverage_entries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_import_module()
    monkeypatch.setattr(module, "LLM_WIKI_DIR", WIKI_DIR)
    store = KnowledgeStore(
        db_path=tmp_path / "knowledge.db",
        export_dir=tmp_path / "export",
    )
    expected = {
        "kb_concept_f107_flux_001",
        "kb_concept_proxy_relationship_drift_001",
        "kb_concept_polar_field_observable_001",
        "kb_mechanism_hemispheric_coupling_001",
        "kb_concept_flare_cycle_relation_001",
        "kb_experiment_paradigm_feature_ablation_001",
        "kb_experiment_paradigm_indicator_drift_001",
    }
    try:
        stats = _stats()
        module.import_llm_wiki(store, stats)
        for entry_id in expected:
            entry = store.get_entry(entry_id)
            assert entry is not None
            assert entry["status"] == "canonical"
            assert entry["content"]
        assert stats["inserted"] >= len(expected)
    finally:
        store.close()


def test_historical_run_import_is_idempotent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_import_module()
    experiment_runs = tmp_path / "experiment"
    hypothesis_runs = tmp_path / "hypothesis"
    run_dir = experiment_runs / "question-demo-001"
    run_dir.mkdir(parents=True)
    hypothesis_runs.mkdir()
    (run_dir / "report.md").write_text(
        "# Demo report\n\n## Result\n\nA bounded historical result.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "EXPERIMENT_RUNS", experiment_runs)
    monkeypatch.setattr(module, "HYPOTHESIS_RUNS", hypothesis_runs)
    store = KnowledgeStore(
        db_path=tmp_path / "knowledge.db",
        export_dir=tmp_path / "export",
    )
    try:
        first = _stats()
        module.import_runs(store, first)
        second = _stats()
        module.import_runs(store, second)

        assert first["inserted"] == 1
        assert second["skipped"] == 1
        assert second["inserted"] == 0
    finally:
        store.close()
