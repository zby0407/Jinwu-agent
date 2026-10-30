"""Topic evolution must be a property of the cohort, stated with its caveat."""

from __future__ import annotations

from pathlib import Path

from jw.research_influence.contracts import PaperRecord
from jw.research_influence.topics import (
    RECENT_YEARS,
    paper_topic_position,
    topic_trends,
    trend_note,
)


def _paper(
    paper_id: str,
    *,
    year: int,
    keywords: tuple[str, ...],
    citations: int | None = 10,
) -> PaperRecord:
    return PaperRecord(
        paper_id=paper_id,
        title=f"Paper {paper_id}",
        year=year,
        keywords=keywords,
        cited_by_count=citations,
    )


def _cohort() -> list[PaperRecord]:
    papers = [
        _paper(f"old{i}", year=2010 + i, keywords=("Legacy topic",), citations=40)
        for i in range(6)
    ]
    papers += [
        _paper(f"new{i}", year=2023 + i % 2, keywords=("Rising topic",), citations=5)
        for i in range(6)
    ]
    return papers


def test_a_rising_topic_has_a_positive_growth():
    """Growth compares the recent share against the earlier share, not counts."""
    trends = {
        trend.topic: trend for trend in topic_trends(_cohort(), reference_year=2024)
    }
    rising = trends["Rising topic"]
    assert rising.rising
    # A topic with no papers in the earlier window has no baseline to divide by,
    # so it is reported as new rather than with an invented growth rate.
    assert rising.new_topic
    assert rising.growth is None
    assert "新出现" in rising.sentence()
    legacy = trends["Legacy topic"]
    assert not legacy.rising
    assert legacy.growth == -1.0


def test_shares_are_shares_of_the_cohort_not_of_the_topic():
    """Two topics of equal size have equal shares; the sum over topics can exceed 1."""
    trends = topic_trends(_cohort(), reference_year=2024)
    for trend in trends:
        assert trend.recent_share is not None
        assert 0.0 <= trend.recent_share <= 1.0
        assert trend.earlier_share is not None
        assert 0.0 <= trend.earlier_share <= 1.0


def test_trends_are_ordered_by_size():
    """The report prints the top of this list, so the order must be stable."""
    trends = topic_trends(_cohort(), reference_year=2024)
    assert [trend.papers for trend in trends] == sorted(
        (trend.papers for trend in trends), reverse=True
    )


def test_a_tiny_topic_is_marked_unreadable():
    """A cell of two papers is not an evolution; it says so instead of a share."""
    papers = [*_cohort(), _paper("solo", year=2024, keywords=("Rare topic",))]
    trend = next(
        item
        for item in topic_trends(papers, reference_year=2024)
        if item.topic == "Rare topic"
    )
    assert trend.status == "too_few_papers"
    assert "fewer_than_min_papers" in trend.notes
    assert "无法评估" in trend.sentence()


def test_untimed_papers_do_not_create_a_share():
    """A paper with no year belongs to no period; guessing one would be invention."""
    papers = [_paper("x", year=0, keywords=("Undated topic",))] * 3
    trend = next(
        item
        for item in topic_trends(papers, reference_year=2024)
        if item.topic == "Undated topic"
    )
    assert trend.first_year is None
    assert trend.recent_share is None
    assert trend.earlier_share is None
    assert trend.growth is None
    assert not trend.new_topic


def test_position_uses_the_cohort_table():
    """The subject's topics are read off the same table the report prints."""
    cohort = _cohort()
    trends = topic_trends(cohort, reference_year=2024)
    rows = paper_topic_position(cohort[0], trends)
    assert rows[0]["topic"] == "Legacy topic"
    assert rows[0]["papers"] == 6
    assert rows[0]["rising"] is False


def test_the_caveat_travels_with_the_numbers():
    """Topic labels are model-assigned; the note has to say that."""
    note = trend_note(topic_trends(_cohort(), reference_year=2024))
    assert "索引模型" in note
    assert "主题热度偏差审计" in note
    assert "无法评估" in trend_note(())


def test_the_run_writes_and_reports_the_topic_table(tmp_path):
    """A run stores topics.csv and the report reads it in plain Chinese."""
    from jw.research_influence.cohort import synthetic_cohort
    from jw.research_influence.workflow import RunOptions, run_influence

    snapshot = synthetic_cohort(count=60, seed=5, reference_year=2024)
    run = run_influence(
        workspace=tmp_path,
        snapshot=snapshot,
        options=RunOptions(reference_year=2024, citation_network=False),
    )
    assert run.stages["topics"]["state"] == "complete"
    assert run.stages["topics"]["topics"] > 0
    table = Path(run.package_dir) / "topics.csv"
    assert table.is_file()
    header = table.read_text(encoding="utf-8").splitlines()[0]
    assert "recent_share" in header and "growth" in header
    report = (Path(run.package_dir) / "report.md").read_text(encoding="utf-8")
    assert "研究主题的时间演化" in report
    assert f"近 {RECENT_YEARS} 年占比" in report
