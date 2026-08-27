"""Literature pipeline for the knowledge base (plan §5.3).

``lit_search`` queries NASA ADS, OpenAlex (equivalent logic to
``src/research_planner/knowledge.py::search_scholarly_literature``,
re-implemented here so the planner package stays untouched), the public arXiv
API, Crossref, or all providers with partial-failure tolerance. It refreshes each
provider-version row and groups preprint, journal, and updated-review variants
into one literature family.

``lit_feed_sync`` runs one bounded, versioned solar-research subscription and
stores an auditable receipt. Feed hits remain raw sources until the existing
task-bound fetch/distill/review pipeline promotes their claims.

``lit_fetch`` writes the cached abstract (open full text is out of scope for
the pure-stdlib P2; see plan §10) to ``workspace/literature/`` — or to
``<DATA_DIR>/literature`` when the current working directory has no
``workspace/``.

``lit_distill`` validates an LLM-produced distill payload against the cached
source text (the anti-hallucination contract in ``contracts.py``: every
evidence-bearing field needs a <=40-word verbatim quote that hits the cached
text) and stores it as a candidate entry via ``service.propose`` (R2). A
task-bound question/focus and direct relevance are mandatory; idempotency is
per literature family and normalized focus.

Pure standard library; network access uses urllib only.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from . import service
from .contracts import (
    QUOTE_MAX_WORDS,
    ContractError,
    quote_is_grounded,
    validate_distill_content,
)
from .literature_identity import normalize_focus
from .store import KnowledgeStore, default_db_path, utc_now

ADS_SEARCH_URL = "https://api.adsabs.harvard.edu/v1/search/query"
OPENALEX_WORKS_URL = "https://api.openalex.org/works"
ARXIV_API_URL = "https://export.arxiv.org/api/query"
CROSSREF_WORKS_URL = "https://api.crossref.org/works"
MAX_REMOTE_BYTES = 2 * 1024 * 1024
USER_AGENT = "JW-KnowledgeBase/1.0 (literature pipeline)"

SOURCES = ("all", "ads", "openalex", "arxiv", "crossref")
SORTS = ("relevance", "recent")
_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV_NS = "{http://arxiv.org/schemas/atom}"

_GENERIC_ENGLISH_TERMS = {
    "activity",
    "analysis",
    "cycle",
    "literature",
    "method",
    "methods",
    "paper",
    "research",
    "solar",
    "study",
}
_GENERIC_CJK_TERMS = {
    "分析",
    "太阳",
    "太阳活动",
    "活动",
    "活动周",
    "影响",
    "文献",
    "方法",
    "比较",
    "研究",
    "关系",
    "周期",
}


def _fail(message: str, *, error_code: str, field_path: str, suggestion: str) -> None:
    raise ContractError(
        message,
        error_code=error_code,
        field_path=field_path,
        suggestion=suggestion,
    )


def _bounded_query(query: Any) -> str:
    if not isinstance(query, str) or not query.strip():
        _fail(
            "query must be a non-empty string",
            error_code="query_missing",
            field_path="query",
            suggestion="提供检索关键词（英文效果更稳）。",
        )
    text = query.strip()
    if len(text) > 500:
        _fail(
            "query exceeds 500 characters",
            error_code="query_too_long",
            field_path="query",
            suggestion="缩短检索词到 500 字符以内。",
        )
    return text


def _bounded_year(value: int, label: str) -> int | None:
    year = int(value or 0)
    if year == 0:
        return None
    if not 1600 <= year <= 2200:
        _fail(
            f"{label} must be 0 or a year in [1600, 2200]",
            error_code="year_invalid",
            field_path=label,
            suggestion="年份给 0（不过滤）或 1600-2200 之间的值。",
        )
    return year


def _bounded_text(value: Any, label: str, *, max_length: int = 1000) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(
            f"{label} must be a non-empty string",
            error_code=f"{label}_missing",
            field_path=label,
            suggestion=f"提供由当前研究任务明确给定的 {label}。",
        )
    text = " ".join(value.split())
    if len(text) > max_length:
        _fail(
            f"{label} exceeds {max_length} characters",
            error_code=f"{label}_too_long",
            field_path=label,
            suggestion=f"将 {label} 缩短到 {max_length} 字符以内。",
        )
    return text


def _significant_terms(value: str) -> set[str]:
    text = value.casefold()
    terms = {
        token
        for token in re.findall(r"[a-z][a-z0-9_-]{2,}", text)
        if token not in _GENERIC_ENGLISH_TERMS
    }
    for sequence in re.findall(r"[\u3400-\u9fff]{2,}", text):
        for width in (2, 3, 4):
            for index in range(max(0, len(sequence) - width + 1)):
                token = sequence[index : index + width]
                if token not in _GENERIC_CJK_TERMS:
                    terms.add(token)
    return terms


def compound_focus_phrases(value: str) -> set[str]:
    """Return concrete adjacent English concepts from a bilingual query."""

    normalized = value.casefold().replace("-", " ").replace("_", " ")
    tokens = re.findall(r"[a-z][a-z0-9]{2,}", normalized)
    phrases: set[str] = set()
    for width in (2, 3):
        for index in range(max(0, len(tokens) - width + 1)):
            window = tokens[index : index + width]
            if all(token in _GENERIC_ENGLISH_TERMS for token in window):
                continue
            phrases.add(" ".join(window))
    return phrases


def _shared_terms(left: str, right: str) -> list[str]:
    return sorted(_significant_terms(left) & _significant_terms(right))


def _query_term_matches(query: str, item: dict[str, Any]) -> list[str]:
    source_text = " ".join(
        (
            str(item.get("title") or ""),
            str(item.get("abstract") or ""),
        )
    )
    return sorted(_significant_terms(query) & _significant_terms(source_text))


def _source_contains_required_terms(
    item: dict[str, Any], required_terms: list[str]
) -> bool:
    source_text = " ".join(
        (
            str(item.get("title") or ""),
            str(item.get("abstract") or ""),
        )
    ).casefold()
    return all(term in source_text for term in required_terms)


def _source_contains_any_terms(
    item: dict[str, Any], required_any_terms: list[str]
) -> bool:
    if not required_any_terms:
        return True
    source_text = " ".join(
        (
            str(item.get("title") or ""),
            str(item.get("abstract") or ""),
        )
    ).casefold()
    return any(term in source_text for term in required_any_terms)


def _source_title_contains_any_terms(
    item: dict[str, Any], required_any_title_terms: list[str]
) -> bool:
    if not required_any_title_terms:
        return True
    title = str(item.get("title") or "").casefold()
    return any(term in title for term in required_any_title_terms)


def _source_passes_gate(
    item: dict[str, Any],
    *,
    query: str,
    minimum_query_term_matches: int,
    required_terms: list[str],
    required_any_terms: list[str],
    required_any_title_terms: list[str],
) -> bool:
    query_terms = _significant_terms(query)
    required_matches = min(minimum_query_term_matches, len(query_terms))
    return (
        len(_query_term_matches(query, item)) >= required_matches
        and _source_contains_required_terms(item, required_terms)
        and _source_contains_any_terms(item, required_any_terms)
        and _source_title_contains_any_terms(item, required_any_title_terms)
    )


def bind_distill_task(
    research_question: str,
    distill_focus: str,
    *,
    run_id: str = "",
) -> dict[str, Any]:
    """Freeze the task-owned question/focus pair before literature work."""

    question = _bounded_text(
        research_question, "research_question", max_length=8000
    )
    focus = _bounded_text(distill_focus, "distill_focus", max_length=500)
    question_focus_terms = _shared_terms(question, focus)
    if not question_focus_terms:
        _fail(
            "distill_focus is not traceably related to the research question",
            error_code="focus_not_related_to_question",
            field_path="distill_focus",
            suggestion=(
                "由任务重写 distill_focus，保留研究问题中的核心专名，并可附文献语言中的等价词。"
            ),
        )
    payload = {
        "research_question": question,
        "distill_focus": focus,
        "normalized_focus": normalize_focus(focus),
        "question_focus_terms": question_focus_terms,
        "run_id": str(run_id or "").strip(),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    payload["binding_id"] = hashlib.sha256(canonical).hexdigest()
    return payload


def validate_distill_relevance(
    *,
    research_question: str,
    focus: str,
    source_text: str,
    content: dict[str, Any],
) -> dict[str, Any]:
    """Fail closed unless question, focus, source, and output stay connected."""

    question_focus = _shared_terms(research_question, focus)
    if not question_focus:
        _fail(
            "distill focus drifted away from the bound research question",
            error_code="focus_not_related_to_question",
            field_path="focus",
            suggestion="使用任务绑定工具返回的原始 distill_focus，不要自行改题。",
        )
    source_focus = _shared_terms(source_text, focus)
    if len(source_focus) < 2:
        _fail(
            "cached source is not directly related to the bound distill focus",
            error_code="source_not_related_to_focus",
            field_path="source_id",
            suggestion="换用直接讨论该 focus 的文献；背景性综述不要蒸馏成机制主张。",
        )
    content_text = " ".join(
        " ".join(value) if isinstance(value, list) else str(value)
        for value in content.values()
    )
    output_focus = _shared_terms(content_text, focus)
    if not output_focus:
        _fail(
            "distilled entry does not preserve the bound focus",
            error_code="distill_output_not_related_to_focus",
            field_path="content",
            suggestion="只保留能直接回答 distill_focus 的主张；其余记为 evidence_gap。",
        )
    return {
        "classification": "direct_support",
        "question_focus_terms": question_focus,
        "source_focus_terms": source_focus,
        "output_focus_terms": output_focus,
    }


def _http_get(url: str, *, accept: str, headers: dict[str, str] | None = None) -> bytes:
    request_headers = {"Accept": accept, "User-Agent": USER_AGENT}
    request_headers.update(headers or {})
    request = urllib.request.Request(
        url,
        headers=request_headers,
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read(MAX_REMOTE_BYTES + 1)
    if len(raw) > MAX_REMOTE_BYTES:
        raise ContractError(f"remote response exceeded {MAX_REMOTE_BYTES} bytes")
    return raw


def _safe_diagnostic(exc: BaseException) -> str:
    """Return a bounded diagnostic without ever exposing provider credentials."""

    message = str(exc)
    if isinstance(exc, ContractError):
        details = [exc.error_code]
        if exc.field_path:
            details.append(f"field={exc.field_path}")
        message = f"{message} ({'; '.join(details)})"
    for name in ("ADS_API_TOKEN", "OPENALEX_API_KEY", "CROSSREF_MAILTO"):
        secret = os.getenv(name, "")
        if secret and len(secret) >= 4:
            message = message.replace(secret, "[redacted]")
    return message[:500]


# ----------------------------------------------------------------------
# NASA ADS
# ----------------------------------------------------------------------
def _first_text(value: Any) -> str:
    if isinstance(value, list):
        return str(value[0]).strip() if value else ""
    return str(value or "").strip()


def _search_ads(
    query: str,
    limit: int,
    from_year: int | None,
    to_year: int | None,
    sort: str,
) -> list[dict[str, Any]]:
    token = os.getenv("ADS_API_TOKEN", "").strip()
    if not token:
        _fail(
            "NASA ADS token is not configured",
            error_code="ads_token_missing",
            field_path="ADS_API_TOKEN",
            suggestion="在运行环境设置 ADS_API_TOKEN，其余来源仍可继续检索。",
        )
    ads_query = f"abs:({query})"
    if from_year is not None or to_year is not None:
        ads_query += f" year:[{from_year or 1600} TO {to_year or 2200}]"
    params = {
        "q": ads_query,
        "fl": (
            "bibcode,title,author,year,doi,abstract,pubdate,property,"
            "doctype,indexstamp,identifier"
        ),
        "rows": limit,
        "sort": "date desc" if sort == "recent" else "score desc",
    }
    url = ADS_SEARCH_URL + "?" + urllib.parse.urlencode(params)
    payload = json.loads(
        _http_get(
            url,
            accept="application/json",
            headers={"Authorization": f"Bearer {token}"},
        ).decode("utf-8")
    )
    response = payload.get("response") if isinstance(payload, dict) else None
    rows = response.get("docs") if isinstance(response, dict) else None
    if not isinstance(rows, list):
        raise ContractError("NASA ADS returned an invalid result collection")
    items: list[dict[str, Any]] = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        bibcode = str(row.get("bibcode") or "").strip()
        if not bibcode:
            continue
        properties = {
            str(value).upper()
            for value in row.get("property", [])
            if isinstance(value, str)
        }
        doctype = str(row.get("doctype") or "").casefold()
        title = _first_text(row.get("title"))
        doi = _first_text(row.get("doi"))
        is_retracted = (
            "RETRACTED" in properties
            or "retract" in doctype
            or title.casefold().startswith(("retracted:", "retraction:"))
        )
        try:
            year = int(row["year"]) if row.get("year") is not None else None
        except (TypeError, ValueError):
            year = None
        items.append(
            {
                "source_id": f"ads:{bibcode}",
                "provider": "ads",
                "source_version": str(row.get("indexstamp") or ""),
                "title": title,
                "authors": [
                    str(author).strip()
                    for author in row.get("author", [])[:8]
                    if str(author).strip()
                ],
                "year": year,
                "publication_date": str(row.get("pubdate") or "").strip(),
                "doi": doi,
                "url": (
                    "https://ui.adsabs.harvard.edu/abs/"
                    f"{urllib.parse.quote(bibcode, safe='')}/abstract"
                ),
                "abstract": str(row.get("abstract") or "").strip(),
                "is_refereed": "REFEREED" in properties,
                "is_retracted": is_retracted,
            }
        )
    return items


# ----------------------------------------------------------------------
# OpenAlex
# ----------------------------------------------------------------------
def _abstract_from_inverted_index(index: Any) -> str:
    """Rebuild the abstract string from OpenAlex's inverted index."""

    if not isinstance(index, dict):
        return ""
    positions: dict[int, str] = {}
    for word, offsets in index.items():
        if not isinstance(offsets, list):
            continue
        for offset in offsets:
            if isinstance(offset, int):
                positions[offset] = str(word)
    return " ".join(positions[i] for i in sorted(positions))


