"""Deterministic long-tail review for scientific-hypothesis candidate pools.

The language model proposes mechanism-diverse candidates and grades them with
an independent, violation-first rubric.  This module does not trust a
model-authored winner or aggregate score: it validates the review, rejects
hard scientific violations, recomputes the Pareto frontier, and preserves
eligible null/control sentinels.

The design adapts the paper-specific, violation-based rubric rewards in
arXiv:2512.23707 to inference-time hypothesis search by combining general
scientific gates with instance rubrics derived from the bound question,
evidence, or candidate contrasts. Novelty is deliberately not treated as a
verified fact, and no high tail score can compensate for missing boundaries,
falsifiability, or evidence discipline.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from .contracts import SAFE_ID, ContractError, canonical_json_sha256

TAIL_REVIEW_VERSION = "scientific-hypothesis-tail-review-v2"
TAIL_REVIEWER_MODE = "independent_violation_first"

GENERATION_OPERATORS = {
    "modal_baseline",
    "residual_anomaly",
    "causal_edge_change",
    "regime_boundary",
    "causal_reversal",
    "latent_driver",
    "symmetry_break",
    "measurement_null",
    "nonlinear_interaction",
    "premise_reversal",
}
SEARCH_REGIONS = {
    "modal_baseline",
    "positive_tail",
    "negative_tail",
    "null_control",
}
NOVELTY_STATUSES = {
    "known_baseline",
    "adjacent_possibility",
    "tail_candidate_unverified",
}
SENTINEL_REGIONS = {"null_control"}

RUBRIC_ITEMS = (
    "boundary_completeness",
    "prediction_operationalization",
    "falsifiability",
    "evidence_discipline",
    "alternative_explanations",
    "test_feasibility",
    "internal_consistency",
)
GENERAL_GUIDELINES = (
    "handles_all_criteria",
    "detailed_and_specific",
    "no_overlooked_flaws",
    "well_justified",
    "cost_and_effort_efficient",
    "ethical_and_safe",
    "consistent_with_portfolio",
)
GENERAL_GUIDELINE_DEFINITIONS = {
    "handles_all_criteria": {
        "question": "Does the assessed text satisfy every part of this rubric item?",
        "violation_when": (
            "A required clause is absent, replaced by a different requirement, or "
            "asserted without being carried through to the candidate fields.",
        ),
    },
    "detailed_and_specific": {
        "question": "Does it state what will be checked, how, and under what rule?",
        "violation_when": (
            "It relies on vague terms, unspecified observables, hidden choices, or "
            "claims that something is handled without saying how.",
        ),
    },
    "no_overlooked_flaws": {
        "question": "Is there no obvious flaw that defeats this rubric item?",
        "violation_when": (
            "A measurement artifact, confounder, leakage path, scope mismatch, "
            "unavailable datum, or circular test could invalidate the claimed pass.",
        ),
    },
    "well_justified": {
        "question": "Is the choice justified against simpler or rival explanations?",
        "violation_when": (
            "The rationale merely restates the hypothesis, appeals to novelty, or "
            "does not explain why the proposed contrast is scientifically useful.",
        ),
    },
    "cost_and_effort_efficient": {
        "question": "Is this the least costly test that preserves the needed contrast?",
        "violation_when": (
            "An equally discriminating existing-data or simpler analysis is ignored, "
            "or the proposed effort is disproportionate to the information gained.",
        ),
    },
    "ethical_and_safe": {
        "question": "Does the relevant step avoid material ethical or safety problems?",
        "violation_when": (
            "It requires unsafe, prohibited, deceptive, privacy-invasive, or otherwise "
            "unjustified research conduct without safeguards.",
        ),
    },
    "consistent_with_portfolio": {
        "question": "Is it consistent with the candidate and the rest of the portfolio?",
        "violation_when": (
            "Its mechanism, scope, evidence role, prediction, falsifier, confidence, "
            "or proposed test contradicts another field that is supposed to describe "
            "the same candidate.",
        ),
    },
}
RUBRIC_DEFINITIONS = {
    "boundary_completeness": {
        "criterion": (
            "The claim is bounded by target system, time, space, data, method, "
            "holds-when conditions, non-applicability conditions, and "
            "generalization limits."
        ),
        "pass_when": (
            "Every boundary dimension is explicit or explicitly inapplicable.",
            "Observed scope is not silently generalized to an unobserved regime.",
            "Premises, confounders, non-applicability, and falsifiers remain distinct.",
        ),
        "violation_when": (
            "Any boundary dimension is missing or hidden only in prose.",
            "The claim uses universal scope without matched evidence.",
            "A premise, confounder, or falsifier is substituted for a scope condition.",
        ),
        "edge_rule": (
            "An unknown boundary may pass only when the unknown is named, the "
            "conclusion is correspondingly limited, and a way to resolve it is given."
        ),
    },
    "prediction_operationalization": {
        "criterion": (
            "At least one observable prediction differs from the mechanism statement "
            "and specifies the variable, comparison, direction or pattern, analysis "
            "window, and decision rule."
        ),
        "pass_when": (
            "A reader can determine what observation would count for or against it.",
            "Any threshold is sourced or explicitly delegated to a preregistered rule.",
            "Measurement and sampling uncertainty are included in the decision rule.",
        ),
        "violation_when": (
            "The prediction only repeats the mechanism.",
            "Words such as significant, obvious, stable, or anomalous replace a rule.",
            "A numerical cutoff or time window is invented without bound support.",
        ),
        "edge_rule": (
            "A qualitative prediction may pass when it gives an unambiguous directional "
            "or conditional contrast and states how uncertainty will be handled."
        ),
    },
    "falsifiability": {
        "criterion": (
            "The candidate names an attainable result that would weaken or abandon it "
            "without redefining the hypothesis after seeing the data."
        ),
        "pass_when": (
            "The weakening result is observable with the proposed data and method.",
            "It is logically compatible with a rival or null candidate.",
            "The candidate cannot absorb every possible outcome.",
        ),
        "violation_when": (
            "Only complete technical failure could count against the candidate.",
            "The falsifier is vague, unreachable, circular, or identical to the claim.",
            "Every outcome is explained post hoc by changing an unspecified parameter.",
        ),
        "edge_rule": (
            "A noisy null result need not falsify the candidate, but the rule must say "
            "how repeated or interval-bounded null evidence would lower confidence."
        ),
    },
    "evidence_discipline": {
        "criterion": (
            "Support, opposition, limitations, and gaps are separated and every "
            "empirical claim is traceable to evidence bound in the current task."
        ),
        "pass_when": (
            "Scenario assumptions and model knowledge are labelled as premises.",
            "Evidence roles match what the quoted or measured source actually supports.",
            "Confidence is capped by evidence quality, coverage, and transfer limits.",
        ),
        "violation_when": (
            "A citation, result, threshold, or observation is fabricated or unbound.",
            "Metadata, logs, source discovery, or absence of refutation is called support.",
            "A mechanism constraint is promoted to verified empirical confirmation.",
        ),
        "edge_rule": (
            "No evidence can still yield a pass when empirical support is explicitly "
            "none, the candidate remains exploratory, and the gap is carried forward."
        ),
    },
    "alternative_explanations": {
        "criterion": (
            "The candidate includes a mechanistically distinct rival or measurement "
            "null, the main confounders, and an observable that separates them."
        ),
        "pass_when": (
            "At least one rival can generate the same apparent observation differently.",
            "Confounders describe concrete alternative signal sources.",
            "The next test yields different expectations for the competing candidates.",
        ),
        "violation_when": (
            "The alternative is only a synonym or parameter tweak.",
            "A plausible measurement, selection, or processing explanation is omitted.",
            "The proposed test would leave all named explanations equally compatible.",
        ),
        "edge_rule": (
            "A single-candidate task still requires one credible rival or null unless "
            "the bound question logically excludes all alternatives and explains why."
        ),
    },
    "test_feasibility": {
        "criterion": (
            "The next test names the data or observable, analysis method, comparison, "
            "resource assumptions, and interpretable outcome branches."
        ),
        "pass_when": (
            "Required data and coverage exist or their acquisition path is credible.",
            "The test is safe, affordable relative to its information gain, and scoped.",
            "At least two plausible outcomes update candidates differently.",
        ),
        "violation_when": (
            "It says only collect more data, validate, or run an experiment.",
            "It relies on an unavailable instrument or sample with no fallback.",
            "It is expensive but cannot distinguish the candidate from its main rival.",
        ),
        "edge_rule": (
            "A high-cost test may pass only when no cheaper test preserves the key "
            "contrast and the dependency and stopping conditions are explicit."
        ),
    },
    "internal_consistency": {
        "criterion": (
            "Statement, mechanism, premises, scope, prediction, evidence, falsifier, "
            "uncertainty, confidence, and next test form one non-contradictory account."
        ),
        "pass_when": (
            "Predicted directions follow from the stated mechanism and premises.",
            "The falsifier actually tests the stated claim within its scope.",
            "Confidence and wording agree with the evidence and uncertainty record.",
        ),
        "violation_when": (
            "Two fields predict incompatible outcomes under the same conditions.",
            "The next test targets a different mechanism from the candidate.",
            "Confidence is high while support is absent or a hard conflict is unresolved.",
        ),
        "edge_rule": (
            "Competing candidates may contradict each other; that is desirable. A "
            "violation exists only when one candidate contradicts itself or its record."
        ),
    },
}
BENEFIT_METRICS = (
    "mechanism_distance",
    "prediction_disagreement",
    "expected_information_gain",
    "falsifiability",
)
SELECTION_BENEFIT_METRICS = (
    "prediction_disagreement",
    "expected_information_gain",
    "falsifiability",
)
COST_METRICS = ("evidence_risk", "test_cost")
LEVELS = {"low": 1, "medium": 2, "high": 3}
TAIL_METRIC_ANCHORS = {
    "mechanism_distance": {
        "direction": "diversity_only",
        "low": (
            "Same accepted mechanism as the modal baseline with a parameter, scale, "
            "or wording change."
        ),
        "medium": (
            "A different causal link or interaction within a related framework, with "
            "at least one distinct premise and prediction."
        ),
        "high": (
            "A premise reversal, latent driver, regime change, symmetry break, or "
            "measurement account that is mechanistically remote from the baseline."
        ),
    },
    "prediction_disagreement": {
        "direction": "benefit",
        "low": "Candidates predict the same observable in the same direction and regime.",
        "medium": (
            "Candidates differ in magnitude, timing, subgroup, or conditional response."
        ),
        "high": (
            "Candidates predict opposite directions, presence versus absence, or "
            "mutually exclusive conditional patterns under one feasible comparison."
        ),
    },
    "expected_information_gain": {
        "direction": "benefit",
        "low": "Most plausible outcomes leave the portfolio essentially unchanged.",
        "medium": "A feasible result removes one candidate or narrows one mechanism.",
        "high": (
            "Plausible outcome branches partition several candidates, including a "
            "baseline or null, and materially change the next decision."
        ),
    },
    "falsifiability": {
        "direction": "benefit",
        "low": "The weakening outcome is vague, unreachable, circular, or post hoc.",
        "medium": (
            "An observable weakening outcome exists but the decision rule or rival "
            "interpretation remains partly ambiguous."
        ),
        "high": (
            "A feasible observation with an explicit uncertainty-aware rule would "
            "clearly lower confidence or reject the candidate."
        ),
    },
    "evidence_risk": {
        "direction": "risk",
        "low": "Direct, verified, scope-matched evidence and observables are available.",
        "medium": "Evidence is indirect, proxy-based, limited in coverage, or transferable.",
        "high": (
            "A key premise is unverified, the observable is unavailable, or the claim "
            "requires severe extrapolation. High means worse."
        ),
    },
    "test_cost": {
        "direction": "cost",
        "low": "Existing data and a simple, bounded reanalysis are sufficient.",
        "medium": "New preprocessing, modelling, or modest coordination is required.",
        "high": (
            "A new instrument, long observation, rare event, major coordination, or "
            "large compute budget is required. High means worse."
        ),
    },
}
_PROCESS_ONLY_INSTANCE_BASIS = re.compile(
    r"(?:绑定|当前|用户)(?:问题|请求).{0,24}(?:要求|必须)"
    r"|(?:bound|current|user) (?:question|request).{0,32}"
    r"(?:requires?|must)"
    r"|输出要求|格式要求|字段要求|search[-_ ]region requirement",
    re.IGNORECASE,
)


def tail_review_scoring_guide() -> dict[str, Any]:
    """Return the canonical grader-only rubric and metric anchors."""

    return deepcopy(
        {
            "protocol": {
                "sequence": (
                    "write weaknesses and violated guideline codes before assigning "
                    "status"
                ),
                "item_status_rule": (
                    "pass if and only if violated_guidelines is empty; otherwise "
                    "violation"
                ),
                "candidate_gate": (
                    "every common and instance-specific rubric item must pass"
                ),
                "aggregation_rule": (
                    "the pass fraction is an internal trace only; it cannot compensate "
                    "for any violation and is never shown to the reader"
                ),
                "metric_rule": (
                    "tail metrics are anchored ordinal coordinates, not a total score; "
                    "code performs Pareto selection"
                ),
            },
            "general_guidelines": GENERAL_GUIDELINE_DEFINITIONS,
            "scientific_rubrics": RUBRIC_DEFINITIONS,
            "tail_metric_anchors": TAIL_METRIC_ANCHORS,
        }
    )


def candidate_pool_sha256(draft_or_candidates: object) -> str:
    """Hash only candidate bodies so notes/distinction edits do not stale review."""

    candidates = (
        draft_or_candidates.get("candidates")
        if isinstance(draft_or_candidates, dict)
        else draft_or_candidates
    )
    if not isinstance(candidates, list):
        raise ContractError("candidate pool 必须是数组")
    return canonical_json_sha256({"candidates": candidates})


def _exact_fields(value: dict[str, Any], expected: set[str], label: str) -> None:
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing or unknown:
        details = []
        if missing:
            details.append("缺少 " + ", ".join(missing))
        if unknown:
            details.append("包含未知字段 " + ", ".join(unknown))
        raise ContractError(f"{label} 字段不闭合：" + "; ".join(details))


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{label} 必须是 JSON 对象")
    return value


def _text(
    value: object,
    label: str,
    *,
    min_length: int = 1,
    max_length: int = 2_000,
) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{label} 必须是字符串")
    normalized = " ".join(value.replace("\r", " ").replace("\n", " ").split())
    if not min_length <= len(normalized) <= max_length:
        raise ContractError(
            f"{label} 长度必须在 {min_length} 到 {max_length} 个字符之间"
        )
    return normalized


def _enum(value: object, allowed: set[str], label: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ContractError(f"{label} 必须是：{', '.join(sorted(allowed))}")
    return value


def _violation_codes(value: object, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ContractError(f"{label} 必须是数组")
    if len(value) > len(GENERAL_GUIDELINES):
        raise ContractError(f"{label} 数量超过通用评分准则数量")
    codes: list[str] = []
    for index, raw_code in enumerate(value):
        code = _enum(
            raw_code,
            set(GENERAL_GUIDELINES),
            f"{label}[{index}]",
        )
        if code in codes:
            raise ContractError(f"{label} 包含重复项：{code}")
        codes.append(code)
    return codes


def _rubric_status(
    status_value: object,
    violations_value: object,
    label: str,
) -> tuple[str, list[str]]:
    status = _enum(status_value, {"pass", "violation"}, f"{label}.status")
    violations = _violation_codes(
        violations_value,
        f"{label}.violated_guidelines",
    )
    expected = "pass" if not violations else "violation"
    if status != expected:
        raise ContractError(
            f"{label}.status 与 violated_guidelines 不一致："
            "只有零违规才能 pass，有任一违规必须是 violation"
        )
    return status, violations


def _dominates(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Return whether left Pareto-dominates right under explicit directions."""

    left_metrics = left["tail_metrics"]
    right_metrics = right["tail_metrics"]
    # Mechanism distance is a diversity coordinate, not scientific utility.
    # Treating "farther from the modal answer" as monotonically better lets an
    # exotic but equally risky candidate dominate a more testable adjacent
    # mechanism solely because it is stranger.
    no_worse = all(
        LEVELS[left_metrics[key]] >= LEVELS[right_metrics[key]]
        for key in SELECTION_BENEFIT_METRICS
    ) and all(
        LEVELS[left_metrics[key]] <= LEVELS[right_metrics[key]] for key in COST_METRICS
    )
    strictly_better = any(
        LEVELS[left_metrics[key]] > LEVELS[right_metrics[key]]
        for key in SELECTION_BENEFIT_METRICS
    ) or any(
        LEVELS[left_metrics[key]] < LEVELS[right_metrics[key]] for key in COST_METRICS
    )
    return no_worse and strictly_better


