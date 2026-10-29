"""Shared contracts for the Research Influence kernel.

This module is the single source of truth for everything the influence
pipeline exchanges: the paper record that a PDF or a metadata provider
produces, the author/venue/reference/open-data evidence beside it, the access
ladder that decides how much of a paper we were allowed to read, and the
provenance that ties every field back to a page, a section or an API response.

Design rules that every stage must honour:

* **Access is reported, never disguised.**  A paper is read through one of the
  :class:`AccessRoute` levels, and the route is carried on the record and in
  every report.  A missing full text is a typed absence, not a silent gap.
* **Every field is sourced.**  Scalar fields point at their
  :class:`Provenance`; a field with no source is missing with a
  :class:`MissingReason` instead of being invented.
* **Abstract-level analysis is legitimate.**  Metadata features work without
  the full text, so a paywalled paper is analysed at reduced strength rather
  than dropped -- and that reduction is measured, not hidden.
* **No circumvention.**  The ladder stops at what the publisher, the
  repository or the user's own entitlement allows; there is no Sci-Hub rung
  and no borrowed credential.
* **Everything is hash-bound.**  Records freeze their inputs so a rerun on the
  same snapshot produces the same numbers.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "jw.research-influence/v1"

__all__ = [
    "SCHEMA_VERSION",
    "AccessAttempt",
    "AccessDecision",
    "AccessRoute",
    "AuthorRecord",
    "ConfidenceBand",
    "Locator",
    "MissingReason",
    "OpenDataRecord",
    "PaperRecord",
    "Provenance",
    "ProviderResult",
    "ProviderStatus",
    "ReferenceRecord",
    "VenueRecord",
    "canonical_json",
    "content_hash",
    "file_digest",
    "merge_records",
    "new_run_id",
    "normalise_doi",
    "summarise",
    "utc_now",
]


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def utc_now() -> str:
    """Current UTC timestamp in second-precision ISO-8601."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def new_run_id(prefix: str = "infl") -> str:
    """Sortable run identifier: ``{prefix}-{UTC stamp}-{digest prefix}``."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    seed = f"{prefix}:{stamp}:{id(object())}"
    return f"{prefix}-{stamp}-{hashlib.sha256(seed.encode()).hexdigest()[:8]}"


def canonical_json(payload: Any) -> str:
    """Deterministic JSON encoding used for hashing and manifests."""
    return json.dumps(
        _jsonable(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def content_hash(payload: Any) -> str:
    """``sha256:`` prefixed digest of any JSON-serialisable payload."""
    return (
        "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    )


def file_digest(path: str | Path) -> str:
    """``sha256:`` digest of a file's bytes; empty string when unreadable."""
    try:
        return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""


#: Fields that record *when* something happened, not *what* it said.  They stay
#: in the artefacts but never enter a fingerprint.
VOLATILE_KEYS: frozenset[str] = frozenset(
    {"retrieved_at", "created_at", "decided_at", "generated_at", "saved_at"}
)

#: Fields holding *where a copy sits on this machine*, not what the paper says.
#: The content digest is what identifies the payload, so two runs in different
#: work directories still hash the same paper.
LOCAL_PATH_KEYS: frozenset[str] = frozenset(
    {"fulltext_path", "source_paths", "path", "directory"}
)


def _stable_payload(payload: Any) -> Any:
    """Recursively drop volatile timestamps so a hash can be reproduced."""
    if isinstance(payload, Mapping):
        return {
            str(key): _stable_payload(value)
            for key, value in payload.items()
            if str(key) not in VOLATILE_KEYS and str(key) not in LOCAL_PATH_KEYS
        }
    if isinstance(payload, (list, tuple)):
        return [_stable_payload(item) for item in payload]
    return _jsonable(payload)


def normalise_doi(raw: str) -> str:
    """Lower-case bare DOI, stripping resolver prefixes and stray whitespace."""
    text = str(raw or "").strip()
    lowered = text.casefold()
    for prefix in (
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "doi:",
    ):
        if lowered.startswith(prefix):
            text = text[len(prefix) :]
            break
    return text.strip().strip(".").casefold()


