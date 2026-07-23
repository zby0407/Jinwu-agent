"""Business logic for the knowledge base (plan §5.2).

Implements the hard rules R1-R5: candidates first (R2), automatic
provenance logging on read (R4), the promotion gate with two defensible
auto-approval rules (cross-run reproduction or explicit expert review),
and explicit conflict surfacing (R5). A DOI proves identity, not scientific
support, so literature entries without review remain candidates.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .contracts import (
    REVIEW_DECISIONS,
    ContractError,
    check_status_transition,
    normalize_content,
    parse_entry_id,
    validate_entry,
)
from .export import export_entry, import_entry_file
from .store import KnowledgeStore, utc_now

_AUTO_RULES = ("cross_run_reproduction", "expert_review")

_SLUG_STRIP = re.compile(r"[^a-z0-9_-]+")


def _slugify(title: str) -> str:
    """ASCII slug for entry ids; non-ASCII titles fall back to 'entry'."""

    slug = _SLUG_STRIP.sub("-", (title or "").lower()).strip("-_")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:48].strip("-") or "entry"


def _new_entry_id(store: KnowledgeStore, entry_type: str, title: str) -> str:
    prefix = f"kb_{entry_type}_{_slugify(title)}_"
    return f"{prefix}{store.next_seq(prefix):03d}"


def _export(store: KnowledgeStore, entry: dict[str, Any]) -> str:
    return str(export_entry(entry, store.export_dir))


# ----------------------------------------------------------------------
# search / read
# ----------------------------------------------------------------------
def search(
    store: KnowledgeStore,
    query: str = "",
    *,
    entry_type: str = "",
    status: str = "",
    confidence: str = "",
    valid_range: str = "",
    limit: int = 8,
) -> dict[str, Any]:
    """FTS5 + structured-filter search; deprecated hidden unless requested."""

    limit = max(1, min(int(limit), 50))
    results = store.search(
        query,
        entry_type=entry_type,
        status=status,
        confidence=confidence,
        valid_range=valid_range,
        limit=limit,
    )
    return {"status": "ok", "query": query, "count": len(results), "results": results}


def read(
    store: KnowledgeStore,
    entry_id: str,
    *,
    agent: str = "",
    run_id: str = "",
    purpose: str = "",
) -> dict[str, Any]:
    """Read one entry; every read is written to provenance_log (R4)."""

    entry = store.get_entry(entry_id)
    if entry is None:
        raise ContractError(
            f"entry not found: {entry_id!r}",
            error_code="entry_not_found",
            field_path="entry_id",
            suggestion="先用 kb_search 检索有效条目 id。",
        )
    if entry.get("provenance", {}).get("grounding_blocked") and purpose not in {
        "audit",
        "review",
        "revalidate",
    }:
        raise ContractError(
            f"knowledge entry requires literature revalidation: {entry_id!r}",
            error_code="knowledge_entry_grounding_blocked",
            field_path="entry_id",
            suggestion="该旧蒸馏尚未通过问题/focus/来源相关性复核；仅可用于 audit/review。",
        )
    store.log_provenance(run_id=run_id, agent=agent, entry_id=entry_id, purpose=purpose)
    return {"status": "ok", "entry": entry}


# ----------------------------------------------------------------------
# propose / conflict detection (R2, R5)
# ----------------------------------------------------------------------
def propose(
    store: KnowledgeStore,
    *,
    entry_type: str,
    title: str,
    content: dict[str, Any],
    source_type: str,
    source_ref: str,
    confidence: str,
    valid_range: str = "",
    related_ids: list[str] | None = None,
    agent: str = "",
    run_id: str = "",
    created_by: str = "",
    provenance_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Insert a new entry. Always ``status=candidate`` — no backdoors (R2)."""

    related_ids = [str(item) for item in (related_ids or [])]
    now = utc_now()
    provenance: dict[str, Any] = dict(provenance_extra or {})
    if run_id:
        provenance["run_id"] = run_id
    if agent:
        provenance["agent"] = agent
    entry = {
        "id": _new_entry_id(store, entry_type, title),
        "type": entry_type,
        "title": title,
        "content": normalize_content(entry_type, content),
        "source_type": source_type,
        "source_ref": source_ref,
        "confidence": confidence,
        "status": "candidate",
        "valid_range": valid_range,
        "related_ids": related_ids,
        "provenance": provenance,
        "version": 1,
        "created_at": now,
        "updated_at": now,
        "created_by": created_by or agent or "unknown",
    }
    entry = validate_entry(entry)
    store.create_entry(entry, changed_by=entry["created_by"], reason="propose")
    entry["export_path"] = _export(store, entry)

    conflicts = _detect_conflicts(store, entry)
    result: dict[str, Any] = {"status": "ok", "entry": entry}
    if conflicts:
        result["conflicts"] = conflicts
        result["warning"] = (
            "candidate conflicts with existing canonical entries; "
            "queued for review (R5), not merged"
        )
    return result


