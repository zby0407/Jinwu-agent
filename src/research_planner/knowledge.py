"""Read-only knowledge retrieval behind the Pi Research Planner Tools."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from research_layout import PLANNER_RESOURCE_ROOT, PROJECT_ROOT

from .contracts import ContractError

KNOWLEDGE_ROOT = PLANNER_RESOURCE_ROOT / "knowledge"
OPENALEX_WORKS_URL = "https://api.openalex.org/works"
MAX_REMOTE_BYTES = 512 * 1024
MAX_LOCAL_EVIDENCE_BYTES = 2 * 1024 * 1024
MAX_DATASET_BYTES = 8 * 1024 * 1024


def _bounded_text(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{label} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ContractError(f"{label} exceeds {maximum} characters")
    return normalized


def _bounded_int(value: object, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"{label} must be an integer")
    if not minimum <= value <= maximum:
        raise ContractError(f"{label} must be in [{minimum}, {maximum}]")
    return value


def _safe_local_path(value: object, label: str, maximum_bytes: int) -> Path:
    raw = _bounded_text(value, label, 1_000)
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    resolved = candidate.resolve()
    root = PROJECT_ROOT.resolve()
    if resolved != root and root not in resolved.parents:
        raise ContractError(f"{label} must stay inside the Research Planner directory")
    if not resolved.is_file():
        raise ContractError(f"{label} does not identify a readable file")
    size = resolved.stat().st_size
    if size > maximum_bytes:
        raise ContractError(f"{label} exceeds the {maximum_bytes} byte limit")
    return resolved


def _fetch_json(url: str, label: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "PiResearchPlanner/1.0 (reference verification)",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read(MAX_REMOTE_BYTES + 1)
    if len(raw) > MAX_REMOTE_BYTES:
        raise ContractError(f"{label} response exceeded 512 KiB")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ContractError(f"{label} returned a non-object response")
    return payload


def _terms(text: str) -> set[str]:
    lowered = text.casefold()
    latin = set(re.findall(r"[a-z0-9][a-z0-9_.:+-]{1,}", lowered))
    chinese_runs = re.findall(r"[\u3400-\u9fff]+", lowered)
    chinese: set[str] = set()
    for run in chinese_runs:
        if len(run) == 1:
            chinese.add(run)
        else:
            chinese.update(run[index : index + 2] for index in range(len(run) - 1))
            chinese.update(run[index : index + 3] for index in range(len(run) - 2))
    return latin | chinese


def _markdown_sections(path: Path) -> list[tuple[str, str]]:
    text = path.read_text(encoding="utf-8")
    heading = path.stem
    body: list[str] = []
    sections: list[tuple[str, str]] = []
    for line in text.splitlines():
        if line.startswith("#"):
            if body and "\n".join(body).strip():
                sections.append((heading, "\n".join(body).strip()))
            heading = line.lstrip("#").strip() or path.stem
            body = []
        else:
            body.append(line)
    if body and "\n".join(body).strip():
        sections.append((heading, "\n".join(body).strip()))
    return sections


def search_local_knowledge(query: object, limit: object = 5) -> dict[str, Any]:
    """Search curated, bundled Markdown without reading outside the Planner."""

    normalized_query = _bounded_text(query, "query", 500)
    normalized_limit = _bounded_int(limit, "limit", 1, 10)
    if not KNOWLEDGE_ROOT.exists():
        return {
            "schema_version": "research-planner-local-search-v1",
            "status": "unavailable",
            "query": normalized_query,
            "results": [],
            "diagnostic": "bundled knowledge directory is absent",
        }
    query_terms = _terms(normalized_query)
    scored: list[tuple[int, str, str, str]] = []
    for path in sorted(KNOWLEDGE_ROOT.glob("*.md")):
        resolved = path.resolve()
        if resolved.parent != KNOWLEDGE_ROOT.resolve():
            continue
        for heading, body in _markdown_sections(resolved):
            heading_terms = _terms(heading)
            body_terms = _terms(body)
            overlap = len(query_terms & body_terms)
            heading_overlap = len(query_terms & heading_terms)
            score = overlap + 3 * heading_overlap
            if score:
                snippet = re.sub(r"\s+", " ", body).strip()[:1_200]
                scored.append((score, path.name, heading, snippet))
    scored.sort(key=lambda row: (-row[0], row[1], row[2]))
    results = [
        {
            "source_id": f"local:{filename}#{index + 1}",
            "file": f"knowledge/{filename}",
            "heading": heading,
            "score": score,
            "snippet": snippet,
        }
        for index, (score, filename, heading, snippet) in enumerate(
            scored[:normalized_limit]
        )
    ]
    return {
        "schema_version": "research-planner-local-search-v1",
        "status": "ok",
        "query": normalized_query,
        "result_count": len(results),
        "results": results,
        "notice": "Curated summaries are orientation evidence; verify decisive claims against their listed primary sources.",
    }


def search_scholarly_literature(
    query: object,
    limit: object = 5,
    from_year: object | None = None,
    to_year: object | None = None,
) -> dict[str, Any]:
    """Search OpenAlex metadata; fail closed instead of inventing references."""

    normalized_query = _bounded_text(query, "query", 500)
    normalized_limit = _bounded_int(limit, "limit", 1, 10)
    start_year = (
        _bounded_int(from_year, "from_year", 1600, 2200)
        if from_year is not None
        else None
    )
    end_year = (
        _bounded_int(to_year, "to_year", 1600, 2200)
        if to_year is not None
        else None
    )
    if start_year is not None and end_year is not None and start_year > end_year:
        raise ContractError("from_year cannot be later than to_year")
    params: dict[str, str | int] = {
        "search": normalized_query,
        "per-page": normalized_limit,
        "select": (
            "id,doi,title,publication_year,type,authorships,primary_location,"
            "cited_by_count,is_retracted"
        ),
    }
    filters: list[str] = []
    if start_year is not None:
        filters.append(f"from_publication_date:{start_year}-01-01")
    if end_year is not None:
        filters.append(f"to_publication_date:{end_year}-12-31")
    if filters:
        params["filter"] = ",".join(filters)
    url = OPENALEX_WORKS_URL + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "PiResearchPlanner/1.0 (metadata search)",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(MAX_REMOTE_BYTES + 1)
        if len(raw) > MAX_REMOTE_BYTES:
            raise ContractError("OpenAlex response exceeded 512 KiB")
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, urllib.error.URLError) as exc:
        return {
            "schema_version": "research-planner-literature-search-v1",
            "status": "unavailable",
            "query": normalized_query,
            "results": [],
            "diagnostic": str(exc)[:500],
            "safe_next_action": "Record the evidence gap or retry with network access; do not fabricate citations.",
        }
    rows = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ContractError("OpenAlex returned an invalid result collection")
    results: list[dict[str, Any]] = []
    for row in rows[:normalized_limit]:
        if not isinstance(row, dict):
            continue
        authorships = row.get("authorships")
        authors: list[str] = []
        if isinstance(authorships, list):
            for authorship in authorships[:8]:
                author = authorship.get("author") if isinstance(authorship, dict) else None
                name = author.get("display_name") if isinstance(author, dict) else None
                if isinstance(name, str) and name.strip():
                    authors.append(name.strip())
        primary_location = row.get("primary_location")
        source = (
            primary_location.get("source")
            if isinstance(primary_location, dict)
            else None
        )
        venue = source.get("display_name") if isinstance(source, dict) else None
        results.append(
            {
                "source_id": row.get("id"),
                "title": row.get("title"),
                "authors": authors,
                "publication_year": row.get("publication_year"),
                "work_type": row.get("type"),
                "venue": venue,
                "doi": row.get("doi"),
                "cited_by_count": row.get("cited_by_count"),
                "is_retracted": bool(row.get("is_retracted")),
            }
        )
    return {
        "schema_version": "research-planner-literature-search-v1",
        "status": "ok",
        "provider": "OpenAlex",
        "query": normalized_query,
        "result_count": len(results),
        "results": results,
        "notice": "These are discovery metadata, not verified full-text support. Read primary sources before asserting a scientific claim.",
    }


def resolve_reference(reference: object) -> dict[str, Any]:
    """Canonicalize a DOI, URL, or project-local file and report verification limits."""

    normalized = _bounded_text(reference, "reference", 1_000)
    doi_match = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", normalized, re.IGNORECASE)
    if doi_match:
        doi = doi_match.group(0).rstrip(".,;)").lower()
        encoded_id = urllib.parse.quote(f"https://doi.org/{doi}", safe="")
        openalex_url = f"{OPENALEX_WORKS_URL}/{encoded_id}"
        crossref_url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
        try:
            openalex = _fetch_json(openalex_url, "OpenAlex")
            crossref = _fetch_json(crossref_url, "Crossref")
        except (OSError, UnicodeError, json.JSONDecodeError, urllib.error.URLError, ContractError) as exc:
            return {
                "schema_version": "research-planner-reference-resolution-v1",
                "status": "unavailable",
                "reference_kind": "doi",
                "canonical_locator": f"https://doi.org/{doi}",
                "duplicate_key": f"doi:{doi}",
                "retraction_status": "not_verified",
                "correction_status": "not_verified",
                "diagnostic": str(exc)[:500],
            }
        message = crossref.get("message") if isinstance(crossref.get("message"), dict) else {}
        relation = message.get("relation") if isinstance(message, dict) else {}
        relation_keys = set(relation) if isinstance(relation, dict) else set()
        correction_keys = sorted(
            key for key in relation_keys if "correct" in key or "update" in key
        )
        authors: list[str] = []
        for authorship in openalex.get("authorships", [])[:12]:
            author = authorship.get("author") if isinstance(authorship, dict) else None
            name = author.get("display_name") if isinstance(author, dict) else None
            if isinstance(name, str) and name.strip():
                authors.append(name.strip())
        return {
            "schema_version": "research-planner-reference-resolution-v1",
            "status": "verified",
            "reference_kind": "doi",
            "canonical_locator": f"https://doi.org/{doi}",
            "duplicate_key": f"doi:{doi}",
            "title": openalex.get("title"),
            "authors": authors,
            "publication_year": openalex.get("publication_year"),
            "work_type": openalex.get("type"),
            "retraction_status": "retracted" if bool(openalex.get("is_retracted")) else "not_flagged_by_openalex",
            "correction_status": "linked_update" if correction_keys else "not_flagged_by_crossref",
            "correction_relation_keys": correction_keys,
            "notice": "Registry metadata can miss late corrections; decisive claims still require primary-source review.",
        }
    parsed = urllib.parse.urlparse(normalized)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        canonical = urllib.parse.urlunparse(
            (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", "", parsed.query, "")
        )
        return {
            "schema_version": "research-planner-reference-resolution-v1",
            "status": "normalized_only",
            "reference_kind": "url",
            "canonical_locator": canonical,
            "duplicate_key": f"url:{canonical}",
            "retraction_status": "not_applicable_or_not_verified",
            "correction_status": "not_verified",
            "notice": "A generic URL was normalized but its scientific metadata was not inferred.",
        }
    path = _safe_local_path(normalized, "reference", MAX_LOCAL_EVIDENCE_BYTES)
    raw = path.read_bytes()
    return {
        "schema_version": "research-planner-reference-resolution-v1",
        "status": "verified_local",
        "reference_kind": "local_file",
        "canonical_locator": path.relative_to(PROJECT_ROOT).as_posix(),
        "duplicate_key": f"sha256:{hashlib.sha256(raw).hexdigest()}",
        "size_bytes": len(raw),
        "retraction_status": "not_applicable",
        "correction_status": "requires_document_metadata_review",
    }


def extract_source_evidence(
    source_id: object,
    claim: object,
    *,
    source_text: object | None = None,
    local_path: object | None = None,
    relationship: object = "context",
    limit: object = 5,
) -> dict[str, Any]:
    """Return bounded candidate passages; never assert that a passage proves a claim."""

    normalized_source_id = _bounded_text(source_id, "source_id", 1_000)
    normalized_claim = _bounded_text(claim, "claim", 2_000)
    normalized_limit = _bounded_int(limit, "limit", 1, 10)
    normalized_relationship = _bounded_text(relationship, "relationship", 20)
    if normalized_relationship not in {"supports", "opposes", "limits", "context"}:
        raise ContractError("relationship must be supports, opposes, limits, or context")
    if (source_text is None) == (local_path is None):
        raise ContractError("provide exactly one of source_text or local_path")
    locator_prefix = "provided_text"
    if local_path is not None:
        path = _safe_local_path(local_path, "local_path", MAX_LOCAL_EVIDENCE_BYTES)
        if path.suffix.lower() not in {".md", ".txt", ".json", ".csv"}:
            raise ContractError("local_path must be a UTF-8 text, Markdown, JSON, or CSV file")
        text = path.read_text(encoding="utf-8-sig")
        locator_prefix = path.relative_to(PROJECT_ROOT).as_posix()
    else:
        text = _bounded_text(source_text, "source_text", 100_000)
    claim_terms = _terms(normalized_claim)
    passages: list[tuple[int, int, int, str]] = []
    paragraph_lines: list[str] = []
    start_line = 1
    lines = text.splitlines()
    for index, line in enumerate(lines + [""], start=1):
        if line.strip():
            if not paragraph_lines:
                start_line = index
            paragraph_lines.append(line.strip())
            continue
        if not paragraph_lines:
            continue
        passage = re.sub(r"\s+", " ", " ".join(paragraph_lines)).strip()
        score = len(claim_terms & _terms(passage))
        if score:
            passages.append((score, start_line, index - 1, passage[:1_500]))
        paragraph_lines = []
    passages.sort(key=lambda row: (-row[0], row[1]))
    results = [
        {
            "candidate_id": f"{normalized_source_id}:evidence:{index + 1}",
            "locator": f"{locator_prefix}:lines-{start}-{end}",
            "term_overlap_score": score,
            "text": passage,
            "proposed_relationship": normalized_relationship,
            "verification_status": "candidate_only",
        }
        for index, (score, start, end, passage) in enumerate(passages[:normalized_limit])
    ]
    return {
        "schema_version": "research-planner-evidence-extraction-v1",
        "status": "ok" if results else "no_candidate_passage",
        "source_id": normalized_source_id,
        "claim": normalized_claim,
        "result_count": len(results),
        "results": results,
        "notice": "Lexical candidates must be read in context before being linked as support, opposition, or limitation.",
    }


def inspect_dataset(
    local_path: object,
    *,
    expected_variables: object | None = None,
    time_field: object | None = None,
    sample_limit: object = 5_000,
) -> dict[str, Any]:
    """Inspect bounded local CSV/JSON/JSONL metadata without executing analysis."""

    path = _safe_local_path(local_path, "local_path", MAX_DATASET_BYTES)
    normalized_limit = _bounded_int(sample_limit, "sample_limit", 1, 5_000)
    expected = [] if expected_variables is None else [
        _bounded_text(value, f"expected_variables[{index}]", 200)
        for index, value in enumerate(
            expected_variables if isinstance(expected_variables, list) else []
        )
    ]
    if expected_variables is not None and not isinstance(expected_variables, list):
        raise ContractError("expected_variables must be an array")
    if len(expected) > 30 or len(expected) != len(set(expected)):
        raise ContractError("expected_variables must contain at most 30 unique names")
    normalized_time_field = (
        _bounded_text(time_field, "time_field", 200) if time_field is not None else None
    )
    suffix = path.suffix.lower()
    records: list[dict[str, Any]] = []
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            variables = list(reader.fieldnames or [])
            for row in reader:
                records.append(dict(row))
                if len(records) >= normalized_limit:
                    break
    elif suffix in {".json", ".jsonl"}:
        if suffix == ".jsonl":
            with path.open("r", encoding="utf-8-sig") as handle:
                for line in handle:
                    if line.strip():
                        value = json.loads(line)
                        if isinstance(value, dict):
                            records.append(value)
                    if len(records) >= normalized_limit:
                        break
        else:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            rows = payload.get("records") if isinstance(payload, dict) else payload
            if not isinstance(rows, list):
                raise ContractError("JSON dataset must be an array or an object with a records array")
            records = [row for row in rows[:normalized_limit] if isinstance(row, dict)]
        variables = sorted({str(key) for row in records for key in row})
    else:
        raise ContractError("local_path must be CSV, JSON, or JSONL")
    missing_counts = {
        variable: sum(
            1
            for row in records
            if row.get(variable) is None or str(row.get(variable, "")).strip() == ""
        )
        for variable in variables
    }
    units: dict[str, str] = {}
    for variable in variables:
        unit_match = re.search(r"(?:\[([^\]]+)\]|\(([^)]+)\))$", variable)
        if unit_match:
            units[variable] = (unit_match.group(1) or unit_match.group(2)).strip()
    time_values = []
    if normalized_time_field is not None and normalized_time_field in variables:
        time_values = [
            str(row.get(normalized_time_field)).strip()
            for row in records
            if row.get(normalized_time_field) is not None
            and str(row.get(normalized_time_field)).strip()
        ]
    raw = path.read_bytes()
    return {
        "schema_version": "research-planner-dataset-inspection-v1",
        "status": "ok",
        "path": path.relative_to(PROJECT_ROOT).as_posix(),
        "format": suffix.lstrip("."),
        "size_bytes": len(raw),
        "version_fingerprint": hashlib.sha256(raw).hexdigest(),
        "variables": variables,
        "expected_variables_missing": sorted(set(expected) - set(variables)),
        "sampled_record_count": len(records),
        "sample_limit": normalized_limit,
        "missing_counts_in_sample": missing_counts,
        "unit_hints_from_headers": units,
        "time_field": normalized_time_field,
        "time_coverage_in_sample": (
            {"minimum": min(time_values), "maximum": max(time_values), "basis": "lexical_sample"}
            if time_values
            else None
        ),
        "revision_status": "needs_confirmation",
        "license_status": "needs_confirmation",
        "notice": "This is a bounded metadata inspection, not a scientific analysis or a full data-quality audit.",
    }


__all__ = [
    "extract_source_evidence",
    "inspect_dataset",
    "resolve_reference",
    "search_local_knowledge",
    "search_scholarly_literature",
]
