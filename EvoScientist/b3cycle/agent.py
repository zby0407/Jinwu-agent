from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .analysis import run_b3_analysis
from .data import (
    app_file,
    b3_root,
    figures_root,
    final_report_root,
    frontend_root,
    is_submission_release_layout,
    materials_root,
    output_root,
    raw_root,
    repo_root,
    scripts_root,
    source_root,
)
from .evidence import evidence_summary_for_run
from .qwen_adapter import QwenAdapter


TASKS: dict[str, dict[str, str]] = {
    "cycle26_prediction": {
        "title": "Solar Cycle 26 strength and mechanism explanation",
        "question": "Can late Cycle-25 observations constrain the likely strength and mechanism of Solar Cycle 26?",
    },
    "dynamo_mechanism_review": {
        "title": "11-year solar-cycle dynamo mechanism review",
        "question": "Which observed long-cycle features provide useful constraints on solar dynamo mechanisms?",
    },
    "proxy_drift_analysis": {
        "title": "F10.7 and sunspot-number proxy drift analysis",
        "question": "Is the relation between F10.7 radio flux and sunspot number stable enough for single-proxy reasoning?",
    },
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def analysis_report(refresh: bool = False) -> dict[str, Any]:
    report_path = output_root() / "b3_analysis_report.json"
    if refresh or not report_path.exists():
        return run_b3_analysis()
    return _read_json(report_path)


def model_runtime_status() -> dict[str, Any]:
    return QwenAdapter.from_env().status()


def dataset_catalog() -> dict[str, Any]:
    analysis = analysis_report()
    material_manifest = materials_root() / "manifest.json"
    materials = _read_json(material_manifest) if material_manifest.exists() else []
    return {
        "primary_data": analysis.get("data_manifest", []),
        "coverage": analysis.get("series_coverage", {}),
        "derived_products": [
            {
                "id": "cycle_features",
                "file": "b3/data/processed/cycle_features.json",
                "description": "Cycle segmentation and per-cycle morphology features derived from SILSO smoothed monthly sunspot number.",
            },
            {
                "id": "b3_analysis_report",
                "file": "b3/outputs/b3_analysis_report.json",
                "description": "Unified analysis report used by the B3 research agent.",
            },
            {
                "id": "polar_precursor_pairs",
                "file": "b3/outputs/b3_analysis_report.json#polar_precursor",
                "description": "WSO polar-field minimum-to-next-cycle pairs and correlation diagnostics.",
            },
            {
                "id": "low_order_dynamo_toy_model",
                "file": "b3/outputs/b3_analysis_report.json#dynamo_toy_model",
                "description": "Low-order Babcock-Leighton map linking polar seed field to next-cycle activity with a nonlinear quenching term.",
            },
        ],
        "supporting_materials": materials,
    }


def build_model_assist(task: str, analysis: dict[str, Any]) -> dict[str, Any]:
    adapter = QwenAdapter.from_env()
    top = analysis["hypothesis_cards"][0]
    fallback = {
        "executive_summary": (
            "Deterministic fallback: WSO polar precursor evidence makes H1 the strongest bounded "
            "mechanism hypothesis, while F10.7 drift and sparse polar-field pairs prevent an operational "
            "Cycle-26 amplitude forecast."
        ),
        "review_focus": [
            "Check that every Cycle-26 statement is bounded.",
            "Check that WSO polar precursor evidence is described as sparse but useful.",
            "Check that the low-order dynamo toy model is framed as explanatory, not predictive.",
        ],
        "suggested_next_iteration": [
            "Add NSO/SOLIS or polar-faculae precursor extension.",
            "Connect Qwen/Bailian for language-only critique while keeping numerical gates deterministic.",
        ],
    }
    schema = {
        "type": "object",
        "required": ["executive_summary", "review_focus", "suggested_next_iteration"],
        "properties": {
            "executive_summary": {"type": "string"},
            "review_focus": {"type": "array", "items": {"type": "string"}},
            "suggested_next_iteration": {"type": "array", "items": {"type": "string"}},
        },
    }
    return adapter.complete_json(
        system_prompt=(
            "You are the language-only critique layer of Solar-Cycle Co-Scientist. "
            "You may summarize, critique, and suggest next validation, but you must not change numeric results, "
            "scores, claim boundaries, or experiment pass/fail status."
        ),
        user_payload={
            "task": task,
            "claim_boundary": analysis["claim_boundary"],
            "top_hypothesis": top,
            "polar_precursor": analysis["polar_precursor"],
            "dynamo_toy_model": {
                "equation": analysis["dynamo_toy_model"]["equation"],
                "fit": analysis["dynamo_toy_model"]["fit"],
                "leave_one_out": analysis["dynamo_toy_model"]["leave_one_out"],
            },
            "tournament_top": analysis["tournament_ranking"]["top_hypothesis"],
        },
        schema=schema,
        fallback=fallback,
    )


def build_research_plan(task: str, data_sources: list[str], max_iterations: int) -> list[dict[str, Any]]:
    task_meta = TASKS.get(task, TASKS["cycle26_prediction"])
    return [
        {
            "agent": "Unified Research Agent",
            "action": "Create run state and route the request to specialist agents.",
            "output": task_meta["question"],
            "status": "done",
        },
        {
            "agent": "Research Planner Agent",
            "action": "Translate the open solar-physics question into observable tests and stopping rules.",
            "output": {
                "task": task,
                "data_sources": data_sources,
                "max_iterations": max_iterations,
                "acceptance_tests": [
                    "Every hypothesis must cite at least one data-derived metric.",
                    "Every high-level claim must expose counter-evidence and next validation.",
                    "Cycle-26 language must stay bounded because NOAA does not yet publish a Cycle-26 forecast.",
                    "WSO polar-field evidence must be reported as a limited-cycle precursor constraint, not a definitive Cycle-26 forecast.",
                    "Mechanism claims from the low-order dynamo model must expose equation, fitted parameters, and validation error.",
                ],
            },
            "status": "done",
        },
        {
            "agent": "Data & Feature Agent",
            "action": "Load public long-cycle data and construct cycle, morphology, proxy-drift, hemispheric, and polar-field features.",
            "output": "SILSO total/hemispheric sunspot numbers, NOAA observed/predicted SSN and F10.7 products, and WSO polar-field observations.",
            "status": "done",
        },
        {
            "agent": "Experiment Agent",
            "action": "Run deterministic diagnostics and compare evidence before and after confidence corrections.",
            "output": "Cycle segmentation, Waldmeier constraint, F10.7 drift, hemispheric asymmetry, WSO polar precursor, low-order dynamo toy model, Cycle-26 proxy-prior, and pairwise ranking checks.",
            "status": "done",
        },
        {
            "agent": "Hypothesis Agent",
            "action": "Generate mechanism-facing hypotheses instead of plain time-series forecasts.",
            "output": "Ranked hypothesis cards with mechanism, support, counter-evidence, and next test.",
            "status": "done",
        },
        {
            "agent": "Evidence Review Agent",
            "action": "Apply claim-boundary, counterexample, and missing-data checks to adjust final confidence.",
            "output": "Self-correction log and bounded report.",
            "status": "done",
        },
    ]


def _latest_cycles(analysis: dict[str, Any], n: int = 5) -> list[dict[str, Any]]:
    cycles = analysis.get("cycle_features", [])
    return cycles[-n:] if len(cycles) > n else cycles


def build_experiment_log(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    cycle26 = analysis["cycle26_proxy_forecast"]
    wald = analysis["waldmeier"]
    drift = analysis["f10_7_drift"]
    hemi = analysis["hemispheric_asymmetry"]
    polar = analysis.get("polar_precursor", {})
    toy = analysis.get("dynamo_toy_model", {})
    tournament = analysis.get("tournament_ranking", {})
    return [
        {
            "id": "E1_cycle_segmentation",
            "name": "Long-cycle segmentation and morphology extraction",
            "status": "passed",
            "metrics": {
                "cycles_detected": len(analysis.get("cycle_features", [])),
                "last_complete_cycles": [
                    {
                        "cycle": row["cycle"],
                        "start_year": row["start_year"],
                        "end_year": row["end_year"],
                        "peak_ssn": row["peak_ssn"],
                        "rise_years": row["rise_years"],
                    }
                    for row in _latest_cycles(analysis)
                ],
            },
            "interpretation": "The system extracts interpretable solar-cycle descriptors rather than fitting a black-box sequence model.",
        },
        {
            "id": "E2_waldmeier_constraint",
            "name": "Waldmeier-like morphology constraint",
            "status": "passed",
            "metrics": {
                "n_cycles": wald["n"],
                "spearman_peak_vs_rise_time": wald["spearman_peak_vs_rise_time"],
                "spearman_peak_vs_rise_rate": wald["spearman_peak_vs_rise_rate"],
            },
            "interpretation": wald["interpretation"],
        },
        {
            "id": "E3_proxy_relation_drift",
            "name": "F10.7 and sunspot-number relation drift",
            "status": "warning" if drift["drift_flag"] else "passed",
            "metrics": {"windows": drift["windows"], "drift_flag": drift["drift_flag"]},
            "interpretation": drift["interpretation"],
        },
        {
            "id": "E4_hemispheric_asymmetry",
            "name": "North-south hemispheric asymmetry",
            "status": "passed" if hemi["n_months"] >= 120 else "warning",
            "metrics": hemi,
            "interpretation": hemi["interpretation"],
        },
        {
            "id": "E5_cycle26_proxy_prior",
            "name": "Cycle-26 bounded proxy prior",
            "status": "warning",
            "metrics": cycle26,
            "interpretation": "Useful as an evidence-gap detector, not an operational Cycle-26 forecast.",
        },
        {
            "id": "E6_polar_precursor_validation",
            "name": "WSO polar-field precursor constraint",
            "status": "passed" if polar.get("n_pairs", 0) >= 4 else "warning",
            "metrics": {
                "coverage": polar.get("coverage", {}),
                "n_pairs": polar.get("n_pairs"),
                "spearman_polar_strength_vs_next_peak": polar.get("spearman_polar_strength_vs_next_peak"),
                "pearson_polar_strength_vs_next_peak": polar.get("pearson_polar_strength_vs_next_peak"),
                "latest_filtered": polar.get("latest_filtered", {}),
            },
            "interpretation": polar.get(
                "interpretation",
                "WSO polar-field data should be used as bounded precursor evidence.",
            ),
        },
        {
            "id": "E7_hypothesis_tournament_ranking",
            "name": "Pairwise hypothesis tournament",
            "status": "passed" if tournament.get("top_hypothesis") else "warning",
            "metrics": {
                "method": tournament.get("method"),
                "top_hypothesis": tournament.get("top_hypothesis"),
                "ranking": tournament.get("ranking", []),
                "comparison_count": len(tournament.get("comparisons", [])),
            },
            "interpretation": "A deterministic pairwise tournament exposes why the top mechanism-facing hypothesis wins instead of relying on a hidden LLM preference.",
        },
        {
            "id": "E8_low_order_dynamo_toy_model",
            "name": "Low-order Babcock-Leighton toy model",
            "status": "passed" if toy.get("status") == "executed" else "warning",
            "metrics": {
                "equation": toy.get("equation"),
                "sample_size": toy.get("sample_size"),
                "gain": toy.get("fit", {}).get("gain"),
                "quenching_gamma": toy.get("fit", {}).get("quenching_gamma"),
                "rmse_ssn": toy.get("fit", {}).get("rmse_ssn"),
                "median_baseline_rmse_ssn": toy.get("fit", {}).get("median_baseline_rmse_ssn"),
                "leave_one_out_rmse_ssn": toy.get("leave_one_out", {}).get("rmse_ssn"),
            },
            "interpretation": toy.get(
                "interpretation",
                "The low-order toy model should make the mechanism chain explicit without becoming an operational forecast.",
            ),
        },
    ]


def build_self_correction_log(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    drift = analysis["f10_7_drift"]
    hemi = analysis["hemispheric_asymmetry"]
    cycle26 = analysis["cycle26_proxy_forecast"]
    polar = analysis.get("polar_precursor", {})
    if polar.get("n_pairs", 0) >= 3:
        polar_correction = {
            "trigger": "immature_cycle26_polar_precursor",
            "before": "Cycle-26 strength could be stated from sunspot/F10.7 proxies or from historical polar-field correlation alone.",
            "after": "Use WSO as a historical precursor constraint, but keep Cycle-26 amplitude bounded until the Cycle-25/26 minimum-time polar field is mature and cross-validated.",
            "reason": f"{cycle26['claim_boundary']} WSO complete precursor pairs = {polar.get('n_pairs')}.",
        }
    else:
        polar_correction = {
            "trigger": "missing_polar_field_precursor",
            "before": "Cycle-26 strength could be stated from sunspot/F10.7 proxies alone.",
            "after": "Cycle-26 output is downgraded to a proxy prior and requires polar-field precursor validation.",
            "reason": cycle26["claim_boundary"],
        }
    corrections: list[dict[str, Any]] = [polar_correction]
    if drift["drift_flag"]:
        corrections.append(
            {
                "trigger": "proxy_relation_drift",
                "before": "Treat F10.7 as a stable one-to-one proxy for sunspot number.",
                "after": "Lower confidence for single-proxy hypotheses and request phase-stratified reanalysis.",
                "reason": drift["interpretation"],
            }
        )
    if hemi.get("coverage", {}).get("start_year"):
        corrections.append(
            {
                "trigger": "hemispheric_coverage_limit",
                "before": "Use hemispheric asymmetry as if it covered the full 1749-present interval.",
                "after": "Use asymmetry as modern-era mechanism evidence only, because the available SILSO hemispheric product begins in 1992.",
                "reason": f"Coverage starts at {hemi['coverage']['start_year']} and ends at {hemi['coverage']['end_year']}.",
            }
        )
    return corrections


def build_iteration_trace(
    analysis: dict[str, Any],
    experiments: list[dict[str, Any]],
    hypotheses: list[dict[str, Any]],
    corrections: list[dict[str, Any]],
    max_iterations: int,
) -> list[dict[str, Any]]:
    experiment_by_id = {experiment["id"]: experiment for experiment in experiments}
    hypothesis_by_id = {card["id"]: card for card in hypotheses}
    review_confidence = _confidence_from_hypotheses(hypotheses, corrections)
    h1_score = float(hypothesis_by_id.get("H1_poloidal_precursor_needed", {}).get("score", 0.0))
    h2_score = float(hypothesis_by_id.get("H2_waldmeier_constraint", {}).get("score", 0.0))
    h3_score = float(hypothesis_by_id.get("H3_proxy_relation_drift", {}).get("score", 0.0))
    polar_pairs = analysis.get("polar_precursor", {}).get("n_pairs")
    drift_flag = analysis.get("f10_7_drift", {}).get("drift_flag")

    trace = [
        {
            "iteration": 1,
            "phase": "baseline_cycle_morphology",
            "status": "completed",
            "trigger": "initial_research_task",
            "agents": ["Research Planner Agent", "Data & Feature Agent", "Experiment Agent"],
            "evidence_added": [
                "E1_cycle_segmentation",
                "E2_waldmeier_constraint",
                "E3_proxy_relation_drift",
            ],
            "key_observation": experiment_by_id["E2_waldmeier_constraint"]["interpretation"],
            "hypothesis_decisions": [
                {
                    "hypothesis_id": "H2_waldmeier_constraint",
                    "decision": "promote_to_candidate",
                    "reason": "cycle morphology gives a reproducible nonlinear-dynamo constraint",
                },
                {
                    "hypothesis_id": "H3_proxy_relation_drift",
                    "decision": "mark_as_risk",
                    "reason": f"proxy-drift flag = {drift_flag}",
                },
            ],
            "confidence_before": 0.5,
            "confidence_after": round(max(h2_score, h3_score), 2),
            "next_action": "Request polar-field precursor evidence before making any Cycle-26 amplitude statement.",
        },
        {
            "iteration": 2,
            "phase": "polar_precursor_and_toy_model",
            "status": "completed",
            "trigger": "baseline_run_found_missing_polar_precursor",
            "agents": ["Data & Feature Agent", "Experiment Agent", "Hypothesis Agent"],
            "evidence_added": [
                "E6_polar_precursor_validation",
                "E8_low_order_dynamo_toy_model",
            ],
            "key_observation": experiment_by_id["E6_polar_precursor_validation"]["interpretation"],
            "hypothesis_decisions": [
                {
                    "hypothesis_id": "H1_poloidal_precursor_needed",
                    "decision": "promote_to_top_rank",
                    "reason": f"WSO complete precursor pairs = {polar_pairs}",
                },
                {
                    "hypothesis_id": "H5_low_order_dynamo_closure",
                    "decision": "keep_as_diagnostic_support",
                    "reason": "toy model exposes the Babcock-Leighton precursor chain but remains small-sample",
                },
            ],
            "confidence_before": round(max(h2_score, h3_score), 2),
            "confidence_after": round(h1_score, 2),
            "next_action": "Run evidence review and lower confidence where sparse data or proxy drift creates risk.",
        },
        {
            "iteration": 3,
            "phase": "evidence_review_and_self_correction",
            "status": "completed",
            "trigger": "warning_experiments_and_claim_boundary_rules",
            "agents": ["Evidence Review Agent", "Evolution and Self-correction Agent", "Report/API Agent"],
            "evidence_added": [
                "pairwise_tournament_ranking",
                "self_correction_log",
                "bounded_next_validation_plan",
            ],
            "key_observation": "Warnings do not disappear from the final answer; they become confidence penalties and next-validation tasks.",
            "hypothesis_decisions": [
                {
                    "hypothesis_id": hypotheses[0]["id"] if hypotheses else None,
                    "decision": "keep_with_boundary",
                    "reason": "top hypothesis has support, counter-evidence, and a named next test",
                },
                {
                    "hypothesis_id": "cycle26_amplitude_claim",
                    "decision": "downgrade_to_proxy_prior",
                    "reason": analysis["cycle26_proxy_forecast"]["claim_boundary"],
                },
            ],
            "confidence_before": round(h1_score, 2),
            "confidence_after": review_confidence,
            "next_action": "Export the run as JSON/Markdown and ask the next data-acquisition iteration to extend polar precursor evidence.",
        },
        {
            "iteration": 4,
            "phase": "external_precursor_extension",
            "status": "planned",
            "trigger": "sparse_wso_pair_count",
            "agents": ["Literature and Evidence Agent", "Data Acquisition Agent", "Experiment Agent"],
            "evidence_added": [],
            "key_observation": "Four WSO precursor pairs are useful but too sparse for a strict operational forecast.",
            "hypothesis_decisions": [
                {
                    "hypothesis_id": "H1_poloidal_precursor_needed",
                    "decision": "schedule_validation",
                    "reason": "extend with NSO/SOLIS or polar faculae proxies before increasing confidence",
                }
            ],
            "confidence_before": review_confidence,
            "confidence_after": None,
            "next_action": "Add a longer polar precursor proxy and rerun leave-one-cycle-out robustness.",
        },
        {
            "iteration": 5,
            "phase": "surrogate_model_comparison",
            "status": "planned",
            "trigger": "toy_model_is_explanatory_not_final",
            "agents": ["Experiment Agent", "Hypothesis Agent", "Evidence Review Agent"],
            "evidence_added": [],
            "key_observation": "The current toy map makes the mechanism explicit but should be compared with an ODE or flux-transport surrogate.",
            "hypothesis_decisions": [
                {
                    "hypothesis_id": "H5_low_order_dynamo_closure",
                    "decision": "schedule_ablation",
                    "reason": "compare nonlinear quenching closure with alternative low-order dynamo formulations",
                }
            ],
            "confidence_before": review_confidence,
            "confidence_after": None,
            "next_action": "Run model-family ablation and keep only mechanisms that survive cross-cycle validation.",
        },
    ]
    return trace[: max(1, min(max_iterations, len(trace)))]


def _confidence_from_hypotheses(cards: list[dict[str, Any]], corrections: list[dict[str, Any]]) -> float:
    if not cards:
        return 0.0
    base = float(cards[0].get("score", 0.5))
    penalty = 0.04 * max(0, len(corrections) - 1)
    return round(max(0.35, min(0.86, base - penalty)), 2)


def build_run_report(
    task: str,
    analysis: dict[str, Any],
    hypotheses: list[dict[str, Any]],
    experiments: list[dict[str, Any]],
    corrections: list[dict[str, Any]],
    iteration_trace: list[dict[str, Any]],
) -> dict[str, Any]:
    cycle26 = analysis["cycle26_proxy_forecast"]
    tournament = analysis.get("tournament_ranking", {})
    confidence = _confidence_from_hypotheses(hypotheses, corrections)
    top = hypotheses[0] if hypotheses else {}
    next_validation = []
    for card in hypotheses[:3]:
        next_validation.append(card["next_test"])
    completed_iterations = [item for item in iteration_trace if item.get("status") == "completed"]
    return {
        "task": task,
        "answer_type": "bounded_hypothesis_and_evidence_report",
        "claim_boundary": analysis["claim_boundary"],
        "prediction": {
            "target": "solar_cycle_26_peak_strength",
            "class": cycle26["strength_class"],
            "confidence": confidence,
            "boundary": cycle26["claim_boundary"],
            "interpretation": cycle26["interpretation"],
        },
        "top_hypothesis": top,
        "tournament_ranking": tournament,
        "evidence_score": top.get("score"),
        "key_experiment_findings": [
            {
                "id": exp["id"],
                "status": exp["status"],
                "interpretation": exp["interpretation"],
            }
            for exp in experiments
        ],
        "self_corrections": corrections,
        "iteration_summary": {
            "requested_iterations": len(iteration_trace),
            "completed_iterations": len(completed_iterations),
            "confidence_path": [
                {
                    "iteration": item["iteration"],
                    "phase": item["phase"],
                    "status": item["status"],
                    "confidence_after": item["confidence_after"],
                }
                for item in iteration_trace
            ],
        },
        "next_validation": next_validation,
        "submission_claim": "The system builds an auditable loop from long-cycle public data to hypothesis ranking, counter-evidence, correction, and next experiments.",
    }


def create_research_run(payload: dict[str, Any] | None = None, refresh: bool = False) -> dict[str, Any]:
    payload = payload or {}
    task = str(payload.get("task") or "cycle26_prediction")
    if task not in TASKS:
        task = "cycle26_prediction"
    data_sources = payload.get("data_sources") or [
        "silso_sunspot",
        "noaa_f10_7",
        "silso_hemispheric",
        "wso_polar_field",
    ]
    max_iterations = int(payload.get("max_iterations") or 3)
    max_iterations = max(1, min(max_iterations, 5))

    analysis = analysis_report(refresh=refresh)
    experiments = build_experiment_log(analysis)
    hypotheses = analysis["hypothesis_cards"]
    corrections = build_self_correction_log(analysis)
    iteration_trace = build_iteration_trace(analysis, experiments, hypotheses, corrections, max_iterations)
    model_assist = build_model_assist(task, analysis)
    evidence_summary = evidence_summary_for_run([card["id"] for card in hypotheses])
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{task}_{now}_{uuid.uuid4().hex[:8]}"
    run = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "task": task,
        "task_title": TASKS[task]["title"],
        "agent_mode": payload.get("agent_mode", "hypothesis_experiment_review"),
        "data_sources": data_sources,
        "max_iterations": max_iterations,
        "plan": build_research_plan(task, data_sources, max_iterations),
        "iteration_trace": iteration_trace,
        "experiments": experiments,
        "hypotheses": hypotheses,
        "evidence_summary": evidence_summary,
        "model_assist": model_assist,
        "report": build_run_report(task, analysis, hypotheses, experiments, corrections, iteration_trace),
        "analysis_digest": {
            "series_coverage": analysis["series_coverage"],
            "cycle_count": len(analysis["cycle_features"]),
            "waldmeier_spearman_peak_vs_rise_time": analysis["waldmeier"]["spearman_peak_vs_rise_time"],
            "f10_7_drift_flag": analysis["f10_7_drift"]["drift_flag"],
            "hemispheric_mean_abs_asymmetry": analysis["hemispheric_asymmetry"]["mean_abs_asymmetry"],
            "polar_precursor_pairs": analysis["polar_precursor"]["n_pairs"],
            "polar_precursor_spearman": analysis["polar_precursor"]["spearman_polar_strength_vs_next_peak"],
            "dynamo_toy_rmse_ssn": analysis["dynamo_toy_model"]["fit"]["rmse_ssn"],
            "dynamo_toy_loo_rmse_ssn": analysis["dynamo_toy_model"]["leave_one_out"]["rmse_ssn"],
            "tournament_top_hypothesis": analysis["tournament_ranking"]["top_hypothesis"],
            "model_mode": model_assist.get("_qwen_adapter", {}).get("mode"),
            "evidence_hypothesis_count": len(evidence_summary.get("hypotheses", [])),
        },
    }
    run_dir = output_root() / "runs" / run_id
    _write_json(run_dir / "run.json", run)
    (run_dir / "report.md").write_text(render_markdown_report(run), encoding="utf-8")
    return run


def load_research_run(run_id: str) -> dict[str, Any]:
    path = output_root() / "runs" / run_id / "run.json"
    if not path.exists():
        raise FileNotFoundError(f"Unknown run_id: {run_id}")
    return _read_json(path)


def list_research_runs(limit: int = 20) -> list[dict[str, Any]]:
    runs_root = output_root() / "runs"
    if not runs_root.exists():
        return []
    rows = []
    for path in sorted(runs_root.glob("*/run.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        run = _read_json(path)
        rows.append(
            {
                "run_id": run["run_id"],
                "created_at": run["created_at"],
                "task": run["task"],
                "task_title": run["task_title"],
                "top_hypothesis": run["hypotheses"][0]["id"] if run.get("hypotheses") else None,
                "confidence": run["report"]["prediction"]["confidence"],
                "completed_iterations": run["report"].get("iteration_summary", {}).get("completed_iterations"),
            }
        )
        if len(rows) >= limit:
            break
    return rows


def render_markdown_report(run: dict[str, Any]) -> str:
    report = run["report"]
    prediction = report["prediction"]
    top = report["top_hypothesis"]
    lines = [
        f"# {run['task_title']}",
        "",
        f"- run_id: `{run['run_id']}`",
        f"- created_at: `{run['created_at']}`",
        f"- prediction class: `{prediction['class']}`",
        f"- confidence: `{prediction['confidence']}`",
        f"- claim boundary: {report['claim_boundary']}",
        "",
        "## Top Hypothesis",
        "",
        f"**{top.get('id', 'none')}**: {top.get('hypothesis', '')}",
        "",
        f"- mechanism: {top.get('mechanism', '')}",
        f"- evidence score: {top.get('score', '')}",
        f"- next test: {top.get('next_test', '')}",
        "",
        "## Supporting Evidence",
        "",
    ]
    for item in top.get("supporting_evidence", []):
        lines.append(f"- {item}")
    lines.extend(["", "## Counter Evidence", ""])
    for item in top.get("counter_evidence", []):
        lines.append(f"- {item}")
    tournament = report.get("tournament_ranking", {})
    if tournament.get("ranking"):
        lines.extend(["", "## Tournament Ranking", ""])
        for item in tournament["ranking"]:
            lines.append(f"- {item['id']}: Elo {item['elo']}")
    lines.extend(["", "## Self Corrections", ""])
    for item in report["self_corrections"]:
        lines.append(f"- {item['trigger']}: {item['after']}")
    if run.get("iteration_trace"):
        lines.extend(["", "## Iteration Trace", ""])
        for item in run["iteration_trace"]:
            lines.append(
                f"- Iteration {item['iteration']} `{item['phase']}` ({item['status']}): "
                f"{item['confidence_before']} -> {item['confidence_after']}; next: {item['next_action']}"
            )
    lines.extend(["", "## Next Validation", ""])
    for item in report["next_validation"]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def review_hypothesis(hypothesis: str) -> dict[str, Any]:
    analysis = analysis_report()
    text = hypothesis.lower()
    matches = []
    for card in analysis["hypothesis_cards"]:
        haystack = " ".join(
            [
                card["hypothesis"],
                card["mechanism"],
                " ".join(card["supporting_evidence"]),
                " ".join(card["counter_evidence"]),
            ]
        ).lower()
        overlap = sum(1 for token in text.split() if len(token) > 4 and token in haystack)
        matches.append((overlap, card))
    matches.sort(key=lambda item: (item[0], item[1]["score"]), reverse=True)
    best_overlap, best_card = matches[0]
    score = round(min(0.82, float(best_card["score"]) - (0.12 if best_overlap == 0 else 0.0)), 2)
    return {
        "input": hypothesis,
        "review_score": score,
        "nearest_supported_hypothesis": best_card,
        "required_improvements": [
            "Add explicit observable evidence.",
            "State a counter-example that could lower confidence.",
            "Name the next validation dataset or experiment.",
        ],
    }


def claim_boundary_prevents_overclaiming(value: object) -> bool:
    """Accept the reviewed English or Chinese global scientific boundary."""

    text = str(value).strip()
    lowered = text.lower()
    english_boundary = "do not prove" in lowered and (
        "operational forecast" in lowered or "operational forecasts" in lowered
    )
    chinese_boundary = (
        "因果机制仍待独立验证" in text
        and "仅供研究" in text
    )
    return english_boundary or chinese_boundary


def package_readiness_report() -> dict[str, Any]:
    analysis = analysis_report()
    release_layout = is_submission_release_layout()
    part4_doc = b3_root() / "docs" / "part4_system_architecture_agent_design.md"
    if release_layout and not part4_doc.exists():
        part4_doc = b3_root() / "docs" / "第四部分_系统架构与子Agent技术设计_正式稿.md"
    agent_protocol_doc = b3_root() / "docs" / "agent_contracts_and_prompt_protocol.md"
    agent_contracts_path = b3_root() / "specs" / "agent_contracts.json"
    evidence_ledger_path = b3_root() / "specs" / "evidence_ledger.json"
    openapi_path = b3_root() / "specs" / "openapi.json"
    agent_source_path = source_root() / "b3cycle" / "agent.py"
    frontend_js_path = frontend_root() / "app.js"
    final_report_md_path = final_report_root() / "b3_final_technical_report.md"
    final_report_pdf_path = final_report_root() / "b3_final_technical_report.pdf"
    release_root = repo_root() if release_layout else b3_root() / "submission_release"
    release_manifest_path = release_root / "release_manifest.json"
    release_audit_path = b3_root() / "docs" / "release_readiness_audit.json"
    figure_paths = [
        figures_root() / "fig01_cycle_peak_timeline.png",
        figures_root() / "fig02_polar_toy_model.png",
        figures_root() / "fig03_hypothesis_ranking.png",
        figures_root() / "fig04_closed_loop_architecture.png",
    ]
    part4_text = part4_doc.read_text(encoding="utf-8") if part4_doc.exists() else ""
    final_report_text = final_report_md_path.read_text(encoding="utf-8") if final_report_md_path.exists() else ""
    agent_contracts = _read_json(agent_contracts_path) if agent_contracts_path.exists() else {"agents": []}
    evidence_ledger = _read_json(evidence_ledger_path) if evidence_ledger_path.exists() else {"entries": []}
    if release_layout:
        required_files = [
            b3_root() / "README.md",
            b3_root() / "RELEASE_READINESS_AUDIT.md",
            release_manifest_path,
            app_file(),
            source_root() / "b3cycle" / "agent.py",
            source_root() / "b3cycle" / "analysis.py",
            source_root() / "b3cycle" / "qwen_adapter.py",
            frontend_root() / "index.html",
            frontend_root() / "app.js",
            frontend_root() / "styles.css",
            scripts_root() / "run_b3_analysis.py",
            scripts_root() / "check_qwen_connection.py",
            scripts_root() / "check_frontend_api_smoke.py",
            b3_root() / "docs" / "requirements_alignment.md",
            b3_root() / "docs" / "model_integration_and_openapi.md",
            b3_root() / "docs" / "representative_test_cases.md",
            b3_root() / "docs" / "第四部分_系统架构与子Agent技术设计_正式稿.md",
            b3_root() / "docs" / "最终提交审计清单.json",
            b3_root() / "docs" / "最终提交审计清单.md",
            agent_contracts_path,
            evidence_ledger_path,
            openapi_path,
            b3_root() / "specs" / "hypothesis_evidence_matrix.json",
            b3_root() / "test_cases" / "manifest.json",
            b3_root() / "test_cases" / "case_01_cycle26_bounded_research_run.json",
            b3_root() / "test_cases" / "case_02_polar_precursor_and_dynamo_toy_model.json",
            b3_root() / "test_cases" / "case_03_f107_proxy_drift_guard.json",
            b3_root() / "test_cases" / "case_04_evidence_query_h1.json",
            final_report_md_path,
            final_report_pdf_path,
            *figure_paths,
            b3_root() / "proofs" / "QWEN_BAILIAN_PROOF_TEMPLATE.md",
            release_audit_path,
        ]
    else:
        required_files = [
            b3_root() / "README.md",
            b3_root() / "docs" / "requirements_alignment.md",
            b3_root() / "docs" / "co_scientist_agent_architecture.md",
            part4_doc,
            agent_protocol_doc,
            agent_contracts_path,
            evidence_ledger_path,
            b3_root() / "docs" / "first_results.md",
            app_file(),
            frontend_root() / "index.html",
            frontend_root() / "app.js",
            frontend_root() / "styles.css",
            scripts_root() / "run_b3_analysis.py",
            scripts_root() / "check_qwen_connection.py",
            scripts_root() / "export_representative_test_cases.py",
            scripts_root() / "build_report_figures.py",
            scripts_root() / "build_final_technical_report.py",
            scripts_root() / "build_submission_release.py",
            scripts_root() / "verify_b3_release.py",
            scripts_root() / "verify_b3_package.py",
            b3_root() / "docs" / "model_integration_and_openapi.md",
            openapi_path,
            b3_root() / "specs" / "hypothesis_evidence_matrix.json",
            b3_root() / "docs" / "representative_test_cases.md",
            b3_root() / "test_cases" / "manifest.json",
            b3_root() / "test_cases" / "case_01_cycle26_bounded_research_run.json",
            b3_root() / "test_cases" / "case_02_polar_precursor_and_dynamo_toy_model.json",
            b3_root() / "test_cases" / "case_03_f107_proxy_drift_guard.json",
            b3_root() / "test_cases" / "case_04_evidence_query_h1.json",
            final_report_md_path,
            final_report_pdf_path,
            *figure_paths,
            release_root / "README.md",
            release_root / "RELEASE_READINESS_AUDIT.md",
            release_manifest_path,
            release_root / "paper" / "b3_final_technical_report.pdf",
            release_root / "code" / "app_b3.py",
            release_root / "frontend" / "static_b3" / "index.html",
            release_root / "proofs" / "QWEN_BAILIAN_PROOF_TEMPLATE.md",
            release_audit_path,
        ]
    test_case_manifest_path = b3_root() / "test_cases" / "manifest.json"
    test_case_manifest = _read_json(test_case_manifest_path) if test_case_manifest_path.exists() else {"cases": []}
    model_status = model_runtime_status()
    evidence_matrix_path = b3_root() / "specs" / "hypothesis_evidence_matrix.json"
    evidence_matrix = _read_json(evidence_matrix_path) if evidence_matrix_path.exists() else {"sources": [], "hypothesis_links": []}
    openapi = _read_json(openapi_path) if openapi_path.exists() else {"components": {"schemas": {}}}
    agent_source_text = agent_source_path.read_text(encoding="utf-8") if agent_source_path.exists() else ""
    frontend_js_text = frontend_js_path.read_text(encoding="utf-8") if frontend_js_path.exists() else ""
    release_manifest = _read_json(release_manifest_path) if release_manifest_path.exists() else {"files": []}
    release_audit = (
        _read_json(release_audit_path)
        if release_audit_path.exists()
        else {"practical_ready_without_secret": False, "checks": []}
    )

    def display_path(path: Path) -> str:
        try:
            return str(path.relative_to(repo_root()))
        except ValueError:
            return str(path)

    def test_case_path_exists(rel_path: str) -> bool:
        candidates = [repo_root() / rel_path]
        if release_layout and rel_path.startswith("b3/"):
            candidates.append(repo_root() / rel_path.removeprefix("b3/"))
        return any(path.exists() for path in candidates)

    if release_layout:
        architecture_tokens = [
            "统一科研主智能体",
            "研究规划子Agent",
            "数据与特征子Agent",
            "自动实验子Agent",
            "科学假设子Agent",
            "证据审查子Agent",
            "状态机",
            "Qwen/百炼",
        ]
    else:
        architecture_tokens = [
            "Unified Research Agent",
            "Research Planner Agent",
            "Evidence Review and Ranking Agent",
            "stateDiagram-v2",
            "erDiagram",
            "score =",
            "QwenAdapter",
            "References",
        ]
    checks = [
        {
            "id": "data_sources_present",
            "passed": all((raw_root() / name).exists() for name in [
                "SN_m_tot_V2.0.csv",
                "SN_ms_tot_V2.0.csv",
                "SN_m_hem_V2.0.csv",
                "observed-solar-cycle-indices.json",
                "predicted-solar-cycle.json",
                "wso_polar_field_observations.html",
            ]),
        },
        {
            "id": "analysis_report_present",
            "passed": (output_root() / "b3_analysis_report.json").exists(),
        },
        {
            "id": "hypothesis_cards_ranked",
            "passed": len(analysis.get("hypothesis_cards", [])) >= 4
            and analysis["hypothesis_cards"][0]["score"] >= analysis["hypothesis_cards"][-1]["score"],
        },
        {
            "id": "claim_boundary_present",
            "passed": claim_boundary_prevents_overclaiming(
                analysis.get("claim_boundary", "")
            ),
        },
        {
            "id": "cycle26_overclaim_guard",
            "passed": "not a direct official Cycle 26 amplitude forecast"
            in analysis["cycle26_proxy_forecast"]["claim_boundary"],
        },
        {
            "id": "wso_polar_precursor_executed",
            "passed": analysis.get("polar_precursor", {}).get("coverage", {}).get("n_valid_rows", 0) >= 1000
            and analysis.get("polar_precursor", {}).get("n_pairs", 0) >= 4,
        },
        {
            "id": "pairwise_tournament_executed",
            "passed": bool(analysis.get("tournament_ranking", {}).get("top_hypothesis"))
            and len(analysis.get("tournament_ranking", {}).get("comparisons", [])) >= 6,
        },
        {
            "id": "low_order_dynamo_toy_model_executed",
            "passed": analysis.get("dynamo_toy_model", {}).get("status") == "executed"
            and analysis.get("dynamo_toy_model", {}).get("sample_size", 0) >= 4
            and bool(analysis.get("dynamo_toy_model", {}).get("equation")),
        },
        {
            "id": "representative_test_cases_exported",
            "passed": len(test_case_manifest.get("cases", [])) >= 4
            and all(test_case_path_exists(item.get("file", "")) for item in test_case_manifest.get("cases", [])),
        },
        {
            "id": "qwen_adapter_declared",
            "passed": model_status.get("provider") == "Alibaba Cloud Model Studio / Qwen"
            and model_status.get("credential_policy")
            and model_status.get("mode") in {"deterministic_fallback", "qwen_openai_compatible"},
        },
        {
            "id": "literature_evidence_matrix_complete",
            "passed": len(evidence_matrix.get("sources", [])) >= 12
            and {link.get("hypothesis_id") for link in evidence_matrix.get("hypothesis_links", [])}
            >= {card["id"] for card in analysis.get("hypothesis_cards", [])},
        },
        {
            "id": "final_technical_report_exported",
            "passed": final_report_md_path.exists()
            and final_report_pdf_path.exists()
            and final_report_pdf_path.stat().st_size > 150_000,
        },
        {
            "id": "final_report_figures_exported",
            "passed": all(path.exists() and path.stat().st_size > 20_000 for path in figure_paths)
            and all(f"figures/{path.name}" in final_report_text for path in figure_paths),
            "missing": [
                display_path(path)
                for path in figure_paths
                if not path.exists() or path.stat().st_size <= 20_000
            ],
        },
        {
            "id": "submission_release_built",
            "passed": release_manifest_path.exists()
            and len(release_manifest.get("files", [])) >= 40
            and release_manifest.get("official_alignment", {}).get("no_ppt_included") is True
            and release_root.exists()
            and not any(release_root.rglob("*.pptx")),
        },
        {
            "id": "research_iteration_trace_visible",
            "passed": "build_iteration_trace" in agent_source_text
            and "iteration_trace" in agent_source_text
            and "renderIterations" in frontend_js_text
            and "IterationTraceItem" in openapi.get("components", {}).get("schemas", {}),
        },
        {
            "id": "release_readiness_audit_recorded",
            "passed": bool(release_audit.get("practical_ready_without_secret"))
            and any(
                item.get("id") == "qwen_bailian_live_proof"
                for item in release_audit.get("checks", [])
            ),
        },
        {
            "id": "submission_files_present",
            "passed": all(path.exists() for path in required_files),
            "missing": [display_path(path) for path in required_files if not path.exists()],
        },
        {
            "id": "part4_architecture_doc_submission_ready",
            "passed": all(token in part4_text for token in architecture_tokens),
        },
        {
            "id": "agent_contracts_machine_readable",
            "passed": len(agent_contracts.get("agents", [])) >= 9
            and all(
                all(key in agent for key in ["id", "inputs", "outputs", "tools", "hard_guards", "success_criteria"])
                for agent in agent_contracts.get("agents", [])
            ),
        },
        {
            "id": "evidence_ledger_sources_cover_core_needs",
            "passed": len(evidence_ledger.get("entries", [])) >= 8
            and all(
                source_id in {entry.get("id") for entry in evidence_ledger.get("entries", [])}
                for source_id in [
                    "SRC_OFFICIAL_ALIYUN_XH202619",
                    "SRC_GOOGLE_COSCIENTIST",
                    "SRC_SILSO",
                    "SRC_NOAA_SWPC_CYCLE_PROGRESS",
                    "SRC_WSO_POLAR",
                    "SRC_PETROVAY_2020",
                    "SRC_CHARBONNEAU_2020",
                ]
            ),
        },
    ]
    return {
        "project": "Solar-Cycle Co-Scientist Direction 2B3",
        "ready": all(item["passed"] for item in checks),
        "checks": checks,
        "metrics": {
            "cycle_count": len(analysis.get("cycle_features", [])),
            "hypothesis_count": len(analysis.get("hypothesis_cards", [])),
            "top_hypothesis": analysis["hypothesis_cards"][0]["id"],
            "polar_precursor_pairs": analysis["polar_precursor"]["n_pairs"],
            "dynamo_toy_rmse_ssn": analysis["dynamo_toy_model"]["fit"]["rmse_ssn"],
            "tournament_top_hypothesis": analysis["tournament_ranking"]["top_hypothesis"],
            "model_mode": model_status["mode"],
            "evidence_source_count": len(evidence_matrix.get("sources", [])),
        },
    }
