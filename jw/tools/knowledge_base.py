"""LangChain tool wrappers for the knowledge base (LLM Wiki) subsystem.

These tools expose the knowledge service to the JW agent. They
wrap deterministic contract validation, storage, retrieval, provenance
logging, and the promotion review gate implemented in
``src/knowledge_base``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SRC = _PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from langchain_core.runnables import RunnableConfig  # noqa: E402
from langchain_core.tools import tool  # noqa: E402

from jw.tools.registry import register_tool_bundle  # noqa: E402
from knowledge_base import literature, service  # noqa: E402
from knowledge_base.store import KnowledgeStore  # noqa: E402

_STORE: KnowledgeStore | None = None
_DISTILL_BINDINGS: dict[str, dict[str, Any]] = {}
_ACTIVE_DISTILL_BINDINGS: dict[str, str] = {}


def _get_store() -> KnowledgeStore:
    """Shared store (lazy so importing tools never touches the real db)."""

    global _STORE
    if _STORE is None:
        _STORE = KnowledgeStore()
    return _STORE


def _ok(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, default=str)


def _err(message: str) -> str:
    return json.dumps(
        {"status": "error", "error": message}, ensure_ascii=False, default=str
    )


def _run_context(config: RunnableConfig | None) -> tuple[str, str]:
    """Best-effort ``(agent, run_id)`` extracted from the LangGraph run config.

    Callers (main agent, sub-agents) often omit explicit attribution; fall
    back to the thread id and graph/node metadata so provenance logging
    never silently degrades to empty strings.
    """

    if not config or not isinstance(config, dict):
        return "", ""
    configurable = config.get("configurable") or {}
    metadata = config.get("metadata") or {}
    run_id = str(configurable.get("thread_id") or "")
    agent = str(
        metadata.get("agent_name")
        or metadata.get("langgraph_node")
        or configurable.get("agent_name")
        or ""
    )
    return agent, run_id


def _binding_context_key(config: RunnableConfig | None) -> str:
    agent, run_id = _run_context(config)
    return run_id or agent or "__default__"


def _human_gate_active() -> bool:
    """True when kb_promote calls cannot execute without human approval.

    ``kb_promote`` is in the HITL ``interrupt_on`` set whenever
    ``auto_approve`` is off, so a promote call that *runs* necessarily
    passed human review — that approval IS the expert-review evidence.
    """

    try:
        from jw.config import get_effective_config

        return not bool(get_effective_config().auto_approve)
    except Exception:  # noqa: BLE001
        return False


@tool(parse_docstring=True)
def kb_search(
    query: str,
    type: str = "",
    status: str = "",
    confidence: str = "",
    valid_range: str = "",
    limit: int = 8,
) -> str:
    """Search the knowledge base with FTS5 keywords plus structured filters.

    By default only canonical and candidate entries are returned;
    deprecated/superseded entries are excluded unless ``status`` explicitly
    asks for them.

    Args:
        query: Keyword query (Chinese and English supported); empty lists all.
        type: Optional entry type filter (concept/mechanism/data_source/
            experiment_paradigm/hypothesis_template/finding/counterexample).
        status: Optional status filter (candidate/canonical/deprecated/superseded).
        confidence: Optional confidence filter (high/medium/low).
        valid_range: Optional substring filter on the entry's valid range.
        limit: Maximum number of results (1-50, default 8).

    Returns:
        JSON string with ranked matching entries and their provenance fields.
    """
    try:
        return _ok(
            service.search(
                _get_store(),
                query,
                entry_type=type,
                status=status,
                confidence=confidence,
                valid_range=valid_range,
                limit=limit,
            )
        )
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


@tool(parse_docstring=True)
def kb_read(
    entry_id: str,
    agent: str = "",
    run_id: str = "",
    purpose: str = "",
    config: RunnableConfig = None,
) -> str:
    """Read one knowledge entry in full; the read is logged to provenance_log.

    Args:
        entry_id: Entry id such as ``kb_concept_sunspot_cycle_001``.
        agent: Calling agent name recorded in the provenance log.
        run_id: Current research run id recorded in the provenance log.
        purpose: Why the entry is being used (grounding, review, ...).

    Returns:
        JSON string with the full entry including content and provenance.
    """
    try:
        ctx_agent, ctx_run_id = _run_context(config)
        return _ok(
            service.read(
                _get_store(),
                entry_id,
                agent=agent or ctx_agent,
                run_id=run_id or ctx_run_id,
                purpose=purpose,
            )
        )
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


@tool(parse_docstring=True)
def kb_propose(
    type: str,
    title: str,
    content: dict[str, Any],
    source_type: str,
    source_ref: str,
    confidence: str,
    valid_range: str = "",
    related_ids: list[str] | None = None,
    agent: str = "",
    run_id: str = "",
    config: RunnableConfig = None,
) -> str:
    """Propose a new knowledge entry. Always stored as status=candidate.

    Content is schema-validated per entry type. If a counterexample names a
    canonical entry in ``related_ids``, a conflict review item is queued
    (the write is not blocked and never overwrites canonical knowledge).

    Args:
        type: Entry type (concept/mechanism/data_source/experiment_paradigm/
            hypothesis_template/finding/counterexample).
        title: One-sentence title.
        content: Structured content object with the per-type sub-fields
            (e.g. concept requires ``definition``; finding requires
            ``statement`` and ``run_id``).
        source_type: literature/textbook/dataset_doc/historical_run/expert/derived.
        source_ref: Traceable source: DOI, URL, run_id, reviewer, or book page.
        confidence: high/medium/low.
        valid_range: Applicability range, e.g. "SC21–SC25".
        related_ids: Related entry ids.
        agent: Proposing agent name.
        run_id: Research run that produced this candidate.

    Returns:
        JSON string with the created entry (status=candidate) and any
        queued conflict warnings.
    """
    try:
        ctx_agent, ctx_run_id = _run_context(config)
        return _ok(
            service.propose(
                _get_store(),
                entry_type=type,
                title=title,
                content=content,
                source_type=source_type,
                source_ref=source_ref,
                confidence=confidence,
                valid_range=valid_range,
                related_ids=related_ids,
                agent=agent or ctx_agent,
                run_id=run_id or ctx_run_id,
            )
        )
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


@tool(parse_docstring=True)
def kb_promote(
    entry_id: str,
    reason: str,
    reviewer: str = "",
    config: RunnableConfig = None,
) -> str:
    """Run the promotion review gate for a candidate entry.

    Auto-approves to canonical only when the finding was reproduced in >=2
    distinct run ids or a non-empty ``reviewer`` provides expert review.
    A DOI proves source identity but never auto-approves scientific support.
    When HITL approval is active (auto_approve off), a promote call that
    executes has necessarily been human-approved, so the expert rule fires
    with reviewer recorded as ``human(HITL)``. Otherwise the request is
    queued as pending for human decision via ``kb_review_decide`` and the
    entry stays candidate.

    Args:
        entry_id: Candidate entry id to promote.
        reason: Promotion justification (reproduction evidence / literature
            support / expert judgement).
        reviewer: Expert reviewer name; non-empty triggers the expert rule.

    Returns:
        JSON string with the gate decision (auto_approved or pending_review).
    """
    try:
        if not reviewer.strip() and _human_gate_active():
            reviewer = "human(HITL)"
        return _ok(
            service.promote(_get_store(), entry_id, reason=reason, reviewer=reviewer)
        )
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


@tool(parse_docstring=True)
def kb_deprecate(entry_id: str, reason: str, superseded_by: str = "") -> str:
    """Mark an entry deprecated or superseded; never deletes, keeps versions.

    Args:
        entry_id: Entry id to deprecate.
        reason: Why the entry is retired (contradicting evidence, data
            recalibration, weakened theory).
        superseded_by: Optional replacement entry id; when given the status
            becomes ``superseded`` instead of ``deprecated``.

    Returns:
        JSON string with the updated entry status and version.
    """
    try:
        return _ok(
            service.deprecate(
                _get_store(), entry_id, reason=reason, superseded_by=superseded_by
            )
        )
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


@tool(parse_docstring=True)
def kb_conflicts(entry_id: str = "") -> str:
    """List pending conflict-review items (counterexample vs canonical).

    Args:
        entry_id: Optional entry id to filter conflicts involving it.

    Returns:
        JSON string with pending conflict queue items.
    """
    try:
        return _ok(service.conflicts(_get_store(), entry_id=entry_id))
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


@tool(parse_docstring=True)
def kb_review_queue(kind: str = "", entry_id: str = "") -> str:
    """List pending knowledge review items, including legacy revalidation.

    Args:
        kind: Optional promote/conflict/deprecate/revalidate filter.
        entry_id: Optional entry id filter.

    Returns:
        JSON string with pending review items and their evidence requirements.
    """
    try:
        return _ok(service.review_queue(_get_store(), kind=kind, entry_id=entry_id))
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


@tool(parse_docstring=True)
def kb_review_decide(
    queue_id: int, decision: str, note: str = "", reviewer: str = "human"
) -> str:
    """Apply a human approve/reject decision to a pending review-queue item.

    For ``revalidate`` items, approval only unblocks the legacy entry for
    grounding; rejection deprecates it. A legacy DOI-promoted canonical is
    demoted to candidate and still needs a separate promotion decision. All
    outcomes preserve version history.

    Args:
        queue_id: Review queue id from kb_review_queue, kb_promote, or kb_conflicts.
        decision: ``approved`` or ``rejected``.
        note: Optional reviewer note recorded with the decision.
        reviewer: Reviewer name (defaults to ``human``).

    Returns:
        JSON string with the queue item outcome and affected entry status.
    """
    try:
        return _ok(
            service.review_decide(
                _get_store(),
                int(queue_id),
                decision=decision,
                note=note,
                reviewer=reviewer,
            )
        )
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


@tool(parse_docstring=True)
def kb_log(run_id: str) -> str:
    """Knowledge usage report for one research run.

    Args:
        run_id: Research run id.

    Returns:
        JSON string listing entries read (from provenance_log) and
        candidate entries proposed during that run.
    """
    try:
        return _ok(service.usage_log(_get_store(), run_id))
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


@tool(parse_docstring=True)
def kb_import(path: str = "") -> str:
    """Re-import exported (possibly hand-edited) markdown entries.

    Existing ids are updated with version + 1 and a new snapshot; new ids
    are created as version 1. All content is contract-validated.

    Args:
        path: A markdown file or a directory of markdown files; empty uses
            the active ``<workspace>/knowledge_base/`` export directory.

    Returns:
        JSON string with imported/updated id lists and per-file errors.
    """
    try:
        store = _get_store()
        target = path or str(store.export_dir)
        return _ok(service.import_markdown(store, target))
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


@tool(parse_docstring=True)
def lit_bind_task(
    research_question: str,
    distill_focus: str,
    run_id: str = "",
    config: RunnableConfig = None,
) -> str:
    """Bind the task-owned research question and focus before literature work.

    The focus must preserve distinctive terms from the research question. For
    cross-language literature, keep the task's original core term and append
    its source-language equivalent, for example ``极区磁场 / polar field``.
    The returned binding id is required by ``lit_distill`` and prevents an
    agent from silently changing topic between search and distillation.

    Args:
        research_question: Exact research question supplied by the parent task.
        distill_focus: Exact bounded sub-question this literature pass must answer.
        run_id: Optional research run id; runtime thread id is used when omitted.

    Returns:
        JSON string containing an immutable binding id and normalized focus.
    """
    try:
        _, ctx_run_id = _run_context(config)
        binding = literature.bind_distill_task(
            research_question,
            distill_focus,
            run_id=run_id or ctx_run_id,
        )
        binding_id = str(binding["binding_id"])
        _DISTILL_BINDINGS[binding_id] = binding
        _ACTIVE_DISTILL_BINDINGS[_binding_context_key(config)] = binding_id
        return _ok({"status": "ok", **binding})
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


@tool(parse_docstring=True)
def lit_search(
    query: str,
    source: str = "all",
    limit: int = 5,
    from_year: int = 0,
    to_year: int = 0,
) -> str:
    """Search external literature and cache hits.

    Every hit refreshes its provider-version row and is grouped with preprint,
    journal, or updated-review relatives. Results expose only the preferred
    family version. ``all`` queries OpenAlex, arXiv, and Crossref, tolerating
    a partial provider outage without fabricating references.

    Args:
        query: Search keywords (English works best).
        source: ``all``, ``openalex``, ``arxiv``, or ``crossref``.
        limit: Maximum number of results (1-10, default 5).
        from_year: Earliest publication year (0 means no filter).
        to_year: Latest publication year (0 means no filter).

    Returns:
        JSON string with matched literature metadata and cache flags.
    """
    try:
        return _ok(
            literature.search_literature(
                _get_store(),
                query,
                source=source,
                limit=limit,
                from_year=from_year,
                to_year=to_year,
            )
        )
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


@tool(parse_docstring=True)
def lit_fetch(source_id: str) -> str:
    """Write a cached literature source to the task and persistent Wiki source layer.

    The task-readable copy goes to ``workspace/literature/`` (falling back to
    ``<DATA_DIR>/literature``); an immutable Wiki copy goes to
    ``<workspace>/knowledge_base/raw/sources/``. Idempotent: an already-fetched
    source returns the existing file with ``cached=true``.

    Args:
        source_id: Cached source id from ``lit_search``, like ``openalex:W123``.

    Returns:
        JSON string with the text file path and its length.
    """
    try:
        return _ok(literature.fetch_literature(_get_store(), source_id))
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


@tool(parse_docstring=True)
def lit_distill(
    source_id: str,
    entry_type: str,
    title: str,
    content: dict[str, Any],
    binding_id: str = "",
    agent: str = "",
    confidence: str = "low",
    config: RunnableConfig = None,
) -> str:
    """Distill a cached literature source into a candidate knowledge entry.

    Anti-hallucination contract: every evidence-bearing content field must be
    ``{"text": ..., "quote": ..., "location": ...}`` where ``quote`` is a
    verbatim passage (<=40 words) that hits the cached source text; fields
    without textual support must be ``"evidence_gap"``. Quotes that do not
    hit are rejected (``quote_not_grounded``). A prior ``lit_bind_task`` call
    is mandatory; idempotency is enforced per literature family and normalized
    focus. Single-source abstract distillation defaults to low confidence and
    cannot exceed medium. ``source_ref`` is auto-filled from DOI/URL.

    Args:
        source_id: Cached source id (from ``lit_search``).
        entry_type: Entry type (concept/mechanism/data_source/
            experiment_paradigm/hypothesis_template/finding/counterexample).
        title: One-sentence title for the distilled entry.
        content: Mapping of content fields to evidence objects or
            ``"evidence_gap"``.
        binding_id: Binding id returned by ``lit_bind_task``; active binding is
            used when omitted in the same runtime thread.
        agent: Distilling agent name.
        confidence: medium/low (default low); high is rejected.

    Returns:
        JSON string with the new candidate entry id and verified quote count.
    """
    try:
        selected_id = binding_id or _ACTIVE_DISTILL_BINDINGS.get(
            _binding_context_key(config), ""
        )
        binding = _DISTILL_BINDINGS.get(selected_id)
        if not binding:
            raise ValueError(
                "No bound literature task. Call lit_bind_task first and pass its binding_id."
            )
        ctx_agent, ctx_run_id = _run_context(config)
        return _ok(
            literature.distill_literature(
                _get_store(),
                source_id,
                entry_type,
                title,
                content,
                focus=str(binding["distill_focus"]),
                research_question=str(binding["research_question"]),
                research_request_sha256=selected_id,
                run_id=str(binding.get("run_id") or ctx_run_id),
                agent=agent or ctx_agent,
                confidence=confidence,
            )
        )
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


KB_TOOLS = [
    kb_search,
    kb_read,
    kb_propose,
    kb_promote,
    kb_deprecate,
    kb_conflicts,
    kb_review_queue,
    kb_review_decide,
    kb_log,
    kb_import,
    lit_bind_task,
    lit_search,
    lit_fetch,
    lit_distill,
]

register_tool_bundle("knowledge-base", KB_TOOLS)

__all__ = ["KB_TOOLS"] + [t.name for t in KB_TOOLS]
