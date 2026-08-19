"""Task-scoped persistence and deterministic state transitions for review 2.0."""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import mimetypes
import os
import re
import sys
import threading
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from filelock import FileLock

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SRC = _PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from jw.research_protocols import (  # noqa: E402
    SILSO_CYCLE_EXTREMA_DATA_PRODUCT,
    SOLAR_POLAR_PRECURSOR_DATA_PRODUCT,
    detect_analysis_protocol,
    plan_dataset_selection_conflicts_protocol,
    required_data_product_for_protocol,
    resolve_required_dataset_ids,
)
from jw.workspaces import (  # noqa: E402
    workspace_context_key,
    workspace_root_from_config,
)
from research_quality.contracts import (  # noqa: E402
    build_scientific_quality_assessment,
    validate_scientific_quality_assessment,
)
from research_review.adapters import adapt_v1_producer_output  # noqa: E402
from research_review.assessment import (  # noqa: E402
    build_review_assessment,
    validate_review_assessment,
)
from research_review.contracts import (  # noqa: E402
    CLAIM_VERSION,
    POLICY_VERSION,
    RUN_STATE_VERSION,
    ContractError,
    build_research_artifact,
    build_review_verdict,
    build_revision_capsule,
    build_revision_response,
    canonical_json_sha256,
    issue_fingerprint,
    validate_research_artifact,
    validate_review_verdict,
    validate_run_state,
)
from research_review.policies import policy_registry  # noqa: E402
from scientific_hypothesis.tail_search import tail_review_is_current  # noqa: E402

logger = logging.getLogger(__name__)

Producer = Literal[
    "solar-planner", "solar-data", "solar-hypothesis", "solar-experiment", "main"
]

PRODUCER_FOR_STAGE: dict[str, Producer] = {
    "planning": "solar-planner",
    "data": "solar-data",
    "hypothesis": "solar-hypothesis",
    "experiment_design": "solar-experiment",
    "experiment_result": "solar-experiment",
    "final_release": "main",
}
STAGE_FOR_OWNER = {
    "solar-planner": "planning",
    "solar-data": "data",
    "solar-hypothesis": "hypothesis",
    "main": "final_release",
}
REVISION_OWNERS_FOR_MODE = {
    "planning": {"solar-planner"},
    "data": {"solar-planner", "solar-data"},
    "hypothesis": {"solar-planner", "solar-data", "solar-hypothesis"},
    "experiment_design": {
        "solar-planner",
        "solar-data",
        "solar-hypothesis",
        "solar-experiment",
    },
    "experiment_result": {
        "solar-planner",
        "solar-data",
        "solar-hypothesis",
        "solar-experiment",
    },
    "integration": {
        "solar-planner",
        "solar-data",
        "solar-hypothesis",
        "solar-experiment",
    },
    "final_release": {
        "solar-planner",
        "solar-data",
        "solar-hypothesis",
        "solar-experiment",
        "main",
    },
}
REVIEW_SEQUENCE = (
    "planning",
    "data",
    "hypothesis",
    "experiment_design",
    "experiment_result",
)
ALL_REVIEW_MODES = (*REVIEW_SEQUENCE, "integration", "final_release")
SINGLE_PASS_REVIEW_INVOCATIONS = len(ALL_REVIEW_MODES)
SINGLE_PASS_ACTION_INVOCATIONS = 17
STAGE_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "planning": (),
    "data": ("planning",),
    "hypothesis": ("planning", "data"),
    "experiment_design": ("planning", "data", "hypothesis"),
    "experiment_result": (
        "planning",
        "data",
        "hypothesis",
        "experiment_design",
    ),
    "integration": (
        "planning",
        "data",
        "hypothesis",
        "experiment_design",
        "experiment_result",
    ),
    "final_release": ("integration",),
}

_PATH_REF = re.compile(
    r"(?<![A-Za-z0-9_.-])(?:inputs|work|outputs|receipts|planner|hypothesis|experiment|research_review)/[A-Za-z0-9_./:-]+"
)


def _producer_path_refs(text: str) -> set[str]:
    """Extract concrete file-like refs without promoting prose fragments."""

    refs: set[str] = set()
    for raw_ref in _PATH_REF.findall(text):
        source_ref = raw_ref.rstrip(".,:;")
        if source_ref and not source_ref.endswith("/"):
            refs.add(source_ref)
    return refs