def _search_openalex(
    query: str,
    limit: int,
    from_year: int | None,
    to_year: int | None,
    sort: str,
) -> list[dict[str, Any]]:
    params: dict[str, str | int] = {
        "search": query,
        "per-page": limit,
        "select": (
            "id,doi,title,publication_year,type,authorships,primary_location,"
            "abstract_inverted_index,is_retracted,publication_date"
        ),
    }
    api_key = os.getenv("OPENALEX_API_KEY", "").strip()
    if api_key:
        params["api_key"] = api_key
    if sort == "recent":
        params["sort"] = "publication_date:desc"
    filters: list[str] = []
    if from_year is not None:
        filters.append(f"from_publication_date:{from_year}-01-01")
    if to_year is not None:
        filters.append(f"to_publication_date:{to_year}-12-31")
    if filters:
        params["filter"] = ",".join(filters)
    url = OPENALEX_WORKS_URL + "?" + urllib.parse.urlencode(params)
    payload = json.loads(_http_get(url, accept="application/json").decode("utf-8"))
    rows = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ContractError("OpenAlex returned an invalid result collection")
    items: list[dict[str, Any]] = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        work_id = str(row.get("id") or "")
        short_id = work_id.rsplit("/", 1)[-1].strip()
        if not short_id:
            continue
        authors: list[str] = []
        authorships = row.get("authorships")
        if isinstance(authorships, list):
            for authorship in authorships[:8]:
                author = (
                    authorship.get("author") if isinstance(authorship, dict) else None
                )
                name = author.get("display_name") if isinstance(author, dict) else None
                if isinstance(name, str) and name.strip():
                    authors.append(name.strip())
        primary_location = row.get("primary_location")
        landing = (
            primary_location.get("landing_page_url")
            if isinstance(primary_location, dict)
            else None
        )
        doi = str(row.get("doi") or "")
        items.append(
            {
                "source_id": f"openalex:{short_id}",
                "provider": "openalex",
                "source_version": "",
                "title": str(row.get("title") or "").strip(),
                "authors": authors,
                "year": row.get("publication_year"),
                "publication_date": str(row.get("publication_date") or ""),
                "doi": doi,
                "url": str(landing or doi or work_id),
                "abstract": _abstract_from_inverted_index(
                    row.get("abstract_inverted_index")
                ),
                "is_refereed": False,
                "is_retracted": bool(row.get("is_retracted")),
            }
        )
    return items


