"""What survives when a cohort is rebuilt, measured instead of asserted.

A search-backed cohort is a *moment*: the query is stable, the ranking is not.
The benchmark report used to say so in prose.  A claim in prose cannot be
checked, cannot be re-run, and quietly stops being true when the index changes
behaviour, so this module turns it into numbers with a fingerprint:

* **membership** — how many papers were kept, added and removed;
* **values** — for the papers that survived, how much their own numbers moved
  (citations accumulate between two builds, which is drift too);
* **ranks** — where a survivor sits inside its cohort, because rank-derived
  features are relative to whoever else is in the cohort.

Three limits are deliberate and stated in ``notes`` rather than hidden:

* two records are the same paper when they share a DOI **or** a work id.  A
  paper that gained a DOI between builds is not churn, but a paper with neither
  identifier cannot be matched and shows up as one removal plus one addition;
* a diff measures the *inputs*.  It cannot say the model's output moved; that
  needs a run on each snapshot, which ``infl_run`` already does and pins;
* when the two snapshots answer different questions (other query or other
  reference year) the diff is a comparison of definitions, not drift, and the
  verdict says so instead of computing a churn number nobody should quote.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .cohort import CohortSnapshot
from .contracts import PaperRecord, canonical_json, content_hash, normalise_doi
from .model import spearman

__all__ = [
    "CohortDiff",
    "diff_snapshots",
    "drift_markdown",
    "paper_identity",
]

#: Move of a paper's within-cohort percentile that this module calls "moved".
#: Ten points is one decile: below that the paper kept its place for anyone
#: who thinks in deciles, above it the cohort-relative features changed.
PERCENTILE_MOVE_THRESHOLD = 10.0


def paper_identity(paper: PaperRecord) -> str:
    """One readable label for a paper, used in samples and messages.

    Matching itself uses every identifier a paper has (see :func:`_pair`), so
    this label is for humans, not for equality tests.
    """
    if paper.work_id:
        return f"work:{paper.work_id}"
    if paper.doi:
        return f"doi:{paper.doi}"
    return f"paper:{paper.paper_id}"


@dataclass(slots=True)
class CohortDiff:
    """One measured comparison between two frozen cohorts."""

    left: Mapping[str, Any]
    right: Mapping[str, Any]
    same_question: bool
    elapsed_hours: float | None
    membership: Mapping[str, Any]
    values: Mapping[str, Any]
    ranks: Mapping[str, Any]
    verdict: str
    notes: tuple[str, ...] = ()
    identity_rule: str = "shared_doi_or_work_id"

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON mapping, safe to store next to the snapshots."""
        return {
            "left": dict(self.left),
            "right": dict(self.right),
            "same_question": self.same_question,
            "elapsed_hours": self.elapsed_hours,
            "membership": dict(self.membership),
            "values": dict(self.values),
            "ranks": dict(self.ranks),
            "verdict": self.verdict,
            "notes": list(self.notes),
            "identity_rule": self.identity_rule,
            "fingerprint": self.fingerprint(),
        }

    def fingerprint(self) -> str:
        """Hash of the measured numbers, so a report can pin this diff.

        Both snapshots' fingerprints are inside, so the hash answers "which two
        snapshots produced these churn numbers", not merely "what were the
        numbers".
        """
        return content_hash(
            {
                "left": self.left.get("fingerprint"),
                "right": self.right.get("fingerprint"),
                "membership": _plain(self.membership),
                "values": _plain(self.values),
                "ranks": _plain(self.ranks),
            }
        )

    @property
    def churn_share(self) -> float:
        """Fraction of the older cohort that is absent from the newer one."""
        removed = float(self.membership.get("removed", 0) or 0)
        papers = float(self.membership.get("papers_left", 0) or 0)
        if papers <= 0:
            return 1.0
        return rounded(removed / papers)

    def summary_line(self) -> str:
        """One line a report can quote without shipping the whole table."""
        return (
            f"{self.left.get('cohort_id')} -> {self.right.get('cohort_id')}: "
            f"{self.membership.get('kept')}/{self.membership.get('papers_right')} "
            f"kept, churn {self.churn_share:.0%}, citation median "
            f"{self.values.get('median_citation_delta')}"
        )