def _pareto_frontier(rows: list[dict[str, Any]]) -> list[str]:
    return [
        row["candidate_id"]
        for row in rows
        if not any(
            other["candidate_id"] != row["candidate_id"] and _dominates(other, row)
            for other in rows
        )
    ]


def _review_rows(payload: object, candidate_ids: list[str]) -> list[dict[str, Any]]:
    review = _object(payload, "tail review")
    _exact_fields(
        review,
        {
            "schema_version",
            "candidate_pool_sha256",
            "reviewer_mode",
            "instance_rubrics",
            "candidates",
        },
        "tail review",
    )
    if review["schema_version"] != TAIL_REVIEW_VERSION:
        raise ContractError(f"tail review.schema_version 必须是 {TAIL_REVIEW_VERSION}")
    _text(review["candidate_pool_sha256"], "tail review.candidate_pool_sha256")
    if review["reviewer_mode"] != TAIL_REVIEWER_MODE:
        raise ContractError(f"tail review.reviewer_mode 必须是 {TAIL_REVIEWER_MODE}")
    raw_rows = review["candidates"]
    if not isinstance(raw_rows, list):
        raise ContractError("tail review.candidates 必须是数组")
    if len(raw_rows) != len(candidate_ids):
        raise ContractError("tail review 必须逐一覆盖当前候选池中的全部候选")

    expected_ids = set(candidate_ids)
    seen_ids: set[str] = set()
    seen_signatures: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_rows):
        label = f"tail review.candidates[{index}]"
        row = _object(raw, label)
        _exact_fields(
            row,
            {
                "candidate_id",
                "generation_operator",
                "search_region",
                "mechanism_signature",
                "novelty_status",
                "rubric",
                "tail_metrics",
                "reviewer_summary",
            },
            label,
        )
        candidate_id = _text(
            row["candidate_id"], f"{label}.candidate_id", max_length=64
        )
        if SAFE_ID.fullmatch(candidate_id) is None:
            raise ContractError(f"{label}.candidate_id 格式无效")
        if candidate_id not in expected_ids:
            raise ContractError(
                f"{label}.candidate_id 不属于当前候选池：{candidate_id}"
            )
        if candidate_id in seen_ids:
            raise ContractError(f"{label}.candidate_id 重复：{candidate_id}")
        seen_ids.add(candidate_id)

        signature = _text(
            row["mechanism_signature"],
            f"{label}.mechanism_signature",
            min_length=8,
            max_length=240,
        )
        normalized_signature = "".join(signature.casefold().split())
        other = seen_signatures.get(normalized_signature)
        if other is not None:
            raise ContractError(
                f"候选 {candidate_id} 与 {other} 的 mechanism_signature 重复。"
                "同义改写不能作为长尾候选"
            )
        seen_signatures[normalized_signature] = candidate_id

        rubric_raw = _object(row["rubric"], f"{label}.rubric")
        _exact_fields(rubric_raw, set(RUBRIC_ITEMS), f"{label}.rubric")
        rubric: dict[str, dict[str, Any]] = {}
        for key in RUBRIC_ITEMS:
            item_label = f"{label}.rubric.{key}"
            item = _object(rubric_raw[key], item_label)
            _exact_fields(
                item,
                {"status", "violated_guidelines", "rationale"},
                item_label,
            )
            status, violated_guidelines = _rubric_status(
                item["status"],
                item["violated_guidelines"],
                item_label,
            )
            rubric[key] = {
                "status": status,
                "violated_guidelines": violated_guidelines,
                "rationale": _text(
                    item["rationale"],
                    f"{item_label}.rationale",
                    min_length=8,
                    max_length=1_000,
                ),
            }

        metrics_raw = _object(row["tail_metrics"], f"{label}.tail_metrics")
        _exact_fields(
            metrics_raw,
            set(BENEFIT_METRICS + COST_METRICS),
            f"{label}.tail_metrics",
        )
        metrics = {
            key: _enum(metrics_raw[key], set(LEVELS), f"{label}.tail_metrics.{key}")
            for key in BENEFIT_METRICS + COST_METRICS
        }
        item_rewards = {
            key: int(rubric[key]["status"] == "pass") for key in RUBRIC_ITEMS
        }
        generation_operator = _enum(
            row["generation_operator"],
            GENERATION_OPERATORS,
            f"{label}.generation_operator",
        )
        search_region = _enum(
            row["search_region"], SEARCH_REGIONS, f"{label}.search_region"
        )
        if (generation_operator == "modal_baseline") != (
            search_region == "modal_baseline"
        ):
            raise ContractError(
                f"{label} 的 modal_baseline operator 与 search_region 必须一致"
            )
        if (generation_operator == "measurement_null") != (
            search_region == "null_control"
        ):
            raise ContractError(
                f"{label} 的 measurement_null operator 与 null_control region 必须一致"
            )

        rows.append(
            {
                "candidate_id": candidate_id,
                "generation_operator": generation_operator,
                "search_region": search_region,
                "mechanism_signature": signature,
                "novelty_status": _enum(
                    row["novelty_status"],
                    NOVELTY_STATUSES,
                    f"{label}.novelty_status",
                ),
                "rubric": rubric,
                "rubric_item_rewards": item_rewards,
                "tail_metrics": metrics,
                "reviewer_summary": _text(
                    row["reviewer_summary"],
                    f"{label}.reviewer_summary",
                    min_length=12,
                    max_length=2_000,
                ),
            }
        )
    if seen_ids != expected_ids:
        missing = sorted(expected_ids - seen_ids)
        raise ContractError("tail review 缺少候选：" + ", ".join(missing))

    instance_raw = review["instance_rubrics"]
    if not isinstance(instance_raw, list):
        raise ContractError("tail review.instance_rubrics 必须是数组")
    if not 1 <= len(instance_raw) <= 32:
        raise ContractError("tail review.instance_rubrics 数量必须覆盖候选且保持有界")
    instance_by_candidate: dict[str, list[dict[str, Any]]] = {
        candidate_id: [] for candidate_id in candidate_ids
    }
    seen_instance_ids: set[str] = set()
    for index, raw in enumerate(instance_raw):
        label = f"tail review.instance_rubrics[{index}]"
        item = _object(raw, label)
        _exact_fields(
            item,
            {
                "id",
                "candidate_id",
                "criterion",
                "basis",
                "status",
                "violated_guidelines",
                "rationale",
            },
            label,
        )
        rubric_id = _text(item["id"], f"{label}.id", max_length=64)
        if SAFE_ID.fullmatch(rubric_id) is None:
            raise ContractError(f"{label}.id 格式无效")
        if rubric_id in seen_instance_ids:
            raise ContractError(f"{label}.id 重复：{rubric_id}")
        seen_instance_ids.add(rubric_id)
        candidate_id = _text(
            item["candidate_id"], f"{label}.candidate_id", max_length=64
        )
        if candidate_id not in expected_ids:
            raise ContractError(
                f"{label}.candidate_id 不属于当前候选池：{candidate_id}"
            )
        basis = _text(
            item["basis"],
            f"{label}.basis",
            min_length=12,
            max_length=1_000,
        )
        if _PROCESS_ONLY_INSTANCE_BASIS.search(basis):
            raise ContractError(
                f"{label}.basis 只复述了任务或格式要求。实例级 rubric 必须来自"
                "具体科学前提、证据限制、可观测量或候选间冲突"
            )
        status, violated_guidelines = _rubric_status(
            item["status"],
            item["violated_guidelines"],
            label,
        )
        instance_by_candidate[candidate_id].append(
            {
                "id": rubric_id,
                "criterion": _text(
                    item["criterion"],
                    f"{label}.criterion",
                    min_length=12,
                    max_length=1_000,
                ),
                "basis": basis,
                "status": status,
                "violated_guidelines": violated_guidelines,
                "rationale": _text(
                    item["rationale"],
                    f"{label}.rationale",
                    min_length=8,
                    max_length=1_000,
                ),
            }
        )

    for row in rows:
        candidate_id = row["candidate_id"]
        instance_items = instance_by_candidate[candidate_id]
        if not instance_items:
            raise ContractError(
                f"候选 {candidate_id} 缺少从当前问题、证据或候选冲突派生的实例级 rubric"
            )
        instance_rewards = {
            item["id"]: int(item["status"] == "pass") for item in instance_items
        }
        all_rewards = [
            *row["rubric_item_rewards"].values(),
            *instance_rewards.values(),
        ]
        row["instance_rubrics"] = instance_items
        row["instance_rubric_rewards"] = instance_rewards
        row["rubric_reward"] = sum(all_rewards) / len(all_rewards)
        row["hard_gate_passed"] = all(all_rewards)
    return rows


