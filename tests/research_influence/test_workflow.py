"""The run and its bundle: the numbers must survive a trip through files."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jw.research_influence.cohort import build_snapshot, synthetic_cohort
from jw.research_influence.contracts import file_digest
from jw.research_influence.workflow import (
    RunOptions,
    load_run,
    paper_from_upload,
    run_influence,
    stages_of,
)


@pytest.fixture(scope="module")
def cohort():
    return synthetic_cohort(count=140, seed=7)


@pytest.fixture(scope="module")
def run(tmp_path_factory, cohort):
    return run_influence(
        workspace=tmp_path_factory.mktemp("infl"),
        snapshot=cohort,
        options=RunOptions(importance_repeats=2, importance_seeds=(0, 1), bootstrap=40),
    )


def test_run_writes_every_advertised_file(run):
    """The bundle is the deliverable, so every role must exist on disk."""
    root = Path(run.package_dir)
    expected = {
        "papers.csv",
        "predictions.csv",
        "factors.csv",
        "comparators.csv",
        "importance.csv",
        "bias_audit.csv",
        "bias_audit.md",
        "model_card.json",
        "metrics.json",
        "provenance.jsonl",
        "report.md",
        "README.md",
        "manifest.json",
    }
    present = {path.name for path in root.iterdir()}
    assert expected <= present


def test_manifest_digests_match_the_files(run):
    """A digest that does not match its file makes the manifest worthless."""
    root = Path(run.package_dir)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for artifact in manifest["artifacts"]:
        actual = file_digest(root / artifact["path"])
        assert artifact["sha256"] == actual, artifact["path"]
    assert manifest["counts"]["papers"] == len(run.papers)
    assert manifest["cohort_fingerprint"] == run.snapshot.fingerprint()


def test_report_answers_the_competition_questions(run):
    """Every required element of the brief must be readable in the report."""
    text = (Path(run.package_dir) / "report.md").read_text(encoding="utf-8")
    for section in (
        "## 1. 运行概要",
        "## 2. 数据来源与可得性",
        "## 3. 预测结果",
        "## 4. 解释",
        "## 5. 偏差审计",
        "## 6. 可复现性",
        "## 7. 局限",
    ):
        assert section in text
    # the interval, not just a score
    assert "80% 区间" in text
    # the two honest exclusions
    assert "性别" in text and "自引" in text
    # provenance honesty for a synthetic cohort
    assert "没有任何字段级 provenance" in text
    # a reader must be told the calibration, not just the score
    assert "区间校准" in text


def test_metrics_record_determinism_and_the_window(run):
    """Determinism is a claim with a number behind it."""
    assert run.metrics["determinism"] == 1.0
    assert run.metrics["cohort_fingerprint"] == run.snapshot.fingerprint()
    assert run.metrics["labelled"] == (run.metrics["records"])
    assert run.metrics["censored_paper_ids"]
    assert run.metrics["decile_calibration"]


def test_same_cohort_and_seed_reproduce_the_same_numbers(tmp_path, cohort):
    """Two runs over one frozen snapshot must agree, or the report is anecdote."""
    options = RunOptions(importance_repeats=1, importance_seeds=(0,), bootstrap=30)
    first = run_influence(workspace=tmp_path / "a", snapshot=cohort, options=options)
    second = run_influence(workspace=tmp_path / "b", snapshot=cohort, options=options)
    assert first.metrics["fingerprint"] == second.metrics["fingerprint"]
    assert first.model.card.fingerprint() == second.model.card.fingerprint()
    assert first.metrics["spearman"] == second.metrics["spearman"]
    assert first.metrics["interval_coverage"] == second.metrics["interval_coverage"]


def test_run_record_is_readable_without_unpickling_a_model(run):
    """Runs are inspected later, so the record has to be plain JSON."""
    record = load_run(run.directory)
    assert record["schema"] == "jw.research-influence.run/v1"
    assert record["run_id"] == run.run_id
    assert "model" in record["stages"]
    assert stages_of(run.directory)["report"]["state"] == "complete"


def test_subject_selection_is_explicit(tmp_path, cohort):
    """The explained paper is an argument, so the caller can name it."""
    target = sorted(paper.paper_id for paper in cohort.papers)[3]
    result = run_influence(
        workspace=tmp_path,
        snapshot=cohort,
        options=RunOptions(
            subject_ids=(target,),
            importance_repeats=1,
            importance_seeds=(0,),
            bootstrap=20,
        ),
    )
    assert [item.paper_id for item in result.explanations] == [target]
    rows = (Path(result.package_dir) / "predictions.csv").read_text(encoding="utf-8")
    assert "is_subject" in rows.splitlines()[0]


def test_missing_cohort_is_refused(tmp_path):
    """A prediction needs a reference population; silence would be worse."""
    with pytest.raises(ValueError, match="snapshot"):
        run_influence(workspace=tmp_path)


def test_small_cohort_reports_no_model_instead_of_a_bad_one(tmp_path):
    """Below the training floor the honest answer is "no model"."""
    tiny = synthetic_cohort(count=12, seed=3)
    result = run_influence(workspace=tmp_path, snapshot=tiny)
    assert result.model is None
    assert result.predictions is None
    assert any(note.startswith("model_unavailable:") for note in result.notes)
    root = Path(result.package_dir)
    assert not (root / "predictions.csv").exists()
    text = (root / "report.md").read_text(encoding="utf-8")
    assert "不给出任何预测数字" in text
    assert "未训练模型" in text


def test_uploaded_pdf_becomes_a_parsed_paper(tmp_path):
    """The operator-supplied PDF is the top rung and must still be parsed."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path = tmp_path / "supplied.pdf"
    figure = plt.figure(figsize=(8.5, 11))
    figure.text(0.1, 0.95, "1. Introduction", fontsize=12)
    figure.text(0.1, 0.90, "We stack 40 epochs of SN 2011fe.", fontsize=10)
    figure.text(0.1, 0.60, "Data availability", fontsize=12)
    figure.text(0.1, 0.55, "Data at https://github.com/example/lc", fontsize=10)
    figure.savefig(path)
    plt.close(figure)

    paper = paper_from_upload(path, directory=tmp_path / "store")
    assert paper.access_route.value == "user_supplied"
    assert paper.has_full_text
    assert paper.sections
    assert any(record.kind.value == "code" for record in paper.open_data)
    assert paper.provenance_for("title") == ()


