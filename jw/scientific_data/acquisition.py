"""Acquisition: fetch local files or allowlisted URLs, once, with a budget.

Every fetch is bounded by the run policy and by the contract budget, is stored
under a content-addressed name, and leaves either an acquired source or an
explicit failure issue behind. Nothing here parses data; it only moves bytes and
records where they came from.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit

from .contracts import (
    DataRequirementContract,
    IssueCode,
    IssueSeverity,
    QualityIssue,
    RetrievalState,
    SourceRecord,
    file_digest,
)
from .policy import AcquisitionPolicy, PolicyDecision
from .sources import SourceCatalog

__all__ = [
    "AcquisitionReport",
    "FetchResult",
    "Fetcher",
    "LocalFileFetcher",
    "acquire",
]

_MAX_EXTENSION = 8

#: Characters reserved by Windows file names; source ids may contain colons.
_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(frozen=True, slots=True)
class FetchResult:
    """The outcome of one fetch attempt."""

    source_id: str
    url: str
    status: str
    http_status: int = 0
    content_type: str = ""
    path: str = ""
    sha256: str = ""
    size: int = 0
    error: str = ""
    from_cache: bool = False

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "url": self.url,
            "status": self.status,
            "http_status": self.http_status,
            "content_type": self.content_type,
            "path": self.path,
            "sha256": self.sha256,
            "size": self.size,
            "error": self.error,
            "from_cache": self.from_cache,
        }


class Fetcher(Protocol):
    """Minimal fetch interface so tests can run without a network."""

    def fetch(self, url: str, *, policy: AcquisitionPolicy) -> tuple[int, bytes, str]:
        """Return ``(status_code, body, content_type)`` for ``url``."""
        ...


class LocalFileFetcher:
    """Read ``file://`` URLs and plain paths from disk."""

    def fetch(self, url: str, *, policy: AcquisitionPolicy) -> tuple[int, bytes, str]:
        path = _local_path(url)
        if not path.exists():
            raise FileNotFoundError(f"no such file: {path}")
        if not path.is_file():
            raise IsADirectoryError(f"not a file: {path}")
        return 200, path.read_bytes(), _content_type(path)


class HttpFetcher:
    """Fetch over HTTP(S) with the run's user agent and timeout."""

    def __init__(self, *, client: Any = None) -> None:
        self._client = client

    def fetch(self, url: str, *, policy: AcquisitionPolicy) -> tuple[int, bytes, str]:
        if self._client is not None:
            response = self._client.get(url, timeout=policy.timeout_seconds)
            return (
                int(response.status_code),
                bytes(response.content),
                str(response.headers.get("content-type", "")),
            )
        import httpx  # imported lazily so offline runs never need the transport

        with httpx.Client(
            timeout=policy.timeout_seconds,
            follow_redirects=True,
            headers={"user-agent": policy.user_agent},
        ) as client:
            response = client.get(url)
            return (
                int(response.status_code),
                bytes(response.content),
                str(response.headers.get("content-type", "")),
            )


@dataclass(frozen=True, slots=True)
class AcquisitionReport:
    """Everything the acquisition stage learned."""

    catalog: SourceCatalog
    results: tuple[FetchResult, ...] = ()
    issues: tuple[QualityIssue, ...] = ()
    stats: Mapping[str, Any] = field(default_factory=dict)

    @property
    def fetched(self) -> tuple[FetchResult, ...]:
        return tuple(result for result in self.results if result.ok)

    @property
    def failed(self) -> tuple[FetchResult, ...]:
        return tuple(
            result
            for result in self.results
            if result.status not in {"ok", "skipped_budget"}
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "stats": dict(self.stats),
            "results": [result.to_dict() for result in self.results],
            "issues": [issue.issue_id for issue in self.issues],
        }