def validate_and_select_tail_review(
    payload: object,
    draft: dict[str, Any],
    *,
    evidence_sha256: str,
    require_two_sided_tail: bool = False,
) -> dict[str, Any]:
    """Validate one independent review and deterministically select candidates."""

    candidates = draft.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ContractError("必须先形成非空候选池，才能执行长尾审查")
    candidate_ids = [
        candidate.get("id") if isinstance(candidate, dict) else None
        for candidate in candidates
    ]
    if not all(
        isinstance(candidate_id, str) and SAFE_ID.fullmatch(candidate_id)
        for candidate_id in candidate_ids
    ):
        raise ContractError("当前候选池包含无效 candidate id")
    typed_ids = [str(candidate_id) for candidate_id in candidate_ids]
    source_pool_sha = candidate_pool_sha256(candidates)
    review_object = _object(payload, "tail review")
    if review_object.get("candidate_pool_sha256") != source_pool_sha:
        raise ContractError(
            "tail review 的 candidate_pool_sha256 与当前候选池不一致。"
            "候选修改后必须重新审查"
        )
    rows = _review_rows(review_object, typed_ids)

    regions = {row["search_region"] for row in rows}
    if len(rows) > 1 and regions <= {"modal_baseline"}:
        raise ContractError("多候选搜索不能全部停留在 modal_baseline")
    if (
        require_two_sided_tail
        and not {
            "modal_baseline",
            "positive_tail",
            "negative_tail",
        }
        <= regions
    ):
        raise ContractError(
            "长尾发现请求必须同时覆盖 modal_baseline、positive_tail 与 "
            "negative_tail，不能通过缩小候选池绕过两侧搜索"
        )
    if len(rows) >= 4 and not {"positive_tail", "negative_tail"} <= regions:
        raise ContractError(
            "四个及以上候选的搜索必须同时覆盖 positive_tail 与 negative_tail"
        )

    eligible = [row for row in rows if row["hard_gate_passed"]]
    rejected = [row["candidate_id"] for row in rows if row not in eligible]
    frontier = _pareto_frontier(eligible)
    regional_frontiers = {
        region: _pareto_frontier(
            [row for row in eligible if row["search_region"] == region]
        )
        for region in sorted(regions)
    }

    sentinels = [
        row["candidate_id"]
        for row in eligible
        if row["search_region"] in SENTINEL_REGIONS
    ]
    selected_set = set(frontier) | set(sentinels)
    for regional_ids in regional_frontiers.values():
        selected_set.update(regional_ids)
    selected = [
        candidate_id for candidate_id in typed_ids if candidate_id in selected_set
    ]
    dominated = [
        row["candidate_id"]
        for row in eligible
        if row["candidate_id"] not in selected_set
    ]
    return {
        "schema_version": TAIL_REVIEW_VERSION,
        "reviewer_mode": TAIL_REVIEWER_MODE,
        "source_candidate_pool_sha256": source_pool_sha,
        "evidence_sha256": evidence_sha256,
        "candidates": rows,
        "eligible_candidate_ids": [
            candidate_id
            for candidate_id in typed_ids
            if candidate_id in {row["candidate_id"] for row in eligible}
        ],
        "pareto_frontier_ids": [
            candidate_id for candidate_id in typed_ids if candidate_id in set(frontier)
        ],
        "regional_frontier_ids": regional_frontiers,
        "sentinel_candidate_ids": [
            candidate_id for candidate_id in typed_ids if candidate_id in set(sentinels)
        ],
        "selected_candidate_ids": selected,
        "dominated_candidate_ids": dominated,
        "rejected_candidate_ids": rejected,
        "search_regions": sorted(regions),
        "selection_policy": {
            "rubric_reward_use": (
                "logged as a training trace only; any violation fails the hard gate"
            ),
            "tail_metric_use": (
                "vector-valued Pareto selection over prediction disagreement, "
                "expected information gain, falsifiability, evidence risk, and "
                "test cost; mechanism distance is logged only as a diversity "
                "coordinate and cannot dominate another candidate"
            ),
            "diversity_preservation": (
                "union of global and per-search-region Pareto frontiers plus "
                "eligible null-control sentinels"
            ),
        },
    }


