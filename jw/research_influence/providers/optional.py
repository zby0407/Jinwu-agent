"""Credentialed and optional providers.

Every function here fails *loudly and honestly* when it cannot run: an unused
provider returns ``NOT_CONFIGURED`` naming the environment variable, never an
empty result that would look like "no such paper".  That distinction is what
lets the report say "we did not look" instead of "nothing was found".

Two deliberate refusals are encoded here:

* **No mirror scraping.**  There is no Sci-Hub / LibGen / shadow-library code
  path anywhere in this package, and none should ever be added.
* **No credential borrowing.**  Publisher TDM and institutional-proxy routes use
  only the *operator's own* credentials, read from the environment.  We never
  ship, store or share a key, and we never guess a publisher's TDM endpoint: the
  licensed link template must be supplied by the operator.
"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import quote

from ..contracts import ProviderResult, ProviderStatus, normalise_doi
from ..transport import Transport
from .base import CREDENTIAL_ENV, ProviderPolicy, as_int, as_text, json_result

__all__ = [
    "S2_URL",
    "ads_lookup",
    "core_lookup",
    "semantic_scholar_lookup",
    "tdm_lookup",
]

S2_URL = "https://api.semanticscholar.org/graph/v1/paper/"
_S2_FIELDS = "title,abstract,year,venue,citationCount,referenceCount,openAccessPdf,externalIds,authors"


def semantic_scholar_lookup(
    doi: str,
    *,
    transport: Transport,
    policy: ProviderPolicy,
) -> ProviderResult:
    """Semantic Scholar citation counts (keyless works, but is rate limited)."""
    bare = normalise_doi(doi)
    if not bare:
        return ProviderResult(
            provider="semantic_scholar",
            status=ProviderStatus.SKIPPED,
            note="no doi to resolve",
        )
    key = policy.credential("semantic_scholar")
    headers = dict(policy.headers())
    if key:
        headers["x-api-key"] = key
    url = f"{S2_URL}DOI:{quote(bare, safe='')}?fields={_S2_FIELDS}"
    response = transport.get(
        url, headers=headers, timeout=policy.timeout, max_bytes=policy.max_bytes
    )
    result = json_result("semantic_scholar", url, response)
    if not key:
        result.note = (result.note + " ").strip() + "unauthenticated_rate_limited"
    return result


def parse_semantic_scholar(payload: Mapping[str, object]) -> dict[str, object]:
    """Normalise a Semantic Scholar paper payload."""
    open_pdf = payload.get("openAccessPdf")
    open_pdf = open_pdf if isinstance(open_pdf, Mapping) else {}
    authors = []
    for position, author in enumerate(payload.get("authors") or (), 1):
        if isinstance(author, Mapping) and as_text(author.get("name")):
            authors.append({"name": as_text(author.get("name")), "position": position})
    return {
        "title": as_text(payload.get("title")),
        "abstract": as_text(payload.get("abstract")),
        "year": as_int(payload.get("year")),
        "venue": as_text(payload.get("venue")),
        "cited_by_count": as_int(payload.get("citationCount")),
        "reference_count": as_int(payload.get("referenceCount")),
        "authors": tuple(authors),
        "pdf_url": as_text(open_pdf.get("url")),
        "doi": normalise_doi(as_text((payload.get("externalIds") or {}).get("DOI")))
        if isinstance(payload.get("externalIds"), Mapping)
        else "",
    }


def core_lookup(
    doi: str,
    *,
    transport: Transport,
    policy: ProviderPolicy,
) -> ProviderResult:
    """CORE repository aggregator; requires CORE_API_KEY."""
    key, env_name = policy.credential("core"), CREDENTIAL_ENV["core"]
    if not key:
        return ProviderResult(
            provider="core",
            status=ProviderStatus.NOT_CONFIGURED,
            note=f"{env_name} is not set, so CORE was not queried",
        )
    bare = normalise_doi(doi)
    if not bare:
        return ProviderResult(
            provider="core", status=ProviderStatus.SKIPPED, note="no doi to resolve"
        )
    url = (
        "https://api.core.ac.uk/v3/search/works"
        f"?q={quote(f'doi:{bare}', safe='')}&limit=1"
    )
    headers = dict(policy.headers())
    headers["Authorization"] = f"Bearer {key}"
    response = transport.get(
        url, headers=headers, timeout=policy.timeout, max_bytes=policy.max_bytes
    )
    return json_result("core", url, response)


def ads_lookup(
    identifier: str,
    *,
    transport: Transport,
    policy: ProviderPolicy,
) -> ProviderResult:
    """NASA ADS; requires ADS_API_TOKEN and is astronomy's citation authority."""
    token, env_name = policy.credential("ads"), CREDENTIAL_ENV["ads"]
    if not token:
        return ProviderResult(
            provider="ads",
            status=ProviderStatus.NOT_CONFIGURED,
            note=f"{env_name} is not set, so ADS was not queried",
        )
    bare = normalise_doi(identifier)
    query = f'doi:"{bare}"' if bare else f"bibcode:{as_text(identifier)}"
    url = "https://api.adsabs.harvard.edu/v1/search/query"
    headers = dict(policy.headers())
    headers["Authorization"] = f"Bearer {token}"
    response = transport.post_json(
        url,
        payload={"q": query, "fl": "bibcode,title,doi,year,citation_count,author"},
        headers=headers,
        timeout=policy.timeout,
        max_bytes=policy.max_bytes,
    )
    return json_result("ads", url, response)


def tdm_lookup(
    publisher: str,
    doi: str,
    *,
    transport: Transport,
    policy: ProviderPolicy,
) -> ProviderResult:
    """Publisher TDM download using the operator's own licensed credentials.

    The licensed link template must be supplied as ``tdm_url:<publisher>`` in the
    policy overrides (for example a Wiley TDM URL).  We never guess it: guessing
    publisher endpoints is how well-meaning tools end up violating a contract.
    """
    name = as_text(publisher).lower() or "unknown"
    credential_name = CREDENTIAL_ENV.get(f"{name}_tdm", "")
    key = policy.credential(f"{name}_tdm") or policy.credential("elsevier_tdm")
    template = as_text(policy.overrides.get(f"tdm_url:{name}"))
    if not template or not key:
        missing = []
        if not template:
            missing.append(f"tdm_url:{name}")
        if not key and credential_name:
            missing.append(credential_name)
        return ProviderResult(
            provider=f"{name}_tdm",
            status=ProviderStatus.NOT_CONFIGURED,
            note=(
                "publisher TDM needs the operator's own licensed access: "
                + ", ".join(missing)
                + " is not set"
            ),
        )
    bare = normalise_doi(doi)
    if not bare:
        return ProviderResult(
            provider=f"{name}_tdm",
            status=ProviderStatus.SKIPPED,
            note="no doi to resolve",
        )
    url = template.replace("{doi}", quote(bare, safe=""))
    headers = dict(policy.headers())
    headers["Authorization"] = f"Bearer {key}"
    response = transport.get(
        url, headers=headers, timeout=policy.timeout, max_bytes=policy.max_bytes
    )
    if response.error or not response.ok:
        status = (
            ProviderStatus.FORBIDDEN
            if response.status in (401, 403)
            else ProviderStatus.UNAVAILABLE
        )
        return ProviderResult(
            provider=f"{name}_tdm",
            status=status,
            url=url,
            http_status=response.status,
            note=response.error or f"http_{response.status}",
        )
    return ProviderResult(
        provider=f"{name}_tdm",
        status=ProviderStatus.OK,
        url=url,
        http_status=response.status,
        payload={"bytes": len(response.body or b"")},
    )
