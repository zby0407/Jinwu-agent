"""Europe PMC adapter: the biomedical mirror, and a legal green route for it.

For astronomy this provider is usually empty, but the acquisition ladder is
domain-agnostic: if a paper does have a Europe PMC record, its
``fullTextUrlList`` is a first-class open-access location and should be used
rather than re-searching arXiv.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from urllib.parse import quote

from ..contracts import ProviderResult, ProviderStatus, normalise_doi
from ..transport import Transport
from .base import ProviderPolicy, as_text, json_result

__all__ = ["SEARCH_URL", "fetch_work", "parse_work"]

SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def fetch_work(
    doi: str,
    *,
    transport: Transport,
    policy: ProviderPolicy,
) -> ProviderResult:
    """Look a DOI up in Europe PMC."""
    bare = normalise_doi(doi)
    if not bare:
        return ProviderResult(
            provider="europepmc",
            status=ProviderStatus.SKIPPED,
            note="no doi to resolve",
        )
    query = quote(f'DOI:"{bare}"', safe="")
    url = f"{SEARCH_URL}?query={query}&format=json&resultType=core"
    response = transport.get(
        url,
        headers=policy.headers(),
        timeout=policy.timeout,
        max_bytes=policy.max_bytes,
    )
    return json_result("europepmc", url, response)


def parse_work(payload: Mapping[str, object]) -> dict[str, object]:
    """Normalise the first Europe PMC hit into location candidates."""
    result_list = payload.get("resultList")
    result_list = result_list if isinstance(result_list, Mapping) else {}
    hits = [
        item
        for item in _as_sequence(result_list.get("result"))
        if isinstance(item, Mapping)
    ]
    if not hits:
        return {"records": 0, "locations": (), "pmcid": "", "is_open_access": False}
    first = hits[0]
    locations = []
    url_list = first.get("fullTextUrlList")
    url_list = url_list if isinstance(url_list, Mapping) else {}
    for item in _as_sequence(url_list.get("fullTextUrl")):
        if not isinstance(item, Mapping):
            continue
        link = as_text(item.get("url"))
        if not link:
            continue
        style = as_text(item.get("documentStyle"))
        locations.append(
            {
                "pdf_url": link if style == "pdf" else "",
                "landing_url": link if style != "pdf" else "",
                "is_oa": as_text(item.get("availability")).lower() == "open access",
                "license": as_text(item.get("license")),
                "version": "publishedVersion",
                "host_type": "repository",
                "source_name": as_text(item.get("site")),
                "source_type": "repository",
                "publisher": "",
                "evidence": f"europepmc:{as_text(item.get('availability'))}",
            }
        )
    return {
        "records": len(hits),
        "pmcid": as_text(first.get("pmcid")),
        "pmid": as_text(first.get("pmid")),
        "is_open_access": as_text(first.get("isOpenAccess")).upper() == "Y",
        "title": as_text(first.get("title")),
        "year": first.get("pubYear"),
        "locations": tuple(locations),
    }


def _as_sequence(raw: object) -> Sequence:
    """Provider lists may arrive as lists, tuples or nothing at all."""
    if isinstance(raw, (list, tuple)):
        return raw
    return ()
