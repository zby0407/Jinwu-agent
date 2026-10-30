"""Cohort drift: the numbers a report can pin, computed from two files."""

from __future__ import annotations

import pytest

from jw.research_influence.cohort import build_snapshot
from jw.research_influence.contracts import PaperRecord, VenueRecord
from jw.research_influence.drift import (
    PERCENTILE_MOVE_THRESHOLD,
    diff_snapshots,
    drift_markdown,
    paper_identity,
)


def _paper(key: str, *, citations: int | None, work_id: str = "", doi: str = ""):
    return PaperRecord(
        paper_id=f"paper:{key}",
        title=key.upper(),
        year=2015,
        cited_by_count=citations,
        work_id=work_id or f"W{key}",
        doi=doi,
        venue=VenueRecord(name="MNRAS"),
    )


def _snapshot(papers, *, cohort_id: str, query: str = "supernova", year: int = 2024):
    return build_snapshot(
        papers,
        cohort_id=cohort_id,
        query=query,
        source="openalex",
        reference_year=year,
    )


def test_identical_snapshots_report_no_churn():
    """Same papers twice: nothing was lost, and the verdict says so."""
    papers = [_paper("a", citations=10), _paper("b", citations=20)]
    older = _snapshot(papers, cohort_id="one")
    newer = _snapshot(papers, cohort_id="two")

    diff = diff_snapshots(older, newer)

    assert diff.membership["kept"] == 2
    assert diff.membership["added"] == 0
    assert diff.membership["removed"] == 0
    assert diff.membership["jaccard"] == 1.0
    assert diff.churn_share == 0.0
    assert "没有换掉任何论文" in diff.verdict
    # The same cohort must keep its own top decile; an overlap of zero here
    # would mean the rank comparison is looking at the wrong side.
    assert diff.ranks["top_overlap"] == diff.ranks["left_top"]
    assert diff.ranks["top_overlap_share"] == 1.0
    assert diff.ranks["entered_top"] == 0


def test_membership_counts_and_jaccard():
    """Three survivors out of four on each side: Jaccard 3/5, not 3/4."""
    older = _snapshot(
        [
            _paper("a", citations=1),
            _paper("b", citations=2),
            _paper("c", citations=3),
            _paper("d", citations=4),
        ],
        cohort_id="one",
    )
    newer = _snapshot(
        [
            _paper("a", citations=1),
            _paper("b", citations=2),
            _paper("c", citations=3),
            _paper("e", citations=5),
        ],
        cohort_id="two",
    )

    diff = diff_snapshots(older, newer)

    assert diff.membership["papers_left"] == 4
    assert diff.membership["papers_right"] == 4
    assert diff.membership["kept"] == 3
    assert diff.membership["added"] == 1
    assert diff.membership["removed"] == 1
    assert diff.membership["jaccard"] == pytest.approx(0.6)
    assert diff.membership["added_sample"] == ["work:We"]
    assert diff.membership["removed_sample"] == ["work:Wd"]
    assert diff.churn_share == pytest.approx(0.25)


def test_citation_growth_is_reported_for_survivors():
    """Two builds apart, the same paper already has more citations."""
    older = _snapshot(
        [
            _paper("a", citations=10),
            _paper("b", citations=20),
            _paper("c", citations=5),
        ],
        cohort_id="one",
    )
    newer = _snapshot(
        [
            _paper("a", citations=14),
            _paper("b", citations=20),
            _paper("c", citations=3),
        ],
        cohort_id="two",
    )

    diff = diff_snapshots(older, newer)

    assert diff.values["citation_pairs"] == 3
    assert diff.values["citations_changed"] == 2
    assert diff.values["citations_gained"] == 1
    assert diff.values["citations_lost"] == 1
    assert diff.values["median_citation_delta"] == 0.0
    assert diff.values["median_abs_citation_delta"] == 2.0
    assert diff.values["max_abs_citation_delta"] == 4.0
    assert diff.values["total_citation_gain"] == 2


def test_rank_shift_reports_that_a_survivor_moved():
    """A survivor can keep its citations and still lose its place in the cohort."""
    older = _snapshot(
        [
            _paper("a", citations=1),
            _paper("b", citations=2),
            _paper("c", citations=3),
            _paper("d", citations=4),
        ],
        cohort_id="one",
    )
    newer = _snapshot(
        [
            _paper("a", citations=1),
            _paper("b", citations=2),
            _paper("c", citations=100),
            _paper("d", citations=4),
        ],
        cohort_id="two",
    )

    diff = diff_snapshots(older, newer)

    assert diff.ranks["compared"] == 4
    assert diff.ranks["left_top"] == 1
    assert diff.ranks["right_top"] == 1
    assert diff.ranks["top_overlap"] == 0
    assert diff.ranks["entered_top"] == 1
    assert diff.ranks["dropped_from_top"] == 1
    assert diff.ranks["moved_beyond_threshold"] == 2
    assert diff.ranks["median_abs_percentile_shift"] > PERCENTILE_MOVE_THRESHOLD / 2
    assert diff.ranks["spearman_citations"] is not None


