"""Stage-aware knowledge context selection for JW research agents.

The Wiki is only useful if every research stage sees the *right* slice of it.
This module turns a research question into a small, deterministic, auditable
context packet:

* canonical entries outrank candidates, regardless of keyword rank;
* entry types are prioritised per research stage;
* unresolved counterexamples mark canonical entries as contested;
* every selected entry carries its applicability range and provenance;
* selection is logged as a knowledge read (usage receipt), never as
  reproduction evidence.

Selection is pure Python on top of the existing FTS search.  The FTS schema is
not changed; ``bm25`` remains the first-pass discovery signal, and status/type/
conflict weighting is applied here.
"""

from __future__ import annotations

import hashlib
import html
import json
from typing import Any

from . import service
from .store import KnowledgeStore

SCHEMA_VERSION = "knowledge-context-v1"
CONTEXT_STAGES = {
    "planning",
    "data",
    "hypothesis",
    "experiment_design",
    "experiment_result",
    "evidence",
}
STAGE_ALIASES = {"experiment": "experiment_design", "review": "evidence"}

STAGE_AGENT = {
    "planning": "solar-planner",
    "data": "solar-data",
    "hypothesis": "solar-hypothesis",
    "experiment_design": "solar-experiment",
    "experiment_result": "solar-experiment",
    "evidence": "solar-evidence",
}

# Lower is better.  The ordering is intentionally explicit: a stage should not
# accidentally prefer a hypothesis template over a measured finding.
_STAGE_TYPE_PRIORITY: dict[str, dict[str, float]] = {
    "planning": {
        "mechanism": 0.00,
        "data_source": 0.05,
        "experiment_paradigm": 0.10,
        "concept": 0.15,
        "finding": 0.20,
        "hypothesis_template": 0.25,
        "lesson": 0.30,
        "counterexample": 0.35,
    },
    "data": {
        "data_source": 0.00,
        "lesson": 0.05,
        "experiment_paradigm": 0.10,
        "concept": 0.20,
        "mechanism": 0.25,
        "finding": 0.30,
        "counterexample": 0.35,
        "hypothesis_template": 0.40,
    },
    "hypothesis": {
        "mechanism": 0.00,
        "hypothesis_template": 0.05,
        "finding": 0.10,
        "counterexample": 0.15,
        "experiment_paradigm": 0.20,
        "data_source": 0.25,
        "lesson": 0.30,
        "concept": 0.35,
    },
    "experiment_design": {
        "experiment_paradigm": 0.00,
        "lesson": 0.05,
        "data_source": 0.10,
        "mechanism": 0.15,
        "finding": 0.20,
        "counterexample": 0.25,
        "hypothesis_template": 0.30,
        "concept": 0.35,
    },
    "experiment_result": {
        "finding": 0.00,
        "counterexample": 0.05,
        "lesson": 0.10,
        "experiment_paradigm": 0.15,
        "data_source": 0.20,
        "mechanism": 0.25,
        "concept": 0.30,
        "hypothesis_template": 0.35,
    },
    "evidence": {
        "finding": 0.00,
        "counterexample": 0.05,
        "lesson": 0.10,
        "mechanism": 0.15,
        "data_source": 0.20,
        "experiment_paradigm": 0.25,
        "concept": 0.30,
        "hypothesis_template": 0.35,
    },
}

_STATUS_BASE = {"canonical": 0.0, "candidate": 0.55}
_CONFIDENCE_PENALTY = {"high": 0.0, "medium": 0.04, "low": 0.12}
_CONTESTED_PENALTY = 0.28
_UNKNOWN_TYPE_PRIORITY = 0.40

_EXCERPT_ORDER = {
    "concept": ("definition", "physical_notes", "see_also"),
    "mechanism": (
        "claim",
        "supporting_evidence",
        "counter_evidence",
        "testable_predictions",
        "controversy",
    ),
    "data_source": (
        "collection_method",
        "coverage",
        "known_biases",
        "calibration_history",
    ),
    "experiment_paradigm": ("design", "metrics", "pitfalls"),
    "hypothesis_template": ("structure", "applicable_when", "example"),
    "finding": ("statement", "effect_size", "uncertainty"),
    "counterexample": ("statement", "effect_size", "uncertainty"),
    "lesson": ("statement", "failure_mode", "remediation", "uncertainty"),
}

_MAX_QUERY_CHARS = 800
_MAX_EXCERPT_FIELDS = 5
_MAX_EXCERPT_FIELD_CHARS = 420
_MAX_RENDER_CHARS = 14_000


def normalize_stage(stage: str) -> str:
    """Return a canonical stage name or raise ``ValueError``."""

    value = str(stage or "").strip().lower()
    value = STAGE_ALIASES.get(value, value)
    if value not in CONTEXT_STAGES:
        raise ValueError(
            f"unsupported knowledge-context stage: {stage!r}; "
            f"expected one of {sorted(CONTEXT_STAGES)}"
        )
    return value