def diff_snapshots(
    left: CohortSnapshot,
    right: CohortSnapshot,
    *,
    top_share: float = 0.1,
    sample: int = 5,
) -> CohortDiff:
    """Measure how the second snapshot differs from the first.

    ``left`` is the older build, ``right`` the newer one; "added" and "removed"
    read in that direction.  Nothing here touches the network or the
    filesystem, so the same two files always produce the same numbers.
    """
    left_papers, left_duplicates = _collapse(left)
    right_papers, right_duplicates = _collapse(right)
    pairing = _pair(left_papers, right_papers)
    membership = {
        "papers_left": len(left_papers),
        "papers_right": len(right_papers),
        "kept": len(pairing.kept),
        "added": len(pairing.added),
        "removed": len(pairing.removed),
        "jaccard": (
            rounded(
                len(pairing.kept)
                / (len(pairing.kept) + len(pairing.added) + len(pairing.removed))
            )
            if pairing.kept or pairing.added or pairing.removed
            else 0.0
        ),
        "kept_share_of_left": (
            rounded(len(pairing.kept) / len(left_papers)) if left_papers else 0.0
        ),
        "added_sample": [paper_identity(paper) for paper in pairing.added[:sample]],
        "removed_sample": [paper_identity(paper) for paper in pairing.removed[:sample]],
        "unidentified": pairing.unidentified,
        "ambiguous_matches": pairing.ambiguous,
    }
    values = _value_drift(pairing.kept)
    ranks = _rank_drift(left_papers, right_papers, pairing.kept, top_share=top_share)
    same_question = _same_question(left, right)
    notes = _notes(
        left,
        right,
        left_duplicates=left_duplicates,
        right_duplicates=right_duplicates,
        pairing=pairing,
        same_question=same_question,
    )
    return CohortDiff(
        left=_side(left),
        right=_side(right),
        same_question=same_question,
        elapsed_hours=_elapsed_hours(left.created_at, right.created_at),
        membership=membership,
        values=values,
        ranks=ranks,
        verdict=_verdict(membership, values, ranks, same_question),
        notes=notes,
    )


def drift_markdown(diff: CohortDiff) -> str:
    """Render the comparison as Markdown, ready to paste into a report."""
    membership = diff.membership
    values = diff.values
    ranks = diff.ranks
    lines = [
        "### Cohort drift",
        "",
        f"- older: `{diff.left.get('cohort_id')}` "
        f"({diff.left.get('papers')} records, {membership['papers_left']} distinct,"
        f" {diff.left.get('fingerprint')})",
        f"- newer: `{diff.right.get('cohort_id')}` "
        f"({diff.right.get('papers')} records, {membership['papers_right']} distinct,"
        f" {diff.right.get('fingerprint')})",
        f"- elapsed: {diff.elapsed_hours} hours",
        f"- captured: {diff.left.get('created_at')} -> {diff.right.get('created_at')}",
        "",
        "| 项目 | 值 |",
        "| --- | --- |",
        f"| 保留 / 新增 / 流失 | {membership['kept']} / "
        f"{membership['added']} / {membership['removed']} |",
        f"| Jaccard | {membership['jaccard']} |",
        f"| 流失占旧队列 | {membership['removed']} / {membership['papers_left']}"
        f" = {diff.churn_share:.1%} |",
        f"| 被引变化（保留论文） | 变化 {values['citations_changed']} / "
        f"{values['citation_pairs']} 篇，中位 Δ {values['median_citation_delta']}，"
        f"|Δ| P90 {values['p90_abs_citation_delta']} |",
        f"| 被引总量变化 | {values['total_citation_gain']:+d} |",
        f"| 队列内位次 | 中位移位 {ranks['median_abs_percentile_shift']} 个百分点，"
        f"移位超过 {PERCENTILE_MOVE_THRESHOLD:g} 点 "
        f"{ranks['moved_beyond_threshold']} / {ranks['compared']} 篇 |",
        f"| 前 {ranks['top_share']:.0%} 重叠 | {ranks['top_overlap']} / "
        f"{ranks['left_top']}（{ranks['top_overlap_share']:.1%}） |",
        f"| 被引排序相关 | Spearman {ranks['spearman_citations']} |",
        "",
        f"**判读**：{diff.verdict}",
    ]
    if diff.notes:
        lines.append("")
        for note in diff.notes:
            lines.append(f"- `{note}`")
    lines.append("")
    lines.append(f"_diff fingerprint: `{diff.fingerprint()}`_")
    return "\n".join(lines)


