"""Literature outputs preserve source identity and task/file boundaries."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from jw.tools.literature_artifacts import (
    literature_report_save,
    literature_source_read,
    literature_sources_list,
)
from jw.workspaces import create_legacy_binding, ensure_thread_workspace


@pytest.fixture
def literature_task(tmp_path, monkeypatch):
    base = tmp_path / "workspace"
    (base / "data").mkdir(parents=True)
    (base / "data" / "registered.csv").write_bytes(b"cycle,peak\r\n1,100\r\n")
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(tmp_path / "bindings"))
    monkeypatch.setattr("jw.paths.WORKSPACE_ROOT", base)
    binding = ensure_thread_workspace("literature-one", base)
    root = Path(binding.workspace)
    paper = root / "inputs" / "evidence" / "paper.md"
    paper.parent.mkdir()
    paper.write_text(
        "# 原始证据\nObserved evidence, not a causal proof.\n", encoding="utf-8"
    )
    manifest_path = root / "input_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["inputs"] = [
        {
            "path": "inputs/evidence/paper.md",
            "bytes": paper.stat().st_size,
            "sha256": hashlib.sha256(paper.read_bytes()).hexdigest(),
            "role": "user_input",
        }
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return root, {"configurable": {"thread_id": "literature-one"}}, base


def _call(tool, args, config):
    return json.loads(tool.invoke(args, config=config))


def test_literature_reads_only_bound_task_and_project_sources(literature_task):
    root, config, _ = literature_task
    (root / "inputs" / "unbound.txt").write_text("not registered", encoding="utf-8")
    listing = _call(literature_sources_list, {}, config)
    assert listing["status"] == "ok"
    assert {item["path"] for item in listing["sources"]} == {
        "inputs/evidence/paper.md",
        "/project/data/registered.csv",
    }
    source = _call(
        literature_source_read,
        {"source_ref": "/project/data/registered.csv"},
        config,
    )
    assert source["content"] == "cycle,peak\r\n1,100\r\n"
    assert source["review_status"] == "not_reviewed"
    assert (
        _call(literature_source_read, {"source_ref": "inputs/unbound.txt"}, config)[
            "status"
        ]
        == "error"
    )


def test_literature_source_pagination_and_changed_input(literature_task):
    root, config, _ = literature_task
    source_ref = "inputs/evidence/paper.md"
    first = _call(
        literature_source_read, {"source_ref": source_ref, "limit": 7}, config
    )
    second = _call(
        literature_source_read,
        {"source_ref": source_ref, "offset": first["next_offset"]},
        config,
    )
    source = root / source_ref
    assert first["content"] + second["content"] == source.read_bytes().decode("utf-8")
    assert second["next_offset"] is None
    assert first["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    source.write_text("altered evidence", encoding="utf-8")
    assert (
        _call(literature_source_read, {"source_ref": source_ref}, config)["status"]
        == "error"
    )


def test_literature_inputs_do_not_cross_threads(literature_task):
    root, _, base = literature_task
    ensure_thread_workspace("literature-two", base)
    other_config = {"configurable": {"thread_id": "literature-two"}}
    for source_ref in (
        "inputs/evidence/paper.md",
        str(root / "inputs/evidence/paper.md"),
    ):
        assert (
            _call(literature_source_read, {"source_ref": source_ref}, other_config)[
                "status"
            ]
            == "error"
        )


def test_literature_save_verifies_real_bytes_and_requires_explicit_revision(
    literature_task,
):
    root, config, _ = literature_task
    markdown = "# 文献报告\n\nEvidence remains uncertain.\n"
    saved = _call(
        literature_report_save,
        {"path": "/outputs/report.md", "markdown": markdown},
        config,
    )
    target = root / "outputs" / "report.md"
    assert saved["status"] == "saved"
    assert target.read_bytes() == markdown.encode("utf-8")
    assert saved["bytes"] == target.stat().st_size
    assert saved["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    assert saved["artifact_ref"] == "outputs/report.md"
    assert saved["review_status"] == "not_reviewed"
    assert not (root / "research_review").exists()
    rejected = _call(
        literature_report_save,
        {"path": "outputs/report.md", "markdown": "changed"},
        config,
    )
    assert rejected["status"] == "error"
    assert target.read_bytes() == markdown.encode("utf-8")
    revised = _call(
        literature_report_save,
        {"path": "outputs/report.md", "markdown": "revised", "overwrite": True},
        config,
    )
    assert revised["status"] == "saved"
    assert target.read_text(encoding="utf-8") == "revised"


@pytest.mark.parametrize(
    "path",
    [
        "../outside.md",
        "outputs/../../outside.md",
        "/project/report.md",
        "inputs/report.md",
        "receipts/review.md",
        "outputs/report.py",
        "outputs/nested/report.md",
        "outputs/report.md:stream",
        "outputs\\report.md",
        "outputs/.hidden.md",
        "//outputs/report.md",
    ],
)
def test_literature_report_rejects_non_output_paths(literature_task, path):
    root, config, _ = literature_task
    assert (
        _call(literature_report_save, {"path": path, "markdown": "report"}, config)[
            "status"
        ]
        == "error"
    )
    assert list((root / "outputs").iterdir()) == []


def test_literature_save_refuses_redirected_output(literature_task, tmp_path):
    root, config, _ = literature_task
    target = tmp_path / "external.md"
    target.write_text("preserve", encoding="utf-8")
    link = root / "outputs" / "report.md"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("platform does not permit symlink creation")
    assert (
        _call(
            literature_report_save,
            {"path": "outputs/report.md", "markdown": "report", "overwrite": True},
            config,
        )["status"]
        == "error"
    )
    assert target.read_text(encoding="utf-8") == "preserve"


def test_literature_file_tools_require_initialized_thread():
    assert _call(literature_sources_list, {}, {})["status"] == "error"
    assert (
        _call(
            literature_report_save,
            {"path": "outputs/report.md", "markdown": "report"},
            {},
        )["status"]
        == "error"
    )


def test_literature_file_tools_reject_legacy_shared_workspace(literature_task):
    _, _, base = literature_task
    create_legacy_binding("legacy-literature", base)
    config = {"configurable": {"thread_id": "legacy-literature"}}
    assert _call(literature_sources_list, {}, config)["status"] == "error"
    result = _call(
        literature_report_save,
        {"path": "outputs/report.md", "markdown": "must not be shared"},
        config,
    )
    assert result["status"] == "error"
    assert "start a new thread" in result["error"]
    assert not (base / "outputs" / "report.md").exists()
