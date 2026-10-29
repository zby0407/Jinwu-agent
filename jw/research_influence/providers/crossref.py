"""Crossref adapter: publisher-deposited metadata and, when present, references.

Crossref is the fallback when OpenAlex has no record and the only free source
that sometimes ships a machine-readable reference list (``reference``), which
is what makes reference-level analysis possible for papers whose publisher
deposited them.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from html import unescape
from urllib.parse import quote

from ..contracts import ProviderResult, ProviderStatus, normalise_doi
from ..transport import Transport
from .base import ProviderPolicy, as_int, as_text, json_result

__all__ = ["WORK_URL", "fetch_work", "parse_work"]

#: JATS markup used by publishers around abstract content.
_JATS_TAG = re.compile(r"<[^>]+>")

WORK_URL = "https://api.crossref.org/works/"


def fetch_work(
    doi: str,
    *,
    transport: Transport,
    policy: ProviderPolicy,
) -> ProviderResult:
    """Fetch one work by DOI from Crossref."""
    bare = normalise_doi(doi)
    if not bare:
        return ProviderResult(
            provider="crossref", status=ProviderStatus.SKIPPED, note="no doi to resolve"
        )
    url = f"{WORK_URL}{quote(bare, safe='')}"
    if policy.email:
        url += f"?mailto={quote(policy.email, safe='')}"
    response = transport.get(
        url,
        headers=policy.headers(),
        timeout=policy.timeout,
        max_bytes=policy.max_bytes,
    )
    return json_result("crossref", url, response)


def parse_work(payload: Mapping[str, object]) -> dict[str, object]:
    """Normalise one Crossref work into the fields the kernel consumes."""
    message = payload.get("message")
    message = message if isinstance(message, Mapping) else payload
    authors = []
    for position, author in enumerate(_as_sequence(message.get("author")), 1):
        if not isinstance(author, Mapping):
            continue
        name = " ".join(
            part
            for part in (as_text(author.get("given")), as_text(author.get("family")))
            if part
        ) or as_text(author.get("name"))
        if not name:
            continue
        affiliations = [
            as_text(item.get("name"))
            for item in _as_sequence(author.get("affiliation"))
            if isinstance(item, Mapping) and as_text(item.get("name"))
        ]
        authors.append(
            {
                "name": name,
                "given": as_text(author.get("given")),
                "family": as_text(author.get("family")),
                "position": position,
                "orcid": _bare_orcid(author.get("ORCID")),
                "institution": affiliations[0] if affiliations else "",
                "country": "",
                "is_corresponding": bool(author.get("sequence") == "first"),
                "affiliation": "; ".join(affiliations),
            }
        )
    references = []
    for index, reference in enumerate(_as_sequence(message.get("reference")), 1):
        if not isinstance(reference, Mapping):
            continue
        references.append(
            {
                "index": index,
                "raw": as_text(reference.get("unstructured"))
                or as_text(reference.get("article-title")),
                "doi": normalise_doi(as_text(reference.get("DOI"))),
                "title": as_text(reference.get("article-title")),
                "year": as_int(reference.get("year")),
                "venue": as_text(reference.get("journal-title")),
                "authors": as_text(reference.get("author")),
            }
        )
    issued = message.get("issued")
    issued = issued if isinstance(issued, Mapping) else {}
    parts = _as_sequence(issued.get("date-parts"))
    year = None
    if parts and isinstance(parts[0], (list, tuple)) and parts[0]:
        year = as_int(parts[0][0])
    return {
        "doi": normalise_doi(as_text(message.get("DOI"))),
        "title": as_text(_as_sequence(message.get("title"))) or "",
        "abstract": _strip_jats(as_text(message.get("abstract"))),
        "year": year,
        "type": as_text(message.get("type")),
        "language": as_text(message.get("language")),
        "authors": tuple(authors),
        "venue": {
            "name": as_text(_as_sequence(message.get("container-title"))),
            "publisher": as_text(message.get("publisher")),
            "issn": tuple(
                str(item) for item in _as_sequence(message.get("ISSN")) if item
            ),
            "venue_type": as_text(message.get("type")),
            "is_open_access": False,
        },
        "cited_by_count": as_int(message.get("is-referenced-by-count")),
        "reference_count": as_int(message.get("reference-count")),
        "references": tuple(references),
        "is_retracted": bool(message.get("is-retracted")),
        "license": tuple(
            as_text(item.get("URL"))
            for item in _as_sequence(message.get("license"))
            if isinstance(item, Mapping) and as_text(item.get("URL"))
        ),
    }


def _strip_jats(text: str) -> str:
    """Crossref abstracts arrive wrapped in JATS XML tags."""
    if not text:
        return ""
    return " ".join(unescape(_JATS_TAG.sub(" ", text)).split())


def _bare_orcid(raw: object) -> str:
    """Strip the resolver prefix from an ORCID URL."""
    text = as_text(raw)
    return text.rsplit("/", 1)[-1] if text else ""


def _as_sequence(raw: object) -> Sequence:
    """Provider lists may arrive as lists, tuples or nothing at all."""
    if isinstance(raw, (list, tuple)):
        return raw
    return ()
