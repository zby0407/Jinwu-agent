"""Bounded, task-local retrieval of explicitly identified public articles.

Downloaded text remains source evidence, never a scientific assessment. Only
articles with source identity and substantial article-body markers qualify;
metadata/abstract/landing/login pages remain explicit retrieval failures.
"""

from __future__ import annotations

import hashlib
import io
import ipaddress
import json
import re
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit

import httpx

MAX_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5
TOTAL_SECONDS = 20.0
MAX_TEXT_CHARS = 1_000_000
_REDIRECTS = {301, 302, 303, 307, 308}
_SECRET_QUERY = re.compile(
    r"(?:api[_-]?key|token|password|secret|signature|authorization|credential|^x-amz-)",
    re.IGNORECASE,
)
_REGISTERED_FULL_TEXT: dict[tuple[str, str], dict[str, str]] = {}


class ArticleRetrievalError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _safe_url(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 3000:
        raise ArticleRetrievalError("invalid_article_url")
    if any(ord(char) < 32 for char in value) or "\\" in value:
        raise ArticleRetrievalError("invalid_article_url")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ArticleRetrievalError("invalid_article_url") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 80, 443}
        or any(
            _SECRET_QUERY.search(key) or key.casefold() in {"key", "auth", "sig", "pwd"}
            for key, _ in parse_qsl(parsed.query)
        )
    ):
        raise ArticleRetrievalError("invalid_or_credentialed_article_url")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def _bounded_worker(code: str, payload: dict, timeout: float) -> dict:
    """DNS and parser work also have a killable wall-clock boundary."""
    if timeout <= 0:
        raise ArticleRetrievalError("retrieval_deadline_exceeded")
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            capture_output=True,
            cwd=Path(__file__).resolve().parents[1],
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise ArticleRetrievalError("retrieval_deadline_exceeded") from exc
    if result.returncode:
        raise ArticleRetrievalError("bounded_retrieval_worker_failed")
    try:
        parsed = json.loads(result.stdout)
    except (ValueError, UnicodeError) as exc:
        raise ArticleRetrievalError("bounded_retrieval_worker_failed") from exc
    if not parsed.get("ok"):
        raise ArticleRetrievalError(
            str(parsed.get("error") or "bounded_retrieval_worker_failed")
        )
    return parsed["result"]


def _resolve_public_target(url: str, timeout: float) -> tuple[str, str, str]:
    from .research_harness import _public_http_target

    # Literal addresses need no possibly blocking OS DNS operation.
    try:
        ipaddress.ip_address(urlsplit(url).hostname or "")
    except ValueError:
        code = (
            "import json,sys\n"
            "from jw.research_harness import _public_http_target\n"
            "try:\n"
            " value=_public_http_target(json.load(sys.stdin)['url'])\n"
            " print(json.dumps({'ok':True,'result':value}))\n"
            "except Exception:\n"
            " print(json.dumps({'ok':False,'error':'public_article_target_rejected'}))\n"
        )
        result = _bounded_worker(code, {"url": url}, timeout)
        if not isinstance(result, list) or len(result) != 3:
            raise ArticleRetrievalError("public_article_target_rejected") from None
        return tuple(result)
    return _public_http_target(url)


