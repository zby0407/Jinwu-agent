"""Identify an uploaded paper against an index and fill in what the PDF cannot say.

A text layer tells you the title, roughly the authors, and nothing verifiable
about the journal or the citations.  The scenario in the competition brief is
"the user uploads a paper and the application works out what it is", so the
upload path needs the same enrichment step the search path gets for free: a
lookup that says *this PDF is OpenAlex work W..., published here, cited so many
times*, with the match recorded as evidence rather than assumed.

Two rules make this safe:

* a title-based match must clear :data:`DEFAULT_MIN_SIMILARITY` *and* agree on
  the year within one, otherwise the record is left alone and the attempt is
  reported as ``refused_similarity`` — a wrong match would move a paper into
  the wrong research area and quietly corrupt every comparison made against it;
* enrichment only fills fields that are empty.  The PDF's own text stays
  authoritative for anything read from it, and each filled field carries an
  ``openalex`` provenance entry saying which lookups produced it.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from .contracts import (
    AuthorRecord,
    ConfidenceBand,
    MissingReason,
    PaperRecord,
    Provenance,
    ReferenceRecord,
    VenueRecord,
    utc_now,
)
from .metadata import PdfMetadata
from .providers.base import ProviderPolicy
from .providers.openalex import fetch_work, parse_work, search_works
from .transport import Transport

__all__ = [
    "DEFAULT_MIN_SIMILARITY",
    "Enrichment",
    "apply_metadata",
    "enrich_paper",
    "lookup_work",
    "title_similarity",
]

#: A title match below this is treated as "not the same paper".  Indexed titles
#: differ in case, punctuation and subtitle, not in wording.
DEFAULT_MIN_SIMILARITY = 0.90

#: Years differing by more than this mean two different papers with a shared
#: title, which happens with conference and journal versions of the same work.
YEAR_TOLERANCE = 1

_WORD = re.compile(r"[a-z0-9]+")
_MATCHED_STATUSES = ("enriched_doi", "enriched_arxiv_doi", "enriched_title")


def _tokens(text: str) -> list[str]:
    return _WORD.findall((text or "").casefold())


def title_similarity(left: str, right: str) -> float:
    """Similarity of two titles in ``[0, 1]``.

    Character similarity alone punishes subtitle differences ("...: II. The
    survey" against "..."), so the word-set F1 is averaged with the character
    ratio and the better of the two is returned.
    """
    a = _tokens(left)
    b = _tokens(right)
    if not a or not b:
        return 0.0
    shared = set(a) & set(b)
    precision = len(shared) / len(set(a))
    recall = len(shared) / len(set(b))
    f1 = (
        0.0
        if precision + recall == 0
        else 2 * precision * recall / (precision + recall)
    )
    ratio = difflib.SequenceMatcher(None, " ".join(a), " ".join(b)).ratio()
    return max(f1, ratio)


@dataclass(slots=True)
class Enrichment:
    """The result of trying to identify one paper against an index."""

    paper: PaperRecord
    status: str
    method: str = ""
    work_id: str = ""
    similarity: float = 0.0
    filled: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    candidates: int = 0

    @property
    def matched(self) -> bool:
        """Whether the paper was identified and at least one field filled."""
        return self.status in _MATCHED_STATUSES

    def to_dict(self) -> dict[str, Any]:
        """Plain mapping for manifests."""
        return {
            "status": self.status,
            "method": self.method,
            "work_id": self.work_id,
            "similarity": round(self.similarity, 4),
            "filled": list(self.filled),
            "candidates": self.candidates,
            "notes": list(self.notes),
        }


def _prov(method: str, *, confidence: ConfidenceBand, url: str = "") -> Provenance:
    return Provenance(
        provider="openalex",
        route="openalex",
        method=method,
        url=url,
        retrieved_at=utc_now(),
        sha256="",
        license="",
        confidence=confidence,
        locator=None,
        note=f"enrichment_match:{method}",
    )


def lookup_work(
    *,
    paper: PaperRecord,
    transport: Transport,
    policy: ProviderPolicy,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
    title_search: bool = True,
) -> tuple[dict[str, object] | None, str, float, int, tuple[str, ...]]:
    """Find the indexed work behind a paper.

    Returns ``(work, status, similarity, candidates, notes)``.  ``work`` is
    ``None`` whenever nothing trustworthy was found, and the status says why.
    """
    notes: list[str] = []
    if paper.doi:
        result = fetch_work(paper.doi, transport=transport, policy=policy)
        if result.ok and isinstance(result.payload, Mapping):
            return parse_work(result.payload), "enriched_doi", 1.0, 1, tuple(notes)
        notes.append(f"doi_lookup_failed:{result.status.value}")
    metadata = extract_pdf_metadata_from(paper)
    if metadata.arxiv_doi and metadata.arxiv_doi.casefold() != paper.doi.casefold():
        result = fetch_work(metadata.arxiv_doi, transport=transport, policy=policy)
        if result.ok and isinstance(result.payload, Mapping):
            return (
                parse_work(result.payload),
                "enriched_arxiv_doi",
                1.0,
                1,
                tuple(notes),
            )
        notes.append(f"arxiv_doi_lookup_failed:{result.status.value}")
    if not title_search or not paper.title:
        notes.append("no_identifier_and_title_search_disabled")
        return None, "not_found", 0.0, 0, tuple(notes)
    result = search_works(paper.title, transport=transport, policy=policy, per_page=5)
    if not result.ok:
        notes.append(f"title_search_failed:{result.status.value}")
        return None, result.status.value, 0.0, 0, tuple(notes)
    payload = result.payload if isinstance(result.payload, Mapping) else {}
    results = payload.get("results")
    candidates = (
        [item for item in results if isinstance(item, Mapping)]
        if isinstance(results, list)
        else []
    )
    best: dict[str, object] | None = None
    best_score = 0.0
    for candidate in candidates:
        parsed = parse_work(candidate)
        score = title_similarity(paper.title, str(parsed.get("title") or ""))
        parsed_year = parsed.get("year")
        if (
            paper.year
            and isinstance(parsed_year, int)
            and abs(paper.year - parsed_year) > YEAR_TOLERANCE
        ):
            notes.append(f"year_mismatch_skipped:{parsed_year}")
            score -= 0.5
        if score > best_score:
            best, best_score = parsed, score
    if best is None or best_score < min_similarity:
        notes.append(f"best_title_similarity:{best_score:.3f}")
        return (
            None,
            "refused_similarity" if best is not None else "not_found",
            best_score,
            len(candidates),
            tuple(notes),
        )
    return best, "enriched_title", best_score, len(candidates), tuple(notes)


def extract_pdf_metadata_from(paper: PaperRecord) -> PdfMetadata:
    """Metadata already recovered for a paper, when the caller kept it.

    The workflow stores the recovered metadata in ``notes`` as
    ``pdf_metadata:<json>``; when it is absent the function returns an empty
    :class:`PdfMetadata` rather than re-reading a file that may be gone.
    """
    for note in paper.notes:
        if note.startswith("pdf_metadata:"):
            import json

            try:
                payload = json.loads(note.split(":", 1)[1])
            except ValueError:
                return PdfMetadata()
            if not isinstance(payload, Mapping):
                return PdfMetadata()
            return PdfMetadata(
                title=str(payload.get("title") or ""),
                authors=tuple(str(item) for item in payload.get("authors") or ()),
                year=payload.get("year")
                if isinstance(payload.get("year"), int)
                else None,
                venue=str(payload.get("venue") or ""),
                doi=str(payload.get("doi") or ""),
                arxiv_id=str(payload.get("arxiv_id") or ""),
            )
    return PdfMetadata()


def _authors_from_work(
    work: Mapping[str, object], *, method: str
) -> tuple[AuthorRecord, ...]:
    raw = work.get("authors")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    out: list[AuthorRecord] = []
    for position, entry in enumerate(
        (item for item in raw if isinstance(item, Mapping)), 1
    ):
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        out.append(
            AuthorRecord(
                name=name,
                given=str(entry.get("given") or ""),
                family=str(entry.get("family") or ""),
                position=position,
                orcid=str(entry.get("orcid") or ""),
                institution=str(entry.get("institution") or ""),
                country=str(entry.get("country") or ""),
                is_corresponding=bool(entry.get("is_corresponding")),
                provenance=(_prov(method, confidence=ConfidenceBand.HIGH),),
            )
        )
    return tuple(out)


def _venue_from_work(work: Mapping[str, object], *, method: str) -> VenueRecord:
    raw = work.get("venue")
    entry = raw if isinstance(raw, Mapping) else {}
    return VenueRecord(
        name=str(entry.get("name") or ""),
        venue_type=str(entry.get("venue_type") or ""),
        publisher=str(entry.get("publisher") or ""),
        is_open_access=entry.get("is_open_access")
        if isinstance(entry.get("is_open_access"), bool)
        else None,
        provenance=(_prov(method, confidence=ConfidenceBand.HIGH),),
    )


def _references_from_work(
    work: Mapping[str, object], *, method: str
) -> tuple[ReferenceRecord, ...]:
    raw = work.get("referenced_works")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    return tuple(
        ReferenceRecord(
            index=index,
            raw=str(work_id),
            resolved_work_id=str(work_id),
            match_confidence=ConfidenceBand.HIGH,
            provenance=(_prov(method, confidence=ConfidenceBand.MEDIUM),),
        )
        for index, work_id in enumerate(
            (item for item in raw if isinstance(item, str)), 1
        )
    )


def _counts_from_work(work: Mapping[str, object]) -> tuple[tuple[int, int], ...]:
    raw = work.get("counts_by_year")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    out: list[tuple[int, int]] = []
    for item in raw:
        if isinstance(item, Mapping):
            year = item.get("year")
            count = item.get("cited_by_count")
            if isinstance(year, int) and isinstance(count, int):
                out.append((year, count))
        elif (
            isinstance(item, Sequence)
            and not isinstance(item, (str, bytes))
            and len(item) == 2
        ):
            year, count = item
            if isinstance(year, int) and isinstance(count, int):
                out.append((year, count))
    return tuple(sorted(out))


def apply_metadata(
    paper: PaperRecord, work: Mapping[str, object], *, method: str
) -> tuple[PaperRecord, tuple[str, ...]]:
    """Fill a paper's empty fields from an indexed work, without overwriting.

    Returns the new record and the names of the fields that were filled.  The
    PDF stays authoritative for title, abstract and sections: an index entry
    can carry a different subtitle, and rewriting text the user can see in
    their own file would make the report disagree with the document.
    """
    filled: list[str] = []
    provenance = {key: tuple(value) for key, value in paper.provenance.items()}
    missing = dict(paper.missing)
    changes: dict[str, Any] = {}

    work_title = str(work.get("title") or "")
    if not paper.title and work_title:
        changes["title"] = work_title
        provenance["title"] = (_prov(method, confidence=ConfidenceBand.HIGH),)
        filled.append("title")
        missing.pop("title", None)

    if not paper.abstract:
        abstract = str(work.get("abstract") or "")
        if abstract:
            changes["abstract"] = abstract
            provenance["abstract"] = (_prov(method, confidence=ConfidenceBand.HIGH),)
            filled.append("abstract")
            missing.pop("abstract", None)

    work_year = work.get("year")
    if paper.year is None and isinstance(work_year, int):
        changes["year"] = work_year
        provenance["year"] = (_prov(method, confidence=ConfidenceBand.HIGH),)
        filled.append("year")
        missing.pop("year", None)

    if not paper.doi:
        doi = str(work.get("doi") or "")
        if doi:
            changes["doi"] = doi
            provenance["doi"] = (_prov(method, confidence=ConfidenceBand.HIGH),)
            filled.append("doi")
            missing.pop("doi", None)

    if not paper.authors:
        authors = _authors_from_work(work, method=method)
        if authors:
            changes["authors"] = authors
            provenance["authors"] = (_prov(method, confidence=ConfidenceBand.HIGH),)
            filled.append("authors")
            missing.pop("authors", None)

    if not paper.venue.name:
        venue = _venue_from_work(work, method=method)
        if venue.name:
            changes["venue"] = venue
            provenance["venue"] = (_prov(method, confidence=ConfidenceBand.HIGH),)
            filled.append("venue")
            missing.pop("venue", None)

    if not paper.references:
        references = _references_from_work(work, method=method)
        if references:
            changes["references"] = references
            changes["reference_count"] = len(references)
            provenance["references"] = (
                _prov(method, confidence=ConfidenceBand.MEDIUM),
            )
            filled.append("references")
            missing.pop("references", None)

    if paper.cited_by_count is None:
        cited = work.get("cited_by_count")
        if isinstance(cited, int):
            changes["cited_by_count"] = cited
            filled.append("cited_by_count")

    if not paper.counts_by_year:
        counts = _counts_from_work(work)
        if counts:
            changes["counts_by_year"] = counts
            filled.append("counts_by_year")

    if not paper.keywords:
        raw = work.get("topics")
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            keywords = tuple(
                name
                for name in (
                    str(item.get("name") or "")
                    if isinstance(item, Mapping)
                    else str(item)
                    for item in raw
                )
                if name.strip()
            )
            if keywords:
                changes["keywords"] = keywords
                filled.append("keywords")

    if not paper.work_id:
        work_id = str(work.get("work_id") or "")
        if work_id:
            changes["work_id"] = work_id
            filled.append("work_id")

    if not filled:
        return paper, ()
    changes["provenance"] = provenance
    changes["missing"] = missing
    changes["notes"] = (
        *paper.notes,
        f"enriched_from_openalex:{method}",
        f"enriched_fields:{','.join(filled)}",
    )
    return replace(paper, **changes), tuple(filled)


def enrich_paper(
    paper: PaperRecord,
    *,
    transport: Transport,
    policy: ProviderPolicy,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
    title_search: bool = True,
) -> Enrichment:
    """Identify a paper and fill its empty bibliographic fields."""
    work, status, similarity, candidates, notes = lookup_work(
        paper=paper,
        transport=transport,
        policy=policy,
        min_similarity=min_similarity,
        title_search=title_search,
    )
    if work is None:
        return Enrichment(
            paper=paper,
            status=status,
            similarity=similarity,
            candidates=candidates,
            notes=(*notes, "paper_left_unchanged"),
        )
    enriched, filled = apply_metadata(paper, work, method=status)
    if not filled:
        notes = (*notes, "index_had_nothing_the_pdf_lacks")
    return Enrichment(
        paper=enriched,
        status=status,
        method=status,
        work_id=str(work.get("work_id") or ""),
        similarity=similarity,
        filled=filled,
        candidates=candidates,
        notes=notes,
    )


def unresolved_fields(paper: PaperRecord) -> tuple[str, ...]:
    """Core fields still missing after enrichment, for honest reporting."""
    return tuple(
        sorted(
            key
            for key, reason in paper.missing.items()
            if reason is MissingReason.NOT_REPORTED
        )
    )


@dataclass(slots=True)
class EnrichmentBatch:
    """Enrichment for a list of uploads, with the counts a report can quote."""

    results: tuple[Enrichment, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def papers(self) -> tuple[PaperRecord, ...]:
        """The papers after enrichment."""
        return tuple(result.paper for result in self.results)

    def counts(self) -> dict[str, int]:
        """Status histogram, including ``matched`` and ``unchanged``."""
        out: dict[str, int] = {
            "uploads": len(self.results),
            "matched": 0,
            "unchanged": 0,
        }
        for result in self.results:
            out[result.status] = out.get(result.status, 0) + 1
            out["matched" if result.matched else "unchanged"] += 1
        return out

    def to_dict(self) -> dict[str, Any]:
        """Plain mapping for manifests."""
        return {
            "counts": self.counts(),
            "results": [result.to_dict() for result in self.results],
            "notes": list(self.notes),
        }