# ----------------------------------------------------------------------
# arXiv
# ----------------------------------------------------------------------
def _search_arxiv(
    query: str,
    limit: int,
    from_year: int | None,
    to_year: int | None,
    sort: str,
) -> list[dict[str, Any]]:
    terms = [term for term in re.split(r"\s+", query) if term]
    if not terms:
        return []
    search_query = "+AND+".join(f"all:{term}" for term in terms)
    # Fetch extra rows when year filtering happens client-side.
    batch = min(50, limit * 3 if (from_year or to_year) else limit)
    params = {
        "search_query": search_query,
        "start": 0,
        "max_results": batch,
        "sortBy": "submittedDate" if sort == "recent" else "relevance",
        "sortOrder": "descending",
    }
    url = ARXIV_API_URL + "?" + urllib.parse.urlencode(params)
    root = ET.fromstring(_http_get(url, accept="application/atom+xml"))
    items: list[dict[str, Any]] = []
    for entry in root.findall(f"{_ATOM}entry"):
        raw_id = (entry.findtext(f"{_ATOM}id") or "").strip()
        versioned_id = raw_id.rsplit("/", 1)[-1]
        version_match = re.search(r"v(?P<version>\d+)$", versioned_id)
        source_version = version_match.group("version") if version_match else ""
        arxiv_id = re.sub(r"v\d+$", "", versioned_id)
        if not arxiv_id:
            continue
        published = (entry.findtext(f"{_ATOM}published") or "").strip()
        try:
            year = int(published[:4]) if published else None
        except ValueError:
            year = None
        if from_year is not None and (year is None or year < from_year):
            continue
        if to_year is not None and (year is None or year > to_year):
            continue
        authors = [
            name.text.strip()
            for name in entry.findall(f"{_ATOM}author/{_ATOM}name")
            if name.text and name.text.strip()
        ][:8]
        title = " ".join((entry.findtext(f"{_ATOM}title") or "").split())
        summary = " ".join((entry.findtext(f"{_ATOM}summary") or "").split())
        doi = (entry.findtext(f"{_ARXIV_NS}doi") or "").strip()
        items.append(
            {
                "source_id": f"arxiv:{arxiv_id}",
                "provider": "arxiv",
                "source_version": source_version,
                "title": title,
                "authors": authors,
                "year": year,
                "publication_date": published[:10],
                "doi": doi,
                "url": raw_id or f"https://arxiv.org/abs/{arxiv_id}",
                "abstract": summary,
                "is_refereed": False,
                "is_retracted": False,
            }
        )
        if len(items) >= limit:
            break
    return items


# ----------------------------------------------------------------------
# Crossref
# ----------------------------------------------------------------------
def _crossref_year(row: dict[str, Any]) -> int | None:
    for key in ("published", "published-print", "published-online", "issued"):
        value = row.get(key)
        date_parts = value.get("date-parts") if isinstance(value, dict) else None
        if (
            isinstance(date_parts, list)
            and date_parts
            and isinstance(date_parts[0], list)
            and date_parts[0]
        ):
            try:
                return int(date_parts[0][0])
            except (TypeError, ValueError):
                continue
    return None


def _crossref_publication_date(row: dict[str, Any]) -> str:
    for key in ("published", "published-print", "published-online", "issued"):
        value = row.get(key)
        date_parts = value.get("date-parts") if isinstance(value, dict) else None
        if (
            isinstance(date_parts, list)
            and date_parts
            and isinstance(date_parts[0], list)
            and date_parts[0]
        ):
            parts = [str(part) for part in date_parts[0][:3]]
            return "-".join(
                part.zfill(2) if index else part for index, part in enumerate(parts)
            )
    return ""


