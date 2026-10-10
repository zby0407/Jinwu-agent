"""Task-bound B1 flare calculation; never a scientific-review shortcut."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from jw.workspaces import workspace_root_from_config

from .registry import register_tool_bundle
from .solar_feature import (
    _atomic_write_json,
    _eligible_input_records,
    _file_sha256,
    _resolve_eligible_data_path,
    _to_json,
    _validated_task_metadata,
)

B1_INPUT_DATASET_IDS = {
    "magnetic": "b1-sharp-11158-v1",
    "events": "b1-goes-events-2011-v1",
    "xrs": "b1-goes15-xrs-201102-v1",
    "parameters": "b1-case-parameters-v1",
    "sources": "b1-case-sources-v1",
}


def _safe_task_path(root: Path, relative: str) -> Path:
    """Reject symlink/junction write redirections inside the current task."""

    candidate = root / relative
    cursor = root
    for part in Path(relative).parts:
        cursor /= part
        if cursor.is_symlink() or (
            hasattr(cursor, "is_junction") and cursor.is_junction()
        ):
            raise PermissionError("B1 task path cannot traverse a link")
    if not candidate.resolve().is_relative_to(root):
        raise PermissionError("B1 task path escaped the current task")
    return candidate


def _artifacts(root: Path, output_dir: Path) -> list[dict[str, object]]:
    if not output_dir.exists():
        return []
    result = []
    for path in sorted(output_dir.rglob("*")):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise PermissionError("B1 output contains a link")
        if path.is_file():
            if not path.resolve().is_relative_to(output_dir.resolve()):
                raise PermissionError("B1 output escaped its new output directory")
            result.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": _file_sha256(path),
                    "bytes": path.stat().st_size,
                }
            )
    return result


def _verified_result_summary(root: Path, output_dir: Path, audit: dict) -> dict:
    """Read only four hash-bound products from this verified attempt.

    This returns calculated observations, never hypotheses, acceptance or new
    file capabilities. Rejected-window numbers remain solely in the audit file.
    """
    if audit.get("status") != "verified":
        raise ValueError("B1 summary requires a verified current attempt")
    declared = {item["path"]: item for item in audit["outputs"]}
    bindings = []

    def read_product(name: str):
        relative = (output_dir / name).relative_to(root).as_posix()
        path = _safe_task_path(root, relative)
        record = declared.get(relative)
        if record is None or not path.is_file():
            raise PermissionError("B1 summary product is not declared by this attempt")
        content = path.read_bytes()
        actual = hashlib.sha256(content).hexdigest()
        if actual != record["sha256"] or len(content) != record["bytes"]:
            raise PermissionError("B1 summary product hash/size mismatch")
        bindings.append({"path": relative, "sha256": actual, "bytes": len(content)})
        return json.loads(content)

    result = read_product("receipt.json")
    windows = read_product("windows.json")
    screening = read_product("event_screening.json")
    sources = read_product("inputs/source_manifest.json")
    eligible = [row for row in windows if row["status"] == "eligible"]
    counts = {
        kind: sum(row["kind"] == kind for row in eligible)
        for kind in ("event", "control")
    }
    if counts != {
        "event": result["eligible_events"],
        "control": result["eligible_controls"],
    }:
        raise ValueError("B1 summary counts disagree with the computed receipt")
    from jw.b1_compute import parse_utc

    overlaps = []
    for index, left in enumerate(eligible):
        for right in eligible[index + 1 :]:
            start = max(
                parse_utc(left["window_start_utc"]),
                parse_utc(right["window_start_utc"]),
            )
            end = min(
                parse_utc(left["window_end_utc"]), parse_utc(right["window_end_utc"])
            )
            if end > start:
                overlaps.append(
                    {
                        "left_window_id": left["window_id"],
                        "right_window_id": right["window_id"],
                        "overlap_seconds": (end - start).total_seconds(),
                        "interpretation": "shared time support; not independent samples",
                    }
                )
    primary_refs = set()
    for value in sources.get("primary_inputs", {}).values():
        primary_refs.update(value if isinstance(value, list) else [value])
    source_rows = [item for item in sources["sources"] if item["path"] in primary_refs]
    # Synthetic/bounded fixtures may contain only canonical source entries.
    if not primary_refs:
        source_rows = [
            item for item in sources["sources"] if item.get("role") == "canonical_input"
        ]
    source_fields = (
        "source_id",
        "path",
        "title",
        "url",
        "final_url",
        "retrieved_at_utc",
        "sha256",
        "license_or_terms",
        "citation",
    )
    summary = {
        "schema_version": "b1-flare-result-summary-v1",
        "attempt_id": audit["attempt_id"],
        "task_id": audit["task_id"],
        "receipt_ref": f"receipts/datasets/b1-flare-{audit['attempt_id']}.json",
        "scientific_status": "not_reviewed",
        "execution_kind": "deterministic_tool_result_projection",
        "product_bindings": bindings,
        "eligible_events": counts["event"],
        "eligible_controls": counts["control"],
        "comparison_scope": result["comparison_scope"],
        "eligible_window_results": eligible,
        "all_window_screening": [
            {key: value for key, value in row.items() if key != "metrics"}
            for row in windows
        ],
        "catalog_event_screening": screening,
        "overlapping_eligible_windows": overlaps,
        "primary_data_sources": [
            {key: row[key] for key in source_fields if key in row}
            for row in source_rows
        ],
        "source_context": {
            key: sources[key]
            for key in (
                "semantic_decisions",
                "transport_limitations",
                "frozen_scope",
                "region_mapping",
            )
            if key in sources
        },
        "interpretation_limits": [
            "Only eligible_window_results may supply numerical case conclusions; rejected-window metrics are excluded.",
            "Counts describe windows in one active region, not independent replicate samples.",
            "A proxy increase is not a flare-specific precursor or a verified trigger mechanism.",
            "Scientific review is pending; these are deterministic calculations, not an acceptance verdict.",
        ],
    }
    # Fail honestly instead of silently truncating facts, sources or caveats.
    if len(json.dumps(summary, ensure_ascii=False)) > 50_000:
        raise ValueError(
            "B1 result summary exceeds the bounded 50000-character contract"
        )
    return summary


@tool(parse_docstring=True)
def run_b1_flare_case(
    magnetic_path: str,
    events_path: str,
    xrs_path: str,
    parameters_path: str,
    sources_path: str,
    config: RunnableConfig = None,
) -> str:
    """Calculate a bounded B1 flare case from five registered immutable inputs.

    This deterministic tool aligns UTC observations, checks quality, and
    compares preflare and control windows using the frozen parameter file.
    It writes a fresh calculation report with source citations and an audit
    receipt. A completed calculation is scientific_not_reviewed; independent
    evidence review is still required before any mechanism claim. Do not use
    monthly solar-cycle tools, forecasting tools, or arbitrary host paths.

    Args:
        magnetic_path: Exact eligible task path of the canonical magnetic CSV.
        events_path: Exact eligible task path of the flare-event CSV.
        xrs_path: Exact eligible task path of the canonical GOES XRS CSV.
        parameters_path: Exact eligible task path of b1-flare-case-v1 JSON.
        sources_path: Exact eligible task path of the source manifest JSON.

    Returns:
        JSON calculation status, output hashes, and per-attempt audit receipt.
    """

    attempt_id = f"{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}-{uuid4().hex[:12]}"
    requested = {
        "magnetic": magnetic_path,
        "events": events_path,
        "xrs": xrs_path,
        "parameters": parameters_path,
        "sources": sources_path,
    }
    audit: dict[str, object] = {
        "schema_version": "b1-flare-case-receipt-v1",
        "receipt_type": "b1_flare_case",
        "analysis_protocol": "b1-flare-case-v1",
        "tool": "run_b1_flare_case",
        "producer": "solar-data",
        "attempt_id": attempt_id,
        "status": "started",
        "calculation_status": "started",
        "scientific_status": "not_reviewed",
        "scientific_not_reviewed": True,
        "execution_kind": "deterministic_tool",
        "requested_inputs": requested,
        "inputs": [],
        "outputs": [],
        "created_at": datetime.now(UTC).isoformat(),
    }
    root = None
    audit_path = None
    receipt_path = None
    output_dir = None
    try:
        root = workspace_root_from_config(config).resolve()
        attempt_path = _safe_task_path(root, f"work/solar_data/b1_flare/{attempt_id}")
        attempt_path.mkdir(parents=True, exist_ok=False)
        audit_path = attempt_path / "attempt.json"
        _atomic_write_json(audit_path, audit)
        receipt_path = _safe_task_path(
            root, f"receipts/datasets/b1-flare-{attempt_id}.json"
        )
        for name in ("task.json", "input_manifest.json"):
            _safe_task_path(root, name)
        bound_root, task_id, task = _validated_task_metadata(config)
        if bound_root != root:
            raise PermissionError("B1 runtime task root changed during validation")
        question = task.get("research_question")
        if not isinstance(question, str) or not question.strip():
            raise ValueError("B1 task.json must contain the bound research question")
        audit["task_id"] = task_id
        audit["task_sha256"] = _file_sha256(root / "task.json")
        audit["input_manifest_sha256"] = _file_sha256(root / "input_manifest.json")
        audit["research_question_sha256"] = hashlib.sha256(
            question.encode("utf-8")
        ).hexdigest()
        eligible = {str(item["path"]): item for item in _eligible_input_records(config)}
        resolved = {}
        inputs = []
        for kind, ref in requested.items():
            path = _resolve_eligible_data_path(ref, config)
            record = eligible.get(ref.strip())
            if record is None:
                raise PermissionError("B1 input changed during manifest validation")
            if record.get("dataset_id") != B1_INPUT_DATASET_IDS[kind]:
                raise PermissionError(
                    f"B1 {kind} requires registered dataset_id {B1_INPUT_DATASET_IDS[kind]}"
                )
            resolved[kind] = path
            inputs.append({"input_kind": kind, **record})
        if len(set(resolved.values())) != len(requested):
            raise ValueError("B1 requires five distinct bound input files")
        audit["inputs"] = inputs
        audit["parameters_sha256"] = _file_sha256(resolved["parameters"])
        audit["sources_sha256"] = _file_sha256(resolved["sources"])
        parameters = json.loads(resolved["parameters"].read_text(encoding="utf-8"))
        if (
            not isinstance(parameters, dict)
            or parameters.get("schema_version") != "b1-flare-case-v1"
        ):
            raise ValueError(
                "B1 parameters must declare schema_version=b1-flare-case-v1"
            )
        _atomic_write_json(audit_path, audit)

        from jw import b1_compute

        audit["implementation"] = {
            "tool_sha256": _file_sha256(Path(__file__)),
            "core_sha256": _file_sha256(Path(b1_compute.__file__)),
        }
        output_dir = attempt_path / "products"
        b1_compute.run_b1_analysis(
            magnetic_path=resolved["magnetic"],
            events_path=resolved["events"],
            xrs_path=resolved["xrs"],
            parameters_path=resolved["parameters"],
            sources_path=resolved["sources"],
            output_dir=output_dir,
        )
        for record in inputs:
            kind = str(record["input_kind"])
            if _file_sha256(resolved[kind]) != record["sha256"]:
                raise PermissionError("B1 input changed while calculation was running")
        for field, name in (
            ("task_sha256", "task.json"),
            ("input_manifest_sha256", "input_manifest.json"),
        ):
            if _file_sha256(root / name) != audit[field]:
                raise PermissionError("B1 task binding changed during calculation")
        audit["outputs"] = _artifacts(root, output_dir)
        if not audit["outputs"]:
            raise RuntimeError("B1 computation produced no auditable output files")
        audit["status"] = "verified"
        audit["calculation_status"] = "completed"
        audit["result_summary"] = _verified_result_summary(root, output_dir, audit)
        audit["result_summary_sha256"] = hashlib.sha256(
            json.dumps(
                audit["result_summary"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        audit["limitations"] = [
            "Receipt verification checks computation and file provenance only.",
            "Observational association does not establish a causal mechanism.",
            "Independent scientific evidence review has not been performed.",
        ]
    except Exception as exc:
        audit.pop("result_summary", None)
        audit.pop("result_summary_sha256", None)
        audit["status"] = "error"
        audit["calculation_status"] = "failed"
        audit["error_type"] = type(exc).__name__
        audit["error_message"] = str(exc)
        if root is not None and output_dir is not None:
            try:
                audit["partial_outputs"] = _artifacts(root, output_dir)
            except Exception as artifact_exc:
                audit["partial_output_audit_error"] = str(artifact_exc)
    audit["finished_at"] = datetime.now(UTC).isoformat()
    artifact_refs = [str(item["path"]) for item in audit["outputs"]]
    receipt_refs = []
    if root is not None:
        for target in (audit_path, receipt_path):
            if target is not None:
                try:
                    _atomic_write_json(target, audit)
                    receipt_refs.append(target.relative_to(root).as_posix())
                except Exception as exc:
                    audit.pop("result_summary", None)
                    audit.pop("result_summary_sha256", None)
                    audit["audit_persistence_error"] = str(exc)
                    audit["status"] = "error"
                    audit["calculation_status"] = "audit_failed"
    if not receipt_refs:
        audit["audit_persistence_error"] = audit.get(
            "audit_persistence_error", "No writable initialized task workspace"
        )
    if audit.get("status") != "verified":
        audit.pop("result_summary", None)
        audit.pop("result_summary_sha256", None)
    result = {**audit, "artifact_refs": artifact_refs, "receipt_refs": receipt_refs}
    if audit.get("status") == "verified" and receipt_path is not None:
        result["verified_receipt_sha256"] = _file_sha256(receipt_path)
    return _to_json(result)


B1_FLARE_TOOLS = [run_b1_flare_case]
register_tool_bundle("solar-features", B1_FLARE_TOOLS)
