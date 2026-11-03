"""Discover machine-readable data files, not just papers.

The literature path in :mod:`jw.scientific_data.discovery` answers *which papers
mention this goal*.  This module answers *which data files can actually be
downloaded and parsed*.  It queries open data repositories with one bounded
search per provider, normalises every hit into a :class:`ProviderHit`, and
registers candidates in the same :class:`SourceCatalog` the acquisition stage
already understands.

Two rules are inherited from discovery and cloud table recognition:

* a provider outage is reported as an outage, never as an empty success;
* nothing is downloaded here.  Candidates still pass through the acquisition
  policy, which is the only place that decides whether a URL may be fetched.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .contracts import IssueSeverity, QualityIssue, SourceRole
from .discovery import (
    DiscoveryReport,
    discovery_issue,
    discovery_query,
)
from .domain import DomainPlugin, get_plugin
from .policy import AcquisitionPolicy
from .sources import SourceCatalog

# The opener shape used by cloud table recognition: (request, timeout) -> bytes.
Opener = Callable[[urllib.request.Request, float], bytes]

USER_AGENT = "jw-scientific-data/1.0"
DEFAULT_PROVIDERS = ("zenodo", "dataverse", "openalex")
_MAX_LIMIT = 20
_TRUST_REPOSITORY = 0.65
_TRUST_JOURNAL = 0.85
_TRUST_PREPRINT = 0.6

_PROVIDER_ROLES = {
    "csv": SourceRole.AUTHORED_TABLE,
    "tsv": SourceRole.AUTHORED_TABLE,
    "xlsx": SourceRole.AUTHORED_TABLE,
    "xls": SourceRole.AUTHORED_TABLE,
    "json": SourceRole.AUTHORED_TABLE,
    "jsonl": SourceRole.AUTHORED_TABLE,
    "txt": SourceRole.AUTHORED_TABLE,
    "dat": SourceRole.AUTHORED_TABLE,
    "zip": SourceRole.SUPPLEMENT,
    "pdf": SourceRole.PRIMARY_PUBLISHED,
}


class ProviderError(Exception):
    """A data provider failed in a way the report must not hide."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class ProviderHit:
    """One downloadable file candidate, already normalised."""

    url: str
    title: str
    provider: str
    identifier: str = ""
    format_hint: str = "html"
    license: str = ""
    published: str = ""
    size: int = 0
    role: SourceRole = SourceRole.CATALOG
    trust: float = _TRUST_REPOSITORY
    relevance: float = 0.5
    discovered_via: str = ""
    notes: tuple[str, ...] = ()


@dataclass(slots=True)
class ProviderResult:
    """What one provider returned, including the files it refused."""

    hits: list[ProviderHit] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    status: str = "ok"
    detail: str = ""


@dataclass(slots=True)
class DataProviderSettings:
    """Endpoints and budgets for the open data repositories."""

    providers: tuple[str, ...] = DEFAULT_PROVIDERS
    zenodo_endpoint: str = "https://zenodo.org/api/records"
    dataverse_endpoint: str = "https://dataverse.harvard.edu"
    openalex_endpoint: str = "https://api.openalex.org/works"
    mailto: str = ""
    timeout: float = 30.0
    max_bytes: int = 50_000_000

    @property
    def enabled(self) -> bool:
        return bool(self.providers)


