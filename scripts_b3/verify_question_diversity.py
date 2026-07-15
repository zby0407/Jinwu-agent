#!/usr/bin/env python3
"""Verify multi-question routing and exact task binding for the three B3 agents."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from b3cycle.science_agents import (  # noqa: E402
    REGISTERED_EXPERIMENTS,
    RunStore,
    canonical_json_sha256,
    submit_research_plan_draft,
    validate_research_plan,
)


SCHEMA_VERSION = "b3-question-diversity-proof-v1"
CASE_PATH = Path("b3/evals/question_diversity_cases.json")
CATALOG_PATH = Path("b3/specs/experiment_catalog.json")
PROMPT_PATH = Path(".pi/prompts/b3-research-loop.md")
DEPRECATED_ALIAS_PATH = Path(".pi/prompts/b3-agent-run.md")
PROOF_JSON_PATH = Path("b3/proofs/question_diversity.json")
PROOF_MD_PATH = Path("b3/proofs/question_diversity.md")
EXPECTED_AGENTS = {
    "b3-research-planner": Path(".pi/agents/b3-research-planner.md"),
    "b3-experiment": Path(".pi/agents/b3-experiment.md"),
    "b3-hypothesis": Path(".pi/agents/b3-hypothesis.md"),
}


SOURCE_CONTRACTS: dict[str, dict[str, str]] = {
    "silso_monthly_total": {
        "source": "SILSO monthly total sunspot number",
        "url": "https://www.sidc.be/SILSO/datafiles",
        "version": "2.0",
        "license": "SILSO terms",
        "time_coverage": "1749-present",
        "available_at": "after monthly publication",
        "semantic_layer": "observation",
        "data_status": "definitive",
        "sha256": "1" * 64,
    },
    "silso_monthly_smoothed_total": {
        "source": "SILSO monthly smoothed total sunspot number",
        "url": "https://www.sidc.be/SILSO/datafiles",
        "version": "2.0",
        "license": "SILSO terms",
        "time_coverage": "1749-present",
        "available_at": "after the centered smoothing future half-window",
        "semantic_layer": "observation",
        "data_status": "definitive",
        "sha256": "2" * 64,
    },
    "silso_monthly_hemispheric": {
        "source": "SILSO post-1992 direct hemispheric observations",
        "url": "https://www.sidc.be/SILSO/datafiles",
        "version": "2.0",
        "license": "SILSO terms",
        "time_coverage": "1992-present",
        "available_at": "after monthly publication",
        "semantic_layer": "observation",
        "data_status": "definitive",
        "sha256": "3" * 64,
    },
    "silso_extended_hemispheric_catalogue_b": {
        "source": "extended pre-1992 hemispheric reconstruction",
        "url": "https://doi.org/10.1051/0004-6361/201936352",
        "version": "reviewed local snapshot",
        "license": "source terms",
        "time_coverage": "1874-1992",
        "available_at": "after reconstruction publication",
        "semantic_layer": "reconstruction",
        "data_status": "retrospective",
        "sha256": "4" * 64,
    },
    "noaa_observed_solar_cycle_indices": {
        "source": "NOAA observed solar-cycle indices including F10.7",
        "url": "https://services.swpc.noaa.gov/json/solar-cycle/observed-solar-cycle-indices.json",
        "version": "frozen local vintage",
        "license": "US government public data",
        "time_coverage": "current published observations",
        "available_at": "after daily publication",
        "semantic_layer": "proxy",
        "data_status": "provisional",
        "sha256": "5" * 64,
    },
    "noaa_predicted_solar_cycle": {
        "source": "NOAA published solar-cycle prediction series",
        "url": "https://services.swpc.noaa.gov/json/solar-cycle/predicted-solar-cycle.json",
        "version": "frozen local vintage",
        "license": "US government public data",
        "time_coverage": "published prediction horizon",
        "available_at": "after bulletin publication",
        "semantic_layer": "proxy",
        "data_status": "provisional",
        "sha256": "6" * 64,
    },
    "wso_polar_field_observations": {
        "source": "Wilcox Solar Observatory polar-field observations",
        "url": "http://wso.stanford.edu/Polar.html",
        "version": "frozen local snapshot",
        "license": "WSO source terms",
        "time_coverage": "1976-present",
        "available_at": "after observation publication",
        "semantic_layer": "observation",
        "data_status": "retrospective",
        "sha256": "7" * 64,
    },
}

EXPERIMENT_SOURCES: dict[str, tuple[str, ...]] = {
    "E0_data_vintage_audit": tuple(SOURCE_CONTRACTS),
    "E1_cycle_segmentation_baseline": (
        "silso_monthly_total",
        "silso_monthly_smoothed_total",
    ),
    "E2_waldmeier_leave_one_cycle_out": ("silso_monthly_smoothed_total",),
    "E3_f107_phase_stratified_drift": ("noaa_observed_solar_cycle_indices",),
    "E4_extended_hemispheric_calibration": (
        "silso_extended_hemispheric_catalogue_b",
        "silso_monthly_hemispheric",
    ),
    "E5_polar_precursor_robustness": (
        "silso_monthly_smoothed_total",
        "wso_polar_field_observations",
    ),
    "E6_low_order_dynamo_family_ablation": (
        "silso_monthly_smoothed_total",
        "wso_polar_field_observations",
    ),
    "E7_negative_controls_and_placebos": (
        "silso_monthly_smoothed_total",
        "wso_polar_field_observations",
    ),
    "E8_clean_reproduction": tuple(SOURCE_CONTRACTS),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.as_posix()} must contain one JSON object")
    return payload


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _split_strategy(experiment_id: str) -> str:
    if experiment_id in {
        "E0_data_vintage_audit",
        "E4_extended_hemispheric_calibration",
        "E7_negative_controls_and_placebos",
        "E8_clean_reproduction",
    }:
        return "not_applicable"
    if experiment_id in {
        "E2_waldmeier_leave_one_cycle_out",
        "E5_polar_precursor_robustness",
    }:
        return "leave_one_cycle_out"
    return "expanding_window"


def _node_type(experiment_id: str) -> str:
    if experiment_id.startswith("E0_"):
        return "data_audit"
    if experiment_id.startswith("E1_"):
        return "baseline"
    if experiment_id.startswith("E6_"):
        return "ablation"
    if experiment_id.startswith("E7_"):
        return "negative_control"
    if experiment_id.startswith("E8_"):
        return "replication"
    return "experiment"


def _success_criteria(experiment_id: str) -> list[str]:
    if experiment_id == "E4_extended_hemispheric_calibration":
        return [
            "Calibrate reconstruction against direct observation only on the declared overlap; if overlap error exceeds its uncertainty tolerance, mark E4 inconclusive"
        ]
    return [
        "The registered result and accounting are finite and complete; otherwise retain the failure and reject or downgrade the bounded claim"
    ]


def _plan_draft(case: dict[str, Any]) -> dict[str, Any]:
    experiment_ids = [str(item) for item in case["expected_experiments"]]
    source_ids: list[str] = []
    for experiment_id in experiment_ids:
        for source_id in EXPERIMENT_SOURCES[experiment_id]:
            if source_id not in source_ids:
                source_ids.append(source_id)
    contracts = [
        {"id": source_id, **SOURCE_CONTRACTS[source_id]} for source_id in source_ids
    ]
    nodes: list[dict[str, Any]] = []
    for index, experiment_id in enumerate(experiment_ids, start=1):
        inputs = list(EXPERIMENT_SOURCES[experiment_id])
        nodes.append(
            {
                "id": f"N{index}_{experiment_id.split('_', 1)[0]}",
                "type": _node_type(experiment_id),
                "depends_on": [],
                "inputs": inputs,
                "outputs": [f"artifacts/{experiment_id}_metrics.json"],
                "tool": f"registered:{experiment_id}",
                "seed": 0,
                "budget": {"wall_seconds": 30, "tokens": 0},
                "success_criteria": _success_criteria(experiment_id),
                "failure_strategy": "retain the immutable failed node and block its claim",
                "status": "ready",
                "split_strategy": _split_strategy(experiment_id),
            }
        )
    return {
        "schema_version": "b3-research-plan-v2",
        "research_question": str(case["research_question"]),
        "claim_boundary": (
            "Retrospective solar-cycle constraints only; this is not an official "
            "forecast and does not prove the origin of the solar cycle."
        ),
        "data_contracts": contracts,
        "task_graph": nodes,
        "primary_metrics": ["registered status and finite diagnostics"],
        "counter_evidence_paths": ["E7_negative_controls_and_placebos"],
        "stop_rules": ["stop on failed safety, leakage, accounting, or wall-budget gate"],
    }


def _case_shape_errors(case: Any, index: int) -> list[str]:
    if not isinstance(case, dict):
        return [f"cases[{index}] must be an object"]
    errors: list[str] = []
    for field in (
        "id",
        "agent",
        "task_type",
        "research_question",
        "expected_behavior",
    ):
        if not isinstance(case.get(field), str) or not case[field].strip():
            errors.append(f"cases[{index}].{field} must be a non-empty string")
    experiments = case.get("expected_experiments")
    if not isinstance(experiments, list) or not experiments:
        errors.append(f"cases[{index}].expected_experiments must be a non-empty list")
    elif len(experiments) != len(set(experiments)):
        errors.append(f"cases[{index}].expected_experiments contains duplicates")
    return errors


def build_report(root: Path = ROOT) -> dict[str, Any]:
    project = Path(root).resolve()
    errors: list[str] = []
    try:
        suite = _read_json(project / CASE_PATH)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        suite = {}
        errors.append(f"cannot read diversity suite: {exc}")
    cases = suite.get("cases", [])
    if suite.get("schema_version") != "b3-question-diversity-suite-v1":
        errors.append("unexpected diversity suite schema_version")
    if not isinstance(cases, list):
        errors.append("diversity suite cases must be a list")
        cases = []
    if len(cases) != 9:
        errors.append(f"diversity suite must contain exactly 9 cases, found {len(cases)}")
    for index, case in enumerate(cases):
        errors.extend(_case_shape_errors(case, index))

    case_ids = [str(case.get("id", "")) for case in cases if isinstance(case, dict)]
    questions = [
        str(case.get("research_question", "")).strip()
        for case in cases
        if isinstance(case, dict)
    ]
    agents = [str(case.get("agent", "")) for case in cases if isinstance(case, dict)]
    task_types = [
        str(case.get("task_type", "")) for case in cases if isinstance(case, dict)
    ]
    expected_experiment_ids = {
        str(experiment_id)
        for case in cases
        if isinstance(case, dict) and isinstance(case.get("expected_experiments"), list)
        for experiment_id in case["expected_experiments"]
    }
    if len(case_ids) != len(set(case_ids)):
        errors.append("case ids must be unique")
    normalized_questions = [" ".join(question.split()).casefold() for question in questions]
    if len(normalized_questions) != len(set(normalized_questions)):
        errors.append("research questions must be unique")
    if len(task_types) != len(set(task_types)):
        errors.append("task types must be unique")
    if set(agents) != set(EXPECTED_AGENTS):
        errors.append("all three agent roles must be represented")
    registered_ids = set(REGISTERED_EXPERIMENTS)
    if expected_experiment_ids != registered_ids:
        missing = sorted(registered_ids - expected_experiment_ids)
        extra = sorted(expected_experiment_ids - registered_ids)
        errors.append(f"E0-E8 coverage mismatch; missing={missing}, extra={extra}")

    try:
        catalog = _read_json(project / CATALOG_PATH)
        catalog_ids = {
            str(item.get("id"))
            for item in catalog.get("experiments", [])
            if isinstance(item, dict)
        }
        if catalog_ids != registered_ids:
            errors.append("experiment catalog and Python registry do not match")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        errors.append(f"cannot read experiment catalog: {exc}")

    prompt_report: dict[str, Any] = {"path": PROMPT_PATH.as_posix(), "passed": False}
    try:
        prompt = (project / PROMPT_PATH).read_text(encoding="utf-8")
        prompt_errors: list[str] = []
        for marker in ("`${1}`", "不得初始化运行", "补充一个有边界的研究问题"):
            if marker not in prompt:
                prompt_errors.append(f"missing marker: {marker}")
        if "${1:-" in prompt:
            prompt_errors.append("silent default research question is still present")
        if (project / DEPRECATED_ALIAS_PATH).exists():
            prompt_errors.append("deprecated b3-agent-run alias is still present")
        prompt_report = {
            "path": PROMPT_PATH.as_posix(),
            "passed": not prompt_errors,
            "parameterized": "`${1}`" in prompt,
            "silent_default_absent": "${1:-" not in prompt,
            "missing_question_stops": "不得初始化运行" in prompt,
            "deprecated_alias_absent": not (project / DEPRECATED_ALIAS_PATH).exists(),
            "errors": prompt_errors,
        }
        errors.extend(prompt_errors)
    except (OSError, UnicodeError) as exc:
        errors.append(f"cannot read canonical prompt: {exc}")

    agent_reports: list[dict[str, Any]] = []
    for agent, relative in EXPECTED_AGENTS.items():
        agent_errors: list[str] = []
        try:
            text = (project / relative).read_text(encoding="utf-8")
            for marker in (f"name: {agent}", "model: b3-default"):
                if marker not in text:
                    agent_errors.append(f"missing marker: {marker}")
            if any(question and question in text for question in questions):
                agent_errors.append("one diversity-suite question is hard-coded in the agent")
        except (OSError, UnicodeError) as exc:
            agent_errors.append(str(exc))
        agent_reports.append(
            {
                "agent": agent,
                "path": relative.as_posix(),
                "passed": not agent_errors,
                "errors": agent_errors,
            }
        )
        errors.extend(f"{agent}: {item}" for item in agent_errors)

    case_reports: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict) or _case_shape_errors(case, 0):
            continue
        case_errors: list[str] = []
        try:
            draft = _plan_draft(case)
            with tempfile.TemporaryDirectory() as tmp:
                store = RunStore(Path(tmp))
                run_id = str(case["id"])
                run = store.create_run(str(case["research_question"]), run_id=run_id)
                plan = submit_research_plan_draft(store, run_id, draft)
                validate_research_plan(plan)
                if plan["research_question"] != run["task"]:
                    case_errors.append("submitted plan question is not task-bound")
                if run.get("research_question_binding") != "exact":
                    case_errors.append("run does not require exact question binding")
                if run.get("task_sha256") != canonical_json_sha256(run["task"]):
                    case_errors.append("run task hash is invalid")
        except Exception as exc:  # retain a per-case diagnostic in the proof
            plan = {}
            case_errors.append(f"{type(exc).__name__}: {exc}")
        case_reports.append(
            {
                "id": case["id"],
                "agent": case["agent"],
                "task_type": case["task_type"],
                "question_sha256": canonical_json_sha256(case["research_question"]),
                "expected_experiments": list(case["expected_experiments"]),
                "task_bound": not case_errors,
                "plan_contract_valid": not case_errors,
                "plan_artifact_hash_present": isinstance(
                    plan.get("artifact_sha256"), str
                ),
                "passed": not case_errors,
                "errors": case_errors,
            }
        )
        errors.extend(f"{case['id']}: {item}" for item in case_errors)

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utc_now(),
        "passed": not errors,
        "mode": "offline_contract",
        "proof_scope": (
            "Parameterized prompt, exact task-to-plan binding, ResearchPlan 1.0 validation, "
            "role coverage, and E0-E8 routing coverage only."
        ),
        "fixture_input": True,
        "model_invoked": False,
        "qwen_live_quality_evaluated": False,
        "case_count": len(cases),
        "unique_question_count": len(set(normalized_questions)),
        "task_type_count": len(set(task_types)),
        "agent_coverage": {
            agent: agents.count(agent) for agent in sorted(EXPECTED_AGENTS)
        },
        "experiment_coverage": sorted(expected_experiment_ids),
        "prompt": prompt_report,
        "agents": agent_reports,
        "cases": case_reports,
        "errors": errors,
    }
    return report


def stable_report(report: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in report.items() if key != "generated_at"}


def proof_matches_report(stored: dict[str, Any], current: dict[str, Any]) -> bool:
    return stable_report(stored) == stable_report(current)


def _markdown(report: dict[str, Any]) -> str:
    rows = "\n".join(
        "| {id} | {agent} | {experiments} | {status} |".format(
            id=item["id"],
            agent=item["agent"],
            experiments=", ".join(item["expected_experiments"]),
            status="passed" if item["passed"] else "failed",
        )
        for item in report["cases"]
    )
    return f"""# 三 Agent 多问题合同覆盖证明