def acquire(
    catalog: SourceCatalog,
    *,
    policy: AcquisitionPolicy,
    workdir: str | Path,
    fetcher: Fetcher | None = None,
    contract: DataRequirementContract | None = None,
) -> AcquisitionReport:
    """Fetch every pending source the policy allows, within budget."""
    directory = Path(workdir) / "sources"
    directory.mkdir(parents=True, exist_ok=True)
    resolved_fetcher = fetcher or _default_fetcher(policy)
    budget = policy.max_fetches
    if contract is not None:
        budget = min(budget, int(contract.budget.get("max_fetches", budget)))

    results: list[FetchResult] = []
    issues: list[QualityIssue] = []
    seen_digests: dict[str, str] = {}
    attempts = 0

    for record in catalog.pending():
        if attempts >= budget:
            results.append(
                FetchResult(
                    source_id=record.source_id,
                    url=record.url,
                    status="skipped_budget",
                    error=f"fetch budget of {budget} reached",
                )
            )
            catalog.mark(
                record.source_id,
                RetrievalState.SKIPPED,
                failure_reason=f"fetch_budget_reached:{budget}",
            )
            continue
        decision = policy.allows(record.url or record.canonical_uri)
        if not decision.allowed:
            results.append(
                FetchResult(
                    source_id=record.source_id,
                    url=record.url,
                    status="denied",
                    error=decision.reason,
                )
            )
            catalog.mark(
                record.source_id,
                RetrievalState.SKIPPED,
                failure_reason=decision.reason,
                note=f"policy:{decision.reason}",
            )
            issues.append(_policy_issue(record, decision))
            continue
        attempts += 1
        result = _fetch_one(
            record,
            policy=policy,
            directory=directory,
            fetcher=resolved_fetcher,
            seen_digests=seen_digests,
        )
        results.append(result)
        if result.ok:
            catalog.mark(
                record.source_id,
                RetrievalState.ACQUIRED,
                local_path=result.path,
                content_sha256=result.sha256,
                size=result.size,
                note=f"acquired:{result.content_type or 'unknown'}",
            )
        else:
            catalog.mark(
                record.source_id,
                RetrievalState.FAILED,
                failure_reason=result.error,
                note="unreadable_source",
            )
            issues.append(_failure_issue(record, result))

    stats: dict[str, Any] = {
        "sources": len(catalog),
        "attempts": attempts,
        "acquired": len([item for item in results if item.ok]),
        "failed": len([item for item in results if item.status == "failed"]),
        "denied": len([item for item in results if item.status == "denied"]),
        "skipped_budget": len(
            [item for item in results if item.status == "skipped_budget"]
        ),
        "duplicate_payloads": len(
            [item for item in results if item.status == "duplicate_content"]
        ),
        "fetch_budget": budget,
        "policy": policy.to_dict(),
    }
    return AcquisitionReport(
        catalog=catalog, results=tuple(results), issues=tuple(issues), stats=stats
    )


def _default_fetcher(policy: AcquisitionPolicy) -> Fetcher:
    return HttpFetcher() if policy.allow_network else LocalFileFetcher()


def _fetch_one(
    record: SourceRecord,
    *,
    policy: AcquisitionPolicy,
    directory: Path,
    fetcher: Fetcher,
    seen_digests: dict[str, str],
) -> FetchResult:
    """Fetch one source and store it under a content-addressed name."""
    url = record.url or record.canonical_uri
    try:
        status, body, content_type = fetcher.fetch(url, policy=policy)
    except Exception as error:  # a fetch failure must never abort the run
        return FetchResult(
            source_id=record.source_id,
            url=url,
            status="failed",
            error=f"{type(error).__name__}: {error}",
        )
    if status >= 400:
        return FetchResult(
            source_id=record.source_id,
            url=url,
            status="failed",
            http_status=status,
            error=f"http_{status}",
        )
    if len(body) > policy.max_bytes:
        return FetchResult(
            source_id=record.source_id,
            url=url,
            status="failed",
            http_status=status,
            size=len(body),
            error=f"too_large:{len(body)}>{policy.max_bytes}",
        )
    digest = _digest(body)
    if digest in seen_digests:
        return FetchResult(
            source_id=record.source_id,
            url=url,
            status="duplicate_content",
            http_status=status,
            sha256=digest,
            size=len(body),
            path=seen_digests[digest],
            from_cache=True,
        )
    target = directory / (
        f"{_filename_stem(record.source_id)}__"
        f"{digest[7:19]}{_extension(url, content_type)}"
    )
    existed = target.exists()
    if not existed:
        target.write_bytes(body)
    seen_digests[digest] = str(target)
    return FetchResult(
        source_id=record.source_id,
        url=url,
        status="ok",
        http_status=status,
        content_type=content_type,
        path=str(target),
        sha256=digest,
        size=len(body),
        from_cache=existed,
    )


