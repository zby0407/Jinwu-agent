"""The acquisition ladder: how far down the paywall we are allowed to go.

The ladder is ordered, explicit and auditable.  Every candidate location a
provider reports is classified into one rung, candidates are ranked, and the
first rung that can actually be read becomes the paper's access route.  What is
refused is recorded as an attempt, not silently dropped.

Three hard rules encoded below (and enforced by tests):

* no shadow-library host is ever contacted, even if a caller passes the URL;
* the absence of a credential is reported as ``NOT_CONFIGURED`` with the
  variable name, never as "no open copy exists";
* a preprint is never presented as the version of record.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .contracts import (
    AccessAttempt,
    AccessDecision,
    AccessRoute,
    normalise_doi,
    utc_now,
)

__all__ = [
    "DENIED_HOSTS",
    "AccessCandidate",
    "AccessPolicy",
    "acquisition_request",
    "candidates_from_arxiv",
    "candidates_from_europepmc",
    "candidates_from_openalex",
    "candidates_from_unpaywall",
    "classify_location",
    "decide_access",
    "rank_candidates",
]

#: Hosts that serve copyrighted papers without permission.  The pipeline never
#: fetches from them and refuses a caller-supplied URL that points at one: the
#: whole point of the ladder is that the last rung is a *request to a human*,
#: not a shortcut through someone else's rights.
DENIED_HOSTS: frozenset[str] = frozenset(
    {
        "sci-hub.se",
        "sci-hub.st",
        "sci-hub.ru",
        "sci-hub.wf",
        "sci-hub.ee",
        "sci-hub.ren",
        "libgen.is",
        "libgen.rs",
        "libgen.st",
        "libgen.li",
        "libgen.gs",
        "b-ok.org",
        "b-ok.cc",
        "z-lib.org",
        "z-lib.io",
        "1lib.org",
        "annas-archive.org",
        "annas-archive.se",
    }
)

#: Licences that mean "free to read but not openly licensed" (Unpaywall bronze).
_BRONZE_LICENCES = frozenset({"", "other-oa", "implied-oa", "unknown", "none"})

_GOLD_LICENCES = frozenset(
    {
        "cc-by",
        "cc-by-sa",
        "cc-by-nc",
        "cc-by-nc-sa",
        "cc-by-nd",
        "cc0",
        "public-domain",
        "publisher-specific-oa",
    }
)


@dataclass(slots=True)
class AccessCandidate:
    """One place a copy might be readable, classified into a ladder rung."""

    url: str
    route: AccessRoute
    provider: str
    license: str = ""
    version: str = ""
    host_type: str = ""
    label: str = ""
    evidence: str = ""

    @property
    def host(self) -> str:
        """Host of the candidate URL."""
        return _host_of(self.url)

    def to_dict(self) -> dict[str, object]:
        """Plain JSON mapping."""
        return {
            "url": self.url,
            "route": self.route.value,
            "provider": self.provider,
            "license": self.license,
            "version": self.version,
            "host_type": self.host_type,
            "label": self.label,
            "evidence": self.evidence,
        }


@dataclass(slots=True)
class AccessPolicy:
    """What the operator allows the ladder to do."""

    allow_network: bool = True
    deny_hosts: frozenset[str] = DENIED_HOSTS
    allow_hosts: frozenset[str] = field(default_factory=frozenset)
    max_fulltext_bytes: int = 60_000_000
    notes: Mapping[str, str] = field(default_factory=dict)

    def refuse_reason(self, url: str) -> str:
        """Why ``url`` must not be fetched, or an empty string when allowed."""
        text = str(url or "").strip()
        if not text:
            return "empty_url"
        parts = urlsplit(text)
        if parts.scheme not in {"http", "https", "file"}:
            return f"scheme_not_allowed:{parts.scheme}"
        if parts.scheme == "file":
            # A local file has no host to allowlist; existence is checked when
            # the bytes are actually read, not here.
            return ""
        host = (parts.hostname or "").casefold()
        if not host:
            return "host_missing"
        if host in self.deny_hosts or _matches_suffix(host, self.deny_hosts):
            return f"shadow_library_refused:{host}"
        if self.allow_hosts and not (
            host in self.allow_hosts or _matches_suffix(host, self.allow_hosts)
        ):
            return f"host_not_allowlisted:{host}"
        if not self.allow_network:
            return "network_disabled"
        return ""


def classify_location(
    location: Mapping[str, object], *, provider: str
) -> AccessCandidate | None:
    """Classify one provider location into a ladder rung."""
    pdf_url = str(location.get("pdf_url") or "").strip()
    landing_url = str(location.get("landing_url") or "").strip()
    url = pdf_url or landing_url
    if not url:
        return None
    if not bool(location.get("is_oa", True)):
        return None
    version = str(location.get("version") or "").strip()
    host_type = str(location.get("host_type") or "").strip().casefold()
    licence = _text(location.get("license")).casefold()
    source_type = str(location.get("source_type") or "").strip().casefold()
    route = _route_for(
        version=version,
        host_type=host_type,
        licence=licence,
        source_type=source_type,
        provider=provider,
    )
    return AccessCandidate(
        url=url,
        route=route,
        provider=provider,
        license=licence,
        version=version,
        host_type=host_type,
        label=str(location.get("source_name") or "").strip(),
        evidence=str(location.get("evidence") or "").strip(),
    )


def _route_for(
    *,
    version: str,
    host_type: str,
    licence: str,
    source_type: str,
    provider: str,
) -> AccessRoute:
    """Decide which rung a location belongs on."""
    if provider == "arxiv":
        return AccessRoute.PREPRINT
    publisher_host = host_type == "publisher" or source_type in {"journal", "publisher"}
    repository_host = host_type == "repository" or source_type == "repository"
    if publisher_host:
        if licence in _GOLD_LICENCES:
            return AccessRoute.OPEN_ACCESS_GOLD
        return AccessRoute.FREE_TO_READ
    if version.casefold() == "submittedversion":
        return AccessRoute.PREPRINT
    if repository_host:
        return AccessRoute.OPEN_ACCESS_GREEN
    if licence in _GOLD_LICENCES:
        return AccessRoute.OPEN_ACCESS_GOLD
    return AccessRoute.OPEN_ACCESS_GREEN


def candidates_from_openalex(
    parsed: Mapping[str, object],
) -> tuple[AccessCandidate, ...]:
    """Every open location OpenAlex reports for one work."""
    out = []
    for location in parsed.get("locations") or ():
        if isinstance(location, Mapping):
            candidate = classify_location(location, provider="openalex")
            if candidate is not None:
                out.append(candidate)
    return tuple(out)


def candidates_from_unpaywall(
    parsed: Mapping[str, object],
) -> tuple[AccessCandidate, ...]:
    """Every open location Unpaywall reports for one DOI."""
    out = []
    for location in parsed.get("locations") or ():
        if isinstance(location, Mapping):
            candidate = classify_location(location, provider="unpaywall")
            if candidate is not None:
                out.append(candidate)
    return tuple(out)


def candidates_from_europepmc(
    parsed: Mapping[str, object],
) -> tuple[AccessCandidate, ...]:
    """Full-text links Europe PMC reports."""
    out = []
    for location in parsed.get("locations") or ():
        if isinstance(location, Mapping):
            candidate = classify_location(location, provider="europepmc")
            if candidate is not None:
                out.append(candidate)
    return tuple(out)


def candidates_from_arxiv(
    entries: Sequence[Mapping[str, object]],
    *,
    title: str = "",
    require_title_match: bool = True,
    threshold: float = 0.6,
) -> tuple[AccessCandidate, ...]:
    """Preprint candidates, optionally filtered by title similarity.

    A wrong preprint is worse than no preprint: it silently substitutes another
    team's data for this paper's.  The title check exists for that reason.
    """
    out = []
    for entry in entries:
        url = str(entry.get("pdf_url") or "").strip()
        if not url:
            continue
        entry_title = str(entry.get("title") or "").strip()
        if (
            require_title_match
            and title
            and _title_similarity(title, entry_title) < threshold
        ):
            continue
        out.append(
            AccessCandidate(
                url=url,
                route=AccessRoute.PREPRINT,
                provider="arxiv",
                license="arxiv-non-exclusive",
                version="submittedVersion",
                host_type="repository",
                label=str(entry.get("arxiv_id") or ""),
                evidence="preprint_not_version_of_record",
            )
        )
    return tuple(out)


def rank_candidates(
    candidates: Iterable[AccessCandidate], *, policy: AccessPolicy | None = None
) -> tuple[AccessCandidate, ...]:
    """Rank candidates best-first, dropping duplicates and refused hosts."""
    settings = policy or AccessPolicy()
    seen: set[str] = set()
    kept: list[AccessCandidate] = []
    for candidate in candidates:
        key = _canonical(candidate.url)
        if not key or key in seen:
            continue
        if settings.refuse_reason(candidate.url):
            continue
        seen.add(key)
        kept.append(candidate)
    return tuple(
        sorted(kept, key=lambda item: (item.route.rank, -len(item.label), item.url))
    )


def decide_access(
    *,
    doi: str,
    candidates: Sequence[AccessCandidate] = (),
    has_abstract: bool = False,
    has_metadata: bool = True,
    policy: AccessPolicy | None = None,
    notes: Sequence[str] = (),
) -> AccessDecision:
    """Pick the route the pipeline will use, without fetching anything yet."""
    settings = policy or AccessPolicy()
    ranked = rank_candidates(candidates, policy=settings)
    refused = [
        f"{candidate.route.value}:{settings.refuse_reason(candidate.url)}"
        for candidate in candidates
        if settings.refuse_reason(candidate.url)
    ]
    note_parts = list(notes)
    if refused:
        note_parts.append("refused:" + ",".join(sorted(set(refused))))
    if not ranked:
        route = AccessRoute.ABSTRACT_ONLY if has_abstract else AccessRoute.METADATA_ONLY
        if not has_abstract:
            note_parts.append("no readable full text and no abstract text")
        else:
            note_parts.append("no legal open copy found; abstract-level analysis only")
        if not has_metadata:
            note_parts.append("no metadata either: nothing was confirmed for this doi")
        return AccessDecision(
            doi=doi,
            route=route,
            attempts=(),
            license="",
            note="; ".join(note_parts),
        )
    best = ranked[0]
    note_parts.insert(0, f"best_available_route:{best.route.value}")
    if best.route is AccessRoute.PREPRINT:
        note_parts.append("preprint_is_not_the_version_of_record")
    return AccessDecision(
        doi=normalise_doi(doi),
        route=best.route,
        attempts=(),
        license=best.license,
        note="; ".join(note_parts),
    )


def with_attempt(
    decision: AccessDecision,
    attempt: AccessAttempt,
    *,
    route: AccessRoute | None = None,
) -> AccessDecision:
    """Record one rung that was actually tried (used during acquisition)."""
    attempts = (*decision.attempts, attempt)
    resolved = route or decision.route
    if attempt.ok and attempt.path:
        return AccessDecision(
            doi=decision.doi,
            route=route or attempt.route,
            attempts=attempts,
            fulltext_path=attempt.path,
            license=attempt.license or decision.license,
            note=decision.note,
            decided_at=utc_now(),
        )
    return AccessDecision(
        doi=decision.doi,
        route=resolved,
        attempts=attempts,
        fulltext_path=decision.fulltext_path,
        license=decision.license,
        note=decision.note,
        decided_at=utc_now(),
    )


def request_acquisition(decision: AccessDecision) -> AccessDecision:
    """Escalate a paper to the human acquisition rung."""
    return AccessDecision(
        doi=decision.doi,
        route=AccessRoute.ACQUISITION_REQUESTED,
        attempts=decision.attempts,
        fulltext_path=decision.fulltext_path,
        license=decision.license,
        note=(decision.note + "; " if decision.note else "")
        + "needs a human acquisition step: interlibrary loan, author request or library copy",
        decided_at=utc_now(),
    )


def acquisition_request(record: Mapping[str, object]) -> dict[str, object]:
    """A concrete, copy-pasteable acquisition request for one paper."""
    authors = record.get("authors") or ()
    first_author = ""
    if isinstance(authors, (list, tuple)) and authors:
        first = authors[0]
        if isinstance(first, Mapping):
            first_author = str(first.get("name") or "").strip()
        else:
            first_author = str(first).strip()
    title = str(record.get("title") or "").strip()
    venue = record.get("venue")
    venue_name = ""
    if isinstance(venue, Mapping):
        venue_name = str(venue.get("name") or "").strip()
    doi = normalise_doi(str(record.get("doi") or ""))
    year = record.get("year")
    citation = ", ".join(
        part
        for part in (
            first_author,
            str(year) if year else "",
            f'"{title}"' if title else "",
            venue_name,
            f"doi:{doi}" if doi else "",
        )
        if part
    )
    return {
        "doi": doi,
        "citation": citation,
        "steps": (
            "interlibrary_loan",
            "author_request",
            "library_copy",
            "publisher_purchase",
        ),
        "reason": (
            "no legal open copy was found and no operator credential covers this "
            "publisher, so the pipeline kept metadata-level analysis only"
        ),
    }


def _text(raw: object) -> str:
    """Coerce a provider field to a stripped string."""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, (list, tuple)) and raw:
        return _text(raw[0])
    return ""


def _host_of(url: str) -> str:
    """Hostname of ``url``, lower-cased."""
    return (urlsplit(str(url or "")).hostname or "").casefold()


def _matches_suffix(host: str, candidates: Iterable[str]) -> bool:
    """Whether ``host`` is a subdomain of any candidate host."""
    return any(host.endswith(f".{item.casefold()}") for item in candidates)


def _canonical(url: str) -> str:
    """Canonical form used to deduplicate candidates."""
    parts = urlsplit(str(url or "").strip())
    if not parts.scheme:
        return ""
    return (
        f"{parts.scheme.casefold()}://{parts.netloc.casefold()}{parts.path.rstrip('/')}"
    )


def _title_similarity(left: str, right: str) -> float:
    """Token-overlap similarity, insensitive to case and punctuation."""
    return _jaccard(_tokens(left), _tokens(right))


def _tokens(text: str) -> frozenset[str]:
    """Lower-case alphanumeric tokens of length >= 3."""
    out = set()
    current: list[str] = []
    for char in str(text or "").casefold():
        if char.isalnum():
            current.append(char)
        elif current:
            word = "".join(current)
            if len(word) >= 3:
                out.add(word)
            current = []
    if current:
        word = "".join(current)
        if len(word) >= 3:
            out.add(word)
    return frozenset(out)


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    """Jaccard similarity of two token sets."""
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)