- 状态：`{'passed' if report['passed'] else 'failed'}`
- 模式：`offline_contract`
- 不同问题：{report['unique_question_count']}/{report['case_count']}
- 角色覆盖：Planner={report['agent_coverage'].get('b3-research-planner', 0)}，Experiment={report['agent_coverage'].get('b3-experiment', 0)}，Hypothesis={report['agent_coverage'].get('b3-hypothesis', 0)}
- 实验覆盖：{', '.join(report['experiment_coverage'])}
- 模型调用：`false`

该证明只验证参数化入口、缺题停止、任务—计划精确绑定、ResearchPlan 1.0 合同与 E0–E8 路由覆盖。它**不评估 Qwen 对这些问题的 live 回答质量**，也不替代 12 案例 × 3 重复的正式 live proof。

| 案例 | 角色 | 预期实验 | 合同结果 |
|---|---|---|---|
{rows}

错误：{json.dumps(report['errors'], ensure_ascii=False)}
"""


def write_proof(report: dict[str, Any], root: Path = ROOT) -> tuple[Path, Path]:
    project = Path(root).resolve()
    json_path = project / PROOF_JSON_PATH
    md_path = project / PROOF_MD_PATH
    _atomic_write_text(
        json_path,
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
    )
    _atomic_write_text(md_path, _markdown(report))
    return json_path, md_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--write-proof", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_report(args.root)
    if args.write_proof:
        write_proof(report, args.root)
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