def _norm_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, (list, tuple)):
        return " · ".join(part for item in value if (part := _norm_text(item)))
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return " ".join(str(value).split())


def _clip(text: str, limit: int) -> str:
    text = _norm_text(text)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _entry_excerpt(entry: dict[str, Any]) -> dict[str, str]:
    """Small per-type excerpt; the full entry stays behind ``kb_read``."""

    content = entry.get("content")
    if not isinstance(content, dict):
        return {}
    order = _EXCERPT_ORDER.get(entry.get("type", ""), ())
    keys = [key for key in order if key in content and _norm_text(content[key])]
    keys.extend(
        key for key in sorted(content) if key not in keys and _norm_text(content[key])
    )
    excerpt: dict[str, str] = {}
    for key in keys[:_MAX_EXCERPT_FIELDS]:
        excerpt[key] = _clip(content[key], _MAX_EXCERPT_FIELD_CHARS)
    return excerpt


def _conflict_index(
    store: KnowledgeStore,
) -> tuple[dict[str, list[str]], list[dict[str, str]], str]:
    """Map entry id -> related conflicted ids and return unresolved conflicts."""

    try:
        result = service.conflicts(store)
    except Exception as exc:
        return {}, [], f"unresolved-conflict index unavailable: {exc}"
    index: dict[str, list[str]] = {}
    conflicts: list[dict[str, str]] = []
    for item in result.get("conflicts", []):
        candidate_id = str(item.get("candidate_id") or "")
        canonical_id = str(item.get("canonical_id") or "")
        if not candidate_id or not canonical_id:
            continue
        index.setdefault(candidate_id, []).append(canonical_id)
        index.setdefault(canonical_id, []).append(candidate_id)
        conflicts.append(
            {
                "candidate_id": candidate_id,
                "canonical_id": canonical_id,
                "status": str(item.get("status") or "unresolved"),
            }
        )
    return index, conflicts, ""


def _rank_key(
    entry: dict[str, Any],
    *,
    stage: str,
    conflict_ids: list[str],
) -> tuple[float, str]:
    status = str(entry.get("status") or "candidate")
    entry_type = str(entry.get("type") or "")
    score = _STATUS_BASE.get(status, 0.6)
    score += (
        _STAGE_TYPE_PRIORITY.get(stage, {}).get(entry_type, _UNKNOWN_TYPE_PRIORITY)
        * 0.35
    )
    score += _CONFIDENCE_PENALTY.get(str(entry.get("confidence") or ""), 0.08)
    if status == "canonical" and conflict_ids:
        score += _CONTESTED_PENALTY
    return score, str(entry.get("id") or "")


def _select_entries(
    canonical: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    *,
    stage: str,
    conflict_index: dict[str, list[str]],
    limit: int,
) -> list[dict[str, Any]]:
    """Select canonical-first, with a small candidate reserve for new work."""

    def ranked(entries: list[dict[str, Any]]) -> list[tuple[float, dict[str, Any]]]:
        rows = [
            (
                _rank_key(
                    entry,
                    stage=stage,
                    conflict_ids=conflict_index.get(str(entry.get("id")), []),
                )[0],
                entry,
            )
            for entry in entries
        ]
        return sorted(rows, key=lambda item: (item[0], str(item[1].get("id") or "")))

    canonical_rows = ranked(canonical)
    candidate_rows = ranked(candidates)
    candidate_slots = min(2, limit, len(candidate_rows))
    canonical_cap = max(0, limit - candidate_slots)
    selected = canonical_rows[:canonical_cap] + candidate_rows[:candidate_slots]

    seen = {str(entry.get("id")) for _, entry in selected}
    remaining = sorted(
        [
            *(row for row in canonical_rows[canonical_cap:]),
            *(row for row in candidate_rows[candidate_slots:]),
        ],
        key=lambda item: (item[0], str(item[1].get("id") or "")),
    )
    for score, entry in remaining:
        if len(selected) >= limit:
            break
        entry_id = str(entry.get("id"))
        if entry_id not in seen:
            selected.append((score, entry))
            seen.add(entry_id)
    return [entry for _, entry in sorted(selected, key=lambda item: item[0])]