def _detect_conflicts(
    store: KnowledgeStore, entry: dict[str, Any]
) -> list[dict[str, Any]]:
    """Simplified conflict detection: a counterexample that names a canonical
    entry in related_ids is surfaced as a pending conflict, never merged."""

    if entry["type"] != "counterexample":
        return []
    conflicts: list[dict[str, Any]] = []
    for related_id in entry.get("related_ids", []):
        target = store.get_entry(related_id)
        if target is None or target.get("status") != "canonical":
            continue
        queue_id = store.add_review_item(
            kind="conflict",
            entry_id=entry["id"],
            payload={
                "candidate_id": entry["id"],
                "candidate_title": entry["title"],
                "canonical_id": target["id"],
                "canonical_title": target["title"],
                "reason": (
                    f"counterexample {entry['id']} relates to canonical "
                    f"{target['id']}; human adjudication required"
                ),
            },
            status="pending",
        )
        conflicts.append(
            {"queue_id": queue_id, "canonical_id": target["id"], "status": "pending"}
        )
    return conflicts


# ----------------------------------------------------------------------
# promote (审核门, §4.9.6)
# ----------------------------------------------------------------------
def _supporting_run_ids(store: KnowledgeStore, entry: dict[str, Any]) -> list[str]:
    run_ids = set(store.distinct_run_ids(entry["id"]))
    content_run = entry.get("content", {}).get("run_id")
    if isinstance(content_run, str) and content_run.strip():
        run_ids.add(content_run.strip())
    provenance_run = entry.get("provenance", {}).get("run_id")
    if isinstance(provenance_run, str) and provenance_run.strip():
        run_ids.add(provenance_run.strip())
    return sorted(run_ids)


def _auto_rule(
    store: KnowledgeStore, entry: dict[str, Any], reviewer: str
) -> tuple[str | None, list[str]]:
    """First satisfied §4.9.6 auto rule, plus the supporting run_id list."""

    run_ids = _supporting_run_ids(store, entry)
    if reviewer.strip():
        return "expert_review", run_ids
    if len(run_ids) >= 2:
        return "cross_run_reproduction", run_ids
    return None, run_ids


def promote(
    store: KnowledgeStore,
    entry_id: str,
    *,
    reason: str,
    reviewer: str = "",
) -> dict[str, Any]:
    """Run the promotion gate for a candidate.

    If any §4.9.6 auto rule holds, the entry becomes canonical immediately
    and the queue records ``auto_approved`` (with ``human_reviewed`` marked
    in provenance). Otherwise a pending review item is queued and the entry
    stays candidate until ``review_decide``.
    """

    entry = store.get_entry(entry_id)
    if entry is None:
        raise ContractError(
            f"entry not found: {entry_id!r}",
            error_code="entry_not_found",
            field_path="entry_id",
            suggestion="先用 kb_search 检索有效条目 id。",
        )
    if entry["status"] != "candidate":
        raise ContractError(
            f"only candidate entries can be promoted (current: {entry['status']})",
            error_code="promote_requires_candidate",
            field_path="entry_id",
            suggestion="晋升门只接受 candidate；deprecated/superseded 条目不可复活。",
        )
    if not reason.strip():
        raise ContractError(
            "promotion reason is required",
            error_code="promote_reason_missing",
            field_path="reason",
            suggestion="说明晋升理由（复现证据 / 文献支撑 / 专家判断依据）。",
        )

    rule, run_ids = _auto_rule(store, entry, reviewer)
    if rule is None:
        queue_id = store.add_review_item(
            kind="promote",
            entry_id=entry_id,
            payload={"reason": reason, "supporting_run_ids": run_ids},
            status="pending",
        )
        return {
            "status": "ok",
            "decision": "pending_review",
            "queue_id": queue_id,
            "entry_id": entry_id,
            "entry_status": "candidate",
            "supporting_run_ids": run_ids,
        }

    check_status_transition(entry["status"], "canonical")
    now = utc_now()
    entry["status"] = "canonical"
    entry["version"] = int(entry["version"]) + 1
    entry["updated_at"] = now
    entry["provenance"] = {
        **entry.get("provenance", {}),
        "promote_reason": reason,
        "auto_rule": rule,
        "supporting_run_ids": run_ids,
        "reviewer": reviewer,
        "human_reviewed": bool(reviewer.strip()),
        "promoted_at": now,
    }
    store.update_entry(
        entry, changed_by=reviewer or entry.get("created_by", ""), reason="promote"
    )
    queue_id = store.add_review_item(
        kind="promote",
        entry_id=entry_id,
        payload={
            "reason": reason,
            "auto_rule": rule,
            "supporting_run_ids": run_ids,
            "human_reviewed": bool(reviewer.strip()),
        },
        status="auto_approved",
        reviewer=reviewer,
    )
    entry["export_path"] = _export(store, entry)
    return {
        "status": "ok",
        "decision": "auto_approved",
        "auto_rule": rule,
        "queue_id": queue_id,
        "entry_id": entry_id,
        "entry_status": "canonical",
        "supporting_run_ids": run_ids,
        "entry": entry,
    }


