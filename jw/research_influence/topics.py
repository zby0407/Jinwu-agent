"""Topic evolution: which research programmes the cohort is moving into.

The brief asks for 主题演化, and it is also the honest way to talk about
"topic popularity": a topic that is growing in the reference population will
look like an advantage to any model that can see it, whether or not the science
is better.  Everything here is computed from the frozen snapshot, so it is
reproducible and needs no network.

The labels come from the index (OpenAlex topics), which are assigned by a model.
A "hot topic" is therefore a property of the *labelling*, not of the science; the
report says so rather than dressing the number up as discovery.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from statistics import median

from .contracts import PaperRecord

__all__ = [
    "TopicTrend",
    "paper_topic_position",
    "topic_trends",
    "trend_note",
]

#: How many years count as "recent" when comparing to the rest of the window.
RECENT_YEARS = 3


@dataclass(slots=True)
class TopicTrend:
    """One topic's history inside the reference population."""

    topic: str
    papers: int = 0
    first_year: int | None = None
    last_year: int | None = None
    yearly: tuple[tuple[int, int], ...] = ()
    recent_share: float | None = None
    earlier_share: float | None = None
    growth: float | None = None
    new_topic: bool = False
    anchor_year: int | None = None
    median_citations: float | None = None
    status: str = "ok"
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def rising(self) -> bool:
        """Whether the recent share of the cohort is above the earlier share.

        A topic that appears only in the recent window has no baseline to divide
        by, which is not the same thing as "no change": it is new, and it is
        reported as new rather than as an unmeasurable growth rate.
        """
        if self.new_topic:
            return True
        return self.growth is not None and self.growth > 0

    def to_dict(self) -> Mapping[str, object]:
        """Plain mapping for the manifest and the report."""
        return {
            "topic": self.topic,
            "papers": self.papers,
            "first_year": self.first_year,
            "last_year": self.last_year,
            "recent_share": _round(self.recent_share, 4),
            "earlier_share": _round(self.earlier_share, 4),
            "growth": _round(self.growth),
            "new_topic": self.new_topic,
            "anchor_year": self.anchor_year,
            "median_citations": _round(self.median_citations, 1),
            "status": self.status,
            "notes": list(self.notes),
        }

    def sentence(self) -> str:
        """One checkable sentence."""
        if self.status != "ok":
            return f"主题“{self.topic}”：无法评估（{self.status}）。"
        if self.new_topic:
            change = "更早年份没有样本，属于新出现（相对变化无法计算）"
        else:
            direction = (
                "上升" if self.rising else "下降" if self.growth is not None else "持平"
            )
            change = (
                "相对变化无法计算"
                if self.growth is None
                else f"{direction}（相对变化 {self.growth:+.2f}）"
            )
        median_text = (
            "被引中位数无法计算"
            if self.median_citations is None
            else f"被引中位数 {self.median_citations:.0f}"
        )
        recent = (
            "近三年占比无法计算"
            if self.recent_share is None
            else f"近 {RECENT_YEARS} 年占比 {self.recent_share * 100:.0f}%"
        )
        earlier = (
            "更早占比无法计算"
            if self.earlier_share is None
            else f"更早 {self.earlier_share * 100:.0f}%"
        )
        return (
            f"主题“{self.topic}”：{self.papers} 篇"
            f"（{self.first_year}-{self.last_year}），{recent} 对 {earlier}，"
            f"{change}，{median_text}。"
        )


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(float(value), digits)


def _anchor_year(years: Sequence[int], reference_year: int) -> int:
    """The year the "recent" window ends at.

    Not the maximum year: a snapshot can contain two 2024 papers inside a
    2012-2021 population, and anchoring on them would make every topic look
    extinct.  The anchor is the latest year that actually carries a share of the
    population, so the window compares like with like.
    """
    if not years:
        return reference_year
    counts: dict[int, int] = {}
    for year in years:
        counts[year] = counts.get(year, 0) + 1
    floor = max(3, round(0.01 * len(years)))
    qualifying = [year for year, count in counts.items() if count >= floor]
    return max(qualifying) if qualifying else max(years)