def _digest(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _filename_stem(source_id: str) -> str:
    """Turn a ``src:local:001`` style id into a name Windows will accept.

    Colons and the other reserved characters are legal in a URL but not in a
    Windows file name, so the content-addressed store replaces them.
    """
    cleaned = _UNSAFE_FILENAME.sub("_", source_id)
    return cleaned.rstrip(" .") or "source"


def _extension(url: str, content_type: str) -> str:
    if url.lower().startswith("file:"):
        candidate = _local_path(url)
    else:
        candidate = Path(unquote(urlsplit(url).path))
    suffix = candidate.suffix.lstrip(".")
    if suffix and len(suffix) <= _MAX_EXTENSION:
        return f".{suffix.lower()}"
    hinted = {
        "text/csv": ".csv",
        "text/tab-separated-values": ".tsv",
        "text/html": ".html",
        "application/json": ".json",
        "application/pdf": ".pdf",
        "image/png": ".png",
        "image/jpeg": ".jpg",
    }.get(content_type.split(";")[0].strip().lower(), "")
    return hinted or ".bin"


def _local_path(url: str) -> Path:
    if url.startswith("file://"):
        return Path(unquote(url[len("file://") :]))
    return Path(unquote(url))


def _content_type(path: Path) -> str:
    return {
        ".csv": "text/csv",
        ".tsv": "text/tab-separated-values",
        ".html": "text/html",
        ".htm": "text/html",
        ".md": "text/markdown",
        ".json": "application/json",
        ".jsonl": "application/jsonl",
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }.get(path.suffix.lower(), "application/octet-stream")


def _policy_issue(record: SourceRecord, decision: PolicyDecision) -> QualityIssue:
    """A refused fetch is a policy finding, not a silent skip."""
    return QualityIssue(
        issue_id=f"issue:acquisition:source_host_denied:{record.source_id}",
        code=IssueCode.SOURCE_HOST_DENIED,
        severity=IssueSeverity.ERROR,
        stage="acquisition",
        subject={"source_id": record.source_id, "host": decision.host},
        detail=(
            f"{record.url or record.canonical_uri} was not fetched because "
            f"{decision.reason.replace('_', ' ')}."
        ),
        evidence=decision.to_dict(),
        proposed_action="Either allow this host explicitly or confirm the gap.",
    )


def _failure_issue(record: SourceRecord, result: FetchResult) -> QualityIssue:
    return QualityIssue(
        issue_id=f"issue:acquisition:unreadable_source:{record.source_id}",
        code=IssueCode.UNREADABLE_SOURCE,
        severity=IssueSeverity.ERROR,
        stage="acquisition",
        subject={"source_id": record.source_id, "url": result.url},
        detail=f"{result.url} could not be read: {result.error}.",
        evidence=result.to_dict(),
        proposed_action="Retry the fetch or replace this source with another copy.",
    )


def verify_sources(
    catalog: SourceCatalog, *, workdir: str | Path | None = None
) -> tuple[str, ...]:
    """Check that every acquired file is still on disk with its recorded digest.

    Returns the ids of sources whose bytes changed, which the reproducibility
    report must surface.
    """
    drifted: list[str] = []
    for record in catalog.to_records():
        if record.retrieval is not RetrievalState.ACQUIRED or not record.local_path:
            continue
        path = Path(record.local_path)
        if not path.exists():
            drifted.append(record.source_id)
            continue
        if record.content_sha256 and file_digest(path) != record.content_sha256:
            drifted.append(record.source_id)
    return tuple(drifted)


def source_texts(catalog: SourceCatalog) -> dict[str, str]:
    """Map source id to local path for the extraction stage."""
    return {
        record.source_id: record.local_path
        for record in catalog.acquired()
        if record.local_path
    }


def describe_acquisition(report: AcquisitionReport) -> Sequence[str]:
    """One line per fetch, for the report appendix."""
    lines: list[str] = []
    for result in report.results:
        state = result.status
        detail = result.error or result.sha256 or ""
        lines.append(f"- `{result.source_id}` {state}: {detail}".rstrip())
    return tuple(lines)
