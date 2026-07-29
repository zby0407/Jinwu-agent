"""Machine-verifiable contracts for research tools, evidence, and task state.

This module deliberately contains no model-facing heuristics.  It is the
deterministic boundary between conversational tool output and claims that may
be presented as audited research.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

OutcomeStatus = Literal["success", "partial", "blocked", "error"]
TaskStatus = Literal[
    "created",
    "routed",
    "inputs_bound",
    "running",
    "verifying",
    "finalized",
    "partial",
    "blocked",
    "error",
    "cancelled",
]

_SUCCESS_VALUES = {"ok", "success", "succeeded", "completed", "finalized", "verified"}
_PARTIAL_VALUES = {"partial", "incomplete"}
_BLOCKED_VALUES = {
    "blocked",
    "boundary_blocked",
    "required_missing",
    "policy_blocked",
}
_ERROR_VALUES = {"error", "failed", "failure", "invalid", "design_invalid"}
_TERMINAL_TASK_STATUSES = {"finalized", "partial", "blocked", "error", "cancelled"}
_SHA256_PATTERN = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", re.IGNORECASE)

F107_DISCONTINUITY_REQUIRED_MEASUREMENTS: tuple[str, ...] = (
    "f107_full_period_relation",
    "f107_pre_1980_relation",
    "f107_post_1980_relation",
    "f107_fixed_1980_chow_f",
    "f107_scan_best_break_year",
    "f107_relative_scale_jump",
    "f107_pre_model_predicts_post_mean_residual",
    "f107_pre_model_predicts_post_positive_fraction",
    "f107_post_model_predicts_pre_mean_residual",
    "f107_post_model_predicts_pre_positive_fraction",
    "f107_low_activity_sensitivity",
    "f107_month_coverage_sensitivity",
)


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    schema_version: int = 1
    status: OutcomeStatus = "success"
    summary: str = ""
    artifact_refs: tuple[str, ...] = ()
    receipt_refs: tuple[str, ...] = ()
    error_code: str | None = None
    retryable: bool = False

    @property
    def succeeded(self) -> bool:
        return self.status == "success"

    @property
    def has_verified_receipt(self) -> bool:
        """Compatibility alias: only indicates that receipt references were returned.

        A ToolOutcome cannot verify files in the task workspace. Callers that
        advance a research stage must intersect these references with
        :func:`verified_receipt_paths`.
        """

        return self.succeeded and bool(self.receipt_refs)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["artifact_refs"] = list(self.artifact_refs)
        value["receipt_refs"] = list(self.receipt_refs)
        return value


@dataclass(frozen=True, slots=True)
class DatasetSemanticManifest:
    manifest_id: str
    input_path: str
    input_sha256: str
    adapter_id: str
    adapter_version: str
    product_id: str
    product_version: str
    column_bindings: Mapping[str, str]
    unit: str
    observation_grain: str
    time_column: str
    primary_key: tuple[str, ...]
    duplicate_policy: str
    missing_policy: str
    quality_policy: str
    aggregation_plan: tuple[str, ...]
    coverage_start: str
    coverage_end: str
    diagnostics: Mapping[str, Any] = field(default_factory=dict)
    excluded_inputs: tuple[Mapping[str, str], ...] = ()
    limitations: tuple[str, ...] = ()
    analysis_requirements: tuple[str, ...] = ()
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["column_bindings"] = dict(self.column_bindings)
        value["primary_key"] = list(self.primary_key)
        value["aggregation_plan"] = list(self.aggregation_plan)
        value["diagnostics"] = dict(self.diagnostics)
        value["excluded_inputs"] = [dict(row) for row in self.excluded_inputs]
        value["limitations"] = list(self.limitations)
        value["analysis_requirements"] = list(self.analysis_requirements)
        return value


@dataclass(frozen=True, slots=True)
class EvidenceReceipt:
    receipt_id: str
    claim_id: str
    claim_sha256: str
    claim_text: str
    source_id: str
    source_url: str
    source_path: str
    source_class: Literal["official", "primary_research", "review", "secondary"]
    source_sha256: str
    locator: Mapping[str, str]
    evidence_span: str
    relation: Literal["supports", "contradicts", "limits"]
    scope: str
    confidence_limit: str
    submitted_by: str
    submission_status: Literal["pending"] = "pending"
    doi: str | None = None
    schema_version: int = 2

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["locator"] = dict(self.locator)
        return value


_EXPLICIT_EVIDENCE_PATTERN = re.compile(
    r"(?:查阅|检索|引用|阅读).{0,12}(?:文献|论文|原始研究|数据说明|观测史)"
    r"|(?:文献|论文|原始研究|数据说明|观测史).{0,12}(?:查阅|检索|引用|阅读)"
    r"|(?:literature|primary\s+(?:study|research)|data\s+documentation|"
    r"observation\s+history|source\s+documentation)",
    re.IGNORECASE,
)
_CAUSAL_PATTERN = re.compile(
    r"(?:归因|原因|因果|机制|解释.{0,12}(?:变化|漂移|断点)|"
    r"仪器|校准|团队|处理流程|caus(?:e|al|ation)|attribut(?:e|ion)|"
    r"mechanism|calibration|instrument|processing\s+pipeline)",
    re.IGNORECASE,
)
_COMPETING_PATTERN = re.compile(
    r"(?:比较|对比|区分|排除|权衡).{0,40}(?:假说|假设|解释|原因|机制)"
    r"|(?:competing|compare|distinguish|weigh).{0,40}"
    r"(?:hypotheses|explanations|causes|mechanisms)",
    re.IGNORECASE,
)
_HISTORICAL_PATTERN = re.compile(
    r"(?:观测站|机构|迁移|迁站|团队变更|仪器更换|历史事件|"
    r"observatory|institution|relocat(?:e|ion)|team\s+change|"
    r"instrument\s+(?:change|replacement)|historical\s+event)",
    re.IGNORECASE,
)

_DOMAIN_EVIDENCE_REQUIREMENTS: dict[str, tuple[dict[str, Any], ...]] = {
    "f107": (
        {
            "claim_id": "f107_product_definition",
            "required_source_classes": ["official"],
            "minimum_supports": 1,
            "requires_counterevidence_search": False,
        },
        {
            "claim_id": "f107_observatory_history",
            "required_source_classes": ["official", "primary_research"],
            "minimum_supports": 1,
            "requires_counterevidence_search": True,
        },
        {
            "claim_id": "f107_1980_discontinuity",
            "required_source_classes": ["primary_research"],
            "minimum_supports": 1,
            "requires_counterevidence_search": True,
        },
    )
}


def canonical_json_sha256(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def claim_text_sha256(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


def derive_external_evidence_policy(
    text: str,
    route: Mapping[str, Any],
    *,
    required_domain_adapter: str,
    deliverable: str,
) -> dict[str, Any]:
    """Derive evidence duties deterministically; model routing may only add them."""

    reasons: list[str] = []
    if route.get("source_mode") in {"external", "mixed"}:
        reasons.append("source_mode")
    if _EXPLICIT_EVIDENCE_PATTERN.search(text):
        reasons.append("explicit_literature_request")
    causal = bool(_CAUSAL_PATTERN.search(text))
    if causal and deliverable == "audited_report":
        reasons.append("causal_attribution")
    if _COMPETING_PATTERN.search(text):
        reasons.append("competing_hypotheses")
    if _HISTORICAL_PATTERN.search(text):
        reasons.append("historical_fact")
    requirements = [
        dict(row)
        for row in _DOMAIN_EVIDENCE_REQUIREMENTS.get(required_domain_adapter, ())
    ]
    if requirements:
        reasons.append("domain_mandatory_claim")
    return {
        "requires_external_evidence": bool(reasons),
        "external_evidence_reasons": list(dict.fromkeys(reasons)),
        "required_evidence_claims": requirements,
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if str(item).strip())


def _status_from_payload(payload: Mapping[str, Any]) -> OutcomeStatus:
    raw = payload.get("outcome", payload.get("status", ""))
    normalized = str(raw).strip().lower()
    if normalized in _SUCCESS_VALUES:
        return "success"
    if normalized in _PARTIAL_VALUES:
        return "partial"
    if normalized in _BLOCKED_VALUES:
        return "blocked"
    if normalized in _ERROR_VALUES or payload.get("error"):
        return "error"
    # Structured payloads with an unknown explicit status must not silently
    # become successful research evidence.
    if normalized:
        return "error"
    return "success"


def normalize_tool_outcome(
    content: object,
    *,
    transport_status: object = None,
    allow_plain_success: bool = False,
) -> ToolOutcome:
    """Normalize a ToolMessage payload without trusting transport success.

    Plain text is successful only for explicitly allow-listed read-only tools.
    Specialist and computation stages therefore need a structured outcome.
    """

    if str(transport_status).lower() == "error":
        return ToolOutcome(status="error", summary=str(content)[:500])

    payload: Mapping[str, Any] | None = None
    if isinstance(content, Mapping):
        payload = content
    elif isinstance(content, str):
        try:
            decoded = json.loads(content)
        except (TypeError, ValueError, json.JSONDecodeError):
            decoded = None
        if isinstance(decoded, Mapping):
            payload = decoded

    if payload is None:
        return ToolOutcome(
            status="success" if allow_plain_success else "error",
            summary=str(content)[:500],
            error_code=None if allow_plain_success else "unstructured_tool_result",
        )

    status = _status_from_payload(payload)
    summary = str(
        payload.get("summary")
        or payload.get("message")
        or payload.get("reason")
        or payload.get("error")
        or ""
    )[:1000]
    return ToolOutcome(
        status=status,
        summary=summary,
        artifact_refs=_string_tuple(
            payload.get("artifact_refs", payload.get("artifacts", ()))
        ),
        receipt_refs=_string_tuple(
            payload.get("receipt_refs", payload.get("receipts", ()))
        ),
        error_code=(
            str(payload.get("error_code"))
            if payload.get("error_code") is not None
            else None
        ),
        retryable=bool(payload.get("retryable", False)),
    )


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _receipt_artifacts_match(
    run_root: Path,
    ref: str,
    payload: Mapping[str, Any],
) -> bool:
    """Validate hashes for receipt kinds that publish immutable artifacts."""

    checks: tuple[tuple[str, str], ...] = ()
    if ref.startswith("receipts/datasets/"):
        if payload.get("schema_version") != 1:
            return False
        artifact = str(payload.get("canonical_artifact", "")).strip()
        digest = str(payload.get("canonical_sha256", "")).strip()
        if not artifact or not digest:
            return False
        artifact_ref = artifact if "/" in artifact else f"work/{artifact}"
        checks = ((artifact_ref, digest),)
    elif ref.startswith("receipts/experiments/"):
        if (
            payload.get("schema_version") != 1
            or not str(payload.get("run_id", "")).strip()
        ):
            return False
        checks = (
            (
                str(payload.get("experiment_report", "")),
                str(payload.get("experiment_report_sha256", "")),
            ),
            (
                str(payload.get("experiment_audit", "")),
                str(payload.get("experiment_audit_sha256", "")),
            ),
            (
                str(payload.get("experiment_record", "")),
                str(payload.get("experiment_record_sha256", "")),
            ),
            (
                str(payload.get("output_report", "")),
                str(payload.get("output_report_sha256", "")),
            ),
        )
    elif ref == "receipts/claims/claims-v2.json":
        return (
            payload.get("schema_version") == 2
            and payload.get("status") == "accepted"
            and isinstance(payload.get("claims"), list)
        )
    if not checks:
        return True
    for artifact_ref, expected_sha in checks:
        if not artifact_ref or not re.fullmatch(
            r"[0-9a-f]{64}", expected_sha, re.IGNORECASE
        ):
            return False
        artifact_path = (run_root / artifact_ref).resolve()
        try:
            artifact_path.relative_to(run_root.resolve())
        except ValueError:
            return False
        if not artifact_path.is_file() or sha256_file(artifact_path) != expected_sha:
            return False
    return True


def verified_receipt_paths(run_root: Path) -> set[str]:
    receipts_root = run_root / "receipts"
    if not receipts_root.is_dir():
        return set()
    verified: set[str] = set()
    for path in receipts_root.rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, Mapping):
            continue
        status = str(payload.get("review_status", payload.get("status", ""))).lower()
        if status not in {"accepted", "verified", "finalized", "success", "ok"}:
            continue
        if (ref := path.relative_to(run_root).as_posix()).startswith(
            "receipts/evidence/"
        ) and payload.get("schema_version") != 2:
            continue
        if not _receipt_artifacts_match(run_root, ref, payload):
            continue
        verified.add(ref)
    return verified


def _contains_exact(value: object, target: str) -> bool:
    if isinstance(value, Mapping):
        return any(
            key == target or _contains_exact(item, target)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_exact(item, target) for item in value)
    return value == target


def _known_sha256_values(
    run_root: Path,
    verified_receipts: set[str],
) -> set[str]:
    """Collect hashes only from verified receipts and hash-bound records."""

    known: set[str] = set()
    trusted_json_paths = {run_root / ref for ref in verified_receipts}
    for ref in verified_receipts:
        if not ref.startswith("receipts/experiments/"):
            continue
        try:
            receipt = json.loads((run_root / ref).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        record_ref = str(receipt.get("experiment_record", ""))
        if record_ref:
            trusted_json_paths.add(run_root / record_ref)

    for path in trusted_json_paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue

        def collect(value: object) -> None:
            if isinstance(value, Mapping):
                for item in value.values():
                    collect(item)
            elif isinstance(value, list):
                for item in value:
                    collect(item)
            elif isinstance(value, str) and re.fullmatch(
                r"[0-9a-f]{64}", value, re.IGNORECASE
            ):
                known.add(value.lower())

        collect(payload)
    return known


def accepted_evidence_receipts(run_root: Path) -> dict[str, dict[str, Any]]:
    """Return immutable v2 submissions that have a matching accepted review."""

    submissions_root = run_root / "receipts" / "evidence" / "submissions"
    reviews_root = run_root / "receipts" / "evidence" / "reviews"
    accepted: dict[str, dict[str, Any]] = {}
    if not submissions_root.is_dir() or not reviews_root.is_dir():
        return accepted
    for path in submissions_root.rglob("*.json"):
        try:
            submission = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if not isinstance(submission, dict) or submission.get("schema_version") != 2:
            continue
        receipt_id = str(submission.get("receipt_id", ""))
        review_path = reviews_root / f"{receipt_id}.json"
        try:
            review = json.loads(review_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if (
            isinstance(review, dict)
            and review.get("schema_version") == 2
            and review.get("receipt_id") == receipt_id
            and review.get("review_status") == "accepted"
            and review.get("reviewed_by") == "solar-evidence"
            and review.get("source_sha256") == submission.get("source_sha256")
            and review.get("claim_sha256") == submission.get("claim_sha256")
        ):
            accepted[receipt_id] = submission
    return accepted


def record_task_route(run_root: Path, route: Mapping[str, Any]) -> dict[str, Any]:
    """Persist route obligations so the finalizer cannot trust model arguments."""

    task_path = run_root / "task.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    if not isinstance(task, dict):
        raise ValueError("task.json must contain an object")
    obligations = {
        key: route.get(key)
        for key in (
            "mode",
            "source_mode",
            "needs_computation",
            "requires_dataset_semantics",
            "requires_computation_receipt",
            "requires_external_evidence",
            "required_domain_adapter",
            "required_analysis_protocol",
            "deliverable",
            "external_evidence_reasons",
        )
    }
    required_kinds: list[str] = []
    if obligations["requires_dataset_semantics"] is True:
        required_kinds.append("dataset")
    if obligations["requires_external_evidence"] is True:
        required_kinds.append("evidence")
    if obligations["requires_computation_receipt"] is True:
        required_kinds.append("experiment")
        required_kinds.append("claims")
    task["research_obligations"] = obligations
    task["required_receipt_kinds"] = required_kinds
    task["evidence_schema_version"] = 2
    route_requirements = route.get("required_evidence_claims", [])
    if not route_requirements and obligations["requires_external_evidence"] is True:
        route_requirements = _DOMAIN_EVIDENCE_REQUIREMENTS.get(
            str(obligations["required_domain_adapter"]), ()
        )
    task["required_evidence_claims"] = [
        str(row["claim_id"])
        for row in route_requirements
        if isinstance(row, Mapping) and row.get("claim_id")
    ]
    task["evidence_requirements"] = [
        dict(row) for row in route_requirements if isinstance(row, Mapping)
    ]
    task["required_measurement_ids"] = (
        list(F107_DISCONTINUITY_REQUIRED_MEASUREMENTS)
        if obligations["required_analysis_protocol"] == "f107_discontinuity_v1"
        else []
    )
    if task.get("status") == "created":
        task["status"] = "routed"
    write_json_atomic(task_path, task)
    return task


def transition_task(
    run_root: Path, status: TaskStatus, *, summary: str = ""
) -> dict[str, Any]:
    """Advance a non-terminal task without allowing terminal-state rewrites."""

    task_path = run_root / "task.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    if not isinstance(task, dict):
        raise ValueError("task.json must contain an object")
    current = str(task.get("status", "created"))
    if current in _TERMINAL_TASK_STATUSES:
        return task
    order = {
        "created": 0,
        "routed": 1,
        "inputs_bound": 2,
        "running": 3,
        "verifying": 4,
    }
    if status not in order:
        raise ValueError(f"{status} is not a non-terminal task phase")
    if order[status] < order.get(current, 0):
        return task
    task["status"] = status
    if summary:
        task["status_summary"] = summary
    write_json_atomic(task_path, task)
    return task


def finalize_task(
    run_root: Path,
    *,
    requested_status: Literal["finalized", "partial", "blocked", "error"],
    required_receipts: tuple[str, ...] = (),
    summary: str = "",
) -> dict[str, Any]:
    """Apply the task terminal-state gate and atomically persist the result."""

    task_path = run_root / "task.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    if not isinstance(task, dict):
        raise ValueError("task.json must contain an object")
    current = str(task.get("status", "created"))
    if current in _TERMINAL_TASK_STATUSES and current != requested_status:
        raise ValueError(f"terminal task status cannot transition: {current}")

    verified = verified_receipt_paths(run_root)
    missing = sorted(set(required_receipts) - verified)
    kind_prefixes = {
        "dataset": "receipts/datasets/",
        "evidence": "receipts/evidence/",
        "experiment": "receipts/experiments/",
        "claims": "receipts/claims/",
    }
    for kind in task.get("required_receipt_kinds", []):
        prefix = kind_prefixes.get(str(kind))
        if prefix and not any(ref.startswith(prefix) for ref in verified):
            missing.append(prefix + "*.json")
    accepted_evidence = accepted_evidence_receipts(run_root)
    bound_evidence_claims = {
        str(evidence["claim_id"])
        for evidence in accepted_evidence.values()
        if evidence.get("claim_id")
    }
    for claim_id in task.get("required_evidence_claims", []):
        if str(claim_id) not in bound_evidence_claims:
            missing.append(f"evidence:{claim_id}")
    for requirement in task.get("evidence_requirements", []):
        if not isinstance(requirement, Mapping):
            continue
        claim_id = str(requirement.get("claim_id", ""))
        allowed_classes = {
            str(value) for value in requirement.get("required_source_classes", [])
        }
        minimum = int(requirement.get("minimum_supports", 1))
        supports = [
            evidence
            for evidence in accepted_evidence.values()
            if evidence.get("claim_id") == claim_id
            and evidence.get("relation") == "supports"
            and (
                not allowed_classes
                or str(evidence.get("source_class")) in allowed_classes
            )
        ]
        if len(supports) < minimum:
            missing.append(f"evidence_support:{claim_id}")
        if requirement.get("requires_counterevidence_search") is True:
            safe_claim = re.sub(r"[^A-Za-z0-9_.-]+", "-", claim_id).strip("-")
            coverage_path = (
                run_root / "receipts" / "evidence" / "coverage" / f"{safe_claim}.json"
            )
            try:
                coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                coverage = {}
            if (
                not isinstance(coverage, Mapping)
                or coverage.get("schema_version") != 2
                or coverage.get("status") != "complete"
                or coverage.get("claim_id") != claim_id
            ):
                missing.append(f"counterevidence:{claim_id}")
    required_claim_ids = {
        str(value) for value in task.get("required_evidence_claims", [])
    }
    ledger_claim_ids: set[str] = set()
    claims_path = run_root / "receipts" / "claims" / "claims-v2.json"
    try:
        claims_payload = json.loads(claims_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        claims_payload = {}
    if (
        isinstance(claims_payload, Mapping)
        and claims_payload.get("schema_version") == 2
        and claims_payload.get("status") == "accepted"
    ):
        ledger_claim_ids = {
            str(claim.get("claim_id"))
            for claim in claims_payload.get("claims", [])
            if isinstance(claim, Mapping) and claim.get("claim_id")
        }
    for claim_id in required_claim_ids - ledger_claim_ids:
        missing.append(f"claim_ledger:{claim_id}")

    experiment_records: list[object] = []
    for receipt_ref in verified:
        if not receipt_ref.startswith("receipts/experiments/"):
            continue
        try:
            receipt = json.loads((run_root / receipt_ref).read_text(encoding="utf-8"))
            record_ref = str(receipt.get("experiment_record", ""))
            record_path = (run_root / record_ref).resolve()
            record_path.relative_to(run_root.resolve())
            experiment_records.append(
                json.loads(record_path.read_text(encoding="utf-8"))
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    for measurement_id in task.get("required_measurement_ids", []):
        if not any(
            _contains_exact(record, str(measurement_id))
            for record in experiment_records
        ):
            missing.append(f"measurement:{measurement_id}")

    report = run_root / "outputs" / "report.md"
    if report.is_file():
        try:
            report_text = report.read_text(encoding="utf-8")
        except OSError:
            report_text = ""
        known_hashes = _known_sha256_values(run_root, verified)
        for digest in _SHA256_PATTERN.findall(report_text):
            if digest.lower() not in known_hashes:
                missing.append(f"unverified_report_sha256:{digest.lower()}")

    missing = sorted(set(missing))
    effective = requested_status
    if requested_status == "finalized" and (not report.is_file() or missing):
        effective = "partial" if report.is_file() or verified else "blocked"

    reviews_root = run_root / "receipts" / "evidence" / "reviews"
    review_statuses: list[str] = []
    if reviews_root.is_dir():
        for review_path in reviews_root.glob("*.json"):
            try:
                review = json.loads(review_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
            if isinstance(review, Mapping):
                review_statuses.append(str(review.get("review_status", "")))
    submission_count = len(
        list((run_root / "receipts" / "evidence" / "submissions").rglob("*.json"))
    )
    task.update(
        {
            "status": effective,
            "status_summary": summary,
            "required_receipts": list(required_receipts),
            "verified_receipts": sorted(verified),
            "missing_receipts": missing,
            "accepted_evidence_count": len(accepted_evidence),
            "pending_evidence_count": max(
                0,
                submission_count - len(review_statuses),
            ),
            "rejected_evidence_count": review_statuses.count("rejected"),
            "missing_evidence_obligations": [
                item
                for item in missing
                if item.startswith(
                    (
                        "evidence:",
                        "evidence_support:",
                        "counterevidence:",
                        "claim_ledger:",
                    )
                )
            ],
            "final_report": ("outputs/report.md" if report.is_file() else None),
        }
    )
    write_json_atomic(task_path, task)
    return task
