"""The citation network around a paper: who it cites, who cites it, and where it sits.

The competition brief lists "引用网络" as one of the things the application must
be able to parse, and it matters for the explanation layer as well: a paper with
twelve citations that are all in the top decile of their field is a different
object from a paper with twelve citations from papers nobody reads.  Neither
fact is visible in the citation count itself.

Three measurements come out of here:

* **reference quality** — the citation counts of the works a paper cites, as a
  median and as a share in the top decile of everything we resolved.  A high
  share means the paper is plugged into a well-cited neighbourhood, which is
  part of why some papers accumulate citations quickly.
* **incoming attention** — who cites the paper, how well cited they are, and
  how much of that is self-citation by the same authors.
* **network position** — PageRank over the reference graph of the cohort.  It
  is computed here with numpy rather than pulled from a network library, so the
  number is reproducible from the stored snapshot alone.

Everything is best-effort and offline-first: when the network is unavailable the
functions return empty results with a status, never a guessed number.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .contracts import PaperRecord, normalise_doi
from .providers.base import ProviderPolicy, as_int, as_text
from .transport import Transport

__all__ = [
    "CitationProfile",
    "citation_profile",
    "citing_works",
    "cohort_pagerank",
    "pagerank",
    "percentile_of",
    "reference_quality",
    "resolve_works",
    "self_citation_count",
]

WORKS_URL = "https://api.openalex.org/works"

#: Only what the network needs: identifiers, the year and the citation count.
NETWORK_SELECT = (
    "id,doi,title,publication_year,cited_by_count,referenced_works,authorships"
)

#: A single ``filter=id:A|B|C`` request resolves this many ids at once.  Larger
#: batches get truncated by the API, which would silently bias the resolution.
RESOLVE_CHUNK = 25

#: How many pages of citing works to walk.  One page is enough to characterise
#: the neighbourhood; walking further turns a report into a crawl.
MAX_CITING_PAGES = 2

_BARE_WORK_ID = re.compile(r"(?:https?://openalex\.org/)?(W\d+)$")
_BARE_AUTHOR = re.compile(r"[^\w\u00c0-\u024f]+")


@dataclass(slots=True)
class CitationProfile:
    """What the citation network says about one paper, with its limits."""

    paper_id: str = ""
    work_id: str = ""
    reference_count: int = 0
    resolved_references: int = 0
    mean_referenced_citations: float | None = None
    median_referenced_citations: float | None = None
    references_in_top_decile: float | None = None
    citing_count: int | None = None
    citing_sampled: int = 0
    mean_citing_citations: float | None = None
    self_citations: int = 0
    pagerank: float | None = None
    pagerank_percentile: float | None = None
    status: str = "not_requested"
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def usable(self) -> bool:
        """Whether any network measurement was actually obtained."""
        return self.resolved_references > 0 or self.citing_sampled > 0

    def to_dict(self) -> dict[str, Any]:
        """Plain mapping for manifests and the report."""
        return {
            "paper_id": self.paper_id,
            "work_id": self.work_id,
            "reference_count": self.reference_count,
            "resolved_references": self.resolved_references,
            "mean_referenced_citations": _rounded(self.mean_referenced_citations),
            "median_referenced_citations": _rounded(self.median_referenced_citations),
            "references_in_top_decile": _rounded(self.references_in_top_decile),
            "citing_count": self.citing_count,
            "citing_sampled": self.citing_sampled,
            "mean_citing_citations": _rounded(self.mean_citing_citations),
            "self_citations": self.self_citations,
            "pagerank": _rounded(self.pagerank, 6),
            "pagerank_percentile": _rounded(self.pagerank_percentile),
            "status": self.status,
            "notes": list(self.notes),
        }

    def summary_lines(self) -> list[str]:
        """Sentences a reader can check against the numbers above."""
        if not self.usable:
            return [
                f"引用网络：未取得数据（status={self.status}）。"
                "这里缺的不是「没有引用」，而是「我们没能读到引用网络」，"
                "两者在结论里必须区分。"
            ]
        lines: list[str] = []
        if self.resolved_references:
            source = (
                "索引给出的参考文献"
                if "reference_list_from_index" in self.notes
                else f"{self.reference_count} 条参考文献"
            )
            lines.append(
                f"引用网络（向内）：{source}中解析出 {self.resolved_references} 条，"
                f"被引次数中位数 {self.median_referenced_citations:.1f}；"
                f"{self.references_in_top_decile * 100:.0f}% 的参考文献本身位于本队列被引前 10%。"
            )
        if not self.resolved_references and self.reference_count:
            lines.append(
                f"引用网络（向内）：该论文的 {self.reference_count} 条参考文献一条也没能"
                "解析成可核算的作品（索引没有它的参考文献列表，或 id 对不上），"
                "所以向内指标为空——空白表示未测到，不表示它不引用好文献。"
            )
        if self.citing_sampled:
            lines.append(
                f"引用网络（向外）：已有 {self.citing_count} 篇论文引用它，抽样 {self.citing_sampled} 篇，"
                f"引用者被引次数均值 {self.mean_citing_citations:.1f}，其中自引 {self.self_citations} 篇。"
            )
        if self.pagerank_percentile is not None:
            lines.append(
                f"网络位置：参考文献图上的 PageRank 位于本队列第 {self.pagerank_percentile:.1f} 百分位。"
            )
        return lines


def _rounded(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(float(value), digits)


def bare_work_id(value: str) -> str:
    """``https://openalex.org/W123`` and ``W123`` mean the same work."""
    match = _BARE_WORK_ID.match(str(value).strip())
    return match.group(1) if match else ""


