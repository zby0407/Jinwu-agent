"""Cohort snapshots: frozen, hashed, and honest about being synthetic."""

from __future__ import annotations

import pytest

from jw.research_influence.cohort import (
    CohortSnapshot,
    build_snapshot,
    citations_within,
    is_right_censored,
    openalex_snapshot,
    select_cohort,
    synthetic_cohort,
)
from jw.research_influence.contracts import AccessRoute, PaperRecord


def test_snapshot_fingerprint_is_order_independent():
    """A snapshot is a set of papers, not a sequence."""
    first = PaperRecord(paper_id="paper:a", title="A", year=2015)
    second = PaperRecord(paper_id="paper:b", title="B", year=2016)
    forward = build_snapshot([first, second], cohort_id="c1", source="test")
    backward = build_snapshot([second, first], cohort_id="c1", source="test")
    assert forward.fingerprint() == backward.fingerprint()


def test_snapshot_round_trips_through_disk(tmp_path):
    """A saved cohort reloads to the same fingerprint."""
    snapshot = build_snapshot(
        [PaperRecord(paper_id="paper:a", title="A", year=2015, cited_by_count=9)],
        cohort_id="c1",
        query="supernovae",
        source="test",
        reference_year=2024,
    )
    path = snapshot.write(tmp_path / "cohort.json")
    reloaded = CohortSnapshot.load(path)
    assert reloaded.fingerprint() == snapshot.fingerprint()
    assert reloaded.reference_year == 2024
    assert reloaded.papers[0].cited_by_count == 9


def test_merge_duplicate_papers_uses_doi_not_the_id():
    """The same DOI arriving through two tools must not become two rows."""
    from jw.research_influence.cohort import merge_duplicate_papers

    first = PaperRecord(
        paper_id="paper:10.1_x",
        doi="10.1/x",
        title="T",
        year=2015,
        cited_by_count=10,
    )
    second = PaperRecord(
        paper_id="paper:10.1/x",
        doi="10.1/x",
        title="T",
        year=2015,
        abstract="A",
        cited_by_count=10,
    )
    merged, collapsed = merge_duplicate_papers([first, second])
    assert collapsed == 1
    assert len(merged) == 1
    assert merged[0].abstract == "A"


def test_imported_snapshot_decides_access_from_metadata_without_network():
    """An OA location must survive import even though no download is allowed."""
    record = {
        "id": "https://openalex.org/W1",
        "doi": "https://doi.org/10.1093/mnras/stv1947",
        "title": "An open paper",
        "publication_year": 2015,
        "authorships": [],
        "cited_by_count": 3,
        "locations": [
            {
                "pdf_url": "https://example.org/paper.pdf",
                "landing_page_url": "https://doi.org/10.1093/mnras/stv1947",
                "is_oa": True,
                "license": "cc-by",
                "version": "publishedVersion",
                "source": {"type": "journal"},
            }
        ],
    }
    snapshot = openalex_snapshot([record], cohort_id="cohort:oa")
    assert snapshot.papers[0].access_route is AccessRoute.OPEN_ACCESS_GOLD
    assert snapshot.papers[0].access is not None
    assert snapshot.papers[0].access.fulltext_path == ""


def test_imported_snapshot_uses_one_id_for_a_doi():
    record = {
        "id": "https://openalex.org/W1",
        "doi": "https://doi.org/10.1093/mnras/stv1947",
        "title": "A paper",
        "publication_year": 2015,
        "authorships": [],
    }
    snapshot = openalex_snapshot([record], cohort_id="cohort:id")
    assert snapshot.papers[0].paper_id == "paper:10.1093/mnras/stv1947"


def test_synthetic_cohort_is_deterministic():
    """The same seed produces the same cohort, so a run can be reproduced."""
    one = synthetic_cohort(count=25, seed=5, reference_year=2024)
    two = synthetic_cohort(count=25, seed=5, reference_year=2024)
    other = synthetic_cohort(count=25, seed=6, reference_year=2024)
    assert len(one) == 25
    assert one.fingerprint() == two.fingerprint()
    assert one.fingerprint() != other.fingerprint()


def test_synthetic_cohort_declares_itself_synthetic():
    """The cohort says what it is in the machine-readable record, not only prose."""
    snapshot = synthetic_cohort(count=10, seed=1, reference_year=2024)
    assert snapshot.is_synthetic
    assert snapshot.source.startswith("synthetic")
    assert any("not evidence" in note for note in snapshot.notes)
    assert snapshot.summary()["synthetic"] is True


def test_synthetic_cohort_contains_the_planted_effects():
    """The generator must actually produce the structure the audit looks for."""
    snapshot = synthetic_cohort(count=200, seed=9, reference_year=2024)
    tiers = {paper.venue.name for paper in snapshot.papers}
    assert len(tiers) > 3
    assert any(paper.has_full_text for paper in snapshot.papers)
    assert any(not paper.has_full_text for paper in snapshot.papers)
    assert any(paper.open_data for paper in snapshot.papers)
    assert any(paper.counts_by_year for paper in snapshot.papers)


