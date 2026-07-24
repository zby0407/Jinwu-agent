"""SQLite storage for the knowledge base (plan §5.1).

Database lives at ``$JW_DATA_DIR/knowledge.db`` or
``~/.jw/knowledge.db`` (same rule as ``jw/paths.py``;
re-implemented here so ``src/knowledge_base`` stays pure stdlib with no
import side effects). WAL mode, normalized literature-family/distillation
tables, and one FTS5 virtual table.

Deviation from the plan's exact FTS DDL: ``entries_fts`` is a standalone
FTS5 table (not ``content='entries'``) because we index *pre-tokenized*
text (CJK bigrams, see ``fts.py``) which the built-in triggers/external
content mode cannot produce. Columns stay ``(id, title, content)`` and the
table is maintained on every write by the store itself.
"""

from __future__ import annotations

import functools
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .fts import query_to_match, tokenize
from .literature_identity import (
    first_author_key,
    focus_key,
    infer_provider,
    normalize_doi,
    normalize_focus,
    normalize_text_key,
    stable_family_id,
    title_author_key,
)


def _locked(fn):
    """Serialize a store method through the instance RLock.

    The same ``KnowledgeStore`` (e.g. the module-level singleton in
    ``service.py``) is shared across async-subagent worker threads. Python's
    ``sqlite3`` driver serializes individual statements, but multi-statement
    writes (entry + version snapshot + FTS sync + commit) would interleave
    across threads without an explicit lock.
    """

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)

    return wrapper


_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_ref TEXT NOT NULL,
  confidence TEXT NOT NULL,
  status TEXT NOT NULL,
  valid_range TEXT,
  related_ids TEXT,
  provenance TEXT,
  version INTEGER NOT NULL,
  created_at TEXT, updated_at TEXT, created_by TEXT
);
CREATE TABLE IF NOT EXISTS entry_versions (
  entry_id TEXT, version INTEGER, snapshot TEXT, changed_at TEXT, changed_by TEXT, reason TEXT,
  PRIMARY KEY (entry_id, version)
);
CREATE TABLE IF NOT EXISTS provenance_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT, agent TEXT, entry_id TEXT, purpose TEXT, ts TEXT
);
CREATE TABLE IF NOT EXISTS lit_sources (
  source_id TEXT PRIMARY KEY,
  title TEXT, authors TEXT, year INTEGER, doi TEXT, url TEXT,
  abstract TEXT, fetched_at TEXT, distilled_entry_id TEXT,
  family_id TEXT, canonical_source_id TEXT, provider TEXT,
  source_version TEXT, normalized_doi TEXT, title_key TEXT,
  first_author_key TEXT, is_preferred INTEGER DEFAULT 1,
  last_seen_at TEXT
);
CREATE TABLE IF NOT EXISTS lit_distillations (
  source_id TEXT NOT NULL,
  family_id TEXT NOT NULL,
  focus_key TEXT NOT NULL,
  focus TEXT NOT NULL,
  research_question TEXT NOT NULL,
  research_request_sha256 TEXT,
  entry_id TEXT NOT NULL,
  relevance TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (source_id, focus_key),
  UNIQUE (family_id, focus_key),
  UNIQUE (entry_id),
  FOREIGN KEY (source_id) REFERENCES lit_sources(source_id)
);
CREATE TABLE IF NOT EXISTS review_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT,
  entry_id TEXT, payload TEXT,
  status TEXT,
  reviewer TEXT, decided_at TEXT, note TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(id UNINDEXED, title, content);
"""

_ENTRY_COLUMNS = (
    "id",
    "type",
    "title",
    "content",
    "source_type",
    "source_ref",
    "confidence",
    "status",
    "valid_range",
    "related_ids",
    "provenance",
    "version",
    "created_at",
    "updated_at",
    "created_by",
)


def utc_now() -> str:
    """ISO-8601 UTC timestamp used for all rows."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_db_path() -> Path:
    """``~/.jw/knowledge.db`` (env-overridable like paths.py)."""

    override = os.getenv("JW_DATA_DIR")
    base = Path(override).expanduser() if override else Path.home() / ".jw"
    return base / "knowledge.db"


