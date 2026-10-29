"""OpenAlex adapter: the bibliometric backbone of the pipeline.

OpenAlex is the only free source that gives, in one call, the citation count,
the referenced works, the topic assignment and the open-access locations of a
paper.  That is why it is the *metadata* provider rather than a nice-to-have,
and why the ``select`` list below is longer than the one the literature search
tool uses: the influence features need ``cited_by_count``, ``referenced_works``,
``counts_by_year``, ``topics`` and the full ``locations`` list.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from urllib.parse import quote

from ..contracts import ProviderResult, ProviderStatus, normalise_doi
from ..transport import Transport
from .base import (
    ProviderPolicy,
    abstract_from_inverted_index,
    as_int,
    as_text,
    credential,
    first_list_item,
    json_result,
)

__all__ = [
    "OPENALEX_SELECT",
    "WORK_URL",
    "fetch_work",
    "locations_of",
    "parse_work",
    "search_works",
]

WORK_URL = "https://api.openalex.org/works/"
SEARCH_URL = "https://api.openalex.org/works"

#: Fields the influence pipeline needs; anything else is deliberately not asked
#: for, so the payload stays small and the parsing code stays auditable.
OPENALEX_SELECT = (
    "id,doi,title,display_name,publication_year,publication_date,type,language,"
    "authorships,primary_location,best_oa_location,locations,"
    "abstract_inverted_index,referenced_works,cited_by_count,counts_by_year,"
    "topics,primary_topic,open_access,is_retracted"
)


def fetch_work(
    doi: str,
    *,
    transport: Transport,
    policy: ProviderPolicy,
) -> ProviderResult:
    """Fetch one work by DOI."""
    bare = normalise_doi(doi)
    if not bare:
        return ProviderResult(
            provider="openalex",
            status=ProviderStatus.SKIPPED,
            note="no doi to resolve",
        )
    url = f"{WORK_URL}doi:{quote(bare, safe='')}"
    params = f"?select={OPENALEX_SELECT}"
    if policy.email:
        params += f"&mailto={quote(policy.email, safe='')}"
    key, _ = credential("openalex", policy)
    if key:
        params += f"&api_key={quote(key, safe='')}"
    response = transport.get(
        url + params,
        headers=policy.headers(),
        timeout=policy.timeout,
        max_bytes=policy.max_bytes,
    )
    result = json_result("openalex", url, response)
    if result.ok:
        result.payload = dict(result.payload)
    return result


def search_works(
    query: str,
    *,
    transport: Transport,
    policy: ProviderPolicy,
    per_page: int = 25,
    filter_expression: str = "",
    page: int = 1,
) -> ProviderResult:
    """Search works, used to build cohorts and to match titles.

    ``page`` exists because a cohort larger than one page needs paging, and a
    builder that silently keeps the first page would look complete while being
    a biased sample of whatever OpenAlex ranked highest.
    """
    text = str(query or "").strip()
    if not text:
        return ProviderResult(
            provider="openalex", status=ProviderStatus.SKIPPED, note="empty query"
        )
    params = [
        f"search={quote(text, safe='')}",
        f"per-page={max(1, min(int(per_page or 25), 200))}",
        f"select={OPENALEX_SELECT}",
    ]
    if filter_expression:
        params.append(f"filter={quote(filter_expression, safe='')}")
    if int(page or 1) > 1:
        params.append(f"page={int(page)}")
    if policy.email:
        params.append(f"mailto={quote(policy.email, safe='')}")
    key, _ = credential("openalex", policy)
    if key:
        params.append(f"api_key={quote(key, safe='')}")
    url = f"{SEARCH_URL}?{'&'.join(params)}"
    response = transport.get(
        url,
        headers=policy.headers(),
        timeout=policy.timeout,
        max_bytes=policy.max_bytes,
    )
    return json_result("openalex", url, response)


def parse_work(payload: Mapping[str, object]) -> dict[str, object]:
    """Normalise one OpenAlex work into the fields the kernel consumes."""
    authors = []
    for position, authorship in enumerate(_as_sequence(payload.get("authorships")), 1):
        if not isinstance(authorship, Mapping):
            continue
        author = authorship.get("author")
        author = author if isinstance(author, Mapping) else {}
        institutions = [
            item
            for item in _as_sequence(authorship.get("institutions"))
            if isinstance(item, Mapping)
        ]
        primary_institution = institutions[0] if institutions else {}
        name = as_text(author.get("display_name")) or as_text(
            authorship.get("raw_author_name")
        )
        if not name:
            continue
        countries = [
            str(item) for item in _as_sequence(authorship.get("countries")) if item
        ]
        authors.append(
            {
                "name": name,
                "given": "",
                "family": "",
                "position": as_int(authorship.get("author_position")) or position,
                "orcid": _bare_orcid(author.get("orcid")),
                "institution": as_text(primary_institution.get("display_name")),
                "institution_id": as_text(primary_institution.get("id")),
                "institution_type": as_text(primary_institution.get("type")),
                "country": (
                    as_text(primary_institution.get("country_code"))
                    or (countries[0] if countries else "")
                ),
                "is_corresponding": bool(authorship.get("is_corresponding")),
                "affiliation": as_text(
                    _as_sequence(authorship.get("raw_affiliation_strings"))
                ),
            }
        )
    topics = [
        {
            "id": as_text(item.get("id")),
            "name": as_text(item.get("display_name")),
            "score": float(item.get("score") or 0.0),
            "field": as_text((item.get("field") or {}).get("display_name"))
            if isinstance(item.get("field"), Mapping)
            else "",
        }
        for item in _as_sequence(payload.get("topics"))
        if isinstance(item, Mapping)
    ]
    primary_location = first_list_item(payload.get("primary_location")) or {}
    source = primary_location.get("source")
    source = source if isinstance(source, Mapping) else {}
    open_access = payload.get("open_access")
    open_access = open_access if isinstance(open_access, Mapping) else {}
    return {
        "work_id": as_text(payload.get("id")),
        "doi": normalise_doi(as_text(payload.get("doi"))),
        "title": as_text(payload.get("title")) or as_text(payload.get("display_name")),
        "year": as_int(payload.get("publication_year")),
        "publication_date": as_text(payload.get("publication_date")),
        "type": as_text(payload.get("type")),
        "language": as_text(payload.get("language")),
        "abstract": abstract_from_inverted_index(
            payload.get("abstract_inverted_index")
        ),
        "authors": tuple(authors),
        "venue": {
            "name": as_text(source.get("display_name")),
            "publisher": as_text(source.get("host_organization_name")),
            "issn": tuple(
                str(item) for item in _as_sequence(source.get("issn")) if item
            ),
            "venue_type": as_text(source.get("type")),
            "is_open_access": bool(open_access.get("is_oa")),
        },
        "cited_by_count": as_int(payload.get("cited_by_count")),
        "referenced_works": tuple(
            str(item) for item in _as_sequence(payload.get("referenced_works")) if item
        ),
        "counts_by_year": tuple(
            {
                "year": as_int(item.get("year")),
                "cited_by_count": as_int(item.get("cited_by_count")),
            }
            for item in _as_sequence(payload.get("counts_by_year"))
            if isinstance(item, Mapping)
        ),
        "topics": tuple(topics),
        "oa_status": as_text(open_access.get("oa_status")),
        "is_retracted": bool(payload.get("is_retracted")),
        "locations": locations_of(payload),
    }


def locations_of(payload: Mapping[str, object]) -> tuple[dict[str, object], ...]:
    """Every open-access location, best first, with its classification hints."""
    candidates: list[dict[str, object]] = []
    best = payload.get("best_oa_location")
    if isinstance(best, Mapping):
        candidates.append(_location(best))
    for item in _as_sequence(payload.get("locations")):
        if isinstance(item, Mapping):
            candidates.append(_location(item))
    unique: dict[str, dict[str, object]] = {}
    for item in candidates:
        key = str(item.get("pdf_url") or item.get("landing_url") or "")
        if key and key not in unique:
            unique[key] = item
    return tuple(unique.values())


def _location(raw: Mapping[str, object]) -> dict[str, object]:
    """Normalise one OpenAlex location entry."""
    source = raw.get("source")
    source = source if isinstance(source, Mapping) else {}
    return {
        "pdf_url": as_text(raw.get("pdf_url")),
        "landing_url": as_text(raw.get("landing_page_url")),
        "is_oa": bool(raw.get("is_oa")),
        "license": as_text(raw.get("license")),
        "version": as_text(raw.get("version")),
        "host_type": as_text(raw.get("host_type")),
        "source_name": as_text(source.get("display_name")),
        "source_type": as_text(source.get("type")),
        "publisher": as_text(source.get("host_organization_name")),
    }


def _bare_orcid(raw: object) -> str:
    """Strip the resolver prefix from an ORCID URL."""
    text = as_text(raw)
    return text.rsplit("/", 1)[-1] if text else ""


def _as_sequence(raw: object) -> Sequence:
    """Provider lists may arrive as lists, tuples or nothing at all."""
    if isinstance(raw, (list, tuple)):
        return raw
    return ()
