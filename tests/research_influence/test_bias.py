"""The bias audit must detect what is injected and refuse what it cannot see."""

from __future__ import annotations

from dataclasses import replace

import pytest

from jw.research_influence.bias import AUDITS, audit_bias
from jw.research_influence.cohort import synthetic_cohort
from jw.research_influence.contracts import AccessRoute, canonical_json
from jw.research_influence.explain import permutation_importance
from jw.research_influence.model import fit_model

COUNT = 180
SEED = 4
REFERENCE_YEAR = 2024


def _boost(paper, factor: float):
    """Inflate one paper's citations in both the total and the yearly history."""
    history = tuple((year, int(count * factor)) for year, count in paper.counts_by_year)
    total = int((paper.cited_by_count or 0) * factor)
    return replace(paper, counts_by_year=history, cited_by_count=total)


def _fit(papers):
    return fit_model(
        papers,
        reference_year=REFERENCE_YEAR,
        cohort_fingerprint="sha256:test",
    )


def _report(papers):
    model = _fit(papers)
    importances = permutation_importance(model, papers, seeds=(0, 1))
    return audit_bias(model, papers, importances=importances)


@pytest.fixture(scope="module")
def baseline():
    snapshot = synthetic_cohort(count=COUNT, seed=SEED, reference_year=REFERENCE_YEAR)
    return _report(list(snapshot.papers))


def test_every_declared_audit_is_reported_once(baseline):
    """The audit list is the contract; a missing one would be a silent gap."""
    names = tuple(finding.name for finding in baseline.findings)
    assert names == AUDITS
    assert len(set(names)) == len(names)


def test_each_finding_carries_method_evidence_and_advice(baseline):
    """A finding without a method is an opinion, not an audit."""
    for finding in baseline.findings:
        assert finding.status in {
            "detected",
            "not_detected",
            "insufficient_evidence",
            "cannot_assess",
        }
        assert finding.method
        assert finding.conclusion
        assert finding.recommendation
        assert finding.label


def test_severity_is_only_claimed_for_detected_findings(baseline):
    """An undetected audit may not be dressed up with a severity."""
    for finding in baseline.findings:
        if finding.status == "detected":
            assert finding.severity in {"low", "medium", "high"}
        else:
            assert finding.severity == "none"


def test_self_citation_and_demographics_are_refused_by_name(baseline):
    """Two audits must say "cannot assess", with the reason spelled out."""
    refused = {finding.name for finding in baseline.unassessable}
    assert {"self_citation", "author_demographics"} <= refused
    demographics = next(
        finding
        for finding in baseline.findings
        if finding.name == "author_demographics"
    )
    assert "not attempted" in demographics.method.lower() or "not attempted" in (
        demographics.label.lower()
    )
    assert demographics.evidence["attempted"] is False


def test_report_is_reproducible_and_serialisable(baseline):
    """Same cohort and model: same fingerprint, JSON-safe payload."""
    assert baseline.fingerprint().startswith("sha256:")
    payload = canonical_json(baseline.to_dict())
    assert '"audits"' in payload
    snapshot = synthetic_cohort(count=COUNT, seed=SEED, reference_year=REFERENCE_YEAR)
    again = _report(list(snapshot.papers))
    assert again.fingerprint() == baseline.fingerprint()


def test_markdown_has_a_row_per_audit(baseline):
    """The human report is generated from the same findings."""
    text = baseline.to_markdown()
    assert "## Bias audit" in text
    for finding in baseline.findings:
        assert f"### {finding.label}" in text
    assert text.count("| status |") >= 1


def test_cohort_block_counts_what_the_snapshot_contains(baseline):
    """The cohort table is what makes the sample size of each audit checkable."""
    cohort = baseline.cohort
    assert cohort["papers"] == COUNT
    assert cohort["labelled"] <= cohort["papers"]
    assert cohort["with_full_text"] <= cohort["papers"]
    assert cohort["horizon_years"] == 3


def test_injected_language_penalty_is_detected():
    """Boost 30 papers and label them non-English: the audit must see it."""
    snapshot = synthetic_cohort(count=COUNT, seed=SEED, reference_year=REFERENCE_YEAR)
    papers = []
    for index, paper in enumerate(snapshot.papers):
        if index % 6 == 0:
            papers.append(replace(_boost(paper, 4.0), language="zh"))
        else:
            papers.append(paper)
    report = _report(papers)
    finding = next(
        item for item in report.findings if item.name == "language_geography"
    )
    assert finding.status == "detected", finding.conclusion
    assert finding.evidence["difference"]["delta"] > 0
    assert "under-predicted" in finding.conclusion


def test_injected_topic_penalty_is_detected():
    """A boosted topic that the features cannot see must surface as residual."""
    snapshot = synthetic_cohort(count=COUNT, seed=SEED, reference_year=REFERENCE_YEAR)
    papers = []
    for index, paper in enumerate(snapshot.papers):
        if index % 5 == 0:
            papers.append(replace(_boost(paper, 5.0), keywords=("hot topic",)))
        else:
            papers.append(paper)
    report = _report(papers)
    finding = next(item for item in report.findings if item.name == "topic_heat")
    assert finding.status == "detected", finding.conclusion
    assert finding.evidence["difference"]["delta"] > 0


def test_injected_missingness_pattern_is_detected():
    """Hide the full text of the most-cited papers: MNAR must be reported."""
    snapshot = synthetic_cohort(count=COUNT, seed=SEED, reference_year=REFERENCE_YEAR)
    ordered = sorted(
        snapshot.papers,
        key=lambda paper: paper.cited_by_count or 0,
        reverse=True,
    )
    hidden = {paper.paper_id for paper in ordered[:60]}
    papers = [
        replace(
            paper,
            sections={},
            access_route=AccessRoute.METADATA_ONLY,
            fulltext_path="",
            fulltext_pages=0,
        )
        if paper.paper_id in hidden
        else paper
        for paper in snapshot.papers
    ]
    report = _report(papers)
    finding = next(item for item in report.findings if item.name == "missing_mechanism")
    assert finding.status == "detected", finding.conclusion
    assert finding.evidence["spread_bottom_to_top"] < 0 or finding.evidence["monotone"]


def test_audit_subset_can_be_requested(baseline):
    """Running one audit is enough for a targeted question."""
    model = _fit(
        list(
            synthetic_cohort(
                count=COUNT, seed=SEED, reference_year=REFERENCE_YEAR
            ).papers
        )
    )
    report = audit_bias(
        model,
        list(
            synthetic_cohort(
                count=COUNT, seed=SEED, reference_year=REFERENCE_YEAR
            ).papers
        ),
        audits=("time_censoring",),
    )
    assert tuple(finding.name for finding in report.findings) == ("time_censoring",)


def test_unknown_audit_names_are_ignored(baseline):
    """A typo must not crash a run that also asked for real audits."""
    model = _fit(
        list(
            synthetic_cohort(
                count=COUNT, seed=SEED, reference_year=REFERENCE_YEAR
            ).papers
        )
    )
    papers = list(
        synthetic_cohort(count=COUNT, seed=SEED, reference_year=REFERENCE_YEAR).papers
    )
    report = audit_bias(model, papers, audits=("time_censoring", "does_not_exist"))
    assert tuple(finding.name for finding in report.findings) == ("time_censoring",)
