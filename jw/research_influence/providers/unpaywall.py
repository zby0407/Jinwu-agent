"""Unpaywall adapter: the cheapest way to find a legal open copy of a paper.

Unpaywall requires a contact email.  We refuse to invent one: without
``UNPAYWALL_EMAIL`` (or ``CROSSREF_MAILTO``) the provider reports
``NOT_CONFIGURED`` and names the variable, so the acquisition report can say
"we did not look" instead of "there is nothing".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from urllib.parse import quote

from ..contracts import ProviderResult, ProviderStatus, normalise_doi
from ..transport import Transport
from .base import CREDENTIAL_ENV, ProviderPolicy, as_text, json_result

__all__ = ["WORK_URL", "fetch_work", "parse_work"]

WORK_URL = "https://api.unpaywall.org/v2/"


def fetch_work(
    doi: str,
    *,
    transport: Transport,
    policy: ProviderPolicy,
) -> ProviderResult:
    """Fetch the open-access locations Unpaywall knows for a DOI."""
    email, env_name = policy.credential("unpaywall"), CREDENTIAL_ENV["unpaywall"]
    if not email:
        return ProviderResult(
            provider="unpaywall",
            status=ProviderStatus.NOT_CONFIGURED,
            note=f"{env_name} is not set, so open-access locations were not looked up",
        )
    bare = normalise_doi(doi)
    if not bare:
        return ProviderResult(
            provider="unpaywall",
            status=ProviderStatus.SKIPPED,
            note="no doi to resolve",
        )
    url = f"{WORK_URL}{quote(bare, safe='')}?email={quote(email, safe='')}"
    response = transport.get(
        url,
        headers=policy.headers(),
        timeout=policy.timeout,
        max_bytes=policy.max_bytes,
    )
    return json_result("unpaywall", url, response)


def parse_work(payload: Mapping[str, object]) -> dict[str, object]:
    """Normalise an Unpaywall record into location candidates."""
    locations = []
    best = payload.get("best_oa_location")
    if isinstance(best, Mapping):
        locations.append(_location(best))
    for item in _as_sequence(payload.get("oa_locations")):
        if isinstance(item, Mapping):
            locations.append(_location(item))
    unique: dict[str, dict[str, object]] = {}
    for item in locations:
        key = str(item.get("pdf_url") or item.get("landing_url") or "")
        if key and key not in unique:
            unique[key] = item
    return {
        "doi": normalise_doi(as_text(payload.get("doi"))),
        "is_oa": bool(payload.get("is_oa")),
        "oa_status": as_text(payload.get("oa_status")),
        "journal_is_oa": bool(payload.get("journal_is_oa")),
        "published_date": as_text(payload.get("published_date")),
        "year": payload.get("year"),
        "locations": tuple(unique.values()),
    }


def _location(raw: Mapping[str, object]) -> dict[str, object]:
    """Normalise one Unpaywall location entry."""
    return {
        "pdf_url": as_text(raw.get("url_for_pdf")),
        "landing_url": as_text(raw.get("url_for_landing_page")),
        "is_oa": True,
        "license": as_text(raw.get("license")),
        "version": as_text(raw.get("version")),
        "host_type": as_text(raw.get("host_type")),
        "source_name": as_text(raw.get("journal_name")),
        "source_type": "",
        "publisher": as_text(raw.get("journal_name")),
        "evidence": as_text(raw.get("evidence")),
    }


def _as_sequence(raw: object) -> Sequence:
    """Provider lists may arrive as lists, tuples or nothing at all."""
    if isinstance(raw, (list, tuple)):
        return raw
    return ()