def _plain_crossref_abstract(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return " ".join(html.unescape(without_tags).split())


def _search_crossref(
    query: str,
    limit: int,
    from_year: int | None,
    to_year: int | None,
    sort: str,
) -> list[dict[str, Any]]:
    params: dict[str, str | int] = {
        "query.bibliographic": query,
        "rows": limit,
        "select": "DOI,title,author,published,URL,abstract,type,indexed",
    }
    mailto = os.getenv("CROSSREF_MAILTO", "").strip()
    if mailto:
        params["mailto"] = mailto
    if sort == "recent":
        params["sort"] = "published"
        params["order"] = "desc"
    filters: list[str] = []
    if from_year is not None:
        filters.append(f"from-pub-date:{from_year}-01-01")
    if to_year is not None:
        filters.append(f"until-pub-date:{to_year}-12-31")
    if filters:
        params["filter"] = ",".join(filters)
    url = CROSSREF_WORKS_URL + "?" + urllib.parse.urlencode(params)
    payload = json.loads(_http_get(url, accept="application/json").decode("utf-8"))
    message = payload.get("message") if isinstance(payload, dict) else None
    rows = message.get("items") if isinstance(message, dict) else None
    if not isinstance(rows, list):
        raise ContractError("Crossref returned an invalid result collection")
    items: list[dict[str, Any]] = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        doi = str(row.get("DOI") or "").strip()
        if not doi:
            continue
        raw_title = row.get("title")
        title = (
            str(raw_title[0]).strip()
            if isinstance(raw_title, list) and raw_title
            else str(raw_title or "").strip()
        )
        authors: list[str] = []
        raw_authors = row.get("author")
        if isinstance(raw_authors, list):
            for author in raw_authors[:8]:
                if not isinstance(author, dict):
                    continue
                name = " ".join(
                    part
                    for part in (
                        str(author.get("given") or "").strip(),
                        str(author.get("family") or "").strip(),
                    )
                    if part
                )
                if name:
                    authors.append(name)
        updated = row.get("indexed")
        source_version = (
            str(updated.get("timestamp") or "") if isinstance(updated, dict) else ""
        )
        items.append(
            {
                "source_id": f"crossref:{doi.casefold()}",
                "provider": "crossref",
                "source_version": source_version,
                "title": title,
                "authors": authors,
                "year": _crossref_year(row),
                "publication_date": _crossref_publication_date(row),
                "doi": doi,
                "url": str(row.get("URL") or f"https://doi.org/{doi}"),
                "abstract": _plain_crossref_abstract(row.get("abstract")),
                "is_refereed": False,
                "is_retracted": bool(
                    re.search(r"\bretract(?:ed|ion)\b", title, flags=re.IGNORECASE)
                ),
            }
        )
    return items


# ----------------------------------------------------------------------
# lit_search
# ----------------------------------------------------------------------
def search_literature(
    store: KnowledgeStore,
    query: str,
    *,
    source: str = "all",
    limit: int = 5,
    from_year: int = 0,
    to_year: int = 0,
    sort: str = "relevance",
    minimum_query_term_matches: int = 0,
    required_terms: list[str] | None = None,
    required_any_terms: list[str] | None = None,
    required_any_title_terms: list[str] | None = None,
) -> dict[str, Any]:
    """Search external literature and cache hits in ``lit_sources``.

    Network failures return ``status=unavailable`` (fail closed — never
    fabricate references) instead of raising.
    """

    text = _bounded_query(query)
    normalized_source = str(source or "").strip().lower()
    if normalized_source not in SOURCES:
        _fail(
            f"unknown literature source: {source!r}",
            error_code="unknown_lit_source",
            field_path="source",
            suggestion=f"source 必须是 {list(SOURCES)} 之一。",
        )
    normalized_limit = max(1, min(int(limit or 5), 50))
    normalized_minimum_matches = max(0, min(int(minimum_query_term_matches or 0), 5))
    normalized_required_terms = [
        " ".join(str(term).casefold().split())
        for term in (required_terms or [])
        if str(term).strip()
    ][:10]
    normalized_required_any_terms = [
        " ".join(str(term).casefold().split())
        for term in (required_any_terms or [])
        if str(term).strip()
    ][:20]
    normalized_required_any_title_terms = [
        " ".join(str(term).casefold().split())
        for term in (required_any_title_terms or [])
        if str(term).strip()
    ][:20]
    normalized_sort = str(sort or "").strip().lower()
    if normalized_sort not in SORTS:
        _fail(
            f"unknown literature sort: {sort!r}",
            error_code="unknown_lit_sort",
            field_path="sort",
            suggestion=f"sort 必须是 {list(SORTS)} 之一。",
        )
    start_year = _bounded_year(from_year, "from_year")
    end_year = _bounded_year(to_year, "to_year")
    if start_year is not None and end_year is not None and start_year > end_year:
        _fail(
            "from_year cannot be later than to_year",
            error_code="year_range_inverted",
            field_path="from_year",
            suggestion="调整年份过滤区间。",
        )
    searchers = {
        "ads": _search_ads,
        "openalex": _search_openalex,
        "arxiv": _search_arxiv,
        "crossref": _search_crossref,
    }
    selected_sources = (
        tuple(searchers) if normalized_source == "all" else (normalized_source,)
    )
    provider_items: dict[str, list[dict[str, Any]]] = {}
    diagnostics: dict[str, str] = {}
    filtered_out: dict[str, int] = {}
    remote_limit = (
        min(100, normalized_limit * 3)
        if (
            normalized_minimum_matches
            or normalized_required_terms
            or normalized_required_any_terms
            or normalized_required_any_title_terms
        )
        else normalized_limit
    )
    for selected_source in selected_sources:
        try:
            rows = searchers[selected_source](
                text, remote_limit, start_year, end_year, normalized_sort
            )
            if (
                normalized_minimum_matches
                or normalized_required_terms
                or normalized_required_any_terms
                or normalized_required_any_title_terms
            ):
                accepted = [
                    row
                    for row in rows
                    if _source_passes_gate(
                        row,
                        query=text,
                        minimum_query_term_matches=normalized_minimum_matches,
                        required_terms=normalized_required_terms,
                        required_any_terms=normalized_required_any_terms,
                        required_any_title_terms=normalized_required_any_title_terms,
                    )
                ]
                filtered_out[selected_source] = len(rows) - len(accepted)
                rows = accepted
            provider_items[selected_source] = rows
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            urllib.error.URLError,
            ET.ParseError,
            ContractError,
        ) as exc:
            diagnostics[selected_source] = _safe_diagnostic(exc)
    if normalized_source == "all":
        # Interleave providers so a broad search cannot be monopolized by the
        # first endpoint before family-level DOI/title deduplication runs.
        items = []
        for index in range(normalized_limit):
            for selected_source in selected_sources:
                rows = provider_items.get(selected_source, [])
                if index < len(rows):
                    items.append(rows[index])
    else:
        items = provider_items.get(normalized_source, [])
    if not items and diagnostics:
        return {
            "status": "unavailable",
            "source": normalized_source,
            "query": text,
            "results": [],
            "diagnostic": diagnostics,
            "safe_next_action": "记录证据缺口或稍后重试；不要编造文献引用。",
        }
    results: list[dict[str, Any]] = []
    seen_families: dict[str, dict[str, Any]] = {}
    for item in items[:normalized_limit]:
        cached = store.get_lit_source(item["source_id"]) is not None
        stored = store.upsert_lit_source(item)
        preferred = store.resolve_lit_source(item["source_id"]) or stored
        family_id = str(preferred.get("family_id") or item["source_id"])
        if family_id in seen_families:
            existing_result = seen_families[family_id]
            existing_result["family_members"].append(item["source_id"])
            existing_result.update(
                {
                    "source_id": preferred["source_id"],
                    "title": preferred.get("title") or existing_result["title"],
                    "authors": preferred.get("authors") or existing_result["authors"],
                    "year": preferred.get("year") or existing_result["year"],
                    "doi": preferred.get("doi") or existing_result["doi"],
                    "url": preferred.get("url") or existing_result["url"],
                    "source_version": preferred.get("source_version") or "",
                    "publication_date": preferred.get("publication_date") or "",
                    "is_refereed": bool(preferred.get("is_refereed")),
                    "is_retracted": bool(preferred.get("is_retracted")),
                    "abstract_chars": len(preferred.get("abstract") or ""),
                    "cached": existing_result["cached"] and cached,
                }
            )
            continue
        result = {
            "source_id": preferred["source_id"],
            "family_id": family_id,
            "title": preferred.get("title") or item["title"],
            "authors": preferred.get("authors") or item["authors"],
            "year": preferred.get("year") or item["year"],
            "doi": preferred.get("doi") or item["doi"],
            "url": preferred.get("url") or item["url"],
            "source_version": preferred.get("source_version") or "",
            "publication_date": preferred.get("publication_date") or "",
            "abstract_chars": len(preferred.get("abstract") or ""),
            "is_refereed": bool(preferred.get("is_refereed")),
            "is_retracted": bool(preferred.get("is_retracted")),
            "cached": cached,
            "family_members": [item["source_id"]],
        }
        seen_families[family_id] = result
        results.append(result)
    return {
        "status": "partial" if diagnostics else "ok",
        "source": normalized_source,
        "providers_queried": list(selected_sources),
        "provider_diagnostics": diagnostics,
        "relevance_gate": {
            "minimum_query_term_matches": normalized_minimum_matches,
            "required_terms": normalized_required_terms,
            "required_any_terms": normalized_required_any_terms,
            "required_any_title_terms": normalized_required_any_title_terms,
            "filtered_out_by_provider": filtered_out,
        },
        "query": text,
        "sort": normalized_sort,
        "count": len(results),
        "raw_count": len(items[:normalized_limit]),
        "results": results,
        "notice": (
            "检索命中已刷新缓存并按文献族去重；蒸馏前先绑定研究问题与 focus，"
            "再用 lit_fetch 落盘首选版本并走 lit_distill。"
        ),
    }