def test_identity_prefers_work_id_then_doi():
    """A provider-side id change is not churn when the DOI still matches."""
    older = _snapshot(
        [
            PaperRecord(paper_id="paper:x", title="X", work_id="W1", doi="10.1/x"),
            PaperRecord(paper_id="paper:y", title="Y", work_id="", doi="10.1/y"),
        ],
        cohort_id="one",
    )
    newer = _snapshot(
        [
            PaperRecord(paper_id="paper:x", title="X", work_id="W1", doi="10.1/x"),
            PaperRecord(paper_id="paper:y2", title="Y", work_id="W9", doi="10.1/y"),
        ],
        cohort_id="two",
    )

    diff = diff_snapshots(older, newer)

    assert diff.membership["added"] == 0
    assert diff.membership["removed"] == 0
    assert paper_identity(older.papers[0]) == "work:W1"


def test_different_question_is_not_drift():
    """Two different queries answer two different questions."""
    older = _snapshot([_paper("a", citations=1)], cohort_id="one", query="supernova")
    newer = _snapshot([_paper("a", citations=1)], cohort_id="two", query="pulsar")

    diff = diff_snapshots(older, newer)

    assert diff.same_question is False
    assert "不是同一个问题" in diff.verdict
    assert any(note.startswith("different_question") for note in diff.notes)


def test_disjoint_cohorts_say_so():
    """No overlap at all is a finding, not a zero to divide by."""
    older = _snapshot([_paper("a", citations=1)], cohort_id="one")
    newer = _snapshot([_paper("z", citations=1)], cohort_id="two")

    diff = diff_snapshots(older, newer)

    assert diff.membership["kept"] == 0
    assert diff.membership["jaccard"] == 0.0
    assert diff.churn_share == 1.0
    assert "互不相交" in diff.verdict
    assert diff.values["citation_pairs"] == 0
    assert diff.ranks["top_overlap"] == 0


def test_metadata_repair_shows_up_as_value_drift():
    """An abstract that appeared later is drift too, not only citations."""
    older = _snapshot(
        [PaperRecord(paper_id="paper:a", title="A", work_id="W1", cited_by_count=3)],
        cohort_id="one",
    )
    newer = _snapshot(
        [
            PaperRecord(
                paper_id="paper:a",
                title="A",
                work_id="W1",
                cited_by_count=3,
                abstract="now indexed",
            )
        ],
        cohort_id="two",
    )

    diff = diff_snapshots(older, newer)

    assert diff.values["abstract_gained"] == 1
    assert diff.values["abstract_lost"] == 0
    assert diff.values["citations_changed"] == 0


def test_fingerprint_pins_the_numbers_and_the_two_snapshots():
    """Same inputs, same hash; any change to a number, a new hash."""
    older = _snapshot([_paper("a", citations=10)], cohort_id="one")
    newer = _snapshot([_paper("a", citations=12)], cohort_id="two")
    again = _snapshot([_paper("a", citations=12)], cohort_id="two")

    first = diff_snapshots(older, newer)
    second = diff_snapshots(older, again)
    assert first.fingerprint() == second.fingerprint()

    shifted = diff_snapshots(
        older, _snapshot([_paper("a", citations=99)], cohort_id="two")
    )
    assert shifted.fingerprint() != first.fingerprint()
    assert first.to_dict()["fingerprint"] == first.fingerprint()


def test_synthetic_snapshots_are_flagged():
    """A generator's churn is not the literature's churn."""
    from jw.research_influence.cohort import synthetic_cohort

    older = synthetic_cohort(cohort_id="syn-one", count=10, seed=1)
    newer = synthetic_cohort(cohort_id="syn-two", count=10, seed=1)

    diff = diff_snapshots(older, newer)

    assert any(note.startswith("synthetic_snapshot") for note in diff.notes)


def test_markdown_carries_the_numbers_and_the_fingerprint():
    """The rendered block is what lands in the report, so it must be complete."""
    older = _snapshot(
        [_paper("a", citations=1), _paper("b", citations=2)], cohort_id="one"
    )
    newer = _snapshot([_paper("a", citations=1)], cohort_id="two")

    text = drift_markdown(diff_snapshots(older, newer))

    assert "Cohort drift" in text
    assert "`one`" in text and "`two`" in text
    assert "Jaccard" in text
    assert "Spearman" in text
    assert "diff fingerprint" in text
    assert "判读" in text


def test_duplicate_identities_are_collapsed_and_noted():
    """Two records for one work would otherwise read as one removal."""
    older = _snapshot(
        [
            PaperRecord(paper_id="paper:a", title="A", work_id="W1"),
            PaperRecord(paper_id="paper:a2", title="A again", work_id="W1"),
        ],
        cohort_id="one",
    )
    newer = _snapshot(
        [PaperRecord(paper_id="paper:a", title="A", work_id="W1")],
        cohort_id="two",
    )

    diff = diff_snapshots(older, newer)

    assert diff.membership["papers_left"] == 1
    assert any(note.startswith("duplicate_identities") for note in diff.notes)
