"""Enrich a frozen cohort's references from Crossref deposits.

OpenAlex tells us *which* works a paper cites (``referenced_works``) but not
what those works are called.  Crossref often carries the publisher-deposited
reference list, including titles and DOIs, which is what turns an opaque id into
a reference a reader can check.

The network half is deliberately a separate, cached pass: a snapshot is taken
once, enriched once, and then every model run stays offline and reproducible.
"""

from __future__ import annotations

import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Callable

from .paper import apply_crossref_references, needs_crossref_references
from .providers.base import ProviderPolicy
from .providers.crossref import fetch_work, parse_work
from .contracts import PaperRecord, normalise_doi
from .transport import Transport

__all__ = ["EnrichmentReport", "enrich_references", "readable_reference_count"]


@dataclass(slots=True)
class EnrichmentReport:
    """Counts a reader can compare with the cohort they started from."""

    papers: int = 0
    papers_with_doi: int = 0
    eligible: int = 0
    attempted: int = 0
    cached: int = 0
    fetched: int = 0
    enriched: int = 0
    no_references: int = 0
    not_found: int = 0
    rate_limited: int = 0
    unavailable: int = 0
    references_before: int = 0
    references_after: int = 0
    readable_before: int = 0
    readable_after: int = 0
    statuses: Counter[str] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "papers": self.papers,
            "papers_with_doi": self.papers_with_doi,
            "eligible": self.eligible,
            "attempted": self.attempted,
            "cached": self.cached,
            "fetched": self.fetched,
            "enriched": self.enriched,
            "no_references": self.no_references,
            "not_found": self.not_found,
            "rate_limited": self.rate_limited,
            "unavailable": self.unavailable,
            "references_before": self.references_before,
            "references_after": self.references_after,
            "readable_before": self.readable_before,
            "readable_after": self.readable_after,
            "readable_share_before": round(
                self.readable_before / self.references_before, 4
            )
            if self.references_before
            else None,
            "readable_share_after": round(
                self.readable_after / self.references_after, 4
            )
            if self.references_after
            else None,
            "statuses": dict(self.statuses or {}),
        }


def readable_reference_count(paper: PaperRecord) -> int:
    """References a reader can actually identify by DOI or title."""
    return sum(1 for entry in paper.references if entry.doi or entry.title)


def _load_cache(path: Path | None) -> dict[str, dict[str, object]]:
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_cache(path: Path | None, cache: dict[str, dict[str, object]]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _fetch_one(
    paper: PaperRecord,
    *,
    cache: dict[str, dict[str, object]],
    cache_path: Path | None,
    cache_lock: Lock,
    transport: Transport,
    policy: ProviderPolicy,
) -> tuple[PaperRecord, str]:
    """Fetch one paper's Crossref deposit and apply it when it has references."""
    doi = normalise_doi(paper.doi)
    if not doi:
        return paper, "no_doi"
    if not needs_crossref_references(paper):
        return paper, "already_readable"
    with cache_lock:
        cached = cache.get(doi)
    if isinstance(cached, dict) and cached.get("status") in {
        "ok",
        "not_found",
        "no_references",
    }:
        raw = cached.get("references") or []
        if cached.get("status") == "not_found":
            return paper, "not_found_cached"
        crossref = {"references": raw}
        enriched, count = apply_crossref_references(paper, crossref)
        return enriched, "enriched_cached" if count else "no_references_cached"

    result = fetch_work(doi, transport=transport, policy=policy)
    if not result.ok:
        if result.status.value == "not_found":
            with cache_lock:
                cache[doi] = {"status": "not_found", "references": []}
                _write_cache(cache_path, cache)
        return paper, (result.status.value or "unavailable")
    parsed = parse_work(result.payload or {})
    raw = list(parsed.get("references") or ())
    if not raw:
        with cache_lock:
            cache[doi] = {"status": "no_references", "references": []}
            _write_cache(cache_path, cache)
        return paper, "no_references"
    with cache_lock:
        cache[doi] = {"status": "ok", "references": raw}
        _write_cache(cache_path, cache)
    enriched, count = apply_crossref_references(paper, parsed)
    return enriched, "enriched" if count else "no_references"


def enrich_references(
    papers: list[PaperRecord] | tuple[PaperRecord, ...],
    *,
    transport_factory: Callable[[], Transport],
    policy: ProviderPolicy,
    cache_path: str | Path | None = None,
    max_papers: int = 0,
    workers: int = 4,
) -> tuple[tuple[PaperRecord, ...], EnrichmentReport]:
    """Enrich id-only reference lists from Crossref, with a reusable cache.

    Args:
        papers: Frozen cohort papers.
        transport_factory: Builds one transport per worker; tests pass a fake.
        policy: Provider policy (contact email, timeout, credentials).
        cache_path: JSON cache keyed by DOI; reuse across runs.
        max_papers: Stop after this many eligible papers (0 means all).
        workers: Bounded concurrency; Crossref asks for polite use.

    Returns:
        Papers in their original order and a report with before/after counts.
    """
    cache_file = Path(cache_path) if cache_path else None
    cache = _load_cache(cache_file)
    cache_lock = Lock()
    report = EnrichmentReport(papers=len(papers), statuses=Counter())
    eligible_indices: list[int] = []
    for index, paper in enumerate(papers):
        report.references_before += len(paper.references)
        report.readable_before += readable_reference_count(paper)
        if paper.doi:
            report.papers_with_doi += 1
        if needs_crossref_references(paper):
            eligible_indices.append(index)
    report.eligible = len(eligible_indices)
    if max_papers > 0:
        eligible_indices = eligible_indices[:max_papers]

    results: list[PaperRecord] = list(papers)
    if not eligible_indices:
        return tuple(results), report

    def work(index: int) -> tuple[int, PaperRecord, str]:
        paper, status = _fetch_one(
            papers[index],
            cache=cache,
            cache_path=cache_file,
            cache_lock=cache_lock,
            transport=transport_factory(),
            policy=policy,
        )
        return index, paper, status

    if workers <= 1 or len(eligible_indices) == 1:
        completed = [work(index) for index in eligible_indices]
    else:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            completed = list(pool.map(work, eligible_indices))

    for index, paper, status in completed:
        results[index] = paper
        report.statuses[status] += 1
        report.attempted += 1
        if status in {"enriched", "enriched_cached"}:
            report.enriched += 1
        if status.endswith("cached"):
            report.cached += 1
        elif status in {"enriched", "no_references"}:
            report.fetched += 1
        if status in {"no_references", "no_references_cached"}:
            report.no_references += 1
        if status in {"not_found", "not_found_cached"}:
            report.not_found += 1
        if status == "rate_limited":
            report.rate_limited += 1
        if status not in {
            "enriched",
            "enriched_cached",
            "no_references",
            "no_references_cached",
            "not_found",
            "not_found_cached",
            "already_readable",
            "no_doi",
        }:
            report.unavailable += 1

    report.references_after = sum(len(paper.references) for paper in results)
    report.readable_after = sum(readable_reference_count(paper) for paper in results)
    return tuple(results), report
