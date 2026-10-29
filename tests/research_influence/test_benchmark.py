"""Benchmark: one frozen cohort, one honest scoreboard, one repeatable report.

Every number here is measured on evidence the repository can regenerate:

* the extraction half uses a real PDF with a text layer, and scores title,
  sections, reference entries and the data statement against what was written
  into that PDF;
* the modelling half uses the synthetic cohort, whose generative structure is
  known — so the metrics validate the pipeline and say nothing about real
  astrophysics;
* the audit half injects six biases and checks that at least five are found,
  that the two refused audits stay refused, and that nothing is auto-accepted.

The thresholds live in ``THRESHOLDS`` so the numbers in the report document and
the numbers a reader reproduces cannot drift apart.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from jw.research_influence.bias import audit_bias
from jw.research_influence.cohort import synthetic_cohort
from jw.research_influence.contracts import AccessRoute, Provenance
from jw.research_influence.explain import permutation_importance
from jw.research_influence.features import venue_tier
from jw.research_influence.model import fit_model
from jw.research_influence.workflow import (
    RunOptions,
    paper_from_upload,
    run_influence,
)

COUNT = 300
SEED = 5
REFERENCE_YEAR = 2024
HORIZON = 3

THRESHOLDS = {
    "spearman": 0.45,
    "top_decile_lift": 2.5,
    "interval_coverage_low": 0.70,
    "interval_coverage_high": 0.90,
    "median_absolute_error_log": 0.60,
    "finish_seconds": 120.0,
    "biases_detected": 5,
    "audits_cannot_assess": 2,
}

PDF_TITLE = "Photometric calibration of nearby supernovae light curves"
PDF_ABSTRACT = (
    "We present a homogeneous photometric calibration of 42 nearby Type Ia "
    "supernovae observed between 2018 and 2022."
)
PDF_SECTIONS = {
    "1. Introduction": "Type Ia supernovae remain the workhorse distance indicator.",
    "2. Data and Methods": "Photometry was reduced with a single zeropoint solution.",
    "3. Results": "The Hubble diagram scatter falls to 0.11 mag.",
    "4. Data availability": "All light curves are released on Zenodo.",
}
PDF_STATEMENT = "Photometry and scripts: https://zenodo.org/record/7654321"
PDF_REFERENCES = (
    "1. Riess, A. G. et al. 1998, AJ, 116, 1009, doi:10.1086/300499",
    "2. Perlmutter, S. et al. 1999, ApJ, 517, 565, doi:10.1086/307221",
    "3. Scolnic, D. et al. 2018, ApJ, 859, 101, doi:10.3847/1538-4357/aab9bb",
)


def _boost(paper, factor: float):
    from dataclasses import replace

    history = tuple((year, int(count * factor)) for year, count in paper.counts_by_year)
    total = int((paper.cited_by_count or 0) * factor)
    return replace(paper, counts_by_year=history, cited_by_count=total)


def _hide_full_text(paper):
    from dataclasses import replace

    return replace(
        paper,
        sections={},
        access_route=AccessRoute.METADATA_ONLY,
        fulltext_path="",
        fulltext_pages=0,
    )


@pytest.fixture(scope="module")
def baseline_papers():
    return list(
        synthetic_cohort(count=COUNT, seed=SEED, reference_year=REFERENCE_YEAR).papers
    )


@pytest.fixture(scope="module")
def fitted(baseline_papers):
    model = fit_model(
        baseline_papers,
        reference_year=REFERENCE_YEAR,
        cohort_fingerprint="sha256:benchmark",
    )
    return model


@pytest.fixture(scope="module")
def benchmark_pdf(tmp_path_factory) -> Path:
    """A real PDF, so the extraction scores are not scored against mocks."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    target = tmp_path_factory.mktemp("bench") / "benchmark.pdf"
    figure = plt.figure(figsize=(8.5, 11))
    figure.text(0.1, 0.96, PDF_TITLE, fontsize=13)
    figure.text(0.1, 0.92, "Abstract", fontsize=11)
    figure.text(0.1, 0.88, PDF_ABSTRACT, fontsize=9)
    offset = 0.80
    for heading, body in PDF_SECTIONS.items():
        figure.text(0.1, offset, heading, fontsize=11)
        figure.text(0.1, offset - 0.035, body, fontsize=9)
        offset -= 0.08
    figure.text(0.1, 0.40, PDF_STATEMENT, fontsize=9)
    figure.text(0.1, 0.34, "References", fontsize=11)
    offset = 0.30
    for entry in PDF_REFERENCES:
        figure.text(0.1, offset, entry, fontsize=8)
        offset -= 0.03
    figure.savefig(target)
    plt.close(figure)
    return target