def _parse_network_work(payload: Mapping[str, object]) -> dict[str, object]:
    authorships = payload.get("authorships")
    names: list[str] = []
    if isinstance(authorships, Sequence) and not isinstance(authorships, (str, bytes)):
        for authorship in authorships:
            if not isinstance(authorship, Mapping):
                continue
            author = authorship.get("author")
            name = (
                as_text(author.get("display_name"))
                if isinstance(author, Mapping)
                else ""
            )
            if name:
                names.append(name)
    return {
        "work_id": bare_work_id(as_text(payload.get("id"))),
        "doi": normalise_doi(as_text(payload.get("doi"))),
        "title": as_text(payload.get("title")),
        "year": as_int(payload.get("publication_year")),
        "cited_by_count": as_int(payload.get("cited_by_count")) or 0,
        "referenced_works": tuple(
            bare_work_id(str(item))
            for item in payload.get("referenced_works") or ()
            if bare_work_id(str(item))
        ),
        "authors": tuple(names),
    }


def _paged(
    filter_expression: str,
    *,
    transport: Transport,
    policy: ProviderPolicy,
    per_page: int = 50,
    max_pages: int = 1,
) -> tuple[list[dict[str, object]], int]:
    """Walk one filtered query and return the parsed works plus the total count."""
    works: list[dict[str, object]] = []
    total = 0
    for page in range(1, max_pages + 1):
        url = (
            f"{WORKS_URL}?filter={filter_expression}"
            f"&per-page={per_page}&page={page}&select={NETWORK_SELECT}"
        )
        if policy.email:
            url = f"{url}&mailto={policy.email}"
        response = transport.get(
            url,
            headers=policy.headers(),
            timeout=policy.timeout,
            max_bytes=policy.max_bytes,
        )
        if not response.ok:
            return works, total
        try:
            payload = response.json()
        except ValueError:
            return works, total
        if not isinstance(payload, Mapping):
            return works, total
        meta = payload.get("meta")
        if isinstance(meta, Mapping):
            total = as_int(meta.get("count")) or total
        results = payload.get("results")
        if not isinstance(results, Sequence) or isinstance(results, (str, bytes)):
            break
        batch = [item for item in results if isinstance(item, Mapping)]
        works.extend(_parse_network_work(item) for item in batch)
        if len(batch) < per_page:
            break
    return works, total


def resolve_works(
    work_ids: Iterable[str],
    *,
    transport: Transport,
    policy: ProviderPolicy,
) -> dict[str, dict[str, object]]:
    """Look up many works at once, keyed by bare work id *or* by DOI.

    Mixed input is fine: ``W123`` style ids go through ``filter=id:`` and
    anything else is treated as a DOI and normalised first.  Unresolved ids are
    simply absent: a reference we could not resolve is a missing measurement,
    not a zero-citation work.
    """
    bare_ids: list[str] = []
    dois: list[str] = []
    for item in work_ids:
        text = str(item).strip()
        if not text:
            continue
        bare = bare_work_id(text)
        if bare:
            bare_ids.append(bare)
        else:
            doi = normalise_doi(text)
            if doi:
                dois.append(doi)
    resolved: dict[str, dict[str, object]] = {}
    # ``id:`` is not a filter field in the current API; ``ids.openalex:`` is.
    for field_name, values in (("ids.openalex", bare_ids), ("doi", dois)):
        unique = list(dict.fromkeys(values))
        for start in range(0, len(unique), RESOLVE_CHUNK):
            chunk = unique[start : start + RESOLVE_CHUNK]
            works, _total = _paged(
                f"{field_name}:{'|'.join(chunk)}",
                transport=transport,
                policy=policy,
                per_page=len(chunk),
            )
            for work in works:
                key = str(work.get("work_id") or "")
                if key:
                    resolved[key] = work
    return resolved