@dataclass(slots=True)
class _Pairing:
    kept: list[tuple[PaperRecord, PaperRecord]]
    added: list[PaperRecord]
    removed: list[PaperRecord]
    unidentified: int
    ambiguous: int


def _collapse(snapshot: CohortSnapshot) -> tuple[list[PaperRecord], int]:
    """Drop records that carry the same identifier inside one snapshot."""
    seen: set[str] = set()
    papers: list[PaperRecord] = []
    duplicates = 0
    for paper in snapshot.papers:
        keys = _keys(paper)
        if keys & seen:
            duplicates += 1
            continue
        seen |= keys
        papers.append(paper)
    return papers, duplicates


def _pair(left: Sequence[PaperRecord], right: Sequence[PaperRecord]) -> _Pairing:
    """Match papers that share any identifier, not just the preferred one.

    A provider that starts reporting a DOI, or re-indexes a record under a new
    work id, changes one identifier without changing the paper.  Joining on the
    union of identifiers is what makes the churn number mean "papers that are
    gone" instead of "records whose spelling changed".
    """
    nodes = [("left", index) for index in range(len(left))]
    nodes += [("right", index) for index in range(len(right))]
    parent = {node: node for node in nodes}

    def find(node: tuple[str, int]) -> tuple[str, int]:
        root = node
        while parent[root] != root:
            root = parent[root]
        while parent[node] != root:
            parent[node], node = root, parent[node]
        return root

    def union(first: tuple[str, int], second: tuple[str, int]) -> None:
        root_first, root_second = find(first), find(second)
        if root_first != root_second:
            parent[root_second] = root_first

    owners: dict[str, tuple[str, int]] = {}
    for side, papers in (("left", left), ("right", right)):
        for index, paper in enumerate(papers):
            for key in _keys(paper):
                node = (side, index)
                if key in owners:
                    union(owners[key], node)
                else:
                    owners[key] = node

    groups: dict[tuple[str, int], dict[str, list[int]]] = {}
    for index in range(len(left)):
        groups.setdefault(find(("left", index)), {"left": [], "right": []})[
            "left"
        ].append(index)
    for index in range(len(right)):
        groups.setdefault(find(("right", index)), {"left": [], "right": []})[
            "right"
        ].append(index)

    kept: list[tuple[PaperRecord, PaperRecord]] = []
    added: list[PaperRecord] = []
    removed: list[PaperRecord] = []
    ambiguous = 0
    for root in sorted(groups):
        group = groups[root]
        lefts = [left[index] for index in sorted(group["left"])]
        rights = [right[index] for index in sorted(group["right"])]
        matched = min(len(lefts), len(rights))
        if matched and (len(lefts) != len(rights)):
            ambiguous += 1
        for position in range(matched):
            kept.append((lefts[position], rights[position]))
        removed.extend(lefts[matched:])
        added.extend(rights[matched:])
    unidentified = sum(1 for paper in (*left, *right) if not _keys(paper))
    return _Pairing(
        kept=kept,
        added=added,
        removed=removed,
        unidentified=unidentified,
        ambiguous=ambiguous,
    )