def _jsonable(payload: Any) -> Any:
    """Recursively convert dataclasses/enums into plain JSON structures."""
    if isinstance(payload, Enum):
        return payload.value
    if isinstance(payload, Mapping):
        return {str(key): _jsonable(value) for key, value in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [_jsonable(item) for item in payload]
    if dataclasses.is_dataclass(payload) and not isinstance(payload, type):
        return _jsonable(asdict(payload))
    if isinstance(payload, datetime):
        return payload.isoformat()
    if isinstance(payload, (str, int, float, bool)) or payload is None:
        return payload
    return str(payload)


def _coerce_enum(enum_type: type[Enum], raw: Any, default: Any) -> Any:
    """Parse ``raw`` into ``enum_type``, falling back to ``default``."""
    if isinstance(raw, enum_type):
        return raw
    if raw is None:
        return default
    try:
        return enum_type(str(raw))
    except ValueError:
        return default


def _as_position(raw: Any) -> int:
    """Author order as a 1-based ordinal, tolerating words from older snapshots."""
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, int):
        return raw
    text = str(raw or "").strip().casefold()
    if not text:
        return 0
    known = {"first": 1, "middle": 2, "last": -1, "corresponding": 1}
    if text in known:
        return known[text]
    return int(text) if text.isdigit() else 0


def _provenance_tuple(raw: Any) -> tuple[Provenance, ...]:
    """Rebuild a provenance tuple from stored dicts."""
    items: list[Provenance] = []
    for item in raw or ():
        if isinstance(item, Provenance):
            items.append(item)
        elif isinstance(item, Mapping):
            items.append(Provenance.from_dict(item))
    return tuple(items)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AccessRoute(StrEnum):
    """How much of a paper the pipeline was allowed to read.

    The order below *is* the ladder: earlier rungs are preferred because they
    give the version of record, later rungs are honest degradations.  Nothing
    here circumvents a paywall -- that is a deliberate omission, not a gap.
    """

    USER_SUPPLIED = "user_supplied"
    OPEN_ACCESS_GOLD = "open_access_gold"
    OPEN_ACCESS_GREEN = "open_access_green"
    FREE_TO_READ = "free_to_read"
    PREPRINT = "preprint"
    PUBLISHER_TDM = "publisher_tdm"
    INSTITUTIONAL_PROXY = "institutional_proxy"
    ABSTRACT_ONLY = "abstract_only"
    METADATA_ONLY = "metadata_only"
    ACQUISITION_REQUESTED = "acquisition_requested"

    @property
    def rank(self) -> int:
        """Position on the ladder; lower is better."""
        return _ROUTE_ORDER.index(self)

    @property
    def has_full_text(self) -> bool:
        """Whether this route is expected to yield readable full text."""
        return self in _FULL_TEXT_ROUTES

    @property
    def has_abstract(self) -> bool:
        """Whether this route still supports abstract-level analysis."""
        return self.rank <= AccessRoute.ABSTRACT_ONLY.rank

    @property
    def needs_human(self) -> bool:
        """Whether the only remaining step is a human acquisition request."""
        return self is AccessRoute.ACQUISITION_REQUESTED

    @classmethod
    def ladder(cls) -> tuple[AccessRoute, ...]:
        """Every route in preference order."""
        return _ROUTE_ORDER


_ROUTE_ORDER: tuple[AccessRoute, ...] = (
    AccessRoute.USER_SUPPLIED,
    AccessRoute.OPEN_ACCESS_GOLD,
    AccessRoute.OPEN_ACCESS_GREEN,
    AccessRoute.FREE_TO_READ,
    AccessRoute.PREPRINT,
    AccessRoute.PUBLISHER_TDM,
    AccessRoute.INSTITUTIONAL_PROXY,
    AccessRoute.ABSTRACT_ONLY,
    AccessRoute.METADATA_ONLY,
    AccessRoute.ACQUISITION_REQUESTED,
)