def test_upload_participates_in_the_run(tmp_path, cohort):
    """An uploaded paper is explained against the frozen cohort."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path = tmp_path / "supplied.pdf"
    figure = plt.figure(figsize=(8.5, 11))
    figure.text(0.1, 0.95, "2. Methods", fontsize=12)
    figure.text(0.1, 0.90, "We model the light curve.", fontsize=10)
    figure.savefig(path)
    plt.close(figure)

    result = run_influence(
        workspace=tmp_path,
        snapshot=cohort,
        uploads=(path,),
        options=RunOptions(importance_repeats=1, importance_seeds=(0,), bootstrap=20),
    )
    assert len(result.uploads) == 1
    assert result.explanations[0].paper_id == result.uploads[0].paper_id
    papers_csv = (Path(result.package_dir) / "papers.csv").read_text(encoding="utf-8")
    assert "user_supplied" in papers_csv
    text = (Path(result.package_dir) / "report.md").read_text(encoding="utf-8")
    # an unlabelled upload has no window count, and the report must not invent one
    assert result.uploads[0].cited_by_count is None


def test_bias_audit_can_be_limited_by_the_caller(tmp_path, cohort):
    """Audit subsetting is honoured, so a reader can rerun one audit."""
    result = run_influence(
        workspace=tmp_path,
        snapshot=cohort,
        options=RunOptions(
            audits=("venue_prestige", "author_demographics"),
            importance_repeats=1,
            importance_seeds=(0,),
            bootstrap=20,
        ),
    )
    names = [finding.name for finding in result.bias.findings]
    assert names == ["venue_prestige", "author_demographics"]
    demographics = result.bias.findings[1]
    assert demographics.status == "cannot_assess"
    assert "Name-based" in demographics.conclusion
    assert "Deliberately not attempted" in demographics.method


def test_snapshot_round_trips_through_the_run(tmp_path, cohort):
    """The snapshot stored in the bundle can be re-run later, byte for byte."""
    cohort.write(tmp_path / "snapshot.json")
    first = run_influence(
        workspace=tmp_path / "one",
        snapshot_path=tmp_path / "snapshot.json",
        options=RunOptions(importance_repeats=1, importance_seeds=(0,), bootstrap=20),
    )
    assert first.snapshot.fingerprint() == cohort.fingerprint()
    assert first.metrics["determinism"] == 1.0


def test_written_snapshot_is_stable(tmp_path):
    """Re-freezing the same papers must not change the fingerprint."""
    snapshot = synthetic_cohort(count=40, seed=5)
    first = build_snapshot(
        snapshot.papers,
        cohort_id=snapshot.cohort_id,
        query=snapshot.query,
        source=snapshot.source,
        reference_year=snapshot.reference_year,
    )
    second = build_snapshot(
        list(reversed(snapshot.papers)),
        cohort_id=snapshot.cohort_id,
        query=snapshot.query,
        source=snapshot.source,
        reference_year=snapshot.reference_year,
    )
    assert first.fingerprint() == second.fingerprint()