def _keys(paper: PaperRecord) -> set[str]:
    keys: set[str] = set()
    doi = normalise_doi(paper.doi)
    if doi:
        keys.add(f"doi:{doi}")
    if paper.work_id:
        keys.add(f"work:{paper.work_id}")
    return keys


def _side(snapshot: CohortSnapshot) -> dict[str, Any]:
    return {
        "cohort_id": snapshot.cohort_id,
        "fingerprint": snapshot.fingerprint(),
        "created_at": snapshot.created_at,
        "query": snapshot.query,
        "source": snapshot.source,
        "reference_year": snapshot.reference_year,
        "papers": len(snapshot.papers),
        "paging": str(snapshot.filters.get("paging", "")) or None,
    }


def _value_drift(
    kept: Sequence[tuple[PaperRecord, PaperRecord]],
) -> dict[str, Any]:
    deltas: list[int] = []
    gained = lost = 0
    abstract_gained = abstract_lost = 0
    year_changed = venue_changed = reference_changed = 0
    doi_gained = doi_lost = 0
    for before, after in kept:
        if before.cited_by_count is not None and after.cited_by_count is not None:
            delta = int(after.cited_by_count) - int(before.cited_by_count)
            deltas.append(delta)
            if delta > 0:
                gained += 1
            elif delta < 0:
                lost += 1
        had_abstract = bool(before.abstract.strip())
        has_abstract = bool(after.abstract.strip())
        if has_abstract and not had_abstract:
            abstract_gained += 1
        elif had_abstract and not has_abstract:
            abstract_lost += 1
        had_doi = bool(before.doi)
        has_doi = bool(after.doi)
        if has_doi and not had_doi:
            doi_gained += 1
        elif had_doi and not has_doi:
            doi_lost += 1
        if (
            before.year is not None
            and after.year is not None
            and int(before.year) != int(after.year)
        ):
            year_changed += 1
        if before.venue.name.strip().lower() != after.venue.name.strip().lower():
            venue_changed += 1
        if (
            before.reference_count is not None
            and after.reference_count is not None
            and int(before.reference_count) != int(after.reference_count)
        ):
            reference_changed += 1
    absolute = [abs(delta) for delta in deltas]
    return {
        "citation_pairs": len(deltas),
        "citations_changed": sum(1 for delta in deltas if delta != 0),
        "citations_gained": gained,
        "citations_lost": lost,
        "median_citation_delta": _median([float(delta) for delta in deltas]),
        "median_abs_citation_delta": _median([float(value) for value in absolute]),
        "p90_abs_citation_delta": _percentile(
            [float(value) for value in absolute], 0.9
        ),
        "max_abs_citation_delta": float(max(absolute)) if absolute else 0.0,
        "total_citation_gain": sum(deltas),
        "abstract_gained": abstract_gained,
        "abstract_lost": abstract_lost,
        "doi_gained": doi_gained,
        "doi_lost": doi_lost,
        "year_changed": year_changed,
        "venue_changed": venue_changed,
        "reference_count_changed": reference_changed,
    }