# ----------------------------------------------------------------------
# bounded topic subscriptions
# ----------------------------------------------------------------------
def default_literature_feeds_path() -> Path:
    override = os.getenv("JW_LITERATURE_FEEDS_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    return (
        Path(__file__).resolve().parents[2]
        / "jw"
        / "subagents"
        / "solar"
        / "skills"
        / "solar-cycle"
        / "references"
        / "llm_wiki"
        / "_meta"
        / "literature_feeds.json"
    )


def load_literature_feeds(path: str | Path | None = None) -> dict[str, Any]:
    """Load and validate the versioned solar-research feed catalog."""

    catalog_path = Path(path) if path else default_literature_feeds_path()
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(
            f"cannot load literature feed catalog: {_safe_diagnostic(exc)}",
            error_code="literature_feed_catalog_invalid",
            field_path=str(catalog_path),
            suggestion="检查 literature_feeds.json 是否存在且为有效 JSON。",
        )
    if not isinstance(payload, dict) or not isinstance(payload.get("feeds"), list):
        _fail(
            "literature feed catalog must contain a feeds list",
            error_code="literature_feed_catalog_invalid",
            field_path="feeds",
            suggestion="按 literature-feed-catalog-v1 结构提供 feeds 数组。",
        )
    normalized_feeds: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(payload["feeds"]):
        if not isinstance(raw, dict):
            _fail(
                "feed must be an object",
                error_code="literature_feed_invalid",
                field_path=f"feeds[{index}]",
                suggestion="为每个订阅提供 id、query、providers 等字段。",
            )
        feed_id = str(raw.get("id") or "").strip()
        query = _bounded_query(raw.get("query"))
        providers = raw.get("providers")
        required_terms = raw.get("required_terms", [])
        required_any_terms = raw.get("required_any_terms", [])
        required_any_title_terms = raw.get("required_any_title_terms", [])
        if (
            not re.fullmatch(r"[a-z0-9_]{3,80}", feed_id)
            or feed_id in seen_ids
            or not isinstance(providers, list)
            or not providers
            or any(provider not in SOURCES[1:] for provider in providers)
            or not isinstance(required_terms, list)
            or not isinstance(required_any_terms, list)
            or not isinstance(required_any_title_terms, list)
            or any(
                not isinstance(term, str) or not term.strip() for term in required_terms
            )
            or any(
                not isinstance(term, str) or not term.strip()
                for term in required_any_terms
            )
            or any(
                not isinstance(term, str) or not term.strip()
                for term in required_any_title_terms
            )
        ):
            _fail(
                f"invalid literature feed: {feed_id!r}",
                error_code="literature_feed_invalid",
                field_path=f"feeds[{index}]",
                suggestion="feed id 必须唯一，providers 只能使用具体受支持来源。",
            )
        seen_ids.add(feed_id)
        lookback_years = max(1, min(int(raw.get("lookback_years") or 2), 10))
        limit = max(1, min(int(raw.get("limit") or 5), 50))
        feed_sort = str(raw.get("sort") or "recent").strip().lower()
        if feed_sort not in SORTS:
            _fail(
                f"invalid sort for feed {feed_id}",
                error_code="literature_feed_invalid",
                field_path=f"feeds[{index}].sort",
                suggestion=f"sort 必须是 {list(SORTS)} 之一。",
            )
        normalized_feeds.append(
            {
                **raw,
                "id": feed_id,
                "query": query,
                "providers": list(dict.fromkeys(providers)),
                "required_terms": [
                    " ".join(term.casefold().split()) for term in required_terms
                ][:10],
                "required_any_terms": [
                    " ".join(term.casefold().split()) for term in required_any_terms
                ][:20],
                "required_any_title_terms": [
                    " ".join(term.casefold().split())
                    for term in required_any_title_terms
                ][:20],
                "lookback_years": lookback_years,
                "limit": limit,
                "sort": feed_sort,
                "enabled": bool(raw.get("enabled", True)),
            }
        )
    return {
        "schema_version": str(payload.get("schema_version") or ""),
        "path": str(catalog_path),
        "feeds": normalized_feeds,
    }


