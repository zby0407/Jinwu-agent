"""Bound C1/C2 tools and deterministic review integrity (never scientific approval)."""

from datetime import datetime, timezone
import csv
import json
from pathlib import Path
import re
import shutil
import uuid

from jw import cross_series, cycle_sensitivity, cycle_associations
from jw.workspaces import resolve_scoped_path

MODULES = {
    cross_series.PROTOCOL: cross_series,
    cycle_sensitivity.PROTOCOL: cycle_sensitivity,
    cycle_associations.PROTOCOL: cycle_associations,
}
NAMES = {
    cross_series.PROTOCOL: "c1_annual",
    cycle_sensitivity.PROTOCOL: "c2_sensitivity",
    cycle_associations.PROTOCOL: "d_associations",
}
IDS = {
    "monthly": "silso-monthly-total-v2",
    "smoothed": "silso-monthly-smoothed-v2",
    "boundaries": "silso-cycle-extrema-v2",
}


def rows(path):
    with Path(path).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def batch_from_question(question):
    matches = re.findall(r"\[C2_BATCH=(C2[abc])\]", question)
    if len(matches) != 1:
        raise ValueError(
            "C2 requires exactly one explicit [C2_BATCH=C2a|C2b|C2c] marker"
        )
    return matches[0]


def d_batch_from_question(question):
    matches = re.findall(r"\[D_BATCH=(D[12])\]", question)
    if len(matches) != 1:
        raise ValueError("D requires exactly one explicit D_BATCH=D1|D2")
    return matches[0]


