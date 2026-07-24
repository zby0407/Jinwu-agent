"""Read-only REST surface for the research Wiki (plan §5.7).

The WebUI reads ``~/.jw/knowledge.db`` through these routes.
Besides entries/reviews/usage, the surface exposes the source layer, the
compiled source→claim graph, and coverage diagnostics.  This follows the
LLM-Wiki split between immutable sources and incrementally maintained pages
without changing the agent-side write and HITL approval path.

Connections use SQLite's URI read-only mode (``file:...?mode=ro``) so a
missing database, a missing table, or a concurrent writer can never turn into
a 500 — list routes answer ``[]`` and the detail route answers a JSON 404.

Keyword filtering (``q=``) is a plain ``LIKE`` rather than the FTS5 table:
the FTS index is maintained by the writing process, and keeping this reader
on the base tables avoids any chance of lock coupling between the two.

Mounted by ``http.py``; all blocking sqlite3 work is offloaded to a thread
(``asyncio.to_thread``) because langgraph-dev's ``blockbuster`` middleware
refuses blocking syscalls on the event loop.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import urllib.parse
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

try:
    from knowledge_base.store import default_db_path
except ImportError:  # packaged installs without the repo's src/ tree

    def default_db_path() -> Path:  # type: ignore[no-redef]
        """Same rule as ``jw/paths.py`` (and store.py)."""

        override = os.getenv("JW_DATA_DIR")
        base = (
            Path(override).expanduser() if override else Path.home() / ".jw"
        )
        return base / "knowledge.db"


_NO_STORE = {"Cache-Control": "no-store"}
_ENTRY_LIST_COLUMNS = (
    "id, type, title, status, confidence, valid_range, updated_at, source_ref"
)
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200
_GRAPH_MAX_NODES = 300


def _empty_overview() -> dict[str, Any]:
    return {
        "entries": 0,
        "sources": 0,
        "source_families": 0,
        "fetched_sources": 0,
        "distilled_sources": 0,
        "distillations": 0,
        "pending_reviews": 0,
        "usage_reads": 0,
        "by_type": {},
        "by_status": {},
        "by_provider": {},
        "coverage": {
            "fetch_rate": 0.0,
            "distillation_rate": 0.0,
            "canonical_rate": 0.0,
        },
        "gaps": [],
    }


def _json(data: Any, *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status_code, headers=_NO_STORE)


def _connect_ro() -> sqlite3.Connection | None:
    """Open the knowledge db read-only; ``None`` when it doesn't exist yet."""

    path = default_db_path()
    if not path.exists():
        return None
    uri = "file:" + urllib.parse.quote(str(path)) + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _like_escape(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _clamp_limit(raw: str | None) -> int:
    try:
        value = int(raw) if raw else _DEFAULT_LIMIT
    except ValueError:
        return _DEFAULT_LIMIT
    return max(1, min(value, _MAX_LIMIT))


def _parse_json(raw: Any, fallback: Any) -> Any:
    if isinstance(raw, str) and raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return fallback
    return fallback if raw is None else raw


def _fetch_entries(
    entry_type: str, status: str, query: str, limit: int
) -> list[dict[str, Any]]:
    conn = _connect_ro()
    if conn is None:
        return []
    try:
        filters: list[str] = []
        params: list[Any] = []
        if entry_type:
            filters.append("type = ?")
            params.append(entry_type)
        if status:
            filters.append("status = ?")
            params.append(status)
        if query:
            like = f"%{_like_escape(query)}%"
            filters.append(
                "(id LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\' "
                "OR content LIKE ? ESCAPE '\\' OR source_ref LIKE ? ESCAPE '\\')"
            )
            params.extend((like, like, like, like))
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        rows = conn.execute(
            f"SELECT {_ENTRY_LIST_COLUMNS} FROM entries {where} "
            "ORDER BY updated_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _fetch_entry(entry_id: str) -> dict[str, Any] | None:
    conn = _connect_ro()
    if conn is None:
        return None
    try:
        row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if row is None:
            return None
        entry = dict(row)
        entry["content"] = _parse_json(entry.get("content"), {})
        entry["related_ids"] = _parse_json(entry.get("related_ids"), [])
        entry["provenance"] = _parse_json(entry.get("provenance"), {})
        evidence_map = entry["provenance"].get("evidence_map", {})
        entry["evidence"] = evidence_map if isinstance(evidence_map, dict) else {}
        evidence_gaps = entry["provenance"].get("evidence_gaps", [])
        entry["evidence_gaps"] = (
            evidence_gaps if isinstance(evidence_gaps, list) else []
        )
        related_entries: list[dict[str, Any]] = []
        related_ids = [
            str(value) for value in entry["related_ids"] if str(value).strip()
        ]
        if related_ids:
            placeholders = ", ".join("?" for _ in related_ids)
            related_rows = conn.execute(
                "SELECT id, type, title, status FROM entries "
                f"WHERE id IN ({placeholders})",
                related_ids,
            ).fetchall()
            by_id = {str(item["id"]): dict(item) for item in related_rows}
            related_entries = [
                by_id.get(
                    related_id,
                    {
                        "id": related_id,
                        "type": "unknown",
                        "title": related_id,
                        "status": "missing",
                    },
                )
                for related_id in related_ids
            ]
        entry["related_entries"] = related_entries
        lit_source_id = str(entry["provenance"].get("lit_source_id") or "")
        source = None
        if lit_source_id:
            source_row = conn.execute(
                "SELECT source_id, family_id, canonical_source_id, provider, "
                "source_version, title, authors, year, doi, url, "
                "length(COALESCE(abstract, '')) AS abstract_chars, fetched_at "
                "FROM lit_sources WHERE source_id = ?",
                (lit_source_id,),
            ).fetchone()
            if source_row is not None:
                source = dict(source_row)
                source["authors"] = _parse_json(source.get("authors"), [])
        entry["source"] = source
        versions = conn.execute(
            "SELECT version, changed_at, changed_by, reason FROM entry_versions "
            "WHERE entry_id = ? ORDER BY version",
            (entry_id,),
        ).fetchall()
        entry["versions"] = [dict(v) for v in versions]
        entry["version_count"] = len(versions)
        return entry
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _fetch_overview() -> dict[str, Any]:
    conn = _connect_ro()
    if conn is None:
        return _empty_overview()
    try:
        overview = _empty_overview()
        overview["entries"] = int(
            conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        )
        overview["sources"] = int(
            conn.execute("SELECT COUNT(*) FROM lit_sources").fetchone()[0]
        )
        overview["source_families"] = int(
            conn.execute(
                "SELECT COUNT(DISTINCT NULLIF(family_id, '')) FROM lit_sources"
            ).fetchone()[0]
        )
        overview["fetched_sources"] = int(
            conn.execute(
                "SELECT COUNT(*) FROM lit_sources "
                "WHERE fetched_at IS NOT NULL AND fetched_at <> ''"
            ).fetchone()[0]
        )
        overview["distilled_sources"] = int(
            conn.execute(
                "SELECT COUNT(DISTINCT source_id) FROM lit_distillations"
            ).fetchone()[0]
        )
        overview["distillations"] = int(
            conn.execute("SELECT COUNT(*) FROM lit_distillations").fetchone()[0]
        )
        overview["pending_reviews"] = int(
            conn.execute(
                "SELECT COUNT(*) FROM review_queue WHERE status = 'pending'"
            ).fetchone()[0]
        )
        overview["usage_reads"] = int(
            conn.execute("SELECT COUNT(*) FROM provenance_log").fetchone()[0]
        )
        for key in ("type", "status"):
            rows = conn.execute(
                f"SELECT {key} AS label, COUNT(*) AS count FROM entries GROUP BY {key}"
            ).fetchall()
            overview[f"by_{key}"] = {
                str(row["label"]): int(row["count"]) for row in rows
            }
        providers = conn.execute(
            "SELECT COALESCE(NULLIF(provider, ''), 'unknown') AS label, "
            "COUNT(*) AS count FROM lit_sources GROUP BY label"
        ).fetchall()
        overview["by_provider"] = {
            str(row["label"]): int(row["count"]) for row in providers
        }
        entries = overview["entries"]
        sources = overview["sources"]
        canonical = int(overview["by_status"].get("canonical", 0))
        overview["coverage"] = {
            "fetch_rate": round(overview["fetched_sources"] / sources, 4)
            if sources
            else 0.0,
            "distillation_rate": round(overview["distilled_sources"] / sources, 4)
            if sources
            else 0.0,
            "canonical_rate": round(canonical / entries, 4) if entries else 0.0,
        }
        historical_findings = int(
            conn.execute(
                "SELECT COUNT(*) FROM entries "
                "WHERE type = 'finding' AND source_type = 'historical_run'"
            ).fetchone()[0]
        )
        related_rows = conn.execute(
            "SELECT id, related_ids FROM entries "
            "WHERE status IN ('candidate', 'canonical')"
        ).fetchall()
        related_targets: set[str] = set()
        linked_entries: set[str] = set()
        for row in related_rows:
            values = _parse_json(row["related_ids"], [])
            if not isinstance(values, list):
                continue
            for target in values:
                target_id = str(target)
                if target_id:
                    linked_entries.add(str(row["id"]))
                    related_targets.add(target_id)
        distillation_entry_ids = {
            str(row[0])
            for row in conn.execute(
                "SELECT DISTINCT entry_id FROM lit_distillations"
            ).fetchall()
        }
        active_entry_ids = {str(row["id"]) for row in related_rows}
        connected = linked_entries | related_targets | distillation_entry_ids
        orphan_entries = len(active_entry_ids - connected)
        gaps: list[dict[str, Any]] = []
        undistilled = max(sources - overview["distilled_sources"], 0)
        if undistilled:
            gaps.append(
                {
                    "code": "undistilled_sources",
                    "label": "尚未精炼的来源",
                    "count": undistilled,
                    "severity": "high",
                    "hint": "优先把高相关来源编译成有引文定位的 Wiki 条目。",
                }
            )
        if orphan_entries:
            gaps.append(
                {
                    "code": "orphan_entries",
                    "label": "孤立条目",
                    "count": orphan_entries,
                    "severity": "medium",
                    "hint": "补充条目关系或来源绑定，避免知识停留为孤立卡片。",
                }
            )
        candidate_count = int(overview["by_status"].get("candidate", 0))
        if candidate_count:
            gaps.append(
                {
                    "code": "candidate_backlog",
                    "label": "候选条目积压",
                    "count": candidate_count,
                    "severity": "medium",
                    "hint": "按证据强度处理晋升、冲突或废弃审核。",
                }
            )
        if historical_findings:
            gaps.append(
                {
                    "code": "run_log_noise",
                    "label": "历史运行型发现",
                    "count": historical_findings,
                    "severity": "low",
                    "hint": "运行记录应先聚合，再把可复用结论沉淀进 Wiki。",
                }
            )
        overview["gaps"] = gaps
        return overview
    except sqlite3.Error:
        return _empty_overview()
    finally:
        conn.close()


def _source_stage(row: dict[str, Any]) -> str:
    if int(row.get("distillation_count") or 0) > 0:
        return "distilled"
    if row.get("fetched_at"):
        return "fetched"
    return "cached"


def _fetch_sources(
    provider: str, state: str, query: str, limit: int
) -> list[dict[str, Any]]:
    conn = _connect_ro()
    if conn is None:
        return []
    try:
        filters = ["COALESCE(s.is_preferred, 1) = 1"]
        params: list[Any] = []
        if provider:
            filters.append("s.provider = ?")
            params.append(provider)
        if query:
            like = f"%{_like_escape(query)}%"
            filters.append(
                "(s.source_id LIKE ? ESCAPE '\\' OR "
                "s.title LIKE ? ESCAPE '\\' OR s.authors LIKE ? ESCAPE '\\' OR "
                "s.doi LIKE ? ESCAPE '\\')"
            )
            params.extend((like, like, like, like))
        distillation_count = (
            "(SELECT COUNT(*) FROM lit_distillations d WHERE d.family_id = s.family_id)"
        )
        if state == "distilled":
            filters.append(f"{distillation_count} > 0")
        elif state == "fetched":
            filters.append(
                f"{distillation_count} = 0 AND "
                "s.fetched_at IS NOT NULL AND s.fetched_at <> ''"
            )
        elif state == "cached":
            filters.append(
                f"{distillation_count} = 0 AND "
                "(s.fetched_at IS NULL OR s.fetched_at = '')"
            )
        rows = conn.execute(
            "SELECT s.source_id, s.family_id, s.canonical_source_id, "
            "COALESCE(NULLIF(s.provider, ''), 'unknown') AS provider, "
            "s.source_version, s.title, s.authors, s.year, s.doi, s.url, "
            "length(COALESCE(s.abstract, '')) AS abstract_chars, "
            "s.fetched_at, s.last_seen_at, "
            f"{distillation_count} AS distillation_count "
            "FROM lit_sources s WHERE "
            + " AND ".join(filters)
            + " ORDER BY COALESCE(s.last_seen_at, s.fetched_at, '') DESC, "
            "s.year DESC, s.title LIMIT ?",
            (*params, limit),
        ).fetchall()
        results: list[dict[str, Any]] = []
        for raw in rows:
            row = dict(raw)
            row["authors"] = _parse_json(row.get("authors"), [])
            row["stage"] = _source_stage(row)
            results.append(row)
        return results
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _fetch_source(source_id: str) -> dict[str, Any] | None:
    conn = _connect_ro()
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT * FROM lit_sources WHERE source_id = ?", (source_id,)
        ).fetchone()
        if row is None:
            return None
        source = dict(row)
        source["authors"] = _parse_json(source.get("authors"), [])
        distillations = conn.execute(
            "SELECT d.focus, d.research_question, d.entry_id, d.relevance, "
            "d.created_at, e.type AS entry_type, e.title AS entry_title, "
            "e.status AS entry_status, e.confidence AS entry_confidence "
            "FROM lit_distillations d LEFT JOIN entries e ON e.id = d.entry_id "
            "WHERE d.family_id = ? ORDER BY d.created_at DESC",
            (source.get("family_id") or "",),
        ).fetchall()
        source["distillations"] = [dict(item) for item in distillations]
        source["distillation_count"] = len(distillations)
        source["stage"] = _source_stage(source)
        return source
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _fetch_graph(limit: int) -> dict[str, Any]:
    conn = _connect_ro()
    if conn is None:
        return {
            "nodes": [],
            "edges": [],
            "stats": {"nodes": 0, "edges": 0, "orphans": 0},
        }
    try:
        entry_rows = conn.execute(
            "SELECT id, type, title, status, confidence, source_type, "
            "related_ids, provenance FROM entries "
            "WHERE status IN ('candidate', 'canonical') "
            "AND NOT (type = 'finding' AND source_type = 'historical_run') "
            "ORDER BY CASE status WHEN 'canonical' THEN 0 ELSE 1 END, "
            "updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        entry_ids = {str(row["id"]) for row in entry_rows}
        nodes: dict[str, dict[str, Any]] = {}
        entry_provenance: dict[str, dict[str, Any]] = {}
        entry_relations: dict[str, list[str]] = {}
        for raw in entry_rows:
            row = dict(raw)
            provenance = _parse_json(row.pop("provenance"), {})
            related_ids = _parse_json(row.pop("related_ids"), [])
            entry_id = str(row["id"])
            entry_provenance[entry_id] = (
                provenance if isinstance(provenance, dict) else {}
            )
            entry_relations[entry_id] = (
                [str(value) for value in related_ids]
                if isinstance(related_ids, list)
                else []
            )
            evidence = entry_provenance[entry_id].get("evidence_map", {})
            nodes[entry_id] = {
                **row,
                "kind": "entry",
                "evidence_count": len(evidence) if isinstance(evidence, dict) else 0,
                "degree": 0,
            }

        source_ids = {
            str(provenance.get("lit_source_id"))
            for provenance in entry_provenance.values()
            if provenance.get("lit_source_id")
        }
        if source_ids:
            placeholders = ", ".join("?" for _ in source_ids)
            source_rows = conn.execute(
                "SELECT source_id, family_id, provider, title, year, doi, url, "
                "length(COALESCE(abstract, '')) AS abstract_chars "
                f"FROM lit_sources WHERE source_id IN ({placeholders})",
                tuple(source_ids),
            ).fetchall()
            for raw in source_rows:
                row = dict(raw)
                source_node_id = f"source:{row['source_id']}"
                nodes[source_node_id] = {
                    "id": source_node_id,
                    "kind": "source",
                    "type": "literature_source",
                    "status": "source",
                    "confidence": "",
                    "source_type": "literature",
                    "degree": 0,
                    **row,
                }

        edges: list[dict[str, Any]] = []
        edge_keys: set[tuple[str, str, str]] = set()

        def add_edge(
            source: str, target: str, relation: str, weight: float, signal: str
        ) -> None:
            if source not in nodes or target not in nodes or source == target:
                return
            canonical = (*sorted((source, target)), relation)
            if canonical in edge_keys:
                return
            edge_keys.add(canonical)
            edges.append(
                {
                    "source": source,
                    "target": target,
                    "relation": relation,
                    "weight": weight,
                    "signal": signal,
                }
            )
            nodes[source]["degree"] += 1
            nodes[target]["degree"] += 1

        for entry_id, related_ids in entry_relations.items():
            for related_id in related_ids:
                if related_id in entry_ids:
                    add_edge(entry_id, related_id, "related_to", 3.0, "direct_link")
        for entry_id, provenance in entry_provenance.items():
            lit_source_id = str(provenance.get("lit_source_id") or "")
            if lit_source_id:
                add_edge(
                    f"source:{lit_source_id}",
                    entry_id,
                    "distilled_into",
                    4.0,
                    "source_grounding",
                )

        # Entries independently distilled from the same literature family gain
        # a weaker semantic edge, mirroring LLM-Wiki's source-overlap signal.
        family_members: dict[str, list[str]] = defaultdict(list)
        for entry_id, provenance in entry_provenance.items():
            family_id = str(provenance.get("lit_family_id") or "")
            if family_id:
                family_members[family_id].append(entry_id)
        for member_ids in family_members.values():
            for index, source_id in enumerate(member_ids):
                for target_id in member_ids[index + 1 :]:
                    add_edge(
                        source_id,
                        target_id,
                        "shares_source",
                        2.0,
                        "source_overlap",
                    )
        node_values = sorted(
            nodes.values(),
            key=lambda item: (
                -int(item.get("degree") or 0),
                0 if item.get("kind") == "entry" else 1,
                str(item.get("title") or ""),
            ),
        )
        orphans = sum(1 for node in node_values if int(node.get("degree") or 0) == 0)
        kind_counts = Counter(str(node.get("kind")) for node in node_values)
        return {
            "nodes": node_values,
            "edges": edges,
            "stats": {
                "nodes": len(node_values),
                "edges": len(edges),
                "orphans": orphans,
                "entry_nodes": kind_counts.get("entry", 0),
                "source_nodes": kind_counts.get("source", 0),
            },
        }
    except sqlite3.Error:
        return {
            "nodes": [],
            "edges": [],
            "stats": {"nodes": 0, "edges": 0, "orphans": 0},
        }
    finally:
        conn.close()


def _fetch_review_queue(status: str) -> list[dict[str, Any]]:
    conn = _connect_ro()
    if conn is None:
        return []
    try:
        if status and status != "all":
            rows = conn.execute(
                "SELECT * FROM review_queue WHERE status = ? ORDER BY id DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM review_queue ORDER BY id DESC"
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["payload"] = _parse_json(item.get("payload"), {})
            items.append(item)
        return items
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _fetch_usage(run_id: str, limit: int) -> list[dict[str, Any]]:
    conn = _connect_ro()
    if conn is None:
        return []
    try:
        base_sql = (
            "SELECT p.id, p.run_id, p.agent, p.entry_id, p.purpose, p.ts, "
            "e.title AS entry_title FROM provenance_log p "
            "LEFT JOIN entries e ON e.id = p.entry_id "
        )
        if run_id:
            rows = conn.execute(
                base_sql + "WHERE p.run_id = ? ORDER BY p.id", (run_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                base_sql + "ORDER BY p.id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


async def list_entries(request: Request) -> JSONResponse:
    """Return the filtered Wiki entry list."""

    params = request.query_params
    entries = await asyncio.to_thread(
        _fetch_entries,
        params.get("type", "").strip(),
        params.get("status", "").strip(),
        params.get("q", "").strip(),
        _clamp_limit(params.get("limit")),
    )
    return _json(entries)


async def get_overview(request: Request) -> JSONResponse:
    """Return source and page coverage with actionable knowledge gaps."""

    return _json(await asyncio.to_thread(_fetch_overview))


async def get_entry(request: Request) -> JSONResponse:
    """Return one complete Wiki entry with provenance and versions."""

    entry_id = request.path_params["entry_id"]
    entry = await asyncio.to_thread(_fetch_entry, entry_id)
    if entry is None:
        return _json({"error": f"entry not found: {entry_id}"}, status_code=404)
    return _json(entry)


async def list_sources(request: Request) -> JSONResponse:
    """Return the filtered raw-source catalog."""

    params = request.query_params
    sources = await asyncio.to_thread(
        _fetch_sources,
        params.get("provider", "").strip(),
        params.get("state", "").strip(),
        params.get("q", "").strip(),
        _clamp_limit(params.get("limit")),
    )
    return _json(sources)


async def get_source(request: Request) -> JSONResponse:
    """Return raw-source metadata and its compiled Wiki entries."""

    source_id = request.path_params["source_id"]
    source = await asyncio.to_thread(_fetch_source, source_id)
    if source is None:
        return _json({"error": f"source not found: {source_id}"}, status_code=404)
    return _json(source)


async def get_graph(request: Request) -> JSONResponse:
    """Return a bounded source-to-entry knowledge graph."""

    limit = min(_clamp_limit(request.query_params.get("limit")), _GRAPH_MAX_NODES)
    return _json(await asyncio.to_thread(_fetch_graph, limit))


async def list_review_queue(request: Request) -> JSONResponse:
    """Return the review and revalidation queue."""

    status = request.query_params.get("status", "pending").strip() or "pending"
    items = await asyncio.to_thread(_fetch_review_queue, status)
    return _json(items)


async def list_usage(request: Request) -> JSONResponse:
    """Return the Wiki usage provenance log."""

    params = request.query_params
    rows = await asyncio.to_thread(
        _fetch_usage,
        params.get("run_id", "").strip(),
        _clamp_limit(params.get("limit")),
    )
    return _json(rows)


KB_ROUTES = [
    Route("/api/kb/overview", get_overview, methods=["GET"]),
    Route("/api/kb/entries", list_entries, methods=["GET"]),
    Route("/api/kb/entries/{entry_id}", get_entry, methods=["GET"]),
    Route("/api/kb/sources", list_sources, methods=["GET"]),
    Route("/api/kb/sources/{source_id:path}", get_source, methods=["GET"]),
    Route("/api/kb/graph", get_graph, methods=["GET"]),
    Route("/api/kb/review_queue", list_review_queue, methods=["GET"]),
    Route("/api/kb/usage", list_usage, methods=["GET"]),
]