def _rank_drift(
    left_papers: Sequence[PaperRecord],
    right_papers: Sequence[PaperRecord],
    kept: Sequence[tuple[PaperRecord, PaperRecord]],
    *,
    top_share: float,
) -> dict[str, Any]:
    left_percentiles = _percentiles(left_papers)
    right_percentiles = _percentiles(right_papers)
    shifts: list[float] = []
    pairs_left: list[float] = []
    pairs_right: list[float] = []
    for before, after in kept:
        before_rank = left_percentiles.get(id(before))
        after_rank = right_percentiles.get(id(after))
        if before_rank is not None and after_rank is not None:
            shifts.append(abs(after_rank - before_rank))
        if before.cited_by_count is not None and after.cited_by_count is not None:
            pairs_left.append(float(before.cited_by_count))
            pairs_right.append(float(after.cited_by_count))
    left_top = _top_keys(left_percentiles, top_share)
    # Percentiles are keyed by object identity, so the newer side's top set has
    # to be translated through the pairing before it can be intersected.
    right_to_left = {id(after): id(before) for before, after in kept}
    right_top = {
        right_to_left[key]
        for key in _top_keys(right_percentiles, top_share)
        if key in right_to_left
    }
    overlap = left_top & right_top
    correlation = (
        spearman(pairs_left, pairs_right) if len(pairs_left) >= 3 else float("nan")
    )
    return {
        "compared": len(shifts),
        "median_abs_percentile_shift": _median(shifts),
        "p90_abs_percentile_shift": _percentile(shifts, 0.9),
        "moved_beyond_threshold": sum(
            1 for shift in shifts if shift > PERCENTILE_MOVE_THRESHOLD
        ),
        "top_share": top_share,
        "left_top": len(left_top),
        "right_top": len(right_top),
        "top_overlap": len(overlap),
        "top_overlap_share": (
            rounded(len(overlap) / len(left_top)) if left_top else 0.0
        ),
        "entered_top": len(right_top - left_top),
        "dropped_from_top": len(left_top - right_top),
        "right_top_unmatched": sum(
            1
            for key in _top_keys(right_percentiles, top_share)
            if key not in right_to_left
        ),
        "spearman_citations": (
            None if correlation != correlation else rounded(correlation)
        ),
        "percentile_basis": "cited_by_count_average_rank_within_cohort",
    }


def _percentiles(papers: Sequence[PaperRecord]) -> dict[int, float]:
    """Average-rank percentile of ``cited_by_count``, keyed by object identity."""
    values = {
        id(paper): float(paper.cited_by_count)
        for paper in papers
        if paper.cited_by_count is not None
    }
    if not values:
        return {}
    if len(values) == 1:
        return {key: 100.0 for key in values}
    ordered = sorted(values.items(), key=lambda item: item[1])
    out: dict[int, float] = {}
    position = 0
    scale = len(ordered) - 1
    while position < len(ordered):
        end = position
        while end + 1 < len(ordered) and ordered[end + 1][1] == ordered[position][1]:
            end += 1
        # Ties share the midpoint of the positions they occupy: two papers with
        # the same count must not be told they rank differently.
        midpoint = (position + end) / 2.0
        value = 100.0 * midpoint / scale
        for index in range(position, end + 1):
            out[ordered[index][0]] = rounded(value, 1)
        position = end + 1
    return out


def _top_keys(percentiles: Mapping[int, float], top_share: float) -> set[int]:
    if not percentiles:
        return set()
    cut = 100.0 * (1.0 - max(0.0, min(1.0, top_share)))
    selected = {key for key, value in percentiles.items() if value >= cut}
    if selected:
        return selected
    best = max(percentiles.values())
    return {key for key, value in percentiles.items() if value == best}