def citing_works(
    work_id: str,
    *,
    transport: Transport,
    policy: ProviderPolicy,
    max_pages: int = MAX_CITING_PAGES,
    per_page: int = 50,
) -> tuple[tuple[dict[str, object], ...], int, str]:
    """The works citing this one, the total count, and a status string."""
    bare = bare_work_id(work_id)
    if not bare:
        return (), 0, "no_work_id"
    works, total = _paged(
        f"cites:{bare}",
        transport=transport,
        policy=policy,
        per_page=per_page,
        max_pages=max_pages,
    )
    if not works and not total:
        return (), 0, "unavailable"
    return tuple(works), total, "ok"


def _top_decile_threshold(values: Sequence[int]) -> float:
    """The citation count that starts the top tenth of the pool.

    The index is floored rather than rounded so that a small pool still yields a
    usable threshold: with four works the tenth is the second-highest, not the
    highest (rounding would make the share always zero, which would read as "no
    paper cites good work" when the truth is "the pool is tiny").
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, int(0.9 * (len(ordered) - 1)))
    return float(ordered[index])


def reference_quality(
    referenced: Sequence[Mapping[str, object]],
    *,
    percentile_pool: Sequence[int] = (),
) -> tuple[float | None, float | None, float | None]:
    """Mean, median and top-decile share of the citation counts a paper cites."""
    counts = [int(work.get("cited_by_count") or 0) for work in referenced]
    if not counts:
        return None, None, None
    mean = float(np.mean(counts))
    median = float(np.median(counts))
    pool = list(percentile_pool) or counts
    threshold = _top_decile_threshold(pool)
    share = float(sum(1 for value in counts if value >= threshold) / len(counts))
    return mean, median, share


def author_surnames(paper: PaperRecord) -> set[str]:
    """Lower-case surname tokens of a paper's authors."""
    out: set[str] = set()
    for author in paper.authors:
        name = (author.family or author.name or "").strip()
        if not name:
            continue
        parts = [part for part in _BARE_AUTHOR.split(name.casefold()) if len(part) > 2]
        if parts:
            out.add(parts[-1])
    return out


def self_citation_count(
    paper: PaperRecord, citing: Sequence[Mapping[str, object]]
) -> int:
    """Citing works that share at least one author surname with the paper.

    This is a lower bound: surnames collide, initials are not compared, and a
    citing work whose author list we did not fetch cannot be counted at all.
    """
    own = author_surnames(paper)
    if not own:
        return 0
    hits = 0
    for work in citing:
        names = work.get("authors")
        if not isinstance(names, Sequence) or isinstance(names, (str, bytes)):
            continue
        surnames = {
            parts[-1]
            for parts in (
                [
                    part
                    for part in _BARE_AUTHOR.split(str(name).casefold())
                    if len(part) > 2
                ]
                for name in names
            )
            if parts
        }
        if own & surnames:
            hits += 1
    return hits


def pagerank(
    nodes: Sequence[str],
    edges: Iterable[tuple[str, str]],
    *,
    damping: float = 0.85,
    iterations: int = 100,
    tolerance: float = 1e-12,
) -> dict[str, float]:
    """PageRank over a directed graph, weighted by nothing but structure.

    ``edges`` are ``(from, to)`` pairs meaning "from cites to".  The walk follows
    citations, so mass flows towards the papers everything else cites.
    """
    index = {node: position for position, node in enumerate(nodes)}
    size = len(index)
    if size == 0:
        return {}
    matrix = np.zeros((size, size), dtype=float)
    for source, target in edges:
        if source in index and target in index and source != target:
            matrix[index[source], index[target]] = 1.0
    out_degree = matrix.sum(axis=1)
    dangling = out_degree == 0
    with np.errstate(divide="ignore", invalid="ignore"):
        transition = np.where(
            out_degree[:, None] > 0, matrix / out_degree[:, None], 0.0
        )
    rank = np.full(size, 1.0 / size)
    teleport = np.full(size, 1.0 / size)
    for _ in range(iterations):
        updated = (
            damping * (transition.T @ rank) + damping * rank[dangling].sum() * teleport
        )
        updated += (1.0 - damping) * teleport
        if np.abs(updated - rank).sum() < tolerance:
            rank = updated
            break
        rank = updated
    total = rank.sum()
    if total > 0:
        rank = rank / total
    return {node: float(rank[position]) for node, position in index.items()}