def _share(part: int, total: int) -> float | None:
    return part / total if total else None


def _growth(recent: float | None, earlier: float | None) -> float | None:
    """Relative change of a share, guarded against a zero baseline."""
    if recent is None or earlier is None:
        return None
    if earlier <= 0:
        return None
    return recent / earlier - 1.0


def topic_trends(
    papers: Iterable[PaperRecord],
    *,
    reference_year: int,
    recent_years: int = RECENT_YEARS,
    min_papers: int = 3,
) -> tuple[TopicTrend, ...]:
    """Per-topic history, ordered by size then name.

    A topic with fewer than ``min_papers`` papers is still reported, but its
    shares are marked as too small to read: the honest state for a cell of two.
    """
    items = list(papers)
    # The window is anchored to the latest year actually present in the cohort.
    # A frozen cohort often stops short of the reference year (a 2012-2021
    # snapshot against reference_year=2024), and using the reference year there
    # would put every topic in a "recent" window that contains nothing, making
    # every theme look like it vanished.
    anchor = _anchor_year([paper.year for paper in items if paper.year], reference_year)
    cutoff = anchor - recent_years
    recent_total = sum(1 for paper in items if (paper.year or 0) > cutoff)
    earlier_total = sum(1 for paper in items if paper.year and paper.year <= cutoff)
    grouped: dict[str, list[PaperRecord]] = {}
    for paper in items:
        for topic in dict.fromkeys(paper.keywords):
            grouped.setdefault(str(topic), []).append(paper)
    trends: list[TopicTrend] = []
    for topic, members in grouped.items():
        years = [paper.year for paper in members if paper.year]
        recent = sum(1 for paper in members if (paper.year or 0) > cutoff)
        earlier = sum(1 for paper in members if paper.year and paper.year <= cutoff)
        counts: dict[int, int] = {}
        for year in years:
            counts[year] = counts.get(year, 0) + 1
        citations = [
            paper.cited_by_count
            for paper in members
            if paper.cited_by_count is not None
        ]
        recent_share = _share(recent, recent_total)
        earlier_share = _share(earlier, earlier_total)
        status = "ok" if len(members) >= min_papers else "too_few_papers"
        trends.append(
            TopicTrend(
                topic=topic,
                papers=len(members),
                first_year=min(years) if years else None,
                last_year=max(years) if years else None,
                yearly=tuple(sorted(counts.items())),
                recent_share=recent_share,
                earlier_share=earlier_share,
                growth=_growth(recent_share, earlier_share),
                new_topic=bool(recent) and not earlier,
                anchor_year=anchor,
                median_citations=float(median(citations)) if citations else None,
                status=status,
                notes=() if len(members) >= min_papers else ("fewer_than_min_papers",),
            )
        )
    trends.sort(key=lambda item: (-item.papers, item.topic))
    return tuple(trends)


def paper_topic_position(
    paper: PaperRecord,
    trends: Sequence[TopicTrend],
) -> tuple[Mapping[str, object], ...]:
    """Where this paper's topics sit in the trend table."""
    index = {trend.topic: trend for trend in trends}
    rows: list[Mapping[str, object]] = []
    for topic in paper.keywords:
        trend = index.get(str(topic))
        if trend is None:
            continue
        rows.append(
            {
                "topic": trend.topic,
                "papers": trend.papers,
                "growth": _round(trend.growth),
                "rising": trend.rising,
                "median_citations": _round(trend.median_citations, 1),
                "status": trend.status,
            }
        )
    return tuple(rows)


def trend_note(trends: Sequence[TopicTrend]) -> str:
    """The caveat that has to travel with any "hot topic" claim."""
    if not trends:
        return "本队列没有可用的主题标签，主题演化无法评估。"
    labelled = sum(1 for trend in trends if trend.status == "ok")
    return (
        f"主题标签由索引模型自动分配（共 {len(trends)} 个主题，{labelled} 个样本量足够读占比），"
        "所以“主题在升温”说的是**标签在样本里变多**，不是科学价值变高；"
        "把它当作解释项时必须与主题热度偏差审计一起读。"
    )
