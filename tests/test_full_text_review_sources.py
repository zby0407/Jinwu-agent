"""Current-task retrieved body text enters native Data review, with no model calls."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jw import literature_full_text as full
from jw import paths
from jw.research_review import ResearchReviewStore
from jw.tools import knowledge_base as literature_tools
from jw.tools import research_review as review_tools
from jw.workspaces import ensure_thread_workspace

SOURCE = {
    "source_id": "synthetic:10.1234/current-review-source",
    "title": "Synthetic task scoped article source",
    "doi": "https://doi.org/10.1234/current-review-source",
    "url": "https://example.org/synthetic-article",
    "is_retracted": False,
}
THREAD = "synthetic-current-review"
BINDING = "synthetic-current-binding"


def _article():
    return "\n".join(
        [
            SOURCE["title"],
            "10.1234/current-review-source",
            "Introduction",
            "Synthetic technical fixture for source routing, with no actual observations. "
            * 15,
            "Methods",
            "Artificial source text exercises the review read contract, not scientific validation. "
            * 15,
            "Results",
            "This synthetic article supplies no scientific conclusion or measured result. "
            * 15,
            "References",
            "Synthetic reference placeholder for extraction tests only.",
        ]
    )


@pytest.fixture
def retrieved(tmp_path, monkeypatch):
    root = tmp_path / "current-task"
    root.mkdir()
    (root / "task.json").write_text(
        json.dumps({"thread_id": THREAD, "run_id": "synthetic-current-run"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        full,
        "_request_public_article",
        lambda _url: {
            "raw": _article().encode("utf-8"),
            "content_type": "text/plain",
            "final_url": SOURCE["url"],
            "http_status": 200,
            "redirect_chain": [],
        },
    )
    result = full.fetch_article_full_text(
        SOURCE,
        article_url=SOURCE["url"],
        directory=root / "literature/full_text",
        binding_id=BINDING,
        task_workspace=root,
        task_thread_id=THREAD,
    )
    assert result["status"] == "ok", result
    registration = full.register_successful_full_text(
        root, Path(result["receipt_path"])
    )
    return root, result, registration


def test_native_data_context_declares_and_reads_verified_body_text(
    retrieved, monkeypatch
):
    root, result, _registration = retrieved
    store = ResearchReviewStore(root, THREAD)
    artifact = store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content="Synthetic source routing fixture. Retrieved article text is available; no scientific assessment or result is claimed.",
        phase="bounded_data",
    )
    text_ref = Path(result["path"]).relative_to(root).as_posix()
    receipt_ref = Path(result["receipt_path"]).relative_to(root).as_posix()
    raw_ref = Path(result["raw_path"]).relative_to(root).as_posix()
    assert text_ref in artifact["evidence_refs"]
    assert receipt_ref in artifact["evidence_refs"]
    assert raw_ref not in artifact["evidence_refs"]
    assert {item["source_ref"] for item in artifact["payload"]["source_manifest"]} == {
        receipt_ref,
        text_ref,
    }
    monkeypatch.setattr(review_tools, "store_from_config", lambda _config: store)
    opened = json.loads(
        review_tools.evidence_review_open_context.invoke({"review_mode": "data"})
    )
    assert opened["ok"] is True
    assert text_ref in opened["result"]["artifacts"][0]["evidence_refs"]
    read = json.loads(
        review_tools.evidence_review_read_source.invoke(
            {"review_mode": "data", "source_ref": text_ref}
        )
    )
    assert read["ok"] is True
    assert read["result"]["content"] == _article()
    assert read["result"]["sha256"] == result["text_sha256"]
    assert read["result"]["hash_matches_checkpoint"] is True
    assert read["result"]["truncated"] is False


def test_real_range_read_receipt_does_not_change_data_source_manifest(
    retrieved, monkeypatch
):
    root, result, _registration = retrieved
    store = ResearchReviewStore(root, THREAD)
    artifact = store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content="Synthetic task-local source routing; no science claimed.",
        phase="bounded_data",
    )
    before = store._source_manifest(store._canonical_stage_sources("data"))
    monkeypatch.setattr(
        literature_tools,
        "_task_literature_directory",
        lambda _config: root / "literature",
    )
    monkeypatch.setattr(
        literature_tools, "workspace_root_from_config", lambda _config: root
    )
    monkeypatch.setitem(
        literature_tools._DISTILL_BINDINGS,
        BINDING,
        {"binding_id": BINDING, "run_id": THREAD},
    )
    monkeypatch.setitem(literature_tools._ACTIVE_DISTILL_BINDINGS, THREAD, BINDING)
    read = json.loads(
        literature_tools.lit_read_full_text.invoke(
            {"receipt_path": result["receipt_path"], "max_lines": 2},
            config={"configurable": {"thread_id": THREAD}},
        )
    )
    assert read["status"] == "ok", read
    assert Path(read["reading_receipt_path"]).is_file()
    after = store._source_manifest(store._canonical_stage_sources("data"))
    assert before == after == artifact["payload"]["source_manifest"]
    assert store._source_integrity_issues([artifact]) == []
    assert not any("/reads/" in item["source_ref"] for item in after)


@pytest.mark.parametrize("damage", ["text", "raw", "receipt", "registration"])
def test_tampered_registered_article_blocks_data_source_discovery(retrieved, damage):
    root, result, registration = retrieved
    path = Path(
        {
            "text": result["path"],
            "raw": result["raw_path"],
            "receipt": result["receipt_path"],
            "registration": registration["registration_path"],
        }[damage]
    )
    path.write_bytes(b"changed synthetic source")
    with pytest.raises(PermissionError):
        ResearchReviewStore(root, THREAD)._canonical_stage_sources("data")


def test_abstract_failure_and_arbitrary_literature_files_are_not_review_sources(
    tmp_path, monkeypatch
):
    root = tmp_path / "task-with-source-gap"
    root.mkdir()
    (root / "task.json").write_text(
        json.dumps({"thread_id": THREAD, "run_id": "source-gap-run"}), encoding="utf-8"
    )
    monkeypatch.setattr(
        full,
        "_request_public_article",
        lambda _url: {
            "raw": (SOURCE["title"] + "\nAbstract only. " * 150).encode(),
            "content_type": "text/plain",
            "final_url": SOURCE["url"],
            "http_status": 200,
            "redirect_chain": [],
        },
    )
    failed = full.fetch_article_full_text(
        SOURCE,
        article_url=SOURCE["url"],
        directory=root / "literature/full_text",
        binding_id=BINDING,
        task_workspace=root,
        task_thread_id=THREAD,
    )
    assert failed["status"] == "unavailable"
    arbitrary = root / "literature/arbitrary.txt"
    arbitrary.write_text(
        "File existence is not source qualification.", encoding="utf-8"
    )
    sources = ResearchReviewStore(root, THREAD)._canonical_stage_sources("data")
    assert Path(failed["receipt_path"]) not in sources
    assert arbitrary not in sources
    with pytest.raises(PermissionError):
        full.register_successful_full_text(root, Path(failed["receipt_path"]))


def test_cross_task_registration_cannot_promote_another_tasks_article(
    retrieved, tmp_path
):
    _root, _result, registration = retrieved
    other = tmp_path / "other-task"
    other.mkdir()
    (other / "task.json").write_text(
        json.dumps({"thread_id": "other-task", "run_id": "other-run"}), encoding="utf-8"
    )
    target = other / "receipts/literature_full_text"
    target.mkdir(parents=True)
    original = Path(registration["registration_path"])
    (target / original.name).write_bytes(original.read_bytes())
    with pytest.raises(PermissionError):
        ResearchReviewStore(other, "other-task")._canonical_stage_sources("data")


def test_legacy_data_without_article_registry_keeps_existing_sources(tmp_path):
    output = tmp_path / "outputs/synthetic-data-table.json"
    output.parent.mkdir()
    output.write_text('{"fixture": "technical synthetic output"}', encoding="utf-8")
    # Existing Data discovery needs neither a task.json nor an article registry.
    assert ResearchReviewStore(
        tmp_path, "legacy-source-fixture"
    )._canonical_stage_sources("data") == [output.resolve()]


def test_native_task_can_fetch_read_and_review_without_b3_profile(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(tmp_path / "bindings"))
    monkeypatch.setattr(paths, "WORKSPACE_ROOT", tmp_path)
    binding = ensure_thread_workspace("native-article-fixture", tmp_path)
    config = {"configurable": {"thread_id": binding.thread_id}}
    root = Path(binding.workspace)
    monkeypatch.setattr(
        literature_tools,
        "_get_store",
        lambda: SimpleNamespace(resolve_lit_source=lambda _source_id: dict(SOURCE)),
    )
    monkeypatch.setattr(
        full,
        "_request_public_article",
        lambda _url: {
            "raw": _article().encode("utf-8"),
            "content_type": "text/plain",
            "final_url": SOURCE["url"],
            "http_status": 200,
            "redirect_chain": [],
        },
    )
    bound = json.loads(
        literature_tools.lit_bind_task.invoke(
            {
                "research_question": "Synthetic article source routing",
                "distill_focus": "Synthetic article source routing",
            },
            config=config,
        )
    )
    assert bound["status"] == "ok", bound
    fetched = json.loads(
        literature_tools.lit_fetch_full_text.invoke(
            {"source_id": SOURCE["source_id"]}, config=config
        )
    )
    assert fetched["status"] == "ok", fetched
    assert Path(fetched["receipt_path"]).is_relative_to(root / "literature/full_text")
    read = json.loads(
        literature_tools.lit_read_full_text.invoke(
            {"receipt_path": fetched["receipt_path"], "max_lines": 2}, config=config
        )
    )
    assert read["status"] == "ok", read
    assert read["scientific_assessment_performed"] is False
    sources = ResearchReviewStore(root, binding.thread_id)._canonical_stage_sources(
        "data"
    )
    assert Path(fetched["path"]).resolve() in sources
    assert Path(fetched["receipt_path"]).resolve() in sources
