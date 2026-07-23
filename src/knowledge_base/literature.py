"""Literature pipeline for the knowledge base (plan §5.3).

``lit_search`` queries OpenAlex (equivalent logic to
``src/research_planner/knowledge.py::search_scholarly_literature``,
re-implemented here so the planner package stays untouched), the public arXiv
API, Crossref, or all three with partial-failure tolerance. It refreshes each
provider-version row and groups preprint, journal, and updated-review variants
into one literature family.

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
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from . import service
from .contracts import ContractError, validate_distill_content
from .literature_identity import normalize_focus
from .store import KnowledgeStore, default_db_path, utc_now

OPENALEX_WORKS_URL = "https://api.openalex.org/works"
ARXIV_API_URL = "https://export.arxiv.org/api/query"
CROSSREF_WORKS_URL = "https://api.crossref.org/works"
MAX_REMOTE_BYTES = 512 * 1024
USER_AGENT = "EvoScientist-KnowledgeBase/1.0 (literature pipeline)"

SOURCES = ("all", "openalex", "arxiv", "crossref")
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


def _shared_terms(left: str, right: str) -> list[str]:
    return sorted(_significant_terms(left) & _significant_terms(right))


def bind_distill_task(
    research_question: str,
    distill_focus: str,
    *,
    run_id: str = "",
) -> dict[str, Any]:
    """Freeze the task-owned question/focus pair before literature work."""

    question = _bounded_text(research_question, "research_question")
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


def _http_get(url: str, *, accept: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"Accept": accept, "User-Agent": USER_AGENT},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read(MAX_REMOTE_BYTES + 1)
    if len(raw) > MAX_REMOTE_BYTES:
        raise ContractError(f"remote response exceeded {MAX_REMOTE_BYTES} bytes")
    return raw


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
    query: str, limit: int, from_year: int | None, to_year: int | None
) -> list[dict[str, Any]]:
    params: dict[str, str | int] = {
        "search": query,
        "per-page": limit,
        "select": (
            "id,doi,title,publication_year,type,authorships,primary_location,"
            "abstract_inverted_index,is_retracted"
        ),
    }
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
                "doi": doi,
                "url": str(landing or doi or work_id),
                "abstract": _abstract_from_inverted_index(
                    row.get("abstract_inverted_index")
                ),
                "is_retracted": bool(row.get("is_retracted")),
            }
        )
    return items


# ----------------------------------------------------------------------
# arXiv
# ----------------------------------------------------------------------
def _search_arxiv(
    query: str, limit: int, from_year: int | None, to_year: int | None
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
        "sortBy": "relevance",
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
                "doi": doi,
                "url": raw_id or f"https://arxiv.org/abs/{arxiv_id}",
                "abstract": summary,
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


def _plain_crossref_abstract(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return " ".join(html.unescape(without_tags).split())


def _search_crossref(
    query: str, limit: int, from_year: int | None, to_year: int | None
) -> list[dict[str, Any]]:
    params: dict[str, str | int] = {
        "query.bibliographic": query,
        "rows": limit,
        "select": "DOI,title,author,published,URL,abstract,type,indexed",
    }
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
                "doi": doi,
                "url": str(row.get("URL") or f"https://doi.org/{doi}"),
                "abstract": _plain_crossref_abstract(row.get("abstract")),
                "is_retracted": False,
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
    normalized_limit = max(1, min(int(limit or 5), 10))
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
        "openalex": _search_openalex,
        "arxiv": _search_arxiv,
        "crossref": _search_crossref,
    }
    selected_sources = (
        tuple(searchers) if normalized_source == "all" else (normalized_source,)
    )
    provider_items: dict[str, list[dict[str, Any]]] = {}
    diagnostics: dict[str, str] = {}
    for selected_source in selected_sources:
        try:
            provider_items[selected_source] = searchers[selected_source](
                text, normalized_limit, start_year, end_year
            )
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            urllib.error.URLError,
            ET.ParseError,
            ContractError,
        ) as exc:
            diagnostics[selected_source] = str(exc)[:500]
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
            "abstract_chars": len(preferred.get("abstract") or ""),
            "is_retracted": item["is_retracted"],
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
        "query": text,
        "count": len(results),
        "raw_count": len(items[:normalized_limit]),
        "results": results,
        "notice": (
            "检索命中已刷新缓存并按文献族去重；蒸馏前先绑定研究问题与 focus，"
            "再用 lit_fetch 落盘首选版本并走 lit_distill。"
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
