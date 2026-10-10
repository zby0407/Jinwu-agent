"""Task-isolated adapter for the independently tested descriptive calculator."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import runpy
import shutil
import uuid

from jw.workspaces import resolve_scoped_path


def receipt_defect(root, task_id, manifest_by_ref):
    """Validate a review snapshot and live files; never grant a review verdict."""
    import csv
    import math

    root = Path(root).resolve()

    def read(ref):
        if ref not in manifest_by_ref or Path(ref).is_absolute():
            raise ValueError("artifact not in reviewed source manifest")
        candidate = (root / ref).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            raise ValueError("missing or escaped artifact")
        actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if actual != manifest_by_ref[ref].get("sha256"):
            raise ValueError("artifact changed since review snapshot")
        return candidate

    try:
        receipt = json.loads(
            read("receipts/datasets/silso_cycle_descriptive.json").read_text(
                encoding="utf-8"
            )
        )
        if any(
            receipt.get(k) != v
            for k, v in {
                "schema_version": "solar-cycle-descriptive-receipt-v1",
                "receipt_type": "silso_cycle_descriptive",
                "status": "verified",
                "producer": "solar-data",
                "task_id": task_id,
                "analysis_protocol": "silso_cycle_descriptive_v1",
            }.items()
        ):
            return "invalid current-task descriptive receipt"
        bound = json.loads((root / "input_manifest.json").read_text(encoding="utf-8"))
        identities = {
            (r.get("path"), r.get("sha256"), r.get("bytes"))
            for group in ("inputs", "project_inputs")
            for r in bound.get(group, [])
        }
        for rec in receipt["inputs"] + [receipt["parameter_input"]]:
            if (rec["path"], rec["sha256"], rec["bytes"]) not in identities:
                return "receipt input is not bound to this task"
        outputs = {}
        for item in receipt["outputs"]:
            path = read(item["path"])
            if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                return "receipt output hash mismatch"
            outputs[path.name] = path
        required = {
            "cycle_metrics.csv",
            "contrasts.csv",
            "clean_monthly.csv",
            "data_quality.json",
            "analysis_parameters.json",
            "sc3_sc6_quantitative_report.md",
            "run_manifest.json",
            "hypothesis_cards.json",
            "cycle_metrics.png",
            "monthly_history.png",
            "analyze_cycle_metrics.py",
        }
        if not required <= outputs.keys():
            return "missing required descriptive deliverables"
        params = json.loads(
            outputs["analysis_parameters.json"].read_text(encoding="utf-8")
        )
        # The canonical JSON may normalize whitespace; compare the bound input
        # semantics rather than pretend equal JSON has equal byte hashes.
        param_ref = receipt["parameter_input"]["path"]
        if param_ref.startswith("/project/"):
            return "analysis parameters must be task-local"
        original = read(param_ref)
        if json.loads(original.read_text(encoding="utf-8-sig")) != params:
            return "parameters differ from the bound input"
        rows = list(csv.DictReader(outputs["cycle_metrics.csv"].open(encoding="utf-8")))
        if [int(r["cycle"]) for r in rows] != params["cycles"] or receipt[
            "row_count"
        ] != len(rows):
            return "cycle scope/row count mismatch"
        for row in rows:
            n = int(row["months"])
            if n <= 0 or int(row["rise_months"]) + int(row["decline_months"]) != n:
                return "invalid cycle duration decomposition"
            if not math.isclose(
                float(row["monthly_mean"]) * n,
                float(row["activity_sn_month"]),
                rel_tol=1e-12,
            ):
                return "mean and integrated activity disagree"
            for threshold in params["thresholds"]:
                key = f"low_lt_{threshold:g}"
                count = int(row[key + "_months"])
                if not 0 <= int(row[key + "_longest_months"]) <= count <= n:
                    return "invalid low-activity counts"
                if not math.isclose(
                    float(row[key + "_fraction"]), count / n, abs_tol=1e-12
                ):
                    return "low-activity fraction disagrees with count"
        run = json.loads(outputs["run_manifest.json"].read_text(encoding="utf-8"))
        if run.get("status") != "computed_not_scientifically_reviewed":
            return "calculation did not complete"
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return str(exc)
    return None


def run_adapter(monthly_path, smoothed_path, extrema_path, parameters_path, config):
    from .solar_feature import (
        _atomic_write_json,
        _eligible_input_records,
        _error_json,
        _file_sha256,
        _resolve_eligible_data_path,
        _resolve_eligible_dataset_path,
        _to_json,
        _validated_task_metadata,
    )
    from jw.research_protocols import (
        SILSO_CYCLE_DESCRIPTIVE_PROTOCOL,
        detect_analysis_protocol,
    )

    attempt = uuid.uuid4().hex
    audit_path = None
    try:
        root, task_id, task = _validated_task_metadata(config)
        audit_path = resolve_scoped_path(
            f"receipts/attempts/cycle-descriptive-{attempt}.json", config
        )
        _atomic_write_json(
            audit_path,
            {
                "status": "started",
                "task_id": task_id,
                "created_at": datetime.now(UTC).isoformat(),
            },
        )
        if (
            detect_analysis_protocol(str(task.get("research_question", "")))
            != SILSO_CYCLE_DESCRIPTIVE_PROTOCOL
        ):
            raise ValueError(
                "current task does not request the descriptive cycle protocol"
            )
        sources = [
            _resolve_eligible_dataset_path(path, dataset, config)
            for path, dataset in [
                (monthly_path, "silso-monthly-total-v2"),
                (smoothed_path, "silso-monthly-smoothed-v2"),
                (extrema_path, "silso-cycle-extrema-v2"),
            ]
        ]
        parameter_file = _resolve_eligible_data_path(parameters_path, config)
        parameter_record = next(
            r for r in _eligible_input_records(config) if r["path"] == parameters_path
        )
        if parameter_record["source_group"] != "inputs":
            raise ValueError("analysis parameters must be task-local")
        if parameter_file.suffix.lower() != ".json":
            raise ValueError("parameters must be a hash-bound JSON input")
        params = json.loads(parameter_file.read_text(encoding="utf-8-sig"))
        script = Path(__file__).resolve().parents[1] / "cycle_metrics.py"
        implementation = runpy.run_path(str(script))
        implementation["validate_parameters"](params)
        # Bound input identity + code identity identifies the calculation, but a
        # new attempt directory preserves both failures and successful retries.
        input_key = hashlib.sha256(
            json.dumps(
                [r["sha256"] for _, r in sources]
                + [parameter_record["sha256"], _file_sha256(script)]
            ).encode()
        ).hexdigest()
        work = resolve_scoped_path(f"work/cycle_descriptive/{attempt}", config)
        work.mkdir(parents=True, exist_ok=False)
        staged = work / "inputs"
        (staged / "metadata").mkdir(parents=True)
        records = []
        for source, record in sources:
            relative = f"data/{source.name}"
            dest = staged / relative
            dest.parent.mkdir(exist_ok=True)
            shutil.copy2(source, dest)
            if _file_sha256(dest) != record["sha256"]:
                raise ValueError("input changed during staging")
            records.append(
                {
                    **record,
                    "package_path": relative,
                    "source_local_path": str(source),
                    "version": "SILSO V2.0",
                    "source": "current task manifest",
                }
            )
            provenance_ref = record.get("provenance_ref")
            if provenance_ref:
                # Provenance is read-only metadata; use only its declared path.
                provenance = resolve_scoped_path(
                    str(provenance_ref),
                    config,
                    allow_project=record["source_group"] == "project_inputs",
                )
                shutil.copy2(provenance, dest.with_name(dest.name + ".provenance.json"))
        implementation["save_json"](
            staged / "metadata/silso_snapshot_manifest.json",
            {"schema": "task-bound-cycle-inputs-v1", "files": records},
        )
        shutil.copy2(parameter_file, work / "parameters.json")
        output = resolve_scoped_path(f"outputs/cycle_descriptive/{attempt}", config)
        actual_run_id = (config or {}).get("configurable", {}).get("run_id")
        context = {
            "executor": "jw_deterministic_tool",
            "agent_run": bool(actual_run_id),
            "thread_id": task_id,
            "run_id": str(actual_run_id) if actual_run_id else None,
            "execution_note": "Actual tool invocation; operator preflight invocations must be labelled separately in the external audit.",
        }
        rows = implementation["run"](
            staged, work / "parameters.json", output, execution_context=context
        )
        if _file_sha256(parameter_file) != parameter_record["sha256"] or any(
            _file_sha256(path) != rec["sha256"] for path, rec in sources
        ):
            raise ValueError("bound input changed during calculation")
        outputs = [
            {"path": p.relative_to(root).as_posix(), "sha256": _file_sha256(p)}
            for p in sorted(output.rglob("*"))
            if p.is_file()
            and not p.relative_to(output).as_posix().startswith("inputs/")
        ]
        receipt = {
            "schema_version": "solar-cycle-descriptive-receipt-v1",
            "receipt_type": "silso_cycle_descriptive",
            "status": "verified",
            "analysis_protocol": SILSO_CYCLE_DESCRIPTIVE_PROTOCOL,
            "producer": "solar-data",
            "task_id": task_id,
            "attempt_id": attempt,
            "calculation_identity": input_key,
            "row_count": len(rows),
            "cycle_numbers": params["cycles"],
            "inputs": [r for _, r in sources],
            "parameter_input": parameter_record,
            "outputs": outputs,
            "review_status": "not_reviewed",
            "created_at": datetime.now(UTC).isoformat(),
        }
        receipt_path = resolve_scoped_path(
            "receipts/datasets/silso_cycle_descriptive.json", config
        )
        _atomic_write_json(receipt_path, receipt)
        _atomic_write_json(audit_path, {**receipt, "status": "computation_completed"})
        return _to_json(
            {
                "status": "verified",
                "analysis_protocol": SILSO_CYCLE_DESCRIPTIVE_PROTOCOL,
                "row_count": len(rows),
                "cycle_numbers": params["cycles"],
                "artifact_refs": [r["path"] for r in outputs],
                "receipt_refs": [receipt_path.relative_to(root).as_posix()],
                "review_status": "not_reviewed",
                "note": "Computation verified; normal independent Data review is still required.",
            }
        )
    except Exception as exc:
        if audit_path is not None:
            _atomic_write_json(
                audit_path,
                {
                    "status": "failed",
                    "attempt_id": attempt,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "created_at": datetime.now(UTC).isoformat(),
                },
            )
        return _error_json("run_silso_cycle_descriptive", exc)