def run_adapter(protocol, paths, config):
    from jw.research_protocols import detect_analysis_protocol
    from .solar_feature import (
        _validated_task_metadata,
        _resolve_eligible_dataset_path,
        _resolve_eligible_data_path,
        _eligible_input_records,
        _atomic_write_json,
        _error_json,
        _to_json,
    )

    attempt = uuid.uuid4().hex
    audit = None
    try:
        module = MODULES[protocol]
        name = NAMES[protocol]
        root, task_id, task = _validated_task_metadata(config)
        audit = resolve_scoped_path(f"receipts/attempts/{name}-{attempt}.json", config)
        _atomic_write_json(
            audit,
            {
                "status": "started",
                "attempt_id": attempt,
                "task_id": task_id,
                "protocol": protocol,
            },
        )
        question = str(task.get("research_question", ""))
        if detect_analysis_protocol(question) != protocol:
            raise ValueError("task protocol does not authorize this C tool")
        expected = (
            {
                "monthly",
                "smoothed",
                "boundaries",
                "a_metrics",
                "a_parameters",
                "gnbb2",
                "parameters",
            }
            if module is cross_series
            else module.INPUT_KEYS | {"parameters"}
        )
        if set(paths) != expected:
            raise ValueError("C input key set mismatch")
        eligible = {r["path"]: r for r in _eligible_input_records(config)}
        resolved = {}
        for key, ref in paths.items():
            if key in IDS:
                path, rec = _resolve_eligible_dataset_path(ref, IDS[key], config)
            else:
                path = _resolve_eligible_data_path(ref, config)
                rec = eligible[ref]
                if rec["source_group"] != "inputs" or rec.get("role") != "user_input":
                    raise ValueError(
                        "GNbb2, baseline and parameters must be explicitly bound local user inputs"
                    )
            resolved[key] = path, rec
        if len({path for path, _ in resolved.values()}) != len(resolved):
            raise ValueError("one bound path cannot impersonate multiple C inputs")
        parameter = json.loads(
            resolved["parameters"][0].read_text(encoding="utf-8-sig")
        )
        module.validate_parameters(parameter)
        if module is cycle_sensitivity and parameter["batch"] != batch_from_question(
            question
        ):
            raise ValueError("task C2 batch does not match bound parameters")
        if module is cycle_associations and parameter["batch"] != d_batch_from_question(
            question
        ):
            raise ValueError("task D batch does not match bound parameters")
        work = resolve_scoped_path(f"work/{name}/{attempt}", config)
        inputs = work / "inputs"
        inputs.mkdir(parents=True, exist_ok=False)
        records = []
        for key in sorted(expected - {"parameters"}):
            path, rec = resolved[key]
            target = key + path.suffix
            shutil.copy2(path, inputs / target)
            records.append(
                {
                    "key": key,
                    "path": target,
                    "sha256": rec["sha256"],
                    "bytes": rec["bytes"],
                    "bound_input": rec,
                }
            )
            if rec.get("provenance_ref"):
                provenance = resolve_scoped_path(
                    rec["provenance_ref"],
                    config,
                    allow_project=rec["source_group"] == "project_inputs",
                )
                shutil.copy2(provenance, inputs / (target + ".provenance.json"))
        cross_series.save(
            inputs / "input_manifest.json",
            {
                "schema": "cross-series-inputs-v1"
                if module is cross_series
                else "cycle-sensitivity-inputs-v1",
                "files": records,
            },
        )
        shutil.copy2(resolved["parameters"][0], work / "parameters.json")
        run_id = (config or {}).get("configurable", {}).get("run_id")
        output = resolve_scoped_path(f"outputs/{name}/{attempt}", config)
        info = module.run(
            inputs,
            work / "parameters.json",
            output,
            execution_context={
                "executor": "jw_deterministic_tool",
                "agent_run": bool(run_id),
                "thread_id": task_id,
                "run_id": str(run_id) if run_id else None,
            },
        )
        if any(
            cross_series.digest(path) != rec["sha256"]
            for path, rec in resolved.values()
        ):
            raise ValueError("bound source changed during C execution")
        artifacts = [
            {
                "path": p.relative_to(root).as_posix(),
                "sha256": cross_series.digest(p),
                "bytes": p.stat().st_size,
            }
            for p in sorted(output.rglob("*"))
            if p.is_file()
        ]
        receipt = {
            "schema_version": "solar-c-stage-receipt-v1",
            "receipt_type": name,
            "analysis_protocol": protocol,
            "producer": "solar-data",
            "task_id": task_id,
            "attempt_id": attempt,
            "status": "verified",
            "review_status": "not_reviewed",
            "calculation_status": info["status"],
            "inputs": [{"input_key": key, **rec} for key, (_, rec) in resolved.items()],
            "output_dir": output.relative_to(root).as_posix(),
            "outputs": artifacts,
            "batch": parameter.get("batch"),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        receipt_path = resolve_scoped_path(f"receipts/datasets/{name}.json", config)
        _atomic_write_json(receipt_path, receipt)
        _atomic_write_json(audit, {**receipt, "status": "computation_completed"})
        if module is cross_series:
            key_results = {
                "primary_annual_mean_contrasts": [
                    r
                    for r in rows(output / "within_product_contrasts.csv")
                    if r["scenario"] == "calendar_days__whole_calendar_year"
                    and r["metric"] == "annual_mean"
                ],
                "primary_Q3": [
                    r
                    for r in rows(output / "Q3_direction_agreement.csv")
                    if r["scenario"] == "calendar_days__whole_calendar_year"
                ],
            }
        elif module is cycle_associations:
            key_results = {
                "batch": parameter["batch"],
                "baseline_associations": [
                    r
                    for r in rows(output / "association_statistics.csv")
                    if r["scenario"] == "baseline" and r["subset"] == "all"
                ],
            }
        else:
            key_results = {
                "batch": parameter["batch"],
                "Q2": rows(output / "scenario_targets.csv"),
            }
        return _to_json(
            {
                "status": "verified",
                "review_status": "not_reviewed",
                "analysis_protocol": protocol,
                "artifact_refs": [r["path"] for r in artifacts],
                "receipt_refs": [receipt_path.relative_to(root).as_posix()],
                "key_results": key_results,
                "note": "Actual table values, not scientific approval; independent Data review required.",
            }
        )
    except Exception as exc:
        if audit is not None:
            _atomic_write_json(
                audit,
                {
                    "status": "failed",
                    "attempt_id": attempt,
                    "protocol": protocol,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        return _error_json("run_c_stage", exc)


def receipt_defect(root, task_id, manifest, protocol):
    """Validate every referenced identity and recompute numeric tables read-only."""
    root = Path(root).resolve()
    try:
        module, name = MODULES[protocol], NAMES[protocol]

        def read(ref, sha=None):
            path = (root / ref).resolve()
            if (
                not path.is_relative_to(root)
                or ref not in manifest
                or not path.is_file()
            ):
                raise ValueError("missing, escaped or unreviewed C artifact")
            actual = cross_series.digest(path)
            if (
                actual != manifest[ref].get("sha256")
                or sha is not None
                and sha != actual
            ):
                raise ValueError("C artifact changed or receipt hash mismatch")
            return path

        receipt = json.loads(
            read(f"receipts/datasets/{name}.json").read_text(encoding="utf-8")
        )
        required = {
            "schema_version": "solar-c-stage-receipt-v1",
            "receipt_type": name,
            "analysis_protocol": protocol,
            "producer": "solar-data",
            "task_id": task_id,
            "status": "verified",
            "calculation_status": "computed_not_scientifically_reviewed",
            "review_status": "not_reviewed",
        }
        if any(receipt.get(key) != value for key, value in required.items()):
            raise ValueError("invalid current-task C receipt")
        question = json.loads((root / "task.json").read_text(encoding="utf-8"))[
            "research_question"
        ]
        from jw.research_protocols import detect_analysis_protocol

        if detect_analysis_protocol(question) != protocol:
            raise ValueError("wrong task protocol")
        bound = json.loads((root / "input_manifest.json").read_text(encoding="utf-8"))
        identities = {
            (r.get("path"), r.get("sha256"), r.get("bytes"))
            for k in ("inputs", "project_inputs")
            for r in bound.get(k, [])
        }
        by_key = {r["input_key"]: r for r in receipt["inputs"]}
        expected = (
            {
                "monthly",
                "smoothed",
                "boundaries",
                "a_metrics",
                "a_parameters",
                "gnbb2",
                "parameters",
            }
            if module is cross_series
            else module.INPUT_KEYS | {"parameters"}
        )
        if len(by_key) != len(receipt["inputs"]) or set(by_key) != expected:
            raise ValueError("missing or duplicate C input identities")
        for key, rec in by_key.items():
            if (rec["path"], rec["sha256"], rec["bytes"]) not in identities:
                raise ValueError("unbound C input")
            originals = [
                r
                for group in ("inputs", "project_inputs")
                for r in bound.get(group, [])
                if r.get("path") == rec["path"] and r.get("sha256") == rec["sha256"]
            ]
            if key in IDS and (
                rec.get("dataset_id") != IDS[key]
                or not any(r.get("dataset_id") == IDS[key] for r in originals)
            ):
                raise ValueError("C registered input dataset identity changed")
            if key not in IDS and (
                rec.get("source_group") != "inputs"
                or rec.get("role") != "user_input"
                or not any(
                    r.get("path") == rec["path"] and r.get("role") == "user_input"
                    for r in bound.get("inputs", [])
                )
            ):
                raise ValueError("C local input role changed")
            read(rec["path"], rec["sha256"])
        output_ref = receipt["output_dir"]
        if not output_ref.startswith(f"outputs/{name}/"):
            raise ValueError("wrong C output scope")
        files = {}
        for rec in receipt["outputs"]:
            path = read(rec["path"], rec["sha256"])
            if (
                not path.is_relative_to(root / output_ref)
                or path.stat().st_size != rec["bytes"]
            ):
                raise ValueError("invalid output scope/size")
            rel = path.relative_to(root / output_ref).as_posix()
            if rel in files:
                raise ValueError("duplicate C output")
            files[rel] = path
        param_name = (
            "C1_parameters.json"
            if module is cross_series
            else "D_parameters.json"
            if module is cycle_associations
            else "C2_parameters.json"
        )
        original = json.loads(
            read(by_key["parameters"]["path"]).read_text(encoding="utf-8-sig")
        )
        p = json.loads(files[param_name].read_text(encoding="utf-8-sig"))
        if (
            p != original
            or cross_series.digest(files[param_name]) != by_key["parameters"]["sha256"]
        ):
            raise ValueError("C parameter substitution")
        module.validate_parameters(p)
        info = json.loads(files["run_manifest.json"].read_text(encoding="utf-8"))
        if (
            info.get("check_only") is not False
            or info.get("status") != "computed_not_scientifically_reviewed"
            or info.get("parameter_sha256") != by_key["parameters"]["sha256"]
        ):
            raise ValueError("preflight is not formal C computation")
        for rec in info["artifacts"]:
            if (
                rec["path"] not in files
                or cross_series.digest(files[rec["path"]]) != rec["sha256"]
            ):
                raise ValueError("incomplete run manifest")
        # Ensure copied computation inputs are precisely the original bound files.
        input_dir = files["inputs/input_manifest.json"].parent
        inner = json.loads(
            files["inputs/input_manifest.json"].read_text(encoding="utf-8")
        )
        for rec in inner["files"]:
            if (
                rec["key"] not in by_key
                or rec["sha256"] != by_key[rec["key"]]["sha256"]
                or rec["bytes"] != by_key[rec["key"]]["bytes"]
            ):
                raise ValueError("substituted copied C input")
        if module is cross_series:
            monthly, gn, boundaries, years, assignments, _ = module.load_inputs(
                input_dir, p
            )
            expected_tables = module.calculate(monthly, gn, boundaries, years, p)
            expected_tables["calendar_membership_plan.csv"] = assignments
            required_files = {
                "C1_report.md",
                "hypothesis_Q3.json",
                "C1_annual_comparison.png",
                "readiness.json",
                "code/analyze_cross_series.py",
                "code/c_stage_figures.py",
            }
            card = json.loads(files["hypothesis_Q3.json"].read_text(encoding="utf-8"))
            if card["claims"] != expected_tables["Q3_direction_agreement.csv"]:
                raise ValueError("Q3 interpretation differs from results")
        elif module is cycle_associations:
            if (
                p["batch"] != d_batch_from_question(question)
                or receipt.get("batch") != p["batch"]
            ):
                raise ValueError("D batch substitution")
            loaded = module.load_inputs(input_dir, p)
            expected_tables = module.calculate(*loaded[:-1], p)
            required_files = {
                "D_report.md",
                "hypotheses_D.json",
                "D_association_scatter.png",
                "readiness.json",
                "code/cycle_associations.py",
                "code/cycle_metrics.py",
                "code/cycle_sensitivity.py",
                "code/cycle_phase.py",
            }
            if json.loads(
                files["hypotheses_D.json"].read_text("utf-8")
            ) != module.hypothesis_cards(expected_tables, p):
                raise ValueError("D interpretation differs from actual results")
        else:
            if (
                p["batch"] != batch_from_question(question)
                or receipt.get("batch") != p["batch"]
            ):
                raise ValueError("C2 batch substitution")
            monthly, smooth, bounds, a, bp, b, _ = module.load_inputs(input_dir, p)
            expected_tables = module.calculate(monthly, smooth, bounds, a, bp, b, p)
            required_files = {
                "C2_report.md",
                "hypotheses_C2.json",
                "C2_sensitivity_margins.png",
                "readiness.json",
                "code/analyze_cycle_sensitivity.py",
                "code/cycle_phase.py",
                "code/cycle_metrics.py",
                "code/c_stage_figures.py",
            }
            card = json.loads(files["hypotheses_C2.json"].read_text(encoding="utf-8"))
            if (
                card["Q1"] != expected_tables["scenario_Q1.csv"]
                or card["Q2"] != expected_tables["scenario_targets.csv"]
            ):
                raise ValueError("C2 interpretation differs from results")
        if not required_files <= files.keys():
            raise ValueError("incomplete C deliverables")
        for name, expected_rows in expected_tables.items():
            canonical = [
                {key: "" if value is None else str(value) for key, value in row.items()}
                for row in expected_rows
            ]
            if name not in files or rows(files[name]) != canonical:
                raise ValueError(
                    "C numeric results disagree with bound inputs: " + name
                )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return str(exc)
    return None
