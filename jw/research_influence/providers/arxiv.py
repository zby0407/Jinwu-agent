"""arXiv adapter: the green route that actually matters in astronomy.

Astronomy preprints essentially always exist, so ``match_preprint`` is the step
that decides whether a paywalled paper still gets a readable full text.  A
preprint is *not* the version of record, and the caller must label it as such;
this module therefore reports the version string verbatim instead of implying
equivalence.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ElementTree
from collections.abc import Mapping
from urllib.parse import quote

from ..contracts import ProviderResult, ProviderStatus
from ..transport import Transport
from .base import ProviderPolicy, as_text

__all__ = [
    "API_URL",
    "lookup_ids",
    "normalise_arxiv_id",
    "parse_atom",
    "search_by_title",
]

API_URL = "http://export.arxiv.org/api/query"
_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"
_NEW_ID = re.compile(r"arxiv\.org/abs/(?P<id>[^v]+)(?P<version>v\d+)?", re.IGNORECASE)


def normalise_arxiv_id(raw: str) -> str:
    """Strip URL prefixes and version suffixes from an arXiv identifier."""
    text = as_text(raw)
    if not text:
        return ""
    match = _NEW_ID.search(text)
    if match:
        return match.group("id")
    if text.lower().startswith("arxiv:"):
        text = text[6:]
    return re.sub(r"v\d+$", "", text.strip())


def lookup_ids(
    arxiv_ids: tuple[str, ...],
    *,
    transport: Transport,
    policy: ProviderPolicy,
) -> ProviderResult:
    """Fetch metadata for one or more arXiv identifiers."""
    cleaned = tuple(
        item for item in (normalise_arxiv_id(raw) for raw in arxiv_ids) if item
    )
    if not cleaned:
        return ProviderResult(
            provider="arxiv", status=ProviderStatus.SKIPPED, note="no arxiv id"
        )
    url = f"{API_URL}?id_list={quote(','.join(cleaned), safe='')}&max_results={len(cleaned)}"
    return _fetch_atom(url, transport=transport, policy=policy)


def search_by_title(
    title: str,
    *,
    transport: Transport,
    policy: ProviderPolicy,
    max_results: int = 5,
) -> ProviderResult:
    """Search arXiv by title, used to match a paywalled paper to its preprint."""
    text = " ".join(str(title or "").split())
    if not text:
        return ProviderResult(
            provider="arxiv", status=ProviderStatus.SKIPPED, note="empty title"
        )
    query = quote(f'ti:"{text}"', safe="")
    url = f"{API_URL}?search_query={query}&max_results={max(1, int(max_results))}"
    return _fetch_atom(url, transport=transport, policy=policy)


def _fetch_atom(
    url: str, *, transport: Transport, policy: ProviderPolicy
) -> ProviderResult:
    """Fetch and parse an arXiv Atom response."""
    response = transport.get(
        url,
        headers=policy.headers(),
        timeout=policy.timeout,
        max_bytes=policy.max_bytes,
    )
    if response.error or not response.ok:
        status = (
            ProviderStatus.RATE_LIMITED
            if response.status == 429
            else ProviderStatus.UNAVAILABLE
        )
        return ProviderResult(
            provider="arxiv",
            status=status,
            url=url,
            http_status=response.status,
            note=response.error or f"http_{response.status}",
        )
    body = response.body.decode("utf-8", "replace") if response.body else ""
    entries = parse_atom(body)
    if not entries:
        return ProviderResult(
            provider="arxiv",
            status=ProviderStatus.NOT_FOUND,
            url=url,
            http_status=response.status,
            note="no atom entries",
        )
    return ProviderResult(
        provider="arxiv",
        status=ProviderStatus.OK,
        url=url,
        http_status=response.status,
        payload={"entries": entries},
    )


def parse_atom(body: str) -> tuple[dict[str, object], ...]:
    """Parse an arXiv Atom feed into plain dictionaries."""
    if not body.strip():
        return ()
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return ()
    entries = []
    for entry in root.findall(f"{_ATOM}entry"):
        identifier = as_text(entry.findtext(f"{_ATOM}id"))
        title = " ".join((entry.findtext(f"{_ATOM}title") or "").split())
        abstract = " ".join((entry.findtext(f"{_ATOM}summary") or "").split())
        published = as_text(entry.findtext(f"{_ATOM}published"))
        authors = []
        for position, author in enumerate(entry.findall(f"{_ATOM}author"), 1):
            name = " ".join((author.findtext(f"{_ATOM}name") or "").split())
            if name:
                authors.append({"name": name, "position": position})
        if not identifier or not title:
            continue
        entries.append(
            {
                "arxiv_id": normalise_arxiv_id(identifier),
                "title": title,
                "abstract": abstract,
                "published": published,
                "year": int(published[:4]) if published[:4].isdigit() else None,
                "authors": tuple(authors),
                "pdf_url": _pdf_url(entry, identifier),
                "comment": as_text(entry.findtext(f"{_ARXIV}comment")),
                "journal_reference": as_text(entry.findtext(f"{_ARXIV}journal_ref")),
                "doi": as_text(entry.findtext(f"{_ARXIV}doi")),
            }
        )
    return tuple(entries)


def _pdf_url(entry: ElementTree.Element, identifier: str) -> str:
    """Prefer the feed's own PDF link, else derive it from the identifier."""
    for link in entry.findall(f"{_ATOM}link"):
        if (
            link.attrib.get("title") == "pdf"
            or link.attrib.get("type") == "application/pdf"
        ):
            href = link.attrib.get("href", "")
            if href:
                return href
    bare = normalise_arxiv_id(identifier)
    return f"https://arxiv.org/pdf/{bare}" if bare else ""


def parse_payload(payload: Mapping[str, object]) -> tuple[dict[str, object], ...]:
    """Convenience accessor for the entries inside a provider payload."""
    entries = payload.get("entries") if isinstance(payload, Mapping) else None
    return tuple(item for item in entries or () if isinstance(item, Mapping))