def test_extraction_quality_on_a_real_pdf(tmp_path, benchmark_pdf):
    """Fields, sections, references and the data statement come from the PDF."""
    paper = paper_from_upload(benchmark_pdf, directory=tmp_path / "store")
    sections = set(paper.sections)
    # Headings are normalised to canonical names, not copied verbatim.
    expected = {"introduction", "methods", "results", "data_availability"}
    assert expected <= sections, sorted(sections)
    assert "photometric calibration" in paper.sections.get("body", "").casefold() or (
        "photometric calibration" in paper.sections.get("abstract", "").casefold()
    )
    assert len(paper.references) >= len(PDF_REFERENCES)
    resolved = [item for item in paper.references if item.doi]
    assert len(resolved) >= 2, [item.doi for item in paper.references]
    assert any("zenodo.org" in record.url for record in paper.open_data)
    assert paper.access_route == AccessRoute.USER_SUPPLIED
    assert paper.fulltext_pages >= 1
    # A user-supplied PDF has no citation record until it is matched: the absence
    # is typed, never guessed.
    assert paper.cited_by_count is None
    assert paper.missing


def test_field_provenance_covers_what_the_pdf_supplied(benchmark_pdf, tmp_path):
    """Every field the PDF answered must carry a locator-bearing provenance row."""
    paper = paper_from_upload(benchmark_pdf, directory=tmp_path / "store")
    for field in ("title", "sections", "references", "open_data"):
        entries = paper.provenance.get(field, ())
        if field in paper.missing:
            continue
        assert entries, field
        assert all(isinstance(item, Provenance) for item in entries)
        assert all(item.provider for item in entries)


def test_model_metrics_reach_the_declared_targets(fitted, baseline_papers):
    """The prediction half is scored out of sample, on a cohort with known structure."""
    holdout = fitted.metrics_for(baseline_papers)
    assert holdout["eval_subset"] == "holdout"
    assert holdout["scored_rows"] >= 40, holdout
    assert holdout["spearman"] >= THRESHOLDS["spearman"], holdout
    assert (
        THRESHOLDS["interval_coverage_low"]
        <= holdout["interval_coverage"]
        <= THRESHOLDS["interval_coverage_high"]
    ), holdout
    assert (
        holdout["median_absolute_error_log"] <= THRESHOLDS["median_absolute_error_log"]
    ), holdout

    # The top-decile lift needs a decile worth estimating, so it is quoted on the
    # whole labelled set and marked descriptive by the model card's own fields.
    descriptive = fitted.metrics_for(baseline_papers, subset="all")
    assert descriptive["in_sample_rows"] > 0
    assert descriptive["top_decile_lift"] >= THRESHOLDS["top_decile_lift"], descriptive
    assert descriptive["interval_coverage"] > holdout["interval_coverage"]


def test_censored_papers_are_never_training_labels(fitted, baseline_papers):
    """A paper whose citation window has not finished cannot be a label."""
    from jw.research_influence.features import targets

    truth = targets(baseline_papers, reference_year=REFERENCE_YEAR, horizon=HORIZON)
    censored = [
        paper.paper_id
        for paper, available in zip(baseline_papers, truth.available, strict=True)
        if not available
    ]
    assert censored, "the benchmark cohort must contain censored papers"
    assert fitted.card.labelled == len(baseline_papers) - len(censored)
    trained = set(fitted.card.training_paper_ids)
    held = set(fitted.card.holdout_paper_ids)
    assert len(trained) == fitted.card.training_rows
    assert len(held) == fitted.card.holdout_rows
    assert not trained & held, "a paper cannot be both trained on and held out"
    assert len(trained) + len(held) == fitted.card.labelled


