"""Sources: what we looked for, where it came from, and how much we trust it.

Discovery is deliberately separate from acquisition. This module decides *which*
sources a research goal implies, in what order they should be tried, and how the
catalogue is written out. Fetching lives in :mod:`jw.scientific_data.acquisition`
so that discovery can be planned, reviewed and tested without a network.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from .contracts import (
    DataRequirementContract,
    RetrievalState,
    SourceRecord,
    SourceRole,
    content_hash,
)
from .domain import DomainPlugin, get_plugin, slug

__all__ = [
    "CATALOG_COLUMNS",
    "RetrievalStep",
    "SourceCatalog",
    "canonical_url",
    "catalog_csv",
    "plan_retrieval",
]

CATALOG_COLUMNS = (
    "source_id",
    "role",
    "title",
    "provider",
    "canonical_uri",
    "format_hint",
    "retrieval",
    "identifier",
    "license",
    "bytes",
    "content_sha256",
    "discovered_via",
    "failure_reason",
    "notes",
)

_TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid",
    "ref",
    "source",
}
_QUERY_ORDER = ("table", "query", "search", "id", "bibcode", "doi", "-out", "-source")


def canonical_url(url: str) -> str:
    """Normalise a URL so the same document is not catalogued twice."""
    if not url:
        return ""
    stripped = url.strip()
    if stripped.lower().startswith("file:"):
        return _canonical_file_url(stripped)
    try:
        parts = urlsplit(stripped)
    except ValueError:
        return stripped
    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower()
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_PARAMS
    ]
    kept.sort(key=lambda item: _query_rank(item[0]))
    netloc = host
    if parts.port and parts.port not in (80, 443):
        netloc = f"{host}:{parts.port}"
    return urlunsplit((scheme, netloc, path, urlencode(kept), ""))


def _canonical_file_url(url: str) -> str:
    """Keep a local file URI opaque instead of treating its path as a netloc.

    ``urlsplit('file://C:\\data\\x.csv')`` reads ``C:\\data\\x.csv`` as the
    network location, so anything that inspects ``.port`` raises on Windows.
    Local paths are not URLs, so keep the path text as it came in.
    """
    raw = unquote(url[len("file:") :]).removeprefix("//")
    return "file://" + raw


def _query_rank(key: str) -> tuple[int, str]:
    lowered = key.lower()
    return (_QUERY_ORDER.index(lowered) if lowered in _QUERY_ORDER else 99, lowered)


@dataclass(frozen=True, slots=True)
class RetrievalStep:
    """One planned action in the discovery workflow."""

    step_id: str
    role: SourceRole
    action: str
    query: str
    expected_format: str = ""
    priority: int = 1
    rationale: str = ""
    optional: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "role": str(self.role),
            "action": self.action,
            "query": self.query,
            "expected_format": self.expected_format,
            "priority": self.priority,
            "rationale": self.rationale,
            "optional": self.optional,
        }


def plan_retrieval(
    contract: DataRequirementContract,
    *,
    plugin: DomainPlugin | None = None,
    include_figures: bool = True,
) -> tuple[RetrievalStep, ...]:
    """Turn a research goal into an ordered discovery plan.

    The plan names the *kind* of source each step is expected to yield, so the
    report can explain the provenance chain from question to table.
    """
    domain = plugin or get_plugin(contract.domain_plugin)
    subject = contract.entity_class or domain.entity.name or "objects"
    observables = [field.name for field in contract.observables]
    measured = [
        name
        for name in observables
        if name not in {"object", "time", "ra", "dec", "classification"}
    ]
    terms = f"{subject} {' '.join(measured)}".strip()
    hints = domain.hints_for(measured[0]) if measured else ()
    query = " ".join(part for part in (terms, hints[0] if hints else "") if part)

    steps: list[RetrievalStep] = [
        RetrievalStep(
            step_id="discover:literature",
            role=SourceRole.PRIMARY_PUBLISHED,
            action="search_literature",
            query=query,
            expected_format="pdf|html",
            priority=1,
            rationale=(
                "Papers report the measurements and point at the tables and "
                "supplementary files that hold them."
            ),
            optional=False,
        ),
        RetrievalStep(
            step_id="discover:tables",
            role=SourceRole.SUPPLEMENT,
            action="search_supplementary_tables",
            query=f"{query} machine readable table csv",
            expected_format="csv|tsv|xlsx|txt",
            priority=2,
            rationale=(
                "A machine-readable supplement is cheaper and safer than parsing "
                "a printed table."
            ),
        ),
        RetrievalStep(
            step_id="discover:catalogs",
            role=SourceRole.CATALOG,
            action="search_open_catalog",
            query=query,
            expected_format="csv|json|api",
            priority=3,
            rationale=(
                "Open catalogs provide homogeneous, already-structured records "
                "for cross-checking."
            ),
        ),
        RetrievalStep(
            step_id="discover:authored",
            role=SourceRole.AUTHORED_TABLE,
            action="inspect_local_inputs",
            query=contract.research_goal,
            expected_format="csv|tsv|xlsx|html|md|json",
            priority=1,
            rationale="User-supplied files are treated as authored tables.",
            optional=False,
        ),
    ]
    if include_figures:
        steps.append(
            RetrievalStep(
                step_id="discover:figures",
                role=SourceRole.FIGURE_DIGITIZED,
                action="collect_figures",
                query=f"{query} light curve figure",
                expected_format="png|jpg|eps|pdf",
                priority=4,
                rationale=(
                    "When the numbers exist only in a plot, digitisation recovers "
                    "them with an explicit error budget and overlay check."
                ),
            )
        )
    return tuple(sorted(steps, key=lambda step: (step.priority, step.step_id)))


@dataclass(slots=True)
class SourceCatalog:
    """The run's catalogue of sources, deduplicated by canonical URL."""

    sources: dict[str, SourceRecord] = field(default_factory=dict)
    _by_uri: dict[str, str] = field(default_factory=dict)
    _sequence: int = 0

    def __len__(self) -> int:
        return len(self.sources)

    def add(
        self,
        *,
        url: str = "",
        title: str = "",
        role: SourceRole = SourceRole.UNKNOWN,
        provider: str = "",
        identifier: str = "",
        format_hint: str = "",
        license: str = "",
        published: str = "",
        discovered_via: str = "",
        relevance: float = 0.5,
        trust: float = 0.5,
        notes: Iterable[str] = (),
        source_id: str = "",
        local_path: str = "",
    ) -> SourceRecord:
        """Register a source, returning the existing record for duplicates."""
        uri = canonical_url(url)
        if uri and uri in self._by_uri:
            return self.sources[self._by_uri[uri]]
        self._sequence += 1
        identity = source_id or (
            f"src:{slug(provider) or 'local'}:{self._sequence:03d}"
            if not identifier
            else f"src:{slug(identifier)}"
        )
        record = SourceRecord(
            source_id=identity,
            role=role,
            title=title,
            url=url,
            canonical_uri=uri,
            provider=provider,
            retrieval=RetrievalState.CANDIDATE,
            local_path=local_path,
            license=license,
            published=published,
            discovered_via=discovered_via,
            notes=tuple(notes),
            identifier=identifier,
            format_hint=format_hint,
            relevance=max(0.0, min(1.0, relevance)),
            trust=max(0.0, min(1.0, trust)),
        )
        self.sources[record.source_id] = record
        if uri:
            self._by_uri[uri] = record.source_id
        return record

    def get(self, source_id: str) -> SourceRecord | None:
        return self.sources.get(source_id)

    def by_uri(self, url: str) -> SourceRecord | None:
        source_id = self._by_uri.get(canonical_url(url), "")
        return self.sources.get(source_id) if source_id else None

    def mark(
        self,
        source_id: str,
        retrieval: RetrievalState,
        *,
        local_path: str = "",
        content_sha256: str = "",
        size: int = 0,
        failure_reason: str = "",
        note: str = "",
    ) -> SourceRecord:
        """Record the outcome of an acquisition attempt."""
        record = self.sources[source_id]
        updated = replace(
            record,
            retrieval=retrieval,
            local_path=local_path or record.local_path,
            content_sha256=content_sha256 or record.content_sha256,
            bytes=size or record.bytes,
            failure_reason=failure_reason or record.failure_reason,
            notes=(*record.notes, note) if note else record.notes,
        )
        self.sources[source_id] = updated
        return updated

    def ranked(self, *, role: SourceRole | None = None) -> tuple[SourceRecord, ...]:
        """Acquisition order: highest priority first, ties broken by id."""
        candidates = [
            record
            for record in self.sources.values()
            if role is None or record.role is role
        ]
        return tuple(
            sorted(
                candidates, key=lambda record: (-record.priority(), record.source_id)
            )
        )

    def pending(self) -> tuple[SourceRecord, ...]:
        return tuple(
            record
            for record in self.ranked()
            if record.retrieval is RetrievalState.CANDIDATE
        )

    def acquired(self) -> tuple[SourceRecord, ...]:
        return tuple(
            record
            for record in self.ranked()
            if record.retrieval is RetrievalState.ACQUIRED
        )

    def by_role(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in self.sources.values():
            counts[str(record.role)] = counts.get(str(record.role), 0) + 1
        return dict(sorted(counts.items()))

    def coverage_summary(self) -> dict[str, Any]:
        """How much of the discovery plan actually delivered data."""
        roles = {str(record.role) for record in self.sources.values()}
        acquired_roles = {str(record.role) for record in self.acquired()}
        return {
            "sources": len(self.sources),
            "acquired": len(self.acquired()),
            "pending": len(self.pending()),
            "by_role": self.by_role(),
            "roles_with_data": sorted(acquired_roles),
            "roles_missing_data": sorted(roles - acquired_roles),
        }

    def fingerprint(self) -> str:
        """Stable digest of the catalogue, used in the run manifest."""
        return content_hash(
            [
                [record.source_id, record.canonical_uri, str(record.retrieval)]
                for record in sorted(self.sources.values(), key=lambda r: r.source_id)
            ]
        )

    def to_records(self) -> tuple[SourceRecord, ...]:
        return tuple(sorted(self.sources.values(), key=lambda record: record.source_id))

    def extend(self, records: Iterable[SourceRecord]) -> None:
        """Load sources back from a ledger without re-deriving ids."""
        for record in records:
            self.sources[record.source_id] = record
            if record.canonical_uri:
                self._by_uri[record.canonical_uri] = record.source_id
            self._sequence = max(self._sequence, len(self.sources))

    @classmethod
    def from_records(cls, records: Iterable[SourceRecord]) -> SourceCatalog:
        catalog = cls()
        catalog.extend(records)
        return catalog


def catalog_csv(sources: Iterable[SourceRecord]) -> str:
    """Render the catalogue as the CSV the delivery package contains."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CATALOG_COLUMNS)
    for record in sorted(sources, key=lambda item: item.source_id):
        writer.writerow(
            [
                record.source_id,
                str(record.role),
                record.title,
                record.provider,
                record.canonical_uri or record.url,
                record.format_hint,
                str(record.retrieval),
                record.identifier,
                record.license,
                record.bytes,
                record.content_sha256,
                record.discovered_via,
                record.failure_reason,
                " | ".join(record.notes),
            ]
        )
    return buffer.getvalue()


def source_role_for_format(format_hint: str, *, fallback: SourceRole) -> SourceRole:
    """Map a file format onto the role it usually plays."""
    hint = format_hint.lower().lstrip(".")
    if hint in {"png", "jpg", "jpeg", "gif", "eps", "svg"}:
        return SourceRole.FIGURE_DIGITIZED
    if hint in {
        "csv",
        "tsv",
        "xlsx",
        "xls",
        "json",
        "jsonl",
        "txt",
        "dat",
    } and fallback in {
        SourceRole.UNKNOWN,
        SourceRole.AUTHORED_TABLE,
    }:
        return SourceRole.AUTHORED_TABLE
    return fallback


def register_local_sources(
    catalog: SourceCatalog,
    paths: Sequence[str],
    *,
    provider: str = "local",
    discovered_via: str = "user_input",
) -> tuple[SourceRecord, ...]:
    """Register files the user supplied, which are trusted but never assumed valid."""
    added: list[SourceRecord] = []
    for path in paths:
        hint = str(path).rsplit(".", 1)[-1].lower() if "." in str(path) else ""
        added.append(
            catalog.add(
                url=f"file://{path}",
                # A Windows path uses backslashes, so split on both kinds of
                # separator rather than only the POSIX one.
                title=str(path).replace("\\", "/").rsplit("/", 1)[-1],
                role=source_role_for_format(hint, fallback=SourceRole.AUTHORED_TABLE),
                provider=provider,
                format_hint=hint,
                relevance=1.0,
                trust=0.8,
                discovered_via=discovered_via,
                local_path=str(path),
                notes=("supplied_by_user",),
            )
        )
    return tuple(added)


def _describe_source(record: SourceRecord) -> str:
    bits = [str(record.role), record.provider or "unknown provider"]
    if record.identifier:
        bits.append(record.identifier)
    return " / ".join(bits)


def source_summary_lines(catalog: SourceCatalog) -> tuple[str, ...]:
    """Markdown bullets describing where the data came from."""
    lines: list[str] = []
    for record in catalog.ranked():
        extra = f" ({record.failure_reason})" if record.failure_reason else ""
        lines.append(
            f"- `{record.source_id}` [{record.retrieval}] {_describe_source(record)}{extra}"
        )
    return tuple(lines)