_FULL_TEXT_ROUTES = frozenset(
    {
        AccessRoute.USER_SUPPLIED,
        AccessRoute.OPEN_ACCESS_GOLD,
        AccessRoute.OPEN_ACCESS_GREEN,
        AccessRoute.FREE_TO_READ,
        AccessRoute.PREPRINT,
        AccessRoute.PUBLISHER_TDM,
        AccessRoute.INSTITUTIONAL_PROXY,
    }
)


class ProviderStatus(StrEnum):
    """Outcome of one provider call, including the honest failures."""

    OK = "ok"
    NOT_CONFIGURED = "not_configured"
    NOT_FOUND = "not_found"
    FORBIDDEN = "forbidden"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    INVALID_PAYLOAD = "invalid_payload"
    SKIPPED = "skipped"

    @property
    def usable(self) -> bool:
        """Whether the payload can be trusted as an answer."""
        return self is ProviderStatus.OK


class MissingReason(StrEnum):
    """Why a value is absent; a bare ``None`` is never a statement."""

    NOT_REPORTED = "not_reported"
    NOT_PARSED = "not_parsed"
    NOT_APPLICABLE = "not_applicable"
    NOT_FOUND = "not_found"
    FULL_TEXT_UNAVAILABLE = "full_text_unavailable"
    NOT_CONFIGURED = "not_configured"
    NOT_INDEXED = "not_indexed"
    UNDETECTED = "undetected"