# ----------------------------------------------------------------------
# deprecate / supersede
# ----------------------------------------------------------------------
def deprecate(
    store: KnowledgeStore,
    entry_id: str,
    *,
    reason: str,
    superseded_by: str = "",
    agent: str = "",
) -> dict[str, Any]:
    """Mark an entry deprecated (or superseded when ``superseded_by`` given).

    Never deletes; a version snapshot is written so the change is
    traceable and rollback-able via entry_versions.
    """

    entry = store.get_entry(entry_id)
    if entry is None:
        raise ContractError(
            f"entry not found: {entry_id!r}",
            error_code="entry_not_found",
            field_path="entry_id",
            suggestion="先用 kb_search 检索有效条目 id。",
        )
    if not reason.strip():
        raise ContractError(
            "deprecation reason is required",
            error_code="deprecate_reason_missing",
            field_path="reason",
            suggestion="说明废弃原因（新证据矛盾 / 数据源校正 / 理论被削弱）。",
        )
    target_status = "superseded" if superseded_by else "deprecated"
    if superseded_by:
        replacement = store.get_entry(superseded_by)
        if replacement is None or parse_entry_id(superseded_by) is None:
            raise ContractError(
                f"superseded_by entry not found: {superseded_by!r}",
                error_code="superseded_by_not_found",
                field_path="superseded_by",
                suggestion="superseded_by 必须指向库中已存在的替代条目 id。",
            )
    check_status_transition(entry["status"], target_status)
    now = utc_now()
    entry["status"] = target_status
    entry["version"] = int(entry["version"]) + 1
    entry["updated_at"] = now
    entry["provenance"] = {
        **entry.get("provenance", {}),
        "deprecate_reason": reason,
        "superseded_by": superseded_by,
        "deprecated_at": now,
        "deprecated_by": agent,
    }
    store.update_entry(
        entry, changed_by=agent or entry.get("created_by", ""), reason="deprecate"
    )
    entry["export_path"] = _export(store, entry)
    return {
        "status": "ok",
        "entry_id": entry_id,
        "entry_status": target_status,
        "superseded_by": superseded_by,
        "version": entry["version"],
        "entry": entry,
    }


# ----------------------------------------------------------------------
# conflicts / review decisions (R5, HITL landing point)
# ----------------------------------------------------------------------
def conflicts(store: KnowledgeStore, entry_id: str = "") -> dict[str, Any]:
    """List pending conflict-review items, optionally for one entry."""

    items = store.pending_review_items(kind="conflict", entry_id=entry_id)
    return {"status": "ok", "count": len(items), "conflicts": items}


def review_queue(
    store: KnowledgeStore, *, kind: str = "", entry_id: str = ""
) -> dict[str, Any]:
    """List pending promotion, conflict, deprecation, or revalidation items."""

    if kind and kind not in {"promote", "conflict", "deprecate", "revalidate"}:
        raise ContractError(
            f"unknown review kind: {kind!r}",
            error_code="unknown_review_kind",
            field_path="kind",
            suggestion="kind 留空或使用 promote/conflict/deprecate/revalidate。",
        )
    items = store.pending_review_items(kind=kind, entry_id=entry_id)
    return {"status": "ok", "count": len(items), "items": items}


