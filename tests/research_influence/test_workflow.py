"""The run and its bundle: the numbers must survive a trip through files."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jw.research_influence.cohort import build_snapshot, synthetic_cohort
from jw.research_influence.contracts import ConfidenceBand, file_digest
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
    # The scored rows are the held-out ones; the labelled count must be larger,
    # because scoring the training rows would report the booster's memory.
    assert run.metrics["eval_subset"] == "holdout"
    assert run.metrics["records"] == run.metrics["scored_rows"]
    assert run.metrics["records"] <= run.metrics["labelled"]
    assert run.metrics["in_sample_rows"] == 0
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


def test_the_package_fingerprint_survives_a_rerun(tmp_path, cohort):
    """The on-disk bundle has to support the same claim as the numbers.

    Byte digests legitimately differ — ``report.md`` names its run and says when
    it happened.  The stable digest is the promise: same frozen snapshot, same
    seed, same content.
    """
    options = RunOptions(importance_repeats=1, importance_seeds=(0,), bootstrap=20)
    first = run_influence(workspace=tmp_path / "a", snapshot=cohort, options=options)
    second = run_influence(workspace=tmp_path / "b", snapshot=cohort, options=options)

    assert first.run_id != second.run_id
    assert first.metrics["package_fingerprint"] == second.metrics["package_fingerprint"]

    manifests = [
        json.loads((Path(run.package_dir) / "manifest.json").read_text("utf-8"))
        for run in (first, second)
    ]
    for manifest in manifests:
        assert manifest["package_fingerprint"] == first.metrics["package_fingerprint"]
        assert all(
            item["stable_sha256"].startswith("sha256:")
            for item in manifest["artifacts"]
        )
        roles = {item["role"] for item in manifest["artifacts"]}
        assert {"papers", "report", "metrics", "provenance"} <= roles

    bytes_of = [
        {item["role"]: item["sha256"] for item in manifest["artifacts"]}
        for manifest in manifests
    ]
    stable_of = [
        {item["role"]: item["stable_sha256"] for item in manifest["artifacts"]}
        for manifest in manifests
    ]
    assert bytes_of[0]["report"] != bytes_of[1]["report"]
    assert stable_of[0] == stable_of[1]


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
    # Whatever the text layer offered is attributed to the text layer and
    # carries a low confidence band: no index was consulted for this upload.
    for field in ("title", "authors", "year"):
        for entry in paper.provenance_for(field):
            assert entry.provider == "pdf_text_layer"
            assert entry.confidence in {ConfidenceBand.LOW, ConfidenceBand.UNVERIFIED}
    assert all(entry.provider != "openalex" for entry in paper.provenance_for("venue"))


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


def test_a_missing_venue_is_not_counted_as_a_journal(tmp_path):
    """An absent venue arrives as an empty record, so counting `is not None` lies."""
    from jw.research_influence.cohort import CohortSnapshot
    from jw.research_influence.contracts import AccessRoute, PaperRecord
    from jw.research_influence.workflow import RunOptions, run_influence

    papers = tuple(
        PaperRecord(
            paper_id=f"paper:{index}",
            title=f"Paper {index}",
            access_route=AccessRoute.METADATA_ONLY,
        )
        for index in range(3)
    )
    snapshot = CohortSnapshot(
        cohort_id="cohort:bare", papers=papers, query="q", reference_year=2024
    )
    result = run_influence(
        workspace=tmp_path,
        snapshot=snapshot,
        options=RunOptions(reference_year=2024, package_name="bare"),
    )
    text = (Path(result.directory) / "bare" / "report.md").read_text(encoding="utf-8")
    assert "| 有期刊信息 | 0 |" in text
    assert "| 有标题 | 3 |" in text


def test_verify_recomputes_what_the_manifest_claims(tmp_path, cohort):
    """Trusting the manifest proves nothing; re-reading the files does."""
    from jw.research_influence.package import verify_package

    result = run_influence(
        workspace=tmp_path,
        snapshot=cohort,
        options=RunOptions(importance_repeats=1, importance_seeds=(0,), bootstrap=20),
    )
    report = verify_package(result.package_dir)
    assert report["ok"] is True, report
    assert report["failed"] == []
    assert report["fingerprint_ok"] is True
    assert report["checked"] == len(report["artifacts"])
    assert (
        report["package_fingerprint_expected"] == result.metrics["package_fingerprint"]
    )


def test_verify_catches_an_edited_table(tmp_path, cohort):
    """An edited CSV is the case the manifest exists for."""
    from jw.research_influence.package import verify_package

    result = run_influence(
        workspace=tmp_path,
        snapshot=cohort,
        options=RunOptions(importance_repeats=1, importance_seeds=(0,), bootstrap=20),
    )
    path = Path(result.package_dir) / "predictions.csv"
    path.write_text(
        path.read_text(encoding="utf-8").replace(",", ",", 1) + "tampered\n",
        encoding="utf-8",
    )
    report = verify_package(result.package_dir)
    assert report["ok"] is False
    assert "predictions" in report["failed"]
    row = next(item for item in report["artifacts"] if item["role"] == "predictions")
    assert row["bytes_ok"] is False and row["stable_ok"] is False


def test_verify_distinguishes_a_rewritten_timestamp_from_a_changed_finding(
    tmp_path, cohort
):
    """Re-stamping a report is drift in bytes, not in content — say so."""
    from jw.research_influence.package import verify_package

    result = run_influence(
        workspace=tmp_path,
        snapshot=cohort,
        options=RunOptions(importance_repeats=1, importance_seeds=(0,), bootstrap=20),
    )
    path = Path(result.package_dir) / "report.md"
    text = path.read_text(encoding="utf-8")
    stamped = text.replace("2026-", "2027-", 1)
    assert stamped != text, "the report must contain an instant to rewrite"
    path.write_text(stamped, encoding="utf-8")

    report = verify_package(result.package_dir)
    row = next(item for item in report["artifacts"] if item["role"] == "report")
    assert row["bytes_ok"] is False
    assert row["stable_ok"] is True
    # The bytes no longer agree, so the package is not intact — but the finding
    # survived the re-stamp, and the verdict says which of the two broke.
    assert report["ok"] is False
    assert report["fingerprint_ok"] is True


def test_verify_catches_a_manifest_edited_to_agree_with_edited_files(tmp_path, cohort):
    """A manifest that was rewritten alongside the files must still fail."""
    from jw.research_influence.package import verify_package

    result = run_influence(
        workspace=tmp_path,
        snapshot=cohort,
        options=RunOptions(importance_repeats=1, importance_seeds=(0,), bootstrap=20),
    )
    root = Path(result.package_dir)
    target = root / "importance.csv"
    target.write_text("paper_id,feature,importance\nx,y,1.0\n", encoding="utf-8")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for artifact in manifest["artifacts"]:
        if artifact["role"] == "importance":
            artifact["sha256"] = file_digest(target)
            artifact["stable_sha256"] = file_digest(target)
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    report = verify_package(root)
    assert report["ok"] is False
    assert report["fingerprint_ok"] is False
