"""Deterministic identifiers for literature records and distillation focuses."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any

_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)
_ARXIV_ID = re.compile(
    r"(?:arxiv:|arxiv\.org/abs/)?(?P<id>\d{4}\.\d{4,5}|[a-z-]+/\d{7})(?:v(?P<version>\d+))?$",
    re.IGNORECASE,
)
_TRAILING_DOI_PUNCTUATION = ".,;:)]}>"


def normalize_doi(value: Any) -> str:
    """Return a provider-independent lowercase DOI without URL prefixes."""

    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = _DOI_PREFIX.sub("", text).strip().rstrip(_TRAILING_DOI_PUNCTUATION)
    return text.casefold()


def normalize_arxiv_id(value: Any) -> tuple[str, str]:
    """Return ``(base_id, version)`` for an arXiv id or URL."""

    text = str(value or "").strip().rstrip("/")
    text = text.rsplit("/", 1)[-1]
    match = _ARXIV_ID.fullmatch(text)
    if not match:
        return "", ""
    return match.group("id").casefold(), match.group("version") or ""


def normalize_text_key(value: Any) -> str:
    """Normalize human text for deterministic identity comparisons."""

    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(re.findall(r"[\w]+", text, flags=re.UNICODE))


def normalize_focus(value: Any) -> str:
    """Canonical focus string used by the idempotency key."""

    return normalize_text_key(value)


def authors_from_record(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            decoded = [value]
        value = decoded
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def first_author_key(value: Any) -> str:
    authors = authors_from_record(value)
    return normalize_text_key(authors[0]) if authors else ""


def title_author_key(title: Any, authors: Any) -> str:
    """Identity hint that groups updated editions with the same author/title."""

    title_key = normalize_text_key(title)
    author_key = first_author_key(authors)
    return f"{title_key}\x1f{author_key}" if title_key and author_key else ""


def stable_family_id(*, title: Any, authors: Any, doi: Any, source_id: Any) -> str:
    """Create a stable fallback family id for a previously unseen work."""

    identity = title_author_key(title, authors) or normalize_doi(doi) or str(source_id)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return f"litfam_{digest}"


def infer_provider(source_id: Any) -> str:
    text = str(source_id or "")
    return text.split(":", 1)[0].casefold() if ":" in text else "unknown"


def focus_key(value: Any) -> str:
    normalized = normalize_focus(value)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