def _download_public_article(url: str, context: dict[str, Any]) -> dict[str, Any]:
    # Reuse the established DNS-all-public check and pinned-IP TLS transport.
    # A single monotonic deadline bounds redirects and incremental body reads.
    from .research_harness import _PinnedHTTPTransport

    current = _safe_url(url)
    # Stop initiating/reading at 15s, leaving one maximum 5s socket wait
    # inside the advertised 20s total envelope. DNS/extraction are killable.
    deadline = time.monotonic() + max(0.0, TOTAL_SECONDS - 5.0)
    redirects: list[dict[str, Any]] = []
    context.update({"redirect_chain": redirects, "network_requests": 0})
    headers = {
        "User-Agent": "JW-Literature/1.0 (task-scoped public article retrieval)",
        "Accept": "application/pdf,text/html,application/xhtml+xml,text/plain",
        "Accept-Encoding": "identity",
    }
    for hop in range(MAX_REDIRECTS + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ArticleRetrievalError("retrieval_deadline_exceeded")
        _, host, address = _resolve_public_target(current, remaining)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ArticleRetrievalError("retrieval_deadline_exceeded")
        with httpx.Client(
            transport=_PinnedHTTPTransport(address, expected_host=host),
            follow_redirects=False,
            trust_env=False,
            headers=headers,
            timeout=min(5.0, remaining),
        ) as client:
            context["network_requests"] += 1
            context["last_request_url"] = current
            with client.stream("GET", current, timeout=min(5.0, remaining)) as response:
                context["http_status"] = response.status_code
                if response.status_code in _REDIRECTS:
                    if hop == MAX_REDIRECTS:
                        raise ArticleRetrievalError("redirect_limit_exceeded")
                    location = response.headers.get("location", "")
                    if not location:
                        raise ArticleRetrievalError("redirect_location_missing")
                    destination = _safe_url(urljoin(current, location))
                    # Validate even the next redirect's DNS before recording it.
                    _resolve_public_target(destination, deadline - time.monotonic())
                    redirects.append(
                        {
                            "url": current,
                            "http_status": response.status_code,
                            "redirect_to": destination,
                        }
                    )
                    current = destination
                    continue
                if response.status_code in {401, 402, 403, 429}:
                    raise ArticleRetrievalError(
                        f"public_access_unavailable_http_{response.status_code}"
                    )
                if response.status_code != 200:
                    raise ArticleRetrievalError("article_http_status_not_200")
                content_type = (
                    response.headers.get("content-type", "")
                    .split(";", 1)[0]
                    .lower()
                    .strip()
                )
                if content_type not in {
                    "application/pdf",
                    "text/html",
                    "application/xhtml+xml",
                    "text/plain",
                }:
                    raise ArticleRetrievalError("unsupported_article_content_type")
                if response.headers.get("content-encoding", "identity").lower() not in {
                    "",
                    "identity",
                }:
                    raise ArticleRetrievalError("compressed_response_not_accepted")
                size_header = response.headers.get("content-length")
                if size_header:
                    try:
                        if int(size_header) > MAX_BYTES:
                            raise ArticleRetrievalError("article_byte_limit_exceeded")
                    except ValueError as exc:
                        if isinstance(exc, ArticleRetrievalError):
                            raise
                        raise ArticleRetrievalError("invalid_content_length") from exc
                chunks: list[bytes] = []
                count = 0
                for chunk in response.iter_raw(chunk_size=32 * 1024):
                    if time.monotonic() >= deadline:
                        raise ArticleRetrievalError("retrieval_deadline_exceeded")
                    count += len(chunk)
                    if count > MAX_BYTES:
                        raise ArticleRetrievalError("article_byte_limit_exceeded")
                    chunks.append(chunk)
                return {
                    "raw": b"".join(chunks),
                    "content_type": content_type,
                    "final_url": current,
                    "redirect_chain": redirects,
                    "http_status": 200,
                    "network_requests": context["network_requests"],
                }
    raise ArticleRetrievalError("redirect_limit_exceeded")


def _request_public_article(url: str) -> dict[str, Any]:
    context: dict[str, Any] = {}
    try:
        return _download_public_article(url, context)
    except Exception as exc:
        error = (
            exc
            if isinstance(exc, ArticleRetrievalError)
            else ArticleRetrievalError("public_article_transport_rejected")
        )
        error.retrieval_context = context
        if error is exc:
            raise
        raise error from exc


def _normalized(value: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", value.casefold(), re.UNICODE))


def _extract_article(raw: bytes, content_type: str, source: dict) -> dict:
    title = str(source.get("title") or "").strip()
    if len(_normalized(title).split()) < 3:
        raise ArticleRetrievalError("source_article_identity_insufficient")
    pdf_metadata: dict[str, str] = {}
    if content_type == "application/pdf":
        if not raw.startswith(b"%PDF-"):
            raise ArticleRetrievalError("pdf_magic_missing")
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ArticleRetrievalError("pdf_text_extractor_unavailable") from exc
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 100:
            raise ArticleRetrievalError("encrypted_or_overlong_pdf")
        pdf_metadata = {str(k): str(v) for k, v in (reader.metadata or {}).items()}
        pages = []
        text_count = 0
        for page in reader.pages:
            value = (page.extract_text() or "").strip()
            text_count += len(value)
            if text_count > MAX_TEXT_CHARS:
                raise ArticleRetrievalError("article_text_limit_exceeded")
            pages.append(value)
        if any(not text for text in pages):
            raise ArticleRetrievalError("pdf_requires_ocr_or_has_unread_pages")
        text = "\n\n".join(f"[page {i}]\n{value}" for i, value in enumerate(pages, 1))
        original_text = text
        format_name = "pdf_text"
        limits = [
            "PDF reading order, equations, tables and figures may be imperfect; no OCR performed."
        ]
    else:
        try:
            original_text = raw.decode("utf-8-sig")
        except UnicodeError as exc:
            raise ArticleRetrievalError("article_utf8_decoding_unsupported") from exc
        if content_type in {"text/html", "application/xhtml+xml"}:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(original_text, "html.parser")
            for element in soup(
                [
                    "script",
                    "style",
                    "noscript",
                    "nav",
                    "header",
                    "footer",
                    "aside",
                    "form",
                ]
            ):
                element.decompose()
            article = soup.find("article") or soup.find("main")
            if article is None:
                raise ArticleRetrievalError("html_article_body_not_identified")
            text = article.get_text("\n", strip=True)
            format_name = "html_article_text"
            limits = [
                "Only the identified article/main text is extracted; figures, scripts and external supplements are not read."
            ]
        else:
            text = original_text.strip()
            format_name = "plain_article_text"
            limits = [
                "Plain text only; figure content, typesetting and supplements are not verified."
            ]
    if not 1200 <= len(text) <= MAX_TEXT_CHARS:
        raise ArticleRetrievalError("article_body_missing_or_text_limit_exceeded")
    normalized_text = _normalized(text)
    title_terms = set(_normalized(title).split())
    title_match = len(title_terms & set(normalized_text.split())) / len(title_terms)
    if title_match < 0.8:
        raise ArticleRetrievalError("retrieved_article_title_mismatch")
    # Be conservative: an abstract or landing page cannot pass on length alone.
    sections = set(
        re.findall(
            r"(?im)^\s*(?:\d+(?:\.\d+)*[.)]?\s+)?(introduction|methods?|materials and methods|results?|discussion|conclusions?|references|bibliography)\s*[:.]?\s*$",
            text,
        )
    )
    sections = {section.casefold() for section in sections}
    if len(sections) < 3 or not (sections & {"references", "bibliography"}):
        raise ArticleRetrievalError("full_article_sections_not_identified")
    doi = str(source.get("doi") or "").strip().casefold()
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi)
    if doi and doi not in original_text.casefold():
        raise ArticleRetrievalError("retrieved_article_doi_mismatch")
    return {
        "text": text,
        "raw_text_for_validation": original_text,
        "pdf_metadata": pdf_metadata,
        "format": format_name,
        "article_identity_basis": {
            "cached_source_title": title,
            "title_term_overlap": title_match,
            "doi_matched": bool(doi),
            "article_sections": sorted(sections),
        },
        "extraction_limits": [
            *limits,
            "Article body markers and source identity matched; completeness and scientific claims still require independent source review.",
        ],
    }


def _extract_article_bounded(
    raw: bytes, content_type: str, source: dict, timeout: float
) -> dict:
    import base64

    code = (
        "import base64,json,sys\n"
        "from jw.literature_full_text import _extract_article,ArticleRetrievalError\n"
        "try:\n"
        " payload=json.load(sys.stdin)\n"
        " value=_extract_article(base64.b64decode(payload['raw']),payload['content_type'],payload['source'])\n"
        " print(json.dumps({'ok':True,'result':value},ensure_ascii=True))\n"
        "except Exception as exc:\n"
        " print(json.dumps({'ok':False,'error':exc.code if isinstance(exc,ArticleRetrievalError) else 'article_extraction_failed'}))\n"
    )
    return _bounded_worker(
        code,
        {
            "raw": base64.b64encode(raw).decode("ascii"),
            "content_type": content_type,
            "source": source,
        },
        timeout,
    )


def fetch_article_full_text(
    source: dict,
    *,
    article_url: str,
    directory: Path,
    binding_id: str,
    content_validator=None,
    task_workspace: Path | None = None,
    task_thread_id: str = "",
) -> dict:
    """Fetch once into a new task directory; never changes cached abstracts."""
    if (
        not isinstance(source, dict)
        or not source.get("source_id")
        or source.get("is_retracted")
    ):
        raise ArticleRetrievalError("cached_nonretracted_article_required")
    safe_url = _safe_url(article_url)
    attempt = directory / ("article_" + uuid.uuid4().hex[:16])
    attempt.mkdir(parents=True, exist_ok=False)
    receipt: dict[str, Any] = {
        "schema_version": "task-public-article-full-text-v1",
        "source_id": source["source_id"],
        "binding_id": binding_id,
        "requested_url": safe_url,
        "retrieved_utc": datetime.now(UTC).isoformat(),
        "reading_level": "not_retrieved",
        "full_text_retrieved": False,
        "evidence_scope": "none",
        "scientific_assessment_performed": False,
        "limits": {
            "maximum_bytes": MAX_BYTES,
            "maximum_redirects": MAX_REDIRECTS,
            "network_deadline_seconds": TOTAL_SECONDS,
        },
    }
    if task_workspace is not None:
        task_root = task_workspace.resolve(strict=True)
        task = json.loads((task_root / "task.json").read_text(encoding="utf-8"))
        if task.get("thread_id") != task_thread_id or not task.get("run_id"):
            raise ArticleRetrievalError("article_task_binding_mismatch")
        receipt.update(
            {
                "task_workspace": str(task_root),
                "task_thread_id": task_thread_id,
                "task_run_id": task["run_id"],
            }
        )
    deadline = time.monotonic() + TOTAL_SECONDS
    try:
        fetched = _request_public_article(safe_url)
        raw = fetched.pop("raw")
        receipt.update(fetched)
        receipt.update(
            {"raw_bytes": len(raw), "raw_sha256": hashlib.sha256(raw).hexdigest()}
        )
        article = _extract_article_bounded(
            raw, fetched["content_type"], source, deadline - time.monotonic()
        )
        if content_validator is not None:
            content_validator(
                {
                    "text": article["raw_text_for_validation"],
                    "metadata": article["pdf_metadata"],
                    "receipt": receipt,
                }
            )
        extension = {"application/pdf": ".pdf", "text/plain": ".txt"}.get(
            fetched["content_type"], ".html"
        )
        raw_path, text_path = (
            attempt / ("source" + extension),
            attempt / "extracted_text.txt",
        )
        raw_path.write_bytes(raw)
        text_bytes = article["text"].encode("utf-8")
        text_path.write_bytes(text_bytes)
        receipt.update(
            {
                "status": "ok",
                "reading_level": "retrieved_article_body_not_independently_reviewed",
                "full_text_retrieved": True,
                "evidence_scope": "article_body_text",
                "raw_path": str(raw_path),
                "path": str(text_path),
                "text_sha256": hashlib.sha256(text_bytes).hexdigest(),
                "text_bytes": len(text_bytes),
                "format": article["format"],
                "article_identity_basis": article["article_identity_basis"],
                "extraction_limits": article["extraction_limits"],
                "safe_next_action": "Read the extracted text and original source, cite exact locations, and preserve extraction/completeness limits; retrieval is not scientific acceptance.",
            }
        )
    except Exception as exc:
        receipt.update(getattr(exc, "retrieval_context", {}))
        receipt.update(
            {
                "status": "unavailable",
                "failure_kind": exc.code
                if isinstance(exc, ArticleRetrievalError)
                else "article_retrieval_or_extraction_failed",
                "safe_next_action": "Keep the full-text evidence gap. Do not bypass access controls or replace full text with metadata/abstracts.",
            }
        )
    receipt_path = attempt / "receipt.json"
    receipt["receipt_path"] = str(receipt_path)
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return receipt


def _regular_scoped_file(path: Path, root: Path) -> Path:
    root = root.absolute()
    path = path.absolute()
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise PermissionError("Article evidence escaped its current task") from exc
    cursor = root
    for part in (None, *relative.parts):
        if part is not None:
            cursor /= part
        if cursor.is_symlink() or (
            hasattr(cursor, "is_junction") and cursor.is_junction()
        ):
            raise PermissionError("Article evidence cannot follow linked paths")
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise PermissionError("Article evidence is missing or outside its task")
    return path.resolve()


def register_successful_full_text(workspace_root: Path, receipt_path: Path) -> dict:
    """Host-only immutable registration in the live task process."""
    root = workspace_root.resolve(strict=True)
    target = root / "literature/full_text"
    path = _regular_scoped_file(receipt_path, target)
    relative = path.relative_to(target)
    if len(relative.parts) != 2 or path.name != "receipt.json":
        raise PermissionError("Article receipt has no canonical current-task path")
    receipt = json.loads(path.read_text(encoding="utf-8"))
    task_path = _regular_scoped_file(root / "task.json", root)
    task = json.loads(task_path.read_text(encoding="utf-8"))
    if (
        receipt.get("status") != "ok"
        or receipt.get("full_text_retrieved") is not True
        or receipt.get("task_workspace") != str(root)
        or receipt.get("task_thread_id") != task.get("thread_id")
        or receipt.get("task_run_id") != task.get("run_id")
    ):
        raise PermissionError("Article receipt does not match the current task binding")
    registration = root / "receipts/literature_full_text" / (path.parent.name + ".json")
    relative_registration = registration.relative_to(root)
    cursor = root
    for part in relative_registration.parts:
        cursor /= part
        if cursor.is_symlink() or (
            hasattr(cursor, "is_junction") and cursor.is_junction()
        ):
            raise PermissionError("Article registration cannot follow linked paths")
    registration.parent.mkdir(parents=True, exist_ok=True)

    item = {
        "receipt_path": str(path),
        "receipt_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "binding_id": receipt["binding_id"],
        "source_id": receipt["source_id"],
        "task_workspace": str(root),
        "task_run_id": task["run_id"],
        "task_thread_id": task["thread_id"],
    }
    with registration.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(item, ensure_ascii=False, indent=2) + "\n")
    _REGISTERED_FULL_TEXT[(str(root), str(path))] = {
        "receipt_sha256": item["receipt_sha256"],
        "registration_path": str(registration),
        "registration_sha256": hashlib.sha256(registration.read_bytes()).hexdigest(),
    }
    # Validate all actual raw/text bytes before returning a canonical source.
    verified_full_text_sources(root)
    return {
        "receipt_sha256": item["receipt_sha256"],
        "registration_path": str(registration),
    }