def resolve_provider_settings(
    explicit: DataProviderSettings | None = None,
    *,
    config: Any = None,
    environ: Mapping[str, str] | None = None,
) -> DataProviderSettings:
    """Resolve provider endpoints, env first and configuration second."""

    if explicit is not None:
        return explicit
    if config is None and environ is None:
        try:
            from jw.config.settings import load_config

            config = load_config()
        except Exception:
            config = None
    env = environ if environ is not None else _process_environ()
    base = DataProviderSettings()

    def pick(value: str, attribute: str, fallback: str) -> str:
        if value.strip():
            return value.strip()
        config_value = getattr(config, attribute, "") if config is not None else ""
        return str(config_value or "").strip() or fallback

    providers_raw = pick(
        env.get("JW_DATA_PROVIDERS", ""), "data_providers", " ".join(base.providers)
    )
    providers = tuple(
        item.strip().lower()
        for item in providers_raw.replace(";", ",").split(",")
        if item.strip()
    )
    timeout_raw = env.get("JW_DATA_PROVIDER_TIMEOUT", "").strip()
    try:
        timeout = float(timeout_raw) if timeout_raw else base.timeout
    except ValueError:
        timeout = base.timeout
    max_bytes_raw = env.get("JW_DATA_PROVIDER_MAX_BYTES", "").strip()
    try:
        max_bytes = int(max_bytes_raw) if max_bytes_raw else base.max_bytes
    except ValueError:
        max_bytes = base.max_bytes
    return DataProviderSettings(
        providers=providers,
        zenodo_endpoint=pick(
            env.get("JW_ZENODO_ENDPOINT", ""),
            "zenodo_endpoint",
            base.zenodo_endpoint,
        ),
        dataverse_endpoint=pick(
            env.get("JW_DATAVERSE_ENDPOINT", ""),
            "dataverse_endpoint",
            base.dataverse_endpoint,
        ),
        openalex_endpoint=pick(
            env.get("JW_OPENALEX_ENDPOINT", ""),
            "openalex_endpoint",
            base.openalex_endpoint,
        ),
        mailto=pick(
            env.get("JW_OPENALEX_MAILTO", "") or env.get("OPENALEX_MAILTO", ""),
            "openalex_mailto",
            "",
        ),
        timeout=max(1.0, timeout),
        max_bytes=max(0, max_bytes),
    )


def discover_data_sources(
    contract: Any,
    *,
    plugin: DomainPlugin | None = None,
    providers: Sequence[str] | None = None,
    limit: int = 5,
    allow_network: bool = False,
    catalog: SourceCatalog | None = None,
    opener: Opener | None = None,
    settings: DataProviderSettings | None = None,
    policy: AcquisitionPolicy | None = None,
    query: str = "",
    extra_urls: Sequence[str] = (),
) -> DiscoveryReport:
    """Search open data repositories and register file candidates.

    ``allow_network=False`` refuses the search outright.  Provider failures are
    recorded per provider and never abort the others.  ``policy`` additionally
    gates each provider endpoint when the caller configured a host allowlist.
    """

    resolved = plugin or get_plugin(contract.domain_plugin)
    resolved_settings = settings or resolve_provider_settings()
    wanted = tuple(providers) if providers is not None else resolved_settings.providers
    text = (query or discovery_query(contract, plugin=resolved)).strip()
    bounded = max(1, min(int(limit or 5), _MAX_LIMIT))
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

    if not allow_network:
        issues.append(
            discovery_issue(
                status="policy_blocked",
                message=(
                    "Data repository discovery needs network access, which is "
                    "refused unless the caller explicitly allows it."
                ),
                severity=IssueSeverity.WARNING,
            )
        )
        return DiscoveryReport(
            query=text,
            limit=bounded,
            status="policy_blocked",
            catalog=report_catalog,
            issues=tuple(issues),
            note="No repository was queried; supply data files or allow network access.",
        )

    active_opener = opener or _default_opener
    statuses: dict[str, Any] = {}
    registered: list[str] = []
    total_hits = 0

    for name in wanted:
        provider = _PROVIDERS.get(name)
        if provider is None:
            statuses[name] = {"status": "unknown_provider", "hits": 0}
            issues.append(
                discovery_issue(
                    status="unavailable",
                    message=f"Unknown data provider {name!r}.",
                )
            )
            continue
        endpoint = _endpoint_of(name, resolved_settings)
        if policy is not None and not policy.allows(endpoint).allowed:
            statuses[name] = {"status": "policy_blocked", "hits": 0}
            issues.append(
                discovery_issue(
                    status="policy_blocked",
                    message=(
                        f"Data provider {name!r} is outside the acquisition "
                        f"policy allowlist ({endpoint})."
                    ),
                )
            )
            continue
        try:
            result = provider(
                text,
                bounded,
                opener=active_opener,
                settings=resolved_settings,
            )
        except ProviderError as exc:
            statuses[name] = {
                "status": "unavailable",
                "hits": 0,
                "detail": f"{exc.code}: {exc.detail}" if exc.detail else exc.code,
            }
            issues.append(
                discovery_issue(
                    status="unavailable",
                    message=(
                        f"Data provider {name!r} failed ({exc.code}); "
                        "no candidate was invented to fill the gap."
                    ),
                )
            )
            continue
        except Exception as exc:  # a provider must never crash a run
            statuses[name] = {
                "status": "unavailable",
                "hits": 0,
                "detail": f"{type(exc).__name__}: {exc}",
            }
            issues.append(
                discovery_issue(
                    status="unavailable",
                    message=f"Data provider {name!r} failed: {exc}",
                )
            )
            continue

        statuses[name] = {
            "status": result.status,
            "hits": len(result.hits),
            "skipped": list(result.skipped),
        }
        if result.detail:
            statuses[name]["detail"] = result.detail
        skipped.extend(result.skipped)
        total_hits += len(result.hits)
        for index, hit in enumerate(result.hits):
            record = report_catalog.add(
                url=hit.url,
                title=hit.title or hit.url,
                role=hit.role,
                provider=hit.provider,
                identifier=hit.identifier,
                format_hint=hit.format_hint,
                license=hit.license,
                published=hit.published,
                discovered_via=hit.discovered_via,
                relevance=hit.relevance if hit.relevance > 0 else _rank(index),
                trust=hit.trust,
                notes=hit.notes,
            )
            if record.source_id not in registered:
                registered.append(record.source_id)

    failed = [
        name
        for name, status in statuses.items()
        if status.get("status") not in {"ok", "partial"}
    ]
    if wanted and len(failed) == len(statuses):
        overall = "unavailable"
    elif failed:
        overall = "partial"
    elif total_hits == 0:
        overall = "no_results"
    elif not registered:
        overall = "skipped_all"
    else:
        overall = "ok"

    if total_hits == 0 and overall != "unavailable":
        issues.append(
            discovery_issue(
                status="no_results",
                message=f"No data file matched {text!r}; try a broader query.",
            )
        )

    return DiscoveryReport(
        query=text,
        limit=bounded,
        status=overall,
        catalog=report_catalog,
        issues=tuple(issues),
        note=(
            "Candidates only: nothing was downloaded. Every URL still has to "
            "pass the acquisition policy before it can be parsed."
        ),
        provider_status=statuses,
        skipped=tuple(skipped),
    )