def test_six_injected_biases_and_how_many_are_found(baseline_papers):
    """Inject six biases, one at a time, and score the audit against the truth."""
    from dataclasses import replace

    boosted_language = []
    boosted_topic = []
    boosted_venue = []
    stripped_affiliations = []
    hidden_top = [
        paper.paper_id
        for paper in sorted(
            baseline_papers, key=lambda item: item.cited_by_count or 0, reverse=True
        )[:120]
    ]
    for index, paper in enumerate(baseline_papers):
        boosted_language.append(
            replace(_boost(paper, 4.0), language="zh") if index % 6 == 0 else paper
        )
        boosted_topic.append(
            replace(_boost(paper, 5.0), keywords=("hot topic",))
            if index % 5 == 0
            else paper
        )
        tier = venue_tier(paper.venue) if paper.venue else None
        boosted_venue.append(
            _boost(paper, 3.0) if tier is not None and tier >= 3 else paper
        )
        stripped_affiliations.append(
            replace(
                _boost(paper, 3.0),
                authors=tuple(
                    replace(author, institution="", country="")
                    for author in paper.authors
                ),
            )
            if index % 4 == 0
            else paper
        )

    injected = {
        "language_geography": boosted_language,
        "topic_heat": boosted_topic,
        "venue_prestige": boosted_venue,
        "affiliation_hierarchy": stripped_affiliations,
        # An advantage granted to papers whose full text is readable: the audit
        # must see it, because that is exactly the "visible work is rewarded"
        # pathway a reader cannot see from the model's features.
        "open_access_visibility": [
            _boost(paper, 3.0) if paper.has_full_text else paper
            for paper in baseline_papers
        ],
        "missing_mechanism": [
            _hide_full_text(paper) if paper.paper_id in hidden_top else paper
            for paper in baseline_papers
        ],
    }

    detected: dict[str, str] = {}
    for name, papers in injected.items():
        model = fit_model(
            papers,
            reference_year=REFERENCE_YEAR,
            cohort_fingerprint="sha256:benchmark",
        )
        importances = permutation_importance(model, papers, seeds=(0,))
        report = audit_bias(model, papers, importances=importances)
        finding = next(item for item in report.findings if item.name == name)
        detected[name] = finding.status
        if finding.status == "detected":
            assert finding.severity != "none"

    found = [name for name, status in detected.items() if status == "detected"]
    assert len(found) >= THRESHOLDS["biases_detected"], detected
    assert "language_geography" in found, detected
    assert "topic_heat" in found, detected
    assert "missing_mechanism" in found, detected


def test_a_visibility_advantage_the_model_absorbs_is_not_called_clean(
    baseline_papers,
):
    """Inflating visible papers makes the residuals even -- that is not fairness.

    When the model can tell the two access groups apart it learns the gap, so the
    residual comparison goes quiet.  The audit therefore reports the pathway as
    unresolved and shows the predicted gap it absorbed, instead of clearing it.
    """
    injected = [
        _boost(paper, 3.0) if paper.has_full_text else paper
        for paper in baseline_papers
    ]
    model = fit_model(injected, reference_year=REFERENCE_YEAR)
    report = audit_bias(model, injected)
    finding = next(
        item for item in report.findings if item.name == "open_access_visibility"
    )
    assert finding.status == "insufficient_evidence", finding.evidence
    assert finding.evidence["absorbed_into_features"] is True
    assert abs(finding.evidence["predicted_gap_log"]) >= 0.30
    assert finding.severity == "none"


def test_the_two_refused_audits_stay_refused(fitted, baseline_papers):
    """Refusing an audit is a result; silently estimating it would be a defect."""
    importances = permutation_importance(fitted, baseline_papers, seeds=(0,))
    report = audit_bias(fitted, baseline_papers, importances=importances)
    refused = {finding.name for finding in report.unassessable}
    assert "author_demographics" in refused
    assert "self_citation" in refused
    assert len(report.unassessable) >= THRESHOLDS["audits_cannot_assess"]
    demographics = next(
        item for item in report.findings if item.name == "author_demographics"
    )
    assert demographics.evidence["attempted"] is False


def test_no_high_risk_conclusion_is_taken_automatically(fitted, baseline_papers):
    """Detected bias produces an explicit caveat, never a silent correction."""
    importances = permutation_importance(fitted, baseline_papers, seeds=(0,))
    report = audit_bias(fitted, baseline_papers, importances=importances)
    for finding in report.findings:
        if finding.status == "detected":
            assert finding.recommendation
            assert "correct" not in finding.conclusion.casefold()
            assert finding.evidence


@pytest.fixture(scope="module")
def benchmark_run(tmp_path_factory, benchmark_pdf):
    workspace = tmp_path_factory.mktemp("bench-run")
    snapshot = synthetic_cohort(count=COUNT, seed=SEED, reference_year=REFERENCE_YEAR)
    return run_influence(
        workspace=workspace,
        snapshot=snapshot,
        uploads=(benchmark_pdf,),
        options=RunOptions(
            reference_year=REFERENCE_YEAR,
            horizon=HORIZON,
            seed=3,
            max_subjects=3,
            importance_repeats=2,
            importance_seeds=(0, 1),
            bootstrap=40,
        ),
    )


