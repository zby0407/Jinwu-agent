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
import hashlib
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_layout import knowledge_export_root

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
  last_seen_at TEXT, publication_date TEXT,
  is_refereed INTEGER DEFAULT 0, is_retracted INTEGER DEFAULT 0,
  content_fingerprint TEXT, first_seen_at TEXT
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
CREATE TABLE IF NOT EXISTS lit_feed_families (
  feed_id TEXT NOT NULL,
  family_id TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  PRIMARY KEY (feed_id, family_id)
);
CREATE TABLE IF NOT EXISTS lit_feed_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  feed_id TEXT NOT NULL,
  query TEXT NOT NULL,
  providers TEXT NOT NULL,
  status TEXT NOT NULL,
  result_count INTEGER NOT NULL DEFAULT 0,
  new_source_count INTEGER NOT NULL DEFAULT 0,
  new_family_count INTEGER NOT NULL DEFAULT 0,
  diagnostics TEXT NOT NULL DEFAULT '{}',
  started_at TEXT NOT NULL,
  completed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lit_delta_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_key TEXT NOT NULL UNIQUE,
  event_type TEXT NOT NULL,
  source_id TEXT,
  family_id TEXT,
  feed_id TEXT,
  prior_source_version TEXT,
  source_version TEXT,
  prior_fingerprint TEXT,
  source_fingerprint TEXT,
  payload TEXT NOT NULL DEFAULT '{}',
  detected_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lit_task_bundles (
  bundle_id TEXT PRIMARY KEY,
  binding_id TEXT NOT NULL,
  run_id TEXT,
  research_question TEXT NOT NULL,
  focus TEXT NOT NULL,
  source_snapshots TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'frozen',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lit_entry_impacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id TEXT NOT NULL,
  family_id TEXT NOT NULL,
  entry_id TEXT NOT NULL,
  relation TEXT NOT NULL,
  affected_fields TEXT NOT NULL DEFAULT '[]',
  scope TEXT NOT NULL DEFAULT '{}',
  quote TEXT NOT NULL,
  location TEXT,
  rationale TEXT NOT NULL,
  confidence TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'proposed',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(family_id, entry_id, relation, quote),
  FOREIGN KEY (source_id) REFERENCES lit_sources(source_id),
  FOREIGN KEY (entry_id) REFERENCES entries(id)
);
CREATE TABLE IF NOT EXISTS wiki_candidate_patches (
  patch_id TEXT PRIMARY KEY,
  target_entry_id TEXT NOT NULL,
  base_version INTEGER NOT NULL,
  source_id TEXT NOT NULL,
  family_id TEXT NOT NULL,
  impact_id INTEGER NOT NULL,
  relation TEXT NOT NULL,
  patch TEXT NOT NULL,
  patch_sha256 TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  review_queue_id INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(target_entry_id, family_id, patch_sha256),
  FOREIGN KEY (target_entry_id) REFERENCES entries(id),
  FOREIGN KEY (source_id) REFERENCES lit_sources(source_id),
  FOREIGN KEY (impact_id) REFERENCES lit_entry_impacts(id)
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


def literature_content_fingerprint(record: dict[str, Any]) -> str:
    """Hash only source content/identity fields, excluding observation times."""

    authors = record.get("authors") or []
    if isinstance(authors, str):
        try:
            parsed = json.loads(authors)
            authors = parsed if isinstance(parsed, list) else [authors]
        except json.JSONDecodeError:
            authors = [authors]
    payload = {
        "title": " ".join(str(record.get("title") or "").split()),
        "authors": [" ".join(str(author).split()) for author in authors],
        "year": record.get("year"),
        "publication_date": str(record.get("publication_date") or ""),
        "doi": normalize_doi(record.get("doi")),
        "url": str(record.get("url") or ""),
        "abstract": " ".join(str(record.get("abstract") or "").split()),
        "provider": str(record.get("provider") or ""),
        "source_version": str(record.get("source_version") or ""),
        "is_refereed": bool(record.get("is_refereed")),
        "is_retracted": bool(record.get("is_retracted")),
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def default_db_path() -> Path:
    """``~/.jw/knowledge.db`` (env-overridable like paths.py)."""

    override = os.getenv("JW_DATA_DIR")
    base = Path(override).expanduser() if override else Path.home() / ".jw"
    return base / "knowledge.db"


def default_export_dir() -> Path:
    """Return the live Markdown export directory under the active workspace.

    ``JW_KB_EXPORT_DIR`` overrides the location (used by tests to
    keep the real export tree clean).
    """

    override = os.getenv("JW_KB_EXPORT_DIR")
    if override:
        return Path(override).expanduser()
    return knowledge_export_root()


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
            "publication_date": "TEXT",
            "is_refereed": "INTEGER DEFAULT 0",
            "is_retracted": "INTEGER DEFAULT 0",
            "content_fingerprint": "TEXT",
            "first_seen_at": "TEXT",
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
            CREATE TABLE IF NOT EXISTS lit_feed_families (
              feed_id TEXT NOT NULL,
              family_id TEXT NOT NULL,
              first_seen_at TEXT NOT NULL,
              last_seen_at TEXT NOT NULL,
              PRIMARY KEY (feed_id, family_id)
            );
            CREATE INDEX IF NOT EXISTS idx_lit_feed_families_family
              ON lit_feed_families(family_id);
            CREATE TABLE IF NOT EXISTS lit_feed_runs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              feed_id TEXT NOT NULL,
              query TEXT NOT NULL,
              providers TEXT NOT NULL,
              status TEXT NOT NULL,
              result_count INTEGER NOT NULL DEFAULT 0,
              new_source_count INTEGER NOT NULL DEFAULT 0,
              new_family_count INTEGER NOT NULL DEFAULT 0,
              diagnostics TEXT NOT NULL DEFAULT '{}',
              started_at TEXT NOT NULL,
              completed_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_lit_feed_runs_feed
              ON lit_feed_runs(feed_id, id DESC);
            CREATE TABLE IF NOT EXISTS lit_delta_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              event_key TEXT NOT NULL UNIQUE,
              event_type TEXT NOT NULL,
              source_id TEXT,
              family_id TEXT,
              feed_id TEXT,
              prior_source_version TEXT,
              source_version TEXT,
              prior_fingerprint TEXT,
              source_fingerprint TEXT,
              payload TEXT NOT NULL DEFAULT '{}',
              detected_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_lit_delta_events_detected
              ON lit_delta_events(id DESC);
            CREATE INDEX IF NOT EXISTS idx_lit_delta_events_source
              ON lit_delta_events(source_id, id DESC);
            CREATE INDEX IF NOT EXISTS idx_lit_delta_events_feed
              ON lit_delta_events(feed_id, id DESC);
            CREATE TABLE IF NOT EXISTS lit_task_bundles (
              bundle_id TEXT PRIMARY KEY,
              binding_id TEXT NOT NULL,
              run_id TEXT,
              research_question TEXT NOT NULL,
              focus TEXT NOT NULL,
              source_snapshots TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'frozen',
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_lit_task_bundles_binding
              ON lit_task_bundles(binding_id, created_at DESC);
            CREATE TABLE IF NOT EXISTS lit_entry_impacts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              source_id TEXT NOT NULL,
              family_id TEXT NOT NULL,
              entry_id TEXT NOT NULL,
              relation TEXT NOT NULL,
              affected_fields TEXT NOT NULL DEFAULT '[]',
              scope TEXT NOT NULL DEFAULT '{}',
              quote TEXT NOT NULL,
              location TEXT,
              rationale TEXT NOT NULL,
              confidence TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'proposed',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE(family_id, entry_id, relation, quote),
              FOREIGN KEY (source_id) REFERENCES lit_sources(source_id),
              FOREIGN KEY (entry_id) REFERENCES entries(id)
            );
            CREATE INDEX IF NOT EXISTS idx_lit_entry_impacts_entry
              ON lit_entry_impacts(entry_id, id DESC);
            CREATE INDEX IF NOT EXISTS idx_lit_entry_impacts_source
              ON lit_entry_impacts(source_id, id DESC);
            CREATE TABLE IF NOT EXISTS wiki_candidate_patches (
              patch_id TEXT PRIMARY KEY,
              target_entry_id TEXT NOT NULL,
              base_version INTEGER NOT NULL,
              source_id TEXT NOT NULL,
              family_id TEXT NOT NULL,
              impact_id INTEGER NOT NULL,
              relation TEXT NOT NULL,
              patch TEXT NOT NULL,
              patch_sha256 TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending',
              review_queue_id INTEGER,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE(target_entry_id, family_id, patch_sha256),
              FOREIGN KEY (target_entry_id) REFERENCES entries(id),
              FOREIGN KEY (source_id) REFERENCES lit_sources(source_id),
              FOREIGN KEY (impact_id) REFERENCES lit_entry_impacts(id)
            );
            CREATE INDEX IF NOT EXISTS idx_wiki_candidate_patches_status
              ON wiki_candidate_patches(status, created_at DESC);
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
            fingerprint = str(row.get("content_fingerprint") or "")
            if not fingerprint:
                fingerprint = literature_content_fingerprint(
                    {
                        **row,
                        "provider": row.get("provider")
                        or infer_provider(row.get("source_id")),
                    }
                )
            self._conn.execute(
                "UPDATE lit_sources SET family_id = ?, provider = ?, "
                "normalized_doi = ?, title_key = ?, first_author_key = ?, "
                "last_seen_at = COALESCE(last_seen_at, fetched_at, ?), "
                "first_seen_at = COALESCE(first_seen_at, fetched_at, last_seen_at, ?), "
                "content_fingerprint = ? "
                "WHERE source_id = ?",
                (
                    family_id,
                    row.get("provider") or infer_provider(row.get("source_id")),
                    doi_key,
                    title_key,
                    author_key,
                    utc_now(),
                    utc_now(),
                    fingerprint,
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
        "publication_date",
        "is_refereed",
        "is_retracted",
        "content_fingerprint",
        "first_seen_at",
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

    def list_preferred_lit_sources(
        self, *, feed_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Return deduplicated preferred cached sources, optionally by feed."""

        params: list[Any] = []
        if feed_ids:
            normalized = sorted({str(feed_id) for feed_id in feed_ids if feed_id})
            placeholders = ", ".join("?" for _ in normalized)
            sql = (
                "SELECT DISTINCT ls.* FROM lit_sources ls "
                "JOIN lit_feed_families lff ON lff.family_id = ls.family_id "
                f"WHERE ls.is_preferred = 1 AND lff.feed_id IN ({placeholders})"
            )
            params.extend(normalized)
        else:
            sql = "SELECT * FROM lit_sources WHERE is_preferred = 1"
        sql += (
            " ORDER BY COALESCE(publication_date, '') DESC, "
            "COALESCE(year, 0) DESC, source_id"
        )
        results: list[dict[str, Any]] = []
        for row in self._conn.execute(sql, params).fetchall():
            record = dict(row)
            raw_authors = record.get("authors")
            if isinstance(raw_authors, str) and raw_authors:
                try:
                    record["authors"] = json.loads(raw_authors)
                except json.JSONDecodeError:
                    record["authors"] = [raw_authors]
            elif raw_authors is None:
                record["authors"] = []
            results.append(record)
        return results

    def _record_lit_delta_event(
        self,
        *,
        event_type: str,
        source_id: str = "",
        family_id: str = "",
        feed_id: str = "",
        prior_source_version: str = "",
        source_version: str = "",
        prior_fingerprint: str = "",
        source_fingerprint: str = "",
        payload: dict[str, Any] | None = None,
        detected_at: str = "",
    ) -> int | None:
        """Insert one immutable, content-addressed literature delta event."""

        event_identity = {
            "event_type": event_type,
            "source_id": source_id,
            "family_id": family_id,
            "feed_id": feed_id,
            "prior_source_version": prior_source_version,
            "source_version": source_version,
            "prior_fingerprint": prior_fingerprint,
            "source_fingerprint": source_fingerprint,
        }
        event_key = hashlib.sha256(
            json.dumps(
                event_identity,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        cursor = self._conn.execute(
            "INSERT OR IGNORE INTO lit_delta_events "
            "(event_key, event_type, source_id, family_id, feed_id, "
            "prior_source_version, source_version, prior_fingerprint, "
            "source_fingerprint, payload, detected_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_key,
                event_type,
                source_id,
                family_id,
                feed_id,
                prior_source_version,
                source_version,
                prior_fingerprint,
                source_fingerprint,
                json.dumps(payload or {}, ensure_ascii=False),
                detected_at or utc_now(),
            ),
        )
        return int(cursor.lastrowid) if cursor.rowcount else None

    @_locked
    def seed_literature_baseline(self) -> dict[str, int]:
        """Record pre-existing sources once without calling them newly discovered."""

        inserted = 0
        existing = 0
        for source in self.list_preferred_lit_sources():
            event_id = self._record_lit_delta_event(
                event_type="baseline_source",
                source_id=str(source.get("source_id") or ""),
                family_id=str(source.get("family_id") or ""),
                source_version=str(source.get("source_version") or ""),
                source_fingerprint=str(source.get("content_fingerprint") or ""),
                payload={
                    "title": str(source.get("title") or ""),
                    "provider": str(source.get("provider") or ""),
                },
                detected_at=str(source.get("first_seen_at") or utc_now()),
            )
            if event_id is None:
                existing += 1
            else:
                inserted += 1
        self._conn.commit()
        return {"inserted": inserted, "existing": existing}

    def list_lit_delta_events(
        self,
        *,
        event_type: str = "",
        feed_id: str = "",
        source_id: str = "",
        include_baseline: bool = True,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM lit_delta_events WHERE 1 = 1"
        params: list[Any] = []
        if event_type:
            sql += " AND event_type = ?"
            params.append(event_type)
        if feed_id:
            sql += (
                " AND (feed_id = ? OR EXISTS ("
                "SELECT 1 FROM lit_feed_families lff "
                "WHERE lff.feed_id = ? "
                "AND lff.family_id = lit_delta_events.family_id))"
            )
            params.extend((feed_id, feed_id))
        if source_id:
            sql += " AND source_id = ?"
            params.append(source_id)
        if not include_baseline:
            sql += " AND event_type <> 'baseline_source'"
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))
        events: list[dict[str, Any]] = []
        for row in self._conn.execute(sql, params).fetchall():
            event = dict(row)
            event["payload"] = json.loads(event.get("payload") or "{}")
            events.append(event)
        return events

    def count_lit_delta_events(self, *, include_baseline: bool = False) -> int:
        sql = "SELECT COUNT(*) AS count FROM lit_delta_events"
        if not include_baseline:
            sql += " WHERE event_type <> 'baseline_source'"
        row = self._conn.execute(sql).fetchone()
        return int(row["count"] if row else 0)

    def _queue_literature_retraction_reviews(self, source_id: str) -> None:
        impacts = self._conn.execute(
            "SELECT * FROM lit_entry_impacts WHERE source_id = ? "
            "AND status <> 'rejected'",
            (source_id,),
        ).fetchall()
        for impact in impacts:
            entry_id = str(impact["entry_id"])
            already_pending = self._conn.execute(
                "SELECT 1 FROM review_queue WHERE kind = 'literature_retraction' "
                "AND entry_id = ? AND status = 'pending' "
                "AND payload LIKE ? LIMIT 1",
                (entry_id, f'%"source_id": "{source_id}"%'),
            ).fetchone()
            if already_pending:
                continue
            self._conn.execute(
                "UPDATE lit_entry_impacts SET status = 'needs_revalidation', "
                "updated_at = ? WHERE id = ?",
                (utc_now(), int(impact["id"])),
            )
            payload = {
                "source_id": source_id,
                "impact_id": int(impact["id"]),
                "reason": "linked literature source is retracted",
            }
            self._conn.execute(
                "INSERT INTO review_queue "
                "(kind, entry_id, payload, status, reviewer, decided_at, note) "
                "VALUES ('literature_retraction', ?, ?, 'pending', '', NULL, '')",
                (entry_id, json.dumps(payload, ensure_ascii=False)),
            )

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
            "ORDER BY COALESCE(is_retracted, 0) ASC, "
            "COALESCE(is_refereed, 0) DESC, COALESCE(year, 0) DESC, "
            "CASE WHEN normalized_doi <> '' THEN 1 ELSE 0 END DESC, "
            "CASE provider WHEN 'ads' THEN 3 WHEN 'openalex' THEN 2 "
            "WHEN 'crossref' THEN 2 WHEN 'arxiv' THEN 1 ELSE 0 END DESC, "
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
        source_id = str(normalized.get("source_id") or "").strip()
        existing = self.get_lit_source(source_id) if source_id else None
        observed_at = utc_now()
        normalized["family_id"] = self._family_for_record(normalized)
        normalized["provider"] = normalized.get("provider") or infer_provider(
            normalized.get("source_id")
        )
        normalized["normalized_doi"] = normalize_doi(normalized.get("doi"))
        normalized["title_key"] = normalize_text_key(normalized.get("title"))
        normalized["first_author_key"] = first_author_key(normalized.get("authors"))
        normalized["last_seen_at"] = observed_at
        normalized["first_seen_at"] = (
            str(existing.get("first_seen_at") or observed_at)
            if existing
            else observed_at
        )
        normalized.setdefault("is_preferred", 1)
        normalized["is_refereed"] = int(bool(normalized.get("is_refereed", False)))
        normalized["is_retracted"] = int(bool(normalized.get("is_retracted", False)))
        normalized["content_fingerprint"] = literature_content_fingerprint(normalized)
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
            "publication_date = excluded.publication_date, "
            "is_refereed = excluded.is_refereed, "
            "is_retracted = excluded.is_retracted, "
            "content_fingerprint = excluded.content_fingerprint, "
            "first_seen_at = COALESCE(lit_sources.first_seen_at, excluded.first_seen_at), "
            "last_seen_at = excluded.last_seen_at, "
            "fetched_at = COALESCE(excluded.fetched_at, lit_sources.fetched_at), "
            "distilled_entry_id = COALESCE("
            "excluded.distilled_entry_id, lit_sources.distilled_entry_id)",
            tuple(values[key] for key in self._LIT_COLUMNS),
        )
        self._refresh_preferred_source(str(normalized["family_id"]))
        event_types: list[str] = []
        prior_fingerprint = (
            str(existing.get("content_fingerprint") or "") if existing else ""
        )
        source_fingerprint = str(normalized["content_fingerprint"])
        prior_version = str(existing.get("source_version") or "") if existing else ""
        source_version = str(normalized.get("source_version") or "")
        if existing is None:
            event_types.append("new_source")
        else:
            if prior_version != source_version:
                event_types.append("new_version")
            if not bool(existing.get("is_retracted")) and bool(
                normalized.get("is_retracted")
            ):
                event_types.append("source_retracted")
            if prior_fingerprint != source_fingerprint and not event_types:
                event_types.append("metadata_updated")
        for event_type in event_types:
            self._record_lit_delta_event(
                event_type=event_type,
                source_id=source_id,
                family_id=str(normalized["family_id"]),
                prior_source_version=prior_version,
                source_version=source_version,
                prior_fingerprint=prior_fingerprint,
                source_fingerprint=source_fingerprint,
                payload={
                    "title": str(normalized.get("title") or ""),
                    "provider": str(normalized.get("provider") or ""),
                    "is_retracted": bool(normalized.get("is_retracted")),
                },
                detected_at=observed_at,
            )
        if "source_retracted" in event_types:
            self._queue_literature_retraction_reviews(source_id)
        self._conn.commit()
        result = self.get_lit_source(source_id) or {}
        result["delta_types"] = event_types
        return result

    @_locked
    def touch_lit_feed_family(self, feed_id: str, family_id: str) -> bool:
        """Associate a discovered family with a feed; return True on first sight."""

        existing = self._conn.execute(
            "SELECT 1 FROM lit_feed_families WHERE feed_id = ? AND family_id = ?",
            (feed_id, family_id),
        ).fetchone()
        now = utc_now()
        self._conn.execute(
            "INSERT INTO lit_feed_families "
            "(feed_id, family_id, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(feed_id, family_id) DO UPDATE SET "
            "last_seen_at = excluded.last_seen_at",
            (feed_id, family_id, now, now),
        )
        if existing is None:
            preferred = self._conn.execute(
                "SELECT source_id, source_version, content_fingerprint "
                "FROM lit_sources WHERE family_id = ? AND is_preferred = 1 LIMIT 1",
                (family_id,),
            ).fetchone()
            self._record_lit_delta_event(
                event_type="feed_discovered",
                source_id=str(preferred["source_id"]) if preferred else "",
                family_id=family_id,
                feed_id=feed_id,
                source_version=(
                    str(preferred["source_version"] or "") if preferred else ""
                ),
                source_fingerprint=(
                    str(preferred["content_fingerprint"] or "") if preferred else ""
                ),
                payload={},
                detected_at=now,
            )
        self._conn.commit()
        return existing is None

    def list_lit_feed_sources(self, feed_id: str) -> list[dict[str, Any]]:
        """Return the preferred source row for every family mapped to a feed."""

        rows = self._conn.execute(
            "SELECT ls.* FROM lit_feed_families lff "
            "JOIN lit_sources ls ON ls.family_id = lff.family_id "
            "WHERE lff.feed_id = ? AND ls.is_preferred = 1 "
            "ORDER BY COALESCE(ls.publication_date, '') DESC, "
            "COALESCE(ls.year, 0) DESC, ls.source_id",
            (feed_id,),
        ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            record = dict(row)
            raw_authors = record.get("authors")
            if isinstance(raw_authors, str) and raw_authors:
                record["authors"] = json.loads(raw_authors)
            elif raw_authors is None:
                record["authors"] = []
            results.append(record)
        return results

    def count_lit_feed_sources(self, feed_id: str) -> int:
        """Count display-distinct preferred sources in one feed."""

        row = self._conn.execute(
            "SELECT COUNT(DISTINCT COALESCE(NULLIF(ls.title_key, ''), ls.source_id)) "
            "AS count FROM lit_feed_families lff "
            "JOIN lit_sources ls ON ls.family_id = lff.family_id "
            "WHERE lff.feed_id = ? AND ls.is_preferred = 1",
            (feed_id,),
        ).fetchone()
        return int(row["count"] if row else 0)

    @_locked
    def remove_lit_feed_family(self, feed_id: str, family_id: str) -> bool:
        """Remove only a feed association, leaving cached sources untouched."""

        preferred = self._conn.execute(
            "SELECT source_id, source_version, content_fingerprint "
            "FROM lit_sources WHERE family_id = ? AND is_preferred = 1 LIMIT 1",
            (family_id,),
        ).fetchone()
        cursor = self._conn.execute(
            "DELETE FROM lit_feed_families WHERE feed_id = ? AND family_id = ?",
            (feed_id, family_id),
        )
        if cursor.rowcount:
            self._record_lit_delta_event(
                event_type="feed_removed",
                source_id=str(preferred["source_id"]) if preferred else "",
                family_id=family_id,
                feed_id=feed_id,
                source_version=(
                    str(preferred["source_version"] or "") if preferred else ""
                ),
                source_fingerprint=(
                    str(preferred["content_fingerprint"] or "") if preferred else ""
                ),
                payload={},
            )
        self._conn.commit()
        return bool(cursor.rowcount)

    @_locked
    def record_lit_feed_run(
        self,
        *,
        feed_id: str,
        query: str,
        providers: list[str],
        status: str,
        result_count: int,
        new_source_count: int,
        new_family_count: int,
        diagnostics: dict[str, Any],
        started_at: str,
    ) -> dict[str, Any]:
        """Persist an immutable feed-sync receipt."""

        completed_at = utc_now()
        cursor = self._conn.execute(
            "INSERT INTO lit_feed_runs "
            "(feed_id, query, providers, status, result_count, new_source_count, "
            "new_family_count, diagnostics, started_at, completed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                feed_id,
                query,
                json.dumps(providers, ensure_ascii=False),
                status,
                int(result_count),
                int(new_source_count),
                int(new_family_count),
                json.dumps(diagnostics, ensure_ascii=False),
                started_at,
                completed_at,
            ),
        )
        self._conn.commit()
        return self.get_lit_feed_run(int(cursor.lastrowid)) or {}

    def get_lit_feed_run(self, run_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM lit_feed_runs WHERE id = ?", (int(run_id),)
        ).fetchone()
        return self._decode_lit_feed_run(row)

    def latest_lit_feed_run(self, feed_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM lit_feed_runs WHERE feed_id = ? ORDER BY id DESC LIMIT 1",
            (feed_id,),
        ).fetchone()
        return self._decode_lit_feed_run(row)

    @staticmethod
    def _decode_lit_feed_run(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        result["providers"] = json.loads(result.get("providers") or "[]")
        result["diagnostics"] = json.loads(result.get("diagnostics") or "{}")
        return result

    @_locked
    def create_lit_task_bundle(
        self,
        *,
        bundle_id: str,
        binding_id: str,
        run_id: str,
        research_question: str,
        focus: str,
        source_snapshots: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Freeze a bounded task-specific snapshot of cached literature."""

        self._conn.execute(
            "INSERT OR IGNORE INTO lit_task_bundles "
            "(bundle_id, binding_id, run_id, research_question, focus, "
            "source_snapshots, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'frozen', ?)",
            (
                bundle_id,
                binding_id,
                run_id,
                research_question,
                focus,
                json.dumps(source_snapshots, ensure_ascii=False),
                utc_now(),
            ),
        )
        self._conn.commit()
        return self.get_lit_task_bundle(bundle_id) or {}

    def get_lit_task_bundle(self, bundle_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM lit_task_bundles WHERE bundle_id = ?", (bundle_id,)
        ).fetchone()
        if row is None:
            return None
        bundle = dict(row)
        bundle["source_snapshots"] = json.loads(bundle.get("source_snapshots") or "[]")
        return bundle

    def list_lit_task_bundles(self, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM lit_task_bundles ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit), 500)),),
        ).fetchall()
        bundles: list[dict[str, Any]] = []
        for row in rows:
            bundle = dict(row)
            snapshots = json.loads(bundle.get("source_snapshots") or "[]")
            bundle["source_count"] = len(snapshots)
            bundle["source_snapshots"] = snapshots
            bundles.append(bundle)
        return bundles

    @_locked
    def record_lit_entry_impact(
        self,
        *,
        source_id: str,
        family_id: str,
        entry_id: str,
        relation: str,
        affected_fields: list[str],
        scope: dict[str, Any],
        quote: str,
        location: str,
        rationale: str,
        confidence: str,
    ) -> dict[str, Any]:
        now = utc_now()
        self._conn.execute(
            "INSERT INTO lit_entry_impacts "
            "(source_id, family_id, entry_id, relation, affected_fields, scope, "
            "quote, location, rationale, confidence, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?) "
            "ON CONFLICT(family_id, entry_id, relation, quote) DO UPDATE SET "
            "source_id = excluded.source_id, affected_fields = excluded.affected_fields, "
            "scope = excluded.scope, location = excluded.location, "
            "rationale = excluded.rationale, confidence = excluded.confidence, "
            "updated_at = excluded.updated_at",
            (
                source_id,
                family_id,
                entry_id,
                relation,
                json.dumps(affected_fields, ensure_ascii=False),
                json.dumps(scope, ensure_ascii=False),
                quote,
                location,
                rationale,
                confidence,
                now,
                now,
            ),
        )
        row = self._conn.execute(
            "SELECT * FROM lit_entry_impacts WHERE family_id = ? AND entry_id = ? "
            "AND relation = ? AND quote = ?",
            (family_id, entry_id, relation, quote),
        ).fetchone()
        self._conn.commit()
        return self._decode_lit_entry_impact(row) or {}

    def get_lit_entry_impact(self, impact_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM lit_entry_impacts WHERE id = ?", (int(impact_id),)
        ).fetchone()
        return self._decode_lit_entry_impact(row)

    def list_lit_entry_impacts(
        self,
        *,
        entry_id: str = "",
        source_id: str = "",
        status: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM lit_entry_impacts WHERE 1 = 1"
        params: list[Any] = []
        if entry_id:
            sql += " AND entry_id = ?"
            params.append(entry_id)
        if source_id:
            sql += " AND source_id = ?"
            params.append(source_id)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))
        return [
            self._decode_lit_entry_impact(row) or {}
            for row in self._conn.execute(sql, params).fetchall()
        ]

    @staticmethod
    def _decode_lit_entry_impact(
        row: sqlite3.Row | None,
    ) -> dict[str, Any] | None:
        if row is None:
            return None
        impact = dict(row)
        impact["affected_fields"] = json.loads(impact.get("affected_fields") or "[]")
        impact["scope"] = json.loads(impact.get("scope") or "{}")
        return impact

    @_locked
    def create_wiki_candidate_patch(
        self,
        *,
        patch_id: str,
        target_entry_id: str,
        base_version: int,
        source_id: str,
        family_id: str,
        impact_id: int,
        relation: str,
        patch: dict[str, Any],
        patch_sha256: str,
    ) -> dict[str, Any]:
        now = utc_now()
        self._conn.execute(
            "INSERT OR IGNORE INTO wiki_candidate_patches "
            "(patch_id, target_entry_id, base_version, source_id, family_id, "
            "impact_id, relation, patch, patch_sha256, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
            (
                patch_id,
                target_entry_id,
                int(base_version),
                source_id,
                family_id,
                int(impact_id),
                relation,
                json.dumps(patch, ensure_ascii=False),
                patch_sha256,
                now,
                now,
            ),
        )
        patch_row = self.get_wiki_candidate_patch(patch_id)
        if patch_row and patch_row.get("review_queue_id") is None:
            cursor = self._conn.execute(
                "INSERT INTO review_queue "
                "(kind, entry_id, payload, status, reviewer, decided_at, note) "
                "VALUES ('wiki_patch', ?, ?, 'pending', '', NULL, '')",
                (
                    target_entry_id,
                    json.dumps(
                        {
                            "patch_id": patch_id,
                            "impact_id": int(impact_id),
                            "base_version": int(base_version),
                            "relation": relation,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            self._conn.execute(
                "UPDATE wiki_candidate_patches SET review_queue_id = ? "
                "WHERE patch_id = ?",
                (int(cursor.lastrowid), patch_id),
            )
        self._conn.commit()
        return self.get_wiki_candidate_patch(patch_id) or {}

    def get_wiki_candidate_patch(self, patch_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM wiki_candidate_patches WHERE patch_id = ?", (patch_id,)
        ).fetchone()
        return self._decode_wiki_candidate_patch(row)

    def list_wiki_candidate_patches(
        self,
        *,
        status: str = "",
        target_entry_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM wiki_candidate_patches WHERE 1 = 1"
        params: list[Any] = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if target_entry_id:
            sql += " AND target_entry_id = ?"
            params.append(target_entry_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))
        return [
            self._decode_wiki_candidate_patch(row) or {}
            for row in self._conn.execute(sql, params).fetchall()
        ]

    @staticmethod
    def _decode_wiki_candidate_patch(
        row: sqlite3.Row | None,
    ) -> dict[str, Any] | None:
        if row is None:
            return None
        patch = dict(row)
        patch["patch"] = json.loads(patch.get("patch") or "{}")
        return patch

    @_locked
    def update_wiki_candidate_patch_status(self, patch_id: str, *, status: str) -> None:
        self._conn.execute(
            "UPDATE wiki_candidate_patches SET status = ?, updated_at = ? "
            "WHERE patch_id = ?",
            (status, utc_now(), patch_id),
        )
        self._conn.commit()

    @_locked
    def update_lit_entry_impact_status(self, impact_id: int, *, status: str) -> None:
        self._conn.execute(
            "UPDATE lit_entry_impacts SET status = ?, updated_at = ? WHERE id = ?",
            (status, utc_now(), int(impact_id)),
        )
        self._conn.commit()

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
