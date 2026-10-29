"""Fetch and verify full text, then hand back page-level text.

Everything about this module is about not fooling ourselves: a 200 response is
not a paper, an ``application/pdf`` header is not a PDF, and a downloaded file
is not the file we analysed.  So each acquisition is content-addressed, hashed,
sniffed for a real ``%PDF`` header, and its extracted text is stored next to the
bytes.

The store is content-addressed on purpose: two URLs that serve the same bytes
cost one copy, and re-running a pipeline over a frozen snapshot reproduces the
same hashes.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .access import AccessPolicy
from .contracts import normalise_doi
from .transport import Transport

__all__ = [
    "FulltextResult",
    "describe_fulltext",
    "fetch_fulltext",
    "read_local_fulltext",
    "verify_fulltext",
]

_SLUG = re.compile(r"[^0-9a-z]+")
_PDF_MAGIC = b"%PDF"
_HTML_HINTS = (b"<!doctype", b"<html", b"<?xml")


@dataclass(slots=True)
class FulltextResult:
    """One downloaded (or supplied) full text, with its integrity facts."""

    url: str = ""
    path: str = ""
    sha256: str = ""
    byte_count: int = 0
    content_type: str = ""
    is_pdf: bool = False
    pages: tuple[str, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)
    doi: str = ""

    @property
    def available(self) -> bool:
        """Whether usable text came out of this document."""
        return self.is_pdf and any(page.strip() for page in self.pages)

    @property
    def page_count(self) -> int:
        """Number of pages in the text layer."""
        return len(self.pages)

    @property
    def characters(self) -> int:
        """Characters of extracted text."""
        return sum(len(page) for page in self.pages)

    @property
    def text(self) -> str:
        """Whole document text, page separated by a form feed."""
        return "\f".join(self.pages)

    def to_dict(self) -> dict[str, object]:
        """Plain JSON mapping (never includes the text itself)."""
        return {
            "url": self.url,
            "path": self.path,
            "sha256": self.sha256,
            "byte_count": self.byte_count,
            "content_type": self.content_type,
            "is_pdf": self.is_pdf,
            "page_count": self.page_count,
            "characters": self.characters,
            "warnings": list(self.warnings),
            "doi": self.doi,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> FulltextResult:
        """Rebuild from :meth:`to_dict` output plus an optional page cache."""
        pages = raw.get("pages")
        return cls(
            url=str(raw.get("url") or ""),
            path=str(raw.get("path") or ""),
            sha256=str(raw.get("sha256") or ""),
            byte_count=int(raw.get("byte_count") or 0),
            content_type=str(raw.get("content_type") or ""),
            is_pdf=bool(raw.get("is_pdf")),
            pages=tuple(str(item) for item in pages) if isinstance(pages, list) else (),
            warnings=tuple(str(item) for item in raw.get("warnings") or ()),
            doi=str(raw.get("doi") or ""),
        )


def fetch_fulltext(
    url: str,
    *,
    transport: Transport,
    policy: AccessPolicy,
    directory: str | Path,
    doi: str = "",
    timeout: float = 30.0,
) -> FulltextResult:
    """Download one candidate and verify it really is readable full text."""
    refusal = policy.refuse_reason(url)
    if refusal:
        return FulltextResult(
            url=url, doi=normalise_doi(doi), warnings=(f"refused:{refusal}",)
        )
    response = transport.get(
        url, headers={}, timeout=timeout, max_bytes=policy.max_fulltext_bytes
    )
    if response.error or not response.ok:
        return FulltextResult(
            url=url,
            doi=normalise_doi(doi),
            content_type=response.content_type,
            warnings=(
                f"download_failed:{response.error or 'http_' + str(response.status)}",
            ),
        )
    body = response.body or b""
    warnings: list[str] = []
    if not body:
        return FulltextResult(
            url=url,
            doi=normalise_doi(doi),
            content_type=response.content_type,
            warnings=("empty_body",),
        )
    if len(body) >= policy.max_fulltext_bytes:
        warnings.append("download_truncated_at_limit")
    is_pdf = body.startswith(_PDF_MAGIC)
    head = body[:512].lstrip().lower()
    if not is_pdf and head.startswith(_HTML_HINTS):
        warnings.append("html_instead_of_pdf")
    elif not is_pdf:
        warnings.append("not_a_pdf")
    digest = "sha256:" + hashlib.sha256(body).hexdigest()
    target = _store_path(directory, digest, is_pdf=is_pdf)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(body)
    result = FulltextResult(
        url=url,
        path=str(target),
        sha256=digest,
        byte_count=len(body),
        content_type=response.content_type,
        is_pdf=is_pdf,
        doi=normalise_doi(doi),
        warnings=tuple(warnings),
    )
    if is_pdf:
        pages, text_warnings = _extract_pages(target)
        result.pages = pages
        result.warnings = (*result.warnings, *text_warnings)
    return result


def read_local_fulltext(
    path: str | Path,
    *,
    directory: str | Path | None = None,
    doi: str = "",
    url: str = "",
) -> FulltextResult:
    """Adopt a file the operator supplied by hand (the top ladder rung)."""
    source = Path(path)
    if not source.is_file():
        return FulltextResult(
            url=url,
            path=str(source),
            doi=normalise_doi(doi),
            warnings=("file_missing",),
        )
    data = source.read_bytes()
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    is_pdf = data.startswith(_PDF_MAGIC)
    target = source
    if directory is not None:
        target = _store_path(directory, digest, is_pdf=is_pdf)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copyfile(source, target)
    warnings: list[str] = [] if is_pdf else ["not_a_pdf"]
    pages: tuple[str, ...] = ()
    if is_pdf:
        pages, text_warnings = _extract_pages(target)
        warnings.extend(text_warnings)
    return FulltextResult(
        url=url or f"file://{source}",
        path=str(target),
        sha256=digest,
        byte_count=len(data),
        content_type="application/pdf" if is_pdf else "",
        is_pdf=is_pdf,
        pages=pages,
        warnings=tuple(warnings),
        doi=normalise_doi(doi),
    )


def verify_fulltext(result: FulltextResult) -> bool:
    """Whether the bytes on disk still hash to what was analysed."""
    if not result.path or not result.sha256:
        return False
    target = Path(result.path)
    if not target.is_file():
        return False
    actual = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
    return actual == result.sha256


def describe_fulltext(result: FulltextResult) -> str:
    """One-line human summary of an acquisition."""
    if not result.path:
        reason = ", ".join(result.warnings) or "no attempt"
        return f"not acquired ({reason})"
    if not result.is_pdf:
        return f"downloaded {result.byte_count} bytes but it is not a PDF ({', '.join(result.warnings)})"
    return (
        f"{result.page_count} pages, {result.characters} characters, "
        f"{result.sha256[:19]}"
    )


def _store_path(directory: str | Path, digest: str, *, is_pdf: bool) -> Path:
    """Content-addressed storage path for one payload."""
    name = _SLUG.sub("", digest.split(":", 1)[-1].lower())[:32]
    suffix = ".pdf" if is_pdf else ".bin"
    return Path(directory) / "fulltext" / f"{name}{suffix}"


def _extract_pages(path: Path) -> tuple[tuple[str, ...], list[str]]:
    """Extract page text, degrading loudly when the text layer is missing."""
    from jw.scientific_data.documents import read_pdf_text

    parsed = read_pdf_text(path)
    warnings = [str(item) for item in parsed.warnings]
    if (
        not any(page.strip() for page in parsed.pages)
        and "no_text_layer" not in warnings
    ):
        warnings.append("no_text_layer")
    return tuple(parsed.pages), warnings
