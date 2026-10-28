"""Resource-path regressions for the solar literature feed catalog.

The packaged desktop runtime keeps ``src/`` under ``<runtime>/project`` while the
``jw`` package lives at ``<runtime>/jw``; anchoring the catalog at
``parents[2]/jw`` therefore resolves to ``<runtime>/project/jw``, which does not
exist. See ``test_packaged_layout_finds_wiki_next_to_runtime_root``.
"""

from __future__ import annotations

from pathlib import Path

from knowledge_base import literature


def test_source_checkout_resolves_catalog() -> None:
    path = literature.default_literature_feeds_path()

    assert path.name == "literature_feeds.json"
    assert path.is_file(), path


def test_env_override_wins(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "feeds.json"
    target.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("JW_LITERATURE_FEEDS_PATH", str(target))

    assert literature.default_literature_feeds_path() == target


def test_packaged_layout_finds_wiki_next_to_runtime_root(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_root = tmp_path / "runtime"
    module_file = runtime_root / "project" / "src" / "knowledge_base" / "literature.py"
    module_file.parent.mkdir(parents=True)
    module_file.write_text("", encoding="utf-8")
    catalog = (
        runtime_root
        / "jw"
        / "subagents"
        / "solar"
        / "skills"
        / "solar-cycle"
        / "references"
        / "llm_wiki"
        / "_meta"
        / "literature_feeds.json"
    )
    catalog.parent.mkdir(parents=True)
    catalog.write_text("{}\n", encoding="utf-8")

    monkeypatch.delenv("JW_LITERATURE_FEEDS_PATH", raising=False)
    monkeypatch.setattr(literature, "__file__", str(module_file))

    assert literature.default_literature_feeds_path() == catalog


def test_missing_catalog_falls_back_to_source_layout(
    tmp_path: Path, monkeypatch
) -> None:
    module_file = tmp_path / "src" / "knowledge_base" / "literature.py"
    module_file.parent.mkdir(parents=True)
    module_file.write_text("", encoding="utf-8")

    monkeypatch.delenv("JW_LITERATURE_FEEDS_PATH", raising=False)
    monkeypatch.setattr(literature, "__file__", str(module_file))

    resolved = literature.default_literature_feeds_path()
    expected_prefix = tmp_path / "src" / "knowledge_base" / "jw"
    assert resolved.is_relative_to(expected_prefix)