# ---------------------------------------------------------------------------
# Zenodo
# ---------------------------------------------------------------------------


def zenodo_search(
    query: str,
    limit: int,
    *,
    opener: Opener,
    settings: DataProviderSettings,
) -> ProviderResult:
    """Search Zenodo records and return their downloadable files."""

    params = urllib.parse.urlencode(
        {"q": query, "size": str(limit), "sort": "bestmatch"}
    )
    payload = _get_json(
        f"{settings.zenodo_endpoint}?{params}",
        opener=opener,
        timeout=settings.timeout,
    )
    hits_block = payload.get("hits")
    if isinstance(hits_block, Mapping):
        records = hits_block.get("hits") or []
    elif isinstance(hits_block, list):
        records = hits_block
    else:
        records = []
    result = ProviderResult()
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            continue
        metadata = (
            record.get("metadata")
            if isinstance(record.get("metadata"), Mapping)
            else {}
        )
        title = str(metadata.get("title") or record.get("title") or "").strip()
        record_id = str(record.get("id") or "").strip()
        title = title or f"zenodo:{record_id}"
        license_block = metadata.get("license")
        if isinstance(license_block, Mapping):
            license_id = str(license_block.get("id") or "").strip()
        else:
            license_id = str(license_block or "").strip()
        published = str(metadata.get("publication_date") or "").strip()
        doi = str(record.get("doi") or metadata.get("doi") or "").strip()
        files = [
            item for item in (record.get("files") or ()) if isinstance(item, Mapping)
        ]
        if not files:
            landing = _zenodo_landing(record, record_id)
            if landing:
                result.hits.append(
                    ProviderHit(
                        url=landing,
                        title=title,
                        provider="zenodo",
                        identifier=doi,
                        format_hint="html",
                        license=license_id,
                        published=published,
                        role=SourceRole.CATALOG,
                        trust=0.5,
                        relevance=_rank(index),
                        discovered_via="zenodo_search",
                        notes=("zenodo", "landing_page", "no_files"),
                    )
                )
            continue
        for entry in files:
            key = str(entry.get("key") or entry.get("filename") or "").strip()
            size = _int(entry.get("size"))
            if settings.max_bytes and size > settings.max_bytes:
                result.skipped.append(
                    f"zenodo:{record_id}/{key or 'file'} (too_large:{size})"
                )
                continue
            links = (
                entry.get("links") if isinstance(entry.get("links"), Mapping) else {}
            )
            url = str(links.get("self") or links.get("download") or "").strip()
            if not url:
                result.skipped.append(f"zenodo:{record_id}/{key or 'file'} (no_url)")
                continue
            hint = _format_hint(key or url)
            result.hits.append(
                ProviderHit(
                    url=url,
                    title=f"{title} [{key or 'file'}]",
                    provider="zenodo",
                    identifier=doi or record_id,
                    format_hint=hint,
                    license=license_id,
                    published=published,
                    size=size,
                    role=_PROVIDER_ROLES.get(hint, SourceRole.CATALOG),
                    trust=_TRUST_REPOSITORY,
                    relevance=_rank(index),
                    discovered_via="zenodo_search",
                    notes=("zenodo", f"record:{record_id}")
                    + ((f"size:{size}",) if size else ()),
                )
            )
    if result.skipped and result.status == "ok":
        result.status = "partial"
        result.detail = f"{len(result.skipped)} file(s) skipped by the size budget"
    return result