def _verified_full_text_sources(workspace_root: Path) -> list[Path]:
    """Return only successful immutable receipt/text pairs for this live task.

    Failed/unregistered fetch attempts are omitted. Every registered artifact
    fails closed on missing files, tampering, links or a different task. The
    process hash anchor intentionally cannot serve as a cross-process resume.
    Range-read receipts are excluded so reading cannot stale accepted Data.
    """
    root = workspace_root.resolve(strict=True)
    registrations = root / "receipts/literature_full_text"
    if not registrations.exists():
        return []
    task = json.loads(
        _regular_scoped_file(root / "task.json", root).read_text(encoding="utf-8")
    )
    sources: list[Path] = []
    for registration in sorted(registrations.glob("*.json")):
        registration = _regular_scoped_file(registration, root)
        item = json.loads(registration.read_text(encoding="utf-8"))
        path = _regular_scoped_file(
            Path(item.get("receipt_path", "")), root / "literature/full_text"
        )
        anchor = _REGISTERED_FULL_TEXT.get((str(root), str(path)))
        if not anchor or anchor["registration_path"] != str(registration):
            raise PermissionError(
                "Article evidence lacks its current live host registration"
            )
        if (
            hashlib.sha256(registration.read_bytes()).hexdigest()
            != anchor["registration_sha256"]
            or hashlib.sha256(path.read_bytes()).hexdigest() != anchor["receipt_sha256"]
            or item.get("receipt_sha256") != anchor["receipt_sha256"]
        ):
            raise PermissionError("Article registration or receipt hash changed")
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if (
            receipt.get("status") != "ok"
            or receipt.get("full_text_retrieved") is not True
            or receipt.get("task_workspace") != str(root)
            or receipt.get("task_thread_id") != task.get("thread_id")
            or receipt.get("task_run_id") != task.get("run_id")
            or receipt.get("binding_id") != item.get("binding_id")
            or receipt.get("source_id") != item.get("source_id")
            or item.get("task_workspace") != str(root)
            or item.get("task_run_id") != task.get("run_id")
            or item.get("task_thread_id") != task.get("thread_id")
        ):
            raise PermissionError(
                "Registered article belongs to a different or unsuccessful task"
            )
        for key, hash_key, bytes_key in [
            ("raw_path", "raw_sha256", "raw_bytes"),
            ("path", "text_sha256", "text_bytes"),
        ]:
            artifact = _regular_scoped_file(Path(receipt.get(key, "")), path.parent)
            raw = artifact.read_bytes()
            if (
                artifact.parent != path.parent
                or len(raw) != receipt.get(bytes_key)
                or hashlib.sha256(raw).hexdigest() != receipt.get(hash_key)
            ):
                raise PermissionError(
                    "Registered article bytes no longer match their receipt"
                )
            if key == "path":
                sources.extend([path, artifact])
    return sources


def verified_full_text_sources(workspace_root: Path) -> list[Path]:
    """Validate current-task registrations; every malformed item fails closed."""
    try:
        return _verified_full_text_sources(workspace_root)
    except PermissionError:
        raise
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise PermissionError(
            "Registered article evidence is malformed or unavailable"
        ) from exc
