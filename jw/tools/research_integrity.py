"""System-owned tools for claim evidence and truthful task finalization."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from io import BytesIO
from typing import Any, Literal

import httpx
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from markdownify import markdownify

from jw.research_integrity import (
    EvidenceReceipt,
    accepted_evidence_receipts,
    canonical_json_sha256,
    claim_text_sha256,
    finalize_task,
    sha256_file,
    write_json_atomic,
)
from jw.workspaces import resolve_scoped_path, workspace_root_from_config

from .registry import register_tool_bundle


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _safe_id(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-")
    if not normalized:
        raise ValueError("claim_id must contain at least one safe character")
    return normalized[:100]


def _contains_exact(value: object, target: str) -> bool:
    if isinstance(value, dict):
        return target in value or any(
            _contains_exact(item, target) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_exact(item, target) for item in value)
    return isinstance(value, str) and value == target


def _agent_name(config: RunnableConfig) -> str:
    if not isinstance(config, dict):
        return "main-agent"
    configurable = config.get("configurable") or {}
    metadata = config.get("metadata") or {}
    return str(
        metadata.get("agent_name")
        or metadata.get("langgraph_node")
        or configurable.get("agent_name")
        or "main-agent"
    )


@tool(parse_docstring=True)
async def fetch_evidence_source(
    source_id: str,
    source_url: str,
    source_class: Literal["official", "primary_research", "review", "secondary"],
    doi: str = "",
    config: RunnableConfig = None,
) -> str:
    """Fetch an external source into immutable task-scoped, hash-addressed storage.

    Args:
        source_id: Stable source identifier chosen during discovery.
        source_url: Direct official, article, or PDF URL.
        source_class: Provenance class used by evidence policy.
        doi: Optional DOI.

    Returns:
        Structured ToolOutcome with source artifact and fetch receipt.
    """

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=30.0,
            headers={"User-Agent": "JinwuResearchAudit/2.0"},
        ) as client:
            response = await client.get(source_url)
            response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        raw = response.content
        if "pdf" in content_type or source_url.lower().endswith(".pdf"):
            try:
                from pypdf import PdfReader
            except ImportError as exc:
                raise ValueError(
                    "PDF evidence requires the pypdf package in the locked environment"
                ) from exc
            reader = PdfReader(BytesIO(raw))
            text_content = "\n\n".join(
                f"## Page {index}\n\n{page.extract_text() or ''}"
                for index, page in enumerate(reader.pages, start=1)
            )
        elif "html" in content_type:
            text_content = markdownify(response.text)
        else:
            text_content = response.text
        normalized = text_content.replace("\r\n", "\n").strip() + "\n"
        if len(normalized) < 100:
            raise ValueError("fetched source contains too little auditable text")
        source_hash = canonical_json_sha256(
            {
                "final_url": str(response.url),
                "normalized_text": normalized,
            }
        )
        root = workspace_root_from_config(config)
        artifact_relative = f"work/evidence_sources/{source_hash}.md"
        artifact = root / artifact_relative
        artifact.parent.mkdir(parents=True, exist_ok=True)
        if artifact.exists() and artifact.read_text(encoding="utf-8") != normalized:
            raise ValueError("hash-addressed source collision")
        artifact.write_text(normalized, encoding="utf-8")
        receipt_relative = f"receipts/evidence/sources/{source_hash}.json"
        write_json_atomic(
            root / receipt_relative,
            {
                "schema_version": 2,
                "status": "fetched",
                "source_id": source_id.strip(),
                "source_url": source_url.strip(),
                "final_url": str(response.url),
                "doi": doi.strip() or None,
                "source_class": source_class,
                "content_type": content_type,
                "http_status": response.status_code,
                "raw_sha256": __import__("hashlib").sha256(raw).hexdigest(),
                "source_sha256": sha256_file(artifact),
                "source_path": artifact_relative,
                "fetched_at": datetime.now(UTC).isoformat(),
            },
        )
        return _json(
            {
                "schema_version": 1,
                "status": "success",
                "summary": "External source was fetched and hash-pinned.",
                "artifact_refs": [artifact_relative],
                "receipt_refs": [receipt_relative],
                "retryable": False,
            }
        )
    except Exception as exc:
        return _json(
            {
                "schema_version": 1,
                "status": "error",
                "summary": str(exc),
                "artifact_refs": [],
                "receipt_refs": [],
                "error_code": type(exc).__name__,
                "retryable": True,
            }
        )


@tool(parse_docstring=True)
def submit_evidence_receipt(
    source_id: str,
    source_path: str,
    source_url: str,
    source_class: Literal["official", "primary_research", "review", "secondary"],
    locator_type: Literal["section_paragraph", "page", "line", "table"],
    locator_value: str,
    evidence_span: str,
    claim_id: str,
    claim_text: str,
    relation: Literal["supports", "contradicts", "limits"],
    scope: str,
    confidence_limit: str,
    doi: str = "",
    config: RunnableConfig = None,
) -> str:
    """Submit grounded external evidence for independent review.

    Args:
        source_id: Stable literature or official-source identifier.
        source_path: Task path of the fetched/read source text.
        source_url: Canonical source URL or DOI URL.
        source_class: Official, primary research, review, or secondary.
        locator_type: Auditable locator type within the source.
        locator_value: Section/paragraph, page, line, or table locator.
        evidence_span: Exact text contained in the fetched source.
        claim_id: Stable claim identifier used by the report.
        claim_text: Exact reader-facing claim being supported or limited.
        relation: Whether the span supports, contradicts, or limits the claim.
        scope: What the source establishes and does not establish.
        confidence_limit: Maximum confidence justified by this source.
        doi: Optional DOI.

    Returns:
        Structured ToolOutcome containing an immutable pending submission.
    """

    try:
        source = resolve_scoped_path(source_path, config)
        if not source.is_file():
            raise ValueError("fetched source file does not exist")
        span = evidence_span.strip()
        if len(span) < 12:
            raise ValueError("evidence_span is too short to audit")
        source_text = source.read_text(encoding="utf-8", errors="replace")
        if span not in source_text:
            raise ValueError("evidence_span is not grounded in the fetched source")
        relative_source = source.relative_to(workspace_root_from_config(config)).as_posix()
        if not relative_source.startswith("work/evidence_sources/"):
            raise ValueError(
                "evidence must use a formally fetched work/evidence_sources artifact"
            )
        if not locator_value.strip():
            raise ValueError("locator_value is required")
        normalized_claim = " ".join(claim_text.split())
        if len(normalized_claim) < 8:
            raise ValueError("claim_text is too short to audit")
        root = workspace_root_from_config(config)
        source_hash = sha256_file(source)
        source_receipts = root / "receipts" / "evidence" / "sources"
        formally_fetched = False
        if source_receipts.is_dir():
            for fetch_receipt in source_receipts.glob("*.json"):
                try:
                    fetched = json.loads(fetch_receipt.read_text(encoding="utf-8"))
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    continue
                if (
                    fetched.get("schema_version") == 2
                    and fetched.get("status") == "fetched"
                    and fetched.get("source_path") == relative_source
                    and fetched.get("source_sha256") == source_hash
                ):
                    formally_fetched = True
                    break
        if not formally_fetched:
            raise ValueError("search or local text is not a formal fetched-source receipt")
        safe_claim = _safe_id(claim_id)
        claim_hash = claim_text_sha256(normalized_claim)
        receipt_id = "evidence_" + canonical_json_sha256(
            {
                "claim_sha256": claim_hash,
                "source_sha256": source_hash,
                "locator": [locator_type, locator_value.strip()],
                "evidence_span": span,
                "relation": relation,
            }
        )[:32]
        receipt_relative = (
            f"receipts/evidence/submissions/{safe_claim}/{receipt_id}.json"
        )
        receipt = EvidenceReceipt(
            receipt_id=receipt_id,
            claim_id=claim_id.strip(),
            claim_sha256=claim_hash,
            claim_text=normalized_claim,
            source_id=source_id.strip(),
            source_url=source_url.strip(),
            source_path=relative_source,
            source_class=source_class,
            source_sha256=source_hash,
            locator={"type": locator_type, "value": locator_value.strip()},
            evidence_span=span,
            relation=relation,
            scope=scope.strip(),
            confidence_limit=confidence_limit.strip(),
            submitted_by=_agent_name(config),
            doi=doi.strip() or None,
        )
        target = root / receipt_relative
        payload = receipt.to_dict()
        if target.exists():
            existing = json.loads(target.read_text(encoding="utf-8"))
            if existing != payload:
                raise ValueError("immutable evidence receipt collision")
        else:
            write_json_atomic(target, payload)
        return _json(
            {
                "schema_version": 1,
                "status": "success",
                "summary": f"Evidence for {claim_id} is pending independent review.",
                "artifact_refs": [relative_source],
                "receipt_refs": [receipt_relative],
                "retryable": False,
                "receipt_id": receipt_id,
                "submission_status": "pending",
            }
        )
    except Exception as exc:
        return _json(
            {
                "schema_version": 1,
                "status": "error",
                "summary": str(exc),
                "artifact_refs": [],
                "receipt_refs": [],
                "error_code": type(exc).__name__,
                "retryable": False,
            }
        )


@tool(parse_docstring=True)
def read_evidence_submission(
    submission_ref: str,
    context_chars: int = 1200,
    config: RunnableConfig = None,
) -> str:
    """Read one pending submission with hash-verified source context.

    Args:
        submission_ref: Task-relative v2 evidence submission path.
        context_chars: Maximum surrounding source characters on each side.

    Returns:
        Structured submission, exact span, locator, and bounded source context.
    """

    try:
        submission_path = resolve_scoped_path(submission_ref, config)
        root = workspace_root_from_config(config)
        relative = submission_path.relative_to(root).as_posix()
        if not relative.startswith("receipts/evidence/submissions/"):
            raise ValueError("read target must be a v2 evidence submission")
        submission = json.loads(submission_path.read_text(encoding="utf-8"))
        if (
            not isinstance(submission, dict)
            or submission.get("schema_version") != 2
            or submission.get("submission_status") != "pending"
        ):
            raise ValueError("legacy or malformed evidence cannot be reviewed")
        source = resolve_scoped_path(str(submission["source_path"]), config)
        if not source.is_file() or sha256_file(source) != submission.get(
            "source_sha256"
        ):
            raise ValueError("fetched source hash no longer matches submission")
        source_text = source.read_text(encoding="utf-8", errors="replace")
        span = str(submission.get("evidence_span", ""))
        index = source_text.find(span)
        if index < 0:
            raise ValueError("submitted evidence span is absent from source")
        limit = min(max(int(context_chars), 200), 4000)
        context = source_text[
            max(0, index - limit) : min(len(source_text), index + len(span) + limit)
        ]
        return _json(
            {
                "schema_version": 1,
                "status": "success",
                "summary": "Pending evidence and source context were hash-verified.",
                "artifact_refs": [relative, str(submission["source_path"])],
                "receipt_refs": [relative],
                "retryable": False,
                "submission": submission,
                "source_context": context,
            }
        )
    except Exception as exc:
        return _json(
            {
                "schema_version": 1,
                "status": "error",
                "summary": str(exc),
                "artifact_refs": [],
                "receipt_refs": [],
                "error_code": type(exc).__name__,
                "retryable": False,
            }
        )


@tool(parse_docstring=True)
def review_evidence_receipt(
    submission_ref: str,
    review_status: Literal["accepted", "rejected"],
    claim_relation_valid: bool,
    source_class_valid: bool,
    scope_valid: bool,
    review_notes: str,
    config: RunnableConfig = None,
) -> str:
    """Independently accept or reject one immutable evidence submission.

    Args:
        submission_ref: Task-relative v2 evidence submission path.
        review_status: Accepted or rejected after semantic review.
        claim_relation_valid: Whether the quoted span entails the declared relation.
        source_class_valid: Whether provenance meets the claim requirement.
        scope_valid: Whether scope and confidence avoid overclaiming.
        review_notes: Concrete audit reasoning.

    Returns:
        Structured ToolOutcome containing an immutable review receipt.
    """

    try:
        root = workspace_root_from_config(config)
        submission_path = resolve_scoped_path(submission_ref, config)
        relative = submission_path.relative_to(root).as_posix()
        if not relative.startswith("receipts/evidence/submissions/"):
            raise ValueError("review target must be a v2 evidence submission")
        submission = json.loads(submission_path.read_text(encoding="utf-8"))
        if (
            not isinstance(submission, dict)
            or submission.get("schema_version") != 2
            or submission.get("submission_status") != "pending"
        ):
            raise ValueError("legacy or malformed evidence cannot be reviewed")
        source = resolve_scoped_path(str(submission["source_path"]), config)
        source_hash_verified = (
            source.is_file()
            and sha256_file(source) == submission.get("source_sha256")
        )
        span_grounded = source_hash_verified and str(
            submission.get("evidence_span", "")
        ) in source.read_text(encoding="utf-8", errors="replace")
        semantic_checks = (
            claim_relation_valid and source_class_valid and scope_valid
        )
        effective = (
            "accepted"
            if review_status == "accepted"
            and source_hash_verified
            and span_grounded
            and semantic_checks
            else "rejected"
        )
        notes = review_notes.strip()
        if len(notes) < 12:
            raise ValueError("review_notes must explain the evidence decision")
        receipt_id = str(submission["receipt_id"])
        review_relative = f"receipts/evidence/reviews/{receipt_id}.json"
        review = {
            "schema_version": 2,
            "receipt_id": receipt_id,
            "review_status": effective,
            "reviewed_by": "solar-evidence",
            "source_sha256": submission["source_sha256"],
            "claim_sha256": submission["claim_sha256"],
            "source_hash_verified": source_hash_verified,
            "span_grounded": span_grounded,
            "claim_relation_valid": claim_relation_valid,
            "source_class_valid": source_class_valid,
            "scope_valid": scope_valid,
            "review_notes": notes,
            "reviewed_at": datetime.now(UTC).isoformat(),
        }
        target = root / review_relative
        if target.exists():
            existing = json.loads(target.read_text(encoding="utf-8"))
            stable_existing = {
                key: value for key, value in existing.items() if key != "reviewed_at"
            }
            stable_review = {
                key: value for key, value in review.items() if key != "reviewed_at"
            }
            if stable_existing != stable_review:
                raise ValueError("evidence review is immutable once recorded")
        else:
            write_json_atomic(target, review)
        return _json(
            {
                "schema_version": 1,
                "status": "success" if effective == "accepted" else "blocked",
                "summary": f"Evidence review completed: {effective}.",
                "artifact_refs": [],
                "receipt_refs": [review_relative],
                "retryable": effective != "accepted",
                "review_status": effective,
            }
        )
    except Exception as exc:
        return _json(
            {
                "schema_version": 1,
                "status": "error",
                "summary": str(exc),
                "artifact_refs": [],
                "receipt_refs": [],
                "error_code": type(exc).__name__,
                "retryable": False,
            }
        )


@tool(parse_docstring=True)
def record_counterevidence_search(
    claim_id: str,
    claim_text: str,
    queries: list[str],
    providers: list[str],
    outcome: Literal["no_candidate_found", "candidates_bound"],
    candidate_evidence_ids: list[str],
    limitations: str,
    config: RunnableConfig = None,
) -> str:
    """Record counterevidence coverage without treating search metadata as support.

    Args:
        claim_id: Claim whose counterevidence was searched.
        claim_text: Exact claim text.
        queries: Non-empty counter-hypothesis search queries.
        providers: Search providers actually queried.
        outcome: Whether candidates were absent or bound as evidence.
        candidate_evidence_ids: Bound contradicting/limiting evidence ids, if found.
        limitations: Coverage and access limitations.

    Returns:
        Structured ToolOutcome with a process-only coverage receipt.
    """

    try:
        if not queries or not providers:
            raise ValueError("counterevidence queries and providers are required")
        if outcome == "candidates_bound" and not candidate_evidence_ids:
            raise ValueError("candidate evidence ids are required when candidates exist")
        root = workspace_root_from_config(config)
        normalized_claim = " ".join(claim_text.split())
        claim_hash = claim_text_sha256(normalized_claim)
        safe_claim = _safe_id(claim_id)
        relative = f"receipts/evidence/coverage/{safe_claim}.json"
        write_json_atomic(
            root / relative,
            {
                "schema_version": 2,
                "status": "complete",
                "claim_id": claim_id.strip(),
                "claim_sha256": claim_hash,
                "queries": [str(query).strip() for query in queries if str(query).strip()],
                "providers": [
                    str(provider).strip()
                    for provider in providers
                    if str(provider).strip()
                ],
                "outcome": outcome,
                "candidate_evidence_ids": candidate_evidence_ids,
                "limitations": limitations.strip(),
                "recorded_at": datetime.now(UTC).isoformat(),
            },
        )
        return _json(
            {
                "schema_version": 1,
                "status": "success",
                "summary": "Counterevidence coverage was recorded; it is not support.",
                "artifact_refs": [],
                "receipt_refs": [relative],
                "retryable": False,
            }
        )
    except Exception as exc:
        return _json(
            {
                "schema_version": 1,
                "status": "error",
                "summary": str(exc),
                "artifact_refs": [],
                "receipt_refs": [],
                "error_code": type(exc).__name__,
                "retryable": False,
            }
        )


@tool(parse_docstring=True)
def finalize_research_task(
    requested_status: Literal["finalized", "partial", "blocked", "error"],
    required_receipts: list[str],
    summary: str,
    config: RunnableConfig = None,
) -> str:
    """Apply the final report/receipt gate and persist the truthful task state.

    Args:
        requested_status: Desired terminal status.
        required_receipts: Exact task-relative receipt paths required by route.
        summary: Concise user-facing status explanation.

    Returns:
        Structured ToolOutcome with the effective persisted task status.
    """

    try:
        root = workspace_root_from_config(config)
        task = finalize_task(
            root,
            requested_status=requested_status,
            required_receipts=tuple(required_receipts),
            summary=summary,
        )
        effective = str(task["status"])
        outcome = "success" if effective == "finalized" else (
            "partial" if effective == "partial" else (
                "blocked" if effective == "blocked" else "error"
            )
        )
        return _json(
            {
                "schema_version": 1,
                "status": outcome,
                "summary": summary,
                "artifact_refs": (
                    [task["final_report"]] if task.get("final_report") else []
                ),
                "receipt_refs": list(task.get("verified_receipts", [])),
                "missing_receipts": list(task.get("missing_receipts", [])),
                "retryable": effective in {"partial", "blocked"},
                "task_status": effective,
            }
        )
    except Exception as exc:
        return _json(
            {
                "schema_version": 1,
                "status": "error",
                "summary": str(exc),
                "artifact_refs": [],
                "receipt_refs": [],
                "error_code": type(exc).__name__,
                "retryable": False,
            }
        )


@tool(parse_docstring=True)
def validate_research_claims(
    claims: list[dict[str, Any]],
    config: RunnableConfig = None,
) -> str:
    """Validate every reader-facing claim against evidence or measurements.

    Args:
        claims: Claim objects with ``claim_id``, ``kind`` and ``text``.
            Quantitative claims also require ``measurement_id``. Historical
            claims require ``evidence_receipt_refs``. Interpretations require
            non-empty ``support_refs`` and ``limitations``.

    Returns:
        Structured ToolOutcome with a verified claim-ledger receipt.
    """

    try:
        if not claims:
            raise ValueError("at least one reader-facing claim is required")
        root = workspace_root_from_config(config)
        accepted_evidence = accepted_evidence_receipts(root)
        task_path = root / "task.json"
        task = (
            json.loads(task_path.read_text(encoding="utf-8"))
            if task_path.is_file()
            else {}
        )
        experiment_receipts = list((root / "receipts" / "experiments").glob("*.json"))
        experiment_records: list[object] = []
        for receipt_path in experiment_receipts:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            record_ref = receipt.get("experiment_record")
            if isinstance(record_ref, str):
                record_path = resolve_scoped_path(record_ref, config)
                experiment_records.append(
                    json.loads(record_path.read_text(encoding="utf-8"))
                )

        normalized: list[dict[str, Any]] = []
        issues: list[str] = []
        seen: set[str] = set()
        for index, claim in enumerate(claims):
            claim_id = str(claim.get("claim_id", "")).strip()
            kind = str(claim.get("kind", "")).strip()
            text = str(claim.get("text", "")).strip()
            label = claim_id or f"claim[{index}]"
            if not claim_id or claim_id in seen:
                issues.append(f"{label}: claim_id is missing or duplicated")
                continue
            seen.add(claim_id)
            if not text:
                issues.append(f"{label}: text is required")
            claim_hash = claim_text_sha256(text)
            if kind == "quantitative":
                measurement_id = str(claim.get("measurement_id", "")).strip()
                if not measurement_id or not any(
                    _contains_exact(record, measurement_id)
                    for record in experiment_records
                ):
                    issues.append(
                        f"{label}: measurement_id is absent from finalized records"
                    )
            elif kind in {"historical", "causal_attribution"}:
                evidence_ids = claim.get("evidence_ids", [])
                matched = [
                    accepted_evidence[evidence_id]
                    for evidence_id in evidence_ids
                    if isinstance(evidence_id, str)
                    and evidence_id in accepted_evidence
                    and accepted_evidence[evidence_id].get("claim_id") == claim_id
                    and accepted_evidence[evidence_id].get("claim_sha256") == claim_hash
                ]
                supports = [
                    evidence for evidence in matched
                    if evidence.get("relation") == "supports"
                ]
                contradictions = [
                    evidence for evidence in matched
                    if evidence.get("relation") == "contradicts"
                ]
                limits = [
                    evidence for evidence in matched
                    if evidence.get("relation") == "limits"
                ]
                if not supports:
                    issues.append(
                        f"{label}: an accepted, claim-matched supporting source is required"
                    )
                if kind == "causal_attribution":
                    if supports and not any(
                        evidence.get("source_class") in {"official", "primary_research"}
                        for evidence in supports
                    ):
                        issues.append(
                            f"{label}: causal attribution needs official or primary evidence"
                        )
                    coverage_ref = str(
                        claim.get("counterevidence_coverage_ref", "")
                    ).strip()
                    try:
                        coverage_path = resolve_scoped_path(coverage_ref, config)
                        coverage = json.loads(
                            coverage_path.read_text(encoding="utf-8")
                        )
                    except (OSError, ValueError, TypeError, json.JSONDecodeError):
                        coverage = {}
                    if (
                        not isinstance(coverage, dict)
                        or coverage.get("schema_version") != 2
                        or coverage.get("status") != "complete"
                        or coverage.get("claim_id") != claim_id
                        or coverage.get("claim_sha256") != claim_hash
                    ):
                        issues.append(
                            f"{label}: matching counterevidence coverage is required"
                        )
                    if not str(claim.get("synthesis", "")).strip():
                        issues.append(f"{label}: synthesis is required")
                    if contradictions or limits:
                        synthesis = str(claim.get("synthesis", ""))
                        referenced = {
                            str(evidence["receipt_id"])
                            for evidence in (*contradictions, *limits)
                        }
                        if not any(ref in synthesis for ref in referenced):
                            issues.append(
                                f"{label}: synthesis must address contradictions and limits"
                            )
            elif kind == "interpretation":
                if not claim.get("support_refs"):
                    issues.append(f"{label}: support_refs are required")
                if not str(claim.get("limitations", "")).strip():
                    issues.append(f"{label}: limitations are required")
            else:
                issues.append(f"{label}: unsupported claim kind {kind!r}")
            normalized.append(dict(claim))

        supplied_ids = {
            str(claim.get("claim_id", ""))
            for claim in claims
            if isinstance(claim, dict)
        }
        for required_claim in task.get("required_evidence_claims", []):
            if str(required_claim) not in supplied_ids:
                issues.append(
                    f"{required_claim}: mandatory domain claim is absent from ledger"
                )

        if issues:
            return _json(
                {
                    "schema_version": 1,
                    "status": "blocked",
                    "summary": "Reader-facing claims are not fully traceable.",
                    "artifact_refs": [],
                    "receipt_refs": [],
                    "issues": issues,
                    "retryable": True,
                }
            )

        receipt_relative = "receipts/claims/claims-v2.json"
        write_json_atomic(
            root / receipt_relative,
            {
                "schema_version": 2,
                "status": "accepted",
                "claims": normalized,
            },
        )
        return _json(
            {
                "schema_version": 1,
                "status": "success",
                "summary": f"Validated {len(normalized)} reader-facing claims.",
                "artifact_refs": [],
                "receipt_refs": [receipt_relative],
                "retryable": False,
            }
        )
    except Exception as exc:
        return _json(
            {
                "schema_version": 1,
                "status": "error",
                "summary": str(exc),
                "artifact_refs": [],
                "receipt_refs": [],
                "error_code": type(exc).__name__,
                "retryable": False,
            }
        )


RESEARCH_INTEGRITY_TOOLS = [
    fetch_evidence_source,
    submit_evidence_receipt,
    record_counterevidence_search,
    validate_research_claims,
    finalize_research_task,
]
RESEARCH_EVIDENCE_REVIEW_TOOLS = [
    read_evidence_submission,
    review_evidence_receipt,
]
register_tool_bundle("research-integrity", RESEARCH_INTEGRITY_TOOLS)
register_tool_bundle(
    "research-evidence-review",
    RESEARCH_EVIDENCE_REVIEW_TOOLS,
    include_in_main=False,
)

__all__ = ["RESEARCH_INTEGRITY_TOOLS", "RESEARCH_EVIDENCE_REVIEW_TOOLS"] + [
    tool.name
    for tool in (*RESEARCH_INTEGRITY_TOOLS, *RESEARCH_EVIDENCE_REVIEW_TOOLS)
]