def _zenodo_landing(record: Mapping[str, Any], record_id: str) -> str:
    links = record.get("links") if isinstance(record.get("links"), Mapping) else {}
    for key in ("self_html", "html", "self"):
        value = str(links.get(key) or "").strip()
        if value:
            return value
    doi_url = str(record.get("doi_url") or "").strip()
    if doi_url:
        return doi_url
    return f"https://zenodo.org/records/{record_id}" if record_id else ""


# ---------------------------------------------------------------------------
# Dataverse
# ---------------------------------------------------------------------------


def dataverse_search(
    query: str,
    limit: int,
    *,
    opener: Opener,
    settings: DataProviderSettings,
) -> ProviderResult:
    """Search Dataverse datasets, then list the files each dataset carries."""

    root = settings.dataverse_endpoint.rstrip("/")
    params = urllib.parse.urlencode(
        {"q": query, "type": "dataset", "per_page": str(limit)}
    )
    payload = _get_json(
        f"{root}/api/search?{params}",
        opener=opener,
        timeout=settings.timeout,
    )
    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
    items = [item for item in (data.get("items") or ()) if isinstance(item, Mapping)]
    result = ProviderResult()
    for index, item in enumerate(items):
        name = str(item.get("name") or "dataverse dataset").strip()
        persistent_id = str(item.get("global_id") or "").strip()
        landing = str(item.get("url") or "").strip()
        if not persistent_id:
            if landing:
                result.hits.append(
                    ProviderHit(
                        url=landing,
                        title=name,
                        provider="dataverse",
                        format_hint="html",
                        role=SourceRole.CATALOG,
                        trust=0.5,
                        relevance=_rank(index),
                        discovered_via="dataverse_search",
                        notes=("dataverse", "landing_page", "no_persistent_id"),
                    )
                )
            else:
                result.skipped.append(f"dataverse:{name} (no_url)")
            continue
        encoded = urllib.parse.quote(persistent_id, safe="")
        try:
            listing = _get_json(
                f"{root}/api/datasets/:persistentId?persistentId={encoded}",
                opener=opener,
                timeout=settings.timeout,
            )
        except ProviderError as exc:
            result.status = "partial"
            result.detail = f"{persistent_id}: {exc.code}"
            if landing:
                result.hits.append(
                    ProviderHit(
                        url=landing,
                        title=name,
                        provider="dataverse",
                        identifier=persistent_id,
                        format_hint="html",
                        role=SourceRole.CATALOG,
                        trust=0.5,
                        relevance=_rank(index),
                        discovered_via="dataverse_search",
                        notes=("dataverse", "landing_page", "file_listing_failed"),
                    )
                )
            continue
        files = _dataverse_files(listing)
        if not files:
            if landing:
                result.hits.append(
                    ProviderHit(
                        url=landing,
                        title=name,
                        provider="dataverse",
                        identifier=persistent_id,
                        format_hint="html",
                        role=SourceRole.CATALOG,
                        trust=0.5,
                        relevance=_rank(index),
                        discovered_via="dataverse_search",
                        notes=("dataverse", "landing_page", "no_files"),
                    )
                )
            continue
        for entry in files:
            filename = str(entry.get("filename") or "").strip()
            file_id = entry.get("id")
            size = _int(entry.get("filesize"))
            if file_id is None or not filename:
                result.skipped.append(f"dataverse:{persistent_id} (no_file_id)")
                continue
            if settings.max_bytes and size > settings.max_bytes:
                result.skipped.append(
                    f"dataverse:{persistent_id}/{filename} (too_large:{size})"
                )
                continue
            hint = _format_hint(filename)
            result.hits.append(
                ProviderHit(
                    url=f"{root}/api/access/datafile/{file_id}",
                    title=f"{name} [{filename}]",
                    provider="dataverse",
                    identifier=persistent_id,
                    format_hint=hint,
                    size=size,
                    role=_PROVIDER_ROLES.get(hint, SourceRole.CATALOG),
                    trust=_TRUST_REPOSITORY,
                    relevance=_rank(index),
                    discovered_via="dataverse_search",
                    notes=("dataverse", f"persistent_id:{persistent_id}"),
                )
            )
    if result.skipped and result.status == "ok":
        result.status = "partial"
        result.detail = f"{len(result.skipped)} file(s) skipped by the size budget"
    return result


