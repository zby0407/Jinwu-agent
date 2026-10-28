"""Post-run claim compiler (P2).

Deterministic compilation of verified run outcomes into knowledge candidates:

1. Every writeback claim is reduced to a **stable claim fingerprint**
   (``claim_key``) built from the claim substance — research question +
   primary estimand for findings/counterexamples, failure mode for lessons —
   never from ``run_id``.
2. The same claim reproduced by an independent run lands on the *same*
   entry and appends one row to ``entry_reproduction_evidence`` (the only
   promotion currency; reads never count).
3. Once two distinct runs carry traceable evidence refs, the compiler runs
   the ordinary promotion gate immediately — no agent discretion involved.

The compiler never rewrites claim text, never merges different claims, and
never bypasses the conflict block: promotion stays deterministic and
auditable end to end.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from . import service
from .store import KnowledgeStore

__all__ = [
    "claim_key",
    "compile_run_claim",
    "normalize_substance",
]

_MAX_SUBSTANCE_FIELD = 4_000


def normalize_substance(substance: dict[str, Any]) -> dict[str, str]:
    """Whitespace-fold and cap every substance field for stable hashing."""

    normalized: dict[str, str] = {}
    for key in sorted(substance):
        value = " ".join(str(substance.get(key) or "").split())
        if value:
            normalized[str(key)] = value[:_MAX_SUBSTANCE_FIELD]
    return normalized


def claim_key(entry_type: str, substance: dict[str, Any]) -> str:
    """Stable fingerprint of one scientific claim.

    Two runs asserting the same claim (same entry type + same normalized
    substance) produce the same key regardless of run id, timestamps, or
    phrasing of the surrounding narrative.
    """

    payload = json.dumps(
        {
            "entry_type": str(entry_type),
            "substance": normalize_substance(substance),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "claim_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def compile_run_claim(
    store: KnowledgeStore,
    *,
    entry_type: str,
    title: str,
    statement: str,
    substance: dict[str, Any],
    run_id: str,
    evidence_ref: str,
    confidence: str = "low",
    valid_range: str = "",
    related_ids: list[str] | None = None,
    agent: str = "post_run_compiler",
    content_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile one verified run outcome into the knowledge base.

    Returns ``decision`` in {``created``, ``reproduced``,
    ``already_recorded``, ``promoted``, ``promotion_not_ready``,
    ``promotion_blocked_by_conflict``, ``already_canonical``} plus the
    affected ``entry_id`` / ``entry_ids`` and ``claim_key``.
    """

    normalized_run = " ".join(str(run_id or "").split())
    normalized_evidence = " ".join(str(evidence_ref or "").split())
    if not normalized_run or not normalized_evidence:
        return {
            "status": "skipped",
            "decision": "missing_run_or_evidence",
            "entry_ids": [],
            "reason": "run_id 与可追溯 evidence_ref 均为必填。",
        }

    key = claim_key(entry_type, substance)
    content: dict[str, Any] = {"statement": statement, "run_id": normalized_run}
    content.update(content_extra or {})

    existing = store.find_entry_by_claim_key(key)
    if existing is None:
        result = service.propose(
            store,
            entry_type=entry_type,
            title=title,
            content=content,
            source_type="historical_run",
            source_ref=normalized_run,
            confidence=confidence,
            valid_range=valid_range,
            related_ids=related_ids,
            agent=agent,
            run_id=normalized_run,
            provenance_extra={
                "claim_key": key,
                "claim_substance": normalize_substance(substance),
            },
        )
        entry_id = str(result["entry"]["id"])
        reproduction = service.record_reproduction(
            store,
            entry_id,
            run_id=normalized_run,
            evidence_ref=normalized_evidence,
            method="post_run_compile",
            scope=title[:1_000],
            recorded_by=agent,
        )
        outcome: dict[str, Any] = {
            "status": "ok",
            "decision": "created",
            "entry_id": entry_id,
            "entry_ids": [entry_id],
            "claim_key": key,
            "reproduction": reproduction.get("reproduction"),
        }
        if result.get("conflicts"):
            outcome["conflicts"] = result["conflicts"]
        return outcome

    entry_id = str(existing["id"])
    if existing.get("status") == "canonical":
        return {
            "status": "ok",
            "decision": "already_canonical",
            "entry_id": entry_id,
            "entry_ids": [entry_id],
            "claim_key": key,
        }

    reproduction = service.record_reproduction(
        store,
        entry_id,
        run_id=normalized_run,
        evidence_ref=normalized_evidence,
        method="post_run_compile",
        scope=title[:1_000],
        recorded_by=agent,
    )
    if reproduction.get("decision") != "recorded":
        # Same run finalized twice: idempotent, no gate re-run needed.
        return {
            "status": "ok",
            "decision": "already_recorded",
            "entry_id": entry_id,
            "entry_ids": [entry_id],
            "claim_key": key,
            "reproduction": reproduction.get("reproduction"),
        }

    promotion = service.promote(
        store,
        entry_id,
        reason=(
            f"cross-run reproduction compiled from run {normalized_run}; "
            f"evidence: {normalized_evidence}"
        ),
    )
    decision = str(promotion.get("decision", "promotion_not_ready"))
    outcome = {
        "status": "ok",
        "decision": decision,
        "entry_id": entry_id,
        "entry_ids": [entry_id],
        "claim_key": key,
        "reproduction": reproduction.get("reproduction"),
        "supporting_run_ids": promotion.get("supporting_run_ids", []),
    }
    if decision == "promotion_blocked_by_conflict":
        outcome["conflicts"] = promotion.get("conflicts", [])
    return outcome