def review_decide(
    store: KnowledgeStore,
    queue_id: int,
    *,
    decision: str,
    note: str = "",
    reviewer: str = "human",
) -> dict[str, Any]:
    """Apply a human decision to a pending review-queue item.

    Approved promote items flip the entry to canonical (transition checked
    against the current status); rejected items leave the entry untouched.
    Conflict items only record the resolution. Revalidation approval unblocks
    a legacy entry for grounding; rejection deprecates it with a versioned
    audit trail.
    """

    if decision not in REVIEW_DECISIONS:
        raise ContractError(
            f"unknown review decision: {decision!r}",
            error_code="unknown_review_decision",
            field_path="decision",
            suggestion=f"decision 必须是 {sorted(REVIEW_DECISIONS)} 之一。",
        )
    item = store.get_review_item(int(queue_id))
    if item is None:
        raise ContractError(
            f"review queue item not found: {queue_id}",
            error_code="queue_item_not_found",
            field_path="queue_id",
            suggestion="queue_id 取自 kb_conflicts / kb_promote 返回结果。",
        )
    if item["status"] != "pending":
        raise ContractError(
            f"review queue item {queue_id} is already {item['status']}",
            error_code="queue_item_already_decided",
            field_path="queue_id",
            suggestion="该审核项已有决定；如需变更请人工处理。",
        )
    store.decide_review_item(
        int(queue_id), status=decision, reviewer=reviewer, note=note
    )

    entry_status = ""
    if item["kind"] == "promote" and decision == "approved":
        entry = store.get_entry(item["entry_id"])
        if entry is not None and entry["status"] == "candidate":
            check_status_transition(entry["status"], "canonical")
            now = utc_now()
            entry["status"] = "canonical"
            entry["version"] = int(entry["version"]) + 1
            entry["updated_at"] = now
            entry["provenance"] = {
                **entry.get("provenance", {}),
                "promote_reason": item["payload"].get("reason", ""),
                "reviewer": reviewer,
                "review_note": note,
                "human_reviewed": True,
                "promoted_at": now,
            }
            store.update_entry(entry, changed_by=reviewer, reason="review_approved")
            entry["export_path"] = _export(store, entry)
        entry_status = entry["status"] if entry is not None else "missing"
    elif item["kind"] == "revalidate":
        entry = store.get_entry(item["entry_id"])
        if entry is not None:
            now = utc_now()
            provenance = dict(entry.get("provenance") or {})
            if decision == "approved":
                provenance["grounding_blocked"] = False
                provenance["revalidated_at"] = now
                provenance["revalidated_by"] = reviewer
                provenance["human_reviewed"] = True
                provenance["review_note"] = note
            elif entry["status"] in {"candidate", "canonical"}:
                check_status_transition(entry["status"], "deprecated")
                entry["status"] = "deprecated"
                provenance["deprecated_at"] = now
                provenance["deprecate_reason"] = (
                    note or "legacy literature revalidation rejected"
                )
                provenance["deprecated_by"] = reviewer
            entry["provenance"] = provenance
            entry["version"] = int(entry["version"]) + 1
            entry["updated_at"] = now
            store.update_entry(
                entry, changed_by=reviewer, reason=f"revalidate_{decision}"
            )
            entry["export_path"] = _export(store, entry)
        entry_status = entry["status"] if entry is not None else "missing"
    else:
        entry = store.get_entry(item["entry_id"])
        entry_status = entry["status"] if entry is not None else "missing"

    return {
        "status": "ok",
        "queue_id": int(queue_id),
        "kind": item["kind"],
        "decision": decision,
        "reviewer": reviewer,
        "entry_id": item["entry_id"],
        "entry_status": entry_status,
    }