def default_export_dir() -> Path:
    """``<repo root>/knowledge_base/`` markdown export directory.

    ``JW_KB_EXPORT_DIR`` overrides the location (used by tests to
    keep the repository export tree clean); unset keeps the P1 default.
    """

    override = os.getenv("JW_KB_EXPORT_DIR")
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[2] / "knowledge_base"


def _content_text(entry: dict[str, Any]) -> str:
    """Flatten title + content values into indexable text."""

    parts = [str(entry.get("title", ""))]
    content = entry.get("content", {})
    if isinstance(content, dict):
        for value in content.values():
            if isinstance(value, list):
                parts.extend(str(item) for item in value)
            else:
                parts.append(str(value))
    return " ".join(parts)


class KnowledgeStore:
    """Thin SQLite wrapper; all business logic lives in ``service.py``."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        export_dir: str | Path | None = None,
    ) -> None:
        self.db_path = Path(db_path) if db_path else default_db_path()
        self.export_dir = Path(export_dir) if export_dir else default_export_dir()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        # check_same_thread=False: the store is shared across async-subagent
        # worker threads; statement serialization is left to the driver and
        # multi-statement writes to ``_locked`` (see above).
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._migrate_literature_schema()
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "KnowledgeStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def locked(self):
        """Hold the store's re-entrant lock across a multi-method operation."""

        with self._lock:
            yield

    def _column_names(self, table: str) -> set[str]:
        return {
            str(row["name"])
            for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        }

    def _migrate_literature_schema(self) -> None:
        """Upgrade pre-focus/family databases in place without losing rows."""

        columns = self._column_names("lit_sources")
        additions = {
            "family_id": "TEXT",
            "canonical_source_id": "TEXT",
            "provider": "TEXT",
            "source_version": "TEXT",
            "normalized_doi": "TEXT",
            "title_key": "TEXT",
            "first_author_key": "TEXT",
            "is_preferred": "INTEGER DEFAULT 1",
            "last_seen_at": "TEXT",
        }
        for name, ddl in additions.items():
            if name not in columns:
                self._conn.execute(f"ALTER TABLE lit_sources ADD COLUMN {name} {ddl}")

        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS lit_distillations (
              source_id TEXT NOT NULL,
              family_id TEXT NOT NULL,
              focus_key TEXT NOT NULL,
              focus TEXT NOT NULL,
              research_question TEXT NOT NULL,
              research_request_sha256 TEXT,
              entry_id TEXT NOT NULL,
              relevance TEXT NOT NULL,
              created_at TEXT NOT NULL,
              PRIMARY KEY (source_id, focus_key),
              UNIQUE (family_id, focus_key),
              UNIQUE (entry_id),
              FOREIGN KEY (source_id) REFERENCES lit_sources(source_id)
            );
            CREATE INDEX IF NOT EXISTS idx_lit_sources_family
              ON lit_sources(family_id);
            CREATE INDEX IF NOT EXISTS idx_lit_sources_normalized_doi
              ON lit_sources(normalized_doi);
            CREATE INDEX IF NOT EXISTS idx_lit_sources_title_author
              ON lit_sources(title_key, first_author_key);
            CREATE INDEX IF NOT EXISTS idx_lit_distillations_family_focus
              ON lit_distillations(family_id, focus_key);
            """
        )

        rows = self._conn.execute(
            "SELECT * FROM lit_sources ORDER BY source_id"
        ).fetchall()
        doi_families: dict[str, str] = {}
        title_families: dict[str, str] = {}
        for raw in rows:
            row = dict(raw)
            authors = row.get("authors") or "[]"
            doi_key = normalize_doi(row.get("doi"))
            title_key = normalize_text_key(row.get("title"))
            author_key = first_author_key(authors)
            title_identity = title_author_key(row.get("title"), authors)
            family_id = str(row.get("family_id") or "")
            if not family_id and doi_key:
                family_id = doi_families.get(doi_key, "")
            if not family_id and title_identity:
                family_id = title_families.get(title_identity, "")
            if not family_id:
                family_id = stable_family_id(
                    title=row.get("title"),
                    authors=authors,
                    doi=doi_key,
                    source_id=row.get("source_id"),
                )
            if doi_key:
                doi_families[doi_key] = family_id
            if title_identity:
                title_families[title_identity] = family_id
            self._conn.execute(
                "UPDATE lit_sources SET family_id = ?, provider = ?, "
                "normalized_doi = ?, title_key = ?, first_author_key = ?, "
                "last_seen_at = COALESCE(last_seen_at, fetched_at, ?) "
                "WHERE source_id = ?",
                (
                    family_id,
                    row.get("provider") or infer_provider(row.get("source_id")),
                    doi_key,
                    title_key,
                    author_key,
                    utc_now(),
                    row.get("source_id"),
                ),
            )

        family_ids = [
            row["family_id"]
            for row in self._conn.execute(
                "SELECT DISTINCT family_id FROM lit_sources WHERE family_id <> ''"
            ).fetchall()
        ]
        for family_id in family_ids:
            self._refresh_preferred_source(family_id)

        legacy_rows = self._conn.execute(
            "SELECT source_id, family_id, distilled_entry_id FROM lit_sources "
            "WHERE distilled_entry_id IS NOT NULL AND distilled_entry_id <> ''"
        ).fetchall()
        for legacy in legacy_rows:
            entry = self.get_entry(str(legacy["distilled_entry_id"]))
            provenance = entry.get("provenance", {}) if entry else {}
            focus = str(provenance.get("distill_focus") or "").strip()
            if not focus:
                focus = f"legacy-unbound:{legacy['distilled_entry_id']}"
            self._conn.execute(
                "INSERT OR IGNORE INTO lit_distillations "
                "(source_id, family_id, focus_key, focus, research_question, "
                "research_request_sha256, entry_id, relevance, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    legacy["source_id"],
                    legacy["family_id"],
                    focus_key(focus),
                    normalize_focus(focus),
                    str(provenance.get("research_question") or ""),
                    str(provenance.get("research_request_sha256") or ""),
                    legacy["distilled_entry_id"],
                    "legacy_unverified",
                    str(provenance.get("distilled_at") or utc_now()),
                ),
            )
        self._repair_legacy_literature_entries()

    def _repair_legacy_literature_entries(self) -> None:
        """Quarantine pre-contract distillations and cap their confidence."""

        rows = self._conn.execute(
            "SELECT * FROM entries WHERE source_type = 'literature' ORDER BY id"
        ).fetchall()
        for raw in rows:
            entry = self._row_to_entry(raw)
            provenance = dict(entry.get("provenance") or {})
            if not provenance.get("lit_source_id"):
                continue
            changed = False
            needs_review = False
            if entry.get("confidence") == "high":
                entry["confidence"] = "medium"
                provenance["confidence_migration"] = (
                    "single-source abstract legacy cap: high -> medium"
                )
                changed = True
            if provenance.get("evidence_scope") != "abstract_only":
                provenance["evidence_scope"] = "abstract_only"
                changed = True
            if provenance.get("independent_source_count") != 1:
                provenance["independent_source_count"] = 1
                changed = True
            relevance = provenance.get("relevance")
            task_bound_and_validated = bool(
                (
                    isinstance(relevance, dict)
                    and relevance.get("classification") == "direct_support"
                    and provenance.get("research_question")
                    and provenance.get("research_request_sha256")
                )
                or (
                    provenance.get("revalidated_at")
                    and provenance.get("human_reviewed")
                )
            )
            legacy_relevance = {"classification": "legacy_unverified"}
            if (
                not task_bound_and_validated
                and provenance.get("relevance") != legacy_relevance
            ):
                provenance["relevance"] = legacy_relevance
                changed = True
            if not task_bound_and_validated and not provenance.get("grounding_blocked"):
                provenance["grounding_blocked"] = True
                provenance["grounding_block_reason"] = (
                    "legacy literature distillation predates task-bound relevance validation"
                )
                changed = True
                needs_review = entry.get("status") not in {"deprecated", "superseded"}
            legacy_doi_promotion = bool(
                provenance.get("auto_rule") == "literature_support"
                and not provenance.get("human_reviewed")
            )
            if legacy_doi_promotion and not provenance.get(
                "legacy_promotion_invalidated"
            ):
                provenance["legacy_promotion_invalidated"] = True
                changed = True
            if legacy_doi_promotion and entry.get("status") == "canonical":
                provenance["legacy_status_before_migration"] = "canonical"
                entry["status"] = "candidate"
                changed = True
            needs_review = bool(provenance.get("grounding_blocked")) and entry.get(
                "status"
            ) not in {"deprecated", "superseded"}
            if changed:
                entry["provenance"] = provenance
                entry["version"] = int(entry.get("version") or 0) + 1
                entry["updated_at"] = utc_now()
                self.update_entry(
                    entry,
                    changed_by="knowledge-base-migration",
                    reason="legacy_literature_revalidation_required",
                )
            if needs_review:
                pending = self._conn.execute(
                    "SELECT id FROM review_queue WHERE kind = 'revalidate' "
                    "AND entry_id = ? AND status = 'pending' LIMIT 1",
                    (entry["id"],),
                ).fetchone()
                if pending is None:
                    self.add_review_item(
                        kind="revalidate",
                        entry_id=entry["id"],
                        payload={
                            "reason": provenance.get("grounding_block_reason", ""),
                            "required_checks": [
                                "research-question/focus binding",
                                "source-to-focus direct relevance",
                                "claim-to-quote support",
                                "confidence calibration",
                            ],
                        },
                    )

    # ------------------------------------------------------------------
    # row <-> dict
    # ------------------------------------------------------------------
    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> dict[str, Any]:
        entry = dict(row)
        for key, fallback in (("content", {}), ("related_ids", []), ("provenance", {})):
            raw = entry.get(key)
            if isinstance(raw, str) and raw:
                entry[key] = json.loads(raw)
            elif raw is None:
                entry[key] = fallback
        return entry

    # ------------------------------------------------------------------
    # entries
    # ------------------------------------------------------------------
    def get_entry(self, entry_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM entries WHERE id = ?", (entry_id,)
        ).fetchone()
        return self._row_to_entry(row) if row else None

    def find_entry_by_source(
        self, source_ref: str, title: str
    ) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM entries WHERE source_ref = ? AND title = ? LIMIT 1",
            (source_ref, title),
        ).fetchone()
        return self._row_to_entry(row) if row else None

    def next_seq(self, prefix: str) -> int:
        """Next sequence number for an id prefix like ``kb_concept_sun_``."""

        rows = self._conn.execute(
            "SELECT id FROM entries WHERE id LIKE ?", (prefix + "%",)
        ).fetchall()
        seq = 0
        for row in rows:
            suffix = row["id"][len(prefix) :]
            if suffix.isdigit():
                seq = max(seq, int(suffix))
        return seq + 1

    @_locked
    def create_entry(
        self, entry: dict[str, Any], *, changed_by: str, reason: str
    ) -> None:
        values = {key: entry.get(key) for key in _ENTRY_COLUMNS}
        for key in ("content", "related_ids", "provenance"):
            values[key] = json.dumps(
                values[key] or ({} if key != "related_ids" else []), ensure_ascii=False
            )
        self._conn.execute(
            f"INSERT INTO entries ({', '.join(_ENTRY_COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in _ENTRY_COLUMNS)})",
            tuple(values[key] for key in _ENTRY_COLUMNS),
        )
        self._conn.execute(
            "INSERT INTO entries_fts (rowid, id, title, content) VALUES "
            "((SELECT rowid FROM entries WHERE id = ?), ?, ?, ?)",
            (
                entry["id"],
                entry["id"],
                tokenize(str(entry.get("title", ""))),
                tokenize(_content_text(entry)),
            ),
        )
        self._snapshot(entry, changed_by=changed_by, reason=reason)
        self._conn.commit()

    @_locked
    def update_entry(
        self, entry: dict[str, Any], *, changed_by: str, reason: str
    ) -> None:
        values = {key: entry.get(key) for key in _ENTRY_COLUMNS}
        for key in ("content", "related_ids", "provenance"):
            values[key] = json.dumps(
                values[key] or ({} if key != "related_ids" else []), ensure_ascii=False
            )
        assignments = ", ".join(f"{key} = ?" for key in _ENTRY_COLUMNS if key != "id")
        self._conn.execute(
            f"UPDATE entries SET {assignments} WHERE id = ?",
            tuple(values[key] for key in _ENTRY_COLUMNS if key != "id")
            + (entry["id"],),
        )
        self._conn.execute("DELETE FROM entries_fts WHERE id = ?", (entry["id"],))
        self._conn.execute(
            "INSERT INTO entries_fts (rowid, id, title, content) VALUES "
            "((SELECT rowid FROM entries WHERE id = ?), ?, ?, ?)",
            (
                entry["id"],
                entry["id"],
                tokenize(str(entry.get("title", ""))),
                tokenize(_content_text(entry)),
            ),
        )
        self._snapshot(entry, changed_by=changed_by, reason=reason)
        self._conn.commit()

    def _snapshot(self, entry: dict[str, Any], *, changed_by: str, reason: str) -> None:
        snapshot = dict(entry)
        for key in ("content", "related_ids", "provenance"):
            value = snapshot.get(key)
            if isinstance(value, str):
                snapshot[key] = (
                    json.loads(value) if value else ({} if key != "related_ids" else [])
                )
        self._conn.execute(
            "INSERT OR REPLACE INTO entry_versions "
            "(entry_id, version, snapshot, changed_at, changed_by, reason) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                entry["id"],
                int(entry.get("version", 1)),
                json.dumps(snapshot, ensure_ascii=False),
                utc_now(),
                changed_by,
                reason,
            ),
        )

    def get_version(self, entry_id: str, version: int) -> dict[str, Any] | None:
        """Return the historical snapshot at ``version`` (rollback source)."""

        row = self._conn.execute(
            "SELECT snapshot FROM entry_versions WHERE entry_id = ? AND version = ?",
            (entry_id, version),
        ).fetchone()
        return json.loads(row["snapshot"]) if row else None

    def list_versions(self, entry_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT version, changed_at, changed_by, reason FROM entry_versions "
            "WHERE entry_id = ? ORDER BY version",
            (entry_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # search
    # ------------------------------------------------------------------
    def search(
        self,
        query: str = "",
        *,
        entry_type: str = "",
        status: str = "",
        confidence: str = "",
        valid_range: str = "",
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        """FTS5 keyword search + structured filters.

        Default visibility: canonical + candidate (deprecated/superseded are
        excluded unless ``status`` explicitly asks for them).
        """

        filters: list[str] = []
        params: list[Any] = []
        filters.append(
            "COALESCE(json_extract(e.provenance, '$.grounding_blocked'), 0) != 1"
        )
        if status:
            filters.append("e.status = ?")
            params.append(status)
        else:
            filters.append("e.status IN ('canonical', 'candidate')")
        if entry_type:
            filters.append("e.type = ?")
            params.append(entry_type)
        if confidence:
            filters.append("e.confidence = ?")
            params.append(confidence)
        if valid_range:
            filters.append("e.valid_range LIKE ?")
            params.append(f"%{valid_range}%")
        where = " AND ".join(filters) if filters else "1=1"

        match = query_to_match(query) if query else ""
        if match:
            sql = (
                "SELECT e.*, bm25(entries_fts) AS rank FROM entries_fts f "
                "JOIN entries e ON e.rowid = f.rowid "
                f"WHERE entries_fts MATCH ? AND {where} "
                "ORDER BY rank LIMIT ?"
            )
            rows = self._conn.execute(sql, (match, *params, limit)).fetchall()
        else:
            sql = (
                f"SELECT e.*, 0.0 AS rank FROM entries e WHERE {where} "
                "ORDER BY e.updated_at DESC LIMIT ?"
            )
            rows = self._conn.execute(sql, (*params, limit)).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            entry = self._row_to_entry(row)
            entry["rank"] = row["rank"]
            results.append(entry)
        return results

    def count_by(self, key: str, status: str | None = None) -> dict[str, int]:
        if key not in {"type", "status", "confidence", "source_type"}:
            raise ValueError(f"unsupported count key: {key}")
        sql = f"SELECT {key} AS k, COUNT(*) AS n FROM entries"
        params: tuple[Any, ...] = ()
        if status:
            sql += " WHERE status = ?"
            params = (status,)
        sql += f" GROUP BY {key}"
        return {row["k"]: row["n"] for row in self._conn.execute(sql, params)}

    # ------------------------------------------------------------------
    # provenance_log (R4)
    # ------------------------------------------------------------------
    @_locked
    def log_provenance(
        self, *, run_id: str, agent: str, entry_id: str, purpose: str
    ) -> None:
        self._conn.execute(
            "INSERT INTO provenance_log (run_id, agent, entry_id, purpose, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, agent, entry_id, purpose, utc_now()),
        )
        self._conn.commit()

    def provenance_for_run(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM provenance_log WHERE run_id = ? ORDER BY id", (run_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def provenance_for_entry(self, entry_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM provenance_log WHERE entry_id = ? ORDER BY id",
            (entry_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def distinct_run_ids(self, entry_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT run_id FROM provenance_log "
            "WHERE entry_id = ? AND run_id IS NOT NULL AND run_id != ''",
            (entry_id,),
        ).fetchall()
        return [row["run_id"] for row in rows]

    def entries_proposed_by_run(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM entries WHERE json_extract(provenance, '$.run_id') = ? "
            "ORDER BY created_at",
            (run_id,),
        ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    # ------------------------------------------------------------------
    # review_queue
    # ------------------------------------------------------------------
    @_locked
    def add_review_item(
        self,
        *,
        kind: str,
        entry_id: str,
        payload: dict[str, Any],
        status: str = "pending",
        reviewer: str = "",
        note: str = "",
    ) -> int:
        cursor = self._conn.execute(
            "INSERT INTO review_queue (kind, entry_id, payload, status, reviewer, "
            "decided_at, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                kind,
                entry_id,
                json.dumps(payload, ensure_ascii=False),
                status,
                reviewer,
                utc_now() if status == "auto_approved" else None,
                note,
            ),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def get_review_item(self, queue_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM review_queue WHERE id = ?", (queue_id,)
        ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["payload"] = json.loads(item["payload"]) if item.get("payload") else {}
        return item

    @_locked
    def decide_review_item(
        self, queue_id: int, *, status: str, reviewer: str, note: str
    ) -> None:
        self._conn.execute(
            "UPDATE review_queue SET status = ?, reviewer = ?, decided_at = ?, "
            "note = ? WHERE id = ?",
            (status, reviewer, utc_now(), note, queue_id),
        )
        self._conn.commit()

    def pending_review_items(
        self, *, kind: str = "", entry_id: str = ""
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM review_queue WHERE status = 'pending'"
        params: list[Any] = []
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        if entry_id:
            sql += " AND (entry_id = ? OR payload LIKE ?)"
            params.extend((entry_id, f'%"{entry_id}"%'))
        sql += " ORDER BY id"
        items: list[dict[str, Any]] = []
        for row in self._conn.execute(sql, params).fetchall():
            item = dict(row)
            item["payload"] = json.loads(item["payload"]) if item.get("payload") else {}
            items.append(item)
        return items

    # ------------------------------------------------------------------
    # lit_sources (plan §5.3 literature cache)
    # ------------------------------------------------------------------
    _LIT_COLUMNS = (
        "source_id",
        "title",
        "authors",
        "year",
        "doi",
        "url",
        "abstract",
        "fetched_at",
        "distilled_entry_id",
        "family_id",
        "canonical_source_id",
        "provider",
        "source_version",
        "normalized_doi",
        "title_key",
        "first_author_key",
        "is_preferred",
        "last_seen_at",
    )

    def get_lit_source(self, source_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM lit_sources WHERE source_id = ?", (source_id,)
        ).fetchone()
        if not row:
            return None
        record = dict(row)
        raw_authors = record.get("authors")
        if isinstance(raw_authors, str) and raw_authors:
            record["authors"] = json.loads(raw_authors)
        elif raw_authors is None:
            record["authors"] = []
        return record

    def resolve_lit_source(self, source_id: str) -> dict[str, Any] | None:
        """Resolve any family member to the currently preferred source row."""

        row = self.get_lit_source(source_id)
        if row is None:
            return None
        canonical_id = str(row.get("canonical_source_id") or source_id)
        return self.get_lit_source(canonical_id) or row

    def _family_for_record(self, record: dict[str, Any]) -> str:
        requested = str(record.get("family_id") or "").strip()
        if requested:
            return requested
        source_id = str(record.get("source_id") or "").strip()
        if source_id:
            existing = self._conn.execute(
                "SELECT family_id FROM lit_sources WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            if existing and existing["family_id"]:
                return str(existing["family_id"])
        doi_key = normalize_doi(record.get("doi"))
        if doi_key:
            row = self._conn.execute(
                "SELECT family_id FROM lit_sources "
                "WHERE normalized_doi = ? AND family_id <> '' LIMIT 1",
                (doi_key,),
            ).fetchone()
            if row:
                return str(row["family_id"])
        title_key = normalize_text_key(record.get("title"))
        author_key = first_author_key(record.get("authors"))
        if title_key and author_key:
            row = self._conn.execute(
                "SELECT family_id FROM lit_sources "
                "WHERE title_key = ? AND first_author_key = ? "
                "AND family_id <> '' LIMIT 1",
                (title_key, author_key),
            ).fetchone()
            if row:
                return str(row["family_id"])
        return stable_family_id(
            title=record.get("title"),
            authors=record.get("authors"),
            doi=doi_key,
            source_id=record.get("source_id"),
        )

    def _refresh_preferred_source(self, family_id: str) -> str:
        preferred = self._conn.execute(
            "SELECT source_id FROM lit_sources WHERE family_id = ? "
            "ORDER BY COALESCE(year, 0) DESC, "
            "CASE WHEN normalized_doi <> '' THEN 1 ELSE 0 END DESC, "
            "CASE provider WHEN 'openalex' THEN 2 WHEN 'arxiv' THEN 1 ELSE 0 END DESC, "
            "CAST(COALESCE(source_version, '0') AS INTEGER) DESC, source_id ASC LIMIT 1",
            (family_id,),
        ).fetchone()
        if not preferred:
            return ""
        source_id = str(preferred["source_id"])
        self._conn.execute(
            "UPDATE lit_sources SET canonical_source_id = ?, "
            "is_preferred = CASE WHEN source_id = ? THEN 1 ELSE 0 END "
            "WHERE family_id = ?",
            (source_id, source_id, family_id),
        )
        return source_id

    @_locked
    def upsert_lit_source(self, record: dict[str, Any]) -> dict[str, Any]:
        """Insert or refresh a literature cache row.

        Provider ids identify versions; ``family_id`` groups preprint, journal,
        and updated-review variants. Re-searches refresh metadata/abstracts so
        a cached arXiv v1 cannot permanently hide v2.
        """

        normalized = dict(record)
        normalized["family_id"] = self._family_for_record(normalized)
        normalized["provider"] = normalized.get("provider") or infer_provider(
            normalized.get("source_id")
        )
        normalized["normalized_doi"] = normalize_doi(normalized.get("doi"))
        normalized["title_key"] = normalize_text_key(normalized.get("title"))
        normalized["first_author_key"] = first_author_key(normalized.get("authors"))
        normalized["last_seen_at"] = utc_now()
        normalized.setdefault("is_preferred", 1)
        values = {key: normalized.get(key) for key in self._LIT_COLUMNS}
        authors = values["authors"]
        if not isinstance(authors, str):
            values["authors"] = json.dumps(authors or [], ensure_ascii=False)
        self._conn.execute(
            f"INSERT INTO lit_sources ({', '.join(self._LIT_COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in self._LIT_COLUMNS)}) "
            "ON CONFLICT(source_id) DO UPDATE SET "
            "title = excluded.title, authors = excluded.authors, "
            "year = excluded.year, doi = excluded.doi, url = excluded.url, "
            "abstract = excluded.abstract, family_id = excluded.family_id, "
            "provider = excluded.provider, source_version = excluded.source_version, "
            "normalized_doi = excluded.normalized_doi, title_key = excluded.title_key, "
            "first_author_key = excluded.first_author_key, "
            "last_seen_at = excluded.last_seen_at, "
            "fetched_at = COALESCE(excluded.fetched_at, lit_sources.fetched_at), "
            "distilled_entry_id = COALESCE("
            "excluded.distilled_entry_id, lit_sources.distilled_entry_id)",
            tuple(values[key] for key in self._LIT_COLUMNS),
        )
        self._refresh_preferred_source(str(normalized["family_id"]))
        self._conn.commit()
        return self.get_lit_source(str(normalized["source_id"])) or {}

    @_locked
    def mark_lit_fetched(self, source_id: str) -> None:
        self._conn.execute(
            "UPDATE lit_sources SET fetched_at = ? WHERE source_id = ?",
            (utc_now(), source_id),
        )
        self._conn.commit()

    @_locked
    def set_lit_distilled(self, source_id: str, entry_id: str) -> None:
        """Compatibility marker; idempotency lives in ``lit_distillations``."""

        self._conn.execute(
            "UPDATE lit_sources SET distilled_entry_id = COALESCE(distilled_entry_id, ?) "
            "WHERE source_id = ?",
            (entry_id, source_id),
        )
        self._conn.commit()

    def get_lit_distillation(self, source_id: str, focus: str) -> dict[str, Any] | None:
        row = self.get_lit_source(source_id)
        if row is None:
            return None
        result = self._conn.execute(
            "SELECT * FROM lit_distillations WHERE family_id = ? AND focus_key = ?",
            (row.get("family_id"), focus_key(focus)),
        ).fetchone()
        return dict(result) if result else None

    @_locked
    def record_lit_distillation(
        self,
        *,
        source_id: str,
        focus: str,
        research_question: str,
        research_request_sha256: str,
        entry_id: str,
        relevance: str,
    ) -> dict[str, Any]:
        source = self.get_lit_source(source_id)
        if source is None:
            raise ValueError(f"literature source not found: {source_id}")
        normalized = normalize_focus(focus)
        self._conn.execute(
            "INSERT INTO lit_distillations "
            "(source_id, family_id, focus_key, focus, research_question, "
            "research_request_sha256, entry_id, relevance, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                source_id,
                source.get("family_id"),
                focus_key(normalized),
                normalized,
                research_question,
                research_request_sha256,
                entry_id,
                relevance,
                utc_now(),
            ),
        )
        self._conn.execute(
            "UPDATE lit_sources SET distilled_entry_id = COALESCE(distilled_entry_id, ?) "
            "WHERE source_id = ?",
            (entry_id, source_id),
        )
        self._conn.commit()
        return self.get_lit_distillation(source_id, normalized) or {}