class ConfidenceBand(StrEnum):
    """Coarse confidence for one extracted fact."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNVERIFIED = "unverified"


class OpenDataKind(StrEnum):
    """What an open-data statement points at."""

    DATA = "data"
    CODE = "code"
    SUPPLEMENTARY = "supplementary"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class Locator:
    """Where in a document or a payload a fact came from."""

    page: int | None = None
    page_label: str = ""
    section: str = ""
    char_start: int = -1
    char_end: int = -1
    line: int | None = None
    table: str = ""
    field: str = ""

    @property
    def usable(self) -> bool:
        """Whether the locator names something a reader could go and check."""
        return bool(self.section or self.page is not None or self.char_start >= 0)

    def describe(self) -> str:
        """Short human-readable position, used in reports."""
        parts: list[str] = []
        if self.section:
            parts.append(self.section)
        if self.page is not None:
            parts.append(f"p{self.page + 1}")
        if self.line is not None:
            parts.append(f"line {self.line}")
        if self.char_start >= 0:
            parts.append(f"chars {self.char_start}-{self.char_end}")
        if self.field:
            parts.append(f"field {self.field}")
        return ", ".join(parts) or "unspecified"

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON mapping."""
        return {
            "page": self.page,
            "page_label": self.page_label,
            "section": self.section,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "line": self.line,
            "table": self.table,
            "field": self.field,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> Locator:
        """Rebuild from :meth:`to_dict` output."""
        data = dict(raw or {})
        page = data.get("page")
        line = data.get("line")
        return cls(
            page=int(page) if isinstance(page, int) else None,
            page_label=str(data.get("page_label") or ""),
            section=str(data.get("section") or ""),
            char_start=int(data.get("char_start", -1)),
            char_end=int(data.get("char_end", -1)),
            line=int(line) if isinstance(line, int) else None,
            table=str(data.get("table") or ""),
            field=str(data.get("field") or ""),
        )


@dataclass(slots=True, frozen=True)
class Provenance:
    """One fact's origin: provider, route, locator and confidence."""

    provider: str
    route: AccessRoute
    method: str
    url: str = ""
    retrieved_at: str = ""
    sha256: str = ""
    license: str = ""
    confidence: ConfidenceBand = ConfidenceBand.UNVERIFIED
    locator: Locator | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON mapping."""
        return {
            "provider": self.provider,
            "route": self.route.value,
            "method": self.method,
            "url": self.url,
            "retrieved_at": self.retrieved_at,
            "sha256": self.sha256,
            "license": self.license,
            "confidence": self.confidence.value,
            "locator": self.locator.to_dict() if self.locator else None,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Provenance:
        """Rebuild from :meth:`to_dict` output."""
        locator = raw.get("locator")
        return cls(
            provider=str(raw.get("provider") or ""),
            route=_coerce_enum(
                AccessRoute, raw.get("route"), AccessRoute.METADATA_ONLY
            ),
            method=str(raw.get("method") or ""),
            url=str(raw.get("url") or ""),
            retrieved_at=str(raw.get("retrieved_at") or ""),
            sha256=str(raw.get("sha256") or ""),
            license=str(raw.get("license") or ""),
            confidence=_coerce_enum(
                ConfidenceBand, raw.get("confidence"), ConfidenceBand.UNVERIFIED
            ),
            locator=Locator.from_dict(locator)
            if isinstance(locator, Mapping)
            else None,
            note=str(raw.get("note") or ""),
        )


# ---------------------------------------------------------------------------
# Paper evidence
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AuthorRecord:
    """One author, with whatever affiliation evidence the source gave."""

    name: str
    given: str = ""
    family: str = ""
    position: int = 0
    orcid: str = ""
    institution: str = ""
    country: str = ""
    is_corresponding: bool = False
    provenance: tuple[Provenance, ...] = ()

    @property
    def surname(self) -> str:
        """Best-effort family name for matching."""
        if self.family:
            return self.family
        parts = self.name.split()
        return parts[-1] if parts else ""

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON mapping."""
        return {
            "name": self.name,
            "given": self.given,
            "family": self.family,
            "position": self.position,
            "orcid": self.orcid,
            "institution": self.institution,
            "country": self.country,
            "is_corresponding": self.is_corresponding,
            "provenance": [_jsonable(item) for item in self.provenance],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> AuthorRecord:
        """Rebuild from :meth:`to_dict` output."""
        return cls(
            name=str(raw.get("name") or ""),
            given=str(raw.get("given") or ""),
            family=str(raw.get("family") or ""),
            position=_as_position(raw.get("position")),
            orcid=str(raw.get("orcid") or ""),
            institution=str(raw.get("institution") or ""),
            country=str(raw.get("country") or ""),
            is_corresponding=bool(raw.get("is_corresponding")),
            provenance=_provenance_tuple(raw.get("provenance")),
        )


@dataclass(slots=True)
class ReferenceRecord:
    """One reference entry as printed, and what we managed to resolve."""

    index: int
    raw: str
    doi: str = ""
    title: str = ""
    year: int | None = None
    venue: str = ""
    authors: tuple[str, ...] = ()
    resolved_work_id: str = ""
    match_confidence: ConfidenceBand = ConfidenceBand.UNVERIFIED
    provenance: tuple[Provenance, ...] = ()

    def __post_init__(self) -> None:
        self.doi = normalise_doi(self.doi)

    @property
    def resolved(self) -> bool:
        """Whether the entry is linked to a verifiable work identifier."""
        return bool(self.resolved_work_id)

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON mapping."""
        return {
            "index": self.index,
            "raw": self.raw,
            "doi": self.doi,
            "title": self.title,
            "year": self.year,
            "venue": self.venue,
            "authors": list(self.authors),
            "resolved_work_id": self.resolved_work_id,
            "match_confidence": self.match_confidence.value,
            "provenance": [_jsonable(item) for item in self.provenance],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ReferenceRecord:
        """Rebuild from :meth:`to_dict` output."""
        year = raw.get("year")
        return cls(
            index=int(raw.get("index") or 0),
            raw=str(raw.get("raw") or ""),
            doi=str(raw.get("doi") or ""),
            title=str(raw.get("title") or ""),
            year=int(year) if isinstance(year, int) else None,
            venue=str(raw.get("venue") or ""),
            authors=tuple(str(item) for item in raw.get("authors") or ()),
            resolved_work_id=str(raw.get("resolved_work_id") or ""),
            match_confidence=_coerce_enum(
                ConfidenceBand, raw.get("match_confidence"), ConfidenceBand.UNVERIFIED
            ),
            provenance=_provenance_tuple(raw.get("provenance")),
        )


@dataclass(slots=True)
class OpenDataRecord:
    """An open-data / code statement and the link it names."""

    kind: OpenDataKind
    url: str = ""
    repository: str = ""
    accession: str = ""
    statement: str = ""
    locator: Locator | None = None
    confidence: ConfidenceBand = ConfidenceBand.UNVERIFIED
    provenance: tuple[Provenance, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON mapping."""
        return {
            "kind": self.kind.value,
            "url": self.url,
            "repository": self.repository,
            "accession": self.accession,
            "statement": self.statement,
            "locator": self.locator.to_dict() if self.locator else None,
            "confidence": self.confidence.value,
            "provenance": [_jsonable(item) for item in self.provenance],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> OpenDataRecord:
        """Rebuild from :meth:`to_dict` output."""
        locator = raw.get("locator")
        return cls(
            kind=_coerce_enum(OpenDataKind, raw.get("kind"), OpenDataKind.UNKNOWN),
            url=str(raw.get("url") or ""),
            repository=str(raw.get("repository") or ""),
            accession=str(raw.get("accession") or ""),
            statement=str(raw.get("statement") or ""),
            locator=Locator.from_dict(locator)
            if isinstance(locator, Mapping)
            else None,
            confidence=_coerce_enum(
                ConfidenceBand, raw.get("confidence"), ConfidenceBand.UNVERIFIED
            ),
            provenance=_provenance_tuple(raw.get("provenance")),
        )


@dataclass(slots=True)
class VenueRecord:
    """Where the paper was published, as far as we could verify it."""

    name: str = ""
    venue_type: str = ""
    publisher: str = ""
    issn: tuple[str, ...] = ()
    is_open_access: bool | None = None
    provenance: tuple[Provenance, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON mapping."""
        return {
            "name": self.name,
            "venue_type": self.venue_type,
            "publisher": self.publisher,
            "issn": list(self.issn),
            "is_open_access": self.is_open_access,
            "provenance": [_jsonable(item) for item in self.provenance],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> VenueRecord:
        """Rebuild from :meth:`to_dict` output."""
        oa = raw.get("is_open_access")
        return cls(
            name=str(raw.get("name") or ""),
            venue_type=str(raw.get("venue_type") or ""),
            publisher=str(raw.get("publisher") or ""),
            issn=tuple(str(item) for item in raw.get("issn") or ()),
            is_open_access=bool(oa) if isinstance(oa, bool) else None,
            provenance=_provenance_tuple(raw.get("provenance")),
        )


# ---------------------------------------------------------------------------
# Access ladder records
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AccessAttempt:
    """One rung of the ladder that was actually tried."""

    route: AccessRoute
    provider: str
    status: ProviderStatus
    url: str = ""
    http_status: int = 0
    byte_count: int = 0
    sha256: str = ""
    path: str = ""
    license: str = ""
    note: str = ""
    retrieved_at: str = ""

    @property
    def ok(self) -> bool:
        """Whether this rung yielded usable content."""
        return self.status is ProviderStatus.OK

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON mapping."""
        return {
            "route": self.route.value,
            "provider": self.provider,
            "status": self.status.value,
            "url": self.url,
            "http_status": self.http_status,
            "byte_count": self.byte_count,
            "sha256": self.sha256,
            "path": self.path,
            "license": self.license,
            "note": self.note,
            "retrieved_at": self.retrieved_at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> AccessAttempt:
        """Rebuild from :meth:`to_dict` output."""
        return cls(
            route=_coerce_enum(
                AccessRoute, raw.get("route"), AccessRoute.METADATA_ONLY
            ),
            provider=str(raw.get("provider") or ""),
            status=_coerce_enum(
                ProviderStatus, raw.get("status"), ProviderStatus.UNAVAILABLE
            ),
            url=str(raw.get("url") or ""),
            http_status=int(raw.get("http_status") or 0),
            byte_count=int(raw.get("byte_count") or 0),
            sha256=str(raw.get("sha256") or ""),
            path=str(raw.get("path") or ""),
            license=str(raw.get("license") or ""),
            note=str(raw.get("note") or ""),
            retrieved_at=str(raw.get("retrieved_at") or ""),
        )


@dataclass(slots=True)
class AccessDecision:
    """The ladder's verdict for one paper, including what was refused."""

    doi: str = ""
    route: AccessRoute = AccessRoute.METADATA_ONLY
    attempts: tuple[AccessAttempt, ...] = ()
    fulltext_path: str = ""
    license: str = ""
    note: str = ""
    decided_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.doi = normalise_doi(self.doi)

    @property
    def has_full_text(self) -> bool:
        """Whether readable full text is on disk for this paper."""
        return bool(self.fulltext_path) and self.route.has_full_text

    @property
    def attempted_routes(self) -> tuple[AccessRoute, ...]:
        """Routes in the order they were tried."""
        return tuple(attempt.route for attempt in self.attempts)

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON mapping."""
        return {
            "doi": self.doi,
            "route": self.route.value,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "fulltext_path": self.fulltext_path,
            "license": self.license,
            "note": self.note,
            "decided_at": self.decided_at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> AccessDecision:
        """Rebuild from :meth:`to_dict` output."""
        return cls(
            doi=str(raw.get("doi") or ""),
            route=_coerce_enum(
                AccessRoute, raw.get("route"), AccessRoute.METADATA_ONLY
            ),
            attempts=tuple(
                AccessAttempt.from_dict(item) for item in raw.get("attempts") or ()
            ),
            fulltext_path=str(raw.get("fulltext_path") or ""),
            license=str(raw.get("license") or ""),
            note=str(raw.get("note") or ""),
            decided_at=str(raw.get("decided_at") or utc_now()),
        )


# ---------------------------------------------------------------------------
# Provider results
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ProviderResult:
    """Uniform envelope for one provider call, success or honest failure."""

    provider: str
    status: ProviderStatus
    url: str = ""
    http_status: int = 0
    payload: Mapping[str, Any] = field(default_factory=dict)
    note: str = ""
    retrieved_at: str = field(default_factory=utc_now)

    @property
    def ok(self) -> bool:
        """Whether this provider answered with a usable payload."""
        return self.status is ProviderStatus.OK

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON mapping."""
        return {
            "provider": self.provider,
            "status": self.status.value,
            "url": self.url,
            "http_status": self.http_status,
            "payload": _jsonable(self.payload),
            "note": self.note,
            "retrieved_at": self.retrieved_at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ProviderResult:
        """Rebuild from :meth:`to_dict` output."""
        payload = raw.get("payload")
        return cls(
            provider=str(raw.get("provider") or ""),
            status=_coerce_enum(
                ProviderStatus, raw.get("status"), ProviderStatus.UNAVAILABLE
            ),
            url=str(raw.get("url") or ""),
            http_status=int(raw.get("http_status") or 0),
            payload=dict(payload) if isinstance(payload, Mapping) else {},
            note=str(raw.get("note") or ""),
            retrieved_at=str(raw.get("retrieved_at") or utc_now()),
        )


# ---------------------------------------------------------------------------
# The paper
# ---------------------------------------------------------------------------

#: Scalar fields that every complete paper record is expected to carry.
CORE_FIELDS: tuple[str, ...] = (
    "title",
    "abstract",
    "year",
    "doi",
    "venue",
    "authors",
    "references",
)


@dataclass(slots=True)
class PaperRecord:
    """One paper as the influence pipeline sees it, with typed absences."""

    paper_id: str
    title: str = ""
    abstract: str = ""
    year: int | None = None
    doi: str = ""
    language: str = ""
    venue: VenueRecord = field(default_factory=VenueRecord)
    authors: tuple[AuthorRecord, ...] = ()
    references: tuple[ReferenceRecord, ...] = ()
    open_data: tuple[OpenDataRecord, ...] = ()
    sections: Mapping[str, str] = field(default_factory=dict)
    keywords: tuple[str, ...] = ()
    cited_by_count: int | None = None
    reference_count: int | None = None
    counts_by_year: tuple[tuple[int, int], ...] = ()
    access_route: AccessRoute = AccessRoute.METADATA_ONLY
    access: AccessDecision | None = None
    fulltext_path: str = ""
    fulltext_pages: int = 0
    work_id: str = ""
    source_paths: tuple[str, ...] = ()
    missing: Mapping[str, MissingReason] = field(default_factory=dict)
    provenance: Mapping[str, tuple[Provenance, ...]] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # A DOI has one canonical spelling; storing the resolver URL would make
        # the record's hash depend on which provider answered first.
        self.doi = normalise_doi(self.doi)

    @property
    def has_full_text(self) -> bool:
        """Whether section-level analysis is possible for this paper."""
        return bool(self.sections) or self.access_route.has_full_text

    @property
    def author_count(self) -> int:
        """Number of listed authors, zero when unknown."""
        return len(self.authors)

    def missing_fields(self) -> tuple[str, ...]:
        """Fields explicitly declared absent, sorted for determinism."""
        return tuple(sorted(self.missing))

    def covered_fields(self) -> tuple[str, ...]:
        """Core fields that carry a value."""
        present: list[str] = []
        for name in CORE_FIELDS:
            value = getattr(self, name, None)
            if isinstance(value, (tuple, list, Mapping)):
                if value:
                    present.append(name)
            elif value not in (None, ""):
                present.append(name)
        return tuple(present)

    def provenance_for(self, fieldname: str) -> tuple[Provenance, ...]:
        """All provenance entries recorded for one field."""
        return tuple(self.provenance.get(fieldname, ()))

    def coverage(self) -> float:
        """Share of core fields that are present rather than absent."""
        return len(self.covered_fields()) / len(CORE_FIELDS)

    def fingerprint(self) -> str:
        """Stable hash of the record's scientific content.

        Wall-clock fields (``retrieved_at`` and friends) are kept in the record as
        an audit trail but excluded here: a hash that changes every second cannot
        be used to prove that two runs read the same paper.
        """
        return content_hash(_stable_payload(self.to_dict()))

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON mapping, artefacts excluded to stay comparable."""
        return {
            "schema_version": SCHEMA_VERSION,
            "paper_id": self.paper_id,
            "title": self.title,
            "abstract": self.abstract,
            "year": self.year,
            "doi": self.doi,
            "language": self.language,
            "venue": self.venue.to_dict(),
            "authors": [author.to_dict() for author in self.authors],
            "references": [entry.to_dict() for entry in self.references],
            "open_data": [item.to_dict() for item in self.open_data],
            "sections": {str(key): str(value) for key, value in self.sections.items()},
            "keywords": list(self.keywords),
            "cited_by_count": self.cited_by_count,
            "reference_count": self.reference_count,
            "counts_by_year": [[year, count] for year, count in self.counts_by_year],
            "access_route": self.access_route.value,
            "access": self.access.to_dict() if self.access else None,
            "fulltext_path": self.fulltext_path,
            "fulltext_pages": self.fulltext_pages,
            "work_id": self.work_id,
            "source_paths": list(self.source_paths),
            "missing": {str(key): value.value for key, value in self.missing.items()},
            "provenance": {
                str(key): [_jsonable(item) for item in value]
                for key, value in self.provenance.items()
            },
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> PaperRecord:
        """Rebuild from :meth:`to_dict` output."""
        access = raw.get("access")
        missing = raw.get("missing") or {}
        provenance = raw.get("provenance") or {}
        year = raw.get("year")
        cited = raw.get("cited_by_count")
        reference_count = raw.get("reference_count")
        return cls(
            paper_id=str(raw.get("paper_id") or ""),
            title=str(raw.get("title") or ""),
            abstract=str(raw.get("abstract") or ""),
            year=int(year) if isinstance(year, int) else None,
            doi=normalise_doi(str(raw.get("doi") or "")),
            language=str(raw.get("language") or ""),
            venue=VenueRecord.from_dict(raw.get("venue") or {}),
            authors=tuple(
                AuthorRecord.from_dict(item) for item in raw.get("authors") or ()
            ),
            references=tuple(
                ReferenceRecord.from_dict(item) for item in raw.get("references") or ()
            ),
            open_data=tuple(
                OpenDataRecord.from_dict(item) for item in raw.get("open_data") or ()
            ),
            sections={
                str(key): str(value)
                for key, value in (raw.get("sections") or {}).items()
            },
            keywords=tuple(str(item) for item in raw.get("keywords") or ()),
            cited_by_count=int(cited) if isinstance(cited, int) else None,
            reference_count=int(reference_count)
            if isinstance(reference_count, int)
            else None,
            counts_by_year=count_pairs(raw.get("counts_by_year")),
            access_route=_coerce_enum(
                AccessRoute, raw.get("access_route"), AccessRoute.METADATA_ONLY
            ),
            access=AccessDecision.from_dict(access)
            if isinstance(access, Mapping)
            else None,
            fulltext_path=str(raw.get("fulltext_path") or ""),
            fulltext_pages=int(raw.get("fulltext_pages") or 0),
            work_id=str(raw.get("work_id") or ""),
            source_paths=tuple(str(item) for item in raw.get("source_paths") or ()),
            missing={
                str(key): _coerce_enum(MissingReason, value, MissingReason.UNDETECTED)
                for key, value in dict(missing).items()
            },
            provenance={
                str(key): _provenance_tuple(value)
                for key, value in dict(provenance).items()
            },
            notes=tuple(str(item) for item in raw.get("notes") or ()),
        )


def count_pairs(raw: Any) -> tuple[tuple[int, int], ...]:
    """Parse ``counts_by_year`` pairs from JSON-ish input.

    Public because ``build_paper`` has to carry the per-year history into the
    record: the horizon window is computed from it, and a record that drops it
    silently loses every label.
    """
    if not isinstance(raw, (list, tuple)):
        return ()
    out: list[tuple[int, int]] = []
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            year, count = item
            if isinstance(year, int) and isinstance(count, int):
                out.append((year, count))
        elif isinstance(item, Mapping):
            year, count = item.get("year"), item.get("cited_by_count")
            if isinstance(year, int) and isinstance(count, int):
                out.append((year, count))
    return tuple(sorted(out))


def merge_records(records: Iterable[PaperRecord]) -> tuple[PaperRecord, ...]:
    """Deduplicate paper records by DOI (or paper id), richest first."""
    best: dict[str, PaperRecord] = {}
    for record in records:
        key = record.doi or record.paper_id
        current = best.get(key)
        if current is None or record.coverage() > current.coverage():
            best[key] = record
    return tuple(best[key] for key in sorted(best))


def summarise(records: Sequence[PaperRecord]) -> dict[str, Any]:
    """Coverage and access summary used by reports and manifests."""
    routes: dict[str, int] = {}
    for record in records:
        routes[record.access_route.value] = routes.get(record.access_route.value, 0) + 1
    full_text = sum(1 for record in records if record.has_full_text)
    accounted = sum(len(record.missing_fields()) for record in records)
    return {
        "papers": len(records),
        "with_full_text": full_text,
        "abstract_only": sum(
            1 for record in records if record.access_route is AccessRoute.ABSTRACT_ONLY
        ),
        "metadata_only": sum(
            1 for record in records if record.access_route is AccessRoute.METADATA_ONLY
        ),
        "needs_human": sum(
            1
            for record in records
            if record.access_route is AccessRoute.ACQUISITION_REQUESTED
        ),
        "routes": dict(sorted(routes.items())),
        "missing_fields": accounted,
        "mean_coverage": (
            sum(record.coverage() for record in records) / len(records)
            if records
            else 0.0
        ),
    }