def _dataverse_files(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
    version = (
        data.get("latestVersion")
        if isinstance(data.get("latestVersion"), Mapping)
        else {}
    )
    entries = [
        item for item in (version.get("files") or ()) if isinstance(item, Mapping)
    ]
    files: list[dict[str, Any]] = []
    for entry in entries:
        data_file = entry.get("dataFile")
        if not isinstance(data_file, Mapping):
            continue
        files.append(
            {
                "id": data_file.get("id"),
                "filename": (
                    data_file.get("filename")
                    or entry.get("label")
                    or entry.get("directoryLabel")
                ),
                "filesize": data_file.get("filesize"),
            }
        )
    return files


# ---------------------------------------------------------------------------
# OpenAlex
# ---------------------------------------------------------------------------


def openalex_search(
    query: str,
    limit: int,
    *,
    opener: Opener,
    settings: DataProviderSettings,
) -> ProviderResult:
    """Search OpenAlex works and return their best open-access PDF."""

    params: dict[str, str] = {"search": query, "per-page": str(limit)}
    if settings.mailto:
        params["mailto"] = settings.mailto
    payload = _get_json(
        f"{settings.openalex_endpoint}?{urllib.parse.urlencode(params)}",
        opener=opener,
        timeout=settings.timeout,
    )
    works = [
        item for item in (payload.get("results") or ()) if isinstance(item, Mapping)
    ]
    result = ProviderResult()
    for index, work in enumerate(works):
        title = str(work.get("display_name") or "").strip()
        doi = str(work.get("doi") or "").strip()
        published = str(work.get("publication_date") or "").strip()
        url, hint, refereed = _openalex_pdf(work)
        if not url:
            result.skipped.append(f"openalex:{doi or title or 'work'} (no_open_url)")
            continue
        trust = (
            _TRUST_JOURNAL if refereed else (_TRUST_PREPRINT if hint == "pdf" else 0.5)
        )
        notes = ["openalex"]
        open_access = work.get("open_access")
        if isinstance(open_access, Mapping) and open_access.get("oa_status"):
            notes.append(f"oa_status:{open_access['oa_status']}")
        result.hits.append(
            ProviderHit(
                url=url,
                title=title or doi or url,
                provider="openalex",
                identifier=doi or str(work.get("id") or ""),
                format_hint=hint,
                published=published,
                role=SourceRole.PRIMARY_PUBLISHED,
                trust=trust,
                relevance=_rank(index),
                discovered_via="openalex_search",
                notes=tuple(notes),
            )
        )
    return result


def _openalex_pdf(work: Mapping[str, Any]) -> tuple[str, str, bool]:
    """Return (url, format_hint, refereed) for the most machine-readable copy."""

    locations = [
        item for item in (work.get("locations") or ()) if isinstance(item, Mapping)
    ]
    open_access = (
        work.get("open_access") if isinstance(work.get("open_access"), Mapping) else {}
    )
    oa_url = str(open_access.get("oa_url") or "").strip()
    # Repository copies (arXiv, institutional archives) download far more
    # reliably than publisher PDFs, which often answer with 403 or an HTML
    # challenge.  Prefer them, then the journal copy, then the OA landing URL.
    repository_pdfs = [
        str(location.get("pdf_url") or "").strip()
        for location in locations
        if str(location.get("pdf_url") or "").strip()
        and _source_type(location) != "journal"
    ]
    if repository_pdfs:
        return repository_pdfs[0], "pdf", _has_journal_location(locations)
    for location in locations:
        pdf_url = str(location.get("pdf_url") or "").strip()
        if pdf_url:
            return pdf_url, "pdf", _has_journal_location(locations)
    if oa_url and _looks_like_pdf(oa_url):
        return oa_url, "pdf", _has_journal_location(locations)
    if oa_url:
        return oa_url, "html", _has_journal_location(locations)
    doi = str(work.get("doi") or "").strip()
    if doi:
        return doi, "html", _has_journal_location(locations)
    return "", "", False


def _source_type(location: Mapping[str, Any]) -> str:
    source = location.get("source")
    return str(source.get("type") or "") if isinstance(source, Mapping) else ""


def _has_journal_location(locations: Sequence[Mapping[str, Any]]) -> bool:
    return any(_source_type(location) == "journal" for location in locations)


def _looks_like_pdf(url: str) -> bool:
    lowered = url.casefold()
    return lowered.endswith(".pdf") or "/pdf" in lowered


# ---------------------------------------------------------------------------
# Shared plumbing
# ---------------------------------------------------------------------------


def _default_opener(request: urllib.request.Request, timeout: float) -> bytes:
    """Standard-library HTTP opener with structured failures."""

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = _http_error_detail(exc)
        raise ProviderError(f"http_{exc.code}", detail) from exc
    except urllib.error.URLError as exc:
        raise ProviderError("network_unreachable", str(exc.reason or exc)) from exc
    except TimeoutError as exc:
        raise ProviderError("timeout", "request timed out") from exc
    except OSError as exc:
        raise ProviderError("transport_error", str(exc)) from exc


def _http_error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", "replace")
    except OSError:
        return str(exc)
    try:
        payload = json.loads(body)
    except ValueError:
        return body[:200]
    if isinstance(payload, Mapping):
        for key in ("message", "error_msg", "error", "detail"):
            value = payload.get(key)
            if value:
                return str(value)
    return body[:200]


def _get_json(
    url: str,
    *,
    opener: Opener,
    timeout: float,
    headers: Mapping[str, str] | None = None,
) -> Mapping[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            **dict(headers or {}),
        },
    )
    try:
        raw = opener(request, timeout)
    except ProviderError:
        raise
    except OSError as exc:
        raise ProviderError("transport_error", str(exc)) from exc
    try:
        payload = json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProviderError("invalid_json", f"{url}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ProviderError("bad_payload", f"{url} returned a non-object payload")
    return payload


def _endpoint_of(name: str, settings: DataProviderSettings) -> str:
    if name == "zenodo":
        return settings.zenodo_endpoint
    if name == "dataverse":
        return settings.dataverse_endpoint
    if name == "openalex":
        return settings.openalex_endpoint
    return ""


def _format_hint(name: str) -> str:
    lowered = name.casefold().split("?", 1)[0].split("#", 1)[0]
    for suffix, hint in (
        (".pdf", "pdf"),
        (".csv", "csv"),
        (".tsv", "tsv"),
        (".xlsx", "xlsx"),
        (".xls", "xls"),
        (".jsonl", "jsonl"),
        (".json", "json"),
        (".zip", "zip"),
        (".fits", "fits"),
        (".dat", "dat"),
        (".tab", "tsv"),
        (".txt", "txt"),
        (".png", "png"),
        (".jpg", "jpg"),
        (".jpeg", "jpeg"),
        (".html", "html"),
    ):
        if lowered.endswith(suffix):
            return hint
    return "html"


def _rank(index: int) -> float:
    return max(0.1, 1.0 - 0.1 * index)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _unique(values: Sequence[str]) -> tuple[str, ...]:
    seen: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.append(text)
    return tuple(seen)


def _process_environ() -> Mapping[str, str]:
    import os

    return os.environ


_PROVIDERS: dict[str, Callable[..., ProviderResult]] = {
    "zenodo": zenodo_search,
    "dataverse": dataverse_search,
    "openalex": openalex_search,
}


def register_provider(
    name: str, search: Callable[..., ProviderResult], *, replace: bool = False
) -> None:
    """Register one more repository adapter under a stable name."""

    key = name.strip().lower()
    if not key:
        raise ValueError("provider name must not be empty")
    if key in _PROVIDERS and not replace:
        raise ValueError(f"provider already registered: {key}")
    _PROVIDERS[key] = search


def list_providers() -> tuple[str, ...]:
    return tuple(_PROVIDERS)


__all__ = [
    "DEFAULT_PROVIDERS",
    "DataProviderSettings",
    "Opener",
    "ProviderError",
    "ProviderHit",
    "ProviderResult",
    "dataverse_search",
    "discover_data_sources",
    "list_providers",
    "openalex_search",
    "register_provider",
    "resolve_provider_settings",
    "zenodo_search",
]