def build_knowledge_context(
    store: KnowledgeStore,
    query: str,
    *,
    stage: str,
    valid_range: str = "",
    limit: int = 6,
    agent: str = "",
    run_id: str = "",
    record_usage: bool = True,
) -> dict[str, Any]:
    """Build one frozen, stage-aware context packet from the Wiki.

    ``record_usage`` writes a provenance row for every selected entry.  Those
    rows are usage receipts, never reproduction evidence.
    """

    stage = normalize_stage(stage)
    query = " ".join(str(query or "").split())[:_MAX_QUERY_CHARS]
    limit = max(1, min(int(limit), 10))
    search_limit = max(8, limit * 3)
    warnings: list[str] = []
    conflict_index, open_conflicts, conflict_error = _conflict_index(store)
    if conflict_error:
        warnings.append(conflict_error)

    canonical = store.search(
        query,
        status="canonical",
        valid_range=valid_range,
        limit=search_limit,
    )
    candidates = store.search(
        query,
        status="candidate",
        valid_range=valid_range,
        limit=search_limit,
    )
    selected = _select_entries(
        canonical,
        candidates,
        stage=stage,
        conflict_index=conflict_index,
        limit=limit,
    )

    entries: list[dict[str, Any]] = []
    for raw in selected:
        entry_id = str(raw.get("id") or "")
        conflict_ids = sorted(set(conflict_index.get(entry_id, [])))
        status = str(raw.get("status") or "candidate")
        contested = status == "canonical" and bool(conflict_ids)
        usage_role = (
            "contested_canonical"
            if contested
            else ("canonical" if status == "canonical" else "candidate")
        )
        entries.append(
            {
                "id": entry_id,
                "type": raw.get("type", ""),
                "title": raw.get("title", ""),
                "status": status,
                "usage_role": usage_role,
                "confidence": raw.get("confidence", ""),
                "valid_range": raw.get("valid_range", ""),
                "source_type": raw.get("source_type", ""),
                "source_ref": raw.get("source_ref", ""),
                "version": raw.get("version"),
                "excerpt": _entry_excerpt(raw),
                "related_ids": list(raw.get("related_ids") or [])[:8],
                "conflict_ids": conflict_ids,
            }
        )

    if record_usage:
        for entry in entries:
            try:
                service.read(
                    store,
                    entry["id"],
                    agent=agent,
                    run_id=run_id,
                    purpose=f"stage_context:{stage}",
                )
            except Exception as exc:
                warnings.append(f"usage receipt failed for {entry['id']}: {exc}")

    counts = {
        "canonical": sum(1 for entry in entries if entry["status"] == "canonical"),
        "candidate": sum(1 for entry in entries if entry["status"] == "candidate"),
        "contested": sum(
            1 for entry in entries if entry["usage_role"] == "contested_canonical"
        ),
    }
    if not query:
        status = "empty"
        gaps = [
            "No research query was supplied; no stage-specific Wiki context can be selected."
        ]
    elif not entries:
        status = "empty"
        gaps = ["No eligible Wiki entry matched the stage query."]
    elif counts["canonical"] == 0:
        status = "candidate_only"
        gaps = [
            "Only candidate entries matched; treat them as provisional research leads, "
            "not settled knowledge."
        ]
    else:
        status = "ok"
        gaps = []
    if entries and counts["canonical"] == 0 and status == "ok":
        gaps.append("No canonical entry matched the stage query.")

    constraints: list[dict[str, str]] = []
    for entry in entries:
        if entry["valid_range"]:
            constraints.append(
                {
                    "entry_id": entry["id"],
                    "kind": "valid_range",
                    "text": f"Applicability is limited to: {entry['valid_range']}",
                }
            )
        if entry["usage_role"] == "contested_canonical":
            constraints.append(
                {
                    "entry_id": entry["id"],
                    "kind": "unresolved_conflict",
                    "text": (
                        "Canonical but contested by unresolved counterexample(s): "
                        + ", ".join(entry["conflict_ids"])
                    ),
                }
            )

    digest_payload = json.dumps(
        {
            "stage": stage,
            "query": query,
            "valid_range": valid_range,
            "entries": [
                [entry["id"], entry["status"], entry["version"]] for entry in entries
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(digest_payload.encode("utf-8")).hexdigest()[:16]
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "stage": stage,
        "agent": agent or STAGE_AGENT[stage],
        "query": query,
        "valid_range": valid_range,
        "digest": digest,
        "counts": counts,
        "entries": entries,
        "constraints": constraints,
        "open_conflicts": open_conflicts,
        "gaps": gaps,
        "warnings": warnings,
        "usage_receipts": [
            {"entry_id": entry["id"], "purpose": f"stage_context:{stage}"}
            for entry in entries
        ],
        "instruction": (
            "Use only the listed entry ids as Wiki context. Canonical entries may "
            "constrain terminology and scope; candidates are provisional leads; "
            "contested canonical entries must not be treated as settled. Wiki "
            "context is not observational or experimental evidence. If the packet "
            "is empty or insufficient, state the knowledge gap. In your final "
            "result, cite the entry ids actually used where this task's response "
            "schema or report format allows; never add fields forbidden by a "
            "strict contract, and record the gap reason when none were used."
        ),
    }


def unavailable_context(stage: str, query: str, error: str) -> dict[str, Any]:
    """Explicit failure packet: never silently drop knowledge context."""

    try:
        normalized = normalize_stage(stage)
    except ValueError:
        normalized = str(stage or "unknown")
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "unavailable",
        "stage": normalized,
        "agent": STAGE_AGENT.get(normalized, ""),
        "query": " ".join(str(query or "").split())[:_MAX_QUERY_CHARS],
        "valid_range": "",
        "digest": "",
        "counts": {"canonical": 0, "candidate": 0, "contested": 0},
        "entries": [],
        "constraints": [],
        "open_conflicts": [],
        "gaps": [f"Knowledge context is unavailable: {error}"],
        "warnings": [error],
        "usage_receipts": [],
        "instruction": (
            "Do not fabricate Wiki context. Continue only with inspected task "
            "evidence and record the knowledge-context unavailability as a gap."
        ),
    }


def _render_excerpt(excerpt: dict[str, str]) -> str:
    return "\n".join(
        f"    {field}: {html.escape(text, quote=False)}"
        for field, text in excerpt.items()
    )


def render_knowledge_context(context: dict[str, Any]) -> str:
    """Render a compact XML-like block for prompt injection."""

    stage = html.escape(str(context.get("stage") or ""), quote=True)
    digest = html.escape(str(context.get("digest") or ""), quote=True)
    lines = [
        f'<knowledge_context schema_version="{SCHEMA_VERSION}" '
        f'stage="{stage}" digest="{digest}">',
        f"status: {context.get('status', '')}",
        f"query: {html.escape(str(context.get('query') or ''), quote=False)}",
        (
            "counts: canonical={canonical} candidate={candidate} "
            "contested={contested}".format(**context.get("counts", {}))
        ),
    ]
    entries = context.get("entries") or []
    if entries:
        lines.append("entries:")
        for entry in entries:
            lines.append(
                "- [{role}] {entry_id} | {entry_type} | {status} | "
                "confidence={confidence} | valid_range={valid_range}".format(
                    role=entry.get("usage_role", ""),
                    entry_id=entry.get("id", ""),
                    entry_type=entry.get("type", ""),
                    status=entry.get("status", ""),
                    confidence=entry.get("confidence", ""),
                    valid_range=html.escape(
                        str(entry.get("valid_range") or "-"), quote=False
                    ),
                )
            )
            lines.append(
                f"  title: {html.escape(str(entry.get('title') or ''), quote=False)}"
            )
            if entry.get("excerpt"):
                lines.append(_render_excerpt(entry["excerpt"]))
            if entry.get("conflict_ids"):
                lines.append("  conflict_ids: " + ", ".join(entry["conflict_ids"]))
            if entry.get("source_ref"):
                lines.append(
                    "  source_ref: "
                    + html.escape(str(entry["source_ref"]), quote=False)
                )
    else:
        lines.append("entries: []")

    constraints = context.get("constraints") or []
    if constraints:
        lines.append("constraints:")
        for item in constraints:
            lines.append(
                f"- [{item.get('kind')}] {item.get('entry_id')}: "
                f"{html.escape(str(item.get('text') or ''), quote=False)}"
            )
    gaps = context.get("gaps") or []
    if gaps:
        lines.append("gaps:")
        for gap in gaps:
            lines.append("- " + html.escape(str(gap), quote=False))
    conflicts = context.get("open_conflicts") or []
    if conflicts:
        lines.append("unresolved_conflicts:")
        for item in conflicts[:10]:
            lines.append("- {candidate_id} vs {canonical_id} ({status})".format(**item))
    tips = context.get("warnings") or []
    if tips:
        lines.append("warnings:")
        for tip in tips:
            lines.append("- " + html.escape(str(tip), quote=False))
    lines.extend(
        [
            "rules:",
            "- Canonical = curated scope/terminology, not proof of an empirical claim.",
            "- Candidate = provisional lead; do not present it as settled.",
            "- Contested canonical = unresolved counterexample; state the conflict.",
            "- Cite the entry ids you rely on where the response schema or report "
            "format allows; never add forbidden fields to a strict contract.",
            "</knowledge_context>",
        ]
    )
    rendered = "\n".join(lines)
    if len(rendered) > _MAX_RENDER_CHARS:
        rendered = (
            rendered[: _MAX_RENDER_CHARS - 40].rstrip()
            + "\n... [knowledge context truncated]\n</knowledge_context>"
        )
    return rendered


__all__ = [
    "CONTEXT_STAGES",
    "SCHEMA_VERSION",
    "STAGE_AGENT",
    "build_knowledge_context",
    "normalize_stage",
    "render_knowledge_context",
    "unavailable_context",
]