def test_the_bundle_answers_the_six_questions(benchmark_run):
    """The report must answer prediction, explanation, bias, repro and limits."""
    root = Path(benchmark_run.package_dir)
    report = (root / "report.md").read_text(encoding="utf-8")
    for marker in (
        "1. 运行概要",
        "2. 数据来源与可得性",
        "3. 预测结果",
        "4. 解释",
        "5. 偏差审计",
        "6. 可复现性",
        "7. 局限",
    ):
        assert marker in report, marker
    assert "80% 区间" in report

    metrics = json.loads((root / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["spearman"] >= THRESHOLDS["spearman"]
    assert metrics["cohort_fingerprint"].startswith("sha256:")
    assert metrics["wall_seconds"] < THRESHOLDS["finish_seconds"]

    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    roles = {item["role"] for item in manifest["artifacts"]}
    assert {"papers", "predictions", "factors", "bias_audit", "report"} <= roles
    assert all(item["sha256"].startswith("sha256:") for item in manifest["artifacts"])


def test_the_uploaded_pdf_is_explained_and_traceable(benchmark_run, benchmark_pdf):
    """A paper the operator supplied gets an explanation and a source line."""
    upload_ids = [paper.paper_id for paper in benchmark_run.uploads]
    assert upload_ids
    explained = {item.paper_id for item in benchmark_run.explanations}
    assert upload_ids[0] in explained

    root = Path(benchmark_run.package_dir)
    rows = [
        line
        for line in (root / "provenance.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows
    entries = [json.loads(row) for row in rows]
    upload_rows = [entry for entry in entries if entry.get("paper_id") == upload_ids[0]]
    assert upload_rows
    assert all(entry.get("provider") for entry in upload_rows)


def test_rerunning_the_same_inputs_gives_the_same_numbers(
    tmp_path, benchmark_pdf, benchmark_run
):
    """Reproducibility is a claim about hashes, so it is checked as one."""
    snapshot = synthetic_cohort(count=COUNT, seed=SEED, reference_year=REFERENCE_YEAR)
    again = run_influence(
        workspace=tmp_path / "again",
        snapshot=snapshot,
        uploads=(benchmark_pdf,),
        options=RunOptions(
            reference_year=REFERENCE_YEAR,
            horizon=HORIZON,
            seed=3,
            max_subjects=3,
            importance_repeats=2,
            importance_seeds=(0, 1),
            bootstrap=40,
        ),
    )
    keys = (
        "spearman",
        "top_decile_lift",
        "interval_coverage",
        "median_absolute_error_log",
        "cohort_fingerprint",
        "labelled",
    )
    assert {key: again.metrics[key] for key in keys} == {
        key: benchmark_run.metrics[key] for key in keys
    }
    assert again.metrics["fingerprint"] == benchmark_run.metrics["fingerprint"]
    first = Path(benchmark_run.package_dir) / "factors.csv"
    second = Path(again.package_dir) / "factors.csv"
    assert first.read_text(encoding="utf-8") == second.read_text(encoding="utf-8")


def test_synthetic_cohort_is_labelled_as_synthetic_in_the_report(benchmark_run):
    """The report must not let a validation cohort pose as astrophysics."""
    report = (Path(benchmark_run.package_dir) / "report.md").read_text(encoding="utf-8")
    assert "合成" in report or "synthetic" in report.casefold()


def test_missing_full_text_widens_the_statement_not_the_numbers(benchmark_run):
    """When a subject has no full text, the explanation says so instead of faking."""
    report = (Path(benchmark_run.package_dir) / "report.md").read_text(encoding="utf-8")
    metrics = benchmark_run.metrics
    assert metrics["labelled"] <= len(benchmark_run.papers)
    assert "全文" in report


def test_the_fingerprints_agree_between_two_independent_processes():
    """In-process agreement is too easy: the hashes must survive a new process.

    This is the failure that actually happened. Everything the run hashed was
    stable within one process, yet a second `measure_benchmark.py` disagreed,
    because a PDF's creation date, a temporary directory and an artefact's byte
    digest were all reaching the fingerprint. Re-running the measurement here is
    the only way to catch that class of leak.
    """
    root = Path(__file__).resolve().parents[2]
    script = root / "tests/research_influence/fixtures/measure_benchmark.py"
    env = dict(os.environ, MPLCONFIGDIR=os.environ.get("MPLCONFIGDIR", "/tmp/mpl"))
    outputs = []
    for _ in range(2):
        completed = subprocess.run(
            [sys.executable, str(script)],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=600,
        )
        outputs.append(json.loads(completed.stdout)["pipeline"])
    assert outputs[0]["fingerprint"] == outputs[1]["fingerprint"]
    assert outputs[0]["package_fingerprint"] == outputs[1]["package_fingerprint"]