def tail_review_is_current(
    review: object,
    draft: object,
    *,
    evidence_sha256: str,
) -> bool:
    """Return whether a stored review still covers the selected live pool."""

    if not isinstance(review, dict) or not isinstance(draft, dict):
        return False
    if review.get("schema_version") != TAIL_REVIEW_VERSION:
        return False
    if review.get("evidence_sha256") != evidence_sha256:
        return False
    selected_ids = review.get("selected_candidate_ids")
    candidates = draft.get("candidates")
    if not isinstance(selected_ids, list) or not isinstance(candidates, list):
        return False
    live_ids = [
        candidate.get("id") if isinstance(candidate, dict) else None
        for candidate in candidates
    ]
    if live_ids != selected_ids:
        return False
    try:
        return review.get("selected_candidate_pool_sha256") == candidate_pool_sha256(
            candidates
        )
    except ContractError:
        return False


__all__ = [
    "BENEFIT_METRICS",
    "COST_METRICS",
    "GENERATION_OPERATORS",
    "RUBRIC_ITEMS",
    "SEARCH_REGIONS",
    "SELECTION_BENEFIT_METRICS",
    "TAIL_REVIEWER_MODE",
    "TAIL_REVIEW_VERSION",
    "candidate_pool_sha256",
    "tail_review_is_current",
    "validate_and_select_tail_review",
]