# ----------------------------------------------------------------------
# grounding gate (R3, warning mode; plan §5.4 #2/#3)
# ----------------------------------------------------------------------
def grounding_warnings(
    store: KnowledgeStore, subjects: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """R3 warning-mode check shared by the hypothesis/experiment gates.

    Each subject is ``{"id": str, "evidence_ids": [str], "knowledge_gap":
    bool}``: it passes when at least one cited id starts with ``kb_`` and
    exists in the store, or when it explicitly declares a knowledge gap.
    Returns one warning row per failing subject; never raises on missing
    entries (a cited-but-absent kb id simply does not count).
    """

    missing: list[dict[str, Any]] = []
    for subject in subjects:
        if subject.get("knowledge_gap"):
            continue
        evidence_ids = [str(item) for item in subject.get("evidence_ids", [])]
        cited = [item for item in evidence_ids if item.startswith("kb_")]
        valid = []
        blocked = []
        for item in cited:
            entry = store.get_entry(item)
            if entry is None:
                continue
            if entry.get("provenance", {}).get("grounding_blocked"):
                blocked.append(item)
                continue
            valid.append(item)
        if not valid:
            missing.append(
                {
                    "id": str(subject.get("id", "")),
                    "kb_ids_cited": cited,
                    "blocked_kb_ids": blocked,
                    "reason": (
                        "no valid kb entry id cited in evidence/grounding fields "
                        "and no explicit knowledge_gap declaration (R3, warning mode)"
                    ),
                }
            )
    return missing


# ----------------------------------------------------------------------
# usage log (R4)
# ----------------------------------------------------------------------
def usage_log(store: KnowledgeStore, run_id: str) -> dict[str, Any]:
    """Knowledge usage report for one research run: entries read + proposed."""

    if not run_id.strip():
        raise ContractError(
            "run_id is required",
            error_code="run_id_missing",
            field_path="run_id",
            suggestion="提供研究运行的 run_id。",
        )
    reads = store.provenance_for_run(run_id)
    proposed = store.entries_proposed_by_run(run_id)
    return {
        "status": "ok",
        "run_id": run_id,
        "entries_used": reads,
        "entries_proposed": [
            {
                "id": entry["id"],
                "type": entry["type"],
                "title": entry["title"],
                "status": entry["status"],
                "confidence": entry["confidence"],
            }
            for entry in proposed
        ],
        "used_count": len(reads),
        "proposed_count": len(proposed),
    }


# ----------------------------------------------------------------------
# markdown re-import
# ----------------------------------------------------------------------
def import_markdown(
    store: KnowledgeStore,
    path: str | Path,
    *,
    changed_by: str = "kb_import",
) -> dict[str, Any]:
    """Re-import exported (possibly hand-edited) markdown files.

    Existing entries are updated in place with version + 1 and a fresh
    snapshot; new ids are created as version 1. Status changes go through
    the same state-machine check.
    """

    root = Path(path)
    if not root.exists():
        raise ContractError(
            f"import path not found: {root}",
            error_code="import_path_not_found",
            field_path="path",
            suggestion="提供 knowledge_base/<type>/<id>.md 文件或包含 .md 的目录。",
        )
    files = sorted(root.rglob("*.md")) if root.is_dir() else [root]
    # LLM-Wiki root documents are navigation/configuration, not entry pages.
    # Only files carrying the stable ``kb_`` naming contract are importable
    # when a whole Wiki directory is supplied.
    if root.is_dir():
        files = [file_path for file_path in files if file_path.stem.startswith("kb_")]
    imported: list[str] = []
    updated: list[str] = []
    errors: list[dict[str, str]] = []
    for file_path in files:
        try:
            parsed = import_entry_file(file_path)
            existing = store.get_entry(parsed["id"])
            now = utc_now()
            if existing is None:
                parsed.setdefault("created_at", now)
                parsed["created_at"] = parsed.get("created_at") or now
                parsed["updated_at"] = now
                parsed["version"] = 1
                parsed["created_by"] = parsed.get("created_by") or changed_by
                parsed = validate_entry(parsed)
                store.create_entry(
                    parsed, changed_by=changed_by, reason="markdown import"
                )
                _export(store, parsed)
                imported.append(parsed["id"])
            else:
                new_status = parsed.get("status", existing["status"])
                if new_status != existing["status"]:
                    check_status_transition(existing["status"], new_status)
                merged = dict(existing)
                for key in (
                    "title",
                    "content",
                    "source_type",
                    "source_ref",
                    "confidence",
                    "valid_range",
                    "related_ids",
                ):
                    if key in parsed:
                        merged[key] = parsed[key]
                merged["status"] = new_status
                merged["version"] = int(existing["version"]) + 1
                merged["updated_at"] = now
                merged = validate_entry(merged)
                store.update_entry(
                    merged, changed_by=changed_by, reason="markdown import"
                )
                _export(store, merged)
                updated.append(parsed["id"])
        except ContractError as exc:
            errors.append(
                {
                    "path": str(file_path),
                    "error": str(exc),
                    "error_code": exc.error_code,
                }
            )
    return {
        "status": "ok" if not errors else "partial",
        "imported": imported,
        "updated": updated,
        "errors": errors,
        "files_seen": len(files),
    }