def percentile_of(values: Sequence[float], value: float) -> float:
    """Share of ``values`` at or below ``value``, in percent."""
    if not values:
        return 0.0
    ordered = sorted(values)
    below = sum(1 for item in ordered if item <= value)
    return round(100.0 * below / len(ordered), 1)


def cohort_pagerank(
    papers: Sequence[PaperRecord],
    resolved: Mapping[str, Mapping[str, object]],
) -> dict[str, float]:
    """PageRank over the reference graph induced by the cohort.

    Only works we resolved become sources, so the graph is the *observed* part
    of the citation network; a paper whose references resolved poorly will sit
    lower than its real position, and that bias is reported, not corrected.
    """
    nodes: list[str] = []
    edges: list[tuple[str, str]] = []
    for paper in papers:
        source = bare_work_id(paper.work_id)
        if not source or source not in resolved:
            continue
        nodes.append(source)
        referenced = resolved[source].get("referenced_works") or ()
        if isinstance(referenced, Sequence) and not isinstance(
            referenced, (str, bytes)
        ):
            edges.extend((source, str(target)) for target in referenced)
    edges = [(source, target) for source, target in edges if target in resolved]
    for _source, target in edges:
        nodes.append(target)
    ordered = list(dict.fromkeys(nodes))
    return pagerank(ordered, edges)


def citation_profile(
    paper: PaperRecord,
    *,
    resolved: Mapping[str, Mapping[str, object]],
    referenced_ids: Sequence[str] = (),
    citing: Sequence[Mapping[str, object]] = (),
    citing_total: int | None = None,
    rank: float | None = None,
    rank_pool: Sequence[float] = (),
    status: str = "ok",
) -> CitationProfile:
    """Assemble one paper's network measurements from already-fetched data."""
    work_id = bare_work_id(paper.work_id)
    # The index knows which works a paper cites; the parsed reference list is
    # free text and mostly cannot be turned into work ids (an uploaded PDF gives
    # us "M. Sullivan et al. 2011, ApJ 732, 118", not a DOI).  When the index
    # answers we prefer its list, and we say so rather than pretending the two
    # lists are the same thing.
    wanted = [bare_work_id(item) for item in referenced_ids]
    wanted = [item for item in wanted if item]
    if wanted:
        referenced = [resolved[item] for item in wanted if item in resolved]
        note_prefix: tuple[str, ...] = (
            ("reference_resolution_partial",) if len(referenced) < len(wanted) else ()
        )
        note_extra: tuple[str, ...] = ("reference_list_from_index",)
    else:
        referenced = [
            resolved[target]
            for target in (
                bare_work_id(item.resolved_work_id or item.raw)
                for item in paper.references
            )
            if target and target in resolved
        ]
        note_prefix = (
            ("reference_resolution_partial",)
            if len(referenced) < len(paper.references)
            else ()
        )
        note_extra = ()
    pool = [int(work.get("cited_by_count") or 0) for work in resolved.values()]
    mean, median, share = reference_quality(referenced, percentile_pool=pool)
    citing_counts = [int(work.get("cited_by_count") or 0) for work in citing]
    return CitationProfile(
        paper_id=paper.paper_id,
        work_id=work_id,
        reference_count=len(paper.references),
        resolved_references=len(referenced),
        mean_referenced_citations=mean,
        median_referenced_citations=median,
        references_in_top_decile=share,
        citing_count=citing_total,
        citing_sampled=len(citing),
        mean_citing_citations=float(np.mean(citing_counts)) if citing_counts else None,
        self_citations=self_citation_count(paper, citing),
        pagerank=rank,
        pagerank_percentile=(
            percentile_of(rank_pool, rank)
            if rank is not None and rank_pool and referenced
            else None
        ),
        status=status,
        notes=(
            *note_prefix,
            *note_extra,
            # A paper whose references all failed to resolve sits in the graph as
            # an isolated node: its PageRank is whatever the dangling mass gives
            # it, so a percentile computed from that is scenery, not position.
            *(
                ("pagerank_percentile_skipped_unlinked",)
                if rank is not None and rank_pool and not referenced
                else ()
            ),
        ),
    )