def sync_literature_feed(
    store: KnowledgeStore,
    feed_id: str,
    *,
    limit: int = 0,
    feeds_path: str | Path | None = None,
) -> dict[str, Any]:
    """Incrementally refresh one bounded topic feed and record its receipt."""

    catalog = load_literature_feeds(feeds_path)
    feed = next(
        (item for item in catalog["feeds"] if item["id"] == str(feed_id).strip()),
        None,
    )
    if feed is None:
        _fail(
            f"unknown literature feed: {feed_id!r}",
            error_code="literature_feed_not_found",
            field_path="feed_id",
            suggestion="先调用 lit_feed_catalog 获取有效 feed id。",
        )
    if not feed["enabled"]:
        _fail(
            f"literature feed is disabled: {feed_id!r}",
            error_code="literature_feed_disabled",
            field_path="feed_id",
            suggestion="在订阅目录中启用该 feed，或选择其他 feed。",
        )
    normalized_limit = max(1, min(int(limit or feed["limit"]), 50))
    current_year = int(utc_now()[:4])
    from_year = current_year - int(feed["lookback_years"]) + 1
    started_at = utc_now()
    diagnostics: dict[str, Any] = {}
    results_by_family: dict[str, dict[str, Any]] = {}
    new_source_ids: set[str] = set()
    new_family_ids: set[str] = set()
    for provider in feed["providers"]:
        response = search_literature(
            store,
            feed["query"],
            source=provider,
            limit=normalized_limit,
            from_year=from_year,
            to_year=current_year,
            sort=feed["sort"],
            minimum_query_term_matches=2,
            required_terms=feed["required_terms"],
            required_any_terms=feed["required_any_terms"],
            required_any_title_terms=feed["required_any_title_terms"],
        )
        if response["status"] != "ok":
            diagnostic = response.get(
                "provider_diagnostics", response.get("diagnostic", {})
            )
            if isinstance(diagnostic, dict) and set(diagnostic) == {provider}:
                diagnostic = diagnostic[provider]
            diagnostics[provider] = diagnostic
        for row in response.get("results", []):
            family_id = str(row["family_id"])
            if not row.get("cached"):
                new_source_ids.update(row.get("family_members") or [row["source_id"]])
            if store.touch_lit_feed_family(feed["id"], family_id):
                new_family_ids.add(family_id)
            existing = results_by_family.get(family_id)
            if existing is None:
                results_by_family[family_id] = row
            else:
                family_members = sorted(
                    set(existing.get("family_members", []))
                    | set(row.get("family_members", []))
                )
                existing.update(row)
                existing["family_members"] = family_members
    pruned_family_ids: list[str] = []
    for source in store.list_lit_feed_sources(feed["id"]):
        if _source_passes_gate(
            source,
            query=feed["query"],
            minimum_query_term_matches=2,
            required_terms=feed["required_terms"],
            required_any_terms=feed["required_any_terms"],
            required_any_title_terms=feed["required_any_title_terms"],
        ):
            continue
        family_id = str(source.get("family_id") or "")
        if family_id and store.remove_lit_feed_family(feed["id"], family_id):
            pruned_family_ids.append(family_id)
    results = sorted(
        results_by_family.values(),
        key=lambda row: (
            int(row.get("year") or 0),
            str(row.get("publication_date") or ""),
        ),
        reverse=True,
    )
    status = (
        "unavailable"
        if not results and diagnostics
        else ("partial" if diagnostics else "ok")
    )
    receipt = store.record_lit_feed_run(
        feed_id=feed["id"],
        query=feed["query"],
        providers=feed["providers"],
        status=status,
        result_count=len(results),
        new_source_count=len(new_source_ids),
        new_family_count=len(new_family_ids),
        diagnostics=diagnostics,
        started_at=started_at,
    )
    return {
        "status": status,
        "feed": feed,
        "window": {"from_year": from_year, "to_year": current_year},
        "count": len(results),
        "new_source_count": len(new_source_ids),
        "new_family_count": len(new_family_ids),
        "pruned_family_count": len(pruned_family_ids),
        "results": results,
        "provider_diagnostics": diagnostics,
        "receipt": receipt,
        "notice": (
            "同步结果仅进入原始来源/候选层，必须继续绑定任务、抓取、逐字引证蒸馏和审核，"
            "才可能成为内置 Wiki 知识。"
        ),
    }


# ----------------------------------------------------------------------
# lit_fetch
# ----------------------------------------------------------------------
def default_literature_dir() -> Path:
    """``workspace/literature`` under cwd, else ``<DATA_DIR>/literature``."""

    workspace = Path.cwd() / "workspace"
    if workspace.is_dir():
        return workspace / "literature"
    return default_db_path().parent / "literature"


