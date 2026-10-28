"""Deterministic growth jobs for the knowledge base (P2 foundation).

The Wiki grows through bounded, idempotent, auditable jobs instead of an
agent freely rewriting knowledge:

* ``seed_sync``      — import missing built-in Wiki seeds (never overwrite).
* ``health_check``   — report stale candidates, unread canonical entries,
                       orphans, unresolved conflicts, undistilled sources and
                       pending review patches. Reports only; a human or the
                       main agent decides the next action.
* ``distill_queue``  — refresh the ordered list of cached sources that still
                       lack a Wiki entry. The queue is the input for the
                       task-bound ``lit_distill`` workflow; this job never
                       distills by itself because distillation requires
                       verbatim quote grounding by an agent.
* ``feed_sync``      — incremental literature feed refresh (network; only run
                       when explicitly requested).
* ``compile_runs``   — replay the post-run claim compiler over finalized
                       experiment runs (backfill). Idempotent per (claim, run),
                       so already-compiled runs contribute nothing new while
                       runs written back under the old run-scoped key get
                       merged into claim-keyed entries.

Every job row lives in ``kb_growth_jobs`` with a lease, attempt budget and an
idempotency key, so a crashed or repeated sweep cannot double-apply work.
``run_growth_cycle`` is the single entry point for a scheduled maintenance
run; it enqueues date-bucketed jobs and executes them in a fixed order.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from . import service
from .seed import ensure_builtin_wiki
from .store import KnowledgeStore, utc_now

JOB_TYPES = ("seed_sync", "health_check", "distill_queue", "feed_sync", "compile_runs")
# ``compile_runs`` 是历史回填的一次性动作，不进每日例行扫描：新 run 在
# finalize 时已经由 post-run compiler 自动编译，例行队列无需重放全部历史目录。
_SWEEP_TYPES = ("seed_sync", "health_check", "distill_queue")


def _today_bucket() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def enqueue_growth_job(
    store: KnowledgeStore,
    job_type: str,
    *,
    idempotency_key: str = "",
    payload: dict[str, Any] | None = None,
    max_attempts: int = 3,
) -> dict[str, Any]:
    """Create (or return the existing) job for one idempotency key."""

    if job_type not in JOB_TYPES:
        raise ValueError(
            f"unknown growth job type: {job_type!r}; expected one of {JOB_TYPES}"
        )
    key = str(idempotency_key or "").strip() or _today_bucket()
    return store.create_growth_job(
        job_type=job_type,
        idempotency_key=key,
        payload=payload,
        max_attempts=max_attempts,
    )


# ----------------------------------------------------------------------
# workers
# ----------------------------------------------------------------------
def _run_seed_sync(store: KnowledgeStore, payload: dict[str, Any]) -> dict[str, Any]:
    stats = ensure_builtin_wiki(store, payload.get("wiki_dir"))
    return {"seed": stats}


def _run_health_check(store: KnowledgeStore, payload: dict[str, Any]) -> dict[str, Any]:
    stale_days = int(payload.get("stale_candidate_days", 30))
    stale_candidates = store.list_stale_candidates(days=stale_days)
    unread_canonical = store.list_unread_canonical()
    orphans = store.list_orphan_entries()
    conflicts = service.conflicts(store)
    undistilled = store.count_undistilled_lit_sources()
    pending_patches = store.list_wiki_candidate_patches(
        status="proposal_only", limit=200
    )
    counts = {
        "entries": store.count_by("status"),
        "stale_candidates": len(stale_candidates),
        "unread_canonical": len(unread_canonical),
        "orphans": len(orphans),
        "unresolved_conflicts": int(conflicts.get("count", 0)),
        "undistilled_sources": undistilled,
        "proposal_only_patches": len(pending_patches),
    }
    actions: list[str] = []
    if counts["stale_candidates"]:
        actions.append(
            f"{counts['stale_candidates']} candidate(s) older than {stale_days} days "
            "without reproduction evidence: attach evidence via "
            "kb_record_reproduction, deprecate, or leave as a documented gap."
        )
    if counts["unresolved_conflicts"]:
        actions.append(
            f"{counts['unresolved_conflicts']} unresolved counterexample conflict(s) "
            "block promotion; adjudicate with new evidence."
        )
    if counts["undistilled_sources"]:
        actions.append(
            f"{counts['undistilled_sources']} cached source(s) lack a Wiki entry; "
            "run the distill_queue job and distill through lit_distill."
        )
    if counts["proposal_only_patches"]:
        actions.append(
            f"{counts['proposal_only_patches']} proposal-only patch(es) await review; "
            "patches never auto-apply."
        )
    if counts["unread_canonical"]:
        actions.append(
            f"{counts['unread_canonical']} canonical entr(ies) were never read; "
            "verify retrieval relevance or valid_range."
        )
    return {
        "counts": counts,
        "stale_candidates": stale_candidates[:50],
        "unread_canonical": unread_canonical[:50],
        "orphans": orphans[:50],
        "unresolved_conflicts": conflicts.get("conflicts", []),
        "proposal_only_patches": [
            {
                "patch_id": patch.get("patch_id"),
                "target_entry_id": patch.get("target_entry_id"),
                "relation": patch.get("relation"),
            }
            for patch in pending_patches[:50]
        ],
        "actions": actions,
    }


def _run_distill_queue(
    store: KnowledgeStore, payload: dict[str, Any]
) -> dict[str, Any]:
    limit = max(1, min(int(payload.get("limit", 10)), 50))
    queue = store.list_distill_queue(limit=limit)
    return {
        "queue_size": len(queue),
        "undistilled_total": store.count_undistilled_lit_sources(),
        "queue": queue,
        "next_step": (
            "For each queued source: lit_bind_task → lit_fetch → read the text → "
            "lit_distill with verbatim quote-grounded content. This job only "
            "orders the backlog; it never writes Wiki entries itself."
        ),
    }


def _run_feed_sync(store: KnowledgeStore, payload: dict[str, Any]) -> dict[str, Any]:
    from . import literature

    feed_id = str(payload.get("feed_id") or "").strip()
    if not feed_id:
        raise ValueError("feed_sync payload requires feed_id")
    receipt = literature.sync_literature_feed(
        store,
        feed_id,
        limit=int(payload.get("limit", 0)),
    )
    return {
        "feed_id": feed_id,
        "status": receipt.get("status"),
        "new_source_count": receipt.get("new_source_count"),
        "new_family_count": receipt.get("new_family_count"),
        "result_count": receipt.get("result_count"),
        "diagnostics": receipt.get("diagnostics"),
    }


def _run_compile_runs(store: KnowledgeStore, payload: dict[str, Any]) -> dict[str, Any]:
    """Replay the post-run claim compiler over finalized experiment runs."""

    from automatic_experiment import service as experiment_service

    limit = max(1, min(int(payload.get("limit", 50)), 200))
    runs_dir = str(payload.get("runs_dir") or "").strip()
    receipt = experiment_service.backfill_knowledge_writeback(
        limit=limit,
        runs_dir=runs_dir or None,
    )
    return {
        "scanned": receipt.get("scanned"),
        "decisions": receipt.get("decisions", {}),
        "entry_ids": receipt.get("entry_ids", []),
        "failures": receipt.get("failures", []),
    }


_WORKERS = {
    "seed_sync": _run_seed_sync,
    "health_check": _run_health_check,
    "distill_queue": _run_distill_queue,
    "feed_sync": _run_feed_sync,
    "compile_runs": _run_compile_runs,
}


# ----------------------------------------------------------------------
# runner
# ----------------------------------------------------------------------
def run_growth_job(store: KnowledgeStore, job_id: str) -> dict[str, Any]:
    """Claim and execute one job; returns the finished job row."""

    claimed = store.claim_growth_job(job_id)
    if claimed is None:
        existing = store.get_growth_job(job_id)
        if existing is None:
            raise ValueError(f"growth job not found: {job_id!r}")
        return {
            "status": "skipped",
            "reason": f"job is {existing.get('status')!r} or out of attempts",
            "job": existing,
        }
    worker = _WORKERS[claimed["job_type"]]
    try:
        result = worker(store, dict(claimed.get("payload") or {}))
    except Exception as exc:
        job = store.finish_growth_job(job_id, error=f"{type(exc).__name__}: {exc}")
        return {"status": "error", "error": str(exc), "job": job}
    job = store.finish_growth_job(job_id, result=result)
    return {"status": "done", "job": job, "result": result}


def run_growth_cycle(
    store: KnowledgeStore,
    *,
    bucket: str = "",
    feed_ids: list[str] | None = None,
    run_feed_sync: bool = False,
    distill_limit: int = 10,
    compile_limit: int = 50,
    run_compile_runs: bool = False,
) -> dict[str, Any]:
    """Enqueue and run one deterministic maintenance cycle.

    ``bucket`` defaults to today's UTC date, so repeated calls on the same day
    are idempotent: finished jobs are skipped, not re-executed. Feed syncs are
    network operations and only run when ``run_feed_sync`` is true; historical
    claim backfill only runs when ``run_compile_runs`` is true.
    """

    key = str(bucket or "").strip() or _today_bucket()
    planned: list[dict[str, Any]] = []
    payloads: dict[str, dict[str, Any]] = {
        "distill_queue": {"limit": max(1, min(int(distill_limit), 50))},
        "compile_runs": {"limit": max(1, min(int(compile_limit), 200))},
    }
    for job_type in _SWEEP_TYPES:
        planned.append(
            enqueue_growth_job(
                store,
                job_type,
                idempotency_key=key,
                payload=payloads.get(job_type),
            )
        )
    if run_compile_runs:
        planned.append(
            enqueue_growth_job(
                store,
                "compile_runs",
                idempotency_key=key,
                payload=payloads["compile_runs"],
            )
        )
    if run_feed_sync:
        for feed_id in feed_ids or []:
            planned.append(
                enqueue_growth_job(
                    store,
                    "feed_sync",
                    idempotency_key=f"{key}:{feed_id}",
                    payload={"feed_id": str(feed_id)},
                )
            )

    outcomes: list[dict[str, Any]] = []
    for job in planned:
        job_id = str(job.get("job_id") or "")
        if not job_id:
            continue
        if job.get("status") == "done":
            outcomes.append(
                {
                    "job_id": job_id,
                    "job_type": job["job_type"],
                    "status": "already_done",
                }
            )
            continue
        outcome = run_growth_job(store, job_id)
        outcomes.append(
            {
                "job_id": job_id,
                "job_type": job["job_type"],
                "status": outcome.get("status"),
                "result": outcome.get("result"),
                "error": outcome.get("error"),
            }
        )
    return {
        "status": "ok",
        "bucket": key,
        "created_at": utc_now(),
        "jobs": outcomes,
    }


def _rate(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def derive_metrics(raw: dict[str, Any]) -> dict[str, Any]:
    """Derived health metrics for the Wiki.

    The four rates answer "is the Wiki actually being used and actually
    growing", not "how many rows exist":

    * ``reuse_rate``            — share of canonical entries that were read at
      least once. A canonical entry nobody reads is decoration.
    * ``injection_rate``        — share of reads that came from host-injected
      stage context rather than an agent choosing to query. This is the number
      P1 was built to move.
    * ``promotion_precision``   — share of auto-promoted canonical entries that
      carry ≥2 distinct reproduction runs. By construction this must be 1.0;
      anything lower means the gate leaked, so it doubles as an invariant check.
    * ``distillation_conversion`` — share of cached preferred sources that have
      been distilled into an entry. This is the literature → Wiki funnel.
    """

    entries = raw["entries"]
    reads = raw["reads"]
    reproduction = raw["reproduction"]
    literature = raw["literature"]
    return {
        "status": "ok",
        "counts": raw,
        "reuse_rate": _rate(reads["canonical_entries_read"], entries["canonical"]),
        "injection_rate": _rate(reads["injected_total"], reads["total"]),
        "promotion_precision": _rate(
            reproduction["promoted_with_evidence"],
            reproduction["auto_promoted_canonical"],
        ),
        "distillation_conversion": _rate(
            literature["distilled_sources"], literature["preferred_sources"]
        ),
        "reads_per_run": _rate(reads["total"], reads["distinct_runs"]),
    }


def growth_metrics(store: KnowledgeStore) -> dict[str, Any]:
    """``derive_metrics`` over a live store."""

    return derive_metrics(store.usage_raw_counts())


def growth_status(store: KnowledgeStore, *, limit: int = 20) -> dict[str, Any]:
    """Compact dashboard for the maintenance queue."""

    jobs = store.list_growth_jobs(limit=limit)
    by_status: dict[str, int] = {}
    for job in jobs:
        status = str(job.get("status") or "unknown")
        by_status[status] = by_status.get(status, 0) + 1
    return {
        "status": "ok",
        "job_types": list(JOB_TYPES),
        "recent_jobs": [
            {
                "job_id": job.get("job_id"),
                "job_type": job.get("job_type"),
                "status": job.get("status"),
                "attempts": job.get("attempts"),
                "last_error": job.get("last_error"),
                "updated_at": job.get("updated_at"),
            }
            for job in jobs
        ],
        "by_status": by_status,
        "metrics": growth_metrics(store),
    }


__all__ = [
    "JOB_TYPES",
    "derive_metrics",
    "enqueue_growth_job",
    "growth_metrics",
    "growth_status",
    "run_growth_cycle",
    "run_growth_job",
]
