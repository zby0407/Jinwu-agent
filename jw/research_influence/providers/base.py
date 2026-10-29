"""Provider plumbing shared by every scholarly source adapter.

A provider is a pure function of ``(doi or title, transport, policy)`` and
returns a :class:`~jw.research_influence.contracts.ProviderResult`.  Nothing
here raises on a missing credential or a broken host: an absent key becomes
``ProviderStatus.NOT_CONFIGURED`` with the environment variable named, so the
report can say *why* a paper is metadata-only instead of pretending it is
closed access.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from ..contracts import ProviderResult, ProviderStatus
from ..transport import HttpResponse, Transport

__all__ = [
    "ProviderPolicy",
    "abstract_from_inverted_index",
    "as_int",
    "credential",
    "json_result",
    "select_fields",
]

#: Which environment variable each credentialed provider reads.
CREDENTIAL_ENV: Mapping[str, str] = {
    "ads": "ADS_API_TOKEN",
    "openalex": "OPENALEX_API_KEY",
    "unpaywall": "UNPAYWALL_EMAIL",
    "semantic_scholar": "SEMANTIC_SCHOLAR_API_KEY",
    "core": "CORE_API_KEY",
    "crossref": "CROSSREF_MAILTO",
    "elsevier_tdm": "ELSEVIER_API_KEY",
    "wiley_tdm": "WILEY_TDM_TOKEN",
    "springer_tdm": "SPRINGER_API_KEY",
}

#: Polite-pool contact. Providers ask for an email; we never invent one.
DEFAULT_MAILTO_ENV = "CROSSREF_MAILTO"


@dataclass(slots=True)
class ProviderPolicy:
    """Everything a provider needs to know about how we are allowed to talk."""

    timeout: float = 20.0
    max_bytes: int = 4_000_000
    allow_network: bool = True
    email: str = ""
    overrides: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_environment(cls, *, overrides: Mapping[str, str] | None = None):
        """Build a policy whose contact email comes from the environment."""
        email = (
            os.getenv(DEFAULT_MAILTO_ENV, "").strip()
            or os.getenv("UNPAYWALL_EMAIL", "").strip()
        )
        return cls(email=email, overrides=dict(overrides or {}))

    def credential(self, provider: str) -> str:
        """Resolve one provider's credential, or an empty string."""
        override = self.overrides.get(provider)
        if override:
            return override.strip()
        name = CREDENTIAL_ENV.get(provider, "")
        return os.getenv(name, "").strip() if name else ""

    def headers(self) -> dict[str, str]:
        """Headers every request carries (polite-pool identification)."""
        if self.email:
            return {"From": self.email}
        return {}


def credential(provider: str, policy: ProviderPolicy) -> tuple[str, str]:
    """Return ``(value, env_name)`` for one provider's credential."""
    return policy.credential(provider), CREDENTIAL_ENV.get(provider, "")


def select_fields(payload: Mapping[str, object], names: tuple[str, ...]) -> dict:
    """Project a provider payload onto the fields we actually parse."""
    return {name: payload.get(name) for name in names if name in payload}


def json_result(
    provider: str,
    url: str,
    response: HttpResponse,
    *,
    missing_status: ProviderStatus = ProviderStatus.NOT_FOUND,
) -> ProviderResult:
    """Turn a transport response into a typed provider result."""
    if response.error or not response.ok:
        status = _status_for(response)
        return ProviderResult(
            provider=provider,
            status=status,
            url=url,
            http_status=response.status,
            note=response.error or f"http_{response.status}",
        )
    payload = response.json()
    if payload is None:
        return ProviderResult(
            provider=provider,
            status=ProviderStatus.INVALID_PAYLOAD,
            url=url,
            http_status=response.status,
            note="response was not a JSON object",
        )
    if not payload:
        return ProviderResult(
            provider=provider,
            status=missing_status,
            url=url,
            http_status=response.status,
            note="empty payload",
        )
    return ProviderResult(
        provider=provider,
        status=ProviderStatus.OK,
        url=url,
        http_status=response.status,
        payload=payload,
    )


def _status_for(response: HttpResponse) -> ProviderStatus:
    """Map a transport failure onto the provider vocabulary."""
    if response.status in (401, 403):
        return ProviderStatus.FORBIDDEN
    if response.status == 429:
        return ProviderStatus.RATE_LIMITED
    if response.status == 404:
        return ProviderStatus.NOT_FOUND
    if response.status == 0:
        return ProviderStatus.UNAVAILABLE
    return ProviderStatus.UNAVAILABLE


def abstract_from_inverted_index(index: object) -> str:
    """Rebuild an abstract from OpenAlex's inverted index representation."""
    if not isinstance(index, Mapping):
        return ""
    positions: dict[int, str] = {}
    for word, slots in index.items():
        if not isinstance(slots, (list, tuple)):
            continue
        for slot in slots:
            if isinstance(slot, int):
                positions[slot] = str(word)
    if not positions:
        return ""
    return " ".join(positions[key] for key in sorted(positions))


def as_int(raw: object) -> int | None:
    """Coerce a provider value to ``int`` without inventing numbers."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return None


def as_text(raw: object) -> str:
    """Coerce a provider value to a stripped string."""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, (list, tuple)) and raw:
        first = raw[0]
        return as_text(first)
    return ""


def first_list_item(raw: object) -> Mapping[str, object] | None:
    """First mapping inside a list-ish provider field."""
    if isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, Mapping):
                return item
    if isinstance(raw, Mapping):
        return raw
    return None


def transport_ok(transport: Transport | None) -> bool:
    """Whether a transport was supplied at all."""
    return transport is not None