def _source_filename(source_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", source_id) + ".md"


def _render_source_text(row: dict[str, Any]) -> str:
    authors = row.get("authors") or []
    if isinstance(authors, str):
        authors = [authors]
    lines = [
        f"# {row.get('title') or row['source_id']}",
        "",
        f"- source_id: {row['source_id']}",
        f"- authors: {', '.join(str(name) for name in authors)}",
        f"- year: {row.get('year') or ''}",
        f"- doi: {row.get('doi') or ''}",
        f"- url: {row.get('url') or ''}",
        "",
        "## abstract",
        "",
        str(row.get("abstract") or "").strip(),
        "",
    ]
    return "\n".join(lines)


def fetch_literature(
    store: KnowledgeStore,
    source_id: str,
    *,
    literature_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Write the cached source text to disk; idempotent per ``source_id``."""

    requested_source_id = source_id
    row = store.resolve_lit_source(source_id)
    if row is None:
        _fail(
            f"literature source not cached: {source_id!r}",
            error_code="lit_source_not_found",
            field_path="source_id",
            suggestion="先用 lit_search 检索并缓存该 source_id。",
        )
    directory = Path(literature_dir) if literature_dir else default_literature_dir()
    source_id = str(row["source_id"])
    filename = _source_filename(source_id)
    path = directory / filename
    archive_path = store.export_dir / "raw" / "sources" / filename
    if row.get("fetched_at") and path.is_file():
        text = path.read_text(encoding="utf-8")
        if not archive_path.is_file():
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            archive_path.write_text(text, encoding="utf-8")
        return {
            "status": "ok",
            "source_id": source_id,
            "requested_source_id": requested_source_id,
            "family_id": row.get("family_id"),
            "path": str(path),
            "archive_path": str(archive_path),
            "text_length": len(text),
            "cached": True,
        }
    if not str(row.get("abstract") or "").strip():
        _fail(
            f"no cached abstract text for {source_id!r}",
            error_code="no_abstract_available",
            field_path="source_id",
            suggestion="该文献缓存中没有摘要文本；重新 lit_search 或换一篇。",
        )
    text = _render_source_text(row)
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if archive_path.resolve() != path.resolve():
        archive_path.write_text(text, encoding="utf-8")
    store.mark_lit_fetched(source_id)
    return {
        "status": "ok",
        "source_id": source_id,
        "requested_source_id": requested_source_id,
        "family_id": row.get("family_id"),
        "path": str(path),
        "archive_path": str(archive_path),
        "text_length": len(text),
        "cached": False,
    }


# ----------------------------------------------------------------------
# task-bound cached literature and source-to-Wiki impacts
# ----------------------------------------------------------------------
def build_literature_task_bundle(
    store: KnowledgeStore,
    research_question: str,
    focus: str,
    *,
    feed_ids: list[str] | None = None,
    limit: int = 3,
    run_id: str = "",
    ranking_focus: str | None = None,
    required_anchor_phrases: list[str] | None = None,
) -> dict[str, Any]:
    """Freeze a bounded, directly relevant set of cached source snapshots."""

    bound = bind_distill_task(research_question, focus, run_id=run_id)
    selected_limit = max(1, min(int(limit or 3), 5))
    normalized_feed_ids = sorted(
        {str(feed_id).strip() for feed_id in (feed_ids or []) if str(feed_id).strip()}
    )
    question_terms = _significant_terms(bound["research_question"])
    ranking_focus_text = (
        _bounded_text(ranking_focus, "ranking_focus", max_length=500)
        if ranking_focus is not None
        else bound["distill_focus"]
    )
    focus_terms = _significant_terms(ranking_focus_text)
    anchors = sorted(
        {
            " ".join(str(phrase).casefold().replace("-", " ").split())
            for phrase in (required_anchor_phrases or [])
            if str(phrase).strip()
        }
    )
    ranked: list[tuple[tuple[int, int, int, int, str, str], dict[str, Any]]] = []
    for source in store.list_preferred_lit_sources(feed_ids=normalized_feed_ids):
        abstract = " ".join(str(source.get("abstract") or "").split())
        if not abstract or bool(source.get("is_retracted")):
            continue
        source_text = " ".join((str(source.get("title") or ""), abstract))
        source_compounds = compound_focus_phrases(source_text)
        matched_anchor_phrases = sorted(set(anchors) & source_compounds)
        if anchors and not matched_anchor_phrases:
            continue
        source_terms = _significant_terms(source_text)
        focus_overlap = sorted(focus_terms & source_terms)
        question_overlap = sorted(question_terms & source_terms)
        if not focus_overlap:
            continue
        title_terms = _significant_terms(str(source.get("title") or ""))
        title_overlap = len(focus_terms & title_terms)
        score = (
            len(focus_overlap) * 5 + len(question_overlap) * 2 + title_overlap * 3,
            int(bool(source.get("is_refereed"))),
            int(bool(abstract)),
            int(source.get("year") or 0),
            str(source.get("publication_date") or ""),
            str(source.get("source_id") or ""),
        )
        snapshot = {
            "source_id": str(source.get("source_id") or ""),
            "family_id": str(source.get("family_id") or ""),
            "title": str(source.get("title") or ""),
            "authors": list(source.get("authors") or []),
            "year": source.get("year"),
            "publication_date": str(source.get("publication_date") or ""),
            "doi": str(source.get("doi") or ""),
            "url": str(source.get("url") or ""),
            "provider": str(source.get("provider") or ""),
            "source_version": str(source.get("source_version") or ""),
            "content_fingerprint": str(source.get("content_fingerprint") or ""),
            "is_refereed": bool(source.get("is_refereed")),
            "is_retracted": False,
            "abstract": abstract[:6000],
            "matched_focus_terms": focus_overlap,
            "matched_focus_phrases": matched_anchor_phrases,
            "matched_question_terms": question_overlap,
            "relevance_score": score[0],
        }
        ranked.append((score, snapshot))
    ranked.sort(key=lambda item: item[0], reverse=True)
    snapshots = [item[1] for item in ranked[:selected_limit]]
    identity = {
        "binding_id": bound["binding_id"],
        "sources": [
            {
                "source_id": item["source_id"],
                "content_fingerprint": item["content_fingerprint"],
            }
            for item in snapshots
        ],
    }
    digest = hashlib.sha256(
        json.dumps(
            identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    bundle_id = f"litbundle_{digest[:32]}"
    bundle = store.create_lit_task_bundle(
        bundle_id=bundle_id,
        binding_id=bound["binding_id"],
        run_id=str(run_id or ""),
        research_question=bound["research_question"],
        focus=bound["distill_focus"],
        source_snapshots=snapshots,
    )
    return {
        "status": "ok" if snapshots else "evidence_gap",
        "bundle_id": bundle_id,
        "binding_id": bound["binding_id"],
        "research_question": bound["research_question"],
        "focus": bound["distill_focus"],
        "ranking_focus": ranking_focus_text,
        "required_anchor_phrases": anchors,
        "feed_ids": normalized_feed_ids,
        "source_count": len(snapshots),
        "sources": snapshots,
        "created_at": bundle.get("created_at", ""),
        "notice": (
            "This frozen bundle is task evidence, not reusable Wiki grounding. "
            "Bind only verbatim abstract quotes that bear on a candidate."
            if snapshots
            else "No cached source directly matched the bound focus; record an evidence gap."
        ),
    }


def read_literature_task_bundle(
    store: KnowledgeStore, bundle_id: str
) -> dict[str, Any]:
    """Read an immutable task bundle by id."""

    bundle = store.get_lit_task_bundle(str(bundle_id or "").strip())
    if bundle is None:
        _fail(
            f"literature task bundle not found: {bundle_id}",
            error_code="lit_bundle_not_found",
            field_path="bundle_id",
            suggestion="使用 lit_bundle_build 返回的 bundle_id。",
        )
    return {
        "status": "ok",
        "bundle_id": bundle["bundle_id"],
        "binding_id": bundle["binding_id"],
        "research_question": bundle["research_question"],
        "focus": bundle["focus"],
        "source_count": len(bundle["source_snapshots"]),
        "sources": bundle["source_snapshots"],
        "created_at": bundle["created_at"],
    }


def record_literature_entry_impact(
    store: KnowledgeStore,
    *,
    source_id: str,
    entry_id: str,
    relation: str,
    affected_fields: list[str],
    scope: dict[str, Any] | None,
    quote: str,
    location: str,
    rationale: str,
    confidence: str = "low",
) -> dict[str, Any]:
    """Record a quote-grounded source-to-Wiki impact without editing the Wiki."""

    normalized_relation = str(relation or "").strip().lower()
    if normalized_relation not in {"supports", "contradicts", "qualifies", "extends"}:
        _fail(
            f"unknown literature impact relation: {relation!r}",
            error_code="lit_impact_relation_invalid",
            field_path="relation",
            suggestion="relation 使用 supports/contradicts/qualifies/extends。",
        )
    normalized_confidence = str(confidence or "").strip().lower()
    if normalized_confidence not in {"low", "medium"}:
        _fail(
            "single-source literature impact confidence must be low or medium",
            error_code="lit_impact_confidence_invalid",
            field_path="confidence",
            suggestion="单篇摘要影响默认为 low，最高 medium。",
        )
    source = store.resolve_lit_source(str(source_id or "").strip())
    if source is None:
        _fail(
            f"literature source not found: {source_id}",
            error_code="lit_source_not_found",
            field_path="source_id",
            suggestion="source_id 使用已缓存且可读取的文献来源。",
        )
    if bool(source.get("is_retracted")):
        _fail(
            "retracted literature cannot create a new Wiki impact",
            error_code="lit_source_retracted",
            field_path="source_id",
            suggestion="记录撤稿复核，不要用该来源提出新补丁。",
        )
    entry = store.get_entry(str(entry_id or "").strip())
    if entry is None:
        _fail(
            f"Wiki entry not found: {entry_id}",
            error_code="entry_not_found",
            field_path="entry_id",
            suggestion="entry_id 使用已读取的 Wiki 条目。",
        )
    normalized_quote = " ".join(str(quote or "").split())
    if not normalized_quote or len(normalized_quote.split()) > QUOTE_MAX_WORDS:
        _fail(
            f"impact quote must contain 1-{QUOTE_MAX_WORDS} words",
            error_code="lit_impact_quote_invalid",
            field_path="quote",
            suggestion="从缓存摘要中复制不超过 40 词的逐字引文。",
        )
    if not quote_is_grounded(normalized_quote, str(source.get("abstract") or "")):
        _fail(
            "impact quote is not grounded in the cached abstract",
            error_code="quote_not_grounded",
            field_path="quote",
            suggestion="逐字复制缓存摘要中的原文，不要改写。",
        )
    normalized_fields = sorted(
        {str(field).strip() for field in affected_fields if str(field).strip()}
    )
    allowed_fields = set(entry.get("content", {})) | {"valid_range"}
    invalid_fields = [
        field for field in normalized_fields if field not in allowed_fields
    ]
    if not normalized_fields or invalid_fields:
        _fail(
            f"affected_fields must name existing Wiki fields; invalid={invalid_fields}",
            error_code="lit_impact_fields_invalid",
            field_path="affected_fields",
            suggestion=f"可选字段：{sorted(allowed_fields)}。",
        )
    normalized_rationale = " ".join(str(rationale or "").split())
    if not normalized_rationale:
        _fail(
            "impact rationale is required",
            error_code="lit_impact_rationale_missing",
            field_path="rationale",
            suggestion="说明该引文为何支持、反对、限定或扩展目标字段。",
        )
    impact = store.record_lit_entry_impact(
        source_id=str(source["source_id"]),
        family_id=str(source.get("family_id") or source["source_id"]),
        entry_id=entry["id"],
        relation=normalized_relation,
        affected_fields=normalized_fields,
        scope=dict(scope or {}),
        quote=normalized_quote,
        location=" ".join(str(location or "abstract").split()),
        rationale=normalized_rationale,
        confidence=normalized_confidence,
    )
    return {"status": "ok", "impact": impact, "wiki_changed": False}


# ----------------------------------------------------------------------
# lit_distill
# ----------------------------------------------------------------------
def distill_literature(
    store: KnowledgeStore,
    source_id: str,
    entry_type: str,
    title: str,
    content: dict[str, Any],
    *,
    focus: str,
    research_question: str,
    research_request_sha256: str = "",
    run_id: str = "",
    agent: str = "",
    confidence: str = "low",
) -> dict[str, Any]:
    """Validate an LLM distill payload and store it as a candidate entry.

    The task must bind a non-empty research question and focus first. This
    function verifies question/focus/source/output relevance, quote grounding,
    and the abstract-only confidence cap before proposing a candidate. The
    idempotency key is the resolved literature family plus normalized focus.
    """

    bound = bind_distill_task(research_question, focus, run_id=run_id)
    if research_request_sha256 and research_request_sha256 != bound["binding_id"]:
        _fail(
            "distillation binding does not match the supplied question/focus",
            error_code="distill_binding_mismatch",
            field_path="research_request_sha256",
            suggestion="原样使用 lit_bind_task 返回的 binding_id、研究问题和 focus。",
        )
    requested_source_id = source_id
    row = store.resolve_lit_source(source_id)
    if row is None:
        _fail(
            f"literature source not cached: {source_id!r}",
            error_code="lit_source_not_found",
            field_path="source_id",
            suggestion="先用 lit_search 检索并缓存该 source_id。",
        )
    source_id = str(row["source_id"])
    existing = store.get_lit_distillation(source_id, bound["distill_focus"])
    if existing:
        existing_id = str(existing["entry_id"])
        return {
            "status": "ok",
            "source_id": source_id,
            "requested_source_id": requested_source_id,
            "family_id": row.get("family_id"),
            "focus": bound["distill_focus"],
            "entry_id": existing_id,
            "idempotent": True,
            "entry": store.get_entry(existing_id),
            "notice": "该文献族已针对同一 focus 蒸馏过；返回已有条目。",
        }
    if confidence == "high":
        _fail(
            "single-source abstract distillation cannot use high confidence",
            error_code="confidence_cap_exceeded",
            field_path="confidence",
            suggestion="单源摘要默认 low、上限 medium；high 需要独立多源或复现证据。",
        )
    source_text = str(row.get("abstract") or "").strip()
    normalized, evidence_map, gaps = validate_distill_content(
        entry_type, content, source_text
    )
    if not isinstance(title, str) or not title.strip():
        _fail(
            "title must be a non-empty string",
            error_code="title_missing",
            field_path="title",
            suggestion="提供一句话标题。",
        )
    relevance = validate_distill_relevance(
        research_question=bound["research_question"],
        focus=bound["distill_focus"],
        source_text=source_text,
        content=normalized,
    )
    source_ref = str(row.get("doi") or row.get("url") or source_id)
    with store.locked():
        existing = store.get_lit_distillation(source_id, bound["distill_focus"])
        if existing:
            existing_id = str(existing["entry_id"])
            return {
                "status": "ok",
                "source_id": source_id,
                "requested_source_id": requested_source_id,
                "family_id": row.get("family_id"),
                "focus": bound["distill_focus"],
                "entry_id": existing_id,
                "idempotent": True,
                "entry": store.get_entry(existing_id),
            }
        result = service.propose(
            store,
            entry_type=entry_type,
            title=title.strip(),
            content=normalized,
            source_type="literature",
            source_ref=source_ref,
            confidence=confidence,
            valid_range="",
            related_ids=[],
            agent=agent or "solar-knowledge",
            run_id=run_id,
            provenance_extra={
                "lit_source_id": source_id,
                "lit_family_id": row.get("family_id"),
                "distill_focus": bound["distill_focus"],
                "research_question": bound["research_question"],
                "research_request_sha256": bound["binding_id"],
                "distilled_at": utc_now(),
                "evidence_scope": "abstract_only",
                "independent_source_count": 1,
                "relevance": relevance,
                "evidence_map": evidence_map,
                "evidence_gaps": gaps,
            },
        )
        entry = result["entry"]
        store.record_lit_distillation(
            source_id=source_id,
            focus=bound["distill_focus"],
            research_question=bound["research_question"],
            research_request_sha256=bound["binding_id"],
            entry_id=entry["id"],
            relevance=relevance["classification"],
        )
    response: dict[str, Any] = {
        "status": "ok",
        "source_id": source_id,
        "requested_source_id": requested_source_id,
        "family_id": row.get("family_id"),
        "focus": bound["distill_focus"],
        "research_request_sha256": bound["binding_id"],
        "entry_id": entry["id"],
        "idempotent": False,
        "quotes_verified": len(evidence_map),
        "evidence_gaps": gaps,
        "entry": entry,
    }
    if result.get("conflicts"):
        response["conflicts"] = result["conflicts"]
        response["warning"] = result["warning"]
    return response