# Scientific-number guards must not interpret the numeric suffix of a stable
# identifier (for example ``issue_prov_bound_001``) as a reviewer-authored
# measurement.  Underscores are identifier characters throughout the review
# contracts, so keep them inside both token boundaries.
_SEVERITY_RANK = {"minor": 0, "major": 1, "critical": 2}
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.RLock()
_MAX_REVIEW_SOURCE_BYTES = 256 * 1024
_MAX_DOCUMENT_TEXT_CHARS = 800_000
_MAX_DOCUMENT_SECTION_CHARS = 6_000
_STRUCTURED_SOURCE_SUFFIXES = {
    ".csv",
    ".json",
    ".jsonl",
    ".md",
    ".parquet",
    ".tsv",
}
_SOLAR_DATA_OUTPUT_RECEIPT_CONTRACTS = {
    ("research-dataset-receipt-v1", "silso_cycle_extrema_reproduction"),
    ("solar-precursor-cycle-table-v1", "solar_precursor_cycle_table"),
    ("solar-precursor-cycle-table-v2", "solar_precursor_cycle_table"),
    ("solar-cycle-pair-analysis-table-v2", "solar_cycle_pair_analysis_table"),
}
_DATA_CONTEXT_TRANSIENT_FIELDS = {
    "context_sha256",
    "created_at",
    "instruction",
    "path_policy",
    "produced_data_receipt_ref",
    "receipt_ref",
}
_HARNESS_SOURCE_PREFIX = "research_review/harness/"
_SAME_CYCLE_BMR_CAUSALITY = re.compile(
    r"(?:下一|后一)\s*(?:太阳)?(?:活动)?(?:周|周期).{0,30}"
    r"(?:振幅|强度|峰值).{0,100}(?:该|本|同一)\s*(?:太阳)?(?:活动)?"
    r"(?:周|周期).{0,30}(?:自身)?\s*(?:BMR|双极磁区|双极区|倾斜角)"
    r"|(?:next|following)\s+(?:solar\s+)?cycle.{0,30}"
    r"(?:amplitude|strength|peak).{0,100}(?:its\s+own|same[-\s]+cycle)"
    r".{0,30}(?:BMR|bipolar|tilt)",
    re.IGNORECASE,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _query_terms(query: str) -> list[str]:
    folded = query.casefold()
    terms = re.findall(r"[a-z0-9][a-z0-9_.-]{2,}", folded)
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", folded))
    terms.extend(
        chinese[index : index + 2] for index in range(max(len(chinese) - 1, 0))
    )
    return list(dict.fromkeys(term for term in terms if term))[:40]


def _chunk_document_text(text: str, prefix: str) -> list[dict[str, str]]:
    sections: list[dict[str, str]] = []
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    for block_index, block in enumerate(blocks, start=1):
        for part_index, start in enumerate(
            range(0, len(block), _MAX_DOCUMENT_SECTION_CHARS), start=1
        ):
            suffix = (
                f"/part:{part_index}"
                if len(block) > _MAX_DOCUMENT_SECTION_CHARS
                else ""
            )
            sections.append(
                {
                    "section_id": f"{prefix}:{block_index}{suffix}",
                    "text": block[start : start + _MAX_DOCUMENT_SECTION_CHARS],
                }
            )
    return sections


def _normalize_claim_versions(claims: object) -> object:
    """Remove adapter-only version suffixes for idempotence comparison."""

    cloned = json.loads(json.dumps(claims, ensure_ascii=False))
    if isinstance(cloned, list):
        for claim in cloned:
            if isinstance(claim, dict) and isinstance(claim.get("claim_id"), str):
                claim["claim_id"] = re.sub(r"-v\d+$", "-v", claim["claim_id"])
    return cloned


def _adapted_producer_semantics(
    adapted: dict[str, Any], *, evidence_refs: list[str], upstream_refs: list[str]
) -> dict[str, Any]:
    payload = adapted.get("payload") if isinstance(adapted.get("payload"), dict) else {}
    semantic_payload = {
        key: value
        for key, value in payload.items()
        if key not in {"producer_result", "revision_response"}
    }
    if "hypothesis_scientific_content" in semantic_payload:
        # Hypothesis content and its exact evidence rows are already retained
        # in semantic fields. A state-file digest change caused only by a
        # timestamp or checkpoint counter must not mint a new scientific
        # artifact version.
        semantic_payload.pop("source_manifest", None)
    return {
        "claims": _normalize_claim_versions(adapted.get("claims", [])),
        "limitations": adapted.get("limitations", []),
        "evidence_refs": evidence_refs,
        "upstream_refs": upstream_refs,
        "payload": semantic_payload,
    }


def _producer_semantics(artifact: dict[str, Any]) -> dict[str, Any]:
    return _adapted_producer_semantics(
        {
            "claims": artifact.get("claims", []),
            "limitations": artifact.get("limitations", []),
            "payload": artifact.get("payload", {}),
        },
        evidence_refs=list(artifact.get("evidence_refs", [])),
        upstream_refs=list(artifact.get("upstream_refs", [])),
    )


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be in [{minimum}, {maximum}]")
    return value


def store_from_config(config: object) -> ResearchReviewStore:
    """Build the task-scoped store from deployment-safe review settings."""

    policy = os.environ.get("JW_RESEARCH_REVISION_POLICY", "adaptive").strip()
    if policy not in {"adaptive", "fixed"}:
        raise ValueError("JW_RESEARCH_REVISION_POLICY must be adaptive or fixed")
    context_key = workspace_context_key(config)
    task_id = (
        context_key
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", context_key)
        else f"task-{hashlib.sha256(context_key.encode('utf-8')).hexdigest()[:24]}"
    )
    return ResearchReviewStore(
        workspace_root_from_config(config),
        task_id,
        revision_policy=policy,
        max_revisions=_int_env(
            "JW_RESEARCH_MAX_REVISIONS",
            0 if policy == "adaptive" else 3,
            minimum=0,
            maximum=100,
        ),
        no_progress_patience=_int_env(
            "JW_RESEARCH_NO_PROGRESS_PATIENCE", 2, minimum=1, maximum=20
        ),
        budget_multiplier=_int_env(
            "JW_RESEARCH_BUDGET_MULTIPLIER", 5, minimum=1, maximum=20
        ),
    )


def _lock_for(root: Path) -> threading.RLock:
    key = str(root.resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock_for(path):
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _safe_content(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        pieces: list[str] = []
        for item in value:
            if isinstance(item, Mapping) and isinstance(item.get("text"), str):
                pieces.append(item["text"])
            elif isinstance(item, str):
                pieces.append(item)
        return "\n".join(pieces)
    return str(value or "")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _default_state(
    task_id: str,
    *,
    revision_policy: str = "adaptive",
    max_revisions: int = 0,
    no_progress_patience: int = 2,
    budget_multiplier: int = 5,
) -> dict[str, Any]:
    return validate_run_state(
        {
            "schema_version": RUN_STATE_VERSION,
            "task_id": task_id,
            "revision_policy": revision_policy,
            "max_revisions": max_revisions,
            "no_progress_patience": no_progress_patience,
            "budget_multiplier": budget_multiplier,
            "action_invocations": 0,
            "max_action_invocations": SINGLE_PASS_ACTION_INVOCATIONS
            * budget_multiplier,
            "review_invocations": 0,
            "max_review_invocations": SINGLE_PASS_REVIEW_INVOCATIONS
            * budget_multiplier,
            "status": "active",
            "current_stage": "planning",
            "artifacts": [],
            "verdicts": [],
            "stage_status": dict.fromkeys(ALL_REVIEW_MODES, "pending"),
            "dependency_graph": {
                "schema_version": "research-dependency-graph-v2",
                "source_ref": None,
                "stage_dependencies": {
                    stage: list(dependencies)
                    for stage, dependencies in STAGE_DEPENDENCIES.items()
                },
                "planner_steps": [],
            },
            "updated_at": _now(),
        }
    )


class ResearchReviewStore:
    """Own immutable artifacts, verdicts, and the task-level review state."""

    def __init__(
        self,
        workspace_root: Path,
        task_id: str,
        *,
        revision_policy: str = "adaptive",
        max_revisions: int = 0,
        no_progress_patience: int = 2,
        budget_multiplier: int = 5,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.task_id = task_id
        self.root = self.workspace_root / "research_review"
        self.state_path = self.root / "run_state.json"
        self._file_lock = FileLock(str(self.root / ".research-review.lock"), timeout=30)
        self._defaults = {
            "revision_policy": revision_policy,
            "max_revisions": max_revisions,
            "no_progress_patience": no_progress_patience,
            "budget_multiplier": budget_multiplier,
        }
        self._lock = _lock_for(self.root)

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        """Serialize read-modify-write transitions across threads and workers."""

        self.root.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with self._file_lock:
                yield

    def load_state(self) -> dict[str, Any]:
        with self._transaction():
            payload = _read_json(self.state_path)
            if payload is None:
                return _default_state(self.task_id, **self._defaults)
            # Additive migration for task workspaces created by the first v2
            # implementation before graph/action budget fields were persisted.
            defaults = _default_state(self.task_id, **self._defaults)
            for field in (
                "action_invocations",
                "max_action_invocations",
                "dependency_graph",
            ):
                payload.setdefault(field, defaults[field])
            state = validate_run_state(payload)
            if state["task_id"] != self.task_id:
                raise RuntimeError("research review state belongs to another task")
            return state

    def _save_state(self, state: dict[str, Any]) -> dict[str, Any]:
        state = dict(state)
        state["updated_at"] = _now()
        validated = validate_run_state(state)
        _atomic_write_json(self.state_path, validated)
        return validated

    def reserve_action(self, action: Mapping[str, Any]) -> dict[str, Any]:
        """Charge one actual graph action before invoking a model/tool node."""

        if action.get("kind") in {"terminal", "released"}:
            return self.load_state()
        with self._transaction():
            state = self.load_state()
            if state["action_invocations"] >= state["max_action_invocations"]:
                state["status"] = "blocked"
                self._save_state(state)
                raise RuntimeError("RESEARCH_ACTION_BUDGET_EXHAUSTED")
            state["action_invocations"] += 1
            return self._save_state(state)

    def latest_tool_failure_receipt(self) -> dict[str, Any] | None:
        receipts: list[tuple[str, str, dict[str, Any]]] = []
        for path in (self.root / "failures").glob("*/*.json"):
            payload = _read_json(path)
            if not isinstance(payload, dict) or payload.get("task_id") != self.task_id:
                continue
            created_at = payload.get("created_at")
            if not isinstance(created_at, str):
                continue
            receipts.append((created_at, path.as_posix(), payload))
        return (
            max(receipts, default=None, key=lambda item: item[:2])[2]
            if receipts
            else None
        )

    def block_for_tool_failures(
        self,
        *,
        stage: str,
        producer: Producer,
        fingerprints: Sequence[str],
        failure_summaries: Sequence[str] = (),
        recovery: str = "new_task_after_fix",
    ) -> dict[str, Any]:
        """Persist a terminal infrastructure failure and close the task state."""

        if PRODUCER_FOR_STAGE.get(stage) != producer:
            raise ValueError(f"{producer} does not own stage {stage}")
        stable_fingerprints = sorted(
            {value for value in fingerprints if re.fullmatch(r"[0-9a-f]{64}", value)}
        )
        summaries = []
        for value in failure_summaries:
            sanitized = " ".join(str(value).split()).strip()
            if sanitized and sanitized not in summaries:
                summaries.append(sanitized[:500])
        with self._transaction():
            failure_dir = self.root / "failures" / stage
            index = len(list(failure_dir.glob("*.json"))) + 1
            receipt = {
                "schema_version": "research-tool-failure-v1",
                "task_id": self.task_id,
                "stage": stage,
                "producer": producer,
                "reason_code": "REQUIRED_SPECIALIST_FAILED_TWICE",
                "failure_count": 2,
                "fingerprints": stable_fingerprints,
                "failure_summaries": summaries[:2],
                "recovery": recovery,
                "created_at": _now(),
            }
            receipt["receipt_sha256"] = canonical_json_sha256(receipt)
            path = failure_dir / f"tool-failure-{index:04d}.json"
            _atomic_write_json(path, receipt)
            state = self.load_state()
            state["status"] = "blocked"
            state["current_stage"] = stage
            state["stage_status"][stage] = "blocked"
            self._save_state(state)
            return receipt

    def block_for_review_failures(
        self,
        *,
        stage: str,
        reviewer: str,
        fingerprints: Sequence[str],
        failure_summaries: Sequence[str] = (),
        recovery: str = "new_task_after_fix",
    ) -> dict[str, Any]:
        """Persist repeated reviewer-contract failures without blaming a producer."""

        if stage not in PRODUCER_FOR_STAGE:
            raise ValueError(f"unknown research stage {stage}")
        if reviewer != "solar-evidence":
            raise ValueError(f"unsupported research reviewer {reviewer}")
        stable_fingerprints = sorted(
            {value for value in fingerprints if re.fullmatch(r"[0-9a-f]{64}", value)}
        )
        summaries = []
        for value in failure_summaries:
            sanitized = " ".join(str(value).split()).strip()
            if sanitized and sanitized not in summaries:
                summaries.append(sanitized[:500])
        with self._transaction():
            failure_dir = self.root / "failures" / stage
            index = len(list(failure_dir.glob("*.json"))) + 1
            receipt = {
                "schema_version": "research-tool-failure-v1",
                "task_id": self.task_id,
                "stage": stage,
                "specialist": reviewer,
                "specialist_role": "reviewer",
                "reason_code": "REQUIRED_SPECIALIST_FAILED_TWICE",
                "failure_count": 2,
                "fingerprints": stable_fingerprints,
                "failure_summaries": summaries[:2],
                "recovery": recovery,
                "created_at": _now(),
            }
            receipt["receipt_sha256"] = canonical_json_sha256(receipt)
            path = failure_dir / f"tool-failure-{index:04d}.json"
            _atomic_write_json(path, receipt)
            state = self.load_state()
            state["status"] = "blocked"
            state["current_stage"] = stage
            state["stage_status"][stage] = "blocked"
            self._save_state(state)
            return receipt

    def recover_canonical_producer_after_tool_failure(
        self,
    ) -> dict[str, Any] | None:
        """Reconcile a blocked producer when its canonical artifact now exists.

        The action budget and original failure receipt remain intact. Recovery
        is permitted only after the exact task-local v1 source passes the
        existing deterministic readiness gate.
        """

        with self._transaction():
            state = self.load_state()
            if state["status"] != "blocked":
                return None
            failure = self.latest_tool_failure_receipt()
            if (
                failure is None
                or failure.get("reason_code") != "REQUIRED_SPECIALIST_FAILED_TWICE"
                or failure.get("stage") != state["current_stage"]
                or failure.get("specialist_role") == "reviewer"
            ):
                return None
            stage = str(failure["stage"])
            producer = str(failure["producer"])
            canonical_sources = self._canonical_stage_sources(stage)
            if not self._canonical_stage_ready(stage, canonical_sources, phase=stage):
                return None
            preserved_action_invocations = state["action_invocations"]
            try:
                artifact = self.checkpoint_producer_result(
                    stage=stage,
                    producer=producer,  # type: ignore[arg-type]
                    content=(
                        "[DETERMINISTIC CANONICAL PRODUCER RECOVERY] "
                        + ", ".join(
                            path.relative_to(self.workspace_root).as_posix()
                            for path in canonical_sources
                        )
                    ),
                    phase=stage,
                    require_canonical_source=True,
                )
            except RuntimeError:
                return None
            recovery = {
                "schema_version": "research-tool-failure-recovery-v1",
                "task_id": self.task_id,
                "stage": stage,
                "producer": producer,
                "failure_receipt_sha256": failure["receipt_sha256"],
                "artifact_ref": self.artifact_ref(artifact),
                "preserved_action_invocations": preserved_action_invocations,
                "created_at": _now(),
            }
            recovery["receipt_sha256"] = canonical_json_sha256(recovery)
            recovery_dir = self.root / "recoveries" / stage
            index = len(list(recovery_dir.glob("*.json"))) + 1
            _atomic_write_json(
                recovery_dir / f"canonical-recovery-{index:04d}.json", recovery
            )
            return recovery

    def reopen_tool_failure_after_harness_change(
        self, *, failure_receipt_sha256: str, change_id: str
    ) -> dict[str, Any]:
        """Explicitly reopen one blocked stage after a versioned harness change."""

        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", change_id):
            raise ValueError("change_id must be a stable identifier")
        with self._transaction():
            state = self.load_state()
            failure = self.latest_tool_failure_receipt()
            if failure is None:
                raise RuntimeError("research run has no tool failure receipt")
            if failure.get("receipt_sha256") != failure_receipt_sha256:
                raise RuntimeError("failure receipt hash does not match latest failure")
            stage = str(failure["stage"])
            recovery_dir = self.root / "recoveries" / stage
            for path in recovery_dir.glob("harness-reopen-*.json"):
                prior = _read_json(path)
                if isinstance(prior, dict) and prior.get("change_id") == change_id:
                    # Older recovery code restored an unreviewed reviewer-failed
                    # artifact to pending, which incorrectly re-ran the producer.
                    # Repair that idempotently when the immutable artifact still
                    # exists and no verdict has been bound.
                    artifact = self.latest_artifact(stage)
                    verdict = (
                        self.matching_verdict(stage, [self.artifact_ref(artifact)])
                        if artifact is not None
                        else None
                    )
                    if (
                        prior.get("restored_stage_status") == "pending"
                        and artifact is not None
                        and verdict is None
                    ):
                        state["status"] = "active"
                        state["current_stage"] = stage
                        state["stage_status"][stage] = "produced"
                        self._save_state(state)
                    return prior
            if (
                state["status"] != "blocked"
                and state["stage_status"].get(stage) != "blocked"
            ):
                raise RuntimeError("research run is not blocked by a tool failure")
            artifact = self.latest_artifact(stage)
            verdict = (
                self.matching_verdict(stage, [self.artifact_ref(artifact)])
                if artifact is not None
                else None
            )
            restored_stage_status = (
                "revise"
                if verdict is not None and verdict.get("decision") == "revise"
                else (
                    "produced"
                    if artifact is not None and verdict is None
                    else "pending"
                )
            )
            preserved_action_invocations = state["action_invocations"]
            state["status"] = "active"
            state["current_stage"] = stage
            state["stage_status"][stage] = restored_stage_status
            self._save_state(state)
            recovery = {
                "schema_version": "research-harness-change-recovery-v1",
                "task_id": self.task_id,
                "stage": stage,
                "failure_receipt_sha256": failure_receipt_sha256,
                "change_id": change_id,
                "restored_stage_status": restored_stage_status,
                "preserved_action_invocations": preserved_action_invocations,
                "created_at": _now(),
            }
            recovery["receipt_sha256"] = canonical_json_sha256(recovery)
            index = len(list(recovery_dir.glob("harness-reopen-*.json"))) + 1
            _atomic_write_json(
                recovery_dir / f"harness-reopen-{index:04d}.json", recovery
            )
            return recovery

    @staticmethod
    def _invalidate_downstream(state: dict[str, Any], stage: str, phase: str) -> None:
        downstream = {
            "planning": (
                "data",
                "hypothesis",
                "experiment_design",
                "experiment_result",
                "integration",
                "final_release",
            ),
            "data": (
                "hypothesis",
                "experiment_design",
                "experiment_result",
                "integration",
                "final_release",
            ),
            "hypothesis": (
                ("integration", "final_release")
                if phase
                in {
                    "hypothesis_update",
                    "integration_revision",
                    "hypothesis_revision_from_final_release",
                }
                else (
                    "experiment_design",
                    "experiment_result",
                    "integration",
                    "final_release",
                )
            ),
            "experiment_design": (
                "experiment_result",
                "integration",
                "final_release",
            ),
            "experiment_result": ("hypothesis", "integration", "final_release"),
        }.get(stage, ())
        for downstream_stage in downstream:
            state["stage_status"][downstream_stage] = "pending"

    def _artifact_paths(self) -> list[Path]:
        return sorted((self.root / "artifacts").glob("*/v*.json"))

    def _verdict_paths(self) -> list[Path]:
        return sorted((self.root / "verdicts").glob("*.json"))

    def artifacts(self, *, stage: str | None = None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in self._artifact_paths():
            payload = _read_json(path)
            if payload is None:
                continue
            try:
                artifact = validate_research_artifact(payload)
            except ValueError:
                continue
            if artifact["task_id"] == self.task_id and (
                stage is None or artifact["stage"] == stage
            ):
                rows.append(artifact)
        return sorted(rows, key=lambda row: (row["stage"], row["version"]))

    def verdicts(self, *, mode: str | None = None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in self._verdict_paths():
            payload = _read_json(path)
            if payload is None:
                continue
            try:
                verdict = validate_review_verdict(payload)
            except ValueError:
                continue
            if verdict["task_id"] == self.task_id and (
                mode is None or verdict["review_mode"] == mode
            ):
                rows.append(verdict)
        return sorted(rows, key=lambda row: (row["review_mode"], row["round"]))

    def latest_artifact(self, stage: str) -> dict[str, Any] | None:
        rows = self.artifacts(stage=stage)
        return max(rows, key=lambda row: row["version"], default=None)

    def matching_verdict(
        self, mode: str, artifact_refs: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        expected = {
            (ref["artifact_id"], ref["version"], ref["artifact_sha256"])
            for ref in artifact_refs
        }
        matches = []
        for verdict in self.verdicts(mode=mode):
            if verdict["policy_version"] != POLICY_VERSION:
                continue
            actual = {
                (ref["artifact_id"], ref["version"], ref["artifact_sha256"])
                for ref in verdict["artifact_refs"]
            }
            if actual == expected:
                matches.append(verdict)
        return max(matches, key=lambda row: row["round"], default=None)

    @staticmethod
    def artifact_ref(artifact: dict[str, Any]) -> dict[str, Any]:
        return {
            "artifact_id": artifact["artifact_id"],
            "version": artifact["version"],
            "artifact_sha256": artifact["artifact_sha256"],
        }

    def accepted_artifacts(self) -> list[dict[str, Any]]:
        accepted: list[dict[str, Any]] = []
        for stage in REVIEW_SEQUENCE:
            artifact = self.latest_artifact(stage)
            if artifact is None:
                continue
            verdict = self.matching_verdict(stage, [self.artifact_ref(artifact)])
            if verdict and verdict["decision"] in {"accept", "accept_with_limits"}:
                accepted.append(artifact)
        return accepted

    @staticmethod
    def _long_ref(item: dict[str, Any]) -> str:
        return f"{item['artifact_id']}@v{item['version']}:{item['artifact_sha256']}"

    def _upstream_refs(self) -> list[str]:
        return [self._long_ref(item) for item in self.accepted_artifacts()]

    def _accepted_stage(self, stage: str) -> dict[str, Any] | None:
        artifact = self.latest_artifact(stage)
        if artifact is None:
            return None
        verdict = self.matching_verdict(stage, [self.artifact_ref(artifact)])
        if verdict is None or verdict["decision"] not in {
            "accept",
            "accept_with_limits",
        }:
            return None
        return artifact

    def _producer_upstream_refs(self, stage: str, phase: str) -> list[str]:
        if phase.startswith("bounded_"):
            if stage == "hypothesis":
                data = self._accepted_stage("data")
                if data is not None:
                    return [self._long_ref(data)]
            return []
        refs: list[str] = []
        for dependency in STAGE_DEPENDENCIES.get(stage, ()):
            artifact = self._accepted_stage(dependency)
            if artifact is None:
                raise RuntimeError(
                    f"{stage} requires an accepted {dependency} artifact"
                )
            refs.append(self._long_ref(artifact))
        if stage == "hypothesis" and phase in {
            "hypothesis_update",
            "integration_revision",
            "hypothesis_revision_from_final_release",
        }:
            result = self._accepted_stage("experiment_result")
            if result is None:
                raise RuntimeError(
                    "hypothesis_update requires an accepted experiment_result"
                )
            refs.append(self._long_ref(result))
        return refs

    def _latest_run_root(self, relative: str) -> Path | None:
        root = self.workspace_root / relative
        candidates = [path for path in root.glob("*") if path.is_dir()]
        if not candidates:
            return None
        return max(candidates, key=lambda path: path.stat().st_mtime_ns)

    def _receipted_solar_data_outputs(self, receipt_paths: list[Path]) -> list[Path]:
        """Return only current-task Data outputs bound by recognized receipts."""

        output_root = (self.workspace_root / "work" / "solar_data").resolve()
        outputs: list[Path] = []
        for receipt_path in receipt_paths:
            receipt = _read_json(receipt_path)
            if not isinstance(receipt, dict):
                continue
            contract = (receipt.get("schema_version"), receipt.get("receipt_type"))
            status_is_reviewable = receipt.get("status") == "verified" or (
                contract
                == (
                    "solar-cycle-pair-analysis-table-v2",
                    "solar_cycle_pair_analysis_table",
                )
                and receipt.get("status") == "partial"
                and receipt.get("analysis_status") == "analysis_table_incomplete"
            )
            if (
                contract not in _SOLAR_DATA_OUTPUT_RECEIPT_CONTRACTS
                or not status_is_reviewable
                or receipt.get("producer") != "solar-data"
                or receipt.get("task_id") != self.task_id
            ):
                continue
            declared_outputs = receipt.get("outputs")
            if not isinstance(declared_outputs, list):
                continue
            for declared in declared_outputs:
                if not isinstance(declared, dict):
                    continue
                source_ref = declared.get("path")
                sha256 = declared.get("sha256")
                if (
                    not isinstance(source_ref, str)
                    or not isinstance(sha256, str)
                    or Path(source_ref).is_absolute()
                ):
                    continue
                unresolved = self.workspace_root / source_ref
                cursor = self.workspace_root
                has_symlink = False
                for part in Path(source_ref).parts:
                    cursor /= part
                    if cursor.is_symlink():
                        has_symlink = True
                        break
                candidate = unresolved.resolve()
                if (
                    has_symlink
                    or not candidate.is_relative_to(output_root)
                    or not candidate.is_file()
                    or _file_sha256(candidate) != sha256
                ):
                    continue
                outputs.append(candidate)
        return outputs

    def _task_manifest_inputs(self) -> list[Path]:
        """Return exact task-local files declared by the current input manifest."""

        task = _read_json(self.workspace_root / "task.json")
        manifest = _read_json(self.workspace_root / "input_manifest.json")
        if (
            not isinstance(task, dict)
            or task.get("thread_id") != self.task_id
            or not isinstance(manifest, dict)
        ):
            return []
        excluded_roles = {
            "derived_artifact",
            "provenance",
            "reference_code",
            "test_fixture",
        }
        inputs: list[Path] = []
        for source_group in ("inputs", "project_inputs"):
            records = manifest.get(source_group)
            if not isinstance(records, list):
                continue
            for record in records:
                if not isinstance(record, dict):
                    continue
                source_ref = record.get("path")
                sha256 = record.get("sha256")
                role = str(record.get("role") or "user_input")
                if (
                    not isinstance(source_ref, str)
                    or not isinstance(sha256, str)
                    or Path(source_ref).is_absolute()
                    or role in excluded_roles
                ):
                    continue
                unresolved = self.workspace_root / source_ref
                cursor = self.workspace_root
                has_symlink = False
                for part in Path(source_ref).parts:
                    cursor /= part
                    if cursor.is_symlink():
                        has_symlink = True
                        break
                candidate = unresolved.resolve()
                if (
                    has_symlink
                    or not candidate.is_relative_to(self.workspace_root)
                    or not candidate.is_file()
                    or _file_sha256(candidate) != sha256
                ):
                    continue
                declared_bytes = record.get("bytes")
                if (
                    isinstance(declared_bytes, int)
                    and candidate.stat().st_size != declared_bytes
                ):
                    continue
                inputs.append(candidate)
        return inputs

    def _resolve_project_data_manifest_path(self, source_ref: str) -> Path | None:
        """Resolve one registered ``/project/data`` path inside this run's project.

        Workspace manifests intentionally retain the agent-visible virtual path,
        while the review store is given only the concrete run directory.  Isolated
        workspaces use ``projects/<project>/runs/<run>`` and keep shared data at
        the sibling ``shared/data`` directory.  Resolve only that exact virtual
        prefix and reject symlinks or traversal before checking the declared
        bytes and digest at the caller.
        """

        prefix = "/project/data/"
        if not source_ref.startswith(prefix):
            return None
        relative_text = source_ref.removeprefix(prefix)
        relative = Path(relative_text)
        if (
            not relative_text
            or relative.is_absolute()
            or "\\" in relative_text
            or "\x00" in relative_text
            or ".." in relative.parts
        ):
            return None
        runs_root = self.workspace_root.parent
        project_root = runs_root.parent
        if (
            runs_root.name != "runs"
            or project_root.parent.name != "projects"
            or project_root.name in {"", ".", ".."}
        ):
            return None
        shared = project_root / "shared"
        data_root = shared / "data"
        if shared.is_symlink() or data_root.is_symlink():
            return None
        try:
            project_resolved = project_root.resolve(strict=True)
            data_resolved = data_root.resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            return None
        if not data_resolved.is_relative_to(project_resolved):
            return None
        cursor = data_root
        for part in relative.parts:
            cursor /= part
            if cursor.is_symlink():
                return None
        try:
            candidate = cursor.resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            return None
        if not candidate.is_relative_to(data_resolved) or not candidate.is_file():
            return None
        return candidate

    def _project_data_registration_matches(
        self,
        source_ref: str,
        expected_sha256: str,
        declared_bytes: int,
    ) -> bool:
        """Require a virtual project input to be present in its shared registry."""

        runs_root = self.workspace_root.parent
        project_root = runs_root.parent
        if runs_root.name != "runs" or project_root.parent.name != "projects":
            return False
        shared = project_root / "shared"
        relative = source_ref.removeprefix("/project/data/")
        if not relative:
            return False
        expected_relative = Path(relative).as_posix()
        for registry_name in ("project_data_catalog.json", "data_manifest.json"):
            registry = _read_json(shared / registry_name)
            files = registry.get("files") if isinstance(registry, dict) else None
            if not isinstance(files, list):
                continue
            for item in files:
                if not isinstance(item, Mapping):
                    continue
                if (
                    item.get("virtual_path") == source_ref
                    and item.get("path") == expected_relative
                    and item.get("sha256") == expected_sha256
                    and item.get("bytes") == declared_bytes
                    and str(item.get("role") or "primary_data") == "primary_data"
                ):
                    return True
        return False

    def _current_manifest_input_records(self) -> list[dict[str, Any]] | None:
        """Rebuild eligible input records from the current task manifest."""

        task = _read_json(self.workspace_root / "task.json")
        manifest = _read_json(self.workspace_root / "input_manifest.json")
        if (
            not isinstance(task, dict)
            or task.get("thread_id") != self.task_id
            or not isinstance(manifest, dict)
            or manifest.get("thread_id") != self.task_id
        ):
            return None
        excluded_roles = {
            "derived_artifact",
            "provenance",
            "reference_code",
            "test_fixture",
        }
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for source_group in ("inputs", "project_inputs"):
            raw_records = manifest.get(source_group, [])
            if not isinstance(raw_records, list):
                return None
            for raw in raw_records:
                if not isinstance(raw, Mapping):
                    return None
                role = str(raw.get("role") or "user_input")
                if role in excluded_roles:
                    continue
                source_ref = raw.get("path")
                expected_sha256 = raw.get("sha256")
                declared_bytes = raw.get("bytes")
                if (
                    not isinstance(source_ref, str)
                    or not source_ref
                    or "\\" in source_ref
                    or ".." in Path(source_ref).parts
                    or not isinstance(expected_sha256, str)
                    or not expected_sha256
                    or not isinstance(declared_bytes, int)
                    or isinstance(declared_bytes, bool)
                    or declared_bytes <= 0
                    or source_ref in seen
                ):
                    return None
                seen.add(source_ref)
                project_path = (
                    self._resolve_project_data_manifest_path(source_ref)
                    if source_group == "project_inputs"
                    else None
                )
                if source_group == "project_inputs":
                    if (
                        project_path is None
                        or not self._project_data_registration_matches(
                            source_ref, expected_sha256, declared_bytes
                        )
                    ):
                        return None
                    candidate = project_path
                else:
                    if Path(source_ref).is_absolute():
                        return None
                    unresolved = self.workspace_root / source_ref
                    cursor = self.workspace_root
                    has_symlink = False
                    for part in Path(source_ref).parts:
                        cursor /= part
                        if cursor.is_symlink():
                            has_symlink = True
                            break
                    candidate = unresolved.resolve()
                    if has_symlink or not candidate.is_relative_to(self.workspace_root):
                        return None
                if (
                    not candidate.is_file()
                    or candidate.stat().st_size != declared_bytes
                    or _file_sha256(candidate) != expected_sha256
                ):
                    return None
                record: dict[str, Any] = {
                    "path": source_ref,
                    "sha256": expected_sha256,
                    "bytes": declared_bytes,
                    "role": role,
                    "source_group": source_group,
                }
                for key in ("dataset_id", "provenance_ref"):
                    value = raw.get(key)
                    if isinstance(value, str) and value.strip():
                        record[key] = value
                records.append(record)
        return records

    def _canonical_stage_sources(self, stage: str) -> list[Path]:
        """Locate producer-owned task artifacts with known v1 semantics."""

        candidates: list[Path] = []
        if stage == "planning":
            run = self._latest_run_root("planner/runs")
            if run is not None:
                candidates.extend(
                    run / name
                    for name in (
                        "planner_request.json",
                        "research_plan.json",
                        "research_plan.md",
                    )
                )
        elif stage == "data":
            candidates.append(
                self.workspace_root / "work" / "solar_data" / "chat_session.json"
            )
            for relative_root in (
                "receipts/datasets",
                "outputs",
                f"research_review/harness/{self.task_id}",
            ):
                root = self.workspace_root / relative_root
                if root.is_dir():
                    candidates.extend(
                        path
                        for path in root.rglob("*")
                        if path.is_file()
                        and path.suffix.casefold() in _STRUCTURED_SOURCE_SUFFIXES
                    )
            receipts_root = (self.workspace_root / "receipts" / "datasets").resolve()
            receipt_paths = [
                path
                for path in candidates
                if path.is_file() and path.resolve().is_relative_to(receipts_root)
            ]
            candidates.extend(self._receipted_solar_data_outputs(receipt_paths))
            candidates.extend(self._task_manifest_inputs())
        elif stage == "hypothesis":
            candidates.append(
                self.workspace_root / "work" / "scientific_hypothesis_state.json"
            )
            run = self._latest_run_root("hypothesis/runs")
            if run is not None:
                candidates.extend(
                    run / name
                    for name in (
                        "hypothesis_request.json",
                        "hypothesis_portfolio.json",
                        "hypotheses.md",
                    )
                )
        elif stage in {"experiment_design", "experiment_result"}:
            run = None
            if stage == "experiment_result":
                design_artifact = self._accepted_stage("experiment_design")
                if design_artifact is not None:
                    manifest = design_artifact.get("payload", {}).get(
                        "source_manifest", []
                    )
                    for item in manifest if isinstance(manifest, list) else []:
                        source_ref = (
                            item.get("source_ref") if isinstance(item, dict) else None
                        )
                        if isinstance(source_ref, str) and source_ref.endswith(
                            "/design.json"
                        ):
                            run = (self.workspace_root / source_ref).resolve().parent
                            break
            if run is None:
                run = self._latest_run_root("experiment/runs")
            if run is not None:
                names = ["state.json", "request.json", "response.json", "design.json"]
                if stage == "experiment_result":
                    names.extend(
                        ["record.json", "entry_result.json", "report.md", "audit.md"]
                    )
                candidates.extend(run / name for name in names)
        quality_contract = (
            self.workspace_root
            / "work"
            / "research_quality"
            / f"{stage}.analysis_claim.json"
        )
        if quality_contract.is_file():
            candidates.append(quality_contract)
        unique = {
            path.resolve(): path.resolve()
            for path in candidates
            if path.is_file() and path.resolve().is_relative_to(self.workspace_root)
        }
        return sorted(unique.values(), key=lambda path: path.as_posix())[:200]

    def _canonical_stage_ready(
        self, stage: str, sources: list[Path], *, phase: str = ""
    ) -> bool:
        names = {path.name for path in sources}
        if stage == "data":

            def bounded_data_outputs(paths: list[Path]) -> list[Path]:
                output_roots = [
                    (self.workspace_root / "work" / "solar_data").resolve(),
                    (self.workspace_root / "outputs").resolve(),
                ]
                runtime_names = {
                    "chat_session.json",
                    "request.json",
                    "response.json",
                    "trace.json",
                    "receipt.json",
                }
                result: list[Path] = []
                for path in paths:
                    resolved = path.resolve()
                    if path.name in runtime_names:
                        continue
                    if any(
                        resolved.is_relative_to(root)
                        and path.suffix.casefold() in _STRUCTURED_SOURCE_SUFFIXES
                        for root in output_roots
                    ):
                        result.append(path)
                return result

            bounded_phase = phase.startswith("bounded_data")
            contexts: list[tuple[str, dict[str, Any]]] = []
            produced_sources: list[Path] = []
            for path in sources:
                if path.name.startswith("data-context-") and path.suffix == ".json":
                    payload = _read_json(path)
                    if (
                        isinstance(payload, dict)
                        and payload.get("schema_version") == "solar-data-context-v1"
                    ):
                        contexts.append(
                            (
                                path.relative_to(self.workspace_root).as_posix(),
                                payload,
                            )
                        )
                        continue
                produced_sources.append(path)
            if not contexts:
                # Bounded/legacy Data analyses do not have an accepted Planning
                # artifact from which solar_data_open_context can be opened.
                # Preserve their existing producer-owned artifact contract;
                # full-research dispatch always creates a context receipt first.
                return bounded_phase and bool(bounded_data_outputs(produced_sources))
            authoritative = [
                (source_ref, context)
                for source_ref, context in contexts
                if self._data_context_is_authoritative(source_ref, context, phase=phase)
            ]
            if authoritative:
                _source_ref, current = max(
                    authoritative,
                    key=lambda item: (
                        str(item[1].get("created_at") or ""),
                        item[0],
                    ),
                )
            else:
                return False
            if (
                current is not None
                and self._data_context_confirms_required_inputs_missing(current)
            ):
                # A hash-bound, honest input blocker is a complete Data-stage
                # result. It may proceed to Evidence review without fabricated
                # derived output.
                return True
            if bounded_phase:
                # Preserve the plan-free bounded Data producer contract. Full
                # research has the stricter recognized receipt/output boundary
                # below and cannot inherit this compatibility path.
                return bool(bounded_data_outputs(produced_sources))
            receipt_root = (self.workspace_root / "receipts" / "datasets").resolve()
            receipt_paths = [
                path
                for path in produced_sources
                if path.suffix.casefold() == ".json"
                and path.resolve().is_relative_to(receipt_root)
            ]
            recognized_outputs = {
                path.resolve()
                for path in self._receipted_solar_data_outputs(receipt_paths)
            }
            produced_refs = {path.resolve() for path in produced_sources}
            return bool(recognized_outputs & produced_refs)
        if stage == "hypothesis" and not phase.startswith("bounded_hypothesis"):
            state_path = next(
                (
                    path
                    for path in sources
                    if path.name == "scientific_hypothesis_state.json"
                ),
                None,
            )
            if state_path is None:
                return False
            payload = _read_json(state_path)
            if not isinstance(payload, dict):
                return False
            latest_draft = payload.get("latest_draft")
            if not isinstance(latest_draft, dict):
                return False
            response_kind = latest_draft.get("response_kind")
            if response_kind in {"clarification_needed", "hypothesis_blocked"}:
                details_key = (
                    "questions"
                    if response_kind == "clarification_needed"
                    else "blockers"
                )
                details = latest_draft.get(details_key)
                return (
                    isinstance(details, list)
                    and bool(details)
                    and payload.get("latest_draft_sha256")
                    == canonical_json_sha256(latest_draft)
                )
            checkpoint = payload.get("checkpoint")
            if not isinstance(checkpoint, dict):
                return False
            candidates = checkpoint.get("candidates")
            if (
                checkpoint.get("response_kind") != "hypotheses_ready"
                or not isinstance(candidates, list)
                or not candidates
            ):
                return False
            checkpoint_sha256 = canonical_json_sha256(checkpoint)
            if payload.get("checkpoint_sha256") != checkpoint_sha256:
                return False
            latest_sha256 = canonical_json_sha256(latest_draft)
            if (
                latest_sha256 != checkpoint_sha256
                or payload.get("latest_draft_sha256") != latest_sha256
            ):
                return False
            evidence_register = payload.get("evidence_register")
            if not isinstance(evidence_register, list):
                return False
            evidence_sha256 = canonical_json_sha256(
                {"evidence_register": evidence_register}
            )
            if payload.get("checkpoint_evidence_sha256") != evidence_sha256:
                return False
            return tail_review_is_current(
                payload.get("tail_review"),
                checkpoint,
                evidence_sha256=evidence_sha256,
            )
        required = {
            "planning": {"research_plan.json"},
            "hypothesis": {"scientific_hypothesis_state.json"},
            "experiment_design": {"design.json"},
            "experiment_result": {"record.json", "entry_result.json", "report.md"},
        }.get(stage)
        return bool(sources) if required is None else required <= names

    def _source_manifest(self, paths: list[Path]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in paths:
            stat = path.stat()
            rows.append(
                {
                    "source_ref": path.relative_to(self.workspace_root).as_posix(),
                    "bytes": stat.st_size,
                    "sha256": _file_sha256(path),
                }
            )
        return rows

    def _canonical_documents(self, paths: list[Path]) -> list[dict[str, Any]]:
        documents: list[dict[str, Any]] = []
        for path in paths:
            if path.suffix.casefold() != ".json":
                continue
            try:
                raw_bytes = path.read_bytes()
                payload = json.loads(raw_bytes.decode("utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            documents.append(
                {
                    "source_ref": path.relative_to(self.workspace_root).as_posix(),
                    "payload": payload,
                    "raw_bytes": raw_bytes,
                }
            )
        return documents

    def _current_harness_receipt_ref(self, paths: list[Path]) -> str | None:
        """Return the newest current-task Harness invocation receipt."""

        candidates: list[tuple[int, str]] = []
        prefix = self.workspace_root / "research_review" / "harness" / self.task_id
        for path in paths:
            try:
                resolved = path.resolve()
                relative = resolved.relative_to(self.workspace_root).as_posix()
            except ValueError:
                continue
            if (
                resolved.name != "receipt.json"
                or not resolved.is_file()
                or not resolved.is_relative_to(prefix.resolve())
            ):
                continue
            parts = relative.split("/")
            if len(parts) != 5 or parts[:2] != ["research_review", "harness"]:
                continue
            if parts[2] != self.task_id or parts[4] != "receipt.json":
                continue
            try:
                mtime_ns = resolved.stat().st_mtime_ns
            except OSError:
                continue
            candidates.append((mtime_ns, relative))
        return max(candidates, default=(0, None))[1]

    @staticmethod
    def _planner_dependency_steps(
        documents: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Preserve the Planner's validated route DAG without interpreting it."""

        for document in documents:
            payload = document.get("payload")
            if not isinstance(payload, dict):
                continue
            route = payload.get("research_route")
            if not isinstance(route, list):
                continue
            steps: list[dict[str, Any]] = []
            for raw in route:
                if not isinstance(raw, dict):
                    continue
                step_id = raw.get("id")
                stage = raw.get("stage")
                prerequisites = raw.get("prerequisite_step_ids")
                if (
                    isinstance(step_id, str)
                    and isinstance(stage, str)
                    and isinstance(prerequisites, list)
                    and all(isinstance(item, str) for item in prerequisites)
                ):
                    steps.append(
                        {
                            "step_id": step_id,
                            "stage": stage,
                            "prerequisite_step_ids": prerequisites,
                        }
                    )
            if steps:
                return steps
        return []

    def _upstream_refs_current(self, artifact: dict[str, Any]) -> bool:
        """Return True if every declared upstream_ref still resolves to an accepted
        artifact whose long ref (id@version:sha256) matches exactly. Used to detect
        stale bounded artifacts after a downstream-invalidating upstream re-run."""

        for ref in artifact.get("upstream_refs") or []:
            artifact_id, _, version_and_sha = ref.partition("@")
            _version_str, _, _sha = version_and_sha.partition(":")
            stage = artifact_id.replace("-artifact", "")
            accepted = self._accepted_stage(stage)
            if accepted is None or self._long_ref(accepted) != ref:
                return False
        return True

    def _dependencies_current(self, stage: str, artifact: dict[str, Any]) -> bool:
        """Reject accepted downstream artifacts bound to stale upstream hashes."""

        refs = set(artifact["upstream_refs"])
        required_stages = dict(STAGE_DEPENDENCIES)
        # A post-result hypothesis is intentionally newer than the design/result
        # it interprets; those accepted artifacts remain valid inputs rather than
        # becoming cyclic dependencies.
        required_stages["experiment_design"] = ("planning", "data")
        required_stages["experiment_result"] = (
            "planning",
            "data",
            "experiment_design",
        )
        for dependency in required_stages.get(stage, ()):
            upstream = self._accepted_stage(dependency)
            if upstream is None or self._long_ref(upstream) not in refs:
                return False
        if stage in {"experiment_design", "experiment_result"}:
            hypothesis_refs = [
                ref for ref in refs if ref.startswith("hypothesis-artifact@")
            ]
            if not hypothesis_refs:
                return False
            latest_hypothesis = self._accepted_stage("hypothesis")
            if latest_hypothesis is None:
                return False
            latest_phase = latest_hypothesis.get("payload", {}).get("phase")
            if (
                latest_phase != "hypothesis_update"
                and self._long_ref(latest_hypothesis) not in refs
            ):
                return False
        return True

    def checkpoint_producer_result(
        self,
        *,
        stage: str,
        producer: Producer,
        content: object,
        phase: str = "",
        require_canonical_source: bool = False,
        revision_review_id: str | None = None,
    ) -> dict[str, Any]:
        """Adapt a producer's v1 result into one immutable v2 artifact."""

        if PRODUCER_FOR_STAGE.get(stage) != producer:
            raise ValueError(f"{producer} does not own stage {stage}")
        text = _safe_content(content).strip()
        if not text:
            raise ValueError("producer result is empty")
        with self._transaction():
            previous = self.latest_artifact(stage)
            version = 1 if previous is None else previous["version"] + 1
            prior_revision_verdict = None
            if revision_review_id is not None:
                prior_revision_verdict = next(
                    (
                        verdict
                        for verdict in self.verdicts()
                        if verdict["review_id"] == revision_review_id
                    ),
                    None,
                )
                if prior_revision_verdict is None:
                    raise RuntimeError("revision_review_id does not identify a verdict")
                if (
                    prior_revision_verdict["decision"] != "revise"
                    or prior_revision_verdict["next_owner"] != producer
                ):
                    raise RuntimeError(
                        "revision_review_id is not a revise verdict owned by this producer"
                    )
            elif previous is not None:
                prior_revision_verdict = self.matching_verdict(
                    stage, [self.artifact_ref(previous)]
                )
            if (
                prior_revision_verdict is not None
                and prior_revision_verdict["decision"] != "revise"
            ):
                prior_revision_verdict = None
            canonical_sources = self._canonical_stage_sources(stage)
            if require_canonical_source and not self._canonical_stage_ready(
                stage, canonical_sources, phase=phase or stage
            ):
                raise RuntimeError(
                    f"{stage} returned without its complete task-local canonical v1 artifact"
                )
            evidence_refs = sorted(
                _producer_path_refs(text)
                | {
                    path.relative_to(self.workspace_root).as_posix()
                    for path in canonical_sources
                }
            )[:200]
            source_manifest = self._source_manifest(canonical_sources)
            adapted = adapt_v1_producer_output(
                stage=stage,
                version=version,
                phase=phase or stage,
                text=text,
                evidence_refs=evidence_refs,
                canonical_documents=self._canonical_documents(canonical_sources),
                current_task_id=self.task_id,
                current_harness_receipt_ref=self._current_harness_receipt_ref(
                    canonical_sources
                ),
                source_manifest=source_manifest,
            )
            artifact_evidence_refs = list(adapted.get("evidence_refs", evidence_refs))
            adapted["payload"]["source_manifest"] = source_manifest
            if (
                require_canonical_source
                and prior_revision_verdict is not None
                and previous is not None
                and adapted["payload"]["source_manifest"]
                == previous.get("payload", {}).get("source_manifest")
            ):
                raise RuntimeError(
                    "revision did not change any task-local canonical producer source"
                )
            if (
                previous is not None
                and prior_revision_verdict is None
                and _producer_semantics(previous)
                == _adapted_producer_semantics(
                    adapted,
                    evidence_refs=artifact_evidence_refs,
                    upstream_refs=self._producer_upstream_refs(stage, phase or stage),
                )
            ):
                # Model retries and duplicate handoffs frequently change only
                # rendered prose, timestamps, or the proposed artifact version.
                # Reuse the immutable artifact unless the scientific claim,
                # evidence surface, source manifest, phase, or upstream binding
                # actually changed.
                return previous
            if prior_revision_verdict is not None:
                adapted["payload"]["revision_response"] = build_revision_response(
                    task_id=self.task_id,
                    stage=stage,
                    producer=producer,
                    artifact_version=version,
                    prior_verdict=prior_revision_verdict,
                    acceptance_evidence=evidence_refs,
                )
            artifact = build_research_artifact(
                artifact_id=f"{stage}-artifact",
                task_id=self.task_id,
                stage=stage,
                version=version,
                producer=producer,
                upstream_refs=self._producer_upstream_refs(stage, phase or stage),
                claims=adapted["claims"],
                evidence_refs=artifact_evidence_refs,
                limitations=adapted["limitations"],
                payload=adapted["payload"],
            )
            path = (
                self.root
                / "artifacts"
                / artifact["artifact_id"]
                / f"v{version:04d}.json"
            )
            _atomic_write_json(path, artifact)
            state = self.load_state()
            rel = path.relative_to(self.workspace_root).as_posix()
            if rel not in state["artifacts"]:
                state["artifacts"].append(rel)
            if stage == "planning":
                state["dependency_graph"] = {
                    "schema_version": "research-dependency-graph-v2",
                    "source_ref": self._long_ref(artifact),
                    "stage_dependencies": {
                        name: list(dependencies)
                        for name, dependencies in STAGE_DEPENDENCIES.items()
                    },
                    "planner_steps": self._planner_dependency_steps(
                        self._canonical_documents(canonical_sources)
                    ),
                }
            state["current_stage"] = stage
            self._invalidate_downstream(state, stage, phase or stage)
            state["stage_status"][stage] = "produced"
            state["status"] = "active"
            self._save_state(state)
            return artifact

    def ensure_integration_artifact(self) -> dict[str, Any]:
        with self._transaction():
            accepted = self.accepted_artifacts()
            if len(accepted) < len(REVIEW_SEQUENCE):
                raise RuntimeError(
                    "integration requires every producer stage to be accepted"
                )
            refs = [self.artifact_ref(item) for item in accepted]
            existing = self.latest_artifact("integration")
            if (
                existing is not None
                and existing["payload"].get("artifact_refs") == refs
            ):
                return existing
            version = 1 if existing is None else existing["version"] + 1
            accepted_claim_ids: set[str] = set()
            carried_limits: set[str] = set()
            for item in accepted:
                verdict = self.matching_verdict(
                    item["stage"], [self.artifact_ref(item)]
                )
                if verdict is None:
                    raise RuntimeError(
                        f"accepted {item['stage']} artifact has no matching verdict"
                    )
                accepted_claim_ids.update(verdict["accepted_claims"])
                carried_limits.update(verdict["carry_forward_limits"])
                carried_limits.update(item["limitations"])
            artifact = build_research_artifact(
                artifact_id="integration-artifact",
                task_id=self.task_id,
                stage="integration",
                version=version,
                producer="supervisor",
                upstream_refs=self._upstream_refs(),
                claims=[
                    claim
                    for item in accepted
                    for claim in item["claims"]
                    if claim["claim_id"] in accepted_claim_ids
                    and not (
                        item["stage"] == "hypothesis"
                        and item.get("payload", {}).get("result_status")
                        in {"clarification_status", "blocked_status"}
                    )
                ],
                evidence_refs=sorted(
                    {ref for item in accepted for ref in item["evidence_refs"]}
                ),
                limitations=sorted(carried_limits),
                payload={"artifact_refs": refs},
            )
            path = (
                self.root
                / "artifacts"
                / artifact["artifact_id"]
                / f"v{version:04d}.json"
            )
            _atomic_write_json(path, artifact)
            state = self.load_state()
            rel = path.relative_to(self.workspace_root).as_posix()
            if rel not in state["artifacts"]:
                state["artifacts"].append(rel)
            state["stage_status"]["integration"] = "produced"
            state["stage_status"]["final_release"] = "pending"
            state["current_stage"] = "integration"
            self._save_state(state)
            return artifact

    def review_targets(self, mode: str) -> list[dict[str, Any]]:
        if mode == "integration":
            return [self.ensure_integration_artifact()]
        artifact = self.latest_artifact(mode)
        if artifact is None:
            raise RuntimeError(f"no {mode} artifact exists for review")
        return [artifact]

    @staticmethod
    def _harness_evidence_roles(artifact: Mapping[str, Any]) -> dict[str, list[str]]:
        payload = artifact.get("payload")
        payload = payload if isinstance(payload, Mapping) else {}
        harness = payload.get("harness_evidence")
        if not isinstance(harness, Mapping):
            return {}

        def _refs(field: str) -> list[str]:
            values = harness.get(field)
            if not isinstance(values, list):
                return []
            return sorted(
                {value for value in values if isinstance(value, str) and value.strip()}
            )

        return {
            "candidate_evidence_refs": _refs("candidate_evidence_refs"),
            "provenance_refs": _refs("provenance_refs"),
            "gap_refs": _refs("gap_refs"),
        }

    def _candidate_harness_support_refs(
        self, targets: list[dict[str, Any]]
    ) -> set[str]:
        candidates: set[str] = set()
        for artifact in self._artifact_closure(targets):
            roles = self._harness_evidence_roles(artifact)
            candidates.update(roles.get("candidate_evidence_refs", []))
        return candidates

    def review_context(self, mode: str) -> dict[str, Any]:
        targets = self.review_targets(mode)
        prior = self.verdicts(mode=mode)
        state = self.load_state()
        upstream_acceptance: list[dict[str, Any]] = []
        accepted_by_ref = {
            self._long_ref(artifact): artifact for artifact in self.accepted_artifacts()
        }
        for target in targets:
            for upstream_ref in target.get("upstream_refs", []):
                artifact = accepted_by_ref.get(upstream_ref)
                if artifact is None:
                    continue
                verdict = self.matching_verdict(
                    artifact["stage"], [self.artifact_ref(artifact)]
                )
                if verdict is None:
                    continue
                upstream_acceptance.append(
                    {
                        "artifact_ref": upstream_ref,
                        "stage": artifact["stage"],
                        "decision": verdict["decision"],
                        "accepted_claims": verdict["accepted_claims"],
                        "carry_forward_limits": verdict["carry_forward_limits"],
                        "interpretation": (
                            "The upstream artifact and its declared data/provenance "
                            "boundary passed Evidence review. Preserve its limits. "
                            "This acceptance does not by itself establish predictive "
                            "skill, a causal mechanism, or support for the current "
                            "stage's scientific claim."
                        ),
                    }
                )
        return {
            "schema_version": "research-review-context-v2",
            "task_id": self.task_id,
            "review_mode": mode,
            "policy_version": POLICY_VERSION,
            "policy_registry": policy_registry(stage=mode),
            # Qwen reviewers receive a compact, hash-bound projection here.  The
            # immutable registry keeps the complete artifact (including the
            # producer's often very long rendered report), while scientific
            # source content remains available through review_source.  Sending
            # producer_result again made the reviewer re-read tens of thousands
            # of redundant characters before it could inspect the canonical
            # evidence and materially increased timeout/loop risk.
            "artifacts": [self._review_artifact_projection(item) for item in targets],
            "upstream_acceptance": upstream_acceptance,
            "prior_verdicts": prior[-3:],
            "budget": {
                "revision_policy": state["revision_policy"],
                "max_revisions": state["max_revisions"],
                "no_progress_patience": state["no_progress_patience"],
                "action_invocations": state["action_invocations"],
                "max_action_invocations": state["max_action_invocations"],
                "review_invocations": state["review_invocations"],
                "max_review_invocations": state["max_review_invocations"],
            },
            "dependency_graph": state["dependency_graph"],
        }

    def _review_artifact_projection(self, artifact: dict[str, Any]) -> dict[str, Any]:
        """Return the minimal immutable artifact view needed to start review.

        This is deliberately not a replacement ResearchArtifactV2 and therefore
        carries an explicit projection schema.  The original artifact hash binds
        the full registry record; declared sources must be opened explicitly for
        detailed scientific inspection.
        """

        payload = artifact.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        metadata_fields = (
            "adapter_id",
            "source_schema_version",
            "canonical_source_refs",
            "phase",
            "source_manifest",
            "revision_response",
            "artifact_refs",
            "required_limits",
            "claim_citations",
        )
        payload_metadata = {
            key: payload[key] for key in metadata_fields if key in payload
        }
        harness_roles = self._harness_evidence_roles(artifact)
        if harness_roles:
            payload_metadata["harness_evidence_roles"] = harness_roles
        producer_result = payload.get("producer_result")
        omitted_chars = len(producer_result) if isinstance(producer_result, str) else 0
        return {
            "schema_version": "research-artifact-review-projection-v1",
            "artifact_id": artifact["artifact_id"],
            "task_id": artifact["task_id"],
            "stage": artifact["stage"],
            "version": artifact["version"],
            "producer": artifact["producer"],
            "upstream_refs": artifact["upstream_refs"],
            "claims": artifact["claims"],
            "evidence_refs": artifact["evidence_refs"],
            "limitations": artifact["limitations"],
            "created_at": artifact["created_at"],
            "artifact_sha256": artifact["artifact_sha256"],
            "payload_metadata": payload_metadata,
            "producer_result_omitted_chars": omitted_chars,
            "inspection_instruction": (
                "Open every material declared source with evidence_review_read_source; "
                "producer prose is not scientific evidence. Artifact and embedded "
                "domain-object hashes may use canonical JSON, while source-manifest "
                "and workspace-file SHA-256 values cover raw file bytes. Compare two "
                "hashes only when their declared representation is the same; different "
                "representations of one JSON document are not an integrity conflict."
            ),
        }

    def _artifact_closure(self, targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_ref = {self._long_ref(item): item for item in self.artifacts()}
        visible = list(targets)
        cursor = 0
        while cursor < len(visible):
            artifact = visible[cursor]
            cursor += 1
            refs = set(artifact["upstream_refs"])
            refs.update(artifact["evidence_refs"])
            for claim in artifact["claims"]:
                refs.update(claim["supporting_evidence"])
                refs.update(claim["opposing_evidence"])
                refs.update(claim.get("limiting_evidence", []))
            for ref in refs:
                upstream = by_ref.get(ref)
                if upstream is not None and upstream not in visible:
                    visible.append(upstream)
        return visible

    def _source_integrity_issues(
        self, targets: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        seen: set[str] = set()
        for artifact in self._artifact_closure(targets):
            manifest = artifact.get("payload", {}).get("source_manifest", [])
            if not isinstance(manifest, list):
                continue
            owner = artifact["producer"]
            if owner not in REVISION_OWNERS_FOR_MODE[artifact["stage"]]:
                owner = "main"
            for item in manifest:
                if not isinstance(item, dict):
                    continue
                source_ref = item.get("source_ref")
                expected = item.get("sha256")
                if not isinstance(source_ref, str) or source_ref in seen:
                    continue
                seen.add(source_ref)
                path = (self.workspace_root / source_ref.removeprefix("/")).resolve()
                try:
                    path.relative_to(self.workspace_root)
                except ValueError:
                    actual = None
                else:
                    actual = _file_sha256(path) if path.is_file() else None
                if actual == expected:
                    continue
                rule_id = "ARTIFACT_SOURCE_HASH_MISMATCH"
                issues.append(
                    {
                        "issue_id": f"source-integrity-{len(issues) + 1:03d}",
                        "rule_id": rule_id,
                        "severity": "critical",
                        "claim_ref": source_ref,
                        "evidence_refs": [source_ref],
                        "owner": owner,
                        "message": (
                            "A canonical producer source is missing or no longer "
                            "matches the SHA-256 frozen into ResearchArtifactV2."
                        ),
                        "required_action": (
                            "Create a new producer artifact from the current source; "
                            "the stale artifact and verdict cannot be reused."
                        ),
                        "acceptance_test": (
                            "The new artifact source_manifest matches every source "
                            "byte-for-byte at review time."
                        ),
                        "fingerprint": issue_fingerprint(rule_id, source_ref, owner),
                    }
                )
        return issues

    def _planning_route_closure_issues(
        self, targets: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Require the planner DAG to represent every global producer stage."""

        for artifact in targets:
            if artifact.get("stage") != "planning":
                continue
            manifest = artifact.get("payload", {}).get("source_manifest", [])
            if not isinstance(manifest, list):
                continue
            for source in manifest:
                if not isinstance(source, Mapping):
                    continue
                source_ref = source.get("source_ref")
                if not isinstance(source_ref, str) or not source_ref.endswith(
                    "/research_plan.json"
                ):
                    continue
                path = (self.workspace_root / source_ref).resolve()
                try:
                    path.relative_to(self.workspace_root)
                except ValueError:
                    continue
                if not path.is_file() or _file_sha256(path) != source.get("sha256"):
                    continue
                plan = _read_json(path)
                route = plan.get("research_route", []) if plan else []
                if not isinstance(route, list):
                    continue
                stages = [
                    str(step.get("stage") or "").strip().lower()
                    for step in route
                    if isinstance(step, Mapping)
                ]
                data_positions = [
                    index
                    for index, stage in enumerate(stages)
                    if stage in {"data", "data_preparation", "data_and_features"}
                ]
                design_positions = [
                    index
                    for index, stage in enumerate(stages)
                    if stage == "experiment_design"
                ]
                result_positions = [
                    index
                    for index, stage in enumerate(stages)
                    if stage in {"experiment_result", "experiment_run"}
                ]
                hypothesis_positions = [
                    index
                    for index, stage in enumerate(stages)
                    if stage
                    in {
                        "hypothesis",
                        "hypothesis_generation",
                        "hypothesis_update",
                    }
                ]
                closed = any(
                    data < hypothesis < design < result < update
                    for data in data_positions
                    for hypothesis in hypothesis_positions
                    for design in design_positions
                    for result in result_positions
                    for update in hypothesis_positions
                )
                if closed:
                    return []
                rule_id = "CROSS_STAGE_CLOSURE"
                claim_ref = "planning-plan-v1#research_route.stage_sequence"
                owner = "solar-planner"
                return [
                    {
                        "issue_id": "deterministic-planning-stage-closure",
                        "rule_id": rule_id,
                        "severity": "critical",
                        "claim_ref": claim_ref,
                        "evidence_refs": [source_ref],
                        "owner": owner,
                        "message": (
                            "The executable planner route does not contain an ordered "
                            "data -> hypothesis generation -> experiment design -> "
                            "experiment result -> hypothesis update chain. Observed "
                            f"stage sequence: {stages}."
                        ),
                        "required_action": (
                            "Add explicit, dependency-linked route steps for each "
                            "global producer checkpoint; do not combine experiment "
                            "design with execution or omit the pre-experiment hypothesis."
                        ),
                        "acceptance_test": (
                            "The frozen research_plan.json contains an ordered "
                            "data, hypothesis generation, experiment_design, "
                            "experiment_result, and hypothesis update path."
                        ),
                        "fingerprint": issue_fingerprint(rule_id, claim_ref, owner),
                    }
                ]
        return []

    def _deterministic_semantic_issues(
        self, mode: str, targets: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Return high-precision scientific defects that cannot be model-voted away."""

        if mode == "planning":
            return self._planning_route_closure_issues(targets)
        if mode == "data":
            return self._data_input_boundary_issues(targets)
        if mode not in {"hypothesis", "integration", "final_release"}:
            return []
        issues: list[dict[str, Any]] = []
        seen_claims: set[str] = set()
        for artifact in self._artifact_closure(targets):
            if artifact.get("stage") != "hypothesis":
                continue
            for claim in artifact.get("claims", []):
                if not isinstance(claim, Mapping):
                    continue
                claim_id = str(claim.get("claim_id") or "")
                text = str(claim.get("text") or "")
                if (
                    not claim_id
                    or claim_id in seen_claims
                    or _SAME_CYCLE_BMR_CAUSALITY.search(text) is None
                ):
                    continue
                seen_claims.add(claim_id)
                owner = "solar-hypothesis"
                rule_id = "TEMPORAL_CAUSAL_ORDER"
                issues.append(
                    {
                        "issue_id": f"deterministic-temporal-{claim_id}",
                        "rule_id": rule_id,
                        "severity": "major",
                        "claim_ref": claim_id,
                        "evidence_refs": list(claim.get("supporting_evidence", [])),
                        "owner": owner,
                        "message": (
                            "The claim makes a following cycle's amplitude depend on "
                            "that same cycle's own emerging BMR or tilt statistics. "
                            "Those emergences are descendants of the cycle's toroidal "
                            "field and cannot be used as its precursor cause."
                        ),
                        "required_action": (
                            "Index the mechanism explicitly: BMR and tilt fluctuations "
                            "during a prior cycle may alter the polar seed for the "
                            "following cycle. Otherwise narrow the claim to polar-field "
                            "or forecast-residual uncertainty."
                        ),
                        "acceptance_test": (
                            "No accepted claim attributes a cycle's already emerging "
                            "amplitude to that cycle's own BMR statistics."
                        ),
                        "fingerprint": issue_fingerprint(rule_id, claim_id, owner),
                    }
                )
        return issues

    def _manifest_json_source(
        self,
        manifest_by_ref: Mapping[str, Mapping[str, Any]],
        source_ref: str,
    ) -> dict[str, Any] | None:
        """Read one hash-matching task-local JSON source from an artifact manifest."""

        source = manifest_by_ref.get(source_ref)
        expected_sha256 = source.get("sha256") if isinstance(source, Mapping) else None
        if not isinstance(expected_sha256, str):
            return None
        path = (self.workspace_root / source_ref).resolve()
        if (
            not path.is_relative_to(self.workspace_root)
            or not path.is_file()
            or _file_sha256(path) != expected_sha256
        ):
            return None
        payload = _read_json(path)
        return payload if isinstance(payload, dict) else None

    def _silso_cycle_reproduction_defect(
        self,
        context: Mapping[str, Any],
        manifest_by_ref: Mapping[str, Mapping[str, Any]],
    ) -> str | None:
        """Return the first canonical SILSO reproduction defect, if any."""

        if "receipts/datasets/solar_precursor_cycle_table.json" in manifest_by_ref:
            return (
                "the artifact includes a polar-precursor product outside this protocol"
            )

        receipt_ref = "receipts/datasets/silso_cycle_extrema_reproduction.json"
        receipt = self._manifest_json_source(manifest_by_ref, receipt_ref)
        if receipt is None:
            return "the SILSO reproduction receipt is absent or stale"
        if not (
            receipt.get("schema_version") == "research-dataset-receipt-v1"
            and receipt.get("receipt_type") == "silso_cycle_extrema_reproduction"
            and receipt.get("analysis_protocol") == "silso_cycle_reproduction_v1"
            and receipt.get("status") == "verified"
            and receipt.get("cycle_numbers") == [21, 22, 23, 24]
            and receipt.get("row_count") == 4
        ):
            return "the SILSO reproduction receipt has invalid protocol metadata"

        expected_inputs = {
            str(item.get("dataset_id")): str(item.get("sha256"))
            for item in context.get("eligible_inputs", [])
            if isinstance(item, Mapping)
        }
        observed_inputs = {
            str(item.get("dataset_id")): str(item.get("sha256"))
            for item in receipt.get("inputs", [])
            if isinstance(item, Mapping)
        }
        required_inputs = (
            "silso-monthly-total-v2",
            "silso-monthly-smoothed-v2",
            "silso-cycle-extrema-v2",
        )
        if not all(
            observed_inputs.get(dataset_id) == expected_inputs.get(dataset_id)
            for dataset_id in required_inputs
        ):
            return "the SILSO reproduction receipt is not bound to all context hashes"

        outputs = receipt.get("outputs")
        if not isinstance(outputs, list):
            return "the SILSO reproduction receipt has no output manifest"
        output_by_ref = {
            str(item.get("path")): item for item in outputs if isinstance(item, Mapping)
        }
        required_outputs = {
            "work/solar_data/silso_cycle_extrema_comparison.csv",
            "work/solar_data/silso_cycle_extrema_comparison.json",
        }
        if set(output_by_ref) != required_outputs:
            return "the SILSO reproduction receipt lacks its canonical CSV or JSON"
        for output_ref, output in output_by_ref.items():
            output_path = (self.workspace_root / output_ref).resolve()
            output_sha256 = output.get("sha256")
            if not (
                output_path.is_relative_to(self.workspace_root)
                and output_path.is_file()
                and isinstance(output_sha256, str)
                and _file_sha256(output_path) == output_sha256
            ):
                return f"the SILSO output hash is stale: {output_ref}"

        json_path = self.workspace_root / (
            "work/solar_data/silso_cycle_extrema_comparison.json"
        )
        payload = _read_json(json_path)
        if not (
            isinstance(payload, dict)
            and payload.get("schema_version") == "silso-cycle-reproduction-v1"
            and payload.get("analysis_protocol") == "silso_cycle_reproduction_v1"
            and payload.get("cycles") == [21, 22, 23, 24]
            and isinstance(payload.get("method"), str)
            and payload.get("source") == "WDC-SILSO Sunspot Number Version 2.0"
        ):
            return "the SILSO comparison JSON has invalid source or protocol metadata"
        rows = payload.get("comparison")
        if not isinstance(rows, list) or len(rows) != 4:
            return "the SILSO comparison JSON does not contain exactly four cycles"
        try:
            if [int(row["cycle"]) for row in rows] != [21, 22, 23, 24]:
                return "the SILSO comparison cycles are not exactly 21 through 24"
            for row in rows:
                official_minimum = row["official_minimum"]
                official_maximum = row["official_maximum"]
                recomputed_minimum = row["recomputed_minimum"]
                recomputed_maximum = row["recomputed_maximum"]
                if not all(
                    isinstance(value, Mapping)
                    for value in (
                        official_minimum,
                        official_maximum,
                        recomputed_minimum,
                        recomputed_maximum,
                    )
                ):
                    raise TypeError
                expected_rise = (
                    (int(official_maximum["year"]) - int(official_minimum["year"])) * 12
                    + int(official_maximum["month"])
                    - int(official_minimum["month"])
                )
                if int(row["official_rise_months"]) != expected_rise:
                    return "an official SILSO rise time is inconsistent with its dates"
                for extremum in (official_minimum, official_maximum):
                    number = float(extremum["sunspot_number"])
                    if round(number, 1) != number:
                        return (
                            "an official SILSO value is not preserved at 0.1 precision"
                        )
                if not isinstance(
                    row["minimum_matches_official"], bool
                ) or not isinstance(row["maximum_matches_official"], bool):
                    return "the SILSO comparison lacks extrema consistency flags"
                if not str(row["difference_explanation"]).strip():
                    return "the SILSO comparison lacks a difference explanation"
        except (KeyError, TypeError, ValueError):
            return "the SILSO comparison rows do not satisfy the canonical schema"

        csv_path = self.workspace_root / (
            "work/solar_data/silso_cycle_extrema_comparison.csv"
        )
        try:
            with csv_path.open(encoding="utf-8", newline="") as handle:
                csv_rows = list(csv.DictReader(handle))
            if [int(row["cycle"]) for row in csv_rows] != [21, 22, 23, 24]:
                return "the SILSO comparison CSV cycles are not exactly 21 through 24"
            required_columns = {
                "official_minimum",
                "official_minimum_sn",
                "official_maximum",
                "official_maximum_sn",
                "official_rise_months",
                "recomputed_minimum",
                "recomputed_maximum",
                "recomputed_rise_months",
                "minimum_matches_official",
                "maximum_matches_official",
                "difference_explanation",
            }
            if not csv_rows or not required_columns <= set(csv_rows[0]):
                return "the SILSO comparison CSV lacks canonical columns"
        except (KeyError, TypeError, ValueError):
            return "the SILSO comparison CSV is not parseable"
        return None

    def accepted_bounded_markdown(
        self,
        stage: str,
        *,
        analysis_protocol: str = "none",
    ) -> str | None:
        """Return the accepted bounded answer, deterministically when required."""

        artifact = self._accepted_stage(stage)
        if artifact is None:
            return None
        producer_result = artifact.get("payload", {}).get("producer_result")
        if stage == "hypothesis":
            if not isinstance(producer_result, str) or not producer_result.strip():
                return None
            verdict = self.matching_verdict(stage, [self.artifact_ref(artifact)])
            if verdict is None:
                return None
            assessment = next(
                (
                    row
                    for row in reversed(self.assessments(mode=stage))
                    if row["round"] == verdict["round"]
                    and row["artifact_refs"] == verdict["artifact_refs"]
                ),
                None,
            )
            quality = next(
                (
                    row
                    for row in reversed(self.scientific_quality_assessments(mode=stage))
                    if row["round"] == verdict["round"]
                    and row["artifact_refs"] == verdict["artifact_refs"]
                ),
                None,
            )
            rendered = self._accepted_hypothesis_markdown(
                producer_result,
                verdict=verdict,
                assessment=assessment,
                quality=quality,
            )
            self._mark_bounded_result_released(stage)
            return rendered
        if analysis_protocol != "silso_cycle_reproduction_v1":
            if not isinstance(producer_result, str):
                return None
            self._mark_bounded_result_released(stage)
            return producer_result

        manifest = artifact.get("payload", {}).get("source_manifest", [])
        if not isinstance(manifest, list):
            return None
        manifest_by_ref = {
            str(item.get("source_ref")): item
            for item in manifest
            if isinstance(item, Mapping) and isinstance(item.get("source_ref"), str)
        }
        context: dict[str, Any] | None = None
        for source_ref in manifest_by_ref:
            if not (
                source_ref.startswith("receipts/datasets/data-context-")
                and source_ref.endswith(".json")
            ):
                continue
            candidate = self._manifest_json_source(manifest_by_ref, source_ref)
            if candidate and (
                candidate.get("required_data_product")
                == SILSO_CYCLE_EXTREMA_DATA_PRODUCT
            ):
                context = candidate
                break
        if (
            context is None
            or self._silso_cycle_reproduction_defect(context, manifest_by_ref)
            is not None
        ):
            return None

        from jw.research_protocols import render_silso_cycle_reproduction_markdown

        payload = _read_json(
            self.workspace_root / "work/solar_data/silso_cycle_extrema_comparison.json"
        )
        if not isinstance(payload, Mapping):
            return None
        try:
            rendered = render_silso_cycle_reproduction_markdown(payload)
        except (KeyError, TypeError, ValueError):
            return None
        self._mark_bounded_result_released(stage)
        return rendered

    def _mark_bounded_result_released(self, stage: str) -> None:
        state = self.load_state()
        state["status"] = "released"
        state["current_stage"] = stage
        self._save_state(state)

    @staticmethod
    def _accepted_hypothesis_markdown(
        producer_result: str,
        *,
        verdict: Mapping[str, Any],
        assessment: Mapping[str, Any] | None,
        quality: Mapping[str, Any] | None,
    ) -> str:
        """Append the accepted Evidence assessment to the reviewed hypothesis."""

        decision_labels = {
            "accept": "接受",
            "accept_with_limits": "接受，但保留限制",
        }
        disposition_labels = {
            "supported": "支持",
            "limited_support": "有限支持",
            "opposed": "受到反对证据挑战",
            "contradicted": "受到证据否定",
            "undecided": "证据不足",
        }
        confidence_labels = {
            "high": "高",
            "moderate": "中等",
            "low": "低",
            "very_low": "很低",
            "unknown": "未知",
        }
        component_labels = {
            "statement": "主张",
            "mechanism": "机制",
            "prediction": "预测",
            "scope": "适用范围",
            "numeric_result": "数值结果",
            "conclusion": "结论",
            "unknown": "待界定部分",
        }
        role_labels = {
            "supports": "支持",
            "opposes": "反对",
            "limits": "限制",
            "gap": "缺口",
        }
        novelty_labels = {
            "known_baseline": "已知基线",
            "incremental_extension": "增量扩展",
            "potentially_novel": "可能具有新意",
            "novelty_not_assessed": "尚未评估原创性",
        }
        lines = [producer_result.rstrip(), "", "## 独立证据审查", ""]
        lines.append(
            "- 审查结论："
            + decision_labels.get(str(verdict["decision"]), str(verdict["decision"]))
            + "。"
        )
        if assessment is not None:
            for row in assessment["claims"]:
                disposition = disposition_labels.get(
                    str(row["disposition"]), str(row["disposition"])
                )
                confidence = confidence_labels.get(
                    str(row["confidence"]), str(row["confidence"])
                )
                lines.append(
                    f"- 主张评估：{disposition}；置信度：{confidence}；"
                    f"主要不确定性：{row['key_uncertainty']}；"
                    f"下一步检验：{row['next_test']}"
                )
        if quality is not None:
            rows = quality["claims"]
            component_summaries = []
            for row in rows:
                component = component_labels.get(
                    str(row["claim_component"]), str(row["claim_component"])
                )
                component_summaries.append(
                    f"{component}：{row['quality_status']}，结论上限 {row['conclusion_cap']}"
                )
            if component_summaries:
                lines.extend(("", "### 主张分量与证据上限", ""))
                lines.extend(f"- {item}" for item in component_summaries)

            seen_evidence: set[tuple[str, ...]] = set()
            evidence_rows: list[Mapping[str, Any]] = []
            for row in rows:
                for evidence in row["evidence_matrix"]:
                    key = (
                        str(evidence["source_ref"]),
                        str(evidence["evidence_role"]),
                        str(evidence["locator"]),
                        str(evidence["rationale"]),
                    )
                    if key not in seen_evidence:
                        seen_evidence.add(key)
                        evidence_rows.append(evidence)
            if evidence_rows:
                lines.extend(("", "### 证据矩阵", ""))
                for evidence in evidence_rows:
                    role = role_labels.get(
                        str(evidence["evidence_role"]),
                        str(evidence["evidence_role"]),
                    )
                    lines.append(
                        f"- {role}｜{evidence['source_class']}｜"
                        f"{evidence['evidence_scope']}｜{evidence['scope_match']}："
                        f"{evidence['locator']}。{evidence['rationale']}"
                    )

            novelty = rows[0]["novelty_assessment"] if rows else None
            if novelty is not None:
                lines.extend(("", "### 原创性边界", ""))
                novelty_status = novelty_labels.get(
                    str(novelty["status"]), str(novelty["status"])
                )
                lines.append(
                    f"- 定位：{novelty_status}；贡献类型：{novelty['contribution_type']}。"
                )
                lines.append(f"- 可核查增量：{novelty['novelty_delta']}")
                for prior in novelty["nearest_prior_art"]:
                    lines.append(
                        f"- 最近既有工作：{prior['source_ref']}；"
                        f"重叠：{prior['overlap']}；差异：{prior['difference']}；"
                        f"重复风险：{prior['duplication_risk']}"
                    )
                for gap in novelty["coverage_gaps"]:
                    lines.append(f"- 检索缺口：{gap}")

        limits = list(verdict["carry_forward_limits"])
        if limits:
            lines.extend(("", "### 结论限制", ""))
            lines.extend(f"- {limit}" for limit in limits)
        return "\n".join(lines).strip() + "\n"

    def _data_input_boundary_issues(
        self, targets: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Enforce the hash-bound Data input and specialized output boundary.

        The Data producer may honestly report that the accepted plan has no
        task-bound immutable inputs. That blocker artifact is valid audit
        evidence, but it is not a valid data product for downstream stages.
        When the curated SILSO and MWO/WSO pair is available, the producer must
        also persist the verified cycle table; a context receipt alone cannot
        be accepted as completed data preparation.
        """

        authoritative_context = self._authoritative_data_context(targets)
        for artifact in targets:
            if artifact.get("stage") != "data":
                continue
            manifest = artifact.get("payload", {}).get("source_manifest", [])
            artifact_phase = str(artifact.get("payload", {}).get("phase") or "")
            if not isinstance(manifest, list):
                continue
            manifest_by_ref = {
                str(item.get("source_ref")): item
                for item in manifest
                if isinstance(item, Mapping) and isinstance(item.get("source_ref"), str)
            }
            specialized_context: tuple[str, dict[str, Any]] | None = None
            for source in manifest:
                if not isinstance(source, Mapping):
                    continue
                source_ref = source.get("source_ref")
                expected_sha256 = source.get("sha256")
                if not (
                    isinstance(source_ref, str)
                    and source_ref.startswith("receipts/datasets/data-context-")
                    and source_ref.endswith(".json")
                    and isinstance(expected_sha256, str)
                ):
                    continue
                path = (self.workspace_root / source_ref).resolve()
                try:
                    path.relative_to(self.workspace_root)
                except ValueError:
                    continue
                if not path.is_file() or _file_sha256(path) != expected_sha256:
                    # Source-integrity preflight owns missing or stale receipts.
                    continue
                context = _read_json(path)
                if not (
                    context
                    and context.get("schema_version") == "solar-data-context-v1"
                    and context.get("task_id") == self.task_id
                ):
                    continue
                if context.get("context_mode") == "full_research":
                    plan_ref = context.get("plan_source_ref")
                    plan = (
                        self._manifest_json_source(manifest_by_ref, plan_ref)
                        if isinstance(plan_ref, str)
                        else None
                    )
                    if plan is None and isinstance(plan_ref, str):
                        plan_path = (self.workspace_root / plan_ref).resolve()
                        if (
                            plan_path.is_relative_to(self.workspace_root)
                            and plan_path.is_file()
                            and context.get("plan_sha256") == _file_sha256(plan_path)
                        ):
                            candidate_plan = _read_json(plan_path)
                            plan = (
                                candidate_plan
                                if isinstance(candidate_plan, dict)
                                else None
                            )
                    protocol = context.get("analysis_protocol")
                    if (
                        isinstance(plan, Mapping)
                        and isinstance(protocol, str)
                        and plan_dataset_selection_conflicts_protocol(plan, protocol)
                    ):
                        rule_id = "PLAN_DATASET_PROTOCOL_CONFLICT"
                        claim_ref = f"{source_ref}#plan-dataset-conflict"
                        owner = "solar-planner"
                        return [
                            {
                                "issue_id": "deterministic-plan-dataset-conflict",
                                "rule_id": rule_id,
                                "severity": "major",
                                "claim_ref": claim_ref,
                                "evidence_refs": [source_ref, str(plan_ref)],
                                "owner": owner,
                                "message": (
                                    "The accepted Planning dataset selections conflict "
                                    "with the task-bound analysis protocol mapping."
                                ),
                                "required_action": (
                                    "Revise the accepted plan's selected_source_id "
                                    "values to match the protocol or explicitly change "
                                    "the protocol before reopening Data."
                                ),
                                "acceptance_test": (
                                    "A replacement Planning artifact contains a "
                                    "protocol-consistent dataset selection set."
                                ),
                                "fingerprint": issue_fingerprint(
                                    rule_id, claim_ref, owner
                                ),
                            }
                        ]
                context_is_authoritative = self._data_context_is_authoritative(
                    source_ref, context, phase=artifact_phase
                )
                if (
                    context.get("context_mode") == "full_research"
                    and not context_is_authoritative
                    and authoritative_context is None
                ):
                    rule_id = "DATA_SEMANTICS_BOUND"
                    claim_ref = f"{source_ref}#authority"
                    owner = "solar-data"
                    return [
                        {
                            "issue_id": "deterministic-data-context-authority",
                            "rule_id": rule_id,
                            "severity": "critical",
                            "claim_ref": claim_ref,
                            "evidence_refs": [source_ref],
                            "owner": owner,
                            "message": (
                                "The full-research Data context does not pass its "
                                "task, manifest, question, plan, filename, and hash "
                                "authority checks."
                            ),
                            "required_action": (
                                "Open a new canonical Data context from the accepted "
                                "plan and checkpoint the replacement Data artifact."
                            ),
                            "acceptance_test": (
                                "The replacement context passes the complete authority "
                                "validation and is bound to the current task inputs."
                            ),
                            "fingerprint": issue_fingerprint(rule_id, claim_ref, owner),
                        }
                    ]
                if (
                    context_is_authoritative
                    and self._data_context_confirms_required_inputs_missing(context)
                ):
                    rule_id = "REQUIRED_DATA_INPUT_UNAVAILABLE"
                    claim_ref = f"{source_ref}#status"
                    owner = "main"
                    return [
                        {
                            "issue_id": "deterministic-data-input-missing",
                            "rule_id": rule_id,
                            "severity": "critical",
                            "claim_ref": claim_ref,
                            "evidence_refs": [source_ref],
                            "owner": owner,
                            "message": (
                                "The hash-bound Data context proves that no eligible "
                                "immutable input is bound to the task. This is an "
                                "honest terminal blocker, not an acceptable data product."
                            ),
                            "required_action": (
                                "Bind the plan-required datasets with provenance and "
                                "SHA-256, then produce a new Data artifact from a new "
                                "inputs_available context receipt."
                            ),
                            "acceptance_test": (
                                "The replacement Data artifact references a hash-bound "
                                "context whose status is inputs_available and whose "
                                "eligible inputs satisfy the accepted plan."
                            ),
                            "fingerprint": issue_fingerprint(rule_id, claim_ref, owner),
                        }
                    ]
                if context.get("required_data_product") in {
                    SILSO_CYCLE_EXTREMA_DATA_PRODUCT,
                    SOLAR_POLAR_PRECURSOR_DATA_PRODUCT,
                }:
                    specialized_context = (source_ref, context)

            if specialized_context is None:
                continue

            context_ref, context = specialized_context
            required_product = context.get("required_data_product")
            if required_product == SILSO_CYCLE_EXTREMA_DATA_PRODUCT:
                defect = self._silso_cycle_reproduction_defect(context, manifest_by_ref)
                if defect is None:
                    continue
                rule_id = "DATA_SEMANTICS_BOUND"
                claim_ref = (
                    "receipts/datasets/silso_cycle_extrema_reproduction.json"
                    "#verified-cycle-extrema"
                )
                owner = "solar-data"
                return [
                    {
                        "issue_id": "deterministic-silso-cycle-reproduction",
                        "rule_id": rule_id,
                        "severity": "critical",
                        "claim_ref": claim_ref,
                        "evidence_refs": [context_ref],
                        "owner": owner,
                        "message": f"The bounded SILSO reproduction is incomplete: {defect}.",
                        "required_action": (
                            "Run reproduce_silso_cycle_extrema for cycles 21-24 on "
                            "the exact context-bound monthly-total, monthly-smoothed, "
                            "and official extrema inputs. Persist its canonical CSV, "
                            "JSON, and receipt without adding polar-field products."
                        ),
                        "acceptance_test": (
                            "The replacement Data artifact binds all three SILSO "
                            "input hashes and a live four-cycle comparison containing "
                            "official and recomputed extrema, rise times, consistency "
                            "flags, and difference explanations."
                        ),
                        "fingerprint": issue_fingerprint(rule_id, claim_ref, owner),
                    }
                ]

            receipt_ref = "receipts/datasets/solar_precursor_cycle_table.json"
            receipt_source = manifest_by_ref.get(receipt_ref)
            defect = "the specialized cycle-table receipt is absent"
            receipt: dict[str, Any] | None = None
            if isinstance(receipt_source, Mapping) and isinstance(
                receipt_source.get("sha256"), str
            ):
                receipt_path = (self.workspace_root / receipt_ref).resolve()
                if (
                    receipt_path.is_relative_to(self.workspace_root)
                    and receipt_path.is_file()
                    and _file_sha256(receipt_path) == receipt_source["sha256"]
                ):
                    receipt = _read_json(receipt_path)
                    defect = "the specialized cycle-table receipt is invalid"

            schema = receipt.get("schema_version") if receipt else None
            valid_v1 = bool(
                receipt
                and schema == "solar-precursor-cycle-table-v1"
                and receipt.get("status") == "verified"
                and receipt.get("row_count") == 10
                and receipt.get("cycle_numbers") == list(range(15, 25))
            )
            requested_pairs = [f"{cycle}->{cycle + 1}" for cycle in range(14, 24)]
            pair_coverage = receipt.get("pair_coverage") if receipt else None
            valid_v2 = bool(
                receipt
                and schema == "solar-precursor-cycle-table-v2"
                and receipt.get("receipt_type") == "solar_precursor_cycle_table"
                and receipt.get("status") == "verified"
                and receipt.get("producer") == "solar-data"
                and receipt.get("task_id") == self.task_id
                and receipt.get("row_count") == 11
                and receipt.get("cycle_numbers") == list(range(14, 25))
                and receipt.get("analysis_cycle_numbers") == list(range(15, 25))
                and receipt.get("boundary_cycle_numbers") == [14]
                and isinstance(pair_coverage, Mapping)
                and pair_coverage.get("requested_pairs") == requested_pairs
                and pair_coverage.get("available_pairs") == requested_pairs
                and pair_coverage.get("unavailable_pairs") == []
            )
            valid = valid_v1 or valid_v2
            if valid and receipt is not None:
                expected_inputs = {
                    str(item.get("dataset_id")): str(item.get("sha256"))
                    for item in context.get("eligible_inputs", [])
                    if isinstance(item, Mapping)
                }
                observed_inputs = {
                    str(item.get("dataset_id")): str(item.get("sha256"))
                    for item in receipt.get("input_refs", [])
                    if isinstance(item, Mapping)
                }
                valid = all(
                    observed_inputs.get(dataset_id) == expected_inputs.get(dataset_id)
                    for dataset_id in (
                        "silso-monthly-total-v2",
                        "mwo-wso-polar-field-v2",
                    )
                )
                if not valid:
                    defect = (
                        "the cycle-table receipt is not bound to the context hashes"
                    )

            if valid and receipt is not None:
                outputs = receipt.get("outputs")
                output = (
                    outputs[0]
                    if isinstance(outputs, list) and len(outputs) == 1
                    else None
                )
                output_ref = output.get("path") if isinstance(output, Mapping) else None
                output_sha = (
                    output.get("sha256") if isinstance(output, Mapping) else None
                )
                output_bytes = (
                    output.get("bytes") if isinstance(output, Mapping) else None
                )
                output_path = (
                    (self.workspace_root / output_ref).resolve()
                    if isinstance(output_ref, str)
                    else None
                )
                valid = bool(
                    output_path
                    and output_path.is_relative_to(self.workspace_root)
                    and output_path.is_file()
                    and isinstance(output_sha, str)
                    and _file_sha256(output_path) == output_sha
                    and (
                        schema != "solar-precursor-cycle-table-v2"
                        or output_bytes == output_path.stat().st_size
                    )
                )
                if not valid:
                    defect = "the cycle-table output hash or path is stale"

            if valid and output_path is not None:
                try:
                    with output_path.open(encoding="utf-8", newline="") as handle:
                        rows = list(csv.DictReader(handle))
                    if schema == "solar-precursor-cycle-table-v2":
                        valid = len(rows) == 11 and [
                            int(row["cycle_number"]) for row in rows
                        ] == list(range(14, 25))
                        valid = valid and rows[0].get("row_role") == "boundary"
                        analysis_rows = rows[1:]
                    else:
                        valid = len(rows) == 10 and [
                            int(row["cycle_number"]) for row in rows
                        ] == list(range(15, 25))
                        analysis_rows = rows
                    valid = valid and all(
                        float(row["north_measurement_date"])
                        <= float(row["predictor_cutoff_decimal_year"])
                        and float(row["south_measurement_date"])
                        <= float(row["predictor_cutoff_decimal_year"])
                        for row in analysis_rows
                    )
                except (KeyError, TypeError, ValueError):
                    valid = False
                if not valid:
                    defect = (
                        "the cycle table violates cycle coverage or pre-minimum cutoffs"
                    )

            if not valid:
                rule_id = "DATA_SEMANTICS_BOUND"
                claim_ref = f"{receipt_ref}#verified-cycle-table"
                owner = "solar-data"
                return [
                    {
                        "issue_id": "deterministic-solar-precursor-table",
                        "rule_id": rule_id,
                        "severity": "critical",
                        "claim_ref": claim_ref,
                        "evidence_refs": [context_ref, receipt_ref],
                        "owner": owner,
                        "message": (
                            "The curated polar-precursor Data stage is incomplete: "
                            f"{defect}."
                        ),
                        "required_action": (
                            "Run prepare_solar_precursor_cycle_table on the exact "
                            "eligible SILSO and MWO/WSO paths and persist its verified "
                            "receipt and hash-bound cycles 15-24 table."
                        ),
                        "acceptance_test": (
                            "The replacement Data artifact binds both context input "
                            "hashes, a live verified output hash, exactly cycles 15-24, "
                            "and no polar measurement later than its nominal minimum."
                        ),
                        "fingerprint": issue_fingerprint(rule_id, claim_ref, owner),
                    }
                ]
        return []

    @staticmethod
    def _data_context_confirms_required_inputs_missing(
        context: Mapping[str, Any],
    ) -> bool:
        """Trust only explicit missing IDs/must-stop, plus legacy empty v1 blockers."""

        missing = context.get("missing_required_dataset_ids")
        if isinstance(missing, list) and any(
            isinstance(item, str) and item.strip() for item in missing
        ):
            return True
        if context.get("must_stop") is True:
            return True
        has_authoritative_fields = (
            "required_dataset_ids" in context
            or "missing_required_dataset_ids" in context
            or "must_stop" in context
        )
        eligible = context.get("eligible_inputs")
        return (
            not has_authoritative_fields
            and context.get("schema_version") == "solar-data-context-v1"
            and context.get("status") == "input_missing"
            and isinstance(eligible, list)
            and not eligible
        )

    def _data_context_is_authoritative(
        self,
        source_ref: str,
        context: Mapping[str, Any],
        *,
        phase: str,
    ) -> bool:
        """Validate current contexts strictly while retaining old empty v1 stops."""

        if (
            context.get("schema_version") != "solar-data-context-v1"
            or context.get("task_id") != self.task_id
        ):
            return False
        context_mode = context.get("context_mode")
        bounded_phase = phase.startswith("bounded_data")
        if context_mode != "full_research" and not bounded_phase:
            return False
        context_sha256 = context.get("context_sha256")
        if not isinstance(context_sha256, str):
            return (
                bounded_phase
                and context_mode in {None, "bounded_data"}
                and context.get("status") == "input_missing"
                and context.get("eligible_inputs") == []
                and "required_dataset_ids" not in context
                and "missing_required_dataset_ids" not in context
                and "must_stop" not in context
            )
        body = {
            key: value
            for key, value in context.items()
            if key not in _DATA_CONTEXT_TRANSIENT_FIELDS
        }
        if (
            canonical_json_sha256(body) != context_sha256
            or source_ref
            != f"receipts/datasets/data-context-{context_sha256[:16]}.json"
        ):
            return False
        task_path = self.workspace_root / "task.json"
        manifest_path = self.workspace_root / "input_manifest.json"
        task = _read_json(task_path)
        if not (
            isinstance(task, dict)
            and task.get("thread_id") == self.task_id
            and manifest_path.is_file()
            and context.get("task_sha256") == _file_sha256(task_path)
            and context.get("input_manifest_sha256") == _file_sha256(manifest_path)
        ):
            return False
        question = task.get("research_question")
        if not (
            isinstance(question, str)
            and context.get("research_question_sha256")
            == hashlib.sha256(question.encode("utf-8")).hexdigest()
        ):
            return False
        if context_mode == "bounded_data":
            return bounded_phase and context.get("data_steps") in (None, [])
        if context_mode != "full_research":
            return False
        current_manifest_records = self._current_manifest_input_records()
        if current_manifest_records is None:
            return False
        if context.get("eligible_inputs") != current_manifest_records:
            return False
        plan_ref = context.get("plan_source_ref")
        if (
            not isinstance(plan_ref, str)
            or not plan_ref
            or Path(plan_ref).is_absolute()
        ):
            return False
        unresolved_plan = self.workspace_root / plan_ref
        plan_path = unresolved_plan.resolve()
        if (
            unresolved_plan.is_symlink()
            or not plan_path.is_relative_to(self.workspace_root)
            or not plan_path.is_file()
            or context.get("plan_sha256") != _file_sha256(plan_path)
        ):
            return False
        plan = _read_json(plan_path)
        if not (
            isinstance(plan, dict)
            and plan.get("schema_version") == "research-plan-v1"
            and plan.get("research_question") == question
        ):
            return False
        planning = self._accepted_stage("planning")
        if planning is None:
            return False
        planning_ref = self.artifact_ref(planning)
        if context.get("planning_artifact_ref") != planning_ref:
            return False
        planning_verdict = self.matching_verdict("planning", [planning_ref])
        if planning_verdict is None or planning_verdict.get("decision") not in {
            "accept",
            "accept_with_limits",
        }:
            return False
        if context.get("planning_verdict_ref") != {
            "review_id": planning_verdict.get("review_id"),
            "verdict_sha256": planning_verdict.get("verdict_sha256"),
        }:
            return False
        planning_manifest = planning.get("payload", {}).get("source_manifest", [])
        plan_row = next(
            (
                row
                for row in planning_manifest
                if isinstance(row, Mapping) and row.get("source_ref") == plan_ref
            ),
            None,
        )
        if not isinstance(plan_row, Mapping) or plan_row.get("sha256") != context.get(
            "plan_sha256"
        ):
            return False
        analysis_protocol = context.get("analysis_protocol")
        if not isinstance(
            analysis_protocol, str
        ) or analysis_protocol != detect_analysis_protocol(question):
            return False
        try:
            expected_required_dataset_ids = list(
                resolve_required_dataset_ids(plan, analysis_protocol)
            )
        except ValueError:
            return False
        if (
            context.get("required_data_product")
            != required_data_product_for_protocol(analysis_protocol)
            or context.get("required_dataset_ids") != expected_required_dataset_ids
        ):
            return False
        eligible_inputs = current_manifest_records
        available_dataset_ids = {
            str(item.get("dataset_id"))
            for item in eligible_inputs
            if isinstance(item, Mapping) and isinstance(item.get("dataset_id"), str)
        }
        expected_missing_dataset_ids = [
            dataset_id
            for dataset_id in expected_required_dataset_ids
            if dataset_id not in available_dataset_ids
        ]
        expected_must_stop = bool(expected_missing_dataset_ids) or (
            not eligible_inputs and not expected_required_dataset_ids
        )
        if (
            context.get("missing_required_dataset_ids") != expected_missing_dataset_ids
            or context.get("must_stop") is not expected_must_stop
            or context.get("status")
            != ("input_missing" if expected_must_stop else "inputs_available")
        ):
            return False
        data_steps = [
            step
            for step in plan.get("research_route", [])
            if isinstance(step, dict) and step.get("stage") == "data"
        ]
        return bool(data_steps) and context.get("data_steps") == data_steps

    def _authoritative_data_context(
        self, targets: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        candidates: list[tuple[str, str, dict[str, Any]]] = []
        for artifact in targets:
            if artifact.get("stage") != "data":
                continue
            payload = artifact.get("payload", {})
            manifest = payload.get("source_manifest", [])
            phase = str(payload.get("phase") or "")
            for item in manifest if isinstance(manifest, list) else []:
                if not isinstance(item, Mapping):
                    continue
                source_ref = item.get("source_ref")
                expected_sha256 = item.get("sha256")
                if not (
                    isinstance(source_ref, str)
                    and source_ref.startswith("receipts/datasets/data-context-")
                    and source_ref.endswith(".json")
                    and isinstance(expected_sha256, str)
                ):
                    continue
                path = (self.workspace_root / source_ref).resolve()
                if (
                    not path.is_relative_to(self.workspace_root)
                    or not path.is_file()
                    or _file_sha256(path) != expected_sha256
                ):
                    continue
                context = _read_json(path)
                if not (
                    isinstance(context, dict)
                    and self._data_context_is_authoritative(
                        source_ref, context, phase=phase
                    )
                ):
                    continue
                candidates.append(
                    (str(context.get("created_at") or ""), source_ref, context)
                )
        return max(candidates)[2] if candidates else None

    def _recover_misapplied_data_input_issues(
        self,
        mode: str,
        targets: list[dict[str, Any]],
        issues: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], bool]:
        if mode != "data":
            return issues, False
        context = self._authoritative_data_context(targets)
        if context is not None and self._data_context_confirms_required_inputs_missing(
            context
        ):
            return issues, False
        normalized: list[dict[str, Any]] = []
        recovered = False
        for raw in issues:
            if raw.get("rule_id") != "REQUIRED_DATA_INPUT_UNAVAILABLE":
                normalized.append(raw)
                continue
            issue = dict(raw)
            issue.update(
                {
                    "rule_id": "DATA_SEMANTICS_BOUND",
                    "owner": "solar-data",
                    "message": (
                        "The authoritative Data context does not prove a missing "
                        "required dataset; the requested Data scope or semantics "
                        "must be revised without creating a permanent input block."
                    ),
                    "required_action": (
                        "Revise the Data product against the accepted scope and the "
                        "task-bound required dataset IDs."
                    ),
                    "acceptance_test": (
                        "The revised Data artifact follows the accepted cycle scope, "
                        "uses the context-bound datasets, and records remaining gaps."
                    ),
                }
            )
            issue["fingerprint"] = issue_fingerprint(
                "DATA_SEMANTICS_BOUND", str(issue.get("claim_ref") or ""), "solar-data"
            )
            normalized.append(issue)
            recovered = True
        return normalized, recovered

    def persist_deterministic_preflight_verdict(
        self, mode: str
    ) -> dict[str, Any] | None:
        """Persist a deterministic verdict for narrow high-precision contracts.

        This runs at the Evidence tool boundary before a remote reviewer call.
        Most artifacts still require the isolated model reviewer. The bounded
        SILSO reproduction is an exception because its complete claim surface
        is covered by hash, schema, range, precision, and consistency checks;
        accepting it here prevents a model reviewer from expanding the protocol
        or rewriting the verified values.
        """

        targets = self.review_targets(mode)
        refs = [self.artifact_ref(item) for item in targets]
        if self.matching_verdict(mode, refs) is not None:
            return None
        issues = self._deterministic_semantic_issues(mode, targets)
        if not issues:
            if mode == "data":
                for artifact in targets:
                    manifest = artifact.get("payload", {}).get("source_manifest", [])
                    if not isinstance(manifest, list):
                        continue
                    manifest_by_ref = {
                        str(item.get("source_ref")): item
                        for item in manifest
                        if isinstance(item, Mapping)
                        and isinstance(item.get("source_ref"), str)
                    }
                    silso_context = any(
                        (
                            context := self._manifest_json_source(
                                manifest_by_ref, source_ref
                            )
                        )
                        and context.get("required_data_product")
                        == SILSO_CYCLE_EXTREMA_DATA_PRODUCT
                        for source_ref in manifest_by_ref
                        if source_ref.startswith("receipts/datasets/data-context-")
                    )
                    if silso_context:
                        accepted_claims = sorted(
                            claim["claim_id"]
                            for item in targets
                            for claim in item.get("claims", [])
                            if isinstance(claim, Mapping)
                            and isinstance(claim.get("claim_id"), str)
                        )
                        return self.submit_verdict(
                            mode=mode,
                            decision="accept",
                            issues=[],
                            accepted_claims=accepted_claims,
                        )
            return None
        claim_ids = {
            claim["claim_id"]
            for item in targets
            for claim in item.get("claims", [])
            if isinstance(claim, Mapping) and isinstance(claim.get("claim_id"), str)
        }
        blocked_claims = sorted(
            {
                issue["claim_ref"]
                for issue in issues
                if issue.get("claim_ref") in claim_ids
            }
        )
        limitations = sorted(
            {
                str(limit)
                for item in targets
                for limit in item.get("limitations", [])
                if isinstance(limit, str) and limit.strip()
            }
        )
        hard_block = any(
            issue.get("rule_id") == "REQUIRED_DATA_INPUT_UNAVAILABLE"
            for issue in issues
        )
        if hard_block:
            blocked_claims = sorted(claim_ids)
        return self.submit_verdict(
            mode=mode,
            decision="block" if hard_block else "revise",
            issues=[],
            blocked_claims=blocked_claims,
            carry_forward_limits=limitations,
            next_owner=None if hard_block else str(issues[0]["owner"]),
        )

    def review_source(self, mode: str, source_ref: str) -> dict[str, Any]:
        """Read one source explicitly referenced by the current review target.

        Evidence review is intentionally read-only and cannot browse the whole
        workspace.  A source must be named by the hash-bound target artifact (or
        one of its claims), and filesystem resolution must remain inside the
        task workspace.  Artifact refs are returned from the immutable registry;
        bounded text files include a preview plus the full-file SHA-256.
        """

        normalized = source_ref.strip()
        if not normalized:
            raise ValueError("source_ref must not be empty")
        targets = self.review_targets(mode)
        all_artifacts = self.artifacts()
        by_ref = {self._long_ref(item): item for item in all_artifacts}
        visible_artifacts = self._artifact_closure(targets)
        allowed: set[str] = set()
        for artifact in visible_artifacts:
            allowed.update(artifact["evidence_refs"])
            allowed.update(artifact["upstream_refs"])
            for claim in artifact["claims"]:
                allowed.update(claim["supporting_evidence"])
                allowed.update(claim["opposing_evidence"])
                allowed.update(claim.get("limiting_evidence", []))
        if normalized not in allowed:
            raise PermissionError(
                "source_ref is not declared by the current hash-bound artifact"
            )

        if normalized.startswith("hypothesis-evidence:"):
            for visible in visible_artifacts:
                index = visible.get("payload", {}).get("hypothesis_evidence_index", {})
                if isinstance(index, dict) and isinstance(index.get(normalized), dict):
                    return {
                        "schema_version": "review-source-v2",
                        "source_ref": normalized,
                        "kind": "hypothesis_evidence_entry",
                        "truncated": False,
                        "evidence": index[normalized],
                    }
            raise FileNotFoundError(
                f"declared hypothesis evidence entry is unavailable: {normalized}"
            )

        artifact = by_ref.get(normalized)
        if artifact is not None:
            return {
                "schema_version": "review-source-v2",
                "source_ref": normalized,
                "kind": "research_artifact",
                "sha256": artifact["artifact_sha256"],
                "truncated": False,
                "artifact": artifact,
            }

        relative = normalized.removeprefix("/")
        candidate = (self.workspace_root / relative).resolve()
        try:
            candidate.relative_to(self.workspace_root)
        except ValueError as exc:
            raise PermissionError("source_ref escapes the task workspace") from exc
        if not candidate.is_file():
            raise FileNotFoundError(f"referenced source does not exist: {normalized}")

        digest = hashlib.sha256()
        preview = bytearray()
        total = 0
        with candidate.open("rb") as handle:
            while chunk := handle.read(64 * 1024):
                digest.update(chunk)
                total += len(chunk)
                if len(preview) < _MAX_REVIEW_SOURCE_BYTES:
                    remaining = _MAX_REVIEW_SOURCE_BYTES - len(preview)
                    preview.extend(chunk[:remaining])
        media_type = (
            mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        )
        expected = None
        for visible in visible_artifacts:
            manifest = visible.get("payload", {}).get("source_manifest", [])
            if not isinstance(manifest, list):
                continue
            for item in manifest:
                if isinstance(item, dict) and item.get("source_ref") == normalized:
                    expected = item.get("sha256")
                    break
            if expected is not None:
                break
        actual_sha256 = digest.hexdigest()
        try:
            content = bytes(preview).decode("utf-8")
            encoding: str | None = "utf-8"
        except UnicodeDecodeError:
            content = ""
            encoding = None
        return {
            "schema_version": "review-source-v2",
            "source_ref": normalized,
            "kind": "workspace_file",
            "media_type": media_type,
            "bytes": total,
            "sha256": actual_sha256,
            "checkpoint_sha256": expected,
            "hash_matches_checkpoint": (
                expected is not None and expected == actual_sha256
            ),
            "encoding": encoding,
            "truncated": total > _MAX_REVIEW_SOURCE_BYTES,
            "content": content,
        }

    def _document_sections(self, mode: str, source_ref: str) -> list[dict[str, str]]:
        """Extract bounded, locator-stable text sections from an allowed source."""

        source = self.review_source(mode, source_ref)
        if source.get("kind") != "workspace_file":
            raise ValueError(
                "document section reading requires a workspace file source"
            )
        relative = source_ref.strip().removeprefix("/")
        path = (self.workspace_root / relative).resolve()
        suffix = path.suffix.casefold()
        sections: list[dict[str, str]] = []
        total = 0
        if suffix == ".pdf":
            try:
                from pypdf import PdfReader
            except ImportError as exc:
                raise RuntimeError(
                    "PDF section reading requires the declared pypdf dependency"
                ) from exc
            reader = PdfReader(str(path))
            for page_index, page in enumerate(reader.pages, start=1):
                page_text = str(page.extract_text() or "").strip()
                if not page_text:
                    continue
                remaining = _MAX_DOCUMENT_TEXT_CHARS - total
                if remaining <= 0:
                    break
                page_text = page_text[:remaining]
                page_sections = _chunk_document_text(page_text, f"page:{page_index}")
                sections.extend(page_sections)
                total += len(page_text)
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
            if suffix in {".html", ".htm"}:
                from markdownify import markdownify

                text = markdownify(text)
            text = text[:_MAX_DOCUMENT_TEXT_CHARS]
            sections = _chunk_document_text(text, "paragraph")
        if not sections:
            raise ValueError("document contains no readable text sections")
        return sections

    def search_document(
        self,
        mode: str,
        source_ref: str,
        query: str,
        *,
        max_hits: int = 8,
    ) -> dict[str, Any]:
        """Search one declared task-local document and return exact section ids."""

        normalized_query = query.strip()
        if len(normalized_query) < 2:
            raise ValueError("document query must contain at least 2 characters")
        if not 1 <= max_hits <= 20:
            raise ValueError("max_hits must be in [1, 20]")
        terms = _query_terms(normalized_query)
        if not terms:
            raise ValueError("document query has no searchable terms")
        scored: list[tuple[int, int, dict[str, str]]] = []
        for index, section in enumerate(self._document_sections(mode, source_ref)):
            folded = section["text"].casefold()
            score = sum(folded.count(term) for term in terms)
            if score:
                scored.append((score, -index, section))
        scored.sort(reverse=True, key=lambda item: (item[0], item[1]))
        hits = []
        for score, _, section in scored[:max_hits]:
            text = section["text"]
            folded = text.casefold()
            positions = [folded.find(term) for term in terms if folded.find(term) >= 0]
            start = max(min(positions, default=0) - 240, 0)
            hits.append(
                {
                    "section_id": section["section_id"],
                    "score": score,
                    "excerpt": text[start : start + 1_200],
                }
            )
        return {
            "schema_version": "review-document-search-v1",
            "source_ref": source_ref,
            "query": normalized_query,
            "hits": hits,
            "search_gap": not bool(hits),
        }

    def read_document_sections(
        self, mode: str, source_ref: str, section_ids: list[str]
    ) -> dict[str, Any]:
        """Read exact sections returned by search_document, capped for one call."""

        if not section_ids or len(section_ids) > 12:
            raise ValueError("section_ids must contain 1 to 12 ids")
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("section_ids must be unique")
        by_id = {
            section["section_id"]: section
            for section in self._document_sections(mode, source_ref)
        }
        unknown = [section_id for section_id in section_ids if section_id not in by_id]
        if unknown:
            raise ValueError("unknown document section ids: " + ", ".join(unknown))
        selected = [by_id[section_id] for section_id in section_ids]
        return {
            "schema_version": "review-document-sections-v1",
            "source_ref": source_ref,
            "sections": selected,
        }

    def _no_progress_count(
        self,
        mode: str,
        issues: list[dict[str, Any]],
        artifact_refs: list[dict[str, Any]],
    ) -> int:
        current = {
            item["fingerprint"]: item["severity"]
            for item in issues
            if item["severity"] in {"critical", "major"}
        }
        if not current:
            return 0
        count = 0
        last_refs = artifact_refs
        for verdict in reversed(self.verdicts(mode=mode)):
            previous_refs = verdict["artifact_refs"]
            if previous_refs == last_refs:
                # Policy re-adjudication or a reviewer retry over the exact same
                # immutable artifact is not a failed producer revision.
                continue
            previous = {
                item["fingerprint"]: item["severity"]
                for item in verdict["issues"]
                if item["severity"] in {"critical", "major"}
            }
            if previous != current:
                break
            count += 1
            last_refs = previous_refs
        return count

    @staticmethod
    def _enforce_policy_severity_floors(
        mode: str, issues: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Raise known review-rule severities to the registry minimum."""

        floors = {
            str(rule["rule_id"]): str(rule["default_severity"])
            for rule in policy_registry(stage=mode)["rules"]
        }
        normalized: list[dict[str, Any]] = []
        for raw in issues:
            issue = dict(raw)
            severity = issue.get("severity")
            floor = floors.get(str(issue.get("rule_id") or ""))
            if (
                isinstance(severity, str)
                and floor in _SEVERITY_RANK
                and severity in _SEVERITY_RANK
                and _SEVERITY_RANK[severity] < _SEVERITY_RANK[floor]
            ):
                issue["severity"] = floor
            normalized.append(issue)
        return normalized

    def submit_verdict(
        self,
        *,
        mode: str,
        decision: str,
        issues: list[dict[str, Any]],
        accepted_claims: list[str] | None = None,
        blocked_claims: list[str] | None = None,
        carry_forward_limits: list[str] | None = None,
        next_owner: str | None = None,
    ) -> dict[str, Any]:
        with self._transaction():
            issues = self._enforce_policy_severity_floors(mode, issues)
            targets = self.review_targets(mode)
            all_issues_are_misapplied_data_input = bool(issues) and all(
                issue.get("rule_id") == "REQUIRED_DATA_INPUT_UNAVAILABLE"
                for issue in issues
            )
            issues, recovered_data_input_issue = (
                self._recover_misapplied_data_input_issues(mode, targets, issues)
            )
            if recovered_data_input_issue and all_issues_are_misapplied_data_input:
                decision = "revise"
                next_owner = "solar-data"
                accepted_claims = []
                blocked_claims = []
            refs = [self.artifact_ref(item) for item in targets]
            target_claims = {
                claim["claim_id"] for item in targets for claim in item["claims"]
            }
            accepted_claim_set = set(accepted_claims or [])
            blocked_claim_set = set(blocked_claims or [])
            unknown_claims = (accepted_claim_set | blocked_claim_set) - target_claims
            if unknown_claims:
                raise ValueError(
                    "verdict references unknown claim ids: "
                    + ", ".join(sorted(unknown_claims))
                )
            if accepted_claim_set & blocked_claim_set:
                raise ValueError("a claim cannot be both accepted and blocked")
            if decision in {"accept", "accept_with_limits"} and not accepted_claim_set:
                raise ValueError("an accepting verdict must name accepted_claims")
            if decision == "accept" and accepted_claim_set != target_claims:
                raise ValueError("accept must accept every target claim")
            if (
                decision == "revise"
                and next_owner not in REVISION_OWNERS_FOR_MODE[mode]
            ):
                raise ValueError(f"{next_owner} cannot own a revision from {mode}")
            integrity_issues = self._source_integrity_issues(targets)
            if integrity_issues:
                decision = "block"
                next_owner = None
                issues = [*issues, *integrity_issues]
            semantic_issues = self._deterministic_semantic_issues(mode, targets)
            if semantic_issues:
                issues = [*issues, *semantic_issues]
                if any(
                    issue.get("rule_id") == "REQUIRED_DATA_INPUT_UNAVAILABLE"
                    for issue in semantic_issues
                ):
                    decision = "block"
                    next_owner = None
                elif decision != "block":
                    decision = "revise"
                    next_owner = semantic_issues[0]["owner"]
            unresolved_severities = {
                str(issue.get("severity"))
                for issue in issues
                if isinstance(issue, Mapping)
            }
            if decision in {
                "accept",
                "accept_with_limits",
            } and unresolved_severities & {
                "critical",
                "major",
            }:
                revision_owner = next(
                    (
                        str(issue.get("owner"))
                        for issue in issues
                        if isinstance(issue, Mapping)
                        and issue.get("severity") in {"critical", "major"}
                        and issue.get("owner") in REVISION_OWNERS_FOR_MODE[mode]
                    ),
                    None,
                )
                decision = "revise" if revision_owner else "block"
                next_owner = revision_owner
            existing = self.verdicts(mode=mode)
            round_number = len(existing) + 1
            state = self.load_state()
            if state["review_invocations"] >= state["max_review_invocations"]:
                decision = "block"
                next_owner = None
                issues = [
                    {
                        "issue_id": f"budget-{round_number}",
                        "rule_id": "REVIEW_BUDGET_EXHAUSTED",
                        "severity": "critical",
                        "claim_ref": f"{mode}:all",
                        "evidence_refs": [],
                        "owner": "main",
                        "message": "The task-level review budget is exhausted.",
                        "required_action": "Return the best current artifact with unresolved issues.",
                        "acceptance_test": "A new explicitly authorized task budget is available.",
                        "fingerprint": issue_fingerprint(
                            "REVIEW_BUDGET_EXHAUSTED", f"{mode}:all", "main"
                        ),
                    }
                ]
            no_progress = self._no_progress_count(mode, issues, refs)
            if decision == "revise" and no_progress >= state["no_progress_patience"]:
                decision = "block"
                next_owner = None
                issues = [
                    *issues,
                    {
                        "issue_id": f"no-progress-{round_number}",
                        "rule_id": "NO_PROGRESS_STOP",
                        "severity": "critical",
                        "claim_ref": f"{mode}:all",
                        "evidence_refs": [],
                        "owner": "main",
                        "message": (
                            "Distinct immutable revisions repeated the same unresolved "
                            "major issue fingerprints without lowering severity."
                        ),
                        "required_action": (
                            "Stop serial self-revision and return the best artifact "
                            "with the unresolved issues as a blocked result."
                        ),
                        "acceptance_test": (
                            "New external evidence or a predeclared method change "
                            "materially alters an unresolved issue or its severity."
                        ),
                        "fingerprint": issue_fingerprint(
                            "NO_PROGRESS_STOP", f"{mode}:all", "main"
                        ),
                    },
                ]
            if (
                decision == "revise"
                and state["revision_policy"] == "fixed"
                and sum(1 for row in existing if row["decision"] == "revise")
                >= state["max_revisions"]
            ):
                decision = "block"
                next_owner = None
                issues = [
                    *issues,
                    {
                        "issue_id": f"revision-limit-{round_number}",
                        "rule_id": "REVISION_LIMIT_REACHED",
                        "severity": "critical",
                        "claim_ref": f"{mode}:all",
                        "evidence_refs": [],
                        "owner": "main",
                        "message": "The configured fixed revision limit was reached.",
                        "required_action": (
                            "Return the best artifact and unresolved issues without "
                            "converting the limit into scientific acceptance."
                        ),
                        "acceptance_test": (
                            "A new task with an explicitly authorized budget and "
                            "method decision is available."
                        ),
                        "fingerprint": issue_fingerprint(
                            "REVISION_LIMIT_REACHED", f"{mode}:all", "main"
                        ),
                    },
                ]
            verdict = build_review_verdict(
                review_id=f"{mode}-review-{round_number:04d}",
                task_id=self.task_id,
                review_mode=mode,
                artifact_refs=refs,
                round_number=round_number,
                decision=decision,
                issues=issues,
                accepted_claims=accepted_claims,
                blocked_claims=blocked_claims,
                carry_forward_limits=carry_forward_limits,
                next_owner=next_owner,
            )
            path = self.root / "verdicts" / f"{verdict['review_id']}.json"
            _atomic_write_json(path, verdict)
            state = self.load_state()
            rel = path.relative_to(self.workspace_root).as_posix()
            if rel not in state["verdicts"]:
                state["verdicts"].append(rel)
            state["review_invocations"] += 1
            state["current_stage"] = mode
            state["stage_status"][mode] = {
                "accept": "accepted",
                "accept_with_limits": "accepted_with_limits",
                "revise": "revise",
                "block": "blocked",
            }[decision]
            if decision == "block":
                state["status"] = "blocked"
            elif mode == "final_release" and decision in {
                "accept",
                "accept_with_limits",
            }:
                state["status"] = "release_ready"
            else:
                state["status"] = "active"
            self._save_state(state)
            return verdict

    def revision_capsule(self, review_id: str, owner: str) -> dict[str, Any]:
        """Return the compact producer-facing view of one immutable verdict."""

        verdict = next(
            (row for row in self.verdicts() if row["review_id"] == review_id),
            None,
        )
        if verdict is None:
            raise ValueError("review_id does not identify a persisted verdict")
        return build_revision_capsule(prior_verdict=verdict, owner=owner)

    def assessments(self, *, mode: str | None = None) -> list[dict[str, Any]]:
        """Read every persisted ReviewAssessmentV1 sidecar, newest round last."""

        directory = self.root / "assessments"
        rows: list[dict[str, Any]] = []
        for path in sorted(directory.glob("*.json")):
            payload = _read_json(path)
            if payload is None:
                continue
            try:
                row = validate_review_assessment(payload)
            except ContractError:
                continue
            if mode is None or row["review_mode"] == mode:
                rows.append(row)
        rows.sort(key=lambda row: (row["round"], row["assessment_id"]))
        return rows

    def record_assessment(
        self,
        *,
        mode: str,
        assessment_review_mode: str,
        claims: list[dict[str, Any]],
        replace_uncommitted: bool = False,
    ) -> dict[str, Any]:
        """Persist one ReviewAssessmentV1 sidecar for the current review round.

        The assessment is additive: it binds the exact artifact refs the verdict
        for this round reviews, but it never enters run_state, never feeds the
        verdict validator, and never drives no-progress or severity machinery.
        """

        with self._transaction():
            targets = self.review_targets(mode)
            refs = [self.artifact_ref(item) for item in targets]
            target_claims = {
                claim["claim_id"] for item in targets for claim in item["claims"]
            }
            target_claim_kinds = {
                claim["claim_id"]: claim["kind"]
                for item in targets
                for claim in item["claims"]
            }
            referenced = {claim["claim_id"] for claim in claims}
            unknown = referenced - target_claims
            if unknown:
                raise ValueError(
                    "assessment references unknown claim ids: "
                    + ", ".join(sorted(unknown))
                )
            missing = target_claims - referenced
            if missing:
                raise ValueError(
                    "assessment omits reviewed claim ids: " + ", ".join(sorted(missing))
                )
            mismatched_kinds = sorted(
                claim["claim_id"]
                for claim in claims
                if target_claim_kinds.get(claim["claim_id"]) != claim.get("kind")
            )
            if mismatched_kinds:
                raise ValueError(
                    "assessment claim kind does not match the reviewed artifact: "
                    + ", ".join(mismatched_kinds)
                )
            candidate_support = self._candidate_harness_support_refs(targets)
            invalid_support = sorted(
                {
                    source_ref
                    for claim in claims
                    for source_ref in claim.get("supporting_evidence", [])
                    if isinstance(source_ref, str)
                    and source_ref.startswith(_HARNESS_SOURCE_PREFIX)
                    and source_ref not in candidate_support
                }
            )
            if invalid_support:
                raise ValueError(
                    "assessment supporting_evidence requires a current visible "
                    "Harness candidate; provenance/gap or unknown refs cannot support: "
                    + ", ".join(invalid_support)
                )
            existing = self.verdicts(mode=mode)
            round_number = len(existing) + 1
            assessment_id = f"{mode}-assessment-{round_number:04d}"
            assessment_path = self.root / "assessments" / f"{assessment_id}.json"
            if assessment_path.exists():
                if not replace_uncommitted:
                    raise ValueError(
                        f"assessment already recorded for {mode} round {round_number}"
                    )
                if any(row["round"] == round_number for row in existing):
                    raise ValueError(
                        f"assessment for {mode} round {round_number} is already bound to a verdict"
                    )
            assessment = build_review_assessment(
                assessment_id=assessment_id,
                task_id=self.task_id,
                review_mode=mode,
                assessment_review_mode=assessment_review_mode,
                artifact_refs=refs,
                policy_version=POLICY_VERSION,
                round=round_number,
                claims=claims,
                created_at=_now(),
            )
            _atomic_write_json(assessment_path, assessment)
            return assessment

    def scientific_quality_assessments(
        self, *, mode: str | None = None
    ) -> list[dict[str, Any]]:
        """Read persisted ScientificQualityAssessmentV1 sidecars."""

        directory = self.root / "scientific_quality_assessments"
        rows: list[dict[str, Any]] = []
        for path in sorted(directory.glob("*.json")):
            payload = _read_json(path)
            if payload is None:
                continue
            try:
                row = validate_scientific_quality_assessment(payload)
            except ContractError:
                continue
            if mode is None or row["review_mode"] == mode:
                rows.append(row)
        rows.sort(key=lambda row: (row["round"], row["assessment_id"]))
        return rows

    def record_scientific_quality_assessment(
        self,
        *,
        mode: str,
        assessment_review_mode: str,
        claims: list[dict[str, Any]],
        replace_uncommitted: bool = False,
    ) -> dict[str, Any]:
        """Persist exactly one scientific-quality matrix for the current round."""

        with self._transaction():
            targets = self.review_targets(mode)
            refs = [self.artifact_ref(item) for item in targets]
            target_claims = {
                claim["claim_id"] for item in targets for claim in item["claims"]
            }
            referenced = {
                claim.get("claim_id") for claim in claims if isinstance(claim, dict)
            }
            unknown = referenced - target_claims
            missing = target_claims - referenced
            if unknown:
                raise ValueError(
                    "scientific quality assessment references unknown claim ids: "
                    + ", ".join(sorted(str(item) for item in unknown))
                )
            if missing:
                raise ValueError(
                    "scientific quality assessment omits claim ids: "
                    + ", ".join(sorted(missing))
                )
            candidate_support = self._candidate_harness_support_refs(targets)
            invalid_support = sorted(
                {
                    str(evidence.get("source_ref"))
                    for claim in claims
                    if isinstance(claim, dict)
                    for evidence in claim.get("evidence_matrix", [])
                    if isinstance(evidence, dict)
                    and evidence.get("evidence_role") == "supports"
                    and isinstance(evidence.get("source_ref"), str)
                    and str(evidence["source_ref"]).startswith(_HARNESS_SOURCE_PREFIX)
                    and evidence.get("source_ref") not in candidate_support
                }
            )
            if invalid_support:
                raise ValueError(
                    "scientific quality supports require a current visible Harness "
                    "candidate; provenance/gap or unknown refs are not candidates: "
                    + ", ".join(invalid_support)
                )
            round_number = len(self.verdicts(mode=mode)) + 1
            assessment_id = f"{mode}-quality-{round_number:04d}"
            path = (
                self.root / "scientific_quality_assessments" / f"{assessment_id}.json"
            )
            if path.exists():
                if not replace_uncommitted:
                    raise ValueError(
                        f"scientific quality assessment already recorded for {mode} "
                        f"round {round_number}"
                    )
                if any(
                    row["round"] == round_number for row in self.verdicts(mode=mode)
                ):
                    raise ValueError(
                        f"scientific quality assessment for {mode} round {round_number} "
                        "is already bound to a verdict"
                    )
            assessment = build_scientific_quality_assessment(
                assessment_id=assessment_id,
                task_id=self.task_id,
                review_mode=mode,
                assessment_review_mode=assessment_review_mode,
                artifact_refs=refs,
                round=round_number,
                claims=claims,
                created_at=_now(),
            )
            _atomic_write_json(path, assessment)
            return assessment

    def prepare_release(
        self,
        draft_markdown: str,
        claim_citations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        integration = self.latest_artifact("integration")
        if integration is None:
            raise RuntimeError("final release requires an integration artifact")
        verdict = self.matching_verdict("integration", [self.artifact_ref(integration)])
        if verdict is None or verdict["decision"] not in {
            "accept",
            "accept_with_limits",
        }:
            raise RuntimeError("final release requires an accepted integration review")
        text = draft_markdown.strip()
        if not text:
            raise ValueError("draft_markdown must not be empty")
        limits = sorted(set(verdict["carry_forward_limits"]))
        integration_claims = {
            claim["claim_id"]: claim for claim in integration["claims"]
        }
        accepted_claim_ids = set(verdict["accepted_claims"])
        if not claim_citations:
            raise ValueError(
                "claim_citations must bind each material report passage to accepted claims"
            )
        normalized_citations: list[dict[str, str]] = []
        seen_citations: set[tuple[str, str]] = set()
        for index, citation in enumerate(claim_citations):
            if not isinstance(citation, dict) or set(citation) != {
                "claim_id",
                "draft_excerpt",
            }:
                raise ValueError(
                    f"claim_citations[{index}] must contain claim_id and draft_excerpt"
                )
            claim_id = citation.get("claim_id")
            excerpt = citation.get("draft_excerpt")
            if (
                not isinstance(claim_id, str)
                or claim_id not in integration_claims
                or claim_id not in accepted_claim_ids
            ):
                raise ValueError(
                    f"claim_citations[{index}] references an unaccepted claim"
                )
            if not isinstance(excerpt, str) or not excerpt.strip():
                raise ValueError(
                    f"claim_citations[{index}].draft_excerpt must be a non-empty string"
                )
            row = (claim_id, excerpt.strip())
            if row in seen_citations:
                raise ValueError("claim_citations must not contain duplicate bindings")
            seen_citations.add(row)
            normalized_citations.append(
                {"claim_id": claim_id, "draft_excerpt": excerpt.strip()}
            )
        with self._transaction():
            previous = self.latest_artifact("final_release")
            version = 1 if previous is None else previous["version"] + 1
            prior_release_verdict = (
                None
                if previous is None
                else self.matching_verdict(
                    "final_release", [self.artifact_ref(previous)]
                )
            )
            if prior_release_verdict is not None and (
                prior_release_verdict["decision"] != "revise"
                or prior_release_verdict["next_owner"] != "main"
            ):
                prior_release_verdict = None
            integration_ref = (
                f"{integration['artifact_id']}@v{integration['version']}:"
                f"{integration['artifact_sha256']}"
            )
            claim = {
                "schema_version": CLAIM_VERSION,
                "claim_id": f"final-release-output-v{version}",
                "kind": "inference",
                "text": text[:20_000],
                "scope": "Reader-facing synthesis of accepted integration claims.",
                "supporting_evidence": [integration_ref],
                "opposing_evidence": [],
                "confidence": "unknown",
                "unknowns": [
                    "Publication significance requires independent adjudication."
                ],
            }
            artifact = build_research_artifact(
                artifact_id="final_release-artifact",
                task_id=self.task_id,
                stage="final_release",
                version=version,
                producer="main",
                upstream_refs=[integration_ref],
                claims=[claim],
                evidence_refs=[integration_ref],
                limitations=limits,
                payload={
                    "phase": "final_release",
                    "producer_result": text,
                    "required_limits": limits,
                    "claim_citations": normalized_citations,
                    **(
                        {
                            "revision_response": build_revision_response(
                                task_id=self.task_id,
                                stage="final_release",
                                producer="main",
                                artifact_version=version,
                                prior_verdict=prior_release_verdict,
                                acceptance_evidence=[integration_ref],
                            )
                        }
                        if prior_release_verdict is not None
                        else {}
                    ),
                },
            )
            path = (
                self.root
                / "artifacts"
                / artifact["artifact_id"]
                / f"v{version:04d}.json"
            )
            _atomic_write_json(path, artifact)
            state = self.load_state()
            rel = path.relative_to(self.workspace_root).as_posix()
            if rel not in state["artifacts"]:
                state["artifacts"].append(rel)
            state["current_stage"] = "final_release"
            state["stage_status"]["final_release"] = "produced"
            state["status"] = "active"
            self._save_state(state)
            return artifact

    def accepted_release_markdown(self) -> str | None:
        artifact = self.latest_artifact("final_release")
        if artifact is None:
            return None
        verdict = self.matching_verdict("final_release", [self.artifact_ref(artifact)])
        if verdict is None or verdict["decision"] not in {
            "accept",
            "accept_with_limits",
        }:
            return None
        text = artifact["payload"].get("producer_result")
        return text if isinstance(text, str) and text.strip() else None

    def mark_release_delivered(self) -> dict[str, Any]:
        """Commit the terminal state after the accepted report is returned."""

        with self._transaction():
            artifact = self.latest_artifact("final_release")
            if artifact is None:
                raise RuntimeError("final release delivery requires an artifact")
            verdict = self.matching_verdict(
                "final_release", [self.artifact_ref(artifact)]
            )
            if verdict is None or verdict["decision"] not in {
                "accept",
                "accept_with_limits",
            }:
                raise RuntimeError("final release delivery requires acceptance")
            state = self.load_state()
            state["status"] = "released"
            state["current_stage"] = "final_release"
            return self._save_state(state)

    def next_action(self) -> dict[str, Any]:
        """Return the one deterministic graph action allowed next."""

        state = self.load_state()
        if state["status"] == "blocked":
            failure = self.latest_tool_failure_receipt()
            return {
                "kind": "terminal",
                "status": state["status"],
                "reason": (
                    failure["reason_code"]
                    if isinstance(failure, Mapping)
                    else "UNRESOLVED_REVIEW_GATE"
                ),
            }
        if state["action_invocations"] >= state["max_action_invocations"]:
            state["status"] = "blocked"
            self._save_state(state)
            return {
                "kind": "terminal",
                "status": "blocked",
                "reason": "RESEARCH_ACTION_BUDGET_EXHAUSTED",
            }
        if (
            state["status"] != "release_ready"
            and state["review_invocations"] >= state["max_review_invocations"]
        ):
            state["status"] = "blocked"
            self._save_state(state)
            return {
                "kind": "terminal",
                "status": "blocked",
                "reason": "REVIEW_BUDGET_EXHAUSTED",
            }

        for stage in REVIEW_SEQUENCE:
            artifact = self.latest_artifact(stage)
            if artifact is None:
                return {
                    "kind": "producer",
                    "stage": stage,
                    "producer": PRODUCER_FOR_STAGE[stage],
                    "phase": stage,
                }
            ref = self.artifact_ref(artifact)
            verdict = self.matching_verdict(stage, [ref])
            if verdict is None:
                return {
                    "kind": "review",
                    "stage": stage,
                    "review_mode": stage,
                    "artifact_refs": [ref],
                }
            if verdict["decision"] == "revise":
                owner = verdict["next_owner"] or PRODUCER_FOR_STAGE[stage]
                owner_stage = (
                    stage
                    if owner == "solar-experiment"
                    and stage in {"experiment_design", "experiment_result"}
                    else STAGE_FOR_OWNER.get(owner, stage)
                )
                return {
                    "kind": "producer",
                    "stage": owner_stage,
                    "producer": owner,
                    "phase": f"{owner_stage}_revision_from_{stage}",
                    "revision_review_id": verdict["review_id"],
                    "issues": [
                        issue for issue in verdict["issues"] if issue["owner"] == owner
                    ],
                }
            if verdict["decision"] == "block":
                return {
                    "kind": "terminal",
                    "status": verdict["decision"],
                    "verdict": verdict,
                }
            if not self._dependencies_current(stage, artifact):
                return {
                    "kind": "producer",
                    "stage": stage,
                    "producer": PRODUCER_FOR_STAGE[stage],
                    "phase": f"{stage}_dependency_refresh",
                    "reason": "accepted upstream artifact hash changed",
                }

        experiment = self.latest_artifact("experiment_result")
        hypothesis = self.latest_artifact("hypothesis")
        if experiment is not None and hypothesis is not None:
            experiment_ref = f"{experiment['artifact_id']}@v{experiment['version']}:{experiment['artifact_sha256']}"
            if experiment_ref not in hypothesis["upstream_refs"]:
                return {
                    "kind": "producer",
                    "stage": "hypothesis",
                    "producer": "solar-hypothesis",
                    "phase": "hypothesis_update",
                    "required_upstream": experiment_ref,
                }

        integration = self.ensure_integration_artifact()
        integration_ref = self.artifact_ref(integration)
        integration_verdict = self.matching_verdict("integration", [integration_ref])
        if integration_verdict is None:
            return {
                "kind": "review",
                "stage": "integration",
                "review_mode": "integration",
                "artifact_refs": [integration_ref],
            }
        if integration_verdict["decision"] == "revise":
            owner = integration_verdict["next_owner"] or "solar-hypothesis"
            owner_stage = {
                "solar-planner": "planning",
                "solar-data": "data",
                "solar-hypothesis": "hypothesis",
                "solar-experiment": "experiment_result",
                "main": "final_release",
            }.get(owner, "hypothesis")
            return {
                "kind": "producer",
                "stage": owner_stage,
                "producer": owner,
                "phase": "integration_revision",
                "revision_review_id": integration_verdict["review_id"],
                "issues": integration_verdict["issues"],
            }
        if integration_verdict["decision"] == "block":
            return {"kind": "terminal", "status": integration_verdict["decision"]}

        accepted_integration_claim_ids = set(integration_verdict["accepted_claims"])
        release_claims = [
            {
                "claim_id": claim["claim_id"],
                "kind": claim["kind"],
                "text": claim["text"],
                "scope": claim["scope"],
                "confidence": claim["confidence"],
            }
            for claim in integration["claims"]
            if claim["claim_id"] in accepted_integration_claim_ids
        ]
        release = self.latest_artifact("final_release")
        if release is None:
            return {
                "kind": "prepare_release",
                "stage": "final_release",
                "release_context": {
                    "claims": release_claims,
                    "required_limits": sorted(
                        set(integration_verdict["carry_forward_limits"])
                    ),
                },
            }
        release_ref = self.artifact_ref(release)
        release_verdict = self.matching_verdict("final_release", [release_ref])
        if release_verdict is None:
            return {
                "kind": "review",
                "stage": "final_release",
                "review_mode": "final_release",
                "artifact_refs": [release_ref],
            }
        if release_verdict["decision"] == "revise":
            owner = release_verdict["next_owner"] or "main"
            if owner != "main":
                owner_stage = (
                    "experiment_result"
                    if owner == "solar-experiment"
                    else STAGE_FOR_OWNER[owner]
                )
                return {
                    "kind": "producer",
                    "stage": owner_stage,
                    "producer": owner,
                    "phase": f"{owner_stage}_revision_from_final_release",
                    "revision_review_id": release_verdict["review_id"],
                    "issues": [
                        issue
                        for issue in release_verdict["issues"]
                        if issue["owner"] == owner
                    ],
                }
            return {
                "kind": "prepare_release",
                "stage": "final_release",
                "revision_review_id": release_verdict["review_id"],
                "issues": release_verdict["issues"],
                "release_context": {
                    "claims": release_claims,
                    "required_limits": sorted(
                        set(integration_verdict["carry_forward_limits"])
                    ),
                },
            }
        if release_verdict["decision"] == "block":
            return {"kind": "terminal", "status": release_verdict["decision"]}
        return {"kind": "released", "stage": "final_release"}

    def bounded_hypothesis_action(self) -> dict[str, Any]:
        """Return the graded single-stage hypothesis action."""

        return self.bounded_stage_action("hypothesis")

    def bounded_sequence_action(self, stages: Sequence[str]) -> dict[str, Any]:
        """Return the first unfinished action in a short bounded stage sequence."""

        if not stages:
            raise ValueError("bounded stage sequence cannot be empty")
        for stage in stages:
            action = self.bounded_stage_action(stage)
            if action["kind"] != "released":
                return action
        return action

    def bounded_stage_action(self, stage: str) -> dict[str, Any]:
        """Return a producer/reviewer loop for one explicitly bounded stage."""

        state = self.load_state()
        if state["status"] == "blocked":
            failure = self.latest_tool_failure_receipt()
            return {
                "kind": "terminal",
                "status": state["status"],
                "reason": (
                    failure["reason_code"]
                    if isinstance(failure, Mapping)
                    else "UNRESOLVED_REVIEW_GATE"
                ),
            }
        if stage not in PRODUCER_FOR_STAGE or stage == "final_release":
            raise ValueError(f"unsupported bounded stage: {stage}")
        producer = PRODUCER_FOR_STAGE[stage]
        artifact = self.latest_artifact(stage)
        if artifact is None:
            return {
                "kind": "producer",
                "stage": stage,
                "producer": producer,
                "phase": f"bounded_{stage}",
            }
        ref = self.artifact_ref(artifact)
        verdict = self.matching_verdict(stage, [ref])
        stage_status = state["stage_status"].get(stage)
        if (
            verdict is not None
            and verdict["decision"] != "block"
            and stage_status == "pending"
        ):
            return {
                "kind": "producer",
                "stage": stage,
                "producer": producer,
                "phase": f"bounded_{stage}_dependency_refresh",
                "reason": "accepted upstream artifact hash changed",
            }
        if verdict is None:
            if state["review_invocations"] >= state["max_review_invocations"]:
                state["status"] = "blocked"
                self._save_state(state)
                return {
                    "kind": "terminal",
                    "status": "blocked",
                    "reason": "REVIEW_BUDGET_EXHAUSTED",
                }
            return {
                "kind": "review",
                "stage": stage,
                "review_mode": stage,
                "artifact_refs": [ref],
            }
        if verdict["decision"] == "revise":
            if state["review_invocations"] >= state["max_review_invocations"]:
                state["status"] = "blocked"
                self._save_state(state)
                return {
                    "kind": "terminal",
                    "status": "blocked",
                    "reason": "REVIEW_BUDGET_EXHAUSTED",
                }
            if verdict["next_owner"] != producer:
                return {
                    "kind": "terminal",
                    "status": "blocked",
                    "reason": "bounded request cannot expand to another owner",
                }
            return {
                "kind": "producer",
                "stage": stage,
                "producer": producer,
                "phase": f"bounded_{stage}_revision",
                "revision_review_id": verdict["review_id"],
                "issues": verdict["issues"],
            }
        if verdict["decision"] == "block":
            return {
                "kind": "terminal",
                "status": verdict["decision"],
                "verdict": verdict,
            }
        return {"kind": "released", "stage": stage, "verdict": verdict}


__all__ = [
    "ALL_REVIEW_MODES",
    "PRODUCER_FOR_STAGE",
    "REVIEW_SEQUENCE",
    "ResearchReviewStore",
    "store_from_config",
]