def test_horizon_target_uses_per_year_history():
    """Only citations inside the horizon count towards the target."""
    paper = PaperRecord(
        paper_id="paper:a",
        year=2015,
        counts_by_year=((2015, 4), (2016, 3), (2017, 2), (2018, 50), (2019, 60)),
        cited_by_count=119,
    )
    assert citations_within(paper, horizon=3) == 9
    assert citations_within(paper, horizon=1) == 4


def test_missing_history_is_not_replaced_by_the_lifetime_count():
    """The fallback to total citations would be leakage, so it is refused."""
    paper = PaperRecord(paper_id="paper:a", year=2015, cited_by_count=500)
    assert citations_within(paper) is None


def test_recent_papers_are_right_censored():
    """A three-year window that has not elapsed cannot be a label."""
    recent = PaperRecord(paper_id="paper:a", year=2023)
    old = PaperRecord(paper_id="paper:b", year=2015)
    assert is_right_censored(recent, reference_year=2024)
    assert not is_right_censored(old, reference_year=2024)
    assert is_right_censored(PaperRecord(paper_id="paper:c"), reference_year=2024)


def test_selection_filters_are_explicit():
    """Dropping rows is a decision with a reason, recorded by the caller."""
    papers = (
        PaperRecord(paper_id="paper:a", year=2010, doi="10.1/a", abstract="x"),
        PaperRecord(paper_id="paper:b", year=2020, doi="", abstract=""),
        PaperRecord(paper_id="paper:c", year=None, doi="10.1/c", abstract="y"),
    )
    assert [p.paper_id for p in select_cohort(papers, min_year=2015)] == ["paper:b"]
    assert [p.paper_id for p in select_cohort(papers, require_doi=True)] == [
        "paper:a",
        "paper:c",
    ]
    assert [p.paper_id for p in select_cohort(papers, require_abstract=True)] == [
        "paper:a",
        "paper:c",
    ]


def test_snapshot_summary_reports_degradation():
    """The header counts how much of the cohort is metadata only."""
    snapshot = synthetic_cohort(count=40, seed=2, reference_year=2024)
    summary = snapshot.summary()
    assert summary["papers"] == 40
    assert 0 < summary["with_full_text"] < 40
    assert summary["with_open_data"] <= summary["with_full_text"]
    assert summary["fingerprint"].startswith("sha256:")


@pytest.mark.parametrize("field", ["cohort_id", "query", "source"])
def test_snapshot_identity_fields_survive_the_disk(field):
    """Identity fields are not lost by serialisation."""
    snapshot = build_snapshot(
        [PaperRecord(paper_id="paper:a", year=2015)],
        cohort_id="c9",
        query="q",
        source="s",
        reference_year=2020,
    )
    reloaded = CohortSnapshot.from_dict(snapshot.to_dict())
    assert getattr(reloaded, field) == getattr(snapshot, field)


def test_a_window_the_history_does_not_cover_is_not_a_zero():
    """A 1998 paper's first three years are outside OpenAlex's per-year history."""
    uncovered = PaperRecord(
        paper_id="paper:old",
        title="Old",
        year=1998,
        cited_by_count=40,
        counts_by_year=((2012, 3), (2013, 5)),
    )
    assert citations_within(uncovered, horizon=3) is None
    covered = PaperRecord(
        paper_id="paper:new",
        title="New",
        year=2020,
        cited_by_count=8,
        counts_by_year=((2020, 2), (2021, 3)),
    )
    assert citations_within(covered, horizon=3) == 5


def _payload(work: str, *, doi: str, title: str, abstract: bool = False) -> dict:
    record = {
        "id": f"https://openalex.org/{work}",
        "doi": f"https://doi.org/{doi}",
        "title": title,
        "publication_year": 2016,
        "type": "article",
        "cited_by_count": 7,
        "authorships": [],
        "primary_location": {"source": {"display_name": "MNRAS"}},
    }
    if abstract:
        record["abstract_inverted_index"] = {"We": [0], "observe": [1], "it": [2]}
    return record


def test_one_doi_under_two_work_ids_becomes_one_paper():
    """OpenAlex lists pending merges twice; a cohort must not count them twice."""
    bare = _payload("W1", doi="10.1000/dup", title="Dup")
    rich = _payload("W2", doi="10.1000/dup", title="Dup", abstract=True)

    snapshot = openalex_snapshot(
        [bare, rich], cohort_id="dup", query="supernova", reference_year=2024
    )

    assert len(snapshot.papers) == 1
    assert snapshot.papers[0].abstract
    assert "collapsed_1_duplicate_records" in snapshot.notes


def test_the_collapse_does_not_depend_on_page_order():
    """Whichever copy arrives first, the same record wins."""
    bare = _payload("W1", doi="10.1000/dup", title="Dup")
    rich = _payload("W2", doi="10.1000/dup", title="Dup", abstract=True)

    forward = openalex_snapshot([bare, rich], cohort_id="dup", reference_year=2024)
    backward = openalex_snapshot([rich, bare], cohort_id="dup", reference_year=2024)

    assert forward.fingerprint() == backward.fingerprint()
