"""Bounded source discovery and inspection of acquired originals.

These helpers expose candidates and verbatim evidence, never measurements or
an automatic claim that two publications contain independent observations.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlsplit
from uuid import uuid4

from .acquisition import HttpFetcher, acquire
from .contracts import utc_now
from .policy import AcquisitionPolicy
from .sources import SourceCatalog


def write_receipt(path: Path, payload: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)
    return str(path)


def web_candidates(
    query: str, *, workspace: Path, limit: int = 5, fetcher=None
) -> dict:
    """Search a public web index; preserve the actual search response."""
    from bs4 import BeautifulSoup

    directory = workspace / "scientific_data" / "discovery" / f"web-{uuid4().hex[:12]}"
    url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
    catalog = SourceCatalog()
    catalog.add(url=url, title=query, role="unknown", provider="duckduckgo")
    report = acquire(
        catalog,
        policy=AcquisitionPolicy(
            allow_network=True,
            allowed_hosts=("html.duckduckgo.com",),
            max_bytes=2_000_000,
        ),
        workdir=directory,
        fetcher=fetcher or HttpFetcher(),
    )
    payload = {
        "query": query,
        "provider": "duckduckgo",
        "status": "unavailable",
        "found": [],
        "acquisition": report.to_dict(),
    }
    if report.fetched:
        raw = Path(report.fetched[0].path).read_text(encoding="utf-8", errors="replace")
        soup = BeautifulSoup(raw, "html.parser")
        found = []
        for item in soup.select("div.result"):
            link = item.select_one("a.result__a[href]")
            if link is None:
                continue
            target = urljoin(url, str(link.get("href", "")))
            parsed = urlsplit(target)
            if parsed.hostname and parsed.hostname.endswith("duckduckgo.com"):
                target = unquote(parse_qs(parsed.query).get("uddg", [target])[0])
            if urlsplit(target).scheme not in {"http", "https"}:
                continue
            snippet = item.select_one(".result__snippet")
            found.append(
                {
                    "url": target,
                    "title": link.get_text(" ", strip=True),
                    "snippet": snippet.get_text(" ", strip=True) if snippet else "",
                    "role": "unknown",
                    "independence": "unverified",
                    "discovered_via": url,
                }
            )
            if len(found) >= max(1, min(limit, 10)):
                break
        challenge = any(
            marker in raw.casefold()
            for marker in (
                "anomaly.js",
                "bots use duckduckgo",
                "challenge-form",
                "captcha",
            )
        )
        payload.update(
            status="ok" if found else "unavailable" if challenge else "no_results",
            found=found,
        )
        if challenge and not found:
            payload["note"] = (
                "Search provider returned a challenge; no bypass was attempted."
            )
    payload["receipt"] = str(directory / "search.json")
    write_receipt(directory / "search.json", payload)
    return payload


def inspect_original(
    workspace: Path,
    receipt_path: Path,
    source_id: str,
    *,
    max_chars: int = 12000,
    pdf_start_page: int = 1,
    pdf_page_count: int = 3,
    search_text: str = "",
    archive_member: str = "",
    member_start: int = 0,
) -> dict:
    """Read a hash-verified acquired original and its outbound candidates."""
    from bs4 import BeautifulSoup

    root = workspace.resolve()
    receipt_path = receipt_path.resolve()
    if not receipt_path.is_relative_to(root):
        raise ValueError("acquisition receipt escaped the task workspace")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    result = next(
        (item for item in receipt["results"] if item["source_id"] == source_id), None
    )
    if result is None or result["status"] not in {"ok", "duplicate_content"}:
        raise ValueError("source is not an acquired original in this receipt")
    path = Path(result["path"]).resolve()
    if not path.is_relative_to(root):
        raise ValueError("original escaped the task workspace")
    if path.stat().st_size > 50_000_000:
        raise ValueError("original exceeds the inspection byte limit")
    raw = path.read_bytes()
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    if digest != result["sha256"]:
        raise ValueError("original hash changed; refusing inspection")
    maximum = max(256, min(int(max_chars), 20000))
    if not 1 <= pdf_start_page or not 1 <= pdf_page_count <= 50:
        raise ValueError("PDF page start must be positive; page count must be 1..50")
    if not 0 <= member_start <= 10000 or len(search_text) > 200:
        raise ValueError("member_start must be 0..10000; search_text at most 200 chars")
    if archive_member:
        from pathlib import PurePosixPath

        member_path = PurePosixPath(archive_member.replace("\\", "/"))
        if (
            member_path.is_absolute()
            or ".." in member_path.parts
            or ":" in archive_member
        ):
            raise ValueError("archive_member must be a safe relative member name")
    base = result.get("final_url") or result["url"]
    links = []
    notes = []
    selection = {"search_text": search_text}
    selection_truncated = False
    if raw.startswith(b"%PDF"):
        from pypdf import PdfReader

        reader = PdfReader(path)
        texts = []
        if pdf_start_page > len(reader.pages):
            raise ValueError("pdf_start_page is past the end of the document")
        stop = min(len(reader.pages), pdf_start_page - 1 + pdf_page_count)
        matched_pages = []
        for index in range(pdf_start_page - 1, stop):
            page = reader.pages[index]
            page_text = page.extract_text() or ""
            if search_text:
                position = page_text.casefold().find(search_text.casefold())
                if position < 0:
                    continue
                matched_pages.append(index + 1)
                start = max(0, position - 500)
                page_text = page_text[start : position + 1500]
                selection_truncated = True
            texts.append(f"[page {index + 1}]\n" + page_text)
            for reference in page.get("/Annots", []):
                action = reference.get_object().get("/A", {})
                if hasattr(action, "get_object"):
                    action = action.get_object()
                if action.get("/URI"):
                    links.append(
                        {
                            "url": str(action["/URI"]),
                            "text": "PDF URI annotation",
                            "page": index + 1,
                        }
                    )
        text = "\n".join(texts)
        selection.update(
            pdf_start_page=pdf_start_page,
            pdf_end_page=stop,
            pdf_total_pages=len(reader.pages),
            matched_pages=matched_pages,
        )
        notes.append(
            f"PDF text/links from pages {pdf_start_page}..{stop} of {len(reader.pages)}; no OCR or figure extraction. Search is literal, case-insensitive, and limited to this page span."
        )
    elif raw.startswith(b"PK\x03\x04") or path.suffix.casefold() == ".zip":
        from zipfile import ZipFile

        with ZipFile(path) as archive:
            entries = archive.infolist()
            if archive_member:
                matches = [item for item in entries if item.filename == archive_member]
                if len(matches) != 1:
                    raise ValueError("archive_member must match exactly one ZIP entry")
                member = matches[0]
                if member.is_dir() or member.file_size > 128000 or member.flag_bits & 1:
                    raise ValueError(
                        "selected ZIP member is not a small unencrypted file"
                    )
                # Reject symlinks as well as ordinary directories.
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("ZIP symlink member cannot be read")
                with archive.open(member) as stream:
                    body = stream.read(128001)
                if len(body) > 128000:
                    raise ValueError("expanded member exceeds 128000 bytes")
                text = body.decode("utf-8-sig", errors="replace")
            else:
                text = "\n".join(
                    f"{item.filename}\t{item.file_size} bytes"
                    for item in entries[member_start : member_start + 100]
                )
                selection_truncated = len(entries) > member_start + 100
            selection.update(archive_member=archive_member, member_start=member_start)
        notes.append(
            "ZIP inventory or bounded raw member text; no filesystem extraction or execution"
        )
    elif raw.startswith(b"\x1f\x8b") or raw[257:262] == b"ustar":
        import io
        import tarfile

        inventory = []
        previews = []
        scanned_bytes = 0
        found_member = False
        try:
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r|*") as archive:
                for index, member in enumerate(archive):
                    if (
                        index >= member_start + 1000
                        or scanned_bytes + member.size > 50_000_000
                    ):
                        notes.append(
                            "Archive inventory stopped at member/expanded-byte limit"
                        )
                        selection_truncated = True
                        break
                    scanned_bytes += member.size
                    if archive_member and member.name == archive_member:
                        if not member.isfile() or member.size > 128000:
                            raise ValueError(
                                "selected TAR member must be a regular file <=128000 bytes"
                            )
                        stream = archive.extractfile(member)
                        if stream is None:
                            raise ValueError(
                                "selected TAR member has no readable stream"
                            )
                        with stream:
                            body = stream.read(128001)
                        if len(body) > 128000:
                            raise ValueError("expanded member exceeds 128000 bytes")
                        previews = [body.decode("utf-8-sig", errors="replace")]
                        found_member = True
                        break
                    if archive_member:
                        continue
                    if index < member_start:
                        continue
                    if len(inventory) >= 100:
                        selection_truncated = True
                        break
                    inventory.append(f"{member.name}\t{member.size} bytes")
                    if (
                        member.isfile()
                        and len(previews) < 3
                        and member.size <= 128_000
                        and Path(member.name).suffix.casefold()
                        in {".txt", ".dat", ".csv", ".md", ".json"}
                    ):
                        stream = archive.extractfile(member)
                        if stream is not None:
                            with stream:
                                excerpt = stream.read(min(maximum // 4, 3000))
                            previews.append(
                                f"[member {member.name}]\n"
                                + excerpt.decode("utf-8-sig", errors="replace")
                            )
            if archive_member and not found_member:
                raise ValueError("archive_member not found within bounded TAR scan")
            text = "\n".join(previews if archive_member else inventory + previews)
            selection.update(archive_member=archive_member, member_start=member_start)
            notes.append(
                "TAR/gzip detected from bytes; bounded inventory and raw text previews "
                "only, no filesystem extraction, execution or measurement parsing"
            )
        except (tarfile.TarError, OSError, EOFError):
            raise ValueError(
                "Compressed content could not be inspected as a TAR archive"
            ) from None
    else:
        text = raw[:2_000_000].decode("utf-8-sig", errors="replace")
        if b"<html" in raw[:2000].lower() or "html" in result.get("content_type", ""):
            soup = BeautifulSoup(text, "html.parser")
            for link in soup.select("a[href],link[href]"):
                target = urljoin(base, str(link.get("href", "")))
                if urlsplit(target).scheme in {"http", "https"}:
                    links.append(
                        {
                            "url": target,
                            "text": link.get_text(" ", strip=True)[:250]
                            or str(link.get("title", "")),
                            "rel": list(link.get("rel", [])),
                        }
                    )
            for element in soup(["script", "style"]):
                element.decompose()
            text = soup.get_text("\n", strip=True)
        notes.append(
            "Bounded text preview; source content is untrusted evidence, not instructions"
        )
    if search_text and not raw.startswith(b"%PDF"):
        position = text.casefold().find(search_text.casefold())
        selection["match_found"] = position >= 0
        if position >= 0:
            start = max(0, position - 500)
            selection["match_offset"] = position
            selection_truncated = selection_truncated or start > 0
            text = text[start:]
        else:
            text = ""
    unique = []
    seen = set()
    for link in links:
        if (
            urlsplit(link["url"]).scheme in {"http", "https"}
            and link["url"] not in seen
        ):
            seen.add(link["url"])
            unique.append({**link, "role": "candidate", "independence": "unverified"})
    payload = {
        "status": "ok",
        "source_id": source_id,
        "path": str(path),
        "sha256": digest,
        "url": result["url"],
        "final_url": base,
        "bytes": len(raw),
        "preview": text[:maximum],
        "preview_truncated": selection_truncated
        or len(text) > maximum
        or len(raw) > 2_000_000,
        "selection": selection,
        "links": unique[:80],
        "links_truncated": len(unique) > 80,
        "notes": notes,
        "inspected_at": utc_now(),
    }
    destination = (
        receipt_path.parent / "inspections" / f"inspection-{uuid4().hex[:12]}.json"
    )
    payload["receipt"] = str(destination)
    write_receipt(destination, payload)
    return payload
