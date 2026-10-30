"""Turn a research goal into concrete, auditable source candidates.

The kernel cannot invent references. Discovery therefore asks a pluggable
searcher (the repository's cached literature search by default) and converts
whatever it returns into :class:`SourceRecord` entries. A provider outage is
reported as an outage — never as an empty-but-successful search, and never as a
fabricated citation.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .contracts import (
    IssueCode,
    IssueSeverity,
    QualityIssue,
    RetrievalState,
    SourceRecord,
    SourceRole,
)
from .domain import DomainPlugin, get_plugin, slug
from .sources import SourceCatalog, plan_retrieval

# A searcher maps ``(query, limit)`` to the payload ``lit_search`` returns.
Searcher = Callable[[str, int], Mapping[str, Any]]

_REFEREED_TRUST = 0.9
_PREPRINT_TRUST = 0.6
_MAX_LIMIT = 20


def literature_searcher(query: str, limit: int) -> Mapping[str, Any]:
    """Default searcher: the repository's cached literature search.

    Imported lazily so the kernel works without the knowledge-base extra, and
    so tests never touch a network path by accident.
    """

    from jw.tools.knowledge_base import lit_search

    payload = json.loads(lit_search(query, source="all", limit=limit))
    if not isinstance(payload, Mapping):
        raise TypeError("literature search returned a non-object payload")
    return payload


@dataclass(slots=True)
class DiscoveryReport:
    """What discovery searched for, what came back, and what it refused."""

    query: str
    limit: int = 5
    status: str = "ok"
    steps: tuple[str, ...] = ()
    catalog: SourceCatalog | None = None
    issues: tuple[QualityIssue, ...] = ()
    note: str = ""
    provider_status: Mapping[str, Any] = field(default_factory=dict)
    skipped: tuple[str, ...] = ()

    @property
    def found(self) -> tuple[SourceRecord, ...]:
        return tuple(self.catalog.to_records()) if self.catalog else ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "limit": self.limit,
            "status": self.status,
            "note": self.note,
            "steps": list(self.steps),
            "found": [record.to_dict() for record in self.found],
            "skipped": list(self.skipped),
            "provider_status": _jsonable(self.provider_status),
            "issues": [issue.to_dict() for issue in self.issues],
        }


def discovery_query(contract: Any, *, plugin: DomainPlugin | None = None) -> str:
    """Build one search string from the contract, not from the raw goal alone."""

    resolved = plugin or get_plugin(contract.domain_plugin)
    observables = [
        field.name for field in getattr(contract, "observables", ()) if field.name
    ] or list(resolved.observable_names())
    parts = [str(getattr(contract, "research_goal", "") or "").strip()]
    if not parts[0]:
        parts = [str(getattr(contract, "entity_class", "") or "").strip()]
    wanted = [item.replace("_", " ") for item in observables[:4]]
    if wanted:
        parts.append(", ".join(wanted))
    return " ".join(part for part in parts if part).strip()


def _role_for_hit(hit: Mapping[str, Any]) -> SourceRole:
    """A paper is a primary published source; its own data may be a supplement.

    The distinction is made later, when a fetched payload shows what it carries
    (a table, a data file, a figure), because a title cannot tell us that.
    """

    del hit
    return SourceRole.PRIMARY_PUBLISHED


def _format_hint(url: str) -> str:
    lowered = url.casefold()
    for suffix, hint in (
        (".pdf", "pdf"),
        (".csv", "csv"),
        (".tsv", "tsv"),
        (".xlsx", "xlsx"),
        (".json", "json"),
        (".zip", "zip"),
        (".fits", "fits"),
        (".png", "png"),
        (".jpg", "jpeg"),
    ):
        if lowered.endswith(suffix):
            return hint
    return "html"


def _provider_of(hit: Mapping[str, Any]) -> str:
    for candidate in ("provider", "source", "source_version"):
        value = str(hit.get(candidate) or "").strip()
        if value:
            return value
    source_id = str(hit.get("source_id") or "")
    return source_id.split(":", 1)[0] if ":" in source_id else "literature"


def _hit_url(hit: Mapping[str, Any]) -> str:
    url = str(hit.get("url") or "").strip()
    if url:
        return url
    doi = str(hit.get("doi") or "").strip()
    return f"https://doi.org/{doi}" if doi else ""


def discovery_issue(
    *, status: str, message: str, severity: IssueSeverity | None = None
) -> QualityIssue:
    """One issue describing a discovery failure or a skipped candidate."""

    return QualityIssue(
        issue_id=f"issue:discover:{slug(status)}:{slug(message)[:60]}",
        code=IssueCode.SOURCE_HOST_DENIED
        if status == "policy_blocked"
        else IssueCode.MISSING_COVERAGE,
        severity=severity or IssueSeverity.WARNING,
        stage="discover",
        subject={"status": status},
        detail=message,
        proposed_action=(
            "Ask the user for a source list, or re-run with network access "
            "explicitly allowed."
        ),
    )


def discover_sources(
    contract: Any,
    *,
    plugin: DomainPlugin | None = None,
    searcher: Searcher | None = None,
    limit: int = 5,
    allow_network: bool = False,
    catalog: SourceCatalog | None = None,
    extra_urls: Sequence[str] = (),
) -> DiscoveryReport:
    """Find candidate sources for ``contract`` and register them in a catalog.

    ``allow_network=False`` refuses to run the network searcher at all: the
    report then says so instead of returning an empty, silently "successful"
    result. ``extra_urls`` are registered regardless, because a URL the user
    supplied is not a network fetch.
    """

    resolved = plugin or get_plugin(contract.domain_plugin)
    query = discovery_query(contract, plugin=resolved)
    bounded = max(1, min(int(limit or 5), _MAX_LIMIT))
    steps = tuple(step.step_id for step in plan_retrieval(contract, plugin=resolved))
    report_catalog = catalog if catalog is not None else SourceCatalog()
    issues: list[QualityIssue] = []
    skipped: list[str] = []

    for url in _unique(extra_urls):
        if url:
            report_catalog.add(
                url=url,
                title=url.rsplit("/", 1)[-1] or url,
                role=SourceRole.UNKNOWN,
                provider="user_url",
                discovered_via="user_input",
                relevance=1.0,
                trust=0.8,
                notes=("supplied_by_user",),
            )

    if searcher is None and not allow_network:
        issues.append(
            discovery_issue(
                status="policy_blocked",
                message=(
                    "Literature discovery needs network access, which is refused "
                    "by the acquisition policy unless it is explicitly allowed."
                ),
                severity=IssueSeverity.WARNING,
            )
        )
        return DiscoveryReport(
            query=query,
            limit=bounded,
            status="policy_blocked",
            steps=steps,
            catalog=report_catalog,
            issues=tuple(issues),
            note="No search was performed; supply a source list or allow network access.",
        )

    active = searcher or literature_searcher
    try:
        payload = active(query, bounded)
    except Exception as exc:  # a provider must never crash a run
        issues.append(
            discovery_issue(
                status="unavailable",
                message=f"Literature search failed: {exc}",
                severity=IssueSeverity.WARNING,
            )
        )
        return DiscoveryReport(
            query=query,
            limit=bounded,
            status="unavailable",
            steps=steps,
            catalog=report_catalog,
            issues=tuple(issues),
            note="The search provider failed; no reference was invented to fill the gap.",
        )

    results = payload.get("results")
    hits = [hit for hit in (results or ()) if isinstance(hit, Mapping)]
    status = str(payload.get("status") or "ok")

    for index, hit in enumerate(hits):
        if bool(hit.get("is_retracted")):
            skipped.append(str(hit.get("source_id") or hit.get("title") or "retracted"))
            continue
        url = _hit_url(hit)
        if not url:
            skipped.append(str(hit.get("source_id") or "no_url"))
            continue
        refereed = bool(hit.get("is_refereed"))
        report_catalog.add(
            url=url,
            title=str(hit.get("title") or url),
            role=_role_for_hit(hit),
            provider=_provider_of(hit),
            identifier=str(hit.get("doi") or hit.get("source_id") or ""),
            format_hint=_format_hint(url),
            published=str(hit.get("publication_date") or hit.get("year") or ""),
            discovered_via=f"literature_search:{payload.get('source') or 'all'}",
            relevance=max(0.1, 1.0 - 0.1 * index),
            trust=_REFEREED_TRUST if refereed else _PREPRINT_TRUST,
            notes=(
                f"rank:{index}",
                "refereed" if refereed else "not_refereed",
            ),
        )

    if status not in {"ok", "partial"}:
        issues.append(
            discovery_issue(
                status=status,
                message=(
                    "The literature search reported status "
                    f"{status!r}: {'; '.join(str(item) for item in (payload.get('provider_diagnostics') or ())) or 'no diagnostic detail'}"
                ),
            )
        )
    registered = report_catalog.to_records()
    if not hits:
        issues.append(
            discovery_issue(
                status="no_results",
                message=f"No literature hit matched {query!r}; try a broader query.",
            )
        )
    elif not registered:
        issues.append(
            discovery_issue(
                status="skipped_all",
                message=(
                    "Every literature hit was skipped ("
                    f"{', '.join(skipped) or 'no usable URL'}"
                    "); nothing entered the catalog."
                ),
            )
        )
    for record in report_catalog.to_records():
        if record.retrieval is RetrievalState.CANDIDATE:
            # Nothing was downloaded, and the catalog must say exactly that.
            report_catalog.mark(record.source_id, RetrievalState.CANDIDATE)

    if not hits:
        reported_status = "no_results"
    elif not registered:
        reported_status = "skipped_all"
    else:
        reported_status = status

    return DiscoveryReport(
        query=query,
        limit=bounded,
        status=reported_status,
        steps=steps,
        catalog=report_catalog,
        issues=tuple(issues),
        note=(
            "Candidates only: nothing was downloaded. Table, supplement and "
            "figure payloads still have to be acquired and parsed."
        ),
        provider_status=_provider_summary(payload),
        skipped=tuple(skipped),
    )


def _provider_summary(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        key: payload.get(key)
        for key in ("source", "count", "raw_count", "providers_queried")
        if key in payload
    }


def _unique(values: Sequence[str]) -> tuple[str, ...]:
    seen: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.append(text)
    return tuple(seen)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


__all__ = [
    "DiscoveryReport",
    "Searcher",
    "discover_sources",
    "discovery_issue",
    "discovery_query",
    "literature_searcher",
]
