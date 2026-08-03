"""LangChain tools for evidence-grounded scientific-hypothesis checkpoints.

The conversational agent may explore, critique, and revise hypotheses without
creating a frozen artifact.  These tools provide the stricter boundary used
when it needs to bind evidence, checkpoint a complete portfolio, or explicitly
publish that checkpoint.

Contract state is task-scoped. Rebinding the same request is idempotent, and a
failed checkpoint never destroys the last valid checkpoint.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SRC = _PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from langchain_core.runnables import RunnableConfig  # noqa: E402
from langchain_core.tools import tool  # noqa: E402

from jw.tools.registry import register_tool_bundle  # noqa: E402
from jw.workspaces import (  # noqa: E402
    resolve_scoped_path,
    workspace_context_key,
    workspace_root_from_config,
)
from scientific_hypothesis.contracts import (  # noqa: E402
    AMBIGUOUS_SOLAR_CYCLE_TERM,
    DECISION_RULE_QUALIFIER,
    HARD_NUMERIC_CUTOFF,
    RESPONSE_VERSION,
    SAFE_ID,
    SOLAR_CYCLE_CONTEXT,
    UNIVERSAL_SCOPE_CLAIM,
    VAGUE_DECISION_RULE,
    canonical_json_sha256,
    validate_hypothesis_request,
)
from scientific_hypothesis.harness import (  # noqa: E402
    EvidenceRegister,
    build_hypothesis_brief,
    build_natural_hypothesis_request,
    build_wiki_evidence_excerpt,
    freeze_hypothesis_portfolio,
    preflight_hypothesis_response,
    validate_evidence_provenance,
)
from scientific_hypothesis.reader_view import (  # noqa: E402
    render_hypothesis_reader_markdown,
)
from scientific_hypothesis.tail_search import (  # noqa: E402
    BENEFIT_METRICS,
    COST_METRICS,
    GENERAL_GUIDELINES,
    GENERATION_OPERATORS,
    RUBRIC_ITEMS,
    SEARCH_REGIONS,
    TAIL_REVIEW_VERSION,
    TAIL_REVIEWER_MODE,
    candidate_pool_sha256,
    tail_review_is_current,
    tail_review_scoring_guide,
    validate_and_select_tail_review,
)

MAX_EVIDENCE_BINDS = 20
MAX_SAME_CHECKPOINT_FAILURES = 2
LONG_TAIL_QUERY = re.compile(
    r"长尾|稀疏候选|罕见机制|非常规假设|\blong[- ]?tail\b|\bnovel hypothes",
    re.IGNORECASE,
)
WORKING_STATE_VERSION = 1
WORKING_STATE_RELATIVE_PATH = Path("work") / "scientific_hypothesis_state.json"
DRAFT_OPERATIONS = {
    "replace",
    "upsert_candidate",
    "patch_candidate",
    "remove_candidate",
    "set_distinctions",
    "set_portfolio_notes",
}
REQUIRED_CANDIDATE_FIELDS = {
    "id",
    "statement",
    "applicability",
    "scope_conditions",
    "epistemic_status",
    "mechanism",
    "assumptions",
    "predictions",
    "supporting_evidence",
    "opposing_evidence",
    "evidence_gaps",
    "alternative_explanations",
    "confounders",
    "falsification_conditions",
    "next_test",
    "uncertainty",
    "confidence",
    "evidence_update",
    "prior_version_id",
}
WIKI_GROUNDING_TYPES = {
    "concept",
    "mechanism",
    "data_source",
    "experiment_paradigm",
    "hypothesis_template",
}
TRANSPORT_CONTEXT = re.compile(
    r"子午流|磁通输运|输运效率|跨赤道抵消|"
    r"meridional[-\s]+flow|flux[-\s]+transport|transport\s+efficiency",
    re.IGNORECASE,
)
CYCLE_AMPLITUDE_CONTEXT = re.compile(
    r"(?:活动周|太阳周|周期).{0,20}(?:峰值|振幅|强度)"
    r"|(?:峰值|振幅|强度).{0,20}(?:活动周|太阳周|周期)"
    r"|第\s*\d+\s*(?:活动)?周.{0,20}(?:峰值|振幅|强度)"
    r"|(?:峰值|振幅|强度).{0,20}第\s*\d+\s*(?:活动)?周"
    r"|cycle[-\s]+(?:amplitude|peak|strength)"
    r"|(?:amplitude|peak|strength).{0,20}cycle",
    re.IGNORECASE,
)
DOMINANCE_CLAIM = re.compile(
    r"主要决定因素|主导(?:因素|作用)?|决定(?:了|着)?"
    r"|主要由.{0,24}(?:调制|控制|影响)"
    r"|primary\s+determinant|main\s+determinant|dominates?|determines?",
    re.IGNORECASE,
)
DIRECTION_CLAIM = re.compile(
    r"高于|低于|更高|更低|偏高|偏低|升高|降低|增强|减弱"
    r"|higher|lower|increase[sd]?|decrease[sd]?|faster|slower",
    re.IGNORECASE,
)
BMR_CONTEXT = re.compile(
    r"BMR|双极磁区|双极区|倾斜角|Joy(?:'s)?\s+law|Hale",
    re.IGNORECASE,
)
BMR_AMPLITUDE_CAUSALITY = re.compile(
    r"(?:来源|导致|造成|决定|影响|调制|贡献|扰动|取决)"
    r"|(?:source|cause|determine|influence|modulate|contribute|perturb|depend)",
    re.IGNORECASE,
)
BMR_PRECURSOR_ORDER = re.compile(
    r"(?:第\s*)?n\s*(?:活动)?周.{0,100}(?:BMR|双极磁区|双极区|倾斜角)"
    r".{0,120}(?:第\s*)?n\s*\+\s*1\s*(?:活动)?周"
    r"|(?:前一|上一|前驱)\s*(?:活动)?(?:周|周期).{0,100}"
    r"(?:BMR|双极磁区|双极区|倾斜角).{0,120}"
    r"(?:下一|后一)\s*(?:活动)?(?:周|周期).{0,40}(?:振幅|强度|峰值)"
    r"|cycle\s+n.{0,100}(?:BMR|bipolar|tilt).{0,120}"
    r"cycle\s+n\s*\+\s*1.{0,40}(?:amplitude|strength|peak)",
    re.IGNORECASE,
)
LITERATURE_AUTHOR_YEAR = re.compile(
    r"(?<![A-Za-z])([A-Z][A-Za-z-]{2,})(?:\s+(?:et\s+al\.?)|等)?"
    r"\s*[\(（]?\s*((?:19|20)\d{2})",
    re.IGNORECASE,
)
CHINESE_CHARACTER = re.compile(r"[\u3400-\u9fff]")
APPROX_CYCLE_SAMPLE_COUNT = re.compile(
    r"(?:约|大约|roughly|about)\s*"
    r"(\d+(?:\s*[–—-]\s*\d+)?)\s*(?:个\s*)?"
    r"(?:完整\s*)?(?:独立\s*)?"
    r"(?:活动周|太阳周|周期|cycles?|samples?)",
    re.IGNORECASE,
)
READINESS_REQUEST = re.compile(
    r"是否.{0,24}(?:适合|可以).{0,12}启动"
    r"|是否已经适合启动",
    re.IGNORECASE,
)
POSITIVE_READINESS_CLAIM = re.compile(
    r"(?:当前|截至.{0,18}|截止.{0,18}|已经).{0,30}"
    r"(?:可以|适合).{0,16}(?:启动|提供.{0,20}(?:判断|预测|评估))",
    re.IGNORECASE,
)
UNVERIFIED_CURRENT_STATE = re.compile(
    r"(?:实际|当前|最新|截至|截止).{0,60}(?:尚未|未).{0,24}"
    r"(?:核验|核实|验证)"
    r"|是否.{0,50}(?:尚未|未)(?:核验|核实|验证)"
    r"|(?:实际|当前|最新|截至|截止).{0,80}"
    r"需要(?:直接)?(?:获取|核验|核实|查阅|比较)",
    re.IGNORECASE,
)
EVENT_TIMING_WINDOW = re.compile(
    r"20\d{2}年(?:底|初|上半年|下半年)"
    r"(?:\s*(?:或|至|到|[-–—])\s*"
    r"20\d{2}年(?:底|初|上半年|下半年))?",
)
FIXED_PROTOCOL_REQUEST = re.compile(
    r"(?:若|if).{0,50}(?:没有胜过|未胜过|不优于|does\s+not\s+beat)"
    r".{0,30}(?:基线|baseline).{0,60}"
    r"(?:不得|不要|而不是|do\s+not|rather\s+than).{0,30}"
    r"(?:特征|feature)",
    re.IGNORECASE,
)
ADAPTIVE_HOLDOUT_REUSE = re.compile(
    r"(?:同一批|相同).{0,30}(?:留出|目标).{0,100}"
    r"(?:同时|一并).{0,50}(?:单特征|分组|新增特征|额外特征)"
    r"|(?:同时报告|一并比较).{0,100}"
    r"(?:单特征|分组|新增特征|额外特征)",
    re.IGNORECASE,
)


@dataclass(slots=True)
class _HypothesisState:
    request: dict[str, Any] | None = None
    request_sha256: str = ""
    evidence_register: EvidenceRegister = field(default_factory=EvidenceRegister)
    validated_response: dict[str, Any] | None = None
    preflight_response_sha256: str | None = None
    checkpoint_evidence_sha256: str | None = None
    preflight_attempts: int = 0
    latest_draft: dict[str, Any] | None = None
    latest_draft_sha256: str | None = None
    tail_review: dict[str, Any] | None = None
    last_validation_error: str | None = None
    same_validation_error_count: int = 0
    persistence_warning: str | None = None
    literature_bundle_attempted: bool = False
    literature_bundle_id: str | None = None
    _persistence_lock: Any = field(
        default_factory=RLock,
        repr=False,
        compare=False,
    )


_STATES: dict[str, _HypothesisState] = {}
_STATE_LOCK = RLock()


def _ok(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, default=str)


def _working_state_path(config: RunnableConfig | None) -> Path | None:
    """Return the task-local state path when a workspace binding is available."""

    try:
        return workspace_root_from_config(config) / WORKING_STATE_RELATIVE_PATH
    except RuntimeError:
        # Direct unit calls may carry a synthetic thread id without creating a
        # task binding. They retain the historical in-memory behavior.
        return None


def _working_state_payload(state: _HypothesisState) -> dict[str, Any]:
    return {
        "schema_version": WORKING_STATE_VERSION,
        "request": state.request,
        "request_sha256": state.request_sha256,
        "evidence_register": state.evidence_register.all(),
        "checkpoint": state.validated_response,
        "checkpoint_sha256": state.preflight_response_sha256,
        "checkpoint_evidence_sha256": state.checkpoint_evidence_sha256,
        "checkpoint_attempts": state.preflight_attempts,
        "latest_draft": state.latest_draft,
        "latest_draft_sha256": state.latest_draft_sha256,
        "tail_review": state.tail_review,
        "last_validation_error": state.last_validation_error,
        "same_validation_error_count": state.same_validation_error_count,
        "literature_bundle_attempted": state.literature_bundle_attempted,
        "literature_bundle_id": state.literature_bundle_id,
    }


def _persist_state(
    config: RunnableConfig | None, state: _HypothesisState
) -> Path | None:
    path = _working_state_path(config)
    if path is None:
        return None
    with state._persistence_lock:
        temp: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temp_name = tempfile.mkstemp(
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
            )
            temp = Path(temp_name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(
                    _working_state_payload(state),
                    handle,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                handle.write("\n")
            os.replace(temp, path)
            state.persistence_warning = None
            return path
        except OSError as exc:
            state.persistence_warning = f"working state could not be persisted: {exc}"
            return None
        finally:
            if temp is not None:
                try:
                    temp.unlink()
                except FileNotFoundError:
                    pass


def _evidence_sha256(register: EvidenceRegister) -> str:
    return canonical_json_sha256({"evidence_register": register.all()})


def _draft_skeleton(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": RESPONSE_VERSION,
        "task_name": request["task_name"],
        "research_question": request["research_question"],
        "response_kind": "hypotheses_ready",
        "candidates": [],
        "pairwise_distinctions": [],
        "portfolio_notes": None,
    }


def _normalize_working_draft(
    payload: object, request: dict[str, Any]
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("working draft must be a JSON object")
    draft = deepcopy(payload)
    allowed = {
        "schema_version",
        "task_name",
        "research_question",
        "response_kind",
        "candidates",
        "pairwise_distinctions",
        "portfolio_notes",
    }
    unknown = sorted(set(draft) - allowed)
    if unknown:
        raise ValueError(f"working draft contains unsupported fields: {unknown}")
    expected = _draft_skeleton(request)
    for key in ("schema_version", "task_name", "research_question", "response_kind"):
        if key in draft and draft[key] != expected[key]:
            raise ValueError(f"working draft {key} does not match the bound request")
        draft[key] = expected[key]
    draft.setdefault("candidates", [])
    draft.setdefault("pairwise_distinctions", [])
    draft.setdefault("portfolio_notes", None)
    if not isinstance(draft["candidates"], list):
        raise ValueError("working draft candidates must be an array")
    if not isinstance(draft["pairwise_distinctions"], list):
        raise ValueError("working draft pairwise_distinctions must be an array")
    if draft["portfolio_notes"] is not None and not isinstance(
        draft["portfolio_notes"], str
    ):
        raise ValueError("working draft portfolio_notes must be a string or null")
    candidate_ids: list[str] = []
    for index, candidate in enumerate(draft["candidates"]):
        if not isinstance(candidate, dict):
            raise ValueError(f"working draft candidate {index} must be an object")
        candidate_id = candidate.get("id")
        if not isinstance(candidate_id, str) or SAFE_ID.fullmatch(candidate_id) is None:
            raise ValueError(f"working draft candidate {index} has an invalid id")
        candidate_ids.append(candidate_id)
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("working draft candidate ids must be unique")
    return draft


def _merge_draft_changes(target: dict[str, Any], changes: dict[str, Any]) -> None:
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge_draft_changes(target[key], value)
        else:
            target[key] = deepcopy(value)


def _draft_warnings(
    state: _HypothesisState, request: dict[str, Any]
) -> list[dict[str, Any]]:
    draft = state.latest_draft
    if not isinstance(draft, dict):
        return [
            {
                "code": "no_draft",
                "candidate_id": None,
                "message": "No working hypothesis draft exists yet.",
            }
        ]
    candidates = draft.get("candidates")
    if not isinstance(candidates, list):
        return [
            {
                "code": "invalid_candidates",
                "candidate_id": None,
                "message": "The working draft candidates value is not an array.",
            }
        ]

    warnings: list[dict[str, Any]] = []

    def add(code: str, message: str, candidate_id: str | None = None) -> None:
        if len(warnings) < 50:
            warnings.append(
                {
                    "code": code,
                    "candidate_id": candidate_id,
                    "message": message,
                }
            )

    for entry in state.evidence_register.all():
        try:
            validate_evidence_provenance(request, entry)
        except Exception as exc:
            add(
                "invalid_evidence_provenance",
                f"Evidence {entry['evidence_id']} has invalid provenance: {exc}",
            )

    if len(candidates) > request["max_candidates"]:
        add(
            "candidate_budget_exceeded",
            f"Draft has {len(candidates)} candidates; maximum is {request['max_candidates']}.",
        )
    if candidates and not state.literature_bundle_attempted:
        add(
            "literature_pass_missing",
            (
                "A cached task-literature pass has not been attempted for the "
                "exact bound question. Build it before adding more candidates "
                "or returning the draft."
            ),
        )
    if FIXED_PROTOCOL_REQUEST.search(request["research_question"]):
        portfolio_notes = draft.get("portfolio_notes")
        portfolio_text = (
            portfolio_notes.strip() if isinstance(portfolio_notes, str) else ""
        )
        if not portfolio_text:
            add(
                "fixed_protocol_plan_missing",
                (
                    "The request fixes the primary evaluation protocol and forbids "
                    "adaptive feature changes after a baseline failure. Persist a "
                    "portfolio-level plan that executes and reports the fixed "
                    "protocol alone first; post-hoc alternatives require a separate "
                    "preregistered future holdout."
                ),
            )
        elif ADAPTIVE_HOLDOUT_REUSE.search(portfolio_text):
            add(
                "fixed_protocol_adaptive_reuse",
                (
                    "The portfolio reuses the fixed holdouts to compare post-hoc "
                    "single-feature, subgroup, or added-feature alternatives. Run "
                    "and report the preregistered primary protocol alone first. "
                    "Revise the scientific claim after failure, and evaluate any "
                    "new alternative only on a separate preregistered future holdout."
                ),
            )

    statements: dict[str, str] = {}
    mechanisms: dict[str, str] = {}
    candidate_ids: set[str] = set()
    all_linked_evidence_ids: set[str] = set()
    candidate_linked_evidence_ids: dict[str, set[str]] = {}
    candidate_texts: dict[str, str] = {}
    task_literature_markers: dict[tuple[str, str], set[str]] = {}
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            add("invalid_candidate", f"Candidate {index} is not an object.")
            continue
        candidate_id = candidate.get("id")
        if not isinstance(candidate_id, str):
            add("invalid_candidate_id", f"Candidate {index} has no valid id.")
            continue
        candidate_ids.add(candidate_id)
        missing = sorted(REQUIRED_CANDIDATE_FIELDS - set(candidate))
        if missing:
            add(
                "candidate_incomplete",
                f"Candidate is missing fields: {', '.join(missing)}.",
                candidate_id,
            )

        if (
            SOLAR_CYCLE_CONTEXT.search(request["research_question"]) is not None
            and AMBIGUOUS_SOLAR_CYCLE_TERM.search(
                json.dumps(candidate, ensure_ascii=False)
            )
            is not None
        ):
            add(
                "ambiguous_solar_cycle_unit",
                "Candidate uses ambiguous '下一周' in a solar-cycle task; write "
                "'下一周期' or '下一太阳活动周期', or explicitly state a seven-day "
                "forecast horizon when one week is intended.",
                candidate_id,
            )

        statement = candidate.get("statement")
        if isinstance(statement, str) and statement.strip():
            normalized = " ".join(statement.split())
            other = statements.get(normalized)
            if other is not None:
                add(
                    "duplicate_statement",
                    f"Statement duplicates candidate {other}.",
                    candidate_id,
                )
            else:
                statements[normalized] = candidate_id

        mechanism = candidate.get("mechanism")
        summary = mechanism.get("summary") if isinstance(mechanism, dict) else None
        if isinstance(summary, str) and summary.strip():
            normalized = " ".join(summary.split())
            other = mechanisms.get(normalized)
            if other is not None:
                add(
                    "duplicate_mechanism",
                    f"Mechanism duplicates candidate {other}.",
                    candidate_id,
                )
            else:
                mechanisms[normalized] = candidate_id

        supporting = candidate.get("supporting_evidence", [])
        opposing = candidate.get("opposing_evidence", [])
        gaps = candidate.get("evidence_gaps", [])
        if (
            isinstance(supporting, list)
            and isinstance(opposing, list)
            and not supporting
            and not opposing
        ):
            add(
                "candidate_evidence_unlinked",
                (
                    "Candidate has no linked supporting or opposing/limiting "
                    "evidence. Bind evidence for this candidate-specific claim "
                    "and attach its evidence_id before returning; an evidence "
                    "gap alone does not establish the candidate's evidence relation."
                ),
                candidate_id,
            )
        for evidence_kind, links, allowed_roles in (
            ("supporting", supporting, {"supports", "limits"}),
            ("opposing", opposing, {"opposes"}),
        ):
            if not isinstance(links, list):
                add(
                    "invalid_evidence_links",
                    f"{evidence_kind} evidence must be an array.",
                    candidate_id,
                )
                continue
            for link in links:
                evidence_id = (
                    link.get("evidence_id") if isinstance(link, dict) else None
                )
                entry = (
                    state.evidence_register.get(evidence_id)
                    if isinstance(evidence_id, str)
                    else None
                )
                if entry is None:
                    add(
                        "unbound_evidence",
                        f"{evidence_kind} evidence references an unbound id: "
                        f"{evidence_id!r}.",
                        candidate_id,
                    )
                elif (
                    not entry["verified_support"] or entry["role"] not in allowed_roles
                ):
                    add(
                        "evidence_role_mismatch",
                        f"{evidence_kind} evidence {evidence_id} is not verified "
                        "for that role.",
                        candidate_id,
                    )
        if not supporting and not gaps:
            add(
                "evidence_gap_missing",
                "Candidate has neither supporting evidence nor an explicit evidence gap.",
                candidate_id,
            )
        confidence = candidate.get("confidence")
        if (
            isinstance(confidence, dict)
            and confidence.get("level") == "high"
            and (not supporting or bool(opposing))
        ):
            add(
                "high_confidence_unsupported",
                "High confidence is inconsistent with missing support or opposing evidence.",
                candidate_id,
            )

        scope = candidate.get("scope_conditions")
        required_scope_fields = {
            "target_system",
            "temporal_scope",
            "spatial_scope",
            "data_scope",
            "method_scope",
            "holds_when",
            "does_not_apply_when",
            "generalization_limits",
        }
        if not isinstance(scope, dict):
            add(
                "scope_conditions_missing",
                "Candidate must separate target, temporal, spatial, data, method, "
                "holds-when, does-not-apply, and generalization boundaries.",
                candidate_id,
            )
        else:
            missing_scope = sorted(required_scope_fields - set(scope))
            empty_scope = sorted(
                key
                for key in required_scope_fields & set(scope)
                if scope.get(key) in (None, "", [])
            )
            if missing_scope or empty_scope:
                details = []
                if missing_scope:
                    details.append(f"missing: {', '.join(missing_scope)}")
                if empty_scope:
                    details.append(f"empty: {', '.join(empty_scope)}")
                add(
                    "scope_conditions_incomplete",
                    "Boundary conditions are incomplete (" + "; ".join(details) + ").",
                    candidate_id,
                )

        epistemic = candidate.get("epistemic_status")
        if not isinstance(epistemic, dict) or any(
            not epistemic.get(key)
            for key in ("claim", "mechanism", "empirical_support", "basis")
        ):
            add(
                "epistemic_status_missing",
                "Candidate must label the claim, mechanism inference, empirical "
                "support level, and the basis for those labels.",
                candidate_id,
            )

        uncertainty = candidate.get("uncertainty")
        if (
            not isinstance(uncertainty, dict)
            or not uncertainty.get("sources")
            or not uncertainty.get("implications")
            or not uncertainty.get("reduction_strategy")
        ):
            add(
                "uncertainty_incomplete",
                "Candidate must state uncertainty sources, inferential implications, "
                "and a concrete reduction strategy.",
                candidate_id,
            )

        linked_evidence_ids = {
            link.get("evidence_id")
            for links in (supporting, opposing)
            if isinstance(links, list)
            for link in links
            if isinstance(link, dict) and isinstance(link.get("evidence_id"), str)
        }
        candidate_linked_evidence_ids[candidate_id] = linked_evidence_ids
        candidate_texts[candidate_id] = json.dumps(candidate, ensure_ascii=False)
        candidate_narrative = json.dumps(
            {
                field_name: candidate.get(field_name)
                for field_name in (
                    "statement",
                    "applicability",
                    "assumptions",
                    "mechanism",
                    "predictions",
                    "evidence_gaps",
                    "alternative_explanations",
                    "confounders",
                    "falsification_conditions",
                    "next_test",
                    "confidence",
                )
            },
            ensure_ascii=False,
        )
        if (
            len(CHINESE_CHARACTER.findall(request["research_question"])) >= 4
            and len(CHINESE_CHARACTER.findall(candidate_narrative)) < 12
        ):
            add(
                "candidate_language_mismatch",
                (
                    "The bound research question is Chinese, but this candidate's "
                    "human-readable narrative is not Chinese. Translate every "
                    "candidate field to Chinese; stable ids, schema keys, source "
                    "titles, and technical abbreviations may remain unchanged."
                ),
                candidate_id,
            )
        all_linked_evidence_ids.update(linked_evidence_ids)
        grounded_parts = [request["research_question"]]
        linked_non_wiki_evidence: list[str] = []
        for evidence_id in linked_evidence_ids:
            entry = state.evidence_register.get(evidence_id)
            if (
                entry is not None
                and entry["verified_support"]
                and not entry["material_id"].startswith("kb_")
            ):
                grounded_parts.append(entry["excerpt"])
                linked_non_wiki_evidence.append(entry["excerpt"])
                if str(entry.get("material_id") or "").startswith("litbundle_"):
                    for links in (supporting, opposing):
                        if not isinstance(links, list):
                            continue
                        for link in links:
                            if (
                                not isinstance(link, dict)
                                or link.get("evidence_id") != evidence_id
                            ):
                                continue
                            relation_note = str(link.get("relation_note") or "")
                            for author, year in LITERATURE_AUTHOR_YEAR.findall(
                                relation_note
                            ):
                                task_literature_markers.setdefault(
                                    (author.lower(), year), set()
                                ).add(evidence_id)

        candidate_text = candidate_texts[candidate_id]
        linked_non_wiki_text = "\n".join(linked_non_wiki_evidence)
        sample_grounding_text = "\n".join(
            [request["research_question"], linked_non_wiki_text]
        )
        evidence_gaps_text = json.dumps(
            candidate.get("evidence_gaps", []), ensure_ascii=False
        )
        if (
            READINESS_REQUEST.search(request["research_question"])
            and POSITIVE_READINESS_CLAIM.search(str(candidate.get("statement") or ""))
            and UNVERIFIED_CURRENT_STATE.search(evidence_gaps_text)
        ):
            add(
                "readiness_claim_unverified",
                (
                    "The candidate makes a positive current readiness or "
                    "directional-prediction claim while its own evidence gaps say "
                    "the current observational state is unverified. Recast it as "
                    "a conditional branch only, and keep the present decision at "
                    "not ready until those observations are verified."
                ),
                candidate_id,
            )
        for timing_match in EVENT_TIMING_WINDOW.finditer(candidate_text):
            timing_text = timing_match.group(0)
            if timing_text not in sample_grounding_text:
                add(
                    "ungrounded_event_timing",
                    (
                        f"Event timing {timing_text!r} is not stated in the user "
                        "request or linked non-Wiki evidence. Do not infer a cycle "
                        "minimum, confirmation, or precursor-stability window from "
                        "average cycle timing; state the event as unverified."
                    ),
                    candidate_id,
                )
                break
        for sample_match in APPROX_CYCLE_SAMPLE_COUNT.finditer(candidate_text):
            sample_count = sample_match.group(1)
            grounded_count = re.search(
                rf"(?:约|大约|roughly|about)?\s*{re.escape(sample_count)}"
                r"\s*(?:个\s*)?(?:完整\s*)?(?:独立\s*)?"
                r"(?:活动周|太阳周|周期|cycles?|samples?)",
                sample_grounding_text,
                re.IGNORECASE,
            )
            if grounded_count is None:
                add(
                    "ungrounded_sample_count",
                    (
                        f"Approximate independent-cycle sample count "
                        f"{sample_match.group(0)!r} is not stated in the user "
                        "request or linked non-Wiki evidence. Keep the limitation "
                        "qualitative until an exact observational coverage source "
                        "is bound."
                    ),
                    candidate_id,
                )
                break
        statement_text = str(candidate.get("statement") or "")
        if (
            BMR_CONTEXT.search(statement_text)
            and CYCLE_AMPLITUDE_CONTEXT.search(statement_text)
            and BMR_AMPLITUDE_CAUSALITY.search(statement_text)
            and not BMR_PRECURSOR_ORDER.search(statement_text)
        ):
            add(
                "temporal_causal_order_unbound",
                (
                    "A BMR/tilt fluctuation is linked causally to cycle amplitude "
                    "without an explicit precursor timeline. State whether BMRs in "
                    "cycle n alter the polar seed for cycle n+1; do not let BMRs that "
                    "emerge during a cycle become a cause of that same cycle's already "
                    "emerging toroidal-flux amplitude. Otherwise narrow the claim to "
                    "polar-field uncertainty and record amplitude causality as a gap."
                ),
                candidate_id,
            )
        if DOMINANCE_CLAIM.search(statement_text) and not DOMINANCE_CLAIM.search(
            linked_non_wiki_text
        ):
            add(
                "causal_dominance_unbound",
                (
                    "The candidate uses determines, dominates, primary, or an "
                    "equivalent causal-dominance claim, but its linked non-Wiki "
                    "evidence does not make that comparative attribution. Narrow "
                    "the statement to the supported association or conditional "
                    "mechanism, and record causal dominance as an evidence gap."
                ),
                candidate_id,
            )
        if (
            TRANSPORT_CONTEXT.search(statement_text)
            and CYCLE_AMPLITUDE_CONTEXT.search(statement_text)
            and DOMINANCE_CLAIM.search(statement_text)
            and not (
                TRANSPORT_CONTEXT.search(linked_non_wiki_text)
                and CYCLE_AMPLITUDE_CONTEXT.search(linked_non_wiki_text)
                and DOMINANCE_CLAIM.search(linked_non_wiki_text)
            )
        ):
            add(
                "transport_amplitude_overclaim",
                (
                    "The candidate states that surface transport determines or "
                    "dominates cycle amplitude without linked non-Wiki evidence "
                    "supporting that causal strength. Narrow the claim to polar-field "
                    "buildup/modulation and record amplitude dominance as an evidence gap."
                ),
                candidate_id,
            )
        predictions = candidate.get("predictions", [])
        if TRANSPORT_CONTEXT.search(candidate_text) and isinstance(predictions, list):
            for prediction in predictions:
                if not isinstance(prediction, dict):
                    continue
                directional_text = " ".join(
                    str(prediction.get(field_name) or "")
                    for field_name in ("statement", "would_weaken_if")
                )
                if not (
                    TRANSPORT_CONTEXT.search(directional_text)
                    and CYCLE_AMPLITUDE_CONTEXT.search(directional_text)
                    and DIRECTION_CLAIM.search(directional_text)
                ):
                    continue
                if (
                    TRANSPORT_CONTEXT.search(linked_non_wiki_text)
                    and CYCLE_AMPLITUDE_CONTEXT.search(linked_non_wiki_text)
                    and DIRECTION_CLAIM.search(linked_non_wiki_text)
                ):
                    continue
                add(
                    "transport_direction_overclaim",
                    (
                        f"Prediction {prediction.get('id') or 'unknown'} assigns a "
                        "directional transport-to-amplitude effect without linked "
                        "non-Wiki evidence for that sign. Make the effect "
                        "regime-dependent and test competing directions."
                    ),
                    candidate_id,
                )
        normalized_grounding = {
            "".join(match.group(0).split()).lower()
            for part in grounded_parts
            for match in HARD_NUMERIC_CUTOFF.finditer(part)
        }
        threshold_texts: list[tuple[str, str]] = []
        for field_name in ("statement", "applicability"):
            value = candidate.get(field_name)
            if isinstance(value, str):
                threshold_texts.append((field_name, value))
        mechanism = candidate.get("mechanism")
        if isinstance(mechanism, dict):
            for field_name in ("summary", "physical_basis"):
                value = mechanism.get(field_name)
                if isinstance(value, str):
                    threshold_texts.append((f"mechanism.{field_name}", value))
            for value in mechanism.get("required_premises", []):
                if isinstance(value, str):
                    threshold_texts.append(("mechanism.required_premises", value))
        if isinstance(scope, dict):
            for field_name in (
                "target_system",
                "temporal_scope",
                "spatial_scope",
                "data_scope",
                "method_scope",
            ):
                value = scope.get(field_name)
                if isinstance(value, str):
                    threshold_texts.append((f"scope_conditions.{field_name}", value))
            for field_name in (
                "holds_when",
                "does_not_apply_when",
                "generalization_limits",
            ):
                values = scope.get(field_name, [])
                if isinstance(values, list):
                    threshold_texts.extend(
                        (f"scope_conditions.{field_name}", value)
                        for value in values
                        if isinstance(value, str)
                    )
        for field_name in (
            "assumptions",
            "evidence_gaps",
            "alternative_explanations",
            "confounders",
            "falsification_conditions",
        ):
            values = candidate.get(field_name, [])
            if isinstance(values, list):
                threshold_texts.extend(
                    (field_name, value) for value in values if isinstance(value, str)
                )
        for evidence_kind, links in (
            ("supporting_evidence", supporting),
            ("opposing_evidence", opposing),
        ):
            if isinstance(links, list):
                for link in links:
                    if not isinstance(link, dict):
                        continue
                    relation_note = link.get("relation_note")
                    if isinstance(relation_note, str):
                        threshold_texts.append(
                            (f"{evidence_kind}.relation_note", relation_note)
                        )
        predictions = candidate.get("predictions", [])
        if isinstance(predictions, list):
            for prediction in predictions:
                if not isinstance(prediction, dict):
                    continue
                prediction_id = str(prediction.get("id") or "unknown")
                for field_name in ("statement", "observable", "would_weaken_if"):
                    value = prediction.get(field_name)
                    if isinstance(value, str):
                        threshold_texts.append(
                            (f"prediction.{prediction_id}.{field_name}", value)
                        )
        next_test = candidate.get("next_test")
        if isinstance(next_test, dict):
            for field_name in ("objective", "discriminating_power"):
                value = next_test.get(field_name)
                if isinstance(value, str):
                    threshold_texts.append((f"next_test.{field_name}", value))
            expected = next_test.get("expected_signals", [])
            if isinstance(expected, list):
                threshold_texts.extend(
                    ("next_test.expected_signals", value)
                    for value in expected
                    if isinstance(value, str)
                )
        if isinstance(uncertainty, dict):
            for value in uncertainty.get("sources", []):
                if isinstance(value, str):
                    threshold_texts.append(("uncertainty.sources", value))
            for field_name in ("implications", "reduction_strategy"):
                value = uncertainty.get(field_name)
                if isinstance(value, str):
                    threshold_texts.append((f"uncertainty.{field_name}", value))
        if isinstance(epistemic, dict):
            value = epistemic.get("basis")
            if isinstance(value, str):
                threshold_texts.append(("epistemic_status.basis", value))
        for field_name, value in threshold_texts:
            for match in HARD_NUMERIC_CUTOFF.finditer(value):
                token = "".join(match.group(0).split()).lower()
                if token in normalized_grounding:
                    continue
                add(
                    "ungrounded_numeric_threshold",
                    (
                        f"{field_name} contains an ungrounded numeric threshold "
                        f"{match.group(0)!r}; remove it, make it qualitative, or "
                        "link verified non-Wiki evidence containing the same threshold."
                    ),
                    candidate_id,
                )

        empirical_evidence = []
        verified_evidence = []
        for evidence_id in linked_evidence_ids:
            entry = state.evidence_register.get(evidence_id)
            if entry is None or not entry["verified_support"]:
                continue
            verified_evidence.append(entry)
            if (
                entry["role"] == "supports"
                and entry["evidence_kind"] in {"experiment", "literature", "upstream"}
                and not entry["material_id"].startswith("kb_")
            ):
                empirical_evidence.append(entry)
        if isinstance(epistemic, dict):
            empirical_level = epistemic.get("empirical_support")
            if empirical_level in {"verified", "partial"} and not empirical_evidence:
                add(
                    "epistemic_support_mismatch",
                    f"Empirical support is labelled {empirical_level!r}, but no "
                    "verified non-Wiki supporting evidence is linked.",
                    candidate_id,
                )
            if (
                epistemic.get("claim") == "evidence_constrained_hypothesis"
                and not verified_evidence
            ):
                add(
                    "epistemic_claim_mismatch",
                    "An evidence-constrained claim must link verified evidence.",
                    candidate_id,
                )
            if (
                epistemic.get("mechanism") == "supported_inference"
                and not verified_evidence
            ):
                add(
                    "epistemic_mechanism_mismatch",
                    "A supported mechanism inference must link verified evidence.",
                    candidate_id,
                )

        scope_texts: list[str] = []
        if isinstance(scope, dict):
            for field_name in (
                "target_system",
                "temporal_scope",
                "spatial_scope",
                "data_scope",
                "method_scope",
            ):
                value = scope.get(field_name)
                if isinstance(value, str):
                    scope_texts.append(value)
            values = scope.get("holds_when", [])
            if isinstance(values, list):
                scope_texts.extend(value for value in values if isinstance(value, str))
        if not empirical_evidence:
            for value in [
                candidate.get("statement", ""),
                candidate.get("applicability", ""),
                *scope_texts,
            ]:
                if not isinstance(value, str):
                    continue
                match = UNIVERSAL_SCOPE_CLAIM.search(value)
                if match is not None:
                    add(
                        "unsupported_scope_generalization",
                        f"Unbounded scope term {match.group(0)!r} is unsupported; "
                        "narrow the population/data regime and state the extrapolation limit.",
                        candidate_id,
                    )
                    break

        decision_texts: list[tuple[str, str]] = []
        falsifiers = candidate.get("falsification_conditions", [])
        if isinstance(falsifiers, list):
            decision_texts.extend(
                ("falsification_conditions", value)
                for value in falsifiers
                if isinstance(value, str)
            )
        if isinstance(scope, dict):
            for scope_field in ("holds_when", "does_not_apply_when"):
                values = scope.get(scope_field, [])
                if isinstance(values, list):
                    decision_texts.extend(
                        (f"scope_conditions.{scope_field}", value)
                        for value in values
                        if isinstance(value, str)
                    )
        if isinstance(predictions, list):
            for prediction in predictions:
                if not isinstance(prediction, dict):
                    continue
                for prediction_field in ("statement", "would_weaken_if"):
                    value = prediction.get(prediction_field)
                    if isinstance(value, str):
                        decision_texts.append((f"prediction.{prediction_field}", value))
        if isinstance(next_test, dict):
            value = next_test.get("discriminating_power")
            if isinstance(value, str):
                decision_texts.append(("next_test.discriminating_power", value))
            expected_signals = next_test.get("expected_signals", [])
            if isinstance(expected_signals, list):
                decision_texts.extend(
                    ("next_test.expected_signals", value)
                    for value in expected_signals
                    if isinstance(value, str)
                )
        for field_name, value in decision_texts:
            match = VAGUE_DECISION_RULE.search(value)
            if match is not None and DECISION_RULE_QUALIFIER.search(value) is None:
                add(
                    "unoperationalized_decision_rule",
                    f"{field_name} uses vague decision term {match.group(0)!r}; "
                    "state a directional observable result or a preregistered "
                    "decision rule/test with an uncertainty bound.",
                    candidate_id,
                )

    for candidate_id, candidate_text in candidate_texts.items():
        linked_ids = candidate_linked_evidence_ids.get(candidate_id, set())
        mentioned_markers = {
            (author.lower(), year)
            for author, year in LITERATURE_AUTHOR_YEAR.findall(candidate_text)
        }
        for marker in sorted(mentioned_markers):
            source_ids = task_literature_markers.get(marker)
            if not source_ids or linked_ids.intersection(source_ids):
                continue
            author, year = marker
            add(
                "cross_candidate_literature_citation",
                (
                    f"Candidate prose cites {author.title()} {year}, but the known "
                    "task-literature evidence for that citation is attached only "
                    "to another candidate. Bind the source for this candidate-specific "
                    "claim/role and attach the returned evidence_id, or remove the citation."
                ),
                candidate_id,
            )

    for entry in state.evidence_register.all():
        evidence_id = entry["evidence_id"]
        material_id = str(entry.get("material_id") or "")
        is_attachable = (
            bool(entry.get("verified_support"))
            and entry.get("role") in {"supports", "opposes", "limits"}
            and evidence_id not in all_linked_evidence_ids
        )
        if is_attachable and material_id.startswith("litbundle_"):
            add(
                "unattached_literature_evidence",
                (
                    f"Bound literature evidence {evidence_id} is not attached "
                    "to any candidate; patch the matching candidate's "
                    "supporting_evidence or opposing_evidence before returning."
                ),
            )
        elif is_attachable and material_id.startswith("kb_"):
            add(
                "unattached_wiki_evidence",
                (
                    f"Bound Wiki evidence {evidence_id} is not attached to any "
                    "candidate; patch the matching candidate's supporting_evidence "
                    "or opposing_evidence with a bounded relation_note before returning."
                ),
            )

    distinctions = draft.get("pairwise_distinctions", [])
    if len(candidate_ids) > 1:
        covered: set[str] = set()
        if isinstance(distinctions, list):
            for row in distinctions:
                if isinstance(row, dict):
                    for key in ("left_id", "right_id"):
                        value = row.get(key)
                        if isinstance(value, str) and value in candidate_ids:
                            covered.add(value)
        for uncovered in sorted(candidate_ids - covered):
            add(
                "candidate_not_distinguished",
                "Candidate is not covered by any pairwise distinction.",
                uncovered,
            )
    return warnings


def _unresolved_warning_error(
    warnings: list[dict[str, Any]], *, action: str
) -> RuntimeError:
    codes = sorted(
        {
            str(warning.get("code") or "unknown_warning")
            for warning in warnings
            if isinstance(warning, dict)
        }
    )
    return RuntimeError(
        f"Cannot {action} while draft review warnings remain: " + ", ".join(codes)
    )


def _draft_summary(
    state: _HypothesisState,
    request: dict[str, Any],
    config: RunnableConfig | None,
) -> dict[str, Any]:
    draft = state.latest_draft if isinstance(state.latest_draft, dict) else {}
    candidates = draft.get("candidates", [])
    candidate_count = len(candidates) if isinstance(candidates, list) else 0
    warnings = _draft_warnings(state, request)
    pool_sha256 = (
        candidate_pool_sha256(candidates) if isinstance(candidates, list) else None
    )
    review_current = tail_review_is_current(
        state.tail_review,
        draft,
        evidence_sha256=_evidence_sha256(state.evidence_register),
    )
    review_status = (
        "current"
        if review_current
        else ("stale" if state.tail_review is not None else "missing")
    )
    result = {
        "schema_version": "scientific-hypothesis-draft-status-v1",
        "status": "draft",
        "candidate_count": candidate_count,
        "draft_sha256": state.latest_draft_sha256,
        "candidate_pool_sha256": pool_sha256,
        "tail_review_status": review_status,
        "tail_review_required": candidate_count > 1
        or bool(
            candidate_count and LONG_TAIL_QUERY.search(request["research_question"])
        ),
        "tail_review_frontier_ids": (
            state.tail_review.get("pareto_frontier_ids", [])
            if isinstance(state.tail_review, dict)
            else []
        ),
        "tail_review_selected_ids": (
            state.tail_review.get("selected_candidate_ids", [])
            if isinstance(state.tail_review, dict)
            else []
        ),
        "checkpoint_available": state.validated_response is not None,
        "draft_differs_from_checkpoint": bool(
            state.latest_draft_sha256
            and state.preflight_response_sha256
            and state.latest_draft_sha256 != state.preflight_response_sha256
        ),
        "soft_warning_count": len(warnings),
        "soft_warnings": warnings,
        "hard_validation_run": False,
        "state_persistence": (
            "workspace" if _working_state_path(config) is not None else "memory_only"
        ),
        "persistence_warning": state.persistence_warning,
    }
    if warnings:
        warning_codes = {warning["code"] for warning in warnings}
        literature_required = "literature_pass_missing" in warning_codes
        result.update(
            {
                "return_gate": "blocked_until_warnings_resolved",
                "natural_language_return_allowed": False,
                "next_required_action": {
                    "tool": (
                        "scientific_hypothesis_build_literature_bundle"
                        if literature_required
                        else "scientific_hypothesis_update_draft"
                    ),
                    "instruction": (
                        "Build one bounded cached-literature bundle with a "
                        "concrete bilingual focus for the exact bound question."
                        if literature_required
                        else (
                            "Apply the smallest complete patch that resolves every "
                            "avoidable soft warning. Do not narrate the intended "
                            "repair and do not return natural language yet."
                        )
                    ),
                },
            }
        )
    else:
        result.update(
            {
                "return_gate": "ready_for_persisted_render",
                "natural_language_return_allowed": True,
                "next_required_action": None,
            }
        )
    return result


def _load_persisted_state(path: Path) -> _HypothesisState:
    state = _HypothesisState()
    if not path.is_file():
        return state
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("working state must be a JSON object")
        if raw.get("schema_version") != WORKING_STATE_VERSION:
            raise ValueError("unsupported scientific-hypothesis working-state version")
        request = validate_hypothesis_request(raw.get("request"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        state.persistence_warning = f"persisted working state was ignored: {exc}"
        return state

    state.request = request
    state.request_sha256 = canonical_json_sha256(request)
    warnings: list[str] = []
    stored_request_sha = raw.get("request_sha256")
    if stored_request_sha != state.request_sha256:
        warnings.append("stored request hash was repaired")

    evidence_rows = raw.get("evidence_register", [])
    if isinstance(evidence_rows, list):
        for index, row in enumerate(evidence_rows):
            try:
                state.evidence_register.bind(row)
            except Exception as exc:
                warnings.append(f"evidence entry {index} was skipped: {exc}")
    else:
        warnings.append("invalid evidence register was ignored")

    attempts = raw.get("checkpoint_attempts", 0)
    if isinstance(attempts, int) and not isinstance(attempts, bool) and attempts >= 0:
        state.preflight_attempts = attempts

    draft = raw.get("latest_draft")
    if isinstance(draft, dict):
        state.latest_draft = draft
        state.latest_draft_sha256 = canonical_json_sha256(draft)

    tail_review = raw.get("tail_review")
    if isinstance(tail_review, dict):
        state.tail_review = tail_review

    error = raw.get("last_validation_error")
    if isinstance(error, str) and error:
        state.last_validation_error = error
    error_count = raw.get("same_validation_error_count", 0)
    if (
        isinstance(error_count, int)
        and not isinstance(error_count, bool)
        and error_count >= 0
    ):
        state.same_validation_error_count = error_count
    state.literature_bundle_attempted = bool(
        raw.get("literature_bundle_attempted", False)
    )
    stored_bundle_id = raw.get("literature_bundle_id")
    if isinstance(stored_bundle_id, str) and stored_bundle_id:
        state.literature_bundle_id = stored_bundle_id

    checkpoint = raw.get("checkpoint")
    if isinstance(checkpoint, dict):
        try:
            result = preflight_hypothesis_response(
                request,
                checkpoint,
                state.evidence_register,
                include_validated_response=True,
            )
            checked = result.pop("_validated_response", None)
            if result.get("status") != "hypotheses_ready" or not isinstance(
                checked, dict
            ):
                raise ValueError("persisted checkpoint is not hypotheses_ready")
            checkpoint_sha = canonical_json_sha256(checked)
            if raw.get("checkpoint_sha256") != checkpoint_sha:
                warnings.append("stored checkpoint hash was repaired")
            state.validated_response = checked
            state.preflight_response_sha256 = checkpoint_sha
            evidence_sha = _evidence_sha256(state.evidence_register)
            if raw.get("checkpoint_evidence_sha256") != evidence_sha:
                warnings.append("stored checkpoint evidence hash was repaired")
            state.checkpoint_evidence_sha256 = evidence_sha
        except Exception as exc:
            warnings.append(f"invalid checkpoint was ignored: {exc}")

    if warnings:
        state.persistence_warning = "; ".join(warnings)
    return state


def read_persisted_hypothesis_draft(path: str | Path) -> dict[str, Any]:
    """Read a task-local draft receipt without mutating or checkpointing it.

    This is the deterministic recovery path used when a specialist exhausts
    its model-call budget after it has already persisted useful work.
    """

    state_path = Path(path)
    state = _load_persisted_state(state_path)
    if state.request is None:
        detail = state.persistence_warning or "no bound hypothesis request"
        raise ValueError(f"persisted hypothesis state is unusable: {detail}")
    result = _draft_summary(state, state.request, None)
    result["state_persistence"] = "workspace"
    result["state_file"] = str(state_path)
    result["bound_evidence_ids"] = [
        entry["evidence_id"] for entry in state.evidence_register.all()
    ]
    result["draft"] = deepcopy(state.latest_draft)
    result["tail_review"] = deepcopy(state.tail_review)
    return result


def render_persisted_hypothesis_reader_view(
    path: str | Path,
    *,
    partial_reason: str | None = None,
) -> str:
    """Render the persisted contract as a concise researcher-facing response."""

    return render_hypothesis_reader_markdown(
        read_persisted_hypothesis_draft(path),
        partial_reason=partial_reason,
    )


def _needs_revision(
    exc: Exception,
    *,
    state: _HypothesisState | None = None,
    count_failure: bool = False,
) -> str:
    error = str(exc)
    if state is not None and count_failure:
        if state.last_validation_error == error:
            state.same_validation_error_count += 1
        else:
            state.last_validation_error = error
            state.same_validation_error_count = 1

    repeated = bool(
        state is not None
        and state.same_validation_error_count >= MAX_SAME_CHECKPOINT_FAILURES
    )
    checkpoint_available = bool(
        state is not None
        and state.validated_response is not None
        and state.preflight_response_sha256 is not None
    )
    return json.dumps(
        {
            "schema_version": "scientific-hypothesis-outcome-v1",
            "status": "review_limit_reached" if repeated else "needs_revision",
            "working_status": "draft",
            "validation_error": error,
            "same_validation_error_count": (
                state.same_validation_error_count if state is not None else 0
            ),
            "checkpoint_preserved": checkpoint_available,
            "persistence_warning": (
                state.persistence_warning if state is not None else None
            ),
            "retry_recommended": not repeated,
            "user_message": (
                "同一检查问题已重复出现。停止自动重试，保留当前草稿并向用户说明"
                "未解决项。只有获得新证据或明确修改方案后再检查。"
                if repeated
                else "这是可继续修改的草稿。保留已正确内容，只修正列出的问题，"
                "自动修复最多再尝试一次。"
            ),
        },
        ensure_ascii=False,
        default=str,
    )


def _state(config: RunnableConfig | None) -> _HypothesisState:
    context = workspace_context_key(config)
    with _STATE_LOCK:
        existing = _STATES.get(context)
        if existing is not None:
            return existing
        path = _working_state_path(config)
        state = _load_persisted_state(path) if path is not None else _HypothesisState()
        _STATES[context] = state
        return state


def _require_active_request(state: _HypothesisState) -> dict[str, Any]:
    if state.request is None:
        raise RuntimeError(
            "No hypothesis request is bound. Call scientific_hypothesis_bind_request first."
        )
    return state.request


@tool(parse_docstring=True)
def scientific_hypothesis_bind_request(
    request_input: str, config: RunnableConfig = None
) -> str:
    """Bind a natural-language research question and return the hypothesis brief.

    This starts an optional evidence/checkpoint session. If
    ``request_input`` starts with ``@``, the remainder is treated as a path
    (relative to the project root) to a JSON request file. Otherwise the input
    is used verbatim as the research question. Rebinding the same request
    preserves evidence, drafts, and checkpoints. Binding a different request
    starts a new task-scoped working state.

    Args:
        request_input: A research question or ``@<path-to-json-request>``.

    Returns:
        JSON string containing the hypothesis brief, response contract,
        scientific boundaries, and ``request_sha256``.
    """
    try:
        supplied = request_input.strip()
        if not supplied:
            raise ValueError("Research question must not be empty")
        if supplied.startswith("@"):
            path = resolve_scoped_path(supplied[1:].strip(), config, allow_project=True)
            payload = json.loads(path.read_text(encoding="utf-8"))
            request = validate_hypothesis_request(payload)
        else:
            request = build_natural_hypothesis_request(supplied)

        brief = build_hypothesis_brief(request)
        brief["tail_search_contract"] = {
            "schema_version": TAIL_REVIEW_VERSION,
            "reviewer_mode": TAIL_REVIEWER_MODE,
            "generation_operators": sorted(GENERATION_OPERATORS),
            "search_regions": sorted(SEARCH_REGIONS),
            "rubric_items": list(RUBRIC_ITEMS),
            "benefit_metrics": list(BENEFIT_METRICS),
            "cost_metrics": list(COST_METRICS),
            "metric_levels": ["low", "medium", "high"],
            "scoring_guide": tail_review_scoring_guide(),
            "review_shape": {
                "schema_version": TAIL_REVIEW_VERSION,
                "candidate_pool_sha256": "copy from scientific_hypothesis_get_draft",
                "reviewer_mode": TAIL_REVIEWER_MODE,
                "instance_rubrics": [
                    {
                        "id": "unique rubric id",
                        "candidate_id": "one current candidate id",
                        "criterion": (
                            "question-specific necessary condition or flaw check"
                        ),
                        "basis": (
                            "derive from the bound question, bound evidence, or "
                            "a concrete candidate contrast"
                        ),
                        "status": "pass or violation",
                        "violated_guidelines": (
                            "zero or more of: " + ", ".join(GENERAL_GUIDELINES)
                        ),
                        "rationale": "specific review rationale",
                    }
                ],
                "candidates": [
                    {
                        "candidate_id": "current candidate id",
                        "generation_operator": "one controlled operator",
                        "search_region": "one controlled search region",
                        "mechanism_signature": "concise mechanism-specific signature",
                        "novelty_status": (
                            "known_baseline, adjacent_possibility, or "
                            "tail_candidate_unverified"
                        ),
                        "rubric": {
                            key: {
                                "status": "pass or violation",
                                "violated_guidelines": (
                                    "zero or more of: " + ", ".join(GENERAL_GUIDELINES)
                                ),
                                "rationale": "specific review rationale",
                            }
                            for key in RUBRIC_ITEMS
                        },
                        "tail_metrics": dict.fromkeys(
                            BENEFIT_METRICS + COST_METRICS,
                            "low, medium, or high",
                        ),
                        "reviewer_summary": "violation-first summary",
                    }
                ],
            },
            "selection_rule": (
                "Every rubric item is a hard violation gate. Among candidates "
                "without violations, code recomputes the non-dominated Pareto "
                "frontier; eligible null_control sentinels are retained."
            ),
        }
        context = workspace_context_key(config)
        with _STATE_LOCK:
            existing = _STATES.get(context)
            if (
                existing is not None
                and existing.request_sha256 == brief["request_sha256"]
            ):
                brief["binding_status"] = "already_bound"
                brief["working_state_preserved"] = True
                brief["bound_evidence_count"] = len(existing.evidence_register)
                brief["checkpoint_available"] = existing.validated_response is not None
                active_state = existing
            else:
                active_state = _HypothesisState(
                    request=request,
                    request_sha256=brief["request_sha256"],
                )
                _STATES[context] = active_state
                brief["binding_status"] = "bound"
                brief["working_state_preserved"] = False
                brief["bound_evidence_count"] = 0
                brief["checkpoint_available"] = False
        state_path = _persist_state(config, active_state)
        brief["state_persistence"] = (
            "workspace" if state_path is not None else "memory_only"
        )
        brief["persistence_warning"] = active_state.persistence_warning
        return _ok(brief)
    except Exception as exc:
        return _needs_revision(exc)


@tool(parse_docstring=True)
def scientific_hypothesis_bind_evidence(
    evidence_id: str,
    evidence_kind: str,
    material_id: str,
    excerpt: str,
    verified_support: bool,
    role: str,
    config: RunnableConfig = None,
) -> str:
    """Register one piece of upstream material as evidence for this task.

    ``verified_support`` may be true only when the material text has actually
    been checked against the claim it supports, opposes, or limits; unchecked
    material must be registered with ``role="gap"``.

    Args:
        evidence_id: Unique identifier for this evidence entry (1-64 chars).
        evidence_kind: One of ``experiment``, ``literature``, ``upstream``, ``user``.
        material_id: Identifier of the upstream material the excerpt comes from.
        excerpt: Verbatim excerpt from the material (1-2000 chars).
        verified_support: Whether the excerpt was verified against the claim.
        role: One of ``supports``, ``opposes``, ``limits``, ``gap``.

    Returns:
        JSON string with the bind outcome and total bound evidence count.
    """
    try:
        state = _state(config)
        request = _require_active_request(state)
        if len(state.evidence_register) >= MAX_EVIDENCE_BINDS:
            return _ok(
                {
                    "schema_version": "scientific-hypothesis-evidence-bound-v1",
                    "status": "budget_reached",
                    "message": "本次任务的证据绑定已达上限。请只使用已登记证据，把其余内容列为证据缺口。",
                }
            )
        if material_id.startswith("kb_"):
            raise ValueError(
                "Wiki 条目不能通过通用证据入口绑定。"
                "请使用 scientific_hypothesis_bind_wiki_evidence，"
                "由服务端核对 canonical 状态和读取回执"
            )
        if material_id.startswith("litbundle_"):
            raise ValueError(
                "任务文献包不能通过通用证据入口绑定。"
                "请使用 scientific_hypothesis_bind_literature_evidence，"
                "由服务端核对冻结快照和逐字引文"
            )
        row = {
            "evidence_id": evidence_id,
            "evidence_kind": evidence_kind,
            "material_id": material_id,
            "excerpt": excerpt,
            "verified_support": verified_support,
            "role": role,
        }
        validate_evidence_provenance(request, row)
        result = state.evidence_register.bind(row)
        state_path = _persist_state(config, state)
        result["state_persistence"] = (
            "workspace" if state_path is not None else "memory_only"
        )
        result["persistence_warning"] = state.persistence_warning
        return _ok(result)
    except Exception as exc:
        return _needs_revision(exc)


@tool(parse_docstring=True)
def scientific_hypothesis_bind_wiki_evidence(
    entry_id: str,
    config: RunnableConfig = None,
) -> str:
    """Bind one canonical Wiki entry as mechanism, scope, data, or method grounding.

    The tool re-reads the knowledge store on the server side, rejects
    candidate/deprecated/blocked entries and persists a bounded receipt with
    the exact version, confidence, valid range and provenance used by this
    hypothesis run. Wiki grounding is always registered as ``role=limits``;
    it is never observational support.

    Args:
        entry_id: Canonical Wiki entry id returned by ``kb_query``/``kb_read``.

    Returns:
        JSON string with the bound evidence id and canonical Wiki receipt.
    """
    try:
        from jw.tools.knowledge_base import _get_store, _run_context
        from knowledge_base import service as knowledge_service

        state = _state(config)
        request = _require_active_request(state)
        if len(state.evidence_register) >= MAX_EVIDENCE_BINDS:
            return _ok(
                {
                    "schema_version": "scientific-hypothesis-evidence-bound-v1",
                    "status": "budget_reached",
                    "message": "本次任务的证据绑定已达上限。请只使用已登记证据，把其余内容列为证据缺口。",
                }
            )
        agent, run_id = _run_context(config)
        if not run_id:
            raise ValueError(
                "Wiki binding requires a task-scoped run id so the prior kb_read "
                "receipt can be verified"
            )
        store = _get_store()
        prior_reads = store.provenance_for_run(run_id)
        read_receipt = next(
            (
                row
                for row in reversed(prior_reads)
                if row.get("entry_id") == entry_id
                and row.get("purpose") != "hypothesis_grounding"
            ),
            None,
        )
        if read_receipt is None:
            raise ValueError(
                f"No prior kb_read receipt exists for Wiki entry {entry_id} in "
                f"the current task run {run_id}. Call kb_read for this exact entry "
                "before binding it."
            )
        entry = knowledge_service.read(
            store,
            entry_id,
            agent=agent or "solar-hypothesis",
            run_id=run_id,
            purpose="hypothesis_grounding",
        )["entry"]
        if entry["status"] != "canonical":
            raise ValueError(
                f"Wiki 条目 {entry_id} 当前状态为 {entry['status']}。"
                "只有 canonical 条目可以进入假设状态"
            )
        if entry["type"] not in WIKI_GROUNDING_TYPES:
            raise ValueError(
                f"Wiki 条目 {entry_id} 的 type={entry['type']} 不能作为假设依据。"
                "仅允许稳定内置类型 concept、mechanism、data_source、"
                "experiment_paradigm、hypothesis_template"
            )
        receipt = build_wiki_evidence_excerpt(
            entry,
            read_receipt=read_receipt,
        )
        row = {
            "evidence_id": entry_id,
            "evidence_kind": "literature",
            "material_id": entry_id,
            "excerpt": receipt,
            "verified_support": True,
            "role": "limits",
        }
        validate_evidence_provenance(request, row)
        result = state.evidence_register.bind(row)
        state_path = _persist_state(config, state)
        result.update(
            {
                "wiki_grounding": {
                    "entry_id": entry_id,
                    "type": entry["type"],
                    "status": entry["status"],
                    "version": entry["version"],
                    "confidence": entry["confidence"],
                    "valid_range": entry.get("valid_range", ""),
                    "source_type": entry["source_type"],
                    "source_ref": entry["source_ref"],
                },
                "kb_read_receipt": {
                    "log_id": read_receipt.get("id"),
                    "run_id": read_receipt.get("run_id"),
                    "agent": read_receipt.get("agent"),
                    "purpose": read_receipt.get("purpose"),
                    "ts": read_receipt.get("ts"),
                },
                "state_persistence": (
                    "workspace" if state_path is not None else "memory_only"
                ),
                "persistence_warning": state.persistence_warning,
            }
        )
        return _ok(result)
    except Exception as exc:
        return _needs_revision(exc)


@tool(parse_docstring=True)
def scientific_hypothesis_build_literature_bundle(
    focus: str,
    limit: int = 3,
    config: RunnableConfig = None,
) -> str:
    """Build cached literature evidence for the exact bound hypothesis request.

    The active request supplies the research question server-side, so the
    caller cannot accidentally shorten, paraphrase, or otherwise detach the
    literature bundle from the hypothesis evidence contract. The focus remains
    caller-provided and must retain concrete bilingual mechanism or observable
    terms. This tool reads only the local literature cache.

    Args:
        focus: Bounded bilingual literature focus for the active question.
        limit: Number of cached sources to return, from one to five.

    Returns:
        JSON string containing the immutable task bundle and a marker that the
        exact bound request supplied its research question.
    """
    try:
        from jw.tools.knowledge_base import _get_store, _run_context
        from knowledge_base import literature

        state = _state(config)
        request = _require_active_request(state)
        _, run_id = _run_context(config)
        result = literature.build_literature_task_bundle(
            _get_store(),
            request["research_question"],
            focus,
            feed_ids=[],
            limit=limit,
            run_id=run_id,
        )
        state.literature_bundle_attempted = True
        bundle_id = result.get("bundle_id")
        state.literature_bundle_id = (
            bundle_id if isinstance(bundle_id, str) and bundle_id else None
        )
        state_path = _persist_state(config, state)
        result["request_source"] = "bound_hypothesis_request"
        result["state_persistence"] = (
            "workspace" if state_path is not None else "memory_only"
        )
        result["persistence_warning"] = state.persistence_warning
        return _ok(result)
    except Exception as exc:
        return _needs_revision(exc)


@tool(parse_docstring=True)
def scientific_hypothesis_bind_literature_evidence(
    bundle_id: str,
    source_id: str,
    role: str,
    quote: str,
    claim: str,
    config: RunnableConfig = None,
) -> str:
    """Bind one verified quote from a frozen task literature bundle.

    Unlike reusable Wiki grounding, task literature may support, oppose, or
    limit a candidate. The service checks the active research question, bundle
    membership, retraction flag, source fingerprint, and verbatim quote before
    adding evidence.

    Args:
        bundle_id: Frozen bundle id returned by
            scientific_hypothesis_build_literature_bundle.
        source_id: Source id contained in that exact bundle.
        role: supports, opposes, or limits.
        quote: Verbatim abstract quote of at most 40 words.
        claim: Bounded description of the candidate claim the quote bears on.

    Returns:
        JSON string with the bound evidence id and immutable source receipt.
    """
    try:
        from jw.tools.knowledge_base import _get_store, _run_context
        from knowledge_base.contracts import QUOTE_MAX_WORDS, quote_is_grounded

        state = _state(config)
        request = _require_active_request(state)
        if len(state.evidence_register) >= MAX_EVIDENCE_BINDS:
            return _ok(
                {
                    "schema_version": "scientific-hypothesis-evidence-bound-v1",
                    "status": "budget_reached",
                    "message": "本次任务的证据绑定已达上限。请把其余内容列为证据缺口。",
                }
            )
        normalized_role = str(role or "").strip().lower()
        if normalized_role not in {"supports", "opposes", "limits"}:
            raise ValueError("role 必须是 supports、opposes 或 limits")
        normalized_quote = " ".join(str(quote or "").split())
        if not normalized_quote or len(normalized_quote.split()) > QUOTE_MAX_WORDS:
            raise ValueError(f"quote 必须是缓存摘要中 1-{QUOTE_MAX_WORDS} 词的逐字引文")
        normalized_claim = " ".join(str(claim or "").split())
        if not normalized_claim or len(normalized_claim) > 500:
            raise ValueError("claim 必须是 1-500 字符的候选主张说明")
        store = _get_store()
        bundle = store.get_lit_task_bundle(str(bundle_id or "").strip())
        if bundle is None:
            raise ValueError(f"任务文献包不存在：{bundle_id}")
        active_question = " ".join(str(request["research_question"]).split())
        bundle_question = " ".join(str(bundle["research_question"]).split())
        if active_question != bundle_question:
            raise ValueError(
                "任务文献包绑定的研究问题与当前假设请求不一致。"
                "请用当前问题重新调用 lit_bundle_build"
            )
        _, run_id = _run_context(config)
        bundle_run_id = str(bundle.get("run_id") or "")
        if bundle_run_id and run_id and bundle_run_id != run_id:
            raise ValueError("任务文献包属于另一个运行，不能跨任务绑定")
        snapshot = next(
            (
                item
                for item in bundle["source_snapshots"]
                if str(item.get("source_id") or "") == str(source_id or "").strip()
            ),
            None,
        )
        if snapshot is None:
            raise ValueError(f"来源 {source_id} 不在任务文献包 {bundle_id} 中")
        if bool(snapshot.get("is_retracted")):
            raise ValueError("撤稿来源不能绑定为假设证据")
        if not quote_is_grounded(normalized_quote, str(snapshot.get("abstract") or "")):
            raise ValueError("quote 无法在冻结摘要快照中逐字定位")
        receipt_payload = {
            "status": "verified",
            "bundle_id": bundle["bundle_id"],
            "source_id": snapshot["source_id"],
            "family_id": snapshot.get("family_id", ""),
            "title": snapshot.get("title", ""),
            "doi": snapshot.get("doi", ""),
            "source_version": snapshot.get("source_version", ""),
            "content_fingerprint": snapshot.get("content_fingerprint", ""),
            "role": normalized_role,
            "quote": normalized_quote,
            "claim": normalized_claim,
        }
        receipt = json.dumps(
            receipt_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        evidence_id = "litevidence_" + canonical_json_sha256(receipt_payload)[:32]
        row = {
            "evidence_id": evidence_id,
            "evidence_kind": "literature",
            "material_id": bundle["bundle_id"],
            "excerpt": receipt,
            "verified_support": True,
            "role": normalized_role,
        }
        validate_evidence_provenance(request, row)
        result = state.evidence_register.bind(row)
        state_path = _persist_state(config, state)
        draft_evidence_field = (
            "opposing_evidence"
            if normalized_role == "opposes"
            else "supporting_evidence"
        )
        result.update(
            {
                "literature_evidence": receipt_payload,
                "draft_attachment_required": True,
                "draft_evidence_field": draft_evidence_field,
                "draft_attachment_instruction": (
                    f"Patch the matching candidate's {draft_evidence_field} "
                    f"with evidence_id {evidence_id}; binding registers the "
                    "source but does not modify the draft."
                ),
                "state_persistence": (
                    "workspace" if state_path is not None else "memory_only"
                ),
                "persistence_warning": state.persistence_warning,
            }
        )
        return _ok(result)
    except Exception as exc:
        return _needs_revision(exc)


@tool(parse_docstring=True)
def scientific_hypothesis_update_draft(
    operation: str,
    payload_json: str,
    config: RunnableConfig = None,
) -> str:
    """Incrementally update the mutable hypothesis draft without hard validation.

    Supported operations are ``replace``, ``upsert_candidate``,
    ``patch_candidate``, ``remove_candidate``, ``set_distinctions``, and
    ``set_portfolio_notes``. Candidate patches recursively update only the
    supplied fields. Every successful change is persisted and returns soft
    evidence/completeness warnings; it does not create a checkpoint or publish.

    Args:
        operation: One supported draft operation.
        payload_json: JSON payload containing the draft, candidate, patch,
            candidate identifier, distinctions, or portfolio notes required by
            the selected operation.

    Returns:
        JSON string with the updated draft summary and non-blocking warnings.
    """
    state = _state(config)
    state._persistence_lock.acquire()
    try:
        request = _require_active_request(state)
        if operation not in DRAFT_OPERATIONS:
            raise ValueError(
                f"operation must be one of: {', '.join(sorted(DRAFT_OPERATIONS))}"
            )
        payload = json.loads(payload_json)
        if operation == "patch_candidate" and isinstance(payload, dict):
            candidate_id = payload.get("candidate_id") or payload.get("id")
            changes = payload.get("changes")
            if not isinstance(changes, dict):
                changes = payload.get("patch")
            if not isinstance(changes, dict):
                changes = {
                    key: value
                    for key, value in payload.items()
                    if key not in {"candidate_id", "id", "changes", "patch"}
                }
            payload = {
                "candidate_id": candidate_id,
                "changes": changes,
            }
        elif operation == "set_distinctions" and isinstance(payload, dict):
            wrapped = payload.get("pairwise_distinctions")
            if not isinstance(wrapped, list):
                wrapped = payload.get("distinctions")
            if isinstance(wrapped, list):
                payload = wrapped
        elif operation == "set_portfolio_notes" and isinstance(payload, dict):
            if "portfolio_notes" in payload:
                payload = payload["portfolio_notes"]
        if operation == "replace":
            draft = _normalize_working_draft(payload, request)
        else:
            base = (
                state.latest_draft
                if isinstance(state.latest_draft, dict)
                else _draft_skeleton(request)
            )
            draft = _normalize_working_draft(base, request)
            candidates = draft["candidates"]

            if operation == "upsert_candidate":
                if not isinstance(payload, dict):
                    raise ValueError("upsert_candidate payload must be an object")
                candidate_id = payload.get("id")
                if (
                    not isinstance(candidate_id, str)
                    or SAFE_ID.fullmatch(candidate_id) is None
                ):
                    raise ValueError("upsert_candidate requires a valid candidate id")
                existing_index = next(
                    (
                        index
                        for index, candidate in enumerate(candidates)
                        if candidate.get("id") == candidate_id
                    ),
                    None,
                )
                if existing_index is None:
                    if len(candidates) >= request["max_candidates"]:
                        raise ValueError("candidate budget has been reached")
                    candidates.append(deepcopy(payload))
                else:
                    candidates[existing_index] = deepcopy(payload)

            elif operation == "patch_candidate":
                if not isinstance(payload, dict):
                    raise ValueError("patch_candidate payload must be an object")
                candidate_id = payload.get("candidate_id")
                changes = payload.get("changes")
                if not isinstance(candidate_id, str) or not isinstance(changes, dict):
                    raise ValueError(
                        "patch_candidate requires candidate_id and object changes"
                    )
                candidate = next(
                    (
                        candidate
                        for candidate in candidates
                        if candidate.get("id") == candidate_id
                    ),
                    None,
                )
                if candidate is None:
                    raise ValueError(f"candidate does not exist: {candidate_id}")
                changed_id = changes.get("id")
                if changed_id is not None and changed_id != candidate_id:
                    raise ValueError("patch_candidate cannot change the candidate id")
                _merge_draft_changes(candidate, changes)

            elif operation == "remove_candidate":
                if not isinstance(payload, dict):
                    raise ValueError("remove_candidate payload must be an object")
                candidate_id = payload.get("candidate_id")
                if not isinstance(candidate_id, str):
                    raise ValueError("remove_candidate requires candidate_id")
                remaining = [
                    candidate
                    for candidate in candidates
                    if candidate.get("id") != candidate_id
                ]
                if len(remaining) == len(candidates):
                    raise ValueError(f"candidate does not exist: {candidate_id}")
                draft["candidates"] = remaining
                draft["pairwise_distinctions"] = [
                    row
                    for row in draft["pairwise_distinctions"]
                    if not isinstance(row, dict)
                    or (
                        row.get("left_id") != candidate_id
                        and row.get("right_id") != candidate_id
                    )
                ]

            elif operation == "set_distinctions":
                if not isinstance(payload, list) or not all(
                    isinstance(row, dict) for row in payload
                ):
                    raise ValueError(
                        "set_distinctions payload must be an array of objects"
                    )
                draft["pairwise_distinctions"] = deepcopy(payload)

            elif operation == "set_portfolio_notes":
                if payload is not None and not isinstance(payload, str):
                    raise ValueError(
                        "set_portfolio_notes payload must be a string or null"
                    )
                draft["portfolio_notes"] = payload

        state.latest_draft = draft
        state.latest_draft_sha256 = canonical_json_sha256(draft)
        # A material edit is the escape condition for a previous repeated
        # validation failure, so a genuinely revised draft gets a fresh review.
        state.last_validation_error = None
        state.same_validation_error_count = 0
        _persist_state(config, state)
        result = _draft_summary(state, request, config)
        result["operation"] = operation
        result["retry_budget_reset"] = True
        if not result["soft_warnings"]:
            result["return_gate"] = "get_draft_required"
            result["natural_language_return_allowed"] = False
            result["next_required_action"] = {
                "tool": "scientific_hypothesis_get_draft",
                "instruction": (
                    "Read the persisted draft once before rendering the final answer."
                ),
            }
        return _ok(result)
    except Exception as exc:
        return _needs_revision(exc, state=state)
    finally:
        state._persistence_lock.release()


@tool(parse_docstring=True)
def scientific_hypothesis_get_draft(config: RunnableConfig = None) -> str:
    """Return the current mutable draft and its non-blocking review warnings.

    Use this after an interruption or before applying a targeted patch. This
    operation never validates, checkpoints, publishes, or modifies the draft.

    Args:
        config: Injected LangGraph runtime configuration; callers omit it.

    Returns:
        JSON string containing the current draft and soft review summary.
    """
    state = _state(config)
    try:
        request = _require_active_request(state)
        result = _draft_summary(state, request, config)
        result["draft"] = deepcopy(state.latest_draft)
        result["tail_review_scoring_guide"] = tail_review_scoring_guide()
        return _ok(result)
    except Exception as exc:
        return _needs_revision(exc, state=state)


@tool(parse_docstring=True)
def scientific_hypothesis_review_tail(
    review_json: str, config: RunnableConfig = None
) -> str:
    """Independently review a candidate pool and apply deterministic tail selection.

    Use this only after persisting the complete candidate pool. The review must
    use ``scientific-hypothesis-tail-review-v2`` and the current
    ``candidate_pool_sha256``. It must cover every candidate with a controlled
    generation operator, one of ``modal_baseline``, ``positive_tail``,
    ``negative_tail``, or ``null_control`` as its search region, a mechanism
    signature, an unverified novelty status, seven general violation-first
    rubric items, at least one question-specific instance rubric per candidate,
    and six low/medium/high tail metrics. Instance rubrics must be derived from
    the bound question, bound evidence, or a concrete candidate contrast.

    Every common and instance-specific rubric item has ``status``,
    ``violated_guidelines``, and ``rationale``. The reviewer must list
    weaknesses and violated guideline codes first. Status is ``pass`` if and
    only if that list is empty; otherwise it is ``violation``. The detailed
    pass conditions, violation conditions, edge rules, and metric anchors are
    returned by both ``scientific_hypothesis_bind_request`` and
    ``scientific_hypothesis_get_draft``. Review metrics include mechanism_distance,
    prediction_disagreement, expected_information_gain, falsifiability,
    evidence_risk, and test_cost. Mechanism distance is a diversity marker,
    not a monotonic scientific benefit, so novelty alone cannot dominate a
    more adjacent candidate. Per-item rubric rewards are logged for later
    training, but a hard violation cannot be offset by their average or by any
    tail metric. If violations exist, the draft is preserved for repair.
    Otherwise code—not the reviewer—recomputes global and per-search-region
    Pareto frontiers, preserves eligible null controls, and prunes candidates
    dominated within their search region.

    Args:
        review_json: Complete independent tail-review JSON for the current pool.

    Returns:
        JSON string containing violations, the recomputed frontier, selected
        candidates, pruned candidates, and the updated draft hashes.
    """
    state = _state(config)
    try:
        request = _require_active_request(state)
        if not isinstance(state.latest_draft, dict):
            raise ValueError("No working draft exists to review.")
        nonblocking_review_warnings = {
            "candidate_evidence_unlinked",
            "candidate_not_distinguished",
            "cross_candidate_literature_citation",
            "literature_pass_missing",
            "unattached_literature_evidence",
            "unattached_wiki_evidence",
        }
        blocking_warnings = [
            warning
            for warning in _draft_warnings(state, request)
            if warning["code"] not in nonblocking_review_warnings
        ]
        if blocking_warnings:
            codes = sorted({warning["code"] for warning in blocking_warnings})
            raise ValueError(
                "Resolve deterministic draft warnings before independent tail "
                f"review: {', '.join(codes)}"
            )
        payload = json.loads(review_json)
        review = validate_and_select_tail_review(
            payload,
            state.latest_draft,
            evidence_sha256=_evidence_sha256(state.evidence_register),
            require_two_sided_tail=bool(
                LONG_TAIL_QUERY.search(request["research_question"])
            ),
        )
        review["selected_candidate_pool_sha256"] = None
        state.tail_review = review

        rejected_ids = review["rejected_candidate_ids"]
        if rejected_ids:
            _persist_state(config, state)
            result = _draft_summary(state, request, config)
            result.update(
                {
                    "schema_version": TAIL_REVIEW_VERSION,
                    "status": "needs_revision",
                    "draft_changed": False,
                    "rejected_candidate_ids": rejected_ids,
                    "message": (
                        "At least one candidate has a hard rubric violation. "
                        "Repair or remove those candidates, then review the changed "
                        "candidate pool again; tail value cannot offset a violation."
                    ),
                    "tail_review": deepcopy(review),
                }
            )
            return _ok(result)

        selected_ids = review["selected_candidate_ids"]
        selected_set = set(selected_ids)
        draft = deepcopy(state.latest_draft)
        draft["candidates"] = [
            candidate
            for candidate in draft["candidates"]
            if candidate.get("id") in selected_set
        ]
        draft["pairwise_distinctions"] = [
            row
            for row in draft.get("pairwise_distinctions", [])
            if isinstance(row, dict)
            and row.get("left_id") in selected_set
            and row.get("right_id") in selected_set
        ]
        state.latest_draft = draft
        state.latest_draft_sha256 = canonical_json_sha256(draft)
        review["selected_candidate_pool_sha256"] = candidate_pool_sha256(draft)
        state.tail_review = review
        state.last_validation_error = None
        state.same_validation_error_count = 0
        _persist_state(config, state)
        result = _draft_summary(state, request, config)
        result.update(
            {
                "schema_version": TAIL_REVIEW_VERSION,
                "status": "tail_reviewed",
                "draft_changed": bool(review["dominated_candidate_ids"]),
                "pareto_frontier_ids": review["pareto_frontier_ids"],
                "regional_frontier_ids": review["regional_frontier_ids"],
                "sentinel_candidate_ids": review["sentinel_candidate_ids"],
                "selected_candidate_ids": selected_ids,
                "pruned_candidate_ids": review["dominated_candidate_ids"],
                "search_regions": review["search_regions"],
                "tail_review": deepcopy(review),
            }
        )
        return _ok(result)
    except Exception as exc:
        return _needs_revision(exc, state=state)


def _checkpoint_response(
    state: _HypothesisState,
    response: dict[str, Any],
    config: RunnableConfig | None,
    *,
    require_tail_review: bool = False,
) -> str:
    state._persistence_lock.acquire()
    try:
        request = _require_active_request(state)
        state.latest_draft = response
        state.latest_draft_sha256 = canonical_json_sha256(response)
        candidates = response.get("candidates")
        tail_review_was_current = tail_review_is_current(
            state.tail_review,
            response,
            evidence_sha256=_evidence_sha256(state.evidence_register),
        )
        tail_review_required = bool(
            require_tail_review
            and isinstance(candidates, list)
            and (
                len(candidates) > 1
                or bool(
                    candidates and LONG_TAIL_QUERY.search(request["research_question"])
                )
            )
        )
        if tail_review_required and not tail_review_was_current:
            raise ValueError(
                "A current independent tail review is required before checkpointing "
                "a multi-candidate or explicit long-tail draft. Call "
                "scientific_hypothesis_review_tail "
                "with the current candidate_pool_sha256; candidate or evidence "
                "changes make an earlier review stale."
            )
        state.preflight_attempts += 1
        result = preflight_hypothesis_response(
            request,
            response,
            state.evidence_register,
            include_validated_response=True,
        )
        checked = result.pop("_validated_response", None)
        result["preflight_attempt"] = state.preflight_attempts
        checkpoint_created = False
        if result.get("status") == "hypotheses_ready" and isinstance(checked, dict):
            state.validated_response = checked
            state.preflight_response_sha256 = canonical_json_sha256(checked)
            state.checkpoint_evidence_sha256 = _evidence_sha256(state.evidence_register)
            state.latest_draft = checked
            state.latest_draft_sha256 = state.preflight_response_sha256
            if (
                require_tail_review
                and tail_review_was_current
                and isinstance(state.tail_review, dict)
            ):
                state.tail_review["selected_candidate_pool_sha256"] = (
                    candidate_pool_sha256(checked)
                )
            checkpoint_created = True
        state.last_validation_error = None
        state.same_validation_error_count = 0
        result["working_status"] = (
            "checkpointed" if checkpoint_created else result["status"]
        )
        result["checkpoint_available"] = state.validated_response is not None
        result["publication_required"] = False
        state_path = _persist_state(config, state)
        result["state_persistence"] = (
            "workspace" if state_path is not None else "memory_only"
        )
        result["persistence_warning"] = state.persistence_warning
        return _ok(result)
    except Exception as exc:
        outcome = _needs_revision(exc, state=state, count_failure=True)
        _persist_state(config, state)
        return outcome
    finally:
        state._persistence_lock.release()


@tool(parse_docstring=True)
def scientific_hypothesis_validate_response(
    response_json: str, config: RunnableConfig = None
) -> str:
    """Replace the current draft and checkpoint one complete response.

    This compatibility path accepts a complete scientific-hypothesis response
    in one call. New multi-step work should use incremental draft updates and
    ``scientific_hypothesis_checkpoint_draft`` instead. A failed check remains
    a draft and does not erase the last valid checkpoint.

    Args:
        response_json: One JSON string containing a
            scientific-hypothesis-response-v1 object.

    Returns:
        JSON string with the checkpoint status and issue list.
    """
    state = _state(config)
    try:
        response = json.loads(response_json)
        if not isinstance(response, dict):
            raise ValueError("response must be a JSON object")
    except Exception as exc:
        outcome = _needs_revision(exc, state=state, count_failure=True)
        _persist_state(config, state)
        return outcome
    return _checkpoint_response(state, response, config)


@tool(parse_docstring=True)
def scientific_hypothesis_checkpoint_draft(config: RunnableConfig = None) -> str:
    """Hard-check the current mutable draft without requiring it to be resent.

    Use only when a structured handoff or formal publication is needed.
    Checkpoint failure preserves both the draft and the last valid checkpoint.

    Args:
        config: Injected LangGraph runtime configuration; callers omit it.

    Returns:
        JSON string with the checkpoint status and issue list.
    """
    state = _state(config)
    if not isinstance(state.latest_draft, dict):
        outcome = _needs_revision(
            ValueError("No working draft exists to checkpoint."),
            state=state,
            count_failure=True,
        )
        _persist_state(config, state)
        return outcome
    return _checkpoint_response(
        state,
        deepcopy(state.latest_draft),
        config,
        require_tail_review=True,
    )


@tool(parse_docstring=True)
def scientific_hypothesis_get_status(config: RunnableConfig = None) -> str:
    """Return the current draft/checkpoint status without modifying it.

    Use this after an interruption or validation failure to decide whether to
    continue editing, report a partial result, or explicitly publish a valid
    checkpoint.

    Args:
        config: Injected LangGraph runtime configuration; callers omit it.

    Returns:
        JSON string describing the current task-scoped working state.
    """
    state = _state(config)
    try:
        request = _require_active_request(state)
        draft_differs = bool(
            state.latest_draft_sha256
            and state.preflight_response_sha256
            and state.latest_draft_sha256 != state.preflight_response_sha256
        )
        evidence_differs = bool(
            state.checkpoint_evidence_sha256
            and state.checkpoint_evidence_sha256
            != _evidence_sha256(state.evidence_register)
        )
        draft_summary = _draft_summary(state, request, config)
        return _ok(
            {
                "schema_version": "scientific-hypothesis-working-status-v1",
                "status": "working",
                "research_question": request["research_question"],
                "request_sha256": state.request_sha256,
                "bound_evidence_count": len(state.evidence_register),
                "draft_available": state.latest_draft is not None,
                "candidate_count": draft_summary["candidate_count"],
                "candidate_pool_sha256": draft_summary["candidate_pool_sha256"],
                "soft_warning_count": draft_summary["soft_warning_count"],
                "soft_warnings": draft_summary["soft_warnings"],
                "tail_review_status": draft_summary["tail_review_status"],
                "tail_review_required": draft_summary["tail_review_required"],
                "tail_review_frontier_ids": draft_summary["tail_review_frontier_ids"],
                "tail_review_selected_ids": draft_summary["tail_review_selected_ids"],
                "checkpoint_available": state.validated_response is not None,
                "draft_differs_from_checkpoint": draft_differs,
                "evidence_differs_from_checkpoint": evidence_differs,
                "checkpoint_attempts": state.preflight_attempts,
                "same_validation_error_count": state.same_validation_error_count,
                "retry_recommended": (
                    state.same_validation_error_count < MAX_SAME_CHECKPOINT_FAILURES
                ),
                "state_persistence": (
                    "workspace"
                    if _working_state_path(config) is not None
                    else "memory_only"
                ),
                "persistence_warning": state.persistence_warning,
            }
        )
    except Exception as exc:
        return _needs_revision(exc, state=state)


@tool(parse_docstring=True)
def scientific_hypothesis_freeze(config: RunnableConfig = None) -> str:
    """Explicitly publish the most recently checkpointed hypotheses.

    This is a publication operation, not a required conversational step. It
    takes no parameters: the latest valid checkpoint is compiled into a
    portfolio.

    Args:
        config: Injected LangGraph runtime configuration; callers omit it.

    Returns:
        JSON string with the freeze outcome, run id, file paths, and the
        user-display Markdown.
    """
    state = _state(config)
    state._persistence_lock.acquire()
    try:
        request = _require_active_request(state)
        if state.validated_response is None or state.preflight_response_sha256 is None:
            raise RuntimeError(
                "Publish requires a successful scientific_hypothesis_checkpoint_draft "
                "or compatibility validation checkpoint first."
            )
        if (
            state.latest_draft_sha256 is not None
            and state.latest_draft_sha256 != state.preflight_response_sha256
        ):
            raise RuntimeError(
                "The current draft differs from the last valid checkpoint. "
                "Checkpoint the intended draft before publishing; the older "
                "checkpoint was preserved and has not been overwritten."
            )
        if (
            state.checkpoint_evidence_sha256 is not None
            and state.checkpoint_evidence_sha256
            != _evidence_sha256(state.evidence_register)
        ):
            raise RuntimeError(
                "The evidence register changed after the last valid checkpoint. "
                "Checkpoint the intended draft against the current evidence before "
                "publishing."
            )
        if (
            canonical_json_sha256(state.validated_response)
            != state.preflight_response_sha256
        ):
            raise RuntimeError(
                "Hypothesis response changed after validation; check the revised response first."
            )
        warnings = _draft_warnings(state, request)
        if warnings:
            raise _unresolved_warning_error(warnings, action="publish draft")
        workspace_root = workspace_root_from_config(config)
        outcome = freeze_hypothesis_portfolio(
            request,
            state.validated_response,
            state.evidence_register,
            runs_root=workspace_root / "hypothesis" / "runs",
            path_root=workspace_root,
        )
        outcome["bound_request_sha256"] = state.request_sha256
        outcome["response_submissions"] = state.preflight_attempts
        outcome["contract_repairs"] = max(0, state.preflight_attempts - 1)
        outcome["publication_status"] = "published"
        return _ok(outcome)
    except Exception as exc:
        return _needs_revision(exc, state=state)
    finally:
        state._persistence_lock.release()


SCIENTIFIC_HYPOTHESIS_TOOLS = [
    scientific_hypothesis_bind_request,
    scientific_hypothesis_bind_evidence,
    scientific_hypothesis_bind_wiki_evidence,
    scientific_hypothesis_build_literature_bundle,
    scientific_hypothesis_bind_literature_evidence,
    scientific_hypothesis_update_draft,
    scientific_hypothesis_get_draft,
    scientific_hypothesis_review_tail,
    scientific_hypothesis_validate_response,
    scientific_hypothesis_checkpoint_draft,
    scientific_hypothesis_get_status,
    scientific_hypothesis_freeze,
]

register_tool_bundle("scientific-hypothesis", SCIENTIFIC_HYPOTHESIS_TOOLS)

__all__ = ["SCIENTIFIC_HYPOTHESIS_TOOLS", "read_persisted_hypothesis_draft"] + [
    t.name for t in SCIENTIFIC_HYPOTHESIS_TOOLS
]
