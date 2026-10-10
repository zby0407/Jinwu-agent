"""Task-bound phase-comparison adapter; receipts never grant scientific approval."""

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import uuid

from jw import cycle_phase as computation
from jw.workspaces import resolve_scoped_path


def run_adapter(
    monthly, smoothed, boundaries, a_metrics, a_parameters, parameters, config
):
    from .solar_feature import (
        _validated_task_metadata,
        _resolve_eligible_dataset_path,
        _resolve_eligible_data_path,
        _eligible_input_records,
        _atomic_write_json,
        _error_json,
        _to_json,
    )
    from jw.research_protocols import detect_analysis_protocol

    audit = None
    attempt = uuid.uuid4().hex
    try:
        root, task_id, task = _validated_task_metadata(config)
        audit = resolve_scoped_path(
            f"receipts/attempts/cycle-phase-{attempt}.json", config
        )
        _atomic_write_json(
            audit, {"status": "started", "attempt_id": attempt, "task_id": task_id}
        )
        if (
            detect_analysis_protocol(str(task.get("research_question", "")))
            != computation.PROTOCOL
        ):
            raise ValueError("task does not request phase comparison")
        resolved = {}
        for key, path, dataset in [
            ("monthly", monthly, "silso-monthly-total-v2"),
            ("smoothed", smoothed, "silso-monthly-smoothed-v2"),
            ("boundaries", boundaries, "silso-cycle-extrema-v2"),
        ]:
            resolved[key] = _resolve_eligible_dataset_path(path, dataset, config)
        eligible = {r["path"]: r for r in _eligible_input_records(config)}
        for key, ref in [
            ("a_metrics", a_metrics),
            ("a_parameters", a_parameters),
            ("parameters", parameters),
        ]:
            path = _resolve_eligible_data_path(ref, config)
            record = eligible[ref]
            if record["source_group"] != "inputs":
                raise ValueError(
                    "A baseline and parameters must be task-local bound inputs"
                )
            resolved[key] = path, record
        work = resolve_scoped_path(f"work/cycle_phase/{attempt}", config)
        inputs = work / "inputs"
        inputs.mkdir(parents=True, exist_ok=False)
        records = []
        for key in sorted(computation.INPUT_KEYS):
            path, rec = resolved[key]
            relative = f"{key}{path.suffix}"
            shutil.copy2(path, inputs / relative)
            records.append(
                {
                    "key": key,
                    "path": relative,
                    "sha256": rec["sha256"],
                    "bytes": rec["bytes"],
                    "bound_input": rec,
                }
            )
            if rec.get("provenance_ref"):
                source = resolve_scoped_path(
                    rec["provenance_ref"],
                    config,
                    allow_project=rec["source_group"] == "project_inputs",
                )
                shutil.copy2(source, inputs / (relative + ".provenance.json"))
        computation.core.save_json(
            inputs / "input_manifest.json",
            {"schema": "cycle-phase-inputs-v1", "files": records},
        )
        shutil.copy2(resolved["parameters"][0], work / "parameters.json")
        run_id = (config or {}).get("configurable", {}).get("run_id")
        output = resolve_scoped_path(f"outputs/cycle_phase/{attempt}", config)
        info = computation.run(
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
        for path, rec in resolved.values():
            if computation.core.digest(path) != rec["sha256"]:
                raise ValueError("bound source changed during calculation")
        outputs = [
            {
                "path": p.relative_to(root).as_posix(),
                "sha256": computation.core.digest(p),
            }
            for p in sorted(output.rglob("*"))
            if p.is_file() and "inputs" not in p.relative_to(output).parts
        ]
        receipt = {
            "schema_version": "solar-cycle-phase-receipt-v1",
            "receipt_type": "silso_cycle_phase",
            "analysis_protocol": computation.PROTOCOL,
            "producer": "solar-data",
            "task_id": task_id,
            "attempt_id": attempt,
            "status": "verified",
            "review_status": "not_reviewed",
            "inputs": [rec for _, rec in resolved.values()],
            "outputs": outputs,
            "calculation_status": info["status"],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        receipt_path = resolve_scoped_path(
            "receipts/datasets/silso_cycle_phase.json", config
        )
        _atomic_write_json(receipt_path, receipt)
        _atomic_write_json(audit, {**receipt, "status": "computation_completed"})
        return _to_json(
            {
                "status": "verified",
                "review_status": "not_reviewed",
                "analysis_protocol": computation.PROTOCOL,
                "artifact_refs": [rec["path"] for rec in outputs],
                "receipt_refs": [receipt_path.relative_to(root).as_posix()],
                "note": "Normal independent Data review required; this receipt is not scientific approval.",
            }
        )
    except Exception as exc:
        if audit is not None:
            _atomic_write_json(
                audit,
                {
                    "status": "failed",
                    "attempt_id": attempt,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        return _error_json("run_silso_cycle_phase", exc)


def receipt_defect(root, task_id, manifest):
    """Require bound immutable inputs and coherent outputs in the reviewed snapshot."""
    import csv
    import math

    root = Path(root).resolve()

    def read(ref, expected=None):
        path = (root / ref).resolve()
        if ref not in manifest or not path.is_relative_to(root) or not path.is_file():
            raise ValueError("missing, escaped or unreviewed phase artifact")
        sha = computation.core.digest(path)
        if sha != manifest[ref].get("sha256") or expected and sha != expected:
            raise ValueError("phase artifact changed or receipt hash mismatch")
        return path

    try:
        receipt = json.loads(
            read("receipts/datasets/silso_cycle_phase.json").read_text(encoding="utf-8")
        )
        expected = {
            "schema_version": "solar-cycle-phase-receipt-v1",
            "receipt_type": "silso_cycle_phase",
            "analysis_protocol": computation.PROTOCOL,
            "task_id": task_id,
            "producer": "solar-data",
            "status": "verified",
            "calculation_status": "computed_not_scientifically_reviewed",
        }
        if any(receipt.get(k) != v for k, v in expected.items()):
            return "invalid current-task phase receipt"
        bound = json.loads((root / "input_manifest.json").read_text(encoding="utf-8"))
        identities = {
            (r.get("path"), r.get("sha256"), r.get("bytes"))
            for k in ("inputs", "project_inputs")
            for r in bound.get(k, [])
        }
        if len(receipt["inputs"]) != 6 or any(
            (r["path"], r["sha256"], r["bytes"]) not in identities
            for r in receipt["inputs"]
        ):
            return "unbound phase inputs"
        files = {
            Path(r["path"]).name: read(r["path"], r["sha256"])
            for r in receipt["outputs"]
        }
        required = {
            "phase_parameters.json",
            "readiness.json",
            "phase_curves.csv",
            "reference_curve.csv",
            "background_loo.csv",
            "target_comparisons.csv",
            "pairwise_distances.csv",
            "cycle_shape_metrics.csv",
            "hypothesis_Q2.json",
            "B_phase_report.md",
            "phase_absolute.png",
            "phase_normalized.png",
            "run_manifest.json",
            "analyze_cycle_phase.py",
            "cycle_metrics.py",
        }
        if not required <= files.keys():
            return "missing required phase deliverables"
        params = json.loads(files["phase_parameters.json"].read_text(encoding="utf-8"))
        computation.validate_parameters(params)
        run = json.loads(files["run_manifest.json"].read_text(encoding="utf-8"))
        if (
            run.get("check_only") is not False
            or run.get("status") != "computed_not_scientifically_reviewed"
        ):
            return "input preflight is not phase computation"
        param_records = [
            r for r in receipt["inputs"] if r["sha256"] == run.get("parameter_sha256")
        ]
        if len(param_records) != 1:
            return "phase parameters not bound"
        original = read(param_records[0]["path"], param_records[0]["sha256"])
        if json.loads(original.read_text(encoding="utf-8-sig")) != params:
            return "phase parameter substitution"

        def table(name):
            with files[name].open(encoding="utf-8", newline="") as stream:
                return list(csv.DictReader(stream))

        curves = table("phase_curves.csv")
        n = params["grid_intervals"] + 1
        if len(curves) != len(params["cycles"]) * n:
            return "wrong phase grid row count"
        for i, c in enumerate(params["cycles"]):
            for j, row in enumerate(curves[i * n : (i + 1) * n]):
                if int(row["cycle"]) != c or not math.isclose(
                    float(row["phase"]), j / (n - 1), abs_tol=1e-12
                ):
                    return "phase grid mismatch"
                if any(
                    not math.isfinite(float(row[k])) or float(row[k]) < 0
                    for k in ("absolute_smoothed_sn", "peak_normalized")
                ):
                    return "invalid phase value"
        background = table("background_loo.csv")
        targets = table("target_comparisons.csv")
        if [int(r["cycle"]) for r in background] != params["references"] or [
            int(r["cycle"]) for r in targets
        ] != params["targets"]:
            return "reference or target scope mismatch"
        maximum = max(float(r["normalized_rms"]) for r in background)
        for row in targets:
            distance = float(row["normalized_rms"])
            if (
                not math.isfinite(distance)
                or distance < 0
                or not math.isfinite(maximum)
                or maximum < 0
            ):
                return "invalid phase distance"
            tolerance = params["comparison_tolerance"]["absolute"] + params[
                "comparison_tolerance"
            ]["relative"] * abs(maximum)
            expected_status = (
                "outside_reference_background"
                if distance - maximum > tolerance
                else "not_outside_reference_background"
            )
            if row["status"] != expected_status or not math.isclose(
                float(row["background_max_rms"]), maximum
            ):
                return "phase conclusion does not follow result"
        if (
            len(table("pairwise_distances.csv"))
            != len(params["cycles"]) * (len(params["cycles"]) - 1) // 2
        ):
            return "incomplete pairwise distances"
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return str(exc)
    return None