def _verdict(
    membership: Mapping[str, Any],
    values: Mapping[str, Any],
    ranks: Mapping[str, Any],
    same_question: bool,
) -> str:
    kept = int(membership["kept"])
    papers = int(membership["papers_right"])
    older = int(membership["papers_left"])
    removed = int(membership["removed"])
    added = int(membership["added"])
    if not same_question:
        return (
            "两份快照回答的不是同一个问题（查询或参考年不同），"
            "下面的重叠数只是两个定义的交集，不能当作队列漂移引用。"
        )
    if kept == 0:
        return "两份快照没有任何共同论文：同一个查询两次返回的是互不相交的样本。"
    # Survival of the older cohort is what churn means, so the share is
    # measured against it, not against today's shorter list.
    share = kept / older if older else 0.0
    drift = (
        f"被引中位变化 {values['median_citation_delta']}，"
        f"队列内位次中位移位 {ranks['median_abs_percentile_shift']} 个百分点，"
        f"前 {ranks['top_share']:.0%} 重叠 {ranks['top_overlap_share']:.1%}。"
    )
    if kept == papers == older and added == 0 and removed == 0:
        return f"重抓没有换掉任何论文（{kept}/{papers} 完全一致），这个窗口内队列成员稳定；{drift}"
    if share >= 0.9:
        return (
            f"队列基本稳定（旧队列保留 {share:.1%}，流失 {removed} 篇），但仍有 {drift}"
        )
    if share >= 0.5:
        return (
            f"队列只是部分稳定（保留 {share:.1%}，流失 {removed} 篇、"
            f"新增 {added} 篇），{drift}"
        )
    return (
        f"队列已经不成立（旧队列仅保留 {share:.1%}，流失 {removed} 篇）："
        f"同一查询在不同时刻给出不同样本，任何数字都必须绑定快照指纹。{drift}"
    )


def _notes(
    left: CohortSnapshot,
    right: CohortSnapshot,
    *,
    left_duplicates: int,
    right_duplicates: int,
    pairing: _Pairing,
    same_question: bool,
) -> tuple[str, ...]:
    notes: list[str] = [
        "identity_is_identifier_union: two records are one paper when they share "
        "a DOI or a work id, so a newly indexed identifier is not churn",
        "diff_measures_inputs_not_outputs: model scores are compared by running "
        "each snapshot, not by this module",
    ]
    if not same_question:
        notes.append(
            "different_question: query or reference_year differs, so this is a "
            "comparison of two definitions, not drift of one cohort"
        )
    if left.is_synthetic or right.is_synthetic:
        notes.append(
            "synthetic_snapshot: at least one side is generated, so the numbers "
            "describe the generator, not the literature"
        )
    if left_duplicates or right_duplicates:
        notes.append(
            f"duplicate_identities: {left_duplicates} on the older side and "
            f"{right_duplicates} on the newer side were collapsed to one paper"
        )
    if pairing.unidentified:
        notes.append(
            f"unidentified_papers: {pairing.unidentified} record(s) carry neither "
            "a DOI nor a work id, so they cannot be matched across builds and "
            "read as churn"
        )
    if pairing.ambiguous:
        notes.append(
            f"ambiguous_matches: {pairing.ambiguous} group(s) had a different "
            "number of records on each side; the extra records count as churn"
        )
    if left.filters.get("paging") != right.filters.get("paging"):
        notes.append(
            "paging_mode_differs: the two samples were walked differently, so "
            "part of the difference may be the paging strategy"
        )
    return tuple(notes)


def _same_question(left: CohortSnapshot, right: CohortSnapshot) -> bool:
    return left.query.strip().lower() == right.query.strip().lower() and int(
        left.reference_year
    ) == int(right.reference_year)


def _elapsed_hours(before: str, after: str) -> float | None:
    try:
        first = datetime.fromisoformat(before)
        second = datetime.fromisoformat(after)
    except (TypeError, ValueError):
        return None
    return rounded(abs((second - first).total_seconds()) / 3600.0, 2)


def _plain(payload: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(canonical_json(dict(payload)))


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return rounded(ordered[middle])
    return rounded((ordered[middle - 1] + ordered[middle]) / 2.0)


def _percentile(values: Sequence[float], quantile: float) -> float:
    """Nearest-rank percentile: no interpolation, so no invented precision."""
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return rounded(ordered[0])
    index = int(round(max(0.0, min(1.0, quantile)) * (len(ordered) - 1)))
    return rounded(ordered[index])


def rounded(value: float, digits: int = 4) -> float:
    """Round for JSON: the same float always prints the same way."""
    return round(float(value), digits)
